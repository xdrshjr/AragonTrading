"""OpenAI SSE 归一化解析单测（离线样本字节流，不依赖网络）。"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator

from app.agent.llm.openai_client import parse_openai_sse

# 含 content + 分片 tool_calls.arguments + finish_reason + 末尾 usage 的 OpenAI SSE 样本。
_SAMPLE = """\
data: {"choices":[{"index":0,"delta":{"content":"好的"}}]}

data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"run_backtest","arguments":""}}]}}]}

data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"strategyId\\":1,"}}]}}]}

data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\"instId\\":\\"BTC-USDT-SWAP\\"}"}}]}}]}

data: {"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}

data: {"choices":[],"usage":{"completion_tokens":12}}

data: [DONE]
"""


async def _alines(text: str) -> AsyncIterator[str]:
    for line in text.split("\n"):
        yield line


async def _collect(text: str):
    return [ev async for ev in parse_openai_sse(_alines(text))]


async def test_parses_content_and_fragmented_tool_calls():
    events = await _collect(_SAMPLE)
    assert any(e.kind == "text_delta" and e.text == "好的" for e in events)
    start = next(e for e in events if e.kind == "tool_use_start")
    assert start.id == "call_1" and start.name == "run_backtest"
    args_text = "".join(e.partial_json for e in events if e.kind == "tool_use_args_delta")
    assert json.loads(args_text) == {"strategyId": 1, "instId": "BTC-USDT-SWAP"}


async def test_finish_reason_maps_to_message_done():
    events = await _collect(_SAMPLE)
    stops = [e for e in events if e.kind == "message_done"]
    assert any(e.stop_reason == "tool_calls" for e in stops)
    # 末尾 usage-only chunk 也产出一个 message_done，携带 usage。
    assert any(e.usage == {"completion_tokens": 12} for e in stops)
