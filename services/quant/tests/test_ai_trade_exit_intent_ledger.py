"""离场意图账本单测（ai-trade-exit-priority-and-commitment 轴 A · #28）。

**F-1 的回归用例**：线上轨迹里持仓 10.31 小时、约 20 个决策轮之后 `exitConfirmStreak` 仍然停在
`1`、`confirmWindowRemainingSeconds` 仍然是满窗 1 小时——因为「一轮非离场动作即清零」把
`first_exit_intent_wall` 连同计数一起抹掉了，`horizon:P0-3` 立下的「锁必须有钥匙」这条不变式**在
实现层被自己违反**，且不报任何错。

G-3 的「先红后绿」判据就是本文件第一条用例：修法前 `streak == 1`，修法后 `streak == 2`。
"""
from __future__ import annotations

from app.ai_auto.exit_intent import (
    RELEASE_EXIT_PENDING,
    advance_intent,
    contradicts_exit,
    derive_exit_pending_max_s,
    exit_pending_elapsed,
    intent_wire,
)
from app.ai_auto.hold_horizon import (
    GATE_EXIT_CONFIRM,
    GATE_HYSTERESIS,
    GATE_MIN_HOLD,
    RELEASE_GATES,
    HorizonFacts,
    HorizonRule,
    HorizonState,
    build_snapshot,
    release_reason_for,
)
from app.ai_auto.horizon_runtime import HorizonRuntime

_NOW = 1_700_000_000.0
_WAKE = 1800.0                    # `intraday_swing` 的建议唤醒周期：30 分钟


def _rule(**over) -> HorizonRule:
    base = {"min_hold_s": 7200.0, "exit_confirm_rounds": 2, "confirm_window_s": 3600.0,
            "hysteresis_k": 1.5, "target_hold_min_s": 14400.0, "target_hold_max_s": 43200.0,
            "title": "日内波段", "exit_intent_decay_rounds": 2}
    return HorizonRule(**{**base, **over})


def _facts(now: float, **over) -> HorizonFacts:
    base = {"seq": 1, "side": "long", "entry_px": 0.09, "mark_px": 0.09085,
            "leverage": 3.0, "now_wall": now, "px_age_s": 1.0, "cost_bps": 10.5}
    return HorizonFacts(**{**base, **over})


def _drive(runtime: HorizonRuntime, actions: list[str], *, start: float = _NOW) -> float:
    """按 30 分钟唤醒周期跑若干轮第一阶段意图；返回最后一轮的墙钟。"""
    now = start
    runtime.note_open({"side": "long"}, start, seq=1)
    for action in actions:
        now += _WAKE
        runtime.note_intent(action, None, position_side="long", now_wall=now)
    return now


# ---------------- F-1 的正修：close → hold → close ----------------
def test_a_single_hesitation_no_longer_wipes_the_exit_ledger():
    """**G-3 的先红后绿判据**：`close → hold → close` 之后 `streak == 2`（修法前恒为 1）。"""
    runtime = HorizonRuntime(_rule())
    _drive(runtime, ["close", "hold", "close"])
    assert runtime.state.exit_confirm_streak == 2
    assert runtime.state.exit_intent_lapses == 0


def test_the_wall_clock_key_survives_a_hesitation():
    """`first_exit_intent_wall` **不被重置**——没有它，那把墙钟钥匙每一次犹豫都被重新上一遍。"""
    runtime = HorizonRuntime(_rule())
    runtime.note_open({"side": "long"}, _NOW, seq=1)
    runtime.note_intent("close", None, position_side="long", now_wall=_NOW + _WAKE)
    first = runtime.state.first_exit_intent_wall
    assert first == _NOW + _WAKE
    runtime.note_intent("hold", None, position_side="long", now_wall=_NOW + 2 * _WAKE)
    assert runtime.state.first_exit_intent_wall == first      # 修法前这里是 None
    assert runtime.state.exit_confirm_streak == 1             # 计数保留、只记一次 lapse
    assert runtime.state.exit_intent_lapses == 1


def test_two_consecutive_hesitations_do_reset_the_ledger():
    """衰减**只容忍一次**间断（`decay_rounds=2`）：连续两轮观望即整条论证链终结。"""
    runtime = HorizonRuntime(_rule())
    _drive(runtime, ["close", "hold", "hold"])
    assert runtime.state.exit_confirm_streak == 0
    assert runtime.state.first_exit_intent_wall is None


def test_decay_zero_is_a_verbatim_rollback_to_todays_behaviour():
    """C-2 一键回退：`decay_rounds=0` ⇒ 一轮 hold 即清零，与本特性合入前逐字等价。"""
    runtime = HorizonRuntime(_rule(exit_intent_decay_rounds=0))
    _drive(runtime, ["close", "hold"])
    assert runtime.state.exit_confirm_streak == 0
    assert runtime.state.first_exit_intent_wall is None


def test_horizon_rule_default_decay_is_zero():
    """【R:P2-6】把一条**隐式依赖**变成显式断言。

    `tests/test_ai_trade_horizon_runtime.py::test_note_intent_resets_on_other_actions_but_holds_on_degrade`
    断言的正是今天的「一轮 hold 即清零」，它零改动继续绿**只靠** `HorizonRule` 的默认值仍是 `0`
    （settings 默认是 2，两者刻意分层）。实施者一旦顺手把这里也改成 2，那条用例会红而根因极难查。
    """
    assert HorizonRule().exit_intent_decay_rounds == 0
    assert HorizonRule().exit_pending_max_s == 0.0
    assert HorizonRule().exit_priority is None


def test_same_side_scale_in_clears_the_ledger_immediately():
    """同向加仓与离场**直接矛盾** ⇒ 立即清零，不走衰减（R-2 缓解②）。"""
    assert contradicts_exit("open_long", "long") is True
    assert contradicts_exit("open_short", "short") is True
    assert contradicts_exit("open_short", "long") is False     # 反手不是矛盾，是另一种换手
    runtime = HorizonRuntime(_rule())
    _drive(runtime, ["close", "open_long"])
    assert runtime.state.exit_confirm_streak == 0
    assert runtime.state.first_exit_intent_wall is None


def test_degraded_rounds_leave_the_ledger_untouched():
    """降级轮既非真实意愿也非真实观望 ⇒ 账本**整体不变**（沿用今天的语义）。"""
    runtime = HorizonRuntime(_rule())
    runtime.note_open({"side": "long"}, _NOW, seq=1)
    runtime.note_intent("close", None, position_side="long", now_wall=_NOW + _WAKE)
    before = runtime.state
    runtime.note_intent("hold", "llm_error", position_side="long", now_wall=_NOW + 2 * _WAKE)
    assert runtime.state == before


def test_advance_intent_is_a_pure_function():
    """纯核心不 mutate 入参、不依赖调用顺序（X-10）。"""
    state = HorizonState(epoch=("long", 1), opened_wall=_NOW, exit_confirm_streak=1,
                         first_exit_intent_wall=_NOW)
    out = advance_intent(state, gated=False, contradicts=False, degraded=False,
                         now_wall=_NOW + 60.0, decay_rounds=2)
    assert state.exit_intent_lapses == 0 and out.exit_intent_lapses == 1
    assert out is not state


# ---------------- 第二把钥匙：exit_pending 豁免 ----------------
def test_exit_pending_releases_only_g2_and_g3():
    """【R:P2-4】**只解 G2/G3，不解 G1**——G1 只由既有三条豁免解除。"""
    assert RELEASE_GATES[RELEASE_EXIT_PENDING] == (GATE_EXIT_CONFIRM, GATE_HYSTERESIS)
    assert GATE_MIN_HOLD not in RELEASE_GATES[RELEASE_EXIT_PENDING]


def test_exit_pending_fires_after_the_wall_clock_cap():
    """离场意图在账本上挂满上界 ⇒ `release_reason_for` 末位命中 `exit_pending`。"""
    rule = _rule(exit_pending_max_s=7200.0)
    state = HorizonState(epoch=("long", 1), opened_wall=_NOW, exit_confirm_streak=1,
                         first_exit_intent_wall=_NOW + 60.0)
    # 刚挂起 ⇒ 不命中
    assert release_reason_for(rule, state, _facts(_NOW + 120.0), return_pct=0.01) is None
    # 挂满 2 小时 ⇒ 命中
    assert release_reason_for(rule, state, _facts(_NOW + 7261.0),
                              return_pct=0.01) == RELEASE_EXIT_PENDING


def test_exit_pending_never_preempts_the_four_existing_exemptions():
    """既有四条豁免的**相对顺序逐字不变**：浮亏解除仍然优先于新增的这一条。"""
    rule = _rule(exit_pending_max_s=7200.0)
    state = HorizonState(epoch=("long", 1), opened_wall=_NOW, exit_confirm_streak=1,
                         first_exit_intent_wall=_NOW + 60.0)
    assert release_reason_for(rule, state, _facts(_NOW + 7261.0),
                              return_pct=-0.99) == "loss_release"


def test_exit_pending_is_off_when_the_cap_is_zero():
    """`mult=0` / 窗口为 0 ⇒ 上界为 0 ⇒ 本豁免关闭（C-3 一键回退）。"""
    assert derive_exit_pending_max_s(3600.0, 0) == 0.0
    assert derive_exit_pending_max_s(0.0, 2.0) == 0.0
    assert derive_exit_pending_max_s(3600.0, 2.0) == 7200.0
    assert exit_pending_elapsed(0.0, _NOW, _NOW + 1e9) is False
    assert exit_pending_elapsed(60.0, None, _NOW + 1e9) is False


# ---------------- 快照三键恒在场 ----------------
def test_snapshot_always_carries_the_three_additive_keys():
    """形状稳定是本仓库 journal / trace 的既有纪律：三键**恒在场**，不是条件写入。"""
    rule = _rule(exit_pending_max_s=7200.0)
    state = HorizonState(epoch=("long", 1), opened_wall=_NOW, exit_confirm_streak=2,
                         exit_intent_lapses=1, first_exit_intent_wall=_NOW + 60.0)
    snap = build_snapshot(rule, state, _facts(_NOW + 660.0))
    assert snap["exitIntentLapses"] == 1
    assert snap["exitIntentAgeSeconds"] == 600.0
    assert snap["exitPendingMaxSeconds"] == 7200.0
    # 尚无离场意图 ⇒ 年龄为 None（绝不用 0 冒充「刚提出」）
    empty = build_snapshot(rule, HorizonState(epoch=("long", 1), opened_wall=_NOW),
                           _facts(_NOW + 660.0))
    assert empty["exitIntentAgeSeconds"] is None
    assert intent_wire(state, pending_max_s=0.0, now_wall=_NOW + 660.0)[
        "exitPendingMaxSeconds"] == 0.0


# ---------------- A-2：不需要模型学会任何新字段的那一半收益 ----------------
def test_a2_two_closes_with_one_hold_between_satisfies_g2_structurally():
    """A-2：连续两轮 close（中间夹一轮 hold）⇒ 第三轮 `streak==2` ⇒ **G2 结构性满足**。

    这是本轮收益里**不需要模型学会任何新字段**的那一半，且它由轴 A 单独兑现——不需要
    `expedite` 解任何闸，更不需要碰 G3（那是成本闸）。
    """
    runtime = HorizonRuntime(_rule())
    now = _drive(runtime, ["close", "hold", "close"])
    snap = runtime.refresh(_facts(now))
    assert snap["exitConfirmStreak"] >= snap["exitConfirmRounds"]
