"""情报 Agent 的流式编排与工具解析（§7.1 · test_ai_intel_agent）。

全部用假客户端。三条判据对应三个曾经真实发生过的坑：
① usage 在 OpenAI 标准上走**第二条** `message_done`；② 截断口径必须 provider 无关；
③ 任何失败都翻译成降级码而不是异常——情报绝不阻断真实资金的交易路径。
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.agent.llm.base import StreamEvent
from app.ai_auto.intel.agent import run_round

TOOL = "submit_market_intel"
PAYLOAD = {"items": [{"headline": "h"}]}


class FakeLLM:
    """按脚本产出 `StreamEvent` 的假客户端（`stream` 签名与 Protocol 逐字一致）。"""

    provider = "anthropic"

    def __init__(self, events, *, capture=None):
        self._events = events
        self.capture = capture if capture is not None else {}

    async def stream(self, *, system, messages, tools):
        self.capture["system"] = system
        self.capture["messages"] = messages
        self.capture["tools"] = tools
        for event in self._events:
            yield event


def _tool_events(payload: dict, *, stop_reason="tool_use", usage=None):
    return [
        StreamEvent(kind="text_delta", text="S1 通读语料。"),
        StreamEvent(kind="tool_use_start", id="t1", name=TOOL),
        StreamEvent(kind="tool_use_args_delta", id="t1",
                    partial_json=json.dumps(payload, ensure_ascii=False)),
        StreamEvent(kind="message_done", stop_reason=stop_reason, usage=usage),
    ]


async def _run(llm):
    return await run_round(llm, system="sys", user="usr", tool_name=TOOL,
                           tool_description="d", tool_schema={"type": "object"},
                           timeout_s=5.0)


@pytest.mark.asyncio
async def test_happy_path_parses_tool_input_and_keeps_notes():
    llm = FakeLLM(_tool_events(PAYLOAD, usage={"input_tokens": 10, "output_tokens": 3}))
    result = await _run(llm)
    assert result.degrade is None
    assert result.raw == PAYLOAD
    assert result.notes.startswith("S1")
    assert (result.input_tokens, result.output_tokens) == (10, 3)


@pytest.mark.asyncio
async def test_tools_is_exactly_one_and_is_the_intel_tool():
    """§3.9 第 ② 层防护：采集 Agent **结构上**无法交易。"""
    llm = FakeLLM(_tool_events(PAYLOAD))
    await _run(llm)
    assert len(llm.capture["tools"]) == 1
    assert llm.capture["tools"][0].name == TOOL


@pytest.mark.asyncio
async def test_no_tool_call_is_reported_as_such():
    llm = FakeLLM([StreamEvent(kind="text_delta", text="我想了想"),
                   StreamEvent(kind="message_done", stop_reason="end_turn")])
    result = await _run(llm)
    assert result.raw is None and result.degrade == "no_tool_call"


@pytest.mark.asyncio
async def test_truncation_is_provider_agnostic():
    """Anthropic 报 `max_tokens`、OpenAI 把 `finish_reason="length"` 塞进同一个字段。"""
    for stop in ("max_tokens", "length"):
        llm = FakeLLM([StreamEvent(kind="text_delta", text="很长的研判"),
                       StreamEvent(kind="message_done", stop_reason=stop)])
        result = await _run(llm)
        assert result.degrade == "reasoning_truncated", stop


@pytest.mark.asyncio
async def test_usage_arrives_on_a_second_message_done():
    """OpenAI 的 usage 走一个 `choices` 为空的收尾 chunk：见到首条即 break 会让它恒空。"""
    events = _tool_events(PAYLOAD, stop_reason="tool_calls", usage=None)
    events.append(StreamEvent(kind="message_done", stop_reason=None,
                              usage={"input_tokens": 7, "output_tokens": 2}))
    result = await _run(FakeLLM(events))
    assert result.raw == PAYLOAD
    assert (result.input_tokens, result.output_tokens) == (7, 2)


@pytest.mark.asyncio
async def test_invalid_json_is_not_a_crash():
    events = [StreamEvent(kind="tool_use_start", id="t1", name=TOOL),
              StreamEvent(kind="tool_use_args_delta", id="t1", partial_json="{not json"),
              StreamEvent(kind="message_done", stop_reason="tool_use")]
    result = await _run(FakeLLM(events))
    assert result.raw is None and result.degrade == "no_tool_call"


@pytest.mark.asyncio
async def test_error_event_becomes_llm_failed():
    llm = FakeLLM([StreamEvent(kind="error", code="LLM_HTTP_ERROR", message="503")])
    result = await _run(llm)
    assert result.degrade == "llm_failed"


@pytest.mark.asyncio
async def test_timeout_becomes_llm_failed():
    class Hanging:
        provider = "anthropic"

        async def stream(self, *, system, messages, tools):
            await asyncio.sleep(10)
            yield StreamEvent(kind="message_done")

    result = await run_round(Hanging(), system="s", user="u", tool_name=TOOL,
                             tool_description="d", tool_schema={}, timeout_s=0.05)
    assert result.degrade == "llm_failed"


@pytest.mark.asyncio
async def test_cancellation_propagates():
    """关停信号绝不被吞成降级码——那会让一次 cancel 变成一条假的「模型失败」记录。"""
    class Cancelling:
        provider = "anthropic"

        async def stream(self, *, system, messages, tools):
            raise asyncio.CancelledError
            yield  # pragma: no cover —— 让它成为 async generator

    with pytest.raises(asyncio.CancelledError):
        await run_round(Cancelling(), system="s", user="u", tool_name=TOOL,
                        tool_description="d", tool_schema={}, timeout_s=5.0)
