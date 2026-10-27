# 实盘「买多 / 买空」下单面板布局混乱 — 问题定位与分析

- **Bug slug**: `live-long-short-order-panel-layout`
- **诊断节点**: Subtask #0（问题定位与分析）
- **日期**: 2026-06-27
- **结论一句话**: 实盘下单面板的"乱"由两层原因叠加 —— ① 共享 `Slider` 原语缺少滑块拇指（thumb）样式与轨道横向内缩，导致数量滑块在 `value=0`（默认空仓）时橙色拇指**溢出到面板左边缘外**（截图里最刺眼的"卡在最左的小圆点"）；② 面板把**多种交互范式 + 多种分段样式**平铺在一个无分组的 `flex-col` 里，相邻的杠杆 / 数量两个同类控件结构不一致，整体不工整。

---

## 1. 问题描述（Problem description）

用户在**实盘模式**下查看「买多 · 开多 / 买空 · 开空」下单面板（`accountMode==="live"` 时图表下方左栏），反馈"布局有问题，内部混乱，需要修复，工整、合理、符合人机交互最佳实践"。

随任务附带的截图（`.agentmesh/attachments/paste-1782585588200.png`）中可见的具体异常：

| # | 现象 | 直观感受 |
|---|---|---|
| P1 | **滑块拇指溢出左边缘**：数量滑块在数量为 0 时，橙色圆形拖拽点紧贴并溢出面板最左侧，像一个被裁切的"孤立小圆点"；同版本下杠杆滑块也有同样表现 | 最刺眼，像渲染坏了 |
| P2 | **杠杆与数量两个数值控件范式不一致**：一个用滑块、一个（新版）用档位按钮，且数量区把"滑块 / 输入框 / 百分比按钮 / 指标盒"摞成 3~4 行 | 同类控件长得不一样，凌乱 |
| P3 | **面板扁平、无分组**：价格、杠杆、数量、止盈止损平铺在同一缩进、同一间距下，数量区独占多行，节奏失衡 | 拥挤、没有层次 |
| P4 | **同屏出现 4~5 种"分段 / 档位"样式**（方向切换、限价/市价、全仓/逐仓、杠杆档位、数量百分比、止盈止损模式页签），风格各异 | 视觉不统一 |
| P5 | **校验错误远离对应输入框**："数量需 ≥ 最小下单量 0.01 张"被百分比按钮与指标盒挤到下方，离数量输入框很远 | 不易关联 |

> ⚠️ **版本说明（重要）**：附带截图中"杠杆"一行显示的是**滑块**；而当前已提交源码（commit `4f72c39`，编译产物 `.next` BUILD 时间 2026-06-26）里 `LeverageControl` 已改为**档位按钮**（详见 §3 证据 E3）。即截图来自杠杆尚为滑块的**较早构建**。但**数量区（`QuantitySlider`）至今仍在用同一个有缺陷的 `Slider` 原语**，故 P1 在当前源码中依然存在、可复现（见 §2）。本报告以**当前源码**为准给出根因与修复方案。

---

## 2. 复现步骤（Reproduction steps）

### 2.1 真机路径（实盘面板，需就绪账户，成本高）

1. 后端 `LIVE_TRADING_ENABLED=true`，并配置一个"就绪默认账户"（`hasSecret && hasPassphrase`）。
2. 前端 Header 经 `AccountModeSwitch` 切到**实盘**（`accountMode==="live"`），图表下方渲染 `LiveBottomPanel`（`apps/web/components/Chart/ChartWorkspace.tsx:305-307`）。
3. 观察左栏下单面板：数量未输入（`sz=0`）时，数量滑块橙色拇指紧贴并溢出面板左边缘。

> 该路径被"就绪账户 + 实盘开关"双重守门，且会触达真实 OKX 账户（详见 `docs/plans/live-trading-mode/spec.md`、记忆 `aragon-live-trading-mode`），不适合作为复现 UI 渲染缺陷的最小手段。下面给出**零依赖最小复现**。

### 2.2 最小复现（推荐，零依赖、可复制粘贴）

把当前 `Slider` 原语的真实类名（`apps/web/components/ui/index.tsx:226-230`）与编译后的 `.appearance-none` 规则原样还原为一段静态 HTML，用无头 Chrome 截图即可稳定复现 P1：

仓库内已附复现文件：

- `docs/diagnoses/live-long-short-order-panel-layout/slider-repro.html`（精确还原 Slider 标签与编译 CSS，并放了一个"已修复"对照）
- `docs/diagnoses/live-long-short-order-panel-layout/slider-repro.png`（截图结果）

复现命令（Windows，已验证可跑）：

```bash
"/c/Program Files/Google/Chrome/Application/chrome.exe" \
  --headless=new --disable-gpu --hide-scrollbars --force-color-profile=srgb \
  --window-size=460,260 \
  --screenshot="docs/diagnoses/live-long-short-order-panel-layout/slider-repro.png" \
  "docs/diagnoses/live-long-short-order-panel-layout/slider-repro.html"
```

**观察结果**（见 `slider-repro.png`）：

- `value=0`：橙色拇指圆心落在轨道最左端，左半个圆**溢出控件 content-box 之外**，紧贴/越过容器左边缘 —— 与用户截图完全一致。
- `value=40`：拇指居中、正常。
- 对照行（补了 `::-webkit-slider-thumb` 样式）：拇指大小可控、不再贴边异常。

> 复现同时**修正了一个初始假说**：原以为 `appearance:none` 会让拇指"完全不可见"；实测当前 Chrome 下 `accent-color` 仍能给原生拇指上色，拇指**可见**，真正的缺陷是**端点溢出**而非不可见。这条结论以截图为准，而非主观推断。

---

## 3. 根因分析（Root cause analysis）

### 主因 A — 共享 `Slider` 原语缺拇指样式与轨道内缩（对应 P1）

`Slider` 是一个裸 `<input type="range">`，只设了轨道底色与 `accent-color`，**没有为 `::-webkit-slider-thumb` / `::-moz-range-thumb` 定义任何尺寸 / 形状，也没有给轨道预留横向内缩（padding / margin）**：

```tsx
// apps/web/components/ui/index.tsx:212-232
<input
  type="range"
  ...
  className={clsx(
    "h-1.5 w-full cursor-pointer appearance-none rounded-full bg-[var(--surface-2)]",
    "accent-[var(--accent)] disabled:cursor-not-allowed disabled:opacity-40",
    className,
  )}
/>
```

- 原生 range 控件在 `value=min`（或 `max`）时，拇指**圆心**对齐轨道端点，于是拇指半径会越过控件 content-box 的左 / 右边界。浏览器对原生外观通常会用内部 padding 兜住，但这里既 `appearance-none` 又**未补任何拇指 / 轨道几何**，端点溢出无人约束。
- 调用处该 `<input>` 作为 `flex-1` 直接紧贴面板 `p-3` 左内边距，没有任何左侧留白：

```tsx
// apps/web/components/Live/QuantitySlider.tsx:59-69
<div className="flex items-center gap-2">
  <Slider value={pct} min={0} max={100} step={1} ... className="flex-1" />
  <Input ... className="w-20 text-right tabular" />
</div>
```

- 默认进入面板时数量为 0（`LiveTradePanel.tsx:77` `const [sz, setSz] = useState(0)`，`pct = sizeToSliderPct(0, maxSz) = 0`），所以**一进面板拇指就贴在最左、左半溢出到面板边缘** —— 即截图 P1。

**证据 E1（拇指样式缺失，编译产物佐证）**：在编译输出里全局检索，`.appearance-none` 规则存在，但**没有任何 `::-webkit-slider-thumb` / `range-thumb` 规则**：

```
# .next 编译 CSS
.appearance-none{-webkit-appearance:none;-moz-appearance:none;appearance:none}
# grep -r "slider-thumb|range-thumb|::-webkit-slider"  apps/web/.next/static/css/  → 0 命中
```

`apps/web/app/globals.css` 全文也无任何 range thumb 规则（已通读，见文件 1-114 行）。

**证据 E2（最小复现截图）**：`slider-repro.png` 中 `value=0` 行拇指溢出左边缘，与用户截图一致；补 thumb 样式的对照行正常。

### 主因 B — 面板范式 / 样式不统一 + 扁平无分组（对应 P2 / P3 / P4 / P5）

下单面板根节点是一个扁平的 `flex flex-col gap-3 p-3`，按顺序平铺 9 个异构块，无任何分组 / 分隔，也无统一的"区"概念：

```tsx
// apps/web/components/Live/LiveTradePanel.tsx:206-294（结构摘要）
<div className="flex flex-col gap-3 p-3">
  {blockedReason && <p>…</p>}
  <DirectionToggle … />                 {/* 范式①：填充式分段，选中=色块 */}
  <div className="flex items-center gap-2">
    <SegToggle … />  {/* 限价/市价  范式②：surface 填充+阴影 */}
    <SegToggle … />  {/* 全仓/逐仓 */}
  </div>
  <label>委托价格…<Input/></label>
  <LeverageControl … />                 {/* 范式③：档位“描边 chip”+输入框（同一行）*/}
  <QuantitySlider … />                  {/* 滑块+输入框 / 范式④：百分比“描边 chip”/指标盒（多行）*/}
  {validation.errors.sz && <span>…</span>}  {/* P5：sz 错误渲染在 QuantitySlider 之后 */}
  <TpSlFields … />                       {/* 范式⑤：ModeTab 页签 */}
  <Button>买入开多 / 卖出开空</Button>
</div>
```

具体不一致点（每条都可在代码定位）：

- **P2 范式不一致**：相邻的两个"数值控件"——
  - 杠杆：`grid grid-cols-5` 档位按钮 **+** 输入框，**同一行**（`LeverageControl.tsx:32-63`）。
  - 数量：滑块 + 输入框（一行）**＋** `grid grid-cols-4` 百分比按钮（另一行）**＋** `grid grid-cols-3` 指标盒（再一行）（`QuantitySlider.tsx:59-104`）。
  两者本应同构，却一个一行、一个三四行，且一个用档位、一个用滑块。
- **P4 样式不统一**：同屏至少 5 种"分段 / 档位 / 页签"样式：
  - `DirectionToggle`：`bg-[--surface-2] p-1`，选中=语义色填充（`DirectionToggle.tsx:55-71`）。
  - `SegToggle`（限价/市价、全仓/逐仓）：`bg-[--surface-2] p-1`，选中=`surface` 填充+阴影（`LiveTradePanel.tsx:348-368`）。
  - 杠杆档位：`border` 描边 chip，选中=`accent` 描边 + `accent-soft` 底（`LeverageControl.tsx:40-49`）。
  - 数量百分比：`border` 描边 chip，**无选中态**（`QuantitySlider.tsx:88-96`）。
  - 止盈止损 `ModeTab`：`surface-2` 填充页签（`TpSlFields.tsx:96-110`）。
- **P3 节奏失衡**：统一 `gap-3` 下，数量区独占 3~4 行、其余 1~2 行，且无分隔线把"下单参数 / 仓位 / 风险(止盈止损)"分区。
- **P5 错误离位**：`validation.errors.sz` 由父组件渲染在 `<QuantitySlider>` **之后**（`LiveTradePanel.tsx:278-280`），被百分比按钮 + 指标盒隔开，远离数量输入框；而价格错误是贴着价格输入框的（`LiveTradePanel.tsx:250-252`），两者处理方式也不一致。

**证据 E3（杠杆当前已是档位按钮，非滑块；佐证截图来自较早构建、也佐证"团队已对滑块不满并替换过一次"）**：当前编译产物渲染杠杆为档位按钮：

```js
// apps/web/.next/static/chunks/app/page-3635e449131c0afa.js（"杠杆倍率"附近）
… "上限 ",Math.floor(a),"×" … className:"grid flex-1 grid-cols-5 gap-1",
  children: et.filter(e=>e<=Math.floor(a)).map(e=>("button", … e+"×"))
```

而**数量区仍渲染 `Slider`**（同一构建）：

```js
// 同文件，"仓位数量（张）"附近
… (Slider){value:a>0?Math.min(Math.max(t/a*100,0),100):0,min:0,max:100,step:1, … ariaLabel:"仓位数量百分比", className:"flex-1"} …
```

> 解读：杠杆从"滑块"换成"档位按钮"很可能正是为规避同一个 `Slider` 缺陷的一次局部修补，但**数量区未同步**，于是留下了"两个同类控件范式割裂 + 数量滑块仍坏"的不一致状态 —— 这正是"内部混乱"的来源。

---

## 4. 影响面（Impact surface）

- **触发条件**：仅 `accountMode==="live"`（实盘）时渲染的 `LiveBottomPanel` 左栏下单面板（`ChartWorkspace.tsx:305-307`）。模拟 / 回测路径不渲染该面板，不受影响。
- **受影响用户**：所有进入实盘下单面板的用户（开关开启 + 就绪默认账户）。属于**真金白银**界面，凌乱布局会抬高误操作风险，体验影响权重高于普通页面。
- **`Slider` 原语的爆炸半径很小**：全仓检索，`Slider` 原语仅被 `QuantitySlider` 使用（`apps/web/components/Live/QuantitySlider.tsx` 及其测试），杠杆已不再使用它。故"给 `Slider` 补拇指样式"或"在 `QuantitySlider` 内改用百分比 chip"两种方案的回归面都被限制在实盘数量控件，不波及其它页面。
- **现有测试约束**：`QuantitySlider.test.tsx` 依赖 `getByLabelText("仓位数量百分比")`（即 `<input type=range>` 存在）做滑块↔尺寸联动断言。**若方案选择"移除滑块"**，需同步更新该测试（见 §5 风险列）。

---

## 5. 候选修复方案（Candidate fixes）

> 本节为分析建议，**本诊断节点不写修复代码**；落地由 Subtask #2 依推荐方案实施。

| 方案 | 做法 | 侵入性 | 风险 | 工作量 | 是否解决 P1~P5 |
|---|---|---|---|---|---|
| **F1：给 `Slider` 原语补拇指/轨道样式（最小修 P1）** | 在 `globals.css` 增加 `input[type=range]::-webkit-slider-thumb`/`::-moz-range-thumb` 几何与配色，并给轨道留横向内缩（或在 `Slider` 容器加 `px`/`mx` 抵消拇指半径，使端点不溢出） | 低（仅原语 + 全局 CSS） | 低（仅影响 `QuantitySlider` 一处） | 小 | 只解决 P1；P2~P5 仍在 |
| **F2：重排面板布局 + 统一控件范式（治本）** | ①数量区改为与杠杆**同构**：百分比"档位 chip（带选中态）+ 输入框"同一行，**移除**坏滑块（或保留并按 F1 修好作为可选）；②抽一个统一的 `SegChip`/`PresetChip` 风格收敛 P4 的多种 chip；③用分区（"下单参数 / 仓位 / 止盈止损"）+ 细分隔提升层次；④把 `sz` 错误移到紧邻数量输入框处 | 中（动 `QuantitySlider`/`LiveTradePanel`，可选抽取 1 个小组件） | 中（需同步 `QuantitySlider.test.tsx`；纯展示层，无业务逻辑改动） | 中 | 解决 P1~P5 |
| **F3：仅补 P1 + 局部理顺（折中）** | F1 修好滑块拇指 **＋** 把 `sz` 错误移到数量输入框下方（P5）＋ 给数量百分比 chip 补选中态以对齐杠杆（缓解 P2/P4），不做整体分区 | 中低 | 低~中 | 中小 | 解决 P1/P5，缓解 P2/P4，P3 仍在 |

补充说明：

- 三个方案都**不触碰** `lib/liveOrder.ts` 的换算逻辑与下单链路，纯展示层。
- F1 是任何方案的"地基"：即使选 F2 决定保留滑块，也要先把拇指几何补齐；若 F2 决定移除滑块，则 F1 对 `Slider` 原语的修补可不做（但建议仍补，避免该原语日后被复用时再踩坑）。

---

## 6. 推荐方案（Recommended fix）

**推荐 F2（重排布局 + 统一范式）为目标态，并以 F1 作为其内部必做的第一步。**

理由：

1. **对症**：用户诉求是"布局混乱、要工整、要符合人机交互最佳实践"，本质是 P2/P3/P4 的**结构性不一致**，单修 P1（F1）只去掉最刺眼的溢出点，"乱"仍在，无法满足验收语义。
2. **风险可控**：改动局限在实盘数量控件与下单面板这两个文件（必要时多抽一个 ~30 行的 chip 组件），不碰业务逻辑与下单链路；`Slider` 原语仅 `QuantitySlider` 使用，回归面小。
3. **与既有演进方向一致**：团队已把杠杆从滑块改为"档位 chip + 输入框"（§3 证据 E3）；把数量也收敛成同构形态，是顺势补齐一致性，而非另起炉灶。
4. **测试代价明确**：唯一需要同步的是 `QuantitySlider.test.tsx`（若移除滑块，则把"仓位数量百分比"用例改为点击百分比 chip 断言尺寸联动；其余用例不变）。

**给 Subtask #2 的落地要点（建议，不强制实现细节）**：

- **P1（必做）**：要么在 `globals.css` 为 `input[type=range]` 补 `::-webkit-slider-thumb`/`::-moz-range-thumb`（含宽高、圆角、`background:var(--accent)`、`margin-top` 居中）并给轨道左右各留 ≈ 半个拇指宽的内缩；要么直接在 `QuantitySlider` 移除滑块、改用"百分比档位 chip + 输入框"。
- **P2/P4（建议）**：数量区与杠杆区采用同一种"档位 chip + 输入框"布局；把 `DirectionToggle`/`SegToggle`/档位 chip 收敛到尽量少的几种风格（例如 chip 统一描边+选中态，分段统一填充+选中态）。
- **P3（建议）**：用分区标题或细分隔线把"下单参数（方向/类型/价格）｜ 仓位（杠杆/数量/指标）｜ 止盈止损 ｜ 提交"分组，统一垂直节奏。
- **P5（建议）**：把数量校验错误移到紧邻数量输入框之下（与价格错误一致）。

---

## 附：关键证据索引（便于评审复查）

| 证据 | 位置 |
|---|---|
| 用户截图（旧构建，杠杆为滑块、数量拇指溢出左缘） | `.agentmesh/attachments/paste-1782585588200.png` |
| 最小复现 HTML / 截图（数量滑块 value=0 拇指溢出；含已修复对照） | `docs/diagnoses/live-long-short-order-panel-layout/slider-repro.html` · `slider-repro.png` |
| `Slider` 原语：`appearance-none` + `accent-color`，无 thumb 样式 | `apps/web/components/ui/index.tsx:212-232` |
| 全局 CSS 无任何 range thumb 规则 | `apps/web/app/globals.css:1-114` |
| 编译 CSS：`.appearance-none{…appearance:none}`，grep 无 `slider-thumb` | `apps/web/.next/static/css/*.css` |
| 数量滑块紧贴 `flex-1`、默认 `sz=0` | `apps/web/components/Live/QuantitySlider.tsx:59-69` · `LiveTradePanel.tsx:77` |
| 杠杆=档位按钮 / 数量=滑块（当前构建，范式割裂） | `apps/web/.next/static/chunks/app/page-3635e449131c0afa.js`（"杠杆倍率"/"仓位数量"附近） |
| 范式 / 样式不一致来源 | `LeverageControl.tsx:32-63` · `QuantitySlider.tsx:59-104` · `LiveTradePanel.tsx:206-294,348-368` · `DirectionToggle.tsx:55-71` · `TpSlFields.tsx:96-110` |
| `sz` 错误离位 | `LiveTradePanel.tsx:278-280`（对比价格错误 `:250-252`） |
| 面板挂载点（仅实盘渲染） | `ChartWorkspace.tsx:305-307` · `LiveBottomPanel.tsx:32-35` |
| `Slider` 原语仅被 `QuantitySlider` 使用（爆炸半径） | grep `<Slider` → `QuantitySlider.tsx`（+其测试） |

---

## 评审结论（Review Verdict）

- **评审节点**: Subtask #1（分析报告评审与修复）
- **评审人**: 资深评审（Anthropic 工程团队）
- **日期**: 2026-06-27
- **一句话裁定**: 根因证据**成立且已逐条核验通过**；推荐方向（F2 重排 + 统一范式）**正确**，但其中"是否移除数量滑块"应**收敛为「保留并修好滑块」**，而非改成档位 chip —— 这样更贴合交易类 UI 的最佳实践、回归面更小、且避免留下死代码。最终推荐 **F2′（F2 的最小化修正版，见下）**。

### A. 根因证据复核（逐条核验，全部命中）

我对 §3 的每条引用与"是否真在失败路径上执行"做了源码级回放，结论：**证据链成立，无悬空引用**。补强如下：

1. **失败路径执行链已端到端确认**（原报告分散在多处，这里串成一条，便于复查）：
   `accountMode==="live"`（`ChartWorkspace.tsx:305`）→ `<LiveBottomPanel>`（`:307`）→ 左栏 `<LiveTradePanel>`（`LiveBottomPanel.tsx:35`）→ `<QuantitySlider>`（`LiveTradePanel.tsx:267`）→ `<Slider>`（`QuantitySlider.tsx:60`）→ 缺 thumb 样式的 `<input type=range>`（`ui/index.tsx:212-232`）。**主因 A 引用的代码确在失败路径被渲染**，非旁支。
2. **`sz=0 ⇒ pct=0` 已用换算函数证实**（原报告仅推断）：`sizeToSliderPct(0, maxSz)` 在 `maxSz>0` 时返回 `(0/maxSz)*100 = 0`（`apps/web/lib/liveOrder.ts:61-64`），叠加 `LiveTradePanel.tsx:77` 的 `useState(0)`，"一进面板拇指即贴最左"成立。
3. **复现忠实度可信**：`slider-repro.html` 第 15 行使用的 `.appearance-none{…}` 与第 19-22 行 `.slider-current` 与当前 `Slider` 原语类名串（`ui/index.tsx:227-228`）逐字一致；截图 `slider-repro.png` 的 value=0 行拇指左溢、value=40 居中、FIXED 对照正常，能解释用户截图 P1。**证据确实解释了观察到的症状**。
4. **爆炸半径已复核**：`grep <Slider` 仅命中 `QuantitySlider.tsx`；`Slider` 原语只此一处消费者，杠杆已不再使用它（`LeverageControl.tsx` 现为 `grid-cols-5` 档位按钮，源码佐证强于 §3 的 `.next` 反编译片段 E3）。
5. **测试依赖已复核**：`QuantitySlider.test.tsx:28/48` 依赖 `getByLabelText("仓位数量百分比")`（即滑块 `aria-label`）做"百分比↔尺寸"联动断言——**这是"是否移除滑块"决策的关键回归约束**。

### B. 反例 / 被报告高估或低估之处（评审修正）

证据本身无误，但有两处**结论推演**需要校正，直接影响最终修复取舍：

1. **P4 把"数量百分比 chip 无选中态"判为缺陷，属误判**。杠杆 chip 反映的是**持续选择**（当前 `lever` 恒等于某一档），故有选中态合理；而数量快捷 chip（25/50/75/100%）是**一次性动作快捷键**——点击后 `sz` 仍可被滑块/输入框微调而偏离任一预设档，此时给它"选中态"反而会误导用户以为当前数量恰为该百分比。**"无选中态"是符合语义的设计，不应作为必改项**；只需统一其描边/圆角/字号视觉语言即可。
2. **§6 理由 3 由 E3 过度外推团队意图**。E3 只证明**杠杆**从滑块换成了档位 chip，并不能推出"所有数值控件都应收敛为 chip"。杠杆天然是**离散预设**（1/3/5/10/20× 才有意义），数量是**可开额度的连续百分比**——两者输入性质不同，**不要求控件类型一致**；一致性应由"统一视觉语言 + 清晰分区"达成，而非强行同构。交易类专业 UI（OKX / Binance 的仓位 size 条）普遍以滑块表达仓位比例，**移除数量滑块并非更优的 HCI 实践**。

### C. 候选修复审计（边界 / 最小化 / 回归）

- **F1（补 thumb / 轨道内缩）**：方向正确，但 §5 与复现仅覆盖 **value=0（左端）**。range 控件在 **value=max（100%，右端）同样会溢出**，故修复**必须左右两端都预留 ≈ 半个拇指宽的内缩**，且需同时给 `::-webkit-slider-thumb` 与 `::-moz-range-thumb` 定义几何（跨 Chromium/Firefox），并保持 `disabled:opacity-40` 生效。
- **F2（移除滑块改 chip）**：能解决 P1~P5，但**做了超出修复所需的额外工作且抬高回归面**——需重写 `QuantitySlider.test.tsx` 的联动用例、并使 `Slider` 原语沦为**未被引用的死导出**（与本仓 Clean Code §四"删除僵尸代码"冲突，留之则违规、删之则扩大改面）。同时按 B-2，它牺牲了一个符合交易 UI 习惯的连续控件。
- **F3（折中）**：未做分区（P3 仍在），不满足"工整、有层次"的验收语义。

### D. 最终推荐修复（单一方案）：F2′ — 保留并修好滑块的 F2

**采用 F2 的"重排 + 统一范式 + 分区"目标态，但数量控件保留滑块（按 F1 修好），不改成档位 chip。** 具体落地边界交付给 Subtask #2：

1. **（必做，地基）修 `Slider` 原语**：在 `ui/index.tsx` 的 `Slider`（或对其 className 定向加规则）补 `::-webkit-slider-thumb` + `::-moz-range-thumb`（宽高、圆角、`background:var(--accent)`、垂直居中 `margin-top`），并在轨道**左右两端**各预留 ≈ 半个拇指宽的内缩，使 value=0 与 value=100 都不溢出。**优先用定向选择器/类名而非裸 `input[type=range]` 全局规则**，避免对未来其它 range 输入产生全局副作用（当前虽仅此一处，但全局规则是不必要的爆炸半径）。
2. **分区 + 节奏（P3）**：把扁平的 `flex-col gap-3` 重排为"下单参数（方向 / 类型 / 价格）｜ 仓位（杠杆 / 数量 / 指标盒）｜ 止盈止损 ｜ 提交"分组，用分区标题或细分隔线建立层次，统一垂直间距。
3. **统一视觉语言（P4，按 B-1 收敛而非套选中态）**：把 `DirectionToggle` / `SegToggle`（限价市价、全仓逐仓）/ 杠杆档位 / 数量快捷 chip / `TpSlFields` ModeTab 收敛到尽量少的几套描边/填充风格；**数量快捷 chip 保持"动作按钮"语义（无持续选中态），仅对齐圆角/描边/字号**。
4. **校验错误归位（P5）**：把 `LiveTradePanel.tsx:278-280` 的 `sz` 错误移到紧邻数量输入框之下，与价格错误（`:250-252`）一致。
5. **测试**：保留滑块即保留 `aria-label="仓位数量百分比"`，**`QuantitySlider.test.tsx` 现有联动断言可不改**（仅若 DOM 结构调整影响 `getByLabelText`/`getByRole` 选择器时做等价更新）。

**选 F2′ 而非 F2 原版的理由**：

1. **更贴合 HCI 最佳实践**：仓位比例滑块是交易类 UI 的成熟范式；保留它（修好）比强行 chip 化更符合用户"符合人机交互最佳实践"的诉求。
2. **更小的最小改动 / 回归面**：保留滑块 ⇒ 联动测试基本不动、`Slider` 原语仍是被引用的有效原语（无死代码）、不触碰 `lib/liveOrder.ts` 任何换算逻辑（纯展示层）。F2 原版的移除+重写测试+处理死原语属"超出修复所需"的额外工作。
3. **一致性照样达成**：P2 的"乱"根源是**视觉语言割裂 + 无分组 + 滑块坏 + 错误离位**，而非"控件类型不同"。靠分区（步骤 2）+ 视觉收敛（步骤 3）即可工整，无需牺牲滑块。

**回归监视清单（交付 Subtask #2 / Subtask #3 Review 时核对）**：

- 轨道内缩须与 `accent-color` 轨道填充对齐，勿因加 padding 导致填充色与拇指错位；勿因加内缩改变 `flex-1` 与右侧 `Input` 的水平排布。
- thumb 规则务必同时覆盖 webkit 与 moz；保留 `disabled` 态视觉（`opacity-40` / `cursor-not-allowed`）。
- 视觉收敛勿破坏 a11y：`DirectionToggle` 的 `role="radiogroup"/"radio"`、`aria-checked` 必须保留；ModeTab / SegToggle 的可点击语义不变。
- 改动**仅限**展示层文件（`ui/index.tsx` 的 `Slider`、`QuantitySlider.tsx`、`LiveTradePanel.tsx`、必要时 `globals.css` 定向规则、`DirectionToggle.tsx` / `TpSlFields.tsx` 视觉），**不得**触碰 `lib/liveOrder.ts`、下单链路 `tradingApi`、确认弹窗安全护栏。

> **Definition of Done 对照**：根因证据链已逐条复核airtight（§A）；本节给出**单一**推荐修复 **F2′** 及显式理由（§D）。下一节点（Subtask #2）按 §D 落地实现。
