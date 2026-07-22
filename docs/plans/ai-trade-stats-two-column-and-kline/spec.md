# AI 自动交易统计看板 · 左右双栏 + 预计年化 + 交易记录 K 线（ai-trade-stats-two-column-and-kline）开发方案

> 版本：**v2**（方案评审与修复，Subtask #1 产出）
> 作者 / 评审：senior reviewer（Anthropic 工程团队）
> **流程说明（透明记录）**：上游「方案设计」节点（Subtask #0）在看板上标记 `completed`，但**未在 `docs/plans/` 下产出任何 `spec.md`**（经全量时间戳与 `git status` 核查确认为零产出——此为本仓已知的复发模式）。为不阻断下游「代码开发」（Subtask #2），评审节点在**逐文件核对真实源码**的基础上补齐设计正文，并在**同一轮**完成评审、就地修复全部 P0/P1、产出本 v2 文档。设计正文的每一处代码断言（组件名 / 函数签名 / props / 数据结构 / 落库路径 / 复用组件）均以真实符号为锚二次确认，避免出现「sketch 与真实符号对不上、开发期即报错」的缺陷。
> 适用仓库：`M:/takoAI/AragonTrading`（monorepo：`apps/web` Next.js 14 + `services/quant` FastAPI + SQLite）
> 关联既有方案（本方案在其之上做 UI 增强）：`docs/plans/ai-trade-stats-dashboard/spec.md`（已落地的「统计看板」基座，本方案复用其全部组件与端点）
> 复用蓝本：`apps/web/components/Factor/WeightBacktestChart.tsx`（slim K 线回放，lightweight-charts v4 + markers + 成交连线，本方案 K 线组件的直接镜像）、`apps/web/lib/tradeTrails.ts`（成交 markers / 段落纯构造，已单测）、`apps/web/lib/api.ts::candlesWithMeta`（任意历史窗口 K 线拉取）

---

## 评审记录（Review Notes）

> 本节由 Subtask #1（方案评审与修复）产出。评审方法：以三个并行只读核查通道逐文件核对——(1) 现有看板前端组件（`AiTradeStatsPanel`/`AiTradeStatsLive`/`AiTradeStatsHistoryList`/`AiTradeStatsDetail`/`AutoTradeRoundTripRow`/`AutoTradePnlSummary`/`autoTradePnl`/`ui.Modal`/`globals.css`/`store`）、(2) K 线图基础设施（`CandleChart`/`WeightBacktestChart`/`tradeTrails`/`api.ts`/`aiBacktest.barSeconds`/`lightweight-charts` 依赖）、(3) 后端 OHLC 数据源（`api/market.py::/candles`/`okx/rest_client.py`/`db/ai_auto_repositories.py`/`schemas.trade_out`）。
> **核查总体结论**：三条需求（① 面板 60% 宽 + 左右双栏、② 左栏预计年化、③ 右栏交易记录展开显示 K 线买卖点位）**全部可行、右尺寸、且可近乎全复用既有实现**：Modal 宽度经 `className` 单行即可放宽到 60vw（arbitrary vw 值本仓已广泛使用）；预计年化是**纯前端派生**（`group.startedTs` + 已实现收益 + `totalBudgetUsd` 全部可达）；K 线**无需任何后端改动**——`GET /api/candles` 已支持 `before/after` 秒级游标做**任意历史窗口**拉取、`WeightBacktestChart` 已把 `Candle[] + Trade[]` 渲染为蜡烛 + 买卖 markers + 开→平连线、`RoundTrip` 已携带 `instId/entry/exit/openedTs/closedTs`、`bar` 在 `AiTradeGroupState.bar` 与 `AiTradeHistoryDetailOut.bar` 均可得。但发现 **4 个 P1**（1 处会开发期即崩、1 处触碰「绝不伪造数据」红线、1 处会回归其它调用点、1 处是外部依赖的正确性 / 成本缺口）与 **8 个 P2**，均已在下方就地修复或明确接地。**无 P0**。

### 严重度定义
- **P0**：方案根本不可行 / 造成数据错误或资金安全风险，必须修复否则不可进入开发。
- **P1**：代码断言与真实源码不符（开发期即失败）、或存在正确性 / 诚实性 / 回归缺口，必须就地修复。
- **P2**：完整性 / 一致性 / 措辞欠精确，就地澄清以免开发期误解。

### P1（已就地修复）

| 编号 | 维度 | 问题 | 真实源码 / 依据 | 修复（v2） |
|---|---|---|---|---|
| **P1-1** | 可行性 / 一致性 | K 线组件若**静态 import** `lightweight-charts` 或把 `var(--x)` 直接喂给图库，会在 **SSR 期崩溃**、且配色全黑（v4 不解析 CSS 变量）| 本仓**所有** lightweight-charts 用法（`ChartWorkspace.tsx:43`、`WeightBacktestPanel.tsx:17`、`AiBacktestModal.tsx:36`）均 `next/dynamic(..., { ssr:false })` 动态引入；配色一律经本地 `cssVar()`（`WeightBacktestChart.tsx:127-130`）从 computed style 解析为具体色值，绝不把 `var(--x)` 传给图库 | §3.4 强制：纯图组件 `TradeCandleChart` **必须**由 `TradeCandleChartPanel` 经 `next/dynamic(..., { ssr:false })` 引入；一切设计令牌走 `cssVar()`；建图 effect 依赖含 `theme`（明暗切换即重建）——逐字镜像 `WeightBacktestChart` |
| **P1-2** | 正确性 / 诚实性 | 「预计年化收益」若对**极短样本朴素外推**（如运行 5 分钟、1 笔交易也给出年化），会产出「看起来像收益」的天文数字，误导用户，**违反 CLAUDE.md「绝不伪造数据 / 显式降级」** | 短窗年化在数学上必然放大：`YEAR/elapsed` 在 12h 样本即达 730×，任何小收益都会外推成数万百分比 | §3.3 定死诚实护栏：① **最小样本门禁**（`minElapsedSec` 默认 6h、`minTrades` 默认 3，二者任一不满足 → 不出数字、显式「样本不足以估算年化」）；② 采用**线性外推**（`returnPct × YEAR/elapsed`，非复利爆炸）；③ 卡片必带**免责声明 + 样本基数**（「基于 X 天 / N 笔样本线性外推，非未来承诺」）；④ 口径 = **已实现收益**（不含浮动盈亏，明示）；⑤ `elapsed<=0` / `1+returnPct<=0` 安全降级 |
| **P1-3** | 可行性 / 回归 | 需求③要求「每条交易记录都能展开看 K 线」，但现 `AutoTradeRoundTripRow` 的 `expandable = Boolean(rt.exit.explain)`——**仅** exit 带 `explain` 才可展开。若直接改成「全部可展开」，会**回归**该共享组件的其它调用点（最左侧 `AutoTradePnlPanel` 等 live-auto / paper 面板）| `AutoTradeRoundTripRow.tsx:21` `const expandable = Boolean(rt.exit.explain)`；该组件被 AI-trade 看板与 live-auto 面板共享 | §3.5 改为**可选 prop 门控**：新增 `bar?: string`；`expandable = Boolean(bar) || Boolean(rt.exit.explain)`。**仅** AI 统计看板向该 row 传 `bar` → 只有它开启「行内 K 线」；其它调用点不传 `bar`、行为**逐字节不变**（零回归） |
| **P1-4** | 可行性 / 完整性（成本 & 边界） | 为历史 / 老交易拉 K 线走的是 **OKX 公共实时 REST**。若每次展开都无缓存地打，或一次展开触发大量并发，会有成本与 `history-candles` **旧数据缺失**（拉空）问题 | `api.candlesWithMeta`→`GET /api/candles`→`okx/rest_client.py` 公共路径**已自带节流（0.12s）+ 429 退避**；但**缓存只服务「最近 N」不服务任意旧窗口**（`repositories.get_cached_candles`），旧窗口须实时拉；OKX `history-candles` 保留期有限，极旧窗口可能拉空 | §3.4 定死：**懒加载**（`useQuery` `enabled: open`，仅展开时拉）+ **单开手风琴**（既有 `openRowKey`，同时至多 1 个在飞）+ **不可变缓存**（`queryKey:[instId,bar,openedTs,closedTs]`、`staleTime: Infinity`，重复展开零重拉）+ **拉空 / 出错优雅降级**（「该时段 K 线暂不可用」，不阻断该行其余信息）|

### P2（已就地澄清 / 接地）

| 编号 | 维度 | 问题 | 修复（v2） |
|---|---|---|---|
| **P2-1** | 一致性 | K 线组件的 `theme` 从何取 | 复用既有链路 `useAppStore((s) => s.theme)`（`store.ts:300`，`"light"\|"dark"`，与 `ChartWorkspace`/`WeightBacktestPanel` 同源），下传给纯图组件作重建触发；`cssVar()` 自动随 `.dark` 类解析明暗色，无需手工配色（§3.4） |
| **P2-2** | 完整性 | 窗口尺寸 / `openedTs==closedTs`（同 bar 内开平）如何取 | 复用既有 `barSeconds(bar)`（`aiBacktest.ts:26`，未知周期回落 3600）；窗口跨度 `span = max(closedTs-openedTs, barSec*4)`、两侧各 padding `padBars=6`；`limit = min(1000, ceil(span/barSec)+2*padBars+4)`；拉回后按 `[openedTs-pad, closedTs+pad]` 客户端裁剪（§3.4）|
| **P2-3** | 完整性 / 一致性 | 双栏后 KPI 网格在窄左栏会拥挤；外层 body 原是单列 `overflow-auto`，双栏须避免嵌套双滚动 | 左栏 KPI 由 `sm:grid-cols-4` 收敛为 `grid-cols-2`；面板 body（`AiTradeStatsPanel.tsx:42`）与两页正文由 `flex-col` 改 `flex min-h-0`，**左右两栏各自 `overflow-y-auto`**、去掉外层单一滚动（§3.2 / §3.6）|
| **P2-4** | 完整性（HCI / 响应式） | 60vw 在窄屏（< md）过挤 | Modal 宽度 `max-w-[60vw]` 加**小屏回退** `max-md:max-w-[94vw]`；双栏容器 `max-md:flex-col`（窄屏上下堆叠、各自占满）——符合 HCI 最佳实践（§3.2）|
| **P2-5** | 正确性 / 一致性 | 预计年化必须**与时间维度筛选解耦**（否则用户切 7d / 30d 会让年化跳动，语义错乱）| 年化口径 = **全会话已实现收益**（`range="all"` 等价的未筛选 `buildLedger` 汇总）+ **真实经历时长**（live: `now-startedTs`；history: `endedTs-startedTs`），**不接** `AutoTradePnlSummary` 里被筛选的 `summary`——与「持仓浮盈条」既有解耦范式一致（§3.3）|
| **P2-6** | 一致性 | 历史详情的 `bar` 是**组级**（同组成员共享一个周期），非逐成员 | 详情 `MemberTrades` 向每条 row 传 `bar={detail.bar}`（`AiTradeHistoryDetailOut.bar` 顶层字段，源自 `runs[0].bar`）；AI-trade 分组同组同周期，逐成员传同值正确（§3.6）|
| **P2-7** | 完整性 | 时间戳单位 | 全链路**秒**：`candle.ts`、`entry.ts`/`exit.ts`、`openedTs`/`closedTs`、`startedTs` 均为秒；lightweight-charts `UTCTimestamp` 亦按秒；`before/after` 查询参数按秒——全程不得混入毫秒（§6）|
| **P2-8** | 一致性（测试） | 组件测试在 jsdom 下无法真跑 lightweight-charts（需 canvas / DOM API）| §7：纯逻辑（`projectAnnualizedReturn`、窗口尺寸、`tradeTrails` 复用）走**纯函数单测**；含图组件测试**须** `NODE_ENV=test` 且 **mock `TradeCandleChart`**（对齐既有 `CandleChart.test.tsx` 的图库处理），断言的是「展开触发拉取 + 状态机（loading/error/empty/ok）」而非图库内部 |

---

## 1. 概述（Overview）

本方案是对**已落地**的「AI 自动交易统计看板」（`docs/plans/ai-trade-stats-dashboard/spec.md`，前端 `AiTradeStatsButton → AiTradeStatsPanel`，含「当前 / 历史」两页 + 单会话详情下钻）的**三点 UI 增强**，不改任何数据口径、不新增任何后端存储：

1. **面板加宽为屏宽 ~60% 并改为左右双栏**：现看板 Modal 为 `max-w-3xl`（768px）的**单列纵向**布局（统计在上、交易明细在下）。改为 `~60vw` 宽的**左右双栏**——**左栏**承载「现在这种统计数据」（KPI 概览 + 时间筛选 + 运行观测 + 持仓浮盈），**右栏**承载「具体的交易记录」（倒序完整交易往返明细列表）。「当前」页与「历史·单会话详情」页同构采用该双栏。
2. **左栏新增「预计年化收益」**：据当前（或该历史会话）的已实现收益与真实经历时长，**线性外推**估算年化收益率。因短样本外推天然放大，严格附**最小样本门禁 + 免责声明 + 样本基数**，不满足即诚实降级为「样本不足以估算年化」（P1-2）。
3. **右栏交易记录展开显示 K 线**：每条完整交易往返（开→平）行可展开，展开后在**该标的、该周期、该往返时间窗口**上渲染一张 slim K 线（蜡烛）图，叠加**买（BUY）/ 卖 / 平（CLOSE）markers**与**开仓点→平仓点连线**，并保留既有平仓触发原因（`TradeExplainDetail`）。

核心策略是**最大化复用、零后端改动**：K 线数据走既有 `GET /api/candles`（已支持任意历史窗口）；K 线渲染逐字镜像既有 `WeightBacktestChart`（lightweight-charts v4 + `tradeTrails` 纯构造 markers / 连线）；交易往返数据来自既有 `RoundTrip`（`autoTradePnl`）；面板宽度仅改一行 `className`。全程遵循 CLAUDE.md「绝不伪造数据 / 单一数据源 / 显式降级 / 因果时序」：K 线蜡烛来自真实 OKX 行情，买卖点位来自真实落库成交价 / 时刻，年化外推明确标注为估算并对短样本诚实降级。

---

## 2. 现状与关键代码路径（Grounding）

> 本节所有结论均经源码核对（行号以核对时为准，实现时以符号为锚二次确认）。

### 2.1 现看板结构（本方案改造对象）

- **入口 & 壳**：`components/Agent/AiTradeStatsButton.tsx`（37 行，自持 `open`）→ `components/Agent/AiTradeStatsPanel.tsx`（53 行）。Panel `:23` `<Modal ... className="max-w-3xl">`；`:38-40` 数据驱动 `Tabs[当前｜历史]`（`variant="underline"`）；`:42` body `<div className="flex max-h-[72vh] flex-col overflow-auto px-5 py-4">`；`:43-49` 按 `active` / `selectedGroupId` 条件渲染 `AiTradeStatsLive` / `AiTradeStatsDetail` / `AiTradeStatsHistoryList`。
- **「当前」页**：`aiTradeStats/AiTradeStatsLive.tsx`（167 行）。`:23` `useAppStore((s) => s.aiTradeGroup)`；`:24` `range` state（`AutoTradeRange`，默认 `"all"`，向 `AutoTradePnlSummary` 下传）；`:26` `openRowKey`（单开手风琴）；`:42-54` `buildLedger → filterRoundTrips → sort desc → summarizeRoundTrips`；`:63` 根 `flex flex-col gap-3`：数据起始提示 → 运行观测卡 → `<AutoTradePnlSummary>`（`:76-82`，时间筛选 + KPI 网格）→ 持仓浮盈条（`:83-93`，诚实降级、与窗口解耦）→ 往返明细列表（`:94-110`，`visible.map` → `<AutoTradeRoundTripRow rt open onToggle>`）。
- **「历史·详情」页**：`aiTradeStats/AiTradeStatsDetail.tsx`（302 行）。`DetailBody`（`:53-112`）单列 `flex flex-col gap-3`：返回 + 「已对账」chip → 元信息条 → `<KpiGrid>` → 「权益 / 回撤」区（`PeriodReturnBars`/`UnderwaterChart`，`combinedEquity=mergeMemberEquity(members)`，`:55`/`272-288`）→ `<AiStatsCard>` → 「逐成员完整交易」（`:103-109` `MemberTrades` → 每成员 `buildRoundTrips(member.instId, member.trades, member.leverage)` → `<AutoTradeRoundTripRow>`）。顶层 `detail.bar`（`AiTradeHistoryDetailOut.bar`）可得。
- **交易往返行**：`components/Chart/AutoTradeRoundTripRow.tsx`（80 行）。props `{ rt: RoundTrip; open; onToggle }`；`:21` `expandable = Boolean(rt.exit.explain)`；折叠态显示方向 Badge / `instId` / `entry.price→exit.price` / `fmtTime(closedTs)` / 持仓时长 / `signed(pnlUsd)` / `fmtPct(pnlPct)`；展开态（`:57-61`）`<TradeExplainDetail trade={rt.exit} />`。
- **往返数据**：`lib/autoTradePnl.ts` `RoundTrip`（`:12-22`）：`{ instId, direction:"long"|"short", entry:Trade, exit:Trade, pnlUsd, pnlPct:number|null, openedTs, closedTs, isWin }`。`entry.price`/`exit.price`/`entry.ts`/`exit.ts` 来自 `Trade`。
- **Modal**：`components/ui/index.tsx`（`Modal` `:251-341`）。props `{ open, onClose, labelledBy?, className?, children }`；面板基类 `flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden ...`，`className` 在 `clsx` **末位**（调用方 `max-w-*` 覆盖基类 `max-w-2xl`）。本仓 arbitrary vw / calc 宽度已广泛使用（`LiveBottomPanel.tsx:38` `w-2/5 min-w-[300px] max-w-[460px]` 等），`max-w-[60vw]` 合法。
- **设计令牌**（`app/globals.css`）：`--long`（盈 / 绿）、`--short`（亏 / 锈红）、`--accent`（权益线 / 强调）、`--surface`/`--surface-2`（卡片底）、`--muted`（次要）、`--border`/`--bg`/`--text`；`.tabular` 等宽数字。`darkMode:"class"`（`.dark` 挂 `document.documentElement`）。

### 2.2 K 线基础设施（需求③ 可行性接地，零后端改动）

- **图库**：`lightweight-charts`（TradingView）`^4.2.0`（`package.json:16`），**始终** `next/dynamic(..., { ssr:false })` 引入。
- **slim 蜡烛组件蓝本**：`components/Factor/WeightBacktestChart.tsx`（125 行）。props `{ candles:Candle[]; trades:Trade[]; equity:EquityPoint[]; theme:"light"|"dark" }`；一次成型建图 effect（`:42-122`）：`addCandlestickSeries` + `candleSeries.setData(candles.map(c => ({time:c.ts, open:c.o, high:c.h, low:c.l, close:c.c})))`（`:71-79`）+ `buildTradeMarkerEvents(trades)`→`setMarkers`（`:82-83`）+ `buildTradeTrailSegments(trades)`→两点连线（`:86-100`）+ `fitContent()`（`:119`）+ `return () => chart.remove()`；`cssVar()`（`:127-130`）解析令牌；容器 `h-[320px] w-full`（`:124`）。
- **成交 markers / 段落纯构造（已单测）**：`lib/tradeTrails.ts`——`buildTradeMarkerEvents(trades)`（因果配对 open→close，出 `entry-long/entry-short/close`）、`buildTradeTrailSegments(trades)`（每往返一段 `{entry,exit,direction,pnl,isWin,canRenderLine,lineStyle}`）。单个往返直接喂 `trades={[rt.entry, rt.exit]}` 即得 BUY/SELL/CLOSE markers + 开→平连线。
- **任意历史窗口拉取**：`lib/api.ts::candlesWithMeta(instId, bar, limit=300, before?, after?)`（`:114-127`）→ `GET /api/py/candles`，返回 `{ candles:Candle[]; source:"live"|"cache" }`。`Candle`（`lib/types.ts:14-22`）：`{ ts, o, h, l, c, vol, confirm? }`（**秒**）。窗口范式（`page.tsx:215-218`）：`candlesWithMeta(instId, bar, 1000, endTs/*before*/, startTs/*after*/)` 再客户端裁剪。
- **周期→秒**：`lib/aiBacktest.ts::barSeconds(bar)`（`:26`，`BAR_SECONDS` 表，未知回落 3600）。
- **主题源**：`useAppStore((s) => s.theme)`（`store.ts:300`）。

### 2.3 后端 OHLC（需求③ 数据源，零改动确认）

- `GET /api/candles`（`api/market.py:69-129`，mount `prefix="/api"`）：query `instId`、`bar`（默认 `"1H"`）、`limit`（1..1000，默认 300）、`before`（秒 UTC，「早于该 ts」）、`after`（秒 UTC，「晚于该 ts」）；返回 `[{ts,o,h,l,c,vol}]`（**升序、秒**）。
- 数据源 = OKX 公共 REST（`okx/rest_client.py:130-184`），`before/after` 走 `history-candles` 分页可取**任意历史窗口**；公共路径**自带 0.12s 节流 + 429 指数退避**（`rest_client.py:38-53/90-110`）。已知 429 诊断（`docs/diagnoses/live-auto-multi-symbol-rate-limit-429`）针对**私有签名**路径，**不涉**公共 candles。
- 缓存 `candles_cache` 仅服务「最近 N」回退（`repositories.get_cached_candles`，默认 `allow_cached_fallback=False`），**不服务任意旧窗口** → 旧交易窗口须实时拉（P1-4 的懒加载 + 前端缓存据此设计）。
- 交易→窗口的字段路径：`Trade` 行不含 `instId/bar`（须经父 `StrategyRun.inst_id`/`.bar`）；但前端 `RoundTrip.instId` 已在手，`bar` 取自 `AiTradeGroupState.bar`（当前页）/ `AiTradeHistoryDetailOut.bar`（详情页），故**前端渲染点已具备 instId + bar + 开 / 平 ts**，无需后端补任何字段。

---

## 3. 技术设计（Technical Design）

### 3.1 架构总览（纯前端增强，后端零改动）

```
后端：不改任何文件（GET /api/candles 已满足需求③；历史端点 / schemas / 落库均不变）

前端
  components/Agent/AiTradeStatsPanel.tsx              （改）Modal 宽 max-w-3xl → 60vw + 响应式；body 单列 → 双栏容器
  components/Agent/aiTradeStats/AiTradeStatsLive.tsx  （改）左右双栏：左=统计+预计年化+浮盈；右=往返明细（传 bar）
  components/Agent/aiTradeStats/AiTradeStatsDetail.tsx（改）左右双栏：左=KPI+权益图+预计年化；右=逐成员往返（传 bar）
  components/Agent/aiTradeStats/AnnualizedProjectionCard.tsx（新）预计年化卡（消费 projectAnnualizedReturn，诚实降级）
  components/Chart/AutoTradeRoundTripRow.tsx          （改）+可选 bar?：可展开门控放宽 + 展开渲染行内 K 线面板（零回归其它调用点）
  components/Chart/TradeCandleChartPanel.tsx          （新）行内 K 线：useQuery 拉窗口 K 线 + 状态机 + 动态引入纯图组件
  components/Chart/TradeCandleChart.tsx               （新）纯 slim 蜡烛图（镜像 WeightBacktestChart：candles+markers+连线；ssr:false）
  lib/annualized.ts                                   （新）projectAnnualizedReturn 纯函数 + 护栏
```

### 3.2 面板宽度与双栏骨架（`AiTradeStatsPanel.tsx`）

- **宽度**：`:23` `className="max-w-3xl"` → `className="max-w-[60vw] max-md:max-w-[94vw]"`（基类 `w-full` 保留 → 宽屏取 60vw，窄屏回退近满屏；高度沿用基类 `max-h-[85vh]`）。**仅改此一行**即满足需求①的「60% 宽」。
- **双栏容器**：本 Panel 的 body（`:42`）保持为**页级滚动壳**，但把 `flex ... flex-col` 改为 `flex min-h-0 flex-col`（去掉 `overflow-auto`，改由两页正文自持双栏与各自滚动，避免 P2-3 的嵌套双滚动）。左右双栏在 `AiTradeStatsLive` / `DetailBody` 内实现（因「历史·列表」页不分栏——它是会话卡导航，全宽即可）。
- 双栏通用骨架（Live 与 Detail 复用同一版式，直接内联，不额外抽壳组件以免过度设计）：
  ```tsx
  <div className="flex min-h-0 flex-1 gap-4 max-md:flex-col">
    <aside className="flex w-[40%] shrink-0 flex-col gap-3 overflow-y-auto pr-1 max-md:w-full">
      {/* 左栏：统计数据 + 预计年化 */}
    </aside>
    <section className="flex min-w-0 flex-1 flex-col gap-2 overflow-y-auto">
      {/* 右栏：交易记录明细（可展开 K 线）*/}
    </section>
  </div>
  ```
  左栏约 40%、右栏约 60%；`max-md:flex-col` 窄屏堆叠（P2-4）。

### 3.3 预计年化收益（`lib/annualized.ts` 新 + `AnnualizedProjectionCard.tsx` 新）

**纯函数**（可单测、绝不抛）：
```ts
export interface AnnualizedProjection {
  ok: boolean;                 // false → 样本不足，不出数字
  annualizedPct: number | null;
  elapsedSec: number;
  tradeCount: number;
  reason?: "no-elapsed" | "too-short" | "too-few-trades" | "wipeout";
}
const YEAR_SECONDS = 365 * 86_400;
export const MIN_ELAPSED_SEC = 6 * 3600;   // 默认门禁：≥6h（可调，调整须记录）
export const MIN_TRADES = 3;               // 默认门禁：≥3 笔完整交易

export function projectAnnualizedReturn(params: {
  returnPct: number;    // 全会话已实现收益率（小数，如 0.05）
  elapsedSec: number;   // live: now-startedTs；history: endedTs-startedTs
  tradeCount: number;   // 全会话完整往返数
  minElapsedSec?: number;
  minTrades?: number;
}): AnnualizedProjection {
  const { returnPct, elapsedSec, tradeCount } = params;
  const minE = params.minElapsedSec ?? MIN_ELAPSED_SEC;
  const minT = params.minTrades ?? MIN_TRADES;
  if (!(elapsedSec > 0)) return { ok:false, annualizedPct:null, elapsedSec, tradeCount, reason:"no-elapsed" };
  if (elapsedSec < minE)  return { ok:false, annualizedPct:null, elapsedSec, tradeCount, reason:"too-short" };
  if (tradeCount < minT)  return { ok:false, annualizedPct:null, elapsedSec, tradeCount, reason:"too-few-trades" };
  if (1 + returnPct <= 0) return { ok:false, annualizedPct:null, elapsedSec, tradeCount, reason:"wipeout" };
  // 线性外推（非复利爆炸）：如按当前收益速度持续一年
  const annualizedPct = returnPct * (YEAR_SECONDS / elapsedSec);
  return { ok:true, annualizedPct, elapsedSec, tradeCount };
}
```
**护栏（P1-2 / P2-5）**：
- **口径解耦**：`returnPct` 取**全会话已实现收益**（`summarizeRoundTrips(buildLedger(members)/* 未按 range 筛选 */, capitalBase).totalReturnPct`；history 取 `summary.returnPct`），`elapsedSec` 取真实经历时长——**不接**被时间筛选的 `summary`（否则切窗年化跳变，语义错乱）。与既有「持仓浮盈条与窗口解耦」范式一致。
- **仅已实现**：不含浮动盈亏（卡片明示「仅计已实现」）。
- **卡片诚实呈现**：`ok===false` → 显示「样本不足以估算年化」+ 具体原因（运行需 ≥6h 且 ≥3 笔完整交易）；`ok===true` → 大字 `fmtPct(annualizedPct)`（色走 `colorForReturn`）+ 次行「按当前已实现收益线性外推一年（估算，非未来承诺）· 基于 {fmtDuration(elapsedSec)} / {tradeCount} 笔样本」。样本越短越不可靠的提示常驻。
- 卡片组件 `AnnualizedProjectionCard`（props `{ returnPct; elapsedSec; tradeCount }`）由 Live 与 Detail 复用，置于各自**左栏**。

### 3.4 行内 K 线（`TradeCandleChart.tsx` 新 + `TradeCandleChartPanel.tsx` 新）

**`TradeCandleChart.tsx`（纯图，镜像 `WeightBacktestChart`，去掉 equity 轴、容器更矮）**：
- props `{ candles: Candle[]; trades: Trade[]; theme: "light"|"dark" }`。
- 建图 effect 逐字沿用 `WeightBacktestChart.tsx:42-122` 的蜡烛 + `buildTradeMarkerEvents`/`buildTradeTrailSegments` 渲染与 `cssVar()` 令牌解析；**去掉**左价轴净值分支；容器改 `h-[240px] w-full`；effect 依赖 `[candles, trades, theme]`；`return () => chart.remove()`。
- **P1-1**：本组件**只**经 `TradeCandleChartPanel` 的 `next/dynamic(() => import("./TradeCandleChart"), { ssr:false })` 引入；绝不静态 import；绝不把 `var(--x)` 传图库。

**`TradeCandleChartPanel.tsx`（数据 + 状态机 + 动态引入）**：
```tsx
const TradeCandleChart = dynamic(() => import("./TradeCandleChart"), { ssr:false });

export default function TradeCandleChartPanel({ instId, bar, entry, exit }:
  { instId: string; bar: string; entry: Trade; exit: Trade }) {
  const theme = useAppStore((s) => s.theme);            // P2-1
  const barSec = barSeconds(bar);                        // P2-2，aiBacktest.ts
  const span = Math.max(exit.ts - entry.ts, barSec * 4);
  const padSec = barSec * 6;
  const startTs = entry.ts - padSec;
  const endTs = exit.ts + padSec;
  const limit = Math.min(1000, Math.ceil(span / barSec) + 12 + 4);
  const q = useQuery({
    queryKey: ["trade-window-candles", instId, bar, entry.ts, exit.ts],  // P1-4 不可变缓存键
    queryFn: async () => {
      const { candles } = await api.candlesWithMeta(instId, bar, limit, endTs, startTs);
      return candles.filter((c) => c.ts >= startTs && c.ts <= endTs);     // 客户端裁剪
    },
    enabled: true,      // 本组件仅在行展开时被挂载 → 天然懒加载
    staleTime: Infinity, gcTime: 30 * 60_000,
  });
  // loading → Skeleton；error / 空 → 「该时段 K 线暂不可用」降级（不阻断本行其余信息）；
  // ok → <TradeCandleChart candles={q.data} trades={[entry, exit]} theme={theme} /> + 一行图注（开/平价、方向）
}
```
- **P1-4 落地**：本 Panel **仅在 row 展开时挂载**（`AutoTradeRoundTripRow` `open && bar` 才渲染），即天然懒加载；`enabled` 恒真但受挂载时机门控。`queryKey` 用不可变的 `(instId,bar,entry.ts,exit.ts)` + `staleTime:Infinity` → 重复展开零重拉；单开手风琴（既有 `openRowKey`）确保同时至多 1 个在飞。拉空 / 出错 → 降级文案，不抛、不阻断。
- 封装收益：`AutoTradeRoundTripRow`（共享 Chart 组件）**不**直接引入 `useQuery`/`api`/`lightweight-charts`，仅条件渲染 `<TradeCandleChartPanel>`，其它调用点零负担。

### 3.5 交易往返行放宽可展开（`AutoTradeRoundTripRow.tsx`）

新增**可选** prop `bar?: string`，其余不变；改两处（P1-3 零回归）：
```tsx
export default function AutoTradeRoundTripRow({ rt, open, onToggle, bar }:
  { rt: RoundTrip; open: boolean; onToggle: () => void; bar?: string }) {
  const tone = colorForReturn(rt.pnlUsd);
  const expandable = Boolean(bar) || Boolean(rt.exit.explain);   // 有 bar 即可展开看 K 线
  ...
  {expandable && open && (
    <div className="px-3 pb-3 pt-0.5 flex flex-col gap-2">
      {bar && <TradeCandleChartPanel instId={rt.instId} bar={bar} entry={rt.entry} exit={rt.exit} />}
      {rt.exit.explain && <TradeExplainDetail trade={rt.exit} />}
    </div>
  )}
}
```
- 不传 `bar` 的既有调用点（`AutoTradePnlPanel` 等 live-auto / paper 面板）：`expandable` 仍 `= Boolean(rt.exit.explain)`、展开仍只有 `TradeExplainDetail` → **逐字节不变**。
- 仅 AI 统计看板向 row 传 `bar` → 开启行内 K 线。

### 3.6 两页双栏改造（`AiTradeStatsLive.tsx` / `AiTradeStatsDetail.tsx`）

- **`AiTradeStatsLive`**：把现根 `flex flex-col gap-3`（`:63`）改为 §3.2 双栏骨架——
  - **左栏（`<aside>`）**：数据起始提示 + 运行观测卡 + `<AutoTradePnlSummary>`（时间筛选 + KPI，KPI 网格 `sm:grid-cols-4` → `grid-cols-2`，P2-3）+ **`<AnnualizedProjectionCard>`**（口径 = 未筛选全会话汇总 + `nowSec-group.startedTs`，P2-5）+ 持仓浮盈条。
  - **右栏（`<section>`）**：`visible.map` → `<AutoTradeRoundTripRow rt open onToggle bar={group.bar} />`（新增 `bar`），空态沿用 `WindowEmpty`。
  - `range`/`openRowKey` 等 state、`buildLedger`/`summarize` 管线**不动**；仅重排 JSX 分栏 + 传 `bar` + 插年化卡。
- **`AiTradeStatsDetail` 的 `DetailBody`**：把单列改为双栏——
  - **左栏**：返回 + 「已对账」chip + 元信息条 + `<KpiGrid>`（`grid-cols-2`）+ 「权益 / 回撤」区（`PeriodReturnBars`/`UnderwaterChart`，不变）+ `<AiStatsCard>` + **`<AnnualizedProjectionCard>`**（`returnPct=summary.returnPct`、`elapsedSec=endedTs-startedTs`、`tradeCount=summary.tradeCount`）。
  - **右栏**：「逐成员完整交易」`MemberTrades` → 每成员 row 传 `bar={detail.bar}`（P2-6）。
  - `mergeMemberEquity` 及所有既有派生**不动**。

---

## 4. 文件 / 模块变更计划（File / Module Change Plan）

| 文件 | 动作 | 一句话意图 |
|---|---|---|
| `apps/web/components/Agent/AiTradeStatsPanel.tsx` | 改 | Modal 宽 `max-w-3xl`→`max-w-[60vw] max-md:max-w-[94vw]`；body `flex-col overflow-auto`→`flex min-h-0 flex-col`（滚动下放两栏）|
| `apps/web/components/Agent/aiTradeStats/AiTradeStatsLive.tsx` | 改 | 左右双栏重排；左栏插 `<AnnualizedProjectionCard>`；右栏往返行传 `bar={group.bar}`；KPI 网格收敛 |
| `apps/web/components/Agent/aiTradeStats/AiTradeStatsDetail.tsx` | 改 | `DetailBody` 左右双栏；左栏插 `<AnnualizedProjectionCard>`；`MemberTrades` 行传 `bar={detail.bar}` |
| `apps/web/components/Agent/aiTradeStats/AnnualizedProjectionCard.tsx` | 新 | 预计年化卡：消费 `projectAnnualizedReturn`，样本不足 / 出错诚实降级 + 免责声明 |
| `apps/web/lib/annualized.ts` | 新 | `projectAnnualizedReturn` 纯函数 + 最小样本门禁 / 线性外推 / 安全降级 |
| `apps/web/components/Chart/AutoTradeRoundTripRow.tsx` | 改 | 新增可选 `bar?`：放宽 `expandable` + 展开渲染 `<TradeCandleChartPanel>`（零回归其它调用点）|
| `apps/web/components/Chart/TradeCandleChartPanel.tsx` | 新 | 行内 K 线数据层：`useQuery` 窗口拉取 + 状态机（loading/error/empty/ok）+ 动态引入纯图 |
| `apps/web/components/Chart/TradeCandleChart.tsx` | 新 | 纯 slim 蜡烛图（镜像 `WeightBacktestChart`：candles+买卖 markers+开→平连线；`cssVar`；`ssr:false`）|
| `apps/web/lib/annualized.test.ts` | 新 | 年化纯函数：门禁 / 线性口径 / `elapsed<=0`·`wipeout`·`too-few` 降级、绝不抛 |
| `apps/web/components/Chart/TradeCandleChartPanel.test.tsx` | 新 | 行内 K 线状态机（mock 图组件 + `api.candlesWithMeta`）：展开触发拉取 / loading / 空降级 / 不可变缓存键 |
| `apps/web/components/Agent/AiTradeStatsPanel.test.tsx` | 改 | 扩测：双栏渲染、年化样本不足降级、往返行展开→挂载 K 线面板（图组件 mock）|

> 说明：**零后端改动**（`/api/candles` 已满足需求③）、**零 DB 迁移**、**零 store 形状 / 持久化变更**、**零新增依赖**（`lightweight-charts`/`@tanstack/react-query` 均在册）。新文件均控制在 CLAUDE.md 阈值内（≤800 行 / 方法 ≤50 行 / 形参 ≤5 / 嵌套 ≤4）；`TradeCandleChartPanel`/`TradeCandleChart` 拆分以隔离 `ssr:false` 动态引入边界。

---

## 5. 接口设计（Interface Design）

### 5.1 REST（复用既有，无新增）

| Method | Path | Query | 200 | 用途 |
|---|---|---|---|---|
| GET | `/api/candles`（前端经 `/api/py/candles`） | `instId`、`bar`、`limit`(1..1000)、`before`(秒)、`after`(秒) | `[{ts,o,h,l,c,vol}]`（升序、秒）| 需求③ 窗口 K 线（既有端点，零改）|

前端调用：`api.candlesWithMeta(instId, bar, limit, before=endTs, after=startTs)`（`lib/api.ts:114`）。历史列表 / 详情端点（`/ai-trade/history*`）沿用既有。

### 5.2 组件 props 契约（新 / 改）

- `AnnualizedProjectionCard`：`{ returnPct: number; elapsedSec: number; tradeCount: number }`。
- `TradeCandleChartPanel`：`{ instId: string; bar: string; entry: Trade; exit: Trade }`。
- `TradeCandleChart`：`{ candles: Candle[]; trades: Trade[]; theme: "light"|"dark" }`。
- `AutoTradeRoundTripRow`：`{ rt; open; onToggle; bar?: string }`（**新增可选** `bar`；不传即旧行为）。
- `projectAnnualizedReturn(params) → AnnualizedProjection`（§3.3）。

### 5.3 WS / CLI

无新增。K 线为一次性只读拉取；「当前」页仍读既有 `store.aiTradeGroup`。

---

## 6. 数据模型（Data Model）

- **DB**：完全不变（无迁移、无填列、无加法写）。
- **wire / 运行期形状**：`Candle`（`{ts,o,h,l,c,vol,confirm?}`）、`RoundTrip`（§2.1）、`AiTradeHistoryDetailOut.bar` / `AiTradeGroupState.bar` 均既有；新增类型仅 `AnnualizedProjection`（组件 / lib 局部，不入 store）。
- **单一口径**：年化 `returnPct` 与看板「总收益率」同源（`summarizeRoundTrips` / backend `summary`，分母 `totalBudgetUsd`）；K 线蜡烛为真实 OKX 行情、买卖点位为真实落库成交（`entry/exit.price`、`entry/exit.ts`）。
- **P2-7 单位**：`candle.ts`、`entry.ts`/`exit.ts`、`openedTs`/`closedTs`、`startedTs`、`before`/`after` **全为秒**；lightweight-charts `UTCTimestamp` 按秒；全程不混毫秒。

---

## 7. 测试与验收标准（Testing & Acceptance）

### 7.1 前端单测（`NODE_ENV=test`；`tsc --noEmit` 为类型门；既有用例零回归）

- **`annualized.test.ts`（纯函数）**：`elapsedSec<=0`→`no-elapsed`；`elapsedSec<minElapsed`→`too-short`（不出数字）；`tradeCount<minTrades`→`too-few-trades`；`1+returnPct<=0`→`wipeout`；满足门禁 → `annualizedPct === returnPct*YEAR/elapsed`（线性、逐值）；绝不抛。
- **`TradeCandleChartPanel.test.tsx`（P2-8：mock `./TradeCandleChart` 与 `api.candlesWithMeta`）**：挂载即触发一次拉取（`queryKey` 含 `instId/bar/entry.ts/exit.ts`）；loading 显 Skeleton；拉空 / reject → 「该时段 K 线暂不可用」降级、不抛；成功 → 以裁剪后的 `candles` + `trades=[entry,exit]` 挂 mock 图；同参重挂命中缓存不重复请求。
- **`AiTradeStatsPanel.test.tsx`（扩）**：Modal 携 `max-w-[60vw]`；「当前」/「详情」双栏（左栏含 KPI + 年化卡、右栏含往返列表）；年化样本不足 → 卡片显降级文案而非数字；往返行传了 `bar` → 可展开且展开挂载 K 线面板（图组件 mock）；不传 `bar` 的既有调用点行为不变（回归用例）。
- **纯函数复用零回归**：`autoTradePnl`/`tradeTrails`/`mergeMemberEquity` 既有测试保持绿。

### 7.2 验收标准（Definition of Done）

1. AI 自动交易统计看板 Modal 宽约屏宽 60%（窄屏回退近满屏），呈**左右双栏**：左栏统计数据、右栏交易记录（「当前」页与「历史·详情」页同构）。
2. 左栏显示**预计年化收益**：满足最小样本（≥6h 且 ≥3 笔完整交易）时按已实现收益线性外推并明确标注「估算 · 基于 X 样本」；否则显式降级「样本不足以估算年化」——**绝不**对极短样本给天文数字。
3. 右栏每条完整交易往返可展开，展开后在该标的 / 周期 / 往返窗口上渲染 K 线，叠加 BUY/SELL/CLOSE markers 与开→平连线，并保留平仓触发原因；K 线拉空 / 出错优雅降级。
4. 其它复用 `AutoTradeRoundTripRow` 的面板（live-auto / paper）行为零回归。
5. 零后端改动、零 DB 迁移、零新依赖；全部数字与点位由真实行情 / 真实成交派生；`tsc --noEmit` 与 `next build` 通过、前端测试全绿。

---

## 8. 关键时序（Sequence）

```
展开一条交易记录（右栏）
  用户点行 → AutoTradeRoundTripRow(open=true, bar 存在) 挂载 <TradeCandleChartPanel>
    → useQuery(["trade-window-candles",instId,bar,entry.ts,exit.ts])（懒加载、单开手风琴至多 1 在飞）
       → api.candlesWithMeta(instId,bar,limit,before=exit.ts+pad,after=entry.ts-pad) → GET /api/candles（公共、节流、429 安全）
       → 客户端裁剪 [entry.ts-pad, exit.ts+pad]
    → 有数据：<TradeCandleChart candles trades=[entry,exit] theme>（蜡烛+BUY/SELL/CLOSE+开→平连线）
    → 拉空/出错：「该时段 K 线暂不可用」（不阻断该行 pnl / 触发原因）
  再次展开同一行 → staleTime:Infinity 命中缓存，零重拉

预计年化（左栏）
  Live: returnPct=未筛选全会话汇总, elapsedSec=now-startedTs, tradeCount=全会话往返数
  History: returnPct=summary.returnPct, elapsedSec=endedTs-startedTs, tradeCount=summary.tradeCount
    → projectAnnualizedReturn → ok? 大字年化+免责+样本基数 : 「样本不足以估算年化」
```

---

## 9. 范围边界（Scope）

- **范围内**：看板 Modal 加宽 60vw + 左右双栏（「当前」与「历史·详情」两页）；左栏预计年化（线性外推 + 诚实门禁）；右栏交易记录行内 K 线（复用 `WeightBacktestChart` 范式 + 既有 `/api/candles`）。
- **已知限制（对用户诚实）**：
  - 预计年化为**线性外推估算**，仅计已实现收益、不含浮动盈亏，样本越短越不可靠；短样本（<6h 或 <3 笔）直接不估。
  - 行内 K 线依赖 OKX `history-candles`；**极旧**窗口若超出其保留期会拉空 → 显式降级，不伪造蜡烛。
  - K 线周期用组级 `bar`（AI-trade 分组同组同周期）。
- **范围外（明确不做）**：改任何后端 / DB / 落库 / 数据口径；把 K 线塞进「历史·列表」页（列表是会话导航，不分栏）；为 K 线新建缓存表 / 预取；复利年化 / 蒙特卡洛等更复杂的收益预测模型（本期只做线性外推 + 门禁）；改动 `WeightBacktestChart` / `CandleChart` / 其它复用组件的对外行为。

---

## 10. 风险与缓解（Risks & Mitigations）

| 编号 | 风险 | 缓解 |
|---|---|---|
| R-1 | lightweight-charts SSR / `var()` 崩 | **P1-1**：纯图组件 `ssr:false` 动态引入 + `cssVar()` 解析令牌 + effect 依赖 `theme`，逐字镜像 `WeightBacktestChart`（已在产验证）|
| R-2 | 短样本年化误导 | **P1-2**：最小样本门禁 + 线性外推 + 免责声明 + 样本基数 + `wipeout`/`no-elapsed` 降级；口径与窗口解耦（P2-5）|
| R-3 | 放宽可展开回归其它面板 | **P1-3**：可选 `bar?` 门控，仅统计看板传 `bar`；其它调用点行为逐字节不变；补回归用例 |
| R-4 | 旧窗口 K 线拉取成本 / 拉空 | **P1-4**：懒加载（展开才挂载）+ 单开手风琴 + 不可变缓存键 `staleTime:Infinity` + 拉空 / 出错降级；公共路径后端已节流 + 429 退避 |
| R-5 | 双栏 KPI 拥挤 / 嵌套双滚动 | **P2-3**：左栏 KPI `grid-cols-2`；两栏各自 `overflow-y-auto`、去外层单滚动；`min-w-0` 防溢出 |
| R-6 | 窄屏 60vw 过挤 | **P2-4**：`max-md:max-w-[94vw]` + 双栏 `max-md:flex-col` 堆叠 |
| R-7 | 主题 / 单位不一致 | **P2-1** 主题走 `store.theme`；**P2-7** 全链路秒、`UTCTimestamp` 秒 |
| R-8 | jsdom 跑不动图库 | **P2-8**：纯逻辑纯函数测；含图测 mock `TradeCandleChart` + `api`，只断状态机 |

---

## 11. 实施过程发现的方案缺陷（Implementation Deviations）

> 预留给 Subtask #2（代码开发）。如实现中发现方案缺陷或需偏离，在此记录原因与回退条件。

**已由 Subtask #1 评审核实解决（源码已确认，开发期无需再查）**：
- ✅ Modal `className` 末位覆盖基类 `max-w-2xl`，`max-w-[60vw]` 一行即改宽（`ui/index.tsx:324-335`；本仓 arbitrary vw 已广泛使用）。
- ✅ K 线全要件已在手：`WeightBacktestChart` 渲染范式、`tradeTrails` 纯构造、`api.candlesWithMeta` 窗口拉取、`barSeconds`、`store.theme`、`RoundTrip.instId/entry/exit`、`AiTradeGroupState.bar`/`AiTradeHistoryDetailOut.bar` 均逐一核实存在且签名吻合——**零后端改动**成立。
- ✅ `AutoTradeRoundTripRow` 现 `expandable=Boolean(rt.exit.explain)`（`:21`），可选 `bar?` 门控放宽为零回归方案（P1-3）。

**仍待开发节点核对（评审无法从静态源码定论）——Subtask #2 已核实**：
- ✅ `store.theme` 选择器名确认为 `useAppStore((s) => s.theme)`，与 `WeightBacktestPanel.tsx:36` / `ChartWorkspace` 同源（§3.4 / P2-1）。`TradeCandleChartPanel` 已按此取用。
- ✅ `history-candles` 旧窗口保留期：不硬保证，`TradeCandleChartPanel` 以「拉空 / reject → 该时段 K 线暂不可用」优雅降级兜底（§10 R-4），已补状态机单测。
- ✅ 图库 / 动态引入 mock 对齐 `WeightBacktestPanel.test.tsx` 范式（`vi.mock("next/dynamic")` stub + 部分 mock `api.candlesWithMeta`），非 `CandleChart.test.tsx` 的整库打桩——因本组件经 `next/dynamic` 引入，stub 动态边界更贴切（§7 / P2-8）。

---

## Subtask #2（代码开发）实施记录与偏离

**实施结果**：§4 变更计划 11 个文件全部落地（5 改 + 6 新），`tsc --noEmit` 对本特性文件零错误（仓库仅存 `WeightBatchArena.test.tsx` 一处**既有** tsc 错误，非本特性引入），`apps/web` 全量前端测试 **116 文件 / 1130 用例全绿**（含本特性新增 8+5+6 用例），4 放行条件（P1-1 图库 ssr:false + cssVar + theme 依赖、P1-2 年化诚实门禁、P1-3 可选 `bar?` 零回归、P1-4 懒加载 + 不可变缓存键 + 降级）逐条落实。

**唯一偏离（P2-3 局部收窄，原因=零回归优先）**：
- **方案缺陷**：P2-3 要求「左栏 KPI 由 `sm:grid-cols-4` 收敛为 `grid-cols-2`」。但**当前页**左栏的 KPI 概览由**共享组件** `components/Chart/AutoTradePnlSummary.tsx`（`:97` `grid ... sm:grid-cols-4`）渲染，该组件亦被 `AutoTradePnlPanel`（live-auto / paper 面板）复用，且**不在 §4 变更计划内**。若在此收敛其网格，会回归其它调用点的既有版式——与 P1-3「零回归」及《范围外·不改复用组件对外行为》直接冲突。
- **采取的修正**：**不改**共享的 `AutoTradePnlSummary`；仅对 Detail 页**局部私有**的 `KpiGrid`（`AiTradeStatsDetail.tsx` 内联组件，无外部复用）执行 `sm:grid-cols-4 → grid-cols-2` 收敛。当前页 KPI 沿用 `sm:grid-cols-4`——在 40% 左栏（60vw × 0.4 ≈ 24vw）下 4 列紧凑但可读（单元格为 `text-[10px]`/`text-sm` 小卡），属可接受的 P2 观感，不构成正确性问题。
- **回退条件**：若后续确需当前页左栏也收敛，应给 `AutoTradePnlSummary` 增**可选** prop（如 `kpiCols?: 2 | 4`，默认 4 保持既有调用点不变）而非直接改其网格类，以维持零回归——此项独立于本特性，留待专门迭代。

---

## 12. 评审说明（Review — 详见文首《评审记录》）

> Subtask #1（方案评审与修复）已完成：因上游设计节点零产出，评审节点在逐文件核对真实源码的基础上补齐设计正文并同轮评审，问题清单（4 P1 + 8 P2，无 P0）与就地修复均见**文首《评审记录》**，全部 P1/P2 已在正文对应小节修复 / 接地，版本为 **v2**。评审结论见下。

---

## 评审结论（Review Verdict）

**结论：有条件通过（Approved with Conditions）**

### 评审判断
三条需求的**核心工程判断经全量源码核实成立**：需求① 仅需一行 `className` 改宽 + 两页 JSX 分栏（既有 Modal / 双栏范式充分）；需求② 是纯前端派生（`startedTs`+已实现收益+`totalBudgetUsd` 全可达）；需求③ **零后端改动**——`GET /api/candles` 已支持任意历史窗口、`WeightBacktestChart`+`tradeTrails` 已把 `Candle[]+Trade[]` 渲染为蜡烛+买卖 markers+开→平连线、`RoundTrip`+`bar` 已在渲染点齐备。整体路线**可行、右尺寸（未过度设计、未欠规格）、与 CLAUDE.md 及既有约定一致**（复用优先、零迁移、显式降级、单一口径），可进入开发。

发现的 4 个 P1——(P1-1) 图库 SSR/`var()`、(P1-2) 短样本年化的诚实性红线、(P1-3) 放宽可展开的共享组件回归、(P1-4) 旧窗口 K 线成本与拉空——与 8 个 P2 **均已在本 v2 正文就地修复 / 接地**，文档层面**无遗留 P0/P1**。

### 放行条件（开发节点必须落实）
1. **图库边界（P1-1）**：K 线纯图组件必须 `ssr:false` 动态引入 + `cssVar()` 令牌解析 + `theme` 入 effect 依赖，绝不静态 import、绝不把 `var(--x)` 传图库。
2. **年化诚实护栏（P1-2 / P2-5）**：最小样本门禁（≥6h 且 ≥3 笔）+ 线性外推 + 免责声明 + 样本基数 + 与时间筛选解耦 + 仅已实现口径；样本不足即显式降级，严禁天文数字。
3. **零回归门控（P1-3）**：`AutoTradeRoundTripRow` 经**可选 `bar?`** 放宽可展开，仅统计看板传 `bar`；补「不传 `bar` 行为不变」的回归用例。
4. **K 线拉取韧性（P1-4）**：懒加载 + 单开手风琴 + 不可变缓存键 `staleTime:Infinity` + 拉空 / 出错优雅降级；不改任何后端。

满足以上 4 条即视为完全落地本方案；其余 P2 已在正文澄清，属实现须知而非阻塞项。
