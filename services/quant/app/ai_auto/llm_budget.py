"""AI 自动交易的三份 LLM 预算（ai-trade-deep-reasoning-chain 轴 C / §3.3.1）。

根因 C-1：`agent_max_tokens = 2048` 是**整个后端共用的一个上限**——决策请求的「研判文字 +
工具调用 JSON」必须一起挤进这 2048 个输出 token，模型即使想深想，物理上也没有地方写。

本模块按**调用点**把预算拆成三份（决策 / 反思 / 摘要），实现方式是 `dataclasses.replace`
造三份 `LlmProviderConfig` 再各调 `make_llm_client_from_config`。

**刻意不改 `LLMClient.stream` 的签名**（R-9）：`stream` 是 Protocol，本仓库有 32 个测试文件
实现了 `async def stream(self, *, system, messages, tools)` 的假客户端，给它加一个生产上恒会
传的关键字参数会一次性打翻它们全部。改构造参数则零波及（用例 G-5 冻结这一取舍）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, is_dataclass, replace
from typing import TYPE_CHECKING

from ..agent.llm.base import LLMClient
from ..agent.llm.factory import make_llm_client_from_config
from .reasoning_protocol import DepthProfile

if TYPE_CHECKING:                       # 仅类型标注；运行期延迟导入避免与 providers 的循环依赖
    from ..agent.providers import LlmProviderConfig
    from ..config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AiTradeLlmClients:
    """AI 自动交易三个调用点各自的 LLM 客户端（预算互不串味）。"""

    decision: LLMClient
    reflection: LLMClient
    summary: LLMClient


def resolve_thinking_budget(cfg: "LlmProviderConfig", settings: "Settings", *,
                            profile: DepthProfile) -> int:
    """决策客户端实际生效的扩展思考预算（不合法一律降为 0 并告警，绝不发必然 400 的请求体）。

    三条门（任一不过即 0）：
      1. `settings.agent_thinking_budget_tokens > 0`（默认 0 ⇒ 请求体逐字节不变）；
      2. `cfg.api_standard == "anthropic"`（openai 标准无此概念，factory 显式不透传）；
      3. `decision_max_tokens > thinking + reserve`——思考预算吃掉整个输出预算之后模型没有
         地方写工具调用，那是一个每轮必然截断、界面上却看不出原因的形态（R-1）。
    """
    budget = max(0, int(getattr(settings, "agent_thinking_budget_tokens", 0) or 0))
    if budget <= 0 or not profile.enabled:
        return 0
    if (cfg.api_standard or "anthropic").strip().lower() != "anthropic":
        logger.info("ai-trade thinking budget dropped: api_standard=%s 不支持扩展思考",
                    cfg.api_standard)
        return 0
    reserve = int(settings.ai_trade_reasoning_reserve_tokens)
    if profile.decision_max_tokens <= budget + reserve:
        logger.warning(
            "ai-trade thinking budget %d 与收尾预留 %d 之和已达决策输出预算 %d，已降为 0"
            "（否则每轮必然在调用决策工具之前耗尽预算）",
            budget, reserve, profile.decision_max_tokens)
        return 0
    return budget


def build_ai_trade_llm_clients(cfg: "LlmProviderConfig", settings: "Settings", *,
                               profile: DepthProfile,
                               make_client=None) -> AiTradeLlmClients:
    """按深度档给三个调用点各造一个 client。

    Args:
        cfg: 当前生效的 provider 配置（`resolve_active_provider` / `spec.provider` 的产物）。
        settings: 会话生效的 `Settings`（读摘要预算与扩展思考预算两项）。
        profile: 本会话的深度档（`resolve_depth_profile` 的产物）。
        make_client: 造 client 的工厂（keyword-only，默认 `make_llm_client_from_config`）。
            **调用方必须传自己模块里的那一份**：本仓库有 20+ 个测试文件以
            `monkeypatch.setattr("app.ai_auto.manager.make_llm_client_from_config", …)` 注入假
            客户端，把工厂换到本模块内解析会一次性打翻它们全部，而症状是「装配点起不来」而不是
            「预算没生效」——那是一条与本特性完全无关的红。

    Returns:
        三个客户端。`profile.enabled == False`（off 档）⇒ 三者都用**原 `cfg`**，行为与今日的
        单 client 逐字一致（三次 `make_llm_client_from_config(cfg)` 与一次的行为相同）。
    """
    factory = make_client or make_llm_client_from_config
    if not profile.enabled or not is_dataclass(cfg):
        # `not is_dataclass(cfg)`：`dataclasses.replace` 只接受 dataclass 实例，而本仓库有 30+ 个
        # 用例以裸 `object()` 当 provider 配置（它们只关心「装配起得来」）。此时**不可能**拆预算，
        # 故三者同用原 cfg——与今日的单 client 行为逐字一致。生产路径恒是 `LlmProviderConfig`
        # （`resolve_active_provider` / `spec.provider`），故这条分支只在测试替身上走到。
        return AiTradeLlmClients(decision=factory(cfg), reflection=factory(cfg),
                                 summary=factory(cfg))
    thinking = resolve_thinking_budget(cfg, settings, profile=profile)
    decision_cfg = replace(cfg, max_tokens=profile.decision_max_tokens,
                           thinking_budget_tokens=thinking)
    # 反思与摘要**不开**扩展思考：反思已有自己的 7 阶段协议，摘要只是压缩历史流水——给它们
    # 开思考只会白烧 token，且 `_stream_reflection` 把 thinking 与 text 合喂同一个 phase（P2-3）。
    reflection_cfg = replace(cfg, max_tokens=profile.reflection_max_tokens,
                             thinking_budget_tokens=0)
    summary_cfg = replace(cfg, max_tokens=int(settings.ai_trade_summary_max_tokens),
                          thinking_budget_tokens=0)
    return AiTradeLlmClients(decision=factory(decision_cfg),
                             reflection=factory(reflection_cfg),
                             summary=factory(summary_cfg))
