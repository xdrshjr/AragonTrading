"""执行侧离场静默路径的回归用例（ai-trade-exit-priority-and-commitment F-4 / 【R:P1-1】· #32）。

F-4：`_prepare_resting_slot` 在撤单回 `"unknown"` 时返回「不下新单」，`_do_close` 收到之后
`return pre`——**这一轮什么也没平，且 `degrade` 为 `None`**：轨迹上会显示「决策工具 · 平仓」而没有
任何成交、没有任何降级码。它不只影响 Agent 的主动离场，利润保护层的兜底平仓走的是同一条路径。

放行的安全前提已经在仓库里：市价平仓走 `close_position(..., auto_cxl=True)`，由**交易所侧**撤掉该
合约全部未成交挂单；重复平仓在 `LIVE_NO_POSITION` 分支已有良性处理。**开仓路径不适用这条论证**
（双开风险真实存在），故只放开 close 的市价分支——G-6 冻结开仓侧三态逐字不变。

OKX 调用全程经注入桩，不触网；铁律：占位假凭证，绝不写真实凭证。
"""
from __future__ import annotations

from dataclasses import asdict

from app.ai_auto.order_plan import OrderPlan
from app.config import Settings
from app.engine.simulator import RiskParams
from app.live_auto.executor import LiveExecutor
from app.okx.accounts import AccountCredentials
from app.okx.private_client import OKXPrivateError

_REF_PX = 30000.0
_LIMIT_PX = 29700.0
_TS = 1000
_UNKNOWN = {"outcome": "unknown", "code": "ORDER_NOT_FOUND"}


class _Sig:
    def __init__(self, side: str, strength: float = 1.0, reason: str = ""):
        self.side = side
        self.strength = strength
        self.reason = reason


def _limit_plan(px: float = _LIMIT_PX) -> OrderPlan:
    return OrderPlan(ord_type="limit", px=px)


def _make_executor(**over):
    calls = {"orders": [], "closes": [], "cancels": [], "gets": []}
    cancel_seq = over.pop("cancel_seq", None)
    order_state_seq = over.pop("order_state_seq", None)
    raise_on_close = over.pop("raise_on_close", None)

    def _next(seq, idx):
        return seq[min(idx, len(seq) - 1)] if seq else None

    async def place_order_fn(order):
        calls["orders"].append(order)
        return {"ok": True, "ordId": "x1", "clOrdId": "aragonx"}

    async def close_position_fn(close):
        calls["closes"].append(close)
        # 判据必须是**委托类型**而不是调用序号：`_long_with_resting_close` 会 `calls["closes"]
        # .clear()` 把计数清回 0，按「第 2 次调用才抛」写会让整条 `LIVE_NO_POSITION` 时序**根本
        # 不被执行**，而用例照样全绿（节点 #3 复核时实测：那一轮走的是 `_settle_close` 成功路径）。
        if raise_on_close and close.ord_type == "market":
            raise raise_on_close
        return {"ok": True, "clOrdId": "aragoncx", "ordId": "c1", "closedSz": 0.1}

    async def positions_fn():
        return []

    async def cancel_order_fn(cancel):
        calls["cancels"].append(cancel)
        return _next(cancel_seq, len(calls["cancels"]) - 1) or {"outcome": "canceled"}

    async def get_order_fn(cl_ord_id):
        calls["gets"].append(cl_ord_id)
        return _next(order_state_seq, len(calls["gets"]) - 1) or {"state": "live"}

    executor = LiveExecutor(
        creds=AccountCredentials("ak-fake", "sk-fake-000000", "pp-fake-000000"),
        account_id=1, inst_id="BTC-USDT-SWAP", td_mode="cross",
        pos_mode="long_short_mode", leverage=3.0, budget_usd=1000.0,
        risk=RiskParams(), ct_val=0.01, lot_sz=0.01, min_sz=0.01,
        settings=Settings(), place_order_fn=place_order_fn,
        close_position_fn=close_position_fn, positions_fn=positions_fn,
        cancel_order_fn=cancel_order_fn, get_order_fn=get_order_fn)
    return executor, calls


async def _long_with_resting_close(ex, calls):
    """建一笔多头，再挂一张限价平仓单 ⇒ 挂单槽位被占住。"""
    await ex.apply([_Sig("long")], ref_px=_REF_PX, bar_index=0, ts=_TS)
    await ex.apply([_Sig("close")], ref_px=_REF_PX, bar_index=1, ts=_TS + 1,
                   order_plan=_limit_plan())
    assert ex.resting is not None and ex.resting.intent == "close"
    assert ex.projected == "long"           # I-2b：平仓挂单期间 projected 保持原方向
    calls["closes"].clear()


# ---------------- F-4 的正修 ----------------
async def test_market_close_is_still_sent_when_the_cancel_is_unconfirmed():
    """**F-4 的回归用例**：撤单回 `unknown` + 市价平仓 ⇒ 仍然下发（此前整轮静默丢弃）。"""
    ex, calls = _make_executor(cancel_seq=[_UNKNOWN])
    await _long_with_resting_close(ex, calls)
    events = await ex.apply([_Sig("close")], ref_px=_REF_PX, bar_index=2, ts=_TS + 2)
    assert len(calls["closes"]) == 1                     # ← 修法前这里是 0
    assert calls["closes"][0].ord_type == "market"
    assert ex.projected == "flat"
    assert any(e["type"] == "trade" and e["data"]["side"] == "close" for e in events)
    # 交易所侧的 autoCxl 已经撤掉了那张挂单，本地留着只会让下一轮去查一张不存在的单
    assert ex.resting is None
    assert ex.last_close_block is None                   # 真的平成了 ⇒ 无降级码


async def test_the_status_event_says_why_it_went_through():
    """如实说明（A-4）：放行分支必须留一条可读的 status，不许静默改变行为。"""
    ex, calls = _make_executor(cancel_seq=[_UNKNOWN])
    await _long_with_resting_close(ex, calls)
    events = await ex.apply([_Sig("close")], ref_px=_REF_PX, bar_index=2, ts=_TS + 2)
    texts = [e["data"].get("message", "") for e in events if e["type"] == "status"]
    assert any("仍按市价平仓" in t for t in texts)


async def test_a_filled_cancel_still_stops_the_close():
    """`filled` 仍必须止步：那是**真的已经平掉了**，再打一单会开出一笔反向新仓。"""
    ex, calls = _make_executor(
        cancel_seq=[{"outcome": "filled", "code": "ORDER_ALREADY_FILLED"}],
        order_state_seq=[{"state": "filled", "avgPx": 29690.0, "accFillSz": 0.1}])
    await _long_with_resting_close(ex, calls)
    await ex.apply([_Sig("close")], ref_px=_REF_PX, bar_index=2, ts=_TS + 2)
    assert calls["closes"] == []


async def test_a_limit_close_still_stops_the_close():
    """【R:P0-3】限价平仓**逐字不变**：本轮不下新单，那张挂单继续排队到 TTL。"""
    ex, calls = _make_executor(cancel_seq=[_UNKNOWN])
    await _long_with_resting_close(ex, calls)
    await ex.apply([_Sig("close")], ref_px=_REF_PX, bar_index=2, ts=_TS + 2,
                   order_plan=_limit_plan(29650.0))
    assert calls["closes"] == []
    assert ex.resting is not None
    assert ex.last_close_block == "exit_order_slot_busy(unknown)"


async def test_the_silent_path_now_carries_a_readable_code():
    """F-4 的另一半：本轮真的没平成时**必须有降级码**（此前连码都没有）。"""
    ex, calls = _make_executor(cancel_seq=[_UNKNOWN])
    await _long_with_resting_close(ex, calls)
    await ex.apply([_Sig("close")], ref_px=_REF_PX, bar_index=2, ts=_TS + 2,
                   order_plan=_limit_plan(29650.0))
    assert ex.last_close_block is not None
    # 每次 apply 先清零，绝不跨轮残留
    await ex.apply([], ref_px=_REF_PX, bar_index=3, ts=_TS + 3)
    assert ex.last_close_block is None


# ---------------- G-6：开仓路径三态逐字不变 ----------------
async def test_g6_open_path_is_verbatim_unchanged_in_all_three_cancel_outcomes():
    """开仓侧**一行不改**：双开风险真实存在，`autoCxl` 的论证只对平仓成立。"""
    for outcome in ({"outcome": "unknown", "code": "X"},
                    {"outcome": "filled", "code": "ORDER_ALREADY_FILLED"}):
        ex, calls = _make_executor(
            cancel_seq=[outcome],
            order_state_seq=[{"state": "filled", "avgPx": 29690.0, "accFillSz": 0.1}])
        await ex.apply([_Sig("long")], ref_px=_REF_PX, bar_index=0, ts=_TS,
                       order_plan=_limit_plan())
        await ex.apply([_Sig("long")], ref_px=_REF_PX, bar_index=1, ts=_TS + 1,
                       order_plan=_limit_plan(29800.0))
        assert len(calls["orders"]) == 1, outcome        # 绝不双挂
    # `canceled` ⇒ 照常撤旧挂新
    ex, calls = _make_executor(cancel_seq=[{"outcome": "canceled"}])
    await ex.apply([_Sig("long")], ref_px=_REF_PX, bar_index=0, ts=_TS,
                   order_plan=_limit_plan())
    await ex.apply([_Sig("long")], ref_px=_REF_PX, bar_index=1, ts=_TS + 1,
                   order_plan=_limit_plan(29800.0))
    assert len(calls["orders"]) == 2 and calls["orders"][1].px == 29800.0


async def test_market_open_payload_stays_byte_identical():
    """G-6 的另一半：市价开仓 payload 与本特性合入前逐字相同（三元组解包不改一行逻辑）。"""
    ex, calls = _make_executor()
    await ex.apply([_Sig("long")], ref_px=_REF_PX, bar_index=0, ts=_TS)
    assert asdict(calls["orders"][0]) == {
        "inst_id": "BTC-USDT-SWAP", "direction": "long", "ord_type": "market",
        "td_mode": "cross", "px": None, "sz": 10.0, "lever": 3.0,
        "tp_trigger_px": None, "sl_trigger_px": None}


# ---------------- 【R:P1-1】放行分支的两个后果 ----------------
async def test_our_own_limit_close_is_never_recorded_as_an_exchange_oco():
    """放行后若那张限价单其实已经成交，OKX 回 `LIVE_NO_POSITION`——**绝不能**记成交易所 OCO。

    `_settle_benign_close` 此前**无条件**按 `source="oco"` 合成 `external_close`，而 `reconcile`
    里的同类分支早有 `_external_close_suppressed()` 保护、`_do_close` 这一条没有。

    收敛方式是**延后一轮**而不是就地猜：此刻本地分不清「我方限价单成交了」与「交易所 OCO 先平
    了」，就地结算无论选哪个 `source` 都有一半概率是假的，且会按 `ref_px`（而非真实 `avgPx`）
    捏出一个盈亏。故本轮只推一条如实的 status，把结算留给下一轮 `_poll_resting`（见下一条用例）。
    """
    ex, calls = _make_executor(
        cancel_seq=[_UNKNOWN],
        raise_on_close=OKXPrivateError("LIVE_NO_POSITION", "no position"),
        order_state_seq=[{"state": "filled", "avgPx": 29690.0, "accFillSz": 10.0}])
    await _long_with_resting_close(ex, calls)
    events = await ex.apply([_Sig("close")], ref_px=_REF_PX, bar_index=2, ts=_TS + 2)
    assert [c.ord_type for c in calls["closes"]] == ["market"]     # 分支真的被走到了
    kinds = [(e["data"].get("explain") or {}).get("kind")
             for e in events if e["type"] == "trade"]
    assert "external_close" not in kinds          # ← 修法前这里会是 external_close(source=oco)
    assert [t for t in ex.trades if t["side"] == "close"] == []   # 本轮不落成交（等下轮核对）
    assert ex.resting is not None                 # 挂单状态未核对前不丢弃它


async def test_the_deferred_settlement_lands_on_the_next_poll_as_our_own_close():
    """延后的那一轮：`_poll_resting` 读到 `filled` ⇒ **按真实成交价**结算一笔归 `agent` 的平仓。

    这是「不就地猜」的兑现点——盈亏取 `avgPx`、张数取真实持仓，`explain.kind` 是 `atomic`
    （退出归因据它归 `agent` / `agentOverride`），而不是一条 `qty=0` 的幽灵成交。
    """
    ex, calls = _make_executor(
        cancel_seq=[_UNKNOWN],
        raise_on_close=OKXPrivateError("LIVE_NO_POSITION", "no position"),
        order_state_seq=[{"state": "filled", "avgPx": 29690.0, "accFillSz": 10.0}])
    await _long_with_resting_close(ex, calls)
    qty_before = ex.entry_qty_sz
    await ex.apply([_Sig("close")], ref_px=_REF_PX, bar_index=2, ts=_TS + 2)
    await ex.reconcile(_REF_PX, _TS + 3)
    closes = [t for t in ex.trades if t["side"] == "close"]
    assert len(closes) == 1 and closes[0]["qty"] == qty_before   # 不是 qty=0 的幽灵
    assert closes[0]["price"] == 29690.0                         # 真实成交价，不是 ref_px
    assert (closes[0].get("explain") or {}).get("kind") == "atomic"
    assert ex.projected == "flat" and ex.resting is None


async def test_no_ghost_settlement_on_the_following_poll():
    """时序的下半段：放行分支收敛之后，下一轮 `_poll_resting` 不得再结算第二笔。"""
    ex, calls = _make_executor(cancel_seq=[_UNKNOWN],
                               order_state_seq=[{"state": "filled", "avgPx": 29690.0,
                                                 "accFillSz": 0.1}])
    await _long_with_resting_close(ex, calls)
    await ex.apply([_Sig("close")], ref_px=_REF_PX, bar_index=2, ts=_TS + 2)
    before = len(ex.trades)
    await ex.reconcile(_REF_PX, _TS + 3)
    assert len(ex.trades) == before               # 无幽灵成交
    assert calls["gets"] == []                    # resting 已清 ⇒ 根本不会去查
