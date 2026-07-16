"""`ai_auto/order_plan.py` 纯映射单测（ai-trade-limit-orders §2.1 / §5.4）。

`decision_to_order_plan` 是「决策 → 委托类型意图」的**唯一**映射点，且刻意不含任何门禁（校验与
价带全在 `decider._map_decision`，P1-5）。故本组用例只锁两件事：限价意图被完整转达、以及
**其余一切情形都回 `None`**——`None` 使 `LiveExecutor.apply(order_plan=None)` 走与今日逐字节
相同的市价路径，是零回归的支点。纯离线（无网络、无 DB）。
"""
from __future__ import annotations

import dataclasses

import pytest

from app.ai_auto.decider import TradeDecision
from app.ai_auto.order_plan import OrderPlan, decision_to_order_plan


def _limit(action: str, px: float = 101.0) -> TradeDecision:
    return TradeDecision(action=action, size_fraction=0.5, confidence=0.9, reason="r",
                         ord_type="limit", limit_px=px)


@pytest.mark.parametrize("action", ["open_long", "open_short", "close"])
def test_limit_decision_maps_to_limit_order_plan_for_each_ordering_action(action):
    """三个会产生真实委托的动作都须转达限价意图（平仓限价复用既有 ClosePositionInput）。"""
    assert decision_to_order_plan(_limit(action)) == OrderPlan(ord_type="limit", px=101.0)


def test_market_decision_maps_to_none():
    """市价 ⇒ `None` ⇒ 执行器走今日路径（零回归）。"""
    decision = TradeDecision(action="open_long", size_fraction=0.5, confidence=0.9,
                             reason="r", ord_type="market")
    assert decision_to_order_plan(decision) is None


def test_hold_decision_maps_to_none():
    assert decision_to_order_plan(_limit("hold")) is None


def test_cancel_order_decision_maps_to_none_even_when_carrying_limit_price():
    """`cancel_order` 不开不平 ⇒ 恒无 OrderPlan，即便决策上挂着一个限价（不该发生亦须挡住）。"""
    assert decision_to_order_plan(_limit("cancel_order")) is None


def test_limit_decision_without_price_maps_to_none():
    """防御：`_map_decision` 已保证 limit ⇒ 有价；此处**绝不猜价**，宁可回落市价路径。"""
    decision = TradeDecision(action="open_long", size_fraction=0.5, confidence=0.9,
                             reason="r", ord_type="limit", limit_px=None)
    assert decision_to_order_plan(decision) is None


def test_positional_four_arg_construction_still_defaults_to_market():
    """零回归门禁：`ord_type`/`limit_px` 是**末位带默认值**的字段。

    全仓既有的 `TradeDecision(action, size, conf, reason)` 位置构造必须继续成立且默认市价——
    该断言一旦失败，说明新字段被插到了中间或缺了默认值，全部既有调用点会静默错位。
    """
    decision = TradeDecision("open_long", 0.5, 0.9, "r")
    assert decision.ord_type == "market"
    assert decision.limit_px is None
    assert decision_to_order_plan(decision) is None


def test_order_plan_is_frozen_value_object():
    """`OrderPlan` 不可变——委托意图定下后不容下游就地改写。"""
    with pytest.raises(dataclasses.FrozenInstanceError):
        decision_to_order_plan(_limit("open_long")).px = 1.0
