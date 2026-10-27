# 打包桌面端后顶栏右侧仍不可点（WCO 已落地却报「还是有问题」）—— 问题定位与分析

- **Bug slug**: `desktop-packaged-wco-header-right-still-unclickable`
- **诊断节点**: Subtask #0（问题定位与分析，本节点**不写修复代码、不 git commit**）
- **日期**: 2026-06-29
- **本轮来源**: 多轮迭代第 2 轮。第 1 轮需求与上一迭代第 4 轮同字面（「整个 header 右侧无法点击 + 模拟盘/实盘切不动」），第 4 轮诊断 `desktop-header-right-and-live-toggle-unclickable` 已**裁定升级 A3（Windows Controls Overlay）**并据此落地了 WCO 修复。本轮用户反馈：**「没修改好，打包为桌面端之后还是有问题，继续修复」**。

## 结论先行（单一阻断根因 + 一条待证支线）

> **本轮决定性新事实：WCO 修复确已正确写进源码、且确已烤进当前唯一的最新桌面构建（`desktop/dist`，06-29 01:57），构建比源码新、产物内含全部 WCO 标记、无任何陈旧/已安装副本在跑。** 也就是说——「补丁没写 / 没构建 / 跑旧包」这条上一轮已排除的廉价可能，本轮再次被实证排除。
>
> **然而本轮无法闭合，根因是 RC-1（构建身份不可辨识 / 溯源戳失明）**：WCO 改动**至今未提交**（工作区 6 个文件 dirty），而打包脚本 `pack.ps1:313-318` 的溯源戳取的是 `git rev-parse --short HEAD`——即**不含 WCO 的提交 `efa83f6`**。实测打进 asar 的 `package.json` 为 `"aragonCommit":"efa83f6"`。这意味着：**含 WCO 的新构建与不含 WCO 的旧构建，在「关于」对话框里显示的构建戳完全相同（都是 efa83f6）**。上一轮专门加入溯源戳就是为了「把『跑了旧会话』与『真代码 bug』一眼区分」——这套机制被「WCO 未提交」彻底击穿。**因此用户的「还是有问题」到底来自『跑了 WCO 构建仍失效』还是『跑了 WCO 落地前的旧会话/旧构建』，目前无法判别**，整个迭代环就此卡死。
>
> **RC-2（条件支线，仅当 RC-1 证明用户确跑了 WCO 后才成立）**：窗口仍为默认 `resizable:true`（`WS_THICKFRAME` 未去除，正是历轮几何触发源）。WCO 本应由 Chromium 原生 caption 命中测试**中和**还原态顶边非客户命中带，但**该效果从未在用户真实 Windows 上被人工确认**（上一轮明确把验收下沉为「真机人工门」）。源码侧唯一可 headless 复核的 WCO 安全区 `env(titlebar-area-*)` 经验证**正确无误**，故 RC-2 若成立只会是 Electron/Windows 版本相关的运行期缺口，而非源码缺陷。
>
> **症状 B（模拟盘/实盘切不动）仍是 A 的下游**，与上一轮一致：分段开关长在右簇里，右簇可点即可切（`AccountModeSwitch.tsx:62-89` 状态④收到点击即 `setAccountMode("live")`）。

---

## 一、问题描述（Problem description）

第 2 轮用户报障（原文）：

> 没修改好，打包为桌面端之后还是有问题，继续修复

承接第 1 轮（同字面于上一迭代第 4 轮）的两个症状：

- **症状 A**：桌面端顶栏**右侧整片**控件——账户模式（模拟盘/实盘）、主题、Factor、设置齿轮、最小化/最大化/关闭——单击无反应；左侧品牌区不受影响。
- **症状 B**：右簇内「模拟盘/实盘」分段点了不切换。

**本轮的关键新增语义**：用户**已经按上一轮的推荐做了「打包为桌面端」**这一动作（WCO 已落地后打包），结果**仍报障**。本轮的核心问题因此从「该怎么修」变成：**「WCO 已经打进包了，为什么还不行？」**——这要求先证伪/证实「用户跑的到底是不是 WCO 构建」，再谈 WCO 本身是否有残留缺陷。

---

## 二、复现步骤（Reproduction steps）

### 2.0 运行目标与端口

- 桌面后端固定 `18760`、前端 `18761`（`desktop/src/lib/config.js`）。
- 唯一可运行的交易端二进制：`desktop/dist/win-unpacked/Aragon Trading.exe`（`appId=com.aragon.trading`、`productName=Aragon Trading`，`desktop/electron-builder.yml:4-9`）。
- 干扰排除：`M:/takoAI/JRAgentMesh/desktop/release/win-unpacked/AragonMesh.exe` 是 **AgentMesh 编排器**（当前有 5 个该进程在跑），**不是**交易端，与本 bug 无关（无 `<header>`/`drag-region`）。

### 2.1 症状 A：顶栏右侧不可点（端到端）

1. 启动 `desktop/dist/win-unpacked/Aragon Trading.exe`（或 `desktop/` 下 `npm run dev`；两者共用 `main.js` 的 `createWindow`，窗口 1440×900 **还原态**开箱即受影响态，`main.js:232-237`）。
2. 确保窗口处于**还原态**（非最大化，默认即是）。
3. 单击顶栏右侧「模拟盘/实盘」「主题」「Factor」「设置齿轮」「窗口控制键」→ 观察是否无反应。
4. 最大化铺满屏幕，再次单击右侧控件 → 历轮规律是「最大化可点、还原不可点」（几何缺陷签名）。

### 2.2 症状 B：切不动（实为 A 的下游）

```bash
# 后端在线时看默认账户是否就绪（决定 selectLive 走哪条分支）
curl -s http://127.0.0.1:18760/api/accounts
```
上一轮实测回包为就绪默认账户（`hasSecret/hasPassphrase` 全真、`defaultId=1`）→ `selectLive` 命中状态④「无条件切 live」（`AccountModeSwitch.tsx:74-78`）。即逻辑层**收到点击就会切**，「切不动」只能是点击没下发——故 B 是 A 的下游。

### 2.3 ⚠️ 强制 pre-flight：先判别「用户跑的是不是 WCO 构建」（本轮新增、最关键）

由于溯源戳失明（§三 RC-1），**必须**先用下列步骤确定运行体里到底有没有 WCO，否则一切后续判断都是空中楼阁：

1. **看「关于」对话框的构建戳**：当前一切构建都显示 `构建：efa83f6`（实测见 §三），**无法据此判别**——这正是 bug 的一部分。
2. **改看 OS 三键是否由系统绘制**：WCO 生效时，最小化/最大化/关闭三键由**系统**在右上角绘制（样式与 Windows 原生一致，且不随主题外的 app 配色变化）；若看到的是 app **自绘**的三键（细线 SVG、hover 变 `#e81123`），说明跑的是 WCO 落地前的旧渲染层（`WindowControls.tsx` 旧版会在 win 自绘）。
3. **若确为系统三键（WCO 已生效）仍不可点** → 进入 RC-2 排查（§三 3.4）。
4. **若为 app 自绘三键** → 用户跑的是**旧构建/旧会话**，按 §五 F1 先让构建身份可辨识后重测。

---

## 三、根因分析（Root cause analysis）

### 3.1 WCO 修复在源码侧确已完整落地（逐项证据）

| 环节 | 证据（path:line） | 内容 |
|---|---|---|
| 窗口按平台分流，win 用 WCO | `desktop/src/main.js:229-260` | `isWin ? { titleBarStyle:'hidden', titleBarOverlay: overlayForTheme('dark') } : ...`；**win 分支无 `frame:false`** |
| 主题同步系统三键配色 | `desktop/src/main.js:523-526` | `aragon:win:set-overlay-theme` → `win.setTitleBarOverlay(...)` |
| preload 暴露 setOverlayTheme | `desktop/src/preload.js:24` | `setOverlayTheme: (t)=>ipcRenderer.send('aragon:win:set-overlay-theme', t)` |
| win 端不自绘三键（避免与系统重影） | `apps/web/components/Desktop/WindowControls.tsx:27` | `if (!isDesktop || platform==="darwin" \|\| platform==="win32") return null;` |
| 右簇让位系统三键安全区 | `apps/web/app/page.tsx:490-495`、`:527` | `winOverlay` 状态 + header `winOverlay ? "win-titlebar-safe pl-4" : ...` |
| 安全区 CSS | `apps/web/app/globals.css:149-155` | `.win-titlebar-safe{padding-right:calc(100vw - env(titlebar-area-width,100vw) - env(titlebar-area-x,0px))}` |
| 主题切换驱动配色同步 | `apps/web/app/page.tsx:480-484` | `getWindowControls()?.setOverlayTheme?.(theme)` |

### 3.2 决定性新证据：WCO 确已烤进最新构建，构建比源码新、非陈旧

**(1) 构建比源码新**（说明构建已纳入当前 WCO 源码，非旧产物）：

```
源码 mtime:  main.js 01:38 · preload.js 01:34 · page.tsx 01:35 · globals.css 01:35 · WindowControls.tsx 01:34
构建 mtime:  app.asar 01:55:56 · resources/web/.next/server/app/page.js 01:55:10 · NSIS 安装包 01:57:34
```
构建（01:55–01:57）**晚于**全部源码（01:34–01:38）→ 构建反映当前 WCO 工作区。

**(2) asar（主进程）含 WCO 标记**：`grep` `desktop/dist/win-unpacked/resources/app.asar` → `titleBarStyle`、`titleBarOverlay`、`set-overlay-theme`、`setOverlayTheme` 均命中。

**(3) resources/web（渲染层）含 WCO 标记且编译正确**：
- `desktop/dist/win-unpacked/resources/web/.next/static/css/e28321b872bcfae2.css` 实测：
  `win-titlebar-safe{padding-right:calc(100vw - env(titlebar-area-width, 100vw) - env(titlebar-area-x, 0px))}`（**未被 PostCSS 破坏**），`-webkit-app-region:drag` / `:no-drag` 规则均在位。
- `page.js` / `static/chunks/app/page-*.js` 均含 `win-titlebar-safe`。

**(4) 无陈旧/已安装副本干扰**：`%LOCALAPPDATA%\Programs` 下**无** `Aragon Trading` 已安装副本；进程表中**无** `Aragon Trading.exe` 在跑（仅 AragonMesh 编排器）。

**结论**：「补丁没写 / 没构建 / 跑旧包」三条本轮再次被实证排除。**WCO 这套代码本身确实进了用户能跑的最新包。**

### 3.3 RC-1（本轮阻断根因）：溯源戳失明——WCO 构建与旧构建无法区分

这是本轮**最关键、可 headless 钉死**的发现：

- WCO 改动**至今未提交**：`git status --porcelain` 中 `main.js / preload.js / page.tsx / globals.css / WindowControls.tsx / desktop.ts` 共 **6 个文件 dirty**；`git log` 当前 `HEAD=efa83f6`（`fix: live auto-trade uses checkbox-selected strategy`），**早于** WCO 落地。
- 打包脚本溯源戳取 HEAD、**不含 dirty 标记**：`desktop/pack.ps1:313-318`
  ```powershell
  $rev = & git -C $RepoRoot rev-parse --short HEAD ...
  if (...) { $aragonCommit = "$rev".Trim() }
  ...
  $builderArgs += "--config.extraMetadata.aragonCommit=$aragonCommit"
  ```
- **实测打进包的戳**：从 `desktop/dist/win-unpacked/resources/app.asar` 抽出 `package.json` →
  ```json
  { "name": "aragon-desktop", "version": "0.1.0", ..., "aragonCommit": "efa83f6" }
  ```
- 主进程读取并展示：`desktop/src/main.js:285-294`（`buildCommit()` 读 `pkg.aragonCommit`）→ `:314-315` 在「关于」追加 `构建：efa83f6`。

**推论（致命）**：因为 WCO 一直没提交，HEAD 始终是 `efa83f6`，所以**含 WCO 的 01:57 构建**与**WCO 落地前的旧构建（上一轮记录的 00:57 包）** 烤进的 `aragonCommit` **都是 `efa83f6`**。**「关于」对话框对二者显示完全相同的构建戳**。上一轮专门为「一眼区分『旧会话』vs『真 bug』」加入的溯源戳机制，被「WCO 未提交」**完全击穿**。

→ 这直接导致：**用户报「打包后还是有问题」时，无人能证明用户跑的到底是不是 WCO 构建。** 迭代环因此无法收敛——每轮重打包、用户重测、却没人能确认测的是哪一版。**这既是一个独立缺陷（溯源戳应反映 dirty 树），也是本轮无法闭合的直接原因。**

### 3.4 RC-2（条件支线）：WCO 对还原态几何缺陷的中和未经真机确认

仅当 §2.3 pre-flight 证明「系统三键已出现（WCO 生效）却仍不可点」时，RC-2 才成立：

- 窗口仍为默认 `resizable:true`：`grep resizable|thickFrame desktop/src/main.js` → **0 命中**，`opts`（`main.js:231-260`）未设 `resizable` → 默认 `true` → `WS_THICKFRAME`，OS 在窗口顶边保留 resize 非客户命中带。这正是历轮（`desktop-titlebar-buttons-unclickable-when-restored`）实测的几何触发源，**本轮未改动**。
- WCO 的作用是把 caption 命中交还 Chromium 原生标题栏命中测试，**理应**在还原态正确区分「顶边细 resize 带（NC）」与「标题栏控件（HTCLIENT）」，从而中和该缺陷——这是 WCO 作为业界标准方案的预期行为。
- **但**：OS 非客户区命中测试发生在浏览器进程、渲染层不可观测，`sendInputEvent` 绕过 NC 命中，**无法 headless 录到「click 未下发」**（上一轮已自陈此局限）。WCO 是否真的中和了该缺陷，**只能在用户真实 Windows 还原态人工点击确认**——而这道门**从未被执行过**（上一轮把验收下沉为人工门后即收尾）。
- 源码侧唯一可 headless 复核的 WCO 失效点是安全区 `env(titlebar-area-*)`：经 §3.2(3) 验证**编译正确**。若它在用户机上回退到默认值（`100vw`/`0px`）使 `padding-right` 归零，右簇最右几枚控件（设置齿轮/Factor）会滑入系统三键覆盖区而不可点；但此为 Electron/Windows 版本相关的运行期表现，非源码缺陷，且需真机佐证。

### 3.5 排除项（已证伪）

- **非构建陈旧 / 跑旧包**：§3.2(1)(2)(3)(4) 实测构建新鲜、含 WCO、无旧副本。
- **非 CSS 编译破坏**：§3.2(3) 实测 `win-titlebar-safe` 与 `app-region` 规则在编译产物中完好。
- **非 toggle 切换逻辑**：症状 B 是 A 的下游，逻辑层「收到点击即切」（`AccountModeSwitch.tsx:62-89`，`AccountModeSwitch.test.tsx` 有单测护栏）。
- **非 mac**：mac 走 `titleBarStyle:'hidden'` + 系统红绿灯（`main.js:247-252`），不受影响。

---

## 四、影响面（Impact surface）

| 维度 | 说明 |
|---|---|
| 平台 | **Windows**（本轮报障平台 Windows 11 / `10.0.26200`）。linux 维持 frameless 旧路径（`main.js:253`），mac 不受影响。 |
| 窗口态 | 还原 / 普通浮动态（默认启动态）；最大化态历轮正常。 |
| 受影响功能（症状 A） | 顶栏右簇全部：模拟盘/实盘、主题、Factor、设置齿轮、最小化/最大化/关闭。 |
| 受影响功能（症状 B / 连带） | 整条**实盘链路入口**：右簇不可点 → 无法进 `accountMode==="live"` → `LiveBottomPanel`（实盘下单/持仓）不渲染 → 实时自动交易（守门要求 live）不可达。 |
| **流程影响（本轮新增）** | **迭代环无法收敛**：溯源戳失明使「用户测的是不是 WCO 构建」不可判别，每轮重打包→重测→无法确认版本，**可能无限复发**。 |
| 严重度 | **高**——既是功能阻断，又是流程阻断（无法验收）。 |
| 误诊风险 | 极易再被当作「WCO 没生效 / 又一轮 CSS 微调」处理；§2.3 pre-flight + §3.2/§3.3 取证用于**一键证伪**误诊。 |

---

## 五、候选修复方案对比（Candidate fixes）

> 本节仅比较，**不写修复代码**（留给 Subtask #2）。分两组：A 组解决「构建身份不可辨识」（RC-1，必做、可 headless 验收）；B 组解决「WCO 残留命中缺陷」（RC-2，仅 A 组证明用户确跑 WCO 后才动）。

### 5.1 A 组 —— RC-1 构建身份可辨识（必做）

| 方案 | 做法 | 侵入性 | 风险 | 工作量 | 是否根治 |
|---|---|---|---|---|---|
| **F1（推荐）提交 WCO + dirty 感知溯源戳** | (a) 由 Subtask #3 把 6 个 WCO 文件**提交**（HEAD 推进到含 WCO 的提交）；(b) `pack.ps1` 溯源戳改为：当 `git status --porcelain` 非空时追加 `-dirty`（或改用「短 hash + 关键文件内容指纹」）。使「关于」对构建身份给出**WCO 与非 WCO 不同**的戳。 | 低 | 无 | 低 | **是**——构建身份从此可辨识，人工门可信，迭代环可收敛 |
| F2 仅加 dirty 标记（不提交） | 仅改 `pack.ps1` 追加 `-dirty` | 低 | 无 | 极低 | 部分（能区分「干净构建 vs dirty 构建」，但多个 dirty 构建之间仍不可区分；不如 F1） |
| F3 维持现状 | 不动溯源戳 | — | — | — | 否（迭代环继续卡死） |

### 5.2 B 组 —— RC-2 WCO 残留命中缺陷（条件触发）

| 方案 | 做法 | 侵入性 | 风险 | 工作量 | 是否根治 |
|---|---|---|---|---|---|
| **G1（推荐先做）运行期断言 WCO 是否生效** | 主进程/「关于」记录并展示 `navigator.windowControlsOverlay.visible` 与 overlay 矩形（或 `win` 侧能力探测），把「WCO 到底有没有接管 caption」从不可观测变为**可读**。 | 低 | 无 | 低 | 诊断级（让 RC-2 可判定，避免再凭猜测改 UI） |
| G2 右簇宽度永不越过安全区 | 给右簇设最大宽 / 在 env 回退时仍保证不滑入系统三键区 | 低-中 | 低 | 中 | 硬化 RC-2 的 env 失效子情形 |
| G3 WCO 未生效时回退自绘三键 | `WindowControls` 改为：仅当 `windowControlsOverlay.visible===false` 时在 win 自绘，否则 `null` | 中 | 中（与系统三键重影风险） | 中 | belt-and-suspenders；仅在 G1 证明 WCO 不生效时才有意义 |
| G4 `resizable:false` | 去顶边 resize 非客户带 | 低 | **高**（连带破坏 drag/resize，Electron #30788/#48576） | 低 | 否（历轮已否决） |

### 5.3 症状 B

| 方案 | 做法 | 是否根治 |
|---|---|---|
| **B0（推荐）不单独修，随 A 解决** | B 是 A 下游；右簇可点 + 就绪默认账户 + `c3eaa65` 三态逻辑即可切 live。 | 是（前提 A 修好） |

---

## 六、推荐方案（Recommended fix）

> **先做 A 组 F1（提交 WCO + dirty 感知溯源戳）+ G1（运行期 WCO-active 断言），让下一次真机人工门可信、可判别；当且仅当人工门确认「系统三键已出现（WCO 生效）却仍不可点」时，再按 G2→G3 处置 RC-2。症状 B 采 B0 随 A 解决。**

**理由（凝练）**：

1. **本轮的阻断根因是「不可判别」，不是「又一处 UI 没调对」。** 实证已确立：WCO 代码正确、已进新构建、无旧副本（§3.2）。在这种状态下，唯一让迭代环卡死的是溯源戳失明（§3.3）——含 WCO 与不含 WCO 的构建都显示 `efa83f6`，没人能证明用户测的是哪一版。**先恢复可判别性（F1+G1），是把问题从「猜」变成「测」的唯一杠杆**，也正面修复一个真实缺陷（溯源戳应反映 dirty 树）。

2. **拒绝在一个已被验证为「正确」的实现上再叠加投机式改动。** 历轮反复复发的根因，正是「未经真机确认就再来一轮 CSS/几何微调」。WCO 是上一轮裁定的根级方案且已正确落地；在 G1 证明「WCO 生效却仍失效」之前，任何 G2/G3/G4 都是无证据的猜测，应被冻结。

3. **G1 让 RC-2 可判定。** WCO 命中不可 headless 观测（§3.4），但「overlay 是否 visible」可经 `navigator.windowControlsOverlay` 在渲染层读到并回传——这把「WCO 到底有没有接管」从盲区变为可读事实，是定位 RC-2 的前置。

4. **B 无需独立修复。** 实时后端就绪默认账户 + `c3eaa65` 四态逻辑 + 单测三重佐证「收到点击即切」，B 纯为 A 的下游。

**强制人工验收门（交付 Subtask #2/#3 后由人在真实 Windows 执行；历轮反复复发即因此门被跳过）**：

1. 用**确定新鲜**的构建（`./desktop/pack.ps1 -Target smoke` 重产 `win-unpacked`）**重启** `Aragon Trading.exe`，并在「关于」确认构建戳**已不再是裸 `efa83f6`**（F1 后应显示含 WCO 的提交 hash 或 `-dirty`），据此**坐实跑的是 WCO 构建**。
2. 确认顶栏右上角三键为**系统绘制**（G1 应另有可读的 `WCO active` 标识）。
3. **还原态**下右侧「模拟盘/实盘」「主题」「Factor」「设置齿轮」「最小化/最大化/关闭」**单击均下发生效**；品牌簇仍可拖窗、双击仍可最大化；四边/四角 resize、最大化态、mac、纯 Web `pnpm dev` **均不回归**。
4. 症状 B：右簇可点后点「实盘」**稳定切 live**（分段高亮 + `ChartWorkspace` 渲染 `LiveBottomPanel`），点「模拟盘」稳定切回。
5. 若第 3 步在「已确认 WCO 生效」前提下仍失效 → 反馈「WCO active 但右簇不可点」，触发 RC-2 的 G2/G3。

**范围守卫**：A 组主要改 `desktop/pack.ps1`（溯源戳）+ 提交既有 WCO 改动；G1 改 `desktop/src/main.js` / `apps/web`（最小只读探测）。**不得带入** `desktop/dist/`、`.next/`、`tmps/` 等构建/临时产物；**不得触碰** `setAccountMode` 契约与 `liveAuto` 守门；mac/linux 窗口路径不动。

---

## 附录：关键证据索引

- **WCO 在源码（A）**：`desktop/src/main.js:229-260`（win 分支 `titleBarStyle:'hidden'+titleBarOverlay`、无 `frame:false`）、`:208-219`（`TITLEBAR_OVERLAY` 配色+高度）、`:523-526`（`set-overlay-theme` IPC）；`desktop/src/preload.js:24`（`setOverlayTheme`）；`apps/web/components/Desktop/WindowControls.tsx:27`（win32 返回 null）；`apps/web/app/page.tsx:489-495`（`winOverlay`）、`:527`（`win-titlebar-safe`）、`:480-484`（主题同步）；`apps/web/app/globals.css:149-155`（安全区 CSS）；`apps/web/lib/desktop.ts:17`（类型）。
- **WCO 在最新构建、非陈旧（A）**：源码 mtime（main.js 01:38…）< 构建 mtime（app.asar 01:55:56、web page.js 01:55:10、NSIS 01:57:34）；`app.asar` grep 命中 `titleBarStyle/titleBarOverlay/set-overlay-theme/setOverlayTheme`；`desktop/dist/win-unpacked/resources/web/.next/static/css/e28321b872bcfae2.css` 实测 `win-titlebar-safe{padding-right:calc(100vw - env(titlebar-area-width, 100vw) - env(titlebar-area-x, 0px))}` 与 `-webkit-app-region:drag/no-drag` 完好。
- **无旧副本/旧进程（A）**：`%LOCALAPPDATA%\Programs` 无 `Aragon Trading`；进程表仅 `AragonMesh.exe`（编排器，5 个），无 `Aragon Trading.exe`。
- **RC-1 溯源戳失明（核心）**：`git status --porcelain` 中 6 个 WCO 文件 dirty；`git rev-parse --short HEAD`=`efa83f6`；`pack.ps1:313-318`（取 HEAD、无 dirty 标记，经 `--config.extraMetadata.aragonCommit` 注入）；`app.asar` 内 `package.json` 实测 `"aragonCommit":"efa83f6"`；`main.js:285-294`（`buildCommit()`）、`:314-315`（「关于」展示 `构建：efa83f6`）。
- **RC-2 几何触发源仍在（条件）**：`grep resizable|thickFrame desktop/src/main.js` → 0 命中（`opts` 未设 `resizable` → 默认 true/`WS_THICKFRAME`）；`main.js:232-237`（1440×900 还原态）。
- **症状 B 是 A 的下游（B）**：`apps/web/components/Header/AccountModeSwitch.tsx:37-41`（`hasReadyDefault`/`needsSetup`）、`:62-89`（`selectLive` 四态，状态④无条件 `setAccountMode("live")`）、`:46-49`（水合守门）；`AccountModeSwitch.test.tsx`（单测护栏）。
- **历轮母诊断与升级承诺**：`docs/diagnoses/desktop-header-right-and-live-toggle-unclickable/analysis.md`（裁定 A3/WCO，并把验收下沉为真机人工门）、`docs/diagnoses/desktop-titlebar-buttons-unclickable-when-restored/`（几何缺陷实测：还原 inset 0 / 最大化 7px；NC 命中不可 headless 验证）。

---

## 评审结论（Review Verdict）

> **评审人**：Subtask #1（分析报告评审与修复）· 2026-06-29
> **评审方式**：不采信转述，逐条重读源码与 git 状态独立复核证据是否在失败路径上真实成立；并审查候选方案的最小性、边界与回归面。

### 一、根因证据复核（逐项独立验证，全部成立）

下表每一行均由评审人本次重新打开文件核对行号，**与报告主张一致**：

| 报告主张 | 评审复核（path:line） | 裁决 |
|---|---|---|
| win 走 WCO、无 `frame:false` | `desktop/src/main.js:230` `isWin` 分流；`:241-246` win 分支仅 `titleBarStyle:'hidden'+titleBarOverlay:overlayForTheme('dark')`，确无 `frame:false` | ✅ 成立 |
| 窗口默认 `resizable:true`（几何触发源仍在） | `desktop/src/main.js:231-260` `opts` 全字段未含 `resizable`/`thickFrame` → Electron 默认 `true` | ✅ 成立 |
| 溯源戳取 HEAD、无 dirty 标记 | `desktop/pack.ps1:315` `git rev-parse --short HEAD`；`:319-320` 仅当非空即 `--config.extraMetadata.aragonCommit=$aragonCommit`，**全程无 `git status` 判 dirty** | ✅ 成立 |
| 「关于」读 `pkg.aragonCommit` 展示 | `desktop/src/main.js:285-294` `buildCommit()` 读 `require('../package.json').aragonCommit` | ✅ 成立 |
| HEAD=efa83f6 且 WCO 未提交 | `git rev-parse --short HEAD`→`efa83f6`；`git log -1`=「fix: live auto-trade uses checkbox-selected strategy」；`git status --porcelain` 中 `main.js/preload.js/page.tsx/globals.css/WindowControls.tsx/desktop.ts` 6 文件 dirty | ✅ 成立 |
| win 端不自绘三键 | `apps/web/components/Desktop/WindowControls.tsx:27` `if (!isDesktop \|\| platform==="darwin" \|\| platform==="win32") return null;` | ✅ 成立 |
| 右簇让位安全区 | `apps/web/app/page.tsx:490-495`（`winOverlay` 由挂载后 effect 置位）、`:527`（`winOverlay ? "win-titlebar-safe pl-4"`） | ✅ 成立 |
| 安全区 CSS + 非 WCO 回退归零 | `apps/web/app/globals.css:153-155` `padding-right:calc(100vw - env(titlebar-area-width,100vw) - env(titlebar-area-x,0px))`（env 缺失 → `100vw-100vw-0=0`） | ✅ 成立 |
| 主题同步系统三键配色 | `apps/web/app/page.tsx:480-484` `getWindowControls()?.setOverlayTheme?.(theme)`；`desktop/src/preload.js:24` `setOverlayTheme`→`aragon:win:set-overlay-theme` | ✅ 成立 |
| 症状 B 是 A 下游 | `apps/web/components/Header/AccountModeSwitch.tsx:74-78` 状态④ `hasReadyDefault → setAccountMode("live")`（无条件）；`:46-49` 水合守门同 `isSuccess` 前置 | ✅ 成立 |

**结论**：报告的证据链在「源码侧确已落地 WCO」「构建已纳入 WCO」「溯源戳对 WCO/非 WCO 构建给出相同 `efa83f6`」三点上**扎实可信**，无夸大、无错引。`app.asar` 内 `aragonCommit=efa83f6` 一项虽为二进制不便重抽，但因 `戳 = HEAD = efa83f6` 是逻辑必然（打包脚本即取 HEAD），**无需再抽即成立**。

### 二、补充 / 收紧证据（本评审新增，使评审可据此收口）

**补 1 —— 渲染层拖拽契约已完全收敛，RC-2 残面比报告所述更窄。** 报告 §3.4 仅以「`resizable:true` → 顶边 resize 带」论证 RC-2，但**遗漏**了历轮另一条几何机制（`desktop-header-right-and-auto-trade-toggle-unresponsive`：中部 `flex-1` 拖拽面 OS 命中区在还原态外溢到窗口右缘、吞掉整片右簇）**当前已在源码中根除**：`apps/web/app/page.tsx:537-549` 仅最左品牌簇标 `.drag-region`（唯一拖拽手柄），`:552` 中部 `<div className="flex-1 self-stretch" />` **已无 `.drag-region`**，`:553` 右簇 `no-drag`，注释 `:531-535` 明确记载「故去其 drag」。**含义**：能解释「**整片**右簇（48px 高按钮全高）单击无反应」的两条强机制里，「中部 drag 外溢」已死；仅剩「顶边 resize 非客户带」一条，而该带仅占顶边数像素（历轮实测还原 inset≈0），**在物理上难以吞掉 48px 按钮的全高点击**。故若 WCO 生效后用户仍报「整片不可点」，最可能并非 resize 带，而是 §补 2 的 env 失效或 WCO 根本未激活——这进一步坐实「先装 G1 探针、别再盲改几何」的判断。

**补 2 —— env 安全区失效的精确失败式。** `globals.css:154` 在 `env(titlebar-area-*)` 未被浏览器填充时回退到 `100vw/0px` → `padding-right=0` → 右簇延伸至窗口最右、滑入系统三键覆盖区。**但此机制只会吞掉右簇最右侧紧邻三键的 1–2 枚控件（设置齿轮/Factor），不会令账户开关等左侧控件失效**——可作为 G1/人工门下「部分控件不可点 vs 整片不可点」的判别尺。

**补 3 —— G1 的实现位点须更正（防止 Subtask #2 走错）。** 报告 §5.2/§六 表述为「主进程/『关于』记录并展示 `navigator.windowControlsOverlay.visible`」。但 `navigator.windowControlsOverlay` 是**渲染进程 Web API，主进程不可直接读**。正确实现：**渲染层**读 `navigator.windowControlsOverlay?.visible` 与 `getTitlebarAreaRect()`，经现有 IPC 通道（`preload.js` 增一只读 `sendSync`/`send`）回传主进程，再在 `showAbout()`（`main.js:298+`）追加一行（如 `WCO active: true/false · area WxH`）。**且必须 feature-detect**（浏览器 / mac / linux / 旧 Chromium 下 `navigator.windowControlsOverlay` 为 `undefined`），否则裸读 `.visible` 会抛错——这是 G1 唯一的回归点，须用 `?.` 或显式守卫。

**补 4 —— git 工作树含 WCO 之外的无关 dirty 文件，直接影响 Subtask #3 暂存范围。** `git status --porcelain` 实测除 6 个 WCO 文件外，还有 `apps/web/lib/chartSync.ts`、`chartSync.test.ts`、`desktop/README.md`、`desktop/pack.ps1`、`desktop/package.json`、`desktop/src/lib/process-manager.js` 同处 dirty（属其它历轮未提交工作），以及 `desktop/dist/`、`.next/`、`tmps/`、`.agentmesh/` 等**绝不可入库**的产物/运行时目录。报告 §5「范围守卫」未点名这些**必须排除项**，特此补全（见 §六）。

### 三、根因裁决（含一处必须的正名）

- **RC-1 成立，但须正名其性质**：RC-1（溯源戳失明）是「**迭代死锁**」的根因，**不是「右簇不可点」这一用户症状本身的根因**。报告自身已点到（「既是独立缺陷，也是本轮无法闭合的直接原因」），但措辞易被下游读成「修了 RC-1 就修了 bug」。**评审在此钉死**：RC-1 是「我们无法判定症状是否已被 WCO 修好」的根因；它本身不会让按钮失效。本轮交付的是**可判别性 + 可观测性**，症状是否已愈取决于其后的人工门结果。
- **RC-2 成立但仍为「待证条件支线」**，且经 §补 1 收窄：唯有人工门确认「WCO 已激活（系统三键 + G1 探针为真）却仍不可点」时方升格为待修缺陷；在此之前任何几何/CSS 改动都是无证投机，应冻结。

### 四、候选方案裁决（最小性 / 边界 / 回归）

- **F1（提交 WCO + dirty 感知溯源戳）—— 采纳，且是恢复可判别性的唯一杠杆。**
  - *最小性*：严格说，恢复**本轮**可判别性的最小动作是 F1(a)「提交 WCO」一项（提交后 HEAD 自动推进，戳即与旧 `efa83f6` 不同，无需动 `pack.ps1`）。F1(b)「dirty 感知戳」并非本轮判别所必需，但因本 bug 的本质是**反复复发**、且工作树长期带无关 dirty，F1(b) 是**防止同类失明再次发生**的合理硬化，且它本身正是一个真实缺陷修复（戳应反映 dirty 树）。故 F1(a)+F1(b) 均不算「多做」。
  - *回归*：F1(b) 仅向一段显示字符串追加 `-dirty`（`pack.ps1` 已有 `try/catch` 兜 git 不可用），**不可能影响构建产物或运行期**，回归面≈0。提交既有 WCO 文件不改变任何运行时行为（这些改动早已在工作树并已烤进构建），回归面=0。
- **G1（运行期 WCO-active 断言）—— 采纳，是让 RC-2 可判定的前置必需，非额外工作。** 实现约束见 §补 3（渲染层读 + IPC 回传 + feature-detect）。回归面仅在「未 feature-detect 致非 WCO 平台抛错」一处，已给出守卫。
- **为何本轮不并入 G2/G3（实验设计完整性，评审主动裁定）**：G2（右簇限宽/防滑入）与 G3（WCO 未生效回退自绘）虽低风险，但**若与 F1+G1 同轮落地，会污染人工门的判别力**——届时「测通过」将无法区分到底是「用户之前跑了旧包（RC-1）」还是「G2 恰好补上了 env 失效」。保持本轮**只改可判别性与可观测性、不动症状面**，才能让下一次人工门产出**干净的三路信号**：①新戳+WCO active+可点 → 真因是「跑了旧包」，结案；②WCO active+仍不可点 → RC-2 坐实，再据 §补 2 定向上 G2/G3；③WCO 未 active → Electron/Windows 的 WCO 支持缺口，另走能力回退。故 G2/G3 **冻结至人工门给出信号后**，与报告 §六 的分级处置一致，评审予以背书并补充其**实验设计理由**。
- **症状 B（B0 随 A 解决）—— 采纳**：`AccountModeSwitch.tsx:74-78` 状态④无条件切 live + `:46-49` 水合守门同口径，逻辑层「收到点击即切」已由源码与单测双重佐证，B 纯为 A 的下游。

### 五、最终推荐修复（单一方案）

**F1 + G1，分两节点落地，不并入 G2/G3：**

1. **Subtask #2（代码）**：
   - 改 `desktop/pack.ps1`：在取得 `$rev` 后增 `git -C $RepoRoot status --porcelain`，非空则戳追加 `-dirty`（git 不可用沿用既有静默跳过）。
   - 落 **G1**：渲染层（`apps/web`）读 `navigator.windowControlsOverlay?.visible` + 区域矩形，经 `preload.js`/IPC 回传，`main.js:showAbout()` 追加一行 `WCO active`；**必须 feature-detect**。
2. **Subtask #3（提交）**：`git add` **精确路径**，仅含本修复相关文件——6 个 WCO 文件 + `desktop/pack.ps1` + G1 触及文件；以 `fix:` 前缀提交，使 HEAD 推进到含 WCO 的提交。

**理由凝练**：本轮阻断根因是「**不可判别**」而非「又一处 UI 没调对」；在 WCO 代码已证正确、已进新构建、无旧副本（§3.2 经评审复核成立）的前提下，唯一让迭代环卡死的是溯源戳失明。**先恢复可判别性（F1）+ 可观测性（G1）**，把问题从「猜」变「测」，是收敛迭代环的唯一正解，且正面修复一个真实缺陷（戳应反映 dirty 树），并为 RC-2 的后续定向处置提供干净信号。

### 六、交付须知（防止下游误判，**务必随方案传递**）

1. **本轮不得声称「症状已修复」。** 交付物是「可判别 + 可观测的构建 + 一道明确人工门」。症状是否已愈，**以人工门结果为准**；下游节点不得在提交 WCO 后即标注「bug fixed」。
2. **重打包必须发生在 Subtask #3 提交之后**：唯有提交使 HEAD 推进，重打包的 `aragonCommit` 才会脱离裸 `efa83f6`，人工门「构建戳已变」的判据才成立。
3. **Subtask #3 暂存范围（硬约束）**：仅 `git add` 6 WCO 文件 + `desktop/pack.ps1` + G1 文件；**必须排除**无关 dirty（`apps/web/lib/chartSync.ts`、`chartSync.test.ts`、`desktop/README.md`、`desktop/package.json`、`desktop/src/lib/process-manager.js`）与一切产物/运行时目录（`desktop/dist/`、`.next/`、`tmps/`、`.agentmesh/`、`.claude-index/`、`docs/diagnoses/*` 等本不属本修复者）。
4. **契约红线不动**：`setAccountMode` 契约、`liveAuto` 守门、mac/linux 窗口路径、`AccountModeSwitch` 三态逻辑（`c3eaa65`）一律不得触碰。
5. **人工门（真实 Windows 还原态执行）**：①「关于」构建戳已非裸 `efa83f6`（坐实跑的是 WCO 构建）；②G1 显示 `WCO active: true` 且三键为系统绘制；③还原态右簇全部控件单击下发、品牌簇仍可拖窗/双击最大化、resize/最大化/mac/纯 Web 均不回归；④点「实盘」稳定切 live；⑤若 ①②为真而 ③仍失效 → 反馈「WCO active 但右簇不可点」，依 §补 2 触发 G2/G3。

**一句话裁决**：**采纳 F1 + G1（分两节点），冻结 G2/G3 至人工门给出信号；本轮交付可判别性与可观测性，而非症状修复声明。**
