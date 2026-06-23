# 诊断报告：K 线主图与副图技术指标存在「固定一段（≈一个横向格子）」横向错位，十字光标不对应

> Bug slug: `chart-indicator-constant-bar-offset`
> 作者：Subtask #0（问题定位与分析 / 资深调试工程师）
> 日期：2026-06-23
> 状态：根因已定位（含代码证据），给出候选修复对比与推荐方案
> 关联前序诊断：[`chart-indicator-zoom-misalignment`](../chart-indicator-zoom-misalignment/analysis.md)（缩放累积错位，已修）、[`chart-crosshair-sync-value-is-null`](../chart-crosshair-sync-value-is-null/analysis.md)（十字光标 Value is null 崩溃）

---

## 1. 问题描述（Problem description）

工作台中间图表区由 **K 线主图** 与其下方约 20% 高的 **技术指标副图**（MACD / RSI / KDJ / VOL）垂直堆叠组成。最近为修复「缩放后两图右价轴宽度漂移导致累积错位」（commit `0216201`）与「副图恒显空左轴造成的固定横向错位」（工作区未提交改动：`IndicatorPane` 新增 `showLeftAxis`）做了两轮修复，但用户反馈**问题仍未修好**：

- **症状**：主图 K 线与下方副图指标在横轴上**整体错位一段固定距离**，这段距离约等于主图中**一个横向格子（相邻两条竖直网格线之间）**的宽度。
- **关键特征（与已修的缩放错位区分）**：本错位是**与缩放无关的常量**——它不随缩放程度逐根累积放大（那是已修的 `chart-indicator-zoom-misalignment`），而是**始终偏移固定的「若干根 K 线」**。
- **十字光标**：鼠标在主图上移动时，主图的竖直虚线与副图的竖直虚线**不在同一水平位置**，偏移量与上面的整体错位一致——即同一时间点在两图被画到了不同的 x 像素。

直觉上这是「两图把**同一个被同步的逻辑范围**映射到了**各自不同的数据起点**」的指纹：不是像素几何（价轴宽度）问题，而是**横轴数据域（logical index 原点）不一致**。前两轮修复都只动了「绘图区几何（价轴宽度 / 左轴可见性）」，没有触碰「两图的逻辑索引原点是否对齐」，所以错位仍在。

---

## 2. 复现步骤（Reproduction steps）

前置：本地已能连通 OKX（真实数据硬约束），Node ≥ 18.18 + pnpm 已就绪。

1. 启动后端 + 前端：

   ```bash
   pnpm dev          # 后端 :18760 + 前端 :18761，等价 python scripts/dev.py up
   ```

2. 浏览器打开 `http://localhost:18761`。
3. 默认会加载一个标的并显示 **MACD** 副图（注册表中 `macd.defaultOn = true`，见 `apps/web/lib/indicators.ts:312`）。
4. **观察整体错位**：把主图任意一根 K 线的垂直中线，与下方 MACD 柱状图对应位置对比——同一时间点的 K 线与 MACD 柱**不在同一 x**，副图整体相对主图**左移了固定的一段**（约一个竖网格格子）。
5. **观察十字光标**：把鼠标移到主图某根 K 线上，主图出现竖直虚线；同时副图也出现一条贯穿的竖直虚线，但**两条虚线不在同一水平位置**，偏移量与第 4 步一致。
6. **关键判别实验（用于确认根因，强烈建议执行）**：在右侧指标菜单切换当前副图指标，观察错位幅度变化：

   | 当前副图指标 | 该指标最早有效点的暖机长度（被丢弃的前导根数） | 预期横向错位 |
   |---|---|---|
   | **VOL**（成交量） | 0（每根 K 线都有成交量，无暖机） | **几乎不错位** ✅ |
   | **RSI**（周期 6/12/24） | 6（最短的 RSI6 首个有效点在 index=6） | 小幅错位（约 6 根） |
   | **KDJ**（n=9） | 8（首个有效点 index=n−1=8） | 中等错位（约 8 根） |
   | **MACD**（12,26,9） | 25（DIF 首个有效点 index=slow−1=25） | 明显错位（约 25 根，默认态） |

   **现象**：错位幅度**随所选指标的暖机长度成正比变化**，VOL 基本对齐、MACD 错位最大。这正是「逻辑索引原点不一致」的判定性证据——若是价轴几何问题，错位幅度应与指标暖机长度无关。

> 最小复现要点：**只要副图当前指标的最早有效点不是第一根 K 线**（即丢弃了暖机前导点），副图的 logical index 0 就不再等于主图的 candle[0]，被同步的同一逻辑范围即被映射到不同 x → 常量横向错位。VOL（零暖机）是对照组。

---

## 3. 根因分析（Root cause analysis，含证据）

### 3.1 架构事实：主/副图是相互独立的 chart，仅靠**逻辑索引**同步横轴

lightweight-charts v4.2（`apps/web/package.json:16` → `"lightweight-charts": "^4.2.0"`）不支持原生多窗格（panes 为 v5 特性），本项目用**多个 `createChart` 垂直堆叠**模拟副图：

- 主图：`apps/web/components/Chart/CandleChart.tsx:161` `createChart(el, {...})`
- 副图：`apps/web/components/Chart/IndicatorPane.tsx:65` `createChart(el, {...})`

两图横轴的唯一同步通道是 `chartSync.ts` 里的**可见逻辑范围广播**（index 维度），全仓库再无任何按「时间」对齐横轴的代码（`grep setVisibleRange|setVisibleLogicalRange apps/web` 仅命中下列三处，均为 logical 维度）：

- 缩放/平移广播：`apps/web/lib/chartSync.ts:54` `other.timeScale().setVisibleLogicalRange(range)`（订阅点 `:65`）。
- 新副图注册时把主图当前逻辑范围拷给自己：`apps/web/lib/chartSync.ts:101-104` `const r = ref.timeScale().getVisibleLogicalRange(); ... chart.timeScale().setVisibleLogicalRange(r)`。
- 主图自身的范围回调只做「滚到左边界 → 增量加载」，不参与对齐：`apps/web/components/Chart/CandleChart.tsx:211-219`。

**关键库语义**：lightweight-charts 的 logical index 是**相对每个 chart 自身数据数组的下标**，index 0 = 该 chart 时间轴上的**第一个数据点**。两个独立 chart 的 logical index **互不共享**。因此 `setVisibleLogicalRange({from,to})` 让每图各自把「自己的第 from..to 个点」铺满绘图区——**只有当两图的「第 0 个点」是同一时间戳时，同一 from..to 才落在同一时间/同一 x**。

### 3.2 致命点：副图指标系列**丢弃暖机前导点**，导致其 index 0 晚于主图 candle[0]

主图 K 线系列喂入的是**全量 candles**（无丢弃）：

- `apps/web/components/Chart/CandleChart.tsx:305-317` —— `series.setData(data)`，`data = candles.map(...)`，覆盖 candle[0..N−1]。故**主图 index 0 = candle[0]**。

而副图每条指标系列在 `compute` 阶段用 `lineData` / 直方构造时**显式跳过暖机期的 `undefined`，曲线从首个有效点起绘**：

- 通用折线：`apps/web/lib/indicators.ts:208-216`
  ```ts
  function lineData(candles, vals) {
    const out = [];
    for (let i = 0; i < candles.length; i++) {
      const v = vals[i];
      if (v === undefined || Number.isNaN(v)) continue;   // ← 丢弃暖机前导点
      out.push({ time: candles[i].ts, value: v });
    }
    return out;
  }
  ```
- MACD 直方同样丢弃：`apps/web/lib/indicators.ts:318-326`（`if (v === undefined || Number.isNaN(v)) continue;`）。
- 各指标暖机长度（首个有效点下标）：SMA/EMA `period−1`、`bollinger` 同 SMA、`macd` 的 DIF 在 `slow−1=25`（`apps/web/lib/indicators.ts:113-119`）、`rsi` 在 `period`（`:160`，RSI6→6）、`kdj` 在 `n−1=8`（`:187`）。
- **唯一例外 VOL**：每根 K 线都有成交量，无暖机、全量：`apps/web/lib/indicators.ts:396-403`。

副图 chart 的时间轴 = 其上所有系列时间点的并集（`reconcilePlots` 把每条 plot `setData`，`apps/web/components/Chart/plots.ts:77-81`）。因此副图的**最早时间点 = 该 pane 内最早起绘的系列的首个有效点**：MACD pane = candle[25]、RSI pane = candle[6]、KDJ pane = candle[8]、VOL pane = candle[0]。

于是除 VOL 外，**副图 index 0 = candle[W]（W 为暖机长度），而主图 index 0 = candle[0]**。

### 3.3 几何闭合：常量错位 = 暖机根数 W

两图被 `setVisibleLogicalRange` 强制贴到**同一逻辑范围 `[from,to]`**，绘图区几何（价轴宽度 / 左轴可见性）已被前两轮修复拉平（见 §3.5），故屏幕 x 是 logical index 的**同一线性函数**。但在同一屏幕 x（对应 logical index L）处：

- 主图画 **candle[L]**（主图 index L = candle L）；
- 副图画 **candle[W+L]**（副图 index L = candle W+L）。

要找主图 candle[C] 与副图 candle[C] 对齐的位置：主图 candle[C] 在屏幕 index C；副图 candle[C] 在副图 index C−W → 屏幕 index C−W。**两者相差 W 个 index（= W 根 K 线）**。这是一段**与缩放无关、固定 W 根**的横向偏移——完全对应用户「固定宽度、约一个格子」的描述（默认 MACD 时 W=25，在默认 bar spacing 下约一个竖网格格子量级）。

**十字光标同源**：主图广播 `setCrosshairPosition(NaN, param.time, 副图系列)`（`apps/web/lib/chartSync.ts:79`，时间维度）。副图把该 `time` 画在自己 time→x 映射的位置；由于副图 time↔index 整体偏移了 W，同一 `time` 在副图落到的 x 比主图偏 `W/(to−from)·plotW` 像素 → 两条竖直虚线不重合，偏移量恰等于 §3.3 的整体错位。

**结论（根因）**：
> 主/副图横轴**仅按 logical index 同步**（`setVisibleLogicalRange`，`chartSync.ts:54/101-104`），而副图指标系列**丢弃了暖机前导点**（`indicators.ts:212` 等），使副图的 index 0（最早有效点）晚于主图的 candle[0]（全量 K 线，`CandleChart.tsx:315`）。两图「第 0 个点」的时间戳不同，被同步的同一 `[from,to]` 便映射到不同时间/不同 x，产生**固定 W 根 K 线**的横向错位；时间维度的十字光标贯穿因此同样偏移 W 根。前两轮修复只对齐了绘图区几何（价轴宽度、左轴可见性），**从未对齐两图的逻辑索引原点**，故常量错位残留。

### 3.4 为什么默认态尤其明显、且「固定」

默认开启的副图是 **MACD**（`indicators.ts:312` `defaultOn: true`），其暖机 W=25 为四个副图中最大，且与缩放无关恒为 25 根，故用户看到的是一段「固定宽度」的明显错位。切到 VOL（W=0）会基本消失，这与用户「固定」描述一致（用户多在默认 MACD 下观察）。

### 3.5 反证排除（确认不是已修的几何问题，也不是数据时间戳错误）

- **不是右价轴宽度漂移（已修的缩放错位）**：那条路径已由 commit `0216201` 修复——可见范围变化时 rAF 合并重对齐左右价轴等宽（`chartSync.ts:28-40` `scheduleAlign` → `realignPriceScaleWidths`）。若错位源于价轴宽度，则其幅度应**随缩放累积变化**；而本错位**固定 W 根、与缩放无关**，且**随所选指标暖机长度成正比**（§2 判别实验），二者特征正交，故非同一问题。
- **不是空左轴预留（已修的左轴不对称）**：副图左轴已改为跟随主图 `showLeftAxis=hasEquity`（未提交改动：`IndicatorPane.tsx` 新增 `showLeftAxis`，`CandleChart.tsx:520` 传入 `hasEquity`），默认无净值时两图左轴皆隐藏，左缘几何已对齐。若仍是左轴问题，VOL 也应错位；但 VOL 基本对齐，证伪。
- **不是数据/时间戳错误**：K 线与指标都以同一 `candles` 的同一 `ts` 为横轴键（主图 `CandleChart.tsx:308-315`；副图经 `plots.ts` reconcile 用 `CandleChart.tsx:456` 传入的同一 `candles` 计算）。数据正确，错的是「不同长度的数据 + 仅按 index 同步」这一映射前提。

---

## 4. 影响面（Impact surface）

- **受影响功能**：中间图表区中**任意时刻开启了副图指标**（默认 MACD，及 RSI / KDJ）的视图。涉及 `CandleChart.tsx`（主图 + 副图容器）、`IndicatorPane.tsx`（副图）、`indicators.ts`（暖机丢弃）、`chartSync.ts`（仅 index 同步）。
- **受影响用户**：所有看 K 线 + 副图指标的用户（核心看盘路径），无需任何缩放即可见——比已修的缩放错位影响面更基础。
- **错位幅度**：等于当前副图最早起绘系列的暖机根数 W：MACD≈25、KDJ≈8、RSI≈6、VOL≈0。默认 MACD 最严重。
- **不影响**：纯主图（未开副图）视图；主图叠加指标（MA/EMA/BOLL）虽同样丢弃暖机，但与 K 线**同图共用一条时间轴**（`CandleChart.tsx:456-468` reconcile 到主图右价轴），其 index 由全量 candleSeries 锚定，故不产生跨图错位。后端 / 数据正确性 / 回测模拟逻辑均不受影响（纯前端横轴映射问题）。
- **严重度**：功能性可用，但**读图被系统性误导**——指标对应的 K 线被读错整整 W 根，对交易看盘工具属高可感知缺陷，且十字光标贯穿这一核心交互直接失效。

---

## 5. 候选修复方案（Candidate fixes）

> 本节不写修复代码，仅做方案比较，供 Subtask #2 实施参考。

| 方案 | 思路 | 侵入性 | 风险 | 工作量 | 备注 |
|---|---|---|---|---|---|
| **A（推荐）** 用 whitespace 把副图系列补齐到全量 K 线域 | 让每条副图系列覆盖**全部 candle 时间**：暖机位不再 `continue` 丢弃，而是 push 一个**仅含 `time`、无 `value` 的 whitespace 点**（库会建立时间轴点但不绘制）。等价做法：给每个 pane 额外挂**一条覆盖全量 candle 时间的隐藏 whitespace 锚系列**。两者都使副图 index 0 = candle[0]，与主图逻辑索引一一对齐 | 低 | 低 | 小 | 直击根因；保留现有 logical-index 同步与全部几何修复。注意**必须全量补齐**（每根缺失 candle 都要有 whitespace 点），仅在首根加一个 whitespace 锚不够——中间缺口仍会让 index 错位（见下「为何单点不够」） |
| **B** 横轴改按**时间**同步而非 logical index | 把 `chartSync` 的广播从 `setVisibleLogicalRange` 换成 `setVisibleRange`（按 `getVisibleRange()` 的时间区间同步） | 中 | 中 | 中 | 时间维度天然与数据长度无关，根治错位；但 `setVisibleRange` 对端点时间戳须存在、对 whitespace / 边界更敏感，且要同步改造现有 `chartSync.test.ts` 的 logical 语义，回归面比 A 大 |
| **C** 升级 lightweight-charts v5 用原生 panes | 单 chart + 原生多窗格，主副图共享同一条时间轴，跨图错位（含本 Bug 与已修的几何错位）一并消失 | 高 | 高 | 大 | 一劳永逸，但需 `CandleChart`/`IndicatorPane`/`plots.ts`/`chartSync.ts` 全链路 v5 迁移与回归，远超「最小破坏面」，应另立技改任务 |

**为何「仅在首根加一个 whitespace 锚」不够（方案 A 的实施红线）**：若只在 candle[0].ts 补一个 whitespace、其余暖机位仍丢弃，则副图时间轴点集为 `{candle0, candleW, candleW+1, …}`——缺失 candle[1..W−1]，于是副图 index 1 = candle[W] 而非 candle[1]，逻辑索引仍与主图错位。**必须让副图时间轴点集与主图 candle 域逐点一致**（每根都在），index 才能一一对应。最稳妥是「每个 pane 一条全量 whitespace 锚系列」或「`lineData`/直方对暖机位 push `{time}` 而非 `continue`」。

---

## 6. 推荐修复（Recommended fix）及理由

**推荐方案 A：用 whitespace 数据点把副图各系列（或每个 pane 一条隐藏锚系列）补齐到与主图相同的全量 K 线时间域，使两图 logical index 原点一致。**

理由：

1. **直击且仅触及根因**：错位的唯一直接成因是「副图丢弃暖机前导点 → index 0 晚于主图 → 仅按 index 同步即错位」。方案 A 让副图时间轴覆盖全量 candle 域，`setVisibleLogicalRange` 的同一 `[from,to]` 重新映射到同一时间/同一 x；时间维度的十字光标贯穿也随之对齐。VOL 不受影响（本就全量），其余指标错位归零。
2. **破坏面最小、与既有修复正交**：改动集中在 `indicators.ts`（`lineData` / MACD 直方对暖机位改 push whitespace）或 `IndicatorPane.tsx`（挂一条全量 whitespace 锚系列）；**完全保留** commit `0216201` 的价轴等宽重对齐与未提交的左轴可见性修复（那两者修的是几何，仍必要）。不升依赖、不改同步协议、不改后端。
3. **风险可控**：whitespace 是 lightweight-charts 一等公民（`{ time }` 无 `value` 即 WhitespaceData），line / histogram 均原生支持混入，不绘制、不影响 `autoscaleInfoProvider` 锁定的固定值域（RSI 0~100 等），也不改变指标曲线本身的起绘位置（暖机段仍不画线，只是占住时间轴点）。

**交付给 Subtask #2 的硬约束（验收口径）**：

- [ ] 副图每条系列（或每个 pane 的锚系列）的时间轴点集**逐根覆盖全量 candles**，使副图 index 0 = candle[0]（杜绝「单点锚」半成品，见 §5 红线）。
- [ ] 暖机段仍**不绘制**指标线/柱（whitespace 无 value，视觉与现状一致），仅用于对齐时间轴。
- [ ] 手动验收：默认 MACD 下，任取一根 K 线其垂直中线与下方 MACD 柱中线重合；鼠标移动时主/副图两条竖直虚线水平对齐；切 VOL/RSI/KDJ 均无横向错位。
- [ ] 回归测试：补一条「副图系列时间域与主图 candle 域逐点一致 / 副图 index 0 == 主图 index 0」的单测，封住当前测试缺口（现有 `chartSync.test.ts`、`IndicatorPane.test.tsx` 均未覆盖逻辑索引原点对齐）。
- [ ] 不得回退已生效的几何修复（价轴等宽重对齐、左轴可见性跟随主图）。

---

## 附：关键证据索引（代码引用）

- `apps/web/package.json:16` — lightweight-charts `^4.2.0`（无原生 panes，主/副图各自 `createChart` 手动同步）。
- `apps/web/components/Chart/CandleChart.tsx:305-317` — 主图 K 线 `setData` 全量 candles → 主图 index 0 = candle[0]。
- `apps/web/components/Chart/CandleChart.tsx:456` — 主图把同一 `candles` 传给 `IndicatorPane`（同源数据，不同长度才是问题）。
- `apps/web/lib/indicators.ts:208-216` — `lineData` 对暖机 `undefined` `continue` 丢弃前导点（折线指标 index 0 晚于 candle[0]）。
- `apps/web/lib/indicators.ts:318-326` — MACD 直方同样丢弃暖机点。
- `apps/web/lib/indicators.ts:113-119 / 160 / 187` — 各指标暖机起点（MACD DIF=25 / RSI=period / KDJ=n−1）。
- `apps/web/lib/indicators.ts:396-403` — VOL 全量、零暖机（对照组，预期不错位）。
- `apps/web/components/Chart/plots.ts:77-81` — reconcile 对每条 plot `setData`，副图时间轴 = 各系列时间点并集。
- `apps/web/lib/chartSync.ts:54` / `:65` — 横轴仅按 `setVisibleLogicalRange`（index 维度）广播同步。
- `apps/web/lib/chartSync.ts:101-104` — 新副图注册时按 logical range 对齐到主图（仍 index 维度）。
- `apps/web/lib/chartSync.ts:79` — 十字光标按 `param.time`（时间维度）贯穿，因副图 time↔index 偏移而落到错误 x。
- `apps/web/lib/chartSync.ts:28-40` — commit `0216201` 的缩放重对齐（修的是几何，不修逻辑索引原点）。
- `apps/web/components/Chart/IndicatorPane.tsx`（未提交）+ `CandleChart.tsx:520` — 左轴可见性跟随主图（修的是几何，不修逻辑索引原点）。

---

## 评审结论（Review Verdict）

> 评审人：Subtask #1（分析报告评审 / Anthropic 资深评审）
> 日期：2026-06-23
> 评审范围：根因证据链 + 候选修复边界面 + 推荐方案最小化
> 总评：**根因成立、证据基本airtight、推荐方向（方案 A）正确**。本节对全部代码引用做了逐行复核（结论：准确），补强两处偏薄证据，并把推荐修复从「方案 A（两种实现皆可）」**收敛为单一最小变体**，附边界/回归硬约束。

### 一、根因证据复核（逐条核验，结论：通过）

我对报告引用的每一处代码做了源文件比对，全部命中且语义与报告一致：

1. **横轴仅按 logical index 同步** —— `chartSync.ts:54` `other.timeScale().setVisibleLogicalRange(range)`（订阅 `:65`）、`:101-104` 新图按 `getVisibleLogicalRange` 对齐、`:79` 十字光标按 `param.time` 贯穿。全仓库无任何 `setVisibleRange`（时间维度）同步路径。✔ 与报告一致。
2. **主图 index 0 = candle[0]** —— `CandleChart.tsx:305-317` 对全量 `candles` `setData`；`:190-198` candleSeries 用全量数据建系列，时间轴原点被 candleSeries 钉死在 candle[0]，即便主图叠加层（MA/EMA/BOLL）也走 `lineData` 丢弃暖机（`:456-468` reconcile 到同一右价轴），原点仍是 candle[0]——**故主图叠加层不产生跨图错位**，报告 §4 论断正确。✔
3. **副图丢弃暖机前导点** —— `indicators.ts:208-216` `lineData` 对 `undefined/NaN` `continue`；MACD 直方 `:318-326` 同样 `continue`；暖机起点 SMA/EMA `period-1`、`rsi` `:160` `out[period]`、`kdj` `:187` `if (i < n-1) continue`、VOL `:396-403` 全量零暖机。✔
4. **副图时间轴 = 各系列时间点并集** —— `plots.ts:77-81` 对每条 plot `setData`，无任何覆盖全域的锚系列。✔

**判定：被引代码确实在失败路径上执行，且证据确实解释了观察到的症状**（固定偏移 + 十字光标错位）。无伪引用、无张冠李戴。

### 二、补强证据（原报告偏薄之两处）

**补强 1：MACD 的暖机原点须以「最早起绘的系列」为准，W=25 而非 33。**
报告称 MACD pane 原点 = candle[25]，正确，但未点明三条子系列起点并不相同，易被下游误读。复核 `indicators.ts:107-144`：

- DIF：`dif[i]` 在 `emaFast[i]`、`emaSlow[i]` 均有效时取值，`emaSlow` 种子在 `index = slow-1 = 25`（`ema` `:72` `out[period-1]=seed`），故 **DIF 首个有效点 = index 25**。
- DEA：对「压实后的 dif 有效子序列」求 `ema(_, 9)`（`:122-136`），其首个有效点在压实下标 `9-1=8` → 映射回原下标 `25+8 = 33`，故 **DEA 首个有效点 = index 33**。
- HIST：`hist[i]` 需 `dif[i]` 且 `dea[i]` 均有效（`:138-142`），故 **HIST 首个有效点 = index 33**。

副图时间轴原点 = 三者并集的最早点 = **DIF 的 index 25**。因此偏移量 **W = 25**（由 DIF 锚定），与报告结论一致；但 MACD 最显眼的红绿柱（HIST）实际从 index 33 起绘——这只影响柱子起点的视觉位置，不改变「pane index 0 = candle[25]」这一原点事实。该精度对方案 A 的「必须逐根全量覆盖」要求是正向支撑（见三）。

**补强 2：库语义——logical index 是「对当前存在的时间点做稠密 0,1,2… 编号」，缺口被压实，这正是「单点锚不够」的根本原因。**
lightweight-charts v4 的 time scale 对一条图上**所有系列时间点的并集**按出现顺序赋予连续整数 logical index（缺失的 bar 不占位、被压实），index 0 = 该图最早时间点。故若副图时间点集为 `{candle0, candle25, candle26, …}`（仅在 candle0 加单点锚、其余暖机位仍丢弃），则 **index 1 直接跳到 candle25**，1..W−1 的位被压实——`setVisibleLogicalRange` 同一 `[from,to]` 在副图仍偏移。**结论：方案 A 必须让副图时间点集逐根覆盖 candle[0..N−1]，原点对齐才能传导到整段逻辑轴**，报告 §5 的「实施红线」成立且是硬约束，不是可选优化。

### 三、候选修复审计（边界 / 回归 / 最小化）

| 维度 | 方案 A（whitespace 全域补齐） | 方案 B（改按时间 `setVisibleRange` 同步） | 方案 C（v5 原生 panes） |
|---|---|---|---|
| 是否根治 | 是（原点对齐，index 同步重新成立） | 是（时间维度与数据长度无关） | 是（共享时间轴） |
| 边界/并发 | whitespace 无 value，不进 autoscale、不改 fixedRange、不绘制；live 增量须同步更新锚 | 端点时间戳须存在；滚入暖机区左缘行为需复验；与左加载 `from<5` 判定耦合 | 全链路重写 |
| 回归面 | 小且**与既有几何修复正交**（价轴等宽、左轴可见性全部保留） | 中（须重写 `chartSync.test.ts` 的 logical 语义、十字光标贯穿改时间口径） | 大（`CandleChart`/`IndicatorPane`/`plots.ts`/`chartSync.ts` 全改 + 回归） |
| 最小化 | ✔ 最小 | ✘ 改同步协议 | ✘ 升级依赖 |

**审计结论**：方案 B 在「滚动到暖机区左缘」时仍可能因两图 bar 数不同而残留边缘错位，且需推翻当前已被 `chartSync.test.ts` 充分覆盖的 logical 同步契约，回归面明显大于 A；方案 C 远超最小破坏面，应另立技改任务。**方案 A 是直击根因且破坏面最小的选择。**

**方案 A 的两种实现需进一步甄别**（报告把二者并列，本评审收敛取其一）：

- 实现 A-1：改共享 `lineData` / MACD 直方循环，对暖机位 push `{time}`（whitespace）而非 `continue`。
  - 副作用：`Pt = {time, value:number}` 与 `Plot.data` 类型须放宽为允许无 `value` 的 whitespace 点，类型涟漪扩散到 `plots.ts` 的 `setData`；且 `lineData` 被主图叠加层共用，会顺带给 MA/EMA/BOLL 也注入 whitespace（无害但**扩大了改动半径**，违背最小化）。
- 实现 A-2（**本评审推荐**）：在 `IndicatorPane` 内新增**一条隐藏的「全域 whitespace 锚系列」**，数据 = `candles.map(c => ({ time: c.ts }))`，逐根覆盖全部 candle 时间。
  - 仅触一个文件（`IndicatorPane.tsx`）；不改 `indicators.ts`、不改类型、不动指标数学、主图叠加层零影响；锚系列 whitespace 无 value → 不进 autoscale、不破坏 RSI 的 `fixedRange`。与主图「candleSeries 钉死原点」的现有机制同构（副图缺的正是这条全域锚）。

**A-2 的唯一回归风险点（必须规避）**：本仓库存在历史 Bug `chart-crosshair-sync-value-is-null`（`chartSync.ts:75-83` 对无数据系列调用 `setCrosshairPosition` 会抛 `Error: Value is null`，且 `:77` 现留有 `// TEMP-REVERT-FOR-VERIFY` 标记）。`IndicatorPane` 的 `getSeries` 取 `mapRef.current` 首个系列供跨图十字光标（`:96`）。**若把 whitespace 锚系列放进 `mapRef` 且它恰为首个，`getSeries` 会返回一条无 value 系列，可能复发 `Value is null`**。因此 A-2 必须把锚系列存于**独立 ref（如 `anchorSeriesRef`），不纳入 `mapRef`**，确保 `getSeries` 始终返回真实指标系列。该风险有 `:81` 的 try/catch 兜底，但仍应从结构上避免。

### 四、最终推荐修复（单一方案）

> **采用方案 A，实现 A-2：在 `IndicatorPane` 中为每个副图挂一条隐藏的「全域 whitespace 锚系列」，其时间点逐根覆盖全部 `candles`（`candles.map(c => ({ time: c.ts }))`），使副图时间轴原点 index 0 = candle[0]，与主图逻辑索引一一对齐；锚系列独立于 `mapRef` 管理，不参与十字光标 `getSeries`。**

理由：

1. **直击且仅触根因**：错位的唯一直接成因是「副图丢弃暖机 → index 0 晚于主图 → 仅按 index 同步即错位」。全域锚让 `setVisibleLogicalRange` 的同一 `[from,to]` 在两图重新映射到同一时间/同一 x；时间维度十字光标随之对齐。VOL 本就全量、不受影响。
2. **破坏面最小且正交**：单文件改动；**完整保留** commit `0216201` 价轴等宽重对齐与未提交的左轴可见性修复（那两者修几何，仍必要且互不冲突）；不升依赖、不改同步协议、不改指标数学、主图叠加层零影响。
3. **类型与值域安全**：whitespace（`{time}` 无 `value`）是 lightweight-charts 一等公民，line 系列原生支持混入，不进 `autoscaleInfoProvider`、不破坏 RSI `fixedRange`、不在暖机段绘制任何线/柱（视觉与现状一致）。
4. **相对 A-1 更省**：避免放宽 `Pt`/`Plot` 类型、避免给主图叠加层注入无关 whitespace，改动半径更小、回归更可控。

（若实施时发现 A-2 的锚系列生命周期管理成本超预期，A-1 为等效兜底；但须接受其类型涟漪与叠加层附带改动，且优先级低于 A-2。）

### 五、交付 Subtask #2 的硬约束（验收口径，含可落地的回归测试）

- [ ] **全域逐根覆盖**：锚系列时间点集 = 全部 `candles` 的 `ts`，使副图 index 0 = candle[0]（杜绝「单点锚」半成品，见 §二·补强 2 与 §5 红线）。
- [ ] **暖机段仍不绘制**指标线/柱（whitespace 无 value），仅占位对齐时间轴；视觉与现状一致。
- [ ] **锚系列不入 `getSeries`**：独立 ref 管理，避免复发 `chart-crosshair-sync-value-is-null`（§三·A-2 风险点）。
- [ ] **live 增量同步**：candles 变化（实时新增/左加载更早历史）时锚系列须随之 `setData`，保持持续覆盖；空 candles 时锚为 `[]`。
- [ ] **不回退既有几何修复**：价轴等宽重对齐（`chartSync.ts:28-40`）、左轴可见性跟随主图（`IndicatorPane.tsx`/`CandleChart.tsx:520`）必须原样保留。
- [ ] **补回归测试（封住当前缺口）**：现有 `chartSync.test.ts` 仅覆盖范围广播/十字光标短路/价轴等宽；`IndicatorPane.test.tsx` 仅覆盖左轴可见性，且其桩 `makeSeriesStub.setData` 为 no-op、jsdom 无法复现像素错位。**新测应在数据层断言**——增强该桩捕获 `setData` 入参，断言副图存在一条系列其时间点集逐根等于输入 `candles` 的 `ts`（首点 == `candles[0].ts`、长度 == `candles.length`）。此口径不依赖 canvas/像素，jsdom 可稳定运行，且能精确复现「修复前副图最早时间点晚于 candle[0]」的回归。
- [ ] **手动验收（运行期决定性确认）**：默认 MACD 下任取一根 K 线其垂直中线与下方 MACD 柱/DIF 对应位置重合；鼠标移动时主/副图两条竖直虚线水平对齐；切 VOL/RSI/KDJ 均无横向错位。建议执行报告 §2 判别实验（切 VOL 错位归零、MACD 最大），作为「错位幅度随暖机长度成正比」的运行期实证——代码层证据已闭合，此步为终验确认。

### 六、残留风险与说明

- **「约一个横向格子」≈ W=25 是软性描述**：竖网格线间距以 bar 数计随 timeframe/缩放而变，25 根是否恰为「一格」取决于当时 bar spacing，无法在不运行 UI 时精确核定。但这不影响根因——**判定性指纹是「固定、与缩放无关、且幅度随所选指标暖机长度成正比」**（§2 表 + §3.5 反证），与价轴几何问题特征正交。运行期 §2 实验为最终确认。
- 评审仅改 `analysis.md`，未触业务代码（遵守本节点约束）；修复实现交 Subtask #2。

</invoke>
