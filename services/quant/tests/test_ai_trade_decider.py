"""AiTradeDecider 单测（ai-auto-trading §2.3 / §2.4 / P1-3 / P1-5）。

桩 LLMClient 产出 `tool_use_start/args_delta/message_done` 流：决策器自拼 partial_json → 决策；
LLM 超时 / 无工具 / 多工具（取首条）/ 非法入参 / open_* 缺 sizeFraction → 一律 hold（安全回落）；
sizeFraction 越界夹紧；summarize 到第 N 轮触发、以 `_journal`（非 `_context`）为素材、上下文被摘要
种子重置、`_journal` 清空、滚动累积。纯离线（无网络、无 DB）。
"""
from __future__ import annotations

import asyncio
import json

from app.agent.llm.base import StreamEvent
from app.ai_auto.decider import AiTradeDecider
from app.ai_auto.prompts import build_decision_prompt
from app.config import Settings


class _ScriptedLLM:
    """按预置脚本逐次响应 stream；记录最近一次 messages（供摘要素材断言）。"""

    def __init__(self, scripts: list[list[StreamEvent]]):
        self._scripts = list(scripts)
        self.last_messages = None

    async def stream(self, *, system, messages, tools):
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
    return AiTradeDecider(_ScriptedLLM(scripts), "sys", context_refresh_rounds=rounds,
                          settings=settings or Settings())


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

    d = _decider([], settings=Settings(ai_trade_decision_timeout_s=0.01))
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
    d = _decider([first], settings=Settings(ai_trade_decision_timeout_s=0.1))
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
