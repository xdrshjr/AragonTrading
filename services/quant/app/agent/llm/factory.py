"""按 api_standard / provider 实例化 LLMClient，注入凭证 / 代理 / 超时 (spec §2.2 / §2.3)。"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ...config import Settings
from .anthropic_client import AnthropicClient
from .base import LLMClient
from .openai_client import OpenAIClient

if TYPE_CHECKING:  # 仅类型注解需要；运行期延迟导入避免与 providers 的循环依赖。
    from ..providers import LlmProviderConfig


def make_llm_client_from_config(cfg: "LlmProviderConfig") -> LLMClient:
    """按 `cfg.api_standard` 造对应 client；未知标准抛 ValueError（边界一次性校验）。"""
    std = (cfg.api_standard or "anthropic").strip().lower()
    if std == "anthropic":
        return AnthropicClient(
            base_url=cfg.base_url, api_key=cfg.api_key, model=cfg.model,
            anthropic_version=cfg.anthropic_version, user_agent=cfg.user_agent,
            max_tokens=cfg.max_tokens, temperature=cfg.temperature, top_p=cfg.top_p,
            timeout=cfg.timeout, proxy=cfg.proxy, retry=cfg.retry,
            # 扩展思考预算（deep-reasoning-chain §3.3.2）：默认 0 ⇒ 请求体逐字节不变。
            thinking_budget_tokens=getattr(cfg, "thinking_budget_tokens", 0))
    if std == "openai":
        # OpenAI 标准**显式不接** thinking 预算：该标准无此概念，显式不透传比静默忽略诚实
        # （用例 B-4 冻结「构造不含该参数」这一点）。
        return OpenAIClient(
            base_url=cfg.base_url, api_key=cfg.api_key, model=cfg.model,
            max_tokens=cfg.max_tokens, temperature=cfg.temperature, top_p=cfg.top_p,
            timeout=cfg.timeout, proxy=cfg.proxy, retry=cfg.retry)
    raise ValueError(f"未知 api_standard: {std!r}（仅支持 anthropic | openai）")


def make_llm_client(settings: Settings) -> LLMClient:
    """既有签名（env 路径）：薄封装 `provider_config_from_settings`，行为向后兼容不变。"""
    from ..providers import provider_config_from_settings  # 延迟导入避免循环依赖
    return make_llm_client_from_config(provider_config_from_settings(settings))
