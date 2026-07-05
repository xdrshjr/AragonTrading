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
from .nonlinear import (
    BUILTIN_EN_VERSION,
    BUILTIN_KNN_VERSION,
    BUILTIN_KRR_VERSION,
    BUILTIN_MI_VERSION,
    BUILTIN_MLP_VERSION,
    EN_MAX_TRAIN_ROWS,
    KNN_MAX_TRAIN_ROWS,
    KRR_MAX_TRAIN_ROWS,
    MI_MAX_TRAIN_ROWS,
    MLP_MAX_TRAIN_ROWS,
    PERM_REPEATS,
    _rbf_gamma,
    cap_train_rows,
    fit_elastic_net_coefficients,
    fit_kernel_ridge_permutation_importances,
    fit_knn_permutation_importances,
    fit_mlp_permutation_importances,
    fit_mutual_info_importances,
    resolve_en_params,
    resolve_knn_params,
    resolve_krr_params,
    resolve_mi_params,
    resolve_mlp_params,
)
from .scoring import DEFAULT_CLIP, DEFAULT_NORM_WINDOW, causal_zscore, directional_values, member_names
from .tree_ensemble import (
    BUILTIN_ET_VERSION,
    BUILTIN_GBDT_VERSION,
    BUILTIN_MAX_TRAIN_ROWS,
    BUILTIN_RF_VERSION,
    BUILTIN_SEED,
    ET_DEFAULTS,
    GBDT_DEFAULTS,
    RF_DEFAULTS,
    fit_extra_trees_importances,
    fit_gbdt_importances,
    fit_rf_importances,
    resolve_tree_params,
)

MIN_TRAIN_SAMPLES = 30   # 对齐 dropna 后的最小样本数；不足显式报错而非静默（R9）
DEFAULT_SPLIT = 0.7


class InsufficientSamplesError(ValueError):
    """对齐 dropna 后样本不足（< ``MIN_TRAIN_SAMPLES``）的专用异常（weight-model-workspace-hardening H-B5/R-05）。

    继承 ``ValueError`` 保持既有调用方 ``except ValueError`` 行为不破坏；新增子类仅用于把「样本不足」与
    「越界 config 解析失败」等其它 ``ValueError`` 区分开，使流式作业能把前者精确映射为
    ``WEIGHT_TRAIN_INSUFFICIENT_SAMPLES``、后者照常落 ``WEIGHT_TRAIN_FAILED``（绝不误标）。
    """
DEFAULT_L2 = 1.0
DEFAULT_CURVE_STEPS = 6   # 扩样本学习曲线默认 fraction 网格步数（末点恒 1.0，weight-model-workspace R-10）
_CURVE_START_FRACTION = 0.2  # 网格起点（最早子样本占训练段比例）


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
class TrainSample:
    """对齐后的训练样本矩阵 + 时序切分点（``cut``=训练段长度）。

    供「权重 → 指标 → 结果」等价装配（``build_train_result``）与扩样本学习曲线（``fit_at_fraction``）共用，
    保证流式末步与同步 ``train_weights`` 对相同输入字节级等价（weight-model-workspace 放行条件 1）。
    """

    matrix: np.ndarray
    label: np.ndarray
    cut: int


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


# ---------------- 新增顶级训练器（5 非线性 + 1 稀疏线性，weight-model-nonlinear-trainers §3.2/§3.4） ----------------
# 成本护栏对齐两后端（评审 v2 P1-2）：每个 ``_fit_*_sklearn`` 入口先按各自 ``*_MAX_TRAIN_ROWS`` 行封顶
# （``training.py`` 框架层把整段 ``aligned_x[:cut]`` 原样传后端、并不封顶），令库/内置成本上界一致。
# 确定性对齐两后端（评审 v2 P1-1）：凡含随机性的 sklearn 估计器一律显式播种 ``random_state=BUILTIN_SEED``。
def _sklearn_perm_importance(model, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """无原生逐特征重要度的库模型（mlp/kernel_ridge/knn）走 sklearn 置换重要度；显式 ``random_state`` 保确定性。"""
    from sklearn.inspection import permutation_importance as sk_permutation_importance

    result = sk_permutation_importance(model, x, y, n_repeats=PERM_REPEATS, random_state=BUILTIN_SEED)
    return np.clip(np.asarray(result.importances_mean, dtype=float), 0.0, None)


def _fit_extra_trees_sklearn(x_train: np.ndarray, y_train: np.ndarray, config: dict) -> np.ndarray:
    """sklearn 极端随机树 → feature_importances_（库后端；行封顶 4000 对齐内置，评审 P1-2）。"""
    from sklearn.ensemble import ExtraTreesRegressor

    x, y = cap_train_rows(x_train, y_train, BUILTIN_MAX_TRAIN_ROWS)
    params = resolve_tree_params(config, ET_DEFAULTS)
    model = ExtraTreesRegressor(
        n_estimators=params["n_estimators"], max_depth=params["max_depth"],
        min_samples_leaf=params["min_samples_leaf"], random_state=0, n_jobs=-1)
    model.fit(x, y)
    return np.asarray(model.feature_importances_, dtype=float)


def _fit_extra_trees_builtin(x_train: np.ndarray, y_train: np.ndarray, config: dict) -> np.ndarray:
    """内置纯 numpy 极端随机树重要度（恒可用兜底；缺 sklearn 时生效）。"""
    params = resolve_tree_params(config, ET_DEFAULTS)
    return fit_extra_trees_importances(
        x_train, y_train, n_estimators=params["n_estimators"], max_depth=params["max_depth"],
        min_samples_leaf=params["min_samples_leaf"], seed=BUILTIN_SEED)


def _fit_mlp_sklearn(x_train: np.ndarray, y_train: np.ndarray, config: dict) -> np.ndarray:
    """sklearn MLP → 置换重要度（库后端；tanh 单隐层，行封顶 4000，random_state=0）。"""
    from sklearn.neural_network import MLPRegressor

    x, y = cap_train_rows(x_train, y_train, MLP_MAX_TRAIN_ROWS)
    params = resolve_mlp_params(config)
    model = MLPRegressor(
        hidden_layer_sizes=(params["hidden"],), activation="tanh", solver="adam",
        alpha=params["l2"], learning_rate_init=params["lr"], max_iter=params["epochs"], random_state=0)
    model.fit(x, y)
    return _sklearn_perm_importance(model, x, y)


def _fit_mlp_builtin(x_train: np.ndarray, y_train: np.ndarray, config: dict) -> np.ndarray:
    """内置纯 numpy MLP 置换重要度（恒可用兜底；缺 sklearn 时生效）。"""
    return fit_mlp_permutation_importances(x_train, y_train, seed=BUILTIN_SEED, **resolve_mlp_params(config))


def _fit_kernel_ridge_sklearn(x_train: np.ndarray, y_train: np.ndarray, config: dict) -> np.ndarray:
    """sklearn 核岭回归(RBF) → 置换重要度（库后端；O(n³) 行封顶收紧到 1200，评审 P1-2）。"""
    from sklearn.kernel_ridge import KernelRidge

    x, y = cap_train_rows(x_train, y_train, KRR_MAX_TRAIN_ROWS)
    params = resolve_krr_params(config)
    gamma = _rbf_gamma(x) if params["gamma"] is None else params["gamma"]   # 与内置同 scale 口径
    model = KernelRidge(kernel="rbf", alpha=params["alpha"], gamma=gamma)
    model.fit(x, y)
    return _sklearn_perm_importance(model, x, y)


def _fit_kernel_ridge_builtin(x_train: np.ndarray, y_train: np.ndarray, config: dict) -> np.ndarray:
    """内置纯 numpy 核岭回归置换重要度（恒可用兜底；缺 sklearn 时生效）。"""
    return fit_kernel_ridge_permutation_importances(
        x_train, y_train, seed=BUILTIN_SEED, **resolve_krr_params(config))


def _fit_knn_sklearn(x_train: np.ndarray, y_train: np.ndarray, config: dict) -> np.ndarray:
    """sklearn K 近邻回归 → 置换重要度（库后端；行封顶 2500，k 钳到 n−1，评审 P2-5）。"""
    from sklearn.neighbors import KNeighborsRegressor

    x, y = cap_train_rows(x_train, y_train, KNN_MAX_TRAIN_ROWS)
    params = resolve_knn_params(config)
    k = max(1, min(params["k"], x.shape[0] - 1))
    model = KNeighborsRegressor(n_neighbors=k)
    model.fit(x, y)
    return _sklearn_perm_importance(model, x, y)


def _fit_knn_builtin(x_train: np.ndarray, y_train: np.ndarray, config: dict) -> np.ndarray:
    """内置纯 numpy K 近邻留一置换重要度（恒可用兜底；缺 sklearn 时生效）。"""
    return fit_knn_permutation_importances(x_train, y_train, seed=BUILTIN_SEED, **resolve_knn_params(config))


def _fit_mutual_info_sklearn(x_train: np.ndarray, y_train: np.ndarray, config: dict) -> np.ndarray:
    """sklearn 互信息回归 → 逐因子 MI（库后端；**必须显式 random_state=0**，否则 kNN 型 MI 非确定性，评审 P1-1）。"""
    from sklearn.feature_selection import mutual_info_regression

    _ = config
    x, y = cap_train_rows(x_train, y_train, MI_MAX_TRAIN_ROWS)
    mi = mutual_info_regression(x, y, random_state=BUILTIN_SEED)
    return np.clip(np.asarray(mi, dtype=float), 0.0, None)


def _fit_mutual_info_builtin(x_train: np.ndarray, y_train: np.ndarray, config: dict) -> np.ndarray:
    """内置纯 numpy 直方图互信息（恒可用兜底；缺 sklearn 时生效；天然确定性）。"""
    return fit_mutual_info_importances(x_train, y_train, seed=BUILTIN_SEED, **resolve_mi_params(config))


def _fit_elastic_net_sklearn(x_train: np.ndarray, y_train: np.ndarray, config: dict) -> np.ndarray:
    """sklearn 弹性网络 → |coef|（库后端；cyclic 更新确定性，行封顶 4000）。返回带符号系数，_normalize_weights 保号。"""
    from sklearn.linear_model import ElasticNet

    x, y = cap_train_rows(x_train, y_train, EN_MAX_TRAIN_ROWS)
    params = resolve_en_params(config)
    model = ElasticNet(
        alpha=params["alpha"], l1_ratio=params["l1_ratio"],
        max_iter=params["max_iter"], tol=params["tol"], selection="cyclic")
    model.fit(x, y)
    return np.asarray(model.coef_, dtype=float)


def _fit_elastic_net_builtin(x_train: np.ndarray, y_train: np.ndarray, config: dict) -> np.ndarray:
    """内置纯 numpy 坐标下降弹性网络系数（恒可用兜底；缺 sklearn 时生效）。"""
    return fit_elastic_net_coefficients(x_train, y_train, seed=BUILTIN_SEED, **resolve_en_params(config))


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
    # 顶级 · 含非线性模型库（weight-model-nonlinear-trainers）：库后端全落已列 scikit-learn、均有纯 numpy 兜底，
    # 故目录中 available 恒 True（library 或 builtin），与 rf/xgb 现状一致。
    "extra_trees": Trainer((
        Backend(_fit_extra_trees_sklearn, "library", "sklearn", lambda: _library_version("sklearn")),
        Backend(_fit_extra_trees_builtin, "builtin", None, lambda: BUILTIN_ET_VERSION),
    )),
    "mlp": Trainer((
        Backend(_fit_mlp_sklearn, "library", "sklearn", lambda: _library_version("sklearn")),
        Backend(_fit_mlp_builtin, "builtin", None, lambda: BUILTIN_MLP_VERSION),
    )),
    "kernel_ridge": Trainer((
        Backend(_fit_kernel_ridge_sklearn, "library", "sklearn", lambda: _library_version("sklearn")),
        Backend(_fit_kernel_ridge_builtin, "builtin", None, lambda: BUILTIN_KRR_VERSION),
    )),
    "knn": Trainer((
        Backend(_fit_knn_sklearn, "library", "sklearn", lambda: _library_version("sklearn")),
        Backend(_fit_knn_builtin, "builtin", None, lambda: BUILTIN_KNN_VERSION),
    )),
    "mutual_info": Trainer((
        Backend(_fit_mutual_info_sklearn, "library", "sklearn", lambda: _library_version("sklearn")),
        Backend(_fit_mutual_info_builtin, "builtin", None, lambda: BUILTIN_MI_VERSION),
    )),
    "elastic_net": Trainer((
        Backend(_fit_elastic_net_sklearn, "library", "sklearn", lambda: _library_version("sklearn")),
        Backend(_fit_elastic_net_builtin, "builtin", None, lambda: BUILTIN_EN_VERSION),
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


def _train_cut(n: int, config: dict) -> int:
    """时序切分点 ``cut=max(1,min(n-1,floor(n*split)))``（默认 split=0.7，绝不打乱，杜绝未来泄漏）。

    单源钳制公式：``train_weights`` 与 ``fit_at_fraction`` 共用，保证 ``f=1.0`` 末步与同步训练字节级等价（R-09）。
    """
    split = float(config.get("split", DEFAULT_SPLIT))
    return max(1, min(n - 1, int(np.floor(n * split))))


def _standardized_mse(pred: np.ndarray, actual: np.ndarray) -> float | None:
    """标准化后均方误差 ``mse(zscore(pred), zscore(actual)) = 2*(1-pearson)``，落在 [0,4]。

    拟合越好越小（pearson→1 则 →0）；样本 <2 或任一退化（方差 0）→ ``None``（绝不假装为 0，R-07/C1）。
    """
    corr = _pearson(pred, actual)
    return None if corr is None else round(2.0 * (1.0 - corr), 6)


def build_train_result(method: str, weights: dict[str, float], names: list[str], *,
                       sample: TrainSample, norm_stats: dict,
                       trainer_version: str | None = None, note: str = "") -> TrainResult:
    """从已拟合权重装配 ``TrainResult``（权重 → 样本内 / 外 IC + r2 指标 → 结果）。

    ``train_weights`` 与流式训练「末步」共用本装配，保证两路径对相同 ``(weights, sample)`` 产出**逐字段相等**的
    ``metrics``（守护「不破坏既有契约」，weight-model-workspace 放行条件 1）。
    """
    weight_vec = np.asarray([weights[name] for name in names], dtype=float)
    metrics = _split_metrics(sample.matrix, sample.label, sample.cut, weight_vec)
    return TrainResult(method=method, weights=weights, normStats=norm_stats, metrics=metrics,
                       available=True, trainerVersion=trainer_version, note=note)


def prepare_curve_sample(matrix: np.ndarray, label: np.ndarray, config: dict) -> TrainSample:
    """对齐 dropna + 时序切分 → ``TrainSample``，供流式作业逐 fraction 复用同一样本（与 ``train_weights`` 同口径）。

    Raises:
        InsufficientSamplesError: dropna 后样本不足（< ``MIN_TRAIN_SAMPLES``），调用方据此判失败（绝不空成功 / 卡 running，R-07）。
    """
    aligned_x, aligned_y = _aligned(matrix, label)
    if len(aligned_y) < MIN_TRAIN_SAMPLES:
        raise InsufficientSamplesError(f"训练样本不足：对齐后仅 {len(aligned_y)} 行（需 ≥{MIN_TRAIN_SAMPLES}）")
    return TrainSample(aligned_x, aligned_y, _train_cut(len(aligned_y), config))


def learning_curve_steps(cut: int, *, steps: int = DEFAULT_CURVE_STEPS) -> list[float]:
    """扩样本学习曲线的 fraction 网格（在 [0.2,1.0] 线性取 ``steps`` 点，末点恒 1.0）。

    剔除子样本 ``int(f*cut) < MIN_TRAIN_SAMPLES`` 的早期步（样本太少损失 / IC 无统计意义），但**末步 1.0 恒保留**
    （= 规范训练段全量拟合，与 ``train_weights`` 等价）。``cut`` 自身 < MIN 时网格仅含 1.0（曲线退化为单点，
    诚实呈现「样本太少无法画学习曲线」，绝不补假点，R-07/R-10）。
    """
    count = max(2, int(steps))
    grid = [round(_CURVE_START_FRACTION + (1.0 - _CURVE_START_FRACTION) * i / (count - 1), 4)
            for i in range(count)]
    kept = [f for f in grid if int(np.floor(f * cut)) >= MIN_TRAIN_SAMPLES]
    if not kept or kept[-1] != 1.0:
        kept.append(1.0)
    return kept


def fit_at_fraction(matrix: np.ndarray, label: np.ndarray, names: list[str], *,
                    cut: int, frac: float, method: str, config: dict) -> tuple[dict[str, float], dict]:
    """在训练段前 ``int(frac*cut)`` 行上拟合权重，并在留出验证段上度量真实损失 / IC（纯函数，绝不伪造）。

    返回 ``(weights, curvePoint)``：``curvePoint`` 不含 ``step``（由编排层按序赋）。``trainLoss/validLoss`` 为
    复合得分与前瞻收益标准化后的均方误差（验证段为严格更晚时间，无未来泄漏）；``trainIc/validIc`` 为 RankIC。
    ``equal`` 法权重与样本无关，故其验证段曲线跨 fraction 恒定（R-11 构造性恒定，诚实真实行为，非 bug）。

    Raises:
        ValueError: 未知 method。
    """
    trainer = TRAINERS.get(method)
    if trainer is None:
        raise ValueError(f"未知训练方法：{method}")
    backend = trainer.resolve()
    k = max(1, int(np.floor(float(frac) * cut)))
    raw = backend.fit(matrix[:k], label[:k], config)
    weights = _normalize_weights(np.asarray(raw, dtype=float), names)
    weight_vec = np.asarray([weights[name] for name in names], dtype=float)
    composite = matrix @ weight_vec
    point = {
        "fraction": round(float(frac), 4), "nTrain": int(k),
        "trainLoss": _standardized_mse(composite[:k], label[:k]),
        "validLoss": _standardized_mse(composite[cut:], label[cut:]),
        "trainIc": _round(_spearman(composite[:k], label[:k])),
        "validIc": _round(_spearman(composite[cut:], label[cut:])),
    }
    return weights, point


def train_weights(matrix: np.ndarray, label: np.ndarray, names: list[str], *,
                  method: str = "linear", config: dict | None = None,
                  norm_stats: dict | None = None) -> TrainResult:
    """按 ``method`` 取训练器首个可用后端拟合权重（时序切分不打乱）；后端经 ``trainerVersion`` 如实回报。

    rf/xgb 因内置树兜底**恒可用**：库在场走库（高保真），缺库走内置实现（``note`` 提示、``trainerVersion``
    为 ``builtin-*``），绝不假权重、绝不静默降级（C1）。``available=False`` 分支保留作前向兼容（现实中
    rf/xgb 永真；仅未来若新增「纯探测无兜底」训练器才会触达）。

    Args:
        matrix / label / names: ``build_factor_matrix`` 与 ``forward_return_label`` 产物。
        method: equal | ic_weight | linear | random_forest | xgboost | extra_trees | mlp |
            kernel_ridge | knn | mutual_info | elastic_net（后 6 款为顶级 · 含非线性模型库，双后端恒可用）。
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
    cut = _train_cut(len(aligned_y), config)
    backend = trainer.resolve()
    raw = backend.fit(aligned_x[:cut], aligned_y[:cut], config)
    weights = _normalize_weights(np.asarray(raw, dtype=float), names)
    note = _BUILTIN_NOTE if backend.kind == "builtin" else ""
    return build_train_result(method, weights, names, sample=TrainSample(aligned_x, aligned_y, cut),
                              norm_stats=norm, trainer_version=backend.version(), note=note)


# ============================ 多标的池化训练（factor-lab-symbol-groups §3.5，绝不改上方等价性门禁函数） ============================
def build_pooled_sample(parts: list[tuple[np.ndarray, np.ndarray]], config: dict) -> TrainSample:
    """把逐标的 ``(matrix, label)`` 池化为单 ``TrainSample``：各自 ``_aligned`` 去 NaN → 各自 ``_train_cut``
    时序切分 → 拼「全体训练块 ‖ 全体验证块」，``cut = Σ`` 各标的训练段长（无未来泄漏：任意 fraction 前缀只取
    训练块，验证块恒为各标的留出段之并集）。

    单标的输入与 ``prepare_curve_sample`` **字节级等价**（``vstack([aligned[:cut], aligned[cut:]]) == aligned``、
    ``cut`` 相同），故多标的走本函数、单标的仍走原路径时权重训练等价性门禁不受影响（§十 不变量 1/2）。
    空 / 单行标的（对齐后 < 2 行、无法时序切分）跳过——其余标的照常池化，绝不因单标的退化整体失败。

    Args:
        parts: 逐标的 ``(build_factor_matrix 矩阵, forward_return_label 标签)``；[0] 为焦点标的。
        config: 训练配置（``split`` 决定各标的时序切分比例）。

    Returns:
        池化 ``TrainSample``（``matrix`` = 训练块拼验证块，``cut`` = Σ 各标的训练段长）。

    Raises:
        InsufficientSamplesError: 池化后对齐样本不足（< ``MIN_TRAIN_SAMPLES``），调用方据此判失败（绝不空成功）。
    """
    train_x: list[np.ndarray] = []
    train_y: list[np.ndarray] = []
    valid_x: list[np.ndarray] = []
    valid_y: list[np.ndarray] = []
    total_cut = 0
    width = 0
    for matrix, label in parts:
        aligned_x, aligned_y = _aligned(matrix, label)
        if len(aligned_y) < 2:
            continue
        cut_i = _train_cut(len(aligned_y), config)
        train_x.append(aligned_x[:cut_i])
        train_y.append(aligned_y[:cut_i])
        valid_x.append(aligned_x[cut_i:])
        valid_y.append(aligned_y[cut_i:])
        total_cut += cut_i
        if aligned_x.ndim == 2:
            width = aligned_x.shape[1]
    blocks_x = train_x + valid_x
    blocks_y = train_y + valid_y
    pooled_x = np.vstack(blocks_x) if blocks_x else np.empty((0, width))
    pooled_y = np.concatenate(blocks_y) if blocks_y else np.empty(0)
    if len(pooled_y) < MIN_TRAIN_SAMPLES:
        raise InsufficientSamplesError(
            f"池化训练样本不足：对齐后仅 {len(pooled_y)} 行（需 ≥{MIN_TRAIN_SAMPLES}）")
    return TrainSample(pooled_x, pooled_y, total_cut)


def train_weights_on_sample(sample: TrainSample, names: list[str], *, method: str = "linear",
                            config: dict | None = None, norm_stats: dict | None = None) -> TrainResult:
    """在**已对齐 / 已切分**的 ``TrainSample`` 上拟合并装配 ``TrainResult``（池化末步 = 曲线末步 f=1.0 等价）。

    与 ``train_weights`` 的差别仅在于：样本已由 ``build_pooled_sample`` 池化对齐（不再重复 ``_aligned`` / ``_train_cut``）。
    复用 ``build_train_result``（不改），拟合仅取训练块 ``sample.matrix[:cut]``（绝不触碰验证块，无未来泄漏）。

    Raises:
        ValueError: 未知 method / 样本不足（显式报错，绝不静默）。
    """
    config = config or {}
    trainer = TRAINERS.get(method)
    if trainer is None:
        raise ValueError(f"未知训练方法：{method}")
    norm = norm_stats or {"normWindow": DEFAULT_NORM_WINDOW, "clip": DEFAULT_CLIP}
    if not trainer.available():
        return TrainResult(method=method, weights={}, normStats=norm, metrics={},
                           available=False, note=_INSTALL_HINT)
    if len(sample.label) < MIN_TRAIN_SAMPLES:
        raise ValueError(f"训练样本不足：池化后仅 {len(sample.label)} 行（需 ≥{MIN_TRAIN_SAMPLES}）")
    backend = trainer.resolve()
    raw = backend.fit(sample.matrix[:sample.cut], sample.label[:sample.cut], config)
    weights = _normalize_weights(np.asarray(raw, dtype=float), names)
    note = _BUILTIN_NOTE if backend.kind == "builtin" else ""
    return build_train_result(method, weights, names, sample=sample, norm_stats=norm,
                              trainer_version=backend.version(), note=note)
