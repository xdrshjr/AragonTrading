"""compute_extended_metrics 单测（ai-auto-trading-backtest-analytics §2.2 / §6.1，纯函数）。

已知 equity/trades → 逐键断言 Sortino/Calmar/CAGR/年化波动/回撤时长/在场占比/盈亏比/连胜连亏/
平均持仓/交易频率；退化输入（无成交、单根、全胜、maxDrawdown=0、final<=0）不抛且回落 0。
口径与 metrics.py 逐字对齐（ddof=1、平仓笔 side=="close"）。离线纯函数，无外部依赖。
"""
from __future__ import annotations

import math

import pytest

from app.engine.metrics_ext import (
    _annualized_sharpe,
    _bar_returns,
    _sample_std,
    compute_extended_metrics,
)
from app.engine.simulator import EquityRecord, TradeRecord


def _eq(points: list[tuple[int, float]]) -> list[EquityRecord]:
    """由 (ts, equity) 构造带峰值回撤的权益曲线（drawdown=equity/peak-1 ≤0）。"""
    out: list[EquityRecord] = []
    peak = 0.0
    for ts, equity in points:
        peak = max(peak, equity)
        out.append(EquityRecord(ts=ts, equity=equity, drawdown=min(equity / peak - 1.0, 0.0)))
    return out


def _trade(ts: int, side: str, pnl: float) -> TradeRecord:
    return TradeRecord(ts=ts, side=side, price=100.0, qty=1.0, fee=0.0, pnl=pnl,
                       position_after=0.0, reason="t")


# 12 根权益：含一段长度 2 的连续水下（idx 2,3），其余创新高 → maxDrawdownBars=2。
_EQUITY = _eq([
    (0, 10000), (3600, 10100), (7200, 10050), (10800, 9950), (14400, 10200), (18000, 10300),
    (21600, 10250), (25200, 10400), (28800, 10500), (32400, 10450), (36000, 10600), (39600, 10700),
])
# 5 笔平仓（夹在开仓间），pnl 顺序 [100,200,-50,-30,150]：连胜 2、连亏 2。
_TRADES = [
    _trade(0, "long", 0), _trade(3600, "close", 100),
    _trade(7200, "long", 0), _trade(10800, "close", 200),
    _trade(14400, "short", 0), _trade(21600, "close", -50),      # 持仓 2 根
    _trade(25200, "long", 0), _trade(28800, "close", -30),
    _trade(32400, "long", 0), _trade(39600, "close", 150),       # 持仓 2 根
]


def test_deterministic_quality_and_streak_metrics():
    ext = compute_extended_metrics(_EQUITY, _TRADES, "1H", 10000.0, bars_in_market=6)
    assert ext["avgWin"] == 150.0          # (100+200+150)/3
    assert ext["avgLoss"] == 40.0          # |(-50-30)/2|
    assert ext["payoffRatio"] == 3.75      # 150/40
    assert ext["largestWin"] == 200.0
    assert ext["largestLoss"] == -50.0
    assert ext["maxConsecutiveWins"] == 2
    assert ext["maxConsecutiveLosses"] == 2
    assert ext["avgHoldBars"] == 1.4       # holds [1,1,2,1,2] → 7/5


def test_drawdown_duration_and_time_in_market():
    ext = compute_extended_metrics(_EQUITY, _TRADES, "1H", 10000.0, bars_in_market=6)
    assert ext["maxDrawdownBars"] == 2                 # idx 2,3 连续水下
    assert ext["maxDrawdownSecs"] == 2 * 3600          # 复用 mtf.bar_to_seconds(1H)=3600
    assert ext["timeInMarketPct"] == 0.5               # 6/12（运行器权威分子）
    # 交易频率：5 笔 / (39600 秒 / 一周)；round(6)
    assert ext["tradesPerWeek"] == round(5 / (39600 / 604800), 6)


def test_risk_ratios_positive_and_finite_on_net_up_curve():
    ext = compute_extended_metrics(_EQUITY, _TRADES, "1H", 10000.0, bars_in_market=6)
    assert ext["annualizedReturn"] > 0                 # base=1.07 → CAGR 正
    assert ext["calmar"] > 0                           # 正收益 / 正回撤幅度
    assert ext["sortino"] > 0                          # 净上行
    assert math.isfinite(ext["annualizedVolatility"]) and ext["annualizedVolatility"] > 0


def test_annualized_volatility_uses_sample_std_ddof1():
    # equity [100,110,99] → returns [0.1,-0.1]，均值 0，样本方差 (0.01+0.01)/1=0.02
    eq = _eq([(0, 100), (3600, 110), (7200, 99)])
    ext = compute_extended_metrics(eq, [], "1H", 100.0, bars_in_market=0)
    expected = math.sqrt(0.02) * math.sqrt(8760)
    assert ext["annualizedVolatility"] == round(expected, 6)
    assert ext["sortino"] == 0.0                       # 均值 0 → Sortino 分子 0


def test_annualized_sharpe_matches_sample_std_definition():
    returns = [0.1, -0.05, 0.02, 0.03]
    std = _sample_std(returns)
    mean = sum(returns) / len(returns)
    expected = mean / std * math.sqrt(8760)
    assert _annualized_sharpe(returns, "1H") == pytest.approx(expected)
    # _bar_returns 与 metrics.py 同式：prev==0 跳过
    assert _bar_returns([0.0, 100.0, 110.0]) == [110.0 / 100.0 - 1.0]


def test_empty_equity_returns_all_zero_without_raising():
    ext = compute_extended_metrics([], [], "1H", 10000.0)
    assert ext["annualizedReturn"] == 0.0 and ext["sortino"] == 0.0 and ext["calmar"] == 0.0
    assert ext["maxDrawdownBars"] == 0 and ext["avgWin"] == 0.0 and ext["tradesPerWeek"] == 0.0


def test_no_trades_zeroes_quality_metrics():
    ext = compute_extended_metrics(_EQUITY, [], "1H", 10000.0, bars_in_market=0)
    for key in ("avgWin", "avgLoss", "payoffRatio", "largestWin", "largestLoss", "avgHoldBars"):
        assert ext[key] == 0.0
    assert ext["maxConsecutiveWins"] == 0 and ext["maxConsecutiveLosses"] == 0
    assert ext["timeInMarketPct"] == 0.0               # bars_in_market=0


def test_all_wins_gives_zero_avg_loss_and_payoff():
    trades = [_trade(0, "long", 0), _trade(3600, "close", 50),
              _trade(7200, "long", 0), _trade(10800, "close", 80)]
    ext = compute_extended_metrics(_EQUITY, trades, "1H", 10000.0, bars_in_market=6)
    assert ext["avgLoss"] == 0.0 and ext["payoffRatio"] == 0.0    # 无亏损笔 → 赔率不除零
    assert ext["largestLoss"] == 0.0 and ext["maxConsecutiveLosses"] == 0


def test_single_bar_does_not_raise_annualization():
    ext = compute_extended_metrics(_eq([(0, 10000)]), [], "1H", 10000.0, bars_in_market=0)
    assert math.isfinite(ext["annualizedReturn"])      # 单根不抛（无 bar_returns）
    assert ext["annualizedVolatility"] == 0.0 and ext["sortino"] == 0.0


def test_zero_max_drawdown_yields_zero_calmar():
    eq = _eq([(0, 10000), (3600, 10100), (7200, 10200)])   # 单调上行 → 全程创新高 → 回撤恒 0
    ext = compute_extended_metrics(eq, [], "1H", 10000.0, bars_in_market=0)
    assert ext["maxDrawdownBars"] == 0 and ext["calmar"] == 0.0


def test_final_equity_non_positive_returns_minus_one_cagr():
    eq = _eq([(0, 10000), (3600, 5000), (7200, 0.0)])       # 归零 → base<=0
    ext = compute_extended_metrics(eq, [], "1H", 10000.0, bars_in_market=0)
    assert ext["annualizedReturn"] == -1.0


def test_infers_time_in_market_from_trade_pairs_when_count_absent():
    # bars_in_market=None → 从成交对推断 in-market 根数 = holds 之和 (7)，bars=12
    ext = compute_extended_metrics(_EQUITY, _TRADES, "1H", 10000.0)
    assert ext["timeInMarketPct"] == round(7 / 12, 6)
