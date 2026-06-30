"""因子权重训练（training.py）—— 有序多后端训练器注册表 + 能力探测 (spec §2.4/§2.5)。

核心训练器 ``linear``（numpy 岭回归，默认）/ ``equal`` / ``ic_weight`` **恒可用**（仅 numpy/pandas，
``kind="native"``）。``random_forest`` / ``xgboost`` 升级为**双后端**：真实 sklearn/xgboost 在场则优先
（``kind="library"``，高保真 + 加速），缺库则回落内置纯 numpy 树集成（``kind="builtin"``，恒可用兜底）。
两后端**复用单源默认超参**（``RF_DEFAULTS`` / ``GBDT_DEFAULTS``），令「装库与否」只切换保真度/速度、
不切换模型粗形态（评审 P1-2）；实际生效后端经 ``trainerVersion`` 如实回报前端，**绝不假权重、绝不静默
降级**（C1）。时序切分（默认前 70% 训练）**绝不打乱**（杜绝未来泄漏）；同时回样本内 / 外复合得分 IC。

归一化契约（评审 C-P0-1）：``build_factor_matrix`` 施加与打分端 ``compose_score`` **逐字一致**的
因果滚动 z-score（同 ``norm_window``、同 clip），权重在该变换后的特征上学习。
"""
from __future__ import annotations

import importlib.util
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

from .evaluation import _pearson, _spearman, forward_return
from .scoring import DEFAULT_CLIP, DEFAULT_NORM_WINDOW, causal_zscore, directional_values, member_names
from .tree_ensemble import (
    BUILTIN_GBDT_VERSION,
    BUILTIN_RF_VERSION,
    BUILTIN_SEED,
    GBDT_DEFAULTS,
    RF_DEFAULTS,
    fit_gbdt_importances,
    fit_rf_importances,
    resolve_tree_params,
)

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


Kind = Literal["native", "library", "builtin"]  # 评审 P1-1：显式标签，消除 native↔builtin 派生歧义


@dataclass(frozen=True)
class Backend:
    """单个训练后端：能力探测 + 拟合（返回未归一化原始权重，符号已含在方向化特征里）+ 版本标签。"""

    fit: Callable[[np.ndarray, np.ndarray, dict], np.ndarray]
    kind: Kind                           # native=核心训练器；library=sklearn/xgboost；builtin=内置树兜底
    probe_module: str | None             # None → 无需探测、恒可用（native / builtin）
    version: Callable[[], "str | None"]  # 后端版本标签（native 恒 None）

    def available(self) -> bool:
        return self.probe_module is None or importlib.util.find_spec(self.probe_module) is not None


@dataclass(frozen=True)
class Trainer:
    """有序多后端训练器：按序取首个可用后端（内置 / 核心兜底恒可用，故 rf/xgb 永真）。"""

    backends: tuple[Backend, ...]

    def available(self) -> bool:
        return any(b.available() for b in self.backends)

    def resolve(self) -> Backend:
        """取首个 available 后端（库优先、内置兜底；rf/xgb 至少命中 builtin）。"""
        return next(b for b in self.backends if b.available())


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


def _fit_rf_sklearn(x_train: np.ndarray, y_train: np.ndarray, config: dict) -> np.ndarray:
    """sklearn 随机森林 → feature_importances_ 作权重（库后端，复用单源默认 RF_DEFAULTS，评审 P1-2）。"""
    from sklearn.ensemble import RandomForestRegressor

    params = resolve_tree_params(config, RF_DEFAULTS)
    model = RandomForestRegressor(
        n_estimators=params["n_estimators"], max_depth=params["max_depth"],
        min_samples_leaf=params["min_samples_leaf"], random_state=0, n_jobs=-1)
    model.fit(x_train, y_train)
    return np.asarray(model.feature_importances_, dtype=float)


def _fit_xgb_native(x_train: np.ndarray, y_train: np.ndarray, config: dict) -> np.ndarray:
    """xgboost 回归 → gain 重要度作权重（库后端，复用 GBDT_DEFAULTS + 显式 importance_type="gain"，评审 P1-2）。"""
    from xgboost import XGBRegressor

    params = resolve_tree_params(config, GBDT_DEFAULTS)
    model = XGBRegressor(
        n_estimators=params["n_estimators"], max_depth=params["max_depth"],
        learning_rate=GBDT_DEFAULTS["learning_rate"], min_child_weight=params["min_samples_leaf"],
        importance_type="gain", random_state=0, verbosity=0)
    model.fit(x_train, y_train)
    return np.asarray(model.feature_importances_, dtype=float)


def _fit_rf_builtin(x_train: np.ndarray, y_train: np.ndarray, config: dict) -> np.ndarray:
    """内置纯 numpy 随机森林重要度（恒可用兜底；缺 sklearn 时生效，评审 §2.3）。"""
    params = resolve_tree_params(config, RF_DEFAULTS)
    return fit_rf_importances(
        x_train, y_train, n_estimators=params["n_estimators"], max_depth=params["max_depth"],
        min_samples_leaf=params["min_samples_leaf"], seed=BUILTIN_SEED)


def _fit_gbdt_builtin(x_train: np.ndarray, y_train: np.ndarray, config: dict) -> np.ndarray:
    """内置纯 numpy 梯度提升 gain 重要度（恒可用兜底；缺 xgboost 时生效，评审 §2.3）。"""
    params = resolve_tree_params(config, GBDT_DEFAULTS)
    return fit_gbdt_importances(
        x_train, y_train, n_estimators=params["n_estimators"], max_depth=params["max_depth"],
        learning_rate=GBDT_DEFAULTS["learning_rate"], min_samples_leaf=params["min_samples_leaf"],
        seed=BUILTIN_SEED)


def _library_version(module: str) -> str:
    """库后端版本标签（如 ``"sklearn 1.6.1"``）；导入失败回退裸模块名（resolve 后调用，通常已在场）。"""
    try:
        return f"{module} {importlib.import_module(module).__version__}"
    except (ImportError, AttributeError):
        return module


def _native_backend(fit: Callable[[np.ndarray, np.ndarray, dict], np.ndarray]) -> Backend:
    """核心训练器单后端工厂：``kind="native"``、恒可用、版本恒 ``None``（对外行为字节级不变）。"""
    return Backend(fit=fit, kind="native", probe_module=None, version=lambda: None)


TRAINERS: dict[str, Trainer] = {
    "equal": Trainer((_native_backend(_fit_equal),)),
    "ic_weight": Trainer((_native_backend(_fit_ic_weight),)),
    "linear": Trainer((_native_backend(_fit_linear),)),
    "random_forest": Trainer((
        Backend(_fit_rf_sklearn, "library", "sklearn", lambda: _library_version("sklearn")),
        Backend(_fit_rf_builtin, "builtin", None, lambda: BUILTIN_RF_VERSION),
    )),
    "xgboost": Trainer((
        Backend(_fit_xgb_native, "library", "xgboost", lambda: _library_version("xgboost")),
        Backend(_fit_gbdt_builtin, "builtin", None, lambda: BUILTIN_GBDT_VERSION),
    )),
}

_INSTALL_HINT = "训练器不可用：请安装可选依赖 requirements-ml.txt（pip install -r requirements-ml.txt）。"
_BUILTIN_NOTE = "使用内置实现；安装 requirements-ml.txt（pip install -r requirements-ml.txt）可启用 sklearn/xgboost 加速。"


def trainer_available(method: str) -> bool:
    trainer = TRAINERS.get(method)
    return bool(trainer and trainer.available())


def available_trainers() -> list[dict]:
    """训练器目录 ``[{method, available, backend}]``：``backend=resolve().kind``（rf/xgb 因内置兜底恒可用）。"""
    out: list[dict] = []
    for name, trainer in TRAINERS.items():
        available = trainer.available()
        out.append({"method": name, "available": available,
                    "backend": trainer.resolve().kind if available else None})
    return out


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
    """按 ``method`` 取训练器首个可用后端拟合权重（时序切分不打乱）；后端经 ``trainerVersion`` 如实回报。

    rf/xgb 因内置树兜底**恒可用**：库在场走库（高保真），缺库走内置实现（``note`` 提示、``trainerVersion``
    为 ``builtin-*``），绝不假权重、绝不静默降级（C1）。``available=False`` 分支保留作前向兼容（现实中
    rf/xgb 永真；仅未来若新增「纯探测无兜底」训练器才会触达）。

    Args:
        matrix / label / names: ``build_factor_matrix`` 与 ``forward_return_label`` 产物。
        method: equal | ic_weight | linear | random_forest | xgboost。
        config: 训练配置（split / l2 / nEstimators / maxDepth 等）。
        norm_stats: 归一化契约参数（透传进结果，供打分端取同窗口）。

    Returns:
        ``TrainResult``：``trainerVersion`` 取实际后端（库版本 / ``builtin-*`` / ``None``）；内置后端附 ``note``。

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
    backend = trainer.resolve()
    raw = backend.fit(aligned_x[:cut], aligned_y[:cut], config)
    weights = _normalize_weights(np.asarray(raw, dtype=float), names)
    weight_vec = np.asarray([weights[name] for name in names], dtype=float)
    metrics = _split_metrics(aligned_x, aligned_y, cut, weight_vec)
    note = _BUILTIN_NOTE if backend.kind == "builtin" else ""
    return TrainResult(method=method, weights=weights, normStats=norm, metrics=metrics,
                       available=True, trainerVersion=backend.version(), note=note)
