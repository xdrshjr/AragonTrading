# 策略「运行回测」配置弹窗 — 开发方案 (spec)

- **Feature slug**: `strategy-backtest-config-modal`
- **版本**: v1（方案设计，待 Subtask #1 评审升 v2）
- **日期**: 2026-07-07
- **作者角色**: Solution Architect（Subtask #0）
- **关联任务板**: `.agentmesh/task-tge-bb5506d5-24a2-417c-ad22-d039f7e1b88e/task-board.md`

> 评审记录：（占位，Subtask #1 在此填写逐节评审与 P0/P1 修复清单）

---

## 一、概述（Overview）

当前交易策略面板（右侧 `StrategyPanel` → `StrategyDetail`）的「运行回测」按钮是**一键直跑**：点击后立即用**全局图表状态**里的周期（`bar`）与回测区间（`backtestStartTs` / `backtestEndTs`）去调 `POST /strategies/{id}/backtest`。而该区间的默认值在 `apps/web/lib/store.ts` 中被硬编码为「近 30 天」（`DEFAULT_RANGE_DAYS = 30`，模块加载时以 `Date.now()` 计算一次），周期则沿用主图当前所选。这就是用户反馈的「固定使用了某一段 K 线 / 某一段区间」的根因：回测无法在运行前独立设定区间、周期与交易成本，只能被动继承图表恰好显示的那段，既不专业也不稳健，且结果只在右侧内联区渲染，缺少一个聚焦的运行工作台。

本方案在点击「运行回测」时弹出一个**回测配置与结果弹窗**（`BacktestConfigModal`）。弹窗内用户可显式修改：**回测区间**（预设近 7/30/90/180 天、近 1 年，或自定义起止日期）、**K 线周期**（1m/5m/15m/30m/1H/4H/1D）、**初始资金**、**手续费（taker bps）**、**滑点（bps）**、**杠杆**。「运行」按钮在弹窗内触发回测，运行态、覆盖度透明提示、错误态、以及**回测绩效指标**都在**同一弹窗**内呈现。指标不仅是既有 7 项，还复用后端已存在且经测试的 `engine/metrics_ext.compute_extended_metrics` 追加**专业风险/质量指标**（年化收益 CAGR、年化波动、Sortino、Calmar、最长水下根数、在场占比、平均/最大连亏、平均持仓根数、交易频率等），以「更加专业、更加真实、更加稳健」地评估策略。

关键判断：**后端 `POST /strategies/{id}/backtest` 已经支持 `initialCapital` / `fees` / `slippage` / `leverage` / `bar` / `startTs` / `endTs` 全部入参**（见 `schemas.py::BacktestReq`、`services.run_single_backtest`），前端 `api.backtest` 客户端也已带齐这些字段。因此本需求的**主体是前端**（新增弹窗 + 配置态 + 结果渲染），后端仅做**一处加法式增强**：在回测响应里附带 `extendedMetrics`（复用现成纯函数，零 schema/DB 变更、对既有 7 项契约与所有旧调用方零影响）。该设计把改动面和回归风险压到最小，同时显著提升专业度与人机交互体验。

---

## 二、技术方案（Technical Design）

### 2.1 现状与根因

调用链（简单回测）当前为：

```
StrategyDetail「运行回测」按钮 onClick
  → onApplyAndBacktest(params)                      // StrategyDetail.tsx:264
  → StrategyPanel.backtest.mutate({id, params, showRunRange:true})   // StrategyPanel.tsx:582
  → runBacktest(id, params, instId, bar, backtestStartTs, backtestEndTs)  // StrategyPanel.tsx:765
  → api.backtest(id, {instId, bar, startTs, endTs})  // lib/api.ts:139（仅传 4 个字段）
  → POST /strategies/{id}/backtest                  // api/strategies.py:100
  → services.run_single_backtest(...)               // agent/services.py:238
  → engine.run_backtest(...) + compute_metrics(...) // engine/backtester.py:66 / metrics.py:20
```

问题点：`bar` 来自全局 store（主图周期），`backtestStartTs/backtestEndTs` 默认「近 30 天」且随图表联动，`initialCapital/fees/slippage/leverage` **前端从不传**（后端回落 `settings` 默认）。用户无法在运行前控制这些，回测退化为「对主图当前那段固定 K 线跑一遍」。

### 2.2 目标与设计原则

1. **配置前置**：点「运行回测」→ 弹窗 → 用户改区间/周期/成本/杠杆 → 弹窗内运行。
2. **就地看结果**：运行态、覆盖度、指标、净值缩略图、错误态都在弹窗内。
3. **复用优先**：后端端点、`api.backtest`、`Modal`/`Input`/`Button`/`Badge`/`Tabs` 原语、`compute_extended_metrics`、`downloadCsv`/`equityToCsv` 全部复用；不引新依赖。
4. **加法式、可回退**：后端只加 `extendedMetrics` 字段；前端 `Metrics` 契约不动，扩展指标走新 `ExtendedMetrics` 可选类型；旧内联区行为保留。
5. **原子/因子/组合三类策略统一**：一个弹窗、一个 `api.backtest`（端点内部按 `template_key` 分流组合），组合策略也能配置区间/周期/成本。
6. **不劫持主图**：弹窗只覆盖 `bar/区间/成本` 传给回测请求，不改全局 `instId`（标的仍是主图选中标的，弹窗只读展示）；避免与图表/多样本回测状态互相污染。

### 2.3 架构与数据流

单一数据源（single source of truth）：沿用 `StrategyPanel` 的 `backtest` react-query mutation，弹窗作为**它的可视化前台**——弹窗通过 props 观察 `running`/`result`/`error`，通过回调 `onRun(cfg)` 触发同一个 mutation。这样内联绩效区与弹窗共享同一次运行结果，不产生第二套状态。

时序（简单回测）：

```
用户点「运行回测」
  → StrategyDetail 打开弹窗（本地 configOpen=true），用 store.backtestConfig 或默认+当前区间做种子
用户在弹窗调整 cfg（区间预设/自定义、周期、成本、杠杆）→ 前端即时校验 + 估算根数 + 样本内外提示
用户点弹窗内「运行」
  → onRun(cfg)：持久化 cfg 到 store.backtestConfig（乐观）→ backtest.mutate({id, params, config: cfg})
  → runBacktest：若 params 变更先 api.updateStrategy → api.backtest(id, {instId, bar=cfg.bar, startTs/endTs=cfg, initialCapital/fees/slippage/leverage=cfg})
  → 后端跑回测，返回 {runId, metrics, extendedMetrics, effectiveCandleCount, requestedBars, truncated, combo?}
  → mutation.onSuccess：setRunId/setMetrics/setComboResult/setCoverage/setChartView("run-range")/setBacktestConfig
  → 弹窗 props（running=false, result=data）更新 → 渲染指标网格 + 覆盖横幅 + 净值缩略图 + 操作按钮
```

### 2.4 弹窗 UI 结构与交互（HCI）

弹窗采用「上配置、下结果」的单面板布局（`Modal` 原语，宽 `max-w-2xl`，`max-h-[85vh]` 内部滚动）：

- **Header**：标题「运行回测」+ 副标题 `策略名 · 标的(instId) · 只读`；右上角关闭按钮（`aria-label="关闭"`）。
- **配置区**（分组卡片）：
  1. **回测区间**：预设 chips（近7天/近30天/近90天/近180天/近1年/自定义）+ 两个日期输入（`<input type="datetime-local">`，仅「自定义」时可编辑；预设选中时显示派生的起止只读）。选预设时以打开弹窗那一刻的 `now` 重新派生区间（保证「近30天」始终新鲜）。
  2. **K 线周期**：7 个 chips（`1m … 1D`），复用 `ChartToolbar` 的 `BARS` 口径。
  3. **成本与杠杆**：初始资金、手续费(bps)、滑点(bps)、杠杆四个数字输入，带单位与提示（默认值：资金 10000、费 5bps、滑点 2bps、杠杆 1x，与后端 `settings` 对齐）。
- **即时反馈**：显示「预计 ≈N 根 K 线」（`estimateBars`）；对 `factor_score` 策略若估算根数 < `normWindow+60` 给黄色告警「区间过短将安静空仓」；`startTs>=endTs` 或数值非法时禁用「运行」并给红字原因。
- **样本内/外提示**：若策略带 `provenance`（因子权重训练区间），提示当前回测区间是否落在训练区间内（沿用 `StrategyDetail` 现有逻辑口径）。
- **运行按钮**：`variant="primary"`，运行中显示「回测中…」并禁用；`disabled` 时 title 给原因。
- **结果区**：
  - 空态（未运行）：占位提示「配置区间与参数后点运行」。
  - 运行态：`Skeleton` 骨架网格。
  - 成功态：**覆盖横幅**（`truncated` 时黄条「仅覆盖最近 x/y 根」）+ **绩效指标网格**（`BacktestMetricsGrid`，见 2.5）+ **净值缩略图**（`EquitySparkline`，自绘 SVG，`api.equity(runId)` 拉取）+ 操作条（导出净值 CSV / 在主图查看 / 关闭）。
  - 错误态：红色错误卡片，展示 `error.message`。
- **无障碍**：完全复用 `Modal` 的焦点陷阱、Esc 关闭、初始聚焦、body 滚动锁定、`aria-modal`/`aria-labelledby`；数字用 `tabular` 等宽；颜色走 `var(--long)/var(--short)/var(--text)` 主题变量，明暗自适应。

### 2.5 专业指标（复用现成模块）

- 既有 7 项（`compute_metrics`）：总收益率、胜率、盈亏比、最大回撤、夏普、成交笔数、平均每笔。
- 追加（`compute_extended_metrics`，已存在且经 `test_metrics_ext.py` 覆盖）：`annualizedReturn`(CAGR)、`annualizedVolatility`、`sortino`、`calmar`、`maxDrawdownBars`、`maxDrawdownSecs`、`timeInMarketPct`、`avgWin`、`avgLoss`、`payoffRatio`、`largestWin`、`largestLoss`、`maxConsecutiveWins`、`maxConsecutiveLosses`、`avgHoldBars`、`tradesPerWeek`。
- 口径与 `metrics.py` 逐字一致（ddof=1、`BARS_PER_YEAR` 年化、平仓笔 `side=="close"`），无「策略指标漂移」风险。
- 弹窗网格分两组呈现：**收益/风险**（总收益、年化、年化波动、夏普、Sortino、Calmar、最大回撤、最长水下、在场占比）与**交易质量**（胜率、盈亏比、Payoff、成交笔数、平均每笔、平均/最大连亏、平均持仓、周均交易）。缺失值（如组合路径未附带或旧数据）统一渲染「—」。

### 2.6 后端最小增强

在 `agent/services.py::run_single_backtest` 的原子/因子返回处附带扩展指标（`_execute_and_persist` 已在内存里拿到 `result.equity_points` 与 `result.trades`）：

```python
run, result = await _execute_and_persist(...)
extended = compute_extended_metrics(result.equity_points, result.trades, bar, costs[0])  # costs[0]=initial_capital
return {"runId": run.id, "metrics": result.metrics, "extendedMetrics": extended,
        **_coverage_fields(candles, bar=bar, start_ts=start_ts, end_ts=end_ts)}
```

组合路径（`_run_saved_combo`）在能拿到组合组合级 `equity_points`/`trades` 时同样附带 `extendedMetrics`；形状不满足则安全省略（前端渲染「—」）。`extendedMetrics` **不落库、不进 `metrics_json`**（保持既有落库契约不变），仅随响应回传（响应为路由手搓 dict、无 `response_model`，附加键可透传）。

---

## 三、文件 / 模块变更计划（File / Module Change Plan）

| 文件 | 类型 | 一句话意图 |
|---|---|---|
| `apps/web/components/Strategy/BacktestConfigModal.tsx` | 新增 | 回测配置+运行+结果弹窗主组件（配置区/运行/结果区，复用 `Modal` 原语） |
| `apps/web/components/Strategy/BacktestMetricsGrid.tsx` | 新增 | 弹窗内扩展绩效指标网格（7 项 + 扩展项，缺失渲染「—」） |
| `apps/web/components/Strategy/EquitySparkline.tsx` | 新增 | 自绘 SVG 净值曲线缩略图（`api.equity(runId)` → 纯几何映射） |
| `apps/web/lib/backtestConfig.ts` | 新增 | 纯函数：区间预设→起止、`barToSeconds`、`estimateBars`、`validateConfig`、默认成本、标签映射 |
| `apps/web/lib/backtestConfig.test.ts` | 新增 | `backtestConfig.ts` 单测（预设换算/估算/校验边界） |
| `apps/web/components/Strategy/BacktestConfigModal.test.tsx` | 新增 | 弹窗组件测试（打开/校验禁用/运行回调/结果渲染/关闭 a11y） |
| `apps/web/components/Strategy/StrategyDetail.tsx` | 修改 | 「运行回测」改为打开弹窗；渲染 `BacktestConfigModal`；接入新 props |
| `apps/web/components/Strategy/ComboStrategyDetail.tsx` | 修改 | 组合「运行回测」同样改为打开弹窗（无策略参数表单，仅区间/周期/成本） |
| `apps/web/components/Strategy/StrategyPanel.tsx` | 修改 | `backtest` mutation 接受 `config` 覆盖；`onSuccess` 持久化 config；下发 `running/result/error/onRun/backtestConfig` 给两个 Detail |
| `apps/web/lib/store.ts` | 修改 | 新增持久化 `backtestConfig: BacktestConfig \| null` + `setBacktestConfig`；加入 `partialize` |
| `apps/web/lib/types.ts` | 修改 | 新增 `ExtendedMetrics` 接口；`BacktestResult` 增 `extendedMetrics?`；新增 `BacktestConfig`/`RangePreset`/`Bar` 契约 |
| `apps/web/lib/api.ts` | 不改 | `api.backtest` 已支持全部字段，仅需在注释确认 `extendedMetrics` 为可选响应字段（如需集中类型可微调返回类型引用） |
| `services/quant/app/agent/services.py` | 修改 | `run_single_backtest`（及组合路径可行时）返回附带 `extendedMetrics`（复用 `compute_extended_metrics`） |
| `services/quant/tests/test_backtest_extended_response.py` | 新增 | 校验 `/strategies/{id}/backtest` 响应含 `extendedMetrics` 且键齐全、旧 `metrics` 契约不变 |

> 约束：Subtask #2 严格按本表落地，不做超出方案的额外重构。若实施中发现方案缺陷，记入文末「实施过程发现的方案缺陷」段。

---

## 四、接口设计（Interface Design）

### 4.1 REST（无破坏性变更）

- 端点不变：`POST /api/py/strategies/{id}/backtest`，请求体 `BacktestReq`（已含 `instId,bar,limit?,startTs,endTs,initialCapital?,fees?,slippage?,leverage?`）。前端本次开始**真正传** `initialCapital/fees/slippage/leverage`。
- 响应新增可选字段：

```jsonc
{
  "runId": 123,
  "metrics": { "totalReturn": 0.12, "winRate": 0.55, "profitFactor": 1.8,
               "maxDrawdown": -0.08, "sharpe": 1.3, "tradeCount": 42, "avgPnl": 3.1 },
  "extendedMetrics": {            // 新增，可选；组合路径可能缺省
    "annualizedReturn": 0.31, "annualizedVolatility": 0.42, "sortino": 1.9, "calmar": 3.8,
    "maxDrawdownBars": 57, "maxDrawdownSecs": 205200, "timeInMarketPct": 0.63,
    "avgWin": 6.2, "avgLoss": 3.1, "payoffRatio": 2.0, "largestWin": 40.1, "largestLoss": -22.3,
    "maxConsecutiveWins": 6, "maxConsecutiveLosses": 3, "avgHoldBars": 8.4, "tradesPerWeek": 5.2
  },
  "effectiveCandleCount": 720, "requestedBars": 720, "truncated": false,
  "combo": null
}
```

### 4.2 前端 TS 契约（`lib/types.ts`）

```ts
export type Bar = "1m" | "5m" | "15m" | "30m" | "1H" | "4H" | "1D";
export type RangePreset = "7d" | "30d" | "90d" | "180d" | "1y" | "custom";

export interface BacktestConfig {
  bar: Bar;
  rangePreset: RangePreset;
  startTs: number;          // UTC 秒；preset!=="custom" 时打开弹窗按 now 重派生
  endTs: number;
  initialCapital: number;   // > 0
  feesBps: number;          // taker_fee_bps, >= 0
  slippageBps: number;      // slippage_bps, >= 0
  leverage: number;         // >= 1
}

export interface ExtendedMetrics {
  annualizedReturn: number; annualizedVolatility: number; sortino: number; calmar: number;
  maxDrawdownBars: number; maxDrawdownSecs: number; timeInMarketPct: number;
  avgWin: number; avgLoss: number; payoffRatio: number; largestWin: number; largestLoss: number;
  maxConsecutiveWins: number; maxConsecutiveLosses: number; avgHoldBars: number; tradesPerWeek: number;
}
// BacktestResult 增: extendedMetrics?: ExtendedMetrics;
```

### 4.3 纯函数（`lib/backtestConfig.ts`）

```ts
export const BARS: Bar[] = ["1m","5m","15m","30m","1H","4H","1D"];
export const BAR_LABEL: Record<Bar,string>;
export const PRESET_LABEL: Record<RangePreset,string>;
export const DEFAULT_BACKTEST_CONFIG: Omit<BacktestConfig,"startTs"|"endTs">; // bar:"1H",preset:"30d",cap:10000,fee:5,slip:2,lev:1
export function barToSeconds(bar: Bar): number;                 // 与后端 mtf.bar_to_seconds 同表
export function presetToRange(preset: Exclude<RangePreset,"custom">, nowSec: number): { startTs: number; endTs: number };
export function estimateBars(bar: Bar, startTs: number, endTs: number): number;  // floor((end-start)/sec)+1
export function validateConfig(cfg: BacktestConfig, warmupBars?: number):
  { ok: boolean; errors: string[]; warnings: string[] };        // start<end、cap>0、fee/slip>=0、lev>=1；warmup 告警
```

### 4.4 组件 props（关键接口）

```ts
// BacktestConfigModal.tsx
export default function BacktestConfigModal(props: {
  open: boolean; onClose: () => void;
  strategyName: string; instId: string;
  warmupBars?: number;                          // factor_score: normWindow+60；其它可省
  provenanceHint?: { startTs: number; endTs: number; instId?: string; bar?: string } | null;
  initialConfig: BacktestConfig;                // 种子（store.backtestConfig 或默认+当前区间）
  running: boolean;                             // = backtest.isPending
  result?: BacktestResult;                      // = backtest.data（本会话最近一次成功）
  errorMessage?: string | null;
  onRun: (cfg: BacktestConfig) => void;         // 触发面板 mutation
}): JSX.Element | null;

// StrategyDetail.tsx 新增 props（替换 simple 模式的 onApplyAndBacktest）
//   backtestConfig: BacktestConfig; onRunBacktest: (params: Params, cfg: BacktestConfig) => void;
//   backtestRunning: boolean; backtestResult?: BacktestResult; backtestError?: string | null;
// ComboStrategyDetail.tsx 新增 props：onRunBacktest: (cfg: BacktestConfig) => void; 其余同上（无 params）
```

---

## 五、数据模型（Data Model）

- **无 DB schema 变更**。回测 run 仍走 `create_run`（`config_json` 已记录 `initialCapital/fees/slippage/leverage/limit/startTs/endTs/effectiveCandleCount`，本次由前端真实传入而非默认）+ `persist_backtest_result`（`metrics_json` 仍是既有 7 项）。`extendedMetrics` 仅随响应回传、不落库。
- **前端 store（`aragon-ui` persist）新增**：

```ts
backtestConfig: BacktestConfig | null;                 // 默认 null（新装走初始化器，默认值写在此处）
setBacktestConfig: (cfg: BacktestConfig) => void;      // set({ backtestConfig })
// partialize 增: backtestConfig: s.backtestConfig
```

- **内存形状**：`BacktestConfig`（4.2）；`ExtendedMetrics`（4.2）；净值缩略图消费 `EquityPoint[]`（既有 `api.equity` 返回）。
- **种子逻辑**：打开弹窗时 `cfg = store.backtestConfig ?? { ...DEFAULT_BACKTEST_CONFIG, bar: 当前图表 bar, rangePreset: "custom", startTs: backtestStartTs, endTs: backtestEndTs }`；若 `cfg.rangePreset !== "custom"`，`useEffect(open)` 内以 `now` 重派生 `startTs/endTs`。

---

## 六、测试与验收（Testing & Acceptance）

### 6.1 单元/组件测试

1. `backtestConfig.test.ts`（vitest）：
   - `presetToRange` 各预设跨度正确（7d=7*86400，1y=365*86400），`endTs≈now`。
   - `estimateBars` 边界：区间恰好 1 根、跨周期换算、start==end。
   - `validateConfig`：`start>=end`→error；`cap<=0`/`fee<0`/`lev<1`→error；`estimateBars<warmupBars`→warning 且 `ok=true`。
2. `BacktestConfigModal.test.tsx`（vitest，`NODE_ENV=test`，见记忆「Aragon web vitest NODE_ENV」）：
   - 打开渲染标题/标的/默认配置；非法区间禁用「运行」并显示原因。
   - 点「运行」以当前 cfg 调 `onRun`（断言入参）。
   - `running=true` 显示骨架；传入 `result` 后渲染指标网格与覆盖横幅；`errorMessage` 渲染错误态。
   - Esc/点遮罩触发 `onClose`（复用 `Modal` a11y）。
3. `test_backtest_extended_response.py`（pytest，复用既有回测集成夹具）：
   - `/strategies/{id}/backtest` 响应含 `extendedMetrics` 且 16 键齐全、类型正确。
   - 既有 `metrics` 7 键不变（回归护栏）。
   - 退化输入（空区间/极短区间）不抛异常、扩展项回落 0。

### 6.2 手动验收标准（Definition of Done 对齐需求）

- [ ] 点「运行回测」弹出配置弹窗，而非立即直跑。
- [ ] 可修改回测区间（预设 + 自定义起止）、K 线周期、初始资金、手续费、滑点、杠杆。
- [ ] 「运行」在弹窗内触发回测并显示运行态。
- [ ] 弹窗内显示绩效指标（含专业扩展项）+ 覆盖度提示 + 净值缩略图。
- [ ] 原子、因子、组合三类策略均可用该弹窗回测。
- [ ] 修改成本/杠杆确实影响结果（如提高费率总收益下降），证明参数生效。
- [ ] 明暗主题、键盘可达、无越界横向滚动；`pnpm --filter web test` 与 `pytest` 全绿；`pnpm --filter web build` 通过。

---

## 七、风险与缓解（Risks & Mitigations）

| 风险 | 影响 | 缓解 |
|---|---|---|
| 弹窗与内联绩效区双态不一致 | 用户困惑 | 单一 mutation 为唯一数据源，弹窗只读其 `isPending/data/error`，不另起状态 |
| 长区间 + 小周期取数过重（如 1y×1m） | 后端 413 / 慢 | 前端 `estimateBars` 预警 + 后端既有 `backtest_max_bars=20000` 二次夹取 + `truncated` 透明回传横幅 |
| 组合路径 `extendedMetrics` 形状不齐 | 指标缺失 | 组合路径能取到组合级 equity/trades 才附带，否则安全省略；前端缺失渲染「—」 |
| `datetime-local` 时区/取值歧义 | 区间偏差 | 统一按本地时间解析后转 UTC 秒（`Math.floor(Date.parse(v)/1000)`），并以只读回显起止 UTC 供核对 |
| 因子策略区间过短安静空仓被误读为「无收益」 | 误判策略 | 估算根数 < `normWindow+60` 时黄色告警，沿用 `StrategyDetail` 现有最小根数口径 |
| 修改 `StrategyPanel`/`StrategyDetail` 触碰组合/多样本/纸上交易共存逻辑 | 回归 | 仅替换 simple 模式「运行回测」入口；walk-forward/多样本/paper 分支不动；组件测试守回归 |
| 持久化 `backtestConfig` 让「近30天」变陈旧 | 区间过期 | 只持久化 preset 与自定义值；预设在打开弹窗时按 `now` 重派生，绝不持久化陈旧绝对时间当预设结果 |

---

## 八、非目标与后续扩展（Non-goals / Future）

- 本期不做：资金费率（funding）成本建模、逐笔滑点分布、蒙特卡洛/参数网格寻优、回测任务异步化与进度流（当前同步足够；超大工作量已有 413 护栏）。
- 后续可选：把弹窗配置一键带入 Walk-forward / 多样本 / 对比，形成统一「回测运行配置」；把 `extendedMetrics` 落库以便历史复看；标的选择器（当前标的固定为主图选中标的）。

---

> 评审结论：（占位，Subtask #1 在此填写结论与版本升级说明）

> 实施过程发现的方案缺陷：（占位，Subtask #2 在此回填）
