# 权重模型工作区（Weight Model Workspace）开发方案 v2

> Feature: `weight-model-workspace`
> 所属层：因子层（Factor Layer）· 因子实验室（Factor Lab）
> 文档状态：v2（已过 Subtask #1 评审，P0/P1 已在正文修复）
> 约定：字段 camelCase、时间一律秒级 UTC、错误体统一 `{error:{code,message}}`（沿用 `main.py` 异常处理器）；
> 复用因子层既有「单一归一化契约（causal z-score）」「方向冻结 long/short」「绝不假数据/绝不静默降级」三条铁律。

---

## 评审记录（Review Notes · Subtask #1）

> 评审视角：Feasibility（可建性）/ Completeness（边界与回滚）/ Consistency（与 CLAUDE.md 及代码现状一致）/ Right-sizing（范围合理性）。
> 评审已对照代码核验关键断言并全部属实：训练器常量 `MIN_TRAIN_SAMPLES=30`、`DEFAULT_SPLIT=0.7`、`TRAINERS`
> 字典（`engine/factors/training.py`）；`train_weights` 缺 rf/xgb 时走内置树兜底**恒可用**（`builtin-*`，绝不假权重）；
> `main.py` 现挂载 11 个路由（权重模型为第 12 个）；`LiveOrderAudit.account_id` 确为「指向 id 但不加外键」的普通整数；
> `db/repositories.py` 顶部 re-export `factor_repositories`；`store.ts` `version: 22` + `migrate` + 一次性消费 `factorEditTarget`；
> `lib/ws.ts` `ReconnectingWS` 经 `NEXT_PUBLIC_PY_WS_URL` 浏览器直连 `/api/ws/*`；`schemas._FactorRange`、`POST /factor-sets/train`、
> `MemberPalette/WeightBars/FactorSetList/FactorScoreChart` 均在场。整体为**纯加法、零破坏**设计，可行性成立。
>
> 下列为评审发现的具体问题；**全部 P1 已在 v2 正文落地修复**（见各条「修复」列指向的小节），P2 视情改进。

| # | 维度 | 严重度 | 问题 | 处置 |
|---|---|---|---|---|
| R-01 | Completeness | **P1** | 守门顺序未定义：`POST /weight-models/train` 在 413/400/429 判定与「自动建模型 + 落 running 记录」之间无先后约定，超并发/越界请求可能先落库再被拒，产生孤儿模型/记录。 | 已修：§3.1/§5.3 明确**先全部守门、后持久化**。 |
| R-02 | Completeness | **P1** | 自动建模型重复：builder「训练权重」无 `modelId`，每次点击都新建一个 `WeightModel`，导致模型列表污染。 | 已修：§5.3 增去重/复用语义；§3.4-A 回写 `modelId` 复用同一模型。 |
| R-03 | Completeness | **P1** | 取消缺接口：前端 `WeightTrainLive` 提供「取消」，但断 WS 不取消作业（设计明确作业续跑），无任何取消 API/manager 方法，按钮成空头支票。 | 已修：§5.3 增 `DELETE /weight-models/train/{jobId}`；§3.3 manager 增 `cancel()`；§6.2 run 状态增 `cancelled`。 |
| R-04 | Completeness | **P1** | 孤儿 running 记录：作业仅存进程内，进程重启/崩溃后 DB 里的 `running` 记录永远悬挂，详情页误显示「训练中」。 | 已修：§3.3/§4 增 lifespan 启动对账（仿 `_backfill_companion_strategies`），悬挂 `running`→`failed`。 |
| R-05 | Completeness | **P1** | 指针完整性：`run_cap` 剪枝或 DELETE run 可能删掉 `latest_run_id` 指向的记录，造成指针悬挂、`best_valid_ic` 陈旧。 | 已修：§5.2/§6.1 规定剪枝/删除**永不动指针记录**，并在变更后重算指针与 `best_valid_ic`。 |
| R-06 | Completeness | **P1** | 快作业/晚订阅悬挂：小区间训练可能在 WS 连上前就结束，客户端等不到任何事件而卡死。 | 已修：§3.1/§5.4 规定缓冲**含终态事件**，订阅即全量回放、若已终态立即收到 done/error 并关闭，绝不悬挂。 |
| R-07 | Completeness | **P1** | 整体样本不足：对齐 dropna 后 `n < MIN_TRAIN_SAMPLES`（或 `cut < MIN_TRAIN_SAMPLES` 致网格全空）时行为未定义，可能空成功或卡 running。 | 已修：§3.2/§5.4 规定发 `error`（`WEIGHT_TRAIN_INSUFFICIENT_SAMPLES`）并 finalize failed，绝不空成功。 |
| R-08 | Consistency | P2 | 引用精度：§3.3 称 manager 提供 `subscribe/snapshot/unsubscribe`，但 paper 中扇出/订阅实为 `paper/session.py`（per-session），manager 仅持注册表。 | 已修：§3.3 注明扇出抄 `session.py`，作业对象持订阅队列、manager 持作业注册表。 |
| R-09 | Feasibility | P2 | 等价精度：§3.2 的 `cut` 未写 `train_weights` 的 `max(1,min(n-1,…))` 钳制，边界处 `f=1.0` 权重可能与同步 train 漂移。 | 已修：§3.2 显式复用同一钳制公式。 |
| R-10 | Consistency | P2 | 网格来源不一致：§3.2 硬编码 6 点网格，config 又给 `weight_train_curve_steps`，二者关系未述。 | 已修：§3.2 注明 `learning_curve_steps` 由 config 步数派生，6 点为默认示例。 |
| R-11 | Completeness | P2 | `equal` 法曲线恒定：等权权重与样本无关，其 `validLoss/validIc` 跨 fraction 恒定（诚实但易被误读为「卡住」）。 | 已修：§3.2 注明此为构造性恒定，前端按「该法不随样本学习」标注而非显示异常。 |
| R-12 | Consistency | P2 | persist 版本号未点明：仅说「升版 + migrate 占位」，未写新版本号。 | 已修：§4 注明 `version: 22 → 23`，交接态瞬态不入 partialize。 |

---

## 一、概述（Overview）

因子实验室当前的「权重训练与组合」（`FactorSetBuilder`）把"训练权重"做成一次性的同步调用：用户选成员、选方法、点「训练权重」，
前端 `POST /factor-sets/train` 阻塞等待，后端一把算完返回 `weights / normStats / metrics`，结果只活在组件内存里——
**没有持久化的"模型"实体，没有训练历史，更没有训练过程的实时可视化**。用户无法回看"这个模型上次训出来什么样"，
也看不到训练过程中复合得分对前瞻收益的拟合是怎么随数据量推进而变化的（即俗称的 loss 曲线）。本方案要补齐的，正是把
"权重训练方法"从一个隐藏在组合搭建里的按钮，升级为一个**有专属面板、可管理、可回溯、可实时观测**的一等功能。

具体做三件事。其一，在因子实验室左侧工作区导航新增「**权重模型**」类别；点击后右侧呈现用户的全部权重模型列表，支持
**增删改查（CRUD）**，并可点进任一模型查看其**历史训练记录**（每次训练的方法、区间、样本内外 IC、loss/IC 曲线、训练权重、
溯源信息）。其二，把训练升级为**带实时进度与曲线的流式训练**：用户在「权重训练与组合」点「训练权重」时，不再原地阻塞，
而是**跳转到「权重模型」的实时训练视图**，通过 WebSocket 实时推送训练阶段、学习曲线（trainLoss/validLoss + trainIc/validIc）、
逐步收敛的权重条，训练完成即落库为一条训练记录。其三，训练产物可**一键回流**到「权重训练与组合」，沿用既有的"保存组合即
自动同步伴生交易策略"链路，保证现有 factor_set ⇄ factor_score 策略 1:1 同步契约**完全不变**。

设计原则是**纯加法、零破坏**：新增 `WeightModel` / `WeightTrainRun` 两张表与一套 `/api/weight-models/*` 路由（第 12 个路由），
不改 `factor_sets` 表结构、不改 `POST /factor-sets/train`（Agent 与其它调用点继续可用）、不动 factor_set⇄strategy 同步事务。
"loss 曲线"采用**真实的扩样本学习曲线**（在严格因果的时序切分上，按训练子样本递增重训并在留出验证段上度量复合得分与
前瞻收益的拟合损失），对线性/等权/IC 加权/随机森林/XGBoost 全部方法统一适用，**绝不伪造任何收敛曲线**，恪守因子层
"绝不假数据"的铁律。界面镜像设置中心 / 因子组合策略既有 shell（左列表 + 右详情、卡片化、设计 token、明暗主题零适配），
追求美观优雅、符合人机交互最佳实践。

---

## 二、命名与范围

- 新工作区 section key：`models`，中文标签「权重模型」，描述「模型管理 + 实时训练」。插入位置：`SECTIONS` 中
  「权重训练与组合」(`builder`) 之后、「因子组合策略」(`sets`) 之前。
- 新实体：`WeightModel`（权重模型，承载"成员快照 + 方法 + 配置 + 当前最佳产物指针"）、`WeightTrainRun`（一次训练记录，
  承载"状态 + 曲线 + 权重 + 指标 + 溯源"）。
- 模型与现有 `FactorSet` **解耦但可弱关联**：`WeightModel.factorSetId`（可空，仅溯源）；不引入任何破坏 factor_set 既有
  不变量的强约束。
- 不在本方案范围：Agent 工具暴露权重模型（后续可加）、跨实例分布式训练、GPU 训练、模型版本回滚到旧权重再训。

---

## 三、技术方案（Technical Design）

### 3.1 架构与数据流

```text
因子实验室「权重训练与组合」(FactorSetBuilder)
  └─点「训练权重」─> 写 store.weightTrainTarget + setFactorLabSection("models")
                      └─> 「权重模型」实时训练视图 (WeightTrainLive)
                            ├─ POST /api/weight-models/train  ──> WeightTrainManager.start_job()
                            │     守门优先（R-01）：校验成员 400 → 区间 413 → 并发 429 →（全过后才）解析/建模型
                            │     → 持久化 run(running) → 注册作业(此刻起即开缓冲) → 返回 {jobId,runId,modelId}
                            └─ WS  /api/ws/weight-train/{jobId} ──> 订阅 = 全量回放缓冲(含终态) + 实时流
                                  事件序列: run → phase* → curve* → result → done | error | cancelled
                                  缓冲含终态(R-06)：快作业先于连线结束时，订阅即收完整回放并立刻收到终态后关闭，绝不悬挂
WeightTrainManager (进程内单例，作业注册表，job-capped，lifespan 关闭)
  └─ async 作业协程:
       load_range_candles(真实 OKX) ─> build_factor_matrix(因果 z-score, 线程池)
       ─> 对 fraction 网格逐步 run_in_threadpool(fit_at_fraction) ─> 每步 enqueue curve 事件
       ─> 末步(frac=1.0)= 规范训练集全量拟合 = build_train_result (与 train_weights 等价)
       ─> finalize_run(succeeded, weights/normStats/metrics/curve/provenance) + 更新 model 指针
应用回流: WeightTrainLive 「应用到组合并返回」─> 写 store.weightTrainResult + setFactorLabSection("builder")
          └─> FactorSetBuilder 消费 weightTrainResult ─> setTrained(...) + 锁 normWindow（= 旧 train.onSuccess 效果）
          └─> 现有「保存组合 → 单事务同步伴生 factor_score 策略」链路完全不变
模型管理: 「权重模型」列表/详情 (WeightModelList / WeightModelDetail)
          GET/POST/PUT/DELETE /api/weight-models[...] + GET /api/weight-models/{id}/runs[...]
```

关键点：训练 CPU 工作交给 `run_in_threadpool`（沿用 `factors/service.py` 既有范式，绝不阻塞事件循环）；作业与 socket
**解耦**（作业归 `WeightTrainManager`，WS 断开只取消订阅、训练继续跑完并落库，历史持久可靠）；取数仍一律走真实 OKX
（`load_range_candles`，空区间 400、不可达 502），复用 `factors/service.py` 的进程内 K 线缓存。

### 3.2 "loss 曲线"如何真实生成（核心算法）

现有训练器里只有 `xgboost`/`random_forest` 天然有"迭代"概念，`linear`(岭回归闭式解)/`equal`/`ic_weight` 没有迭代步，
无法逐轮取 loss。为了对**全部方法统一**且**绝不伪造**，采用**扩样本学习曲线（expanding-window learning curve）**：

1. 复用现有 `build_factor_matrix`（与打分端逐字一致的因果滚动 z-score）得到 `X`，`forward_return_label` 得到因果前瞻收益 `y`，
   `_aligned` 统一 dropna。**整体样本守门（R-07）**：若对齐后 `n < MIN_TRAIN_SAMPLES`，与 `train_weights` 同口径直接判失败——
   作业发 `error` 事件（`code=WEIGHT_TRAIN_INSUFFICIENT_SAMPLES`，message 含实际行数与阈值）、`finalize_run(failed)`，**绝不空成功、绝不卡 running**。
2. 复用现有时序切分：训练段长度严格沿用 `train_weights` 的钳制公式 `cut = max(1, min(n-1, floor(n*split)))`（默认 split=0.7；
   **R-09**：用同一钳制以保证 `f=1.0` 步与同步 train 字节级等价），其后为留出验证段（**严格更晚的时间，无未来泄漏**）。
3. fraction 网格 `F` 由 `learning_curve_steps(cut)` 依 config `weight_train_curve_steps`（默认 6）派生（**R-10**：默认 6 步即
   `[0.2,0.35,0.5,0.65,0.8,1.0]`，末点恒为 1.0；硬编码列表仅为默认示例）。每个子样本须 ≥ `MIN_TRAIN_SAMPLES=30`，不足的早期步收缩网格剔除；
   若连最大步 `cut` 都 `< MIN_TRAIN_SAMPLES`，归入第 1 步的整体样本守门按 R-07 判失败。
   对每个 `f∈F`：在训练段前 `int(f*cut)` 行上拟合权重（同一 backend.fit + `_normalize_weights`），随后：
   - 计算复合得分 `composite = X·w`；
   - `trainLoss = mse(zscore(composite[:k]), zscore(y[:k]))`（标准化后均方误差 = `2*(1-pearson)`，落在 [0,4]，拟合越好越小）；
   - `validLoss = mse(zscore(composite[valid]), zscore(y[valid]))`（在留出验证段度量，真实泛化损失）；
   - `trainIc / validIc = spearman(composite, y)`（RankIC）。
   产出一个 `curvePoint = {step, fraction:f, nTrain:k, trainLoss, validLoss, trainIc, validIc}` 并实时下发。
4. 末步 `f=1.0` 即在**完整训练段**上拟合，等价于现有 `train_weights` 的规范训练集拟合 → 直接作为最终权重与指标来源
   （保证流式路径的最终 `TrainResult` 与 `POST /factor-sets/train` 对相同输入**字节级等价**，由回归测试守护）。

该曲线对所有方法都有意义（"喂更多历史，验证段拟合如何变化"），数据全部真实、严格因果、可复现；对 boosting 方法，曲线
反映的是数据规模维度的学习行为（逐迭代 staging 留作未来增强，不在 v1）。`trainLoss/validLoss` 任一段样本不足 2 行时
该指标记 `null`（绝不假装为 0），由前端按缺测渲染。

> **R-11（`equal` 法构造性恒定）**：等权权重与训练样本无关，故 `equal` 法的 `validLoss/validIc` 跨 fraction **恒为常数**——
> 这是诚实的真实行为（"等权不随数据学习"），并非 bug。前端在该法下以「此方法不随样本量学习，曲线恒定」的注记呈现，
> 不渲染成异常/卡住；`ic_weight`/`linear`/树模型的权重依训练子样本而变，曲线正常起伏。

### 3.3 关键代码路径

- 训练等价与曲线：`engine/factors/training.py` 新增
  - `learning_curve_steps(cut:int) -> list[float]`（生成 fraction 网格，保证最小样本）；
  - `fit_at_fraction(x, y, names, *, cut, frac, method, config) -> tuple[dict, dict]`（返回 `(weights, curvePoint)`，纯函数）；
  - `build_train_result(method, weights, names, x, y, cut, norm_stats, trainer_version, note) -> TrainResult`（从既有 `train_weights`
    抽出的"权重→指标→结果"装配，二者共用以保证等价）。`train_weights` 改为调用 `build_train_result`，对外行为不变。
- 作业编排：`factors/weight_train_manager.py` 的 `WeightTrainManager`（进程内单例，`get_weight_train_manager()`，作业注册表镜像
  `paper/manager.py`）。**职责切分（R-08，对齐 paper 真实分层）**：manager **只持作业注册表**，方法
  `start_job(payload)`、`get(job_id)`、`cancel(job_id)`（R-03）、`reconcile_dangling_runs()`（R-04，启动对账）、`shutdown()`；
  **asyncio.Queue 扇出 + 全量事件缓冲（含终态，供晚订阅/重连/快作业无损回放）落在 per-job 作业对象上**，对外暴露
  `subscribe()/snapshot()/unsubscribe()`——这与 paper 的 `subscribe/unsubscribe` 实位于 `paper/session.py`（非 `manager.py`）一致。
  - `cancel(job_id)`（R-03）：协作式取消——在每个 fraction 步之间检查取消标志，置位则停止后续步、`finalize_run(cancelled)`、发 `cancelled` 终态事件；
    已终态作业 cancel 幂等无副作用。
  - `reconcile_dangling_runs()`（R-04）：lifespan 启动时调用（仿 `main.py:_backfill_companion_strategies` 一次性 sessionmaker session），
    把上轮进程残留的 `running` 记录批量改判 `failed`（`error_code=WEIGHT_TRAIN_INTERRUPTED`），并按 R-05 重算受影响模型的指针/`best_valid_ic`。
  - 作业协程用一次性 sessionmaker session 落库（与 `_backfill_companion_strategies` 同范式）。
- 模型/记录 CRUD：`factors/weight_model_service.py`（model/run 校验 + 落库编排 + spec 组装），数据访问下沉
  `db/weight_model_repositories.py`（镜像 `db/factor_repositories.py`，并经 `db/repositories.py` re-export 以统一 `repo.X` 调用点）。
- 路由：`api/weight_models.py`（第 12 个路由），`main.py` 挂载并在 lifespan 关闭 manager。
- 前端：`components/Factor/WeightModelPanel.tsx`（section 根，按内部视图态在 列表/详情/实时训练 间路由），
  `WeightModelList/Detail/Editor/WeightTrainLive/TrainingCurveChart`；`lib/weightModel*.ts` + `lib/weightTrainWs.ts`；
  `store.ts` 增 `weightTrainTarget/weightTrainResult` 交接态；`FactorSetBuilder.tsx` 改"训练权重"为跳转 + 消费回流。

### 3.4 操作时序（Sequence）

**A. 从组合搭建发起训练（主链路）**
1. `FactorSetBuilder` 点「训练权重」→ 校验成员通过 → `setWeightTrainTarget({members,method,config:{normWindow},horizon,instId,bar,startTs,endTs,name,modelId?})` → `setFactorLabSection("models")`。
2. `WeightModelPanel` 检测到 `weightTrainTarget` → 切到 `WeightTrainLive` 子视图并**消费一次性**（立即清空，防重入，模式同 `factorEditTarget`）。
3. `WeightTrainLive` → `POST /api/weight-models/train`（去重见 §5.3 R-02：有 `modelId` 复用；无 `modelId` 但有 `factorSetId` 按组合复用-或-建；皆无才新建）→ 得 `{jobId,runId,modelId}` → 开 WS `/api/ws/weight-train/{jobId}`。
   - **R-02 回写**：拿到后端返回的 `modelId` 立即经 store 记入 builder 关联（`weightTrainResult.modelId` / builder 侧 `boundModelId`），使**同一搭建会话内反复点「训练权重」复用同一模型**（更新其训练历史），不再每次新建，杜绝模型列表污染。
4. 流式渲染：`run`（建 run）→ `phase`（loading/building/fitting/evaluating）→ 多个 `curve`（实时画 loss/IC 曲线 + 更新权重条）→ `result`（最终权重/指标）→ `done`。
5. 用户点「应用到组合并返回」→ `setWeightTrainResult({weights,normStats,metrics,provenance,method,modelId,runId})` → `setFactorLabSection("builder")`。
6. `FactorSetBuilder` 消费 `weightTrainResult`：`setTrained(...)`、`retrainedRef=true`、`setThresholds(normWindow=锁定)`（与旧 `train.onSuccess` 效果一致）→ 既有「保存组合 → 同步伴生策略」流程零改动。

**B. 在「权重模型」直接管理**
- 列表：`GET /api/weight-models` → 卡片（名称/标的·周期/方法/成员数/最佳验证 IC/最近训练时间/启用）→ 新建（Editor）/编辑/删除（二次确认）/训练（直接进 `WeightTrainLive`）/查看（Detail）。
- 详情：`GET /api/weight-models/{id}` + `GET /api/weight-models/{id}/runs` → 模型概要 + 训练历史表（状态/方法/区间/IC/时间）→ 选中某 run → `GET /api/weight-models/{id}/runs/{runId}` 取曲线 → `TrainingCurveChart` + `WeightBars` 回放历史训练。

---

## 四、文件 / 模块变更计划（File / Module Change Plan）

### 后端（services/quant/app）

| 文件 | 动作 | 意图（一行） |
|---|---|---|
| `db/models.py` | 修改 | 新增 `WeightModel`、`WeightTrainRun` 两个 ORM 模型（秒级 UTC、JSON 文本列）。 |
| `db/weight_model_repositories.py` | 新建 | 权重模型 + 训练记录 CRUD（list/get/create/update/delete、add_run/finalize_run/list_runs/get_run/prune_runs/delete_run/repoint_model）；**R-05**：`prune_runs`/`delete_run` 永不删 `latest_run_id` 指向记录，变更后 `repoint_model` 重算指针与 `best_valid_ic`；`reconcile_dangling_runs` 批量改判残留 running。 |
| `db/repositories.py` | 修改 | 顶部 re-export 权重模型 repo 符号，统一 `repo.X` 调用点（镜像 factor repo re-export）。 |
| `schemas.py` | 修改 | 新增 `WeightModelCreateReq/UpdateReq`、`WeightTrainStartReq`、`weight_model_out`、`weight_train_run_out` 序列化器与 curve 点结构。 |
| `engine/factors/training.py` | 修改 | 抽出 `build_train_result`、新增 `learning_curve_steps`、`fit_at_fraction`；`train_weights` 复用 `build_train_result` 保持等价。 |
| `engine/factors/__init__.py` | 修改 | 导出 `build_train_result/learning_curve_steps/fit_at_fraction`。 |
| `factors/weight_model_service.py` | 新建 | 模型/记录 CRUD 校验编排 + spec 组装 + 训练入参规范化（与 manager 协作）。 |
| `factors/weight_train_manager.py` | 新建 | `WeightTrainManager` 进程内作业管理（start/get/subscribe/snapshot/shutdown）+ async 流式训练作业 + 持久化落库。 |
| `api/weight_models.py` | 新建 | 第 12 个路由：模型 CRUD、训练记录读、`POST /weight-models/train`、`DELETE /weight-models/train/{jobId}`（取消，R-03）、`WS /ws/weight-train/{jobId}`。 |
| `main.py` | 修改 | 挂载 `weight_models.router`（`/api`）；lifespan **启动**调 `reconcile_dangling_runs()`（R-04，悬挂 running→failed）、**关闭** `get_weight_train_manager().shutdown()`。 |
| `config.py` | 修改 | 新增 `max_weight_train_jobs`（默认 4）、`weight_train_curve_steps`（默认 6）、`weight_model_run_cap`（默认 50）。 |

### 前端（apps/web）

| 文件 | 动作 | 意图（一行） |
|---|---|---|
| `lib/weightModelTypes.ts` | 新建 | `WeightModel/WeightTrainRun/TrainCurvePoint/WeightTrainTarget/WeightTrainResult/TrainStreamEvent` 类型。 |
| `lib/weightModelApi.ts` | 新建 | 权重模型 REST 客户端（复用 `ApiRequestError` 解包）。 |
| `lib/weightModel.ts` | 新建 | 纯助手：曲线几何、最佳/最近 run 选取、模型摘要、交接载荷构造（带测试）。 |
| `lib/weightTrainWs.ts` | 新建 | 训练流 WS 订阅器（复用 `ReconnectingWS`，类型化事件回调）。 |
| `lib/store.ts` | 修改 | 增 `weightTrainTarget/weightTrainResult`（瞬态，**不入 partialize**）+ `boundModelId`（R-02 复用）+ setter；persist **`version: 22 → 23`** + migrate 显式占位（R-12）；`factorLabSection` 可取 `models`。 |
| `components/Factor/FactorLab.tsx` | 修改 | `SECTIONS` 增「权重模型」，渲染 `WeightModelPanel`。 |
| `components/Factor/WeightModelPanel.tsx` | 新建 | section 根：按内部视图态（list/detail/live）路由，并消费 `weightTrainTarget`。 |
| `components/Factor/WeightModelList.tsx` | 新建 | 模型列表 + CRUD（镜像 `FactorSetList`）。 |
| `components/Factor/WeightModelDetail.tsx` | 新建 | 模型概要 + 训练历史表 + 历史 run 曲线回放。 |
| `components/Factor/WeightModelEditor.tsx` | 新建 | 新建/编辑模型表单（成员选择复用 `MemberPalette`、方法选择复用既有 METHODS）。 |
| `components/Factor/WeightTrainLive.tsx` | 新建 | 实时训练视图：WS 流、进度/阶段、loss/IC 曲线、权重条收敛、取消、应用回流。 |
| `components/Factor/TrainingCurveChart.tsx` | 新建 | 多序列自绘 SVG 曲线（trainLoss/validLoss + trainIc/validIc，设计 token，明暗零适配）。 |
| `components/Factor/FactorSetBuilder.tsx` | 修改 | 「训练权重」改为写 `weightTrainTarget` + 跳转；新增消费 `weightTrainResult` 水合 `trained`。 |

### 测试

| 文件 | 动作 | 意图 |
|---|---|---|
| `services/quant/tests/test_weight_model_repo.py` | 新建 | 模型/记录 CRUD、prune、指针更新。 |
| `services/quant/tests/test_weight_train_streaming.py` | 新建 | `fit_at_fraction` 真实损失/IC、曲线长度、因果无泄漏、流式末步与 `train_weights` 等价。 |
| `services/quant/tests/test_weight_models_api.py` | 新建 | 模型/记录 REST、train 启动（jobId/runId/modelId）、413/400 守门。 |
| `services/quant/tests/test_weight_train_ws.py` | 新建 | WS 事件序列、重连回放、断线仍落库（历史持久）。 |
| `apps/web/lib/weightModel.test.ts` | 新建 | 纯助手单测（曲线几何/最佳 run/交接构造）。 |
| `apps/web/components/Factor/WeightModelPanel.test.tsx` | 新建 | 列表/详情/实时视图与 builder 交接冒烟（NODE_ENV=test）。 |

---

## 五、接口设计（Interface Design）

所有 REST 走 Next 代理 `/api/py/*` → FastAPI `/api/*`；字段 camelCase，时间秒级 UTC。

### 5.1 模型 CRUD（REST）

- `GET /api/weight-models?enabled=<bool?>` → `WeightModelOut[]`（按 `updatedTs` 降序由前端排序；后端按 id 升序返回）。
- `POST /api/weight-models` → 201 `WeightModelOut`。Body `WeightModelCreateReq`：
  `{ name, instId, bar, members:FactorMemberIn[1..12], method, config?, horizon?, notes?, factorSetId?, enabled? }`。
- `GET /api/weight-models/{id}` → `WeightModelOut`（含 `latestRun:WeightTrainRunOut|null` 概要）；不存在 404。
- `PUT /api/weight-models/{id}` → `WeightModelOut`。Body `WeightModelUpdateReq`（部分字段，`None=不改`）。
- `DELETE /api/weight-models/{id}` → 204（连带删除其全部训练记录，单事务）；不存在 404。

### 5.2 训练记录（REST）

- `GET /api/weight-models/{id}/runs?limit=<int?>&status=<str?>` → `WeightTrainRunOut[]`（含曲线摘要计数，不含完整 curve）。
- `GET /api/weight-models/{id}/runs/{runId}` → `WeightTrainRunOut`（**含**完整 `curve`、`weights`、`metrics`、`provenance`）；不存在 404。
- `DELETE /api/weight-models/{id}/runs/{runId}` → 204（可选；不删 running 记录，409）。**R-05 完整性**：删后若该 run 曾被
  `latest_run_id` 指向或贡献 `best_valid_ic`，单事务内 `repoint_model` 重算指针（取剩余最近 succeeded run）与 `best_valid_ic`
  （取剩余 succeeded 最大值，无则 `null`），杜绝悬挂指针/陈旧冗余。

### 5.3 启动训练（REST）

- `POST /api/weight-models/train` → `{ jobId:str, runId:int, modelId:int }`。Body `WeightTrainStartReq`（继承 `_FactorRange`）：
  `{ instId, bar, startTs, endTs, members[1..12], method, config?, horizon?, modelId?, factorSetId?, name?, maxBars? }`。
  - **守门优先（R-01）——严格按序，全过后才有任何持久化副作用**：① 成员非法 → 400 `FACTOR_TRAIN_FAILED`；
    ② 区间过长复用 `factor_train_max_bars` → 413 `FACTOR_TRAIN_TOO_LARGE`；③ 作业数超 `max_weight_train_jobs` → 429 `WEIGHT_TRAIN_BUSY`；
    ④ 整体样本不足在取数/对齐后由作业内发 `error`（R-07）。**任一守门命中即返回错误，绝不先建模型/落 run 再拒**（杜绝孤儿）。
  - **模型解析去重（R-02）**：有 `modelId` → 复用并刷新其 spec；无 `modelId` 但有 `factorSetId` → 按 `factorSetId` 复用-或-新建
    （同一组合不重复造模型）；二者皆无 → 用 `name`（缺省回落 `成员摘要`）+ spec 新建，并把 `modelId` 回传供 builder 记忆复用（§3.4-A 步 3）。
  - 缺 rf/xgb 库时因内置树兜底**恒可用**（与 `train_weights` 现状一致，`trainerVersion=builtin-*`，绝不假权重、绝不静默降级）。
- `DELETE /api/weight-models/train/{jobId}` → 202 `{ jobId, status:"cancelling" }`（**取消，R-03**）：转 `WeightTrainManager.cancel(jobId)`
  协作式取消（步间检查标志），作业 `finalize_run(cancelled)` 并发 `cancelled` 终态事件；jobId 未知或已终态 → 幂等 200/404，绝不抛。

### 5.4 训练实时流（WebSocket）

- `WS /api/ws/weight-train/{jobId}`（前端经 `NEXT_PUBLIC_PY_WS_URL + /api/ws/weight-train/{jobId}`，复用 `ReconnectingWS`）。
- 连接即**全量回放**缓冲事件（晚订阅/重连无损，模式同 `/ws/paper/{id}`），随后实时推送。**R-06 缓冲含终态**：缓冲自作业注册即开启、
  保留从 `run` 到终态（`done`/`error`/`cancelled`）的**全部**事件（有界——`phase` 数 + ≤`weight_train_curve_steps` 个 `curve` + 1 终态）；
  **若订阅时作业已终态**，客户端先收完整回放、紧接收到缓冲的终态后服务端关闭，**绝不悬挂**（解决快作业先于连线结束）。事件（均 `{type,data}`）：

| type | data | 说明 |
|---|---|---|
| `run` | `{runId, modelId, status:"running"}` | 训练记录已建。 |
| `phase` | `{phase:"loading"\|"building"\|"fitting"\|"evaluating", pct:0..100}` | 阶段进度。 |
| `curve` | `TrainCurvePoint` | 实时学习曲线点（逐 fraction 推送）。 |
| `result`| `{method,weights,normStats,metrics,trainerVersion,note,provenance}` | 最终训练产物（= 同步 train 等价）。 |
| `done` | `{runId, finishedTs}` | 正常结束（随后服务端关闭）。 |
| `cancelled` | `{runId, finishedTs}` | 用户取消（记录置 `cancelled`，随后关闭，R-03）。 |
| `error`| `{code,message,runId?}` | 失败（记录置 `failed`，随后关闭）。整体样本不足时 `code=WEIGHT_TRAIN_INSUFFICIENT_SAMPLES`（R-07）。 |

`TrainCurvePoint = { step:int, fraction:float, nTrain:int, trainLoss:number\|null, validLoss:number\|null, trainIc:number\|null, validIc:number\|null }`。

---

## 六、数据模型（Data Model）

### 6.1 `weight_models` 表（`WeightModel`）

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | Integer PK | |
| `name` | String(128) | 模型名。 |
| `inst_id` | String(32) | 训练标的。 |
| `bar` | String(8) | 周期。 |
| `members_json` | Text | 自包含成员快照 `[{factorKey,params,weight,direction}]`（方向冻结 long/short）。 |
| `method` | String(24) | equal\|ic_weight\|linear\|random_forest\|xgboost。 |
| `config_json` | Text | `{normWindow,clip,split,l2,horizon,treeParams...,thresholds?}`。 |
| `horizon` | Integer | 前瞻收益根数（默认 6）。 |
| `source` | String(16) | builder\|manual（来源标识）。 |
| `factor_set_id` | Integer NULL | 弱关联来源/目标组合 id（仅溯源，**不加外键**，组合可删留痕）。 |
| `latest_run_id` | Integer NULL | 最近成功训练记录 id（**普通整数、不加外键**，规避 model↔run 循环依赖，仿 `LiveOrderAudit.account_id`）。**R-05**：剪枝/删除该记录前须先 `repoint_model` 改指剩余最近 succeeded run（无则置 `null`），绝不留悬挂指针。 |
| `best_valid_ic` | Float NULL | 反范式冗余：历史最佳验证 IC（列表排序/展示用）。 |
| `notes` | String(256) | 备注。 |
| `enabled` | Boolean | 启停。 |
| `created_at`/`updated_at` | DateTime | `_utcnow` / `onupdate`。 |

### 6.2 `weight_train_runs` 表（`WeightTrainRun`）

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | Integer PK | |
| `model_id` | Integer FK→`weight_models.id` index | 归属模型（cascade delete 由 service 单事务保证）。 |
| `status` | String(16) | running\|succeeded\|failed\|cancelled（cancelled = 用户取消，R-03）。 |
| `method` | String(24) | 训练方法快照。 |
| `inst_id`/`bar` | String | 区间快照。 |
| `start_ts`/`end_ts`/`horizon` | Integer | 训练窗口快照（秒级 UTC）。 |
| `config_json` | Text | 训练配置快照。 |
| `curve_json` | Text | `TrainCurvePoint[]`（loss/IC 学习曲线）。 |
| `weights_json` | Text | 归一化权重 `{name:weight}`。 |
| `norm_stats_json` | Text | `{normWindow,clip}`（归一化契约参数，**不存固定 mean/std**）。 |
| `metrics_json` | Text NULL | `{icIn,icOut,r2In,r2Out,nTrain,nValid}`。 |
| `provenance_json` | Text | `{instId,bar,startTs,endTs,horizon}`。 |
| `trainer_version` | String(64) NULL | 实际生效后端（库版本/`builtin-*`/`None`）。 |
| `note` | String(256) | 内置兜底提示等。 |
| `error_code`/`error_message` | String | 失败时填（成功为空）。 |
| `started_ts`/`finished_ts` | Integer/NULL | 秒级 UTC。 |

> 建表：两模型继承 `Base`，`init_db()` 的 `create_all` 启动自动建表（新库/老库均加法补，无数据迁移，与 `AppSetting` 同口径）。

### 6.3 前端类型（`lib/weightModelTypes.ts`）

镜像后端 camelCase：`WeightModel`、`WeightTrainRun`、`TrainCurvePoint`、`WeightTrainTarget`（交接载荷）、
`WeightTrainResult`（回流载荷）、`TrainStreamEvent`（`run|phase|curve|result|done|error` 联合类型）。

---

## 七、测试与验收标准（Testing & Acceptance Criteria）

### 7.1 单元 / 集成测试（先写复现，再改业务；高风险路径用真实集成）

- **训练等价性（关键）**：对同一 `(members,method,config,window,horizon)`，流式作业最终 `result` 与 `POST /factor-sets/train`
  的 `weights/normStats/metrics` **逐字段相等**（守护"不破坏既有契约"）。
- **曲线真实且因果**：`fit_at_fraction` 在递增子样本上产出的 `validLoss/validIc` 全部来自留出验证段；构造已知线性数据时
  loss 随样本量整体下降、IC 同号；样本不足段 loss 记 `null`（不为 0）。断言验证段时间严格晚于训练段（无泄漏）。
- **守门 + 顺序（R-01）**：超 `factor_train_max_bars` → 413；成员非法 → 400；作业超并发上限 → 429；缺 rf/xgb 库仍 200（内置兜底，`trainerVersion=builtin-*`）。
  断言守门命中时**不产生任何模型/run 落库**（无孤儿：守门前后模型/记录计数不变）。
- **整体样本不足（R-07）**：构造 `n < MIN_TRAIN_SAMPLES` 的区间 → 作业发 `error`（`code=WEIGHT_TRAIN_INSUFFICIENT_SAMPLES`）、run 终为 `failed`，**绝不空成功、绝不卡 running**。
- **模型去重（R-02）**：同 `factorSetId` 连续两次 train 复用同一 `modelId`（模型数 +1 而非 +2，训练历史 +2）；显式 `modelId` 复用刷新。
- **持久化/历史**：`start_job` 落 running 记录；成功 finalize 为 succeeded 且写曲线/权重/指标 + 更新 `latest_run_id/best_valid_ic`；
  失败 finalize 为 failed 且带 `error_code`；超 `weight_model_run_cap` 触发 prune 且**显式 log 丢弃条数**（绝不静默截断）。
- **指针完整性（R-05）**：prune/DELETE 命中 `latest_run_id` 指向或 `best_valid_ic` 贡献者时，单事务 `repoint_model` 重算指针与冗余；删尽后置 `null`，无悬挂指针。
- **启动对账（R-04）**：预置一条残留 `running` 记录 → lifespan 启动 `reconcile_dangling_runs()` 后改判 `failed`（`error_code=WEIGHT_TRAIN_INTERRUPTED`）并按 R-05 修指针。
- **取消（R-03）**：训练中途 `DELETE /weight-models/train/{jobId}` → 作业停步、run 终为 `cancelled`、收 `cancelled` 终态；对未知/已终态 job 幂等无副作用。
- **WS**：事件序列 `run→phase→curve+→result→done`；中途断线后训练继续跑完、记录仍 succeeded（断线重连可回放/可在详情页看到）。
  **快作业/晚订阅（R-06）**：作业已终态后才订阅 → 收完整回放 + 缓冲终态后关闭，客户端不悬挂。
- **CRUD**：模型增删改查；删模型连带删其训练记录（单事务，`get /runs` 返回空）；模型不存在统一 404。
- **前端纯助手**：曲线几何、最佳/最近 run 选取、交接载荷构造的 vitest（`NODE_ENV=test`）。
- **前端组件冒烟**：「权重模型」列表渲染、详情历史表、实时视图按 mock WS 事件画曲线；`FactorSetBuilder` 点「训练权重」
  写 `weightTrainTarget` 并切到 `models`；消费 `weightTrainResult` 后 `trained` 就绪、保存按钮解禁。

### 7.2 验收标准（Definition of Done，面向 Subtask #2/#3）

1. 因子实验室左侧新增「权重模型」类别，点击右侧呈现模型列表，支持新建/编辑/删除/查看，UI 与既有 shell 一致、美观优雅。
2. 点进任一模型可见其**历史训练记录**（状态/方法/区间/样本内外 IC/时间），可选中某次回放其 loss/IC 曲线与权重。
3. 「权重训练与组合」点「训练权重」**跳转**到「权重模型」实时训练视图，**实时**显示阶段进度、loss/IC 曲线、收敛中的权重；
   训练完成落库为一条记录。
4. 训练产物可一键回流「权重训练与组合」，保存组合 → 自动同步伴生 factor_score 策略链路**与现状一致、不回归**。
5. `POST /factor-sets/train`、factor_set CRUD、factor_score 策略回测/对比/Walk-forward 路径**全部不回归**。
6. `pnpm test`（web vitest，`NODE_ENV=test`）、`pytest`（quant）全绿；前端 `tsc`/lint 通过；新文件均守 800 行/50 行清洁度阈值。
7. 项目可启动/编译（`scripts/dev.py`），新表自动建、老库零迁移。

---

## 八、风险与缓解（Risks & Mitigations）

| 风险 | 影响 | 缓解 |
|---|---|---|
| "loss 曲线"被误做成伪造的平滑收敛 | 违背因子层"绝不假数据"铁律 | 采用**真实扩样本学习曲线**（验证段度量真实损失），样本不足记 `null`；闭式方法同样产真实曲线；测试断言曲线来自真实数据。 |
| 学习曲线引入未来泄漏 | 评估失真、契约破坏 | 复用既有因果时序切分：子样本取训练段最早部分、验证段严格更晚；复用 `build_factor_matrix` 同一因果 z-score；测试守护。 |
| 流式最终结果与同步 train 漂移 | 双路径不一致、组合权重不可信 | 抽 `build_train_result` 单源装配，`f=1.0` 步即规范训练集全量拟合；回归测试断言等价。 |
| 长训练阻塞事件循环 / socket hang up | 服务卡死、前端超时 | 复用 `factor_train_max_bars` 413 守门；每步 `run_in_threadpool`；作业与 socket 解耦（manager 持有），并发上限 429。 |
| 渲染进程刷新致作业成孤儿 / 占满配额 | 资源泄漏、训练丢失 | manager 作业注册表 + WS 重连回放 + 记录持久化（断线照样跑完落库）；作业完成后 TTL 清理；lifespan 关闭 manager。 |
| 训练记录无界增长 | DB 膨胀 | 每模型 `weight_model_run_cap` 上限，超出按时间剪枝并**显式 log 丢弃数**（绝不静默截断，呼应"无静默上限"约定）。 |
| model↔run 循环外键 / 迁移复杂 | 建表失败、删除受阻 | `latest_run_id` 用普通整数不加外键；`create_all` 加法建表零迁移；删模型连带删记录走 service 单事务（与本机 SQLite 未开外键一致）。 |
| 破坏 factor_set ⇄ strategy 1:1 同步 | 交易策略错乱 | 权重模型与组合解耦，仅 `factorSetId` 弱关联；不改 `factor_sets`/同步事务；回流仍走既有 builder 保存链路。 |
| store persist 版本迁移遗漏默认 | 水合到陈旧/缺省态 | 交接态为瞬态（不入 partialize）；persist `version` 升级 + migrate 显式占位（与既有 22 个版本同范式）。 |
| 缺 rf/xgb 库时静默降级 | 训练结果不可信 | 沿用现状：内置纯 numpy 树集成兜底恒可用，`trainerVersion=builtin-*` + `note` 如实回报，绝不假权重。 |
| 守门后才落库未约定 → 孤儿模型/记录（R-01） | 列表污染、配额虚耗 | 守门严格按序 400→413→429，全过后才解析/建模型、落 run、注册作业；任一命中即返回，绝无持久化副作用。 |
| builder 重复训练造重复模型（R-02） | 模型列表污染 | 解析去重：`modelId`/`factorSetId` 复用-或-建；新建后回传 `modelId` 供 builder 记忆，同会话反复训练复用同模型。 |
| DB 残留 `running` 记录悬挂（R-04） | 详情页假「训练中」、状态不可信 | lifespan 启动 `reconcile_dangling_runs()` 改判 `failed`（`WEIGHT_TRAIN_INTERRUPTED`）并按 R-05 修指针（仿 `_backfill_companion_strategies`）。 |
| 剪枝/删除击中指针记录（R-05） | 悬挂 `latest_run_id`、陈旧 `best_valid_ic` | 单事务内永不删指针记录或删前先 `repoint_model` 重算指针/冗余，删尽置 `null`，测试守护。 |
| 快作业先于 WS 连线结束（R-06） | 前端永久 loading 悬挂 | 缓冲自作业注册即开、含终态全量事件；订阅即全量回放，已终态则立刻收终态后关闭，绝不悬挂。 |
| 取消按钮无后端（R-03） | 空头交互、作业不可中止 | `DELETE /weight-models/train/{jobId}` → `cancel()` 协作式步间取消，run 终 `cancelled` + 终态事件，幂等安全。 |
| 整体样本不足行为未定（R-07） | 空成功 / 卡 running | 对齐后 `n<MIN_TRAIN_SAMPLES` 即发 `error`（`WEIGHT_TRAIN_INSUFFICIENT_SAMPLES`）+ finalize failed，绝不空成功。 |

---

## 九、对下游实现者的提示（Implementation Notes）

- 严守因子层三铁律：单一归一化契约（`build_factor_matrix` 与打分端 `compose_score` 同 `normWindow/clip`）、方向冻结 long/short、
  绝不假数据 / 绝不静默降级。
- 复用而非另起：取数 `load_range_candles`、线程池 `run_in_threadpool`；**WS 扇出/订阅抄 `paper/session.py` 的 `subscribe/unsubscribe/_broadcast`
  + asyncio.Queue（R-08：扇出在 session 而非 manager）**，作业注册表抄 `paper/manager.py`，WS 端点抄 `api/paper.py:ws_paper`；
  列表/详情 shell 抄 `FactorSetList`、权重条 `WeightBars`、曲线几何可借 `lib/factorSeries.ts`/`FactorScoreChart` 思路扩成多序列。
- 交接态 `weightTrainTarget/weightTrainResult` 的"一次性消费 + 立即清空 + 防重入"严格照 `FactorSetBuilder` 消费 `factorEditTarget` 的写法。
- 新建文件务必守 800 行/方法 50 行/参数 ≤5/嵌套 ≤4 阈值；DB 写聚合传参用 frozen dataclass（仿 `FactorSetValues`）。

---

## 实施过程发现的方案缺陷（Issues Found During Implementation · Subtask #2）

> 实现阶段对照代码现状发现以下与方案的偏差；已按修正方案落地，记录于此供回填评审。

- **I-01（P1 · 已修）re-export 命名冲突**：§3.3/§四 要求权重训练记录的数据访问函数经 `db/repositories.py`
  re-export 以统一 `repo.X`。但既有 `repositories.py` 已有 `get_run(session, run_id)`、`list_runs(session, *, strategy_id=…)`、
  `finalize_run(session, run_id, *, status=…)`（**策略运行**用），与本方案的 `get_run/list_runs/finalize_run`（权重训练记录，
  签名不同）**同名冲突**——re-export 在后会**遮蔽**既有符号，导致 `api/runs.py`、`paper.py`、`walk_forward` 等调用
  `repo.get_run/list_runs` 时 `TypeError`（实测全量 pytest 由此红 33 例）。**修正**：权重训练记录的全部仓库函数 /
  dataclass 一律加 `weight` 限定名（`add_weight_run`/`get_weight_run`/`list_weight_runs`/`finalize_weight_run`/
  `delete_weight_run`/`repoint_weight_model`/`reconcile_dangling_weight_runs`/`WeightRunStart`/`WeightRunOutcome`），
  彻底消除遮蔽；模型 CRUD 本就带 `weight_model` 限定名，无冲突。对外 REST/WS 契约、service 公开方法名不受影响。

- **I-02（P3 · 已处理）路由计数漂移**：§四 称权重模型为「第 12 个路由」，但实现期发现并行任务已新增
  `api/workspace.py`（watchlist-auto-trade-persistence）占据第 12 槽。本路由改挂为其后一个 `include_router`，
  不扰动既有路由顺序；功能与契约不变，仅文档中的序号标签过时。

---

## 评审结论（Review Verdict · Subtask #1）

**结论：有条件通过（Approved with Conditions）。**

本方案为**纯加法、零破坏**设计，技术路径清晰、与既有架构高度自洽：流式训练镜像 `paper` 的 manager/session 分层，
取数/线程池/守门/WS 全部复用现网范式；"loss 曲线"以**真实扩样本学习曲线**实现，严守因子层"绝不假数据"铁律；
不动 `factor_sets` 表与 factor_set ⇄ strategy 1:1 同步契约。评审核验的关键断言（训练器常量、路由计数、FK-less 指针、
store 交接、`ReconnectingWS`、`_FactorRange` 等）均与代码现状一致。**可行性、一致性、范围合理性均成立，未发现 P0。**

评审发现 **7 项 P1** 完整性缺口（R-01 守门顺序 / R-02 模型去重 / R-03 取消接口 / R-04 残留 running 对账 /
R-05 指针完整性 / R-06 快作业回放 / R-07 整体样本不足）与 **5 项 P2** 精度问题（R-08…R-12）。**全部 P1 已在 v2 正文落地修复**
（见「评审记录」处置列与各小节标注），并补齐对应回归测试（§7.1）；P2 亦一并改进。

**放行条件（交予 Subtask #2/#3 实现时必须满足）**：

1. **等价性回归是硬门禁**：流式 `f=1.0` 末步产物与 `POST /factor-sets/train` 对相同输入 `weights/normStats/metrics` 逐字段相等的测试**必须先绿**，方可继续。
2. **守门零副作用**：实现须确保 400/413/429 命中时无任何模型/run 落库（测试断言计数不变）。
3. **状态机闭环**：每条 run 必达终态之一（succeeded/failed/cancelled），含 lifespan 启动对账；详情页绝不长期呈现孤儿 `running`。
4. **指针完整性**：任何剪枝/删除路径后 `latest_run_id`/`best_valid_ic` 不得悬挂/陈旧（单事务 + 测试守护）。
5. **不回归既有契约**：`POST /factor-sets/train`、factor_set CRUD、factor_score 回测/对比/Walk-forward 全链路用既有测试守护不回归。

满足以上条件即视为达标；实现阶段如需偏离本方案任一决策，须回填本文件并重新过评审。
