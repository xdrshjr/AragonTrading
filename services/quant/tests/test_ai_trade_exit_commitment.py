"""离场意图兑现纯核心单测（ai-trade-exit-priority-and-commitment 轴 C · #30）。

覆盖武装 / 重试间隔 / epoch 变化即清空 / `max_attempts` 与 `max_age_s` 双到顶 / stuck 后不再重试 /
**`resting_close=True` ⇒ `should_retry` 恒 `False`**（【R:P0-3】的第二层防线）。
"""
from __future__ import annotations

from app.ai_auto.exit_commitment import (
    ExitCommitment,
    ExitCommitmentRule,
    arm,
    build_wire,
    note_attempt,
    on_epoch_change,
    rule_from_settings,
    should_retry,
)
from app.config import Settings

_NOW = 1_700_000_000.0
_EPOCH = ("long", 3)


def _rule(**over) -> ExitCommitmentRule:
    return ExitCommitmentRule(**{"retry_interval_s": 10.0, "max_attempts": 6,
                                 "max_age_s": 300.0, **over})


def _armed(**over) -> ExitCommitment:
    base = arm(ExitCommitment(), epoch=_EPOCH, now_wall=_NOW,
               reason_code="exit_priority_override(final,2/2)", priority_level="override")
    return base if not over else ExitCommitment(**{**base.__dict__, **over})


def _retry(state: ExitCommitment, *, now: float, resting: bool = False,
           side: str = "long", epoch=_EPOCH) -> bool:
    return should_retry(state, _rule(), position_side=side, epoch=epoch,
                        now_wall=now, resting_close=resting)


# ---------------- 武装 ----------------
def test_arm_records_the_identity_and_the_reason():
    state = _armed()
    assert state.armed and state.epoch == _EPOCH
    assert state.priority_level == "override"
    assert state.reason_code.startswith("exit_priority_override")
    assert state.attempts == 0 and state.stuck is False


def test_arm_on_the_same_epoch_does_not_restart_the_clock():
    """连续两轮都提 close 而都没成交时，`max_age_s` 必须从**第一次**起算——否则「已确认离场却
    兑现不了」这件事永远到不了告警。"""
    first = _armed()
    again = arm(first, epoch=_EPOCH, now_wall=_NOW + 120.0, reason_code="x",
                priority_level="normal")
    assert again is first


def test_arm_on_a_flat_position_is_a_no_op():
    assert arm(ExitCommitment(), epoch=None, now_wall=_NOW, reason_code="x",
               priority_level="normal").armed is False


# ---------------- 重试判据（自上而下的「否」） ----------------
def test_retry_waits_for_the_interval():
    state = _armed()
    assert _retry(state, now=_NOW + 5.0) is False
    assert _retry(state, now=_NOW + 10.0) is True


def test_retry_stops_on_flat_or_epoch_change():
    state = _armed()
    assert _retry(state, now=_NOW + 60.0, side="flat") is False
    assert _retry(state, now=_NOW + 60.0, epoch=("long", 4)) is False
    assert _retry(state, now=_NOW + 60.0, epoch=None) is False


def test_retry_is_off_when_unarmed_or_unarmed_rule():
    assert _retry(ExitCommitment(), now=_NOW + 60.0) is False
    assert should_retry(_armed(), None, position_side="long", epoch=_EPOCH,
                        now_wall=_NOW + 60.0, resting_close=False) is False
    assert should_retry(_armed(), _rule(enabled=False), position_side="long", epoch=_EPOCH,
                        now_wall=_NOW + 60.0, resting_close=False) is False


def test_resting_close_short_circuits_every_retry():
    """【R:P0-3】**挂单在飞就是「正在兑现」**：本层绝不去撤掉模型自己挂的限价平仓单。

    没有这条短路，`retry_interval_s` 到点后 `executor.apply([Signal(side="close")])` 不带
    `order_plan` ⇒ 市价 ⇒ 与原挂单 `is_same_intent` 不成立 ⇒ 撤单改市价成交，整个 limit-orders
    特性在离场侧被静默废掉，而界面上还显示着那张限价单。
    """
    assert _retry(_armed(), now=_NOW + 3600.0, resting=True) is False


def test_stuck_state_never_retries_again():
    state = note_attempt(_armed(attempts=5, last_attempt_wall=_NOW), _rule(),
                         now_wall=_NOW + 10.0, filled=False, error=None)
    assert state.stuck is True
    assert _retry(state, now=_NOW + 1e6) is False


# ---------------- 记账 ----------------
def test_a_fill_clears_the_intent():
    """成交即完成：返回**空态**（下一笔持仓绝不继承上一笔的离场意图）。"""
    assert note_attempt(_armed(), _rule(), now_wall=_NOW + 10.0, filled=True,
                        error=None).armed is False


def test_max_attempts_and_max_age_both_trip_stuck():
    """双上限：次数到顶或时长到顶，任一成立即 `stuck`。"""
    by_count = note_attempt(_armed(attempts=5), _rule(), now_wall=_NOW + 60.0,
                            filled=False, error=None)
    assert by_count.attempts == 6 and by_count.stuck is True
    by_age = note_attempt(_armed(), _rule(), now_wall=_NOW + 301.0, filled=False,
                          error="OKX 超时")
    assert by_age.attempts == 1 and by_age.stuck is True
    assert by_age.last_error == "OKX 超时"


def test_epoch_change_voids_the_intent_and_is_idempotent():
    assert on_epoch_change(_armed(), ("short", 4)).armed is False
    assert on_epoch_change(_armed(), None).armed is False
    assert on_epoch_change(_armed(), _EPOCH).armed is True
    assert on_epoch_change(ExitCommitment(), None).armed is False   # 空态幂等


# ---------------- wire ----------------
def test_wire_is_absent_when_unarmed_and_complete_when_armed():
    assert build_wire(ExitCommitment(), _rule(), now_wall=_NOW) is None
    assert build_wire(_armed(), None, now_wall=_NOW) is None
    wire = build_wire(_armed(attempts=2), _rule(), now_wall=_NOW + 24.0)
    assert wire["armed"] is True and wire["attempts"] == 2
    assert wire["maxAttempts"] == 6 and wire["ageSeconds"] == 24.0
    assert wire["priorityLevel"] == "override" and wire["stuck"] is False


def test_rule_follows_the_ops_switch():
    assert rule_from_settings(Settings()) is not None
    assert rule_from_settings(Settings(ai_trade_exit_commitment_enabled=False)) is None
