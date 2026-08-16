"""下单前二次反思校验单测（ai-trade-pre-trade-reflection §6.1）。

覆盖三层：
- `AiTradeDecider.reflect`：桩 LLM 脚本化反思裁决 → confirm/abort/收缩/fail-closed/重试/CancelledError/关闭直通。
- `reflection.apply_reflection` 纯函数：confirm/abort/收缩量化只减不增/fail-closed 全分支。
- 会话集成：反思否决 open → executor 未被调用（无真实下单）+ decision 事件 reflectionVerdict=abort；
  confirm → 开仓仍落地；hold 轮 reflect 从不被调用。

纯离线（无网络、无 DB 除会话集成用临时库），桩 LLM 按 tool 名区分决策流 / 反思流。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.agent.llm.base import StreamEvent
from app.ai_auto.decider import AiTradeDecider, ReflectionVerdict, TradeDecision, _fail_closed
from app.ai_auto.prompts import REFLECTION_TOOL_NAME
from app.ai_auto.reflection import applied_shrink, apply_reflection, build_reflection_note
from app.config import Settings


# ---------- 桩 LLM：按 tool 名区分决策流 / 反思流，脚本化逐次响应 ----------
class _ScriptedLLM:
    """记录调用次数与最近 messages；反思流（tools[0].name==反思工具）弹反思脚本，其余弹决策/摘要脚本。"""

    def __init__(self, reflection_scripts: list[list[StreamEvent]]):
        self._reflection_scripts = list(reflection_scripts)
        self.reflection_calls = 0
        self.last_messages = None

    async def stream(self, *, system, messages, tools):
        self.last_messages = messages
        if tools and tools[0].name == REFLECTION_TOOL_NAME:
            self.reflection_calls += 1
            script = (self._reflection_scripts.pop(0) if self._reflection_scripts
                      else [StreamEvent("message_done", stop_reason="end")])
            for ev in script:
                yield ev
            return
        # 决策/摘要流（本文件只直接测 reflect，不走 decide）——返回空收尾。
        yield StreamEvent("message_done", stop_reason="end")


def _reflection_events(args: dict, tool_id: str = "rf1"):
    return [StreamEvent("tool_use_start", id=tool_id, name=REFLECTION_TOOL_NAME),
            StreamEvent("tool_use_args_delta", id=tool_id,
                        partial_json=json.dumps(args, ensure_ascii=False)),
            StreamEvent("message_done", stop_reason="tool_use")]


def _reflect_settings(**kw):
    """反思单测统一 settings：退避基数/封顶置 0（仅剩抖动），保证快且确定。"""
    base = {"ai_trade_retry_base_ms": 0, "ai_trade_retry_max_ms": 0,
            "ai_trade_reflection_enabled": True}
    base.update(kw)
    return Settings(**base)


def _decider(reflection_scripts, *, settings=None, position_step_pct=0.0):
    return AiTradeDecider(
        _ScriptedLLM(reflection_scripts), "sys", context_refresh_rounds=10,
        settings=settings or _reflect_settings(ai_trade_reflection_max_retries=0),
        position_step_pct=position_step_pct, reflection_enabled=True)


def _payload(round_no: int = 1) -> dict:
    return {"round": round_no, "barTs": 1_700_000_000 + round_no, "lastPx": 30000.0,
            "position": None, "sessionPnlUsd": 0.0, "budgetUsd": 100.0,
            "takeProfitUsd": None, "stopLossUsd": None, "signalBoard": []}


def _open_long(size: float = 0.5) -> TradeDecision:
    return TradeDecision("open_long", size, 0.8, "trend up")


# ============================ reflect(): 裁决映射 + 安全回落 ============================
async def test_confirm_keeps_decision_ran_true():
    d = _decider([_reflection_events({"verdict": "confirm", "reason": "稳健"})])
    verdict = await d.reflect(_payload(), _open_long(0.5))
    assert verdict.verdict == "confirm"
    assert verdict.ran is True
    assert verdict.degrade_reason is None
    assert d._llm.reflection_calls == 1


async def test_abort_verdict_is_terminal_not_fail_closed():
    d = _decider([_reflection_events({"verdict": "abort", "reason": "高周期顶背离，追多风险高"})])
    verdict = await d.reflect(_payload(), _open_long(0.5))
    assert verdict.verdict == "abort"
    assert verdict.degrade_reason is None            # abort 是终态裁决，非 fail-closed
    assert verdict.reason == "高周期顶背离，追多风险高"


async def test_confirm_with_shrink_carries_adjusted_size():
    d = _decider([_reflection_events(
        {"verdict": "confirm", "adjustedSizeFraction": 0.2, "reason": "减仓更稳"})])
    verdict = await d.reflect(_payload(), _open_long(0.5))
    assert verdict.verdict == "confirm"
    assert abs(verdict.adjusted_size_fraction - 0.2) < 1e-9


async def test_no_tool_call_fails_closed():
    d = _decider([[StreamEvent("text_delta", text="只有复核文字，无工具"),
                   StreamEvent("message_done", stop_reason="end")]])
    verdict = await d.reflect(_payload(), _open_long())
    assert verdict.verdict == "abort"
    assert verdict.degrade_reason == "reflection_no_tool"


async def test_invalid_verdict_fails_closed():
    d = _decider([_reflection_events({"verdict": "maybe", "reason": "含糊"})])
    verdict = await d.reflect(_payload(), _open_long())
    assert verdict.degrade_reason == "reflection_invalid"


async def test_invalid_json_fails_closed():
    events = [StreamEvent("tool_use_start", id="rf1", name=REFLECTION_TOOL_NAME),
              StreamEvent("tool_use_args_delta", id="rf1", partial_json="{not valid"),
              StreamEvent("message_done", stop_reason="tool_use")]
    d = _decider([events])
    verdict = await d.reflect(_payload(), _open_long())
    assert verdict.degrade_reason == "reflection_no_tool"   # 解析失败 → 无有效工具 → no_tool


async def test_error_event_fails_closed_llm_error():
    d = _decider([[StreamEvent("error", code="LLM_ERROR", message="boom")]])
    verdict = await d.reflect(_payload(), _open_long())
    assert verdict.degrade_reason == "reflection_llm_error"


async def test_timeout_fails_closed():
    async def _slow(*, system, messages, tools):
        await asyncio.sleep(1.0)
        yield StreamEvent("message_done", stop_reason="end")

    d = _decider([], settings=_reflect_settings(ai_trade_reflection_timeout_s=0.01,
                                                ai_trade_reflection_max_retries=0))
    d._llm.stream = _slow   # type: ignore[attr-defined]
    verdict = await d.reflect(_payload(), _open_long())
    assert verdict.degrade_reason == "reflection_timeout"


# ============================ reflect(): 重试 + 终态 + 取消 + 关闭 ============================
async def test_fail_closed_retries_then_succeeds():
    """瞬时 llm_error → error → confirm：重试后成功，恰好 3 次反思调用（1 首次 + 2 重试）。"""
    d = _decider([[StreamEvent("error", code="LLM_ERROR", message="boom")],
                  [StreamEvent("error", code="LLM_ERROR", message="boom")],
                  _reflection_events({"verdict": "confirm", "reason": "ok"})],
                 settings=_reflect_settings(ai_trade_reflection_max_retries=3))
    verdict = await d.reflect(_payload(), _open_long())
    assert verdict.verdict == "confirm"
    assert verdict.degrade_reason is None
    assert d._llm.reflection_calls == 3


async def test_fail_closed_degrades_after_exhausting_retries():
    d = _decider([[StreamEvent("error", code="LLM_ERROR", message="boom")] for _ in range(4)],
                 settings=_reflect_settings(ai_trade_reflection_max_retries=3))
    verdict = await d.reflect(_payload(), _open_long())
    assert verdict.degrade_reason == "reflection_llm_error"
    assert d._llm.reflection_calls == 4


async def test_abort_does_not_retry():
    """abort 是模型真实终态裁决 → 零重试，只调用一次（即便 max_retries 很大）。"""
    d = _decider([_reflection_events({"verdict": "abort", "reason": "no"})],
                 settings=_reflect_settings(ai_trade_reflection_max_retries=3))
    verdict = await d.reflect(_payload(), _open_long())
    assert verdict.verdict == "abort"
    assert d._llm.reflection_calls == 1


async def test_cancelled_error_propagates_without_degrade():
    async def _stub(*, system, messages, tools):
        raise asyncio.CancelledError
        yield   # pragma: no cover —— 使 _stub 成为 async generator

    d = _decider([], settings=_reflect_settings(ai_trade_reflection_max_retries=3))
    d._llm.stream = _stub   # type: ignore[attr-defined]
    with pytest.raises(asyncio.CancelledError):
        await d.reflect(_payload(), _open_long())


async def test_reflection_disabled_is_passthrough_no_llm_call():
    """零回归门禁：reflection_enabled=False（直接构造默认）→ reflect 直通、无 LLM 调用、ran=False。"""
    d = AiTradeDecider(_ScriptedLLM([]), "sys", context_refresh_rounds=10,
                       settings=Settings())          # reflection_enabled 缺省 → False
    verdict = await d.reflect(_payload(), _open_long())
    assert verdict.ran is False
    assert verdict.verdict == "confirm"
    assert d._llm.reflection_calls == 0


# ============================ apply_reflection 纯函数：全分支 ============================
def test_apply_reflection_passthrough_when_not_ran():
    decision = _open_long(0.5)
    v = ReflectionVerdict("confirm", None, None, "", None, ran=False)
    out, degrade = apply_reflection(decision, None, v)
    assert out is decision and degrade is None


def test_apply_reflection_none_verdict_passthrough():
    decision = _open_long(0.5)
    out, degrade = apply_reflection(decision, "prev", None)
    assert out is decision and degrade == "prev"


def test_apply_reflection_abort_downgrades_to_hold_with_reason():
    decision = _open_long(0.5)
    v = ReflectionVerdict("abort", None, None, "顶背离追多风险高", None, ran=True)
    out, degrade = apply_reflection(decision, None, v)
    assert out.action == "hold"
    assert out.reason == "顶背离追多风险高"       # §5.3：决策理由改写为反思理由
    assert degrade == "reflection_abort"


def test_apply_reflection_fail_closed_downgrades_and_keeps_code():
    decision = _open_long(0.5)
    v = _fail_closed("reflection_timeout")
    out, degrade = apply_reflection(decision, None, v)
    assert out.action == "hold"
    assert degrade == "reflection_timeout"


def test_apply_reflection_preserves_prior_degrade():
    decision = _open_long(0.5)
    v = ReflectionVerdict("abort", None, None, "no", None, ran=True)
    out, degrade = apply_reflection(decision, "decision_timeout", v)
    assert degrade == "decision_timeout"          # 保留决策器先前回落原因


def test_apply_reflection_confirm_shrinks_size():
    decision = _open_long(0.5)
    v = ReflectionVerdict("confirm", 0.2, None, "减仓", None, ran=True)
    out, _ = apply_reflection(decision, None, v)   # step=0 连续
    assert out.action == "open_long"
    assert abs(out.size_fraction - 0.2) < 1e-9


def test_apply_reflection_confirm_never_enlarges():
    """收缩值 > 原仓位 → 忽略（只减不增，绝不放大）。"""
    decision = _open_long(0.3)
    v = ReflectionVerdict("confirm", 0.9, None, "想加仓", None, ran=True)
    out, _ = apply_reflection(decision, None, v)
    assert abs(out.size_fraction - 0.3) < 1e-9      # 原样，未放大


def test_apply_reflection_confirm_equal_size_ignored():
    decision = _open_long(0.5)
    v = ReflectionVerdict("confirm", 0.5, None, "同值", None, ran=True)
    out, _ = apply_reflection(decision, None, v)
    assert abs(out.size_fraction - 0.5) < 1e-9      # 等值不算收缩，原样


def test_apply_reflection_shrink_quantized_then_min_only_decreases():
    """RV-4：step=0.25、原 0.5、调整 0.4 → quantize(0.4)=0.5(就近取整抬回)，再 min(0.5,0.5)=0.5，只减不增。"""
    decision = _open_long(0.5)
    v = ReflectionVerdict("confirm", 0.4, None, "略减", None, ran=True)
    out, _ = apply_reflection(decision, None, v, position_step=0.25)
    assert abs(out.size_fraction - 0.5) < 1e-9      # 量化就近抬回后被 min 收口回原档位（绝不 >原值）


def test_apply_reflection_shrink_quantized_real_decrease():
    """step=0.25、原 0.75、调整 0.3 → quantize(0.3)=0.25，min(0.75,0.25)=0.25，真实收缩到合法档位。"""
    decision = _open_long(0.75)
    v = ReflectionVerdict("confirm", 0.3, None, "减到低档", None, ran=True)
    out, _ = apply_reflection(decision, None, v, position_step=0.25)
    assert abs(out.size_fraction - 0.25) < 1e-9


def test_apply_reflection_confirm_no_adjust_is_noop():
    decision = _open_long(0.5)
    v = ReflectionVerdict("confirm", None, None, "确认原样", None, ran=True)
    out, degrade = apply_reflection(decision, None, v)
    assert out is decision and degrade is None


def test_applied_shrink_only_when_strictly_smaller():
    orig = _open_long(0.5)
    assert applied_shrink(orig, _open_long(0.2)) == 0.2
    assert applied_shrink(orig, _open_long(0.5)) is None          # 等值非收缩
    assert applied_shrink(orig, TradeDecision("hold", None, None, "x")) is None


# ============================ F-1 复现（ai-trade-limit-orders-fix §2.2，先红后绿）============================
def test_reflection_shrink_preserves_limit_intent():
    """收缩仓位时 `ord_type`/`limit_px` 必须原样保留——位置重建丢字段是「限价静默变市价」的 P0 根因。"""
    decision = TradeDecision("open_long", 0.5, 0.9, "r", "limit", 100.0)
    v = ReflectionVerdict("confirm", 0.25, None, "减仓更稳", None, ran=True)
    out, degrade = apply_reflection(decision, None, v)
    assert out.ord_type == "limit"
    assert out.limit_px == 100.0
    assert abs(out.size_fraction - 0.25) < 1e-9
    assert out.action == "open_long" and degrade is None


def test_reflection_shrink_market_decision_behavior_unchanged():
    """零回归钉：市价决策的收缩行为逐字不变（默认 ord_type=market / limit_px=None 原样带过）。"""
    decision = _open_long(0.5)
    v = ReflectionVerdict("confirm", 0.2, None, "减仓", None, ran=True)
    out, degrade = apply_reflection(decision, None, v)
    assert out.action == "open_long"
    assert abs(out.size_fraction - 0.2) < 1e-9
    assert out.ord_type == "market" and out.limit_px is None
    assert out.confidence == decision.confidence and out.reason == decision.reason
    assert degrade is None


# ============================ F2 build_reflection_note（hold-inertia-fix §2.2）============================
def test_note_none_when_verdict_missing_or_not_ran():
    """直通分支：verdict=None（反思关闭/hold 轮）或 ran=False → None（journal 该键为 None）。"""
    decision = _open_long(0.5)
    assert build_reflection_note(decision, None, decision) is None
    not_ran = ReflectionVerdict("confirm", None, None, "", None, ran=False)
    assert build_reflection_note(decision, not_ran, decision) is None


def test_note_records_vetoed_open_intent():
    """否决轮：vetoed=True 且原提议（方向/仓位/置信度）与最终 hold 双双保真——「曾想开仓但被否决」
    不再从记忆里蒸发（根因 ② 复现钉）。"""
    original = _open_long(0.5)
    verdict = ReflectionVerdict("abort", None, None, "顶背离追多风险高", None, ran=True)
    final, _ = apply_reflection(original, None, verdict)
    note = build_reflection_note(original, verdict, final)
    assert note is not None
    assert note["vetoed"] is True
    assert note["failClosed"] is False               # 模型真实否决 ≠ 基础设施故障
    assert note["verdict"] == "abort"
    assert note["reason"] == "顶背离追多风险高"
    assert note["proposedAction"] == "open_long"
    assert abs(note["proposedSizeFraction"] - 0.5) < 1e-9
    assert abs(note["proposedConfidence"] - 0.8) < 1e-9
    assert note["finalAction"] == "hold"
    assert note["finalSizeFraction"] is None


def test_note_confirm_with_shrink_not_vetoed():
    """收缩确认轮：动作未变 → vetoed=False，final 仓位记收缩后档位（proposed 保留原值）。"""
    original = _open_long(0.5)
    verdict = ReflectionVerdict("confirm", 0.2, None, "减仓更稳", None, ran=True)
    final, _ = apply_reflection(original, None, verdict)
    note = build_reflection_note(original, verdict, final)
    assert note["vetoed"] is False
    assert note["verdict"] == "confirm"
    assert abs(note["proposedSizeFraction"] - 0.5) < 1e-9
    assert abs(note["finalSizeFraction"] - 0.2) < 1e-9


def test_note_fail_closed_flagged_distinct_from_model_abort():
    """fail-closed 轮：vetoed=True 但 failClosed=True——摘要器须把系统故障保守跳过与模型真实否决
    区分开（F3 head 指令依赖此位）。"""
    original = _open_long(0.5)
    verdict = _fail_closed("reflection_timeout")
    final, _ = apply_reflection(original, None, verdict)
    note = build_reflection_note(original, verdict, final)
    assert note["vetoed"] is True
    assert note["failClosed"] is True
    assert note["finalAction"] == "hold"
