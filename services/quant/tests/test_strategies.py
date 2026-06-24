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
                "trend_pullback_long", "trend_pullback_short",
                "range_scalping_strategy"):
        assert key in STRATEGY_REGISTRY
    assert len(STRATEGY_REGISTRY) >= 8


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
        # 组合策略（kind=="combo"）走 engine.combo.run_combo_backtest，不可直接进 run_backtest
        # （其 on_candle 显式抛错）；其覆盖见 test_combo_engine.py。
        if getattr(cls.meta, "kind", "atomic") == "combo":
            continue
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


# ---------------- 多周期：高周期趋势门控（MTF T5 / G4）----------------

def _cross_up_df() -> pd.DataFrame:
    """两行 df：第 1 行恰为快线上穿慢线的金叉（慢线水平 → slope=0）。"""
    return pd.DataFrame({
        "ts": [1_700_000_000, 1_700_000_300], "o": [50.0, 50.0],
        "h": [51.0, 51.0], "l": [49.0, 49.0], "c": [50.0, 50.0],
        "ema_fast": [99.0, 101.0], "ema_slow": [100.0, 100.0],
    })


def test_htf_filter_blocks_counter_trend_cross():
    """基础金叉但高周期下行：开启过滤不开多；关闭过滤则开多（多周期改变决策）。"""
    from app.engine.simulator import BrokerContext
    ctx = BrokerContext(position_side="flat", bars_in_position=0, entry_price=0, equity=10000)
    df = _cross_up_df()
    # 显式注入「高周期下行」列（数值列非 NaN → 走方向门控）。
    df["htf_1H_ema_fast"] = [90.0, 90.0]
    df["htf_1H_ema_slow"] = [100.0, 100.0]
    df["htf_1H_slope"] = [-1.0, -1.0]
    df["htf_1H_uptrend"] = [False, False]
    df["htf_1H_downtrend"] = [True, True]

    on = EmaCrossScalper({"fast": 3, "slow": 6, "htf_trend_filter": True, "htf_bar": "1H"})
    assert on.on_candle(df, 1, ctx) == []          # 逆 1H 趋势的金叉被过滤
    off = EmaCrossScalper({"fast": 3, "slow": 6, "htf_trend_filter": False})
    assert any(s.side == "long" for s in off.on_candle(df, 1, ctx))


def test_htf_required_gates_on_missing_data():
    """高周期数据缺失（数值列 NaN）：htf_required=False 放行、True 禁开（P1-1，不 bool(nan)）。"""
    from app.engine.simulator import BrokerContext
    ctx = BrokerContext(position_side="flat", bars_in_position=0, entry_price=0, equity=10000)
    df = _cross_up_df()
    df["htf_1H_ema_fast"] = [float("nan"), float("nan")]
    df["htf_1H_uptrend"] = [False, False]
    df["htf_1H_downtrend"] = [False, False]

    lenient = EmaCrossScalper({"fast": 3, "slow": 6, "htf_trend_filter": True,
                               "htf_required": False})
    assert any(s.side == "long" for s in lenient.on_candle(df, 1, ctx))
    strict = EmaCrossScalper({"fast": 3, "slow": 6, "htf_trend_filter": True,
                              "htf_required": True})
    assert strict.on_candle(df, 1, ctx) == []


def test_htf_filter_absent_columns_is_backward_compatible():
    """过滤开启但未并入高周期列（无帧）：按缺失放行，等价单周期行为（向后兼容）。"""
    from app.engine.simulator import BrokerContext
    ctx = BrokerContext(position_side="flat", bars_in_position=0, entry_price=0, equity=10000)
    df = _cross_up_df()                            # 不含任何 htf_* 列
    strat = EmaCrossScalper({"fast": 3, "slow": 6, "htf_trend_filter": True,
                             "htf_required": False})
    assert any(s.side == "long" for s in strat.on_candle(df, 1, ctx))


# ---------------- 黄金回归（MTF T4 / G3）：单周期既有策略行为零变化 ----------------

_UNCHANGED_KEYS = ["long_shadow_reversal", "momentum_breakout", "bollinger_reversion",
                   "vwap_rsi_momentum", "range_scalping_strategy"]


def test_single_timeframe_strategies_declare_no_frames():
    """既有单周期策略 timeframes() 恒为空 → 永不触发 MTF 取数/对齐路径。"""
    for key in _UNCHANGED_KEYS:
        assert STRATEGY_REGISTRY[key]().timeframes() == []


def test_mtf_plumbing_is_inert_for_single_timeframe_strategies():
    """timeframes()==[] 的策略：htf_frames=None 与 ={} 两路成交/净值/指标逐字节一致（G3）。"""
    candles = make_synthetic_candles(300)
    for key in _UNCHANGED_KEYS:
        base = run_backtest(STRATEGY_REGISTRY[key](), candles, "1H", initial_capital=10000,
                            taker_fee_bps=5, slippage_bps=2)
        with_empty = run_backtest(STRATEGY_REGISTRY[key](), candles, "1H",
                                  initial_capital=10000, taker_fee_bps=5, slippage_bps=2,
                                  htf_frames={})
        assert base.metrics == with_empty.metrics
        assert [(t.ts, t.side, t.price, t.qty) for t in base.trades] == \
               [(t.ts, t.side, t.price, t.qty) for t in with_empty.trades]
        assert [(e.ts, e.equity) for e in base.equity_points] == \
               [(e.ts, e.equity) for e in with_empty.equity_points]
