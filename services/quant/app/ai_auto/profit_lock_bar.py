"""AI 回测侧的收益锁判定（ai-trade-profit-lock-and-entry-unblock 轴 B-5）。

与 `profit_guard_bar` 同一保守成交序，且**只调用 `profit_lock` 的既有纯函数**（零逻辑复制）：

0. epoch 初始化：状态缺失 / 身份不符 ⇒ 以 entry 为峰值建状态，本根跳过回撤判定；
1. 先判回撤：用本根**不利极值**判越线，峰值与锁线不推进；越线按锁线成交，跳空按开盘价；
2. 再推峰值：未越线才用**有利极值**推进峰值与锁线；
3. 收盘复核：本根收盘价已落到第 2 步新锁线的不利一侧 ⇒ 价格在「极值 → 收盘」途中必然穿过锁线，
   按锁线成交（收盘晚于任何根内极值，这一推断不含未来信息）。不做这一步，武装根内冲高回落的
   单子会被推迟到下一根开盘才成交——系统性地把本该保本的往返记成亏损。

回测里 `confirm_s` 视为 0（一根 K 线远长于 8 秒确认窗口）。同一根内刚创的新高不会立刻抬高锁线，
故系统性**低估**收益锁的收益，这是允许它进回测的前提。
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from .profit_lock import (LockFacts, LockState, LockVerdict, ProfitLockRule, build_verdict,
                          evaluate_lock)


@dataclass(frozen=True)
class LockBar:
    """第 i 根的持仓与行情断面（聚合结构体；bar ts 充当墙钟）。"""

    seq: int
    side: str
    entry_px: float
    qty_coin: float
    ts: int
    opened_ts: int
    open_px: float
    high_px: float
    low_px: float
    cost_bps: float | None = None
    atr_bps: float | None = None
    close_px: float | None = None     # 第 3 步收盘复核用；None ⇒ 跳过（逐字回退两步序）


@dataclass(frozen=True)
class BarLockState:
    """跨根状态：锁状态 + 本 epoch 的方向化峰值价。"""

    lock: LockState
    peak_px: float


@dataclass(frozen=True)
class BarLockResult:
    """一根上的判定结果：`verdict` 非空即应按 `fill_px` 平仓。"""

    state: BarLockState
    verdict: LockVerdict | None = None
    fill_px: float | None = None


def _facts(bar: LockBar, *, peak_px: float, mark_px: float) -> LockFacts:
    return LockFacts(side=bar.side, entry_px=bar.entry_px, peak_px=peak_px, mark_px=mark_px,
                     opened_wall=float(bar.opened_ts), now_wall=float(bar.ts),
                     cost_bps=bar.cost_bps, atr_bps=bar.atr_bps, qty_coin=bar.qty_coin)


def _fill_px(bar: LockBar, lock_px: float) -> float:
    """锁线在持仓不利方向；跳空穿越只能按开盘价成交（long 取 min，short 取 max）。"""
    return min(lock_px, bar.open_px) if bar.side == "long" else max(lock_px, bar.open_px)


def evaluate_lock_bar(state: BarLockState | None, rule: ProfitLockRule,
                      bar: LockBar) -> BarLockResult:
    """一根上的保守收益锁判定（先判回撤、后推峰值）。成本不可算 ⇒ 只推峰值，不判定。"""
    rule = replace(rule, confirm_s=0.0)
    epoch = (bar.side, bar.seq)
    adverse = bar.low_px if bar.side == "long" else bar.high_px
    favorable = bar.high_px if bar.side == "long" else bar.low_px
    if state is None or state.lock.epoch != epoch:
        state = BarLockState(lock=LockState(epoch=epoch), peak_px=bar.entry_px)
    elif bar.cost_bps is not None and state.lock.lock_px is not None:
        facts = _facts(bar, peak_px=state.peak_px, mark_px=adverse)
        lock, verdict = evaluate_lock(rule, facts, state.lock)
        if verdict is not None:
            fill = _fill_px(bar, float(lock.lock_px))
            verdict = build_verdict(replace(facts, mark_px=fill), lock)
            return BarLockResult(BarLockState(lock, state.peak_px), verdict, fill)
    peak = (max(state.peak_px, favorable) if bar.side == "long"
            else min(state.peak_px, favorable))
    lock = state.lock
    if bar.cost_bps is None:
        return BarLockResult(BarLockState(lock, peak))
    lock, _ = evaluate_lock(rule, _facts(bar, peak_px=peak, mark_px=favorable), lock)
    if bar.close_px is not None and lock.lock_px is not None:
        facts = _facts(bar, peak_px=peak, mark_px=bar.close_px)
        lock, verdict = evaluate_lock(rule, facts, lock)
        if verdict is not None:
            verdict = build_verdict(replace(facts, mark_px=lock.lock_px), lock)
            return BarLockResult(BarLockState(lock, peak), verdict, float(lock.lock_px))
    return BarLockResult(BarLockState(lock, peak))
