"""AI 回测侧保护层判定单测（ai-trade-profit-protection-adaptive §2.3 / T-1）。

覆盖：**T-1 保守成交序**（同一根内的新高不抬线）、跳空穿越按开盘价成交、硬止盈走 favorable 通道、
epoch 初始化根跳过回撤判定（P1-2）、`min_hold_s` 在 bar 时钟下「开仓根不触发、次根才触发」（P1-3）。

断言一律**构造式**（由 entry/peak/giveback 反算），不照抄字面价格。
"""
from __future__ import annotations

import pytest

from app.ai_auto.profit_guard import (
    REASON_HARD_TAKE_PROFIT,
    REASON_TRAILING,
    ProfitGuardRule,
)
from app.ai_auto.profit_guard_bar import BarFacts, evaluate_bar

_BAR_SEC = 900          # 15m：> min_hold_s(30) ⇒ 次根即可触发
_TS0 = 1_700_000_000


def _rule(**over) -> ProfitGuardRule:
    return ProfitGuardRule(**over)


def _bar(*, ts: int, opened_ts: int, o: float, h: float, low: float,
         entry: float = 100.0, side: str = "long", seq: int = 7, **over) -> BarFacts:
    return BarFacts(seq=seq, side=side, entry_px=entry, qty_coin=1.0, leverage=1.0,
                    ts=ts, opened_ts=opened_ts, open_px=o, high_px=h, low_px=low, **over)


def test_entry_bar_builds_state_and_never_fires():
    """P1-2 + P1-3：开仓根先建 state（peak=entry）且**恒不触发**——`holdSeconds=0 < min_hold_s`。"""
    rule = _rule()
    result = evaluate_bar(None, rule, _bar(ts=_TS0, opened_ts=_TS0, o=100.0, h=112.0, low=90.0))

    assert result.verdict is None and result.fill_px is None
    assert result.state is not None and result.state.side == "long"
    assert result.state.opened_wall == float(_TS0)
    assert result.state.peak_px == pytest.approx(112.0)     # 峰值仍在本根推进（供次根用）


def test_conservative_order_uses_previous_peak_not_this_bars_high():
    """T-1（本轮核心正确性）：先判回撤、后推峰值——同一根内刚创出的新高**不会**立刻抬高移动止盈线。

    构造：entry=100、上一根遗留 peak=110、giveback=0.4 ⇒ trail=106；本根 `o=100,h=112,l=99`。
    ① 必须用 `low=99` 判定并触发 trailing；② 成交价为 `min(106, open=100) = 100`。
    若实现写反（先把 peak 抬到 112 再算 trail=107.2），成交价会变成 107.2 —— 一个看起来更好、
    其实是未来函数的价格，本用例必红。
    """
    rule = _rule(giveback_pct=0.4, arm_return_pct=0.05, breakeven_arm_return_pct=0.0)
    seeded = evaluate_bar(None, rule, _bar(ts=_TS0, opened_ts=_TS0, o=100.0, h=110.0, low=100.0))
    assert seeded.state.peak_px == pytest.approx(110.0)

    result = evaluate_bar(seeded.state, rule,
                          _bar(ts=_TS0 + _BAR_SEC, opened_ts=_TS0, o=100.0, h=112.0, low=99.0))

    assert result.verdict is not None
    assert result.verdict.reason_code == REASON_TRAILING
    trail = 100.0 + (110.0 - 100.0) * (1 - 0.4)             # = 106，构造式反算
    assert result.verdict.note["exitPx"] == pytest.approx(trail)
    assert result.fill_px == pytest.approx(100.0)           # min(106, open) —— 跳空低开按开盘价
    assert result.fill_px < trail                           # 保守：绝不给出这根从未成交过的价格


def test_gap_through_protection_line_fills_at_open_price():
    """跳空穿越：保护线在开盘价之上（long）⇒ 只能按开盘价成交（更差，保守）。"""
    rule = _rule(giveback_pct=0.4, arm_return_pct=0.05, breakeven_arm_return_pct=0.0)
    seeded = evaluate_bar(None, rule, _bar(ts=_TS0, opened_ts=_TS0, o=100.0, h=110.0, low=100.0))

    gapped = evaluate_bar(seeded.state, rule,
                          _bar(ts=_TS0 + _BAR_SEC, opened_ts=_TS0, o=95.0, h=96.0, low=94.0))

    assert gapped.verdict.reason_code == REASON_TRAILING
    assert gapped.fill_px == pytest.approx(95.0)            # = open，而不是 106 这条没成交过的线


def test_hard_take_profit_fires_through_the_favorable_channel():
    """硬止盈是**上方目标位**：只在第 2 步（推完峰值）判定，成交价取 `max(线, open)`。"""
    rule = _rule(hard_tp_return_pct=0.10, arm_return_pct=5.0, breakeven_arm_return_pct=0.0)
    seeded = evaluate_bar(None, rule, _bar(ts=_TS0, opened_ts=_TS0, o=100.0, h=100.0, low=100.0))

    result = evaluate_bar(seeded.state, rule,
                          _bar(ts=_TS0 + _BAR_SEC, opened_ts=_TS0, o=100.0, h=115.0, low=99.0))

    assert result.verdict.reason_code == REASON_HARD_TAKE_PROFIT
    assert result.verdict.note["exitPx"] == pytest.approx(110.0)   # entry×(1+0.10/lev=1)
    assert result.fill_px == pytest.approx(110.0)                  # max(110, open=100)


def test_min_hold_seconds_uses_bar_timestamps():
    """§2.3.2：bar ts 即墙钟 ⇒ `min_hold_s` 一字不改复用；把它设成 0 才可能在开仓根生效。"""
    rule = _rule(giveback_pct=0.4, arm_return_pct=0.05, breakeven_arm_return_pct=0.0,
                 min_hold_s=_BAR_SEC * 2)
    seeded = evaluate_bar(None, rule, _bar(ts=_TS0, opened_ts=_TS0, o=100.0, h=110.0, low=100.0))

    # 次根：holdSeconds=900 < min_hold_s=1800 ⇒ 仍不触发（判据未被 bar 时钟绕过）。
    second = evaluate_bar(seeded.state, rule,
                          _bar(ts=_TS0 + _BAR_SEC, opened_ts=_TS0, o=100.0, h=100.0, low=99.0))
    assert second.verdict is None

    third = evaluate_bar(second.state, rule,
                         _bar(ts=_TS0 + 2 * _BAR_SEC, opened_ts=_TS0, o=100.0, h=100.0, low=99.0))
    assert third.verdict is not None and third.verdict.reason_code == REASON_TRAILING


def test_new_epoch_resets_peak_instead_of_inheriting_it():
    """epoch 身份 = (side, seq)：换仓（`entry_bar` 变化）必须重新起算峰值，绝不继承上一仓。"""
    rule = _rule(giveback_pct=0.4, arm_return_pct=0.05, breakeven_arm_return_pct=0.0)
    seeded = evaluate_bar(None, rule,
                          _bar(ts=_TS0, opened_ts=_TS0, o=100.0, h=110.0, low=100.0, seq=7))

    reopened = evaluate_bar(seeded.state, rule,
                            _bar(ts=_TS0 + _BAR_SEC, opened_ts=_TS0 + _BAR_SEC,
                                 o=100.0, h=101.0, low=99.0, seq=8))

    assert reopened.verdict is None                       # 新仓的开仓根恒不触发
    assert reopened.state.seq == 8
    assert reopened.state.peak_px == pytest.approx(101.0)  # 从 entry 起算，而非继承 110


def test_short_side_is_mirror_symmetric():
    """做空对称：adverse 取 `h`、保护线在上方 ⇒ 跳空成交价取 `max(线, open)`。"""
    rule = _rule(giveback_pct=0.4, arm_return_pct=0.05, breakeven_arm_return_pct=0.0)
    seeded = evaluate_bar(None, rule, _bar(ts=_TS0, opened_ts=_TS0, o=100.0, h=100.0, low=90.0,
                                           side="short"))
    assert seeded.state.peak_px == pytest.approx(90.0)

    result = evaluate_bar(seeded.state, rule,
                          _bar(ts=_TS0 + _BAR_SEC, opened_ts=_TS0, o=105.0, h=106.0, low=104.0,
                               side="short"))

    trail = 100.0 - (100.0 - 90.0) * (1 - 0.4)            # = 94（做空线在上方）
    assert result.verdict.reason_code == REASON_TRAILING
    assert result.verdict.note["exitPx"] == pytest.approx(trail)
    assert result.fill_px == pytest.approx(105.0)         # max(94, open=105)
