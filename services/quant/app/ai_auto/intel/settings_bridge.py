"""情报设置的库内行 ⇄ `Settings` 桥（ai-trade-market-intel §3.2.3 起，历轮共同维护）。

**本模块是从 `service.py` 整段搬过来的，零语义变化**：actionability 轮给 `service.py` 加了告警
写段、战绩求解与矛盾检测之后，该文件实测越过 CLAUDE.md 的 800 行阈值，而 CLAUDE.md 对越线的
处置是「超了就再下沉一层」而不是放宽阈值（spec §十二 已把这条处置写死，`EG-I2` 是同形先例）。
搬家的方向是**单向**的——本模块不 import `service`，`service` 重新导出本模块的四个公开名，故
既有的 `intel_service.INTEL_SETTING_BRIDGE` / `intel_service.resolve_intel_settings` 调用点
**一个字节不改**。

⚠️ **这张表是本仓库最容易做成「假开关」的地方**（历轮三次前科）：一把露在
`PUT /settings/intel` 上却没进这张表的键 ⇒ 请求 200、面板上开关照动、`Settings` 上的值**永远
不变**，而且不报错。把安全开关做成假开关是最不可接受的一种失败。
"""
from __future__ import annotations

from ...db import repositories as repo
from . import taxonomy as tax

# ---------------- 设置解析（DB 行优先、env 回落，与 `resolve_active_provider` 同构） ----------------
# `(wire 键, app_settings 键, Settings 字段, 类型)`。**单一真源**：读端点、写端点与运行时装配点
# 共用它，避免「面板上改得动、系统永远不读、而且不报错」这类假面板（experience 的 I-10 教训）。
INTEL_SETTING_BRIDGE: tuple[tuple[str, str, str, type], ...] = (
    ("enabled", "ai_intel.enabled", "ai_trade_intel_enabled", bool),
    ("injectEnabled", "ai_intel.inject", "ai_trade_intel_inject", bool),
    ("provider", "ai_intel.provider", "ai_trade_intel_search_provider", str),
    ("collectIntervalMin", "ai_intel.collect_interval_min",
     "ai_trade_intel_collect_interval_min", float),
    ("curateEveryNCollects", "ai_intel.curate_every_n",
     "ai_trade_intel_curate_every_n_collects", int),
    ("dailyCap", "ai_intel.daily_cap", "ai_trade_intel_daily_round_cap", int),
    # ---- closed-loop 评审 P0-5：三把安全单键 + 五个核查旋钮**必须**扩这张表。
    # 不扩 ⇒ 请求 200、面板上开关照动、`Settings` 上的值**永远不变**——用户在设置卡上关掉核查轮，
    # 系统照跑照降级。把安全开关做成假开关是本轮最不可接受的一种失败（G-18 逐键往返冻结）。
    ("citeEnabled", "ai_intel.cite_enabled", "ai_trade_intel_cite_enabled", bool),
    ("attributionEnabled", "ai_intel.attribution_enabled",
     "ai_trade_intel_attribution_enabled", bool),
    ("verifyEnabled", "ai_intel.verify_enabled", "ai_trade_intel_verify_enabled", bool),
    ("verifyEveryNCurates", "ai_intel.verify_every_n",
     "ai_trade_intel_verify_every_n_curates", int),
    ("verifyMaxItems", "ai_intel.verify_max_items", "ai_trade_intel_verify_max_items", int),
    ("verifyDocsPerItem", "ai_intel.verify_docs_per_item",
     "ai_trade_intel_verify_docs_per_item", int),
    ("blockedSweepEnabled", "ai_intel.blocked_sweep",
     "ai_trade_intel_blocked_sweep_enabled", bool),
    ("citeMaxRefs", "ai_intel.cite_max_refs", "ai_trade_intel_cite_max_refs", int),
    # ---- evidence-grounding：轴 A / 轴 B 的三把单键。**不扩这张表 ⇒ 假面板**（E-14）：
    # 请求 200、开关照动、`Settings` 上的值永远不变，而且不报错。
    ("readerEnabled", "ai_intel.reader_enabled", "ai_trade_intel_reader_enabled", bool),
    ("readerTopK", "ai_intel.reader_top_k", "ai_trade_intel_reader_top_k", int),
    ("quoteMode", "ai_intel.quote_mode", "ai_trade_intel_quote_mode", str),
    # ---- ai-trade-intel-actionability：三把总闸 + 三个告警旋钮。**不扩这张表 ⇒ 假面板**：
    # 请求 200、开关照动、`Settings` 上的值永远不变，而且不报错（G-16 逐键往返冻结）。
    ("alertEnabled", "ai_intel.alert_enabled", "ai_trade_intel_alert_enabled", bool),
    ("alertMinImpact", "ai_intel.alert_min_impact", "ai_trade_intel_alert_min_impact", str),
    ("alertCooldownMin", "ai_intel.alert_cooldown_min",
     "ai_trade_intel_alert_cooldown_min", float),
    ("alertDailyCap", "ai_intel.alert_daily_cap", "ai_trade_intel_alert_daily_cap", int),
    ("efficacyEnabled", "ai_intel.efficacy_enabled", "ai_trade_intel_efficacy_enabled", bool),
    ("conflictEnabled", "ai_intel.conflict_enabled", "ai_trade_intel_conflict_enabled", bool),
)
# 密钥的 app_settings 键。**绝不出接口**（`GET /settings/intel` 只回 `hasKey` / `keyPreview`）、
# **绝不进日志**（`IntelSearchError` 构造时即脱敏）。
INTEL_API_KEY_APP_KEY = "ai_intel.search_api_key"


async def resolve_intel_settings(session, settings):
    """把库内行叠加到 `Settings` 上，产出**运行时真正会读到的那一份**。

    产出会话私有副本而不是改全局：`get_settings()` 是进程级 `lru_cache` 单例，且 `LiveExecutor`
    被 `ai_auto` 与 `live_auto` 共用同一个 settings 对象，`setattr` 会让情报设置静默溢出到非 AI
    链路（本仓库的历史坑）。
    """
    updates: dict = {}
    for _wire_key, app_key, field_name, caster in INTEL_SETTING_BRIDGE:
        stored = await repo.get_app_setting(session, app_key, None)
        if stored is None:
            continue
        try:
            value = bool(stored) if caster is bool else caster(stored)
        except (TypeError, ValueError):
            continue
        # ⚠️ `settings.model_copy(update=…)` **不做校验**（评审 P2-9）：一个从库内行读回来的坏值
        # 会绕过 pydantic 的 `Literal`。故在这里再收一次值域，不在集合内即跳过该键——与上面那条
        # `except: continue` 同形（「读不出来就当没配过」）。
        if field_name == "ai_trade_intel_quote_mode" and value not in tax.QUOTE_MODES:
            continue
        # 同一条理由，同一处修法：`alertMinImpact` 是一个 `Literal["medium","high"]`，而
        # `model_copy` 不跑校验器 ⇒ 一个从库内行读回来的坏值会一路穿到 `IntelLimits`，
        # 让「影响力门槛」变成一个恒不成立的判据（`IMPACT_W.get(坏值, 1.0)`）。
        if (field_name == "ai_trade_intel_alert_min_impact"
                and value not in tax.MIN_IMPACTS):
            continue
        updates[field_name] = value
    stored_key = await repo.get_app_setting(session, INTEL_API_KEY_APP_KEY, None)
    if isinstance(stored_key, str) and stored_key:
        updates["ai_trade_intel_search_api_key"] = stored_key
    return settings.model_copy(update=updates) if updates else settings


async def resolve_api_key(session, settings) -> str:
    """检索源密钥：库内行优先、env 回落（§3.2.3 的解析优先级）。"""
    stored = await repo.get_app_setting(session, INTEL_API_KEY_APP_KEY, None)
    if isinstance(stored, str) and stored.strip():
        return stored.strip()
    return str(getattr(settings, "ai_trade_intel_search_api_key", "") or "").strip()
