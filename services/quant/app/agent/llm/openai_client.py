"""OpenAI Chat Completions API 适配（httpx 直连，SSE 解析）(spec §2.2)。

为 OpenAI 兼容厂商（DeepSeek / 本地 vLLM 等）保留；KIMI Coding 端点拒绝本格式（403，P2-8），
故本路径以离线 SSE 样本单测验证（code-complete + unit-tested），不依赖 live 实证。
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx

from .base import (
    LlmMessage,
    StreamEvent,
    TextBlock,
    ToolResultBlock,
    ToolSchema,
    ToolUseBlock,
)


class OpenAIClient:
    """OpenAI `/v1/chat/completions` 流式客户端。"""

    def __init__(self, *, base_url: str, api_key: str, model: str, max_tokens: int,
                 temperature: float, top_p: float, timeout: float, proxy: str | None):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._top_p = top_p
        self._timeout = timeout
        self._proxy = proxy

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }

    def _body(self, system: str, messages: list[LlmMessage],
              tools: list[ToolSchema]) -> dict:
        msgs: list[dict] = [{"role": "system", "content": system}]
        for m in messages:
            msgs.extend(_to_openai_messages(m))
        body = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": msgs,
            "temperature": self._temperature,
            "top_p": self._top_p,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            body["tools"] = [
                {"type": "function",
                 "function": {"name": t.name, "description": t.description,
                              "parameters": t.input_schema}}
                for t in tools
            ]
            body["tool_choice"] = "auto"
        return body

    async def stream(self, *, system: str, messages: list[LlmMessage],
                     tools: list[ToolSchema]) -> AsyncIterator[StreamEvent]:
        url = f"{self._base_url}/v1/chat/completions"
        body = self._body(system, messages, tools)
        try:
            async with httpx.AsyncClient(timeout=self._timeout, proxy=self._proxy) as client:
                async with client.stream("POST", url, headers=self._headers(), json=body) as resp:
                    if resp.status_code >= 400:
                        text = (await resp.aread()).decode("utf-8", "replace")
                        yield StreamEvent(kind="error", code="LLM_HTTP_ERROR",
                                          message=f"HTTP {resp.status_code}: {text[:400]}")
                        return
                    async for event in parse_openai_sse(resp.aiter_lines()):
                        yield event
        except httpx.HTTPError as exc:
            yield StreamEvent(kind="error", code="LLM_NETWORK_ERROR", message=str(exc))


def _to_openai_messages(msg: LlmMessage) -> list[dict]:
    """归一化消息 → OpenAI 消息（tool_result 还原为独立 role:"tool" 消息，P1-B）。"""
    if msg.role == "tool":
        return [
            {"role": "tool", "tool_call_id": b.tool_use_id, "content": b.content}
            for b in msg.content if isinstance(b, ToolResultBlock)
        ]
    text = "".join(b.text for b in msg.content if isinstance(b, TextBlock))
    tool_calls = [
        {"id": b.id, "type": "function",
         "function": {"name": b.name, "arguments": json.dumps(b.input, ensure_ascii=False)}}
        for b in msg.content if isinstance(b, ToolUseBlock)
    ]
    out: dict = {"role": msg.role, "content": text or None}
    if tool_calls:
        out["tool_calls"] = tool_calls
    return [out]


async def parse_openai_sse(lines: AsyncIterator[str]) -> AsyncIterator[StreamEvent]:
    """逐行解析 OpenAI SSE → 归一化事件（含分片 tool_calls 累积）。"""
    started_tools: set[int] = set()
    async for line in lines:
        if not line or not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue
        for event in _dispatch_chunk(chunk, started_tools):
            yield event


def _dispatch_chunk(chunk: dict, started_tools: set[int]) -> list[StreamEvent]:
    events: list[StreamEvent] = []
    choices = chunk.get("choices") or []
    if not choices:
        if chunk.get("usage"):
            events.append(StreamEvent(kind="message_done", stop_reason=None,
                                      usage=chunk.get("usage")))
        return events
    choice = choices[0]
    delta = choice.get("delta") or {}
    if delta.get("content"):
        events.append(StreamEvent(kind="text_delta", text=delta["content"]))
    for call in delta.get("tool_calls") or []:
        events.extend(_on_tool_call(call, started_tools))
    if choice.get("finish_reason"):
        events.append(StreamEvent(kind="message_done",
                                  stop_reason=choice["finish_reason"], usage=chunk.get("usage")))
    return events


def _on_tool_call(call: dict, started_tools: set[int]) -> list[StreamEvent]:
    idx = call.get("index", 0)
    fn = call.get("function") or {}
    events: list[StreamEvent] = []
    if idx not in started_tools and call.get("id"):
        started_tools.add(idx)
        events.append(StreamEvent(kind="tool_use_start", id=call.get("id"), name=fn.get("name")))
    if fn.get("arguments"):
        events.append(StreamEvent(kind="tool_use_args_delta", id=call.get("id"),
                                  partial_json=fn["arguments"]))
    return events
