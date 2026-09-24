"""成本锚定收益锁的纯核心（ai-trade-profit-lock-and-entry-unblock 轴 B）。

为什么需要它：生产库 2026-08 以来 131 笔往返里，46 笔 Agent 自主离场的单子曾经浮盈、最后以亏损
离场，回吐 100.4 USDT（超过同期全部净亏损）。这些单子峰值浮盈四分位只有 11 / 20 / 32 bps，而既有
利润保护层按**保证金收益率**口径武装（5× 下保本线要 60 bps、移动止盈要 100 bps），结构上看不见
这个尺度。收益锁以**价格 bps** 计、以本笔往返成本为锚：

- 阶段 1（保本锁）：峰值浮盈 ≥ max(arm_cost_mult × 成本, arm_atr_mult × ATR) ⇒ 离场线抬到
  「含费保本 + buffer」；
- 阶段 2（利润锁）：峰值浮盈 ≥ max(trail_cost_mult × 成本, trail_atr_mult × ATR) ⇒ 离场线 =
  entry ± keep_ratio × 峰值浮盈（不低于阶段 1 线）；
- 锁线按方向**单调棘轮**；mark 越过锁线且**按墙钟**持续 ≥ confirm_s 才触发。

确认按墙钟而不是调用次数：`_maybe_profit_exit` 有两个调用点（盯市 tick 与 `_post_round`），同一秒内
可能连判两次，「连续 N 次」数的不是时间。本模块全部是纯函数，无 IO、无时钟读取。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

REASON_LOCK_BREAKEVEN = "profit_lock_breakeven"
REASON_LOCK_TRAILING = "profit_lock_trailing"

LOCK_LABELS = {REASON_LOCK_BREAKEVEN: "保本锁", REASON_LOCK_TRAILING: "利润锁"}

# 武装瞬间 mark 与锁线之间的最小间距（bps）。不变式「武装那一刻 mark 必在锁线上方」不能靠默认参数
# 碰运气（buffer 可以被调大到接近 arm 阈值），故写成硬约束且不开放配置。
MIN_GAP_BPS = 3.0

STATUS_WATCHING = "watching"
STATUS_BREAKEVEN = "breakeven"
STATUS_TRAILING = "trailing"
STATUS_UNAVAILABLE = "unavailable"
_STAGE_STATUS = {0: STATUS_WATCHING, 1: STATUS_BREAKEVEN, 2: STATUS_TRAILING}


@dataclass(frozen=True)
class ProfitLockRule:
    """收益锁规则（价格 bps 口径，与杠杆无关）。"""

    enabled: bool = True
    arm_cost_mult: float = 1.5
    arm_atr_mult: float = 0.35
    buffer_bps: float = 2.0
    trail_cost_mult: float = 3.0
    trail_atr_mult: float = 0.8
    keep_ratio: float = 0.5
    min_hold_s: float = 60.0
    confirm_s: float = 8.0


@dataclass(frozen=True)
class LockFacts:
    """一次评估的断面（聚合结构体）。

    Attributes:
        side: `long` | `short`。
        entry_px: 持仓均价。
        peak_px: 方向化峰值价——取 `ProfitGuardState.peak_px`（同一个 epoch 峰值，不另算第二份）。
        mark_px: 判定价（实盘为 `_current_px()`，回测为本根极值）。
        opened_wall: epoch 起始墙钟。
        now_wall: 当前墙钟（回测为 bar ts）。
        cost_bps: 按持仓方向估的往返成本；None ⇒ 不可判定（fail-open）。
        atr_bps: ATR 的 bps 表示；None ⇒ ATR 项按 0 处理。
        qty_coin: 持仓币量（只用于平仓回执里的 USD 展示量）。
    """

    side: str
    entry_px: float
    peak_px: float
    mark_px: float
    opened_wall: float
    now_wall: float
    cost_bps: float | None
    atr_bps: float | None = None
    qty_coin: float = 0.0


@dataclass(frozen=True)
class LockState:
    """epoch 作用域状态：只存棘轮后的锁线、阶段与越线起点。"""

    epoch: tuple
    lock_px: float | None = None
    stage: int = 0
    breach_since_wall: float | None = None


@dataclass(frozen=True)
class LockVerdict:
    """触发裁决；与 `ProtectVerdict` 同名同形，供会话复用 `_apply_profit_exit`。"""

    reason_code: str
    label: str
    note: dict


@dataclass(frozen=True)
class _Thresholds:
    mfe_bps: float
    arm_bps: float
    trail_arm_bps: float
    floor_bps: float


def _sign(side: str) -> float:
    return 1.0 if side == "long" else -1.0


def _finite(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _thresholds(rule: ProfitLockRule, facts: LockFacts) -> _Thresholds | None:
    """现算 MFE 与两级门槛；成本不可算或断面非法 ⇒ None。"""
    cost = _finite(facts.cost_bps)
    entry = _finite(facts.entry_px)
    if cost is None or entry is None or entry <= 0 or facts.side not in ("long", "short"):
        return None
    atr = max(0.0, _finite(facts.atr_bps) or 0.0)
    mfe = _sign(facts.side) * (facts.peak_px - entry) / entry * 1e4
    return _Thresholds(
        mfe_bps=mfe,
        arm_bps=max(rule.arm_cost_mult * cost, rule.arm_atr_mult * atr),
        trail_arm_bps=max(rule.trail_cost_mult * cost, rule.trail_atr_mult * atr),
        floor_bps=cost + rule.buffer_bps)


def _stage1_at(th: _Thresholds) -> float:
    """阶段 1 实际武装点：不低于「含费保本 + MIN_GAP」，武装瞬间 mark 必在锁线上方。"""
    return max(th.arm_bps, th.floor_bps + MIN_GAP_BPS)


def _px_at(facts: LockFacts, bps: float) -> float:
    return facts.entry_px * (1.0 + _sign(facts.side) * bps / 1e4)


def _tighter(side: str, prev: float | None, cand: float) -> float:
    """单调棘轮：long 只升、short 只降。"""
    if prev is None:
        return cand
    return max(prev, cand) if side == "long" else min(prev, cand)


def derive_lock(rule: ProfitLockRule, facts: LockFacts, prev: LockState) -> LockState:
    """算两级门槛与候选锁线，按方向单调棘轮；成本不可算 ⇒ 原样返回。"""
    th = _thresholds(rule, facts)
    if th is None:
        return prev
    stage1_at = _stage1_at(th)
    if th.mfe_bps < stage1_at:
        return prev
    stage, lock_bps = 1, th.floor_bps
    if th.mfe_bps >= th.trail_arm_bps:
        stage, lock_bps = 2, max(th.floor_bps, rule.keep_ratio * th.mfe_bps)
    return replace(prev, lock_px=_tighter(facts.side, prev.lock_px, _px_at(facts, lock_bps)),
                   stage=max(prev.stage, stage))


def reset_breach(state: LockState) -> LockState:
    """stale / 成本不可判定时调用：越线起点清零，锁线与 stage 保留。

    只「不推进」是不够的：断连前留下的越线起点加上恢复后的一次越线，会被当成「已持续 ≥ confirm_s」。
    """
    return replace(state, breach_since_wall=None)


def is_touched(side: str, mark_px: float, lock_px: float | None) -> bool:
    """mark 越过锁线（含等于）；锁线缺失 ⇒ 未越线。"""
    if lock_px is None or not math.isfinite(mark_px):
        return False
    return mark_px <= lock_px if side == "long" else mark_px >= lock_px


def _usd(facts: LockFacts, px: float) -> float:
    return facts.qty_coin * _sign(facts.side) * (px - facts.entry_px)


def build_verdict(facts: LockFacts, state: LockState) -> LockVerdict:
    """按当前阶段组装裁决（note 形状与利润保护的 `_verdict` 同键）。"""
    code = REASON_LOCK_TRAILING if state.stage >= 2 else REASON_LOCK_BREAKEVEN
    label = LOCK_LABELS[code]
    return LockVerdict(code, label, {
        "reasonCode": code, "label": label, "side": facts.side, "entryPx": facts.entry_px,
        "exitPx": state.lock_px, "peakPx": facts.peak_px,
        "pnlUsd": _usd(facts, facts.mark_px), "peakPnlUsd": _usd(facts, facts.peak_px),
        "lockStage": state.stage})


def evaluate_lock(rule: ProfitLockRule, facts: LockFacts,
                  state: LockState) -> tuple[LockState, LockVerdict | None]:
    """推进锁线并判定是否触发（墙钟确认）。

    min_hold 内不判定、不记越线起点，但照常推进锁线；任一次「未越线」⇒ 越线起点归 None；
    首次越线只记起点，`confirm_s <= 0` ⇒ 首次越线即触发。
    """
    state = derive_lock(rule, facts, state)
    held = facts.now_wall - facts.opened_wall
    if held < rule.min_hold_s or not is_touched(facts.side, facts.mark_px, state.lock_px):
        return reset_breach(state), None
    if state.breach_since_wall is None:
        state = replace(state, breach_since_wall=facts.now_wall)
    if facts.now_wall - state.breach_since_wall >= rule.confirm_s:
        return state, build_verdict(facts, state)
    return state, None


def lock_wire(rule: ProfitLockRule, facts: LockFacts, state: LockState, *,
              unavailable: str | None = None) -> dict:
    """camelCase 快照（spec §5.2 形状），供持仓行与 status 使用。"""
    th = _thresholds(rule, facts)
    lock_net = None
    if state.lock_px is not None and th is not None:
        lock_bps = _sign(facts.side) * (state.lock_px - facts.entry_px) / facts.entry_px * 1e4
        lock_net = round(lock_bps - (th.floor_bps - rule.buffer_bps), 2)
    held = facts.now_wall - facts.opened_wall
    reason = unavailable or ("min_hold" if held < rule.min_hold_s else None)
    breach = (0.0 if state.breach_since_wall is None
              else round(max(0.0, facts.now_wall - state.breach_since_wall), 1))
    return {
        "stage": state.stage,
        "status": STATUS_UNAVAILABLE if unavailable else _STAGE_STATUS.get(state.stage),
        "unavailableReason": reason, "side": facts.side, "entryPx": facts.entry_px,
        "peakPx": facts.peak_px,
        "mfeBps": None if th is None else round(th.mfe_bps, 2),
        "costBps": None if facts.cost_bps is None else round(facts.cost_bps, 2),
        "atrBps": None if facts.atr_bps is None else round(facts.atr_bps, 2),
        "armBps": None if th is None else round(_stage1_at(th), 2),
        # 阶段 2 只在阶段 1 之后生效（`derive_lock` 先判阶段 1），故展示值不低于 armBps。
        "trailArmBps": None if th is None else round(max(th.trail_arm_bps, _stage1_at(th)), 2),
        "lockPx": state.lock_px, "lockNetBps": lock_net,
        "breachSeconds": breach, "confirmSeconds": rule.confirm_s}


def lock_rule_wire(rule: ProfitLockRule | None) -> dict | None:
    """规则的 camelCase wire（group_status / config_json）；未装配 ⇒ None。"""
    if rule is None:
        return None
    return {"enabled": rule.enabled, "armCostMult": rule.arm_cost_mult,
            "armAtrMult": rule.arm_atr_mult, "bufferBps": rule.buffer_bps,
            "trailCostMult": rule.trail_cost_mult, "trailAtrMult": rule.trail_atr_mult,
            "keepRatio": rule.keep_ratio, "minHoldSeconds": rule.min_hold_s,
            "confirmSeconds": rule.confirm_s}


_WIRE_TO_SETTING = (
    ("armCostMult", "arm_cost_mult"), ("armAtrMult", "arm_atr_mult"),
    ("bufferBps", "buffer_bps"), ("trailCostMult", "trail_cost_mult"),
    ("trailAtrMult", "trail_atr_mult"), ("keepRatio", "keep_ratio"),
    ("minHoldSeconds", "min_hold_s"), ("confirmSeconds", "confirm_s"),
)


def resolve_profit_lock_rule(raw: dict | None, settings) -> ProfitLockRule | None:
    """请求体 → 规则。运维总闸关 ⇒ None；显式 `enabled=false` ⇒ None；缺省 ⇒ 按 settings 开启。

    Raises:
        ValueError: `trailCostMult < armCostMult`（API 层转 422，文案含字段名与收到的值）。
    """
    if not getattr(settings, "ai_trade_profit_lock_enabled", True):
        return None
    raw = raw or {}
    if raw.get("enabled") is False:
        return None
    values = {}
    for wire_key, name in _WIRE_TO_SETTING:
        given = raw.get(wire_key)
        values[name] = float(getattr(settings, f"ai_trade_profit_lock_{name}")
                             if given is None else given)
    if values["trail_cost_mult"] < values["arm_cost_mult"]:
        raise ValueError(
            "profitLock.trailCostMult 必须 ≥ armCostMult（收到 "
            f"{values['trail_cost_mult']:g} < {values['arm_cost_mult']:g}）")
    return ProfitLockRule(enabled=True, **values)
