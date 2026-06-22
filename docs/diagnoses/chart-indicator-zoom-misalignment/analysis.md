# 诊断报告：K 线与副图技术指标在缩放后左右错位

> Bug slug: `chart-indicator-zoom-misalignment`
> 作者：Subtask #0（问题定位与分析）
> 日期：2026-06-22
> 状态：根因已定位（含代码证据），给出候选修复对比与推荐方案

---

## 1. 问题描述（Problem description）

工作台左侧图表区由 **K 线主图** 与其下方的 **技术指标副图**（MACD / RSI / KDJ / VOL）垂直堆叠组成。

- **正常态**：进入页面、默认 K 线比例（默认 bar spacing）时，K 线与下方副图指标在**横轴（时间轴）上一一对齐**——同一根 K 线与其对应的指标柱/点位于同一水平 x 位置。
- **异常态**：用户用鼠标滚轮放大 / 缩小（或拖动平移）后，主图 K 线与副图指标出现**左右错位**——同一时间点的 K 线与指标不再位于同一 x 位置，且**错位幅度随缩放程度累积放大**（越往图的一侧、缩放越深，偏差越明显）。

错位是「渐进式」而非「整体平移」：在某一边（绘图区锚定边）基本贴合，向另一边逐根累积偏移。这是典型的**两套绘图区几何宽度不一致**的指纹，而非数据时间戳错误。

---

## 2. 复现步骤（Reproduction steps）

前置：本地已能连通 OKX（真实数据硬约束），Node ≥ 18.18 + pnpm 已就绪。

1. 启动后端 + 前端：

   ```bash
   pnpm dev          # 后端 :18760 + 前端 :18761，等价 python scripts/dev.py up
   ```

2. 浏览器打开 `http://localhost:18761`。
3. 默认会加载一个高价标的（如 BTC，价格量级≈6 万）。在右侧指标菜单中开启任一**副图**指标，优先选 **MACD** 或 **VOL**（数值量级与主图差异最大、最容易触发），确保主图下方出现技术指标窗格。
4. 观察此时 K 线与副图指标在横轴上**对齐正常**（默认比例）。
5. 把鼠标移到**主图绘图区**上，用**滚轮快速放大若干档，再缩小若干档**（反复几次）。
6. 现象：主图某根 K 线的垂直中线与下方副图对应指标柱的中线**不再对齐**，且来回缩放几次后偏差**越来越大**。切换 MACD/VOL（自适应量级）比 RSI/KDJ（固定 0~100）更明显。

> 最小复现要点：**主图与副图右价轴的"内容宽度"在缩放后变得不同**，即可复现。高价标的（BTC）+ 自适应量级副图（MACD/VOL）= 偏差最大的组合。

---

## 3. 根因分析（Root cause analysis，含证据）

### 3.1 架构事实：主图与副图是相互独立的 chart 实例，靠手动同步

lightweight-charts v4.2（`apps/web/package.json:16` → `"lightweight-charts": "^4.2.0"`）**不支持原生多窗格**（panes 是 v5 特性）。本项目用**多个 `createChart` 垂直堆叠**模拟副图，并靠 `chartSync.ts` 手动同步缩放/平移/十字光标/价轴宽度。该约束在模块头注释中写明：

- `apps/web/lib/chartSync.ts:1-3`
  > 主图 + 各副图的时间轴双向同步（必选）… v4.2 不支持原生多窗格（panes 是 v5），副图用多个 createChart 垂直堆叠，靠本模块把缩放 / 平移 / 十字光标 / 价格轴宽度对齐到像素级一致。

- 主图：`apps/web/components/Chart/CandleChart.tsx:161` `createChart(el, {...})`
- 副图：`apps/web/components/Chart/IndicatorPane.tsx:53` `createChart(el, {...})`

### 3.2 横轴对齐的充要条件

两个上下堆叠的独立 chart，要让"同一逻辑索引的 K 线落在同一 x 像素"，必须同时满足：

1. **可见逻辑范围一致**（同一 `from..to`）；
2. **绘图区几何一致**——即两图的 `总宽度` 与 `左价轴宽 + 右价轴宽` 一致，使绘图区的左右边缘像素对齐。

只有当条件 2 成立时，被同步的同一逻辑范围才会在两图中**线性映射到同一组 x 像素**。任何一图的价轴宽度变化，都会让其绘图区左/右边缘平移，于是"同一逻辑范围"被映射到不同的像素区间 → 逐根累积错位。

- 条件 1 已满足：`chartSync.ts:28-44` 的 `onRange` 把任一图的可见逻辑范围广播 `setVisibleLogicalRange` 给其余图（缩放时确实同步了逻辑范围）。
- 条件 2 是**问题所在**：两图总宽相同（均 `autoSize:true`，同一列），但**右价轴宽度会在缩放时各自漂移且不再被重新对齐**。

### 3.3 关键机制：缩放会改变右价轴宽度

右价轴宽度由**当前可见价格区间的刻度标签像素宽**决定（v4.2 中 `minimumWidth` 只是下限，内容更宽会超过它，注释已点明，见 `chartSync.ts:103-107`）。

缩放横轴会改变可见 K 线根数 → 改变可见区间的最高/最低价 → 自适应价轴重新拟合 → **刻度标签位数变化** → `priceScale("right").width()` 变化。

- 主图（BTC ≈ 6 万）：放大后可见区间收窄，标签可能从 `60,000` 变为 `63,250.5` 等，宽度变化；
- 副图 MACD / VOL：数值量级完全不同且同样自适应，缩放后标签宽度独立变化。

于是**主图与副图的右价轴宽度在缩放过程中各自漂移、彼此偏离**。

### 3.4 致命点：价轴等宽对齐**不会在缩放时被触发**

把两图价轴宽度重新拉平的唯一函数是 `alignPriceScaleWidths`（`chartSync.ts:108-133`：先把各图价轴重置回保底下限，下一帧测真实宽，取同侧最大值回写为所有图的 `minimumWidth`）。

全仓库仅有**两处**调用它（`grep alignPriceScaleWidths apps/web`）：

| 调用点 | 触发时机 | 缩放时是否触发 |
|---|---|---|
| `CandleChart.tsx:241-247`（ResizeObserver 回调） | 容器**尺寸**变化 | ❌ 缩放不改变容器尺寸 |
| `CandleChart.tsx:485-487`（effect，依赖 `[priceMagnitude, activeKey, hasPane, theme, hasEquity]`） | 这些 React 依赖变化 | ❌ 见下 |

第二处的依赖在**滚轮缩放时全都不变**：

- `priceMagnitude`（`CandleChart.tsx:151-154`）只取**最后一根 K 线收盘价**的量级，缩放不改变 candles，故不变；
- `activeKey` / `hasPane`（切换/开关副图才变）、`theme`（切主题才变）、`hasEquity`（净值存废才变）——缩放期间均不变。

而真正监听可见范围变化的回调里，**没有任何一处重新对齐价轴宽度**：

- `chartSync.ts:28-44` 的 `onRange`：只做逻辑范围广播；
- `CandleChart.tsx:211-219` 的 `onRange`：只做"滚到左边界 → 增量加载更多历史"。

**结论（根因）**：
> 缩放会让主图与副图的**右价轴内容宽度各自漂移并彼此偏离**，但负责把两者拉平的 `alignPriceScaleWidths` **只在容器 resize / 量级·副图·主题等离散事件时触发，从不在缩放（可见范围变化）时触发**。于是两图绘图区左右边缘像素不再一致，被同步的同一逻辑范围被映射到不同 x 像素，产生随缩放累积放大的横向错位。进入页面时之所以正常，是因为挂载与上述 effect 已跑过一次对齐；首次缩放后对齐即失效。

固定量级副图（RSI/KDJ，`fixedRange` 0~100）也会错位，因为即便副图自身价轴稳定，**主图右价轴仍会随缩放变宽变窄**，单边漂移同样破坏等宽前提。

### 3.5 反证排除

- 不是数据/时间戳问题：K 线与指标都以同一 `ts`（UTC 秒）作横轴键（`CandleChart.tsx:308-315` 与 `IndicatorPane.tsx:122` 经 `plots.ts` reconcile 用同一 candles 计算），逻辑范围也已同步广播；若是数据错位则默认比例也会错，与"默认正常"矛盾。
- 不是逻辑范围未同步：`chartSync.test.ts` 证明 `createSyncGroup` 的逻辑范围广播工作正常（每图各被设置一次、无回环）。但该测试**完全没有覆盖价轴等宽对齐**，更未覆盖"缩放后是否仍对齐"，这正是缺口所在。

---

## 4. 影响面（Impact surface）

- **受影响功能**：左侧图表工作台中，**任意时刻开启了副图指标**（MACD / RSI / KDJ / VOL）的视图。涉及组件：`CandleChart.tsx`（主图 + 副图容器）、`IndicatorPane.tsx`（副图）、`chartSync.ts`（同步与等宽对齐）。
- **受影响用户**：所有使用桌面端 / Web 端图表并进行缩放/平移操作的用户——即正常看盘的核心路径。
- **加重条件**：高价量级标的（BTC 等）+ 自适应量级副图（MACD/VOL）错位最明显；低价标的或纯固定量级副图错位较轻但仍存在（因主图单边漂移）。
- **不影响**：纯主图（未开副图）视图无副图可错位；后端、数据正确性、回测/模拟逻辑均不受影响（纯前端渲染对齐问题）。
- **严重度**：功能性可用，但**读图判断被误导**（指标对应的 K 线被读错），对一个交易看盘工具属于高可感知的体验缺陷。

---

## 5. 候选修复方案（Candidate fixes）

| 方案 | 思路 | 侵入性 | 风险 | 工作量 | 备注 |
|---|---|---|---|---|---|
| **A（推荐）** 缩放时按帧合并地重新等宽对齐 | 监听各图可见范围变化（`subscribeVisibleLogicalRangeChange`，已有订阅点），在缩放/平移时用 `requestAnimationFrame` **合并去抖**地调用一次价轴等宽重对齐 | 低（仅 `chartSync.ts` + 少量 `CandleChart` 接线） | 低 | 小 | 直击根因。需注意：① rAF 合并避免每事件抖动；② 缩放时宜用「只测量并取同侧最大值回写、不做重置」的对齐，避免现有 `alignPriceScaleWidths` 每帧先重置到 64 造成 1 帧闪缩。建议新增一个「非重置版对齐」用于缩放热路径，保留现有「重置版」用于换标的（量级变化需要收窄） |
| **B** 固定价轴宽度，禁用动态对齐 | 给主图与所有副图设定一个足够大的固定 `minimumWidth`，不再测量 | 低 | 中 | 小 | 简单但治标不治本：当实际标签宽**超过**该固定值时（v4.2 中 `minimumWidth` 仅下限），两图仍会各自超宽并再次偏离；且高价标的需很大的固定值，浪费横向空间、低价标的留白 |
| **C** 升级 lightweight-charts v5 用原生 panes | 改用单 chart + 原生多窗格，彻底消除跨 chart 几何同步 | 高 | 高 | 大 | 一劳永逸（共享同一时间轴，天然像素对齐），但需 v5 迁移：`CandleChart` / `IndicatorPane` / `plots.ts` / `chartSync.ts` 全链路 API 改动与回归，超出"最小破坏面"修复范畴 |

---

## 6. 推荐修复（Recommended fix）及理由

**推荐方案 A：在可见范围变化（缩放/平移）时，按帧合并地重新执行价轴等宽对齐。**

理由：

1. **直击根因**：错位的唯一直接成因是"缩放改变了两图右价轴宽度，却没有触发等宽对齐"。方案 A 把缺失的触发补上，使条件 2（绘图区几何一致）在缩放后持续成立，从而 `setVisibleLogicalRange` 的同步重新映射到同一组 x 像素。
2. **破坏面最小**：改动集中在 `chartSync.ts`（同步组在广播范围后顺带调度一次去抖的等宽对齐）+ `CandleChart.tsx` 极少量接线，不触碰数据流、不升级依赖、不改副图计算。符合任务"改动尽量小"。
3. **风险可控**：`applyOptions({ minimumWidth })` 不会改变逻辑范围，不会与逻辑范围广播形成回环；用 `requestAnimationFrame` 合并多图、多事件为每帧一次，避免性能抖动。

落地要点（供 Subtask #2 实施参考，本节不写修复代码）：

- 在同步组的 `onRange`（`chartSync.ts:28-44`）广播完逻辑范围后，**调度一次 rAF 合并的等宽重对齐**（同一帧内多次 range 事件只对齐一次）。
- 为缩放热路径提供**「只测量取最大值回写、不重置到下限」**的对齐变体，避免现有 `alignPriceScaleWidths` 先重置（`chartSync.ts:115-118`）导致的逐帧闪缩；现有「重置版」继续用于换标的（`priceMagnitude` 变化需要收窄价轴）。
- 注意 guard：等宽对齐内部本身已是 rAF，叠加外层去抖时确保不会无限自触发（宽度收敛后再次对齐结果不变，天然收敛）。
- 回归校验：补一条针对 `chartSync` 的测试——"可见范围变化应触发一次（且每帧仅一次）价轴等宽对齐"，把当前测试缺口（`chartSync.test.ts` 未覆盖等宽对齐）补上。

---

## 附：关键证据索引（代码引用）

- `apps/web/package.json:16` — lightweight-charts `^4.2.0`（无原生 panes，必须手动同步）。
- `apps/web/lib/chartSync.ts:1-3` — 多 `createChart` 堆叠 + 手动对齐的架构说明。
- `apps/web/lib/chartSync.ts:28-44` — 缩放时仅广播逻辑范围，**未**重对齐价轴宽度。
- `apps/web/lib/chartSync.ts:103-133` — `alignPriceScaleWidths`：minimumWidth 只是下限、需动态测量回写；含"先重置到下限"的逐帧闪缩隐患。
- `apps/web/components/Chart/CandleChart.tsx:161` / `IndicatorPane.tsx:53` — 主图与副图各自独立 `createChart`。
- `apps/web/components/Chart/CandleChart.tsx:151-154` — `priceMagnitude` 只取末根收盘价量级，缩放不变。
- `apps/web/components/Chart/CandleChart.tsx:241-247` — 等宽对齐仅由 ResizeObserver（容器尺寸）触发。
- `apps/web/components/Chart/CandleChart.tsx:485-487` — 等宽对齐 effect 依赖 `[priceMagnitude, activeKey, hasPane, theme, hasEquity]`，缩放期间均不变。
- `apps/web/components/Chart/CandleChart.tsx:211-219` — 主图可见范围回调仅做增量加载，未重对齐。
- `apps/web/lib/chartSync.test.ts` — 仅覆盖逻辑范围广播，**未**覆盖价轴等宽对齐与缩放后对齐（测试缺口）。

---

## 评审结论（Review Verdict）

> 评审人：Subtask #1（分析报告评审与修复）
> 日期：2026-06-22
> 范围：仅审计 + 补全证据，未改业务代码、未写修复（修复为 Subtask #2 职责）。

### A. 证据复核结论：根因成立，引用全部核实无误

我按"被引用代码是否真的在故障路径上执行 / 是否被反例推翻 / 是否真能解释症状"三条标准，逐行打开源码复核，结论是**根因链成立、可放行**。复核明细：

| 报告论点 | 核实方式 | 结论 |
|---|---|---|
| v4.2 无原生 panes，主/副图各自 `createChart` 手动同步 | `package.json:16` = `^4.2.0`（本机已装 `4.2.3`）；`CandleChart.tsx:161` + `IndicatorPane.tsx:53` 各一次 `createChart` | ✅ 属实 |
| 缩放仅广播逻辑范围、不重对齐价轴 | `chartSync.ts:28-44` `onRange` 只调 `setVisibleLogicalRange`；`CandleChart.tsx:211-219` `onRange` 只做增量加载 | ✅ 属实 |
| 等宽对齐**全仓库仅两处触发**，均与缩放无关 | `grep alignPriceScaleWidths apps/web` 仅命中 `CandleChart.tsx:244`（ResizeObserver）与 `:486`（effect，依赖 `[priceMagnitude, activeKey, hasPane, theme, hasEquity]`） | ✅ 属实 |
| 缩放期间上述 5 个依赖均不变 | `priceMagnitude`(`:151-154`) 只取末根收盘价量级；其余 4 个仅在切副图/切主题/净值存废时变 | ✅ 属实 |
| 测试缺口 | `chartSync.test.ts` 仅 2 个用例（广播一次 / unregister 停播），**零**断言触及 `alignPriceScaleWidths` | ✅ 属实 |

**症状↔根因的几何闭合**（我补全此前报告隐含未写明的推导，作为证据闭环）：两图被 `setVisibleLogicalRange` 强制贴到**同一逻辑范围 `[from,to]`**，则第 `i` 根映射到 `x = leftAxisW + (i-from)/(to-from) · plotW`，其中 `plotW = 总宽 − leftAxisW − rightAxisW`。两图总宽相同（同列 `autoSize`）。当 `rightAxisW` 因缩放各自漂移、不再相等 → `plotW` 不等 → 在锚定边（左边缘，二者 `leftAxisW` 相等时）重合、向另一边按 `Δ(plotW)·(i-from)/(to-from)` **线性累积偏移**。这与用户描述的"一边贴合、向另一边逐根越拉越大、来回缩放后越发严重"完全吻合；也解释了"默认比例正常"——挂载时 `:486` 的 effect 已跑过一次对齐，**首次缩放后**对齐即失效且无任何路径恢复。反例（数据/时间戳错位、逻辑范围未同步）均已被原报告 §3.5 正确排除，且与"默认正常"矛盾，可排除。

### B. 补充证据：左价轴可见性不对称（原报告未覆盖，本次补全）

原报告聚焦右价轴漂移，但还有一处不对称值得记入证据，它决定了**修复必须同时对齐左右两侧**：

- 主图：`CandleChart.tsx:174-178` `leftPriceScale: { visible: hasEquity, minimumWidth: 64 }` —— 左轴**仅净值存在时可见**。
- 副图：`IndicatorPane.tsx:69-73` `leftPriceScale: { visible: true, minimumWidth: 64 }` —— 左轴**恒可见**（其上无任何 series，`reconcilePlots` 一律挂 `priceScaleId:"right"`，见 `IndicatorPane.tsx:122-125`）。

含义有二：① 在**净值视图/对比模式**下主图左轴承载净值刻度，缩放改变可见净值区间 → **左轴宽度同样会漂移**；副图恒开的空左轴正是为了让 `alignPriceScaleWidths` 能给它回写一个与主图左轴匹配的预留宽（该函数 `chartSync.ts:115-131` 对 `left`/`right` 同等处理）。② 因此修复触发的重对齐**必须沿用 `alignPriceScaleWidths` 同时处理左右两侧的语义**——这恰是方案 A（复用该函数）优于方案 B（仅给右轴设固定宽）的又一理由：方案 B 不跟踪净值左轴漂移，在净值视图下仍会错位。

### C. 修复方案审计（边界 / 最小性 / 回归）

**方案 A（缩放时按 rAF 合并重对齐）—— 通过，为最小且正确的修复。** 但落地有三处边界必须在 Subtask #2 处理，否则会引入新缺陷：

1. **自激反馈环（最高优先级风险）**：`applyMinWidth` 经 `applyOptions({minimumWidth})`（`chartSync.ts:143-148`）改价轴宽 → 时间轴绘图区宽度变化 → 在固定 `visibleLogicalRange` 下重新拟合 → **再次触发 `subscribeVisibleLogicalRangeChange`**。若重对齐挂在该订阅上，则形成"对齐→改宽→范围事件→再对齐"。`chartSync.ts` 现有的 `applyingRange` guard（`:20`）**只挡同步广播内的回环，不跨 rAF 帧**，无法拦截本反馈环。须显式收敛：仅当新 `minimumWidth` 与现值**不同**时才 `applyOptions`（达到不动点即自然停手），或加一个 `applyingWidths` 标志在回写期间抑制再次调度。
2. **`alignPriceScaleWidths` 的"先重置到 64"会在热路径闪缩**：该函数第一步 `chartSync.ts:115-118` 同步把所有轴重置回 `64`，下一帧才测真实宽回写。每缩放帧都跑一次 → 出现 1 帧"塌到 64 再撑开"的抖动。热路径应改用**"只测量取同侧最大值回写、不重置下限"**的变体；该变体仍能保持对齐（两图取同一 `max` → 始终等宽），代价仅是单个标的会话内不回收多余宽度（可接受）。
3. **保留重置版用于换标的**：现有"先重置"语义对**切换到低价标的**（量级变小、价轴应收窄）是必要的，不能整体删除。结论：**新增非重置热路径变体**，原 `alignPriceScaleWidths`（重置版）继续绑定 `priceMagnitude` 等离散事件（`:486`）。
4. **rAF 合并去抖**：滚轮一次手势触发大量 range 事件，必须合并为**每帧至多一次**重对齐，避免在布局未稳时测宽、避免性能抖动。

**方案 B（固定大 `minimumWidth`，禁动态对齐）—— 否决为主方案。** 反证：`chartSync.ts:103-107` 注释与 `alignPriceScaleWidths` 存在本身即说明 v4.2 中 `minimumWidth` **只是下限**，真实标签更宽时会突破它；BTC≈6 万、深缩放后标签位数仍可能超过任何预设固定值，两图再次各自超宽偏离 → 治标不治本；且按最坏宽度预留会浪费横向空间、低价标的留白。可作为兜底，不作主修复。

**方案 C（升级 v5 原生 panes）—— 否决用于本 Bug。** 一劳永逸但需 `CandleChart`/`IndicatorPane`/`plots.ts`/`chartSync.ts` 全链路迁移与回归，远超"最小破坏面"，应另立技改任务。

### D. 最终推荐修复（单一方案）

**采纳方案 A：在可见逻辑范围变化（缩放/平移）时，经 `requestAnimationFrame` 合并去抖地、用"非重置版"等宽对齐重新拉平主/副图左右价轴宽度。**

理由（择要）：

1. **直击且仅触及根因**：错位的唯一直接成因是"缩放改宽两图价轴却无任何路径重对齐"。方案 A 补上缺失的触发，使几何前提（两图 `leftAxisW`、`rightAxisW` 对应相等 → `plotW` 相等）在缩放后持续成立，被同步的同一逻辑范围重新映射到同一组 x 像素。
2. **破坏面最小**：改动集中在 `chartSync.ts`（同步组广播范围后顺带调度一次去抖重对齐 + 一个非重置对齐变体）与 `CandleChart`/`IndicatorPane` 极少量接线；不动数据流、不升依赖、不改副图计算与逻辑范围广播。
3. **左右兼顾**：复用 `alignPriceScaleWidths` 同时处理左右轴的语义，顺带覆盖净值视图下主图左轴漂移（见 §B），比方案 B 更完整。

**交付给 Subtask #2 的硬约束（验收口径）**：

- [ ] 重对齐绑定在可见范围变化上，且经 rAF **每帧至多一次**（C-4）。
- [ ] 热路径使用**非重置**对齐变体，杜绝"塌到 64 再撑开"的闪缩（C-2）；原重置版仅保留给 `priceMagnitude` 等离散事件（C-3）。
- [ ] 显式打断自激反馈环：宽度未变则不 `applyOptions`，或加 `applyingWidths` 抑制位（C-1）。
- [ ] 补一条 `chartSync` 回归测试：模拟可见范围变化应触发**恰好一次/帧**等宽重对齐，封住现有测试缺口（A 表末行）。
- [ ] 手动验收：BTC + MACD/VOL，反复滚轮放大缩小若干轮后，任取一根 K 线其垂直中线与副图对应柱中线仍重合，无累积偏移。
