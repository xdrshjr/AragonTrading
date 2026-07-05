"""内置纯 numpy 非线性 / 稀疏线性拟合器（nonlinear.py）—— mlp/kernel_ridge/knn/mutual_info/elastic_net
训练器的恒可用兜底后端 (weight-model-nonlinear-trainers §3.3/§3.4)。

当 scikit-learn 缺失（尤其桌面端 PyInstaller 冻结包不含重型 ML 库）时，作为对应训练器的**兜底后端**：
用纯 numpy 从数据学出每个因子对前瞻收益的**非线性预测贡献**（置换重要度 / 互信息 / 稀疏系数），返回
**逐因子非负重要度**（``elastic_net`` 返带符号系数）——语义与树模型 ``feature_importances_`` 同构，经上层
``_normalize_weights`` 归一后无缝接入既有「权重 → 线性打分」契约（**诚实声明**：非线性只体现在「权重学习」
一步，部署的组合打分仍是线性加权；端到端非线性打分列未来工作）。

工程契约与 ``tree_ensemble.py`` 完全对齐：
- **确定性**（评审 P1-1/P2-4）：固定 ``seed=0``；权重初始化、置换洗牌、行下采样全部 seeded，「同输入 →
  同权重」跨平台可复现。
- **全函数性**（评审 P1-3）：对已 dropna 且行数 ≥ 2 的输入是全函数——退化情形（常数 y、零方差列）一律返
  **全零重要度**（由上层回落等权），绝不抛 ``LinAlgError``；仅输入契约违例（``X``/``y`` 行数不一致、``X``
  非二维）抛 ``ValueError``（由 service 层收敛 400）。
- **最坏成本封顶**（评审 P1-2）：拟合前对训练行数按各自 ``*_MAX_TRAIN_ROWS`` 做确定性无放回下采样
  （``cap_train_rows``），把纯 numpy 最坏拟合成本按行数封顶；``kernel_ridge``(O(n³)) / ``knn``(O(n²)) 设更紧
  上限。**该行封顶同样施于库（sklearn）后端**（见 ``training.py`` 各 ``_fit_*_sklearn`` 入口），令「装库与否」
  只切保真度、不切最坏成本上界。
"""
from __future__ import annotations

from collections.abc import Callable

import numpy as np

from .tree_ensemble import BUILTIN_SEED  # 单源确定性种子（与树集成兜底同源）

# 版本徽标（经 trainerVersion 如实回报后端，UI 显示，不静默降级 C1）。
BUILTIN_MLP_VERSION = "builtin-mlp@1"
BUILTIN_KRR_VERSION = "builtin-krr@1"
BUILTIN_KNN_VERSION = "builtin-knn@1"
BUILTIN_MI_VERSION = "builtin-mi@1"
BUILTIN_EN_VERSION = "builtin-en@1"

# 单源默认超参（库后端与内置后端**共用**，令「装库与否」只切保真度/速度、不切模型粗形态，评审 P1-2）。
MLP_DEFAULTS = {"hidden": 16, "epochs": 300, "lr": 1e-2, "l2": 1e-4}
KRR_DEFAULTS = {"alpha": 1.0, "gamma": None}          # gamma=None → RBF "scale" 口径 1/(k·Var(X))
KNN_DEFAULTS = {"k": 15}
MI_DEFAULTS = {"bins": None}                           # bins=None → clip(round(sqrt(n)),8,32)
EN_DEFAULTS = {"alpha": 1e-3, "l1_ratio": 0.5, "max_iter": 500, "tol": 1e-4}

# 最坏成本行封顶（kernel_ridge O(n³) / knn O(n²) 设更紧上限，评审 P1-2 风险表）。
MLP_MAX_TRAIN_ROWS = 4000
KRR_MAX_TRAIN_ROWS = 1200
KNN_MAX_TRAIN_ROWS = 2500
MI_MAX_TRAIN_ROWS = 4000
EN_MAX_TRAIN_ROWS = 4000

# 置换重要度默认重复次数（噪声↔成本折中；被学习曲线步数放大，故保守取 4）。
PERM_REPEATS = 4

# 超参硬上限（防纯 numpy 算力爆炸；本迭代前端不暴露调参，仅供引擎默认与未来扩展收敛）。
_MLP_MAX_HIDDEN = 128
_MLP_MAX_EPOCHS = 2000
_KNN_MAX_K = 200
_MI_MAX_BINS = 64
_EN_MAX_ITER = 5000
_MIN_ALPHA = 1e-6   # 核岭 λ 下限（保 K+λI 正定，杜绝 LinAlgError）


# ============================ 输入契约 / 成本封顶 / 退化门控 ============================
def _validate_xy(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """转 float 并校验输入契约（唯一抛 ``ValueError`` 处，评审 P1-3；与 ``tree_ensemble`` 同口径）。

    Raises:
        ValueError: ``x`` 非二维、或 ``x`` / ``y`` 行数不一致（输入契约违例，由上层收敛 400）。
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float).ravel()
    if x.ndim != 2:
        raise ValueError(f"nonlinear 需要二维特征矩阵，收到 ndim={x.ndim}")
    if x.shape[0] != y.shape[0]:
        raise ValueError(f"特征/标签行数不一致：X={x.shape[0]} 行、y={y.shape[0]} 行")
    return x, y


def cap_train_rows(x: np.ndarray, y: np.ndarray, max_rows: int,
                   seed: int = BUILTIN_SEED) -> tuple[np.ndarray, np.ndarray]:
    """行数超 ``max_rows`` 时确定性（seeded）无放回下采样封顶最坏成本（评审 P1-2）。

    库后端与内置后端**共用**本封顶（``training.py`` 框架层不封顶，护栏须落各后端入口），令库/内置成本上界
    一致——尤其 ``kernel_ridge`` O(n³) 绝不可无封顶跑长窗。行序保持升序（时序不打乱语义无关，仅切保真度）。
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float).ravel()
    n = x.shape[0]
    if n <= int(max_rows):
        return x, y
    rng = np.random.default_rng(seed)
    idx = np.sort(rng.choice(n, size=int(max_rows), replace=False))
    return x[idx], y[idx]


def _degenerate(x: np.ndarray, y: np.ndarray) -> bool:
    """退化输入门控：样本 < 2 / 无特征 / 非有限 / y 零方差 → 不可学（返全零重要度，上层回落等权，评审 P1-3）。"""
    if x.shape[0] < 2 or x.shape[1] == 0:
        return True
    if not (np.isfinite(x).all() and np.isfinite(y).all()):
        return True
    return float(np.var(y)) <= 0.0


def _mse(pred: np.ndarray, actual: np.ndarray) -> float:
    return float(np.mean((np.asarray(pred, dtype=float) - actual) ** 2))


# ============================ 共享置换重要度（permutation importance） ============================
def permutation_importance(predict: Callable[[np.ndarray], np.ndarray], x: np.ndarray, y: np.ndarray,
                           *, repeats: int = PERM_REPEATS, seed: int = BUILTIN_SEED) -> np.ndarray:
    """模型无关置换重要度（供 mlp/kernel_ridge 内置后端复用，评审 §3.3）。

    基线 ``mse(predict(x), y)``；逐列 seeded 洗牌 ``repeats`` 次、取验证误差**增量均值**；``clip(min=0)``。
    语义：某因子被打乱后误差上升越多 → 该因子（含非线性）预测贡献越大 → 权重越高。样本内口径（在训练块上
    计算，与树重要度同基准）。全零由上层回落等权。
    """
    x = np.asarray(x, dtype=float)
    base = _mse(predict(x), y)
    n, k = x.shape
    rng = np.random.default_rng(seed)
    imp = np.zeros(k, dtype=float)
    for j in range(k):
        acc = 0.0
        for _ in range(int(repeats)):
            xp = x.copy()
            xp[:, j] = x[rng.permutation(n), j]
            acc += _mse(predict(xp), y) - base
        imp[j] = acc / int(repeats)
    return np.clip(imp, 0.0, None)


# ============================ RBF 核工具（kernel_ridge 共用） ============================
def _rbf_gamma(x: np.ndarray) -> float:
    """RBF 核带宽 ``γ = 1/(k·Var(X))``（sklearn "scale" 口径）；零方差回落 ``1/k``（评审 §3.4-③）。"""
    k = max(1, x.shape[1])
    var = float(np.mean(np.var(x, axis=0)))
    return 1.0 / (k * var) if var > 0.0 else 1.0 / k


def _rbf_kernel(a: np.ndarray, b: np.ndarray, gamma: float) -> np.ndarray:
    """RBF 核矩阵 ``K_ij = exp(-γ‖a_i − b_j‖²)``（平方距离 clip≥0 防浮点负值）。"""
    a2 = np.sum(a * a, axis=1)[:, None]
    b2 = np.sum(b * b, axis=1)[None, :]
    d2 = np.clip(a2 + b2 - 2.0 * (a @ b.T), 0.0, None)
    return np.exp(-float(gamma) * d2)


# ============================ 超参解析（镜像 tree_ensemble.resolve_tree_params） ============================
def resolve_mlp_params(config: dict, defaults: dict = MLP_DEFAULTS) -> dict:
    """读可选 ``hidden``/``epochs``/``lr``/``l2`` 覆盖单源默认并按硬上限收敛（库/内置共用）。"""
    return {
        "hidden": max(1, min(int(config.get("hidden", defaults["hidden"])), _MLP_MAX_HIDDEN)),
        "epochs": max(1, min(int(config.get("epochs", defaults["epochs"])), _MLP_MAX_EPOCHS)),
        "lr": float(config.get("lr", defaults["lr"])),
        "l2": max(0.0, float(config.get("l2", defaults["l2"]))),
    }


def resolve_krr_params(config: dict, defaults: dict = KRR_DEFAULTS) -> dict:
    """读可选 ``alpha``（λ，钳到 ``≥_MIN_ALPHA`` 保正定）/ ``gamma``（None → scale 口径）覆盖默认。"""
    gamma = config.get("gamma", defaults["gamma"])
    return {
        "alpha": max(_MIN_ALPHA, float(config.get("alpha", defaults["alpha"]))),
        "gamma": None if gamma is None else float(gamma),
    }


def resolve_knn_params(config: dict, defaults: dict = KNN_DEFAULTS) -> dict:
    """读可选 ``k`` 覆盖默认并按硬上限收敛（实际邻数在拟合期再钳到 ``n−1``，评审 P2-5）。"""
    return {"k": max(1, min(int(config.get("k", defaults["k"])), _KNN_MAX_K))}


def resolve_mi_params(config: dict, defaults: dict = MI_DEFAULTS) -> dict:
    """读可选 ``bins`` 覆盖默认（None → 拟合期按 ``clip(round(sqrt(n)),8,32)`` 自适应）。"""
    bins = config.get("bins", defaults["bins"])
    return {"bins": None if bins is None else max(2, min(int(bins), _MI_MAX_BINS))}


def resolve_en_params(config: dict, defaults: dict = EN_DEFAULTS) -> dict:
    """读可选 ``alpha``/``l1Ratio``/``maxIter``/``tol`` 覆盖默认并收敛（``l1_ratio`` 钳到 [0,1]）。"""
    return {
        "alpha": max(0.0, float(config.get("alpha", defaults["alpha"]))),
        "l1_ratio": min(1.0, max(0.0, float(config.get("l1Ratio", defaults["l1_ratio"])))),
        "max_iter": max(1, min(int(config.get("maxIter", defaults["max_iter"])), _EN_MAX_ITER)),
        "tol": max(1e-9, float(config.get("tol", defaults["tol"]))),
    }


# ============================ MLP（单隐层前馈网络，置换重要度） ============================
def _train_mlp(x: np.ndarray, y: np.ndarray, *, hidden: int, epochs: int, lr: float, l2: float,
               seed: int) -> Callable[[np.ndarray], np.ndarray]:
    """全批 Adam 训练单隐层 ``k→H→1``（tanh 激活），返回预测闭包（供置换重要度调用）。

    权重 seeded 初始化（``1/√k`` 缩放）保确定性；L2 正则抑过拟合。y 应已标准化（调用方负责），预测同尺度。
    """
    n, k = x.shape
    rng = np.random.default_rng(seed)
    scale = 1.0 / np.sqrt(k)
    w1 = rng.standard_normal((k, hidden)) * scale
    b1 = np.zeros(hidden, dtype=float)
    w2 = rng.standard_normal(hidden) * (1.0 / np.sqrt(hidden))
    b2 = 0.0
    # Adam 状态
    params = [w1, b1, w2, b2]
    m_state = [np.zeros_like(p) if isinstance(p, np.ndarray) else 0.0 for p in params]
    v_state = [np.zeros_like(p) if isinstance(p, np.ndarray) else 0.0 for p in params]
    beta1, beta2, eps = 0.9, 0.999, 1e-8
    for t in range(1, int(epochs) + 1):
        z1 = x @ w1 + b1
        h = np.tanh(z1)
        out = h @ w2 + b2
        err = out - y                                   # dL/dout（MSE，1/n 归一）
        g_out = (2.0 / n) * err
        g_w2 = h.T @ g_out + 2.0 * l2 * w2
        g_b2 = float(np.sum(g_out))
        g_h = np.outer(g_out, w2) * (1.0 - h * h)       # tanh'
        g_w1 = x.T @ g_h + 2.0 * l2 * w1
        g_b1 = np.sum(g_h, axis=0)
        grads = [g_w1, g_b1, g_w2, g_b2]
        for i, (p, g) in enumerate(zip(params, grads)):
            m_state[i] = beta1 * m_state[i] + (1.0 - beta1) * g
            v_state[i] = beta2 * v_state[i] + (1.0 - beta2) * (g * g)
            m_hat = m_state[i] / (1.0 - beta1**t)
            v_hat = v_state[i] / (1.0 - beta2**t)
            params[i] = p - lr * m_hat / (np.sqrt(v_hat) + eps)
    w1, b1, w2, b2 = params

    def predict(xq: np.ndarray) -> np.ndarray:
        return np.tanh(xq @ w1 + b1) @ w2 + b2

    return predict


def fit_mlp_permutation_importances(x: np.ndarray, y: np.ndarray, *, hidden: int = MLP_DEFAULTS["hidden"],
                                    epochs: int = MLP_DEFAULTS["epochs"], lr: float = MLP_DEFAULTS["lr"],
                                    l2: float = MLP_DEFAULTS["l2"], repeats: int = PERM_REPEATS,
                                    seed: int = BUILTIN_SEED) -> np.ndarray:
    """神经网络（单隐层 MLP）逐因子置换重要度：训练 ``k→H→1`` 网络后测每因子被打乱的误差增量（非负）。"""
    x, y = _validate_xy(x, y)
    x, y = cap_train_rows(x, y, MLP_MAX_TRAIN_ROWS, seed)
    if _degenerate(x, y):
        return np.zeros(x.shape[1], dtype=float)
    ys = (y - float(np.mean(y))) / (float(np.std(y)) or 1.0)   # 标准化 y（还原不影响重要度排序）
    predict = _train_mlp(x, ys, hidden=hidden, epochs=epochs, lr=lr, l2=l2, seed=seed)
    return permutation_importance(predict, x, ys, repeats=repeats, seed=seed)


# ============================ Kernel Ridge（RBF 核回归，置换重要度） ============================
def fit_kernel_ridge_permutation_importances(x: np.ndarray, y: np.ndarray, *, alpha: float = KRR_DEFAULTS["alpha"],
                                             gamma: float | None = KRR_DEFAULTS["gamma"],
                                             repeats: int = PERM_REPEATS, seed: int = BUILTIN_SEED) -> np.ndarray:
    """核岭回归（RBF）逐因子置换重要度：闭式 ``α=(K+λI)⁻¹y``，``λ≥_MIN_ALPHA`` 保正定杜绝 LinAlgError。"""
    x, y = _validate_xy(x, y)
    x, y = cap_train_rows(x, y, KRR_MAX_TRAIN_ROWS, seed)
    if _degenerate(x, y):
        return np.zeros(x.shape[1], dtype=float)
    g = _rbf_gamma(x) if gamma is None else float(gamma)
    lam = max(_MIN_ALPHA, float(alpha))
    n = x.shape[0]
    k_train = _rbf_kernel(x, x, g)
    dual = np.linalg.solve(k_train + lam * np.eye(n), y)   # K+λI 正定 → solve 不抛 LinAlgError

    def predict(xq: np.ndarray) -> np.ndarray:
        return _rbf_kernel(xq, x, g) @ dual

    return permutation_importance(predict, x, y, repeats=repeats, seed=seed)


# ============================ KNN（局部非参回归，留一置换重要度） ============================
def fit_knn_permutation_importances(x: np.ndarray, y: np.ndarray, *, k: int = KNN_DEFAULTS["k"],
                                    repeats: int = PERM_REPEATS, seed: int = BUILTIN_SEED) -> np.ndarray:
    """K 近邻回归逐因子置换重要度：留一（预测某点排除自身）取最近 ``k`` 邻标签均值。

    专用置换循环（非共享 ``permutation_importance``）：查询行与训练行 1:1 对齐，故对角距离置 ∞ 实现留一，
    避免基线预测被自身标签泄漏（评审 §3.4-④）。``k`` 钳到 ``min(k, n−1)`` 防小样本步越界（评审 P2-5）。
    """
    x, y = _validate_xy(x, y)
    x, y = cap_train_rows(x, y, KNN_MAX_TRAIN_ROWS, seed)
    if _degenerate(x, y):
        return np.zeros(x.shape[1], dtype=float)
    n, kcols = x.shape
    kk = max(1, min(int(k), n - 1))

    def predict(xq: np.ndarray) -> np.ndarray:
        a2 = np.sum(xq * xq, axis=1)[:, None]
        b2 = np.sum(x * x, axis=1)[None, :]
        d2 = np.clip(a2 + b2 - 2.0 * (xq @ x.T), 0.0, None)
        np.fill_diagonal(d2, np.inf)                       # 留一：预测第 i 点排除训练第 i 点
        nn = np.argpartition(d2, kk - 1, axis=1)[:, :kk]
        return y[nn].mean(axis=1)

    base = _mse(predict(x), y)
    rng = np.random.default_rng(seed)
    imp = np.zeros(kcols, dtype=float)
    for j in range(kcols):
        acc = 0.0
        for _ in range(int(repeats)):
            xp = x.copy()
            xp[:, j] = x[rng.permutation(n), j]
            acc += _mse(predict(xp), y) - base
        imp[j] = acc / int(repeats)
    return np.clip(imp, 0.0, None)


# ============================ Mutual Information（信息论依赖，直方图 MI 估计） ============================
def _histogram_mi(a: np.ndarray, b: np.ndarray, bins: int) -> float:
    """等宽 2D 直方图互信息估计（非负）：``I = ΣΣ p(x,y)·log(p(x,y)/(p(x)p(y)))``，捕捉任意（含非单调）依赖。"""
    joint, _, _ = np.histogram2d(a, b, bins=bins)
    total = float(joint.sum())
    if total <= 0.0:
        return 0.0
    joint = joint / total
    px = joint.sum(axis=1, keepdims=True)
    py = joint.sum(axis=0, keepdims=True)
    denom = px @ py
    mask = (joint > 0.0) & (denom > 0.0)
    return float(np.sum(joint[mask] * np.log(joint[mask] / denom[mask])))


def fit_mutual_info_importances(x: np.ndarray, y: np.ndarray, *, bins: int | None = MI_DEFAULTS["bins"],
                                seed: int = BUILTIN_SEED) -> np.ndarray:
    """互信息加权：逐因子估计与前瞻收益的互信息（直方图 MI，非负），权重 ∝ MI。天然确定性（无随机）。"""
    x, y = _validate_xy(x, y)
    x, y = cap_train_rows(x, y, MI_MAX_TRAIN_ROWS, seed)
    if _degenerate(x, y):
        return np.zeros(x.shape[1], dtype=float)
    n, kcols = x.shape
    nb = int(bins) if bins else int(np.clip(round(np.sqrt(n)), 8, 32))
    mi = np.asarray([_histogram_mi(x[:, j], y, nb) for j in range(kcols)], dtype=float)
    return np.clip(mi, 0.0, None)


# ============================ Elastic Net（L1+L2 稀疏线性，坐标下降） ============================
def _soft_threshold(rho: float, thresh: float) -> float:
    """软阈值算子 ``S(ρ,t) = sign(ρ)·max(|ρ|−t, 0)``（L1 近端算子）。"""
    if rho > thresh:
        return rho - thresh
    if rho < -thresh:
        return rho + thresh
    return 0.0


def fit_elastic_net_coefficients(x: np.ndarray, y: np.ndarray, *, alpha: float = EN_DEFAULTS["alpha"],
                                 l1_ratio: float = EN_DEFAULTS["l1_ratio"], max_iter: int = EN_DEFAULTS["max_iter"],
                                 tol: float = EN_DEFAULTS["tol"], seed: int = BUILTIN_SEED) -> np.ndarray:
    """弹性网络稀疏**带符号**系数（坐标下降 + 软阈值），``_normalize_weights`` 保号。cyclic 更新天然确定性。

    目标 ``½‖y−Xw‖²/n + α(l1_ratio‖w‖₁ + ½(1−l1_ratio)‖w‖₂²)``；y 中心化（不用截距，仅取 ``|coef|`` 作
    重要度）。全函数：退化输入返全零（上层回落等权）。``seed`` 仅作行封顶与签名一致性透传（cyclic 无随机）。
    """
    x, y = _validate_xy(x, y)
    x, y = cap_train_rows(x, y, EN_MAX_TRAIN_ROWS, seed)
    if _degenerate(x, y):
        return np.zeros(x.shape[1], dtype=float)
    n, kcols = x.shape
    yc = y - float(np.mean(y))                             # 中心化（弹性网络默认拟合截距）
    a1 = float(alpha) * float(l1_ratio)                    # L1 强度
    a2 = float(alpha) * (1.0 - float(l1_ratio))            # L2 强度
    col_sq = np.sum(x * x, axis=0) / n                     # 每特征归一化分母
    w = np.zeros(kcols, dtype=float)
    resid = yc - x @ w
    for _ in range(int(max_iter)):
        w_prev = w.copy()
        for j in range(kcols):
            if col_sq[j] <= 0.0:                           # 零方差列 → 系数恒 0
                continue
            resid += x[:, j] * w[j]                        # 剔除第 j 特征当前贡献
            rho = float(x[:, j] @ resid) / n
            w[j] = _soft_threshold(rho, a1) / (col_sq[j] + a2)
            resid -= x[:, j] * w[j]                        # 加回更新后贡献
        if float(np.max(np.abs(w - w_prev))) < float(tol):
            break
    return w
