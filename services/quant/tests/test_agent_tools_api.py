"""Agent 工具管理 REST + 持久化 + 启用集解析单测（设置中心 · Agent 工具管理）。

测试隔离（P1-2）：services/quant 测试共用同一临时 DB，`agent_tool_states` 为稀疏表，跨用例 /
跨文件的启用态残留会污染「缺省即启用」断言。故每个用例起始调用 `_reset_tool_states()` 清表；
`finally` 仍 `dispose_engine()`。
"""
from __future__ import annotations

from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.agent.manager import AgentManager
from app.agent.tools.registry import _ALL_TOOLS, ToolRegistry
from app.config import Settings
from app.db import repositories as repo
from app.db.models import AgentToolState
from app.db.session import dispose_engine, get_sessionmaker, init_db


def _load_app():
    from app.main import app
    return app


async def _reset_tool_states() -> None:
    """清空 `agent_tool_states`（确定起步：空表即「全部启用」，消除稀疏表跨用例污染）。"""
    await init_db()
    sm = get_sessionmaker()
    async with sm() as session:
        await session.execute(delete(AgentToolState))
        await session.commit()


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=_load_app()), base_url="http://test")


def _manager() -> AgentManager:
    return AgentManager(Settings(agent_api_key="x"), registry=ToolRegistry(),
                        session_factory=get_sessionmaker())


async def test_list_tools_returns_all_registry_tools_enabled_by_default():
    await _reset_tool_states()
    try:
        async with await _client() as client:
            r = await client.get("/api/agent/tools")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["totalCount"] == len(_ALL_TOOLS)
            assert body["enabledCount"] == body["totalCount"]
            assert all(t["enabled"] is True for t in body["tools"])
            assert all("category" in t and "paramsSchema" in t for t in body["tools"])
            # 顺序与注册表一致（C6：展示真源即注册表）。
            assert [t["name"] for t in body["tools"]] == [tool.name for tool in _ALL_TOOLS]
    finally:
        await dispose_engine()


async def test_put_tool_disable_persists():
    await _reset_tool_states()
    try:
        async with await _client() as client:
            r = await client.put("/api/agent/tools/run_backtest", json={"enabled": False})
            assert r.status_code == 200, r.text
            assert r.json()["enabled"] is False
            body = (await client.get("/api/agent/tools")).json()
            target = next(t for t in body["tools"] if t["name"] == "run_backtest")
            assert target["enabled"] is False
            assert body["enabledCount"] == body["totalCount"] - 1
    finally:
        await dispose_engine()


async def test_put_tool_reenable():
    await _reset_tool_states()
    try:
        async with await _client() as client:
            await client.put("/api/agent/tools/run_backtest", json={"enabled": False})
            r = await client.put("/api/agent/tools/run_backtest", json={"enabled": True})
            assert r.status_code == 200 and r.json()["enabled"] is True
            body = (await client.get("/api/agent/tools")).json()
            assert body["enabledCount"] == body["totalCount"]
    finally:
        await dispose_engine()


async def test_put_unknown_tool_returns_404():
    await _reset_tool_states()
    try:
        async with await _client() as client:
            r = await client.put("/api/agent/tools/not_a_tool", json={"enabled": False})
            assert r.status_code == 404
            assert r.json()["error"]["code"] == "TOOL_NOT_FOUND"
    finally:
        await dispose_engine()


async def test_bulk_disable_then_enable():
    await _reset_tool_states()
    try:
        async with await _client() as client:
            r = await client.post("/api/agent/tools/bulk", json={"enabled": False})
            assert r.status_code == 200, r.text
            assert r.json()["enabledCount"] == 0
            r = await client.post("/api/agent/tools/bulk", json={"enabled": True})
            body = r.json()
            assert body["enabledCount"] == body["totalCount"] == len(_ALL_TOOLS)
    finally:
        await dispose_engine()


async def test_resolve_enabled_tools_defaults_all_enabled():
    """空覆盖表 → 解析得全集（验证「无记录即启用」分支，P1-3）。"""
    await _reset_tool_states()
    try:
        mgr = _manager()
        assert await mgr.resolve_enabled_tools() == mgr._registry.all_names()
    finally:
        await dispose_engine()


async def test_resolve_enabled_tools_applies_db_overrides():
    """禁用一项后解析 → 恰少该项，其余仍全在（合并逻辑 states.get(n, True)，P1-3）。"""
    await _reset_tool_states()
    try:
        sm = get_sessionmaker()
        async with sm() as session:
            await repo.set_tool_enabled(session, "run_backtest", False)
        mgr = _manager()
        enabled = await mgr.resolve_enabled_tools()
        assert enabled == mgr._registry.all_names() - {"run_backtest"}
    finally:
        await dispose_engine()
