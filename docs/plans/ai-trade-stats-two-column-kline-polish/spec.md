# AI 自动交易统计看板 · 迭代 2 打磨（左栏密度 / K 线点位增强 / 加载韧性 / 年化可信度）开发方案

> 版本：**v2**（方案设计 v1 → Subtask #1 评审修订，迭代 2/2）
> 作者：senior software engineer（Anthropic 工程团队，本任务的方案架构师）
> 特性目录：`docs/plans/ai-trade-stats-two-column-kline-polish/`
> 适用仓库：`M:/takoAI/AragonTrading`（monorepo：`apps/web` Next.js 14 + `services/quant` FastAPI + SQLite）
>
> **基座（本迭代在其之上做增量）**：迭代 1 已把「AI 自动交易统计看板」加宽为 60vw 左右双栏、左栏加「预计年化」、右栏交易记录展开显示 K 线，并**已合并** `commit 0fc59ae`（11 文件、`tsc --noEmit` 对本特性零错误、`apps/web` 全量测试 116 文件 / 1130 用例全绿）。基座设计见 `docs/plans/ai-trade-stats-two-column-and-kline/spec.md`（v2，有条件通过），用户三条硬需求（① 60% 宽 + 左右双栏、② 左栏预计年化、③ 右栏展开 K 线含买卖点位）**均已达成**。
> **本迭代目标**：不重复已完成工作，只承接需求文案里与「基础功能」并列的**质量硬指标**——「相关界面美观优雅顶级设计，符合人机交互最佳实践，功能完善可靠稳健」——做一轮**打磨与加固**，并**闭合基座方案自己明确留给「专门迭代」的一处欠账**（基座 spec §11「回退条件」：为 `AutoTradePnlSummary` 增可选 `kpiCols` prop 以收敛窄左栏 KPI）。

---

## 评审记录（Review Notes · Subtask #1 · Anthropic 工程团队 senior reviewer）

> 评审基线：v1 方案 + 迭代 1 已合并源码（`commit 0fc59ae`）逐文件复核（`AutoTradePnlSummary.tsx`、`AiTradeStatsLive.tsx`、`TradeCandleChart.tsx`、`TradeCandleChartPanel.tsx`、`annualized.ts`、`AnnualizedProjectionCard.tsx`、`lib/format.ts`、`lib/types.ts`、基座 `ai-trade-stats-two-column-and-kline/spec.md`、安装的 `lightweight-charts@4.2` d.ts）。维度：**可行性 / 完整性 / 一致性 / 右尺寸**。
> 结论速览：**0 P0 · 2 P1（均已在正文修正并升版 v2）· 5 P2（4 项已在正文落实，1 项记录取舍）**。方案整体质量高、grounding 扎实（每处以真实符号锚定），四项增量均为纯前端、零后端、零迁移、默认等价旧行为，右尺寸得当。

**已核验通过（无需改动）**
- **E1 前提属实**：基座 `ai-trade-stats-two-column-and-kline/spec.md:375`（§11 回退条件）原文即建议「给 `AutoTradePnlSummary` 增可选 prop（如 `kpiCols?: 2 | 4`，默认 4 保持既有调用点不变）……留待专门迭代」。`AutoTradePnlSummary.tsx:97` 现为 `grid grid-cols-2 gap-2 sm:grid-cols-4`、`clsx` 已 import（`:3`）、`AiTradeStatsLive.tsx:94-100` 调用点确认——E1 可行且与基座一致。
- **E2 API 可行**：`ISeriesApi.createPriceLine(options: CreatePriceLineOptions): IPriceLine`，其中 `CreatePriceLineOptions = Partial<PriceLineOptions> & Pick<PriceLineOptions,"price">`（已核对安装的 v4.2 d.ts `:1898/:3476`）——传部分字段 `{price,color,lineWidth,lineStyle,axisLabelVisible,title}` **零 tsc 错误**；参考线随 `chart.remove()`（`:101`）销毁、零泄漏，R-2 成立。
- **E2-b 依赖齐备**：`fmtTime`/`colorForReturn`/`signed`/`fmtPrice` **均已从 `@/lib/format` 导出**（已核对）——§10 的「⏳待开发核对」项**可结案为已确认**。
- **E4 算法正确**：`annualizedConfidence` 对 §7.1 全部用例（含边界 `(30d,30笔)→high`、`(7d,10笔)→medium` 与「取较低者」的 `(2d,40笔)→low`/`(45d,5笔)→low`）验算一致、绝不抛。
- **测试就位**：三个待扩测文件 `annualized.test.ts`、`TradeCandleChartPanel.test.tsx`、`AiTradeStatsPanel.test.tsx` **均已存在**（§4 均为「改」而非「新建」，属实）。

**P1（阻断项，已在正文修正 → v2）**
- **P1-1 一致性 / 右尺寸 ｜ §3.3 E2-a 内联会使建图 effect 超 CLAUDE.md「方法 ≤50 行」阈值，且 §4 对此作了不实的合规声明。** `TradeCandleChart.tsx` 建图 `useEffect`（`:41-102`）**现已约 60 行**（迭代 1 既有欠账），E2-a 再内联约 20 行会到 ~80 行；而 §4（`:254`）声称「所有改动文件仍在 CLAUDE.md 阈值内（…方法 ≤50 行…）」——该声明无法成立，下游若照抄会提交超阈方法却以为合规。**修正**：把 E2-a 参考线绘制抽为**模块级 helper** `drawTradeReferenceLines(candleSeries, trades)`（镜像既有 `markerForEvent`/`cssVar` 的模块级私有函数范式），effect 内仅保留一行调用；§4 声明改为如实说明。
- **P1-2 一致性 / HCI ｜ §3.3 E2-a 未关闭蜡烛系列默认末值价线，叠加后图上出现三条水平线，稀释「点位醒目」这一本迭代硬指标。** `addCandlestickSeries`（`:60-67`）未设 `priceLineVisible:false`，蜡烛系列**默认在最后收盘价画一条水平价线**；E2-a 再加开 / 平两条参考线 → 图上共三条水平线，与需求「顶级设计 / 点位醒目 / HCI 最佳实践」相悖。**修正**：E2-a 引入参考线时同步给蜡烛系列设 `priceLineVisible:false`，令图上恰好只剩开仓 / 平仓两条**有语义**的参考线。

**P2（改进项）**
- **P2-1 grounding 精度 ｜ E2 把 `Trade.pnl` 当可空处理，与真实契约不符（已随 P1-1 一并修正）。** `types.ts:307` `pnl: number`（必填）、`side: "long"|"short"|"close"`；v1 的 `trades.find((t)=>t!==entryTrade && t.pnl!==undefined)` 与 `exit.pnl ?? 0`/`(exitTrade.pnl ?? 0)` 是**恒真 / 死兜底**且误导（暗示 pnl 可空）。apps/web **无 `.eslintrc`**、无类型感知 lint 规则，故不炸 `next build`，但应更正——已随 P1-1 的 helper 重写改为类型正确的解构 `const [entryTrade, exitTrade] = trades;` 与直接 `exitTrade.pnl >= 0`。
- **P2-2 完整性 / 措辞 ｜「逐字节不变」为过度声明（已在正文更正）。** E1 用 `clsx` 取代静态 className 后，未传 `kpiCols` 的调用点渲染出的类名字符串顺序改变（`grid gap-2 grid-cols-2 sm:grid-cols-4` vs 原字面 `grid grid-cols-2 gap-2 sm:grid-cols-4`），**同一组类、Tailwind 顺序无关、视觉零差异**，但非字面「逐字节不变」。已核对 `AutoTradePnlPanel.test.tsx` **无精确 className 断言**，不破测。正文「逐字节不变」已改为「类名等价（同组类、顺序无关、视觉零差异）」并加一条测试守则（勿写精确 className 字符串断言）。
- **P2-3 完整性 ｜ E3-b 截断判定的成因归因是近似的（已在正文补注）。** `candles[0].ts > entry.ts` 在「行情本就缺开仓前数据（低流动性 / 跳空）」时亦会触发，未必是条数上限截断；但提示语「仅显示临近平仓的一段」在任何「首根晚于开仓」情形下**都成立**，故不误导，无需改判定逻辑。
- **P2-4 精度 ｜ E4 徽标的 flex 包裹未在片段中显式给出（已在正文补出）。** §3.5 述「把大字包进 flex 行」但代码片段只给徽标 span；正文已补出外层 `<div className="flex items-center gap-2">` 包裹，消除歧义。
- **P2-5 HCI ｜ E2-a 参考线配色混用「方向」与「盈亏」语义（记录取舍，本迭代保留）。** 连线按方向着色（long 绿 / short 红），平仓参考线按盈亏着色（盈绿 / 亏红），开仓参考线中性灰。三者并存可辨但语义略杂；**本迭代保留**（平仓盈亏着色对「平仓价」参考线直观、与 CLOSE marker 的 pnl 语义一致），仅记录取舍——未来若观感偏杂，可统一为中性参考线，以固定输出锁测。

---

## 1. 概述（Overview）

迭代 1 已经把三条功能需求完整落地并合入主干（`commit 0fc59ae`）：看板 Modal 加宽到 `max-w-[60vw]`、「当前」与「历史·详情」两页均改为左右双栏、左栏渲染 `AnnualizedProjectionCard`（线性外推 + 最小样本诚实门禁）、右栏每条完整交易往返可展开并懒加载渲染 slim K 线（复用 `WeightBacktestChart` 范式，叠加 BUY/SELL/CLOSE markers 与开→平连线）。因此**本迭代不再触碰这三条主干路径的骨架**，而是承接用户需求原文中与功能并列的第二类要求：界面**美观优雅、符合人机交互最佳实践、功能完善可靠稳健**。这是一类质量指标，落到工程上就是一批**小而精、零回归、纯前端**的打磨点。

本迭代锁定四项增量，全部围绕「让已交付的看板更好看、更好用、更抗故障」，且**没有一项改变数据口径、后端、DB、store 形状或对外契约**：**（E1）左栏 KPI 密度修复**——把基座 spec §11 明确记录、并主动留给「专门迭代」的欠账落地：给共享组件 `AutoTradePnlSummary` 增一个**可选** `kpiCols?: 2 | 4`（默认 4），仅 AI 统计看板的窄左栏传 `2`，让 40% 宽（≈24vw）的左栏 KPI 从四列拥挤收敛为两列整齐，其它复用该组件的 live-auto / paper 面板**类名等价（同组类、顺序无关、视觉零差异）**（P2-2：`clsx` 会重排类名字符串，非字面逐字节相同，但 Tailwind 顺序无关、无既有精确断言，故零回归）。**（E2）K 线「点位 + 相应信息」增强**——需求③原文要求「用 K 线图的方式显示对应这次交易记录的买卖情况和点位，**以及相应的信息**」；当前 K 线已有 markers 与连线，但「点位」不够醒目、「相应的信息」只有一行开 / 平价。本迭代在 K 线上叠加**开仓价 / 平仓价的水平参考线**（`createPriceLine`，直观标出两个价位），并把图下方注脚升级为一条紧凑的**交易事实条**（方向、开仓价@时间、平仓价@时间、持仓时长、盈亏 USDT + 收益率）。**（E3）K 线加载韧性**——当前拉空 / 出错只给一句静态降级文案且 `retry:false`；本迭代补一个**「重试」按钮**（手动 `refetch`，不改自动不重试的克制策略），并对「窗口过长被行情条数上限截断」这一边界补一句诚实提示。**（E4）年化可信度提示**——线性外推即便过了 6h/3 笔门禁，短样本仍可能外推出很大的数（如 6h 内 +5% → 年化 +7300%）；本迭代在年化卡出数字时并排一枚**样本可信度徽标（低 / 中 / 高）**，由纯函数据经历天数与交易笔数派生，进一步强化 CLAUDE.md「诚实、显式降级」原则。

核心策略延续基座：**最大化复用、零后端改动、零迁移、零新依赖、零回归**。E1 是「加一个默认保持旧行为的可选 prop」的经典零回归手法；E2 只改 K 线专用的 `TradeCandleChart`（迭代 1 新建、非共享镜像组件）与其数据层 `TradeCandleChartPanel`，不碰被复用的 `WeightBacktestChart`；E3 只在既有状态机里加一个按钮与一条判断；E4 是一个纯函数 + 一枚徽标。所有数字与点位继续由**真实 OKX 行情**与**真实落库成交**派生，年化继续显式标注为估算并对短样本诚实降级——不伪造任何数据。

---

## 2. 现状与关键代码路径（Grounding）

> 以下结论均经**迭代 1 已合并源码**逐文件核对（`git show 0fc59ae`）。行号以核对时为准，实现时以符号为锚二次确认。

### 2.1 基座已交付组件（本迭代的改造对象）

- **面板壳**：`apps/web/components/Agent/AiTradeStatsPanel.tsx`——Modal 已是 `className="max-w-[60vw] max-md:max-w-[94vw]"`，body 已是 `flex min-h-0 flex-col`（滚动下放两栏）。**本迭代不改此文件**。
- **「当前」页**：`apps/web/components/Agent/aiTradeStats/AiTradeStatsLive.tsx`（196 行）。已是双栏：`:80` `<div className="flex min-h-0 flex-1 gap-4 max-md:flex-col">`；左栏 `<aside className="... w-[40%] ...">`（`:81`）依次渲染 数据起始提示 → 运行观测卡 → `<AutoTradePnlSummary ... summary={summary}/>`（`:94-100`）→ `<AnnualizedProjectionCard returnPct={fullSummary.totalReturnPct} elapsedSec={nowSec-group.startedTs} tradeCount={fullSummary.count}/>`（`:101-105`）→ 持仓浮盈条；右栏 `<section>`（`:119`）`visible.map` → `<AutoTradeRoundTripRow ... bar={group.bar}/>`。**E1 改此文件一处**：向 `AutoTradePnlSummary` 传 `kpiCols={2}`。
- **「历史·详情」页**：`apps/web/components/Agent/aiTradeStats/AiTradeStatsDetail.tsx`（321 行）。`DetailBody`（`:54`）已双栏；左栏含局部 `KpiGrid`（`:129`，已是 `grid-cols-2`，无外部复用，**无需改**）+ `<AnnualizedProjectionCard/>`（`:109-113`）；右栏 `MemberTrades`（`:212`）逐成员 `<AutoTradeRoundTripRow ... bar={bar}/>`。**本迭代不改此文件**（其 KpiGrid 已收敛；E1 的欠账只存在于「当前」页的共享 `AutoTradePnlSummary`）。
- **KPI 概览（共享组件，E1 改造对象）**：`apps/web/components/Chart/AutoTradePnlSummary.tsx`（155 行）。`:97` `<div className="grid grid-cols-2 gap-2 sm:grid-cols-4">`——次级 KPI（完整交易 / 胜率 / 最大盈 / 最大亏）在 `sm` 断点起为四列。该组件被 **AI 统计看板**（`AiTradeStatsLive`）与 **live-auto / paper 面板**（`AutoTradePnlPanel`）共享，故**不能**直接改其网格类（会回归其它调用点，基座 spec §11 已明确此约束）。
- **年化卡（E4 改造对象）**：`apps/web/components/Agent/aiTradeStats/AnnualizedProjectionCard.tsx`（76 行）。`ok===true` 分支（`:40-53`）出大字 `fmtPct(annualizedPct)` + 免责/样本基数注脚。**E4 在此并排一枚可信度徽标**。
- **年化纯函数（E4 改造对象）**：`apps/web/lib/annualized.ts`（60 行）。导出 `projectAnnualizedReturn` + `MIN_ELAPSED_SEC`(6h) + `MIN_TRADES`(3)。**E4 在此增一个纯函数 `annualizedConfidence`**。
- **交易往返行（E2 间接受益，本迭代不改其对外行为）**：`apps/web/components/Chart/AutoTradeRoundTripRow.tsx`（91 行）。`:28` `expandable = Boolean(bar) || Boolean(rt.exit.explain)`；`:64-71` 展开态渲染 `<TradeCandleChartPanel .../>` + `<TradeExplainDetail/>`。**本迭代不改此文件对外契约**（E2 全部落在被它渲染的 `TradeCandleChartPanel` / `TradeCandleChart` 内部）。
- **行内 K 线数据层（E2 / E3 改造对象）**：`apps/web/components/Chart/TradeCandleChartPanel.tsx`（87 行）。`useQuery({queryKey:["trade-window-candles",instId,bar,entry.ts,exit.ts], staleTime:Infinity, retry:false})`（`:42-53`）；`:57-59` loading → `Skeleton`；`:60-66` error / 空 → 静态降级文案 `<p>`；`:68-86` ok → `<TradeCandleChart/>` + 一行注脚（仅方向 + 开 / 平价）。**E2 升级注脚为交易事实条；E3 给 error 分支加「重试」按钮 + 窗口截断提示**。
- **行内 K 线纯图（E2 改造对象）**：`apps/web/components/Chart/TradeCandleChart.tsx`（142 行）。`useEffect`（`:41-102`）内 `createChart` → `addCandlestickSeries` → `candleSeries.setData(...)` → `buildTradeMarkerEvents` → `setMarkers` → `buildTradeTrailSegments` → `addLineSeries` 两点连线 → `fitContent` → `return () => chart.remove()`；依赖 `[candles, trades, theme]`；`cssVar()`（`:107-110`）解析令牌；容器 `h-[240px] w-full`。**E2 在 `candleSeries` 上叠加两条 `createPriceLine`（开仓价 / 平仓价）**。

### 2.2 复用基础设施（零改动确认）

- **图库**：`lightweight-charts ^4.2.0`（`package.json`）。`ISeriesApi.createPriceLine(options)` 为 v4 公开 API（options：`price`、`color`、`lineWidth`、`lineStyle`、`axisLabelVisible`、`title`），返回 `IPriceLine`；随 `series` / `chart` 移除自动销毁——本组件每次 effect 重跑都整体 `chart.remove()`，故**无需手工 `removePriceLine`**，零泄漏。
- **格式化**：`apps/web/lib/format.ts` 已导出 `fmtPrice`、`fmtPct`、`fmtTime`、`signed`、`colorForReturn`（均在基座组件已 import 使用）。E2 事实条、E4 徽标复用之，无新增。
- **UI 原子**：`@/components/ui` 已导出 `Skeleton`、`Button`、`Badge`（`AiTradeStatsDetail` 已用 `Button`/`Badge`、`TradeCandleChartPanel` 已用 `Skeleton`）。E3 重试按钮复用 `Button`，E4 徽标复用 `Badge` 或裸 `<span>`（见 §3.5）。
- **数据源**：`GET /api/candles`（公共 REST，已节流 + 429 退避）与 `api.candlesWithMeta` 不变。E1/E2/E3/E4 **均不新增任何请求**。

---

## 3. 技术设计（Technical Design）

### 3.1 架构总览（纯前端打磨，后端 / DB / store 零改动）

```
后端 / DB / store：不改任何文件（沿用 commit 0fc59ae 的一切端点、口径、形状）

前端（apps/web）
  components/Chart/AutoTradePnlSummary.tsx        （改·E1）+可选 kpiCols?: 2|4（默认 4）；网格类按 prop 派生
  components/Agent/aiTradeStats/AiTradeStatsLive.tsx（改·E1）左栏 <AutoTradePnlSummary> 传 kpiCols={2}
  components/Chart/TradeCandleChart.tsx           （改·E2）candleSeries 叠加开 / 平价水平参考线（模块级 helper 抽出 · 关默认末值价线）
  components/Chart/TradeCandleChartPanel.tsx      （改·E2/E3）注脚升级为交易事实条；error 分支加「重试」+ 截断提示
  lib/annualized.ts                               （改·E4）+ 纯函数 annualizedConfidence(elapsedSec, tradeCount)
  components/Agent/aiTradeStats/AnnualizedProjectionCard.tsx（改·E4）ok 分支并排样本可信度徽标
```

改动集中在 5 个文件（其中 `AiTradeStatsLive` 仅 1 行传参）。全部为**加法 / 可选**改动，默认路径保持迭代 1 行为。

### 3.2 E1 — 左栏 KPI 密度修复（`AutoTradePnlSummary.tsx` + `AiTradeStatsLive.tsx`）

**问题（基座 spec §11 已记录的欠账）**：`AiTradeStatsLive` 左栏宽 40%（60vw × 0.4 ≈ 24vw）。其 KPI 概览由共享的 `AutoTradePnlSummary` 渲染，次级 KPI 在 `sm:grid-cols-4` 下四列拥挤。基座**有意未改**，因为该组件也被 live-auto / paper 面板（`AutoTradePnlPanel`，非窄栏）复用，直接改网格会回归它。基座 §11「回退条件」已给出正解：**给组件增可选 `kpiCols`，默认 4 保旧行为**。

**改造 `AutoTradePnlSummary`**（零回归——默认值等价旧行为）：
```tsx
export default function AutoTradePnlSummary({
  range, onRangeChange, custom, onCustomChange, summary,
  kpiCols = 4,                                  // 新增可选，默认 4 → 既有调用点类名等价、行为零回归（P2-2：clsx 顺序无关）
}: {
  range: AutoTradeRange;
  onRangeChange: (range: AutoTradeRange) => void;
  custom: { startTs?: number; endTs?: number };
  onCustomChange: (custom: { startTs?: number; endTs?: number }) => void;
  summary: PnlSummary;
  kpiCols?: 2 | 4;
}) {
  ...
  {/* :97 改：四列版仍是 grid-cols-2 sm:grid-cols-4；两列版恒为 grid-cols-2（窄栏不再拥挤）*/}
  <div className={clsx("grid gap-2", kpiCols === 2 ? "grid-cols-2" : "grid-cols-2 sm:grid-cols-4")}>
    <Kpi label="完整交易" value={`${summary.count} 笔`} />
    <Kpi label="胜率" value={fmtPct(summary.winRate)} />
    <Kpi label="最大单笔盈" value={signed(summary.bestUsd)} color={colorForReturn(summary.bestUsd)} />
    <Kpi label="最大单笔亏" value={signed(summary.worstUsd)} color={colorForReturn(summary.worstUsd)} />
  </div>
```
（`clsx` 已在该文件 import。主指标那一行 `grid-cols-2`（`:84`）不动。）

**改造 `AiTradeStatsLive`**（唯一一行）：`:94-100` 的 `<AutoTradePnlSummary .../>` 增 `kpiCols={2}`。其它调用点（`AutoTradePnlPanel`）不传 → 默认 4 → 不变。

### 3.3 E2 — K 线「点位 + 相应信息」增强（`TradeCandleChart.tsx` + `TradeCandleChartPanel.tsx`）

**目标**：需求③「显示买卖情况和点位，以及相应的信息」。当前有 markers + 连线，但两个关键价位不够醒目、图下信息偏少。

**（E2-a）K 线叠加开 / 平价水平参考线**（改 `TradeCandleChart.tsx`）：在 `candleSeries.setMarkers(...)`（`:80`）之后、连线循环之前，从 `trades` 派生开 / 平价并各画一条 `createPriceLine`。**不新增 props**（`trades=[entry, exit]` 已含全部所需，与既有 markers / 连线同源）。

**【P1-1 修正：抽为模块级 helper，勿内联膨胀建图 effect】** 建图 `useEffect`（`:41-102`）**迭代 1 既有已约 60 行**，若把参考线逻辑内联会到 ~80 行，破 CLAUDE.md「方法 ≤50 行」。故与既有 `markerForEvent`/`cssVar` 同范式，抽一个**模块级私有 helper**，effect 内只加一行调用；helper 签名需新增 `type ISeriesApi` 到 lightweight-charts 的 type import（或用 `ReturnType<IChartApi["addCandlestickSeries"]>`）：
```tsx
// 模块级 helper（镜像 markerForEvent / cssVar），把 E2-a 逻辑移出建图 effect，effect 维持在阈值内。
// P2-1：Trade.pnl 为必填 number、side 为 "long"|"short"|"close"（types.ts:307/:303）；唯一调用点恒传
// [entry, exit]（TradeCandleChartPanel），故直接解构、按真实契约取值，不做可空兜底（无 ?? 0 / !== undefined 死分支）。
function drawTradeReferenceLines(candleSeries: ISeriesApi<"Candlestick">, trades: Trade[]): void {
  const [entryTrade, exitTrade] = trades;
  if (entryTrade) {
    candleSeries.createPriceLine({
      price: entryTrade.price,
      color: cssVar("--muted"),
      lineWidth: 1,
      lineStyle: 2 /* Dashed */ as LineStyle,
      axisLabelVisible: true,
      title: "开仓",
    });
  }
  if (exitTrade && exitTrade !== entryTrade) {
    candleSeries.createPriceLine({
      price: exitTrade.price,
      color: exitTrade.pnl >= 0 ? cssVar("--long") : cssVar("--short"), // 平仓价参考线按盈亏着色（P2-5：与 CLOSE marker 同语义）
      lineWidth: 1,
      lineStyle: 2 /* Dashed */ as LineStyle,
      axisLabelVisible: true,
      title: "平仓",
    });
  }
}
```
effect 内（`:80` `setMarkers` 之后、连线循环之前）仅加一行：
```tsx
drawTradeReferenceLines(candleSeries, trades); // E2-a：叠加开仓价 / 平仓价水平参考线
```

**【P1-2 修正：关闭蜡烛系列默认末值价线，避免三线拥挤】** `addCandlestickSeries`（`:60-67`）现未设 `priceLineVisible`，蜡烛系列**默认在最后收盘价画一条水平价线**；叠加 E2-a 的开 / 平两条参考线后图上会有三条水平线，稀释「点位醒目」。故在蜡烛系列选项中加一行：
```tsx
const candleSeries = chart.addCandlestickSeries({
  // …既有 up/down/border/wick 颜色不变…
  priceLineVisible: false, // P1-2：关闭默认末值价线，令图上只剩开仓 / 平仓两条有语义的参考线
});
```
- 价位与既有 markers / 连线**同一真实成交源**（`entry.price`/`exit.price`），不引入第二口径。
- 参考线随 `chart.remove()`（`:101`）整体销毁，零泄漏；effect 依赖不变（`[candles, trades, theme]`），明暗切换整体重建、无残留。
- 空 `trades` / 单元素守卫：解构后 `entryTrade` 存在才画开仓线、`exitTrade !== entryTrade` 才画平仓线——不崩。
- **零外溢**：本文件是 K 线专用镜像组件（迭代 1 新建），**非**被复用的 `WeightBacktestChart`，改它不影响任何其它图。

**（E2-b）图下注脚升级为交易事实条**（改 `TradeCandleChartPanel.tsx` `:73-84`）：把现「方向 + 开 / 平价 + 一句说明」升级为一条紧凑、信息完整的事实条，补上**时间、持仓时长、盈亏**——即需求③的「相应的信息」：
```tsx
const pnlUsd = exit.pnl ?? 0;
const holdSec = Math.max(0, exit.ts - entry.ts);
// 事实条：方向徽标 · 开仓价@时间 · 平仓价@时间 · 持仓时长 · 盈亏(USDT + 收益率)
<div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 px-0.5 text-[10px] text-[var(--muted)]">
  <span className="tabular font-medium" style={{ color: direction === "long" ? "var(--long)" : "var(--short)" }}>
    {direction === "long" ? "开多" : "开空"}
  </span>
  <span className="tabular">开仓 {fmtPrice(entry.price)} @ {fmtTime(entry.ts)}</span>
  <span className="tabular">平仓 {fmtPrice(exit.price)} @ {fmtTime(exit.ts)}</span>
  <span className="tabular">持仓 {fmtHoldSpan(holdSec)}</span>
  <span className="tabular font-medium" style={{ color: colorForReturn(pnlUsd) }}>
    盈亏 {signed(pnlUsd)}
  </span>
</div>
```
- 新 import：`fmtTime`、`colorForReturn`、`signed`（`fmtPrice` 已 import）来自 `@/lib/format`。
- `fmtHoldSpan(seconds)`：本文件**局部**小工具（分 / 时 / 天两段），逻辑镜像 `AutoTradeRoundTripRow.formatHoldDuration`。**有意在此重复这个约 10 行的 trivial 工具，而非抽公共函数**——因为集中化会改到共享行组件 `AutoTradeRoundTripRow` 的渲染路径，带来它自身的回归面；一个纯格式化小工具的局部复制，成本远低于跨文件耦合的回归风险，符合「右尺寸」取舍。若未来另有需求，再统一集中到 `lib/format.ts` 并以固定输出串锁测。

### 3.4 E3 — K 线加载韧性（`TradeCandleChartPanel.tsx`）

**（E3-a）「重试」按钮**：现 error / 空分支（`:60-66`）只有静态文案且 `retry:false`（有意不自动重试以免打爆公共行情源）。补一个**手动重试**：
```tsx
if (query.isError || !query.data || query.data.length === 0) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-[10px] border border-[var(--border)] bg-[var(--surface-2)] px-3 py-6 text-center">
      <p className="text-[11px] leading-relaxed text-[var(--muted)]">
        该时段 K 线暂不可用（历史窗口超出行情保留期或行情源暂不可达）。
      </p>
      <Button variant="ghost" size="sm" onClick={() => query.refetch()} disabled={query.isFetching}>
        {query.isFetching ? "重试中…" : "重试"}
      </Button>
    </div>
  );
}
```
- 保留 `retry:false`（自动不重试的克制策略不变）；只提供**用户主动**触发的 `refetch`——瞬时故障（OKX 短暂不可达）用户可原地重试，无需折叠再展开。
- `disabled={query.isFetching}` 防抖，避免连点。
- `Button` 从 `@/components/ui` import（该库已在 `AiTradeStatsDetail` 使用）。

**（E3-b）窗口过长截断提示**：`limit` 被 `Math.min(1000, …)` 上限钳制（`:40`）。极长持仓（如 1H 周期持仓 > 1000 小时 ≈ 41 天）时返回的 1000 根 K 线是以 `endTs` 往回取，可能不含开仓点。这是**罕见边界**，但要诚实告知。在 ok 分支渲染前判定并在事实条尾附一句：
```tsx
const candles = query.data;
const truncated = candles.length > 0 && candles[0].ts > entry.ts; // 首根晚于开仓 → 窗口被截断
...
{truncated && (
  <span className="text-[10px] text-[var(--muted)]">· 窗口较长，仅显示临近平仓的一段</span>
)}
```
- 只加一句诚实提示，**不**引入分页多拉（那属于过度设计，且此边界对 AI 自动交易极罕见）；不满足即不显示。
- **P2-3（成因归因近似，措辞恒真）**：`candles[0].ts > entry.ts` 亦可能由「行情本就缺开仓前数据（低流动性 / 跳空）」触发，未必是条数上限截断；但提示语「仅显示临近平仓的一段」在任何「首根晚于开仓」情形下**都成立**，故不误导，无需为区分成因增加 raw-length 探测的复杂度。

### 3.5 E4 — 年化可信度提示（`lib/annualized.ts` + `AnnualizedProjectionCard.tsx`）

**动机**：`projectAnnualizedReturn` 过了 6h/3 笔门禁仍是**线性外推**，短样本仍会外推出较大数（6h 内 +5% → +7300%）。基座已有免责注脚，但缺一个**一眼可辨**的可信度信号。E4 补一枚纯函数派生的可信度徽标，强化诚实呈现（CLAUDE.md「显式降级 / 不误导」），**不改任何年化数值口径**。

**纯函数（新增到 `lib/annualized.ts`，绝不抛）**：
```ts
export type AnnualizedConfidence = "low" | "medium" | "high";

// 样本可信度：据经历时长与完整交易笔数分档。阈值为经验值（可调，调整须在 .claude-index/config.md 记录）。
// high：≥30 天 且 ≥30 笔；medium：≥7 天 且 ≥10 笔；否则 low。取「时长档」「笔数档」的较低者，避免任一维度虚高。
const DAY = 86_400;
export function annualizedConfidence(elapsedSec: number, tradeCount: number): AnnualizedConfidence {
  const days = elapsedSec / DAY;
  const timeTier: AnnualizedConfidence = days >= 30 ? "high" : days >= 7 ? "medium" : "low";
  const tradeTier: AnnualizedConfidence = tradeCount >= 30 ? "high" : tradeCount >= 10 ? "medium" : "low";
  const order: Record<AnnualizedConfidence, number> = { low: 0, medium: 1, high: 2 };
  return order[timeTier] <= order[tradeTier] ? timeTier : tradeTier;
}
```

**徽标（改 `AnnualizedProjectionCard.tsx` `ok===true` 分支）**：在大字年化同一行右侧并排一枚小徽标：
```tsx
const confidence = annualizedConfidence(elapsedSec, tradeCount);   // ok 分支内
const CONF = {
  low:    { label: "可信度 低", cls: "text-[var(--short)] border-[var(--short)]" },
  medium: { label: "可信度 中", cls: "text-[var(--muted)] border-[var(--border)]" },
  high:   { label: "可信度 高", cls: "text-[var(--long)] border-[var(--long)]" },
}[confidence];
// 布局（P2-4：显式给出 flex 包裹，勿只写徽标 span）：把现 :42-47 的大字与徽标同置一行，右侧并排徽标。
<div className="flex items-center gap-2">
  <span
    className="tabular text-xl font-semibold"
    style={{ color: colorForReturn(projection.annualizedPct) }}
  >
    {fmtPct(projection.annualizedPct)}
  </span>
  <span className={clsx("rounded-full border px-1.5 py-0.5 text-[9px] font-medium", CONF.cls)}>
    {CONF.label}
  </span>
</div>
```
- 仅 `ok===true`（已出数字）时显示；降级分支不变。
- 纯呈现、不改 `annualizedPct` 数值；徽标只回答「这个外推有多可信」。
- `clsx` import 到该文件（现未 import，需补）。

### 3.6 关键时序（Sequence）

```
展开一条交易记录（右栏，E2/E3）
  点行 → AutoTradeRoundTripRow(open,bar) 挂 <TradeCandleChartPanel>（懒加载，单开手风琴不变）
    → useQuery(["trade-window-candles",instId,bar,entry.ts,exit.ts])（staleTime:Infinity，重复展开零重拉）
       → api.candlesWithMeta(...) → GET /api/candles（公共、节流、429 安全，不变）
       → 客户端裁剪 [entry.ts-pad, exit.ts+pad]
    → ok：<TradeCandleChart>（蜡烛 + BUY/SELL/CLOSE markers + 开→平连线 + 【E2-a 开/平价参考线】）
          + 【E2-b 交易事实条：方向·开/平价@时间·持仓·盈亏】+【E3-b 若截断则附提示】
    → error/空：降级文案 +【E3-a 重试按钮 → query.refetch()】
预计年化（左栏，E4）
  projectAnnualizedReturn(...) → ok? 大字年化 +【E4 可信度徽标(annualizedConfidence)】+ 免责注脚 : 降级文案
KPI 概览（左栏，E1）
  <AutoTradePnlSummary kpiCols={2}> → 次级 KPI 恒两列（窄栏不再拥挤）；其它调用点默认 4 不变
```

---

## 4. 文件 / 模块变更计划（File / Module Change Plan）

| 文件 | 动作 | 一句话意图 |
|---|---|---|
| `apps/web/components/Chart/AutoTradePnlSummary.tsx` | 改 | E1：+可选 `kpiCols?: 2\|4`（默认 4）；次级 KPI 网格类按 prop 派生，零回归其它调用点 |
| `apps/web/components/Agent/aiTradeStats/AiTradeStatsLive.tsx` | 改 | E1：左栏 `<AutoTradePnlSummary>` 传 `kpiCols={2}`（仅此一行） |
| `apps/web/components/Chart/TradeCandleChart.tsx` | 改 | E2-a：`candleSeries` 叠加开仓价 / 平仓价水平参考线（`createPriceLine`，同真实成交源，随 chart 销毁） |
| `apps/web/components/Chart/TradeCandleChartPanel.tsx` | 改 | E2-b 事实条（方向 / 开平价@时间 / 持仓 / 盈亏）+ E3-a 重试按钮 + E3-b 截断提示 |
| `apps/web/lib/annualized.ts` | 改 | E4：+纯函数 `annualizedConfidence(elapsedSec, tradeCount)`（时长档 / 笔数档取较低者，绝不抛） |
| `apps/web/components/Agent/aiTradeStats/AnnualizedProjectionCard.tsx` | 改 | E4：`ok` 分支并排样本可信度徽标（纯呈现，不改数值口径） |
| `apps/web/lib/annualized.test.ts` | 改 | 扩测 `annualizedConfidence`：low/medium/high 三档 + 边界 + 取较低者 |
| `apps/web/components/Chart/TradeCandleChartPanel.test.tsx` | 改 | 扩测：事实条渲染关键字段、error 分支「重试」触发 `refetch`、截断提示 |
| `apps/web/components/Agent/AiTradeStatsPanel.test.tsx` | 改 | 扩测：Live 左栏 KPI 两列（`kpiCols={2}`）、年化卡出数字时显示可信度徽标 |

> 说明：**零后端改动、零 DB 迁移、零 store 形状 / 持久化变更、零新增依赖**（`lightweight-charts.createPriceLine`、`@/components/ui.Button`、`clsx`、`@/lib/format.*` 均在册）。
>
> **CLAUDE.md 阈值合规（P1-1 修正后如实说明）**：文件均 ≤800 行、形参 ≤5、嵌套 ≤4。方法长度上，`TradeCandleChart.tsx` 的建图 `useEffect`（`:41-102`）**迭代 1 既有已约 60 行**（超 50 行阈值的 pre-existing 欠账，非本迭代引入）；本迭代**不再向其内联** E2-a 逻辑，而是抽出**模块级 helper** `drawTradeReferenceLines`（自身 ≤30 行、`markerForEvent` 同范式），effect 仅加一行调用，故**本迭代新增 / 改动的方法全部 ≤50 行**。既有 effect 超阈的收敛属独立重构，超出本纯打磨迭代范围，此处显式记录而不裹挟。无新建生产文件（仅扩既有测试）。

---

## 5. 接口设计（Interface Design）

### 5.1 REST / WS / CLI

**无新增、无改动**。E2 的 K 线继续走既有 `GET /api/candles`（`api.candlesWithMeta`），且**不新增任何请求**（参考线、事实条均从已拉回的 `candles` 与已在手的 `entry/exit` 派生）；E3 重试是对**同一** `queryKey` 的 `refetch`。

### 5.2 组件 props 契约（改 / 新）

- `AutoTradePnlSummary`：新增**可选** `kpiCols?: 2 | 4`（默认 `4`）。不传 = 旧行为。
- `TradeCandleChart`：props 契约**不变**（`{ candles; trades; theme }`）；E2-a 全部从既有 `trades` 派生。
- `TradeCandleChartPanel`：props 契约**不变**（`{ instId; bar; entry; exit }`）；E2-b/E3 全部内部化。
- `AnnualizedProjectionCard`：props 契约**不变**（`{ returnPct; elapsedSec; tradeCount }`）；E4 从既有 props 派生。
- 新增纯函数：`annualizedConfidence(elapsedSec: number, tradeCount: number): "low"|"medium"|"high"`（`lib/annualized.ts`）。

---

## 6. 数据模型（Data Model）

- **DB**：完全不变（无迁移、无填列、无加法写）。
- **wire / 运行期形状**：不变。新增类型仅 `AnnualizedConfidence`（`"low"|"medium"|"high"` 字面量联合，`lib/annualized.ts` 局部，不入 store、不入 wire）。
- **单一口径**：E2 参考线价位 = `entry.price`/`exit.price`（与既有 markers / 连线同源真实成交）；E4 可信度只据 `elapsedSec`/`tradeCount` 分档，**不改** `annualizedPct` 数值。
- **单位（沿用基座 P2-7）**：`candle.ts`、`entry.ts`/`exit.ts`、`elapsedSec` 全为**秒**；`createPriceLine.price` 为价格数值（非时间），不涉时间单位。

---

## 7. 测试与验收标准（Testing & Acceptance）

### 7.1 前端单测（`NODE_ENV=test`；`tsc --noEmit` 为类型门；既有 1130 用例零回归）

- **`annualized.test.ts`（扩，纯函数）**：`annualizedConfidence`——`(45d, 40笔)→"high"`；`(10d, 12笔)→"medium"`；`(2d, 40笔)→"low"`（时长档 low 压低）；`(45d, 5笔)→"low"`（笔数档 low 压低）；`(8d, 15笔)→"medium"`；边界 `(30d, 30笔)→"high"`、`(7d, 10笔)→"medium"`；绝不抛。既有 `projectAnnualizedReturn` 用例保持绿（本迭代不改它）。
- **`TradeCandleChartPanel.test.tsx`（扩，mock `./TradeCandleChart` + `api.candlesWithMeta`）**：成功态事实条含「开仓」「平仓」「持仓」「盈亏」关键字与 `fmtPrice`/`fmtTime` 结果；`isError` / 空数据 → 显「重试」按钮，点击调用 `refetch`（可断言 `api.candlesWithMeta` 二次被调用或 `refetch` spy 命中）；`candles[0].ts > entry.ts` → 显截断提示，反之不显。既有「挂载即拉 / loading Skeleton / 不可变缓存键」用例保持绿。
- **`AiTradeStatsPanel.test.tsx`（扩）**：Live 左栏次级 KPI 容器类含 `grid-cols-2` 且**不含** `sm:grid-cols-4`（`kpiCols={2}` 生效）；年化样本充分（≥6h 且 ≥3 笔）时年化卡出数字**并**显示可信度徽标（低 / 中 / 高之一）；年化样本不足时仍显降级文案且**不**显徽标。既有双栏 / 展开挂载 K 线 / 不传 bar 零回归用例保持绿。
- **共享组件零回归**：`AutoTradePnlSummary` 不传 `kpiCols` 时次级 KPI 仍 `grid-cols-2 sm:grid-cols-4`（`AutoTradePnlPanel` 等调用点若有既有测试保持绿；无则 §7.2 手测覆盖）。

### 7.2 验收标准（Definition of Done）

1. **E1**：AI 统计看板「当前」页左栏次级 KPI 恒为两列，窄栏不再拥挤；`AutoTradePnlSummary` 的其它调用点（live-auto / paper 面板）KPI 版式**类名等价（同组类、顺序无关、视觉零差异）、行为零回归**（P2-2：`clsx` 重排类名字符串，勿写精确 className 字符串断言，用 `toHaveClass` 断分立类）。
2. **E2**：展开任一交易记录，K 线上除既有蜡烛 / BUY/SELL/CLOSE markers / 开→平连线外，**并叠加开仓价、平仓价两条水平参考线**（右轴带价签）；图下**交易事实条**完整展示方向、开 / 平价与时间、持仓时长、盈亏（USDT + 收益率）。参考线 / 事实条价位与既有成交同源，无第二口径。
3. **E3**：K 线拉空 / 出错时在降级文案下出现**「重试」按钮**，点击原地重取、`isFetching` 时禁用防抖；极长窗口被行情条数上限截断时**显式提示**「仅显示临近平仓的一段」，不伪造缺失蜡烛。
4. **E4**：年化卡出数字时并排**样本可信度徽标（低 / 中 / 高）**，由经历天数与交易笔数分档派生；样本不足降级时不显徽标。年化数值本身不变。
5. **全局**：零后端改动、零 DB 迁移、零新依赖；`tsc --noEmit` 对本迭代文件零错误（仓库既有 `WeightBatchArena.test.tsx` 的 pre-existing tsc 错误非本迭代引入）；`apps/web` 前端测试全绿（既有 1130 + 本迭代新增用例）；`next build` 通过。

---

## 8. 风险与缓解（Risks & Mitigations）

| 编号 | 风险 | 缓解 |
|---|---|---|
| R-1 | E1 改共享组件回归 live-auto / paper 面板 | `kpiCols` **默认 4** → 不传即旧行为；仅 AI 看板传 `2`；补「不传 kpiCols 版式不变」断言 |
| R-2 | E2-a `createPriceLine` 泄漏 / 主题切换残留 | 参考线挂在 `candleSeries` 上，每次 effect 重跑整体 `chart.remove()` 销毁；依赖含 `theme` → 明暗切换整体重建，无残留 |
| R-3 | E2-a 空 / 单元素 `trades` 越界 | `entryTrade` 存在才画开仓线、`exitTrade !== entryTrade` 才画平仓线；沿用既有空数据不崩范式 |
| R-4 | E2-b 事实条与行折叠态信息重复冗余 | 事实条是**图内语境**（配合参考线 / markers 解读点位），与折叠行摘要视角不同；措辞紧凑（`text-[10px]`），不喧宾夺主 |
| R-5 | E2-b 局部复制 `fmtHoldSpan` 违背 DRY | 有意取舍：集中化会改共享 `AutoTradeRoundTripRow` 渲染路径引回归面；trivial 小工具局部复制成本更低，§3.3 已记录回退路径（未来统一到 `lib/format.ts` 并锁测） |
| R-6 | E3 重试打爆公共行情源 | 保留 `retry:false`（自动不重试）；仅**用户主动**触发；`disabled={isFetching}` 防连点；公共 `/api/candles` 本身已节流 + 429 退避 |
| R-7 | E4 可信度阈值武断 | 阈值为经验值、**只影响提示语不影响数值**；取时长 / 笔数两档较低者避免单维虚高；阈值可调、调整须记 `.claude-index/config.md` |
| R-8 | jsdom 跑不动 lightweight-charts | E2-a 参考线属图库内部，测试沿用既有 **mock `TradeCandleChart`** 范式（面板测只断事实条 / 状态机 / 重试）；E4 走纯函数单测 |
| R-9 | 与仓内其它在制特性（services/quant、desktop 等未提交改动）串味 | 本迭代**只**动上表 9 个 `apps/web` 文件；提交节点须 `git add <精确路径>`（严禁 `git add -A`），剔除无关在制改动 |

---

## 9. 范围边界（Scope）

- **范围内**：E1 左栏 KPI 两列收敛（可选 prop，零回归）；E2 K 线开 / 平价参考线 + 交易事实条；E3 K 线加载重试 + 截断诚实提示；E4 年化样本可信度徽标。均为纯前端、零后端、零迁移、零新依赖、默认等价旧行为。
- **范围外（明确不做）**：改任何后端 / DB / 落库 / 数据口径；改被复用的 `WeightBacktestChart` / `CandleChart` 对外行为；给 K 线做分页多拉以覆盖超长窗口（诚实提示即可）；把复利 / 蒙特卡洛等复杂预测引入年化（延续基座只做线性外推 + 门禁 + 可信度提示）；改「历史·列表」页（会话导航，仍全宽不分栏）；改「历史·详情」页局部 `KpiGrid`（已是两列，无需动）。
- **对用户诚实的已知限制（沿用并强化基座）**：预计年化仍为线性外推估算、仅已实现、样本越短越不可靠——E4 徽标使这一点更醒目；行内 K 线仍依赖 OKX `history-candles`，极旧 / 超长窗口拉空或截断→显式降级 / 提示，绝不伪造蜡烛。

---

## 10. 实施过程发现的方案缺陷（Implementation Deviations）

> 预留给 Subtask #2（代码开发）。如实现中发现方案缺陷或需偏离，在此记录原因与回退条件。

**开发前已核实、开发期无需再查（Subtask #0 已逐符号核对 commit 0fc59ae 源码）**：
- ✅ `AutoTradePnlSummary` 次级 KPI 在 `:97` `grid-cols-2 sm:grid-cols-4`，被 `AiTradeStatsLive` 与 `AutoTradePnlPanel` 共享 → E1 必须走可选 `kpiCols` 默认 4，不可直接改类。
- ✅ `TradeCandleChart` 是迭代 1 新建的 K 线专用组件（非 `WeightBacktestChart`），改它零外溢；`chart.remove()` 已在 effect cleanup（`:101`），参考线随之销毁。
- ✅ `TradeCandleChartPanel` error 分支现为静态 `<p>`、`retry:false`（`:52`）→ E3 加按钮不与自动重试策略冲突。
- ✅ `AnnualizedProjectionCard` `ok` 分支（`:40-53`）出数字，E4 徽标并排安全；`clsx` 需新 import。
- ✅（Subtask #1 评审已核实，欠账结案）：`lib/format.ts` **已导出** `fmtPct`/`fmtNum`/`fmtPrice`/`fmtTime`/`signed`/`colorForReturn`/`fmtQty`/`fmtMoney`——E2-b / E4 直接 import 即可，签名与用法一致，无出入。
- ✅（Subtask #1 评审已核实）：`Trade` 契约（`types.ts:299-324`）为 `side: "long"|"short"|"close"`、`price: number`、`pnl: number`（**必填**）、`ts: number`——E2 一律按此必填契约取值，**不做可空兜底**（`?? 0`/`!== undefined` 属死分支，已在 §3.3 修正为直接解构）。
- ✅（Subtask #1 评审已核实）：`createPriceLine(options: CreatePriceLineOptions)`、`CreatePriceLineOptions = Partial<PriceLineOptions> & Pick<PriceLineOptions,"price">`（v4.2 d.ts）——传部分字段合法、零 tsc 错误；`priceLineVisible` 为 `SeriesOptionsCommon` 字段，可在 `addCandlestickSeries` 选项内直接设。

---

## 11. 与基座方案的关系与迭代说明

- 本方案是 `docs/plans/ai-trade-stats-two-column-and-kline/spec.md`（v2，已随 `commit 0fc59ae` 落地）的**迭代 2 增量**，不重复其任何已完成工作，聚焦需求原文的质量硬指标与基座自留欠账。
- **E1 直接源自基座 spec §11「回退条件」**（原文：「若后续确需当前页左栏也收敛，应给 `AutoTradePnlSummary` 增可选 prop（如 `kpiCols?: 2 \| 4`，默认 4 保持既有调用点不变）……留待专门迭代」）——本迭代即该「专门迭代」。
- 下游节点指引：Subtask #1（评审）逐节核对 v1 → 已出 **v2**（本文件）；Subtask #2（开发）按 §4 变更计划逐项落码，仅动上表 9 个 `apps/web` 文件，且须遵循本次评审在 §3.3/§4 落定的 P1-1（helper 抽出）、P1-2（`priceLineVisible:false`）、P2-1（按必填契约取值）修正；Subtask #3（Review + 提交）以 `git add <精确路径>` 精确暂存，`feat:` 前缀提交，剔除仓内无关在制改动（R-9）。

---

## 评审结论（Review Verdict）

**有条件通过（Approved with Conditions）**

本方案 v1 grounding 扎实、右尺寸得当，四项增量（E1 左栏 KPI 密度 / E2 K 线点位增强 / E3 加载韧性 / E4 年化可信度）均为纯前端、零后端、零 DB 迁移、零新依赖、默认等价旧行为，直接承接需求原文「美观优雅顶级设计 / HCI 最佳实践 / 功能完善可靠稳健」的质量硬指标，并如实闭合基座 §11 明确留给「专门迭代」的 `kpiCols` 欠账。评审逐节核对迭代 1 已合并源码（`commit 0fc59ae`）与安装依赖后，**未发现 P0**；发现的 **2 项 P1 与 5 项 P2 已在正文（§3.2/§3.3/§3.4/§3.5/§4/§7.2/§10）逐条修正或记录取舍**，文档已升版 **v2**，**无 P0/P1 遗留**。

**放行条件（Subtask #2 开发时必须遵守，Subtask #3 Review 时逐条验收）**：
1. **P1-1**：E2-a 参考线绘制**必须**抽为模块级 helper `drawTradeReferenceLines(candleSeries, trades)`（`markerForEvent` 同范式），建图 `useEffect` 内仅一行调用；**不得**内联使该 effect 更长。新增 / 改动的方法一律 ≤50 行。
2. **P1-2**：E2-a 引入开 / 平参考线时**必须**同步给蜡烛系列设 `priceLineVisible:false`，确保图上恰好两条有语义的水平参考线（不出现默认末值价线的第三条）。
3. **P2-1**：E2 一律按 `Trade` 必填契约取值（`pnl: number`、`side: "long"|"short"|"close"`）——**禁用** `?? 0`、`!== undefined` 等暗示可空的死分支；`exitTrade.pnl >= 0` 直接判定。
4. **P2-2**：`AutoTradePnlSummary` 其它调用点断言用 `toHaveClass` 断分立类，**勿**写精确 className 字符串断言（`clsx` 会重排类名顺序）。
5. **零回归护栏**：`tsc --noEmit` 对本迭代文件零错误（仓库既有 `WeightBatchArena.test.tsx` 的 pre-existing 错误非本迭代引入）；`apps/web` 既有 1130 用例 + 本迭代新增用例全绿；`next build` 通过；组件测试须 `NODE_ENV=test`。
6. **精确暂存（R-9）**：提交仅 `git add` 上表 9 个 `apps/web` 文件，严禁 `git add -A`，剔除 `services/quant`、`desktop`、`.env.example`、诊断文档、构建 / 索引目录等仓内无关在制改动。

达成上述条件即视为完成，无需再次评审。
