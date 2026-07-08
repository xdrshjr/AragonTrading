# AI 自动交易「执行轨迹面板」精修方案（第 3 轮）

- **Feature slug**: `ai-trade-trace-panel-refinement`
- **版本**: v2（Subtask #1 评审与修复：见「评审记录」「评审结论」；所有 P0/P1 已就地修复）
- **范围**: 纯前端 UI/交互精修，**零后端、零 wire、零数据模型变更**
- **承接**: 第 1 轮「思维链独立研判」→ 第 2 轮「LLM 决策失败先重试三次再降级」→ 本轮精修右侧「AI 助手 · 执行轨迹」分段视图的呈现与滚动交互

---

## 评审记录（Review Notes · Subtask #1）

> 评审人：Anthropic 资深工程评审。维度：**可行性 / 完整性 / 一致性 / 规模适配**。已逐节比对 spec 与真实代码库（`AiTradeAgentTrace.tsx`、`AiTradeTraceRound.tsx`、`AgentPanel.tsx`、`lib/aiTrade.ts`、`lib/aiTradeTypes.ts` 及既有测试 `AiTradeAgentTrace.test.tsx`）核验每一条技术断言。**所有 P0/P1 已在正文就地修复并升版至 v2**。

| 编号 | 维度 | 严重度 | 问题 | 处置 |
|---|---|---|---|---|
| P0-1 | 完整性 | **P0** | spec 仅盘点了 **3 处**「第 X 轮」显示，**遗漏第 4 处**：`AgentPanel.tsx:346-356` 的「执行轨迹」分段 tab 上有 `第 {aiTradeMaxRounds} 轮` 徽标（`aiTradeMaxRounds` 由各成员 `roundsCompleted` 取最大值派生，第 311-314 行；且仅此一处使用）。该徽标正是「AI 交易面板上的具体轮数显示」，是本轮 R1 的删除对象；但 §四文件清单只列 5 个文件、不含 `AgentPanel.tsx` —— 照此实现则 tab 上的「第 N 轮」会**残留**，直接导致验收 §7.3.1「面板任意位置无第 X 轮」不达标。 | 已修复：§3.1 更正为「四处」；新增 §3.2.5 删除该徽标及仅供其用的 `aiTradeMaxRounds` 归约；§四增列 `AgentPanel.tsx`（文件数 5→6）；§七增该 tab 断言；§十精确 git add 增该路径。 |
| P1-1 | 一致性 | P1 | §3.2.1 `describeTrigger` 内新增局部 `TRIGGER_ACTION_LABELS`（open_long→开多…），与 `aiTrade.ts` 中**既有并已导出**的 `describeAiAction`（同映射、且已覆盖 hold/未知回落）重复。既违反 CLAUDE.md §五「不制造孤岛」/§八复用检查，也与本 spec §十「触发原因优先复用 `describeAiAction`」自相矛盾。 | 已修复：§3.2.1 改为直接调用 `describeAiAction(s.latestSignal)`，删除 `TRIGGER_ACTION_LABELS`。 |
| P1-2 | 可行性 | P1 | `describeTrigger` 落在 `aiTrade.ts`、形参 `Pick<AiTradeRoundTrace, "signalBoard">`，但 `aiTrade.ts` 现有 `import type { … } from "./aiTradeTypes"` **未含 `AiTradeRoundTrace`**；不补该 import，`tsc --noEmit` 会报「找不到名称 AiTradeRoundTrace」。且原文「describeTrigger 需从 aiTrade.ts 导出并加入 aiTradeTypes」措辞含混（函数不应加入类型文件）。 | 已修复：§3.2.1 明确「在 `aiTrade.ts` 既有 `aiTradeTypes` import 块补入类型 `AiTradeRoundTrace`」并厘清导出关系（无循环依赖：`aiTradeTypes` 仅依赖 `./types`）。 |
| P1-3 | 规模适配 | P1 | §九留 3 项「待评审决策」悬空，而拍板正是本节点职责；不决会让 Subtask #2 自行揣测，破坏可执行性。 | 已拍板并落正文：① 轮头 `lastPx` **保留**为 muted 次要（决策时市价，属市场上下文非轮数序号）；② 概览「距下次摘要」倒计时 **保留但改写**为非序号措辞「距上下文刷新 N 根K线」，`N≤0` 时隐藏；③ 触发短语措辞**采纳**（配合 P1-1 复用 `describeAiAction`）。§九相应改为「已决」。 |
| P2-1 | 可行性 | P2 | `jumpToLatest` 用 `behavior:"smooth"`；平滑动画中途的 `scroll` 事件（`nearBottom=false`）会瞬时 `setPinned(false)+armIdleTimer()`，虽落底后自愈，但削弱了 §3.3.2「程序化滚动恒落底、无需守卫」论证的严谨性。 | 已加固（低成本）：§3.3.1 将 `jumpToLatest` 改用瞬时滚动，使「程序化滚动恒落底、单次事件」不变量对**所有**程序化路径成立。 |
| P2-2 | 完整性 | P2 | §7.3.1「面板任意位置均无第 X 轮」与 §3.2.3 已接受「摘要正文若含模型自述『第 N 轮』属数据非 UI」相冲突。 | 已澄清：§7.3.1 收窄为「UI chrome（非模型生成正文）任意位置」。 |
| P2-3 | 完整性 | P2 | §7.2 新增 R1 用例需在 `signalBoard` 放**完整** `StrategySignalView`（6 字段），否则测试 tsc 报缺字段。 | 已补：§7.2 标注完整对象形状。 |
| P2-4 | 可行性 | P2 | 行号轻微漂移：`SummaryNode` 实际在 `AiTradeTraceRound.tsx:235`（spec 记 236）。不影响实现。 | 备忘，无需改。 |

> **结论摘要**：两项需求（R1 去轮数 / R2 智能滚动）方向正确、技术选型可行、规模适配得当。核心风险是 P0-1 的**遗漏站点**——不补 `AgentPanel.tsx` 则主需求不达标。全部 P0/P1 已在下方正文修复。评审结论见文末「评审结论」。

---

## 一、概述（Overview）

开启「AI 自动交易」后，右侧「AI 助手」面板会切到「执行轨迹」分段视图（`AiTradeAgentTrace`），把 Agent 每根已确认 K 线触发的一轮「研判 → 决策 → 执行」以卡片时间线的形式实时呈现。当前每张卡片的轮头以「**第 N 轮 · 时间 · 最新价**」开头，会话概览条也显示「**第 {roundsCompleted} 轮 · 距下次摘要 {roundsToSummary} 轮**」，上下文刷新分隔节点显示「**上下文已刷新（第 N 轮摘要）**」。用户反馈：这些「第 X 轮」的具体轮数对使用者没有价值，属于噪音；一条执行记录真正需要传达的是「**它发生在什么时间**」以及「**是什么触发了这一轮决策**」。

本轮第一项需求即：**去掉面板上一切「第 X 轮」这类轮数序号显示，改为只呈现「执行时间 + 触发原因」**，并要求该呈现「美观优雅、顶级设计、符合人机交互最佳实践」。触发原因并非新增的后端字段——本项目在第 1 轮已确立「交易策略给出的信号只是参考与**触发机制**，不是确定性指令」，而每一轮轨迹的 `round_start` 事件里已经携带了逐策略的信号板 `signalBoard`（含本根边沿信号 `latestSignal`）。因此触发原因可以完全在前端由既有数据派生出人类可读的短语，无需触碰后端 `trace.py`/`session.py`/`schemas.py`，把改动面和回归风险压到最低。

第二项需求是滚动体验：现状是经典「贴底跟随 + 尊重用户上滚」（`pinned` 距底阈值 48px），用户一旦上滚查看历史即停止自动跟随，需手动点「↓ 回到最新」才恢复。用户希望更贴心：**新记录到达时自动滚动到最新；用户手动上滚后暂停自动滚动；但用户停止操作超过 2 分钟后，自动恢复「跟随最新」**。这是一个「跟随 → 用户接管 → 空闲超时后自动交还」的状态机，落点仅在 `AiTradeAgentTrace` 的滚动容器上，通过一个可重置的空闲计时器实现。两项需求都属展示/交互层，互不耦合，可在同一批前端文件内一次完成。

---

## 二、需求拆解与设计目标

| # | 需求 | 设计目标 | 衡量口径 |
|---|---|---|---|
| R1 | 面板不显示「第 X 轮」轮数 | 轮头/概览/摘要节点均去掉轮数序号；轮头改为「时间 + 触发原因」 | 面板任何位置不出现「第 N 轮」文案；轮头首要信息为时间与触发原因 |
| R2 | 智能自动滚动 | 新记录自动贴底；用户上滚暂停；空闲满 2 分钟自动恢复贴底 | 上滚后出现「回到最新」；静置 120s 后自动滚到底并隐藏该按钮；期间任一次滚动都重置 120s 倒计时 |

**共同约束**：不回滚既有功能；不引入超出方案的重构；沿用既有设计语言（`describeConsensus` 的 `3多/1空` 计数风格、`--long/--short/--accent/--muted` 主题变量、`tabular` 数字、`ease-smooth` 过渡）。

---

## 三、技术设计

### 3.1 现状与改动定位（关键代码路径）

- 触发链路（后端，**不改**）：`ai_auto/session.py::feed_candle`（每根确认 K 线一轮）→ `_run_decision_round` → `_begin_round_trace` 推 `round_start`（携带 `signalBoard/marketContext/consensus/marketDigest`）→ 前端 `store.ts::applyAiTradeTracePhase` 归约进 `member.traces`。
- 呈现链路（前端，**本轮改**）：`AgentPanel.tsx:362` 挂载 `<AiTradeAgentTrace group={aiTradeGroup} activeInstId={instId} />` → 内部按聚焦标的取 `member.traces` → 逐条渲染 `<AiTradeTraceRound trace={t} />`。
- **四处**「第 X 轮」显示（P0-1 修订：原方案漏列第 4 处）：
  1. `AiTradeTraceRound.tsx::RoundHeader`（`第 {trace.round} 轮`，第 37 行）——**主目标**。
  2. `AiTradeTraceRound.tsx::SummaryNode`（`上下文已刷新（第 {round} 轮摘要）`，第 235 行）。
  3. `AiTradeAgentTrace.tsx::TraceOverview`（`第 {session.roundsCompleted} 轮 · 距下次摘要 {roundsToSummary} 轮`，第 50 行）。
  4. `AgentPanel.tsx`「执行轨迹」分段 tab 徽标（`第 {aiTradeMaxRounds} 轮`，第 353 行；`aiTradeMaxRounds` 由各成员 `roundsCompleted` 取最大值派生，第 311-314 行，且**仅此一处使用**）——**P0-1 遗漏站点，本轮一并删除**（详见 §3.2.5）。
- 滚动逻辑：`AiTradeAgentTrace.tsx` 第 125-176 行（`pinned` 状态、`signature` 变化后贴底的 `useEffect`、`onScroll` 距底判定、`jumpToLatest`、`!pinned` 时的「回到最新」按钮）。镜像自 `AgentMessageList.tsx`。

> `trace.round` 仍作为**内部数据**保留（`AiTradeAgentTrace` 的 React `key={`${t.round}-${t.ts}`}`、`store.ts` reducer 的按 `round` 去重定位、`signature` 拼接均依赖它）。本轮只删除其**显示**，不动数据。故数据模型、wire、reducer 全部零改动。

### 3.2 需求 R1：以「时间 + 触发原因」取代轮数

#### 3.2.1 新增纯函数 `describeTrigger`（`apps/web/lib/aiTrade.ts`）

从既有 `signalBoard` 派生一条人类可读的触发原因短语 + 着色 tone。语义锚点：`latestSignal !== "none"` 的策略即为本根「触发信号」的来源；无任何边沿信号时，本轮是「K 线收盘的常规复盘」。

> **P1-1 修订（复用 `describeAiAction`）**：动作中文化不再新建局部 `TRIGGER_ACTION_LABELS`，直接复用 `aiTrade.ts` 中既有并导出的 `describeAiAction`（`open_long→开多 / open_short→开空 / close→平仓 / hold→观望`，未知回落原值）。`describeAiAction` 与本函数同文件，无需 import。

```ts
export interface AiTradeTriggerView {
  label: string;    // 触发原因短语
  tone: AiTraceTone; // long | short | muted | accent（复用轮头着色体系）
}

// 触发方向 → 轮头着色 tone（open_long 绿 / open_short 红 / close 及未知边沿 accent）。
function toneForTriggerAction(action: string): AiTraceTone {
  if (action === "open_long") return "long";
  if (action === "open_short") return "short";
  return "accent"; // close 或未知边沿
}

/**
 * 触发原因派生（第 3 轮 R1）：由本轮信号板边沿信号（latestSignal）归纳「是什么触发了这一轮」。
 * 无边沿 → 常规复盘（muted）；单策略触发 → 「<动作>信号 · <策略名>」；多策略同向 → 「N 策略<动作>信号」；
 * 多策略分歧 → 「信号分歧 · a开多/b开空」（沿用 describeConsensus 计数风格）。着色取多/空/其它。
 * 动作中文化复用既有 describeAiAction（P1-1），不再自建标签表。
 */
export function describeTrigger(
  trace: Pick<AiTradeRoundTrace, "signalBoard">,
): AiTradeTriggerView {
  const fired = (trace.signalBoard ?? []).filter((s) => s.latestSignal !== "none");
  if (fired.length === 0) {
    return { label: "K 线收盘 · 常规复盘", tone: "muted" };
  }
  if (fired.length === 1) {
    const s = fired[0];
    return { label: `${describeAiAction(s.latestSignal)}信号 · ${s.name}`, tone: toneForTriggerAction(s.latestSignal) };
  }
  const counts = new Map<string, number>();
  for (const s of fired) counts.set(s.latestSignal, (counts.get(s.latestSignal) ?? 0) + 1);
  if (counts.size === 1) {
    const action = [...counts.keys()][0];
    return { label: `${fired.length} 策略${describeAiAction(action)}信号`, tone: toneForTriggerAction(action) };
  }
  const tally = ["open_long", "open_short", "close"]
    .filter((a) => counts.get(a))
    .map((a) => `${counts.get(a)}${describeAiAction(a)}`)
    .join("/");
  return { label: `信号分歧 · ${tally}`, tone: "accent" };
}
```

**导入与导出（P1-2 修订）**：`describeTrigger`、`AiTradeTriggerView`、`toneForTriggerAction` 均定义在 `apps/web/lib/aiTrade.ts`；`describeTrigger` 与 `AiTradeTriggerView` 需 `export`（供 `AiTradeTraceRound.tsx` 与 `aiTrade.test.ts` 引用），`toneForTriggerAction` 为文件内私有。函数形参用到 `AiTradeRoundTrace`，须在 `aiTrade.ts` 顶部**既有** `import type { … } from "./aiTradeTypes"` 块中**补入 `AiTradeRoundTrace`**（当前该块含 `AiTradeAction/AiTradeDecision/…` 但缺此类型，不补则 `tsc` 报「找不到名称」）；`AiTraceTone` 已在本文件定义。`aiTradeTypes` 仅依赖 `./types`，此 import 不产生循环依赖。

#### 3.2.2 重构 `RoundHeader`（`AiTradeTraceRound.tsx`）

去掉 `第 {trace.round} 轮`，改为「时间（主）· 触发原因（着色）· 最新价（次要，muted）」。右侧状态 chip 逐字保留。

```tsx
function RoundHeader({ trace }: { trace: AiTradeRoundTrace }) {
  const chip = describeTraceState(trace);
  const trigger = describeTrigger(trace);
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="flex min-w-0 items-center gap-1.5 text-[11px] text-[var(--muted)]">
        <span className="tabular shrink-0 font-medium text-[var(--text)]">{fmtTime(trace.ts, false)}</span>
        <span className="shrink-0 truncate" style={{ color: TONE_VAR[trigger.tone] }}>
          {trigger.label}
        </span>
        {trace.lastPx != null && <span className="tabular shrink-0">· {fmtPrice(trace.lastPx)}</span>}
      </span>
      {/* 右侧状态 chip：逐字保留原实现（deciding 附脉冲点） */}
      <span
        className="inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium"
        style={{ color: TONE_VAR[chip.tone], backgroundColor: toneSoft(chip.tone) }}
      >
        {chip.tone === "accent" && (
          <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full" style={{ backgroundColor: TONE_VAR[chip.tone] }} />
        )}
        {chip.label}
      </span>
    </div>
  );
}
```

- 引入 `describeTrigger` 到本文件从 `@/lib/aiTrade` 的 import 块；`AiTraceTone`/`fmtTime`/`fmtPrice`/`TONE_VAR`/`toneSoft` 均已在文件内可用。
- 设计取舍：`lastPx` 是「决策时市价」，属市场上下文而非轮数序号，**保留**为 muted 次要信息，不违反「不显示轮数」诉求（§九决策 1 已确认保留）。
- 可读性：触发短语 `truncate` 防溢出；时间与价格 `tabular` 对齐；触发短语按方向着色（多头绿 / 空头红 / 其它 accent / 常规 muted），一眼可辨「这轮是被什么带起来的」。

#### 3.2.3 `SummaryNode` 去轮数（`AiTradeTraceRound.tsx`）

`summary` 文案改为不含轮数序号；卡片已在轮头呈现时间，摘要节点无需再报时。

```tsx
function SummaryNode({ summary }: { summary: string }) {
  return (
    <details className="rounded-[8px] border border-[var(--accent-soft)] bg-[var(--accent-soft)] px-2.5 py-1.5 text-[11px]">
      <summary className="cursor-pointer select-none font-medium text-[var(--accent)]">
        🔄 上下文已刷新
      </summary>
      <p className="mt-1 whitespace-pre-wrap leading-relaxed text-[var(--text)]">{summary}</p>
    </details>
  );
}
```

- 调用处（第 255 行）改为 `{trace.summary && <SummaryNode summary={trace.summary} />}`（去掉 `round={trace.round}`）。
- 摘要正文由 LLM 生成，可能自带「截至第 N 轮」等字样——那是**模型文本/数据**，不属 UI 轮数 chrome，不在本轮改造范围（保持诚实回放）。

#### 3.2.4 `TraceOverview` 去轮数（`AiTradeAgentTrace.tsx`）

删除 `第 {session.roundsCompleted} 轮` 序号。上下文刷新倒计时属「机制提示」（每 N 根 K 线刷新上下文保持纯净）而非轮数序号，**保留但改写**为非序号、自解释的措辞（P1-3 已决：保留倒计时，改写文案；`roundsToSummary≤0` 时隐藏，避免出现「0」或刷新瞬间的噪音）：

```tsx
// 原：<span>第 {session.roundsCompleted} 轮 · 距下次摘要 {roundsToSummary} 轮</span>
// 改：仅保留上下文刷新倒计时（自解释措辞，无「第 X 轮」序号；≤0 时不渲染）
{roundsToSummary > 0 ? (
  <span>距上下文刷新 {roundsToSummary} 根K线</span>
) : null}
```

- `roundsToSummary` 的既有计算（`max(0, contextRefreshRounds - roundsSinceSummary)`）保留；仅去掉 `roundsCompleted` 序号展示。
- 措辞理由：一轮 = 一根确认 K 线，「距上下文刷新 N 根K线」比「倒计时 N」自解释，且语义上是「机制倒计时」而非「第 N 轮」序号，不违反 R1 诉求（详见 §九决策 2）。`AiTradeSessionRow.roundsCompleted` 字段保留（后端/数据层不动，仅不再显示）。

#### 3.2.5 `AgentPanel` 「执行轨迹」tab 去轮数徽标（P0-1 遗漏站点，新增）

`AgentPanel.tsx` 的分段 tab（第 346-356 行）在「执行轨迹」文案旁挂了一枚 `第 {aiTradeMaxRounds} 轮` 徽标——这是 R1 要删除的「AI 交易面板上的具体轮数显示」，原方案漏列。删除该徽标；由于 `aiTradeMaxRounds`（第 311-314 行的 `reduce`）**仅供此徽标使用**，一并删除以免留下未使用变量（触发 lint/`noUnusedLocals`）。tab 上表征「运行中」的脉冲点（`aiTradeAnyRunning`）**保留**——它传达状态而非轮数。

```tsx
// 删除：第 311-314 行 aiTradeMaxRounds 归约（仅徽标使用，删后无引用）。
// const aiTradeMaxRounds = aiTradeMembers.reduce((max, m) => Math.max(max, m.session?.roundsCompleted ?? 0), 0);

// tab 内容：去掉「第 N 轮」徽标 span，保留文案与运行脉冲点。
<ViewTab active={panelView === "aitrade"} onClick={() => setPanelView("aitrade")}>
  <span className="flex items-center gap-1.5">
    执行轨迹
    {aiTradeAnyRunning && (
      <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-[var(--long)]" />
    )}
    {/* 删除：<span className="tabular …">第 {aiTradeMaxRounds} 轮</span> */}
  </span>
</ViewTab>
```

- `aiTradeMembers` / `aiTradeAnyRunning`（第 309-310 行）**保留**（`aiTradeAnyRunning` 仍被脉冲点使用；`aiTradeMembers` 仍被 `aiTradeAnyRunning` 使用）。
- 既有 `AgentPanel segmented view` 测试用 `getByRole("tab", { name: /执行轨迹/ })` 正则部分匹配，删徽标后仍通过（§七补一条「tab 不含第 N 轮」正向断言）。

### 3.3 需求 R2：智能自动滚动（贴底跟随 + 2 分钟空闲恢复）

在 `AiTradeAgentTrace.tsx` 现有 `pinned` 机制上叠加一个「空闲恢复计时器」。状态机：

```
[跟随中 pinned=true]  --用户上滚(离底)-->  [用户接管 pinned=false, 启动 120s 倒计时]
[用户接管]            --再次滚动-->        [重置 120s 倒计时]（保持 pinned=false）
[用户接管]            --滚回底部/点回到最新--> [跟随中]（清除倒计时）
[用户接管]            --静置满 120s-->      [跟随中]（倒计时触发：pinned=true + 滚到最新）
[跟随中]              --新记录到达-->        自动滚到最新（既有 signature effect）
```

#### 3.3.1 常量与计时器

```tsx
// 距底阈值（px）：既有。
const PIN_THRESHOLD = 48;
// 用户离底后静置达此时长即自动恢复贴底跟随（第 3 轮 R2：2 分钟）。
const AUTO_FOLLOW_RESUME_MS = 120_000;
```

组件内新增一个计时器 ref 与两个助手，改写 `onScroll`：

```tsx
const idleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

const clearIdleTimer = () => {
  if (idleTimerRef.current != null) {
    clearTimeout(idleTimerRef.current);
    idleTimerRef.current = null;
  }
};

const armIdleTimer = () => {
  clearIdleTimer();
  idleTimerRef.current = setTimeout(() => {
    idleTimerRef.current = null;
    setPinned(true); // 恢复跟随；下方 [signature, pinned] effect 会滚到最新
  }, AUTO_FOLLOW_RESUME_MS);
};

const onScroll = () => {
  const el = containerRef.current;
  if (!el) return;
  const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < PIN_THRESHOLD;
  setPinned(nearBottom);
  if (nearBottom) clearIdleTimer(); // 回到底部 → 跟随，取消恢复倒计时
  else armIdleTimer();              // 离底浏览历史 → (重)启 120s 恢复倒计时
};
```

- 既有贴底 effect 不变：`useEffect(() => { if (pinned) endRef.current?.scrollIntoView({ block: "end" }); }, [signature, pinned])`。倒计时触发 `setPinned(true)` 会让该 effect 重跑并瞬时滚到最新（`behavior` 缺省=瞬时，避免平滑动画产生中间态 scroll 事件）。
- `jumpToLatest` 增加清计时器并**改用瞬时滚动**（P2-1：原 `behavior:"smooth"` 会在动画中途派发 `nearBottom=false` 的 scroll 事件而瞬时 `armIdleTimer`，虽落底自愈但削弱「程序化滚动恒落底、单次事件」不变量；改瞬时后该不变量对所有程序化路径成立，§3.3.2 论证严谨）：`clearIdleTimer(); setPinned(true); endRef.current?.scrollIntoView({ block: "end" });`
- 滚动容器加 `data-testid="ai-trade-trace-scroll"`（第 184 行的 `<div ref={containerRef} …>`），供测试注入几何与派发 `scroll`。

#### 3.3.2 用户滚动 vs 程序化滚动的区分（无需显式守卫）

程序化滚动（新记录跟随、倒计时恢复、点「回到最新」）**总是落到底部** → `nearBottom=true` → 走 `clearIdleTimer` 分支，永不 `armIdleTimer`；且三条程序化路径**全部用瞬时滚动**（`block:"end"` 无 `behavior`，单次 scroll 事件、无中间 `nearBottom=false`——P2-1 已将 `jumpToLatest` 一并改瞬时，堵住原 smooth 动画中途误触的漏洞）。因此不会把程序化滚动误判为「用户上滚」，无需引入 `programmaticRef` 之类守卫，保持实现简洁。仅「用户主动上滚离底」这一条路径会 `armIdleTimer`，语义精确。

#### 3.3.3 生命周期清理与切标的复位

```tsx
// 卸载清理，避免计时器泄漏。
useEffect(() => () => clearIdleTimer(), []);
// 切换聚焦标的：复位为跟随并清除上一标的的倒计时（切换即看新标的最新记录）。
useEffect(() => {
  clearIdleTimer();
  setPinned(true);
}, [focused]);
```

（`focused` 已是既有 state；此 effect 与既有「默认聚焦」effect 并存，二者职责不同：一个定位 focus，一个复位滚动。）

---

## 四、文件 / 模块变更计划

| 文件 | 变更类型 | 一行意图 |
|---|---|---|
| `apps/web/lib/aiTrade.ts` | 修改 | 新增并导出纯函数 `describeTrigger` + 类型 `AiTradeTriggerView`（由 `signalBoard` 派生触发原因 + tone，动作复用 `describeAiAction`）；import 块补 `AiTradeRoundTrace` |
| `apps/web/components/Agent/AiTradeTraceRound.tsx` | 修改 | `RoundHeader` 去「第 N 轮」改为「时间 + 触发原因 + 价」；`SummaryNode` 去「第 N 轮摘要」为「上下文已刷新」 |
| `apps/web/components/Agent/AiTradeAgentTrace.tsx` | 修改 | `TraceOverview` 去 `roundsCompleted` 序号、倒计时改「距上下文刷新 N 根K线」；新增 `AUTO_FOLLOW_RESUME_MS` + 空闲恢复计时器与清理/切标的复位 effect；`jumpToLatest` 改瞬时；容器加 `data-testid` |
| `apps/web/components/Agent/AgentPanel.tsx` | 修改 | **（P0-1）** 删「执行轨迹」tab 的 `第 {aiTradeMaxRounds} 轮` 徽标及仅供其用的 `aiTradeMaxRounds` 归约；保留运行脉冲点 |
| `apps/web/lib/aiTrade.test.ts` | 修改 | 为 `describeTrigger` 加单测（无信号/单策略/多策略同向/分歧/平仓 共 5 类） |
| `apps/web/components/Agent/AiTradeAgentTrace.test.tsx` | 修改 | 更新摘要文案断言；新增「轮头无第 N 轮 + 有触发原因」「概览无第 N 轮」「tab 无第 N 轮」「上滚后 120s 自动恢复贴底 + 期间滚动重置倒计时」用例 |

> **不改**：任何 `services/quant/**`、`store.ts`、`aiTradeTypes.ts` wire 形状、`AgentMessageList.tsx`（见 §八非目标）。工作区现存的无关脏文件（`AiTradeLaunchModal.tsx`、`chartSync.ts`、`services/quant/**` 等）不属本功能，Subtask #3 精确 `git add` 时**只暂存上表 6 个文件**，严禁 `git add -A`。

---

## 五、接口设计

无 REST/WebSocket/CLI 变更。新增前端纯函数接口：

```ts
// apps/web/lib/aiTrade.ts
export interface AiTradeTriggerView { label: string; tone: AiTraceTone }
export function describeTrigger(trace: Pick<AiTradeRoundTrace, "signalBoard">): AiTradeTriggerView;
```

组件 props 不变：`AiTradeAgentTrace({ group, activeInstId })`、`AiTradeTraceRound({ trace })`、`SummaryNode` 由 `({ round, summary })` 收窄为 `({ summary })`（内部私有组件，无外部调用者）。

---

## 六、数据模型

**无变更**。触发原因完全由既有 `AiTradeRoundTrace.signalBoard: StrategySignalView[]`（每项含 `latestSignal ∈ open_long|open_short|close|none`、`name`）派生，该数据已由 `round_start` 事件承载并经 `store.ts::applyAiTradeTracePhase` 落入 `member.traces`。`trace.round`、`session.roundsCompleted`、`session.roundsSinceSummary`、`session.contextRefreshRounds` 等字段一律保留在内存/wire 形状中，仅调整其 UI 显示。空闲恢复计时器为组件内 `useRef`，非持久化、不入 store、不入 `partialize`。

---

## 七、测试与验收标准

**运行前置**：组件 vitest 需 `NODE_ENV=test`（否则 React act 报错）；jsdom 无 `scrollIntoView`（测试文件已在顶部桩 `Element.prototype.scrollIntoView = vi.fn()`）；jsdom 不做布局，`scrollHeight/scrollTop/clientHeight` 默认为 0，须用 `Object.defineProperty(el, "scrollHeight", { value, configurable: true })` 注入几何以模拟「用户上滚离底」。

### 7.1 单元测试 `aiTrade.test.ts::describeTrigger`
- 空/全 `none` 信号板 → `{ label: "K 线收盘 · 常规复盘", tone: "muted" }`。
- 单策略 `open_long` → `label` 为 `"开多信号 · <名>"`、`tone: "long"`；单策略 `open_short` → `tone: "short"`；单策略 `close` → `"平仓信号 · <名>"`、`tone: "accent"`。
- 多策略同向（如 3× `open_long`）→ `"3 策略开多信号"`、`tone: "long"`。
- 多策略分歧（2× long + 1× short）→ `"信号分歧 · 2开多/1开空"`、`tone: "accent"`。

### 7.2 组件测试 `AiTradeAgentTrace.test.tsx`

> **测试数据前置（P2-3）**：`signalBoard` 项须为**完整** `StrategySignalView`（6 字段：`strategyId/name/stance/latestSignal/strength/reason`），否则 tsc 报缺字段。示例：`{ strategyId: 1, name: "均线突破", stance: "long", latestSignal: "open_long", strength: 0.8, reason: "" }`。
> **「无第 N 轮」断言的作用域（P2-2）**：`queryByText(/第\s*\d+\s*轮/)` 匹配的是渲染出的**全部**文本，包括模型生成的 `analysis`/`summary` 正文。故做「无第 N 轮」断言的用例，其 trace 的模型文本**不得**自含「第 N 轮」（如避免用 `summary: "截至第1轮…"` 这类数据）——断言针对 UI chrome，而非模型正文（与 §3.2.3 一致）。

- **R1 轮头**：给一条含 `signalBoard`（某策略 `latestSignal="open_long"`，完整对象见上）、且 `analysis/summary` 不含「第 N 轮」的 trace，断言出现触发短语文本（如 `/开多信号/`）、断言时间存在、`expect(screen.queryByText(/第\s*\d+\s*轮/)).not.toBeInTheDocument()`。
- **R1 摘要**：更新既有第 155 行断言为 `screen.getByText(/上下文已刷新/)`（去掉「（第 1 轮摘要）」），摘要正文断言不变（该用例**不**做「无第 N 轮」全局断言，因其 summary 数据自含「第1轮」）。
- **R1 概览**：用 `analysis/summary` 不含「第 N 轮」的 trace，断言概览条不含 `/第\s*\d+\s*轮/`。
- **R1 tab 徽标（P0-1）**：在 `AgentPanel segmented view` describe 块内，`useAppStore.setState({ aiTradeGroup })` 后渲染 `<AgentPanel />`，断言 `screen.queryByText(/第\s*\d+\s*轮/)` 不在文档（验证 tab 徽标已删）；既有 `getByRole("tab", { name: /执行轨迹/ })` 断言保持通过。
- **R2 空闲恢复**：`vi.useFakeTimers()`；渲染含 ≥1 trace 的分组；`getByTestId("ai-trade-trace-scroll")` 取容器，注入几何（`scrollHeight=1000, clientHeight=200, scrollTop=0`）后 `fireEvent.scroll` → 断言出现「回到最新」（`pinned=false`）；`act(() => vi.advanceTimersByTime(120_000))` → 断言「回到最新」消失且 `scrollIntoView` 被再次调用（`pinned=true` 恢复贴底）。
- **R2 倒计时重置**：上滚离底后 `advanceTimersByTime(60_000)` → 再 `fireEvent.scroll`（仍离底）→ 再 `advanceTimersByTime(90_000)`（距上次滚动仅 90s < 120s）→ 断言仍未恢复（「回到最新」仍在）；再 `advanceTimersByTime(30_000)`（累计 120s）→ 断言已恢复。
- **回归**：既有「等待空态 / 流式研判 / 已成交 / 降级 / 停止空态 / 多标的段控」用例保持通过。

### 7.3 验收标准（Definition of Done）
1. 面板 UI chrome 任意位置（轮头、概览条、摘要节点标题、**执行轨迹 tab 徽标**）均无「第 X 轮」文案；模型生成的 `analysis`/`summary` 正文若自含「第 N 轮」属数据非 UI，不在此列（§3.2.3）。
2. 每条执行记录轮头首要呈现「时间 + 触发原因」，触发原因按方向着色，布局不溢出。
3. 新记录到达时（跟随态）自动滚到最新；用户上滚后暂停并出现「回到最新」；静置满 2 分钟自动恢复贴底并滚到最新；期间每次滚动重置该 2 分钟倒计时；滚回底部或点「回到最新」立即恢复。
4. `pnpm test`（前端 vitest）与 `tsc --noEmit` 在改动文件上零错（既知无关 `WeightBatchArena.test.tsx` 预存 tsc 错除外）；后端不受影响。
5. 无 `services/quant/**`、`store.ts`、wire 形状改动。

---

## 八、风险与缓解

| 风险 | 等级 | 缓解 |
|---|---|---|
| 程序化滚动被误判为用户上滚，导致倒计时被反复重置/误触发 | 中 | 程序化滚动恒落底 → 走 `clearIdleTimer` 分支永不 `armIdleTimer`；跟随/恢复用瞬时滚动无中间态；无需守卫（§3.3.2） |
| jsdom 几何为 0、无 scrollIntoView，测试无法复现滚动 | 中 | 测试注入几何 + 沿用既有 scrollIntoView 桩 + `data-testid` 定位容器（§七前置） |
| 计时器跨卸载/切标的泄漏或误触发别的标的 | 中 | `useEffect` 卸载清理 + `[focused]` 复位清理（§3.3.3） |
| 删除轮数显示误伤内部逻辑（React key / reducer 去重 / signature） | 低 | 仅删显示不删数据；`trace.round` 等字段全部保留（§3.1 注） |
| 触发原因在降级/跳过/异常轮 `signalBoard` 为空 | 低 | `describeTrigger` 空板回落「K 线收盘 · 常规复盘」，无异常 |
| 触发短语过长破坏轮头布局 | 低 | `truncate` + `min-w-0` + 价格/chip `shrink-0` |
| 摘要正文含模型自述「第 N 轮」 | 低 | 属数据/模型文本非 UI chrome，明确不在范围（§3.2.3） |
| 概览「上下文刷新倒计时」去留分歧 | 低 | §九已拍板：保留并改写为「距上下文刷新 N 根K线」、≤0 隐藏（P1-3） |
| 遗漏站点导致主需求不达标（如本轮 `AgentPanel` tab 徽标） | 中 | 已全库 grep `第\s*\{`/`轮` 复核，补齐第 4 处（§3.2.5）；验收 §7.3.1 增 tab 断言兜底 |

---

## 九、实施边界、非目标与评审决策（已拍板）

**非目标**：
- 不改 `AgentMessageList.tsx`（对话流的贴底跟随）。本轮需求仅针对「AI 交易执行轨迹」面板；对话流保持其较简单的 pin 行为，避免超范围重构（CLAUDE.md「不引入超出方案的额外重构」）。
- 不新增后端「触发原因」字段、不改 wire、不改 store 形状。
- 不改动会话触发节奏（仍每根确认 K 线一轮）。

**评审决策（Subtask #1 已拍板，P1-3）**：
1. **轮头保留最新价 `lastPx`** —— ✅ 保留为 muted 次要信息。理由：`lastPx` 是「决策时市价」，属市场上下文而非轮数序号，用户可一眼知悉该轮在何价位决策；与右侧状态 chip 并列不喧宾夺主。R1 诉求针对的是「轮数序号噪音」，`lastPx` 不在其列。
2. **概览「上下文刷新倒计时」保留并改写** —— ✅ 保留，措辞改为「距上下文刷新 N 根K线」，`N≤0` 时隐藏（§3.2.4）。理由：这是「机制透明度」提示（告知再过几根 K 线 Agent 会做摘要并刷新上下文），非「第 N 轮」序号；用户仅抱怨轮数序号，未涉及该倒计时，最小改动即改写去序号。
3. **触发短语文案** —— ✅ 采纳 `K 线收盘 · 常规复盘 / <动作>信号 · <策略名> / N 策略<动作>信号 / 信号分歧 · a开多/b开空`；动作中文化复用 `describeAiAction`（P1-1）。措辞可后续按产品语感微调，不影响结构。

---

## 十、给下游节点（Subtask #2/#3）的落地提示

- 严格按 §四表落 **6 个文件**（含 P0-1 新增的 `AgentPanel.tsx`）；组件 vitest 记得 `NODE_ENV=test`。
- 触发原因优先复用 `describeAiAction`/`TONE_VAR` 等既有件，保持设计语言一致（P1-1：**不要**新建动作标签表）。
- 提交阶段用精确路径 `git add`，**不要** `git add -A`（工作区有大量其它功能的脏文件与未跟踪目录）：
  ```
  git add apps/web/lib/aiTrade.ts \
          apps/web/components/Agent/AiTradeTraceRound.tsx \
          apps/web/components/Agent/AiTradeAgentTrace.tsx \
          apps/web/components/Agent/AgentPanel.tsx \
          apps/web/lib/aiTrade.test.ts \
          apps/web/components/Agent/AiTradeAgentTrace.test.tsx
  ```
  （PowerShell/cmd 下反斜杠续行不通用，请分行或单行列全路径）；commit 以 `feat:` 前缀 + 一句话摘要；`git log -1 --stat` 应只含上述 6 个本功能文件。

---

## 评审结论（Review Verdict）

**结论：有条件通过（Approved with conditions）。**

两项需求方向正确、技术选型可行、规模适配得当，纯前端落点将改动面与回归风险压到最低。评审逐节核对了 spec 对真实代码的每一处断言（文件、行号、类型、既有函数、测试），已就地修复全部 P0/P1，文档升至 **v2**，无遗留 P0/P1。放行条件如下，请 Subtask #2/#3 严格执行：

1. **（源自 P0-1，最关键）** 必须一并改 `AgentPanel.tsx`，删除「执行轨迹」tab 的 `第 {aiTradeMaxRounds} 轮` 徽标及仅供其用的 `aiTradeMaxRounds` 归约（§3.2.5）——否则主需求「面板无第 X 轮」不达标。落地后应确认面板**四处**「第 X 轮」全部消除（轮头 / 摘要节点 / 概览条 / tab 徽标）。
2. **（源自 P1-1）** `describeTrigger` 的动作中文化复用既有 `describeAiAction`，**不得**新建 `TRIGGER_ACTION_LABELS` 之类重复标签表。
3. **（源自 P1-2）** 在 `aiTrade.ts` 的 `import type { … } from "./aiTradeTypes"` 块补入 `AiTradeRoundTrace`，确保 `tsc --noEmit` 零错。
4. **（源自 P1-3）** 按 §九已拍板的三项决策落地：保留 `lastPx`（muted）、概览倒计时改「距上下文刷新 N 根K线」且 ≤0 隐藏、触发短语采纳既定措辞。
5. **（源自 P2-2/P2-3）** 新增「无第 N 轮」断言的用例，其 trace 模型文本不得自含「第 N 轮」；`signalBoard` 测试项须为完整 `StrategySignalView`（6 字段）。
6. **验证门槛**：改动文件上 `pnpm test`（`NODE_ENV=test`）与 `tsc --noEmit` 零错（既知无关 `WeightBatchArena.test.tsx` 预存 tsc 错除外）；提交仅含 §四所列 6 个文件，`git add` 用精确路径。

> 评审维度小结：**可行性** ✓（数据链路 `signalBoard.latestSignal`、既有 tone/format 件、jsdom 几何注入均已核实可行）；**完整性** ✓（补齐遗漏站点、边界与测试作用域、错误路径空信号板回落）；**一致性** ✓（复用 `describeAiAction`、沿用主题变量与设计语言、符合 CLAUDE.md 阈值与「interface 优先」）；**规模适配** ✓（6 文件纯前端，无过度设计、无欠规格，非目标边界清晰）。
