"""内置纯 numpy 树集成单测（tree_ensemble.py）：重要度正确性、非负、确定性、全函数性、行下采样封顶。"""
from __future__ import annotations

import numpy as np

from app.engine.factors.tree_ensemble import (
    BUILTIN_MAX_TRAIN_ROWS,
    fit_gbdt_importances,
    fit_rf_importances,
    resolve_tree_params,
)

RF_KW = {"n_estimators": 40, "max_depth": 6, "min_samples_leaf": 5, "seed": 0}
GBDT_KW = {"n_estimators": 60, "max_depth": 3, "learning_rate": 0.1, "min_samples_leaf": 5, "seed": 0}


def test_rf_identifies_single_informative_feature():
    """单一信息特征（y 强依赖 feature 1）→ 该特征 RF 重要度显著最大。"""
    rng = np.random.default_rng(0)
    x = rng.standard_normal((300, 4))
    y = 3.0 * x[:, 1] + 0.01 * rng.standard_normal(300)
    importance = fit_rf_importances(x, y, **RF_KW)
    assert importance.shape == (4,)
    assert int(np.argmax(importance)) == 1


def test_gbdt_gain_identifies_informative_feature():
    """梯度提升总增益（gain）同样把信息特征 feature 2 顶到最大。"""
    rng = np.random.default_rng(1)
    x = rng.standard_normal((300, 4))
    y = 2.5 * x[:, 2] + 0.01 * rng.standard_normal(300)
    importance = fit_gbdt_importances(x, y, **GBDT_KW)
    assert int(np.argmax(importance)) == 2


def test_clean_step_split_assigns_importance_to_separating_feature():
    """干净阶跃标签由单调特征 0 完美切分 → 方差下降全归 feature 0（验证分裂打分正确性）。"""
    n = 200
    x = np.zeros((n, 2))
    x[:, 0] = np.arange(n, dtype=float)
    x[:, 1] = np.random.default_rng(6).standard_normal(n)
    y = np.where(np.arange(n) < n // 2, -1.0, 1.0).astype(float)
    importance = fit_rf_importances(x, y, n_estimators=10, max_depth=3, min_samples_leaf=5, seed=0)
    assert int(np.argmax(importance)) == 0
    assert importance[0] > importance[1]


def test_importances_are_non_negative():
    """重要度向量恒非负（与现有真实库 feature_importances_ 符号语义一致）。"""
    rng = np.random.default_rng(2)
    x = rng.standard_normal((250, 5))
    y = x[:, 0] - x[:, 3] + 0.05 * rng.standard_normal(250)
    assert np.all(fit_rf_importances(x, y, **RF_KW) >= 0.0)
    assert np.all(fit_gbdt_importances(x, y, **GBDT_KW) >= 0.0)


def test_constant_label_returns_zero_importance():
    """退化输入：常数 y → 全零重要度（上层回落等权），且不抛异常（评审 P1-3）。"""
    x = np.random.default_rng(3).standard_normal((120, 3))
    y = np.full(120, 5.0)
    assert np.array_equal(fit_rf_importances(x, y, **RF_KW), np.zeros(3))
    assert np.array_equal(fit_gbdt_importances(x, y, **GBDT_KW), np.zeros(3))


def test_zero_variance_feature_does_not_raise():
    """退化列：常数特征恒不参与分裂（重要度 0），其余特征正常，不抛异常（评审 P1-3）。"""
    rng = np.random.default_rng(4)
    x = rng.standard_normal((200, 3))
    x[:, 0] = 1.0
    y = 2.0 * x[:, 1] + 0.01 * rng.standard_normal(200)
    importance = fit_rf_importances(x, y, **RF_KW)
    assert importance[0] == 0.0
    assert int(np.argmax(importance)) == 1


def test_builtin_importances_are_deterministic():
    """同输入两次拟合重要度逐位相等（固定 seed + tie-break 确定性，评审 P2-4）。"""
    rng = np.random.default_rng(5)
    x = rng.standard_normal((250, 5))
    y = x[:, 0] - x[:, 3] + 0.05 * rng.standard_normal(250)
    assert np.array_equal(fit_rf_importances(x, y, **RF_KW), fit_rf_importances(x, y, **RF_KW))
    assert np.array_equal(fit_gbdt_importances(x, y, **GBDT_KW), fit_gbdt_importances(x, y, **GBDT_KW))


def test_row_subsample_is_deterministic_and_bounded():
    """行数 > BUILTIN_MAX_TRAIN_ROWS 时确定性下采样：两次拟合相等、信息特征仍可识别、合理时限内返回（评审 P1-4）。"""
    rng = np.random.default_rng(7)
    n = BUILTIN_MAX_TRAIN_ROWS + 1500
    x = rng.standard_normal((n, 3))
    y = x[:, 1] + 0.1 * rng.standard_normal(n)
    a = fit_rf_importances(x, y, n_estimators=10, max_depth=5, min_samples_leaf=5, seed=0)
    b = fit_rf_importances(x, y, n_estimators=10, max_depth=5, min_samples_leaf=5, seed=0)
    assert np.array_equal(a, b)
    assert int(np.argmax(a)) == 1


def test_row_count_mismatch_raises_value_error():
    """契约违例（X/y 行数不一致）抛 ValueError，由 service 端统一收敛 400（评审 P1-3）。"""
    x = np.zeros((10, 2))
    y = np.zeros(9)
    try:
        fit_rf_importances(x, y, n_estimators=5, max_depth=3, min_samples_leaf=2, seed=0)
        assert False, "行数不一致应抛 ValueError"
    except ValueError:
        pass


def test_resolve_tree_params_clamps_to_caps():
    """config 覆盖默认后收敛到硬上限（防纯 numpy 算力爆炸）。"""
    from app.engine.factors.tree_ensemble import GBDT_DEFAULTS, MAX_DEPTH_CAP, MAX_N_ESTIMATORS, RF_DEFAULTS

    params = resolve_tree_params({"nEstimators": 9999, "maxDepth": 999}, RF_DEFAULTS)
    assert params["n_estimators"] == MAX_N_ESTIMATORS
    assert params["max_depth"] == MAX_DEPTH_CAP
    assert params["min_samples_leaf"] == RF_DEFAULTS["min_samples_leaf"]
    defaults = resolve_tree_params({}, GBDT_DEFAULTS)
    assert defaults["n_estimators"] == GBDT_DEFAULTS["n_estimators"]
    assert defaults["max_depth"] == GBDT_DEFAULTS["max_depth"]
