# 实时模式下「顶栏右侧不可点（模拟盘/实盘）」+「自动交易开关点击无反应」— 问题定位与分析

- **Bug slug**: `desktop-header-right-and-auto-trade-toggle-unresponsive`
- **诊断节点**: Subtask #0（问题定位与分析）
- **日期**: 2026-06-28
- **本轮来源**: 多轮迭代第 2 轮（在第 1 轮「实时自动交易」特性 `f0d44a7` / `a0b7b87` 之上继续修改）
- **结论先行（两条独立根因）**:
  1. **顶栏右侧不可点** = `desktop-titlebar-buttons-unclickable-when-restored` 同一**无边框（`frame:false`）+ 默认可缩放（`resizable` 未设→`true`）窗口在「还原态」下拖拽区命中错位**缺陷的**复发**。`b6abdb1`「反转拖拽契约」修复**已在源码与打包产物中落地**（实测打包 HTML 顶栏已是反转结构），但**未根治**：反转后顶栏仅剩**一条紧贴顶边、横贯中部的 `drag-region flex-1` 伸缩拖拽面**，它**紧邻右簇控件**；还原态下该拖拽面的 OS 命中矩形向右溢出盖住右簇 → **整个右侧（模拟盘/实盘、主题、Factor、设置、窗口控制键）落入 caption 拖拽面被吞点击**。症状由第 1 轮前的「全部按钮失效」收敛为「**仅右侧失效**」，恰好排他地指向这条**唯一残留、且唯一与右簇相邻**的中部拖拽面。
  2. **自动交易开关点击无反应** = 开关被**守门函数静默置为禁用态**，而打包桌面端**永远无法满足守门条件**。`AutoTradeToggle` 在 `resolveAutoTradeGate` 任一条件不满足时把开关 `blocked`（`opacity-40 cursor-not-allowed` + `onChange` 空操作）；其中 `liveAutoEnabled` 取自后端 `/live-auto/status` 的 `live_auto_trading_enabled`，**默认 `False`**（`config.py:59`），而桌面打包链**从不注入 `LIVE_AUTO_TRADING_ENABLED`**（`agent.env` 模板与 `build-agent-env.mjs` 只产出 `AGENT_*`），故状态恒为「未启用」→ 开关恒灰、点击无反应。叠加根因 1 使用户**连「实盘」都切不过去**（守门第 1 条 `accountMode!=="live"` 同样不满足），双重封死。

---

## 一、问题描述（Problem description）

第 2 轮用户报障（原文）：

> 有问题，继续修改：
> 1 软件最上方，整个 header 的右侧，无法点击，右侧的 模拟盘/实盘 按钮无法点击
> 2 点击 自动交易 按钮，没反应，无法正确的开启自动交易

拆为两个独立症状：

- **症状 A（顶栏右侧不可点）**：桌面端顶栏**右侧整片**控件——账户模式切换（`模拟盘 / 实盘`）、主题切换、Factor、设置齿轮、最小化/最大化/关闭——**单击无反应**；左侧品牌区/菜单不受影响（区别于第 1 轮前的「全部按钮失效」，本轮**仅右侧**）。
- **症状 B（自动交易开关无反应）**：实时（paper）模式下，策略面板标题栏右上的「自动交易」`Switch` **点击无任何反应**，无法弹出确认弹窗、无法启动自动交易。

二者在屏幕上都落在「右侧」，但**根因不同**（A 是桌面 OS 拖拽命中、B 是前后端守门），需分别修复。

---

## 二、复现步骤（Reproduction steps）

### 2.1 症状 A：顶栏右侧不可点（端到端）

1. 取当前分支（含 `a0b7b87`，顶栏为 `b6abdb1` 反转契约）。
2. 启动桌面端任一形态：
   - 开发态：`desktop/` 下 `npm run dev`。
   - 生产态：运行打包产物 `desktop/dist/win-unpacked/Aragon Trading.exe`。
3. **确保窗口处于还原态**（非最大化；默认启动即 1440×900 还原态，`desktop/src/main.js:214-215`）。
4. 单击顶栏**右侧**的「模拟盘/实盘」分段开关、主题、设置齿轮、窗口控制键 → **无反应**（在中部空白处按下拖动则整窗跟随移动）。
5. 点「最大化」铺满屏幕，再次单击同样的右侧控件 → **此时生效**；再「还原」→ 右侧**再次失效**。可在「还原↔最大化」间稳定复现。

> 关键观察：失效**只与是否最大化相关**、且**只发生在右侧**（左侧品牌/菜单两态都可点）。这与「中部那条紧贴顶边、横到右簇左缘的 `drag` 伸缩面在还原态命中右移」一一对应（见 §3.1）。

### 2.2 症状 B：自动交易开关无反应（端到端）

1. 桌面端启动（生产态，即打包 `Aragon Trading.exe`）。
2. 切到「实时」模式（顶部模式切换）；策略面板标题栏右上出现「自动交易」开关。
3. 单击该开关 → **无任何反应**：无确认弹窗、`accountMode` 不变、无网络请求。
4. 鼠标悬停该开关，`title` 提示显示禁用原因之一：「请先切换到实盘账户模式」/「实时自动交易未启用（需后端开启 LIVE_AUTO_TRADING_ENABLED）」。

### 2.3 守门取证（无需 OKX 实盘即可定位 B）

后端是否放行自动交易，直接看 `/api/live-auto/status` 回包的 `liveAutoTradingEnabled`：

```bash
# 桌面端后端固定 18760（desktop/src/lib/config.js:8）
curl -s http://127.0.0.1:18760/api/live-auto/status
# 打包态预期： {"liveAutoTradingEnabled": false, ...}  ← 守门第 3 条恒不满足
```

`false` 即复现 B 的后端侧根因（开关被前端守门置灰）。

---

## 三、根因分析（Root cause analysis）

### 3.1 症状 A：还原态拖拽命中缺陷复发，残留的「中部 `flex-1` 拖拽面」溢出盖住右簇

**(1) 触发条件未变：无边框 + 默认可缩放。**
`desktop/src/main.js:223` `frame: false`（win/linux）；`main.js:213-237` 的 `opts` **未设 `resizable`**（取默认 `true` → Windows `WS_THICKFRAME`，顶边保留 resize/caption 非客户命中带）。这与既往诊断 `desktop-titlebar-buttons-unclickable-when-restored` 的触发条件**完全一致**、且本轮**未改动**。

**(2) `b6abdb1` 反转契约已落地，但顶栏残留一条与右簇相邻的拖拽面。**
当前顶栏（`apps/web/app/page.tsx:517-555`）结构：

```
<header class="flex h-12 shrink-0 items-center ... px-4">   // 顶栏整体不再 drag（line 517-524）
  <div class="flex items-center gap-2">                     // 左簇
    <div class="drag-region ... self-stretch">logo/标题</div> // 左侧品牌：drag（line 530）
    <AppMenu/>
  </div>
  <div class="drag-region flex-1 self-stretch" />           // ★中部伸缩拖拽面：drag（line 545）
  <div class="no-drag flex items-center gap-2">             // 右簇：no-drag（line 546）
    <AccountModeSwitch/> 主题 <FactorButton/> <SettingsGearButton/> <WindowControls/>
  </div>
</header>
```

打包产物实测同构（`desktop/dist/win-unpacked/resources/web/.next/server/app/index.html`）：
`<header class="flex h-12 shrink-0 items-center border-b ... px-4">` … `<div class="drag-region flex-1 self-stretch"></div><div class="no-drag flex items-c…">` —— **证明打包态已含反转修复，并非「构建陈旧」**。

拖拽契约样式：`apps/web/app/globals.css:137-146`（`.drag-region{-webkit-app-region:drag}`、`.no-drag{…no-drag}`）。打包 CSS 实测含 `.drag-region{-webkit-app-region:drag}` 与 `.no-drag{-webkit-app-region:no-drag}` 两条声明，**契约生效**。

**(3) 机理：还原态命中错位让中部 `drag` 面向右溢出盖住右簇。**
既往诊断的 Electron 31 探针已实测：无边框可缩放窗口**还原态帧内缩进 0px、最大化态 7px**，两态非对称导致 Chromium 推送给 OS 的可拖区矩形（按内容坐标计算 = `drag` 并集 ∖ `no-drag` 并集）在还原态与真实控件位置**错位**。反转契约把控件移出 drag、本意是让右簇落 `no-drag`(HTCLIENT)；但**顶栏仍保留一条紧贴 y=0、`flex-1` 横贯到右簇左缘的 `drag` 面**（line 545）。还原态错位下，这条**唯一与右簇相邻**的拖拽面其 OS 命中矩形向右越界、覆盖到右簇之上——右簇虽是 `no-drag`，却被相邻 `drag` 面的错位命中矩形「吃掉」，`mousedown` 被 OS 用作「拖窗」，`click` 不下发 → **整片右侧不可点**。

**症状收敛即排他证据**：第 1 轮前顶栏整体 `drag`（`b6abdb1` 之前 `<header className="drag-region …">`）→ **全部**按钮失效；反转后顶栏只剩**中部这一条** `drag` 面、且只在**右簇**左缘——症状随之收敛为「**仅右侧**失效」。左簇品牌虽也是 `drag`，但它在最左、其右紧邻的是 `AppMenu`（默认区）而非整片右簇，错位溢出不致命。**唯一能解释「恰好右侧整片失效」的残留拖拽面就是 line 545 的 `drag-region flex-1`**。

**(4) 排除项（已证伪）**：
- **非 Web 覆盖层**：确认弹窗 `Modal` 在关闭时 `return null`（`apps/web/components/ui/index.tsx:313`），不存在常驻遮罩；第 1 轮新增的 `AutoTradeStatusBar`/`AutoTradeEventLog` 均为 `border-b` 常规流元素、渲染在右面板内、不进主顶栏，**不构成覆盖**。
- **非构建陈旧**：打包 HTML/CSS 实测已含反转契约（见 (2)），故 A 不是「用户跑了旧包」，而是**反转修复对真实 Windows 无效**——`b6abdb1` 自身诊断已声明「OS 非客户区命中发生在浏览器进程、渲染层不可观测，无法 headless 验证点击是否下发」，本轮报障即该未验证假设的**实证证伪**。

### 3.2 症状 B：自动交易开关被守门静默禁用，且打包端永远满足不了守门

**(1) 开关禁用即「点击空操作」（前端形态）。**
`apps/web/components/Strategy/AutoTradeToggle.tsx`：

- `:55-61` `active = !!liveAutoSessionId`；`disabledReason = resolveAutoTradeGate({accountMode, hasReadyDefault, liveAutoEnabled, strategyId})`。
- `:106` `const blocked = !active && !!disabledReason;`
- `:117-126` `blocked` 时外层 `div` 置 `cursor-not-allowed opacity-40`，且 `Switch.onChange={(next)=>(blocked?undefined:onToggle(next))}` —— **`blocked` 时点击空操作**。
- `Switch` 原语本身无缺陷（`ui/index.tsx:149-187`，`onClick={()=>onChange(!checked)}`，可点击只是 handler 被门控空操作）。

故「点击无反应」= 开关处于 `blocked`，即 `resolveAutoTradeGate` 返回了非空禁用原因。

**(2) 守门四条（`apps/web/lib/liveAuto.ts:22-28`）：**

```ts
if (accountMode !== "live")  return "请先切换到实盘账户模式";          // 第1条
if (!hasReadyDefault)        return "请先在设置中配置默认实盘账户";      // 第2条
if (!liveAutoEnabled)        return "实时自动交易未启用（需后端开启 LIVE_AUTO_TRADING_ENABLED）"; // 第3条
if (strategyId == null)      return "请先选择一个交易策略";             // 第4条
return null;
```

**(3) 第 3 条在打包桌面端恒不满足（核心后端根因）。**
- `liveAutoEnabled` 取自 `/live-auto/status` 的 `liveAutoTradingEnabled`（`AutoTradeToggle.tsx:43-49`），后端实现 `services/quant/app/api/live_auto.py:95` 直接回 `settings.live_auto_trading_enabled`。
- 该配置**默认 `False`**：`services/quant/app/config.py:59` `live_auto_trading_enabled: bool = False`（与 `:47` `live_trading_enabled: bool = True` 对比——手动实盘下单默认开，**自动交易专用闸默认关**）。
- 桌面打包链**从不注入** `LIVE_AUTO_TRADING_ENABLED`：
  - `desktop/scripts/build-agent-env.mjs:19-39` 的 `AGENT_KEYS` **只挑 `AGENT_*`**，产出的 `agent.env` 不含任何 `LIVE_*`；
  - 模板 `desktop/resources/agent/agent.env` 与打包产物 `desktop/dist/win-unpacked/resources/agent/agent.env` 实测**只有 `AGENT_*` 5 项**；
  - `desktop/src/lib/config.js:59-65` 的 `backendEnv` 虽愿意透传 `key.startsWith('AGENT_')||key.startsWith('LIVE_')`，但 `agentEnv` 里**根本没有 `LIVE_*` 可传**（且 `:44` 注释列出的「须透传 LIVE_*」清单为 `LIVE_TRADING_ENABLED/…NOTIONAL_USD/…TD_MODE/…LEVERAGE`，**未含 `LIVE_AUTO_TRADING_ENABLED`**）。
- 结论：打包后端 `live_auto_trading_enabled` 恒取默认 `False` → `/live-auto/status` 回 `liveAutoTradingEnabled:false` → 守门第 3 条恒返回「实时自动交易未启用」→ 开关恒灰、点击无反应。**且** `live_auto.py:33` 的写类双闸（`live_auto_trading_enabled and live_trading_enabled`）同样会 403，纵使绕过前端也启动不了。

**(4) 第 1 条被症状 A 连带封死（耦合放大）。**
开关在 `mode==="paper"`（实时模式）时由 `StrategyPanel.tsx:440` 渲染，但守门第 1 条要求 `accountMode==="live"`。切换实盘的唯一入口是顶栏右侧的「实盘」按钮（`AccountModeSwitch.tsx:43-51` `selectLive`）——**而它正落在症状 A 的不可点右簇里**。于是用户**连实盘都切不过去**，守门第 1 条先行不满足；即便切过去，第 3 条仍封死。**A 是 B 的前置障碍，B 另有独立后端根因**——两者都要修。

**(5) 次生 UX 缺陷（加剧「没反应」体感）**：`blocked` 态只有 `opacity-40` + `title` 悬浮提示，**无就地可见的禁用原因**。用户点了不动、又难注意到 tooltip，故主观为「没反应/坏了」。补一行就地原因文案可显著改善可诊断性（非必须，但建议）。

---

## 四、影响面（Impact surface）

| 维度 | 症状 A（顶栏右侧不可点） | 症状 B（自动交易开关无反应） |
|---|---|---|
| 平台 | Windows/Linux（`frame:false`+默认可缩放）；mac 走 `titleBarStyle:'hidden'`+系统红绿灯，基本不受影响 | 全平台桌面打包态（与平台无关，纯配置/守门） |
| 运行态 | **还原态复现（默认启动态）**；最大化态正常 | 打包态恒复现；开发态若未设 `LIVE_AUTO_TRADING_ENABLED` 亦复现 |
| 受影响功能 | 右簇全部：账户模式切换（模拟盘/实盘）、主题、Factor、设置齿轮、最小化/最大化/关闭 | 实时模式「自动交易」开关：无法弹确认弹窗、无法启动会话；连带使第 1 轮整条实时自动交易特性在打包端不可达 |
| 严重度 | **高**——「切实盘 / 进设置 / 关窗」唯一入口，还原态近乎不可用 | **高**——第 1 轮新增的核心特性在交付形态下 100% 不可用 |
| 引入/复发点 | 触发条件源自 `ff20361`（自绘标题栏）；`b6abdb1` 反转修复**未根治**，本轮复发 | 第 1 轮 `f0d44a7` 引入守门 + 默认关闸 `live_auto_trading_enabled=False`，但**打包链未补 `LIVE_AUTO_TRADING_ENABLED` 注入** |

---

## 五、候选修复方案对比（Candidate fixes）

> 本节仅做方案比较，**不写修复代码**（留给 Subtask #2）。A、B 两症状各自独立给方案。

### 5.1 症状 A（顶栏右侧不可点）

| 方案 | 做法 | 侵入性 | 风险 | 工作量 | 是否根治 |
|---|---|---|---|---|---|
| **A1（推荐）消除与右簇相邻的中部拖拽面** | 去掉 `page.tsx:545` `<div class="drag-region flex-1 self-stretch"/>` 的 `drag`（改为普通 `flex-1` 占位/或 `no-drag`），把可拖区收敛到**最左品牌簇**（`:530` 已有 `drag-region`，远离右簇）。窗口仍可从品牌簇拖动、双击最大化（`onTitleDoubleClick` 已是 `.drag-region` allowlist，`:495-498`）。 | 低（删一个类名/几行） | 低（拖拽手柄变小但仍可用；Web 端 `app-region` 无副作用） | 低 | 机理对位——移除唯一与右簇相邻的拖拽面，右簇不再被错位命中盖住 |
| **A2 顶边留 no-drag 安全缩进** | 顶栏上方加一条不可拖、不含控件的细缩进，把右簇压离 y=0 顶边 resize/命中错位带。 | 低（CSS） | 中（仅缓解、依赖 DPI/版本；改顶栏视觉高度） | 低 | 部分（缓解错位，非形态级） |
| **A3 Windows Controls Overlay** | win 上 `titleBarStyle:'hidden'`+`titleBarOverlay`，交还系统绘制/命中 caption 三键；应用控件置于 `env(titlebar-area-*)` 安全区。 | 中-高（删 win 自绘 `WindowControls`、布局让位、颜色同步） | 中（UX/视觉变化，与自绘三键设计取舍冲突） | 中 | 是（系统托管 caption，绕开 `app-region` 命中缺陷），但改动面大 |
| A4 `resizable:false`/`thickFrame:false` | 去顶边 resize 非客户带 | 低 | **高**——连带禁用 `drag` 拖拽（Electron #30788）/破坏 resize（#48576），失去缩放 | 低 | 否（回归更重，否决） |

### 5.2 症状 B（自动交易开关无反应）

| 方案 | 做法 | 侵入性 | 风险 | 工作量 | 是否根治 |
|---|---|---|---|---|---|
| **B1（推荐）打包链注入 `LIVE_AUTO_TRADING_ENABLED`** | 在 `agent.env` 模板 + `build-agent-env.mjs` 的可透传清单中加入 `LIVE_AUTO_TRADING_ENABLED`（及 §3.2(3) 注释提到却也漏的 `LIVE_TRADING_ENABLED` 等 `LIVE_*`），由 `config.js:backendEnv` 既有 `LIVE_*` 透传通道注入后端。`config.js` 透传逻辑**已就绪、无需改**。 | 低（构建脚本 + env 模板 + 注释清单） | 低（仅放行自动化总闸；写类仍受 notional/单会话/二次确认等既有 guard） | 低 | 是——后端 `live_auto_trading_enabled=True` → 守门第 3 条放行 |
| **B2 守门禁用态就地反馈（UX 加固）** | `AutoTradeToggle` 在 `blocked` 时把 `disabledReason` 就地渲染为一行可见文案（而非仅 `title`），让「为何点不动」对用户透明。 | 极低 | 无 | 极低 | 否（不放行，只提可诊断性，作 B1 的附带改善） |
| B3 默认开启 `live_auto_trading_enabled=True` | 改 `config.py:59` 默认值 | 低 | **高**——破坏「自动化专用闸默认关」的安全姿态（真实资金无人值守下单），与既定安全契约冲突 | 低 | 否（安全回退，否决） |

> A1 与 B1 互相独立、可并行落地；二者都修复后，用户方能：还原态点「实盘」→ 进入实盘 → 选策略 → 「自动交易」开关可用。

---

## 六、推荐方案（Recommended fix）

**症状 A：采 A1（消除中部 `flex-1` 拖拽面，可拖区收敛到品牌簇），并把「真实 Windows 还原态点击下发」纳入验收硬门槛；若 A1 实测仍不足，回退 A3（WCO）。**

理由：
1. **机理对位、最小改动**：症状从「全部失效」收敛为「仅右侧失效」，排他地指向反转后**唯一残留、且唯一与右簇相邻**的中部 `drag-region flex-1`（`page.tsx:545`）。删除其 `drag` 即移除盖住右簇的错位命中源，是覆盖该机理、改动最小、Web 零回归的方案。
2. **不重蹈未验证覆辙**：`b6abdb1` 因「NC 命中不可 headless 观测」而**未在真实 Windows 验证**即收尾，导致本轮复发。Subtask #2/#3 **必须在真实 Windows 还原态人工点击四类右簇控件确认下发**（不可只靠 headless 几何探针），把这条假设固化为验收项。
3. **WCO 作根因级兜底**：若 A1 在某些 DPI/版本仍有残留错位，A3（系统托管 caption）从架构上绕开 `app-region` 命中缺陷，但改动面大、动自绘三键，故列为**回退**而非首选。

**症状 B：采 B1（打包链注入 `LIVE_AUTO_TRADING_ENABLED`），并附带 B2（禁用态就地反馈）。**

理由：
1. **直击后端根因**：开关恒灰的唯一后端原因是打包端 `live_auto_trading_enabled` 取默认 `False` 且无注入路径；B1 经**既有** `config.js` `LIVE_*` 透传通道补齐，**不动透传逻辑**，最小且对位。
2. **保安全姿态**：不改 `config.py:59` 默认（B3 否决）——默认关闸仍在，仅在打包随附 env 中显式开启自动化总闸，写类仍受单会话/名义上限/二次确认/审计等既有 guard 约束，符合「实盘显式 gated」约定。
3. **B2 提升可诊断性**：把禁用原因就地呈现，避免用户把「守门拦截」误判为「按钮坏了」，对未来同类「点不动」报障也是低成本护栏。

**验收对齐（供 Subtask #1/#2/#3）**：
- A：**真实 Windows 还原态**下，顶栏右侧「模拟盘/实盘」、主题、Factor、设置齿轮、最小化/最大化/关闭**单击均下发生效**；品牌簇仍可拖窗、双击仍可最大化；四边/四角 resize、最大化态、纯 Web `pnpm dev` 行为均不回归。
- B：打包态 `curl /api/live-auto/status` 回 `liveAutoTradingEnabled:true`；实时模式下切到实盘、选定策略后「自动交易」开关**可点 → 弹确认弹窗 → 可启动会话**；任一守门未满足时**就地显示中文原因**。
- 范围守卫：A 仅 `apps/web/app/page.tsx`（必要时 `desktop/src/main.js` for A3）；B 仅 `desktop/resources/agent/agent.env`、`desktop/scripts/build-agent-env.mjs`、`apps/web/components/Strategy/AutoTradeToggle.tsx`（B2）；不得带入 `desktop/dist/`、`tmps/` 等构建/临时产物。

---

## 附录：关键证据索引

- 触发条件（A）：`desktop/src/main.js:223`（`frame:false`）、`:213-237`（`opts` 未设 `resizable` → 默认 `true`）、`:214-215`（初始 1440×900 还原态）。
- 顶栏反转契约（A）：`apps/web/app/page.tsx:517-524`（header 不再 drag）、`:530`（品牌簇 drag）、**`:545`（中部 `drag-region flex-1 self-stretch` —— 残留主因）**、`:546-554`（右簇 `no-drag` 含 `AccountModeSwitch`）；`:495-498`（双击最大化 `.drag-region` allowlist）。拖拽样式 `apps/web/app/globals.css:137-146`。
- 打包态非陈旧（A）：`desktop/dist/win-unpacked/resources/web/.next/server/app/index.html` 顶栏实测含 `<header class="flex h-12 …">`+`<div class="drag-region flex-1 self-stretch">`+`<div class="no-drag …">`；打包 CSS 含 `.drag-region{-webkit-app-region:drag}`/`.no-drag{…no-drag}`。
- 既往同源诊断（A）：`docs/diagnoses/desktop-titlebar-buttons-unclickable-when-restored/`（Electron 31 探针：还原 inset 0、最大化 7px；并自述 NC 命中不可 headless 验证）；修复提交 `b6abdb1`「invert the titlebar drag contract」。
- 排除覆盖层（A）：`apps/web/components/ui/index.tsx:313`（`Modal` 关闭 `return null`）；`AutoTradeStatusBar`/`AutoTradeEventLog` 由 `StrategyPanel.tsx:443-444` 渲染在右面板内。
- 守门与禁用态（B）：`apps/web/components/Strategy/AutoTradeToggle.tsx:43-49`（`liveAutoEnabled` 取 `/live-auto/status`）、`:55-61`、`:96-104`、`:106`、`:117-126`；`apps/web/lib/liveAuto.ts:22-28`（四条守门）；`apps/web/components/ui/index.tsx:149-187`（`Switch` 原语无缺陷）。
- 后端默认关闸（B）：`services/quant/app/config.py:59`（`live_auto_trading_enabled=False`）、`:47`（`live_trading_enabled=True`）；`services/quant/app/api/live_auto.py:33`（写类双闸 403）、`:95`（status 回 `liveAutoTradingEnabled`）；`services/quant/app/schemas.py:239`（`liveAutoTradingEnabled: bool`）。
- 打包链漏注入（B）：`desktop/scripts/build-agent-env.mjs:19-39`（`AGENT_KEYS` 仅 `AGENT_*`）；`desktop/resources/agent/agent.env` 与 `desktop/dist/win-unpacked/resources/agent/agent.env`（实测仅 `AGENT_*` 5 项）；`desktop/src/lib/config.js:44`（注释 `LIVE_*` 清单漏 `LIVE_AUTO_TRADING_ENABLED`）、`:59-65`（`backendEnv` 透传 `AGENT_*`/`LIVE_*` 通道已就绪）。
- 入口耦合（A→B）：`apps/web/components/Header/AccountModeSwitch.tsx:43-51`（`selectLive` 在不可点右簇内）；`apps/web/components/Strategy/StrategyPanel.tsx:440`（`mode==="paper"` 渲染 `AutoTradeToggle`）。

---

## 评审结论（Review Verdict）

- **评审节点**: Subtask #1（分析报告评审与修复）
- **评审人**: 资深评审（Anthropic 工程团队）
- **日期**: 2026-06-28
- **一句话结论**: 两条根因的**定位方向均成立、结构性证据经逐处打开复核全部属实**；但**症状 A 的「精确机理」表述（7px 命中矩形右溢）站不住**，评审据前序诊断 `desktop-titlebar-buttons-unclickable-when-restored` 已确立的机理①把它**纠正为「最右 `drag` 面尾边 `no-drag` 减除失效、命中区延伸至窗口右边缘吞掉整片右簇」**——这反而**更强地支持**推荐的 A1（删中部 `flex-1` 拖拽面），且解释了「为何整片右簇而非一条 7px 失效」。**症状 B 的修复 B1 通道经验证确实可用，但原写法漏了两处边界**（dev `.env` 取值脆弱 + `userData/agent.env` 升级不重播种），评审把 B 收敛为**更稳健且仍最小的单点注入（`config.js backendEnv` prod 默认 `LIVE_AUTO_TRADING_ENABLED=true`）**，并界定**只需注入这一个键**（其余 `LIVE_*` 默认值已可用，注入即多余）。

### 一、证据链逐项复核（评审已实际打开每个引用，结论：结构性证据全部属实）

**症状 A（全部通过）**
- 触发条件：`desktop/src/main.js:223` `frame:false`、`:213-237` `opts` 确未设 `resizable`（默认 `true`）、`:214-215` `1440×900` 还原态启动 ✓。**评审补核**：该文件仅 `:243` 一处 `new BrowserWindow(opts)`，无任何 `resizable:false` / `thickFrame` / 自定义 NC 命中改写——窗口非客户区行为完全由「无边框+默认可缩放」默认值决定，排除「别处改写窗口特性」干扰变量。
- 反转契约现状：`apps/web/app/page.tsx:517-524`（header 仅作 flex 容器、不再 drag）、`:530`（品牌簇 `drag-region self-stretch`）、**`:545`（中部 `drag-region flex-1 self-stretch` 空面——残留主因）**、`:546-554`（右簇 `no-drag` 含 `AccountModeSwitch`/主题/`FactorButton`/`SettingsGearButton`/`WindowControls`）、`:495-498`（双击最大化仍以 `.drag-region` allowlist 判定）✓。`globals.css:137-146` 契约生效，且 `:140-146` 有「`.drag-region` 内 `button/input/a/[role=radiogroup|switch]` 自动 `no-drag`」结构兜底 ✓。
- **非陈旧（评审实测打包产物）**：`desktop/dist/win-unpacked/resources/web/.next/server/app/index.html` 经 `grep` 确含 `drag-region flex-1 self-stretch` 与紧随其后的 `no-drag flex items-center`——**反转契约确已打进交付包、残留 `flex-1` 拖拽面也在包内**，A 不是「用户跑了旧包」。

**症状 B（全部通过）**
- 前端禁用形态：`AutoTradeToggle.tsx:43-49`（`statusQ` 仅 `accountMode==="live"` 时 enabled、`liveAutoEnabled = data?.liveAutoTradingEnabled ?? false`）、`:55-61`、`:96-104`（`onToggle` 内 `if(disabledReason) return`）、`:106`（`blocked`）、`:117-126`（`blocked`→`cursor-not-allowed opacity-40` + `onChange` 空操作）✓。**双重门控**（外层 `blocked` 包裹 + `onToggle` 内再判）使禁用态点击彻底空操作。
- 守门四条：`apps/web/lib/liveAuto.ts:22-27` ✓。
- 后端默认与守门：`config.py:59` `live_auto_trading_enabled=False`、`:47` `live_trading_enabled=True` ✓；`live_auto.py:33`（写类双闸 `live_auto_trading_enabled and live_trading_enabled` 否则 403）、`:95`（`/live-auto/status` 回 `liveAutoTradingEnabled`）✓。
- 打包链：`build-agent-env.mjs:19-39`（`AGENT_KEYS` 仅 `AGENT_*`，无任何 `LIVE_*`）✓；`config.js:61`（`backendEnv` 透传 `key.startsWith('AGENT_')||key.startsWith('LIVE_')`）✓、`:44-45`（注释「须透传的 `LIVE_*`」清单确**漏列** `LIVE_AUTO_TRADING_ENABLED`）✓。
- **评审新增决定性核验（通道确实可用）**：`desktop/src/lib/agent-env.js:47-58` `loadAgentEnv` **不做 key 过滤**（`parseEnvText` 收下全部 `K=V`），故「只要 `agent.env`/进程 env 里有 `LIVE_AUTO_TRADING_ENABLED`，它就能经 `backendEnv` 注入后端」——**透传链路成立，缺的只是「源」**。这把 B 的根因精确钉死为：**透传通道齐备、但全链路无任何一处「产出」该键**。

### 二、对症状 A「精确机理」的评审纠正（关键，决定 A1 是否对位）

原报告 §3.1(3) 将机理表述为「中部 `flex-1` 拖拽面的 OS 命中矩形**向右溢出约 7px**、覆盖右簇」。评审认为**该表述不成立且与既证事实冲突**：

1. **量级对不上**：右簇横跨约 250px（5 组控件）。7px 右溢至多吃掉最左 `AccountModeSwitch` 的左缘一条，**无法解释「主题/Factor/设置/窗口键」这些更靠右的控件也整体失效**。
2. **与前序实测相左**：前序诊断 `repro/result.txt` 实测**还原态帧内缩进 = 0px**（最大化才 7px），即**还原态根本不存在那 7px 偏移**；且前序《评审结论》§二**已显式否决**「小偏移盖住控件」的机理②、确立机理①（`drag` 区内 `no-drag` 子区**未被减除**→整片成 HTCAPTION）。原报告把被否决的机理②换皮成「7px 右溢」，方向回退。

**评审纠正后的机理（与机理①一致，且解释整片失效）**：
> 还原态无边框可缩放窗口下，Chromium 推给 OS 的可拖区在「**最右侧 `drag` 面与其右侧尾随 `no-drag` 簇的边界**」处**减除失效**——最右 `drag` 面（`page.tsx:545` 的 `flex-1`）的命中区**不在右簇左缘截断，而是延伸到窗口右边缘**，把整片尾随 `no-drag` 右簇吞为 HTCAPTION，`mousedown` 被用作拖窗、`click` 不下发 → **整片右侧失效**（非一条 7px）。

该机理还**自洽解释了原报告未处理的反例**：品牌簇（`:530` 同为 `drag`）紧邻其右的 `AppMenu` **却可点**——因为品牌簇**不是最右 `drag` 面**（其右还有 `flex-1`），不发生「向窗口右边缘外溢」；只有**最右那块 `drag` 面**才把尾随区一路吞到窗口右缘。**「最右 `drag` 面」而非「任意相邻 `drag`」才是肇因**——这正指向删除 `flex-1` 的 `drag`。

> **如实标注的推断缺口**：OS 非客户区命中发生在浏览器进程、渲染层不可观测，`sendInputEvent` 会绕过 NC 命中测试，**无法 headless 录到「click 未下发」**（前序诊断已自陈此局限，亦是其「未真机验证即收尾」导致本轮复发的根由）。故上述机理为**最佳推断**，**A 的修复必须以真实 Windows 还原态人工点击为唯一验收依据**，不得再以 headless 几何探针替代。

### 三、候选修复审计（边界 / 最小化 / 回归）

**症状 A**
- **A1（删 `page.tsx:545` `flex-1` 的 `drag`，品牌簇 `:530` 为唯一拖拽手柄）——最小且对两种候选机理都根治**：删后**最右且唯一**的 `drag` 面收敛到**远离右簇的最左品牌簇**，右簇左侧退化为普通 `flex-1` 占位（HTCLIENT）；无论真凶是「右溢」还是「尾边减除失效」，**右簇都不再与任何 `drag` 面相邻/共边**，故均被覆盖。窗口仍可由品牌簇拖动、`onTitleDoubleClick`（`:495-498`）的 `.drag-region` allowlist 不变→双击最大化不回归。**边界须覆盖**：四边/四角 resize、最大化态、mac（`titleBarStyle:'hidden'` 分支不受影响）、纯 Web `pnpm dev` 零回归。
- **硬门槛（非可选）**：Subtask #2/#3 **必须在真实 Windows 还原态人工逐一点击右簇五类控件确认下发**（前序正因略过此步而复发）。
- **升级路径**：若 A1 经真机仍残留失效，**直接升级 A3（Windows Controls Overlay）**——从架构上把 caption 命中交还系统、绕开 `app-region` 缺陷；**不要在 A2/顶边微缩进等 CSS 微调上空转**（A2 仅对已被否决的机理②有意义，且改顶栏视觉高度，列为否决项）。

**症状 B**
- **B1 通道已验证可用，但原写法有两处边界缺口（评审实查 `agent-env.js`/`config.js` 后认定）**：
  - **(a) 取值源脆弱**：`build-agent-env.mjs` 只从 dev 的 `services/quant/.env` 拷键；若该 `.env` 无 `LIVE_AUTO_TRADING_ENABLED`，产物 `agent.env` 仍不含该键 → 打包默认仍禁用。
  - **(b) 升级不重播种**：`agent-env.js:31-44` `seedUserAgentEnv` 在 `userData/agent.env` **已存在时直接返回、不覆盖**，而 `loadAgentEnv` **优先读 `userData`**——既装过旧版（已播种无此键的 `userData/agent.env`）的用户，即便新包资源副本含该键也**永不生效**。
  - → **评审收敛的更稳健且仍最小的单点修复**：在 **`desktop/src/lib/config.js` `backendEnv` 的 `mode==='prod'` 分支注入默认** `env.LIVE_AUTO_TRADING_ENABLED ??= 'true'`（置于 `agentEnv` 透传循环之前，使用户在 `userData/agent.env` 的显式值仍可覆盖）。它**不依赖 `userData` 重播种、不依赖 dev `.env`、仅作用 prod、改动单点一行**，同时**顺手修正 `config.js:44` 注释漏列**。（如需 dev 平价，可附带在 `build-agent-env.mjs` 写入「打包默认 `LIVE_AUTO_TRADING_ENABLED=true`」占位——但非必需，dev 本就经 `services/quant/.env` 自配。）
- **最小化范围（避免做多余的事）**：守门四条里**只有** `live_auto_trading_enabled` 默认 `False` 是肇因；`live_trading_enabled` 默认 `True`、`live_max_order_notional_usd`/`live_default_td_mode`/`live_default_leverage` 均有可用默认，写门（`live_auto.py:33`）也只另需 `live_trading_enabled`（已 True）。故**只注入 `LIVE_AUTO_TRADING_ENABLED` 一个键即可**，不应顺带注入其余 `LIVE_*`（无收益、扩大改动面）。
- **B2（禁用态就地中文原因）——保留**：纯 UX、零风险、低成本；把「守门拦截」与「按钮坏了」区分开，对未来同类报障也是护栏。
- **B3（改 `config.py:59` 默认为 True）——否决（同意原报告）**：那是**服务端通用默认**，改之会让任意部署（含 server）默认开启无人值守自动下单，破坏「自动化专用闸默认关」的安全契约；正确做法是**仅在桌面打包形态**经 env 覆盖，安全护栏（名义上限、单会话、1%/5% 资金、二次确认+acknowledge、审计）全部保留。

### 四、最终推荐（单一方案，逐症状）

> **症状 A：方案 A1 —— 删除 `apps/web/app/page.tsx:545` 中部 `flex-1` 拖拽面的 `drag-region`（改为普通 `flex-1` 占位），令品牌簇（`:530`）成为唯一拖拽手柄。**
> **症状 B：精炼版 B1 —— 在 `desktop/src/lib/config.js` `backendEnv` 的 prod 分支注入默认 `LIVE_AUTO_TRADING_ENABLED='true'`（显式 `userData` 值可覆盖）并修正 `:44` 注释；附带 B2（禁用态就地中文原因）。仅注入该一个键。**

**理由（凝练）**：
1. **A1 机理对位且最小**：经评审纠正的机理为「最右 `drag` 面尾边减除失效、命中区外溢到窗口右缘吞整片右簇」；A1 删掉这块**唯一与右簇相邻的最右 `drag` 面**，对「右溢」与「尾边减除失效」两种候选机理**都根治**，是 Web 零回归、保留产品形态（自绘暖色顶栏+右侧三键）的唯一根因级最小改动；真机还原态人工验收为**硬门槛**，失败即升级 A3。
2. **B1 精炼直击且最小**：根因是「透传通道齐备但无源产出 `LIVE_AUTO_TRADING_ENABLED`」；在 `config.js` prod 分支单点注入既**补上唯一缺失的源**，又**规避** B1 原写法的两处边界缺口（dev `.env` 脆弱、`userData` 升级不重播种），且**不动透传逻辑、只加一行**；只注入这一个必需键，安全默认（`config.py`）不变。
3. **B2 提升可诊断性**：禁用原因就地可见，避免「守门」被误判「坏了」。

**验收对齐（交付 Subtask #2/#3 的硬性口径）**：
- **A**：**真实 Windows 还原态**下，顶栏右侧「模拟盘/实盘」「主题」「Factor」「设置齿轮」「最小化/最大化/关闭」**单击均下发生效**；品牌簇仍可拖窗、双击仍可最大化；四边/四角 resize、最大化态、mac、纯 Web `pnpm dev` **均不回归**。
- **B**：打包态 `curl http://127.0.0.1:18760/api/live-auto/status` 回 `liveAutoTradingEnabled:true`；实时模式切到实盘、配好默认实盘账户、选定策略后「自动交易」开关**可点→弹确认弹窗→可启动会话**；任一守门未满足时**就地显示中文原因**（B2）。
- **范围守卫**：A 仅改 `apps/web/app/page.tsx`（升级 A3 时才动 `desktop/src/main.js`）；B 仅改 `desktop/src/lib/config.js` 与 `apps/web/components/Strategy/AutoTradeToggle.tsx`（B2，可选附带 `desktop/scripts/build-agent-env.mjs` 作 dev 平价）；**不得带入** `desktop/dist/`、`tmps/`、`.next/` 等构建/临时产物。
