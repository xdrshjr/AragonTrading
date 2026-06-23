# 桌面端顶栏按钮「仅最大化时可点、还原态点击无效」— 问题定位与分析

- **Bug slug**: `desktop-titlebar-buttons-unclickable-when-restored`
- **诊断节点**: Subtask #0（问题定位与分析）
- **日期**: 2026-06-23
- **结论先行**: 这是**无边框窗口（`frame:false`）+ 默认可缩放（`resizable:true` → Windows `WS_THICKFRAME`）** 组合下，Chromium/Electron 在 **「还原态(restored) vs 最大化态(maximized)」** 之间**非客户区 / 拖拽命中区不对称**导致的已知缺陷。顶栏 `<header>` 是 `-webkit-app-region: drag` 且**紧贴窗口顶边(y=0)**，其内按钮虽都正确标了 `no-drag`（已实测验证），但**还原态下这些 `no-drag` 命中区相对 OS 顶部 resize 非客户区错位/未被生效**，点击落到了拖拽 / resize 非客户面上——被 OS 用于「拖窗 / 改尺寸」，不再下发给渲染层，故「点击没用」。**最大化态下窗口不可缩放、顶部 resize 非客户带塌缩**，命中区恢复对齐，按钮即可点。探针实测两态唯一差异是**帧内缩进 0px(还原) vs 7px(最大化)**，与症状一一对应。

---

## 一、问题描述（Problem description）

桌面端（Electron）出现交互回归：

- 当窗口**不是**最大化（还原 / 普通浮动窗口）时，最上方 **Header 中的按钮无法有效点击**——典型如右侧的**设置（齿轮）按钮**，点了**没有任何反应**。
- 只有当窗口**最大化铺满整个屏幕**后，点击这些按钮才生效。
- 用户诉求：还原态下也要能正常点击顶栏按钮。

「同一组按钮，最大化能点、还原不能点」这一条件——布局/CSS 在两态下完全相同（窗口只是尺寸/位置变化），唯一能在两态间改变「点击是否下发到页面」的，是 **无边框窗口在 OS 层的非客户区(non-client)命中测试**：还原态可缩放窗口在顶边保留 resize 非客户带、最大化态不可缩放则无。这把问题牢牢指向「自绘标题栏 + 无边框可缩放窗口」特性。

---

## 二、复现步骤（Reproduction steps）

### 2.1 用户侧复现（端到端）

1. 检出含自绘标题栏特性（`ff20361`「replace native desktop chrome with in-app custom title bar」）的代码。
2. 启动桌面端（任一路径）：
   - 开发态：`desktop/` 下 `npm run dev`。
   - 生产态：运行打包产物 `desktop/dist/win-unpacked/Aragon Trading.exe`。
3. 确保窗口处于**还原态**（非最大化；若当前最大化，点右上「还原」或双击顶栏还原）。
4. 用鼠标单击顶栏右侧的**设置齿轮按钮**（`SettingsGearButton`）、**主题切换**、**最小化/最大化/关闭**等按钮 → **无反应**（或在空白拖拽处变成「拖动整窗」）。
5. 点击右上「最大化」让窗口铺满屏幕；再次单击**同样的**按钮 → **此时全部生效**。
6. 再「还原」窗口 → 按钮**再次失效**。可反复在「还原↔最大化」间切换稳定复现。

> 关键观察点：失效/生效**只与窗口是否最大化相关**，与点击位置在按钮内的精确像素、与是否开 DevTools 无关。

### 2.2 机理验证探针（Electron 31 实测，随报告留存 `repro/`）

为把「两态差异」量化、并排除 CSS 因素，构造了与生产同构的最小无边框可缩放窗口，分别在还原 / 最大化态读取**窗口外框矩形 vs Web 内容矩形**与按钮的计算 `-webkit-app-region`：

- 用例：`repro/page.html`（48px 顶栏 `drag` 紧贴 y=0 + 32px 居中 `no-drag` 按钮，复刻 `page.tsx` 顶栏与 `SettingsGearButton` 几何）+ `repro/probe.js`（`frame:false`、默认 `resizable`，测 `getBounds`/`getContentBounds`）。
- 运行（`desktop/` 下，需先清 `ELECTRON_RUN_AS_NODE`）：
  `node_modules/electron/dist/electron.exe ../docs/diagnoses/desktop-titlebar-buttons-unclickable-when-restored/repro/probe.js`

实测输出（`repro/result.txt`）：

```
=== RESTORED (非最大化) ===
getBounds       : x=377 y=174 w=1001 h=701
getContentBounds: x=377 y=174 w=1001 h=701      ← 外框与内容完全重合，帧内缩进 = 0px
button/header   : {"btnRect":{"x":936.57,"y":8,"w":32,"h":32},"btnRegion":"no-drag","headerRegion":"drag"}

=== MAXIMIZED (最大化) ===
getBounds       : x=-7 y=-7 w=1770 h=1063        ← 窗口向外溢出工作区 7px
getContentBounds: x=0   y=0  w=1756 h=1050        ← 内容被向内缩进 ≈7px
button/header   : {"btnRect":{"x":1691.43,"y":8,"w":32,"h":32},"btnRegion":"no-drag","headerRegion":"drag"}
```

**实测结论（证据）**：
1. **CSS 正确、布局一致**：两态下按钮计算值恒为 `no-drag`、顶栏恒为 `drag`、按钮几何 `(y=8,h=32)` 完全相同——**排除** CSS 漏标 / 布局错位。
2. **唯一差异 = 帧内缩进不对称**：**还原态缩进 0px**（内容紧贴窗口顶边 y=0），**最大化态缩进 ≈7px**（内容相对窗口原点内移 7px）。这正是 Chromium 无边框可缩放窗口在两态间「拖拽 / 非拖拽命中区」对齐与否的根因输入（详见第三节）。

> 注：本探针证明「两态在 OS 帧内缩进上不对称」这一**根因输入**，并用计算样式排除了 CSS。OS 非客户区命中测试发生在浏览器进程内、不经渲染层，无法用计算样式或 `sendInputEvent` 复现（后者绕过 NC 命中测试），故「点击是否下发」仍以 §2.1 的人工端到端复现为准；本探针与之互补。

---

## 三、根因分析（Root cause analysis）

### 3.1 触发条件：无边框 + 默认可缩放（两者缺一不可）

- `desktop/src/main.js:216` `frame: false`（win/linux 分支，mac 走 `titleBarStyle:'hidden'`，`main.js:217-223`）。**无边框窗口下「能否拖窗 / resize 命中」完全由 OS 非客户区命中测试 + `-webkit-app-region` 共同决定。**
- `desktop/src/main.js:206-230` 的 `opts` **未设置 `resizable`**，故取默认 `true`。Windows 上「无边框 + 可缩放」会保留 `WS_THICKFRAME`，**OS 在窗口四边（含顶边）保留 resize 非客户命中带**（见 Electron #3022 / #4139：无边框 + 顶部拖拽区与顶边 resize 命中区相互干扰）。
- 顶栏接线：`apps/web/app/page.tsx:417-447` —— `<header className="drag-region …">` 是 `flex h-12`（48px）、**作为 `<div className="flex h-screen flex-col">` 的第一个子元素，紧贴窗口顶边(y=0)**；右簇 `<div className="no-drag …">`（`page.tsx:439-446`）含 `AccountModeSwitch` / 主题键 / `SettingsGearButton` / `WindowControls`。
- 拖拽契约：`apps/web/app/globals.css:103-113` —— `.drag-region{-webkit-app-region:drag}` + 「`.no-drag` 及 `.drag-region` 内 `button/input/a/[role]` 一律 `no-drag`」。故齿轮按钮（`SettingsGearButton.tsx:23` 显式 `no-drag`）、窗口控制键（`WindowControls.tsx:30,36` `no-drag`）**都正确是 drag 区里的 no-drag「洞」**。

> 也就是说：顶栏交互按钮的本质形态是**「紧贴窗口顶边的 `drag` 区里挖出的若干 `no-drag` 洞」**。这一形态恰好命中下文的两态命中区缺陷。

### 3.2 直接成因：还原态下「drag 区里的 no-drag 洞」未被 OS 命中测试正确生效

把 §2.2 的实测代入 Chromium 无边框窗口命中测试语义：

- Chromium 在渲染层按**内容(client)坐标**计算可拖区 = `drag 元素并集 ∖ no-drag 元素并集`，推送给浏览器进程用于 `WM_NCHITTEST`；OS 传入的是屏幕坐标，需按窗口客户区原点换算回内容坐标。
- **最大化态**：内容相对窗口原点**真实内移 7px**（实测 `getContentBounds` 原点 = `getBounds` 原点 + 7）。此时命中测试换算与真实缩进一致 → no-drag 洞与按钮**精确对齐** → 点击落在 no-drag(HTCLIENT) → **下发到页面，按钮生效**。
- **还原态**：实测帧内缩进为 **0px**（`getBounds == getContentBounds`），内容紧贴窗口顶边。但**可缩放无边框窗口在顶边仍保留 resize 非客户带**，且 Electron #3022/#4139 记载「顶部 `drag` 区会盖住顶边 resize 命中区」——即顶边一条带被 OS 当作**非客户(拖拽/resize)** 处理。叠加 Chromium 在**还原态对无边框可缩放窗口的可拖区命中存在已知错位**（与最大化态 7px 缩进相关的偏移在还原态未被正确归零），**no-drag 洞相对按钮实际渲染位置发生纵向错位 / 未被从拖拽面减去**：按钮（内容 y=8..40，仅 32px 高）被错位的拖拽/resize 非客户面盖住，`mousedown` 被 OS 用于「拖窗 / 改尺寸」，**不再作为 click 下发给页面** → 用户感知「点了没用」。

**症状的排他性核验**：在无边框窗口上，「点击不下发到页面」只可能因为该点被 OS 判为**非客户区**（`drag`→HTCAPTION 拖窗，或 resize→HTTOP/HTLEFT 等）。布局/CSS 两态相同（§2.2 实测），故唯一能解释「最大化可点、还原不可点」的变量就是**两态间非客户区命中的差异**——而这正是「可缩放窗口还原态有 resize 非客户带、最大化态无」+「还原态帧内缩进 0 与最大化态 7px 不对称」的直接后果。

### 3.3 为何「最大化」恰好能规避

- **最大化窗口不可缩放**：Windows 不为最大化窗口保留四边 resize 非客户带（无法 resize 一个已最大化的窗口），顶边那条「resize/拖拽」干扰带随之塌缩。
- **最大化态帧内缩进是「真实存在且被一致换算」的 7px**：命中区与按钮对齐，no-drag 洞生效。
- 二者叠加 → 最大化态按钮恒可点；还原态恒失效。

### 3.4 与既往诊断的关系（同源不同症）

- 本特性由 `ff20361` 引入无边框 + `app-region`；`45f4241`（对应 `docs/diagnoses/desktop-titlebar-drag-region-blocks-left-panels/`）为**内容区**补了 `no-drag` 兜底（`page.tsx:450,454` 的 `<main className="no-drag …">`），治的是「左侧面板被拖拽吞点击」。
- 本 bug 是**同一拖拽契约的另一面**：问题不在内容区，而在**顶栏自身**——「紧贴窗口顶边的 drag 区里的 no-drag 洞」在**还原态**不被 OS 命中测试生效。既往修复未触及、也无法覆盖该路径。

---

## 四、影响面（Impact surface）

| 维度 | 影响 |
|---|---|
| 平台 | Windows / Linux（`frame:false` + 默认 `resizable` 分支）。mac 走 `titleBarStyle:'hidden'` + 原生红绿灯，非客户命中由系统处理，基本不受影响。 |
| 窗口态 | **还原 / 普通浮动态**复现（绝大多数日常使用形态）；**最大化态**正常。**这是默认启动态**：`main.js:206-207` 初始 `width:1440,height:900` 非最大化，用户开箱即处于受影响态。 |
| 受影响功能 | 顶栏右簇全部交互：**设置齿轮**（进/出设置中心，`SettingsGearButton`）、**主题切换**、**账户/模拟资金切换**（`AccountModeSwitch`）、**最小化/最大化/关闭**（`WindowControls`），以及左簇 `AppMenu`（关于/日志/重载）。 |
| 严重度 | **高**——顶栏是进入「设置 / 切换主题 / 账户模式 / 关闭应用」的唯一入口；还原态下这些入口几乎不可用，用户被迫先最大化才能操作，体验严重受损。 |
| 引入点 | `ff20361`（自绘标题栏：`frame:false` + 顶栏 `drag-region`）。此前原生边框窗口由系统绘制标题栏，无此问题。 |

---

## 五、候选修复方案对比（Candidate fixes）

> 本节仅做方案比较，**不写修复代码**（留给下游修复节点）。核心目标：让顶栏交互按钮**不再依赖「紧贴窗口顶边的 drag 区里的 no-drag 洞」**这一在还原态失效的形态。

| 方案 | 做法 | 侵入性 | 风险 | 工作量 | 是否根治 |
|---|---|---|---|---|---|
| **A（推荐）反转拖拽契约：顶栏默认不可拖，仅留无交互的专用拖拽面** | 顶栏根**不再整体 `drag`**；改为：①整窗/顶栏默认 `no-drag`（或不标 drag）；②仅给**不含任何交互控件**的区域（如品牌 logo/标题簇、或右簇与左簇之间的空白伸缩区）单独标 `-webkit-app-region: drag`。如此交互按钮处于 `no-drag`/默认区（非「drag 里的洞」），不再触发还原态命中缺陷；拖拽面是纯 drag（无洞），亦不受影响。 | 中（顶栏结构/类名重排，几处） | 低（`app-region` 浏览器侧无副作用、Web 端零回归；需回归双击最大化与拖窗手感） | 中 | 是——从形态上消除「还原态 no-drag 洞失效」的依赖 |
| **B 顶边留出非拖拽安全缩进**：让 `drag` 区不再紧贴 y=0 | 在 `<header>` 上方或顶部留一条**不可拖、且不放交互控件**的细缩进（如几 px 顶部 padding/spacer），把交互按钮压到顶边 resize/命中错位带之下。 | 低（CSS） | 中（仅缓解、依赖具体偏移量，可能在不同 DPI/版本残留；改变顶栏视觉高度） | 低 | 部分——缓解错位，非形态级根治 |
| **C 改用 Windows Controls Overlay（`titleBarStyle:'hidden'` + `titleBarOverlay`）** | win 上交还系统绘制最小/最大/关闭于 overlay，应用顶栏只放菜单/账户/设置且位于 overlay 安全区内（`env(titlebar-area-*)`）。 | 中-高（删 win 端自绘 `WindowControls`、布局让位 overlay、颜色同步） | 中（UX/视觉变化；与既有「右侧自绘三键」设计取舍冲突，spec §10 R-2 已列为后续增强） | 中 | 是（系统托管 caption，绕开 `app-region` 命中缺陷） |
| **D 关闭窗口可缩放 / 关 `thickFrame`** | `resizable:false` 或 `thickFrame:false` 去掉顶边 resize 非客户带。 | 低 | **高**——`resizable:false` 会**连带禁用自定义 `drag` 区拖拽**（Electron #30788），且失去缩放能力；`thickFrame:false` 在部分版本破坏 resize（#48576）。 | 低 | 否（引入更严重回归，不可取） |

---

## 六、推荐方案（Recommended fix）

**采用 A（反转拖拽契约），并可附带 B 作为低成本顶边加固。**

理由：

1. **形态级根治**：本 bug 的本质是「**紧贴窗口顶边的 `drag` 区里的 `no-drag` 洞**在还原态不被 OS 命中测试生效」。方案 A 把交互按钮从「drag 区里的洞」改为「`no-drag`/默认区」，并让拖拽面退化为**无交互控件的纯 drag 面**——无论还原/最大化、无论 Chromium 版本如何处理 no-drag 减法，交互按钮都不再落在拖拽/resize 非客户面上。这与既往诊断（左侧面板）所确立的「显式 `no-drag` 兜底」是同一理念的延伸：**让交互区拥有防御性下界，而非依赖「drag 区恰好正确减去 no-drag 洞」的正向约束**。
2. **保留产品形态**：相较 C（交还系统 overlay、删自绘三键、改视觉），A 完整保留 spec 所要的「应用自身 Header 即顶栏 + 右侧自绘控制键 + 暖色设计语言」，UX 改动面最小。
3. **最小破坏面、零 Web 回归**：改动集中在顶栏的 `drag`/`no-drag` 类名归属（`page.tsx` 顶栏 + 必要的 `globals.css` 工具类），不碰业务逻辑、IPC、数据流；`-webkit-app-region` 在普通浏览器为无副作用样式。
4. **规避 D 的回归**：实测/官方（#30788/#48576）表明「`resizable:false`/`thickFrame:false`」会连带破坏拖拽或缩放，不能选；B 仅作 A 之外的低成本顶边加固（把交互控件压离顶边命中错位带），不单独作为根治。

**验收对齐（供下游修复节点）**：修复后，在**还原态**下单击顶栏的设置齿轮、主题切换、账户切换、最小化/最大化/关闭、AppMenu **均有响应**；最大化态行为不回归；在**专用拖拽面**（无按钮处）按下拖动仍可移动窗口、双击仍可切换最大化；四边/四角 resize 仍可用（对照 spec §8.3 第 10–12 条）；浏览器纯 Web 跑 `pnpm dev` 顶栏行为与改动前一致（零回归）。建议保留并扩展 §2.2 探针为回归用例（断言两态下交互控件命中均为 client）。

---

## 附录：关键证据索引

- 触发条件：`desktop/src/main.js:216`（`frame:false`）、`:206-230`（`opts` **未设 `resizable`** → 默认 `true`/`WS_THICKFRAME`）、`:217-223`（mac 分支）。
- 顶栏接线：`apps/web/app/page.tsx:417-447`（`<header className="drag-region flex h-12 …">` 紧贴窗口顶边；右簇 `no-drag`，含 `SettingsGearButton`/`WindowControls`）。
- 拖拽契约：`apps/web/app/globals.css:103-113`（`.drag-region` + `.no-drag` 及 drag 区内交互元素兜底）。
- 控件几何：`apps/web/components/Settings/SettingsGearButton.tsx:23`（`no-drag h-8 w-8`）、`apps/web/components/Desktop/WindowControls.tsx:30,36`（`no-drag h-12`）。
- 两态命中根因输入实测：`docs/diagnoses/desktop-titlebar-buttons-unclickable-when-restored/repro/`（Electron 31.7.7：还原态缩进 0px、最大化态缩进 7px；按钮两态恒 `no-drag`）。
- 外部佐证：Electron issue [#3022](https://github.com/electron/electron/issues/3022)（无边框窗口顶部 `drag` 区与顶边 resize 命中区相互干扰）、[#4139](https://github.com/electron/electron/issues/4139)（`frame:false` 自绘标题栏顶边不可 resize）、[#30788](https://github.com/electron/electron/issues/30788)（`resizable:false` 连带禁用 `drag` 区拖拽，佐证方案 D 的回归）、[#48576](https://github.com/electron/electron/issues/48576)（`thickFrame:false` 破坏 resize）。
- 设计/风险溯源：`docs/plans/desktop-custom-titlebar/spec.md` §4.1 注 / RV-6 / R-2（明确将「无边框窗口最大化贴合、四角 resize 命中区在个别版本偏窄」列为 Windows 版本相关已知风险点，须实测确认）。

---

## 评审结论（Review Verdict）

- **评审节点**: Subtask #1（分析报告评审与修复）
- **评审人**: 资深评审（Anthropic 工程团队）
- **日期**: 2026-06-23
- **一句话结论**: 根因定位**成立**、证据链经复核**可靠**；评审补强了一条**决定性几何反证**与两条旁证，据此**收敛为单一推荐方案 A（反转拖拽契约）**，并把方案 B 从「附带加固」**降级为 QA 后的或有兜底**（避免做多余的事）。

### 一、证据链核验（逐项落地，全部通过）

1. **引用代码真实且在失效路径上**（评审已逐处打开复核）：
   - `desktop/src/main.js:216` `frame:false`（win/linux）、`:217-223` mac 分支、`:206-230` `opts` **确未设 `resizable`**（默认 `true`）。**评审新增核验**：全文件仅 `:236` 一处 `new BrowserWindow`，且无任何 `resizable:false` / `thickFrame` / `WM_NCCALCSIZE` / 自定义 NC 命中处理——故窗口非客户区行为**完全由「无边框 + 默认可缩放」默认值决定**，排除「别处改写窗口特性」这一干扰变量。
   - `apps/web/app/page.tsx:415-447`：顶栏为 `<div className="flex h-screen flex-col">` 的**首子元素、紧贴 y=0**，根 `drag-region`；`:439-446` 右簇 `no-drag` 含 `AccountModeSwitch`/主题键/`SettingsGearButton`/`WindowControls`。
   - `apps/web/app/globals.css:103-113`：drag/no-drag 契约（含 `.drag-region` 内 `button/input/a/[role]` 兜底 `no-drag`）。
   - `SettingsGearButton.tsx:23`、`WindowControls.tsx:30,36`：控件均显式 `no-drag`。
   - **repro 探针方法学正确**：与生产同构（`frame:false`、未设 `resizable`、48px `drag` 顶栏紧贴 y=0、32px `no-drag` 居中按钮），`result.txt` 与正文引用一致（还原 inset=0、最大化 inset≈7px、两态 `btnRegion` 恒 `no-drag`、`btnRect.y=8`）。

2. **旁证（评审新增）**：`docs/plans/desktop-custom-titlebar/spec.md` 早已把本缺陷列为**实施期必测风险**——RV-6 / §4.1 注（line 95）明确：「无边框窗口下 Electron 默认保留四边/四角 resize 命中区……在 Windows 无边框窗口上是**版本相关**已知风险点（四角命中区在个别版本偏窄、最大化偶越界），不作『无需关注』断言，**须在 Windows 主目标实测确认**」。本 bug 正是该 P2 风险在**还原态**的实证落地，与根因方向完全吻合（即「计划阶段已预判、却未在 Windows 实测兜住」的典型回归）。

3. **排他性论证成立**：两态间渲染层完全一致——CSS / 布局 / 按钮几何 / JS 句柄均不随窗口态改变（`SettingsGearButton.tsx:18` onClick 仅切 store 状态，**最大化态可用**即反证 JS/IPC 链无恙）。唯一随窗口态变化的变量是 **OS 非客户区命中**（还原态可缩放→保留顶边 resize/caption 命中带；最大化态不可缩放→塌缩）。由排除法，能解释「最大化可点、还原不可点」的只剩 OS NC 命中差异——**定位正确**。

### 二、对根因「子机理」的评审补强（关键，决定选哪个修复）

原报告把直接成因并列为两种可能：① 「`drag`(caption) 区里的 `no-drag` 洞未被减去 → 整颗按钮落入 HTCAPTION」；② 「顶边 resize 带(HTTOP) 盖住按钮顶部」。二者对**选哪个修复**影响重大，必须判明主导机理：

**决定性几何反证（评审新增推论）**：探针实测按钮 `btnRect.y=8`——h-12(48px) 顶栏内 h-8(32px) 按钮垂直居中，**天然距窗口顶边 8px**。Windows 顶边 resize 命中带通常仅约 4–8px。**若主导机理是 ②**，按钮主体（y≈8..40，除最顶端极薄一条外）本应仍可点，用户会感知「大部分能点、只有最顶端难点」；**但实际是整颗按钮完全无反应**（§2.1 / 用户原述「点了没有任何反应」）。故 **② 不足以解释症状，主导机理必为 ①**：还原态下整条 `drag` 顶栏（含已内缩 8px 的按钮）被 OS 当作可拖拽 caption，`no-drag` 洞未生效，`mousedown` 被吞作「拖窗」，`click` 不下发；最大化态 `no-drag` 被正确生效（最大化可点即证），按钮恢复 HTCLIENT 可点。

→ **失效的底层原语**：「**还原态无边框可缩放窗口下，`drag` 区内的 `no-drag` 子区未被减去**」。任何「让交互按钮不再依赖 no-drag 减法」的方案都能根治；任何「仅微调偏移/顶边缩进」的方案（B）只在机理 ② 下有意义，对主导机理 ① **无效**，故 **B 不能作为主修复**。

> **仅存的推断缺口（如实标注）**：OS NC 命中发生在浏览器进程、渲染层不可观测，无法用 `executeJavaScript` / `sendInputEvent` 直接录到「click 未下发」（`sendInputEvent` 还会绕过 NC 命中测试）。但「8px 内缩 + 整颗失效」的几何反证 + 端到端人工复现(§2.1) + 排他性论证三者交叉印证，已把该缺口收敛到**不影响修复决策**的程度。建议下游把 §2.2 探针扩成回归用例（断言两态下交互控件命中均为 client）。

### 三、候选修复审计（边界 / 最小化 / 回归）

- **A（反转拖拽契约）——最小且根因级**：直接绕开失效原语（按钮置于 `no-drag`/默认区 = HTCLIENT；拖拽面退化为无洞纯 `drag`）。配合按钮**现有天然 8px 内缩**（已清掉顶边 resize 带），A **单独即可**使按钮全可点，**无需改动顶栏视觉高度**。边界覆盖见第四节。
- **B（顶边安全缩进）——降级为或有兜底，非主修复**：仅对机理 ② 有效，而主导机理是 ①；且 B 改变顶栏视觉高度。**仅当 A 落地后 QA 在某 DPI/版本仍见「按钮最顶端极薄一条难点」时再追加**，不应预防性引入（否则即「做多余的事」，违背最小改动）。
- **C（WCO / `titleBarOverlay`）——过度**：删 win 端自绘三键、布局让位 overlay、颜色同步；破坏面与 UX 改动远超必要，且与 spec §10 R-2 取舍冲突。否决为主修复。
- **D（`resizable:false` / `thickFrame:false`）——回归更重**：连带禁用自定义 `drag` 拖拽（#30788）或破坏 resize（#48576），并失去缩放能力。否决。

### 四、修复须覆盖的边界（交付 Subtask #2 的硬性约束）

A 的实现须**同时**保证下列各项，否则引入新回归：

1. **双击最大化不回归**：`onTitleDoubleClick`（`page.tsx:395-398`）现经 `closest('.no-drag')` 判定「落在控件则不处理」。反转契约后，该判定赖以区分「拖拽面 vs 控件」的类名语义会变，**须同步调整**，确保「双击拖拽面→最大化」「双击控件→忽略」仍正确。
2. **窗口仍可拖动**：必须保留一块**不含任何交互控件**的纯 `drag` 面（建议用 logo/标题文字簇与右簇之间的 `justify-between` 伸缩空白，或品牌簇本身），否则用户无法拖动窗口。
3. **四边 / 四角 resize 仍可用**（spec §8.3 第 11 条）、**最大化态行为不回归**。
4. **mac 不受影响**：mac 走 `titleBarStyle:'hidden'` + 系统红绿灯，本改动应仅作用于 win/linux 自绘顶栏，不得波及 mac 安全区 / 红绿灯布局。
5. **浏览器零回归**：`-webkit-app-region` 在普通浏览器为无副作用样式，纯 Web 行为不变。
6. **（范围外提示）** `loading.html` 是另一套独立 drag 契约（spec D7），本 bug 不涉及，修复不应顺带改动它。

### 五、最终推荐（单一方案）

> **推荐且仅推荐：方案 A —— 反转顶栏拖拽契约。**

**做法**：顶栏交互控件**移出顶边 `drag` 区**（置于 `no-drag`/默认区，落为 HTCLIENT）；仅对**无任何交互控件**的子区域单独标 `-webkit-app-region: drag` 作为拖拽面。

**理由（凝练）**：
1. 本 bug 的失效原语是「还原态 `drag` 区内 `no-drag` 子区未被减去」；A 从**形态上消除对该原语的依赖**，是覆盖**主导机理 ①**、改动最小、Web 零回归、且**完整保留产品形态**（自绘暖色顶栏 + 右侧三键）的**唯一根因级**方案。
2. 借助按钮**现有 8px 内缩**，A 单独即可生效，**无需** B 的顶边加缩进，亦**无需**改顶栏视觉高度——满足「最小改动、不做多余事」。
3. B 仅在非主导机理 ② 下有意义且改视觉，**降级为 QA 实测后的或有兜底**，不纳入主修复；C / D 过度或回归更重，均否决。

**验收口径**（对齐 §六 与 spec §8.3 第 10–12 条）：还原态下顶栏全部控件（设置齿轮 / 主题 / 账户切换 / 最小化-最大化-关闭 / AppMenu）**可点** + 拖拽面**可拖动** + 双击**可最大化** + 四边四角**可 resize** + 最大化态与纯 Web 端**均不回归**。
