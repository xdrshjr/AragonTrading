"""离场优先级纯核心单测（ai-trade-exit-priority-and-commitment 轴 B · #29）。

覆盖 §4.3.1 **五档判定表逐格** + 四种降档原因 + `may_pend` 只对 `ledger_short` 为真 + 配额 / 证据 /
地板三条合取项 + **反手恒返回 `none`**（G-5 的行为断言，X-1 的守门人）+ `level=none` 时 wire 整键缺席。
"""
from __future__ import annotations

import pytest

from app.ai_auto.exit_priority import (
    CONVICTION_FINAL,
    CONVICTION_HIGH,
    CONVICTION_NORMAL,
    DOWNGRADE_EVIDENCE,
    DOWNGRADE_LEDGER,
    DOWNGRADE_MIN_HOLD,
    DOWNGRADE_QUOTA,
    LEVEL_ADJUDICATED,
    LEVEL_EXPEDITE,
    LEVEL_NONE,
    LEVEL_NORMAL,
    LEVEL_OVERRIDE,
    ExitPriorityInput,
    ExitPriorityRule,
    adjudicate,
    normalize_conviction,
    profiles_block,
    resolve_exit_priority,
    rule_from_settings,
    rule_wire,
)
from app.ai_auto.hold_horizon import GATE_EXIT_CONFIRM, GATE_HYSTERESIS, GATE_MIN_HOLD
from app.config import Settings

_EVIDENCE = "1H 均线通道中轨失守，且成交量连续两根放大"


def _rule(**over) -> ExitPriorityRule:
    return ExitPriorityRule(**{"exit_confirm_rounds": 2, "override_rounds": 2,
                               "override_min_hold_s": 60.0,
                               "max_overrides_per_session": 6,
                               "evidence_min_chars": 10, **over})


def _inp(**over) -> ExitPriorityInput:
    base = {"action": "close", "position_side": "long", "gate_reverse": True,
            "conviction": None, "evidence": None, "intent_streak": 0, "intent_lapses": 0,
            "hold_seconds": 3600.0, "overrides_used": 0}
    return ExitPriorityInput(**{**base, **over})


# ---------------- 判定表逐格 ----------------
def test_row1_non_close_is_never_in_scope():
    """第 1 条：非平仓（含 `hold` / 开仓 / **反手**）⇒ `none`，本层整体空转。"""
    for action, side in [("hold", "long"), ("open_long", "flat"), ("cancel_order", "long"),
                         ("close", "flat")]:
        out = resolve_exit_priority(_inp(action=action, position_side=side), _rule())
        assert out.level == LEVEL_NONE, (action, side)
        assert out.wire == {}          # `level=none` ⇒ wire 整键缺席


@pytest.mark.parametrize("action,side", [("open_short", "long"), ("open_long", "short")])
def test_g5_reverse_never_gets_a_priority(action, side):
    """**G-5 ②（行为断言）**：反手即便声明 final + 证据 + 账本达标 + 配额充足，也恒 `none`。

    ①的 AST 扫描单独是不够的——泄漏点在 `hold_horizon` 的接线上，`exit_priority.py` 里确实
    一个 `open_long` 都没有。反手携带一条**开仓腿**，给它解闸就是用「帮你离场」的名义去开新仓，
    直接违反 X-1。
    """
    out = resolve_exit_priority(
        _inp(action=action, position_side=side, conviction=CONVICTION_FINAL,
             evidence=_EVIDENCE, intent_streak=9, hold_seconds=99999.0), _rule())
    assert out.level == LEVEL_NONE
    assert out.released_gates == ()
    assert out.may_pend is False


def test_row2_unarmed_is_always_normal():
    """第 2 条：未装配 ⇒ `normal`、不解闸、wire 整键缺席（X-7 的纯核心一侧）。"""
    for rule in (None, _rule(enabled=False)):
        out = resolve_exit_priority(_inp(conviction=CONVICTION_FINAL, evidence=_EVIDENCE,
                                         intent_streak=9), rule)
        assert out.level == LEVEL_NORMAL
        assert out.released_gates == ()
        assert out.wire == {}


def test_row3_override_needs_all_five_conjuncts():
    """第 3 条：五个合取项**同时**成立才 `override`，解 G1/G2/G3。"""
    out = resolve_exit_priority(
        _inp(conviction=CONVICTION_FINAL, evidence=_EVIDENCE, intent_streak=2,
             hold_seconds=3600.0, overrides_used=0), _rule())
    assert out.level == LEVEL_OVERRIDE
    assert set(out.released_gates) == {GATE_MIN_HOLD, GATE_EXIT_CONFIRM, GATE_HYSTERESIS}
    assert out.downgrade_reason is None
    assert out.reason == "exit_priority_override(final,2/2)"


def test_row4_adjudicated_is_an_independent_level_not_a_disjunct():
    """第 4 条【R:P1-2】：`adjudicated` 是**独立档位**——写成 `override` 的合取项会让它在唯一的
    判定落点上恒为 `False`（`resolve` 跑在反思之前），`level` 长期说谎。"""
    inp = _inp(conviction=CONVICTION_FINAL, evidence=_EVIDENCE, intent_streak=1)
    assert resolve_exit_priority(inp, _rule()).level == LEVEL_EXPEDITE     # 账本不足 ⇒ 降档
    out = adjudicate(inp, _rule())
    assert out.level == LEVEL_ADJUDICATED
    assert set(out.released_gates) == {GATE_MIN_HOLD, GATE_EXIT_CONFIRM, GATE_HYSTERESIS}
    assert out.reason == "exit_adjudicated(1/2)"
    assert out.wire["adjudicated"] is True


def test_row5_expedite_releases_only_g2():
    """第 5 条【R:P1-4】：`expedite` **只解 G2**，绝不解 G3（那是成本闸）。

    该析取支由 `intent_streak >= exit_confirm_rounds` 命中时 G2 本就已放行，故顺带解 G3 的**唯一
    净效果**是「在浮盈不足以覆盖一次往返成本时离场」——确定性地做一笔净亏的往返。
    """
    for inp in (_inp(conviction=CONVICTION_HIGH), _inp(intent_streak=2)):
        out = resolve_exit_priority(inp, _rule())
        assert out.level == LEVEL_EXPEDITE
        assert out.released_gates == (GATE_EXIT_CONFIRM,)
        assert GATE_HYSTERESIS not in out.released_gates
        assert GATE_MIN_HOLD not in out.released_gates


def test_row6_plain_close_stays_normal():
    """第 6 条：什么都没声明、账本也不够 ⇒ `normal`，三闸照旧（今天的行为）。"""
    out = resolve_exit_priority(_inp(), _rule())
    assert out.level == LEVEL_NORMAL
    assert out.released_gates == ()
    assert out.reason is None


# ---------------- 四种降档原因（自上而下取第一条） ----------------
@pytest.mark.parametrize("over,expected", [
    ({"evidence": None}, DOWNGRADE_EVIDENCE),
    ({"evidence": "太短"}, DOWNGRADE_EVIDENCE),
    ({"evidence": _EVIDENCE, "intent_streak": 1}, DOWNGRADE_LEDGER),
    ({"evidence": _EVIDENCE, "intent_streak": 2, "overrides_used": 6}, DOWNGRADE_QUOTA),
    ({"evidence": _EVIDENCE, "intent_streak": 2, "hold_seconds": 30.0}, DOWNGRADE_MIN_HOLD),
])
def test_downgrade_reasons_are_reported_verbatim(over, expected):
    """**静默降档会让模型在下一轮重复完全相同的错误**，故四种原因必须逐一如实留痕。"""
    out = resolve_exit_priority(_inp(conviction=CONVICTION_FINAL, **over), _rule())
    assert out.downgrade_reason == expected
    assert out.level == LEVEL_EXPEDITE          # 降档后按 high 处理
    assert out.wire["downgradeReason"] == expected


def test_non_final_declarations_never_carry_a_downgrade_reason():
    """`normal` / `high` 没有「降档」可言（它们本就没申请 override）。"""
    for conviction in (None, CONVICTION_NORMAL, CONVICTION_HIGH):
        assert resolve_exit_priority(_inp(conviction=conviction),
                                     _rule()).downgrade_reason is None


# ---------------- may_pend：只对 ledger_short 开启（【R:P0-1】） ----------------
def test_may_pend_is_true_only_for_ledger_short():
    """四种降档原因里**只有**「账本还不够厚」是复核官看得见证据、有资格替代的判断。

    另外三种若也走裁决，等于一次删掉 X-4 四个合取项中的三个——会话配额、证据要求、60 秒绝对
    地板全部可被复核官一句 `confirm` 抹掉。
    """
    ledger = _inp(conviction=CONVICTION_FINAL, evidence=_EVIDENCE, intent_streak=1)
    assert resolve_exit_priority(ledger, _rule()).may_pend is True
    for over in ({"evidence": None},
                 {"evidence": _EVIDENCE, "intent_streak": 2, "overrides_used": 6},
                 {"evidence": _EVIDENCE, "intent_streak": 2, "hold_seconds": 30.0}):
        out = resolve_exit_priority(_inp(conviction=CONVICTION_FINAL, **over), _rule())
        assert out.may_pend is False, out.downgrade_reason


def test_may_pend_respects_the_shared_quota():
    """X-4b：裁决与 override **共用同一份配额**——配额用尽时连挂起都不允许。"""
    inp = _inp(conviction=CONVICTION_FINAL, evidence=_EVIDENCE, intent_streak=1,
               overrides_used=6)
    out = resolve_exit_priority(inp, _rule())
    assert out.downgrade_reason == DOWNGRADE_LEDGER   # 账本先命中（自上而下）
    assert out.may_pend is False                      # 但配额已尽 ⇒ 不得挂起


def test_quota_zero_closes_the_override_lane_entirely():
    """配额 0 ⇒ `final` 一律按 `high` 处理（C-6 的关闭语义）。"""
    out = resolve_exit_priority(
        _inp(conviction=CONVICTION_FINAL, evidence=_EVIDENCE, intent_streak=2),
        _rule(max_overrides_per_session=0))
    assert out.level == LEVEL_EXPEDITE
    assert out.downgrade_reason == DOWNGRADE_QUOTA
    assert out.may_pend is False


# ---------------- 归一与 wire ----------------
def test_unknown_conviction_never_downgrades_the_safest_action():
    """值域外 / 类型错 ⇒ 归一为 `normal`（**绝不据一个读不懂的确信度把 close 降级**）。"""
    for raw in (None, "", "FINAL!", 123, {"a": 1}):
        assert normalize_conviction(raw) == CONVICTION_NORMAL
    out = resolve_exit_priority(_inp(conviction="whatever"), _rule())
    assert out.level == LEVEL_NORMAL and out.declared == CONVICTION_NORMAL


def test_wire_shape_is_complete_and_self_consistent():
    """wire 与产物三者恒自洽（trace / aiStats / 退出归因读的正是它们）。"""
    out = resolve_exit_priority(
        _inp(conviction=CONVICTION_FINAL, evidence=_EVIDENCE, intent_streak=2,
             intent_lapses=1, overrides_used=1), _rule())
    wire = out.wire
    assert wire["level"] == out.level == LEVEL_OVERRIDE
    assert wire["releasedGates"] == list(out.released_gates)
    assert wire["reason"] == out.reason
    assert wire["mayPend"] == out.may_pend
    assert wire["intentStreak"] == 2 and wire["intentLapses"] == 1
    assert wire["overridesUsed"] == 1 and wire["overridesRemaining"] == 5
    assert wire["pending"] is False and wire["failOpenReason"] is None


def test_rule_and_profiles_wire_follow_the_ops_switch():
    """契约镜像与 `horizon-profiles` 只读块：总闸关 ⇒ 两者皆 `None`（前端整块不渲染）。"""
    settings = Settings()
    rule = rule_from_settings(settings, exit_confirm_rounds=3)
    assert rule is not None and rule.exit_confirm_rounds == 3   # 原样投影，本层不复算
    assert rule_wire(rule)["maxOverridesPerSession"] == rule.max_overrides_per_session
    block = profiles_block(settings)
    assert [level["id"] for level in block["levels"]] == [CONVICTION_HIGH, CONVICTION_FINAL]
    assert "只放行平仓" in block["note"]        # X-1 在 UI 上的唯一落点
    off = Settings(ai_trade_exit_priority_enabled=False)
    assert rule_from_settings(off, exit_confirm_rounds=2) is None
    assert rule_wire(None) is None
    assert profiles_block(off) is None
