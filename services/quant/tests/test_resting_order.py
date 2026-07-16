"""`live_auto/resting.py` 纯函数单测（ai-trade-limit-orders §2.3 / §2.6）。

覆盖挂单生命周期的四组纯逻辑：OKX state 归一（**兜底方向必须是「不动」**）、TTL 边界、成交增量
采纳（R-19 防重复计入）、限价意图校验与价带门禁（§2.6）。本模块无 IO、无状态，故全部用例
**不构造 executor / decider、不落库、不联网**。
"""
from __future__ import annotations

import pytest

from app.live_auto.resting import (
    DEGRADE_INVALID_ORDER_TYPE,
    DEGRADE_LIMIT_PRICE_OUT_OF_BAND,
    DEGRADE_MISSING_LIMIT_PRICE,
    RestingOrder,
    adopt_fill_delta,
    build_resting,
    classify_state,
    fill_delta,
    is_expired,
    is_same_intent,
    resting_payload,
    validate_limit_intent,
)


def _order(**overrides) -> RestingOrder:
    """一笔基线挂单：限价买多 0.03 张 @ 100.0，1000 时刻挂出、1300 到期。"""
    values = dict(cl_ord_id="aragon-1", ord_id="okx-1", intent="open", side="long",
                  px=100.0, sz=0.03, strength=0.5, reason="r", created_ts=1000,
                  expire_ts=1300, adopted_sz=0.0)
    values.update(overrides)
    return RestingOrder(**values)


# ---------------- classify_state：未知一律 unknown（兜底方向必须是「不动」）----------------


@pytest.mark.parametrize("state, expected", [
    ("live", "live"),
    ("partially_filled", "partial"),
    ("filled", "filled"),
    ("canceled", "canceled"),
    ("mmp_canceled", "canceled"),
])
def test_classify_state_maps_each_okx_state(state, expected):
    assert classify_state(state) == expected


@pytest.mark.parametrize("state", ["", None, "unknown_future_state", "LIVE"])
def test_classify_state_falls_back_to_unknown_for_unrecognized_state(state):
    """未知 / 空 / None → `"unknown"`（语义＝保留挂单、下轮再判）。

    绝不猜成任何一个会触发动作的态：`"canceled"` 会丢掉真实挂单、`"filled"` 会凭空采纳持仓。
    """
    assert classify_state(state) == "unknown"


# ---------------- TTL ----------------


def test_is_expired_is_true_at_exact_expire_ts():
    """边界：`now == expire_ts` 即到期（实现用 `>=`），不留「刚好卡在到期点仍不撤」的缝。"""
    assert is_expired(_order(expire_ts=1300), 1300) is True


def test_is_expired_is_false_before_expire_ts():
    assert is_expired(_order(expire_ts=1300), 1299) is False


def test_build_resting_derives_expire_ts_from_now_plus_ttl():
    """TTL 单一真源：`expire_ts` 由 `now_ts + ttl_s` 派生，绝不在别处重复算。"""
    order = build_resting(cl_ord_id="c", ord_id="o", intent="open", side="long", px=100.0,
                          sz=0.03, strength=0.5, reason="r", now_ts=1000, ttl_s=300.0)
    assert order.created_ts == 1000
    assert order.expire_ts == 1300
    assert order.adopted_sz == 0.0


def test_build_resting_clamps_negative_ttl_to_zero():
    """负 TTL（配置误填）夹到 0 ⇒ 立即到期，而非把 `expire_ts` 推到 `created_ts` 之前。"""
    order = build_resting(cl_ord_id="c", ord_id="o", intent="open", side="long", px=100.0,
                          sz=0.03, strength=0.5, reason="r", now_ts=1000, ttl_s=-60.0)
    assert order.expire_ts == 1000
    assert is_expired(order, 1000) is True


# ---------------- 成交增量采纳（R-19）----------------


def test_fill_delta_returns_increment_over_adopted_sz():
    """`accFillSz` 是**累计**值：本轮新增 = 累计 - 已采纳，逐轮全量采纳会把持仓越加越多。"""
    assert fill_delta(_order(adopted_sz=0.01), 0.03) == pytest.approx(0.02)


def test_fill_delta_clamps_negative_increment_to_zero():
    """异常回读（累计值倒退）不得产生负增量——否则会反向减仓。"""
    assert fill_delta(_order(adopted_sz=0.03), 0.01) == 0.0


def test_fill_delta_treats_missing_acc_fill_sz_as_zero():
    assert fill_delta(_order(adopted_sz=0.0), None) == 0.0


def test_adopt_fill_delta_returns_new_object_and_leaves_original_untouched():
    """`frozen=True` 的不可变契约：推进 `adopted_sz` 生成**新值对象**，原对象一字不改。"""
    order = _order(adopted_sz=0.01)
    advanced = adopt_fill_delta(order, 0.03)
    assert advanced is not order
    assert advanced.adopted_sz == pytest.approx(0.03)
    assert order.adopted_sz == pytest.approx(0.01)   # 原对象不可变
    assert advanced.cl_ord_id == order.cl_ord_id     # 其余字段原样承继


def test_adopt_fill_delta_never_regresses_adopted_sz():
    """已采纳张数只增不减（实现取 max）——回读倒退时若跟着退，下轮会把同一笔重复采纳。"""
    assert adopt_fill_delta(_order(adopted_sz=0.03), 0.01).adopted_sz == pytest.approx(0.03)


# ---------------- 幂等：同向同价不重挂（P2-3）----------------


def test_is_same_intent_true_only_when_intent_side_and_px_all_match():
    assert is_same_intent(_order(), intent="open", side="long", px=100.0) is True


@pytest.mark.parametrize("kwargs", [
    {"intent": "close", "side": "long", "px": 100.0},   # intent 不同
    {"intent": "open", "side": "short", "px": 100.0},   # 方向不同
    {"intent": "open", "side": "long", "px": 100.1},    # 价不同 ⇒ 必须撤旧挂新
])
def test_is_same_intent_false_when_any_field_differs(kwargs):
    """任一字段不同即非同一意图——判同会让 Agent 的改价决策被静默吞掉。"""
    assert is_same_intent(_order(), **kwargs) is False


# ---------------- 决策 prompt 只读上下文（§4.2）----------------


def test_resting_payload_is_none_without_resting_order():
    """无挂单 ⇒ 该段整体省略（返回 None）。"""
    assert resting_payload(None, 1100) is None


def test_resting_payload_exposes_age_and_remaining_ttl():
    """Agent 看不见自己的挂单就无法就它做决策——这是 `cancel_order` 可用的前提。"""
    assert resting_payload(_order(adopted_sz=0.01), 1047) == {
        "intent": "open", "side": "long", "px": 100.0, "sz": 0.03,
        "ageS": 47, "expiresInS": 253, "filledSz": 0.01,
    }


def test_resting_payload_clamps_age_and_expiry_at_zero_when_clock_is_past_expiry():
    """时钟越过到期点（或倒流）时不得渲染出负秒数。"""
    payload = resting_payload(_order(created_ts=1000, expire_ts=1300), 900)
    assert payload["ageS"] == 0
    payload = resting_payload(_order(created_ts=1000, expire_ts=1300), 1500)
    assert payload["expiresInS"] == 0


# ---------------- 限价意图校验 + 价带门禁（§2.6）----------------


def test_validate_limit_intent_passes_market_through():
    assert validate_limit_intent({"orderType": "market"}, 100.0, 0.02) == ("market", None, None)


def test_validate_limit_intent_defaults_to_market_when_order_type_absent():
    """缺省即市价 ⇒ 今日行为逐字回退（`allow_limit=False` 时 LLM 根本看不到该字段）。"""
    assert validate_limit_intent({}, 100.0, 0.02) == ("market", None, None)


def test_validate_limit_intent_rejects_limit_without_price():
    assert validate_limit_intent({"orderType": "limit"}, 100.0, 0.02) == (
        "market", None, DEGRADE_MISSING_LIMIT_PRICE)


def test_validate_limit_intent_rejects_unknown_order_type():
    assert validate_limit_intent({"orderType": "post_only"}, 100.0, 0.02) == (
        "market", None, DEGRADE_INVALID_ORDER_TYPE)


def test_validate_limit_intent_accepts_price_within_band():
    assert validate_limit_intent({"orderType": "limit", "limitPrice": 101.0},
                                 100.0, 0.02) == ("limit", 101.0, None)


def test_validate_limit_intent_rejects_price_out_of_band():
    """LLM 幻觉价（BTC 报 60000 却挂 6000）必须在本地拦下——挂在深处的单一旦被下穿就真成交。"""
    assert validate_limit_intent({"orderType": "limit", "limitPrice": 6000.0},
                                 60000.0, 0.02) == ("market", None, DEGRADE_LIMIT_PRICE_OUT_OF_BAND)


@pytest.mark.parametrize("limit_px", [102.0, 98.0])
def test_validate_limit_intent_accepts_deviation_exactly_at_band_edge(limit_px):
    """边界：偏离**恰等于** `max_dev` 时放行（实现以 `>` 拒绝），上下两侧对称。"""
    ord_type, px, degrade = validate_limit_intent(
        {"orderType": "limit", "limitPrice": limit_px}, 100.0, 0.02)
    assert (ord_type, px, degrade) == ("limit", limit_px, None)


@pytest.mark.parametrize("last_px", [None, 0.0, -1.0, float("nan")])
def test_validate_limit_intent_degrades_when_last_px_is_unusable(last_px):
    """`last_px` 缺失 / 非正 ⇒ **降级**而非跳过门禁——无参考价时放行等同于没有护栏。"""
    ord_type, px, degrade = validate_limit_intent(
        {"orderType": "limit", "limitPrice": 101.0}, last_px, 0.02)
    assert (ord_type, px, degrade) == ("market", None, DEGRADE_LIMIT_PRICE_OUT_OF_BAND)


@pytest.mark.parametrize("limit_px", [0.0, -5.0, float("nan"), float("inf"), float("-inf"),
                                      "not-a-number", None])
def test_validate_limit_intent_degrades_on_non_positive_or_nonfinite_limit_price(limit_px):
    """NaN / inf / 非正 / 不可解析的委托价一律降级（守 `_positive_float` 的健壮性）。

    若 NaN 漏过门禁，`abs(nan-ref)/ref > max_dev` 恒为 False —— **价带会静默失效**。
    """
    ord_type, px, degrade = validate_limit_intent(
        {"orderType": "limit", "limitPrice": limit_px}, 100.0, 0.02)
    assert (ord_type, px, degrade) == ("market", None, DEGRADE_MISSING_LIMIT_PRICE)


def test_validate_limit_intent_never_returns_half_limit_intent_on_degrade():
    """降级路径恒为 `("market", None, reason)`——绝不携带半截限价意图流向下游。"""
    for raw in ({"orderType": "limit"},
                {"orderType": "limit", "limitPrice": 6000.0},
                {"orderType": "bogus", "limitPrice": 101.0}):
        ord_type, px, degrade = validate_limit_intent(raw, 100.0, 0.02)
        assert degrade is not None
        assert ord_type == "market" and px is None
