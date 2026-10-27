"""加密永续扩充因子锁定测试（factor-library-crypto-expansion）。

锁定库总数 == 20、8 个新因子的 category/direction/cn，及在合成 K 线上的定值合理性：
输出有限、值域合规（CMF/ER ∈ [-1,1]、MFI ∈ [0,100]）、暖机 NaN、除零不产 inf。
严格因果由 ``test_factor_library.py::test_compute_factor_is_strictly_causal`` 泛化覆盖。
"""
from __future__ import annotations

import numpy as np

from app.engine.backtester import candles_to_df
from app.engine.factors import FACTOR_LIBRARY, compute_factor
from app.engine.factors.library import factor_meta
from tests.conftest import make_synthetic_candles

# 8 个新因子的契约锁定：key -> (category, direction, cn)
_NEW_FACTORS = {
    "mom_tsmom_vol": ("momentum", "long", "波动率调整时序动量（收益/已实现波动）"),
    "mom_macd_hist": ("momentum", "long", "MACD 柱状（归一化动量加速度）"),
    "trend_donchian_pos": ("trend", "long", "唐奇安通道位置（突破/趋势位置）"),
    "trend_efficiency_ratio": ("trend", "long", "考夫曼效率比（净位移/路径，带符号）"),
    "rev_str": ("reversal", "short", "短期反转（近 N 根收益反向）"),
    "volu_mfi": ("volume", "short", "资金流量指标 MFI（量价超买超卖）"),
    "volu_cmf": ("volume", "long", "蔡金资金流 CMF（累积/派发）"),
    "vol_skew": ("volatility", "short", "收益偏度（尾部/彩票效应）"),
}
_EPS = 1e-9


def _df(n: int = 320):
    return candles_to_df(make_synthetic_candles(n, step=3600))


def _flat_candles(n: int = 120):
    """全平（o==h==l==c、等量）K 线：触发所有分母守卫（h-l=0、rv=0、path=0、vol 非零）。"""
    return [{"ts": 1_700_000_000 + i * 3600, "o": 100.0, "h": 100.0,
             "l": 100.0, "c": 100.0, "vol": 1000.0} for i in range(n)]


def test_library_total_is_twenty():
    """扩充后内置因子总数锁定为 20。"""
    assert len(FACTOR_LIBRARY) == 20


def test_new_factors_category_direction_cn():
    """8 个新因子的 category/direction/cn 契约锁定（复用现有 7 类，零前端改动）。"""
    for key, (category, direction, cn) in _NEW_FACTORS.items():
        meta = factor_meta(key)
        assert meta is not None, f"缺少新因子 {key}"
        assert meta["category"] == category, f"{key} 类别应为 {category}"
        assert meta["directionHint"] == direction, f"{key} 方向应为 {direction}"
        assert meta["cn"] == cn


def test_new_factors_reuse_existing_categories():
    """新因子只复用现有 7 个类别（不引入新类别，避免改前端硬编码标签）。"""
    existing = {"momentum", "trend", "reversal", "volatility",
                "volume", "liquidity", "mean_revert"}
    for key in _NEW_FACTORS:
        assert FACTOR_LIBRARY[key].category in existing


def test_new_factors_finite_and_no_inf_on_synthetic():
    """合成 K 线上每个新因子有非空有限输出，且全程不产 inf（除零安全）。"""
    df = _df()
    for key in _NEW_FACTORS:
        arr = compute_factor(df, key, None, bar="1H").to_numpy()
        assert not np.isinf(arr).any(), f"{key} 产生了 inf"
        assert np.isfinite(arr).sum() > 0, f"{key} 无任何有限输出"


def test_bounded_factors_stay_in_range():
    """有界因子值域合规：ER/CMF ∈ [-1,1]、MFI ∈ [0,100]。"""
    df = _df()
    checks = {"trend_efficiency_ratio": (-1.0, 1.0), "volu_cmf": (-1.0, 1.0),
              "volu_mfi": (0.0, 100.0)}
    for key, (lo, hi) in checks.items():
        arr = compute_factor(df, key, None, bar="1H").to_numpy()
        finite = arr[np.isfinite(arr)]
        assert finite.min() >= lo - _EPS, f"{key} 下越界"
        assert finite.max() <= hi + _EPS, f"{key} 上越界"


def test_rolling_new_factors_have_nan_warmup():
    """带回看窗口的新因子暖机段为 NaN（绝不补零外推）。"""
    df = _df(120)
    warmups = {"mom_tsmom_vol": 29, "trend_donchian_pos": 19,
               "trend_efficiency_ratio": 19, "volu_mfi": 12,
               "volu_cmf": 18, "vol_skew": 29}
    for key, last_nan in warmups.items():
        series = compute_factor(df, key, None, bar="1H")
        assert np.isnan(series.iloc[0]), f"{key} 首根应为 NaN"
        assert np.isnan(series.iloc[last_nan]), f"{key} 暖机段第 {last_nan} 根应为 NaN"
        assert np.isfinite(series.iloc[-1]), f"{key} 末根应有限"


def test_zero_denominator_never_produces_inf():
    """全平/十字星 K 线触发所有分母守卫（h-l=0、rv=0、path=0）时绝不产 inf。"""
    df = candles_to_df(_flat_candles(80))
    for key in _NEW_FACTORS:
        arr = compute_factor(df, key, None, bar="1H").to_numpy()
        assert not np.isinf(arr).any(), f"{key} 在零分母输入下产生了 inf"
