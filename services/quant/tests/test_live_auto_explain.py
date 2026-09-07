"""实盘自动交易可解释性测试（auto-trade-explainability §6.1）。

覆盖：执行器各生命周期分支挂对应 kind 的 explain（factor_score / risk_exit / reverse /
session_stop / external_close）；external_close 为只读合成成交且不触发下单；append_live_trade
落库（自带 commit）后另开 session 经 trade_out 读回无损；explain 不含凭证；未知接收回执
不合成成交。OKX 调用全程桩驱动，不触网；铁律：占位假凭证，绝不写真实凭证。
"""
from __future__ import annotations

import json

import pytest

from app.config import Settings
from app.db import repositories as repo
from app.db.models import Trade
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.engine.simulator import RiskParams
from app.live_auto.executor import LiveExecutor
from app.live_auto.session import LiveAutoSession
from app.okx.accounts import AccountCredentials
from app.okx.private_client import OKXPrivateError
from app.okx.types import Candle
from app.schemas import trade_out
from app.strategies.base import BaseStrategy, Signal, StrategyMeta

_REF_PX = 30000.0
_START_TS = 1_700_000_000
_STEP = 3600


def _factor_explain(side: str) -> dict:
    """模拟 FactorScoreStrategy 产出的 factor_score 解释（含一条因子归因断面）。"""
    return {
        "schema": 1, "kind": "factor_score", "side": side,
        "headline": "复合得分多头" if side == "long" else "复合得分空头",
        "metrics": [{"label": "复合得分", "value": "0.42"}],
        "composite": {"score": 0.42, "preSquash": 0.45, "squash": True,
                      "enterLong": 0.3, "enterShort": 0.3, "exitBand": 0.1},
        "factors": [{"factorKey": "mom_roc", "name": "mom_roc", "direction": "long",
                     "value": 1.2, "zscore": 1.4, "weight": 0.6, "contribution": 0.84,
                     "share": 1.0, "isTopDriver": True}],
    }


def _make_executor(**over):
    """构造带桩 OKX 回调的 executor；返回 (executor, calls 记录)。"""
    calls = {"orders": [], "closes": [], "reads": 0}
    raise_on_close = over.pop("raise_on_close", None)

    async def place_order_fn(order):
        calls["orders"].append(order)
        return {"ok": True, "ordId": "x1", "clOrdId": "aragonx"}

    async def close_position_fn(close):
        calls["closes"].append(close)
        if raise_on_close:
            raise raise_on_close
        return {"ok": True, "clOrdId": "aragoncx"}

    async def positions_fn():
        calls["reads"] += 1
        return []

    executor = LiveExecutor(
        creds=AccountCredentials("ak-fake", "sk-fake-000000", "pp-fake-000000"),
        account_id=1, inst_id="BTC-USDT-SWAP", td_mode="cross", pos_mode="long_short_mode",
        leverage=3.0, budget_usd=1000.0, risk=over.pop("risk", RiskParams()), ct_val=0.01,
        lot_sz=0.01, min_sz=0.01, settings=Settings(), place_order_fn=place_order_fn,
        close_position_fn=close_position_fn, positions_fn=positions_fn)
    return executor, calls


def _trade_events(events):
    return [e for e in events if e["type"] == "trade"]


# ------------------------------ 信号驱动开 / 平 ------------------------------
async def test_open_long_carries_factor_score_explain_with_risk():
    ex, _ = _make_executor(risk=RiskParams(stop_loss_pct=0.02, take_profit_pct=0.04))
    sig = Signal(side="long", strength=0.8, reason="复合得分多头", explain=_factor_explain("long"))
    events = await ex.apply([sig], ref_px=_REF_PX, bar_index=0, ts=1000)
    trade = _trade_events(events)[0]["data"]
    assert trade["side"] == "long"
    assert trade["explain"]["kind"] == "factor_score"
    assert trade["explain"]["factors"]            # 因子归因非空
    assert trade["explain"]["side"] == "long"
    assert trade["explain"]["risk"]["stopLossPrice"] is not None   # 开仓挂的 OCO 触发价


async def test_signal_close_reuses_factor_explain_as_close():
    ex, _ = _make_executor()
    await ex.apply([Signal(side="long")], ref_px=_REF_PX, bar_index=0, ts=1000)
    close_sig = Signal(side="close", reason="得分落入退出带", explain=_factor_explain("close"))
    events = await ex.apply([close_sig], ref_px=_REF_PX, bar_index=1, ts=2000)
    trade = _trade_events(events)[0]["data"]
    assert trade["explain"]["kind"] == "factor_score"
    assert trade["explain"]["side"] == "close"      # 平仓断面（让用户看到平仓那一刻各因子状态）


async def test_max_hold_close_has_risk_exit_explain():
    ex, _ = _make_executor(risk=RiskParams(max_hold_bars=2))
    await ex.apply([Signal(side="long")], ref_px=_REF_PX, bar_index=0, ts=1000)
    await ex.apply([], ref_px=_REF_PX, bar_index=1, ts=2000)   # 未到 max_hold
    events = await ex.apply([], ref_px=_REF_PX, bar_index=2, ts=3000)
    trade = _trade_events(events)[0]["data"]
    assert trade["explain"]["kind"] == "risk_exit"
    assert trade["reason"] == "max_hold"
    assert any(m["label"] == "上限根数" for m in trade["explain"]["metrics"])


async def test_reverse_close_has_reverse_explain_and_open_keeps_signal_explain():
    ex, _ = _make_executor()
    await ex.apply([Signal(side="long")], ref_px=_REF_PX, bar_index=0, ts=1000)
    short_sig = Signal(side="short", reason="复合得分空头", explain=_factor_explain("short"))
    events = await ex.apply([short_sig], ref_px=_REF_PX, bar_index=1, ts=2000)
    trades = _trade_events(events)
    assert len(trades) == 2
    assert trades[0]["data"]["explain"]["kind"] == "reverse"        # 先平
    assert trades[0]["data"]["explain"]["side"] == "close"
    assert trades[1]["data"]["explain"]["kind"] == "factor_score"   # 后开
    assert trades[1]["data"]["side"] == "short"


async def test_session_stop_close_via_flatten_has_session_stop_explain():
    ex, _ = _make_executor()
    opened = _trade_events(
        await ex.apply([Signal(side="long", strength=0.4)],
                       ref_px=_REF_PX, bar_index=0, ts=1000)
    )[0]["data"]
    result = await ex.flatten(_REF_PX, ts=2000)
    trade = result["trade"]
    assert trade is not None
    assert trade["explain"]["kind"] == "session_stop"
    assert trade["side"] == "close"
    assert trade["reason"] == "session_stop"
    assert trade["positionPct"] == pytest.approx(opened["positionPct"])
    assert ex.entry_position_pct is None


# ------------------------------ external_close（v2 合成成交） ------------------------------
async def test_external_close_synthesized_on_reconcile_without_any_orders():
    """_on_reconcile_ok 侦测持仓归零：产生且仅产生一条 external_close 成交，且不调用 place/close 桩。"""
    ex, calls = _make_executor()
    opened = _trade_events(
        await ex.apply([Signal(side="long", strength=0.4)],
                       ref_px=_REF_PX, bar_index=0, ts=1000)
    )[0]["data"]
    orders_before, closes_before = len(calls["orders"]), len(calls["closes"])

    async def flat_positions():
        return []                       # 交易所侧 / 外部已平仓

    ex._read_positions = flat_positions
    events = await ex.reconcile(_REF_PX, ts=2000)
    ext = [e for e in _trade_events(events) if e["data"]["explain"]["kind"] == "external_close"]
    assert len(ext) == 1
    assert ext[0]["data"]["side"] == "close"
    assert ext[0]["data"]["positionPct"] == pytest.approx(opened["positionPct"])
    assert ex.projected == "flat"
    assert ex.entry_position_pct is None
    assert len(calls["orders"]) == orders_before    # 仅读持仓合成只读成交，绝不下单
    assert len(calls["closes"]) == closes_before


async def test_external_close_synthesized_on_benign_oco_close():
    """_settle_benign_close（LIVE_NO_POSITION）：合成 external_close，且合成本身不新增下单。"""
    ex, calls = _make_executor(raise_on_close=OKXPrivateError("LIVE_NO_POSITION", "无可平持仓"))
    opened = _trade_events(
        await ex.apply([Signal(side="long", strength=0.4)],
                       ref_px=_REF_PX, bar_index=0, ts=1000)
    )[0]["data"]
    orders_before = len(calls["orders"])
    events = await ex.apply([Signal(side="close")], ref_px=_REF_PX, bar_index=1, ts=2000)
    ext = [e for e in _trade_events(events) if e["data"]["explain"]["kind"] == "external_close"]
    assert len(ext) == 1
    assert ext[0]["data"]["side"] == "close"
    assert ext[0]["data"]["positionPct"] == pytest.approx(opened["positionPct"])
    assert ex.projected == "flat"
    assert ex.entry_position_pct is None
    assert len(calls["orders"]) == orders_before    # 合成只记录已发生平仓，绝不新增下单
    assert not [e for e in events if e["type"] == "error"]


# ------------------------------ 订单身份与接收事实 ------------------------------
async def test_open_captures_order_ids_when_present():
    ex, _ = _make_executor()
    events = await ex.apply([Signal(side="long")], ref_px=_REF_PX, bar_index=0, ts=1000)
    trade = _trade_events(events)[0]["data"]
    assert trade["clOrdId"] == "aragonx" and trade["ordId"] == "x1"


@pytest.mark.parametrize("managed", [False, True], ids=["普通执行器", "AI执行器"])
async def test_open_without_order_ids_is_unknown_and_does_not_invent_a_trade(managed):
    ex, calls = _make_executor()
    snapshots = []
    if managed:
        from app.ai_auto.entry_quota import EntryQuota
        from app.ai_auto.open_attempts import OpenAttemptManager

        async def persist(snapshot):
            snapshots.append(snapshot)
            return True

        ex.open_attempts = OpenAttemptManager(
            account_id=1, run_id="run", inst_id=ex.inst_id,
            quota=EntryQuota("run", 2), persist_callback=persist)
        ex.open_attempts.set_context(round=1, runtime_epoch_id="epoch", lane="contrarian")

    async def place_no_ids(order):
        calls["orders"].append(order)
        return {"ok": True}             # 回执不含 clOrdId / ordId

    ex._place_order = place_no_ids
    events = await ex.apply([Signal(side="long", reason="复合得分多头")],
                            ref_px=_REF_PX, bar_index=0, ts=1000)
    assert _trade_events(events) == []
    assert ex.trades == [] and ex.projected == "flat"
    assert ex.last_open_receipt.status == "unknown"
    assert ex.last_open_receipt.reason == "acceptance_unknown"
    assert ex.last_open_receipt.submission_phase == "sent"
    assert ex.last_open_receipt.ord_id is None
    assert not ex.last_open_receipt.submitted_new
    assert len(calls["orders"]) == 1
    if managed:
        identity = calls["orders"][0].client_order_id
        assert snapshots[0]["pendingOpenAttempt"]["clOrdId"] == identity
        assert snapshots[-1]["pendingOpenAttempt"]["phase"] == "sent"
        assert ex.open_attempts.pending.cl_ord_id == identity
        assert ex.open_attempts.quota.reserved == 1
        assert ex.open_attempts.quota.used == 0
        await ex.apply([Signal(side="long")], ref_px=_REF_PX, bar_index=1, ts=1001)
        assert len(calls["orders"]) == 1
        assert ex.open_attempts.pending.cl_ord_id == identity
    else:
        assert calls["orders"][0].client_order_id is None
        assert calls["orders"][0].submission_trace is None


# ------------------------------ 无凭证 ------------------------------
async def test_explain_and_trade_contain_no_credentials():
    ex, _ = _make_executor()
    sig = Signal(side="long", reason="复合得分多头", explain=_factor_explain("long"))
    events = await ex.apply([sig], ref_px=_REF_PX, bar_index=0, ts=1000)
    blob = json.dumps(_trade_events(events)[0]["data"], ensure_ascii=False)
    for secret in ("ak-fake", "sk-fake", "pp-fake", "api_key", "secretKey", "passphrase"):
        assert secret not in blob


# ------------------------------ 落库往返（自带 commit）+ trade_out 容错 ------------------------------
async def test_append_live_trade_persists_explain_and_round_trips_through_trade_out():
    await init_db()
    try:
        sm = get_sessionmaker()
        async with sm() as s:
            run = await repo.create_run(s, strategy_id=1, inst_id="BTC-USDT-SWAP", bar="1H",
                                        mode="live-auto", config={"mode": "live-auto"})
            run_id = run.id
        trade = {"ts": 1000, "side": "long", "price": 30000.0, "qty": 0.03, "fee": 0.0,
                 "pnl": 0.0, "positionAfter": 0.03, "reason": "复合得分多头",
                 "positionPct": 0.4, "explain": _factor_explain("long")}
        async with sm() as s:
            await repo.append_live_trade(s, run_id, trade)
        # 另开 session 读回 —— 若仅 flush 未 commit，这里将读不到（钉死 R12）。
        async with sm() as s:
            rows = await repo.list_trades(s, run_id)
            assert len(rows) == 1
            out = trade_out(rows[0])
            assert out["explain"]["kind"] == "factor_score"
            assert out["explain"]["factors"][0]["factorKey"] == "mom_roc"
            assert rows[0].position_pct == pytest.approx(0.4)
            assert out["positionPct"] == pytest.approx(0.4)
    finally:
        await dispose_engine()


async def test_append_live_trade_ignores_pnl_pct_key_without_regression():
    # auto-trade-pnl-records R1：trade dict 新增派生键 pnlPct，append_live_trade 显式字段映射应静默
    # 忽略（不入库、无 TypeError），落库不回归。
    await init_db()
    try:
        sm = get_sessionmaker()
        async with sm() as s:
            run = await repo.create_run(s, strategy_id=1, inst_id="BTC-USDT-SWAP", bar="1H",
                                        mode="live-auto", config={"mode": "live-auto"})
            run_id = run.id
        trade = {"ts": 2000, "side": "close", "price": 31500.0, "qty": 0.03, "fee": 0.0,
                 "pnl": 19.05, "pnlPct": 0.0148, "positionAfter": 0.0, "reason": "signal_close",
                 "explain": None}
        async with sm() as s:
            await repo.append_live_trade(s, run_id, trade)   # 含 pnlPct，不得抛
        async with sm() as s:
            rows = await repo.list_trades(s, run_id)
            assert len(rows) == 1 and rows[0].pnl == pytest.approx(19.05)
    finally:
        await dispose_engine()


async def test_append_live_trade_keeps_join_ids_when_explain_is_missing():
    await init_db()
    try:
        sm = get_sessionmaker()
        async with sm() as s:
            run = await repo.create_run(
                s,
                strategy_id=1,
                inst_id="BTC-USDT-SWAP",
                bar="1H",
                mode="live-auto",
                config={"mode": "live-auto"},
            )
            run_id = run.id
        trade = {
            "ts": 3000,
            "side": "long",
            "price": 30000.0,
            "qty": 0.05,
            "fee": 0.0,
            "pnl": 0.0,
            "positionAfter": 0.05,
            "positionPct": 0.4,
            "reason": "signal_open",
            "clOrdId": "pct-only",
            "ordId": "ord-pct",
            "explain": None,
        }
        async with sm() as s:
            await repo.append_live_trade(s, run_id, trade)
        async with sm() as s:
            rows = await repo.list_explained_trades(s)
            row = next(item for item in rows if item.run_id == run_id)
            stored = json.loads(row.explain_json)
            assert stored["meta"]["clOrdId"] == "pct-only"
            assert trade_out(row)["explain"] is None
            assert trade_out(row)["positionPct"] == pytest.approx(0.4)
    finally:
        await dispose_engine()


def test_trade_out_explain_is_none_for_missing_or_corrupt_json():
    base = dict(run_id=1, ts=1, side="long", price=1.0, qty=1.0, fee=0.0, pnl=0.0,
                position_after=1.0, reason="x")
    assert trade_out(Trade(**base, explain_json=None))["explain"] is None
    assert trade_out(Trade(**base, explain_json="{not valid json"))["explain"] is None
    assert trade_out(Trade(**base, explain_json="[1,2,3]"))["explain"] is None   # 非 dict 顶层


# ------------------------------ session：落库失败不阻塞交易（异常隔离） ------------------------------
class _StubStrategy(BaseStrategy):
    meta = StrategyMeta(key="_stub_explain", name="Stub", description="t",
                        default_params={}, params_schema={})

    def __init__(self, signals_by_bar=None):
        super().__init__({})
        self._signals_by_bar = signals_by_bar or {}

    def warmup(self) -> int:
        return 1

    def on_candle(self, df, i, ctx):
        return self._signals_by_bar.get(i, [])


def _candle(idx: int, price: float = _REF_PX) -> Candle:
    return Candle(ts=_START_TS + idx * _STEP, o=price, h=price + 5, l=price - 5,
                  c=price, vol=100.0, confirm=True)


def _make_session(signals_by_bar):
    calls = {"orders": [], "closes": []}

    async def place_order_fn(order):
        calls["orders"].append(order)
        return {"ok": True, "ordId": "x", "clOrdId": "aragonx"}

    async def close_position_fn(close):
        calls["closes"].append(close)
        return {"ok": True, "clOrdId": "aragoncx"}

    async def positions_fn():
        executor = session.executor
        if executor.projected == "flat":
            return []
        return [{"instId": "BTC-USDT-SWAP", "pos": executor.entry_qty_sz, "upl": 0.0}]

    executor = LiveExecutor(
        creds=AccountCredentials("ak", "sk-000000", "pp-000000"), account_id=1,
        inst_id="BTC-USDT-SWAP", td_mode="cross", pos_mode="long_short_mode", leverage=3.0,
        budget_usd=1000.0, risk=RiskParams(), ct_val=0.01, lot_sz=0.01, min_sz=0.01,
        settings=Settings(), place_order_fn=place_order_fn,
        close_position_fn=close_position_fn, positions_fn=positions_fn)
    session = LiveAutoSession("liveauto_explain", 1, _StubStrategy(signals_by_bar),
                              "BTC-USDT-SWAP", "1H", executor, priming=False,
                              strategy_name="因子组合得分", strategy_kind="factor")
    return session, calls


def _drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


async def test_persist_failure_does_not_block_trade_dispatch(monkeypatch):
    await init_db()
    try:
        sig = Signal(side="long", reason="复合得分多头", explain=_factor_explain("long"))
        session, calls = _make_session({1: [sig]})
        q = session.subscribe()

        async def boom(*_a, **_k):
            raise RuntimeError("db down")

        monkeypatch.setattr("app.live_auto.session.repo.append_live_trade", boom)
        await session.feed_candle(_candle(0))
        await session.feed_candle(_candle(1))
        # 落库失败仅 warning：真实开仓与 WS 扇出均不受影响。
        assert session.executor.projected == "long"
        trade_events = [e for e in _drain(q) if e.get("type") == "trade"]
        assert trade_events
        # explain.meta 已被会话注入（可追溯）。
        assert trade_events[0]["data"]["explain"]["meta"]["strategyName"] == "因子组合得分"
        assert trade_events[0]["data"]["explain"]["meta"]["instId"] == "BTC-USDT-SWAP"
    finally:
        await dispose_engine()


def test_profit_guard_explain_shape_and_values():
    """利润保护平仓解释（ai-trade-profit-protection §2.7）：新 kind、六项 metrics、绝不含凭证。"""
    from app.live_auto.explain import build_profit_guard_explain

    explain = build_profit_guard_explain(
        pos_side="long", trigger_label="移动止盈", entry_price=0.07245,
        exit_price=0.0729, peak_pnl=0.302, giveback_ratio=0.4, pnl=0.181)

    assert explain["kind"] == "profit_guard" and explain["side"] == "close"
    assert "移动止盈" in explain["headline"]
    labels = [m["label"] for m in explain["metrics"]]
    assert labels == ["触发规则", "原持仓方向", "峰值浮盈", "自峰值回吐", "入场价",
                      "平仓价", "触发时浮盈"]
    by_label = {m["label"]: m["value"] for m in explain["metrics"]}
    assert by_label["自峰值回吐"] == "40.0%"
    assert by_label["峰值浮盈"] == "+0.30 USDT"
    # 本地派生口径必须标注（不含手续费 / 资金费，比交易所 upl 略乐观）——绝不冒充已实现盈亏。
    trigger_pnl = next(m for m in explain["metrics"] if m["label"] == "触发时浮盈")
    assert "不含手续费" in trigger_pnl["hint"]
    assert "sk-" not in str(explain) and "passphrase" not in str(explain)


def test_profit_guard_explain_degrades_without_numbers():
    """峰值 / 回吐算不出时如实降级为「—」，绝不补零假装有数（诚实约束）。"""
    from app.live_auto.explain import build_profit_guard_explain

    explain = build_profit_guard_explain(
        pos_side="short", trigger_label="保本线", entry_price=1.0, exit_price=1.0,
        peak_pnl=None, giveback_ratio=None, pnl=None)
    by_label = {m["label"]: m["value"] for m in explain["metrics"]}
    assert by_label["峰值浮盈"] == "—" and by_label["自峰值回吐"] == "—"
    assert by_label["原持仓方向"] == "空"

