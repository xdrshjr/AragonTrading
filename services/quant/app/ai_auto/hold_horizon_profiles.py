"""持仓周期预设的**只读展示 / 预览**装配（`GET /api/ai-trade/horizon-profiles` 的回执）。

从 `hold_horizon.py` 切出来的展示半边（切法与 `hold_horizon_text.py` 同款）：判定与状态推进留在
`hold_horizon`，「弹窗里给用户看什么」全部收在本模块。切分的直接动因是 CLAUDE.md 的 800 行红线
——`exit-priority` 一轮把 `hold_horizon.py` 从 796 推到 842（spec §十一 D-1 把这件事留给复核节点
决定），而这一块本就是它职责上最松的一块：没有任何判定逻辑，全是把已经算好的数摆成 wire。

依赖方向**单向**：本模块 import `hold_horizon` / `exit_priority` / `horizon_acceptance`，三者都
**不反向** import 本模块，故不成环（这也让 `hold_horizon` 里为绕环而写的两处函数内延迟导入在
本模块可以还原成正常的模块级导入）。

三条纪律与 `hold_horizon` 逐字相同：
1. **前端不复制任何一个数字或中文标签**——一张前后端各存一份的表必然漂移（R-3 / G-10）。
2. 预览值就是预览：`expectedHoldBars` / `confirmWindowSeconds` 按条目自带 bar 算，实际生效值按
   用户所选 bar 现算。
3. 阈值与派生式**只有一份真源**（`horizon_acceptance.resolve_thresholds` /
   `turnover_discipline` 的三个派生函数），本模块只调用、绝不复算。
"""
from __future__ import annotations

from .exit_priority import profiles_block as exit_profiles_block
from .hold_horizon import (
    PROFILES,
    HorizonProfile,
    HorizonRule,
    derive_confirm_window_s,
    derive_expected_hold_bars,
    derive_min_hold_s,
)
from .horizon_acceptance import resolve_thresholds
from .turnover_discipline import (
    derive_max_turnover_per_day,
    derive_reentry_cooldown_s,
    derive_turnover_capacity,
)


def profile_wire(profile: HorizonProfile, *, acceptance: dict | None = None,
                 turnover: dict | None = None) -> dict:
    """预设条目 → `AiTradeHorizonProfileOut` 形状（`custom` 除三个字符串外全为 `null`，[P1-8]）。

    `expectedHoldBars` / `confirmWindowSeconds` 是**按该条目自带 bar 的预览值**——实际生效值按用户
    所选 bar 现算（弹窗切换周期后会变），故前端必须把它们当预览而非承诺。

    `acceptance`（horizon-acceptance §4.1）：本模式将被按什么标准判定的**门槛预览**，由调用方从
    `horizon_acceptance.resolve_thresholds` 现算后传入——阈值的解析点只能有一个，本模块不复算。
    """
    bar = profile.bar
    return {"id": profile.id, "title": profile.title, "description": profile.description,
            "targetLabel": profile.target_label, "bar": bar,
            "wakeIntervalMinutes": profile.wake_interval_minutes,
            "minHoldSeconds": (None if profile.target_hold_min_s is None
                               else derive_min_hold_s(profile.target_hold_min_s)),
            "exitConfirmRounds": profile.exit_confirm_rounds,
            "turnoverHysteresisK": profile.hysteresis_k,
            "targetHoldMinSeconds": profile.target_hold_min_s,
            "targetHoldMaxSeconds": profile.target_hold_max_s,
            "expectedHoldBars": (None if bar is None
                                 else derive_expected_hold_bars(profile.target_hold_max_s, bar)),
            "confirmWindowSeconds": (None if bar is None
                                     else derive_confirm_window_s(profile.target_hold_min_s, bar)),
            "leverageMaxHint": profile.leverage_max_hint,
            "acceptance": acceptance,
            # 开仓侧换手纪律的三个**预览**值（turnover-budget §4.1）；`custom` 恒 `null`
            # （沿用 [P1-8]：那些格子是「用户填」，给出数字会让弹窗据一组无意义的值联动）。
            **(turnover or {"reentryCooldownSeconds": None,
                            "turnoverBudgetPerDay": None,
                            "turnoverBudgetCapacity": None})}


def profiles_wire(settings, *, estimated_cost_bps: float | None = None) -> dict:
    """`GET /api/ai-trade/horizon-profiles` 的完整回执（§4.1）——前端不复制任何一个数字或中文标签。"""
    return {"enabled": bool(settings.ai_trade_horizon_enabled),
            "defaultProfile": str(settings.ai_trade_horizon_default_profile),
            "estimatedCostBps": estimated_cost_bps,
            "lossReleaseFrac": float(settings.ai_trade_horizon_loss_release_frac),
            "lossReleaseReturnPctFallback": float(
                settings.ai_trade_horizon_loss_release_return_pct),
            # 三个只读运维标志（turnover-budget §4.1 / [P2-4]）：UI 据此如实说明「这两道闸是否
            # 真的开着 / 多久向交易所核实一次」。第三个尤其重要——弹窗那句「每 N 分钟核实一次」
            # 若硬抄配置值，运维改了配置而文案不动，就又造出一句没核实的话。
            "turnoverGateEnabled": bool(settings.ai_trade_horizon_turnover_enabled),
            "backstopCheckEnabled": bool(settings.ai_trade_backstop_check_enabled),
            "backstopPollSeconds": float(settings.ai_trade_backstop_poll_ttl_s),
            "backstopRepair": _backstop_repair_wire(settings),
            # 离场优先通道的只读说明块（exit-priority §6.2）；总闸关 ⇒ `None` ⇒ 前端整块不渲染。
            "exitPriority": exit_profiles_block(settings),
            "profiles": [profile_wire(PROFILES[key], acceptance=_acceptance_preview(
                settings, PROFILES[key]), turnover=_turnover_preview(
                settings, PROFILES[key])) for key in PROFILES]}


def _backstop_repair_wire(settings) -> dict | None:
    """自愈控件的下发块（backstop-repair §4.2）；运维总闸关 ⇒ `None` ⇒ 前端整块不渲染。

    标签、说明、缺省模式与两个上限**全部由后端下发**，前端一个字、一个数都不硬编（R-3 / G-10
    的成文教训：一张前后端各存一份的表必然漂移）。「自动补挂」那句 detail 里必须写清授权含义
    ——那是本轮唯一一处「系统会自己花钱」的授权点。
    """
    if not getattr(settings, "ai_trade_backstop_repair_enabled", False):
        return None
    return {
        "defaultMode": str(settings.ai_trade_backstop_repair_default_mode),
        "modes": [
            {"id": "plan", "label": "仅提醒",
             "detail": "系统只给出补挂方案，由你一键执行"},
            {"id": "arm", "label": "自动补挂",
             "detail": "核实到止损缺失时由系统自动补挂，本会话将允许系统在你不在场时发出止损委托"},
        ],
        "note": ("补挂只补止损腿、只补差额张数、永不平仓、永不加仓；"
                 "现价已越过止损价时拒绝补挂。"),
        "maxPerEpoch": int(settings.ai_trade_backstop_repair_max_per_epoch),
        "maxPerSession": int(settings.ai_trade_backstop_repair_max_per_session),
    }


def _turnover_preview(settings, profile: HorizonProfile) -> dict:
    """本模式的开仓侧换手纪律预览（turnover-budget §4.1）。

    与 `_acceptance_preview` 同款姿态：数字全部由后端按**唯一那份派生式**现算，前端一个都不算
    （R-3 / G-10）。`custom` 未填目标区间 ⇒ 三个值恒 `null` ⇒ 弹窗不渲染那两行契约摘要。
    """
    target_min = profile.target_hold_min_s
    if target_min is None or float(target_min) <= 0:
        return {"reentryCooldownSeconds": None, "turnoverBudgetPerDay": None,
                "turnoverBudgetCapacity": None}
    per_day = derive_max_turnover_per_day(target_min)
    return {"reentryCooldownSeconds": derive_reentry_cooldown_s(
                derive_min_hold_s(target_min),
                frac=float(settings.ai_trade_horizon_reentry_frac)),
            "turnoverBudgetPerDay": per_day,
            "turnoverBudgetCapacity": derive_turnover_capacity(
                per_day, burst_frac=float(settings.ai_trade_horizon_turnover_burst_frac),
                min_capacity=float(settings.ai_trade_horizon_turnover_min_capacity))}


def _acceptance_preview(settings, profile: HorizonProfile) -> dict:
    """本模式的验收门槛预览（horizon-acceptance §4.1）。

    阈值一律由 `horizon_acceptance.resolve_thresholds` 现算——弹窗里写的门槛与会话跑起来之后真正
    据以判定的门槛必须是同一个数，前端与本模块都不许自己再乘一遍（本仓库已为「前后端各存一份口径
    静默漂移」出过事故）。`custom` 未填目标区间 ⇒ 派生阈为 `null` ⇒ 前端显示「按你填的区间派生」。
    """
    th = resolve_thresholds(settings, HorizonRule(
        target_hold_min_s=float(profile.target_hold_min_s or 0.0),
        target_hold_max_s=float(profile.target_hold_max_s or 0.0)))
    return {"sampleFloor": th.sample_floor, "minAvgHoldSeconds": th.min_avg_hold_s,
            "minWithinTargetRate": th.min_within_target_rate,
            "maxTurnoverPerDay": th.max_turnover_per_day,
            "maxFeeToGross": th.max_fee_to_gross,
            "requiresPositiveNetAfterFunding": True}
