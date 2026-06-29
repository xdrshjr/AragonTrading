# 多策略对比面板：默认空选 + 顶部批量选择/操作 + 每行「详情」弹窗

> 设计文档 **v2（评审修复版）** · feature slug: `compare-batch-select-and-strategy-detail`
> 作者：方案设计节点（标准四阶段开发 · Subtask #0）
> 关联前序方案：`docs/plans/aragon-trading-platform/spec.md`（F3 多策略对比榜单原始落地）、`docs/plans/combo-strategy-list-and-modal/spec.md`（`Modal` 详情弹窗范式 + 组合并入策略列表，已落地）、`docs/plans/multi-strategy-paper-simulation/spec.md`（`PaperSelectionBar` 批量选择范式，已落地）
> 适用分支事实：以下结论均经「真实源码逐文件核对」（`apps/web/components/Strategy/CompareView.tsx`、`StrategyPanel.tsx`、`StrategyList.tsx`、`ComboDetailModal.tsx`、`StrategyDetail.tsx`、`components/ui/index.tsx`、`lib/compare.ts`、`lib/types.ts`、`lib/api.ts`、`lib/store.ts`、`app/page.tsx`）确认，可直接据此落地。

---

## 评审记录（Review Notes · v2）

> 评审人：方案评审节点（标准四阶段开发 · Subtask #1）。评审维度：**可行性 / 完整性 / 一致性 / 规模适配**。
> 评审方式：对 spec 引用的真实源码逐文件复核（`CompareView.tsx`（220 行）、`lib/compare.ts`、`ComboDetailModal.tsx`、`StrategyDetail.tsx`、`components/ui/index.tsx`、`lib/types.ts`、`app/page.tsx:178-192`、`StrategyPanel.tsx:439/679`、`compare.test.ts`）。结论：spec 对现状的描述基本准确——入口路由、缓存键 `["compare", instId, bar, backtestStartTs, backtestEndTs]`、`colorForIndex` 配色、`Modal` a11y 契约、`PaperSelectionBar` 范式、`Button/Badge` 变体、`StrategyKind`/`Strategy`/`CompareItem`/`CompareResult` 类型、`ComboDetailModal` 私有分区均与源码一致，抽出 / 复用方案可落地。
> 下列 **P1 已就地修复**并反映到正文（§2.1 / §2.4 / §2.7 / §2.8 / §三 / §五 / §六 / §七）；P2 作为实施期需确认项列入文末「评审结论」放行条件。无 P0。

| 编号 | 维度 | 严重度 | 问题 | 处置 |
|---|---|---|---|---|
| C-P1-1 | 可行性 / 完整性 | **P1** | `CompareView` 现以**非响应式** `qc.getQueryData(compareKey)` 读取对比结果，当前仅靠 `useMutation` 状态切换触发重渲染才「碰巧」刷新榜单。新增的「清空结果」只调用 `qc.setQueryData(compareKey, null)`，**不会触发 `CompareView` 重渲染**——榜单不消失、`hasResult` 不更新，需求 2 的「批量操作·清空结果」实际失效。 | **已修复**：`CompareView` 改用与 `app/page.tsx:178-183` 完全一致的响应式订阅 `useQuery<CompareResult \| null>({ queryKey: compareKey, queryFn: () => null, enabled: false, initialData: null })` 读取结果；运行 / 清空结果均响应式刷新。既修缺陷又对齐既有模式。 |
| C-P1-2 | 一致性 | **P1** | 筛选语义自相矛盾：§2.4 称 `factor` 策略「仅并入全部」（即不出现在「原子」「组合」任一标签），而 §六 测试②又写 `atomic` 过滤「排除 combo」（即会包含 factor）。两定义冲突；且「factor 仅在全部可见」会让因子策略切到「原子」标签时彻底消失（无「因子」标签兜底），形成可发现性陷阱。 | **已修复**：统一定为 `atomic = kind !== "combo"`（原子 + 因子，均可在「原子」标签下被发现）、`combo = kind === "combo"`、`all` 为全集。§2.4 / §2.7 / §六 同步改写。 |
| C-P1-3 | 一致性 / 完整性 | **P1** | `selectAllEligible(list, cap)` 签名只吃「筛选后列表」，无法纳入「已选集合」；启用筛选时点「全选」会用筛选集合**整体替换**选择、丢弃其它筛选下的已选项，违背 §2.4「切筛选不清空已选」的意图，且与已带 `current` 形参的 `invertSelection` 不一致。 | **已修复**：改为并集语义 `selectAllEligible(current, visible, cap?)`——把当前筛选下可用项并入已选，按稳定顺序夹取至 12；与 `invertSelection` 形参对齐。见 §2.7 / §六。 |
| C-P2-1 | 可行性 | P2 | 删除自动全选 `useEffect` 后，`CompareView` 第 4 行 `import { useEffect, useState }` 的 `useEffect` 变为未使用（改 C-P1-1 后该组件不再需要 `useEffect`），可能令 `tsc --noEmit`（`noUnusedLocals`）/ ESLint 报错，违反 DoD「tsc 无误」。 | 已在 §三 change plan 注明：删 effect 同时移除未用的 `useEffect` 导入。实施期确认。 |
| C-P2-2 | 完整性 | P2 | 待删自动全选块实际为第 **32–44** 行（含 `// 默认全选可用的内置策略` 注释与行尾 `eslint-disable`），spec 原文写「33–44」。 | 正文 §2.3 已订正：以「删除整个含注释 + `eslint-disable` 的 effect 块」为准，勿按字面行号误删相邻代码。 |
| C-P2-3 | 完整性 / 顶级设计 | P2 | sticky 工具条能否「常驻」取决于最近的滚动祖先：若 `CompareView` 的直接滚动容器不是右栏本身，`sticky top-0` 不生效，「常驻」退化为「随列表滚走」。 | 实施期需确认 `StrategyPanel` 右栏 `overflow` 滚动容器层级；若 sticky 不生效则提升工具条至滚动容器内 / 接受非 sticky（「上方有工具条」仍满足需求 2）。已记入 §2.4 与放行条件。 |
| C-P2-4 | 完整性 | P2 | spec 未明确把运行按钮迁入工具条后 `compare.isError` 错误横幅的落点。 | 正文 §2.4 已注明：错误横幅保留在 `CompareView` 主体（工具条下方、榜单上方），与现状一致。 |
| OK-1 | 可行性 | — | 核对通过：`Button` 变体 `default/primary/ghost/danger` + `size sm/md`、`Badge` 色调 `neutral/long/short/accent`、`Modal` a11y 契约、`colorForIndex`、`compareKey`、关键类型、`ComboDetailModal` 私有分区均与 spec 一致，抽出 / 复用可行。 | 无需改动。 |
| OK-2 | 规模适配 | — | 范围克制（零后端 / 零表结构 / 零 store 持久化、纯助手下沉 + 同名测试、复用 `Modal`/抽出组合分区去重），CLAUDE 清洁码阈值自查到位，未过度设计。 | 维持。 |

---

## 一、概述（Overview）

「交易策略」右侧面板在 K 线图上方「回测 / 对比 / 实时」三模式中，**对比（`mode==="compare"`）** 进入的是 `CompareView`：用户在一份策略清单里勾选若干策略，点「运行对比」对同一标的 / 周期一键回测，得到一张按收益率 / 夏普 / 胜率 / 最大回撤 / 交易数可排序的横向榜单，同时左图叠加各策略归一化净值曲线（配色与榜单色块一致）。本方案要在**不改任何后端、不动数据库表结构、不新增 REST 端点**的前提下，把这个对比操作界面打磨成「美观优雅、顶级设计、符合人机交互最佳实践」的形态，并补齐三项明确的产品诉求。

需求拆解为三件事：(1) **默认空选**——当前 `CompareView` 进入时由一个 `useEffect` 自动全选所有「内置且可用」的策略（至多 12 个），与用户预期相悖；改为进入后**什么都不选**，由用户主动挑选；(2) **顶部批量选择 / 批量操作工具条**——在清单上方提供一个常驻（sticky）工具条，承载「已选计数 + 分类筛选 + 全选 / 反选 / 清空（批量选择）+ 运行对比 / 清空结果（批量操作）」，让多选与执行集中、可达、不被长列表滚动埋没；(3) **每行「详情」按钮 + 详情弹窗**——清单每一行最右侧新增一个命名为「详情」的按钮，点击弹出一个屏幕居中的模态弹窗，按策略类型（原子 / 组合 / 因子组合得分）只读地展示该策略的完整内容（描述、参数、组合成员与轮动参数、因子成员与训练溯源、以及当次对比绩效），不在对比语境里夹带编辑动作。

之所以改动可以很克制：对比面板本身已是「选择 → 运行 → 榜单 + 左图叠加」的闭环，后端 `POST /strategies/compare` 与 `GET /strategies` 均已就绪，配色 / 叠加经 `lib/compare.ts` 的 `colorForIndex` 与 `app/page.tsx` 的 `compareOverlays` 串联（详见 §2.1）。因此主体是前端三件事：删除自动全选的 effect、抽出一个对比专用的批量工具条与策略行组件、新增一个按 `kind` 自适应的只读详情弹窗（复用既有 `Modal` a11y 原语与组合详情分区，杜绝重复实现）。所有可被纯函数化的选择 / 筛选逻辑下沉到 `lib/compare.ts` 并配同名单测，组件只消费纯逻辑，符合项目「纯助手 + 同名测试」的既有约定。

---

## 二、技术设计（Technical Design）

### 2.1 当前架构事实（核对结论，复用不重造）

- **入口与路由**：K 线图上方 `ChartToolbar` 的 `MODES` 含「对比 compare」；`StrategyPanel.tsx:439` 按 `mode === "compare"` 渲染 `<CompareView />`（对比模式下不渲染策略列表 / 详情 / 结果页签那一套）。`setMode("compare")` 在 `store.ts:328-334` 联动 `chartView: "compare-range"`（左图进入多策略叠加态）。
- **CompareView 现状（220 行，逐行核对）**：
  - 本地态：`selected: Set<number>`（初值 `new Set()`）、`sortKey`、`sortDir`。
  - **自动全选 effect（lines 33-44）**：`strategies` 到位且 `selected.size===0` 时，`setSelected(new Set(builtin && !unavailable 的前 12 个 id))`。**← 需求 1 要删除的就是这个 effect。**
  - **清单**：`strategies.map` 渲染为 `<label>`（checkbox + 名称 + `组合`/`内置` 徽标）；`toggle(id)` 写选择，受 `MAX_COMPARE_SELECTIONS = 12` 上限保护（`atSelectionLimit` 时未选中项 checkbox 禁用）。**无每行「详情」入口。**
  - **运行**：清单下方一个 `运行对比（N）` 主按钮，`disabled = selected.size < 2 || > 12 || compare.isPending`；旁注「至少选择 2 个策略」与「已达 12 个策略上限」。
  - **结果与配色**：`compare` mutation 成功后 `qc.setQueryData(compareKey, data)`，`compareKey = ["compare", instId, bar, backtestStartTs, backtestEndTs]`；榜单按结果原始顺序用 `colorForIndex(i)` 固定配色；`sortCompareItems` 排序；每行一个 `打开` 按钮 → `openDetail(strategyId, runId)`（切 `strategyId/runId`、`setMode("backtest")`、`setChartView("run-range")`）。
  - **（评审 C-P1-1）结果读取现为非响应式**：`CompareView.tsx:48` 用 `qc.getQueryData<CompareResult>(compareKey)` 读取，**不订阅缓存**——当前只因 mutation 状态切换重渲染才碰巧刷新榜单。这意味着新增的「清空结果」（`setQueryData(compareKey, null)`）不会触发本组件重渲染。v2 改为响应式订阅（见 §2.4 / §2.8 / §5），与 `app/page.tsx:178-183` 同口径。
- **左图叠加耦合（不可破坏）**：`app/page.tsx:178-189` 用 `compareQ = useQuery(["compare", instId, bar, backtestStartTs, backtestEndTs])` 读**同一缓存键**，`chartView === "compare-range" && mode === "compare" && compareQ.data` 时把 `compareQ.data.items.map((item,index)=>({…, color: colorForIndex(index)}))` 作为 overlays 叠加到左图。**结论**：只要 CompareView 仍把结果写入同一 `compareKey`、仍按 `items` 原始顺序 `colorForIndex(index)` 配色，叠加与配色一致性即自动保持。
- **批量选择既有范式（直接借鉴）**：`StrategyPanel.tsx` 内 `PaperSelectionBar`——一个 `rounded-[8px] border bg-[var(--surface-2)]` 的小工具条，左 `已选 N/min(total,cap)`、右 `全选当前页` / `清空全部`（ghost 按钮）。本方案的对比工具条沿用同一视觉语汇并扩展批量操作。
- **Modal 原语（a11y 契约已测）**：`components/ui/index.tsx` 的 `Modal` 提供 `role="dialog"` + `aria-modal` + `aria-labelledby` + `Esc` 关闭 + 点遮罩关闭 + 焦点陷阱 + 焦点恢复 + body 滚动锁 + Portal 居中 + `max-h-[85vh]`/`max-w-2xl`（`Modal.test.tsx` 守约）。`ComboDetailModal.tsx` 是其 header/body/footer 三段式的成熟用法范本。
- **组合详情分区可复用**：`ComboDetailModal.tsx` 内 `OverviewSection`（成员数 + 8 项轮动概览）、`MembersSection` + `MemberCard`（按 `templateKey` 回查名称、体制亲和标签）、`RotationSection`（轮动参数网格）、`SectionTitle`，以及纯助手 `extractComboPolicy`(`lib/combo.ts`)、`POLICY_*`/`SELECTION_METRIC_LABELS`(`lib/comboPolicyHints.ts`)、`regimeAffinityLabels`(`lib/regimeAffinity.ts`)。这些**当前是 `ComboDetailModal` 私有**，本方案将只读分区抽到共享文件供两处复用（§2.5）。
- **因子策略摘要可复用**：`StrategyDetail.tsx` 的 `FactorStrategyBanner` 展示 `kind==="factor"` 的内联快照（成员 + 方向徽标、`thresholds.normWindow` 推算最小所需根数、`provenance` 训练溯源）；`directionLabel` 在 `lib/factor.ts`。详情弹窗的因子分区按此**只读**呈现（去掉「在因子实验室编辑」动作，对比语境不夹带编辑）。
- **类型**：`Strategy { id, templateKey, name, description, kind?: "atomic"|"combo"|"factor", params, paramsSchema, enabled, builtin, unavailable? }`；`CompareItem { strategyId, name, runId, metrics, equity }`；`CompareResult { instId, bar, items }`（`lib/types.ts`）。

### 2.2 目标形态（改动后）

```
mode==="compare" → <CompareView/>
┌ CompareView ───────────────────────────────────────────────┐
│ 头部：多策略对比（标题 + 副标题：选 2–12 个，真实 OKX 数据）       │
│ ┌ CompareSelectionBar（sticky 顶，批量选择 + 批量操作）────────┐ │
│ │ 第一行：[全部|原子|组合]（筛选）            已选 3/12          │ │
│ │ 第二行：[全选][反选][清空]      [清空结果] [运行对比 (3)]      │ │
│ └────────────────────────────────────────────────────────┘ │
│ 策略清单（按筛选；每行 CompareStrategyRow）：                    │
│   ┌ ☑  策略名     〔组合/内置/不可用 徽标〕        [详情] ┐      │
│   └ 描述（line-clamp-2，muted）                          ┘      │
│   …                                                          │
│ 空选态：友好提示「请从下方勾选 2–12 个策略后点运行对比」          │
│ 榜单结果（运行后）：可排序表 + 每行色块 + [打开]（沿用）           │
└──────────────────────────────────────────────────────────────┘
  弹窗（Portal 居中，复用 Modal）：
   - StrategyDetailModal（按 kind 只读详情）   ← 需求 3 的核心
```

### 2.3 改动 1：默认空选（需求 1）

- **删除** `CompareView.tsx` 第 **32-44** 行的自动全选块——即整个含 `// 默认全选可用的内置策略` 注释、`useEffect(...)` 与行尾 `eslint-disable` 的代码块（评审 C-P2-2：以「删除整块」为准，勿按字面行号误删相邻代码）。`selected` 仍以 `new Set<number>()` 初始化 → 进入对比即 0 选中。
- **（评审 C-P2-1）连带清理**：删除该 effect 且 §2.1 改动落地后，`CompareView` 不再使用 `useEffect`，须从第 4 行 `import { useEffect, useState }` 移除 `useEffect`，避免 `tsc --noEmit`（`noUnusedLocals`）/ ESLint 报未使用导入（DoD 要求 tsc 无误）。
- 由于不再自动选，须补**空选态 UI**：`selected.size === 0` 时，在清单与运行区之间（或清单上方副标题处）显示 muted 提示「请从下方勾选 2–12 个策略后点『运行对比』」，并保持 `运行对比` 禁用（`< 2`）。
- 不引入任何持久化：对比选择刻意是**瞬态本地态**（每次进入都从空开始），不写入 `store`/`localStorage`（与需求「默认进来什么都不选」一致）。

### 2.4 改动 2：顶部批量选择 / 批量操作工具条（需求 2）

新增 `components/Strategy/CompareSelectionBar.tsx`，在 `CompareView` 头部副标题下方 sticky 渲染（`sticky top-0 z-10` + `bg-[var(--bg)]`/`backdrop` 视需要），承载两类能力：

**批量选择（selection）**
- **分类筛选**（segmented，复用 `ui/Tabs` 的 `underline` 或 segmented 变体，或自绘 pill 组）：`全部 | 原子 | 组合`。筛选只影响清单可见集合与「全选 / 反选」的作用域，不影响已选集合的保留（切筛选不清空已选）。**（评审 C-P1-2）筛选语义统一**：`全部 = 全集`、`组合 = kind==="combo"`、`原子 = kind !== "combo"`（即 atomic + factor 一并归入「原子」标签），确保因子策略在「原子」与「全部」均可被发现，避免落入「无标签兜底」的盲区。本期定为 `全部 | 原子 | 组合` 三档；若后续因子数量增多可再加「因子」专属标签。
- **已选计数**：`已选 {selected.size}/{MAX_COMPARE_SELECTIONS}`（tabular），并按状态显示上下文提示（`< 2` → 「至少 2 个」；`>= 12` → 「已达上限」）。
- **全选**：把**当前筛选下的可用策略**（`!unavailable`）**并入已选**（并集语义，不丢弃其它筛选下的已选项——评审 C-P1-3），按稳定顺序夹取至上限 12；若并集 > 12，保留前 12 并提示「已达 12 个上限，未全部选入」。复用 `lib/compare.ts` 纯助手 `selectAllEligible(current, visible, cap)`（§2.7）。
- **反选**：在**当前筛选下的可用集合**内反转选择，结果同样受 12 上限夹取（反选后若超限，按稳定顺序保留前 12 并提示）。复用纯助手 `invertSelection`。
- **清空**：`setSelected(new Set())`。

**批量操作（actions）**
- **运行对比 (N)**（primary）：触发既有 `compare.mutate()`；`disabled = selected.size < 2 || selected.size > 12 || compare.isPending`，loading 文案「对比中…」。这是工具条的主操作，移到顶部常驻（替代原清单下方按钮；下方不再重复放置，避免双主操作）。
- **清空结果**（ghost，仅当存在对比结果时显示）：`qc.setQueryData(compareKey, null)` 清空榜单与左图叠加（page.tsx `compareOverlays` 在 `compareQ.data` 为空时自动不叠加）。让用户能一键回到「未对比」干净态。**（评审 C-P1-1）此动作要生效，`CompareView` 必须响应式读取结果**——v2 把 `qc.getQueryData` 改为 `useQuery<CompareResult | null>({ queryKey: compareKey, queryFn: () => null, enabled: false, initialData: null })`（与 `app/page.tsx:178-183` 同模式），否则 `setQueryData(null)` 不会触发本组件重渲染、榜单不会消失。

**版式（HCI 最佳实践）**
- 工具条用一个 `rounded-[10px] border border-[var(--border)] bg-[var(--surface-2)]` 卡片，内分两行：第一行 `[筛选 segmented] …(spacer)… [已选 N/12]`；第二行 `[全选][反选][清空] …(spacer)… [清空结果][运行对比 (N)]`。两行在窄栏下垂直堆叠不溢出。
- 主操作（运行对比）右下角常驻、视觉最重；破坏性 / 次要操作（清空 / 清空结果）用 ghost 弱化。
- 全部沿用设计 token（`--surface-2/--border/--accent/--accent-soft/--muted/--text`），明暗主题零适配；动效 `transition-* duration-150 ease-smooth`，克制。
- 计数与提示用 `aria-live="polite"`，多选 / 全选 / 反选时读屏可感知（a11y）。
- **（评审 C-P2-4）错误反馈落点**：运行按钮迁入工具条后，`compare.isError` 错误横幅仍保留在 `CompareView` 主体（工具条下方、榜单上方），与现状一致，不并入工具条卡片。
- **（评审 C-P2-3）sticky 落地前置**：`sticky top-0` 仅在最近滚动祖先为右栏自身时生效；实施前确认 `StrategyPanel` 右栏 `overflow` 容器层级，否则「常驻」会退化为「随列表滚走」（缓解见 §七 风险表与文末放行条件）。

### 2.5 改动 3：每行「详情」按钮 + 详情弹窗（需求 3 · 顶级设计）

**(a) 清单行改造 → 新增 `components/Strategy/CompareStrategyRow.tsx`**

把现 `<label>`（整行点击即 toggle，无法再塞独立按钮）改造为一个 `<div>` 行容器，明确划分两个交互区：

```
┌ row（div, rounded-[10px] border, hover:border-accent, 选中:border-accent + bg-accent-soft）┐
│ [☑ checkbox] [名称（truncate, font-semibold）]     〔组合/内置/不可用 badge〕  [详情(ghost,sm)] │
│ 描述（mt-0.5 line-clamp-2 text-[11px] muted）                                              │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

- **选择区**：checkbox + 名称 + 描述包在一个 `<label>`（或带 `onClick` 的可点区）内，点击 toggle 选择；checkbox `accent-[var(--accent)]`，`disabled = s.unavailable || (!checked && atSelectionLimit)`。
- **详情按钮**：行最右一个 `<Button variant="ghost" size="sm">详情</Button>`，`onClick={(e)=>{ e.stopPropagation(); onOpenDetail(s); }}`——**务必 `stopPropagation` 且不置于 label 内**，确保「点详情不会顺带勾选 / 取消勾选」（关键交互正确性，见风险表）。详情按钮对 `unavailable` 策略**仍可点**（详情是只读信息，用户需要据此理解为何不可用）。
- 视觉与 `StrategyList.tsx` 行保持同语汇（圆角、边框、hover、选中态、徽标），让对比清单与回测清单观感统一。
- Props：`{ strategy: Strategy; checked: boolean; disabled: boolean; onToggle: (id:number)=>void; onOpenDetail: (s:Strategy)=>void }`。

**(b) 只读详情弹窗 → 新增 `components/Strategy/StrategyDetailModal.tsx`**

按 `kind` 自适应、**纯只读**（不含运行 / 编辑 / 删除）的策略详情，复用 `Modal` 原语：

- **Props**：`{ strategy: Strategy; strategies: Strategy[]; compareItem?: CompareItem; onClose: () => void }`。`strategies` 供组合成员按 `templateKey` 回查名称；`compareItem` 为该策略在「当次对比」中的榜单项（存在则展示「本次对比绩效」分区）。
- **结构**：header（名称 + `原子/组合/因子` 类型徽标 + `内置`/`不可用` 徽标 + 关闭）/ body（`overflow-y-auto`，分区）/ footer（关闭）。`labelledBy` 指向 header 标题 id（a11y）。
- **body 分区（按 kind）**：
  - **通用**：策略描述段（`strategy.description`）；一个「概览」网格（类型、模板键 `templateKey`、是否内置、是否启用 / 可用）。
  - **atomic**：从 `paramsSchema` 渲染**只读参数网格**（`label` + 当前值 `params[key] ?? default`；`bool→是/否`、`enum→原值`、数值 `tabular`）；若声明高周期参考（`htf_trend_filter !== false && typeof htf_bar === "string"`）展示「参考周期：{htf_bar}」徽标（与 `StrategyDetail` 同口径）。
  - **factor**（`kind==="factor"`）：因子成员快照（`params.members` 的 `factorKey·方向`，方向用 `directionLabel` + `Badge` long/short）；最小所需根数提示（`Number(thresholds.normWindow ?? 200) + 60`）；训练溯源（`params.provenance` 存在时展示 instId/bar/区间/前瞻 + 样本内外自查提示）。皆为 `FactorStrategyBanner` 的**只读化**复刻（去编辑按钮）。
  - **combo**（`kind==="combo"`）：复用 §2.6 抽出的 `OverviewSection` + `MembersSection` + `RotationSection`（成员数 / 选优指标 / 体制门控 / 再平衡 / 回看 / 持有 / 迟滞 / 换手 概览 + 成员卡 + 轮动参数网格）。**不**含 `ComboDetailModal` 的 `PerformanceSection`（懒回测）——对比面板自身的「运行对比」已产出绩效，详情弹窗保持纯信息。
  - **本次对比绩效（可选）**：`compareItem` 存在时，追加一个分区，以 `MetricsCards` 或紧凑指标条展示该策略当次对比的 `收益率/夏普/胜率/最大回撤/交易数`（复用 `fmtPct`/`fmtNum`），并提供「打开该策略回测」入口（等价现榜单行「打开」：`setStrategyId/setRunId/setMode("backtest")/setChartView("run-range")`）——但此入口为可选，若实现复杂可省略，核心是只读展示。
- **去孤岛**：分区标题统一用 `SectionTitle`（`text-xs font-semibold uppercase tracking-wide text-[var(--muted)]`），与 `ComboDetailModal`/`OkxAccountDetailModal` 同语汇。

**(c) `CompareView` 集成**

- 新增本地态 `detail: Strategy | null`（详情弹窗目标）与 `filter: CompareFilter`（筛选）。
- 清单改为 `filterCompareStrategies(strategies, filter).map(s => <CompareStrategyRow … onOpenDetail={setDetail} />)`。
- 末尾条件渲染 `{detail && <StrategyDetailModal strategy={detail} strategies={strategies} compareItem={result?.items.find(it=>it.strategyId===detail.id)} onClose={()=>setDetail(null)} />}`。
- 顶部渲染 `<CompareSelectionBar … />`，把 `selected`、`filter`、计数、各批量回调、`运行对比`/`清空结果` 接好；删除清单下方原运行按钮区（迁入工具条）。
- 保留：`compareKey` 写缓存、`sortCompareItems` 榜单、`colorForIndex` 配色、每行 `打开`。

### 2.6 抽出 `ComboDetailSections.tsx`（只读组合分区，两处复用）

把 `ComboDetailModal.tsx` 内现为私有的只读分区 `OverviewSection`、`MembersSection`(`MemberCard`)、`RotationSection`、`SectionTitle`、`formatPolicyValue` **平移**到新文件 `components/Strategy/ComboDetailSections.tsx` 并 `export`；`ComboDetailModal.tsx` 改为从该文件 `import`（其 `PerformanceSection` 与 footer 编辑 / 删除动作留在 `ComboDetailModal`）。`StrategyDetailModal` 的 combo 分区复用同一组导出，**杜绝重复实现**（CLAUDE §四 / §8.8 去孤岛 / 去重复）。平移须保持 JSX 与 props 完全等价，仅改变文件归属（行为零变化，`ComboDetailModal` 渲染快照不变）。

### 2.7 纯助手下沉 `lib/compare.ts`（可测、组件只消费）

在既有 `lib/compare.ts`（含 `sortCompareItems`/`normalizeEquity`/`colorForIndex`）追加：

```ts
import type { Strategy, StrategyKind } from "./types";

export type CompareFilter = "all" | "atomic" | "combo";
export const MAX_COMPARE_SELECTIONS = 12;   // 单一真源，CompareView 与工具条共享
export const MIN_COMPARE_SELECTIONS = 2;

// 可入选 = 非 unavailable（与现 checkbox disabled 口径一致）
export function isCompareEligible(s: Strategy): boolean;
// 当前筛选下的可见清单（保持原顺序）。语义（评审 C-P1-2）：
//   all=全集；combo=kind==="combo"；atomic=kind!=="combo"（原子 + 因子，均归「原子」标签）
export function filterCompareStrategies(list: Strategy[], filter: CompareFilter): Strategy[];
// 全选（评审 C-P1-3）：把当前筛选下可用项「并入」current（并集，不丢其它筛选下已选项），
// 按稳定顺序夹取至 cap；返回 id 数组。与 invertSelection 形参对齐。
export function selectAllEligible(current: Set<number>, visible: Strategy[], cap?: number): number[];
// 在（筛选后）可用集合内反转选择，受 cap 夹取；current 之外的非可见项保持原选择
export function invertSelection(current: Set<number>, visible: Strategy[], cap?: number): number[];
// 类型中文标签：atomic→原子 / combo→组合 / factor→因子
export function compareKindLabel(kind?: StrategyKind): string;
```

组件层 `全选`/`反选`/`清空` 仅调用上述纯函数并 `setSelected`。同名 `compare.test.ts` 已存在，追加这些函数的用例（含上限夹取、筛选作用域、反选边界、空集合退化）。

### 2.8 关键时序（Sequence）

- **进入对比**：`setMode("compare")` → `chartView="compare-range"` → `<CompareView/>` 挂载 → `selected` 为空 → 工具条显「已选 0/12 · 至少 2 个」，`运行对比` 禁用，清单空选提示。
- **批量选择**：点「全选」→ `selectAllEligible(selected, filtered, 12)`（并集 + 夹取）→ `setSelected` → 计数刷新；点「反选」→ `invertSelection(selected, filtered, 12)` → 刷新；点「清空」→ 置空。
- **运行对比**：点「运行对比 (N)」→ `compare.mutate({strategyIds:[...selected], instId, bar, startTs, endTs, limit:1000})` → `onSuccess` `qc.setQueryData(compareKey, data)` → 榜单渲染 + `page.tsx` `compareOverlays` 据同键叠加左图（配色 `colorForIndex(index)` 对齐）。
- **清空结果**：点「清空结果」→ `qc.setQueryData(compareKey, null)` → 榜单消失（依赖 §2.4 的响应式 `useQuery` 读取，评审 C-P1-1）+ 左图叠加移除（page.tsx 在无数据时不叠加）。
- **查看详情**：点某行「详情」（`stopPropagation`，不改选择）→ `setDetail(s)` → `StrategyDetailModal` 按 `s.kind` 渲染只读分区（combo 复用抽出分区、factor 复用快照只读化、atomic 渲染参数网格）；`compareItem` 命中则附「本次对比绩效」；`Esc`/点遮罩/「关闭」→ `setDetail(null)`，焦点恢复到「详情」按钮。

---

## 三、文件 / 模块变更计划（File / Module Change Plan）

| 文件 | 新增/修改/删除 | 一句话意图 |
|---|---|---|
| `apps/web/components/Strategy/CompareView.tsx` | 修改 | 删除自动全选 effect（默认空选）并移除随之未用的 `useEffect` 导入（评审 C-P2-1）；**改用响应式 `useQuery<CompareResult\|null>({queryKey:compareKey, queryFn:()=>null, enabled:false, initialData:null})` 读取对比结果（同 `app/page.tsx`），以支持「清空结果」（评审 C-P1-1）**；接入 `CompareSelectionBar` + `CompareStrategyRow` + `StrategyDetailModal`；新增 `detail`/`filter` 本地态；迁出清单下方运行按钮到工具条；保留缓存写入 / 榜单排序 / 配色 / 「打开」。 |
| `apps/web/components/Strategy/CompareSelectionBar.tsx` | 新增 | 顶部 sticky 批量工具条：分类筛选 + 已选计数 + 全选/反选/清空（选择）+ 运行对比/清空结果（操作）；`aria-live` 计数。 |
| `apps/web/components/Strategy/CompareStrategyRow.tsx` | 新增 | 对比清单单行：选择区（checkbox + 名称 + 描述）+ 行尾「详情」按钮（`stopPropagation`，点详情不改选择）+ 徽标；视觉对齐 `StrategyList` 行。 |
| `apps/web/components/Strategy/StrategyDetailModal.tsx` | 新增 | 按 `kind`（atomic/combo/factor）只读自适应详情弹窗，复用 `Modal`；组合复用抽出分区、因子复用快照只读化、原子渲染只读参数网格；可选「本次对比绩效」。 |
| `apps/web/components/Strategy/ComboDetailSections.tsx` | 新增（抽出） | 从 `ComboDetailModal` 平移只读分区 `OverviewSection/MembersSection/MemberCard/RotationSection/SectionTitle/formatPolicyValue` 并导出，供两处复用去重。 |
| `apps/web/components/Strategy/ComboDetailModal.tsx` | 修改（轻） | 改为从 `ComboDetailSections.tsx` 导入只读分区（删除本文件内同名私有实现）；`PerformanceSection` 与编辑/删除 footer 不变，渲染等价。 |
| `apps/web/lib/compare.ts` | 修改 | 追加 `CompareFilter`、`MAX/MIN_COMPARE_SELECTIONS`、`isCompareEligible`、`filterCompareStrategies`、`selectAllEligible`、`invertSelection`、`compareKindLabel` 纯助手（单一真源）。 |
| `apps/web/lib/compare.test.ts` | 修改 | 追加上述纯助手用例（上限夹取 / 筛选作用域 / 反选边界 / 空集合退化）。 |
| `apps/web/components/Strategy/CompareSelectionBar.test.tsx` | 新增 | 计数渲染、全选/反选/清空回调、运行对比禁用阈值（<2/>12/pending）、清空结果仅有结果时出现。 |
| `apps/web/components/Strategy/StrategyDetailModal.test.tsx` | 新增 | 三种 kind 分区正确渲染；`compareItem` 命中显绩效；a11y 关闭（Esc/遮罩）+ 焦点陷阱（复用 Modal 契约）。 |
| `apps/web/components/Strategy/CompareView.test.tsx` | 新增（或并入现有） | 进入默认 0 选；点详情不改选择；运行对比写入缓存键；空选态提示。 |
| `services/quant/**` | **不改** | `GET /strategies`、`POST /strategies/compare` 均已就绪，零后端改动、零表结构变更。 |
| `docs/plans/compare-batch-select-and-strategy-detail/spec.md` | 新增 | 本设计文档。 |

> 阈值自查（CLAUDE 清洁码）：`CompareView.tsx` 现 220 行，删 effect、迁工具条后增量受控，预计仍 < 300 行；`StrategyDetailModal.tsx` 因三 kind 分支须留意，combo 分区已抽出复用、factor/atomic 分区简短，预计 < 250 行；若任一文件逼近 800 行须再拆子组件。单文件 ≤800 行、单函数 ≤50 行、形参 ≤5（详情弹窗 props 4 个 OK）、嵌套 ≤4 层、单行 ≤80 列，提交前逐项自查。

---

## 四、接口设计（Interface Design）

**全部复用既有 REST，无新增 / 无变更端点。** 经 Next rewrite `/api/py/*` 代理到 FastAPI。

| Method | Path | 用途 | 入参（关键） | 出参（关键） |
|---|---|---|---|---|
| GET | `/strategies` | 列全部策略（含组合 / 因子，`kind` 字段区分） | — | `Strategy[]`（含 `kind/params/paramsSchema/builtin/unavailable`） |
| POST | `/strategies/compare` | 多策略一键对比回测 | `{strategyIds:number[], instId, bar, startTs, endTs, limit?, initialCapital?, fees?, slippage?, leverage?}` | `CompareResult {instId, bar, items: CompareItem[]}` |

前端 `lib/api.ts` 现有 `strategies()` 与 `compare(body)` 直接可用，**本方案不新增 api 方法、不改请求 / 返回类型**。详情弹窗为**纯客户端**渲染——所需数据（`description/params/paramsSchema/kind`）已在 `GET /strategies` 返回的 `Strategy` 中，无额外网络请求。

**前端组件接口（新增）**

```ts
// CompareSelectionBar.tsx
export default function CompareSelectionBar(props: {
  filter: CompareFilter;
  onFilter: (f: CompareFilter) => void;
  selectedCount: number;          // selected.size
  maxSelections: number;          // MAX_COMPARE_SELECTIONS = 12
  minSelections: number;          // MIN_COMPARE_SELECTIONS = 2
  hasResult: boolean;             // 是否存在对比结果（控制「清空结果」显隐）
  running: boolean;               // compare.isPending
  onSelectAll: () => void;
  onInvert: () => void;
  onClear: () => void;
  onRun: () => void;
  onClearResult: () => void;
}): JSX.Element;

// CompareStrategyRow.tsx
export default function CompareStrategyRow(props: {
  strategy: Strategy;
  checked: boolean;
  disabled: boolean;              // unavailable || (!checked && atSelectionLimit)
  onToggle: (id: number) => void;
  onOpenDetail: (s: Strategy) => void;
}): JSX.Element;

// StrategyDetailModal.tsx
export default function StrategyDetailModal(props: {
  strategy: Strategy;
  strategies: Strategy[];         // 供组合成员按 templateKey 回查名称
  compareItem?: CompareItem;      // 命中则展示「本次对比绩效」
  onClose: () => void;
}): JSX.Element;
```

---

## 五、数据模型（Data Model）

**无数据库表结构变更、无后端 schema 变更。** 详情弹窗完全消费 `GET /strategies` 已返回的 `Strategy` 形态：

- `kind ∈ {"atomic","combo","factor"}` 决定详情分区；`paramsSchema`（`{type,default,min?,max?,step?,options?,label}`）驱动原子只读参数网格；`params` 提供当前值、组合 `params.members`/轮动标量、因子 `params.members`/`params.thresholds`/`params.provenance`。
- `CompareItem {strategyId, name, runId, metrics, equity}` / `CompareResult {instId, bar, items}` 不变。

**前端状态（本地，非持久化）**：
- `CompareView` 局部：`selected: Set<number>`（**默认空集**）、`sortKey`、`sortDir`、新增 `filter: CompareFilter`（默认 `"all"`）、`detail: Strategy | null`（默认 `null`）。
- **刻意不入 `store`/`localStorage`**：对比选择每次进入从空开始（满足需求 1「默认什么都不选」）。`store` 的 `version`/`partialize` **零改动**。
- 对比结果仍缓存于 TanStack Query 键 `["compare", instId, bar, backtestStartTs, backtestEndTs]`；左图叠加 / 配色耦合（`app/page.tsx` `compareOverlays` + `colorForIndex(index)`）因键与配色口径不变而自动保持。**（评审 C-P1-1）`CompareView` v2 以响应式 `useQuery({ queryKey: compareKey, queryFn: () => null, enabled: false, initialData: null })` 读取**（替代非响应式 `getQueryData`），与 `app/page.tsx:178-183` 同模式，确保运行 / 清空结果均即时反映到榜单与 `hasResult`。

**常量单一真源**：`MAX_COMPARE_SELECTIONS=12`、`MIN_COMPARE_SELECTIONS=2` 迁入 `lib/compare.ts` 导出，`CompareView` 与 `CompareSelectionBar` 共享（替代现 `CompareView` 内私有常量，避免双份定义漂移）。

---

## 六、测试与验收标准（Testing & Acceptance Criteria）

**前端单测（vitest；组件测试须 `NODE_ENV=test`，否则 React act() 报错——见项目记忆 `aragon-web-vitest-node-env`）**

- `compare.test.ts`（纯助手）：①`selectAllEligible(current, visible, 12)` 把可见可用项**并入** `current`、排除 `unavailable`、并集 > 12 时按稳定顺序取前 12，**且不丢弃 `current` 中不在 `visible` 的已选项**（评审 C-P1-3）；②`filterCompareStrategies`：`all` 全集、`combo` 仅 `kind==="combo"`、`atomic` 为 `kind!=="combo"`（含 `factor`，评审 C-P1-2）；③`invertSelection` 在可见集合内反转且受 cap 夹取、可见外已选项保留；④空集合 / 全 unavailable 退化为 `[]`；⑤`compareKindLabel` 三类标签；⑥`isCompareEligible` 与现 checkbox disabled 口径一致。
- `CompareSelectionBar.test.tsx`：计数文案随 `selectedCount` 刷新；`< 2` 与 `>= 12` 上下文提示；`运行对比` 在 `<2 || >12 || running` 禁用、否则可点并触发 `onRun`；`全选/反选/清空` 触发对应回调；`hasResult=false` 时「清空结果」不渲染，`true` 时渲染并触发 `onClearResult`。
- `CompareStrategyRow.test.tsx`（或并入 `CompareView.test.tsx`）：**点「详情」按钮触发 `onOpenDetail` 且不触发 `onToggle`**（核心交互正确性）；点选择区触发 `onToggle`；`unavailable` 时 checkbox 禁用但「详情」仍可点；徽标按 `kind/builtin/unavailable` 渲染。
- `StrategyDetailModal.test.tsx`：①`atomic` 渲染描述 + 只读参数网格（标签 / 值）+ 高周期徽标（声明时）；②`combo` 渲染 Overview/Members/Rotation（复用抽出分区，成员按 `templateKey` 回查名称）；③`factor` 渲染成员方向徽标 + 最小根数 + 溯源（有 `provenance` 时）；④`compareItem` 命中追加「本次对比绩效」；⑤a11y：`Esc` 与点遮罩触发 `onClose`，焦点陷阱在弹窗内（复用 `Modal` 既测契约）。
- `CompareView.test.tsx`：①进入默认 `selected.size===0`、`运行对比` 禁用、空选提示可见（验收需求 1）；②选≥2 后点运行写入 `["compare",…]` 缓存键（保左图叠加耦合）；③点某行「详情」开弹窗、关弹窗后选择不变；④运行得到结果后点「清空结果」→ 榜单消失、`hasResult` 转 false（验证响应式读取生效，评审 C-P1-1）。

**后端测试**：本方案零后端改动，沿用既有 `test_api.py` / 对比相关回归（`POST /strategies/compare` 多策略返回 `items` 含 metrics/equity）保持全绿，不新增。

**验收清单（Definition of Done 对齐）**

1. 进入「对比」模式后**默认无任何策略被勾选**；`运行对比` 禁用，并提示至少选 2 个。
2. 清单上方有**常驻批量工具条**：分类筛选（全部/原子/组合）、已选计数（N/12）、**批量选择**（全选 / 反选 / 清空）与**批量操作**（运行对比 / 清空结果）齐备且可用；全选 / 反选受 12 上限夹取并在截断时提示；清空结果可一键移除榜单与左图叠加。
3. 清单**每一行最右侧**有命名为「详情」的按钮；点击弹出**屏幕居中模态弹窗**，按策略类型只读展示完整内容（原子：描述 + 参数网格；组合：概览 + 成员 + 轮动参数；因子：成员方向 + 最小根数 + 训练溯源），命中当次对比时附绩效；弹窗满足 a11y（焦点陷阱 / `Esc` / 遮罩 / 焦点恢复）且明暗主题正常。
4. **点「详情」不改变该行勾选状态**（`stopPropagation` 生效）。
5. 运行对比仍写入 `["compare", instId, bar, startTs, endTs]` 缓存键，左图多策略归一化净值叠加与榜单色块一致（配色 `colorForIndex(index)` 口径不变）。
6. 不改 DB / 后端 / `store` 持久化；前端 `tsc --noEmit` 无误；`pnpm test`（前端 vitest + 后端 pytest 离线）通过。
7. 视觉与既有 `StrategyList`/`ComboDetailModal` 语汇统一，符合「美观优雅、顶级设计、人机交互最佳实践」（sticky 工具条、主操作常驻、空态 / 禁用 / 截断均有明确反馈、读屏可感知）。

---

## 七、风险与缓解（Risks & Mitigations）

| 风险 | 等级 | 缓解 |
|---|---|---|
| **「详情」按钮置于可点行内导致点详情顺带切换勾选** | 中 | 行容器改 `<div>`（不再整行 `<label>`）；选择区与详情区分离；详情按钮 `onClick` 内 `e.stopPropagation()`；`CompareStrategyRow.test.tsx` 显式断言「点详情仅触发 `onOpenDetail`、不触发 `onToggle`」。 |
| **默认改空选后，旧用户预期『进来已全选』落差** | 低 | 即需求明确诉求；以空选提示 + 一键「全选」补偿，路径比自动全选更可控（用户主动决定对比集合）。 |
| **抽出 `ComboDetailSections` 改坏 `ComboDetailModal` 渲染** | 中 | 平移保持 JSX/props 完全等价、仅改文件归属；`ComboDetailModal` 既有快照 / 行为不变；保留其 `PerformanceSection` 与 footer；改后跑组合相关组件测试回归。 |
| **左图叠加 / 配色因缓存键或顺序变动而错位** | 中 | 严格保持写入键 `["compare", instId, bar, backtestStartTs, backtestEndTs]` 与按 `items` 原始顺序 `colorForIndex(index)`；「清空结果」用 `setQueryData(compareKey, null)` 而非改键；`CompareView.test.tsx` 断言写入键不变。 |
| **「清空结果」因非响应式读取而不刷新榜单**（评审 C-P1-1） | 高 | `CompareView` 由非响应式 `getQueryData` 改为响应式 `useQuery`（`enabled:false`+`initialData:null`+`queryFn:()=>null`）订阅 `compareKey`（与 `app/page.tsx` 同模式），运行 / 清空均即时刷新；`CompareView.test.tsx` 断言清空后榜单消失、`hasResult` 转 false。 |
| **全选 / 反选与 12 上限、筛选作用域交互复杂** | 中 | 逻辑全部下沉 `lib/compare.ts` 纯函数并单测（上限夹取 / 筛选作用域 / 反选边界 / 空集合）；组件只消费，不在 JSX 里写分支算法。 |
| **因子 / 组合 `params` 字段缺失（旧快照）导致详情渲染崩** | 低 | 与既有 `FactorStrategyBanner`/`ComboDetailModal` 同口径做可空守门（`?? []`、`provenance` 存在才渲染、成员模板删除标「失效」）；不假数据（CLAUDE C1）。 |
| **`StrategyDetailModal` 三 kind 分支致文件膨胀 / 函数过长** | 低 | combo 分区复用抽出文件、factor/atomic 分区短；按 kind 拆内部子组件（`AtomicDetail`/`FactorDetail`/`ComboDetail`），单函数 ≤50 行、单文件 ≤800 行。 |
| **sticky 工具条在窄右栏溢出 / 遮挡** | 低 | 两行布局 + spacer 自适应；按钮 `size="sm"`；筛选用紧凑 segmented；`max-w` 由父栏约束，必要时第二行换行。 |
| **常量双份定义漂移（12/2 同时存在于组件与 lib）** | 低 | 统一迁入 `lib/compare.ts` 导出为单一真源，组件 import 使用。 |

---

## 八、实施顺序建议（供开发节点参考）

1. `lib/compare.ts`：迁入 `MAX/MIN_COMPARE_SELECTIONS` 常量 + 追加 `CompareFilter` 与 5 个纯助手；补 `compare.test.ts` 用例（先让纯逻辑可测通过）。
2. 抽 `ComboDetailSections.tsx`（从 `ComboDetailModal` 平移只读分区并导出）；改 `ComboDetailModal` 改为 import；跑组合组件测试回归（行为零变化）。
3. 新增 `StrategyDetailModal.tsx`（atomic/combo/factor 三分区 + 可选绩效）；补 `StrategyDetailModal.test.tsx`。
4. 新增 `CompareStrategyRow.tsx`（选择区 + 详情按钮，`stopPropagation`）；补行为测试（点详情不改选择）。
5. 新增 `CompareSelectionBar.tsx`（筛选 + 计数 + 全选/反选/清空 + 运行对比/清空结果）；补 `CompareSelectionBar.test.tsx`。
6. 改 `CompareView.tsx`：删自动全选 effect（默认空选）、接入工具条 + 行组件 + 详情弹窗、迁出下方运行按钮、加 `filter`/`detail` 态、保留缓存写入 / 榜单 / 配色 / 「打开」；补 / 并 `CompareView.test.tsx`。
7. 收尾：`tsc --noEmit` + `pnpm test`（vitest + pytest 离线）全绿；自查 CLAUDE 清洁码阈值（文件 ≤800 / 函数 ≤50 / 形参 ≤5 / 嵌套 ≤4 / 单行 ≤80）。

> 全程遵循项目约定：纯助手 + 同名测试、复用 `Modal`/既有分区杜绝孤岛与重复、错误显式传播不假数据（C1）、后端零改动、`store` 持久化与密钥口径不变。

---

## 评审结论（Review Verdict）

**有条件通过（approved with conditions）。**

本方案对现状的核对扎实（所有关键源码引用经逐文件复核属实），范围克制、与既有约定（纯助手 + 同名测试、复用 `Modal`/抽出组合分区去重、零后端 / 零表结构 / 零 `store` 持久化）一致，未过度设计。3 项 P1 已在评审中**就地修复**并反映到正文（§2.1 / §2.4 / §2.7 / §2.8 / §三 / §五 / §六 / §七）：

- **C-P1-1**（响应式读取对比结果，让「清空结果」生效）——已改为与 `app/page.tsx` 同模式的 `useQuery` 订阅；
- **C-P1-2**（筛选语义统一：`atomic = kind !== "combo"`，因子可被发现）——已统一三档定义；
- **C-P1-3**（「全选」并集语义、签名带 `current`）——已对齐 `invertSelection`。

**无遗留 P0 / P1。**

**放行条件（实施期须确认的 P2，逐条勾对后即视为完全达标）：**

1. **C-P2-1**：删除自动全选 effect 后，从 `CompareView` 第 4 行移除未使用的 `useEffect` 导入，确保 `tsc --noEmit` / ESLint 全绿（DoD「tsc 无误」）。
2. **C-P2-2**：以「删除整个含注释 + `eslint-disable` 的 effect 块（约 32–44 行）」为准，勿按字面行号误删相邻代码。
3. **C-P2-3**：确认 `StrategyPanel` 右栏滚动容器层级，使 `sticky top-0` 真正「常驻」；若不生效，择一：提升工具条至滚动容器内 / 接受非 sticky（「上方有工具条」仍满足需求 2）。
4. **C-P2-4**：运行按钮迁入工具条后，`compare.isError` 错误横幅保留在主体（工具条下方、榜单上方）。

满足以上 4 条即可放行进入「代码开发」节点（Subtask #2）。

---

（评审完 · **v2** · 方案评审节点 Subtask #1；前序：设计完 · v1 · 方案设计节点 Subtask #0）

---

## 实施过程发现的方案缺陷（Issues Found During Implementation · Subtask #2）

> 记录人：代码开发节点（标准四阶段开发 · Subtask #2）。按任务约定：发现方案不完整不静默偏离，记录后按修正方案落地。

| 编号 | 维度 | 问题 | 处置 |
|---|---|---|---|
| I-1 | 完整性（测试清单遗漏） | §三「文件 / 模块变更计划」与 §六测试清单**未列入既有的 `apps/web/components/Strategy/StrategyPanel.test.tsx`**——其中 `caps compare selection at 12 and disables unchecked strategies at the cap` 用例断言「进入对比即自动全选前 12 个内置策略」（旧 `CompareView` 自动全选 effect 的行为）。需求 1「默认空选」删除该 effect 后，此用例必然失败，与 DoD「`pnpm test` 通过」冲突。 | **已就地更新该用例**（非删除）为验证**新正确行为**：进入对比默认 0 选 → 手动勾选前 12 个受 12 上限夹取 → 第 13 个未选且禁用 → 工具条提示「已达上限」→ 运行对比以选中的 12 个 id 调 `api.compare`。用例改名为 `defaults to no compare selection and caps manual selection at 12`。仅改测试断言以对齐需求 1，未改 `StrategyPanel` 源码。 |
| I-2 | 一致性（小幅偏离正文措辞，更优解） | §2.5(b) 详情弹窗通用「概览」分区与 §2.6 抽出的组合 `OverviewSection` 同名「概览」，组合策略详情会出现两个标题同为「概览」的分区（一个通用元信息、一个组合参数概览），既视觉重复又导致测试 `getByText("概览")` 命中多元素。 | 将详情弹窗通用元信息分区标题改为 **「基本信息」**（类型 / 模板键 / 内置 / 状态），与组合「概览」区分；语义更清晰，亦消除标题碰撞。属正文意图内的命名细化，不影响分区职责与数据来源。 |

> 以上两项均为**测试 / 文案层面的局部修正**，不改变 §三 change plan 的源码落点与 §四接口签名；核心实现严格遵循 v2 方案（默认空选 + 响应式读取 + 顶部批量工具条 + 每行详情按钮 + 按 kind 只读详情弹窗 + 组合分区抽出复用）。
