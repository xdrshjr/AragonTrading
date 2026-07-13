# AI 自动交易启动弹窗输入被每秒重置（复发）· 诊断报告

- **Bug slug**: `ai-trade-launch-modal-inputs-revert-recurrence`
- **诊断日期**: 2026-07-13
- **节点**: Subtask #0（问题定位与分析）
- **轮次**: 第 2 轮（复发。第 1 轮同症状诊断见 `docs/diagnoses/ai-trade-launch-modal-inputs-revert/analysis.md`）
- **结论一句话**: 第 1 轮诊断出的根因——`AiTradeLaunchModal` 的「打开即回填默认」重置 `useEffect` 依赖数组含**非稳定引用**（`seededMembers`、`defaultStrategyIds`）——**从未被修复**（第 1 轮无修复提交，依赖数组自 `73e9b3b` 至今一字未改）。此后两个「最近的修改」（`0a61ce0` 预算联动、`cc3114b` 仓位档位）不但没修，反而（a）在弹窗打开态内**新增了重渲染来源**，（b）**新增了一个会被该坏 effect 一并重置的用户可编辑控件（仓位操作档位）**，于是「无论怎么操作、约 1 秒后回退」的现象完整复现，且现在连新加的仓位档位下拉也被冲回默认。

---

## 1. 问题描述（Problem description）

打开「启动 AI 自动交易」弹窗后，用户**任何**编辑动作——勾选/取消交易策略、切换时间级别、增删标的、修改分配百分比、选择**仓位操作档位**、填写预算/杠杆/止盈止损等输入框——都会在大约 **0.5~1 秒** 后被自动还原到弹窗刚打开时的默认状态。表现为「无论怎么操作，一秒钟后就恢复原样」，弹窗实际上无法正常配置与提交。

这是第 1 轮已诊断问题的**复发**。第 1 轮（`docs/diagnoses/ai-trade-launch-modal-inputs-revert/`）已把根因定位到重置 effect 的非稳定依赖并给出推荐修复（把依赖数组收敛为 `[open]`），但**该修复从未落地**（见 §3.4 的 git 证据），因此缺陷一直存在；本轮的「最近的修改」又在同一个坏 effect 上叠加了新的可编辑字段与新的重渲染来源，使症状再次凸显——尤其是新加的「仓位操作档位」下拉现在也会被每秒冲回默认。

## 2. 复现步骤（Reproduction steps）

前置：前端已 `pnpm dev` 起动（`apps/web`），后端服务可用；页面处于会持续推送行情的模式（默认/纸面/回测/对比等 `shouldStreamMarket` 为真时，行情 WebSocket 持续推送 ticker/trade/book）。且 `aiTradeConfig` 尚未保存过（首次使用，或从未成功启动过 AI 自动交易——此为**必现**前提，见 §4）。

1. 打开 Web 应用主页面，确认图表正在接收实时行情（顶部最新价/成交带在跳动，说明行情流 flush 正在以 1~2Hz 刷新页面）。
2. 右侧「AI 助手」面板头部，点击「AI 自动交易」开关 → 打开启动弹窗。
3. 在「交易策略」清单里勾选任意一个策略（此时勾选生效）。
4. **静置约 1 秒不做任何操作**，观察：勾选被自动取消、时间级别下拉回到默认、金额输入框被清空、**仓位操作档位下拉回到默认**、标的列表回填为默认。
5. 换其它字段（时间级别、标的百分比、预算输入框、仓位操作档位下拉）重复步骤 3~4，现象一致：约 0.5~1 秒后回退。

> 最小复现要点：只要「弹窗处于打开状态」且「页面每约 1 秒发生一次重渲染」，即可稳定触发。页面每秒重渲染由行情流节流 flush 提供（见 §3.2 证据）。

补充观测（反证根因）：临时把浏览器网络断开或切到无行情流的模式，让页面停止每秒重渲染，则弹窗输入**不再**自动回退——反证「重渲染节拍」是触发条件之一。

## 3. 根因分析（Root cause analysis）

### 3.1 直接原因：重置 effect 把非稳定引用写进依赖数组（与第 1 轮完全相同，未修复）

`AiTradeLaunchModal` 用一个「打开即回填默认」的 effect 给所有本地表单 state 播种（seed）。注释明确写「弹窗每次*打开*」：

```
// 弹窗每次打开：回落默认段 / 成员 / 分配模式 / 周期 / 策略 / 轮数 / 区间并清空金额输入。
```
`apps/web/components/Agent/AiTradeLaunchModal.tsx:171`

effect 体把用户可编辑的每一个字段都重置回默认（**注意第 183 行的 `setPositionStep` 是本轮 `cc3114b` 新加的重置行**）：

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
  setSelectedIds(defaultStrategyIds);                              // ← 冲掉用户刚勾选的策略
  setRoundsRaw(String(defaultContextRounds || AI_TRADE_DEFAULT_CONTEXT_ROUNDS));
  setPositionStep(defaultPositionStepPct || AI_TRADE_DEFAULT_POSITION_STEP);  // ← cc3114b 新加：冲掉用户所选仓位档位
  setCapitalRaw(""); setLeverageRaw(""); ...                       // ← 清空所有输入框
  ...
}, [open, propBar, defaultAllocationMode, defaultInstId, seededMembers, defaultStrategyIds,
    defaultContextRounds, defaultStartTs, defaultEndTs, defaultDecisionEveryBars, liveAvailable]); // eslint-disable-line react-hooks/exhaustive-deps
```
`apps/web/components/Agent/AiTradeLaunchModal.tsx:172-195`

问题在依赖数组（第 195 行）：其中 `seededMembers` 与 `defaultStrategyIds` 是**数组类型**，且在父组件每次渲染时可能是**全新引用**。React 对依赖用 `Object.is` 做浅比较，引用一变 effect 就重跑——于是「打开时回填」被悄悄降级成了「几乎每次渲染都回填」。effect 一重跑就把上面所有 `setX` 再执行一遍，用户的编辑随即被覆盖。

- **`defaultStrategyIds`（主要每帧变化源）**：父组件内联现算，且在偏好未保存时返回全新数组字面量：

  ```tsx
  const defaultStrategyIds =
    aiTradeConfig?.strategyIds && aiTradeConfig.strategyIds.length > 0
      ? aiTradeConfig.strategyIds
      : strategyId != null
        ? [strategyId]      // ← 每次渲染都是新数组
        : [];               // ← 每次渲染都是新数组
  ```
  `apps/web/components/Agent/AiTradeToggle.tsx:164-169`，作为 prop 下传于 `AiTradeToggle.tsx:207`（`defaultStrategyIds={defaultStrategyIds}`）。

  store 默认 `aiTradeConfig: null`，因此首次/未保存偏好时走 `[strategyId]` 或 `[]` 分支——**每次父渲染都是新引用**，稳定地把 effect 依赖打脏。

- **`seededMembers`（次要变化源）**：`useMemo` 依赖 `[defaultMembers, defaultInstId]`：

  ```tsx
  const seededMembers = useMemo<AutoTradeMember[]>(() => {
    const src = defaultMembers.length > 0 ? defaultMembers : [{ instId: defaultInstId, allocationPct: 100 }];
    return src.slice(0, AI_TRADE_MAX_SYMBOLS).map((m) => ({ ...m }));
  }, [defaultMembers, defaultInstId]);
  ```
  `apps/web/components/Agent/AiTradeLaunchModal.tsx:147-150`

  `defaultMembers = autoTradeMembers`（`AiTradeToggle.tsx:204` 下传的 store 切片，默认 `[]`）在纯重渲染下引用稳定，故 `seededMembers` 大多数帧稳定；但一旦 `autoTradeMembers` 被替换（workspace 首次水合 GET、`evenSplit`、增删成员等 action 都会产生新数组），它也会成为一次额外的重置触发源。

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
    const marketTs = frame.marketTs;
    if (marketTs != null) setLastMarketTs((prev) => Math.max(prev ?? 0, marketTs));
  },
});
```
`apps/web/app/page.tsx`（`streamMarket` effect 内的 `createMarketThrottle`）

每次 flush 触发 `setMarketTicker / setTradeTape / setLastPrice / setLastMarketTs`，令 `page` 组件以 **1~2Hz** 重渲染。这正是用户所述「大概一秒钟或者半秒钟」的来源。

### 3.3 渲染级联路径无记忆化，重渲染直达弹窗

`page` 的重渲染沿组件树无阻碍下传到弹窗（沿途均无 `React.memo`，已 grep 核验 `RightPanel.tsx` / `AgentPanel.tsx` / `AiTradeToggle.tsx` 三文件**无任何 `memo(`**）：

- `page.tsx` → `<RightPanel />` → `<AgentPanel />`（两面板「始终挂载、用 hidden 切换」，即便当前不在 AI 助手页签也保持挂载与重渲染）→ `<AiTradeToggle />` → `<AiTradeLaunchModal ... defaultStrategyIds={defaultStrategyIds} ... />`

`AiTradeToggle` 一旦随父树重渲染，就在 `AiTradeToggle.tsx:164-169` 重算出**新的** `defaultStrategyIds` 数组，作为 prop 传入弹窗。弹窗 effect 的 `defaultStrategyIds` 依赖被打脏 → effect 重跑 → 表单被重置。闭环成立。

> 注：`AiTradeLaunchModal` 在 `AiTradeToggle` 内**无条件渲染**（`AiTradeToggle.tsx:193`，仅靠 `open` prop 控显隐），故其 hooks 始终运行，effect 在 `open===true` 时每帧都有机会重跑。

### 3.4 「复发」的准确定性：第 1 轮修复从未落地 + 最近两次修改雪上加霜

**（1）依赖数组自 `73e9b3b` 至今一字未改，第 1 轮无修复提交。**

`git log -L 195,195:apps/web/components/Agent/AiTradeLaunchModal.tsx` 显示，该行（11 项依赖数组）最后一次改动就是**引入本缺陷的 `73e9b3b`**（Round 6），此后 `0a61ce0`、`cc3114b` 均未再触碰它：

```
commit 73e9b3b  feat: auto-fill AI auto-trade launch modal ...（引入 11 项依赖）
- }, [open, defaultInstId, defaultContextRounds, defaultStartTs, defaultEndTs, defaultDecisionEveryBars, liveAvailable];
+ }, [open, propBar, defaultAllocationMode, defaultInstId, seededMembers, defaultStrategyIds, defaultContextRounds, defaultStartTs, defaultEndTs, defaultDecisionEveryBars, liveAvailable];
（此后无任何提交修改此行）
```

任务看板亦记录第 1 轮「该轮产出的 Git 提交：(无 git 提交记录)」。两处证据交叉印证：第 1 轮只产出了诊断报告，**推荐的 `[open]` 收敛修复从未写进代码**。因此严格说，本缺陷并非「修好后被回归」，而是**自 `73e9b3b` 起持续存在、从未被修复**——对用户而言即「又出现了」。

**（2）最近两次修改在同一坏 effect 上叠加了新问题面。**

- `cc3114b`（*constrain AI auto-trade open size to a user-selected position ladder*，最新一次相关提交）新增了「仓位操作档位」`<Select>` 控件（`AiTradeLaunchModal.tsx:643-662`）与其 `positionStep` state（`:140`），并往坏 effect 体内**新增了一行重置** `setPositionStep(defaultPositionStepPct || AI_TRADE_DEFAULT_POSITION_STEP)`（`:183`）。于是这个**本轮才上线的可编辑控件也被卷进每秒回退**——用户刚选好的档位约 1 秒后被冲回默认，扩大了可见回归面。
- `0a61ce0`（*drive AI auto-trade per-symbol allocation from the edited total budget*，Round 7）在弹窗内新增了 `settingsQ` / `configQ` 两个 React-Query（`AiTradeLaunchModal.tsx:157-168`，`enabled: open && mode === "live"`）及 `previewBudget` 派生（`:295-302`）。它们在弹窗打开后**异步返回**并改写 `availUsdt` / `maxBudgetPct`，在打开瞬间制造了**额外一批弹窗/父树重渲染**，每一次都重算非稳定的 `defaultStrategyIds` 并再次点燃坏 effect（叠加在行情流鼓点之上）。

综上，本轮「复发」= 未修复的坏 effect（§3.1）× 未减弱的重渲染鼓点（§3.2/3.3）×「最近修改」新增的重置字段与重渲染来源（§3.4-2）。

### 3.5 排他性：重置动作只来自这唯一 effect

已通读当前 `AiTradeLaunchModal.tsx` 全文并核验：
- 全文件仅有**一个** `useEffect`（`:172`）会写 state（`grep useEffect` 仅命中 import 与该行）。其余所有 `setX` 均为**用户事件处理器**（`toggleId`/`updateAllocation`/`removeMember`/`applyEvenSplit`/`handleAddSymbol` 及各 `onChange`），只在用户主动操作时触发，不构成「自动回退」来源。
- `0a61ce0`/`cc3114b` 新增的 `settingsQ`/`configQ` 为**只读**，仅喂 `previewBudget` 这个 `useMemo`（`:295-302`）做预览展示，**从不**回写任何输入 state。
- 计时器排查：`AiTradeToggle` 内 `statusQ` 的 `refetchInterval` 为 **15_000ms**（`AiTradeToggle.tsx:64`），与「约 1 秒」节拍不符；`configQ`/`settingsQ`/`strategiesQ` 均无 `refetchInterval`。唯一 ~1Hz 节拍来自行情流节流 flush。

因此「重置动作来自 `:172` 这唯一 effect」是排他性结论。

## 4. 影响面（Impact surface）

- **受影响功能**：AI 自动交易「启动」弹窗（`AiTradeLaunchModal`）的**实盘**与**回测**两种模式的全部可编辑项：多选策略、时间级别、标的列表/分配百分比/分配模式、预算/杠杆/总止盈损、单仓 OCO 止盈损%、**仓位操作档位（本轮新增，现也受影响）**、上下文刷新轮数、回测区间/决策间隔/共识门槛、真实资金勾选。
- **受影响用户**：所有尝试使用「启动 AI 自动交易 / AI 回测」的用户。在 `aiTradeConfig` 尚未保存（首次或多数使用场景，`defaultStrategyIds` 走新数组分支）时为**必现**；此为功能级阻断——用户几乎无法完成配置与提交（除非在两次 flush 之间的极短窗口里飞快勾选并点确认）。
- **已保存偏好的场景**：若曾成功启动过 AI 自动交易（`aiTradeConfig.strategyIds` 非空），`defaultStrategyIds` 取 store 内稳定引用、`seededMembers` 亦稳定，则**纯行情 flush 不会**触发重置；但只要 `autoTradeMembers` 被某 action 替换（水合 GET / `evenSplit` / 增删成员），仍会触发一次重置。缺陷不因偏好已保存而消失，仅触发频率随场景变化。
- **不受影响**：后端 `services/quant` 全链路零改动，仅前端弹窗交互受损；已在运行中的 AI 交易分组不受影响（弹窗仅用于「启动」）。
- **触发依赖**：需页面存在约 1Hz 的重渲染鼓点。行情流节流 flush 是最常见来源；其它任何令 `page`/子树以 ~1Hz 重渲染的因素亦可触发，故根因与具体行情模式**无关**，属设计缺陷而非偶发时序。
- **为何 CI 漏过**：`AiTradeLaunchModal.test.tsx` 的用例要么单次 `renderModal(...)`、要么用 `rerender` 但**只切换 `open`**（如 `:332` 的 `"resets the position step to default when the modal reopens (guards inputs-revert)"`，它 `open:false → open:true` 验证的是「重开即重置」的正向行为，而非本 bug）。**没有任何用例模拟「`open` 恒为真、父传入变化的 `defaultStrategyIds`/`seededMembers` 新引用」并断言已编辑字段不被重置**——正是这道缺口让缺陷长期溜过测试。

## 5. 候选修复方案（Candidate fixes）

> 本节点不写修复代码，仅给出候选与推荐。

| 方案 | 做法 | 侵入性 | 风险 | 工作量 | 是否根治 |
|---|---|---|---|---|---|
| **A（推荐）** | 把重置逻辑收敛为「仅在 `open` 由 false→true 的那一次」执行：把 `:195` 的依赖数组由 11 项收敛为 `[open]`（保留既有 `// eslint-disable-line react-hooks/exhaustive-deps` 与体内 `if (!open) return;`），语义上等价于「用 `useRef` 记上一次 `open`、仅在 `!prev && open` 播种」。 | 低（仅改弹窗一个 effect 的依赖数组一行） | 低（与 effect 文档化本意一致；现有测试因首挂载仍播种而全绿） | 小 | 是——无论父渲染频率/引用稳定性如何都不再抖动 |
| **B** | 保持「依赖变化即重置」语义，改为**稳定化**上游引用：`AiTradeToggle` 用 `useMemo` 包裹 `defaultStrategyIds`、确保 `autoTradeMembers`/`seededMembers` 稳定，依赖数组据实补全。 | 中（改父组件 + 弹窗） | 中（脆弱：日后任一默认值 prop 再次非稳定即复发；本轮 `cc3114b`/`0a61ce0` 正是这种「新增字段/来源即复发」的活例） | 中 | 否——只压住当前引用，未纠正「用户编辑期间不应被 seed 覆盖」的设计 |
| **C** | 降低父树重渲染频率/隔离：对 `AiTradeToggle` 或 `AgentPanel` 施加 `React.memo`，或把行情流 state 移出 `page` 渲染路径。 | 高（触及共享组件/页面架构） | 高（可能引入与本 bug 无关的行情/面板刷新回归） | 大 | 否——只移除当前鼓点，effect「任一合法默认变化即重置」的缺陷仍在 |

## 6. 推荐修复（Recommended fix）

**采用方案 A**：把「回填默认」严格限定在弹窗**打开的那一次转换**上（`open` false→true），而不是「依赖数组任一项变化就跑」。具体即把 `AiTradeLaunchModal.tsx:195` 的依赖数组

`[open, propBar, defaultAllocationMode, defaultInstId, seededMembers, defaultStrategyIds, defaultContextRounds, defaultStartTs, defaultEndTs, defaultDecisionEveryBars, liveAvailable]`

改为 `[open]`（保留 `// eslint-disable-line react-hooks/exhaustive-deps` 与体内 `if (!open) return;`）。

推荐理由：

1. **与 effect 本意完全吻合**——注释即写「弹窗每次*打开*：回落默认」（`:171`）。当前把它当成「同步外部默认」的 effect 使用才导致抖动；方案 A 让实现回到语义。
2. **命中唯一必要且充分的差异**——§3.4 的 git 证据表明，`73e9b3b` 引入的「把 `seededMembers`/`defaultStrategyIds` 写进依赖」正是本 bug 的必要且充分条件；收敛回 `[open]` 即精确复原「仅在弹窗打开的那次转换播种」的原意，不多做也不少做。
3. **对引用稳定性与重渲染频率彻底免疫**——`[open]` 依赖下，effect 只在 `open` 真正变化时运行；任何频率的父树重渲染、任何 prop 引用抖动、以及**日后再新增的字段/查询**都不会再触发重置（相较 B/C 更彻底，也不会像本轮那样被后续 feature 一再打破）。
4. **改动面最小、无外溢**——仅改弹窗内一行依赖数组；不动父组件 `AiTradeToggle`、不动 `page.tsx` 行情流、不加 `React.memo`、不碰 `services/quant`。
5. **回归安全已核验**——被移除的「open 期间某默认 prop 变化即回填」语义要么由每帧直算的门禁替代（`liveAvailable` 在 `:366` 直接门禁「实盘」段控禁用，不经该 effect；对 CTA/`ready` 门禁边界的精确化见文末「评审结论」§R-2），要么本就是隐患（`defaultInstId` 等在遮罩层 open 期间用户几无从改动）。`AiTradeLaunchModal.test.tsx` 全部用例均以 `open` 初始为真挂载，`[open]` 依赖下 effect 在挂载时即运行一次并完成播种，故现有用例（含 `:332` 「重开即重置」用例，其显式切换 `open` 仍会触发）保持通过。

实现要点（供 Subtask #2 参考，非本节点交付）：

- **最小实现**：将 `:195` 依赖数组改为 `[open]`；保留 `// eslint-disable-line react-hooks/exhaustive-deps` 与体内 `if (!open) return;`。或等价地引入 `const prevOpenRef = useRef(false);`，effect 内 `if (open && !prevOpenRef.current) { …播种… }` 并在末尾 `prevOpenRef.current = open;`——二者取其一即可（`[open]` 变体更小，优先）。
- **必须补回归测试**（把本坑固化为护栏，也是本次能长期溜过 CI 的根因）：新增用例，模拟「弹窗 `open` 恒为真、rerender 传入**新的** `defaultStrategyIds` 引用（以及变化的 `seededMembers`）」后，断言已勾选的 `selectedIds` / 已选仓位档位 / 已编辑输入框**不被**重置。
- **不改动范围**：除该 effect 依赖数组与新增测试外，不触碰其它文件；`defaultStrategyIds`/`seededMembers` 上游计算保持原样（方案 A 不依赖其引用稳定性）。

---

### 附：关键证据索引

- 重置 effect 及非稳定依赖（含 `cc3114b` 新加的 `setPositionStep` 重置行）：`apps/web/components/Agent/AiTradeLaunchModal.tsx:147-150`、`:171-195`（依赖数组在 `:195`）
- 父组件内联现算的 `defaultStrategyIds`：`apps/web/components/Agent/AiTradeToggle.tsx:164-169`，下传 `:207`；`defaultMembers=autoTradeMembers` 下传 `:204`
- 行情流 1s/0.5s 节流 flush（重渲染鼓点）：`apps/web/app/page.tsx`（`streamMarket` effect 内 `createMarketThrottle`，flush 写 `setMarketTicker/setTradeTape/setLastPrice/setLastMarketTs`）
- 渲染级联路径（grep 确认沿途无 `memo(`）：`page.tsx` → `RightPanel.tsx` → `AgentPanel.tsx` → `AiTradeToggle.tsx:193`（弹窗无条件渲染）
- git 证据：依赖数组行自 `73e9b3b` 至今未改（`git log -L 195,195:.../AiTradeLaunchModal.tsx`）；第 1 轮无修复提交（task-board「无 git 提交记录」）
- 最近两次修改的叠加：`cc3114b`（仓位档位控件 `:643-662` + `positionStep` state `:140` + 重置行 `:183`）、`0a61ce0`（`settingsQ`/`configQ` `:157-168` + `previewBudget` `:295-302`）
- CI 漏检缺口：`AiTradeLaunchModal.test.tsx` 无「open 恒真 + 新 `defaultStrategyIds` 引用」用例；`:332` 用例仅验证「切换 open 后重置」

---

## 评审结论（Review Verdict）

- **评审节点**: Subtask #1（分析报告评审与修复）
- **评审方式**: 对照**当前源码**（非报告快照）与 `git` 历史逐条复核根因证据链、候选方案边界与回归安全性。
- **评审日期**: 2026-07-13

### R-1. 根因证据链：逐条复核，判定「成立且充分」

以下每一处均由评审者在当前工作区独立核对（行号以当前源码为准，与报告一致）：

| 报告论点 | 复核方式 | 结论 |
|---|---|---|
| 重置 effect 在 `:172-195`，依赖数组 `:195` 含 11 项（内含 `seededMembers`/`defaultStrategyIds`） | 直读 `AiTradeLaunchModal.tsx:172-195` | ✅ 逐字一致 |
| `:171` 注释「弹窗每次打开：回落默认…」印证 effect 本意是「打开即播种」，而非「同步外部默认」 | 直读 `:171` | ✅ 一致 |
| `setPositionStep`（`cc3114b` 新增重置行）在 `:183`；`positionStep` state 在 `:140` | 直读 `:140`、`:183` | ✅ 一致 |
| `seededMembers` 为 `useMemo`，deps `[defaultMembers, defaultInstId]`（`:147-150`） | 直读 `:147-150` | ✅ 一致 |
| `defaultStrategyIds` 在 `AiTradeToggle.tsx:164-169` 内联现算，`aiTradeConfig` 为空时返回**新数组字面量** `[strategyId]`/`[]`；`:207` 下传、`defaultMembers=autoTradeMembers` `:204` 下传 | 直读 `AiTradeToggle.tsx:164-169`、`:204`、`:207` | ✅ 一致；store 默认 `aiTradeConfig:null`，故首次场景**每帧新引用**成立 |
| 重渲染鼓点：`page.tsx` 行情节流 flush 写 `setMarketTicker/setTradeTape/setLastPrice`（`:402-408`） | 直读 `page.tsx:402-408` + `lib/marketThrottle.ts` | ✅ 一致；节流常量 `BASELINE_INTERVAL_MS=1000`/`HIGH_CHANGE_INTERVAL_MS=500`（`marketThrottle.ts:9-10`）正是「1s/0.5s」的确证 |
| 渲染级联无 `memo`：`page → RightPanel → AgentPanel → AiTradeToggle → AiTradeLaunchModal`，弹窗无条件渲染 | grep `memo(`（Agent 目录 0 命中）+ `RightPanel.tsx:4`「两面板始终挂载（用 hidden 切换）」+ `AgentPanel.tsx:331` 渲染 `<AiTradeToggle/>` | ✅ 级联链无记忆化边界成立，重渲染直达弹窗 |
| 「复发」定性：依赖数组行**自 `73e9b3b` 至今未改**、第 1 轮无修复提交 | `git log -L 195,195:apps/web/components/Agent/AiTradeLaunchModal.tsx` | ✅ 亲验：最后一次改动即 `73e9b3b`（把 7 项扩为 11 项、引入 `seededMembers`/`defaultStrategyIds`），`0a61ce0`/`cc3114b` 均未再触碰该行 |
| 排他性：全文件仅一个写 state 的 `useEffect`（`:172`），`settingsQ`/`configQ` 只读、仅喂 `previewBudget`（`:295-302`）；`statusQ.refetchInterval=15_000`（`AiTradeToggle.tsx:64`）与「约 1 秒」不符 | 直读 `:60-64`、`:295-302` | ✅ 一致，~1Hz 节拍唯一来源为行情 flush |

**替代解释已被报告正确排除**：（a）组件 remount——弹窗在 `AiTradeToggle.tsx:193` 无条件渲染，不卸载重挂，故非 remount 导致；（b）定时器/轮询——最近的 `refetchInterval` 为 15s，节拍对不上；（c）只读查询回写——`settingsQ`/`configQ` 从不回写输入 state。三条反证均成立。**症状（任意编辑约 1 秒后回退）由证据链（非稳定依赖 × 1Hz 重渲染鼓点 × 无 memo 级联）完整解释，无逻辑缺口。**

### R-2. 一处措辞精确化（对 §6 推荐理由第 5 点的更正）

报告 §6 第 5 点原称 `liveAvailable`「参与段控禁用与 **CTA 门禁**」。评审复核发现：

- `liveAvailable` **确**门禁「实盘」段控按钮：`disabled={!liveAvailable || submitting}`（`AiTradeLaunchModal.tsx:366`）。
- 但**提交 CTA/`ready` 并不直接复核 `liveAvailable`**：`const ready = mode === "live" ? baseValid && !membersError && acknowledged : …`（`:306-309`），提交处 `if (!ready || submitting) return;`（`:312`）后即 `onConfirm({ mode: "live", … })`（`:326`）。

**由此产生方案 A 唯一的行为差量**：若弹窗**打开期间** `mode` 已为 `"live"`、而 `liveGateReason` 事后翻为非空（其由 `resolveAiTradeGate` 依 `accountMode/hasReadyDefault/aiTradeEnabled/agentConfigured/liveAutoActive/running` 派生，`AiTradeToggle.tsx:71-78`；其中 `aiTradeEnabled` 走 15s 轮询、`running` 随会话变），则在 `[open]` 依赖下 effect 不再重跑，「实盘」段控置灰但 `mode` 仍停留 `"live"`，CTA 不会因 `liveAvailable` 转假而自动禁用。

**对该差量的评审判定（不阻断方案 A）**：

1. **节拍与 bug 无关**：该翻转最快 15s 级（`aiTradeEnabled` 轮询）或事件驱动，**绝非**触发本 bug 的 1Hz 节拍，与用户症状无涉。
2. **非本次引入、且有后端兜底**：当前（有 bug 的）代码之所以「看似防住」，只是坏 effect 顺带把 `mode` 冲回 `backtest` 的副作用——这本身就是缺陷行为，并非有意的门禁设计。实盘启动最终由后端 `aiTradeApi.start` 的实盘/互斥护栏兜底，前端该差量不构成资金安全回归。
3. **修复语义更正确**：seed effect 的职责是「打开时播种表单默认」，本不应承担「把 `mode` 与 `liveAvailable` 保持同步」的门禁职责；把二者解耦正是方案 A 的正确性来源。

**可选的防御性加固（供 Subtask #2 斟酌，非本 bug 必需、不属最小修复）**：可在 `ready` 的 live 分支追加 `&& liveAvailable`（`:306-309`，一处布尔与），使 CTA 在实盘门禁翻转时即时禁用，把上述差量彻底抹平。**但这属独立于本 bug 的加固，若采纳须单独列为附带改动，不得掺入「依赖收敛」这一最小修复本身**，以保持改动可审计、可回退。

### R-3. 候选方案审计

- **方案 A（依赖数组收敛为 `[open]`）— 采纳。**
  - *边界/回归*：①**首次挂载**——React 对首挂载无条件运行 effect，`[open]` 下打开态（`open===true`）仍会播种一次，既有以 `open` 初始为真挂载的用例（如 `:303` 断言 `positionStep`）保持通过；②**重开**——`open` false→true 转换命中 `[open]` 依赖，`:332`「重开即重置」用例仍触发、保持绿；③**引用/频率抖动**——`[open]` 下与父渲染频率、prop 引用稳定性、**日后新增字段**彻底解耦，无复发路径。
  - *测试可行性已核验*：测试脚手架 `element()` 默认 `open`（`AiTradeLaunchModal.test.tsx:33`）、且注释「每次调用都用全新的 `[]` 字面量播种 default 数组，方便回归测试模拟父树重渲染时传入的新引用」（`:29`）——**基建早已就绪，唯独缺了那条断言**，正是本 bug 长期溜过 CI 的直接原因。补测毫无阻力。
- **方案 B（稳定化上游引用）— 否决。** 复发史即其反证：`0a61ce0`/`cc3114b` 每新增一个默认 prop/来源即再度打破引用稳定，属「治标不治本、且随 feature 增长持续劣化」。
- **方案 C（memo/降频/架构隔离）— 否决。** 触及 `page`/共享面板，爆炸半径大，可能引入与本 bug 无关的行情刷新回归，且未纠正 effect「任一默认变化即重置」的设计缺陷。

### R-4. 最终推荐修复（单一）

**采用方案 A：将 `AiTradeLaunchModal.tsx:195` 的 11 项依赖数组收敛为 `[open]`，保留 `// eslint-disable-line react-hooks/exhaustive-deps` 与体内 `if (!open) return;`；语义即「仅在弹窗 `open` 由 false→true 的那一次播种默认」。**

**理由（充分且必要）**：`git log -L` 证实 `73e9b3b` 把 `seededMembers`/`defaultStrategyIds` 写进依赖数组是本缺陷的**必要且充分条件**；收敛回 `[open]` 精确复原 effect 注释所述本意（`:171`），对引用抖动与任意重渲染频率**彻底免疫**，改动面最小（弹窗内一行），不触碰 `AiTradeToggle`/`page.tsx`/`services/quant`，与 §6 论证一致且经本评审逐条复核无误。

**交付附带的强制项（Subtask #2 必做）**：新增回归测试——弹窗 `open` 恒为真，`rerender` 传入**新的** `defaultStrategyIds` 引用（并可叠加变化的 `seededMembers`），断言已勾选的 `selectedIds` / 已选仓位档位 `positionStep` / 已编辑输入框**不被重置**。此测试把本坑固化为护栏，直接封堵 R-1/§4 所述的 CI 缺口。

**明确排除项**：R-2 的 `ready += liveAvailable` 加固为**可选、独立**改动，非最小修复的一部分；如不采纳，不影响本 bug 修复的正确性与完整性。除依赖数组一行 + 新增测试外，不得改动其它文件。

**评审判定：报告根因成立、证据链在更正 R-2 措辞后已然airtight；推荐方案 A 为唯一最小根治方案，准予进入 Subtask #2 实施。**
