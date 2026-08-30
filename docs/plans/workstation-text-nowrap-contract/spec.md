# 交易工作台「文字零折行」排版契约 开发方案 spec.md

> Feature: `workstation-text-nowrap-contract` · 版本 **v2**（v1 = 方案设计节点 Subtask #0 产出；v2 = 评审节点 Subtask #1 按本文《评审记录》逐条修订）
> **行号口径（v2 新增）**：本文中的 `文件.tsx:NN` 是撰写时快照，实测存在 1~4 行漂移（见评审记录 P2-1）。**实施一律以被引用的锚点文本为准，不得按行号盲改。**
> 关联需求（原文）：
> 「这个项目整体的用户交互界面……对应于交易功能，其左侧的这样的一个标的展示面板以及中间的 K 线面板以及对应的这样的一个成交列面板。就是当用户去调整整个软件的这个宽度，或者是调整左侧的这些各个容器的这样的一个宽度的时候，其内部可能会存在它显示的文字的换行，自动换行的这样的一个情况，然后导致它显示的效果极其不美观，然后用户体验不好，需要进行修复……最近有类似的修复，是在 AI 自动交易的这样的一个 AI 助手面板去进行修复的，看起来是比较好的解决了这样的一个问题。需要整个系统性的排查整个软件的对应于这样的用户操作界面的这种文字自动换行的这种情况，并且进行修复完善。」
> 参照先例：`docs/plans/agent-panel-toolbar-two-rows/spec.md`（v2，已实现并合入 `5dfbe62`）。本轮把那一次**只在右侧 AI 助手面板生效的局部修法**，提炼成一条**可复用、可冻结、可扩散**的仓库级排版契约，并按契约系统性修完交易工作台的左 / 中 / 右三块。

---

## 评审记录（Review Notes）

> 评审节点 Subtask #1 · 评审对象 v1 · 结论见文末《评审结论》。
> **评审方法**：逐节把 §2–§11 的每一条事实断言拿到 `apps/web` 源码里实测——行号、类名、导出符号、
> 既有用例条数、滚动宿主、`colSpan` 落点、`Tabs` 调用点、`store` 持久化键与版本、Tailwind 版本与插件表
> 全部逐条核对过。**下表 P0 / P1 已在 v2 正文改完**（「处置」列写明改在哪一节）；P2 除注明「已顺手修正」者外
> 保留为记录，不改正文。
>
> **先说结论中最要紧的一句**：v1 的方向、根因分析与四类行契约本身是**成立且右尺寸的**——
> 那份 24 个受管文件的清单由三路交叉得出（v2 补到 25），八个滚动宿主逐个核实过，`Tabs` 的 13 个调用点与
> `store` 的 `version: 30`、Tailwind `^3.4.13` + `plugins: []` 全部实测无误，这在本仓库的方案文档里属上乘。
> 问题集中在**同一个形状**：**「有断言点／有门禁，却没有对应的写入点」**——四条 P0 里有三条是它，
> 这也正是本仓库共享看板反复登记的那个缺陷形状。

| 编号 | 级别 | 节 | 问题（附实测证据） | 处置 |
|---|---|---|---|---|
| **P0-1** | P0 | §4.5.1 / §6 / §9.1 T8 | **`data-testid="market-depth-panel"` 有断言点、无写入点。** §6 的 DOM 契约表与 T8 都以 `getByTestId("market-depth-panel")` 定位，但 §4.5.1 的改动清单从头到尾没要求给根节点加这个属性。实测 `MarketDepthPanel.tsx` 当前 `data-testid` 出现次数为 **0**（`ChartToolbar` / `ChartHeaderBar` / `PositionPanel` / `AutoTradePanel` 同为 0，但那四处 v1 在正文代码里写了 testid，唯独这一处漏了）⇒ **T8 必红**。 | 已修：§4.5.1 补写 `data-testid="market-depth-panel"`；§6 标注它是**新增**标识而非既有标识 |
| **P0-2** | P0 | §4.4.2 / §9.1 T7 | **`shrink-0` 被要求加在「不是 flex item」的元素上，且 T7 的遍历断言不可能通过。** v1 写「`IndicatorMenu` 触发钮、`ConnectionStatus` 触发钮逐个补 `shrink-0`」。实测两者的**根节点**分别是 `IndicatorMenu.tsx:58` 的 `<div ref={rootRef} className="relative">` 与 `ConnectionStatus.tsx:104` 的 `<div ref={wrapRef} className="relative">`——工具栏行的 flex item 是这两个根 `<div>`，把 `shrink-0` 写在它们**内部**的按钮上对收缩计算完全无效；而这两个文件**不在 §5 的受管清单里**，直接改它们又会破坏 G3 / G5 的边界。同时 T7 断言「`chart-toolbar` 的每一个直接子元素都含 `shrink-0`」——`<IndicatorMenu />` 渲染出的那个直接子元素永远拿不到这个类名 ⇒ **T7 必红**。 | 已修：§4.4.2 改为在 `ChartToolbar.tsx` 内**就地包一层** `<div className="shrink-0">`（零跨文件、零 a11y、受管清单不变）；§9.1 T7 的断言口径同步改写 |
| **P0-3** | P0 | §4.5.2 / §9.2 G4 | **`<td colSpan>` 落点漏记一处，导致门禁与改动清单自相矛盾。** 实测 9 张受管表里 `colSpan` 共 **4 处**：`TradesTable.tsx:139`（v1 写 `:137`）、**`PaperPositionsTable.tsx:74`（v1 记为「—（无）」，实为 `<td colSpan={5}>当前无持仓</td>`）**、`LiveFillsTable.tsx:146`（v1 写「需现场确认行号」）、`LivePositionsTable.tsx:237` ✓。G4 是**全量规则**（「凡出现 `colSpan={` 的 `<td` 必须含 `whitespace-normal`」），而 §4.5.2 只让实现者改 3 处 ⇒ **G4 恒红**。 | 已修：§4.5.2 表格补齐第 4 处并写死四个确切行号；G4 改为「逐一断言 §4.5.2 点名的四处」，并写明只匹配 `<td` 开标签（`LiveFillsTable.tsx:13` 的注释里也含 `colSpan` 字样，行匹配会误命中——顺带修掉 P2-3） |
| **P0-4** | P0 | §9.2 G3 / §9.4 DoD | **锚点数量的 DoD 与正文清点结果冲突，且 G3 的计数表以「…」省略，门禁无法实现。** 把 §4.3–§4.5 显式要求的 `data-nowrap` 逐条点清：InstrumentSidebar 2 / InstrumentRow 1 / WatchOtherPanel 1 / AutoTradePanel 3 / AutoTradeMemberRow 1 / AiTradePanel 1 / ChartToolbar 1 / ChartHeaderBar 1 / ChartWorkspace 1 / MarketDepthPanel 1 / MetricsCards 1 / PositionPanel 1 / LiveBottomPanel 1 / LiveTradePanel 2 = **18**，而 DoD 写 `≥ 20`——实现者只能靠**凭空编两个锚点**来满足它；而 K3 已经承认 G2 挡不住删属性、G3 的计数才是真防线，一条以「…」收尾的规格等于没有防线。 | 已修：G3 写成**完整的 15 行计数表**（v2 因 P1-7 新增一个受管文件，合计 **19**）；DoD 改为等值断言 `=== 19` 而不是下界 |
| **P1-1** | P1 | §4.6 / §9.3 R4 | **轴 E 引入「侧栏拖不宽」的死区，而 R4 覆盖不到它。** E-1 去掉左侧栏 `shrink-0` 后，过约束档位下 `渲染宽 < store 存值`；`onResizeSidebar` 写回的是 `widthFromLeftEdge(topRef.rect.left, clientX)`（实测 `ChartWorkspace.tsx:157`，确为指针绝对定位）。用户在压缩态**向右**拖时，新存值仍小于旧存值 ⇒ 布局零变化 ⇒ **手柄不跟指针**，直到指针越过旧存值才恢复。§4.6 只论证了「无累积漂移」，没论证「跟手」；而 R4 规定的 200↔460 连续拖动是在 1440 默认窗口下做的，那个档位**根本不过约束**，验不到这条。 | 已修：§4.6「已知副作用」重写为死区分析并判定为**预期行为**（放不下就不能再拖宽）；§9.3 新增 **R14** 专门在过约束档位验证「向右拖无效但不抖动 / 向左拖立即跟手 / 放大窗口后回弹到存值」 |
| **P1-2** | P1 | §4.1 / §4.5.1 / §4.5.3 / §4.5.4 / §6 | **`data-nowrap="row"` 被用在三个「不是行」的容器上，契约自相矛盾。** `MarketDepthPanel` 根实测是 `flex ... flex-col`（`:19`）、`PositionPanel` 是 grid（`:31`）、`MetricsCards` 是 `grid grid-cols-2`（`:35`）——三处都被要求写 `data-nowrap="row"` 并宣称「套 Type A」。而 Type A 的定义是「内容必须留在一行的单行栏 + 行内恰好一处 `truncate`」，与这三处的真实语义（**继承子树根**）不符；后续维护者照着三条铁律去找「唯一 truncate」会找不到，G2 / G3 的语义也跟着含混。 | 已修：§4.1 给锚点定义**第二个取值** `data-nowrap="scope"`（继承子树根），三处改用它；G2 改为匹配 `data-nowrap="(row\|scope)"`；§6 DOM 契约表补一行 |
| **P1-3** | P1 | §9.2 G2 | **G2 的「同一开标签内配对」判据用朴素正则实现会被 `=>` 打断。** JSX 开标签里常有箭头函数（如 `onChange={(next) => …}`），`<[^>]*>` 会在 `=>` 的 `>` 处提前截断，带事件处理器的受管行会**假红**。既有门禁 `agentToolbarNoWrap.source.test.ts` 用的 `/<header[^>]*>/` 只是恰好没踩到（那个开标签没有箭头函数），不能照抄。 | 已修：§9.2 给出确定性切片规则——以每处 `data-nowrap=` 为锚，**向前取最近的 `<`、向后取下一个 `<`**，两者之间即开标签体（JSX 开标签内不会再出现 `<`），在该切片内断言 `whitespace-nowrap` |
| **P1-4** | P1 | §4.5.4 / §9.1 T9 | **`PositionPanel` 的「值」是 `children`，`Cell` 内部加不了 `truncate`。** 实测 `Cell({ label, children })`（`:56-63`），值由 6 个调用点以 `<span>` / `<Badge>` 传入；v1 写「`Cell` 的 `<span>` 标签与值各补 `truncate`」在 `Cell` 里无法落地——它自己只有 label 那一个 `<span>`。 | 已修：§4.5.4 明确「`Cell` 根补 `min-w-0`、label `<span>` 补 `truncate`、`children` 由 `Cell` 用 `<span className="block min-w-0 truncate">` 包一层（6 个调用点零改动）」；T9 补上对应断言 |
| **P1-5** | P1 | §5 排除表 | **实盘下单栏的四个子组件既不在受管清单、也不在排除表。** `Live/DirectionToggle.tsx`、`LeverageControl.tsx`、`QuantitySlider.tsx`、`TpSlFields.tsx` 全部渲染在 `LiveBottomPanel` 左栏（实测 `w-2/5 min-w-[300px] max-w-[460px]`，宽度随 `rightPanelWidth` 变化），且都是 `justify-between` 的标签／数值行（`LeverageControl.tsx:28`、`QuantitySlider.tsx:54`、`TpSlFields.tsx:42`）。它们属于 G2 承诺的「下方成交与持仓区」，K8 说的「改了 24 个漏了第 25 个」正是这一处——**不写进任何一张表，等于既没修也没解释**。 | 已修：§5 排除表补一行，给出最窄档（860 窗口 / 右栏 320 ⇒ 左栏 300px，扣 `p-3` 后约 276px）的自然宽估算与排除理由；§9.3 R7 增加一条目视确认，实测折行则回流到下一轮 |
| **P1-6** | P1 | §4.3.1 | **对 `InstrumentSidebar` 三处 Type D 的继承判断是错的，且与 §4.3.6 的判据自相矛盾。** 实测头部容器是 `:193-202`；错误块 `:213`、空态 `:243`、截断提示 `:264` **全部**落在 `:204` 的兄弟容器里，**没有一处**在头部的 nowrap 继承链上——v1 却写「加了头部 nowrap 后必须显式声明」，还特意为 `:264` 一处做了例外说明。而 §4.3.6 对 `AiTradePanel` 的**同型结构**给出的结论是「不构成继承 ⇒ 零改动」。同一份文档对同一种结构给出两种处置，实现者无法判断信哪条。 | 已修：§4.3.1 统一表述为「三处均不在继承链内 ⇒ 本可零改动；仍显式写 `whitespace-normal break-words` 是**契约自证**，防止后人把 nowrap 提到 `:192` 根节点」，并在 §4.1 Type D 里写明这条「防御性显式声明」的适用条件 |
| **P1-7** | P1 | §2.2 根因 4 / §4.5 / §5 | **根因 4 在受管区域里有第二个落点，v1 只修了一个。** 实测 `Strategy/TradeExplainDetail.tsx:67` 的 `MetricsGrid` 写的是 `grid grid-cols-2 gap-x-4 gap-y-1.5 sm:grid-cols-3`——它渲染在 `TradesTable` 展开行的 `<td colSpan>` 里，宽度由 `窗口宽 − 49 − 6 − rightPanelWidth` 决定，与视口宽同样毫无关系。在 860 窗口 + 右栏 640 档下下区只有 165px，而 `sm:`（视口 ≥640px）**恒为真** ⇒ 每格 55px ⇒ 必然折行。它既不在受管清单也不在排除表，而 **R9 恰好就是走这条路的验收**——评审时一眼就会撞见。 | 已修：作为受管文件 **#25** 纳入（新增 §4.5.6，一行改动 + 一处 `min-w-0`）；§5 表格与全文计数 24 → 25，G3 计数表 18 → 19 |
| P2-1 | P2 | 全文 | **行号系统性漂移 1~4 行。** 抽样实测：`ui/index.tsx` 的 `Tabs` 两个变体 v1 写 `:481` / `:505`，实测 `:482` / `:508`；`AiTradePanel` 分组头 v1 写 `:96`，实测 `:92`；`MarketDepthPanel` 根 v1 写 `:18`，实测 `:19`；`PositionPanel` grid v1 写 `:32`，实测 `:31`；`TradesTable` 展开行 v1 写 `:137`，实测 `:139`。（另一方面 `WatchOtherPanel` / `LiveTradePanel` / `InstrumentSidebar` / `ChartHeaderBar` / `StrategyPanel` 的行号**完全准确**，说明只是快照差异而非臆造。） | 已顺手修正：文首加「行号口径」声明；被 P0/P1 改到的段落顺带订正为实测行号 |
| P2-2 | P2 | §4.4.1 / §9.4 | **`Tabs.test.tsx` 既有用例是 17 条不是 18 条。** 实测 `describe.each(["segmented","underline"])` 内 **8** 条 `it` + 外层独立 `describe` 1 条 = 8×2+1 = **17**。 | 已顺手修正：§4.4.1 与 DoD 改为 17（加 T15 后 19） |
| P2-3 | P2 | §9.2 G4 | `LiveFillsTable.tsx:13` 的**注释**里含 `colSpan` 字样，G4 若按行匹配会误命中。 | 已顺手修正（并入 P0-3 的 G4 改写） |
| P2-4 | P2 | §4.5.5 | `SegToggle` 根实测已有 `flex-1`（`LiveTradePanel.tsx:365`），`flex-basis` 为 0 ⇒ 收缩权重本就是 0，再补 `shrink-0` 是**空操作**（Tailwind 的 `flex-shrink` 层序在 `flex` 之后，类名生效但无实际效果）。保留无害，但不应让实现者以为它解决了什么。 | 已顺手标注为「仅为契约一致性，非功能性」 |
| P2-5 | P2 | §10 | `LivePositionsTable` 的「一键市价全平」按钮行与 `<table>` 同在一个 `flex flex-col gap-2` 里（`:100`）。表拿到 `min-w-[640px]` 后横向滚动时，**按钮行不会跟着滚**（它停在容器宽）。视觉可接受，但验收时容易被当成缺陷。 | 记录：已作为 K11 写进 §10 风险表，R7 判据里点名 |
| P2-6 | P2 | §4.5.2 | `LiveFillsTable.tsx:70` 有一条 `<caption>`（19 字说明），它在 `<table>` 内、`<thead>` 外，不受本轮 nowrap 影响，但按 §4.1 属 Type D，未被归类。 | 已顺手在 §4.5.2 标注「保持换行、零改动」 |

**复核并确认无误的关键断言**（评审已实测，实现时可直接信赖，不必重新考证）：

| 断言 | 实测结果 |
|---|---|
| 全仓 `whitespace-nowrap` 非测试用法为 9 行（含 1 行注释） | ✓ 实测 9 |
| 全仓 `data-nowrap` 出现 0 次（是全新锚点） | ✓ 实测 0 |
| `Tabs` 非测试调用点 13 个 | ✓ 实测 13，且 §4.4.1 逐点列出的组件与实测文件集**逐一对上** |
| `Tabs.test.tsx` 既有 9 条断言全是 `role` / `aria-selected` / `tabIndex` / 键盘 / 点击语义，无 `toHaveClass` | ✓ |
| `WatchlistTab.test.tsx` 9 条无任何 `querySelector` / `parentElement` / `closest` / `toHaveClass` 结构断言（K6 的前提） | ✓ 实测为 0 命中 |
| 八个横向滚动宿主全部已是 `overflow-auto` / `overflow-x-auto`，唯 `ComboResult` 缺宿主 | ✓ 逐个核实：`ChartWorkspace.tsx:382` / `LiveBottomPanel.tsx:51` / `TradesTable.tsx:38` / `PaperPositionsTable.tsx:24` / `StrategyPanel.tsx:496` / `RunHistory.tsx:67` / `WalkForwardResults.tsx:111` / `CompareView.tsx:162` |
| `LiveFillsTable` 导出 `FillsTableView`（`:58`）、`LiveOpenOrdersTable` 导出 `OpenOrdersTableView`（`:76`），可裸渲染 | ✓ 行号也准确 |
| `store` 持久化键 `"aragon-ui"`、`version: 30` | ✓ `lib/store.ts:1285-1286` |
| `tailwindcss ^3.4.13`、`tailwind.config.ts` 的 `plugins: []` | ✓（N2 的否决理由成立） |
| `scripts/dev.py test` 存在（DoD 引用） | ✓ `cmd_test` @ `:225` |
| §4.3.6「`AiTradePanel` 的横幅 / 红条是分组头的兄弟节点 ⇒ 零改动」 | ✓ 实测分组头 `:92`，`PausedBanner` 等七块均为其兄弟；且它们本体都是 `leading-relaxed` 的整句 `<p>`，本就该换行 |
| §4.4.3「去掉 `w-[208px]` 后 segmented 页签按内容宽自适应」 | ✓ 两个标签为闭集（「K线价格走势」/「净值」），去掉写死宽后 flex 容器按 max-content 分配，实算约 204px，与 208 基本等价且**不再有溢出临界** |
| §2.3 表 C 的下区宽换算（1440 默认 ⇒ 右栏内容 578px） | ✓ 与 `LiveBottomPanel` 的 `w-2/5 min-w-[300px] max-w-[460px]` + `px-3` 实测结构一致 |

---

## 1. 概述（Overview）

Aragon Trading 的交易工作台是一块**四列可拖拽**的工作区（`apps/web/app/page.tsx:653-709` + `apps/web/components/Chart/ChartWorkspace.tsx`）：最左 `NavRail`（固定 48px）、左侧「标的展示面板」（`InstrumentSidebar`，像素宽，夹取 `[200,460]`，默认 240）、中间「K 线面板」（`flex-1`，被动吸收剩余宽度）、K 线右侧的「盘口 / 成交列面板」（`MarketDepthPanel`，比例宽，夹取 `[0.10,0.30]` 且 CSS 兜底 `min-w-[160px] max-w-[420px]`），以及最右侧的「AI 助手 / 交易策略」面板（`rightPanelWidth`，夹取 `[320,640]`，默认 380）。K 线面板底下还有一条上下可拖的「成交记录 / 当前持仓」区（实盘模式下换成左右分栏的 `LiveBottomPanel`：左下单、右三张数据表）。这四个方向上的宽度**全部由用户拖拽决定并持久化**（`lib/store.ts:98-127`），窗口本身还能被拖到 Electron 允许的最小值 `860×680`（`desktop/src/main.js:240`）。

问题出在：**这套可任意收缩的布局里，全仓从来没有声明过「按钮 / 标签 / 表头文字永不折行」这条契约。** `components/ui/index.tsx` 的 `Button`（`:27`）、`Badge`（`:143`）、`Tabs`（`:432`）三个共用基类都不带 `whitespace-nowrap`；`white-space` 的默认值 `normal` 允许在**任意两个汉字之间**断行（中文没有词边界，最小内容宽度就是一个字）。于是一旦容器被压窄，Flex item 的默认 `min-width:auto` 只保证「不小于一个字」，结果不是横向溢出、也不是整体缩放，而是**每个控件各自把自己的中文文案逐字劈开**：「自动 / 交易」「运行 / 中」「收益 / 记录」「随单 / 止盈」「浮动 / 盈亏」。这正是需求里说的「极其不美观」。全仓 `whitespace-nowrap` 的**实际类名用法**总共只有 **8 处**（`grep -rn "whitespace-nowrap" apps/web/components --include=*.tsx | grep -v "\.test\."` 实测 9 行，扣掉 1 行注释），其中 2 处就是上一轮 AI 助手面板修复留下的，交易工作台的左 / 中 / 下三块**一处都没有**。

本轮做三件事，且**只动表现层**：其一，把上一轮在 AI 助手面板上验证过的做法（**行级 `whitespace-nowrap` 继承 + 唯一一处 `min-w-0 truncate` 吸收压力**）提炼成一条写死在源码门禁里的**四类行排版契约**（§4.1），让「折行」这件事在结构上不再可能发生，而宽度压力一律流向**每行指定的那一处文本**，以省略号确定性地退化；其二，按这条契约把用户能拖宽的**全部四个容器**逐处改完，共 **25 个源文件**（左侧标的面板 6 / 共用 kit 1 + 中间 K 线面板 3 / 盘口成交列与下方数据表 10 / 右侧策略面板 5），并顺带修掉**根因同源的反模式的全部两个落点**——`PositionPanel`（`:31`）与 `TradeExplainDetail`（`:67`，评审 P1-7 新增）都在用 `sm:` / `lg:` 视口断点决定**面板内**的列数（面板宽与视口宽毫无关系，这是必然错的）；其三，建立两道门禁——一套 jsdom 组件用例冻结每一行的类名契约，一条源码扫描冻结「受管文件必须继续带着这条契约」，因为这套修法**全部由类名承载，一次误删就静默失效，而 jsdom 没有布局引擎，普通渲染用例察觉不到像素回归**。

方案不新增任何 REST / WebSocket / CLI 接口，不改 store 形态与任何持久化字段，不动后端 `services/quant` 与桌面主进程，不引入新依赖，不改变任何一个控件的可访问名、`title` 文案与点击行为。唯一一处刻意的**文案精简**在 `AutoTradePanel` 的分配汇总行（§4.3.4），已核实全仓无用例断言该字符串。

---

## 2. 现状与根因（Current state & root cause）

### 2.1 布局骨架（已逐行核对）

```
app/page.tsx:672  <div class="flex min-h-0 flex-1">
                    <NavRail/>                                   ← w-12 (48) + border-r (1) = 49px
app/page.tsx:654    <main class="grid" style="minmax(0,1fr) 6px {rightPanelWidth}px">
                      ├ ChartWorkspace                            ← 第 1 轨：可伸缩
                      ├ PanelResizeHandle 6px
                      └ <aside>RightPanel</aside>                 ← 第 3 轨：320–640px，用户拖拽
ChartWorkspace.tsx:226 <section class="flex h-full min-h-0 min-w-0 flex-col">      ← 纵向 splitRatio [0.40,0.85]
ChartWorkspace.tsx:230   <div ref=topRef class="flex min-h-0 min-w-0 flex-row">     ← 上区
ChartWorkspace.tsx:234     <div class="shrink-0 min-w-[200px]" style="width:{instrumentSidebarWidth}">
                             └ InstrumentSidebar                  ← 左侧标的面板 200–460px
ChartWorkspace.tsx:241     <PanelResizeHandle/>  6px
ChartWorkspace.tsx:252     <div class="min-h-[320px] min-w-0 flex-1">               ← 中间 K 线面板：无宽度地板
                             ├ ChartToolbar   （px-4，flex-wrap）
                             ├ 三条提示条     （缓存样例 / 回测封顶 / 实时封顶）
                             ├ ChartHeaderBar （px-4，flex-wrap，内含 w-[208px] 页签块）
                             └ CandleChart / EquityChart
ChartWorkspace.tsx:318     <PanelResizeHandle/>  6px
ChartWorkspace.tsx:322     <div class="min-w-[160px] max-w-[420px]" style="flexGrow:0;flexShrink:0;flexBasis:{ratio}%">
                             └ MarketDepthPanel                   ← 盘口 / 成交列面板
ChartWorkspace.tsx:329   <PaneSplitHandle/>
ChartWorkspace.tsx:334   <div class="min-h-[140px]">                                 ← 下区：整个 section 宽
                           模拟盘 → Tabs(成交记录|当前持仓) + TradesTable / PositionPanel / PaperPositionsTable
                           实盘   → LiveBottomPanel：左 LiveTradePanel(300–460) | 右 三张表
```

### 2.2 根因四条

1. **无不折行契约（主因）。** `Button`（`ui/index.tsx:27`）、`Badge`（`:143`）、`Tabs` 两个变体（`:481` / `:505`）的基类均**不含** `whitespace-nowrap`。Flex item 的 `min-width:auto` 只保证「不小于最小内容宽度」，而中文的最小内容宽度是**一个字**，所以压缩的结果一定是逐字折行而不是横向溢出。
2. **无指定的压力吸收点。** 交易工作台里没有任何一行声明过「宽度不够时**由谁**让位」。左簇不带 `min-w-0`（Flex 截断失效的头号原因）、右簇不带 `shrink-0`，于是「谁先被挤谁倒霉」——同一行在不同数据下会挤不同的控件，用户看到的破碎位置每次都不一样。
3. **表格用 `w-full` 强行塞进容器。** 受管范围内共 **9 张**数据表——工作台 5 张（`TradesTable` 7 列 / `PaperPositionsTable` 7 列 / `LiveFillsTable` 7 列 / `LivePositionsTable` 8 列 / `LiveOpenOrdersTable` 10 列）与右侧策略面板 4 张（`ComboResult` 3 列 / `CompareView` 动态列 / `RunHistory` 4 列 / `WalkForwardResults` 5 列）——一律 `<table className="w-full text-xs">`，单元格无 `whitespace-nowrap`。列数固定而容器可无限收窄，浏览器只能把每一列压到最小内容宽度——中文表头「浮动盈亏」「随单止盈」「仓位占比」「最大回撤」被劈成两行甚至四行。
   （**对照组 —— 正确做法仓库里早就有**：`Factor/` 与 `Settings/experience/` 下的 8 张表一律写成 `<div className="overflow-x-auto"><table className="w-full min-w-[420px|560px|680px|780px|860px]">`，`ExperienceList.tsx:113,124` 甚至已经在单元格上写了 `whitespace-nowrap`。**本轮就是把这套已被验证的写法搬到交易工作台与策略面板**，不是新发明。）
4. **用视口断点决定面板内布局。** `PositionPanel`（`Strategy/PositionPanel.tsx:32`）写的是 `grid-cols-2 sm:grid-cols-3 lg:grid-cols-6`。`sm:` / `lg:` 跟的是**视口宽**，而这块面板的实际宽度由 `rightPanelWidth` / `chartSplitRatio` / 下方左右分栏共同决定，与视口宽毫无关系：在 1440px 视口上把下方右栏拖到 300px，`lg:grid-cols-6` 照样生效，每格 50px，「开仓均价」的值必然折行。这是同一根因的另一种表达——**用一个不携带该信息的量去做布局判据**。

### 2.3 宽度预算（实测换算，中文字宽按字号计、ASCII 按 0.55×字号计，误差 ±6px）

**A. 左侧标的面板 · `AutoTradePanel` 顶部第 1 行**（`AutoTradePanel.tsx:90-133`）
容器 `px-2` ⇒ 侧栏 200px 档可用 **184px**、默认 240px 档可用 **224px**。

| 元素 | 宽度 |
|---|---|
| 「自动交易」`text-[11px]` | 44 |
| 状态：`已停止` `text-[10px]` / `● 运行中` | 30 / 43 |
| 聚合盈亏 `tabular text-[11px]`（如 `+1234.56`） | 0 / 46 |
| 「▮ 收益记录」按钮（icon 11 + gap 4 + 4 字 40 + `px-1.5`×2 12） | 67 |
| `Switch`（`h-5 w-9`，kit 自带 `shrink-0`） | 36 |
| gap（`gap-1.5`=6 ×3~4，行 `gap-2`=8） | 26 / 32 |
| **停止态合计** | **197** |
| **运行态合计** | **268** |

⇒ **200px 档：停止态即溢出 13px、运行态溢出 84px。默认 240px 档：停止态勉强放下、运行态仍溢出 44px。**
这就是需求里那句「调整左侧各个容器宽度时文字自动换行」的**最主要落点**：用户一开自动交易，这一行立刻碎成两行半。

**B. 中间 K 线面板可用宽**（`= 窗口宽 − 49 − 6 − rightPanelWidth − 侧栏宽 − 6 − max(depthRatio×上区宽, 160)`）

| 场景 | 窗口 | 右栏 | 侧栏 | **中栏实得** |
|---|---|---|---|---|
| 默认 | 1440 | 380 | 240 | **593px** ✓ |
| 用户把左右两栏都拖到最大（需求描述的场景） | 1440 | 640 | 460 | **113px** ✗ |
| 1024 笔记本 | 1024 | 380 | 240 | **177px** ✗ |
| 桌面端窗口最小值 | 860 | 380 | 240 | **13px** ✗✗ |

中栏内容的自然宽阈值：`ChartToolbar` 的周期段控（7 颗 × 36 + gaps + `px-4`×2）≈ **300px**；`ChartHeaderBar` 的 `w-[208px]` 页签块 + `px-4`×2 = **240px**。⇒ 中栏 < 300px 时工具栏开始碎，< 240px 时页签块也碎。**上表后三行全部落在碎裂区。**

**C. 下方数据表**（下区宽 `= 窗口宽 − 49 − 6 − rightPanelWidth`，与侧栏 / 盘口列无关）

| 表 | 列数 | 自然宽（不折行所需） | 1440/右栏 380 实得 | 1024/右栏 380 实得 |
|---|---|---|---|---|
| `TradesTable` | 7 | ≈ 616 | 981 ✓ | 565 ✗ |
| `LiveOpenOrdersTable` | 10 | ≈ 744 | 578（实盘右栏，扣掉左下单栏 402 与 `px-3`） ✗ | 264 ✗✗ |
| `LivePositionsTable` | 8 | ≈ 640 | 578 ✗ | 264 ✗✗ |
| `LiveFillsTable` | 7 | ≈ 520 | 578 ✓ | 264 ✗ |

⇒ **实盘模式下方的三张表，即便在 1440 默认窗口下也已经在折行**；「随单止盈 / 随单止损 / 仓位占比 / 浮动盈亏」是首当其冲的四个表头。

### 2.4 仓库里已有的正确样板（不是从零发明）

- `ChartWorkspace.tsx:359-360`：策略色标 chip 行用的是 `overflow-x-auto` + 内层 `flex min-w-max`——**这正是本轮要推广到表格上的 Type C 做法**，仓库已经知道它。
- `AiTradeMemberRow.tsx`：几乎每一行文本都带 `truncate`（`truncate` = `overflow:hidden; text-overflow:ellipsis; white-space:nowrap`），所以 AI 分组成员行**已经**不会折行。它是 Type A 的正样本。
- `IndicatorLegendOverlay.tsx:44`、`Settings/experience/ExperienceList.tsx:113,124`：已有 `whitespace-nowrap` 的零散用法。
- `AgentPanel.tsx:317,395` + `agentToolbarNoWrap.source.test.ts`：上一轮的完整样板（行级继承 + 单点吸收 + 源码冻结）。

---

## 3. 目标与非目标（Goals / Non-goals）

**目标**

- **G1**：在仓库里建立一条**成文、可机检**的排版契约（四类行 + 压力吸收点规则），写在 §4.1 与源码门禁的文件头注释里，成为后续所有面板的默认写法。
- **G2**：在「窗口 860–2560px × 左侧栏 200–460px × 右侧栏 320–640px × 盘口列 10%–30% × 上下分栏 40%–85%」的**全区间**内，交易工作台（左侧标的面板 / 中间 K 线面板 / 盘口成交列 / 下方成交与持仓区）**以及右侧交易策略面板**的**按钮文案、控件标签、表头、状态徽标、数值**没有任何一处发生**字内折行**。
- **G3**：宽度不足时的退化路径是**确定的、单点的、可预期的**——每一行只有一处文本会以省略号让位，且该处必带 `title` 让完整值仍可读；不是「谁先被挤谁倒霉」。
- **G4**：**零行为回归**——所有控件的 `role` / `aria-label` / 可访问名 / `title` 文案 / 点击语义 / 数据取值逐字不变。唯一例外是 §4.3.4 的一处刻意文案精简，已在正文标注。
- **G5**：真正需要**换行**的散文（空态提示、错误说明、免责文案、展开详情）**必须继续换行**，且由显式 `whitespace-normal` 声明，不靠「恰好没被继承到」这种运气。
- **G6**：明暗双主题下配色全部走既有 token，不新增颜色、不触碰 `globals.source.test.ts` 与 `contrastTokens.source.test.ts` 两道既有对比度门禁。

**非目标（明确不做，附理由）**

- **N1：不给 `Button` / `Badge` 基类加 `whitespace-nowrap`。** 二者分别有 60+ / 160 处调用，`children` 是调用方给的任意长度内容（含整段说明文字），基类锁死不折行会在弹窗里造出横向溢出。本轮用**行级继承**达到同一效果（§4.2）。
  **但 `Tabs` 是例外，本轮改它**——理由见 §4.4.1（它的 `label` 是闭集短字符串，且 `segmented` 变体的 `flex-1` 会主动制造折行）。
- **N2：不引入容器查询（`@container`）或 `ResizeObserver` 自适应。** `apps/web/tailwind.config.ts` 的 `plugins: []`，未装 `@tailwindcss/container-queries`；而 `sm:` / `md:` / `lg:` 跟的是视口宽、与可独立拖拽的面板宽无关，用了就是错的（§2.2 根因 4 正是这么错的）。行级不折行 + 单点截断是纯 CSS 的确定性解，零测量、零副作用。
- **N3：不解决「窗口过窄时中栏被压到 13px」这个窗口尺寸策略问题。** 算术在 §2.3 表 B：即便给中栏加上 240px 地板、让左栏与盘口列都可收缩（§4.6 做了这一步），上区的硬地板仍是 `200+6+240+6+160 = 612px`；而桌面端 `minWidth:860` 配 `rightPanelWidth:320` 时上区只有 `860−49−6−320 = 485px`，**任何 CSS 排布都放不下**，残余部分由 `<main>` 既有的 `overflow-hidden` 从右侧裁掉（行为不变）。彻底解法是「窄宽度下折叠盘口列 / 提高窗口最小宽 / 工作台横向滚动」三选一，属于**布局策略变更**，不该夹带在一次排版修复里 —— 记入 §12 后续提案。本轮的承诺是：**任何宽度下都只会看到省略号，绝不会看到被劈开的汉字。**
- **N4：不改任何业务逻辑、数据取值、请求时序、store 字段。** 本轮对每个 `.tsx` 的改动都只落在 `return (` 之后的容器结构与 `className` 上（外加 4 处新增 `data-*` 属性与 3 处新增 `title`）。
- **N5：不改动 `docs/` 之外的后端 / 桌面端任何文件。**
- **N6：不做受管清单（25 个源文件，§5）之外的扩散修复。** 设置中心、因子实验室、挖掘台、各类弹窗不在本轮受管清单里——它们要么宽度固定（弹窗 `w-[280px]` / `max-w-*`），要么不随交易工作台的四个拖拽手柄变化。§4.1 的契约对它们同样适用，但**落地留给后续轮次**，本轮不把门禁挂到它们头上（挂上去等于凭空造 200 处红）。

---

## 4. 技术设计（Technical design）

### 4.1 排版契约（本方案的机械核心）

CSS 的 `white-space` 是**可继承属性**，且继承沿 **DOM 树**传播而不是 React 树——所以在**行容器**上写一次 `whitespace-nowrap`，其下所有后代文本（含 `Button` / `Badge` / `Tabs` 内的文案）全部不折行，**无需修改共用 kit，也不会漏进 `createPortal` 出去的弹窗**（`Modal` 挂在 `document.body`，继承源是 `body`）。

本轮把所有会随宽度变化的容器归为**四类行**，每一类有唯一写法。**实现者必须先判断行的类型，再套写法，不得自由发挥。**

| 类型 | 名称 | 何时用 | 写法 | 退化方式 |
|---|---|---|---|---|
| **A** | 单行栏（single-line bar） | 头部 / 状态条 / 控制条：内容必须留在一行 | 行容器：`flex items-center justify-between gap-2 whitespace-nowrap` + `data-nowrap="row"`；左簇 `min-w-0`；右簇 `shrink-0`；**行内恰好一处** `min-w-0 truncate` + `title` | 指定文本出省略号 |
| **B** | 可回流控件栏（reflowing bar） | 工具栏 / chip 组：条目彼此独立、整块换行不难看 | 行容器：`flex flex-wrap items-center gap-x-* gap-y-* whitespace-nowrap` + `data-nowrap="row"`；每个条目 `shrink-0` | 整个条目掉到下一行，**条目内部永不折** |
| **C** | 数据表（table） | `<table>` 数据表 | 外层 `<div className="overflow-x-auto">`（无则新建）+ `<table className="w-full min-w-[Npx]">` + `<thead className="… whitespace-nowrap">` + 数据 `<tr className="… whitespace-nowrap">`；展开详情 `<td colSpan>` 显式 `whitespace-normal`。**`N` = 该表各列自然宽之和向上取整到 20 的倍数**，逐表在 §4.5.2 给定 | 表格超出容器宽 ⇒ 容器横向滚动 |
| **D** | 散文（prose） | 空态提示 / 错误说明 / 免责文案 / 展开详情 | 显式 `whitespace-normal break-words`（落在 A/B/C/S 的继承范围内时**必须写**；不在继承链内时**可以**写，作为防御性自证——见下方「两种写法的适用条件」），配 `leading-relaxed` | 正常换行——**这是期望行为** |
| **S** | 继承子树根（scope root） | 一整块面板 / 网格：想用**一次继承**覆盖整棵子树，而该块本身**不是一行**（`flex-col` / `grid`） | 块容器：`whitespace-nowrap` + `data-nowrap="scope"`；块内**每个**可压缩文本各自 `min-w-0 truncate` + `title`（不受「每行只能一处」约束——它们本就不在同一行） | 各文本各自出省略号 |

**三条铁律（下游实现逐条守住）：**

1. **左簇必须 `min-w-0`。** Flex item 默认 `min-width:auto`，不显式清零则 `truncate` 永不触发——这是 flexbox 截断失效的头号原因。
2. **右簇必须 `shrink-0`。** 保证压力**永远**流向左侧那一处指定文本，而不是随机压某个按钮。
3. **每行只能有一处 `truncate`。** 两处截断 = 两处都截一半 = 两处都读不出来。多出来的项要么 `shrink-0`，要么下沉到第二行（Type A 拆两行，正是上一轮 AI 助手面板的做法）。**这条只约束 Type A 的一行之内**；Type S 覆盖的是一棵子树，其中不同行各有自己的吸收点，互不冲突。
4. **锚点有且只有两个取值。** `data-nowrap="row"` = Type A / B 的**行**；`data-nowrap="scope"` = Type S 的**继承子树根**。写错取值不影响渲染，但会让 G3 的逐文件计数错位，所以 §4.3–§4.5 逐处点名了该用哪一个，实现者不得自行更换。

**Type D 两种写法的适用条件（评审 P1-6 补写）**：一段散文若**确实落在** A/B/C/S 的继承链内，`whitespace-normal` 是**必须**的（漏写 = 信息被截成一行，比折行更糟，见 K1）；若它只是**同一个组件里的兄弟节点**、根本不在继承链上，那么写它属于**防御性自证**——只在「未来有人可能把 nowrap 上提到共同祖先」时才值得写，并且**必须在正文里注明这是自证而非必需**，否则下一位维护者会据此反推出一条不存在的继承关系。本轮按这条规则处理的有两处：`InstrumentSidebar` 的三段（§4.3.1，写，因为它的头部与列表区共用 `:192` 根）与 `AiTradePanel` 的七块横幅（§4.3.6，不写，因为它们的共同祖先不会被加 nowrap）。

**为什么用 `data-nowrap="row"` 这个新属性**：这套修法**全部由类名承载**，而类名是最容易在重构里被顺手删掉的东西；jsdom 没有布局引擎，普通渲染用例察觉不到像素回归（这正是上一轮 `agentToolbarNoWrap.source.test.ts` 存在的理由）。给受管行加一个语义化的 `data-nowrap`（`row` / `scope`）锚点，让源码门禁可以做**跨 25 个文件的通用配对断言**（「凡带此属性的开标签必须同时含 `whitespace-nowrap`」+ 「每个受管文件至少有 N 处」），而不是写 40 条互不相干、一改就得改门禁的脆弱正则。它是标准 `data-*` 属性，零行为、零样式、零 a11y 影响，且在源码里自我说明「这一行受排版契约管辖」。

### 4.2 为什么不是别的解法

| 备选 | 否决理由 |
|---|---|
| 给 `Button` / `Badge` 基类加 `whitespace-nowrap` | 60+ / 160 处调用，`children` 长度不可控；弹窗内的长说明会横向溢出（N1） |
| 容器查询 / `ResizeObserver` 测量后切换布局 | 无插件、需新依赖；引入测量帧与布局抖动；纯 CSS 已能确定性解决（N2） |
| `sm:` / `lg:` 视口断点 | 面板宽 ≠ 视口宽，判据取自不携带该信息的量（§2.2 根因 4，本轮**要删掉**现存的一处） |
| 缩小字号 / `text-[9px]` | 可读性与对比度倒退，且只是把阈值推后一点，没有消除折行的可能性 |
| 每个 `Button` 调用点单独补 `whitespace-nowrap` | 本轮涉及 60+ 个控件；漏一个就破一处，且没有任何机制能发现漏掉的那个 |
| 让容器 `overflow-x-auto` 横向滚 | 对**数据表**是正解（Type C 就是这么做的）；对**头部控制条**不是——用户不会想到去横向滚一条工具栏才能看到开关 |

### 4.3 轴 B：左侧标的展示面板（6 个文件）

#### 4.3.1 `InstrumentSidebar.tsx`

- `:193` 头部容器补 `whitespace-nowrap` + `data-nowrap="row"`（覆盖页签 / 搜索框 / 筛选 chip 三层）。
- `:108` `FilterChips` 的 `flex flex-wrap gap-1` 是标准 **Type B**：补 `whitespace-nowrap` + `data-nowrap="row"`，每颗 chip 补 `shrink-0`。
- `:213` 错误块、`:243` 空态、`:264` 截断提示三处是 **Type D**：显式补 `whitespace-normal break-words`。
  **注意继承关系（评审 P1-6 订正）**：实测头部容器是 `:193-202`，而这三段**全部**落在 `:204` 的`<div className={clsx("min-h-0 flex-1", …)}>` 里——它是头部的**兄弟**，**没有一段**在头部的 nowrap 继承链上。所以本轮给它们写 `whitespace-normal` **不是必需**，而是 §4.1 定义的**防御性自证**：这两块共用 `:192` 的 `flex h-full min-h-0 flex-col` 根，后人若图省事把 nowrap 上提到那个根上，这三段就会瞬间变成一行加省略号——错误原因、「未找到匹配「xxx」的标的」、「仅显示前 N 条」都是**必须读全**的话。写这三处的成本是 3 个类名，收益是把那条误改路径提前堵死。
- 搜索框 `placeholder` 是单行 `<input>`，天然不折行，不动。

#### 4.3.2 `InstrumentRow.tsx`

- `:66-74` 行根节点补 `whitespace-nowrap` + `data-nowrap="row"`。
- `:96` 中间按钮已有 `min-w-0 flex-1`；`:100`/`:102` 两行文本已有 `truncate` ✓；`:106` 右簇已有 `shrink-0` ✓。**本文件只是把既有的正确写法用契约固定下来**，不改任何结构。
- `:99` 外层 `<span className="flex min-w-0 flex-col">` 保持不变。

#### 4.3.3 `WatchOtherPanel.tsx`

- `:62` header 改 **Type A**：`flex shrink-0 items-center justify-between gap-2 whitespace-nowrap px-2 py-1` + `data-nowrap="row"`；`:63` 标题 `<span>` 补 `min-w-0 truncate` 并补 `title={title}`（唯一吸收点）；`:66` 计数 `<span>` 补 `shrink-0`。
- `:69-72` 空态 `<p>` 是 **Type D**：补 `whitespace-normal break-words`（它已有 `leading-relaxed`）。

#### 4.3.4 `AutoTradePanel.tsx`（本轴的主战场，§2.3 表 A）

**三行头部全部重排为 Type A。**

**(a) 第 1 行（替换 `:90-133`）** —— 关键动作：把「▮ 收益记录」按钮**移出第 1 行**（它是 67px，占了停止态可用宽的 36%，且它是**低频、非破坏性**的查看动作，与「开 / 停整组自动交易」这个有资金后果的开关放在一起本就是信息架构错误）。

```tsx
<div
  data-nowrap="row"
  data-testid="auto-trade-header-row"
  className="flex min-h-[24px] items-center justify-between gap-2 whitespace-nowrap"
>
  {/* 左簇：唯一压力吸收点在标题上。 */}
  <span className="flex min-w-0 items-center gap-1.5 text-[11px] font-semibold text-[var(--text)]">
    <span className="min-w-0 truncate" title="自动交易">自动交易</span>
    {running ? (
      <span className="inline-flex shrink-0 items-center gap-1 text-[var(--long-ink)]">
        <span className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-[var(--long)]" />
        运行中
      </span>
    ) : (
      <span className="shrink-0 text-[10px] font-normal text-[var(--muted)]">已停止</span>
    )}
    {running && aggregatePnl != null && (
      <span className="shrink-0 tabular text-[11px]" style={{ color: colorForReturn(aggregatePnl) }}>
        {fmtMoney(aggregatePnl)}
      </span>
    )}
  </span>
  {/* 右簇：开关。kit 自带 shrink-0，外层包一层也写 shrink-0 以承接 title。 */}
  <div
    title={gateReason ?? (running ? "停止全部自动交易并平仓" : "启动整组自动交易")}
    className={clsx("shrink-0", blocked && "cursor-not-allowed opacity-40")}
  >
    <Switch checked={running} onChange={…原样…} label="自动交易分组开关" />
  </div>
</div>
```

`min-h-[24px]` 的作用与上一轮 AI 助手控制条同款：停止态行内最高的是 20px 的 `Switch`，运行态多出 `animate-pulse` 圆点与 `text-[11px]` 文本，不钉死会在启停瞬间抖一格并推动下面两行。改后预算：停止态 `44+6+30+8+36 = 124` ≤ 184 ✓（余 60）；运行态 `44+6+43+6+46+8+36 = 189` ≈ 184，**超出 5px 由标题截断为「自动交…」吸收**，`title` 兜底完整语义。

**(b) 第 2 行（`:135-147`）**：`ModeToggle` + 「均分」。补 `whitespace-nowrap` + `data-nowrap="row"`；`ModeToggle`（`:220-224`）根节点补 `shrink-0`，两颗按钮（`:232`）补 `shrink-0`；「均分」按钮（`:139`）补 `shrink-0`。预算：`118 + 8 + 32 = 158` ≤ 184 ✓。

**(c) 第 3 行（`:149-171`）**：改成「左汇总（吸收点）｜右计数 + 收益记录」。

```tsx
<div
  data-nowrap="row"
  data-testid="auto-trade-summary-row"
  className="flex items-center justify-between gap-2 whitespace-nowrap text-[10px]"
>
  <span
    data-testid="auto-trade-allocation-summary"
    className="min-w-0 truncate tabular text-[var(--muted)]"
    title={allocationMode === "even"
      ? "已分配占总预算的比例，Σ 不得超过 100%"
      : "每个标的各自的仓位上限，各标的独立，合计可超过 100%"}
  >
    {allocationMode === "even" ? "已分配 " : "合计上限 "}
    <span className={clsx("font-semibold", overLimit ? "text-[var(--short-ink)]" : "text-[var(--text)]")}>
      {Math.round(totalPct * 100) / 100}%
    </span>
    {allocationMode === "even" ? "/100%" : ""}
  </span>
  <span className="flex shrink-0 items-center gap-1.5">
    <span className="tabular text-[var(--muted)]">
      {members.length}/{LIVE_AUTO_MAX_GROUP_SYMBOLS}
    </span>
    <button type="button" onClick={onOpenRecords} title="按时间维度查看每次完整交易的收益与总收益率"
            className="inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium text-[var(--accent-ink)] transition-colors hover:bg-[var(--accent-soft)]">
      <svg …原样… /> 收益记录
    </button>
  </span>
</div>
```

> **唯一一处文案变更（RV 预警）**：`max_ratio` 模式下的「（各标的独立）」被降级进 `title`。理由：它就写在**已经选中「最大比例」的 `ModeToggle` 正上方**，是同一件事的第二次表述，而它独占 70px——占 200px 档可用宽的 38%。已核实全仓无任何用例断言 `"各标的独立"`（`grep -rn "各标的独立" apps/web --include=*.test.ts*` 为空；仅 `lib/store.test.ts:707` 与 `lib/autoTrade.ts:85` 的**注释**里出现，非断言）。改后预算：`(30|40) + 22 + 27 + 8 + 20 + 6 + 67 = 180|190` vs 184，`even` 模式 ✓、`max_ratio` 模式由左侧汇总截断 6px 吸收。
- `:174-176` 门禁 / 拖入错误 `<p>`：现为 `truncate`，改为 **Type D** `whitespace-normal break-words`（它是解释「为什么按钮点不动」的话，截成一行看不全等于没说；`title` 保留）。
- `:176-181` 空成员提示 `<p>` 保持 **Type D**，显式补 `whitespace-normal`。

#### 4.3.5 `AutoTradeMemberRow.tsx`

- `:56-64` 行根节点补 `whitespace-nowrap` + `data-nowrap="row"`。
- `:83` 中间按钮已 `min-w-0 flex-1` ✓；`:86`/`:87` 两行已 `truncate` ✓；`:112` / `:123` 右簇已 `shrink-0` ✓。
- `:89-107` 运行态内联簇（圆点 + 多/空 + 盈亏）补 `shrink-0`，避免它把左侧 `instId` 的截断额度吃光。

#### 4.3.6 `AiTradePanel.tsx`

- `:96` 分组头行：`flex items-center justify-between gap-2` → 补 `whitespace-nowrap` + `data-nowrap="row"`；`:97` 左簇（已有 `flex-wrap`，属 Type B 内嵌）补 `min-w-0`，其中 `:99` 标题 `<span>` 补 `shrink-0`、`:114` 「N 个标的」补 `shrink-0`；`:116` 右簇 `<span>` 补 `shrink-0`。
- `:158-160` 「自动交易标的」小标题：补 `truncate`（它是 `flex-col` 的直接子项，`min-w-0` 由 `:157` 容器提供 —— 需给 `:157` 的 `flex flex-col gap-1` 补 `min-w-0`）。
- `ResumableCard` / `PausedBanner` / `StoppedCard` / `NotArmedBanner` / `TradabilityStrip` / `SummaryStalledStrip` / `DisciplineStrip` 内的说明文字一律 **Type D**：本轮**只在它们落在新 nowrap 继承链之内时**补 `whitespace-normal`。经核对，这些块都是分组头 `<div>` 的**兄弟节点**（`:95` 的 `flex flex-col gap-2` 根之下），而 nowrap 只加在 `:96` 那一行上，**不构成继承**，故 **零改动**。这一条必须写进实现说明，否则实施者会去改一堆不该改的地方。

### 4.4 轴 C：中间 K 线面板 + 共用 kit（4 个文件）

#### 4.4.1 `components/ui/index.tsx` —— `Tabs` 两个变体加 `whitespace-nowrap`（对 N1 的**刻意例外**）

`Tabs` 的 `segmented` 变体给每颗按钮写的是 `flex-1`（`:505`），也就是**强制等分、主动制造压缩**——它是全仓唯一一个「宽度一紧就必然折行」的基类。`underline` 变体已有 `shrink-0`（`:481`），本身不折，但一并加上以统一契约、并让门禁只需一条判据。

与 `Button` / `Badge` 的关键差别：`Tabs` 的 `label` 不是任意 `children`，而是 `tabs: { key: string; label: string }[]` —— 一个**闭集短字符串**。全仓 13 个非测试调用点的标签实测如下，**没有任何一个需要换行**：

| 调用点 | 变体 | 标签 |
|---|---|---|
| `RightPanel.tsx:22` | segmented | AI 助手 / 交易策略 |
| `Chart/ChartHeaderBar.tsx:40` | segmented | K线价格走势 / 净值 |
| `Chart/InstrumentSidebar.tsx:194` | segmented | 自选 / 现货 / 合约 |
| `Strategy/StrategyPanel.tsx:686` | segmented | 绩效指标 / 历史 |
| `Chart/ChartWorkspace.tsx:351` | underline | 成交记录 / 当前持仓 |
| `Live/LiveBottomPanel.tsx:44` | underline | 当前持仓 / 成交记录 / 委托交易 |
| `Chart/CandleChart.tsx:571` | underline | MACD / RSI / KDJ / VOL（`d.key.toUpperCase()`） |
| `Chart/AiBacktestModal.tsx:312` | underline | 概览 / 进阶指标 / AI 决策 / 成交 |
| `Agent/AiTradeStatsPanel.tsx:44` | underline | 当前 / 历史 |
| `Intel/IntelModal.tsx:365` | underline | 超短 N / 中期 N / 长期 N / 影响力 / 信源 / 已归档 N / 整理记录 |
| `Settings/ExperienceSection.tsx:382` | underline | 列表 / 图谱 / 检索预演 / 注入后表现 / 上下文 |
| `Strategy/MemberPicker.tsx:42` | underline | 普通策略 / 组合策略 |
| `Strategy/StrategyListSection.tsx:90` | underline | 普通策略 / 组合策略 |

改动就是两处类名各加一个 `whitespace-nowrap`：

```tsx
// :482（v1 写 :481）  underline 变体
"-mb-px shrink-0 whitespace-nowrap border-b-2 px-3 py-1.5 text-xs font-medium transition-colors duration-150",
// :508（v1 写 :505）  segmented 变体
"flex-1 whitespace-nowrap rounded-[7px] px-3 py-1.5 text-xs font-medium transition-all duration-150",
```

`components/ui/Tabs.test.tsx` 现有 **17 条**用例（`describe.each(["segmented","underline"])` 内 8 条 × 2 变体 = 16，加外层独立 `describe` 里断言「不传 `label` 即无 `aria-label`」的 1 条；**评审 P2-2 订正：v1 写的「9 条 × 2 = 18」不对**）全部是 `role` / `aria-selected` / `tabIndex` / 键盘漫游 / 点击语义断言（已逐条核对，无一条 `toHaveClass` / 结构断言），**一条不受影响**。

#### 4.4.2 `ChartToolbar.tsx`

- `:52` 根行是标准 **Type B**：`flex flex-wrap items-center gap-2` → 补 `whitespace-nowrap` + `data-nowrap="row"` + `data-testid="chart-toolbar"`。
- 三个段控容器（`:53` 周期、`:75` 视图、`:161` 模式）、三个 `<input type="date">`（`:118` / `:127` / `:140`）与 `LivePrice`（`:185`，见下条）逐个补 `shrink-0`；`ml-auto` 的右簇（`:160`）补 `shrink-0`。
  ⇒ 中栏窄到放不下时，整个**段控**掉到下一行，而不是段控内部把「15m」劈成「15 / m」。
- **`IndicatorMenu` 与 `ConnectionStatus` 必须就地包一层，不能改它们自己（评审 P0-2）**：实测两者的根节点分别是 `IndicatorMenu.tsx:58` 的 `<div ref={rootRef} className="relative">` 与 `ConnectionStatus.tsx:104` 的 `<div ref={wrapRef} className="relative">`——**工具栏行的 flex item 是这两个根 `<div>`，不是它们内部的触发钮**，把 `shrink-0` 写在触发钮上对收缩计算完全无效（v1 的写法就是错在这里）。而这两个文件不在 §5 受管清单内，改它们会把 G3 / G5 的边界撑开。因此本轮**在 `ChartToolbar.tsx` 里就地包裹**：

  ```tsx
  {/* 这两个组件的根 <div className="relative"> 是下拉定位的包含块，不能动；
      收缩控制只能加在外面这一层（评审 P0-2）。包一层不影响 relative 的定位语义。 */}
  <div className="shrink-0"><IndicatorMenu /></div>
  …
  <div className="shrink-0"><ConnectionStatus … /></div>
  ```

  这样 §5 的受管清单不变，T7「每个直接子元素都含 `shrink-0`」也真的成立。
- `:185-192` 的 `LivePrice` `<span>`：补 `shrink-0`（它含实时价，绝不能被压）。

#### 4.4.3 `ChartHeaderBar.tsx`

- `:38` 根行是 **Type B**：补 `whitespace-nowrap` + `data-nowrap="row"` + `data-testid="chart-header-bar"`。
- `:39` **`w-[208px] shrink-0` → `shrink-0`**（去掉写死宽度）。理由：208px 是照着「K线价格走势」六个字量出来的，余量只有约 7px（`(208−8−4)/2 − px-3×2 = 74` vs 文本 67px），字体 / 字重 / 语言任一微调即折行；去掉后 `segmented` 的 `flex-1` 在**内容宽容器**里按 max-content 分配，页签块自然贴合内容，配合 §4.4.1 的 `whitespace-nowrap` 后**永不折行**，且在窄档下作为一个整体掉到下一行。
- `:47` OHLC 组（已有 `flex-wrap`）补 `shrink-0`；`:104-109` 的 `Field` 根 `<span>` 补 `shrink-0`。
- `:63-68` 涨跌组、`:73-88` 净值组同样补 `shrink-0`。
- `:91` 策略色标组（已有 `flex-wrap`）保持；`:103` 里的 `max-w-[72px] truncate` 保留（它已经是正确的单点吸收）。

#### 4.4.4 `ChartWorkspace.tsx`（提示条 + 下区页签）

- `:256-258` / `:261-263` / `:266-268` 三条提示条：它们是**整句说明**（「缓存样例 · OKX 暂不可达，当前显示本地缓存历史。」），属 **Type D**，显式补 `whitespace-normal break-words`。**刻意不给它们 nowrap**——把一句话截成「缓存样例 · OKX 暂不…」等于没说。
- `:345-348` 模拟提示条同上。
- `:350-356` 下区页签容器补 `whitespace-nowrap` + `data-nowrap="row"`。
- `:359-360` 策略 chip 行**保持不变**（已是正确的 Type C 变体：`overflow-x-auto` + `min-w-max`），仅给 `:365` 的按钮补 `shrink-0` 与 `whitespace-nowrap` 的显式声明（由 `:359` 容器补 `whitespace-nowrap` 承接）。
- `:382` 下区内容容器 `overflow-auto` 保持——它就是 Type C 表格的横向滚动宿主。

### 4.5 轴 D：盘口成交列 + 下方数据表 + 右侧策略面板数据表（14 个文件）

#### 4.5.1 `MarketDepthPanel.tsx`（需求里的「成交列面板」）

- `:19`（v1 写 `:18`）根容器 `<div className="flex min-h-0 flex-col gap-2 px-2 py-2 text-[11px]">` 是 **Type S**（它是 `flex-col`，不是一行）：补 `whitespace-nowrap` + **`data-nowrap="scope"`** （评审 P1-2：v1 写的 `row` 与 Type A 的定义冲突）+ **`data-testid="market-depth-panel"`**（**评审 P0-1：这个属性 §6 与 T8 都在用，v1 却从未要求写它；实测该文件当前 `data-testid` 出现 0 次**）。一次继承覆盖标题 / 四格指标 / 买卖档 / 成交流。
- `:29-31` `market.error` `<div>` 是 **Type D**：补 `whitespace-normal break-words`（错误必须读得全）。
- `:22`/`:26`/`:28` 标题、最新价、时间三行已有 `truncate` ✓（`:21` 的连接点补 `shrink-0`）。
- `:99` `BookSide` 的价格 `<span>` 补 `truncate`（`:100` 数量已有 ✓）——`grid-cols-[1fr_1fr]` 在 160px 档下每格 68px，五位价格 + 小数会顶满。
- `:82-84` / `:63` 的 `Waiting for OKX …` 是 **Type D**：补 `whitespace-normal`。

#### 4.5.2 工作台五张数据表统一套 Type C

| 文件 | 列数 | `min-w-[N]` | `<thead>` | 数据 `<tr>` | 展开 `<td colSpan>` | 横向滚动宿主 |
|---|---|---|---|---|---|---|
| `Strategy/TradesTable.tsx` | 7 | **620** | `:40` | `:74-80` | **`:139`**（`colSpan={COLUMN_COUNT}`，v1 写 `:137`）补 `whitespace-normal` | `:38` 既有 `max-h-[320px] overflow-auto` ✓ |
| `Strategy/PaperPositionsTable.tsx` | 7 | **560** | `:26` | 数据行 | **`:74`**（`<td colSpan={5}>当前无持仓</td>`，**评审 P0-3 补记：v1 记为「—」，实际存在**）补 `whitespace-normal` | `:24` 既有 ✓ |
| `Live/LiveFillsTable.tsx` | 7 | **520** | `:69`（`FillsTableView` 内）/ `<thead>` `:73` | 数据行 | **`:146`**（`colSpan={COL_COUNT}`，评审已实测行号）补 `whitespace-normal` | `LiveBottomPanel.tsx:51` ✓ |
| `Live/LivePositionsTable.tsx` | 8 | **640** | `:114` | `:197` | `:237`（`colSpan={8}`）补 `whitespace-normal` | 同上 ✓ |
| `Live/LiveOpenOrdersTable.tsx` | 10 | **760** | `:86` | 数据行 | — | 同上 ✓ |

**为什么把 `whitespace-nowrap` 放在 `<thead>` / 数据 `<tr>` 而不是每个 `<th>` / `<td>`**：继承一次覆盖全行，改动量从 60+ 处降到 10 处；而**展开详情行**（`TradeExplainDetail` / `PositionOrdersRow` / 成交触发原因）是 Type D，必须在那一个 `<td colSpan>` 上显式 `whitespace-normal` 把继承覆盖掉。**这是本轴最容易漏的一处。评审已把五个文件全文搜完，`colSpan` 落点确切为四处，实现时照此逐一处理即可、不必再搜（评审 P0-3）**：

| # | 落点 | 是什么 | 处置 |
|---|---|---|---|
| 1 | `TradesTable.tsx:139` `colSpan={COLUMN_COUNT}` | `TradeExplainDetail` 展开详情 | 补 `whitespace-normal` |
| 2 | `PaperPositionsTable.tsx:74` `colSpan={5}` | 「当前无持仓」空态格（**不是展开行**） | 补 `whitespace-normal`（4 个字永远不会折，纯粹为让 G4 保持成一条无例外的全量规则——例外比规则贵） |
| 3 | `LiveFillsTable.tsx:146` `colSpan={COL_COUNT}` | 自动交易触发原因展开行 | 补 `whitespace-normal` |
| 4 | `LivePositionsTable.tsx:237` `colSpan={8}` | `PositionOrdersRow` 子委托展开行 | 补 `whitespace-normal` |

> `LiveFillsTable.tsx:13` 是一条**注释**、里面也含 `colSpan` 字样（评审 P2-3）；G4 只匹配 `<td` 开标签，注释与常量名不计。
> `LiveFillsTable.tsx:70` 的 `<caption>`（19 字操作说明）在 `<table>` 内、`<thead>` 外，**不在本轮任何 nowrap 的继承链上，保持换行、零改动**（评审 P2-6 归类）。

**为什么同时要 `min-w-[Npx]` 而不是只靠 nowrap**：只写 nowrap 时表格的 used width = `max(100%, min-content)`，`min-content` 会随**数据**变化（一条 `instId` 短、下一条长），列宽因此会在数据刷新时抖动；写死 `min-w-[Npx]` 把「不横滚就一定不折行」的阈值钉成常量，列宽稳定、横滚出现的时机可预期。这也正是仓库 `Factor/` 与 `Settings/experience/` 八张表的既有写法（§2.2 对照组）。`sticky top-0` 表头在横向滚动容器里行为正常，无需改动。

#### 4.5.3 右侧策略面板四张数据表（同一套 Type C）

右侧面板同样由用户拖宽（320–640px），`StrategyPanel.tsx:496` 的 `flex-1 overflow-auto` 是它们的默认滚动宿主。

| 文件 | 列 | `min-w-[N]` | 备注 |
|---|---|---|---|
| `Strategy/RunHistory.tsx` | 标的 / 来源 / 收益 / 时间 | **420** | `:67` 已有 `max-h-[320px] overflow-auto` ✓ |
| `Strategy/WalkForwardResults.tsx` | 窗口 / 时间区间 / 收益率 / 最大回撤 / 成交 | **560** | `:111` 已有 `overflow-auto` ✓；「时间区间」列值最长，是本表的折行首犯 |
| `Strategy/CompareView.tsx` | 策略 + 动态指标列 | **560** | `:162` 已有 `overflow-x-auto` ✓ |
| `Strategy/ComboResult.tsx` | 体制 / 成员 / 根数 / 贡献 | **360** | **无自有滚动容器**，需新建 `<div className="overflow-x-auto">` 包住 `:71` 的 `<table>`；否则横向压力会传导到整块 `StrategyPanel` 的滚动条上 |

`Strategy/MetricsCards.tsx`（`:35` 的 `grid grid-cols-2`，v1 写 `:36`）一并处理，它是 **Type S**（网格不是一行，评审 P1-2）：容器补 `whitespace-nowrap` + **`data-nowrap="scope"`**，标签 `<div>`（`:43`）与数值 `<div>`（`:44`）各补 `truncate`、卡片根 `<div>`（`:38`）补 `min-w-0`（320px 档下每格 ≈148px，`text-base font-semibold` 的六位数值会顶满）。标签与数值各有自己的截断额度，互不争抢——这正是 Type S 与 Type A 的差别。

#### 4.5.4 `Strategy/PositionPanel.tsx`（根因 4 的**第一个**落点；第二个见 §4.5.6）

```tsx
// :31（v1 写 :32）改前
<div className="grid grid-cols-2 gap-x-6 gap-y-3 text-xs sm:grid-cols-3 lg:grid-cols-6">
// 改后：列数由**容器**宽决定，与视口无关；每格最少 120px，放不下就自动少一列
// 锚点用 scope 而非 row（评审 P1-2）：它是 grid，不是一行
<div
  data-nowrap="scope"
  data-testid="position-panel-grid"
  className="grid grid-cols-[repeat(auto-fit,minmax(120px,1fr))] gap-x-6 gap-y-3 whitespace-nowrap text-xs"
>
```

`Cell` 的改法（**评审 P1-4 订正**）：实测它的签名是 `Cell({ label, children })`（`:56-63`，v1 写 `:57-64`），**「值」是调用方传进来的 `children`**（6 个调用点分别传 `<span>` 或 `<Badge>`），所以 v1 那句「`Cell` 的 `<span>` 标签与值各补 `truncate`」在 `Cell` 内部无法落地——它自己只有 label 那一个 `<span>`。确切改法是：

```tsx
function Cell({ label, children }: { label: string; children: ReactNode }) {
  return (
    // min-w-0：grid item 的 min-width 同样默认 auto，不清零则下面两处 truncate 永不触发。
    <div className="flex min-w-0 flex-col gap-1">
      <span className="truncate text-[10px] text-[var(--muted)]" title={label}>{label}</span>
      {/* 值由调用方传入，截断只能在这里统一包一层；6 个调用点零改动。 */}
      <span className="block min-w-0 truncate">{children}</span>
    </div>
  );
}
```

`:21-25` 空态 `<div>` 是 Type D，补 `whitespace-normal`。

> `auto-fit` + `minmax()` 是纯 CSS 的容器自适应，**不需要容器查询插件**，Tailwind 3.4 的任意值语法直接支持（无空格，无需下划线转义）。

#### 4.5.5 `Live/LiveBottomPanel.tsx` + `Live/LiveTradePanel.tsx`

- `LiveBottomPanel.tsx:43-50` 页签容器补 `whitespace-nowrap` + `data-nowrap="row"`。
- `LiveTradePanel.tsx:219` 的两个 `SegToggle` 行补 `whitespace-nowrap` + `data-nowrap="row"`；`:373` 按钮补 `whitespace-nowrap`。
  > `:365` 的 `SegToggle` 根实测已有 `flex-1`（`flex-basis` 为 0 ⇒ 收缩权重本就是 0），再补 `shrink-0` 是**空操作**（评审 P2-4）。仍可为契约一致性写上，但**不要指望它解决什么**——这一行真正起作用的是行级 `whitespace-nowrap`。
- `:212`（`blockedReason`）、`:259`（市价单说明）、`:145`（加载失败）三处是 **Type D**，补 `whitespace-normal break-words`。
- `:242` 「委托价格（USDT）」`<label>` 内标签补 `truncate`（左栏最窄 300px，不至于折，但契约要求单点吸收）。

#### 4.5.6 `Strategy/TradeExplainDetail.tsx`（根因 4 的第二个落点 —— **评审 P1-7 新增受管文件**）

v1 把根因 4 说成「只有 `PositionPanel` 一处」，实测不成立：`TradeExplainDetail.tsx:67` 的 `MetricsGrid` 写的是 `grid grid-cols-2 gap-x-4 gap-y-1.5 sm:grid-cols-3`。它渲染在 `TradesTable` 展开行的 `<td colSpan={COLUMN_COUNT}>` 里（§4.5.2 落点 1），宽度由 `窗口宽 − 49 − 6 − rightPanelWidth` 决定——**与视口宽同样毫无关系**。在 860 窗口 + 右栏 640 档下下区只有 165px，而 `sm:`（视口 ≥ 640px）**恒为真** ⇒ 每格 55px ⇒ 「入场依据」这类三四字标签必然折行。而 **R9 恰好就是走这条路的验收**，留着它等于把一个必现缺陷放在验收动线的正中间。

```tsx
// :67 改前
<div className="grid grid-cols-2 gap-x-4 gap-y-1.5 sm:grid-cols-3">
// 改后：与 §4.5.4 同款容器自适应；140px 是「标签 + 六位数值」的自然宽
<div
  data-nowrap="scope"
  data-testid="trade-explain-metrics-grid"
  className="grid grid-cols-[repeat(auto-fit,minmax(140px,1fr))] gap-x-4 gap-y-1.5 whitespace-nowrap"
>
```

格子根 `<div className="flex flex-col gap-0.5">`（`:69`）补 `min-w-0`，其中 `:70` 标签与 `:71` 数值各补 `truncate`（`title` 已有 `m.hint`，无需新增）。

**本文件其余部分一律零改动**：`:60` 的 `headline`、`CompositeBar` 的说明等都是 Type D 散文，它们是 `MetricsGrid` 的**兄弟**、不在新 nowrap 的继承链上（判据同 §4.3.6），**不要**顺手给它们加 `whitespace-normal`——按 §4.1 的「两种写法的适用条件」，这里没有会被上提 nowrap 的共同祖先。

### 4.6 轴 E：结构地板与压缩顺序（辅助轴，可独立回滚）

现状（`ChartWorkspace.tsx`）：左侧栏 `shrink-0`、盘口列 `flexShrink: 0`、中栏 `flex-1 min-w-0`。三者叠加的后果是**中栏是唯一的压缩受害者，且无地板**——§2.3 表 B 里 860px 窗口下中栏实得 13px。

三处一行改动，把压缩顺序变成「先让左侧栏退到它的 200px 地板 → 再让盘口列退到 160px 地板 → 中栏始终保底 240px」：

| # | 位置 | 改前 | 改后 |
|---|---|---|---|
| E-1 | `ChartWorkspace.tsx:235` | `flex min-h-0 shrink-0 min-w-[200px] flex-col` | `flex min-h-0 min-w-[200px] shrink flex-col`（去掉 `shrink-0`） |
| E-2 | `ChartWorkspace.tsx:252` | `flex min-h-[320px] min-w-0 flex-1 flex-col` | `flex min-h-[320px] min-w-[240px] flex-1 flex-col` |
| E-3 | `ChartWorkspace.tsx:324` | `style={{ flexGrow: 0, flexShrink: 0, flexBasis: … }}` | `flexShrink: 1`（`min-w-[160px]` / `max-w-[420px]` 不动） |

Flex 负自由空间的分配规则保证了这个顺序：中栏的 `flex-basis` 是 0，收缩权重 = `shrink × basis = 0`，本来就分不到收缩额度，加上 `min-w-[240px]` 后它会被判为「触底冻结」而完全退出收缩计算；剩余亏空按 basis 加权落到左侧栏（basis = 用户设定的像素宽）与盘口列（basis = 比例）身上，各自触到自己的 `min-w-*` 后停住。

效果（§2.3 表 B 场景 2「用户把左右两栏都拖到最大」）：上区 745px，改前中栏 113px，改后左侧栏从 460 收缩到 339、盘口列保持 160、**中栏拿到 240px**。

**已知残留**（写进 N3）：硬地板合计 `200+6+240+6+160 = 612px`；上区小于 612px 时（窗口 < `612+49+6+rightPanelWidth`，如 860px 窗口 + 320px 右栏 ⇒ 上区 485px）仍会由 `<main>` 既有的 `overflow-hidden` 从右侧裁掉盘口列的一部分——**与改前行为一致，不是本轮引入的**。

**已知副作用**（必须写进实现说明与人工验收 —— **评审 P1-1 重写**）：左侧栏被压缩时，`store.instrumentSidebarWidth` 的存值会大于渲染宽，由此产生一个 v1 没有论证到的**拖拽死区**。

拖拽换算用的是 `setInstrumentSidebarWidth(widthFromLeftEdge(rect.left, clientX))`（实测 `ChartWorkspace.tsx:157`，`rect` 取自上区 `topRef`）——它是**指针绝对定位**而非增量累加，所以 v1 说的「无累积漂移」**成立**。但「无漂移」不等于「跟手」：

| 状态 | 向左拖 | 向右拖 |
|---|---|---|
| 未过约束（`渲染宽 == 存值`，如 1440 窗口 / 右栏 380 / 侧栏 240） | 立即跟手 | 立即跟手 |
| **过约束**（`渲染宽 < 存值`，如 1440 / 右栏 640 / 侧栏 460 ⇒ 实得 ≈339） | 立即跟手（存值一降到实得宽以下，两者立刻重合） | **死区**：新存值仍小于旧存值 ⇒ 负自由空间不变 ⇒ 渲染宽不变 ⇒ **手柄停在原地不跟指针**，直到指针越过旧存值（本例 460）才恢复 |

**处置：判定为预期行为，不加 JS 补偿。** 理由有三——(1) 它表达的语义是正确的：**放不下的时候就是不能再拖宽**；(2) 要消除死区就得在拖拽回调里读实际渲染宽做二次夹取，那是往纯 CSS 方案里塞一条测量逻辑，与 N2 / N4 直接冲突，代价远大于收益；(3) 死区**只在过约束档位出现**，而那个档位下中栏本来就已经触底，用户真正想做的是把右栏拖窄而不是把左栏拖宽。

**但必须验**：v1 用来兜这条的 R4（200↔460 连续拖 20 次）是在 **1440 默认窗口**下做的，那个档位根本不过约束，**验不到死区**。因此 §9.3 新增 **R14** 专门在过约束档位走一遍。用户放大窗口后侧栏会自动回弹到存值——这一条同样并入 R14。**不需要**改 store 夹取逻辑。

### 4.7 渲染时序（无新增副作用）

本轮**不新增任何 state / effect / 订阅 / 查询 / 定时器**。渲染路径不变：

```
store.{rightPanelWidth, instrumentSidebarWidth, depthPanelRatio, chartSplitRatio}
   └─► page.tsx grid / ChartWorkspace flex ─► 各面板 ─► (纯 CSS) 同一帧内完成排布与截断判定
```

所有改动都是 `className` / 容器嵌套 / `data-*` / `title`，不触发额外 re-render，不改变任何组件的挂载与卸载时机，因此不产生额外请求、不重置任何 React Query 缓存。

---

## 5. 文件 / 模块变更计划（File / module change plan）

| # | 文件 | 类型 | 一句话意图 | 轴 |
|---|---|---|---|---|
| 1 | `apps/web/components/ui/index.tsx` | 修改 | `Tabs` 两个变体的按钮类名各加 `whitespace-nowrap`（N1 的刻意例外，§4.4.1） | C |
| 2 | `apps/web/components/Chart/InstrumentSidebar.tsx` | 修改 | 头部套 Type A、筛选 chip 套 Type B、错误 / 空态 / 截断提示三处显式 Type D | B |
| 3 | `apps/web/components/Chart/InstrumentRow.tsx` | 修改 | 行根节点补 `whitespace-nowrap` + `data-nowrap="row"`，把既有的正确写法固定成契约 | B |
| 4 | `apps/web/components/Chart/WatchOtherPanel.tsx` | 修改 | header 套 Type A（标题为唯一吸收点 + `title`，计数 `shrink-0`）；空态 Type D | B |
| 5 | `apps/web/components/Chart/AutoTradePanel.tsx` | 修改 | 三行头部全部重排为 Type A；「收益记录」从第 1 行下沉到第 3 行右簇；`min-h-[24px]` 钉死行高；「（各标的独立）」降级进 `title` | B |
| 6 | `apps/web/components/Chart/AutoTradeMemberRow.tsx` | 修改 | 行根节点套 Type A；运行态内联簇补 `shrink-0` | B |
| 7 | `apps/web/components/Chart/AiTradePanel.tsx` | 修改 | 分组头行套 Type A + 左右簇 `min-w-0` / `shrink-0`；「自动交易标的」小标题补 `truncate`。**横幅 / 红条各组件零改动**（不在继承链内，§4.3.6） | B |
| 8 | `apps/web/components/Chart/ChartToolbar.tsx` | 修改 | 根行套 Type B；六个段控 / 三个日期输入 / 实时价 chip / 连接状态逐个 `shrink-0` | C |
| 9 | `apps/web/components/Chart/ChartHeaderBar.tsx` | 修改 | 根行套 Type B；**去掉 `w-[208px]` 写死宽**；OHLC / 涨跌 / 净值三组与每个 `Field` 补 `shrink-0` | C |
| 10 | `apps/web/components/Chart/ChartWorkspace.tsx` | 修改 | 三条提示条 + 模拟提示条显式 Type D；下区页签行套 Type A；**轴 E 的三处地板与收缩顺序改动（E-1/E-2/E-3）** | C+E |
| 11 | `apps/web/components/Chart/MarketDepthPanel.tsx` | 修改 | 根容器一次 nowrap 继承；错误行与 `Waiting for OKX…` 显式 Type D；买卖档价格补 `truncate` | D |
| 12 | `apps/web/components/Strategy/TradesTable.tsx` | 修改 | Type C：`<thead>` / 数据 `<tr>` 补 nowrap；`:139` 展开详情 `<td colSpan>` 补 `whitespace-normal`；空态 Type D | D |
| 13 | `apps/web/components/Strategy/PositionPanel.tsx` | 修改 | **删掉 `sm:` / `lg:` 视口断点**，改 `grid-cols-[repeat(auto-fit,minmax(120px,1fr))]` + nowrap（Type S）；`Cell` 根补 `min-w-0`、label 补 `truncate`、`children` 由 `Cell` 包一层 `truncate`（评审 P1-4） | D |
| **13b** | `apps/web/components/Strategy/TradeExplainDetail.tsx` | 修改 | **评审 P1-7 新增受管文件**：`MetricsGrid`（`:67`）删掉 `sm:grid-cols-3`，改容器自适应 + Type S；格子根补 `min-w-0`、标签与值补 `truncate`。**其余部分（散文 / CompositeBar）零改动** | D |
| 14 | `apps/web/components/Strategy/PaperPositionsTable.tsx` | 修改 | Type C | D |
| 15 | `apps/web/components/Live/LiveBottomPanel.tsx` | 修改 | 右栏页签行套 Type A | D |
| 16 | `apps/web/components/Live/LiveFillsTable.tsx` | 修改 | Type C（改 `FillsTableView`，`min-w-[520px]`） | D |
| 17 | `apps/web/components/Live/LivePositionsTable.tsx` | 修改 | Type C（`min-w-[640px]`）；`:237` 展开 `<td colSpan={8}>` 补 `whitespace-normal` | D |
| 18 | `apps/web/components/Live/LiveOpenOrdersTable.tsx` | 修改 | Type C（改 `OpenOrdersTableView`，`min-w-[760px]`），10 列表头是全仓最挤的一处 | D |
| 19 | `apps/web/components/Live/LiveTradePanel.tsx` | 修改 | 两个 `SegToggle` 行套 Type B；三处说明文案显式 Type D | D |
| 20 | `apps/web/components/Strategy/RunHistory.tsx` | 修改 | Type C（`min-w-[420px]`，宿主 `:67` 已有） | D |
| 21 | `apps/web/components/Strategy/WalkForwardResults.tsx` | 修改 | Type C（`min-w-[560px]`，宿主 `:111` 已有）；「时间区间」列是本表折行首犯 | D |
| 22 | `apps/web/components/Strategy/CompareView.tsx` | 修改 | Type C（`min-w-[560px]`，宿主 `:162` 已有 `overflow-x-auto`） | D |
| 23 | `apps/web/components/Strategy/ComboResult.tsx` | 修改 | Type C（`min-w-[360px]`）；**需新建 `overflow-x-auto` 容器包住 `:71` 的 `<table>`**（四张表里唯一没有自有滚动宿主的） | D |
| 24 | `apps/web/components/Strategy/MetricsCards.tsx` | 修改 | `grid grid-cols-2` 容器套 **Type S**（评审 P1-2，v1 写 Type A）；标签 / 数值补 `truncate`，卡片根补 `min-w-0` | D |
| 25 | `apps/web/components/Chart/workstationNoWrap.test.tsx` | **新增** | 组件用例 T1–T10 + T16–T18：左侧标的面板 / 中间 K 线面板 / 成交列 / 右侧策略面板的行契约与吸收点冻结 | F |
| 26 | `apps/web/components/Live/liveTablesNoWrap.test.tsx` | **新增** | 组件用例 T11–T14：三张实盘表的 Type C 契约（复用各自既有桩化方式） | F |
| 27 | `apps/web/components/ui/noWrapContract.source.test.ts` | **新增** | 源码冻结门禁 G0–G7：自证、`Tabs` kit、`data-nowrap`（`row` \| `scope`）配对不变式、受管文件逐一计数（合计 19）、表格 Type C 与四处 `colSpan`、禁用视口断点、轴 E（含上区三列行的 `overflow-hidden`）、可回流条目的 `max-w-full`（**G7 为终审新增**，见《终审发现的实现缺陷》D-4） | F |
| 28 | `apps/web/components/ui/Tabs.test.tsx` | 修改 | 追加 1 条 T15：两个变体的按钮都带 `whitespace-nowrap`（既有 **17** 条一条不改，评审 P2-2 订正） | F |
| 29 | `docs/plans/workstation-text-nowrap-contract/spec.md` | **新增** | 本文档 | — |

**受管源文件合计 25 个**（轴 B 6 + 轴 C 4 含共用 kit + 轴 D **15**；v1 为 24，评审 P1-7 补入 `TradeExplainDetail.tsx`），加 3 新增 / 1 修改的测试文件与本文档，共 **30 个变更条目**。

**曾在候选清单里、经核实后刻意排除的（附理由，避免评审重提）：**

| 文件 | 排除理由 |
|---|---|
| `Strategy/AutoTradeTradesTable.tsx` | **全仓无调用方**（`grep -rn "AutoTradeTradesTable" apps/web/components` 只命中它自身的 `export default` 与其 `.test.tsx`）——是死代码，且它根本不是 `<table>`（`<ul>` 列表）。本轮不碰死代码，也不顺手删（删除属另一件事）。 |
| `Chart/AutoTradePnlPanel.tsx` / `AutoTradePnlSummary.tsx` | 是 `Modal`（`max-w-2xl`），宽度由视口而非工作台手柄决定 ⇒ N6 范围外。`AutoTradePnlSummary.tsx:100` 的 `sm:grid-cols-4` 在这里**是正确的**（弹窗宽确实跟视口走），故 G5 的视口断点禁令**不得**扩到它头上。 |
| `Chart/AiTradeMemberRow.tsx` | 每一行文本都已带 `truncate`（= nowrap + 省略号），已经满足契约（§2.4）。加锚点等于制造无意义的 diff。 |
| `Live/DirectionToggle.tsx`、`LeverageControl.tsx`、`QuantitySlider.tsx`、`TpSlFields.tsx` | **评审 P1-5 补入本表**（v1 既没修也没解释，属清单漏洞）。四者都渲染在 `LiveBottomPanel` 左栏，宽度随 `rightPanelWidth` 变化，且都是 `justify-between` 的标签／数值行（`LeverageControl.tsx:28`、`QuantitySlider.tsx:54`、`TpSlFields.tsx:42`）——形状上确实属于本轮的靶子。**排除理由是算过账**：该栏实测 `w-2/5 min-w-[300px] max-w-[460px]`，**有 300px 硬地板**，扣掉 `p-3` 后仍有约 276px；按 §2.3 的字宽口径，其中最长的一行是 `LeverageControl` 的「杠杆倍率 ＋ 上限 125×」≈ 98px、`TpSlFields` 的 `grid-cols-2` 每格 134px 而「止盈金额 USDT」≈ 70px，**均有 2 倍以上余量**。所以它们不像左侧栏（200px 无缓冲）那样是必现缺陷。**但这是估算不是实测**，故 §9.3 R7 增加一条目视确认；一旦看到折行，按同一套契约纳入下一轮（§12）。 |
| `Agent/**` | 上一轮 `5dfbe62` 已修并已有自己的门禁 `agentToolbarNoWrap.source.test.ts`。 |
| `Live/MarketCloseAllConfirm.tsx`、各类 `Modal` | 宽度固定，N6。 |
| `Factor/**`、`Discovery/**`、`Settings/**` | N6；且其中 8 张表**已经**是正确写法（§2.2 对照组）。 |

**明确不涉及**：`lib/store.ts`（宽度夹取一字不改）、`app/page.tsx`、`app/globals.css`、`tailwind.config.ts`、`components/RightPanel/RightPanel.tsx`、`components/Agent/**`、任何 `services/quant/**`、任何 `desktop/**`。

---

## 6. 接口设计（Interface design）

**REST / WebSocket / CLI：无任何新增或变更。** 本轮是纯表现层，不触碰 `lib/api.ts` / `lib/tradingApi.ts` / `lib/liveAutoGroupApi.ts` / `lib/aiTradeApi.ts` / `lib/ws.ts` 的任何签名，后端 `services/quant` 零改动。

**组件 props 契约变化：**

| 组件 | 变更 | 兼容性 |
|---|---|---|
| `Tabs`（`components/ui/index.tsx`） | props 签名不变；渲染的按钮类名多一个 `whitespace-nowrap` | 13 个调用点零改动；**17** 条既有用例全绿（评审 P2-2 订正） |
| `AutoTradePanel` | props 签名**逐字不变**（含 `onOpenRecords`）；仅「收益记录」按钮的**渲染位置**从第 1 行移到第 3 行 | 唯一调用方 `WatchlistTab.tsx:276`，无需改动 |
| `PositionPanel` | props 不变；根节点由响应式 grid 改为容器自适应 grid | 唯一调用方 `ChartWorkspace.tsx:388` |
| 其余 22 个文件 | props 全部不变 | — |

**DOM 契约（对外可依赖的稳定标识，供用例与后续维护定位）：**

| 选择器 | 含义 |
|---|---|
| `[data-nowrap="row"]` | **契约锚点（行）**。Type A / B 的行容器。凡带此属性的元素，其 `className` 必须同时含 `whitespace-nowrap`；该元素之下的所有文本除显式 `whitespace-normal` 者外一律不折行。 |
| `[data-nowrap="scope"]` | **契约锚点（继承子树根）**（评审 P1-2 新增）。Type S 的块容器（`flex-col` / `grid`，本身不是一行）：`MarketDepthPanel` 根、`PositionPanel` 网格、`MetricsCards` 网格、`TradeExplainDetail` 的 `MetricsGrid`。约束与 `row` 相同，差别只在「每行唯一 truncate」那条铁律不适用。两个取值合计跨 25 个受管文件、共 **19** 处。 |
| `[data-testid="auto-trade-header-row"]` | 自动交易区第 1 行（标题 / 状态 / 盈亏 / 开关），带 `min-h-[24px]`。 |
| `[data-testid="auto-trade-summary-row"]` | 自动交易区第 3 行（分配汇总 / 成员计数 / 收益记录）。 |
| `[data-testid="auto-trade-allocation-summary"]` | 第 3 行的**唯一压力吸收点**：`min-w-0 truncate` + `title`。 |
| `[data-testid="chart-toolbar"]` | K 线工具栏行（Type B）。 |
| `[data-testid="chart-header-bar"]` | K 线 OHLC 头部行（Type B）；其首个子元素**不得**再带 `w-[208px]`。 |
| `[data-testid="market-depth-panel"]` | 盘口 / 成交列面板根容器（Type S 继承源）。**本轮新增的属性**（评审 P0-1：v1 把它混在「既有标识」里，实测该文件当时 `data-testid` 数为 0），写入点见 §4.5.1。 |
| `[data-testid="position-panel-grid"]` | 持仓卡片网格；`className` **不得**含 `sm:grid-cols-` / `lg:grid-cols-`。**本轮新增。** |
| `[data-testid="trade-explain-metrics-grid"]` | 成交触发原因的指标网格（评审 P1-7）；同样**不得**含任何 `*:grid-cols-` 视口断点。**本轮新增。** |
| `role="region"` + `aria-label="自动交易"` / `"其他自选"` / `"自选标的"` | 既有可访问名，**逐字不变**。 |
| `role="tablist"` / `role="tab"` / `role="switch"` / `role="table"` / `role="columnheader"` | 既有语义，逐字不变。 |

---

## 7. 数据模型（Data model）

**无新增持久化字段、无 DB 迁移、无新增 store 键、无新增组件 state、无新增 wire 字段。**

本轮读取的全部是既有状态，读取点与推导点一处不变：

| 名称 | 来源 | 类型 | 本轮用途 |
|---|---|---|---|
| `instrumentSidebarWidth` | `useAppStore`（zustand persist，`store.ts:119-121` 夹取 `[200,460]`，默认 240） | `number` | 左侧栏宽度。**本方案只被动适应，不读取、不写入**（纯 CSS，无 JS 分支）。轴 E 之后渲染宽可能小于存值（§4.6）。 |
| `depthPanelRatio` | `store.ts:125-127` 夹取 `[0.10,0.30]`，默认 0.15 | `number` | 盘口列 `flexBasis`。轴 E 只改 `flexShrink`，**不改夹取区间**。 |
| `rightPanelWidth` | `store.ts:113-115` 夹取 `[320,640]`，默认 380 | `number` | 右栏宽度。不改。 |
| `chartSplitRatio` | `store.ts:98-100` 夹取 `[0.40,0.85]`，默认 0.8 | `number` | 上下分栏。不改。 |
| `allocationMode` | `store.autoTradeAllocationMode` | `"even" \| "max_ratio"` | 决定第 3 行汇总文案分支（§4.3.4）。取值与语义不变。 |
| `running` / `gateReason` / `aggregatePnl` / `totalPct` | `WatchlistTab` 既有派生 | — | 第 1 / 3 行内容。推导点一处不变。 |

**新增的布局常量**（均为 Tailwind 类，刻意**不**抽成 TS 常量——它们各自只在一处使用，抽出去只会制造间接层）：

| 常量 | 值 | 出处 |
|---|---|---|
| 自动交易头行最小行高 | `min-h-[24px]` | §4.3.4（与 `AiTradeToggle` 同款，理由同源） |
| 中栏宽度地板 | `min-w-[240px]` | §4.6 E-2 |
| 持仓卡片最小格宽 | `minmax(120px,1fr)` | §4.5.3 |

---

## 8. 可访问性与交互细节（A11y & interaction）

1. **可访问名零变化。** 25 个受管文件里没有任何一处 `aria-label` / `aria-current` / `aria-pressed` / `role` / 按钮文案 / `Badge` 文案被改动。读屏播报内容与改前逐字相同。
2. **截断必配 `title`。** 每一处新增的 `truncate` 都同时补 `title`（`WatchOtherPanel` 标题、`AutoTradePanel` 汇总行、`PositionPanel` 各格、`MarketDepthPanel` 价格档）。截断只是**视觉**退化，完整值始终可通过悬停与读屏获得——`title` 会进入可访问描述，读屏不受截断影响。
3. **Tab 顺序 = 视觉顺序，且本轮不变。** 唯一的 DOM 顺序变化是「收益记录」按钮从 `AutoTradePanel` 第 1 行移到第 3 行（第 3 行仍在同一个 `<header>` 内、仍在成员列表之前），键盘从第 1 跳变为第 4 跳。这与上一轮把自动交易开关移出顶栏是同一类取舍：**把低频查看动作从有资金后果的开关旁边挪开**，降低误触。
4. **误触防护改善。** 改前「收益记录」（打开弹窗）与「自动交易分组开关」（**立即停止全部自动交易并平仓，无二次确认**）在同一行、相距约 8px。改后二者分处第 3 行左区与第 1 行右区，物理距离拉开一整行。
5. **零布局跳动。** `AutoTradePanel` 第 1 行 `min-h-[24px]` 把停止 / 运行两态行高锁成同值（停止态最高 20px 的 `Switch`，运行态多出 `text-[11px]` 文本的 24px 行盒），启停时不再抖 4px 并推动下面两行与整条成员列表。
6. **表格横向滚动的可达性。** Type C 之后表格可能横向溢出，宿主容器均为既有的 `overflow-auto`：鼠标可用 Shift+滚轮、触控板可双指横滑、键盘可 Tab 进单元格内可聚焦元素后由浏览器自动滚入视口。表头 `sticky top-0` 仅纵向粘附，横滚时随内容移动（正确行为——列标题必须跟着自己的列走）。
7. **对比度不变。** 本轮不新增、不修改任何颜色 token 与前景 / 背景配对，`app/globals.source.test.ts` 的 WCAG 矩阵与 `components/ui/contrastTokens.source.test.ts` 的用法扫描均不受影响。
8. **动效不变。** 不新增任何 `transition` / `animation`；既有的 `animate-pulse`（运行中圆点）与 `transition-colors duration-150 ease-smooth` 原样保留，`prefers-reduced-motion` 既有全局压制照常生效。
9. **`data-nowrap` 不影响无障碍树。** `data-*` 属性不映射任何 ARIA 语义，不进入可访问名 / 描述 / 角色计算。

---

## 9. 测试与验收标准（Testing & acceptance criteria）

> **跑测前置**（两条已被本仓库反复验证的环境约束，务必写进实现说明）：
> ① `apps/web` 的组件用例**必须**在 `NODE_ENV=test` 下跑，否则 React 会抛 `act()` 环境错误；
> ② 仓库 `core.autocrlf=true`，源码扫描的跨行正则一律先 `.replace(/\r\n/g, "\n")` 归一化，否则干净检出上恒红。
> 命令：`cd apps/web && NODE_ENV=test pnpm vitest run`（或全量 `python scripts/dev.py test`）。

### 9.1 组件用例（jsdom，冻结类名契约与结构）

**A. `apps/web/components/Chart/workstationNoWrap.test.tsx`（新增）**

桩化沿用 `WatchlistTab.test.tsx:11-26` 的既有写法（`vi.mock` 掉 `liveAutoGroupApi` / `accountApi` / `tradingApi` / `autoTradeSettingsApi` / `api.strategies`）；`AutoTradePanel` / `WatchOtherPanel` / `ChartHeaderBar` / `MarketDepthPanel` / `PositionPanel` / `TradeExplainDetail`（T9b，评审 P1-7）**六个组件都是纯展示组件、可直接 `render` 传 props**，不需要 `QueryClientProvider`。

| 用例 | 断言 |
|---|---|
| T1 `自动交易头行声明了不折行契约且行高被钉死` | `getByTestId("auto-trade-header-row")` 同时 `toHaveClass("whitespace-nowrap", "min-h-[24px]")` 且带 `data-nowrap="row"` |
| T2 `停止态与运行态的头行高度类名完全一致` | 分别以 `running=false/true` 渲染，两次 `className` 中的 `min-h-[24px]` 均在；**并断言两态下 `getByRole("switch", { name: "自动交易分组开关" })` 都存在**（防止有人为省宽度把开关条件渲染掉） |
| T3 `收益记录按钮已移出第 1 行、落在汇总行` | `within(getByTestId("auto-trade-header-row")).queryByRole("button", { name: /收益记录/ })` 为 null；`within(getByTestId("auto-trade-summary-row")).getByRole("button", { name: /收益记录/ })` 存在；点击后 `onOpenRecords` 被调用**一次**（零行为回归） |
| T4 `分配汇总行只有一处截断吸收点，且带完整 title` | `getByTestId("auto-trade-allocation-summary")` `toHaveClass("min-w-0", "truncate")` 且 `title` 非空；在该行内 `querySelectorAll(".truncate")` 长度 **恰为 1** |
| T5 `其他自选区标题是唯一吸收点、计数不被压缩` | `WatchOtherPanel` 的 header 带 `whitespace-nowrap`；标题 `<span>` 有 `truncate` + `title`；计数 `<span>` 有 `shrink-0` |
| T6 `K 线头部行不再写死 208px` | `getByTestId("chart-header-bar")` 有 `whitespace-nowrap`；其**首个子元素**的 `className` **不含** `w-[208px]`，且含 `shrink-0` |
| T7 `K 线工具栏是可回流栏且每个直接子元素都不可压缩` | `getByTestId("chart-toolbar")` 同时有 `flex-wrap` 与 `whitespace-nowrap`；**遍历 `toolbar.children`，逐个断言 `classList.contains("shrink-0")`**（防止新增控件时漏加）。**评审 P0-2 前置条件**：`IndicatorMenu` / `ConnectionStatus` 的根节点是它们自己文件里的 `<div className="relative">`，本用例只有在 §4.4.2 那两层 `<div className="shrink-0">` 包裹**真的落地**后才可能绿——若实现者按 v1 的写法去改「触发钮」，这条会红，**红得对**。 |
| T8 `成交列面板一次继承覆盖全部行情文本，错误行仍换行` | `getByTestId("market-depth-panel")` 有 `whitespace-nowrap`；传入 `market.error` 后该错误节点 `toHaveClass("whitespace-normal")` |
| T9 `持仓卡片按容器宽自适应，且不含视口断点` | `getByTestId("position-panel-grid")` 的 `className` 匹配 `/grid-cols-\[repeat\(auto-fit/`，且**不含** `sm:grid-cols-` / `lg:grid-cols-`；每个 `Cell` 根有 `min-w-0`；**且每个 `Cell` 内恰有 2 个 `.truncate`（label 一处 + 包裹 `children` 的那一层一处，评审 P1-4）** |
| **T9b** `成交触发原因的指标网格同样不含视口断点`（评审 P1-7 新增） | `TradeExplainDetail` 传入一条带 `metrics` 的 `explain` → `getByTestId("trade-explain-metrics-grid")` 匹配 `/grid-cols-\[repeat\(auto-fit/` 且**不含** `sm:grid-cols-`；同时断言同一次渲染里的 `headline` 段落**没有**被加上 `whitespace-nowrap`（Type D 未被误伤） |
| T10 `成交记录表的表头与数据行不折行，展开详情行仍换行` | `TradesTable` 传入一条带 `explain` 的成交 → `container.querySelector("thead")` 有 `whitespace-nowrap`；展开后 `querySelector("td[colspan]")` 有 `whitespace-normal`；表头文案「止损(SL)」「止盈(TP)」仍能被 `getByRole("columnheader", { name: … })` 命中（文案零变更） |

**B. `apps/web/components/Live/liveTablesNoWrap.test.tsx`（新增）**

`LiveFillsTable` 与 `LiveOpenOrdersTable` 各自导出了**纯展示子视图**（`FillsTableView` @ `LiveFillsTable.tsx:58`、`OpenOrdersTableView` @ `LiveOpenOrdersTable.tsx:76`），可直接渲染、**无需任何 mock 与 QueryClientProvider**；`LivePositionsTable` 没有导出视图，沿用 `LivePositionsTable.test.tsx:10-16` 的 `vi.mock("@/lib/tradingApi")` + `QueryClientProvider` harness。

| 用例 | 断言 |
|---|---|
| T11 `成交记录表（实盘）表头与数据行不折行` | `FillsTableView` → `thead` 与每个数据 `tr` 均 `toHaveClass("whitespace-nowrap")` |
| T12 `委托交易表 10 列表头一律不折行` | `OpenOrdersTableView` → 同上；并逐条断言 10 个 `columnheader` 的可访问名与改前一致（「随单止盈」「随单止损」「挂出时间」等零变更） |
| T13 `持仓表数据行不折行，而展开的子委托行仍换行` | `LivePositionsTable` 渲染一条持仓 → 展开 → `td[colspan="8"]` 有 `whitespace-normal`，数据 `tr` 有 `whitespace-nowrap` |
| T14 `三张表的滚动宿主仍是 overflow-auto`（防止有人为了「让表格塞进去」把 `overflow` 改回 `hidden`） | `LiveBottomPanel` 右栏内容容器 `toHaveClass("overflow-auto")` |

**C. `apps/web/components/ui/Tabs.test.tsx`（追加 1 条，既有 **17** 条一字不改；评审 P2-2 订正）**

| 用例 | 断言 |
|---|---|
| T15 `两个变体的页签按钮都声明了不折行` | `describe.each` 内新增：`screen.getAllByRole("tab").forEach(t => expect(t).toHaveClass("whitespace-nowrap"))` |

**D. 右侧策略面板四张表 + 指标卡（并入 `workstationNoWrap.test.tsx`）** —— 四个组件都是纯展示组件（`ComboResult` / `WalkForwardResults` / `MetricsCards` 直接传 props；`CompareView` 沿用 `CompareView.test.tsx` 既有的 store 夹具写法；`RunHistory` 沿用其既有的 `vi.mock("@/lib/api")` 写法）。

| 用例 | 断言 |
|---|---|
| T16 `右侧策略面板的四张表都有列宽地板与不折行表头` | 逐个渲染 → `container.querySelector("table")` 同时 `toHaveClass("w-full")` 且 `className` 匹配 `/min-w-\[\d+px\]/`；`thead` `toHaveClass("whitespace-nowrap")` |
| T17 `ComboResult 的表被自有的横向滚动容器包住` | `container.querySelector("table")!.parentElement` `toHaveClass("overflow-x-auto")`（四张里唯一需要**新建**宿主的一张） |
| T18 `指标卡的标签与数值各自截断，不互相挤压` | `MetricsCards` 传入一条超长数值 → 网格容器有 `whitespace-nowrap` 且带 **`data-nowrap="scope"`**；每张卡根有 `min-w-0`；标签与数值 `<div>` 各有 `truncate` |
| **T19** `锚点取值只有 row 与 scope 两种`（评审 P1-2 新增，与 G2/G3 呼应） | 在 T1–T18 已渲染的容器上遍历 `container.querySelectorAll("[data-nowrap]")`，断言每个的属性值 ∈ `{"row","scope"}` 且其 `classList` 含 `whitespace-nowrap`——把 G2 的源码不变式在**运行时**再钉一遍（源码门禁看的是文本，这条看的是真实 DOM） |


### 9.2 源码冻结门禁 `apps/web/components/ui/noWrapContract.source.test.ts`（新增）

> **职责边界**（与既有三道门禁分工，避免造出第二个所有者）：
> · CSS 取值 / WCAG 配对 → `app/globals.source.test.ts`
> · 面色当文字用 / 未定义 token / fetch 超时 → `components/ui/contrastTokens.source.test.ts`
> · AI 助手面板两横栏 → `components/Agent/agentToolbarNoWrap.source.test.ts`（**本轮不动它**）
> · **交易工作台排版契约 → 本文件**

| 编号 | 断言 |
|---|---|
| **G0** | 自证：受管清单里的 **25** 个文件全部读到且长度 > 500，且各含一条标志性内容（如 `AutoTradePanel.tsx` 含 `"LIVE_AUTO_MAX_GROUP_SYMBOLS"`、`TradeExplainDetail.tsx` 含 `"MetricsGrid"`）。**缺了这条，路径写错会让下面全部空转变绿。** 所有读入内容先 `.replace(/\r\n/g, "\n")`。 |
| **G1** | `components/ui/index.tsx` 的 `Tabs` 两个变体按钮类名各含 `whitespace-nowrap`（用两条锚定正则：`/-mb-px shrink-0 whitespace-nowrap/` 与 `/flex-1 whitespace-nowrap/`）。 |
| **G2** | **配对不变式（全仓扫描 `components/` + `app/`）**：凡出现 `data-nowrap="row"` 或 `data-nowrap="scope"`，同一开标签内必须出现 `whitespace-nowrap`；且属性值只允许这两种。反过来不要求（不带锚点的 nowrap 是允许的）。<br>**实现方式必须是「切片」而不是 `<[^>]*>`（评审 P1-3）**：JSX 开标签里常有箭头函数（`onChange={(next) => …}`），`[^>]*` 会在 `=>` 的 `>` 处提前截断，带事件处理器的受管行会**假红**（既有的 `agentToolbarNoWrap.source.test.ts` 用 `/<header[^>]*>/` 只是恰好没踩到，不能照抄）。确定性做法：以每处 `data-nowrap=` 的下标为锚，`slice(src.lastIndexOf("<", i), 下一个 "<" 的下标)` —— JSX 开标签内部不会再出现 `<`，这个切片恰好就是完整的开标签体，在它里面断言 `whitespace-nowrap`。 |
| **G3** | **受管文件逐一计数**（**评审 P0-4：v1 以「…」收尾，等于没有规格；下表是完整的**）：每个文件的 `data-nowrap=` 出现次数必须**等于**下表规定值（用 `===` 而不是 `≥`——`≥` 挡不住「加一个新的、删一个旧的」）。合计 **19**。K3 说得对，G2 挡不住删属性，**G3 才是真防线**，所以它不能是半张表。 |
| **G4** | **Type C 冻结**：9 张受管表的源码中，`<table` 开标签必须同时含 `w-full` 与 `min-w-[`，`<thead` 开标签必须含 `whitespace-nowrap`；且 §4.5.2 点名的**四处** `<td colSpan={…}` 各自含 `whitespace-normal`（**评审 P0-3**：v1 只让改 3 处却写了一条全量规则，G4 恒红；同时**只匹配 `<td` 开标签**——`LiveFillsTable.tsx:13` 的注释里也含 `colSpan` 字样，按行匹配会误命中，评审 P2-3）。开标签的取法同 G2 的切片规则。 |
| **G5** | **禁用视口断点**：受管清单里的 **25** 个文件中**不得**出现 `sm:grid-cols-` / `md:grid-cols-` / `lg:grid-cols-` / `xl:grid-cols-`（面板内布局用视口断点是本轮根因之一；**评审 P1-7 已把第二个落点 `TradeExplainDetail.tsx` 纳入受管，这条才真的成立**）。附一句注释说明「这条只管受管文件，不是全仓禁令——弹窗（`AiBacktestModal` / `StrategyDetailModal` / `ComboDetailSections` / `AutoTradePnlSummary` / `aiBacktest/panels`）与设置中心用视口断点是合理的，评审已逐个核实它们的宽度确实跟视口走」。 |
| **G6** | **轴 E 冻结**：`ChartWorkspace.tsx` 中栏容器含 `min-w-[240px]`；左侧栏容器**不含** `shrink-0`；盘口列的 `flexShrink` 值不为 `0`。 |

**G3 的完整计数表（合计 19；`AutoTradePanel` 3 处、其余多数 1 处）：**

| 文件 | `row` | `scope` | 小计 | 落点 |
|---|---|---|---|---|
| `Chart/InstrumentSidebar.tsx` | 2 | 0 | 2 | 头部 `:193` + `FilterChips` `:107`（源码 1 处、渲染 2 次） |
| `Chart/InstrumentRow.tsx` | 1 | 0 | 1 | 行根 |
| `Chart/WatchOtherPanel.tsx` | 1 | 0 | 1 | header `:62` |
| `Chart/AutoTradePanel.tsx` | 3 | 0 | 3 | 头部三行 |
| `Chart/AutoTradeMemberRow.tsx` | 1 | 0 | 1 | 行根 |
| `Chart/AiTradePanel.tsx` | 1 | 0 | 1 | 分组头 `:92` |
| `Chart/ChartToolbar.tsx` | 1 | 0 | 1 | 根行 `:52` |
| `Chart/ChartHeaderBar.tsx` | 1 | 0 | 1 | 根行 `:38` |
| `Chart/ChartWorkspace.tsx` | 1 | 0 | 1 | 下区页签容器 |
| `Chart/MarketDepthPanel.tsx` | 0 | 1 | 1 | 根容器 `:19` |
| `Strategy/PositionPanel.tsx` | 0 | 1 | 1 | 网格 `:31` |
| `Strategy/MetricsCards.tsx` | 0 | 1 | 1 | 网格 `:35` |
| `Strategy/TradeExplainDetail.tsx` | 0 | 1 | 1 | `MetricsGrid` `:67` |
| `Live/LiveBottomPanel.tsx` | 1 | 0 | 1 | 右栏页签行 |
| `Live/LiveTradePanel.tsx` | 2 | 0 | 2 | 两个 `SegToggle` 行 |
| **合计** | **15** | **4** | **19** | 其余 10 个受管文件（`ui/index.tsx` 与 9 张表）走 Type C，**不用锚点** |

所有正则匹配前先 `.replace(/\r\n/g, "\n")`。

### 9.3 人工验收（无头浏览器 + 真机各一遍，**这是终审门**）

jsdom 没有布局引擎，**上面所有自动化用例都只能证明「类名写对了」，证明不了「像素上真的没折行」**。因此必须有一遍真实渲染验收。

复现环境（沿用 `docs/diagnoses/workstation-layout-resize-and-overflow/analysis.md` 的既有手法）：

```bash
node apps/web/node_modules/next/dist/bin/next dev apps/web -p 18761
# 逐档截图（Windows / git bash）
CHROME="C:/Program Files/Google/Chrome/Application/chrome.exe"
for w in 860 1024 1200 1440 1920; do
  "$CHROME" --headless --disable-gpu --no-sandbox \
    --screenshot="tmps/nowrap-${w}.png" --window-size=${w},900 "http://localhost:18761/"
done
```

> **如何在无头档位里预置面板宽度**：四个宽度全部由 zustand persist 落在 `localStorage` 的
> `"aragon-ui"` 键下（`lib/store.ts:1285`，`version: 30`）。截图前用
> `--user-data-dir` 起一个持久 profile，或在页面加载后注入
> `localStorage.setItem("aragon-ui", JSON.stringify({state:{instrumentSidebarWidth:200,rightPanelWidth:640,depthPanelRatio:0.3},version:30}))`
> 再 reload，即可复现 R1–R5 的极端档位而不必真的用鼠标拖。**注意 `version` 必须写成 30**，
> 否则会触发 migrate 把这几个键刷回默认值。

| # | 场景 | 判据 |
|---|---|---|
| R1 | 左侧栏拖到最窄 200px，自动交易**停止**态 | 「自动交易 / 已停止 / 开关」一行完整，无折行、无截断 |
| R2 | 同上，自动交易**运行**态（含聚合盈亏） | 一行完整；宽度不够时**只有**「自动交易」出省略号，「运行中」「盈亏」「开关」全部完整 |
| R3 | 左侧栏 200px，`max_ratio` 模式，成员 8/8 | 第 3 行「合计上限 …% ｜ 8/8 ｜ 收益记录」一行完整或仅左侧汇总截断 |
| R4 | 左侧栏在 200↔460 之间连续拖动 20 次 | 全程无任何字被劈开；行高不跳动；无横向滚动条出现在侧栏内 |
| R5 | 右栏拖到 640 + 左栏拖到 460（窗口 1440） | 中栏 ≥ 240px（轴 E）；工具栏段控整块换行、无「15 / m」；OHLC 字段整块换行、无「开 / 104235」 |
| R6 | 窗口从 1920 拖到 860 再拖回 | 全程无字内折行；860 档下最右盘口列被裁切（**已知残留，N3**），但可见部分文字仍完整 |
| R7 | 切实盘模式，下方右栏三个页签逐个看；**并顺手看一眼左侧下单栏**（评审 P1-5） | 10 列的「委托交易」表出现**横向滚动条**而不是折行表头；「随单止盈 / 随单止损」各自一行；持仓表横滚时「一键市价全平」按钮**停在容器宽不跟着滚**属已知副作用（K11），不算缺陷；**左侧下单栏**（`DirectionToggle` / `LeverageControl` / `QuantitySlider` / `TpSlFields`）在 860 窗口 + 右栏 320 的最窄档（左栏 300px）下**目视确认无折行**——若折行，按 §12 纳入下一轮而**不在本轮扩大改动面** |
| R8 | 实盘持仓表展开子委托行 | 子行内的说明文字**仍然正常换行**（Type D 未被误伤） |
| R9 | 「成交记录」表展开触发原因详情（**在 860 窗口 + 右栏 640 的最窄档也看一遍**，评审 P1-7） | `TradeExplainDetail` 内的长段落**仍然正常换行**；而它的指标网格（`MetricsGrid`）**按容器宽掉列**、标签不再被劈成两行（v1 遗漏的第二个视口断点落点） |
| R10 | 右栏拖到最窄 320px，依次看「回测指标卡 / 运行历史 / 多策略对比 / 步进检验 / 组合分体制贡献」 | 五处表头与指标卡标签一行完整；放不下时表格出现**横向滚动条**而不是折行表头；指标卡数值出省略号而不是换行 |
| R11 | 明 / 暗双主题各走一遍 R1–R10 | 视觉一致，无配色回归 |
| R12 | 情报弹窗（7 个动态页签，K4 的观察对象）在 1024 与 1440 两档各看一次 | 页签不折行；若出现横向溢出，按 K4 的处置把 `overflow-x-auto` 下沉到内层 wrapper，**不得**加在 tablist 上（会裁掉 `-mb-px` 下划线） |
| R13 | 桌面端打包产物（`desktop/`）真机跑 R1–R10 | 与 Web 一致（Electron 的 WCO 安全区只影响顶栏，不影响工作台） |
| **R14** | **过约束档位的拖拽手感**（评审 P1-1 新增；R4 覆盖不到这条）：1440 窗口 / 右栏拖到 640 / 侧栏拖到 460 ⇒ 侧栏被压到约 339px。① 抓住侧栏手柄**向右**拖；② 再**向左**拖；③ 把窗口放大回 1920 | ① 手柄**停在原地不跟指针**（直到指针越过 460）——这是 §4.6 判定的**预期行为**，判据是「不跟手但也**不抖动、不闪烁、不把中栏挤破 240px 地板**」；② 立即跟手，无跳变；③ 侧栏平滑回弹到存值 460px，无残留、无横向滚动条 |

### 9.4 完成定义（DoD）

- [x] §5 的 **25** 个源文件改动全部落地，且**每一处 `className` 改动都能对应到 §4.1 的 A/B/C/D/S 五类中的一类**（v2 因评审 P1-2 增加 Type S）。
- [x] 新增 3 个测试文件、修改 1 个测试文件；**T1–T19（含 T9b、终审新增 T6b）**全绿（T15 在 `describe.each` 的两个变体下各跑一次）。
- [x] `noWrapContract.source.test.ts` 的 **G0–G7** 全绿（G7 为终审新增，见 D-4）。
- [x] `components/ui/Tabs.test.tsx` 既有 **17 条**（8 条 × 2 变体 + 外层 1 条；评审 P2-2 订正）、`WatchlistTab.test.tsx` 既有 **9 条**、`LiveFillsTable.test.tsx` 既有 **9 条**、`LiveOpenOrdersTable.test.tsx` 既有 **10 条**、`LivePositionsTable.test.tsx` 既有 **2 条**、`TradesTable.test.tsx` 既有 **4 条**——**一条不改、一条不红**。
- [x] `cd apps/web && NODE_ENV=test pnpm vitest run` 全绿（与改动前的既有红清单逐条比对，不得新增一条）。终审实测 214 文件 / 2472 条，两条红 `Intel/IntelModal.test.tsx` 与 `ui/contrastTokens.source.test.ts` 均来自同一工作树另一并发节点未提交的情报层改动（前者断言的页签「整理记录」已被那一轮改名为「动态」，后者的 offender 是它新建的 `Intel/IntelAlertRow.tsx`），与本轮零关联。
- [x] `pnpm --filter web exec tsc --noEmit` 无新增错误（终审实测整仓 **0 条**）。
- [x] **R1–R14** 人工验收：R1–R12 / R14 通过（真实浏览器 + 真实拖拽，判据为 `Range.getClientRects()` 行盒数），**R13 桌面端打包产物未执行**；报告与截图见 `evidence/r1-r14-headless-acceptance.md`。验收过程发现并修复两个真实渲染缺陷（D-4 / D-5）。
- [x] `grep -rn "whitespace-nowrap" apps/web/components --include=*.tsx | grep -v "\.test\." | wc -l` 从 **9**（评审实测确认；含 1 行注释，实际用法 8 处）升到 **≥ 35**；`grep -rn 'data-nowrap=' apps/web/components | grep -v "\.test\." | wc -l` **恰为 19**（**评审 P0-4 订正**：v1 写 `≥ 20`，但按 §4.3–§4.5 逐条清点 v1 只产出 18 处、v2 为 19 处——下界写高了会逼实现者凭空编锚点，所以这里用等值，且与 G3 的计数表同源）。

---

## 10. 风险与缓解（Risks & mitigations）

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| **K1** | **nowrap 继承误伤散文**——头部加了 nowrap，底下的空态提示 / 错误说明 / 免责文案被拉成一行加省略号，用户读不到关键信息（尤其「为什么按钮点不动」那类门禁原因）。 | 高（信息丢失，比折行更糟） | §4.1 Type D 是契约的**一等公民**而非补丁；§4.3–§4.5 逐处点名了 **14 处**必须显式 `whitespace-normal` 的落点；T8 / T10 / T13 三条用例分别在成交列、成交表、持仓表上钉住「散文仍换行」；R8 / R9 两条人工验收专门走这条路。 |
| **K2** | **表格改 nowrap 后横向溢出但宿主没有 `overflow-auto`**——表格把父容器撑破，或被 `overflow-hidden` 直接裁掉右侧几列，用户看不到「浮动盈亏」。 | 高 | 已逐个核实 8 个宿主：工作台 4 个（`ChartWorkspace.tsx:382` / `LiveBottomPanel.tsx:51` / `TradesTable.tsx:38` / `PaperPositionsTable.tsx:24`）与策略面板 4 个（`StrategyPanel.tsx:496` / `RunHistory.tsx:67` / `WalkForwardResults.tsx:111` / `CompareView.tsx:162`）全部已是 `overflow-auto` / `overflow-x-auto`；**唯一缺宿主的是 `ComboResult.tsx:71`，本轮为它新建一个**（§4.5.3）。T14 / T17 分别冻结这两件事；R7 人工确认横向滚动条真的出现。 |
| **K3** | **`data-nowrap` 属性被当成装饰删掉**，门禁 G2 仍绿（因为它只检查「带属性的必须带 nowrap」），G3 的计数门槛才是真防线。 | 中 | G3 逐文件规定最少出现次数；G0 自证文件读到；两条合起来才构成防线——**实现者不得只写 G2**。 |
| **K4** | **`Tabs` 加 nowrap 后某处页签溢出**（`underline` 变体的 tablist 没有 `overflow-x-auto`，注释里明确说过加了会裁掉 `-mb-px` 下划线）。最长的一处是 `IntelModal` 的 7 个动态页签。 | 中 | 该 tablist 位于 `Modal` 内、宽度由 `max-w-*` 固定，不随工作台四个手柄变化（N6 范围外）；改前它是**折行**、改后是**溢出**，二者都不理想但溢出更容易被发现。**实现时必须在 R10 之外补看一眼情报弹窗**；若确认溢出，按注释建议把 `overflow-x-auto` 下沉到**不承载底边线的内层 wrapper**，不要加在 tablist 上。 |
| **K5** | **轴 E 改变收缩顺序后拖拽手感异常**——左侧栏被压缩时存值 > 渲染宽。 | 中 | `widthFromLeftEdge` 是指针绝对定位、非增量累加，无累积漂移（§4.6 已论证）；R4「连续拖 20 次」专门验这一条。轴 E 与轴 A–D 完全解耦，**可独立回滚**（3 行改动 + G6 一条门禁）。 |
| **K6** | **`AutoTradePanel` 挪按钮改结构，撞坏 `WatchlistTab.test.tsx` 的 9 条既有用例。** | 中 | 已逐条核对：9 条全部以 `role` / `name` / 文本定位，无一条 `container.querySelector` / `parentElement` 结构断言；「收益记录」按钮在**同一个 `<header>` 内**换行，`getByRole("button", { name: /收益记录/ })` 照样命中。T3 额外冻结「点击仍只调一次 `onOpenRecords`」。 |
| **K7** | **文案精简（「（各标的独立）」降级进 `title`）被判为擅自改需求。** | 低 | 已核实全仓无用例断言该串；它与正上方 `ModeToggle` 的「最大比例」是同一件事的重复表述且占 38% 可用宽；语义由 `title` 完整保留。**这是全文唯一一处文案变更，已在 §4.3.4 与 §5 双处标注**，评审若不认可，退化方案是保留原文并把它一并纳入截断范围（不影响其余任何设计）。 |
| **K8** | **改了 24 个文件却漏改第 25 个**——例如 `Strategy/AutoTradeStatusBar.tsx`、`Chart/AiTradeExitCard.tsx` 这类同样住在工作台里的小组件。 | 中 | §5 的清单由「§2.1 布局骨架逐节点展开 + `grep -rn "<table" ` + `grep -rn "sm:grid-cols\|lg:grid-cols"`」三路交叉得出；G5 把视口断点禁令挂在受管清单上；**评审节点（Subtask #1）的首要任务就是复核这份清单的完整性**。已知被**刻意排除**的七类连同理由见 §5 末的排除表（含两个曾误入清单、经核实后剔除的文件：死代码 `AutoTradeTradesTable` 与弹窗内的 `AutoTradePnlPanel`）。<br>**评审结果（v2）：这条风险确实命中了两次** —— ① `Strategy/TradeExplainDetail.tsx` 是根因 4 的第二个落点，已补入受管清单（P1-7）；② 实盘下单栏的四个子组件既没修也没解释，已补入排除表并附算账（P1-5）。另有 `IndicatorMenu` / `ConnectionStatus` 两个文件，改法已调整为**在受管文件内包裹**、从而不必把它们拉进清单（P0-2）。三路交叉法本身是对的，漏的都是「不是 `<table>`、也不在布局骨架主干上、但确实随手柄变宽窄」的那一类。 |
| **K9** | **`min-w-[240px]` 让中栏在极窄窗口下把盘口列彻底挤出可视区**，用户以为盘口面板「不见了」。 | 低 | 盘口列有 `min-w-[160px]` 地板，收缩到底后由 `<main>` 的 `overflow-hidden` 裁切——**与改前行为完全一致**（改前是中栏被裁到 13px，改后是盘口列被裁），只是受害者换了一个。N3 已如实记录；§12 给出后续解法。 |
| **K10** | **Tailwind 任意值 `grid-cols-[repeat(auto-fit,minmax(120px,1fr))]` 写法出错**（含空格需下划线转义）。 | 低 | 该串无空格，Tailwind 3.4.13 直接支持；T9 用正则断言编译前的类名，R5 的截图验证编译后的实际效果。若 JIT 未生成，回退方案是把它写进 `globals.css` 的一条具名 utility。 |
| **K11** | **表格横滚时，与表同处一个 `flex-col` 的兄弟元素不跟着滚**（评审 P2-5）。实测 `LivePositionsTable.tsx:100` 的 `<div className="flex flex-col gap-2">` 里同时装着「一键市价全平」按钮行与 `<table>`；表拿到 `min-w-[640px]` 后横滚，按钮行仍停在容器宽处。 | 低 | 视觉上可接受（按钮本就该常驻可见），但**必须先写进文档**，否则验收时会被当成新缺陷来回排查。R7 的判据里已点名。若日后确认碍眼，做法是把 `<table>` 单独包一层 `overflow-x-auto`，而不是让按钮行跟着滚。 |
| **K12** | **Type S 的引入让「每行唯一 truncate」这条铁律出现例外**（评审 P1-2 的副作用）。实现者可能把 Type S 当成「随便加 truncate 都行」的口子，退回到 v1 那种「谁先被挤谁倒霉」的状态。 | 低 | §4.1 已写明 Type S 的适用前提是「该块本身不是一行」，且块内**每一行仍各自只有一处**吸收点；T9（每个 `Cell` 恰 2 处）、T18、T19 三条用例分别在 `PositionPanel` / `MetricsCards` / 全体锚点上钉住这一点。全仓只有 4 处 `scope`，G3 的等值计数保证它不会悄悄扩散。 |

---

## 11. 实施顺序（Implementation order）

严格按序，**每一步做完先跑一次 `NODE_ENV=test pnpm vitest run` 再进下一步**，避免把 25 个文件的红搅在一起：

1. **契约落文（无代码）**：在 `noWrapContract.source.test.ts` 的文件头写下 §4.1 的**五类行（A/B/C/D/S）与四条铁律**（这是契约的唯一权威副本，spec 只是它的展开说明），先只实现 **G0 + G1**（此时 G1 是红的）。
2. **轴 C-kit**：改 `components/ui/index.tsx` 的 `Tabs` 两处（实测 `:482` / `:508`）→ G1 转绿；追加 `Tabs.test.tsx` 的 T15；跑既有 **17** 条确认全绿。
3. **轴 B**：按 §4.3 改 6 个文件（`AutoTradePanel` 最后改，它结构动得最多）；写 T1–T5；跑 `WatchlistTab.test.tsx` 既有 9 条确认全绿。
4. **轴 C-面板**：按 §4.4.2–§4.4.4 改 3 个文件；写 T6–T7。**先做 `ChartToolbar` 的两层 `<div className="shrink-0">` 包裹（P0-2），否则 T7 必红。**
5. **轴 D-成交列**：`MarketDepthPanel`（**别忘了 `data-testid="market-depth-panel"`，P0-1**）+ `PositionPanel` + `TradeExplainDetail`（P1-7，与 `PositionPanel` 是同一处根因、同一套改法，一起做最省心）；写 T8–T9 + T9b。
6. **轴 D-表格**：9 张表统一套 Type C（工作台 5 张 §4.5.2 + 右侧策略面板 4 张 §4.5.3，各自的 `min-w-[N]` 已给定）。**`colSpan` 落点评审已搜完并写死为四处（§4.5.2 的小表），照着做即可，注意 `PaperPositionsTable.tsx:74` 那处 v1 漏记**；写 T10–T14。
7. **轴 D-右侧策略面板**：4 张表 + `MetricsCards`（§4.5.3）；写 T16–T18；跑 `CompareView.test.tsx` / `StrategyPanel.test.tsx` 既有用例确认全绿。
8. **轴 E**：`ChartWorkspace.tsx` 三行改动；补 G6。
9. **门禁补全**：实现 G2–G5（**G2/G4 用 §9.2 的「切片」取开标签，不要用 `<[^>]*>`，P1-3**）；跑全量确认 `data-nowrap` 计数**恰为 19** 且与 G3 的计数表逐文件一致。
10. **全量回归**：`python scripts/dev.py test`；与改动前的既有红清单逐条比对，**不得新增一条**。
11. **人工验收**：**R1–R14**（R14 是过约束档位的拖拽手感，P1-1），截图归档到 `docs/plans/workstation-text-nowrap-contract/evidence/`。

**回滚粒度**：本轮是纯表现层，五个轴互不依赖，可按轴单独回滚——
· 轴 A（契约文档 + G0/G2/G3）：删门禁文件即可；
· 轴 B / C / D：按文件 `git checkout` 即可，彼此无耦合；
· 轴 C-kit（`Tabs`）：单独回滚会让 `Tabs.test.tsx` 的 T15 与 G1 变红，需一并回滚这两条；
· 轴 E：3 行 + G6 一条，单独回滚零副作用。

---

## 12. 后续（明确不在本轮，供下一轮立项）

1. **窄宽度下的列折叠策略**（N3 / K9 的根治）：上区硬地板 612px 在 860px 窗口下放不下。三个候选——① 上区宽度低于阈值时把盘口列折叠成一条可点开的竖标签；② 桌面端 `minWidth` 从 860 提到 ~1100（会牺牲 1366×768@150% 的高 DPI 小屏，这正是当初降到 860 的原因）；③ 工作台整体允许横向滚动。需要单独立项评估。
2. **把契约扩散到 N6 排除的区域**：设置中心、因子实验室、挖掘台、各类弹窗，以及本轮排除表里的实盘下单栏四个子组件（评审 P1-5，若 R7 目视看到折行则优先做这一处）。做法完全相同（五类行 + `data-nowrap` 锚点），只需把受管清单扩容并让 G3/G5 覆盖新文件。
3. **把契约写进 `CLAUDE.md` 的 Clean Code 章节**（或 `.claude-index/config.md`），让后续所有 AI 与人工改动默认遵守，而不是靠门禁事后拦截。
4. **`Badge` 的 nowrap 评估**：本轮 N1 排除了它（160 处调用），但其中大多数 `children` 也是 2–4 字的闭集短语。若逐点核实后确认无长内容，可用与 `Tabs` 同款的理由收编。


---

## 评审结论（Review Verdict）

### 结论：**有条件通过**

v1 是一份质量在本仓库平均线之上的方案：根因分析（四条）成立且逐条可复现，四类行契约是**结构性**解法而不是补丁堆砌，
`N1`–`N6` 六条非目标写得克制且都给了理由，K1–K10 十条风险里有八条命中了真实的失败模式，
受管清单的三路交叉法、八个滚动宿主的逐个核实、`Tabs` 13 个调用点的闭集论证、
`store` 的 `version: 30` 与 Tailwind `plugins: []` 这些底层事实**全部实测无误**。
方案的尺寸也是对的——它没有去做容器查询、没有去改 `Button` / `Badge` 基类、没有顺手删死代码、
没有把 N3 那个真正的布局策略问题夹带进来，这几处克制都判断正确。

**但 v1 不能直接进入实施**，因为它有四条会**让 DoD 自身无法达成**的缺陷（P0-1 ~ P0-4），
且这四条里有三条是同一个形状：**「有断言点／有门禁，却没有对应的写入点」**——
`market-depth-panel` 的 testid、`PaperPositionsTable` 的第四处 `colSpan`、`data-nowrap` 的锚点数量，
都是「测试或门禁在等一个正文从未要求写下的东西」。这正是本仓库共享看板反复登记的那个缺陷形状，
它的危险之处在于：实施者会先把用例写出来、看到红、然后**倒过来改用例去迁就实现**，
于是本轮真正想冻结的契约就被悄悄放宽了。

以上 **P0 四条、P1 七条已在 v2 正文逐条改完**（见《评审记录》的「处置」列），P2 六条中四条顺手修正、两条留作记录。

### 通过条件（实施节点必须逐条兑现，缺一条即视为未完成）

1. **C1 · 先写入点、后断言点。** 实施顺序必须按 v2 的 §11 走：`data-testid="market-depth-panel"`（P0-1）、
   `ChartToolbar` 的两层 `shrink-0` 包裹（P0-2）、`PaperPositionsTable.tsx:74` 的 `whitespace-normal`（P0-3）
   三处**必须先落地**，再写对应的 T8 / T7 / T10。**任何一条用例因为「实现没跟上」而被改写或删除，都要回到本文档
   把契约一并改掉并说明理由**，不允许只改用例。

2. **C2 · G3 是等值计数，不是下界。** `data-nowrap` 全仓**恰为 19 处**，且逐文件与 §9.2 的计数表一致。
   实施中若因结构调整多出或少掉一处，**先改本文档的计数表再改代码**——这张表是本轮唯一能挡住
   「锚点被当装饰删掉」的防线（K3 已论证 G2 挡不住）。

3. **C3 · G2 / G4 的开标签必须用「切片」取，不得用 `<[^>]*>`。**（P1-3）JSX 里的 `=>` 会让后者提前截断，
   造成假红。切片规则写在 §9.2 G2 里，实现时直接照抄。

4. **C4 · 轴 E 的死区必须由 R14 亲自走一遍。**（P1-1）R4 在 1440 默认窗口下验不到它。
   R14 若发现的不是「不跟手但稳定」而是「抖动 / 闪烁 / 中栏被挤破 240px 地板」，
   **立即单独回滚轴 E 的三行 + G6**（本轮五个轴解耦，轴 E 回滚零副作用），其余四轴照常合入。

5. **C5 · 排除表里的实盘下单栏四个组件必须真的看一眼。**（P1-5）R7 增加的那条目视确认不是形式——
   §5 里给出的是**估算**而非实测。看到折行就记进 §12 交下一轮，**不要在本轮临时扩大改动面**
   （那会让「25 个文件 + 19 个锚点」这套等值门禁全部作废）。

6. **C6 · Type S 不是「随便加 truncate」的口子。**（K12）它的适用前提只有一个：
   该块本身不是一行。块内每一行仍然各自只有一处吸收点。全仓只允许 4 处 `scope`，G3 的等值计数负责兜底。

7. **C7 · 全量回归以「不新增一条红」为准，不得以「-k 子集全绿」代替。**
   §9.4 已列出六个既有测试文件的确切条数（`Tabs` 17 / `WatchlistTab` 9 / `LiveFillsTable` 9 /
   `LiveOpenOrdersTable` 10 / `LivePositionsTable` 2 / `TradesTable` 4，评审已逐个实测），
   跑完拿它们逐条对账。

### 尚存但**明确接受**的残余（不构成阻断）

- **N3 / K9：上区硬地板 612px 在 860px 窗口下仍会裁掉盘口列的一部分。** 评审复核了这个算术，结论与 v1 一致：
  任何 CSS 排布都放不下，属布局策略问题，本轮承诺的是「只会看到省略号，不会看到被劈开的汉字」——这个承诺成立。
- **K11：表格横滚时同一 `flex-col` 里的兄弟元素不跟着滚。** 视觉可接受，已入档。
- **P2-1：全文行号有 1~4 行漂移。** 已加全局口径声明；实施以锚点文本为准。
- **`Badge` 未收编（N1）。** 160 处调用、`children` 不可控，本轮用行级继承覆盖，理由成立；留作 §12 第 4 条。

> 评审人：Subtask #1（设计评审节点） · 评审对象：`spec.md` v1 · 产出：`spec.md` **v2**
> 本次评审只改本文档，未触碰任何源码，未执行 `git commit`。

---

## 实施过程发现的方案缺陷（Issues Found During Implementation）

> 实施节点（Subtask #2）按 v2 正文与 §11 顺序逐条落地时命中的**方案自身**缺陷。
> 每条都写明：在哪一节、按字面实现会怎样、实际采用的修正、以及它是否改变了 v2 承诺的门禁口径。
> **没有一条是「改用例去迁就实现」**（C1）：三条全部落在方案文本上，代码按修正后的口径写。

| 编号 | 级别 | 节 | 问题（附实测） | 实施采用的修正 |
|---|---|---|---|---|
| **D-1** | P0 | §9.2 G2 的切片规则 → G4 | **切片规则在 `<table` / `<thead` / `<td` 上退化为空串，G4 按字面实现必红。** G2 给出的确定性取法是「以锚点下标 `i` 为锚，`slice(src.lastIndexOf("<", i), 下一个 "<" 的下标)`」。对 `data-nowrap=` 这种**不以 `<` 开头**的锚点它是对的；但 §9.2 G4 把同一条规则用在 `<table` / `<thead` / `<td colSpan` 上，而这三处的锚点**自己就是那个 `<`** ⇒ `lastIndexOf("<", i) === i === indexOf("<", i)` ⇒ 切片恒为**空串** ⇒ 「`<table>` 缺 `w-full`」必红（实测第一次跑 G4 即是这条：`expected '' to contain 'w-full'`）。 | 切片的**向后**一端改为从 `start + 1` 起找下一个 `<`（`src.indexOf("<", start + 1)`），两类锚点统一。对 `data-nowrap=` 结果与 v2 原文完全一致（同一个开标签），对 `<xxx` 锚点才正确。已写进 `noWrapContract.source.test.ts` 的 `openTagAt()` 注释。**门禁口径不变**。<br>另附**反向验证**：把 `AutoTradeMemberRow.tsx` 行根的 `whitespace-nowrap` 删掉跑一次，G2 如期变红——那一行的开标签里正好带着 `onDragStart={(e) => …}`，正是评审 P1-3 说的箭头函数场景，证明这条切片规则确实挡得住、且不是假绿。 |
| **D-2** | P1 | §4.5.5 与 §9.2 G3 计数表 | **`LiveTradePanel` 的锚点数自相矛盾：正文只指得出 1 处，计数表要 2 处。** §4.5.5 写「`LiveTradePanel.tsx:219` 的**两个 `SegToggle` 行**补 `whitespace-nowrap` + `data-nowrap="row"`」，而 `:219` 实测是**一个** `<div className="flex items-center gap-2">`、里面装着两个 `<SegToggle>`——按字面只能产出 **1** 处锚点；G3 计数表却写 `Live/LiveTradePanel.tsx = 2`，且 §9.4 要求全仓**恰为 19**。二者不可能同时满足。 | 取「两个 `SegToggle` **行**」的字面语义：锚点落在 **① `:219` 的行容器**与 **② `SegToggle` 组件根**（`:365`，源码 1 处、渲染 2 次，与 §9.2 对 `FilterChips` 的「源码 1 处、渲染 2 次记 1」口径同源）各一处 ⇒ 该文件 **2** 处，全仓实测**恰为 19**，G3 逐文件与 §9.4 的等值断言均成立（C2 未被放宽）。<br>另按 §4.5.5 的 P2-4 注记，**未**给 `SegToggle` 根补那个空操作的 `shrink-0`（正文用词是「仍**可**为契约一致性写上」，非必须），以免让后人以为它解决了什么。 |
| **D-3** | P2 | §9.1 D 表 T16 | **T16 对 `CompareView` 的断言缺一步 `await`，按字面写必红。** T16 写「逐个渲染 → `container.querySelector("table")`」，但 `CompareView.tsx:102` 有一条前置返回：`if (!strategies) return <div>加载策略…</div>`——`api.strategies` 是异步 query，同步断言时表格**根本不存在**。（其余三张表 `ComboResult` / `WalkForwardResults` 是纯 props 组件，同步断言成立；`RunHistory` 同样需要等 `api.runsList` 落地。） | T16 里对 `RunHistory` 与 `CompareView` 各补一次 `await screen.findBy…` 再断言；`compare` 结果用 `qc.setQueryData(compareKey, …)` 预置（`CompareView` 的对比查询是 `enabled:false + initialData:null` 的响应式订阅，预置缓存即可命中，不必真的触发 mutation）。断言内容与 v2 完全一致。 |

**另外三处「正文没点名、但按 §4.1 Type D 的防御性自证顺手写下」的改动**（各 1 个类名，均落在本来就要改的受管文件内，不扩大改动面）：
`TradesTable` / `PaperPositionsTable` / `RunHistory` 三处空态 `<div>` 补 `whitespace-normal break-words`。
理由与 §4.3.1 对 `InstrumentSidebar` 三段的处置同源：它们与刚拿到 `whitespace-nowrap` 的 `<thead>` / 数据 `<tr>` 同处一棵子树，
后人若把 nowrap 上提到表容器上，「暂无成交记录，运行回测或启动模拟后显示。」这类**必须读全**的话会瞬间被截成一行。
（§5 变更计划第 12 行本就写了「空态 Type D」，另两处是把同一条规则补齐。）

**跑测结果（实施节点自测，与 §9.4 逐条对账）**

- `cd apps/web && NODE_ENV=test npx vitest run` —— **214 个文件 / 2469 条**，本轮新增 26 条（T1–T19 共 15 条 + T11–T14 共 4 条 + G0–G6 共 7 条）全绿，T15 在两个变体下各跑一次。
- 既有条数逐个复核，**一条不改、一条不红**：`Tabs` 17（+T15 → 19）/ `WatchlistTab` 9 / `LiveFillsTable` 9 / `LiveOpenOrdersTable` 10 / `LivePositionsTable` 2 / `TradesTable` 4。
- 全量唯一一条红是 `lib/experienceLabels.source.test.ts`，**与本轮无关**：它比对前后端中文词表，而工作树里 `services/quant/app/ai_auto/intel/taxonomy.py` 有另一节点**未提交**的新增取值 `intel_alert`（`git show HEAD:…/taxonomy.py` 里出现 **0** 次、工作树里 **11** 次）。本轮零后端改动，未做任何处置。
- `npx tsc --noEmit`：本轮 28 个文件零错误（改完当时整仓干净）。此后同一工作树里出现了另一节点新建的未跟踪文件 `apps/web/lib/intelActionability.ts`，它引用 `intelTypes.ts` 中不存在的 `IntelAlert` / `IntelConflict` / `IntelEfficacy` / `IntelStat` 与 `IntelSourceRow.accuracy`，使 `tsc` / `next build` 产生 6 条错误——**全部集中在那一个文件**，同样与本轮无关。
- `grep -rn "whitespace-nowrap" apps/web/components --include=*.tsx | grep -v "\.test\." | wc -l` = **54**（DoD 要求 ≥ 35）；`grep -rn 'data-nowrap=' apps/web/components | grep -v "\.test\." | wc -l` = **19**（DoD 要求恰为 19）。
- **R1–R14 人工验收（含截图归档）未在本节点执行**：它需要起 dev server + 真实拖拽（R4 / R14 的判据是「跟手 / 不抖动」，无头截图证不了），§9.3 已把它定义为**终审门**。本节点交付的是 §5 的 30 个变更条目与两道自动化门禁。

---

## 终审发现的实现缺陷（Issues Found During Final Review）

> 终审节点（Subtask #3）执行 §9.3 的 R1–R14 时命中的**实现**缺陷。两条都是 jsdom
> **原理上**察觉不到的一类（判据是像素几何而非类名字符串），且**都由本轮改动引入**。
> 已在源码修好、各配一道门禁并做反向验证；完整报告见
> `evidence/r1-r14-headless-acceptance.md`，原始度量见 `evidence/r1-r14-audit.json`。

| 编号 | 级别 | 落点 | 问题（附实测） | 修法与门禁 |
|---|---|---|---|---|
| **D-4** | P1 | §4.4.3 `ChartHeaderBar.tsx` | **`shrink-0` 把 OHLC 组钉死在 max-content，它自己的 `flex-wrap` 永远轮不到触发。** 实测 `chart-header-bar` 的 `scrollWidth` 在任何容器宽下恒为 **642px**：中栏 634 时溢出 8px、414 时溢出 228px、240 时溢出 **402px**。这与 R5 判据「OHLC 字段**整块换行**」正好相反 —— 退化方式从「掉行」变成了「横向溢出」。 | 两个组各补 `max-w-full`（与同文件 `paperStrategies` 组的既有写法同源），修后九档 `cw == sw` 全部成立。契约**新增第 5 条铁律**「本身还会回流的条目，`shrink-0` 必须与 `max-w-full` 同行出现」，由源码门禁 **G7** 与组件用例 **T6b** 各守一道。反向验证：删掉任一处 `max-w-full`，G7 与 T6b 同时变红。 |
| **D-5** | P1 | §4.6 E-2 / §10 K9 | **240px 地板没有配套裁切，盘口列盖在右侧策略面板上。** K9 写的退化是「由 `<main>` 的 `overflow-hidden` 裁掉一部分」，实测**不成立**：`<main>` 在上区行**之外**且比它宽（860 窗口下 main 812 / 上区行仅 426）。E-2 之后三列硬地板合计 **612px** > 426px ⇒ 溢出 186px，盘口列右边缘 x=660 而右侧 `<aside>` 左边缘 x=480 —— **重叠 180px**，「disconnected / 24h Vol」直接压在「交易策略」上。 | 上区三列行（`ref={topRef}`）补 `overflow-hidden`，让 K9 那句承诺成立；修后 860 档行右边缘 474 / `aside` 左边缘 480，不再重叠。**G6 扩一条**：`ref={topRef}` 的开标签必须含 `overflow-hidden`（**地板与裁切必须成对存在**）。已实测代价：860 档打开「指标」下拉时面板右边缘被切掉约 76px，七个条目与「恢复默认」全部可读可点（`evidence/menu-860.png`）；改前该下拉同样越界，只是盖在右侧面板上而非被裁。 |

**这两条共享一个形状，值得写进后续评审清单**：`shrink-0` 与 `min-w-[Npx]` 都是**只声明下界、不声明上界**的约束。
声明下界的一方必须同时回答「下界之和放不下时谁让步、往哪儿退」——
D-4 的答案是 `max-w-full`（退给自己的 `flex-wrap`），D-5 的答案是 `overflow-hidden`（退给裁切）。
不回答的话，退化路径就默认是「横向溢出并覆盖邻居」，而这恰恰是 jsdom 用例与源码门禁**都**看不见的那一种。

**终审复核结论（非缺陷，记录以免重提）**

1. **轴 E 无需回滚（C4）。** R14 实测：侧栏存值 460 / 实渲 334（「不跟手」为 §4.6 预期），
   全程无抖动、无闪烁，中栏稳定停在 240 未被挤破，文档零横向溢出；R4 连续 20 次拖拽精确落在
   200 / 460，零漂移，侧栏内横向滚动量恒为 0。C4 的三个回滚触发条件一个都没命中。
2. **`ChartToolbar` 在中栏 240px 处被裁不是新缺陷。** 七个周期段控是一个不可分割的条目（264px）
   而容器只有 208px，属 Type B 声明的退化方式与 N3 已接受档位的交集；改前该档中栏实宽仅 13px，
   整条工具栏溢出得更远。
3. **`MarketDepthPanel` 买卖档的 `truncate` 在 `grid-cols-[1fr_1fr]` 下不会触发**（`1fr` 的
   min 轨道是 auto），但实测该面板在 160px 地板处 `scrollWidth == clientWidth`、零折行 ——
   契约承诺的是「不折行」，这一条成立，故**判定为不改**（改成 `minmax(0,1fr)` 会把「完整价格」
   换成「省略号价格」，是净损失）。
