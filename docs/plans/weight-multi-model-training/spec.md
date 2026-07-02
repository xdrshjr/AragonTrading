# 权重多模型并行训练与择优（weight-multi-model-training）设计文档

> 作者：解决方案架构师（Anthropic Engineering）
> 范围：因子实验室「权重模型」工作区
> 版本：**v2**（2026-07-02 评审修复；v1 为方案设计节点产出）
> 状态：已过设计评审，P0/P1 已就地修复，供下游工程师逐行实现
> 关联既有实现：`weight-model-workspace`（§流式训练）、`weight-model-workspace-hardening`、
> `weight-model-workspace-refinement`、`weight-model-backtest-replay`

---

## 评审记录（Review Notes，v2）

> 评审人：资深评审（Anthropic Engineering）｜日期：2026-07-02
> 方法：逐节把方案对既有符号与不变量的引用，对照 `services/quant`（`weight_train_manager.py` / `weight_model_service.py` / `weight_model_repositories.py` / `models.py` / `session.py` / `api/weight_models.py` / `schemas.py` / `config.py` / `main.py`）与 `apps/web`（`WeightModelPanel/List/Detail`、`FactorSetBuilder`、`store.ts`、`weightModel.ts`、`weightModelApi.ts`、`weightTrainWs.ts`、`ws.ts`）现网代码**逐一核验**。共发现 1×P0 + 4×P1 + 8×P2；**P0/P1 已在本版正文就地修复**（详见各项「处置」列与对应小节）。

| # | 维度 | 严重度 | 问题摘要 | 处置 |
|---|---|---|---|---|
| R1 | 可行性 | **P0** | 方案给**既有** `weight_train_runs` 表加 `batch_id`/`variant_label` 两列，却称「`create_all` 加法建表、零迁移」。`Base.metadata.create_all` 只建**缺失的表**，绝不给**已存在的表**补列——老库（=所有现网库）上 ORM 声明有列而物理表无列，`add_weight_run` 等 INSERT 立即 `OperationalError: no such column`，**连既有单次训练路径一并崩**。 | 已修：§3.2/§5.2 改为仿 `session.py::_ensure_trade_risk_columns` 增 `_ensure_weight_run_columns`（`PRAGMA table_info`+`ALTER TABLE ADD COLUMN`+`CREATE INDEX IF NOT EXISTS`），`init_db` 内 `create_all` 后调用；新表 `weight_train_batches` 仍走 `create_all`。 |
| R2 | 正确性 | **P1** | 调度泵先在锁外 `reserve()` 失败、再 `await await_slot()`（锁内 wait）——两步间若有子作业终态并 `notify_all()`，唤醒丢失，排队候选在**有空名额时永久卡 `queued`**（快作业尤甚）。 | 已修：§2.3 改为「同一把 `_slot_cond` 下 `while not reserve(): await wait()`」的检查-等待原子闭环；取消路径显式 `notify_all` 唤醒；持有裸预占时绝不 break（防名额泄漏）。 |
| R3 | 正确性 | **P1** | 子作业→批次的转发协程 `while ...: await q.get()` 语义未定义；子作业到终态后队列再无事件，`q.get()` 永阻塞 → `_await_all_children` 挂起 → 批次**永不 `batchDone`**。 | 已修：§2.4 明确转发协程见终态子事件（done/error/cancelled）即 `unsubscribe` 退出（并处理「订阅晚于终态」用 snapshot 回放后直接收尾），对齐 `ws_weight_train` 终态即关；`_await_all_children` join 全部转发协程。 |
| R4 | 一致性 | **P1** | `source="batch"` 不在既有白名单 `WEIGHT_MODEL_SOURCES=("builder","manual")`；「指针机制零改动复用」低估改动面。 | 已修：§2.2/§3.2/§5.1 明确把 `"batch"` 并入 `WEIGHT_MODEL_SOURCES`，并要求审计所有读 `source` 的分支（主列表过滤为**新增**前端逻辑；run/backtest/delete 路径须把 batch 父模型当普通模型对待）。 |
| R5 | 可行性 | **P1** | `WeightTrainRun.model_id` 是**无 `ondelete=CASCADE`** 的外键，且 SQLite 外键约束未开启；「真外键、级联删」措辞会误导实现者依赖 DB 级联。 | 已修：§2.2/§2.5/§3.1/§4.1 改为「service 层单事务级联删（候选 run+父模型+批次行），镜像既有 `delete_weight_model`」。 |
| R6 | 可行性 | P2 | §2.3 称 `start_training` 为「reserve 后 await `add_weight_run` 再 start_job」，实际其间有 `_resolve_model`+`add_weight_run` **两处** await；结论（同步预占跨 await 安全，H-B3）仍成立，仅计数措辞失准。 | 已澄清：§2.3 更正为「预占后仅 `mark_run_running` 一处 await；既有 `start_training` 为两处 await，同属预占跨窗范式」。 |
| R7 | 一致性 | P2 | `weight_train_run_out` 的 IC/nTrain **嵌套在 `metrics` 内**、run 标识键为 `id`（非 `runId`）；排行榜派生须读 `metrics.icOut/icIn/nTrain`，roster 的 `runId` 与详情的 `id` 需桥接。 | 已澄清：§2.5/§4.2 加注。 |
| R8 | 完备性 | P2 | 父模型 `method` 列单值，批次跨 K 个方法——未定义父模型存哪个 method。 | 已澄清：§2.2 定为「取当前 best/selected 候选方法，仅供批次 tab 展示；batch 父模型默认从主列表过滤，无功能耦合」。 |
| R9 | 一致性 | P2 | `R-02`/`R-06` 在三份前序评审文档中各有不同含义，裸引用有歧义。 | 已澄清：§2.4/§七 标注来源轮次（R-06=workspace 轮·快作业/晚订阅回放；R-02=hardening 轮·`_persist` 有界重试）。 |
| R10 | 一致性 | P2 | `RunHistoryTable` 是 `WeightModelDetail.tsx` 内私有组件、非共享导出件。 | 已澄清：§六.11 改为「复刻其键盘可达模式」而非 import。 |
| R11 | 完备性 | P2 | 批次派生状态机与「全候选失败」路径未完整定义。 | 已补：§4.1 定义批次派生状态；§2.5 定义无成功候选时 best/apply/select 全禁用。 |
| R12 | 正确性 | P2 | 「应用最佳到组合」复用 `buildTrainResult(winner, target, …)` 时，`target.method` 须为 winner 方法（helper 从 target 取 method），不可用共享/首个方法。 | 已澄清：§2.5 加注。 |
| R13 | 一致性 | P2 | 新模块 `weight_batch_manager.py`（runtime+manager+调度泵）易破 CLAUDE.md 800 行/50 行阈值。 | 已提示：§3.1 加注，超阈即按职责拆分（runtime / manager 两文件）。 |

无 P0/P1 遗留。P2 均已在正文澄清或补全。放行条件见文末《评审结论》。

---

## 一、Overview（概述）

因子实验室的「权重模型」工作区现已具备**单次流式训练**能力：用户从「权重训练与组合」搭建台点「训练权重」，或在模型列表/详情点「训练」，都会进入一个全屏 `WeightTrainLive` 视图，对**单一方法**（linear / equal / ic_weight / random_forest / xgboost 之一）发起一次训练，实时观测阶段进度、扩样本 loss/IC 曲线与收敛中的权重条，完成后可「应用到组合并返回」或「查看模型详情」。后端 `WeightTrainManager` 实际上**已支持最多 `max_weight_train_jobs`（默认 4）个作业并发**，但前端把用户强行漏斗进「一次一个方法、且离开列表」的阻塞式单视图，导致用户无法在一屏内同时发起并观测多个候选方法、更无法在完成后横向比较、择优。

本设计新增**「多模型训练竞技场」（Multi-Model Training Arena）**：用户在同一份 `{成员 members + 标的/周期 + 时间窗 + 前瞻 horizon + 阈值 thresholds}` 共享规格下，一次性勾选**多个候选方法（及可选超参变体）**并行训练；在一屏「竞技场」内同时观测每个候选的实时进度与曲线；训练过程中即滚动更新一张按**样本外 IC（icOut）**排名的**排行榜**，最优候选高亮（奖杯标记）；全部完成后，用户可**一键择优**——把最优候选应用回组合（仅搭建台发起时）、或将其保留为可复用的权重模型，并对任一候选查看其完整训练结果（学习曲线回放 + 权重条 + K 线回测）。

设计的第一性原则是 **「批次是既有单次训练引擎之上的一层薄编排，绝不派生第二套训练器」**（镜像 backtest-replay 的「回测不是新引擎」不变量）。每个候选候选在后端仍是一个**与今天字节级等价**的 `_TrainJob`（复用 `train_weights` 末步装配、复用进程内 K 线缓存、复用 `run_in_threadpool` 的 CPU 卸载）。并发正确性由**既有全局并发上限的单一真源 `WeightTrainManager.reserve()`** 统一治理：候选数超过可用名额时进入**服务端 FIFO 队列**，名额释放即泵起下一个，从而在「稳定、可靠、稳健、正确」上继承既有加固成果，同时把资源占用严格封顶。

---

## 二、Technical design（技术设计）

### 2.1 架构总览

```text
Builder / List ──(weightBatchTarget)──► WeightModelPanel(view=batchLaunch)
        │                                        │
        │  POST /weight-models/batches           ▼
        ▼                             WeightBatchLauncher（勾选方法/变体）
  weight_batch_service.start_batch ──► 建 WeightTrainBatch + parent WeightModel(source="batch")
        │                             + 每候选一条 WeightTrainRun(status="queued", batch_id)
        ▼
  WeightBatchManager.start(runtime) ──► WeightBatchRuntime.scheduler（async 泵）
        │  逐候选 FIFO：reserve() 成功→queued→running→manager.start_job(JobSpec)
        │              名额满→await manager.reserve_when_free()（同锁预占或等待，名额释放即唤醒）
        ▼
  订阅每个子 _TrainJob 的事件队列 ──► 打标(runId,method)转发到 batch 级 fan-out buffer
        ▼
  WS /ws/weight-batch/{batchId} ──► WeightBatchArena：候选卡网格 + 实时排行榜 + 叠加曲线
        ▼
  全部终态 → emit batchDone → 排行榜定稿 → 用户「择优」→ POST /batches/{id}/select
                                                        └► 或 builder-origin「应用最佳到组合并返回」
```

### 2.2 后端：候选=既有 _TrainJob，批次=薄编排

- **零第二训练器**：候选的 `JobSpec` 与 `weight_model_service.start_training` 构造的完全同构（`run_id/model_id/rest/members/method/config/window/horizon/run_cap/curve_steps`）。因此单个候选（方法 M）与「独立 `POST /weight-models/train`（方法 M，同输入）」**逐字节等价**，由新增 `test_weight_batch_equivalence.py` 守护。**绝不改** `engine/factors/training.py`。
- **共享规格、仅方法/超参异**：一个批次的全部候选共享同一 `members / instId / bar / startTs / endTs / horizon / thresholds(normWindow)`，差异仅在 `method` 与可选 `config` 变体。共享成员保证 parent 模型可承载全部候选记录，且每候选回测复用 parent 的成员快照（见 2.4）。
- **父模型（parent WeightModel）**：每次批次创建一个 `source="batch"` 的 `WeightModel`，`members_json/config_json` 取共享规格，`factor_set_id=req.factorSetId`（弱溯源）。K 个候选 run 全部挂在这一个父模型下（`WeightTrainRun.model_id` 为外键，但**无 `ondelete=CASCADE`**、且 SQLite 外键约束未开启——级联删由 **service 层单事务**保证，镜像既有 `delete_weight_model`，**绝不依赖 DB 级联**，见 2.5/R5）。既有 `repoint_weight_model` 维护父模型的 `best_valid_ic=max(icOut)`（即「最优候选」的 IC **值**）与 `latest_run_id=max(run.id)`（**最新**成功候选，注意**非**最优）——「最优候选的 runId」由排行榜 `argmax icOut` 或显式 `selected_run_id` 派生，**不从** `latest_run_id` 取（R8）。父模型 `method` 列单值，缺省取 best/selected 候选方法，仅供「训练批次」tab 展示（batch 父模型默认从主列表过滤，无功能耦合）。**一致性要求（R4）**：`source="batch"` 必须并入白名单 `WEIGHT_MODEL_SOURCES=("builder","manual")`→`("builder","manual","batch")`，并审计所有读 `source` 的分支——现网仅 create 时写死 `builder`/`manual`、无 source 分支逻辑，故风险面收敛于「主列表过滤（新增前端逻辑）」与「run/backtest/delete 路径须把 batch 父模型当普通模型对待」两处。

### 2.3 并发调度（本设计的正确性核心）

**单一名额真源**：候选与独立训练都经 `WeightTrainManager.reserve()`（同步、无 await 预占）竞争同一 `max_weight_train_jobs` 名额。批次**不引入第二套并发计数**，从根本上杜绝「批次内并发 + 独立训练并发」叠加突破硬上限。

**名额释放通知（新增）**：在 `WeightTrainManager` 增加一把 `asyncio.Condition`（`_slot_cond`）。任一 `_TrainJob` 转终态时回调 `manager.notify_slot_free()`：`async with self._slot_cond: self._slot_cond.notify_all()`。**关键顺序**：`on_terminal` 必须在 `_persist` 把 `self.status` 置为终态**之后**触发（此刻 `active_count()` 已不再计入本作业、名额确已空出），否则被唤醒者 `reserve()` 仍见旧占用而空转。`_TrainJob` 新增可注入 `on_terminal: Callable|None`，由 `start_job` 绑定为 `manager.notify_slot_free`。

**预占-等待原子闭环（消除丢唤醒，修复 v1 缺陷 R2）**：**不要**用「锁外 `reserve()` 失败 → 再 `await await_slot()`」的两段式（reserve 与 wait 之间的缝隙里若发生 `notify_all`，唤醒永久丢失，排队候选卡死）。改为在 `WeightTrainManager` 提供 `async def reserve_when_free(self, cancelled)`，把「检查」与「等待」收进**同一把锁**：
```text
async def reserve_when_free(self, cancelled) -> bool:
    async with self._slot_cond:
        while not self.reserve():        # reserve() 同步，锁内调用期间不让出
            if cancelled():
                return False              # 等待期间被取消：未持名额，安全退出
            await self._slot_cond.wait()  # 释放锁并挂起；被 notify_all 唤醒后重判
        return True                       # 已持有一个预占名额
```
任一终态转迁要么 happens-before 本次 `reserve()`（reserve 直接见空名额），要么发生在挂起进入 `wait()` 之后（必被 `notify_all` 唤醒）——经典条件变量范式，杜绝丢唤醒。这是跨来源（不同批次 / 独立训练）竞争同一名额的唯一等待点。

**调度泵（`WeightBatchRuntime.scheduler`，async 协程）**：
```text
pending = deque(candidates)              # FIFO
while pending and not self._cancelled:
    got = await manager.reserve_when_free(lambda: self._cancelled)  # 锁内「预占或等待」，防丢唤醒（R2）
    if not got:                          # 仅在等待期间被取消才返回 False（此时未持名额）
        break
    cand = pending.popleft()             # 已持一个预占名额 → 必达 start_job（持裸预占时绝不 break，防泄漏）
    await repo.mark_run_running(run_id=cand.run_id)     # queued→running：预占后唯一一处 await（跨窗安全，H-B3）
    job = manager.start_job(JobSpec(...))               # 内部 release() 把预占转成 running 计数
    self._attach(cand, job)                             # 订阅 job 队列、起转发协程；见终态即退（§2.4）
await self._await_all_children()          # 等全部子作业 + 转发协程收尾
self._finalize_batch()                    # emit batchDone（定稿 ranking）；runtime 转终态
```
关键正确性：预占（`reserve_when_free` 内的 `reserve()`）→`start_job()` 之间**只夹一处 DB `mark_run_running` await**；既有 `start_training` 是「reserve 后 `_resolve_model`+`add_weight_run` **两处** await 再 start_job」（R6 更正 v1「一处 await」的措辞）——两者同属「同步预占跨越 await 窗口」范式（H-B3，hardening 轮），预占名额在整个窗口内有效，TOCTOU 安全。泵的唯一让出点是 `reserve_when_free` 内的 `wait()`；一旦持有预占即无条件走到 `start_job`，绝不在 reserve↔start 间 break 或让出（否则名额泄漏）。

**取消**：
- 单候选：`POST /batches/{id}/candidates/{runId}/cancel` → 若已 running，`manager.cancel(jobId)`（协作式，作业续跑落 `cancelled` 终态）；若仍 queued，直接 `repo.finalize` 为 `cancelled` 并从 `pending` 移除、emit 事件（绝不留 DB running/queued 泄漏）。
- 整批：`POST /batches/{id}/cancel` → `runtime.request_cancel()`：置 `_cancelled`、对全部 running 子作业 `request_cancel()`、对全部 queued 候选直接 finalize 为 `cancelled`、并调 `manager.notify_slot_free()` **显式唤醒**卡在 `reserve_when_free` 的泵（泵醒后见 `cancelled()` 为真即退，不再启新候选）——否则泵可能永久挂起于 `wait()`。
- 进程关停：`WeightBatchManager.shutdown()`（镜像 `WeightTrainManager.shutdown`）先置全 runtime 取消标志、带超时 join、`force_cancel` 兜底；未及启动的 queued 候选由**启动对账**改判 failed（见 2.6）。

**公平性**：多个批次/独立训练竞争同一 `_slot_cond` 广播，采用「唤醒即抢 `reserve()`」的尽力而为 FIFO，不做严格优先级（见 §七风险）。可选 `max_weight_batch_candidates` 封顶单批候选数以界定资源上界。

### 2.4 批次级实时流（fan-out）

`WeightBatchRuntime` **镜像 `_TrainJob` 的 `subscribe()/unsubscribe()/snapshot()/_push()` 分层**：持一个全量事件缓冲（含 roster 快照与终态，供晚订阅/重连无损回放，沿用 R-06｜workspace 轮·快作业/晚订阅终态回放）+ 订阅者 `asyncio.Queue` 集合。`_attach(cand, job)` 内起一个转发协程（`subscribe()` 与 `snapshot()` 之间**无 await**，杜绝缝隙重复）：
```text
q = job.subscribe()
for ev in job.snapshot(): forward(cand, ev)        # 回放已缓冲事件（含 roster/curve/终态）
if snapshot_last_is_terminal():                    # 快作业/订阅晚于终态：队列不会再来事件
    job.unsubscribe(q); return                     # 直接收尾，绝不进 q.get() 循环
while True:
    ev = await q.get(); forward(cand, ev)
    if ev.type in _TERMINAL_TYPES:                 # done/error/cancelled
        job.unsubscribe(q); break                  # 终态即退（对齐 ws_weight_train「终态即关」）
```
`forward` 把子事件包成 `{type:"candidate", data:{runId, method, event:<原 TrainStreamEvent>}}` 后 `_push`。**「终态即退」是必需的（修复 v1 缺陷 R3）**：否则子作业终态后 `q.get()` 永阻塞 → `_await_all_children` 挂起 → 批次永不 `batchDone`。`_await_all_children` join 全部转发协程（与其订阅的子作业）。批次自身另 `_push`：`batch`（roster 快照）、`candidate` 状态迁移（queued→running）、`batchDone`（含定稿 ranking）。WS 端点 `/ws/weight-batch/{batchId}` **抄 `ws_weight_train`**：accept→取 runtime→subscribe+snapshot 回放→终态即关→`_WS_HEARTBEAT_SECONDS` 静默下发 `ping` 兼半开探测→满队列丢终态兜底补发。

### 2.5 择优与保留

- **默认最优**：`bestCandidate = argmax icOut`（tie-break：icIn → nTrain → runId）。**取值口径（R7）**：`icOut/icIn/nTrain` 嵌套在 run 的 `metrics_json` 内（`weight_train_run_out` 输出为 `metrics.icOut/icIn/nTrain`，**非**顶层字段；run 标识键为 `id`，roster/ranking 对外用 `runId`——批次序列化层负责桥接）。前端排行榜实时高亮，`batchDone.ranking[isBest]` 与之一致。**无成功候选**（全 failed/cancelled）时 `ranking` 不置任何 `isBest`，`select`/「应用最佳」全禁用（见 §4.1 派生状态）。
- **持久择优**：`POST /batches/{id}/select {runId}` → 校验该 run 为 `succeeded` 且有权重，写 `batch.selected_run_id`（409 `WEIGHT_BATCH_RUN_NOT_SELECTABLE` 否则）。父模型的 `latest_run_id/best_valid_ic` 由既有 finalize 维护，`selected_run_id` 是「用户显式选择」的权威记录（缺省对齐 best）。
- **应用到组合（仅 builder-origin，守 G-F1）**：竞技场保留每候选终态 `TrainResultEvent`；「应用最佳到组合并返回」复用 `buildTrainResult(winnerResult, target, {modelId:parentModelId, runId:winnerRunId})` → `setWeightTrainResult` → 切 builder。**注意（R12）**：`buildTrainResult` 从 `target` 取 `method/members/thresholds`，故这里的 `target` 须是**按 winner 合成**的 `WeightTrainTarget`——`target.method = winner.method`（**不可**用共享/首个候选方法），`members/thresholds` 取共享规格；winner 的 `normStats` 主导阈值对齐（既有前端自动对齐，规避 `FACTOR_NORM_WINDOW_MISMATCH`，见 [[aragon-weight-model-workspace]] 回流口径）。model/list-origin 批次**绝不**渲染该动作（杜绝劫持草稿）。
- **保留/清理**：父模型即真·可复用模型（可改名、回测、再训）。竞技场提供「只保留最佳，删除其余候选」便捷项：删除非 selected 的候选 run（`DELETE /weight-models/{parentId}/runs/{runId}`，既有路径，删后 `repoint`），二次确认（复用 `ConfirmDialog`）。

### 2.6 生命周期与对账

- **启动对账**：`reconcile_dangling_weight_runs` 与 `fail_orphan_running_runs` 的 `status == "running"` 改为 `status in ("running","queued")`，把上轮残留的 running/queued 批次候选统一改判 `failed`（`WEIGHT_TRAIN_INTERRUPTED`）并 `repoint`。批次视图据此显示中断候选。
- **终态回收**：`WeightBatchManager` 复用 `_prune_finished`（TTL + 数量上限）回收终态 runtime；`main._weight_train_sweeper` 增补 `batch_manager._prune_finished()`。running/queued 批次绝不回收。
- **关停顺序**：`main.lifespan` 在 `get_weight_train_manager().shutdown()` **之前** `await get_weight_batch_manager().shutdown()`（先停批次泵、再停底层作业，避免泵在作业消失后空转）。

---

## 三、File / module change plan（文件变更计划）

### 3.1 后端（新增）

| 文件 | 意图（一行） |
|---|---|
| `services/quant/app/db/weight_batch_repositories.py` | 批次行 CRUD + 子候选查询 + `selected_run_id` 写入 + **service 层单事务级联删**（删候选 run+父模型+批次行，镜像 `delete_weight_model`，**不依赖 DB 外键级联**，R5）+ `mark_run_running`/`add_queued_weight_run`；经 `repositories.py` re-export。 |
| `services/quant/app/factors/weight_batch_service.py` | 批次编排：`start_batch` 守门（成员/bar/config/区间/候选数）→ 建批次+父模型+queued 记录 → 注册 runtime；`list/get/select/cancel/delete`。 |
| `services/quant/app/factors/weight_batch_manager.py` | `WeightBatchRuntime`（fan-out + FIFO 调度泵）+ `WeightBatchManager`（进程内单例注册表、TTL 回收、关停）。**尺寸提示（R13）**：runtime+manager+调度泵易破 CLAUDE.md 800 行阈值，超阈即拆为 `weight_batch_runtime.py` / `weight_batch_manager.py` 两文件。 |
| `services/quant/app/api/weight_batches.py` | 批次路由（start/list/get/select/cancel/delete/候选取消）+ WS `/ws/weight-batch/{batchId}`（抄 `ws_weight_train`）。 |

### 3.2 后端（修改）

| 文件 | 意图（一行） |
|---|---|
| `services/quant/app/db/models.py` | 加 `WeightTrainBatch` **新表**（`create_all` 自动建）；`WeightTrainRun` 加 `batch_id`(int,null,索引)、`variant_label`(str,默认"")；状态机文档补 `queued`。**注意（R1/P0）**：`create_all` 不给**既有**表补列——`weight_train_runs` 的两列须靠 `session.py` 启动补丁 `ALTER TABLE`（见下方 `session.py` 行 + §5.2），否则老库崩。 |
| `services/quant/app/factors/weight_train_manager.py` | 加 `_slot_cond`/`notify_slot_free`/`reserve_when_free`（检查-等待同锁闭环，防丢唤醒 R2）；`_TrainJob` 加 `on_terminal` 钩子并在 `_persist` **置终态之后**触发；`start_job` 绑定钩子。 |
| `services/quant/app/db/weight_model_repositories.py` | `reconcile_dangling_weight_runs`/`fail_orphan_running_runs`/`_prune_runs` 的 running 守门纳入 `queued`；`add_weight_run` 支持 `status`（running\|queued）或新增 `add_queued_weight_run`+`mark_run_running`；**把 `"batch"` 并入 `WEIGHT_MODEL_SOURCES`**（否则 batch 父模型来源非法，R4）。 |
| `services/quant/app/db/session.py` | **（R1/P0 修复）** 新增 `_ensure_weight_run_columns(conn)`：`PRAGMA table_info(weight_train_runs)` → 缺列则 `ALTER TABLE ... ADD COLUMN batch_id INTEGER` / `variant_label VARCHAR(64) DEFAULT ''`，再 `CREATE INDEX IF NOT EXISTS ix_weight_run_batch ON weight_train_runs(batch_id)`；`init_db()` 在 `create_all` 后、紧跟 `_ensure_trade_risk_columns` 调用（幂等、老库安全，仿既有补列机制）。 |
| `services/quant/app/db/repositories.py` | re-export 新批次仓储符号，保持 `repo.X` 调用点统一。 |
| `services/quant/app/config.py` | 加 `max_weight_batch_candidates:int=8`(ge=2,le=16)；可选 `max_weight_batches_inflight`。 |
| `services/quant/app/schemas.py` | 加 `WeightBatchCandidateIn`/`WeightBatchStartReq`/`WeightBatchSelectReq` 请求模型 + `weight_batch_out`/`weight_batch_summary_out` 序列化助手（子记录复用 `weight_train_run_out`）。 |
| `services/quant/app/main.py` | 启动对账纳入 queued；`include_router(weight_batches.router)`；关停先 `weight_batch_manager.shutdown()`；sweeper 增补批次回收。 |

### 3.3 前端（新增）

| 文件 | 意图（一行） |
|---|---|
| `apps/web/lib/weightBatchTypes.ts` | 批次 TS 契约：`WeightBatch*`、`BatchStreamEvent`、`CandidateState`、`WeightBatchTarget`。 |
| `apps/web/lib/weightBatchApi.ts` | 批次 REST 客户端（镜像 `weightModelApi`）。 |
| `apps/web/lib/weightBatchWs.ts` | 批次 WS 订阅器（复用 `ReconnectingWS`，镜像 `weightTrainWs`）。 |
| `apps/web/lib/weightBatch.ts` | 纯助手：候选排名/择优/批次状态派生/标签/target·body 构造（与同名单测约定）。 |
| `apps/web/components/Factor/WeightBatchLauncher.tsx` | 批次启动器：方法多选（含 `TrainerInfo` 不可用禁用）+ 可选超参变体 + 共享规格摘要 + 「开始批量训练」。 |
| `apps/web/components/Factor/CandidateCard.tsx` | 紧凑候选卡：方法徽标/状态/进度+phase/迷你 loss·IC 曲线/收敛权重条/单候选取消。 |
| `apps/web/components/Factor/WeightBatchCompare.tsx` | 排行榜（按 icOut 排名、最优高亮奖杯、Δvs#1、行内 应用/查看/回测）+ 多候选验证 IC 叠加曲线。 |
| `apps/web/components/Factor/WeightBatchArena.tsx` | 竞技场：订阅批次 WS，候选卡网格 + `WeightBatchCompare` + 连接健康 + 终态择优动作。 |
| `apps/web/components/Factor/WeightBatchList.tsx` | 历史批次列表（作为「权重模型」区新 tab）。 |

### 3.4 前端（修改）

| 文件 | 意图（一行） |
|---|---|
| `apps/web/lib/store.ts` | 加瞬态 `weightBatchTarget` + `setWeightBatchTarget`（不持久化，随 `weightTrainTarget` 一并复位）。 |
| `apps/web/components/Factor/WeightModelPanel.tsx` | View 联合加 `batchLaunch/batchArena/batchList`；消费一次性 `weightBatchTarget` 切竞技场（模式同 `weightTrainTarget`）。 |
| `apps/web/components/Factor/WeightModelList.tsx` | 顶栏加「多模型训练」入口 + 「训练批次」tab；默认列表过滤 `source==="batch"` 父模型。 |
| `apps/web/components/Factor/FactorSetBuilder.tsx` | 「训练权重」旁加「多模型训练」按钮，写 `weightBatchTarget`（含完整 builder 状态，origin="builder"）。 |
| `apps/web/lib/weightModel.ts` | 加 `isBatchModel`/`filterUserModels` 过滤助手；`methodLabel` 等复用。 |

### 3.5 测试（新增）

| 文件 | 意图（一行） |
|---|---|
| `services/quant/tests/test_weight_batch_scheduler.py` | 并发上限、FIFO 队列、名额释放泵起、单批 K>cap 全终态（注入时钟/桩训练器，确定性）；**含「快桩作业即时终态」回归 R2 丢唤醒**：排队候选不卡 `queued`、全达终态、任一时刻 `active_count ≤ cap`。 |
| `services/quant/tests/test_weight_batch_equivalence.py` | 候选(method M) ⇔ 独立 `/weight-models/train`(method M) 权重/normStats/metrics 字节级相等。 |
| `services/quant/tests/test_weight_batch_api.py` | start/list/get/select/delete/候选取消 契约与错误码。 |
| `services/quant/tests/test_weight_batch_ws.py` | roster 快照/打标转发/晚订阅回放/终态关闭；**回归 R3**：子作业终态后转发协程退出、`_await_all_children` 不挂起、批次如期 `batchDone`。 |
| `services/quant/tests/test_weight_batch_cancel.py` | 整批取消：running→cancelled、queued→cancelled、无 DB 泄漏、批次终态。 |
| `services/quant/tests/test_weight_batch_reconcile.py` | 重启对账把残留 running/queued 候选改判 failed 并 repoint；**回归 R1**：老库（无 `batch_id`/`variant_label` 列）经 `_ensure_weight_run_columns` 幂等补列后 INSERT/SELECT 正常，既有单次训练不回归。 |
| `apps/web/lib/weightBatch.test.ts` | 排名/择优/状态派生纯函数。 |
| `apps/web/components/Factor/WeightBatchArena.test.tsx` | 候选卡渲染、排行榜高亮、择优仅 builder-origin、aria-live（`NODE_ENV=test`）。 |
| `apps/web/components/Factor/WeightBatchLauncher.test.tsx` | 方法多选、不可用禁用、候选数上限、提交体构造。 |

---

## 四、Interface design（接口设计）

### 4.1 REST（前缀 `/api`，字段 camelCase、时间秒级 UTC、错误体 `{error:{code,message}}`）

- `POST /weight-models/batches` → `201`
  - Body `WeightBatchStartReq`（继承 `_FactorRange`）：
    ```jsonc
    {
      "instId": "BTC-USDT", "bar": "1H", "startTs": 0, "endTs": 0,
      "members": [FactorMemberIn, ...],            // 1..12，共享
      "horizon": 6, "config": { "normWindow": 200, "thresholds": {...} },
      "candidates": [                               // 2..max_weight_batch_candidates
        { "method": "linear",        "config": {"l2": 0.1}, "variantLabel": "L2=0.1" },
        { "method": "random_forest", "config": {"nEstimators": 300} },
        { "method": "xgboost" }
      ],
      "factorSetId": 12, "name": "BTC 因子多模型对比", "origin": "builder", "maxBars": null
    }
    ```
  - 返回 `{ batchId, parentModelId, label, origin, candidates: [{ runId, method, variantLabel, status:"queued" }] }`。
  - 错误：`400` 成员/bar/candidate.config 非法；`413 FACTOR_TRAIN_TOO_LARGE` 区间过长；`422/400 WEIGHT_BATCH_TOO_MANY_CANDIDATES` 候选超上限；`400 WEIGHT_BATCH_EMPTY_CANDIDATES` 候选为空。
- `GET /weight-models/batches` → `[WeightBatchSummary]`（newest first）。
- `GET /weight-models/batches/{batchId}` → `WeightBatchDetail`（共享规格 + 候选 run 概要数组 + `ranking` + `selectedRunId` + 派生 `status`）；`404 NOT_FOUND`。
- `POST /weight-models/batches/{batchId}/select` → `200`，Body `{ runId }`；`409 WEIGHT_BATCH_RUN_NOT_SELECTABLE`（非 succeeded/空权重）；`404`。
- `POST /weight-models/batches/{batchId}/cancel` → `202`（有在跑/排队）/ `200`（已终态，幂等）；`404`。
- `POST /weight-models/batches/{batchId}/candidates/{runId}/cancel` → `202`/`200`/`404`。
- `DELETE /weight-models/batches/{batchId}` → `204`（级联删父模型+全部候选 run+批次行）；`409 WEIGHT_BATCH_RUNNING`（仍有 running/queued 候选，须先取消）；`404`。
- 候选详情/回测**复用既有**：`GET /weight-models/{parentModelId}/runs/{runId}`、`POST /weight-models/{parentModelId}/runs/{runId}/backtest`（parent 承载共享成员，回测口径自洽）。注意该详情体的 run 标识键为 `id`、IC 在 `metrics.*`（R7 桥接）。

**批次派生状态（`status`，R11）**：由候选集合派生，不单独落库——`running`＝存在 `queued` 或 `running` 候选；`succeeded`＝全部候选终态且 **≥1** `succeeded`；`failed`＝全部候选终态且**无** `succeeded`；`cancelled`＝经整批取消收敛（全 `cancelled`/混合终态但由取消触发）。`batchDone` 仅在派生进入终态（无 queued/running）时 emit 一次。无 `succeeded` 候选时 `ranking` 不置 `isBest`，`select`/「应用最佳」在前后端均禁用。

### 4.2 WebSocket `/ws/weight-batch/{batchId}`

联合事件（订阅即全量回放缓冲，含终态）：
```jsonc
{ "type":"batch", "data": { "batchId":1, "parentModelId":9, "status":"running",
    "candidates":[{ "runId":11,"method":"linear","variantLabel":"","status":"queued" }, ...] } }
{ "type":"candidate", "data": { "runId":11, "method":"linear",
    "event": <TrainStreamEvent: run|phase|curve|result|done|cancelled|error> } }
{ "type":"batchDone", "data": { "batchId":1, "finishedTs":0,
    "ranking":[{ "runId":13,"method":"xgboost","status":"succeeded","icOut":0.071,"icIn":0.11,"isBest":true }, ...] } }
{ "type":"ping" }
```
前端按 `data.runId` 路由 `event` 进「按候选」reducer；`batch`/`candidate(status)`/`batchDone` 驱动 roster 与排行榜。

### 4.3 前端交接契约

```ts
export interface WeightBatchTarget {          // 瞬态 store，一次性消费
  members: FactorMember[]; thresholds: FactorThresholds; horizon: number;
  instId: string; bar: string; startTs: number; endTs: number; name: string;
  methods?: TrainMethod[];                     // 预选（launcher 可增删）
  factorSetId?: number; origin: "builder" | "model";
}
```

---

## 五、Data model（数据模型）

### 5.1 新表 `weight_train_batches`（`create_all` 加法建表）

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | INT PK autoinc | |
| `label` | VARCHAR(128) | 批次名（缺省 = 成员摘要 + "多模型对比"） |
| `inst_id` / `bar` | VARCHAR(32)/(16) | 共享标的/周期 |
| `start_ts` / `end_ts` / `horizon` | INT | 共享训练窗口/前瞻 |
| `members_json` | TEXT | 共享成员快照（JSON） |
| `config_json` | TEXT | 共享配置（thresholds/normWindow 等，JSON） |
| `parent_model_id` | INT | 批次父模型 id（普通整数、不加外键，仿 `latest_run_id`） |
| `factor_set_id` | INT NULL | 弱溯源来源组合 |
| `origin` | VARCHAR(16) | "builder"\|"model"（回流门禁用，缺省 "model"） |
| `selected_run_id` | INT NULL | 用户显式择优的候选 run（缺省 null，读取时回落 best） |
| `created_at` / `updated_at` | DateTime | 秒级 UTC，与全系统统一 |

### 5.2 `weight_train_runs` 增列

| 列 | 类型 | 说明 |
|---|---|---|
| `batch_id` | INT NULL, 索引 | 归属批次；独立训练为 NULL（既有行为零影响） |
| `variant_label` | VARCHAR(64) 默认 "" | 同方法多超参变体的显示标签 |

> **迁移（R1/P0，务必落实）**：`weight_train_runs` 是**既有**表，`Base.metadata.create_all` **不会**为其补列。上述二列必须由 `session.py::_ensure_weight_run_columns` 在启动时 `ALTER TABLE ADD COLUMN` 幂等补齐（仿既有 `_ensure_trade_risk_columns` 对 `trades` 表补 `explain_json` 等列的机制），否则老库上 ORM 声明有列而物理表无列，`add_weight_run` 等 INSERT 立即 `OperationalError: no such column`，**连既有单次训练一并崩**。**回滚安全**：新表 `weight_train_batches` 与多出的两列对旧代码无害（被忽略），无破坏性迁移、无数据回填。

### 5.3 状态机扩展

`WeightRunStatus`：`queued → running → succeeded|failed|cancelled`。`queued` = 已落库、等待名额、尚未启动作业。约束：
- `active_run_ids()`（manager）**不含** queued（无在跑 job）；删模型/删记录守门把 queued 视同 running（不可删，须先取消）。
- `reconcile_dangling_weight_runs`/`fail_orphan_running_runs`：`queued` 同 `running` → `failed`。
- `_prune_runs`：跳过条件加入 `status == "queued"`（绝不剪未跑候选）。
- 前端 `STATUS_LABELS.queued="排队中"`、`runStatusTone("queued")="neutral"`；回测/择优门禁只认 `succeeded`。

### 5.4 内存态

- `CandidateSpec`：`run_id, method, config, variant_label`。
- `WeightBatchRuntime`：`batch_id, parent_model_id, spec, candidates, _buffer, _subscribers, _child_tasks, _cancelled, _sched_task, finished_mono, _clock`（镜像 `_TrainJob` 分层 + 调度泵）。
- `WeightBatchManager`：`_runtimes: dict[int, WeightBatchRuntime]`，`_prune_finished`/`shutdown`（镜像 `WeightTrainManager`）。

---

## 六、Testing & acceptance criteria（测试与验收）

**后端**
1. **等价性硬门禁**：`test_weight_batch_equivalence` 断言候选(method M) 与独立 `/weight-models/train`(method M) 同输入下 `weights/normStats/metrics` 字节级相等（守「无第二引擎」）。
2. **并发上限**：`max_weight_train_jobs=2`、单批 6 候选，任一时刻 `manager.active_count() ≤ 2`；6 候选全部达 `succeeded`；启动顺序 FIFO；无候选丢失/卡 queued（注入时钟 + 桩快训练）。
3. **名额共享**：一个独立训练 + 一个批次并存时，全局 running 恒 ≤ cap。
4. **整批取消**：running→cancelled、queued→cancelled（**无** DB 残留 running/queued），批次派生 status=cancelled，WS 收 batchDone 后关闭。
5. **对账**：进程重启（模拟）后，残留 running/queued 候选全改判 failed(`WEIGHT_TRAIN_INTERRUPTED`) 并 `repoint`。
6. **择优**：默认 best=max icOut；`select` 非 succeeded → 409；select 成功写 `selected_run_id`。
7. **删批守门**：有在跑/排队候选 → 409；取消后 → 204 级联删父模型+候选+批次行。
8. **WS**：晚订阅回放 roster+缓冲（含终态）；断线中训练续跑、重连续显。

**前端**
9. 竞技场按 roster 渲染 queued/running/done 候选卡；排行榜按 icOut 排名、#1 高亮奖杯并显 Δ；叠加曲线多序列可辨（legend=方法）。
10. 「应用最佳到组合并返回」**仅** `origin==="builder"` 渲染并生效（守 G-F1，回流复用 `buildTrainResult`）。
11. 无障碍：状态迁移经 `aria-live`；排行榜行键盘可达（Enter/Space，**复刻** `WeightModelDetail.tsx` 内私有 `RunHistoryTable` 的键盘可达模式——该组件未导出，故复刻而非 import，R10）；视图切换焦点迁移（镜像 `WeightModelPanel`）；`prefers-reduced-motion` 抑制脉冲动画。
12. 连接健康：断连/陈旧流 banner（复用 `WeightTrainLive` 看门狗与 `connected/stale` 语义）。
13. 组件测试须置 `NODE_ENV=test`（见 [[aragon-web-vitest-node-env]]）。

**验收 Definition of Done**
- 用户可从搭建台/列表一次发起 ≥2 候选的批次，一屏并行观测其实时进度与曲线，全局并发不超 cap。
- 全部完成后排行榜按样本外 IC 排名、最优显著标记；用户可查看任一候选完整结果（回放+权重+回测），并一键择优（builder-origin 回流 / 保留最佳并清理其余）。
- 既有单次训练路径行为不变；等价性、并发、取消、对账测试全绿；`services/quant` 与 `apps/web` 既有测试不回归。

---

## 七、Risks & mitigations（风险与缓解）

1. **`queued` 新状态波及面**：涉及对账/孤儿/剪枝/删除守门/前端标签与门禁。缓解：§5.3 逐点枚举全部触点；作为加法枚举值引入，`succeeded` 门禁不放宽；为其补对账与守门测试。
2. **全局名额争用/饥饿**（多批次/独立训练）：缓解：统一 `_slot_cond` 广播 + 「唤醒即抢」尽力而为 FIFO；`max_weight_batch_candidates` 封顶单批资源；不做严格优先级（记为已知取舍）。若需强公平，后续可加名额票据队列（增量、不破契约）。
3. **TOCTOU 突破 cap**：缓解：`reserve()` 同步、其与 `start_job()` 间只夹一次 DB await 且预占跨越该窗口（沿用 H-B3）；泵仅在 `reserve_when_free` 内 `wait()` 让出，绝不在 reserve↔start 间让出；测试断言任一时刻 `active_count ≤ cap`。另见 R2：预占与等待须同锁闭环，杜绝丢唤醒。
4. **父模型污染主列表**：缓解：`source="batch"` 默认从 `WeightModelList` 过滤，仅「训练批次」tab 可见；删批次级联清理。
5. **fan-out 缓冲增长**（长批次/多候选）：缓解：缓冲随 runtime 生命周期，终态 TTL 回收（复用 `_prune_finished`）；候选数封顶；WS 满队列丢终态有补发兜底。
6. **SQLite 单写并发落库**：多候选并发 finalize 各写自身 run 行 + 少量批次行更新。缓解：复用既有 `_persist` 尽力而为 + 有界重试（H-B1；R-02＝hardening 轮·`_persist` 落库有界重试一次，R9 消歧），批次不新增高频写路径。
7. **等价性回归**：缓解：候选走与 `start_training` 同构的 `JobSpec`，**绝不动** `training.py`；等价性测试守护。
8. **跨上下文回流劫持**：缓解：仅 builder-origin 批次渲染「应用最佳到组合」，复用 G-F1 守门；`setWeightTrainResult` 全仓唯一写入点仍在应用动作内。
9. **候选间成员漂移**：批次共享唯一成员快照，天然无漂移；父模型回测 `assert_weights_cover` 兜底（成员与权重不符 → 400 `FACTOR_WEIGHTS_STALE`）。

---

## 八、Non-goals（非目标）

- 不做候选级超参**自动搜索/网格寻优**（本设计支持用户手填变体，自动搜索留待后续）。
- 不改 `engine/factors/training.py` 与既有单次训练/回测链路的任何字节级契约。
- 不引入分布式/多进程训练（仍进程内 `run_in_threadpool`，受同一 CPU 卸载与并发上限治理）。
- 不做批次结果的持久化回测存档（回测仍一次性、不落库，复用 backtest-replay 不变量）。

---

## 评审结论（Review Verdict）

**有条件通过（Approved with conditions）。**

本方案顶层架构判断正确且与代码库高度自洽：以「薄编排、零第二训练器」为第一性原则，候选=既有 `_TrainJob`、并发经单一真源 `reserve()` 硬上限治理，fan-out/WS/回测/回流均镜像既有已加固实现。绝大多数对既有符号的引用经**逐一核验属实**——`reserve()`（同步预占、`max_weight_train_jobs=4`）、`_TrainJob.subscribe/snapshot/_push`、`start_job(JobSpec)`（10 字段名全对）、`repoint_weight_model`、`ws_weight_train`+`_WS_HEARTBEAT_SECONDS=20.0`、`buildTrainResult(event,target,{modelId,runId})`/`setWeightTrainResult` 单写点/`weightTrainTarget` 瞬态不持久化、`ReconnectingWS`、`TrainerInfo`（`GET /factors/trainers` 禁用不可用方法）、以及 `G-F1`/`H-B1`/`H-B3` 不变量均确认存在。

评审发现 **1×P0 + 4×P1 + 8×P2**，其中 **P0/P1 已在本 v2 正文就地修复**（见《评审记录》处置列）：

- **P0 R1**：既有表加列不能靠 `create_all` → 已改为 `session.py` 启动 `ALTER TABLE` 补丁（`_ensure_weight_run_columns`）。
- **P1 R2**：调度泵丢唤醒 → 已改为同锁「预占或等待」原子闭环（`reserve_when_free`）+ 取消显式唤醒。
- **P1 R3**：转发协程终态不退致批次悬挂 → 已明确「终态即退」。
- **P1 R4**：`source="batch"` 白名单缺失 → 已并入 `WEIGHT_MODEL_SOURCES` 并要求审计 source 分支。
- **P1 R5**：无 DB 级联 → 已改为 service 层单事务级联删（镜像 `delete_weight_model`）。

**放行条件**（下游代码开发节点须落实，均已写入正文）：

1. **落实 P0 补列补丁并加回归测试**：`_ensure_weight_run_columns` 必须在 `init_db` 内 `create_all` 之后调用；`test_weight_batch_reconcile` 覆盖老库幂等补列且既有单次训练不回归。
2. **调度并发测试须触发丢唤醒场景**：`test_weight_batch_scheduler` 用「快桩作业即时终态 + K>cap」断言排队候选不卡 `queued`、全达终态、任一时刻 `active_count ≤ cap`。
3. **等价性硬门禁不可降级**：`test_weight_batch_equivalence` 守候选(method M) 与独立 `/weight-models/train`(method M) 的 `weights/normStats/metrics` 字节级相等；**绝不改** `engine/factors/training.py`。
4. **P2 项（R6–R13）** 已在正文澄清/补全，实现时对照《评审记录》处置列即可，无需再返工设计。

满足以上四项条件后即可进入代码开发节点。

---

## 实施过程发现的方案缺陷（Issues Found During Implementation）

> 代码开发节点记录：以下为落地时相对方案的**已授权偏离 / 澄清**，均未偏离设计意图，且不影响验收门禁。

1. **后端模块按 R13 拆两文件（已落实，非偏离）**：`weight_batch_manager.py`（runtime + manager + 调度泵）落地即触 CLAUDE.md 800 行阈值预警，遂按 §3.1/R13 明文授权拆为 `services/quant/app/factors/weight_batch_runtime.py`（`WeightBatchRuntime` + `CandidateSpec` + `BatchSharedSpec` + 调度泵 + fan-out + 取消对账）与 `weight_batch_manager.py`（`WeightBatchManager` 注册表 + TTL/数量回收 + 关停）。变更计划表列的单文件即此二者，`repositories.py` re-export 与 `main.py` 关停 / sweeper 均对齐。

2. **`schemas.py` 批次序列化助手沿用「函数内局部 `import json`」既有约定**：本仓 `schemas.py` 的 `json` 均为**函数内**局部导入（无模块级 `import json`），故新增 `_run_metric`/`weight_batch_ranking` 等助手在函数内 `import json`，与既有 `_load_json`/`factor_set_out` 同风格（非模块级导入是本文件既有惯例，非缺陷）。

3. **候选落库助手双实现（`add_weight_run(status=...)` + `add_queued_weight_run`/`mark_run_running`）**：§3.2 给的是「或」选项，实现两者皆备——`add_weight_run` 增可选 `status`（默认 `running`，仅接受 `running|queued`）保后向兼容；批次候选走 `add_queued_weight_run` + `mark_run_running`（语义清晰、幂等守门）。既有单次训练调用点零改动。

4. **启动器超参变体 UI 收敛为「方法级多选」（前端范围澄清，后端 / 数据模型不减）**：`WeightBatchLauncher` 落地为**每勾选一个方法 = 一个候选**（字节级等价于独立训练该方法），覆盖需求核心「同时训练多个模型、择优」。设计所述「可选超参变体」的**手填变体编辑器**本轮未做 UI——原因：无逐方法超参输入时，同方法多候选会退化为字节级重复候选（无对比意义）；而后端契约（`WeightBatchCandidateIn.config`/`variantLabel`）、数据模型（`WeightTrainRun.variant_label`）、前端 `batchStartBody`/`candidateLabel`/roster 均**已完整支持变体**，后续加超参输入即可零后端改动启用。此与 §八「非目标：不做自动网格搜索」一致，仅将手填变体 UI 后延。
