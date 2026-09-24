"""路由：AI 自动交易 start/stop/status + ws /ws/ai-trade/{sid}（ai-auto-trading §4）。

``start`` 受 ``AI_TRADE_ENABLED`` **与** ``LIVE_TRADING_ENABLED`` 双闸守门（自动路径直接调服务层，
须在此显式同时校验，任一 false → 403 ``AI_TRADE_DISABLED``，先于一切凭证读取）。``stop`` 刻意不守门：
即便运维中途关闭总闸，也须保证既有会话随时可停止并平掉自管持仓（安全优先）。``GET /status`` 只读不守门。
错误体统一 ``{error:{code,message}}``（经 main 全局 HTTPException 处理器包装）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import (APIRouter, Body, Depends, HTTPException, Query, WebSocket,
                     WebSocketDisconnect)
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai_auto import get_ai_trade_manager
from ..ai_auto.backtest_manager import (
    AiBacktestError,
    AiBacktestStartRequest,
    get_ai_backtest_manager,
)
from ..ai_auto.backstop_repair import AiTradeRepairBlocked
from ..ai_auto.backstop_repair import fail_message as repair_fail_message
from ..ai_auto.base_rules import MaBandRule, resolve_ma_band_rule
from ..ai_auto.cost_runtime import CostRuntime
from ..ai_auto.discipline import (
    PRESET_IDS,
    PRESET_LABELS,
    DisciplinePreset,
    inert_thresholds,
    preset_rows,
    preset_table,
    resolve_updates,
    unguarded_horizon,
)
from ..ai_auto.experience import (
    load_session_graph,
    resolve_experience,
    resolve_experience_settings,
)
from ..ai_auto.feed_runtime import AiTradeFeedSelfHealDisabled
from ..ai_auto.hold_horizon import HorizonRule, resolve_horizon_rule
from ..ai_auto.intel import IntelSessionRule, resolve_session_rule
from ..ai_auto.hold_horizon_profiles import profiles_wire
from ..ai_auto.manager import (
    AiTradeGroupMemberInput,
    AiTradeGroupPreflightError,
    AiTradeGroupStartRequest,
    AiTradePreflightError,
    AiTradeSessionLimitError,
)
from ..ai_auto.prior_service import prior_snapshot_out, rebuild_prior_snapshot
from ..ai_auto.profit_guard import ProfitGuardRule, resolve_profit_guard_rule
from ..ai_auto.profit_lock import ProfitLockRule, resolve_profit_lock_rule
from ..ai_auto.session import AiTradeManualExitDisabled
from ..ai_auto.signal_prior import resolve_prior_params, strategy_fingerprint
from ..ai_auto.sizing import parse_sizing_rule, window_need
from ..ai_auto.sizing_runtime import margin_precheck_notice
from ..ai_auto.track_record_service import build_profitability, resolve_track_record
from ..ai_auto.tradability_probe import probe
from ..ai_auto.trajectory import (TrajectoryOpenError, TrajectoryRule, probe_directory,
                                  resolve_trajectory_rule)
from ..ai_auto.wake_runtime import coherent_wake_floor_s
from ..config import Settings, get_settings
from ..db import ai_prior_repositories as prior_repo
from ..db import repositories as repo
from ..db.session import get_session
from ..okx.private_client import OKXPrivateError
from ..okx.types import BAR_SECONDS
from ..paper.strategy_factory import build_paper_strategy_spec
from ..schemas import (
    AiBacktestStartIn,
    AiTradeBaseRulesIn,
    AiTradeDirectionIn,
    AiTradeDisciplineIn,
    AiTradeGroupStartIn,
    AiTradeHistoryDetailOut,
    AiTradeHistoryListOut,
    AiTradeHorizonIn,
    AiTradeHorizonProfilesOut,
    AiTradePresetComparisonOut,
    AiTradePriorRebuildIn,
    AiTradePriorSnapshotOut,
    AiTradeProfitabilityOut,
    AiTradeProfitGuardIn,
    AiTradeProfitLockIn,
    AiTradeTradabilityOut,
    AiTradeTrendIn,
    trade_out,
)
from .ws_guard import guard_ws_origin

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ai-trade"])

_WS_HEARTBEAT_SECONDS = 20.0   # 静默此秒数后下发 ping（半开探测 + 满队列丢终态兜底，镜像 weight-train）
_BACKTEST_TERMINAL_TYPES = ("done", "cancelled", "error")


def _require_ai_enabled(settings: Settings) -> None:
    """写类守门：AI 自动化总闸与底层写链路总闸须同时为 True，否则 403（先于凭证读取）。"""
    if not (settings.ai_trade_enabled and settings.live_trading_enabled):
        raise HTTPException(status_code=403, detail={
            "code": "AI_TRADE_DISABLED",
            "message": "AI 自动交易未启用（需 AI_TRADE_ENABLED 与 LIVE_TRADING_ENABLED 均开启）"})


def _resolve_discipline(raw: AiTradeDisciplineIn | None, settings: Settings, *,
                        spec_count: int, priming_bars: int) -> tuple[Settings, DisciplinePreset,
                                                                     int]:
    """交易纪律预设的**唯一解析点**（discipline-preset-and-integrity §3.1；实盘与回测共用）。

    产出「会话私有的 Settings 副本」而不是改全局——`get_settings()` 是进程级缓存单例，且
    `LiveExecutor` 被 `ai_auto` 与 `live_auto` 共用同一个 settings 对象，`setattr` 会让 AI 侧的
    纪律阈值静默溢出到非 AI 链路（历史坑）。

    ⚠️ **调用点必须排在所有其它 `_resolve_*` 之前**（[P1-2]）：`_resolve_base_rules` /
    `_resolve_profit_guard` / `_resolve_horizon` / `_resolve_position_step` / `_resolve_wake_interval_s`
    全都在实参位置读 `settings`，而预设表里含 `ai_trade_profit_guard_hard_tp_return_pct`。派生副本
    晚一步产生，那一行预设就是死字母**且不报任何错**。G-13 钉死这一条。

    三层优先级（§3.1.4）：运维总闸 > 用户显式 > 服务端默认预设。

    Returns:
        `(settings, preset, priming_bars)`。未装配（总闸关 / `off` / `enabled=false`）时返回的是
        **同一个 settings 对象**（`is` 相等，G-18 / §7.1 的零回归支点）。

    Raises:
        HTTPException: 422 `AI_TRADE_DISCIPLINE_CONFLICT`——「关闭」与逐项覆盖同时出现（[P1-5]）。
            静默丢弃用户显式填的数是本仓库明令禁止的形状，故宁可拒绝也不猜。
    """
    off = DisciplinePreset(preset_id="off", updates={})
    if not settings.ai_trade_discipline_enabled:      # ① 运维总闸：一键回滚，优先级高于请求体
        return settings, off, priming_bars
    overrides = raw.overrides() if raw is not None else {}
    closed = raw is not None and (not raw.enabled or raw.preset == "off")
    if closed and overrides:
        names = "、".join(sorted(overrides))
        switch = "enabled=false" if (raw is not None and not raw.enabled) else 'preset="off"'
        raise HTTPException(422, detail={
            "code": "AI_TRADE_DISCIPLINE_CONFLICT",
            "message": f"交易纪律已被 {switch} 关闭，不能同时指定覆盖字段（{names}）："
                       "请去掉这些字段，或改选一个具体预设"})
    if closed:
        return settings, off, priming_bars
    preset_id = (raw.preset if raw is not None and raw.preset else
                 settings.ai_trade_discipline_default_preset)          # ②用户显式 / ③服务端默认
    preset = resolve_updates(preset_id, overrides, spec_count)
    if not preset.armed:                              # `off` ⇒ 原对象本身，逐字节回退
        return settings, preset, priming_bars
    derived = settings.model_copy(update=preset.updates)
    # R-2：装配仓位层后 `check_window_capacity` 会要求窗口装得下回看根数。**须从派生后的 settings
    # 现算**（`ai_trade_correlation_min_samples` 不在预设表里，运维调到 200 而这里写死 97，启动就会
    # 被自己的 422 打回），且**默认装配绝不允许把用户的启动打回 422**，故改为抬 primingBars 并如实
    # 披露。默认 primingBars=300 时这条抬升不触发。
    need = window_need(parse_sizing_rule(derived))
    if 0 < priming_bars < need:      # `priming_bars<=0` = 回测调用方（无暖机根数概念）⇒ 恒不触发
        preset.adjustments.append({
            "field": "primingBars", "from": priming_bars, "to": need,
            "reason": f"仓位层需要 {need} 根收盘价（波动率窗口与相关性样本的较大者），已自动抬高暖机根数"})
        priming_bars = need
    return derived, preset, priming_bars


# 方向对称性覆盖字段（camelCase wire）→ settings 键。值域已由 `AiTradeDirectionIn` 的 `Field`
# 在入口校验，本层**只做映射与缺省回填、不复夹**（沿用 `_resolve_position_step` 的 [R-2] 惯例）。
_DIRECTION_TO_SETTING: dict[str, str] = {
    "contrarianMinConfidence": "ai_trade_consensus_contrarian_min_confidence",
    "contrarianMaxOpposingVotes": "ai_trade_consensus_contrarian_max_opposing_votes",
    "contrarianMinEvidenceChars": "ai_trade_consensus_contrarian_min_evidence_chars",
    "contrarianMaxPerSession": "ai_trade_consensus_contrarian_max_per_session",
    "contrarianSizeMult": "ai_trade_consensus_contrarian_size_mult",
}


def _resolve_direction(raw: AiTradeDirectionIn | None, settings: Settings) -> Settings:
    """方向对称性「缺省即开启」语义的**唯一解析点**（directional-symmetry §7.1）。

    与 `_resolve_base_rules` / `_resolve_horizon` 的成文惯例逐字同构：字段缺省 ⇒ 按 settings
    装配开启态（老客户端 / 桌面旧包不发该字段也默认开启）；显式 `contrarianEnabled=false` ⇒
    该会话不装配逆势通路；运维总闸 `AI_TRADE_DIRECTIONAL_SYMMETRY_ENABLED=false` ⇒ 恒不装配。

    产出的是**又一层派生 settings 副本**（镜像 `_resolve_discipline` 与 `resolve_experience_settings`
    的既有姿态），而不是一个规则对象：本轴的六个阈值本来就住在 settings 上，
    `signals.consensus_rule_from_settings` 是它们唯一的读点，多造一个规则对象等于多一份会漂移的真源。

    ⚠️ **调用点必须排在 `_resolve_discipline` 之后**：纪律预设的 `steady` / `balanced` 两档各自带了
    `ai_trade_consensus_contrarian_enabled` 的默认值，早一步派生就会被预设覆盖掉用户的显式选择。

    Returns:
        原对象本身（无需改动时，`is` 相等）或新的派生副本。
    """
    if not settings.ai_trade_directional_symmetry_enabled:
        return settings
    if raw is None:
        return settings                      # 缺省 ⇒ 按 settings（含纪律预设）装配，零改动
    updates: dict = {"ai_trade_consensus_contrarian_enabled": bool(raw.contrarianEnabled)}
    for wire_key, setting_key in _DIRECTION_TO_SETTING.items():
        value = raw.overrides().get(wire_key)
        if value is not None:
            updates[setting_key] = value
    return settings.model_copy(update=updates)


# 趋势基调覆盖字段（camelCase wire）→ settings 键。值域已由 `AiTradeTrendIn` 的 `Field` 在入口
# 校验，本层**只做映射与缺省回填、不复夹**（沿用 `_resolve_position_step` 的 [R-2] 惯例）。
_TREND_TO_SETTING: dict[str, str] = {
    "counterGateEnabled": "ai_trade_trend_counter_gate_enabled",
    "counterMinConfidence": "ai_trade_trend_counter_min_confidence",
    "counterSizeMult": "ai_trade_trend_counter_size_mult",
    "consensusVoteEnabled": "ai_trade_trend_consensus_vote",
    "contrarianAllowanceEnabled": "ai_trade_trend_contrarian_allowance",
}


def _resolve_trend(raw: AiTradeTrendIn | None, settings: Settings) -> Settings:
    """执行周期趋势基调「缺省即开启」语义的**唯一解析点**（execution-trend-verdict §5.1）。

    与 `_resolve_direction` / `_resolve_horizon` 的成文惯例逐字同构：字段缺省 ⇒ 按 settings
    装配开启态（老客户端 / 桌面旧包不发该字段也默认开启）；显式 `enabled=false` ⇒ 该会话不
    装配基调层；运维总闸 `AI_TRADE_TREND_VERDICT_ENABLED=false` ⇒ **恒不装配**（`raw` 一律被
    忽略）——它是回退开关，一个还生效着的用户覆盖会让「按下总闸」之后仍有一层合入前不存在的东西。

    产出的是**又一层派生 settings 副本**（镜像 `_resolve_direction`），而不是一个规则对象：
    十个阈值本来就住在 settings 上，`trend_verdict.trend_rule_from_settings` 是它们唯一的读点，
    多造一个规则对象等于多一份会漂移的真源。

    ⚠️ **调用点必须排在 `_resolve_discipline` 之后**：纪律预设把 `settings` 换成了派生副本，
    早一步派生就会被预设覆盖掉用户的显式选择（与 `_resolve_direction` 同一条纪律）。

    Returns:
        原对象本身（无需改动时，`is` 相等）或新的派生副本。
    """
    if not settings.ai_trade_trend_verdict_enabled:
        return settings
    if raw is None:
        return settings                      # 缺省 ⇒ 按 settings 装配，零改动
    updates: dict = {"ai_trade_trend_verdict_enabled": bool(raw.enabled)}
    overrides = raw.overrides()
    for wire_key, setting_key in _TREND_TO_SETTING.items():
        value = overrides.get(wire_key)
        if value is not None:
            updates[setting_key] = value
    return settings.model_copy(update=updates)


def _resolve_intel(body: AiTradeGroupStartIn,
                   settings: Settings) -> tuple[IntelSessionRule, str]:
    """会话级情报采信的**唯一**解析点（ai-trade-intel-session-binding 轴 A）。

    ⚠️ **字段缺省 ⇒ 关闭**——与 `_resolve_base_rules` / `_resolve_profit_guard` /
    `_resolve_horizon` / `_resolve_direction` / `_resolve_trend` 那五个「缺省即开启」的兄弟
    函数**方向相反**。这是需求的明文要求，不是笔误：外部情报是一份会改变证据面的可选输入，
    用户没有明确要求时不该悄悄进入一次真实资金决策的上下文。照抄兄弟函数的默认值即判红
    （用例 `G-A1`）。

    ⚠️ **绝不通过在 `Settings` 副本上改 `ai_trade_intel_inject` 来表达会话选择**：
    `build_group_runtime` 的第一件事就是 `resolve_intel_settings(db, settings)`，而
    `INTEL_SETTING_BRIDGE` 里恰好有 `("injectEnabled", "ai_intel.inject", …)`——只要用户在
    设置中心动过一次那个开关，库内行就会**静默覆盖**会话的选择，而且不报错。规则因此作为一个
    独立对象一路传到 `_build_deps`（spec §3.1 A-2）。

    ⚠️ 本函数读的 `ai_trade_intel_session_opt_in_enabled` 来自**启动请求那条派生链**，而
    `IntelLimits` 里那两把闸（`expand_enabled` / `repeat_compress_enabled`）只能经
    `intel.intel_limits_for(inst_id)` 到达会话侧。两条链**刻意**分工：轴 A 的总闸必须在启动
    请求解析时就生效，而那一刻还没有 runtime、也拿不到情报派生副本。别「顺手统一」成一个。

    Args:
        body: 启动请求体。`legacy` 的判据**只有一个**——`"intel" not in body.model_fields_set`。
            pydantic v2 的 `model_fields_set` 能区分「键缺席」与「显式传了 null」，而
            `resume.py` 用 `AiTradeGroupStartIn(**start_wire)` 重建请求体：本特性合入后每一次
            新启动的 `start_wire` 都必然含 `"intel"` 键，故这个谓词精确地只对「本特性合入前
            落库、如今才冷续跑」的会话为真。
        settings: `_resolve_trend` 之后的派生副本。

    Returns:
        `(规则, source)`，`source ∈ {"request", "legacy_fallback", "ops_off", "resolve_failed"}`
        ——它进 `group_status().intel`，是「服务端替我决定了什么」的**显式回执**（本仓库对这件
        事的成文纪律）。

    Note:
        fail-open 的**唯一**落点就是这里（spec §3.0 原则 5）：任何异常都回退成
        `enabled=False`（**方向是关闭，不是开启**）并如实回执，绝不让 `/ai-trade/start` 失败，
        也**不在 `ai_trade_start` 端点外层再包一层 `except`**（CLAUDE.md 五：缩小作用域）。
    """
    try:
        legacy = "intel" not in body.model_fields_set
        raw = body.intel.model_dump() if body.intel is not None else None
        rule = resolve_session_rule(raw, settings, legacy=legacy)
        if not settings.ai_trade_intel_session_opt_in_enabled:
            return rule, "ops_off"
        return rule, ("legacy_fallback" if legacy else "request")
    except Exception as exc:  # noqa: BLE001 —— 见 docstring：情报层绝不阻挡真实资金启动
        logger.warning("ai-trade intel session rule resolve failed: %s", exc)
        return IntelSessionRule(enabled=False), "resolve_failed"


def _resolve_trajectory(body: AiTradeGroupStartIn,
                        settings: Settings) -> tuple[TrajectoryRule, str]:
    """会话级后训练轨迹采集的**唯一**解析点（ai-trade-trajectory-capture §2.10.2）。

    ⚠️ **整键缺省 ⇒ 不采集**（与 `_resolve_intel` 同向）。纯函数、零 IO、绝不抛：目录可用性由端点
    `_require_trajectory_directory` 探测，**绝不在这里校验**——冷续跑用 `start_wire` 重跑本链，任何
    `HTTPException` 都会把续跑翻成 409。

    Returns:
        `(规则, source)`，`source ∈ {request, not_requested, ops_off, resolve_failed}`。
    """
    raw = body.trajectoryCapture.model_dump() if body.trajectoryCapture is not None else None
    return resolve_trajectory_rule(raw, settings)


def _resolve_position_step(raw: float | None, settings: Settings) -> float:
    """仓位操作档位步长解析（[R-2] **仅**缺省回填）：`None → settings 默认`，否则原样返回。

    不复夹 `[0,1]`——边界已由 schema `Field(ge=0, le=1)` 在入口拒绝（越界 422，与既有 `leverage:
    Field(gt=0, le=125)` 只在 schema 兜边界、API 不复夹的惯例一致）。`0` 为显式连续退出口，被接受。
    """
    return settings.ai_trade_default_position_step_pct if raw is None else raw


def _resolve_wake_interval_s(minutes: float | None, settings: Settings) -> float:
    """Agent 墙钟定时唤醒周期解析（分钟 → 秒并**夹紧**，与 `_resolve_position_step` 同风格但夹紧而非仅回填）。

    `None → 默认（ai_trade_wake_interval_default_min）`；`<=0 → 0`（关闭定时唤醒，退出口）；否则夹到
    `[ai_trade_wake_interval_floor_s, ai_trade_wake_interval_ceil_min*60]` 秒（越界不拒绝，向 UX 友好收敛；
    前端 validateWakeInterval 另做即时报错，二者区间一致、职责互补，RN-9）。
    """
    if minutes is None:
        minutes = settings.ai_trade_wake_interval_default_min
    if minutes <= 0:
        return 0.0
    lo = settings.ai_trade_wake_interval_floor_s
    hi = settings.ai_trade_wake_interval_ceil_min * 60.0
    return min(max(minutes * 60.0, lo), hi)


def _resolve_base_rules(raw: AiTradeBaseRulesIn | None,
                        settings: Settings) -> MaBandRule | None:
    """【基础策略】默认开启语义的**唯一解析点**（ai-trade-base-strategy-ma-band §2.10/P1-5）。

    字段缺省 ⇒ 按 settings 装配开启态（老客户端 / 桌面旧包不发该字段也默认开启，符合需求「默认开启」）；
    显式 `enabled=false` ⇒ `None`（该会话不装配）；运维总闸关 ⇒ 恒 `None`。

    **刻意不在 dataclass 层给开启默认值**：`AiTradeGroupStartRequest.base_rule` / `AiBacktestSpec`
    的字段默认仍是 `None`，故既有直接构造它们的单测行为逐字不变。实盘与回测共用本函数，保证「用回测
    调出的参数在实盘不失真」（P1-4）。
    """
    raw_rule = raw.maBandEntry if raw is not None else None
    return resolve_ma_band_rule(
        raw_rule.model_dump() if raw_rule is not None else None, settings)


def _resolve_profit_guard(raw: AiTradeProfitGuardIn | None,
                          settings: Settings) -> ProfitGuardRule | None:
    """利润保护层「默认开启」语义的**唯一解析点**（ai-trade-profit-protection §2.8）。

    字段缺省 ⇒ 按 settings 装配开启态（老客户端 / 桌面旧包不发该字段也受保护）；显式 `enabled=false`
    ⇒ `None`（该会话不装配）；运维总闸关 ⇒ 恒 `None`。

    **实盘与回测两处启动都走本函数**（ai-trade-profit-protection-adaptive §7.2a I-3，与
    `_resolve_base_rules` 重新对齐）：第 1 轮把回测列为非目标，是因为当时只想得到「用收盘价近似」这一
    种做法（会低估触发频次，属假数据）；第 2 轮 F3 改用 **bar 内 OHLC 极值 + 保守成交序**（§2.3.1，与
    `simulator.apply_risk` 处理止损止盈同一范式），故该限制解除。回测侧另有运维总闸
    `ai_trade_profit_guard_backtest_enabled`（false ⇒ 恒不装配，一键回到第 1 轮的回测行为），
    结果 notes 与 UI 必须同时显示 §2.3.5 的保守近似免责。
    """
    return resolve_profit_guard_rule(raw.model_dump() if raw is not None else None, settings)


def _resolve_profit_lock(raw: AiTradeProfitLockIn | None,
                         settings: Settings) -> ProfitLockRule | None:
    """收益锁「默认开启」语义的唯一解析点：缺省 ⇒ 按 settings 开启；`enabled=false` ⇒ None。

    Raises:
        HTTPException: 422，`trailCostMult` 与 settings 合并后小于 `armCostMult`。
    """
    try:
        return resolve_profit_lock_rule(raw.model_dump() if raw is not None else None, settings)
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "AI_TRADE_PROFIT_LOCK_INVALID",
                                         "message": str(exc)}) from exc


def _resolve_backtest_profit_lock(raw: AiTradeProfitLockIn | None,
                                  settings: Settings) -> ProfitLockRule | None:
    """回测侧：在实盘语义之上再叠运维总闸 `ai_trade_profit_lock_backtest_enabled`。"""
    if not settings.ai_trade_profit_lock_backtest_enabled:
        return None
    return _resolve_profit_lock(raw, settings)


def _resolve_backtest_profit_guard(raw: AiTradeProfitGuardIn | None,
                                   settings: Settings) -> ProfitGuardRule | None:
    """回测侧解析：在实盘那条语义之上再叠一层运维总闸（§2.3.4 的一键回退）。"""
    if not settings.ai_trade_profit_guard_backtest_enabled:
        return None
    return _resolve_profit_guard(raw, settings)


def _resolve_horizon(raw: AiTradeHorizonIn | None, settings: Settings, *,
                     bar: str) -> HorizonRule | None:
    """交易周期模式「默认开启」语义的**唯一解析点**（ai-trade-holding-horizon-modes §4.2 / D-2）。

    四层语义：运维总闸关 ⇒ 恒 `None`；字段缺省 ⇒ 按 settings 装配默认模式（老客户端 / 桌面旧包不发
    该字段也默认开启）；显式 `enabled=false` ⇒ `None`（该会话不装配）；逐项覆盖 ⇒ 覆盖预设。

    `bar` 是**用户实际选择的执行周期**：`expectedHoldBars` 与 `confirmWindowSeconds` 两个派生量按它
    现算，绝不按预设自带的建议周期算——否则用户把 `intraday_swing` 的周期从 1H 改成 5m 之后，资金费
    期数与 G2 的墙钟上界仍按 1H 的口径走，是一个不会报错的静默错配。

    **实盘与回测共用本函数**（`base_rules` P1-4 / `profit_guard` I-3 的成文教训）：两侧默认不一致，
    用户拿回测调出来的参数在实盘直接失真。
    """
    return resolve_horizon_rule(raw.model_dump() if raw is not None else None,
                                settings, bar=bar)


def _resolve_backtest_horizon(raw: AiTradeHorizonIn | None, settings: Settings, *,
                              bar: str) -> HorizonRule | None:
    """回测侧解析：在实盘那条语义之上再叠一层运维总闸（一键回到不装配的回测行为）。"""
    if not settings.ai_trade_horizon_backtest_enabled:
        return None
    return _resolve_horizon(raw, settings, bar=bar)


async def _resolve_account(session: AsyncSession, account_id: int | None) -> repo.OkxAccount:
    """取指定账户或回落默认账户；缺失 → 404，凭证不全 → 400（绝不假数据，对齐 trading 路由）。"""
    if account_id is not None:
        row = await repo.get_okx_account(session, account_id)
    else:
        row = await repo.get_default_okx_account(session)
    if row is None:
        raise HTTPException(status_code=404, detail={
            "code": "ACCOUNT_NOT_FOUND", "message": "账户不存在或未配置默认账户"})
    if not ((row.secret_key or "").strip() and (row.passphrase or "").strip()):
        raise HTTPException(status_code=400, detail={
            "code": "ACCOUNT_NOT_CONFIGURED", "message": "请先补全 Secret Key / Passphrase"})
    return row


async def _build_specs(session: AsyncSession,
                       strategy_ids: list[int]) -> tuple[list, dict[int, str]]:
    """逐个策略行取存在性 + `build_paper_strategy_spec` 可实例化；任一失败 → AI_TRADE_STRATEGIES_INVALID。

    **顺带算出策略指纹**（C-6，signal-prior-and-ev-gate §3.5）：本函数已逐个取过策略行，
    `template_key` / `params_json` 就在手上，零额外查询。返回 `(specs, {strategyId: 指纹})`。
    """
    specs, fingerprints = [], {}
    for sid in strategy_ids:
        row = await repo.get_strategy_row(session, sid)
        if row is None:
            raise HTTPException(400, detail={
                "code": "AI_TRADE_STRATEGIES_INVALID", "message": f"策略不存在：{sid}"})
        try:
            specs.append(build_paper_strategy_spec(row))
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            raise HTTPException(400, detail={
                "code": "AI_TRADE_STRATEGIES_INVALID",
                "message": detail.get("message", f"策略无法实例化：{sid}")}) from exc
        fingerprints[sid] = strategy_fingerprint(row.template_key, row.params_json)
    return specs, fingerprints


async def _resolve_prior_snapshots(session: AsyncSession, settings: Settings, *,
                                   inst_ids: list[str], bar: str,
                                   fingerprints: dict[int, str]) -> dict[str, dict] | None:
    """按 `instId` 逐个取先验快照并注入指纹表；总闸①关 ⇒ `None`（连查询都不发）。

    **按标的键**而非分组共享一份（P1-5）：一个分组最多 5 个标的，共享快照等于让 BTC 的成员拿
    ETH 的历史统计去开真实仓位。缺键 = 该成员无先验（不是错误，逐字回退 ATR 粗估）。

    `fingerprints` 作为**内存态键**注入快照（不落库）：`PriorRuntime` 据此做 C-6 比对，改过参数
    的策略条目会被丢弃并推一条 `AI_TRADE_PRIOR_CONTRACT_MISMATCH`。
    """
    if not settings.ai_trade_signal_prior_enabled:
        return None
    out: dict[str, dict] = {}
    for inst_id in inst_ids:
        snapshot = await prior_repo.load_prior_snapshot(session, inst_id, bar)
        if snapshot is not None:
            out[inst_id] = {**snapshot, "fingerprints": fingerprints}
    return out or None


@router.post("/ai-trade/start")
async def ai_trade_start(body: AiTradeGroupStartIn, session: AsyncSession = Depends(get_session),
                         settings: Settings = Depends(get_settings)) -> dict:
    """启动 AI 自动交易「分组」（受双闸守门；§4.1 两段式前置门禁 + 整组回滚；回执含逐成员夹紧后预算）。

    预算总额天花板由服务端解析 `cap_frac = min(maxBudgetPct/100, ai_trade_max_capital_frac(默认 1.0=不约束),
    live_auto_group_max_capital_frac)`，与非 AI 多标的分组口径统一（ai-trade-total-budget-allocation 有意放开
    历史 5% 绝对红线）：该式恒等于前端预览所读 `effectiveMaxBudgetPct/100`，故「弹窗预览 == 落地」逐配置严格
    相等。留空默认 `default_frac = ai_trade_default_capital_frac`（1%，保守）；前端任何「上限」入参一概不接受。
    成员总预算按 allocationPct 切分（非倍增）。
    """
    _require_ai_enabled(settings)
    req = await build_start_request(session, settings, body)
    _require_profit_exit_with_horizon(req, settings)
    await _require_trajectory_directory(req, settings)
    try:
        await get_ai_trade_manager().start_group(req)
    except TrajectoryOpenError as exc:
        # RV10：新启动时 recorder 打开失败（建分组目录 / 写说明卡 / 起线程 / 单写者占用）与目录
        # 探测失败共用同一个 422——勾选了却静默不采，等于替用户丢掉明确要的数据。
        raise trajectory_dir_http_error(exc.message, directory=exc.directory,
                                        reason_code=exc.code, is_default=exc.is_default) from exc
    except AiTradeGroupPreflightError as exc:
        # 整组前置失败须透传逐标的 members 明细（全局处理器据此包成 {error:{code,message,members}}）。
        raise HTTPException(exc.status, detail={
            "code": exc.code, "message": exc.message, "members": exc.members})
    except (AiTradeSessionLimitError, AiTradePreflightError) as exc:
        raise HTTPException(exc.status, detail={"code": exc.code, "message": exc.message})
    return get_ai_trade_manager().group_status()


def _require_profit_exit_with_horizon(req: AiTradeGroupStartRequest,
                                      settings: Settings) -> None:
    """★ 轴 A-1 互锁（loss-and-liveness-hardening §4.1）：致命组合**不可被新启动**。

    **本闸默认已关**（`ai_trade_require_profit_exit_with_horizon` 默认 `False`，
    ai-trade-guided-launch 轴 A）：不开启利润保护时照样可以启动自动交易，代价由启动弹窗最后一步
    的风险清单如实披露。本函数因而是一道**运维可选的加固闸**——置 `True` 即逐字节恢复今天的行为。
    函数体不因默认值改变而放松：判据、落点、三条 remedies、错误体形状全部保留，落点也仍**必须**
    留在端点内（下一段的理由不因默认值改变而失效）。

    **落点必须在本端点内，绝不在 `build_start_request` 里**（评审 P0-3）：冷续跑
    （`ai_auto/resume.py`）延迟导入并用 `start_wire` 原文重跑同一个 `build_start_request`，
    而 `_rebuild_request` 把任何 `HTTPException` 一律翻成 409「启动契约已失效，无法继续」。
    判据放进装配链 ⇒ 生产里那 50 条「周期已装配 + 保护层未装配」的会话一旦暂停就**再也续不回来**，
    而续不回来的会话可能正持有真实仓位。互锁只拦「新建」，不碰「续跑」（`G-A8` 在同一个用例里
    同时断言这两件事，任何把判据挪回装配链的改动都会当场判红）。

    错误体给出**三条可执行的出路**，前端逐条渲染为按钮、不自造文案；选 422 而不是「后端自动补装
    保护层」，是因为后者会静默改变用户提交的语义，而本仓库对「服务端替我改了什么」的既有纪律是
    **必须显式回执**。

    Raises:
        HTTPException: 422 `AI_TRADE_UNGUARDED_HORIZON`。
    """
    if not unguarded_horizon(req.profit_rule, req.horizon_rule,
                             enabled=settings.ai_trade_require_profit_exit_with_horizon):
        return
    rule = req.horizon_rule
    minutes = int(float(getattr(rule, "min_hold_s", 0.0) or 0.0) // 60)
    raise HTTPException(422, detail={
        "code": "AI_TRADE_UNGUARDED_HORIZON",
        "message": (f"已选择「{getattr(rule, 'title', '') or '持仓周期'}」持仓周期"
                    f"（最短持仓 {minutes} 分钟），但利润保护层被关闭："
                    "浮盈回吐将没有任何自动兑现通道。"),
        "horizon": {"profileId": getattr(rule, "profile_id", None),
                    "minHoldSeconds": float(getattr(rule, "min_hold_s", 0.0) or 0.0)},
        "remedies": [
            {"id": "enable_profit_guard", "label": "开启利润保护（推荐）"},
            {"id": "disable_horizon", "label": "关闭持仓周期约束"},
            {"id": "ops_override",
             "label": "运维总闸 AI_TRADE_REQUIRE_PROFIT_EXIT_WITH_HORIZON=false"},
        ]})


async def _require_trajectory_directory(req: AiTradeGroupStartRequest,
                                        settings: Settings) -> None:
    """用户勾选了采集而目录不可用 ⇒ 422 + 可执行补救（trajectory-capture §2.10.3）。

    落点只能在端点内（理由同 `_require_profit_exit_with_horizon`：冷续跑重跑解析链，不经本端点）。
    勾选了却静默不采，等于替用户丢掉明确要的数据；故这里拒绝启动并给出一键出路，而不是「先跑起来
    再说」。未勾选 ⇒ 一次 IO 都不做。

    Raises:
        HTTPException: 422 `AI_TRADE_TRAJECTORY_DIR_INVALID`。
    """
    if not req.trajectory_rule.enabled:
        return
    verdict = await probe_directory(req.trajectory_rule.directory, settings)
    if verdict.ok:
        return
    raise trajectory_dir_http_error(verdict.message or "目录不可用", directory=verdict.directory,
                                    reason_code=verdict.code, is_default=verdict.is_default)


def trajectory_dir_http_error(reason: str, *, directory: str | None, reason_code: str | None,
                              is_default: bool) -> HTTPException:
    """目录探测失败与 recorder 打开失败**共用**的 422（同一组 remedies，前端逐条渲染为按钮）。

    Args:
        reason: 后端中文原因（不含 `**`）。
        directory: 归一化后的目标目录。
        reason_code: `TRAJECTORY_DIR_*` 或 `OPEN_FAILED` / `OPEN_TIMEOUT` / `OPEN_BUSY`。
        is_default: 目标是否已是默认根目录（是 ⇒ 不给「改用默认目录」这条出路）。
    """
    remedies = ([] if is_default
                else [{"id": "trajectory_use_default_dir", "label": "改用默认目录"}])
    remedies.append({"id": "trajectory_disable", "label": "本次不采集数据"})
    return HTTPException(422, detail={
        "code": "AI_TRADE_TRAJECTORY_DIR_INVALID", "message": f"后训练数据目录不可用：{reason}",
        "directory": directory, "reasonCode": reason_code, "remedies": remedies})


async def build_start_request(session: AsyncSession, settings: Settings,
                              body: AiTradeGroupStartIn) -> AiTradeGroupStartRequest:
    """`/ai-trade/start` 的**唯一**解析链（`ai_auto/resume.py` 的 S2 复用同一条，绝不另写一份）。

    抽出来的理由：冷续跑的真源是 `start_wire`（启动请求原文），它必须**重跑一遍**这条链才能拿到
    与首次启动逐字段相同的落地参数（spec §3.5 S2）。两处各写一遍必然漂移，而这里漂移的是止损
    百分比与账户凭证——不会报错，只会让用户以为止损 1% 实际 5%（D-2）。
    """
    account_row = await _resolve_account(session, body.accountId)
    specs, fingerprints = await _build_specs(session, body.strategyIds)
    # ★ [P1-2] 纪律预设**必须**在这里就把 `settings` 换成派生副本——下面所有 `_resolve_*` 都在
    # 实参位置读它，晚一步产生就会让预设里的 `profit_guard_hard_tp_return_pct` 变成死字母。
    # 此后本函数体内**再无**任何一处读得到旧对象（G-13 钉死顺序）。
    settings, discipline, priming_bars = _resolve_discipline(
        body.discipline, settings, spec_count=len(specs), priming_bars=body.primingBars)
    # 经验层 W-1（★ E-3 / 评审 P0-3）：**必须排在 `_resolve_discipline` 之后**——纪律预设把
    # `settings` 换成了派生副本，早一步读到的是旧对象。本步在其结果上**再派生一次**，把设置中心
    # 那 15 项可写阈值（`app_settings["experience.*"]`）并回 `Settings`；没有这一座桥，那个面板就是
    # 一个用户改得动、系统永远不读、而且不报错的假面板（不变式 I-10，用例 A-41）。
    settings = await resolve_experience_settings(session, settings)
    # 方向对称性（directional-symmetry §7.1）：**必须排在 `_resolve_discipline` 之后**——
    # `steady` / `balanced` 两档预设各自带了 `ai_trade_consensus_contrarian_enabled` 的默认值，
    # 早一步派生就会被预设覆盖掉用户的显式选择。本步在其结果上**再派生一次**。
    settings = _resolve_direction(body.direction, settings)
    # 执行周期趋势基调（execution-trend-verdict §5.1）：**同样必须排在 `_resolve_discipline`
    # 之后**，在其结果上再派生一次。与 `_resolve_direction` 的顺序无关（两者改的键不相交）。
    settings = _resolve_trend(body.trend, settings)
    # 会话级情报采信（intel-session-binding 轴 A）：紧跟 `_resolve_trend` 之后。它与其余
    # `_resolve_*` **不共享任何 Settings 键**（不产出派生副本，只产出一个规则对象），故顺序上
    # 只有一条硬约束——必须在 `req = AiTradeGroupStartRequest(...)` 之前。
    intel_rule, intel_source = _resolve_intel(body, settings)
    # 后训练轨迹采集（trajectory-capture §2.10.2）：只产出规则对象、不改 Settings；目录校验在端点。
    trajectory_rule, trajectory_source = _resolve_trajectory(body, settings)
    experience_rule = resolve_experience(
        body.experience.model_dump() if body.experience is not None else None, settings)
    experience_snapshot = await load_session_graph(
        session, settings=settings, rule=experience_rule)
    priors = await _resolve_prior_snapshots(
        session, settings, inst_ids=[m.instId for m in body.members], bar=body.bar,
        fingerprints=fingerprints)
    # ★ 轴 A-4 / C-1（loss-and-liveness-hardening）：这两步都要在 `req` 装配**之前**拿到三条
    # 已解析的契约，故在这里各解析一次并把结果复用进 `req`——`_resolve_*` 是纯函数、无副作用，
    # 但**绝不调用两遍**（两份产物在下一次改动时会静默分叉，而它们并排出现在同一张回执上）。
    horizon_rule = _resolve_horizon(body.tradingHorizon, settings, bar=body.bar)
    profit_rule = _resolve_profit_guard(body.profitGuard, settings)
    profit_lock_rule = _resolve_profit_lock(body.profitLock, settings)   # 同样只解析一次
    # ★ C-1：与目标持仓相干的唤醒下限。抬升写成装配层的一行 + 一条如实回执，
    # **不抛错**——它与 A-1 的 422 极性相反：这一条在冷续跑时会再跑一遍，而那是安全且想要的
    # （同一份 `start_wire` 得到同一个 `wake_s`，抬升是幂等的纯派生）。`divisor=0`（默认）⇒
    # `floor_s == 0` ⇒ 唤醒周期逐字不变、`adjustments` 不变。
    wake_s = _resolve_wake_interval_s(body.wakeIntervalMinutes, settings)
    floor_s = coherent_wake_floor_s(horizon_rule,
                                    divisor=settings.ai_trade_wake_horizon_divisor)
    if floor_s > 0 and 0 < wake_s < floor_s:
        discipline.adjustments.append({
            "field": "wakeIntervalMinutes",
            "from": round(wake_s / 60.0, 4), "to": round(floor_s / 60.0, 4),
            "reason": (f"所选持仓周期的目标最短持仓是 {int(horizon_rule.target_hold_min_s // 60)} "
                       f"分钟，唤醒周期已抬到 1/{settings.ai_trade_wake_horizon_divisor}，"
                       "避免一次持仓被反复问「要不要走」")})
        wake_s = floor_s
    # ★ E-8：`ai_trade_sizing_margin_precheck` 的**唯一读点**——它本轮不改任何行为，故必须在
    # 回执里如实说出这件事。一个「置 true 也什么都不会发生」的开关如果不说出来，就是本仓库
    # 明令禁止的「显示了但不作数」。默认关 ⇒ 恒 `None` ⇒ 回执逐字不变。
    precheck_notice = margin_precheck_notice(settings)
    if precheck_notice is not None:
        discipline.adjustments.append(precheck_notice)
    at = await repo.get_auto_trade_settings(session)
    # 天花板委托给账户级 maxBudgetPct 与运营天花板（与 effectiveMaxBudgetPct 同源，min 只收紧不放大）；
    # ai_trade_max_capital_frac 默认 1.0=不额外约束，运维可下调为 AI 专属更紧的绝对上限。
    cap_frac = min(at["maxBudgetPct"] / 100.0,
                   settings.ai_trade_max_capital_frac,          # AI 专属可选绝对天花板（默认 1.0=不约束）
                   settings.live_auto_group_max_capital_frac)   # 分组运营天花板（与 effectiveMaxBudgetPct 同源）
    req = AiTradeGroupStartRequest(
        specs=specs,
        members_in=[AiTradeGroupMemberInput(inst_id=m.instId, allocation_pct=m.allocationPct)
                    for m in body.members],
        bar=body.bar, account_row=account_row, settings=settings,
        context_refresh_rounds=body.contextRefreshRounds, allocation_mode=body.allocationMode,
        total_capital_usd=body.capitalUsd, leverage=body.leverage,
        take_profit_usd=body.takeProfitUsd, stop_loss_usd=body.stopLossUsd,
        stop_loss_pct=body.stopLossPct, take_profit_pct=body.takeProfitPct,  # E4：单仓 OCO 入参
        priming=body.priming, priming_bars=priming_bars,   # R-2 抬升后的值（如实列在 adjustments）
        budget_cap_frac=cap_frac, default_budget_frac=settings.ai_trade_default_capital_frac,
        position_step_pct=_resolve_position_step(body.positionStepPct, settings),
        wake_interval_s=wake_s,          # ★ C-1：可能已被相干下限抬升（如实进 adjustments）
        # 研判深度档（§4.4 / P1-1）：**必须落在请求对象上**，不能在装配时再读一次 settings——
        # 否则请求里的 `reasoningDepth` 会通过 422 校验、进 `start_wire`、然后在装配时被 settings
        # 默认值覆盖；而「暂停 → 冷续跑」走的正是「用 `start_wire` 重跑本函数」，深度档会静默
        # 降回默认档且**不报任何错**（用例 S-8）。
        reasoning_depth=body.reasoningDepth,
        base_rule=_resolve_base_rules(body.baseRules, settings),
        profit_rule=profit_rule,         # ★ 上面已解析一次，绝不再解析第二遍（两份产物必分叉）
        profit_lock_rule=profit_lock_rule,
        horizon_rule=horizon_rule,
        prior_snapshots=priors,
        track_records=await resolve_track_record(   # 轴 D：启动期一次性快照（会话内不刷新，R-5）
            session, settings, inst_ids=[m.instId for m in body.members], bar=body.bar),
        # 纪律预设的回执载体（`group_status().discipline` 现装配）。`adjustments` 是**可变列表**：
        # `_validate_sizing` 的 [P0-4] 最小下单量复核会在启动期继续往里追加。
        discipline_preset=discipline.preset_id, discipline_f_floor=discipline.f_floor,
        discipline_adjustments=discipline.adjustments,
        discipline_thresholds=preset_table(discipline.preset_id),
        # ★ 轴 A-4：在本会话中「其消费层未装配因而不作数」的阈值键。这是 D2「显示了但不作数」
        # 的通用修法——`balanced` 预设承诺的硬止盈 50% 在保护层未装配时逐字节不作数，而实测
        # 45 条 run 正处于这个状态且界面上没有任何一处说出来。**只披露，不改任何行为。**
        discipline_inert=inert_thresholds(
            preset_table(discipline.preset_id), profit_rule=profit_rule,
            sizing_rule=parse_sizing_rule(settings), horizon_rule=horizon_rule),
        # 会话级情报采信（轴 A）：规则 + 「服务端替我决定了什么」的显式回执。
        intel_rule=intel_rule, intel_source=intel_source,
        # 后训练轨迹采集：规则 + 显式回执码（`start_wire` 原文含本键 ⇒ 冷续跑自动同配置）。
        trajectory_rule=trajectory_rule, trajectory_source=trajectory_source,
        # 经验层 W-1：规则与启动期快照（`SessionGraph{graph, totals, as_of_ts}` —— 图与库存总量
        # **同一次取数**，因为那两个整数要被逐字渲染进 L0 页眉喂给下真单的模型，不变式 I-9）。
        experience_rule=experience_rule, experience_snapshot=experience_snapshot,
        # 续跑的唯一真源（pause-resume D-2 / [P2-11]）：只落请求体，绝不含 apiKey/secret/passphrase。
        # `experience` 键在原文里 ⇒ 冷续跑重跑本函数即自动同配置，**这一半无需额外代码**（§4.11）。
        start_wire=body.model_dump(mode="json"))
    return req


@router.post("/ai-trade/{group_id}/stop")
async def ai_trade_stop(group_id: str) -> dict:
    """停整组（停所有成员、各自平自管仓、落 Run 终态；刻意不守门，确保随时可停止平仓）。"""
    result = await get_ai_trade_manager().stop_group(group_id)
    if result is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "分组不存在"})
    return result


@router.post("/ai-trade/{group_id}/member/{inst_id}/stop")
async def ai_trade_member_stop(group_id: str, inst_id: str) -> dict:
    """仅停某成员（其余续跑；不守门）；停后无运行成员则分组清空。"""
    result = await get_ai_trade_manager().stop_member(group_id, inst_id)
    if result is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "分组或成员不存在"})
    return result


@router.post("/ai-trade/{group_id}/member/{inst_id}/exit")
async def ai_trade_member_exit(group_id: str, inst_id: str) -> dict:
    """人工授权的**单次**市价离场（ai-trade-exit-priority-and-commitment §6.2）。

    语义：只平**本会话自管仓位**，**不停止会话**（与 `/member/{inst}/stop` 的关键差别）。它是
    本轮人机交互的兜底——无论模型怎么想、无论哪一层怎么判，用户永远有一个不经模型的离场按钮。

    **刻意不守 AI 总闸**（与 `/ai-trade/{gid}/stop`、`.../backstop/repair` 同理）：离场是收敛
    风险的动作，任何时候都该能执行。它守的是运维总闸 `ai_trade_manual_exit_enabled`（关 ⇒ 409）。

    错误映射（端点层是唯一的翻译点）：运维总闸关 → 409 `AI_TRADE_MANUAL_EXIT_DISABLED`；
    分组 / 成员缺失 → 404（镜像 `stop_member`）。

    **交易所业务失败刻意不映射成 502**（与 `/backstop/repair` 的差别，见 §十一 D-10）：
    `executor.flatten` **不抛** `OKXError`——它内部重试后返回结构化失败 `{"ok": False, "code",
    "message", "residual"}`，故这里写一个 `except OKXPrivateError` 只会得到一段**不可达的死代码**。
    失败沿 200 + `{"ok": false, "message"}` 返回（message 已由会话层译成中文原因在前 + sCode），
    并另有一条 `AI_TRADE_MANUAL_EXIT_FAILED` 的 WS error 推给面板。

    **返回裸 `dict`、不绑 `response_model`**（`AiTradeExitOut` 只作文档 schema）：同类两个端点
    正是为绕开「`response_model` 静默吞掉新 wire 键」这个本仓库出过事的形态。
    """
    try:
        result = await get_ai_trade_manager().exit_member(group_id, inst_id)
    except AiTradeManualExitDisabled as exc:
        raise HTTPException(409, detail={"code": exc.code, "message": exc.message}) from exc
    if result is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "分组或成员不存在"})
    return result


@router.post("/ai-trade/fee-rates/refresh")
async def ai_trade_refresh_fee_rates() -> dict:
    """人工重取一次交易所官方费率档位（silent-degradation-repair 轴 A-4）。

    真机实测：`GET /account/trade-fee` **1,524 次请求、0 次成功**（自 2026-08-11 起），于是三周来
    成本模型、`edge` 门禁的成本项、净额盈亏的扣减**全部跑在本地保守常数上**。轴 A-2 把参数改成
    阶梯之后，这个端点是「改完之后立刻看一眼对不对」的那个按钮——真值源卡片第 1 行的 `action`
    指向它，而那条纪律是「每一行的 action 都必须真的点得下去」。

    **只读私有 GET**：不触碰任何写路径，故与 `/okx/connectivity/probe` 同源，**不要求**
    `live_trading_enabled`；带单飞锁。分组未在跑 ⇒ **409 而不是静默成功**——一个点了没反应的
    按钮比没有按钮更糟。

    **返回裸 `dict`、不绑 `response_model`**（同族三个端点同款纪律：绑上之后新 wire 键会被
    FastAPI 静默剔除）。
    """
    wire = await get_ai_trade_manager().refresh_fee_rates()
    if wire is None:
        raise HTTPException(409, detail={
            "code": "AI_TRADE_NOT_RUNNING",
            "message": "当前没有在跑的 AI 自动交易分组，无法重取费率档位"})
    return {"ok": True, "feeRates": wire}


@router.post("/ai-trade/{group_id}/member/{inst_id}/relink")
async def ai_trade_member_relink(group_id: str, inst_id: str) -> dict:
    """人工授权的**单次**强制重连 + REST 活性补取（ai-trade-feed-self-healing §4.2）。

    语义：换一条新的 K 线 WS，再立刻取一次最新已收线根。**不停止会话、不平仓、不改分组生命周期**
    （与 `/member/{inst}/exit`、`/backstop/repair` 的姿态同源）。它是本轮人机交互的兜底——自动阶梯
    走到 `stuck` 之后，用户永远有一个不经模型、不停会话的重试按钮。

    **刻意不守 AI 总闸**（`ai_trade_enabled`）：恢复观测是**收敛**风险的动作，任何时候都该能执行。
    它守的是运维总闸 `ai_trade_feed_selfheal_enabled`（关 ⇒ 409 `AI_TRADE_FEED_SELFHEAL_DISABLED`）。

    **交易所侧失败刻意不映射成 502**（与 `/exit` 同理）：会话层不抛 `OKXError`——它内部被一条硬
    死线（`ai_trade_feed_relink_deadline_s`，默认 12 秒）包住并返回结构化失败，故这里写一个
    `except OKXPrivateError` 只会得到一段不可达的死代码。失败沿 200 + `{"ok": false, "message"}`
    返回。死线本身也**不映射 504**：前端对本路径落 `FAST_MS = 20_000`，而本端点最该被点击的场景
    就是「网络不通」——12 秒内必须给出一个诚实的回执，绝不让用户在唯一的兜底按钮上读到
    「本次操作结果未知」。

    **返回裸 `dict`、不绑 `response_model`**：`response_model` 会静默吞掉新 wire 键，那是本仓库
    出过事的形态（同类三个端点同款）。
    """
    try:
        result = await get_ai_trade_manager().relink_member(group_id, inst_id)
    except AiTradeFeedSelfHealDisabled as exc:
        raise HTTPException(409, detail={"code": exc.code, "message": exc.message}) from exc
    if result is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "分组或成员不存在"})
    return result


@router.post("/ai-trade/{group_id}/member/{inst_id}/adopt-orphan")
async def ai_trade_member_adopt_orphan(group_id: str, inst_id: str,
                                       body: dict | None = Body(default=None)) -> dict:
    """把交易所侧那笔未接管持仓接进当前会话（fill-truth-and-effective-controls 轴 B）。

    路径与既有五个成员端点**同构**（`/ai-trade/{group_id}/member/{inst_id}/…`）：多标的分组是既有
    能力，同一个 `instId` 可以同时出现在两个运行中的分组里，丢掉 `group_id` 会让 404 的语义在那时
    是错的（评审 RV-P1-5）。

    **必须二次确认**（`{"confirm": true}`）：这是本轮唯一一个会改写真实资金立场的端点，而它接管的
    是一笔来路不明的持仓——`confirm` 缺失即 400，绝不让一次误点改变账本。

    **刻意不守 AI 总闸**（与 `/exit`、`/backstop/repair`、`/relink` 同理）：把一笔无人看管的持仓
    纳入管辖是**收敛**风险的动作，任何时候都该能执行。暂停态同样放行（§十三 Q5 的裁定：留在孤儿态
    同样无人看管，而且还多一层「系统不知道它存在」），会话侧的回执会写明后果。

    **返回裸 `dict`、不绑 `response_model`**：`response_model` 会静默吞掉新 wire 键（本仓库出过事
    的形态，同类三个端点同款）。
    """
    if not isinstance(body, dict) or body.get("confirm") is not True:
        raise HTTPException(400, detail={
            "code": "CONFIRM_REQUIRED",
            "message": "接管交易所侧持仓会改变本会话的真实资金立场，请显式确认（confirm: true）"})
    try:
        result = await get_ai_trade_manager().adopt_orphan_member(group_id, inst_id)
    except OKXPrivateError as exc:
        raise HTTPException(502, detail={
            "code": exc.code,
            "message": f"向交易所取持仓断面失败：{exc.message}"}) from exc
    if result is None:
        raise HTTPException(404, detail={"code": "MEMBER_NOT_FOUND",
                                         "message": "分组或成员不存在"})
    if not result.get("ok"):
        raise HTTPException(409, detail={"code": result.get("code") or "NO_ORPHAN",
                                         "message": result.get("message") or ""})
    return result


@router.post("/ai-trade/{group_id}/member/{inst_id}/backstop/repair")
async def ai_trade_member_backstop_repair(group_id: str, inst_id: str) -> dict:
    """人工授权的**单次**交易所侧兜底补挂（ai-trade-backstop-repair-and-net-backtest §4.1）。

    与自动路径共用同一个 `plan_repair` 与同一条写路径，**只绕过 `mode`，绝不绕过 H1/H2/H3**。

    **刻意不守 AI 总闸**（与 `/ai-trade/{gid}/stop` 同理，D-11）：补挂是收敛风险的动作，任何时候
    都该能执行。它守的是运维总闸——总闸关 ⇒ 整层未装配 ⇒ 会话侧抛 `not_applicable` ⇒ 本处 409。

    错误映射（端点层是唯一的翻译点）：`AiTradeRepairBlocked` → 409 + `blockedReason`；
    `OKXPrivateError` → 502 + 原始 sCode + **中文原因在前**（§4.1；交易所原文常是英文，而 `plan`
    是默认模式 ⇒ 人工那一发的失败提示往往是用户对本层唯一的一次阅读，绝不能只给一串归一码）；
    分组 / 成员缺失 → 404（镜像 `stop_member`）。message 已在服务层经 `_redact` 脱敏。
    """
    try:
        result = await get_ai_trade_manager().repair_member(group_id, inst_id)
    except AiTradeRepairBlocked as exc:
        raise HTTPException(409, detail={"code": "AI_TRADE_BACKSTOP_NOT_REPAIRABLE",
                                         "message": exc.message,
                                         "blockedReason": exc.reason}) from exc
    except OKXPrivateError as exc:
        text = str(exc.message or "")
        why = repair_fail_message(exc.code)
        raise HTTPException(502, detail={
            "code": exc.code,
            "message": f"{why}：{text}" if text and why not in text else (text or why),
        }) from exc
    if result is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "分组或成员不存在"})
    return result


@router.get("/ai-trade/status")
async def ai_trade_status(settings: Settings = Depends(get_settings)) -> dict:
    """当前活跃 AI 分组（无则 group:null）；前端重载后据此重连各成员 WS（只读，不守门）。

    `resumable`（pause-resume §5.6）：**仅**冷占位（内存无会话、磁盘有暂停契约）时非空。
    它与 `group` **互斥**——热暂停走 `group.state="paused"`，两者同时非空即 bug（G-9 冻结）。
    该端点 `-> dict` 且无 `response_model`，故新键不会被 FastAPI 静默吞掉。

    `lifecycleUnknown` / `serverTs`（lifecycle-truth B-8）：两个**加法**键。
    前者修 D-12 ——「后端不知道有没有暂停分组」今天在界面上表现为「没有暂停分组」，与真实资金上
    「读不到 ≠ 没有」的既定极性矛盾；后者是前端倒计时的**服务端时钟锚点**（本仓库已有
    `live-db-location-and-ai-trade-ts-dual-clock` 的双时钟教训，本机时钟偏移一小时不该让
    「下一次决策」读出一个荒谬的数）。总闸关 ⇒ 两键整键缺席（逐字回退，用例 12）。
    """
    manager = get_ai_trade_manager()
    body = {"group": manager.group_status(), "aiTradeEnabled": settings.ai_trade_enabled,
            "resumable": manager.resumable()}
    if settings.ai_trade_lifecycle_truth_enabled:
        body["lifecycleUnknown"] = manager.is_hydration_blind()
        body["serverTs"] = int(time.time())
    return body


@router.get("/ai-trade/discipline-presets")
async def ai_trade_discipline_presets(settings: Settings = Depends(get_settings)) -> dict:
    """交易纪律预设的只读阈值表（discipline-preset 轴 A 的弹窗数据源）。

    **不守 AI 总闸**（沿用 `/ai-trade/horizon-profiles` 与 `priors/rebuild` 的先例）：读一张表不下
    任何单，总闸关闭时弹窗也要能把「有哪些预设、各是什么阈值」画出来。

    数值与中文标签**全部由本端点下发，前端一个都不复制**——本仓库已经有过前后端各存一份口径导致
    静默漂移的事故（`edgeSource` 逐键手抄吞新键）。一张用户按下启动键**之前**看的表，如果与真正
    生效的那组数分叉，比不给这张表更坏。

    `enabled=false`（运维总闸关）时照常下发表格，但 UI 须据此说明「本机已整轴关闭」——藏起来会让
    用户以为是自己选错了。
    """
    return {
        "enabled": settings.ai_trade_discipline_enabled,
        "defaultPreset": settings.ai_trade_discipline_default_preset,
        "presets": [{"id": preset_id,
                     "label": PRESET_LABELS[preset_id],
                     "rows": preset_rows(preset_id)}
                    for preset_id in PRESET_IDS],
    }


@router.get("/ai-trade/horizon-profiles", response_model=AiTradeHorizonProfilesOut)
async def ai_trade_horizon_profiles(bar: str = Query("1H"),
                                    settings: Settings = Depends(get_settings)) -> dict:
    """交易周期模式预设表（ai-trade-holding-horizon-modes §4.1）。

    **不守 AI 总闸**：读预设表不下任何单，弹窗在总闸关闭时也要能把「有哪些模式」画出来（沿用
    `priors/rebuild` 不守总闸的先例理由）。数值与中文标签**全部**由本端点下发，前端不复制任何一个
    ——本仓库已经有过前后端各存一份口径导致静默漂移的事故（`edgeSource` 逐键手抄吞新键），一张
    11 列 × 5 行的表手抄一遍必然漂移（R-3 / G-10）。

    `estimatedCostBps` 按当前配置常数估（**非实时盘口**，UI 须标「约」）：契约摘要第三行的
    「需 ≥N bps」要有一个可信来源，前端自己乘一遍就会与门禁真正判定的数分叉。
    """
    bar_seconds = BAR_SECONDS.get(bar) or BAR_SECONDS["1H"]
    # 复用**同一对**成本纯核心（不发任何请求的只读运行时，镜像 `build_charter_text` 的姿态）；
    # 取不到（总闸关 / 估算失败）⇒ None ⇒ 前端该行改述为「费率未知」，绝不编一个数。
    preview = CostRuntime(bar, settings, None, None).estimate_preview_cost(
        ord_type="market", side="long", bar_seconds=bar_seconds)
    return profiles_wire(settings,
                         estimated_cost_bps=None if preview is None else preview.total_bps)


@router.get("/ai-trade/tradability", response_model=AiTradeTradabilityOut)
async def ai_trade_tradability(instId: str, bar: str,
                               preset: str | None = Query(None),
                               slPct: float | None = Query(None, gt=0),
                               session: AsyncSession = Depends(get_session),
                               settings: Settings = Depends(get_settings)) -> dict:
    """可交易性预检（tradability-and-open-funnel §5.1）：**只提示、不阻断**。

    **不守 AI 总闸**（沿用 `/ai-trade/horizon-profiles`、`/ai-trade/discipline-presets` 的成文
    先例：读一张表不下任何单）。取数与判据全在 `ai_auto.tradability_probe` / `ai_auto.tradability`
    ——本函数只解析 query → 调它 → 原样返回（`api/ai_auto.py` 的净增预算是硬验收判据，G-19b）。

    `preset` 缺省取 `ai_trade_discipline_default_preset`；**未知值按 `off` 处理**（不 422：一个
    看不懂的预设名不该把用户的预检挡掉）。`slPct` 是弹窗里单仓止损那一格的现值（留空 = 用服务端
    默认）。**取数失败一律 200 + `level:"unknown"`，绝不 5xx。**
    """
    if bar not in BAR_SECONDS:
        raise HTTPException(422, detail={"code": "BAD_BAR",
                                         "message": f"不支持的周期：{bar}"})
    derived = _preset_settings(preset, settings, sl_pct=slPct)
    return await probe(session, derived, inst_id=instId, bar=bar)


def _preset_settings(preset: str | None, settings: Settings,
                     *, sl_pct: float | None = None) -> Settings:
    """按弹窗当前选中的预设**与用户填的止损**派生一份会话私有 Settings 副本（与启动路径同一解析点）。

    探针算的必须是「按下启动键之后真正会生效的那组阈值」，而不是进程默认值——两者分叉会让预检
    说的和门禁做的不是一回事，那正是 R-2 要防的漂移。两者皆缺省 ⇒ 原对象直通（`is` 相等）。

    `sl_pct` 同理（realized-edge-calibration 轴 E-0）：启动路径取的是
    `req.stop_loss_pct or ai_trade_default_sl_pct`（`manager.py:868`），故预检若只读进程默认值，
    一个已经把止损改到 1% 的用户仍会被按出厂的 5% 告知「止损离现价约 500 bps、建议改到 0.56%」
    ——**判据取自不携带该信息的入参**，正是 [P1-8] 点名的那个形状。
    入参**照启动路径同样夹紧**到用户可见红线（`manager._resolve_risk:867` 的 `ai_trade_max_sl_pct`）：
    预检要说的是真正会挂出去的那个止损，而不是用户填在框里的那个数。
    """
    preset_id = preset or settings.ai_trade_discipline_default_preset
    updates: dict = {}
    if settings.ai_trade_discipline_enabled and preset_id in PRESET_IDS:
        resolved = resolve_updates(preset_id, {}, 0)
        if resolved.armed:
            updates.update(resolved.updates)
    if sl_pct is not None:
        cap = float(updates.get("ai_trade_max_sl_pct", settings.ai_trade_max_sl_pct))
        updates["ai_trade_default_sl_pct"] = min(max(0.0, float(sl_pct)), cap)
    return settings.model_copy(update=updates) if updates else settings


@router.get("/ai-trade/history/preset-comparison",
            response_model=AiTradePresetComparisonOut)
async def ai_trade_preset_comparison(session: AsyncSession = Depends(get_session)) -> dict:
    """历史纪律预设对照卡（轴 E）：按 `aiStats.disciplinePreset` 分组聚合历史 run（只读，不守门）。

    ⚠️ 路由顺序：本条**必须**排在 `/ai-trade/history/{group_id}` **之前**，否则
    `preset-comparison` 会被那条路径参数吞掉并当成一个 group_id 去查（返回 404）。
    """
    return await repo.list_ai_trade_preset_comparison(session)


@router.get("/ai-trade/history/profitability", response_model=AiTradeProfitabilityOut)
async def ai_trade_profitability(instId: str | None = Query(None), bar: str | None = Query(None),
                                 sinceTs: int | None = Query(None),
                                 preset: str | None = Query(None),
                                 horizonProfile: str | None = Query(None),
                                 session: AsyncSession = Depends(get_session),
                                 settings: Settings = Depends(get_settings)) -> dict:
    """跨会话盈利体检（realized-edge-calibration 轴 B/C · §5.1）：**池化**口径的历史统计。

    **不守 AI 总闸**（沿用 `/ai-trade/horizon-profiles`、`/ai-trade/history/preset-comparison`
    的先例：读一张历史表不下任何单）。装配全在 `ai_auto.track_record_service`，本函数只解析
    query → 调它 → 原样返回（`api/ai_auto.py` 的净增预算是硬验收判据）。

    ⚠️ 路由顺序：本条**必须**排在 `/ai-trade/history/{group_id}` **之前**，否则会被那条路径参数
    吞掉、当成一个 `group_id` 去查并返回 404。`preset-comparison` 已经踩过这一次（G-21 钉住）。
    """
    return await build_profitability(session, settings, repo.TripQuery(
        inst_id=instId, bar=bar, since_ts=sinceTs, preset=preset,
        horizon_profile=horizonProfile))


@router.get("/ai-trade/history", response_model=AiTradeHistoryListOut)
async def ai_trade_history(limit: int = Query(20, ge=1, le=100),
                          offset: int = Query(0, ge=0),
                          session: AsyncSession = Depends(get_session)) -> dict:
    """历史 AI 自动交易会话列表（ai-trade-stats-dashboard §3.7）：按 group 聚合、startedTs DESC、
    分页；排除当前运行中分组（其在「当前」视图展示）。只读，不守门（历史查看不涉及下单）。"""
    active = get_ai_trade_manager().group_status()
    exclude = active["groupId"] if active else None
    # D-8 / [P0-3]：已暂停的分组按 **DB `status`** 排除（不依赖内存派生的 id——冷启后内存是空的，
    # 一个仍持真仓的 paused 分组会同时出现在「历史会话」里）。**只加 "paused"，不加 "running"**：
    # 加它会打破既有用例 `test_history_endpoints`（`_add_run` 默认 status="running"）。
    return await repo.list_ai_trade_groups(
        session, limit=limit, offset=offset, exclude_group_id=exclude,
        exclude_statuses=("paused",))


@router.get("/ai-trade/history/{group_id}", response_model=AiTradeHistoryDetailOut)
async def ai_trade_history_detail(group_id: str,
                                 session: AsyncSession = Depends(get_session)) -> dict:
    """单次 AI 自动交易历史详情（§3.7）：会话头 + 逐成员成交（trade_out）/ metrics + 后端聚合 KPI；组不存在 → 404。"""
    detail = await repo.get_ai_trade_group_detail(session, group_id)
    if detail is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "历史会话不存在"})
    for member in detail["members"]:
        member["trades"] = [trade_out(t) for t in member["trades"]]
    return detail


# ============================ 信号先验快照（signal-prior-and-ev-gate §4.1）============================
# 同一 `(instId, bar)` 的重建守门（进程内）：重建是秒级同步计算 + 分页取数，重入会白烧公共配额并让
# 两次写互相覆盖。只读端点不守门。
_PRIOR_REBUILD_INFLIGHT: set[tuple[str, str]] = set()


@router.post("/ai-trade/priors/rebuild", response_model=AiTradePriorSnapshotOut)
async def ai_trade_prior_rebuild(body: AiTradePriorRebuildIn,
                                 session: AsyncSession = Depends(get_session),
                                 settings: Settings = Depends(get_settings)) -> dict:
    """离线重建某 `(instId, bar)` 的先验快照（同步执行，重计算在 `run_in_threadpool` 内）。

    业务逻辑全在 `ai_auto/prior_builder`，本层只做守门与编排。**不守 AI 总闸**：重建只算统计、
    不下任何单，运维需要在开闸之前先把快照建好（§九 L1 的第一个动作）。
    """
    if body.bar not in BAR_SECONDS:
        raise HTTPException(400, detail={"code": "AI_PRIOR_BAR_INVALID",
                                         "message": f"不支持的周期：{body.bar}"})
    specs, fingerprints = await _build_specs(session, body.strategyIds)
    lookback = int(body.lookbackBars or settings.ai_trade_prior_lookback_bars)
    if lookback > settings.ai_trade_prior_max_bars:      # **取数前**快速失败，绝不无界拉 K 线
        raise HTTPException(413, detail={
            "code": "AI_PRIOR_TOO_LARGE",
            "message": (f"回看根数 {lookback} 超过上限 {settings.ai_trade_prior_max_bars}，"
                        "请缩小 lookbackBars")})
    key = (body.instId, body.bar)
    if key in _PRIOR_REBUILD_INFLIGHT:
        raise HTTPException(409, detail={"code": "AI_PRIOR_REBUILD_BUSY",
                                         "message": "该标的/周期的先验重建正在进行中"})
    _PRIOR_REBUILD_INFLIGHT.add(key)
    try:
        snapshot = await rebuild_prior_snapshot(
            inst_id=body.instId, bar=body.bar, specs=specs, fingerprints=fingerprints,
            settings=settings, lookback=lookback, segments=body.segments,
            # holding-horizon §4.5：按目标持仓模式的前瞻窗口重建（缺省仍取全局常数）。装配了周期
            # 模式的会话其 horizonBars 是 12–72，按全局 8 建的快照会被 C-3 整份丢弃——这是出路。
            horizon_bars=body.horizonBars)
    finally:
        _PRIOR_REBUILD_INFLIGHT.discard(key)
    await prior_repo.save_prior_snapshot(session, snapshot)
    return prior_snapshot_out(snapshot)


@router.get("/ai-trade/priors")
async def ai_trade_prior_get(instId: str, bar: str,
                             horizonBars: int | None = Query(None, ge=1, le=1000),
                             session: AsyncSession = Depends(get_session),
                             settings: Settings = Depends(get_settings)) -> dict:
    """读当前快照。无记录 ⇒ `{"exists": false}`——「还没建」是正常状态，不是错误（故非 404）。

    `contract` 直接把 C-2 新鲜度 / C-3 持有期的判定结果告诉运维，省得对着时间戳心算。
    可选 `horizonBars`（holding-horizon §4.5）用于**按目标周期模式核对**契约；缺省仍比全局常数
    （行为逐字不变）。
    """
    snapshot = await prior_repo.load_prior_snapshot(session, instId, bar)
    if snapshot is None:
        return {"exists": False}
    params = resolve_prior_params(settings, horizon_bars=horizonBars)
    age_s = time.time() - float(snapshot.get("builtAtTs") or 0)
    return {"exists": True, "snapshot": prior_snapshot_out(snapshot),
            "contract": {"fresh": age_s <= params.max_age_s,
                         "horizonMatch": (int(snapshot.get("horizonBars") or 0)
                                          == params.horizon_bars)}}


@router.websocket("/ws/ai-trade/{session_id}")
async def ws_ai_trade(websocket: WebSocket, session_id: str) -> None:
    """事件协议与 /ws/live-auto/{id} 同信封；连接即原子 subscribe→status→snapshot（防丢事件）。"""
    if not await guard_ws_origin(websocket):   # 必须在 accept() 之前：不合规连接一帧都不许收到
        return
    await websocket.accept()
    session = get_ai_trade_manager().get(session_id)
    if session is None:
        await websocket.send_text(json.dumps({"type": "error",
                                             "data": {"code": "NOT_FOUND", "message": "会话不存在"}}))
        await websocket.close()
        return
    queue = session.subscribe()
    snap = session.snapshot()
    try:
        await websocket.send_text(json.dumps({"type": "status", "data": snap["status"]}))
        await websocket.send_text(json.dumps({"type": "snapshot", "data": snap}))
        while True:
            # 空闲保活（spec D3）：终态由 `status` 事件承载，服务端不主动关连接（同 ws_paper）。
            try:
                event = await asyncio.wait_for(queue.get(), timeout=_WS_HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                await websocket.send_text(json.dumps({"type": "ping"}))
                continue
            await websocket.send_text(json.dumps(event))
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        session.unsubscribe(queue)


# ============================ AI 回测模式（ai-auto-trading-backtest §4）============================
# 回测子路由**仅**校验 Agent 已配置（在 AiBacktestManager.start 内经 resolve_active_provider），
# **不**校验 AI_TRADE_ENABLED/LIVE_TRADING_ENABLED 双闸——回测走纯模拟 SimBroker、不下真单、无真实资金面。
@router.post("/ai-trade/backtest/start")
async def ai_backtest_start(body: AiBacktestStartIn, session: AsyncSession = Depends(get_session),
                            settings: Settings = Depends(get_settings)) -> dict:
    """启动一个 AI 自动交易回测作业（§2.9 前置门禁；回执含 plannedRounds 与夹紧后生效资金/杠杆）。"""
    specs, _ = await _build_specs(session, body.strategyIds)   # 回测侧不接先验（D-2 前视偏差）
    # R-3：回测吃**同一个** `_resolve_discipline`（`base_rules` P1-4 / `profit_guard` I-3 的成文
    # 教训：两处各解析一次 ⇒ 用户拿回测调出来的参数在实盘失真）。回测无暖机根数概念，故 R-2 的
    # `primingBars` 抬升在此不适用（传 0 ⇒ 恒不触发；回测窗口由 `start_ts/end_ts` 决定）。
    settings, discipline, _ = _resolve_discipline(
        body.discipline, settings, spec_count=len(specs), priming_bars=0)
    # 方向对称性：与实盘**同一解析点、同一顺序**（纪律预设之后）。两侧不同源，用户拿回测调出来
    # 的逆势通路参数在实盘直接失真（`base_rules` P1-4 的成文教训）。
    settings = _resolve_direction(body.direction, settings)
    # 执行周期趋势基调：与实盘**同一解析点、同一顺序**（纪律预设之后）。两侧不同源，用户拿
    # 回测调出来的基调参数在实盘直接失真（评审 RP1-10 / `base_rules` P1-4 的成文教训）。
    settings = _resolve_trend(body.trend, settings)
    req = AiBacktestStartRequest(
        specs=specs, inst_id=body.instId, bar=body.bar, start_ts=body.startTs, end_ts=body.endTs,
        settings=settings, context_refresh_rounds=body.contextRefreshRounds,
        decision_every_bars=body.decisionEveryBars, initial_capital=body.initialCapital,
        leverage=body.leverage, stop_loss_pct=body.stopLossPct, take_profit_pct=body.takeProfitPct,
        take_profit_usd=body.takeProfitUsd, stop_loss_usd=body.stopLossUsd,
        min_consensus=body.minConsensus,
        position_step_pct=_resolve_position_step(body.positionStepPct, settings),
        base_rule=_resolve_base_rules(body.baseRules, settings),
        profit_rule=_resolve_backtest_profit_guard(body.profitGuard, settings),
        profit_lock_rule=_resolve_backtest_profit_lock(body.profitLock, settings),
        horizon_rule=_resolve_backtest_horizon(body.tradingHorizon, settings, bar=body.bar))
    try:
        job = await get_ai_backtest_manager().start(req)
    except AiBacktestError as exc:
        raise HTTPException(exc.status, detail={"code": exc.code, "message": exc.message})
    runner = job.runner
    return {"jobId": job.job_id, "instId": runner.inst_id, "bar": runner.bar,
            "strategyIds": runner.strategy_ids, "startTs": runner.start_ts, "endTs": runner.end_ts,
            "decisionEveryBars": runner.decision_every_bars, "plannedRounds": runner.planned_rounds,
            "initialCapital": runner.initial_capital, "leverage": runner.leverage,
            # 纪律预设回执（R-3）：回测与实盘吃同一份解析，回执也必须能逐字段核对——否则
            # 「我在回测里调好的那组阈值到底跑没跑」这个问题在回测侧无从回答。
            "discipline": {"preset": discipline.preset_id,
                           "minConsensus": runner.min_consensus,
                           "adjustments": discipline.adjustments},
            # 方向对称性回执（directional-symmetry §7.1）：与实盘同一解析点、同一形状。
            # 未装配 ⇒ `null`——回测侧也必须能回答「这一跑到底开没开逆势通路」，否则「开 vs 不开」
            # 的净额对照跑的是什么参数就说不清了。
            "direction": runner.direction_wire()}


@router.post("/ai-trade/backtest/{job_id}/cancel")
async def ai_backtest_cancel(job_id: str) -> dict:
    """协作式取消回测作业（不存在 → 404）；实际终态经 WS `cancelled` 事件驱动前端。"""
    outcome = get_ai_backtest_manager().cancel(job_id)
    if outcome == "unknown":
        raise HTTPException(404, detail={"code": "AI_BACKTEST_JOB_NOT_FOUND",
                                         "message": "无此回测任务"})
    job = get_ai_backtest_manager().get(job_id)
    return {"jobId": job_id, "state": job.state if job is not None else "cancelled"}


@router.get("/ai-trade/backtest/{job_id}")
async def ai_backtest_get(job_id: str) -> dict:
    """回测作业只读状态（前端重载后据此重发现 + 重连 WS）。"""
    job = get_ai_backtest_manager().get(job_id)
    if job is None:
        raise HTTPException(404, detail={"code": "AI_BACKTEST_JOB_NOT_FOUND",
                                         "message": "无此回测任务"})
    return {"job": job.status_dict()}


@router.websocket("/ws/ai-backtest/{job_id}")
async def ws_ai_backtest(websocket: WebSocket, job_id: str) -> None:
    """回测事件流（同信封 {type,data}，镜像 weight-train）：连接即原子 subscribe→snapshot 回放→续订新事件。"""
    if not await guard_ws_origin(websocket):   # 必须在 accept() 之前：不合规连接一帧都不许收到
        return
    await websocket.accept()
    job = get_ai_backtest_manager().get(job_id)
    if job is None:
        await websocket.send_text(json.dumps({
            "type": "error", "data": {"code": "AI_BACKTEST_JOB_NOT_FOUND",
                                      "message": "无此回测任务或已过期"}}))
        await websocket.close()
        return
    queue = job.subscribe()                       # subscribe() 与 snapshot_events() 间无 await：单线程下原子
    snapshot = job.snapshot_events()
    try:
        for event in snapshot:
            await websocket.send_text(json.dumps(event))
        if snapshot and snapshot[-1].get("type") in _BACKTEST_TERMINAL_TYPES:
            await websocket.close()               # 已终态（快作业先于连线结束）：回放完即关，绝不悬挂
            return
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=_WS_HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                await websocket.send_text(json.dumps({"type": "ping"}))
                if job.is_terminal:               # 满队列若丢终态：关闭前补发 result + 终态事件
                    tail = job.snapshot_events()
                    last_result = next((e for e in reversed(tail) if e.get("type") == "result"), None)
                    if last_result is not None:
                        await websocket.send_text(json.dumps(last_result))
                    if tail and tail[-1].get("type") in _BACKTEST_TERMINAL_TYPES:
                        await websocket.send_text(json.dumps(tail[-1]))
                    await websocket.close()
                    break
                continue
            await websocket.send_text(json.dumps(event))
            if event.get("type") in _BACKTEST_TERMINAL_TYPES:
                await websocket.close()
                break
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        job.unsubscribe(queue)
