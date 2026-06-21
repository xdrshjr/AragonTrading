"""Anthropic SSE 归一化解析单测（离线样本字节流，不依赖网络）。"""
from __future__ import annotations

from collections.abc import AsyncIterator

from app.agent.llm.anthropic_client import parse_anthropic_sse

# 含 text + tool_use + 分片 input_json_delta + message_delta(stop_reason+usage) 的 Anthropic SSE 样本。
_SAMPLE = """\
event: content_block_start
data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}

event: content_block_delta
data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"画出"}}

event: content_block_stop
data: {"type":"content_block_stop","index":0}

event: content_block_start
data: {"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"toolu_1","name":"show_chart","input":{}}}

event: content_block_delta
data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{\\"instId\\":"}}

event: content_block_delta
data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"\\"BTC-USDT-SWAP\\"}"}}

event: content_block_stop
data: {"type":"content_block_stop","index":1}

event: message_delta
data: {"type":"message_delta","delta":{"stop_reason":"tool_use"},"usage":{"output_tokens":21}}

event: message_stop
data: {"type":"message_stop"}
"""


async def _alines(text: str) -> AsyncIterator[str]:
    for line in text.split("\n"):
        yield line


async def _collect(text: str):
    return [ev async for ev in parse_anthropic_sse(_alines(text))]


async def test_parses_text_and_tool_use_stream():
    events = await _collect(_SAMPLE)
    kinds = [e.kind for e in events]
    assert kinds == [
        "text_delta",
        "tool_use_start",
        "tool_use_args_delta",
        "tool_use_args_delta",
        "tool_use_end",
        "message_done",
    ]


async def test_tool_use_args_concatenate_to_valid_json():
    import json

    events = await _collect(_SAMPLE)
    start = next(e for e in events if e.kind == "tool_use_start")
    assert start.id == "toolu_1" and start.name == "show_chart"
    args_text = "".join(e.partial_json for e in events if e.kind == "tool_use_args_delta")
    assert json.loads(args_text) == {"instId": "BTC-USDT-SWAP"}


async def test_message_done_carries_stop_reason_and_usage():
    events = await _collect(_SAMPLE)
    done = next(e for e in events if e.kind == "message_done")
    assert done.stop_reason == "tool_use"
    assert done.usage == {"output_tokens": 21}
