"""因子 engine 退化输入鲁棒性 battery（R3）：把每个退化场景钉成受保护的回归不变量。

回归优先（CLAUDE.md 七）：engine 对全 NaN / 常数序列 / 样本不足 / 空·全零权重 / 含 NaN·Inf 的 OHLCV
处理「良好但未被测试固化」，任何后续重构都可能悄悄破坏「std=0 → NaN」「auto → ValueError」这类不变量。
本套件用确定性合成 K 线（零网络）逐一固化：不崩 + 返回 NaN/None/note/显式错误、**绝不假数据、绝不传播 Inf**。

护栏纪律（评审放行条件 3）：仅在本套件暴露**真实传播**时才补最小护栏。实测 compose_score 经 causal_zscore
的 clip 天然 Inf-safe、evaluate/score_points 经 isfinite 配对/过滤天然不外泄 Inf——「不传播出 Inf」不变量
**已在边界成立**，故不新增任何防御代码（避免对「真实数据永不为 Inf」的分支加噪音，CLAUDE.md 五；详见 spec §10）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.engine.backtester import candles_to_df
from app.engine.factors import (
    FACTOR_LIBRARY,
    build_factor_matrix,
    causal_zscore,
    compose_score,
    compute_factor,
    default_candidates,
    evaluate_factor,
    evaluate_factor_key,
    forward_return_label,
    mine_factors,
    score_points,
    train_weights,
)
from app.engine.factors.training import MIN_TRAIN_SAMPLES
from tests.conftest import make_synthetic_candles


def _df(n: int = 120, step: int = 3600) -> pd.DataFrame:
    return candles_to_df(make_synthetic_candles(n, start_ts=1_700_000_000, step=step))


_MEMBERS = [{"factorKey": "mom_roc", "params": {"win": 20}, "direction": "long", "weight": 1.0},
            {"factorKey": "rev_rsi", "params": {}, "direction": "short", "weight": 1.0}]


# ============================ compute_factor ============================
def test_compute_factor_extreme_window_all_nan_not_raise():
    """窗口远超序列长度 → 全 NaN，不抛异常（退化参数不崩）。"""
    series = compute_factor(_df(), "mom_roc", {"win": 9999}, bar="1H")
    arr = np.asarray(series, dtype=float)
    assert len(arr) == 120
    assert np.isnan(arr).all()


def test_compute_factor_unknown_key_raises():
    """未知 factorKey → ValueError（显式报错，由工具/路由层转 400，绝不静默）。"""
    with pytest.raises(ValueError):
        compute_factor(_df(), "not_a_real_factor", None, bar="1H")


# ============================ causal_zscore ============================
def test_causal_zscore_constant_series_all_nan_no_inf():
    """常数序列（窗口内 std==0）→ 全 NaN，绝不除零、绝不产出 Inf（归一化契约安全口径）。"""
    z = causal_zscore(pd.Series([5.0] * 120), 20)
    arr = np.asarray(z, dtype=float)
    assert np.isnan(arr).all()
    assert not np.isinf(arr).any()


# ============================ compose_score ============================
def test_compose_score_auto_direction_raises():
    """成员方向 auto → ValueError（打分期前视泄漏防线，绝不在打分时现算符号）。"""
    with pytest.raises(ValueError):
        compose_score(_df(), [{"factorKey": "mom_roc", "direction": "auto", "weight": 1.0}],
                      None, norm_window=20, bar="1H")


def test_compose_score_zero_and_empty_weights_all_nan():
    """全 0 权重 / 空成员 → 全 NaN（不 Inf、不除零）；无任何成员贡献即整段 NaN。"""
    zero_w = [{**m, "weight": 0.0} for m in _MEMBERS]
    cs = compose_score(_df(), zero_w, None, norm_window=20, bar="1H")
    arr = np.asarray(cs, dtype=float)
    assert np.isnan(arr).all() and not np.isinf(arr).any()

    empty = compose_score(_df(), [], None, norm_window=20, bar="1H")
    assert np.isnan(np.asarray(empty, dtype=float)).all()


def test_compose_score_squash_bounds_output():
    """squash=True：有限得分点恒落在 [-1,1]（tanh 压缩给阈值恒定基准）。"""
    cs = compose_score(_df(), _MEMBERS, None, norm_window=20, squash=True, bar="1H")
    finite = np.asarray(cs, dtype=float)
    finite = finite[np.isfinite(finite)]
    assert finite.size > 0
    assert np.all(finite >= -1.0) and np.all(finite <= 1.0)


# ============================ evaluate_factor ============================
def test_evaluate_factor_all_nan_factor_safe():
    """全 NaN 因子 → occurrences==0、coverage==0、各 horizon stat 为 None，不抛（绝不对空集取均值）。"""
    result = evaluate_factor(_df(), pd.Series([np.nan] * 120), "long", factor_key="x")
    assert result.occurrences == 0 and result.coverage == 0.0
    for stat in result.horizons:
        assert stat.ic is None and stat.rankIc is None and stat.ir is None


def test_evaluate_factor_constant_factor_safe():
    """常数因子（无方差）→ IC/RankIC 退化为 None（相关系数对零方差返回 None），不崩。"""
    result = evaluate_factor(_df(), pd.Series([1.0] * 120), "long", factor_key="x")
    assert result.horizons[0].ic is None and result.horizons[0].rankIc is None


# ============================ train_weights ============================
def test_train_weights_insufficient_samples_raises():
    """dropna 后样本 < MIN_TRAIN_SAMPLES → ValueError（service 转 400 FACTOR_TRAIN_FAILED）。"""
    small = _df(25)
    matrix, names, _ = build_factor_matrix(small, _MEMBERS, norm_window=5, bar="1H")
    label = forward_return_label(small, 3)
    assert len(label) < MIN_TRAIN_SAMPLES + 30  # 区间本就短
    with pytest.raises(ValueError):
        train_weights(matrix, label, names, method="linear")


@pytest.mark.parametrize("method", ["linear", "ic_weight", "equal"])
def test_train_weights_constant_label_degrades_gracefully(method):
    """常数 label（零方差）→ 不崩、权重有限可解释（linear→零解回退等权 / ic_weight→无 IC 回退等权）。"""
    df = _df(160)
    matrix, names, norm_stats = build_factor_matrix(df, _MEMBERS, norm_window=10, bar="1H")
    label = np.zeros(matrix.shape[0], dtype=float)
    result = train_weights(matrix, label, names, method=method, norm_stats=norm_stats)
    assert result.available is True
    assert set(result.weights) == set(names)
    assert all(np.isfinite(w) for w in result.weights.values())


# ============================ mine_factors ============================
def test_mine_factors_skips_bar_incompatible_not_raise():
    """候选含 bar 不兼容因子（日内限定因子用于 1D）→ 标 note 跳过、整轮不报错（绝不拖垮扫描）。"""
    daily = candles_to_df(make_synthetic_candles(120, start_ts=1_700_000_000, step=86400))
    candidates = default_candidates("1D")
    ranked = mine_factors(daily, candidates, bar="1D")
    incompatible = [r for r in ranked if r.get("note") == "bar_incompatible"]
    assert len(incompatible) >= 1                       # px_vwap_dist 等日内因子被标记
    assert all(r.get("ir") is None for r in incompatible)


# ============================ OHLCV 含 NaN/Inf —— 不传播出 Inf 不变量 ============================
def _df_with_inf_nan() -> pd.DataFrame:
    rows = make_synthetic_candles(120, start_ts=1_700_000_000, step=3600)
    rows[60]["c"] = float("inf")     # 收盘 Inf（最易经 pct_change 传播）
    rows[70]["vol"] = float("inf")   # 量 Inf
    rows[80]["c"] = float("nan")     # 收盘 NaN
    return candles_to_df(rows)


def test_inf_nan_ohlcv_compose_score_does_not_emit_inf():
    """含 Inf/NaN 的 OHLCV → compose_score 经 causal_zscore 的 clip 天然不外泄 Inf（无需护栏）。"""
    cs = compose_score(_df_with_inf_nan(), _MEMBERS, None, norm_window=20, bar="1H")
    assert not np.isinf(np.asarray(cs, dtype=float)).any()
    # 有效得分点（剔暖机 NaN）全为有限值——「不传播出 Inf」在打分边界成立。
    points = score_points(_df_with_inf_nan(), cs)
    assert all(np.isfinite(p["score"]) for p in points)


def test_inf_nan_ohlcv_evaluate_does_not_raise():
    """含 Inf/NaN 的 OHLCV → evaluate 经 isfinite 配对剔除非有限值，不崩、统计字段不含 Inf。"""
    result = evaluate_factor_key(_df_with_inf_nan(), "mom_roc", None, "long", bar="1H")
    for stat in result.horizons:
        for value in (stat.ic, stat.rankIc, stat.ir):
            assert value is None or np.isfinite(value)
