# 左侧竖向主导航栏（Left Nav Rail）开发方案 spec.md

> Feature: `left-nav-rail` · 版本 **v2** · 作者：方案设计节点（Subtask #0）· 评审：方案评审节点（Subtask #1）
> 关联需求：在整个工作台最左侧常驻一条竖向菜单栏，纵向排布功能按钮（当前两项：「交易」「因子实验室」），
> 点击「因子实验室」切到因子实验室面板；移除顶栏原有的「因子实验室」按钮；
> 不同功能来回切换时保留各自切换前的状态；美观优雅、符合人机交互最佳实践。

---

## 评审记录（Review Notes · Subtask #1）

> 评审维度：可行性（Feasibility）/ 完整性（Completeness）/ 一致性（Consistency）/ 规模适配（Right-sizing）。
> 评审基于对真实代码的逐项核对（`apps/web/app/page.tsx`、`apps/web/lib/store.ts`、`apps/web/components/RightPanel/RightPanel.tsx`、
> `apps/web/components/Factor/FactorLab.tsx`、`apps/web/components/Factor/FactorButton.tsx`、`apps/web/components/Chart/CandleChart.tsx`、`apps/web/app/globals.css`）。
> 已核实并通过的关键前提：① `RightPanel` 确以「常驻挂载 + HTML `hidden` 属性」切换且其被切元素**不带** `display` 工具类（精确印证惯用法）；
> ② `CandleChart` 已用 `autoSize: true` + `ResizeObserver`（line 198/253）——隐藏后再显示可自愈重排；③ store 当前 `version: 18`、
> `--accent`/`--accent-soft`/`--surface-2` 等 token 与 `ease-smooth` 均存在；④ `setShowFactorLab` 调用点共 6 处（store、page、FactorLab、
> AgentPanel、StrategyDetail、FactorSetBuilder）+ `FactorButton`（随文件删除），与 R5 枚举一致；⑤ `show_factor` ui-action 另设 `setFactorOverlay`/
> `setFactorLabSection` 后再开 lab，本方案保留前两者、仅替换开关动作，无回归。

| # | 维度 | 严重度 | 问题 | 处置 |
|---|---|---|---|---|
| C-1 | 可行性 | **P0** | §2.1/§2.2 提出「以 `hidden` 切换交易 `<main>`」，而该 `<main>` 携带 `grid`（`display:grid`）类。HTML `hidden` 属性的隐藏来自 UA 样式 `[hidden]{display:none}`（来源层级最低），会被作者样式 `.grid` 覆盖——**切换元素仍可见，视图切换核心机制失效**（两视图叠显）。这正是 `RightPanel` 惯用法刻意把 `hidden` 放在「不带 display 工具类」的 wrapper 上的原因。 | 已修：§2.1/§2.2 改为「被切元素一律为不带任何 `display` 工具类的 wrapper，承载 `hidden`；`grid`/`flex` 等布局类与 `mainRef` 下沉到 wrapper 内层元素」。同样适用于 `FactorLab` 根（带 `flex`）与设置层。 |
| C-2 | 完整性 | **P1** | 设置中心由原「条件渲染整块替换 `<main>`」改为「覆盖」。原文未显式约束：访问设置时**两个一级视图必须保持挂载**。若开发者把 `showSettings` 实现成卸载/替换内容区，则进出设置会丢失交易/因子视图状态，与「保留状态」主目标冲突。 | 已修：§2.1 明确「设置打开时交易/因子 wrapper 继续挂载、仅追加 `hidden`；设置层不得卸载这两视图」，并给出纯 `hidden`（无绝对定位）的同层方案。 |
| C-3 | 完整性 | **P1** | §6 测试/验收缺两条针对上述 P0/P1 的回归护栏：①「切到因子后交易视图确实隐藏（而非叠显）」②「往返设置中心后交易/因子视图状态不丢」。CLAUDE.md §七要求修 bug 先有可复现测试，C-1 属 bug 类机制缺陷，须有验收覆盖。 | 已修：§6.1 增 NavRail 切换后被隐藏视图 `hidden`/不可见的断言指引；§6.2 增「设置往返保真」验收项。 |
| C-4 | 规模适配 | P2 | §2.1 的「三块**绝对定位堆叠**（`absolute inset-0`）」相对惯用法过度设计：纯 `hidden`（同层流式，镜像 `RightPanel`）即可满足互斥显示且更简单；且绝对定位本身不解决 C-1（`.grid` 仍覆盖 `[hidden]`）。 | 已修：§2.1 收敛为「同层流式 + `hidden`」，移除不必要的绝对定位堆叠。 |
| C-5 | 一致性 | P2 | R1（`hidden` 下图表重绘）措辞偏保守（"若有问题再复用 resize 路径"）。实测 `CandleChart` 已 `autoSize:true`+`ResizeObserver`，由隐藏(0×0)恢复可见时自动重排且**保留**用户缩放/平移（恰是状态保真所需，不应 `fitContent`）。 | 已修：R1 升级为「机制已自洽」的确定性表述并标注 line 198/253，仅保留开发期目检。 |
| C-6 | 可行性 | P2（建议） | NavRail 为竖向导航：现方案以普通 `<button>`+`aria-current` 实现，键盘 Tab/Enter 可达（达标）。可选增强：roving tabindex + 上/下方向键移动焦点更贴合 APG。`aria-current="page"` 用于 SPA 视图切换可接受，亦可用 `aria-current="true"`。 | 不阻塞，记为可选增强（§2.3 备注）。 |
| C-7 | 可行性 | P2（建议） | 设置覆盖关闭后 `active` 由假转真会再次抢焦 FactorLab 标题（§2.4 焦点 effect），切设置只是临时遮挡时略显突兀。 | 不阻塞：可在实现时把「聚焦标题」限定为「交易↔因子的真实视图切换」而非设置遮挡开合；记为可选优化。 |

---

## 1. 概述（Overview）

当前工作台（`apps/web/app/page.tsx`）是一个「左图 + 右面板」的单视图结构：顶栏（`<header>`）右簇里有一个
`FactorButton`（因子实验室入口）与 `SettingsGearButton`（设置入口），二者各自把整块 `<main>` 在
「工作台 / 因子实验室 / 设置中心」三态间**条件渲染**互斥切换。因子实验室与设置面板是全屏覆盖式面板，
被切走的视图会**被卸载**，组件级本地态（例如因子实验室 `WorkbenchContext` 里的评估目标 `evalTarget`、
待加入组合成员 `pendingMembers`，以及 lightweight-charts 图表实例的缩放/平移视图）随之丢失，回到该功能时需重建。

本方案把「功能切换」从「顶栏散落的开关按钮 + 条件卸载」升级为「最左侧常驻竖向导航栏（NavRail） + 多视图常驻挂载、
用 `hidden` 切换可见性」的应用外壳（App Shell）模型。导航栏纵向排布两个一级功能项——「交易」（即现有工作台）与
「因子实验室」——常驻在 header 之下、内容区最左侧；选中项以赭色强调（`--accent`）+ 左侧指示条高亮。顶栏的
`FactorButton` 随之移除。两个一级视图同时挂载、互斥显示，从而**天然保留各自切换前的完整状态**，这与仓库现有
`RightPanel`（「两面板始终挂载，用 hidden 切换页签不破坏状态」）一脉相承，是项目内已验证的惯用法。

设计目标有三：其一，导航语义清晰、可扩展（未来新增一级功能只需往导航项数组追加一条）；其二，状态保真——
来回切换不重置用户工作上下文；其三，零回归桌面端标题栏——header 的拖拽区 / mac 红绿灯安全区 / Windows WCO
系统三键安全区契约**完全不动**（该区域历史上反复回归，见 `docs/diagnoses/desktop-*`），导航栏置于 header 之下规避一切交互。

---

## 2. 技术设计（Technical design）

### 2.1 布局架构

外层仍为竖向 flex 列：`header`（48px，原样保留）在上；其下新增一行 `flex` 容器，左侧是 `NavRail`（固定宽 64px、
`border-r`、常驻），右侧是「内容区」（`flex min-h-0 flex-1 flex-col`）。内容区内为**同层流式的若干 wrapper，以 HTML
`hidden` 属性互斥切换可见**（镜像 `RightPanel` 惯用法，不引入绝对定位）：

```
┌───────────────────────────────────────────────────────────────┐
│ header (brand/drag · AppMenu · 空 · 账户/主题/设置/窗口键)        │  ← 原样不动
├──────┬────────────────────────────────────────────────────────┤
│      │  内容区 (flex flex-col, flex-1)                          │
│ Nav  │   ├ <div hidden=showSettings||view!="trade"> ← wrapper   │
│ Rail │   │     └ <main ref=mainRef class="grid h-full ...">交易 │
│ 64px │   ├ <div hidden=showSettings||view!="factor"> ← wrapper  │
│ 交易  │   │     └ <FactorLab active=.../>  (首激活后常驻)        │
│ 因子  │   └ {showSettings && <SettingsPanel/>}  设置覆盖层       │
│      │   ← 交易/因子始终挂载，仅切 hidden ⇒ 状态保留            │
└──────┴────────────────────────────────────────────────────────┘
```

- **关键约束（评审 C-1 / P0）**：承载 `hidden` 的元素**绝不可同时带 `display` 工具类**（`grid`/`flex`/`block` 等）。
  HTML `hidden` 的隐藏源自 UA 样式 `[hidden]{display:none}`（来源层级最低），会被作者样式（如 `.grid`/`.flex`）覆盖而失效。
  故交易视图的 `grid` 布局与 `mainRef`**下沉到 wrapper 内层 `<main className="grid h-full ...">`**，外层 wrapper 仅持
  `min-h-0 flex-1` + `hidden`（与 `RightPanel` 被切 `<div className="min-h-0 flex-1" hidden>` 完全同形）。`FactorLab` 根自带
  `flex`，同样必须包一层无 display 类的 wrapper 再切 `hidden`。
- **一级视图（交易 / 因子实验室）**：由持久化的 `activeView: "trade" | "factor"` 驱动，互斥 `hidden`。两者始终挂载
  （因子实验室采用「首激活后常驻」惰性挂载，详见 2.4），切换不卸载、不丢本地态。
- **设置中心（评审 C-2 / P1）**：保持「瞬态覆盖」语义，由 `showSettings`（不持久化）控制。其打开时，交易/因子两 wrapper
  **继续挂载、仅追加 `hidden`**（`hidden = showSettings || activeView!==key`），设置层作为同层最后一个孩子条件渲染于其上——
  **严禁把 `showSettings` 实现成卸载/替换内容区**，否则进出设置会丢失两视图状态、与「保留状态」主目标冲突。导航栏在设置打开时
  仍可见，点击任一导航项先 `setShowSettings(false)` 再切视图。设置不是导航栏一级项（与需求「两个功能按钮」一致）。
- `mainRef` 仍挂在交易视图内层 grid `<main>` 上；wrapper 与内层同宽，其右边缘 = 内容区右边缘 = 窗口右缘（导航栏在内容区
  **之外**的左侧），故右侧面板拖拽测宽 `widthFromRightEdge(rect.right, clientX)` 的几何**无需改动**。

### 2.2 状态模型变更（单一真相源：Zustand store）

新增一级视图选择态 `activeView`，**替换**原 `showFactorLab`：

- 类型：`export type WorkbenchView = "trade" | "factor";`
- 字段：`activeView: WorkbenchView`（默认 `"trade"`）；动作 `setActiveView(view: WorkbenchView): void`。
- **移除** `showFactorLab` / `setShowFactorLab`（因子实验室可见性改由 `activeView === "factor"` 派生）。
- **持久化**：把 `activeView` 加入 `partialize`（持久化），刷新后恢复用户上次所在视图——直接服务「保留状态」目标，
  与 `settingsSection` / `factorLabSection` 已持久化的取向一致。`showSettings` 仍为瞬态、不入 `partialize`。
- **版本迁移**：`version` 18 → 19，新增分支 `if (version < 19) next = { ...next, activeView: "trade" };`
  （老用户从未持久化过 `showFactorLab`，无需迁移其值，仅补 `activeView` 默认）。

### 2.3 导航栏组件 NavRail

新建 `apps/web/components/Nav/NavRail.tsx`（`"use client"`，内联 SVG，无三方图标依赖，镜像 `FactorButton`/
`SettingsGearButton` 的着色与圆角 token）：

- 容器：`<nav aria-label="主功能导航" class="flex w-16 shrink-0 flex-col gap-1 border-r border-[var(--border)] bg-[var(--surface)]/60 px-2 py-3">`。
- 导航项数组 `ITEMS = [{ key:"trade", label:"交易", icon:<TradeIcon/> }, { key:"factor", label:"因子实验室", icon:<FactorIcon/> }]`。
- 每项为按钮：图标在上、`text-[10px]` 标签在下居中；`id={`nav-rail-${key}`}`；`aria-label`=完整标签；`title`=完整标签；
  选中态 `aria-current="page"` + `bg-[var(--accent-soft)] text-[var(--accent)]` + 左侧 `3px` 圆角强调条；
  非选中态 `text-[var(--muted)] hover:bg-[var(--surface-2)] hover:text-[var(--text)]`；过渡 `transition-all duration-150 ease-smooth`。
- 选中逻辑：`const select = (k) => { if (showSettings) setShowSettings(false); setActiveView(k); };`（点导航即关设置浮层）。
- 「交易」图标用蜡烛图风格内联 SVG；「因子实验室」图标复用 `FactorButton` 现有烧瓶 SVG path。
- 高亮以 `!showSettings && activeView === key` 计算（设置浮层打开时两项均不显选中，避免误导）。
- a11y（评审 C-6，达标 + 可选增强）：普通 `<button>` + `aria-current` 已满足 Tab/Enter/Space 可达；可选增强为 roving tabindex +
  上/下方向键移动焦点（更贴合 WAI-ARIA APG 竖向导航），不阻塞本期落地。

### 2.4 视图常驻与惰性挂载（状态保真的核心）

- 交易视图：始终挂载（页面级行情 WS / K 线缓冲本就在 `Page` 顶层 effect、与显示哪个视图无关，已天然续跑），
  仅以 `hidden` 切显隐 ⇒ 图表实例、右面板页签态、滚动位置全部保留。
- 因子实验室：采用「**首激活后常驻**」——用 `hasOpenedFactor`（`useState`/`useRef`）记录是否曾切到过因子实验室，
  `activeView==="factor"` 首次为真时置位；置位后即便切回交易也保持挂载（`hidden`）。好处：从未打开过因子实验室的
  用户不会在应用启动时白白触发因子相关 TanStack Query；一旦打开，其 `WorkbenchContext` 暂存态（`evalTarget`/
  `pendingMembers`）即跨切换保留。
- `FactorLab` 接收新 prop `active: boolean`（= `activeView==="factor" && !showSettings`）：
  - 焦点：把「挂载即 focus 标题」改为「`active` 变为真时 focus 标题」（`useEffect(() => { if (active) headingRef.current?.focus(); }, [active])`），
    避免隐藏态抢焦点；**删除**原卸载时 `focus("#factor-lab-button")` 的回焦（该按钮已移除）。
  - Esc：仅在 `active` 时响应，且改为 `setActiveView("trade")`（不再 `setShowFactorLab(false)`）。
  - 「返回工作台」按钮：`onClick` 改为 `setActiveView("trade")`。

### 2.5 调用点改写（show*Lab → activeView）

所有原先开关因子实验室的入口统一切到 `setActiveView`：

- `AgentPanel.tsx`（`show_factor` ui-action）：`st.setShowFactorLab(true)` → `st.setActiveView("factor")`（其余 `setFactorOverlay`/`setFactorLabSection` 不变）。
- `StrategyDetail.tsx`（「在因子实验室编辑」）：选择器换 `setActiveView`；`setShowFactorLab(true)` → `setActiveView("factor")`（保留先 `setFactorLabSection("builder")`）。
- `FactorSetBuilder.tsx`（创建得分策略成功）：`setShowFactorLab(false)` → `setActiveView("trade")`。

### 2.6 操作时序（Sequence）

切到因子实验室：用户点 NavRail「因子实验室」→ `select("factor")`：若设置浮层开则 `setShowSettings(false)` →
`setActiveView("factor")` → store 通知 → `page` 重渲染：交易 `<main>` 置 `hidden`，`FactorLab` 取消 `hidden`
（首次激活触发惰性挂载）→ `FactorLab` 的 `active` effect 把焦点送入标题。切回交易反之，交易 `<main>` 重新可见、
图表实例与右面板态原样呈现，因子实验室转入隐藏但保持挂载与暂存态。

---

## 3. 文件 / 模块变更计划（File / module change plan）

| 文件 | 动作 | 一句话意图 |
|---|---|---|
| `apps/web/components/Nav/NavRail.tsx` | 新建 | 左侧竖向主导航栏：交易/因子实验室两项，驱动 `activeView`，常驻可见、选中高亮、完整 a11y。 |
| `apps/web/components/Nav/NavRail.test.tsx` | 新建 | NavRail 渲染两项、点击切 `activeView` 并关设置、`aria-current` 与 `aria-label` 的单元测试。 |
| `apps/web/lib/store.ts` | 修改 | 新增 `WorkbenchView`/`activeView`/`setActiveView`，移除 `showFactorLab`/`setShowFactorLab`，`partialize` 持久化 `activeView`，`version`→19 + 迁移补默认。 |
| `apps/web/app/page.tsx` | 修改 | header 下重排为 `NavRail + 内容区(flex-col)`；交易/因子各以「无 `display` 类的 wrapper + `hidden`」常驻切换（`grid`/`mainRef` 下沉内层 `<main>`，评审 C-1）；设置层条件渲染覆盖且**不卸载**两视图（C-2）；移除 `FactorButton` 引入与渲染；因子惰性挂载。 |
| `apps/web/components/Factor/FactorLab.tsx` | 修改 | 接收 `active` prop；焦点/Esc 按 `active` 门控；返回/Esc 改走 `setActiveView("trade")`；删除对已移除按钮的回焦。 |
| `apps/web/components/Factor/FactorButton.tsx` | 删除 | 顶栏因子入口由左侧导航栏取代，文件不再需要。 |
| `apps/web/components/Agent/AgentPanel.tsx` | 修改 | `show_factor` ui-action 由 `setShowFactorLab(true)` 改 `setActiveView("factor")`。 |
| `apps/web/components/Strategy/StrategyDetail.tsx` | 修改 | 「在因子实验室编辑」由 `setShowFactorLab(true)` 改 `setActiveView("factor")`。 |
| `apps/web/components/Factor/FactorSetBuilder.tsx` | 修改 | 创建得分策略成功后由 `setShowFactorLab(false)` 改 `setActiveView("trade")`。 |
| `apps/web/lib/store.test.ts` | 修改 | 增 `activeView` 默认为 `"trade"` 且持久化、`setActiveView` 写入的用例（可选但推荐）。 |

> 不新增任何第三方依赖；不触碰后端（`services/quant`）、桌面（`desktop/`）与 header 拖拽/安全区逻辑。

---

## 4. 接口设计（Interface design）

本需求为纯前端壳层重构，**不涉及任何 REST / WebSocket / CLI 协议变更**。唯一新增的「接口」是前端 store 动作签名：

```ts
// apps/web/lib/store.ts
export type WorkbenchView = "trade" | "factor";
interface AppState {
  activeView: WorkbenchView;                 // 默认 "trade"，持久化
  setActiveView: (view: WorkbenchView) => void;
  // 移除：showFactorLab: boolean; setShowFactorLab: (show: boolean) => void;
}

// NavRail 内部：
const select = (key: WorkbenchView) => { if (showSettings) setShowSettings(false); setActiveView(key); };

// FactorLab 新 prop：
function FactorLab({ active }: { active: boolean }) { /* ... */ }
```

`showSettings` / `setShowSettings` / `settingsSection` / `factorLabSection` / `factorOverlay` 等既有动作签名保持不变。

---

## 5. 数据模型（Data model）

无数据库 / 后端 schema 变更。仅前端内存态（`aragon-ui` localStorage 持久化键）形态调整：

```jsonc
// localStorage["aragon-ui"].state 的 partialize 子集（节选，新增/移除项）
{
  "activeView": "trade",   // 新增：持久化；"trade" | "factor"
  "factorLabSection": "catalog",
  "settingsSection": "ai-models"
  // 移除：showFactorLab（本就为瞬态，从未写入持久化，无残留）
}
```

- `activeView` 是唯一新增持久字段；`version` 由 18 升 19，旧持久态经 `migrate` 补 `activeView:"trade"`。
- 瞬态、不入 `partialize`：`showSettings`、`factorOverlay`、因子实验室 `WorkbenchContext` 本地态（随组件常驻挂载而在内存中保留，不落盘）。

---

## 6. 测试与验收标准（Testing & acceptance criteria）

### 6.1 自动化测试

- `store.test.ts`：断言 `activeView` 默认 `"trade"`；`setActiveView("factor")` 后 `getState().activeView==="factor"`
  且持久化串含 `activeView`；确认旧字段 `showFactorLab` 已不存在（编译期即由类型保证）。
- `NavRail.test.tsx`（组件测试，须置 `NODE_ENV=test` 运行，见 `apps/web` 既有约定）：
  - 渲染出两个按钮，可访问名分别为「交易」「因子实验室」；
  - 默认 `activeView==="trade"` 时「交易」项带 `aria-current="page"`；
  - 点击「因子实验室」→ `useAppStore.getState().activeView==="factor"`；
  - 设置浮层打开（`showSettings=true`）时点任一项 → `showSettings` 变 `false` 且 `activeView` 切换到该项。
- **隐藏机制回归护栏（评审 C-3 / 针对 C-1 P0）**：须有一条断言「被切走的视图 wrapper 真正不可见」的测试，而非仅断言
  `activeView` 已变。优先在 `NavRail.test.tsx` 同级或一个轻量 page/wrapper 片段测试里，对 wrapper 元素断言 `toHaveAttribute("hidden")`
  且 `toBeVisible()` 为否（`@testing-library/jest-dom` 的 `toBeVisible` 会顺带捕获「带 `display` 类导致 `hidden` 被覆盖」的本类回归）。
- 回归：全量 `pnpm --filter web test` 通过；`pnpm --filter web build`（或 `tsc --noEmit`）零类型错误
  （借此验证所有 `setShowFactorLab` 调用点均已改写、`FactorButton` 删除无悬挂引用）。

### 6.2 人工 / 验收

1. 最左侧常驻竖向导航栏可见，纵向排「交易」「因子实验室」两项，明暗主题下配色协调、选中态有赭色高亮 + 左指示条。
2. 顶栏不再有「因子实验室」按钮；设置齿轮与窗口控件位置、桌面端拖拽/最大化与系统三键安全区行为零回归。
3. 点击「因子实验室」切到因子实验室面板；点「交易」切回工作台。**切换后被切走的视图须确实不可见（不得与目标视图叠显）**（评审 C-3，守 C-1）。
3b. **设置往返保真（评审 C-3，守 C-2）**：在交易视图设定上下文（缩放 K 线 / 选中策略）→ 打开设置中心 → 关闭设置 → 交易视图状态原样保持；
   在因子实验室暂存成员 → 打开设置 → 关闭设置 → 因子实验室暂存态仍在（即设置覆盖期间两视图未被卸载）。
4. **状态保真**：在因子实验室「权重训练与组合」暂存若干成员 → 切「交易」→ 切回，暂存成员仍在；
   在交易视图缩放/平移 K 线、选中策略/运行模拟盘 → 切因子实验室 → 切回，图表视图与右面板态保持。
5. 刷新页面后恢复到刷新前所在视图（`activeView` 持久化）。
6. Agent 的 `show_factor`、策略详情「在因子实验室编辑」、组合保存成功后的跳转均正确落到对应视图。
7. 键盘可达：Tab 可聚焦导航项，回车/空格激活；切到因子实验室后焦点进入其标题；读屏可朗读各项可访问名与当前项。

---

## 7. 风险与缓解（Risks & mitigations）

| # | 风险 | 缓解 |
|---|---|---|
| R1 | lightweight-charts 在 `hidden`（`display:none`、尺寸 0）下隐藏，切回时需重新测宽重绘。 | **机制已自洽**（评审 C-5）：`CandleChart` 已 `autoSize: true` + 自建 `ResizeObserver`（`apps/web/components/Chart/CandleChart.tsx` line 198/253），容器从 0×0 恢复为可见尺寸时自动重排，且**保留**用户缩放/平移逻辑范围（恰为状态保真所需，故**不**调用 `fitContent`，亦**不**改 `chartSync` 范围/十字线契约）。开发期仅需目检切回无空白即可。 |
| R2 | 因子实验室常驻挂载在隐藏时仍跑查询，增加无谓网络/内存。 | 采用「首激活后常驻」惰性挂载（`hasOpenedFactor`），从未打开者零因子查询；打开后才挂载并保留态。 |
| R3 | 持久化 `activeView="factor"` 时，SSR 默认渲染 `trade` 与水合恢复间有一帧切换闪烁。 | 沿用既有 `skipHydration` + 挂载后 `rehydrate()` 的「默认→恢复一帧」既定取舍，不引入新模式。 |
| R4 | 误改 header 触发桌面端标题栏右簇被吞为 caption 的历史回归。 | 导航栏置于 header **之下**的内容区，header 拖拽区/`win-titlebar-safe`/mac 安全区与右簇结构**完全不动**，仅删一个右簇按钮。 |
| R5 | 残留 `setShowFactorLab` 调用点漏改导致编译失败。 | 已枚举全部 6 处调用点（store、page、FactorLab、AgentPanel、StrategyDetail、FactorSetBuilder）逐一改写；`tsc` 兜底。 |
| R6 | 焦点管理：删除 `#factor-lab-button` 后 FactorLab 卸载回焦指向空元素。 | 改为常驻不卸载 + `active` 门控聚焦标题，删除卸载回焦逻辑。 |
| R7 | 「因子实验室」标签较长（5 字）在 64px 宽栏内换行。 | `text-[10px] leading-tight text-center`，允许两行；`title`/`aria-label` 提供完整名；必要时把栏宽调到 72px（单点调整，不影响布局其余部分）。 |

---

## 8. 实施过程发现的方案缺陷（由下游开发节点回填）

（留空，开发节点如发现方案偏差请在此记录。）

---

## 评审结论（Review Verdict · Subtask #1）

**结论：通过（v2）。**

所有 **P0 / P1** 问题已在本文档 v2 正文就地修复，无遗留：

- **C-1（P0，已修）** —— `hidden` 切换必须作用于「不带 `display` 工具类」的 wrapper；交易 `grid` 与 `mainRef` 下沉内层 `<main>`，
  `FactorLab`（根带 `flex`）同样包 wrapper。修复落点：§2.1 关键约束、§3 `page.tsx` 行。
- **C-2（P1，已修）** —— 设置中心为「覆盖」而非「替换」：打开设置期间交易/因子两 wrapper 继续挂载仅追加 `hidden`，
  严禁卸载，保住「状态保真」。修复落点：§2.1 设置中心条目、§3 `page.tsx` 行。
- **C-3（P1，已修）** —— 增补针对 C-1/C-2 的回归护栏：§6.1「被切视图真正不可见」断言（含 `toBeVisible` 捕获 `display` 覆盖回归）、
  §6.2 第 3 / 3b 条「切换不叠显」「设置往返保真」验收。

其余为 **P2 非阻塞**，已在「评审记录」标注，可在实现时择优采纳，不构成放行条件：

- C-4（已就地收敛为同层 `hidden`，移除多余绝对定位）、C-5（R1 升级为「`autoSize`+`ResizeObserver` 自洽」的确定性表述）；
- C-6（NavRail 可选 roving tabindex + 方向键增强）、C-7（设置遮挡开合时避免重复抢焦 FactorLab 标题）——均为可选优化。

**放行前提（已写入正文，开发节点须严格遵循）**：承载 `hidden` 的元素一律不得叠加 `display` 工具类；`mainRef` 锚点几何
与 header 拖拽区 / 桌面安全区契约保持零改动（守 R4 标题栏历史回归）。据此，方案可进入「代码开发（Subtask #2）」。
