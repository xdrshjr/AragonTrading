"""生命周期纯核心的离线用例（ai-trade-lifecycle-truth §8.1 用例 1~3）。

零 IO、零 fixture：`ai_auto/lifecycle.py` 不 import 任何会话对象，本文件因此可以逐格枚举
判定表。**先写这一层**是 §十 的交付顺序第一步 —— 派生态与 `armed` 的判据一旦在这里被钉死，
后面三层（会话 / manager / 前端）都只是它的搬运工。
"""
from __future__ import annotations

import pytest

from app.ai_auto import lifecycle


def _readings(**over) -> lifecycle.MemberReadings:
    """一个「一切正常」的读数，逐用例只改它关心的那一两个字段。"""
    base = {"inst_id": "BTC-USDT-SWAP", "state": lifecycle.STATE_RUNNING,
            "warmed_bars": 300, "feed_age_s": 5.0, "stale_after_s": 180.0,
            "link_state": "up", "next_wake_in_s": 120.0, "next_bar_in_s": 480.0,
            "rounds_completed": 41}
    base.update(over)
    return lifecycle.MemberReadings(**base)


# ---------------- 用例 1：derive_group_state ----------------
@pytest.mark.parametrize("states,expected", [
    ([], lifecycle.STATE_STOPPED),
    (["stopped"], lifecycle.STATE_STOPPED),
    (["stopped", "stopped"], lifecycle.STATE_STOPPED),
    (["paused", "stopped"], lifecycle.STATE_PAUSED),
    (["paused", "paused"], lifecycle.STATE_PAUSED),
    (["running", "paused"], lifecycle.STATE_RUNNING),
    (["running", "stopped"], lifecycle.STATE_RUNNING),
    (["running", "running"], lifecycle.STATE_RUNNING),
])
def test_derive_group_state_covers_every_combination(states, expected):
    assert lifecycle.derive_group_state(states) == expected


def test_empty_group_is_never_running():
    """空成员列表是 `stopped`，绝不是 `running` —— 与三处清组判据同极性。"""
    assert lifecycle.derive_group_state([]) != lifecycle.STATE_RUNNING


# ---------------- 用例 2：classify_member 的判定顺序 ----------------
def test_stopped_wins_over_feed_stale():
    """同时满足 `stopped` 与 `feed_stale` 时**必须**回 `session_stopped`（§3.1 的判定顺序）。

    先说次要原因等于把最重要的事实埋起来 —— 那正是 D-1 现场的读感。
    """
    r = _readings(state="stopped", stop_reason="session_stop_loss",
                  feed_age_s=9999.0, warmed_bars=0, link_state="down")
    code, text = lifecycle.classify_member(r)
    assert code == lifecycle.NOT_ARMED_STOPPED
    assert "触发会话止损" in text


def test_paused_wins_over_warmup_and_link():
    r = _readings(state="paused", pause_reason="user", warmed_bars=0, link_state="down")
    assert lifecycle.classify_member(r)[0] == lifecycle.NOT_ARMED_PAUSED


def test_warmup_wins_over_link_and_stale():
    r = _readings(warmed_bars=0, link_state="down", feed_age_s=9999.0)
    assert lifecycle.classify_member(r)[0] == lifecycle.NOT_ARMED_WARMUP


def test_link_down_wins_over_stale():
    r = _readings(link_state="down", feed_age_s=9999.0)
    assert lifecycle.classify_member(r)[0] == lifecycle.NOT_ARMED_LINK


def test_stale_feed_reports_the_age_in_chinese():
    code, text = lifecycle.classify_member(_readings(feed_age_s=720.0, stale_after_s=180.0))
    assert code == lifecycle.NOT_ARMED_STALE
    assert "12 分钟" in text
    assert "交易所侧止盈止损仍生效" in text


def test_unknown_link_state_does_not_disarm():
    """`unknown` 是「还没探到」而不是「不可达」：刚起的会话不该挂一条假红条。"""
    assert lifecycle.is_armed(_readings(link_state="unknown"))
    assert lifecycle.is_armed(_readings(link_state="idle"))
    assert lifecycle.is_armed(_readings(link_state="degraded"))


def test_armed_requires_every_condition():
    assert lifecycle.is_armed(_readings())
    assert lifecycle.classify_member(_readings())[1] == ""


def test_zero_stale_after_never_judges_stale():
    """`stale_after_s <= 0`（周期未知）时不判陈旧 —— 除以一个不知道的阈值只会造假红条。"""
    assert lifecycle.is_armed(_readings(stale_after_s=0.0, feed_age_s=99999.0))


# ---------------- 用例 3：group_lifecycle_wire ----------------
def _wire(readings, **over) -> dict:
    kwargs = {"declared": "running", "paused_ts": 0, "pause_reason": None,
              "resumed_count": 0, "now_ts": 1_755_500_000}
    kwargs.update(over)
    return lifecycle.group_lifecycle_wire(readings, **kwargs)


def test_group_wire_counts_armed_members():
    wire = _wire([_readings(inst_id="BTC-USDT-SWAP"),
                  _readings(inst_id="ETH-USDT-SWAP", feed_age_s=720.0)])
    assert wire["membersArmed"] == 1
    assert wire["membersTotal"] == 2
    assert wire["armed"] is False
    assert [row["instId"] for row in wire["notArmed"]] == ["ETH-USDT-SWAP"]
    assert wire["notArmed"][0]["reason"] == lifecycle.NOT_ARMED_STALE


def test_group_wire_is_armed_only_when_every_member_is():
    wire = _wire([_readings(inst_id="A"), _readings(inst_id="B")])
    assert wire["armed"] is True
    assert wire["notArmed"] == []
    assert wire["membersArmed"] == 2


def test_empty_group_is_not_armed():
    wire = _wire([])
    assert wire["armed"] is False
    assert wire["state"] == lifecycle.STATE_STOPPED
    assert wire["membersTotal"] == 0


def test_group_wire_next_wake_takes_the_soonest():
    wire = _wire([_readings(inst_id="A", next_wake_in_s=300.0, next_bar_in_s=900.0),
                  _readings(inst_id="B", next_wake_in_s=42.0, next_bar_in_s=120.0)])
    assert wire["nextWakeInS"] == 42
    assert wire["nextBarInS"] == 120


def test_group_wire_next_wake_is_null_without_a_wall_clock_wake():
    """未装墙钟唤醒（**最常见配置**）时 `nextWakeInS` 必须是 `null` 而不是 0。

    前端据它回落到「下一根 K 线」（[评审 R1-6]）；用 0 冒充「不适用」会让界面对着一个
    不会到来的时刻读秒。
    """
    wire = _wire([_readings(next_wake_in_s=None, next_bar_in_s=480.0)])
    assert wire["nextWakeInS"] is None
    assert wire["nextBarInS"] == 480


def test_group_wire_carries_the_declared_state_for_diagnosis():
    """`declaredState != state` 即 bug 的信号，故两者必须同时在 wire 里。"""
    wire = _wire([_readings(state="stopped", stop_reason="session_stop_loss")],
                 declared="running")
    assert wire["state"] == lifecycle.STATE_STOPPED
    assert wire["declaredState"] == "running"


# ---------------- member_lifecycle_wire ----------------
def test_member_wire_has_every_key_of_the_contract():
    """§5.2 的每个键都必须恒下发（前端要能区分「没数据」与「后端没上线这个键」）。"""
    wire = lifecycle.member_lifecycle_wire(_readings(), paused_ts=0, resumed_ts=1_755_499_880)
    assert set(wire) == {"state", "armed", "reason", "reasonText", "warmedBars", "feedAgeS",
                         "staleAfterS", "linkState", "nextWakeInS", "nextBarInS",
                         "roundsCompleted", "pauseReason", "pausedTs", "resumedTs", "stopReason"}
    assert wire["armed"] is True
    assert wire["reason"] == lifecycle.ARMED_OK
    assert wire["resumedTs"] == 1_755_499_880


def test_member_wire_clamps_negative_feed_age():
    """负年龄（时钟回拨 / 夹取边界）一律按 0 —— 一个负数在界面上没有任何含义。"""
    assert lifecycle.member_lifecycle_wire(_readings(feed_age_s=-3.0))["feedAgeS"] == 0.0


# ---------------- describe_not_armed / describe_age ----------------
def test_describe_not_armed_names_the_symbol_only_when_there_are_several():
    single = lifecycle.describe_not_armed([_readings(feed_age_s=720.0)])
    assert not single.startswith("BTC-USDT-SWAP")
    multi = lifecycle.describe_not_armed([
        _readings(inst_id="BTC-USDT-SWAP"),
        _readings(inst_id="ETH-USDT-SWAP", link_state="down")])
    assert multi.startswith("ETH-USDT-SWAP：")
    assert "BTC-USDT-SWAP" not in multi     # 武装成功的那个绝不出现在拒绝文案里


def test_describe_not_armed_is_empty_when_everyone_is_armed():
    assert lifecycle.describe_not_armed([_readings()]) == ""


@pytest.mark.parametrize("seconds,expected", [
    (-5, "不到 1 分钟"), (0, "不到 1 分钟"), (59, "不到 1 分钟"),
    (60, "1 分钟"), (720, "12 分钟"), (3600, "1 小时"), (7380, "2 小时 3 分钟"),
])
def test_describe_age(seconds, expected):
    assert lifecycle.describe_age(seconds) == expected
