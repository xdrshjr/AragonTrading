"""AgentSession agentic loop 单测（假 LLMClient 驱动，真实 DB + 真实 registry）。"""
from __future__ import annotations

from app.agent.llm.base import StreamEvent
from app.agent.loop import AgentSession
from app.agent.tools.registry import ToolRegistry
from app.config import Settings
from app.db import repositories as repo
from app.db.session import dispose_engine, get_sessionmaker, init_db


class _ScriptedLLM:
    """按 turns 脚本化产出归一化流事件。"""

    def __init__(self, turns):
        self._turns = turns
        self.calls = 0

    async def stream(self, *, system, messages, tools):
        turn = self._turns[min(self.calls, len(self._turns) - 1)]
        self.calls += 1
        for ev in turn:
            yield ev


class _CancelingLLM:
    def __init__(self):
        self.session = None

    async def stream(self, *, system, messages, tools):
        yield StreamEvent(kind="tool_use_start", id="t1", name="show_chart")
        yield StreamEvent(kind="tool_use_args_delta", id="t1",
                          partial_json='{"instId":"BTC-USDT-SWAP"}')
        self.session.cancel()  # 模拟 run 中途取消 / 断连
        yield StreamEvent(kind="text_delta", text="(应被跳过)")


def _settings(**kw):
    return Settings(agent_api_key="x", agent_provider="anthropic", **kw)


def _session(llm, settings):
    return AgentSession("conv-loop", llm, ToolRegistry(), settings, get_sessionmaker())


async def _collect_run(session, text="画出 BTC 永续日线"):
    events: list[dict] = []

    async def emit(ev):
        events.append(ev)

    await session.run(text, {"instId": "BTC-USDT-SWAP", "bar": "1D"}, emit)
    return events


async def test_tool_round_then_end_turn_executes_and_emits_ui_action():
    await init_db()
    llm = _ScriptedLLM([
        [
            StreamEvent(kind="text_delta", text="我来画图"),
            StreamEvent(kind="tool_use_start", id="t1", name="show_chart"),
            StreamEvent(kind="tool_use_args_delta", id="t1",
                        partial_json='{"instId":"BTC-USDT-SWAP","bar":"1D"}'),
            StreamEvent(kind="message_done", stop_reason="tool_use"),
        ],
        [
            StreamEvent(kind="text_delta", text="已切换主图"),
            StreamEvent(kind="message_done", stop_reason="end_turn"),
        ],
    ])
    try:
        events = await _collect_run(_session(llm, _settings()))
        types = [e["type"] for e in events]
        assert "message_start" in types
        assert any(e["type"] == "tool_call" and e["data"]["name"] == "show_chart" for e in events)
        tool_result = next(e for e in events if e["type"] == "tool_result")
        assert tool_result["data"]["ok"] is True
        ui = next(e for e in events if e["type"] == "ui_action")
        assert ui["data"]["action"] == "show_chart"
        assert events[-1]["type"] == "done" and events[-1]["data"]["cancelled"] is False

        sm = get_sessionmaker()
        async with sm() as session:
            rows = await repo.list_agent_messages(session, "conv-loop")
        assert [r.role for r in rows] == ["user", "assistant", "tool", "assistant"]
    finally:
        await dispose_engine()


async def test_tool_loop_limit_emits_error():
    await init_db()
    tool_turn = [
        StreamEvent(kind="tool_use_start", id="t1", name="list_strategies"),
        StreamEvent(kind="message_done", stop_reason="tool_use"),
    ]
    llm = _ScriptedLLM([tool_turn])  # 每轮都要工具 → 触顶
    try:
        events = await _collect_run(_session(llm, _settings(agent_max_tool_rounds=2)))
        assert any(e["type"] == "error" and e["data"]["code"] == "TOOL_LOOP_LIMIT" for e in events)
        assert events[-1]["type"] == "done"
    finally:
        await dispose_engine()


async def test_cancel_mid_stream_stops_before_executing_tools():
    await init_db()
    llm = _CancelingLLM()
    session = _session(llm, _settings())
    llm.session = session
    try:
        events = await _collect_run(session)
        assert events[-1]["type"] == "done" and events[-1]["data"]["cancelled"] is True
        # 取消后不应执行工具：无 ui_action / tool_result。
        assert not any(e["type"] in ("ui_action", "tool_result") for e in events)
    finally:
        await dispose_engine()
