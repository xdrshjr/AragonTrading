"""因子权重训练（training.py）—— 可插拔训练器注册表 + 能力探测 (spec §2.5)。

核心训练器 ``linear``（numpy 岭回归，默认）/ ``equal`` / ``ic_weight`` **恒可用**（仅 numpy/pandas）；
``random_forest`` / ``xgboost`` 为**可选**训练器，运行时用 ``importlib.util.find_spec`` 探测，缺库即如实
返回 ``available=False`` + 安装提示，**绝不假权重、绝不静默降级**（C1）。时序切分（默认前 70% 训练）
**绝不打乱**（杜绝未来泄漏）；同时回样本内 / 外复合得分 IC，UI 据此提示过拟合风险。

归一化契约（评审 C-P0-1）：``build_factor_matrix`` 施加与打分端 ``compose_score`` **逐字一致**的
因果滚动 z-score（同 ``norm_window``、同 clip），权重在该变换后的特征上学习。
"""
from __future__ import annotations

import importlib.util
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .evaluation import _pearson, _spearman, forward_return
from .scoring import DEFAULT_CLIP, DEFAULT_NORM_WINDOW, causal_zscore, directional_values, member_names

MIN_TRAIN_SAMPLES = 30   # 对齐 dropna 后的最小样本数；不足显式报错而非静默（R9）
DEFAULT_SPLIT = 0.7
DEFAULT_L2 = 1.0


@dataclass
class TrainResult:
    """训练产物（序列化进 factor_sets.model_json / metrics_json）。"""

    method: str
    weights: dict[str, float]
    normStats: dict
    metrics: dict
    available: bool
    trainerVersion: str | None = None
    note: str = ""


@dataclass(frozen=True)
class Trainer:
    """单个训练器：能力探测 + 拟合（返回未归一化的原始权重，符号已含在方向化特征里）。"""

    fit: Callable[[np.ndarray, np.ndarray, dict], np.ndarray]
    probe_module: str | None = None   # None=恒可用；否则 find_spec 探测该模块

    def available(self) -> bool:
        if self.probe_module is None:
            return True
        return importlib.util.find_spec(self.probe_module) is not None


# ============================ 训练数据构建（归一化契约） ============================
def build_factor_matrix(df: pd.DataFrame, members: list[dict], *,
                        norm_window: int = DEFAULT_NORM_WINDOW, bar: str = "",
                        clip: float = DEFAULT_CLIP) -> tuple[np.ndarray, list[str], dict]:
    """逐成员 ``compute_factor`` → 方向化 → **与打分端完全相同的因果滚动 z-score** → 矩阵 X[n,k]。

    Returns:
        ``(X, names, normStats)``：X 含暖机 NaN 行（对齐由 ``train_weights`` 统一 dropna）；
        ``normStats`` 仅记录契约参数 ``{normWindow, clip}``，**不**存固定 mean/std（评审 C-P0-1）。
    """
    work = df.reset_index(drop=True)
    names = member_names(members)
    columns = [
        causal_zscore(directional_values(work, member, bar=bar), norm_window, clip).to_numpy(dtype=float)
        for member in members
    ]
    matrix = np.column_stack(columns) if columns else np.empty((len(work), 0))
    return matrix, names, {"normWindow": int(norm_window), "clip": float(clip)}


def forward_return_label(df: pd.DataFrame, horizon: int) -> np.ndarray:
    """因果前瞻收益标签 ``y[i]=c[i+horizon]/c[i]-1``，尾部 horizon 行为 NaN（与 X 对齐时裁掉）。"""
    return forward_return(df.reset_index(drop=True)["c"].to_numpy(dtype=float), int(horizon))


def _aligned(matrix: np.ndarray, label: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """丢弃任一因子或标签为 NaN 的行（统一 dropna，绝不插值 C1）。"""
    if matrix.size == 0:
        return matrix, label
    mask = np.isfinite(matrix).all(axis=1) & np.isfinite(label)
    return matrix[mask], label[mask]


# ============================ 训练器实现 ============================
def _fit_equal(x_train: np.ndarray, y_train: np.ndarray, config: dict) -> np.ndarray:
    """等权基线（恒可用，零训练）。"""
    _ = (y_train, config)
    k = x_train.shape[1]
    return np.ones(k, dtype=float)


def _fit_ic_weight(x_train: np.ndarray, y_train: np.ndarray, config: dict) -> np.ndarray:
    """权重 ∝ 各方向化因子与标签的 RankIC（恒可用）；全零则回落等权。"""
    _ = config
    ics = np.asarray([(_spearman(x_train[:, j], y_train) or 0.0) for j in range(x_train.shape[1])])
    return ics if np.any(ics != 0.0) else np.ones(x_train.shape[1], dtype=float)


def _fit_linear(x_train: np.ndarray, y_train: np.ndarray, config: dict) -> np.ndarray:
    """岭回归闭式解 ``w=(XᵀX+λI)⁻¹Xᵀy``（恒可用、默认）。"""
    lam = float(config.get("l2", DEFAULT_L2))
    k = x_train.shape[1]
    gram = x_train.T @ x_train + lam * np.eye(k)
    return np.linalg.solve(gram, x_train.T @ y_train)


def _fit_random_forest(x_train: np.ndarray, y_train: np.ndarray, config: dict) -> np.ndarray:
    """sklearn 随机森林 → feature_importances_ 作权重（可选探测）。"""
    from sklearn.ensemble import RandomForestRegressor

    model = RandomForestRegressor(
        n_estimators=int(config.get("nEstimators", 200)),
        max_depth=config.get("maxDepth"), random_state=0, n_jobs=-1)
    model.fit(x_train, y_train)
    return np.asarray(model.feature_importances_, dtype=float)


def _fit_xgboost(x_train: np.ndarray, y_train: np.ndarray, config: dict) -> np.ndarray:
    """xgboost 回归 → gain 重要度作权重（可选探测）。"""
    from xgboost import XGBRegressor

    model = XGBRegressor(
        n_estimators=int(config.get("nEstimators", 200)),
        max_depth=int(config.get("maxDepth", 3)), learning_rate=0.1,
        random_state=0, verbosity=0)
    model.fit(x_train, y_train)
    return np.asarray(model.feature_importances_, dtype=float)


TRAINERS: dict[str, Trainer] = {
    "equal": Trainer(_fit_equal),
    "ic_weight": Trainer(_fit_ic_weight),
    "linear": Trainer(_fit_linear),
    "random_forest": Trainer(_fit_random_forest, probe_module="sklearn"),
    "xgboost": Trainer(_fit_xgboost, probe_module="xgboost"),
}

_INSTALL_HINT = "训练器不可用：请安装可选依赖 requirements-ml.txt（pip install -r requirements-ml.txt）。"


def trainer_available(method: str) -> bool:
    trainer = TRAINERS.get(method)
    return bool(trainer and trainer.available())


def available_trainers() -> list[dict]:
    """训练器目录 ``[{method, available}]``（UI / 工具据此提示可选项）。"""
    return [{"method": name, "available": trainer.available()} for name, trainer in TRAINERS.items()]


def _normalize_weights(raw: np.ndarray, names: list[str]) -> dict[str, float]:
    """权重归一到 ``sum|w|=1``（全零回落等权），映射回成员名。"""
    total = float(np.sum(np.abs(raw)))
    norm = raw / total if total > 0 else np.ones(len(raw)) / max(len(raw), 1)
    return {name: round(float(w), 6) for name, w in zip(names, norm)}


def _split_metrics(matrix: np.ndarray, label: np.ndarray, cut: int,
                   weight_vec: np.ndarray) -> dict:
    """样本内 / 外复合得分 IC（RankIC）+ 线性可解释方差 r2（Pearson²）。"""
    composite = matrix @ weight_vec
    return {
        "icIn": _round(_spearman(composite[:cut], label[:cut])),
        "icOut": _round(_spearman(composite[cut:], label[cut:])),
        "r2In": _r2(composite[:cut], label[:cut]),
        "r2Out": _r2(composite[cut:], label[cut:]),
        "nTrain": int(cut), "nValid": int(len(label) - cut),
    }


def _r2(pred: np.ndarray, actual: np.ndarray) -> float | None:
    corr = _pearson(pred, actual) if len(pred) >= 2 else None
    return None if corr is None else round(corr * corr, 6)


def _round(value: float | None) -> float | None:
    return None if value is None else round(float(value), 6)


def train_weights(matrix: np.ndarray, label: np.ndarray, names: list[str], *,
                  method: str = "linear", config: dict | None = None,
                  norm_stats: dict | None = None) -> TrainResult:
    """按 ``method`` 取训练器拟合权重（时序切分不打乱）；缺库即 available=False，绝不假权重。

    Args:
        matrix / label / names: ``build_factor_matrix`` 与 ``forward_return_label`` 产物。
        method: equal | ic_weight | linear | random_forest | xgboost。
        config: 训练配置（split / l2 / nEstimators 等）。
        norm_stats: 归一化契约参数（透传进结果，供打分端取同窗口）。

    Returns:
        ``TrainResult``（不可用即 ``available=False`` + 安装提示）。

    Raises:
        ValueError: 未知 method / dropna 后样本不足（显式报错，绝不静默）。
    """
    config = config or {}
    trainer = TRAINERS.get(method)
    if trainer is None:
        raise ValueError(f"未知训练方法：{method}")
    norm = norm_stats or {"normWindow": DEFAULT_NORM_WINDOW, "clip": DEFAULT_CLIP}
    if not trainer.available():
        return TrainResult(method=method, weights={}, normStats=norm, metrics={},
                           available=False, note=_INSTALL_HINT)
    aligned_x, aligned_y = _aligned(matrix, label)
    if len(aligned_y) < MIN_TRAIN_SAMPLES:
        raise ValueError(f"训练样本不足：dropna 后仅 {len(aligned_y)} 行（需 ≥{MIN_TRAIN_SAMPLES}）")
    split = float(config.get("split", DEFAULT_SPLIT))
    cut = max(1, min(len(aligned_y) - 1, int(np.floor(len(aligned_y) * split))))
    raw = trainer.fit(aligned_x[:cut], aligned_y[:cut], config)
    weights = _normalize_weights(np.asarray(raw, dtype=float), names)
    weight_vec = np.asarray([weights[name] for name in names], dtype=float)
    metrics = _split_metrics(aligned_x, aligned_y, cut, weight_vec)
    return TrainResult(method=method, weights=weights, normStats=norm, metrics=metrics,
                       available=True, trainerVersion=_trainer_version(method))


def _trainer_version(method: str) -> str | None:
    """可选训练器的库版本（用于审计 / 复现；核心训练器返回 None）。"""
    module = {"random_forest": "sklearn", "xgboost": "xgboost"}.get(method)
    if module is None:
        return None
    try:
        return importlib.import_module(module).__version__
    except (ImportError, AttributeError):
        return None
