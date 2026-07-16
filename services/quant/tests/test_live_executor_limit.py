"""LiveExecutor 限价挂单生命周期单测（ai-trade-limit-orders §2.3/§2.5/§2.7/§2.8）。

覆盖：零回归门禁（`order_plan=None` ⇒ 市价 payload 逐字不变）、I-2a/I-2b（挂单期间不乐观置位
projected）、成交采纳（`avgPx` 而非 `ref_px`）、部分成交增量采纳、TTL 撤单、撤旧挂新、flatten
撤挂单，以及三条 P0 正面守门：撤单撞成交不双开（P0-2）、限价平仓成交不记 external_close（P0-3）、
`_poll_resting` 读取失败不污染 reconcile 降级计数。

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


class _Sig:
    def __init__(self, side: str, strength: float = 1.0, reason: str = ""):
        self.side = side
        self.strength = strength
        self.reason = reason


def _limit_plan(px: float = _LIMIT_PX) -> OrderPlan:
    return OrderPlan(ord_type="limit", px=px)


def _make_executor(**over):
    """构造带桩 OKX 回调的 executor（含限价两缝）；返回 (executor, calls 记录)。

    `cancel_seq` / `order_state_seq` 支持逐次不同的回读，用于表达「本轮 live、下轮 filled」这类时序。
    """
    calls = {"orders": [], "closes": [], "reads": 0, "cancels": [], "gets": []}
    positions_seq = over.pop("positions_seq", None)
    cancel_seq = over.pop("cancel_seq", None)
    order_state_seq = over.pop("order_state_seq", None)
    raise_on_get = over.pop("raise_on_get", None)
    raise_on_open = over.pop("raise_on_open", None)

    def _next(seq, idx):
        return seq[min(idx, len(seq) - 1)] if seq else None

    async def place_order_fn(order):
        calls["orders"].append(order)
        if raise_on_open:
            raise raise_on_open
        return {"ok": True, "ordId": "x1", "clOrdId": "aragonx"}

    async def close_position_fn(close):
        calls["closes"].append(close)
        return {"ok": True, "clOrdId": "aragoncx", "ordId": "c1"}

    async def positions_fn():
        calls["reads"] += 1
        if positions_seq:
            return _next(positions_seq, calls["reads"] - 1)
        return []

    async def cancel_order_fn(cancel):
        calls["cancels"].append(cancel)
        return _next(cancel_seq, len(calls["cancels"]) - 1) or {"outcome": "canceled"}

    async def get_order_fn(cl_ord_id):
        calls["gets"].append(cl_ord_id)
        if raise_on_get:
            raise raise_on_get
        return _next(order_state_seq, len(calls["gets"]) - 1) or {"state": "live"}

    executor = LiveExecutor(
        creds=AccountCredentials("ak-fake", "sk-fake-000000", "pp-fake-000000"),
        account_id=1, inst_id="BTC-USDT-SWAP", td_mode="cross",
        pos_mode=over.pop("pos_mode", "long_short_mode"),
        leverage=over.pop("leverage", 3.0), budget_usd=over.pop("budget_usd", 1000.0),
        risk=over.pop("risk", RiskParams()), ct_val=over.pop("ct_val", 0.01),
        lot_sz=over.pop("lot_sz", 0.01), min_sz=over.pop("min_sz", 0.01),
        settings=over.pop("settings", Settings()), place_order_fn=place_order_fn,
        close_position_fn=close_position_fn, positions_fn=positions_fn,
        cancel_order_fn=cancel_order_fn, get_order_fn=get_order_fn)
    return executor, calls


def _trades(events):
    return [e for e in events if e["type"] == "trade"]


async def _open_resting(ex, **over):
    """走一次限价开仓，使 executor 处于「挂单在飞」态。"""
    return await ex.apply([_Sig("long")], ref_px=_REF_PX, bar_index=0, ts=_TS,
                          order_plan=over.pop("plan", _limit_plan()))


# ---------- 零回归门禁（最高优先级，R-9）----------

async def test_market_path_payload_byte_identical_when_no_order_plan():
    """`order_plan=None`（live_auto 的全部调用点）⇒ 下单 payload 与本特性合入前逐字相同。"""
    ex, calls = _make_executor()
    await ex.apply([_Sig("long")], ref_px=_REF_PX, bar_index=0, ts=_TS)
    assert asdict(calls["orders"][0]) == {
        "inst_id": "BTC-USDT-SWAP", "direction": "long", "ord_type": "market",
        "td_mode": "cross", "px": None, "sz": 10.0, "lever": 3.0,
        "tp_trigger_px": None, "sl_trigger_px": None}
    assert ex.resting is None          # 市价路径绝不进挂单态
    assert ex.projected == "long"      # 市价仍走乐观置位（口径一字不改）


async def test_market_path_never_polls_or_cancels():
    """`resting is None` ⇒ reconcile 不查订单、不撤单（live_auto 侧零新增 API 频次，R-11）。"""
    ex, calls = _make_executor()
    await ex.apply([_Sig("long")], ref_px=_REF_PX, bar_index=0, ts=_TS)
    await ex.reconcile(_REF_PX, _TS)
    assert calls["gets"] == [] and calls["cancels"] == []


# ---------- I-2a/I-2b：挂单期间绝不乐观置位 ----------

async def test_resting_open_does_not_set_projected():
    """限价开仓挂出后 `projected` 仍为 flat（I-2a）——幻影成交分支的条件因此根本不成立。"""
    ex, calls = _make_executor()
    await _open_resting(ex)
    assert calls["orders"][0].ord_type == "limit" and calls["orders"][0].px == _LIMIT_PX
    assert ex.projected == "flat"
    assert ex.resting is not None and ex.resting.intent == "open"
    assert ex.resting.expire_ts == _TS + int(Settings().ai_trade_limit_order_ttl_s)


async def test_unfilled_resting_survives_reconcile_without_phantom_fill():
    """回归守门：挂单未成交时连跑 3 次 reconcile，绝不产生任何 external_close 假成交。"""
    ex, _ = _make_executor(order_state_seq=[{"state": "live"}])
    await _open_resting(ex)
    before = len(ex.trades)
    for _ in range(3):
        events = await ex.reconcile(_REF_PX, _TS + 1)
        assert _trades(events) == []
    assert len(ex.trades) == before
    assert ex.projected == "flat" and ex.resting is not None


async def test_resting_close_keeps_projected_until_filled():
    """限价平仓挂出后 `projected` 仍为原方向（I-2b）——真仓确实还在，提前归 flat 会漏平。"""
    ex, calls = _make_executor()
    await ex.apply([_Sig("long")], ref_px=_REF_PX, bar_index=0, ts=_TS)   # 市价先建仓
    await ex.apply([_Sig("close")], ref_px=_REF_PX, bar_index=1, ts=_TS,
                   order_plan=_limit_plan(30300.0))
    assert calls["closes"][0].ord_type == "limit" and calls["closes"][0].px == 30300.0
    assert ex.projected == "long"
    assert ex.resting is not None and ex.resting.intent == "close"


# ---------- 成交采纳 ----------

async def test_filled_resting_adopts_avg_px_not_ref_px():
    """成交采纳真实成交价 `avgPx`（非 K 线 `ref_px`）并置位 projected。"""
    ex, _ = _make_executor(order_state_seq=[{"state": "filled", "avgPx": 29650.0,
                                             "accFillSz": 0.1}])
    await _open_resting(ex)
    events = await ex.reconcile(_REF_PX, _TS + 5)
    assert ex.projected == "long"
    assert ex.entry_price == 29650.0        # 不是 _REF_PX，也不是挂单价
    assert ex.entry_qty_sz == 0.1
    assert ex.resting is None
    assert _trades(events)[0]["data"]["price"] == 29650.0


async def test_adopted_open_fill_is_not_phantom_closed_by_lagging_positions():
    """【O-3 守门】刚采纳的开仓成交，绝不被**同一轮**尚未跟上的持仓读数合成幻影 external_close。

    O-1 令 `_poll_resting` 与 `_read_positions` 在同一轮里仅隔一次网络往返，而 OKX 的订单态与持仓
    读数之间存在最终一致性窗口。若不给宽限，一笔刚成交的**真实持仓**会被立刻记成「开仓又被外部
    平掉」并捏造盈亏——R-1 在开仓路径上原样复发。
    """
    ex, _ = _make_executor(
        positions_seq=[[]],          # 持仓读数尚未跟上（本轮仍为空）
        order_state_seq=[{"state": "filled", "avgPx": 29650.0, "accFillSz": 0.1}])
    await _open_resting(ex)
    events = await ex.reconcile(_REF_PX, _TS + 5)
    phantom = [t for t in _trades(events) if t["data"]["reason"] == "external_close"]
    assert phantom == []                   # 绝不捏造幻影平仓
    assert ex.projected == "long"          # 真仓仍被自管跟踪
    assert ex.entry_price == 29650.0


async def test_fill_grace_lasts_exactly_one_round():
    """O-3 的宽限只给**一轮**：持仓读数持续为空 ⇒ 下一轮仍如实判定为外部平仓（绝不永久豁免）。"""
    ex, _ = _make_executor(
        positions_seq=[[]],
        order_state_seq=[{"state": "filled", "avgPx": 29650.0, "accFillSz": 0.1}])
    await _open_resting(ex)
    await ex.reconcile(_REF_PX, _TS + 5)          # 第一轮：宽限
    assert ex.projected == "long"
    events = await ex.reconcile(_REF_PX, _TS + 10)  # 第二轮：确实没仓 → 如实校正
    assert [t for t in _trades(events) if t["data"]["reason"] == "external_close"]
    assert ex.projected == "flat"


async def test_partially_filled_adopts_only_the_increment():
    """部分成交按**增量** `accFillSz - adopted_sz` 采纳，剩余继续挂（R-19 防重复计入）。"""
    ex, _ = _make_executor(
        # 部分成交后交易所侧确有该笔仓位（持仓读数如实反映），故不涉 O-3 宽限。
        positions_seq=[[{"instId": "BTC-USDT-SWAP", "pos": 0.04, "upl": 0.0}]],
        order_state_seq=[
            {"state": "partially_filled", "avgPx": 29700.0, "accFillSz": 0.04},
            {"state": "partially_filled", "avgPx": 29700.0, "accFillSz": 0.04},
            {"state": "partially_filled", "avgPx": 29700.0, "accFillSz": 0.07}])
    await _open_resting(ex)
    first = await ex.reconcile(_REF_PX, _TS + 1)
    assert _trades(first)[0]["data"]["qty"] == 0.04
    assert ex.resting is not None and ex.resting.adopted_sz == 0.04
    same = await ex.reconcile(_REF_PX, _TS + 2)      # 同一累计值：绝不重复采纳
    assert _trades(same) == []
    more = await ex.reconcile(_REF_PX, _TS + 3)
    assert _trades(more)[0]["data"]["qty"] == 0.07 - 0.04
    assert ex.resting is not None and ex.resting.adopted_sz == 0.07


async def test_expired_resting_is_cancelled():
    """TTL 到期由 `_poll_resting` 撤单：Agent 唤醒是离散的，永不成交的挂单须有兜底。"""
    ex, calls = _make_executor(order_state_seq=[{"state": "live"}])
    await _open_resting(ex)
    expired_ts = _TS + int(Settings().ai_trade_limit_order_ttl_s)
    await ex.reconcile(_REF_PX, expired_ts)
    assert len(calls["cancels"]) == 1
    assert ex.resting is None


async def test_externally_canceled_resting_converges_to_none():
    """外部/我方撤销后回读 canceled ⇒ 收敛为无挂单，且不采纳任何成交。"""
    ex, _ = _make_executor(order_state_seq=[{"state": "canceled"}])
    await _open_resting(ex)
    events = await ex.reconcile(_REF_PX, _TS + 1)
    assert ex.resting is None and ex.projected == "flat"
    assert _trades(events) == []


# ---------- 撤旧挂新（§2.5）----------

async def test_new_open_cancels_stale_resting_then_places():
    """撤旧挂新：撤单成功才下新单。"""
    ex, calls = _make_executor(cancel_seq=[{"outcome": "canceled"}])
    await _open_resting(ex)
    await ex.apply([_Sig("long")], ref_px=_REF_PX, bar_index=1, ts=_TS + 1,
                   order_plan=_limit_plan(29800.0))
    assert len(calls["cancels"]) == 1
    assert len(calls["orders"]) == 2 and calls["orders"][1].px == 29800.0


async def test_failed_cancel_does_not_place_new_order():
    """撤单未获确认 ⇒ **不下新单**（绝不双挂），保留旧挂单待下轮核对。"""
    ex, calls = _make_executor(cancel_seq=[{"outcome": "unknown", "code": "ORDER_NOT_FOUND"}])
    await _open_resting(ex)
    await ex.apply([_Sig("long")], ref_px=_REF_PX, bar_index=1, ts=_TS + 1,
                   order_plan=_limit_plan(29800.0))
    assert len(calls["orders"]) == 1        # 仍是最初那一单
    assert ex.resting is not None           # 旧挂单被保留


async def test_same_price_open_does_not_replace_resting():
    """同向同价重复决策 ⇒ 不撤不挂（P2-3 幂等），保住 maker 排队优先级。"""
    ex, calls = _make_executor()
    await _open_resting(ex)
    await ex.apply([_Sig("long")], ref_px=_REF_PX, bar_index=1, ts=_TS + 1,
                   order_plan=_limit_plan(_LIMIT_PX))
    assert calls["cancels"] == [] and len(calls["orders"]) == 1


async def test_cancel_racing_fill_adopts_and_skips_new_order():
    """【P0-2 正面守门】撤单撞成交（51402）⇒ 采纳成交且**当轮绝不下新单**（敞口绝不翻倍）。"""
    ex, calls = _make_executor(
        cancel_seq=[{"outcome": "filled", "code": "ORDER_ALREADY_FILLED"}],
        order_state_seq=[{"state": "filled", "avgPx": 29690.0, "accFillSz": 0.1}])
    await _open_resting(ex)
    assert len(calls["orders"]) == 1
    await ex.apply([_Sig("long")], ref_px=_REF_PX, bar_index=1, ts=_TS + 1,
                   order_plan=_limit_plan(29800.0))
    assert ex.projected == "long"           # (a) 成交被采纳
    assert ex.entry_price == 29690.0        #     且用的是权威 avgPx
    assert len(calls["orders"]) == 1        # (b) place_order 零新增调用
    assert ex.resting is None


async def test_cancel_on_benign_canceled_adopts_no_fill():
    """三态 `canceled`（51401 良性幂等）⇒ resting 清空、不采纳任何成交、projected 不动。"""
    ex, _ = _make_executor(cancel_seq=[{"outcome": "canceled"}])
    await _open_resting(ex)
    await ex.cancel_resting("agent", _TS + 1)
    assert ex.resting is None and ex.projected == "flat"
    assert ex.trades == []


# ---------- P0-3：平仓挂单成交绝不记成 external_close ----------

async def test_filled_close_is_not_recorded_as_external_close():
    """【P0-3 正面守门】限价平仓成交 + OKX 持仓归零 ⇒ 记 signal_close @ avgPx，而非 external_close。

    O-1（`_poll_resting` 先于 `_on_reconcile_ok`）真的生效时，成交先被 `_adopt_close_fill` 采纳、
    projected 已归 flat，幻影分支便不再命中；盈亏据真实成交价而非 mark_px 计算。
    """
    ex, _ = _make_executor(
        positions_seq=[[]],          # 平仓成交后交易所持仓归零
        order_state_seq=[{"state": "filled", "avgPx": 30500.0, "accFillSz": 0.1}])
    await ex.apply([_Sig("long")], ref_px=_REF_PX, bar_index=0, ts=_TS)   # 市价建仓 @30000
    await ex.apply([_Sig("close")], ref_px=_REF_PX, bar_index=1, ts=_TS,
                   order_plan=_limit_plan(30500.0))
    events = await ex.reconcile(20000.0, _TS + 5)      # mark_px 刻意远离成交价以暴露口径
    closes = [t["data"] for t in _trades(events) if t["data"]["side"] == "close"]
    assert len(closes) == 1
    assert closes[0]["reason"] != "external_close"
    assert closes[0]["price"] == 30500.0               # 真实成交价，非 mark_px(20000)
    assert closes[0]["pnl"] > 0                        # 30500 平 30000 的多头 = 真盈利
    assert ex.projected == "flat" and ex.resting is None


async def test_reconcile_suppresses_external_close_while_close_resting():
    """【O-2 兜底】平仓挂单在飞且 `_poll_resting` 读取失败 ⇒ 绝不合成任何 external_close。"""
    ex, _ = _make_executor(
        positions_seq=[[]],
        raise_on_get=OKXPrivateError("OKX_UNREACHABLE", "boom"))
    await ex.apply([_Sig("long")], ref_px=_REF_PX, bar_index=0, ts=_TS)
    await ex.apply([_Sig("close")], ref_px=_REF_PX, bar_index=1, ts=_TS,
                   order_plan=_limit_plan(30500.0))
    events = await ex.reconcile(_REF_PX, _TS + 5)
    assert [t for t in _trades(events) if t["data"]["reason"] == "external_close"] == []
    assert ex.projected == "long"          # 真仓仍在，绝不被幻影平掉
    assert ex.resting is not None          # 延后一轮采纳，而非误记


async def test_poll_resting_failure_does_not_degrade_reconcile():
    """`get_order` 抛错须**自隔离**：不得污染 `_reconcile_fail_streak`、不得触发盯市降级。

    否则一次读单失败会被误归因为「持仓读取失败」，触发假降级（`_on_reconcile_failure`）。
    """
    ex, _ = _make_executor(raise_on_get=OKXPrivateError("OKX_UNREACHABLE", "boom"))
    await _open_resting(ex)
    for _ in range(5):                     # 远超 live_auto_reconcile_max_failures
        events = await ex.reconcile(_REF_PX, _TS + 1)
        assert [e for e in events if e["type"] == "error"] == []
    assert ex._reconcile_fail_streak == 0
    assert ex.degraded is False


# ---------- §2.7 / P1-6 / §2.8 ----------

async def test_limit_tp_sl_anchored_on_limit_px_not_ref_px():
    """限价开仓的随单 OCO 触发价基于 `limit_px`（§2.7）——否则止损距离随挂单距离系统性偏移。"""
    risk = RiskParams(stop_loss_pct=0.01, take_profit_pct=0.02)
    ex, calls = _make_executor(risk=risk)
    await _open_resting(ex)
    order = calls["orders"][0]
    assert order.sl_trigger_px == _LIMIT_PX * 0.99      # 基准是挂单价
    assert order.tp_trigger_px == _LIMIT_PX * 1.02


async def test_reverse_forces_market_on_close_leg():
    """【P1-6 守门】反手两腿：close 腿强制市价、open 腿才用限价（order_plan 只绑终态腿）。"""
    ex, calls = _make_executor()
    await ex.apply([_Sig("short")], ref_px=_REF_PX, bar_index=0, ts=_TS)   # 先持空仓
    await ex.apply([_Sig("long")], ref_px=_REF_PX, bar_index=1, ts=_TS + 1,
                   order_plan=_limit_plan())
    assert calls["closes"][0].ord_type == "market"      # close 腿：绝不挂起
    assert calls["orders"][-1].ord_type == "limit"      # open 腿：Agent 的价格意图
    assert calls["orders"][-1].px == _LIMIT_PX


async def test_flatten_cancels_resting_when_flat():
    """【§2.8 孤儿单】无持仓 + 有挂单 ⇒ flatten 早退前仍须撤单，绝不把挂单遗留在交易所。"""
    ex, calls = _make_executor()
    await _open_resting(ex)
    result = await ex.flatten(_REF_PX, _TS + 9)
    assert len(calls["cancels"]) == 1
    assert ex.resting is None
    assert result["ok"] is True and result["closedSz"] == 0


async def test_flatten_adopts_and_closes_last_moment_fill():
    """【§2.8 竞态】停止瞬间挂单成交（51402）⇒ 采纳成交 → **不早退** → 照常市价平掉。

    否则会「报告干净停止，却在交易所留一笔无人看管的真实持仓」。
    """
    ex, calls = _make_executor(
        cancel_seq=[{"outcome": "filled", "code": "ORDER_ALREADY_FILLED"}],
        order_state_seq=[{"state": "filled", "avgPx": 29690.0, "accFillSz": 0.1}])
    await _open_resting(ex)
    result = await ex.flatten(_REF_PX, _TS + 9)
    assert len(calls["closes"]) == 1        # 刚成交的仓位被照常平掉（未早退）
    assert result["ok"] is True
    assert ex.projected == "flat"


async def test_flatten_without_resting_omits_events_key():
    """无挂单时 flatten 结果**不加 events 键**（live_auto 的结果形状逐字不变）。"""
    ex, _ = _make_executor()
    await ex.apply([_Sig("long")], ref_px=_REF_PX, bar_index=0, ts=_TS)
    result = await ex.flatten(_REF_PX, _TS + 9)
    assert "events" not in result
