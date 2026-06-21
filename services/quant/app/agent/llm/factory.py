"""按 `settings.agent_provider` 实例化 LLMClient，注入凭证 / 代理 / 超时 (spec §2.2)。"""
from __future__ import annotations

from ...config import Settings
from .anthropic_client import AnthropicClient
from .base import LLMClient
from .openai_client import OpenAIClient
from .retry import RetryConfig


def _retry_config(settings: Settings) -> RetryConfig:
    """从 AGENT_* 设置构造 pre-stream 重试参数（H1）。"""
    return RetryConfig(
        max_retries=settings.agent_max_retries,
        base_ms=settings.agent_retry_base_ms,
        max_ms=settings.agent_retry_max_ms,
    )


def make_llm_client(settings: Settings) -> LLMClient:
    """造对应 provider 的客户端；未知 provider 抛 ValueError（边界一次性校验）。"""
    provider = (settings.agent_provider or "anthropic").strip().lower()
    retry = _retry_config(settings)
    if provider == "anthropic":
        return AnthropicClient(
            base_url=settings.agent_base_url,
            api_key=settings.agent_api_key,
            model=settings.agent_model,
            anthropic_version=settings.agent_anthropic_version,
            user_agent=settings.agent_user_agent,
            max_tokens=settings.agent_max_tokens,
            temperature=settings.agent_temperature,
            top_p=settings.agent_top_p,
            timeout=settings.agent_timeout_seconds,
            proxy=settings.agent_proxy_url,
            retry=retry,
        )
    if provider == "openai":
        return OpenAIClient(
            base_url=settings.agent_base_url,
            api_key=settings.agent_api_key,
            model=settings.agent_model,
            max_tokens=settings.agent_max_tokens,
            temperature=settings.agent_temperature,
            top_p=settings.agent_top_p,
            timeout=settings.agent_timeout_seconds,
            proxy=settings.agent_proxy_url,
            retry=retry,
        )
    raise ValueError(f"未知 agent_provider: {provider!r}（仅支持 anthropic | openai）")
