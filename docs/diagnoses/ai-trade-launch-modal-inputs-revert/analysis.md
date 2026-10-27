# AI 自动交易启动弹窗输入被每秒重置 · 诊断报告

- **Bug slug**: `ai-trade-launch-modal-inputs-revert`
- **诊断日期**: 2026-07-07
- **节点**: Subtask #0（问题定位与分析）
- **结论一句话**: `AiTradeLaunchModal` 的「打开即回填默认」重置 `useEffect`（引入于 commit `73e9b3b`）把**非稳定引用**（`seededMembers`、`defaultStrategyIds`）写进了依赖数组，配合 `page.tsx` 行情流按 **1s/0.5s** 节流刷帧引发的父树级联重渲染，使该 effect 每秒重跑一次并把用户正在编辑的全部表单字段冲回默认值。

---

## 1. 问题描述（Problem description）

打开「启动 AI 自动交易」弹窗后，用户**任何**编辑动作——勾选/取消交易策略、切换时间级别、增删标的、修改分配百分比、填写预算/杠杆/止盈止损等输入框——都会在大约 **0.5~1 秒** 后被自动还原到弹窗刚打开时的默认状态。表现为「无论怎么操作，一秒钟后就恢复原样」，弹窗实际上无法正常配置与提交。

该问题由「最近的修改」引入，对应 commit `73e9b3b`（*feat: auto-fill AI auto-trade launch modal with left-panel symbols (multi-symbol group) and add an editable timeframe*，Round 6）。此前版本无此现象。

## 2. 复现步骤（Reproduction steps）

前置：前端已 `pnpm dev` 起动（`apps/web`），后端服务可用；页面上行情流处于活动状态（默认/纸面/回测/对比等模式下 `shouldStreamMarket` 为真时，行情 WebSocket 会持续推送 ticker/trade/book）。

1. 打开 Web 应用主页面，确认图表正在接收实时行情（顶部最新价/成交带在跳动，说明行情流 flush 正在以 1~2Hz 刷新页面）。
2. 右侧「AI 助手」面板头部，点击「AI 自动交易」开关 → 打开启动弹窗。
3. 在「交易策略」清单里勾选任意一个策略（此时勾选生效）。
4. **静置约 1 秒不做任何操作**，观察：勾选被自动取消、时间级别下拉回到默认、金额输入框被清空、标的列表回填为默认。
5. 换其它字段（时间级别下拉、标的百分比输入框、预算输入框）重复步骤 3~4，现象一致：约 0.5~1 秒后回退。

> 最小复现要点：只要「弹窗处于打开状态」且「页面每约 1 秒发生一次重渲染」，即可稳定触发。页面每秒重渲染由行情流节流 flush 提供（见 §3 证据 C）。

补充观测（用于确认根因）：临时把浏览器网络断开或切到无行情流的模式，让页面停止每秒重渲染，则弹窗输入**不再**自动回退——反证「重渲染节拍」是触发条件之一。

## 3. 根因分析（Root cause analysis）

### 3.1 直接原因：重置 effect 把非稳定引用写进依赖数组

`AiTradeLaunchModal` 用一个「打开即回填默认」的 effect 给所有本地表单 state 播种（seed）。它的**本意**是「弹窗每次*打开*时回落默认」，注释亦如此写：

```
// 弹窗每次打开：回落默认段 / 成员 / 分配模式 / 周期 / 策略 / 轮数 / 区间并清空金额输入。
```
`apps/web/components/Agent/AiTradeLaunchModal.tsx:148`

effect 体把用户可编辑的每一个字段都重置回默认：

```tsx
useEffect(() => {
  if (!open) return;
  setMode(liveAvailable ? "live" : "backtest");
  setMembers(seededMembers);
  setAllocationMode(defaultAllocationMode);
  setBar(propBar as Bar);
  setInstId(seededMembers[0]?.instId ?? defaultInstId);
  setAddSymbolRaw("");
  setAddError(null);
  setSelectedIds(defaultStrategyIds);          // ← 冲掉用户刚勾选的策略
  setRoundsRaw(...); setCapitalRaw(""); ...     // ← 清空所有输入框
  ...
}, [open, propBar, defaultAllocationMode, defaultInstId, seededMembers,
    defaultStrategyIds, defaultContextRounds, defaultStartTs, defaultEndTs,
    defaultDecisionEveryBars, liveAvailable]); // eslint-disable-line react-hooks/exhaustive-deps
```
`apps/web/components/Agent/AiTradeLaunchModal.tsx:149-171`

问题在依赖数组：其中 `seededMembers` 与 `defaultStrategyIds` 是**数组类型**，且在父组件每次渲染时可能是**全新引用**。React 对依赖用 `Object.is` 做浅比较，引用一变 effect 就重跑——于是「打开时回填」被悄悄降级成了「几乎每次渲染都回填」。effect 一重跑就把上面所有 `setX` 再执行一遍，用户的编辑随即被覆盖。

- **`defaultStrategyIds`（主要每帧变化源）**：父组件内联现算，且在偏好未保存时返回全新数组字面量：

  ```tsx
  const defaultStrategyIds =
    aiTradeConfig?.strategyIds && aiTradeConfig.strategyIds.length > 0
      ? aiTradeConfig.strategyIds
      : strategyId != null
        ? [strategyId]      // ← 每次渲染都是新数组
        : [];               // ← 每次渲染都是新数组
  ```
  `apps/web/components/Agent/AiTradeToggle.tsx:156-161`，作为 prop 下传于 `AiTradeToggle.tsx:199`。

  store 默认 `aiTradeConfig: null`（`apps/web/lib/store.ts:495`），因此首次/未保存偏好时走 `[strategyId]` 或 `[]` 分支——**每次父渲染都是新引用**，稳定地把 effect 依赖打脏。

- **`seededMembers`（次要变化源）**：`useMemo` 依赖 `[defaultMembers, defaultInstId]`：

  ```tsx
  const seededMembers = useMemo<AutoTradeMember[]>(() => {
    const src = defaultMembers.length > 0 ? defaultMembers : [{ instId: defaultInstId, allocationPct: 100 }];
    return src.slice(0, AI_TRADE_MAX_SYMBOLS).map((m) => ({ ...m }));
  }, [defaultMembers, defaultInstId]);
  ```
  `apps/web/components/Agent/AiTradeLaunchModal.tsx:132-135`

  `defaultMembers = autoTradeMembers`（store 切片，默认 `[]`，`store.ts:488`）在纯重渲染下引用稳定，故 `seededMembers` 大多数帧稳定；但**一旦** `autoTradeMembers` 被替换（workspace 首次水合 GET、`evenSplit`、增删成员等 action 都会产生新数组），它也会成为一次额外的重置触发源。它同样是本次回归新引入的非稳定依赖。

### 3.2 触发节拍：行情流按 1s/0.5s 节流刷帧 → 父树级联重渲染

effect 要「每秒重跑」，需要弹窗所在子树「每秒重渲染」。`page.tsx` 的行情流恰好提供了这个鼓点——高频 book/ticker/trade 经自适应节流器缓冲后，**按 1s/0.5s 一次性刷入 React state**：

```tsx
// 自适应节流器：把高频 book/ticker/trade（含其 lastPrice）缓冲后按 1s/0.5s 节奏一次性刷入 state，
// 把面板重渲染从 20~40+Hz 降到 1~2Hz（诊断 market-depth-refresh-too-frequent，方案 A）。
const throttle = createMarketThrottle({
  flush: (frame) => {
    if (frame.book) setOrderBook(frame.book);
    if (frame.ticker) setMarketTicker(frame.ticker);
    if (frame.trades.length) setTradeTape((prev) => foldTradeTape(prev, frame.trades));
    if (frame.lastPrice != null) setLastPrice(frame.lastPrice);
    ...
  },
});
```
`apps/web/app/page.tsx:398-412`

每次 flush 触发 `setMarketTicker / setTradeTape / setLastPrice / setLastMarketTs`，令 `page` 组件以 **1~2Hz** 重渲染。这正是用户所述「大概一秒钟或者半秒钟」的来源。

### 3.3 渲染级联路径无记忆化，重渲染直达弹窗

`page` 的重渲染沿组件树无阻碍下传到弹窗（沿途均无 `React.memo`）：

- `apps/web/app/page.tsx:661` → `<RightPanel />`
- `apps/web/components/RightPanel/RightPanel.tsx:25` → `<AgentPanel />`（两面板「始终挂载、用 hidden 切换」，见 RightPanel.tsx:3-4，故即便当前不在 AI 助手页签也保持挂载与重渲染）
- `apps/web/components/Agent/AgentPanel.tsx:28`（import）、`:333` → `<AiTradeToggle />`（全文件无 `memo(` / `React.memo`，评审已核验）
- `apps/web/components/Agent/AiTradeToggle.tsx:185` → `<AiTradeLaunchModal ... defaultStrategyIds={defaultStrategyIds} ... />`

`AiTradeToggle` 一旦随父树重渲染，就在 `AiTradeToggle.tsx:156-161` 重算出**新的** `defaultStrategyIds` 数组，作为 prop 传入弹窗。弹窗 effect 的 `defaultStrategyIds` 依赖被打脏 → effect 重跑 → 表单被重置。闭环成立。

> 注：`AiTradeLaunchModal` 在 `AiTradeToggle` 内**无条件渲染**（`AiTradeToggle.tsx:185`，仅靠 `open` prop 控制显隐），故其 hooks 始终运行，effect 在 `open===true` 时每帧都有机会重跑。

### 3.4 回归佐证：commit 73e9b3b 改动前后对比

同一个 effect 在改动前的依赖数组**全是原始类型**，因此只在 `open` 切换或某个原始默认值真变化时才跑（`setSelectedIds(defaultStrategyIds)` 在旧代码里也存在，但 `defaultStrategyIds` 被**刻意排除**在依赖之外，正是为避免此类抖动）：

```
// 改动前（b9f1783）：
}, [open, defaultInstId, defaultContextRounds, defaultStartTs, defaultEndTs, defaultDecisionEveryBars, liveAvailable]);

// 改动后（73e9b3b，本次回归）：
}, [open, propBar, defaultAllocationMode, defaultInstId, seededMembers, defaultStrategyIds, defaultContextRounds, defaultStartTs, defaultEndTs, defaultDecisionEveryBars, liveAvailable]);
```
（来源：`git show 73e9b3b -- apps/web/components/Agent/AiTradeLaunchModal.tsx`）

改动把 `seededMembers`、`defaultStrategyIds`（以及 `propBar`、`defaultAllocationMode`）加进依赖，其中两个数组型依赖是非稳定引用——这是本次「一秒回退」回归的**唯一**必要且充分的差异。

**评审补强（关键佐证）**：改动前的 `b9f1783` 版本，effect 体内**同样已有** `setSelectedIds(defaultStrategyIds)`（`git show b9f1783:.../AiTradeLaunchModal.tsx` 第 126 行），但其依赖数组（第 139 行）**刻意不含** `defaultStrategyIds`：

```
// b9f1783 effect 体（节选）
122  useEffect(() => {
123    if (!open) return;
...
126    setSelectedIds(defaultStrategyIds);
...
139  }, [open, defaultInstId, defaultContextRounds, defaultStartTs, defaultEndTs, defaultDecisionEveryBars, liveAvailable]); // eslint-disable-line react-hooks/exhaustive-deps
```

即：原作者**明知**该 effect 会读取非稳定的数组 default，并靠 `// eslint-disable-line react-hooks/exhaustive-deps` 主动把它挡在依赖之外，正是为规避「每帧回填」抖动。`73e9b3b` 在补齐 lint 依赖时把这两个数组也一并写回依赖数组，等于**撤销了这道刻意的防抖屏障**——这把「本次回归的唯一差异是这两个非稳定依赖」的结论从「推断」升级为「有版本证据的确证」。

## 4. 影响面（Impact surface）

- **受影响功能**：AI 自动交易「启动」弹窗（`AiTradeLaunchModal`）的**实盘**与**回测**两种模式的全部可编辑项：多选策略、时间级别、标的列表/分配百分比/分配模式、预算/杠杆/总止盈损、单仓 OCO 止盈损%、上下文刷新轮数、回测区间/决策间隔/共识门槛、真实资金勾选。
- **受影响用户**：所有尝试使用「启动 AI 自动交易 / AI 回测」的用户。在 `aiTradeConfig` 尚未保存（首次或多数使用场景，`defaultStrategyIds` 走新数组分支）时为**必现**；此为功能级阻断——用户几乎无法完成配置与提交（除非在两次 flush 之间的极短窗口里飞快勾选并点确认）。
- **不受影响**：后端 `services/quant` 全链路零改动，仅前端弹窗交互受损；已在运行中的 AI 交易分组不受影响（弹窗仅用于「启动」）。
- **触发依赖**：需页面存在约 1Hz 的重渲染鼓点。行情流节流 flush（`page.tsx:398-412`）是最常见来源；其它任何令 `page`/子树以 ~1Hz 重渲染的因素亦可触发，故根因与具体行情模式**无关**，属设计缺陷而非偶发时序。

## 5. 候选修复方案（Candidate fixes）

> 本节点不写修复代码，仅给出候选与推荐。

| 方案 | 做法 | 侵入性 | 风险 | 工作量 | 是否根治 |
|---|---|---|---|---|---|
| **A（推荐）** | 把重置逻辑收敛为「仅在 `open` 由 false→true 的那一次」执行：用 `useRef` 记录上一次 `open`，仅在 `!prevOpen && open` 时播种；依赖数组只保留 `open`（配合既有 eslint-disable 读取最新默认值）。 | 低（仅改弹窗一个 effect） | 低（与 effect 文档化本意一致，现有测试不校验「重开即重置」故不破坏） | 小 | 是——无论父渲染频率/引用稳定性如何都不再抖动 |
| **B** | 保持「依赖变化即重置」语义，改为**稳定化**上游引用：父组件 `useMemo` 包裹 `defaultStrategyIds`、确保 `seededMembers`/`autoTradeMembers` 稳定，依赖数组据实补全。 | 中（改父组件 + 弹窗） | 中（脆弱：日后任一默认值 prop 再次非稳定即复发；memo 依赖需人工维护正确） | 中 | 否——只压住当前引用，未纠正「用户编辑期间不应被 seed 覆盖」的设计 |
| **C** | 降低父树重渲染频率/隔离：对 `AiTradeToggle` 或 `AgentPanel` 施加 `React.memo`，或把行情流 state 移出 `page` 渲染路径。 | 高（触及共享组件/页面架构） | 高（可能引入与本 bug 无关的行情/面板刷新回归） | 大 | 否——只移除当前鼓点，effect「任一合法默认变化即重置」的缺陷仍在 |

## 6. 推荐修复（Recommended fix）

**采用方案 A**：把「回填默认」严格限定在弹窗**打开的那一次转换**上（`open` false→true），而不是「依赖数组任一项变化就跑」。

推荐理由：

1. **与 effect 本意完全吻合**——注释即写「弹窗每次*打开*：回落默认」（`AiTradeLaunchModal.tsx:148`）。当前把它当成「同步外部默认」的 effect 使用才导致抖动；方案 A 让实现回到语义。
2. **最小破坏面**——改动局限在弹窗单个 effect，不触碰父组件、页面架构或行情流；`services/quant` 与其它前端组件零波及。
3. **健壮**——不依赖任何 prop 的引用稳定性，也不依赖父树重渲染频率，从根上消除「编辑被覆盖」，而非只压制当前触发源（相较 B/C 更彻底）。
4. **不破坏现有测试**——`AiTradeLaunchModal.test.tsx` 仅覆盖「打开态下的回填/校验/确认」等，未断言「改 prop 后重开即重置」，方案 A 保留「打开即播种」故这些用例仍通过。

实现要点（供 Subtask #2 参考，非本节点交付）：

- 引入 `const prevOpenRef = useRef(false);`，在 effect 内 `if (open && !prevOpenRef.current) { …播种… }`，并在末尾 `prevOpenRef.current = open;`（或等价地仅以 `open` 为依赖）。
- 依赖数组精简为 `[open]`，保留 `eslint-disable-next-line react-hooks/exhaustive-deps`，与旧版一致地在「打开瞬间」读取当时的默认值。
- 建议补一条回归测试：模拟「弹窗 `open` 保持为真、父传入变化的 `defaultStrategyIds` 新引用」后，断言用户已勾选的 `selectedIds` **不被**重置（把本次坑固化为护栏）。

---

### 附：关键证据索引

- 重置 effect 及非稳定依赖：`apps/web/components/Agent/AiTradeLaunchModal.tsx:132-135`、`:148-171`
- 父组件内联现算的 `defaultStrategyIds`：`apps/web/components/Agent/AiTradeToggle.tsx:156-161`、下传 `:199`
- 行情流 1s/0.5s 节流 flush（重渲染鼓点）：`apps/web/app/page.tsx:398-412`
- 渲染级联路径（均无 memo）：`page.tsx:661` → `RightPanel.tsx:25` → `AgentPanel` → `AiTradeToggle.tsx:185`
- store 默认值：`autoTradeMembers: []`（`store.ts:488`）、`aiTradeConfig: null`（`store.ts:495`）
- 回归来源 commit：`73e9b3b`（对比 `b9f1783` 的 effect 依赖数组）

---

## 7. 评审补充证据与核验（Reviewer verification）

本节由 Subtask #1（评审）补充，目的是把根因证据链收敛为「无缺口」，并排除候选反例。

### 7.1 逐条引用复核：全部命中当前源码

评审逐一打开被引文件核对行号，结论：**分析报告的每一处代码引用均与仓库当前内容一致**。

| 引用 | 复核结果 |
|---|---|
| 重置 effect 及依赖数组 `AiTradeLaunchModal.tsx:148-171` | ✅ 注释在 148、`if (!open) return` 在 150、依赖数组在 171，逐字一致 |
| `seededMembers` useMemo `:132-135`（deps `[defaultMembers, defaultInstId]`） | ✅ 一致 |
| `defaultStrategyIds` 内联现算 `AiTradeToggle.tsx:156-161`，下传 `:199` | ✅ 一致（`:199` 即 `defaultStrategyIds={defaultStrategyIds}`） |
| 弹窗无条件渲染 `AiTradeToggle.tsx:185` | ✅ 一致（仅 `open` prop 控显隐） |
| 行情流节流 flush `page.tsx:398-412` | ✅ 一致，flush 内 `setMarketTicker/setTradeTape/setLastPrice/setLastMarketTs` 均在 |
| 级联 `page.tsx:661` → `RightPanel.tsx:25` → `AgentPanel.tsx:333` → `AiTradeToggle.tsx:185` | ✅ 一致；沿途 `RightPanel`/`AgentPanel` 文件内均无 `memo(`（评审 grep 核验） |
| store 默认 `autoTradeMembers: []`（`store.ts:488`）、`aiTradeConfig: null`（`store.ts:495`） | ✅ 一致 |
| 回归 diff（`b9f1783`→`73e9b3b`） | ✅ 评审用 `git show` 复算，依赖数组前后差异逐字吻合（见 §3.4 补强） |

### 7.2 唯一重置源确认：排除「其它原因」反例

审计问「症状是否可能由报告未提及的另一处代码引起」。评审对弹窗做了穷举：

- 全文件仅有**一个** `useEffect`（`:149`）会写 state；其余所有 `setX` 均为**用户事件处理器**（`toggleId`/`updateAllocation`/`removeMember`/`applyEvenSplit`/`handleAddSymbol` 及 `onChange`），只在用户主动操作时触发，不构成「自动回退」来源。
- `configQ`/`availUsdt`（`:141-146`）仅喂 `previewBudget` 这个 `useMemo`（`:269-273`）做**预览展示**，**从不**回写任何输入 state，故不是候选反例。
- 计时器排查：`AiTradeToggle` 内 `statusQ`（`:59-64`）的 `refetchInterval` 为 **15_000ms**（`AiTradeToggle.tsx:64`），与「约 1 秒回退」的节拍不符；`strategiesQ`/`configQ` 无 `refetchInterval`。唯一 ~1Hz 节拍来自行情流节流 flush（`page.tsx:400-412` 注释自述「1s/0.5s 一次性刷入」）。

因此「重置动作来自 `:149` 这唯一 effect」是排他性结论，报告根因无遗漏的替代解释。

### 7.3 触发范围精化：`aiTradeConfig` 是否已保存决定是否「必现」

评审确认报告 §4 的「必现」限定准确，并补一层机制说明，避免过度泛化：

- **未保存偏好（首次/多数场景，`aiTradeConfig === null`）**：`defaultStrategyIds` 每帧走 `[strategyId]`/`[]` 新数组分支 → 依赖每帧变脏 → 行情流每次 flush 都触发重置。**此为必现**，也正是用户报告的场景。
- **已保存偏好（`aiTradeConfig.strategyIds` 非空）**：此时 `defaultStrategyIds` 取 store 内**稳定引用**；`seededMembers` 的 useMemo 依赖 `[defaultMembers, defaultInstId]` 在纯重渲染下亦稳定；其余依赖皆为原始类型。故**纯行情 flush 不会**触发重置。但只要 `autoTradeMembers` 被某 action 替换（水合 GET / `evenSplit` / 增删成员），`seededMembers` 换新引用仍会触发一次重置——即便已保存偏好，缺陷依旧潜伏。

结论：缺陷**不因**偏好已保存而消失，仅触发频率随场景变化；「必现」对首次用户成立。

### 7.4 推荐方案 A 的回归安全性（评审重点核验）

审计要求确认「最小修复是否引入别处回归」。方案 A 会一并**移除**旧代码「open 期间某默认 prop 变化即重置」的行为（依赖收敛为 `[open]`），评审据此核验被牺牲的语义是否安全：

- **`liveAvailable` 变化不再触发重置** → 安全。`liveAvailable`（`= liveGateReason == null`）在**每次渲染**直接参与实盘段控禁用与 CTA 门禁判定（不经该 effect），故即便 open 期间实盘由可用转不可用，段控与「确认」按钮仍会实时禁用，无法误提交被门禁的实盘启动。
- **`defaultInstId`/`propBar`/区间等变化不再回填** → 安全且更符合语义。弹窗是「配置一次并启动」的一次性表单，`Modal` 为遮罩层，open 期间用户几乎无从改动图表标的/周期；旧代码在这些 prop 变化时回填，本身就是另一处「悄悄冲掉用户编辑」的隐患。方案 A 收敛为「打开即播种、之后交给用户」反而消除了它。
- **首挂载播种不受影响** → 测试安全。`AiTradeLaunchModal.test.tsx` 全部用例都以 `open` 初始为真挂载；`[open]` 依赖下 effect 在**挂载时**即运行一次并完成播种，故 13+ 条用例（回填成员/校验/确认/回测段）均保持通过（评审已通读该测试文件确认无「改 prop 后重开即重置」类断言）。

---

## 评审结论（Review Verdict）

**采用方案 A。最终推荐的唯一修复：把重置 effect 的依赖数组从当前 11 项收敛为 `[open]`（保留既有 `// eslint-disable-line react-hooks/exhaustive-deps` 与体内 `if (!open) return;` 卫句），并补一条回归测试。** 这是能根治本 Bug 的**最小改动**。

### 为什么是这个修复

1. **命中唯一必要且充分的回归差异**。§3.4 + §7.1 的 git 证据表明，`73e9b3b` 相对 `b9f1783` 的**唯一**行为差异，就是把非稳定的 `seededMembers`/`defaultStrategyIds` 写回依赖数组，撤销了原作者刻意设置的防抖屏障。把依赖收敛回 `[open]`，即精确复原「仅在弹窗打开的那次转换播种」的原意（注释 `:148` 本就如此声明），不多做也不少做。
2. **改动面最小、无外溢**。仅改弹窗内一行依赖数组；不动父组件 `AiTradeToggle`、不动 `page.tsx` 行情流、不加 `React.memo`、不碰 `services/quant`。相较方案 B（须在父层 `useMemo` 稳定化 `defaultStrategyIds` 且长期维护正确、任一新非稳定 prop 即复发）与方案 C（触及共享面板/页面架构、有行情刷新回归风险），A 的破坏面与回归风险都最低。
3. **对引用稳定性与重渲染频率彻底免疫**。`[open]` 依赖下，effect 只在 `open` 真正变化时运行，任何频率的父树重渲染、任何 prop 引用抖动都不再触发重置——从根上消除「编辑被覆盖」，而非仅压制当前触发源。
4. **回归安全已核验**（§7.4）：被移除的「open 期间默认变化即回填」语义要么由每帧直算的门禁替代（`liveAvailable`）、要么本就是隐患（`defaultInstId` 等），无安全回归；现有测试因首挂载仍播种而全部保持通过。

### 实现约束（交给 Subtask #2 落地，本节点不写代码）

- **最小实现**：将 `:171` 的依赖数组由
  `[open, propBar, defaultAllocationMode, defaultInstId, seededMembers, defaultStrategyIds, defaultContextRounds, defaultStartTs, defaultEndTs, defaultDecisionEveryBars, liveAvailable]`
  改为 `[open]`；保留 `// eslint-disable-line react-hooks/exhaustive-deps` 与体内 `if (!open) return;`。语义上等价于「用 `useRef` 记上一次 `open`、仅在 `!prev && open` 播种」，二者取其一即可（`[open]` 变体更小，优先）。
- **必须补回归测试**（把本坑固化为护栏，也是本次回归能溜过 CI 的原因——现有用例从不模拟「open 保持为真时父传入变化的 `defaultStrategyIds`/`seededMembers` 新引用」）：断言「弹窗 `open` 恒为真、rerender 传入**新的** `defaultStrategyIds` 引用后，已勾选的策略 / 已编辑字段**不被**重置」。
- **不改动范围**：除该 effect 依赖数组与新增测试外，不触碰其它文件；`defaultStrategyIds`/`seededMembers` 的上游计算保持原样（方案 A 不依赖其引用稳定性）。
