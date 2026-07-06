# AI 自动交易左侧面板「标的化 + 可拖拽调宽」开发方案

- **Feature**: `ai-trade-left-panel-symbols-and-resize`
- **文档版本**: v2（v1 由「方案设计」节点产出；本 v2 由「方案评审与修复」节点补入「评审记录」、就地修复 P0/P1、追加「评审结论」）
- **所属迭代**: 第 5 轮
- **上游依赖轮次**: 第 1~4 轮已落地 AI 自动交易（决策者 Agent + 回测模式 + 右侧「AI 助手」执行轨迹分段视图）

---

## 评审记录（Review Notes · v2）

> 评审人：Anthropic 工程团队 高级评审。评审方式：对 v1 方案逐节评审，并与真实代码交叉核验（`panelResize.ts` / `PanelResizeHandle.tsx` / `store.ts@v25` / `AiTradePanel.tsx` / `WatchlistTab.tsx` / `WatchOtherPanel.tsx` / `ChartWorkspace.tsx` / `types.ts::Instrument` / `aiTradeTypes.ts::AiTradeSessionRow` / `AiTradeToggle.tsx` 门禁 / `AiBacktestModal.tsx`）。四维：可行性 / 完备性 / 一致性 / 合理规模。

**核验通过项（确认方案与代码现状吻合，可放心实现）：**

- `panelResize.ts` 现有 `widthFromRightEdge / ratioFromRightEdge / ratioFromTopEdge`「只换算不夹取」范式属实；新增对偶 `widthFromLeftEdge` 契合既有约定。
- `PanelResizeHandle.tsx` 的 `onResize(clientX)/onReset/ariaLabel` 签名、Pointer 捕获 + 双击复位 + `pointercancel/lostpointercapture` 复位属实，可直接复用。
- `store.ts` 当前 `version: 25`，`setRightPanelWidth`（有限数守卫 + `Math.min/Math.max` 夹取）与 partialize、`migrate` 逐版 `if (version < N)` 累加范式属实；`25 → 26` 补默认迁移与之同构，正确。
- `ChartWorkspace.tsx:218` 侧栏定宽 `basis-[16%] min-w-[220px] max-w-[300px]` 属实；`topRef`（`:213`，`flex-row`、无内边距）的 `.left` 即侧栏左边缘，零新增 ref 可行。
- `<main>`（`page.tsx:616`）为 `grid h-full overflow-hidden`，K 线列 `min-w-0 flex-1`——widen 侧栏时横向溢出被裁切、不产生整页横向滚动，**AC-5 可行**。
- `Instrument`（`types.ts:68`）含 `instId/instType/base/quote/last?/changePct?`，§3.1 标的行富化字段可用。
- `AiTradeSessionRow`（`aiTradeTypes.ts:118`）含 `instId/bar/strategyNames/budgetUsd/leverage/positionSide/sessionPnlUsd/state/roundsCompleted/lastDecision`，会话头 + 标的行所需字段齐备。
- AI 回测为独立模态（`AiBacktestModal.tsx` + `aiBacktest/panels.tsx`），**不**经左侧 `AiTradePanel` 呈现；本轮瘦身不波及回测视图（见 P2-1 已就地声明作用域）。

**问题清单：**

| # | 维度 | 严重度 | 问题 | 处置 |
|---|---|---|---|---|
| P1-1 | 完备性 / 正确性 | **P1** | v1 §3.1.3「下半区自选列表复用 `WatchOtherPanel` …从行集合中剔除 `session.instId`」未指明**行集合**。现有 `WatchlistTab` 把 `WatchOtherPanel` 接的是 `otherRows`（= `rows` 剔除 `autoTradeMembers`）。AI 会话期 `AutoTradePanel` 被隐藏，若沿用 `otherRows`，那些曾拖入策略自动交易分组的自选标的（`autoTradeMembers` 是**持久化偏好**，AI 会话期仍可能非空）会从侧栏**彻底消失**——既违背「下面为自选标的」需求，又造成自选静默丢失。 | **已就地修复**：§3.1.3 / §4 / §5.3 / §7 明确下半区取**全部自选行** `rows` 剔除 `session.instId`（`aiOtherRows`），而非 `otherRows`；并新增 AC-7 校验分组成员在 AI 会话期仍可见。 |
| P2-1 | 完备性 | P2 | v1 未声明 AI **回测**会话下左侧面板的作用域，代码开发节点可能误改而影响回测。 | **已就地声明**：§3.1 与 §六 补注——左 `AiTradePanel` 仅绑定实盘 `aiTradeSession`；AI 回测走独立模态，**本轮不涉及**。 |
| P2-2 | 一致性 / 可达性 | P2 | `WatchOtherPanel` 的 `aria-label="其他自选"`（`:42`）与 header 文案（`:53`）为硬编码；新增可选 `title` 后二者未随之更新，AI 会话下读屏仍念「其他自选」而视觉显示「自选标的」，标签不一致。 | **已就地补充**：§4 / §5.3 要求 `title` 同时驱动可见标题与 `aria-label`（缺省回落「其他自选」，向后兼容）。 |
| P2-3 | 稳健性 | P2 | §3.1.3 隐藏 `AutoTradePanel` 依赖「AI 与策略分组互斥」。核验：AI 启动门禁确会因 `liveAutoActive` 禁用，分组启动按钮又在 `AutoTradePanel` 内（会话期隐藏），故互斥**实际成立**；但缺显式护栏。 | **已就地补充**：§3.1.3 与 §八 增加防御条款——隐藏条件建议为「会话活跃 **且** 无运行中 `liveAutoGroup`」，万一并发运行也不致丢失分组「停止」入口（正常路径为无操作）。 |
| P2-4 | 可测试性 | P2 | §7.1.3 的 `AiTradePanel` 测试中，组件内部订阅 `store.aiTradeSession`（非 props 注入），用例须**注入并在用例间复位** store（与既有 `AiTradeAgentTrace.test.tsx` 同法），v1 未点明。 | **已就地补充**：§7.1 注明测试需 seed `aiTradeSession` 且逐例复位 store。 |

> 结论摘要：无 P0；唯一 P1（P1-1 行集合来源）已就地修复；4 项 P2 均已就地处置。详见文末「评审结论」。

---

## 一、Overview（概述）

第 1~4 轮已经把「AI 自动交易」这条新交易形态做完：右侧「AI 助手」面板的 `AiTradeToggle` 开关启动会话，`AiTradeLaunchModal` 收集多策略、预算、杠杆、总止盈止损、单仓 OCO 与上下文刷新轮数；后端 `ai_auto/` 以 Agent LLM 为决策者，逐根确认 K 线产出决策并经 `LiveExecutor` 下真实单；第 3~4 轮进一步把 Agent 的**思考 / 分析 / 工具调用 / 执行结果**逐轮轨迹搬到右侧「AI 助手」面板的「执行轨迹」分段视图（`AiTradeAgentTrace` + `AiTradeTraceRound`，见 `apps/web/components/Agent/AgentPanel.tsx:354`）。

在此之前，左侧标的侧栏（`InstrumentSidebar` → 自选页签 `WatchlistTab`）顶部还挂着一块 `AiTradePanel`（`apps/web/components/Chart/AiTradePanel.tsx`），它重复呈现了会话概览、**策略信号板、本轮决策、决策日志、上下文摘要**等「Agent 具体执行情况」。既然这些内容已经在右侧面板完整、实时地呈现，左侧再放一份就成了信息冗余，并且挤占了本该留给标的列表的纵向空间，违背「一处信息、一处呈现」的人机交互原则。

本轮（第 5 轮）要做两件事：**(1)** 把左侧 `AiTradePanel` 从「执行情况面板」瘦身为「标的面板」——上半区展示**当前正在 AI 自动交易的标的**，下半区展示**自选标的**，删除信号板 / 决策 / 摘要等执行细节；**(2)** 让左侧标的侧栏支持**从右边框拖拽调整宽度**（双击复位），使用户可以按需给标的列表更多横向空间。两项改动都是纯前端（`apps/web/`）范畴，不触碰后端、不改变任何交易语义与数据契约，风险面收敛在 UI 布局与既有 store 持久化模式内。

---

## 二、背景与现状（关键代码路径）

### 2.1 左侧「AI 自动交易」当前形态

- `apps/web/components/Chart/InstrumentSidebar.tsx:231` —「自选」页签 `ready && tab==="watch"` 时渲染 `WatchlistTab`。
- `apps/web/components/Chart/WatchlistTab.tsx:246-290` — 自选页签自上而下渲染三块：
  1. `AiTradePanel`（`:249`）——仅当存在活跃 AI 会话时呈现（内部 `if (!session) return null`）。
  2. `AutoTradePanel`（`:250`）——**策略式**多标的自动交易分组（拖拽成员 / 分配 / 启停），与 AI 自动交易互斥。
  3. `WatchOtherPanel`（`:276`）——其他自选标的列表。
- `apps/web/components/Chart/AiTradePanel.tsx` — 当前渲染：会话头（标的/状态/PnL）+ 会话元信息（标的/持仓/预算/上下文）+ `SignalBoard`（策略信号板）+ `LastDecision`（本轮决策）+ `DecisionLog`（决策日志）+ 上下文摘要 `<details>`。**其中 SignalBoard / LastDecision / DecisionLog / 摘要即「Agent 执行情况」，本轮删除。**
- 会话只读视图字段见 `apps/web/lib/aiTradeTypes.ts:118` `AiTradeSessionRow`：单标的 `instId`、`bar`、`strategyNames`、`budgetUsd`、`leverage`、`positionSide`、`sessionPnlUsd`、`state`、`roundsCompleted` 等。
- 右侧执行轨迹已完整承接执行细节：`apps/web/components/Agent/AiTradeAgentTrace.tsx`（概览条 + 逐轮 thinking/analysis/tool_call/result 时间线），由 `AgentPanel` 分段视图挂载。

### 2.2 既有「右锚面板调宽」范式（本轮 (2) 直接复用）

- 纯几何换算：`apps/web/lib/panelResize.ts` — `widthFromRightEdge(containerRight, clientX)`、`ratioFromRightEdge`、`ratioFromTopEdge`（仅换算，不夹取；夹取交给 store setter，单一真相）。
- 可复用竖向拖拽手柄：`apps/web/components/ui/PanelResizeHandle.tsx`（Pointer 捕获 + 每帧回吐 `clientX` + 双击复位 + `pointercancel/lostpointercapture` 复位）。
- 像素宽 + 夹取 + 持久化范式（**右侧面板宽**，最佳参照物）：
  - 常量与夹取边界 `apps/web/lib/store.ts:91-93`（`DEFAULT_RIGHT_PANEL_WIDTH=380` / `[320,640]`）。
  - setter `setRightPanelWidth`（`store.ts:844-854`，`Number.isFinite` 守卫 + `Math.min/Math.max` 夹取）。
  - 装配 `apps/web/app/page.tsx:151-157`（以 `mainRef` 右边缘为锚换算）+ `page.tsx:617` grid `minmax(0,1fr) 6px ${rightPanelWidth}px` + `page.tsx:656` `PanelResizeHandle`。
  - 持久化 `store.ts:907`（partialize）+ 版本迁移 `store.ts:958-962`（version<12 补默认）。
- 左侧标的侧栏当前为**定宽**列：`apps/web/components/Chart/ChartWorkspace.tsx:218` `basis-[16%] min-w-[220px] max-w-[300px]`，不可调。测量容器 `topRef`（`ChartWorkspace.tsx:213`）的 `getBoundingClientRect().left` 即侧栏左边缘。

---

## 三、Technical design（技术设计）

### 3.1 需求 (1)：左侧 AI 自动交易面板「标的化」

**目标布局（存在活跃 AI 会话时，自选页签自上而下）：**

```
┌───────────────────────────────┐
│ AI 自动交易   [运行中]   +12.30 │  ← 瘦身后的会话头（标签 + 状态徽标 + 会话 PnL）
│ ─ 自动交易标的 ─               │
│  BTC-USDT-SWAP · 1H   [多] 68420│  ← 正在 AI 自动交易的标的行（可点击 → setInst 切图）
│  （预算 500 USDT · 3×，副标）    │
├───────────────────────────────┤
│ 自选标的                     N │  ← 下半区：自选标的列表
│  ETH-USDT-SWAP        3120  ★  │
│  SOL-USDT-SWAP         168  ★  │
│  …                            │
└───────────────────────────────┘
```

**设计要点：**

1. **`AiTradePanel` 重写为「标的面板」**（只显示标的，删除执行情况）。保留：
   - 会话头：`AI 自动交易` 文案 + `运行中 / 已停止` 徽标 + 会话累计 PnL（沿用现有 `Badge` + `fmtMoney` + long/short 着色）。
   - 「自动交易标的」小节：把 `session.instId · session.bar` 渲染为一行**可点击标的行**——点击调用 `setInst(instId)` 让主图跟随；行尾以徽标显示 `positionSide`（多/空/空仓，复用局部 `describePosition`）；能解析到行情时附最新价。副标以极简 caption 展示 `预算 · 杠杆`（属于「标的的交易情形」，非 Agent 执行/思考，予以保留但弱化）。
   - **删除**：`SignalBoard`、`LastDecision`、`ExecDot`、`DecisionLog`、上下文摘要 `<details>` 及其依赖的 `describeAiAction/describeConsensus/describeExecuted/describeStance/describeRegime`、`aiTradeDecisions/aiTradeSummary` 订阅、`Consensus/StrategySignalView/AiTradeDecision` 导入。
2. **标的行行情解析（稳健优先）**：`WatchlistTab` 已持有 `rows`（已 resolve 的自选行含行情）与 `livePrice/changePct`（仅当前 active 标的）。将它们透传给 `AiTradePanel`：
   - 若 `session.instId` 命中 `rows`，用该 `Instrument` 的 `base/quote/instType/last` 富化显示；
   - 若同时是 active 标的，用 `livePrice/changePct` 覆盖快照价（与 `InstrumentRow` 同源逻辑）；
   - 若 `session.instId` 不在自选（未收藏），**优雅降级**为 `instId · bar + 持仓 + PnL`，不伪造价格（遵守「取数失败不造数」硬约束）。
3. **`WatchlistTab` 条件编排**（`aiTradeSession` 活跃与否二选一，互斥，避免同屏两套自动交易语义打架）：
   - **AI 会话活跃** → 渲染 `AiTradePanel`（标的，置顶）+「自选标的」列表（下半区）。**隐藏**策略式 `AutoTradePanel`。
     > **[P2-3 稳健性护栏]** 隐藏条件取「`aiTradeSession` 活跃 **且** `liveAutoGroup == null`（无运行中分组）」。经核验，AI 启动门禁会因 `liveAutoActive`（`AiTradeToggle` `resolveAiTradeGate`）在分组运行时禁用、分组启动按钮又只存在于会话期被隐藏的 `AutoTradePanel` 内，故互斥**实际成立**、正常路径此护栏为无操作；但显式保留「分组真在运行则不隐藏」可避免任何并发极端下丢失分组「停止」入口。
   - **[P1-1 关键修复] 下半区行集合来源**：下半区自选列表复用 `WatchOtherPanel`，传入 `title="自选标的"`、`locked`（AI 会话期不提供拖入分组语义）、`emptyHint`（AI 语义文案）。**行集合必须取全部已 resolve 的自选行 `rows`（而非现有 `otherRows`）并剔除 `session.instId`**：
     - 即 `aiOtherRows = rows.filter((r) => r.instId !== session.instId)`。
     - **原因**：现有 `otherRows` 已剔除 `autoTradeMembers`；AI 会话期 `AutoTradePanel` 被隐藏，若沿用 `otherRows`，那些曾拖入策略自动交易分组的自选标的（`autoTradeMembers` 是**持久化偏好**、AI 会话期仍可能非空）将从侧栏彻底消失——违背「下面为自选标的」需求且造成自选静默丢失。取全量 `rows` 剔除会话标的可兼顾「不重复」与「不漏项」。
   - **无 AI 会话** → 维持现状：`AutoTradePanel` + `WatchOtherPanel`（接 `otherRows`），行为字节级不变。
4. **`AiTradePanel` 顶层自守**：内部保留 `if (!session) return null`；`WatchlistTab` 也以 `aiTradeSession` 分支，双保险。**注意**：这里的 `session` 特指实盘运行期态 `aiTradeSession`；**AI 回测**为独立模态（`AiBacktestModal.tsx` + `aiBacktest/panels.tsx`），不经左侧 `AiTradePanel` 呈现，**本轮瘦身不涉及回测视图**（P2-1）。

### 3.2 需求 (2)：左侧标的侧栏可拖拽调宽

复刻「右锚面板调宽」范式，但侧栏是**左锚**（占据 `[containerLeft, handleX]`），故新增一个左锚几何换算函数；夹取与持久化沿用既有 store 范式。

**操作序列（拖拽一帧）：**

```
用户按住侧栏右边框手柄拖动
  → PanelResizeHandle 每帧回吐 clientX
  → ChartWorkspace.onResizeSidebar(clientX):
       rect = topRef.getBoundingClientRect()
       setInstrumentSidebarWidth(widthFromLeftEdge(rect.left, clientX))
  → store setter: Number.isFinite 守卫 + 夹取 [MIN,MAX] → 写 instrumentSidebarWidth
  → ChartWorkspace 侧栏列 style={{ width: instrumentSidebarWidth }} 重渲染
双击手柄 → onReset → setInstrumentSidebarWidth(DEFAULT) 复位
刷新 → partialize 持久化 → 水合恢复
```

**设计要点：**

1. 新增几何换算 `widthFromLeftEdge(containerLeft, clientX) = clientX - containerLeft`（左锚：宽度随指针右移而增大），与 `widthFromRightEdge` 对偶，仅换算不夹取。
2. store 新增像素宽状态 `instrumentSidebarWidth`（默认 `240`，夹取 `[200, 460]`），setter `setInstrumentSidebarWidth` 完全镜像 `setRightPanelWidth`（有限数守卫 + 夹取）。进 partialize，版本 `25 → 26` 补默认迁移。
3. `ChartWorkspace` 侧栏列由定宽 class 改为像素宽 + 手柄：
   - 列样式：`shrink-0` + `style={{ width: instrumentSidebarWidth }}`，保留 `min-w-[200px]` 作为 CSS 兜底下限（与夹取下限一致，双保险防塌陷）。
   - 在侧栏列与 K 线列之间插入 `PanelResizeHandle`，`onResize` 用 `topRef` 左边缘换算、`onReset` 复位默认、`ariaLabel="拖动调整标的侧栏宽度，双击复位"`。
4. 测量容器复用既有 `topRef`（`ChartWorkspace.tsx:213`，其 `.left` 即侧栏左边缘），零新增 ref。

---

## 四、File / module change plan（文件 / 模块变更计划）

| 文件 | 变更类型 | 意图（一句话） |
|---|---|---|
| `apps/web/components/Chart/AiTradePanel.tsx` | 改写 | 从「执行情况面板」瘦身为「AI 自动交易标的面板」：会话头 + 标的行；删除信号板/决策/日志/摘要，接收 `rows/active/livePrice/changePct/onSelect` 以富化并可点击切图（内部仍订阅 `store.aiTradeSession`，`session==null` 返回 null）。 |
| `apps/web/components/Chart/WatchlistTab.tsx` | 修改 | 新增订阅 `aiTradeSession` 并分支编排：会话活跃→`AiTradePanel`(标的)+`WatchOtherPanel`(自选标的，行集合取**全量 `rows` 剔除 `session.instId`**——见 P1-1，非 `otherRows`；`locked`)、隐藏策略 `AutoTradePanel`（隐藏条件另需 `liveAutoGroup==null`，见 P2-3）；无会话→现状不变（`WatchOtherPanel` 仍接 `otherRows`）。透传 `rows/active/livePrice/changePct/onSelect` 给 `AiTradePanel`。 |
| `apps/web/components/Chart/WatchOtherPanel.tsx` | 小改（向后兼容） | 新增可选 `title?`(默认「其他自选」)与 `emptyHint?`(默认现文案)两个 props，供 AI 会话下复用为「自选标的」列表。`title` **须同时驱动可见 header 文案与 `aria-label`**（P2-2；缺省回落「其他自选」，字节级向后兼容）。 |
| `apps/web/lib/panelResize.ts` | 新增函数 | 增加左锚几何换算 `widthFromLeftEdge(containerLeft, clientX)`（对偶于 `widthFromRightEdge`）。 |
| `apps/web/lib/panelResize.test.ts` | 新增用例 | 覆盖 `widthFromLeftEdge` 的方向性与越界（指针越过左边缘返回非正值）。 |
| `apps/web/lib/store.ts` | 修改 | 新增 `instrumentSidebarWidth` 状态 + 常量 `DEFAULT_INSTRUMENT_SIDEBAR_WIDTH/MIN/MAX` + setter `setInstrumentSidebarWidth`(镜像 `setRightPanelWidth`) + partialize + 版本 26 迁移。 |
| `apps/web/components/Chart/ChartWorkspace.tsx` | 修改 | 侧栏列改像素宽 `style={{ width: instrumentSidebarWidth }}`+`shrink-0`(去 `basis-[16%]/max-w`)；在侧栏与 K 线列间插入 `PanelResizeHandle`，用 `topRef` 左边缘 + `widthFromLeftEdge` 换算，双击复位默认。 |
| `apps/web/components/Chart/AiTradePanel.test.tsx` | 新增（可选，推荐） | 断言瘦身后不再渲染决策日志/信号板，仅渲染会话头 + 标的行、点击标的触发 `onSelect`；`session=null` 时不渲染。 |

> 未列出的文件一律不改；后端、契约、交易语义零改动。

---

## 五、Interface design（接口 / 组件签名）

### 5.1 纯几何换算（`panelResize.ts`）

```ts
/**
 * 左锚面板宽度：面板占据 [containerLeft, clientX]，其宽度 = 指针 X − 左边缘。
 * @param containerLeft 容器左边缘视口 X（getBoundingClientRect().left）
 * @param clientX 指针视口 X（PointerEvent.clientX）
 * @returns 面板像素宽（可能非正/越界，由 store setter 夹取）
 */
export function widthFromLeftEdge(containerLeft: number, clientX: number): number {
  return clientX - containerLeft;
}
```

### 5.2 store 契约（`store.ts`）

```ts
export const DEFAULT_INSTRUMENT_SIDEBAR_WIDTH = 240;
const MIN_INSTRUMENT_SIDEBAR_WIDTH = 200;
const MAX_INSTRUMENT_SIDEBAR_WIDTH = 460;

// state
instrumentSidebarWidth: number;                 // 初值 DEFAULT_INSTRUMENT_SIDEBAR_WIDTH
// action
setInstrumentSidebarWidth: (n: number) => void; // 有限数守卫 + 夹取 [MIN,MAX]（镜像 setRightPanelWidth）
```

- partialize 追加：`instrumentSidebarWidth: s.instrumentSidebarWidth`。
- 版本迁移：`if (version < 26) next = { ...next, instrumentSidebarWidth: DEFAULT_INSTRUMENT_SIDEBAR_WIDTH };`，并把 `version: 25` 改为 `version: 26`。

### 5.3 组件 props

```ts
// AiTradePanel（改写后）
interface AiTradePanelProps {
  rows: Instrument[];                 // 已 resolve 的自选行（用于富化会话标的行行情）
  active: string;                     // 当前选中 instId（判断是否用 livePrice 覆盖）
  livePrice?: number | null;
  changePct?: number | null;
  onSelect: (instId: string) => void; // 点击会话标的 → setInst 切图
}
// 内部仍订阅 store.aiTradeSession；session 为空返回 null。

// WatchOtherPanel（新增两个可选、向后兼容的 props）
interface WatchOtherPanelProps {
  /* …既有 props 不变… */
  title?: string;      // 默认「其他自选」；AI 会话下传「自选标的」。同时用作可见 header 文案与 aria-label（P2-2）
  emptyHint?: string;  // 默认现有 locked/unlocked 文案；AI 会话下传专用空态文案
}
```

**WatchlistTab 分支编排（关键片段，落实 P1-1 行集合来源）：**

```tsx
const aiTradeSession = useAppStore((s) => s.aiTradeSession);

// [P1-1] AI 会话期下半区取全量自选行 rows，仅剔除会话标的；不可用 otherRows（其已剔除 autoTradeMembers）。
const aiOtherRows = useMemo(
  () => rows.filter((r) => r.instId !== aiTradeSession?.instId),
  [rows, aiTradeSession?.instId],
);

// [P2-3] 仅当无运行中分组时才隐藏策略式 AutoTradePanel（正常路径 liveAutoGroup 恒为 null）。
const showStrategyAutoPanel = !aiTradeSession || !!liveAutoGroup;

// 渲染：
//   {aiTradeSession && <AiTradePanel rows={rows} active={active} livePrice={livePrice}
//                        changePct={changePct} onSelect={onSelect} />}
//   {showStrategyAutoPanel && <AutoTradePanel … />}
//   <WatchOtherPanel
//     rows={aiTradeSession ? aiOtherRows : otherRows}
//     locked={aiTradeSession ? true : running}
//     title={aiTradeSession ? "自选标的" : undefined}
//     emptyHint={aiTradeSession ? "收藏更多标的以在此展示（AI 自动交易进行中）" : undefined}
//     … />
```

### 5.4 ChartWorkspace 装配（片段）

```tsx
const instrumentSidebarWidth = useAppStore((s) => s.instrumentSidebarWidth);
const setInstrumentSidebarWidth = useAppStore((s) => s.setInstrumentSidebarWidth);

const onResizeSidebar = (clientX: number) => {
  const rect = topRef.current?.getBoundingClientRect();
  if (!rect) return;
  setInstrumentSidebarWidth(widthFromLeftEdge(rect.left, clientX));
};

// 侧栏列：shrink-0 + style={{ width: instrumentSidebarWidth }}（保留 min-w-[200px] 兜底）
// 紧随其后插入：
<PanelResizeHandle
  onResize={onResizeSidebar}
  onReset={() => setInstrumentSidebarWidth(DEFAULT_INSTRUMENT_SIDEBAR_WIDTH)}
  ariaLabel="拖动调整标的侧栏宽度，双击复位"
/>
```

> 无任何 REST / WebSocket / CLI 变更。

---

## 六、Data model（数据模型 / 内存态）

- **新增持久化 UI 偏好**：`aragon-ui`（zustand persist）新增 `instrumentSidebarWidth: number`（像素，夹取 `[200,460]`，默认 `240`）。属**偏好态**，与 `rightPanelWidth/chartSplitRatio/depthPanelRatio` 同类，进 partialize、随版本迁移补默认。
- **无新增运行期句柄**；AI 会话运行期态（`aiTradeSession/aiTradeDecisions/aiTradeTraces/aiTradeSummary`）**只减少订阅、不改结构**。
- **无 DB schema 变更**、无后端 in-memory 结构变更、无 wire 契约变更。

---

## 七、Testing & acceptance criteria（测试与验收）

### 7.1 单元测试（vitest；组件测试需 `NODE_ENV=test`）

1. `panelResize.test.ts`：
   - `widthFromLeftEdge_adds_pointer_from_left`：`widthFromLeftEdge(200, 440)===240`；`widthFromLeftEdge(200, 620)===420`（宽度随指针右移增大）。
   - `widthFromLeftEdge_returns_non_positive_when_pointer_past_left_edge`：`widthFromLeftEdge(200,200)===0`；`widthFromLeftEdge(200,120)===-80`（越界非正，交 store 夹取）。
2. store setter：`setInstrumentSidebarWidth` 夹取——传 `100→200`（下限）、`999→460`（上限）、`NaN/Infinity→` 保持原值（有限数守卫）。
3. `AiTradePanel.test.tsx`（推荐）：
   - **测试前置（P2-4）**：`AiTradePanel` 内部订阅 `store.aiTradeSession`（非 props 注入），故用例须通过 `useAppStore.setState({ aiTradeSession: … })` 注入会话，并在每个用例间 `afterEach` 复位（清 `aiTradeSession`），与既有 `AiTradeAgentTrace.test.tsx` 同法，避免用例间状态串味。
   - `session=null` → 不渲染任何内容。
   - 有会话 → 渲染会话头（含 `运行中`/PnL）与标的行（含 `instId·bar`）；**不含**「决策日志」「策略信号板」「上下文摘要」等文案。
   - 点击标的行 → 调用 `onSelect(session.instId)`。

### 7.2 交互 / 集成验收（人工，`pnpm --filter web dev`）

- **AC-1**：未开启 AI 自动交易时，自选页签行为与本轮前完全一致（策略自动交易分组、拖拽、启停照旧）。
- **AC-2**：开启 AI 自动交易后，左侧自选页签上半区显示「自动交易标的」（会话标的行，可点击切主图），下半区显示「自选标的」列表，且会话标的不在下半区重复出现。
- **AC-3**：左侧不再出现策略信号板、本轮决策、决策日志、上下文摘要等 Agent 执行情况；这些内容仍在右侧「AI 助手 → 执行轨迹」实时可见（信息不丢失）。
- **AC-4**：拖拽侧栏右边框可平滑调宽，夹取在 `[200,460]`px；双击手柄复位到 `240`px；刷新后宽度保持（持久化）。
- **AC-5**：窄窗口（如 860px 宽）拉宽侧栏不撑破布局——主图列 `min-w-0` 收缩、`<main>` `overflow-hidden` 优雅裁切，不出现整页横向滚动。
- **AC-6**：会话由「运行中」转「已停止」再到清空后，左侧优雅回落到无 AI 会话形态（策略分组 + 其他自选）。
- **AC-7（P1-1 回归门禁）**：会话前先把若干自选标的拖入策略自动交易分组（`autoTradeMembers` 非空），再开启 AI 自动交易——**这些分组成员仍须出现在下半区「自选标的」列表中**（仅会话标的本身除外），不得因隐藏 `AutoTradePanel` 而从侧栏消失。

### 7.3 回归门禁

- `pnpm --filter web test`（含 `panelResize`、store、`AiTradePanel` 新增用例）全绿。
- `pnpm --filter web build` / `tsc` 通过；注意 `WeightBatchArena.test` 的既有 tsc 报错为历史遗留、非本轮回归（见记忆），不得因此误判。
- 既有 `AiTradeAgentTrace.test.tsx`、`AiTradeToggle.test.tsx`、`AiTradeLaunchModal.test.tsx` 不受影响、保持通过。

---

## 八、Risks & mitigations（风险与缓解）

| 风险 | 影响 | 缓解 |
|---|---|---|
| 会话标的未在自选列表 → 无法解析行情 | 标的行缺价 | 优雅降级为 `instId·bar + 持仓 + PnL`，绝不伪造价格；命中 `rows` 时才富化（遵守「不造数」硬约束）。 |
| 隐藏策略 `AutoTradePanel` 影响策略式分组用户 | 会话期看不到分组管理 / 极端并发下丢失「停止」入口 | 仅在 AI 会话**活跃且无运行中分组**（`liveAutoGroup==null`）时隐藏（P2-3）；互斥经门禁核验实际成立，此护栏正常路径为无操作，但保证任何情形下分组「停止」入口不丢失；会话结束即恢复，语义无损。 |
| AI 会话期下半区误用 `otherRows`（已剔除分组成员） | 自选标的静默丢失 | **[P1-1]** 下半区取全量 `rows` 剔除 `session.instId`（`aiOtherRows`），分组成员在会话期照常可见；AC-7 回归门禁校验。 |
| 代码开发误改波及 AI 回测视图 | 回测执行/指标视图回归 | **[P2-1]** 左 `AiTradePanel` 仅绑定实盘 `aiTradeSession`；AI 回测走独立模态（`AiBacktestModal.tsx`/`aiBacktest/panels.tsx`），本轮零改动，变更清单亦未列入。 |
| 侧栏定宽 → 窄窗口挤压主图 | 布局溢出 | 夹取上限 `460`、下限 `200`；主图列 `min-w-0`、`<main>` `overflow-hidden`；侧栏 `min-w-[200px]` CSS 兜底——与既有 `rightPanelWidth` 同套包络策略。 |
| 持久化版本迁移遗漏默认 | 老用户水合缺字段 | 版本 `25→26` 显式补默认；zustand 浅合并本就回落初值，迁移为显式稳妥（与既有 12/22 版迁移同范式）。 |
| 左锚换算方向写反（用成 `widthFromRightEdge`） | 拖拽方向相反 | 新增专用 `widthFromLeftEdge` + 方向性单测；store 夹取即便符号错也守住边界不崩。 |
| 删除执行细节被误解为「信息丢失」 | 用户找不到决策日志 | AC-3 明确校验右侧「执行轨迹」承接同款信息；本轮为「迁移去重」而非「删除功能」。 |
| 拖拽手柄与 HTML5 DnD（自选拖拽编排）事件冲突 | 拖拽错乱 | 手柄用 Pointer 事件 + `setPointerCapture` + `touchAction:none`，与自选行的 `dragstart/drop`（HTML5 DnD）不同事件族，互不干扰（既有右侧/盘口手柄已验证共存）。 |

---

## 九、实施顺序建议（供「代码开发」节点）

1. `panelResize.ts` 新增 `widthFromLeftEdge` + 单测（最底层、零依赖，先行）。
2. `store.ts` 新增常量 / 状态 / setter / partialize / version 26 迁移。
3. `ChartWorkspace.tsx` 侧栏改像素宽 + 插入 `PanelResizeHandle`（打通需求 2，可先自测拖拽）。
4. `WatchOtherPanel.tsx` 加可选 `title/emptyHint`（向后兼容，先铺垫）。
5. `AiTradePanel.tsx` 改写为标的面板（删执行细节 + 标的行 + 富化 props）。
6. `WatchlistTab.tsx` 按会话分支编排、透传 props、剔除重复标的（收口需求 1）。
7. 补 `AiTradePanel.test.tsx`（可选推荐）+ 跑 `pnpm --filter web test` / `build` 全绿。

---

## 十、实施过程发现的方案缺陷

（留空，供下游「代码开发」节点在实现中回填。）

---

## 评审结论（Review Verdict · v2）

**结论：有条件通过（Approved with conditions）。**

方案整体**技术可行、规模适度、与既有约定一致**：需求 (2) 严格复刻已被验证的「右锚面板调宽」范式（`panelResize` 只换算 + store 单一真相夹取 + `PanelResizeHandle` + partialize/version 迁移），需求 (1) 是一次「迁移去重」而非删功能（执行细节已由右侧「AI 助手」执行轨迹承接）。经与真实代码逐一交叉核验，v1 引用的文件、行号、类型字段、门禁语义与布局包络均属实。**无 P0**；唯一 **P1（P1-1：下半区行集合来源）已在 §3.1.3 / §4 / §5.3 / §7 就地修复**，配套新增 AC-7 回归门禁；4 项 P2 亦已就地处置。

**放行条件（下游「代码开发」节点须逐条落实，均已写入正文）：**

1. **[P1-1]** AI 会话期下半区 `WatchOtherPanel` 的行集合取**全量 `rows` 剔除 `session.instId`**（`aiOtherRows`），**严禁**沿用 `otherRows`（其已剔除 `autoTradeMembers`，会导致自选静默丢失）。以 AC-7 验收。
2. **[P2-1]** 严守作用域：只改左侧实盘 `AiTradePanel` 一路；**不得**触碰 AI 回测模态（`AiBacktestModal.tsx` / `aiBacktest/panels.tsx`）与后端 / 契约 / 交易语义。
3. **[P2-2]** `WatchOtherPanel` 新增的 `title` 须同时驱动可见 header 文案与 `aria-label`，缺省回落「其他自选」保持字节级向后兼容。
4. **[P2-3]** 隐藏策略 `AutoTradePanel` 的条件为「`aiTradeSession` 活跃 **且** `liveAutoGroup==null`」，保留并发极端下的分组「停止」入口。
5. **[P2-4]** `AiTradePanel` 组件测试须注入并在用例间复位 `store.aiTradeSession`（与 `AiTradeAgentTrace.test.tsx` 同法）。
6. **[回归门禁]** `store.ts` 版本 `25 → 26` 且 `instrumentSidebarWidth` 进 partialize；`pnpm --filter web test` / `build` 全绿（`WeightBatchArena.test` 既有 tsc 报错为历史遗留，非本轮回归，不得据此误判）。

满足以上条件即视为达成本方案的验收标准；无未解决的 P0 / P1。
