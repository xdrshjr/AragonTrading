"""`TradeDecision` → `OrderPlan` 的**纯映射**（ai-trade-limit-orders §2.1 / §2.6）。

本模块**不做任何校验、不产生任何降级**——`orderType`/`limitPrice` 的合法性与价带门禁全部在
`decider._map_decision` 内完成（P1-5/R-15：重试循环只认 `_map_decision` 返回的 degrade，门禁落在
这里会使「可重试」承诺静默失效）。到了本模块，决策已是可信的。

为何限价意图走 `OrderPlan` 独立通路、而不给 `strategies/base.Signal` 加价格字段：`Signal` 被全部
5 个内置策略、`SimBroker`、回测、纸面模拟共用。给它加 `px` 会把「交易所委托价」这个执行层概念漏进
策略信号词汇表，且 `SimBroker` 会**静默忽略**该字段——导致 AI 回测「每单必成」而实盘「可能不成」，
产生系统性高估。故限价意图走 `LiveExecutor.apply(..., order_plan=...)` 独立入参，`order_plan=None`
即市价，`live_auto` 的 6 处调用点一字不改（R-9）。
"""
from __future__ import annotations

from dataclasses import dataclass

from .decider import ORDERING_ACTIONS, TradeDecision


@dataclass(frozen=True)
class OrderPlan:
    """一次真实委托的订单类型意图。"""

    ord_type: str        # "market" | "limit"
    px: float | None     # limit 时为已过价带门禁的委托价；market 恒 None


def decision_to_order_plan(decision: TradeDecision) -> OrderPlan | None:
    """决策 → 订单类型意图；市价 / 不下单的动作一律返回 `None`（= 今日行为，逐字零回归）。

    Args:
        decision: 本轮最终决策（已过 `_map_decision` 的全部门禁与反思 / 共识降级）。

    Returns:
        限价决策 → `OrderPlan("limit", px)`；其余（市价、hold、cancel_order、缺价）→ `None`。
        返回 `None` 使 `LiveExecutor.apply(..., order_plan=None)` 走与今日**逐字节相同**的市价路径。
    """
    # 会产生真实委托的动作单一真源在 decider（F-2 消灭第二真源；order_plan 本就 import decider，无环）。
    if decision.action not in ORDERING_ACTIONS:
        return None
    if getattr(decision, "ord_type", "market") != "limit":
        return None
    px = getattr(decision, "limit_px", None)
    if px is None:
        return None                  # 防御：_map_decision 已保证 limit ⇒ 有价，此处只是不猜
    return OrderPlan(ord_type="limit", px=float(px))
