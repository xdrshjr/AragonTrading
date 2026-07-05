# 因子组合权重训练：扩充「顶级 · 含非线性」模型库设计方案

> Feature: `weight-model-nonlinear-trainers`
> Round: 第 2 轮（在第 1 轮「探索训练」成果之上继续完善）
> 版本：**v2**（评审节点已逐节复核，P0/P1 就地修复；见下「评审记录」与文末「评审结论」）
> 语言：简体中文正文 + 英文代码标识符（与仓库既有 spec / 代码注释约定一致）

---

## 评审记录（Review Notes，v2 新增）

> 评审维度：**可行性（Feasibility）/ 完整性（Completeness）/ 一致性（Consistency）/ 适度性（Right-sizing）**。
> 已对照实际代码逐项核对：`training.py`（双后端框架 / 等价性门禁 / `_normalize_weights`）、`tree_ensemble.py`
> （`_build_tree`/`_best_split`/`_subsample_rows`/行封顶）、`schemas.py`（`_WEIGHT_METHODS` + 三处内联 `Literal`
> + 探索 `methods max_length`）、`weight_explore_service.py`（`_guard_candidate_count` 服务层候选数上限）、
> 前端 `factorTypes/weightModel/weightBatch/weightExplore` 与编辑器 / 探索面板、`requirements-ml.txt`（`scikit-learn==1.6.1`）。

**核对确认为「无需修改」的关键前提（正确）**
- 双后端框架泛化正确：`TRAINERS` 追加条目后 `available_trainers()`/`train_weights()`/`fit_at_fraction()`/`train_weights_on_sample()` 均按 `method` 键零改动生效。
- 线性打分契约不破坏：所有新训练器仅产逐因子标量权重经 `_normalize_weights` 归一，`compose_score` 仍线性合成。
- **无需 DB 迁移**：`method` 列 `String(24)`，最长新名 `kernel_ridge`(12) ≤ 24。
- **放开 `methods max_length` 5→11 安全**：即便前端多选放开，`weight_explore_service._guard_candidate_count(len(combos)×len(methods))` 仍以 `settings.max_weight_explore_candidates` 服务层守门并显式 400（绝不静默截断），故不会因方法变多而绕过算力护栏。
- 库依赖真实存在：`scikit-learn==1.6.1` 已在 `requirements-ml.txt`；`ExtraTreesRegressor`/`MLPRegressor`/`KernelRidge`/`KNeighborsRegressor`/`mutual_info_regression`/`ElasticNet`/`inspection.permutation_importance` 均为该版本合法符号。

### P0（阻断）
- **无。** 方案未破坏任何既有契约，亦无「做不出来」的硬伤。

### P1（必须在 v2 就地修复——已修复）
- **P1-1｜可行性·确定性：`mutual_info` 的 sklearn 库后端非确定性。** `sklearn.feature_selection.mutual_info_regression` 默认 `random_state=None`（其 kNN 型 MI 估计会加随机噪声），若不显式传 `random_state=0`，将违反本方案硬性「同输入→同权重」确定性契约（测试 #3/#8）。**修复**：§3.2 / §3.4-⑤ 明确库后端 `mutual_info_regression(..., random_state=BUILTIN_SEED)`。
- **P1-2｜可行性·成本：库后端缺行封顶，`kernel_ridge` O(n³) 存在「库比内置更慢/疑似卡死」反转风险。** 原方案的 `*_MAX_TRAIN_ROWS` 行封顶只施于**内置**后端；但 `training.py` 把整段训练块 `aligned_x[:cut]` 原样传给后端，长时间窗下 sklearn `KernelRidge`(O(n³)) 及 `knn`/`mlp` 的置换重要度在无封顶时可能远慢于内置兜底，且被学习曲线 6 步 × 置换 `repeats` 放大，可能触发异步等待误判卡死——与本方案自身「成本」风险缓解自相矛盾。**修复**：§3.2 / §3.4 / §八 明确**库后端同样先按各自 `*_MAX_TRAIN_ROWS` 应用 `_subsample_rows`**（成本护栏对齐；只切保真度不切成本上界）。
- **P1-3｜可行性·一致性：`extra_trees`「复用 `_build_tree` 但把 `_best_split` 换成 `_random_split`」与现码冲突。** `_build_tree`（tree_ensemble.py:161）**硬编码**调用 `_best_split`，无 splitter 形参，无法「直接替换」；而方案同时承诺「不动既有 rf/gbdt 路径」。二者张力需消解。**修复**：§3.4-① / 文件计划明确——给 `_build_tree` 增**可选形参** `splitter=_best_split`（默认值令 rf/gbdt 调用点字节级不变、确定性不回归），`extra_trees` 传入 seeded 随机阈值 `_random_split`；并在测试计划补「rf/gbdt 重要度较改造前逐值不变」的回归守护。

### P2（记录，本轮酌情改善，未尽者列「实施注意」）
- **P2-1｜一致性：`db/models.py` 有三处 `method` 列注释**（第 247/273/348 行）而文件计划仅提「列尾注释」单数——应同步三处（已在文件计划订正）。
- **P2-2｜适度性：`nonlinear.py` 单文件承载 5 模型 + 共享助手有触及 800 行阈值风险**——已在文件计划补「超阈即按 `nonlinear_kernel.py` / `nonlinear_net.py` 拆分」预案。
- **P2-3｜一致性：前端方法标签双源**（编辑器本地 `METHODS` 与 `weightModel.METHOD_LABELS` 当前**取值一致**，属**潜在**漂移非 active bug）——重构为单源派生为维护性改善；注意重构后若 `METHOD_LABELS` 措辞与旧本地标签不同会改变既有方法显示文案（当前一致，安全）。
- **P2-4｜完整性·UX：探索结果矩阵列数由 ≤5 增至 ≤11**——需确认结果热力/表格在 11 列下横向不溢出（置于 `overflow-x` 滚动容器）。列为实现节点验收项。
- **P2-5｜完整性·边界：`knn` 早期小样本步 `k=15` 可能 > 样本数**——经既有 `learning_curve_steps` 以 `MIN_TRAIN_SAMPLES=30` 下限剔除小步后 `n_train≥30>15` 天然安全；仍建议内置 knn 兜底 `k=min(k, n_train−1)` 防御（已补入 §3.4-④）。
- **P2-6｜适度性·UX：默认方法示例勿选最慢模型**——`DEFAULT_BATCH_METHODS`/探索 `DEFAULT_METHODS` 若加非线性示例，建议选较快的 `extra_trees` 而非 `kernel_ridge`，以免默认探索/批次体感变慢（已在文件计划注明）。

---

## 一、Overview（概述）

因子实验室的「权重训练与组合」允许用户挑选若干因子成员，对其做组合权重训练；第 1 轮又新增了「探索训练」——用户在弹窗里选择**因子组合生成策略**与**训练方法集合**，系统并行地把每个「组合 × 方法」候选跑出来，按样本外 IC 择优，并给出组合探索洞察。整套机制的价值高度依赖「训练方法（模型）」这一维度的丰富度：探索训练本质上是让不同**归纳偏置（inductive bias）**的模型在同一批因子组合上竞赛，方法越多样，越可能发现被单一模型忽略的优质组合。

然而当前训练器注册表只有 5 款：`linear`（岭回归）/ `equal`（等权）/ `ic_weight`（IC 加权）/ `random_forest` / `xgboost`。其中真正意义上的「机器学习模型」只有随机森林与 XGBoost 两款树模型，模型族覆盖偏窄，且缺少神经网络、核方法、信息论、稀疏线性等能刻画**非线性 / 非单调依赖**的模型。本轮目标即：**在既有双后端训练器框架之上，新增至少 5 款顶级模型（本方案落 6 款），其中 5 款显式覆盖非线性情形**，用于训练因子组合，并**同步更新客户端**（模型编辑器、探索训练弹窗方法多选、多模型竞技场、方法标签/本地化）以暴露这些新模型。

关键设计约束是**必须复用既有的「权重 → 线性打分」契约**：所有训练器无论内部是否非线性，最终都产出一组**逐因子标量权重**（`dict[str, float]`），下游 `compose_score` 恒以 `matrix @ weight_vec` 线性合成得分，`factor_score` 策略、回测、快照机制均依赖这一线性契约。因此本方案对「非线性」的落地口径是：**在「如何从数据学出每个因子的权重」这一步引入非线性建模**——用树 / 神经网络 / 核回归 / kNN / 互信息等非线性模型度量每个因子（已方向化、已因果 z-score）对前瞻收益的**非线性预测贡献**，再把该贡献作为该因子的权重（符号来自因子既有 long/short 方向，与现有 `random_forest`/`xgboost` 完全同构）。这样既真实「考虑了非线性」，又不破坏任何既有契约。**诚实声明**：最终部署的组合打分仍是线性加权；若要做端到端非线性打分（非线性预测函数直接产出得分），将牵动整条 `{members, thresholds, weights}` 快照/回测/策略链，超出本轮范围，列入「未来工作」。

---

## 二、设计目标与非目标

**目标**
1. 新增 6 款训练器（5 款非线性 + 1 款稀疏线性），全部遵循既有 `Trainer`/`Backend` 双后端范式：库后端（scikit-learn，已在 `requirements-ml.txt`）优先、**纯 numpy 内置后端恒可用兜底**（桌面 PyInstaller 冻结包无 sklearn 时功能不降级）。
2. 每款训练器产出与现有 `random_forest`/`xgboost` **同语义**的逐因子权重（经 `_normalize_weights` 归一到 `Σ|w|=1`），无缝接入训练 / 学习曲线 / 探索 / 多模型竞技场 / 回测全链路。
3. 客户端相应更新：`TrainMethod` 类型、方法本地化标签、编辑器下拉、探索弹窗方法多选、竞技场方法列表、并放开会阻断新方法的后端 Schema `Literal` 与 `max_length` 约束。
4. 严格确定性（固定 `seed=0`，跨平台「同输入 → 同权重」）、显式错误传播、退化输入回落等权、最坏成本按行数封顶——全面对齐 `tree_ensemble.py` 既有工程契约。

**非目标**
- 不改 `compose_score` 的线性合成语义、不改 `factor_score` 策略/回测/快照链路。
- 不新增强制依赖（新库后端一律落在**已列**的 scikit-learn；不引入 lightgbm/catboost 等会与既有内置兜底「坍缩重名」的库）。
- 不为新模型开放前端超参调参 UI（与既有 `random_forest`/`xgboost` 一致，仅走引擎默认 + 可选 config 覆盖，评审 P2-2 同口径）。
- 不做 DB 迁移（`method` 为 `String(24)` 字符串列，新方法名均 ≤ 24 字符）。

---

## 三、Technical design（技术设计）

### 3.1 新增训练器清单

| method | 中文标签 | 模型族 | 非线性? | 库后端（sklearn） | 内置纯 numpy 兜底 | 权重来源 |
|---|---|---|---|---|---|---|
| `extra_trees` | 极端随机树 | 树装袋（randomized split） | ✅ | `ensemble.ExtraTreesRegressor` | `fit_extra_trees_importances`（tree_ensemble.py） | 不纯度下降重要度 |
| `mlp` | 神经网络(MLP) | 单隐层前馈网络 | ✅ | `neural_network.MLPRegressor` | `fit_mlp_permutation_importances`（nonlinear.py） | 置换重要度 |
| `kernel_ridge` | 核岭回归(RBF) | RBF 核回归 | ✅ | `kernel_ridge.KernelRidge(kernel="rbf")` | `fit_kernel_ridge_permutation_importances` | 置换重要度 |
| `knn` | K近邻回归 | 实例/局部非参 | ✅ | `neighbors.KNeighborsRegressor` | `fit_knn_permutation_importances` | 置换重要度 |
| `mutual_info` | 互信息加权 | 信息论依赖 | ✅ | `feature_selection.mutual_info_regression` | `fit_mutual_info_importances` | 每因子 MI |
| `elastic_net` | 弹性网络 | L1+L2 稀疏线性 | ❌（稀疏线性补充） | `linear_model.ElasticNet` | `fit_elastic_net_coefficients` | `|coef|` |

合计：现有 5 + 新增 6 = **11 款**，其中新增里 5 款非线性，超额满足「至少 5 款 + 考虑非线性」。`extra_trees` 因属树模型放进 `tree_ensemble.py` 复用建树基建；其余 5 款新建独立模块 `nonlinear.py`（避免 `tree_ensemble.py` 超 800 行、职责清晰）。

### 3.2 复用既有双后端框架（training.py）

`training.py` 已有 `Backend(fit, kind, probe_module, version)` 与 `Trainer(backends)`（按序取首个可用后端）。新增训练器只需在 `TRAINERS` 字典追加条目，其余（`available_trainers()` 目录、`trainer_available()`、`train_weights()`、`fit_at_fraction()`、`train_weights_on_sample()`）**零改动自动生效**——它们全部按 `method` 键泛化处理。

每款新训练器形如（以 `mlp` 为例）：
```python
"mlp": Trainer((
    Backend(_fit_mlp_sklearn,  "library", "sklearn", lambda: _library_version("sklearn")),
    Backend(_fit_mlp_builtin,  "builtin", None,      lambda: BUILTIN_MLP_VERSION),
)),
```
- **库后端** `_fit_mlp_sklearn(x_train, y_train, config)`：装 sklearn 时优先，返回非负逐因子重要度（对无原生 `feature_importances_` 的模型，用 `sklearn.inspection.permutation_importance(model, x_train, y_train, n_repeats, random_state=BUILTIN_SEED).importances_mean`，`clip(min=0)`）。
- **内置后端** `_fit_mlp_builtin(...)`：调 `nonlinear.py` 纯 numpy 实现，恒可用兜底。
- 二后端**共用单源默认超参**（`MLP_DEFAULTS` 等），令「装库与否」只切保真度/速度、不切模型粗形态（评审 P1-2 同原则）。
- 实际生效后端经 `trainerVersion` 如实回报（库版本串 或 `builtin-<name>@1`），绝不假权重、绝不静默降级（C1）。
- **成本护栏对齐两后端（评审 v2 P1-2）**：库后端**与内置后端一样**，在拟合前先对训练块按各自 `*_MAX_TRAIN_ROWS` 做 seeded 无放回下采样（`_subsample_rows`），保证 `装库与否` 只切保真度、**不切最坏成本上界**。否则 sklearn `KernelRidge`(O(n³)) 及 `knn`/`mlp` 的置换重要度在长时间窗未封顶时可能比内置兜底更慢、被学习曲线 6 步放大而触发异步等待误判卡死。
- **确定性对齐两后端（评审 v2 P1-1）**：凡库后端内部含随机性者，一律显式播种至 `BUILTIN_SEED(0)`——`permutation_importance(random_state=0)`、`mutual_info_regression(random_state=0)`、`MLPRegressor(random_state=0)`、`ExtraTreesRegressor(random_state=0)`；`KernelRidge`/`KNeighborsRegressor`/`ElasticNet(selection="cyclic")` 本身确定性，无需播种。这样同机「同输入→同权重」（测试 #3/#8）在库路径上亦成立。

因 `extra_trees`/`mlp`/`kernel_ridge`/`knn`/`mutual_info`/`elastic_net` 的库后端**全部是 sklearn 子模块**，`probe_module` 统一为 `"sklearn"`；且都有纯 numpy 兜底，故 6 款在目录中 `available` **恒 True**（backend = library 或 builtin），与 rf/xgb 现状一致。

### 3.3 共享的置换重要度（permutation importance）

`nonlinear.py` 提供一个模型无关的纯 numpy 助手，供 `mlp`/`kernel_ridge`/`knn`（这三者无天然逐特征重要度）内置后端复用：
```python
def permutation_importance(predict, x, y, *, repeats=PERM_REPEATS, seed=BUILTIN_SEED) -> np.ndarray:
    """基线 mse(predict(x), y)；逐列 seeded 洗牌 repeats 次取误差增量均值；clip(min=0)。全零→上层回落等权。"""
```
语义：某因子被打乱后验证误差上升越多 → 该因子（含非线性）贡献越大 → 权重越高。这与树模型的不纯度下降重要度同为「非负重要度」，符号仍由因子方向承载，与 rf/xgb 完全同构。置换在**训练块**上计算（与现有树重要度同为样本内口径，文档明示）。库后端与内置后端都走置换 → 两后端语义一致（P1-2）。

### 3.4 各模型算法概要、默认超参、成本封顶（内置后端）

所有内置后端遵守 `tree_ensemble.py` 既有契约：对「已 dropna 且行数 ≥ `MIN_TRAIN_SAMPLES`(30)」的输入是**全函数**（退化如常数 y / 零方差列 → 返全零重要度，由上层回落等权；绝不抛 `LinAlgError`）；仅输入契约违例（X/y 行数不一致、X 非二维）抛 `ValueError`（service 层收敛 400）。拟合前统一按各自 `*_MAX_TRAIN_ROWS` 做 seeded 无放回下采样封顶最坏成本（复用 `_subsample_rows` 思路），并按硬上限钳制超参。**评审 v2 P1-2：库后端（sklearn）同样在拟合前应用相同 `*_MAX_TRAIN_ROWS` 行封顶**——`training.py` 现把整段 `aligned_x[:cut]` 原样传后端、并不在框架层封顶，故成本护栏须落在每个 `_fit_*_sklearn` / `_fit_*_builtin` 入口，令库/内置两路径成本上界一致（尤其 `kernel_ridge` O(n³) 绝不可无封顶跑长窗）。

1. **extra_trees**（tree_ensemble.py）：森林 = 多棵 CART，但每棵在每次分裂时对候选特征只随机抽 1 个阈值（Extremely Randomized Trees 思想）。**评审 v2 P1-3 落地口径**：`_build_tree` 现**硬编码**调 `_best_split`（tree_ensemble.py:161），无法「直接替换」；且方案承诺「不动既有 rf/gbdt 路径」。解法——给 `_build_tree` 增**可选形参** `splitter: Callable = _best_split`（**默认值令 rf/gbdt 全部既有调用点字节级不变、确定性不回归**），并把 `feature, threshold, gain = _best_split(...)` 改为 `= splitter(...)`；新增 seeded 随机阈值分裂器 `_random_split(x, y, min_samples_leaf, rng)`——每特征在 `[min,max]` 均匀抽 1 阈值、算其不纯度下降增益、跨特征取最大（并列取最小下标），`extra_trees` 建树时传入。重要度 = 各树归一不纯度下降跨树均值（同 `fit_rf_importances`）。默认 `ET_DEFAULTS = {n_estimators:100, max_depth:6, min_samples_leaf:5}`；行封顶 4000；版本 `builtin-et@1`。**回归守护**：测试须断言改造后 `fit_rf_importances`/`fit_gbdt_importances` 对既有固定样本产出较改造前逐值不变（守「不动 rf/gbdt 路径」）。

2. **mlp**（nonlinear.py）：单隐层 `k→H→1`，`tanh` 激活，全批 Adam 训练。前置对 y 做零均值单位方差标准化（还原不影响重要度排序）。默认 `MLP_DEFAULTS = {hidden:16, epochs:300, lr:1e-2, l2:1e-4}`；行封顶 4000；置换 `repeats=4`；版本 `builtin-mlp@1`。权重初始化 seeded。

3. **kernel_ridge**（nonlinear.py）：RBF 核 `K_ij=exp(-γ‖xi−xj‖²)`，闭式 `α=(K+λI)⁻¹y`，预测 `k(x,·)·α`。`γ` 默认 `1/(k·Var(X))`（sklearn "scale" 口径），`λ=alpha` 默认 1.0。因 O(n³)，**行封顶收紧到 1200**；置换 `repeats=4`；版本 `builtin-krr@1`。

4. **knn**（nonlinear.py）：欧氏距离取最近 `K` 邻均值预测（留一：预测某点时排除自身）。默认 `KNN_DEFAULTS = {k:15}`；因 O(n²) 距离矩阵，**行封顶 2500**；置换 `repeats=4`；版本 `builtin-knn@1`。**边界（评审 v2 P2-5）**：内置后端取 `k=min(k, n_train−1)` 防御小样本步（虽 `learning_curve_steps` 以 `MIN_TRAIN_SAMPLES=30` 下限剔小步后 `n_train≥30>15` 天然安全，仍显式钳制以防未来阈值调整回归）。

5. **mutual_info**（nonlinear.py）：逐因子估计与前瞻收益的互信息（等频/等宽分箱的 2D 直方图 MI 估计，`bins=clip(round(sqrt(n)),8,32)`，非负），权重 ∝ MI。捕捉任意（含非单调）依赖。行封顶 4000；版本 `builtin-mi@1`。**评审 v2 P1-1**：其 sklearn 库后端 `mutual_info_regression` 默认 `random_state=None`（kNN 型 MI 估计会加随机噪声），**必须显式传 `random_state=BUILTIN_SEED(0)`** 以满足确定性契约；内置直方图 MI 本身确定性。

6. **elastic_net**（nonlinear.py）：坐标下降解 `min ½‖y−Xw‖²/n + α(l1_ratio‖w‖₁ + ½(1−l1_ratio)‖w‖₂²)`（软阈值算子），产**带符号**稀疏系数，`_normalize_weights` 保号。默认 `EN_DEFAULTS = {alpha:1e-3, l1_ratio:0.5, max_iter:500, tol:1e-4}`；行封顶 4000；版本 `builtin-en@1`。

每款附一个 `resolve_*_params(config, defaults)`（镜像 `resolve_tree_params`）：读可选 camelCase config 键（如 `hidden`/`epochs`/`k`/`alpha`/`l1Ratio`/`gamma`/`bins`）覆盖默认并按硬上限钳制。当前前端不暴露调参，仅供引擎默认与未来扩展。

### 3.5 训练时序（不变）

`train_weights` / `fit_at_fraction` / `train_weights_on_sample` 均调 `trainer.resolve().fit(matrix[:cut], label[:cut], config)` → `_normalize_weights` → 装配 `TrainResult`。新训练器接入后：
- 单次训练（`POST /weight-models/train` → `WeightTrainManager._TrainJob`）：`fitting` 阶段逐 fraction 调 `fit_at_fraction`，每步用对应内置/库后端拟合、在留出验证段度量真实 loss/IC，流式推送 `curve` 事件（新模型的学习曲线天然可画；`equal` 那类跨 fraction 恒定的构造性除外，本批新模型均随样本变化）。
- 探索批 / 多模型竞技场：编排层对「组合 × 方法」逐候选调同一训练路径，新方法作为候选自动纳入并行与排行。

**成本提示**：学习曲线默认 6 步，每步一次拟合；`kernel_ridge`(O(n³), 封顶 1200 行) 与 `knn`(O(n²), 封顶 2500 行) 是本批最重者，经行封顶后单候选最坏拟合可控（详见风险节）。

---

## 四、File / module change plan（文件 / 模块变更计划）

### 后端（Python）

| 文件 | 变更 | 一句话意图 |
|---|---|---|
| `services/quant/app/engine/factors/nonlinear.py` | **新建** | 纯 numpy 的 mlp/kernel_ridge/knn/mutual_info/elastic_net 拟合器 + `permutation_importance` 共享助手 + 各 `*_DEFAULTS`/`resolve_*_params`/版本徽标/行封顶。**评审 v2 P2-2**：若单文件逼近 800 行阈值（CLAUDE.md），按模型族拆 `nonlinear_kernel.py`(kernel_ridge/knn) 与 `nonlinear_net.py`(mlp) 两子模块，`nonlinear.py` 保留 mutual_info/elastic_net + 共享助手并聚合再导出（拆分不改对 `training.py` 的导入面）。 |
| `services/quant/app/engine/factors/tree_ensemble.py` | 修改 | 新增 `fit_extra_trees_importances` + `_random_split`（seeded 随机阈值）+ `ET_DEFAULTS`/`BUILTIN_ET_VERSION`；**评审 v2 P1-3**：给 `_build_tree` 增可选 `splitter=_best_split` 形参（默认值保证 rf/gbdt 调用点字节级不变），仅 `extra_trees` 传 `_random_split`；不改 rf/gbdt 语义与确定性。 |
| `services/quant/app/engine/factors/training.py` | 修改 | `TRAINERS` 追加 6 条目 + 各 `_fit_*_sklearn`/`_fit_*_builtin` 后端函数 + 导入新拟合器；不动等价性门禁函数。**评审 v2 P1-1/P1-2**：`_fit_*_sklearn` 入口先 `_subsample_rows` 行封顶、并对含随机性的 sklearn 估计器显式播种 `random_state=BUILTIN_SEED`（尤其 `mutual_info_regression`）。 |
| `services/quant/app/engine/factors/__init__.py` | 修改 | 视需要在 training 导出块内保持聚合导出（新增器为内部实现，一般无需新增公开导出）。 |
| `services/quant/app/schemas.py` | 修改 | `_WEIGHT_METHODS` **上移**为单源并追加 6 方法；`FactorTrainReq`/`FactorSetCreateReq`/`FactorSetUpdateReq` 的三处内联 `Literal` 改用 `_WEIGHT_METHODS`；探索 `methods` 的 `max_length=5` → `11`（放开新方法多选）。 |
| `services/quant/app/agent/tools/factor_tools.py` | 修改 | `_METHOD_ENUM` 追加 6 方法 + 训练/建集工具 description 中的方法枚举字串同步。 |
| `services/quant/app/agent/prompts.py` | 修改 | 系统提示里若列举可用训练方法则同步补齐（信息一致性；无则免改）。 |
| `services/quant/app/db/models.py` | 修改（注释） | **三处** `method` 列尾注释（第 247/273/348 行，评审 v2 P2-1）补列新方法名（`String(24)` 容量足够，**无迁移**）。 |
| `services/quant/requirements-ml.txt` | 修改（注释） | 说明新库后端均落已列 `scikit-learn`（不新增依赖）；缺库回落各自 `builtin-*`。 |

### 前端（TypeScript）

| 文件 | 变更 | 一句话意图 |
|---|---|---|
| `apps/web/lib/factorTypes.ts` | 修改 | `TrainMethod` 联合类型追加 6 字面量（单一类型真源）。 |
| `apps/web/lib/weightModel.ts` | 修改 | `METHOD_LABELS` 追加 6 中文标签（标签单源）；新增可选 `METHOD_DESCRIPTIONS` + `methodDescription()` 供 UI tooltip。 |
| `apps/web/lib/weightBatch.ts` | 修改 | `ALL_METHODS` 追加 6 方法（探索/竞技场共享真源）；`DEFAULT_BATCH_METHODS` 可加入 1 款非线性示例（**评审 v2 P2-6：选较快的 `extra_trees`，勿选 O(n³) 的 `kernel_ridge`，以免默认批次体感变慢**）。 |
| `apps/web/components/Factor/WeightModelEditor.tsx` | 修改 | 本地 `METHODS` 重构为由 `ALL_METHODS.map(m=>({value:m,label:methodLabel(m)}))` 派生，消除标签重复漂移。 |
| `apps/web/components/Factor/ExploreConfigPanel.tsx` | 修改 | `DEFAULT_METHODS` 可加入非线性示例（同 P2-6 取 `extra_trees`）；方法多选加 tooltip（`methodDescription`）；受益于 `ALL_METHODS` 自动列出新方法。**评审 v2 P2-4：结果矩阵列数由 ≤5 增至 ≤11，须置于 `overflow-x` 横向滚动容器防溢出。** |
| `apps/web/components/Factor/WeightBatchLauncher.tsx` | 修改（核对） | 复核方法列表/上限假设（其经 `ALL_METHODS` 自动列出；`MAX_BATCH_CANDIDATES=8` 保持不变即可）。 |
| `apps/web/lib/weightExplore.ts` | 修改（可选） | 视 UX 决定是否把 `MAX_EXPLORE_CANDIDATES` 从 12 适度上调（如 16），因非线性模型更慢，倾向保守，记入决策。 |

### 测试

| 文件 | 变更 | 意图 |
|---|---|---|
| `services/quant/tests/test_factor_training.py` | 修改 | 每款新方法：权重非负(适用者)/`Σ|w|=1`/确定性/退化回落等权/库缺失走 builtin+版本徽标/`available_trainers` 恒 available；`extra_trees` 走 `tree_ensemble` 分支。**评审 v2 P1-3**：新增「`_build_tree` 加 `splitter` 形参后 `fit_rf_importances`/`fit_gbdt_importances` 对固定样本产出逐值不变」的回归守护。**评审 v2 P1-1**：装 sklearn 时（`importorskip`）`mutual_info` 库后端两次训练权重逐值相等（验证 `random_state=0` 已生效）。 |
| `services/quant/tests/test_nonlinear_models.py` | **新建** | 单测 `nonlinear.py` 各拟合器与 `permutation_importance`：全函数性、确定性、非线性可辨（构造 U 型/异或型样本，非线性模型重要度 > 线性系数近零）、行封顶生效。 |
| `apps/web/lib/weightBatch.test.ts` | 修改 | `ALL_METHODS` 含新方法、`methodLabel` 全覆盖。 |
| `apps/web/lib/weightModel.test.ts` | 修改 | `METHOD_LABELS`/`methodDescription` 全覆盖新方法（无 undefined）。 |
| `apps/web/lib/weightExplore.test.ts` | 修改（如受影响） | 候选计数在多方法下的上下限阻断逻辑仍正确。 |

---

## 五、Interface design（接口设计）

**REST（形状不变，仅取值域扩充）**
- `GET /api/factors/trainers` → `available_trainers()`：目录数组自动多出 6 条 `{method, available, backend}`（新方法 `available:true`，`backend` 为 `"library"` 或 `"builtin"`）。前端 `factorApi.trainers` 消费，无需改类型。
- `POST /api/weight-models/train`、`POST /api/factors/train`、`POST /api/factor-sets`、`PUT /api/factor-sets/{id}`、探索/竞技场启动端点：请求体 `method` / `methods[]` 的 Pydantic `Literal` 值域扩充后即接受新方法（当前会被 422 拒绝，故此为**必须**的后端改动）。
- 探索启动请求 `methods: list[_WEIGHT_METHODS]` 的 `max_length` 由 5 提到 11，否则多选 >5 个方法会被 422。

**WebSocket（不变）**
- `/api/ws/weight-train/{job_id}` 事件族（`run`/`phase`/`curve`/`result`/`done`/`cancelled`/`error`/`ping`）与 `result.method`/`trainerVersion` 字段语义不变；新方法自然复用。

**Agent 工具（值域扩充）**
- `train_factor_weights` / 建集工具的 `method` 参数 `enum`（`_METHOD_ENUM`）追加 6 值，工具描述串同步。

**前端 CLI/命令**：无。

---

## 六、Data model（数据模型）

- **DB 无迁移**：`weight_models.method` / `weight_train_runs.method` / 批次候选 `method` 均为 `String(24)` 字符串列，最长新名 `kernel_ridge`(12) ≪ 24；`WeightModel.config_json` 为自由 JSON，可容纳未来可选超参键。
- **内存/契约形状不变**：`TrainResult{method, weights: dict[str,float], normStats:{normWindow,clip}, metrics:{icIn,icOut,r2In,r2Out,nTrain,nValid}, available, trainerVersion, note}` 对新方法逐字段同构；`normStats` 仍只记归一化契约参数（不存 mean/std）。
- **新增引擎常量**（模块内私有，非 DB）：`ET_DEFAULTS`/`MLP_DEFAULTS`/`KRR_DEFAULTS`/`KNN_DEFAULTS`/`MI_DEFAULTS`/`EN_DEFAULTS`、各 `BUILTIN_*_VERSION`、各 `*_MAX_TRAIN_ROWS`、`PERM_REPEATS`。
- **前端类型**：`TrainMethod` 联合追加 6 字面量；`TrainerInfo{method,available,backend}` 不变。

---

## 七、Testing & acceptance criteria（测试与验收）

**后端单测（pytest；遵守 `aragon-quant-test-isolation` 约定）**
1. `available_trainers()` 含 11 方法，6 新方法 `available:true`；`monkeypatch find_spec→None` 后仍 `available:true` 且 `backend=="builtin"`、`trainerVersion` 前缀 `builtin-*`。
2. 每款新方法 `train_weights` 产 `Σ|w|=1`；importance/MI/置换类权重非负；`elastic_net` 允许带符号。
3. **确定性**：同输入两次 `train_weights` 权重逐值相等（固定 seed）。
4. **退化回落**：常数 y / 零方差列 → 内置后端返全零 → 上层回落等权（不抛异常）。
5. **样本不足**：`< MIN_TRAIN_SAMPLES` 显式抛 `ValueError`（沿用既有断言）。
6. **非线性可辨**：构造非单调（U 型 / 异或）标签，验证 `mlp`/`extra_trees`/`kernel_ridge`/`mutual_info` 对该因子重要度显著 > `linear` 对其近零系数，佐证「确实捕捉了非线性」。
7. **行封顶**：> 各 `*_MAX_TRAIN_ROWS` 的输入不超时且下采样确定性。
8. 装了 sklearn 时（`importorskip`），各库后端以对应 `*_DEFAULTS` 构造模型且 `trainerVersion` 以 `sklearn ` 开头。

**前端单测（vitest；组件测试需 `NODE_ENV=test`，见 `aragon-web-vitest-node-env`）**
- `methodLabel`/`methodDescription` 对 11 方法均非 undefined；`ALL_METHODS` 长度=11；编辑器下拉、探索多选渲染出新方法项；探索候选计数上下限阻断在多方法下仍正确。

**验收标准（Definition of Done for 实现节点）**
- `GET /factors/trainers` 返回 11 款、6 新款 `available:true`。
- 模型编辑器「训练方法」下拉、探索弹窗方法多选、竞技场方法列表均出现 6 新款并可选。
- 任一新款可完成一次「新建/训练 → loss/IC 曲线渲染 → 回测」闭环，`trainerVersion` 如实显示库/内置后端。
- 前后端 `pnpm test` / `pytest` 全绿（新增用例含在内）；桌面冻结包场景（无 sklearn）6 新款走 `builtin-*` 仍可训练。

---

## 八、Risks & mitigations（风险与缓解）

| 风险 | 缓解 |
|---|---|
| **「非线性但线性打分」语义误解**：模型非线性，最终组合仍线性加权。 | 在 spec/UI tooltip/`note` 中**显式声明**：非线性体现在「权重学习」，部署打分仍线性；端到端非线性打分列未来工作。诚实契约（C1）。 |
| **计算成本**：`kernel_ridge` O(n³)、`knn` O(n²)，学习曲线 6 步 × 置换 repeats 放大。 | 各设更紧行封顶（krr 1200 / knn 2500），`PERM_REPEATS=4`，超参硬上限；镜像 `BUILTIN_MAX_TRAIN_ROWS` 既有「按行封顶最坏成本」策略，杜绝「卡死=不可用」（P1-4）。**评审 v2 P1-2：库（sklearn）后端与内置后端一样在拟合前应用同一 `*_MAX_TRAIN_ROWS` 行封顶**（`training.py` 框架层不封顶，护栏落各 `_fit_*_sklearn` 入口），避免「装了 sklearn 反而 O(n³) 无封顶更慢/疑似卡死」的反转。 |
| **置换重要度噪声/偶发负值**。 | `clip(min=0)`；全零由上层回落等权；样本内口径文档明示，与树重要度同基准。 |
| **跨平台确定性**：MLP 初始化、置换洗牌、ET 随机阈值、MI 分箱、knn 距离并列。 | 全部固定 `seed=0`；并列 tie-break 取最小下标/最小阈值（沿用 `tree_ensemble` P2-4 约定）；`argsort(kind="stable")`。**评审 v2 P1-1：sklearn 库后端凡含随机性者显式播种 `random_state=0`——尤其 `mutual_info_regression`（默认 `None` → 非确定性）、`permutation_importance`、`MLPRegressor`、`ExtraTreesRegressor`。** |
| **Schema `Literal`/`max_length` 未放开 → 新方法 422**。 | 变更计划明列四处 `Literal` 与探索 `methods max_length` 提到 11；单源 `_WEIGHT_METHODS` 上移集中维护。 |
| **前端标签双源漂移**（编辑器本地 `METHODS` 与 `METHOD_LABELS`）。 | 编辑器重构为从 `ALL_METHODS`+`methodLabel` 派生，收敛单源。 |
| **多方法叠乘候选数**：探索 `MAX_EXPLORE_CANDIDATES=12` / 竞技场 8 可能更快触顶。 | 属既有 UX 算力护栏，保留并如实提示「收窄组合/方法」；`MAX_EXPLORE_CANDIDATES` 是否上调记为显式决策（默认保守不改），绝不静默截断。 |
| **等价性门禁**：`train_weights` / `fit_at_fraction` / 池化末步字节级等价不能被破坏。 | 只新增 `TRAINERS` 条目与后端函数，**不改**任何等价性门禁函数（`_train_cut`/`build_train_result`/`build_pooled_sample`）；新方法复用同装配路径。 |
| **PyInstaller 冻结包无 sklearn**。 | 6 款均有纯 numpy 兜底，`available` 恒真，`trainerVersion=builtin-*`；不把 sklearn 打进冻结包（`requirements-ml.txt` 仍为可选）。 |

---

## 九、未来工作（Future work）

- **端到端非线性打分路径**：新增一种可持久化的非线性预测器（如导出 MLP/树的可序列化预测函数），让 `factor_score` 策略在打分时走非线性合成而非线性加权。牵动快照结构、回测复算、策略注册与 UI 展示，成本大，另立 spec。
- **超参调参 UI**：为进阶用户暴露 `hidden`/`k`/`alpha` 等；当前仅引擎默认。
- **CatBoost/LightGBM 库后端**：待其内置兜底能有区别于现有 GBDT 的诚实实现时再引入，避免「多方法坍缩同一 builtin」。

---

## 评审结论（Review Verdict）

**通过（Approved）。**

本方案在**可行性 / 完整性 / 一致性 / 适度性**四维均达标：

- **可行性**：新增 6 款训练器完全复用 `training.py` 既有双后端框架与 `_normalize_weights`/`build_train_result` 等价装配路径，`scikit-learn==1.6.1` 及全部引用子模块真实存在，纯 numpy 兜底可实现；无 DB 迁移。原 v1 的三处可行性风险（`mutual_info` sklearn 非确定性、库后端无行封顶、`extra_trees` 复用 `_build_tree` 与现码冲突）已在 v2 **就地修复**并给出精确落地口径与回归守护测试。
- **完整性**：确定性 / 退化回落 / 样本不足 / 行封顶 / 非线性可辨 / 库缺失走 builtin 均有测试覆盖；放开 `methods max_length` 已核实被服务层 `_guard_candidate_count` 兜底、不绕过算力护栏。
- **一致性**：不改线性打分契约、不动等价性门禁函数、不新增强制依赖（新库后端全落已列 sklearn）；`_WEIGHT_METHODS` 单源上移 + 前端标签单源派生消除双源漂移。
- **适度性**：6 款（5 非线性 + 1 稀疏线性）超额满足「≥5 且考虑非线性」而不过度；未开放调参 UI / 未做端到端非线性打分（列未来工作）为合理收敛。

**放行前实现节点须落实（均已写入 v2 正文，非新增阻断项）**：
1. 库后端与内置后端**同步应用** `*_MAX_TRAIN_ROWS` 行封顶（P1-2）。
2. 库后端含随机性的估计器**显式** `random_state=0`，尤其 `mutual_info_regression`（P1-1）。
3. `_build_tree` 以**默认值形参** `splitter=_best_split` 扩展、rf/gbdt 字节级不变 + 回归守护测试（P1-3）。
4. P2 项（三处 DB 注释、`nonlinear.py` 800 行拆分预案、结果矩阵横向滚动、knn `k` 钳制、默认方法示例取 `extra_trees`）按正文注记执行。

无 P0 / P1 遗留。

---

*本 v2 由「方案评审与修复」节点产出：在 v1 基础上新增「评审记录」「评审结论」两段、就地修复全部 P0/P1（实为 3 个 P1，无 P0）并同步文件计划 / 测试计划 / 风险表，**仅修改 `docs/plans/weight-model-nonlinear-trainers/spec.md` 一文件，未改任何源码、未提交**。*
