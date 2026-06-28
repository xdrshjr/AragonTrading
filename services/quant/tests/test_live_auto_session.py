"""LiveAutoSession 单测（live-auto-trading §2.5/§2.6）：priming 不下单、首实时确认根触发开仓、
reconcile 校正 projected、会话级止损触发自动平仓 + 停。

行情 / OKX 全程桩驱动（直接注入 feed_candle），不触网；铁律：占位假凭证。
"""
from __future__ import annotations

import pytest

from app.config import Settings
from app.db.session import dispose_engine, get_sessionmaker, init_db
from app.db import repositories as repo
from app.engine.simulator import RiskParams
from app.live_auto.executor import LiveExecutor
from app.live_auto.session import LiveAutoSession
from app.okx.accounts import AccountCredentials
from app.okx.types import Candle
from app.strategies.base import BaseStrategy, Signal, StrategyMeta

_START_TS = 1_700_000_000
_STEP = 3600


class _StubStrategy(BaseStrategy):
    meta = StrategyMeta(key="_stub_auto", name="Stub", description="t",
                        default_params={}, params_schema={})

    def __init__(self, signals_by_bar=None):
        super().__init__({})
        self._signals_by_bar = signals_by_bar or {}

    def warmup(self) -> int:
        return 1

    def on_candle(self, df, i, ctx):
        return self._signals_by_bar.get(i, [])


def _candle(idx: int, price: float = 30000.0) -> Candle:
    return Candle(ts=_START_TS + idx * _STEP, o=price, h=price + 5, l=price - 5,
                  c=price, vol=100.0, confirm=True)


def _make_session(signals_by_bar=None, *, run_id=1, stop_loss_usd=None,
                  pos_state=None, risk=None):
    """构造带桩 OKX 回调的会话；pos_state 控制 positions_fn 行为。"""
    pos_state = pos_state if pos_state is not None else {"flat": False, "upl": 0.0}
    calls = {"orders": [], "closes": []}

    async def place_order_fn(order):
        calls["orders"].append(order)
        return {"ok": True, "ordId": "x", "clOrdId": "aragonx"}

    async def close_position_fn(close):
        calls["closes"].append(close)
        return {"ok": True, "clOrdId": "aragoncx"}

    async def positions_fn():
        executor = session.executor
        if pos_state.get("flat") or executor.projected == "flat":
            return []
        return [{"instId": "BTC-USDT-SWAP", "pos": executor.entry_qty_sz,
                 "upl": pos_state.get("upl", 0.0)}]

    executor = LiveExecutor(
        creds=AccountCredentials("ak", "sk-000000", "pp-000000"), account_id=1,
        inst_id="BTC-USDT-SWAP", td_mode="cross", pos_mode="long_short_mode",
        leverage=3.0, budget_usd=1000.0, risk=risk or RiskParams(), ct_val=0.01,
        lot_sz=0.01, min_sz=0.01, settings=Settings(),
        place_order_fn=place_order_fn, close_position_fn=close_position_fn,
        positions_fn=positions_fn)
    session = LiveAutoSession("liveauto_test", run_id, _StubStrategy(signals_by_bar),
                              "BTC-USDT-SWAP", "1H", executor, stop_loss_usd=stop_loss_usd,
                              priming=False)
    return session, calls, pos_state


async def test_priming_window_does_not_trade(monkeypatch):
    await init_db()
    history = [_candle(i) for i in range(10)]

    class _FakeRest:
        async def get_candles(self, *_a, **_k):
            return history

    monkeypatch.setattr("app.live_auto.session.get_rest_client", lambda: _FakeRest())
    session, calls, _ = _make_session()
    try:
        await session._prime_window()
        assert session._warmed_bars == 10
        assert calls["orders"] == [] and session.executor.trades == []
    finally:
        await dispose_engine()


async def test_first_live_candle_triggers_open():
    await init_db()
    session, calls, _ = _make_session({1: [Signal(side="long")]})
    try:
        await session.feed_candle(_candle(0))   # 暖机根，无信号
        await session.feed_candle(_candle(1))   # 首信号根 → 开仓
        assert len(calls["orders"]) == 1
        assert session.executor.projected == "long"
    finally:
        await dispose_engine()


async def test_reconcile_corrects_projected_to_flat():
    await init_db()
    session, calls, pos_state = _make_session({1: [Signal(side="long")]})
    try:
        await session.feed_candle(_candle(0))
        await session.feed_candle(_candle(1))
        assert session.executor.projected == "long"
        pos_state["flat"] = True   # 交易所侧已平（OCO 触发 / 外部干预）
        await session.feed_candle(_candle(2))
        assert session.executor.projected == "flat"
    finally:
        await dispose_engine()


async def test_session_stop_loss_triggers_auto_stop():
    await init_db()
    sm = get_sessionmaker()
    async with sm() as s:
        run = await repo.create_run(s, strategy_id=1, inst_id="BTC-USDT-SWAP", bar="1H",
                                    mode="live-auto", config={"mode": "live-auto"})
        run_id = run.id
    session, calls, pos_state = _make_session({1: [Signal(side="long")]}, run_id=run_id,
                                              stop_loss_usd=50.0)
    pos_state["upl"] = -100.0   # 浮亏 100 > 止损 50
    try:
        await session.feed_candle(_candle(0))
        await session.feed_candle(_candle(1))
        assert session.state == "stopped"
        assert session.stop_reason == "session_stop_loss"
        assert len(calls["closes"]) >= 1   # 自动平仓
    finally:
        await dispose_engine()
