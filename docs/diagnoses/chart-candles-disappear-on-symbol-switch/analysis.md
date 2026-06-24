# 诊断报告：跨价格量级切换标的后，中间 K 线图消失

> Bug slug: `chart-candles-disappear-on-symbol-switch`
> 作者：Subtask #0（问题定位与分析）
> 日期：2026-06-24
> 状态：根因已定位（含库源码 + 业务代码双证据），给出候选修复对比与推荐方案
> 约束：本节不写修复代码，不执行 git commit

---

## 1. 问题描述（Problem description）

工作台中间是 **K 线主图**（`CandleChart`，lightweight-charts v4.2 蜡烛系列，挂在右价轴）。

- **正常态**：首次进入页面、默认标的 `BTC-USDT-SWAP`（价格量级 ≈ 6 万）时，K 线正确居中显示。
- **异常态**：用户切换到**与当前价格量级相差很大**的另一个标的（例如从 BTC ≈ 60,000 切到 `DOGE-USDT-SWAP` ≈ 0.1、或反向从低价标的切回 BTC）后，新标的的 K 线**没有被加载到合适的价位**——价格坐标轴仍停留在上一个标的的价格区间（如仍是 5.9 万~6.5 万），新 K 线（价 0.1）整体落在可视价区间**之外**，被挤到绘图区最底/最顶边缘或完全移出可视区，于是中间面板看上去**「K 线消失了」**。
- 用户的诉求：无论怎样来回切换标的，K 线都应自动加载到合适价位并正常显示。

这是典型的**纵轴（价格轴）未随新数据自适应重拟合**的指纹，而非数据缺失或时间轴错位——切换后数据其实已经 `setData` 进去了，只是被画在了可视价窗之外。

---

## 2. 复现步骤（Reproduction steps）

前置：本地已能连通 OKX（真实行情硬约束），Node ≥ 18.18 + pnpm 已就绪。

1. 启动后端 + 前端（等价 `python scripts/dev.py up`）：

   ```bash
   pnpm dev          # 后端 :18760 + 前端 :18761
   ```

2. 浏览器打开 `http://localhost:18761`（或桌面端）。默认加载 **BTC-USDT-SWAP**（`apps/web/lib/store.ts:165` → `instId: "BTC-USDT-SWAP"`），K 线正常居中。
3. **在主图上对价格轴做一次手动缩放**：把鼠标按在 K 线图**右侧价格刻度（数字轴）上**，上下拖动一小段（这一步会把该价轴的 `autoScale` 关掉——见 §3.3）。此时价区间被「钉死」。
4. 在左侧标的列表搜索并切换到一个**价格量级相差很大**的标的，例如 `DOGE-USDT-SWAP`（≈ 0.1~0.4）或任意低价币（`InstrumentSidebar` → `onSelect` = `store.setInst`，`ChartWorkspace.tsx:214-219`）。
5. **现象**：中间 K 线面板变空白 / K 线不见了。新标的数据已加载，但价格轴仍显示约 5.9 万~6.5 万的刻度，0.1 的 K 线全部落在可视价窗之外。来回切换（BTC ⇄ DOGE）可稳定复现。
6. **确认性反证（强证据）**：在「消失」状态下，**双击右侧价格刻度轴**，K 线会**立刻重新出现并居中**。这是因为双击价轴触发了 `resetPriceScale`：重新把 `autoScale` 置回 `true` 并按当前可见 K 线重拟合价区间（见 §3.4）。这条「双击即恢复」恰好证明根因是 *autoScale 被关掉后价区间没有再被重拟合*。

> 最小复现要点：**任意一次「让价轴 autoScale 变 false」的交互（拖动价轴缩放，最常见）+ 一次跨价格量级的标的切换** = 必现。
> 范围说明（诚实标注）：若**全程未对价轴做过缩放**（autoScale 仍为初始的 `true`），切换标的会正常重拟合、不复现——这正解释了用户「本来显示正常」的前半句：首次加载时 autoScale=true 拟合正确，**用户操作过价轴之后**再切标的才暴露问题。而拖动价轴缩放在看盘工具里是极高频操作，故对真实用户而言这是常态化触发。

---

## 3. 根因分析（Root cause analysis，含证据）

### 3.1 切换标的时，K 线更新逻辑只做 `setData`，从不重置价轴自适应

主图蜡烛系列的数据更新 effect（依赖 `[candles]`）：

```ts
// apps/web/components/Chart/CandleChart.tsx:305-317
useEffect(() => {
  const series = candleSeriesRef.current;
  if (!series || candles.length === 0) return;
  const data = candles.map((c) => ({ time: c.ts as UTCTimestamp, open: c.o, high: c.h, low: c.l, close: c.c }));
  series.setData(data);
  earliestRef.current = candles[0]?.ts ?? null;
}, [candles]);
```

- 切换标的时，`instId` 变化 → `page.tsx` 的 `candlesQ`（react-query，**未设 `keepPreviousData`/`placeholderData`**，`apps/web/app/page.tsx:169-183`）重新拉取 → `candles` 先短暂变 `[]`（此时上面 effect 因 `candles.length === 0` 早退、**不清屏**，旧 K 线滞留），随后新标的数据到达 → `series.setData(newData)`。
- 这段更新逻辑里**只有 `setData`**：没有 `priceScale().applyOptions({ autoScale: true })`，没有 `timeScale().fitContent()`，没有任何「视图复位」。整图唯一处理价轴的代码是 `chartSync.ts` 里的等宽对齐（只改 `minimumWidth` 像素宽，**不碰 `autoScale`**，见 `apps/web/lib/chartSync.ts:196-216`）。
- 全仓库验证：`autoScale` / `fitContent` / `resetPriceScale` 在 `apps/web` 下**零命中**（`grep -r 'autoScale|fitContent|resetPriceScale' apps/web` → No files found）。即：**应用层完全不主动管理价轴自适应，全权依赖 lightweight-charts 的 autoScale 默认开启**。

`createChart` 也未覆写交互项（`apps/web/components/Chart/CandleChart.tsx:161-187` 只设了 layout/grid/crosshair/价轴 `minimumWidth`/timeScale 可见性/`autoSize`），故 `handleScale` 等全部用库默认值——**默认允许拖动价轴缩放**，也即允许把 autoScale 关掉。

### 3.2 lightweight-charts 的 autoScale 语义：开则随数据重拟合，关则价区间钉死

价区间重算入口在数据/范围变化时被调用，但**第一行就按 autoScale 短路**：

```js
// lightweight-charts.development.mjs:6374-6378
_internal_recalculatePriceScale(priceScale) {
    if (priceScale === null || !priceScale._internal_isAutoScale()) {
        return;                       // ← autoScale=false 直接返回，不重算价区间
    }
    this._private__recalculatePriceScaleImpl(priceScale);
}
```

`_internal_isAutoScale()` 即读取该价轴的 `autoScale` 选项（`:5555-5557`）。也就是说：

- autoScale **为 true**：每次 `setData` / 可见范围变化都会按可见 K 线的最高/最低价重拟合价区间（`_private__autoscaleInfoImpl` 用 bar 的 low/high，`:4928-4956`）→ 新标的 K 线自动居中。
- autoScale **为 false**：`recalculatePriceScale` 直接 return → `setData` 进来的新数据**不会**触发价区间重算 → 价区间保持上一个标的的值（如 5.9 万~6.5 万）。

补充排除：自适应只取 **bar 的 low/high + 插件 primitives**（`:4945-4954`），**不含** `createPriceLine` 创建的自定义价线。故主图上的 `LIVE` 价线（`CandleChart.tsx:294-301`）即便短暂滞留旧价也**不影响**自适应，可排除其为元凶；同理，普通「只切标的看盘」场景下 `overlayTrades`/副图指标在非回测/非模拟模式为空（`page.tsx:397-402`），不构成右价轴上的「残留高价系列」，亦非本症状的主因。

### 3.3 autoScale 在「用户拖动价轴缩放」时被置为 false

价轴缩放手势（按住价格刻度上下拖）走 `scaleTo`，**第一件事就是关掉 autoScale**：

```js
// lightweight-charts.development.mjs:5850-5859
_internal_scaleTo(x) {
    if (this._internal_isPercentage() || this._internal_isIndexedTo100()) return;
    if (this._private__scaleStartPoint === null) return;
    this._internal_setMode({
        _internal_autoScale: false,   // ← 手动缩放价轴 = 关闭自适应
    });
    ...
}
```

`_internal_setMode` 把 `autoScale` 写进选项（`:5575-5579`）。由于本项目用库默认 `handleScale`（未覆写），价轴缩放手势是开启的，用户一旦在价轴上拖动即触发此路径。**这就是 autoScale 由初始 true 变 false 的唯一现实来源**。

### 3.4 反证闭环：双击价轴会 `resetPriceScale` → 复位 autoScale 并重拟合（即恢复）

双击价轴触发 `reset`：

```js
// lightweight-charts.development.mjs:9698-9700  (axisDoubleClickReset.price 默认开)
_private__mouseDoubleClickEvent(e) {
    if (this._private__options.handleScale.axisDoubleClickReset.price) {
        this._internal_reset();
    }
}
// → :9611-9614 _internal_reset() → model._internal_resetPriceScale(pane, priceScale)
// → :6380-6386 _internal_resetPriceScale:
_internal_resetPriceScale(priceScale) {
    const visibleBars = this._private__timeScale._internal_visibleStrictRange();
    priceScale._internal_setMode({ _internal_autoScale: true });   // ← 复位为 true
    if (visibleBars !== null) priceScale._internal_recalculatePriceRange(visibleBars);  // ← 立刻重拟合
    this._internal_updateAllSources();
}
```

这解释了复现步骤 6「双击价轴 K 线立刻回来」：复位把 autoScale 拨回 true 并按当前 K 线重算价区间。**修复杠杆**正在于此——`applyOptions` 会直接 `merge` 写入 `autoScale`（`:5533-5534`），所以应用层只要在「切换标的」时对价轴 `applyOptions({ autoScale: true })`（或等效复位），即可让随后的 `setData` 恢复自动居中。

### 3.5 根因结论

> **切换标的时，`CandleChart` 的 K 线更新只调用 `series.setData()`，从不重置右价轴的 `autoScale`；而 lightweight-charts 在用户拖动价轴缩放后会把 `autoScale` 置为 `false`，此后 `recalculatePriceScale` 因 `!isAutoScale()` 短路、`setData` 不再重拟合价区间。于是切到价格量级相差很大的标的时，价区间仍停留在上一标的（如 6 万），新 K 线（如 0.1）整体落在可视价窗之外 → 中间面板「K 线消失」。**
>
> 「本来正常」是因为首屏 autoScale 仍为初始 true、拟合正确；问题在**用户对价轴做过一次缩放之后**才显形，且应用层**没有任何在换标的时复位价轴自适应的路径**（与 `chartSync.ts` 已经妥善处理的「横轴等宽对齐」形成对照：横轴几何被精心维护，纵轴自适应却完全无人接管）。

### 3.6 反证排除清单

- **不是时间轴/逻辑范围问题**：`setData` 后库会保留 bar spacing + 右偏移，新标的的最右若干根 K 线本就落在可见逻辑范围内（横轴并不空）；且若是横轴错位，首屏也会错，与「本来正常」矛盾。
- **不是 LIVE 价线把价轴撑大**：自适应不纳入自定义价线（§3.2，`:4945-4954`）。
- **不是回测成交/副图指标残留**：普通切标的场景这两者为空（`page.tsx:397-402`）；即便在回测/模拟模式它们挂在右价轴可加重症状，也非「纯切标的」这一用户主诉路径的必要条件。
- **不是等宽对齐副作用**：`alignPriceScaleWidths` / `realignPriceScaleWidths` 仅写 `minimumWidth`（`chartSync.ts:135-216`），不改 `autoScale`、不设价区间。

---

## 4. 影响面（Impact surface）

- **受影响功能**：左侧图表工作台的**主 K 线图**（`CandleChart`）在**跨价格量级切换标的**时的纵向显示。
- **受影响用户**：所有先对价轴做过缩放、再切换到价格量级差异大的标的的用户——这是看盘的高频核心路径（拖动价轴缩放 + 频繁切币几乎是看盘标配）。
- **加重条件**：两标的价格量级差越大（BTC ≈ 6 万 ⇄ 低价币 ≈ 0.1，相差 5~6 个数量级）越明显，新 K 线被推得越远，越像「彻底消失」。
- **关联但非主因**：在回测/模拟模式下，若右价轴同时挂着上一标的的成交连线/叠加指标（高价系列残留），即便 autoScale 为 true 也可能把价区间撑成 `[0.1, 60000]` 把新 K 线压成贴边细线——这是同一「纵轴未正确复位」家族的次生表现，建议修复时一并纳入验收（见 §6）。
- **现有兜底/绕过**：用户**双击价轴**可临时恢复（§3.4），但这要求用户知道该操作，不应作为产品行为依赖。
- **不影响**：后端、数据正确性、回测/模拟计算、横轴对齐（`chartSync` 负责，工作正常）。
- **严重度**：高可感知功能缺陷——对一个交易看盘工具，「切个币 K 线就没了」直接阻断核心读图，且无明显自救提示。

---

## 5. 候选修复方案（Candidate fixes）

| 方案 | 思路 | 侵入性 | 风险 | 工作量 | 备注 |
|---|---|---|---|---|---|
| **A（推荐）** 换标的时复位价轴自适应 | 给 `CandleChart` 传入显式的「视图复位键」（`instId` + `bar`，或二者拼成的 `resetKey`），在仅以该键为依赖的 effect 里对**右价轴（及净值左轴）**`applyOptions({ autoScale: true })`，并可选 `timeScale().fitContent()`（同时复位横向缩放/滚动）。复位后随后的 `setData` 即按新数据自动重拟合。 | 低（`CandleChart` 增一个 prop + 一个小 effect；`ChartWorkspace`/`page` 接线传 `instId`/`bar`） | 低 | 小 | 直击根因，且**只在切标的/切周期时复位**，不干扰同一标的内用户对价轴的手动缩放（实时每秒 `setData` 不会反复抢回自适应）。需注意复位 effect 可能早于新 candles 到达执行——无碍：重新置 true 的 autoScale 会持续生效，新 `setData` 到来时自动拟合。 |
| **B** 每次 `setData` 后无条件复位 autoScale | 在 K 线更新 effect 末尾追加 `series.priceScale().applyOptions({ autoScale: true })`。 | 极低（一行） | 中 | 极小 | 能修掉「消失」，但会在**实时模式每 ~1s 的 `setData`**（forming/live 合并，`page.tsx:220-223`）上反复把自适应抢回——用户在同一标的内手动缩放的价轴会被秒级弹回，**实时看盘体验回归**。不可取为主方案。 |
| **C** 仅当新数据落在可见价窗外才复位 | `setData` 后读当前可见价区间，与新 K 线 [min,max] 求交，无交集才强制 `autoScale=true` 重拟合。 | 中 | 中 | 中 | 既保留同一标的内手动缩放、又能在「真的看不见」时自救。但 v4.2 读取「当前可见价区间」缺直接公开 API（需用 `series.coordinateToPrice` 反推绘图区上下边界），实现更绕、回归面更大；更适合作为 A 的增强而非首选。 |

---

## 6. 推荐修复（Recommended fix）及理由

**推荐方案 A：在「标的 / 周期切换」这一离散事件上，显式复位主图价轴的 `autoScale`（必要时配合 `fitContent` 复位横轴），让随后的 `setData` 自动把新标的 K 线拟合到合适价位。**

理由：

1. **直击且仅触及根因**：症状的唯一直接成因是「autoScale 被关后，换标的的 `setData` 不再重拟合价区间」。方案 A 在换标的处把 autoScale 拨回 true（等价于库 `resetPriceScale` 在双击时做的事，§3.4），缺失的复位路径被补上，新数据即自动居中。
2. **不回归实时手动缩放**：复位只绑定 `instId`/`bar` 变化，**不绑定**每次 `candles` 变化；同一标的内的实时秒级 `setData` 不会触发复位，用户手动缩放的价轴得以保留（避免方案 B 的体验回归）。
3. **破坏面最小、与既有架构同构**：改动集中在 `CandleChart`（一个新 prop + 一个小 effect）与极少量接线；不动数据流、不升级依赖、不改 `chartSync` 横轴对齐。与「横轴由 `chartSync` 维护、纵轴在换标的处复位」的职责划分自然契合。

落地要点（供 Subtask #2 实施参考，本节不写修复代码）：

- 从 `page.tsx` / `ChartWorkspace` 把 `instId`、`bar` 透传给 `CandleChart`，组成稳定的复位键（例如 `${instId}|${bar}`）。
- 新增**仅依赖该复位键**的 effect：对 `candleSeriesRef.current?.priceScale().applyOptions({ autoScale: true })` 复位；若净值视图存在，对左价轴（`equitySeriesRef` 所在 `left` 轴）一并复位；可选 `chartRef.current?.timeScale().fitContent()` 复位横向缩放/滚动，让换标的「图形以合适方式重新展示」更彻底。
- **兼顾次生表现（§4 关联项）**：换标的复位时，若右价轴上仍残留上一标的的成交连线/叠加指标，应确保它们也随新标的清理/重算后再参与自适应，避免 `[0.1, 60000]` 把新 K 线压成贴边线。
- **回归校验**：补一条针对该行为的测试——「切换标的（含跨价格量级）后，主图价轴 `autoScale` 应被复位为 true 并按新数据重拟合」，把当前测试缺口补上（现有 `chartSync.test.ts` 只覆盖横轴广播与等宽，未覆盖纵轴自适应复位）。
- **手动验收**：BTC（≈6 万）上拖动价轴缩放 → 切到 DOGE（≈0.1）→ K 线应自动居中显示，无需双击价轴；再切回 BTC 亦自动居中；同一标的内手动缩放价轴后，实时 tick 不应把缩放弹回。

---

## 附：关键证据索引（代码引用）

- `apps/web/components/Chart/CandleChart.tsx:305-317` — K 线更新 effect：**只 `setData`，无任何价轴自适应复位**（根因所在）。
- `apps/web/components/Chart/CandleChart.tsx:161-187` — `createChart` 未覆写 `handleScale`，沿用库默认（允许拖动价轴缩放 → 可关闭 autoScale）。
- `apps/web/components/Chart/CandleChart.tsx:294-301` — `LIVE` 自定义价线（经证伪不影响自适应）。
- `apps/web/lib/chartSync.ts:135-216` — 等宽对齐只改 `minimumWidth`，不碰 `autoScale`（横轴被维护、纵轴无人接管的对照）。
- `apps/web/app/page.tsx:169-183` — `candlesQ` 未设 `keepPreviousData`；`:220-223` — `candles` 组装（实时每秒变化触发 `setData`）。
- `apps/web/app/page.tsx:397-402` — 非回测/非模拟模式 `overlayTrades` 为空（排除残留高价系列为主因）。
- `apps/web/lib/store.ts:165` — 默认 `instId: "BTC-USDT-SWAP"`（高价量级，复现起点）。
- `apps/web/components/Chart/ChartWorkspace.tsx:214-219` — 标的列表 `onSelect` = `store.setInst`（切换入口）。
- lightweight-charts v4.2 源码（`apps/web/node_modules/lightweight-charts/dist/lightweight-charts.development.mjs`）：
  - `:6374-6378` — `recalculatePriceScale` 在 `!isAutoScale()` 时**短路不重算**（autoScale=false 即价区间钉死）。
  - `:5850-5859` — `scaleTo`（拖动价轴）→ `setMode({ autoScale:false })`（autoScale 被关的唯一现实来源）。
  - `:5533-5534` / `:5575-5579` — `applyOptions`/`setMode` 写入 `autoScale`（修复杠杆）。
  - `:4928-4956` — 自适应仅取 bar low/high + primitives，不含自定义价线。
  - `:6380-6386` / `:9698-9700` — 双击价轴 → `resetPriceScale` 复位 autoScale 并重拟合（「双击即恢复」反证）。

---

## 评审结论（Review Verdict）

> 评审人：Subtask #1（分析报告评审与修复）
> 日期：2026-06-24
> 角色：资深评审（对根因证据、候选修复的边界/回归做对抗式复核）
> 结论：**根因成立、证据链经逐条复核全部属实；推荐方案 A 成立**。下附证据复核结果、补强证据、边界/回归审查，以及**唯一推荐修复**与硬性实施约束。

### A. 根因证据复核（逐条核对源码，全部命中）

对报告引用的每一处行号都打开源码核对，与正文描述完全一致，未发现夸大或张冠李戴：

| 引用 | 复核结果 |
|---|---|
| `CandleChart.tsx:305-317` K 线更新 effect | ✅ 属实：依赖仅 `[candles]`，体内**只有** `series.setData(data)` + 回写 `earliestRef`，无任何 `autoScale` / `fitContent` / 价轴复位。 |
| `CandleChart.tsx:161-187` `createChart` | ✅ 属实：仅设 layout/grid/crosshair/价轴 `minimumWidth`/timeScale 可见性/`autoSize`，**未覆写 `handleScale`** → 沿用库默认（默认允许拖动价轴缩放、双击价轴复位）。 |
| `CandleChart.tsx:294-301` LIVE 价线 | ✅ 属实：`createPriceLine`，经证伪不进入自适应（见下）。 |
| `chartSync.ts:135-216` 等宽对齐 | ✅ 属实：`alignPriceScaleWidths`/`realignPriceScaleWidths`/`applyMinWidth*` **只读写 `priceScale().width()` 与 `minimumWidth`**，全程不触碰 `autoScale`、不设价区间。 |
| `page.tsx:169-183` `candlesQ` | ✅ 属实：`retry:0`，**无 `keepPreviousData`/`placeholderData`**；切标的时 queryKey 变 → `data` 暂为 `undefined`。 |
| `page.tsx:198-209` 切标的清栈 effect（评审补充） | ✅ 关键佐证：依赖 `[instId, bar, ...]`，会 `setOlder([])/setLive([])/setForming(null)`，故 `candles`（`:220-223` 的 `mergeCandles`）在切标的瞬间**先收敛为 `[]`**，再随新数据填充——即 `candles` 走 `旧 → [] → 新` 两段提交，而非单提交突变。 |
| `page.tsx:397-402` `overlayTrades` | ✅ 属实：非 `paper`、非 `run-range && singleRunMode` 时为 `undefined`（纯切标的看盘场景右价轴无成交连线残留）。 |
| `store.ts:165` 默认标的 | ✅ 属实：`instId: "BTC-USDT-SWAP"`（高价量级，复现起点）。 |
| `ChartWorkspace.tsx:214-219` 切换入口 | ✅ 属实：`InstrumentSidebar` `onSelect={setInst}`。 |
| lwc `:6374-6378` 短路 | ✅ 属实：`if (priceScale === null || !priceScale._internal_isAutoScale()) return;`。 |
| lwc `:5850-5859` `scaleTo` | ✅ 属实：进入即 `setMode({ _internal_autoScale: false })`。 |
| lwc `:5533-5534`/`:5555-5557`/`:5575-5579` | ✅ 属实：`applyOptions` 用 `merge` 写选项、`isAutoScale()` 读 `options.autoScale`、`setMode` 写 `autoScale`。 |
| lwc `:4928-4956` 自适应取值 | ✅ 属实：蜡烛系列取 `Low/High` 两个 plot + `_private__primitives`，**不含 `createPriceLine` 的价线** → LIVE 价线被正确排除。 |
| lwc `:6380-6386`/`:9698-9700` 双击复位 | ✅ 属实：双击价轴 → `reset` → `resetPriceScale` 置 `autoScale=true` 并 `recalculatePriceRange` → 即「双击即恢复」反证。 |

根因链（autoScale 被用户拖轴关掉 → 切标的 `setData` 因 `!isAutoScale()` 短路不重拟合 → 新价量级 K 线落在旧价窗外「消失」）**因果闭合、反证（双击恢复）成立**，无逻辑缺口。报告对替代假说（时间轴错位 / LIVE 价线撑轴 / 回测成交残留 / 等宽对齐副作用）的逐一排除亦经核对成立。

### B. 评审补充证据：修复杠杆的内部闭环（新增引用）

方案 A 的关键前提是「换标的处对价轴 `applyOptions({ autoScale: true })` 即可让随后的 `setData` 自动重拟合」。为使该前提**airtight**，补全 `IPriceScaleApi.applyOptions` 的完整内部链路（原报告未展开）：

- `lwc:12381-12382` — 公开 `IPriceScaleApi.applyOptions(options)` → `model._internal_applyPriceScaleOptions(priceScaleId, options)`。
- `lwc:7522-7534` — `_internal_applyPriceScaleOptions`：`"right"` → `model._internal_applyOptions({ rightPriceScale: options })`。
- `lwc:6185-6195` — `applyScaleOptions`：`rightPriceScale._internal_applyOptions(options.rightPriceScale)` → `merge` 写入 `autoScale=true`（即 `:5533-5534`）。
- `lwc:7506-7521` — `model._internal_applyOptions` 末尾**无条件调用 `this._internal_fullUpdate()`**（`:7477-7479` → `_private__invalidate(InvalidateMask._internal_full())`）。

结论：`candleSeriesRef.current?.priceScale().applyOptions({ autoScale: true })` 具有**双重保证**——① 把 `autoScale` 持久置回 `true`（此后任何 `setData` 都会重拟合，与首屏 autoScale=true 的正常路径同源）；② 其自身触发 `fullUpdate` 作为兜底刷新。因此该复位**对「复位 effect 与 setData effect 的先后」不敏感**，比原报告「仅靠随后 setData 拟合」的论证更稳。

### C. 候选修复的边界 / 回归审查

1. **实时手动缩放不回归（方案 A vs B 的胜负手）**：复位只绑定 `instId`/`bar` 离散变化，**不绑定** `candles`。同一标的内实时每秒 `setData`（`page.tsx:220-223`）不会触发复位；用户在该标的内拖轴关掉的 `autoScale` 得以保留。方案 B（每次 `setData` 后无条件复位）会被实时 tick 每秒抢回自适应、把用户缩放秒级弹回，**确认为体验回归，不可取**。方案 C 因 v4.2 无公开「读当前可见价区间」API、需 `coordinateToPrice` 反推，实现绕、回归面大，**仅宜作增强**。三者对比成立。

2. **React effect 触发时序（唯一真实实现风险，必须约束）**：
   - *加载态路径*（首次访问该标的，`candlesQ` 无缓存）：`candles` 走 `旧 → [] → 新` 两段提交（B 节 `page.tsx:198-209` 佐证）。复位 effect 在 `instId` 变化的那一提交先跑（此时 `candles=[]`，`setData` effect 因 `length===0` 早退、不清屏），`autoScale` 已置 true；新数据到达的后一提交 `setData` 自动拟合。**天然安全**。
   - *缓存命中路径*（切回曾看过的标的，react-query 同步给出缓存）：`instId` 与 `candles` 可能在**同一提交**变化，此时两个 effect 同帧触发，**按声明顺序执行**。
   - **硬约束**：复位 effect 必须**声明在 `[candles]` 的 `setData` effect（当前 `:305-317`）之前**，确保「先复位 autoScale，再 setData」。即便万一顺序颠倒，B 节的 `fullUpdate` 兜底也会在同帧自愈，但仍应以声明顺序作为第一保证，勿依赖兜底。

3. **次生表现：右价轴残留高价系列（报告 §4 已点名，确认为非阻断）**：右价轴上除蜡烛外还挂着主图叠加指标（MA/EMA/BOLL，`CandleChart.tsx:456-469`，`priceScaleId:"right"`）与回测/模拟的成交连线（`:393-417`，`priceScaleId:"right"`），它们**会参与右轴自适应**。
   - 叠加指标 effect 同样依赖 `[candles]`，切标的时会用新 K 线**重算**为新价量级值，**自愈**，不会持久把价区间撑成 `[0.1, 60000]`；最多在「蜡烛 setData(305) 已跑、指标 effect(456) 未跑」的**一帧内**出现瞬时压扁，下一微任务即纠正，肉眼基本不可感。
   - 成交连线仅在 `paper`/`run-range && singleRunMode` 下非空（`page.tsx:397-402`），**非「纯切标的」主诉路径**。
   - 故此项**不改变主修复方案**，仅纳入验收观察项即可，不应扩大改动面去「清残留系列」。

4. **`fitContent` 是可选增强、非最小修复**：用户主诉是**纵轴**（价格）「消失」；`autoScale` 复位是**必要且充分**的最小修复。`timeScale().fitContent()` 会一并复位**横轴缩放/滚动**，属额外行为——切标的时通常可接受（新标的=全新视图），但它**超出修复根因所需**，且会丢弃用户可能刻意设置的横向位置。按本项目「改动尽量小」准则，**不将 `fitContent` 列为必需**。

### D. 最终推荐修复（唯一）

**采纳方案 A，落点收敛为一条：在 `CandleChart` 中新增一个【仅依赖 `instId`/`bar` 复位键、且声明在 `[candles]` 的 `setData` effect 之前】的 effect，对主图右价轴执行 `candleSeriesRef.current?.priceScale().applyOptions({ autoScale: true })`。**

- **接线**：从 `page.tsx` / `ChartWorkspace` 把 `instId`、`bar` 透传给 `CandleChart`（新增两个 prop，或合成稳定的 `${instId}|${bar}` 复位键）。
- **复位范围**：右价轴（蜡烛所在）为**必需且充分**；若净值视图存在（`hasEquity`），可顺带对左轴一并 `applyOptions({ autoScale: true })`（成本极低的防御，非主诉所必需）。
- **可选增强（非必需，由实现者裁量）**：`chartRef.current?.timeScale().fitContent()` 复位横轴；若加入须明确接受「丢弃用户横向缩放/滚动」的取舍。
- **明确不做**：不改 `[candles]` 的 setData 逻辑使其每 tick 复位（=方案 B 回归）；不引入 `coordinateToPrice` 反推可见价区间（=方案 C 的复杂度）；不扩面去主动清理右轴残留系列（次生项自愈，超出根因）。

**理由（为何是它、且仅是它）**：
1. **直击且仅触及根因**：症状唯一直接成因是「autoScale 被关后换标的 `setData` 不再重拟合」；本修复在换标的离散事件处把 `autoScale` 拨回 true（等价于库 `resetPriceScale` 在双击时所做，§3.4 + B 节链路双重佐证），缺失的复位路径被补上，新数据即自动居中。
2. **零实时回归**：复位不绑 `candles`，同标的内实时 tick 不抢回自适应，用户手动缩放得以保留（避免方案 B）。
3. **破坏面最小、与既有架构同构**：改动集中在 `CandleChart`（一个 prop/复位键 + 一个小 effect）+ 极少接线；不动数据流、不升依赖、不改 `chartSync` 横轴对齐。与「横轴由 `chartSync` 维护、纵轴在换标的处复位」的职责划分自然契合，满足本项目「单文件/单函数阈值内、最小改动」准则。

**验收标准（交付 Subtask #2 / #3 校验）**：
1. BTC（≈6 万）上拖动价轴缩放 → 切到 DOGE（≈0.1）→ 主图 K 线**自动居中显示**，无需双击价轴；再切回 BTC 亦自动居中。来回切换稳定不复现。
2. 同一标的内手动缩放价轴后，实时 tick **不**把缩放弹回（验证未引入方案 B 回归）。
3. 开启 MA/EMA/BOLL 叠加指标后切标的，最终稳定态价区间贴合新标的，无持久压扁（次生项观察）。
4. 新增一条针对性单测，补当前缺口（现有 `chartSync.test.ts` 仅覆盖横轴广播/等宽，未覆盖纵轴自适应复位）：断言「切换标的（含跨价格量级）后，主图右价轴 `autoScale` 被复位为 `true`」。
