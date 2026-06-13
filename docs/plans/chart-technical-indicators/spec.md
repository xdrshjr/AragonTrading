# Aragon Trading — K 线技术指标体系方案 (spec)

- **Feature**: `chart-technical-indicators`
- **文档版本**: v2（Subtask #0 方案设计 → Subtask #1 评审修复；本版已就地修复全部 P0/P1，详见下方「评审记录」）
- **作者 / 角色**: Solution Architect (Anthropic Engineering)
- **创建日期**: 2026-06-13
- **全局目标**: 完成项目开发，确保可用，数据真实可用
- **基线**: 迭代 1/2/3 已交付并提交的 monorepo（`apps/web` Next.js 14 + React 18 + TS + Tailwind + `lightweight-charts@4.2.3`；`services/quant` FastAPI）。前序设计见 `docs/plans/aragon-trading-platform/spec.md`（v2）、`docs/plans/aragon-iter2-enhancements/spec.md`、`docs/plans/aragon-iter3-hardening/spec.md`。
- **关系声明**: 本文档**只规划左侧 K 线图技术指标这一前端增量特性**，不重述、不推翻已交付的架构、后端接口语义、数据模型、5 策略逻辑、SimBroker 撮合内核与回测 / 回放 / 实时管线。迭代 1 v2 的 **5 条放行条件**（① 真实数据无假数据 ② 凭据不入库 ③ 全系统秒级 UTC ts ④ 锁 `lightweight-charts@^4.2` ⑤ SimBroker·VWAP·注册表合并规则）在本特性**继续生效、不得回退**。本特性**零后端改动、零 DB schema 改动**。

---

## 评审记录（Review Notes，Subtask #1 → v2）

> **评审方式**：本次评审**逐节对照工作目录真实源码（`apps/web/...`）与 `lightweight-charts@4.2.3` 实际 typings** 核验，非纸面通读。已确认属实的关键事实：`lightweight-charts@^4.2.0`（lock 解析为 4.2.3）；`CandleChart.tsx` 的 `cssVar()` / overlay reconcile / R6 卸载清理 / `addCandlestickSeries` 默认右价轴 / equity 用独立 `"overlay"` 价轴；`store.ts` 持久化 key `aragon-ui`、`version:1`、`partialize`、`skipHydration:true`；`types.ts` 的 `Candle` **含 `vol` 字段**；`page.tsx` 的 `mergeCandles`/`mergeUnique` 与 300 根拉取；`ui/index.tsx` 有 `Button`（`default/sm`）**且尚无 `Switch`**；`globals.css` 的 `--long/--short/--muted/--accent/--text/--surface/--surface-2/--border/--accent-soft/--font-mono` 全部存在；本仓库**无 `CLAUDE.md`**（一致性以现有代码约定为准）。typings 已核实 `minimumWidth`/`width()`/`addHistogramSeries`/`setVisibleLogicalRange`/`subscribeVisibleLogicalRangeChange`/`createPriceLine`/`setCrosshairPosition`/`clearCrosshairPosition`/`autoscaleInfoProvider` 全部可用。

| # | 维度 | 严重度 | 问题 | 处置 |
|---|---|---|---|---|
| RV-1 | 可行性 / 正确性 | **P1** | §4.4 用静态常量 `minimumWidth:64` 保证主图与各副图价格轴等宽，但 v4.2 typings 对该字段**明确写道「该值在价格轴内容需要更多空间时会被超过」**（`PriceScaleOptions.minimumWidth`，typings.d.ts:2830）。默认标的 `BTC-USDT-SWAP` 价格量级约 6 万（"63,000.0"），主图右价轴实际宽度必然 > 64px，而副图数值小（RSI 0~100、MACD 数百）停在 64px 下限 → 主副图绘图区左右边缘错位、时间刻度对不齐，**直接违背用户「适配正确、完美」诉求**。 | **已就地修复**（§4.4 等宽对齐段、§4.9、§5、R1）：改为**动态等宽对齐**——数据/尺寸变化后用 `chart.priceScale(id).width()`（v4.2 已确认可用，typings.d.ts:1674）测主图左右价轴真实像素宽，回写为所有图（主+副）同侧价轴的 `minimumWidth`；`minimumWidth=64` 退化为保底下限。 |
| RV-2 | 完整性 / 人机交互 | **P1** | 原方案只规定「非底部 pane 隐藏时间轴、仅最底部 pane 显示」，却**完全未处理主图自身的时间轴**。一旦启用 ≥1 个副图，主图底部仍渲染一行日期轴、最底部 pane 又渲染一行 → **出现两行重复日期轴**，同样违背「完美」；且默认开 MACD 即落入此情形。 | **已就地修复**（§4.4 新增「主图时间轴」段、§4.9）：主图 `timeScale.visible = !hasPane`——有 pane 时主图隐藏日期轴（标度仍参与同步、仅不渲染），由最底部 pane 独占；无 pane（仅 MA/BOLL 叠加）时主图正常显示。任何时刻全栈只渲染一行日期轴。 |
| RV-3 | 一致性 | **P1** | `reconcilePlots` 的 id 契约**自相矛盾**：§4.3 步骤 2 说叠加层以 `${def.key}:${plot.id}` 作「全局唯一 plotId」，但 §4.5 又用裸 `plot.id` 构造 `nextIds` 并增删系列；叠加层 map 由多指标（ma/boll/ema）**共用**。按字面实现会「建键用带前缀 id、删除判定用裸 id」→ **每帧把所有系列误删重建**（或裸 id 跨指标碰撞）。 | **已就地修复**（§4.3 步骤 2-3、§4.5）：叠加层 effect 在调用前把每个 `plot.id` 改写为命名空间 id（`${def.key}:${plot.id}`），`reconcilePlots` 对 id 不可知，map 键 / `nextIds` / 建系列三处统一用同一 id；pane 各自独占 map/chart，裸 id 即唯一。 |
| RV-4 | 完整性 | P2 | §4.4 称对「主系列」设 `autoscaleInfoProvider` 锁定 `fixedRange`（RSI 0~100）。当一个 pane 有多条线（RSI 6/12/24）时，仅给一条设 provider，其余线仍按自身范围参与自动缩放，锁定可能不生效。 | 已在 §4.4 注明：provider 须作用于**该 pane 价格轴上的全部系列**（每条返回同一固定区间）。 |
| RV-5 | 完整性 / 数据 | P2 | VOL 取 `candle.vol`；现有 `CandleChart` 从未读过 `vol`（只读 o/h/l/c/ts）。`Candle.vol` 类型必填，但需确认后端 `/candles` 确实回填非零成交量，否则 VOL 柱恒为 0。VOL 默认关，**不阻断**。 | 已在 §8.3 / §11 增加验证项：实现期确认 `/candles` 含真实 `vol`；缺失则按真实数据原则在 §11 登记，不造假量。 |
| RV-6 | 规模适配 / 健壮性 | P2 | 副图列随开关动态增删，若 `<IndicatorPane>` 列未用稳定 `key`，会触发无关 pane 卸载重建（chart churn / 闪烁 / 临时泄漏窗口）。 | 已在 §4.4 注明：pane 列以 `key={def.key}` 渲染，开关某指标只增删对应 pane。 |

> **结论先行**：上述 **3 个 P1 均已在本 v2 文档正文就地修复，无遗留 P0/P1**；P2 为实现期需留意的非阻断项，已写入正文与 §8/§11。完整结论见文末「评审结论」。

---

## 1. Overview（概述）

当前左侧 K 线面板（`apps/web/components/Chart/CandleChart.tsx` + `ChartToolbar.tsx`）已经具备专业图表的基础能力：真实 OKX 历史 K 线、缩放 / 平移、日期跳转、滚动加载更早历史、回测买卖点 markers、净值折线、多策略归一化净值叠加、以及迭代 3 接通的「最右一根实时跳动」。但作为一个量化交易工作台，它**缺少技术指标图层**——用户看不到均线、布林带、MACD、RSI 等判断行情结构与买卖时机的核心工具，图表的「分析」价值没有被释放。

本特性给 K 线面板补齐一套**可开关的技术指标体系**，并区分两类呈现：**(A) 主图叠加型指标**（MA 多周期均线、EMA、BOLL 布林带）直接画在 K 线所在的价格坐标上；**(B) 副图振荡型指标**（MACD、RSI、KDJ、成交量 VOL）画在主图下方各自独立、与主图**时间轴严格联动**的子图窗格里。默认开启 **MA + BOLL + MACD**（符合用户诉求），用户通过 K 线图上方工具栏新增的「指标」按钮，点开一个**下拉弹窗**，在其中逐项开启 / 关闭任意指标；选择态随既有 `localStorage` 持久化策略一并保存，刷新后恢复。

设计的两条主线是**可扩展**与**正确联动**。可扩展：所有指标的数学计算抽成 `apps/web/lib/indicators.ts` 里的**纯函数 + 声明式注册表**（沿用项目既有的 `compare.ts` / `format.ts` 纯函数 + 同名 `*.test.ts` 约定），新增一个指标 = 注册表加一条 + 一个计算函数，**菜单与渲染都无需改**。正确联动：由于锁定的 `lightweight-charts@4.2.3` **不支持原生多窗格（panes 是 v5 才有）**，副图指标采用业界 v4 的标准做法——**多个 `createChart` 实例垂直堆叠 + 时间轴双向同步 + 价格轴等宽对齐**，保证缩放 / 平移 / 十字光标在主副图之间像素级一致，做到「适配正确、完美」。整套特性是**纯前端增量**，不触碰后端、撮合、5 策略与既有契约。

---

## 2. Goals / Non-Goals

### Goals
1. **指标可见**：主图叠加 MA / EMA / BOLL；副图呈现 MACD / RSI / KDJ / VOL；默认开启 MA + BOLL + MACD。
2. **可开关**：工具栏「指标」按钮点开下拉弹窗，逐项开关任意指标，符合人机交互最佳实践（键盘可达、Esc / 点击外部关闭、状态可见、可一键恢复默认）。
3. **完美联动**：副图与主图时间轴严格同步，缩放 / 平移 / 十字光标一致，价格轴等宽对齐，无错位。
4. **真实数据**：指标全部由真实 OKX K 线（含迭代 3 的实时成形根）计算，最右一根随实时行情即时更新；不引入任何假数据。
5. **可扩展**：纯函数 + 注册表驱动；新增指标改动面最小。
6. **持久化**：指标选择态随既有 `aragon-ui` localStorage 持久化，刷新恢复。
7. 全程不违反 5 条放行条件，不改后端 / DB / 5 策略 / SimBroker。

### Non-Goals（本特性不做）
- 不在 v1 提供**指标参数自定义编辑 UI**（周期等用合理默认值；注册表预留参数位，参数编辑列为后续增强）。
- 不新增后端接口 / 不改 `/candles` 响应体 / 不在后端算指标（指标在前端实时计算，避免来回请求且与实时成形根天然同步）。
- 不画指标本身的「买卖信号箭头」（markers 仍由回测 / paper 成交驱动，与本特性解耦）。
- 不升级 `lightweight-charts` 到 v5（放行条件④锁 `^4.2`；用 v4 多图同步方案达成等效体验）。
- 不做指标值的导出（导出仍是 F4 的成交 / 净值 CSV）。

---

## 3. 指标清单与默认（Indicator Catalog）

| key | 名称 | 类别 | 默认 | 参数（v1 固定默认） | 子线 / 绘制 |
|---|---|---|---|---|---|
| `ma` | MA 均线 | 主图叠加 overlay | ✅ 默认开 | 5 / 10 / 20 / 60 | 4 条 SMA 线 |
| `ema` | EMA 指数均线 | overlay | ⬜ | 12 / 26 | 2 条 EMA 线 |
| `boll` | BOLL 布林带 | overlay | ✅ 默认开 | period=20, mult=2 | upper / mid / lower 3 条线（mid 虚线）|
| `macd` | MACD | 副图 pane | ✅ 默认开 | 12 / 26 / 9 | HIST 柱（涨绿跌红）+ DIF 线 + DEA 线 |
| `rsi` | RSI 相对强弱 | pane | ⬜ | 6 / 12 / 24 | 3 条 RSI 线 + 70/50/30 参考线 |
| `kdj` | KDJ 随机指标 | pane | ⬜ | 9 / 3 / 3 | K / D / J 3 条线 |
| `vol` | VOL 成交量 | pane | ⬜ | — | 成交量柱（按 K 线涨跌着色）|

> 默认开启集合严格对齐用户诉求「默认 MA + BOLL 区间 + MACD」。`ema` / `rsi` / `kdj` / `vol` 默认关，作为「我没提到的其它相关技术指标」补全，用户可在弹窗自行开启。后续要再加（如 WR、SAR、OBV）只需在注册表追加一条 + 一个计算函数。

---

## 4. 技术设计（架构 / 关键路径 / 时序）

整体架构沿用前序：浏览器 → Next.js（BFF）→ FastAPI → OKX。本特性**不新增架构层、不动后端**，全部落在 `apps/web` 的图表层与状态层。数据来源是 `app/page.tsx` 已经算好的 `candles`（`mergeCandles(older, base, live, forming)`，秒级 UTC、严格升序、去重，含实时成形根），原样传给 `CandleChart`，由前端纯函数实时计算各指标。

### 4.1 分层结构

```
app/page.tsx
  ├─ 读取 store.indicators（指标开关 Record）
  └─ <CandleChart candles indicators theme .../>
        ├─ 主图 IChartApi（既有：candle / equity / overlays / markers）
        │     └─ 叠加：MA / EMA / BOLL 线系列（右价轴，复用 reconcilePlots）
        ├─ <IndicatorPane>  ← 每个启用的「副图」指标一个（MACD / RSI / KDJ / VOL）
        │     └─ 独立 IChartApi + reconcilePlots + createPriceLine（参考线）
        └─ chartSync 协调器：主图 + 所有副图 时间轴 / 十字光标 双向同步

components/Chart/ChartToolbar.tsx
  └─ <IndicatorMenu/>  ← 「指标」按钮 + 下拉弹窗（toggle store.indicators）

lib/indicators.ts   纯函数（SMA/EMA/BOLL/MACD/RSI/KDJ/VOL）+ 声明式注册表 INDICATORS
lib/chartSync.ts    createSyncGroup()：register/unregister/broadcast，含防回环 guard
components/Chart/plots.ts   reconcilePlots()：按 Plot[] 增/删/更新 line|histogram 系列
lib/store.ts        新增 indicators 开关态 + toggleIndicator + resetIndicators（persist）
components/ui/index.tsx  新增 Switch 原子组件（开关 UI）
```

### 4.2 指标计算（`lib/indicators.ts`，纯函数 + 注册表）

**设计原则**：本文件**只依赖 `lightweight-charts` 的类型**（`UTCTimestamp`）与 `lib/types.ts` 的 `Candle`，**不引入任何 React / DOM**，从而可被 `indicators.test.ts` 在 jsdom 外纯逻辑单测（与 `compare.ts` 同构）。

**统一输出抽象 `Plot`**：每个指标计算后产出一组「绘制描述」，渲染层据此泛化地建系列——`line` → `addLineSeries`，`histogram` → `addHistogramSeries`。这样新增指标**不改任何渲染代码**。

```ts
import type { UTCTimestamp } from "lightweight-charts";
import type { Candle } from "./types";

export type Pt = { time: UTCTimestamp; value: number };
export type HistPt = Pt & { color?: string };

export type Plot =
  | { kind: "line"; id: string; title: string; color: string;
      lineWidth?: 1 | 2 | 3 | 4; lineStyle?: number; data: Pt[] }
  | { kind: "histogram"; id: string; title: string; data: HistPt[]; base?: number };

export type IndicatorCategory = "overlay" | "pane";
export type IndicatorKey = "ma" | "ema" | "boll" | "macd" | "rsi" | "kdj" | "vol";

// 渲染时注入的主题色（从 CSS 变量解析，主题切换可重算着色）
export type ThemeColors = { long: string; short: string; muted: string; accent: string; text: string };

export interface IndicatorDef {
  key: IndicatorKey;
  label: string;            // 中文显示名，如 "MACD"
  description: string;      // 弹窗副文本 / tooltip，如 "平滑异同移动平均"
  category: IndicatorCategory;
  defaultOn: boolean;
  paramsHint: string;       // 显示用，如 "(12,26,9)"
  // 副图固定值域时给定（如 RSI 0~100）；undefined 则自动缩放
  fixedRange?: { min: number; max: number };
  refLines?: { value: number; color?: string; title?: string }[]; // 副图参考线（RSI 70/30）
  compute: (candles: Candle[], tc: ThemeColors) => Plot[];
}

export const INDICATORS: IndicatorDef[];          // 注册表（数组，顺序即菜单顺序）
export function getIndicator(key: string): IndicatorDef | undefined;
export const DEFAULT_INDICATORS: Record<IndicatorKey, boolean>; // 由 defaultOn 派生
```

**核心数学函数**（均为导出纯函数，便于单测；统一约定：暖机期不足时**不产出该点**，使曲线从首个有效点起绘，`time` 与对应 `candle.ts` 一一对应、严格升序）：

- `sma(values: number[], period: number): (number | undefined)[]` — i ≥ period-1 时取窗口均值。
- `ema(values: number[], period: number): (number | undefined)[]` — k=2/(period+1)；以前 `period` 个值的 SMA 作种子（index=period-1），其后 `ema[i]=v[i]*k + ema[i-1]*(1-k)`。
- `bollinger(closes, period=20, mult=2)` → `{ mid, upper, lower }`：mid=SMA(period)，std=窗口**总体标准差**，upper=mid+mult·std，lower=mid-mult·std。
- `macd(closes, fast=12, slow=26, signal=9)` → `{ dif, dea, hist }`：dif=EMA(fast)-EMA(slow)（从 slow-1 起有效）；dea=EMA(dif, signal)；hist=(dif-dea)·2（国内图表惯例 ×2，注释标明可调）。
- `rsi(closes, period)` → `number[]`：Wilder 平滑——首个 avgGain/avgLoss 取前 `period` 根涨跌幅简单均值，其后 `avg=(prevAvg·(period-1)+cur)/period`；`RSI=100-100/(1+avgGain/avgLoss)`；avgLoss=0 时 RSI=100。
- `kdj(candles, n=9, m1=3, m2=3)` → `{ k, d, j }`：RSV=(C-LLV(low,n))/(HHV(high,n)-LLV(low,n))·100（分母 0 时取前值 / 50）；K=2/3·prevK+1/3·RSV（种子 50）；D=2/3·prevD+1/3·K（种子 50）；J=3K-2D。
- 成交量直接取 `candle.vol`，颜色按 `c>=o` 用 `tc.long` 否则 `tc.short`。

每个 `IndicatorDef.compute` 把上述数组包装成 `Plot[]`，并把暖机期的 `undefined` 过滤掉（只 push 有效点）。MA 的 4 条线、BOLL 的 3 条线各自独立 `id`（如 `ma:5`、`boll:upper`）；MACD 产出 `[{kind:"histogram",id:"hist",...}, {kind:"line",id:"dif"}, {kind:"line",id:"dea"}]`，HIST 每根柱按 `value>=0 ? tc.long : tc.short` 写 `color`。

### 4.3 渲染：主图叠加（overlay）

在 `CandleChart.tsx` 既有结构上**新增一个 effect**，依赖 `[candles, overlayKeys, theme]`（`overlayKeys` = 启用的 overlay 指标 key 列表）：

1. 解析主题色 `tc`（复用既有 `cssVar()` 读 `--long/--short/--muted/--accent/--text`）。
2. 对每个启用的 overlay 指标 `def.compute(candles, tc)` 得到 `Plot[]`，**把每个 `plot.id` 就地改写为带前缀的命名空间 id `${def.key}:${plot.id}`**（如 `ma:5`、`boll:upper`），再把所有指标的 plots 平铺进一个数组 `overlayPlots`。命名空间化**必须在调用前完成**，使叠加层共用的 map 键与 `reconcilePlots` 的增删判定用的是**同一个 id**（修复 RV-3：避免裸 id 跨指标碰撞、或「建键用前缀 / 删除用裸 id」导致每帧误删重建）。
3. 调 `reconcilePlots(chart, overlaySeriesMapRef.current, overlayPlots, { priceScaleId: "right" })`（见 §4.5），把叠加线挂在**与 K 线同一右价轴**（蜡烛系列未显式指定 priceScaleId，默认即 `"right"`，故天然共轴），自动按命名空间 id 增 / 删 / 更新系列、`setData`。
4. overlay 系列 `lastValueVisible:false`、`priceLineVisible:false`，避免污染右轴最后值与水平线。

由于挂在 K 线右价轴，MA / BOLL 与蜡烛**天然共用价格刻度**、缩放联动零成本。BOLL 的 mid 用虚线（`lineStyle:2`），upper/lower 实线浅色；v1 **不做带状填充**（v4 无原生 band 系列，3 线已足够清晰，填充列为可选增强见 §9 R9）。

### 4.4 渲染：副图窗格（pane）与时间轴同步（核心）

`CandleChart` 的容器由「单一图表」改为**纵向 flex 列**：顶部主图 `flex-1`（最小高度保障），其下为每个启用的 pane 指标渲染一个 `<IndicatorPane key={def.key} .../>`（固定高 `140px`，带 1px 顶分隔线 + 左上角指标名 + 读数；**以 `def.key` 为稳定 key**，保证开关某指标只增删对应 pane、不抖动其余 pane——RV-6）。

**主图时间轴（RV-2）**：主图 `timeScale.visible` 取决于是否存在启用的 pane——`hasPane = enabledPaneKeys.length > 0`，主图 `timeScale.visible = !hasPane`。有 pane 时主图隐藏日期轴（标度仍参与同步、仅不渲染），由**最底部 pane 独占**日期轴（`showTimeAxis=true`）；无 pane（仅叠加 MA/BOLL 等）时主图正常显示日期轴。如此任何时刻**全栈只渲染一行日期轴**，杜绝重复。该可见性随启用 pane 数量变化用 `chart.applyOptions({ timeScale: { visible } })` 即时更新。

**`IndicatorPane.tsx`**（每实例独占一个子图）：
- `useEffect` 挂载时 `createChart(el, {...})`：`autoSize:true`、`timeScale:{ visible: showTimeAxis }`（**仅最底部 pane 的 `showTimeAxis` 为 true**，省纵向空间、避免重复日期行）、`rightPriceScale/leftPriceScale` 初始仅设 `minimumWidth` 保底下限，实际宽度由下文「动态等宽对齐」统一回写。
- 计算 `def.compute(candles, tc)` → `reconcilePlots(chart, map, plots, { priceScaleId:"right" })`（pane 各自独占 map/chart，沿用裸 `plot.id` 即唯一，无需命名空间化）。
- `def.refLines` 用 `series.createPriceLine({ price, color, lineStyle:2, ... })` 画 RSI 70/50/30 等参考线；`def.fixedRange` 通过 `autoscaleInfoProvider` 锁定值域（RSI 0~100）——该 provider **须挂到本 pane 价格轴上的全部系列**（每条都返回同一 `{ priceRange:{ minValue, maxValue } }`），否则未设 provider 的线仍按自身范围参与自动缩放、锁定失效（RV-4）。
- 向父级传入的 `sync` 协调器**注册自身 chart**；卸载时 `unregister` + `chart.remove()`（严格清理，复用 R6 防泄漏纪律）。

**时间轴双向同步（`lib/chartSync.ts`）**：

```ts
export interface SyncGroup {
  register(chart: IChartApi): () => void;   // 返回 unregister
  // 内部：任一 chart 的 visibleLogicalRange 变化 → 广播到其余 chart
}
export function createSyncGroup(): SyncGroup;
```

实现要点：
- 维护 `charts: Set<IChartApi>` 与 `applying: boolean` 防回环 guard。
- `register(chart)` 时 `chart.timeScale().subscribeVisibleLogicalRangeChange(handler)`；handler 内若 `applying` 直接 return，否则取 `range` 并对其余每个 chart 包在 `applying=true` 内 `setVisibleLogicalRange(range)`，`finally applying=false`。
- 新 chart 注册时立即对齐到组内现有可见范围（避免新开 pane 初始范围不一致）。
- 同理可选订阅 `subscribeCrosshairMove`：在源 chart 取 `param.time`，对其余 chart `setCrosshairPosition(NaN, time, anySeries)`（无 time 时 `clearCrosshairPosition()`），实现跨图十字光标贯穿（**增强项**，先保证时间轴同步这一必选项）。

**价格轴等宽对齐（动态测量，RV-1）**：主图右价轴宽度随价格量级（默认 BTC「63,000.0」≈6 万）变化，副图数值小（如「70」「-120」）。⚠️ **不能只靠静态 `minimumWidth` 常量**：v4.2 typings 对该字段明确写道「`minimumWidth` 只是下限，价格轴内容需要更多空间时会被超过」（`PriceScaleOptions.minimumWidth`，typings.d.ts:2830）。BTC 量级下主图右轴必然 > 64px，而副图停在下限 → 仍然错位。故采用**动态等宽对齐**：

1. 所有 chart（主 + 副）的 `leftPriceScale` / `rightPriceScale` 先设保底 `PRICE_SCALE_MIN_WIDTH = 64` 下限。
2. 每次 `candles` 变更 / 主题切换 / 容器尺寸变化（ResizeObserver）后，于下一帧（数据已布局）用主图 `chart.priceScale("left").width()` 与 `chart.priceScale("right").width()`（`IPriceScaleApi.width()`，v4.2 已确认可用，typings.d.ts:1674）读出主图左右价轴**真实像素宽**。
3. 取主图与各副图同侧的最大值，分别回写为**所有 chart**对应价轴的 `minimumWidth`（`priceScale(id).applyOptions({ minimumWidth })`），使所有图同侧价轴等于同一像素宽 → 各图绘图区左右边缘一致 → 时间轴像素级对齐。
4. 该对齐逻辑收敛进 `chartSync`（新增 `alignPriceScaleWidths(charts)` 辅助）或 `CandleChart` 的一个 effect，主图与 pane 增删时一并重算。

主图左轴用于净值（equity，`leftPriceScale.visible:true` 恒在），副图左轴空置但按同宽预留 → 留白可接受（见 §9 R10）。注意 `width()` 仅在价格轴可见时返回非 0，主副图相关价轴均可见，满足前提。

### 4.5 通用系列协调 `reconcilePlots`（`components/Chart/plots.ts`）

```ts
export function reconcilePlots(
  chart: IChartApi,
  map: Map<string, ISeriesApi<"Line" | "Histogram">>,
  plots: Plot[],
  opts: { priceScaleId?: string },
): void;
```
- `nextIds = new Set(plots.map(p => p.id))`；遍历 `map` 删除已不在 `nextIds` 的系列（`chart.removeSeries` + `map.delete`，防泄漏 R6）。**id 契约（RV-3）**：`reconcilePlots` 对 id 语义不可知，只要求「同一 `plot.id` 唯一对应 map 中同一系列」。叠加层多指标共用一个 map，故调用方（§4.3）须**先**把 `plot.id` 命名空间化为 `${def.key}:${plot.id}` 再传入；pane 各自独占 map/chart，裸 id 即唯一。**map 键、`nextIds`、建系列三处必须用同一 id**，严禁「建键用前缀、删除用裸 id」。
- 对每个 plot：`map` 无则按 `kind` 建系列（line→`addLineSeries`，histogram→`addHistogramSeries`，带 `priceScaleId`、`color/title/lineWidth/lineStyle`、`lastValueVisible:false`、`priceLineVisible:false`）；有则 `applyOptions`（颜色 / 标题，应对主题切换）。
- `series.setData(plot.data)`（line 用 `{time,value}`，histogram 用 `{time,value,color}`）。全量 `setData` 在 ~300–600 根规模下成本可忽略（见 §9 R3）。

这一函数同时服务主图 overlay 与每个 pane，是「注册表驱动、零渲染改动加指标」的关键。

### 4.6 工具栏「指标」下拉弹窗（`IndicatorMenu.tsx`）

- 触发：`ChartToolbar` 在周期（BARS）按钮组之后插入 `<IndicatorMenu/>`——一个复用既有 `Button`（`variant="default" size="sm"`）的「📊 指标」按钮，右上角小徽标显示已开启数量；`aria-haspopup="true"`、`aria-expanded`。
- 弹窗：绝对定位于按钮下方的卡片（`card` 样式：`--surface`/`--border`/`shadow-soft`，宽 `280px`，`max-height` + 纵向滚动）。按类别分组标题「主图叠加」「副图指标」，每项一行：左为 `label` + `paramsHint` + 描述副文本，右为 `Switch`（开关）。
- 状态：从 `useAppStore` 读 `indicators`，点击行或 Switch 调 `toggleIndicator(key)`；底部「恢复默认」调 `resetIndicators()`、「全部关闭」清空。
- HCI 最佳实践：`role` 用 `switch` + `aria-checked`；Space/Enter 切换；`Esc` 关闭并把焦点还给触发按钮；`mousedown` 监听**点击外部关闭**；再次点击触发按钮切换开合；首项可聚焦。小屏可降级为底部抽屉（增强项）。

### 4.7 状态与持久化（`lib/store.ts`）

- 新增 `indicators: Record<IndicatorKey, boolean>`，初值 `DEFAULT_INDICATORS`（由注册表 `defaultOn` 派生：`{ma:true, boll:true, macd:true, ema:false, rsi:false, kdj:false, vol:false}`）。
- 新增 `toggleIndicator(key)`、`setIndicators(rec)`、`resetIndicators()`。
- `persist.partialize` 增 `indicators`；`version` 由 1 升 2，加 `migrate(persisted, v)`：`v<2` 时返回 `{ ...persisted, indicators: DEFAULT_INDICATORS }`（即便不写 migrate，zustand 默认浅合并也会把缺失的 `indicators` 落回初值，migrate 仅为显式稳妥）。
- 沿用 F3 既有 `skipHydration:true` + 挂载后 `useAppStore.persist.rehydrate()`，**不引入新的 hydration 告警**：首帧以默认指标渲染、补水后恢复（一帧闪烁可接受，与既有选择态一致）。

### 4.8 实时与主题联动

- **实时**：`candles` 含迭代 3 的成形根（`forming`），每 tick 变化 → `CandleChart` 的 overlay effect 与各 `IndicatorPane` 的 compute effect（均依赖 `candles`）重算并 `setData`，**最右一根指标值随真实行情即时更新**。最后一根因成形未收口属「暂定值」（与价格本身一致），收口后定格——属正确语义，§9 R11 注明。
- **主题**：overlay effect 与 pane effect 均把 `theme` 纳入依赖；切主题时重算 `tc` 并 `reconcilePlots` 重着色（MACD/VOL 的涨跌色取 `--long/--short`，与蜡烛一致）。复用既有主题切换 effect 不冲突。

### 4.9 时序（默认进入页面）

1. `page.tsx` 拉 300 根真实历史 → `candles` 就绪 → 传入 `CandleChart` + `indicators`（默认 MA/BOLL/MACD）。
2. 主图建蜡烛 + 叠加 MA(5/10/20/60) + BOLL(20,2) 三线。因默认存在 pane（MACD），主图隐藏自身时间轴（RV-2）。
3. 因 MACD 开启，主图下方挂 1 个 `IndicatorPane`（MACD，最底部 → `showTimeAxis=true` 独占日期轴），注册进 sync 组；数据布局后执行**动态等宽对齐**（RV-1）使主副图左右边缘 / 时间轴像素对齐。
4. `marketWS` 推成形根 → `candles` 更新 → MA/BOLL/MACD 最右值实时跳动。
5. 用户点「指标」→ 弹窗开 RSI → store 更新 → 新增一个 RSI pane、自动对齐；关 MACD → 移除其 pane、释放系列与 chart；选择态落 localStorage。
6. 缩放 / 平移主图 → sync 组把可见范围广播到所有 pane → 主副图时间轴一致。

---

## 5. 文件 / 模块变更计划

| 文件 | 动作 | 一句话意图 |
|---|---|---|
| `apps/web/lib/indicators.ts` | **新增** | 指标纯函数（SMA/EMA/BOLL/MACD/RSI/KDJ）+ `Plot` 抽象 + 声明式注册表 `INDICATORS` + `DEFAULT_INDICATORS`。 |
| `apps/web/lib/indicators.test.ts` | **新增** | 对各数学函数与暖机 / 边界（空数组 / 单点 / 周期不足）做 vitest 单测，含已知数值断言。 |
| `apps/web/lib/chartSync.ts` | **新增** | `createSyncGroup()`：主 + 副图时间轴（可选十字光标）双向同步，含防回环 guard；并提供 `alignPriceScaleWidths()` 动态等宽对齐辅助（RV-1）。 |
| `apps/web/lib/chartSync.test.ts` | **新增（可选）** | 用桩 chart 断言广播只触发一次、guard 防回环。 |
| `apps/web/components/Chart/plots.ts` | **新增** | `reconcilePlots()`：按 `Plot[]` 泛化增 / 删 / 更新 line·histogram 系列（主图与 pane 共用）。 |
| `apps/web/components/Chart/IndicatorPane.tsx` | **新增** | 单个副图指标的独立子图组件：建 chart、compute、reconcile、参考线、注册 sync、卸载清理。 |
| `apps/web/components/Chart/IndicatorMenu.tsx` | **新增** | 工具栏「指标」按钮 + 下拉弹窗（分组、Switch、恢复默认、键盘 / 点击外部关闭、ARIA）。 |
| `apps/web/components/Chart/CandleChart.tsx` | **修改** | 新增 `indicators` prop；容器改纵向 flex；加 overlay 指标 effect（reconcilePlots 右价轴，id 命名空间化 RV-3）；按启用的 pane 指标渲染 `IndicatorPane`（稳定 `key={def.key}` RV-6）；接入 sync 组；**有 pane 时隐藏主图时间轴 RV-2**；数据/尺寸变化后**动态等宽对齐**主副价轴 RV-1。 |
| `apps/web/components/Chart/ChartToolbar.tsx` | **修改** | 在 BARS 周期组后插入 `<IndicatorMenu/>`。 |
| `apps/web/lib/store.ts` | **修改** | 新增 `indicators` 态 + `toggleIndicator`/`setIndicators`/`resetIndicators`；`partialize` 加 `indicators`；`version`→2 + `migrate`。 |
| `apps/web/app/page.tsx` | **修改** | 从 store 读 `indicators`，透传给 `CandleChart`。 |
| `apps/web/components/ui/index.tsx` | **修改** | 新增 `Switch` 原子组件（`role="switch"` + `aria-checked`，复用设计 token）。 |
| `apps/web/README` / 根 `README.md` | **修改（可选）** | 「新增功能用法」补一段技术指标说明（可由代码节点顺带）。 |

> 下游 Subtask #2 按「先纯函数 + 单测 → 再 store → 再 plots/sync → 再 CandleChart/Pane → 最后 Menu/Toolbar 接线」顺序实施，可分步自验。

---

## 6. 接口设计（模块 / 组件接口）

> **本特性零后端接口改动**——不新增 / 不修改任何 REST / WS 端点，不改 `/candles` 请求或响应体。以下为**前端内部模块接口**。

**`lib/indicators.ts`** — 见 §4.2 的 `Plot` / `IndicatorDef` / `INDICATORS` / 数学函数签名（`sma`/`ema`/`bollinger`/`macd`/`rsi`/`kdj` 均导出便于单测）。

**`lib/store.ts`（AppState 增量）**：
```ts
indicators: Record<IndicatorKey, boolean>;
toggleIndicator: (key: IndicatorKey) => void;
setIndicators: (rec: Record<IndicatorKey, boolean>) => void;
resetIndicators: () => void;     // → DEFAULT_INDICATORS
```

**`CandleChart` Props（在既有 Props 上新增）**：
```ts
indicators: Record<IndicatorKey, boolean>;   // 当前开关态（来自 store）
```

**`IndicatorPane` Props**：
```ts
{ candles: Candle[]; def: IndicatorDef; theme: "light" | "dark";
  sync: SyncGroup; showTimeAxis: boolean; }   // showTimeAxis 仅最底部 pane 为 true
```

**`IndicatorMenu` Props**：无（直接消费 `useAppStore`）。

**`reconcilePlots` / `createSyncGroup`**：见 §4.5 / §4.4。

---

## 7. 数据模型（内存形态）

无数据库改动、无后端 schema。新增**前端内存 / 持久化形态**：

- `Pt = { time: UTCTimestamp; value: number }`；`HistPt = Pt & { color?: string }`。
- `Plot`（line | histogram 判别联合，见 §4.2）——指标计算到渲染的唯一中间表示。
- `IndicatorDef` / `INDICATORS`（注册表，纯描述 + compute 函数）。
- `store.indicators: Record<IndicatorKey, boolean>`——**唯一需要持久化的新增态**，落 `localStorage` key `aragon-ui`（持久化版本 v2）；持久化形如 `{"instId":...,"bar":...,"indicators":{"ma":true,"boll":true,"macd":true,"ema":false,"rsi":false,"kdj":false,"vol":false},...}`。
- 运行期不持久化：各 `ISeriesApi` / `IChartApi` 句柄、`reconcilePlots` 的 `Map`、sync 组 `Set`（均生命周期内 ref，卸载清理）。

所有指标 `time` 均等于其来源 `candle.ts`（秒级 UTC），严格升序、唯一——继承 `candles` 已由 `mergeUnique` 保证的约束（放行条件③）。

---

## 8. 测试与验收标准

### 8.1 单元测试（`indicators.test.ts`，vitest，与 `compare.test.ts` 同风）
- `sma([1..5],3)` → `[u,u,2,3,4]`（前两位 undefined）。
- `ema` 种子 = 前 period SMA，递推值与手算一致；`bollinger` mid/std/上下轨在固定序列上数值断言。
- `macd`：DIF 从 slow-1 起非空、HIST=(DIF-DEA)·2 符号正确。
- `rsi`：全程上涨 → 趋近 100；全程下跌 → 趋近 0；Wilder 平滑递推断言。
- `kdj`：RSV 分母为 0（横盘）不产生 NaN；K/D 种子 50。
- **边界**：空数组返回空 `Plot[]`；长度 < 最小周期时只产空数据不抛错；输出 `time` 严格升序且与输入 ts 对齐。
- （可选）`chartSync.test.ts`：桩 chart 验证一次 range 变化只回写其余图各一次、guard 阻断回环。

### 8.2 静态 / 构建门
- `cd apps/web && pnpm test`（vitest 全绿，含新用例）。
- `pnpm exec tsc --noEmit` / `next build` 通过（无类型错误、无 SSR 报错——`CandleChart` 仍 `ssr:false`）。
- CI（`.github/workflows/ci.yml`）既有前端 job 自动覆盖上述。

### 8.3 人工验收（真实数据）
1. 默认进入：主图显示 MA(5/10/20/60) + BOLL 三轨；下方有 MACD 副图（HIST 涨绿跌红 + DIF/DEA）。
2. 点「指标」弹窗：开 RSI → 新增 RSI 副图（含 70/50/30 参考线）；开 VOL/KDJ 同理；关 MACD → 其副图消失。
3. **联动 + 等宽（RV-1/RV-2）**：在主图缩放 / 平移 / 拖动时间轴，所有副图时间轴**同步一致**；**默认 BTC（约 6 万价格）下主副图左右边缘对齐无错位、全栈只有一行日期轴**；再切到价格量级差异大的标的（如 SOL）复验一次对齐；十字光标贯穿（若实现增强项）。
4. **实时**：1m 周期下最右一根 K 线跳动时，MA/BOLL/MACD 最右值随之更新。
5. **持久化**：开关若干指标后刷新页面，选择态恢复。
6. **主题**：切暗色，指标颜色随之刷新、可读。
7. **无回归 / 无泄漏**：回测 markers、净值、对比叠加、日期跳转、滚动加载历史均照常；反复开关指标 / 切标的，DevTools 无系列 / chart 泄漏，无 console 报错。
8. **可达性**：键盘可开合弹窗、Tab 遍历、Space 切换、Esc 关闭、点击外部关闭。
9. **VOL 真实量（RV-5）**：开启 VOL 后柱状成交量与真实行情一致、非恒 0；若 `/candles` 未回填 `vol`，按 §11 登记，不以假数据冒充。

### 8.4 Definition of Done
- `spec.md` 存在且 `git status` 可见（本节点）。
- （下游）上述文件全部落地，单测 / tsc / build 全绿，人工验收 1–8 通过。

---

## 9. 风险与缓解

| # | 风险 | 缓解 |
|---|---|---|
| R1 | v4.2 无原生多窗格（panes v5 才有），副图实现复杂 | 采用 v4 标准方案：多 `createChart` 堆叠 + `chartSync` 时间轴双向同步 + **动态等宽对齐**（`priceScale().width()` 测主图实际轴宽回写各图 `minimumWidth`，RV-1，静态常量不足以对齐 BTC 量级价轴）；已核实所需 API（`addHistogramSeries`/`setVisibleLogicalRange`/`createPriceLine`/`minimumWidth`/`width()`/`setCrosshairPosition`/`autoscaleInfoProvider`）在 v4.2.3 typings 全部可用。 |
| R2 | lightweight-charts 要求 `time` 严格升序且唯一，否则抛错 | 指标 `time` 直接复用 `candles` 的 ts（已 `mergeUnique` 去重升序）；暖机期不产点而非产 NaN；`reconcilePlots` 不重排。 |
| R3 | 实时每 tick 全量重算所有指标的性能 | n≈300–600、指标 ≤7、复杂度 O(n·k)，每秒级一次，远低于一帧预算；先全量 `setData` 保正确，必要时再以 `series.update()` 只更最后一点（增强项）。 |
| R4 | 子图 chart / 系列未释放导致内存泄漏 | 沿用既有 R6 纪律：overlay effect 与 `IndicatorPane` 卸载严格 `removeSeries`/`chart.remove()`/清 Map；`reconcilePlots` 删除离场系列。 |
| R5 | 时间轴同步回环（A 改→B 改→A…）卡死 | `createSyncGroup` 用 `applying` guard：广播期间忽略他图回调，`finally` 复位。 |
| R6 | 主题切换后指标旧色残留 | overlay/pane effect 把 `theme` 纳依赖，重算 `tc` 并 `applyOptions` 重着色；涨跌色统一取 `--long/--short`。 |
| R7 | persist 加新字段导致旧 localStorage 迁移问题 | `version`→2 + `migrate` 显式补 `DEFAULT_INDICATORS`；即便缺省，zustand 浅合并也回落初值；未知 key 安全忽略。 |
| R8 | SSR / hydration 不一致告警 | `CandleChart` 仍 `ssr:false`；`IndicatorMenu` 为客户端组件；选择态沿用 F3 `skipHydration` + 挂载 `rehydrate`，首帧默认指标→补水（一帧闪烁，零告警）。 |
| R9 | BOLL 带状填充 v4 无原生 band | v1 用 upper/mid/lower 三线（mid 虚线）已清晰；带状填充用 Area trick 列为可选增强，不阻断。 |
| R10 | 副图过多在小屏拥挤；副图空置左轴留白 | pane 固定 `140px` + 主图 `flex-1` 最小高度；副图数量即开启的 pane 指标数，用户自控；空左轴留白为等宽对齐的可接受代价；小屏抽屉式弹窗为增强项。 |
| R11 | 最右成形根的指标值为「暂定」 | 与价格本身一致语义：成形未收口即暂定、收口定格；不视为缺陷，UI 不另作标注（与迭代 3 实时根策略一致）。 |
| R12 | 工具栏元素增多导致换行 / 拥挤 | 工具栏既有 `flex-wrap`，「指标」按钮用 `size="sm"` 紧凑样式；弹窗承载指标明细，按钮本身仅图标 + 计数徽标。 |

---

## 10. 设计取舍（备选与理由）

- **副图：多图同步 vs 单图 scaleMargins 分带**。单图内用不同 `priceScaleId` + `scaleMargins` 把振荡指标压到底部一带，省去时间轴同步，但**多个振荡指标（MACD+RSI+KDJ+VOL）同时开启时需反复细分底部带宽**，网格交叠、刻度拥挤、可读性差，无法满足「适配正确、完美」。**采用多图同步**：每个振荡指标独占整带、各自 y 轴、专业呈现，代价是时间轴 / 等宽同步代码（已用 `chartSync` + `minimumWidth` 收敛）。
- **指标计算放前端 vs 后端**。后端算可省前端 CPU，但要新增接口、与「实时成形根」同步困难、来回请求增延迟。**采用前端纯函数计算**：与 `candles`（含成形根）天然同源、零接口改动、可纯单测、即时联动。
- **参数自定义 UI**。v1 暂不做（Non-Goal），但注册表已留 `paramsHint` 与 compute 形参位，后续加「参数编辑」只需扩展弹窗与 store，不改数学与渲染骨架。

---

## 11. 实施过程发现的方案缺陷（待 Subtask #2 回填）

> 下游代码节点若发现本方案与实际源码 / 库行为不符（如 `minimumWidth` 实际对齐效果、`autoscaleInfoProvider` 锁值域细节、十字光标同步 API 行为），在此登记并就地修正，不静默偏离。

**评审（Subtask #1）预登记、需实现期实测确认的点：**
- **RV-1 动态等宽**：`priceScale().width()` 的有效读取时机——需在数据布局后读（可能要 `requestAnimationFrame` 或首次 `subscribeVisibleLogicalRangeChange` 回调后测）；若 v4.2 下读数有 1 帧延迟，按需补一次重对齐。
- **RV-4 fixedRange**：`autoscaleInfoProvider` 对同一 pane 多系列锁定值域的实测行为（确认须每条系列都挂 provider）。
- **RV-5 VOL**：确认后端 `/candles` 响应含真实非零 `vol`；如缺失在此登记并按真实数据原则处理（不可造假量）。

（其余暂无）

---

## 评审结论（Review Verdict，Subtask #1）

**结论：有条件通过（Approved with Conditions）。**

**总体评价**：方案在**可行性 / 一致性 / 规模适配**三维度上质量高——逐项对照工作目录真实源码核验，所依赖的库 API（`minimumWidth`/`width()`/`addHistogramSeries`/`setVisibleLogicalRange`/`createPriceLine`/`autoscaleInfoProvider` 等）在 `lightweight-charts@4.2.3` typings 中均确实存在；与既有约定（`cssVar()` 主题色、`reconcile`+R6 卸载清理纪律、`aragon-ui` 持久化、纯函数+`*.test.ts`、设计 token、`ssr:false`）契合良好；严守 5 条放行条件（真实数据 / 不动后端凭据 / 秒级 UTC ts / 锁 `^4.2` / 不碰 SimBroker·策略），**零后端、零 DB 改动**，规模适配用户诉求（默认 MA+BOLL+MACD + 下拉弹窗开关 + 其它指标补全），无过度工程。

**已就地修复（无遗留 P0/P1）**：
- **RV-1（P1，可行性/正确性）**：静态 `minimumWidth:64` 无法对齐 BTC 量级价轴 → 改为 `priceScale().width()` 动态测量回写。
- **RV-2（P1，完整性/HCI）**：主图自身时间轴在有 pane 时会与底部 pane 重复 → 主图 `timeScale.visible=!hasPane`，全栈只一行日期轴。
- **RV-3（P1，一致性）**：`reconcilePlots` id 契约自相矛盾 → 统一为调用方命名空间化、三处用同一 id。

**放行条件（实现期须满足，均为非阻断 P2，列入 §8.3 / §11 跟踪）**：
1. **RV-1 落地实测**：在默认 BTC 标的下肉眼/像素验证主副图左右边缘对齐、时间轴无错位，并切换到价格量级差异大的标的复验；确认 `width()` 的读取时机（必要时 `requestAnimationFrame` 补一次重对齐）。
2. **RV-5 VOL 真实数据**：开工前/中确认后端 `/candles` 回填真实非零 `vol`；若缺失，按真实数据原则在 §11 登记处理，**不得以零或伪造量充数**。
3. **RV-4 fixedRange**：实现 RSI 等固定值域时，将 `autoscaleInfoProvider` 挂到该 pane 的全部系列。

满足以上 3 条放行条件即视为完全通过，可进入 Subtask #2 代码开发。

- **评审人 / 角色**: Senior Reviewer (Anthropic Engineering)
- **评审日期**: 2026-06-13
- **评审结果**: 有条件通过 → 文档升至 **v2**
