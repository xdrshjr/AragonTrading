# 组合策略并入回测策略列表 + 新建组合改为居中弹窗

> 设计文档 **v2** · feature slug: `combo-strategy-list-and-modal`
> 作者：方案设计节点（标准四阶段开发 · Subtask #0）；评审与修复：方案评审节点（Subtask #1）
> 关联前序方案：`docs/plans/regime-adaptive-strategy-combination/spec.md`（体制自适应轮动组合引擎，已落地）、`docs/plans/strategy-combo-management/spec.md`（组合 CRUD / 成员多页签 / 详情弹窗，已落地）
> 适用分支事实：以下结论均经「真实源码逐文件核对」确认，可直接据此落地。
> v2 变更摘要：评审逐文件复核源码后，修复 2 处 P1（漏改 `ChartWorkspace.tsx` 致 `tsc` 失败、walk-forward 渲染分支未按 `effectiveKind` 收口）与 4 处 P2，详见下方「评审记录」。

---

## 〇、评审记录（Review Notes · v2）

> 评审维度：可行性（Feasibility）/ 完整性（Completeness）/ 一致性（Consistency）/ 右尺寸（Right-sizing）。
> 评审方式：对 spec 引用的每条源码事实逐文件核对（前端 `apps/web/**` + 后端 `services/quant/**`），并全仓 grep 校验「影响面」声明。
> 核对结论：方案对现有架构的事实陈述**绝大多数准确**——`Mode` 类型、`ChartToolbar.MODES`、`store`（`setMode`/`version:13`/`comboDraft`/`comboEditingId`/`partialize`/`migrate`）、`page.tsx:84` `singleRunMode`、`StrategyPanel`（`atomicStrategies` 过滤、选中守门 effect、自动回测 effect、`BacktestModeSwitch` 仅 backtest 可见）、`ComboView` 内联结构与四 mutation、`lib/combo.ts` 助手、`StrategyList` 徽标、`ui/Modal` 的 a11y 契约（`role=dialog`/`aria-modal`/`aria-labelledby`/`Esc`/遮罩/焦点陷阱/焦点恢复/滚动锁/Portal 居中/`max-h-[85vh]`/`max-w-2xl`）、后端 `_run_saved_combo` 分流 + `combo` 字段 + 组合 run `mode="backtest"`（`services.py:436`）+ walk-forward 对组合显式抛 `WF_COMBO_UNSUPPORTED`(400) + CRUD/preview 端点 — 均经核对属实。在此基础上发现并修复以下问题：

| 编号 | 维度 | 严重度 | 问题 | 状态 |
|---|---|---|---|---|
| R-P1-1 | 可行性 | **P1** | §2.3 断言「`"combo"` 作为 **Mode 值**仅出现在 4 处源码 + `store.test.ts`」，但全仓 grep 另有一处 **`components/Chart/ChartWorkspace.tsx:195`** 用 `mode === "combo"` 渲染空态文案，且「文件变更计划」未列入该文件。收窄 `Mode` 删除 `"combo"` 后该行触发 TS2367（比较无重叠），违反 DoD §6「`tsc` 无误」与风险表 R1「逐处改净」。 | ✅ 已修复（§2.3、§2.8、§三、§六、§七） |
| R-P1-2 | 完整性 | **P1** | `StrategyPanel` 结果区由 `mode === "backtest" && backtestKind === "walkforward"`（line 418）决定渲染 `<WalkForwardResults>` 还是「绩效/成交/历史」页签。§2.4 仅为**执行路径**与**自动回测 effect 守卫**引入 `effectiveKind`、并隐藏开关，但未规定**该渲染分支**也按 `effectiveKind` 收口。后果：全局 `backtestKind==="walkforward"` 时从合并列表选中组合，右栏会渲染 Walk-Forward 面板（空/陈旧）而非组合绩效 + `ComboResult`，违反验收 §6.2；后端对组合 walk-forward 已显式抛 `WF_COMBO_UNSUPPORTED`，更须前端彻底绕开。 | ✅ 已修复（§2.4、§六） |
| R-P2-1 | 一致性 | P2 | §2.8/文件表要求 `MemberPicker` 组合页签「仅保留导入成员、去掉嵌套查看」。删除「查看」按钮后 `onViewCombo`/`onView` 形参与透传变为死参（CLAUDE §四 僵尸代码）。 | ✅ 已修复（§2.5、§三）：一并移除 `onViewCombo` prop |
| R-P2-2 | 完整性 | P2 | 选中组合后自动回测沿用原子行为（不带 `showRunRange`、不切 `run-range`），左图须经显式「运行回测」绘制。与原子一致、非缺陷，但 §2.9 未点明，实现者易据验收 §6.2 误判为「选中即应自动绘图」。 | ✅ 已修复（§2.9 时序补注） |
| R-P2-3 | 右尺寸 | P2 | `StrategyPanel.tsx` 现 **650 行**，逼近 800 上限；合并列表 + 选中分流 + 三弹窗开关态将显著增量。风险表已提，但缺硬约束。 | ✅ 已修复（§三 阈值自查升级为硬约束） |
| R-P2-4 | 一致性 | P2 | §四接口表未标 `POST /strategies` 的 `201 Created`（后端 `status_code=201`）。文档准确性小瑕。 | ✅ 已修复（§四） |

> 无 P0：方案根基稳健——零后端改动、复用 `Modal`/既有 mutation、按职责抽出三组件、删除两个僵尸文件，影响面克制、与既有约定一致。

---

## 一、概述（Overview）

本项目已在「交易策略」面板落地了**用户自定义组合策略**（体制自适应轮动，模板 `regime_rotation`、`kind="combo"`）：用户在 K 线图上方的「**组合**」模式（`mode==="combo"`）下进入 `ComboView`，勾选成员、配置轮动参数、运行预览，并保存为一行 `Strategy`。然而当前形态有两处明显的体验缺陷，正是本次需求要修复的：(1) **组合被单列为一个独立模式页签**（K 线图上方「回测 / 对比 / 组合 / 实时」中的「组合」），用户必须离开「回测」语境、切到一个专门的页签才能使用组合，割裂了「组合也是一种可回测策略」的心智模型；(2) **新建 / 编辑组合的整套编辑器是内联渲染在右侧面板下方的**（`ComboView` 直接铺在 StrategyPanel 内），表单、成员选择、轮动参数、预览结果层层堆叠在窄长的右栏里，既不聚焦也不美观。

本方案把组合策略**降格为「回测」语境下与单策略平级的一等公民**，并把**新建 / 编辑组合的编辑器抽成屏幕居中的模态弹窗**。具体地：删除 K 线图上方的「组合」模式页签；在「回测」模式下，右侧「交易策略」列表除展示各单个（原子）策略外，**追加展示用户自己创建的组合策略**（同一份列表、同一层级、仅以「组合」徽标区分）；用户在列表中选中一个组合后，可像选中普通策略一样**点击「运行回测」直接回测**（左图照常绘制该组合的单条净值曲线）。新建组合则改为点击「+ 新建组合」打开一个**美观优雅、符合人机交互最佳实践的居中弹窗**完成；编辑、查看、删除沿用既有弹窗与二次确认。

之所以改动可以很克制，是因为后端早已把组合当作普通 `Strategy` 行对待：`POST /strategies/{id}/backtest` 已对组合行分流到 `_run_saved_combo`（在 `{runId, metrics}` 之上追加 `combo` 字段），且组合回测 run 的 `mode` 字段落库就是 `"backtest"`（`services.py:436`）。因此本方案**零后端改动、零数据库表结构变更**，主体是前端的「模式删除 + 列表合并 + 编辑器搬进弹窗」三件事，外加随之而来的 store 持久化迁移与组件拆分。需要特别守住的边界是：组合**只支持回测 / 对比，不支持实时模拟（paper）与高级回测（walk-forward）**——前者后端 paper 引擎不接组合，后者 `run_walk_forward` 走原子撮合路径（`_resolve_strategy_spec` + `cls(params)`，对组合无效），故 UI 必须在「实时」模式列表中排除组合、在选中组合时禁用「高级回测」子模式。

---

## 二、技术设计（Technical Design）

### 2.1 当前架构事实（核对结论，复用不重造）

- **模式页签**：`ChartToolbar.tsx` 的 `MODES = [回测 backtest, 对比 compare, 组合 combo, 实时 paper]`，点击 `setMode(key)` 写入 store。`Mode` 类型为 `"backtest" | "compare" | "combo" | "paper"`（`lib/types.ts:2`）。
- **面板路由**：`StrategyPanel.tsx` 按 `mode` 分流——`combo → <ComboView/>`、`compare → <CompareView/>`、其余（backtest/paper）→ `StrategyList + StrategyDetail + 结果页签`。其中 `atomicStrategies = strategies.filter((s) => s.kind !== "combo")`（`StrategyPanel.tsx:147`），组合被**显式排除**出回测 / 实时列表。
- **ComboView**（将拆解）：内联三区——`SavedCombosList`（「我的组合策略」列表）+ 编辑器（`NameField` + `MemberPicker` + `PolicyEditor` + 保存 / 运行组合回测）+ `ComboResult`（预览结果：年化 + `MetricsCards` + `ComboTimeline` + 分体制贡献）；并挂着 `ComboDetailModal`（查看）与 `DeleteComboConfirm`（删除）两个已是弹窗的子组件。create/update/remove/preview 四个 mutation 与 `loadForEdit`/`resetEditor` 编辑态逻辑都在此。
- **store**：`comboDraft`（持久化，`{memberIds, policy}`）、`comboEditingId`（瞬态，`null`=新建态）已存在并被编辑器消费；`setMode` 对 `"combo"` 有特例 `chartView="run-range"`（`store.ts:216-226`）；持久化 `version:13`，`mode` 在 `partialize` 中（刷新会恢复上次模式）。
- **chart 链路**：`page.tsx:84` `singleRunMode = mode === "backtest" || mode === "combo"`——组合与回测共用「单条 run 净值 + 成交 markers」的 `run-range` 绘制链路。组合回测产出真实 run（含 equity/trades/metrics），故并入 backtest 后绘制行为不变。
- **后端**：`POST /strategies/{id}/backtest → run_single_backtest`，`_is_combo(row.template_key)` 为真即转 `_run_saved_combo`，响应追加 `combo`；前端 `api.backtest` 返回类型 `BacktestResult`（已含可选 `combo`，`lib/types.ts:153-160`）。`api.createStrategy/updateStrategy/deleteStrategy/comboPreview` 均已存在，**无新增端点**。

### 2.2 目标架构（改动后）

```
K 线图上方模式页签：回测 | 对比 | 实时        ← 删除「组合」
        │
        ▼ mode==="backtest"
┌ StrategyPanel（回测）──────────────────────────────────┐
│ 头部：交易策略  · [+ 新建组合]（仅 backtest 模式可见）       │
│ StrategyList（合并列表）                                  │
│   - 原子策略行（现状）                                     │
│   - 用户组合行（追加；行尾「组合」徽标，同一层级）            │
│ 选中行详情：                                              │
│   - 原子 → StrategyDetail（现状：参数网格 + 运行回测 + 另存为）│
│   - 组合 → ComboStrategyDetail（摘要 + 运行回测 + 查看/编辑/删除）│
│ 结果区：绩效指标 / 成交明细 / 历史（选中组合且回测带 combo     │
│         时，绩效页签顶部追加 ComboResult：年化 + 时间线 + 分体制）│
└────────────────────────────────────────────────────────┘
  弹窗（Portal 居中，复用 Modal 原语）：
   - ComboEditorModal（新建 / 编辑组合）   ← 需求 2 的核心
   - ComboDetailModal（查看，沿用）
   - DeleteComboConfirm（删除二次确认，沿用）
```

### 2.3 改动 1：删除「组合」模式（需求 1 的前半）

- `lib/types.ts`：`Mode` 收窄为 `"backtest" | "compare" | "paper"`。
- `components/Chart/ChartToolbar.tsx`：从 `MODES` 删除 `{ key: "combo", label: "组合" }`。
- `lib/store.ts`：
  - `setMode` 的 `chartView` 派生去掉 `mode === "combo" ? "run-range"` 分支 → 只剩 `compare → "compare-range"`，其余 → `"live-follow"`。
  - **持久化迁移**：`mode` 入 `partialize`，老用户本地可能存着 `mode:"combo"`，而该视图已删。bump `version: 13 → 14`，新增 `if (version < 14) { if (next.mode === "combo") next.mode = "backtest"; }`，把历史的 combo 模式安全收敛到 backtest（避免落到一个不存在的视图）。
  - **保留** `comboDraft` / `comboEditingId` 及其 setter（编辑器弹窗仍用）。
- `app/page.tsx:84`：`singleRunMode = mode === "backtest"`（删 `|| mode === "combo"`）。组合并入 backtest 后此式自动覆盖组合。
- **`components/Chart/ChartWorkspace.tsx:195`（v2·R-P1-1 补漏）**：`emptyHint` 三元中含 `mode === "combo" ? "运行组合回测后显示组合净值曲线" : …` 分支。收窄 `Mode` 后该字面量比较会触发 TS2367，须删除该 `mode === "combo"` 分支；删除后组合在 backtest 模式下回落 backtest 文案「运行回测后显示净值曲线」，语义正确（组合即一条可回测策略）。
- 影响面核对（v2·R-P1-1 订正）：经全仓 grep，`"combo"` 作为 **Mode 值**出现在 **5 处源码**——`lib/types.ts`（定义）、`ChartToolbar.tsx`、`lib/store.ts`、`app/page.tsx`、**`ChartWorkspace.tsx`**——外加 `StrategyPanel.tsx`（`mode === "combo"` 分支随整支删除，见 §2.4）与 `store.test.ts`（测试，见 §六）。注意 `CompareView.tsx:108`、`lib/combo.ts:108` 的 `s.kind === "combo"` 是 **`Strategy.kind` 值**（非 `Mode`），原样保留、不受收窄影响。`Run.mode` 由后端返回且组合 run 落库为 `"backtest"`（`services.py:436`，已核对），故收窄 `Mode` 不会与 `Run` 形态冲突。`lib/chartLive.ts`（`shouldStreamMarket`/`shouldApplyPublicCandle`/`resolveChartRange`）无 combo 字面量，按 `mode` 泛化处理，无需改动。

### 2.4 改动 2：组合并入回测列表 + 选中即可回测（需求 1 的后半）

`StrategyPanel.tsx`：

- **列表数据源**：新增
  ```ts
  const userCombos = strategies.filter(isUserCombo);            // 复用 lib/combo.ts
  const listStrategies = mode === "backtest" ? [...atomicStrategies, ...userCombos] : atomicStrategies;
  ```
  「实时（paper）」与既有一致只列原子；「回测」列表把组合追加到原子之后（保持原子在前的稳定顺序，组合靠徽标区分）。`<StrategyList strategies={listStrategies} .../>`。
- **选中合法性守门（替换现 `useEffect` 第 95-99 行）**：把「`strategyId` 必须存在于 `strategies`」升级为「必须存在于**当前模式的 `listStrategies`**」；不满足则回落 `listStrategies[0]`。这同时解决「在回测选中组合后切到实时，组合不在 paper 列表却仍是 selected」的边界——切到 paper 会自动把选择收敛到首个原子策略。
- **选中详情分流**：`selected = strategies.find(id===strategyId)`。
  - `isCombo(selected)` → 渲染新组件 `ComboStrategyDetail`（见 §2.6），不渲染 `StrategyDetail`（后者是原子参数编辑器，对组合无意义）。
  - 否则原子 → `StrategyDetail`（现状不变）。
- **高级回测限制（v2·R-P1-2 强化）**：引入派生量 `effectiveKind = isCombo(selected) ? "simple" : backtestKind`，并在**三处**统一用它收口（缺一不可）：
  1. **开关渲染**：`BacktestModeSwitch`（普通 / 高级回测切换）**仅在选中原子策略时渲染**（`mode==="backtest" && !isCombo(selected)`）；选中组合时隐藏。
  2. **执行路径**：回测执行按 `effectiveKind` 分流，确保即便全局 `backtestKind==="walkforward"`、用户选中组合时也走普通回测（后端对组合 walk-forward 显式抛 `WF_COMBO_UNSUPPORTED`(400)，已核对 `services.py:537-539`）。
  3. **结果区渲染分支（line 418，本次新增收口点）**：现分支条件为 `mode === "backtest" && backtestKind === "walkforward"` → 改为 `mode === "backtest" && effectiveKind === "walkforward"`。否则当全局处于高级回测、用户从合并列表选中组合时，右栏会渲染 `<WalkForwardResults>`（空/陈旧）而非组合的「绩效/成交/历史」页签 + `ComboResult`，与验收 §6.2 冲突。收口后选中组合恒走绩效页签路径。
- **自动回测 effect（第 181-189 行）（v2·R-P1-2 订正）**：守卫由 `backtestKind !== "simple"` 改为 `effectiveKind !== "simple"`（否则全局 walkforward 下选中组合会被守卫提前 return、不自动回测）；并把 `effectiveKind`（或等价的 `selected?.kind`）纳入依赖数组，使「全局 walkforward 时切到组合」也能触发自动回测。`lastAutoKey` 键不变（含 `strategyId`，组合有自己的 id，天然生效）。选中组合即自动 `api.backtest(comboId, {instId,bar,startTs,endTs})`，与原子一致。
- **组合结果呈现**：现 `backtest` mutation 的 `onSuccess` 已写 `setMetrics(data.metrics)`；新增在选中组合时记录 `data.combo`（如 `setComboResult(data.combo ?? null)`）。绩效页签渲染时：选中组合且 `comboResult` 存在 → 在 `MetricsCards` 上方插入 `<ComboResult>`（年化 + `ComboTimeline` + 分体制贡献，见 §2.7 抽出）。成交明细 / 历史页签对组合照常工作（组合 run 有 trades/equity）。
- **「+ 新建组合」入口**：面板头部（`mode==="backtest"` 时）放一个 `Button`「+ 新建组合」，点击：`resetEditor()`（清空草稿 + `setComboEditingId(null)`）后 `setComboEditorOpen(true)`，打开 `ComboEditorModal` 的新建态。

`StrategyList.tsx`：在行右侧徽标处，`s.kind === "combo"` 时渲染 `<Badge tone="accent">组合</Badge>`（与既有「内置」徽标并列）；其余渲染逻辑（选中态、收益徽标、描述）不变。组合行**不参与 paper 多选**（paper 列表本就不含组合，故 `multiSelect*` 对组合不会出现）。

### 2.5 改动 3：新建 / 编辑组合改为居中弹窗（需求 2 的核心 · 顶级设计）

新增 `components/Strategy/ComboEditorModal.tsx`，把 `ComboView` 的编辑器整体搬进 `ui/Modal`（已含 `role=dialog` / `aria-modal` / `aria-labelledby` / 焦点陷阱 / `Esc` / 点遮罩关闭 / 滚动锁定 / 焦点恢复——满足 a11y 与「居中弹窗」诉求）。

**Props**：`{ open: boolean; editingId: number | null; onClose: () => void; onSaved?: (created: Strategy | null) => void }`。组件内部消费 store 的 `comboDraft / setComboDraft / comboEditingId / setComboEditingId`，并持有自身的 `name / saveError / importNotice / preview 结果` 本地态。

**版式与交互（HCI 最佳实践）**：
```
┌ Modal（max-w-3xl, max-h-[85vh], 居中, Portal）────────────┐
│ header（sticky 顶）：标题「新建组合 · 体制自适应轮动 / 编辑组合 · {name}」 + 关闭按钮 │
├──────────────（body：overflow-y-auto, 分区）────────────│
│ ① 组合名称（NameField，空名 aria-invalid + 行内提示）       │
│ ② 选择成员策略（MemberPicker：普通策略 / 组合策略 双页签）   │
│ ③ 轮动参数（PolicyEditor：2 列网格 + 悬浮释义）            │
│ ④ 预览结果（运行组合回测后渲染 ComboResult；失败显错误态）   │
├──────────────（footer：sticky 底，主操作常驻可见）────────│
│ [保存组合 / 保存修改]（primary）· [运行组合回测]（default）· [取消]（ghost）│
└────────────────────────────────────────────────────────┘
```
- **视觉**：沿用既有设计 token（`--bg/--surface/--surface-2/--border/--accent/--accent-soft/--text/--muted/--long/--short`），明暗主题零适配；分区标题用 `text-xs font-semibold uppercase tracking-wide text-[var(--muted)]`，与 `ComboDetailModal`/`OkxAccountDetailModal` 同语汇；动效用既有 `transition-* duration-150 ease-smooth`，克制。
- **sticky footer**：主按钮（保存 / 预览 / 取消）固定在弹窗底部，长表单滚动时始终可点（HCI：主操作不被滚动埋没）。
- **禁用与反馈**：成员数 `<2` 或 `>8`（`validateMemberCount`）禁用「保存」并显示中文原因；名称去空白为空禁用「保存」并提示「请填写组合名称」；保存中 / 回测中按钮显 loading 文案并禁用；保存失败 / 预览失败显行内错误态（不假数据）。
- **嵌套弹窗规避**：`MemberPicker` 的「组合策略」页签在编辑器内**只保留「导入成员」**，不在编辑器里再开 `ComboDetailModal`（避免 Modal-on-Modal 的同层 z-index 与焦点陷阱冲突）。查看详情的入口在列表选中态（`ComboStrategyDetail`）提供。**（v2·R-P2-1）** 随「查看」按钮删除，须一并移除 `MemberPicker` 的 `onViewCombo` prop 及 `ComboSourceList` 的 `onView` 透传与调用点，避免遗留死参（CLAUDE §四 僵尸代码）；`ComboEditorModal` 内调用 `MemberPicker` 时不再传 `onViewCombo`。
- **保存成功**：`invalidateQueries(["strategies"])` → `resetEditor()` → `onClose()`；`onSaved(created)` 回传后由 `StrategyPanel` 把列表选择切到新组合（`setStrategyId(created.id)`），形成「建完即选中、可立即回测」的闭环。
- **编辑入口**：从 `ComboStrategyDetail` 或 `ComboDetailModal` 的「编辑」→ `loadForEdit(combo)`（`resolveMemberIds` 回填成员 id、`extractComboPolicy` 回填参数、回填 `name`、置 `comboEditingId`）→ `setComboEditorOpen(true)`，弹窗进入编辑态。

`NameField` 与 `PolicyEditor`（现为 `ComboView` 内联私有）随编辑器搬入 `ComboEditorModal` 作为其内部私有子组件；`POLICY_HINTS` 等文案仍从共享 `lib/comboPolicyHints.ts` 取（不复制）。

### 2.6 新增 `ComboStrategyDetail.tsx`（列表选中组合时的右栏详情）

紧凑、聚焦：
- 顶部：组合描述（取自 `selected.description`）+ `summarizeComboMembers(combo)` 摘要（如「4 个成员 · 夏普选优 · 体制门控」，复用 `lib/combo.ts`）。
- 操作按钮：
  - **运行回测**（primary）：触发与原子同款的 `backtest.mutate({ id: combo.id, params: undefined, showRunRange: true })`；左图绘制组合单条净值。
  - **查看详情**：打开 `ComboDetailModal`。
  - **编辑**：`loadForEdit` + 打开 `ComboEditorModal`（编辑态）。
  - **删除**：打开 `DeleteComboConfirm`。
- 不含原子参数网格、不含「另存为」、不含 paper 启停（组合不接 paper）。

### 2.7 抽出 `ComboResult.tsx`（供弹窗预览与列表回测复用）

把 `ComboView` 内联的 `ComboResult` + `BreakdownTable` + `RegimeRows` 抽到独立文件 `components/Strategy/ComboResult.tsx`，props：`{ result: { metrics: Metrics; combo: ComboPreviewResult["combo"] }; onGoCompare?: () => void }`。`ComboEditorModal`（预览）与 `StrategyPanel`（选中组合回测结果）同源消费，杜绝重复实现。

### 2.8 删除与去孤岛

- `ComboView.tsx`：模式删除后不再被渲染，整体删除（逻辑已分散到 `ComboEditorModal` + `StrategyPanel` 列表合并 + `ComboResult`）。
- `SavedCombosList.tsx`：组合已并入主 `StrategyList`，「我的组合策略」独立列表不再是承载面（管理动作迁至 `ComboStrategyDetail` + 「+ 新建组合」按钮），删除以免僵尸组件（CLAUDE §8.8）。
- `MemberPicker.tsx` / `ComboDetailModal.tsx` / `DeleteComboConfirm.tsx` / `ComboTimeline.tsx` / `lib/comboPolicyHints.ts` / `lib/combo.ts`：**保留沿用**（`MemberPicker` 在编辑器弹窗内复用；详情 / 删除弹窗在列表选中态复用）。

### 2.9 关键时序（Sequence）

- **回测一个组合**：回测模式 → `StrategyList` 选中组合行 → `setStrategyId(id)` → 自动回测 effect 触发 `api.backtest(id, …)` → 后端 `_run_saved_combo` 返回 `{runId, metrics, combo}` → `setRunId` + `setMetrics` + `setComboResult(combo)` → **右栏绩效页签顶部显 `ComboResult`**。**（v2·R-P2-2）注意**：自动回测 effect **不带 `showRunRange`**，故仅刷新绩效、**不**自动把 `chartView` 切到 `run-range`（与原子策略选中即自动回测的既有行为一致，非缺陷）。左图绘制该组合单条净值需经 `ComboStrategyDetail` 点「运行回测」显式触发（`showRunRange:true` → `setChartView("run-range")`），此即验收 §6.2「点运行回测，左图绘出净值」所述路径；在区间页签下亦可由工具栏「区间」按钮切到 `run-range` 查看。
- **新建组合**：点「+ 新建组合」→ `resetEditor()` + 开 `ComboEditorModal`（新建态）→ 选成员 / 调参数 →（可选「运行组合回测」预览）→「保存组合」`api.createStrategy({templateKey:"regime_rotation", name, params:{members, ...policy}})` → `invalidate(["strategies"])` → 关弹窗 → 列表现新组合行并自动选中。
- **编辑组合**：`ComboStrategyDetail`/`ComboDetailModal`「编辑」→ `loadForEdit` 回填草稿 + name + `comboEditingId` → 开 `ComboEditorModal`（编辑态）→「保存修改」`api.updateStrategy(id, {name, params})` → `invalidate` → 关弹窗。
- **删除组合**：「删除」→ `DeleteComboConfirm` 二次确认 → `api.deleteStrategy(id)` → `invalidate`；若删的是当前选中 / 编辑中的组合，重置选择与编辑态。
- **写后缓存一致性**：create/update/remove 三 mutation 的 `onSuccess` 均 `invalidateQueries(["strategies"])`（沿用 strategy-combo-management v2 既定口径，不得遗漏，否则列表不刷新）。

---

## 三、文件 / 模块变更计划（File / Module Change Plan）

| 文件 | 新增/修改/删除 | 一句话意图 |
|---|---|---|
| `apps/web/lib/types.ts` | 修改 | `Mode` 收窄为 `"backtest" \| "compare" \| "paper"`（删除 `"combo"`）。 |
| `apps/web/components/Chart/ChartToolbar.tsx` | 修改 | 从 `MODES` 删除「组合」页签项。 |
| `apps/web/components/Chart/ChartWorkspace.tsx` | 修改（v2·R-P1-1 补漏） | 删除 `emptyHint` 三元里的 `mode === "combo"` 分支（收窄 `Mode` 后该字面量比较触发 TS2367），组合回落 backtest 空态文案。 |
| `apps/web/lib/store.ts` | 修改 | `setMode` 去掉 combo→run-range 特例；`version 13→14` 迁移把历史 `mode:"combo"` 收敛为 `"backtest"`；保留 `comboDraft/comboEditingId`。 |
| `apps/web/app/page.tsx` | 修改 | `singleRunMode = mode === "backtest"`（删除 `\|\| mode === "combo"`）。 |
| `apps/web/components/Strategy/StrategyPanel.tsx` | 修改 | 删除 `mode==="combo"` 分支；回测列表合并原子+用户组合；选中组合分流到 `ComboStrategyDetail`；高级回测开关仅原子可见+`effectiveKind`；选中合法性守门按当前模式列表校验；「+ 新建组合」按钮打开编辑器弹窗；组合回测结果接 `ComboResult`；持有三弹窗开关态。 |
| `apps/web/components/Strategy/StrategyList.tsx` | 修改 | 组合行追加「组合」徽标（与「内置」徽标并列），其余不变。 |
| `apps/web/components/Strategy/ComboEditorModal.tsx` | 新增 | 居中弹窗编辑器（名称+成员双页签+轮动参数+预览+sticky footer），复用 `Modal`；承接 create/update/preview mutation 与编辑态回填。 |
| `apps/web/components/Strategy/ComboStrategyDetail.tsx` | 新增 | 列表选中组合时的右栏紧凑详情：摘要 + 运行回测 + 查看/编辑/删除。 |
| `apps/web/components/Strategy/ComboResult.tsx` | 新增（抽出） | 从 `ComboView` 抽出 `ComboResult/BreakdownTable/RegimeRows`，供弹窗预览与列表回测复用。 |
| `apps/web/components/Strategy/ComboView.tsx` | 删除 | 模式删除后不再渲染；逻辑已分散到上面三处。 |
| `apps/web/components/Strategy/SavedCombosList.tsx` | 删除 | 组合并入主列表后该独立列表不再是承载面，去僵尸组件。 |
| `apps/web/components/Strategy/MemberPicker.tsx` | 修改（轻） | 「组合策略」页签在编辑器内仅保留「导入成员」（去掉嵌套「查看」以规避 Modal-on-Modal）；**一并移除 `onViewCombo` prop 与 `onView` 透传/调用（v2·R-P2-1 去死参）**；其余不变。 |
| `apps/web/components/Strategy/ComboDetailModal.tsx` | 沿用（可微调） | 查看弹窗保留；「编辑」回调改为由父级打开编辑器弹窗。 |
| `apps/web/components/Strategy/DeleteComboConfirm.tsx` | 沿用 | 删除二次确认弹窗保留。 |
| `apps/web/lib/combo.ts` / `lib/comboPolicyHints.ts` / `components/Strategy/ComboTimeline.tsx` | 沿用 | 纯助手与时间线组件原样复用。 |
| `apps/web/components/Strategy/ComboEditorModal.test.tsx` | 新增 | 新建/编辑/预览/保存流 + a11y 关闭契约 + 禁用态。 |
| `apps/web/components/Strategy/ComboStrategyDetail.test.tsx` | 新增 | 选中组合显示运行/查看/编辑/删除；回测后渲染 `ComboResult`。 |
| `apps/web/components/Strategy/ComboView.test.tsx` | 删除 | 随 `ComboView` 删除；覆盖迁移到上面两个新测试。 |
| `apps/web/lib/store.test.ts` | 修改 | 删/改 `setMode('combo')` 用例；新增 v13→v14 迁移把 `mode:"combo"`→`"backtest"` 的用例；保留 v11 comboDraft 迁移用例。 |
| `services/quant/**` | **不改** | 后端 CRUD / 组合回测分流 / run.mode="backtest" 均已就绪，零改动。 |
| `docs/plans/combo-strategy-list-and-modal/spec.md` | 新增 | 本设计文档。 |

> 阈值自查（CLAUDE 清洁码 · v2·R-P2-3 升级为硬约束）：`StrategyPanel.tsx` 经核对**现为 650 行**，已逼近 800 上限。本次新增列表合并、选中分流、`effectiveKind` 三处收口、三弹窗开关态（编辑器/详情/删除）会显著增量，故以下为**强制项**而非建议：(a) `ComboEditorModal`/`ComboStrategyDetail`/`ComboResult` 必须抽为独立文件（不得内联进 `StrategyPanel`）；(b) 改动落地后若 `StrategyPanel.tsx` 超 **750** 行，须进一步把组合相关开关态与回调抽成自定义 hook（如 `useComboPanel`）或子组件 `ComboPanelSection`，把 `StrategyPanel` 压回阈值内；(c) 单文件 ≤**800** 行、单函数 ≤**50** 行、形参 ≤**5**、嵌套 ≤**4** 层、单行 ≤**80** 列，提交前逐项自查。

---

## 四、接口设计（Interface Design）

**全部复用既有 REST，无新增 / 无变更端点。** 经 Next rewrite `/api/py/*` 代理到 FastAPI。

| Method | Path | 用途 | 入参（关键） | 出参（关键） |
|---|---|---|---|---|
| GET | `/strategies` | 列全部策略（含组合，`kind` 字段区分） | — | `Strategy[]`（含 `kind/params/builtin`） |
| POST | `/strategies` | 新建组合 | `{templateKey:"regime_rotation", name, params:{members, ...policy}}` | `201` + `Strategy`（含新 `id`，后端 `status_code=201`，v2·R-P2-4 补注） |
| PUT | `/strategies/{id}` | 编辑组合 | `{name?, params?:{members, ...policy}}` | `Strategy` |
| DELETE | `/strategies/{id}` | 删除组合 | — | `204`；内置 `409 BUILTIN_READONLY` |
| POST | `/strategies/{id}/backtest` | **回测已存组合 / 原子（同一端点）** | `{instId,bar,startTs,endTs,limit?,initialCapital?}` | `{runId, metrics, combo?}`（组合行带 `combo`） |
| POST | `/strategies/combo/preview` | 弹窗内未保存组合的预览 | `{members,policy?,instId,bar,startTs,endTs,...}` | `{runId, metrics, combo}` |

前端 `lib/api.ts` 现有 `strategies/createStrategy/updateStrategy/deleteStrategy/backtest/comboPreview` 全部可用，**本方案不新增 api 方法、不改请求 / 返回类型**（`api.backtest` 返回的 `BacktestResult` 已含可选 `combo`，由 strategy-combo-management 落地）。

**前端组件接口（新增）**

```ts
// ComboEditorModal.tsx
export default function ComboEditorModal(props: {
  open: boolean;
  editingId: number | null;             // 来自 store.comboEditingId；null=新建
  onClose: () => void;
  onSaved?: (created: Strategy | null) => void;  // 新建成功回传新行，供父级选中
}): JSX.Element | null;

// ComboStrategyDetail.tsx
export default function ComboStrategyDetail(props: {
  combo: Strategy;
  busy: boolean;
  onRunBacktest: (combo: Strategy) => void;
  onView: (combo: Strategy) => void;
  onEdit: (combo: Strategy) => void;
  onDelete: (combo: Strategy) => void;
}): JSX.Element;

// ComboResult.tsx
export default function ComboResult(props: {
  result: { metrics: Metrics; combo: ComboPreviewResult["combo"] };
  onGoCompare?: () => void;
}): JSX.Element;
```

---

## 五、数据模型（Data Model）

**无数据库表结构变更。** 组合仍是 `strategies` 表中一行（`template_key="regime_rotation"`、`params_json` 含 `members:[{templateKey,params,name?}]` + 7 个轮动标量、`builtin=false`、`kind="combo"`）。

**前端类型变更**：
- `Mode = "backtest" | "compare" | "paper"`（删除 `"combo"`）。`Run.mode: Mode` 不受影响（组合 run 落库即 `"backtest"`）。
- store 持久化 `version 13 → 14`；迁移仅做 `mode:"combo" → "backtest"` 的安全收敛。`comboDraft`（持久化）/`comboEditingId`（瞬态）形态不变。

**校验区间（前后端一致，单一真源在后端模板 `params_schema`/`ComboPolicyIn`）**：成员数 `2..8`；`rebalanceBars 4..240`、`lookbackBars 24..1000`、`minDwellBars 1..240`、`switchMarginPct 0..1`、`turnoverBps 0..50`、`selectionMetric ∈ {return,sharpe,calmar}`。前端 `clampComboPolicy`/`validateMemberCount` 夹取与禁用，后端 `validate_combo_params` 边界兜底（已存在）。

---

## 六、测试与验收标准（Testing & Acceptance Criteria）

**前端单测（vitest；组件测试须 `NODE_ENV=test`，否则 React act() 报错——见项目记忆）**

- `store.test.ts`：删除 / 改写 `setMode('combo')` 用例；新增「v13 持久化 `mode:"combo"` 经 migrate 升到 v14 后变为 `"backtest"`」；保留 v11 comboDraft 迁移用例与 comboDraft 持久化用例。
- `ComboEditorModal.test.tsx`：①新建态渲染（标题「新建组合」、空名禁用保存）；②选满 2 成员 + 填名后「保存组合」→ `createStrategy` 入参正确（`templateKey/name/params.members/...policy`）+ `onSaved` 被调；③编辑态由 `editingId` 回填成员 / 参数 / 名称，「保存修改」→ `updateStrategy` 入参正确；④「运行组合回测」→ 预览成功渲染 `ComboResult`（年化 / 时间线），失败显错误态；⑤a11y：`Esc` 与点遮罩触发 `onClose`、焦点陷阱在弹窗内。
- `ComboStrategyDetail.test.tsx`：渲染摘要；点「运行回测 / 查看 / 编辑 / 删除」分别触发对应回调；不渲染原子参数网格与「另存为」。
- `StrategyPanel`（可在现有或新增测试覆盖）：回测模式列表含用户组合（带「组合」徽标）且与原子同级；实时模式列表不含组合；选中组合隐藏「高级回测」开关；选中组合回测后绩效页签渲染 `ComboResult`；从回测（选中组合）切到实时模式时选择回落首个原子策略。**（v2·R-P1-2 新增）当 `backtestKind==="walkforward"`（全局高级回测）且选中组合时，右栏渲染绩效/成交/历史页签（含 `ComboResult`）而非 `<WalkForwardResults>`，且组合仍自动触发普通回测**（验证 `effectiveKind` 在「开关渲染 / 执行路径 / 结果区渲染分支 / 自动回测守卫」四处一致收口）。

**后端测试**：本方案零后端改动，沿用既有 `test_combo_api.py` / `test_combo_engine.py` 回归（`POST /strategies/{id}/backtest` 对组合返回含 `combo`、CRUD、内置 409）应保持全绿，不新增。

**验收清单（Definition of Done 对齐）**

1. K 线图上方模式页签只剩「回测 / 对比 / 实时」，无「组合」。
2. 回测模式下，右侧「交易策略」列表同时展示原子策略与用户组合（组合带「组合」徽标、与原子同级）；选中组合点「运行回测」可正常回测，左图绘出该组合净值，绩效区展示年化 / 时间线 / 分体制贡献。
3. 实时模式列表不含组合；选中组合时「高级回测」开关不可见（组合仅普通回测）。**（v2·R-P1-2）** 即便全局处于「高级回测」，选中组合后右栏仍显示组合普通回测绩效（含年化/时间线/分体制）而非 Walk-Forward 面板。
4. 点「+ 新建组合」弹出屏幕居中的编辑弹窗完成新建；编辑 / 查看 / 删除分别经弹窗与二次确认；弹窗满足 a11y 契约（焦点陷阱 / `Esc` / 遮罩 / 焦点恢复）且明暗主题正常、主操作 sticky 常驻。
5. 新建 / 编辑 / 删除后列表即时刷新；保存后刷新页面组合仍在（持久化）；老用户本地 `mode:"combo"` 迁移后落在「回测」。
6. 不改 DB 表结构、不动后端；`pnpm test`（前端 vitest + 后端 pytest 离线）通过；前端 `tsc` 无误（`Mode` 收窄后无残留 `"combo"` 引用）。

---

## 七、风险与缓解（Risks & Mitigations）

| 风险 | 等级 | 缓解 |
|---|---|---|
| **删除 `Mode` 的 `"combo"` 后有残留引用导致 `tsc` 报错** | 中 | 全仓 grep 已定位 **5 处源码**（`types.ts`/`ChartToolbar.tsx`/`store.ts`/`page.tsx`/**`ChartWorkspace.tsx`**，v2·R-P1-1 补全）+ `StrategyPanel.tsx`（整支删除）+ `store.test.ts`；逐处改净；CI `tsc` 兜底。注意 `s.kind === "combo"`（`CompareView.tsx:108`、`combo.ts:108`）是 `Strategy.kind` 非 `Mode`，保留不动。 |
| **老用户本地持久化 `mode:"combo"` 落到已删视图** | 中 | `version 13→14` 迁移把 `"combo"`→`"backtest"`；`partialize` 内 `mode` 仍持久化，迁移后稳定。 |
| **选中组合后切到实时（paper）模式，组合不在 paper 列表却仍 selected** | 中 | 选中合法性守门按「当前模式 `listStrategies`」校验，不在则回落首个原子，避免对组合误启动 paper。 |
| **组合走高级回测（walk-forward）后端不支持** | 中 | 选中组合隐藏「高级回测」开关 + `effectiveKind="simple"` 强制普通回测；自动回测 effect 守卫 `effectiveKind==="simple"`。 |
| **编辑器内再开详情弹窗造成 Modal-on-Modal（同层 z-index / 焦点陷阱冲突）** | 中 | 编辑器内 `MemberPicker` 组合页签只留「导入成员」；查看详情仅在列表选中态提供。 |
| **拆分后 `ComboView` 删除遗漏内联子组件（NameField/PolicyEditor/ComboResult）** | 低 | 文件计划显式：NameField/PolicyEditor 搬进 `ComboEditorModal`，ComboResult 抽到 `ComboResult.tsx`；删除前确认无其它 import。 |
| **写后列表不刷新** | 低 | create/update/remove 三 mutation `onSuccess` 统一 `invalidateQueries(["strategies"])`。 |
| **`StrategyPanel` 体量膨胀超阈值** | 低 | 列表详情拆 `ComboStrategyDetail`、编辑器拆 `ComboEditorModal`、结果拆 `ComboResult`，单文件守 ≤800 行。 |
| **组合并入主列表后排序 / 视觉混淆** | 低 | 原子在前、组合在后，组合行「组合」徽标；不改原子行视觉。 |

---

## 八、实施顺序建议（供开发节点参考）

1. `lib/types.ts` 收窄 `Mode` + `app/page.tsx` `singleRunMode` + `ChartToolbar` 删页签 + **`ChartWorkspace.tsx` 删 `mode === "combo"` 空态分支（v2·R-P1-1）** + `store.ts` `setMode`/迁移（先让 `tsc` 通过、模式消失；此步后立即跑一次 `tsc` 确认无残留 `"combo"` Mode 引用）。
2. 抽 `ComboResult.tsx`（从 `ComboView` 平移，先不删 `ComboView`）。
3. 新增 `ComboEditorModal.tsx`（搬入编辑器 + create/update/preview mutation + 编辑态回填）。
4. 新增 `ComboStrategyDetail.tsx`。
5. 改 `StrategyPanel.tsx`：列表合并 + 选中分流 + 「+ 新建组合」 + 弹窗开关 + 高级回测限制 + 选中守门 + 组合结果接 `ComboResult`；`StrategyList.tsx` 加「组合」徽标。
6. 删除 `ComboView.tsx` / `SavedCombosList.tsx` / `ComboView.test.tsx`；`MemberPicker` 去嵌套「查看」。
7. 补 `ComboEditorModal.test.tsx` / `ComboStrategyDetail.test.tsx`，改 `store.test.ts`；跑 `pnpm test` + `tsc` 收尾。

> 全程遵循项目 Clean Code 阈值（文件 ≤800 行 / 函数 ≤50 行 / 形参 ≤5 / 嵌套 ≤4），错误显式传播、不假数据（C1），后端零改动、持久化与密钥口径不变。

---

## 九、评审结论（Review Verdict · v2）

**结论：有条件通过（Approved with conditions）。**

方案根基稳健、调研扎实：经逐文件核对，spec 对前后端现有架构的事实陈述绝大多数准确；改动克制（零后端、零表结构、复用 `Modal` 与既有 mutation、按职责抽出 `ComboEditorModal`/`ComboStrategyDetail`/`ComboResult` 并删除 `ComboView`/`SavedCombosList` 两个僵尸文件），与既有约定一致，可直接进入开发。评审已就地修复 2 处 P1 与 4 处 P2（详见「评审记录」），文档升级为 v2，**无遗留 P0 / P1**。

放行附带以下落地条件（开发节点 Subtask #2 须逐条遵守，验收 Subtask #3 须逐条核对）：

1. **（来自 R-P1-1）** 必须改 `ChartWorkspace.tsx:195` 的 `mode === "combo"` 空态分支；步骤 1 完成后立即 `tsc`，确认全仓无残留 `"combo"` 作为 `Mode` 字面量的引用（`s.kind === "combo"` 属 `Strategy.kind`，保留）。
2. **（来自 R-P1-2）** `effectiveKind = isCombo(selected) ? "simple" : backtestKind` 必须在**四处**一致收口：开关渲染、执行路径、**结果区渲染分支（line 418）**、自动回测 effect 守卫与依赖；并补上「全局 walkforward + 选中组合 → 仍显示组合普通回测绩效」的测试。
3. **（来自 R-P2-1）** 删除 `MemberPicker` 的「查看」按钮时一并去掉 `onViewCombo` prop 与透传，不留死参。
4. **（来自 R-P2-3）** `StrategyPanel.tsx`（现 650 行）落地后若超 750 行，须进一步抽 hook / 子组件压回阈值；三组件抽出为强制项。
5. **（通用）** 后端零改动；`store` 持久化 `version 13 → 14` 仅做 `mode:"combo" → "backtest"` 安全收敛；提交前 `pnpm test`（前端 vitest + 后端 pytest 离线）全绿、`tsc` 无误。

> 满足上述 5 条即视为完整落地本方案。

---

（评审完 · v2 · 方案评审节点 Subtask #1）

---

## 实施过程发现的方案缺陷（Issues Found During Implementation · Subtask #2）

> 结论：**未发现 P0/P1 级方案缺陷**；方案对前后端架构的事实陈述与改动边界经落地全部成立。`tsc --noEmit` 0 错、`next build` 编译通过、前端 `vitest run` 全绿（39 文件 / 304 用例），后端零改动。以下为两处实现细节记录，供 Subtask #3 Review 对照（均不偏离方案意图）：

1. **新增文件 `components/Strategy/useComboPanel.tsx`（条件 #4 触发，已在方案内授权）**：三组件抽出后 `StrategyPanel.tsx` 仍达 **780 行**，超 §三/§九条件 #4 的 **750 行硬阈值**。遵方案明文「须进一步把组合相关开关态与回调抽成自定义 hook（如 `useComboPanel`）」，将三弹窗开关态（编辑器/详情/删除）+ `removeCombo` mutation + 弹窗渲染抽入 `useComboPanel` hook，`StrategyPanel.tsx` 压回 **720 行**（≤750）。该文件虽未列于 §三「文件变更计划」表，但属方案条件 #4 显式授权的产物，非超范围新增。

2. **`ComboEditorModal` 的编辑态回填位置（行为等价，接口一致的实现选择）**：§2.5 文字描述「编辑入口由父级 `loadForEdit(combo)`（回填成员/参数/名称 + 置 `comboEditingId`）后开弹窗」。实现按 §四已声明的组件接口 `editingId: number | null` 收口为：父级仅 `setComboEditingId(id)` + 开弹窗，由 `ComboEditorModal` 在「关→开」瞬态、且 `strategies` 已就绪时一次性从 `editingId` 自回填成员/参数/名称（`resolveMemberIds`/`extractComboPolicy`/`name`）。行为与方案完全一致，且更贴合 `editingId` 接口、对 `strategies` 异步加载与写后 `invalidate` 刷新均无竞态（不会把用户正在编辑的内容清空）。新建态由弹窗在开启瞬态清空草稿（等价旧 `resetEditor` 经「+ 新建组合」触发）。
