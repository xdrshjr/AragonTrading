"""接口单测 (spec §13.1)：/strategies CRUD、/backtest、/runs（OKX 用假客户端，无网络）。"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.okx.types import Candle, Instrument
from tests.conftest import make_synthetic_candles


class _FakeRest:
    def __init__(self):
        self.calls = []

    async def health(self) -> bool:
        return True

    async def get_candles(self, inst_id, bar, limit=300, before=None, after=None):
        self.calls.append({"inst_id": inst_id, "bar": bar, "limit": limit,
                           "before": before, "after": after})
        rows = make_synthetic_candles(min(limit, 300), start_ts=1_700_000_000, step=3600)
        candles = [
            Candle(ts=r["ts"], o=r["o"], h=r["h"], l=r["l"], c=r["c"], vol=r["vol"])
            for r in rows
        ]
        return candles

    async def get_instruments(self, inst_type="SWAP"):
        return [Instrument(instId="BTC-USDT-SWAP", instType="SWAP", base="BTC", quote="USDT")]

    async def aclose(self):
        pass


@pytest.fixture
def patch_okx(monkeypatch):
    fake = _FakeRest()
    monkeypatch.setattr("app.api.market.get_rest_client", lambda: fake)
    monkeypatch.setattr("app.api.strategies.get_rest_client", lambda: fake)
    return fake


async def test_full_api_flow(patch_okx):
    from app.db.session import dispose_engine, init_db
    await init_db()
    transport = ASGITransport(app=_load_app())
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # health
            r = await client.get("/api/health")
            assert r.status_code == 200 and r.json()["okx"] == "up"

            # 5 个内置策略
            r = await client.get("/api/strategies")
            assert r.status_code == 200
            strategies = r.json()
            builtins = [s for s in strategies if s["builtin"]]
            assert len(builtins) >= 5
            assert all("paramsSchema" in s and s["paramsSchema"] for s in builtins)
            sid = builtins[0]["id"]

            # 触发回测
            r = await client.post(f"/api/strategies/{sid}/backtest",
                                  json={"instId": "BTC-USDT-SWAP", "bar": "1H", "limit": 200,
                                        "startTs": 1_700_000_000,
                                        "endTs": 1_700_000_000 + 100 * 3600})
            assert r.status_code == 200, r.text
            body = r.json()
            run_id = body["runId"]
            assert "totalReturn" in body["metrics"]

            # equity / trades
            r = await client.get(f"/api/runs/{run_id}/equity")
            assert r.status_code == 200 and len(r.json()) > 0
            r = await client.get(f"/api/runs/{run_id}/trades")
            assert r.status_code == 200

            # 内置不可删 -> 409
            r = await client.delete(f"/api/strategies/{sid}")
            assert r.status_code == 409

            # 另存为自定义实例 -> 可删
            r = await client.post("/api/strategies", json={
                "templateKey": builtins[0]["templateKey"], "name": "我的副本", "params": {}})
            assert r.status_code == 201
            new_id = r.json()["id"]
            r = await client.delete(f"/api/strategies/{new_id}")
            assert r.status_code == 204
    finally:
        await dispose_engine()


async def test_backtest_uses_requested_time_range(patch_okx):
    from app.db.session import dispose_engine, init_db
    await init_db()
    transport = ASGITransport(app=_load_app())
    start_ts = 1_700_000_000 + 20 * 3600
    end_ts = 1_700_000_000 + 80 * 3600
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            sid = [s for s in (await client.get("/api/strategies")).json() if s["builtin"]][0]["id"]
            r = await client.post(f"/api/strategies/{sid}/backtest", json={
                "instId": "BTC-USDT-SWAP",
                "bar": "1H",
                "limit": 200,
                "startTs": start_ts,
                "endTs": end_ts,
            })
            assert r.status_code == 200, r.text
            assert patch_okx.calls[-1] == {
                "inst_id": "BTC-USDT-SWAP",
                "bar": "1H",
                "limit": 200,
                "before": end_ts,
                "after": start_ts,
            }
            run = (await client.get(f"/api/runs/{r.json()['runId']}")).json()
            equity = (await client.get(f"/api/runs/{r.json()['runId']}/equity")).json()
            assert run["startTs"] == start_ts
            assert run["endTs"] == end_ts
            assert equity[0]["ts"] == start_ts
            assert equity[-1]["ts"] == end_ts
            assert all(start_ts <= point["ts"] <= end_ts for point in equity)
    finally:
        await dispose_engine()


async def test_backtest_rejects_empty_requested_range(patch_okx):
    from app.db.session import dispose_engine, init_db
    await init_db()
    transport = ASGITransport(app=_load_app())
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            sid = [s for s in (await client.get("/api/strategies")).json() if s["builtin"]][0]["id"]
            r = await client.post(f"/api/strategies/{sid}/backtest", json={
                "instId": "BTC-USDT-SWAP",
                "bar": "1H",
                "limit": 200,
                "startTs": 1_800_000_000,
                "endTs": 1_800_003_600,
            })
            assert r.status_code == 400
    finally:
        await dispose_engine()


def _load_app():
    from app.main import app
    return app
