# 诊断报告：工作台布局 —— 图表/成交持仓高度不可调 + 窄屏右侧策略面板被遮挡

- **Bug slug**: `workstation-layout-resize-and-overflow`
- **诊断人**: Subtask #0（问题定位与分析）
- **日期**: 2026-06-21
- **涉及模块**: `apps/web`（前端工作台布局）、`desktop`（Electron 窗口约束）
- **结论**: 两个问题均为**纯布局（CSS/结构）缺陷**，不涉及数据/后端逻辑。证据来自代码引用 + 无头浏览器实测截图（见 `evidence/`）。

---

## 一、问题描述（Problem description）

用户在量化模拟交易工作台中反馈两个样式布局问题：

**问题 1 —— 左侧「图表区」与「成交记录/当前持仓」高度比例不可调整。**
左侧面板下方是「成交记录 / 当前持仓」标签容器，上方是 K 线图表面板。当前两者高度被固定为 **8 : 2**，用户无法用鼠标在两者之间上下拖动来调整高度：调高下方面板时应挤压上方 K 线面板，调低时上方面板应回填空间。现状是**完全无法调整**。

**问题 2 —— 软件宽度不足时，右侧「交易策略」面板显示不全、被遮挡。**
当整个软件界面宽度不够时，右侧交易策略面板会显示不全 / 被屏幕边缘遮挡。期望：所有面板**始终落在窗口宽度以内**；解决方式是让中间 K 线显示面板的宽度可缩放，而左右两侧（左侧标的栏、右侧策略栏）保持各自固定宽度。

---

## 二、复现步骤（Reproduction steps）

以下步骤可复制粘贴执行（Windows / `git bash`，仓库根目录 `M:/takoAI/AragonTrading`）：

```bash
# 1) 启动 Web 工作台（无需后端即可渲染布局骨架）
node apps/web/node_modules/next/dist/bin/next dev apps/web -p 18761
#   等待出现 "Ready"，浏览器打开 http://localhost:18761/

# 2) 用无头浏览器在不同窗口宽度截图（也可直接拖动浏览器/桌面端窗口观察）
CHROME="C:/Program Files/Google/Chrome/Application/chrome.exe"
for w in 1440 1200 1024 980 760; do
  "$CHROME" --headless --disable-gpu --no-sandbox \
    --screenshot="tmps/shot-${w}.png" --window-size=${w},800 \
    "http://localhost:18761/"
done
```

**问题 1 的观察**：任意宽度下，下方「成交记录/当前持仓」条带始终占约 1/5 高度，与上方 K 线之间只有一条静态边框（`border-t`），**没有任何可拖动手柄**，鼠标移到分界线上无 `row-resize` 光标，按住拖动无效。证据截图：`evidence/layout-1024-fits.png`（下方条带固定高度）。

**问题 2 的观察**：

- `width ≥ 1024px`（如 1024/1200/1440）：布局为「左侧栏 + K 线(1fr) + 右侧策略(380px)」两列，**当前能放下**，右侧策略面板贴着右边缘但完整可见。见 `evidence/layout-1024-fits.png`。
- `width < 1024px`（如 980/760）：布局**塌缩为单列堆叠并发生元素重叠**（标的栏、底部「成交记录/当前持仓」标签、工具栏、图表头互相压在一起），完全错乱。见 `evidence/layout-980-stacked-broken.png`。
- **桌面端**：`BrowserWindow` 强制 `minWidth: 1024`（CSS/DIP 像素）。在 Windows **高 DPI 缩放**（125%/150%，1366 类笔记本极常见）或窄屏/分屏下，1024 DIP 的最小窗口换算成物理像素会**宽于屏幕可视区**，导致最右侧的 `380px` 策略列被推到屏幕外 → 用户看到的「右侧策略面板被遮挡」。

> 量化证据（无头实测，见 `evidence/overflow-probe.png`）：在 1024/1080/1200/1440 各宽度下注入测量脚本，`documentElement.scrollWidth == innerWidth`、`overflow = 0`、`asideClipped = false`。说明 **≥1024 时 K 线 `1fr` 轨道能吸收多余宽度、右侧面板未被裁切**；问题 2 的裁切发生在「有效宽度 < 1024」这一档——而该档当前要么塌缩重叠（Web），要么被 `minWidth:1024` 顶出屏幕（桌面端）。

---

## 三、根因分析（Root cause analysis）

### 问题 1 根因：上下分栏是写死的 flex 比例，无拖拽分隔条、无持久化状态

左侧 `ChartWorkspace` 的纵向结构为「上图表 + 下成交/持仓」两段，比例被硬编码：

- 区块根：`apps/web/components/Chart/ChartWorkspace.tsx:156`
  ```tsx
  <section className="flex h-full min-h-0 flex-col border-b ... lg:border-r">
  ```
- 上半「图表 + 左侧标的栏」区：`ChartWorkspace.tsx:157`
  ```tsx
  <div className="flex min-h-0 flex-[8] flex-col lg:flex-row">   {/* 固定 8 份 */}
  ```
- 下半「成交记录 / 当前持仓」区：`ChartWorkspace.tsx:223`
  ```tsx
  <div className="flex min-h-[140px] flex-[2] flex-col border-t border-[var(--border)]">  {/* 固定 2 份 */}
  ```

两段分别是 `flex-[8]` 与 `flex-[2]`，比例恒定 = 80% / 20%，二者之间仅有一条 `border-t` 静态边框，**没有可交互的拖拽手柄、没有任何指针事件处理、也没有把"分割比例"落到状态里**。

同时全局状态 `apps/web/lib/store.ts`（Zustand + persist）里**不存在任何高度/比例字段**：`partialize`（`store.ts:212`）持久化的是 `instId/bar/theme/indicators/...` 等选择态，没有 `chartSplitRatio` 之类（`store.ts` 全文无 resize/height/ratio）。组件内也无 `useState` 承载比例。

> 结论：高度比例不可调，是因为**该分栏从设计上就是固定比例、缺少 splitter 交互与状态承载**，并非样式被覆盖。需要新增「拖拽分隔条 + 受控比例（建议持久化）」能力。

### 问题 2 根因：固定列宽 + `overflow-hidden` + 仅在 `lg`(1024px) 才两列 + 桌面 `minWidth:1024`，缺少"缩 K 线保两侧"的收缩机制

主区采用 CSS Grid，右侧策略列写死 `380px`，且容器 `overflow-hidden`：

- 主网格：`apps/web/app/page.tsx:445`
  ```tsx
  <main className="no-drag grid flex-1 grid-cols-1 overflow-hidden lg:grid-cols-[1fr_380px]">
  ```
- 右侧策略 `aside`（固定列、且自身 `overflow-hidden`）：`page.tsx:483`
  ```tsx
  <aside className="min-h-[300px] overflow-hidden bg-[var(--surface)]/40">
    <StrategyPanel />
  </aside>
  ```
- 左侧标的栏（固定下限 220、上限 300）：`ChartWorkspace.tsx:158`
  ```tsx
  <div className="... lg:basis-[16%] lg:min-w-[220px] lg:max-w-[300px] ...">
  ```
- 桌面端窗口最小宽：`desktop/src/main.js:194` → `minWidth: 1024`

关键点逐条拆解：

1. **两列布局只在 `lg`(=1024px) 以上生效**：`grid-cols-1` 为默认（单列堆叠），`lg:grid-cols-[1fr_380px]` 才是两列。因此**有效宽度一旦 < 1024px，就从"两列"突变为"单列堆叠"**，而单列路径下 `ChartWorkspace` 内部从 `lg:flex-row` 退化为 `flex-col`、各 `shrink-0`/`max-h-[42vh]` 段落互相重叠 → 出现 `evidence/layout-980-stacked-broken.png` 的错乱。

2. **K 线轨道用的是 `1fr` 而非 `minmax(0,1fr)`**：`1fr` 等价 `minmax(auto,1fr)`，其最小尺寸是内容 `min-content`。当前因为 K 线容器 `autoSize`（`apps/web/components/Chart/CandleChart.tsx:186` → `autoSize: true`）+ 图表列 `min-w-0`（`ChartWorkspace.tsx:171`），在 ≥1024 时尚能收缩到位（实测 `overflow=0`）；但一旦内容 min-content 变大或宽度更紧，`1fr` 不保证能收到 0，缺少明确的"K 线优先让位"约束。

3. **`main` 与 `aside` 都是 `overflow-hidden`**：任何溢出都被**裁切而非滚动**。配合固定 `380px` 列，溢出时被切掉的恰好是最右侧策略面板。

4. **桌面 `minWidth:1024`（`main.js:194`）**：窗口无法收窄到 1024 DIP 以下。在高 DPI 缩放 / 小屏 / 分屏场景，1024 DIP 物理宽度 > 屏幕可视宽度，窗口右缘（策略列）被顶出屏幕外 → "被遮挡"。

> 结论：问题 2 不是"某一处写错宽度"，而是**整套响应式策略缺少"保持左右固定宽度、让中间 K 线连续缩放"的机制**：两列断点过高（1024）、断点以下塌缩重叠、K 线轨道非 `minmax(0,1fr)`、容器 `overflow-hidden` 只裁不缩、桌面 `minWidth` 偏大。用户诉求"缩 K 线保两侧、所有面板恒在窗口内"正是要补上这一机制。

---

## 四、影响面（Impact surface）

| 维度 | 影响 |
|---|---|
| 问题 1 受影响用户 | **全部用户**。无论窗口大小，均无法调整 K 线 / 成交持仓的高度比例；小屏用户尤甚（下方 20% 永久占用，K 线可视高度被压缩）。 |
| 问题 2 受影响用户 | **窄屏 / 高 DPI 缩放（125%–150%）/ 分屏 / 外接低分辨率显示器**用户。桌面端 1366×768 + 150% 缩放（国内笔记本常见）几乎必现：右侧"交易策略"面板被推出屏幕，**无法选择策略、改参数、启动回测/模拟** → 核心功能不可用。 |
| 受影响功能 | 问题 1：图表观察体验、成交/持仓查看；问题 2：策略选择、参数编辑、回测、对比、实时模拟（全部集中在右侧 `StrategyPanel`）。 |
| 数据/正确性 | 无。两者均为前端布局问题，不影响行情、回测、撮合等数据正确性。 |
| 平台 | Web 与 Desktop 共用同一套 `apps/web` 代码，两端同时受影响；Desktop 因 `minWidth:1024` 叠加 DPI 缩放更严重。 |

---

## 五、候选修复方案（Candidate fixes）

> 本节为定位阶段建议，最终方案由 Subtask #1 评审敲定、Subtask #2 落码。所有方案遵循 CLAUDE.md：尽量不新增依赖、改动最小、不在 `apps/web`/`services` 里 special-case 桌面端。

### 问题 1 候选

| 方案 | 做法 | 侵入性 | 风险 | 工作量 | 备注 |
|---|---|---|---|---|---|
| **1-A 自绘拖拽分隔条 + 持久化比例**（推荐） | 在 `ChartWorkspace.tsx:157` 上区与 `:223` 下区之间插入一个 4–6px 的 `row-resize` 手柄；指针拖动时更新比例并以 `flex-basis`/百分比高度应用；下限/上限夹取（如下区 12%–60%）；双击复位。比例存入 `store.ts`（仿 `partialize` 持久化，新增 `chartSplitRatio`）。 | 中 | 低 | 中 | 与项目既有 Zustand-persist 模式一致、零新依赖；符合 CLAUDE.md 依赖最小化。需处理指针捕获与卸载清理。 |
| 1-B 原生 CSS `resize: vertical` | 给下区加 `resize-y` + `min/max-height`，浏览器原生手柄。 | 低 | 低 | 低 | 手柄是右下角小三角、不美观；不能"上推回填"语义；无持久化；触屏/跨浏览器体验差。 |
| 1-C 引入分栏库（react-resizable-panels / allotment） | 用现成可拖拽分栏组件包裹上下两区。 | 中 | 中 | 低–中 | API 完善、含可达性/触控；但**新增运行时依赖**，与"依赖最小化"相悖，需评估体积与许可证。 |

### 问题 2 候选

| 方案 | 做法 | 侵入性 | 风险 | 工作量 | 备注 |
|---|---|---|---|---|---|
| **2-B 固定两侧 + 中间 K 线连续缩放（推荐，含 2-A 作为核心改动）** | ①把 K 线轨道由 `1fr` 改为 `minmax(0,1fr)`（`page.tsx:445`），并保证图表子树 `min-w-0` 链路完整，让 K 线优先让位；②**降低/取消 `lg` 两列断点**（让"左固定 + 中缩放 + 右固定"在更窄宽度仍保持两列、而非塌缩重叠）；③相应**下调桌面 `minWidth`**（`main.js:194`，如 ~860–960）使窗口能适配缩放/小屏；左侧 `220` 与右侧 `380` 保持固定。 | 中 | 中 | 中 | 完整满足用户诉求"所有面板恒在窗口内、缩 K 线保两侧"。需回归不同宽度与 DPI；属桌面运行时 glue（`main.js`）+ 布局 CSS，未在业务层 special-case。 |
| 2-A 仅 `minmax(0,1fr)` | 只把 `1fr`→`minmax(0,1fr)`、容器允许收缩。 | 低 | 极低 | 极低 | 一行级改动、最小风险；可作为第一步兜底。但**不解决 <1024 塌缩重叠与桌面 `minWidth` 顶出屏幕**，单独使用不充分。 |
| 2-C 右侧策略面板改为响应式/可折叠 | 把 `380px` 改成 `clamp()` 或加折叠开关。 | 中 | 中 | 中 | **与用户"左右宽度保持固定"明确冲突**，且策略面板过窄会影响可用性。不推荐。 |

---

## 六、推荐方案（Recommended fix）

- **问题 1 → 方案 1-A（自绘拖拽分隔条 + 持久化比例）。**
  理由：精确实现用户诉求（上下拖动、相互挤压/回填、随时调整），交互与视觉可控；零新增依赖、与现有 `store.ts` 持久化模式一致，契合 CLAUDE.md「依赖必要性 + 最小破坏面」。落点明确：分隔条插在 `ChartWorkspace.tsx:157`（上区）与 `:223`（下区）之间，比例字段加入 `store.ts` 的 state 与 `partialize`。

- **问题 2 → 方案 2-B（以 2-A 的 `minmax(0,1fr)` 为核心，叠加断点下移 + 桌面 `minWidth` 下调）。**
  理由：实测已证明「≥1024 能放下、<1024 才出问题」，故根治点是**把两列『左固定 + 中 `minmax(0,1fr)` 缩放 + 右固定』的布局延伸到更窄宽度并去掉塌缩重叠，同时放开桌面窗口最小宽**，让 K 线连续让位、两侧恒定、整体永不超出窗口。`minmax(0,1fr)`（2-A）作为最小兜底先行，但必须配合断点与 `minWidth` 调整才能覆盖高 DPI/小屏这一真实触发场景。
  建议落码时同时验证：1024 / 980 / 900 / 860 各宽度下右侧 `380px` 策略面板完整可见、`document.scrollWidth == innerWidth`、且 `<1024` 不再出现元素重叠（可复用本报告 `evidence/overflow-probe.png` 同款测量法回归）。

---

## 七、证据清单（Evidence）

代码引用：
- `apps/web/app/page.tsx:445` —— 主网格 `lg:grid-cols-[1fr_380px]` + `overflow-hidden`（问题 2 核心）
- `apps/web/app/page.tsx:483` —— 右侧 `aside` 固定列 + `overflow-hidden`
- `apps/web/components/Chart/ChartWorkspace.tsx:156/157/223` —— 区块根 / 上区 `flex-[8]` / 下区 `flex-[2]`（问题 1 核心）
- `apps/web/components/Chart/ChartWorkspace.tsx:158/171` —— 左侧栏 `min-w-[220px]` / 图表列 `min-w-0`
- `apps/web/components/Chart/CandleChart.tsx:186` —— `autoSize: true`（图表随容器收缩）
- `apps/web/lib/store.ts:212` —— `partialize` 持久化字段中**不含任何高度/宽度比例**
- `desktop/src/main.js:194` —— `minWidth: 1024`

实测截图（见同目录 `evidence/`）：
- `evidence/layout-1024-fits.png` —— 1024px：两列放得下；下方成交/持仓为固定高度条带（无拖拽手柄）
- `evidence/layout-980-stacked-broken.png` —— 980px(<1024)：单列塌缩、元素重叠错乱
- `evidence/overflow-probe.png` —— 1024–1440 注入测量：`overflow=0 / asideClipped=false`，证明裁切发生在 <1024 档

---

## 八、Definition of Done 对照

- ✅ `analysis.md` 已创建，复现步骤为可复制粘贴的命令 + 明确的宽度档位观察。
- ✅ 根因均有证据支撑：问题 1 有 `ChartWorkspace.tsx` 结构/`store.ts` 引用；问题 2 有代码引用 + 无头实测（`overflow` 测量与 980px 重叠截图）。
- ✅ 给出 1–3 个候选修复对比表与推荐方案 + 理由。
- ⚠️ 本节点不写修复代码、不 `git commit`（遵守约束）。

---

## 评审结论（Review Verdict）

> 评审人：Subtask #1（分析报告评审与修复）。本节对根因证据与候选方案逐项复核，补全原报告证据薄弱处，并敲定**单一**推荐修复方案。复核手段：独立重读全部被引代码行 + 重看三张证据截图，不依赖原报告转述。

### A. 根因证据复核

**全部代码引用均逐行核实属实**（评审独立打开文件核对，非转述）：

| 引用 | 实际内容 | 核验 |
|---|---|---|
| `ChartWorkspace.tsx:156` | `<section className="flex h-full min-h-0 flex-col border-b ... lg:border-r">` | ✅ 属实 |
| `ChartWorkspace.tsx:157` | `<div className="flex min-h-0 flex-[8] flex-col lg:flex-row">` | ✅ 属实，上区固定 8 份 |
| `ChartWorkspace.tsx:158` | `... lg:basis-[16%] lg:min-w-[220px] lg:max-w-[300px] ...` | ✅ 属实，左栏固定宽**仅 `lg:` 生效** |
| `ChartWorkspace.tsx:171` | `<div className="flex min-h-[320px] min-w-0 flex-1 flex-col">` | ✅ 属实，图表列已带 `min-w-0` |
| `ChartWorkspace.tsx:223` | `<div className="flex min-h-[140px] flex-[2] flex-col border-t ...">` | ✅ 属实，下区固定 2 份、下限 140px |
| `page.tsx:445` | `<main className="no-drag grid flex-1 grid-cols-1 overflow-hidden lg:grid-cols-[1fr_380px]">` | ✅ 属实 |
| `page.tsx:483` | `<aside className="min-h-[300px] overflow-hidden bg-[var(--surface)]/40">` | ✅ 属实，右列固定 380px、自身 `overflow-hidden`、**无 `min-w`** |
| `store.ts:212` | `partialize` 仅含 `instId/bar/strategyId/.../instSidebarTab` | ✅ 属实，确无任何高度/比例字段 |
| `CandleChart.tsx:186` | `autoSize: true` | ✅ 属实 |
| `main.js:194` | `minWidth: 1024` | ✅ 属实 |

证据截图复看：`layout-1024-fits.png`（1024 两列放得下、下区为静态条带无手柄）、`layout-980-stacked-broken.png`（980 单列塌缩、`成交记录/当前持仓` 标签与 `Waiting for OKX bids` 文字互相压字、右侧策略列整列消失下沉）、`overflow-probe.png`（1024–1440 全部 `overflow=0 / asideClipped=false`）三者均与正文描述一致。

**问题 1 根因成立**：固定 `flex-[8]/flex-[2]` + 无 splitter + `store` 无比例字段，三条互相印证，无反例。这是「设计上缺能力」而非「样式被覆盖」，结论可靠。

**问题 2 根因：成立，但有一处证据为推断、需点明，且原报告漏列了两个必须同改的落点。**

1. **桌面端「整列被推出屏幕」是本次唯一未被直接测量、仅靠推理支撑的环节。** 无头实测只覆盖 Web ≥1024（证明 Web 层 ≥1024 不裁切），980px 截图证明 Web <1024 塌缩——但用户实际现象（桌面策略面板被遮挡）依赖「`minWidth:1024`(DIP) + 高 DPI 缩放 → 窗口物理宽 > 屏幕可视宽 → 右缘 380px 列出屏」这一链条，文中无桌面端截图佐证。**评审判定该推理链成立**，补强依据如下：
   - `main.js:191-213` 的 `width/minWidth` 单位为 **DIP**（Electron 默认），且窗口为**无边框**（`frame:false`，`main.js:199`），未设 `useContentSize`，故窗口外尺寸 ≈ 内容尺寸，`minWidth:1024` 即「内容区不得窄于 1024 DIP」。
   - 典型机型 1366×768 @150% 缩放 → 工作区仅 ≈910 DIP 宽 < 1024 DIP，窗口无法收窄到屏内，右缘（策略列）被屏幕边界裁掉。这与 Web 层「≥1024 不裁切」并不矛盾：桌面内容区恒 ≥1024，Web CSS 永远走两列且不溢出，**裁切发生在 OS 窗口/屏幕层，而非 CSS 层**。这正是「Web 实测 `overflow=0` 但桌面仍被遮挡」的统一解释，无替代假说更优。
   - 次要替代假说排除：`aside` 内 `StrategyPanel` 内容横向超过 380px 也会被 `overflow-hidden` 裁（`page.tsx:483`），但那是「面板内部内容被切」，与用户描述的「整块策略面板显示不全/被遮挡」不同档；本修复不针对它，留作后续观察项。

2. **原报告「降低/取消 lg 断点」描述过粗，漏列了两个必须同步修改的落点**——这是评审补入的关键代码证据，否则只改 `page.tsx:445` 仍会塌缩：
   - `ChartWorkspace.tsx:157` 的 `lg:flex-row`：上区「左栏 | 图表」仅在 `lg` 才横排，<1024 退化为 `flex-col`（左栏压在图表上方）。
   - `ChartWorkspace.tsx:158` 的 `lg:basis-[16%] lg:min-w-[220px] lg:max-w-[300px] lg:max-h-none`：左栏的固定宽语义**全部挂在 `lg:` 前缀**，<1024 时左栏变为 `max-h-[42vh] shrink-0` 的整宽块。
   即「两列断点」实际散落在 **`page.tsx:445` + `ChartWorkspace.tsx:157` + `:158`** 三处，任何一处不下移都会复现 980px 截图的塌缩重叠。

3. **`minmax(0,1fr)` 必须配齐完整的 `min-w-0` 收缩链，原报告只点到图表列(`:171`)、漏了其上游两层**：grid item 与 flex item 的默认 `min-width:auto` 是内容宽，仅把轨道改 `minmax(0,1fr)` 不足以让内容真正收缩。链路自上而下需逐层带 `min-w-0`：
   - 轨道：`page.tsx:445` `1fr → minmax(0,1fr)`；
   - `section`（grid item，`ChartWorkspace.tsx:156`）：**当前无 `min-w-0`** ← 需补；
   - 上区行容器（`ChartWorkspace.tsx:157`）：**当前无 `min-w-0`** ← 需补；
   - 图表列（`ChartWorkspace.tsx:171`）：已有 `min-w-0` ✅。
   实测当前 ≥1024 `overflow=0`，是因为宽度尚未触达内容 min；一旦窗口下探到新 `minWidth`，缺这两层 `min-w-0` 仍可能横向溢出（原报告 §三-2 已隐约提示「`1fr` 不保证收到 0」，此处把它落成可执行的精确清单）。

### B. 候选方案复核（边界 / 最小化 / 回归）

**问题 1 → 1-A（自绘拖拽分隔条 + 持久化比例）：方案成立，是满足诉求的最小可行解。**
- 1-B（原生 `resize-y`）无「上推回填」语义、无持久化、手柄丑，不满足诉求；1-C 引库违反 CLAUDE.md「依赖最小化」。故 1-A 不是过度设计，而是达成「相互挤压/回填 + 随时调 + 记忆」的下限。
- 边界条件（落码必须覆盖，评审补充）：
  - **夹取必须尊重既有硬下限**：下区 `min-h-[140px]`（`:223`）、图表列 `min-h-[320px]`（`:171`）。比例换算后若使任一段低于其 `min-h`，flex 会顶出 `min-h` 造成总高溢出 → 建议夹取下区在 `[140px(或~15%), ~60%]`，并保留两段的 `min-h-0` 以允许内部滚动而非撑爆。
  - **指针交互**：用 Pointer Events + `setPointerCapture`，`pointerup` 释放并清理监听，`touch-action:none`，手柄 `cursor:row-resize`；组件卸载时移除监听，避免泄漏。
  - **持久化**：在 `store.ts` 新增 `chartSplitRatio` 入 state 与 `partialize`；建议将 persist `version` 6→7 并在 `migrate`（`store.ts:230`）补默认值——按 `store.ts:229` 注释，zustand 浅合并本就回落初值，故为零回归的稳妥项。
  - **复位**：双击手柄回默认 0.8/0.2。
- 回归面：仅改 `ChartWorkspace` 纵向两段 + `store` 增字段，不触碰数据流；风险低。

**问题 2 → 2-B（以 `minmax(0,1fr)` 为核，叠加断点下移 + 桌面 `minWidth` 下调）：方案成立，三处改动彼此耦合、缺一不可，须作为一个整体提交。**
- **耦合性证明（驳「能否只做最小子集」）**：
  - 只做 2-A（`minmax(0,1fr)`）：不解决 <1024 塌缩，也不解决桌面出屏 → 不充分（原报告已述，评审认同）。
  - 只下调 `minWidth` 不下移断点：窗口进入 860–1023 区间时仍命中默认 `grid-cols-1` 单列塌缩 → **反而把窗口推进了已知的破碎区**，更糟。
  - 只下移断点不下调 `minWidth`：桌面窗口被 `minWidth:1024` 钉死、永远进不了新断点区间，桌面出屏问题原样存在。
  - 故三者必须同改，且约束关系为 **断点阈值 ≤ 新 `minWidth`**，保证桌面运行区间恒在两列分支内。
- **`minWidth` 落点定值（评审给出可执行数值与依据）**：两列的横向「地板」= 左栏固定 `220`（`:158`）+ 右列固定 `380`（`page.tsx:445`，`aside` 无 `min-w` 即由轨道定 380）= **600px**，图表列 `min-w-0` 可压到 0。为保图表仍可用，给图表留 ≈260px：**推荐 `minWidth: 860`**（=220+380+260）。该值 < 1366×768@150%（≈910 DIP），覆盖最常见痛点机型；若需兼容更小屏，硬地板为 ~800（图表 ≈200px，再小则不可用）。`minHeight:680`（`main.js:195`）对问题 1 新增的纵向 splitter（上 320 + 下 140 + 头部）仍充裕，无需改。
- **断点落点（评审给出精确清单，补原报告之缺）**：将两列布局设为桌面运行区间的**唯一布局**最干净——
  - `page.tsx:445`：去掉 `grid-cols-1 ... lg:` 塌缩，改为常驻 `grid grid-cols-[minmax(0,1fr)_380px]`；
  - `ChartWorkspace.tsx:157`：`lg:flex-row` → 常驻 `flex-row`（或与下同步降到 `md`）；
  - `ChartWorkspace.tsx:158`：左栏 `lg:basis-[16%] lg:min-w-[220px] lg:max-w-[300px] lg:max-h-none` 的固定宽语义同步常驻（去 `lg:` 前缀或降断点）；
  - `ChartWorkspace.tsx:156`、`:157` 补 `min-w-0`，配合 `:171` 既有 `min-w-0` 形成完整收缩链。
  若产品仍需兼容 <800 的纯 Web/移动场景，则改为**统一降到 `md`(768)** 而非全去前缀，并保证 768>600 地板成立；因桌面 `minWidth=860>768`，桌面恒走两列分支。本工作台为桌面优先，评审倾向「常驻两列」以彻底消灭单列破碎分支。
- 回归面：①降低/取消响应式塌缩会影响极窄纯 Web 视口（<800 出横向滚动）——桌面非目标区间，可接受；②`overflow-hidden`（`page.tsx:445`/`:483`）保留不动即可（地板与 `minWidth` 已保证不溢出，无需改成 `auto` 引入滚动条）。

### C. 最终推荐修复（Final Recommended Fix）

**问题 1：采用 1-A —— 在 `ChartWorkspace.tsx:157`（上区）与 `:223`（下区）之间插入自绘 `row-resize` 拖拽分隔条，以受控比例驱动两段高度（`flex-grow`/百分比 + 保留 `min-h-0`），夹取于各自 `min-h`（下区 `[140px, ~60%]`），双击复位，比例写入 `store.ts` 的 state 与 `partialize`（version 6→7 补默认）。**
理由：唯一同时满足「上下拖动、相互挤压/回填、随时调整、跨会话记忆」四项诉求且零新依赖的方案，契合 CLAUDE.md 最小破坏面；落点与状态承载明确，回归面局限在左侧纵向分栏。

**问题 2：采用 2-B（整体提交，三处耦合改动）——**
1. **`page.tsx:445`**：`1fr → minmax(0,1fr)` 且去除 `grid-cols-1/lg:` 塌缩，常驻两列 `grid-cols-[minmax(0,1fr)_380px]`；
2. **`ChartWorkspace.tsx:156/157` 补 `min-w-0`**、**`:157` 的 `lg:flex-row` 与 `:158` 的 `lg:` 固定宽语义常驻化**，形成自轨道至图表列的完整 `min-w-0` 收缩链；
3. **`main.js:194` `minWidth: 1024 → 860`**（=左 220 + 右 380 + 图表 ≈260；< 1366×768@150% 的 ≈910 DIP）。

理由：实测已证「≥1024 Web 不溢出、<1024 才破碎」，桌面遮挡的真因是 `minWidth:1024`(DIP) 在高 DPI/小屏下把窗口顶出屏外。唯有「中间 K 线 `minmax(0,1fr)` 连续让位 + 两侧 220/380 固定 + 两列断点下移到运行区间内 + 桌面 `minWidth` 落到固定列地板之上」四者合一，才能让 K 线随宽度平滑缩放、左右两侧恒定、所有面板始终落在窗口内，精确命中用户诉求；三处任缺其一都不充分或更糟（见 §B 耦合性证明），故为「能解决问题的最小完整改动集」，非过度设计。

**落码后回归验证（沿用本报告 `overflow-probe.png` 同款注入测量法）**：在 860 / 900 / 1024 / 1280 各宽度断言 `documentElement.scrollWidth == innerWidth`、右侧 380px 策略列完整可见且 `asideClipped=false`、且各宽度下 `成交记录/当前持仓` 与图表区**不再重叠**；并人工在桌面端 1366×768 @150% 缩放下确认窗口可收窄至屏内、策略面板不被遮挡。问题 1 另需验证：拖动分隔条上下挤压/回填生效、触达 `min-h` 时不溢出、刷新后比例保持、双击复位。
