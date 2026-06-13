"""compare 路由单测 (F3)：候选 candles 只取一次 + 各策略独立 runId + 去重 + 入参校验。

OKX 用计数假客户端（无网络）。
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.okx.types import Candle
from tests.conftest import make_synthetic_candles


class _CountingRest:
    """记录 get_candles 调用次数，验证候选 candles 只取一次（R1）。"""

    def __init__(self):
        self.candle_calls = 0
        self.calls = []

    async def health(self) -> bool:
        return True

    async def get_candles(self, inst_id, bar, limit=300, before=None, after=None):
        self.candle_calls += 1
        self.calls.append({"inst_id": inst_id, "bar": bar, "limit": limit,
                           "before": before, "after": after})
        rows = make_synthetic_candles(min(limit, 300), start_ts=1_700_000_000, step=3600)
        return [Candle(ts=r["ts"], o=r["o"], h=r["h"], l=r["l"], c=r["c"], vol=r["vol"]) for r in rows]


@pytest.fixture
def patch_okx(monkeypatch):
    fake = _CountingRest()
    monkeypatch.setattr("app.api.strategies.get_rest_client", lambda: fake)
    return fake


def _load_app():
    from app.main import app
    return app


async def test_compare_runs_multiple_strategies(patch_okx):
    from app.db.session import dispose_engine, init_db
    await init_db()
    transport = ASGITransport(app=_load_app())
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            builtins = [s for s in (await client.get("/api/strategies")).json() if s["builtin"]]
            ids = [b["id"] for b in builtins[:3]]

            r = await client.post("/api/strategies/compare", json={
                "strategyIds": ids, "instId": "BTC-USDT-SWAP", "bar": "1H", "limit": 200,
                "startTs": 1_700_000_000, "endTs": 1_700_000_000 + 100 * 3600})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["instId"] == "BTC-USDT-SWAP"
            assert len(body["items"]) == 3
            # 每项含独立 runId + 齐全 metrics + 归一化前的真实净值
            run_ids = {it["runId"] for it in body["items"]}
            assert len(run_ids) == 3
            for it in body["items"]:
                assert "totalReturn" in it["metrics"]
                assert isinstance(it["equity"], list) and len(it["equity"]) > 0
                assert {"ts", "equity", "drawdown"} <= set(it["equity"][0].keys())
            # 候选 candles 只取一次（R1）
            assert patch_okx.candle_calls == 1
    finally:
        await dispose_engine()


async def test_compare_dedupes_strategy_ids(patch_okx):
    from app.db.session import dispose_engine, init_db
    await init_db()
    transport = ASGITransport(app=_load_app())
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            sid = [s for s in (await client.get("/api/strategies")).json() if s["builtin"]][0]["id"]
            # 同一 id 出现两次 -> 去重后只剩 1 项、不重复落库
            r = await client.post("/api/strategies/compare", json={
                "strategyIds": [sid, sid], "instId": "BTC-USDT-SWAP", "bar": "1H", "limit": 120,
                "startTs": 1_700_000_000, "endTs": 1_700_000_000 + 80 * 3600})
            assert r.status_code == 200, r.text
            assert len(r.json()["items"]) == 1
    finally:
        await dispose_engine()


async def test_compare_rejects_single_strategy(patch_okx):
    """strategyIds 个数 < 2 由 Field(min_length=2) 校验 -> 422 (C9)。"""
    from app.db.session import dispose_engine, init_db
    await init_db()
    transport = ASGITransport(app=_load_app())
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/api/strategies/compare", json={
                "strategyIds": [1], "instId": "BTC-USDT-SWAP", "bar": "1H"})
            assert r.status_code == 422
    finally:
        await dispose_engine()


async def test_compare_uses_requested_time_range(patch_okx):
    from app.db.session import dispose_engine, init_db
    await init_db()
    transport = ASGITransport(app=_load_app())
    start_ts = 1_700_000_000 + 30 * 3600
    end_ts = 1_700_000_000 + 90 * 3600
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            builtins = [s for s in (await client.get("/api/strategies")).json() if s["builtin"]]
            ids = [b["id"] for b in builtins[:2]]

            r = await client.post("/api/strategies/compare", json={
                "strategyIds": ids,
                "instId": "BTC-USDT-SWAP",
                "bar": "1H",
                "limit": 200,
                "startTs": start_ts,
                "endTs": end_ts,
            })
            assert r.status_code == 200, r.text
            assert patch_okx.candle_calls == 1
            assert patch_okx.calls[-1] == {
                "inst_id": "BTC-USDT-SWAP",
                "bar": "1H",
                "limit": 200,
                "before": end_ts,
                "after": start_ts,
            }
            for item in r.json()["items"]:
                assert item["equity"][0]["ts"] == start_ts
                assert item["equity"][-1]["ts"] == end_ts
    finally:
        await dispose_engine()
