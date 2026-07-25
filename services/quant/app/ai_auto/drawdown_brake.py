"""三级回撤刹车与连亏熔断的**纯核心**（ai-trade-vol-sizing-and-drawdown-brake §3.6）。

仓库唯一的止损机制此前是会话级绝对金额阈值 `_maybe_session_exit`——那是**终局开关而非减速带**：在它
触发之前，连亏五笔与连亏一笔的系统行为完全相同。本模块补的就是那条减速带：

| 档 | 触发 | 动作 | 解除 |
|---|---|---|---|
| 1 · 减速 | `dd ≥ slow_dd_pct` | 仓位系数 ×`slow_size_mult`（默认 0.5） | `dd < slow_dd_pct` 自动恢复 |
| 2 · 只平不开 | `dd ≥ halt_dd_pct` | 开仓硬降级 hold | **滞回**：跌回 `slow_dd_pct` 以下才解除 |
| 3 · 连亏冷却 | 连续亏损平仓 ≥ `loss_streak` | 开仓硬降级 hold + 计数归零 | 墙钟 `cooldown_until_wall` 到期 |

三条必须先读的约定：

1. **只拦开仓（S-9）**：动作白名单与 `edge_gate._GATED_ACTIONS` 逐字同构，`close` / `cancel_order` /
   `hold` 永不被触碰。利润保护层的平仓走 `_maybe_profit_exit`、根本不经决策管线，物理上不可能被本层拦住。
2. **`dd` 是正号口径**（`1 − equity/peak`，0.08 = 回撤 8%）。既有 `equity` 事件里的 `drawdown` 字段是
   **负号**口径（`min(dd, 0.0)`），两者绝不可互相赋值。
3. **权益含未实现盈亏**（`equity = budget + realized + unrealized`），故峰值会随**浮盈**推进——而利润
   保护层**按设计允许**浮盈自峰值回吐 `giveback_pct` 才离场。那段「设计内的回吐」在本层眼里就是一次
   实打实的会话回撤：两层在浮盈区间上目标直接冲突（保护层让利润跑，刹车因为让它跑而收紧下一笔）。
   **仍然保留 mark-to-market 口径**——只按已实现算会让刹车在最需要它的时刻（一个正在扩大的浮亏持仓）
   完全瞎掉，那比误触严重得多。冲突用 `unrealizedShare` 读数兜住：halt 触发轮的该值长期 > 0.8 说明
   刹车几乎只被浮盈回吐触发，正确动作是**上调 `halt_dd_pct`**，而不是关掉利润保护层。

**阈值必须按杠杆定标**：`session_pnl_usd` 落在**已含杠杆**的名义额上，而 `dd` 的分母是保证金口径的
`budget`，故标的价波动被放大 `leverage` 倍才进 `dd`。3× 杠杆下 8%/15% 等价于标的价反向 2.7%/5.0%
——BTC 15m 一天内走出 3% 回调是常态。建议生产值以 3× 为锚点按 `× 3/leverage` 折算（见 `.env.example`）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

from .decider import TradeDecision

# 受刹车约束的动作（与 `edge_gate._GATED_ACTIONS` / `sizing._GATED_ACTIONS` 逐字同构，S-2/S-9）。
_GATED_ACTIONS = ("open_long", "open_short")

_LEVEL_NONE = "none"
_LEVEL_SLOW = "slow"
_LEVEL_HALT = "halt"
_LEVEL_COOLDOWN = "cooldown"

# 第二档滞回带的回落阈值：第一档关闭时取 halt 的一半（纯锁存会造出「活着但永不开仓」的僵尸会话，
# 纯阈值又会在阈值附近反复抖动；滞回带是两者之间唯一正确的选择）。
_HALT_RELEASE_RATIO = 0.5


@dataclass(frozen=True)
class BrakeRule:
    """三级刹车规则（三档**各自独立**关闭；全关 ⇒ `parse_brake_rule` 返回 None ⇒ 本层整体不装配）。

    Attributes:
        slow_dd_pct: 第一档减速的回撤阈值（正号口径）；`0` ⇒ 第一档关闭。
        slow_size_mult: 第一档的仓位系数（默认 0.5）；**注意档位地板可能整个吃掉它**（[P1-2]）。
        halt_dd_pct: 第二档「只平不开」的回撤阈值；`0` ⇒ 第二档关闭。
        loss_streak: 连亏熔断笔数；`0` ⇒ 第三档关闭。
        loss_cooldown_s: 连亏冷却时长（秒 = `loss_cooldown_bars × BAR_SECONDS[bar]`）。
    """

    slow_dd_pct: float
    slow_size_mult: float
    halt_dd_pct: float
    loss_streak: int
    loss_cooldown_s: float


@dataclass(frozen=True)
class BrakeState:
    """会话存续期的刹车状态 + 观测计数（不落库；聚合读数经 `metricsJson.aiStats.sizing` 落库）。"""

    peak_equity: float
    equity: float
    loss_streak: int = 0
    halted: bool = False                    # 第二档滞回锁存
    cooldown_until_wall: float = 0.0
    max_dd: float = 0.0
    unrealized_share: float | None = None   # 最近一次权益推进的浮盈占比（[P1-5] 的观测量）


@dataclass(frozen=True)
class BrakeView:
    """一轮的刹车判定（只读；`mult` 只作用于开新仓，`reason` 非 None 即本轮开仓被硬降级）。"""

    level: str                              # none | slow | halt | cooldown
    mult: float                             # 仓位系数 ∈ (0,1]
    reason: str | None                      # 降级码（带参数后缀，与 `edge_below_cost` 同风格）
    drawdown_pct: float
    loss_streak: int
    peak_equity: float
    unrealized_share: float | None = None
    cooldown_remaining_s: float = 0.0


@dataclass
class BrakeCounters:
    """会话级累计观测（`sizing` 统计的一半；另一半在 `SizingRuntime`）。"""

    slow_rounds: int = 0
    halt_rounds: int = 0
    cooldown_hits: int = 0
    halt_unrealized_shares: list[float] = field(default_factory=list)


def parse_brake_rule(settings, bar_seconds: int) -> BrakeRule | None:
    """三档全部取关闭值 ⇒ `None`（本层不装配，`brake_mult ≡ 1.0`、门禁直通、零事件）。"""
    slow = max(0.0, float(getattr(settings, "ai_trade_brake_slow_dd_pct", 0.0) or 0.0))
    halt = max(0.0, float(getattr(settings, "ai_trade_brake_halt_dd_pct", 0.0) or 0.0))
    streak = max(0, int(getattr(settings, "ai_trade_brake_loss_streak", 0) or 0))
    if slow <= 0 and halt <= 0 and streak <= 0:
        return None
    bars = max(0.0, float(getattr(settings, "ai_trade_brake_loss_cooldown_bars", 0.0) or 0.0))
    return BrakeRule(
        slow_dd_pct=slow, halt_dd_pct=halt, loss_streak=streak,
        slow_size_mult=_clamp01(getattr(settings, "ai_trade_brake_slow_size_mult", 1.0)),
        loss_cooldown_s=bars * max(1, int(bar_seconds)))


def initial_state(budget_usd: float) -> BrakeState:
    """会话起点：峰值 = 预算（与 `AiTradeSession._peak_equity` 的既有起点逐字相同）。"""
    start = _finite(budget_usd) or 0.0
    return BrakeState(peak_equity=start, equity=start)


def advance_equity(state: BrakeState, equity: float, rule: BrakeRule,
                   *, unrealized_share: float | None = None) -> BrakeState:
    """推进峰值 / 当前权益 / 最大回撤，并按滞回带推进第二档锁存。

    滞回：`dd ≥ halt_dd_pct` 置 `halted`；`dd ≤ 解除线`（第一档开时取 `slow_dd_pct`，关时取
    `halt_dd_pct/2`）才清除。**绝不**在跨过阈值后一直锁到会话结束——那会造出「活着但永远不开仓」的
    僵尸会话（本仓库有过「反思致永不开单」的同类事故）。
    """
    value = _finite(equity)
    if value is None:
        return state
    peak = max(state.peak_equity, value)
    dd = _drawdown(value, peak)
    halted = state.halted
    if rule.halt_dd_pct > 0:
        if dd >= rule.halt_dd_pct:
            halted = True
        elif halted and dd <= _halt_release(rule):
            halted = False
    else:
        halted = False
    return replace(state, peak_equity=peak, equity=value, halted=halted,
                   max_dd=max(state.max_dd, dd),
                   unrealized_share=(state.unrealized_share if unrealized_share is None
                                     else unrealized_share))


def note_close(state: BrakeState, pnl, rule: BrakeRule, *, now_wall: float) -> BrakeState:
    """一笔平仓成交推进连亏计数；达阈即开冷却并把计数归零。

    判据用 `trade["pnl"]`（A0-2 开启时即净额、关闭时为毛额——两种口径下「亏」的语义都成立，无需分叉）。
    三分支不留二义：`pnl > 0` 归零 / `pnl < 0` 累加 / `pnl == 0` **不动**（平局不该算连亏，也不该清算
    已有连亏）。
    """
    value = _finite(pnl)
    if value is None or value == 0:
        return state
    if value > 0:
        return replace(state, loss_streak=0)
    streak = state.loss_streak + 1
    if rule.loss_streak > 0 and streak >= rule.loss_streak:
        return replace(state, loss_streak=0,
                       cooldown_until_wall=float(now_wall) + rule.loss_cooldown_s)
    return replace(state, loss_streak=streak)


def evaluate_brake(state: BrakeState, rule: BrakeRule, *, now_wall: float) -> BrakeView:
    """本轮判定：先看第二档锁存，再看冷却，最后看第一档减速（严重者优先呈现）。"""
    dd = _drawdown(state.equity, state.peak_equity)
    remaining = max(0.0, state.cooldown_until_wall - float(now_wall))
    common = {"drawdown_pct": round(dd, 6), "loss_streak": state.loss_streak,
              "peak_equity": state.peak_equity, "unrealized_share": state.unrealized_share,
              "cooldown_remaining_s": round(remaining, 3)}
    if state.halted:
        return BrakeView(level=_LEVEL_HALT, mult=1.0,
                         reason=f"drawdown_brake_halt(dd={dd * 100:.1f}%)", **common)
    if remaining > 0:
        return BrakeView(level=_LEVEL_COOLDOWN, mult=1.0,
                         reason=(f"loss_streak_cooldown({rule.loss_streak},"
                                 f"{rule.loss_cooldown_s:.0f}s)"), **common)
    if rule.slow_dd_pct > 0 and dd >= rule.slow_dd_pct:
        return BrakeView(level=_LEVEL_SLOW, mult=rule.slow_size_mult, reason=None, **common)
    return BrakeView(level=_LEVEL_NONE, mult=1.0, reason=None, **common)


def apply_brake_gate(decision: TradeDecision,
                     view: BrakeView) -> tuple[TradeDecision, str | None]:
    """第二 / 第三档把开仓硬降级为 hold；其余一律返回**同一个** decision 对象（`is` 相等）。

    第一档只改仓位系数（由 `sizing.resolve_size_fraction` 消费），**不经本函数**——减速不是否决。
    """
    if view.reason is None or decision.action not in _GATED_ACTIONS:
        return decision, None
    return TradeDecision(action="hold", size_fraction=None, confidence=None,
                         reason=view.reason), view.reason


def brake_wire(view: BrakeView | None) -> dict:
    """`BrakeView` → camelCase 只读读数；未装配 ⇒ 四个键取「本层不生效」的诚实值。

    `peakEquityBasis` 恒为 `markToMarket`（[P1-5] 的显式声明）——口径必须在 UI 上写出来，否则
    「刹车为什么在我刚赚完钱之后触发」永远说不清。
    """
    if view is None:
        return {"brakeLevel": _LEVEL_NONE, "brakeReason": None, "drawdownPct": None,
                "lossStreak": None, "peakEquityBasis": None, "unrealizedShare": None}
    return {"brakeLevel": view.level, "brakeReason": view.reason,
            "drawdownPct": view.drawdown_pct, "lossStreak": view.loss_streak,
            "peakEquityBasis": "markToMarket", "unrealizedShare": view.unrealized_share}


def unrealized_share(unrealized_usd, session_pnl_usd) -> float | None:
    """浮盈占比 `unrealized / max(|session_pnl|, ε)`（[P1-5] 的观测量）；不可算 ⇒ None（绝不填 0）。"""
    unreal = _finite(unrealized_usd)
    pnl = _finite(session_pnl_usd)
    if unreal is None or pnl is None:
        return None
    denom = max(abs(pnl), 1e-9)
    return round(unreal / denom, 6)


def _halt_release(rule: BrakeRule) -> float:
    """第二档滞回带的解除线：第一档开着就用它，否则取 halt 的一半。"""
    return rule.slow_dd_pct if rule.slow_dd_pct > 0 else rule.halt_dd_pct * _HALT_RELEASE_RATIO


def _drawdown(equity: float, peak: float) -> float:
    """**正号**回撤 `1 − equity/peak`；峰值非正 ⇒ 0.0（绝不产出无穷或负零噪音）。"""
    if peak <= 0:
        return 0.0
    return max(0.0, 1.0 - equity / peak)


def _finite(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _clamp01(value) -> float:
    out = _finite(value)
    if out is None:
        return 1.0
    return 0.0 if out < 0 else 1.0 if out > 1 else out
