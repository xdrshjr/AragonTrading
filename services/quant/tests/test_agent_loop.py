"""AgentSession agentic loop 单测（假 LLMClient 驱动，真实 DB + 真实 registry）。"""
from __future__ import annotations

import asyncio

from app.agent.llm.base import StreamEvent
from app.agent.loop import AgentSession, _RunStats
from app.agent.tools.registry import ToolRegistry
from app.config import Settings
from app.db import repositories as repo
from app.db.session import dispose_engine, get_sessionmaker, init_db


class _ScriptedLLM:
    """按 turns 脚本化产出归一化流事件；记录最近一次收到的 system / tools 供启用门控断言。"""

    def __init__(self, turns):
        self._turns = turns
        self.calls = 0
        self.last_system = None
        self.last_tools = None

    async def stream(self, *, system, messages, tools):
        self.last_system = system
        self.last_tools = tools
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


async def test_message_end_carries_normalized_camelcase_usage():
    """假 LLM 在 message_done 给原始 snake_case usage，断言外发为归一 camelCase（RN-2）。"""
    await init_db()
    llm = _ScriptedLLM([
        [
            StreamEvent(kind="text_delta", text="你好"),
            StreamEvent(kind="message_done", stop_reason="end_turn",
                        usage={"input_tokens": 11, "output_tokens": 22}),
        ],
    ])
    try:
        events = await _collect_run(_session(llm, _settings()), text="hi")
        end = next(e for e in events if e["type"] == "message_end")
        assert end["data"]["usage"] == {"inputTokens": 11, "outputTokens": 22}
        assert "input_tokens" not in end["data"]["usage"]  # 不外泄原始 snake_case
    finally:
        await dispose_engine()


async def test_round_limit_triggers_toolless_summary_then_done():
    """命中轮上限后追加禁用工具的终轮总结（产文本即不报 TOOL_LOOP_LIMIT）(H9)。"""
    await init_db()
    tool_turn = [
        StreamEvent(kind="tool_use_start", id="t1", name="list_strategies"),
        StreamEvent(kind="message_done", stop_reason="tool_use"),
    ]
    llm = _ScriptedLLM([
        tool_turn, tool_turn,
        [StreamEvent(kind="text_delta", text="综上，已尽力总结。"),
         StreamEvent(kind="message_done", stop_reason="end_turn")],
    ])
    try:
        events = await _collect_run(_session(llm, _settings(agent_max_tool_rounds=2)))
        assert not any(e["type"] == "error" and e["data"]["code"] == "TOOL_LOOP_LIMIT"
                       for e in events)
        assert any(e["type"] == "text_delta" and "总结" in e["data"]["text"] for e in events)
        assert events[-1]["type"] == "done"
    finally:
        await dispose_engine()


async def test_finalize_on_limit_skips_llm_when_cancelled():
    """终轮总结前命中取消 → 不再发起 LLM，亦不 emit 事件（RN-7；emit→sink 适配）。"""
    await init_db()
    llm = _ScriptedLLM([[StreamEvent(kind="text_delta", text="不应运行"),
                         StreamEvent(kind="message_done", stop_reason="end_turn")]])
    session = _session(llm, _settings())
    session._cancel = True
    events: list[dict] = []

    async def emit(ev):
        events.append(ev)

    session.set_sink(emit)
    try:
        await session._finalize_on_limit("sys", _RunStats())
        assert llm.calls == 0  # 已取消：未发起 LLM 调用
        assert events == []    # 未 emit 任何事件
    finally:
        await dispose_engine()


async def test_run_events_carry_monotonic_seq():
    """run 生命周期事件经 _emit 注入单调递增 seq（重放对账锚点，RS-1）。"""
    await init_db()
    llm = _ScriptedLLM([[StreamEvent(kind="text_delta", text="hi"),
                         StreamEvent(kind="message_done", stop_reason="end_turn")]])
    try:
        events = await _collect_run(_session(llm, _settings()), text="hi")
        assert all("seq" in e for e in events)  # 所有 run 事件带 seq
        seqs = [e["seq"] for e in events]
        assert seqs == list(range(1, len(seqs) + 1))  # 单调 1..N，无缺号、无重复
    finally:
        await dispose_engine()


async def test_run_filters_schemas_by_enabled_tools():
    """启用门控（P1-3）：enabled_tools 仅含 get_candles → 桩收到的 tools 仅该项，其余进禁用说明。"""
    await init_db()
    llm = _ScriptedLLM([[StreamEvent(kind="text_delta", text="ok"),
                         StreamEvent(kind="message_done", stop_reason="end_turn")]])
    session = _session(llm, _settings())

    async def emit(_ev):
        pass

    try:
        await session.run("hi", {"instId": "BTC-USDT-SWAP", "bar": "1D"}, emit,
                          enabled_tools={"get_candles"})
        names = {s.name for s in llm.last_tools}
        assert names == {"get_candles"}  # schema 硬闸门：模型只见启用工具
        marker = "以下工具当前已被用户禁用，本轮不可调用："
        assert marker in llm.last_system
        clause = llm.last_system.split(marker, 1)[1]
        assert "run_backtest" in clause          # 禁用工具进系统提示说明
        assert "get_candles" not in clause       # 启用工具不在禁用清单
    finally:
        await dispose_engine()


async def test_run_enabled_tools_none_keeps_all():
    """回归：enabled_tools=None（直驱 / 旧路径）→ 桩收到全部 13 工具，且不追加禁用说明。"""
    await init_db()
    llm = _ScriptedLLM([[StreamEvent(kind="text_delta", text="ok"),
                         StreamEvent(kind="message_done", stop_reason="end_turn")]])
    try:
        await _collect_run(_session(llm, _settings()), text="hi")
        names = {s.name for s in llm.last_tools}
        assert names == ToolRegistry().all_names()         # 默认参数不过滤，全部可见
        assert "以下工具当前已被用户禁用" not in llm.last_system  # 无禁用说明
    finally:
        await dispose_engine()


class _SlowLLM:
    """每个 text_delta 间 sleep，使 run 持续足够久供心跳 / 宽限测。"""

    def __init__(self, n: int = 5, delay: float = 0.02):
        self.n = n
        self.delay = delay

    async def stream(self, *, system, messages, tools):
        for i in range(self.n):
            await asyncio.sleep(self.delay)
            yield StreamEvent(kind="text_delta", text=str(i))
        yield StreamEvent(kind="message_done", stop_reason="end_turn")


async def test_heartbeat_streams_during_run_and_is_reaped_after():
    """run 期心跳周期下行（不带 seq、不入缓冲）；run 结束后心跳任务被回收（RN-2/RN-7）。"""
    await init_db()
    session = _session(_SlowLLM(), _settings(agent_heartbeat_seconds=0.01))
    try:
        events = await _collect_run(session, text="hi")
        heartbeats = [e for e in events if e["type"] == "heartbeat"]
        assert heartbeats                               # run 期至少一跳
        assert all("seq" not in e for e in heartbeats)  # 心跳不带 seq（RN-2）
        assert session._heartbeat_task is None          # run 结束已回收（RN-7）
        # 心跳不入缓冲：缓冲里只有真实 run 事件，绝无 heartbeat。
        assert all(ev["type"] != "heartbeat" for _, ev in session._buffer)
    finally:
        await dispose_engine()
