"""compute_buy_hold 单测（ai-auto-trading-backtest-analytics §2.3 / §6.1，纯函数）。

区间买入持有基准：首根收盘满仓、逐根盯市。单调上涨 → totalReturn 与 closes[-1]/closes[0]-1
一致、maxDrawdown≤0；平盘 → 全 0；空/单点/首价≤0 → 回落空基准。sharpe 与 compute_metrics
同式（P1-2 交叉校验：同一组 bar_returns 分别喂给共享 _annualized_sharpe 与 compute_metrics，逐位相等）。
"""
from __future__ import annotations

from app.engine.benchmark import compute_buy_hold
from app.engine.metrics import compute_metrics
from app.engine.metrics_ext import _annualized_sharpe, _bar_returns
from app.engine.simulator import EquityRecord


def test_monotone_up_totalreturn_matches_price_ratio_and_no_drawdown():
    bench = compute_buy_hold([100.0, 110.0, 121.0], [0, 3600, 7200], 10000.0, "1H")
    assert bench["totalReturn"] == round(121.0 / 100.0 - 1.0, 6)   # 0.21
    assert bench["maxDrawdown"] == 0.0                             # 全程创新高
    assert bench["equity"][0]["equity"] == 10000.0                 # 首根满仓 = 初始资金
    assert bench["equity"][-1]["equity"] == round(10000.0 / 100.0 * 121.0, 4)


def test_flat_prices_give_all_zero_metrics():
    bench = compute_buy_hold([100.0, 100.0, 100.0], [0, 3600, 7200], 10000.0, "1H")
    assert bench["totalReturn"] == 0.0
    assert bench["maxDrawdown"] == 0.0
    assert bench["sharpe"] == 0.0
    assert bench["annualizedReturn"] == 0.0


def test_dip_then_recover_captures_negative_drawdown():
    bench = compute_buy_hold([100.0, 90.0, 95.0], [0, 3600, 7200], 10000.0, "1H")
    assert bench["maxDrawdown"] == round(90.0 / 100.0 - 1.0, 6)    # -0.1（峰值 10000）
    assert bench["totalReturn"] == round(95.0 / 100.0 - 1.0, 6)    # -0.05


def test_empty_single_or_nonpositive_first_price_returns_empty():
    assert compute_buy_hold([], [], 10000.0, "1H") == {}
    assert compute_buy_hold([100.0], [0], 10000.0, "1H") == {}     # 单点无意义 → {}
    assert compute_buy_hold([0.0, 100.0], [0, 3600], 10000.0, "1H") == {}
    assert compute_buy_hold([-1.0, 100.0], [0, 3600], 10000.0, "1H") == {}


def test_sharpe_bit_identical_to_compute_metrics_on_same_returns():
    """[P1-2] 基准 sharpe 与 compute_metrics 逐位相等（ddof=1、同年化因子），使策略 vs 基准可比。"""
    closes = [100.0, 110.0, 105.0, 115.0, 108.0]
    ts = [0, 3600, 7200, 10800, 14400]
    cap = 10000.0
    bench = compute_buy_hold(closes, ts, cap, "1H")
    units = cap / closes[0]
    equity_points = [EquityRecord(ts=t, equity=units * c, drawdown=0.0) for t, c in zip(ts, closes)]
    metrics = compute_metrics(equity_points, [], "1H", cap)
    # 共享助手 == compute_metrics == benchmark 三处逐位一致
    returns = _bar_returns([units * c for c in closes])
    assert round(_annualized_sharpe(returns, "1H"), 6) == metrics["sharpe"]
    assert bench["sharpe"] == metrics["sharpe"]
