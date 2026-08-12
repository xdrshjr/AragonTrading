"""开仓漏斗纯核心（ai-trade-tradability-and-open-funnel 轴 C · G-10 / G-10c / G-11 / G-12）。

会话级的调用序与闭合不变式在 `test_ai_trade_funnel_session.py`（G-10b）。
"""
from __future__ import annotations

from app.ai_auto.discipline_view import StrictnessFacts, strictness_hint
from app.ai_auto.open_funnel import (
    LAYERS,
    OpenFunnelCounters,
    classify_block,
    conversion,
    funnel_advice,
    funnel_wire,
    is_llm_failure,
    top_blocker,
)


def _round(counters: OpenFunnelCounters, action: str, degrade: str | None = None) -> None:
    """喂一整轮：第一阶段记 intent → 轮末归类（与会话的真实调用序同构）。"""
    counters.note_intent(action, degrade)
    counters.note_block(degrade)
    counters.end_round()


# ---------- G-10：分母口径 ----------
def test_intents_count_only_genuine_open_wishes():
    counters = OpenFunnelCounters()
    _round(counters, "open_long")
    _round(counters, "hold")
    _round(counters, "open_short", "llm_error")
    assert counters.intents == 1
    assert counters.llm_degraded == 1


def test_close_rounds_never_enter_the_open_denominator():
    counters = OpenFunnelCounters()
    _round(counters, "close")
    assert counters.intents == 0 and counters.blocked == {}


# ---------- G-10c：`low_confidence` 只计一次，且不属于 llmDegraded ----------
def test_low_confidence_lands_in_exactly_one_bucket():
    """它是 genuine 决策（`config.py` 明确不计入重试），**不是** LLM 故障。

    真实断面（`decider._map_decision:393-398`）：模型给了 `open_long`，置信度低于门槛 ⇒ 返回
    `_hold("low_confidence")`，故 `note_intent` 看到的 `action` 已是 `hold`。**它仍是一次真实的
    开仓意愿**，必须计进分母，否则置信度门禁在漏斗里恒不可见（见 spec §实施发现 I-1）。
    """
    counters = OpenFunnelCounters()
    counters.note_intent("hold", "low_confidence")
    counters.note_block("low_confidence")
    counters.end_round()
    assert counters.intents == 1
    assert counters.llm_degraded == 0
    assert is_llm_failure("low_confidence") is False
    assert classify_block("low_confidence") == "confidence"
    assert counters.blocked == {"confidence": 1}


def test_a_blocked_open_intent_is_counted_once_in_confidence():
    """对照：第一阶段直出 `open_long`、轮末被下游闸归类 —— 同样只计一次。

    合入前 v1 会把 `low_confidence` 同时计进 `llmDegraded` 与 `blocked["confidence"]`——同一轮
    在漏斗的两个格子里各出现一次，图当场不闭合。
    """
    counters = OpenFunnelCounters()
    counters.note_intent("open_long", None)          # 第一阶段是真实开仓意愿
    counters.note_block("low_confidence")            # 轮末归类
    counters.end_round()
    assert counters.intents == 1
    assert counters.blocked == {"confidence": 1}
    assert counters.llm_degraded == 0


# ---------- G-11：逐层归类 + 未知前缀归 other（绝不静默丢弃）----------
def test_every_layer_prefix_classifies_to_its_own_bucket():
    cases = {
        "base_rule_ma_band(pctB=0.42)": "baseRuleMaBand",
        "horizon_min_hold": "horizon",
        "horizon_exit_unconfirmed": "horizon",
        "horizon_turnover_hysteresis(2.0)": "horizon",
        "horizon_turnover_budget(6/day)": "turnover",
        "horizon_reentry_cooldown(120s,long)": "turnover",
        "horizon_backstop_uncovered(40%)": "exchangeBackstop",
        "profit_cooldown(90s,long)": "profitCooldown",
        "drawdown_brake_halt(dd=9.0%)": "drawdownBrake",
        "loss_streak_cooldown(3,240s)": "drawdownBrake",
        "low_consensus(1/3)": "consensus",
        "low_confidence": "confidence",
        "edge_below_cost(edge=8.1bps,cost=13.0bps,k=1.5)": "edgeGate",
        "reflection_abort": "reflection",
    }
    for degrade, key in cases.items():
        assert classify_block(degrade) == key, degrade
        assert key in LAYERS


def test_unknown_prefix_falls_into_other_instead_of_vanishing():
    """静默丢弃会让 `sum(blocked)` 小于真实拦截数——而这张图唯一的价值就是它闭合。"""
    assert classify_block("some_brand_new_gate(x=1)") == "other"
    assert classify_block(None) is None
    counters = OpenFunnelCounters()
    counters.note_intent("open_long", None)
    counters.note_block("some_brand_new_gate(x=1)")
    assert counters.blocked == {"other": 1}


def test_sizing_zero_is_not_a_gate_and_is_not_in_layers():
    """[P0-4a] `sizingZero` **不在** `LAYERS` 里：`_apply_sizing` 不产生 degrade，把它留在层表里
    就是一条永远点不亮的死枚举。它由独立计数承载，且**不进 `blocked`**。"""
    assert "sizingZero" not in LAYERS
    counters = OpenFunnelCounters()
    counters.note_intent("open_long", None)
    counters.note_sizing("open_long", 0.0)
    counters.note_block(None)
    counters.end_round()
    assert counters.sizing_zeroed == 1
    assert counters.blocked == {}
    assert counters.submitted == 0


def test_sizing_zero_needs_a_real_open_action():
    """被门禁拦下的轮次此刻 `action` 已是 hold，不判 action 会把每一轮观望都记成「解到 0」。"""
    counters = OpenFunnelCounters()
    counters.note_intent("open_long", None)
    counters.note_sizing("hold", None)               # 门禁后的动作
    assert counters.sizing_zeroed == 0


# ---------- top_blocker / conversion / advice ----------
def test_top_blocker_prefers_the_earlier_gate_on_a_tie():
    counters = OpenFunnelCounters()
    counters.blocked = {"edgeGate": 3, "consensus": 3}
    assert top_blocker(counters) == ("consensus", 3)   # consensus 在决策链上更靠前
    assert top_blocker(OpenFunnelCounters()) is None


# ---------- G-12：零样本绝不造百分比 ----------
def test_conversion_is_null_when_no_intent_was_ever_recorded():
    counters = OpenFunnelCounters()
    assert conversion(counters) is None
    assert funnel_wire(counters)["conversion"] is None
    assert funnel_wire(None) is None


def test_conversion_is_a_real_ratio_once_there_are_intents():
    counters = OpenFunnelCounters()
    counters.intents, counters.filled = 4, 1
    assert conversion(counters) == 0.25


def test_wire_shape_carries_labels_and_scope():
    counters = OpenFunnelCounters()
    counters.intents, counters.filled = 12, 0
    counters.blocked = {"edgeGate": 11, "consensus": 1}
    wire = funnel_wire(counters)
    assert wire["topBlocker"] == {"key": "edgeGate", "label": "最小 edge 门禁", "count": 11}
    assert wire["layers"] == list(LAYERS)
    assert "累计" in wire["scope"]       # 口径词必须随 wire 下发（与滚动窗口口径区分）


def test_advice_names_the_blocker_and_defers_the_bar_question_to_the_probe():
    counters = OpenFunnelCounters()
    counters.intents = 12
    counters.blocked = {"edgeGate": 11}
    text = funnel_advice(counters, "infeasible")
    assert text is not None and "最小 edge 门禁" in text and "时间级别" in text
    assert funnel_advice(counters, "ok") is not None
    counters.filled = 1
    assert funnel_advice(counters, "infeasible") is None      # 开过仓 ⇒ 不下结论
    assert funnel_advice(None, "infeasible") is None


def test_every_hint_branch_is_plain_text_not_markdown():
    """五档规则链的产出直接进 JSX 文本节点（`DisciplineCard` 的 `hint`），**不经 markdown**。

    终审逮到的复发缺陷：三条新文案里写过 `**时间级别**` / `**一次都没有提出开仓**` /
    `**平仓与反手**`，而渲染点是纯文本子节点 ⇒ 用户看到的是字面星号。本仓库为这条栽过一次，
    故把「逐档都不含 `**`」固化成判据——`funnel_advice` 与 `strictness_hint` 两处产文案都要守。
    """
    rows = [{"key": "edgeGate", "label": "最小 edge 门禁", "armed": True, "firedCount": 3}]
    blocked = OpenFunnelCounters()
    blocked.intents, blocked.blocked = 12, {"edgeGate": 11}
    empty = OpenFunnelCounters()
    branches = [
        StrictnessFacts(rounds_completed=20, opens=0, tradability_level="infeasible"),   # 第 2 档
        StrictnessFacts(rounds_completed=20, opens=0, funnel=funnel_wire(empty)),        # 第 3 档
        StrictnessFacts(rounds_completed=20, opens=0, funnel=funnel_wire(blocked)),      # 第 4 档
        StrictnessFacts(rounds_completed=20, opens=0),                                   # 第 5 档兜底
    ]
    texts = [strictness_hint(rows, facts) for facts in branches]
    assert all(text for text in texts)          # 四档都必须真的给出文案（不是恒 None 的空转）
    for text in texts:
        assert "**" not in text, text
    assert "**" not in (funnel_advice(blocked, "infeasible") or "")
    # 逐层建议表同源同判据：任一条混进 markdown 标记都会在同一个渲染点上显形。
    for key in LAYERS:
        one = OpenFunnelCounters()
        one.intents, one.blocked = 3, {key: 2}
        hint = strictness_hint(rows, StrictnessFacts(rounds_completed=20, opens=0,
                                                     funnel=funnel_wire(one)))
        assert hint is not None and "**" not in hint, key
