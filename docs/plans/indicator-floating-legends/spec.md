# 技术指标透明浮动图例方案（indicator-floating-legends）

**版本：v2**

## 评审记录

- P1（已修正）：浮层层级原方案建议使用 `z-20`，但当前 `TradeTooltip` 也是 `z-20`，loading / error 遮罩没有显式 z-index。若后续直接实现，透明图例可能压过成交提示、加载提示或错误遮罩。正文已改为图例 `z-10`，成交 tooltip / loading 至少 `z-30`，错误遮罩至少 `z-40`。
- P1（已修正）：浮层 DOM 示例把图例作为主图容器的兄弟节点，虽然 `top-2` 视觉上接近主图左上角，但宽度、换行和避让边界仍以整个 `CandleChart` 根节点为准，窄窗口下可能压到右价轴或副图区域。正文已改为在主图 chart 层外包一层 `relative flex-[4]` wrapper，并把图例绝对定位在该 wrapper 内。
- P1（已修正）：窄屏文案降级策略不足。原方案只写了 `max-w`、`flex-wrap` 和“不超过两行”，但没有给实现者可测试的数据字段或裁剪顺序。正文已补充 `compactLabel` 和 `priority`，要求先切换短标签，仍超过两行时按优先级隐藏低优先级项。
- P2（已修正）：图例派生函数文字描述提到输入包含 `INDICATORS` 注册表，但接口草案没有暴露 `registry` 参数，降低单测和未来扩展的可控性。正文已补充可选 `registry?: readonly IndicatorDef[]`，默认使用 `INDICATORS`。
- P2（已接受）：本方案不展示 MA5/MA10 等子线级图例，只展示指标级图例。该取舍符合“透明、靠左、顶级、低干扰”的目标；若用户后续要求逐线颜色说明，应作为独立增强处理。

## Overview

本方案解决当前 K 线技术指标图例位置不符合用户预期的问题。用户要求「各种技术指标的图例」不要再显示在 K 线图右侧，而是显示在上方 K 线图面板的上方靠左，靠近上方的「K 线价格走势 / 净值」横栏，并且采用文字浮动展示：背景透明、可以看到背后的 K 线、视觉精致且不破坏专业行情终端的交互效率。该需求只改变图例呈现层，不改变指标开关、指标计算、K 线数据、实时行情、回测轨迹、净值曲线和副图页签的既有行为。

当前代码中，指标体系已经由 `apps/web/lib/indicators.ts` 的 `INDICATORS` 注册表统一驱动，主图叠加指标（MA/EMA/BOLL）在 `CandleChart.tsx` 中通过 `reconcilePlots` 挂载到主图右价轴，副图指标（MACD/RSI/KDJ/VOL）通过 `IndicatorPane.tsx` 在下方约 20% 容器中以页签单显。前序实现已经将指标 series 的 `lastValueVisible` 和 `priceLineVisible` 关闭，避免指标最新值污染右侧价轴；但 `title`、副图左上角标签、参考线轴标等信息仍分散在图表内部，缺少一个稳定、统一、靠左的上方图例区。本轮应把「图例」收敛成一个 React 浮层，显示在主 K 线图左上角，视觉上紧贴 `ChartHeaderBar` 下沿，既让用户一眼看到当前启用指标，又不占用额外布局高度。

设计目标是小范围、可回归、面向后续实现的增量方案：保留现有 lightweight-charts 生命周期与绘图同步逻辑，只新增一个轻量图例数据派生函数和一个展示组件，并在 `CandleChart` / `IndicatorPane` 中关闭或移除重复标签。这样后续工程师可以逐文件实现，不需要重新设计图表架构，也不会影响 OKX 实时流、历史 K 线刷新、回测成交折线、市场深度面板和桌面端打包。

## Technical Design

### Architecture

整体架构为「指标注册表 → 纯函数派生图例项 → React 透明浮层 → chart series 只负责绘图」。新增的纯函数位于 `apps/web/lib/indicatorLegend.ts`，输入为当前启用的 `indicators` 状态、当前可见副图页签 `activePaneKey`、以及可选的 `INDICATORS` 注册表，输出一组稳定排序的 `LegendItem`。新增组件 `apps/web/components/Chart/IndicatorLegendOverlay.tsx` 只负责渲染这组数据，不直接读取 store、不创建 chart、不计算指标值。

图例分两类展示：

1. 主图叠加类 overlay：MA、EMA、BOLL。只要用户在指标菜单中启用，就显示在浮层中，因为它们直接画在上方 K 线价格面板上。
2. 当前副图 pane：MACD、RSI、KDJ、VOL。副图现在是页签单显，因此浮层只显示当前 `activePaneDef` 对应的副图指标，不显示已启用但当前未选中的副图，避免与页签重复并降低信息噪声。

浮层放置在 `CandleChart` 根节点内部、主图 chart 容器之上，而不是放在 `ChartHeaderBar` 里。原因是 `ChartHeaderBar` 负责视图切换和 OHLC 数值横栏，属于固定布局行；用户要求的是「文字浮动显示、背景透明能看到背后的 K 线」，因此图例必须覆盖在 K 线画布上方。具体实现应给主图 chart 单独包一层 `relative min-h-0 w-full flex-[4]` 的主图层 wrapper，并把图例定位在该 wrapper 内部左上角，例如 `absolute left-3 top-2 z-10`。这会让图例紧贴 `ChartHeaderBar` 下方，同时让宽度、换行和点击穿透边界都受主图区域约束，不增加任何额外 DOM 高度，也不会推挤主图或副图。

### Key Code Paths

`ChartWorkspace.tsx` 当前负责渲染 `ChartHeaderBar`，然后在相邻的 `relative min-h-0 flex-1` 容器内渲染 `CandleChart` 或 `EquityChart`。本轮不改变该布局，只要求 `CandleChart` 内部的浮层位置与上方 header 视觉对齐。因为 `CandleChart` 的根节点已经是 `relative flex h-full w-full flex-col`，可以直接承载绝对定位浮层。

`CandleChart.tsx` 当前已有：

- `enabledPaneDefs = INDICATORS.filter((d) => d.category === "pane" && indicators[d.key])`
- `activeKey = selectActivePane(...)`
- `activePaneDef = enabledPaneDefs.find(...)`
- overlay 指标 effect：遍历 `INDICATORS` 并调用 `reconcilePlots(...)`
- 副图页签和 `IndicatorPane`

实现时应在这些派生值之后计算 `legendItems = buildIndicatorLegendItems({ indicators, activePaneKey: activeKey })`。组件渲染时把 `<IndicatorLegendOverlay items={legendItems} />` 放在主图层 wrapper 内，而不是作为整个 `CandleChart` 根节点的自由浮层。推荐结构：

```tsx
<div className="relative flex h-full w-full flex-col">
  <div className="relative min-h-0 w-full flex-[4]">
    <div ref={containerRef} className="h-full w-full" />
    <IndicatorLegendOverlay items={legendItems} />
  </div>
  ...
</div>
```

注意浮层覆盖的是主图层 wrapper，而不是整个 `CandleChart` 根节点；其 `top` 坐标以主图容器左上角为准。由于副图在主图层 wrapper 之外，`top-2` 不会落入副图区域。层级必须显式分工：图例使用 `z-10`；`TradeTooltip` 和 loading 提示至少使用 `z-30`；错误遮罩至少使用 `z-40`。这样成交提示、加载状态和错误状态永远高于装饰性图例。

`IndicatorPane.tsx` 当前在每个副图内部渲染：

```tsx
<span className="... absolute left-2 top-1 ...">
  {def.label}
  {def.paramsHint ? <span ...>{def.paramsHint}</span> : null}
</span>
```

本轮应移除或可选隐藏该副图内部标签，避免当前副图名称在浮层、页签、副图内部三处重复。推荐直接删除这段 span。副图页签已经显示 `MACD/RSI/KDJ/VOL`，浮层显示完整名称和参数，副图内部无需再显示标签。参考线（如 RSI 70/30）属于价格轴参考值，不是图例，不在本轮强制移动；若用户实际反馈右侧仍有“指标图例”来自参考线轴标，再作为后续独立需求处理。

`plots.ts` 当前对 indicator series 设置 `title: plot.title`，同时 `lastValueVisible:false`、`priceLineVisible:false`。为彻底避免 lightweight-charts 在右侧或内置状态区展示指标名称，建议给 `reconcilePlots` 增加一个可选配置 `showSeriesTitle?: boolean`，默认 `false`。当为 `false` 时，line / histogram 创建和更新都把 `title` 设为空字符串；当未来其他调用方确实需要内建标题时再显式开启。当前项目里 `reconcilePlots` 只服务指标，因此也可以直接清空 title，但可选配置的设计更稳妥。

### Sequence of Operations

1. 页面从 `useAppStore` 读取 `indicators` 和 `paneTab`，通过 `ChartWorkspace` 传入 `CandleChart`。
2. `CandleChart` 基于 `INDICATORS` 计算启用的 pane 指标集合，并通过 `selectActivePane` 得到当前副图 key。
3. `buildIndicatorLegendItems` 遍历传入的 `registry`（默认 `INDICATORS`）注册表，按注册表顺序收集启用的 overlay 指标；再追加当前活动 pane 指标。
4. `IndicatorLegendOverlay` 在主图层 wrapper 的左上角以透明文字浮层渲染所有图例项，宽度和换行只受主图区域约束。
5. `reconcilePlots` 继续负责 series 增删改和 `setData`，但不再通过 series title 暴露图例信息。
6. 用户切换指标开关、切换副图页签、切换主题或切换标的周期时，React props 和 memo 派生值自动刷新浮层；不新增任何网络请求或 chart 重建。

## File / Module Change Plan

| File / module | Change intent |
|---|---|
| `apps/web/lib/indicatorLegend.ts` | 新增纯函数和类型：从 `INDICATORS`、启用状态、当前 pane key 派生图例项。 |
| `apps/web/lib/indicatorLegend.test.ts` | 新增 Vitest 单测，覆盖 overlay 显示、未启用隐藏、当前 pane 单显、无 pane 时仅显示 overlay。 |
| `apps/web/components/Chart/IndicatorLegendOverlay.tsx` | 新增透明浮动图例组件，负责紧贴主 K 线图左上角渲染短文本和颜色点。 |
| `apps/web/components/Chart/CandleChart.tsx` | 引入图例派生函数和浮层组件；在主图 chart 上方渲染浮层；传入 `activeKey`；确保错误遮罩层级高于图例。 |
| `apps/web/components/Chart/IndicatorPane.tsx` | 删除副图内部左上角 `def.label / paramsHint` 标签，避免重复图例。 |
| `apps/web/components/Chart/plots.ts` | 增加 `showSeriesTitle?: boolean` 或直接清空 indicator series title，确保图例不再依赖 chart 右侧/内建标题。 |
| `apps/web/lib/indicators.ts` | 不改计算逻辑；仅在需要更精确图例文案时复用现有 `label`、`paramsHint`、`Plot.title`。 |
| `apps/web/components/Chart/ChartHeaderBar.tsx` | 原则上不改；如视觉验收发现浮层与 header 缺少对齐基准，只允许微调 `CandleChart` 浮层 top/left，不把图例塞进 header。 |

## Interface Design

本轮不新增 REST、WebSocket、CLI 或后端接口。所有变化都在前端渲染层完成。

新增前端纯函数接口：

```ts
export interface LegendItem {
  key: string;
  label: string;
  compactLabel: string;
  tone: "overlay" | "pane";
  priority: number;
  color?: string;
}

export interface BuildIndicatorLegendItemsInput {
  indicators: Record<IndicatorKey, boolean>;
  activePaneKey: IndicatorKey | null;
  registry?: readonly IndicatorDef[];
}

export function buildIndicatorLegendItems(
  input: BuildIndicatorLegendItemsInput,
): LegendItem[];
```

`label` 规则：

- overlay 指标使用 `def.label + paramsHint`，例如 `MA 均线 (5,10,20,60)`、`BOLL 布林带 (20,2)`。
- 当前 pane 指标使用 `def.label + paramsHint`，例如 `MACD (12,26,9)`。
- `compactLabel` 使用 `def.key.toUpperCase()` 加必要参数短写，例如 `MA`、`BOLL`、`MACD`、`RSI`；窄屏或两行溢出时优先显示该字段。
- `priority` 用于窄屏裁剪：当前活动 pane 最高；默认开启的 overlay 次之；用户额外开启的 overlay 再次；同级按注册表顺序稳定排序。
- 不展示未启用指标。
- 不展示已启用但当前未选中的 pane 指标。

新增组件接口：

```ts
interface IndicatorLegendOverlayProps {
  items: LegendItem[];
}
```

组件行为：

- `items.length === 0` 时返回 `null`。
- 根节点使用 `pointer-events-none`，避免遮挡十字光标、拖拽、缩放和 trade tooltip。
- 使用透明背景，不加卡片、不加毛玻璃、不加大面积底色。允许使用轻微文字阴影或 `drop-shadow` 提高可读性。
- 文本单行优先，空间不足时允许换行，但不应超过两行；移动端可在主图层 wrapper 内使用 `right-20 max-w-[calc(100%-96px)] flex-wrap`，给右侧价格轴至少预留 `PRICE_SCALE_MIN_WIDTH` 级别的空间。
- 当完整 `label` 两行仍放不下时，组件先切换为 `compactLabel`；若仍超过两行，再按 `priority` 从低到高隐藏图例项，且不能隐藏当前活动 pane。

## Data Model

本轮不改变后端数据库、API schema、Zustand 持久化结构或行情数据结构。新增的 `LegendItem` 是纯前端、纯渲染期的内存形状，不持久化、不传输、不写入 localStorage。

`LegendItem.key` 必须稳定，用于 React key。overlay 使用 `overlay:${def.key}`，pane 使用 `pane:${def.key}`。`label` 是正常宽度下的完整显示文本；`compactLabel` 是窄屏降级文本；`priority` 是 UI 裁剪排序，不参与业务语义。若未来希望展开到子系列级别，可以增加 `children?: LegendSubItem[]`，但本轮不做，因为用户明确要求的是各种技术指标的图例位置，而不是每条 MA5/MA10 子线的完整明细表。浮层应保持紧凑，避免遮挡 K 线。

颜色策略应复用现有设计 token 和指标色：

- overlay 总体标识可用 `var(--accent)` 或该指标第一条 plot 的主色。
- pane 标识可用 `var(--muted)` 加当前主题文字色。
- 如果实现选择展示子系列色点，必须从 `def.compute(candles, themeColors())` 中读取 plot color，但这会把图例派生与 K 线数据耦合。本轮建议不读取 candles，只显示指标级图例，降低刷新成本和复杂度。

## Testing & Acceptance Criteria

### Detailed Implementation Checklist

后续工程师实现时建议按以下顺序落地，避免在 UI 调整中误触图表生命周期。第一步只创建 `indicatorLegend.ts` 和同名测试，确认它不依赖 React、DOM、candles 或 theme。第二步创建 `IndicatorLegendOverlay.tsx`，先用静态 story-like data 在组件内自测样式，再接入 `CandleChart`。第三步修改 `CandleChart.tsx`，只增加 `useMemo` 派生和一个 JSX 浮层，不移动 `createChart` effect、不改变 `containerRef`、不改变 `syncRef`。第四步移除 `IndicatorPane.tsx` 内部重复标签，并确认副图页签仍能表达当前 pane。第五步处理 `plots.ts` 的 title 行为，优先以默认隐藏 series title 的方式完成，不引入 breaking API。第六步做视觉检查，最后再跑测试和构建。

The implementation must preserve three interaction guarantees. First, chart gestures remain native: the legend is decorative information only, so it must never receive pointer events, keyboard focus, drag handlers, wheel handlers, or hover state. Second, the legend updates from existing React state only: no polling, no WebSocket subscription, no chart API readback, and no extra indicator computation should be introduced. Third, the visual hierarchy stays quiet: the legend should be readable but secondary to candles, trade markers, live price line, and error states. Use small text, tabular spacing only where needed, modest color dots, and no solid panel background.

Accessibility requirements are intentionally lightweight because the chart itself is a dense visual workspace. The legend container may be `aria-hidden="true"` if the same indicator information remains available through `IndicatorMenu`; otherwise expose a concise `aria-label` such as `Active indicators: MA, BOLL, MACD`. Buttons are not allowed inside the overlay. The component should not become a tab stop. Text contrast must be checked in both themes, but the transparent presentation should rely on text color and subtle shadow rather than an opaque badge. If two-line wrapping still overlaps important chart content on narrow screens, prefer shortening labels (`MA`, `BOLL`, `MACD`) over adding background panels.

### Unit Tests

新增 `apps/web/lib/indicatorLegend.test.ts`：

- `returns_enabled_overlay_indicators_in_registry_order`：默认 MA+BOLL 启用时，返回 MA、BOLL；EMA 关闭不出现。
- `includes_only_active_pane_indicator`：MACD、RSI 都启用但 `activePaneKey="rsi"` 时，只追加 RSI，不追加 MACD。
- `omits_pane_when_active_key_null`：没有启用 pane 或 active key 为空时，不追加 pane 图例。
- `returns_empty_when_all_indicators_disabled`：所有指标关闭时返回空数组。
- `keeps_labels_human_readable`：label 包含 `def.label` 和非空 `paramsHint`，不使用内部 key 作为唯一展示文本。
- `provides_compact_labels_and_priorities`：每个 item 都有非空 `compactLabel` 和确定性的 `priority`，当前活动 pane 的优先级高于普通 overlay。
- `supports_custom_registry_for_unit_tests`：传入裁剪后的 registry 时仅按该 registry 派生图例，默认不传时使用 `INDICATORS`。

如果修改 `plots.ts` 的 `showSeriesTitle` 配置，应补充最小测试或通过现有组件测试覆盖：默认配置下 `addLineSeries` / `applyOptions` 收到的 `title` 为空字符串；显式 `showSeriesTitle:true` 时保留 `plot.title`。

### Visual / Interaction Acceptance

- 在价格视图中启用默认指标（MA、BOLL、MACD）后，主 K 线图左上角出现透明文字浮层，内容包含 MA、BOLL、MACD，位置紧贴 `ChartHeaderBar` 下方靠左。
- 浮层背景必须透明，不能是卡片、面板、实底标签或遮罩；K 线网格和蜡烛在文字后方仍可见。
- 浮层只覆盖主图层 wrapper；窗口变窄时最多两行，优先使用 `compactLabel`，仍不足时隐藏低优先级项，不得压住副图页签或右侧价格轴。
- 右侧价轴不再出现技术指标图例文字或指标最新值；右侧只保留价格刻度、实时价格线标签以及必要的参考线刻度。
- 切换指标菜单时，浮层即时更新；关闭全部指标后浮层消失。
- 切换副图页签时，浮层中的 pane 图例同步变更为当前页签指标。
- 鼠标在图例区域拖拽、滚轮缩放、十字光标移动不受影响；trade tooltip、loading 提示和错误遮罩显示时都必须高于图例。
- 切换浅色 / 深色主题后，浮层文本对比度可读，不出现刺眼背景块。
- 桌面端打包窗口中布局一致，不出现只剩文字、样式失效或白屏。

### Suggested Commands

实现完成后建议运行：

```powershell
cd apps/web
pnpm test -- indicatorLegend
pnpm test
pnpm build
```

如果具备本地环境，再运行根目录 `pnpm test`，并手动打开 `http://localhost:18761` 验证默认指标、开启 EMA、切换 RSI/KDJ/VOL、切换主题和桌面窄宽度。

## Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| 浮层遮挡 K 线或 trade tooltip | 用户难以查看图表细节 | 使用 `pointer-events-none`、小字号、最多两行、靠左上角；图例 `z-10`，tooltip / loading 至少 `z-30`。 |
| 与 `ChartHeaderBar` 信息拥挤 | 顶部区域显得杂乱 | 图例放在 chart 内部而非 header；header 保持 OHLC 职责，浮层使用低饱和文字。 |
| 指标名称仍由 lightweight-charts title 显示在右侧 | 用户认为需求未完成 | 在 `reconcilePlots` 默认清空 series title，并移除 `IndicatorPane` 内部重复 label。 |
| 读取 `def.compute` 生成颜色导致每 tick 额外计算 | 实时刷新成本增加 | 本轮图例只依赖注册表元数据，不依赖 candles，不执行指标计算。 |
| 副图页签、浮层、副图内部标签三处重复 | 视觉噪声 | 删除 `IndicatorPane` 内部 label；页签保留短名，浮层保留完整名和参数。 |
| 移动端或窄窗口文字溢出 | UI 破裂 | 浮层限制在主图层 wrapper 内，先切换 `compactLabel`；超过两行时按 `priority` 隐藏低优先级 item，并给右侧价格轴预留空间。 |
| 错误遮罩 / loading 提示被图例压住 | 错误状态不清晰 | 错误遮罩至少 `z-40`；loading pill 至少 `z-30` 且保持居中，可覆盖图例。 |

## Implementation Notes

后续实现必须保持本轮边界：不修改后端，不修改实时行情协议，不重写指标计算，不改变 `INDICATORS` 注册表语义，不改变 `paneTab` 持久化逻辑。重点是把图例变为一个独立、透明、靠左上方的展示层。若实现过程中发现右侧显示的并非 series title，而是 RSI 参考线价格标签或实时价格线标签，应先确认其是否属于「技术指标图例」；实时价格线和价格刻度不应被移除，RSI 70/30 这类参考线可暂时保留，因为它们是数值刻度，不是指标说明。

最终验收以用户视觉目标为准：打开 K 线价格走势面板后，指标说明应像专业终端里的轻量 overlay 一样浮在左上角，透明、不抢焦点、可读、稳定，且不再占据或污染右侧价轴区域。

## 评审结论

通过。

P0 / P1 问题已在 v2 正文中修正；剩余 P2 取舍不阻塞后续实现。
