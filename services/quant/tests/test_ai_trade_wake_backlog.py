"""`ai_auto/wake_backlog.py` 纯状态单测（ai-trade-wake-scheduler-closed-loop §6.1 T-1~T-4）。

覆盖三条计数口径（节流基准 / 不节流的确认根 / `total_drops` 唯一自增点）、`waitedS` 的口径、
`consume()` 的保留语义、补跑链上限。无 IO、无时钟、不构造 session。
"""
from __future__ import annotations

import pytest

from app.ai_auto.wake_backlog import WakeBacklog

_T0 = 1_785_600_000.0        # 决策轮开始的墙钟（相对秒数 t+N 一律以它为原点）
_MIN_GAP = 120.0             # = wake_interval_s


def _backlog_in_flight() -> WakeBacklog:
    """一个「t+0 起了真实决策轮、随后一直在飞」的账本（节流基准已被 note_round_start 前移）。"""
    backlog = WakeBacklog()
    backlog.note_round_start(_T0)
    return backlog


# ---------- T-1：定时丢弃的节流口径 ----------
def test_interval_drops_are_throttled_to_the_wakes_that_would_really_have_happened():
    """379 秒在飞轮里每 5 秒调一次（76 次）⇒ 只计 3 次，且首次丢弃恰在 t+120。

    「3」正是这段时间里**原本真的会发生**的定时唤醒次数（t≈120/240/360）——这才是能如实说给模型
    听的数。裸 tick 计数会记成 76，写进 prompt 就是给模型一个假事实。
    """
    backlog = _backlog_in_flight()
    counted = 0
    for step in range(76):                    # t+0, t+5, …, t+375
        if backlog.note_interval(_T0 + step * 5.0, min_gap_s=_MIN_GAP):
            counted += 1
    assert (counted, backlog.dropped_intervals) == (3, 3)
    assert backlog.first_dropped_wall == pytest.approx(_T0 + 120.0)
    assert backlog.wire(_T0 + 379.0)["waitedS"] == pytest.approx(259.0)


def test_interval_drop_within_min_gap_returns_false_and_does_not_count():
    """节流窗内重复调用一律 False（调用方据此决定不推 status，频率天然封顶）。"""
    backlog = _backlog_in_flight()
    assert backlog.note_interval(_T0 + 120.0, min_gap_s=_MIN_GAP) is True
    assert backlog.note_interval(_T0 + 121.0, min_gap_s=_MIN_GAP) is False
    assert backlog.note_interval(_T0 + 239.9, min_gap_s=_MIN_GAP) is False
    assert backlog.note_interval(_T0 + 240.0, min_gap_s=_MIN_GAP) is True
    assert backlog.dropped_intervals == 2


# ---------- T-2：确认根丢弃不节流 ----------
def test_bar_drops_are_never_throttled():
    """每一根被丢弃的确认根都是一件独立的、真实发生过的事，全部计数。"""
    backlog = _backlog_in_flight()
    for step in range(3):
        backlog.note_bar(_T0 + 60.0 + step)
    assert backlog.dropped_bars == 3
    assert backlog.first_dropped_wall == pytest.approx(_T0 + 60.0)
    assert backlog.last_dropped_wall == pytest.approx(_T0 + 62.0)


# ---------- T-2b：total_drops 的自增点唯一 + consume 语义 ----------
def test_total_drops_has_a_single_increment_point_and_survives_consume():
    """`total_drops ≡ Σ 计过数的定时丢弃 + Σ 确认根丢弃`；`consume()` 只清本次待办。"""
    backlog = _backlog_in_flight()
    for step in range(76):
        backlog.note_interval(_T0 + step * 5.0, min_gap_s=_MIN_GAP)   # → 3
    for step in range(3):
        backlog.note_bar(_T0 + 100.0 + step)                          # → 3
    assert backlog.total_drops == 6 and backlog.pending is True
    backlog.consume()
    assert backlog.total_drops == 6              # 观测量绝不被 consume 清掉（A-6 的恒等式靠它）
    assert backlog.pending is False
    assert (backlog.dropped_intervals, backlog.dropped_bars) == (0, 0)
    assert backlog.first_dropped_wall == 0.0


def test_consume_keeps_throttle_baseline_so_counting_stays_continuous_across_rounds():
    """`last_noted_wall` 跨轮保留：consume 之后的下一次丢弃仍受同一节流窗约束。"""
    backlog = _backlog_in_flight()
    backlog.note_interval(_T0 + 120.0, min_gap_s=_MIN_GAP)
    backlog.consume()
    assert backlog.note_interval(_T0 + 200.0, min_gap_s=_MIN_GAP) is False
    assert backlog.note_interval(_T0 + 240.0, min_gap_s=_MIN_GAP) is True


def test_wire_reports_zero_waited_when_nothing_dropped():
    """无待办时 `waitedS` 为 0 而不是一个由 0 时间戳算出来的天文数字。"""
    assert WakeBacklog().wire(_T0)["waitedS"] == 0.0


# ---------- T-4：补跑链 ----------
def test_chain_counts_back_to_back_catchups_and_resets_on_a_normal_round():
    """链长只统计**背靠背**的补跑；一个非补跑轮启动后归零。"""
    backlog = _backlog_in_flight()
    backlog.note_catchup()
    backlog.note_catchup()
    assert (backlog.chain, backlog.total_catchups) == (2, 2)
    backlog.reset_chain()
    assert backlog.chain == 0 and backlog.total_catchups == 2   # 累计量不随链归零


def test_stats_exposes_the_three_session_level_counters():
    """`stats()` 是 status 与 metrics 的**唯一**读点（两处同读，故两个数不可能各自漂）。"""
    backlog = _backlog_in_flight()
    backlog.note_bar(_T0 + 10.0)
    backlog.note_catchup()
    backlog.note_ceiling_hit()
    assert backlog.stats() == {"drops": 1, "catchups": 1, "catchupCeilingHits": 1}
