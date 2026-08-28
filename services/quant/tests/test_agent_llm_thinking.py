"""Anthropic 扩展思考开关的请求体单测（ai-trade-deep-reasoning-chain §7.1 G-4 / §7.4 B-3/B-4）。

根因 C-5：归一化层早就解析 `thinking_delta`、trace 有 `thinking` phase、前端有「深度思考」折叠块
——只是**请求体里从来没有人写过那个字段**。本文件守两件事：默认 0 时请求体逐字节不变（第二个
独立回退开关），以及开启时 Anthropic 的两条硬要求（temperature=1、无 top_p）被真的满足。
"""
from __future__ import annotations

from app.agent.llm.anthropic_client import AnthropicClient
from app.agent.llm.base import LlmMessage, TextBlock, ToolSchema
from app.agent.llm.factory import make_llm_client_from_config
from app.agent.llm.openai_client import OpenAIClient
from app.agent.llm.retry import RetryConfig
from app.agent.providers import LlmProviderConfig, provider_config_from_settings
from app.config import Settings

_MSGS = [LlmMessage("user", [TextBlock("hi")])]
_TOOLS = [ToolSchema(name="t", description="d", input_schema={"type": "object"})]


def _client(**kw) -> AnthropicClient:
    base = dict(base_url="https://x", api_key="k", model="m",
                anthropic_version="2023-06-01", user_agent="ua", max_tokens=2048,
                temperature=0.7, top_p=0.95, timeout=60.0, proxy=None, retry=RetryConfig())
    base.update(kw)
    return AnthropicClient(**base)


def test_thinking_budget_zero_keeps_request_body_identical():
    """G-4：默认 0 ⇒ 请求体与本特性合入前**逐键相等**，且没有多出来的 `thinking` 键。"""
    body = _client()._body("sys", _MSGS, _TOOLS)
    assert body == {
        "model": "m", "max_tokens": 2048, "system": "sys",
        "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
        "temperature": 0.7, "top_p": 0.95, "stream": True,
        "tools": [{"name": "t", "description": "d", "input_schema": {"type": "object"}}]}
    assert "thinking" not in body and "top_p" in body


def test_thinking_budget_forces_temperature_one_and_drops_top_p():
    """B-3：`thinking_budget>0` ⇒ body 含 `thinking`、`temperature == 1.0`、**无** `top_p`。

    两条都是 Anthropic 的硬要求；少做一条会得到一个必然 400 的请求体，而症状是「开了扩展思考
    之后每一轮都 llm_error」——那种形态在界面上看不出是配置问题。
    """
    body = _client(thinking_budget_tokens=4096)._body("sys", _MSGS, _TOOLS)
    assert body["thinking"] == {"type": "enabled", "budget_tokens": 4096}
    assert body["temperature"] == 1.0
    assert "top_p" not in body


def test_negative_or_none_budget_is_normalised_to_zero():
    """越界入参不该造出一个 `budget_tokens: -1` 的请求体。"""
    assert "thinking" not in _client(thinking_budget_tokens=-5)._body("sys", _MSGS, [])
    assert "thinking" not in _client(thinking_budget_tokens=0)._body("sys", _MSGS, [])


def test_thinking_budget_is_dropped_for_openai_standard():
    """B-4：openai 标准**显式不接**该参数——显式不透传比静默忽略诚实。"""
    cfg = LlmProviderConfig(
        provider_id=None, slug="openai", label="t", api_standard="openai",
        base_url="https://x", api_key="k", model="m", user_agent="ua", max_tokens=2048,
        temperature=0.7, top_p=0.95, timeout=60.0, anthropic_version="2023-06-01",
        proxy=None, retry=RetryConfig(), thinking_budget_tokens=4096)
    client = make_llm_client_from_config(cfg)
    assert isinstance(client, OpenAIClient)
    body = client._body("sys", _MSGS, _TOOLS)
    assert "thinking" not in body and body["top_p"] == 0.95


def test_factory_passes_the_budget_through_for_anthropic():
    """工厂必须真的透传：不透传的话上游拆预算全白做，而**不报任何错**。"""
    cfg = LlmProviderConfig(
        provider_id=None, slug="anthropic", label="t", api_standard="anthropic",
        base_url="https://x", api_key="k", model="m", user_agent="ua", max_tokens=8192,
        temperature=0.7, top_p=0.95, timeout=60.0, anthropic_version="2023-06-01",
        proxy=None, retry=RetryConfig(), thinking_budget_tokens=4096)
    body = make_llm_client_from_config(cfg)._body("sys", _MSGS, [])
    assert body["thinking"]["budget_tokens"] == 4096


def test_provider_config_defaults_to_zero_budget():
    """`provider_config_from_settings` 的默认值 = 0 ⇒ 全仓零行为变化。"""
    assert provider_config_from_settings(Settings()).thinking_budget_tokens == 0
    cfg = provider_config_from_settings(Settings(agent_thinking_budget_tokens=2048))
    assert cfg.thinking_budget_tokens == 2048
