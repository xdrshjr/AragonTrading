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
    direction_verdict,
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


# ============ 深度思维链细分出的两个降级码（ai-trade-deep-reasoning-chain §3.3.3）============
def test_split_no_tool_call_codes_stay_inside_the_llm_failure_bucket():
    """`no_tool_call` 被细分成两个更诚实的码之后，**同一个物理事件不得掉出 `llmDegraded`**。

    这是本轮最容易漏的一处：截断与内容拦截在合入前都表现为 `no_tool_call`（在集内）；只改码不
    登记，会让漏斗的「模型调用失败」计数**静默少报**，而界面上看不出任何异常。
    """
    from app.ai_auto.open_funnel import classify_block, is_llm_failure

    for code in ("reasoning_truncated", "content_filtered"):
        assert is_llm_failure(code) is True, code
        # 归类与合入前的 `no_tool_call` 保持一致（它本就不在 `_DEGRADE_PREFIXES` 里 ⇒ "other"）。
        assert classify_block(code) == classify_block("no_tool_call") == "other"


# ============ 方向轴（ai-trade-directional-symmetry 轴 D） ============
#
# 方向切分是对**同一批事件的重新分组**，不是第二次计数——三侧求和必须逐字等于既有的四个标量。

def _full_round(counters, action, *, degrade=None, gate=None, size=0.5, skipped=False,
           fill_side=None):
    """跑一整轮的记点序（与 `session._run_decision_round` 的真实调用序同构）。"""
    counters.note_intent(action, degrade)
    final_degrade = degrade or gate
    final_action = "hold" if final_degrade else action
    counters.note_sizing(final_action, size)
    counters.note_executed(final_action, skipped)
    if fill_side is not None:
        counters.note_filled(fill_side)
    counters.note_block(final_degrade)
    counters.end_round()


def test_by_side_closes_per_direction_and_sums_to_the_scalars():
    counters = OpenFunnelCounters()
    _full_round(counters, "open_long", fill_side="long")
    _full_round(counters, "open_long", gate="low_consensus(0/1)")
    _full_round(counters, "open_short", gate="low_consensus(0/1)")
    _full_round(counters, "open_short", size=0.0)
    _full_round(counters, "hold", degrade="low_confidence")       # 方向不可知
    wire = funnel_wire(counters, directional=True)
    by_side = wire["bySide"]
    # ① 每一侧各自闭合
    for side in ("long", "short", "unknown"):
        row = by_side[side]
        assert sum(row["blocked"].values()) + row["submitted"] + row["sizingZeroed"] \
            <= row["intents"]
    # ② 三侧求和逐字等于既有四个标量（重新分组，不是二次计数）
    assert sum(by_side[s]["intents"] for s in ("long", "short", "unknown")) == wire["intents"]
    assert sum(by_side[s]["submitted"] for s in ("long", "short", "unknown")) == wire["submitted"]
    assert sum(by_side[s]["sizingZeroed"] for s in ("long", "short", "unknown")) \
        == wire["sizingZeroed"]
    assert sum(by_side[s]["filled"] for s in ("long", "short", "unknown")) == wire["filled"]
    flat = {}
    for side in ("long", "short", "unknown"):
        for key, value in by_side[side]["blocked"].items():
            flat[key] = flat.get(key, 0) + value
    assert flat == wire["blocked"]


def test_low_confidence_falls_into_unknown_and_never_becomes_a_fourth_bucket():
    """`low_confidence` 轮记进 `unknown`，**不猜方向**，也绝不写成 `"hold"`（评审 P2-1）。"""
    counters = OpenFunnelCounters()
    _full_round(counters, "hold", degrade="low_confidence")
    assert counters.intents_by_side == {"unknown": 1}
    assert "hold" not in counters.intents_by_side
    assert counters.blocked_by_side["unknown"] == {"confidence": 1}


def test_filled_by_side_uses_the_fill_leg_side():
    """★ 评审 P0-1 的直接守卫：先 `note_executed`（它清 `_intent_pending`）再 `note_filled`。

    若实现去读轮级 `_intent_side`，本条必红——而那正是 v1 的写法，且它**不报任何错**。
    """
    counters = OpenFunnelCounters()
    counters.note_intent("open_short", None)
    counters.note_executed("open_short", False)      # 此刻 `_intent_side` 已被消费清空
    counters.note_filled("short")                    # 跨轮到达的成交腿同理
    counters.end_round()
    counters.note_filled("short")                    # 部分成交的第二条腿
    assert counters.filled_by_side == {"short": 2}
    assert counters.filled == 2


def test_filled_with_an_unknown_side_never_guesses():
    counters = OpenFunnelCounters()
    counters.note_filled("")
    counters.note_filled("close")
    assert counters.filled_by_side == {"unknown": 2}


def test_submitted_and_sizing_zeroed_split_by_side():
    """两个 v1 漏声明的字段各一例（评审 P0-1）。"""
    counters = OpenFunnelCounters()
    _full_round(counters, "open_short")                    # 委托发出
    _full_round(counters, "open_long", size=0.0)           # 仓位解到 0
    assert counters.submitted_by_side == {"short": 1}
    assert counters.sizing_zeroed_by_side == {"long": 1}


def test_intent_side_cleared_on_exception_path():
    """决策轮抛异常时 `note_block` 不会被调到 —— `end_round` 必须兜底清零（R-12 同款）。"""
    counters = OpenFunnelCounters()
    counters.note_intent("open_short", None)
    counters.end_round()                              # 模拟异常路径
    _full_round(counters, "open_long", gate="low_consensus(0/1)")
    assert counters.blocked_by_side == {"long": {"consensus": 1}}   # 绝不泄漏成 short


def test_verdict_denominator_excludes_unknown():
    """`unknown` 一律不进分母：3 多 / 0 空 / 40 不可知 ⇒ `insufficient`，不是 `long_skewed`。"""
    counters = OpenFunnelCounters()
    for _ in range(3):
        _full_round(counters, "open_long")
    for _ in range(40):
        _full_round(counters, "hold", degrade="low_confidence")
    wire = funnel_wire(counters, directional=True)
    balance = wire["directionBalance"]
    assert balance["verdict"] == "insufficient"
    assert balance["unknownIntents"] == 40
    assert direction_verdict(counters) == "insufficient"


def test_direction_verdict_never_fabricates_a_percentage():
    counters = OpenFunnelCounters()
    balance = funnel_wire(counters, directional=True)["directionBalance"]
    assert balance["verdict"] == "insufficient"
    assert balance["shortIntentShare"] is None and balance["shortFillShare"] is None


def test_direction_verdict_thresholds():
    counters = OpenFunnelCounters()
    for _ in range(10):
        _full_round(counters, "open_long")
    assert direction_verdict(counters) == "long_skewed"          # 弱侧 0
    for _ in range(9):
        _full_round(counters, "open_short")
    assert direction_verdict(counters) == "balanced"             # 9/19 远超 15%
    lopsided = OpenFunnelCounters()
    for _ in range(20):
        _full_round(lopsided, "open_short")
    _full_round(lopsided, "open_long")                                # 1/21 < 15%
    assert direction_verdict(lopsided) == "short_skewed"


def test_direction_keys_absent_when_switch_off():
    """总闸关 ⇒ 两键**整键缺席** ⇒ wire 逐字节等于本特性合入前（前端整卡不渲染）。"""
    counters = OpenFunnelCounters()
    _full_round(counters, "open_long")
    wire = funnel_wire(counters)
    assert "bySide" not in wire and "directionBalance" not in wire


def test_contrarian_passed_is_not_a_block():
    """逆势放行是独立计数，**不进 `blocked`**（镜像 `sizing_zeroed` 不是一道闸的纪律）。"""
    counters = OpenFunnelCounters()
    counters.note_intent("open_short", None)
    counters.note_contrarian()
    counters.note_executed("open_short", False)
    counters.note_block(None)
    counters.end_round()
    assert counters.contrarian_passed == 1
    assert counters.blocked == {} and counters.blocked_by_side == {}
    assert funnel_wire(counters, directional=True)["directionBalance"]["contrarianPassed"] == 1
