"""AgentSession：agentic loop（pi AgentLoop 等价实现）(spec §2.3)。

每轮：流式累积助手输出 → 若有 tool_use 则顺序执行并回喂 → 否则结束。支持取消 / 上下文裁剪。

韧性流式（iteration 3, RS-1/RS-2/RS-7）：事件经会话级「带序号缓冲 + 可热切换 sink」间接发出，
run 与单条 WS 连接解耦——断连即「脱离」而非取消，重连可重放遗漏事件并无缝续流。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

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
from .tools.base import ToolContext, ToolResult, UiContext
from .tools.registry import ToolRegistry

if TYPE_CHECKING:
    from ..agent_trading.schemas import AgentCallContext

logger = logging.getLogger("aragon.agent.loop")


def _tool_result_block(tool_id: str, result: ToolResult) -> ToolResultBlock:
    """结构化事实只进入模型正文，卡片使用独立中文摘要。"""
    content = result.summary
    if result.model_payload is not None:
        encoded = json.dumps(result.model_payload, ensure_ascii=False)
        if len(encoded.encode("utf-8")) > 24 * 1024:
            encoded = json.dumps({"truncated": True,
                                  "reason": "结果超出展示上限，请缩小查询范围"},
                                 ensure_ascii=False)
        content += "\n" + encoded
    return ToolResultBlock(
        tool_use_id=tool_id, content=content, is_error=not result.ok,
        display_payload=(result.payload if result.model_payload is not None
                         else None),
        display_summary=(result.summary if result.model_payload is not None
                         else None),
        code=result.code,
    )

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


@dataclass(frozen=True)
class RunToolContext:
    """每轮启用集与冻结账户作为一个不可变执行上下文传入。"""
    enabled: set[str]
    call: AgentCallContext


class AgentSession:
    """单会话 agentic 运行态：内存 messages（喂模型）+ 取消 + 带序号事件缓冲 / 可切换 sink。"""

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
        # 本轮启用工具集（设置中心 · Agent 工具管理解析后注入）；None = 不过滤（兼容直驱 / 单测）。
        self._enabled_tools: set[str] | None = None
        # 韧性流式运行态（RS-1）：会话级单调 seq + 当前 run 事件缓冲 + 可热切换 sink + 任务句柄。
        self._seq = 0                                       # 跨重连不重置（重放对账锚点）
        self._buffer: deque[tuple[int, dict]] = deque(
            maxlen=settings.agent_event_buffer_size)        # 仅当前 run 的真实事件窗口
        self._sink: Emit | None = None                      # 当前挂载的 WS 发送闭包；断连即 None
        self._sink_lock = asyncio.Lock()                    # 串行 _emit 与 resume，杜绝重放交错
        self._run_task: asyncio.Task | None = None          # run 归属会话（跨连接存活，RS-2）
        self._heartbeat_task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self._running

    @property
    def loaded(self) -> bool:
        return self._loaded

    @property
    def cancelled(self) -> bool:
        return self._cancel

    @property
    def has_buffered(self) -> bool:
        """当前 run 是否已产出（缓冲非空）——attach 据此判定能否安全立即转实时。"""
        return len(self._buffer) > 0

    @property
    def run_task(self) -> asyncio.Task | None:
        return self._run_task

    def cancel(self) -> None:
        self._cancel = True
        self._cancel_heartbeat()  # RN-7：取消即停心跳，杜绝泄漏

    def bind_run_task(self, task: asyncio.Task) -> None:
        """manager 将后台 run task 句柄回挂到会话（跨连接存活 / 宽限取消用，RS-2）。"""
        self._run_task = task

    # ============================ sink / 缓冲 / 续流原语（RS-1/RS-2） ============================
    def set_sink(self, sink: Emit | None) -> None:
        """挂载当前 WS 发送闭包（attach 用：fresh run 或 run 未产事件时安全立即生效）。"""
        self._sink = sink

    def detach_sink(self, sink: Emit) -> None:
        """仅当当前 sink 即被摘者才置空（避免误摘新连接刚挂的 sink，RS-2）。"""
        if self._sink is sink:
            self._sink = None

    async def notify(self, event: dict) -> None:
        """直发当前 sink（不计 seq / 不入缓冲）：连接级控制 / 错误帧（如 AGENT_NOT_CONFIGURED）。"""
        await self._send_via_sink(event)

    @staticmethod
    async def _safe_send(sink: Emit, event: dict) -> None:
        try:
            await sink(event)
        except Exception:  # noqa: BLE001 —— sink 失效（连接已死）：吞掉，绝不打断后台 run
            pass

    async def _send_via_sink(self, event: dict) -> None:
        if self._sink is not None:
            await self._safe_send(self._sink, event)

    async def _emit(self, event: dict) -> None:
        """带序号缓冲发送（RS-1）：注入单调 seq → 入缓冲 → 发当前 sink（持锁与 resume 串行）。"""
        async with self._sink_lock:
            self._seq += 1
            event["seq"] = self._seq
            self._buffer.append((self._seq, event))
            await self._send_via_sink(event)

    async def resume_to(self, last_seq: int, sink: Emit) -> bool:
        """重连续流（RS-2/RN-1）：原子重放 seq>last_seq 的缓冲事件并转实时。

        持 ``_sink_lock`` 与 ``_emit`` 互斥，杜绝重放期间 run 事件交错（乱序 / 丢失 / 重复）。

        Args:
            last_seq: 客户端已应用的最大 seq。
            sink: 新连接的发送闭包。

        Returns:
            ``True`` 已无损重放并转实时续流；``False`` 缓冲空洞（待重放事件已被淘汰，需 REST 兜底）。
        """
        async with self._sink_lock:
            ok = not self._has_gap(last_seq)
            if ok:
                for seq, event in list(self._buffer):
                    if seq > last_seq:
                        await self._safe_send(sink, event)
            self._sink = sink  # 重放后再转实时（gap 路径亦设 sink，使后续 done 可达新连接触发 REST）
            return ok

    def _has_gap(self, last_seq: int) -> bool:
        """缓冲是否已淘汰 (last_seq, 最旧] 间的待重放事件（无法无损覆盖 → 需 REST 兜底）。"""
        if not self._buffer:
            return False
        oldest = self._buffer[0][0]
        return last_seq < oldest - 1

    # ============================ 心跳保活（RS-7/RN-2） ============================
    async def _heartbeat_loop(self) -> None:
        """run 期周期心跳：纯活性帧，绕过 _seq/_buffer，直发当前 sink（断连即静默）。"""
        interval = self._settings.agent_heartbeat_seconds
        while True:
            await asyncio.sleep(interval)
            await self._send_via_sink(_event("heartbeat", {}))

    def _cancel_heartbeat(self) -> None:
        task = self._heartbeat_task
        if task is not None and not task.done():
            task.cancel()

    async def _await_heartbeat_stop(self) -> None:
        """run finally：取消并回收心跳任务，杜绝泄漏（RN-7）。"""
        task = self._heartbeat_task
        self._heartbeat_task = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def load_from_db(self) -> None:
        async with self._session_factory() as session:
            rows = await repo.list_agent_messages(
                session, self.conversation_id, limit=self._settings.agent_max_context_messages)
        self.messages = [row_to_llm_message(r) for r in rows]
        self._loaded = True

    async def run(self, user_text: str, ui: dict | None = None, sink: Emit | None = None,
                  *, client: LLMClient | None = None,
                  enabled_tools: set[str] | RunToolContext | None = None) -> None:
        """跑一轮 agentic loop。事件经 ``self._emit`` 发出（run/socket 解耦，RS-1）。

        Args:
            user_text: 用户输入。
            ui: 当前工作台上下文（注入 system prompt / ui-action 默认值）。
            sink: 可选初始 sink（兼容既有单测 / 直驱）；生产路径由 ``manager.attach`` 预挂。
            client: 本轮使用的 LLM 客户端（manager 解析的当前默认服务）。仅在**通过下方权威
                BUSY 守卫之后**才绑定 ``self._llm``——擦过 WS best-effort 检查的并发轮在守卫处即被
                拒、绑定永不发生，故进行中 run 的 client 绝不被中途替换（P1-Race）。
            enabled_tools: 本轮启用工具集（manager 解析）；与 ``client`` 同点位绑定，使赢得 run
                槽的这一轮才采用最新启用集，避免并发轮中途替换。``None`` = 不过滤（兼容直驱 / 单测）。
        """
        if sink is not None:
            self._sink = sink
        # 同步 check-and-set BUSY 守卫（RN-4：检查与置位间无 await，并发任务不交错，BUSY 唯一权威）。
        if self._running:
            await self._send_via_sink(
                _event("error", {"code": "BUSY", "message": "上一条仍在处理，请稍候。"}))
            return
        self._running = True
        if client is not None:  # 过守卫后才绑定：赢得 run 槽的这一轮才换用最新默认服务（P1-Race）。
            self._llm = client
        run_context = enabled_tools if isinstance(enabled_tools, RunToolContext) else None
        self._enabled_tools = run_context.enabled if run_context else enabled_tools
        self._cancel = False
        self._buffer.clear()  # 新 run 起始清空事件窗口（缓冲只承载当前 run，R4）
        ctx = ToolContext(session_factory=self._session_factory, ui=UiContext.from_dict(ui),
                          cancel_check=lambda: self._cancel,
                          call=run_context.call if run_context else None)
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        try:
            await self._run_rounds(user_text, ui, ctx)
        finally:
            self._running = False
            await self._await_heartbeat_stop()

    async def _run_rounds(self, user_text: str, ui: dict | None, ctx: ToolContext) -> None:
        await self._append_and_persist(LlmMessage("user", [TextBlock(user_text)]))
        disabled = self._registry.disabled_names(self._enabled_tools)
        system = build_system_prompt(ui, disabled, ctx.call)
        stats = _RunStats()
        outcome = await self._iterate_tool_rounds(system, ctx, stats)
        if outcome == "limit":
            await self._finalize_on_limit(system, stats)
        self._log_run(stats)
        await self._emit(_event("done", {"cancelled": self._cancel}))

    async def _iterate_tool_rounds(self, system: str, ctx: ToolContext, stats: _RunStats) -> str:
        """跑工具轮直到自然收尾 / 出错 / 取消（返回对应状态），或触顶返回 "limit"。"""
        for _ in range(self._settings.agent_max_tool_rounds):
            if self._cancel:
                return "cancelled"
            assistant_msg, errored = await self._stream_assistant_turn(system, stats)
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
            await self._execute_tool_calls(tool_uses, ctx, stats)
        return "limit"

    async def _finalize_on_limit(self, system: str, stats: _RunStats) -> None:
        """轮数耗尽：禁用工具再请求一次文字总结（H9）；已取消则不再调用 LLM（RN-7）。"""
        if self._cancel:
            return
        assistant_msg, errored = await self._stream_assistant_turn(system, stats, tools=[])
        stats.rounds += 1
        if assistant_msg.content:
            await self._append_and_persist(assistant_msg)
        summarized = any(isinstance(b, TextBlock) and b.text for b in assistant_msg.content)
        if errored or not summarized:
            await self._emit(_event("error", {"code": "TOOL_LOOP_LIMIT",
                                              "message": "工具调用轮数已达上限。"}))

    def _log_run(self, stats: _RunStats) -> None:
        """run 结束结构化日志（轮数 / 每工具耗时 / stop_reason / token，仅元数据，C7）。"""
        logger.info("agent run complete: %s", {
            "conv": self.conversation_id, "rounds": stats.rounds, "tools": stats.tools,
            "stop_reason": stats.stop_reason, "in_tokens": stats.in_tokens,
            "out_tokens": stats.out_tokens})

    async def _stream_assistant_turn(self, system: str, stats: _RunStats,
                                     tools: list | None = None) -> tuple[LlmMessage, bool]:
        """单轮流式累积：emit 增量事件并返回（assistant 消息, 是否出错）。`tools=[]` 即禁用工具。"""
        msg_id = uuid.uuid4().hex
        await self._emit(_event("message_start", {"id": msg_id, "role": "assistant"}))
        acc = _StreamAcc()
        errored = False
        schemas = self._registry.schemas(self._enabled_tools) if tools is None else tools
        async for ev in self._llm.stream(system=system, messages=self._trimmed_messages(),
                                          tools=schemas):
            if self._cancel:
                break
            done = await self._on_stream_event(ev, msg_id, acc)
            if done == "error":
                errored = True
                break
            if done == "stop":
                break
        await self._emit(_event("message_end", self._message_end_data(msg_id, acc, stats)))
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

    async def _on_stream_event(self, ev: StreamEvent, msg_id: str, acc: _StreamAcc) -> str | None:
        """消费单个归一化流事件，返回 None|"stop"|"error"。"""
        if ev.kind == "text_delta":
            acc.text_parts.append(ev.text or "")
            await self._emit(_event("text_delta", {"id": msg_id, "text": ev.text or ""}))
        elif ev.kind == "thinking_delta":
            await self._emit(_event("thinking_delta", {"id": msg_id, "text": ev.text or ""}))
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
            await self._emit(_event("error", {"code": ev.code or "LLM_ERROR",
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
                                  stats: _RunStats) -> None:
        """顺序执行工具，emit 结果 / ui_action，回喂 tool_result（断连即取消亦保持配对）。"""
        result_blocks: list[ToolResultBlock] = []
        for tu in tool_uses:
            if self._cancel:
                break
            await self._emit(_event("tool_call", {"id": tu.id, "name": tu.name, "input": tu.input}))
            started = time.perf_counter()
            call_ctx = replace(ctx, call=replace(ctx.call, tool_use_id=tu.id)) if ctx.call else ctx
            result = await self._registry.execute(tu.name, tu.input, call_ctx,
                                                  enabled=self._enabled_tools)
            stats.tools.append({"name": tu.name, "ok": result.ok,
                                "ms": round((time.perf_counter() - started) * 1000, 1)})
            event = {"id": tu.id, "name": tu.name,
                     "ok": result.ok, "summary": result.summary}
            if result.model_payload is not None:
                event["payload"] = result.payload
            if result.code is not None:
                event["code"] = result.code
            await self._emit(_event("tool_result", event))
            if self._registry.is_ui_action(tu.name) and result.ok and result.payload:
                await self._emit(_event("ui_action", result.payload))
            result_blocks.append(_tool_result_block(tu.id, result))
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
