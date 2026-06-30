# 因子组合策略（Factor Combination Strategies）开发方案 · spec.md

> Feature: `factor-combination-strategies`
> 版本：**v2**（方案评审与修复 / Subtask #1 产出；v1 为 Subtask #0 方案设计）
> 适用分支：master
> 语言栈：Next.js 14 App Router + React 18 + Zustand + TanStack Query（前端）；FastAPI + SQLAlchemy（后端，本方案**零改动**）

---

## 评审记录（Review Notes · Subtask #1）

> 评审人：资深评审（Subtask #1）。评审基准：对当前 `master` 实测代码逐项核对——`apps/web/lib/factorApi.ts`、`components/Factor/FactorLab.tsx`、`FactorSetBuilder.tsx`、`components/Strategy/StrategyDetail.tsx`、`lib/store.ts`、`lib/factorTypes.ts`、`lib/factor.ts`，以及后端 `services/quant/app/api/factors.py`、`factors/specs.py`、`api/strategies.py`。
>
> **核对为真的关键判断**（方案技术基础扎实）：后端 `factor-sets` CRUD + `/{id}/score` 齐全；`update_factor_set` 在同传 `modelJson`+`config` 时跑 `assert_norm_contract`、同传 `members`+`modelJson` 时跑 `assert_weights_cover`（partial 安全）；`POST/PUT /strategies` 的 `validate_factor_strategy_params` 仅校验 `params.members`、不剥离其余字段，故新增标量 `factorSetId` 可随 `provenance/normStats` 一道透传 `params_json`（**零后端改动可行**）；`FactorSetBuilder` 确有私有 `WeightBars` 与 `useEffect(()=>setTrained(null),[members])`（H3 复述属实）；`StrategyDetail.FactorStrategyBanner` 的「在因子实验室编辑」当前仅 `setFactorLabSection("builder")+setActiveView("factor")`（编辑名不副实属实）；`store` 中 `factorLabSection` 持久化、`factorOverlay`/`comboEditingId` 为瞬态（不入 partialize）。
>
> 下表 P0/P1 已在本 **v2** 正文就地修复；P2 列为开发期（Subtask #2）注意项并写入文末「评审结论」。

| # | 等级 | 位置 | 问题 | 处置 |
|---|---|---|---|---|
| P0-1 | **P0** | §2.2 数据流 / §3 文件清单 / §6 验收#2 | 「上图叠加到主图」依赖 store 字段 `factorOverlay`，但全仓**零消费方**——仅 `FactorEvalPanel`/`AgentPanel` 写入，**无任何组件读取 `factorOverlay` 渲染主图叠加**（已 grep 全仓确认）。把它列为交付物 + DoD 会让 Subtask #2 做出一个静默无效的按钮，且验收无法如实通过。 | 就地**移出本方案范围**：从数据流、文件意图、验收删去「上图」，标注为依赖「主图 因子/得分 pane 渲染消费者」的独立后续工作（超出本方案「前端零新增渲染管线、后端零改动」边界）。需求本身（task-board）亦未要求上图。 |
| P1-1 | **P1** | §2.3 流程B / §4 类型 / §7 风险 | 风险表称「回链失效时自动回落 snapshot 分支」，但 `buildEditTargetFromStrategy` 在有 `factorSetId` 时只返回 `{source:"set",setId}`——**不带任何快照载荷**，无可回落对象；当 `factorSetId` 指向已删组合时 `sets.get` 404 未捕获 → 交易面板「在因子实验室编辑」在该场景直接报错。文档自相矛盾。 | `set` 目标改为**同时携带内联快照兜底**；builder 的 set 水合 `try/catch`，`sets.get` 失败即按快照水合并提示「来源组合已删除，已按策略快照载入」。 |
| P1-2 | **P1** | §2.1 / §2.4 / §6 验收#1 | 需求要列出「**所有**已经创建过的因子组合策略」，但 builder 允许不点「保存组合」就直接「从组合新建策略」，产生**无 `factor_set` 背书的孤儿** `factor_score` 策略，不会出现在列表中 → 与「所有」不符。 | 「从组合新建策略」在 `editingSetId==null` 时**先自动落库该组合**再带 `factorSetId` 建策略，保证每条策略都有可列出的来源组合，且交易面板编辑恒走稳健的 `set` 分支。 |
| P1-3 | **P1** | §2.4 | 编辑态歧义 footgun：`editingSetId` 为 builder 局部 `useState`，离开 builder 工作区（组件卸载）即丢失，**silently 从「更新组合」回落「新建组合」**；且与既有 `comboEditingId`（置于 store 瞬态以跨切换存活）模式不一致。 | builder 顶部**显著呈现**当前编辑态 + 显式「新建组合」清空；「更新组合」复用与「保存组合」同一 `blockReason` 禁用门。可选硬化：把 `editingSetId/name` 提升 store 瞬态镜像 `comboEditingId`。 |
| P2-1 | P2 | §2.4 | 无重训直接「更新组合」应保留原 `model.provenance/trainedAt`，勿用 `Date.now()` 重打戳；`trainedFromSet` 在 `set.metrics` 为 `null` 时需回落零值 `TrainMetrics`，避免 `TrainResult.metrics` 缺失。 | 开发期注意（见结论）。 |
| P2-2 | P2 | §2.3 | `factorEditTarget` 须在 effect 起始**同步清空**（局部捕获 + `ref` 防重入）再做 async `sets.get`，避免加载态重渲染导致二次水合 / 二次拉取。 | 开发期注意。 |
| P2-3 | P2 | §3 | `FactorSetDetail` 复用 `FactorScoreChart` 需把 `ScorePoint{ts,score}` 适配为 `LinePoint`（镜像 `FactorEvalPanel` 的 `factorPointsToLine`），二者类型不同。 | 开发期注意。 |
| P2-4 | P2 | §2.3 | 删除须二次确认 + `invalidateQueries(["factor","sets"])`；删除当前选中项后需清 `selectedId` 防悬空详情。 | 开发期注意。 |
| P2-5 | P2 | §3 | 列表/详情/builder 新增与改造须守 CLAUDE.md 清洁阈值（≤800 行/文件、≤50 行/函数、≤4 层嵌套、形参 ≤5）。 | 开发期注意。 |

---

## 一、概述（Overview）

本需求来自交易工作台右侧「交易策略」面板：用户在那里能看到由因子实验室生成的 **因子组合得分策略**（后端 `factor_score`，`kind="factor"`，前端 `StrategyDetail.tsx` 里的 `FactorStrategyBanner`）。该卡片上已有一个「在因子实验室编辑」按钮，点击后会跳转到因子实验室。但当前这个跳转**只是把工作区切到了空白的「权重训练与组合」builder**（`setFactorLabSection("builder") + setActiveView("factor")`），并不会把这条策略对应的因子组合载入编辑器——也就是说「编辑」名不副实。更关键的是，用户在 builder 里通过「保存组合」落库的 **因子组合（`factor_set`）目前在整个前端没有任何列表 / 查看 / 编辑入口**：`factorApi.sets.list / get / update` 在前端全部未被调用，组合一旦保存便「只进不出」。

因此本方案要补齐两件事：（1）在因子实验室新增一个一级工作区，**命名为「因子组合策略」**，点击后右侧内容区列出用户已经创建过的**所有**因子组合，支持选择、查看（成员、权重、复合 IC、阈值、训练溯源、复合得分曲线）、编辑（载入 builder 改成员 / 重训 / 改阈值后更新同一条组合）、删除，以及「从该组合新建 / 刷新交易策略」（**「上图叠加到主图」已在 v2 评审中移出范围——见评审记录 P0-1**）；（2）打通交易面板的「在因子实验室编辑」：点击后正确跳转到因子实验室并把**当前这条因子组合得分策略对应的组合**载入编辑器进行编辑。

设计遵循仓库既有约定：后端因子组合 CRUD（`GET/POST/PUT/DELETE /api/factor-sets`、`/factor-sets/{id}/score`）已完整实现且久经测试，本方案**不新增任何后端路由、不改表结构**，只在前端补 UI 与跨视图交接（handoff）逻辑，并把「策略 ← 来源组合」的弱关联以一个可选的 `factorSetId` 写进策略内联快照 `params_json`（向后兼容，旧策略缺该字段时安全回落到「按快照重建」分支）。所有纯逻辑抽到 `apps/web/lib` 配套单测，IO 仍走既有 `factorApi`，符合「纯函数 + 边界 IO」分层与归一化契约单源（`resolve_norm_contract`）等既有不变量。

---

## 二、技术设计（Technical Design）

### 2.1 概念澄清与术语

- **因子组合（factor combination / `factor_set`）**：用户在因子实验室 builder 里「选成员 → 训练权重 → 设阈值 → 保存组合」落库的一条记录，含 `members`（自包含快照、方向冻结 long/short）、`method`、`config.thresholds`、`model`（weights / normStats / provenance / trainedAt）、`metrics`（样本内 / 外 IC）。这是本需求所称的「因子组合策略」的**规范载体**。
- **因子组合得分策略（`factor_score` 策略）**：从某条因子组合「新建策略」得到的**可交易**策略，把 `{members, thresholds, normStats, provenance}` **内联快照**进 `params_json`，注册进 `STRATEGY_REGISTRY`，天然复用回测 / 对比 / WF / 实时链路。它出现在交易工作台右侧策略面板「常规策略」页签里。
- 二者的关系：组合是「配方」，得分策略是「按配方做出来的菜」。当前缺失的是「配方册」（列表 / 查看 / 编辑），以及「从菜回到配方」的链接。

### 2.2 架构与数据流

```text
NavRail(因子实验室) ──► FactorLab(常驻挂载, activeView==="factor" 时可见)
                           │  左导航 SECTIONS（新增第 6 项「因子组合策略」key="sets"）
                           ├─ section==="sets"    ► FactorSetList ─┬─ GET /factor-sets（TanStack Query）
                           │                                        ├─ 选中行 ► FactorSetDetail
                           │                                        │     └─ POST /factor-sets/{id}/score（复合得分曲线）
                           │                                        ├─ 编辑 ► setFactorEditTarget({source:"set",setId,snapshot}) + setFactorLabSection("builder")
                           │                                        ├─ 删除 ► DELETE /factor-sets/{id}（二次确认后；删后清 selectedId + invalidate ["factor","sets"]）
                           │                                        └─ 新建/刷新策略 ► POST /strategies(templateKey="factor_score", params.factorSetId=id)
                           └─ section==="builder" ► FactorSetBuilder（扩展为「新建 | 编辑」双模）
                                  └─ 挂载时消费 store.factorEditTarget → 水合 members/method/name/thresholds/trained
                                       ├─ editingSetId!=null ► 「更新组合」PUT /factor-sets/{id}
                                       └─ editingSetId==null ► 「保存组合」POST /factor-sets（保存成功后回填 editingSetId）

交易工作台右侧 StrategyPanel ─► StrategyDetail ─► FactorStrategyBanner（kind==="factor"）
   「在因子实验室编辑」onClick:
       target = buildEditTargetFromStrategy(strategy)
         ├─ strategy.params.factorSetId 存在 ► {source:"set", setId, strategyId, name, snapshot}  // 始终带 snapshot 兜底（P1-1）
         └─ 否则（旧快照）                 ► {source:"snapshot", strategyId, name, snapshot}
       setFactorEditTarget(target); setFactorLabSection("builder"); setActiveView("factor")
```

> **评审 P0-1（上图移出范围）**：原方案的「上图叠加到主图」依赖 store 字段 `factorOverlay`，但该字段在当前仓库**只写不读**（仅 `FactorEvalPanel`/`AgentPanel` 调 `setFactorOverlay`，无任何组件消费它渲染主图叠加）。要真正叠加到主图需新增「主图 因子/得分 pane 渲染消费者」，属独立特性、超出本方案「前端零新增渲染管线」边界，故本 v2 将其从交付物、数据流、文件意图与验收中移除，留作后续工作。详情与得分曲线仍在「因子组合策略」工作区内用 `FactorScoreChart` 自绘 sparkline 呈现（不依赖主图叠加）。

跨视图交接用 **store 瞬态字段 `factorEditTarget`**（不持久化、不入 partialize）承载，builder 挂载后读取并**立即清空**（消费一次性），避免重复水合；编辑态本身由 builder 局部 `editingSetId` 维持，决定主操作是「更新组合」还是「保存组合」。

### 2.3 关键代码路径与操作时序

**流程 A — 从因子实验室「因子组合策略」列表编辑：**
1. 用户在 NavRail 点「因子实验室」→ `activeView="factor"`；在左导航点「因子组合策略」→ `factorLabSection="sets"`。
2. `FactorSetList` 用 `useQuery(["factor","sets"], () => factorApi.sets.list())` 拉全部组合，按 `updatedTs` 倒序渲染卡片；点击某卡 → 局部 `selectedId` → 右侧 / 下方展开 `FactorSetDetail`。
3. `FactorSetDetail` 渲染成员徽标、`WeightBars`（抽为共享组件）、复合 IC、阈值摘要、provenance；并以 `useWorkbench()` 的 `{instId,bar,startTs,endTs}` 调 `factorApi.sets.score(id, range)` 画复合得分 sparkline（复用 `FactorScoreChart`）。
4. 点「编辑」→ `setFactorEditTarget({source:"set", setId})` + `setFactorLabSection("builder")`。
5. `FactorSetBuilder` 挂载，`useEffect([factorEditTarget])` 检测到非空目标后**第一时间**把它捕获到局部 const 并 `setFactorEditTarget(null)`（配 `ref` 防重入，避免加载态重渲染二次水合 / 二次拉取，P2-2），再分支水合：
   - `source==="set"`：`try { set = await factorApi.sets.get(setId)（或复用 list 缓存）}`；成功 → 纯函数 `factorSetToBuilderState(set)` 映射初态（见 2.4）→ `setEditingSetId(setId)`。`catch`（404 / 网络失败）→ 若目标带 `snapshot` 则按快照水合并提示「来源组合已删除，已按策略快照载入」、`editingSetId` 留空；否则提示失败、保持新建态（P1-1）。
6. 用户改成员 / 阈值 / 重训后点「更新组合」→ 与「保存组合」复用同一 `blockReason` 门 → `factorApi.sets.update(editingSetId, {...})` → `invalidateQueries(["factor","sets"])`。

**流程 B — 从交易面板「在因子实验室编辑」：**
1. `FactorStrategyBanner` 的 onClick 调 `buildEditTargetFromStrategy(strategy)`（纯函数）：策略快照含 `factorSetId` → `{source:"set", setId, strategyId, name, snapshot:{members,thresholds,normStats,provenance}}`（**始终带 snapshot 兜底**，使来源组合被删时 builder 能回落快照，P1-1）；否则 → `{source:"snapshot", strategyId, name, snapshot:{members,thresholds,normStats,provenance}}`。
2. `setFactorEditTarget(target)` + `setFactorLabSection("builder")` + `setActiveView("factor")`。
3. builder 挂载消费：`source==="set"` 同流程 A；`source==="snapshot"` 直接用快照水合（`editingSetId` 留空 → 主按钮为「另存为组合」；并额外提供「更新该策略」按钮，调 `api.updateStrategy(strategyId,{params:newSnapshot})` 满足「对当前因子组合策略进行编辑」）。

### 2.4 Builder 双模改造要点（实现细节，必须落实）

- 新增局部状态 `editingSetId: number | null`（默认 null = 新建态）。
- 新增纯函数 `factorSetToBuilderState(set: FactorSet)`（置于 `lib/factor.ts`），返回 `{ members, method, name, thresholds, trained }`：
  - `members = set.members`；`method = set.method`；`name = set.name`。
  - `thresholds = { ...DEFAULT_THRESHOLDS, ...(set.config.thresholds ?? {}) }`。
  - `trained = trainedFromSet(set)`：若 `set.model.weights` 覆盖全部成员（`weightsCoverMembers`）则重建一个 `TrainResult`（`{method, weights, normStats, metrics, available:true, note:"", trainerVersion:null, provenance}`），否则 `null`（用户需重训才能再保存）。
- **H3 重训失效守卫冲突**：现有 `useEffect(() => setTrained(null), [members])` 会在水合时把刚设的 `trained` 立刻清掉。改造：引入 `const skipTrainResetRef = useRef(false)`，水合时把 `members` 与 `trained` 同步 set 并置 `skipTrainResetRef.current = true`；该 effect 内 `if (skipTrainResetRef.current) { skipTrainResetRef.current = false; return; }`，仅跳过这一帧，用户后续手改成员仍照常作废训练。
- 保存区：`editingSetId == null` → 主按钮「保存组合」`sets.create`，成功后 `setEditingSetId(created.id)`（进入该组合编辑态，便于随后「从组合新建策略」带上 `factorSetId`）；`editingSetId != null` → 主按钮「更新组合」`sets.update(editingSetId, {...})`，并保留「另存为新组合」`sets.create`。**「更新组合」与「保存组合」复用同一 `blockReason` 禁用门**（成员未变 → `trainedFromSet` 重建的 `trained` 覆盖成员 → `trainedFresh` 为真 → 可更新；改了成员 → H3 作废训练 → 须重训后才可更新，亦满足后端 `assert_weights_cover`）。无重训的纯改名 / 改阈值更新须**原样回传 `model.provenance` 与 `trainedAt`**，不要用 `Date.now()` 重打戳（P2-1）。
- 「从组合新建策略」打通来源链路（P1-2）：若 `editingSetId == null`（用户尚未「保存组合」），**先自动落库该组合** `sets.create({同保存组合的 name/members/method/modelJson/metricsJson/config}）` 取得 `id` 并 `setEditingSetId(id)`，再以 `params.factorSetId = id` 建策略；若已有 `editingSetId` 直接复用。由此保证**每条 `factor_score` 策略都有可在「因子组合策略」列表中呈现的来源组合（无孤儿）**，且交易面板「在因子实验室编辑」恒走稳健的 `set` 分支。
- 编辑态可见性与一致性（P1-3）：`editingSetId != null` 时 builder 顶部**显著呈现**横幅「正在编辑组合：&lt;name&gt;（保存将更新该组合）」+「新建组合」清空按钮（`setEditingSetId(null)` + 重置 members/thresholds/name/trained，给一条从编辑态回到全新搭建的退路）。**杜绝静默从「更新」回落「新建」**。可选硬化：把 `editingSetId`（及 `name`）提升为 store 瞬态字段，镜像既有 `comboEditingId`（不入 partialize、刷新复位），使用户离开 builder 工作区再返回不丢编辑指针——若不做此硬化，须在「编辑」入口确保 builder 即时挂载并一次性水合，文档已默认采用后者（消费一次性 + 局部态）。

### 2.5 因子实验室左导航与渲染

`FactorLab.tsx` 的 `SECTIONS` 数组在 `builder` 之后追加：
`{ key: "sets", label: "因子组合策略", desc: "查看 / 编辑已建组合" }`，并在内容区 switch 增加 `section === "sets" ? <FactorSetList /> : ...` 分支。`factorLabSection` 已是自由字符串且持久化，新增可选值无需迁移。

---

## 三、文件 / 模块变更计划（File / Module Change Plan）

| 文件 | 类型 | 一句话意图 |
|---|---|---|
| `apps/web/lib/store.ts` | 修改 | 新增瞬态 `factorEditTarget: FactorEditTarget \| null` 与 `setFactorEditTarget`；**不**入 partialize（刷新即清，避免水合到陈旧编辑指针）。 |
| `apps/web/lib/factorTypes.ts` | 修改 | `FactorScoreSnapshotParams` 增加可选 `factorSetId?: number`；新增 `FactorEditTarget` 联合类型（`{source:"set",setId,strategyId?,name?,snapshot?}` \| `{source:"snapshot",strategyId,name,snapshot}`，set 分支带 snapshot 兜底，P1-1）。 |
| `apps/web/lib/factor.ts` | 修改 | 新增纯函数 `buildEditTargetFromStrategy(strategy)`、`factorSetToBuilderState(set)`、`trainedFromSet(set)`、`thresholdsSummary(thresholds)`（均可单测）。 |
| `apps/web/components/Factor/FactorLab.tsx` | 修改 | `SECTIONS` 追加「因子组合策略」(key `sets`)；内容区 switch 增加 `FactorSetList` 分支。 |
| `apps/web/components/Factor/FactorSetList.tsx` | 新建 | 「因子组合策略」列表：拉取全部组合、卡片渲染、选中展开、空态、删除（二次确认 + invalidate + 清 selectedId，P2-4）、编辑 / 新建策略动作分发（**已移除「上图」，见 P0-1**）。 |
| `apps/web/components/Factor/FactorSetDetail.tsx` | 新建 | 选中组合的详情：成员徽标、权重条、复合 IC、阈值摘要、provenance、复合得分 sparkline（`sets.score` → 把 `ScorePoint{ts,score}` 适配为 `LinePoint` 再喂 `FactorScoreChart`，镜像 `FactorEvalPanel`，P2-3）。 |
| `apps/web/components/Factor/WeightBars.tsx` | 新建 | 把 `FactorSetBuilder` 内私有 `WeightBars` 抽成共享组件，供 builder 与 detail 复用（避免重复 / 控文件行数）。 |
| `apps/web/components/Factor/FactorSetBuilder.tsx` | 修改 | 扩展为「新建 \| 编辑」双模：消费 `factorEditTarget` 水合（set 失败回落 snapshot，P1-1）、`editingSetId` + 顶部编辑态横幅 +「新建组合」清空（P1-3）、「更新组合」(`sets.update`，复用 `blockReason` 门)、H3 重训守卫 ref、「从组合新建策略」无 set 时**先自动落库组合**再带 `factorSetId`（P1-2）、引入共享 `WeightBars`。 |
| `apps/web/components/Strategy/StrategyDetail.tsx` | 修改 | `FactorStrategyBanner`「在因子实验室编辑」改为 `setFactorEditTarget(buildEditTargetFromStrategy(strategy))` + 切 section + 切 view。 |
| `apps/web/lib/factor.test.ts` | 修改/新建 | 覆盖 `buildEditTargetFromStrategy`（有/无 factorSetId）、`factorSetToBuilderState`、`trainedFromSet`、`thresholdsSummary`。 |
| `apps/web/components/Factor/FactorSetList.test.tsx` | 新建 | 列表渲染 / 空态 / 选中展开 / 编辑分发写入 `factorEditTarget` + section 的组件测。 |
| `apps/web/components/Strategy/StrategyDetail.test.tsx` | 修改/新建 | 断言点击「在因子实验室编辑」后 store 的 `factorEditTarget`、`factorLabSection`、`activeView` 正确。 |

> 后端（`services/quant/**`）**无任何文件改动**：factor-set CRUD 与 `/score` 已具备；`factor_score` 策略 `prepare/on_candle` 只读 `members/thresholds/normStats/weights`，多出的 `factorSetId` 在 `params_json` 中被透传忽略（与既有 `provenance` 字段同理）。

---

## 四、接口设计（Interface Design）

本方案**不新增后端接口**，全部复用既有 REST（经 `/api/py/*` Next rewrite → FastAPI `/api/*`）。前端 `factorApi`（`apps/web/lib/factorApi.ts`）已封装下列方法，本次仅新增**调用方**：

| 用途 | 方法（前端） | HTTP | 备注 |
|---|---|---|---|
| 列出全部组合 | `factorApi.sets.list(enabled?)` | `GET /factor-sets[?enabled=]` | 返回 `FactorSet[]`，已含 members/model/metrics/config，列表+详情共用，无需逐条 get。 |
| 取单条（编辑兜底） | `factorApi.sets.get(id)` | `GET /factor-sets/{id}` | 404 → `{code:"NOT_FOUND"}`。 |
| 更新组合 | `factorApi.sets.update(id, patch)` | `PUT /factor-sets/{id}` | partial：仅传字段才校验；同传 `modelJson`+`config` 时后端 `assert_norm_contract`，同传 `members`+`modelJson` 时 `assert_weights_cover`。 |
| 删除组合 | `factorApi.sets.remove(id)` | `DELETE /factor-sets/{id}` | 204；404 → NOT_FOUND。 |
| 复合得分曲线 | `factorApi.sets.score(id, range)` | `POST /factor-sets/{id}/score` | body=`FactorRange`；返回 `FactorScoreResult`（points/latest/coverage/note/minBarsRequired）。 |
| 新建得分策略 | `api.createStrategy({templateKey:"factor_score", name, params})` | `POST /strategies` | `params` 在既有 `{members,thresholds,normStats,provenance}` 基础上**追加** `factorSetId?:number`。 |
| 编辑当前策略（snapshot 分支） | `api.updateStrategy(id, {params})` | `PUT /strategies/{id}` | 仅在交易面板「无 factorSetId 的旧策略」场景的「更新该策略」用。 |

**前端新增类型（`factorTypes.ts`）：**

```ts
export interface FactorScoreSnapshotParams {
  members: FactorMember[];
  thresholds: FactorThresholds;
  normStats?: { normWindow: number; clip: number };
  provenance?: TrainProvenance;
  factorSetId?: number;            // 新增：弱关联来源组合（旧快照缺省安全）
}

export type FactorEditTarget =
  // set 分支额外携带 snapshot 兜底（P1-1）：setId 指向的组合若已删（sets.get 404），builder 回落用 snapshot 水合。
  // 从 builder「编辑」入口发起时 strategyId/name/snapshot 可缺省（直接有 set，无需兜底）；从交易面板发起时三者必带。
  | { source: "set"; setId: number; strategyId?: number; name?: string; snapshot?: FactorScoreSnapshotParams }
  | { source: "snapshot"; strategyId: number; name: string; snapshot: FactorScoreSnapshotParams };
```

**Store 新增（`store.ts`，瞬态、不持久化）：**

```ts
factorEditTarget: FactorEditTarget | null;          // 默认 null
setFactorEditTarget: (t: FactorEditTarget | null) => void;  // set({ factorEditTarget: t })
```

---

## 五、数据模型（Data Model）

- **数据库：零变更。** 复用既有 `factor_sets` 表（`db/models.py: FactorSet`：`members_json / method / config_json / model_json / metrics_json / enabled / created_at / updated_at`）。
- **策略 `params_json`（`factor_score`）：** 形态由 `FactorScoreSnapshotParams` 描述，本次仅**追加一个可选标量** `factorSetId`。它不参与打分（`prepare/on_candle` 不读它），仅作 UI 回链。旧策略无此字段 → `buildEditTargetFromStrategy` 走 `snapshot` 分支，行为安全。
- **前端内存形态：**
  - `FactorSet`（list/detail 渲染源，已有类型）；
  - `FactorEditTarget`（跨视图交接载荷，瞬态 store）；
  - builder 局部：`editingSetId:number|null`、`members/method/name/thresholds/trained`（沿用）。
- **归一化契约不变量（务必保持）：** 编辑载入时 `thresholds.normWindow` 必须等于 `model.normStats.normWindow`（保存时即一致）；builder 在 `trainedFresh` 下锁定 normWindow 输入框；「更新组合」同传 `modelJson`+`config` 触发后端 `assert_norm_contract` 复核——保持 `compose_score` 与训练共享同一 `causal_zscore` 窗口/clip（单源真相，见 `resolve_norm_contract`）。

---

## 六、测试与验收标准（Testing & Acceptance）

**单元测试（vitest，纯逻辑优先）：** `apps/web/lib/factor.test.ts`
- `buildEditTargetFromStrategy`：含 `factorSetId` → 返回 `{source:"set"}`；缺省 → `{source:"snapshot"}` 且 `snapshot.members/thresholds` 与策略 params 一致。
- `factorSetToBuilderState`：成员 / 方法 / 名称 / 阈值（带默认回落）正确；`set.config.thresholds` 缺字段时回落 `DEFAULT_THRESHOLDS`。
- `trainedFromSet`：weights 覆盖成员 → 返回 TrainResult 且 `weightsCoverMembers` 为真；weights 缺失 / 不覆盖 → 返回 `null`。
- `thresholdsSummary`：阈值摘要文案稳定。

**组件测试（vitest + RTL，需 `NODE_ENV=test`）：**
- `FactorSetList.test.tsx`：mock `factorApi.sets.list` 返回 2 条 → 渲染 2 卡；空数组 → 空态文案；点击「编辑」→ `useAppStore.getState().factorEditTarget` 满足 `source==="set" && setId===<该卡 id>` 且 `factorLabSection==="builder"`；点击「删除」走二次确认后调用 `sets.remove` 并清 `selectedId`。
- `StrategyDetail.test.tsx`：渲染 `kind==="factor"` 且 `params.factorSetId=7` 的策略 → 点击「在因子实验室编辑」后 `factorEditTarget.source==="set" && factorEditTarget.setId===7 && factorEditTarget.snapshot!=null`（带快照兜底，P1-1）、`factorLabSection==="builder"`、`activeView==="factor"`；无 `factorSetId` 的旧策略 → `source==="snapshot"` 且 `snapshot.members` 等于策略 members。
- `factor.test.ts` 增补：`buildEditTargetFromStrategy` 含 `factorSetId` 时返回的 `set` 目标**仍带 `snapshot`**（P1-1 回归护栏）。

**后端测试：** 无需新增。可选补一条 `services/quant/tests` 回归断言：创建 `factor_score` 策略时 `params` 含 `factorSetId` 能原样回读且不影响回测（验证透传不破坏既有路径）。

**验收标准（Definition of Done，功能级）：**
1. 因子实验室左导航出现「因子组合策略」工作区；点击后右侧列出全部已建组合（无组合时显示空态与去 builder 的引导）。经「从组合新建策略」创建的每条 `factor_score` 策略都有可在此列出的来源组合（无孤儿，P1-2）。
2. 选中某组合可查看成员 / 权重条 / 复合 IC / 阈值 / 训练溯源 / 复合得分曲线（曲线在工作区内 `FactorScoreChart` sparkline 呈现）。
3. 「编辑」能把该组合载入 builder，顶部显示编辑态横幅；改成员 / 重训 / 改阈值后「更新组合」写回同一条（列表即时刷新），且不违反归一化契约（normWindow 锁定一致）；「更新组合」与「保存组合」同受 `blockReason` 门约束（P1-3）。
4. 交易面板因子组合得分策略的「在因子实验室编辑」点击后跳到因子实验室并把**该策略对应的组合**载入编辑器（有 `factorSetId` 走组合；该组合已删 → 回落策略内联快照水合并提示，P1-1；旧策略无 `factorSetId` 走快照重建并可「更新该策略」）。
5. 旧 `factor_score` 策略（无 `factorSetId`）功能不回归：回测 / 对比 / 实时照常，编辑走快照分支不报错。
6. `pnpm --filter web test` 与 `pnpm --filter web typecheck`（或 `next lint`）通过；新文件遵守清洁代码阈值（单文件 ≤800 行、单函数 ≤50 行、嵌套 ≤4 层、形参 ≤5）。

---

## 七、风险与缓解（Risks & Mitigations）

| 风险 | 等级 | 缓解 |
|---|---|---|
| **（评审 P0-1，已就地处置）** 「上图叠加到主图」依赖 `factorOverlay`，但全仓零消费方，按原方案做出的按钮静默无效、验收无法通过。 | P0 | 本 v2 已将「上图」移出范围（数据流 / 文件意图 / 验收均删），标注为依赖「主图 因子/得分 pane 渲染消费者」的独立后续工作；得分曲线改在工作区内 sparkline 呈现，不依赖主图叠加。 |
| H3 重训守卫把水合的 `trained` 立刻清掉，导致编辑态一进来就提示「请先训练」。 | P0 | 用 `skipTrainResetRef` 仅跳过水合那一帧的 `[members]` 重置；配套单测覆盖「载入后 `trainedFresh` 为真」。 |
| **（评审 P1-1，已就地处置）** 风险表原称「回链失效自动回落 snapshot」，但 `set` 目标不带快照、`sets.get` 404 未捕获 → 交易面板编辑直接报错（自相矛盾）。 | P1 | `set` 目标改为始终携带内联快照；builder set 水合 `try/catch`，失败按快照水合并提示。 |
| **（评审 P1-2，已就地处置）** 不「保存组合」直接「从组合新建策略」会产生无来源组合的孤儿 `factor_score` 策略，不入列表，违背「列出所有」。 | P1 | 「从组合新建策略」在 `editingSetId==null` 时先自动落库组合再带 `factorSetId` 建策略，保证无孤儿、编辑恒走 set 分支。 |
| **（评审 P1-3，已就地处置）** `editingSetId` 为 builder 局部态，离开工作区即丢失，静默由「更新」回落「新建」。 | P1 | 顶部编辑态横幅 +「新建组合」清空 +「更新组合」复用 `blockReason` 门；可选把 `editingSetId/name` 提升 store 瞬态镜像 `comboEditingId`。 |
| 旧 `factor_score` 策略无 `factorSetId`，「编辑」找不到来源组合。 | P1 | `buildEditTargetFromStrategy` 回落 `snapshot` 分支，用内联快照水合 builder；提供「另存为组合 / 更新该策略」两条出路，不阻塞。 |
| `factorSetId` 写进 `params_json` 被后端校验剥离 / 拒绝。 | P1 | 已实测确认：`validate_factor_strategy_params` 仅校验 `params.members`、不剥离其余字段，`provenance/normStats` 已在透传，新增标量同理。可选后端回归测试兜底。 |
| 更新组合违反归一化契约（normWindow 与 normStats 不一致）触发 400。 | P1 | 载入即令 `thresholds.normWindow=model.normStats.normWindow`，`trainedFresh` 下锁定该输入框；改窗口强制重训后再更新（沿用 builder 既有 H1/H2 机制）。 |
| `factorEditTarget` 若误入 persist，刷新后水合到已删组合。 | P1 | 明确**不**入 partialize（瞬态）；builder 消费时第一时间 `setFactorEditTarget(null)` + `ref` 防重入（P2-2）。 |
| 无重训直接「更新组合」误用 `Date.now()` 重打戳 / `set.metrics` 为 null 致 `trained` 重建失败。 | P2 | 纯改名 / 改阈值更新原样回传 `model.provenance/trainedAt`；`trainedFromSet` 在 `metrics` 缺失时回落零值 `TrainMetrics`（P2-1）。 |
| `sets.score` 区间过短（`window_too_short` / `minBarsRequired`）导致曲线空。 | P2 | detail 读取 `FactorScoreResult.note/minBarsRequired` 显式提示「区间过短，至少需 N 根」，不静默空白。 |
| 删除正被某 `factor_score` 策略引用的组合 → 策略仍可跑（内联快照自包含），但「编辑」回链失效。 | P2 | 删除前二次确认 + 删后清 `selectedId`；因策略快照自包含，删除不影响其回测 / 实时；编辑回链失效时由 set 目标携带的内联快照自动回落（P1-1）。 |
| 文件行数膨胀（builder 已较长）。 | P2 | 抽 `WeightBars` 共享组件、纯函数下沉 `lib/factor.ts`，list/detail 拆两个文件，控住单文件 ≤800 行。 |

---

## 八、实现顺序建议（供 Subtask #2）

1. `factorTypes.ts` 增类型（`FactorEditTarget` 含 set 分支 snapshot 兜底）→ `factor.ts` 增纯函数 + 单测（先红后绿，TDD；含 P1-1 回归护栏）。
2. `store.ts` 增 `factorEditTarget`（瞬态、不入 partialize）。
3. 抽 `WeightBars.tsx`；新建 `FactorSetDetail.tsx`（ScorePoint→LinePoint 适配）、`FactorSetList.tsx`（删除二次确认 + 清 selectedId）。
4. `FactorLab.tsx` 接入新 section。
5. 改造 `FactorSetBuilder.tsx` 双模（H3 ref 守卫 + 编辑态横幅 + 「新建组合」清空 + 「更新组合」复用 `blockReason` 门 + 「从组合新建策略」无 set 时先自动落库组合，P1-2/P1-3）。
6. 改 `StrategyDetail.tsx` 的 banner 跳转（`buildEditTargetFromStrategy` 始终带 snapshot 兜底）。
7. 补组件测；跑 `NODE_ENV=test pnpm --filter web test` 与 typecheck。

> 本文档 v1 为 Subtask #0 方案设计；**v2 由 Subtask #1 评审并就地修复 P0/P1**（见顶部「评审记录」与文末「评审结论」）。仍仅描述实现、不含实现代码，交 Subtask #2 落码。

---

## 实施过程发现的方案缺陷（Issues Found During Implementation · Subtask #2）

> 落码时严格按 v2「文件 / 模块变更计划」执行。以下为实现中发现方案**未明确或与实测不符**之处，已就地采用更正后做法并记录，未静默偏离。

| # | 位置 | 发现 | 更正做法 |
|---|---|---|---|
| I-1 | §2.3 流程B / §3 factor.ts | snapshot 分支「直接用快照水合」缺重建细节：`FactorScoreSnapshotParams` **不含 `method`、也无顶层 `weights` 表**（权重已烘焙进各成员 `weight`），若不重建 `trained` 则旧策略快照分支会被 `blockReason` 永久卡在「请先训练」，无法满足 DoD #4「快照重建并可『更新该策略』」。 | 在 `lib/factor.ts` **新增第 5 个纯函数** `trainedFromSnapshot(snapshot, method)`：按 `memberNames` 反推权重表重建 `TrainResult`（`method` 由 builder 当前下拉透传，快照不含），`metrics` 缺失回落零值（同 P2-1）。已配 `factor.test.ts` 覆盖。方案表原只列 4 个纯函数，此为必要补充。 |
| I-2 | §三 文件清单脚注 / §四 | 脚注称「`factorApi`（`apps/web/lib/factorApi.ts`）本次仅新增**调用方**、无改动」，但 `sets.create` 的 `metricsJson` 类型为 `TrainMetrics \| null`、`sets.update` 为 `Record<string, unknown> \| null`，二者不一致；编辑态「更新组合」复用同一 `setBody()`（含 `metricsJson: trained?.metrics`）传给 `update` 时 **TS 编译失败**（`TrainMetrics` 缺索引签名，不可赋给 `Record<string,unknown>`）。 | 对 `factorApi.sets.update` 的 `metricsJson` 做**纯类型放宽**：`TrainMetrics \| Record<string, unknown> \| null`。不改运行时行为、向后兼容既有 `Record` 入参；实测 `sets.update` 在本仓库此前**无任何调用方**（本特性是首个），故零回归风险。`factorApi.ts` 因此进入本次改动清单（仅类型，一行）。 |
| I-3 | §3 FactorSetDetail / P2-3 | P2-3 要求「镜像 `FactorEvalPanel` 的 `factorPointsToLine` 把 `ScorePoint` 适配为 `LinePoint`」。实测 `lib/factorSeries.ts` **已导出现成的 `scorePointsToLine(ScorePoint[])`**。 | `FactorSetDetail` 直接复用 `scorePointsToLine`，无需新增适配器（方案此处略保守，非缺陷，记录以备 review 对照）。 |

> 以上更正均不扩大需求范围、不引入新依赖；P0-1（不引入无消费方的「上图」）与四项 P0/P1 修复、五项 P2 注意项均已在代码中兑现。`tsc --noEmit` 通过；`pnpm --filter web test`（vitest run，含 `NODE_ENV=test`）全量 662 测试通过（含本特性新增 lib 用例与 `FactorSetList.test.tsx` / `StrategyDetail.test.tsx`）。

---

## 评审结论（Review Verdict · Subtask #1）

**结论：有条件通过（Approved with Conditions）。**

方案的技术基础经对 `master` 实测代码逐项核对**基本属实**——后端 `factor-sets` CRUD + `/score` 齐全、`factorSetId` 可经 `params_json` 透传（`validate_factor_strategy_params` 仅校验 `members`）、H3 重训守卫思路成立、builder 私有 `WeightBars` 与 `setTrained([members])` 重置如实存在、`StrategyDetail` 跳转「编辑名不副实」属实、store 持久化口径正确。方案达成需求两大目标（「因子组合策略」工作区 + 交易面板编辑回链），且坚持「后端零改动、前端零新增渲染管线」的右尺度边界，未过度设计。

评审中发现的 **1 个 P0 + 3 个 P1 已在本 v2 正文就地修复**：

- **P0-1**：「上图叠加到主图」依赖只写不读的 `factorOverlay` → 已移出范围（数据流 / 文件意图 / 验收均删），得分曲线改在工作区内 sparkline 呈现。
- **P1-1**：`set` 编辑目标不带快照却宣称可回落 snapshot 的自相矛盾 → 目标始终携带内联快照，builder set 水合 `try/catch` 失败回落。
- **P1-2**：孤儿 `factor_score` 策略不入列表违背「列出所有」→ 「从组合新建策略」无 set 时先自动落库组合再链。
- **P1-3**：`editingSetId` 局部态致「更新/新建」静默歧义 → 顶部编辑态横幅 +「新建组合」清空 + 共用 `blockReason` 门。

**放行条件（Subtask #2 落码时必须满足）：**

1. 兑现上述 4 项 P0/P1 修复的代码实现，不得退回原 v1 形态（尤其不得再引入无消费方的「上图」按钮）。
2. 落实 5 项 P2 注意项：P2-1（更新保留 provenance/trainedAt、metrics 缺失回落）、P2-2（`factorEditTarget` 即时清空 + ref 防重入）、P2-3（ScorePoint→LinePoint 适配）、P2-4（删除二次确认 + invalidate + 清 selectedId）、P2-5（清洁阈值）。
3. 测试须覆盖：P1-1 回归（set 目标带 snapshot）、H3 载入后 `trainedFresh` 为真、列表空态 / 编辑分发 / 删除路径、交易面板两分支跳转；`NODE_ENV=test pnpm --filter web test` 与 typecheck 全绿。
4. 若选择「P1-3 可选硬化」（把 `editingSetId/name` 提升 store 瞬态），须镜像 `comboEditingId` 不入 partialize；若不选，则须确保「编辑」入口即时挂载并一次性水合（本 v2 默认采用后者）。

满足以上条件即视为通过；无新增 P0/P1 遗留。
