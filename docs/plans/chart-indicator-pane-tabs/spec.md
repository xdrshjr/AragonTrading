# 副图技术指标页签化 + 下区页签靠左方案（chart-indicator-pane-tabs）

> 版本：**v2**（Subtask #1 评审与就地修复完成；P0=0 / P1=3 已清零，详见「评审记录」与「评审结论」）
> 作者：Solution Architect（Anthropic 工程团队角色）
> 评审：Senior Reviewer（Anthropic 工程团队角色，2026-06-14）
> 适用迭代：多轮迭代任务「标准四阶段开发」第 3 轮
> 关联代码索引：`.claude-index/index.md`
> 前序方案：`docs/plans/chart-technical-indicators/spec.md`（第 1 轮）、`docs/plans/chart-workspace-layout/spec.md`（第 2 轮）

---

## 0. 评审记录（Review Notes）

> 本节由 Subtask #1 评审节点写入。评审已对照真实代码（`CandleChart.tsx` / `IndicatorPane.tsx` / `components/ui/index.tsx` 的 `Tabs` / `ChartWorkspace.tsx` / `lib/store.ts` / `lib/indicators.ts` / `lib/chartSync.ts` / `lib/types.ts`）逐节核验四个维度：**可行性 / 完整性 / 一致性 / 规模适配**。所有 **P0、P1 已在 v2 正文就地修复**；P2 为非阻塞建议，列于「评审结论」。

### 0.1 总体判断

方案整体质量高：范围澄清清晰（§2 明确「副图=`category==='pane'`」），架构判断正确（副图容器保留在 `CandleChart` 内、复用既有 `syncRef`/`chartSync`），派生链为纯函数（`selectActivePane`）天然可测，持久化迁移与既有 `chartPane`/`bottomTab` 范式一致。下列技术声明经核验**属实**：`flex-[4]`/`flex-[8]`/`flex-[2]` 任意值在 `ChartWorkspace` 已被使用（Tailwind JIT 支持）、`clsx` 已在 `ui/index.tsx` 导入、`IndicatorKey` 已被 `CandleChart` 导入、store `version` 现为 3（→4 正确）、默认指标集启用的 pane 仅 MACD（`DEFAULT_INDICATORS`：ma/boll/macd=on，其余 off）、`syncRef`/`unregister`/`chart.remove()` 卸载清理真实存在。**无 P0**。

> 类型核验补记：§9.5 将 `onPaneTabChange={setPaneTab}`，其中 `setPaneTab:(IndicatorKey|null)=>void` 赋给 `onPaneTabChange?:(IndicatorKey)=>void`——形参逆变下宽入参函数可赋给窄入参形参，**类型成立**，不报错。§9.4 `selectActivePane(..., paneTab ?? null)` 中的 `?? null` 因 `paneTab?` 为可选（含 `undefined`）而**必要**，非冗余。两点均无需修改。

### 0.2 问题清单

| 编号 | 维度 | 严重度 | 问题 | 处置 |
|------|------|--------|------|------|
| R-1 | 完整性 / 一致性 | **P1** | §9.4 副图页签用 `label: d.label`，而注册表 `label` 为「RSI 相对强弱」「KDJ 随机指标」「VOL 成交量」等**长名**；§2 示意图却是 `[MACD][RSI][KDJ][VOL]` **短名**。长名会撑宽 20% 容器顶部页签、与「靠左、紧凑、不占整行」目标冲突。 | v2 §5.2/§9.4/§7.3 改为短标签 `d.key.toUpperCase()`，与示意图统一 |
| R-2 | 可行性 / 完整性 | **P1** | §5.1/§9.2 underline 变体容器用 `overflow-x-auto`。CSS 规范下 `overflow-x:auto` 会把 `overflow-y` 由 `visible` 计算为 `auto`，可能**裁掉**用 `-mb-px` 负外边距压在父底边线上的 2px 下划线，或意外出现纵向滚动条——破坏本变体的核心视觉（需求 2 主交付物）。 | v2 §5.1/§9.2 移除默认 `overflow-x-auto`（≤4 个短标签页签在常见图宽下不溢出），保留单行；并记录溢出回退策略 |
| R-3 | 完整性 | **P1** | 旧设计多副图**同时可见**，主图右价轴宽恒为各 pane 的 max，稳定；改为**单显切换**后，主图右价轴宽 = max(主图, 当前 pane)，而 VOL（大数）与 RSI（0~100）值域差异大，**切页签会使主图 K 线区宽度跳变/轻微重排**。§3.2(D) 仅以「autoSize 负责重排」一笔带过，未分析此新行为。 | v2 §3.2(D) 与风险表补全分析并显式取舍（接受用户主动切换时的重排，符合专业终端；如需消除可固定副图右轴宽度），确认对齐 effect 依赖含 `activeKey` |
| R-4 | 完整性 | P2 | 单显后页签已标注指标名，`IndicatorPane` 仍保留左上角绝对定位标签（§9.3），名称与页签重复。 | 非阻塞：保留亦可（`paramsHint` 仍补充参数信息）；建议仅留 `paramsHint` 或将参数并入页签 title |
| R-5 | 一致性 | P2 | 共享 `Tabs`（segmented 与 underline 两变体）均无 `role="tab"`/`aria-selected`/`role="tablist"`。沿袭既有 segmented，**非本轮回归**，但第 1 轮强调「人机交互最佳实践」。 | 非阻塞：建议后续做一次 a11y 收口（两变体一并加 ARIA + 方向键） |
| R-6 | 规模适配 | P2 | §5.4 `setPaneTab:(IndicatorKey|null)=>void` 的 `null` 入参在本轮无调用方（迁移走 state 而非 setter，点击只传 key）。 | 非阻塞：可窄化为 `(IndicatorKey)=>void`；保留 `null` 亦无害，从简不强制 |
| R-7 | 完整性 | P2 | 20% 副图容器内顶部页签行（约 28px）会占去部分高度，极小视口下 pane 可读区被压。 | v2 §3.2(D)/§9.4 注明 `min-h-[120px]` 已含页签行预算；建议实现期目测 1m 周期小屏不塌陷 |

## 1. 概述（Overview）

本轮在第 1 轮「K 线技术指标体系（MA/EMA/BOLL 主图叠加 + MACD/RSI/KDJ/VOL 副图窗格）」与第 2 轮「左侧 K 线工作区 80/20 + 20/80 重构」已落地的基础上，对**副图技术指标的呈现方式**与**下区数据页签的视觉形态**做一次小而精的打磨，**不回滚任何既有功能**。两条需求都聚焦交互细节，不触碰后端、不改数据链路，因此改动面与回归风险都被刻意压到最小。

需求一针对的是当前副图指标的「堆叠」问题：现状下 `CandleChart` 把每个启用的副图指标（MACD/RSI/KDJ/VOL）各渲染成一个固定 140px 高的 `IndicatorPane`，在主图下方**竖向堆叠、同时全部可见**——启用越多，副图越长，挤占主图高度且观感杂乱。本轮要求把「各个技术指标」收纳进**位于中间 K 线大面板下方、占其约 20% 高度的单一容器**，容器内用**页签切换**，任一时刻只显示一个副图指标。这既贴合专业行情终端（TradingView 风格）的副窗格交互，也让主图稳定占据约 80% 的垂直空间。

需求二针对第 2 轮落地的下区「成交记录 / 当前持仓」页签：现状用共享 `Tabs` 组件，其样式为**整行平铺的分段控件**（每个 tab `flex-1` 撑满），不符合「靠左、不占整行」的预期。本轮要求把这组页签改成**靠左、内容宽度**的页签样式。考虑到需求一的副图页签也需要同样的「靠左紧凑」观感，方案统一在共享 `Tabs` 组件上**新增一个 `underline` 变体**（左对齐、下划线高亮、内容宽度），同时服务两处，避免各自造轮子、保持设计语言一致。整体改动集中在前端 `apps/web`，遵守仓库「绝不造假数据」「单文件 < 800 行、单函数 < 50 行、形参 ≤ 5、命名自解释」等既有约定。

---

## 2. 需求解读与范围澄清

第 3 轮两条需求文字简短，落地前先澄清三处边界，避免下游理解偏差：

1. **「各个技术指标」= 副图指标（pane），非主图叠加指标（overlay）。** 主图叠加类 MA/EMA/BOLL 本质是画在价格刻度上的曲线，必须与 K 线共轴，无法、也不应移到下方独立窗格。能且应当「放进下方 20% 容器、用页签切换」的，是注册表中 `category === "pane"` 的 MACD/RSI/KDJ/VOL。本方案所称「副图指标」「pane 指标」均指这四个。overlay 指标继续叠加在主图上，行为不变。
2. **页签集合 = 当前启用的副图指标。** 第 1 轮的「指标弹窗」（`IndicatorMenu`）开关语义保持不变：用户在弹窗里勾选的 pane 指标，构成下方容器的页签集合。勾选 0 个 pane 指标时，下方容器不渲染、主图占满全高（与现状 `hasPane === false` 行为一致）；勾选 ≥1 个时，渲染容器并以页签切换，默认显示其中第一个（注册表顺序，默认 MACD）。
3. **「占据下方 20% 高度」按弹性比例落地。** 由现状「每个副图固定 140px」改为「主图 : 副图容器 ≈ 80% : 20% 的弹性比例」，并给副图容器设 `min-h` 下限防止在小视口塌陷到不可读。

收敛后的目标形态（仅 price 视图，equity 视图无副图，保持不变）：

```
图表区（ChartWorkspace 上区右 80%，price 视图）
┌──────────────────────────────────────────────┐
│ ChartToolbar（周期 / 指标弹窗 / 区间 / 模式 / 状态）│
├──────────────────────────────────────────────┤
│ [缓存样例 banner——条件渲染]                       │
├──────────────────────────────────────────────┤
│ ChartHeaderBar：[K线价格走势 | 净值] ··· OHLC 横栏 │
├──────────────────────────────────────────────┤
│                                                │
│        主图 CandleChart  ≈ 80% 高              │  ← flex-[4]
│        （K 线 + MA/EMA/BOLL 叠加 + marker）      │
│                                                │
├──────────────────────────────────────────────┤
│ [MACD] [RSI] [KDJ] [VOL]   ← 靠左下划线页签      │  ← 需求 1：副图容器 ≈ 20% 高（flex-1）
│ ┌────────────────────────────────────────────┐│     仅渲染当前页签对应的单个 IndicatorPane
│ │ 当前选中副图（独占日期轴）                      ││
│ └────────────────────────────────────────────┘│
└──────────────────────────────────────────────┘

ChartWorkspace 下区（≈20% 高）
┌──────────────────────────────────────────────┐
│ 成交记录  当前持仓     ← 需求 2：靠左下划线页签，不占整行 │
│ （对应面板内容…）                                 │
└──────────────────────────────────────────────┘
```

---

## 3. 技术设计（Technical Design）

### 3.1 架构与数据流

副图容器**必须留在 `CandleChart` 内部**，不上移到 `ChartWorkspace`。原因：副图与主图的「时间轴双向同步 + 十字光标贯穿 + 价格轴等宽对齐」依赖 `CandleChart` 内部懒创建、生命周期内稳定的 `syncRef`（`createSyncGroup()`）。把副图拆出组件会迫使把同步组上移并跨组件共享，复杂度与回归风险陡增，得不偿失。因此本轮在 `CandleChart` 的根 flex 列里，把原先「`enabledPaneDefs.map` 堆叠多个固定高 `IndicatorPane`」替换为「一个约 20% 高的容器：顶部一条靠左页签 + 下方仅渲染当前选中的单个 `IndicatorPane`」。

页签的「当前选中态」走第 2 轮已确立的 store 持久化范式（`chartPane`/`bottomTab` 皆持久化），新增 `paneTab: IndicatorKey | null`，由 `ChartWorkspace` 从 store 读出并以 props 下传给 `CandleChart`（与既有 `indicators` prop、`onCrosshairChange` 回调同构，保持 `CandleChart` 纯由 props 驱动、可测）。

```
useAppStore（新增 paneTab / setPaneTab，持久化）
        │
ChartWorkspace（读 store：indicators / paneTab / setPaneTab …）
  └─ price 视图 → <CandleChart
                    indicators={indicators}
                    paneTab={paneTab}
                    onPaneTabChange={setPaneTab}
                    …既有 props… />

CandleChart（内部 syncRef 不变）
  ├─ enabledPaneDefs = INDICATORS.filter(d => d.category==='pane' && indicators[d.key])  // 注册表序
  ├─ activeKey      = selectActivePane(enabledPaneDefs.map(d=>d.key), paneTab)            // 纯函数派生
  ├─ activePaneDef  = enabledPaneDefs.find(d => d.key===activeKey) ?? null
  └─ 渲染：主图 flex-[4]；当 activePaneDef 非空，副图容器 flex-1：
        ├─ <Tabs variant="underline" tabs=启用 pane 列表 active=activeKey onChange=onPaneTabChange/>
        └─ <IndicatorPane key=activeKey def=activePaneDef showTimeAxis candles theme sync=syncRef/>
```

`selectActivePane` 抽为 `lib/paneTabs.ts` 纯函数（对齐项目「纯逻辑 + 同名 `.test.ts`」约定，如 `ohlc.ts`/`position.ts`/`indicators.ts`），不引入 React/DOM，可在 jsdom 外单测。

### 3.2 关键代码路径与执行时序

**(A) 选中副图的派生（无副作用）**
1. `CandleChart` 由 `indicators` prop 算出 `enabledPaneDefs`（注册表顺序即页签顺序）。
2. `activeKey = selectActivePane(enabledKeys, paneTab)`：
   - `enabledKeys` 为空 → `null`（不渲染副图容器）；
   - `paneTab` 仍在启用集合中 → 用 `paneTab`（尊重用户上次选择）；
   - 否则 → 回落首个启用项（注册表序，默认 MACD）。
   纯派生、确定性，无需 effect 写回 store，天然规避「effect 改 state 引发额外渲染/回环」。
3. `activePaneDef = enabledPaneDefs.find(d => d.key===activeKey) ?? null`。

**(B) 切换页签时序**
1. 用户点页签 → `onPaneTabChange(key)` → `store.setPaneTab(key)`（key 必为启用项）。
2. `paneTab` 变化 → `activeKey` 重算 → `IndicatorPane` 的 `key` 变化 → React **卸载旧 pane、挂载新 pane**。
3. 旧 pane 卸载 effect：`unregister()` 退出同步组 + `chart.remove()`（已存在，无内存泄漏）。
4. 新 pane 挂载 effect：`createChart` + `sync.register(...)`；`chartSync` 在 `members.size>1` 时于下一帧把新图对齐到主图当前可见逻辑范围（已存在逻辑），保证切换后副图与主图时间轴对齐。
5. 价格轴等宽：新 pane 子图在其挂载 effect（先于父 effect 执行）内已注册进 `syncRef`，故 `CandleChart` 的对齐 effect 触发时 `getCharts()` 已含新图——只需把对齐 effect 的依赖从 `paneSig` 改为 `activeKey`（不同副图值域不同，如 RSI 0~100 vs MACD 小数 vs VOL 大数，须重测重写宽度）。

**(C) 时间轴归属（简化）**
当前实现中「最底部 pane 独占日期轴、其余隐藏」是为应对多个堆叠 pane。改为单 pane 后，**当前选中的 pane 永远是唯一 pane → 永远 `showTimeAxis={true}`**；主图仍按 `timeScale.visible = !hasPane` 在有副图时隐藏自身日期轴（`hasPane = enabledPaneDefs.length > 0`）。即「全栈任一时刻只一行日期轴」的既有不变量自然维持。

**(D) 高度比例**
主图容器 `className` 由 `flex-1` 改为 `flex-[4]`；副图容器 `flex-1` + `min-h-[120px]`（≈20%，下限防塌陷）。`hasPane === false` 时副图容器不渲染，主图 `flex-[4]` 独占全高（grow 填满），并恢复自身日期轴。lightweight-charts `autoSize` + 既有 `ResizeObserver→alignPriceScaleWidths` 负责副图出现/消失时的重排。副图容器内部再分两段：顶部页签行（`shrink-0`，约 28px）+ 下方 `IndicatorPane`（`flex-1 min-h-0`）。`min-h-[120px]` 是**含页签行**的容器下限（页签 ≈28px + pane ≥92px），保证小视口下 pane 仍可读（R-7）。

**(E) 单显切换引入的主图宽度跳变（评审 R-3，须知悉而非视作缺陷）**
旧设计各副图**同时可见**，`alignPriceScaleWidths` 取「主图 + 所有 pane」同侧价轴的最大宽度回写所有图，故主图右价轴宽**恒为各 pane 的 max、保持稳定**。改为**单显**后，`getCharts()` 任一时刻仅含「主图 + 当前 pane」，主图右价轴宽 = `max(主图价格量级, 当前 pane 值域宽度)`。而各副图值域差异大（VOL 为成交量大数、RSI 固定 0~100、MACD 为小数），因此**切换页签时主图右价轴宽度会随之变化，导致 K 线绘图区宽度发生一次轻微重排**。

评审取舍：**接受此重排**。理由：(1) 仅在用户**主动点击页签**时发生一次，非实时抖动（实时每 tick 已由 `priceMagnitude` 量级判定屏蔽，RV-1 不受影响）；(2) 专业行情终端（TradingView 等）切换副窗格时同样会重排绘图区，符合用户预期；(3) 对齐 effect 依赖已含 `activeKey`（见 §3.2(B) 步骤 5 与 §9.4），切页签即重测重写同侧价轴宽度，保证切换后主/副时间轴仍像素级对齐。若后续认为重排观感不佳，可作增量优化：给副图右价轴设一个稳定的 `minimumWidth`（按全体启用 pane 值域预估），使主图宽度不随当前 pane 变化——本轮不做，避免过度设计。

### 3.3 复用与新增边界

- **复用**：共享 `Tabs`（新增 `underline` 变体后两处复用）、既有 `IndicatorPane`（仅改外层高度）、`syncRef`/`chartSync`、`reconcilePlots`、`INDICATORS` 注册表、`IndicatorMenu`（开关语义完全不动）。
- **新增**：`lib/paneTabs.ts`（`selectActivePane` 纯函数）+ 同名测试；store 一个字段 `paneTab` 及 setter；`Tabs` 的 `underline` 变体。
- **修改**：`CandleChart.tsx`（堆叠→页签单窗格 + 两个新 prop + 对齐依赖）、`IndicatorPane.tsx`（固定 140px → `h-full`）、`ChartWorkspace.tsx`（透传 paneTab + 下区 Tabs 改 underline）、`store.ts`（字段/持久化/迁移）、`ui/index.tsx`（Tabs 变体）。
- **不动**：后端全部、`page.tsx`、`ChartHeaderBar`（其 `[K线价格走势|净值]` 页签保持 segmented，不在本轮范围）、`EquityChart`、`StrategyPanel`、`indicators.ts` 计算与注册表、第 1/2 轮其余成果。

---

## 4. 文件 / 模块变更计划

| # | 文件 | 动作 | 一句话意图 | 优先级 |
|---|------|------|-----------|--------|
| 1 | `apps/web/lib/paneTabs.ts` | 新增 | `selectActivePane(enabledKeys, paneTab)` 纯函数：由启用副图键 + 上次选中页签派生当前应显示的副图键（空→null、命中→自身、否则→首个） | P0 |
| 2 | `apps/web/lib/paneTabs.test.ts` | 新增 | Vitest：空集合→null、paneTab 命中→自身、paneTab 失效→首个、paneTab=null→首个 | P0 |
| 3 | `apps/web/lib/store.ts` | 改 | 新增 `paneTab: IndicatorKey \| null`（默认 `null`）+ `setPaneTab`；`partialize` 增列 `paneTab`；`version` 3→4，`migrate` 对 `version<4` 补 `paneTab:null` | P0 |
| 4 | `apps/web/components/ui/index.tsx` | 改 | `Tabs` 新增 `variant?: "segmented" \| "underline"`（默认 `segmented`，保持既有行为；`underline` 为靠左、内容宽度、下划线高亮） | P0 |
| 5 | `apps/web/components/Chart/IndicatorPane.tsx` | 改 | 外层容器由 `style={{height:140}}` 改为 `h-full`，填满父级 20% 容器；label 绝对定位不变 | P0 |
| 6 | `apps/web/components/Chart/CandleChart.tsx` | 改 | 副图由「多窗格堆叠」改为「底部 ≈20% 容器 + 靠左页签 + 单窗格」；新增 `paneTab?` / `onPaneTabChange?` props；主图 `flex-1`→`flex-[4]`；对齐 effect 依赖 `paneSig`→`activeKey` | P0 |
| 7 | `apps/web/components/Chart/ChartWorkspace.tsx` | 改 | 从 store 读 `paneTab`/`setPaneTab` 透传给 CandleChart；下区「成交记录/当前持仓」`<Tabs>` 改 `variant="underline"`（需求 2 靠左） | P0 |

> 全部 P0，无 P1/P2 拆分——两条需求都是必达的小改动。改动 7 个文件，其中 2 个为纯新增（含测试），5 个为局部修改，无新增依赖、无后端改动。

---

## 5. 接口设计（Interface Design）

### 5.1 共享 `Tabs` 变体扩展（`components/ui/index.tsx`）

```ts
export function Tabs({
  tabs,
  active,
  onChange,
  variant = "segmented",            // 新增；默认保持既有分段控件行为
}: {
  tabs: { key: string; label: string }[];
  active: string;
  onChange: (key: string) => void;
  variant?: "segmented" | "underline";
}): JSX.Element
```

- `variant="segmented"`（默认）：**完全保持现状**（`flex gap-1 rounded bg-surface-2 p-1`，每 tab `flex-1` 撑满）。`ChartHeaderBar` 的 `[K线价格走势|净值]` 不传 `variant`，行为零变化。
- `variant="underline"`（新增）：靠左、内容宽度、无填充背景；容器 `flex items-center gap-1 border-b border-[var(--border)]`，每个 tab 为内容宽度按钮（`shrink-0`），激活态用 `border-b-2 border-[var(--accent)] text-[var(--text)]`、非激活态 `border-transparent text-[var(--muted)] hover:text-[var(--text)]`，配合 `-mb-px` 让 2px 下划线压在父容器底边线上。**用于**需求 1 副图页签、需求 2 下区页签。
  - **评审修正 R-2**：容器**不加** `overflow-x-auto`。原因：CSS 规范下设置 `overflow-x:auto` 会把另一轴 `overflow-y` 由 `visible` 计算为 `auto`，进而**裁掉** `-mb-px` 负外边距溢出的 2px 下划线，并可能凭空出现纵向滚动条，破坏下划线观感。本轮两处调用方页签数都很少（副图 ≤4 个短标签 `MACD/RSI/KDJ/VOL`、下区 2 个 `成交记录/当前持仓`），在常见图表宽度（≥320px）下绝不溢出，故单行即可、无需横向滚动容器。**溢出回退策略**（仅当未来页签数显著增多时）：将 `overflow-x-auto` 下沉到一个**不承载底边线**的内层 wrapper，底边线留在外层容器，避免负外边距被裁；切勿在带 `-mb-px` 子元素的同一元素上直接开 `overflow`。
- `onChange` 形参仍为宽类型 `string`：调用方须用收窄包装（沿用第 2 轮 R1 约定），如 `onChange={(k)=>onPaneTabChange?.(k as IndicatorKey)}`、`onChange={(k)=>setBottomTab(k as BottomTab)}`。

### 5.2 `CandleChart` 新增 props（`components/Chart/CandleChart.tsx`）

```ts
type Props = {
  // …既有 props 不变（candles/equity/trades/overlays/indicators/theme/loading/error/onLoadMore/jumpToTs/onCrosshairChange）…
  paneTab?: IndicatorKey | null;                 // 当前选中的副图页签（来自 store，可空）
  onPaneTabChange?: (key: IndicatorKey) => void;  // 点击页签回调（= store.setPaneTab），仅传启用项
};
```

> `IndicatorKey` 已由 `CandleChart` 现有 import（`@/lib/indicators`）引入，无需新增导入。两 prop 均可选：缺省时（如未来在别处复用）退化为「无选中→显示首个启用副图、点击无副作用」，不崩溃。
>
> **评审 R-1**：副图页签 `label` 由调用方（`CandleChart`）传入**短标签**——用 `d.key.toUpperCase()`（→ `MACD/RSI/KDJ/VOL`），**不要**直接传注册表 `d.label`（长名如「RSI 相对强弱」会撑宽 20% 容器顶部页签，违背「靠左紧凑」）。`Tabs` 组件本身对 `label` 不做截断，短化是调用方职责，避免反向污染 `indicators.ts` 注册表。

### 5.3 `selectActivePane` 纯函数签名（`lib/paneTabs.ts`）

```ts
import type { IndicatorKey } from "./indicators";

/**
 * 由「启用的副图指标键（注册表顺序）」与「用户上次选中的页签」派生当前应显示的副图键。
 * @param enabled 启用的副图指标键，按注册表顺序（决定回落时取哪个为首个）
 * @param paneTab 用户上次显式选中的页签；可能已被关闭或为 null
 * @returns 当前应显示的副图键；无启用项时为 null
 */
export function selectActivePane(
  enabled: IndicatorKey[],
  paneTab: IndicatorKey | null,
): IndicatorKey | null {
  if (enabled.length === 0) return null;
  if (paneTab !== null && enabled.includes(paneTab)) return paneTab;
  return enabled[0];
}
```

### 5.4 Store 扩展（`lib/store.ts`）

```ts
// 类型与默认值
paneTab: IndicatorKey | null;     // 默认 null（派生取首个启用副图，默认 MACD）
setPaneTab: (key: IndicatorKey | null) => void;
// action：setPaneTab: (paneTab) => set({ paneTab })
// partialize：在返回对象中增列 paneTab: s.paneTab
// version：3 → 4
// migrate：if (version < 4) next = { ...next, paneTab: null };
```

> 无 REST / WebSocket / CLI 接口变化——本轮纯前端交互，沿用既有 `/api/py/candles`、`marketWS` 等真实数据链路。

---

## 6. 数据模型（Data Model）

本轮**不引入任何后端 DB schema 或网络数据形态变化**，仅扩展前端持久化的 UI 选择态。

### 6.1 Store 持久化形态（localStorage key `aragon-ui`）

```ts
// 新增持久化字段（随 chartPane / bottomTab 一并 partialize）
paneTab: IndicatorKey | null;   // "macd" | "ema" | … | null（仅 pane 类有意义；非 pane 值经 selectActivePane 自然回落，无害）
```

- **默认值**：`null`。首次加载 / 迁移 / 清缓存后，`selectActivePane(enabled, null)` 取首个启用副图——默认指标集（MA+BOLL+MACD）下启用的 pane 仅 MACD，故默认显示 MACD，符合「默认 MACD」直觉。
- **版本迁移**：`version` 3→4，`migrate` 对 `version<4` 注入 `paneTab:null`。即便不迁移，zustand `persist` 浅合并也会让缺失字段回落初始 state 的 `null`（与第 2 轮 R11 同理），升版属「显式稳妥」。
- **健壮性**：若持久化里残留的 `paneTab` 指向一个已被关闭、甚至非 pane 的键，`selectActivePane` 因 `enabled.includes(paneTab)` 为假而回落首个启用项，**不会出现空白副图或崩溃**。

### 6.2 派生关系（运行期内存，无持久化）

```
indicators(持久化) ──▶ enabledPaneDefs = INDICATORS.filter(pane && on)  // CandleChart 内
paneTab(持久化)    ──┐
                     ├─▶ activeKey = selectActivePane(enabledKeys, paneTab)
enabledKeys ─────────┘   ──▶ activePaneDef ──▶ 渲染单个 IndicatorPane
```

派生链全为纯函数 / `useMemo`，无状态回写，确定性可测。

---

## 7. 测试与验收标准（Testing & Acceptance）

### 7.1 单元测试（Vitest，必须先于实现编写并稳定）

`apps/web/lib/paneTabs.test.ts`（覆盖 `selectActivePane` 正常路径与异常路径）：

- `returns_null_when_none_enabled`：`enabled=[]` → `null`。
- `returns_paneTab_when_still_enabled`：`enabled=["macd","rsi"]`, `paneTab="rsi"` → `"rsi"`。
- `falls_back_to_first_when_paneTab_disabled`：`enabled=["rsi","kdj"]`, `paneTab="macd"` → `"rsi"`（注册表序首个）。
- `falls_back_to_first_when_paneTab_null`：`enabled=["macd","vol"]`, `paneTab=null` → `"macd"`。
- `falls_back_to_first_when_paneTab_non_pane`：`paneTab="ma"`（非 pane / 不在集合）→ 首个启用项（健壮性）。

既有 `format/compare/indicators/chartSync/ohlc/position` 等测试须保持全绿（本轮未改其源）。

### 7.2 类型 / 构建门禁（必须）

- `pnpm --filter web typecheck`（tsc）零错误：重点核对 `Tabs` 新增 `variant` 可选不破坏既有调用方、`CandleChart` 新增可选 props、store `IndicatorKey | null` 类型一致、收窄包装到位。
- `pnpm --filter web build` 通过；既有前端 CI 静态门（tsc/vitest/build）与桌面静态门不被破坏。

### 7.3 人工验收清单（对照两条需求）

1. **需求 1**：
   - 默认（MA+BOLL+MACD）下，主图下方出现约 20% 高的副图容器，顶部一条**靠左**页签仅含**短标签**「MACD」（非长名「MACD 平滑异同移动平均」之类），下方显示 MACD 窗格；主图稳定占约 80%。
   - 在「指标弹窗」勾选 RSI/KDJ/VOL → 页签条出现对应**短标签**页签 `RSI/KDJ/VOL`（注册表序）；页签条单行靠左、不占整行、下划线高亮无裁切（R-2）；点击任一页签 → 副图**整窗切换**为该指标，任一时刻只显示一个；主图/副图日期轴对齐、十字光标贯穿、价格轴等宽。
   - 切换页签时主图 K 线区可能因新副图值域不同（如 VOL 大数 ↔ RSI 0~100）而**一次性轻微重排**——属预期行为（R-3），切换后仍保持主/副时间轴像素级对齐，无错位、无残影。
   - 取消勾选全部 pane 指标 → 副图容器消失、主图占满全高并恢复自身日期轴。
   - 切换主题：当前副图重新着色正确；切标的/周期：副图随新数据重算、与主图对齐。
   - 刷新页面：上次选中的副图页签被恢复（持久化）；若上次选中的指标已被关闭，回落首个启用项。
2. **需求 2**：下区「成交记录 / 当前持仓」页签变为**靠左、内容宽度**（不再整行平铺）；点击切换功能与数据展示均与第 2 轮一致（TradesTable / PositionPanel 不变）。
3. **回归**：第 1 轮主图叠加（MA/EMA/BOLL）、指标弹窗（默认 MA+BOLL+MACD、恢复默认 / 全部关闭、键盘可达）功能不变；第 2 轮 OHLC 横栏、`[K线价格走势|净值]` 视图页签（仍为 segmented 样式）、80/20 + 20/80 布局、InstrumentList、缓存样例横幅、实时流脉冲均正常；右侧 StrategyPanel 不变；无 hydration 告警；切页签 / 切视图 / 卸载时图表 `chart.remove()` 无内存泄漏。

---

## 8. 风险与缓解（Risks & Mitigations）

| 风险 | 影响 | 缓解 |
|------|------|------|
| 「各个技术指标」被误解为含主图叠加（MA/BOLL） | 把无法独立成窗的 overlay 错误移入副图 | §2 已明确：仅 `category==='pane'`（MACD/RSI/KDJ/VOL）页签化；overlay 仍叠加主图。评审/开发以此为准 |
| 切页签频繁卸载/重挂 `IndicatorPane` 造成图表抖动或泄漏 | 性能 / 内存 | 复用既有卸载清理（`unregister()`+`chart.remove()`，已验证）；`key={activeKey}` 精确控制重挂；切换不重取数据（candles 已在内存） |
| 副图切换后时间轴 / 价格轴与主图错位 | 视觉瑕疵、不「完美」 | 沿用 `chartSync` 新图下一帧对齐主图可见范围；对齐 effect 依赖由 `paneSig` 改为 `activeKey`，确保不同值域副图切入时重测重写价轴宽度（§3.2 (B)/(D)） |
| 单显切换使主图右价轴宽随当前 pane 值域变化，切页签时主图 K 线区轻微重排（R-3） | 切换观感、非「完美」 | 评审已分析并**显式接受**：仅用户主动切页签时发生一次（非实时抖动）、符合专业终端行为、`activeKey` 依赖保证切后仍像素级对齐；如需消除可后续给副图右轴设稳定 `minimumWidth`（本轮不做，避免过度设计，§3.2(E)） |
| underline 变体 `overflow-x-auto` 裁掉 `-mb-px` 下划线（R-2） | 下划线缺失 / 多余纵向滚动条，破坏需求 2 视觉 | 移除默认 `overflow-x-auto`；两处页签数少、单行不溢出；溢出回退见 §5.1（滚动放不承载底边线的内层 wrapper） |
| 副图页签误用注册表长名 `d.label`（R-1） | 页签撑宽、违背「靠左紧凑」 | 调用方传短标签 `d.key.toUpperCase()`（MACD/RSI/KDJ/VOL），与 §2 示意图一致；不污染注册表 |
| 持久化 `paneTab` 指向已关闭 / 非 pane 的键 | 空白副图 / 崩溃 | `selectActivePane` 以 `enabled.includes(paneTab)` 守卫，失效即回落首个启用项；§7.1 含该健壮性用例 |
| 修改共享 `Tabs` 波及现有调用方 | 既有页签样式回归 | 新增 `variant` 为**可选、默认 `segmented`**，等价旧行为；仅副图页签与下区页签显式传 `underline`；`ChartHeaderBar` 不传、零变化（§5.1） |
| 20% 副图在小视口被压塌 / 不可读 | 小屏可用性 | 副图容器 `min-h-[120px]` 下限 + 主图 `min-h-0` 可缩；桌面/Electron 为一等公民（与索引、第 2 轮定位一致） |
| 共享 `Tabs.onChange:(string)=>void` 接窄类型 setter | tsc 逆变报错 | 沿用第 2 轮 R1 收窄包装：`onChange={(k)=>onPaneTabChange?.(k as IndicatorKey)}` / `(k as BottomTab)` |
| 第 1 轮「多副图同时可见」属隐性能力，本轮改为单显 | 个别用户期望同时看多个副图 | 这是需求一「放进一个容器、用页签切换」的直接落地（单显），非缺陷；如未来需多显可另起增量（容器内分屏） |
| store version 升级迁移 | 旧持久化缺字段 | `version:4` + `migrate(version<4)` 补 `paneTab:null`；追加字段即便不迁移也由浅合并回落默认（显式稳妥） |

---

## 9. 附录：关键落地片段（供开发节点逐行参考）

### 9.1 `lib/paneTabs.ts`（新增，全文见 §5.3）
按 §5.3 实现；配 `paneTabs.test.ts`（§7.1 五用例）。

### 9.2 `components/ui/index.tsx` —— `Tabs` 增 `underline` 变体（示意）
```tsx
export function Tabs({ tabs, active, onChange, variant = "segmented" }: {
  tabs: { key: string; label: string }[];
  active: string;
  onChange: (key: string) => void;
  variant?: "segmented" | "underline";
}) {
  if (variant === "underline") {
    return (
      // 评审 R-2：不加 overflow-x-auto（会令 overflow-y 计算为 auto 而裁掉 -mb-px 下划线）；
      // 两处调用方页签数少，单行不溢出。如未来需横向滚动，见 §5.1 回退策略（滚动放不承载底边线的内层）。
      <div className="flex items-center gap-1 border-b border-[var(--border)]">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => onChange(t.key)}
            className={clsx(
              "-mb-px shrink-0 border-b-2 px-3 py-1.5 text-xs font-medium transition-colors duration-150",
              active === t.key
                ? "border-[var(--accent)] text-[var(--text)]"
                : "border-transparent text-[var(--muted)] hover:text-[var(--text)]",
            )}
          >
            {t.label}
          </button>
        ))}
      </div>
    );
  }
  // —— 既有 segmented 实现，原样保留 ——
  return (
    <div className="flex gap-1 rounded-[10px] bg-[var(--surface-2)] p-1">
      {/* …现状代码不变… */}
    </div>
  );
}
```

### 9.3 `components/Chart/IndicatorPane.tsx` —— 高度自适应（仅改外层 div）
```tsx
// 由：<div className="relative w-full" style={{ height: 140 }}>
// 改为（填满父级 20% 容器；不再写死像素高）：
<div className="relative h-full w-full">
  <div ref={containerRef} className="h-full w-full" />
  <span className="pointer-events-none absolute left-2 top-1 z-10 select-none text-[11px] font-medium text-[var(--muted)]">
    {def.label}{def.paramsHint ? <span className="ml-1 opacity-70">{def.paramsHint}</span> : null}
  </span>
</div>
```

### 9.4 `components/Chart/CandleChart.tsx` —— 副图页签单窗格（核心改动）
```tsx
import { selectActivePane } from "@/lib/paneTabs";
import { Tabs } from "@/components/ui";
// Props 增：paneTab?: IndicatorKey | null; onPaneTabChange?: (key: IndicatorKey) => void;

// 派生（替换原 enabledPaneDefs 之后的 paneSig 用法）：
const enabledPaneDefs = INDICATORS.filter((d) => d.category === "pane" && indicators[d.key]);
const hasPane = enabledPaneDefs.length > 0;
const activeKey = selectActivePane(enabledPaneDefs.map((d) => d.key), paneTab ?? null);
const activePaneDef = enabledPaneDefs.find((d) => d.key === activeKey) ?? null;

// 价格轴等宽对齐依赖：paneSig → activeKey（不同副图值域不同，须重测重写宽度）
useEffect(() => {
  if (syncRef.current) alignPriceScaleWidths(syncRef.current.getCharts());
}, [priceMagnitude, activeKey, hasPane, theme, hasEquity]);

// 主图时间轴可见性：保持 [hasPane]
useEffect(() => {
  chartRef.current?.applyOptions({ timeScale: { visible: !hasPane } });
}, [hasPane]);

// JSX：主图容器 flex-1 → flex-[4]；副图堆叠 → 单容器 + 页签
return (
  <div className="relative flex h-full w-full flex-col">
    <div ref={containerRef} className="min-h-0 w-full flex-[4]" />

    {hasPane && activePaneDef && (
      <div className="flex min-h-[120px] flex-1 flex-col border-t border-[var(--border)]">
        <div className="shrink-0 px-2">
          <Tabs
            variant="underline"
            // 评审 R-1：页签用短标签（指标键大写：MACD/RSI/KDJ/VOL），与 §2 示意图一致；
            // 注册表 d.label 为长名（如「RSI 相对强弱」），会撑宽 20% 容器顶部，违背「靠左紧凑」目标。
            tabs={enabledPaneDefs.map((d) => ({ key: d.key, label: d.key.toUpperCase() }))}
            active={activePaneDef.key}
            onChange={(k) => onPaneTabChange?.(k as IndicatorKey)}
          />
        </div>
        <div className="relative min-h-0 flex-1">
          <IndicatorPane
            key={activePaneDef.key}
            candles={candles}
            def={activePaneDef}
            theme={theme}
            sync={syncRef.current as SyncGroup}
            showTimeAxis
          />
        </div>
      </div>
    )}

    {/* loading / error 覆盖层：原样保留（absolute，不受 flex 影响） */}
  </div>
);
```

### 9.5 `components/Chart/ChartWorkspace.tsx` —— 透传 + 下区页签靠左
```tsx
// 从 store 增取：const { …, paneTab, setPaneTab } = useAppStore();
// 上区 price 视图 CandleChart 增 props：
<CandleChart
  /* …既有 props… */
  paneTab={paneTab}
  onPaneTabChange={setPaneTab}
/>

// 下区「成交记录/当前持仓」Tabs 改 underline（需求 2 靠左）：
<Tabs
  variant="underline"
  tabs={BOTTOM_TABS}
  active={bottomTab}
  onChange={(k) => setBottomTab(k as BottomTab)}
/>
```

### 9.6 `lib/store.ts` —— 字段 / 持久化 / 迁移
```ts
// state 初值：paneTab: null,
// action：setPaneTab: (paneTab) => set({ paneTab }),
// partialize：增 paneTab: s.paneTab,
// version：4,
// migrate：if (version < 4) next = { ...next, paneTab: null };
```

---

> 本方案仅产出设计文档，不含实现代码；落地由 Subtask #2 严格按 §4 文件清单执行，先写 §7.1 测试再写实现，并通过 §7.2 门禁。

---

## 10. 评审结论（Review Verdict）

**结论：有条件通过（Approved with Conditions）。**

本方案经四维度（可行性 / 完整性 / 一致性 / 规模适配）逐节评审，技术判断正确、范围适配恰当、与现有架构和约定一致，**无 P0**。识别出的 **3 个 P1（R-1 短标签、R-2 underline 溢出裁切、R-3 单显切换主图重排）已于 v2 正文就地修复**（§3.2(D)/(E)、§5.1、§5.2、§7.3、§8、§9.2、§9.4），符合 Definition of Done「无 P0/P1 残留」。

「有条件」的条件均为**非阻塞**，留待 Subtask #2 实现时遵从：

1. **必须按 v2 修订实现**（强约束）：副图页签用 `d.key.toUpperCase()` 短标签（R-1）；underline 变体容器**不带** `overflow-x-auto`（R-2）；对齐 effect 依赖含 `activeKey`、知悉并接受切页签的一次性重排（R-3）。
2. **建议（P2，可不阻塞合并）**：
   - **R-4**：评估是否去除 `IndicatorPane` 左上角与页签重复的指标名（保留 `paramsHint`）；
   - **R-5**：择期为共享 `Tabs` 两变体补 `role="tablist"/"tab"` + `aria-selected` + 方向键，呼应第 1 轮「人机交互最佳实践」（本轮非回归，可单独成项）；
   - **R-6**：`setPaneTab` 可窄化为 `(key: IndicatorKey) => void`（`null` 入参本轮无调用方），从简即可；
   - **R-7**：实现期在 1m 周期 + 小屏目测 20% 副图容器（含 ≈28px 页签行）不塌陷、pane 仍可读。
3. **门禁（强约束）**：合并前 `pnpm --filter web typecheck`、`pnpm --filter web build`、`pnpm --filter web test`（含新增 `paneTabs.test.ts` 五用例与既有全绿）必须通过（§7.2）。

满足上述条件即视为本轮交付达标，可进入 Subtask #2 代码开发。

> 评审签署：Senior Reviewer（Anthropic 工程团队角色）· 2026-06-14 · 文档版本 **v2**
