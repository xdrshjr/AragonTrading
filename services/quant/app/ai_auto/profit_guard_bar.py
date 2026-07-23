"""AI 回测侧的利润保护判定（ai-trade-profit-protection-adaptive §2.3 / F3）。

第 1 轮把「AI 回测接入保护层」列为非目标，理由是「按根推进算不出 bar 内峰值，用收盘价近似 = 假数据」。
这个理由对**收盘价近似**成立，但对 **bar 内 OHLC 极值 + 保守成交序**不成立——`engine/simulator.py`
的 `apply_risk` 早就用同一套办法处理止损止盈（`h`/`l` 触发、止损优先、触发价成交）。现状的真实代价是
回测与实盘**系统性不同构**，用户拿回测调出来的参数在实盘直接失真。

本模块**只调用 `profit_guard` 的既有纯函数**（`advance_state` / `build_snapshot` /
`evaluate_profit_guard`），零逻辑复制——第二套状态机迟早与实盘漂移，而它要决定用户看到的曲线。

保守成交序（本特性最容易做错的地方，§2.3.1）：

0. **epoch 初始化**：`state` 缺失 / 身份不符 ⇒ 先用 `mark_px = entry_px` 建 state 并**跳过本根的
   回撤判定**（与 `min_hold_s` 在 bar 时钟下的效果一致，见下）。
1. **先判回撤**：用 `adverse`（long 取 `l`，short 取 `h`）组装快照、`state` **不推进**，
   只允许移动止盈与保本两条下方线。
2. **再推峰值**：未命中才用 `favorable`（long 取 `h`）推进 `peak`，只允许硬止盈。
3. 都未命中 ⇒ 本根无动作，`peak` 已推进到 `favorable`，供下一根使用。

这个顺序意味着**同一根内刚创出的新高不会立刻抬高移动止盈线**：现实中价格若先冲高再回落触及被抬高
后的线，会以更好的价格出场；本模型不给这个便宜，因此**系统性低估**保护层收益——这正是「保守」的
定义，也是允许它进回测的前提（与收盘价近似的**高估 / 失真**有本质区别）。
"""
from __future__ import annotations

from dataclasses import dataclass

from .profit_guard import (
    REASON_BREAKEVEN,
    REASON_HARD_TAKE_PROFIT,
    REASON_TRAILING,
    PositionFacts,
    ProfitGuardRule,
    ProfitGuardState,
    ProtectVerdict,
    advance_state,
    build_snapshot,
    evaluate_profit_guard,
)

# 第 1 步只判两条**下方**保护线（long 语义；short 对称），第 2 步只判上方目标位。
_DRAWDOWN_RULES = (REASON_TRAILING, REASON_BREAKEVEN)
_TARGET_RULES = (REASON_HARD_TAKE_PROFIT,)


@dataclass(frozen=True)
class BarFacts:
    """第 `i` 根上评估保护层所需的全部断面（聚合结构体，满足 CLAUDE.md ≤5 形参）。

    Attributes:
        seq: 持仓周期 id；回测取 `SimBroker.pos.entry_bar`（单持仓、无部分成交，天然唯一）。
        side: `long` | `short`。
        entry_px: 持仓均价（`pos.entry_price`）。
        qty_coin: 持仓币量（`pos.qty`），用于本地派生浮盈展示量。
        leverage: 账户杠杆（保证金收益率口径换算基准）。
        ts: 本根 ts——**回测把 bar ts 当墙钟**（§2.3.2），`min_hold_s` 一字不改地复用。
        opened_ts: 开仓根 ts（`state.opened_wall` 的来源）。
        open_px / high_px / low_px: 本根 OHL（跳空成交价规则用）。
        atr_px: 本根可用的 ATR，取 `iloc[i-1]`（含本根 h/l 的 ATR 是未来函数）；None ⇒ 上下界不生效。
    """

    seq: int
    side: str
    entry_px: float
    qty_coin: float
    leverage: float
    ts: int
    opened_ts: int
    open_px: float
    high_px: float
    low_px: float
    atr_px: float | None = None


@dataclass(frozen=True)
class BarProtectResult:
    """一根上的保护层判定结果：`verdict` 非空即应按 `fill_px` 平仓；`state` 恒为推进后的新状态。"""

    state: ProfitGuardState
    verdict: ProtectVerdict | None = None
    fill_px: float | None = None


def _facts(bar: BarFacts, mark_px: float) -> PositionFacts:
    """把 `BarFacts` + 指定判定价装配成纯核心要的 `PositionFacts`（bar ts 充当墙钟）。"""
    return PositionFacts(
        seq=bar.seq, side=bar.side, entry_px=bar.entry_px, leverage=bar.leverage,
        qty_coin=bar.qty_coin, mark_px=mark_px, now_wall=float(bar.ts),
        atr_px=bar.atr_px)


def _is_new_epoch(state: ProfitGuardState | None, bar: BarFacts) -> bool:
    """epoch 身份判定与实盘同一条判据（`state is None` / 反手 / `seq` 变化）。"""
    return state is None or state.side != bar.side or state.seq != bar.seq


def _fill_px(line: float, open_px: float, *, is_downside_line: bool) -> float:
    """跳空处理：触发价即成交价，但**绝不给出「这根从未成交过」的价格**（§2.3.1）。

    下方线（long 的保护线 / short 的硬止盈）跳空穿越 ⇒ 只能按开盘价成交，取 `min(line, open)`；
    上方线取 `max(line, open)`。滑点与手续费由 `SimBroker._close_at` 既有逻辑照常施加。
    """
    return min(line, open_px) if is_downside_line else max(line, open_px)


def _verdict_fill_px(bar: BarFacts, verdict: ProtectVerdict) -> float:
    """按「触发的是哪条线 + 持仓方向」决定跳空成交价（四种组合，§2.3.1 的成交价表）。"""
    line = float(verdict.note.get("exitPx") or bar.open_px)
    is_target = verdict.reason_code == REASON_HARD_TAKE_PROFIT
    # long 的保护线在下方、硬止盈在上方；short 全部反过来。
    is_downside_line = (bar.side == "long") != is_target
    return _fill_px(line, bar.open_px, is_downside_line=is_downside_line)


def evaluate_bar(state: ProfitGuardState | None, rule: ProfitGuardRule,
                 bar: BarFacts) -> BarProtectResult:
    """一根上的保守保护判定（先判回撤、后推峰值）；未命中即只把 `peak` 推进到本根有利极值。

    **开仓根恒不触发**（§2.3.2 / P1-3）：`opened_wall == now_wall` ⇒ `holdSeconds = 0 <
    min_hold_s(默认 30)`，最早可触发的是次根（15m ⇒ 900s）。这不是额外的保守假设，而是把「开仓根
    不判回撤」这条既有语义在 bar 时钟下写清楚——允许同根强平等于在一根内完成一次零信息量的往返、
    只付两笔手续费。若某天真要在开仓根生效，唯一正确的做法是把 `min_hold_s` 显式设为 0。

    Args:
        state: 上一根遗留的状态；`None` / 身份不符 ⇒ 本根先建 state 并跳过回撤判定（第 0 步）。
        rule: 已装配规则（回测与实盘同一 dataclass、同一份参数）。
        bar: 本根断面。

    Returns:
        `BarProtectResult`——`verdict` 非空时 `fill_px` 必为可成交价；`state` 恒为推进后的新状态。
    """
    adverse = bar.low_px if bar.side == "long" else bar.high_px
    favorable = bar.high_px if bar.side == "long" else bar.low_px
    if _is_new_epoch(state, bar):
        # 第 0 步：用 entry_px（**不是** adverse）建 state，`opened_wall` 即本根 ts；跳过第 1 步。
        state = advance_state(None, _facts(bar, bar.entry_px))
        state.opened_wall = float(bar.opened_ts)
    else:
        drawdown_facts = _facts(bar, adverse)
        snapshot = build_snapshot(rule, state, drawdown_facts)     # state 不推进（保守）
        verdict = evaluate_profit_guard(snapshot, rule, allow=_DRAWDOWN_RULES)
        if verdict is not None:
            return BarProtectResult(state=state, verdict=verdict,
                                    fill_px=_verdict_fill_px(bar, verdict))
    target_facts = _facts(bar, favorable)
    state = advance_state(state, target_facts)                     # 第 2 步：推进峰值
    snapshot = build_snapshot(rule, state, target_facts)
    verdict = evaluate_profit_guard(snapshot, rule, allow=_TARGET_RULES)
    if verdict is None:
        return BarProtectResult(state=state)
    return BarProtectResult(state=state, verdict=verdict,
                            fill_px=_verdict_fill_px(bar, verdict))
