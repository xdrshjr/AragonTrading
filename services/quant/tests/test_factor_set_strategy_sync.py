"""因子组合 ↔ 交易策略 自动同步集成测试（factor-combo-strategy-sync）。

钉死 spec v2 主线与放行条件：组合 create/update/delete 在**单事务**内连带建/改/删伴生 ``factor_score``
策略（REST 与 Agent 共用单源 ``service.create_set``）；读路径批量回填 ``strategyId``（R1，模拟刷新，
不依赖 POST 响应）；多伴生 PUT 坍缩为正典一条（R2）；删除连带删伴生（R3，有意反转旧不变量）；伴生建
失败整体回滚（R8，monkeypatch ``add_strategy`` 抛错）；``backfill_companions`` 幂等（R6）；孤儿
``factor_score`` 策略（无 ``factorSetId``）不被误删/误改。共享进程 DB，按返回 id 断言以抗串扰
（呼应 aragon-quant-test-isolation）。
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.okx.types import Candle
from tests.conftest import make_synthetic_candles


class _FakeRest:
    async def health(self) -> bool:
        return True

    async def get_candles(self, inst_id, bar, limit=300, before=None, after=None):
        rows = make_synthetic_candles(min(limit, 300), start_ts=1_700_000_000, step=3600)
        return [Candle(ts=r["ts"], o=r["o"], h=r["h"], l=r["l"], c=r["c"], vol=r["vol"]) for r in rows]


@pytest.fixture
def patch_okx(monkeypatch):
    fake = _FakeRest()
    monkeypatch.setattr("app.api.factors.get_rest_client", lambda: fake)
    return fake


def _load_app():
    from app.main import app
    return app


async def _client() -> AsyncClient:
    from app.db.session import init_db
    await init_db()
    return AsyncClient(transport=ASGITransport(app=_load_app()), base_url="http://test")


_MEMBERS = [{"factorKey": "mom_roc", "direction": "long"},
            {"factorKey": "rev_rsi", "direction": "short"}]


async def _strategies(client: AsyncClient) -> list[dict]:
    return (await client.get("/api/strategies")).json()


async def test_create_set_deploys_companion_strategy(patch_okx):
    """POST 组合 → 单事务连带建一条 kind=factor、params.factorSetId==set.id 的伴生策略，strategyId 指向它。"""
    from app.db.session import dispose_engine
    client = await _client()
    try:
        async with client:
            saved = await client.post("/api/factor-sets",
                                      json={"name": "同步组合A", "members": _MEMBERS, "method": "equal"})
            assert saved.status_code == 201, saved.text
            body = saved.json()
            sid, companion_id = body["id"], body["strategyId"]
            assert companion_id is not None
            comp = next(s for s in await _strategies(client) if s["id"] == companion_id)
            assert comp["kind"] == "factor" and comp["templateKey"] == "factor_score"
            assert comp["params"]["factorSetId"] == sid
            assert comp["name"] == "同步组合A · 得分策略"
    finally:
        await dispose_engine()


async def test_get_backfills_strategy_id_on_refresh(patch_okx):
    """GET 列表/单条经批量反查回填 strategyId（R1，模拟刷新场景，不依赖 POST 响应）。"""
    from app.db.session import dispose_engine
    client = await _client()
    try:
        async with client:
            saved = (await client.post("/api/factor-sets",
                                       json={"name": "GET回填", "members": _MEMBERS, "method": "equal"})).json()
            sid, companion_id = saved["id"], saved["strategyId"]
            row = next(r for r in (await client.get("/api/factor-sets")).json() if r["id"] == sid)
            assert row["strategyId"] == companion_id
            single = (await client.get(f"/api/factor-sets/{sid}")).json()
            assert single["strategyId"] == companion_id
    finally:
        await dispose_engine()


async def test_update_set_syncs_companion_name_and_members(patch_okx):
    """PUT 组合改名/改成员 → 伴生策略 name 与 params.members 同步刷新，正典 id 不变。"""
    from app.db.session import dispose_engine
    client = await _client()
    try:
        async with client:
            saved = (await client.post("/api/factor-sets",
                                       json={"name": "改前", "members": _MEMBERS, "method": "equal"})).json()
            sid, companion_id = saved["id"], saved["strategyId"]
            upd = await client.put(f"/api/factor-sets/{sid}",
                                   json={"name": "改后", "members": [{"factorKey": "mom_roc", "direction": "long"}],
                                         "method": "equal"})
            assert upd.status_code == 200, upd.text
            assert upd.json()["strategyId"] == companion_id   # 正典不变
            comp = next(s for s in await _strategies(client) if s["id"] == companion_id)
            assert comp["name"] == "改后 · 得分策略"
            assert [m["factorKey"] for m in comp["params"]["members"]] == ["mom_roc"]
    finally:
        await dispose_engine()


async def test_update_self_heals_missing_companion(patch_okx):
    """对**无伴生**的历史组合 PUT → 自愈补建一条伴生（R6 双保险）。"""
    from app.db import repositories as repo
    from app.db.session import dispose_engine, get_sessionmaker
    client = await _client()
    try:
        async with client:
            async with get_sessionmaker()() as session:
                row = await repo.create_factor_set(session, values=repo.FactorSetValues(
                    name="历史组合", members=[{"factorKey": "mom_roc", "params": {}, "weight": 0.0,
                                                "direction": "long"}],
                    method="equal", config={}, model={}, metrics=None, enabled=True))
                hid = row.id
            assert next(r for r in (await client.get("/api/factor-sets")).json()
                        if r["id"] == hid)["strategyId"] is None
            upd = (await client.put(f"/api/factor-sets/{hid}", json={"name": "历史组合改"})).json()
            assert upd["strategyId"] is not None
            comp = next(s for s in await _strategies(client) if s["id"] == upd["strategyId"])
            assert comp["params"]["factorSetId"] == hid
    finally:
        await dispose_engine()


async def test_delete_set_removes_companion(patch_okx):
    """DELETE 组合 → 连带删除伴生策略（R3 有意反转旧不变量），其从 GET /strategies 消失。"""
    from app.db.session import dispose_engine
    client = await _client()
    try:
        async with client:
            saved = (await client.post("/api/factor-sets",
                                       json={"name": "待删", "members": _MEMBERS, "method": "equal"})).json()
            sid, companion_id = saved["id"], saved["strategyId"]
            assert (await client.delete(f"/api/factor-sets/{sid}")).status_code == 204
            assert all(s["id"] != companion_id for s in await _strategies(client))
    finally:
        await dispose_engine()


async def test_update_collapses_multiple_companions(patch_okx):
    """对人为构造的 2 条同 factorSetId 伴生 PUT → 坍缩为正典(min id)一条，其余被删（R2）。"""
    from app.db import repositories as repo
    from app.db.session import dispose_engine, get_sessionmaker
    client = await _client()
    try:
        async with client:
            saved = (await client.post("/api/factor-sets",
                                       json={"name": "多伴生", "members": _MEMBERS, "method": "equal"})).json()
            sid, canonical_id = saved["id"], saved["strategyId"]
            async with get_sessionmaker()() as session:
                dup = await repo.add_strategy(session, "factor_score", "多伴生 · 得分策略(dup)",
                                              {"members": [{"factorKey": "mom_roc", "params": {}, "weight": 0.0,
                                                            "direction": "long"}],
                                               "thresholds": {}, "factorSetId": sid})
                await session.commit()
                dup_id = dup.id
            async with get_sessionmaker()() as session:
                rows = await repo.list_factor_score_rows_for_set(session, sid)
            assert {r.id for r in rows} == {canonical_id, dup_id}
            upd = (await client.put(f"/api/factor-sets/{sid}", json={"name": "多伴生改"})).json()
            assert upd["strategyId"] == canonical_id     # 正典 = min id
            async with get_sessionmaker()() as session:
                rows2 = await repo.list_factor_score_rows_for_set(session, sid)
            assert [r.id for r in rows2] == [canonical_id]
            assert all(s["id"] != dup_id for s in await _strategies(client))
    finally:
        await dispose_engine()


async def test_orphan_factor_strategy_untouched_by_sync(patch_okx):
    """无 factorSetId 的孤儿 factor_score 策略不被任何组合的 update/delete 误删/误改（DoD #4）。"""
    from app.db.session import dispose_engine
    client = await _client()
    try:
        async with client:
            orphan = (await client.post("/api/strategies", json={
                "templateKey": "factor_score", "name": "孤儿因子策略",
                "params": {"members": [{"factorKey": "mom_roc", "direction": "long"}],
                           "thresholds": {"normWindow": 80}}})).json()
            orphan_id = orphan["id"]
            saved = (await client.post("/api/factor-sets",
                                       json={"name": "无关组合", "members": _MEMBERS, "method": "equal"})).json()
            sid = saved["id"]
            await client.put(f"/api/factor-sets/{sid}", json={"name": "无关组合改"})
            await client.delete(f"/api/factor-sets/{sid}")
            strategies = await _strategies(client)
            kept = next(s for s in strategies if s["id"] == orphan_id)
            assert kept["name"] == "孤儿因子策略"       # 名称未被任何组合覆盖
    finally:
        await dispose_engine()


async def test_create_set_atomic_rollback_on_companion_failure(monkeypatch):
    """伴生策略落库失败 → 整体回滚：factor_sets 与 strategies 两表均无新行（R8，单 commit 前异常）。"""
    from app.db import repositories as repo
    from app.db.session import dispose_engine, get_sessionmaker, init_db
    from app.factors import service
    await init_db()
    try:
        async def _boom(*args, **kwargs):
            raise RuntimeError("inject companion failure")

        monkeypatch.setattr(repo, "add_strategy", _boom)
        sm = get_sessionmaker()
        async with sm() as session:
            before_sets = len(await repo.list_factor_sets(session))
            before_strats = len(await repo.list_strategies(session, None))
        with pytest.raises(RuntimeError):
            async with sm() as session:
                await service.create_set(session, name="ATOMIC_X",
                                         members_raw=[{"factorKey": "mom_roc", "direction": "long"}],
                                         method="equal", config=None, model=None, metrics=None, enabled=True)
        async with sm() as session:
            after_sets = len(await repo.list_factor_sets(session))
            after_strats = len(await repo.list_strategies(session, None))
        assert after_sets == before_sets and after_strats == before_strats
    finally:
        await dispose_engine()


async def test_create_set_invalid_members_writes_nothing():
    """非法成员（auto 方向）→ 校验在落库前 400，factor_sets/strategies 两表均无写入。"""
    from fastapi import HTTPException

    from app.db import repositories as repo
    from app.db.session import dispose_engine, get_sessionmaker, init_db
    from app.factors import service
    await init_db()
    try:
        sm = get_sessionmaker()
        async with sm() as session:
            before_sets = len(await repo.list_factor_sets(session))
            before_strats = len(await repo.list_strategies(session, None))
        with pytest.raises(HTTPException):
            async with sm() as session:
                await service.create_set(session, name="BAD_X",
                                         members_raw=[{"factorKey": "mom_roc", "direction": "auto"}],
                                         method="equal", config=None, model=None, metrics=None, enabled=True)
        async with sm() as session:
            after_sets = len(await repo.list_factor_sets(session))
            after_strats = len(await repo.list_strategies(session, None))
        assert after_sets == before_sets and after_strats == before_strats
    finally:
        await dispose_engine()


async def test_backfill_companions_idempotent():
    """backfill_companions 对历史无伴生组合恰补建一条，二次运行不重复建（R6 幂等）。"""
    from app.db import repositories as repo
    from app.db.session import dispose_engine, get_sessionmaker, init_db
    from app.factors import service
    await init_db()
    try:
        async with get_sessionmaker()() as session:
            row = await repo.create_factor_set(session, values=repo.FactorSetValues(
                name="补建组合", members=[{"factorKey": "mom_roc", "params": {}, "weight": 0.0,
                                            "direction": "long"}],
                method="equal", config={}, model={}, metrics=None, enabled=True))
            hid = row.id
        async with get_sessionmaker()() as session:
            created = await service.backfill_companions(session)
        assert created >= 1
        async with get_sessionmaker()() as session:
            rows = await repo.list_factor_score_rows_for_set(session, hid)
        assert len(rows) == 1
        async with get_sessionmaker()() as session:
            await service.backfill_companions(session)
        async with get_sessionmaker()() as session:
            rows2 = await repo.list_factor_score_rows_for_set(session, hid)
        assert [r.id for r in rows2] == [rows[0].id]   # 同一正典，未重复建
    finally:
        await dispose_engine()


async def test_agent_save_factor_set_deploys_companion():
    """Agent save_factor_set 共用 create_set，保存组合后同步出现伴生策略（P0b 回归护栏）。"""
    from app.agent.tools.base import ToolContext, UiContext
    from app.agent.tools.registry import ToolRegistry
    from app.db import repositories as repo
    from app.db.session import dispose_engine, get_sessionmaker, init_db
    await init_db()
    try:
        ctx = ToolContext(session_factory=get_sessionmaker(),
                          ui=UiContext(instId="BTC-USDT-SWAP", bar="1H"))
        res = await ToolRegistry().execute(
            "save_factor_set",
            {"name": "Agent同步组合", "members": [{"factorKey": "mom_roc", "direction": "long"}],
             "method": "equal"}, ctx)
        assert res.ok, res.summary
        sid, companion_id = res.payload["id"], res.payload["strategyId"]
        assert companion_id is not None
        async with get_sessionmaker()() as session:
            comp = await repo.get_strategy(session, companion_id)
            rows = await repo.list_factor_score_rows_for_set(session, sid)
        assert comp is not None and comp["kind"] == "factor"
        assert [r.id for r in rows] == [companion_id]
    finally:
        await dispose_engine()
