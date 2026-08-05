"""LLM 子代理测试（strategy-factor-discovery §7.1 / §2.11）—— **一律 fail-open**。

桩 LLM 覆盖：正常解析、无工具调用、多次调用取首条、非法 JSON、超时、报错、未配置 ⇒ 全部
fail-open 且 ``llmDegraded`` 如实标注；小时调用上限触顶后跳过。

fail-open 是刻意的、与验证层的 fail-closed 正好相反：LLM 负责**供给与边界精判**，达标与否
绝不由它说了算（D-4）。DoD #9 要求「LLM 未配置 / 报错时任务照常推进」。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.agent.llm.base import StreamEvent
from app.discovery.llm_agents import (
    DiscoveryLlmAgents,
    HourlyCallBudget,
    accepted_hashes,
    resolve_llm_client,
)
from app.discovery.prompts import GENERATE_TOOL_NAME, REVIEW_TOOL_NAME


class _ScriptedLLM:
    """按脚本吐流事件的 LLM 桩（每次 ``stream`` 消费一条脚本）。"""

    def __init__(self, scripts: list):
        self._scripts = list(scripts)
        self.calls = 0

    async def stream(self, *, system, messages, tools):
        self.calls += 1
        script = self._scripts.pop(0) if self._scripts else []
        if isinstance(script, Exception):
            raise script
        for event in script:
            yield event


def _tool_events(name: str, payload: dict, tool_id: str = "t1") -> list:
    return [StreamEvent(kind="tool_use_start", id=tool_id, name=name),
            StreamEvent(kind="tool_use_args_delta", id=tool_id,
                        partial_json=json.dumps(payload)),
            StreamEvent(kind="message_done", stop_reason="tool_use")]


def _agents(scripts, **kw) -> DiscoveryLlmAgents:
    budget = kw.pop("budget", HourlyCallBudget(24))
    return DiscoveryLlmAgents(_ScriptedLLM(scripts), timeout_s=kw.pop("timeout_s", 5.0),
                              max_retries=kw.pop("max_retries", 0), budget=budget)


# ============================ 正常路径 ============================
async def test_generate_parses_candidates():
    agents = _agents([_tool_events(GENERATE_TOOL_NAME, {
        "candidates": [{"expr": "ts_mean(ret,20)", "mechanism": "trend_persistence"},
                       {"expr": "neg(ts_zscore(ret,8))"}]})])
    outcome = await agents.generate({"count": 2})
    assert outcome.degraded is False and outcome.calls == 1
    assert len(outcome.payload["candidates"]) == 2
    assert outcome.prompt_chars > 0


async def test_review_parses_verdicts():
    agents = _agents([_tool_events(REVIEW_TOOL_NAME, {
        "verdicts": [{"hash": "a", "accept": False, "reason": "量纲不可比"},
                     {"hash": "b", "accept": True}]})])
    outcome = await agents.review({"candidates": [{"hash": "a"}, {"hash": "b"}]})
    assert outcome.degraded is False
    vetoed, reasons = accepted_hashes(outcome, ["a", "b"])
    assert vetoed == {"a"} and reasons["a"] == "量纲不可比"


async def test_first_tool_call_wins_when_the_model_calls_twice():
    agents = _agents([
        _tool_events(GENERATE_TOOL_NAME, {"candidates": [{"expr": "first"}]}, "t1")
        + _tool_events(GENERATE_TOOL_NAME, {"candidates": [{"expr": "second"}]}, "t2")])
    outcome = await agents.generate({"count": 1})
    assert outcome.payload["candidates"] == [{"expr": "first"}]


# ============================ fail-open 全谱 ============================
@pytest.mark.parametrize("script,reason", [
    ([StreamEvent(kind="text_delta", text="我觉得……"),
      StreamEvent(kind="message_done", stop_reason="end_turn")], "no_tool_call"),
    ([StreamEvent(kind="tool_use_start", id="t1", name="some_other_tool"),
      StreamEvent(kind="message_done")], "no_tool_call"),
    ([StreamEvent(kind="tool_use_start", id="t1", name=GENERATE_TOOL_NAME),
      StreamEvent(kind="tool_use_args_delta", id="t1", partial_json="{not json"),
      StreamEvent(kind="message_done")], "no_tool_call"),
    ([StreamEvent(kind="error", code="LLM_ERROR", message="upstream 500")], "llm_error"),
])
async def test_generate_fails_open_with_honest_reason(script, reason):
    agents = _agents([script])
    outcome = await agents.generate({"count": 3})
    assert outcome.degraded is True
    assert outcome.degraded_reason == reason
    assert outcome.payload == {}


async def test_review_degradation_accepts_everything():
    """审查降级 ⇒ **视为全部 accept**：LLM 挂了绝不能把搜索卡住（fail-open）。"""
    agents = _agents([[StreamEvent(kind="error", code="LLM_ERROR", message="boom")]])
    outcome = await agents.review({"candidates": [{"hash": "a"}]})
    assert outcome.degraded is True
    vetoed, reasons = accepted_hashes(outcome, ["a"])
    assert vetoed == set() and reasons == {}


async def test_timeout_is_reported_as_degraded_not_raised():
    class _Hanging:
        async def stream(self, *, system, messages, tools):
            await asyncio.sleep(5)
            yield StreamEvent(kind="message_done")

    agents = DiscoveryLlmAgents(_Hanging(), timeout_s=0.02, max_retries=0)
    outcome = await agents.generate({"count": 1})
    assert outcome.degraded and outcome.degraded_reason == "timeout"


async def test_unconfigured_client_degrades_without_calling():
    agents = DiscoveryLlmAgents(None)
    assert agents.enabled is False
    outcome = await agents.generate({"count": 5})
    assert outcome.degraded and outcome.degraded_reason == "not_configured"
    assert outcome.calls == 0


async def test_invalid_payload_shape_is_degraded():
    agents = _agents([_tool_events(GENERATE_TOOL_NAME, {"candidates": "not a list"})])
    outcome = await agents.generate({"count": 1})
    assert outcome.degraded and outcome.degraded_reason == "invalid_payload"


async def test_non_dict_items_are_dropped_not_crashed():
    agents = _agents([_tool_events(GENERATE_TOOL_NAME, {
        "candidates": [{"expr": "ok"}, "garbage", 42]})])
    outcome = await agents.generate({"count": 3})
    assert outcome.payload["candidates"] == [{"expr": "ok"}]


async def test_retry_is_attempted_then_degrades():
    agents = _agents([[StreamEvent(kind="error", code="X", message="a")],
                      [StreamEvent(kind="error", code="X", message="b")]],
                     max_retries=1)
    outcome = await agents.generate({"count": 1})
    assert outcome.calls == 2 and outcome.degraded


async def test_retry_recovers_on_the_second_attempt():
    agents = _agents([[StreamEvent(kind="error", code="X", message="a")],
                      _tool_events(GENERATE_TOOL_NAME, {"candidates": [{"expr": "ok"}]})],
                     max_retries=1)
    outcome = await agents.generate({"count": 1})
    assert outcome.degraded is False and outcome.calls == 2


# ============================ 成本闸 ============================
def test_hourly_budget_slides():
    clock = {"now": 0.0}
    budget = HourlyCallBudget(2, clock=lambda: clock["now"])
    assert budget.allows()
    budget.record()
    budget.record()
    assert not budget.allows() and budget.used() == 2
    clock["now"] = 3601.0        # 一小时后旧调用被逐出
    assert budget.allows() and budget.used() == 0


async def test_calls_are_skipped_once_the_hourly_cap_is_reached():
    budget = HourlyCallBudget(1)
    agents = _agents([_tool_events(GENERATE_TOOL_NAME, {"candidates": []}),
                      _tool_events(GENERATE_TOOL_NAME, {"candidates": []})],
                     budget=budget)
    first = await agents.generate({"count": 1})
    second = await agents.generate({"count": 1})
    assert first.degraded is False
    assert second.degraded and second.degraded_reason == "hourly_cap_reached"
    assert second.calls == 0


def test_usage_flags_the_process_scoped_window():
    """UI 必须标注「本小时用量自进程启动起算」，不许让用户误以为是跨重启累计（§2.11）。"""
    agents = DiscoveryLlmAgents(None, budget=HourlyCallBudget(24))
    usage = agents.usage()
    assert usage["limit"] == 24 and usage["callsThisHour"] == 0
    assert usage["sinceProcessStart"] is True


# ============================ 裁决语义：只减不增 ============================
def test_verdicts_can_only_veto_never_bypass_validation():
    from app.discovery.llm_agents import LlmOutcome

    outcome = LlmOutcome(payload={"verdicts": [
        {"hash": "a", "accept": True}, {"hash": "b", "accept": False},
        {"hash": "unknown", "accept": False}]})
    vetoed, _reasons = accepted_hashes(outcome, ["a", "b"])
    assert vetoed == {"b"}          # accept=true 不代表放行，仍要过验证层
    assert "unknown" not in vetoed  # 未知哈希一律忽略，模型编不出否决


# ============================ 客户端解析 ============================
async def test_resolve_client_returns_none_when_llm_switch_is_off():
    class _Settings:
        discovery_llm_enabled = False

    assert await resolve_llm_client(None, _Settings()) is None


async def test_resolve_client_swallows_errors_and_degrades():
    class _Settings:
        discovery_llm_enabled = True

    def _boom():
        raise RuntimeError("no db")

    assert await resolve_llm_client(_boom, _Settings()) is None
