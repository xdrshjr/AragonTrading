"""组合 API 契约测试：combo/preview 形态 + 成员守卫 + compare 混排（monkeypatch rest，无网络）。"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.okx.types import Candle
from tests.conftest import make_synthetic_candles


class _FakeRest:
    """对任意 bar/limit 返回确定性合成 K 线（无网络）。"""

    async def health(self) -> bool:
        return True

    async def get_candles(self, inst_id, bar, limit=300, before=None, after=None):
        rows = make_synthetic_candles(min(limit, 300), start_ts=1_700_000_000, step=3600)
        return [Candle(ts=r["ts"], o=r["o"], h=r["h"], l=r["l"], c=r["c"], vol=r["vol"]) for r in rows]


@pytest.fixture
def patch_okx(monkeypatch):
    fake = _FakeRest()
    monkeypatch.setattr("app.api.strategies.get_rest_client", lambda: fake)
    return fake


def _load_app():
    from app.main import app
    return app


_RANGE = {"startTs": 1_700_000_000, "endTs": 1_700_000_000 + 295 * 3600}


async def test_combo_preview_shape_and_run(patch_okx):
    """combo/preview → {runId, metrics, combo{...}}；runId 可经 /runs/{id}/equity 取回净值。"""
    from app.db.session import dispose_engine, init_db
    await init_db()
    transport = ASGITransport(app=_load_app())
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            body = {
                "members": [
                    {"templateKey": "trend_pullback_long", "params": {}},
                    {"templateKey": "range_scalping_strategy", "params": {}},
                    {"templateKey": "trend_pullback_short", "params": {}},
                ],
                "policy": {"rebalanceBars": 12, "lookbackBars": 48, "selectionMetric": "sharpe"},
                "instId": "BTC-USDT-SWAP", "bar": "1H", "limit": 300, **_RANGE,
            }
            r = await client.post("/api/strategies/combo/preview", json=body)
            assert r.status_code == 200, r.text
            data = r.json()
            assert isinstance(data["runId"], int)
            assert set(data["metrics"].keys()) == {
                "totalReturn", "winRate", "profitFactor", "maxDrawdown", "sharpe",
                "tradeCount", "avgPnl"}
            combo = data["combo"]
            assert "annualizedReturn" in combo
            assert isinstance(combo["timeline"], list)
            assert isinstance(combo["breakdown"], list)

            equity = (await client.get(f"/api/runs/{data['runId']}/equity")).json()
            assert isinstance(equity, list) and len(equity) > 0
            assert {"ts", "equity", "drawdown"} <= set(equity[0].keys())
    finally:
        await dispose_engine()


async def test_combo_preview_rejects_single_member(patch_okx):
    """members < 2 由 Field(min_length=2) 校验 → 422。"""
    from app.db.session import dispose_engine, init_db
    await init_db()
    transport = ASGITransport(app=_load_app())
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/api/strategies/combo/preview", json={
                "members": [{"templateKey": "trend_pullback_long"}],
                "instId": "BTC-USDT-SWAP", "bar": "1H", **_RANGE})
            assert r.status_code == 422
    finally:
        await dispose_engine()


async def test_combo_preview_guards_unavailable_members(patch_okx):
    """两个成员模板都不存在 → 跳过后可用 < 2 → 400 COMBO_MEMBERS（不静默降级）。"""
    from app.db.session import dispose_engine, init_db
    await init_db()
    transport = ASGITransport(app=_load_app())
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/api/strategies/combo/preview", json={
                "members": [{"templateKey": "does_not_exist"},
                            {"templateKey": "also_missing"}],
                "instId": "BTC-USDT-SWAP", "bar": "1H", **_RANGE})
            assert r.status_code == 400
            assert r.json()["error"]["code"] == "COMBO_MEMBERS"
    finally:
        await dispose_engine()


_M2 = [
    {"templateKey": "trend_pullback_long", "params": {}},
    {"templateKey": "range_scalping_strategy", "params": {}},
]


def _combo_body(members: list[dict], name: str = "我的组合") -> dict:
    return {"templateKey": "regime_rotation", "name": name,
            "params": {"members": members, "selectionMetric": "sharpe"}}


async def test_save_update_delete_user_combo(patch_okx):
    """保存用户组合 → 列表含其 kind==combo → 改成员回读一致 → 删用户组合 204 / 删内置 409。"""
    from app.db.session import dispose_engine, init_db
    await init_db()
    transport = ASGITransport(app=_load_app())
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post("/api/strategies", json=_combo_body(_M2))
            assert r.status_code == 201, r.text
            created = r.json()
            assert created["kind"] == "combo"
            cid = created["id"]

            listed = (await client.get("/api/strategies")).json()
            assert any(s["id"] == cid and s["kind"] == "combo" for s in listed)

            m3 = _M2 + [{"templateKey": "trend_pullback_short", "params": {}}]
            r = await client.put(f"/api/strategies/{cid}",
                                 json={"params": {"members": m3, "selectionMetric": "calmar"}})
            assert r.status_code == 200, r.text
            reread = (await client.get(f"/api/strategies/{cid}")).json()
            assert len(reread["params"]["members"]) == 3

            assert (await client.delete(f"/api/strategies/{cid}")).status_code == 204

            builtin_id = next(s["id"] for s in listed
                              if s["templateKey"] == "regime_rotation" and s["builtin"])
            r = await client.delete(f"/api/strategies/{builtin_id}")
            assert r.status_code == 409
            assert r.json()["error"]["code"] == "BUILTIN_READONLY"
    finally:
        await dispose_engine()


async def test_save_combo_rejects_member_count_out_of_bounds(patch_okx):
    """绕过 UI 直接保存 <2 或 >8 成员的组合 → 400 COMBO_MEMBERS（纵深防御）。"""
    from app.db.session import dispose_engine, init_db
    await init_db()
    transport = ASGITransport(app=_load_app())
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r1 = await client.post("/api/strategies", json=_combo_body([_M2[0]]))
            assert r1.status_code == 400
            assert r1.json()["error"]["code"] == "COMBO_MEMBERS"

            too_many = [{"templateKey": "trend_pullback_long", "params": {}} for _ in range(9)]
            r2 = await client.post("/api/strategies", json=_combo_body(too_many))
            assert r2.status_code == 400
            assert r2.json()["error"]["code"] == "COMBO_MEMBERS"
    finally:
        await dispose_engine()


async def test_combo_in_compare(patch_okx):
    """compare 传 1 组合 id + 1 原子 id → items 含两项且结构同构（可混排对比）。"""
    from app.db.session import dispose_engine, init_db
    await init_db()
    transport = ASGITransport(app=_load_app())
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            strategies = (await client.get("/api/strategies")).json()
            combo_id = next(s["id"] for s in strategies if s["templateKey"] == "regime_rotation")
            atomic_id = next(s["id"] for s in strategies
                             if s["builtin"] and s.get("kind") != "combo")
            r = await client.post("/api/strategies/compare", json={
                "strategyIds": [atomic_id, combo_id], "instId": "BTC-USDT-SWAP", "bar": "1H",
                "limit": 300, **_RANGE})
            assert r.status_code == 200, r.text
            items = r.json()["items"]
            assert len(items) == 2
            ids = {it["strategyId"] for it in items}
            assert ids == {atomic_id, combo_id}
            for it in items:
                assert "totalReturn" in it["metrics"]
                assert isinstance(it["equity"], list) and len(it["equity"]) > 0
    finally:
        await dispose_engine()
