"""P1-URL：client URL 构造单测——根 URL 追加 /v1/…；整端点（含资源段）原样直通。"""
from __future__ import annotations

from contextlib import asynccontextmanager

from app.agent.llm.anthropic_client import AnthropicClient
from app.agent.llm.openai_client import OpenAIClient
from app.agent.llm.base import LlmMessage, TextBlock


class _FakeResp:
    status_code = 200

    async def aiter_lines(self):
        return
        yield  # pragma: no cover —— 空 SSE，使解析器立即收尾

    async def aread(self) -> bytes:
        return b""


def _capture_url(client) -> dict:
    """把 client 的 _request_cm 替换为记录 url 的桩；返回捕获字典。"""
    captured: dict = {}

    @asynccontextmanager
    async def fake_cm(url, body):
        captured["url"] = url
        yield _FakeResp()

    client._request_cm = fake_cm
    return captured


async def _run(client) -> str:
    captured = _capture_url(client)
    async for _ in client.stream(system="s",
                                 messages=[LlmMessage("user", [TextBlock("hi")])], tools=[]):
        pass
    return captured["url"]


def _openai(base_url: str) -> OpenAIClient:
    return OpenAIClient(base_url=base_url, api_key="k", model="m", max_tokens=16,
                        temperature=1.0, top_p=0.95, timeout=5.0, proxy=None)


def _anthropic(base_url: str) -> AnthropicClient:
    return AnthropicClient(base_url=base_url, api_key="k", model="m",
                           anthropic_version="2023-06-01", user_agent="ua", max_tokens=16,
                           temperature=1.0, top_p=0.95, timeout=5.0, proxy=None)


async def test_openai_root_appends_v1_chat_completions():
    url = await _run(_openai("https://api.openai.com"))
    assert url == "https://api.openai.com/v1/chat/completions"


async def test_openai_full_endpoint_passthrough():
    full = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    url = await _run(_openai(full))
    assert url == full  # 整端点原样直通（Gemini OpenAI 兼容端点）


async def test_openai_custom_gateway_passthrough():
    full = "https://gw.example.com/proxy/chat/completions"
    assert await _run(_openai(full)) == full


async def test_anthropic_root_appends_v1_messages():
    url = await _run(_anthropic("https://api.anthropic.com"))
    assert url == "https://api.anthropic.com/v1/messages"


async def test_anthropic_kimi_root_appends_v1_messages():
    url = await _run(_anthropic("https://api.kimi.com/coding"))
    assert url == "https://api.kimi.com/coding/v1/messages"


async def test_anthropic_full_endpoint_passthrough():
    full = "https://relay.example.com/anthropic/v1/messages"
    assert await _run(_anthropic(full)) == full
