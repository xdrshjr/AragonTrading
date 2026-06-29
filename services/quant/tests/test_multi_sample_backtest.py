"""多样本回测路由单测（multi-sample-backtest §7.1）。

单策略 × 多标的：聚合 + per-symbol 错误隔离 + 404 整请求级 + 去重保序 + 工作量护栏 413 + 入参校验。
OKX 用假客户端（无网络）；某些 instId 可被指定为「无数据」以触发 EMPTY_RANGE 隔离。
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.okx.types import Candle
from tests.conftest import make_synthetic_candles

_START = 1_700_000_000
_END = 1_700_000_000 + 100 * 3600


class _MultiRest:
    """假 OKX：默认返回合成 K 线；``empty_for`` 中的标的返回空（触发 EMPTY_RANGE）。"""

    def __init__(self, empty_for: tuple[str, ...] = ()):
        self.empty_for = set(empty_for)
        self.calls: list[str] = []

    async def health(self) -> bool:
        return True

    async def get_candles(self, inst_id, bar, limit=300, before=None, after=None):
        self.calls.append(inst_id)
        if inst_id in self.empty_for:
            return []
        rows = make_synthetic_candles(min(limit, 300), start_ts=_START, step=3600)
        return [Candle(ts=r["ts"], o=r["o"], h=r["h"], l=r["l"], c=r["c"], vol=r["vol"]) for r in rows]


def _patch(monkeypatch, rest: _MultiRest) -> _MultiRest:
    monkeypatch.setattr("app.api.strategies.get_rest_client", lambda: rest)
    return rest


def _load_app():
    from app.main import app
    return app


async def _atomic_strategy_id(client: AsyncClient) -> int:
    strategies = (await client.get("/api/strategies")).json()
    return next(s["id"] for s in strategies if s["builtin"] and s.get("kind") != "combo")


async def test_multi_sample_aggregates_per_symbol(monkeypatch):
    from app.db.session import dispose_engine, init_db
    await init_db()
    _patch(monkeypatch, _MultiRest())
    transport = ASGITransport(app=_load_app())
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            sid = await _atomic_strategy_id(client)
            r = await client.post(f"/api/strategies/{sid}/multi-backtest", json={
                "instIds": ["BTC-USDT-SWAP", "ETH-USDT-SWAP"], "bar": "1H", "limit": 200,
                "startTs": _START, "endTs": _END})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["strategyId"] == sid and body["bar"] == "1H"
            assert [it["instId"] for it in body["items"]] == ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
            assert all(it["status"] == "ok" for it in body["items"])
            run_ids = {it["runId"] for it in body["items"]}
            assert len(run_ids) == 2
            for it in body["items"]:
                assert "totalReturn" in it["metrics"]
    finally:
        await dispose_engine()


async def test_multi_sample_isolates_failing_symbol(monkeypatch):
    from app.db.session import dispose_engine, init_db
    await init_db()
    _patch(monkeypatch, _MultiRest(empty_for=("DOGE-USDT-SWAP",)))
    transport = ASGITransport(app=_load_app())
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            sid = await _atomic_strategy_id(client)
            r = await client.post(f"/api/strategies/{sid}/multi-backtest", json={
                "instIds": ["BTC-USDT-SWAP", "DOGE-USDT-SWAP"], "bar": "1H", "limit": 200,
                "startTs": _START, "endTs": _END})
            # 单标的失败不致整体失败：请求整体 200，坏标的落 status=error 且 error 非空。
            assert r.status_code == 200, r.text
            items = {it["instId"]: it for it in r.json()["items"]}
            assert items["BTC-USDT-SWAP"]["status"] == "ok"
            assert items["DOGE-USDT-SWAP"]["status"] == "error"
            assert items["DOGE-USDT-SWAP"]["error"]
    finally:
        await dispose_engine()


async def test_multi_sample_strategy_not_found_404(monkeypatch):
    from app.db.session import dispose_engine, init_db
    await init_db()
    _patch(monkeypatch, _MultiRest())
    transport = ASGITransport(app=_load_app())
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/api/strategies/999999/multi-backtest", json={
                "instIds": ["BTC-USDT-SWAP"], "bar": "1H",
                "startTs": _START, "endTs": _END})
            assert r.status_code == 404
            assert r.json()["error"]["code"] == "NOT_FOUND"
    finally:
        await dispose_engine()


async def test_multi_sample_dedup_preserves_order(monkeypatch):
    from app.db.session import dispose_engine, init_db
    await init_db()
    _patch(monkeypatch, _MultiRest())
    transport = ASGITransport(app=_load_app())
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            sid = await _atomic_strategy_id(client)
            r = await client.post(f"/api/strategies/{sid}/multi-backtest", json={
                "instIds": ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "BTC-USDT-SWAP"], "bar": "1H",
                "limit": 200, "startTs": _START, "endTs": _END})
            assert r.status_code == 200, r.text
            inst_ids = [it["instId"] for it in r.json()["items"]]
            assert inst_ids == ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
    finally:
        await dispose_engine()


async def test_multi_sample_workload_guard_413(monkeypatch):
    from app.db.session import dispose_engine, init_db
    await init_db()
    _patch(monkeypatch, _MultiRest())
    # 极小工作量预算：2 标的 × 任意根数即超阈 -> 413。
    monkeypatch.setattr("app.agent.services._COMPARE_SYNC_WORK_UNIT_LIMIT", 1)
    transport = ASGITransport(app=_load_app(), raise_app_exceptions=False)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            sid = await _atomic_strategy_id(client)
            r = await client.post(f"/api/strategies/{sid}/multi-backtest", json={
                "instIds": ["BTC-USDT-SWAP", "ETH-USDT-SWAP"], "bar": "1H", "limit": 200,
                "startTs": _START, "endTs": _END})
            assert r.status_code == 413
            assert r.json()["error"]["code"] == "MULTI_SAMPLE_TOO_LARGE"
    finally:
        await dispose_engine()


@pytest.mark.parametrize("payload", [
    {"instIds": [], "bar": "1H", "startTs": _START, "endTs": _END},                       # 空
    {"instIds": [f"S{i}-USDT-SWAP" for i in range(21)], "bar": "1H",
     "startTs": _START, "endTs": _END},                                                   # 超 20
    {"instIds": ["BTC-USDT-SWAP"], "bar": "1H", "startTs": _END, "endTs": _START},         # startTs>=endTs
])
async def test_multi_sample_req_validation(monkeypatch, payload):
    from app.db.session import dispose_engine, init_db
    await init_db()
    _patch(monkeypatch, _MultiRest())
    transport = ASGITransport(app=_load_app(), raise_app_exceptions=False)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            sid = await _atomic_strategy_id(client)
            r = await client.post(f"/api/strategies/{sid}/multi-backtest", json=payload)
            assert r.status_code == 422
    finally:
        await dispose_engine()
