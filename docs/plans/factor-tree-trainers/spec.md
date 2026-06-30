# 因子实验室「随机森林 / XGBoost」训练器适配方案（spec v2）

> Feature slug: `factor-tree-trainers`
> 作者：方案设计节点（Subtask #0）；评审与修复：方案评审节点（Subtask #1）
> 版本：**v2**（v1 → v2，2026-06-30 评审）—— 所有 P0/P1 已就地修复
> 状态：已评审通过（有条件），详见下方「评审记录」与文末「评审结论」

## 评审记录（Review Notes / Subtask #1）

> 评审维度：可行性（Feasibility）、完整性（Completeness）、一致性（Consistency，对照 `CLAUDE.md` 与现有代码约定）、合理体量（Right-sizing）。
> 评审基线：逐节核对 `training.py` / `factors/service.py` / `api/factors.py` / `FactorSetBuilder.tsx` / `factorTypes.ts` / `test_factor_training.py` / `requirements-ml.txt` / `agent/tools/factor_tools.py` / `engine/factors/__init__.py` 现实代码，确认 §2.1 根因链路与现状描述**逐条属实**。
> 结论速览：**无 P0**（双后端经 `trainerVersion` 如实回报，未触碰 C1 与归一化契约）；**4 个 P1 已在 v2 正文就地修复**；P2 列为实施期约束条件（见文末「评审结论」）。

### P1（已就地修复）

| # | 维度 | 位置 | 问题 | v2 修复 |
|---|---|---|---|---|
| P1-1 | 完整性 / 可行性 | §2.4、§四 | `available_trainers()` 要回报 `backend ∈ {native,library,builtin}`，但 `Backend` 结构无该标签字段；且核心训练器（equal/linear/ic_weight）与内置树兜底**都** `probe_module=None`，无法区分 native 与 builtin，派生规则缺失，实现者只能臆测。 | §2.4 给 `Backend` 增 **`kind` 显式标签字段**（`"native"/"library"/"builtin"`）；`available_trainers` 直接回报 `resolve().kind`，消除歧义。 |
| P1-2 | 一致性 | §2.3、§2.4 | 双后端默认超参分歧：现 sklearn `_fit_random_forest` 用 `n_estimators=200`、`max_depth=None`、`min_samples_leaf=1`，而内置 RF 用 `100/6/5`；现 xgboost `n_estimators=200`、内置 GBDT `100`。前端只传 `normWindow`、从不传 `nEstimators/maxDepth`，故装库与否会**切换出形态显著不同的模型**（非「同模型不同精度」），违背 §2.2「库=高保真、内置=轻量近似」的同模型语义。xgboost `feature_importances_` 默认 `importance_type` 与内置 GBDT 的「gain」未对齐。 | §2.3 抽出**单源默认常量**（RF=`100/6/5`、GBDT=`100/3/0.1/5`），§2.4 规定 sklearn / xgboost 后端**复用同一组默认**（sklearn 显式 `max_depth=6`、`min_samples_leaf=5`；XGBRegressor 显式 `importance_type="gain"`），令切换后端只改保真度与速度、不改模型粗形态。 |
| P1-3 | 错误处理 / 完整性 | §2.3、§七 | `service._train_weights_guarded` **只** `except ValueError`（收敛为 400）。内置树若对合法但退化输入抛非 `ValueError`（如 `LinAlgError`/`IndexError`/空切片），将逃逸为 **500**，破坏 §service「显式 400」错误契约（违 `CLAUDE.md` §五边界一次性校验、错误显式传播）。 | §2.3 规定内置 `fit_*_importances` 对「已过 dropna 且 ≥`MIN_TRAIN_SAMPLES`」的输入**全函数（total）**：退化（常数 y、零方差、节点样本不足）一律返回全零重要度（上层回落等权），**仅**对契约违例抛 `ValueError`；§七 增对应缓解行。 |
| P1-4 | 可行性 / 体验 | §2.3、§七 | 纯 numpy CART 递归对样本量**无上界**：`backtest_max_bars` 可达数千~上万根，100 棵树逐节点排序在 Python 层最坏可达十余秒，对「原本就抱怨不可用」的用户等同卡死。原方案仅有 `n_estimators/max_depth` 上限，未按**行数**封顶最坏成本。 | §2.3 增 **`BUILTIN_MAX_TRAIN_ROWS=4000` 确定性（seeded）行下采样**，仅作用于内置后端的拟合输入（指标仍用全量对齐集），把最坏拟合成本按行数封顶；§七 更新性能缓解行。 |

### P2（实施期约束，列入「评审结论」条件）

- **P2-1（一致性）**：`api/factors.py::factor_trainers` 路由 docstring 仍写「rf/xgb 缺库即 available=false」，v2 后恒为 true，将过时 → 已补入 §三 文件变更计划，要求实现时同步订正该 docstring。
- **P2-2（合理体量 / 完整性）**：`nEstimators/maxDepth` 在 §四被表述为「可选 config 字段」，但前端 `train.mutationFn` 仅发 `{ normWindow }`，本迭代无 UI 暴露 → §四已注明：本迭代二者为**引擎侧默认**，不新增 UI 控件（避免范围蔓延），仅保留引擎读取入口供未来扩展。
- **P2-3（一致性，TS）**：`factorTypes.ts::TrainerInfo.backend` 建议用字符串字面量联合 `"native" | "library" | "builtin"` 而非裸 `string`（呼应 `CLAUDE.md` §九「避免 any、精确类型」）→ 已在 §三/§四注明。
- **P2-4（可行性，确定性）**：内置最优分裂在并列增益时须有稳定 tie-break（取最小特征下标、最小阈值的首遇者），否则跨平台 argmax 次序差异破坏「同输入同权重」→ 已在 §2.3 注明。
- **P2-5（一致性，阈值）**：`tree_ensemble.py` 须守 `CLAUDE.md` §二阈值（单文件 ≤800 行、单方法 ≤50 行、圈复杂度 ≤10）；最优分裂扫描与建树须拆为命名辅助函数 → 已在 §三注明。
- **P2-6（完整性，可观测）**：内置兜底的 `note` 与 `trainerVersion` 徽标须真正在 UI 渲染，且核心训练器 `trainerVersion=null` 时**不**渲染空徽标 → 已在 §四/§三注明。

## 一、Overview（概述）

因子实验室的「权重训练与组合」工作区（`FactorSetBuilder`）提供 5 种权重训练方法：线性岭回归（`linear`）、等权（`equal`）、IC 加权（`ic_weight`）、随机森林（`random_forest`）、XGBoost（`xgboost`）。其中前三种仅依赖 numpy/pandas，**恒可用**；而随机森林与 XGBoost 在 UI 上长期呈灰色、不可点击，旁边标注「（未安装）」，用户无法用这两种方式去挖掘因子之间的权重组合，更无法把这种组合保存、进而创建 `factor_score` 得分策略。

经全链路排查，**这并非前端 bug，而是运行时缺少可选依赖导致的能力探测结果**：`random_forest` 训练器在运行时用 `importlib.util.find_spec("sklearn")` 探测、`xgboost` 训练器探测 `find_spec("xgboost")`；这两个库被刻意放在 `services/quant/requirements-ml.txt`（可选依赖），默认不安装，且因体量大被**刻意排除在桌面端 PyInstaller 冻结包之外**。于是 `GET /factors/trainers`（`available_trainers()`）对这两个方法返回 `available=false`，前端 `FactorSetBuilder.tsx` 的 `<option disabled={!trainerOf(...)}>` 据此禁用选项；即便强行选中，`POST /factor-sets/train` 也会在 `service.train()` 抛 `400 FACTOR_TRAINER_UNAVAILABLE`。

本方案的目标是：让随机森林与 XGBoost 在**任意运行环境（含桌面冻结包）下恒可用**，且不破坏项目既有的两条铁律——**C1：绝不假权重、绝不静默降级**，以及**刻意不把重型 ML 库塞进桌面冻结包**（体量约束）。为同时满足这两条，采用**「内置纯 numpy 树集成实现」作为恒可用后端 +「真实 sklearn/xgboost」作为可选加速/高保真后端**的双后端策略：库在场则用库（更高保真），库缺失则用内置实现（轻量、零新增强制依赖）。两种情况都返回**真实模型**算出的特征重要度作权重（非假数据），并通过 `trainerVersion` 字段把实际使用的后端如实回报给前端（如 `builtin-rf@1` / `sklearn 1.6.1`），做到**可观测、不静默**。训练产出的权重照旧烘焙进成员快照，下游「保存组合 / 从组合新建 `factor_score` 策略」无需任何改动即可直接复用，从而闭合「挖掘 → 评估 → 训练 → 保存 → 建策略」全链路。

## 二、Technical design（技术设计）

### 2.1 根因与现状链路（key code paths）

| 层 | 文件:符号 | 现状 |
|---|---|---|
| 引擎 | `services/quant/app/engine/factors/training.py` :`TRAINERS` | `random_forest` / `xgboost` 各持 `probe_module="sklearn"/"xgboost"`，`available()` 用 `find_spec` 探测，缺库即 False。`fit` 内 `from sklearn... / from xgboost...` 取 `feature_importances_`。 |
| 引擎 | 同上 :`available_trainers()` | 返回 `[{method, available}]`，rf/xgb 缺库为 `available=false`。 |
| 服务 | `services/quant/app/factors/service.py` :`train()` | `result.available` 为假 → `raise HTTPException(400, {code:"FACTOR_TRAINER_UNAVAILABLE"})`。 |
| 路由 | `services/quant/app/api/factors.py` :`GET /factors/trainers`、`POST /factor-sets/train` | 透传 `available_trainers()` / `service.train()`。 |
| 前端 | `apps/web/components/Factor/FactorSetBuilder.tsx` :`trainerOf`、`<option disabled>` | `available=false` → 选项禁用 + 文案「（未安装）」。 |
| 依赖 | `services/quant/requirements-ml.txt` | `scikit-learn==1.6.1`、`xgboost==2.1.3`，默认不装、刻意不进冻结包。 |

### 2.2 设计决策（与被否方案）

- **被否 A：把 sklearn/xgboost 移入强制 `requirements.txt` 并打进冻结包。** 会使安装包膨胀 ~200MB+，直接违背「刻意不进冻结包」的既有决策；xgboost 在 Windows 还需附带 DLL，打包复杂度高。否决。
- **被否 B：仅修前端，把禁用逻辑去掉。** 选项可点了，但训练仍 400，属于「假装能用」，违背 C1。否决。
- **采用 C：双后端（内置 numpy 树集成恒可用 + 真实库可选加速）。** 任意环境可用；尊重体量约束；产出真实权重；后端经 `trainerVersion` 如实回报，不静默降级。

### 2.3 内置树集成实现（新模块 `tree_ensemble.py`）

纯 numpy 实现 CART 回归树 + 两种集成，**严格确定性**（固定种子 `seed=0`，与现有真实库 `random_state=0` 对齐），与现有训练器同样**返回未归一化的非负重要度向量**（方向已在 `directional_values` 烘焙进特征，符号为正，与现有真实库 `feature_importances_` 语义一致）。

**CART 回归树**（变量缩写仅用于局部循环）：
- 节点 `_Node`：要么是叶（存 `value=mean(y_node)`），要么是内部节点（存 `feature, threshold, left, right`）。
- 最优分裂：对每个特征列升序排序后，以相邻不同取值的中点为候选阈值，用「父节点方差 − 加权子节点方差」即**方差下降（impurity reduction）**为打分，取全特征全阈值的最大者；用前缀和/前缀平方和在排序后 O(n) 扫描候选阈值。**并列增益的 tie-break 取「最小特征下标、最小阈值」的首遇者**（不依赖 `np.argmax` 在并列时的平台相关次序），保证「同输入同权重」的跨平台确定性（评审 P2-4）。
- 停止条件：`depth >= max_depth`、`n_node < 2*min_samples_leaf`、方差为 0、或无正增益分裂。
- 重要度累计：分裂时 `importance[feature] += n_node * reduction`（与 sklearn 的「加权不纯度下降」一致）。

**随机森林** `fit_rf_importances(X, y, *, n_estimators, max_depth, min_samples_leaf, seed) -> np.ndarray`：
- `rng = np.random.default_rng(seed)`；`acc = zeros(k)`。
- 每棵树：`idx = rng.integers(0, n, n)`（bootstrap 有放回采样）→ 在 `X[idx], y[idx]` 上建树并得到本树重要度 → **本树重要度归一到和为 1**（与 sklearn 一致）后累加进 `acc`。
- 返回 `acc / n_estimators`（跨树均值）。全零（如 y 近常数）则返回全零，交由上层 `_normalize_weights` 回落等权。
- 分裂特征集：用全部特征（对齐 sklearn `RandomForestRegressor` 回归默认 `max_features=1.0`；因子数 k 通常 2~12，无需子采样，且利于确定性）。

**梯度提升（XGBoost 代理）** `fit_gbdt_importances(X, y, *, n_estimators, max_depth, learning_rate, min_samples_leaf, seed) -> np.ndarray`：
- `pred = full(n, mean(y))`；`gain = zeros(k)`。
- 每轮：`residual = y - pred` → 在 `X, residual` 上建浅树（`max_depth` 默认 3），**累计总分裂增益**进 `gain` → `pred += learning_rate * predict_tree(node, X)`。
- 返回 `gain`（每特征总增益，对齐 xgboost 的 "gain" 重要度语义）。上层归一。无随机采样，天然确定性（`seed` 仅作签名一致性透传）。

**版本/默认值常量（单源，库后端与内置后端共用，评审 P1-2）**：
- `BUILTIN_RF_VERSION = "builtin-rf@1"`、`BUILTIN_GBDT_VERSION = "builtin-gbdt@1"`。
- `RF_DEFAULTS = {n_estimators:100, max_depth:6, min_samples_leaf:5}`、`GBDT_DEFAULTS = {n_estimators:100, max_depth:3, learning_rate:0.1, min_samples_leaf:5}`。**sklearn / xgboost 后端必须复用同一组默认**（见 §2.4），令「装库与否」只切换保真度与速度、不切换出形态显著不同的模型。
- 上限 `n_estimators<=400`、`max_depth<=12`（防纯 numpy 算力爆炸）。从 `config` 读取 `nEstimators`/`maxDepth` 覆盖默认，并 `min(值, 上限)` 收敛。
- **行数封顶（评审 P1-4）**：`BUILTIN_MAX_TRAIN_ROWS = 4000`。内置后端拟合前，若训练行数 `> BUILTIN_MAX_TRAIN_ROWS`，用 `np.random.default_rng(seed)` 做**确定性无放回下采样**到该上限（仅作用于拟合输入；样本内/外 IC 等指标仍在全量对齐集上计算）。把纯 numpy 最坏拟合成本按行数封顶，避免大区间下「卡死=不可用」。

**全函数性与错误契约（评审 P1-3）**：内置 `fit_rf_importances` / `fit_gbdt_importances` 对**已过 `_aligned` dropna 且行数 ≥ `MIN_TRAIN_SAMPLES`** 的输入是**全函数（total）**——退化情形（常数 y、零方差列、节点样本不足、无正增益分裂）一律返回**全零重要度**（由上层 `_normalize_weights` 回落等权），**绝不**抛 `LinAlgError`/`IndexError`/空切片等异常；仅当违反输入契约（如 `X`/`y` 行数不一致）时抛 `ValueError`，以便 `service._train_weights_guarded` 的 `except ValueError` 统一收敛为 **400**，不逃逸成 500。

### 2.4 训练器注册表重构（`training.py`）

把单后端 `Trainer` 升级为**有序多后端**：

```python
Kind = Literal["native", "library", "builtin"]  # 评审 P1-1：显式标签，消除 native↔builtin 歧义

@dataclass(frozen=True)
class Backend:
    fit: Callable[[np.ndarray, np.ndarray, dict], np.ndarray]
    kind: Kind                           # native=核心 numpy 训练器；library=sklearn/xgboost；builtin=内置树兜底
    probe_module: str | None             # None → 无需探测，恒可用（native / builtin）
    version: Callable[[], str | None]    # 后端版本标签
    def available(self) -> bool:
        return self.probe_module is None or importlib.util.find_spec(self.probe_module) is not None

@dataclass(frozen=True)
class Trainer:
    backends: tuple[Backend, ...]        # 有序：首个 available 胜出
    def available(self) -> bool: return any(b.available() for b in self.backends)
    def resolve(self) -> Backend:        # 取首个可用后端（内置兜底必有）
        return next(b for b in self.backends if b.available())
```

注册表（`kind` 显式标注，`available_trainers` 直接回报 `resolve().kind`，评审 P1-1）：
- `equal` / `ic_weight` / `linear`：单后端 `Backend(fit, kind="native", probe_module=None, version=()->None)`（保持现状，版本恒 `None`）。
- `random_forest`：`(Backend(_fit_rf_sklearn, "library", "sklearn", ()->f"sklearn {ver}"), Backend(_fit_rf_builtin, "builtin", None, ()->BUILTIN_RF_VERSION))`。
- `xgboost`：`(Backend(_fit_xgb_native, "library", "xgboost", ()->f"xgboost {ver}"), Backend(_fit_gbdt_builtin, "builtin", None, ()->BUILTIN_GBDT_VERSION))`。

**库后端复用单源默认（评审 P1-2）**：`_fit_rf_sklearn` 必须以 `RF_DEFAULTS` 构造 `RandomForestRegressor`（显式 `max_depth=6`、`min_samples_leaf=5`、`n_estimators=100`，而非现状的 `200/None/1`）；`_fit_xgb_native` 以 `GBDT_DEFAULTS` 构造 `XGBRegressor` 且**显式 `importance_type="gain"`**，使其 `feature_importances_` 与内置 GBDT 的总增益（gain）语义对齐。两后端读取 `config` 覆盖默认的入口与内置后端一致。

`train_weights()` 改造：取 `trainer.resolve()` 得后端 → `raw = backend.fit(x_train, y_train, config)` → `trainerVersion = backend.version()`；当 `resolve().kind == "builtin"` 时额外在 `note` 写「使用内置实现；安装 requirements-ml.txt 可启用 sklearn/xgboost 加速」。`available=False` 分支保留作前向兼容（现实中 rf/xgb 因内置兜底永真；仅未来若新增「纯探测无兜底」训练器才会触达）。删除旧 `_trainer_version`（版本改由 backend 提供）。

`available_trainers()` 改造：返回 `[{method, available, backend}]`，`backend = trainer.resolve().kind ∈ {"native","library","builtin"}`，供 UI 提示当前生效后端；rf/xgb 的 `available` 恒为 `true`。

> 实现注记：`nEstimators`/`maxDepth` 经 `config` 进入引擎、对两类后端默认生效；本迭代前端 `train.mutationFn` 仅发 `{ normWindow }`，**不**新增 UI 控件调参（避免范围蔓延，评审 P2-2），引擎读取入口仅供未来扩展。

### 2.5 端到端时序（sequence）

1. UI 进入「权重训练与组合」→ `useQuery(/factors/trainers)` → rf/xgb `available=true`，选项可点。
2. 选成员 + 选「随机森林/XGBoost」→ 点「训练权重」→ `POST /factor-sets/train`。
3. `service.train()` → `build_factor_matrix`（归一化契约不变）→ `train_weights(method=...)` → `trainer.resolve()` 选后端 → 内置/库算重要度 → 归一 `sum|w|=1` → 样本内/外 IC。
4. 返回 `TrainResult{weights, normStats, metrics, trainerVersion, provenance}`；UI 显示权重条 + `trainerVersion` 徽标。
5. 「保存组合」/「从组合新建策略」沿用现有逻辑（权重烘焙进成员，下游零改动）。

## 三、File / Module change plan（文件 / 模块变更计划）

| 文件 | 动作 | 一句话意图 |
|---|---|---|
| `services/quant/app/engine/factors/tree_ensemble.py` | 新建 | 纯 numpy CART 回归树 + RF/GBDT 重要度，确定性、恒可用、零新增强制依赖。**最优分裂扫描 / 建树须拆为命名辅助函数**，守 `CLAUDE.md` §二阈值（≤800 行/文件、≤50 行/方法、圈复杂度 ≤10，评审 P2-5）。 |
| `services/quant/app/engine/factors/training.py` | 修改 | `Trainer` 升级为多后端、`Backend` 增 `kind` 字段；rf/xgb 恒可用（库优先、内置兜底）；库后端复用单源默认 + xgb `importance_type="gain"`；`trainerVersion` 取实际后端；`available_trainers` 增 `backend=resolve().kind` 字段。 |
| `services/quant/app/api/factors.py` | 修改 | `factor_trainers` 路由 docstring 订正（rf/xgb 因内置兜底恒 `available=true`，不再「缺库即 false」，评审 P2-1）；响应透传新增 `backend` 字段无需 schema 改动。 |
| `services/quant/app/engine/factors/__init__.py` | 不改 | `available_trainers`/`train_weights` 已导出；`tree_ensemble` 由测试按子模块直接 import，无需新增导出。 |
| `services/quant/tests/test_factor_training.py` | 修改 | 改写 `test_missing_library_degrades_explicitly`（缺库→内置兜底 available=True、真实权重、`builtin-*` 版本）；新增「rf/xgb 恒可用且权重归一」用例。 |
| `services/quant/tests/test_tree_ensemble.py` | 新建 | 内置树集成单测：方差下降正确性、重要度非负且归一、确定性、单一信息特征可被识别。 |
| `services/quant/requirements-ml.txt` | 修改 | 顶部注释更新为「可选加速 / 高保真后端；缺库自动回落内置实现，功能恒可用」。 |
| `services/quant/app/agent/tools/factor_tools.py` | 修改 | `TrainFactorWeightsTool.description` 文案更新（rf/xgb 恒可用 + 可选库加速，不再「缺库如实回报」）。 |
| `apps/web/lib/factorTypes.ts` | 修改 | `TrainerInfo` 增可选 `backend?: "native" \| "library" \| "builtin"`（字符串字面量联合，非裸 `string`，呼应 `CLAUDE.md` §九，评审 P2-3）。 |
| `apps/web/components/Factor/FactorSetBuilder.tsx` | 修改 | 训练结果卡新增 `trainerVersion` 后端徽标（让用户看到内置/加速库）；**`trainerVersion` 为 `null`（核心训练器）时不渲染空徽标**（评审 P2-6）；禁用逻辑保留作前向守卫。 |

## 四、Interface design（接口设计）

- **`GET /factors/trainers`**（响应形状扩展，向后兼容）：
  `[{ "method": "random_forest", "available": true, "backend": "builtin" }, ...]`。`backend = trainer.resolve().kind ∈ {"native","library","builtin"}`（native=核心 numpy 训练器，library=sklearn/xgboost 在场，builtin=内置树集成兜底；由 `Backend.kind` **显式字段**派生，非靠 `probe_module` 猜测，评审 P1-1）。旧前端只读 `available`，缺省安全。
- **`POST /factor-sets/train`**（无破坏性变更）：请求体不变（`config` 仍为自由 dict，可选 `nEstimators`/`maxDepth` 由引擎读取并对库/内置后端默认生效，本迭代无 UI 暴露，评审 P2-2，无需 schema 改动）；响应 `trainerVersion` 现可为 `"builtin-rf@1"`/`"builtin-gbdt@1"`/`"sklearn 1.6.1"`/`"xgboost 2.1.3"`/`null`。
- **TS 类型**：`TrainMethod` 不变；`TrainerInfo` 增 `backend?: "native" | "library" | "builtin"`（字面量联合，评审 P2-3）；`TrainResult.trainerVersion` 类型不变（`string | null`，UI 渲染徽标时对 `null` 短路不渲染，评审 P2-6）。

## 五、Data model（数据模型）

- **内存形状**：`Backend{fit, kind, probe_module, version}`（`kind ∈ {"native","library","builtin"}`，评审 P1-1）、`Trainer{backends}`、`_Node{value | (feature,threshold,left,right)}`、重要度向量 `np.ndarray[k]`。
- **训练产物** `TrainResult{method, weights:dict[str,float], normStats:{normWindow,clip}, metrics:{icIn,icOut,r2In,r2Out,nTrain,nValid}, available, trainerVersion, note}`（字段不变，仅 `trainerVersion`/`note` 取值集扩展）。
- **持久化**：`factor_sets.model_json.trainerVersion` 可落 `builtin-*`，无 schema 迁移（JSON 列）。

## 六、Testing & acceptance criteria（测试与验收）

测试遵循 CLAUDE.md：先写能复现「灰色不可用」的测试，再改实现。后端 `pytest`（注意 services/quant 测试共享临时 DB，见 `aragon-quant-test-isolation`）；前端组件测试需 `NODE_ENV=test`（见 `aragon-web-vitest-node-env`）。

- **回归复现（核心）**：monkeypatch `find_spec→None` 后，`available_trainers()` 中 `random_forest`/`xgboost` 的 `available` 仍为 `True`（旧实现此处为 False，即 bug）。
- **内置训练正确**：缺库时 `train_weights(method="random_forest"/"xgboost")` 返回 `available=True`、`weights` 和 `sum|w|=1`、`trainerVersion` 以 `builtin-` 开头、`metrics.icIn/icOut` 非空。
- **确定性**：同输入两次内置训练 `weights` 完全相等。
- **单测树集成**：构造单一信息特征数据，该特征重要度显著最大；常数 y → 全零重要度（上层回落等权）；重要度恒非负。
- **退化输入不抛异常（评审 P1-3）**：常数 y / 零方差列 / 全相同特征等退化但合法（已 dropna、≥`MIN_TRAIN_SAMPLES`）输入下，内置 `fit_*_importances` **不抛**任何异常、返全零重要度；端到端经 `train_weights` 仍 `available=True` 并回落等权（绝不冒 500）。
- **行下采样确定性（评审 P1-4）**：行数 > `BUILTIN_MAX_TRAIN_ROWS` 时两次内置训练 `weights` 仍完全相等（seeded 下采样确定性），且单次训练在合理时限内返回。
- **库后端默认对齐（评审 P1-2）**：装了 sklearn/xgboost 时，构造的 `RandomForestRegressor`/`XGBRegressor` 超参取自 `RF_DEFAULTS`/`GBDT_DEFAULTS`（CI 若装可选依赖则断言，否则 skip 标注原因）。
- **库在场不回归**：装了 sklearn/xgboost 时仍走真实库，`trainerVersion` 为库版本（CI 若装可选依赖则覆盖；否则跳过标注 skip 原因）。
- **前端**：`trainerOf("random_forest")` 返回 `true` → 选项不再 `disabled`；训练结果卡渲染 `trainerVersion` 徽标。
- **端到端**：随机森林训练 → 保存组合 → 从组合新建 `factor_score` 策略成功（既有路径，验证不回归）。
- **验收 Definition of Done**：UI 上随机森林/XGBoost 可点击、可训练出权重、可保存并建策略；全部既有因子层测试通过；冻结包环境（无 sklearn/xgboost）下功能可用。

## 七、Risks & mitigations（风险与缓解）

| 风险 | 缓解 |
|---|---|
| 纯 numpy 树在大样本上慢（算力爆炸）。 | `n_estimators<=400`、`max_depth<=12` 硬上限 **+ `BUILTIN_MAX_TRAIN_ROWS=4000` 行下采样**（评审 P1-4）把最坏拟合成本按行数封顶；训练走 `run_in_threadpool` 不阻塞事件循环。 |
| 内置 `fit` 对退化输入抛非 `ValueError` → 逃逸成 500，破坏「显式 400」错误契约（评审 P1-3）。 | 内置 `fit_*_importances` 对已 dropna 且 ≥`MIN_TRAIN_SAMPLES` 的输入全函数：退化返全零重要度（上层回落等权），仅契约违例抛 `ValueError`，由 `_train_weights_guarded` 统一收敛 400。新增「退化输入不抛异常」单测兜底。 |
| 「用户选 xgboost 却拿到内置 GBDT」被视为静默降级（违 C1）。 | 不静默：`trainerVersion`（如 `builtin-gbdt@1`）+ `note` 经响应回传并在 UI 徽标显示；安装 `requirements-ml.txt` 即自动切真实库。 |
| 装库与否切换出不同模型形态（评审 P1-2）。 | 库后端复用单源默认（`RF_DEFAULTS`/`GBDT_DEFAULTS`）+ xgb `importance_type="gain"`，令切换只改保真度与速度、不改粗形态；版本号带 `@1` 区分算法迭代。 |
| 内置重要度与真实库数值不同，影响复现性。 | 内置后端固定 `seed=0` + tie-break「最小特征下标/最小阈值」确定性（评审 P2-4）；文档注明内置为「轻量近似」。 |
| 改 `Trainer` 结构波及核心训练器。 | 核心训练器改为单 `kind="native"` 后端、`version()` 返回 `None`，对外行为字节级不变；现有 `linear/equal/ic_weight` 测试应全绿。 |
| 前端旧响应无 `backend` 字段。 | `backend?` 设为可选字面量联合，UI 仅读 `available` 时缺省安全。 |

## 八、实施过程发现的方案缺陷（Subtask #2 回填）

1. **§三文件变更计划遗漏两处既有契约测试**（completeness）。除 `test_factor_training.py` 外，另有两个既有测试**硬编码了「rf/xgb 缺库即不可用」的旧契约**，本特性恒可用化后必然失败，方案未将其列入变更计划：
   - `services/quant/tests/test_factors_api.py::test_train_unavailable_returns_400`：原断言「缺 xgboost → 400 `FACTOR_TRAINER_UNAVAILABLE`」。
   - `services/quant/tests/test_factor_tools.py::test_train_tool_missing_lib_ok_false`：原断言「缺 xgboost → 工具 `ok=False`」。
   - **就地修正**：两者改写为「缺库 → 回落内置兜底（200 / `ok=True`、`trainerVersion` 以 `builtin-gbdt` 开头、权重归一 `sum|w|=1`）」，与 §六「内置训练正确」「退化不抛异常」验收一致。这是文件清单的补漏，未改变方案的设计意图（恒可用 + 不假权重 + 经 `trainerVersion` 如实回报）。

2. **`_fit_xgb_native` 的 `min_samples_leaf` 映射**（consistency，实现细节澄清）。`GBDT_DEFAULTS.min_samples_leaf=5` 在 `XGBRegressor` 无同名超参，最接近的语义是 `min_child_weight`（子节点最小样本权重和）。实现取 `min_child_weight=min_samples_leaf` 以贯彻 §2.4「两后端读取 config 覆盖默认的入口一致」的单源默认意图（装库与否不切换模型粗形态）。`importance_type="gain"`、`n_estimators`、`max_depth`、`learning_rate` 均按 §2.4 显式对齐。

## 评审结论（Review Verdict / Subtask #1）

**结论：有条件通过（Approved with Conditions）。**

方案根因定位准确（已逐条核对现实代码），双后端策略在「恒可用」「尊重冻结包体量约束」「不假权重 / 不静默降级（C1）」三者间取得了正确平衡，未触碰既有归一化契约与下游建策略路径，**无 P0 问题**。本轮发现的 **4 个 P1 已在 v2 正文就地修复**（`Backend.kind` 显式标签消歧、库/内置后端默认单源对齐、内置 `fit` 全函数化以守 400 错误契约、`BUILTIN_MAX_TRAIN_ROWS` 行下采样封顶最坏运行时），当前**无遗留 P0/P1**。

**放行条件（Subtask #2 代码开发须逐条落实，否则视为未完成）：**

1. **P2-1**：订正 `api/factors.py::factor_trainers` 路由 docstring，去除「rf/xgb 缺库即 false」过时表述。
2. **P2-2**：本迭代 `nEstimators/maxDepth` 仅为引擎侧默认，**不**新增前端调参 UI（避免范围蔓延）。
3. **P2-3**：`TrainerInfo.backend` 用字面量联合 `"native" | "library" | "builtin"`，非裸 `string`。
4. **P2-4**：内置最优分裂并列增益的 tie-break 取「最小特征下标 / 最小阈值」首遇者，保证跨平台确定性。
5. **P2-5**：`tree_ensemble.py` 守 `CLAUDE.md` §二阈值（≤800 行/文件、≤50 行/方法、圈复杂度 ≤10），分裂扫描与建树拆为命名辅助函数。
6. **P2-6**：内置兜底的 `trainerVersion` 徽标须真正在 UI 渲染，且 `trainerVersion=null`（核心训练器）时短路不渲染空徽标。

**验收对齐**：满足上述条件且 §六「测试与验收」全部通过（含 v2 新增的「退化输入不抛异常」「行下采样确定性」「库后端默认对齐」用例），即达成本特性的 Definition of Done——UI 上随机森林 / XGBoost 可点击、可训练出真实权重、可保存并新建 `factor_score` 策略，且在无 sklearn/xgboost 的冻结包环境下功能恒可用。

— 评审人：方案评审节点（Subtask #1），2026-06-30
