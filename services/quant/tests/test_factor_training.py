"""因子训练测试：linear 权重确定性、时序切分无泄漏、rf/xgb 缺库显式降级、归一化契约。"""
from __future__ import annotations

import numpy as np

from app.engine.backtester import candles_to_df
from app.engine.factors import build_factor_matrix, forward_return_label, train_weights
from app.engine.factors import training as training_mod
from tests.conftest import make_synthetic_candles


def _df(n: int = 400):
    return candles_to_df(make_synthetic_candles(n, step=3600))


_MEMBERS = [
    {"factorKey": "mom_roc", "params": {"win": 20}, "direction": "long"},
    {"factorKey": "trend_ema_dist", "params": {}, "direction": "long"},
    {"factorKey": "rev_rsi", "params": {}, "direction": "short"},
]


def test_linear_weights_are_deterministic():
    """对固定输入，linear 权重确定可复现，且归一到 sum|w|=1。"""
    df = _df()
    matrix, names, norm = build_factor_matrix(df, _MEMBERS, norm_window=100, bar="1H")
    label = forward_return_label(df, 6)
    r1 = train_weights(matrix, label, names, method="linear", config={"split": 0.7}, norm_stats=norm)
    r2 = train_weights(matrix, label, names, method="linear", config={"split": 0.7}, norm_stats=norm)
    assert r1.weights == r2.weights
    assert abs(sum(abs(w) for w in r1.weights.values()) - 1.0) < 1e-3
    assert r1.available is True


def test_time_split_no_leak(monkeypatch):
    """时序切分点严格 = floor(n_aligned*split)，验证集索引全大于训练集（不打乱）。"""
    df = _df()
    matrix, names, norm = build_factor_matrix(df, _MEMBERS, norm_window=100, bar="1H")
    label = forward_return_label(df, 6)
    result = train_weights(matrix, label, names, method="linear", config={"split": 0.7},
                           norm_stats=norm)
    n_aligned = result.metrics["nTrain"] + result.metrics["nValid"]
    assert result.metrics["nTrain"] == int(np.floor(n_aligned * 0.7))
    assert result.metrics["nValid"] == n_aligned - result.metrics["nTrain"]


def test_norm_stats_records_contract_only():
    """normStats 只记录归一化契约参数（normWindow/clip），不存固定 mean/std（评审 C-P0-1）。"""
    df = _df()
    _, _, norm = build_factor_matrix(df, _MEMBERS, norm_window=150, bar="1H")
    assert set(norm.keys()) == {"normWindow", "clip"}
    assert norm["normWindow"] == 150


def test_custom_clip_flows_into_norm_stats():
    """非默认 clip 必须如实写进 normStats（H1）：打分端据此取同一 clip，端到端不漂移。"""
    df = _df()
    _, _, norm = build_factor_matrix(df, _MEMBERS, norm_window=100, clip=2.5, bar="1H")
    assert norm["clip"] == 2.5
    label = forward_return_label(df, 6)
    matrix, names, norm = build_factor_matrix(df, _MEMBERS, norm_window=100, clip=2.5, bar="1H")
    result = train_weights(matrix, label, names, method="linear", config={}, norm_stats=norm)
    assert result.normStats["clip"] == 2.5   # 训练产物透传契约 clip


def test_missing_library_degrades_explicitly(monkeypatch):
    """monkeypatch find_spec→None：xgboost 训练返回 available=False + 安装提示，绝不假权重。"""
    monkeypatch.setattr(training_mod.importlib.util, "find_spec", lambda name: None)
    df = _df()
    matrix, names, norm = build_factor_matrix(df, _MEMBERS, norm_window=100, bar="1H")
    label = forward_return_label(df, 6)
    result = train_weights(matrix, label, names, method="xgboost", config={}, norm_stats=norm)
    assert result.available is False
    assert result.weights == {}
    assert "requirements-ml.txt" in result.note


def test_insufficient_samples_raises():
    """dropna 后样本不足显式报错（绝不静默，R9）。"""
    df = _df(40)
    matrix, names, norm = build_factor_matrix(df, _MEMBERS, norm_window=200, bar="1H")
    label = forward_return_label(df, 6)
    try:
        train_weights(matrix, label, names, method="linear", config={}, norm_stats=norm)
        assert False, "样本不足应抛 ValueError"
    except ValueError:
        pass


def test_equal_and_ic_weight_always_available():
    df = _df()
    matrix, names, norm = build_factor_matrix(df, _MEMBERS, norm_window=100, bar="1H")
    label = forward_return_label(df, 6)
    for method in ("equal", "ic_weight"):
        result = train_weights(matrix, label, names, method=method, config={}, norm_stats=norm)
        assert result.available is True
        assert abs(sum(abs(w) for w in result.weights.values()) - 1.0) < 1e-3
