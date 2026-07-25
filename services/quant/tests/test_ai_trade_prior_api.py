"""先验快照端点单测（§8.1 T-17）：四类守门 + 落库形状 + `GET` 的契约回显。

OKX 取数全程打桩（绝不出网）。守两件容易被做成「先干活再拒绝」的事：**413 在取数前判定**
（`lookbackBars` 越界时一根 K 线都不该拉），**422 不假数据**（实得根数不足即拒，绝不用短样本硬产统计）。
"""
from __future__ import annotations

import math

from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.db import ai_prior_repositories as prior_repo
from app.db.models import AppSetting, Strategy
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.okx.types import Candle

_START_TS = 1_780_000_000
_STEP = 900


def _load_app():
    from app.main import app
    return app


async def _fresh() -> int:
    await init_db()
    sm = get_sessionmaker()
    async with sm() as session:
        await session.execute(delete(AppSetting))
        await session.commit()
        return (await session.execute(
            select(Strategy.id).order_by(Strategy.id.asc()).limit(1))).scalar_one()


def _candles(count: int) -> list[Candle]:
    """确定性折返序列（有涨有跌 ⇒ 段间均值非常数 ⇒ IR 可定义）。"""
    rows, px = [], 100.0
    for k in range(count):
        rows.append(Candle(ts=_START_TS + k * _STEP, o=px, h=px * 1.002, l=px * 0.998,
                           c=px, vol=100.0, confirm=True))
        px *= 1.0 + 0.0009 * math.sin(k / 5.0) + (0.0004 if k < count // 2 else -0.0004)
    return rows


def _patch_candles(monkeypatch, count: int) -> dict:
    """桩掉取数并记录调用次数（413 用例据此断言「取数前失败」）。"""
    calls = {"range": 0, "htf": 0}

    async def fake_range(*_a, **_k):
        calls["range"] += 1
        return _candles(count)

    async def fake_htf(*_a, **_k):
        calls["htf"] += 1
        return {}

    monkeypatch.setattr("app.ai_auto.prior_service.load_range_candles", fake_range)
    monkeypatch.setattr("app.ai_auto.prior_service.load_htf_frames", fake_htf)
    monkeypatch.setattr("app.ai_auto.prior_service.get_rest_client", lambda: object())
    return calls


async def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _body(strategy_id: int | None = None, **over) -> dict:
    body = {"instId": "BTC-USDT-SWAP", "bar": "15m",
            "strategyIds": [] if strategy_id is None else [strategy_id]}
    body.update(over)
    return body


async def test_invalid_bar_is_rejected_before_anything_else(monkeypatch):
    await _fresh()
    calls = _patch_candles(monkeypatch, 400)
    try:
        async with await _client(_load_app()) as client:
            resp = await client.post("/api/ai-trade/priors/rebuild", json=_body(bar="7m"))
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "AI_PRIOR_BAR_INVALID"
        assert calls["range"] == 0
    finally:
        await dispose_engine()


async def test_oversized_lookback_fails_before_fetching_candles(monkeypatch):
    """413 **取数前**快速失败：越界时一根 K 线都不该被拉（与 factor_train_max_bars 同款姿态）。"""
    await _fresh()
    calls = _patch_candles(monkeypatch, 400)
    try:
        async with await _client(_load_app()) as client:
            resp = await client.post("/api/ai-trade/priors/rebuild",
                                     json=_body(lookbackBars=999_999))
        assert resp.status_code == 413
        assert resp.json()["error"]["code"] == "AI_PRIOR_TOO_LARGE"
        assert calls["range"] == 0
    finally:
        await dispose_engine()


async def test_insufficient_bars_is_422_and_writes_nothing(monkeypatch):
    """422 不假数据：实得根数不足即拒，且**绝不落一份半成品快照**。"""
    await _fresh()
    _patch_candles(monkeypatch, 120)
    try:
        async with await _client(_load_app()) as client:
            resp = await client.post("/api/ai-trade/priors/rebuild", json=_body())
            assert resp.status_code == 422
            assert resp.json()["error"]["code"] == "AI_PRIOR_INSUFFICIENT_BARS"
            got = await client.get("/api/ai-trade/priors",
                                   params={"instId": "BTC-USDT-SWAP", "bar": "15m"})
        assert got.json() == {"exists": False}
    finally:
        await dispose_engine()


async def test_unknown_strategy_reuses_existing_invalid_code(monkeypatch):
    await _fresh()
    _patch_candles(monkeypatch, 400)
    try:
        async with await _client(_load_app()) as client:
            resp = await client.post("/api/ai-trade/priors/rebuild",
                                     json=_body(strategyIds=[999_999]))
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "AI_TRADE_STRATEGIES_INVALID"
    finally:
        await dispose_engine()


async def test_concurrent_rebuild_of_same_pair_is_409(monkeypatch):
    """同一 `(instId, bar)` 重入 ⇒ 409（重建会白烧配额且两次写互相覆盖）。"""
    await _fresh()
    _patch_candles(monkeypatch, 400)
    from app.api import ai_auto as api

    api._PRIOR_REBUILD_INFLIGHT.add(("BTC-USDT-SWAP", "15m"))
    try:
        async with await _client(_load_app()) as client:
            resp = await client.post("/api/ai-trade/priors/rebuild", json=_body())
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "AI_PRIOR_REBUILD_BUSY"
    finally:
        api._PRIOR_REBUILD_INFLIGHT.discard(("BTC-USDT-SWAP", "15m"))
        await dispose_engine()


async def test_successful_rebuild_persists_and_reads_back(monkeypatch):
    """成功路径：回执形状 + 落库后 `GET` 读回同一份 + `contract` 如实回显 C-2 / C-3。"""
    strategy_id = await _fresh()
    _patch_candles(monkeypatch, 400)
    try:
        async with await _client(_load_app()) as client:
            resp = await client.post("/api/ai-trade/priors/rebuild",
                                     json=_body(strategy_id))
            assert resp.status_code == 200, resp.text
            out = resp.json()
            assert out["instId"] == "BTC-USDT-SWAP" and out["bar"] == "15m"
            assert out["horizonBars"] == 8 and out["costBasis"] == "gross"
            assert out["entryCount"] >= 1 and out["usableCount"] <= out["entryCount"]
            assert out["coverage"]["strategies"] == 1
            got = await client.get("/api/ai-trade/priors",
                                   params={"instId": "BTC-USDT-SWAP", "bar": "15m"})
        body = got.json()
        assert body["exists"] is True
        assert body["contract"] == {"fresh": True, "horizonMatch": True}
        assert body["snapshot"]["entryCount"] == out["entryCount"]
        sm = get_sessionmaker()
        async with sm() as session:      # KV 键即 `ai_prior:{instId}:{bar}`（C-5 由键本身保证）
            raw = await prior_repo.load_prior_snapshot(session, "BTC-USDT-SWAP", "15m")
        assert raw["version"] == 1 and raw["costBasis"] == "gross"
        assert all(e["costBasis"] == "gross" for e in raw["entries"])   # G-5
    finally:
        await dispose_engine()


async def test_prior_key_shape_is_within_column_limit():
    assert prior_repo.prior_key("BTC-USDT-SWAP", "15m") == "ai_prior:BTC-USDT-SWAP:15m"
    assert len(prior_repo.prior_key("BTC-USDT-SWAP", "15m")) <= 64
