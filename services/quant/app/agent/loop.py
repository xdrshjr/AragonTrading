"""AgentSession：agentic loop（pi AgentLoop 等价实现）(spec §2.3)。

每轮：流式累积助手输出 → 若有 tool_use 则顺序执行并回喂 → 否则结束。支持取消 / 断连即取消
（P1-D，走同一中断路径）、上下文裁剪（P2-2）。函数按 CLAUDE.md 阈值拆分（P2-3）。
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from ..config import Settings
from ..db import repositories as repo
from .conversation import blocks_to_json, row_to_llm_message
from .llm.base import (
    LLMClient,
    LlmMessage,
    StreamEvent,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from .llm.retry import normalize_usage
from .prompts import build_system_prompt
from .tools.base import ToolContext, UiContext
from .tools.registry import ToolRegistry

logger = logging.getLogger("aragon.agent.loop")

Emit = Callable[[dict], Awaitable[None]]


def _event(event_type: str, data: dict) -> dict:
    return {"type": event_type, "data": data}


class _ToolAcc:
    """流式 tool_use 累积器：name + 分片 input_json。"""

    def __init__(self, tool_id: str, name: str | None):
        self.id = tool_id
        self.name = name or ""
        self._parts: list[str] = []

    def add(self, partial: str) -> None:
        self._parts.append(partial)

    def to_block(self) -> ToolUseBlock:
        raw = "".join(self._parts).strip() or "{}"
        try:
            args = json.loads(raw)
        except json.JSONDecodeError:
            args = {}
        return ToolUseBlock(id=self.id, name=self.name, input=args)


@dataclass
class _StreamAcc:
    """单轮流式累积：文本分片 + 工具累积器 + 末轮 usage / stop_reason。"""

    text_parts: list[str] = field(default_factory=list)
    accs: dict[str, _ToolAcc] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    usage: dict | None = None
    stop_reason: str | None = None


@dataclass
class _RunStats:
    """单次 run 的可观测统计（结构化日志用，仅元数据，绝不含密钥 / 用户原文，C7）。"""

    rounds: int = 0
    tools: list[dict] = field(default_factory=list)
    stop_reason: str | None = None
    in_tokens: int | None = None
    out_tokens: int | None = None


class AgentSession:
    """单会话 agentic 运行态：内存 messages（喂模型）+ 取消 / 并发锁。"""

    def __init__(self, conversation_id: str, llm: LLMClient, registry: ToolRegistry,
                 settings: Settings, session_factory: Callable):
        self.conversation_id = conversation_id
        self._llm = llm
        self._registry = registry
        self._settings = settings
        self._session_factory = session_factory
        self.messages: list[LlmMessage] = []
        self._loaded = False
        self._running = False
        self._cancel = False

    @property
    def running(self) -> bool:
        return self._running

    @property
    def loaded(self) -> bool:
        return self._loaded

    def cancel(self) -> None:
        self._cancel = True

    async def load_from_db(self) -> None:
        async with self._session_factory() as session:
            rows = await repo.list_agent_messages(
                session, self.conversation_id, limit=self._settings.agent_max_context_messages)
        self.messages = [row_to_llm_message(r) for r in rows]
        self._loaded = True

    async def run(self, user_text: str, ui: dict | None, emit: Emit) -> None:
        if self._running:
            await emit(_event("error", {"code": "BUSY", "message": "上一条仍在处理，请稍候。"}))
            return
        self._running = True
        self._cancel = False
        ctx = ToolContext(session_factory=self._session_factory, ui=UiContext.from_dict(ui),
                          cancel_check=lambda: self._cancel)
        try:
            await self._run_rounds(user_text, ui, ctx, emit)
        finally:
            self._running = False

    async def _run_rounds(self, user_text: str, ui: dict | None,
                          ctx: ToolContext, emit: Emit) -> None:
        await self._append_and_persist(LlmMessage("user", [TextBlock(user_text)]))
        system = build_system_prompt(ui)
        stats = _RunStats()
        outcome = await self._iterate_tool_rounds(system, ctx, emit, stats)
        if outcome == "limit":
            await self._finalize_on_limit(system, emit, stats)
        self._log_run(stats)
        await emit(_event("done", {"cancelled": self._cancel}))

    async def _iterate_tool_rounds(self, system: str, ctx: ToolContext,
                                   emit: Emit, stats: _RunStats) -> str:
        """跑工具轮直到自然收尾 / 出错 / 取消（返回对应状态），或触顶返回 "limit"。"""
        for _ in range(self._settings.agent_max_tool_rounds):
            if self._cancel:
                return "cancelled"
            assistant_msg, errored = await self._stream_assistant_turn(system, emit, stats)
            stats.rounds += 1
            if assistant_msg.content:
                await self._append_and_persist(assistant_msg)
            if errored:
                return "error"
            tool_uses = [b for b in assistant_msg.content if isinstance(b, ToolUseBlock)]
            if not tool_uses:
                return "ended"
            if self._cancel:
                return "cancelled"
            await self._execute_tool_calls(tool_uses, ctx, emit, stats)
        return "limit"

    async def _finalize_on_limit(self, system: str, emit: Emit, stats: _RunStats) -> None:
        """轮数耗尽：禁用工具再请求一次文字总结（H9）；已取消则不再调用 LLM（RN-7）。"""
        if self._cancel:
            return
        assistant_msg, errored = await self._stream_assistant_turn(system, emit, stats, tools=[])
        stats.rounds += 1
        if assistant_msg.content:
            await self._append_and_persist(assistant_msg)
        summarized = any(isinstance(b, TextBlock) and b.text for b in assistant_msg.content)
        if errored or not summarized:
            await emit(_event("error", {"code": "TOOL_LOOP_LIMIT",
                                        "message": "工具调用轮数已达上限。"}))

    def _log_run(self, stats: _RunStats) -> None:
        """run 结束结构化日志（轮数 / 每工具耗时 / stop_reason / token，仅元数据，C7）。"""
        logger.info("agent run complete: %s", {
            "conv": self.conversation_id, "rounds": stats.rounds, "tools": stats.tools,
            "stop_reason": stats.stop_reason, "in_tokens": stats.in_tokens,
            "out_tokens": stats.out_tokens})

    async def _stream_assistant_turn(self, system: str, emit: Emit, stats: _RunStats,
                                     tools: list | None = None) -> tuple[LlmMessage, bool]:
        """单轮流式累积：emit 增量事件并返回（assistant 消息, 是否出错）。`tools=[]` 即禁用工具。"""
        msg_id = uuid.uuid4().hex
        await emit(_event("message_start", {"id": msg_id, "role": "assistant"}))
        acc = _StreamAcc()
        errored = False
        schemas = self._registry.schemas() if tools is None else tools
        async for ev in self._llm.stream(system=system, messages=self._trimmed_messages(),
                                          tools=schemas):
            if self._cancel:
                break
            done = await self._on_stream_event(ev, msg_id, acc, emit)
            if done == "error":
                errored = True
                break
            if done == "stop":
                break
        await emit(_event("message_end", self._message_end_data(msg_id, acc, stats)))
        return LlmMessage("assistant", self._build_assistant_blocks(acc)), errored

    def _message_end_data(self, msg_id: str, acc: _StreamAcc, stats: _RunStats) -> dict:
        """构造 message_end 负载：归一化 usage（RN-2）并累计进运行统计（H5）。"""
        usage = normalize_usage(getattr(self._llm, "provider", "anthropic"), acc.usage)
        stats.stop_reason = acc.stop_reason or stats.stop_reason
        if usage:
            stats.in_tokens = (stats.in_tokens or 0) + (usage.get("inputTokens") or 0)
            stats.out_tokens = (stats.out_tokens or 0) + (usage.get("outputTokens") or 0)
        data: dict = {"id": msg_id}
        if usage:
            data["usage"] = usage
        return data

    async def _on_stream_event(self, ev: StreamEvent, msg_id: str, acc: _StreamAcc,
                               emit: Emit) -> str | None:
        """消费单个归一化流事件，返回 None|"stop"|"error"。"""
        if ev.kind == "text_delta":
            acc.text_parts.append(ev.text or "")
            await emit(_event("text_delta", {"id": msg_id, "text": ev.text or ""}))
        elif ev.kind == "thinking_delta":
            await emit(_event("thinking_delta", {"id": msg_id, "text": ev.text or ""}))
        elif ev.kind == "tool_use_start":
            acc.accs[ev.id] = _ToolAcc(ev.id, ev.name)
            acc.order.append(ev.id)
        elif ev.kind == "tool_use_args_delta":
            if ev.id in acc.accs:
                acc.accs[ev.id].add(ev.partial_json or "")
        elif ev.kind == "message_done":
            acc.usage = ev.usage
            acc.stop_reason = ev.stop_reason
            return "stop"
        elif ev.kind == "error":
            await emit(_event("error", {"code": ev.code or "LLM_ERROR",
                                        "message": ev.message or "模型调用失败"}))
            return "error"
        return None

    def _build_assistant_blocks(self, acc: _StreamAcc) -> list:
        blocks: list = []
        text = "".join(acc.text_parts)
        if text:
            blocks.append(TextBlock(text))
        for tool_id in acc.order:
            blocks.append(acc.accs[tool_id].to_block())
        return blocks

    async def _execute_tool_calls(self, tool_uses: list[ToolUseBlock], ctx: ToolContext,
                                  emit: Emit, stats: _RunStats) -> None:
        """顺序执行工具，emit 结果 / ui_action，回喂 tool_result（断连即取消亦保持配对）。"""
        result_blocks: list[ToolResultBlock] = []
        for tu in tool_uses:
            if self._cancel:
                break
            await emit(_event("tool_call", {"id": tu.id, "name": tu.name, "input": tu.input}))
            started = time.perf_counter()
            result = await self._registry.execute(tu.name, tu.input, ctx)
            stats.tools.append({"name": tu.name, "ok": result.ok,
                                "ms": round((time.perf_counter() - started) * 1000, 1)})
            await emit(_event("tool_result", {"id": tu.id, "name": tu.name,
                                              "ok": result.ok, "summary": result.summary}))
            if self._registry.is_ui_action(tu.name) and result.ok and result.payload:
                await emit(_event("ui_action", result.payload))
            result_blocks.append(ToolResultBlock(tool_use_id=tu.id, content=result.summary,
                                                 is_error=not result.ok))
        # 为未执行的 tool_use 补"已取消"结果，保持 tool_use↔tool_result 配对（resume 无损，P1-D）。
        for tu in tool_uses[len(result_blocks):]:
            result_blocks.append(ToolResultBlock(tool_use_id=tu.id, content="已取消", is_error=True))
        await self._append_and_persist(LlmMessage("tool", result_blocks))

    def _trimmed_messages(self) -> list[LlmMessage]:
        """回喂模型前裁剪到近 N 条，并从 user 轮起头（避免孤儿 tool_result，P2-2）。"""
        msgs = self.messages[-self._settings.agent_max_context_messages:]
        while msgs and msgs[0].role != "user":
            msgs = msgs[1:]
        return msgs

    async def _append_and_persist(self, msg: LlmMessage) -> None:
        self.messages.append(msg)
        async with self._session_factory() as session:
            await repo.append_agent_message(
                session, self.conversation_id, msg.role, blocks_to_json(msg.content),
                int(time.time()))
