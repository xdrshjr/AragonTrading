"""Anthropic Messages API 适配（httpx 直连，SSE 解析，KIMI UA）(spec §2.2)。

把归一化 `LlmMessage`/`ToolSchema` 转成 Anthropic 请求体，把原生 SSE 翻译成归一化
`StreamEvent`。KIMI Coding 端点（默认 provider）仅支持本标准并校验 `User-Agent`。
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


class AnthropicClient:
    """Anthropic `/v1/messages` 流式客户端。"""

    def __init__(self, *, base_url: str, api_key: str, model: str,
                 anthropic_version: str, user_agent: str, max_tokens: int,
                 temperature: float, top_p: float, timeout: float,
                 proxy: str | None):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._version = anthropic_version
        self._user_agent = user_agent
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._top_p = top_p
        self._timeout = timeout
        self._proxy = proxy

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "x-api-key": self._api_key,
            "anthropic-version": self._version,
            "User-Agent": self._user_agent,
        }

    def _body(self, system: str, messages: list[LlmMessage],
              tools: list[ToolSchema]) -> dict:
        body = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "system": system,
            "messages": [_to_anthropic_message(m) for m in messages],
            "temperature": self._temperature,
            "top_p": self._top_p,
            "stream": True,
        }
        if tools:
            body["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema}
                for t in tools
            ]
        return body

    async def stream(self, *, system: str, messages: list[LlmMessage],
                     tools: list[ToolSchema]) -> AsyncIterator[StreamEvent]:
        url = f"{self._base_url}/v1/messages"
        body = self._body(system, messages, tools)
        try:
            async with httpx.AsyncClient(timeout=self._timeout, proxy=self._proxy) as client:
                async with client.stream("POST", url, headers=self._headers(), json=body) as resp:
                    if resp.status_code >= 400:
                        text = (await resp.aread()).decode("utf-8", "replace")
                        yield StreamEvent(kind="error", code="LLM_HTTP_ERROR",
                                          message=f"HTTP {resp.status_code}: {text[:400]}")
                        return
                    async for event in parse_anthropic_sse(resp.aiter_lines()):
                        yield event
        except httpx.HTTPError as exc:
            yield StreamEvent(kind="error", code="LLM_NETWORK_ERROR", message=str(exc))


def _to_anthropic_message(msg: LlmMessage) -> dict:
    """归一化消息 → Anthropic 消息。tool 角色的 tool_result 装入 user 轮（P1-B）。"""
    if msg.role == "tool":
        content = [_tool_result_block(b) for b in msg.content if isinstance(b, ToolResultBlock)]
        return {"role": "user", "content": content}
    content = [_content_block(b) for b in msg.content]
    return {"role": msg.role, "content": content}


def _content_block(block) -> dict:
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    if isinstance(block, ToolUseBlock):
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    if isinstance(block, ToolResultBlock):
        return _tool_result_block(block)
    raise TypeError(f"unknown block: {block!r}")


def _tool_result_block(block: ToolResultBlock) -> dict:
    return {
        "type": "tool_result",
        "tool_use_id": block.tool_use_id,
        "content": block.content,
        "is_error": block.is_error,
    }


async def parse_anthropic_sse(lines: AsyncIterator[str]) -> AsyncIterator[StreamEvent]:
    """逐行解析 Anthropic SSE → 归一化事件；`data:` 帧含 `type` 字段，按之分派。"""
    index_to_tool_id: dict[int, str] = {}
    async for line in lines:
        if not line or not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if not payload:
            continue
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue
        for event in _dispatch_chunk(chunk, index_to_tool_id):
            yield event


def _dispatch_chunk(chunk: dict, index_to_tool_id: dict[int, str]) -> list[StreamEvent]:
    ctype = chunk.get("type")
    if ctype == "content_block_start":
        return _on_block_start(chunk, index_to_tool_id)
    if ctype == "content_block_delta":
        return _on_block_delta(chunk, index_to_tool_id)
    if ctype == "content_block_stop":
        tid = index_to_tool_id.get(chunk.get("index"))
        return [StreamEvent(kind="tool_use_end", id=tid)] if tid else []
    if ctype == "message_delta":
        delta = chunk.get("delta") or {}
        return [StreamEvent(kind="message_done", stop_reason=delta.get("stop_reason"),
                            usage=chunk.get("usage"))]
    if ctype == "message_stop":
        return []
    if ctype == "error":
        err = chunk.get("error") or {}
        return [StreamEvent(kind="error", code=err.get("type", "LLM_ERROR"),
                            message=err.get("message", "stream error"))]
    return []


def _on_block_start(chunk: dict, index_to_tool_id: dict[int, str]) -> list[StreamEvent]:
    block = chunk.get("content_block") or {}
    if block.get("type") == "tool_use":
        index_to_tool_id[chunk.get("index")] = block.get("id")
        return [StreamEvent(kind="tool_use_start", id=block.get("id"), name=block.get("name"))]
    return []


def _on_block_delta(chunk: dict, index_to_tool_id: dict[int, str]) -> list[StreamEvent]:
    delta = chunk.get("delta") or {}
    dtype = delta.get("type")
    if dtype == "text_delta":
        return [StreamEvent(kind="text_delta", text=delta.get("text", ""))]
    if dtype == "thinking_delta":
        return [StreamEvent(kind="thinking_delta", text=delta.get("thinking", ""))]
    if dtype == "input_json_delta":
        tid = index_to_tool_id.get(chunk.get("index"))
        return [StreamEvent(kind="tool_use_args_delta", id=tid,
                            partial_json=delta.get("partial_json", ""))]
    return []
