# Aragon Trading — 桌面端自定义标题栏（无边框窗口 + 应用内窗口控制）方案 (spec)

- **Feature**: `desktop-custom-titlebar`
- **文档版本**: **v2**（Subtask #1 方案评审与修复产出。已就地修复全部 P0/P1；「评审记录」见下、「评审结论」见文末；实施由 Subtask #2 依据本 v2 落地。）
- **作者 / 角色**: Solution Architect (Anthropic Engineering)
- **评审 / 角色**: Senior Reviewer (Anthropic Engineering)
- **创建日期**: 2026-06-20 · **评审日期**: 2026-06-20
- **本轮需求（原文）**: 「这个项目的桌面端需要修改完善一下，桌面端不需要它系统自带的这个 Electron 自带的最上方的菜单栏，以及它的放大缩小或者关闭的横栏，而是采用整个软件本身的 Header 作为它的软件顶部，然后它的最右侧有对应的放大、缩放、缩小以及关闭按钮。美观优雅顶级，符合人机交互最佳实践。」
- **基线**: 已交付并提交的 monorepo —— `apps/web`（Next.js 14 App Router + React 18 + TS + Tailwind + lightweight-charts v4 + Zustand）、`services/quant`（FastAPI/uvicorn + SQLite）、`desktop/`（Electron 主进程外壳，复用 Web/后端产物）。桌面外壳现状见 `desktop/src/main.js`（`createWindow()` 用默认 `frame`，`buildMenu()` 安装原生应用菜单）、`desktop/src/preload.js`（contextBridge 暴露最小只读 API）。前序桌面设计见 `docs/plans/desktop-electron-packaging/spec.md`。
- **关系声明**: 本轮**只做"用应用自身 Header 取代系统原生窗口框架"这一前端 + 外壳协同的增量**。**不改** 任何策略逻辑、SimBroker 撮合、回测/回放管线、DB schema、REST/WS 接口语义、端口约定与进程编排/退出清理逻辑。所有改动要么落在 `desktop/`（外壳层），要么落在 `apps/web` 渲染层且**全程以 `window.aragon` 探测做运行期守卫**——浏览器环境（`pnpm dev` 跑纯 Web）下新代码完全降级为「无操作」，Web 体验零回归。

---

## 评审记录（Review Notes · Subtask #1）

> 评审维度：**可行性 / 完整性 / 一致性 / 规模适配**。评审已逐节核对真实源码（`desktop/src/main.js`、`preload.js`、`loading.html`、`apps/web/app/page.tsx`、`globals.css`、`electron-builder.yml`、`apps/web/vitest.config.ts`、`apps/web/tailwind.config.ts`、`.claude-index/index.md`），下表行号 / 约定均已与代码对齐。**所有 P0/P1 已在本 v2 正文就地修复**；P2 为实施期需关注但不阻塞的项，已在正文补充说明或验收清单。

| 编号 | 维度 | 严重度 | 节 | 问题 | 处置 |
|---|---|---|---|---|---|
| RV-1 | 完整性 | **P1** | §4.9 | 现有 `<header>` 是 `justify-between` 且**恰好两个**直接子簇（左品牌、右账户/主题，见 `page.tsx:397–417`）。若按原文把 `<WindowControls/>` 作为**第三个**同级子元素追加，`justify-between` 会把账户/主题簇挤到水平居中、窗口控制带贴右，顶栏布局错位——直接违背「美观优雅顶级」诉求。 | 已修：§4.9 改为把「账户/主题 + WindowControls」收进**同一个右侧 `no-drag` 容器**，保持顶层只有左右两簇；`-mr-4` 仅施于 `WindowControls` 使其贴窗口右缘。 |
| RV-2 | 一致性 / 可行性 | **P1** | §4.2 · D2 | 原文在所有平台一律 `Menu.setApplicationMenu(null)`。在 **macOS** 上全局应用菜单不在窗口内，强行置空会连带移除标准 **Cmd+Q / Cmd+C/V/X/A/Z**（退出、复制粘贴剪切全选撤销）——这些在 mac 上经由 `editMenu` role 提供，置空后文本框内剪贴板/撤销快捷键失效，破坏 mac 标准 HCI；且用户诉求针对的是 **Windows 窗口内菜单栏**，非 mac 顶部全局菜单。 | 已修：§4.2 / D2 / R-6 改为**按平台分流**——`win32`/`linux` → `setApplicationMenu(null)`（彻底移除窗口内菜单）；`darwin` → 安装**最小原生菜单**（`appMenu` + `editMenu`，可选 `viewMenu`），保留 Cmd+Q 与剪贴板/撤销语义。 |
| RV-3 | 可行性 | **P1** | §4.9 · D4 | 原文在 mac 桌面端为 Header 追加左安全区 `pl-[84px]`，且依据 `getDesktopPlatform()`（读 `window.aragon.platform`）在**渲染期**判定。Electron 中 preload 在页面 JS 前即注入 `window.aragon`，故客户端首渲染 `platform==='darwin'` 成立 → `pl-[84px]`；而服务端无 `window` → `px-4`。**两者不一致 → mac 上 React 水合（hydration）报错**（Windows 因 `platform==='win32'` 两端皆 `px-4`，恰好不暴露）。 | 已修：§4.9 / D4 / R-1 / R-5 改为把 mac 安全区 padding 交由**挂载后状态**驱动（与 `useIsDesktop()` 同一「首渲染恒 false、`useEffect` 内置真」机制），首渲染两端一致、挂载后再切，杜绝水合不一致。 |
| RV-4 | 可行性 | P2 | §8.3-10 · R-8 · §5 | 原文拟在 `verify-dist.mjs` 断言「`resources` 内含 `loading.js`」。但 `electron-builder.yml` 的 `files: ["src/**/*"]` 把 `loading.js` 打进 **`app.asar`**（`asar:true`），它**不是** `resources/` 下的散文件；`verify-dist.mjs` 现仅校验 `resources/{backend,web}` 散文件，`existsSync(resources/loading.js)` 恒为假。原断言不可行。 | 已修：去掉「resources 内含 loading.js」的散文件断言；改为说明 `loading.js` 由既有 `files` glob 随 `src/**` 自动进 asar，无需额外打包配置；如确需冒烟校验，应以 `@electron/asar` 列出 asar 内容来断言（标注为可选增强）。 |
| RV-5 | 完整性 | P2 | §4.8 ↔ §4.9 | §4.8 称在「左侧品牌区旁」放 `AppMenu`，§4.9 又称「左侧不变更（纯展示，可拖拽）」，二者措辞冲突，落地易歧义。 | 已修：§4.9 明确 `AppMenu` 置于**左品牌簇内**、自带 `no-drag`，与品牌 logo/标题同属左簇；「左簇仅追加 AppMenu，其余保持纯展示可拖拽」。 |
| RV-6 | 完整性 | P2 | §4.1 · §8.3 | §4.1 注断言无边框窗口「四边拉伸无需额外处理」「最大化不越界」。二者在 **Windows 无边框窗口**上是版本相关的已知风险点（最大化可能轻微越出工作区遮挡任务栏 / 边缘内容被裁、四角 resize 命中区在个别版本偏窄），不宜直接断言为「无需关注」。 | 已修：§4.1 注改为「Electron 默认保留 resize 命中区，**但须在 Windows 主目标实测确认**」；§8.3 新增两条验收：①最大化不遮挡任务栏、内容不被裁切；②四边 / 四角可正常拉伸缩放。 |
| RV-7 | 一致性 | P2（已缓解，接受） | §1 末 · R-10 | 改 `apps/web` 渲染层违反索引「Key Conventions」第 375–376 行「桌面差异勿在 `apps/web`/`services/quant` 特判，应走 desktop 胶水」的约定。 | **接受为受控例外**：窗口控制键在语义上必须渲染于 React Header 树内，纯外壳无法实现；R-10 已以运行期 `window.aragon` 守卫将影响面限定在桌面态、Web 端零回归，理由充分，无需额外修改。 |
| RV-8 | 规模适配 | P2（接受） | §4.8 | `AppMenu`（含 `role="menu"` + 方向键导航 + 点击外部/Esc 关闭）是本轮**最大的净新增交互面**。 | **接受**：用于补偿被移除菜单中的「关于 / 打开日志」（运维必需，否则零回归不成立）。要求实施期保持其**最小**（仅 3 项、单文件 < 120 行），不扩展为通用菜单框架。 |

**结论性判断**：方案技术主线（无边框 + 应用 Header 即顶栏 + 自绘控制键 + `window.aragon` 运行期守卫 + 关闭复用既有退出链）**可行且与现有架构契合**，行号 / 约定 / 依赖（`clsx`、`vitest`+`jsdom`、设计 token `--surface-2/--text/--muted/--accent/--border` 均已存在）核对无误。3 个 P1 均为「跨平台正确性 / 顶栏布局」缺陷，已就地修复；P2 为实施期验证项。

---

## 1. Overview（概述）

当前桌面端是「Web 应用 + Electron 外壳」的组合：`desktop/src/main.js` 用一个**带系统原生框架**的 `BrowserWindow`（默认 `frame: true`）加载 `http://127.0.0.1:18761` 的 Next 应用，并通过 `buildMenu()` 安装了一条原生应用菜单（文件 / 编辑 / 视图 / 帮助）。于是用户看到的是「Windows 系统标题栏（含原生最小化 / 最大化 / 关闭三键）+ 原生菜单栏 + 应用自己的 `<header>`（`apps/web/app/page.tsx` 第 396–417 行）」三层叠在一起，顶部空间被浪费、视觉割裂，且系统灰色标题栏与应用 Anthropic 暖色调设计语言不协调。本轮目标是把这三层收敛为一层：**去掉系统标题栏与原生菜单栏，让应用自身的 `<header>` 直接成为窗口顶部**，并在该 Header 最右侧内置一组与设计语言一致的「最小化 / 最大化↔还原 / 关闭」窗口控制按钮。

技术主线由两段构成。**外壳层（desktop/）**：把窗口改为无边框（Windows/Linux `frame: false`；macOS 用 `titleBarStyle: 'hidden'` 保留左侧红绿灯并整合进内容区），移除原生应用菜单（`Menu.setApplicationMenu(null)`），并把菜单里有价值的动作（重载 / 开发者工具 / 缩放 / 全屏 / 关于 / 打开日志目录）以「窗口级键盘加速键 + 应用内轻量菜单」两条路径补偿回来，避免功能回归；同时通过 IPC 向渲染层暴露最小化 / 最大化切换 / 关闭 / 查询最大化状态等窗口控制能力，并把窗口 `maximize`/`unmaximize` 事件回推给渲染层以驱动「最大化↔还原」图标切换。**渲染层（apps/web/）**：把现有 `<header>` 标记为可拖拽窗口区域（`-webkit-app-region: drag`），其内所有交互子元素标记为 `no-drag` 以保持可点击；在 Header 最右侧渲染一个新的 `WindowControls` 组件，仅当检测到运行于桌面端（`window.aragon` 存在）时出现；双击拖拽区切换最大化，符合桌面操作系统的既有肌肉记忆。

之所以必须改动 `apps/web`（而非项目约定偏好的「仅在 desktop 层做胶水」）：窗口控制按钮在语义上属于应用 Header，它必须渲染在 React 组件树内，无法仅靠外壳实现。本方案以严格的运行期探测把这次例外的影响面压到最小——浏览器里 `window.aragon` 为 `undefined`，`WindowControls` 渲染为 `null`，`-webkit-app-region` 在普通浏览器中本就是无副作用的样式，故 Web 端零行为变化。最终交付一个**美观、优雅、符合人机交互最佳实践**的桌面窗口：统一的暖色顶栏、可拖拽移动、双击最大化、关闭键悬停转红（遵循 Windows 视觉规范）、键盘可达、明暗主题自适应、跨平台体面降级。

---

## 2. Goals / Non-Goals（本轮）

### Goals
1. **去系统框架**：桌面窗口不再显示系统原生标题栏与原生应用菜单栏。
2. **应用 Header 即窗口顶部**：现有 `<header>` 成为唯一顶栏，整条可拖拽移动窗口、双击切换最大化。
3. **应用内窗口控制**：Header 最右侧内置「最小化 / 最大化↔还原 / 关闭」按钮，图标随窗口状态切换，视觉与设计 token 一致、悬停/按下/聚焦态完整、键盘可操作。
4. **零功能回归**：原菜单的「重载 / 强制重载 / 开发者工具 / 缩放 / 全屏 / 关于 / 打开日志目录」通过键盘加速键 + 应用内轻量菜单补偿。
5. **跨平台体面**：Windows 主目标；macOS 保留左侧红绿灯（不画右侧自定义键），Linux 同 Windows 走自绘控制键；浏览器端零回归。
6. **生命周期不变**：关闭按钮 = 既有退出路径（`win.close()` → `before-quit` → `gracefulQuit()` 优雅终止前后端子进程 + 兜底端口回收），不触碰任何进程清理逻辑。

### Non-Goals
- 不实现 Windows 11 原生 Snap Layouts 悬停浮层（自绘控制键的已知取舍，见 §10 R-2）。
- 不改后端、策略、撮合、DB、接口、端口、打包流水线骨架（仅可能在 `verify-dist.mjs` 增加对新静态文件存在性的断言，见 §8.3）。
- 不引入任何新的运行期 npm 依赖（窗口控制仅用 Electron 内置 IPC + 项目已有的 `clsx`）。

---

## 3. 关键设计决策（Design Decisions）

- **D1 无边框策略按平台分流**：`win32`/`linux` → `frame: false` + 自绘右侧控制键；`darwin` → `titleBarStyle: 'hidden'` + `trafficLightPosition: { x: 16, y: 16 }`（在 48px 高 Header 内纵向居中红绿灯），渲染层在 mac 上隐藏自绘控制键、并为 Header 左侧留出约 78px 红绿灯安全区。理由：右侧自绘三键是 Windows 惯例；mac 用户期望左侧红绿灯，强行右置自绘键反而违反平台 HCI。
- **D2 移除原生菜单（按平台分流）**：`win32`/`linux` → `Menu.setApplicationMenu(null)` 彻底移除窗口内菜单栏（`autoHideMenuBar` 仅隐藏、Alt 仍可唤出，不符合「不需要菜单栏」诉求）。**`darwin` 例外**：mac 的全局应用菜单不在窗口内，用户诉求针对的是 Windows 窗口内菜单栏；mac 上保留**最小原生菜单**（`appMenu` + `editMenu`，可选 `viewMenu`），否则会连带丢失 Cmd+Q 与文本框内 Cmd+C/V/X/A/Z（剪贴板/全选/撤销经 `editMenu` role 提供），破坏 mac 标准 HCI（RV-2）。原菜单加速键另由 `webContents.on('before-input-event')` 在主进程窗口级补偿，保证 Ctrl+R / Ctrl+Shift+R / F12 / Ctrl+± / Ctrl+0 / F11 跨平台均可用。
- **D3 关闭键复用既有退出语义**：关闭按钮发 `win.close()`，落到既有 `before-quit`（`main.js` 第 435 行）→ `gracefulQuit()` 链路，**不新增任何进程终止代码**，杜绝退出清理回归。
- **D4 渲染层改动全部运行期守卫 + 挂载后判定**：以 `typeof window !== 'undefined' && !!window.aragon?.windowControls` 为唯一判据决定是否启用拖拽区 + 控制键；SSR 与浏览器两态均安全降级。**关键约束**：一切「依赖平台 / 是否桌面端」而会改变 DOM 输出的判定，**必须经由「首渲染恒 `false`、`useEffect` 内置真」的挂载后状态驱动**（`useIsDesktop()` 即此机制），不得在渲染期直接读 `window.aragon.platform` 决定 className——否则 Electron 中 preload 先于页面 JS 注入 `window.aragon`，客户端首渲染与服务端（无 `window`）输出不一致，触发 React 水合报错（mac 安全区 padding 即此坑，RV-3）。
- **D5 拖拽区与交互元素的 drag/no-drag 契约**：Header 根容器 `-webkit-app-region: drag`；其内**所有**可点击/可输入子元素（账户切换、模拟资金输入、主题切换、窗口控制键、应用菜单）必须 `-webkit-app-region: no-drag`，否则被拖拽层吞掉点击。该契约在 `globals.css` 提供 `.drag-region` / `.no-drag` 两个工具类承载。
- **D6 双击最大化手动接线**：无边框窗口下双击拖拽区最大化在 Windows 非自动，故在 Header 拖拽容器挂 `onDoubleClick`，并校验事件目标落在拖拽面（非 `no-drag` 控件）才调用 `toggleMaximize()`。
- **D7 加载窗口也要可拖拽可关闭**：`loading.html` 在同一无边框 `BrowserWindow` 内先行显示，必须自带拖拽区 + 关闭键（否则首启动后端慢时用户无法移动/关闭窗口）。经由同一 preload 的 `window.aragon.windowControls.close()` 实现，需放宽其 CSP 允许 `script-src 'self'` 并新增 `loading.js`。

---

## 4. Technical Design（技术设计）

### 4.1 外壳层：窗口创建（`desktop/src/main.js::createWindow`）

在现有 `opts` 上按平台追加无边框配置，其余（尺寸、最小尺寸、背景色、`webPreferences`、图标）保持不变：

```js
const isMac = process.platform === 'darwin';
const opts = {
  width: 1440, height: 900, minWidth: 1024, minHeight: 680,
  backgroundColor: '#0b0d12', title: 'Aragon Trading', show: true,
  // —— 新增：去系统框架 ——
  frame: false,                                   // win/linux：完全无边框，自绘控制键
  ...(isMac ? {                                   // mac：保留左侧红绿灯并整合进内容
    frame: true,
    titleBarStyle: 'hidden',
    trafficLightPosition: { x: 16, y: 16 },
  } : {}),
  webPreferences: { preload: path.join(__dirname, 'preload.js'),
    contextIsolation: true, nodeIntegration: false, sandbox: true },
};
```

> 说明：`resizable` 默认 `true`，无边框窗口下 Electron 默认仍保留四边/四角 resize 命中区。**但（RV-6）**：「四边可拉伸」与「最大化精确贴合工作区不越界」在 Windows 无边框窗口上是**版本相关**的已知风险点（最大化偶有轻微越出工作区遮挡任务栏 / 边缘内容被裁、四角命中区在个别版本偏窄），**不作「无需关注」断言**，须在 Windows 主目标按 §8.3 新增的两条验收实测确认；若实测越界，再以 CSS（`html,body` 满高、最大化态去阴影）或最大化时读 `screen.getDisplayNearestPoint(...).workArea` 校正窗口边界兜底（实施期按需，本节不预先复杂化）。

### 4.2 外壳层：移除菜单（按平台分流）+ 加速键补偿（替换 `buildMenu()`）

`bootstrap()` 第 366 行的 `buildMenu()` 调用改为 `installAppMenu()`——**按平台分流**（RV-2 / D2）：Windows/Linux 彻底移除窗口内菜单；macOS 保留最小原生菜单以维持 Cmd+Q 与剪贴板/撤销语义：

```js
function installAppMenu() {
  if (process.platform !== 'darwin') {
    // Windows / Linux：彻底移除窗口内菜单栏（用户诉求）。
    Menu.setApplicationMenu(null);
    return;
  }
  // macOS：全局菜单不在窗口内，保留最小菜单（appMenu 含 Cmd+Q；
  // editMenu 提供 Cmd+C/V/X/A/Z；viewMenu 提供重载/缩放/全屏），
  // 否则文本框剪贴板与撤销快捷键失效（RV-2）。
  Menu.setApplicationMenu(
    Menu.buildFromTemplate([
      { role: 'appMenu' },
      { role: 'editMenu' },
      { role: 'viewMenu' },
    ]),
  );
}
```

新增 `wireWindowShortcuts(win)`，在 `createWindow()` 内对 `win.webContents` 接线（跨平台补偿原视图菜单加速键；mac 上即便有 `viewMenu` 也保留，行为幂等）：

```js
function wireWindowShortcuts(targetWin) {
  targetWin.webContents.on('before-input-event', (event, input) => {
    if (input.type !== 'keyDown') return;
    const ctrl = input.control || input.meta;
    const key = (input.key || '').toLowerCase();
    const match = () => {
      if (ctrl && key === 'r') return targetWin.webContents.reload(), true;
      if (ctrl && input.shift && key === 'r')
        return targetWin.webContents.reloadIgnoringCache(), true;
      if (key === 'f12' || (ctrl && input.shift && key === 'i'))
        return targetWin.webContents.toggleDevTools(), true;
      if (key === 'f11')
        return targetWin.setFullScreen(!targetWin.isFullScreen()), true;
      if (ctrl && (key === '=' || key === '+'))
        return bumpZoom(targetWin, +0.5), true;
      if (ctrl && key === '-') return bumpZoom(targetWin, -0.5), true;
      if (ctrl && key === '0')
        return targetWin.webContents.setZoomLevel(0), true;
      return false;
    };
    if (match()) event.preventDefault();
  });
}
function bumpZoom(w, d) {
  w.webContents.setZoomLevel(w.webContents.getZoomLevel() + d);
}
```

`showAbout()`（第 218–247 行）保留不动，新增 IPC `aragon:show-about` 触发它，供应用内菜单调用（见 §4.4）。`require('electron')` 解构中的 `Menu` 仍保留（用于 `setApplicationMenu(null)`）。

### 4.3 外壳层：窗口控制 IPC（`main.js` 生命周期接线段，第 418–422 行附近）

```js
ipcMain.on('aragon:win:minimize', () => win && win.minimize());
ipcMain.on('aragon:win:maximize-toggle', () => {
  if (!win) return;
  win.isMaximized() ? win.unmaximize() : win.maximize();
});
ipcMain.on('aragon:win:close', () => win && win.close());
ipcMain.handle('aragon:win:is-maximized', () => !!win && win.isMaximized());
ipcMain.on('aragon:show-about', () => showAbout());
```

在 `createWindow()` 内、`win = new BrowserWindow(opts)` 之后，转发最大化状态变化给渲染层（驱动图标切换）：

```js
const pushMax = () =>
  win && win.webContents.send('aragon:win:maximized-changed', win.isMaximized());
win.on('maximize', pushMax);
win.on('unmaximize', pushMax);
win.on('enter-full-screen', pushMax);
win.on('leave-full-screen', pushMax);
wireWindowShortcuts(win);
```

### 4.4 外壳层：preload 桥（`desktop/src/preload.js`）

在现有 `exposeInMainWorld('aragon', {...})` 上扩展（保持 `appVersion`/`platform`/`openLogsDir` 不变）：

```js
const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('aragon', {
  isDesktop: true,
  appVersion: () => ipcRenderer.sendSync('aragon:get-version'),
  platform: process.platform,
  openLogsDir: () => ipcRenderer.invoke('aragon:open-logs'),
  showAbout: () => ipcRenderer.send('aragon:show-about'),
  windowControls: {
    minimize: () => ipcRenderer.send('aragon:win:minimize'),
    toggleMaximize: () => ipcRenderer.send('aragon:win:maximize-toggle'),
    close: () => ipcRenderer.send('aragon:win:close'),
    isMaximized: () => ipcRenderer.invoke('aragon:win:is-maximized'),
    onMaximizeChange: (cb) => {
      const handler = (_e, value) => cb(!!value);
      ipcRenderer.on('aragon:win:maximized-changed', handler);
      return () => ipcRenderer.removeListener('aragon:win:maximized-changed', handler);
    },
  },
});
```

> sandbox:true 下 preload 仍可用 `ipcRenderer`/`contextBridge` 子集，以上 API 全部可用。

### 4.5 外壳层：加载窗口可拖拽可关闭（`loading.html` + 新增 `loading.js`）

- CSP 改为 `default-src 'self'; style-src 'unsafe-inline'; script-src 'self'`。
- `body` 增加 `-webkit-app-region: drag`；新增右上角一个 `position: fixed` 的关闭按钮（`-webkit-app-region: no-drag`，`aria-label="关闭"`）。
- 新增 `desktop/src/loading.js`（外部脚本，CSP 允许）：`document.getElementById('loading-close').addEventListener('click', () => window.aragon?.windowControls?.close())`。

### 4.6 渲染层：桌面探测工具（新增 `apps/web/lib/desktop.ts`）

纯函数 + React hook，集中承载「是否桌面端 / 取窗口控制句柄 / 取平台」，便于单测：

```ts
import { useEffect, useState } from "react";

export interface WindowControls {
  minimize(): void;
  toggleMaximize(): void;
  close(): void;
  isMaximized(): Promise<boolean>;
  onMaximizeChange(cb: (max: boolean) => void): () => void;
}
export interface AragonBridge {
  isDesktop?: boolean;
  platform?: NodeJS.Platform;
  appVersion?(): string;
  openLogsDir?(): Promise<void>;
  showAbout?(): void;
  windowControls?: WindowControls;
}

export function getBridge(): AragonBridge | null {
  if (typeof window === "undefined") return null;
  return (window as unknown as { aragon?: AragonBridge }).aragon ?? null;
}
export function getWindowControls(): WindowControls | null {
  return getBridge()?.windowControls ?? null;
}
export function getDesktopPlatform(): NodeJS.Platform | null {
  return getBridge()?.platform ?? null;
}

/** SSR/浏览器安全：首渲染恒为 false，挂载后才置真，避免水合不一致。 */
export function useIsDesktop(): boolean {
  const [isDesktop, setIsDesktop] = useState(false);
  useEffect(() => { setIsDesktop(!!getWindowControls()); }, []);
  return isDesktop;
}
```

同步新增类型声明 `apps/web/types/aragon.d.ts`（`declare global { interface Window { aragon?: AragonBridge } }`），消除 `window.aragon` 的 TS 报错。

### 4.7 渲染层：窗口控制组件（新增 `apps/web/components/Desktop/WindowControls.tsx`）

- `"use client"`；用 `useIsDesktop()` 守卫，非桌面端返回 `null`；mac（`getDesktopPlatform()==='darwin'`）也返回 `null`（红绿灯在左侧由系统绘制）。
- 本地 `maximized` 状态：挂载时 `controls.isMaximized()` 初始化，并 `controls.onMaximizeChange(setMaximized)` 订阅、卸载时取消订阅。
- 渲染三枚 `no-drag` 按钮（最小化 / 最大化↔还原 / 关闭），尺寸 `h-12 w-[46px]`（与 Header 同高，贴合视觉系统的扁平控制带），SVG 线框图标（10px stroke，最大化态显示「还原」双叠框图标）：

```tsx
"use client";
import clsx from "clsx";
import { useEffect, useState } from "react";
import { getWindowControls, getDesktopPlatform, useIsDesktop } from "@/lib/desktop";

export default function WindowControls() {
  const isDesktop = useIsDesktop();
  const [maximized, setMaximized] = useState(false);
  useEffect(() => {
    const c = getWindowControls();
    if (!c) return;
    c.isMaximized().then(setMaximized);
    return c.onMaximizeChange(setMaximized);
  }, []);
  if (!isDesktop || getDesktopPlatform() === "darwin") return null;
  const c = getWindowControls();
  if (!c) return null;
  const base =
    "no-drag flex h-12 w-[46px] items-center justify-center text-[var(--muted)] " +
    "transition-colors duration-150 hover:bg-[var(--surface-2)] hover:text-[var(--text)] " +
    "focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--accent)]";
  return (
    <div className="no-drag flex items-stretch -mr-4 h-12">
      <button className={base} aria-label="最小化" onClick={() => c.minimize()}>{/* line icon */}</button>
      <button className={base} aria-label={maximized ? "向下还原" : "最大化"}
        onClick={() => c.toggleMaximize()}>{/* square / restore icon by `maximized` */}</button>
      <button className={clsx(base, "hover:bg-[#e81123] hover:text-white")}
        aria-label="关闭" onClick={() => c.close()}>{/* × icon */}</button>
    </div>
  );
}
```

> 关闭键悬停转 Windows 规范红 `#e81123`、白色叉，其余键悬停取 `--surface-2`；全部 `focus-visible` 走既有 `--accent` 焦点环（`globals.css` 第 86–89 行已定义全局，可复用）。`-mr-4` 抵消 Header 的 `px-4` 右内边距，使控制带贴到窗口右边缘（顶级窗口控件惯例）。

### 4.8 渲染层：应用内轻量菜单（新增 `apps/web/components/Desktop/AppMenu.tsx`）

补偿被移除的「关于 / 打开日志目录」等帮助项。渲染为 Header 左侧品牌区旁一枚 `no-drag` 的小图标按钮（⋯ 或 hamburger），点击弹出下拉：`关于 Aragon Trading`（`bridge.showAbout()`）、`打开日志目录`（`bridge.openLogsDir()`）、`重新加载`（`location.reload()`）。仅桌面端渲染（`useIsDesktop()` 守卫）。下拉用既有设计 token + `card` 类，点击外部/Esc 关闭，遵循无障碍（`role="menu"`、方向键导航、`aria-expanded`）。

### 4.9 渲染层：改造 Header（`apps/web/app/page.tsx` 第 396–417 行）

- 引入 `WindowControls`、`AppMenu`、`useIsDesktop`、`getDesktopPlatform`、`getWindowControls`，以及 `clsx`（既有依赖，用于条件 className；亦可改用模板字符串）。
- `<header>` 追加 `drag-region`；**保持顶层只有左右两个直接子簇**（现状为 `justify-between` + 两簇，`page.tsx:397–417`）。
- **左簇**：在品牌 logo/标题旁**仅追加** `<AppMenu/>`（自带 `no-drag`），其余保持纯展示、可拖拽（RV-5）。
- **右簇（RV-1 关键）**：把「账户/主题」与 `<WindowControls/>` 收进**同一个右侧 `no-drag` 容器**——**不可**把 `<WindowControls/>` 作为 `<header>` 的第三个同级子元素（否则 `justify-between` 会把账户/主题挤到水平居中、布局错位）。`-mr-4` 仅施于 `WindowControls` 使其贴窗口右缘（抵消 `<header>` 的 `px-4` 右内边距）。
- **mac 安全区（RV-3 关键）**：mac 红绿灯安全区 `pl-[84px]` **必须由挂载后状态驱动**，不得在渲染期直接按 `getDesktopPlatform()` 判定 className（否则 mac 水合不一致）。用一个首渲染恒 `false` 的 `macSafeArea` 状态承载：

```tsx
const isDesktop = useIsDesktop();           // 首渲染 false、挂载后置真（D4）
const [macSafeArea, setMacSafeArea] = useState(false);
useEffect(() => {
  setMacSafeArea(getDesktopPlatform() === "darwin");
}, []);

const onTitleDoubleClick: React.MouseEventHandler = (e) => {
  if ((e.target as HTMLElement).closest(".no-drag")) return; // 落在控件上不处理
  getWindowControls()?.toggleMaximize();
};

// 顶层仍是「左簇 / 右簇」两个直接子元素，justify-between 不被破坏：
<header
  className={clsx(
    "drag-region flex h-12 shrink-0 items-center justify-between",
    "border-b border-[var(--border)]",
    macSafeArea ? "pl-[84px] pr-4" : "px-4",
  )}
  onDoubleClick={onTitleDoubleClick}
>
  <div className="flex items-center gap-2">
    {/* 品牌 logo / 标题（纯展示，可拖拽）*/}
    <AppMenu />                {/* no-drag，仅桌面端渲染 */}
  </div>
  <div className="no-drag flex items-center gap-2">
    <AccountModeSwitch />
    {/* 主题切换按钮 */}
    <WindowControls />         {/* 内部 -mr-4 贴右缘；mac/浏览器自渲染为 null */}
  </div>
</header>
```

> Header 高度维持 `h-12`（48px），与窗口控制带等高，视觉为一条完整顶栏。`WindowControls` 在浏览器/ mac 各自降级为 `null`（§4.7），故右簇在这些环境下退化为「账户/主题」原貌，零回归。

### 4.10 渲染层：样式工具类（`apps/web/app/globals.css` 末尾追加）

```css
/* 桌面端自定义标题栏：拖拽 / 非拖拽区域（浏览器中为无副作用样式，自动降级） */
.drag-region { -webkit-app-region: drag; }
.no-drag,
.drag-region button,
.drag-region input,
.drag-region a,
.drag-region [role="radiogroup"],
.drag-region [role="switch"] { -webkit-app-region: no-drag; }
```

> 兜底规则把拖拽区内一切可交互元素自动置 `no-drag`，与 D5 契约形成「显式 class + 结构兜底」双保险，降低后续在 Header 加控件时漏标 `no-drag` 的回归风险。

### 4.11 时序（关键交互）

```text
[启动] main.bootstrap → Menu.setApplicationMenu(null) → createWindow(frame:false)
       → loadFile(loading.html)（可拖拽 + 关闭键）→ startEngines 就绪 → loadURL(WEB_URL)
[最大化] 用户点「最大化」/双击顶栏 → preload send aragon:win:maximize-toggle
       → main: win.maximize()/unmaximize() → win 'maximize' 事件 → webContents.send
       → preload onMaximizeChange → WindowControls setMaximized(true) → 图标切「还原」
[关闭]  用户点「关闭」→ send aragon:win:close → main win.close()
       → before-quit → gracefulQuit()（停前后端 + 端口回收）→ app.exit(0)
```

---

## 5. File / Module Change Plan（文件 / 模块变更计划）

| 文件 | 动作 | 一句话意图 |
|---|---|---|
| `desktop/src/main.js` | 改 | `createWindow()` 加 `frame:false`/mac `titleBarStyle`；`bootstrap` 用 `setApplicationMenu(null)` 替 `buildMenu()`；新增窗口控制 IPC + 最大化状态转发 + `wireWindowShortcuts` 加速键补偿 + `aragon:show-about`。 |
| `desktop/src/preload.js` | 改 | 扩展 `window.aragon`：`isDesktop`、`showAbout`、`windowControls`（minimize/toggleMaximize/close/isMaximized/onMaximizeChange）。 |
| `desktop/src/loading.html` | 改 | 放宽 CSP 允许 `script-src 'self'`；`body` 设拖拽区；加右上角关闭键；引入 `loading.js`。 |
| `desktop/src/loading.js` | 新增 | 加载窗口关闭键 → `window.aragon.windowControls.close()`。 |
| `apps/web/lib/desktop.ts` | 新增 | 桌面探测与窗口控制句柄获取：`getBridge`/`getWindowControls`/`getDesktopPlatform`/`useIsDesktop`。 |
| `apps/web/types/aragon.d.ts` | 新增 | `Window.aragon` 全局类型声明，消除 TS 报错。 |
| `apps/web/components/Desktop/WindowControls.tsx` | 新增 | 右侧最小化 / 最大化↔还原 / 关闭按钮，状态驱动图标，桌面端 + 非 mac 才渲染。 |
| `apps/web/components/Desktop/AppMenu.tsx` | 新增 | 左侧轻量菜单，补偿关于 / 打开日志 / 重载（仅桌面端）。 |
| `apps/web/app/page.tsx` | 改 | Header 加 `drag-region` + 双击最大化 + mac 左安全区；右簇加 `no-drag`；挂 `<AppMenu/>`、`<WindowControls/>`。 |
| `apps/web/app/globals.css` | 改 | 追加 `.drag-region` / `.no-drag` 工具类（含结构兜底）。 |
| `desktop/scripts/verify-dist.mjs` | 改（可选增强） | `loading.js` 随 `files: ["src/**/*"]` 自动进 **`app.asar`**（非 `resources/` 散文件），无需打包改动；**不**新增 `existsSync(resources/loading.js)` 断言（恒假，RV-4）。如确需冒烟校验，应以 `@electron/asar` 列出 asar 内容断言 `src/loading.js` 在列。 |
| `apps/web/lib/desktop.test.ts` | 新增 | 纯函数单测：`window.aragon` 缺失时 `getWindowControls()` 返回 `null`、平台读取正确。 |

---

## 6. Interface Design（接口设计 · IPC）

全部为本地 Electron IPC，**无 REST/WS/CLI 变更**。`R`=渲染层→主进程，`M`=主进程→渲染层。

| 通道 | 方向 | 机制 | 入参 | 返回 | 语义 |
|---|---|---|---|---|---|
| `aragon:win:minimize` | R→M | `send`/`on` | — | — | 最小化当前窗口。 |
| `aragon:win:maximize-toggle` | R→M | `send`/`on` | — | — | 已最大化则还原，否则最大化。 |
| `aragon:win:close` | R→M | `send`/`on` | — | — | `win.close()`，落既有退出清理链。 |
| `aragon:win:is-maximized` | R→M | `invoke`/`handle` | — | `boolean` | 查询当前最大化态（组件初始化用）。 |
| `aragon:win:maximized-changed` | M→R | `webContents.send`/`on` | `boolean` | — | 窗口最大化/还原/全屏切换时回推，驱动图标。 |
| `aragon:show-about` | R→M | `send`/`on` | — | — | 触发既有 `showAbout()` 对话框。 |
| `aragon:get-version`（既有） | R→M | `sendSync` | — | `string` | 应用版本（不变）。 |
| `aragon:open-logs`（既有） | R→M | `invoke`/`handle` | — | `Promise<void>` | 打开日志目录（不变）。 |

preload 暴露的 `window.aragon` 契约见 §4.4；渲染层 TS 接口见 §4.6（`AragonBridge` / `WindowControls`）。

---

## 7. Data Model（数据模型）

无数据库、无持久化变更。唯一新增状态为渲染层内存态：

- `WindowControls` 组件局部 `maximized: boolean` —— 由 `isMaximized()` 初始化、`onMaximizeChange` 订阅维护，仅用于切换「最大化 / 还原」图标与 `aria-label`，不入 Zustand、不入 `localStorage`。
- `AppMenu` 组件局部 `open: boolean` —— 下拉开合态，纯 UI。

CSS 层新增两个全局工具类（`.drag-region` / `.no-drag`），属样式契约而非数据。

---

## 8. Testing & Acceptance（测试与验收）

### 8.1 单元测试（apps/web，遵循「lib 纯函数 + .test.ts」约定）
- `apps/web/lib/desktop.test.ts`：
  - `getWindowControls()` 在无 `window.aragon` 时返回 `null`（模拟浏览器）。
  - 注入 `window.aragon = { platform:'win32', windowControls:{...} }` 后，`getWindowControls()` 返回该句柄、`getDesktopPlatform()` 返回 `'win32'`。
  - `getBridge()` 在 `window` 未定义（SSR 模拟）时返回 `null`，不抛错。

### 8.2 构建 / 类型
- `apps/web` `pnpm build` 成功（无 SSR 期访问 `window` 报错、无 TS 报错，`aragon.d.ts` 生效）。
- `pnpm lint` 通过。

### 8.3 桌面端手动验收（Windows，主目标）
1. `desktop/` 下 `npm run dev` 启动：**无系统标题栏、无原生菜单栏**；应用 `<header>` 即窗口顶部。
2. 拖拽 Header 空白处可移动窗口；双击 Header 空白处在「最大化↔还原」间切换。
3. 右侧三键：最小化生效；最大化后图标变「还原」、再点还原；关闭 → 应用退出且前后端子进程被清理（既有行为不回归，可经日志/端口检查确认 18760/18761 释放）。
4. 关闭键悬停转红、其余键悬停高亮；Tab 可聚焦三键并 Enter/Space 触发；焦点环可见。
5. 账户切换、模拟资金输入框、主题切换在 Header 内**仍可点击/输入**（`no-drag` 生效，未被拖拽吞掉）。
6. 加载页（首启动后端就绪前）：窗口可拖拽、右上角关闭键可关闭。
7. 键盘加速键：Ctrl+R 重载、F12 开发者工具、Ctrl+±/0 缩放、F11 全屏均生效。
8. `AppMenu`：关于对话框弹出、打开日志目录生效。
9. （回归）浏览器跑 `pnpm dev`（仅 Web）：Header 无窗口控制键、无 `AppMenu`、行为与改动前一致。
10. **（RV-6）最大化贴合**：点最大化 / 双击顶栏最大化后，窗口**不遮挡任务栏**、Header 与四周内容**不被裁切 / 不越出工作区**；还原后位置尺寸正常。
11. **（RV-6）无边框 resize**：四边、四角均可正常拖拽改变窗口尺寸（命中区可用）。
12. **（RV-1）顶栏布局**：账户/主题簇紧邻窗口控制带、整体靠右成一簇；左品牌簇靠左；中间无异常空档（验证未被 `justify-between` 挤偏）。
13. （可选）`verify-dist.mjs` 冒烟通过；如加 asar 校验则确认 `app.asar` 内含 `src/loading.js`（**不**校验 `resources/loading.js`，RV-4）。

### 8.4 验收标准（Definition of Done · 实施阶段）
- 上述 8.3 第 1–12 条全部通过（含 RV-1 顶栏布局、RV-6 最大化贴合与 resize），第 13 条可选；8.1/8.2 全绿。
- macOS（若可测）：顶栏保留左侧红绿灯且不渲染右侧自绘键；**保留最小原生菜单**，Cmd+Q 与文本框 Cmd+C/V/X/A/Z 仍可用（RV-2）；无 React 水合报错（RV-3）。

---

## 9. 实施顺序建议（供 Subtask #2）

1. 外壳层先行：`preload.js` 扩展 → `main.js`（frame/menu/IPC/快捷键）→ `loading.html`+`loading.js`。先确保 `npm run dev` 出无边框窗口且 IPC 可被 DevTools `window.aragon.windowControls` 手测。
2. 渲染层：`lib/desktop.ts` + `types/aragon.d.ts` → `WindowControls.tsx` → `globals.css` 工具类 → `page.tsx` 接线 → `AppMenu.tsx`。
3. 测试与冒烟：`desktop.test.ts` → `pnpm build`/`lint` → 桌面手动验收 → 可选 `verify-dist.mjs`。

---

## 10. Risks & Mitigations（风险与缓解）

| 编号 | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R-1 | SSR/水合不一致：服务端无 `window`，若首渲染就读 `window.aragon`（含**按平台决定 className**）会报错或水合错配。 | 构建失败 / 水合报错 / 闪烁。 | `useIsDesktop()` 首渲染恒 `false`、`useEffect` 内置真（D4）；`getBridge()` 对 `typeof window==='undefined'` 早返回 `null`；**mac 安全区 padding 亦由挂载后 `macSafeArea` 状态驱动，绝不在渲染期读 `platform` 定样式**（RV-3 / §4.9）。 |
| R-2 | 自绘控制键丢失 Windows 11 Snap Layouts 悬停浮层。 | 高级用户少一个贴靠入口。 | 取舍记录：键盘 `Win+方向`、拖拽到屏缘的贴靠仍可用；如未来需要可改用 `titleBarOverlay`（WCO）方案，列为后续增强，本轮不做。 |
| R-3 | 拖拽区吞掉交互子元素点击/输入（漏标 `no-drag`）。 | 账户切换/输入失灵。 | D5 显式 `no-drag` + `globals.css` 结构兜底规则双保险；验收 8.3-5 专项核对。 |
| R-4 | 无边框窗口在加载阶段无法移动/关闭（后端慢）。 | 用户被「钉死」在加载页。 | D7：`loading.html` 拖拽区 + 关闭键 + `loading.js`，复用同一 preload。 |
| R-5 | macOS 红绿灯与左侧品牌区重叠。 | mac 顶栏错位。 | D1：`titleBarStyle:'hidden'` + `trafficLightPosition` + Header **挂载后** `pl-[84px]` 安全区（RV-3，避免水合错配）；自绘键在 mac 隐藏。 |
| R-6 | 移除原生菜单导致重载/调试/缩放/全屏/关于/日志能力丢失；mac 上更连带丢失 Cmd+Q 与剪贴板/撤销。 | 开发与运维体验回归；mac 标准 HCI 破坏。 | `wireWindowShortcuts` 键盘补偿 + `AppMenu` 应用内补偿；**菜单移除按平台分流**——win/linux 全移除、mac 保留 `appMenu`+`editMenu`(+`viewMenu`)（RV-2 / D2 / §4.2）。 |
| R-7 | 双击控件误触发最大化。 | 误操作。 | D6：`onDoubleClick` 校验 `closest('.no-drag')` 才放行。 |
| R-8 | `loading.js` 漏入打包产物 → 加载页关闭键失效。 | 打包后加载页不可关。 | `loading.js` 置于 `desktop/src/`，由既有 `files: ["src/**/*"]` glob **自动进 `app.asar`**，无需新增打包配置（RV-4 已纠正：它**不**是 `resources/` 散文件，故不可用 `existsSync(resources/loading.js)` 断言）；可选以 `@electron/asar` 列 asar 校验。 |
| R-9 | 退出语义被无意改动 → 子进程残留占端口。 | 端口泄漏回归。 | D3：关闭仅 `win.close()`，不触碰 `gracefulQuit()`/`before-quit`；验收 8.3-3 经端口检查确认。 |
| R-10 | 改 `apps/web` 违背「桌面差异下沉到 desktop 层」的索引约定。 | 一致性质疑。 | 本需求本质要求 Header 内渲染窗口控件，无法纯外壳实现；以运行期 `window.aragon` 守卫把影响面限定在桌面态、Web 端零回归，作为有据可依的受控例外（§1 末段）。 |

---

## 11. 一致性与约定遵循

- 复用现有设计 token（`--surface-2`/`--muted`/`--text`/`--accent` 与 `tailwind.config.ts` 的 `ease-smooth`/`shadow-soft`），不引入新色板；关闭键红 `#e81123` 为 Windows 平台规范例外，仅用于该键悬停态。
- 文件尺寸/函数复杂度遵循 `CLAUDE.md` 阈值：新增组件均为单一职责小文件（< 120 行），`main.js` 改动以小函数（`wireWindowShortcuts`/`bumpZoom`/`pushMax`）承载，避免膨胀。
- TypeScript 用 `interface` 描述公共契约（`AragonBridge`/`WindowControls`）；错误处理上窗口控制为「触发即转发」的 IPC，无需吞异常；preload 句柄缺失时渲染层显式降级（返回 `null`）而非静默假装。
- 不新增运行期 npm 依赖；`clsx` 为既有依赖。
- **菜单移除按平台分流**（RV-2）：win/linux 全移除以满足「不要菜单栏」；mac 保留最小原生菜单维持 Cmd+Q 与剪贴板/撤销，符合各平台 HCI 规范。

---

## 评审结论（Review Verdict · Subtask #1）

**判定：有条件通过（Approved with Conditions）。**

方案技术主线（无边框窗口 + 应用 Header 即顶栏 + 自绘窗口控制键 + `window.aragon` 运行期守卫 + 关闭复用既有 `gracefulQuit` 退出链）**可行、与现有 monorepo 架构契合、规模适配恰当**。评审已逐行核对真实源码，行号 / 设计 token / 依赖 / 测试设施均与代码一致。

发现 **0 个 P0、3 个 P1、5 个 P2**。**全部 3 个 P1 已在本 v2 正文就地修复**：

- **RV-1（P1，§4.9）**：顶栏 `justify-between` 三子元素布局错位 → 改为左右两簇、右簇内聚窗口控制键。
- **RV-2（P1，§4.2/D2/R-6）**：mac 上 `setApplicationMenu(null)` 破坏 Cmd+Q / 剪贴板 → 改为按平台分流保留 mac 最小菜单。
- **RV-3（P1，§4.9/D4/R-1/R-5）**：mac 安全区 padding 渲染期判定致水合不一致 → 改为挂载后状态驱动。

**放行条件（Subtask #2 实施 / 验收期须满足，对应 P2，均不阻塞动工）**：

1. **C-1（RV-6）**：Windows 主目标实测「最大化不遮挡任务栏 / 内容不被裁」与「四边四角可 resize」（§8.3 第 10–11 条）；若实测越界，按 §4.1 注的兜底校正处理。
2. **C-2（RV-4）**：`verify-dist.mjs` **不得**新增 `existsSync(resources/loading.js)` 断言（恒假）；如需校验改走 `app.asar` 列举。
3. **C-3（RV-8）**：`AppMenu` 保持最小（仅 关于 / 打开日志 / 重载 三项，单文件 < 120 行），不扩展为通用菜单框架。
4. **C-4（RV-5/RV-7）**：落地后顶栏与左右簇布局须与 §4.9 JSX 骨架一致（`AppMenu` 在左簇、`WindowControls` 在右簇内并 `-mr-4` 贴缘）；`apps/web` 改动全程以 `window.aragon` 守卫，确保 Web 端零回归。

满足上述条件即视为达成本特性「美观、优雅、符合人机交互最佳实践」的交付目标。

> 评审范围说明：本节及「评审记录」仅修改 `spec.md`，未触碰任何源码（遵循 Subtask #1 约束）。源码落地由 Subtask #2 依本 v2 执行。
