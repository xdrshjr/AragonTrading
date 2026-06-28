"""因子库测试：计算因果性 / 形状 / NaN 暖机 / 库目录一致性 / bar 兼容性。"""
from __future__ import annotations

import numpy as np

from app.engine.backtester import candles_to_df
from app.engine.factors import FACTOR_LIBRARY, available_factors, compute_factor, is_bar_compatible
from app.engine.factors.library import factor_meta
from tests.conftest import make_synthetic_candles


def _df(n: int = 320):
    return candles_to_df(make_synthetic_candles(n, step=3600))


def test_compute_factor_is_strictly_causal():
    """篡改第 k 根之后的未来数据，断言每个因子在 ≤k 的输出逐元素不变（杜绝未来泄漏）。"""
    df = _df()
    k = 160
    for key in FACTOR_LIBRARY:
        base = compute_factor(df, key, None, bar="1H").to_numpy()
        tampered = df.copy()
        for col, mult in (("c", 1.7), ("h", 1.7), ("l", 0.6), ("vol", 3.0)):
            tampered.loc[k + 1:, col] = tampered.loc[k + 1:, col] * mult
        after = compute_factor(tampered, key, None, bar="1H").to_numpy()
        for i in range(k + 1):
            a, b = base[i], after[i]
            assert (np.isnan(a) and np.isnan(b)) or a == b, f"{key} 在第 {i} 根非因果"


def test_compute_factor_shape_matches_df():
    df = _df(200)
    for key in FACTOR_LIBRARY:
        series = compute_factor(df, key, None, bar="1H")
        assert len(series) == len(df)
        assert list(series.index) == list(range(len(df)))


def test_rolling_factor_has_nan_warmup():
    """带回看窗口的因子在暖机段为 NaN（绝不补零外推）。"""
    df = _df(120)
    series = compute_factor(df, "mom_roc", {"win": 20}, bar="1H")
    assert np.isnan(series.iloc[0])
    assert np.isnan(series.iloc[19])
    assert np.isfinite(series.iloc[-1])


def test_unknown_factor_raises():
    df = _df(50)
    try:
        compute_factor(df, "does_not_exist", None, bar="1H")
        assert False, "未知因子应抛 ValueError"
    except ValueError:
        pass


def test_px_vwap_dist_all_nan_on_non_intraday():
    """日内限定因子在非日内 bar 返回全 NaN（coverage=0），不报错（评审 C-P2-5）。"""
    df = _df(100)
    series = compute_factor(df, "px_vwap_dist", None, bar="1D")
    assert series.isna().all()
    assert not is_bar_compatible("px_vwap_dist", "1D")
    assert is_bar_compatible("px_vwap_dist", "1H")


def test_catalog_consistency():
    """available_factors 与库 key 一致，每条含必需字段。"""
    catalog = available_factors()
    assert {f["key"] for f in catalog} == set(FACTOR_LIBRARY)
    for f in catalog:
        assert {"key", "cn", "category", "directionHint", "paramsSchema"} <= set(f)
        assert f["directionHint"] in ("long", "short")
    assert factor_meta("nope") is None
