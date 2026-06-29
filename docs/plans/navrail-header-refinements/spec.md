# 导航栏图标化与顶栏精简（NavRail Hover + Header / Titlebar 精修）开发方案 spec.md

> Feature: `navrail-header-refinements` · 版本 **v2**（v1 由方案设计节点 Subtask #0 产出；本 **v2** 经方案评审节点 Subtask #1 加入「评审记录 / 评审结论」并修复 P1-1）
> 关联需求（第 2 轮，承接第 1 轮「左侧竖向 NavRail App Shell」`970e2ba` / `85b7d77`）：
> 1. 左侧 NavRail 功能按钮：默认**仅显示图标**，hover/focus 时再显示具体文字（而非图标+文字常驻并排）。
> 2. 顶部 header：移除「多策略量化模拟交易 · OKX 真实行情」副标题文字，以及其右侧那个「三条杠」小图标（`AppMenu` 汉堡菜单）。
> 3. header 下方的横线要**贯穿整个 header 全宽**——当前在 Windows 上没有覆盖到最大化/最小化/关闭按钮的下方。
>
> 总目标：美观优雅、顶级设计、符合人机交互最佳实践；零桌面端标题栏回归（该区域历史上反复回归，见 `docs/diagnoses/desktop-*`）。

---

## 评审记录（Review Notes · Subtask #1，v2）

> 评审人：Anthropic 工程团队资深评审。方式：逐节核对四维度（**可行性 / 完备性 / 一致性 / right-sizing**），并把方案声明逐条对照真实代码与项目历史（`docs/diagnoses/desktop-*`、项目记忆）。下列问题清单含处置；**P0/P1 已在 v2 正文直接修复**，P2 列出供开发节点参考（低成本澄清亦顺手并入正文）。

### 维度结论

- **可行性（通过）**：栈核对无阻塞——`apps/web` 用 Tailwind **3.4.13**，原生支持 `group-hover` / `group-focus-visible` / `focus-visible` 变体；`ease-smooth` 与 `--surface`/`--surface-2`/`--border`/`--text`/`--muted`/`--accent`/`--accent-soft` 均为既有 token（NavRail 现有代码已在用）；`clsx` 已引入。方案引用的行号与结构逐一核对**属实**：`NavRail.tsx` 按钮 74-100、`title` 在 82、常驻文字 span 在 98、`aria-label` 在 81；`page.tsx` `AppMenu` import 在 8、`border-b` 在 554、副标题在 574-576、`<AppMenu/>` 在 578、品牌簇外层 wrapper 在 564、内容区 `<div class="flex min-h-0 flex-1">` 在 594；`desktop/src/main.js` `TITLEBAR_OVERLAY_HEIGHT = 48` 在 218（注释明示「= 顶栏 h-12=48px」）；`globals.css` `win-titlebar-safe` 在 153-155。
- **完备性（基本完备，补 1 处 P1 + 数处边界澄清）**：键盘 focus、读屏可访问名、明暗主题、DPI 对齐、Web/mac/linux 零回归均有覆盖；唯一实质缺口是「删 `AppMenu` 后 WCO 自检入口在 Windows 客户端不可达」（P1-1）。
- **一致性（通过）**：与 `CLAUDE.md` 文件/函数阈值、既有 token、组件测试 `NODE_ENV=test` 约定一致；代码库无通用 `Tooltip` 原语（`components/ui` 内仅图表用 `Chart/TradeTooltip`，语义不同），故手写内联气泡不算「重复造轮子」。
- **Right-sizing（通过）**：主体为纯表现层精修、不动 store/后端/主进程，尺度恰当；唯一需小幅扩面的是 P1-1 的最小化设置入口迁移（仍复用既有桥接、零新增 IPC）。

### 问题清单与处置

| # | 维度 | 严重度 | 问题 | 处置 |
|---|---|---|---|---|
| P1-1 | 完备性/回归 | **P1** | 删 `AppMenu` 后 `aragon:show-about` 失去唯一 UI 触发点。该「关于」对话框承载 **Windows WCO 运行期自检**（`main.js` `showAbout` 301-333 的「WCO active：…」读出，依赖渲染层回传的 `lastOverlayState`），是本项目反复回归的标题栏右簇 caption bug 之「真机人工门」核心可观测量（项目记忆 + `docs/diagnoses/desktop-packaged-wco-header-right-still-unclickable` 明确依赖）。已核对：Windows 下 `installAppMenu()` 走 `Menu.setApplicationMenu(null)`（`main.js:356`），mac 模板菜单仅 `appMenu/editMenu/viewMenu` 标准 role，均不触发本项目自带的 instrumented `showAbout()`；故 `AppMenu` 是 **Windows 上唯一入口**。仅留 IPC、无任何调用路径 = 能力实际不可达 = 回归。 | **升 P1，本轮内修复**：把「关于（含 WCO 自检）/ 打开日志目录 / 重新加载」迁入设置中心新增「关于 / 诊断」小节，复用既有 `showAbout`/`openLogsDir` 桥接（**零新增 IPC**）。既满足用户「header 不要三条杠」，又保住自检入口。见新增 **§2.4**，及 §3/§5/§6/§7-R2 联动更新。 |
| P2-1 | 一致性 | P2 | 无通用 `Tooltip` 原语，气泡为内联手写。 | 接受：2 项规模 right-sized；若未来 NavRail 项增多再抽取为 `components/ui` 共享原语以免复制。 |
| P2-2 | 完备性 | P2 | 气泡 `opacity` 过渡未加 `prefers-reduced-motion` 守卫。 | 接受：与既有代码风格一致（项目内多处 `transition` 亦未守卫），淡入 150ms 影响极小，不阻塞。 |
| P2-3 | 完备性 | P2 | `border-b`（48px border-box 内侧 1px）改为 header 之下 1px 同层分隔线后，顶部纵向带从 48px 变 49px，内容整体下移 1px。 | 接受：视觉可忽略，且语义更清晰（分隔线落在 48px 叠加矩形之外正是修复目的）；无需处理。 |
| P2-4 | 完备性/可测 | P2（顺手修） | §6.1 新增用例「定位文字气泡元素」措辞含糊，易写出脆弱定位。 | 已在 §6.1 澄清：改后 label 文本仅存在于气泡 span，`aria-label` 是属性、不被 `getByText` 命中，故 `getByText("交易")` 唯一命中气泡 span，与按钮可访问名互不干扰，可稳定断言其 `aria-hidden` 与 `opacity-0`。 |
| P2-5 | 完备性 | P2（顺手修） | R1 仅泛指「被相邻元素遮挡」，未点名内容区左上角图表浮动图例（`indicator-floating-legends`）可能与气泡同处。 | 已在 §7-R1 补注：人工验收须特别检查图表浮动图例区与气泡的层叠，必要时微调 `z`。 |

---

## 1. 概述（Overview）

第 1 轮已把「功能切换」重构为「最左侧常驻竖向 NavRail + 多视图常驻挂载（`hidden` 切换）」的 App Shell 外壳（`apps/web/components/Nav/NavRail.tsx`、`apps/web/app/page.tsx`）。本轮是在该外壳之上的**纯表现层精修**，不触碰视图切换机制、状态保真链路、store 形态与后端/桌面进程逻辑，只调整三处可视细节，使顶部区域更克制、导航更聚焦、标题栏分隔线在 Windows 上连续完整。

第一处针对 NavRail：当前每个导航按钮以「图标在上、`text-[10px]` 标签在下」的方式**常驻并排显示图标与文字**（`NavRail.tsx:97-99`），在窄栏（64px）里文字偏小且「因子实验室」5 字易换行，视觉略显拥挤。本轮改为业界标准的**图标导航栏**：默认仅渲染图标，鼠标 hover 或键盘 focus 时，在按钮右侧弹出一枚气泡（tooltip）显示完整文字。可访问名仍由 `aria-label` 提供，读屏与既有测试（`NavRail.test.tsx` 以可访问名定位按钮）不受影响。

第二处针对 header 品牌簇：移除副标题文字「多策略量化模拟交易 · OKX 真实行情」（`page.tsx:574-576`）与紧随其后的 `AppMenu` 汉堡（三条杠）入口（`page.tsx:578`），让左上角只保留 Logo + 「Aragon Trading」字样，更简洁。`AppMenu` 提供的「关于 / 打开日志目录 / 重新加载」三项会因此失去 header UI 入口（其中「关于」对话框承载 Windows WCO 运行期自检，见 §7 R2）。为避免该自检入口在 Windows 客户端不可达（评审 P1-1），本轮在删 header 汉堡的同时把这三项动作迁入设置中心「关于 / 诊断」小节，复用既有桥接、**零新增 IPC**（§2.4），header 仍被清空、能力不丢。

第三处针对 header 底部那条 1px 横线：当前它是 `<header>` 自身的 `border-b`（`page.tsx:554`）。在 Windows 上，系统以 `titleBarOverlay` 在右上角绘制一块**纯色矩形**（背景色 = `--bg`，高度 `TITLEBAR_OVERLAY_HEIGHT = 48px`，等于 header 高度，见 `desktop/src/main.js:211-222`）来承载最小化/最大化/关闭三键。该 48px 矩形自顶向下覆盖到 y=48，恰好盖住 header 底边那条位于 y=47~48 的 `border-b`，于是右上角三键下方**没有横线**。修法：把这条横线从 header 的 `border-b` 改为一条**置于 header 之下、同层贯穿全宽**的独立分隔线元素（y≥48，落在 48px 叠加矩形之外），即可让横线连续覆盖整宽（含三键下方），且无需改动桌面主进程。

---

## 2. 技术设计（Technical design）

### 2.1 需求一：NavRail 默认仅图标、hover/focus 弹出文字气泡

改动集中在 `apps/web/components/Nav/NavRail.tsx` 的单个导航按钮渲染（`NavRail.tsx:74-100`），不动 `ITEMS` 数据、`select` 选中逻辑、`activeView` 驱动与容器 `<nav>`。

要点：

- **去掉常驻文字**：删除按钮内 `<span className="text-center text-[10px] leading-tight">{item.label}</span>`（`NavRail.tsx:98`）。文字只在 hover/focus 时由 tooltip 呈现。
- **按钮改为居中图标方块**：按钮容器从「纵向堆叠图标+文字」改为「水平居中单图标」：
  - 由 `relative flex flex-col items-center gap-1 rounded-[12px] px-1 py-2.5`
  - 改为 `group relative flex h-11 items-center justify-center rounded-[12px]`。
  - 仍保留 `transition-all duration-150 ease-smooth` 与选中/悬停配色三元（`active ? "bg-[var(--accent-soft)] text-[var(--accent)]" : "text-[var(--muted)] hover:bg-[var(--surface-2)] hover:text-[var(--text)]"`）。
  - 仍保留选中态左侧 `3px` 圆角强调条（`<span aria-hidden absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-full bg-[var(--accent)]>`）；按钮维持全宽（在 `<nav>` 的 `px-2` 内拉伸），强调条紧贴栏左缘，视觉与第 1 轮一致。
  - `h-11`（44px）满足触控/点击目标尺寸（WCAG 2.5.5 / 移动 HIG 44px 基线）。
- **新增右侧悬浮文字气泡（tooltip）**：在按钮内追加一枚视觉气泡，仅 hover/focus 显隐，**不撑开栏宽**（绝对定位、`pointer-events-none`）：

  ```tsx
  <span
    aria-hidden="true"
    className={clsx(
      "pointer-events-none absolute left-full top-1/2 z-50 ml-2 -translate-y-1/2",
      "whitespace-nowrap rounded-md border border-[var(--border)] bg-[var(--surface)]",
      "px-2 py-1 text-xs font-medium text-[var(--text)] shadow-sm",
      "opacity-0 transition-opacity duration-150 ease-smooth",
      "group-hover:opacity-100 group-focus-visible:opacity-100",
    )}
  >
    {item.label}
  </span>
  ```

  - `group` 加在按钮上，气泡用 `group-hover:opacity-100`（鼠标）与 `group-focus-visible:opacity-100`（键盘）双触发，保证键盘 Tab 聚焦时也能看到文字（无障碍关键）。
  - 气泡 `aria-hidden="true"`（纯视觉）：可访问名仍由按钮 `aria-label={item.label}` 提供，避免读屏重复朗读。
  - `left-full ml-2` 让气泡溢出到栏右侧的内容区上方；`z-50` 压住内容；`<nav>` 无 `overflow-hidden`（现状即如此），气泡可正常外溢。
- **移除原生 `title`**：删除按钮上的 `title={item.label}`（`NavRail.tsx:82`），避免与自绘气泡叠加成「双 tooltip」（原生 `title` 有 ~500ms 延迟且不可控样式，体验差）。`aria-label` 保留，可访问名不变。
- **栏宽**：维持 `w-16`（64px）不变，避免触动右侧面板测宽几何（其锚点为内容区右缘，与栏宽无关，但保持不变最稳）。可选项：未来若想更紧凑可收窄至 `w-14`，非本轮必须。

### 2.2 需求二：header 移除副标题与 AppMenu（三条杠）

改动集中在 `apps/web/app/page.tsx` 的 `<header>` 左侧品牌簇（`page.tsx:564-579`）：

- **删副标题**：移除
  `<span className="hidden text-[11px] text-[var(--muted)] sm:inline">多策略量化模拟交易 · OKX 真实行情</span>`（`page.tsx:574-576`）。
- **删汉堡入口**：移除 `<AppMenu />`（`page.tsx:578`）及其顶部 `import AppMenu from "@/components/Desktop/AppMenu";`（`page.tsx:8`，删用后即成未用 import，须一并删除以免类型/lint 报错）。
- **保留拖拽手柄**：品牌簇 `drag-region`（Logo + 「Aragon Trading」）仍是窗口唯一拖拽手柄，删副标题只缩短其宽度、不改其拖拽契约（守 §7 R3 标题栏历史回归）。`AppMenu` 原本就是 `no-drag` 且在 `drag-region` 之外，删除不影响拖拽区。
- **可选收敛**：删 `AppMenu` 后，外层 `<div className="flex items-center gap-2">`（`page.tsx:564`）只剩单个 `drag-region` 子元素，可把这层冗余 wrapper 拍平（直接保留 `drag-region` 那一层）。非必须，开发节点择优；若拍平，注意 `drag-region` 仍须是品牌簇的最外层以维持拖拽。
- **保留 IPC、迁移入口（评审 P1-1）**：`desktop/src/main.js` 的 `aragon:show-about` / `openLogsDir` / WCO 回报等 IPC 接线**原样不动**。但仅卸载 header 入口会让 **Windows WCO 真机自检门**（「关于」对话框承载，项目记忆与 `docs/diagnoses/desktop-packaged-wco-header-right-still-unclickable` 依赖）在 Windows 客户端**不可触发**（已核：Windows 下 `Menu.setApplicationMenu(null)`，无其它入口）。故本轮把三项动作迁入设置中心「关于 / 诊断」小节（§2.4）保住能力；`page.tsx` 中 `import AppMenu` 必删（删用后即未用 import），`AppMenu.tsx` 组件文件不再被引用，留删皆可（删之更干净，其桥接调用已由 §2.4 的 `AboutSection` 承接）。详见 §7 R2。

### 2.3 需求三：header 底部横线贯穿全宽（含 Windows 三键下方）

根因（已核对 `desktop/src/main.js:211-222` 与 `apps/web/app/globals.css:149-155`）：Windows 走系统托管标题栏 + `titleBarOverlay`，系统在右上角绘制一块 `color = --bg`、`height = 48px`（`TITLEBAR_OVERLAY_HEIGHT`，注释明示「高度等于顶栏 h-12=48px」）的纯色矩形承载三键。该矩形自 y=0 覆盖到 y=48，恰好盖住 header 自身 `border-b`（位于 48px 盒模型底部的 y=47~48 那 1px），故三键下方那段横线被叠加矩形的纯色背景吞没。`win-titlebar-safe` 只处理水平安全区 padding，不影响此纵向覆盖。

修法（**纯渲染层、不改桌面主进程**）：把横线从「header 的 `border-b`」迁移为「header 之下、同层全宽的独立 1px 分隔线」：

- **header 去边框**：从 `<header>` 的 `clsx(...)` 中删除 `"border-b border-[var(--border)]"` 这一段（`page.tsx:554`）。
- **新增全宽分隔线**：在 `</header>` 之后、`<div className="flex min-h-0 flex-1">`（`page.tsx:594`）之前插入：

  ```tsx
  <div aria-hidden="true" className="h-px w-full shrink-0 bg-[var(--border)]" />
  ```

  结构变为：

  ```
  <div class="flex h-screen flex-col bg-[var(--bg)]">
    <header class="flex h-12 ...">           ← 48px，去掉 border-b
    <div aria-hidden class="h-px w-full ...">← 新增：贯穿全宽分隔线（y≈48）
    <div class="flex min-h-0 flex-1">        ← NavRail + 内容区
      …
  ```

- **为何有效**：`titleBarOverlay` 矩形高度 = header 高度 = 48px，自 y=0 覆盖到 y=48；新分隔线作为 header 之后的同层流式元素落在 y=48（48px header 之下），其绘制带 [48,49) **严格位于叠加矩形 [0,48) 之外**，不被纯色覆盖，因此横线在全窗宽连续，包括最小化/最大化/关闭三键的正下方。叠加矩形高度与 header 高度同源于 48（DIP），在 100%/125%/150% 等缩放下两者底边对齐同一设备像素行，分隔线始终在其下一行，跨 DPI 稳健。
- **Web / mac / linux 零回归**：无 `titleBarOverlay`，新分隔线只是把原 `border-b` 这条横线平移到了相同 y 位置，视觉等价。
- **防御性兜底（仅作备选，非本轮必做）**：若真机 QA 在某些分数 DPI 上仍见三键下方 1px 缝隙，可将 `desktop/src/main.js` 的 `TITLEBAR_OVERLAY_HEIGHT` 由 48 调为 47（单行、附注释），使叠加矩形比 header 矮 1px，露出分隔线；此为可选加固，渲染层主方案已自洽。

### 2.4 需求二配套：AppMenu 三项动作迁入设置中心「关于 / 诊断」（评审 P1-1，本轮必做）

删 `AppMenu` 会让 `aragon:show-about` 在 Windows 客户端失去唯一触发点，连带 **WCO 运行期自检**（`main.js` `showAbout`）不可达（见「评审记录」P1-1）。本轮在删除 header 汉堡的同时，把其三项动作下沉到设置中心，保住能力且不污染 header：

- **新增导航项**：在 `apps/web/components/Settings/SettingsNav.tsx` 的 `SECTIONS` 注册表末位追加
  `{ key: "about", label: "关于 / 诊断", desc: "版本信息与日志目录" }`。`settingsSection` 为自由字符串、`setSettingsSection` 直接透传，**无需改 `store.ts` 形态**（见 §5）。
- **新增小节组件**：`apps/web/components/Settings/AboutSection.tsx`，桌面端（`useIsDesktop()` 守卫）渲染三枚动作按钮，**复用 `AppMenu` 原有桥接、零新增 IPC**：
  - 「关于 Aragon Trading」→ `getBridge()?.showAbout?.()`（触发承载 WCO 自检的「关于」对话框）。
  - 「打开日志目录」→ `void getBridge()?.openLogsDir?.()`。
  - 「重新加载」→ `location.reload()`。
  非桌面端（Web）：该小节渲染一行「仅桌面客户端可用」占位，或不在 `SettingsNav` 注册（择一，开发节点按既有桌面分流惯例 `useIsDesktop()`/`getDesktopPlatform()` 取舍，与 `AppMenu` 原 `useIsDesktop()` 守卫保持一致语义）。
- **接入外壳**：在 `apps/web/components/Settings/SettingsPanel.tsx` 的 section 三元分支中增加 `section === "about" ? <AboutSection /> : …`（保持既有「默认落 AiModelsSection」结构）。
- **样式一致**：动作按钮复用设置区既有 token 与圆角（镜像 `SettingsNav` / `Button`），不引新视觉语言。

> 该迁移是评审对用户「三条杠不需要了」的忠实落实：header 仍被清空，能力改由设置中心承载，且把 WCO 自检从「汉堡深处」提升为设置内可检索的「诊断」项，对真机人工门更友好。

---

## 3. 文件 / 模块变更计划（File / module change plan）

| 文件 | 动作 | 一句话意图 |
|---|---|---|
| `apps/web/components/Nav/NavRail.tsx` | 修改 | 按钮改「默认仅图标、hover/focus 右侧弹出文字气泡」：删常驻文字 span、改居中图标方块（`group` + `h-11`）、加 `aria-hidden` tooltip、删原生 `title`、保留 `aria-label` 与选中强调条。 |
| `apps/web/components/Nav/NavRail.test.tsx` | 修改 | 既有「按可访问名定位两项 / 选中 / 切视图」用例保持通过（名来自 `aria-label`）；新增断言：文字气泡为 `aria-hidden` 且默认带 `opacity-0`（锁定「图标 only 默认、文字仅悬浮」契约）。 |
| `apps/web/app/page.tsx` | 修改 | header 删副标题 span 与 `<AppMenu/>`（含其 `import`）；从 `<header>` 移除 `border-b`；在 header 与内容区之间插入 `h-px w-full bg-[var(--border)]` 全宽分隔线；（可选）拍平因删 `AppMenu` 而冗余的外层 wrapper。 |
| `apps/web/components/Settings/SettingsNav.tsx` | 修改 | `SECTIONS` 注册表追加「关于 / 诊断」导航项（评审 P1-1）。 |
| `apps/web/components/Settings/SettingsPanel.tsx` | 修改 | section 分支接入 `<AboutSection/>`（评审 P1-1）。 |
| `apps/web/components/Settings/AboutSection.tsx` | 新增 | 桌面端「关于（WCO 自检）/ 打开日志 / 重载」三动作，复用既有 `showAbout`/`openLogsDir` 桥接、**零新增 IPC**（评审 P1-1）。 |

> 不新增第三方依赖；不改后端（`services/quant`）、不改桌面主进程（`desktop/`）与任何 IPC（`AppMenu.tsx` 的桥接经 §2.4 由 `AboutSection` 复用）。**store 形态不变**：仅复用既有 `settingsSection` 自由字符串字段承载新 section key「about」，无 schema/持久字段增删（见 §5）。`desktop/src/main.js` 仅在 §2.3 防御性兜底被采纳时才动（默认不动）。

---

## 4. 接口设计（Interface design）

本轮为纯前端表现层精修，**不涉及任何 REST / WebSocket / CLI / store 动作签名变更**。唯一需固化的是 NavRail 单按钮的 DOM/可访问性契约：

```tsx
// apps/web/components/Nav/NavRail.tsx —— 单个导航项渲染契约
<button
  type="button"
  id={`nav-rail-${item.key}`}
  onClick={() => select(item.key)}
  aria-current={active ? "page" : undefined}
  aria-label={item.label}            // 唯一可访问名来源（图标 only 后仍稳定）
  className="group relative flex h-11 items-center justify-center rounded-[12px] transition-all duration-150 ease-smooth ..."
>
  {active && <span aria-hidden className="absolute left-0 ... bg-[var(--accent)]" />}  {/* 选中强调条 */}
  {item.icon}                                                                          {/* 20×20 内联 SVG，aria-hidden */}
  <span aria-hidden className="pointer-events-none absolute left-full ... opacity-0 group-hover:opacity-100 group-focus-visible:opacity-100">
    {item.label}                                                                       {/* hover/focus 文字气泡 */}
  </span>
</button>
```

契约要点：① 可访问名恒来自 `aria-label`；② 图标与气泡均 `aria-hidden`，不参与名计算、不重复朗读；③ 气泡默认 `opacity-0`，仅 `group-hover` / `group-focus-visible` 显现；④ 不再使用原生 `title`。`select`、`activeView`、`showSettings` 等既有契约保持不变。

---

## 5. 数据模型（Data model）

无任何数据库 / 后端 schema 变更，亦无前端持久化（localStorage `aragon-ui`）字段增删——本轮不触碰 `store.ts`，`activeView` 等既有持久字段与 `version` 保持原状。纯 DOM/CSS 表现层调整，无内存态形态变化。

> 评审 P1-1 在 `SettingsNav` 增「about」section：`settingsSection` 本就是自由字符串字段、`setSettingsSection` 直接透传，新增取值**不构成 schema 或持久字段形态变更**，故本节「不触碰 `store.ts`」结论维持成立。

---

## 6. 测试与验收标准（Testing & acceptance criteria）

### 6.1 自动化测试（`apps/web`，组件测试须置 `NODE_ENV=test` 运行，见既有约定）

- `NavRail.test.tsx`（沿用 + 增补）：
  - 既有四条用例保持绿：按可访问名「交易」「因子实验室」定位两按钮、默认 `aria-current="page"` 在「交易」、点「因子实验室」切 `activeView`、设置浮层打开时点项关浮层并切视图——这些断言均依赖 `aria-label` 提供的可访问名，删除常驻文字与 `title` 后**仍成立**（验证「图标 only 不破坏可访问名」）。
  - 新增一条：渲染后取某一项的文字气泡元素，断言其 `aria-hidden="true"` 且 className 含 `opacity-0`（即默认不可见、仅 hover/focus 显现）。**定位建议**（评审 P2-4）：用 `screen.getByText("交易")`——改后 label 文本仅存在于气泡 span，而按钮可访问名来自 `aria-label`（属性，不被 `getByText` 命中），二者不冲突，故该定位唯一且稳定。说明：jsdom 不计算 `:hover`/`:focus-visible` 的实际可见性，故不断言「hover 后可见」，改以「默认隐藏 + 触发类存在」锁定契约。
  - 回归：全量 `pnpm --filter web test` 通过；`pnpm --filter web build`（或 `tsc --noEmit`）零类型错误（借此验证 `page.tsx` 删 `AppMenu` 后无悬挂 import、NavRail 改动类型正确）。
- 无新增 page 级测试：`page.tsx` 无既有单测，header 文字/菜单删除与分隔线为结构改动；其中 Windows `titleBarOverlay` 覆盖行为属 Electron/真机范畴，转人工验收（§6.2）。

### 6.2 人工 / 验收

1. **NavRail 图标化**：默认两项仅见图标、无并排文字；鼠标移到任一项，其右侧弹出含完整文字（「交易」/「因子实验室」）的气泡，移开即隐；明暗主题下气泡配色（`--surface`/`--border`/`--text`）协调、不被内容遮挡。
2. **键盘可达**：用 Tab 聚焦导航项时，同样弹出文字气泡（`group-focus-visible`）；回车/空格仍能切换视图；读屏朗读各项可访问名（来自 `aria-label`）与当前项（`aria-current`）。
3. **header 精简**：左上角只剩 Logo + 「Aragon Trading」，**无副标题文字、无三条杠汉堡图标**；账户模式/主题/设置齿轮/窗口控件位置与可点击性零回归。
4. **窗口拖拽零回归**：拖拽品牌簇仍可移动窗口；桌面端（mac 红绿灯 / Windows WCO 三键）安全区与最大化/还原行为不变（守历史标题栏回归）。
5. **横线贯穿全宽（重点，真机 Windows 还原态）**：header 底部 1px 横线在全窗宽连续，**包括最小化/最大化/关闭三键正下方**不再缺线；明暗主题、还原态与最大化态均连续。Web / mac / linux 下横线位置与样式与改动前一致。
6. 「关于 / 打开日志 / 重新加载」入口已从 header 消失（符合需求），并按评审 P1-1 迁入设置中心「关于 / 诊断」小节：在桌面客户端打开设置 →「关于 / 诊断」，可见三动作；点「关于」弹出承载 **WCO 运行期自检**（对话框含「WCO active：…」一行）的对话框，验证删汉堡后自检入口**仍可达**（真机 Windows 重点）；Web 端该小节按 §2.4 取舍渲染（占位或不注册）。

---

## 7. 风险与缓解（Risks & mitigations）

| # | 风险 | 缓解 |
|---|---|---|
| R1 | hover 气泡 `left-full` 外溢到内容区，可能被相邻元素或 `z` 较高层遮挡，或在内容区裁剪容器下被截断。 | 气泡 `z-50` + `pointer-events-none`；`<nav>` 现状无 `overflow-hidden`，气泡可正常外溢。验收第 1 条目检无遮挡/截断；**人工验收须特别检查内容区左上角图表浮动图例（`indicator-floating-legends`）与气泡的层叠，必要时微调 `z`（评审 P2-5）**；若个别主题下对比度不足，微调 `bg/border` token 即可（单点）。 |
| R2 | 删 `AppMenu` 后「关于 / 打开日志 / 重新加载」失去 UI 入口；其中「关于」承载 **Windows WCO 真机自检门**（项目记忆与 `desktop-packaged-wco-header-right-still-unclickable` 依赖），入口缺失会让 WCO 验证**不可触发**（已核：Windows 下 `Menu.setApplicationMenu(null)`，`AppMenu` 是唯一入口）。 | **评审裁定纳入本轮（P1-1，已解）**：三项动作迁入设置中心「关于 / 诊断」小节，复用既有 `showAbout`/`openLogsDir` 桥接、**零新增 IPC**（§2.4）。WCO 自检入口保住且更易检索；`desktop/` 主进程与全部 IPC 不动。 |
| R3 | 误改 header 触发桌面端标题栏右簇被吞为 caption 的历史回归（`desktop/diagnoses/desktop-titlebar-*`）。 | 仅删左侧品牌簇内一个 `no-drag` 子（`AppMenu`）与一段文字、删 `border-b`、在 header 之下插入分隔线；`drag-region`/`win-titlebar-safe`/mac 安全区与右簇结构**完全不动**；分隔线在 header 之外，不进入任何拖拽/命中区。 |
| R4 | 分隔线落在 `titleBarOverlay` 矩形底边附近，分数 DPI 下若叠加矩形向下取整多覆盖 1px，仍可能盖住分隔线。 | 分隔线是 header 之后的**独立同层元素**（y≈48），叠加矩形高度同为 48 且自 y=0 起算，二者底边同源对齐、分隔线始终在其下一行；理论已稳健。兜底：§2.3 末「`TITLEBAR_OVERLAY_HEIGHT` 48→47」单行加固（默认不启用）。 |
| R5 | 既有 `NavRail.test.tsx` 用 `getByRole("button",{name})` 定位——若实现误删 `aria-label` 只留 `aria-hidden` 气泡，可访问名将丢失，测试与读屏双双失效。 | §2.1/§4 明确「`aria-label` 为唯一可访问名来源，必须保留；气泡 `aria-hidden`」；`pnpm --filter web test` 与既有四条用例兜底。 |
| R6 | 删副标题后 `<header>` 左簇外层 wrapper 冗余、或 `clsx` 中误删过多类。 | §2.2 标注「可选拍平」为非必须，按最小改动删两段+`border-b`+`import` 即可；保留 `flex h-12 shrink-0 items-center` 与平台分流 padding 三元不动。 |

---

## 8. 实施过程发现的方案缺陷（由下游开发节点回填）

（留空，开发节点如发现方案偏差请在此记录。）

---

## 评审结论（Review Verdict · Subtask #1）

**结论：有条件通过（Approved with conditions）。**

方案整体可行、与代码库现状及历史诊断高度吻合、尺度克制（以纯表现层精修为主）。三项用户需求的技术路径——NavRail 图标化 + hover/focus 文字气泡、header 删副标题与汉堡、底部横线下沉至 `titleBarOverlay` 48px 叠加矩形之外——均成立，且方案引用的真实行号与 token 已逐一核对属实（见「评审记录·可行性」）。**P0：无。**

**放行条件（均已在 v2 正文落实；开发节点须照此实现，不得退化为「仅卸载、不迁移」）：**

1. **【P1-1，必做】WCO 自检入口不得丢失**：删 header 汉堡的同时，把「关于（含 WCO 运行期自检）/ 打开日志目录 / 重新加载」迁入设置中心「关于 / 诊断」小节（§2.4），复用既有 `showAbout`/`openLogsDir` 桥接、**零新增 IPC**。真机 Windows 验收须确认「关于」对话框中的「WCO active：…」可被触发查看（§6.2-6）。
2. **【护栏，必守】可访问名唯一来源为 `aria-label`**：气泡 `aria-hidden`、不再用原生 `title`；`NavRail.test.tsx` 既有四条用例须保持绿，新增气泡契约用例按 §6.1 澄清的定位写（评审 P2-4）。
3. **【护栏，必守】标题栏零回归**：`drag-region` / `win-titlebar-safe` / mac 安全区 / 右簇结构完全不动；分隔线作为 header 之外的同层元素，不进入任何拖拽 / 命中区（§7 R3/R4）。

P2 项（无通用 `Tooltip` 原语、`prefers-reduced-motion`、顶栏纵向带 +1px 位移）记为**可接受、不阻塞本轮**；其中 P2-4 / P2-5 的低成本澄清已顺手并入正文。满足上述条件即视为通过，可进入开发节点实施。
