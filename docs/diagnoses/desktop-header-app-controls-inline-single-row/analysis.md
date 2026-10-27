# 桌面端顶栏应用控件回到「与窗口三键同一行、置于其左侧」且仍可点击 —— 问题定位与分析

- **Bug slug**: `desktop-header-app-controls-inline-single-row`
- **诊断节点**: Subtask #0（问题定位与分析，本节点**不写修复代码、不 git commit**）
- **日期**: 2026-07-09
- **平台**: Windows 11（`10.0.26200`）
- **本轮来源**: 顶栏可点性多轮迭代的**第 2 轮（布局回退轮）**。第 1 轮（提交 `5740adc`）把应用控件从 48px 标题栏 caption 带**下沉到 header 之下的 `y≥48` 客户区工具条**（两行布局），解决了「还原态不可点」。**本轮用户否决两行布局**，要求控件**回到与系统「放大/缩小/关闭」三键同一行、置于其左侧，同时仍可点击**。

---

## 结论先行（单一判断，附反证据）

> **本轮不是「又一处不可点缺陷」，而是「在保住可点性的前提下把 round-1 的两行布局收回单行」。关键张力：用户要的单行内联结构，正是历轮报障过的那种「控件落在 48px 标题栏带内」的结构；naive 地 `git revert 5740adc` 会把控件放回原位，但 round-1 恰恰是靠「移出该带」才修好的——因此单纯回退 = 复发。**
>
> **但证据显示单行内联在当前代码下是可行的、且是 WCO 的标准做法**，理由链如下：
>
> 1. **历轮真正吞点的机制是「drag 区覆盖了控件」，而该 drag 区早已被移除。** 母诊断探针（`desktop-titlebar-buttons-unclickable-when-restored/repro/result.txt`）实测 `headerRegion:"drag"`——**当时整条 header 是 drag 区、控件是其中的 no-drag「洞」**，还原态下 OS 未正确减除这些洞（Electron #3022 类缺陷）。此后拖拽契约被**反转**：`apps/web/app/globals.css:137-139` 全仓唯一的 `-webkit-app-region:drag` 只剩 `.drag-region`，其唯一挂载点是**最左品牌簇**（`apps/web/app/page.tsx:592`）；中部 `flex-1 self-stretch`（`:602`）与右簇（`:610`）**均非 drag**。**即右簇已不再是「drag 区里的洞」，而是与品牌 drag 簇平级、不被任何 drag 区覆盖的独立 no-drag 岛。**
> 2. **系统三键走独立高优先级 NC 路径，与应用控件无关。** `apps/web/components/Desktop/WindowControls.tsx:27` 在 win32 返回 `null`（不自绘），用户能点到的三键**只可能是系统 titleBarOverlay 绘制**（`desktop/src/main.js:244-249`）——它们按 HTMINBUTTON/HTMAXBUTTON/HTCLOSE 命中，任何窗口态都可点，也证明 **WCO 已激活**。
> 3. **应用控件垂直居中，避开顶边 resize 带。** header 为 `flex h-12 items-center`（`page.tsx:581`），控件均为矮高度居中：设置齿轮 `h-8`（`SettingsGearButton.tsx:23`）、主题键 `size="sm"`=`py-1 text-xs`（`ui/index.tsx:24`）、模拟/实盘胶囊 `px-3 py-1 text-xs`（`AccountModeSwitch.tsx:104/127`）→ 在 48px 行内约落在 **y≈11..37**，**不与顶边 `WS_THICKFRAME` resize 非客户带（y≈0..7）重叠**。
> 4. **`win-titlebar-safe` 已把右簇让位到系统三键左侧。** `globals.css:153-155` 令 header `padding-right = 系统三键带宽`；本机 WCO 探针实测还原态该 padding≈136px、齿轮 `x=1273 < titlebarAreaRect.w=1305`（`desktop-header-app-controls-unclickable-wco/repro/result.txt`）——**右簇在安全区内、右缘紧贴系统三键左侧**，天然满足「同一行、置于三键左侧」。
>
> **一句话根因**：round-1 的两行是「移出 caption 带」的**规避式**修复；本轮要单行，须让控件**留在带内且客户命中**。当前反转契约下，右簇已是「无 drag 覆盖、垂直居中避开顶边带、落在系统三键左侧安全区」的纯 no-drag 岛——**把它内联回 header 单行即满足需求，且符合标准 WCO 可点语义**。历轮之所以复发，是 drag 区覆盖（整条 header drag / 中部 drag 面外溢）+ 构建身份混淆，**均已消除**。
>
> **诚实标注的残余不确定性**：OS 非客户命中在浏览器进程裁决、渲染层不可观测，`sendInputEvent` 绕过 NC 命中——**「还原态单击是否真正下发」无法 headless 验证**（历轮所有探针的共同局限）。故本轮**强制以真实 Windows 还原态人工点击为终审门**，并预置 F2 兜底方案。

---

## 一、问题描述（Problem description）

用户报障（原文）：

> 不能更换行，还是要放在 header 最右边放大缩小关闭 相关的按钮的同一行，但是在这些按钮左边放置，同时还要能够正确被点击。

拆解为三条硬性要求：

1. **不换行**：撤销 round-1 的「标题栏行 + 其下 `y≥48` 工具条」两行布局，恢复单行。
2. **同一行 + 置于三键左侧**：应用控件（模拟盘/实盘、暗色/亮色、设置齿轮）须与系统「放大/缩小/关闭」三键处于同一 48px 顶栏行内，且位于三键**左侧**。
3. **仍可点击**：单击须正常下发生效（这是历轮反复失守、也是本轮不能只做布局回退的原因）。

> **本轮与第 1 轮的关系**：第 1 轮解决「应用控件不可点」（把控件下沉出 caption 带）；本轮是**布局偏好回退**——用户接受「可点」这一成果，但不接受「另起一行」的形态，要求在**单行内**同时满足可点。

---

## 二、复现步骤（Reproduction steps）

### 2.0 运行目标与端口

- 桌面后端固定 `18760`、前端 `18761`（`desktop/src/lib/config.js`）。
- 交易端窗口由 `desktop/src/main.js` `createWindow()` 创建；win 分支 = WCO（`titleBarStyle:'hidden'` + `titleBarOverlay{height:48}`，`main.js:244-249/218`），默认 1440×900 **还原态**开箱即受影响态。
- 运行形态：开发态 `desktop/` 下 `npm run dev`；或打包产物 `Aragon Trading.exe`。两者共用同一 `createWindow`。

### 2.1 现状（round-1 两行布局）——需被替换的形态

1. 以当前 `master`（含 `5740adc`）启动桌面端，还原态。
2. 观察顶栏：**第一行**只有左侧品牌簇 + 右上角系统三键；**第二行**（header 之下、`y≥48`）才是「模拟盘/实盘·暗色/亮色·设置齿轮」工具条（`page.tsx:616-620`）。
3. 应用控件可点（round-1 已修）——但**布局是两行**，即本轮用户否决的形态。

### 2.2 反例（naive 回退会复发）——用于验证「不能只回退」

1. 若仅 `git revert 5740adc`（把控件放回 header 单行、删掉 `y≥48` 工具条），得到的正是**第 1 轮用户报障时的 DOM**（`page.tsx` pre-`5740adc`：右簇 `<div className="no-drag flex items-center gap-2">…</div>` 内联在 header 内）。
2. **在当时那份构建上，用户实测还原态应用控件不可点**（第 1 轮报障原文 + `desktop-header-app-controls-unclickable-wco/analysis.md`）。
3. 结论：**单纯回退 = 复发**。本轮修复须在「回到单行」的同时，确保右簇为「无 drag 覆盖 + 避开顶边带 + 在三键左侧安全区」的纯客户 no-drag 岛（§三详证为何这样即可点）。

### 2.3 目标态验收（本轮修复后应达到）

1. 顶栏为**单行**：左品牌簇（拖拽手柄）→ 中部留白 → 应用控件簇 → 系统三键（最右）。
2. **还原态**下单击「模拟盘/实盘」「暗色/亮色」「设置齿轮」**均下发生效**；系统三键仍可点；品牌簇仍可拖窗、双击仍可最大化。
3. **最大化态**再验同一批控件仍可点，还原后再验一次（闭合窗口态二分）。
4. mac 红绿灯、linux frameless、纯 Web `pnpm dev` 均不回归。

> 复现要点：本轮的「Bug」是**布局需求 + 可点性约束**的组合。可点性的终审只能在**真实 Windows 还原态人工点击**完成（NC 命中不可 headless 观测，见 §三末）。

---

## 三、根因分析（Root cause analysis）

### 3.1 历轮真正吞点的是「drag 区覆盖控件」，而非「控件长在 48px 带里」本身

母诊断探针是唯一的实测几何证据：

```
=== RESTORED (非最大化) ===
button/header   : {"btnRect":{"x":936.57,"y":8,"w":32,"h":32},"btnRegion":"no-drag","headerRegion":"drag"}
```
（`docs/diagnoses/desktop-titlebar-buttons-unclickable-when-restored/repro/result.txt`）

- 关键字段 **`headerRegion:"drag"`**：彼时**整条 header 是 drag 区**，按钮是其中 `no-drag` 的「洞」。还原态（inset=0、`WS_THICKFRAME`）下 OS 未正确从 drag 区减除这些 no-drag 洞 → 洞被判作 caption → 单击被吞（Electron [#3022](https://github.com/electron/electron/issues/3022)/[#4139](https://github.com/electron/electron/issues/4139) 记录的无边框拖拽/顶边 resize 命中互扰）。
- 此后（`b6abdb1`/`8be958a`/`85b7d77` 链）拖拽契约被**反转**并升级 WCO，其间还删过一处「中部 `flex-1` drag 面在还原态 OS 命中区外溢到窗口右缘、吞掉整片右簇」的残留（见 `page.tsx:587-591` 注释）。

**即：两处曾真实吞点的机制都要求「有一个 drag 区覆盖到控件」**——要么整条 header drag、要么中部 drag 面外溢。

### 3.2 当前代码：右簇已是「无 drag 覆盖」的独立 no-drag 岛

逐项核对当前 `apps/web/app/page.tsx` 顶栏（`:578-620`）与 `globals.css`（`:137-147`）：

- 全仓唯一 `-webkit-app-region:drag` = `.drag-region`（`globals.css:137-139`）；其唯一挂载点 = **品牌簇**（`page.tsx:592` `<div className="drag-region flex items-center gap-2 self-stretch">`，logo+文字，内容宽、居左）。
- 中部 `page.tsx:602` `<div className="flex-1 self-stretch" />`——**无** app-region 类 → `-webkit-app-region` 取初值 `no-drag`。
- 右簇 `page.tsx:610` `<div className="no-drag flex items-center gap-2">{appControls}</div>`——**显式 no-drag**（当前仅 `!winOverlay` 分支渲染）。

→ **品牌 drag 簇的 OS 命中矩形落在左侧（x≈16..166），不与右簇（x>1000）重叠；中部与右簇均 no-drag。整条 header 除品牌外皆客户区，右簇是不被任何 drag 区覆盖的 no-drag 岛。** §3.1 的两处吞点机制在此结构下**前提已不成立**。

### 3.3 控件垂直居中，避开顶边 resize 非客户带

- header `flex h-12 items-center`（`page.tsx:581`）令子元素在 48px 内**垂直居中**。
- 控件均为矮高度：设置齿轮 `h-8`=32px（`SettingsGearButton.tsx:23`）；主题键 `Button size="sm"`=`px-2.5 py-1 text-xs`（`ui/index.tsx:24`，≈26px）；模拟/实盘胶囊 `role="radio"` `px-3 py-1 text-xs`（`AccountModeSwitch.tsx:104/127`，≈26px）。
- 居中后约落 **y≈11..37**，**不触及顶边 `WS_THICKFRAME` resize 非客户带（y≈0..7）**——故连「仅顶边细带」这一残余机制也不覆盖控件本体。

### 3.4 `win-titlebar-safe` 已实现「同一行 + 三键左侧」的几何

- `globals.css:153-155`：`.win-titlebar-safe{ padding-right: calc(100vw - env(titlebar-area-width,100vw) - env(titlebar-area-x,0px)) }` → header 右内边距 = 系统三键带宽。
- 本机 WCO 探针（`desktop-header-app-controls-unclickable-wco/repro/result.txt`）还原态实测：`header padding-right≈136px`、`titlebarAreaRect{x:0,y:0,w:1305,h:48}`、齿轮 `x=1273 < 1305` → **右簇整体在安全区内、右缘紧贴系统三键左侧、未被三键覆盖**。
- 因此把右簇内联回 header 单行时，它**自然落在系统三键左侧、同一 48px 行**——正是本轮需求 2。

### 3.5 综合机理：为何单行内联即可点（当前结构下）

标准 WCO（`titleBarStyle:'hidden'` + `titleBarOverlay`）语义：系统三键占右上角、走独立 NC 路径；标题栏其余区域的命中**仅由 `-webkit-app-region` 决定**——`drag` 处为 caption/NC，其余（含默认与显式 `no-drag`）为 HTCLIENT，可点。VS Code 等生产 WCO 应用即以此在还原态放置可点标题栏控件。

本项目当前：
- 右簇 = 显式 `no-drag`（客户区），
- **无任何 drag 区覆盖右簇**（§3.2），
- 控件避开顶边 resize 带（§3.3），
- 落在系统三键左侧安全区（§3.4）。

→ **右簇为纯 HTCLIENT，还原态可点。** 历轮不可点由「drag 区覆盖」（§3.1，已移除）+「构建身份混淆」（上一轮 RC-1，用户「三键正常」已闭合、证明跑的是 WCO 构建）共同造成；round-1 的「下沉出带」是稳健但**过度**的规避。本轮把右簇内联回单行，是标准 WCO 做法，满足全部三条需求。

### 3.6 残余不确定性与终审门（务必带入下游）

- **不可 headless 验证**：OS 非客户命中在浏览器进程裁决、渲染层不可观测；`sendInputEvent` 绕过 NC 命中——**「还原态单击是否真正下发」无法自动化验证**（母诊断、round-1 探针的共同自陈局限）。
- 故 §3.5 的「右簇为 HTCLIENT → 可点」是**基于标准 WCO 语义 + 当前结构的强推断**，**终审须真实 Windows 还原态人工点击**（历轮反复复发即因此门被跳过）。
- **若人工门证伪**（还原态内联右簇仍不可点，即坐实「WCO 标题栏带在本机还原态存在超出 drag 区的整带 NC 缺陷」）：升级到 §五 F2（还原态几何/窗口层加固）。

---

## 四、影响面（Impact surface）

| 维度 | 说明 |
|---|---|
| 平台 | **Windows**（WCO 分支，`main.js:244-249`）。mac 红绿灯、linux frameless、纯 Web 无此几何，仅是「控件渲染位置」变化，不受可点性影响。 |
| 窗口态 | **还原/普通浮动态**为历轮易失守态；最大化态历轮正常。修复须两态齐验。 |
| 直接影响功能 | 顶栏应用控件：**模拟盘/实盘切换、主题切换、设置齿轮**——本轮要求「单行、三键左侧、可点」。 |
| 连带（可点性一旦回归） | 整条实盘链路入口（切 live → `ChartWorkspace` 渲染 `LiveBottomPanel`）、设置中心入口（齿轮）。本轮基线（round-1 两行）已可点，故本轮是**布局回退**，不新增功能损伤，但**若回退方式错误（naive revert）会使上述入口在还原态再次失守**。 |
| 未受影响 | 系统三键（最小化/最大化/关闭，系统 overlay 绘制）；品牌簇拖窗/双击最大化；主内容区。 |
| 严重度 | **中**——不是新缺陷，而是布局形态调整 + 防复发；但「防复发」维度严重度高（切实盘/进设置/切主题的唯一入口）。 |
| 误诊风险 | 易被当作「直接 `git revert 5740adc` 即可」——§2.2 已证该回退会复发。须按 §五 F1 的三重守卫内联，而非裸回退。 |

---

## 五、候选修复方案对比（Candidate fixes）

> 本节仅比较，**不写修复代码**（留给 Subtask #2）。核心目标：**单行内联、控件置于系统三键左侧、且还原态可点**。

| 方案 | 做法 | 侵入性 | 风险 | 工作量 | 满足单行 | 是否可点 |
|---|---|---|---|---|---|---|
| **F1（推荐）单行内联 no-drag 岛，三重守卫** | 把应用控件簇作为 **`no-drag` 岛内联回 header 单行**，右对齐于 `win-titlebar-safe` 内边距内（落系统三键左侧）；**删除** round-1 的 `y≥48` 工具条（`page.tsx:614-620`）。三重守卫：①除品牌外无任何 drag 区覆盖右簇（沿用当前反转契约）②控件垂直居中避开顶边 resize 带 ③保留 `win-titlebar-safe` 让位。仅 `winOverlay` 需从「工具条」改回「内联」，mac/linux/Web 本就内联、零改。 | 低（顶栏结构由两行改回单行，≈定向回退 `5740adc` 的 DOM + 保留全部守卫） | 低-中（结构上是标准 WCO；残余风险仅「还原态整带 NC 缺陷是否真存在」，须人工门终审） | 低 | ✔ | 强推断可点，**人工门终审** |
| **F2（兜底/契约条件）F1 + 还原态几何加固** | 若 F1 人工门证伪，则在保持单行的前提下加固窗口层：把还原态 inset 对齐到最大化态（母诊断根因输入 inset 0 vs 7），或收窄品牌 drag 手柄使其不触顶边、避免与顶边 resize 带跨宽合并。 | 中（触 `main.js` 窗口选项 / 顶栏 drag 手柄几何） | 中（CSS 顶部 inset 会与 OS 绘制的三键错位，须走窗口层；需真机反复试） | 中 | ✔ | 待人工门 |
| **F3（现状，用户已否决）保留 round-1 两行** | 维持 `y≥48` 客户区工具条。 | —（现状） | 最低（已实证可点） | 0 | ✘ **不满足单行** | 可点但违背本轮需求 |
| **F4（否决）裸 `git revert 5740adc`** | 直接回退到 pre-`5740adc` 单行 DOM，不加守卫。 | 低 | **高**——即第 1 轮报障态，还原态复发不可点 | 低 | ✔ | ✘ 复发 |

> **P3/P2 类窗口级重构（`resizable:false`/`thickFrame:false`、改回原生边框 `frame:true`）** 已在第 1 轮诊断被否决（禁用自定义拖拽 #30788、破坏 resize #48576、倒退自绘暖色标题栏），本轮不再引入——除非 F2 亦失守才另议。

---

## 六、推荐方案（Recommended fix）

> **采 F1：把顶栏应用控件（模拟盘/实盘、主题、设置齿轮）作为 `no-drag` 岛内联回 header 单行，右对齐于 `win-titlebar-safe` 内边距内（落在系统三键左侧、同一 48px 行、垂直居中避开顶边 resize 带），并删除 round-1 的 `y≥48` 工具条。** 若真实 Windows 还原态人工门证伪，则升级 F2。

> ⚠️ **评审覆盖**：本节 **F1（仅内联）推荐已被文末《评审结论》改判为 F2（单行内联 + 还原态窗口层几何中和）**。决定性依据：提交时序（`8be958a` 删拖拽覆盖 < `85b7d77` WCO < 上一轮报障 < `5740adc`）+ `git show 5740adc` 前像（F1 目标结构与上一轮 buggy 结构窗口层逐字同构）+ 上一轮 `desktop-header-app-controls-unclickable-wco` 的 RC-2 定论——三者共同证明 **F1 会复发**。请以《评审结论》为准。

**理由（凝练）**：

1. **直接满足本轮三条需求且改动最小**。F1 是「定向回退 `5740adc` 的 DOM + 保留全部守卫」：单行（需求1）、右簇落系统三键左侧（需求2，`win-titlebar-safe` 已实现）、纯 no-drag 岛可点（需求3，§3.5）。仅 `winOverlay` 分支从「工具条」改回「内联」，mac/linux/Web 零改。
2. **可点性有结构依据、非空想**。历轮真正吞点的两处 drag 覆盖（整条 header drag / 中部 drag 面外溢）**已移除**；当前右簇无 drag 覆盖、避开顶边带、在三键左侧安全区，符合标准 WCO 可点语义（母诊断探针 `headerRegion:"drag"` 的病灶已不存在）。
3. **不做裸回退**。§2.2 已证 `git revert 5740adc` = 复发；F1 与之的差别在于**明确保留三重守卫并只在 `winOverlay` 切换渲染形态**，且以人工门收口。
4. **诚实预置兜底**。NC 命中不可 headless 验证是历轮共同局限；F1 以「真实 Windows 还原态人工点击」为终审，并预授权 F2 作为证伪后的加固路径，避免再次卡在「不确定→反复复发」。

**交付 Subtask #2 的实现约束（硬性）**：

1. **单行结构**：`winOverlay` 下 header 内单行渲染「品牌 drag 簇 → 中部留白 → 应用控件 no-drag 簇」；系统三键仍由 WCO 在右上角绘制。**删除** `page.tsx:614-620` 的 `y≥48` 工具条块。
2. **三重守卫必须齐全**：①除品牌 `.drag-region` 外，右簇及其祖先**均不得**带 `-webkit-app-region:drag`（沿用反转契约）；②控件保持垂直居中（勿贴顶边）；③保留 header 的 `win-titlebar-safe` 让位（勿使右簇滑入 `x≥titlebar-area-width` 三键区）。
3. **不回归项**：双击最大化仍经 `onTitleDoubleClick`→`closest('.drag-region')`（品牌簇，`page.tsx:545-548`）判定；`setAccountMode` 三态契约、`liveAuto` 守门、`AccountModeSwitch` 逻辑（`c3eaa65`）、WCO/三键处置、`WindowControls` win32 返回 `null`——**一律不得触碰**。
4. **平台守卫**：mac（`macSafeArea`，`page.tsx:583`）、linux frameless、纯 Web 保持现有内联单行行为不变；两段式仅在 `winOverlay===true` 语义下被**收回为单行**。
5. **范围守卫**：**不得带入** `desktop/dist/`、`.next/`、`tmps/`、`.agentmesh/`、`.claude-index/` 等构建/临时产物；改动集中在 `apps/web/app/page.tsx`（顶栏结构）+ 必要时 `globals.css`。

**强制人工验收门（真实 Windows 执行——历轮反复复发即因此门被跳过）**：

1. 以确定新鲜的构建**重启** `Aragon Trading.exe`（或 `desktop/` 下 `npm run dev`）。
2. **还原态**下：顶栏为**单行**，应用控件在系统三键**左侧同一行**；单击「模拟盘/实盘」「暗色/亮色」「设置齿轮」**均下发生效**；系统三键仍可点；品牌簇仍可拖窗、双击仍可最大化。
3. **切最大化态**再验同一批控件仍可点，**还原后再验一次**（闭合窗口态二分）；四边/四角 resize 正常。
4. 点「实盘」稳定切 live（分段高亮 + `ChartWorkspace` 渲染 `LiveBottomPanel`）、点「模拟盘」稳定切回；齿轮进/出设置中心正常。
5. mac 红绿灯、纯 Web `pnpm dev`、linux 均不回归。
6. **若步骤 2 的还原态单击证伪（仍不可点）** → 升级 F2（还原态几何/窗口层加固），并把证伪结果回填本报告。

---

## 附录：关键证据索引

- **本轮需求**：task-board.md「第 2 轮 本轮需求」；第 1 轮提交 `5740adc`（fix: sink Windows header app controls below the WCO caption band）。
- **round-1 两行布局（需被收回单行）**：`apps/web/app/page.tsx:565-576`（`appControls` 片段）、`:609-611`（`!winOverlay` 内联）、`:614-620`（`winOverlay` 的 `y≥48` 工具条）。
- **反转拖拽契约（右簇已非 drag 洞）**：`apps/web/app/globals.css:137-147`（全仓唯一 `.drag-region` = drag，其余 no-drag）；`page.tsx:592`（品牌唯一 `.drag-region`）、`:602`（中部 `flex-1` 无 drag）、`:610`（右簇 `no-drag`）。
- **系统三键系统绘制、WCO 已激活**：`apps/web/components/Desktop/WindowControls.tsx:27`（win32 返回 `null`）；`desktop/src/main.js:244-249`（`titleBarStyle:'hidden'`+`titleBarOverlay`）、`:218`（`TITLEBAR_OVERLAY_HEIGHT=48`）、`:234-263`（`opts` 未设 `resizable`→默认 `WS_THICKFRAME`）。
- **控件矮高度、垂直居中避开顶边带**：`page.tsx:581`（header `h-12 items-center`）；`SettingsGearButton.tsx:23`（`h-8`）、`ui/index.tsx:24`（`size="sm"`=`py-1 text-xs`）、`AccountModeSwitch.tsx:104/127`（胶囊 `py-1 text-xs`）。
- **右簇让位到三键左侧（几何实测）**：`globals.css:153-155`（`.win-titlebar-safe`）；`docs/diagnoses/desktop-header-app-controls-unclickable-wco/repro/result.txt`（还原态 `padding-right≈136px`、`titlebarAreaRect{w:1305}`、齿轮 `x=1273<1305`）。
- **母诊断病灶（drag 区吞洞，已被移除的机制）**：`docs/diagnoses/desktop-titlebar-buttons-unclickable-when-restored/repro/result.txt`（`headerRegion:"drag"`、还原 inset=0 / 最大化 inset=7）。
- **第 1 轮诊断（同族上轮，NC 不可 headless 之自陈、P2/P3 否决理由）**：`docs/diagnoses/desktop-header-app-controls-unclickable-wco/analysis.md`。
- **外部佐证**：Electron [#3022](https://github.com/electron/electron/issues/3022)/[#4139](https://github.com/electron/electron/issues/4139)（无边框顶部 drag 与顶边 resize 命中互扰）、[#30788](https://github.com/electron/electron/issues/30788)/[#48576](https://github.com/electron/electron/issues/48576)（`resizable:false`/`thickFrame:false` 回归，佐证 P3 否决）。

---

## 评审结论（Review Verdict）

- **评审节点**：Subtask #1（分析报告评审，本节点**不写修复代码、不 git commit、仅改 `analysis.md`**）
- **评审人**：资深评审（Anthropic 工程团队）
- **日期**：2026-07-09
- **一句话结论**：报告**代码引用逐条属实**，但**根因推断存在致命缺陷**——§3.1/§3.2/§3.5/§六 复活了上一轮已被证伪的「drag 覆盖」机制，并与自身 §2.2 直接矛盾。据本评审补入的**决定性证据（提交时序 + `5740adc` 前像 + 上一轮 RC-2 定论）**，**F1（仅内联）会复发**。**推荐修复改判为 F2（单行内联 + 还原态窗口层几何中和）**；F1 降级为「仅布局部分」、不构成完整修复。

### 一、决定性新证据（本评审补入，覆盖 §2.2 / §3.1 / §3.2 / §3.5 / §六）

**证据 1 —— 提交时序（`git log` 实证）：drag 覆盖早在报障前就已移除。**

```
8be958a 2026-06-28  fix: restore header right-cluster clicks and unblock live auto-trade toggle   ← 删中部拖拽面/完成反转契约
85b7d77 2026-06-29  fix: adopt Windows Controls Overlay for titlebar                                ← WCO 激活
   （上一轮 desktop-header-app-controls-unclickable-wco 报障发生于此区间：用户「三键正常」已证 WCO 已激活）
5740adc 2026-07-09  fix: sink Windows header app controls below the WCO caption band               ← round-1 修复（下沉两行）
```

即 `8be958a`（移除中部 drag 面、右簇变纯 no-drag 岛）**早于** `85b7d77`（WCO）**早于**上一轮报障。**上一轮 bug 发生在「drag 覆盖已被移除、WCO 已激活」的构建上，bug 仍然存在。** → §3.1「历轮真正吞点的机制是 drag 区覆盖控件，而该 drag 区早已被移除」在因果上**站不住**：drag 覆盖移除后 bug 并未消失，故它不是（至少不是唯一）机制。

**证据 2 —— `git show 5740adc` 前像：F1 目标结构 = 上一轮 buggy 结构，窗口层逐字同构。**

`5740adc` 的删除侧（即 pre-`5740adc` = 上一轮报障时的 DOM）为：

```jsx
<div className="drag-region …">{/* 品牌，唯一 drag 手柄 */}</div>
<div className="flex-1 self-stretch" />          {/* 中部：无 app-region 类 → no-drag */}
<div className="no-drag flex items-center gap-2"> {/* 右簇：内联 no-drag 岛 */}
  <AccountModeSwitch /> …
</div>
```

header 类含 `win-titlebar-safe`（本次 `5740adc` **未改**）。这与 **F1 的目标结构（单行·品牌唯一 drag·中部 no-drag·右簇内联 no-drag·`win-titlebar-safe` 让位）逐字一致**。且 `git show 5740adc --stat` = **仅 `apps/web/app/page.tsx` 1 文件**（`main.js` 窗口选项、`globals.css` 均未动）→ **F1 目标结构与上一轮 buggy 结构在窗口层完全同构**。F1 ≈ 裸 `git revert 5740adc`，二者仅差 `appControls` 抽为常量这一等价重构。

**证据 3 —— 上一轮 RC-2 定论（`docs/diagnoses/desktop-header-app-controls-unclickable-wco/analysis.md`，含其《评审结论》补强 A）：caption 带内单击被吞，与 app-region 无关。**

上一轮**原分析 + 其资深评审共同钉死**：WCO 把三键交系统（故三键可点），但**还原态 inset=0 + `WS_THICKFRAME` 顶边带 + WCO caption 矩形 NC 命中换算未归零**，导致**落在 48px caption 矩形内的应用控件，无论 DOM 标 `drag` 还是 `no-drag`，单击都被 OS 判作 caption 拖拽/顶边 resize、不下发到渲染层**；并明确「反转契约、`env` 让位等押注单一机理的 CSS/几何微调**历轮连败**」；「用户『三键正常』已闭合 RC-1（**确证跑的是 WCO 构建**、非旧包）」。

→ **三证合流的结论**：F1 的目标结构 = 上一轮已被**用户实测 + 评审定论**共同判为「还原态不可点」的结构，且**窗口层未变**。**F1 必然复发。**

### 二、根因证据审计（对本轮报告）

**1. 引用真实性——逐条复核，全部属实（问题不在引用真伪，而在因果推断）。**
- `page.tsx`：`:581` header `flex h-12 items-center`、`:592` 品牌唯一 `.drag-region`、`:602` 中部 `flex-1`（无 drag）、`:610` 右簇 `no-drag`、`:616-620` `winOverlay` 的 `y≥48` 工具条、`:545-548` `onTitleDoubleClick`——✔ 属实。
- `globals.css:137-147`（`.drag-region`=drag、其余 no-drag）、`:153-155`（`.win-titlebar-safe`）——✔。
- `main.js:214-263`（`opts` 未设 `resizable`→默认 `WS_THICKFRAME`；win 分支 `titleBarStyle:'hidden'`+`titleBarOverlay`）、`:218`（`TITLEBAR_OVERLAY_HEIGHT=48`）——✔。
- `WindowControls.tsx:27`（win32 返回 `null`）、`SettingsGearButton.tsx:23`（`h-8 w-8`）、`ui/index.tsx:24`（`size="sm"`=`px-2.5 py-1 text-xs`）、`AccountModeSwitch.tsx:104/127`（`px-3 py-1 text-xs`）——✔。
- 两处 `repro/result.txt`（母诊断 `headerRegion:"drag"`、`btnRect{y:8,h:32}`；WCO 探针还原 `inset=0`/最大化 `inset=7`、`padding-right≈136`、齿轮 `x=1273<1305`）——✔ 与文中引用一致。

**2. 致命缺陷 A（机理误判）。** §3.1/§3.2/§3.5 断言「吞点机制 = drag 覆盖，已移除 → 内联 no-drag 岛可点」。**证据 1 证明 drag 覆盖在 `8be958a` 即移除、早于报障，bug 却持续**；上一轮《评审结论》补强 A 已把机理**纠偏**为「WCO caption 矩形在还原态 NC 命中错位、与 app-region 无关」。本轮**复活了被证伪的机制**，并据此得出「内联即可点」的错误结论。

**3. 致命缺陷 B（自相矛盾）。** §2.2 判定裸 `git revert 5740adc`（→ pre-`5740adc` 单行 DOM）「**在当时那份构建上用户实测还原态不可点**→ 单纯回退 = 复发」；而 §3.2/§3.5/§六 判定**同一 DOM**（内联 no-drag 岛）「纯 HTCLIENT、还原态可点」。**证据 2 证明二者所指结构逐字相同**，结论却相反。报告以「构建身份混淆 RC-1」自我豁免此矛盾，但**上一轮已闭合 RC-1**（`三键正常` ⟹ 跑的确是 WCO 构建）——豁免不成立。

**4. 次要纠偏（不动结论，仅精度）。** §一.3 / §3.3 称控件「约落 y≈11..37、避开顶边 resize 带」；但**其自引的 WCO 探针实测齿轮 `y=8..40`**（`h-8`=32px 居中于 48 → 8..40），**齿轮顶边 `y=8` 紧贴顶边 resize 带**、几无余量。此点**不改变结论**——母诊断已实测「最大化态同一 `y=8` 几何可点」，证明顶边细带非主因、主因是整条 caption 带——但§3.3「避开顶边带」的措辞应改为「顶边带非主导机理」，避免误导下游以为控件已安全离带。此外，§3.5 引 VS Code「生产 WCO 应用可点标题栏控件」属实，但它恰恰**要求窗口层几何正确**才成立——这是 **F2 的成功前提、也是 F1 缺失的一环**，不能反证 F1。

### 三、候选修复审计

- **F1（仅内联 / 等价裸 `git revert 5740adc`）——否决为独立修复。** 与上一轮 buggy 结构**窗口层同构**（证据 1/2），且上一轮已用户实测 + 评审定论判为还原态不可点（证据 3）→ **必然复发**。用户已历多轮同族复发（见项目记忆「recurring Windows titlebar right-cluster caption bug」），不能再把整轮押在「上一轮报障系误报」这一已被上一轮显式排除的假设上。
- **F2（推荐）= 单行内联 + 还原态窗口层几何中和。** 关键论证：用户本轮硬性要求「与系统三键**同一行**、置于其**左侧**」，在几何上**强制**把控件锁进顶部 `[0,48)` caption 矩形内（「同一行」⟺「落在该矩形内」）；故上一轮 P1（下沉 `y≥48` 两行）正是**用户否决的形态**、不可复用。**唯一出路 = 让该 caption 矩形在还原态可派发客户单击**，即窗口层中和。须中和的机理（上一轮 §3.1+补强 A）：**还原态 inset=0 + `WS_THICKFRAME` 顶边 resize 带 + WCO caption 矩形 NC 命中换算未归零**。Subtask #2 的窗口层候选（**真机人工门逐一验证、取首个通过者、最小改动优先**）：
  1. **还原态帧内缩进对齐最大化态**（inset 0→与最大化等效对齐）：最大化态（inset=7）同结构已实证可点，令还原态复刻其 NC 命中归位；须解决「顶部与系统绘制的 WCO 三键错位」的视觉副作用。
  2. **顶边 NC 命中处置**：经自定义 hit-test（`win.hookWindowMessage`/`WM_NCHITTEST`）或收窄品牌 drag 手柄几何，把 48px 带内 no-drag 区在还原态强制判为 `HTCLIENT`，使单击下发。
  3. **兜底（仅当 1/2 全数证伪）**：Windows 改回原生边框 `frame:true`（上一轮 P2，代价 = 倒退自绘暖色顶栏，须另与产品确认），使 `<header>` 整体落到原生标题栏下方的客户区。
- **F3（现状两行）**：用户已否决——排除。
- **F4（裸 revert）**：= F1 的结构，复发——否决。
- **`resizable:false` / `thickFrame:false`（P3）**：连带禁用自定义拖拽（#30788）、破坏 resize（#48576）——除非 F2 全数失守方另议。

### 四、最终推荐修复（single recommended fix）

> **采 F2：把应用控件单行内联回 header（品牌 drag 簇 → 中部留白 → 应用控件 `no-drag` 簇 → 系统三键），删除 round-1 的 `y≥48` 工具条以满足「不换行 / 同一行 / 三键左侧」；同时在窗口层中和还原态 caption 矩形的 NC 命中错位，使 48px 带内 `no-drag` 控件在还原态可派发单击。** F1 的「仅内联」在窗口层不动的前提下**不足以修复**（证据 1/2/3 证其复发），**窗口层中和是本轮不可省的必要动作**；若真实 Windows 还原态人工门对某窗口层候选证伪，则切换下一候选，全数失守方退 F2-3（`frame:true` 兜底）。

**裁定理由（凝练）：**
1. **唯一同时满足用户三条硬需求（不换行 + 三键左侧同一行 + 可点）的方案。** 「同一行」把控件几何锁进 caption 矩形，故**必须让该矩形可点**——窗口层是唯一杠杆；下沉两行（P1）已被用户否决，纯 CSS 微调（F1/反转契约/env）上一轮连败且被探针证在 WCO 下仍不解决。
2. **F1 已被决定性证据证伪。** F1 目标结构与上一轮 buggy 结构窗口层逐字同构（证据 2）、drag 覆盖机制在报障前即消失（证据 1）、上一轮用户实测 + 评审定论判其还原态不可点（证据 3）。继续以 F1 为主修复 = 赌上一轮结论皆误，与历史复发代价不成比例。
3. **诚实残余不确定性。** OS 非客户命中在浏览器进程裁决、渲染层不可观测，`sendInputEvent` 绕过 NC 命中——**「还原态单击是否真正下发」不可 headless 验证**（历轮共同局限）。VS Code 存在可点 WCO 标题栏控件，说明**内联在窗口层正确时可点**——这正是 F2 的成功前提。故 F2 的窗口层候选必须以**真实 Windows 还原态人工点击**逐一门控。

**强制人工验收门（真机执行、两态齐验——历轮反复复发即因此门被跳过）：**
1. 以确定新鲜的构建**重启** `Aragon Trading.exe`（或 `desktop/` 下 `npm run dev`）。
2. **还原态**：顶栏为**单行**，应用控件在系统三键**左侧同一行**；单击「模拟盘/实盘」「暗色/亮色」「设置齿轮」**均下发生效**；系统三键仍可点；品牌簇仍可拖窗、双击仍可最大化。
3. **切最大化态**复验同批控件仍可点，**还原后再验一次**（闭合窗口态二分）；四边/四角 resize 正常。
4. 点「实盘」稳定切 live（分段高亮 + `ChartWorkspace` 渲染 `LiveBottomPanel`）、点「模拟盘」稳定切回；齿轮进/出设置中心正常。
5. mac 红绿灯、纯 Web `pnpm dev`、linux 均不回归。
6. **若步骤 2 的还原态单击对某窗口层候选证伪** → 换下一候选（F2-1→F2-2→F2-3），并把证伪结果回填本报告。

**评审签署**：本轮报告**代码引用属实、排除项（env/覆盖层/逻辑层）扎实**，但**根因推断有致命缺陷**（复活已证伪的「drag 覆盖」机制 + §2.2↔§3.2 自相矛盾）。据 `git` 提交时序 + `5740adc` 前像 + 上一轮 RC-2 定论，**唯一推荐修复 = F2（单行内联 + 还原态窗口层几何中和）**，F1 降级为「仅布局部分」。移交 Subtask #2 实现，以上述真机两态人工门为终审。

---

## 实施过程发现的方案缺陷（Issues Found During Implementation）

- **实施节点**：Subtask #2（代码修复与开发）
- **日期**：2026-07-09
- **已落地改动**：仅 `apps/web/app/page.tsx`——把应用控件（模拟盘/实盘、主题、设置齿轮、窗口键）**单行内联回 header**、置于系统三键左侧（`win-titlebar-safe` 让位内），并**删除** round-1 的 `y≥48` 两段式工具条。**满足本轮需求 1（不换行）与需求 2（同一行、三键左侧）**。未改 `globals.css` / `desktop/src/main.js`。

### 一、对《评审结论》F2「窗口层几何中和」候选的可实现性审计

评审改判推荐 F2 = 单行内联 + **还原态窗口层几何中和**，并列出候选（取首个通过真机人工门者、最小改动优先）。实施时逐一评估，发现**在本任务硬约束下四候选均无法直接落地**（约束：Task「Constraints」明列**不得引入新依赖 / 新文件**；用户需求 2 硬性要求控件与系统三键**同一行**）：

1. **F2-1 还原态帧内缩进对齐最大化态（inset 0→7）**：Electron 无公开 JS API 可令 `titleBarStyle:'hidden'` 的**可缩放**窗口在**还原态**获得非零客户区缩进——还原态 `getContentBounds==getBounds` 是无边框窗口的既定行为；最大化态那 7px 缩进由 Windows 对 `WS_THICKFRAME` 最大化窗口的自动越界定位产生，无法在还原态复制。评审自身亦标注其「与系统三键错位」的视觉副作用**未解**。→ **无干净实现路径**。
2. **F2-2a 自定义 WM_NCHITTEST（`win.hookWindowMessage`）**：该钩子是**通知型**，回调无法返回值改写 NCHITTEST 结果，故不能把 caption 命中强制改判为 HTCLIENT。要真正改写须原生 addon（WndProc 子类化）→ **触碰「不得引入新依赖」硬约束**。
3. **F2-2b 收窄品牌 drag 手柄使其不触顶边**：品牌簇在**最左**（x≈16..166），与**最右**应用控件簇不重叠（本报告 §3.2 已实测）；收窄它不改变右簇命中。其针对的是 Electron #3022「顶部 drag 区干扰顶边 resize」，症状是「顶边不可 resize」而非「右簇不可点」。→ **靶点与本症状不符**。
4. **F2-3 兜底 `frame:true`**：原生边框把系统三键放到**原生标题栏（独立一行）**、应用 header 落到其**下方客户区**——控件与三键**不再同一行**，**直接违背需求 2**，且倒退自绘暖色标题栏（须产品拍板）。→ **与本轮需求冲突**。

### 二、补充证据：F1 单行内联即标准 WCO，右簇本应为 HTCLIENT

- **全仓 app-region 审计（本节点实证）**：`apps/web` 内**唯一** `-webkit-app-region:drag` = `.drag-region`（`globals.css:137-138`），唯一挂载点是最左品牌簇（`page.tsx:592`）；header 本身、中部 `flex-1`、右簇均非 drag。右簇是「无任何 drag 区覆盖、显式 no-drag」的独立岛——**标准 WCO 语义下判为 HTCLIENT、可点**（VS Code 等生产 WCO 应用的标题栏控件在还原态即可点，反证「WCO caption 矩形在还原态吞掉一切 no-drag 单击」不可能普遍成立）。
- **几何事实**：控件在 48px 行内垂直居中（`h-8` 齿轮 y≈8..40），**已避开还原态顶边 resize 带（≈7–8px）**；故 P4 式「顶边安全缩进」在此**几何上无效**（控件本就不在带内），不能作为窗口层中和手段。
- **对「F1 必复发」的限定**：该结论的最强证据是 round-1 报障时的 pre-`5740adc` 构建；但评审据「三键正常⟹WCO 构建」闭合 RC-1，只证「**是** WCO 构建」，**未证「该次前端 bundle 已刷新」**——无法排除跑了未刷新的旧样式/旧包。

### 三、纠正后的实际实现（corrected approach）

- **采 F1 单行内联（= F2 去掉在本任务约束内不可落地的窗口层部分），刷新构建后交真机人工门终审。** 这是约束内**唯一同时满足需求 1/2、且以标准 WCO 语义给出需求 3 可点性**的最小改动。
- **残余风险与升级路径（务必带入 Subtask #3 与真机人工门）**：若真机**还原态**单击仍被证伪（坐实本机 WCO 标题栏带在还原态存在超出标准语义的整带 NC 缺陷），升级须**跳出本任务硬约束**、二选一且各需一次显式拍板：① 原生 NCHITTEST 子类化 addon（新增原生依赖 → 依赖/安全评审）；② Windows 改 `frame:true`（放弃「控件与三键同一行」的自绘标题栏形态 → 产品拍板）。
- **强制人工验收门不变**（见《评审结论》四）：真实 Windows **还原/最大化两态**逐一点「模拟盘/实盘」「暗色/亮色」「设置齿轮」，复验拖窗 / 双击最大化 / 四边 resize，及 mac 红绿灯、纯 Web、linux 不回归。
