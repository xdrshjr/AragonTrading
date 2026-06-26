"""SimBroker 撮合单测 (spec §6.1 / §13.1)：手续费/滑点/止损止盈/多空/净值。"""
from __future__ import annotations

import math

import pytest

from app.engine.simulator import RiskParams, SimBroker, TradeRecord
from app.strategies.base import Signal


def _row(ts, h, l):
    return {"ts": ts, "h": h, "l": l}


def test_long_open_close_no_fees():
    b = SimBroker(10000, taker_fee_bps=0, slippage_bps=0)
    # i=0: 产生 long 信号
    b.fill_pending(100, 0, 0)
    b.apply_risk(0, _row(0, 101, 99), RiskParams())
    b.mark(0, 100)
    b.submit([Signal(side="long", strength=1.0)])
    # i=1: 开仓成交于 open=100，收盘 110
    b.fill_pending(100, 1, 1)
    b.apply_risk(1, _row(1, 111, 99), RiskParams())
    b.mark(1, 110)
    b.submit([Signal(side="close")])
    # i=2: 平仓成交于 open=120
    b.fill_pending(120, 2, 2)
    b.mark(2, 120)

    assert len(b.trades) == 2
    assert b.trades[0].side == "long" and abs(b.trades[0].qty - 100) < 1e-6
    assert abs(b.trades[1].pnl - 2000) < 1e-6
    assert abs(b.equity_points[-1].equity - 12000) < 1e-6
    assert b.fees_paid == 0


def test_long_with_fees():
    b = SimBroker(10000, taker_fee_bps=10, slippage_bps=0)  # 10 bps = 0.1%
    b.fill_pending(100, 0, 0); b.mark(0, 100)
    b.submit([Signal(side="long", strength=1.0)])
    b.fill_pending(100, 1, 1); b.mark(1, 110)   # 开仓 fee = 100*100*0.001 = 10
    b.submit([Signal(side="close")])
    b.fill_pending(120, 2, 2); b.mark(2, 120)   # 平仓 fee = 100*120*0.001 = 12
    # net pnl = 2000 - (10 + 12) = 1978
    assert abs(b.trades[1].pnl - 1978) < 1e-6
    assert abs(b.fees_paid - 22) < 1e-6


def test_stop_loss_triggers_intrabar():
    b = SimBroker(10000, taker_fee_bps=0, slippage_bps=0)
    risk = RiskParams(stop_loss_pct=0.05)  # 入场 100 -> 止损 95
    b.set_risk(risk)
    b.fill_pending(100, 0, 0); b.mark(0, 100)
    b.submit([Signal(side="long", strength=1.0)])
    b.fill_pending(100, 1, 1)                      # 开仓 entry=100
    b.apply_risk(1, _row(1, 101, 94), risk)        # low=94 <= 95 -> 触发止损平于 95
    b.mark(1, 96)
    closes = [t for t in b.trades if t.side == "close"]
    assert len(closes) == 1
    assert closes[0].reason == "stop_loss"
    assert abs(closes[0].price - 95) < 1e-6
    assert abs(closes[0].pnl - (-500)) < 1e-6     # 100*(95-100)


def test_short_take_profit():
    b = SimBroker(10000, taker_fee_bps=0, slippage_bps=0)
    risk = RiskParams(take_profit_pct=0.05)  # 空 100 -> 止盈 95
    b.set_risk(risk)
    b.fill_pending(100, 0, 0); b.mark(0, 100)
    b.submit([Signal(side="short", strength=1.0)])
    b.fill_pending(100, 1, 1)                      # 开空 entry=100
    b.apply_risk(1, _row(1, 101, 94), risk)        # low=94 <= 95 -> 止盈
    b.mark(1, 95)
    closes = [t for t in b.trades if t.side == "close"]
    assert closes and closes[0].reason == "take_profit"
    assert abs(closes[0].pnl - 500) < 1e-6        # 100*(100-95)


def test_slippage_applied():
    b = SimBroker(10000, taker_fee_bps=0, slippage_bps=100)  # 1%
    b.fill_pending(100, 0, 0); b.mark(0, 100)
    b.submit([Signal(side="long", strength=1.0)])
    b.fill_pending(100, 1, 1)
    # 买入成交价 = 100 * (1 + 0.01) = 101
    assert abs(b.trades[0].price - 101) < 1e-6


def test_trade_record_old_shape_defaults_risk_fields():
    trade = TradeRecord(
        ts=1,
        side="long",
        price=100.0,
        qty=1.0,
        fee=0.0,
        pnl=0.0,
        position_after=1.0,
        reason="entry",
    )

    assert trade.stop_loss_pct is None
    assert trade.take_profit_pct is None
    assert trade.max_hold_bars is None
    assert trade.stop_loss_price is None
    assert trade.take_profit_price is None


def test_set_risk_normalizes_absent_values_and_positive_hold_bars():
    b = SimBroker(10000, taker_fee_bps=0, slippage_bps=0)
    b.set_risk(RiskParams(
        stop_loss_pct=math.nan,
        take_profit_pct=float("inf"),
        max_hold_bars="7",
    ))

    b.submit([Signal(side="long", strength=1.0)])
    b.fill_pending(100, 1, 1)
    entry = b.trades[0]

    assert entry.stop_loss_pct is None
    assert entry.stop_loss_price is None
    assert entry.take_profit_pct is None
    assert entry.take_profit_price is None
    assert entry.max_hold_bars == 7


def test_set_risk_normalizes_non_finite_hold_bars_to_absent():
    b = SimBroker(10000, taker_fee_bps=0, slippage_bps=0)
    b.set_risk(RiskParams(max_hold_bars=float("inf")))
    b.submit([Signal(side="long", strength=1.0)])
    b.fill_pending(100, 1, 1)

    assert b.trades[0].max_hold_bars is None


@pytest.mark.parametrize(
    ("side", "stop_price", "take_profit_price"),
    [
        ("long", 95.95, 111.10),
        ("short", 103.95, 89.10),
    ],
)
def test_open_records_directional_risk_prices_from_filled_entry(
    side,
    stop_price,
    take_profit_price,
):
    b = SimBroker(10000, taker_fee_bps=0, slippage_bps=100)
    b.set_risk(RiskParams(stop_loss_pct=0.05, take_profit_pct=0.10, max_hold_bars=12))

    b.submit([Signal(side=side, strength=1.0)])
    b.fill_pending(100, 1, 1)
    entry = b.trades[0]

    assert entry.stop_loss_pct == 0.05
    assert entry.take_profit_pct == 0.10
    assert entry.max_hold_bars == 12
    assert entry.stop_loss_price == pytest.approx(stop_price)
    assert entry.take_profit_price == pytest.approx(take_profit_price)


def test_stop_loss_close_uses_stored_snapshot_and_copies_it_to_close():
    b = SimBroker(10000, taker_fee_bps=0, slippage_bps=0)
    b.set_risk(RiskParams(stop_loss_pct=0.05, take_profit_pct=0.10, max_hold_bars=12))
    b.submit([Signal(side="long", strength=1.0)])
    b.fill_pending(100, 1, 1)

    b.apply_risk(
        1,
        _row(1, 101, 94),
        RiskParams(stop_loss_pct=0.50, take_profit_pct=0.50, max_hold_bars=999),
    )
    close = b.trades[1]

    assert close.reason == "stop_loss"
    assert close.stop_loss_pct == 0.05
    assert close.take_profit_pct == 0.10
    assert close.max_hold_bars == 12
    assert close.stop_loss_price == pytest.approx(95.0)
    assert close.take_profit_price == pytest.approx(110.0)


def test_take_profit_close_copies_entry_snapshot():
    b = SimBroker(10000, taker_fee_bps=0, slippage_bps=0)
    b.set_risk(RiskParams(stop_loss_pct=0.05, take_profit_pct=0.10, max_hold_bars=12))
    b.submit([Signal(side="short", strength=1.0)])
    b.fill_pending(100, 1, 1)

    b.apply_risk(1, _row(1, 101, 89), RiskParams())
    close = b.trades[1]

    assert close.reason == "take_profit"
    assert close.stop_loss_price == pytest.approx(105.0)
    assert close.take_profit_price == pytest.approx(90.0)
    assert close.stop_loss_pct == 0.05
    assert close.take_profit_pct == 0.10


def test_max_hold_uses_stored_snapshot_not_later_risk_argument():
    b = SimBroker(10000, taker_fee_bps=0, slippage_bps=0)
    b.set_risk(RiskParams(max_hold_bars=2))
    b.submit([Signal(side="long", strength=1.0)])
    b.fill_pending(100, 1, 1)

    b.apply_risk(3, _row(3, 101, 99), RiskParams(max_hold_bars=999))
    b.fill_pending(100, 4, 4)

    assert b.trades[1].reason == "max_hold"
    assert b.trades[1].max_hold_bars == 2


def test_apply_risk_ignores_later_risk_when_entry_snapshot_is_absent():
    b = SimBroker(10000, taker_fee_bps=0, slippage_bps=0)
    b.set_risk(RiskParams())
    b.submit([Signal(side="long", strength=1.0)])
    b.fill_pending(100, 1, 1)

    b.apply_risk(1, _row(1, 101, 94), RiskParams(stop_loss_pct=0.05, max_hold_bars=1))
    b.fill_pending(100, 2, 2)

    assert len(b.trades) == 1
    assert b.trades[0].stop_loss_pct is None
    assert b.trades[0].stop_loss_price is None
    assert b.trades[0].max_hold_bars is None
