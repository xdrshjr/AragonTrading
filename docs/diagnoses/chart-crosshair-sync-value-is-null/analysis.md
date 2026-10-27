# 诊断报告：跨图十字光标贯穿在「目标系列无数据」时抛 `Error: Value is null`

> Bug slug: `chart-crosshair-sync-value-is-null`
> 模块：`apps/web/lib/chartSync.ts`（图表同步）+ `lightweight-charts@4.2.3`
> 角色：本节点仅做**诊断**，不写修复代码、不提交。

---

## 一、问题描述（Problem description）

在左侧 K 线工作台中移动鼠标（`mouseenter` / `mousemove`）时，控制台高频抛出未捕获异常并使十字光标贯穿失效：

```
lightweight-charts.development.mjs:226 Uncaught Error: Value is null
    at ensureNotNull (lightweight-charts.development.mjs:226:15)
    at ChartModel._internal_setAndSaveSyntheticPosition (lightweight-charts.development.mjs:7731:65)
    at ChartApi.setCrosshairPosition (lightweight-charts.development.mjs:13030:54)
    at Object.onCross [as _internal_callback] (chartSync.ts:77:21)   ← 我方代码
    ...
    at PaneWidget._internal_mouseEnterEvent (...)     // 报错 1
    at PaneWidget._internal_mouseMoveEvent (...)      // 报错 2（同因）
```

Next.js 运行时叠加报错（报错 2）精确指向我方源码：

```
lib\chartSync.ts (77:21) @ setCrosshairPosition
  75 |   if (!s) continue;
  76 |   if (param.time !== undefined) {
> 77 |     other.setCrosshairPosition(NaN, param.time, s);
     |           ^
  78 |   } else {
  79 |     other.clearCrosshairPosition();
```

- **触发动作**：鼠标进入 / 移动到某个**有数据**的图（主 K 线图）上。
- **现象**：每次 `mousemove` 都抛一次（日志中可见 `5×` 重复），十字光标无法贯穿到副图；在 Next.js dev 下还会弹出 Unhandled Runtime Error 遮罩。

> 说明：用户栈帧显示库内行号为 `7731` / `13030`，本仓库实际安装版本 `lightweight-charts@4.2.3` 对应行号为 `7710~7712` / `13000~13009`（不同 patch/构建产物行号略有偏移），抛错函数与逻辑完全一致，详见下文「根因分析」。

---

## 二、复现步骤（Reproduction steps）

### A. 应用内复现（端到端）

前置：副图（指标 pane）已开启，使得同步组内**至少有 2 个 chart 成员**，且其中一个成员的系列当前**没有数据点**。

1. 启动前端：仓库根目录 `pnpm --filter web dev`（或按 README「一键启动」）。
2. 打开工作台，等待主 K 线图（`CandleChart`）加载到真实行情、出现蜡烛。
3. 开启任意副图指标（MACD / RSI / KDJ / VOL），使 `IndicatorPane` 挂载并注册进同一个 `SyncGroup`（`CandleChart.tsx:518` 把 `syncRef.current` 透传给 `IndicatorPane`，后者在 `IndicatorPane.tsx:84` 注册）。
4. 制造「主图有数据、副图系列暂无数据」的窗口，任一即可：
   - **切换指标页签**：副图按 `key={def.key}` 重新挂载（`CandleChart.tsx:514`），新 `IndicatorPane` 注册进组后、其系列被 `setData` 填充前存在一帧空窗；
   - **切换标的 / 周期**：`candles` 先清空再到新数据，期间某成员系列为空；
   - **指标计算暂为空**：`def.compute(candles)` 在 candles 不足时返回空 `plot.data`，`reconcilePlots` 仍会建好系列但 `setData([])`（`plots.ts:39-81`）。
5. 在主 K 线图上移动鼠标 → 控制台抛 `Uncaught Error: Value is null`，每帧一次。

### B. 最小确定性复现（库 API 级，100% 必现）

下面这段直接复刻 `chartSync.ts:74-77` 的调用形态——「目标系列存在但无数据」，可在任意 lightweight-charts v4.2 环境稳定复现，不依赖 React/时序：

```ts
import { createChart } from "lightweight-charts";

// 源图：有数据
const a = createChart(document.getElementById("a")!);
const sa = a.addLineSeries();
sa.setData([{ time: 1, value: 10 }, { time: 2, value: 11 }]);

// 目标图：系列已创建，但从未 setData（无任何数据点）
const b = createChart(document.getElementById("b")!);
const sb = b.addLineSeries();           // 注意：sb 非空对象，能通过 `if (!s) continue` 守卫

// 复刻 chartSync.onCross 对“其余图”的广播：
b.setCrosshairPosition(NaN, 2, sb);     // ← 抛 Error: Value is null
```

> 关键点：`sb` 是一个**合法的非 null 系列对象**，因此 `chartSync.ts:75` 的 `if (!s) continue;` 守卫**放行**它；崩溃发生在库内部，因为该系列**没有数据点 / 没有 firstValue**。

---

## 三、根因分析（Root cause analysis）

### 3.1 调用链与抛错点（证据：代码引用）

**我方触发点** —— `apps/web/lib/chartSync.ts:68-86`：

```ts
const onCross = (param: MouseEventParams) => {
  ...
  for (const [other, meta] of members) {
    if (other === chart) continue;
    const s = meta.getSeries?.();
    if (!s) continue;                              // chartSync.ts:75  ← 只判“系列对象是否存在”
    if (param.time !== undefined) {
      other.setCrosshairPosition(NaN, param.time, s);  // chartSync.ts:77  ← 抛错处
    } else {
      other.clearCrosshairPosition();
    }
  }
};
```

**库公共 API** —— `lightweight-charts.development.mjs:13000-13009`：

```js
setCrosshairPosition(price, horizontalPosition, seriesApi) {
    const series = this._private__seriesMap.get(seriesApi);
    if (series === undefined) { return; }                       // 系列未注册→静默返回
    const pane = ...._internal_paneForSource(series);
    if (pane === null) { return; }
    ..._internal_setAndSaveSyntheticPosition(price, horizontalPosition, pane);  // ↓
}
```

**真正抛错的内部方法** —— `lightweight-charts.development.mjs:7707-7714`：

```js
_internal_setAndSaveSyntheticPosition(price, horizontalPosition, pane) {
    const priceScale = pane._internal_defaultPriceScale();
    const firstValue = priceScale._internal_firstValue();
    const y = priceScale._internal_priceToCoordinate(price, ensureNotNull(firstValue)); // 7710 ★
    const index = this._private__timeScale._internal_timeToIndex(horizontalPosition, true);
    const x = this._private__timeScale._internal_indexToCoordinate(ensureNotNull(index)); // 7712 ★
    this._internal_setAndSaveCurrentPosition(x, y, null, pane, true);
}
```

`ensureNotNull` —— `lightweight-charts.development.mjs:203-208`：

```js
function ensureNotNull(value) {
    if (value === null) { throw new Error('Value is null'); }   // 226 行即此处
    return value;
}
```

### 3.2 为何 `firstValue` / `index` 会是 `null`

两个 `ensureNotNull` 守护的值，在「目标系列无数据」时**都会**变成 `null`：

1. **`firstValue` 为 null**（`7710` 行先求值，通常先在这里抛）
   `PriceScale._internal_firstValue()` —— `lightweight-charts.development.mjs:5798-5810`：

   ```js
   _internal_firstValue() {
       let result = null;
       for (const source of this._private__dataSources) {
           const firstValue = source._internal_firstValue();
           if (firstValue === null) { continue; }
           if (result === null || firstValue._internal_timePoint < result._internal_timePoint) {
               result = firstValue;
           }
       }
       return result === null ? null : result._internal_value;   // 该价轴下所有系列都无数据 → null
   }
   ```

2. **`index` 为 null**（若 `firstValue` 侥幸非空，则在 `7712` 行抛）
   `TimeScale._internal_timeToIndex()` —— `lightweight-charts.development.mjs:6742-6746`：

   ```js
   _internal_timeToIndex(time, findNearest) {
       if (this._private__points.length < 1) {   // 目标图时间轴无任何时间点
           return null;                            // ← 直接 null
       }
       ...
   }
   ```

**结论**：两条 null 路径都收敛到同一个事实——**被广播十字光标的「目标 chart」当前没有任何数据点**。`setCrosshairPosition` 需要把 `time` 映射成 x 坐标、把价格映射成 y 坐标，空数据下两次映射都拿不到锚点，于是库主动 `throw`。

### 3.3 守卫缺陷（直接根因）

`chartSync.ts:75` 的守卫 `if (!s) continue;` 只判断 **`getSeries()` 是否返回了系列对象**，**没有判断该系列是否含数据点**：

- 主图成员：`getSeries = () => candleSeriesRef.current`（`CandleChart.tsx:238`）。蜡烛系列在建图时即创建（`CandleChart.tsx:198`），但当 `candles` 为空时 `setData` 被跳过（`CandleChart.tsx:305-317`，第 307 行 `if (!series || candles.length === 0) return;`）——**系列存在但无数据**。
- 副图成员：`getSeries = () => mapRef.current.values().next().value ?? null`（`IndicatorPane.tsx:84`）。指标系列由 `reconcilePlots` 创建后 `setData(plot.data)`（`plots.ts:39-81`）；当 `def.compute(candles)` 返回空数据（candles 不足/为空）时，系列存在但 `data` 为空。

只要同步组内出现「源图有数据（`param.time` 有值）+ 某目标图系列存在但无数据」这一**非对称状态**，移动鼠标即触发崩溃。

### 3.4 为何单测没有拦住（证据）

`apps/web/lib/chartSync.test.ts:41` 把 `setCrosshairPosition` 打桩成**空实现**：

```js
const chart = {
  ...
  setCrosshairPosition: () => {},      // ← 永不抛错的 stub
  clearCrosshairPosition: () => {},
};
```

桩永远不抛，因此「目标系列为空→库内 `ensureNotNull` 抛错」这条真实路径在测试里完全不可见，回归用例缺失（违反 `CLAUDE.md` §七「修 bug 必须先写能稳定复现的测试」——此处需补）。

---

## 四、影响面（Impact surface）

- **受影响功能**：跨图十字光标贯穿（`chartSync.ts` 的 `onCross` 广播）。一旦组内有空数据成员，整条 `onCross` 在 `setCrosshairPosition` 处抛错并中断，后续成员也无法被贯穿。
- **受影响用户/场景**：
  - 所有开启了**副图指标 pane** 的工作台用户（主图 + 副图 = 2 成员同步组）。
  - **行情加载中 / 切换标的 / 切换周期 / 切换指标页签**等存在「一图已就绪、另一图系列暂空」窗口期的所有交互。
  - **多策略对比 / 实时（live）模拟**场景下数据分时到达，空窗更易出现（与 `docs/diagnoses/live-candle-refresh-stalls`、`multi-strategy-paper-*` 等已知不稳定路径同源时序）。
- **严重度**：功能性中高。虽不致数据错误或崩溃整页（生产构建下为 `Uncaught Error`，十字光标静默失效），但在 Next.js dev 下会弹 Unhandled Runtime Error 遮罩、并以**每帧一次**的频率刷爆控制台，严重干扰使用与调试；属高频、易触发、用户可感知的回归。

---

## 五、候选修复（Candidate fixes）

| 方案 | 做法 | 侵入性 | 风险 | 工作量 | 备注 |
|---|---|---|---|---|---|
| **A（推荐）** | 在 `onCross` 中把守卫升级为「系列存在**且**有数据」并对调用加 `try/catch` 兜底：`if (!s || s.data().length === 0) continue;` + `try { other.setCrosshairPosition(...) } catch {}` | 低（仅改 `lib/chartSync.ts` 数行） | 低 | 低 | `ISeriesApi.data()` 在 v4.2 存在（typings.d.ts:1810）。判空短路避免高频抛异常；`try/catch` 兜住 `time` 越界等残余边界。与既有约定一致（见下） |
| B | 仅 `try/catch` 包裹 `setCrosshairPosition`，不加 `data()` 判空 | 低 | 中 | 低 | 每次 `mousemove` 命中空系列都要**抛+捕获**异常，高频路径有性能与日志噪音成本；不如 A 用 `data()` 提前短路 |
| C | 在两处 `getSeries` 回调内自行过滤空系列（`CandleChart.tsx:238`、`IndicatorPane.tsx:84` 返回 `data().length ? s : null`） | 中（改 2 处组件） | 中 | 中 | 防护点分散、未来新增成员易漏；不如在 `chartSync` 单点收口 |

### 既有约定佐证（方案 A 与现有代码风格一致）

同文件对**时间轴广播**早已采用「无数据则忽略」的 `try/catch` 模式 —— `chartSync.ts:53-57`：

```ts
try {
  other.timeScale().setVisibleLogicalRange(range);
} catch {
  /* 目标图暂无数据时忽略 */
}
```

十字光标广播缺了同样的防护，方案 A 正是把这一既有约定对齐到 `onCross`。

---

## 六、推荐修复（Recommended fix）

**采用方案 A**：在 `apps/web/lib/chartSync.ts` 的 `onCross` 中将守卫升级为「存在且有数据」，并对 `setCrosshairPosition` 加 `try/catch` 兜底。示意（供修复节点参考，本节点不落代码）：

```ts
for (const [other, meta] of members) {
  if (other === chart) continue;
  const s = meta.getSeries?.();
  if (!s || s.data().length === 0) continue;       // ← 新增：系列无数据点则跳过
  if (param.time !== undefined) {
    try {
      other.setCrosshairPosition(NaN, param.time, s);
    } catch {
      /* 目标图数据尚未就绪 / time 越界，忽略本次贯穿（与 setVisibleLogicalRange 同约定） */
    }
  } else {
    other.clearCrosshairPosition();
  }
}
```

**理由**：
1. **直击根因**：崩溃的充要条件是「目标系列无数据」，`s.data().length === 0` 正是对该条件的精确、廉价判定，并在抛异常前短路，避免方案 B 的高频异常开销。
2. **单点收口、低风险**：仅改同步层一处，不动各图组件的数据流；`try/catch` 与 `chartSync.ts:53-57` 既有「无数据忽略」约定完全一致，认知与维护成本最低。
3. **API 安全**：`ISeriesApi.data()` 是 v4.2 公开方法（typings.d.ts:1810），无需触碰库内部。

**配套（强烈建议同修复一并提交）**：在 `chartSync.test.ts` 增加回归用例——把桩的 `setCrosshairPosition` 改为「目标系列无数据时抛 `Error('Value is null')`」，断言 `onCross` 不再向外抛、且对有数据成员仍正常贯穿。这样把本次回归固化为护栏（`CLAUDE.md` §七）。当前桩为永不抛的空实现（`chartSync.test.ts:41`），是该 bug 漏网的直接原因。

---

## 附：关键证据索引

- 触发点：`apps/web/lib/chartSync.ts:68-86`（`onCross`），守卫缺陷 `:75`，抛错调用 `:77`
- 既有同约定 `try/catch`：`apps/web/lib/chartSync.ts:53-57`
- 主图成员注册 / 空数据来源：`apps/web/components/Chart/CandleChart.tsx:238`、`:305-317`
- 副图成员注册 / 空数据来源：`apps/web/components/Chart/IndicatorPane.tsx:84`、`:110-125`；`apps/web/components/Chart/plots.ts:39-81`
- 库内抛错链：`lightweight-charts.development.mjs:13000-13009`（`setCrosshairPosition`）→ `:7707-7714`（`_internal_setAndSaveSyntheticPosition`，抛于 `7710`/`7712`）→ `:203-208`（`ensureNotNull`）
- null 来源：`:5798-5810`（`firstValue`）、`:6742-6746`（`timeToIndex`）
- 测试盲点：`apps/web/lib/chartSync.test.ts:41`（`setCrosshairPosition` 空桩）
- 修复可用 API：`lightweight-charts` typings.d.ts:1810（`ISeriesApi.data()`）
