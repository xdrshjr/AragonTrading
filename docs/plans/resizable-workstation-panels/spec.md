# 工作台侧栏可拖拽与盘口面板右移方案（resizable-workstation-panels）

> 版本：**v2**（Subtask #1 方案评审与修复；基于 Subtask #0 的 v1，增补「评审记录」「评审结论」并就地修复 P0/P1）
> 作者：Solution Architect（Anthropic 工程团队角色）
> 适用任务：多智能体「标准四阶段开发」第 1 轮
> 关联代码索引：`.claude-index/index.md`
> 关联诊断：`docs/diagnoses/workstation-layout-resize-and-overflow/analysis.md`（上一轮已落地"常驻两列 + `minmax(0,1fr)` + 桌面 `minWidth:860`"，本轮在其基础上增量）
> 状态：**已评审 · 有条件通过（v2）**

---

## 评审记录（Review Notes · Subtask #1）

> 评审人：Senior Reviewer（Anthropic 工程团队角色）。评审维度：**可行性 / 完整性 / 一致性 / 适当规模**。
>
> **已核对源码（实证，非推断）**：`apps/web/app/page.tsx`（L483 主网格 `grid-cols-[minmax(0,1fr)_380px]`、L398-400 `useAppStore.persist.rehydrate()`、L521 `<aside>`）、`apps/web/components/Chart/ChartWorkspace.tsx`（L184-256 上区/左栏结构、L189-205 `flex-row` + 左栏 `basis-[16%] min-w-[220px]` + `MarketDepthPanel`/`InstrumentSidebar`、L117-134 既有横向手柄 Pointer 范式）、`apps/web/lib/store.ts`（`version: 11`、`partialize`、`migrate(version<11)`、`setChartSplitRatio`/`setSimBalance` 夹取范式、`skipHydration:true`、key `aragon-ui`）、`apps/web/components/Chart/MarketDepthPanel.tsx`（props 仅 `market`+`activeCandle`）、`desktop/src/main.js`（L207-212 `minWidth:860`/`minHeight:680`）、`apps/web/components/ui/index.tsx`（barrel 命名导出）与 `apps/web/package.json`（`clsx ^2.1.1` 已在依赖）。
>
> **总评**：方案与现状高度吻合——persist 版本号、字段、夹取 setter、CSS 变量、桌面 `minWidth`、组件 props 均经源码核对无误；零后端、零新依赖、复用既有 Pointer/store 范式，规模适当、可落地。下列问题已按严重度分级；**所有 P0/P1 已就地修复并标注于正文相应小节**，P2 留作落地建议。

| # | 维度 | 严重度 | 问题摘要 | 处置 |
|---|------|--------|----------|------|
| R1 | 完整性 / 正确性 | **P1** | §6.2「不溢出证明」仅以右栏**最小值 320** 核算，却宣称「任何持久化组合均不溢出 / 不触发裁切」。当右栏被拉到接近 MAX **640** 且窗口接近 `minWidth 860` 时，中列内容地板（标的 220 + 手柄 6 + 盘口 160 = **386**）超过可用轨道（860−6−640 = 214），盘口/标的列会被 `<main> overflow-hidden` **裁切**——与原证明自相矛盾。§8 风险行、§9.3 验收断言 `scrollWidth===innerWidth` 亦沿用了此过度承诺。 | **已修复**：§6.1 / §6.2 / §8 / §9.3 重写为正确的组合地板公式 `386 + 6 + rightPanelWidth`，明确「右栏 ≤ 默认 380（含最小 320）→ 地板 ≤772 < 860」为硬保证包络；近最小窗口放大右栏触发的裁切定性为**可接受的优雅降级**（无横向滚动条、双击复位或拉宽窗口即恢复），并把 §9.3 的 `scrollWidth===innerWidth` 硬断言限定在该包络内、对极端组合改为「优雅裁切、可复位」验收。 |
| R2 | 完整性 | P2 | 最小窗口（860）下左栏 220 + 盘口 160 + 手柄 6 固定占用 386（较改造前仅左栏 220 翻倍），图表被压到 ≈88px，偏窄。 | 接受为权衡：默认窗口 1440（见 `main.js`）下充裕，min 仅为可收窄下限。已在 §6.2 / §9.3 加注并要求 860 档验收确认「无裁切 + 图表仍渲染」。后续如需可单独把盘口像素地板下调（不在本轮范围）。 |
| R3 | 一致性 / 清晰度 | P2 | §6.1 以「新增 state 字段 // 默认 380」描述默认值，未显式点明默认值须写进 `create((set)=>({…}))` **初始化器**（`store.ts` 已注明「全新安装只走初始化器、不触发 migrate」）。 | **已就地补强**：§6.1 增加初始化器片段与显式说明（TS 类型亦强制该字段在初始化器出现，二者互证）。 |
| R4 | 一致性 | P2 | 既有 `components/ui` 为单文件 `index.tsx` 命名导出；新手柄置于独立文件 `PanelResizeHandle.tsx` 默认导出，略偏离 barrel 惯例。 | 接受（独立有状态组件单独成文更清晰，避免 `index.tsx` 膨胀）；已在 §3.3 注明可选地在 barrel 追加再导出以统一入口。 |
| R5 | 适当规模 / 一致性 | P2 | 既有横向分栏手柄仍为 ChartWorkspace 内联 JSX，本轮只为竖向手柄抽组件，形成两套写法。 | 接受（朝向 / 光标 / 边框不同，统一改造超出本轮增量范围）；已在 §3.3 说明分工与不重构理由。 |
| R6 | 健壮性 | P2 | 手柄未处理 `onPointerCancel` / `onLostPointerCapture`，系统取消拖拽时 `draggingRef` 残留 true（与既有横向手柄同样缺口）。 | 接受为既有同构缺口；已在 §5.1 注明可选增强（取消 / 卸载时复位 `draggingRef`）。 |
| R7 | 视觉 | P2 | section 的 `border-r` 与手柄的 `border-l` 相邻叠出 2px 分隔线。 | 接受为像素级观感；已在 §9.1 注明落地时可去其一以保单线。 |

> **修复后状态**：无 P0；唯一 P1（R1）已就地修正且自洽；P2 为落地期建议，不阻断开发。详见文末 **§11 评审结论**。

---

## 1. 概述（Overview）

本轮针对工作台主界面的两条布局诉求做一次**纯前端、零后端**的增量调整，目标是把当前"固定宽度"的两个区域改为"用户可手工拖拽调宽"，并把盘口/成交量信息从 K 线图**左侧**迁移到**右侧**。两条需求均来自用户对专业交易终端布局习惯的对齐：右侧 AI 助手 / 交易策略面板希望能按需放宽以阅读较长的对话与策略参数；盘口与实时成交量希望紧贴 K 线**右侧**（与多数交易所行情页一致），并能按需缩放。

**需求一（右侧面板左边框可拖拽调宽）**：当前最右侧 `<aside>`（承载 `RightPanel` —— "AI 助手"与"交易策略"两页签）在主网格中被固定为 `380px`（`page.tsx` 的 `grid-cols-[minmax(0,1fr)_380px]`）。本轮在该 `<aside>` 的**左边框**处插入一个竖向拖拽手柄，用户按住向左/向右拖动即可放宽/收窄右侧面板；中间 K 线区（`minmax(0,1fr)` 轨道）连续让位吸收宽度变化，整体不溢出窗口。宽度夹取到合理区间并持久化，刷新后保持；双击手柄复位默认 `380px`。

**需求二（盘口/成交量面板移到 K 线右侧并可调宽）**：当前左侧栏（`ChartWorkspace` 上区的 `basis-[16%]` 列）自上而下叠放了 `MarketDepthPanel`（盘口五档 + 24h/Bar 成交量 + 实时成交带）与 `InstrumentSidebar`（标的列表）。本轮把 `MarketDepthPanel` 从左栏**剥离**，作为 K 线图**右侧**的一个独立列（位于上区 `flex-row` 内、图表区之右），默认宽度约为上区宽度的 **15%**，并在其左边框插入竖向拖拽手柄供用户手工调宽；剥离后左栏只保留 `InstrumentSidebar`，标的列表获得整列高度。

从架构看，本轮改动集中在 `apps/web` 前端布局层：新增一个可复用的竖向拖拽手柄组件、一个纯函数换算模块、在 Zustand store 中新增两个持久化的尺寸字段，并在 `page.tsx`（主网格）与 `ChartWorkspace.tsx`（上区结构）两处接线。**不改动任何数据流、后端接口、数据库、K 线/指标渲染与右侧 `RightPanel` 内部**，遵守 CLAUDE.md「最小破坏面 + 依赖最小化（不引入 react-resizable-panels 等运行时库）」与项目既有「Zustand-persist 承载选择态、纯函数 + 同名 `.test.ts`」的约定。复用上一轮纵向分栏（`chartSplitRatio`）已验证的 Pointer Events + `setPointerCapture` 拖拽范式，把竖向（调宽）与既有横向（调高）拖拽统一为同构交互。

---

## 2. 需求解读与布局总览

### 2.1 现状布局（已核验源码）

```
<main grid grid-cols-[minmax(0,1fr)_380px]>           ← page.tsx:483
 ┌─────────────────────────────────────────┬───────────────┐
 │ ChartWorkspace（左 <section>，flex-col）   │ <aside> 380px  │
 │ ┌── 上区 flex-row（flexGrow=chartSplitRatio）─┐│ RightPanel    │
 │ │ ┌左栏 basis-16%┐ ┌── 图表区 flex-1 ──────┐ ││  ├ AI 助手    │
 │ │ │MarketDepth   │ │ Toolbar/HeaderBar/Chart│ ││  └ 交易策略  │
 │ │ │InstrumentBar │ │                        │ ││  （固定宽）   │
 │ │ └──────────────┘ └────────────────────────┘ ││               │
 │ ├──── 横向拖拽手柄（row-resize，已存在）────────┤│               │
 │ │ 下区（成交记录/当前持仓 或 实盘 LiveBottom） │ ││               │
 │ └───────────────────────────────────────────┘│               │
 └─────────────────────────────────────────┴───────────────┘
```

### 2.2 目标布局（本轮）

```
<main grid> gridTemplateColumns = "minmax(0,1fr) 6px {rightPanelWidth}px"   ← 需求一
 ┌──────────────────────────────────────┬─┬───────────────┐
 │ ChartWorkspace                        │↔│ <aside>        │ ← 新增竖向手柄(6px)
 │ ┌── 上区 flex-row ───────────────────┐ │ │ RightPanel    │   在 aside 左边框
 │ │ ┌左栏┐ ┌─ 图表区 flex-1 ─┐ │↔│ ┌盘口列┐ │ │ │ 宽度=rightPanelWidth│
 │ │ │标的 │ │ Toolbar/Chart   │ │ │ │Market│ │ │ │（可拖、持久化）│
 │ │ │列表 │ │                 │ │ │ │Depth │ │ │ └───────────────┘
 │ │ └────┘ └─────────────────┘ └─┘ └──────┘ │   ↑ 需求二：盘口移到图表右侧
 │ │                          新增竖向手柄(6px) │     宽≈15%(depthPanelRatio)、可拖
 │ ├──── 横向拖拽手柄（保持不变）──────────────┤
 │ │ 下区（保持不变）                          │
 │ └──────────────────────────────────────────┘
 └──────────────────────────────────────┴─┴───────────────┘
```

要点：
- 盘口列**只存在于上区 `flex-row` 内**（即"K 线图的右边"），不延伸到下区；下区（成交/持仓 或实盘下单面板）保持满宽、保持现状。
- 左栏剥离盘口后仅剩标的列表，沿用既有 `basis-[16%] min-w-[220px] max-w-[300px]` 固定宽。
- 两个新拖拽手柄与既有横向手柄三者视觉同构（细条 + hover 高亮 + 双击复位）。

### 2.3 交互定义

| 交互 | 触发 | 行为 | 夹取 | 复位 |
|------|------|------|------|------|
| 调右侧面板宽 | 拖 `<aside>` 左边框手柄 | `rightPanelWidth = 主网格右边缘 − 指针X` | `[320, 640]`px | 双击→`380` |
| 调盘口列宽 | 拖盘口列左边框手柄 | `depthPanelRatio = (上区右边缘 − 指针X) / 上区宽` | `[0.10, 0.30]` 且 px `[160, 420]` | 双击→`0.15` |

两者均用 Pointer Events + `setPointerCapture`：`pointerdown` 捕获并置 `dragging=true`；`pointermove` 时按上表换算并写 store（store 内夹取）；`pointerup` 释放捕获并置 `dragging=false`。手柄 `touch-action:none`、`cursor:col-resize`。

---

## 3. 技术设计（Technical Design）

### 3.1 架构与数据流

```
useAppStore（新增 rightPanelWidth / depthPanelRatio + 两 setter，持久化）
        │
page.tsx（主网格）
  ├─ mainRef → 提供右边缘 rect.right 作为换算基准
  ├─ 主网格列模板改为 inline style：minmax(0,1fr) 6px {rightPanelWidth}px
  ├─ <ChartWorkspace/>（列 1）
  ├─ <PanelResizeHandle onResize={x→setRightPanelWidth(widthFromRightEdge(rect.right,x))} …/>（列 2）
  └─ <aside>（列 3，min-w-0）<RightPanel/>（内部不变）
        │
ChartWorkspace.tsx（上区 flex-row）
  ├─ topRef → 提供上区 rect.right / rect.width 作为换算基准
  ├─ 左栏（仅 InstrumentSidebar，整列高度）
  ├─ 图表区 flex-1（不变）
  ├─ <PanelResizeHandle onResize={x→setDepthPanelRatio(ratioFromRightEdge(rect.right,rect.width,x))} …/>
  └─ 盘口列（flexBasis: depthPanelRatio*100% + min/max-w，overflow-auto）<MarketDepthPanel/>（不变）
```

数据来源全部为既有真实链路：`market`（来自 `marketWS` 实时盘口/成交/ticker）、`displayCandle`（ChartWorkspace 已有派生）原样下传给 `MarketDepthPanel`，迁移仅改变其**挂载位置**，不改其 props 与渲染。

### 3.2 关键代码路径与执行时序

**(A) 右侧面板调宽时序（需求一）**
1. `page.tsx` 用 `mainRef` 指向 `<main>`；store 读出 `rightPanelWidth`（默认 380）。
2. 主网格列宽用 inline style：`gridTemplateColumns: "minmax(0,1fr) 6px " + rightPanelWidth + "px"`（不能用 Tailwind 任意值，因运行期动态）。中间 `6px` 为手柄列，第三列即右侧面板宽。
3. 用户在手柄上 `pointerdown` → 捕获指针；`pointermove(clientX)` → `onResizeRight(clientX)`：取 `mainRef` 的 `getBoundingClientRect()`，`setRightPanelWidth(widthFromRightEdge(rect.right, clientX))`。
4. store setter 把原始值夹取到 `[320, 640]` 后写入并持久化；React 重渲染、grid 第三列即时变宽，K 线列（`minmax(0,1fr)` 最小 0）连续让位。
5. 双击手柄 → `setRightPanelWidth(DEFAULT_RIGHT_PANEL_WIDTH)` 复位 380。

**(B) 盘口列迁移 + 调宽时序（需求二）**
1. 从左栏移除 `MarketDepthPanel` 及其包裹 `<div className="shrink-0 border-b …">`，左栏只剩 `InstrumentSidebar`（`min-h-0 flex-1`）。
2. 在上区 `flex-row` 末尾（图表区之后）追加：竖向手柄 + 盘口列。盘口列 `style={{ flexGrow:0, flexShrink:0, flexBasis: depthPanelRatio*100 + "%" }}`，并加 `min-w-[160px] max-w-[420px] overflow-auto border-l`。
3. `topRef` 指向上区 `flex-row` 容器；手柄 `pointermove(clientX)` → `onResizeDepth(clientX)`：取 `topRef` rect，`setDepthPanelRatio(ratioFromRightEdge(rect.right, rect.width, clientX))`。
4. store setter 夹取到 `[0.10, 0.30]` 后写入并持久化；`flexBasis` 百分比即时变化，图表区 `flex-1 min-w-0` 让位。`min-w/max-w` 作为像素安全边界（极宽/极窄视口下兜底）。
5. 双击手柄 → `setDepthPanelRatio(DEFAULT_DEPTH_PANEL_RATIO)` 复位 0.15。

**(C) 持久化与水合**：两字段进 `partialize`，persist `version` 11→12，`migrate` 对 `version<12` 补默认值；沿用 `skipHydration:true` + 根组件 `useAppStore.persist.rehydrate()`（与既有 `chartSplitRatio` 同模式，零回归）。

### 3.3 复用与新增边界

- **复用**：Pointer 捕获范式（仿 `ChartWorkspace` 既有 `onSplitPointerDown/Move/Up`）、store 夹取 setter 范式（仿 `setChartSplitRatio`/`setSimBalance`）、CSS 变量调色板（`--border`/`--surface-2`/`--accent`/`--accent-soft`）、`MarketDepthPanel`（原样迁移）。
- **新增**：`components/ui/PanelResizeHandle.tsx`（可复用竖向手柄）、`lib/panelResize.ts`（换算纯函数）+ `lib/panelResize.test.ts`、store 两字段与 setter。
- **修改**：`page.tsx`（主网格三列 + 右侧手柄接线）、`ChartWorkspace.tsx`（上区盘口列迁移 + 手柄接线）、`store.ts`（字段/setter/partialize/migrate）、`store.test.ts`（夹取与迁移用例）。

> **评审补充（R4 / R5）**：(1) 新手柄落在独立文件 `components/ui/PanelResizeHandle.tsx`（默认导出，直接路径引入），与既有 `components/ui/index.tsx` 单文件命名导出 barrel 略有出入——本轮**有意**单独成文（有状态组件、避免 barrel 文件膨胀），如需统一入口可可选地在 `index.tsx` 追加 `export { default as PanelResizeHandle } from "./PanelResizeHandle";` 再导出。(2) 既有横向分栏手柄（`ChartWorkspace.tsx` L258-270 内联 JSX）本轮**不**一并抽取进 `PanelResizeHandle`：二者朝向（行/列）、光标（`row-resize`/`col-resize`）、边框（`border-t`/`border-l`）与换算轴（clientY/clientX）不同，统一参数化改造属独立重构、超出本轮增量范围，故保留两套写法、仅新增竖向手柄。

---

## 4. 文件 / 模块变更计划

| # | 文件 | 动作 | 一句话意图 | 优先级 |
|---|------|------|-----------|--------|
| 1 | `apps/web/lib/store.ts` | 改 | 新增 `rightPanelWidth:number`(默认 380) 与 `depthPanelRatio:number`(默认 0.15) 状态、夹取 setter、`partialize` 持久化、`version 11→12` 与 `migrate` 补默认；导出 `DEFAULT_RIGHT_PANEL_WIDTH`/`DEFAULT_DEPTH_PANEL_RATIO` | P0 |
| 2 | `apps/web/lib/panelResize.ts` | 新增 | 纯函数 `widthFromRightEdge(containerRight, clientX)` 与 `ratioFromRightEdge(containerRight, containerWidth, clientX)`（右锚换算，无夹取，夹取交由 store） | P0 |
| 3 | `apps/web/lib/panelResize.test.ts` | 新增 | Vitest 覆盖右锚正负号、`width<=0` 守卫、典型 15% 取值 | P0 |
| 4 | `apps/web/components/ui/PanelResizeHandle.tsx` | 新增 | 可复用竖向拖拽手柄：Pointer 捕获 + `onResize(clientX)` 回吐 + `onReset` 双击复位 + `cursor-col-resize`/`touch-action:none` | P0 |
| 5 | `apps/web/app/page.tsx` | 改 | 主网格改三列 inline 模板 `minmax(0,1fr) 6px {rightPanelWidth}px`；在图表区与 `<aside>` 间插入手柄并接线右侧调宽；`<aside>` 补 `min-w-0` | P0 |
| 6 | `apps/web/components/Chart/ChartWorkspace.tsx` | 改 | 左栏移除 `MarketDepthPanel`；上区末尾新增竖向手柄 + 盘口列（`flexBasis=depthPanelRatio`、`min/max-w`、`overflow-auto`、`border-l`）并接线调宽；`topRef` 提供换算基准 | P0 |
| 7 | `apps/web/lib/store.test.ts` | 改 | 新增 `setRightPanelWidth`/`setDepthPanelRatio` 夹取与非有限数忽略、持久化、v11→v12 迁移用例 | P1 |

> P0 = 本轮必须；P1 = 强烈建议（保障回归与门禁绿）。**不**涉及任何 `services/quant`、`desktop`、DB、API 文件。

---

## 5. 接口设计（Interface Design）

> 本轮**无 REST / WebSocket / CLI 接口变更**——纯前端布局与本地状态。以下为前端组件 props 与 store action 契约。

### 5.1 可复用手柄组件 props

```ts
// components/ui/PanelResizeHandle.tsx
interface PanelResizeHandleProps {
  /** 拖拽中每帧回吐指针的视口 X 坐标（clientX）；父组件据自身容器 rect 换算成宽度/比例后写 store。 */
  onResize: (clientX: number) => void;
  /** 双击复位回调（可选）。 */
  onReset?: () => void;
  /** 无障碍标签与 title 提示文案。 */
  ariaLabel?: string;
  /** 透传到根 div 的附加类名（可选）。 */
  className?: string;
}
```

参考实现（落地范式，开发节点可逐行照搬）：

```tsx
"use client";
import clsx from "clsx";
import { useRef, type PointerEvent } from "react";

export default function PanelResizeHandle({ onResize, onReset, ariaLabel, className }: PanelResizeHandleProps) {
  const draggingRef = useRef(false);
  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    draggingRef.current = true;
  };
  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    if (!draggingRef.current) return;
    onResize(e.clientX);
  };
  const onPointerUp = (e: PointerEvent<HTMLDivElement>) => {
    draggingRef.current = false;
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
  };
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={ariaLabel}
      title={ariaLabel}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onDoubleClick={onReset}
      style={{ touchAction: "none" }}
      className={clsx(
        "group flex w-1.5 shrink-0 cursor-col-resize items-center justify-center",
        "border-l border-[var(--border)] bg-[var(--surface-2)] hover:bg-[var(--accent-soft)]",
        className,
      )}
    >
      <div className="h-8 w-0.5 rounded-full bg-[var(--border)] group-hover:bg-[var(--accent)]" />
    </div>
  );
}
```

> 该手柄位于 `<main className="no-drag …">` 之内，天然继承 `no-drag`，不与桌面自定义标题栏拖拽区冲突（参见 `docs/diagnoses/desktop-titlebar-drag-region-blocks-left-panels`）。无需额外 `no-drag`，如需稳妥可由父级透传 `className="no-drag"`。
>
> **评审补充（R6，可选健壮性增强）**：上述参考实现与既有横向手柄同样未处理指针被系统取消的场景——若拖拽中触发 `pointercancel`/`lostpointercapture`，`draggingRef` 会残留为 `true`。该缺口与既有横向手柄同构、概率极低且不影响主路径，本轮不强制；落地时可顺手补 `onPointerCancel`/`onLostPointerCapture={() => { draggingRef.current = false; }}` 一并复位，零额外状态。

### 5.2 换算纯函数（`lib/panelResize.ts`）

```ts
/**
 * 右锚面板：面板占据 [clientX, containerRight]，其宽度 = 右边缘 − 指针X。
 * 仅做几何换算，不夹取（夹取在 store setter 内，单一真相）。
 */
export function widthFromRightEdge(containerRight: number, clientX: number): number {
  return containerRight - clientX;
}

/**
 * 右锚列：列占容器右侧，比例 = (右边缘 − 指针X) / 容器宽。
 * containerWidth<=0 时返回 0（除零守卫；调用方 store 会夹取到下限）。
 */
export function ratioFromRightEdge(containerRight: number, containerWidth: number, clientX: number): number {
  return containerWidth > 0 ? (containerRight - clientX) / containerWidth : 0;
}
```

### 5.3 store action 契约（`lib/store.ts`）

```ts
setRightPanelWidth: (px: number) => void;   // 夹取 [MIN_RIGHT_PANEL_WIDTH, MAX_RIGHT_PANEL_WIDTH]，非有限数忽略
setDepthPanelRatio: (ratio: number) => void; // 夹取 [MIN_DEPTH_PANEL_RATIO, MAX_DEPTH_PANEL_RATIO]，非有限数忽略
```

实现仿既有 `setChartSplitRatio`/`setSimBalance`：

```ts
setRightPanelWidth: (n) =>
  set((s) =>
    Number.isFinite(n)
      ? { rightPanelWidth: Math.min(Math.max(n, MIN_RIGHT_PANEL_WIDTH), MAX_RIGHT_PANEL_WIDTH) }
      : { rightPanelWidth: s.rightPanelWidth },
  ),
setDepthPanelRatio: (n) =>
  set((s) =>
    Number.isFinite(n)
      ? { depthPanelRatio: Math.min(Math.max(n, MIN_DEPTH_PANEL_RATIO), MAX_DEPTH_PANEL_RATIO) }
      : { depthPanelRatio: s.depthPanelRatio },
  ),
```

---

## 6. 数据模型（Data Model）

### 6.1 Store 扩展（持久化形态，localStorage key `aragon-ui`）

```ts
// 新增 state 字段
rightPanelWidth: number;   // 右侧 RightPanel 像素宽，默认 380
depthPanelRatio: number;   // 盘口列占上区宽度比例，默认 0.15

// 新增常量（与既有 DEFAULT_CHART_SPLIT_RATIO 同位置导出，供组件复位与测试引用）
export const DEFAULT_RIGHT_PANEL_WIDTH = 380;
const MIN_RIGHT_PANEL_WIDTH = 320;
const MAX_RIGHT_PANEL_WIDTH = 640;   // 宽屏（默认窗口 1440，见 desktop/src/main.js）上限。⚠ 修正(R1)：640 与左侧内容地板（标的 220 + 手柄 6 + 盘口 160 = 386）相加得 386 + 6 + 640 = 1032 > minWidth 860——故近最小窗口把右栏拉到接近 MAX 会触发 overflow-hidden 优雅裁切（无滚动条、双击复位/拉宽窗口即恢复）；右栏 ≤ 默认 380（含最小 320）→ 地板 ≤772 < 860 为不裁切硬保证包络。完整核算见 §6.2。
export const DEFAULT_DEPTH_PANEL_RATIO = 0.15;
const MIN_DEPTH_PANEL_RATIO = 0.10;
const MAX_DEPTH_PANEL_RATIO = 0.30;
```

`partialize` 增列 `rightPanelWidth`、`depthPanelRatio`（与既有 `chartSplitRatio` 并列）。`version: 11 → 12`，`migrate` 追加：

```ts
if (version < 12) {
  next = { ...next, rightPanelWidth: DEFAULT_RIGHT_PANEL_WIDTH, depthPanelRatio: DEFAULT_DEPTH_PANEL_RATIO };
}
```

> 说明：两字段为可选追加，zustand 浅合并对缺省字段本就回落初值；升版 + `migrate` 仅为「显式稳妥」，与既有 v6→v7（`chartSplitRatio`）同构。两字段均**非运行期句柄**，持久化安全（不会像 `runId`/`paperSessionId` 那样失效）。
>
> **初始化器默认值（R3，必做）**：两字段的默认值必须同时写进 `create<AppState>()((set) => ({ … }))` 的**初始化对象**（与 `chartSplitRatio: DEFAULT_CHART_SPLIT_RATIO` 并列），而非仅声明在 `AppState` 接口里——`store.ts` 顶部已注明「全新安装只走初始化器、不触发 `migrate`，故默认值必须写在此处」。TS 类型亦会强制该字段在初始化器出现，二者互为保险。即在初始化对象中追加：
>
> ```ts
> rightPanelWidth: DEFAULT_RIGHT_PANEL_WIDTH,   // 380
> depthPanelRatio: DEFAULT_DEPTH_PANEL_RATIO,   // 0.15
> ```

### 6.2 边界与几何约束（夹取相互校验）

- **比例夹取与像素夹取双保险**：`depthPanelRatio∈[0.10,0.30]` 控相对宽；盘口列 `min-w-[160px] max-w-[420px]` 控绝对宽。当上区很窄（如 1000px）时 `0.10*1000=100<160` → `min-w` 生效保可读；很宽时 `0.30` 与 `max-w-420` 取较小者。二者不冲突，像素是安全地板/天花板。
- **水平地板核算（不溢出包络，R1 已修正）**：宽度拆为「中列内容地板」+「手柄」+「右栏轨道」三段。
  - **中列内容地板**（ChartWorkspace 上区）= 左栏 `min 220` + 盘口手柄 `6` + 盘口 `min 160` = **386px**（图表 `flex-1 min-w-0` 吸收剩余，可被压到 0）。
  - **主网格总地板** = 中列 `386` + 右栏手柄 `6` + 右栏当前宽 `rightPanelWidth`。代入三档：
    - 默认右栏 `380`：`386 + 6 + 380 = 772 < 860` ✅ 不裁切；
    - 最小右栏 `320`：`386 + 6 + 320 = 712 < 860` ✅ 不裁切（**原 v1 证明实际只覆盖了这一档**）；
    - 最大右栏 `640`：`386 + 6 + 640 = 1032 > 860` ⚠️ 当窗口落在 `[860, 1032)` 且右栏被拉到接近 MAX 时，中列可用轨道（窗口宽 − 6 − 右栏宽）跌破 386，**左栏 / 盘口列会被 `<main> overflow-hidden` 裁切**。注意：因 `overflow-hidden`，表现为内容被裁掉而**非**出现横向滚动条；`min-w-0` 只能让图表自身压到 0，**无法**替固定下限的左栏（220）/ 盘口（160）让位，故这是真实裁切而非「图表被压窄」。
  - **定性与恢复**：上述裁切仅出现在用户「接近最小窗口」上「主动放大右栏」的极端组合，定性为**可接受的优雅降级**——无脏滚动条、双击任一手柄即复位（右栏回 380、盘口回 0.15）、或拉宽窗口即恢复；实际默认窗口为 1440（`main.js`），所有组合（含 1032）均充裕落入窗内。
  - **结论修正**：「不溢出 / 不裁切」的硬保证成立于「**右栏 ≤ 默认 380（含最小 320）**」的包络内；超出该包络的手动放大，以图表 / 盘口可见度换取面板宽度，可一键复位，不属缺陷。
- **最小窗口的图表余量（R2，已知权衡）**：在 860 窗口、右栏默认 380 下，中列可用 `860 − 6 − 380 = 474`，扣除左栏 220 + 盘口手柄 6 + 盘口 160（min-w 生效）后图表 ≈ **88px**，偏窄但可用（不裁切）。这是把盘口列从左栏剥出后**横向固定下限由 220 增至 386** 的代价；`minWidth 860` 仅为可收窄下限，默认 1440 下图表充裕。如后续认为 88px 过窄，可在独立轮次下调盘口像素地板（如 160→140，本轮不改以保持验收基线）。

---

## 7. 测试与验收标准（Testing & Acceptance）

### 7.1 单元测试（Vitest，必须）

- `panelResize.test.ts`（新增）：
  - `widthFromRightEdge_subtracts_pointer_from_right`：`right=1000, x=620 → 380`；`x=400 → 600`（验证右锚减法方向，防符号错）。
  - `ratioFromRightEdge_divides_remainder_by_width`：`right=1200, width=1200, x=1020 → 0.15`；`x=900 → 0.25`。
  - `ratioFromRightEdge_guards_zero_width`：`width=0 → 0`（除零守卫，不产生 NaN/Infinity）。
- `store.test.ts`（扩展）：
  - `setRightPanelWidth clamps to [320, 640]`：传 -5→320、传 9999→640、传 500→500。
  - `setRightPanelWidth ignores non-finite input`：NaN / +Infinity 保持原值。
  - `setDepthPanelRatio clamps to [0.10, 0.30]`：传 0→0.10、传 1→0.30、传 0.2→0.2。
  - `setDepthPanelRatio ignores non-finite input`：同上。
  - `persists rightPanelWidth and depthPanelRatio`：setter 后 `localStorage` 的 `state` 含两字段。
  - `migrates v11 persisted state to v12 with default panel sizes`：注入 `{version:11}` 持久化 → rehydrate 后 `rightPanelWidth===380`、`depthPanelRatio===0.15`，既有字段仍恢复。

### 7.2 类型 / 构建门禁（必须）

- `pnpm --filter web typecheck`（tsc）零错误；`pnpm --filter web test`（vitest）全绿；`pnpm --filter web build` 通过。注意组件测试需 `NODE_ENV=test`（既有约定，见仓库记忆）。

### 7.3 人工验收清单（对照两条需求）

1. **需求一**：右侧"AI 助手 / 交易策略"面板左边框出现可见竖向手柄（hover 高亮、`col-resize` 光标）；按住向左拖→面板变宽、K 线区相应变窄；向右拖→反之；拖到极限被夹取在 `[320,640]`；双击手柄复位 380；刷新页面后宽度保持；任意有效窗口宽度下右侧面板与所有面板均完整落在窗口内、无横向滚动条。
2. **需求二**：盘口五档 + 成交量 + 实时成交带面板从左栏消失，出现在 **K 线图右侧**独立列，默认约占上区 15% 宽；其左边框有可见竖向手柄，可手工拖宽/拖窄（夹取 `[0.10,0.30]`、像素 `[160,420]`）；双击复位 0.15；刷新后保持；左栏此时仅显示标的列表且占满整列高度；盘口数据（最新价 / 五档 / Bar Vol / 成交带）随实时行情正常刷新，与迁移前一致。
3. **回归**：上一轮纵向手柄（图表/下区调高）仍可用；切换标的/周期/主题、实时流脉冲、缓存样例横幅、实盘 `LiveBottomPanel` 左右分栏均正常；`RightPanel` 内"AI 助手 / 交易策略"页签切换与各自状态保持不变；桌面端 1366×768 @150% 缩放下窗口可收窄至屏内、各面板不被遮挡；无 hydration 告警、无指针监听泄漏（`setPointerCapture` 卸载自动释放）。

---

## 8. 风险与缓解（Risks & Mitigations）

| 风险 | 影响 | 缓解 |
|------|------|------|
| `page.tsx` / `ChartWorkspace.tsx` 增量后突破 CLAUDE.md 尺寸阈值（文件<800 行 / 函数<50 行） | 可维护性 / 一致性 | 把拖拽 DOM 捕获封装进可复用 `PanelResizeHandle`、把换算封装进纯函数 `panelResize.ts`，两处接线各仅 +1 ref +1 handler（~6 行）；`page.tsx`、`ChartWorkspace.tsx` 增量后仍 <800 行（开发节点提交前以行数自查） |
| 右锚换算符号写反（用 `clientX − left` 而非 `right − clientX`） | 拖拽方向相反 / 跳变 | 抽 `widthFromRightEdge`/`ratioFromRightEdge` 纯函数并先写测试锁定方向（§7.1），组件仅调用之 |
| 持久化的大右栏宽在更小窗口下挤压 K 线、甚至裁切左栏/盘口 | 极窄视口可用性 | **已修正核算（§6.2 / R1）**：右栏 ≤ 默认 380（含最小 320）的包络内地板 ≤772 < 860，**硬保证不裁切**；右栏拉向 MAX 640 且窗口接近 860 时地板达 1032 > 860，会触发 `overflow-hidden` 裁切——定性为**可接受的优雅降级**（无横向滚动条、双击复位 / 拉宽窗口恢复）。K 线 `min-w-0` 连续让位仅吸收图表自身宽度，**无法**替固定下限的左栏/盘口让位；默认窗口 1440 下所有组合充裕 |
| `flexBasis` 百分比与 `min-w/max-w` 像素夹取互相打架 | 盘口列宽不符预期 | 二者为「相对宽 + 绝对安全边界」分工（§6.2）：常规窗口走比例，极端窗口走像素夹取，取较小/较大者，无逻辑冲突；验收覆盖窄/宽两档 |
| Tailwind 无法表达运行期动态列宽 | 右侧宽度无法生效 | 主网格列模板用 inline `style.gridTemplateColumns`（运行期字符串拼接），手柄列固定 `6px`、右列 `{rightPanelWidth}px`；与既有 `chartSplitRatio` 用 inline `flexGrow` 同范式 |
| 盘口列变高后内容溢出 | 成交带/五档被截断 | 盘口列包裹 `min-h-0 overflow-auto`，`MarketDepthPanel` 内部 `max-h-40` 成交带保持；纵向滚动兜底 |
| 拖拽手柄落在桌面自定义标题栏 `drag-region` 致点击穿透 | 桌面端拖不动 | 手柄在 `<main className="no-drag">` 之内，天然 `no-drag`；参见 `desktop-titlebar-drag-region-blocks-left-panels` 诊断；如仍异常可父级透传 `className="no-drag"` |
| store `version` 升级迁移遗漏 | 旧持久化缺字段 | `version 11→12` + `migrate(version<12)` 补两默认值；追加字段即便不迁移也由浅合并回落默认（显式稳妥，§6.1） |
| 引入第三方分栏库 | 体积 / 许可证 / 与约定相悖 | 明确**不引入**（react-resizable-panels/allotment 等），复用既有 Pointer 范式，零新运行时依赖（CLAUDE.md §八#6） |

---

## 9. 附录：关键落地片段（供开发节点逐行参考）

### 9.1 `page.tsx` 主网格三列 + 右侧手柄

```tsx
// 顶部新增
import PanelResizeHandle from "@/components/ui/PanelResizeHandle";
import { widthFromRightEdge } from "@/lib/panelResize";
// store 解构追加：rightPanelWidth, setRightPanelWidth, DEFAULT_RIGHT_PANEL_WIDTH（从 store 导入）

const mainRef = useRef<HTMLElement>(null);
const onResizeRight = (clientX: number) => {
  const rect = mainRef.current?.getBoundingClientRect();
  if (!rect) return;
  setRightPanelWidth(widthFromRightEdge(rect.right, clientX));
};

// 渲染：替换原 <main className="no-drag grid flex-1 grid-cols-[minmax(0,1fr)_380px] overflow-hidden">
<main
  ref={mainRef}
  className="no-drag grid flex-1 overflow-hidden"
  style={{ gridTemplateColumns: `minmax(0,1fr) 6px ${rightPanelWidth}px` }}
>
  <ChartWorkspace … />
  <PanelResizeHandle
    onResize={onResizeRight}
    onReset={() => setRightPanelWidth(DEFAULT_RIGHT_PANEL_WIDTH)}
    ariaLabel="拖动调整右侧面板宽度，双击复位"
  />
  <aside className="min-h-[300px] min-w-0 overflow-hidden bg-[var(--surface)]/40">
    <RightPanel />
  </aside>
</main>
```

> **评审补充（R7，可选观感）**：列 1 的 `<section>`（ChartWorkspace 根）保留了 `border-r`，而手柄组件自带 `border-l`——二者相邻会叠出约 2px 分隔线。落地时可二择一去掉（例如手柄保留 `border-l`、列 1 `<section>` 去掉 `border-r`）以呈现单像素分隔，纯观感、不影响布局。

### 9.2 `ChartWorkspace.tsx` 上区盘口列迁移 + 手柄

```tsx
// 顶部新增
import PanelResizeHandle from "@/components/ui/PanelResizeHandle";
import { ratioFromRightEdge } from "@/lib/panelResize";
// store 解构追加：depthPanelRatio, setDepthPanelRatio；从 store 导入 DEFAULT_DEPTH_PANEL_RATIO

const topRef = useRef<HTMLDivElement>(null);
const onResizeDepth = (clientX: number) => {
  const rect = topRef.current?.getBoundingClientRect();
  if (!rect) return;
  setDepthPanelRatio(ratioFromRightEdge(rect.right, rect.width, clientX));
};

// 上区容器加 ref；左栏移除 MarketDepthPanel；末尾追加手柄 + 盘口列：
<div ref={topRef} className="flex min-h-0 min-w-0 flex-row" style={{ flexGrow: chartSplitRatio, flexShrink: 1, flexBasis: 0 }}>
  {/* 左栏：仅标的列表 */}
  <div className="flex min-h-0 shrink-0 basis-[16%] min-w-[220px] max-w-[300px] flex-col border-r border-[var(--border)]">
    <div className="min-h-0 flex-1">
      <InstrumentSidebar active={instId} onSelect={setInst} livePrice={market.lastPrice} changePct={sessionChangePct} />
    </div>
  </div>

  {/* 图表区：保持不变 */}
  <div className="flex min-h-[320px] min-w-0 flex-1 flex-col"> … </div>

  {/* 新增：竖向手柄 + 盘口列（K 线右侧，≈15% 可拖） */}
  <PanelResizeHandle
    onResize={onResizeDepth}
    onReset={() => setDepthPanelRatio(DEFAULT_DEPTH_PANEL_RATIO)}
    ariaLabel="拖动调整盘口面板宽度，双击复位"
  />
  <div
    className="flex min-h-0 min-w-[160px] max-w-[420px] flex-col overflow-auto"
    style={{ flexGrow: 0, flexShrink: 0, flexBasis: `${depthPanelRatio * 100}%` }}
  >
    <MarketDepthPanel market={market} activeCandle={displayCandle} />
  </div>
</div>
```

### 9.3 验收回归测量（沿用诊断报告同款注入法）

在 860 / 1024 / 1280 / 1440 各宽度、**右栏取默认 380（即落在 §6.2 的不裁切包络内）** 下断言 `documentElement.scrollWidth === innerWidth`、右侧面板与盘口列均未被裁切；拖动两手柄**在各自夹取范围内**后再次断言不溢出。

**极端组合单列验收（R1）**：在 860 窗口把右栏拖向 MAX 640，预期进入「优雅降级」——因 `<main> overflow-hidden`，`documentElement.scrollWidth` 可能 `> innerWidth` 但**不出现横向滚动条**、无 JS 错误；双击右栏手柄复位 380 或拉宽窗口后，`scrollWidth === innerWidth` 重新成立。故**不要**把 `scrollWidth===innerWidth` 当作全区间不变式，仅在「右栏 ≤ 默认 380」包络内作硬断言。

**最小窗口图表余量验收（R2）**：在 860 窗口、右栏默认 380 下确认图表区仍正常渲染（左 220 + 盘口 160 后图表 ≈88px，偏窄但可用、不裁切）——`minWidth 860` 仅为可收窄下限，默认窗口 1440 下充裕。

刷新后 `localStorage['aragon-ui']` 含 `rightPanelWidth`/`depthPanelRatio` 且渲染宽度与之一致。

---

## 10. Definition of Done 对照（Subtask #0）

- ✅ `docs/plans/resizable-workstation-panels/spec.md` 已创建，可在 `git status` 见到。
- ✅ 含 概述 / 技术设计 / 文件变更计划（表）/ 接口设计 / 数据模型 / 测试与验收 / 风险与缓解 全部章节，篇幅 > 800 字。
- ✅ 设计细化到组件 props、纯函数签名、store 字段与夹取常量、inline 样式与类名、迁移版本号，下游可逐行实现。
- ✅ 本节点只产出设计文档，未改任何 `docs/plans/<feature>/` 以外的源文件，未 `git commit`。

---

## 11. 评审结论（Review Verdict · Subtask #1）

**结论：有条件通过（Approved with conditions）**。

方案经四维（可行性 / 完整性 / 一致性 / 适当规模）逐节评审，并对照实际源码（`page.tsx` / `ChartWorkspace.tsx` / `store.ts` / `MarketDepthPanel.tsx` / `desktop/src/main.js` / `components/ui/index.tsx` / `package.json`）逐项核验。整体判断：

- **可行性 ✅**：纯前端、零后端、零新依赖；persist `version 11→12`、`partialize`、`migrate`、`skipHydration` + `rehydrate()`、夹取 setter、Pointer 捕获、CSS 变量、`MarketDepthPanel` props、桌面 `minWidth:860`、`clsx` 依赖均与现状一一吻合，可逐行落地。
- **完整性 ✅（修复后）**：原 v1 的「不溢出证明」存在以偏概全的逻辑漏洞（**P1 / R1**），已就地修正为正确的组合地板公式与「优雅降级」边界，并同步修正 §8 风险行、§9.3 验收断言；最小窗口图表余量（R2）作为已知权衡显式记录并纳入验收。
- **一致性 ✅**：复用既有范式，未制造孤岛；初始化器默认值要求（R3）已显式补强，barrel/手柄不统一（R4/R5）已说明取舍。
- **适当规模 ✅**：把拖拽与换算抽成 `PanelResizeHandle` + `panelResize.ts`，两处接线各仅 +1 ref +1 handler，文件均 <800 行；未引入超范围重构。

**修复状态**：无 P0；唯一 **P1（R1）已就地修复且自洽**；无未决 P0/P1。

**放行条件（开发节点 Subtask #2 落地时须遵守）**：

1. **（源于 R1）** 严格实现夹取常量 `MIN/MAX_RIGHT_PANEL_WIDTH = [320,640]`、`MIN/MAX_DEPTH_PANEL_RATIO = [0.10,0.30]` 与像素 `min-w-[160px] max-w-[420px]`；验收按 §9.3 区分「默认包络硬断言」与「极端组合优雅降级」，**不得**把 `scrollWidth===innerWidth` 当全区间不变式。
2. **（源于 R3，必做）** 两个新字段的默认值务必写进 `store.ts` 的 `create((set)=>({…}))` 初始化对象（不止接口与 `partialize`），并补 v11→v12 `migrate` 分支与对应 `store.test.ts` 用例。
3. **（源于 R2）** 验收覆盖 860 最小窗口档，确认无裁切且图表仍渲染；如判定 88px 过窄，另起轮次调整盘口像素地板，不在本轮擅自变更基线。
4. **（源于 R4–R7，建议非阻断）** 可选地：barrel 再导出手柄、手柄补 `onPointerCancel`/`onLostPointerCapture` 复位、列 1 与手柄相邻边框去其一。
5. 落地后须满足 §7.2 门禁：`pnpm --filter web typecheck` / `test`（`NODE_ENV=test`）/ `build` 全绿。

> 评审人签署：Senior Reviewer（Anthropic 工程团队角色）。文档版本已升至 **v2**。
