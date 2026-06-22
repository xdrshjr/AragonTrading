"""ToolRegistry 启用过滤单测：schemas / execute / disabled_names 的 enabled 形参语义。"""
from __future__ import annotations

from app.agent.tools.base import ToolContext, UiContext
from app.agent.tools.registry import ToolRegistry


def _ctx() -> ToolContext:
    # 禁用 / 未知判定在 execute 早返回，不触达 session_factory；给占位即可（不建 DB）。
    return ToolContext(session_factory=lambda: None, ui=UiContext())


async def test_schemas_filters_by_enabled():
    registry = ToolRegistry()
    only = registry.schemas({"get_candles"})
    assert {s.name for s in only} == {"get_candles"}
    assert {s.name for s in registry.schemas(None)} == registry.all_names()  # None 不过滤


async def test_execute_rejects_disabled_tool():
    registry = ToolRegistry()
    result = await registry.execute("run_backtest", {}, _ctx(), enabled=set())
    assert result.ok is False
    assert "已被禁用" in result.summary


async def test_disabled_names():
    registry = ToolRegistry()
    all_names = registry.all_names()
    assert registry.disabled_names({"get_candles"}) == sorted(all_names - {"get_candles"})
    assert registry.disabled_names(None) == []  # None 不过滤 → 无禁用项


async def test_execute_unknown_tool_still_unknown():
    """未知名 + 任意 enabled → 「未知工具」（禁用判定不掩盖未知，保持 404 语义）。"""
    registry = ToolRegistry()
    result = await registry.execute("not_a_tool", {}, _ctx(), enabled={"get_candles"})
    assert result.ok is False
    assert "未知工具" in result.summary
