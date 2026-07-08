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
