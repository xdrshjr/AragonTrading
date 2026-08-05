"""状态机与迟滞（discovery-asset-ledger §2.5.4 · AL-8）。

无迟滞 ⇒ 一次数据抖动就把资产打成「已衰减」，用户会关掉整个功能；
``unknown`` 计入连续失败 ⇒ 一段行情空档期把全部资产集体降级。
"""
from __future__ import annotations

import pytest

from app.discovery import health

GATE = 2


def test_healthy_resets_the_streak() -> None:
    assert health.next_status(3, [], fail_streak_gate=GATE) == ("healthy", 0)


def test_first_failure_is_watch_not_decayed() -> None:
    assert health.next_status(0, ["H5"], fail_streak_gate=GATE) == ("watch", 1)


def test_second_consecutive_failure_is_decayed() -> None:
    assert health.next_status(1, ["H5"], fail_streak_gate=GATE) == ("decayed", 2)


@pytest.mark.parametrize("reasons", [["H1"], ["H2"], ["H3"], ["H1", "H5"]])
def test_unknown_never_touches_the_streak(reasons: list) -> None:
    """不可判定时**不给结论**，也不惩罚——数据不足不是资产的错（AL-2）。"""
    assert health.next_status(1, reasons, fail_streak_gate=GATE) == ("unknown", 1)


def test_recovery_is_immediate() -> None:
    """``decayed`` 之后再 healthy 立刻恢复，不需要反向迟滞（降级只是告警，不是惩罚）。"""
    status, streak = health.next_status(5, [], fail_streak_gate=GATE)
    assert (status, streak) == ("healthy", 0)


def test_status_domain_is_closed() -> None:
    """状态取值域是封闭的六个，UI 的徽标表按它对齐。"""
    produced = {health.next_status(streak, reasons, fail_streak_gate=GATE)[0]
                for streak in (0, 1, 2)
                for reasons in ([], ["H5"], ["H1"])}
    assert produced <= set(health.HEALTH_STATUSES)
    assert produced == {"healthy", "watch", "decayed", "unknown"}
