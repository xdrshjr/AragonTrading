"""策略 API 边界校验测试（R1）：factor_score 创建/更新对非法内联快照在边界一次性 400。

钉死「非法 factor_score 快照（auto 方向 / 未知 factorKey / 成员越界）经 POST/PUT /strategies 被
显式拦下」——否则回测期 prepare 会兜底成安静空仓 / 引擎静默越界（评审 P1-1）。同时回归 combo/atomic
创建路径**零行为变化**（validate_combo_params 分支未动）。monkeypatch rest，无网络。
"""
from __future__ import annotations

from httpx import ASGITransport, AsyncClient


def _load_app():
    from app.main import app
    return app


async def _client():
    from app.db.session import init_db
    await init_db()
    return AsyncClient(transport=ASGITransport(app=_load_app()), base_url="http://test")


def _factor_body(members: list[dict], name: str = "因子得分策略") -> dict:
    return {"templateKey": "factor_score", "name": name,
            "params": {"members": members, "thresholds": {"normWindow": 80}}}


_LONG = {"factorKey": "mom_roc", "params": {"win": 20}, "direction": "long"}
_SHORT = {"factorKey": "rev_rsi", "params": {}, "direction": "short"}


async def test_create_factor_strategy_rejects_auto_direction():
    """direction:"auto" 不可冻结 → 400 FACTOR_DIRECTION（杜绝打分期前视泄漏的快照落库）。"""
    from app.db.session import dispose_engine
    client = await _client()
    try:
        async with client:
            r = await client.post("/api/strategies",
                                  json=_factor_body([{"factorKey": "mom_roc", "direction": "auto"}]))
            assert r.status_code == 400, r.text
            assert r.json()["error"]["code"] == "FACTOR_DIRECTION"
    finally:
        await dispose_engine()


async def test_create_factor_strategy_rejects_unknown_key():
    """未知 factorKey → 400 FACTOR_UNKNOWN。"""
    from app.db.session import dispose_engine
    client = await _client()
    try:
        async with client:
            r = await client.post("/api/strategies",
                                  json=_factor_body([{"factorKey": "not_a_factor", "direction": "long"}]))
            assert r.status_code == 400, r.text
            assert r.json()["error"]["code"] == "FACTOR_UNKNOWN"
    finally:
        await dispose_engine()


async def test_create_factor_strategy_rejects_empty_and_overflow_members():
    """空成员 / >12 成员 → 400 FACTOR_MEMBERS（边界一次性拦下静默越界）。"""
    from app.db.session import dispose_engine
    client = await _client()
    try:
        async with client:
            empty = await client.post("/api/strategies", json=_factor_body([]))
            assert empty.status_code == 400 and empty.json()["error"]["code"] == "FACTOR_MEMBERS"

            too_many = [{"factorKey": "mom_roc", "direction": "long"} for _ in range(13)]
            r = await client.post("/api/strategies", json=_factor_body(too_many))
            assert r.status_code == 400 and r.json()["error"]["code"] == "FACTOR_MEMBERS"
    finally:
        await dispose_engine()


async def test_create_factor_strategy_accepts_legal_snapshot():
    """合法快照仍 201 并出现在 /strategies（校验绝不误伤合法创建路径，RR1）。"""
    from app.db.session import dispose_engine
    client = await _client()
    try:
        async with client:
            r = await client.post("/api/strategies", json=_factor_body([_LONG, _SHORT]))
            assert r.status_code == 201, r.text
            created = r.json()
            assert created["kind"] == "factor"
            listed = (await client.get("/api/strategies")).json()
            assert any(s["id"] == created["id"] for s in listed)
    finally:
        await dispose_engine()


async def test_update_factor_strategy_rejects_illegal_members():
    """PUT 改成员同样在边界校验：合法 → 200，随后改为 auto → 400 FACTOR_DIRECTION。"""
    from app.db.session import dispose_engine
    client = await _client()
    try:
        async with client:
            created = (await client.post("/api/strategies", json=_factor_body([_LONG, _SHORT]))).json()
            sid = created["id"]
            ok = await client.put(f"/api/strategies/{sid}",
                                  json={"params": {"members": [_LONG], "thresholds": {"normWindow": 80}}})
            assert ok.status_code == 200, ok.text

            bad = await client.put(f"/api/strategies/{sid}", json={
                "params": {"members": [{"factorKey": "mom_roc", "direction": "auto"}]}})
            assert bad.status_code == 400 and bad.json()["error"]["code"] == "FACTOR_DIRECTION"
    finally:
        await dispose_engine()


async def test_create_atomic_strategy_unaffected_by_factor_validation():
    """原子策略创建路径零行为变化（factor 校验只对 kind=="factor" 生效）。"""
    from app.db.session import dispose_engine
    client = await _client()
    try:
        async with client:
            r = await client.post("/api/strategies", json={
                "templateKey": "momentum_breakout", "name": "我的动量",
                "params": {"breakoutBars": 30}})
            assert r.status_code == 201, r.text
            assert r.json()["kind"] != "factor"
    finally:
        await dispose_engine()
