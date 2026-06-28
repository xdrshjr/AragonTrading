"""因子评估测试：IC/RankIC 正负号、分位单调性、空样本安全口径、右端因果截断。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.engine.factors.evaluation import evaluate_factor, forward_return, resolve_direction
from app.engine.backtester import candles_to_df


def _df_with_returns(n: int = 300):
    """构造收益有变化的合成 K 线（步进收益带正弦波动），便于 IC 有方差。"""
    ts0 = 1_700_000_000
    rows = []
    price = 100.0
    rets = []
    for i in range(n):
        r = 0.01 * np.sin(i / 5.0) + 0.0005
        rets.append(r)
        o = price
        price = price * (1 + r)
        rows.append({"ts": ts0 + i * 3600, "o": o, "h": max(o, price) + 0.1,
                     "l": min(o, price) - 0.1, "c": price, "vol": 1000.0 + i})
    return candles_to_df(rows), np.asarray(rets)


def test_forward_return_right_end_truncation():
    """前瞻收益右端因果截断：有效样本数随 horizon 增大单调不增（绝不补零）。"""
    df, _ = _df_with_returns(200)
    close = df["c"].to_numpy(float)
    c1 = np.isfinite(forward_return(close, 1)).sum()
    c12 = np.isfinite(forward_return(close, 12)).sum()
    assert c1 == len(close) - 1
    assert c1 > c12 == len(close) - 12


def test_ic_sign_positive_for_perfect_long_factor():
    """因子值 = 下一根真实收益（完美预测）→ direction=long 的 IC/RankIC 显著为正。"""
    df, rets = _df_with_returns(300)
    values = pd.Series(rets)   # factor[i] = realized r_i = fwd_ret[i,1]
    result = evaluate_factor(df, values, "long", horizons=(1,))
    stat = result.horizons[0]
    assert stat.ic is not None and stat.ic > 0.9
    assert stat.rankIc is not None and stat.rankIc > 0.9


def test_ic_sign_short_direction_flips_to_positive():
    """因子值与未来收益负相关，direction=short → 方向调整后 IC 仍为正。"""
    df, rets = _df_with_returns(300)
    values = pd.Series(-rets)   # 负相关
    result = evaluate_factor(df, values, "short", horizons=(1,))
    assert result.horizons[0].ic > 0.9


def test_resolve_direction_auto_picks_ic_sign():
    """auto 在评估窗口一次性定号：负相关因子 → short。"""
    df, rets = _df_with_returns(300)
    close = df["c"].to_numpy(float)
    assert resolve_direction(np.asarray(-rets), close, "auto", primary_horizon=1) == "short"
    assert resolve_direction(np.asarray(rets), close, "auto", primary_horizon=1) == "long"


def test_empty_sample_is_safe():
    """全 NaN 因子 → 统计走安全口径（None），occurrences=0，绝不对空集取均值。"""
    df, _ = _df_with_returns(120)
    values = pd.Series([np.nan] * len(df))
    result = evaluate_factor(df, values, "long", horizons=(1, 3))
    assert result.occurrences == 0
    assert all(h.ic is None and h.ir is None for h in result.horizons)


def test_quantile_monotonicity_positive_for_perfect_factor():
    """完美预测因子的分位前瞻收益应单调递增（单调性接近 1）。"""
    df, rets = _df_with_returns(300)
    result = evaluate_factor(df, pd.Series(rets), "long", horizons=(1,), quantiles=5)
    stat = result.horizons[0]
    assert stat.monotonicity is not None and stat.monotonicity > 0.8
    assert stat.topBottomSpread is not None and stat.topBottomSpread > 0
