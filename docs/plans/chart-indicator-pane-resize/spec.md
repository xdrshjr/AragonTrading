# 主图 K 线与副图技术指标可拖拽分隔线 — 开发方案

- **Feature**: `chart-indicator-pane-resize`
- **版本**: v2（评审修复）
- **作者**: 方案设计节点（Subtask #0）；评审与修复：方案评审节点（Subtask #1）
- **状态**: 已评审 —— 有条件通过（条件见文末「评审结论」）
- **关联需求**: K 线面板上方「主 K 线显示」与下方「技术指标显示」之间的分隔线需支持上下拖动，以调整两个面板的相对高度。向下拖放大主 K 线，向上拖放大技术指标面板。

---

## 评审记录（Review Notes）

> 评审节点（Subtask #1）逐节对照 `apps/web` 现有源码核验本方案，维度：可行性 / 完整性 / 一致性 / 规模适配。下列每条均已实地查证（行号为评审时所见）。所有 **P0/P1** 已在 v2 文档正文就地修复；**P2** 为非阻塞建议，留作实现指引。

### 已核验属实（无需改动）

- **store 改造链路**：`store.ts` 现为 `version: 21`，persist 键 `"aragon-ui"`，`skipHydration: true`，`migrate` 逐版分支止于 `if (version < 21)`，`setChartSplitRatio` 用 `Number.isFinite(n)` + `Math.min(Math.max(n, MIN), MAX)` 夹取，初始化器在 L312 写默认值，`partialize` 追加字段——方案的 21→22、setter 复刻、`if (version < 22)` 迁移、partialize/初始化器追加全部与现状范式一致。
- **几何换算**：`panelResize.ts#ratioFromRightEdge` 采用 `containerWidth > 0 ? … : 0` 除零守卫，夹取交由 store——`ratioFromTopEdge` 提案与其同构、正确（验算 `ratioFromTopEdge(0,500,400)=0.8`、`(100,400,300)=0.5` 均成立）。
- **手柄范式**：`PanelResizeHandle.tsx` 为 `role="separator"` 竖向手柄，含 `setPointerCapture` + `onPointerCancel`/`onLostPointerCapture` 完整生命周期；其 docstring 已明示「按朝向分文件」是既有约定。
- **CandleChart 结构**：根 `flex h-full w-full flex-col`、主图区 `flex-[4]`（L521）、副图容器 `flex min-h-[120px] flex-1 flex-col border-t`（L532，条件 `hasPane && activePaneDef`）、`autoSize: true`、挂载期 `ResizeObserver → alignPriceScaleWidths`——方案描述准确。
- **影响面**：`CandleChart` 新增 props 全部可选，生产环境唯一消费者为 `ChartWorkspace`（不受破坏）；`TradeTooltip` 为 `absolute` 定位（不参与 flex 流）；`panelResize.test.ts`/`store.test.ts` 均存在，且 `store.test.ts` 已有「v12 面板尺寸」用例可仿照。

### 问题清单

| 编号 | 严重度 | 维度 | 问题 | 处置 |
|------|--------|------|------|------|
| R1 | **P1** | 可行性 | §2.2-4 主图区 className 拟「保留 `min-h-0` 并追加 `min-h-[120px]`」。二者同为 `min-height` 工具类，**不可并存**——取胜结果取决于 Tailwind 生成 CSS 的先后顺序而非 className 字符串顺序，导致下限行为不确定（可能落在 0 而非 120px，副图被拉大时主图塌陷）。 | 已修复：v2 改为以 `min-h-[120px]` **替换** `min-h-0`（单一像素下限）。 |
| R2 | **P1** | 完整性/正确性 | §2.2-4 仅说「在主图区与副图容器之间渲染手柄，仅当 `hasPane && activePaneDef`」，未明确手柄与副图容器须**共用同一条件、同一片段**且手柄在前。若分别用两个条件渲染，存在「条件错位 → 无副图却出现手柄」或顺序倒置风险；亦未点明 `tradeTooltip` 块夹在二者 JSX 之间但为绝对定位、不影响 flex 流。 | 已修复：v2 写明 `{hasPane && activePaneDef && (<> 手柄 + 副图容器 </>)}` 原子片段与精确插入顺序。 |
| R3 | P2 | 一致性/复用 | 新建 `PaneSplitHandle` 与 `PanelResizeHandle` 高度同构（仅朝向/光标/边框不同）。本仓库既有约定即「按朝向分文件」，故新建与现状一致、可接受；但同时存在 `ChartWorkspace` 既有**内联**横向分隔条（L305–317），落地后会出现「内联手柄 + 新组件」双份横向手柄重复。 | 建议落实 §2.2-6 收敛重构以消重；若选最小改动暂缓，须在 PR 说明。不阻塞。 |
| R4 | P2 | 完整性 | 极小窗口（CandleChart 高度 < 主图 120px + 手柄 6px + 副图 120px ≈ 246px）下两个 `min-h-[120px]` 会令内容溢出根容器。 | 与既有「比例+像素」兜底同源、属可接受边界；建议在 §6.3 增补「极小高度仅溢出、无脚本错误」一条人工核验（已补）。不阻塞。 |
| R5 | P2 | 一致性 | 既有内联横向分隔条（L305–317）缺 `onPointerCancel`/`onLostPointerCapture`，系统取消拖拽时 `draggingRef` 可能残留 `true`。 | 新 `PaneSplitHandle` 已复刻完整生命周期（更健壮）；落实 R3 收敛重构可顺带修复此既有瑕疵。不阻塞。 |
| R6 | P2 | 手感 | 手柄 6px 厚度使「指针 Y → 比例」存在亚像素级非线性偏移。 | 与外层纵向分栏同口径，已在 §七风险表登记，可接受。不阻塞。 |

---

## 一、概述（Overview）

当前交易工作台的图表区由 `apps/web/components/Chart/CandleChart.tsx` 统一渲染。该组件内部用 lightweight-charts v4 的「多 `createChart` 垂直堆叠」方式实现主图与副图：上方是主蜡烛图（`containerRef` 容器，类名 `flex-[4]`），下方是单显的技术指标窗格容器（类名 `flex-1`，内含指标页签 + `IndicatorPane`）。两者之间是一条静态边框 `border-t border-[var(--border)]`，高度比固定为 **4 : 1（即主图 0.8 / 副图 0.2）**，用户无法调整。本需求要把这条静态边框替换为**可上下拖拽的分隔手柄**，让用户自由分配主图与副图的高度，并将该偏好持久化。

本项目已经存在两处成熟的「可拖拽分栏」实现，本方案完全复用其既有约定，不引入新范式：① 工作台纵向分栏（图表区 / 下方成交持仓区），在 `ChartWorkspace.tsx` 内以内联 `role="separator"` 手柄 + Pointer 捕获实现，比例存于 store 字段 `chartSplitRatio`；② 盘口列横向调宽，使用可复用组件 `apps/web/components/ui/PanelResizeHandle.tsx` + 纯几何换算 `apps/web/lib/panelResize.ts#ratioFromRightEdge`，比例存于 `depthPanelRatio`。两者的共同模式是：**手柄每帧回吐指针坐标 → 父组件按容器 `getBoundingClientRect()` 换算为比例 → 写入 store；store setter 做唯一夹取（finite 校验 + min/max clamp）；比例通过 Zustand persist 落盘并随版本 migrate 补默认值**。

因此本方案的核心是：新增一个持久化比例字段 `chartIndicatorRatio`（主蜡烛图占 `CandleChart` 内部列高的比例，默认 `0.8` 与现状像素一致），新增一个**横向（行向）可复用拖拽手柄组件** `PaneSplitHandle`，新增一个纯几何换算 `ratioFromTopEdge`，并在 `CandleChart` 中把固定的 `flex-[4]` / `flex-1` 改为由该比例驱动的 `flexGrow`，在两区之间插入手柄。`CandleChart` 维持「纯展示、靠 props 接线」的现有架构，比例与回调由 `ChartWorkspace` 从 store 读出后透传（与现有 `paneTab` / `onPaneTabChange` 透传方式一致）。lightweight-charts 两张子图均开启 `autoSize`，容器高度变化时会自适应重排；主图挂载期的 `ResizeObserver` 已会在尺寸变化时调用 `alignPriceScaleWidths`，故价格轴等宽对齐**无需新增接线**。

---

## 二、技术设计（Technical design）

### 2.1 架构与数据流

```text
用户在分隔手柄上按下并拖动（PointerEvent, clientY）
  -> PaneSplitHandle: setPointerCapture + 每帧 onResize(e.clientY)
  -> CandleChart: 读取根列容器 rect，调用 ratioFromTopEdge(rect.top, rect.height, clientY)
  -> onChartIndicatorRatioChange(ratio)  // 来自 props
  -> ChartWorkspace: setChartIndicatorRatio(ratio)  // store setter
  -> store: Number.isFinite 校验 + clamp[0.4, 0.85]，写入 chartIndicatorRatio（并 persist 落盘）
  -> CandleChart 重渲染：主图 flexGrow=ratio，副图容器 flexGrow=1-ratio
  -> lightweight-charts autoSize（内部 ResizeObserver）自适应新高度
  -> 主图挂载期 ResizeObserver -> alignPriceScaleWidths(getCharts())（价格轴等宽，已有逻辑）
双击手柄 -> onChartIndicatorRatioReset() -> setChartIndicatorRatio(DEFAULT_CHART_INDICATOR_RATIO)
```

### 2.2 关键代码路径

1. **store（唯一真相 + 夹取 + 持久化）** `apps/web/lib/store.ts`
   - 常量：`DEFAULT_CHART_INDICATOR_RATIO = 0.8`、`MIN_CHART_INDICATOR_RATIO = 0.4`、`MAX_CHART_INDICATOR_RATIO = 0.85`（与 `chartSplitRatio` 同区间，主图至少占 40%，副图最多占 60%）。
   - `AppState` 接口新增 `chartIndicatorRatio: number;` 与 `setChartIndicatorRatio: (ratio: number) => void;`。
   - 初始化器（全新安装兜底，不走 migrate）新增 `chartIndicatorRatio: DEFAULT_CHART_INDICATOR_RATIO`（与 `chartSplitRatio` 并列）。
   - setter 实现完全复刻 `setChartSplitRatio`：`Number.isFinite(n)` 为真时 `Math.min(Math.max(n, MIN), MAX)`，否则保持原值。
   - `partialize` 新增 `chartIndicatorRatio: s.chartIndicatorRatio`（持久化偏好）。
   - `version` 由 `21` 提升为 `22`，并在 `migrate` 末尾新增 `if (version < 22) { next = { ...next, chartIndicatorRatio: DEFAULT_CHART_INDICATOR_RATIO }; }`（浅合并本就回落初值，此处为显式稳妥，沿用既有注释口径）。

2. **纯几何换算** `apps/web/lib/panelResize.ts`
   - 新增 `ratioFromTopEdge(containerTop: number, containerHeight: number, clientY: number): number`，返回 `containerHeight > 0 ? (clientY - containerTop) / containerHeight : 0`（除零守卫，避免 NaN/Infinity，与 `ratioFromRightEdge` 同范式；夹取仍由 store setter 负责）。

3. **可复用行向拖拽手柄（新组件）** `apps/web/components/ui/PaneSplitHandle.tsx`
   - 与 `PanelResizeHandle` 同构，区别为方向：`role="separator"`、`aria-orientation="horizontal"`、`cursor-row-resize`、`onResize(clientY)`（回吐 `e.clientY` 而非 `clientX`）。
   - 复刻 Pointer 捕获生命周期：`onPointerDown`（`preventDefault` + `setPointerCapture` + `draggingRef=true`）、`onPointerMove`（拖拽中 `onResize(e.clientY)`）、`onPointerUp`（`draggingRef=false` + `releasePointerCapture`）、`onPointerCancel` / `onLostPointerCapture`（复位 `draggingRef`），`onDoubleClick` → `onReset`，`style={{ touchAction: "none" }}`。
   - 视觉与现有纵向分栏手柄一致：`h-1.5 w-full`、顶部细边框 `border-t`、`bg-[var(--surface-2)] hover:bg-[var(--accent-soft)]`，内部居中拖柄条 `h-0.5 w-8 rounded-full`。

4. **CandleChart（接线 + 布局改造）** `apps/web/components/Chart/CandleChart.tsx`
   - `Props` 新增三个可选属性：`chartIndicatorRatio?: number`、`onChartIndicatorRatioChange?: (ratio: number) => void`、`onChartIndicatorRatioReset?: () => void`。
   - 新增 `splitContainerRef = useRef<HTMLDivElement>(null)` 绑定到根 `flex-col` 容器（现有 `return` 的最外层 `<div className="relative flex h-full w-full flex-col">`），作为比例测量容器。
   - 拖拽回调 `onSplitResize(clientY)`：读 `splitContainerRef.current?.getBoundingClientRect()`，`height <= 0` 时直接 return；否则 `onChartIndicatorRatioChange?.(ratioFromTopEdge(rect.top, rect.height, clientY))`。
   - 主图区：把 `className="relative min-h-0 w-full flex-[4]"` 改为 `className="relative min-h-[120px] w-full"` + `style={{ flexGrow: ratio, flexShrink: 1, flexBasis: 0 }}`。**注意（评审 R1）：必须用 `min-h-[120px]` 替换 `min-h-0`，不可二者并存**——它们同为 `min-height` 工具类，同时出现时谁生效取决于 Tailwind 生成 CSS 的顺序而非 className 字符串顺序，会让像素下限退化为不确定（可能落 0），副图拉大时主图塌陷。`min-h-[120px]` 即作为主图像素下限防塌陷。`ratio` 取 `chartIndicatorRatio ?? DEFAULT_CHART_INDICATOR_RATIO`（组件内用本地常量回退，避免 props 缺省时为 `undefined`）。
   - 在主图区与副图容器之间插入手柄（评审 R2）：手柄与副图容器**共用同一条件、包在同一片段内**，手柄在前、副图容器在后，使二者随副图开关原子化地一起出现/消失：
     ```tsx
     {hasPane && activePaneDef && (
       <>
         <PaneSplitHandle
           onResize={onSplitResize}
           onReset={onChartIndicatorRatioReset}
           ariaLabel="拖动调整主图与副图高度，双击复位"
         />
         {/* 原副图容器（见下条改造）*/}
         <div className="flex min-h-[120px] flex-col" style={{ flexGrow: 1 - ratio, flexShrink: 1, flexBasis: 0 }}>…</div>
       </>
     )}
     ```
     JSX 内的流内顺序为：主图区 → `tradeTooltip`（绝对定位、不参与 flex 流，详见评审「已核验属实」）→ 上述 `hasPane` 片段（手柄 + 副图容器）。
   - 副图容器：把 `className="flex min-h-[120px] flex-1 flex-col border-t border-[var(--border)]"` 改为 `className="flex min-h-[120px] flex-col"`（移除 `flex-1` 与 `border-t`，边框改由手柄顶边提供）+ `style={{ flexGrow: 1 - ratio, flexShrink: 1, flexBasis: 0 }}`。
   - 行为说明：当 `hasPane` 为假（未开启任何副图指标）时，不渲染手柄与副图容器；主图区作为唯一 `flexGrow` 子项（`flexBasis:0`）自然占满全高，无需条件样式。

5. **ChartWorkspace（从 store 读出并透传）** `apps/web/components/Chart/ChartWorkspace.tsx`
   - 从 `useAppStore()` 解构 `chartIndicatorRatio`、`setChartIndicatorRatio`，从 store 导入 `DEFAULT_CHART_INDICATOR_RATIO`。
   - 渲染 `<CandleChart .../>` 时新增三个 props：`chartIndicatorRatio={chartIndicatorRatio}`、`onChartIndicatorRatioChange={setChartIndicatorRatio}`、`onChartIndicatorRatioReset={() => setChartIndicatorRatio(DEFAULT_CHART_INDICATOR_RATIO)}`。

6. **一致性重构（推荐，与本需求同质，降低重复）**：把 `ChartWorkspace.tsx` 现有「图表 / 下方区」纵向分栏的内联手柄（`onSplitPointerDown/Move/Up` + 内联 `<div role="separator">`）替换为同一个 `PaneSplitHandle`，其 `onResize(clientY)` 用新 `ratioFromTopEdge(rect.top, rect.height, clientY)` 换算后调用 `setChartSplitRatio`。该重构纯收敛重复，不改变行为；若实施节点希望最小化改动面，可暂缓（详见 §七 风险）。

### 2.3 操作时序（拖拽一次）

1. `pointerdown` → 手柄捕获指针，`draggingRef=true`，`preventDefault` 阻止文本选区。
2. 多次 `pointermove` → 每帧 `onResize(clientY)` → `CandleChart` 换算比例 → store clamp 写入 → React 重渲染只改两处 `flexGrow` 内联样式（开销极小）→ lightweight-charts `autoSize` 的内部 `ResizeObserver` 在动画帧重排画布 → 主图 `ResizeObserver` 触发 `alignPriceScaleWidths`（按 rAF 合并）。
3. `pointerup`（或 `pointercancel` / `lostpointercapture`）→ `draggingRef=false` + 释放捕获。
4. 比例已落盘；刷新后 `rehydrate` 恢复，`migrate` 对老版本补默认值。

---

## 三、文件 / 模块变更计划（File / module change plan）

| 文件 | 动作 | 一句话意图 |
|------|------|-----------|
| `apps/web/lib/store.ts` | 修改 | 新增 `chartIndicatorRatio` 字段 / `setChartIndicatorRatio` setter / 默认与 min·max 常量 / partialize / 初始化器 / version 21→22 + migrate 分支。 |
| `apps/web/lib/panelResize.ts` | 修改 | 新增纯函数 `ratioFromTopEdge(containerTop, containerHeight, clientY)`（含除零守卫，不夹取）。 |
| `apps/web/components/ui/PaneSplitHandle.tsx` | 新增 | 可复用「行向」拖拽手柄：Pointer 捕获 + 每帧回吐 `clientY` + 双击复位，`aria-orientation="horizontal"`、`cursor-row-resize`。 |
| `apps/web/components/Chart/CandleChart.tsx` | 修改 | 接收比例/回调 props，根列加测量 ref，主图区将 `min-h-0` 替换为 `min-h-[120px]`（评审 R1，不可并存）并改 `flexGrow=ratio`，副图改 `flexGrow=1-ratio`；手柄与副图容器同包在 `hasPane && activePaneDef` 单一片段内、手柄在前（评审 R2）。 |
| `apps/web/components/Chart/ChartWorkspace.tsx` | 修改 | 从 store 读 `chartIndicatorRatio`/setter 并透传给 `CandleChart`；（推荐）外层纵向分栏改用同一 `PaneSplitHandle` + `ratioFromTopEdge`。 |
| `apps/web/lib/panelResize.test.ts` | 修改 | 为 `ratioFromTopEdge` 增加正常换算与除零守卫用例。 |
| `apps/web/lib/store.test.ts` | 修改 | 为 `setChartIndicatorRatio` 增加 clamp / 非有限忽略 / persist / v21→v22 migrate 用例。 |
| `apps/web/components/ui/PaneSplitHandle.test.tsx` | 新增（可选） | 验证 `onResize` 回吐 `clientY`、`onReset` 双击触发、Pointer 捕获生命周期（NODE_ENV=test）。 |

> 不改动任何后端（`services/quant`）、桌面壳（`desktop`）或图表同步算法（`chartSync.ts`）源码。

---

## 四、接口设计（Interface design）

### 4.1 Store（Zustand）

```ts
// store.ts 导出常量
export const DEFAULT_CHART_INDICATOR_RATIO = 0.8;       // 主蜡烛图占内部列高，4:1 现状等价
const MIN_CHART_INDICATOR_RATIO = 0.4;                  // 主图下限 40%
const MAX_CHART_INDICATOR_RATIO = 0.85;                 // 主图上限 85%（副图下限 15%）

// AppState 新增
chartIndicatorRatio: number;
setChartIndicatorRatio: (ratio: number) => void;
```

`setChartIndicatorRatio` 语义：入参为「主图占内部列高比例」；非有限数（NaN/Infinity）忽略并保持原值；有限数夹取到 `[0.4, 0.85]`。

### 4.2 组件 Props

```ts
// PaneSplitHandle.tsx
interface PaneSplitHandleProps {
  onResize: (clientY: number) => void; // 拖拽中每帧回吐指针视口 Y
  onReset?: () => void;                // 双击复位（可选）
  ariaLabel?: string;                  // 无障碍标签与 title
  className?: string;                  // 透传附加类名（可选）
}

// CandleChart.tsx Props 追加
chartIndicatorRatio?: number;                       // 主图占比，缺省回退 DEFAULT_CHART_INDICATOR_RATIO
onChartIndicatorRatioChange?: (ratio: number) => void;
onChartIndicatorRatioReset?: () => void;
```

无 REST / WebSocket / CLI 接口变更。

### 4.3 纯函数

```ts
// panelResize.ts
export function ratioFromTopEdge(
  containerTop: number,
  containerHeight: number,
  clientY: number,
): number; // containerHeight>0 ? (clientY-containerTop)/containerHeight : 0
```

---

## 五、数据模型（Data model）

仅前端内存态 + localStorage 持久化，无数据库变更。

- **内存形态**：`AppState.chartIndicatorRatio: number ∈ [0.4, 0.85]`，默认 `0.8`。
- **持久化键**：复用 Zustand persist 键 `aragon-ui`，`partialize` 仅追加 `chartIndicatorRatio`；其余持久化字段不变。
- **版本迁移**：`version: 22`；`migrate` 对 `version < 22` 的持久化态补 `chartIndicatorRatio = DEFAULT_CHART_INDICATOR_RATIO`。其余旧字段沿既有逐版分支恢复。
- **SSR / 水合**：沿用 `skipHydration: true`，根组件挂载后 `rehydrate()` 补水（接受首帧默认→恢复闪烁，与既有面板尺寸字段一致）。

---

## 六、测试与验收（Testing & acceptance criteria）

### 6.1 单元测试（vitest，组件测试需 `NODE_ENV=test`）

- `panelResize.test.ts`：
  - `ratioFromTopEdge_maps_pointer_offset_to_ratio`：`ratioFromTopEdge(100, 400, 300)` ≈ `0.5`；`ratioFromTopEdge(0, 500, 400)` ≈ `0.8`。
  - `ratioFromTopEdge_guards_zero_height`：`containerHeight` 为 `0` 或负时返回 `0`，不产生 NaN/Infinity。
- `store.test.ts`（仿照 v12 面板尺寸用例）：
  - `setChartIndicatorRatio clamps to [0.40, 0.85]`：入 `0`→`0.4`，入 `1`→`0.85`，入 `0.6`→`0.6`。
  - `setChartIndicatorRatio ignores non-finite input`：`NaN`/`Infinity` 不改变现值。
  - `persists chartIndicatorRatio`：写入后 `localStorage["aragon-ui"].state.chartIndicatorRatio` 等于所写值。
  - `migrates v21 persisted state to v22 with default ratio`：注入 `version:21` 旧态，`rehydrate()` 后 `chartIndicatorRatio === DEFAULT_CHART_INDICATOR_RATIO`，且既有字段（如 `instId`、`chartSplitRatio`）正常恢复。
- `PaneSplitHandle.test.tsx`（可选）：派发 `pointerdown`+`pointermove` 断言 `onResize` 收到 `clientY`；`dblclick` 断言 `onReset` 触发。

### 6.2 回归保护

- 既有 `chartSync` 等宽对齐 / 十字光标贯穿测试须全绿（本方案不改 `chartSync.ts`，仅高度变化触发既有 RO 路径）。
- 既有 `store.test.ts` 持久化 / migrate 用例须全绿（仅追加字段与一版 migrate 分支）。

### 6.3 人工验收（真实窗口）

1. 开启任一副图指标（MACD/RSI/KDJ/VOL），主图下方出现分隔手柄（hover 变色、`cursor: row-resize`）。
2. 向下拖动 → 主 K 线变高、副图变矮；向上拖动 → 反之；松手后比例保持。
3. 拖到极端位置时主图 ≥40%、副图 ≥15% 且不小于 120px（不塌陷、无溢出）。
4. 双击手柄复位回默认 0.8 / 0.2。
5. 切换副图页签（单显切换）比例保持不变；关闭全部副图手柄消失、主图占满；重新开启恢复上次比例。
6. 刷新页面后比例从 localStorage 恢复；价格轴左右边缘与时间轴在缩放后仍像素级对齐。
7. （评审 R4）把窗口压到极小高度（图表区高度 < 约 246px，即主图 120 + 手柄 6 + 副图 120）时，仅允许内容溢出，不得出现脚本错误、空白崩溃或拖拽失效。

### 6.4 Definition of Done

- 上述单测通过，`pnpm --filter web test` 全绿；
- 主图/副图分隔线可上下拖拽并持久化，满足 6.3 全部人工项；
- 不触碰后端 / 桌面 / `chartSync.ts`；单文件与单函数仍在 CLAUDE.md 阈值内。

---

## 七、风险与对策（Risks & mitigations）

| 风险 | 影响 | 对策 |
|------|------|------|
| 拖动时 lightweight-charts 每帧重排造成卡顿 | 高频 `pointermove` 触发画布 resize | `autoSize` 内部用 rAF 合并；store 写入只改两处 `flexGrow` 内联样式，重渲染廉价；与既有 `chartSplitRatio` 外层拖拽同源，已被验证可接受。 |
| 比例换算偏移（手柄/页签占位使指针 Y 与比例非严格线性） | 拖拽手感轻微偏移 | 与外层纵向分栏同口径：比例以根列整高为分母、store 夹取兜底；直接操纵下偏移在可接受范围。 |
| 极端比例导致面板塌陷或容器溢出 | 主图或副图过小不可用 | 双保险：store clamp `[0.4,0.85]` + 两区 `min-h-[120px]` 像素下限（min-height 优先于 flex-grow），沿用 `depthPanelRatio` 的「比例+像素」兜底范式。 |
| 价格轴等宽对齐在高度变化后错位 | 主副图绘图区左右缘不齐 | 高度变化不改价格轴标签宽度，主图既有 `ResizeObserver → alignPriceScaleWidths` 会在尺寸变化时重对齐并收敛到不动点，无需新增接线；纳入 §6.3 第 6 项人工核验。 |
| 新增持久化字段破坏旧用户水合 | 刷新后状态异常 | `version` 升 22 + migrate 补默认；partialize 仅追加字段；浅合并本就回落初值，迁移为显式稳妥。 |
| 一致性重构（§2.2-6）触动既有外层分栏 | 回归既有纵向拖拽 | 该项标记为「推荐但可分离」：核心交付为副图分隔线；若实施节点选择最小改动，可只新增内层手柄、保留外层内联实现，互不影响。 |
| 触摸设备拖拽与页面滚动冲突 | 移动端误触滚动 | 手柄 `touch-action: none` + Pointer 捕获，复刻既有手柄约定。 |

### 非目标（Non-goals）

- 不实现键盘可达的分隔条调节（现有手柄亦未实现，保持一致）。
- 不改副图「单显切换」逻辑、不支持同时显示多个副图窗格。
- 不调整外层「图表区 / 下方区」与盘口列既有拖拽的默认值与区间。

---

## 评审结论（Review Verdict）

**结论：有条件通过（Approved with conditions）。**

本方案在可行性、完整性、一致性、规模适配四个维度整体扎实：完全复用项目已验证的「store 单一夹取 + 持久化/迁移 + Pointer 捕获手柄 + 纯几何换算」范式，无新范式、无后端/桌面/`chartSync` 改动，默认值 `0.8` 与现状 `flex-[4]/flex-1`（4:1）像素等价，影响面收敛在 5 个前端文件 + 3 个测试文件。评审实地核验了 store 版本链路、几何守卫、手柄范式、CandleChart 结构、影响面与测试文件，均与方案描述一致。

发现的 **2 个 P1（R1 className 冲突、R2 手柄渲染条件/插入顺序）已在 v2 正文就地修复**；无 P0 遗留。准予进入「代码开发」节点，但须满足以下条件：

1. **（源自 R1，强制）** 主图区像素下限只能用 `min-h-[120px]` **替换** `min-h-0`，严禁二者并存；落地后须在浏览器实测：把副图拖到最大（主图至 40%）时主图不塌陷、`min-height` 实际生效。
2. **（源自 R2，强制）** 手柄与副图容器必须包在同一个 `{hasPane && activePaneDef && (<>…</>)}` 片段内、手柄在前；关闭全部副图时手柄须随副图一起消失（对应 §6.3 第 5 项）。
3. **（源自 R3/R5，建议）** 优先落实 §2.2-6 收敛重构（外层内联横向分隔条改用 `PaneSplitHandle`），消除双份横向手柄并顺带补齐既有内联条缺失的 `onPointerCancel`/`onLostPointerCapture`；若为控制改动面而暂缓，须在 PR 描述中显式说明并标注为后续技术债。
4. **（源自 R4，建议）** 执行 §6.3 第 7 项极小高度人工核验，确认仅溢出、无崩溃。

P2 项（R3–R6）均为非阻塞建议，不构成放行障碍。满足上述强制条件 1、2 后，本方案即达到 Definition of Done，无 P0/P1 未决项。
