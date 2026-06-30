# 权重模型工作区 — 稳健化与体验完善方案 v2（Hardening）

> Feature: `weight-model-workspace-hardening`
> 所属层：因子层（Factor Layer）· 因子实验室（Factor Lab）· 权重模型工作区（Weight Model Workspace）
> 迭代：Loop Iteration 2/3 ｜ 全局目标：**确保所有功能正确可用、稳健可靠**
> 上游：`docs/plans/weight-model-workspace/spec.md`（v2，已实现并提交 `9552ac9`）。本文档**只做加固与打磨，不重做已成功的功能**。
> 约定：沿用上游全部铁律 —— 字段 camelCase、时间秒级 UTC、错误体统一 `{error:{code,message}}`、单一归一化契约、方向冻结 long/short、
> **绝不假数据 / 绝不静默降级**；纯加法、零破坏：**不动** `f=1.0` 末步与 `POST /factor-sets/train` 的字节级等价、**不动** `factor_sets` 表与
> factor_set ⇄ factor_score 策略 1:1 同步契约、**不新增任何表 / 列**（零迁移）。

---

## 评审记录（Review Notes · v1 → v2）

> 评审人：资深评审（Anthropic 工程团队）｜评审基准：对照**已提交实现**（commit `9552ac9`）逐节核验本加固方案的四维度——**可行性 / 完整性 / 一致性 / 适度性**。
> **结论先行**：方案**质量高**——`H-B1…B8`、`H-F1…F10` 共 18 条发现经**直接读码逐条核验全部属实**，`file:line` 锚点准确（绝大多数在 ±3 行内），「纯加法、零 schema 变更、不动 `f=1.0` 等价路径与 factor_set 同步契约」的纪律全程守住。下列 `R-` 项为评审在**修复侧**发现的完整性 / 一致性 / 适度性缺口；**P0 / P1 已在正文就地修复并升版 v2**，P2 以可见结论形式留给下游遵循。**未发现新增 P0**——上游 `H-B1` 仍是唯一 P0，其修复方向正确，`R-02` 仅作完整性加固。

| # | 维度 | 严重度 | 评审发现 | 处置 |
|---|---|---|---|---|
| R-01 | 一致性 / 可行性 | **P1** | §3.5 `_validate_train_config` 校验键集**部分失真**：树参实际为 **camelCase** `nEstimators`/`maxDepth`（非 `n_estimators`/`max_depth`），`min_samples_leaf`/`learning_rate` **非 config 驱动**（属臆造键，校验它们=校验不存在的键）；`clip>0` 强校验会**破坏** `clip≤0 ⇒ 关闭裁剪` 这一**合法哨兵**语义；`normWindow≥2` 在 `causal_zscore` 内已 `max(_,2)` 钳制属冗余。真正未被校验且有价值的仅 `split`(数值且∈(0,1))与 `l2`(数值且≥0)。 | 已在 §3.5 **重写**校验口径：仅校验**真实且出现**的键、全部可选、不强制 `clip>0`、不要求 `normWindow`、树参用 camelCase。 |
| R-02 | 完整性 | **P1** | §3.1 落库失败兜底**未尽**：① `_persist` 失败仅记日志、**无重试**，而 SQLite `database is locked` 多为**瞬态**，一次重试通常即清；② 对账 `reconcile_dangling_weight_runs` 仅在**启动时**跑，故落库失败的成功/终态 run 在**本进程余生**于详情/历史页显示**永久假「训练中」**（实时 WS 客户端已收到真终态，但开详情页者看到 running）。 | 已在 §3.1 补**有界重试一次** + 显式声明该残留限制（诚实、不假数据），并约束 sweeper 不得借全局对账掩盖（见 R-08）。 |
| R-03 | 一致性 / 可测性 | **P1** | §3.3 `reserve()` 读**构造期缓存**的 `self._cap = get_settings().max_weight_train_jobs`；manager 为惰性单例，缓存值会与运行期 settings 覆写**脱钩**，并使「并发 cap+1」回归测试无法确定性控制 cap（既有 service 层本是**动态**读取，此改属一致性回退）。 | 已改 §3.3：`reserve()` **动态**读 `get_settings().max_weight_train_jobs`，去掉缓存字段。 |
| R-04 | 完整性 / 可行性 | **P1** | §3.5 守门顺序**未点明**：`_guard_range`（413）自身在 `service.py:96` 调用 `bar_to_seconds(window.bar)`，故 `_validate_bar`（400）与 `_validate_train_config`（400）**必须先于** `_guard_range`，否则非法 bar 仍会在 413 段抛裸 `ValueError` → 无处理器的 **500**（恰是 H-B5 要消灭的故障）。 | 已在 §3.3 / §3.5 **固化守门序**：① 成员 400 → ② bar 400 → ③ config 400 → ④ 区间 413 → ⑤ 并发 429(预占)。 |
| R-05 | 完整性 | P2 | §3.5 typed `InsufficientSamplesError` 作用域核验：**唯一**会被 `except ValueError`（`weight_train_manager.py:147-150`）误标为 `INSUFFICIENT_SAMPLES` 的 config 派生错误是**非数值 `split`**（`_train_cut` 的 `float(config.get("split"))`）；其余（l2/树/normWindow/clip）已分别落 `WEIGHT_TRAIN_FAILED`。故 typed 异常 + 边界 `_validate_train_config` 二者**正向叠加**（坏 split 在边界即 400，根本不进作业）。 | 已在 §3.5 澄清作用域，避免下游过度收窄其它 `ValueError`。 |
| R-06 | 一致性 | P2 | §2.2 / §4.9 中 `H-F9` 原始键 `n_train/n_valid` 的**次级锚点**（`WeightTrainLive 74/152/164`、`WeightModelDetail 175/192`）**不准**——该些行实为错误条 / IC 徽标；裸键的**唯一**渲染点是 `WeightTrainLive.tsx:220`（`ResultSummary`）。 | 已校正锚点为 `WeightTrainLive.tsx:220`。 |
| R-07 | 可行性 | P2 | §4.8 `Modal`（`ui/index.tsx:249-339`）是 focus-trap / 对话框**外壳**，**不含** confirm / 是否 契约；替换 `window.confirm` 需在其上新增一个**薄 `ConfirmDialog` 包装**（状态 open + onConfirm），仍为纯加法，但非「一行替换」。 | 已在 §4.8 与文件计划注明需新增 `ConfirmDialog` 包装组件。 |
| R-08 | 适度性 | P2 | §3.6 常驻 120s 清扫协程偏**镀金**：`start_job` 的惰性 `_prune_finished()` 已界内存、终态作业仅数 KB 缓冲。**可注入时钟为 H-T2 确定性 TTL 测试必需，保留**；但若保留 sweeper：**必须 cancel-safe**（吞 `CancelledError`、循环体包 try），且**严禁**调用全局 `reconcile_dangling_weight_runs`（会把在跑 run 误判 failed）。 | 已在 §3.6 标 sweeper 为**可选**并补 cancel-safety / 禁全局对账约束。 |
| R-09 | 完整性 | P2 | §3.7 / §4.3 解耦说明缺失：`H-F3`(P1) 的「连接中断」**不**硬依赖服务端 `ping`——`ReconnectingWS` 的 socket `onclose`/重连即驱动 `onState`（`ws.ts:48-61`）；`ping` 仅增强**半开**探测。故 P1(H-F3) **不被** P2(H-B7) 阻塞，可独立落地。 | 已在 §3.7 / §4.3 注明二者解耦与降级行为。 |
| R-10 | 适度性 | P2 | 整体批量偏大（1×P0 + 11×P1 + 10×P2 + 7 测试缺口）。为保单轮可交付，建议在验收处明确**必交付**（P0 + 后端 B2…B5 + 前端 F1…F6）与**有余力再做**（P2 打磨）的优先级闸，避免 Subtask #2 在 P2 镀金上拖累 P1 正确性。 | 已在 §8.4 增「必交付 / 有余力」优先级闸。 |

---

## 〇、本轮定位（Why this iteration）

迭代 1 已把「权重模型工作区」从设计走到实现并提交：左导航新增「权重模型」类别、模型 CRUD、训练历史回放（自绘 loss/IC 曲线）、
「训练权重」跳转实时流式训练、产物一键回流组合，后端 `pytest 688 passed`、前端 `vitest 723 passed`、`tsc --noEmit` 全绿。功能骨架是健全的。

但「跑通」不等于「稳健可靠」。本轮对照**真实落地代码**（而非设计文档）做了一次系统体检：后端 `weight_train_manager.py` /
`weight_model_service.py` / `api/weight_models.py`，前端 `WeightTrainLive.tsx` / `WeightModelDetail.tsx` / `weightModel.ts` / `weightTrainWs.ts`，
以及六个测试套件。体检定位出 **1 个 P0**（数据库落库失败会让 WS 客户端永久悬挂 + run 永久卡 `running`）、**一批 P1**（并发上限 TOCTOU 漏判、
取消在最长的加载/构建阶段完全不响应、关停可能被卡死、非法 `bar` 直接抛未捕获 500；前端「记录加载失败被渲染成空状态」误导、`boundModelId`
跨会话污染、WS 连接健康完全不可见导致「取消中…」可永久卡住、组件卸载后 setState 泄漏 socket、训练已存模型时用了错误的标的/周期、编辑器
不校验模型名）与一批 P2 体验缺口。这些都不是「锦上添花」，而是直接关系到「功能正确可用、稳健可靠」与「美观优雅、符合人机交互最佳实践」
两条硬要求的真实风险点。

本方案把上述发现整理为带 `H-` 编号、可逐行实现的加固项。所有改动都是**外科手术式**的：要么是 manager 内部 / 组件内部的局部改写，
要么是新增一个边界校验，**没有任何改动会触碰已通过的等价性回归、factor_set 同步事务或数据库 schema**。这保证「修加固」本身不会引入新的回归。

---

## 一、概述（Overview）

权重模型工作区的核心价值是「把一次性的同步训练，升级为有专属面板、可管理、可回溯、可实时观测的一等功能」。这套链路涉及三类容易出问题的
工程面：**长生命周期的进程内异步作业**（训练协程与 WebSocket 解耦、可取消、可重连回放、要落库）、**前端与流式后端之间的实时状态机**
（启动 → 流式 → 终态/取消/失败，叠加连接抖动与组件卸载），以及**面向人的交互细节**（加载/空/错三态、可访问性、文案一致性、破坏性操作确认）。
迭代 1 把「正常路径」做对了，本轮要把「异常路径」与「边界路径」也做对——这正是「稳健可靠」的真正含义。

本方案分三条主线推进。**主线 A（后端可靠性）**：消除「落库失败 → WS 永久悬挂 + run 永久 `running`」这条 P0 故障链，让任何终态事件即便在
数据库写失败时也必达客户端；把并发上限从「软上限（await 间隙可被击穿）」收紧为「同步预占名额的硬上限」；让协作式取消在最长的取数/建矩阵阶段
也能及时响应；让 lifespan 关停**先置全部取消标志、再带超时 join、最后 `task.cancel()` 兜底**，杜绝单个卡住的作业拖垮整个关停；把非法 `bar`、
越界 `config` 这类边界输入在请求边界一次性判 400，而不是漏到引擎里抛出误导性的 500 / `INSUFFICIENT_SAMPLES`。**主线 B（前端正确性与体验）**：
修掉「记录加载失败被当成空状态」的误导渲染、`boundModelId` 跨训练污染、WS 连接健康不可见与「取消中…」永久卡死、卸载后 setState 泄漏、
训练已存模型时标的/周期漂移、编辑器漏校验模型名等真实缺陷，并补齐曲线图可读性（tooltip/坐标刻度）、可访问性（键盘选择/焦点管理/`focus-visible`）、
文案本地化与破坏性操作的应用内确认弹窗。**主线 C（测试护栏）**：把本轮所有修复以及迭代 1 遗留的高价值未覆盖路径（取消、TTL 淘汰、落库失败兜底、
WS 失败/取消终态回放、并发真实击穿、前端三态）补成稳定可复现的回归测试，把「过去的坑」变成「未来的护栏」。

设计原则与上游一致——**纯加法、零破坏、绝不假数据**。所有 H- 项都标注了精确的 `文件:行` 锚点与最小改写范围；下游实现者可据此逐行落地，
无需再做架构决策。每一项都配套「先写复现测试、再改业务代码」的红→绿流程（修 bug 的硬要求）。

---

## 二、问题清单与定位（Findings · 对照真实代码）

> 体检对照的是**已提交的实现**（commit `9552ac9`），下表 `锚点` 均为本轮直接读码核验的 `文件:行`。严重度：P0 数据丢失/服务悬挂；
> P1 功能在异常/边界路径下不可靠或误导；P2 体验与可访问性缺口。

### 2.1 后端可靠性（主线 A）

| # | 严重度 | 问题 | 锚点 | 处置小节 |
|---|---|---|---|---|
| H-B1 | **P0** | `_persist` 落库在 `_finalize_success/_fail/_finalize_cancelled` 中**先于**终态事件推送；若 DB 写抛异常（如 SQLite 锁），`_finalize_success` 直接抛出 → 冒泡到 `_run` 的 `except` → 再调 `_fail` → 再 `_persist` 再抛 → 协程死亡，**run 永远停在 `running`，且所有 WS 订阅者卡在 `await queue.get()` 永不收到终态**，前端永久 loading。 | `weight_train_manager.py:175-212`（`_finalize_success`→`_persist` 先落库后 push）、`:135-141`（`_run` 双 `_fail` 递归）、`api/weight_models.py:134-139`（WS 死等 `queue.get()`） | §3.1 |
| H-B2 | P1 | 协作式取消只在**fraction 步之间**检查（`:155`、`:165`）。最长的两个阶段——`_load_df`（OKX 分页可达 ~8000 根、几十次串行请求）与 `build_factor_matrix`（线程池）——在**第一个 fraction 步之前**，期间取消标志完全不被观测；末步 `train_weights`（`:170`）也在最后一次检查之后。 | `weight_train_manager.py:120-134`（加载/构建无取消检查）、`:155/:165` | §3.2 |
| H-B3 | P1 | 并发上限是 **TOCTOU 软上限**：`active_count() >= max_weight_train_jobs` 判定（`service:148`）后，到 `start_job`（`:158`）之间隔着 `_resolve_model`、`add_weight_run` 两处 `await`；两个并发 `POST /train` 可在彼此 await 间隙双双过闸，实际作业数可超 `max_weight_train_jobs`。 | `weight_model_service.py:147-161`、`weight_train_manager.py:228-237` | §3.3 |
| H-B4 | P1 | `shutdown()` **逐个**「置取消 → `await join()`」串行处理（`:252-256`），后面的作业在前面 join 完成前根本没被置取消标志；且 `join` 直接 `await task`、**无超时、无 `task.cancel()` 兜底**，任一卡在 `_load_df` 的作业都会把 lifespan 关停拖到它跑完。 | `weight_train_manager.py:252-256`、`main.py:51`（关停调用） | §3.4 |
| H-B5 | P1 | 非法 `bar` 抛**未捕获 500**：`bar` 在 schema 层无白名单（`_FactorRange.bar: str = "1H"`），`_guard_range` 调 `bar_to_seconds(window.bar)` 对未知 bar 抛裸 `ValueError`，而 `start_training` 未包裹、`main.py` 无通用 `ValueError` 处理器（仅 OKXError/校验/HTTP）→ 客户端收 500。`config` 自由 dict 中越界 `split`/`normWindow` 等亦无边界校验，深处 `float()` 失败会被误判成 `WEIGHT_TRAIN_INSUFFICIENT_SAMPLES`。 | `weight_model_service.py:93-104/142-145`、`engine/mtf.py:bar_to_seconds`、`weight_train_manager.py:147-150`（过宽 `except ValueError`） | §3.5 |
| H-B6 | P2 | TTL/数量淘汰只在 `start_job` 内**惰性**触发（`:233`）；进程在一波训练后长期空闲时，终态作业（连同事件缓冲 + 结果载荷）会滞留超过 600s TTL 直到下次训练才回收。缺可注入时钟，无法确定性测试 TTL。 | `weight_train_manager.py:233/258-271` | §3.6 |
| H-B7 | P2 | `_push` 在订阅队列满时 `except QueueFull: pass` 丢事件，**包含终态事件**；理论上慢消费者会丢掉 `done/error/cancelled` 导致该 socket 永久悬挂（实际 ~14 事件 « 256 容量，极难触发）。同时 WS 全程无心跳，长 `loading` 阶段半开连接难以及时发现。 | `weight_train_manager.py:94-100`、`api/weight_models.py:134-139` | §3.7 |
| H-B8 | P2 | `best_valid_ic` 在**自动剪枝**后语义会静默退化：`_prune_runs` 只保护 `latest_run_id` 与 running 行，不保护当前持有 `best_valid_ic` 的那条 run；若它是最老且被剪掉，`best_valid_ic` 从「历史最佳」悄悄变成「保留集内最佳」（指针不悬挂，但「最佳」失真）。 | `db/weight_model_repositories.py:258-302`（`_prune_runs`/`finalize_weight_run`） | §3.8 |

### 2.2 前端正确性与体验（主线 B）

| # | 严重度 | 问题 | 锚点 | 处置小节 |
|---|---|---|---|---|
| H-F1 | **P1（bug）** | `WeightModelDetail` 的 `runs` 查询**从不读 `isError`**：失败时 `isPending=false`、`data=[]`，`RunHistoryTable` 渲染空状态文案「该模型还没有训练记录…」——把**加载失败**显示成**没有记录**，严重误导。 | `WeightModelDetail.tsx:30`（`runs` 查询）、`:76-78`（`runs.data ?? []` / `runs.isPending`）、`:108-114`（空状态） | §4.1 |
| H-F2 | P1 | `boundModelId` 是**单一全局**且被任意训练无条件改写：`WeightTrainLive.start.onSuccess` 总是 `setBoundModelId(ids.modelId)`（`:68`），**包括从列表训练某个已存模型**（`modelToTrainTarget` 携 `model.id`）。随后 builder 把 `boundModelId ?? undefined` 当 `modelId` 传入下一次「训练权重」（`FactorSetBuilder.tsx:233`）→ 在组合里点训练，却把（可能无关的）成员训练成绑定到列表那个模型的新版本。跨上下文污染。 | `WeightTrainLive.tsx:68`、`weightModel.ts:218`、`FactorSetBuilder.tsx:233` | §4.2 |
| H-F3 | P1 | WS 可靠性盲区：`weightTrainWS(jobId, onEvent)` 调用时**不传 `onState`**（参数已存在，`weightTrainWs.ts:9`），`ReconnectingWS` 无限静默重连、永不向上抛错。若 socket 始终连不上，UI 停在「streaming」+ 空曲线无超时、无「连接中断」提示。取消 mutation **无 `onError`/无超时**（`:116-119`）：DELETE 失败或 `cancelled` 事件始终不到，`status` 永卡 `cancelling`，按钮永久禁用、无恢复路径。 | `WeightTrainLive.tsx:111/116-119`、`weightTrainWs.ts:6-16`、`ws.ts`（无限重连） | §4.3 |
| H-F4 | P1 | 卸载后 setState + socket 泄漏：挂载 effect 的 cleanup 是 `() => wsRef.current?.close()`，但 `wsRef` 在 `start.onSuccess→openStream` 前一直是 `null`。React Query 即便组件已卸载也会触发 `onSuccess` → `openStream` 建出一个**无人清理的实时 socket** 并对已卸载组件 setState。无 mounted-ref / AbortController 守卫。 | `WeightTrainLive.tsx:79-85`（cleanup）、`:66-70/110-114`（onSuccess→openStream） | §4.4 |
| H-F5 | P1 | 训练已存模型时**标的/周期漂移**：`modelToTrainTarget` 用的是**当前工作台** `range.instId/bar`（`WeightModelPanel.tsx:39-41`→`weightModel.ts:213-214`），而列表/详情卡片展示的是 `model.instId·model.bar`。若工作台正停在另一标的/周期，点「训练」会静默地用工作台的标的训练，与卡片所示不符。 | `weightModel.ts:201-220`、`WeightModelPanel.tsx:39-41` | §4.5 |
| H-F6 | P1（bug） | 编辑器**从不校验模型名**：默认「我的权重模型」但可清空提交，`disabled={!!memberError || save.isPending}`（`WeightModelEditor.tsx:138`）忽略 `name`，空/纯空白名直达 `editorToCreateBody`。 | `WeightModelEditor.tsx:47/138`、`weightModel.ts:224-242` | §4.6 |
| H-F7 | P2 | `cancelled` run 在详情回放无专门呈现：`RunReplay` 仅特判 `failed`（`:172`），`cancelled` 落到 `runToTrained→null`，渲染「学习曲线回放」卡片却无「已取消」说明，与「成功但空权重」难以区分。 | `WeightModelDetail.tsx:162-178`、`weightModel.ts:283-284` | §4.7 |
| H-F8 | P2 | 可访问性：训练记录表行是纯 `<tr onClick>`、无 `tabindex/role/Enter/Space`（鼠标限定，`:130-137`）；视图切换（list↔detail↔live↔editor）无焦点迁移；`Button` 基类无 `focus-visible` 环；破坏性删除用阻塞式原生 `window.confirm`（`WeightModelList.tsx:75`、`WeightModelDetail.tsx:147`），与应用内 `Modal` 原语不一致。 | `WeightModelDetail.tsx:130-137`、`WeightModelPanel.tsx:43-77`、`ui/index.tsx`（Button/Modal） | §4.8 |
| H-F9 | P2 | 曲线图与文案打磨：`TrainingCurveChart` 用 `preserveAspectRatio="none"` 致点/线随容器拉伸变形、无 tooltip/无数值刻度（仅静态 min/max）、轴标签 `min/max` 为硬编码英文；`ResultSummary` 露出 `n_train/n_valid` 原始英文键；`errorCode`/`trainerVersion` 以裸串直出，无友好映射。 | `TrainingCurveChart.tsx:69/92-95`；裸键 `n_train/n_valid` 的**唯一**渲染点 `WeightTrainLive.tsx:220`（`ResultSummary`，R-06 校正：原列的 74/152/164/175/192 实为错误条/IC 徽标，非裸键） | §4.9 |
| H-F10 | P2 | 列表交互缺口：无「显示已停用」开关（`weightModelApi.list` 已支持 `enabled` 过滤却未暴露）、无 running 卡片轮询刷新、删除按钮在 `remove.isPending` 期间不禁用（可连点触发多次 DELETE）。 | `WeightModelList.tsx:66-79/126-131`、`weightModelApi.ts:79-80` | §4.10 |

### 2.3 测试护栏缺口（主线 C）

| # | 缺口 | 锚点 |
|---|---|---|
| H-T1 | 取消路径整条无测试：cancel running→202 + run 终 `cancelled`；已终态幂等→200 无双写；未知→404；WS `cancelled` 事件序列；步间取消（部分曲线、无 result/done）。 | `weight_train_manager.py:cancel/_finalize_cancelled`、`api/weight_models.py:47-55` |
| H-T2 | TTL/数量淘汰无测试（无可注入时钟）：终态作业过 TTL 被淘汰后晚订阅应得 `NOT_FOUND`；数量超 cap 淘汰最旧；running 永不淘汰。 | `weight_train_manager.py:258-271` |
| H-T3 | 前端三态无测试：list 加载失败 / 空 / loading；train 拒绝（429/413）回灌面板；detail `runs.isError`。 | `WeightModelPanel.test.tsx`（仅 happy path） |
| H-T4 | WS 终态回放仅覆盖 `done`，未覆盖 `error`/`cancelled` 终态的晚订阅回放。 | `test_weight_train_ws.py` |
| H-T5 | 样本不足端到端未覆盖（仅引擎层 `prepare_curve_sample` 抛错）：经 manager→WS `error`→run `failed` 全链路。 | `test_weight_train_streaming.py` |
| H-T6 | 真实并发击穿（H-B3）与落库失败兜底（H-B1）无测试；剪枝 log（无静默截断）未断言。 | — |
| H-T7 | 曲线几何单点/全 null/空、组件级（CurveChart/Live/Detail/Editor）无测试；handoff 重挂不重复消费未测。 | `weightModel.ts:metricGeometry`、各组件 |

---

## 三、技术方案 · 主线 A（后端可靠性）

> 总原则：每条 run **必达终态**且**终态事件必达订阅者**，即便 DB 写失败也不例外；上限是**硬**的；取消与关停**及时**；边界输入在**请求边界**判错。所有改动均在 `weight_train_manager.py` / `weight_model_service.py` / `api/weight_models.py` / `main.py` 内，**不触碰** `training.py` 等价路径与 `db/models.py` schema。

### 3.1 H-B1（P0）落库失败也必达终态——消除 WS 永久悬挂

**根因**：`_persist`（`weight_train_manager.py:205-212`）把 DB 写放在终态事件 push **之前**且会向上抛；抛出后既无终态事件、又使 run 卡 `running`。

**改写（`_TrainJob`）**：让 `_persist` 变为**尽力而为、绝不抛**，并把「设置 `self.status/finished_mono`」与「push 终态事件」从 DB 结果中**解耦**——无论落库成败，终态事件都要发，作业都要转终态（这样 `is_terminal` 为真、`_prune_finished` 可回收、WS 循环可收到终态并关闭）。**R-02 加固**：落库失败时**先有界重试一次**（`database is locked` 多为瞬态，一次重试通常即清）；仍失败才记 `logger.error` 并放弃，run 行真实留 `running`（绝不假成功）。

```python
# weight_train_manager.py —— _persist 改为尽力而为，返回是否落库成功（绝不抛）
_PERSIST_RETRY_DELAY = 0.2   # 瞬态 SQLite 锁多在数十~数百 ms 内自清，单次有界重试

async def _persist(self, outcome: repo.WeightRunOutcome) -> bool:
    ok = await self._try_finalize(outcome)
    if not ok:
        await asyncio.sleep(_PERSIST_RETRY_DELAY)   # R-02：瞬态锁一次有界重试
        ok = await self._try_finalize(outcome)
    if not ok:
        # R-02：重试仍失败 → 显式记错并放弃；run 行真实留 running（绝不静默假成功），
        # 由启动对账（R-04，reconcile_dangling_weight_runs）在下次启动改判 failed。
        logger.error("weight run %d 落库失败（已重试 1 次），仍发终态事件并转终态；run 行留 running 待启动对账修正", self.run_id)
    self.status = outcome.status            # 无论成败都转终态（is_terminal=true → 可回收、WS 可关）
    self.finished_mono = self._clock()      # 注：_clock 见 §3.6（默认 time.monotonic）
    return ok

async def _try_finalize(self, outcome: repo.WeightRunOutcome) -> bool:
    try:
        sm = get_sessionmaker()
        async with sm() as session:
            await repo.finalize_weight_run(session, self.run_id, outcome=outcome,
                                           run_cap=self._spec.run_cap)
        return True
    except Exception as exc:  # noqa: BLE001 —— DB 落库失败不得吞掉终态事件，否则 WS 永久悬挂
        logger.warning("weight run %d finalize 尝试失败：%s", self.run_id, exc)
        return False
```

`_finalize_success/_fail/_finalize_cancelled` 三处保持「先 `await self._persist(...)`，再 `self._push(终态事件)`」的顺序不变——因为 `_persist` 现在**不再抛**，终态 push 必然执行。`_run` 的双 `_fail` 递归（`:135-141`）随之失效（`_fail` 内 `_persist` 不抛），不会再让协程死亡。

> **R-02 已知残留限制（必须如实记录，不得掩盖）**：若重试后落库仍失败（**硬故障**，非瞬态），该 run 行在 DB 中**真实留 `running`**，而 `reconcile_dangling_weight_runs` 仅在**进程启动时**跑——故本进程余生内，**实时 WS 客户端已收到真终态**（succeeded/done 或 error/cancelled），但任何打开该模型详情/历史页者会看到这条 run 仍显示「训练中」，直至下次重启对账改判 `failed`。这是 DB 写确已失败时的**诚实**取舍（产物未能持久化，不假装成功）；**严禁**用「sweeper 周期调用全局 `reconcile_dangling_weight_runs`」来掩盖——那会把**同进程内正在跑**的 run 误判 `failed`（见 R-08）。下游若要进一步收敛，可在 sweeper 中**仅**对「内存中已终态但本次 `_persist` 返回 `False`」的特定 job 重试其 `finalize`（精确目标、不碰在跑 run），属可选增强。

**验收**：注入一个**前 N 次抛、之后成功**的 sessionmaker，断言：① 仅瞬态失败（1 次）时重试后**落库成功**、run 行终为终态；② 持续失败时 WS 仍收到 `error`/`done` 终态并被服务端关闭（不悬挂）、作业 `is_terminal` 为真、进程不崩、run 行留 `running`（待对账）。

### 3.2 H-B2（P1）取消在加载/构建阶段也及时响应

在 `_run` 的 `_load_df` 之后、`build_factor_matrix` 之后各加一处取消检查；保持线程池调用本身不可中断（不强行打断 CPU），但把「等它跑完才看标志」的窗口从「最长阶段之后」收紧到「每个重活之后立即」。

```python
# weight_train_manager.py:_run —— 在加载/构建后各补一处协作式取消检查
df = await service._load_df(spec.rest, spec.window)
if self._cancel:                      # ← 新增：取消在最长的 OKX 取数后立即生效
    return await self._finalize_cancelled()
self._emit_phase("building", 25)
matrix, names, norm = await run_in_threadpool(build_factor_matrix, ...)
if self._cancel:                      # ← 新增：构建后立即生效
    return await self._finalize_cancelled()
```

末步 `train_weights`（`:170`）之前已有 `if self._cancel`（`:165`）。若取消在该检查之后、最终 fit 完成之前到达，则该次 fit 已完成、产物真实有效，**按成功落库**（诚实：工作确已完成）——这是协作式取消的可接受语义，文案上「取消中…」收到 `done` 即转「训练完成」。

### 3.3 H-B3（P1）并发上限改为同步预占的硬上限

在 manager 增加**同步、无 await** 的名额预占，消除 TOCTOU。预占在守门处原子发生，覆盖 `_resolve_model`/`add_weight_run` 两处 await 的整个窗口；`start_job` 注册成功即把预占转为「running 计数」，异常路径显式释放。

```python
# weight_train_manager.py：WeightTrainManager 增加 _reserved 计数 + 同步 reserve/release
def __init__(self):
    self._jobs: dict[str, _TrainJob] = {}
    self._reserved = 0                   # 同步预占名额（覆盖守门→注册之间的 await 窗口）

def reserve(self) -> bool:               # 同步、无 await：守门即占名额，await 间隙不漏
    # R-03：动态读 settings——manager 为惰性单例，**不缓存** cap（缓存会与运行期 settings 覆写脱钩，
    # 并使「并发 cap+1」回归测试无法确定性控制上限）；与既有 service 层动态读取保持一致。
    cap = get_settings().max_weight_train_jobs
    if self.active_count() + self._reserved >= cap:
        return False
    self._reserved += 1
    return True

def release(self) -> None:
    self._reserved = max(0, self._reserved - 1)

def start_job(self, spec: JobSpec) -> _TrainJob:
    self._prune_finished()
    job = _TrainJob(uuid.uuid4().hex, spec, clock=self._clock)
    self._jobs[job.job_id] = job
    self.release()                       # 预占转为 running 计数（job.status=="running" 即被 active_count 计入）
    job.start()
    return job
```

```python
# weight_model_service.py:start_training —— ③ 改为同步预占，失败路径释放
manager = get_weight_train_manager()
if not manager.reserve():                                    # ③ 429（硬上限）
    raise HTTPException(429, detail={"code": "WEIGHT_TRAIN_BUSY",
        "message": f"并发训练作业已达上限 {get_settings().max_weight_train_jobs}，请稍后重试。"})
try:
    model = await _resolve_model(session, req, members)
    run = await repo.add_weight_run(session, start=repo.WeightRunStart(...))
    job = manager.start_job(JobSpec(...))                    # 内部 release()
except Exception:
    manager.release()                                        # 守门后任何失败都释放预占，杜绝名额泄漏
    raise
return {"jobId": job.job_id, "runId": run.id, "modelId": model.id}
```

注：守门顺序（含 H-B5 的 400 校验，见 §3.5 R-04）为 ①成员 400 → ②bar 400 → ③config 400 → ④区间 413 → ⑤并发 429（预占）→ 解析/落库/注册，**R-01「守门优先、全过后才持久化」不变**。

### 3.4 H-B4（P1）关停先置标志、再带超时 join、最后 task.cancel 兜底

```python
# weight_train_manager.py
_SHUTDOWN_JOIN_TIMEOUT = 5.0

async def shutdown(self) -> None:
    jobs = list(self._jobs.values())
    for job in jobs:                       # ① 先把所有作业一次性置取消标志（不再串行等待）
        job.request_cancel()
    for job in jobs:                       # ② 带超时 join；超时则硬取消兜底
        try:
            await asyncio.wait_for(job.join(), timeout=_SHUTDOWN_JOIN_TIMEOUT)
        except asyncio.TimeoutError:
            job.force_cancel()             # task.cancel() → _run 的 except CancelledError → _finalize_cancelled
            await job.join()
    self._jobs.clear()

# _TrainJob 增加
def force_cancel(self) -> None:
    task = self._task
    if task is not None and not task.done():
        task.cancel()
```

`_run` 已处理 `asyncio.CancelledError → _finalize_cancelled() + raise`（`:137-139`），`join` 已吞 `CancelledError`（`:116-117`），二者协同即可在硬取消下仍把 run 落为 `cancelled`。

### 3.5 H-B5（P1）非法 bar / 越界 config 在请求边界判 400

**bar**：在 `start_training` 守门段作为**独立的第 ② 步**（先于 `_guard_range`，见下「守门顺序 R-04」）显式校验 `bar`，把 `bar_to_seconds` 的裸 `ValueError` 映射为 400，避免漏到无处理器的 500。

```python
# weight_model_service.py
def _validate_bar(bar: str) -> None:
    try:
        bar_to_seconds(bar)
    except ValueError:
        raise HTTPException(400, detail={"code": "FACTOR_INVALID_BAR",
            "message": f"不支持的周期：{bar}"})
```

**config 边界（R-01 修正——仅校验真实且出现的键）**：新增 `_validate_train_config(config)`。**经核验**，config 中**真正被引擎消费**的键与读取点如下表，本校验严格据此收敛（避免上一版臆造 `n_estimators`/`max_depth`/`min_samples_leaf`/`learning_rate`，并避免破坏 `clip` 哨兵）：

| 键（**camelCase**） | 读取点 | 默认 | 现状 | 本校验口径 |
|---|---|---|---|---|
| `split` | `_train_cut`（`training.py:277`，经 `prepare_curve_sample`/`train_weights`） | 0.7 | 仅钳到 `[1,n-1]`；**非数值** `split` → 裸 `ValueError` 被误标 INSUFFICIENT_SAMPLES | **要校验**：present 时须为数值且 `∈(0,1)` |
| `l2` | `_fit_linear`（`training.py:149`） | 1.0 | 完全无校验 | **要校验**：present 时须为数值且 `≥0` |
| `nEstimators` | `resolve_tree_params`（`tree_ensemble.py:51`） | 100 | 已钳 `[1,400]` | 可选：present 时须为正整数（越界由引擎钳制兜底） |
| `maxDepth` | `resolve_tree_params`（`tree_ensemble.py:52`） | RF6/GBDT3 | 已钳 `[1,12]` | 可选：present 时须为正整数 |
| `normWindow` | `weight_train_manager.py:128` | 200 | `causal_zscore` 内 `max(_,2)` 钳制 | **不校验**（冗余） |
| `clip` | `weight_train_manager.py:129` | 4.0 | `clip≤0 ⇒ 关闭裁剪`（**合法哨兵**） | **不校验 `>0`**（会破坏哨兵语义） |

即：**只校验出现的键、全部可选、绝不要求任何键**；`min_samples_leaf`/`learning_rate` 非 config 驱动，一律**不纳入**。任一被校验键越界/类型错 → 400 `FACTOR_TRAIN_INVALID_CONFIG`（含字段名与取值）。

```python
# weight_model_service.py —— 仅校验真实且出现的键（R-01）
def _validate_train_config(config: dict) -> None:
    def _num(key):
        if key not in config:           # 缺省合法：引擎用默认值
            return None
        try:
            return float(config[key])
        except (TypeError, ValueError):
            raise HTTPException(400, detail={"code": "FACTOR_TRAIN_INVALID_CONFIG",
                "message": f"参数 {key} 必须为数值，收到：{config[key]!r}"})
    split = _num("split")
    if split is not None and not (0.0 < split < 1.0):
        raise HTTPException(400, detail={"code": "FACTOR_TRAIN_INVALID_CONFIG",
            "message": f"split 须在 (0,1) 内，收到：{split}"})
    l2 = _num("l2")
    if l2 is not None and l2 < 0.0:
        raise HTTPException(400, detail={"code": "FACTOR_TRAIN_INVALID_CONFIG",
            "message": f"l2 须 ≥0，收到：{l2}"})
    for key in ("nEstimators", "maxDepth"):     # 可选：present 时须为正整数（越界仍由引擎钳制兜底）
        val = _num(key)
        if val is not None and (val != int(val) or val <= 0):
            raise HTTPException(400, detail={"code": "FACTOR_TRAIN_INVALID_CONFIG",
                "message": f"{key} 须为正整数，收到：{config[key]!r}"})
```

**typed 异常（R-05 澄清）**：把 `_fit_and_finalize` 里**过宽**的 `except ValueError`（`:147-150`）收窄。经核验，唯一会被它误标为 `WEIGHT_TRAIN_INSUFFICIENT_SAMPLES` 的 config 派生错误是**非数值 `split`**（其余 l2/树/normWindow/clip 已落 `WEIGHT_TRAIN_FAILED`）。让 `prepare_curve_sample` 在样本不足时抛**专用子类** `InsufficientSamplesError(ValueError)`，作业只把该子类映射为 `WEIGHT_TRAIN_INSUFFICIENT_SAMPLES`，其余 `ValueError` 照常冒泡（被 §3.1 的 `_fail` 收口为 `WEIGHT_TRAIN_FAILED`，不再误标）。边界 `_validate_train_config` 已让坏 `split` 在请求边界即 400、根本不进作业，与 typed 异常**正向叠加**——下游切勿借此过度收窄其它合法 `ValueError`。

> 注：`prepare_curve_sample` 仅新增一个异常子类、不改其判定逻辑与阈值（`MIN_TRAIN_SAMPLES=30`）；`f=1.0` 等价路径与样本守门口径**不变**。

**守门顺序（R-04，强约束）**：因 `_guard_range`（413）自身在 `service.py:96` 调 `bar_to_seconds(window.bar)`，故 `_validate_bar`（400）与 `_validate_train_config`（400）**必须先于** `_guard_range`，否则非法 bar 仍会在 413 段抛裸 `ValueError` → 无处理器的 500（恰是本节要消灭的故障）。`start_training` 守门段最终序固定为：① `validate_members`（成员 400）→ ② `_validate_bar`（bar 400）→ ③ `_validate_train_config`（config 400）→ ④ `_guard_range`（区间 413）→ ⑤ `manager.reserve()`（并发 429 预占）→ 解析/落库/注册。任一命中即返回、零持久化副作用（R-01「守门优先」不回归）。`_validate_bar` 同样加到 `create_model`/`update_model`（手动建模/改模型亦可传 bar）。

### 3.6 H-B6（P2）TTL 主动回收 + 可注入时钟

给 manager 与 `_TrainJob` 注入 `clock: Callable[[], float] = time.monotonic`（替换所有 `time.monotonic()` 直调），使 TTL 可在测试中以假时钟确定性推进。**可注入时钟是 H-T2 确定性 TTL 测试的必需件，保留**。

**R-08（适度性）——周期清扫协程为「可选」**：`start_job` 的惰性 `_prune_finished()` 已界住内存（终态作业仅数 KB 缓冲），常驻 sweeper 偏镀金，**可不做**；若做（让空闲进程也按 TTL 回收），则**必须** cancel-safe，且**严禁**借它调用全局 `reconcile_dangling_weight_runs`（那会把同进程内**正在跑**的 run 误判 `failed`，破坏 R-02 的诚实语义）。

```python
# main.py —— 若实现可选 sweeper：必须 cancel-safe，循环体仅调 manager._prune_finished()
async def _weight_train_sweeper() -> None:
    manager = get_weight_train_manager()
    try:
        while True:
            await asyncio.sleep(120)
            manager._prune_finished()     # 仅按 TTL/数量回收终态作业；绝不触碰在跑 run，绝不调全局对账
    except asyncio.CancelledError:        # R-08：关停取消时干净退出，不外抛
        return

# lifespan startup（紧随 _reconcile_weight_train_runs 之后）
sweeper = asyncio.create_task(_weight_train_sweeper())
# lifespan shutdown（manager.shutdown() 之前）
sweeper.cancel()
```

### 3.7 H-B7（P2）WS 心跳 + 满队列终态兜底（一改两得）

把 WS 主循环的 `await queue.get()` 换成带心跳超时的等待：超时即发 `{"type":"ping"}` 兼做半开探测，并**兜底检查 `job.is_terminal`**——若终态事件曾因满队列被丢，从 `snapshot()` 补发终态后关闭，杜绝悬挂。前端 `onEvent` 对未知 type（含 `ping`）天然无副作用（现有 if/else 链直接落空），无需改其分支，仅需在 §4.3 接 `onState`。

> **R-09（解耦说明）**：本项（P2）与 `H-F3`（P1）**无硬依赖**——`H-F3` 的「连接中断」提示由 `ReconnectingWS` 的 socket `onclose`/重连（`ws.ts:48-61`）驱动 `onState` 即可呈现，**不**需要服务端 `ping`；服务端心跳 `ping` 仅**增强半开**（连接看似 open 但无数据流）探测与 §4.3 的「等待训练数据…」看门狗。故 `H-F3` 可先于 `H-B7` 独立落地，`H-B7` 缺席时 `H-F3` 优雅降级（仅少了半开探测）。「满队列丢终态」本身概率极低（~14 事件 « 256 容量），本项主价值在心跳探测，而非该极端兜底。

```python
# api/weight_models.py:ws_weight_train —— 主循环（替换 134-139）
_WS_HEARTBEAT_SECONDS = 20.0
while True:
    try:
        event = await asyncio.wait_for(queue.get(), timeout=_WS_HEARTBEAT_SECONDS)
    except asyncio.TimeoutError:
        await websocket.send_text(json.dumps({"type": "ping"}))
        if job.is_terminal:                                  # 终态兜底：满队列丢终态也不悬挂
            buf = job.snapshot()
            if buf and buf[-1].get("type") in _TERMINAL_TYPES:
                await websocket.send_text(json.dumps(buf[-1]))
            await websocket.close(); break
        continue
    await websocket.send_text(json.dumps(event))
    if event.get("type") in _TERMINAL_TYPES:
        await websocket.close(); break
```

### 3.8 H-B8（P2）best_valid_ic 在自动剪枝下保持「历史最佳」诚实

在 `_prune_runs`（`db/weight_model_repositories.py:258-271`）把**当前持有 `best_valid_ic` 的 succeeded run** 与 `latest_run_id`、running 一并列入「永不自动剪枝」白名单。如此自动剪枝不会让「最佳」失真；只有用户**显式 `delete_run`** 删掉最佳记录时才 `repoint` 重算（已实现，符合「用户主动行为可改变最佳」的直觉）。保留集因此至多比 `cap` 多 1～2 条（软上限，可接受）。**不改 schema**。

---

## 四、技术方案 · 主线 B（前端正确性与体验）

> 所有改动均在 `apps/web/components/Factor/*` 与 `apps/web/lib/weightModel*.ts` 内；交接态为瞬态、不入 partialize，**无需 persist 版本号变更**（沿用 v23）。设计 token / 明暗主题零适配延续上游。

### 4.1 H-F1（P1 bug）记录加载失败≠空状态

`WeightModelDetail` 读取 `runs.isError` 并把「错误」与「空」分流。`RunHistoryTable` 增 `error?: boolean` 与 `errorMessage?: string`，错误分支渲染红色「加载训练记录失败：…」+ 重试按钮（`runs.refetch()`），与空状态文案彻底区分。

```tsx
// WeightModelDetail.tsx
<RunHistoryTable
  runs={runs.data ?? []}
  loading={runs.isPending}
  error={runs.isError}
  errorMessage={(runs.error as Error)?.message}
  onRetry={() => runs.refetch()}
  ... />
// RunHistoryTable 内：if (error) return <ErrorRow message={errorMessage} onRetry={onRetry} />;（先于空状态判断）
```

### 4.2 H-F2（P1）boundModelId 只在 builder 发起时记忆，杜绝跨上下文污染

给 `WeightTrainTarget` 增判别字段 `origin: "builder" | "model"`（瞬态类型，不持久化）。`buildTrainTarget`（builder 发起）置 `origin:"builder"`；`modelToTrainTarget`（列表/详情发起）置 `origin:"model"`。`WeightTrainLive.start.onSuccess` 改为**仅当 `target.origin === "builder"`** 才 `setBoundModelId(ids.modelId)`；列表训练已存模型时不写 `boundModelId`。并在 `FactorSetBuilder` 加载不同组合（`editingSetId` 变化）或清空成员时复位 `setBoundModelId(null)`，避免陈旧绑定。

```tsx
// WeightTrainLive.tsx:onSuccess
handlesRef.current = ids;
if (target.origin === "builder") setBoundModelId(ids.modelId);  // 只记忆 builder 会话内的复用
```

### 4.3 H-F3（P1）WS 连接健康可见 + 取消有超时/错误恢复

接上 `onState` 暴露连接态；`streaming` 期间 `!connected` 显示「连接中断，正在重连…」条；并加**陈旧流看门狗**：进入 `streaming` 后若 `_WS_HEARTBEAT_SECONDS·2` 内无任何事件（含 ping），提示「等待训练数据…」。取消 mutation 补 `onError`（DELETE 失败→回 `streaming` + 提示可重试）与**超时兜底**：发起取消后若 `_CANCEL_TIMEOUT`（如 10s）内未收到 `cancelled`/`done`/`error`，解除按钮禁用并提示「取消未确认，可重试或等待完成」。

```tsx
const [connected, setConnected] = useState(true);
const openStream = (jobId: string) => {
  const ws = weightTrainWS(jobId, onEvent, setConnected);   // ← 接 onState
  wsRef.current = ws; ws.connect();
};
const cancel = useMutation({
  mutationFn: () => weightModelApi.cancel(handlesRef.current?.jobId ?? ""),
  onMutate: () => setStatus("cancelling"),
  onError: () => { setStatus("streaming"); setError({ code: "CANCEL_FAILED", message: "取消请求失败，可重试" }); },
});
// 进入 cancelling 后启动 _CANCEL_TIMEOUT 定时器，超时未达终态则恢复可操作态（清理见 §4.4 mounted 守卫）。
```

### 4.4 H-F4（P1）卸载守卫，杜绝 setState/socket 泄漏

引入 `mountedRef`。`start.onSuccess` 在 `openStream` 前判 `if (!mountedRef.current) return;`；effect cleanup 置 `mountedRef.current=false` 并 `wsRef.current?.close()`；`finishStream`/`onEvent` 的 setState 以 `mountedRef.current` 守卫（避免对已卸载组件 setState 的告警与泄漏）。所有 §4.3 的定时器在 cleanup 中 `clearTimeout`。

```tsx
const mountedRef = useRef(true);
useEffect(() => {
  if (startedRef.current) return;
  startedRef.current = true;
  start.mutate();
  return () => { mountedRef.current = false; wsRef.current?.close(); /* + clearTimeout(...) */ };
}, []);
// onSuccess: if (!mountedRef.current) return; handlesRef.current = ids; ...
```

### 4.5 H-F5（P1）训练已存模型用模型自身的标的/周期

`modelToTrainTarget` 改用 `model.instId/model.bar`（模型自身市场），训练时间窗 `startTs/endTs` 仍取工作台 `range`（窗口是用户当下选择的时段，合理）。如此「卡片所示标的/周期」与「实际训练标的/周期」一致，消除静默漂移。`WeightTrainLive` 头部展示的 `target.instId·target.bar` 随之正确。

```tsx
// weightModel.ts:modelToTrainTarget
return buildTrainTarget({
  members: model.members, method: model.method, thresholds, horizon: model.horizon,
  instId: model.instId, bar: model.bar,            // ← 用模型自身的标的/周期（原为 range.instId/range.bar）
  startTs: range.startTs, endTs: range.endTs,      // 时间窗仍取工作台当前区间
  name: model.name, modelId: model.id, factorSetId: model.factorSetId ?? undefined,
  origin: "model",                                  // ← 配合 §4.2
});
```

### 4.6 H-F6（P1 bug）编辑器校验模型名

`WeightModelEditor` 计算 `nameError = !name.trim() ? "请填写模型名" : ""`，内联展示并纳入提交禁用：`disabled={!!memberError || !!nameError || save.isPending}`。提交前 `name.trim()`。

### 4.7 H-F7（P2）cancelled run 专门呈现

`RunReplay` 在 `failed` 分支旁增 `cancelled` 分支：渲染中性「该次训练已取消（用户主动中止）」提示卡，不再含糊地展示空权重回放。

### 4.8 H-F8（P2）可访问性与破坏性确认

- 记录表行改为可键盘操作：`role="button"`、`tabIndex={0}`、`onKeyDown`（Enter/Space → `onSelect`）。
- 视图切换时把焦点移到新视图标题（参照 `FactorLab` 的 `headingRef` 模式）。
- `Button` 基类补 `focus-visible:outline`/`ring`（共享原语的**加法**类，不改既有视觉）。
- 破坏性删除（模型 / 记录）以应用内确认弹窗替换阻塞式 `window.confirm`，焦点受困于应用、风格统一。**R-07（可行性）**：`components/ui` 的 `Modal`（`ui/index.tsx:249-339`）是 focus-trap / 对话框**外壳**，**无** confirm / 是否 契约——故需在其上**新增一个薄 `ConfirmDialog` 包装**（`{open, title, message, confirmText, onConfirm, onCancel}`，复用 `Modal` 的 focus-trap/Esc/portal + `Button` 原语），删除处改用受控 `open` 状态触发它。仍为纯加法，但非「一行替换」，故 `ConfirmDialog` 列入文件变更计划（见 §五）。

### 4.9 H-F9（P2）曲线图可读性与文案本地化

- `TrainingCurveChart` 去除 `preserveAspectRatio="none"`（或按容器宽自适应计算几何），消除点/线变形；增加**悬停 tooltip**（显示该步 fraction/nTrain/trainLoss/validLoss/trainIc/validIc）与 y 轴少量数值刻度；轴标签 `min/max → 最小/最大`。
- `ResultSummary` 的 `n_train/n_valid → 训练样本/验证样本`；`errorCode` 经一张 `code→中文` 映射表（`WEIGHT_TRAIN_INSUFFICIENT_SAMPLES`/`OKX_UNREACHABLE`/`FACTOR_TRAIN_TOO_LARGE`/`WEIGHT_TRAIN_BUSY`/`WEIGHT_TRAIN_INTERRUPTED` 等）展示友好文案，原始码降级为 tooltip。

### 4.10 H-F10（P2）列表交互完善

- 增「显示已停用」开关，绑定 `weightModelApi.list({ enabled })`。
- 当任一卡片 `latestRun.status==="running"` 时对列表查询设 `refetchInterval`（如 4s），训练态实时刷新；无 running 则不轮询。
- 删除按钮在 `remove.isPending` 期间 `disabled`，杜绝连点多发 DELETE。

---

## 五、文件 / 模块变更计划（File / Module Change Plan）

> 全部为**修改既有文件 + 新增测试**；**无新增源模块、无新增表 / 列**。`(改)` 修改、`(新)` 新建。

### 后端（services/quant）

| 文件 | 动作 | 意图（一行） |
|---|---|---|
| `app/factors/weight_train_manager.py` | 改 | H-B1 `_persist` 尽力而为不抛 + 终态必达 + **有界重试一次（R-02）**；H-B2 加载/构建后补取消检查；H-B3 `reserve/release`（**动态读 cap，不缓存 `_cap`，R-03**）+ `start_job` 转计数；H-B4 `shutdown` 先置标志/超时 join/`force_cancel` 兜底；H-B6 注入 `clock`。 |
| `app/factors/weight_model_service.py` | 改 | H-B3 `start_training` 改同步预占 + 失败释放；H-B5 `_validate_bar` / `_validate_train_config`（**仅真实 camelCase 键，R-01**），守门序 bar/config 400 **先于** 区间 413（R-04）。 |
| `app/api/weight_models.py` | 改 | H-B7 WS 主循环改心跳超时 + 满队列终态兜底。 |
| `app/main.py` | 改 | H-B6 lifespan 启动**可选** cancel-safe 周期清扫协程、关停取消（紧邻既有 reconcile / shutdown；**严禁**调全局对账，R-08）。 |
| `app/engine/factors/training.py` | 改 | H-B5 `prepare_curve_sample` 样本不足改抛 `InsufficientSamplesError(ValueError)` 子类（**仅异常类型，不改判定/阈值/等价路径**）。 |
| `app/engine/factors/__init__.py` | 改 | 导出 `InsufficientSamplesError`。 |
| `app/db/weight_model_repositories.py` | 改 | H-B8 `_prune_runs` 白名单纳入当前 `best_valid_ic` 持有 run（永不自动剪枝）。 |

### 前端（apps/web）

| 文件 | 动作 | 意图（一行） |
|---|---|---|
| `lib/weightModelTypes.ts` | 改 | `WeightTrainTarget` 增 `origin:"builder"\|"model"`（瞬态）。 |
| `lib/weightModel.ts` | 改 | `buildTrainTarget` 透传 `origin`；`modelToTrainTarget` 用 `model.instId/bar` + `origin:"model"`（H-F2/H-F5）。 |
| `components/Factor/WeightTrainLive.tsx` | 改 | H-F2 条件记忆 boundModelId；H-F3 `onState`/看门狗/取消 onError+超时；H-F4 `mountedRef` 卸载守卫 + 清理。 |
| `components/Factor/WeightModelDetail.tsx` | 改 | H-F1 `runs.isError` 分流错误态；H-F7 `cancelled` 回放分支；H-F8 表行键盘可达 + 记录删除改用 `ConfirmDialog`。 |
| `components/Factor/WeightModelEditor.tsx` | 改 | H-F6 模型名必填校验。 |
| `components/Factor/WeightModelList.tsx` | 改 | H-F10 显示已停用开关 / running 轮询 / 删除按钮禁用去抖；H-F8 改用 `ConfirmDialog`（替 `window.confirm`）。 |
| `components/Factor/WeightModelPanel.tsx` | 改 | H-F8 视图切换焦点迁移。 |
| `components/Factor/FactorSetBuilder.tsx` | 改 | H-F2 切换组合 / 清空成员时复位 `boundModelId`。 |
| `components/Factor/TrainingCurveChart.tsx` | 改 | H-F9 去拉伸变形 + tooltip + 数值刻度 + 轴标签本地化。 |
| `components/ui/index.tsx` | 改 | H-F8 `Button` 基类补 `focus-visible` 环（加法，不改既有视觉）。 |
| `components/ui/ConfirmDialog.tsx` | **新** | H-F8 / **R-07**：在 `Modal` 外壳上的薄确认弹窗（`{open,title,message,confirmText,onConfirm,onCancel}`），供模型 / 记录删除的应用内二次确认（替 `window.confirm`）。 |

### 测试（主线 C）

| 文件 | 动作 | 意图 |
|---|---|---|
| `services/quant/tests/test_weight_train_cancel.py` | 新 | H-T1：cancel running→202+run `cancelled`；已终态→200 幂等无双写；未知→404；步间取消（部分曲线、终 `cancelled`）。 |
| `services/quant/tests/test_weight_train_resilience.py` | 新 | H-B1/R-02：注入「首次抛、重试成功」→ 落库成功；注入「持续抛」→ WS 仍收终态并关闭、作业 `is_terminal`、不崩、run 留 `running`；H-B4：`shutdown` 在卡住作业下带超时 `force_cancel` 仍落 `cancelled`。 |
| `services/quant/tests/test_weight_train_manager_limits.py` | 新 | H-B3 真实并发击穿（reserve 硬上限不超）；H-B6 假时钟 TTL/数量淘汰 + running 永不淘汰。 |
| `services/quant/tests/test_weight_models_validation.py` | 新 | H-B5/R-01 非法 bar→400（且先于区间 413，R-04）、`split∉(0,1)`/非数值/`l2<0`/`nEstimators` 非正整数→400，**`clip≤0` 与缺省 `normWindow` 不被拒**；样本不足端到端（manager→WS `error`→run `failed`，H-T5）。 |
| `services/quant/tests/test_weight_train_ws.py` | 改 | H-T4 `error`/`cancelled` 终态晚订阅回放；H-B7 心跳 ping + 终态兜底。 |
| `services/quant/tests/test_weight_model_repo.py` | 改 | H-B8 自动剪枝保留 best-IC run（`best_valid_ic` 不退化）；剪枝 log 计数断言（无静默截断）。 |
| `apps/web/components/Factor/WeightModelDetail.test.tsx` | 新 | H-F1 `runs.isError` 错误态；H-F7 cancelled 回放；表行键盘选择。 |
| `apps/web/components/Factor/WeightModelEditor.test.tsx` | 新 | H-F6 空名禁用提交、合法名解禁。 |
| `apps/web/components/Factor/WeightTrainLive.test.tsx` | 新 | H-F2 origin 条件记忆；H-F3 取消失败恢复；H-F4 卸载不再 openStream/setState。 |
| `apps/web/components/Factor/WeightModelPanel.test.tsx` | 改 | H-T3 list 加载失败 / 空 / loading；train 拒绝（429/413）回灌。 |
| `apps/web/lib/weightModel.test.ts` | 改 | H-T7 曲线几何单点/全 null/空；`modelToTrainTarget` 用模型标的/周期 + `origin`。 |

---

## 六、接口设计（Interface Design）

> **零破坏**：现有 REST/WS 契约**形状不变**。本轮仅新增错误码与一个非业务事件，均为**加法**。

### 6.1 新增错误码（沿用 `{error:{code,message}}`）

| 端点 | 状态码 | code | 触发 |
|---|---|---|---|
| `POST /api/weight-models/train`、`POST/PUT /api/weight-models` | 400 | `FACTOR_INVALID_BAR` | 非白名单周期（H-B5）。 |
| `POST /api/weight-models/train` | 400 | `FACTOR_TRAIN_INVALID_CONFIG` | 真实键越界/类型错（R-01）：`split∉(0,1)`、`l2<0`、`nEstimators/maxDepth` 非正整数（**不**校验 `normWindow`/`clip`，见 §3.5）。 |

`429 WEIGHT_TRAIN_BUSY`（H-B3 改为硬上限触发，码与文案不变）、`413 FACTOR_TRAIN_TOO_LARGE`、`409 WEIGHT_RUN_RUNNING`、各 `404 NOT_FOUND` 全部沿用。

### 6.2 WebSocket：新增 `ping` 心跳事件（加法、向后兼容）

`WS /api/ws/weight-train/{job_id}` 在静默 `_WS_HEARTBEAT_SECONDS`（20s）后下发 `{"type":"ping"}`；前端 `onEvent` 对未知 type 天然忽略，仅 `onState` 用于反映连接健康。终态序列（`run→phase*→curve*→result→done | error | cancelled`）与回放语义**完全不变**；新增「终态兜底」仅在满队列丢终态的极端场景补发缓冲终态（不改正常时序）。

### 6.3 取消端点语义（不变，补齐前端契约消费）

`DELETE /api/weight-models/train/{job_id}` → `202 cancelling | 200 finished(幂等) | 404 unknown`（已实现）。前端新增 `onError` 与超时兜底（§4.3）以消费失败/超时分支，端点签名不变。

---

## 七、数据模型（Data Model）

### 7.1 持久层：零变更（硬约束）

**不新增 / 不修改任何表或列**：`weight_models`、`weight_train_runs` 结构与索引保持上游 v2；`init_db()` 的 `create_all` 加法建表口径不变；**零数据迁移**。H-B8 仅改 `_prune_runs` 的**选择逻辑**（运行期行为），不动列定义。`latest_run_id`（FK-less 整数）与 `best_valid_ic`（反范式冗余）语义与上游一致，仅在自动剪枝下更诚实（best-IC run 受保护）。

### 7.2 进程内形状（In-memory shapes）

| 位置 | 新增 / 改写 | 说明 |
|---|---|---|
| `WeightTrainManager` | `_reserved:int`、`_clock:Callable[[],float]`、`reserve()/release()`（cap **动态读 settings**、不缓存，R-03）、`shutdown()` 重写 | H-B3/H-B4/H-B6。 |
| `_TrainJob` | `_clock` 注入、`force_cancel()`、`_persist()→bool`（不抛）、加载/构建后取消检查 | H-B1/H-B2/H-B4/H-B6。 |
| `main.py` lifespan | `sweeper: asyncio.Task` | H-B6 周期清扫。 |
| `WeightTrainTarget`（前端瞬态类型） | `origin:"builder"\|"model"` | H-F2/H-F5；瞬态、不入 partialize、**无 persist 版本变更**。 |
| `WeightTrainLive` 组件 | `mountedRef`、`connected`、看门狗/取消超时定时器 | H-F3/H-F4。 |

### 7.3 引擎异常类型（加法）

`InsufficientSamplesError(ValueError)`（`engine/factors`）：仅用于把「对齐后样本不足」与「其它 `ValueError`（如越界 config 解析）」区分开（H-B5）。判定阈值 `MIN_TRAIN_SAMPLES=30`、口径、`f=1.0` 等价路径**均不变**。

---

## 八、测试与验收标准（Testing & Acceptance Criteria）

### 8.1 测试策略（先复现、再修复）

每个 bug 类 H- 项（H-B1/H-B3/H-B5/H-F1/H-F2/H-F4/H-F6）**必须先写能稳定复现的失败测试，再改业务代码**（红→绿）。后端沿用 `pyproject.toml` 的 `asyncio_mode=auto`、`tests/conftest.py` 的唯一临时 `DB_PATH` 与 `make_synthetic_candles`；前端沿用 `vitest + jsdom + NODE_ENV=test`、`vi.mock` 桩（`weightModelApi`/`weightTrainWs`/`FactorLab`）。高风险路径（落库失败、关停、并发、WS 终态）用**真实集成**（真 manager + 真 WS + 临时 DB），不 mock 状态机本身。

### 8.2 关键回归断言

- **H-B1 落库失败兜底（含 R-02 重试）**：① 注入「**首次抛、重试即成功**」的 `finalize_weight_run` → 重试后**落库成功**、run 行终为终态（验证有界重试生效）；② 注入「**持续抛**」 → WS 仍收 `done`/`error` 且服务端关闭（不悬挂）、作业 `is_terminal`、进程不崩；run 行留 `running` 但下次 `reconcile_dangling_weight_runs` 改判 `failed`（验证残留限制如实、非假成功）。
- **H-B3 硬上限**：并发发起 `cap+1` 个 `start_training`，断言恰好 1 个 429、实际注册作业数 `== cap`（不超）、被拒方无任何模型/run 落库（守门零副作用不回归）。
- **H-B2/H-B4 取消与关停**：加载阶段取消 → 不必跑完即转 `cancelled`；`shutdown` 对卡住作业带超时 `force_cancel`，run 终为 `cancelled`，关停不被无限拖。
- **H-B5 边界（R-01 真实键）**：非法 bar→400 `FACTOR_INVALID_BAR`（且校验在区间 413 之前，R-04）；`split∉(0,1)` / 非数值 `split` / `l2<0` / `nEstimators` 非正整数 →400 `FACTOR_TRAIN_INVALID_CONFIG`；**`clip≤0` 与缺省 `normWindow` 不被拒**（保哨兵语义、不要求键）；样本不足→`error`(`WEIGHT_TRAIN_INSUFFICIENT_SAMPLES`)+run `failed`，且**非数值 split 不再**被误标为 INSUFFICIENT_SAMPLES（typed 异常 + 边界 400 双保险）。
- **H-B6 TTL**：假时钟推进 > 600s → 终态作业被周期清扫淘汰，晚订阅得 `NOT_FOUND`；running 永不淘汰。
- **H-B7 WS**：心跳 `ping` 下发；`error`/`cancelled` 终态晚订阅可全量回放后关闭（不悬挂）。
- **H-B8**：自动剪枝后 `best_valid_ic` 仍为历史最佳（best-IC run 受保护）；剪枝条数有 log 断言。
- **H-F1**：`runs` 查询失败渲染**错误态**（非空状态），含重试。
- **H-F2**：从列表训练已存模型**不写** `boundModelId`；builder 发起才写；切换组合复位。
- **H-F4**：在 `start` 解析前卸载 → 不 `openStream`、不 setState（无告警、无泄漏 socket）。
- **H-F5**：`modelToTrainTarget` 产出 `instId/bar == model.instId/model.bar`。
- **H-F6**：空/纯空白名禁用提交，合法名解禁。
- **等价性不回归（硬门禁）**：`f=1.0` 末步与 `POST /factor-sets/train` 的 `weights/normStats/metrics` 逐字段相等的既有测试**继续绿**；factor_set CRUD、factor_score 回测/对比/Walk-forward 既有测试**继续绿**。

### 8.3 验收标准（Definition of Done · 面向下游 Subtask #2/#3）

1. P0（H-B1）与全部 P1（H-B2…B5、H-F1…F6）均有**先红后绿**的回归测试守护；新增/改写代码守 800 行/方法 50 行/参数 ≤5/嵌套 ≤4/圈复杂度 ≤10 清洁度阈值。
2. 后端 `pytest`（quant，含本轮新增套件）全绿、`-m 'not network'` 默认集全绿；前端 `pnpm test`（vitest，`NODE_ENV=test`）全绿；`tsc --noEmit` 与 lint 通过。
3. 既有等价性、factor_set ⇄ strategy 同步、factor_score 全链路测试**零回归**；**无新增表/列、零迁移**；`scripts/dev.py` 可启动/编译。
4. 实测三类异常路径不再悬挂/误导：落库失败（WS 收终态）、连接抖动（UI 显示中断并恢复）、记录加载失败（显示错误而非空）。
5. 交互可访问性达标：记录表键盘可选、视图切换焦点迁移、破坏性操作应用内确认、`focus-visible` 可见；文案无英文键泄漏、错误码有友好映射。

### 8.4 交付优先级闸（R-10·适度性）

本轮发现量较大，为保单轮可交付、避免在 P2 镀金上拖累 P1 正确性，按下列优先级分批，前一批未全绿不进下一批：

1. **必交付（Must-ship，门禁）**：P0 `H-B1`（含 R-02 重试）+ 后端 P1 `H-B2/B3/B4/B5`（含 R-01/R-03/R-04）+ 前端 P1 `H-F1…F6`，及其先红后绿回归测试。**此批全绿才算本轮达标。**
2. **有余力再做（Should-ship）**：P2 体验/可访问性 `H-F7…F10`、后端 `H-B6/B7/B8`。其中 `H-B6` 的常驻 sweeper、`H-B7` 的满队列兜底属可选增强（R-08/R-09），缺席不阻塞必交付批。
3. **测试护栏（主线 C）**：随对应 H- 项同批落地（bug 类先红后绿），勿留到最后集中补。

---

## 九、风险与缓解（Risks & Mitigations）

| 风险 | 影响 | 缓解 |
|---|---|---|
| 加固改动触碰 `f=1.0` 等价路径 | 双路径漂移、违反硬门禁 | 本轮**不动** `train_weights/fit_at_fraction/build_train_result`；H-B5 仅给 `prepare_curve_sample` 加异常**子类**、不改判定；等价性测试作为门禁先绿。 |
| `_persist` 改尽力而为后掩盖 DB 故障 | 静默吞错、违反「绝不静默降级」 | 瞬态失败**有界重试一次**（R-02）；仍失败则**显式 `logger.error`**、run 行**真实留 `running`** 待启动对账改判（不假装成功）；终态事件如实下发，前端可见。残留：硬故障下该 run 在本进程余生显示「训练中」直至重启对账（诚实取舍，见 §3.1 R-02 限制）。 |
| 同步预占名额计数泄漏 | 名额虚耗、长期 429 | `reserve` 后**任何**失败路径 `release`（try/except）；`start_job` 注册即转 running 计数；测试断言并发后 `_reserved==0`。 |
| `force_cancel` 在 CPU 线程池中无法即时中断 | 关停仍可能等待单步 fit | 接受单步线程池不可打断（不强杀线程）；`force_cancel` 取消的是协程，配合超时 join 兜底，最坏等待为「当前单步 fit + 超时」，有界。 |
| WS 新增 `ping` 破坏老前端 | 事件解析报错 | `onEvent` 对未知 type 天然忽略（现有 if/else 链落空），`ping` 不进任何业务分支；契约纯加法、向后兼容。 |
| `boundModelId` 复位过激 | builder 同会话复用失效（退回每次新建模型） | 复位仅在「切换组合 / 清空成员」等**显式语义切换**触发；同组合连续训练仍复用（保留 R-02 去重价值），测试守护两侧。 |
| 自动剪枝保护 best-IC run 致老记录长存 | 极端下保留集略超 cap | 软上限本就允许超 `cap` 少量；只多保 1～2 条；用户显式删除 best run 时正常 `repoint`。 |
| 焦点迁移 / 弹窗替换引入键盘陷阱 | 可访问性回归 | 复用应用既有 `Modal`（已带 focus-trap/Esc/restore）与 `FactorLab` `headingRef` 成熟模式，不自造焦点逻辑。 |
| TTL 周期清扫协程未随关停取消 | 关停泄漏任务 | lifespan shutdown 段 `sweeper.cancel()`，与 manager.shutdown 同段；测试断言关停后无悬挂任务。 |

---

## 十、对下游实现者的提示（Implementation Notes）

- **顺序**：先补复现测试（红）→ 再改业务（绿）→ 跑等价性 + factor_set 全链路确认零回归。建议按「P0(H-B1) → 后端 P1(H-B2/B3/B4/B5) → 前端 P1(H-F1/F2/F3/F4/F5/F6) → P2 打磨 → 测试收尾」推进，每步独立可验证。
- **复用而非另起**：取消/关停/对账抄 `paper/manager.py` 与既有 `_reconcile_weight_train_runs`；WS 心跳参考 agent 流的 `agent_heartbeat_seconds`（`config.py:157`）思路；应用内确认弹窗在 `components/ui` 的 `Modal` 外壳上新增薄 `ConfirmDialog`（R-07，`Modal` 本身无 confirm 契约）；焦点迁移复用 `FactorLab` `headingRef`。
- **守门顺序不可乱（R-04）**：H-B3 改预占 + H-B5 加 400 校验后，守门固定为 ①成员 400 → ②bar 400 → ③config 400 → ④区间 413 → ⑤并发 429（预占）→ 解析/落库/注册；bar/config 的 400 **必须先于**区间 413（`_guard_range` 自身调 `bar_to_seconds`）；任一命中即返回、零持久化副作用（R-01 不回归）。
- **绝不触碰**：`db/models.py` 的 `WeightModel/WeightTrainRun` 列定义、`factor_sets` 表、factor_set ⇄ factor_score 同步事务、`POST /factor-sets/train`、`f=1.0` 等价装配。
- **清洁度**：新增测试与改写均守 800/50/≤5/≤4/≤10 阈值；DB 写聚合传参延续 frozen dataclass（`WeightRunOutcome` 等）。
- **若需偏离本方案任一决策**：回填本文件并重新过评审（与上游同范式）。

---

> 本文档仅产出**设计**，不含实现代码改动；除本 `spec.md` 外不修改任何源文件，不执行 `git commit`。下游 Subtask 据此逐行落地。

---

## 评审结论（Review Verdict）

**结论：有条件通过（Approved with Conditions）。**

**总体评价**：本加固方案质量高。`H-B1…B8`、`H-F1…F10` 共 18 条发现经评审**逐条对照已提交实现（`9552ac9`）读码核验全部属实**，`file:line` 锚点准确（绝大多数 ±3 行内），P0（落库失败致 WS 永久悬挂 + run 卡 `running`）定位精确、修复方向正确。方案严守「纯加法、零 schema 变更、不动 `f=1.0` 等价路径与 factor_set 同步契约」的硬纪律，与 `CLAUDE.md` 清洁度阈值（800/50/≤5/≤4/≤10）、camelCase / 秒级 UTC / `{error:{code,message}}` / 「绝不假数据·绝不静默降级」诸约定一致。四维度评价：**可行性**——所有拟改符号（`reserve/release/force_cancel/snapshot/is_terminal/_prune_finished/onState/Modal/model.instId·bar/enabled 过滤` 等）经核验**均真实存在或为干净加法**；**完整性**——异常/边界/卸载/重连/取消/关停路径覆盖到位；**一致性**——本评审已修正个别与真实代码不符之处；**适度性**——主体合理，少量 P2 偏镀金已标注降级路径。

**评审已就地修复（P0/P1，已并入正文并升版 v2）**：

- **R-01（P1）** §3.5 `_validate_train_config` 重写为**仅校验真实且出现的 camelCase 键**（`split∈(0,1)`、`l2≥0`、`nEstimators/maxDepth` 正整数），剔除臆造键、**不破坏** `clip≤0` 关闭裁剪哨兵、不冗余校验 `normWindow`。
- **R-02（P1）** §3.1 `_persist` 补**有界重试一次**并显式记录「硬故障下 run 行留 `running` 至重启对账」的残留限制（诚实、不假成功；禁止用全局对账掩盖）。
- **R-03（P1）** §3.3 `reserve()` 改**动态读** `get_settings().max_weight_train_jobs`，去掉构造期 `_cap` 缓存（避免与 settings 覆写脱钩、保障并发回归测试确定性）。
- **R-04（P1）** §3.3/§3.5/§10 **固化守门序**：成员 400 → bar 400 → config 400 → 区间 413 → 并发 429（预占）——bar/config 的 400 必须先于区间 413，杜绝非法 bar 仍抛 500。

**评审已澄清/校正（P2，已并入正文）**：R-05（typed 异常作用域）、R-06（H-F9 锚点校正为 `WeightTrainLive.tsx:220`）、R-07（`Modal` 需薄 `ConfirmDialog` 包装，已入文件计划）、R-08（常驻 sweeper 可选 + cancel-safe + 禁全局对账）、R-09（`H-F3` 不被 `H-B7` 阻塞）、R-10（§8.4 交付优先级闸）。

**放行条件（下游 Subtask #2/#3 必须满足，否则视为未达标）**：

1. **守门序不可乱**：严格实现 R-04 的五段守门序；任一命中即返回、零持久化副作用（守 R-01「守门优先」）。
2. **config 校验按 R-01 表落地**：只校验真实且出现的 camelCase 键，**不得**强制 `clip>0` 或要求 `normWindow`，**不得**引用 `n_estimators/max_depth/min_samples_leaf/learning_rate` 等非 config 键。
3. **持久化诚实**：R-02 的重试 + 残留限制如实保留；**严禁**用 sweeper 周期调用全局 `reconcile_dangling_weight_runs`（会误杀在跑 run）。
4. **等价性硬门禁先绿**：`f=1.0` 末步与 `POST /factor-sets/train` 逐字段相等、factor_set ⇄ strategy 同步、factor_score 全链路既有测试**零回归**；**无新增表/列、零迁移**。
5. **bug 类先红后绿**：P0 + 全部 P1 均有可稳定复现的失败测试先行，再改业务码。
6. **优先级闸**：按 §8.4 先交付「必交付批」并全绿，再做 P2；P2 缺席不得阻塞必交付批，但 P2 不得以牺牲 P1 正确性为代价镀金。

满足上述 6 条放行条件即视为通过。本文档（v2）**已无未决 P0/P1**。

---

## 实施过程发现的方案缺陷（Issues Found During Implementation）

> 下游实现者（Subtask #2）在按本 v2 方案逐行落地时记录的、与方案不完全吻合之处。均为**完整性**层面的小缺口，
> 已就地修正并保持设计意图不变；不涉及任何 P0/P1 正确性偏离。

- **[完整性] §五测试变更计划遗漏 `test_weight_models_api.py`。** H-B3 把守门从 `manager.active_count() >= cap`
  改为同步预占 `manager.reserve()`（§3.3），而既有 `test_weight_models_api.py::test_gate_busy_429_no_orphan`
  用的 `_BusyManager` 测试替身只实现了 `active_count()`，新口径下会因缺 `reserve`/`release` 抛 `AttributeError`。
  §五「测试」表未列该文件。**处置**：给 `_BusyManager` 补 `reserve()→False`（繁忙即 429）与空 `release()`，
  语义与新硬上限一致；该测试继续守护「429 守门零孤儿」。属设计 file-change-plan 的**枚举遗漏**，非口径错误。

- **[一致性·提示] 列表默认过滤口径（H-F10）。** §4.10 要求「增『显示已停用』开关，绑定 `list({enabled})`」。
  落地时默认 `showDisabled=false → list(true)`（仅显示启用），开关开启才 `list()`（含停用）。这相对迭代 1 的
  `list()`（显示全部）是一次**默认行为收敛**——与「开关默认关、需要时才看停用」的直觉一致，符合 §4.10 意图；
  在此明示以免与迭代 1 行为对照时误判为回归。
