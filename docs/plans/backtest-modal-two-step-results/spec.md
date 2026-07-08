# 回测弹窗「两步向导 · 结果独立成步」— 开发方案 (spec)

- **Feature slug**: `backtest-modal-two-step-results`
- **版本**: **v2**（Subtask #1 评审修复版；本轮修复 2 个 P1，详见文首「评审记录」与文末「评审结论」。v1 为 Subtask #0 方案设计）
- **日期**: 2026-07-08
- **作者角色**: Solution Architect（Subtask #0）
- **本轮需求（第 2 轮）**: 回测结果不再堆在原「回测设置」面板下方，而是移到弹窗的**第二步（独立面板）**呈现，并允许用户**返回上一步**重新配置；美观优雅、顶级、符合人机交互最佳实践。
- **上轮成果（第 1 轮 · commit `1dbf909`）**: 新增 `BacktestConfigModal`（配置在上、结果在下的单面板），本轮在其之上迭代。
- **关联任务板**: `.agentmesh/task-tge-514cedf7-fb94-4f9d-8073-f0801e136433/task-board.md`

## 评审记录（Subtask #1 · Review Notes · v2）

- **评审人**: Senior Reviewer（Anthropic Eng · Subtask #1）
- **评审日期**: 2026-07-08
- **评审方式**: 逐节对照 **Feasibility / Completeness / Consistency / Right-sizing** 四维；核对源文件 `apps/web/components/Strategy/BacktestConfigModal.tsx`、父组件挂载点（`StrategyDetail.tsx:347`、`ComboStrategyDetail.tsx:157`）、UI 原语（`components/ui/index.tsx` 的 `Button` 变体 `default/primary/ghost/danger` 与 `Modal` 焦点陷阱）、父层 `running/result` 接线（`StrategyPanel.tsx:545-549 / 608-612`：`backtestRunning=backtest.isPending`、`backtestResult=backtest.data`、`onRun→backtest.mutate(...)`）与既有测试 `BacktestConfigModal.test.tsx`。
- **总体判断**: 方案技术可行、对外契约零破坏、范围收敛得当，且与 v1 现状高度吻合（行号引用准确、`BacktestMetricsGrid`/`EquitySparkline`/`equityQ` 复用清单成立、父组件零改动可行）。共发现 **0 个 P0、2 个 P1、5 个 P2**；两个 P1 已在下文正文就地修复并升版 v2。

### 问题清单

| 编号 | 维度 | 严重度 | 问题 | 处置 |
|---|---|---|---|---|
| R-1 | Completeness / HCI | **P1** | 步骤机只定义「② 结果 →（返回）① 配置」与「① 配置 →（重跑）② 结果」，缺「已运行后无重跑回看结果」的前进路径。用户返回第一步微调后若不改参数，只能再点「开始回测」重跑一遍才能看回结果——回测有时延/成本，违背「顶级 HCI」；且方案自身引入了可点步骤指示器，语义上理应允许在已完成步骤间自由切换。 | 已修复：§2.3 引入会话级 `ranThisSession` 与 `goForward`，§2.4 令「② 结果」在已运行后可点（前进不重跑），§2.5/§4.2/§5 补状态与契约，§6.1 加用例 #10，§七 加风险行。 |
| R-2 | Completeness / a11y | **P1** | §2.4 焦点管理用 `useEffect(step)`，但推进到第二步时承载焦点的「开始回测」按钮随第一步页脚卸载；`useEffect` 在浏览器绘制后才跑，卸载与移焦之间 `document.activeElement` 回落到 `<body>`，令 `Modal` 焦点陷阱瞬时失效、Tab 行为不可预期。 | 已修复：§2.4 改用 `useLayoutEffect` 同步移焦、「回测结果」小标题显式 `tabIndex={-1}` 作落点、返回时优先聚焦 `configFirstRef` 否则退回配置容器、全程 `focus({preventScroll:true})`。 |
| R-3 | Correctness | P2 | 自动推进到第二步的那一帧，`result` 仍是上次成功值、`running` 由父层 `mutate()` 同帧置真（React 18 事件内批处理）；只要 `ResultStep` 分支顺序保持「先判 `running` 出骨架」，就不会闪回旧指标。需把该不变量写死为实现约束，避免后续误调分支顺序。 | 已在 §2.4/§六 固化：`ResultStep` 分支序固定为 `running → error → result`；依赖父层 `running=mutation.isPending` 且 `onRun` 内同步 `mutate()`（已核对属实）。 |
| R-4 | Completeness | P2 | 未定义「父层外部把 `running` 置真但用户仍在第一步（未点运行）」的表现（如别处触发的运行）。 | 约定：此情形**不自动跳步**，第一步主按钮照常显示「回测中…」并禁用；不视为进入第二步。见 §2.3 兜底项。 |
| R-5 | Consistency / a11y | P2 | 步骤指示器三段须为真正的 `<button type="button">`：当前段 `aria-current="step"`、已完成段可点（`goBack`/`goForward`）、未达段 `disabled`，不得用可点 `div`，以合 CLAUDE.md「键盘可达」约定。 | 已在 §2.4 明确。 |
| R-6 | Right-sizing / Consistency | P2 | 拆分后 `BacktestConfigModal.tsx` 仍须 ≤ 800 行（CLAUDE.md）。当前 424 行，预估 +80~120 行仍安全；若逾阈，允许把 `StepIndicator`/`ResultStep` 抽到同目录兄弟文件（突破「不新建文件」原则时须在「实施过程发现的方案缺陷」段登记）。 | 已在 §2.5/§三 加放宽条款。 |
| R-7 | Testing 精度 | P2 | §6.1 #4 覆盖横幅断言应为**子串/正则**匹配（实际 DOM 为「⚠ 区间过长 / 历史不足，仅覆盖最近 200 / 720 根，收益率为部分区间结果。」），沿用既有 `/仅覆盖最近 200 \/ 720 根/`；§6.1 运行态用例应保留一条「骨架出现」的正向断言，而非仅断言空态缺席。 | 已在 §6.1 订正。 |

> 说明：P2 不阻断开发，但列为「评审结论」中的落地条件，代码开发（Subtask #2）须逐条遵守。

---

## 一、概述（Overview）

第 1 轮已把「运行回测」从一键直跑升级为一个配置弹窗 `BacktestConfigModal`（`apps/web/components/Strategy/BacktestConfigModal.tsx`）。当前弹窗采用**「上配置、下结果」的单面板**布局：`Modal` 里从上到下依次是 Header、可滚动的配置区（回测区间 / K 线周期 / 成本与杠杆 / 即时校验反馈）、运行按钮页脚，最后再**追加一段结果区**（`ResultRegion`：空态占位 / 运行骨架 / 覆盖横幅 + 绩效指标网格 + 净值缩略图 / 错误态）。这意味着运行结束后，用户要在同一个纵向滚动面板里向下滚动去看结果，配置项与指标混在一屏，信息层级不清、聚焦度差，指标越丰富越拥挤，不符合「一屏一任务」的人机交互最佳实践。

本轮把该弹窗从**单面板**改造为**两步向导（wizard）**：**第一步「配置」**只承载回测区间、周期、成本与杠杆及其即时校验；用户点「开始回测」后**推进到第二步「结果」**——一个**独立、聚焦的结果面板**，专门呈现运行态骨架、覆盖度横幅、专业绩效指标网格、净值缩略图与操作条；第二步固定提供**「← 返回上一步」**，让用户随时回到第一步微调参数并重跑。弹窗顶部新增**步骤指示器（① 配置 → ② 结果）**，明确告知当前所处步骤与可回退性。结果不再追加在配置下方，而是**替换**式地占满弹窗正文，纵向不再堆叠。

关键设计取舍：**本轮是纯前端、且改动几乎全部内聚在 `BacktestConfigModal.tsx` 内部**。弹窗的对外 props 契约（`open/onClose/strategyName/instId/warmupBars/provenanceHint/initialConfig/running/result/errorMessage/onRun`）**保持逐字不变**，因此 `StrategyDetail.tsx`、`ComboStrategyDetail.tsx`、`StrategyPanel.tsx`、`lib/store.ts`、`lib/types.ts` 以及后端**均无需改动**；「配置→运行→结果」仍是同一个 react-query mutation 单一数据源，第一步/第二步只是同一状态的两种可视化编排。绩效指标与净值缩略图继续复用现成的 `BacktestMetricsGrid` 与 `EquitySparkline`（零口径漂移）。这样把回归面压到最小，同时显著提升聚焦度与专业感。

---

## 二、技术方案（Technical Design）

### 2.1 现状与本轮改造点

当前 `BacktestConfigModal` 的 `return` 结构（`BacktestConfigModal.tsx:155-327`）自上而下为：`<Modal>` → `<header>` → 配置区 `div.flex-1.overflow-y-auto`（三个分组卡片 + 校验/告警/样本内外提示）→ 运行按钮 `div.border-t`（hint + 「开始回测」）→ **结果区 `div.border-t`（`<ResultRegion/>`，见 `:311-325`）**。组件内用 `hasRun`（`:102`）标记「本次打开是否已从弹窗触发过运行」，以避免跨策略陈旧 `result` 误显；结果区四态由 `running/hasRun/result/errorMessage` 决定（`ResultRegion`，`:359-423`）。

本轮把「结果区常驻在配置下方」改为「结果区独占第二步」。核心是引入一个**步骤机（step machine）**，用条件渲染在同一 `Modal` 内切换两屏，并把运行按钮与「返回/操作」拆到各自步骤的页脚。

### 2.2 设计原则

1. **一屏一任务**：第一步只做配置，第二步只看结果，二者不同屏共存。
2. **可回退**：第二步永远可「返回上一步」；返回后配置态（`cfg`）保持不变，可微调重跑。
3. **对外零破坏**：弹窗 props 契约不变，父组件与后端零改动；`step` 完全是弹窗内部状态。
4. **复用优先**：结果面板继续复用 `BacktestMetricsGrid`、`EquitySparkline`、覆盖横幅逻辑与 `Modal` 无障碍原语；不新增依赖、不新建文件（除测试）。
5. **单一数据源**：运行态/结果/错误仍由父层 mutation 经 props 下发；`step` 只决定「展示哪一屏」，不复制业务数据。

### 2.3 步骤状态机与数据流

新增内部状态 `type Step = "config" | "result"; const [step, setStep] = useState<Step>("config")` 决定「展示哪一屏」，并**配合**一个会话级布尔 `const [ranThisSession, setRanThisSession] = useState(false)`（取代原 `hasRun`，语义为「本次打开是否已从弹窗触发过运行」）。二者分工明确：`step` 管当前屏幕，`ranThisSession` 承担两职——(a) **防陈旧结果误显**（跨策略打开时结果不外漏），(b) **门控步骤指示器「② 结果」的前进可点性**（已运行→可无重跑回看结果，见 R-1 修复）。二者随「打开」一并重置，故重开弹窗恒回第一步且清空「已运行」态。

- **打开播种**（复用现有 `useEffect(open, initialConfig)`，`:106-115`）：`seededRef` 首次置真时，除 `setCfg(refreshPresetRange(...))` 外，追加 `setStep("config")` 与 `setRanThisSession(false)`（替换 `setHasRun(false)`）。关闭时 `seededRef.current=false`，下次打开必回到第一步且「已运行」态归零。
- **推进到结果**（`triggerRun`，`:149-153`）：校验通过后依次 `setRanThisSession(true)` → `setStep("result")` → `onRun(cfg)`；点「开始回测」的同时切到第二步，先展示运行骨架，结果到达后就地渲染。
- **返回配置**（新增 `goBack = () => setStep("config")`）：第二步页脚「← 返回上一步」调用之；`cfg` 与 `ranThisSession` **均不重置**，故「② 结果」保持可回看、参数保持可微调。
- **前进回看**（新增 `goForward = () => { if (ranThisSession) setStep("result"); }`，修复 R-1）：步骤指示器「② 结果」在已运行后可点，直接回看现有 `result`，**不触发重跑**（不调用 `onRun`）。这是「已完成步骤间自由切换」的对称补全。
- **重跑**（显式点「开始回测」/错误态「重新回测」）：仍走 `triggerRun`（此时若已在第二步，`setStep("result")` 幂等；`ranThisSession` 已真）。
- **外部运行兜底**（修复 R-4）：若父层把 `running` 置真而用户仍在第一步（未点运行，如别处触发的运行），**不自动跳步**；第一步主按钮照常显示「回测中…」并禁用，第二步不进入。

时序：

```
用户点「运行回测」→ 父层 setConfigOpen(true) → 弹窗 open，step="config"，按 now 重派生预设区间
用户在第一步调 cfg（区间/周期/成本/杠杆）→ 即时校验 estimateBars/validateConfig/样本内外提示
用户点「开始回测」→ triggerRun：step="result"（切屏，显骨架）→ onRun(cfg)
  → 父层 backtest.mutate → 后端回测 → props(running=false, result=data|errorMessage) 更新
  → 第二步就地渲染：覆盖横幅 + BacktestMetricsGrid + EquitySparkline + 操作条
用户点「← 返回上一步」→ step="config"，可微调后再次「开始回测」
用户点「在主图查看」/关闭/Esc/遮罩 → onClose（父层 setConfigOpen(false)）
```

### 2.4 UI 结构与交互（HCI）

沿用 `Modal`（`max-w-2xl`、`max-h-[85vh]`、焦点陷阱 / Esc / 遮罩关闭 / body 滚动锁定）。整体自上而下为 **Header（含步骤指示器）→ 正文（按 step 切屏）→ 页脚（按 step 切换）**：

- **Header**：标题「运行回测」+ 副标题 `策略名 · 标的 · 标的只读`（不变）；右上角关闭按钮（不变）。副标题行下方新增**步骤指示器** `<StepIndicator step={step} ran={ranThisSession} onBack={goBack} onForward={goForward}/>`：`① 配置 —— ② 结果` 两段，**每段均为真正的 `<button type="button">`**（修复 R-5，不得用可点 `div`，保键盘可达）：
  - 当前段用 `var(--accent)`/`var(--accent-soft)` 高亮并置 `aria-current="step"`。
  - 「① 配置」在第二步时为**已完成段**（显示 ✓）、可点回退（`onBack`=`goBack`）。
  - 「② 结果」在第一步时：未运行（`!ran`）为 `disabled` 灰态；**一旦已运行（`ran`）即变为可点前进（`onForward`=`goForward`），直接回看现有结果、不重跑**（修复 R-1）。
- **正文**（`div.flex-1.overflow-y-auto.px-5.py-4`，仍是唯一滚动容器）：
  - `step === "config"` → `<ConfigStep/>`：把现有三张配置分组卡片 + 即时反馈（校验错误 / 暖机告警 / 样本内外提示）整体搬入本子组件；表单与逻辑逐字保留，仅换外壳。
  - `step === "result"` → `<ResultStep/>`：由现 `ResultRegion` 演化为**三态**（删去「空态占位」，因已进入第二步必已运行）。**分支顺序须固定为 `running → error → result`**（修复 R-3）：自动推进到第二步的那一帧，`result` 仍是上次成功值、`running` 由父层 `mutate()` 同帧置真（React 18 事件内批处理），只要「先判 `running` 出骨架」就不会闪回旧指标；此顺序是实现级不变量，不得调换。三态即：运行骨架 / 错误卡片 / （覆盖横幅 + 指标网格 + 净值缩略图）。顶部加一行小标题「回测结果」（`tabIndex={-1}` 供焦点落点）并对结果容器加 `aria-live="polite"` 以便屏幕阅读器播报完成。
- **页脚**（`div.border-t`）：
  - 第一步页脚：左侧提示「配置区间与参数后点『开始回测』，进入下一步查看结果」；右侧主按钮「开始回测」（`variant="primary"`，运行中显示「回测中…」并禁用，`disabled` 时 `title` 给原因）——保留原运行按钮行为，仅文案由「下方就地呈现」改为「进入下一步」。
  - 第二步页脚：**左侧「← 返回上一步」**（`variant="ghost"`，常驻可用）；右侧按结果态分流——成功态「导出净值 CSV」（`variant="ghost"`，无净值时禁用）+「在主图查看」（`variant="default"`，`onClose`）；错误态「重新回测」（`variant="primary"`，`triggerRun`）；运行态右侧操作禁用。导出与「在主图查看」的实现（`equityQ`/`runId`/`downloadCsv`）保留在父组件作用域，直接在页脚内联使用。
- **焦点管理**（修复 R-2）：新增 **`useLayoutEffect(step)`（非 `useEffect`）**——因为推进到第二步时承载焦点的「开始回测」按钮随第一步页脚卸载，若用绘制后才跑的 `useEffect`，卸载与移焦之间 `document.activeElement` 会回落到 `<body>`，令 `Modal` 焦点陷阱瞬时失效、Tab 行为不可预期；改用 `useLayoutEffect` 在同一提交阶段同步移焦即可闭合此窗口。进入 `result` 时把焦点移到「回测结果」小标题（该标题显式 `tabIndex={-1}`，`resultHeadingRef.current?.focus({preventScroll:true})`）；返回 `config` 时优先聚焦第一步首个可交互控件（`configFirstRef.current`），取不到则退回配置区容器。全程 `focus({preventScroll:true})`，焦点始终落在 `Modal` 的焦点陷阱内。
- **视觉**：数字 `tabular` 等宽；配色走 `var(--long)/var(--short)/var(--accent)/var(--text)/var(--muted)` 主题变量，明暗自适应；切屏用轻量淡入（可选 `transition-opacity`），无横向溢出。

### 2.5 组件内部分解（同文件，不新建文件）

`BacktestConfigModal.tsx` 内部新增/调整以下私有组件与状态（均在同一文件，符合单文件 ≤ 800 行阈值）：

- `type Step`、`const [step,setStep]`、`const [ranThisSession,setRanThisSession]`、`goBack`、`goForward`、`resultHeadingRef`、`configFirstRef`。
- `StepIndicator({ step, ran, onBack, onForward })`：两段步骤条（真 `<button>` + `aria-current`/`disabled`），已完成段 `onBack`、已运行的「② 结果」段 `onForward`。
- `ConfigStep({ ... })`：承载原配置区 JSX（三卡片 + 反馈），props 透传 `cfg/patch/pickPreset/setBound/bars/errors/warnings/provenanceHint/inSample` 等既有派生值。
- `ResultStep({ running, result, errorMessage })`：由 `ResultRegion` 改名精简（删空态、去掉内部操作条——操作条上移到页脚）。
- `Chip`、`CostField`、`triggerRun`、`equityQ`、校验派生（`ok/errors/warnings/bars/inSample`）等**保持不变**。

> **文件尺寸约束（R-6）**：拆分后 `BacktestConfigModal.tsx` 须守 CLAUDE.md 的 ≤ 800 行阈值。现 424 行，本轮预估净增 +80~120 行（新增步骤机/两个页脚/`StepIndicator`，同时 `ConfigStep`/`ResultStep` 只是把既有 JSX 换外壳、近似零净增），落在安全区。**兜底**：若实测逾阈，允许把 `StepIndicator` 与 `ResultStep` 抽到同目录兄弟文件（如 `BacktestWizardParts.tsx`）——这会突破「不新建文件（除测试）」原则，须在文末「实施过程发现的方案缺陷」段登记原因。

---

## 三、文件 / 模块变更计划（File / Module Change Plan）

| 文件 | 类型 | 一句话意图 |
|---|---|---|
| `apps/web/components/Strategy/BacktestConfigModal.tsx` | 修改 | 单面板→两步向导：新增 `step` 状态机 + `StepIndicator` + `ConfigStep`/`ResultStep` 拆分 + 两套页脚（开始回测 / 返回上一步）+ 焦点管理；对外 props 契约不变 |
| `apps/web/components/Strategy/BacktestConfigModal.test.tsx` | 修改 | 更新既有用例并新增：点运行后进入第二步、出现「返回上一步」、点返回回到第一步、步骤指示器 `aria-current`、未运行时结果不渲染（防陈旧）等 |

> 明确的**不改**清单（守住「结果独立成步」不外溢）：`BacktestMetricsGrid.tsx`、`EquitySparkline.tsx`、`lib/backtestConfig.ts`、`StrategyDetail.tsx`、`ComboStrategyDetail.tsx`、`StrategyPanel.tsx`、`lib/store.ts`、`lib/types.ts`、`lib/api.ts` 及 `services/quant/**` 全部**零改动**。若实施中发现必须外溢，先记入文末「实施过程发现的方案缺陷」段再动。

---

## 四、接口设计（Interface Design）

### 4.1 对外组件 props（**逐字不变**，本轮不新增/不删除任何 prop）

```ts
export default function BacktestConfigModal(props: {
  open: boolean; onClose: () => void;
  strategyName: string; instId: string;
  warmupBars?: number;
  provenanceHint?: { startTs: number; endTs: number; instId?: string; bar?: string } | null;
  initialConfig: BacktestConfig;
  running: boolean;
  result?: BacktestResult;
  errorMessage?: string | null;
  onRun: (cfg: BacktestConfig) => void;
}): JSX.Element | null;
```

因契约不变，`StrategyDetail.tsx:347-359` 与 `ComboStrategyDetail.tsx:157-167` 的挂载点**无需任何修改**。

### 4.2 弹窗内部新增（私有，不导出）

```ts
type Step = "config" | "result";                 // 第二步 = 结果独立面板
// 状态：const [step, setStep] = useState<Step>("config");
//       const [ranThisSession, setRanThisSession] = useState(false); // 取代 hasRun
// 行为：triggerRun() 先 setRanThisSession(true) → setStep("result") → onRun(cfg)
//       goBack()    => setStep("config")                    // 不重置 cfg / ranThisSession
//       goForward() => { if (ranThisSession) setStep("result"); }  // 前进回看，不重跑（R-1）
// 播种：useEffect(open) 首次置 seededRef 时 setStep("config") + setRanThisSession(false)

function StepIndicator(props: {
  step: Step; ran: boolean;                       // ran=ranThisSession，门控「② 结果」前进可点
  onBack: () => void; onForward: () => void;
}): JSX.Element;
function ConfigStep(props: { /* 透传原配置区所需的 cfg 与派生/回调 */ }): JSX.Element;
function ResultStep(props: { running: boolean; result?: BacktestResult; errorMessage?: string | null }): JSX.Element;
```

### 4.3 REST / WebSocket / CLI

无。后端端点 `POST /api/py/strategies/{id}/backtest` 与其响应契约（含第 1 轮加入的 `extendedMetrics`）**完全不变**；本轮不触碰任何网络接口。

---

## 五、数据模型（Data Model）

- **无 DB schema 变更**、**无前端 store 变更**、**无 `lib/types.ts` 变更**。回测 run 落库路径、`metrics_json`、`extendedMetrics` 响应形状全部沿用第 1 轮。
- **新增内存形状**：弹窗内部的 `Step = "config" | "result"` 与会话级 `ranThisSession: boolean`（均为组件局部 `useState`，不持久化、不进 store、不进 URL；`ranThisSession` 取代原 `hasRun`，随「打开」重置）。
- **既有内存形状复用**：`BacktestConfig`（第一步表单态）、`BacktestResult` + `ExtendedMetrics`（第二步渲染源）、`EquityPoint[]`（`equityQ` → 净值缩略图与 CSV 导出），均不改。
- **步骤不持久化**：关闭并重开弹窗恒回到第一步（由 `seededRef` 重置保证），避免「重开却停在旧结果页」的错位。

---

## 六、测试与验收（Testing & Acceptance）

### 6.1 组件测试（`BacktestConfigModal.test.tsx`，vitest + testing-library，`NODE_ENV=test`，见记忆「Aragon web vitest NODE_ENV」）

既有用例的适配与新增用例：

1. **第一步默认渲染**（沿用）：打开显示标题「运行回测」、标的、区间预设与周期 chips、「开始回测」可用；**步骤指示器**显示「① 配置」为当前步（`aria-current="step"`）。
2. **非法区间禁用运行**（沿用）：`startTs>=endTs` 时「开始回测」禁用并显示「起始时间必须早于结束时间」。
3. **运行回调**（沿用）：点「开始回测」以当前 `cfg` 调 `onRun`（断言入参对象）。
4. **点运行后进入第二步**（新增/改造）：`renderModal({ result: RESULT })` 点「开始回测」→ 出现「回测结果」小标题与指标（如「12.00%」「年化收益」）与覆盖横幅（**子串/正则**匹配，沿用既有 `/仅覆盖最近 200 \/ 720 根/`，因实际 DOM 含前后缀文案，见 R-7）；页脚出现「返回上一步」；步骤指示器「② 结果」为当前步。
5. **返回上一步**（新增）：在第二步点「返回上一步」→ 回到第一步（区间/周期 chips 再次可见，指标网格从文档移除，`onRun` 不被再次调用）。
6. **未运行不显示结果**（新增，守防陈旧）：`renderModal({ result: RESULT })` **不点**运行 → 指标/覆盖横幅**不在文档**（仍在第一步）。
7. **运行态**（改造）：`renderModal({ running: true })` 点「开始回测」→ 第二步**正向断言出现运行骨架**（`Skeleton`，如按 `.skeleton` 或骨架数量）且不出现空态占位文案；「返回上一步」可用。（R-7：保留正向骨架断言，不只断言空态缺席。）
8. **错误态**（改造）：`renderModal({ errorMessage: "boom" })` 点运行 → 第二步显示「回测失败：boom」，并提供「重新回测」。
9. **无障碍**（沿用）：Esc 与点遮罩触发 `onClose`；进入第二步后焦点落在「回测结果」小标题。
10. **回看已有结果不重跑**（新增，守 R-1）：`renderModal({ result: RESULT })` 点「开始回测」进入第二步 → 点「返回上一步」回到第一步 → 步骤指示器「② 结果」此时**可点**，点击后再次显示指标（如「12.00%」），且 `onRun` **仅被调用过 1 次**（前进回看不触发重跑）。

### 6.2 手动验收（对齐本轮需求 Definition of Done）

- [ ] 点「运行回测」弹窗默认在**第一步（配置）**，结果**不再**堆在配置下方。
- [ ] 点「开始回测」推进到**第二步（独立结果面板）**，就地显示运行态→指标/覆盖/净值缩略图。
- [ ] 第二步提供**「返回上一步」**，返回后配置保持、可微调重跑。
- [ ] 返回第一步后，若不改参数，可经步骤指示器「② 结果」**直接回看已有结果、无需重跑**（R-1）。
- [ ] 顶部步骤指示器正确反映 ① 配置 / ② 结果，当前步高亮、已完成步可回退、已运行后「② 结果」可前进。
- [ ] 原子、因子、组合三类策略均沿用该两步弹窗（父组件零改动即生效）。
- [ ] 明暗主题、键盘可达、焦点管理正确、无横向溢出。
- [ ] `pnpm --filter web test`（`vitest run`）与 `pnpm --filter web build` 全绿；无 TS 报错。

---

## 七、风险与缓解（Risks & Mitigations）

| 风险 | 影响 | 缓解 |
|---|---|---|
| 切到第二步时结果尚未返回，出现空白闪烁 | 观感差 | 第二步先渲染 `running` 骨架（`ResultStep` 运行态），结果到达再就地替换 |
| 运行中点「返回上一步」，mutation 仍在飞行 | 状态错位 | 返回仅切屏、不打断 mutation；`cfg` 保持；若成功时用户在第一步，不强制跳转（下次进入第二步可见最新 `result`）；「返回」始终安全幂等 |
| 删除结果区空态 / 改 `hasRun`→`step` 破坏「防陈旧结果」保护 | 跨策略误显旧指标 | 打开恒 `setStep("config")`；第二步仅在用户本次点运行后进入；用例 6 专门守此不变量 |
| 拆 `ConfigStep`/`ResultStep` 时误改表单逻辑或指标口径 | 回归 / 指标漂移 | 表单 JSX 与派生逻辑逐字搬迁、指标继续复用 `BacktestMetricsGrid`/`EquitySparkline`；组件测试守回归 |
| 焦点管理抢焦点或引发滚动跳动 | 无障碍/体验退化 | 用 `focus({preventScroll:true})` 落到步骤内标题；焦点始终在 `Modal` 陷阱内；`aria-live="polite"` 播报完成 |
| 移焦时机晚于按钮卸载，焦点回落 `<body>` 令陷阱瞬时失效（R-2） | 无障碍退化、Tab 不可预期 | 移焦用 `useLayoutEffect` 同步于同一提交阶段；标题 `tabIndex={-1}` 作落点；返回时退回 `configFirstRef`/容器 |
| 返回第一步微调后被迫重跑才能看回结果（R-1） | 时延/成本浪费、违背顶级 HCI | 引入会话级 `ranThisSession` + `goForward`；已运行后「② 结果」可前进回看、不重跑；用例 #10 守此 |
| 既有测试语义随两步流变化 | CI 红 | 6.1 明确逐条适配；保留 onRun/校验/关闭等核心断言，新增步骤流断言 |
| 父组件误以为需要配合改动 | 无谓改动扩面 | 显式声明 props 契约不变、父组件零改；变更计划列出「不改」清单 |

---

## 八、非目标与后续扩展（Non-goals / Future）

- 本轮**不做**：第三步及以上的多步流、步骤持久化到 URL/store、结果对比（多次运行并列）、新增绩效指标或后端改动、动画库引入。
- 后续可选：把配置一键带入 Walk-forward / 多样本作为统一「回测运行配置」；第二步加「参数快照」摘要条（回显本次 `cfg`）；结果页加「复制指标」/分享。

---

## 评审结论（Subtask #1 · Review Verdict）

**结论：有条件通过（Approved with conditions）** — 文档升级至 **v2**。

**判定依据**：方案在四个维度均成立——(1) **可行性**：纯前端、仅动 `BacktestConfigModal.tsx`，所用 `Button` 变体、`Modal` 焦点陷阱、`BacktestMetricsGrid`/`EquitySparkline`/`equityQ` 均为现成资产，父层 `running=isPending`/`result=data`/`onRun→mutate` 接线已核对可支撑无闪烁自动推进；(2) **完整性**：补齐了原方案缺失的「已运行后前进回看」路径（R-1）与焦点时机边界（R-2）；(3) **一致性**：对外 props 契约逐字不变，父组件与后端零改动，命名/单文件阈值/键盘可达均对齐 CLAUDE.md；(4) **范围**：收敛得当，未过度设计（不引动画库、不做多步流/持久化）。

**已在 v2 就地修复（P1，无遗留）**：
- **R-1**（P1，Completeness/HCI）：引入会话级 `ranThisSession` + `goForward`，步骤指示器「② 结果」在已运行后可点前进、回看结果不重跑。
- **R-2**（P1，a11y）：`useLayoutEffect` 同步移焦 + 标题 `tabIndex={-1}` + 返回焦点回退，闭合焦点陷阱失效窗口。

**放行条件（P2，代码开发 Subtask #2 须逐条落实，均已在正文写明）**：
1. **R-3**：`ResultStep` 分支顺序固定 `running → error → result`，不得调换（防旧指标闪回）。
2. **R-4**：父层外部置 `running` 而用户在第一步时不自动跳步，主按钮显示「回测中…」并禁用。
3. **R-5**：步骤指示器三段用真正的 `<button type="button">`（`aria-current`/`disabled`），非可点 `div`。
4. **R-6**：`BacktestConfigModal.tsx` 守 ≤ 800 行；逾阈方可抽兄弟文件，并登记到「实施过程发现的方案缺陷」段。
5. **R-7**：覆盖横幅断言用子串/正则；运行态用例保留正向「骨架出现」断言。

**验收对齐（Definition of Done）**：本轮需求「结果独立成第二步 + 可返回上一步」已被 §2.3~§2.5、§6 完整覆盖；R-1 修复进一步保证「已完成步骤间自由切换」。P0/P1 已清零，可进入 Subtask #2 代码开发。

**版本升级说明**：v1（Subtask #0 方案设计）→ **v2**（Subtask #1 评审修复：新增「评审记录」「评审结论」两段，修复 R-1/R-2 两个 P1，并把 R-3~R-7 五个 P2 固化为正文实现约束）。

> 实施过程发现的方案缺陷：（占位，Subtask #2 在此回填）
