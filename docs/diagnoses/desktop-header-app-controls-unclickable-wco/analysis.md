# 桌面端顶栏右侧应用控件（模拟盘/实盘·主题·设置）不可点，但系统窗口键（最小化/最大化/关闭）正常 —— 问题定位与分析

- **Bug slug**: `desktop-header-app-controls-unclickable-wco`
- **诊断节点**: Subtask #0（问题定位与分析，本节点**不写修复代码、不 git commit**）
- **日期**: 2026-07-09
- **平台**: Windows 11（`10.0.26200`）
- **本轮来源**: 顶栏不可点缺陷的多轮迭代续集。历轮走过：整条顶栏 drag（`ff20361`）→ 反转拖拽契约（`b6abdb1`）→ 删中部拖拽面 A1（`8be958a`）→ 升级 Windows Controls Overlay / WCO（`85b7d77`）。上一轮 `desktop-packaged-wco-header-right-still-unclickable` 因「溯源戳失明、无法判别用户跑的是不是 WCO 构建（RC-1）」卡死，并**预言**：一旦人工门证明「WCO 已激活却仍不可点」，即坐实 RC-2。**本轮用户的报障恰好就是那道人工门的结果。**

---

## 结论先行（单一根因，历轮首次被实证钉死）

> **本轮决定性新事实（用户症状 + 本机 Electron 探针双证）：WCO 确已激活（系统三键因此可点），但「还原态 vs 最大化态帧内缩进不对称（0px vs 7px）」这一**母诊断早已实测确立的几何根因**在 WCO 之下**原样保留、并未被中和**。**
>
> - **用户症状的判别力（本轮关键差异）**：历轮报障是「顶栏右侧**整片**（含最小化/最大化/关闭）不可点」；**本轮用户明确区分**——最小化/最大化/关闭**正常**，只有**应用控件**（模拟盘/实盘、暗色/亮色、设置齿轮）不可点。因 `WindowControls.tsx:27` 自 `85b7d77` 起在 win32 返回 `null`（不再自绘三键），用户能点到的三键**只可能是系统 titleBarOverlay 绘制的**——**这等于用户亲手证明了 WCO 已激活**，正是上一轮 RC-1 苦求而不得的判据。
> - **本机探针实证（Electron 31.7.7 / 本 Windows 11，`repro/result.txt`）**：与生产同构的 WCO 窗口下，`navigator.windowControlsOverlay.visible === true`（**WCO 真激活**）、`env(titlebar-area-*)` 安全区**正确填充**（header `padding-right≈136px`，右簇**不会**滑入系统三键覆盖区）、齿轮计算 `-webkit-app-region: no-drag`**正确**——**然而**还原态 `getBounds == getContentBounds`（**帧内缩进 = 0px**）、最大化态缩进 = **7px**，与母诊断 `desktop-titlebar-buttons-unclickable-when-restored`（还原 0 / 最大化 7）**逐字一致**。
>
> **根因**：窗口仍为默认 `resizable:true`（`WS_THICKFRAME`，`main.js` 未设 `resizable`）。WCO 只是把**最小化/最大化/关闭**交给系统在 overlay 里绘制并走**独立的高优先级 NC 命中路径（HTMINBUTTON/HTMAXBUTTON/HTCLOSE）**，故这三键在任何窗口态都可点；但**应用控件仍是「紧贴窗口顶边、位于 48px 标题栏 caption 带内的 `no-drag` 洞」**，其 NC 命中在**还原态（缩进 0）** 下仍**不被正确减除/对齐**（母诊断的机理①），单击落到 caption / resize 非客户面被 OS 用作「拖窗 / 改尺寸」，**不下发到渲染层** → 表现为「点了没反应」。最大化态缩进变为一致的 7px、resize 带塌缩 → `no-drag` 洞恢复对齐 → 应用控件可点（母诊断已实测）。
>
> **一句话**：**WCO 搬走了三键（它们因此可点），却没有改变还原态可缩放窗口吞掉标题栏内 `no-drag` 洞的底层几何缺陷；剩在 caption 带里的应用控件继续被吞。** 上一轮预留的 RC-2 分支，本轮被症状 + 探针共同坐实。
>
> **两条历轮候选机制经探针实证排除**：①`env(titlebar-area-*)` 安全区失效（右簇滑入系统三键区）——**证伪**（padding≈136px、齿轮在安全区内）；②覆盖层/z-index 遮挡——**证伪**（全仓仅条件渲染的弹窗背板，见 §3.5）。故根因**唯一**收敛到几何缺陷本身。

---

## 一、问题描述（Problem description）

用户报障（原文）：

> 这个项目对应的桌面端的最顶端的这样的一个 Header 的右边的模拟盘、实盘按钮，以及对应的这样的一个暗色、亮色按钮，以及设置的按钮，都无法点击。点击这些按钮都没有反应。但是可以正常的点击最小化、最大化、最小化以及关闭按钮，这几个按钮是正常的。需要把这个问题定位原因并且修复。

拆解为两类控件、两种表现：

- **系统窗口键（最小化 / 最大化 / 关闭）**：**正常可点**。
- **应用控件（顶栏右簇）**：**模拟盘/实盘切换**（`AccountModeSwitch`）、**暗色/亮色主题**（`toggleTheme`）、**设置齿轮**（`SettingsGearButton`）——**单击无任何反应**。

> **本轮与历轮的关键差异**：历轮报障均为「右侧**整片**不可点（含三键）」；本轮用户**把三键从失效集合里摘了出来**（三键正常、只有应用控件失效）。这条差异**不是噪声，而是本轮最强的诊断信号**——它直接指认「三键」与「应用控件」走了**两条不同的命中路径**，前者已被系统接管、后者仍困在旧的几何缺陷里（§三详证）。

---

## 二、复现步骤（Reproduction steps）

### 2.0 运行目标与端口

- 桌面后端固定 `18760`、前端 `18761`（`desktop/src/lib/config.js`）。
- 交易端窗口由 `desktop/src/main.js` `createWindow()` 创建，win 分支为 WCO（`titleBarStyle:'hidden'` + `titleBarOverlay`，`main.js:244-249`），默认 1440×900 **还原态**开箱即受影响态。
- 运行形态：开发态 `desktop/` 下 `npm run dev`；或打包产物 `Aragon Trading.exe`。两者共用同一 `createWindow`。

### 2.1 症状：应用控件不可点、三键正常（端到端）

1. 启动桌面端，**确保窗口处于还原态**（非最大化，默认启动即是）。
2. 单击顶栏右上角**系统三键**（最小化/最大化/关闭）→ **正常生效**（它们由系统 overlay 绘制）。
3. 单击顶栏右簇的**「模拟盘/实盘」「暗色/亮色」「设置齿轮」** → **无反应**。
4. 点系统「最大化」铺满屏幕，再次单击同样的应用控件 → **此时生效**（母诊断规律：最大化可点、还原不可点）；再「还原」→ 应用控件**再次失效**。可在「还原↔最大化」间稳定复现。

> 判别要点：失效**只发生在应用控件、且（历轮规律）只在还原态**；系统三键**任何态都可点**。这把「系统接管的三键」与「仍走 `-webkit-app-region` 命中的应用控件」清晰分开。

### 2.2 机理探针（本机 Electron 31.7.7 实测，随报告留存 `repro/`）

为把「WCO 是否真激活」「env 安全区是否失效」「两态帧内缩进」三件可 headless 度量的事实钉死，构造与生产同构的最小 WCO 窗口（`repro/probe.js` + `repro/page.html`：`titleBarStyle:'hidden'` + `titleBarOverlay{height:48}`、默认 `resizable`、48px 顶栏 = 品牌 `drag` + 右簇 `no-drag`，右簇含一枚显式 `no-drag` 齿轮，复刻 `SettingsGearButton`）。

运行（`desktop/` 下，**须先清 `ELECTRON_RUN_AS_NODE`**）：

```bash
env -u ELECTRON_RUN_AS_NODE ./node_modules/electron/dist/electron.exe \
  ../docs/diagnoses/desktop-header-app-controls-unclickable-wco/repro/probe.js
```

实测输出（`repro/result.txt`，节选）：

```
=== RESTORED (还原态，默认启动态) ===
getBounds        : x=157 y=74 w=1441 h=901
getContentBounds : x=157 y=74 w=1441 h=901
帧内缩进(inset)  : dx=0 dy=0                 ← 内容紧贴窗口顶边，与母诊断「还原 inset 0」一致
WCO visible      : true                       ← WCO 真激活（系统三键即由此绘制）
titlebarAreaRect : {"x":0,"y":0,"w":1305,"h":48}
header pad-right  : 136.143px                 ← env(titlebar-area-*) 正确填充，右簇让位到安全区
gear app-region  : no-drag                    ← 应用控件命中标注正确
gear rect        : {"x":1273,"y":8,"w":32,"h":32}   innerW=1441   ← 齿轮在安全区内(x<1305)，未被三键盖住

=== MAXIMIZED (最大化态) ===
getBounds        : x=-7 y=-7 w=1770 h=1063
getContentBounds : x=0  y=0  w=1756 h=1050
帧内缩进(inset)  : dx=7 dy=7                  ← 与母诊断「最大化 7px」一致
WCO visible      : true
header pad-right  : 137px
gear app-region  : no-drag
gear rect        : {"x":1587,"y":8,...}       innerW=1756
```

**探针三条实证结论**：

1. **WCO 确已激活**（`windowControlsOverlay.visible === true`，两态皆真）→ 系统三键系统绘制、走独立 NC 命中路径 → 印证用户「三键正常」。
2. **env 安全区正确、右簇未被三键覆盖**（还原态 `padding-right≈136px`、齿轮 `x=1273<1305`）→ **证伪**「env 失效 / 右簇滑入三键区」这条历轮候选机制。
3. **两态帧内缩进不对称原样保留**（还原 0px / 最大化 7px）→ 与母诊断实测**逐字一致**，即 **WCO 并未改变还原态可缩放窗口的底层几何**。

> **不可 headless 的部分（如实标注）**：OS 非客户区命中测试发生在浏览器进程、渲染层不可观测，`sendInputEvent` 会绕过 NC 命中，**探针无法录到「click 未下发」本身**。「点击不下发」以 §2.1 的真机端到端复现 + 用户报障为准；探针只钉死上述可度量的几何/能力事实，两者互补。这也是历轮反复复发的根由——真机还原态人工点击是唯一终审门，历轮屡被跳过。

---

## 三、根因分析（Root cause analysis）

### 3.1 触发条件未变：可缩放（WS_THICKFRAME）+ 内容紧贴顶边

- 窗口仍为默认 `resizable:true`：`desktop/src/main.js` 的 `opts`（`:234-263`）**全字段未含 `resizable` / `thickFrame`** → Electron 默认 `resizable:true` → Windows 保留 `WS_THICKFRAME`，OS 在窗口四边（含顶边）保留 resize 非客户命中带。
- win 分支采 WCO：`main.js:244-249` `isWin ? { titleBarStyle:'hidden', titleBarOverlay: overlayForTheme('dark') } : ...`，overlay 高度 `TITLEBAR_OVERLAY_HEIGHT = 48`（`main.js:218`）。
- 顶栏几何：`apps/web/app/page.tsx:567-598` —— `<header className="flex h-12 …">`（48px，= overlay 高度）是 `<div className="flex h-screen flex-col">` 的**首子元素、紧贴窗口顶边 y=0**；右簇 `:590` `<div className="no-drag flex items-center gap-2">` 含 `AccountModeSwitch` / 主题键 / `SettingsGearButton` / `WindowControls`。
- 拖拽契约（反转后）：`:579` 仅品牌簇标 `.drag-region`（唯一拖拽手柄）；`:589` 中部 `flex-1 self-stretch` 无 drag；`:590` 右簇 `no-drag`。CSS `apps/web/app/globals.css:137-146`：`.drag-region{drag}`、`.no-drag` 及 `.drag-region` 内交互元素兜底 `no-drag`。

→ **应用控件的本质形态仍是「紧贴窗口顶边、位于 48px 标题栏 caption 带内、`drag` 手柄旁的若干 `no-drag` 洞」**。这正是母诊断实测会在还原态失效的形态；WCO 未改变它。

### 3.2 决定性：WCO 只搬走三键，未中和几何缺陷（症状 + 探针双证）

**(1) 三键为何正常（走了系统接管的独立路径）。** 自 `85b7d77` 起 `WindowControls.tsx:27` 在 win32 返回 `null`（不再自绘三键，注释明载「Windows 改用系统 titleBarOverlay 绘制，自绘会重影」）。故用户能点到的最小化/最大化/关闭**只能是系统 overlay 绘制**——探针 `WCO visible:true` 佐证。系统三键由 Chromium 自绘框架按 **HTMINBUTTON/HTMAXBUTTON/HTCLOSE** 命中，**优先级高于 caption 带、与 `-webkit-app-region` 无关**，故**任何窗口态都可点**。**这解释了「三键正常」，也反过来证明 WCO 已激活。**

**(2) 应用控件为何仍不可点（几何缺陷原样保留）。** 探针实测两态帧内缩进 = 还原 0 / 最大化 7（§2.2），与母诊断 `desktop-titlebar-buttons-unclickable-when-restored/repro/result.txt` **一致**。母诊断的机理①（经其评审判为主导机理）：**还原态无边框/自绘框可缩放窗口下，`drag` 带内的 `no-drag` 子区未被 OS 命中测试正确减除/对齐**，整条 48px 标题栏（含已内缩 8px 的按钮）被判为可拖拽 caption，`mousedown` 被吞作「拖窗」，`click` 不下发。WCO 把三键交还系统，却**没有**去掉 `WS_THICKFRAME`、也**没有**改变还原态 inset=0 的内容贴边几何 → **caption 带里剩下的应用控件继续复用同一失效原语**。最大化态缩进变为一致的 7px、resize 带塌缩 → `no-drag` 洞恢复对齐 → 应用控件可点（母诊断已实测「最大化可点」）。

**(3) 关键旁证：连显式 `no-drag` 的齿轮也失效。** `SettingsGearButton.tsx:23` 显式带 `no-drag`（探针也确认其计算值 `no-drag`），仍在还原态失效——**排除**「个别控件漏标 no-drag」这类 CSS 级解释，坐实是**整条 caption 带的 NC 命中层问题**，而非某控件的 app-region 标注问题。

### 3.3 排除项（经探针 / 代码实证证伪）

- **非 env 安全区失效**：探针还原态 `header padding-right≈136px`（`env(titlebar-area-width)` 正确填充）、齿轮 `x=1273 < titlebarAreaRect.w=1305` → 右簇**在安全区内、未被系统三键覆盖**。故「env 未填充 → padding 归零 → 右簇滑入三键区」这条历轮候选**被证伪**。
- **非覆盖层 / z-index 遮挡**：全仓 `apps/web` 的 `fixed inset-0` 仅 `components/ui/index.tsx:319`（Dialog 背板）与 `WeightExploreModal.tsx:237`，**均为条件渲染的整屏弹窗背板**（开启时连品牌区也一并遮住、默认关闭），与「应用控件专属、品牌区正常、还原态相关」的症状签名**不符**；顶栏 / NavRail 无常驻 `inset-0` 或高 z-index 全宽条覆盖。故覆盖层**被排除**。
- **非切换逻辑**：`AccountModeSwitch.tsx:74-78`「就绪默认账户 → 无条件 `setAccountMode("live")`」；主题键 / 齿轮为纯 `onClick`（`page.tsx:592-595`、`SettingsGearButton.tsx`），**三者句柄互不相干却同时失效** → 只能是共同的命中层问题，非逻辑层。
- **非构建陈旧 / 跑旧包**：本轮无需再纠结此项——用户「三键正常」已实证跑的是含 WCO 的构建（`85b7d77` 后，win 端不自绘三键，旧包三键会一并失效）。这恰好**闭合了上一轮 RC-1（构建身份不可辨识）** 的缺口。
- **非 mac / 非纯 Web**：mac 走 `titleBarStyle:'hidden'` + 系统红绿灯，caption 由系统处理；浏览器 `-webkit-app-region` 为无副作用样式。两者不受影响。

### 3.4 与历轮诊断的关系（同一几何根因的第 N 次、且首次被症状+探针共同钉死）

| 轮次 | 处置 | 结果 |
|---|---|---|
| 母诊断 `…buttons-unclickable-when-restored` | 探针实测 inset 0/7，判定 frameless+resizable 还原态 NC 缺陷；荐**方案 A 反转契约** | A 落地（`b6abdb1`/`8be958a`）仍复发 |
| `…live-toggle-unclickable` | A1 进最新构建仍复发 → 荐**升级 A3（WCO）** | WCO 落地（`85b7d77`） |
| `…packaged-wco-…still-unclickable` | 卡在 RC-1（溯源戳失明，无法判别用户是否跑 WCO）；**预言** RC-2「WCO 激活却仍不可点」 | 迭代环卡死 |
| **本轮** | 用户「三键正常/应用控件失效」= WCO 激活的活证 + 本机探针实证几何缺陷保留 | **RC-2 被坐实：WCO 未中和几何缺陷，应用控件仍困在 caption 带** |

**结论**：这不是「又一处 CSS 没调对」，而是**历轮几何根因（还原态可缩放窗口吞掉标题栏内 `no-drag` 洞）在 WCO 之下依旧存活**；WCO 只解决了三键、没解决「应用控件长在 caption 带里」。要根治，必须让**应用控件不再落在标题栏 caption 带内**（§五/§六）。

---

## 四、影响面（Impact surface）

| 维度 | 说明 |
|---|---|
| 平台 | **Windows**（本轮报障平台 Windows 11 / `10.0.26200`）。mac 走系统红绿灯不受影响；linux 维持 frameless 旧路径（`main.js:256`），非本轮报障平台。 |
| 窗口态 | **还原 / 普通浮动态**（默认启动态）复现；最大化态历轮正常。 |
| 受影响功能（直接） | 顶栏右簇应用控件：**模拟盘/实盘切换、主题切换、设置齿轮**。 |
| 受影响功能（连带） | **整条实盘链路入口**：右簇不可点 → 无法进 `accountMode==="live"` → `ChartWorkspace` 不渲染 `LiveBottomPanel`（实盘下单/持仓）→ 实时/AI 自动交易（守门要求 live）不可达；**设置中心入口**（齿轮）亦被堵，用户无法进设置配置账户/主题。 |
| 未受影响 | 系统三键（最小化/最大化/关闭）正常；品牌簇拖窗 / 双击最大化正常；主内容区（图表/面板，`page.tsx:673` `<main className="no-drag …">`）正常。 |
| 严重度 | **高**——切实盘 / 进设置 / 切主题的唯一入口，还原态近乎不可用；且为同一几何根因的多轮复发。 |
| 误诊风险 | 极易再被当作「env 安全区没让位」或「某控件漏标 no-drag」处理——§2.2 探针（padding≈136px、齿轮 no-drag 却失效）用于**一键证伪**这两类误诊。 |

---

## 五、候选修复方案对比（Candidate fixes）

> 本节仅比较，**不写修复代码**（留给 Subtask #2）。核心目标：让应用控件**不再落在 48px 标题栏 caption 带内**、从而不再依赖「还原态被 OS 吞掉的 `no-drag` 洞」。历轮已证 CSS/几何微调（反转契约、env 让位）在真机还原态**不可靠**；本轮探针进一步证 WCO 也未中和该几何。

| 方案 | 做法 | 侵入性 | 风险 | 工作量 | 是否根治 |
|---|---|---|---|---|---|
| **P1（推荐）把应用控件移出 caption 带，下沉到标题栏下方的客户区工具条** | 顶部 48px（= `titleBarOverlay.height`）标题栏**只留**品牌簇（`drag` 手柄）+ 系统三键（WCO 绘制）；把 `AccountModeSwitch` / 主题键 / 设置齿轮**移到紧贴其下的一条 `no-drag` 客户区工具条**（`y ≥ 48`，落在 overlay caption 矩形之外，纯 HTCLIENT）。控件不再是「caption 带里的 no-drag 洞」，还原/最大化任何态、任何 Chromium 版本都不受 NC 命中缺陷影响。 | 中（顶栏结构由「单行」改「标题栏 + 工具条」两段） | 低（纯布局；不动窗口选项、IPC、数据流；mac/linux/web 仅是控件渲染到品牌行下方，行为等价） | 中 | **是**——从形态上消除对「caption 带内 no-drag 洞」的依赖 |
| P2 Windows 改回原生边框（`frame:true`，去 `titleBarStyle`/`titleBarOverlay`） | 由系统绘原生标题栏（含原生三键），应用 `<header>` 整体落到原生标题栏**下方的客户区** → 全部控件天然 HTCLIENT | 中（去 WCO 分支、去自绘/让位逻辑） | 中（**倒退自定义标题栏设计**：顶部多一条原生标题栏，与「暖色自绘顶栏」产品形态冲突；mac 不动） | 低-中 | 是（但以放弃自绘标题栏为代价） |
| P3 `resizable:false` / `thickFrame:false` 去顶边 resize 带 | 关窗口可缩放 / 去 WS_THICKFRAME | 低 | **高**——连带禁用自定义 `drag` 拖拽（Electron #30788）、破坏 resize（#48576）、失去缩放；历轮已否决 | 低 | 否（引入更重回归） |
| P4 顶边留 `no-drag` 安全缩进（母诊断方案 B） | 在标题栏上方加细缩进，把控件压离顶边 resize 带 | 低 | 中（只对「顶边 resize 带(机理②)」有效；而主导是机理①「整条 caption 带吞 no-drag 洞」——齿轮 y=8..40 全高失效即证②不足） | 低 | 否（对主导机理无效） |

> **症状连带项（实盘切换/设置入口）无需单独修**：三者皆为「应用控件不可点」的下游，P1 修好右簇可点后即恢复；`AccountModeSwitch` 三态逻辑（`c3eaa65`）与守门契约**不得触碰**。

---

## 六、推荐方案（Recommended fix）

> **采 P1：把顶栏应用控件（模拟盘/实盘、主题、设置齿轮）移出 48px 标题栏 caption 带，下沉到其正下方的一条 `no-drag` 客户区工具条。顶部 48px 只保留品牌簇（拖拽手柄）+ 系统三键（WCO）。** 保留 WCO（三键系统绘制、已实证可点）、保留自绘暖色顶栏观感，同时把应用控件彻底移出失效几何。

**理由（凝练）**：

1. **直击唯一根因**。本轮已用**症状（三键可点 = WCO 激活）+ 本机探针（WCO visible=true、env 安全区正常、齿栏 no-drag 正确，却仍还原 inset 0 / 最大化 7）** 双证：不可点**不是** env 失效、**不是**覆盖层、**不是**某控件漏标——而是「应用控件长在还原态会吞 `no-drag` 洞的 48px caption 带里」。**把控件移出该带（下沉到 `y≥48` 客户区）是唯一从形态上消除该依赖的动作**：客户区 HTCLIENT 命中与窗口态无关、与 `-webkit-app-region` 减除无关，故还原/最大化都可点。
2. **保留 WCO 的既得成果、不再走已连败的 CSS/几何微调**。三键已由系统接管并实证可点；P1 不动窗口选项、不动 WCO、不动三键，只把**应用控件**从 caption 带移到客户区。反转契约、env 让位这类 CSS 侧微调历轮**连败**，探针证其在 WCO 下依旧不解决——不再重试。
3. **最小破坏面、跨平台零回归**。P1 是纯前端布局改动（`apps/web/app/page.tsx` 顶栏结构 + 必要的 `globals.css`）：mac/linux/纯 Web 上「控件渲染到品牌行下方」行为等价、无功能回归；不碰 `setAccountMode` 契约、`liveAuto` 守门、IPC、数据流。相较 P2（倒退自绘标题栏）与 P3（重回归），P1 侵入面与风险最低而根治性最强。
4. **P4 无效、P3 危险、P2 倒退**，均不作主修（理由见 §五）。

**交付 Subtask #2 的实现约束（硬性）**：

1. **顶部 48px 标题栏行**：只含品牌簇（唯一 `.drag-region` 拖拽手柄）+ 系统三键区（WCO 绘制，右侧安全区）；高度须与 `titleBarOverlay.height`（48）一致。
2. **应用控件工具条**：`AccountModeSwitch` / 主题键 / `SettingsGearButton` 移入紧贴标题栏下方的一条 `no-drag` 行，**整行 `y ≥ 48`**（落在 WCO overlay caption 矩形 `[0,48)` 之外）。可保留细分隔线的既有语义（`page.tsx:603`）。
3. **双击最大化 / 拖窗不回归**：拖拽手柄仍是品牌簇；`onTitleDoubleClick`（`page.tsx:545-548`）经 `closest('.drag-region')` 判定，语义不变。
4. **平台守卫**：改动仅作用于顶栏布局；mac 红绿灯安全区（`page.tsx:570` `macSafeArea`）、linux frameless、纯 Web 行为均不得回归。
5. **范围守卫**：**不得带入** `desktop/dist/`、`.next/`、`tmps/`、`.agentmesh/` 等构建/临时产物；**不得触碰** `setAccountMode` 契约、`liveAuto` 守门、`AccountModeSwitch` 三态逻辑（`c3eaa65`）与 WCO/三键处置。

**强制人工验收门（真实 Windows 还原态执行——历轮反复复发即因此门被跳过）**：

1. 以确定新鲜的构建**重启** `Aragon Trading.exe`。
2. **还原态**下顶栏「模拟盘/实盘」「暗色/亮色」「设置齿轮」**单击均下发生效**；系统三键仍可点；品牌簇仍可拖窗、双击仍可最大化；四边/四角 resize、最大化态、mac、纯 Web `pnpm dev` **均不回归**。
3. 点「实盘」**稳定切 live**（分段高亮 + `ChartWorkspace` 渲染 `LiveBottomPanel`），点「模拟盘」稳定切回；齿轮进/出设置中心正常。

---

## 附录：关键证据索引

- **触发条件（几何）**：`desktop/src/main.js:234-263`（`opts` 未设 `resizable` → 默认 `true`/`WS_THICKFRAME`）、`:244-249`（win WCO 分支）、`:218` `TITLEBAR_OVERLAY_HEIGHT=48`、`:235-236` 1440×900 还原态。
- **WCO 已落地且三键系统绘制**：`85b7d77`（fix: adopt Windows Controls Overlay…，含 WCO probe/dirty stamp，已提交）；`apps/web/components/Desktop/WindowControls.tsx:27`（win32 返回 `null`，不自绘三键）；`desktop/src/main.js:540-543`（`set-overlay-theme` 同步三键配色）。
- **顶栏形态（应用控件在 caption 带内）**：`apps/web/app/page.tsx:567-598`（`<header className="flex h-12 …">` 紧贴顶边；`:579` 品牌唯一 `.drag-region`；`:589` 中部 `flex-1` 无 drag；`:590` 右簇 `.no-drag` 含账户/主题/齿轮/WindowControls）；`apps/web/app/globals.css:137-146`（拖拽契约）、`:153-155`（`.win-titlebar-safe` env 让位）。
- **显式 no-drag 也失效（旁证命中层问题）**：`apps/web/components/Settings/SettingsGearButton.tsx:23`（`no-drag`）；探针实测其计算值 `no-drag` 却仍失效。
- **本机 WCO 探针（决定性）**：`docs/diagnoses/desktop-header-app-controls-unclickable-wco/repro/`（`probe.js` + `page.html` + `result.txt`；Electron 31.7.7 / 本 Windows 11：`windowControlsOverlay.visible=true`、还原 inset=0 / 最大化 inset=7、还原 header `padding-right≈136px`、齿轮在安全区内且 `no-drag`）。
- **母诊断几何根因（同源）**：`docs/diagnoses/desktop-titlebar-buttons-unclickable-when-restored/`（Electron 探针：还原 inset 0 / 最大化 7；机理①「caption 带内 no-drag 洞未减除」为主导；自陈 NC 命中不可 headless 验证）。
- **历轮升级链**：`desktop-header-right-and-live-toggle-unclickable`（裁定升级 WCO）、`desktop-packaged-wco-header-right-still-unclickable`（卡 RC-1、预言 RC-2）。
- **症状连带（实盘入口）**：`apps/web/components/Header/AccountModeSwitch.tsx:74-78`（就绪默认账户 → 无条件切 live）、`:46-49`（水合守门）。
- **外部佐证**：Electron [#3022](https://github.com/electron/electron/issues/3022)/[#4139](https://github.com/electron/electron/issues/4139)（无边框/自绘框顶部 drag 与顶边 resize 命中相互干扰）、[#30788](https://github.com/electron/electron/issues/30788)/[#48576](https://github.com/electron/electron/issues/48576)（`resizable:false`/`thickFrame:false` 的回归，佐证 P3 否决）。

---

## 评审结论（Review Verdict）

- **评审节点**: Subtask #1（分析报告评审与修复，本节点**不写修复代码、不 git commit**）
- **评审人**: 资深评审（Anthropic 工程团队）
- **日期**: 2026-07-09
- **一句话结论**: 根因**定位方向成立、排除项扎实、探针可信**，评审已逐行复核全部代码引用**均属实**；但发现**两处证据链需补强/纠偏**（下详），补强后仍**唯一收敛到推荐方案 P1**。据此裁定：**采 P1，且实现上收窄为「仅 Windows(winOverlay) 分裂两段、mac/linux/纯 Web 保持单行不动」**，以取得最小破坏面。

### 一、根因证据审计（Root cause evidence）

**1. 引用真实性复核——全部通过。** 逐条核对，line 号与代码现状一致：

- `desktop/src/main.js:234-263` `opts` 确未设 `resizable`/`thickFrame`（默认 `resizable:true` → `WS_THICKFRAME`）；`:244-249` win 分支 `titleBarStyle:'hidden' + titleBarOverlay`；`:218` `TITLEBAR_OVERLAY_HEIGHT=48`。✔
- `apps/web/components/Desktop/WindowControls.tsx:27` 确在 `platform==='win32'` 返回 `null`（不自绘三键）。✔ → 用户能点到的三键**只可能**是系统 overlay 绘制，佐证「WCO 已激活」这一本轮决定性判据成立。
- `apps/web/app/page.tsx:567-598` 顶栏结构、`:579` 品牌簇 `.drag-region`、`:589` 中部 `flex-1`（**无** drag）、`:590` 右簇 `.no-drag`、`:545-548` `onTitleDoubleClick` 经 `closest('.drag-region')`、`:603` 分隔线、`:570` `winOverlay` 条件类——全部属实。✔
- `apps/web/app/globals.css:137-146` 拖拽契约、`:153-155` `.win-titlebar-safe` env 让位——属实。✔
- `apps/web/components/Settings/SettingsGearButton.tsx:23` 显式 `no-drag`、`AccountModeSwitch.tsx:74-78/46-49` 三态与守门——属实。✔
- **本机探针复核**：`repro/probe.js` 复刻生产 win 分支窗口选项（`titleBarStyle:'hidden'` + `titleBarOverlay{height:48}` + 默认 `resizable`），`result.txt` 实测「还原 inset=0 / 最大化 inset=7、两态 `WCO visible=true`、还原 header `padding-right≈136px`、齿轮 `x=1273<1305` 在安全区内且 `no-drag`」；与母诊断 `desktop-titlebar-buttons-unclickable-when-restored/repro/result.txt`（inset 0/7）**逐字吻合**。探针构造正确、结论可信。✔

**2. 排除项审计——扎实，且评审补一条独立证据使之更硬。**

- 「env 安全区失效 / 右簇滑入三键区」被探针证伪（padding≈136、齿轮在安全区内）。✔
- 「DOM 覆盖层 / z-index 遮挡」：评审独立全仓复核 `apps/web/app/page.tsx` **无任何** `fixed inset-0` / `z-[...]` / `pointer-events` 覆盖件；全仓 `fixed inset-0` 仅 `components/ui/index.tsx:319`、`WeightExploreModal.tsx:237` 两处条件渲染的整屏弹窗背板。**证伪成立**。✔
- 「逻辑层」：三个句柄互不相干却同时失效 → 只能是共同命中层问题。✔
- 「跑旧包 / 构建陈旧」：用户「三键正常」= 跑的是 `85b7d77` 后不自绘三键的 WCO 构建，闭合上一轮 RC-1。✔

**3. 证据链需补强/纠偏的两处（评审新增，务必带入下游认知）：**

> **补强 A（机理措辞需纠偏，关键）。** 报告多处把应用控件表述为「48px caption 带内的 `no-drag` **洞**」，此措辞直接沿用**母诊断的原始布局**（彼时**整条 header 是 `drag`**，控件才是「drag 面里的 no-drag 洞」）。但**本轮代码早已反转契约**：评审实证**全仓唯一的 `-webkit-app-region: drag` 就是品牌簇**（`globals.css:137-138` 仅 `.drag-region`，`page.tsx:579` 是其唯一挂载点；`:589` 中部 flex-1 无 drag、`:590` 右簇 no-drag）。即**在 DOM 层面，应用控件并非「drag 区里的洞」，而是与品牌 drag 簇平级的独立 no-drag 区**。因此「洞未被减除」这一 DOM 级机理**不再字面成立**。
>
> 精确机理应改述为：**WCO(`titleBarOverlay`) 把顶部 48px（`titlebarAreaRect{0,0,1305,48}`）定义为「系统标题栏/caption 矩形」；该矩形内的 NC 命中由浏览器进程/系统裁决，`-webkit-app-region` 只是向其中「申请」HTCLIENT 洞。还原态 inset=0（内容紧贴顶边、`WS_THICKFRAME` 顶边 resize 带仍在）导致该 caption 矩形内的命中换算未被正确归零，落在 48px 带内的应用控件（无论其 DOM 是 drag 还是 no-drag）之单击被 OS 判作 caption 拖拽/顶边 resize、不下发到渲染层；三键因走系统 HTMINBUTTON/HTMAXBUTTON/HTCLOSE 独立高优先级路径而不受影响。** 这一改述**不改变根因结论**（仍是「还原态可缩放窗口 + 内容贴顶边的几何缺陷在 WCO 之下原样保留」），但把因果链从「DOM 洞减除」纠正为「WCO caption 矩形的 NC 命中在还原态错位」，与本轮反转契约后的真实结构自洽——**这也正是为何『反转契约 + WCO』两道 CSS/系统级处置叠加后仍失效**：只要控件几何落在那 48px caption 矩形内，DOM 侧标注 drag/no-drag 都无法改变 OS 对该矩形的还原态命中错位。
>
> **补强 B（窗口态维度需真机确认）。** 「还原态失效 / 最大化态可点」这一二分，在**本轮**仅由母诊断规律外推，**本轮用户原述只确认了「三键正常、应用控件失效」，未提最大化态**。评审判定：这**不动摇根因**（探针已实证本 WCO 窗口两态 inset 0/7 不对称原样存在），但**强制人工验收门必须同时录『还原态失效→最大化态可点』两态**，以闭合此外推。幸而——见下——**推荐方案 P1 对该二分不敏感**（无论失效是「仅还原」还是「两态皆有」，把控件移出 48px caption 矩形都根治），故此 gap 不构成选型风险。

### 二、候选修复审计（Candidate fixes）

- **P1（移出 caption 带、下沉至 `y≥48` 客户区工具条）——推荐**。边界覆盖充分：并发/错误路径 N/A（纯布局）；命中层面，`y≥48` 落在 WCO overlay caption 矩形 `[0,48)` **之外**、纯 HTCLIENT，与窗口态、与 `-webkit-app-region` 减除、与 Chromium 版本**全部解耦**。**关键优势——机理无关性**：无论真实失效机理是补强 A 的「caption 矩形 NC 错位」、还是残留的顶边 resize 带(机理②)、抑或两态皆失效，**把控件移出该矩形都同时解掉**；相较任何依赖「某一具体机理成立」的 CSS/几何微调（反转契约、env 让位、顶边缩进），P1 是**唯一对机理不确定性稳健**的方案。这正是选 P1 而非更小 CSS 补丁的**决定性理由**（历轮 CSS 微调连败即因其押注单一机理）。
- **P1 的回归审计与实现收窄（评审补充，务必带入 Subtask #2）**：报告称 P1 在 mac/linux/纯 Web「行为等价」，措辞**低估了视觉变更**——把单行顶栏拆成「标题栏行 + 工具条行」在**所有平台**都是可见的两行布局改动，而 mac/linux/Web **本无此 bug**、单行工作正常。为把破坏面压到最小，评审**收窄推荐**：**仅当 `winOverlay` 为真时分裂为两段**（顶部 48px 只余品牌拖拽簇 + 系统三键安全区，应用控件下沉到紧邻其下的 `no-drag` 工具条行）；**mac/linux/纯 Web 保持现有单行结构不变**。该条件化与顶栏既有平台分流一致（`page.tsx:570` `macSafeArea/winOverlay` 已是此模式），复杂度低、且对未受影响平台**零视觉/零行为回归**。
- **P2（改回原生边框 `frame:true`）**：可行但**倒退自绘暖色标题栏产品形态**，且丢弃已实证可点的 WCO 三键既得成果——否决。
- **P3（`resizable:false`/`thickFrame:false`）**：连带禁用自定义拖拽(#30788)、破坏 resize(#48576)、失去缩放，引入更重回归，历轮已否决——否决。
- **P4（顶边安全缩进）**：仅对机理②（顶边 resize 带）有效；而齿轮 `y=8..40` 全高失效证明主导并非②，对 caption 矩形整带失效无效——否决为主修复（可作 P1 落地后 QA 或有兜底，不预防性引入）。

### 三、最终推荐修复（single recommended fix）

> **采 P1，并收窄为「仅 Windows(`winOverlay`) 生效」的两段式顶栏**：
> 1. **顶部 48px 标题栏行（= `titleBarOverlay.height`）**：只保留品牌簇（唯一 `.drag-region` 拖拽手柄）+ 右上角系统三键安全区（WCO 绘制）；**不放任何应用控件**。
> 2. **应用控件工具条行（`y≥48`，落在 WCO caption 矩形之外的纯 HTCLIENT）**：`AccountModeSwitch` / 主题键 / `SettingsGearButton` 移入此 `no-drag` 行。
> 3. **平台守卫**：mac 红绿灯安全区、linux frameless、纯 Web 均**保持现单行结构不动**（两段式仅在 `winOverlay===true` 时启用）。
> 4. **不回归项**：双击最大化仍经 `closest('.drag-region')`（品牌簇）判定；`setAccountMode` 三态契约、`liveAuto` 守门、WCO/三键处置、`AccountModeSwitch` 逻辑（`c3eaa65`）**一律不得触碰**；不得带入 `desktop/dist/`、`.next/`、`.agentmesh/` 等构建/临时产物。

**裁定理由（凝练）**：

1. **直击根因、且对机理不确定性稳健**。评审补强 A 已把机理精确化为「WCO caption 矩形在还原态 inset=0 下的 NC 命中错位」；P1 把控件移出该 48px 矩形、置于纯 HTCLIENT 区，是**唯一从形态上消除对该矩形任何命中行为依赖**的动作，且**同时覆盖**补强 B 未能真机确认的窗口态二分（无论仅还原失效还是两态皆失效均根治）。
2. **保留 WCO 既得成果、终止 CSS 微调连败**。三键已由系统接管并实证可点；P1 不动窗口选项、不动 WCO、不动三键，只搬应用控件。反转契约/env 让位等押注单一机理的 CSS 侧微调历轮连败、探针证其在 WCO 下仍不解决——不再重试。
3. **最小破坏面**。收窄为「仅 Windows 两段式」后，未受影响平台（mac/linux/Web）**零视觉零行为回归**；改动集中在 `apps/web/app/page.tsx` 顶栏结构 + 必要的 `globals.css`，不碰后端/IPC/数据流。相较 P2（倒退）、P3（重回归）、P4（对主导机理无效），P1 侵入面与风险最低而根治性最强。

**强制人工验收门（真实 Windows 还原态执行——历轮反复复发即因此门被跳过；本轮补强 B 要求两态齐验）**：

1. 以确定新鲜的构建重启 `Aragon Trading.exe`。
2. **还原态**下顶栏「模拟盘/实盘」「暗色/亮色」「设置齿轮」单击**均下发生效**；系统三键仍可点；品牌簇仍可拖窗、双击仍可最大化。
3. **切至最大化态**再验同一批应用控件**仍可点**，还原后**再验一次**（闭合补强 B 的窗口态二分）；四边/四角 resize、mac 红绿灯、纯 Web `pnpm dev`、linux **均不回归**。
4. 点「实盘」稳定切 live（分段高亮 + `ChartWorkspace` 渲染 `LiveBottomPanel`）、点「模拟盘」稳定切回；齿轮进/出设置中心正常。

**评审签署**：根因方向与证据链在补入补强 A（机理措辞纠偏为「WCO caption 矩形 NC 错位」）、补强 B（窗口态二分需真机两态确认）后**已闭合**；**唯一推荐修复 = P1（仅 Windows 两段式顶栏，应用控件下沉至 `y≥48` 客户区工具条）**。移交 Subtask #2 实现，验收以上述人工门为终审。
