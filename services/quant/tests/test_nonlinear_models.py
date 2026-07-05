"""内置非线性 / 稀疏线性拟合器单测（nonlinear.py）：确定性、非负、退化回落、非线性可辨、行封顶、留一。

纯 numpy 兜底路径（不依赖 sklearn），与 test_factor_training.py 的双后端集成测试互补（weight-model-nonlinear-trainers §七）。
"""
from __future__ import annotations

import numpy as np
import pytest

from app.engine.factors import nonlinear as nl
from app.engine.factors.tree_ensemble import ET_DEFAULTS, fit_extra_trees_importances

# 逐因子重要度类拟合器（返非负重要度）；elastic_net 单列（返带符号系数）。
_IMPORTANCE_FITTERS = {
    "mlp": nl.fit_mlp_permutation_importances,
    "kernel_ridge": nl.fit_kernel_ridge_permutation_importances,
    "knn": nl.fit_knn_permutation_importances,
    "mutual_info": nl.fit_mutual_info_importances,
}
_ALL_FITTERS = {**_IMPORTANCE_FITTERS, "elastic_net": nl.fit_elastic_net_coefficients}


def _ushape(n: int = 500, seed: int = 0):
    """非单调（U 型）标签：仅 x0（``y≈x0²``）非线性相关，x1/x2 为噪声——线性相关近零，非线性可辨。"""
    rng = np.random.default_rng(seed)
    x0 = rng.uniform(-2, 2, n)
    x1 = rng.standard_normal(n)
    x2 = rng.standard_normal(n)
    return np.column_stack([x0, x1, x2]), x0**2 + rng.standard_normal(n) * 0.1


def _norm(v: np.ndarray) -> np.ndarray:
    v = np.clip(np.asarray(v, dtype=float), 0.0, None)
    total = float(v.sum())
    return v / total if total > 0 else v


@pytest.mark.parametrize("name", list(_ALL_FITTERS))
def test_fitter_is_deterministic(name):
    """同输入两次拟合逐值相等（固定 seed，跨平台可复现，评审 P1-1/P2-4）。"""
    x, y = _ushape()
    assert np.array_equal(_ALL_FITTERS[name](x, y), _ALL_FITTERS[name](x, y))


@pytest.mark.parametrize("name", list(_IMPORTANCE_FITTERS))
def test_importance_is_non_negative(name):
    """置换 / 互信息类重要度非负（clip(min=0)）。"""
    x, y = _ushape()
    assert np.all(_IMPORTANCE_FITTERS[name](x, y) >= 0.0)


@pytest.mark.parametrize("name", list(_ALL_FITTERS))
def test_degenerate_constant_label_returns_zeros(name):
    """常数 y（零方差）→ 返全零（由上层回落等权，绝不抛异常，评审 P1-3）。"""
    x = np.random.default_rng(2).standard_normal((120, 3))
    y = np.full(120, 3.14)
    assert np.allclose(_ALL_FITTERS[name](x, y), 0.0)


@pytest.mark.parametrize("name", list(_ALL_FITTERS))
def test_row_mismatch_raises_value_error(name):
    """X/y 行数不一致（输入契约违例）显式抛 ValueError（service 层收敛 400）。"""
    with pytest.raises(ValueError):
        _ALL_FITTERS[name](np.zeros((10, 3)), np.zeros(9))


@pytest.mark.parametrize(
    "name,fitter",
    [("mlp", nl.fit_mlp_permutation_importances),
     ("extra_trees", None),
     ("kernel_ridge", nl.fit_kernel_ridge_permutation_importances),
     ("mutual_info", nl.fit_mutual_info_importances)],
)
def test_nonlinear_captures_ushape(name, fitter):
    """非单调 U 型信号：非线性模型把 x0 排为最重要且显著高于噪声因子（linear 岭回归系数无法主导）。"""
    x, y = _ushape()
    if name == "extra_trees":
        imp = fit_extra_trees_importances(
            x, y, n_estimators=ET_DEFAULTS["n_estimators"], max_depth=ET_DEFAULTS["max_depth"],
            min_samples_leaf=ET_DEFAULTS["min_samples_leaf"])
    else:
        imp = fitter(x, y)
    w = _norm(imp)
    assert int(np.argmax(w)) == 0                 # x0（U 型因子）最重要
    assert w[0] > w[1] and w[0] > w[2]            # 显著高于两个噪声因子
    # 对比：linear 岭回归对同一 U 型信号无法把 x0 主导得同样干净（佐证「确实捕捉了非线性」）。
    from app.engine.factors.training import _fit_linear
    lin = _norm(np.abs(_fit_linear(x, y, {})))
    assert w[0] > lin[0]


def test_elastic_net_preserves_sign():
    """弹性网络产带符号稀疏系数：负相关特征 → 负系数（_normalize_weights 保号，评审 §3.4-⑥）。"""
    rng = np.random.default_rng(1)
    n = 400
    x0 = rng.standard_normal(n)
    x1 = rng.standard_normal(n)
    x = np.column_stack([x0, x1])
    y = -2.0 * x0 + rng.standard_normal(n) * 0.1
    coef = nl.fit_elastic_net_coefficients(x, y)
    assert coef[0] < 0.0            # 负相关 → 负系数
    assert abs(coef[0]) > abs(coef[1])


def test_permutation_importance_ranks_relevant_feature():
    """共享置换重要度助手：仅 x1 相关的真预测函数 → x1 重要度最大且非负。"""
    rng = np.random.default_rng(0)
    x = rng.standard_normal((300, 3))
    y = 3.0 * x[:, 1]
    imp = nl.permutation_importance(lambda xq: 3.0 * xq[:, 1], x, y)
    assert np.all(imp >= 0.0)
    assert int(np.argmax(imp)) == 1


def test_knn_clamps_k_on_small_sample():
    """knn 内置后端把 k 钳到 min(k, n−1) 防小样本越界（评审 P2-5）。"""
    x, y = _ushape(n=10)          # n=10 < 默认 k=15
    imp = nl.fit_knn_permutation_importances(x, y, k=15)
    assert imp.shape == (3,)
    assert np.all(imp >= 0.0)


def test_row_cap_is_deterministic_and_bounds_rows():
    """行封顶：> *_MAX_TRAIN_ROWS 的输入被确定性无放回下采样到上界（评审 P1-2）。"""
    x, y = _ushape(n=200)
    big_x = np.tile(x, (30, 1))   # 6000 行 > 4000
    big_y = np.tile(y, 30)
    capped_x, capped_y = nl.cap_train_rows(big_x, big_y, nl.MI_MAX_TRAIN_ROWS)
    assert capped_x.shape[0] == nl.MI_MAX_TRAIN_ROWS
    assert capped_y.shape[0] == nl.MI_MAX_TRAIN_ROWS
    again_x, _ = nl.cap_train_rows(big_x, big_y, nl.MI_MAX_TRAIN_ROWS)
    assert np.array_equal(capped_x, again_x)


def test_kernel_ridge_tighter_row_cap():
    """kernel_ridge O(n³) 行封顶收紧到 1200（远小于通用 4000，评审 P1-2 成本护栏）。"""
    assert nl.KRR_MAX_TRAIN_ROWS == 1200
    assert nl.KNN_MAX_TRAIN_ROWS == 2500
    assert nl.KRR_MAX_TRAIN_ROWS < nl.MLP_MAX_TRAIN_ROWS
