"""标的市场情报层（ai-trade-market-intel）。

一个与交易 Agent **完全分开**的情报 Agent，为每一个正在 AI 自动交易的标的做网络检索、把检索到的
真实网页素材摘要成「几句话以内」的短条目、过六道真实性闸之后落进本地情报库；另一个整理轮定期
回看整座库，自主决定哪条从超短升进中期、哪条再升进长期、哪条是噪声该被合并或清理。

**包导出面**是本包对外的全部契约（`manager` / `session` / `api` / `prompts` 只经这里取用）。
包内模块之间的依赖是单向的：

    taxonomy ← gate ← disclosure
    taxonomy ← corpus ← prompts
    search / corpus / gate / prompts / agent → service → runtime → scheduler

`scheduler` 反向依赖 `runtime` 的注册表，故 `runtime.invalidate` 里那一处 import 是**延迟**的
（见该函数的 docstring）。
"""
from .disclosure import format_intel
from .gate import IntelItem, IntelSource
from .runtime import (
    IntelRuntime,
    build_group_runtime,
    intel_digest_for,
    intel_summary_for,
    invalidate,
    register,
    runtime_for,
    unregister,
)
from .service import IntelDeps, run_collect_round, run_curate_round
from .taxonomy import IntelLimits, limits_from_settings

__all__ = [
    "IntelDeps",
    "IntelItem",
    "IntelLimits",
    "IntelRuntime",
    "IntelSource",
    "build_group_runtime",
    "format_intel",
    "intel_digest_for",
    "intel_summary_for",
    "invalidate",
    "limits_from_settings",
    "register",
    "run_collect_round",
    "run_curate_round",
    "runtime_for",
    "unregister",
]
