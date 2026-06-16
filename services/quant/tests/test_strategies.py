"""策略 + 回测端到端单测 (spec §13.1)：合成数据触发信号、回测可跑通。"""
from __future__ import annotations

import pandas as pd

from app.engine.backtester import run_backtest
from app.strategies import STRATEGY_REGISTRY
from app.strategies.base import Signal
from app.strategies.long_shadow_reversal import LongShadowReversal
from app.strategies.ema_cross_scalper import EmaCrossScalper
from tests.conftest import make_synthetic_candles


def test_registry_has_builtins():
    # 注：原 `>= 5` 在新增后仍为真，此处为覆盖增强 + 命名纠正(R7)，纳入 2 个新 key。
    for key in ("long_shadow_reversal", "momentum_breakout", "ema_cross_scalper",
                "bollinger_reversion", "vwap_rsi_momentum",
                "trend_pullback_long", "trend_pullback_short"):
        assert key in STRATEGY_REGISTRY
    assert len(STRATEGY_REGISTRY) >= 7


def test_long_shadow_emits_long_on_hammer():
    strat = LongShadowReversal()
    # 构造：前段下跌后一根长下影锤子线
    rows = []
    ts = 1_700_000_000
    price = 100.0
    for i in range(10):
        price -= 1.5  # 持续下跌
        rows.append({"ts": ts + i * 3600, "o": price + 1.5, "h": price + 2,
                     "l": price - 0.5, "c": price, "vol": 100.0})
    # 第 10 根：长下影 + 收阳（锤子）
    o = price
    c = price + 1.0
    rows.append({"ts": ts + 10 * 3600, "o": o, "h": c + 0.3, "l": o - 8.0, "c": c, "vol": 100.0})
    df = pd.DataFrame(rows)
    df = strat.prepare(df, "1H")

    from app.engine.simulator import BrokerContext
    ctx = BrokerContext(position_side="flat", bars_in_position=0, entry_price=0, equity=10000)
    signals = strat.on_candle(df, len(df) - 1, ctx)
    assert any(s.side == "long" for s in signals)


def test_ema_cross_emits_signal():
    strat = EmaCrossScalper({"fast": 3, "slow": 6, "slope_filter": 0.0})
    # 先跌后涨，制造金叉
    closes = [100 - i for i in range(10)] + [90 + i * 2 for i in range(10)]
    df = pd.DataFrame({
        "ts": [1_700_000_000 + i * 3600 for i in range(len(closes))],
        "o": closes, "h": [c + 1 for c in closes], "l": [c - 1 for c in closes],
        "c": closes, "vol": [100.0] * len(closes),
    })
    df = strat.prepare(df, "1H")
    from app.engine.simulator import BrokerContext
    found = False
    for i in range(strat.warmup(), len(df)):
        ctx = BrokerContext(position_side="flat", bars_in_position=0, entry_price=0, equity=10000)
        sigs = strat.on_candle(df, i, ctx)
        if any(isinstance(s, Signal) and s.side in ("long", "short") for s in sigs):
            found = True
            break
    assert found


def test_backtest_runs_for_all_strategies():
    candles = make_synthetic_candles(300)
    for key, cls in STRATEGY_REGISTRY.items():
        strat = cls()
        result = run_backtest(strat, candles, "1H", initial_capital=10000,
                              taker_fee_bps=5, slippage_bps=2)
        # 指标结构完整、权益点数量 == K 线数量
        assert set(result.metrics.keys()) >= {"totalReturn", "winRate", "sharpe", "maxDrawdown"}
        assert len(result.equity_points) == len(candles)
        # 回撤非正
        assert all(p.drawdown <= 1e-9 for p in result.equity_points)


def test_vwap_strategy_silent_on_daily():
    """VWAP 策略在 1D 周期无信号 (C5)。"""
    candles = make_synthetic_candles(100, step=86400)
    strat = STRATEGY_REGISTRY["vwap_rsi_momentum"]()
    result = run_backtest(strat, candles, "1D", initial_capital=10000,
                          taker_fee_bps=5, slippage_bps=2)
    assert len([t for t in result.trades if t.side != "close"]) == 0
