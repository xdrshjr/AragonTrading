"""收益锁的会话侧运行时（ai-trade-profit-lock-and-entry-unblock 轴 B-2）。

与 `ProfitGuardRuntime` 同一范式：纯同步、无 IO、不碰 executor；会话只留「组装断面 → tick →
命中即经既有 `_apply_profit_exit` 平仓 → 回执」这几行薄接线。`rule=None` ⇒ 全部方法零动作，
既有直接构造会话的单测零回归。
"""
from __future__ import annotations

from .profit_guard_text import describe_exit_outcome
from .profit_lock import (  # noqa: F401 — lock_rule_wire / resolve 经本模块对外转发
    LockFacts,
    LockState,
    LockVerdict,
    ProfitLockRule,
    evaluate_lock,
    lock_rule_wire,
    lock_wire,
    reset_breach,
    resolve_profit_lock_rule,
)


def build_lock_facts(executor, guard_state, *, mark_px: float, now_wall: float,
                     cost_bps: float | None, atr_px: float | None) -> LockFacts | None:
    """由 executor 持仓读数 + 保护层 epoch 状态组装断面；无仓或无峰值状态 ⇒ None。

    峰值取 `ProfitGuardState.peak_px`——同一个 epoch 峰值，绝不另算第二份（两份必漂移）。
    """
    side = executor.projected
    entry = float(executor.entry_price or 0.0)
    if side not in ("long", "short") or guard_state is None or entry <= 0:
        return None
    atr_bps = (float(atr_px) / entry * 1e4) if atr_px else None
    return LockFacts(
        side=side, entry_px=entry, peak_px=float(guard_state.peak_px), mark_px=float(mark_px),
        opened_wall=float(guard_state.opened_wall), now_wall=float(now_wall),
        cost_bps=cost_bps, atr_bps=atr_bps,
        qty_coin=float(executor.entry_qty_sz) * float(executor.ct_val))


def build_exit_snap(facts: LockFacts) -> dict:
    """`_apply_profit_exit` 读的 snap 键（与利润保护快照同名；读不到的键缺席 ⇒ `.get` 得 None）。"""
    sign = 1.0 if facts.side == "long" else -1.0
    return {"side": facts.side, "entryPx": facts.entry_px,
            "unrealizedPnlUsd": facts.qty_coin * sign * (facts.mark_px - facts.entry_px),
            "peakUnrealizedPnlUsd": facts.qty_coin * sign * (facts.peak_px - facts.entry_px)}


def describe_lock_exit(verdict: LockVerdict, *, closed: bool) -> str:
    """锁平回执文案；措辞按 pnl 符号派生（与利润保护同一纪律），纯文本不含 Markdown。"""
    if not closed:
        return (f"收益锁已触发（{verdict.label}）但本次平仓未成交，将在下一次盯市重试"
                "（交易所侧止盈止损仍生效）")
    return (f"收益锁触发（{verdict.label}）：已市价平仓，"
            f"{describe_exit_outcome(verdict.note.get('pnlUsd'))}")


class ProfitLockRuntime:
    """一个会话的收益锁运行时。

    Attributes:
        rule: 已装配规则；None ⇒ 本会话不装配。
        state: 当前 epoch 的锁状态（无仓 ⇒ None）。
        exits: **实际成交**的锁平次数（以 close 成交事件为唯一判据，由会话回执写入）。
        armed_epochs: 进入过 stage ≥ 1 的持仓数。
        warned_seq: 平仓未成交 warn 的每持仓周期节流游标。
    """

    def __init__(self, rule: ProfitLockRule | None):
        self.rule = rule
        self.state: LockState | None = None
        self.exits = 0
        self.armed_epochs = 0
        self.warned_seq: int | None = None
        self._armed_epoch: tuple | None = None
        self._wire: dict | None = None

    def clear(self) -> None:
        """flat / 锁平成交后：丢弃 epoch 状态与快照。"""
        self.state = None
        self._wire = None

    def tick(self, facts: LockFacts, *, epoch: tuple, stale: bool) -> LockVerdict | None:
        """一次评估；stale 或成本不可算 ⇒ 不判定并清越线起点（fail-open）。

        epoch 变化（反手 / 同向重开）⇒ 状态整体复位：原子反手不经 flat，只靠 `clear()` 不够。
        """
        if self.rule is None:
            return None
        if self.state is None or self.state.epoch != epoch:
            self.state = LockState(epoch=epoch)
        if stale or facts.cost_bps is None:
            self.state = reset_breach(self.state)
            self._wire = lock_wire(self.rule, facts, self.state,
                                   unavailable="stale_px" if stale else "cost_unknown")
            return None
        self.state, verdict = evaluate_lock(self.rule, facts, self.state)
        if self.state.stage >= 1 and self._armed_epoch != epoch:
            self._armed_epoch = epoch
            self.armed_epochs += 1
        self._wire = lock_wire(self.rule, facts, self.state)
        return verdict

    def snapshot(self) -> dict | None:
        """最近一次 `lock_wire`；无仓 / 未装配 ⇒ None。"""
        return self._wire

    def status_wire(self) -> dict | None:
        """status 顶层计数；未装配 ⇒ None（调用方据此整键缺席）。"""
        if self.rule is None:
            return None
        return {"exits": self.exits, "armedEpochs": self.armed_epochs}
