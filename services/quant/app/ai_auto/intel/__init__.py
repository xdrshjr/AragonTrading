"""标的市场情报层（ai-trade-market-intel）。

一个与交易 Agent **完全分开**的情报 Agent，为每一个正在 AI 自动交易的标的做网络检索、把检索到的
真实网页素材摘要成「几句话以内」的短条目、过六道真实性闸之后落进本地情报库；另一个整理轮定期
回看整座库，自主决定哪条从超短升进中期、哪条再升进长期、哪条是噪声该被合并或清理。

**包导出面**是本包对外的全部契约（`manager` / `session` / `api` / `prompts` 只经这里取用）。
包内模块之间的依赖是单向的：

    taxonomy ← gate ← disclosure
    taxonomy ← gate ← cleanup
    taxonomy ← quotes ← gate
    taxonomy ← corpus ← prompts
    extract ← reader（reader 是本包**唯一**出网抓取的模块）
    search / corpus / gate / prompts / agent → service → runtime → scheduler

`scheduler` 反向依赖 `runtime` 的注册表，故 `runtime.invalidate` 里那一处 import 是**延迟**的
（见该函数的 docstring）。
"""
from .alert import AlertCandidate, AlertDecision, AlertHandoff, plan_alert
from .attribution import IntelAttribution
from .binding import (
    IntelBinding,
    alert_pending_ttl_s,
    attribution_enabled,
    binding_for,
    binding_wire,
    disclosure_stats,
    intel_limits_for,
    last_round_wire,
    new_disclosure_counters,
    note_disclosure,
)
from .cleanup import CleanupPlan, plan_cleanup
from .conflict import ConflictPair, find_conflicts
from .citation import CitationResolution, classify_outcome, resolve_citations
from .disclosure import DisclosurePlan, format_intel, intel_tier, plan_disclosure
from .expand import ExpandItem, format_expand, inject_intel_expand, plan_expand
from .efficacy import Efficacy, Stat, accuracy, solve
from .extract import ExtractResult, extract_main_text
from .gate import IntelItem, IntelSource
from .quotes import VerifiedQuote, normalize_for_match, verify_item_quotes
from .reader import ReaderConfig, ReaderResult, fetch_many, guard_url
from .runtime import (
    IntelRuntime,
    build_group_runtime,
    intel_digest_for,
    intel_summary_for,
    invalidate,
    note_alert_outcome,
    register,
    runtime_for,
    unregister,
)
from .service import IntelDeps, run_collect_round, run_curate_round
from .session_rule import (
    INTEL_MODES,
    REQUESTABLE_MODES,
    IntelSessionRule,
    resolve_session_rule,
)
from .service_verify import run_verify_round
from .taxonomy import IntelLimits, limits_from_settings

__all__ = [
    "INTEL_MODES",
    "REQUESTABLE_MODES",
    "AlertCandidate",
    "AlertDecision",
    "AlertHandoff",
    "CitationResolution",
    "CleanupPlan",
    "ConflictPair",
    "DisclosurePlan",
    "Efficacy",
    "ExpandItem",
    "ExtractResult",
    "IntelAttribution",
    "IntelBinding",
    "IntelDeps",
    "IntelItem",
    "IntelLimits",
    "IntelRuntime",
    "IntelSessionRule",
    "IntelSource",
    "ReaderConfig",
    "ReaderResult",
    "Stat",
    "VerifiedQuote",
    "binding_for",
    "binding_wire",
    "build_group_runtime",
    "disclosure_stats",
    "extract_main_text",
    "fetch_many",
    "guard_url",
    "accuracy",
    "alert_pending_ttl_s",
    "attribution_enabled",
    "classify_outcome",
    "find_conflicts",
    "format_expand",
    "format_intel",
    "inject_intel_expand",
    "intel_digest_for",
    "intel_limits_for",
    "intel_summary_for",
    "intel_tier",
    "invalidate",
    "last_round_wire",
    "limits_from_settings",
    "new_disclosure_counters",
    "normalize_for_match",
    "note_alert_outcome",
    "note_disclosure",
    "plan_alert",
    "plan_cleanup",
    "plan_disclosure",
    "plan_expand",
    "register",
    "resolve_citations",
    "resolve_session_rule",
    "run_collect_round",
    "run_curate_round",
    "run_verify_round",
    "runtime_for",
    "solve",
    "unregister",
    "verify_item_quotes",
]
