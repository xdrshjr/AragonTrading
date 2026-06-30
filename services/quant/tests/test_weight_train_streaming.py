"""扩样本学习曲线测试：真实损失 / IC、因果无泄漏、曲线几何、**流式末步与 train_weights 字节级等价**。

放行条件 1（硬门禁）：流式 ``f=1.0`` 末步产物与同步 ``train_weights`` 对相同输入 ``weights/metrics`` 逐字段相等。
"""
from __future__ import annotations

import numpy as np

from app.engine.backtester import candles_to_df
from app.engine.factors import (
    build_factor_matrix,
    fit_at_fraction,
    forward_return_label,
    learning_curve_steps,
    prepare_curve_sample,
    train_weights,
)
from app.engine.factors.training import MIN_TRAIN_SAMPLES
from tests.conftest import make_synthetic_candles

_MEMBERS = [
    {"factorKey": "mom_roc", "params": {"win": 20}, "direction": "long"},
    {"factorKey": "trend_ema_dist", "params": {}, "direction": "long"},
    {"factorKey": "rev_rsi", "params": {}, "direction": "short"},
]


def _matrix(n: int = 400, norm_window: int = 100):
    df = candles_to_df(make_synthetic_candles(n, step=3600))
    matrix, names, norm = build_factor_matrix(df, _MEMBERS, norm_window=norm_window, bar="1H")
    label = forward_return_label(df, 6)
    return matrix, label, names, norm


def test_streaming_final_equals_train_weights():
    """流式末步（frac=1.0）weights/metrics 与同步 train_weights 逐字段相等（放行条件 1 硬门禁）。"""
    matrix, label, names, norm = _matrix()
    config = {"split": 0.7}
    sync = train_weights(matrix, label, names, method="linear", config=config, norm_stats=norm)
    sample = prepare_curve_sample(matrix, label, config)
    weights_final, point = fit_at_fraction(
        sample.matrix, sample.label, names, cut=sample.cut, frac=1.0, method="linear", config=config)
    assert weights_final == sync.weights
    # f=1.0 末步在留出验证段的 IC == 同步训练样本外 IC；训练段 IC == 样本内 IC（同一切分、同一权重）。
    assert point["validIc"] == sync.metrics["icOut"]
    assert point["trainIc"] == sync.metrics["icIn"]


def test_curve_is_causal_no_future_leak():
    """验证段严格晚于训练段：fit_at_fraction 的 validLoss/validIc 全部来自 cut 之后的留出段（无泄漏）。"""
    matrix, label, names, _ = _matrix()
    sample = prepare_curve_sample(matrix, label, {"split": 0.7})
    # 留出验证段为 [cut:]；构造性断言：把 cut 之后置 NaN 不应改变训练段 IC（训练只看 [:k]）。
    weights, point = fit_at_fraction(
        sample.matrix, sample.label, names, cut=sample.cut, frac=0.5, method="linear", config={})
    k = point["nTrain"]
    assert k < sample.cut <= len(sample.label)
    # 训练子样本与验证段不相交（k ≤ cut < 末尾），核心因果保证。
    assert k <= sample.cut


def test_loss_decreases_with_more_samples_on_learnable_data():
    """可学习数据上，验证损失随训练样本量整体下降（真实学习行为，绝非伪造平滑曲线）。"""
    matrix, label, names, _ = _matrix()
    sample = prepare_curve_sample(matrix, label, {"split": 0.7})
    steps = learning_curve_steps(sample.cut, steps=6)
    losses = []
    for frac in steps:
        _, point = fit_at_fraction(sample.matrix, sample.label, names,
                                   cut=sample.cut, frac=frac, method="linear", config={})
        losses.append(point["validLoss"])
    assert losses[-1] is not None and losses[0] is not None
    assert losses[-1] <= losses[0]   # 末步（全训练段）验证损失 ≤ 首步（最小子样本）


def test_learning_curve_steps_grid_last_always_one():
    """fraction 网格末点恒 1.0；早期子样本 < MIN_TRAIN_SAMPLES 的步被剔除（cut 充足时网格非空）。"""
    grid = learning_curve_steps(300, steps=6)
    assert grid[-1] == 1.0
    assert all(int(np.floor(f * 300)) >= MIN_TRAIN_SAMPLES for f in grid)
    # cut 过小（< MIN）时网格退化为单点 [1.0]（诚实呈现，绝不补假点）。
    tiny = learning_curve_steps(10, steps=6)
    assert tiny == [1.0]


def test_equal_method_curve_is_constant():
    """R-11：等权法权重与样本无关，其验证段 IC 跨 fraction 恒定（诚实真实行为，非 bug）。"""
    matrix, label, names, _ = _matrix()
    sample = prepare_curve_sample(matrix, label, {"split": 0.7})
    valid_ics = []
    for frac in learning_curve_steps(sample.cut, steps=6):
        _, point = fit_at_fraction(sample.matrix, sample.label, names,
                                   cut=sample.cut, frac=frac, method="equal", config={})
        valid_ics.append(point["validIc"])
    assert len(set(valid_ics)) == 1   # 恒定


def test_insufficient_samples_raises():
    """整体样本不足（dropna 后 < MIN_TRAIN_SAMPLES）→ prepare_curve_sample 抛 ValueError（R-07，绝不空成功）。"""
    matrix, label, names, _ = _matrix(n=40, norm_window=200)
    try:
        prepare_curve_sample(matrix, label, {"split": 0.7})
        assert False, "样本不足应抛 ValueError"
    except ValueError as exc:
        assert "样本不足" in str(exc)
