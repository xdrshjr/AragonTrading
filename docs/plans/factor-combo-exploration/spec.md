# 因子组合探索训练（Factor Combination Exploration Training）· spec v2

> 特性目录：`docs/plans/factor-combo-exploration/`
> 归属节点：AgentMesh execution `tge-ae4468d0` · Subtask #0（方案设计）→ Subtask #1（方案评审与修复，v2）
> 面向读者：下游实现工程师。本文档细化到"逐行可落地"，不再需要二次决策。
> 关联既有特性：[weight-model-workspace](../weight-model-workspace/)、[weight-multi-model-training](../weight-multi-model-training/)、[factor-lab-symbol-groups](../factor-lab-symbol-groups/)、[weight-model-backtest-replay](../weight-model-backtest-replay/)。
> **v2 变更**：经资深评审对照真实代码逐节核验后，已就地修复全部 P0/P1（回流不绑探索父模型、复用单写入器、守门顺序自洽、候选上限对齐资源包络、select 语义澄清），并更正前端复用地图中若干与真实符号不符之处。详见「评审记录」与文末「评审结论」。

---

## 评审记录（Review Notes）

> 评审人：Anthropic 工程团队资深评审（Subtask #1）。评审维度：可行性 / 完整性 / 一致性 / 右尺寸。
> 方法：派发代码核对代理，对照 `services/quant/app/**`、`apps/web/**` 真实代码逐条验证 spec 的复用声明与新增点，再据既有特性记忆（arena 评审 P0/P1、G-F1 单写点回流不变量、多标的取数前扇出护栏、等价性门禁）定级。
> 处置：P0/P1 已在下文正文就地修复并标注「（已修 §x.y）」；P2 记录在案，其中低成本纠偏项亦已就地更正，供开发节点直接落地。

### P0（阻断级，必须修复 —— 已修复）

- **P0-1｜回流绑定探索父模型导致隐性数据损坏（已修 §2.7）**：v1 的「应用最佳组合」执行 `setBoundModelId(resolvedParentId)`，把 builder 绑定到探索**父模型**（`members`=整因子池、`source="batch"` 被主列表过滤）。但胜出候选的 `members` 是池的**子集**。二者不一致带来一条极自然的破坏路径：用户「应用最佳组合 → 微调 → 更新模型/保存」时，`update_weight_model` 检出 members 变更 → 依 G-B4 清空父模型 `latest_run_id`（丢失指向胜出 run 的指标指针），并把父模型成员静默改写为子集；因父模型 `source="batch"` 仍被主列表过滤，用户以为已保存的模型实际不可见。→ 主交互链功能性损坏。**修复**：探索回流不绑定/不改写探索父模型；以 `boundModelId=null`（全新未保存草稿）落地，随后保存生成一个全新 `source="builder"` 模型/因子组合，携胜出子集成员。详见 §2.7。

### P1（条件级，必须修复 —— 已修复）

- **P1-1｜违反 `setWeightTrainResult` 单写点不变量 G-F1（已修 §2.7）**：既有硬约束「`setWeightTrainResult` 全仓唯一写入点在 `applyToBuilder` 内、仅 `target.origin==="builder"` 回流」。v1 拟从 `WeightExploreModal` **直接**调 `setWeightTrainResult` → 新增第二写入点，破坏该不变量。**修复**：探索回流复用同一 `applyToBuilder`（或抽取二者共用的 `flowResultToBuilder` 单写入器），`target.origin="builder"`，唯一差异是成员用胜出子集。
- **P1-2｜守门顺序自相矛盾（已修 §2.3 / §6.1）**：v1 §2.3 把「区间 413」列为第 ④ 步、「候选数上限 400」列为第 ⑧ 步（即 413 先于 400），而 §6.1 测试断言「候选数 400 先于区间 413」，且既有 `weight_batch_service.start_batch` 实测顺序也是候选数先于区间。三者冲突。**修复**：§2.3 重排为「全部 400 校验（池/bar/config/方法/计划/候选数）一律先于 413 区间守门」，区间守门移至展开与候选数校验**之后**的末步，与 §6.1 及 arena 先例一致。
- **P1-3｜候选上限与既有资源包络不一致（已修 §2.3 / §4.1 / §7）**：v1 `max_weight_explore_candidates` default 24 / `le=64`。同族既有 `max_weight_batch_candidates` default 8 / `ge=2 le=16`、并发上限 `max_weight_train_jobs=4`。64 候选 = 64 次完整 `_TrainJob`（各自取数+训练），以 4 并发串行跑 → 墙钟随候选数线性膨胀，且 WS roster / 前端 reducer 态 / watchdog 均按 ≤16 候选设计，64 会显著超出验证过的包络。经核 explore 走**自有端点**（不经 `WeightBatchStartReq.candidates` 的 `max_length=16` 约束），故非硬冲突，但上限失据。**修复**：改 default **12** / `ge=2 le=24`（约为 arena 的 1.5×），并显式注明：并发受 `max_weight_train_jobs` 限、墙钟随候选数线性增长、超限一律显式 400 阻断（绝不静默截断）。
- **P1-4｜复用 `select` 对异构成员候选的语义未澄清（已修 §2.3 / §4.1）**：v1 声称 `POST /batches/{id}/select` 直接复用，但未说明当候选成员≠父模型池时的行为。**修复**：明确 explore 的「择优」**主路径是回流（全新草稿，见 P0-1）**；`select` 仅用于在批次层记录 `selected_run_id`（指针语义），**绝不据候选子集成员改写父模型 `members_json`**；开发节点须核 `select_weight_batch_run` 现实现确为指针 repoint 而非成员改写，若其会改写父模型成员则 explore 不得复用其「择优即改写父模型」语义。

### P2（建议级，记录在案 —— 低成本项已就地更正）

- **P2-1｜`_WEIGHT_METHODS` 是 `Literal` 而非集合（已更正 §2.3）**：`schemas.py::_WEIGHT_METHODS = Literal[...]`，不能直接 `req.methods ⊆ _WEIGHT_METHODS`。改为：`WeightExploreStartReq.methods: list[_WEIGHT_METHODS]`（Pydantic 按 Literal 校验合法性 + `min_length=1, max_length=5`）+ 服务层去重；如需运行时集合用 `set(get_args(_WEIGHT_METHODS))`。
- **P2-2｜`leave_one_out` 退化池边界（已补 §2.2）**：`|pool|=1` 时「去一」得空组合 → v1 会 `ValueError` 令整请求 400。planner 应**跳过空组合**（或要求 `|pool|≥2` 方启用 LOO），避免退化输入整体失败。
- **P2-3｜组合枚举双实现须锁步**：前端 `estimateComboCount` 与后端 `plan_combinations` 是两份实现，须同口径同去重。已由 §6.2 平价测试守护；开发节点改任一侧务必同步另一侧与测试。
- **P2-4｜策略命名易误读（已更正 §2.7 标签）**：`topk_prefix` 标签「IC 前缀贪心」易被读成贪心搜索；其实为「按 IC 排序取嵌套前缀」，标签改为「IC 排序前缀」。
- **P2-5｜前端弹窗尺寸阈值风险（已注 §2.7 / §3）**：`WeightExploreModal.tsx`（配置/运行/完成三态 + WS 消费 + 回流）易破 CLAUDE.md 的 800 行/50 行阈值。要求拆子面板（`ExploreConfigPanel` / `ExploreRunningPanel` / `ExploreResultPanel`）+ 服务端 `start_exploration` 抽辅助函数控行数。
- **P2-6｜前端复用地图纠偏（已更正 §2.7 / §3，供开发节点）**：代码核对发现 v1 若干符号定位不准，直接更正以免开发返工：① `ALL_METHODS` 仅是 `WeightBatchLauncher.tsx` 的私有 `const`，非共享导出（须上移到共享模块再复用）；② 前端 `WeightTrainRun` 定义在 `lib/weightModelTypes.ts` 且**尚无** `variantLabel`（`variantLabel`/`members` 须加在此文件，而非 `weightBatchTypes.ts`）；③ `weightBatchApi.start` 位于 `lib/weightBatchApi.ts`（探索对应 API 放 `lib/weightExploreApi.ts`、纯 body-builder 放 `lib/weightExplore.ts`，与 batch 的拆分口径一致）；④ `weightBatchWS` 第三参名为 `onState`（非 `onOpen`）；⑤ `MemberPalette` 标题现为裸 `<span>`，须先包一层 `flex items-center justify-between` 才能放按钮；⑥ `FactorSetBuilder` **并非**以本地 `useState` 布尔挂载 arena（arena 是 `WeightModelPanel` 的 `view.kind` 状态机 + store 交接 + `setSection` 导航）—— 探索弹窗作为 builder 内**新** modal 是合理的，但勿在文档中声称「镜像 arena 的 modal 模式」。
- **P2-7｜`WeightTrainRun.members_json` 是新范式，须审计全部读取点（已注 §2.5 / §5.1）**：现有架构中成员快照仅存于 `WeightModel` / `WeightTrainBatch`，逐 run 成员是**新概念**。除已覆盖的 `backtest_run`（改优先 `run.members_json`）与 `weight_train_run_out`（新增 `members`）外，开发节点须审计所有「run→成员」读取路径（含经 `select` 指针后的 `factor_score` / 因子组合同步链），确保异构候选不误用父模型池成员。

### 已核验正确（无需改动，供开发节点建立信心）

- 运行时复用成立：`JobSpec.members`、`BatchSharedSpec.members`、`variant_label(String(64))` 均已存在；唯一实质改动 = 给 `CandidateSpec` 加 `members` + `weight_batch_runtime.py:164` 改 `cand.members or spec.members`。
- 迁移已正确规避 P0 陷阱：`_ensure_weight_run_columns`（ALTER 补列）确存在；`_ensure_weight_batch_columns` 确**不存在**，spec 正确要求**新增**该 ALTER 补列器并在 `init_db` 内 `create_all` 之后调用 —— 尊重了 arena 评审教训（`create_all` 不给既有表补列）。
- `source="batch"` 已在 `WEIGHT_MODEL_SOURCES=("builder","manual","batch")`，无需白名单改动（spec 判断正确）。
- `backtest_run` 现读 `model.members_json`（第 190 行），spec 改为优先 `run.members_json` 的方向正确且必要。
- `cascade_delete_weight_batch` 为服务层单事务级联删；`reconcile_dangling_weight_runs` 启动扫 `queued+running`；`GET 列表/详情`、`select`、`cancel`、候选 `cancel`、`DELETE`、`ws/weight-batch/{id}` 路由齐备且与训练方法无关，可原样承接探索批次。
- 前端回流基座齐备：`weightTrainResult`/`setWeightTrainResult`、`consumedResultRef`、`setBoundModelId`、水合 effect（`FactorSetBuilder`），`emptyCandidate/applyCandidateEvent/rankCandidates/bestRunId/allSettled`（`lib/weightBatch.ts`），`WeightBatchCompare`/`WeightBatchList`/`TrainingCurveChart`/`WeightBars`、`factorApi.library/list/trainers`、`memberIdentity/candidateIdentity`、`weightBatchApi.cancel`、`methodLabel`、`buildTrainResult`（`lib/weightModel.ts`）。

## 一、概述（Overview）

因子实验室的"权重训练与组合"（`FactorSetBuilder` / `builder` section）允许用户在**组合成员**面板（`MemberPalette`）里手工挑选一组因子，然后对这组固定成员做一次权重训练（`训练权重`），或在同一份成员上并行比较多种训练方法（`多模型训练`，即 weight-multi-model-training 的"多模型训练竞技场"）。但今天存在一个能力缺口：**用户无法自动发现"哪些因子子集组合"表现更好**——现有流程要求用户先主观确定成员集合，训练只回答"这组成员用哪种方法最好"，从不回答"这些候选因子里，哪种组合才值得训练"。

本特性新增**探索训练**：在**组合成员**面板标题最右侧放一个`探索训练`按钮，点击弹出一个自包含的探索弹窗。用户在弹窗内（1）编辑要探索的**因子池**（默认取当前已选组合成员，可增删）、（2）选择**组合生成策略**（按子集大小穷举 / IC 前缀贪心 / 留一法 / 手动指定）、（3）勾选要参与的**训练方法组合**。系统据此在服务端展开为"因子组合 × 训练方法"的笛卡尔候选集，复用既有批次运行时（`WeightBatchRuntime`）并行训练，弹窗内以**组合×方法矩阵热力图 + 实时排行榜**可视化训练过程与结果。全部候选终态后，用户在矩阵/排行榜中一键"应用最佳组合"，把胜出组合的成员 + 方法 + 权重回流到组合成员面板，进入既有的保存/建策略闭环。

**核心复用决策**：探索训练在存储与运行时层面**就是一个批次**（`weight_train_batches`），唯一的一般化是"批次候选可携带各自的成员快照"（既有批次所有候选共享一份成员，本特性让候选成员异构）。因此我们**不新建批次运行时/管理器/WS/择优/级联删/竞技场读接口**，只：（a）给候选加"按候选成员"能力，（b）新增一个探索启动端点 + 一个纯组合生成器，（c）新增一个探索弹窗与矩阵可视化。读/择优/取消/删除/WS 全部复用 `weight_batches.py` 既有端点。这既满足"薄编排叠薄编排"的既有架构哲学，又把新增面控制到最小、把等价性/并发/对账等硬约束原样继承。

---

## 二、技术设计（Technical Design）

### 2.1 架构总览

```
组合成员面板(MemberPalette 标题右) ──点击──▶ WeightExploreModal（弹窗）
   │  配置：因子池 + 组合策略 + 训练方法 + 候选数预览
   ▼  POST /api/weight-models/explorations
weight_explore_service.start_exploration()
   ① 守门（池成员/bar/config/区间/方法/策略参数）           ← 复用 weight_model_service 守门 + 新增
   ② weight_explore_planner.plan_combinations(pool,strategy) ← 纯函数：生成组合列表
   ③ 展开 combos × methods → 候选集；候选数 ≤ cap 守门（新 cap）
   ④ 建 explore 批次(kind="explore")+父模型(source="batch")+逐候选 queued run(带各自 members)
   ⑤ 注册 WeightBatchRuntime（复用！CandidateSpec 携 members）
   ▼
WeightBatchRuntime（既有）：FIFO 调度泵 → 每候选 _TrainJob（members=候选自有）→ fan-out
   ▼  WS /api/ws/weight-batch/{batchId}（既有，事件流不变：batch/candidate/batchDone）
WeightExploreModal 消费 WS（复用 weightBatchWS + weightBatch.ts reducers）
   → 组合×方法矩阵热力图 + 排行榜 + 逐候选曲线抽屉
   ▼  "应用最佳组合"
setWeightTrainResult(胜出候选的 members+method+weights) → FactorSetBuilder 既有回流消费 → 关闭弹窗
```

**关键不变量**：一个探索候选（组合 C_i、方法 M_j）经 `JobSpec(members=C_i, method=M_j)` 训练，与独立 `POST /weight-models/train`（`members=C_i, method=M_j`）**字节级等价**——因为批次运行时把 `members` 原样透传给 `_TrainJob`，本特性只改"透传哪一份成员"，绝不触碰 `engine/factors/training.py`（等价性硬门禁，见 [weight-model-workspace 记忆](../weight-model-workspace/)）。

### 2.2 后端组合生成器（`weight_explore_planner.py`，纯函数、可单测）

签名与语义（`services/quant/app/factors/weight_explore_planner.py`，新建）：

```python
@dataclass(frozen=True)
class ComboPlan:
    combos: list[list[dict]]        # 每个组合 = 成员快照列表（pool 的子集，保 pool 内相对顺序）
    labels: list[str]              # 与 combos 等长，展示名（如 "RSI·MACD·ATR"）

def plan_combinations(pool: list[dict], *, strategy: str, params: dict) -> ComboPlan:
    """据策略从因子池生成去重后的组合列表（确定性排序，纯函数、零 IO）。

    strategy:
      - "sizes":        对 params["sizes"] 中每个 k，穷举 pool 的全部 C(|pool|,k) 子集（组合数按需截断由 service 守门）。
      - "topk_prefix":  按 params["rankIc"]（与 pool 等长，缺省回落 pool 序）降序排 pool，取 params["sizes"] 中各 k 的前缀嵌套子集。
      - "leave_one_out":全池 1 个 + 逐一去掉单因子的 |pool| 个（共 |pool|+1 个），找"拖后腿"的因子。
                        退化守门：`|pool|==1` 时"去一"得空组合 → **跳过**该空组合（仅保留全池 1 个），不因退化输入令整请求失败。
      - "manual":       params["combos"] 为显式的成员下标列表数组，逐个取 pool 子集（下标越界 → ValueError；显式空组合 → ValueError）。
    去重键 = 组合内成员 memberIdentity 排序集合（跨策略同一组合只留一次，保先出现者顺序）。
    自动策略（sizes/topk_prefix/leave_one_out）生成的空组合一律**跳过**（不报错）；仅 manual 显式空组合报 ValueError。
    保留下来的每个组合须 1..12 成员（越界 → ValueError，交 service 映射 400）。
    """
```

- `memberIdentity(m)` 复用现有口径：`factorKey|规范化params|direction`（与前端 `lib/factor.ts::memberIdentity`、后端 `specs.validate_members` 对齐）。
- 排序确定性：`sizes`/`manual` 按输入顺序 + 组合内成员 pool 序；`topk_prefix` 按 rankIc 降序（tie 用 pool 序）。测试据此断言精确组合集与顺序。
- 该模块**不做候选数上限**判定（那是 service 层守门，见 §2.3 ⑧），只负责生成与去重、成员数边界。

### 2.3 后端探索编排（`weight_explore_service.py`，新建）

`services/quant/app/factors/weight_explore_service.py::start_exploration(session, rest, req)` 镜像 `weight_batch_service.start_batch` 的"守门优先、全过后才落库/注册运行时"范式：

```
# 【v2 P1-2】守门顺序：全部 400（廉价校验）一律先于 413 区间守门；区间守门移至展开与候选数校验之后的末步（⑦），
#            与 §6.1 测试断言「候选数 400 先于区间 413」及既有 weight_batch_service.start_batch 先例一致。
① pool 成员：validate_members([m.model_dump() for m in req.pool]) → 400（复用 specs.validate_members）
② bar：_validate_bar(req.bar) → 400（复用 weight_model_service）
③ 共享 config：_validate_train_config({**req.config}) → 400（复用）
④ 方法非空 + 去重：methods = list(dict.fromkeys(req.methods))；len(methods) < 1 → 400 WEIGHT_EXPLORE_NO_METHOD。
   （方法合法性由 Pydantic 依 `methods: list[_WEIGHT_METHODS]` 在解析期校验 —— _WEIGHT_METHODS 是 typing.Literal，
    非运行时集合，服务层不做 `⊆` 判定；如确需运行时集合用 `set(get_args(_WEIGHT_METHODS))`。见 §4.1 / P2-1。）
⑤ 生成组合：try: plan = plan_combinations(pool, strategy=req.strategy, params=req.params)
            except ValueError as e: raise HTTPException(400, {code:"WEIGHT_EXPLORE_INVALID_PLAN", message:str(e)})
⑥ 展开候选 + 候选数上限（400，均在取数/注册前）：
            candidates_plan = [(combo, method) for combo in plan.combos for method in methods]
            len(candidates_plan) 须 ∈ [2, settings.max_weight_explore_candidates]
            > cap → 400 WEIGHT_EXPLORE_TOO_MANY_CANDIDATES（附实际数与 cap，提示收窄，绝不静默截断）
            < 2   → 400 WEIGHT_EXPLORE_TOO_FEW_CANDIDATES（不足以对比）
⑦ 区间（末步 413，此时候选集已确定）：_guard_range(FactorWindow(...)) → 413（复用 weight_model_service）
⑧ 落库 + 注册：
   parent = repo.create_weight_model(WeightModelSpec(name=_explore_label(...), members=pool(池),
              method=methods[0], config=shared_config, source="batch", ...))         # 复用 batch 父模型来源，主列表自动过滤
              # 注：父模型仅作批次容器（members=整池），随批次级联删；胜出候选的子集成员经回流落到全新 builder 草稿，
              #     绝不改写此父模型（见 §2.7 / P0-1）。
   batch  = repo.create_weight_batch(WeightBatchSpec(..., members=pool, config=shared_config,
              parent_model_id=parent.id, origin=req.origin, kind="explore",
              explore_plan=_plan_json(plan, req)))                                    # kind + explore_plan_json（新增）
   for (combo, method) in candidates_plan:
       cid = "C{index_of_combo}"                                                     # 组合稳定 id（变体标签）
       run = repo.add_queued_weight_run(WeightRunStart(model_id=parent.id, method=method,
                 ..., batch_id=batch.id, variant_label=cid, members=combo))          # members（新增）→ run.members_json
       CandidateSpec(run_id=run.id, method=method, config={}, variant_label=cid, members=combo)  # members（新增）
   runtime = WeightBatchRuntime(batch_id=batch.id, parent_model_id=parent.id, rest=rest,
               manager=get_weight_train_manager(), spec=BatchSharedSpec(members=pool, ...),
               candidates=[...])
   get_weight_batch_manager().register(runtime)
   return {"batchId":batch.id, "parentModelId":parent.id, "label":batch.label, "kind":"explore",
           "origin":batch.origin, "explorePlan":_plan_json(plan, req),
           "candidates":[{runId, method, variantLabel, comboLabel, status:"queued"}...]}
```

`_plan_json(plan, req)` 形如：
```json
{ "strategy":"topk_prefix", "params":{"sizes":[2,3]}, "methods":["linear","xgboost"],
  "poolKeys":["rsi","macd","atr"],
  "combos":[{"id":"C0","label":"RSI·MACD","memberKeys":["rsi","macd"]},
            {"id":"C1","label":"RSI·MACD·ATR","memberKeys":["rsi","macd","atr"]}] }
```

**读/取消/删除/WS 全部复用批次端点**（探索批次就是批次）：`GET /weight-models/batches/{id}`、`/cancel`、`/candidates/{runId}/cancel`、`DELETE`、`ws/weight-batch/{id}`——无需在本节点新增。级联删同样走 `cascade_delete_weight_batch`（删父模型全部候选 run + 父模型 + 批次行，单事务）。

**`select` 端点语义澄清（v2 P1-4）**：探索批次可复用 `POST /weight-models/batches/{id}/select`，但其语义仅限于**在批次层记录 `selected_run_id`（指针 repoint）**，**绝不据候选子集成员改写父模型 `members_json`**（父模型 `members`=整池，胜出候选 `members`=子集，二者不同）。探索的「择优落地」**主路径是前端回流为全新 builder 草稿**（§2.7 / P0-1），而非把父模型改写成胜出子集。开发节点须先核 `select_weight_batch_run` 现实现确为指针语义；若其会改写父模型成员，则 explore **不复用**该「择优即改写父模型」行为，仅用回流路径。

### 2.4 批次运行时的最小一般化（按候选成员）

`weight_batch_runtime.py`：
- `CandidateSpec` 增 `members: list | None = None`（默认 `None` = 沿用共享 `BatchSharedSpec.members`，既有多方法批次零影响）。
- `_start_candidate` 里 `start_job(JobSpec(... members=cand.members or spec.members ...))`——**这是唯一实质改动**。`_TrainJob` 及以下不动。
- `_emit_initial_roster` / `_forward` 已带 `variantLabel`（= 组合 id），前端据此定位矩阵单元格；如需组合展示名，前端从 `explorePlan.combos` 映射。

### 2.5 按候选成员的持久化 + 读路径贯通

- `db/models.py::WeightTrainRun` 增 `members_json: Mapped[str|None]`（nullable，默认 `None`）。`None` = 该 run 沿用父模型成员（既有单次训练/多方法批次全部为 `None`，向后兼容）；探索候选写各自组合。
- `db/weight_model_repositories.py::WeightRunStart` 增 `members: list | None = None`；`add_weight_run` 里 `members_json=_dump(start.members) if start.members is not None else None`。
- `db/weight_batch_repositories.py::WeightBatchSpec` 增 `kind: str = "multi_method"` 与 `explore_plan: dict | None = None`；`create_weight_batch` 写 `kind=spec.kind, explore_plan_json=_dump(spec.explore_plan or {})`。
- `factors/weight_model_service.py::backtest_run`：成员读取改为**优先候选自有成员**——
  ```python
  members = _load_json(run.members_json, None) or _load_json(model.members_json, [])
  ```
  这样探索候选（成员≠父模型池）的一键回测用其自身组合，`assert_weights_cover(members, {"weights":weights})` 对齐；既有 run（`members_json is None`）行为字节不变。
  > **P2-7 审计提醒**：`WeightTrainRun.members_json`（逐 run 成员）是**新范式**——现有架构中成员快照仅存于 `WeightModel`/`WeightTrainBatch`，"run 成员=父模型成员"是隐含假设。除本处 `backtest_run` 与 `weight_train_run_out`（§2.5 新增 `members`）外，开发节点须**逐一审计所有"从 run 推成员"的读取点**（含经 `select` 指针后的 `factor_score` / 因子组合同步链），凡假设 run 成员=父模型池者，异构候选下都可能误用整池；一律改为"优先 `run.members_json`，为空回落父模型"。
- `schemas.py::weight_train_run_out` 增两键（均加法、向后兼容）：
  ```python
  "variantLabel": row.variant_label or "",
  "members": _load_json(row.members_json, None),   # 非探索候选为 None
  ```
- `schemas.py::weight_batch_out` / `weight_batch_summary_out` 增 `"kind": batch.kind` 与 `"explorePlan": _load_json(batch.explore_plan_json, None)`（summary 仅回 `kind` + `comboCount`）。`weight_batch_ranking` 每条增 `"variantLabel"`（已有）+ `"members": _load_json(r.members_json, None)`，供排行榜显示组合名。

### 2.6 数据库迁移（幂等 ALTER，严守 P0 既有模式）

`db/session.py`：
- 扩 `_ensure_weight_run_columns` 的 `batch_columns` 字典加 `"members_json": "TEXT"`（既有 `weight_train_runs` 表须 ALTER 补列，`create_all` 不补，否则老库 INSERT `no such column`）。
- 新增 `_ensure_weight_batch_columns(conn)`（镜像同函数）：对 `weight_train_batches` 补 `"kind": "VARCHAR(16) DEFAULT 'multi_method'"` 与 `"explore_plan_json": "TEXT DEFAULT '{}'"`；`init_db` 内 `create_all` 之后、`_ensure_weight_run_columns` 之后调用。`weight_train_batches` 虽是新表（多数库靠 `create_all` 即带新列），但既有多模型库已建该表 → 必须 ALTER 补列，绝不靠 `create_all`。

### 2.7 前端交互与可视化（`WeightExploreModal`）

**入口**：`MemberPalette` 标题行由纯 `<span>` 改为左右布局，右侧渲染 `探索训练` 按钮（`variant="default" size="sm"`，仅 `members.length ≥ 2` 可用，`title` 说明"从已选因子中挖掘更优子集组合"）。新增可选 prop `onExplore?: () => void`；`FactorSetBuilder` 传入 `() => setShowExplore(true)`。弹窗组件挂在 `FactorSetBuilder` 内（它持有 members/thresholds/name + `useWorkbench()` 的 instId/bar/range/symbols），本地 `const [showExplore, setShowExplore] = useState(false)` 控制开合。**不需要任何 store 交接槽**（弹窗与 builder 同组件树；回流复用既有 `weightTrainResult` 槽）。

**弹窗三阶段**（全视口 overlay，`role="dialog" aria-modal`，Esc/点遮罩关闭；沿用 `var(--*)` 令牌与 `card` 约定）：

> 尺寸约束（v2 P2-5）：`WeightExploreModal.tsx` 只做"外壳 + 阶段路由 + WS 订阅生命周期 + 回流"，三阶段各自拆为子组件 `ExploreConfigPanel` / `ExploreRunningPanel` / `ExploreResultPanel`（各含自身状态与渲染），矩阵单列 `ExploreMatrix.tsx`；单文件守住 CLAUDE.md 的 **800 行 / 单函数 50 行** 阈值。

1. **配置态**
   - *探索因子池*：默认取当前 `members`。chip 列表（可移除）+ "从因子库/我的因子添加"紧凑选择器（复用 `factorApi.library` / `factorApi.list` + `candidateIdentity` 去重；上限 12）。
   - *组合生成策略*：分段控件 `指定子集大小 | IC 排序前缀 | 留一法 | 手动指定`；随选展开参数：
     - 子集大小 / IC 排序前缀：`sizes` 多选 chip（`2因子…poolSize因子`）。（"IC 排序前缀"=按 IC 降序取嵌套前缀子集，非贪心搜索，勿命名为"贪心"，见 P2-4。）
     - 留一法：无参数，展示"将训练 全池 + 逐一去 1 = poolSize+1 个组合"。
     - 手动：可勾选具体组合的轻量构建器（v1 可先支持前 3 种，手动标注为"进阶/后续"）。
   - *训练方法*：多选 chip（复用 `ALL_METHODS` + `factorApi.trainers` 可用性；不可用置灰 + `未安装` Badge），至少 1 个。
   - *候选数预览*（关键 UX）：纯函数 `estimateComboCount(pool, strategy, params)`（`lib/weightExplore.ts`，与后端 planner 同口径）实时算出 `M 组合 × N 方法 = K 候选`；`K > cap` 显式红字警告并禁用启动，`K < 2` 提示需≥2 以对比。
   - `开始探索训练（K 个候选）` 主按钮。
2. **运行态**（可视化训练过程）
   - **组合×方法矩阵热力图**（`ExploreMatrix.tsx`，本特性签名可视化）：行=组合（`explorePlan.combos` 顺序，行首 chip 显示组合名 + 成员数），列=方法（`methodLabel`）。单元格 = 该 `(组合,方法)` 候选（按 `variantLabel==combo.id && method` 定位）：`queued` 灰底、`running` 脉冲 + `phase` 进度环、`succeeded` 按 `icOut` 上色（发散：负→`var(--short)`、正→`var(--long)`，强度按 |icOut| 归一）并标注 IC 值、`failed` `✕`、`cancelled` 中性。最佳单元格 🏆 + `ring-[var(--long)]/40`。
   - **实时排行榜**：复用 `WeightBatchCompare` 同结构（列：排名/组合·方法/状态/样本外IC/Δvs#1/操作），候选名用 `comboLabel · methodLabel`。
   - **总进度**：`X / K 完成` + 进度条 + `全部取消`（`weightBatchApi.cancel`）。单元格/行点击 → 右侧抽屉展示该候选 `TrainingCurveChart`（学习曲线）+ `WeightBars`（权重）。
   - 断线/停滞横幅同竞技场（watchdog 20s×2）。
3. **完成态**（展示结果 + 选最佳）
   - 矩阵满格热力图 + 最终排行榜 + 最佳组合摘要卡（"最佳：RSI·MACD·ATR + XGBoost · 样本外 IC 0.087"）。
   - 操作：`应用最佳组合到组合成员`（回流 + 关弹窗）、`查看候选详情`、`只保留最佳/删除本次探索`（`ConfirmDialog`）。

**WS 消费**：复用 `weightBatchWS(batchId, onEvent, onOpen)` + `weightBatch.ts` 的 `emptyCandidate`/`applyCandidateEvent`/`rankCandidates`/`bestRunId`/`allSettled`——探索批次的 WS 事件与批次完全一致（`batch`/`candidate`/`batchDone`）。弹窗 reducer 态 = `Record<runId, CandidateState>`，与竞技场同构；矩阵只是"按 (variantLabel, method) 二维索引"这份态的展示。

**应用最佳组合（回流）· v2 已就地修复 P0-1 + P1-1**：胜出候选携其**自有组合成员**（来自 `detail.candidates[i].members` 或 `explorePlan.combos`）。构造 target：
```ts
const target: WeightTrainTarget = {
  members: winner.members,               // 胜出子集，而非整池
  method:  winner.method,
  thresholds: builder.thresholds,
  name,
  instId, bar, startTs, endTs,
  // config.normWindow 必须等于胜出候选训练时的 normStats.normWindow（探索全候选共享同一 config.normWindow，
  // 故取共享 normWindow 即可）——否则后续保存因子组合会 400 FACTOR_NORM_WINDOW_MISMATCH。
  config: { normWindow: sharedConfig.normWindow },
  factorSetId,
  origin: "builder",                     // 单写点回流仅 builder-origin 生效（G-F1）
};
```

**两条硬约束（勿退回 v1 写法）**：

- **P1-1 单写点不变量（G-F1）**：`setWeightTrainResult` 全仓**唯一写入点在 `applyToBuilder` 内**。探索回流**不得**在 `WeightExploreModal` 里直接调 `setWeightTrainResult`。做法：复用 arena 同一 `applyToBuilder(winner.result, target, ids)`；若其签名不便共用，则抽取 arena 与 explore **共享**的 `flowResultToBuilder(result, target, ids)` 单一写入器，二者都经它（保持"仅一个 `setWeightTrainResult` 调用点"）。
- **P0-1 绝不绑定/改写探索父模型**：`ids` 传 `{ modelId: null, runId: winner.runId }` → 水合 effect `setBoundModelId(null)`，落为**全新未保存草稿**。随后用户在组合成员面板保存即生成一个**全新 `source="builder"` 模型/因子组合**，携胜出子集成员；**绝不**把 builder 绑定到探索父模型（`source="batch"`、members=整池），从而避免"更新模型"静默改写隐藏父模型、清空其 run 指针、保存后不可见（v1 的 `setBoundModelId(resolvedParentId)` 即此坑）。`runId` 仅作 provenance，不触发对父模型的写入。

```ts
// 复用/共享单写入器（唯一 setWeightTrainResult 调用点在其内部）：
applyToBuilder(winner.result, target, { modelId: null, runId: winner.runId });
onClose();   // 关弹窗；FactorSetBuilder 既有 weightTrainResult 消费 effect（consumedResultRef 防重入）自动水合
             // members/method/name/thresholds/trained，boundModelId=null → 保存生成全新模型
```
——回流仍**复用 `FactorSetBuilder` 既有 `weightTrainResult` 消费 effect + `consumedResultRef` 防重入**；与竞技场 `applyToBuilder` 的差异有二：`members` 用胜出子集（非固定池）、`modelId=null`（不绑定批次父模型）。回流后用户即可在组合成员面板保存新组合、自动同步交易策略。

> 注（P2-6 复用地图纠偏）：探索弹窗是 `FactorSetBuilder` 内**新增**的本地 `useState` modal，这是合理的；但**勿**在实现中声称"镜像 arena 的 modal 挂载"——arena 实为 `WeightModelPanel` 的 `view.kind` 状态机 + store 交接（`setWeightBatchTarget`）+ `setSection("models")` 导航，并非 modal。二者形态不同，只共享**回流写入器**与 **WS/reducer** 这两处基座。

---

## 三、文件 / 模块变更计划（File / Module Change Plan）

| 文件 | 新建/修改 | 一句话意图 |
|---|---|---|
| `services/quant/app/factors/weight_explore_planner.py` | 新建 | 纯函数组合生成器（sizes/topk_prefix/leave_one_out/manual + 去重 + 成员数边界）。 |
| `services/quant/app/factors/weight_explore_service.py` | 新建 | 探索编排：守门→生成组合→展开候选→建 explore 批次(按候选成员)→注册复用运行时。 |
| `services/quant/app/api/weight_batches.py` | 修改 | 加 `POST /weight-models/explorations` 路由，委托 `weight_explore_service.start_exploration`（读/择优/取消/删除/WS 复用既有路由）。 |
| `services/quant/app/factors/weight_batch_runtime.py` | 修改 | `CandidateSpec` 增 `members`；`_start_candidate` 用 `cand.members or spec.members` 起作业。 |
| `services/quant/app/db/models.py` | 修改 | `WeightTrainRun.members_json`（nullable）；`WeightTrainBatch.kind` + `explore_plan_json`。 |
| `services/quant/app/db/session.py` | 修改 | `_ensure_weight_run_columns` 加 `members_json`；新增 `_ensure_weight_batch_columns`（kind/explore_plan_json）并在 `init_db` 调用。 |
| `services/quant/app/db/weight_model_repositories.py` | 修改 | `WeightRunStart.members`；`add_weight_run` 落 `members_json`。 |
| `services/quant/app/db/weight_batch_repositories.py` | 修改 | `WeightBatchSpec.kind`/`explore_plan`；`create_weight_batch` 落新列。 |
| `services/quant/app/factors/weight_model_service.py` | 修改 | `backtest_run` 成员读取优先 `run.members_json`（探索候选自有组合回测）。 |
| `services/quant/app/schemas.py` | 修改 | `weight_train_run_out` 加 `members`/`variantLabel`；`weight_batch_out/summary/ranking` 加 `kind`/`explorePlan`/`members`；新增 `WeightExploreStartReq` + `weight_explore_start_out`。 |
| `services/quant/app/config.py` | 修改 | 加 `max_weight_explore_candidates`（v2：默认 **12**，`ge=2, le=24`；对齐同族 `max_weight_batch_candidates`=8/le16 的资源包络，约 1.5×，见 P1-3）。 |
| `apps/web/components/Factor/MemberPalette.tsx` | 修改 | 标题**裸 `<span>` 先包一层 `flex items-center justify-between`** 再于右侧加 `探索训练` 按钮 + 新增 `onExplore?` prop（现无此 prop）。 |
| `apps/web/components/Factor/FactorSetBuilder.tsx` | 修改 | 挂**新增** `WeightExploreModal`（builder 内本地 `useState showExplore` modal，**非** arena 的 view.kind/store 交接模式），传池/阈值/名/工作台区间；回流经共享 `applyToBuilder` 单写入器（`modelId=null`）。 |
| `apps/web/components/Factor/WeightExploreModal.tsx` | 新建 | 探索弹窗外壳：阶段路由 + WS 订阅生命周期 + 回流；三阶段拆子面板（下）。复用 batch WS/reducers。 |
| `apps/web/components/Factor/ExploreConfigPanel.tsx` / `ExploreRunningPanel.tsx` / `ExploreResultPanel.tsx` | 新建 | 三阶段子面板（P2-5 控行数）。 |
| `apps/web/components/Factor/ExploreMatrix.tsx` | 新建 | 组合×方法矩阵热力图（本特性签名可视化）。 |
| `apps/web/lib/weightExplore.ts` | 新建 | **纯函数 + 类型**：`buildExploreBody`/`estimateComboCount`/`assembleMatrix`/`applyBestTarget` + 探索请求/计划/响应/矩阵态 TS 类型（camelCase 对齐后端）。 |
| `apps/web/lib/weightExploreApi.ts` | 新建 | `weightExploreApi.start(body)`（HTTP 层，**独立文件**，镜像 `weightBatchApi.ts` 与 `weightBatch.ts` 的 API/body-builder 拆分口径）。 |
| `apps/web/components/Factor/WeightBatchLauncher.tsx` 或共享模块 | 修改 | 把私有 `const ALL_METHODS` **上移为共享导出**供探索弹窗复用（现仅为该文件私有 const）。 |
| `apps/web/components/Factor/WeightBatchList.tsx` | 修改 | 列表按 `kind` 加"探索"徽标 + `M 组合 × N 方法` 概要（探索批次也在此可见/可删）。 |
| `apps/web/lib/weightBatchTypes.ts` | 修改 | `WeightBatchSummary`/`WeightBatchDetail` 加 `kind`/`explorePlan?`（Detail 已有 `members`）。 |
| `apps/web/lib/weightModelTypes.ts` | 修改 | **前端 `WeightTrainRun` 定义在此文件**（非 weightBatchTypes.ts）且**现无 `variantLabel`**：加 `members?`/`variantLabel`。 |

配套测试文件见第六节。

---

## 四、接口设计（Interface Design）

### 4.1 REST（新增，其余复用批次端点）

`POST /api/weight-models/explorations` → `201`

请求体 `WeightExploreStartReq`（继承 `_FactorRange` 的 `instId/bar/startTs/endTs` 校验）：
```jsonc
{
  "instId": "BTC-USDT-SWAP", "bar": "1H", "startTs": 1700000000, "endTs": 1706000000,
  "pool": [ {"factorKey":"rsi","params":{},"weight":0,"direction":"long"}, ... ],  // 1..12
  "strategy": "sizes" | "topk_prefix" | "leave_one_out" | "manual",
  "params": { "sizes":[2,3], "rankIc":[0.05,0.03,...], "combos":[[0,1],[0,1,2]] },  // 按 strategy 取用
  "methods": ["linear","xgboost"],           // 1..5，⊆ 五法
  "config": { "normWindow":200, "thresholds":{...} },  // 共享训练配置（含 normWindow + 供父模型回测的 thresholds）
  "horizon": 6, "factorSetId": 123, "origin": "builder" | "model", "maxBars": null
}
```
响应 `WeightExploreStartResult`：
```jsonc
{ "batchId":9, "parentModelId":42, "label":"RSI·MACD 等3因子 组合探索", "kind":"explore",
  "origin":"builder", "explorePlan":{...},
  "candidates":[ {"runId":100,"method":"linear","variantLabel":"C0","comboLabel":"RSI·MACD","status":"queued"}, ... ] }
```
错误码（`{error:{code,message}}`）：`FACTOR_INVALID_BAR`(400)、`FACTOR_TRAIN_INVALID_CONFIG`(400)、`FACTOR_TRAIN_TOO_LARGE`(413)、`WEIGHT_EXPLORE_NO_METHOD`(400)、`WEIGHT_EXPLORE_INVALID_PLAN`(400)、`WEIGHT_EXPLORE_TOO_MANY_CANDIDATES`(400)、`WEIGHT_EXPLORE_TOO_FEW_CANDIDATES`(400)。

**复用批次端点**（探索批次 = 批次，`kind="explore"`）：`GET /weight-models/batches`（列表含探索批次）、`GET /weight-models/batches/{id}`（详情，`candidates[i].members` 携组合、`kind`/`explorePlan`）、`POST /weight-models/batches/{id}/select`、`POST /weight-models/batches/{id}/cancel`、`POST /weight-models/batches/{id}/candidates/{runId}/cancel`、`DELETE /weight-models/batches/{id}`。

### 4.2 WebSocket（复用）

`GET /api/ws/weight-batch/{batchId}`——事件流不变：`{type:"batch"}`（初始 roster，每候选带 `variantLabel`）、`{type:"candidate", data:{runId,method,variantLabel,event:<TrainStreamEvent>}}`、`{type:"batchDone", data:{ranking}}`、`{type:"ping"}`。订阅即全量回放（含终态），重连幂等，`batchDone` 后前端主动关闭。

### 4.3 前端纯函数签名（`lib/weightExplore.ts`）

```ts
export function estimateComboCount(pool: FactorMember[], strategy: ExploreStrategy, params: ExploreParams): number;
export function buildExploreBody(input: {
  pool: FactorMember[]; strategy: ExploreStrategy; params: ExploreParams; methods: TrainMethod[];
  thresholds: FactorThresholds; horizon: number; instId: string; bar: string;
  startTs: number; endTs: number; factorSetId?: number; origin?: WeightBatchOrigin;
}): WeightExploreStartBody;
export function assembleMatrix(plan: ExplorePlan, states: CandidateState[]): ExploreMatrix; // 行=combo,列=method,格=候选
export function applyBestTarget(winner: ExploreWinner, ctx: {...}): WeightTrainTarget;        // 用胜出组合子集成员 + origin:"builder" + config.normWindow=sharedNormWindow
// weightExploreApi 独立在 lib/weightExploreApi.ts（镜像 weightBatchApi.ts / weightBatch.ts 的 API↔body-builder 拆分）：
// export const weightExploreApi = { start(body: WeightExploreStartBody): Promise<WeightExploreStartResult> };
```

回流落地由共享单写入器完成（**唯一** `setWeightTrainResult` 调用点）：`applyToBuilder(winner.result, applyBestTarget(winner, ctx), { modelId: null, runId: winner.runId })`——见 §2.7 / P0-1 / P1-1。

---

## 五、数据模型（Data Model）

### 5.1 DB 变更（全部加法、幂等 ALTER）

`weight_train_runs`（既有表，补列）：
| 列 | 类型 | 语义 |
|---|---|---|
| `members_json` | `TEXT NULL` | 候选自有组合成员快照；`NULL`=沿用父模型成员（既有行零影响）。 |

`weight_train_batches`（补列）：
| 列 | 类型 | 语义 |
|---|---|---|
| `kind` | `VARCHAR(16) DEFAULT 'multi_method'` | `multi_method`（多方法批次）\| `explore`（探索批次）。 |
| `explore_plan_json` | `TEXT DEFAULT '{}'` | 探索计划快照（strategy/params/methods/combos 展示元数据）。 |

无新表、无外键变更；`WeightTrainRun.model_id` 仍指父模型，级联删仍走 service 单事务（`cascade_delete_weight_batch`）。父模型 `source="batch"`（复用既有主列表过滤，无 `WEIGHT_MODEL_SOURCES` 白名单改动）。

### 5.2 内存态 / 前端 shape（要点）

- `CandidateSpec`（运行时）：`+ members: list | None`。
- `WeightRunStart`（repo）：`+ members: list | None`。
- `CandidateState`（前端 reducer，复用既有）：不变；矩阵按 `(variantLabel, method)` 索引。
- `ExplorePlan`（前端）：`{ strategy, params, methods, poolKeys, combos:[{id,label,memberKeys}] }`。
- `ExploreMatrix`：`{ rows:[{comboId,label,memberCount}], cols:TrainMethod[], cells:Record<`${comboId}:${method}`, CandidateState | null> }`。

---

## 六、测试与验收标准（Testing & Acceptance）

### 6.1 后端（`services/quant`，`.venv\Scripts\python -m pytest -q`，cwd=`services/quant`）

- `tests/test_weight_explore_planner.py`（纯函数）：
  - `sizes` 对给定池与 `sizes=[2,3]` 生成精确 C(n,2)+C(n,3) 组合与顺序；`topk_prefix`（IC 排序前缀）按 rankIc 降序取嵌套前缀；`leave_one_out` = 全池 + n 个去一，且 **`|pool|==1` 时跳过空组合仅留全池 1 个（不 `ValueError`）**；`manual` 下标越界 / 显式空组合 → `ValueError`。
  - 跨策略/重复输入按 `memberIdentity` 集合去重；保留组合成员越界（0 或 >12）→ `ValueError`。
- `tests/test_weight_explore_api.py`：
  - 守门序：pool 空/超 400、无方法 400、非法计划 400、候选数 >cap 400、<2 400、区间过长 413；断言错误码，且**全部 400（含候选数 >cap）先于 413**（对齐 §2.3 v2 顺序 ⑥候选数 → ⑦区间）。
  - 成功启动：创建 `kind="explore"` 批次 + 父模型(`source="batch"`) + 每候选一条 `queued` run 且 `members_json` = 对应组合；`explore_plan_json` 落库；响应 `candidates` 带 `comboLabel`。
  - 复用性：`GET/cancel/DELETE /weight-models/batches/{id}` 对探索批次正常工作；`detail.candidates[i].members` 回各自组合。`select` 仅置 `batch.selected_run_id`（指针），**断言不改写父模型 `members_json`**（保持整池，异构候选不污染父模型）。
  - **等价性硬门禁**：某探索候选（组合 C、方法 M）终态 `weights/normStats/metrics` 与独立 `POST /weight-models/train`（`members=C, method=M`，同区间/config）**字节级相等**（扩 `test_streaming_result_equals_sync_train` 思路，新增用例；绝不改 `engine/factors/training.py`）。
  - **按候选回测**：探索候选一键回测（`POST /weight-models/{parentId}/runs/{runId}/backtest`）用候选自有组合成员（断言 `members` 取 `run.members_json`）。
- `tests/test_db_migrations`（若无则并入既有 session 测试）：老库（无 `members_json`/`kind`/`explore_plan_json`）经 `init_db` 幂等补列后，既有单次训练/多方法批次 INSERT/SELECT 不 `no such column`。

### 6.2 前端（`apps/web`，`pnpm test` → `vitest run`）

- `lib/weightExplore.test.ts`：`estimateComboCount` 与后端 planner 同口径（同池/策略/参数 → 同数）；`buildExploreBody` 结构/默认 `origin`；`assembleMatrix` 按 `(comboId,method)` 正确落格 + 空格为 `null`；`applyBestTarget` 用胜出组合成员。
- `components/Factor/WeightExploreModal.test.tsx`：候选数 >cap 时启动禁用 + 警告；无方法禁用；WS `candidate`/`batchDone` 事件驱动矩阵/排行榜；`应用最佳组合` 经共享 `applyToBuilder`（唯一 `setWeightTrainResult` 调用点）以胜出**子集**成员回流、`modelId=null`（断言 `setBoundModelId(null)`，**不**绑定探索父模型 id）并关闭；`全部取消` 调 `weightBatchApi.cancel`。
- `components/Factor/MemberPalette.test.tsx`：`members.length≥2` 时渲染 `探索训练` 按钮且点击调 `onExplore`；`<2` 禁用。
- `components/Factor/ExploreMatrix.test.tsx`：热力图上色分档（负/正/最佳 🏆）与单元格状态映射。

### 6.3 验收标准（Definition of Done for 后续开发节点）

1. 组合成员面板标题右出现 `探索训练` 按钮，点击弹出探索弹窗。
2. 弹窗内可编辑因子池、选组合策略与训练方法，实时预览候选数并对超限/不足给出显式阻断。
3. 启动后弹窗内实时可视化训练过程（组合×方法矩阵 + 排行榜 + 逐候选曲线），全部终态后展示结果。
4. 用户可一键"应用最佳组合"，胜出组合**子集**成员/方法/权重经共享单写入器回流为**全新 builder 草稿**（不绑定探索父模型），保存生成全新 `source="builder"` 模型/因子组合并可继续建策略。
5. 探索批次可在批次列表以"探索"徽标查看/取消/删除；候选一键回测用其自有组合；`select` 仅记录 `selected_run_id` 不改写父模型成员。
6. 前后端测试全绿；老库幂等补列不崩（`members_json`/`kind`/`explore_plan_json` 三列）；权重训练等价性门禁不降级；`setWeightTrainResult` 仍为全仓唯一写入点。

---

## 七、风险与缓解（Risks & Mitigations）

| 风险 | 影响 | 缓解 |
|---|---|---|
| **组合爆炸**（大池 × 多方法 → 候选数失控） | 拖垮 OKX 取数 / 训练队列 / 墙钟 | `max_weight_explore_candidates` 硬上限（v2：默认 **12**，`ge=2 le=24`，对齐 arena 资源包络），service 层**展开候选后、取数/注册前** 400 阻断（`WEIGHT_EXPLORE_TOO_MANY_CANDIDATES`）；前端 `estimateComboCount` 提前预览并禁用启动，绝不静默截断。并发受 `max_weight_train_jobs=4` 限（OKX 突发 ≤4），墙钟随候选数线性增长（N 候选 / 4 并发串行），文档明示便于用户预期。 |
| **既有表补列踩 `create_all` 陷阱** | 老库 `no such column` 连既有训练一并崩（P0） | 严格复用 `_ensure_weight_run_columns` 模式：`members_json` 走 `weight_train_runs` ALTER、`kind`/`explore_plan_json` 走新 `_ensure_weight_batch_columns` ALTER，`init_db` 内 create_all 之后调用。 |
| **等价性门禁回归** | 探索候选与独立训练结果漂移，可信度崩 | 只在批次运行时改"透传哪份成员"，绝不碰 `engine/factors/training.py`；新增等价性用例守护候选 ≡ 独立 train。 |
| **回测/回流读错成员**（候选成员≠父模型池） | 回测用错组合、回流劫持成错成员 | `backtest_run` 优先 `run.members_json`；`members_json is None` 时才回落父模型（既有行为不变）。回流复用 `FactorSetBuilder` 既有 `weightTrainResult` 消费路径 + `consumedResultRef` 防重入，成员用胜出子集。 |
| **回流绑定探索父模型致隐性损坏（P0-1）** | 「应用最佳→更新模型」静默改写隐藏 `source="batch"` 父模型、清 run 指针、保存后不可见 | 回流 `modelId=null` 落为**全新 builder 草稿**，绝不 `setBoundModelId(parentId)`；父模型仅作批次容器随批次级联删。`select` 端点仅指针语义，不据候选子集改写父模型成员（§2.3 / P1-4）。 |
| **回流破坏单写点不变量（P1-1）** | 第二个 `setWeightTrainResult` 调用点 → 跨上下文回流串扰（G-F1） | 探索回流**不**直接调 `setWeightTrainResult`，一律经 arena/explore 共享的 `applyToBuilder`（或 `flowResultToBuilder`）单一写入器，`target.origin="builder"`。 |
| **保存因子组合 normWindow 不匹配** | 回流后保存 400 `FACTOR_NORM_WINDOW_MISMATCH` | target `config.normWindow` = 胜出候选训练时的 `normStats.normWindow`（探索全候选共享同一 config.normWindow，取共享值即对齐）。 |
| **并发调度丢唤醒 / 悬挂候选**（继承批次风险） | 候选永卡 queued / 批次永不 batchDone | 完全复用既有 `WeightBatchRuntime`（`reserve_when_free` 检查-等待同锁闭环、转发协程见终态即退、整批取消显式 `notify_slot_free`）+ 启动对账 `reconcile_dangling_weight_runs`（queued 同判），本特性零新增并发原语。 |
| **弹窗与 builder 状态串扰** | 回流时 builder 误清/误水合 | 弹窗为 builder 内本地 `showExplore` 开合，不新增 store 交接槽；回流经既有共享 `applyToBuilder`（唯一 `setWeightTrainResult` 调用点，一次性 + `consumedResultRef` 防重入），关弹窗后由既有 effect 水合。 |
| **变体标签超长**（`variant_label` `String(64)`） | 组合名溢出列 | `variant_label` 只存稳定组合 id（`C0`/`C1`…），展示名走 `explore_plan_json.combos[].label`，绝不把长成员串塞进 `variant_label`。 |
| **`topk_prefix` 缺 rankIc** | IC 排序前缀退化 | 前端传 `params.rankIc`（`我的因子` 已有 `primaryRankIc`）；缺失时 planner 回落 pool 顺序（确定性、可测），不报错。 |

---

## 八、非目标（Out of Scope）

- 不做跨标的/标的组的组合探索扇出（沿用当前 `useWorkbench()` 单目标/组，标的组能力由既有 symbol-groups 承接，不在本特性叠加）。
- 不做超参数网格搜索（本特性探索"因子组合 × 方法"，超参变体仍属多方型批次范畴，可后续融合）。
- 不持久化"探索会话历史"到独立表（探索即批次，`kind="explore"` + `explore_plan_json` 已足够复现与展示）。
- `manual` 手动组合构建器 UI 可作为 v1.1（v1 优先三种自动策略；后端 planner 已支持 `manual`，前端后补）。

---

## 实施过程发现的方案缺陷（Issues Found During Implementation）

> 记录人：Subtask #2（代码开发）· 2026-07-02。下列为按方案落地时发现的、需在文件变更表之外额外触碰的点，均为
> 忠实实现 §2.7（P0-1/P1-1）所必需，已就地采纳修正方案并全测覆盖；不改变方案架构与验收标准。

- **D-1｜回流「全新草稿」需把 `WeightTrainResult.modelId` 拓宽为 `number | null`，并顺带改 `lib/weightModel.ts::buildTrainResult`（变更表未列该文件）**。
  §2.7/§4.3 要求探索回流经**共享单写入器** `buildTrainResult(winner.result, target, { modelId: null, runId })` 落地，且 `FactorSetBuilder`
  既有消费 effect 执行 `setBoundModelId(result.modelId)`——故 `modelId=null` 必须能穿过 `WeightTrainResult` 契约与
  `buildTrainResult` 的 `ids` 形参。原契约 `modelId: number`（非空）无法承载「全新草稿」语义。**修正**：把
  `lib/weightModelTypes.ts::WeightTrainResult.modelId` 与 `lib/weightModel.ts::buildTrainResult` 的 `ids.modelId` 均拓宽为
  `number | null`（纯加宽、arena/直播回流传真实 id 行为字节不变）。这是实现 P0-1（不绑探索父模型）+ P1-1（复用唯一
  `setWeightTrainResult` 写入点，不新增第二写入点）的最小改动，比「抽取新 `flowResultToBuilder`」更省面且等价——
  `buildTrainResult` 本就是 arena/直播/探索三方共享的结果构造真源。`weightModel.ts` 因此进入变更集（变更表原仅列 `weightModelTypes.ts`）。

- **D-2｜`ALL_METHODS` 上移落点选定为 `lib/weightBatch.ts`（变更表写「`WeightBatchLauncher.tsx` 或共享模块」）**。
  取 `lib/weightBatch.ts` 作共享模块（与既有 `DEFAULT_BATCH_METHODS` 同处、`launcher` 与探索配置面板共同复用），
  `WeightBatchLauncher.tsx` 改为从该处 import，删除其私有 `const ALL_METHODS`。属方案允许的二选一，无架构影响。

- **D-3｜迁移测试独立成文件 `tests/test_weight_explore_migration.py`（§6.1 允许「若无则并入既有 session 测试」）**。
  为聚焦「老库无 `members_json`/`kind`/`explore_plan_json` 三列经 `init_db` 幂等 ALTER 补列后 INSERT/SELECT 不
  `no such column`」（C-2），新建独立测试文件而非并入，便于回归定位。

- **备注｜非缺陷、属方案既定 v1 边界**：`topk_prefix` 前端 v1 不传 `params.rankIc`（`FactorMember` 不携逐成员 IC），
  planner 依 §7 缺省回落**池顺序**（确定性、已测）；`manual` 策略后端已实现并测试，前端配置态 v1 仅呈现三种自动策略
  （§八），二者均为方案明列的 v1 范围，非实现偏差。

---

## 评审结论（Review Verdict）

**判定：有条件通过（Approved with Conditions）· v2。**

本方案的核心架构决策——「探索训练 = 一个 `kind="explore"` 批次，唯一一般化是候选携各自成员，读/取消/删除/WS/运行时/迁移全部复用既有 arena 机制」——经对照真实代码逐节核验，**可行、右尺寸、与既有『薄编排叠薄编排』哲学一致**。复用面被正确控制到最小：`JobSpec.members`/`BatchSharedSpec.members`/`variant_label(64)` 均已存在，迁移正确规避了 `create_all` 不补列的既有 P0 陷阱，`source="batch"` 已在白名单，等价性门禁被明确保护。

评审中发现的 **1 个 P0、4 个 P1 已在本 v2 文档就地修复**（不再遗留）：

1. **[P0-1]** 回流不再绑定/改写探索父模型——改为 `modelId=null` 落全新 builder 草稿（§2.7）。
2. **[P1-1]** 回流复用 arena/explore 共享的 `applyToBuilder` 单写入器，保住 `setWeightTrainResult` 全仓唯一写入点不变量（§2.7）。
3. **[P1-2]** 守门顺序重排为「全部 400 先于 413 区间守门」，与测试断言及 arena 先例自洽（§2.3 / §6.1）。
4. **[P1-3]** 候选上限收敛为默认 12 / `ge=2 le=24`，对齐既有资源包络并明示墙钟/并发边界（§2.3 / §4.1 / §7）。
5. **[P1-4]** `select` 端点语义澄清为「仅指针 repoint、不据候选子集改写父模型成员」（§2.3）。

**放行的附带条件（开发节点务必落实，否则视为未完成）：**

- **C-1（等价性门禁不可降级）**：探索候选（组合 C、方法 M）终态 `weights/normStats/metrics` 必须与独立 `POST /weight-models/train`（`members=C, method=M`）字节级相等；**绝不改** `engine/factors/training.py`。唯一实质改动 = `CandidateSpec` 加 `members` + `weight_batch_runtime.py:164` 改 `cand.members or spec.members`。
- **C-2（迁移必须走新 ALTER 补列器）**：`members_json`（`weight_train_runs`）+ `kind`/`explore_plan_json`（`weight_train_batches`，须**新增** `_ensure_weight_batch_columns`）一律经 `PRAGMA table_info` + `ALTER` 幂等补列，`init_db` 内 `create_all` 之后调用；**不得**指望 `create_all` 给既有表补列（老库会 `no such column` 连既有训练一并崩）。
- **C-3（成员读取点全审计）**：凡"从 run 推成员"的路径（`backtest_run`、`weight_train_run_out`、经 `select` 后的 `factor_score`/因子组合同步链等）一律「优先 `run.members_json`、为空回落父模型」，异构候选不得误用整池（P2-7）。
- **C-4（回流单写点 + 不绑父模型）**：探索回流仅经共享 `applyToBuilder`（唯一 `setWeightTrainResult` 调用点）、`modelId=null`、`target.origin="builder"`、`config.normWindow=` 胜出候选训练 normWindow（避 `FACTOR_NORM_WINDOW_MISMATCH`）。
- **C-5（前端复用地图按 P2-6 纠偏）**：`ALL_METHODS` 上移共享；前端 `WeightTrainRun` 改在 `lib/weightModelTypes.ts` 加 `variantLabel`/`members`；`weightExploreApi` 独立 `lib/weightExploreApi.ts`；`MemberPalette` 标题先包 flex 行；`weightBatchWS` 第三参为 `onState`；勿把探索弹窗声称为"镜像 arena modal"。
- **C-6（尺寸阈值）**：`WeightExploreModal.tsx` 拆 `ExploreConfigPanel/RunningPanel/ResultPanel` + `ExploreMatrix`，服务端 `start_exploration` 抽辅助函数，守住 CLAUDE.md 的 800 行 / 50 行 / 参数 ≤5 / 嵌套 ≤4 阈值。
- **C-7（组合枚举双实现锁步）**：前端 `estimateComboCount` 与后端 `plan_combinations` 同口径同去重，由 §6.2 平价测试守护，改一侧必同步另一侧与测试。

以上 P0/P1 已修复、7 项条件在验收标准（§6.3）与测试计划（§6）中均有对应断言。满足条件即可进入代码开发节点。

> 评审签署：Subtask #1（方案评审与修复）· 2026-07-02 · 文档版本 **v2**。
