"""SimBroker 撮合单测 (spec §6.1 / §13.1)：手续费/滑点/止损止盈/多空/净值。"""
from __future__ import annotations

from app.engine.simulator import RiskParams, SimBroker
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
