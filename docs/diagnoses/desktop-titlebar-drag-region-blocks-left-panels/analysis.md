# 桌面端左侧面板无法点击 / 拖拽即移动整窗 — 问题定位与分析

- **Bug slug**: `desktop-titlebar-drag-region-blocks-left-panels`
- **诊断节点**: Subtask #0（问题定位与分析）
- **日期**: 2026-06-20
- **结论先行**: 由 `ff20361`「替换原生标题栏为应用内自绘标题栏」引入的 **无边框窗口（`frame:false`）+ `-webkit-app-region: drag`** 是根因区域。应用主页面（`page.tsx`）的拖拽区其实已正确收敛在 48px 顶栏（经 Chromium 31 实测，`<main>` 与左侧各面板计算值均为 `none`）。真正把「整窗可拖、面板点不动」带出来的是该特性引入的两个**全窗口/大面积拖拽面与缺失的兜底**：① `loading.html` 把**整个 `<body>` 设为拖拽区**（启动 10–60s 全程展示，唯一的全窗口拖拽面）；② 应用内容区**没有显式 `no-drag` 边界**，完全依赖「拖拽区恰好只在顶栏」且 Chromium 在导航/DevTools 生命周期内正确刷新拖拽区。任一环节出偏，左侧面板即落在活动拖拽面上：点击被吞、拖拽移动整窗。

---

## 一、问题描述（Problem description）

桌面端（Electron）应用近期改动后出现交互回归：

- 左侧工作区——**K 线图面板**，以及 K 线图**左侧的标的列表面板**（含上方盘口面板）——点击无任何反应。
- 在这些面板上**按下并拖动时，被拖动的是整个应用窗口**（操作系统级窗口移动），而非面板内部交互。
- 用户诉求：左侧所有面板（及相关面板）恢复可点击、可正常使用。

「拖拽即移动整窗 + 点击无响应」这一组合，在 Chromium / Electron 中**有且仅有一个成因**：被作用区域处于 `-webkit-app-region: drag` 拖拽面之上——拖拽面会在浏览器/OS 层把鼠标按下事件用于移动窗口，事件不再下发给页面，因此点击「没有任何反应」。这把问题牢牢指向「自定义标题栏」特性。

---

## 二、复现步骤（Reproduction steps）

### 2.1 用户侧复现（端到端）

1. 检出包含 `ff20361`（`feat: replace native desktop chrome with in-app custom title bar and window controls`）的代码。
2. 启动桌面端：
   - 开发态：仓库根执行 `pnpm dev`（或 `npm run dev`）。该路径下主进程会**无边框开窗**并自动 `openDevTools({ mode: 'right' })`（`desktop/src/main.js:403-405`）。
   - 生产态：运行打包产物 `desktop/dist/win-unpacked/Aragon Trading.exe`。
3. 后端(18760)/前端(18761)就绪前，窗口展示 `loading.html` 过渡页（冷启动/首次 `next dev` 编译可达数十秒）。
4. 在窗口客户区任意位置（含左侧面板区域）**按下拖动** → 整窗跟随移动；**单击** → 无反应。

> 关键观察点：症状（整窗可拖 + 点击失效）等价于「该区域是一块 `-webkit-app-region: drag` 拖拽面」。

### 2.2 最小复现 / 机理验证（Electron 31 实测）

为排除「`-webkit-app-region` 是否会从顶栏继承/外溢到兄弟内容区」的猜测，构造了与真实 DOM/CSS 同构的最小用例，在与本项目相同的 Chromium 31 引擎下读取**计算样式**：

- 用例（已随报告留存于本目录 `repro/`）：`repro/page.html`（复刻 `globals.css` 的 `.drag-region`/`.no-drag` 规则与 `header(drag) → main(content)` 的同级结构）+ `repro/main.js`（`frame:false`、`show:false` 无头窗口，加载后读取各元素 `getComputedStyle(...).webkitAppRegion`）。
- 运行：`desktop/node_modules/electron/dist/electron.exe docs/diagnoses/desktop-titlebar-drag-region-blocks-left-panels/repro/main.js`（需清除 `ELECTRON_RUN_AS_NODE` 环境变量）。

实测输出（节选）：

```
===COMPUTED -webkit-app-region===
root: none
hdr: drag            ← 顶栏（.drag-region）
leftcluster: none    ← 顶栏的子元素也只是 none（证明该属性「不继承」）
logo: none
rightcluster: no-drag
main: none           ← 内容区
section: none        ← ChartWorkspace
instlist: none       ← 标的列表面板
chart: none          ← K 线区
===END===
```

**结论（证据）**：
1. `-webkit-app-region` **不是继承属性**：顶栏子元素 `leftcluster/logo` 计算值为 `none`，并未从父级 `.drag-region` 继承到 `drag`。
2. 应用主页面里 `<main>`/`<section>`/标的列表/K 线区计算值**全部为 `none`**——即**当前提交版本里，内容区本身并非拖拽区**。

因此，「整窗可拖、面板点不动」并非来自 `page.tsx` 顶栏拖拽区的「外溢」，而是来自下文根因里**真正把整窗/大面积变成拖拽面的两处代码**。

---

## 三、根因分析（Root cause analysis）

### 3.1 改动定位：拖拽面/无边框窗口由本次特性首次引入

- `ff20361` 之前，桌面窗口是**带原生边框**的常规窗口，整个项目**没有任何 `-webkit-app-region`**，内容区永远可点。
- `ff20361` 首次引入：
  - 无边框窗口：`desktop/src/main.js:199` `frame: false`（win/linux）。无边框窗口下，「能否拖动窗口」**完全由 `-webkit-app-region: drag` 决定**。
  - 拖拽区工具类：`apps/web/app/globals.css:103-113`（`.drag-region{ -webkit-app-region: drag }` + `.no-drag` 一组）。
  - 顶栏接线：`apps/web/app/page.tsx:414-443`（`<header className="drag-region ...">`，右簇 `no-drag`）。

> 证据：`git show ff20361 --stat` 显示本次新增 `globals.css(.drag-region/.no-drag)`、`page.tsx(Header 接线)`、`main.js(frame:false)`、`loading.html(整页拖拽)`；`git log` 确认这是引入 `frame:false`+`app-region` 的**唯一**提交。

### 3.2 真正成因 1（主因）：`loading.html` 把**整个窗口**设为拖拽区

`desktop/src/loading.html:27`

```css
/* 无边框加载窗：整页可拖拽移动，避免后端慢启动时用户被「钉死」 */
body { -webkit-app-region: drag; }
```

- 这是**全代码库里唯一一处全窗口拖拽面**。它精确对应症状「整个软件界面在被拖拽 + 所有面板点击无反应」——整页是拖拽面，除右上角 `.win-close`（`loading.html:41` `no-drag`）外无任何可点元素。
- 该过渡页在 `bootstrap()` 里**贯穿整个启动期展示**：`createWindow()` 先 `win.loadFile('loading.html')`（`main.js:230`），随后 `await startEngines()` 等待后端+前端就绪（冷启动/首次 `next dev` 编译可达数十秒），就绪后才 `win.loadURL(WEB_URL)`（`main.js:401`）替换为应用。
- 风险面：在这段时间里，用户若尝试点击/拖动，看到的就是「点不动、拖整窗」。即便正常导航会重算拖拽区，**无边框窗口在 `loadFile → loadURL` 跨文档导航期间的拖拽区刷新时序**，以及 DevTools 开启时的拖拽区缺陷（见 3.4），都可能让这块全窗口拖拽面在内容已渲染后仍残留生效。

### 3.3 真正成因 2（结构性放大器）：应用内容区缺少 `no-drag` 兜底

`apps/web/app/page.tsx:445-483` 的内容区（`<main>` → `ChartWorkspace`/`StrategyPanel`）**没有任何 `-webkit-app-region: no-drag`**。当前它能点，仅仅因为「拖拽区恰好只标在顶栏」这一**正向约束**成立。

- 这意味着系统对「拖拽面绝不覆盖内容」**没有任何防御性下界**：一旦出现拖拽区外溢/残留（加载页全窗口拖拽面未刷新、DevTools 干扰、未来顶栏重构把 `.drag-region` 挪到更外层容器、或某次构建产物滞后），内容面板会**立刻**变成死区，且无兜底拦截。
- 官方实践亦印证此点：将 `body`/容器设为 `drag` 时，**必须**把其内可交互元素显式标 `no-drag`，否则「用户无法点击它们」。本项目把这一兜底只施加在顶栏内部（`globals.css:106-112` 仅覆盖 `.drag-region` 下的 `button/input/a/[role=radiogroup]/[role=switch]`），**内容区不在保护范围**。

### 3.4 贡献因素（开发态）：DevTools 停靠 + `app-region` 的已知缺陷

`desktop/src/main.js:403-405`

```js
if (!paths.isPackaged() || process.env.ARAGON_MODE === 'dev') {
  win.webContents.openDevTools({ mode: 'right' });
}
```

- 开发态默认**右侧停靠**打开 DevTools。Electron 官方与 issue [#3647](https://github.com/electron/electron/issues/3647) 明确记载：**「DevTools 打开时 `-webkit-app-region: drag` 存在已知问题」**，且「无论停靠还是独立窗口都会触发」。停靠会压缩/位移 Web 内容视口，使拖拽区命中测试坐标与窗口 NC 命中测试错位，拖拽面行为变得不可靠。
- 这解释了为何**开发者本地（`pnpm dev`）最容易复现**：无边框 + 右停靠 DevTools + `app-region` 三者叠加，正是官方点名的脆弱组合。

### 3.5 小结

- 症状 = 内容落在活动拖拽面上（点击被吞 + 拖拽移窗）。
- 当前提交的**应用页面**拖拽区已正确收敛于顶栏（实测内容区 `none`）。
- 但本特性引入了**唯一的全窗口拖拽面**（`loading.html` 整页 `drag`）且**内容区无 `no-drag` 兜底**，叠加无边框窗口跨文档导航/DevTools 停靠的拖拽区时序与已知缺陷，使内容面板在真实运行条件下落到拖拽面上——即用户所见回归。

---

## 四、影响面（Impact surface）

| 维度 | 影响 |
|---|---|
| 平台 | Windows / Linux（`frame:false` 分支）。macOS 走 `titleBarStyle:'hidden'` 且保留原生红绿灯，受影响较小但 `loading.html` 整页拖拽同样存在。 |
| 运行态 | 开发态（`pnpm dev`，DevTools 右停靠）**最易复现**；生产态在启动过渡期（`loading.html`）及导航刷新时序不佳时复现。 |
| 受影响功能 | 左侧工作区全部交互：标的切换（`InstrumentList`）、盘口（`MarketDepthPanel`）、K 线图缩放/十字光标（`CandleChart`）、图表工具栏/页签；以及任何落在残留拖拽面下的区域（严重时右侧 `StrategyPanel` 亦受影响，对应用户「所有面板」表述）。 |
| 严重度 | 高——核心交互不可用，应用近乎不可操作。 |
| 回归引入点 | `ff20361`（自定义标题栏特性）。此前版本无 `app-region`、带原生边框，内容恒可点。 |

---

## 五、候选修复方案对比（Candidate fixes）

> 本节仅做方案比较，**不写修复代码**（留给 Subtask #2）。

| 方案 | 做法 | 侵入性 | 风险 | 工作量 | 是否根治 |
|---|---|---|---|---|---|
| **A（推荐）显式 `no-drag` 兜底 + 收敛加载页拖拽** | ① 在应用内容区根（`page.tsx` 的 `<main>`，或在 `globals.css` 给内容容器）显式 `-webkit-app-region: no-drag`，作为「拖拽面绝不覆盖内容」的硬性下界；② 把 `loading.html` 的 `body{drag}` 收敛为**顶部一条拖拽带**（仿应用顶栏），消除唯一的全窗口拖拽面。 | 低（CSS/类名为主，几行） | 低（`no-drag` 在浏览器为无副作用样式，Web 端零回归） | 低 | 是——无论拖拽区因何外溢/残留，内容区都被显式排除 |
| **B 开发态 DevTools 改为 `detach`** | `main.js` 把 `openDevTools({mode:'right'})` 改为 `{mode:'detach'}`（或默认不自动开）。 | 极低 | 低（仅缓解开发态，不解决生产态加载页/兜底缺失） | 极低 | 否（仅治 3.4） |
| **C 反转拖拽契约：专用拖拽带 + 其余默认 no-drag** | 重构为「一个专用细拖拽条 `drag`，应用根容器整体 `no-drag`，仅拖拽条例外」，从架构上保证内容不可能落在拖拽面。 | 中（动顶栏结构与样式契约） | 中（需回归顶栏全部控件/双击最大化/mac 安全区） | 中 | 是，但改动面大 |

---

## 六、推荐方案（Recommended fix）

**采用 A，并附带 B 作为低成本开发态加固。**

理由：

1. **直击症状且提供硬下界**：方案 A 在内容区显式 `no-drag`，等价于「无论拖拽区因加载页残留、跨文档导航刷新时序、DevTools 干扰还是未来顶栏重构而外溢，内容面板都被从拖拽面中减去」。这把当前「依赖正向约束（拖拽区恰好只在顶栏）」升级为「具备防御性下界」，与 `globals.css` 注释里已声明的「结构兜底」理念一致，只是把兜底从「顶栏内部」扩展到「内容区边界」。
2. **消除唯一全窗口拖拽面**：把 `loading.html` 的整页 `drag` 收敛为顶部拖拽带，直接铲除 3.2 这一主因——既保留「慢启动时可拖动加载窗」的初衷，又不再让整窗成为死区。
3. **最小破坏面、零 Web 回归**：`-webkit-app-region` 在普通浏览器中为无副作用样式，改动集中在 CSS/类名与 `loading.html`，不触碰业务逻辑与数据流；符合「改动尽量小」的下游约束。
4. **B 极低成本兜住开发态**：`detach` 规避 Electron 官方点名的「DevTools 开启 + `app-region`」缺陷（[#3647](https://github.com/electron/electron/issues/3647)），让开发者本地复现路径一并消失。

**验收对齐（供 Subtask #2 验证）**：修复后，按 §2.1 步骤——启动期与应用期，在左侧标的列表、盘口、K 线区、图表工具栏，以及右侧策略面板上**单击均有响应**，**拖拽内容区不再移动整窗**；仅顶栏（及加载页顶部拖拽带）可拖动窗口、双击可切换最大化。建议同时用 §2.2 的最小用例回归 `<main>`/内容区计算值应为 `no-drag`。

---

## 附录：关键证据索引

- 引入点：`desktop/src/main.js:199`（`frame:false`）、`:230`/`:401`（loading→app 导航）、`:403-405`（dev 右停靠 DevTools）。
- 拖拽区样式：`apps/web/app/globals.css:103-113`。
- 顶栏接线：`apps/web/app/page.tsx:414-443`；内容区无 `no-drag`：`apps/web/app/page.tsx:445-483`。
- 全窗口拖拽面：`desktop/src/loading.html:27`。
- 机理实测：`docs/diagnoses/desktop-titlebar-drag-region-blocks-left-panels/repro/`（Chromium 31，内容区计算值 `none`，`-webkit-app-region` 不继承）。
- 外部佐证：Electron 官方文档与 issue [#3647](https://github.com/electron/electron/issues/3647)（DevTools 打开时 `-webkit-app-region: drag` 已知问题）；frameless 文档关于「body 设 drag 时必须给可交互子元素标 no-drag，否则无法点击」的实践。

---

## 评审结论（Review Verdict）

- **评审节点**: Subtask #1（分析报告评审与修复）
- **评审人**: 资深评审（Anthropic 工程团队角色）
- **日期**: 2026-06-20
- **结论**: 根因区域与推荐方向**成立并予采纳**；但原报告把 `loading.html` 列为「主因」存在一处**症状—文档错配**的逻辑缺口，需用「残留拖拽区跨文档存活」这一显式机理补全后方能闭环。最终推荐**方案 A（两处改动作为一个整体的防御性契约）**为唯一修复；B 降级为可选的开发态加固。下文逐条给出我**实测复核**到的证据、补强的机理引用、对修复的边界审计与最终落点。

### 一、根因证据复核（reviewer-verified）

我对报告引用的每一处代码与提交逐一核对，结论如下（均已落到具体行号，供 Subtask #2 直接定位）：

1. **引入点真源 = `ff20361`，证据闭合。** `git show ff20361 --stat` 实测列出本次同时新增/改动 `apps/web/app/globals.css(+17)`、`apps/web/app/page.tsx(±32)`、`desktop/src/loading.html(±28)`、`desktop/src/main.js(±109)` 等 12 个文件；提交信息明确写「win/linux frame:false」「loading.html 整页拖拽」「globals.css 追加 .drag-region/.no-drag」。**全仓 `grep -webkit-app-region`** 仅命中 `globals.css`、`page.tsx`、`loading.html` 三处源文件（其余为 `tmps/`、`repro/`、`docs/` 诊断产物），印证「`ff20361` 之前无任何 `app-region`」属实。
2. **`frame:false` 与 `loadFile→loadURL` 时序，行号准确。** `desktop/src/main.js:199` `frame:false`（win/linux）、`:230` `win.loadFile('loading.html')`、`:401` `win.loadURL(cfg.WEB_URL)`、`:403-405` 仅在 `!isPackaged() || ARAGON_MODE==='dev'` 时 `openDevTools({mode:'right'})`——**打包态（prod）不开 DevTools**。这条很关键：本项目「Project root」为 `…/win-unpacked/resources/server`（打包产物），故**用户侧最可能命中的是打包态，DevTools 通路不成立**，§3.4 只对开发者本地有效。
3. **「整窗唯一拖拽面」= `loading.html:27`，唯一性成立。** 全仓内 `body{-webkit-app-region:drag}` 仅此一处；应用页拖拽区收敛在 `header.drag-region`（`page.tsx:416`，`h-12`=48px），右簇 `no-drag`（`page.tsx:436`），窗口控制键/菜单亦 `no-drag`（`WindowControls.tsx:30,36`、`AppMenu.tsx:61`）。
4. **内容区确无 `no-drag`，且最小用例计算值为 `none`。** `page.tsx:445-483` 的 `<main className="grid flex-1 …">` 及其 `ChartWorkspace`/`<aside><StrategyPanel/>` 子树无任何 `app-region`；`repro/` 实测内容区计算值全为 `none`、且属性不继承。该证据**强**——但它只证「静态 CSS 不会让内容可拖」，属**否定性证据**，并不正向复现运行期故障（详见下条缺口）。

### 二、必须补强的逻辑缺口：症状只存在于「应用文档」，`loading.html` 仅能经「残留区」致因

报告把 `loading.html` 整页拖拽列为**主因**，但用户报障对象是 **K 线面板 + 其左侧标的列表面板**——这些 DOM **只存在于 `page.tsx:445-483` 的 `<main>`（即 `loadURL(WEB_URL)` 之后的应用文档）**，`loading.html` 里**根本没有这些面板**（只有 spinner 与「正在启动…」文案 + 右上角 `.win-close`）。因此：

> **`loading.html` 的整页 `drag` 无法「直接」解释「K 线面板点不动」——只有当该拖拽区在 `loadFile('loading.html')→loadURL(WEB_URL)` 跨文档导航后仍「残留生效」到新应用文档上，它才构成该症状的成因。**

这一「残留」是报告 §3.2 末句一笔带过、却**全篇最吃证据**的假设。补强其机理（这是 `-webkit-app-region` 的确定性语义，非猜测）：

- Chromium 把窗口可拖区计算为「**所有 `drag` 元素矩形之并 ∖ 所有 `no-drag` 元素矩形之并**」，按文档计算后经 `DraggableRegionsChanged` 推送到浏览器/OS 进程。**`none` 不参与减法**——值为 `none` 的元素**既不增加也不削减**任何拖拽区。
- 推论 1（**为何 `repro` 的 `none` 不构成保护**）：内容区计算值是 `none` 而非 `no-drag`，意味着**一旦有任何拖拽矩形（残留的整窗区，或被 DevTools 错位的顶栏区）覆盖到内容之上，内容不会被「减去」**，于是落在活动拖拽面上——点击被 OS 用于移窗、不再下发给页面。这正是「点击无反应 + 拖拽移整窗」。
- 推论 2（**残留为何可能发生**）：无边框窗口在 `loadFile→loadURL` 的跨文档导航中，新文档需要重新计算并推送可拖区以**覆盖**上一文档（整窗 `drag`）的区域；该推送在无边框窗口下存在已知的触发/时序不稳定（与 §3.4 #3647 同源的拖拽区刷新缺陷）。**若新文档的推送未及时覆盖，旧的整窗拖拽区便残留**，整个应用客户区变成拖拽面——与用户「整个软件界面在被拖拽 + 所有面板都点不动」的描述**高度吻合**（注意：这要求是「整窗级」而非「顶栏 48px 级」的拖拽面，恰好排他地指向 `loading.html` 的整页 `drag`，而非顶栏区轻微外溢）。

**症状的排他性核验**：对无边框窗口，OS 端「按下即移窗」**有且仅有** `app-region:drag` 一个触发源（默认客户区不可拖）。全仓能形成「整窗级」拖拽面的**唯一**来源是 `loading.html:27`；能形成「顶栏级」拖拽面的是 `header.drag-region`。因此运行期可致此症状的机理**穷举为两条**：①`loading.html` 整窗区残留（跨文档，打包态/开发态均可能）；②顶栏 48px 区被 DevTools 错位下移盖住内容（同文档，仅开发态）。报告的两机理框架**是完备的**，缺的只是上面这层「残留区跨文档存活」的显式链路——现已补齐。

### 三、候选修复审计（边界 / 最小性 / 回归）

对推荐方案 A 的两处改动逐一审：

| 改动 | 防御的机理 | 边界与正确性 | 回归风险 |
|---|---|---|---|
| **A-1：内容区根 `<main>` 显式 `no-drag`**（`page.tsx:445`，复用 `globals.css` 既有 `.no-drag`） | 机理② + 任何**同文档**拖拽区外溢/错位/未来顶栏重构 | `<main>` 是 48px 顶栏之下、`flex-1` 撑满的唯一内容容器，**单处 `no-drag` 即同时覆盖左侧 `ChartWorkspace`（标的列表 + 盘口 + K 线）与右侧 `<aside><StrategyPanel/>`**，与用户「所有面板」表述对齐；`no-drag` 在同文档内**全局地**从拖拽区减去 `<main>` 矩形，故即便顶栏 `drag` 区被错位下移，内容仍被减出而恢复可点。 | 顶栏双击最大化挂在 `<header>`（`page.tsx:420` `onTitleDoubleClick`），与 `<main>` 无关，不受影响；`no-drag` 在普通浏览器为无副作用样式 → **Web 端零回归**。 |
| **A-2：`loading.html:27` `body{drag}` 收敛为顶部拖拽带** | 机理①（**跨文档**残留）：A-1 改的是「新应用文档」，**无法削减已销毁的 `loading.html` 文档残留下来的旧区**；唯有把加载页拖拽面本身缩为顶部细带，残留时也只剩顶部细带可拖，整窗不再成为死区。 | 保留「慢启动可拖动加载窗」初衷（spec §4.5/D7/R-4）；加载页唯一可交互元素 `.win-close` 已 `no-drag`（`loading.html:41`），收敛后仍可点。 | 仅改加载过渡页样式，不触业务逻辑/数据流。 |

**最小性结论**：A 是**能闭合「两条机理 × 跨/同文档」的最小集合**——A-1 单独无法处理跨文档残留（机理①），A-2 单独无法处理同文档错位（机理②），二者**互补且不冗余**，共同构成「拖拽面绝不覆盖内容」的硬下界。方案 C（反转契约、根容器整体 `no-drag`）能达同等保证但需改顶栏结构契约、回归全部控件/双击/mac 安全区，**改动面更大、非最小**，不取。

**B 的定位（DevTools 改 `detach`）**：仅作用于开发态机理②；而 A-1 的同文档 `no-drag` 减法**已实质缓解**该路径，故 B **不再是修复必需**，降级为开发者本地的可选加固，**不进关键路径、不阻断验收**。

### 四、最终推荐修复（单一方案）

> **采用方案 A，作为一次「防御性拖拽契约」整改，含两处必做改动：**
>
> 1. **A-1**：在 `apps/web/app/page.tsx:445` 的 `<main>` className 上追加 `no-drag`（项目 `globals.css:106-113` 已定义该工具类，无需新增 CSS），把内容区从「计算值 `none`（不设防）」升级为「`no-drag`（主动从任何同文档拖拽区减去）」。
> 2. **A-2**：将 `desktop/src/loading.html:27` 的 `body { -webkit-app-region: drag; }` 收敛为**仅顶部一条拖拽带**（仿应用 48px 顶栏，新增一个置顶、固定高度、`-webkit-app-region: drag` 的元素，`body` 不再整页 `drag`），消除全仓唯一的整窗拖拽面；保留 `.win-close` 的 `no-drag`。
>
> **B（可选，不阻断）**：如需进一步消除开发者本地复现，可把 `desktop/src/main.js:404` 的 `openDevTools({mode:'right'})` 改为 `{mode:'detach'}`。

**理由（为何唯独 A 能根治且最小）**：
1. **直击排他症状**：症状被 `-webkit-app-region:drag` 唯一锁定；A 同时铲除「整窗唯一拖拽面（A-2）」并为内容建立「同文档硬下界（A-1）」，覆盖第二节穷举出的全部两条运行期机理。
2. **机理对位、互补不冗余**：A-1 治同文档（含 DevTools 错位、未来顶栏重构外溢），A-2 治跨文档残留——分别对应两种文档作用域，缺一不可，合起来即最小完备集。
3. **`none`→`no-drag` 是质变而非装饰**：A-1 把内容从「不参与拖拽区减法」改为「主动减去自身矩形」，这是「依赖正向约束（拖拽区恰好只在顶栏）」升级为「具备防御下界」的关键一步，与 `globals.css:101` 注释已声明的「结构兜底」理念一致，只是把兜底边界从顶栏内部扩展到内容区。
4. **最小破坏面、零 Web 回归**：改动集中在 CSS 类名（A-1）与加载页样式（A-2），不碰业务逻辑、数据流、IPC；`-webkit-app-region` 浏览器侧自动降级。

### 五、验收对齐（供 Subtask #2 / #3）

- **功能**：启动过渡期与应用运行期，左侧标的列表、盘口、K 线缩放/十字光标、图表工具栏/页签，以及右侧 `StrategyPanel`，**单击均有响应**；**在内容区按下拖动不再移动整窗**；仅顶栏（及加载页顶部拖拽带）可拖动窗口、双击仍可切换最大化。
- **机理回归**：用 §2.2 最小用例（或对 `<main>` 追加 `no-drag` 后的同构用例）核验内容区计算值由 `none` 变为 `no-drag`；可补一条「`loadFile(loading)`→`loadURL(app)` 后读取窗口可拖区不覆盖内容」的运行期断言，将本报告唯一的假设性环节（残留区存活）固化为回归测试。
- **范围守卫**：staged 仅含 `apps/web/app/page.tsx`、`desktop/src/loading.html`（采纳 B 时含 `desktop/src/main.js`）；不得带入 `tmps/`、`repro/` 等诊断产物或构建产物。
