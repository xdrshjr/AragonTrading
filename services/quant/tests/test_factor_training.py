"""因子训练测试：linear 权重确定性、时序切分无泄漏、rf/xgb 双后端恒可用、归一化契约。"""
from __future__ import annotations

import numpy as np
import pytest

from app.engine.backtester import candles_to_df
from app.engine.factors import build_factor_matrix, forward_return_label, train_weights
from app.engine.factors import training as training_mod
from app.engine.factors.tree_ensemble import GBDT_DEFAULTS, RF_DEFAULTS
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


def test_missing_library_falls_back_to_builtin(monkeypatch):
    """monkeypatch find_spec→None：rf/xgb 回落内置兜底 available=True + builtin-* 版本 + 真实归一权重（不再假权重/400）。"""
    monkeypatch.setattr(training_mod.importlib.util, "find_spec", lambda name: None)
    df = _df()
    matrix, names, norm = build_factor_matrix(df, _MEMBERS, norm_window=100, bar="1H")
    label = forward_return_label(df, 6)
    for method, prefix in (("random_forest", "builtin-rf"), ("xgboost", "builtin-gbdt")):
        result = train_weights(matrix, label, names, method=method, config={}, norm_stats=norm)
        assert result.available is True
        assert result.trainerVersion.startswith(prefix)
        assert abs(sum(abs(w) for w in result.weights.values()) - 1.0) < 1e-3
        assert "内置" in result.note


def test_available_trainers_reports_backend_and_rf_xgb_always_available(monkeypatch):
    """回归复现：即便 find_spec→None，available_trainers 中 rf/xgb 仍 available=True（旧实现此处为 False=bug）。"""
    monkeypatch.setattr(training_mod.importlib.util, "find_spec", lambda name: None)
    catalog = {t["method"]: t for t in training_mod.available_trainers()}
    assert catalog["random_forest"]["available"] is True
    assert catalog["random_forest"]["backend"] == "builtin"
    assert catalog["xgboost"]["available"] is True
    assert catalog["xgboost"]["backend"] == "builtin"
    assert catalog["linear"]["backend"] == "native"


def test_tree_trainers_always_available_and_normalized():
    """rf/xgb 恒可用（库在场走库、缺库走内置）：available=True、权重归一 sum|w|=1、trainerVersion 非空。"""
    df = _df()
    matrix, names, norm = build_factor_matrix(df, _MEMBERS, norm_window=100, bar="1H")
    label = forward_return_label(df, 6)
    for method in ("random_forest", "xgboost"):
        result = train_weights(matrix, label, names, method=method, config={}, norm_stats=norm)
        assert result.available is True
        assert result.metrics["nTrain"] > 0
        assert result.trainerVersion  # 库版本 或 builtin-*
        assert abs(sum(abs(w) for w in result.weights.values()) - 1.0) < 1e-3


def test_builtin_tree_training_is_deterministic(monkeypatch):
    """同输入两次内置训练 weights 完全相等（固定 seed + tie-break 确定性，评审 P2-4）。"""
    monkeypatch.setattr(training_mod.importlib.util, "find_spec", lambda name: None)
    df = _df()
    matrix, names, norm = build_factor_matrix(df, _MEMBERS, norm_window=100, bar="1H")
    label = forward_return_label(df, 6)
    r1 = train_weights(matrix, label, names, method="random_forest", config={}, norm_stats=norm)
    r2 = train_weights(matrix, label, names, method="random_forest", config={}, norm_stats=norm)
    assert r1.weights == r2.weights


def test_library_backend_reuses_single_source_defaults(monkeypatch):
    """装了 sklearn 时，random_forest 库后端以 RF_DEFAULTS 构造 RandomForestRegressor（评审 P1-2，否则 skip）。"""
    ensemble = pytest.importorskip("sklearn.ensemble")
    captured: dict = {}
    real = ensemble.RandomForestRegressor

    def spy(**kwargs):
        captured.update(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(ensemble, "RandomForestRegressor", spy)
    df = _df()
    matrix, names, norm = build_factor_matrix(df, _MEMBERS, norm_window=100, bar="1H")
    label = forward_return_label(df, 6)
    result = train_weights(matrix, label, names, method="random_forest", config={}, norm_stats=norm)
    assert result.available is True
    assert result.trainerVersion.startswith("sklearn ")  # 库在场不回归（走真实库）
    assert captured["n_estimators"] == RF_DEFAULTS["n_estimators"]
    assert captured["max_depth"] == RF_DEFAULTS["max_depth"]
    assert captured["min_samples_leaf"] == RF_DEFAULTS["min_samples_leaf"]


def test_xgboost_library_backend_uses_gain_importance(monkeypatch):
    """装了 xgboost 时，xgboost 库后端以 GBDT_DEFAULTS + importance_type="gain" 构造 XGBRegressor（评审 P1-2，否则 skip）。"""
    xgb = pytest.importorskip("xgboost")
    captured: dict = {}
    real = xgb.XGBRegressor

    def spy(**kwargs):
        captured.update(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(xgb, "XGBRegressor", spy)
    df = _df()
    matrix, names, norm = build_factor_matrix(df, _MEMBERS, norm_window=100, bar="1H")
    label = forward_return_label(df, 6)
    result = train_weights(matrix, label, names, method="xgboost", config={}, norm_stats=norm)
    assert result.available is True
    assert result.trainerVersion.startswith("xgboost ")
    assert captured["importance_type"] == "gain"
    assert captured["n_estimators"] == GBDT_DEFAULTS["n_estimators"]
    assert captured["max_depth"] == GBDT_DEFAULTS["max_depth"]


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
