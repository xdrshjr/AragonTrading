"""AiTradeDecider 单测（ai-auto-trading §2.3 / §2.4 / P1-3 / P1-5）。

桩 LLMClient 产出 `tool_use_start/args_delta/message_done` 流：决策器自拼 partial_json → 决策；
LLM 超时 / 无工具 / 多工具（取首条）/ 非法入参 / open_* 缺 sizeFraction → 一律 hold（安全回落）；
sizeFraction 越界夹紧；summarize 到第 N 轮触发、以 `_journal`（非 `_context`）为素材、上下文被摘要
种子重置、`_journal` 清空、滚动累积。纯离线（无网络、无 DB）。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.agent.llm.base import StreamEvent
from app.ai_auto.decider import AiTradeDecider, _retry_hint_suffix
from app.ai_auto.prompts import build_decision_prompt
from app.config import Settings


class _ScriptedLLM:
    """按预置脚本逐次响应 stream；记录最近一次 messages（供摘要素材断言）与总调用次数。"""

    def __init__(self, scripts: list[list[StreamEvent]]):
        self._scripts = list(scripts)
        self.last_messages = None
        self.calls = 0                 # 累计 stream() 调用次数 = LLM 调用次数（供重试次数断言）

    async def stream(self, *, system, messages, tools):
        self.calls += 1
        self.last_messages = messages
        script = self._scripts.pop(0) if self._scripts else [
            StreamEvent("message_done", stop_reason="end")]
        for ev in script:
            yield ev


def _tool_events(args: dict, tool_id: str = "t1"):
    return [StreamEvent("tool_use_start", id=tool_id, name="submit_trade_decision"),
            StreamEvent("tool_use_args_delta", id=tool_id,
                        partial_json=json.dumps(args, ensure_ascii=False)),
            StreamEvent("message_done", stop_reason="tool_use")]


def _decider(scripts, *, rounds=10, settings=None):
    # 既有（前重试特性）单测默认走「零重试」路径：max_retries=0 即旧行为「首次失败即降级」——使这些用例
    # 成为『零回归门禁』（AI_TRADE_DECISION_MAX_RETRIES=0 时逐字通过，§八）。重试特性用例显式传入自己的
    # settings（_retry_settings）开启重试。
    return AiTradeDecider(_ScriptedLLM(scripts), "sys", context_refresh_rounds=rounds,
                          settings=settings or Settings(ai_trade_decision_max_retries=0))


def _payload(round_no: int) -> dict:
    return {"round": round_no, "barTs": 1_700_000_000 + round_no, "lastPx": 30000.0,
            "position": None, "sessionPnlUsd": 0.0, "budgetUsd": 100.0,
            "takeProfitUsd": None, "stopLossUsd": None,
            "signalBoard": [{"strategyId": 1, "name": "S", "stance": "flat",
                             "latestSignal": "open_long", "strength": 1.0, "reason": "r"}]}


def test_decision_prompt_includes_market_context_and_consensus():
    """E1/E2 契约：payload 含 marketContext/consensus 时，prompt 输出对应两段真实枚举文本。"""
    payload = _payload(1)
    payload["marketContext"] = {"primary": "trend_up", "volatility": "high_volatility",
                                "event": "breakout", "confidence": 0.62, "labels": []}
    payload["consensus"] = {"longVotes": 3, "shortVotes": 1, "flatVotes": 1, "total": 5,
                            "netStance": "long", "agreement": 0.6}
    text = build_decision_prompt(payload)
    assert "市场体制" in text and "trend_up" in text and "high_volatility" in text
    assert "策略共识" in text and "3多" in text and "净立场=long" in text


def test_decision_prompt_omits_segments_when_absent():
    """None/缺省时不输出残段（保持 prompt 纯净，不出现半截空信息）。"""
    text = build_decision_prompt(_payload(1))       # 无 marketContext/consensus 键
    assert "市场体制" not in text
    assert "策略共识" not in text


async def test_valid_open_long_parsed_and_mapped():
    d = _decider([_tool_events({"action": "open_long", "sizeFraction": 0.5,
                                "confidence": 0.8, "reason": "trend"})])
    decision, degrade = await d.decide(_payload(1))
    assert degrade is None
    assert decision.action == "open_long"
    assert abs(decision.size_fraction - 0.5) < 1e-9
    assert abs(decision.confidence - 0.8) < 1e-9


async def test_no_tool_call_falls_back_to_hold():
    d = _decider([[StreamEvent("text_delta", text="我觉得应该观望"),
                   StreamEvent("message_done", stop_reason="end")]])
    decision, degrade = await d.decide(_payload(1))
    assert decision.action == "hold"
    assert degrade == "no_tool_call"


async def test_open_without_size_falls_back_to_hold():
    d = _decider([_tool_events({"action": "open_long", "reason": "no size"})])
    decision, degrade = await d.decide(_payload(1))
    assert decision.action == "hold"
    assert degrade == "missing_size"


async def test_invalid_json_falls_back_to_hold():
    events = [StreamEvent("tool_use_start", id="t1", name="submit_trade_decision"),
              StreamEvent("tool_use_args_delta", id="t1", partial_json="{not valid json"),
              StreamEvent("message_done", stop_reason="tool_use")]
    d = _decider([events])
    decision, degrade = await d.decide(_payload(1))
    assert decision.action == "hold"


async def test_multiple_tool_calls_takes_first_valid():
    events = (_tool_events({"action": "open_long", "sizeFraction": 0.3, "reason": "first"}, "a")
              + _tool_events({"action": "open_short", "sizeFraction": 0.9, "reason": "second"}, "b"))
    d = _decider([events])
    decision, _ = await d.decide(_payload(1))
    assert decision.action == "open_long"    # 取首条有效


async def test_error_event_falls_back_to_hold():
    d = _decider([[StreamEvent("error", code="LLM_ERROR", message="boom")]])
    decision, degrade = await d.decide(_payload(1))
    assert decision.action == "hold"
    assert degrade == "llm_error"


async def test_timeout_falls_back_to_hold():
    async def _slow_stream(*, system, messages, tools):
        await asyncio.sleep(1.0)
        yield StreamEvent("message_done", stop_reason="end")

    d = _decider([], settings=Settings(ai_trade_decision_timeout_s=0.01,
                                       ai_trade_decision_max_retries=0))
    d._llm.stream = _slow_stream   # type: ignore[attr-defined]
    decision, degrade = await d.decide(_payload(1))
    assert decision.action == "hold"
    assert degrade == "decision_timeout"


async def test_size_fraction_clamped_to_unit_interval():
    d = _decider([_tool_events({"action": "open_long", "sizeFraction": 4.0, "reason": "big"})])
    decision, _ = await d.decide(_payload(1))
    assert decision.size_fraction == 1.0


async def test_last_analysis_captured_from_text_deltas():
    """R3：流式 text_delta 累积为 last_analysis（与 emit 解耦——decide 默认 emit=None 也留痕）。"""
    events = ([StreamEvent("text_delta", text="第一步：价格走势向上。"),
               StreamEvent("text_delta", text="结论：开多。")]
              + _tool_events({"action": "open_long", "sizeFraction": 0.3, "reason": "trend"}))
    d = _decider([events])
    await d.decide(_payload(1))
    assert "价格走势向上" in d.last_analysis
    assert "开多" in d.last_analysis


async def test_last_analysis_cleared_on_error_round_no_cross_round_bleed():
    """RC-1 串轮防护：上一轮有研判文本，本轮 _stream_decision 抛错回落 hold → last_analysis 不残留上一轮。"""
    first = ([StreamEvent("text_delta", text="上一轮研判：偏多。")]
             + _tool_events({"action": "hold", "reason": "r"}))
    second = [StreamEvent("text_delta", text="本轮部分研判文字"),
              StreamEvent("error", code="LLM_ERROR", message="boom")]
    d = _decider([first, second])
    await d.decide(_payload(1))
    assert "偏多" in d.last_analysis
    _, degrade = await d.decide(_payload(2))
    assert degrade == "llm_error"
    assert d.last_analysis == ""              # 入口清零 + 提前退出不赋值 → 空串，不错贴上一轮


async def test_last_analysis_cleared_on_timeout_round():
    """RC-1：本轮流超时（被 wait_for 取消）→ last_analysis 恒为空串（降级轮 analysis 语义正确）。"""
    first = ([StreamEvent("text_delta", text="上一轮研判文本")]
             + _tool_events({"action": "hold", "reason": "r"}))
    d = _decider([first], settings=Settings(ai_trade_decision_timeout_s=0.1,
                                            ai_trade_decision_max_retries=0))
    await d.decide(_payload(1))
    assert d.last_analysis != ""

    async def _slow_stream(*, system, messages, tools):
        await asyncio.sleep(1.0)
        yield StreamEvent("message_done", stop_reason="end")

    d._llm.stream = _slow_stream   # type: ignore[attr-defined]
    _, degrade = await d.decide(_payload(2))
    assert degrade == "decision_timeout"
    assert d.last_analysis == ""


async def test_commit_round_stores_analysis_excerpt_in_journal():
    """RC-2/RC-3：commit_round(analysis=excerpt) 使 journal 末条含该摘录（供滚动摘要素材）。"""
    d = _decider([_tool_events({"action": "hold", "reason": "r"})])
    decision, _ = await d.decide(_payload(1))
    excerpt = "研判摘录片段。" * 20
    executed = {"ok": True, "action": decision.action, "positionAfter": "flat"}
    await d.commit_round(_payload(1), decision, executed, analysis=excerpt)
    assert d._journal[-1]["analysis"] == excerpt


async def test_commit_round_defaults_analysis_to_none_for_legacy_callers():
    """回测/旧调用不传 analysis → journal 条目 analysis 为 None（零回归）。"""
    d = _decider([_tool_events({"action": "hold", "reason": "r"})])
    decision, _ = await d.decide(_payload(1))
    executed = {"ok": True, "action": decision.action, "positionAfter": "flat"}
    await d.commit_round(_payload(1), decision, executed)
    assert d._journal[-1]["analysis"] is None


async def test_summarize_triggers_every_n_rounds_from_journal_and_resets_context():
    # contextRefreshRounds=15 > 12（40 条模型窗口硬上限对应约 13 轮）：证摘要素材是完整 _journal 而非
    # 可能被裁剪的 _context（P1-3）。前 15 轮每轮出决策；第 15 轮 commit 触发摘要（脚本第 16 个是摘要流）。
    scripts = [_tool_events({"action": "hold", "reason": f"r{i}"}) for i in range(15)]
    scripts.append([StreamEvent("text_delta", text="综合摘要：偏多"),
                    StreamEvent("message_done", stop_reason="end")])
    d = _decider(scripts, rounds=15)
    summary = None
    for r in range(1, 16):
        decision, _ = await d.decide(_payload(r))
        executed = {"ok": True, "action": decision.action, "positionAfter": "flat"}
        summary = await d.commit_round(_payload(r), decision, executed)
    assert summary == "综合摘要：偏多"
    # 摘要素材（最近一次 stream 的 messages）含全部 15 轮流水（journal 全量，非被裁剪的 context）
    digest_text = d._llm.last_messages[0].content[0].text  # type: ignore[attr-defined]
    for r in range(1, 16):
        assert f'"round": {r}' in digest_text
    # 上下文被摘要种子重置：首条为 user 轮且含摘要，_journal 清空，计数归零
    assert d._context[0].role == "user"
    assert "历史执行摘要" in d._context[0].content[0].text
    assert d._journal == []
    assert d.rounds_since_summary == 0
    assert d.summary == "综合摘要：偏多"


# ---------- LLM 失败先重试三次再降级（ai-trade-decision-llm-retry §八）----------

def _error_script():
    """一条「流内 error → llm_error（可重试）」脚本。"""
    return [StreamEvent("error", code="LLM_ERROR", message="boom")]


def _valid_open_long(size: float = 0.4):
    return _tool_events({"action": "open_long", "sizeFraction": size,
                         "confidence": 0.8, "reason": "trend"})


def _retry_settings(**kw):
    """重试单测统一 settings：退避基数/封顶置 0（仅剩 ≤250ms 抖动），保证快速且确定。"""
    base = {"ai_trade_retry_base_ms": 0, "ai_trade_retry_max_ms": 0}
    base.update(kw)
    return Settings(**base)


async def test_retries_then_succeeds():
    """瞬时 error → error → valid：重试后成功、degrade 清空、恰好消费 3 条脚本（1 首次 + 2 重试）。"""
    d = _decider([_error_script(), _error_script(), _valid_open_long()],
                 settings=_retry_settings(ai_trade_decision_max_retries=3))
    decision, degrade = await d.decide(_payload(1))
    assert degrade is None
    assert decision.action == "open_long"
    assert d._llm.calls == 3


async def test_degrades_after_exhausting_retries():
    """4 条全 error、max_retries=3 → 穷尽后才降级 llm_error/hold，共消费 4 条脚本（1 首次 + 3 重试）。"""
    d = _decider([_error_script() for _ in range(4)],
                 settings=_retry_settings(ai_trade_decision_max_retries=3))
    decision, degrade = await d.decide(_payload(1))
    assert degrade == "llm_error"
    assert decision.action == "hold"
    assert d._llm.calls == 4


async def test_no_retry_on_valid_first_attempt():
    """首次即产出合法决策 → 不重试，只调用一次。"""
    d = _decider([_valid_open_long(0.5)],
                 settings=_retry_settings(ai_trade_decision_max_retries=3))
    decision, degrade = await d.decide(_payload(1))
    assert degrade is None
    assert d._llm.calls == 1


async def test_no_retry_on_low_confidence():
    """low_confidence 是模型真实终态决策（非瞬时失败）→ 不重试，只调用一次（§4.1 白名单排除）。"""
    d = _decider([_tool_events({"action": "open_long", "sizeFraction": 0.5,
                                "confidence": 0.5, "reason": "weak"})],
                 settings=_retry_settings(ai_trade_decision_max_retries=3,
                                          ai_trade_min_confidence=0.9))
    decision, degrade = await d.decide(_payload(1))
    assert degrade == "low_confidence"
    assert decision.action == "hold"
    assert d._llm.calls == 1


async def test_retries_on_no_tool_call_then_succeeds():
    """只回文本无工具（no_tool_call，可重试）→ 重试后成功，调用两次。"""
    scripts = [[StreamEvent("text_delta", text="只回文本无工具"),
                StreamEvent("message_done", stop_reason="end")],
               _valid_open_long(0.3)]
    d = _decider(scripts, settings=_retry_settings(ai_trade_decision_max_retries=3))
    decision, degrade = await d.decide(_payload(1))
    assert degrade is None
    assert decision.action == "open_long"
    assert d._llm.calls == 2


async def test_retries_on_missing_size_then_succeeds():
    """open_long 缺 sizeFraction（missing_size，可重试）→ 重试后成功，调用两次。"""
    scripts = [_tool_events({"action": "open_long", "reason": "no size"}),
               _valid_open_long(0.5)]
    d = _decider(scripts, settings=_retry_settings(ai_trade_decision_max_retries=3))
    decision, degrade = await d.decide(_payload(1))
    assert degrade is None
    assert abs(decision.size_fraction - 0.5) < 1e-9
    assert d._llm.calls == 2


async def test_retries_on_timeout_then_succeeds():
    """首次 stream 挂起触发 wait_for 超时（可重试）→ 重试成功；有状态 stub + 调小 timeout + base/max=0
    保证快且确定（_ScriptedLLM 事件零耗时，无法制造超时，§八 #7）。"""
    state = {"n": 0}
    valid = _valid_open_long(0.3)

    async def _stub(*, system, messages, tools):
        state["n"] += 1
        if state["n"] == 1:
            await asyncio.sleep(1.0)              # > timeout(0.05) → 首次尝试超时
            yield StreamEvent("message_done", stop_reason="end")
            return
        for ev in valid:
            yield ev

    d = _decider([], settings=_retry_settings(ai_trade_decision_max_retries=3,
                                              ai_trade_decision_timeout_s=0.05))
    d._llm.stream = _stub   # type: ignore[attr-defined]
    decision, degrade = await d.decide(_payload(1))
    assert degrade is None
    assert decision.action == "open_long"
    assert state["n"] == 2                        # 1 超时 + 1 成功


async def test_cancelled_error_propagates_without_retry_or_degrade():
    """stream 抛 CancelledError（会话 STOP）→ 直接向上抛，不重试、不降级，仅尝试 1 次（§4.2 + 单测 #8）。"""
    state = {"n": 0}

    async def _stub(*, system, messages, tools):
        state["n"] += 1
        raise asyncio.CancelledError
        yield   # pragma: no cover —— 使 _stub 成为 async generator

    d = _decider([], settings=_retry_settings(ai_trade_decision_max_retries=3))
    d._llm.stream = _stub   # type: ignore[attr-defined]
    with pytest.raises(asyncio.CancelledError):
        await d.decide(_payload(1))
    assert state["n"] == 1


async def test_max_retries_zero_preserves_legacy_direct_degrade():
    """max_retries=0 → 首次 error 即降级（旧行为逐字回归），只调用一次（零回归门禁，§八）。"""
    d = _decider([_error_script()],
                 settings=_retry_settings(ai_trade_decision_max_retries=0))
    decision, degrade = await d.decide(_payload(1))
    assert degrade == "llm_error"
    assert decision.action == "hold"
    assert d._llm.calls == 1


async def test_last_analysis_reset_between_retries():
    """RC-1 跨尝试版：失败尝试产半截研判后 error、重试尝试产新研判并成功 → last_analysis 仅含成功尝试研判。"""
    first = [StreamEvent("text_delta", text="失败尝试的半截研判"),
             StreamEvent("error", code="LLM_ERROR", message="boom")]
    second = ([StreamEvent("text_delta", text="成功尝试的研判：开多。")]
              + _valid_open_long(0.3))
    d = _decider([first, second],
                 settings=_retry_settings(ai_trade_decision_max_retries=3))
    decision, degrade = await d.decide(_payload(1))
    assert degrade is None
    assert "成功尝试的研判" in d.last_analysis
    assert "失败尝试的半截研判" not in d.last_analysis


def test_retry_hint_suffix_mentions_reason():
    """§4.5：提示含 reason 与「只调用一次」；首次（hint=None）prompt 不含系统提示（逐字一致）。"""
    suffix = _retry_hint_suffix("missing_size")
    assert "missing_size" in suffix
    assert "只调用一次" in suffix
    assert "【系统提示】" not in build_decision_prompt(_payload(1))


# ---------- F2/F3 commit_round 反思注记 + 摘要种子尾注（ai-trade-reflection-hold-inertia-fix）----------

def _vetoed_note() -> dict:
    """一份否决注记（build_reflection_note 形状）：open_long 0.5 被 abort 改写为 hold。"""
    return {"verdict": "abort", "vetoed": True, "failClosed": False,
            "reason": "顶背离追多风险高", "proposedAction": "open_long",
            "proposedSizeFraction": 0.5, "proposedConfidence": 0.8,
            "finalAction": "hold", "finalSizeFraction": None}


async def test_commit_round_with_vetoed_reflection_enriches_journal_and_tool_result():
    """否决轮：journal 带完整 reflection 注记 + trigger；_context tool_result 为 executed 与注记的
    浅拷贝合并（executed 入参对象绝不被改动）。"""
    d = _decider([_tool_events({"action": "hold", "reason": "r"})])
    payload = {**_payload(1), "trigger": "candle"}
    decision, _ = await d.decide(payload)
    executed = {"ok": True, "action": "hold", "positionAfter": "flat"}
    executed_snapshot = dict(executed)
    note = _vetoed_note()
    await d.commit_round(payload, decision, executed, reflection=note)
    entry = d._journal[-1]
    assert entry["reflection"] == note
    assert entry["trigger"] == "candle"
    assert entry["decision"]["action"] == "hold"     # R-P1-3 不变式：journal 记最终 hold
    tool_result = d._context[-1].content[0].content
    assert tool_result == json.dumps({**executed, "reflection": note}, ensure_ascii=False)
    assert executed == executed_snapshot             # 入参对象未被就地改动


async def test_commit_round_confirm_note_kept_in_journal_but_not_tool_result():
    """非否决注记（confirm/收缩）：journal 保真，但 tool_result 逐字不变（仅否决轮富化）。"""
    d = _decider([_tool_events({"action": "hold", "reason": "r"})])
    decision, _ = await d.decide(_payload(1))
    executed = {"ok": True, "action": "hold", "positionAfter": "flat"}
    note = {**_vetoed_note(), "verdict": "confirm", "vetoed": False}
    await d.commit_round(_payload(1), decision, executed, reflection=note)
    assert d._journal[-1]["reflection"] == note
    tool_result = d._context[-1].content[0].content
    assert tool_result == json.dumps(executed, ensure_ascii=False)
    assert "reflection" not in tool_result


async def test_commit_round_default_reflection_none_zero_regression():
    """缺省调用（回测/既有单测路径）：journal 恒带 trigger/reflection 两键（None 值，形状稳定 R-06），
    tool_result 内容与升级前逐字相同（R-05 冻结契约）。"""
    d = _decider([_tool_events({"action": "hold", "reason": "r"})])
    decision, _ = await d.decide(_payload(1))
    executed = {"ok": True, "action": decision.action, "positionAfter": "flat"}
    await d.commit_round(_payload(1), decision, executed)
    entry = d._journal[-1]
    assert entry["reflection"] is None
    assert entry["trigger"] is None                  # _payload 无 trigger 键（回测/legacy 同形）
    tool_result = d._context[-1].content[0].content
    assert tool_result == json.dumps(executed, ensure_ascii=False)
    assert "reflection" not in tool_result


async def test_summary_seed_carries_background_not_instruction_note():
    """F3：摘要种子尾注声明「背景参考而非指令」，切断历史观望的路径依赖。"""
    scripts = [_tool_events({"action": "hold", "reason": "r"}),
               [StreamEvent("text_delta", text="综合摘要：偏多"),
                StreamEvent("message_done", stop_reason="end")]]
    d = _decider(scripts, rounds=1)                  # 第 1 轮 commit 即触发摘要
    decision, _ = await d.decide(_payload(1))
    executed = {"ok": True, "action": decision.action, "positionAfter": "flat"}
    summary = await d.commit_round(_payload(1), decision, executed)
    assert summary == "综合摘要：偏多"
    seed_text = d._context[0].content[0].text
    assert "历史执行摘要" in seed_text                # 既有锚点保留
    assert "背景参考而非指令" in seed_text
    assert "历史观望不构成继续观望的理由" in seed_text


# ---------- 【基础策略】降级码冻结（ai-trade-base-strategy-ma-band §6.1 用例 17 / P1-1）----------
def test_base_rule_reasons_are_not_retryable():
    """三个 `base_rule_*` 码是**确定性终态**，绝不可进入重试白名单。

    同一轮内价格与 MA 通道都是常量，重试必然被同样否决——每次重试白烧一次 LLM 调用与最多 60s 超时
    预算。这条断言是「门禁必须落在 `decide()` 之后」这一架构决策的回归防线（R-5）。
    """
    from app.ai_auto.decider import _RETRYABLE_DEGRADE_REASONS, _is_retryable_degrade

    codes = {"base_rule_ma_band", "base_rule_ma_band_direction", "base_rule_band_unavailable"}
    assert codes & _RETRYABLE_DEGRADE_REASONS == set()
    assert not _is_retryable_degrade("base_rule_ma_band(pctB=0.4)")
    assert not _is_retryable_degrade("base_rule_ma_band_direction(zone=upper)")
    assert not _is_retryable_degrade("base_rule_band_unavailable")


# ---------- 承诺退出线 holdExitPx（ai-trade-profit-protection-commitment C1）----------
async def test_hold_exit_px_parsed_only_for_hold_and_illegal_ignored():
    """C1：action=hold 携带的合法 holdExitPx 被采集；非法值忽略且**不降级**；close/非 hold 恒 None。"""
    d = _decider([_tool_events({"action": "hold", "reason": "wait", "holdExitPx": 29500.0})])
    decision, degrade = await d.decide(_payload(1))
    assert decision.action == "hold" and degrade is None
    assert decision.hold_exit_px == 29500.0

    d2 = _decider([_tool_events({"action": "hold", "reason": "wait", "holdExitPx": -1})])
    decision2, degrade2 = await d2.decide(_payload(1))
    assert decision2.action == "hold" and degrade2 is None and decision2.hold_exit_px is None

    d3 = _decider([_tool_events({"action": "close", "reason": "exit", "holdExitPx": 29500.0})])
    decision3, _ = await d3.decide(_payload(1))
    assert decision3.action == "close" and decision3.hold_exit_px is None

    d4 = _decider([_tool_events({"action": "open_long", "sizeFraction": 0.5, "confidence": 0.9,
                                 "reason": "go", "holdExitPx": 29500.0})])
    decision4, _ = await d4.decide(_payload(1))
    assert decision4.action == "open_long" and decision4.hold_exit_px is None


async def test_hold_exit_px_lands_in_commit_round_journal():
    """C1：holdExitPx 随 journal 落痕（滚动摘要重置后问责回路不断，同 orderType/limitPrice 先例）。"""
    d = _decider([_tool_events({"action": "hold", "reason": "wait", "holdExitPx": 29500.0})])
    decision, _ = await d.decide(_payload(1))
    await d.commit_round(_payload(1), decision, {"ok": True})
    assert d._journal[-1]["decision"]["holdExitPx"] == 29500.0


def test_allow_hold_exit_preserves_schema_identity_when_false():
    """G-1/R-5：allow_hold_exit 默认 False ⇒ build_decision_tool_schema(0.0) 恒等模块常量；True 露出可选字段。"""
    from app.ai_auto.prompts import DECISION_TOOL_SCHEMA, build_decision_tool_schema

    assert build_decision_tool_schema(0.0) is DECISION_TOOL_SCHEMA
    assert build_decision_tool_schema(0.0, allow_hold_exit=False) is DECISION_TOOL_SCHEMA
    schema = build_decision_tool_schema(0.0, allow_hold_exit=True)
    assert schema is not DECISION_TOOL_SCHEMA
    assert "holdExitPx" in schema["properties"]
    assert "holdExitPx" not in DECISION_TOOL_SCHEMA["properties"]      # 绝不改模块常量
    assert schema["required"] == ["action", "reason"]                 # 承诺退出线永远可选


def test_decider_exposes_hold_exit_field_only_when_allowed():
    """C1：装配点开启 allow_hold_exit ⇒ 决策器工具 schema 露出 holdExitPx；默认关闭则不露出。"""
    from app.config import Settings as _S

    llm = _ScriptedLLM([])
    off = AiTradeDecider(llm, "sys", context_refresh_rounds=10, settings=_S())
    on = AiTradeDecider(llm, "sys", context_refresh_rounds=10, settings=_S(),
                        allow_hold_exit=True)
    assert "holdExitPx" not in off._tool.input_schema["properties"]
    assert "holdExitPx" in on._tool.input_schema["properties"]


# ================= 深度思维链（ai-trade-deep-reasoning-chain §7.3 / §7.4）=================
from app.ai_auto.decider import _RETRYABLE_DEGRADE_REASONS      # noqa: E402
from app.ai_auto.reasoning_protocol import profile_by_name      # noqa: E402

_DEEP = profile_by_name("deep")


def _text_only(text: str, *, stop_reason: str = "end_turn", usage: dict | None = None):
    """只吐文本、不调工具的一次流（截断 / 内容拦截 / 浅研判三类用例的共同底座）。"""
    return [StreamEvent("text_delta", text=text),
            StreamEvent("message_done", stop_reason=stop_reason, usage=usage)]


def _deep_decider(scripts, *, settings=None, depth=None, emit=None):
    return AiTradeDecider(_ScriptedLLM(scripts), "sys", context_refresh_rounds=10,
                          settings=settings or _retry_settings(
                              ai_trade_decision_max_retries=0,
                              ai_trade_reasoning_reask_max=0),
                          depth_profile=depth or _DEEP)


# ---------------------------------------------------------------- D-1 / D-1b / D-1c
async def test_max_tokens_stop_reason_maps_to_reasoning_truncated():
    """D-1：`stop_reason="max_tokens"`（anthropic 口径）⇒ `reasoning_truncated`，不是 no_tool_call。"""
    d = _deep_decider([_text_only("long analysis", stop_reason="max_tokens")])
    decision, degrade = await d.decide(_payload(1))
    assert degrade == "reasoning_truncated"
    assert decision.action == "hold"                # 安全回落语义逐字不变


async def test_openai_length_finish_reason_also_maps_to_truncated():
    """D-1b：同款输入但 `stop_reason="length"`（OpenAI 把 finish_reason 塞进同一字段）⇒ 同样判截断。

    **这条用例的价值就是会因为有人把判据写回 `== "max_tokens"` 而判红。**
    """
    d = _deep_decider([_text_only("long analysis", stop_reason="length")])
    _, degrade = await d.decide(_payload(1))
    assert degrade == "reasoning_truncated"


async def test_content_filter_is_not_retryable():
    """D-1c：`content_filter` ⇒ `content_filtered` 且**不在**可重试集合里。

    重试同一份 prompt 只会再被拦一次，白烧一次预算与一次超时——它与「写太长」是两回事。
    """
    d = _deep_decider([_text_only("blocked", stop_reason="content_filter")],
                      settings=_retry_settings(ai_trade_decision_max_retries=3,
                                               ai_trade_reasoning_reask_max=0))
    _, degrade = await d.decide(_payload(1))
    assert degrade == "content_filtered"
    assert "content_filtered" not in _RETRYABLE_DEGRADE_REASONS
    assert d._llm.calls == 1                        # 零重试


async def test_truncated_is_retryable_and_the_hint_asks_for_compression():
    """D-2：截断可重试，且第二次尝试的 user 文本含**压缩**指令、不含通用 hint；上下文未被污染。"""
    d = _deep_decider([_text_only("x", stop_reason="max_tokens"),
                       _valid_open_long()],
                      settings=_retry_settings(ai_trade_decision_max_retries=2,
                                               ai_trade_reasoning_reask_max=0))
    decision, degrade = await d.decide(_payload(1))
    assert degrade is None and decision.action == "open_long"
    second = d._llm.last_messages[-1].content[0].text
    assert "Compress" in second and "S10-S12" in second
    assert "【系统提示】" not in second              # 深度档下纠偏提示整段英文
    assert d._context == [] and d._journal == []     # 一次性提示绝不落上下文


# ---------------------------------------------------------------- D-3 / D-4 / D-5
def _reask_settings(**kw):
    base = dict(ai_trade_decision_max_retries=0, ai_trade_reasoning_reask_max=1,
                ai_trade_reasoning_min_chars=3000)
    base.update(kw)
    return _retry_settings(**base)


async def test_shallow_analysis_triggers_exactly_one_reask():
    """D-3：首次研判 200 字符（阈 3000）⇒ 恰好多打一次 LLM；`last_reasoning.reasked` 为真。"""
    shallow = [StreamEvent("text_delta", text="s" * 200), *_valid_open_long()]
    deeper = [StreamEvent("text_delta", text="d" * 5000), *_valid_open_long()]
    d = _deep_decider([shallow, deeper], settings=_reask_settings())
    decision, degrade = await d.decide(_payload(1))
    assert d._llm.calls == 2
    assert degrade is None and decision.action == "open_long"
    assert d.last_reasoning.reasked is True and d.last_reasoning.reask_adopted is True
    assert len(d.last_analysis) == 5000


async def test_shallow_reask_never_downgrades_the_decision():
    """D-4（本轴最重要的一条）：追问失败 ⇒ **原样保留第一次的 open_long**，degrade 恒 None。

    浅研判是质量问题、不是安全问题。把它接到降级链上会造出「模型话少 ⇒ 全部降级观望」的新
    吸收态——本仓库为「反思致永不开单」已经付过一次学费。
    """
    shallow = [StreamEvent("text_delta", text="s" * 200), *_valid_open_long()]
    d = _deep_decider([shallow, _error_script()], settings=_reask_settings())
    decision, degrade = await d.decide(_payload(1))
    assert decision.action == "open_long" and degrade is None
    assert d.last_reasoning.reasked is True
    assert d.last_reasoning.reask_adopted is False
    assert d.last_analysis == "s" * 200              # 研判全文回到第一次那一份


async def test_shallow_reask_keeps_the_deeper_of_the_two():
    """D-5：追问**更浅** ⇒ 保留首次决策与首次研判（追问必须是单向增益）。"""
    shallow = [StreamEvent("text_delta", text="s" * 200),
               *_tool_events({"action": "open_long", "sizeFraction": 0.25,
                              "confidence": 0.9, "reason": "first"})]
    shallower = [StreamEvent("text_delta", text="t" * 50),
                 *_tool_events({"action": "close", "reason": "second"}, tool_id="t2")]
    d = _deep_decider([shallow, shallower], settings=_reask_settings())
    decision, _ = await d.decide(_payload(1))
    assert decision.action == "open_long" and decision.reason == "first"
    assert d.last_reasoning.reask_adopted is False
    assert d.last_analysis == "s" * 200


async def test_reask_emits_into_its_own_phase():
    """D-5b：追问的文本增量全部落在 `phase="reask"`，`analysis` 在追问期间**一帧都不再新增**。"""
    seen: list[tuple[str, int]] = []

    async def _emit(part):
        seen.append((part.get("phase"), len(part.get("delta") or "")))

    shallow = [StreamEvent("text_delta", text="s" * 200), *_valid_open_long()]
    deeper = [StreamEvent("text_delta", text="d" * 5000), *_valid_open_long()]
    d = _deep_decider([shallow, deeper], settings=_reask_settings())
    await d.decide(_payload(1), emit=_emit)
    analysis_chars = sum(n for phase, n in seen if phase == "analysis")
    reask_chars = sum(n for phase, n in seen if phase == "reask")
    assert analysis_chars == 200                     # 首次研判原样保留，未被追加污染
    assert reask_chars == 5000                       # 追问全部落在独立相位


async def test_reask_uses_extra_instruction_not_hint():
    """D-5c：追问那次尝试的 user 文本含追问提示、**不含**任何 `_retry_hint_suffix` 文案。

    `hint` 的值域是 degrade 码（`_retry_hint_suffix` 的查表键），塞自由文本进去会让那张表多出
    一条永远不匹配的分支。
    """
    shallow = [StreamEvent("text_delta", text="s" * 200), *_valid_open_long()]
    deeper = [StreamEvent("text_delta", text="d" * 5000), *_valid_open_long()]
    d = _deep_decider([shallow, deeper], settings=_reask_settings())
    await d.decide(_payload(1))
    second = d._llm.last_messages[-1].content[0].text
    assert "this is a depth request, not a rejection" in second
    assert "ran out of output budget" not in second
    assert d._context == [] and d._journal == []


async def test_reask_disabled_when_min_chars_is_zero():
    """D-6：`reask_max=0` 或 `min_chars=0` ⇒ LLM 调用次数与今日相同。"""
    shallow = [StreamEvent("text_delta", text="s" * 10), *_valid_open_long()]
    from app.ai_auto.reasoning_protocol import resolve_depth_profile

    zero_reask = _reask_settings(ai_trade_reasoning_reask_max=0)
    d = _deep_decider([list(shallow)], settings=zero_reask,
                      depth=resolve_depth_profile(zero_reask))
    await d.decide(_payload(1))
    assert d._llm.calls == 1
    zero_chars = _reask_settings(ai_trade_reasoning_min_chars=0)
    d2 = _deep_decider([list(shallow)], settings=zero_chars,
                       depth=resolve_depth_profile(zero_chars))
    assert resolve_depth_profile(zero_chars).min_analysis_chars == 0
    await d2.decide(_payload(1))
    assert d2._llm.calls == 1


async def test_no_reask_without_a_depth_profile():
    """未装配深度档（32 个既有假客户端用例的形态）⇒ 追问整段空转，元数据恒 None。"""
    shallow = [StreamEvent("text_delta", text="s" * 10), *_valid_open_long()]
    d = AiTradeDecider(_ScriptedLLM([shallow]), "sys", context_refresh_rounds=10,
                       settings=_reask_settings())
    await d.decide(_payload(1))
    assert d._llm.calls == 1
    assert d.last_reasoning is None
    assert d.depth_name == ""


# ---------------------------------------------------------------- D-5d（轮级墙钟闸）
async def test_round_deadline_stops_new_attempts_only(monkeypatch):
    """D-5d：墙钟耗尽后不再发起新尝试，但**不中断在飞的那一次**；沿用当前 degrade，不新造码。

    用假单调钟而不是「设一个极小的超时」：后者依赖机器有多快，是一条会偶发翻绿的用例。
    """
    import types

    import app.ai_auto.decider as decider_mod

    calls = {"n": 0}

    def _fake_monotonic() -> float:
        calls["n"] += 1
        return 0.0 if calls["n"] == 1 else 999.0    # 开轮 0s，此后每次检查都已过 999s

    # 替换的是**决策器模块自己的 `time` 引用**，不是全局 `time.monotonic`——后者会把 asyncio
    # 的定时器一并打翻（那是一条与本用例无关的红）。
    monkeypatch.setattr(decider_mod, "time", types.SimpleNamespace(monotonic=_fake_monotonic))
    d = _deep_decider([_error_script(), _valid_open_long()],
                      settings=_retry_settings(ai_trade_decision_max_retries=3,
                                               ai_trade_reasoning_reask_max=0))
    decision, degrade = await d.decide(_payload(1))
    assert d._llm.calls == 1                        # 首次已发起并跑完，之后不再追加
    assert degrade == "llm_error"                   # 沿用当前码，绝不新造 round_deadline
    assert decision.action == "hold"
    assert d.last_reasoning.deadline_hit is True


async def test_reask_is_skipped_and_flagged_when_the_round_deadline_is_gone(monkeypatch):
    """想追问但墙钟预算已耗尽 ⇒ 不再发起，且 `deadline_hit` 明示（否则就是新的静默行为）。"""
    import types

    import app.ai_auto.decider as decider_mod

    calls = {"n": 0}

    def _fake_monotonic() -> float:
        calls["n"] += 1
        return 0.0 if calls["n"] == 1 else 999.0

    monkeypatch.setattr(decider_mod, "time", types.SimpleNamespace(monotonic=_fake_monotonic))
    shallow = [StreamEvent("text_delta", text="s" * 200), *_valid_open_long()]
    d = _deep_decider([shallow, _valid_open_long()], settings=_reask_settings())
    decision, degrade = await d.decide(_payload(1))
    assert d._llm.calls == 1                      # 追问没被发起
    assert decision.action == "open_long" and degrade is None
    assert d.last_reasoning.reasked is False
    assert d.last_reasoning.deadline_hit is True


async def test_rejected_reask_is_not_reported_as_a_deadline_hit():
    """一次纯质量拒收不得被报成「本轮超时预算用尽」——墙钟没有阻止任何事。"""
    shallow = [StreamEvent("text_delta", text="s" * 200), *_valid_open_long()]
    shallower = [StreamEvent("text_delta", text="t" * 50), *_valid_open_long()]
    d = _deep_decider([shallow, shallower], settings=_reask_settings())
    await d.decide(_payload(1))
    assert d.last_reasoning.reasked is True
    assert d.last_reasoning.reask_adopted is False
    assert d.last_reasoning.deadline_hit is False


async def test_attempts_cap_bounds_the_round_even_with_three_retries():
    """尝试次数闸：`deep` 档 cap=2 ⇒ 即使 settings 允许 3 次重试，一轮也只发起 2 次尝试。"""
    d = _deep_decider([_error_script(), _error_script(), _error_script(), _valid_open_long()],
                      settings=_retry_settings(ai_trade_decision_max_retries=3,
                                               ai_trade_reasoning_reask_max=0))
    _, degrade = await d.decide(_payload(1))
    assert d._llm.calls == 2
    assert degrade == "llm_error"


async def test_attempts_cap_is_absent_without_a_depth_profile():
    """未装配深度档 ⇒ 尝试次数逐字回退到 settings（4 次 = 首次 + 3 重试）。"""
    d = AiTradeDecider(_ScriptedLLM([_error_script(), _error_script(), _error_script(),
                                     _valid_open_long()]), "sys", context_refresh_rounds=10,
                       settings=_retry_settings(ai_trade_decision_max_retries=3))
    decision, degrade = await d.decide(_payload(1))
    assert d._llm.calls == 4 and degrade is None and decision.action == "open_long"


# ---------------------------------------------------------------- D-7 / B-7 / B-8 / B-9
async def test_usage_is_normalised_and_carried_into_meta():
    """D-7：anthropic 风格 usage ⇒ `ReasoningMeta.input_tokens/output_tokens` 正确。"""
    script = [StreamEvent("text_delta", text="a" * 4000),
              StreamEvent("tool_use_start", id="t1", name="submit_trade_decision"),
              StreamEvent("tool_use_args_delta", id="t1",
                          partial_json=json.dumps({"action": "hold", "reason": "r"})),
              StreamEvent("message_done", stop_reason="tool_use",
                          usage={"input_tokens": 12083, "output_tokens": 3401})]
    d = _deep_decider([script])
    await d.decide(_payload(1))
    assert d.last_reasoning.input_tokens == 12083
    assert d.last_reasoning.output_tokens == 3401
    assert d.last_reasoning.depth == "deep"


async def test_openai_second_message_done_backfills_usage():
    """B-7：OpenAI 的真实顺序是两条 `message_done`——第一条带 finish_reason 且 usage 为 None，
    第二条 `choices` 为空只带 usage。合入前「首条即 break」的写法在此判红。"""
    script = [StreamEvent("text_delta", text="a" * 100),
              StreamEvent("tool_use_start", id="t1", name="submit_trade_decision"),
              StreamEvent("tool_use_args_delta", id="t1",
                          partial_json=json.dumps({"action": "hold", "reason": "r"})),
              StreamEvent("message_done", stop_reason="tool_calls", usage=None),
              StreamEvent("message_done", stop_reason=None,
                          usage={"input_tokens": 777, "output_tokens": 88})]
    d = _deep_decider([script])
    decision, degrade = await d.decide(_payload(1))
    assert degrade is None and decision.action == "hold"
    assert d.last_reasoning.input_tokens == 777
    assert d.last_reasoning.output_tokens == 88


async def test_events_after_message_done_are_ignored():
    """B-8：收尾之后到达的 `text_delta` / `error` 一律不生效——`last_analysis` 与「见到即 break」
    的结果**逐字相同**，且晚到的 error 不把一轮已成功的决策变成 `llm_error`。"""
    script = [StreamEvent("text_delta", text="kept"),
              StreamEvent("tool_use_start", id="t1", name="submit_trade_decision"),
              StreamEvent("tool_use_args_delta", id="t1",
                          partial_json=json.dumps({"action": "hold", "reason": "r"})),
              StreamEvent("message_done", stop_reason="tool_use"),
              StreamEvent("text_delta", text="DROPPED"),
              StreamEvent("error", code="LATE", message="late failure")]
    d = _deep_decider([script])
    decision, degrade = await d.decide(_payload(1))
    assert degrade is None and decision.action == "hold"
    assert d.last_analysis == "kept"


async def test_usage_accumulates_across_attempts_within_a_round():
    """一轮内的每一次尝试都真的花了钱：token 用量必须**累加**，不能只留最后一次。

    只留最后一次等于系统性低估成本，而成本可见（G-8）正是本特性对「token 成本 5 倍」这条
    头号真金风险的主要对价——一个少报的数字比没有数字更坏。
    """
    first = [StreamEvent("text_delta", text="s" * 200),
             StreamEvent("tool_use_start", id="t1", name="submit_trade_decision"),
             StreamEvent("tool_use_args_delta", id="t1",
                         partial_json=json.dumps({"action": "hold", "reason": "r"})),
             StreamEvent("message_done", stop_reason="tool_use",
                         usage={"input_tokens": 100, "output_tokens": 10})]
    second = [StreamEvent("text_delta", text="d" * 5000),
              StreamEvent("tool_use_start", id="t2", name="submit_trade_decision"),
              StreamEvent("tool_use_args_delta", id="t2",
                          partial_json=json.dumps({"action": "hold", "reason": "r"})),
              StreamEvent("message_done", stop_reason="tool_use",
                          usage={"input_tokens": 900, "output_tokens": 90})]
    d = _deep_decider([first, second], settings=_reask_settings())
    await d.decide(_payload(1))
    assert d._llm.calls == 2
    assert d.last_reasoning.input_tokens == 1000
    assert d.last_reasoning.output_tokens == 100


async def test_rejected_reask_restores_the_first_attempt_stop_reason():
    """被拒收的追问不得改写 `truncated`——那个位必须描述**被采纳**的那一次尝试。"""
    first = [StreamEvent("text_delta", text="s" * 200), *_valid_open_long()]
    truncated_reask = _text_only("x" * 300, stop_reason="max_tokens")
    d = _deep_decider([first, truncated_reask], settings=_reask_settings())
    decision, degrade = await d.decide(_payload(1))
    assert decision.action == "open_long" and degrade is None
    assert d.last_reasoning.reask_adopted is False
    assert d.last_reasoning.truncated is False       # 首次尝试没被截断


async def test_usage_absent_marks_provider_unknown():
    """B-9：provider 全程不回 usage ⇒ 两个 token 读数为 `None` 而不是 0。"""
    d = _deep_decider([_valid_open_long()])
    await d.decide(_payload(1))
    assert d.last_reasoning.input_tokens is None
    assert d.last_reasoning.output_tokens is None


# ---------------------------------------------------------------- D-9（上限 ×5）
async def test_analysis_cap_is_twenty_thousand():
    """D-9：25000 字符输入 ⇒ `len(last_analysis) == 20000`（5× 上限真的生效）。"""
    script = [StreamEvent("text_delta", text="x" * 25000), *_valid_open_long()]
    d = _deep_decider([script])
    await d.decide(_payload(1))
    assert len(d.last_analysis) == 20000


# ---------------------------------------------------------------- D-11 / D-12（证据摘要）
def _digest_decider(args: dict, *, allow: bool = True):
    return AiTradeDecider(_ScriptedLLM([_tool_events(args)]), "sys",
                          context_refresh_rounds=10,
                          settings=_retry_settings(ai_trade_decision_max_retries=0,
                                                   ai_trade_reasoning_reask_max=0),
                          depth_profile=_DEEP, allow_reasoning_digest=allow)


async def test_evidence_digest_is_optional_and_never_downgrades():
    """D-11：缺席 / 非字符串 / 超长三种输入都不产生 degrade；超长按 400 截断。"""
    d = _digest_decider({"action": "hold", "reason": "r"})
    decision, degrade = await d.decide(_payload(1))
    assert degrade is None and decision.evidence_digest is None

    d2 = _digest_decider({"action": "hold", "reason": "r", "evidenceDigest": 12345})
    decision2, degrade2 = await d2.decide(_payload(1))
    assert degrade2 is None and decision2.evidence_digest == "12345"

    d3 = _digest_decider({"action": "hold", "reason": "r", "evidenceDigest": "摘" * 900})
    decision3, degrade3 = await d3.decide(_payload(1))
    assert degrade3 is None and len(decision3.evidence_digest) == 400


async def test_english_digest_never_downgrades_or_changes_the_decision():
    """D-12：摘要写成英文 ⇒ 决策器**原样收下**（语言校验是呈现层的事，见 session 侧用例）。

    三条铁律：绝不产生 degrade、绝不改 `TradeDecision` 的其余字段、绝不触发追问。
    """
    d = _digest_decider({"action": "open_long", "sizeFraction": 0.25, "confidence": 0.9,
                         "reason": "理由", "evidenceDigest": "Adopted the 4H trend."})
    decision, degrade = await d.decide(_payload(1))
    assert degrade is None
    assert decision.action == "open_long" and decision.size_fraction == 0.25
    assert decision.evidence_digest == "Adopted the 4H trend."
    assert d.last_reasoning.reasked is False


async def test_digest_is_absent_when_the_gate_is_closed():
    """总闸关 ⇒ 恒 None（老会话 / off 档 wire 逐字回退）。"""
    d = _digest_decider({"action": "hold", "reason": "r", "evidenceDigest": "中文摘要"},
                        allow=False)
    decision, _ = await d.decide(_payload(1))
    assert decision.evidence_digest is None


def test_decider_exposes_digest_field_only_when_allowed():
    """schema 恒等断言：总闸关 ⇒ 原样返回模块常量（G-2）。"""
    from app.ai_auto.prompts import DECISION_TOOL_SCHEMA, build_decision_tool_schema

    assert build_decision_tool_schema(0.0) is DECISION_TOOL_SCHEMA
    opened = build_decision_tool_schema(0.0, allow_reasoning_digest=True)
    assert "evidenceDigest" in opened["properties"]
    assert "evidenceDigest" not in DECISION_TOOL_SCHEMA["properties"]


# ---------------------------------------------------------------- D-10（安全契约冻结）
async def test_cancelled_error_still_propagates_on_the_deep_path():
    """D-10：深度档下 `asyncio.CancelledError` 仍原样上抛（绝不吞、绝不降级）。"""
    class _Cancel:
        provider = "anthropic"
        calls = 0

        async def stream(self, *, system, messages, tools):
            raise asyncio.CancelledError()
            yield   # pragma: no cover

    d = AiTradeDecider(_Cancel(), "sys", context_refresh_rounds=10,
                       settings=_retry_settings(ai_trade_decision_max_retries=3),
                       depth_profile=_DEEP)
    with pytest.raises(asyncio.CancelledError):
        await d.decide(_payload(1))


# ---------------------------------------------------------------- 摘要字符兜底
async def test_summary_seed_is_bounded_by_the_character_cap():
    """§3.3.4：摘要种子除模型侧预算外再加一道字符兜底（它是每轮都会被重读的那一段）。"""
    long_summary = [StreamEvent("text_delta", text="摘" * 5000),
                    StreamEvent("message_done", stop_reason="end_turn")]
    d = _deep_decider([long_summary])
    text = await d.summarize()
    assert len(text) == 3000
