"""内置纯 numpy 树集成（tree_ensemble.py）—— rf/xgb 训练器的恒可用兜底后端 (spec §2.3)。

当 sklearn / xgboost 缺失（尤其桌面端 PyInstaller 冻结包不含重型 ML 库）时，作为
``random_forest`` / ``xgboost`` 训练器的**兜底后端**：返回真实模型算出的非负特征重要度向量
（**非假数据**），与现有库后端 ``feature_importances_`` 同语义（CART 加权不纯度下降 / gain）。

严格确定性（评审 P2-4）：固定 ``seed=0``；最优分裂并列增益的 tie-break 取「最小特征下标、最小
阈值」首遇者（不依赖平台相关的 argmax 次序），保证「同输入同权重」跨平台可复现。

全函数性与错误契约（评审 P1-3）：对**已过 dropna 且行数 ≥ MIN_TRAIN_SAMPLES** 的输入是全函数
（total）—— 退化情形（常数 y、零方差列、节点样本不足、无正增益分裂）一律返回**全零重要度**
（由上层 ``_normalize_weights`` 回落等权），**绝不**抛 ``LinAlgError`` / ``IndexError`` / 空切片；
**仅**当违反输入契约（``X`` / ``y`` 行数不一致）时抛 ``ValueError``，以便 service 端统一收敛为 400。

最坏成本封顶（评审 P1-4）：拟合前对训练行数做确定性无放回下采样到 ``BUILTIN_MAX_TRAIN_ROWS``，
把纯 numpy 最坏拟合成本按行数封顶，避免大区间下「卡死=不可用」。
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import partial

import numpy as np

# 版本徽标（经 trainerVersion 如实回报后端，UI 显示，不静默降级 C1）。
BUILTIN_RF_VERSION = "builtin-rf@1"
BUILTIN_GBDT_VERSION = "builtin-gbdt@1"
BUILTIN_ET_VERSION = "builtin-et@1"   # 极端随机树（extra_trees）内置兜底徽标（weight-model-nonlinear-trainers §3.4-①）

# 单源默认超参（库后端与内置后端**共用**，令「装库与否」只切换保真度/速度、不切换模型粗形态，评审 P1-2）。
RF_DEFAULTS = {"n_estimators": 100, "max_depth": 6, "min_samples_leaf": 5}
GBDT_DEFAULTS = {"n_estimators": 100, "max_depth": 3, "learning_rate": 0.1, "min_samples_leaf": 5}
# 极端随机树与 rf 同形超参（随机阈值分裂而非穷举最优，捕捉不同归纳偏置，weight-model-nonlinear-trainers §3.4-①）。
ET_DEFAULTS = {"n_estimators": 100, "max_depth": 6, "min_samples_leaf": 5}

# 硬上限（防纯 numpy 算力爆炸）+ 行数封顶 + 确定性种子。
MAX_N_ESTIMATORS = 400
MAX_DEPTH_CAP = 12
BUILTIN_MAX_TRAIN_ROWS = 4000
BUILTIN_SEED = 0


def resolve_tree_params(config: dict, defaults: dict) -> dict:
    """从 ``config`` 读 ``nEstimators`` / ``maxDepth`` 覆盖单源默认，并按硬上限收敛（库 / 内置共用）。

    本迭代前端不暴露调参 UI（评审 P2-2），此入口仅供引擎默认生效与未来扩展。

    Args:
        config: 训练配置；可选 ``nEstimators`` / ``maxDepth`` 覆盖默认。
        defaults: ``RF_DEFAULTS`` 或 ``GBDT_DEFAULTS`` 单源默认常量。

    Returns:
        ``{n_estimators, max_depth, min_samples_leaf}``，均收敛到 ``[1, MAX_*]``。
    """
    n_estimators = int(config.get("nEstimators", defaults["n_estimators"]))
    max_depth = int(config.get("maxDepth", defaults["max_depth"]))
    return {
        "n_estimators": max(1, min(n_estimators, MAX_N_ESTIMATORS)),
        "max_depth": max(1, min(max_depth, MAX_DEPTH_CAP)),
        "min_samples_leaf": int(defaults["min_samples_leaf"]),
    }


# ============================ CART 回归树 ============================
@dataclass
class _Node:
    """CART 回归树节点：叶存 ``value=mean(y_node)``；内部节点另存 ``feature/threshold/left/right``。"""

    value: float
    feature: int = -1
    threshold: float = 0.0
    left: "_Node | None" = None
    right: "_Node | None" = None

    @property
    def is_leaf(self) -> bool:
        return self.left is None


def _validate_xy(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """转 float 并校验输入契约（唯一抛 ``ValueError`` 处，评审 P1-3）。

    Raises:
        ValueError: ``x`` 非二维、或 ``x`` / ``y`` 行数不一致（输入契约违例，由上层收敛 400）。
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float).ravel()
    if x.ndim != 2:
        raise ValueError(f"tree_ensemble 需要二维特征矩阵，收到 ndim={x.ndim}")
    if x.shape[0] != y.shape[0]:
        raise ValueError(f"特征/标签行数不一致：X={x.shape[0]} 行、y={y.shape[0]} 行")
    return x, y


def _subsample_rows(x: np.ndarray, y: np.ndarray, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """行数超 ``BUILTIN_MAX_TRAIN_ROWS`` 时确定性（seeded）无放回下采样封顶最坏成本（评审 P1-4）。"""
    n = x.shape[0]
    if n <= BUILTIN_MAX_TRAIN_ROWS:
        return x, y
    rng = np.random.default_rng(seed)
    idx = np.sort(rng.choice(n, size=BUILTIN_MAX_TRAIN_ROWS, replace=False))
    return x[idx], y[idx]


def _trainable(x: np.ndarray, y: np.ndarray, min_samples_leaf: int) -> bool:
    """退化输入门控：样本不足 / 非有限 / y 零方差 → 不可训（返全零重要度，上层回落等权，评审 P1-3）。"""
    if x.shape[0] < 2 * int(min_samples_leaf) or x.shape[1] == 0:
        return False
    if not (np.isfinite(x).all() and np.isfinite(y).all()):
        return False
    return float(np.var(y)) > 0.0


def _best_split_on_feature(x_col: np.ndarray, y: np.ndarray, sum_total: float,
                           n: int, min_samples_leaf: int) -> tuple[float, float]:
    """单特征最优分裂：升序排序后 O(n) 前缀和扫描，返回 ``(gain, threshold)``。

    ``gain`` 为分裂前后**残差平方和下降**（= 节点样本数 × 方差下降，与 sklearn 加权不纯度下降一致）：
    ``gain = sum_left²/n_left + sum_right²/n_right − sum_total²/n``。仅在相邻**不同**取值的中点设候选
    阈值，并约束左右子节点各 ≥ ``min_samples_leaf``。并列增益 tie-break：``argmax`` 取首遇（最小阈值，
    因 ``x`` 已升序），保证跨平台确定性（评审 P2-4）。
    """
    lo, hi = int(min_samples_leaf), n - int(min_samples_leaf)
    if hi < lo:
        return 0.0, 0.0
    order = np.argsort(x_col, kind="stable")
    xs, ys = x_col[order], y[order]
    prefix = np.cumsum(ys)
    left_size = np.arange(lo, hi + 1)                      # 左子节点样本数候选
    sum_left = prefix[left_size - 1]
    sum_right = sum_total - sum_left
    gains = sum_left**2 / left_size + sum_right**2 / (n - left_size) - sum_total**2 / n
    distinct = xs[left_size - 1] != xs[left_size]          # 仅相邻不同取值处可分
    gains = np.where(distinct, gains, -np.inf)
    best = int(np.argmax(gains))                           # 并列取首遇（最小阈值），确定性
    best_gain = float(gains[best])
    if not np.isfinite(best_gain) or best_gain <= 0.0:
        return 0.0, 0.0
    pos = int(left_size[best])
    return best_gain, float((xs[pos - 1] + xs[pos]) / 2.0)


def _best_split(x: np.ndarray, y: np.ndarray,
                min_samples_leaf: int) -> tuple[int, float, float]:
    """全特征最优分裂：``(feature, threshold, gain)``；并列 tie-break 取最小特征下标（严格 ``>``，评审 P2-4）。"""
    n, k = x.shape
    sum_total = float(np.sum(y))
    best_feature, best_threshold, best_gain = -1, 0.0, 0.0
    for j in range(k):
        gain, threshold = _best_split_on_feature(x[:, j], y, sum_total, n, min_samples_leaf)
        if gain > best_gain:
            best_feature, best_threshold, best_gain = j, threshold, gain
    return best_feature, best_threshold, best_gain


def _random_split_on_feature(x_col: np.ndarray, y: np.ndarray, sum_total: float, n: int,
                             min_samples_leaf: int, rng: np.random.Generator) -> tuple[float, float]:
    """单特征**随机阈值**分裂（Extra-Trees 思想）：在列 ``[min,max]`` 均匀抽 1 个阈值，返回 ``(gain, threshold)``。

    ``gain`` 口径与 ``_best_split_on_feature`` 完全一致（残差平方和下降），仅阈值由随机抽样而非穷举最优
    得来——故建树更快、树间方差更低。列常数（``hi<=lo``）或左右子节点样本不足 ``min_samples_leaf`` → 返
    ``(0.0, 0.0)``（本特征不可分，交由跨特征取最大处置）。
    """
    lo, hi = float(np.min(x_col)), float(np.max(x_col))
    threshold = float(rng.uniform(lo, hi))
    if hi <= lo or not np.isfinite(threshold):
        return 0.0, 0.0
    mask = x_col <= threshold
    n_left = int(np.count_nonzero(mask))
    n_right = n - n_left
    if n_left < int(min_samples_leaf) or n_right < int(min_samples_leaf):
        return 0.0, 0.0
    sum_left = float(np.sum(y[mask]))
    sum_right = sum_total - sum_left
    gain = sum_left**2 / n_left + sum_right**2 / n_right - sum_total**2 / n
    return (float(gain), threshold) if gain > 0.0 else (0.0, 0.0)


def _random_split(x: np.ndarray, y: np.ndarray, min_samples_leaf: int,
                  rng: np.random.Generator) -> tuple[int, float, float]:
    """全特征**随机阈值**分裂：每特征抽 1 随机阈值、跨特征取最大增益（并列取最小下标，严格 ``>``）。

    与 ``_best_split`` 签名同形（多 ``rng`` 尾参，供 ``_build_tree`` 以 ``splitter`` 形参注入），供
    ``extra_trees`` 建树使用；rng 逐次抽样令整片森林在固定种子下确定性可复现（评审 P2-4 同 tie-break 约定）。
    """
    n, k = x.shape
    sum_total = float(np.sum(y))
    best_feature, best_threshold, best_gain = -1, 0.0, 0.0
    for j in range(k):
        gain, threshold = _random_split_on_feature(x[:, j], y, sum_total, n, min_samples_leaf, rng)
        if gain > best_gain:
            best_feature, best_threshold, best_gain = j, threshold, gain
    return best_feature, best_threshold, best_gain


def _build_tree(x: np.ndarray, y: np.ndarray, depth: int, max_depth: int,
                min_samples_leaf: int, importance: np.ndarray, splitter=_best_split) -> _Node:
    """递归建树，就地累计 ``importance[feature] += gain``；停止条件触发即返叶。

    停止条件：``depth >= max_depth``、``n < 2*min_samples_leaf``、或无正增益分裂（含 y 零方差）。

    ``splitter``（评审 v2 P1-3）：分裂器 ``(x, y, min_samples_leaf) -> (feature, threshold, gain)``，**默认
    ``_best_split`` 令 rf/gbdt 所有既有调用点字节级不变、确定性不回归**；``extra_trees`` 传入绑定了 ``rng`` 的
    ``_random_split``（随机阈值），无需触碰 rf/gbdt 路径即复用整套建树 / 剪枝 / 重要度累计基建。
    """
    node = _Node(value=float(np.mean(y)))
    if depth >= max_depth or y.shape[0] < 2 * int(min_samples_leaf):
        return node
    feature, threshold, gain = splitter(x, y, min_samples_leaf)
    if feature < 0 or gain <= 0.0:
        return node
    importance[feature] += gain
    mask = x[:, feature] <= threshold
    node.feature, node.threshold = feature, threshold
    node.left = _build_tree(x[mask], y[mask], depth + 1, max_depth, min_samples_leaf, importance, splitter)
    node.right = _build_tree(x[~mask], y[~mask], depth + 1, max_depth, min_samples_leaf, importance, splitter)
    return node


def _predict_tree(node: _Node, x: np.ndarray) -> np.ndarray:
    """按阈值递归分区预测（叶返 ``value``）；供 GBDT 逐轮算残差用。"""
    out = np.full(x.shape[0], node.value, dtype=float)
    if node.is_leaf:
        return out
    mask = x[:, node.feature] <= node.threshold
    if mask.any():
        out[mask] = _predict_tree(node.left, x[mask])
    if (~mask).any():
        out[~mask] = _predict_tree(node.right, x[~mask])
    return out


# ============================ 集成（RF / GBDT 重要度） ============================
def fit_rf_importances(x: np.ndarray, y: np.ndarray, *, n_estimators: int, max_depth: int,
                       min_samples_leaf: int, seed: int = BUILTIN_SEED) -> np.ndarray:
    """随机森林特征重要度：bootstrap 多树、每树重要度归一到和为 1 后跨树取均值（对齐 sklearn）。

    全函数（评审 P1-3）：退化输入返全零（上层回落等权），仅契约违例抛 ``ValueError``。分裂用全部
    特征（对齐 ``RandomForestRegressor`` 回归默认 ``max_features=1.0``；因子数通常 2~12，利于确定性）。

    Args:
        x: 特征矩阵 ``[n, k]``（已方向化、已因果 z-score、已 dropna）。
        y: 前瞻收益标签 ``[n]``。
        n_estimators / max_depth / min_samples_leaf: 树超参（应经 ``resolve_tree_params`` 收敛）。
        seed: 随机种子（行下采样 + bootstrap 确定性，默认 0）。

    Returns:
        ``np.ndarray[k]``：各特征非负重要度（未归一；全零交由上层回落等权）。

    Raises:
        ValueError: ``x`` / ``y`` 行数不一致（输入契约违例）。
    """
    x, y = _validate_xy(x, y)
    x, y = _subsample_rows(x, y, seed)
    n, k = x.shape
    if not _trainable(x, y, min_samples_leaf):
        return np.zeros(k, dtype=float)
    rng = np.random.default_rng(seed)
    acc = np.zeros(k, dtype=float)
    for _ in range(int(n_estimators)):
        idx = rng.integers(0, n, n)
        tree_importance = np.zeros(k, dtype=float)
        _build_tree(x[idx], y[idx], 0, max_depth, min_samples_leaf, tree_importance)
        total = float(tree_importance.sum())
        if total > 0.0:
            acc += tree_importance / total
    return acc / int(n_estimators)


def fit_gbdt_importances(x: np.ndarray, y: np.ndarray, *, n_estimators: int, max_depth: int,
                         learning_rate: float, min_samples_leaf: int,
                         seed: int = BUILTIN_SEED) -> np.ndarray:
    """梯度提升（XGBoost 代理）特征**总增益（gain）**重要度：逐轮对残差建浅树、累计分裂增益。

    对齐 xgboost ``importance_type="gain"`` 语义；无随机采样、天然确定性（``seed`` 仅作行下采样与签名
    一致性透传）。全函数性同 ``fit_rf_importances``（评审 P1-3）。

    Args:
        x / y: 同 ``fit_rf_importances``。
        n_estimators / max_depth / learning_rate / min_samples_leaf: 提升超参（应经收敛）。
        seed: 行下采样确定性种子（默认 0）。

    Returns:
        ``np.ndarray[k]``：各特征总增益（非负，未归一；全零交由上层回落等权）。

    Raises:
        ValueError: ``x`` / ``y`` 行数不一致（输入契约违例）。
    """
    x, y = _validate_xy(x, y)
    x, y = _subsample_rows(x, y, seed)
    n, k = x.shape
    if not _trainable(x, y, min_samples_leaf):
        return np.zeros(k, dtype=float)
    pred = np.full(n, float(np.mean(y)), dtype=float)
    gain = np.zeros(k, dtype=float)
    for _ in range(int(n_estimators)):
        residual = y - pred
        tree_importance = np.zeros(k, dtype=float)
        root = _build_tree(x, residual, 0, max_depth, min_samples_leaf, tree_importance)
        gain += tree_importance
        pred = pred + float(learning_rate) * _predict_tree(root, x)
    return gain


def fit_extra_trees_importances(x: np.ndarray, y: np.ndarray, *, n_estimators: int, max_depth: int,
                                min_samples_leaf: int, seed: int = BUILTIN_SEED) -> np.ndarray:
    """极端随机树（Extra-Trees）特征重要度：多棵**随机阈值**树、每树重要度归一到和为 1 后跨树取均值。

    与 ``fit_rf_importances`` 同装配（不纯度下降重要度）、同全函数与错误契约（评审 P1-3），唯一区别是每次
    分裂用 ``_random_split``（随机阈值）而非 ``_best_split``（穷举最优）——建树更快、树间方差更低，提供
    区别于随机森林的非线性归纳偏置（对齐 ``sklearn.ensemble.ExtraTreesRegressor``）。无 bootstrap（对齐
    ExtraTrees 默认 ``bootstrap=False``），树间多样性完全来自**逐树 seeded 随机阈值**，故固定 ``seed`` 下
    「同输入 → 同重要度」跨平台确定性可复现。

    Args:
        x: 特征矩阵 ``[n, k]``（已方向化、已因果 z-score、已 dropna）。
        y: 前瞻收益标签 ``[n]``。
        n_estimators / max_depth / min_samples_leaf: 树超参（应经 ``resolve_tree_params`` 收敛）。
        seed: 随机种子（行下采样 + 逐树随机阈值确定性，默认 0）。

    Returns:
        ``np.ndarray[k]``：各特征非负重要度（未归一；全零交由上层回落等权）。

    Raises:
        ValueError: ``x`` / ``y`` 行数不一致（输入契约违例）。
    """
    x, y = _validate_xy(x, y)
    x, y = _subsample_rows(x, y, seed)
    n, k = x.shape
    if not _trainable(x, y, min_samples_leaf):
        return np.zeros(k, dtype=float)
    acc = np.zeros(k, dtype=float)
    for tree_idx in range(int(n_estimators)):
        rng = np.random.default_rng(seed + tree_idx + 1)   # 逐树独立种子，森林整体确定性
        tree_importance = np.zeros(k, dtype=float)
        _build_tree(x, y, 0, max_depth, min_samples_leaf, tree_importance,
                    splitter=partial(_random_split, rng=rng))
        total = float(tree_importance.sum())
        if total > 0.0:
            acc += tree_importance / total
    return acc / int(n_estimators)
