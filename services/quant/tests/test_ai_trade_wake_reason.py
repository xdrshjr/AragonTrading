"""唤醒原因判定单测（ai-trade-wake-aware-context §6.1 T-4/T-5/T-6/T-22 + 本轮 T-19）。

覆盖：九种 kind 的优先级表、`alsoTrue` 不丢证据、ATR / pct 双判据、价不可信 fail-open、
**`horizon_scale` 校准（T-22，P0-2）**、`signal_flip` 的优先级与 `flip` 键缺席回退。纯函数，无 IO。
"""
from __future__ import annotations

import pytest

from app.ai_auto.wake_reason import WAKE_KINDS, WakeFacts, classify_wake, horizon_scale, moved

_THRESHOLDS = {"move_atr_mult": 0.5, "move_pct": 0.0005}


def _classify(facts: WakeFacts, *, horizon: float = 1.0) -> dict:
    return classify_wake(facts, horizon_scale=horizon, **_THRESHOLDS)


def _steady(**overrides) -> WakeFacts:
    """一个「什么都没发生」的定时唤醒基线：空仓、无挂单、同一根、价格纹丝不动。"""
    base = {"trigger": "interval", "position_side": "flat", "has_resting": False,
            "bar_index": 7, "last_round_bar_index": 7, "px": 30000.0,
            "last_round_px": 30000.0, "atr_px": 100.0, "since_last_round_s": 182.0}
    return WakeFacts(**{**base, **overrides})


_FLIP = {"netStanceFrom": "flat", "netStanceTo": "long",
         "strategies": [{"strategyId": 7, "name": "EMA 交叉短线", "from": "flat", "to": "long"}],
         "provisional": True, "heldS": 47.3, "samples": 4, "barTs": 1_785_600_000}


# ---------- T-4 / T-19：九态优先级 ----------
@pytest.mark.parametrize(("overrides", "expected"), [
    ({"trigger": "candle"}, "candle_close"),
    ({"intel": {"itemId": 7, "ref": "I01"}}, "intel_alert"),
    ({"bar_index": 9}, "bar_advanced"),
    ({"flip": _FLIP}, "signal_flip"),
    ({"has_resting": True, "resting_age_s": 240.0}, "resting_order"),
    ({"position_side": "long", "guard_status": "armed", "last_round_guard_status": "idle"},
     "profit_tier"),
    ({"px": 30060.0}, "price_breakout"),                       # |Δ|=60 >= 0.5×100
    ({"position_side": "long", "guard_status": "idle", "last_round_guard_status": "idle"},
     "position_watch"),
    ({}, "heartbeat"),
])
def test_classify_wake_priority_table(overrides, expected):
    assert _classify(_steady(**overrides))["kind"] == expected


def test_every_declared_kind_is_reachable():
    assert set(WAKE_KINDS) == {
        "candle_close", "intel_alert", "bar_advanced", "signal_flip", "resting_order",
        "profit_tier", "price_breakout", "position_watch", "heartbeat"}


# ---------- G-9（后端一侧）：九组注册表的并集断言 ----------
def test_every_wake_kind_is_registered_in_all_backend_tables():
    """`WAKE_KINDS` 与四张后端表**逐值一致**（ai-trade-intel-actionability G-9）。

    四处漏掉都**不报错**：`conditions` 少一条 ⇒ 判定永远落不到那个 kind；`_WAKE_BODY` 少一条
    ⇒ `format_wake_for_prompt` 直接返回空串（**整段唤醒句连同 alsoTrue 一起消失**）；
    `_ALSO_TRUE_LABEL` 少一条 ⇒ 该项被列表推导**静默过滤掉**（证据消失比裸 slug 更难发现）；
    `VALUE_LABEL_ZH` 少一条 ⇒ 经验图节点标签是裸 slug。
    """
    from app.ai_auto.experience.taxonomy import VALUE_LABEL_ZH
    from app.ai_auto.wake_text import _ALSO_TRUE_LABEL, _WAKE_BODY

    kinds = set(WAKE_KINDS)
    # `conditions` 的名字集合：`heartbeat` 是兜底态，不在 `conditions` 里。
    fired = {name for name, _ok in _conditions_names()}
    assert fired | {"heartbeat"} == kinds
    assert set(_WAKE_BODY) == kinds
    # `_ALSO_TRUE_LABEL` 不含 `heartbeat`：兜底态永远不会作为「同时成立项」出现。
    assert set(_ALSO_TRUE_LABEL) | {"heartbeat"} == kinds
    assert kinds <= set(VALUE_LABEL_ZH)


def _conditions_names() -> list[tuple[str, bool]]:
    """把 `classify_wake` 里那张 `conditions` 元组的名字集合抠出来（同一个真源）。"""
    import inspect
    import re

    from app.ai_auto import wake_reason

    source = inspect.getsource(wake_reason.classify_wake)
    block = source.split("conditions = (", 1)[1].split("\n    )", 1)[0]
    return [(name, True) for name in re.findall(r'\("([a-z_]+)",', block)]


def test_intel_alert_is_a_full_tier_kind_but_not_an_execution_full_kind():
    """§3.1.5 #4：升档集加它、执行周期全量披露集**不加**（后者「刻意不增」）。"""
    from app.ai_auto.tactical_context import _EXECUTION_FULL_KINDS, _FULL_TIER_KINDS

    assert "intel_alert" in _FULL_TIER_KINDS
    assert "intel_alert" not in _EXECUTION_FULL_KINDS


def test_intel_key_is_absent_entirely_when_there_is_no_alert():
    """非告警轮 wire **无 `intel` 键**（不是 None）⇒ 逐字节回退 4b9c352 的 payload。"""
    payload = {"itemId": 7, "ref": "I01"}
    assert "intel" not in _classify(_steady())
    assert _classify(_steady(intel=payload))["intel"] is payload


def test_intel_alert_outranks_bar_advanced_but_yields_to_a_real_candle_close():
    """插位：`candle_close` 之后、`bar_advanced` 之前（§3.1.6 / Q2 的裁决）。

    第二条断言是那条算术依据的另一半：告警自己认领的那一轮 `trigger` 恒为 `"interval"`，
    故 `candle_close` 在**那一轮里**恒不成立，`intel_alert` 恒为首位。
    """
    payload = {"itemId": 7}
    wake = _classify(_steady(bar_index=9, intel=payload))
    assert wake["kind"] == "intel_alert"
    assert "bar_advanced" in wake["alsoTrue"]          # 判定只决定标签，绝不丢证据
    assert _classify(_steady(trigger="candle", intel=payload))["kind"] == "candle_close"


# ---------- 本轮轴 C：signal_flip 的插位与 flip 键的加法性 ----------
def test_a_real_new_bar_always_outranks_a_provisional_flip():
    """真的收了新根永远优先：`signal_flip` 只排在 `bar_advanced` **之后**。

    翻转是未确认根上的预演结论，新根是既成事实——把建议性证据排到既成事实前面，就是让 prompt
    的首行说一件更弱的事。
    """
    wake = _classify(_steady(bar_index=9, flip=_FLIP))
    assert wake["kind"] == "bar_advanced"
    assert wake["alsoTrue"][0] == "signal_flip"        # 判定只决定标签，绝不丢证据


def test_flip_key_is_absent_entirely_when_there_is_no_flip():
    """非翻转轮 wire **无 `flip` 键**（不是 None）⇒ 逐字节回退 a7ed70c 的 payload。"""
    assert "flip" not in _classify(_steady())
    assert _classify(_steady(flip=_FLIP))["flip"] is _FLIP


def test_lower_priority_conditions_are_kept_in_also_true_never_dropped():
    """判定只决定标签，绝不丢证据：挂单 + 越阈同时成立 ⇒ kind=resting_order 且 alsoTrue 保留越阈。"""
    wake = _classify(_steady(has_resting=True, resting_age_s=240.0, px=30060.0))
    assert wake["kind"] == "resting_order"
    assert wake["alsoTrue"] == ["price_breakout"]
    assert wake["restingAgeS"] == 240.0


def test_heartbeat_is_never_listed_as_also_true():
    wake = _classify(_steady())
    assert wake["kind"] == "heartbeat"
    assert wake["alsoTrue"] == []


def test_first_round_without_a_previous_bar_index_is_honestly_bar_advanced():
    """首轮确实是「这根 K 线的首次研判」，文案成立（R-8：有意为之，不是误判）。"""
    wake = _classify(_steady(last_round_bar_index=None, last_round_px=None))
    assert wake["kind"] == "bar_advanced"
    assert wake["barAdvanced"] is True


def test_profit_tier_needs_a_real_transition_into_an_armed_status():
    """已 armed 且**未跨档**的持仓轮退回 position_watch——否则每轮都会被误报成「浮盈跨档」。"""
    same = _steady(position_side="long", guard_status="armed", last_round_guard_status="armed")
    assert _classify(same)["kind"] == "position_watch"
    # 保护层未装配 ⇒ guard_status 恒 None ⇒ 该分支恒假，自然退化（R-7）
    unarmed = _steady(position_side="long", guard_status=None, last_round_guard_status=None)
    assert _classify(unarmed)["kind"] == "position_watch"


# ---------- T-5 / T-6：判据 ----------
def test_atr_criterion_beats_and_misses_around_the_threshold():
    facts_over = _steady(px=30060.0)      # |Δ|=60 >= 0.5 × 100
    facts_under = _steady(px=30040.0)     # |Δ|=40 <  0.5 × 100
    assert moved(facts_over, horizon_scale=1.0, **_THRESHOLDS)[0] is True
    assert moved(facts_under, horizon_scale=1.0, **_THRESHOLDS)[0] is False


def test_missing_atr_falls_back_verbatim_to_the_existing_pct_criterion():
    no_atr = _steady(atr_px=None, px=30040.0)
    is_moved, pct, atr_mult = moved(no_atr, horizon_scale=1.0, **_THRESHOLDS)
    assert atr_mult is None
    assert pct == pytest.approx((30040.0 / 30000.0 - 1) * 100, abs=1e-6)
    assert is_moved is (abs(30040.0 / 30000.0 - 1.0) >= 0.0005)     # 与既有守卫同式
    assert is_moved is True


@pytest.mark.parametrize("bad", [None, 0.0, -1.0])
def test_untrustworthy_price_fails_open_and_always_wakes(bad):
    """价拿不准时宁可唤醒（与 F4c 现行极性逐字一致）——绝不因为读不到价就静默跳过。"""
    assert moved(_steady(px=bad), horizon_scale=1.0, **_THRESHOLDS) == (True, None, None)
    assert moved(_steady(last_round_px=bad), horizon_scale=1.0, **_THRESHOLDS) == (True, None, None)


def test_reported_atr_multiple_is_the_raw_physical_quantity_not_the_scaled_one():
    """返回的第三元是给人和模型看的物理量；折算只发生在判据侧，两者都随 wire 下发。"""
    wake = _classify(_steady(px=30060.0), horizon=0.316)
    assert wake["priceMoveAtr"] == pytest.approx(0.6)                # 60/100，**未**折算
    assert wake["horizonScale"] == pytest.approx(0.316)
    assert wake["moveThresholdAtr"] == 0.5                           # 配置值
    assert wake["moveThresholdAtrEff"] == pytest.approx(0.158)       # 实际门槛
    assert wake["kind"] == "price_breakout"


# ---------- T-22（P0-2）：horizon_scale 校准 ----------
def test_horizon_scale_calibration_table():
    assert horizon_scale(180, 1800) == pytest.approx(0.3162, abs=1e-4)     # 30m / 3min
    assert horizon_scale(120, 86400) == pytest.approx(0.0373, abs=1e-4)    # 1D / 2min
    assert horizon_scale(3600, 1800) == 1.0                                # 只缩不放
    assert horizon_scale(1800, 1800) == 1.0
    assert horizon_scale(0, 1800) == 1.0                                   # 退化为原始 ATR 判据
    assert horizon_scale(180, 0) == 1.0


def test_daily_execution_bar_no_longer_skips_every_interval_wake_forever():
    """1D 执行 + 2 分钟唤醒下 0.06% 的位移必须被判为越阈。

    不折算时门槛是 `0.5 × 3%ATR = 1.5%`——两分钟内**不可能**发生 ⇒ 空仓定时唤醒被永久跳过，
    而定时轮恰恰是战术周期证据服务的对象，两轴互相取消。
    """
    px, atr_px = 100_000.0, 3_000.0                    # ATR ≈ 3%（1D 典型）
    facts = WakeFacts(trigger="interval", position_side="flat", bar_index=1,
                      last_round_bar_index=1, px=px * 1.0006, last_round_px=px, atr_px=atr_px)
    scale = horizon_scale(120, 86400)
    assert moved(facts, horizon_scale=scale, **_THRESHOLDS)[0] is True
    assert moved(facts, horizon_scale=1.0, **_THRESHOLDS)[0] is False      # 原写法：永久跳过
