"""`cancel_order` 动作的双层防线单测（ai-trade-limit-orders §2.2a，P0-1）。

本特性**风险最高的单点**：`decision_to_signals` 此前的末行是裸兜底 `return [Signal(side="close")]`
——若 session 层漏了 `cancel_order` 前置分发，Agent 说「撤掉我那笔没成交的挂单」会被系统翻译成
**「市价平掉真实持仓」**：不抛异常、不留错误、盈亏照记。故两层独立防线各配正面守门测试：

- 第一层（分发）：`AiTradeSession._execute_decision` 先判 `cancel_order` → 只撤挂单，根本不调
  `decision_to_signals`。
- 第二层（白名单兜底，M-12）：`decision_to_signals` 未知动作一律 `[]`，**绝不**兜底成 close。

铁律：占位假凭证，绝不写真实凭证；OKX 调用全程经注入桩，不触网。
"""
from __future__ import annotations

from app.ai_auto.decider import AiTradeDecider, TradeDecision
from app.ai_auto.signals import decision_to_signals
from app.config import Settings
from app.engine.simulator import RiskParams
from app.live_auto.executor import LiveExecutor
from app.okx.accounts import AccountCredentials

_REF_PX = 30000.0
_TS = 1000


class _Sig:
    def __init__(self, side: str, strength: float = 1.0, reason: str = ""):
        self.side = side
        self.strength = strength
        self.reason = reason


def _make_executor():
    """带桩 executor；`closes` 计数是「绝不误平真仓」断言的核心证据。"""
    calls = {"orders": [], "closes": [], "cancels": []}

    async def place_order_fn(order):
        calls["orders"].append(order)
        return {"ok": True, "ordId": "x1", "clOrdId": "aragonx"}

    async def close_position_fn(close):
        calls["closes"].append(close)
        return {"ok": True, "clOrdId": "aragoncx"}

    async def positions_fn():
        return []

    async def cancel_order_fn(cancel):
        calls["cancels"].append(cancel)
        return {"outcome": "canceled"}

    async def get_order_fn(cl_ord_id):
        return {"state": "live"}

    executor = LiveExecutor(
        creds=AccountCredentials("ak-fake", "sk-fake-000000", "pp-fake-000000"),
        account_id=1, inst_id="BTC-USDT-SWAP", td_mode="cross",
        pos_mode="long_short_mode", leverage=3.0, budget_usd=1000.0, risk=RiskParams(),
        ct_val=0.01, lot_sz=0.01, min_sz=0.01, settings=Settings(),
        place_order_fn=place_order_fn, close_position_fn=close_position_fn,
        positions_fn=positions_fn, cancel_order_fn=cancel_order_fn,
        get_order_fn=get_order_fn)
    return executor, calls


# ---------- 第二层：白名单兜底（M-12）----------

def test_decision_to_signals_never_falls_back_to_close():
    """【P0-1 守门】未知 / 非交易动作一律 `[]`——绝不兜底成 close 信号。

    这条同时锁住「未来再加新动作时不会重蹈覆辙」：兜底的方向必须是「不动」，不能是「动」。
    """
    for action in ("cancel_order", "unknown_future_action", "", "hold"):
        decision = TradeDecision(action, None, 0.9, "r")
        assert decision_to_signals(decision) == [], f"{action} 不得产生任何信号"


def test_decision_to_signals_still_maps_the_three_trading_actions():
    """白名单化不得误伤既有三个真实交易动作（零回归）。"""
    assert decision_to_signals(TradeDecision("open_long", 0.5, 0.9, "r"))[0].side == "long"
    assert decision_to_signals(TradeDecision("open_short", 0.5, 0.9, "r"))[0].side == "short"
    assert decision_to_signals(TradeDecision("close", None, 0.9, "r"))[0].side == "close"


def test_close_without_reason_keeps_default_ai_close_reason():
    """`close` 的既有默认理由 `ai_close` 不变（白名单化只改分支结构，不改语义）。"""
    assert decision_to_signals(TradeDecision("close", None, None, ""))[0].reason == "ai_close"


# ---------- 第一层 + 执行器：绝不触碰持仓 ----------

async def test_cancel_order_never_closes_open_position():
    """【P0-1 正面守门】**持有真实多仓**时撤单：`close_position` 桩零调用、projected 仍为 long。

    注意 `cancel_order_with_no_resting_is_benign` 是 flat 场景，**测不出**本缺陷——两条都要。
    """
    ex, calls = _make_executor()
    await ex.apply([_Sig("long")], ref_px=_REF_PX, bar_index=0, ts=_TS)
    assert ex.projected == "long"
    await ex.cancel_resting("agent", _TS + 1)
    assert calls["closes"] == []            # 绝不触发任何平仓调用
    assert ex.projected == "long"           # 真仓原样保留
    assert ex.entry_qty_sz > 0


async def test_cancel_order_with_no_resting_is_benign():
    """无挂单时撤单为良性 no-op：回 status 事件、不报错、不改 projected、不触碰持仓。"""
    ex, calls = _make_executor()
    events = await ex.cancel_resting("agent", _TS)
    assert len(events) == 1 and events[0]["type"] == "status"
    assert calls["cancels"] == [] and calls["closes"] == []
    assert ex.projected == "flat"


async def test_cancel_order_only_cancels_the_resting_order():
    """有挂单时撤单只撤那一笔：撤单桩被调用一次、平仓桩零调用、resting 收敛为空。"""
    ex, calls = _make_executor()
    await ex.apply([_Sig("long")], ref_px=_REF_PX, bar_index=0, ts=_TS,
                   order_plan=type("P", (), {"ord_type": "limit", "px": 29700.0})())
    assert ex.resting is not None
    await ex.cancel_resting("agent", _TS + 1)
    assert len(calls["cancels"]) == 1
    assert calls["closes"] == []
    assert ex.resting is None


# ---------- F-2 复现（ai-trade-limit-orders-fix §2.3，先红后绿）：限价校验只对下单动作 ----------

def _limit_decider() -> AiTradeDecider:
    """`allow_limit=True` 的决策器（`_map_decision` 不触 LLM，llm 传占位对象即可）。"""
    return AiTradeDecider(object(), "sys", context_refresh_rounds=10,
                          settings=Settings(ai_trade_decision_max_retries=0),
                          allow_limit=True)


def test_cancel_order_ignores_spurious_order_type():
    """撤单携 LLM 残留 `orderType:"limit"`（无 limitPrice）不得被降级——撤单意图绝不能被限价校验吞掉。"""
    decision, degrade = _limit_decider()._map_decision(
        {"action": "cancel_order", "orderType": "limit", "reason": "撤"}, last_px=_REF_PX)
    assert decision.action == "cancel_order"
    assert degrade is None
    assert decision.ord_type == "market" and decision.limit_px is None


def test_hold_ignores_spurious_order_type():
    """hold 携同类残留字段同样直通（不产生无意义的 degrade 与重试白烧）。"""
    decision, degrade = _limit_decider()._map_decision(
        {"action": "hold", "orderType": "limit", "reason": "观望"}, last_px=_REF_PX)
    assert decision.action == "hold"
    assert degrade is None


def test_ordering_actions_keep_limit_validation_semantics():
    """零回归钉：下单动作的限价校验语义逐字不变（缺价仍降级 `missing_limit_price`）。"""
    decision, degrade = _limit_decider()._map_decision(
        {"action": "open_long", "orderType": "limit", "sizeFraction": 0.5, "reason": "r"},
        last_px=_REF_PX)
    assert degrade == "missing_limit_price"
    assert decision.action == "hold"
