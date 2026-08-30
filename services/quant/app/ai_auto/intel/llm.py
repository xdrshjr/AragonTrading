"""情报 Agent 的独立 LLM 预算（第四份 client，ai-trade-market-intel §6.1 #8）。

`llm_budget.py` 已经把 AI 自动交易的输出预算拆成决策 / 反思 / 摘要三份；情报是**第四个调用点**，
它既不该抢决策的预算，也不该被 `agent_max_tokens` 那个全后端共用的上限压住。

**刻意不改 `LLMClient.stream` 的签名**（E-4）：`stream` 是 Protocol，本仓库有 32 个测试文件实现了
`async def stream(self, *, system, messages, tools)` 的假客户端，给它加一个生产上恒会传的关键字
参数会一次性打翻它们全部。改构造参数则零波及。

`make_client` 是 **keyword-only** 形参且**调用方必须传自己模块里的那一份**：本仓库有 20+ 个测试
文件以 `monkeypatch.setattr("app.ai_auto.manager.make_llm_client_from_config", …)` 注入假客户端，
把工厂换到本模块内解析会一次性打翻它们全部，而症状是「装配点起不来」而不是「预算没生效」。
"""
from __future__ import annotations

from dataclasses import is_dataclass, replace
from typing import TYPE_CHECKING

from ...agent.llm.base import LLMClient
from ...agent.llm.factory import make_llm_client_from_config

if TYPE_CHECKING:                       # 仅类型标注；运行期延迟导入避免与 providers 的循环依赖
    from ...agent.providers import LlmProviderConfig
    from ...config import Settings


def build_intel_llm_client(cfg: "LlmProviderConfig", settings: "Settings", *,
                           make_client=None,
                           max_tokens_override: int | None = None) -> LLMClient:
    """按情报专用预算造一个 client。

    Args:
        cfg: 当前生效的 provider 配置（`resolve_active_provider` 的产物）。
        settings: 读 `ai_trade_intel_max_tokens`。
        make_client: 造 client 的工厂（keyword-only，默认 `make_llm_client_from_config`）。
        max_tokens_override: 覆盖输出预算（intel-closed-loop 评审 P1-3，keyword-only）。
            `None`（默认）⇒ **今日行为逐字节不变**、既有调用点零改动。核查轮的
            `ai_trade_intel_verify_max_tokens` 只能走这条路——`run_round(...)` 没有 `max_tokens`
            形参，而 `LLMClient.stream` 是 Protocol、32 个测试文件实现了它，给它加参数会一次性
            打翻它们全部（见模块 docstring）。

    Returns:
        输出预算为 `ai_trade_intel_max_tokens`、**不开扩展思考**的客户端。情报轮要的是提炼与
        引用核对，不是长链推理；给它开思考只会白烧 token。

        `cfg` 不是 dataclass 时（本仓库有 30+ 个用例以裸 `object()` 当 provider 配置）原样直通，
        与今日单 client 行为逐字一致。
    """
    factory = make_client or make_llm_client_from_config
    if not is_dataclass(cfg):
        return factory(cfg)
    budget = int(max_tokens_override if max_tokens_override is not None
                 else settings.ai_trade_intel_max_tokens)
    return factory(replace(cfg, max_tokens=budget, thinking_budget_tokens=0))
