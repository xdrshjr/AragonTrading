# 顶栏右侧不可点 +「模拟盘/实盘」切不动 —— 问题定位与分析

- **Bug slug**: `desktop-header-right-and-live-toggle-unclickable`
- **诊断节点**: Subtask #0（问题定位与分析，本节点**不写修复代码**）
- **日期**: 2026-06-29
- **本轮来源**: 多轮迭代第 4 轮。在第 2 轮 `8be958a`（A1：删中部拖拽面）+ 第 3 轮 `c3eaa65`（解耦账户三态）之上继续报障。
- **结论先行（单一根因 + 一条下游）**:
  > **症状 A（顶栏右侧整片不可点）= Windows 无边框（`frame:false`）+ 默认可缩放（`WS_THICKFRAME`）窗口在「还原态」下的 OS 非客户区 / caption 命中缺陷的第 3 次复发。** 这条几何根因早在 `desktop-titlebar-buttons-unclickable-when-restored` 用 Electron 探针实测确立（还原态帧内缩进 0px / 最大化 7px）。三轮 CSS 侧补丁——整条顶栏 `drag`（`ff20361`）→ 反转契约（`b6abdb1`）→ A1 删中部拖拽面（`8be958a`）——**均未根治**：**本轮决定性的新证据是「A1 不仅在源码、且已打进当前唯一可运行的最新构建（`desktop/dist` 06-29 00:57），症状却照旧复发」**。这把问题从「补丁没生效 / 构建陈旧」**收敛**为「**`-webkit-app-region` 这条 CSS 命中路线在真实 Windows 上不可靠**」——正是前两轮诊断**预先承诺**的「A1 实测仍不足即升级 A3（Windows Controls Overlay）」触发条件。
  >
  > **症状 B（「模拟盘/实盘」切不动）不是独立 bug，而是 A 的下游**：该分段开关就长在「不可点的右簇」里。本轮取证显示后端在线、`/api/accounts` 返回**就绪默认账户**（`hasSecret/hasPassphrase` 全真、`defaultId=1`），按第 3 轮 `c3eaa65` 落地的逻辑 `selectLive` 会**无条件切到实盘**——也就是说**逻辑层一旦收到点击就会切**，「切不动」纯粹因为**那一下点击根本没下发到渲染层**。故修好 A（让右簇可点）即解 B，无需另写切换逻辑。

---

## 一、问题描述（Problem description）

第 4 轮用户报障（原文）：

> 软件最上方，整个 header 的右侧，无法点击，右侧的 模拟盘/实盘 按钮无法点击切换 实盘 和 模拟盘，需要定位问题原因并且修复

拆为两个症状：

- **症状 A（顶栏右侧整片不可点）**：桌面端顶栏**右侧整片**控件——账户模式切换（模拟盘/实盘）、主题、Factor、设置齿轮、最小化/最大化/关闭——单击无反应；左侧品牌区不受影响。措辞与第 2 轮几乎逐字一致（第 2 轮：「整个 header 的右侧，无法点击」）。
- **症状 B（「模拟盘/实盘」切不动）**：右簇内的分段开关点了不切换。

> **本轮与第 3 轮的关键差异**：第 3 轮用户**只**抱怨「模拟盘/实盘切不动」、**不再**提整片右侧，第 3 轮诊断据此判定「A1 已让右侧恢复可点、残留的只是切换逻辑」，遂修了 `c3eaa65`。**本轮整片右侧不可点的抱怨回归**——说明「A1 已根治几何缺陷」这一第 3 轮的**前提假设站不住**：要么 A1 在真实 Windows 还原态根本没生效（第 3 轮恰好在最大化态测试，故只看到逻辑层的 toggle 问题），要么几何缺陷本就是还原/最大化态相关的间歇症状。两种解读都指向**同一结论：A1（CSS 命中补丁）未能在真实 Windows 还原态稳定根治几何缺陷**。

---

## 二、复现步骤（Reproduction steps）

### 2.0 端口与运行目标

- 桌面后端固定 `18760`、前端 `18761`（`desktop/src/lib/config.js:8-9`）。
- 唯一可运行的桌面二进制：`desktop/dist/win-unpacked/Aragon Trading.exe`（最新，见 §3.2）。`appId=com.aragon.trading`、`productName=Aragon Trading`、`output: dist`（`desktop/electron-builder.yml:4-9`）。

### 2.1 症状 A：顶栏右侧不可点（端到端，几何根因）

1. 启动桌面端任一形态：
   - 生产态：运行 `desktop/dist/win-unpacked/Aragon Trading.exe`（最新构建，已含 A1）。
   - 开发态：`desktop/` 下 `npm run dev`（窗口默认 1440×900 **还原态**，`desktop/src/main.js:214-215`）。
2. **确保窗口处于还原态**（非最大化；默认启动即还原态）。
3. 单击顶栏**右侧**的「模拟盘/实盘」分段、主题、Factor、设置齿轮、窗口控制键 → **无反应**（在中部空白处按下拖动则整窗跟随）。
4. 点「最大化」铺满屏幕，再次单击同样的右侧控件 → **此时生效**；再「还原」→ 右侧**再次失效**。可在「还原↔最大化」间稳定复现。

> 关键判别：失效**只与是否最大化相关**、且**只发生在右侧**——这是 `desktop-titlebar-buttons-unclickable-when-restored` 已实测确立的几何缺陷签名（最大化可点、还原不可点），与覆盖层 / z-index 遮挡（与窗口态无关）**不同**。

### 2.2 症状 B：「模拟盘/实盘」切不动（实为 A 的下游）

1. 同一还原态窗口，单击右簇内的「实盘」分段 → **无反应、不切换**。
2. 取证（确认 toggle 逻辑层本会切，故「切不动」只能来自「点击没下发」）：

```bash
# 后端在线时，看默认账户是否就绪（决定 selectLive 走哪条分支）
curl -s http://127.0.0.1:18760/api/accounts
# 或经前端代理（与渲染层同源同口径）
curl -s http://127.0.0.1:18761/api/py/accounts
```

**本轮实测回包**（后端在线）：

```json
{"accounts":[{"id":1,"label":"Dev-JR-2623","apiKeyPreview":"····c848",
  "hasSecret":true,"hasPassphrase":true,"isDefault":true,...}],"defaultId":1}
```

`hasSecret=true && hasPassphrase=true && defaultId=1` → `hasReadyDefault=true`、`accountsQ.isSuccess=true`。代入 `c3eaa65` 逻辑（`AccountModeSwitch.tsx:62-89`），`selectLive` 命中**状态④（已就绪默认账户）→ 无条件 `setAccountMode("live")`**。**即逻辑层只要收到点击就会切到实盘**。故「切不动」的唯一可能是**点击根本没到渲染层**——这把 B 钉死为 A（右簇被 OS 吞点击）的下游，而非切换逻辑问题。

### 2.3 判别前置（决定走哪条修复，**强制 pre-flight**）

在**同一还原态窗口**里依次单击右簇的「主题」「设置齿轮」：
- **若二者均无反应** → 几何根因仍在（症状 A 成立）→ 按 §五走「pre-flight 确认 + WCO」路线。
- **若二者均可点、唯独「实盘」切不动** → 退化为纯切换逻辑问题（参 `sim-live-account-mode-toggle-cannot-switch`），但本轮 `/api/accounts` 实测为就绪默认账户、`c3eaa65` 已落地，逻辑层不该拦截，**优先怀疑构建/会话陈旧**（§五 pre-flight）。

---

## 三、根因分析（Root cause analysis）

### 3.1 触发条件未变：无边框 + 默认可缩放

- `desktop/src/main.js:223` `frame: false`（win/linux）；mac 走 `titleBarStyle:'hidden'`（`:224-230`）。
- `desktop/src/main.js:213-237` 的 `opts` **未设 `resizable`** → 取默认 `true` → Windows `WS_THICKFRAME`，OS 在窗口四边（含顶边）保留 resize / caption 非客户命中带。
- 初始 `1440×900` 非最大化（`:214-215`）→ **开箱即还原态**，正是受影响态。

这与 `desktop-titlebar-buttons-unclickable-when-restored` 实测确立的几何触发条件**完全一致、本轮未改动**。

### 3.2 决定性新证据：A1 已落地最新构建，症状仍复发 → CSS 命中路线失败

本轮把「补丁是否真的进了用户在跑的构建」逐层钉死（前几轮反复栽在这条假设上）：

**(1) 源码侧 A1 在位。** 当前顶栏（`apps/web/app/page.tsx:517-556`）：
- `:517-524` `<header>` 不再 `drag`（仅 flex 容器 + `onDoubleClick`）；
- `:531` **品牌簇是唯一 `drag-region`**（最左、无交互控件）；
- `:546` 中部 `<div className="flex-1 self-stretch" />`——**已无 `drag-region`**（A1 删除了它）；
- `:547-555` 右簇 `no-drag`，含 `AccountModeSwitch` / 主题 / `FactorButton` / `SettingsGearButton` / `WindowControls`。
- 拖拽契约 `apps/web/app/globals.css:137-146`（`.drag-region{drag}` + `.no-drag` 及 `.drag-region` 内交互元素兜底 `no-drag`）。

**(2) 最新构建侧 A1 同样在位（非陈旧）。** 对 `desktop/dist/win-unpacked/resources/web/.next/server/app/index.html`（mtime **2026-06-29 00:56:49**）做精确扫描：

| 检测项 | 结果 |
|---|---|
| `drag-region flex-1`（A1 前的中部拖拽面） | **0 处** —— 已删除 |
| `flex-1 self-stretch`（A1 后中部占位） | 1 处，且为 `<div class="flex-1 self-stretch"></div>`（**无** drag-region） |
| `drag-region` | **1 处**（仅品牌簇 `drag-region flex items-center gap-2 self-stretch`） |
| `c3eaa65` 标记串「正在校验账户」 | 命中 `page.js` / `static/chunks/app/page-*.js`（toggle 逻辑也在最新构建里） |

> 注：早期一条 `grep -o '...' | head -1 && echo FOUND` 曾误报「构建仍含 A1 前结构」——那是 `head` 恒 exit 0 掩盖了 grep 退出码的**误报**；上表用 Python 精确计数纠正，**最新构建确为 A1 后结构**。

**(3) NSIS 安装包同样新鲜、且无更旧的已安装副本。** `desktop/dist/Aragon Trading-0.1.0-x64.exe`（NSIS 安装包）mtime **2026-06-29 00:58**；`win-unpacked` mtime **00:57**——**都在两条修复提交之后**（`8be958a` 06-28 23:47、`c3eaa65` 06-29 00:41）。全盘只有 `desktop/dist/win-unpacked/Aragon Trading.exe` 一个可运行二进制，`%LOCALAPPDATA%\Programs` 下**无** `Aragon Trading` 已安装副本（`perMachine:false`，`electron-builder.yml:39` 本会装到该处）。

**(4) 排除「另一个 App 的陈旧产物」干扰。** 任务提示里 `M:/takoAI/JRAgentMesh/desktop/release/win-unpacked/resources/server` 看似一个旧的打包，实测其 `index.html`（title=**AragonMesh**、package `name=aragonmesh`、**无 `<header>` / 无 `drag-region` / 无 `app-region`**）是 **AgentMesh 编排器自身**的产物，**不是** Aragon Trading 交易端，与本 bug 无关。

**结论**：A1 既在源码、又在用户唯一能跑的最新构建里，症状仍复发。**「补丁没写 / 没构建 / 跑了旧包」三条都被排除**，剩下的解释只能是：**A1 这套 `-webkit-app-region` CSS 命中修复，在真实 Windows 还原态对该缺陷无效**。这正是 `desktop-titlebar-buttons-unclickable-when-restored` 与 `desktop-header-right-and-auto-trade-toggle-unresponsive` 两轮诊断都**白纸黑字预留**的升级触发条件：

> 「若 A1 经真机仍残留失效，**直接升级 A3（Windows Controls Overlay）**」（见 `desktop-header-right-and-auto-trade-toggle-unresponsive/analysis.md` §三、§四「升级路径」）。

### 3.3 为何「几何缺陷不可 headless 证实」、但证据链仍闭合

OS 非客户区命中测试发生在浏览器进程、渲染层不可观测，`sendInputEvent` 会绕过 NC 命中测试，**无法 headless 录到「click 未下发」**（前序诊断已自陈此局限，也是它「未真机验证即收尾」导致反复复发的根由）。但本轮证据链**从另一侧闭合**：

1. 几何触发条件在位（§3.1）；
2. 唯一的 CSS 缓解（A1）确已进最新构建却仍复发（§3.2）；
3. 症状 B 的逻辑层经**实时后端**证明「收到点击就会切」（§2.2）——反推「点击没下发」，与几何缺陷的预测一致。

三者交叉印证：**右簇不可点是 OS 命中层问题，不是 CSS / 逻辑 / 构建层问题。**

### 3.4 排除项（已证伪 / 已排除）

- **非构建陈旧**：§3.2(2)(3) 实测最新构建含 A1 + c3eaa65。
- **非 toggle 切换逻辑**：§2.2 实测就绪默认账户 + `c3eaa65` 落地 → 逻辑层会切；B 是 A 的下游。
- **非覆盖层 / z-index 遮挡**：(i) 全仓 `apps/web` 仅一处 `fixed inset-0`——`components/ui/index.tsx:317` 的 **Dialog 半透明背板（`z-50`，仅在弹窗开启时条件渲染）**；它开启时遮的是**整屏**（连左侧品牌区一并不可点）且默认关闭，与「右簇专属、还原态专属、品牌区正常」的症状签名**不符**；`page.tsx` / `components/Header/` 内**无**任何常驻 `inset-0` / 高 z-index 全宽条覆盖顶栏。(ii) 覆盖层遮挡与窗口最大化/还原态**无关**，而本症状是**还原态专属**（§2.1）——双重排除覆盖层。
- **非 mac**：mac 走 `titleBarStyle:'hidden'` + 系统红绿灯，caption 命中由系统处理，不受影响。

---

## 四、影响面（Impact surface）

| 维度 | 说明 |
|---|---|
| 平台 | **Windows / Linux**（`frame:false` + 默认可缩放分支）。mac 基本不受影响。 |
| 窗口态 | **还原 / 普通浮动态复现（默认启动态）**；最大化态正常。 |
| 受影响功能（症状 A） | 顶栏右簇全部：模拟盘/实盘切换、主题、Factor、设置齿轮、最小化/最大化/关闭。 |
| 受影响功能（症状 B / 连带） | 整条**实盘链路入口**：右簇不可点 → 无法进入 `accountMode==="live"` → `ChartWorkspace` 不渲染 `LiveBottomPanel`（实盘下单/持仓） → 第 1 轮实时自动交易（守门要求 `live`）**100% 不可达**。 |
| 严重度 | **高**——切实盘 / 进设置 / 关窗的唯一入口，还原态近乎不可用；且已连续 3 轮复发。 |
| 复发 / 升级点 | 几何触发源 `ff20361`（自绘标题栏）；`b6abdb1`（反转契约）、`8be958a`（A1）两轮 CSS 修复**均未根治**；本轮触发前序诊断**预留的 A3（WCO）升级条件**。 |
| 误诊风险 | 极易再被当作「构建陈旧」或「切换逻辑」处理（前三轮已两次走偏）。§2.3 判别步 + §3.2 构建取证用于**一键证伪**误诊。 |

---

## 五、候选修复方案对比（Candidate fixes）

> 本节仅比较，**不写修复代码**（留给下游修复节点）。症状 A 是主因，B 是其下游（修好 A 即解 B）。

### 5.1 症状 A（顶栏右侧不可点）

| 方案 | 做法 | 侵入性 | 风险 | 工作量 | 是否根治 |
|---|---|---|---|---|---|
| **P0（强制 pre-flight，先做）确认构建 + 重启 + 真机还原态实测** | `npm run pack` 产出确定新鲜的 `win-unpacked` → **重启** `Aragon Trading.exe`（非沿用旧进程）→ 还原态逐一点右簇 5 类控件 + toggle；并加一处**构建溯源戳**（About / 标题栏注入 commit hash），让「旧会话」无法再冒充「代码 bug」。 | 极低 | 无 | 极低 | 否（先证伪「旧会话/未重启」这条廉价可能；若实测全通过即本轮闭合） |
| **A3（推荐，P0 仍失败时）Windows Controls Overlay** | win 上 `titleBarStyle:'hidden'` + `titleBarOverlay:{color,symbolColor,height}`，把 caption 命中交还系统；应用控件置于 `env(titlebar-area-*)` 安全区；移除/让位 win 端自绘 `WindowControls` 三键。 | 中-高（删自绘三键、布局让位、颜色同步） | 中（UX/视觉变化；与自绘三键设计取舍冲突，spec §10 R-2 已列为后续增强） | 中 | **是**——系统托管 caption，**彻底绕开 `-webkit-app-region` 命中缺陷**（这是 3 轮 CSS 补丁都失败后的根因级出路） |
| A2 顶边 no-drag 安全缩进 | 顶栏上方加细缩进把右簇压离顶边命中带 | 低 | 中（仅缓解、依赖 DPI/版本；改顶栏视觉高度） | 低 | 否（只对已被前序否决的「小偏移」机理有意义；CSS 路线已三连败，不再走） |
| A4 `resizable:false` / `thickFrame:false` | 去顶边 resize 非客户带 | 低 | **高**——连带禁用 `drag` 拖拽（Electron #30788）/ 破坏 resize（#48576），失去缩放 | 低 | 否（回归更重，否决） |

### 5.2 症状 B（「模拟盘/实盘」切不动）

| 方案 | 做法 | 侵入性 | 风险 | 工作量 | 是否根治 |
|---|---|---|---|---|---|
| **B0（推荐）不单独修，随 A 一并解决** | B 是 A 的下游：右簇可点后，`/api/accounts` 实测就绪默认账户 + `c3eaa65` 三态逻辑会使「实盘」在收到点击时切到 `live`。 | 无 | 无 | 无 | 是（前提是 A 修好） |
| B1 兜底校验 | 若 A 修好后「实盘」仍切不动，按 `sim-live-account-mode-toggle-cannot-switch` 复核三态（`curl /api/accounts`）；本轮实测为状态④就绪，无需改动。 | 极低 | 无 | 极低 | —（仅兜底，本轮证据不指向逻辑层） |

---

## 六、推荐方案（Recommended fix）

> **症状 A：先 P0（确认新鲜构建 + 重启 + 真机还原态实测，并加构建溯源戳）作为强制 pre-flight；P0 实测右簇仍不可点时，升级 A3（Windows Controls Overlay）。症状 B：采 B0——随 A 一并解决，不单独写切换逻辑。**

**理由（凝练）**：

1. **先堵廉价可能（P0），避免对旧会话做无用的大改**。本轮**决定性新证据**是「A1 已在最新构建却仍复发」（§3.2）。在投入 WCO 这类中等改动前，必须先用一次「确定新鲜构建 + 重启 + 真机还原态点击」证伪「app 在 00:57 重建前就开着、没重启」这条**廉价且可逆**的可能；加一处 commit 溯源戳，让未来同类「点不动」报障能**一眼区分**「跑了旧会话」与「真代码 bug」。若 P0 全通过 → 本轮即闭合（属交付/重启缺口，非代码缺陷）。
2. **P0 仍失败即坐实 CSS 路线失败，A3 是根因级出路**。三轮 CSS 补丁（整条 drag → 反转契约 → A1）已**连续失败**；A1 在最新构建里仍复发，等于把「`-webkit-app-region` 在真实 Windows 还原态可靠」这一假设**实证证伪**。WCO 把 caption 命中**交还系统**，从架构上**不再依赖** `app-region` 减法，是覆盖该缺陷、且不再「换皮重试 CSS」的唯一根因级方案。代价是动 win 端自绘三键与布局，故列为 P0 之后的升级项而非首选。
3. **B 无需独立修复**。实时后端 + `c3eaa65` 已证明 toggle 逻辑层「收到点击即切」，B 纯为 A 的下游；修好 A 即解 B，另写逻辑是多余改动。

**验收对齐（交付下游修复节点的硬性口径）**：

1. **真实 Windows 还原态**为唯一验收依据（不可用 headless 几何探针替代——前序正因略过真机验证而反复复发）。
2. **症状 A**：还原态下顶栏右侧「模拟盘/实盘」「主题」「Factor」「设置齿轮」「最小化/最大化/关闭」**单击均下发生效**；品牌簇仍可拖窗、双击仍可最大化；四边/四角 resize、最大化态、mac、纯 Web `pnpm dev` **均不回归**。
3. **症状 B**：右簇恢复可点后，点「实盘」**稳定切到 `live`**（分段高亮 + `ChartWorkspace` 渲染 `LiveBottomPanel`），点「模拟盘」稳定切回。
4. **构建溯源**：运行中的 app 能显示其构建 commit（About / 标题），便于区分「旧会话」与「代码 bug」。
5. **范围守卫**：A3 主要改 `desktop/src/main.js`（窗口 `titleBarStyle`/`titleBarOverlay`）+ `apps/web/app/page.tsx`（控件让位安全区、win 端三键处置）+ 必要的 `globals.css`；**不得带入** `desktop/dist/`、`tmps/`、`.next/` 等构建 / 临时产物；不动 `setAccountMode` 契约与 `liveAuto.ts` 守门。

---

## 附录：关键证据索引

- **几何触发条件（A）**：`desktop/src/main.js:223`（`frame:false`）、`:213-237`（`opts` 未设 `resizable` → 默认 `true`/`WS_THICKFRAME`）、`:214-215`（初始 1440×900 还原态）、`:224-230`（mac 分支）。
- **A1 在源码（A）**：`apps/web/app/page.tsx:517-524`（header 不再 drag）、`:531`（品牌簇唯一 `drag-region`）、**`:546`（中部 `flex-1 self-stretch` 已去 `drag`）**、`:547-555`（右簇 `no-drag` 含 `AccountModeSwitch` 等）；契约 `apps/web/app/globals.css:137-146`。
- **A1 在最新构建、非陈旧（A）**：`desktop/dist/win-unpacked/resources/web/.next/server/app/index.html`（mtime 2026-06-29 00:56:49）实测 `drag-region flex-1` 0 处、`drag-region` 仅品牌簇 1 处、中部为 `<div class="flex-1 self-stretch">`；`page.js` / `static/chunks/app/page-*.js` 含 `c3eaa65` 标记串「正在校验账户」。NSIS 包 `desktop/dist/Aragon Trading-0.1.0-x64.exe` mtime 00:58、`win-unpacked` 00:57，**均晚于** `8be958a`(06-28 23:47) 与 `c3eaa65`(06-29 00:41)。
- **唯一可运行二进制 + 无旧已安装副本（A）**：全盘仅 `desktop/dist/win-unpacked/Aragon Trading.exe`；`%LOCALAPPDATA%\Programs` 无 `Aragon Trading`；`desktop/electron-builder.yml:4-9,30-42`（appId / output / nsis perMachine:false）。
- **JRAgentMesh 释放是另一个 App（排除干扰）**：`M:/takoAI/JRAgentMesh/desktop/release/win-unpacked/resources/server/.next/...`（title=AragonMesh、package `name=aragonmesh`、无 `<header>`/`drag-region`/`app-region`，index.html mtime 06-28 13:14）。
- **症状 B 是 A 的下游（B）**：实测 `curl http://127.0.0.1:18760/api/accounts` → `{"accounts":[{"id":1,"hasSecret":true,"hasPassphrase":true,"isDefault":true,...}],"defaultId":1}`（就绪默认账户）；逻辑 `apps/web/components/Header/AccountModeSwitch.tsx:37-41`（`hasReadyDefault`/`needsSetup`）、`:62-89`（`selectLive` 三态：①/② pending/error、④ 就绪→无条件 `setAccountMode("live")`、③ needsSetup→引导设置）、`:46-49`（水合守门，与 `selectLive` 同 `isSuccess` 前置）。
- **几何根因母诊断（A）**：`docs/diagnoses/desktop-titlebar-buttons-unclickable-when-restored/`（Electron 探针：还原 inset 0、最大化 7px；并自述 NC 命中不可 headless 验证）。**升级承诺**：`docs/diagnoses/desktop-header-right-and-auto-trade-toggle-unresponsive/analysis.md` §三/§四「若 A1 真机仍失效→升级 A3（WCO）」。
- **外部佐证（A）**：Electron [#3022](https://github.com/electron/electron/issues/3022)、[#4139](https://github.com/electron/electron/issues/4139)（无边框顶部 drag 与顶边 resize 命中相互干扰）、[#30788](https://github.com/electron/electron/issues/30788)、[#48576](https://github.com/electron/electron/issues/48576)（`resizable:false`/`thickFrame:false` 的回归，佐证 A4 否决）。

---

## 评审结论（Review Verdict）

> 评审人：Anthropic 工程团队资深评审。评审对象：本文件 Subtask #0 的定位与候选方案。评审范围：仅核验证据链与方案，**不写修复代码**（留给 Subtask #2）。

### 一、证据复核（逐条已核验，结论：根因证据链成立）

我对本文件全部承重引用做了源码级回核，结论是**根因（症状 A = Windows 无边框 + 默认可缩放还原态 caption 命中缺陷）证据链闭合，症状 B 为其下游成立**。复核明细：

| 引用 | 复核结果 |
|---|---|
| `desktop/src/main.js:223` `frame:false`；`:213-237` `opts` 未设 `resizable`（→ 默认 `true`/`WS_THICKFRAME`）；`:214-215` 1440×900 还原态 | ✅ 逐字属实，且为**唯一** `createWindow` 路径（真实执行） |
| `apps/web/app/page.tsx:517-524` header 不再 drag；`:531` 品牌簇唯一 `drag-region`；`:546` 中部 `<div className="flex-1 self-stretch" />`（**确无** `drag-region`，A1 已落地）；`:547-555` 右簇 `no-drag` | ✅ 逐字属实；A1 在源码端确证 |
| `apps/web/app/globals.css:137-147` 拖拽契约（`.drag-region{drag}` + 交互元素兜底 `no-drag`） | ✅ 属实 |
| `AccountModeSwitch.tsx:37-41`（`hasReadyDefault`/`needsSetup`）、`:46-49`（水合守门仅在 `!hasReadyDefault` 时回落 sim）、`:62-89`（`selectLive` 四态，状态④ `hasReadyDefault` → 无条件 `setAccountMode("live")`） | ✅ 属实；逻辑层「收到点击即切」在 `hasReadyDefault=true` 时成立——故 B 是「点击未下发」的下游 |
| 前序诊断「A1 真机仍失效 → 直接升级 A3（WCO）」的承诺 | ✅ 已核实存在于 `docs/diagnoses/desktop-header-right-and-auto-trade-toggle-unresponsive/analysis.md:254`（另 `:179`/`:184` 同义） |

### 二、反例与替代假说审查（已逐一排除，根因唯一性成立）

我主动对「右簇整片不可点、仅还原态、品牌区正常」这一签名穷举了替代解释：

1. **构建/会话陈旧**：§3.2 已用 mtime（`win-unpacked` 00:57、NSIS 00:58，均晚于 `8be958a`/`c3eaa65`）+ Python 精确计数证伪「没写/没构建/跑旧包」。**唯一残留**是「app 在 00:57 重建**前**就已开启、且从未重启」——这是**人侧运维缺口、非代码缺陷**（处置见下「四」）。
2. **覆盖层/z-index 遮挡**：已补全证据——全仓 `apps/web` 仅 `components/ui/index.tsx:317` 一处 `fixed inset-0`（Dialog 背板，条件渲染、遮整屏、默认关闭），与症状签名不符；排除成立（见 §3.4 已强化）。
3. **`pointer-events:none` / 切换逻辑层拦截**：均会**跨窗口态**生效，无法解释「最大化可点、还原不可点」的态相关签名；且主题键/设置齿轮/窗口三键**本就无任何逻辑门控**（纯 `onClick`），它们同样失效直接证伪「这是 toggle 逻辑问题」。`AccountModeSwitch.test.tsx` 的存在进一步说明 toggle 逻辑有单测护栏（B 非逻辑回归）。
4. **机理子类不确定性**（「尾边减除失效」vs「右溢吞右簇」）：两者皆属 `-webkit-app-region` 命中层，**A3（系统托管 caption）对两者都根治**，故根因结论对子机理不敏感——这反而增强而非削弱定论。

**裁定**：根因证据链**airtight**。`-webkit-app-region` CSS 命中路线在真实 Windows 还原态不可靠，已被「A1 进最新构建仍复发」实证证伪（这是本轮决定性新证据）。

### 三、候选方案审查（最小化 / 边界 / 回归）

- **A3（WCO）是「能根治该 bug 的最小改动」**。注意「最小」的正确口径是「能**解决**问题的最小改动」：A2/A1 这类更小的 CSS 微调已**连续 3 轮实证不解决**，故不构成可选项；A4（`resizable:false`/`thickFrame:false`）带入 drag/resize 回归（#30788/#48576），否决正确。A3 是几何路线里唯一根因级方案。
- **A3 的回归面（必须由 Subtask #2 守住，本评审已勘定）**：
  1. **Windows 端必须移除/隐藏自绘 `WindowControls`**（实际路径 `apps/web/components/Desktop/WindowControls.tsx`，`:25` 已是「仅桌面端且非 mac 渲染」）——否则与系统 overlay 三键**重影**。mac 渲染 `null`、浏览器渲染 `null` 的现有行为**不得改**。
  2. `titleBarOverlay.height` 必须等于顶栏 `h-12`（48px），`color`/`symbolColor` 须随主题（亮/暗）切换时通过 `setTitleBarOverlay` 同步，否则三键错位/配色突兀。
  3. 应用右簇（账户开关、主题、Factor、设置）须让位到 `env(titlebar-area-*)` 安全区，避免被系统三键覆盖。
  4. **平台范围**：`titleBarOverlay` 在 Windows/mac 受支持、**Linux 支持不全**；建议 A3 **仅作用于 Windows**（本轮报障平台即 Windows 11 / `10.0.26200`），Linux 维持现有 frameless 路径不动，避免引入不可验证的 Linux 变更。
  5. **不得触碰** `setAccountMode` 契约与 `liveAuto.ts` 守门；**不得提交** `desktop/dist/`、`.next/`、`tmps/` 等构建/临时产物。
- **B 采 B0（不单独修）正确**：实时 `/api/accounts` 就绪默认账户 + `c3eaa65` 四态逻辑 + `AccountModeSwitch.test.tsx` 三重佐证「收到点击即切 live」，B 纯为 A 的下游；另写切换逻辑属多余改动、徒增回归面。

### 四、最终推荐（单一推荐方案）

> **采 A3（Windows Controls Overlay），范围限 Windows，并在同一改动内置入「构建溯源戳」（运行中 app 可显示其构建 commit）。症状 B 随 A 解决，不单独修。**

本评审**有意将原文「P0 先、A3 后」的两段式收敛为单一已承诺的代码改动**，理由如下：

1. **P0 的「重建+重启+真机还原态逐键点击」本质是人侧验收门，不是一个可与 A3 并列的「修复」**。它要求在真实 Windows 还原态人工点击（NC 命中不可 headless 复现，本文件 §3.3 已自陈），而下游 Subtask #2 是**自动化代码节点、无法执行该人工门**。若把验收门当作「是否动手」的前置开关，将使自动化下游**死锁**（既不能跑门、又不敢写码）。故把 P0 的可测部分**重新归类为 A3 落地后的强制人工验收标准**（见下），而非候选修复。
2. **A3 的升级触发条件已客观满足**：前序诊断白纸黑字承诺「A1 真机仍失效→升级 A3」（已核实 `:254`），而 A1 在源码+最新构建均在位、症状仍复发——触发条件成立，无需再以一轮 CSS/会话试探拖延。这是**第 4 次复发**，前 3 轮 CSS 路线全败，继续小修即重蹈覆辙。
3. **保留 P0 中唯一有持久价值的部分——构建溯源戳——并入 A3 改动**：它廉价、可逆，使未来任何「点不动」报障能**一眼区分「跑了旧会话」与「真代码 bug」**，正面消解「二、1」中唯一残留的「未重启旧会话」可能，而无需把它做成阻塞性 pre-flight。

**强制人工验收标准（交付 Subtask #2 后由人在真实 Windows 执行，是历轮反复复发的根因——验收门被跳过）**：

1. **以确定新鲜的构建**（`npm run pack` 重产 `win-unpacked`）**重启** `Aragon Trading.exe`（不得沿用旧进程）。
2. **还原态**下顶栏右侧「模拟盘/实盘」「主题」「Factor」「设置齿轮」「最小化/最大化/关闭」**单击均下发生效**；品牌簇仍可拖窗、双击仍可最大化；四边/四角 resize、最大化态、mac、纯 Web `pnpm dev` **均不回归**。
3. **症状 B**：右簇恢复可点后，点「实盘」**稳定切到 `live`**（分段高亮 + `ChartWorkspace` 渲染 `LiveBottomPanel`），点「模拟盘」稳定切回。
4. **构建溯源**：运行中的 app 能显示其构建 commit（About/标题），用于区分「旧会话」与「代码 bug」。

**一句话结论**：根因证据链成立且唯一；推荐**单一方案 A3（WCO，限 Windows）+ 构建溯源戳**，B 随 A 解；原 P0 的测试动作下沉为 A3 的强制人工验收门，不作为可选修复，以免阻塞自动化下游节点。
