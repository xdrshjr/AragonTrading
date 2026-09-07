"""往返成本模型纯核心单测（ai-trade-cost-accounting-and-edge-gate §7.2）。

纯函数、无网络、无 LLM：六个分支 + 两个标定值 + 资金费率方向性 + degraded 与「有意不建模」的区分。
最重要的一条钉子是**「缺数据」与「有意不建模」不是一回事**（用例 3 vs 用例 7）：前者必须降级并收紧
门禁阈值，后者是运维的选择、置信度保持 ok——混为一谈会让门禁在断网时悄悄放松。
"""
from __future__ import annotations

from app.ai_auto.cost_model import (
    CostParams,
    CostQuery,
    backtest_params,
    cost_wire,
    estimate_round_trip_cost,
    query_for_decision,
    resolve_cost_params,
)
from app.ai_auto.decider import TradeDecision
from app.config import Settings

_SETTINGS = Settings()
_PARAMS = resolve_cost_params(_SETTINGS)
_BAR_S = 900          # 15m


def _query(**over) -> CostQuery:
    base = {"ord_type": "market", "side": "long",
            "bar_seconds": _BAR_S, "funding_rate": 0.0,
            "funding_enabled": "funding_rate" in over}
    base.update(over)
    return CostQuery(**base)


def test_market_round_trip_matches_calibration_13_bps():
    """用例 1：市价默认参数 ⇒ 5 + 5 + 2×(1.0 + 0.5) = 13.0 bps。"""
    cost = estimate_round_trip_cost(_query(), _PARAMS)
    assert cost.total_bps == 13.0
    assert cost.crossing_legs == 2
    assert cost.confidence == "ok"
    assert cost.model_version == 3
    assert (cost.fee_open_bps, cost.fee_close_bps) == (5.0, 5.0)


def test_limit_open_may_take_and_crosses_both_legs():
    """v2 普通限价开仓 ⇒ 5 + 5 + 2×1.5 = 13 bps。"""
    cost = estimate_round_trip_cost(_query(ord_type="limit"), _PARAMS)
    assert cost.total_bps == 13.0
    assert cost.fee_open_bps == 5.0
    assert cost.fee_close_bps == 5.0       # 平仓腿的委托类型未知，保守恒取 taker
    assert cost.crossing_legs == 2
    assert cost.liquidity_assumption == "may_take"


def test_guaranteed_maker_assumption_requires_explicit_contract():
    cost = estimate_round_trip_cost(_query(
        ord_type="limit", liquidity_assumption="maker_guaranteed"), _PARAMS)
    assert cost.total_bps == 8.5
    assert cost.fee_open_bps == 2
    assert cost.crossing_legs == 1


def test_missing_funding_rate_degrades_and_is_excluded_from_total():
    """用例 3：`funding_rate=None` ⇒ 项为 None + confidence=degraded + total 不含该项（绝不按 0 计）。"""
    cost = estimate_round_trip_cost(_query(funding_rate=None), _PARAMS)
    assert cost.funding_carry_bps is None
    assert cost.confidence == "degraded"
    assert cost.total_bps == 13.0          # 与 ok 分支同值，但置信度不同 ⇒ 门禁阈值会被收紧


def test_long_side_pays_one_funding_period():
    """用例 4：rate=0.0001 / 8 根 15m（=2h）⇒ periods=1 ⇒ 1.0 bps 计入多头成本。"""
    cost = estimate_round_trip_cost(_query(funding_rate=0.0001), _PARAMS)
    assert cost.funding_carry_bps == 1.0
    assert cost.total_bps == 14.0
    assert cost.confidence == "degraded"  # 费率有值，安排仍未确认。


def test_short_side_gets_no_discount_from_receiving_funding():
    """用例 5（方向性）：同一正费率下空头是**收**费的一侧 ⇒ 恒 0.0，绝不给 edge 折扣。"""
    cost = estimate_round_trip_cost(_query(side="short", funding_rate=0.0001), _PARAMS)
    assert cost.funding_carry_bps == 0.0
    assert cost.total_bps == 13.0


def test_backtest_params_match_simbroker_actual_cost():
    """用例 6：回测世界 ⇒ 5 + 5 + 2×2.0 = 14.0 bps（SimBroker 实收双边费 + 双边滑点）。"""
    cost = estimate_round_trip_cost(_query(), backtest_params(_SETTINGS))
    assert cost.total_bps == 14.0
    assert cost.impact_bps == 0.0
    assert cost.fee_open_bps == cost.fee_close_bps == _SETTINGS.default_taker_fee_bps


def test_funding_disabled_is_a_choice_not_missing_data():
    """总闸关 ⇒ `funding_rate == 0.0`（**不是 None**）⇒ confidence 保持 ok（§3.5）。"""
    query = query_for_decision(
        TradeDecision(action="open_long", size_fraction=0.5, confidence=0.7, reason="x"),
        bar_seconds=_BAR_S, funding=None, funding_enabled=False)
    assert query.funding_rate == 0.0
    assert estimate_round_trip_cost(query, _PARAMS).confidence == "ok"


def test_funding_enabled_but_snapshot_missing_degrades():
    """总闸开但快照缺席（陈旧 / 从未取到）⇒ `funding_rate is None` ⇒ degraded。"""
    query = query_for_decision(
        TradeDecision(action="open_short", size_fraction=0.5, confidence=0.7, reason="x"),
        bar_seconds=_BAR_S, funding=None, funding_enabled=True)
    assert query.funding_rate is None
    assert query.side == "short"
    assert estimate_round_trip_cost(query, _PARAMS).confidence == "degraded"


def test_query_for_decision_carries_limit_intent_and_snapshot_interval():
    """限价决策 ⇒ ord_type 透传；快照存在 ⇒ rate/interval 取自快照。"""

    class _Snap:
        rate = 0.0002
        interval_s = 3600.0

    query = query_for_decision(
        TradeDecision(action="open_long", size_fraction=0.5, confidence=0.7, reason="x",
                      ord_type="limit", limit_px=100.0),
        bar_seconds=_BAR_S, funding=_Snap(), funding_enabled=True)
    assert query.ord_type == "limit"
    assert (query.funding_rate, query.funding_interval_s) == (0.0002, 3600.0)
    # 8 根 15m = 2h，结算周期 1h ⇒ 2 期 × 2 bps = 4 bps。
    assert estimate_round_trip_cost(query, _PARAMS).funding_carry_bps == 4.0


def test_half_spread_override_replaces_fallback_constant():
    """盘口半价差一旦可用（阶段二）即取代常数回落值；负值 / 非有限一律回落。"""
    assert estimate_round_trip_cost(_query(half_spread_bps=3.0), _PARAMS).half_spread_bps == 3.0
    assert estimate_round_trip_cost(_query(half_spread_bps=-1.0), _PARAMS).half_spread_bps == 1.0
    assert estimate_round_trip_cost(_query(half_spread_bps=None), _PARAMS).half_spread_bps == 1.0


def test_cost_wire_is_camel_case_and_none_safe():
    assert cost_wire(None) is None
    wire = cost_wire(estimate_round_trip_cost(_query(), _PARAMS))
    assert wire["totalBps"] == 13.0
    assert wire["crossingLegs"] == 2
    assert wire["confidence"] == "ok"


def test_zero_hold_bars_still_charges_one_funding_period():
    """跨结算点的持仓要付整期：`periods` 有下界 1，绝不出现「持仓 0 期 ⇒ 免费」。"""
    params = CostParams(maker_fee_bps=2.0, taker_fee_bps=5.0, half_spread_fallback_bps=1.0,
                        impact_bps=0.5, expected_hold_bars=1)
    cost = estimate_round_trip_cost(_query(funding_rate=0.0001, bar_seconds=60), params)
    assert cost.funding_carry_bps == 1.0
