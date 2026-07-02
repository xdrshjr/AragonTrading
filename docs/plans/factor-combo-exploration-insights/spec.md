# 因子组合探索 · 洞察与引导精修（Factor Combination Exploration — Insights & Guided Refinement）· spec v2

> 特性目录：`docs/plans/factor-combo-exploration-insights/`
> 归属节点：AgentMesh execution `tge-ae4468d0` · **Iteration 2/2** · Subtask #0（方案设计）
> 面向读者：下游实现工程师（评审 → 开发 → 提交）。本文档细化到「逐行可落地」，不再需要二次决策。
> 关联既有特性（已上线）：[factor-combo-exploration](../factor-combo-exploration/)（本迭代的**基座**，v2 已实现并提交 commit `31ddcc9`）、[weight-multi-model-training](../weight-multi-model-training/)、[weight-model-workspace](../weight-model-workspace/)、[factor-lab-symbol-groups](../factor-lab-symbol-groups/)。

---

## 评审记录（Review Notes）· Subtask #1（方案评审）· Iteration 2/2 · 2026-07-02

> 评审方法：逐节对照四维度（可行性 / 完整性 / 一致性 / 右尺寸），并**逐条把设计里引用的既有符号、类型、状态回落到源码验证**（下方结论均带 `文件:行` 证据）。已核实且成立的关键前提：`CandidateState`（`weightBatchTypes.ts:137-148`，含 `variantLabel/method/metrics/result/status`）、`TrainMetrics.icIn/icOut`（`factorTypes.ts:142-149`）、`TrainResultEvent.weights`（`weightModelTypes.ts:133-135`）、`WeightRunStatus` 含 `"succeeded"`（`weightModelTypes.ts:15`）、`memberIdentity`（`factor.ts:240`）/`formatIc`（`factor.ts:48`）、`manual` 策略与 `planCombos/planLocalCombos/estimateComboCount/manualIndexCombos`（`weightExplore.ts:9,145,183,198,212`）、`MAX_EXPLORE_CANDIDATES/MIN/POOL=12/2/12`（`weightExplore.ts:13-16`）、弹窗 `[batchId]` 键控 WS 生命周期与 `launch.onSuccess` 重置（`WeightExploreModal.tsx:106-121,144-149`）。整体「零后端 / 零 DB / 零 API / 零 WS」的右尺寸判断**成立**，等价性门禁与单写点不变量确实被隔离在本迭代之外。以下为发现的问题与处置。

**P0-1 · 洞察面板的数据装配契约不足，阻塞头号能力 A 的渲染。**
问题：§4.3 仅为 `ExploreResultPanel` 新增 `onRefine?`，但要在完成态渲染 `ExploreInsightsPanel` 必须调用 `computeInsights(states, localCombos)`；而 `ExploreResultPanel` 现有 props 为 `matrix/states/ranking/bestId/onApplyBest/onDelete/deleting`（`ExploreResultPanel.tsx:18-34`），**既无 `localCombos`，也无因子中文名映射 `labelOf`**（`labelOf` 只在弹窗里，`WeightExploreModal.tsx:73`）。按原文字，`ExploreInsightsPanel` 的输入无从获得，连 P0 能力 A 都无法落地。
处置（已在 §2.2 接入点 / §2.4 / §三 / §4.3 修复）：由**弹窗外壳**统一 `computeInsights`（它持有 `localCombos`，`WeightExploreModal.tsx:62`），以 `useMemo` 产出 `insights`，把 `insights` + `labelOf` + `onRefine` 作为新 props 下传 `ExploreResultPanel`（P2 运行态同理下传 `ExploreRunningPanel`）；`ExploreInsightsPanel` props 明确为 `{ insights: ExploreInsights; labelOf: (m: FactorMember) => string }`，保持纯展示、不再自持数据。

**P1-1 · 精修需要「上一轮因子池」，但弹窗从未留存它，阻塞能力 C。**
问题：`suggestRefinement(insights, pool, bestComboMembers, bestMethod, cap)` 的 `pool: FactorMember[]` 是硬依赖——它既用来产出 `combos: number[][]`（pool 内下标），又原样成为下一轮 `config.pool`（下标口径必须与该 pool 一致）。但 `launch.onSuccess` 只留存 `localCombos/plan/batchId`（`WeightExploreModal.tsx:144-148`），**从不保存 `config.pool`**；§2.4 原本新增状态也只列 `round/rounds`。缺 pool 则无法产出合法下标，也无法组装下一轮请求。
处置（已在 §2.4 / §5.2 / §三 修复）：新增 `lastConfig` 瞬态，在 `launch.onSuccess` 里 `setLastConfig(config)`（`launch` 的 `mutationFn` 已持有 `config`，可一并回传，`WeightExploreModal.tsx:133`）；精修以 `lastConfig.pool` 作 `suggestRefinement` 的 `pool`、以 `lastConfig` 派生 `bestMethod` 兜底。

**P1-2 · 手动组合下标随因子池编辑而「静默失真」。**
问题：`ManualComboBuilder` 产出 `number[][]`（pool 下标），而 `ExploreConfigPanel` 的 `PoolEditor` 可随时增删池成员（`ExploreConfigPanel.tsx:90,272-276`）。删除一个**低位**因子会使既有下标整体下移、指向**另一个**因子；若下移后仍在界内则不会抛错，用户所见组合与实际训练组合不一致（静默错训），违反本项目「绝不静默」约束（越界情形会被 `manualIndexCombos` 抛错→`safeCount=0`→阻断，属 loud，可接受；界内位移才是真实静默面）。
处置（已在 §2.3 / §七 修复）：`ManualComboBuilder` 内部以 `memberIdentity`（`factor.ts:240`）为选择键、每次渲染对**当前** `pool` 重解下标；池变更导致成员消失时**显式提示并剔除**（「已因因子池变更移除 N 项」），绝不静默保留失真下标。

**P2 级（不阻断，已在正文就近澄清/备注，供开发采纳）：**
- **P2-1 · manual 是「新增第三分支」而非「替代 usesSizes 分支」**：策略区现为 `usesSizes(sizes/topk_prefix)→SizePicker` 与 `else(leave_one_out)→静态文案` 的二分（`ExploreConfigPanel.tsx:113-119`），且 `params = usesSizes ? {sizes} : {}`（`:74`）。接入 manual 须扩为三分支并令 `params` 增 `strategy==="manual" ? {combos} : …` 一支，否则会误改 leave_one_out 文案或把 manual 的 params 发成 `{}`（→`planCombos` 抛错→永久阻断）。§2.3 措辞已澄清。
- **P2-2 · 精修起步相位闪烁**：`phase = batchId==null ? "config" : …`（`WeightExploreModal.tsx:183`）。`startRound` 先 `setBatchId(null)` 会在 launch pending 期间把相位打回 `"config"`（重挂配置态、`initialPool` 回退为原始 `ctx.members`），与「回到运行态」叙事相悖。§2.4 已备注：以 `refining` 标志维持启动/运行视觉，失败再回落 config。
- **P2-3 · 精修候选数下限**：refine 直接 `launch.mutate` 绕过配置态 `tooFew` 守门；若去重后 `combos×methods < MIN_EXPLORE_CANDIDATES(2)` 会命中服务端 400 并回落 config。§2.4/§六 已备注：`suggestRefinement` 须保证 ≥2 候选，否则精修按钮禁用并 `title` 说明。
- **P2-4 · icOut 取值一致性**：`computeInsights` 取样本外 IC 应复用 `candidateIcOut(state)`（`weightBatch.ts:84`，含 `Number.isFinite` 守卫且与排行榜「最佳」同源），而非裸读 `metrics.icOut`，避免归因「前四分位」与展示「最佳」口径漂移。§2.2 已备注。
- **P2-5 · 权重键口径提醒（非缺陷）**：后端权重键来自 `member_names`＝`name||factorKey` 且重复 `factorKey` 追加 `#k`（`scoring.py:84-93`）；`FactorMember` 无 `name` 字段（`factorTypes.ts:134-139`），故默认 `weightKeyOf=m.factorKey` 对「互异 factorKey」正确，仅「同 factorKey 不同 params」的成员会被编号为 `factorKey#2` 而错配/缺失——现设计已「缺键跳过、权重仅作辅助」优雅降级，此处仅记录，不阻断。

---

## 背景与迭代定位（Iteration Context —— 为何是「本增量」）

**上一迭代（Iteration 1）已交付**「因子组合探索训练」完整闭环并合入主干（commit `31ddcc9`，34 文件）：组合成员面板标题右侧的`探索训练`按钮 → 三阶段探索弹窗（配置 / 运行 / 完成）→ 组合×方法笛卡尔候选集 → 复用 `WeightBatchRuntime` 并行训练 → 组合×方法矩阵热力图 + 实时排行榜 → 「应用最佳组合」回流为全新 builder 草稿。后端 `weight_explore_planner`（`sizes`/`topk_prefix`/`leave_one_out`/`manual` 四策略）与 `weight_explore_service`、按候选成员的运行时一般化、迁移补列、`POST /weight-models/explorations` 端点均已落地并通过等价性硬门禁。

**遗留的能力缺口**（Iteration 1 的 spec 明列于「非目标 / v1.1」，正是本迭代要补齐的价值点）：

1. **「挖掘」只停在组合粒度，未下沉到因子粒度**。用户看到的是「哪个组合×方法赢了」，但看不到「**是哪些因子在驱动好表现、哪些因子在拖后腿**」——而后者才是用户原始诉求「挖掘哪些因子组合会产生好表现」的最深回答。矩阵热力图告诉你**结果**，不告诉你**归因**。
2. **手动组合构建器（`manual` 策略）后端已实现、前端未开放**。Iteration 1 §八明确把 `manual` UI 记为 v1.1，前端配置态仅呈现三种自动策略。
3. **探索是「一锤子」，不能据结果迭代收敛**。真正的「挖掘」是一个**看清 → 精修 → 再看清**的循环；当前每次探索彼此孤立，用户需手工重开弹窗、重填池与策略。

**本迭代（Iteration 2）的定位**：在**不触碰任何后端 / 数据库 / 训练引擎 / 等价性门禁 / `setWeightTrainResult` 单写点**的前提下，纯前端叠加三项能力，把探索从「一次性对比」升级为「**可归因、可精修的挖掘循环**」。关键设计洞察：**每个成功候选在前端已持有归因所需的全部数据**——`variantLabel`（=组合 id，可经弹窗持有的 `localCombos` 映射到成员）、`method`、`metrics.icOut/icIn`、`result.weights`。因此因子级归因是一次纯前端聚合，零后端往返、零迁移、零等价性风险。

---

## 一、概述（Overview）

本特性为已上线的「因子组合探索训练」新增**探索洞察（Exploration Insights）**与**引导精修（Guided Refinement）**两大能力，并补齐**手动组合构建器**。核心目标是把用户从「看到哪个组合赢了」带到「**看懂为什么赢、并据此一键收敛到更优组合**」。

**能力 A · 因子贡献洞察**：在探索完成态（及部分运行态），对全部成功候选做一次纯前端聚合，为因子池中每个因子算出可解释的贡献指标——**边际 IC 提升**（含该因子的候选平均样本外 IC − 不含该因子的候选平均样本外 IC，正=有帮助、负=拖后腿）、**登顶命中率**（在样本外 IC 前四分位候选中出现的比例）、**平均学习权重**（模型对它的依赖强度）、**出场次数**。这些指标以「因子贡献榜」呈现：一条**发散水平条形图**（绿=帮助 `var(--long)`、红=拖累 `var(--short)`）按边际提升排序，配「组合大小 × 平均样本外 IC」迷你趋势（判断「加因子是变好还是过拟合」）。这直接回答用户原始诉求里最深的一层——「哪些因子组合会产生好表现」。

**能力 B · 手动组合构建器**：在探索配置态开放第四种策略「手动指定」，用一个交互式网格让用户亲手勾选具体组合参与对比。后端 `weight_explore_planner._manual_index_combos` 与前端 `planCombos` 的 `manual` 分支**均已实现**，本能力仅补前端配置 UI，向 `params.combos`（池内下标数组）写值即可，零后端改动。

**能力 C · 引导精修轮次**：在完成态据洞察一键生成「下一轮」聚焦配置（如「最佳组合 ± 单因子」「按边际提升的前缀嵌套」），把弹窗从完成态重置回运行态再跑一轮——**每一轮仍是一次普通的 `POST /weight-models/explorations`**，完整复用既有 WS / reducer / 矩阵 / 等价性管线，无任何新后端编排。弹窗顶部以轻量面包屑记录各轮最佳（非持久化），让用户直观看到挖掘的收敛轨迹。

三项能力共用同一叙事——**洞察驱动精修**：A 产出因子级归因，C 消费归因生成下一轮，B 提供手动兜底与 C 的落地机制。全部为纯前端、纯加法改动，严格继承 Iteration 1 确立的一切硬约束。

---

## 二、技术设计（Technical Design）

### 2.1 架构总览与设计约束

```
                       ┌─────────────────────────────────────────────────────────┐
                       │  WeightExploreModal（既有弹窗外壳，本迭代内部增强）        │
现有：配置→运行→完成    │                                                          │
                       │  ┌ 配置态 ExploreConfigPanel ─────────────┐              │
本迭代 B：+手动策略 ───▶│  │  策略分段控件 +「手动指定」→ ManualComboBuilder(新)   │  │
                       │  └──────────────────────────────────────┘              │
                       │  ┌ 运行/完成态 Explore(Running/Result)Panel ┐            │
本迭代 A：+洞察面板 ───▶│  │  best 卡 + [ExploreInsightsPanel(新)] + 矩阵 + 排行榜  │  │
                       │  └──────────────────────────────────────┘              │
本迭代 C：+精修按钮 ───▶│  完成态「据洞察精修探索（下一轮）」→ onRefine(cfg)         │
                       │  轮次面包屑（rounds[]）+ startRound(cfg) 重置并再启动     │
                       └──────────────────────────────────────────────────────────┘
                            ▲ 复用（零改动）：weightExploreApi.start / weightBatchWS /
                              weightBatch.ts reducers / buildTrainResult 单写点 / 等价性门禁
数据来源（纯前端，零后端往返）：
  succeeded CandidateState → { variantLabel(组合id), method, metrics.icOut/icIn, result.weights }
  组合 id → localCombos[].members（弹窗已持有）→ memberKeys
  ⇒ exploreInsights.ts 纯聚合 → FactorContribution[] / SizeBucket[] / RefinementSuggestion
```

**设计约束（继承 Iteration 1，本迭代绝不违反）**：

- **C-1 等价性门禁不可降级**：本迭代**不改** `engine/factors/training.py`、不改 `weight_batch_runtime.py`、不改 `weight_explore_service.py`。精修轮次只是再发一次同款请求，候选 ≡ 独立 `train` 的字节级等价性天然继承。
- **C-4 单写点不变量（G-F1）**：`setWeightTrainResult` 全仓唯一写入点仍在 `buildTrainResult`→消费 effect 内；本迭代**不新增任何 `setWeightTrainResult` 调用点**。「应用最佳组合」沿用既有 `applyBest`（`modelId=null` 全新草稿，P0-1）。精修**不写** builder（只发起新探索批次）。
- **零后端 / 零 DB / 零 API / 零 WS 契约变更**：见 §四、§五的显式声明与理由。
- **C-6 尺寸阈值**：新增 UI 一律独立成文件（`ExploreInsightsPanel.tsx` / `ManualComboBuilder.tsx`），纯逻辑归 `lib/exploreInsights.ts`；单文件守 CLAUDE.md 的 **800 行 / 单函数 50 行 / 参数 ≤5 / 嵌套 ≤4**。

### 2.2 能力 A —— 因子贡献洞察（`lib/exploreInsights.ts`，纯函数、可单测）

**数据来源（全部客户端已有，见 §一洞察）**：完成态 `states: CandidateState[]` 中 `status==="succeeded"` 者 + 弹窗持有的 `localCombos: LocalCombo[]`（`id`↔`members`）。归因**只对成功候选**统计（失败/取消不计入，避免样本污染）。

```ts
// 单因子贡献（按 memberIdentity 聚合；展示名由调用方 labelOf 映射）。
export interface FactorContribution {
  factorKey: string;
  identity: string;              // memberIdentity(m)：factorKey|规范化params|direction（跨组合稳定聚合键）
  appearCount: number;           // 含该因子的成功候选数
  absentCount: number;           // 不含该因子的成功候选数
  meanIcWith: number | null;     // 含该因子候选的平均 icOut（appearCount>0 才有值）
  meanIcWithout: number | null;  // 不含该因子候选的平均 icOut（absentCount>0 才有值）
  marginalLift: number | null;   // meanIcWith - meanIcWithout；两侧任一为空 → null（不伪造 0）
  bestIc: number | null;         // 含该因子候选的最优 icOut
  topHitRate: number | null;     // 含该因子候选落入「样本外 IC 前四分位」的比例（分母=含该因子候选数）
  meanAbsWeight: number | null;  // 含该因子候选中该因子学习权重 |w| 的均值（result.weights[weightKey]）
}

// 组合大小分桶（判断「加因子变好还是过拟合」）。
export interface SizeBucket { size: number; count: number; meanIc: number | null; bestIc: number | null; }

// 一次探索的完整洞察。
export interface ExploreInsights {
  factors: FactorContribution[]; // 按 marginalLift 降序（null 殿后，tie 用 bestIc→appearCount→identity）
  sizes: SizeBucket[];           // 按 size 升序
  succeededCount: number;        // 参与统计的成功候选数
  totalCount: number;
  hasEnoughSamples: boolean;     // succeededCount >= 2 且存在「含/不含」对比的因子，才认为归因有意义
}

// 主入口：纯聚合，零 IO。weightKeyOf 把成员映射到 result.weights 的键（默认 m.factorKey，容忍缺失）。
export function computeInsights(
  states: CandidateState[],
  combos: LocalCombo[],
  opts?: { weightKeyOf?: (m: FactorMember) => string },
): ExploreInsights;
```

**计算细节（下游逐条实现）**：

- **样本外 IC 取值口径（P2-4）**：一律经 `candidateIcOut(state)`（`weightBatch.ts:84`，内部读 `state.metrics?.icOut` 且带 `Number.isFinite` 守卫）取值，**不**裸读 `metrics.icOut`——确保归因用的 IC 与排行榜 `rankCandidates`/`bestRunId`「最佳」判定完全同源，`null`/`NaN` 一致地被排除出统计。
- **组合 → 因子集合**：以 `combos.find(c => c.id === state.variantLabel)` 定位候选组合，取其 `members` 的 `memberIdentity` 集合。同一 `variantLabel` 的多方法候选各计一次（一个组合被 N 个方法训练即贡献 N 个样本；这是有意的——方法鲁棒性也是信号）。
- **`marginalLift`**：仅当因子既有「含它的成功候选」又有「不含它的成功候选」时才有值；若因子出现在**所有**候选（如全池恒在的因子）或**没有**成功候选，则 `marginalLift=null`，UI 显示「始终包含 / 样本不足」而非误导性的 0。
- **`topHitRate` 的前四分位**：对所有成功候选 `icOut` 取 75 分位阈值 `q75`（线性插值，池 <4 个成功候选时退化为「≥中位数」，并在 UI 标注「样本较少」）；`topHitRate = |含该因子且 icOut≥q75| / appearCount`。
- **`meanAbsWeight`**：`state.result.weights[weightKeyOf(member)]` 取绝对值求均值；键缺失（等权/IC 加权等无逐因子权重的方法）跳过该样本，全缺失 → `null`。
- **`sizes` 分桶**：按 `combo.members.length` 分组，桶内成功候选 `icOut` 求均值/最优。
- **确定性**：全程无 `Date.now()`/随机；排序 comparator 完全确定（便于快照测试）。

**可视化（`components/Factor/ExploreInsightsPanel.tsx`，新建）**：

- **因子贡献榜**（签名可视化）：每因子一行——左列因子 cn 名 + 出场 `appearCount/总组合` 徽标；中列**发散水平条**（零居中，正向右 `var(--long)`、负向左 `var(--short)`，长度按 `|marginalLift|/maxAbsLift` 归一，`marginalLift=null` 画中性虚线占位 + 「始终包含」Tag）；右列 `IC 提升` 数值（`formatIc`）+ 「登顶 {topHitRate}」小徽标 + 平均权重 `w̄`。行按 `marginalLift` 降序，顶部因子=「最该保留」，底部负值=「建议剔除」。
- **组合大小趋势**：一条紧凑折线 / 分档条「2 因子 IC x̄ → 3 因子 → …」，帮助判断收益随组合增大是升是降。
- **空/弱样本态**：`!hasEnoughSamples` 时渲染友好提示「成功候选不足以做因子归因（至少需 2 个成员不同的成功候选）」，不画误导图。
- 全部使用既有 `var(--*)` 令牌、`card` 约定、`formatIc`；不引入新依赖、不新增图表库（条形用 `div` 宽度百分比，与 `WeightBars` / 矩阵 `color-mix` 同范式）。

**接入点与数据装配（P0-1 修复）**：`computeInsights` 由**弹窗外壳 `WeightExploreModal` 统一计算**——只有它同时持有 `states`（`WeightExploreModal.tsx:160`）与 `localCombos`（`:62`）以及因子中文名映射 `labelOf`（`:73`）；`ExploreResultPanel` / `ExploreRunningPanel` 现有 props（`ExploreResultPanel.tsx:18-34`）既不含 `localCombos` 也不含 `labelOf`，若在子面板内计算则无数据可用。因此：

- 弹窗内新增 `const insights = useMemo(() => computeInsights(states, localCombos, { weightKeyOf }), [states, localCombos])`（`weightKeyOf` 缺省即 `m.factorKey`，见 P2-5）。
- 完成态：`<ExploreResultPanel … insights={insights} labelOf={labelOf} onRefine={…} />`；`ExploreResultPanel` 在「最佳组合摘要卡」与「矩阵」之间插入一段**可折叠** `ExploreInsightsPanel`（默认展开）。
- `ExploreInsightsPanel` 保持**纯展示**，props 固定为 `{ insights: ExploreInsights; labelOf: (m: FactorMember) => string }`——不自持 `states/localCombos`，杜绝与外壳「最佳」口径漂移。
- 运行态 `ExploreRunningPanel` 为**可选（P2）**：弹窗把同一 `insights` 下传，当成功候选数 ≥ `ceil(total/2)` 时以「实时（部分）」标签显示，否则隐藏——避免早期少样本的噪声归因。

### 2.3 能力 B —— 手动组合构建器（`components/Factor/ManualComboBuilder.tsx`，新建）

在 `ExploreConfigPanel` 的 `STRATEGIES` 增第四项 `{ value: "manual", label: "手动指定", hint: "亲手勾选要对比的具体组合" }`。

**接线细节（P2-1 澄清）**：`ExploreConfigPanel` 的策略区当前是「`usesSizes(sizes/topk_prefix)→SizePicker` vs `else(leave_one_out)→静态文案」二分（`ExploreConfigPanel.tsx:113-119`），`params = usesSizes ? { sizes } : {}`（`:74`）。接入 manual 是**新增第三分支**而非「替代 usesSizes 分支」：策略区改为三分支（`usesSizes→SizePicker` / `manual→ManualComboBuilder` / `leave_one_out→静态文案`），且 `params` 改为 `strategy === "manual" ? { combos } : usesSizes ? { sizes } : {}`。切勿把 manual 的 `params` 漏成 `{}`——那会让 `planCombos("manual", {})` 抛错 → `safeCount=0` → 启动被永久阻断。

`ManualComboBuilder`（受控组件，props：`pool: FactorMember[]`、`labelOf`、`value: number[][]`、`onChange`）：

- **下标随池编辑的失真防护（P1-2，硬性要求）**：组件**内部以 `memberIdentity`（`factor.ts:240`）为选择键**记录用户勾选，每次渲染对**当前** `pool` 重新解析为下标后再经 `onChange` 输出 `number[][]`；当 `pool` 因 `PoolEditor` 增删而使某个已选成员消失时，**显式提示并剔除**（如「已因因子池变更移除 N 项」），绝不静默保留会指向别的因子的失真下标。这样即便用户先建组合再改池，所见即所训、无静默错训。
- 「组合行」列表：每行是一组「因子筹码（chip）」，点击池中因子 chip 在当前行内加入/移除；行首显示 `组合 N · k 因子`，行尾「删除本行」。
- 「+ 新增组合」按钮追加空行；空行 / 与已有行成员集合重复的行给出行内提示（重复行由后端/前端 `planCombos` 去重，但 UI 提前提示以免用户困惑）。
- 底部沿用 `ExploreConfigPanel` 既有 `estimateComboCount`/`CandidatePreview`（`manual` 分支 `planCombos` 已支持）——候选数超上限 / 不足下限的显式阻断与三种自动策略一致，零新增守门逻辑。
- **不修改** `lib/weightExplore.ts` 的 `planCombos`/`estimateComboCount`/`manualIndexCombos`（`manual` 已实现），也**不修改**后端 planner。`WeightExploreModal.launch` 的 `planLocalCombos(config.pool, "manual", { combos })` 天然产出 `C0/C1…` 本地组合，矩阵/回流/精修全链路无差别复用。

### 2.4 能力 C —— 引导精修轮次（`exploreInsights.ts` + `WeightExploreModal` 状态）

**纯函数建议器（`lib/exploreInsights.ts`）**：

```ts
export interface RefinementSuggestion {
  strategy: "manual";           // 精修一律落为 manual 组合（确定、可解释、无隐式枚举爆炸）
  combos: number[][];           // pool 内下标数组（已去重、已按 cap 截断，附截断标注）
  methods: TrainMethod[];       // 默认=上轮最佳候选的方法（单方法，聚焦；用户仍可在配置态改）
  pool: FactorMember[];         // 精修池=上轮池（保持下标口径一致）
  rationale: string[];          // 每个建议组合的一句话理由（UI 展示「为何试它」）
  truncated: boolean;           // 因 cap 截断时为 true（UI 明示，绝不静默丢弃）
}

// 据洞察 + 上轮最佳组合，生成下一轮聚焦候选（确定性、纯函数）。
export function suggestRefinement(
  insights: ExploreInsights,
  pool: FactorMember[],
  bestComboMembers: FactorMember[],
  bestMethod: TrainMethod,
  cap?: number,                 // 默认 MAX_EXPLORE_CANDIDATES；combos×methods 不超 cap
): RefinementSuggestion;
```

**建议组合的生成规则（确定性、可解释，下游逐条实现）**，以「最佳组合 B」为锚，按优先级取用直至逼近 `cap`（组合数 = `⌊cap / methods.length⌋`）：

1. **B 本身**（基线锚点，`rationale`：「上轮最佳，作对照」）。
2. **B − 最弱因子**：从 B 中剔除 `marginalLift` 最低（且为负者优先）的成员（`rationale`：「剔除疑似拖后腿的 {因子}」）。
3. **B + 最强候补**：把**不在 B**、`marginalLift` 最高（且为正）的池因子加入 B（`rationale`：「补入高边际贡献的 {因子}」）。
4. **前缀嵌套**：按 `marginalLift` 降序的前 `k` 个因子（`k = |B|-1, |B|, |B|+1` 落在 `[2, |pool|]` 内），生成嵌套前缀组合（`rationale`：「按贡献排序的 top-{k} 组合」）。
5. 全部经 `memberIdentity` 集合去重、越界/空组合剔除、限制在 `[2, MAX_EXPLORE_POOL]` 成员；数量超 `⌊cap/methods⌋` 则**保留优先级高者并置 `truncated=true`**。

**弹窗轮次状态（`WeightExploreModal`）**：

- 新增瞬态：`const [round, setRound] = useState(1)`、`const [rounds, setRounds] = useState<RoundCrumb[]>([])`（`RoundCrumb = { round, bestLabel, bestIc }`，非持久化）、`const [lastConfig, setLastConfig] = useState<ExploreConfig | null>(null)`（**P1-1**）、`const [refining, setRefining] = useState(false)`（**P2-2**）。
- **P1-1 · 留存上一轮 `config`**：`launch` 的 `mutationFn` 已持有本轮 `config`（`WeightExploreModal.tsx:133`），令其 `onSuccess` 一并 `setLastConfig(config)`（连同既有 `setLocalCombos/setPlan/setBatchId`）。精修所需的「上一轮因子池」即 `lastConfig.pool`——这是 `suggestRefinement` 产出合法 `combos: number[][]`（pool 下标）与组装下一轮 `config.pool` 的唯一可靠来源；`localCombos`/`plan.poolKeys` 都不足以无损还原完整 `FactorMember[]` 池。
- 新增 `startRound(config: ExploreConfig)`（抽为 ≤50 行 helper）：① 把当前轮最佳压入 `rounds` 面包屑；② `setRefining(true)`；③ 清空 `candidates/order/plan/localCombos`、`setBatchId(null)`、`setStale(false)/setConnected(true)`；④ `setRound(r => r + 1)`；⑤ `launch.mutate(config)`（**复用既有 launch 通路**，其 `onSuccess` 重置 `localCombos/plan/batchId/lastConfig` 并可 `setRefining(false)`；`onError` 亦 `setRefining(false)`）。WS 生命周期已按 `[batchId]` 键控——`batchId` 归 `null` 触发旧订阅 cleanup（关闭旧 socket），新 `batchId` 触发重订阅，**无需新增 WS 管理逻辑**。
- **P2-2 · 消除相位闪烁**：现相位 `phase = batchId==null ? "config" : settled&&len>0 ? "result" : "running"`（`WeightExploreModal.tsx:183`）。`startRound` 将 `batchId` 置 `null` 会在 launch pending 期间把相位打回 `"config"`（重挂配置态、`initialPool` 回退为原始 `ctx.members`），与「回到运行态」相悖。修复：相位判定改为 `batchId==null && !refining ? "config" : …`，`refining` 期间维持运行/启动视觉（保留上一轮矩阵占位 + 「正在启动下一轮…」提示）；launch 失败则 `setRefining(false)` 回落 config（携 `launchError`，用户可重来，可接受）。
- **精修的建议由外壳构建（对齐 P0-1）**：弹窗以 `computeInsights` 的 `insights` + `lastConfig.pool` + 当前 `best`（`localCombos.find(c=>c.id===best.variantLabel)?.members` 作 `bestComboMembers`、`best.method` 作 `bestMethod`）调 `suggestRefinement`，`useMemo` 得 `refineSuggestion: RefinementSuggestion | null`。`ExploreResultPanel` 新增 prop `onRefine?: () => void` + `refineDisabledReason?: string | null`：按钮「据洞察精修探索（下一轮）」在 `refineDisabledReason` 非空（`!insights.hasEnoughSamples` / 无 best / **P2-3**：`suggestRefinement` 去重后 `combos×methods < MIN_EXPLORE_CANDIDATES(2)`）时禁用并 `title` 说明；点击时外壳把 `refineSuggestion` 映射为 `ExploreConfig`（`{ strategy:"manual", params:{ combos }, methods, pool }`）交给 `startRound`。可选把 `refineSuggestion` 也下传用于展示各组合 `rationale`（「为何试它」）。
- 顶部标题区渲染 `rounds` 面包屑：`第 {round} 轮 · 上轮最佳 {bestLabel} IC {bestIc}`，让用户看清收敛轨迹。

### 2.5 关键序列（Sequence）

```
用户完成第 1 轮探索（既有流程）
  → 弹窗外壳 useMemo: computeInsights(states, localCombos) → insights（P0-1：外壳算，非子面板）
  → ExploreResultPanel 收到 {insights, labelOf, onRefine, refineDisabledReason} → ExploreInsightsPanel 渲染因子贡献榜
  → 外壳 useMemo: suggestRefinement(insights, lastConfig.pool, bestComboMembers, bestMethod, cap) → refineSuggestion（P1-1：pool=lastConfig.pool）
  → 用户点「据洞察精修探索（下一轮）」（refineDisabledReason==null 时可点）
      → onRefine → 外壳把 refineSuggestion 映射为 ExploreConfig → startRound(config)
          → rounds.push({round:1, bestLabel, bestIc}); setRefining(true); 清空候选态; setBatchId(null); setRound(2)
          → launch.mutate(config) → weightExploreApi.start(body)   ← 与首轮同一端点/同一请求形态
          → onSuccess: setLocalCombos/setPlan/setLastConfig/setBatchId(new); setRefining(false)
          → [batchId] effect: 关旧 WS → 订阅新 batchId → 运行态矩阵实时刷新（既有）
          （P2-2：refining 期间相位维持运行/启动视觉，不闪回配置态）
  → 第 2 轮完成 → 新洞察 → 可再精修（循环）或「应用最佳组合」回流（既有 applyBest，单写点，modelId=null）
```

---

## 三、文件 / 模块变更计划（File / Module Change Plan）

| 文件 | 新建/修改 | 一句话意图 |
|---|---|---|
| `apps/web/lib/exploreInsights.ts` | 新建 | 纯函数：`computeInsights`（因子贡献 + 大小分桶）、`suggestRefinement`（引导精修组合）、相关类型（`FactorContribution`/`SizeBucket`/`ExploreInsights`/`RefinementSuggestion`）。 |
| `apps/web/lib/exploreInsights.test.ts` | 新建 | 覆盖边际提升（含/不含对比、恒在因子→null 不伪造 0）、前四分位命中、平均权重缺键容忍、大小分桶、精修建议（B/±因子/前缀、去重、cap 截断标注、样本不足禁用）。 |
| `apps/web/components/Factor/ExploreInsightsPanel.tsx` | 新建 | 因子贡献榜（发散水平条）+ 组合大小趋势 + 弱样本友好态；纯 `var(--*)`/`div` 宽度可视化，无新依赖。 |
| `apps/web/components/Factor/ExploreInsightsPanel.test.tsx` | 新建 | 上色分档（正/负/null 占位）、样本不足渲染提示而非图、排序与数值格式。 |
| `apps/web/components/Factor/ManualComboBuilder.tsx` | 新建 | 手动组合构建器（组合行 × 因子 chip 网格 → `number[][]` 下标），受控组件；复用既有候选数预览守门。 |
| `apps/web/components/Factor/ManualComboBuilder.test.tsx` | 新建 | 加/删组合行、chip 切换、重复行提示、空行不产出、`onChange` 下标数组正确。 |
| `apps/web/components/Factor/ExploreConfigPanel.tsx` | 修改 | `STRATEGIES` 加「手动指定」；策略区扩为三分支（`usesSizes→SizePicker` / `manual→ManualComboBuilder` / `leave_one_out→静态文案`）、`params` 增 `manual→{combos}` 一支（**P2-1**，非「替代 usesSizes 分支」）。 |
| `apps/web/components/Factor/ExploreResultPanel.tsx` | 修改 | 在 best 卡与矩阵间插入 `ExploreInsightsPanel`（新 props `insights`/`labelOf`，**P0-1**）；新增「据洞察精修探索（下一轮）」按钮 + `onRefine?: () => void` + `refineDisabledReason?: string \| null` prop（禁用原因由外壳给出）。 |
| `apps/web/components/Factor/ExploreRunningPanel.tsx` | 修改（可选 P2） | 新 prop `insights?`；成功候选 ≥ 半数时显示「实时（部分）」洞察面板。 |
| `apps/web/components/Factor/WeightExploreModal.tsx` | 修改 | 新增 `round`/`rounds`/`lastConfig`/`refining` 状态（**P1-1/P2-2**）；`launch.onSuccess` 加 `setLastConfig(config)`；外壳 `useMemo` 算 `insights`+`refineSuggestion` 并下传子面板；`startRound(config)`（复用 launch，重置轮次态，`refining` 防相位闪烁）；顶部轮次面包屑。 |
| `apps/web/components/Factor/WeightExploreModal.test.tsx` | 修改 | 新增：完成态渲染洞察面板；精修按钮 → `weightExploreApi.start` 再次被调（第 2 轮）且候选态被重置；面包屑记录上轮最佳；样本不足时精修禁用。 |

> **无后端文件、无 `services/quant/**` 改动、无 DB 迁移、无 `lib/weightExplore.ts`/`weight_explore_planner.py` 改动**（`manual` 既有能力直接复用）。这是本迭代的核心右尺寸决策——见 §四 / §五。

---

## 四、接口设计（Interface Design）

### 4.1 REST / WebSocket —— **无变更（显式声明）**

本迭代**不新增、不修改任何 REST 端点或 WS 契约**。理由：

- 因子归因（能力 A）所需数据在前端已由既有 WS `candidate`/`batchDone` 事件 + 弹窗持有的 `localCombos` 全量提供，无需服务端计算或新字段。
- 手动策略（能力 B）复用既有 `POST /weight-models/explorations` 的 `strategy:"manual"` + `params.combos`（Iteration 1 已实现并测试）。
- 精修轮次（能力 C）的每一轮就是再发一次既有 `POST /weight-models/explorations`；读/取消/删除/WS 全部复用既有批次端点。

**这一「零后端」结论本身是关键设计决策**：它把等价性门禁、迁移风险、并发/对账不变量**完全隔离在本迭代之外**，使改动面收敛为纯前端加法，评审与回归成本最小。

### 4.2 前端纯函数签名（`lib/exploreInsights.ts`）

```ts
export function computeInsights(
  states: CandidateState[],
  combos: LocalCombo[],
  opts?: { weightKeyOf?: (m: FactorMember) => string },
): ExploreInsights;

export function suggestRefinement(
  insights: ExploreInsights,
  pool: FactorMember[],
  bestComboMembers: FactorMember[],
  bestMethod: TrainMethod,
  cap?: number,
): RefinementSuggestion;
```

类型 `FactorContribution` / `SizeBucket` / `ExploreInsights` / `RefinementSuggestion` 定义见 §2.2 / §2.4，与后端 camelCase 无关（纯前端派生态）。复用既有 `CandidateState`（`lib/weightBatchTypes.ts`）、`LocalCombo`/`FactorMember`（`lib/weightExplore.ts` / `lib/factorTypes.ts`）、`memberIdentity`（`lib/factor.ts`）、`formatIc`（`lib/factor.ts`）。

### 4.3 组件 props 增量

- `ExploreInsightsPanel`（新建，纯展示）：`{ insights: ExploreInsights; labelOf: (m: FactorMember) => string }`（**P0-1**：数据由外壳 `computeInsights` 算好后注入，面板不自持 `states/localCombos`）。
- `ExploreResultPanel`：`+ insights: ExploreInsights`、`+ labelOf: (m: FactorMember) => string`、`+ onRefine?: () => void`、`+ refineDisabledReason?: string | null`（**P0-1/P2-3**：`onRefine` 为零参触发器，精修建议与禁用原因均由外壳算好；`onRefine` 省略则不渲染精修按钮，向后兼容）。
- `ExploreRunningPanel`：`+ insights?: ExploreInsights | null`（可选 P2）。
- `ManualComboBuilder`：`{ pool: FactorMember[]; labelOf: (m: FactorMember) => string; value: number[][]; onChange: (v: number[][]) => void }`（≤5 参，符合阈值；下标随池编辑的失真防护见 §2.3 P1-2）。

---

## 五、数据模型（Data Model）

### 5.1 数据库 —— **无变更（显式声明）**

本迭代**不新增表、不新增列、不新增迁移**。归因是运行时派生，探索会话仍以既有 `kind="explore"` 批次 + `explore_plan_json` 持久化（Iteration 1 已足够复现与展示）；精修各轮各自是独立批次，天然可在批次列表回看。**不持久化「轮次链」**——轮次面包屑是弹窗内瞬态 UI 态，关闭即释放（与「探索即批次、不建会话历史表」的既有非目标一致）。

### 5.2 前端内存态（新增，纯派生）

- `ExploreInsights` / `FactorContribution` / `SizeBucket`：由 `computeInsights` 从既有 `CandidateState[]` + `LocalCombo[]` 派生，不落库、不入 store，随弹窗生命周期存活。
- `RefinementSuggestion`：由 `suggestRefinement` 一次性产出，映射为既有 `ExploreConfig` 后即弃。
- `WeightExploreModal` 新增瞬态：`round: number`、`rounds: RoundCrumb[]`（`{ round, bestLabel, bestIc }`）、`lastConfig: ExploreConfig | null`（**P1-1**：留存上一轮 `config`，供 `suggestRefinement` 取 `pool` 与组装下一轮请求）、`refining: boolean`（**P2-2**：精修启动过渡标志，防相位闪回配置态）——均为纯 UI/瞬态派生，非持久化、不入 store。

**既有契约零改动**：`CandidateState`、`ExplorePlan`、`LocalCombo`、`WeightExploreStartBody`、`WeightTrainResult` 全部保持不变。

---

## 六、测试与验收标准（Testing & Acceptance）

### 6.1 前端（`apps/web`，`NODE_ENV=test pnpm test` → `vitest run`；组件测试须 `NODE_ENV=test` 见项目记忆）

- `lib/exploreInsights.test.ts`：
  - `computeInsights`：给定构造的 `states`+`combos`，断言 `marginalLift = meanIcWith − meanIcWithout`；**恒在因子**（出现在所有成功候选）`marginalLift=null`（不为 0）；**无成功候选**时 `hasEnoughSamples=false` 且 `factors` 各项 `meanIcWith=null`。
  - `topHitRate`：前四分位阈值计算正确（含 <4 样本退化为「≥中位数」并可断言）。
  - `meanAbsWeight`：`result.weights` 缺该因子键时跳过该样本、全缺 → `null`（等权/IC 加权候选不崩）。
  - `sizes` 分桶：按成员数分组的均值/最优正确。
  - `suggestRefinement`：产出含「B 本身 / B−最弱 / B+最强 / 前缀」的组合、经 `memberIdentity` 去重、成员数 `[2,12]` 内、`combos×methods ≤ cap` 且超限置 `truncated=true`、`rationale` 与 `combos` 等长；`!hasEnoughSamples` 时返回仅含 B 的安全建议（或空 + 由 UI 禁用）。
- `components/Factor/ExploreInsightsPanel.test.tsx`：正/负 `marginalLift` 上色方向（`--long`/`--short`）、`null` 画中性占位 + 「始终包含」、`!hasEnoughSamples` 渲染提示文案而非图、数值经 `formatIc`。
- `components/Factor/ManualComboBuilder.test.tsx`：新增/删除组合行、chip 加入/移除更新下标、重复行提示、空行不进入 `onChange` 产出、`value` 下标数组结构正确；**P1-2**：删除一个被某组合引用的低位池因子后，该组合按 `memberIdentity` 重解、失真项被剔除并出现「已移除 N 项」提示（断言无静默错训、`onChange` 不再输出指向别因子的下标）。
- `components/Factor/ExploreConfigPanel.test.tsx`（若无则新增）：选「手动指定」渲染 `ManualComboBuilder`；候选数预览/超限阻断在 `manual` 下与自动策略一致。
- `components/Factor/WeightExploreModal.test.tsx`（扩）：完成态渲染 `ExploreInsightsPanel`（断言外壳把 `insights`/`labelOf` 下传，**P0-1**）；点「据洞察精修探索」→ `weightExploreApi.start` **第二次**被调用，其 body `strategy:"manual"` 且 `params.combos` 的下标口径落在**上一轮 `lastConfig.pool`** 上（**P1-1**，断言精修池来源正确、非空）、候选态被清空重建、`round` 递增、面包屑记录上轮最佳、过渡期不闪回配置态（**P2-2**，断言 `role="dialog"` 内不出现配置态特有元素）；`refineDisabledReason` 非空（`!hasEnoughSamples`/无 best/候选 `<2`）时精修按钮 `disabled` 且带 `title`（**P2-3**）；**回归**：「应用最佳组合」仍仅经 `buildTrainResult` 单写点、`modelId=null`（不绑父模型）。

### 6.2 回归门禁（不可降级）

- 既有 `factor-combo-exploration` 全部测试（planner/service/api/migration/matrix/modal/memberpalette）保持全绿——本迭代不改其源文件，理应零回归；开发节点须实跑确认。
- `setWeightTrainResult` 全仓调用点计数不变（仍唯一）；`grep` 断言无第二写入点。

### 6.3 验收标准（Definition of Done for 开发节点）

1. 探索完成态在「最佳组合卡」下出现**因子贡献榜**：每因子显示边际 IC 提升（发散条）、出场次数、登顶命中、平均权重；顶部因子=最该保留，底部负值=建议剔除；成功候选不足时显示友好提示而非误导图。
2. 配置态可选「手动指定」策略，用交互网格亲手勾选具体组合参与对比，候选数预览/超限阻断正常。
3. 完成态可一键「据洞察精修探索（下一轮）」，弹窗回到运行态跑出据洞察聚焦的新一轮候选，顶部面包屑显示各轮最佳；可连续精修多轮。
4. 全程无后端 / DB / API / WS 契约改动；等价性门禁与 `setWeightTrainResult` 单写点不变量不降级；既有探索测试全绿。
5. 新增文件均守 CLAUDE.md 阈值（800 行 / 50 行 / 参数 ≤5 / 嵌套 ≤4）。

---

## 七、风险与缓解（Risks & Mitigations）

| 风险 | 影响 | 缓解 |
|---|---|---|
| **少样本归因误导**（成功候选太少 → 边际提升噪声大） | 用户据噪声剔错因子 | `hasEnoughSamples` 门禁：<2 成功候选或无「含/不含」对比时不画归因图，只提示；前四分位 <4 样本退化并标注「样本较少」；`marginalLift` 无对比一律 `null`（不伪造 0）。 |
| **恒在因子的边际提升无定义** | 全池恒在因子被算成 0 提升，误读为「无用」 | `marginalLift=null` + UI「始终包含」Tag，明确区分「无对比」与「无贡献」。 |
| **`result.weights` 键与因子不对齐**（等权/IC 加权无逐因子权重） | 平均权重列崩或误显 | `weightKeyOf` 缺键跳过样本、全缺 → `null` 列显「—」；权重仅作辅助信号，归因主指标是 icOut 边际提升，不依赖权重。 |
| **精修轮次的 WS 泄漏 / 竞态**（重置 batchId 时旧订阅未关） | 旧 socket 悬挂、事件串扰 | 复用既有 `[batchId]` 键控 effect：`startRound` 先 `setBatchId(null)` 触发旧订阅 cleanup（`ws.close()`），新 id 再订阅；不新增手写 WS 管理。测试断言第 2 轮候选态从零重建。 |
| **精修组合爆炸**（据洞察生成过多组合） | 超候选上限 / 拖垮队列 | `suggestRefinement` 内 `combos×methods ≤ cap` 硬截断并置 `truncated=true`，UI 明示「已按上限保留 N 个，未静默丢弃」；落地仍走既有服务端 400 守门。 |
| **手动构建器与去重口径漂移** | 用户所见组合数 ≠ 实际训练数 | `ManualComboBuilder` 复用 `estimateComboCount`/`planCombos` 的 `manual` 分支（同一去重口径 C-7），不另写枚举；重复行 UI 提前提示。 |
| **手动下标随因子池编辑静默失真（P1-2）** | 删低位因子使下标下移指向另一因子，界内不报错→静默错训 | `ManualComboBuilder` 内部以 `memberIdentity` 为选择键、每渲染对当前 pool 重解下标；成员因池变更消失时显式提示并剔除（「已移除 N 项」），绝不静默保留失真下标。 |
| **精修起步相位闪烁（P2-2）** | `setBatchId(null)` 令相位闪回配置态，与「回到运行态」相悖 | `refining` 标志令相位判定 `batchId==null && !refining ? "config" : …`，过渡期维持运行/启动视觉；失败 `setRefining(false)` 回落 config。 |
| **精修候选数不足下限（P2-3）** | refine 绕过配置态守门，`<2` 候选命中服务端 400 | `suggestRefinement` 保证 `combos×methods ≥ MIN_EXPLORE_CANDIDATES`，否则精修按钮以 `refineDisabledReason` 禁用并说明。 |
| **误触后端 / 等价性面**（开发时顺手改了运行时） | 破坏 Iteration 1 硬门禁 | 变更计划显式限定「仅前端加法、零 `services/quant` 改动」；§6.2 回归门禁 + 单写点计数断言把关；评审节点对照 diff 拒绝任何后端改动。 |
| **弹窗文件膨胀破阈值** | `WeightExploreModal.tsx` 超 800 行 | 洞察逻辑归 `lib/exploreInsights.ts`、UI 归独立 `ExploreInsightsPanel.tsx`/`ManualComboBuilder.tsx`；`startRound` 抽为 ≤50 行 helper；轮次状态最小化。 |

---

## 八、非目标（Out of Scope）

- 不做服务端归因 / 不落库归因结果（纯前端派生，随弹窗生命周期存活）。
- 不做自动多轮无人值守搜索（遗传 / 贝叶斯优化）——精修仍是「用户一键、逐轮确认」的人机协同，避免不可控的长任务与队列压力。
- 不做跨标的 / 标的组归因扇出（沿用既有单目标语境）。
- 不做超参数网格（属多方法批次范畴，可后续融合）。
- 不持久化「精修轮次链」到独立表（各轮即独立 `kind="explore"` 批次，可在批次列表回看）。

---

## 九、实施优先级（Triage —— 供开发/评审节点在时间受限时排序）

- **P0 · 能力 A（因子贡献洞察）**：最贴合用户原始诉求「挖掘哪些因子好」，纯前端零风险，独立可交付。`lib/exploreInsights.ts::computeInsights` + `ExploreInsightsPanel` + 接入 `ExploreResultPanel`。
- **P1 · 能力 C（引导精修轮次）**：把洞察转化为行动，形成挖掘闭环；依赖 A 的 `computeInsights`，但 `suggestRefinement` 产出的是 `manual` 组合，**不依赖能力 B 的 UI**。
- **P2 · 能力 B（手动组合构建器）**：补齐 Iteration 1 的 v1.1 遗留，正交独立；即使不做，C 也能程序化产出 manual 组合。
- **P2 · 运行态实时洞察**：完成态洞察（P0）之外的锦上添花。

> 三项能力共享同一叙事「洞察驱动精修」，但已解耦为可独立交付、可按上表 triage 的增量，确保下游在任何时间预算下都能交付有价值的最小闭环（至少 P0）。

---

## 评审结论（Review Verdict）

**有条件通过（Approved with conditions）。**

本方案的核心判断经逐条源码核验后**成立**：三项能力（因子贡献洞察 / 引导精修 / 手动构建器）确可在「零后端 / 零 DB / 零 API / 零 WS 契约」的前提下纯前端叠加，等价性门禁与 `setWeightTrainResult` 单写点不变量被干净地隔离在本迭代之外；引用的既有符号、类型、状态与 WS 生命周期均真实存在且与设计吻合；P0/P1/P2 的 triage 合理、右尺寸得当。评审中发现的 1 处 P0、2 处 P1 契约缺口**已在本文档正文直接修复**并同步到相关章节（§2.2 / §2.3 / §2.4 / §2.5 / §三 / §4.3 / §5.2 / §六 / §七），文档版本升至 **v2**，当前**无遗留 P0 / P1**。

下游实现节点须落实以下条件（对应正文已固化的约束）：

1. **（P0-1）** `computeInsights` 与 `suggestRefinement` 一律在 `WeightExploreModal` 外壳内计算，把 `insights` + `labelOf` + `onRefine` + `refineDisabledReason` 下传子面板；`ExploreInsightsPanel` 保持纯展示、props 仅 `{ insights, labelOf }`。禁止在子面板内自行装配 `states/localCombos`。
2. **（P1-1）** `launch.onSuccess` 必须 `setLastConfig(config)`；精修的 `pool` 取 `lastConfig.pool`，`combos` 下标口径与之一致。缺 pool 即视为未完成能力 C。
3. **（P1-2）** `ManualComboBuilder` 以 `memberIdentity` 为内部选择键、对当前 `pool` 重解下标，池变更导致成员消失时显式提示并剔除，杜绝界内下标位移的静默错训。
4. **（P2，建议采纳）** manual 接线走三分支且 `params` 增 `manual→{combos}`（P2-1）；`refining` 标志消除相位闪烁（P2-2）；`suggestRefinement` 保证 ≥ `MIN_EXPLORE_CANDIDATES` 候选否则禁用精修（P2-3）；`computeInsights` 复用 `candidateIcOut` 取 IC（P2-4）；`weightKeyOf` 默认 `factorKey` 的同名不同参错配已优雅降级、仅需保持「缺键跳过」不崩（P2-5）。
5. **回归门禁不可降级**：`services/quant/**`、`lib/weightExplore.ts`、后端 planner 零改动；既有 `factor-combo-exploration` 全测保持全绿；`setWeightTrainResult` 全仓单写点计数不变（`grep` 断言）；新增文件守 CLAUDE.md 阈值（800 / 50 / ≤5 参 / 嵌套 ≤4）。评审节点须对照 diff 拒绝任何后端 / 等价性面改动。

满足以上条件即可进入实现。P2 项不阻断合入，但建议在同一 PR 内一并处理以免留尾。

---

## 实施过程发现的方案缺陷（Issues Found During Implementation）· Subtask #2（开发）· Iteration 2/2 · 2026-07-02

> 均为**加法式小补全**，不改变设计意图与任何硬约束（仍零后端 / 零 DB / 零 API / 零 WS、单写点不变、尺寸达标）。已在实现中按下述方式落地，并记录于此以供评审对照。

**F1 ·（补全，非纠错）`FactorContribution` 缺少可供 `labelOf` 映射的成员引用。**
现象：§2.2 的 `FactorContribution` 字段仅有 `factorKey`/`identity`，而 `ExploreInsightsPanel` 的 props 固定为 `{ insights, labelOf: (m: FactorMember) => string }`（P0-1），面板要渲染因子中文名必须以一个 `FactorMember` 调 `labelOf`。仅凭 `factorKey` 无法无损构造 `FactorMember`（缺 params/direction）。
处置：`FactorContribution` 增只读字段 `member: FactorMember`（该身份**首次出现**的代表成员，由 `computeInsights` 从 `combos` 收集），面板以 `labelOf(f.member)` 取名。纯派生、不落库；`computeInsights` 仍是纯函数、确定性不变。

**F2 ·（补全，非纠错）`ExploreRunningPanel` 渲染实时洞察需要 `labelOf`，但 §4.3 只列了 `insights?`。**
现象：§4.3 为运行态面板仅新增 `+ insights?: ExploreInsights | null`，但 `ExploreInsightsPanel` 需要 `labelOf` 才能出名，运行态复用同一面板亦然。
处置：`ExploreRunningPanel` 同时新增可选 `labelOf?: (m: FactorMember) => string`，由弹窗外壳与 `insights` 一并下传；二者齐备且成功候选 ≥ `ceil(total/2)` 时才显示「实时（部分）」洞察（与 §2.2 门槛一致）。属可选 P2，不影响完成态主路径。

**F3 ·（测试补全）新增 `ExploreConfigPanel.test.tsx`。**
§6.1 注明「若无则新增」，仓库原无该测试文件，本次新建，覆盖「选『手动指定』渲染 `ManualComboBuilder`」与「manual 候选数预览 / 达下限解禁 / `onLaunch` 收到 `params.combos`」。

> 以上三项均已在实现中处理完毕，无遗留；不涉及后端 / 等价性 / 迁移面，回归门禁不受影响（全量前端 100 文件 857 用例全绿）。

---

> 方案签署：Subtask #0（方案设计）· Iteration 2/2 · 2026-07-02 · 初稿 v1
> 评审签署：Subtask #1（方案评审）· Iteration 2/2 · 2026-07-02 · 文档版本 **v2**（有条件通过；1×P0 + 2×P1 已在正文修复，无遗留）
> 基座：`factor-combo-exploration` v2（commit `31ddcc9`）。本迭代为其纯前端增强，严守其全部硬约束（等价性门禁 / 单写点 / 迁移隔离 / 尺寸阈值）。
