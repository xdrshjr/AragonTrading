"""AI 决策器：多策略信号板 → 单一受约束交易决策 + 轮次滚动摘要式上下文压缩。

安全姿态（ai-auto-trading §2.3 / P1-5）：
- **唯一放行工具**：只把 `ToolSchema(submit_trade_decision)` 传给 `llm.stream(tools=[...])`，模型物理上
  没有别的工具可调（比 registry 围栏更强的边界）。
- **LLM 层无工具强制**：`stream` 无 `tool_choice`，「本轮必须且只能调用一次」只能由 prompt 引导；
  正确性由**安全回落**兜底——LLM 超时 / 只回文本无工具 / 产生多次（取首条有效）/ 入参非法 /
  `open_*` 缺 `sizeFraction` → 一律 `hold`，绝不在不确定时下单。
- **流事件契约（P1-5 校正）**：真实事件是 `tool_use_start`（name）→ 若干 `tool_use_args_delta`
  （partial_json 片段）→ `message_done`；决策器**自行**累积 partial_json 再 `json.loads`（参照
  `agent/loop._ToolAcc`，但独立实现、不复用交互式会话）。

上下文管理（§2.4 / P1-3）：每轮向 `_context` 追加 user+assistant(tool_use)+tool_result；每
`context_refresh_rounds` 轮以**完整 `_journal`（非可能被裁剪的 `_context`）**为素材 LLM 摘要 → 重置
`_context` 为「摘要种子」、清空 `_journal`、滚动累积上一段摘要。两次摘要间 `_context` 超硬上限则左裁。
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import uuid
from dataclasses import dataclass

from ..agent.llm.base import (
    LLMClient,
    LlmMessage,
    TextBlock,
    ToolResultBlock,
    ToolSchema,
    ToolUseBlock,
)
from ..agent.llm.retry import backoff_delay_ms
from ..config import Settings
from .prompts import (
    DECISION_TOOL_DESCRIPTION,
    DECISION_TOOL_NAME,
    build_decision_prompt,
    build_decision_tool_schema,
    build_summary_prompt,
    build_summary_system_prompt,
)
from .trace import TraceEmit, _TraceCoalescer

logger = logging.getLogger(__name__)

_ACTIONS = ("open_long", "open_short", "close", "hold")

# 本轮研判思维链全文的硬上限（ai-trade-reasoning-chain §2.5）：`_last_analysis` 只经 agent_trace 流与
# _traces(≤20) 承载给 UI，超出即截断；进 journal / decision 事件的是调用方派生的有界摘录（≤400，RC-2/3）。
_ANALYSIS_MAX_CHARS = 4000

# 「可重试」degrade 原因白名单（瞬时 LLM 失败 / 未产出有效决策）；其余（low_confidence 等）为终态决策，
# 绝不重试（ai-trade-decision-llm-retry §4.1）。
_RETRYABLE_DEGRADE_REASONS = frozenset(
    {"decision_timeout", "llm_error", "no_tool_call", "invalid_action", "missing_size"})
# 每次重试前退避的抖动上限（ms），镜像 agent/llm/retry.py 的 _JITTER_MS 语义（§4.3）。
_RETRY_JITTER_MS = 250


def _is_retryable_degrade(reason: str | None) -> bool:
    """该 degrade 原因是否属于瞬时/无有效产出（可重试）；None 与 low_confidence 返回 False。"""
    return reason in _RETRYABLE_DEGRADE_REASONS


def _retry_hint_suffix(reason: str) -> str:
    """重试时追加进瞬时 user_msg 的一次性纠偏提示（不落 _context/_journal，§4.5）。"""
    return (f"\n\n【系统提示】上一次未产出有效决策（{reason}）。"
            f"请务必**只调用一次** submit_trade_decision 并给出完整合法入参。")


@dataclass
class TradeDecision:
    """AI 本轮决策（受约束）：action + 可选仓位占比 / 置信度 + 理由。"""

    action: str                    # open_long | open_short | close | hold
    size_fraction: float | None    # 预算名义占比（open_* 用；close/hold 为 None）
    confidence: float | None
    reason: str


def _hold(reason: str) -> TradeDecision:
    return TradeDecision(action="hold", size_fraction=None, confidence=None, reason=reason)


def _clamp01(value) -> float | None:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(n, 1.0))


class AiTradeDecider:
    """持 LLMClient + 滚动上下文的决策器；每轮 `decide` 出决策，`commit_round` 记录并按需摘要。"""

    def __init__(self, llm: LLMClient, system_prompt: str, *,
                 context_refresh_rounds: int, settings: Settings,
                 position_step_pct: float = 0.0):
        self._llm = llm
        self.system_prompt = system_prompt
        self.context_refresh_rounds = max(1, int(context_refresh_rounds))
        self.settings = settings
        # 仓位操作档位步长（fraction；0=连续，保证所有既有直接构造 AiTradeDecider(...) 的位置零回归）。
        self._position_step = max(0.0, float(position_step_pct))
        self._tool = ToolSchema(name=DECISION_TOOL_NAME, description=DECISION_TOOL_DESCRIPTION,
                                input_schema=build_decision_tool_schema(self._position_step))
        self._context: list[LlmMessage] = []
        self._summary: str = ""
        self._journal: list[dict] = []
        self._rounds_since_summary = 0
        self._last_round = 0
        self._last_analysis: str = ""      # 本轮研判思维链全文（R3；每次尝试入口清零，RC-1，§4.2/R-6）

    # ---------- 只读状态（供 status / snapshot）----------
    @property
    def summary(self) -> str | None:
        return self._summary or None

    @property
    def rounds_since_summary(self) -> int:
        return self._rounds_since_summary

    @property
    def last_analysis(self) -> str:
        """本轮研判思维链全文（供 session 派生有界摘录入 journal/decision 事件）；降级轮为空串。"""
        return self._last_analysis

    # ---------- 决策 ----------
    async def decide(self, payload: dict, *, emit: TraceEmit | None = None
                     ) -> tuple[TradeDecision, str | None]:
        """出本轮决策；可重试失败先带退避重试，穷尽后才降级 hold。签名不变（RC/RV 契约，§4.2）。

        返回 `(decision, degrade_reason)`——degrade_reason 非 None 表示安全回落 hold。可选 `emit`
        （由 session 注入）承接决策器流式认知（分析 / 思考 / 工具起始 / 重试面包屑）经合并后扇出；
        默认 `None` 保证回测 / 单测等既有位置调用零改动、零回归（合并器全程空转）。

        瞬时失败（超时 / LLM 错误 / 未产出有效决策）先带有界指数退避重试至多
        `ai_trade_decision_max_retries` 次，全部失败才降级；模型真实终态决策（合法动作 / 刻意 hold /
        low_confidence 门禁）零重试、行为逐字不变；`asyncio.CancelledError` 立即向上抛（绝不吞、绝不降级）。
        `max_retries=0` 逐字回退旧行为「首次失败即降级」。
        """
        max_retries = max(0, int(self.settings.ai_trade_decision_max_retries))
        decision, degrade = await self._attempt_decision(payload, emit)   # 首次：hint=None
        attempt = 0
        while (degrade is not None and _is_retryable_degrade(degrade)
               and attempt < max_retries):
            attempt += 1
            await self._emit_retry(emit, attempt, max_retries, degrade)   # 面包屑 + 前端本轮累积重置
            await self._retry_backoff(attempt)                            # 可被 cancel 中断
            decision, degrade = await self._attempt_decision(payload, emit, hint=degrade)
        return decision, degrade

    async def _attempt_decision(self, payload: dict, emit: TraceEmit | None = None,
                                *, hint: str | None = None
                                ) -> tuple[TradeDecision, str | None]:
        """单次决策尝试（= 今日 decide 主体）：入口清零 last_analysis → 流式研判 → 映射。

        `hint`（keyword-only，默认 None → 与今日首次尝试逐字一致）：上一次失败的 degrade 原因，
        仅追加进**当次瞬时** user_msg（§4.5），绝不落 `_context/_journal`——上下文零污染。
        """
        self._last_analysis = ""     # RC-1（跨尝试版）：每次尝试入口清零，重试不残留上一次尝试的研判
        prompt_text = build_decision_prompt(payload)
        if hint:
            prompt_text += _retry_hint_suffix(hint)   # §4.5：仅当次重试的一次性纠偏提示（首次不触发）
        user_msg = LlmMessage("user", [TextBlock(prompt_text)])
        try:
            raw = await asyncio.wait_for(
                self._stream_decision(user_msg, emit),
                timeout=self.settings.ai_trade_decision_timeout_s)
        except asyncio.TimeoutError:
            return _hold("decision_timeout"), "decision_timeout"
        except asyncio.CancelledError:
            raise                    # 会话停止：绝不吞、绝不降级，向上抛（BaseException，不入下方 except）
        except Exception as exc:  # noqa: BLE001 —— 任何 LLM/网络失败回落 hold（绝不在不确定时下单）
            logger.warning("ai-trade decide attempt failed: %s", exc)
            return _hold("llm_error"), "llm_error"
        return self._map_decision(raw)

    async def _retry_backoff(self, attempt: int) -> None:
        """第 `attempt` 次（1-based）重试前的有界指数退避 + 抖动；可被 cancel / 会话停止中断（§4.3）。"""
        base = backoff_delay_ms(attempt - 1, self.settings.ai_trade_retry_base_ms,
                                self.settings.ai_trade_retry_max_ms)
        await asyncio.sleep((base + random.uniform(0, _RETRY_JITTER_MS)) / 1000.0)

    async def _emit_retry(self, emit: TraceEmit | None, attempt: int, max_retries: int,
                          reason: str) -> None:
        """重试前发一条 retry 轨迹（emit=None 空转，回测/单测零副作用，§4.4）。"""
        if emit is None:
            return
        await emit({"phase": "retry", "attempt": attempt,
                    "maxAttempts": max_retries, "reason": reason})

    async def _stream_decision(self, user_msg: LlmMessage,
                               emit: TraceEmit | None = None) -> dict | None:
        """流式累积首个 `submit_trade_decision` 的 partial_json 并解析（P1-5）；无工具调用→None。

        旁路：text_delta（分析）/ thinking_delta（深度思考）经 `_TraceCoalescer` 合并后 `emit`；
        `emit is None` 时合并器空转，取工具入参逻辑与今日逐字不变（安全契约不受影响）。
        """
        accs: dict[str, dict] = {}
        order: list[str] = []
        analysis_parts: list[str] = []           # R3：累积研判全文（与 emit 解耦，回测 emit=None 也留痕）
        pump = _TraceCoalescer(emit)             # emit=None → 全程空转（回测 / 单测无订阅）
        async for ev in self._llm.stream(system=self.system_prompt,
                                          messages=self._context + [user_msg],
                                          tools=[self._tool]):
            if ev.kind == "text_delta":
                analysis_parts.append(ev.text or "")
                await pump.feed("analysis", ev.text or "")
            elif ev.kind == "thinking_delta":
                await pump.feed("thinking", ev.text or "")
            elif ev.kind == "tool_use_start":
                await pump.flush()               # 文本段结束，先冲刷再发工具起始占位
                accs[ev.id] = {"name": ev.name or "", "parts": []}
                order.append(ev.id)
                await pump.mark(ev.name or "")
            elif ev.kind == "tool_use_args_delta":
                if ev.id in accs:
                    accs[ev.id]["parts"].append(ev.partial_json or "")
            elif ev.kind == "error":
                await pump.flush()
                raise RuntimeError(ev.message or ev.code or "LLM_ERROR")
            elif ev.kind == "message_done":
                await pump.flush()
                break
        # R3：流正常结束才落全文（超时/取消/error 提前退出则不赋值，_last_analysis 维持入口清零的空串）。
        self._last_analysis = "".join(analysis_parts).strip()[:_ANALYSIS_MAX_CHARS]
        for tid in order:                       # 取首条有效 submit_trade_decision（多调用取其一）
            if accs[tid]["name"] == DECISION_TOOL_NAME:
                raw = "".join(accs[tid]["parts"]).strip() or "{}"
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    return None                 # 入参非法 → hold
        return None                             # 未产生工具调用 → hold

    def _map_decision(self, raw: dict | None) -> tuple[TradeDecision, str | None]:
        """把工具入参映射为受约束 `TradeDecision`；任一不合法一律回落 hold（安全关键）。"""
        if not isinstance(raw, dict):
            return _hold("no_tool_call"), "no_tool_call"
        action = raw.get("action")
        reason = str(raw.get("reason") or "")
        if action not in _ACTIONS:
            return _hold("invalid_action"), "invalid_action"
        confidence = _clamp01(raw.get("confidence"))
        if action in ("open_long", "open_short"):
            size = _clamp01(raw.get("sizeFraction"))
            if size is None or size <= 0:
                return _hold("missing_size"), "missing_size"
            if confidence is not None and confidence < self.settings.ai_trade_min_confidence:
                return _hold("low_confidence"), "low_confidence"
            # [R-5/R-9] 唯一硬约束点：过完 low_confidence 门禁后把开仓规模吸附到用户所选档位（实盘/回测同生效）。
            # 延迟导入打破 signals→decider 模块级循环；quantize 的 max(1,…) 保证 >0 输入不被吸附回 0（不新触发
            # missing_size）；step=0 时逐字等价旧行为（连续直通）。
            from .signals import quantize_size_fraction
            size = quantize_size_fraction(size, self._position_step)
            return TradeDecision(action, size, confidence, reason), None
        return TradeDecision(action, None, confidence, reason), None

    # ---------- 轮次记录 + 滚动摘要 ----------
    async def commit_round(self, payload: dict, decision: TradeDecision,
                           executed: dict, *, analysis: str | None = None) -> str | None:
        """记录本轮（追加 _context + _journal），到期触发摘要重置；返回新摘要文本或 None。

        `analysis`（keyword-only，默认 None → 回测/单测既有调用零回归）：本轮研判的**有界摘录**
        （由调用方从 `last_analysis` 截到 ≤`_ANALYSIS_EXCERPT_CHARS`，RC-2/RC-3），入 journal 供滚动摘要
        沉淀「哪些信号被采纳/推翻」；完整思维链只走 agent_trace 流与 `_traces`，绝不入 journal（防膨胀）。
        """
        self._last_round = int(payload.get("round") or self._last_round + 1)
        tool_id = uuid.uuid4().hex
        decision_input = {"action": decision.action, "sizeFraction": decision.size_fraction,
                          "confidence": decision.confidence, "reason": decision.reason}
        self._context.append(LlmMessage("user", [TextBlock(build_decision_prompt(payload))]))
        self._context.append(LlmMessage(
            "assistant", [ToolUseBlock(id=tool_id, name=DECISION_TOOL_NAME, input=decision_input)]))
        self._context.append(LlmMessage(
            "tool", [ToolResultBlock(tool_use_id=tool_id,
                                     content=json.dumps(executed, ensure_ascii=False))]))
        self._journal.append({
            "round": self._last_round, "signalBoard": payload.get("signalBoard"),
            "decision": decision_input, "executed": executed,
            "positionAfter": executed.get("positionAfter"),
            "sessionPnl": payload.get("sessionPnlUsd"), "analysis": analysis})
        self._rounds_since_summary += 1
        self._trim_context()
        if self._rounds_since_summary >= self.context_refresh_rounds:
            return await self.summarize()
        return None

    async def summarize(self) -> str | None:
        """以完整 `_journal` 为素材（P1-3）摘要执行历程 → 重置上下文；失败保留上一段摘要不崩。"""
        prev = self._summary or None
        journal = list(self._journal)
        try:
            text = await asyncio.wait_for(
                self._stream_summary(prev, journal),
                timeout=self.settings.ai_trade_decision_timeout_s)
        except Exception as exc:  # noqa: BLE001 —— 摘要失败不崩会话：保留上一段摘要，仅重置计数避免每轮重试
            logger.warning("ai-trade summarize failed: %s", exc)
            self._rounds_since_summary = 0
            return None
        if not text:
            self._rounds_since_summary = 0
            return None
        self._summary = text
        seed = f"【历史执行摘要（截至第 {self._last_round} 轮）】\n{text}"
        self._context = [
            LlmMessage("user", [TextBlock(seed)]),
            LlmMessage("assistant", [TextBlock("已了解此前执行情况，将据此继续决策。")])]
        self._rounds_since_summary = 0
        self._journal = []
        return text

    async def _stream_summary(self, prev: str | None, journal: list[dict]) -> str:
        parts: list[str] = []
        async for ev in self._llm.stream(
                system=build_summary_system_prompt(),
                messages=[LlmMessage("user", [TextBlock(build_summary_prompt(prev, journal))])],
                tools=[]):
            if ev.kind == "text_delta":
                parts.append(ev.text or "")
            elif ev.kind == "error":
                raise RuntimeError(ev.message or ev.code or "LLM_ERROR")
            elif ev.kind == "message_done":
                break
        return "".join(parts).strip()

    def _trim_context(self) -> None:
        """两次摘要间硬上限兜底：裁到近 N 条且首条为 user 轮（避免孤儿 tool_result，参照 _trimmed_messages）。"""
        cap = self.settings.ai_trade_max_context_messages
        if len(self._context) <= cap:
            return
        msgs = self._context[-cap:]
        while msgs and msgs[0].role != "user":
            msgs = msgs[1:]
        self._context = msgs
