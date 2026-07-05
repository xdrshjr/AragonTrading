# AI 自动交易 · 回测分析增强包（AI Auto-Trading Backtest · Analytics Enhancements）开发方案

- **Feature key**: `ai-auto-trading-backtest-analytics`
- **文档版本**: **v2**（iteration 2 · 方案设计节点产出 v1，评审节点复核并加固为 v2，含 `## 评审记录` 与 `## 评审结论`；本节点只改 `spec.md`，不写实现代码、不改任何源文件、不 `git commit`）。所有 as-built 锚点均经真实代码逐条核验，5 个 P1 已在正文内直接修订。
- **作者**: 方案设计节点（solution architect）
- **上游基线（均已实现并 commit）**:
  - **AI 自动交易（第三种自动交易类型）** — `docs/plans/ai-auto-trading/spec.md`，commit `4b13b69`。AI Agent 作为多策略信号的综合决策者，`LiveExecutor` 守护执行 + 轮次滚动摘要上下文。
  - **综合研判增强包** — `docs/plans/ai-auto-trading-enhancements/spec.md`，commit `953cd5d`。E1 市场体制上下文 / E2 共识度门禁 / E3 决策可观测性 / E4 会话级 OCO。
  - **AI 自动交易 · 回测模式** — `docs/plans/ai-auto-trading-backtest/spec.md` v2，commit `b9f1783`（27 文件）。在启动弹窗加「实盘 / 回测」段控；回测复用**逐字相同**的 AI 决策链路，把执行落地从 `LiveExecutor` 真单换成 `engine/simulator.SimBroker` 模拟撮合、数据源从实时 WS 换成 `load_range_candles` 历史区间；后端 `ai_auto/{backtest,backtest_manager,backtest_frames}.py` + `api/ai_auto.py` 回测子路由 + `engine/metrics.compute_metrics` 七项指标；前端 `lib/aiBacktest{,Types,Api}.ts` + `useAiBacktestJob` + `components/Chart/AiBacktestModal.tsx`（运行态进度头/决策日志流 / 完成态指标卡 + AI 统计 + 权益曲线 + 成交表 + 决策/摘要时间线）。
- **本轮需求（原文）**: 「继续完善，AI 自动交易，对应的弹窗面板中，可以选择『回测模式』，如果选择回测模式，就启动对应的回测，但是是 AI 自动交易这种形式的回测，AI 要在回测数据上去跑，并且显示对应的执行情况，完成后也要显示各种回测指标结果。美观优雅，顶级，符合人机交互最佳实践。」
- **本轮定位（相对上一轮的根本区别）**: 回测模式的**主干功能已在 `b9f1783` 落地并可用**（弹窗可选回测、AI 逐根跑历史、流式执行情况、完成态指标卡）。本轮需求关键词为「**继续完善**」，其两处显式强调——「**完成后也要显示各种回测指标结果**」与「**美观优雅、顶级、符合人机交互最佳实践**」——正是主干版可继续深化的两个方向。故本方案是一个**边界清晰、纯增量、默认等价**的**分析增强包**：在不改变已交付回测行为（决策脑、SimBroker 执行、护栏、落库口径逐字不变、既有单测全绿）的前提下，把「各种回测指标」从当前七项**扩展为专业量化回测的全套风险/质量指标 + 买入持有基准对比**，并把完成态结果面板从「长列表」升级为「**分页签、KPI 概览、水下回撤图、周期收益分布、一键导出**」的顶级可观测仪表盘。**不新增真单路径、不改实盘链路、不引入超出增强的额外重构。**

---

## 评审记录（Review Notes · v2）

> 评审维度：**可行性 / 完备性 / 一致性（对齐 `CLAUDE.md` 与既有代码约定）/ 合理规模**。评审前用 4 个并行核验代理对全部 as-built 复用锚点（`engine/metrics`、`engine/simulator`、`ai_auto/backtest`、`db/repositories`、`schemas`、前端 `lib/export`、`ui/Tabs`、`lib/types`、`lib/aiBacktestTypes`、`lib/useAiBacktestJob`、`Chart/EquityChart`、`Chart/AiBacktestModal`）逐条比对真实代码。
>
> **总体判断**：方案**方向正确、边界清晰、默认等价、锚点绝大多数准确**——尤其确认 `_run_loop` 迭代 `range(first, n)`、`SimBroker.mark` 逐根 append，故 `equity_points` 窗口与基准 `main_df.iloc[first:]` 窗口**天然对齐**（原「窗口错位致 alpha 误导」的隐患不存在，已核实）。无 P0。以下 5 个 P1 已在正文对应小节直接修订（内联以 `[P1-x]` 标注，落点见下表「修订位置」列），6 个 P2 列为落地条件（见 `## 评审结论`）。

### P1（阻断级；v2 已在正文修订）

| # | 维度 | 问题 | 修订位置 |
|---|---|---|---|
| P1-1 | 一致性/正确性 | **`profitFactor` 被错标为「盈亏比」，且与 B1 新增 `payoffRatio` 撞中文名**。`metrics.py:59` 第七项真实键为 `profitFactor`＝毛盈利/毛亏损（**盈利因子**），而 B1 新增 `payoffRatio`＝平均盈利/平均亏损（**盈亏比/赔率**）。二者若都标「盈亏比」，仪表盘会出现两张同名卡（MetricsCards 的 profitFactor + ExtendedMetricsGrid 的 payoffRatio），误导用户。 | §1、§2.2、§2.5 |
| P1-2 | 正确性 | **夏普/波动率的方差口径未固定，与 `metrics.py` 不可比**。`metrics.py:43` 用**样本方差 ddof=1**（`Σ(r-mean)²/(n-1)`）算 sharpe。BenchmarkCompareCard 要把「策略 sharpe（来自 `metrics.py`）」与「基准 sharpe（来自新助手）」并排；若新助手用总体方差或不同均值口径，两条腿不可比、会误导投资判断。原方案只承诺「benchmark ↔ metrics_ext 不复制两份」，**漏了真正的对比腿 `metrics.py`**。 | §2.2、§2.3、§7 |
| P1-3 | 正确性/一致性 | **B1 交易质量指标的「平仓笔」口径未与 `metrics.py` 对齐**。`metrics.py:52` 以 `t.side == "close"` 选平仓笔；`TradeRecord` 用 `side ∈ {long,short,close}` 区分开/平（**无** `kind`/`action` 字段，开仓 `pnl=0`）。原 §2.2 只写「平仓笔 pnl>0/pnl<0」，若实现者在**全部** trades 上按 pnl 符号过滤，会把开仓笔混入并与 `metrics.py` 的 `tradeCount/winRate` 口径漂移。 | §2.2 |
| P1-4 | 可行性/HCI | **JSON 导出复用 `downloadCsv` 会带 UTF-8 BOM + 错误 MIME，破坏 `JSON.parse` 回读**。`export.ts:77` 的 `downloadCsv` 前置 BOM 且 MIME 固定 `text/csv`；带前导 BOM 的 JSON 会让严格 `JSON.parse('﻿…')` 抛错，扩展名/MIME 也不符，令「导出完整结果 JSON」无法被再次读入。 | §2.5、§三、§4.3、§6.2 |
| P1-5 | 一致性/合理规模 | **`AiBacktestModal.tsx` 已 409 行，内联新增会逼近/突破 `CLAUDE.md` 800 行硬阈值**。本增强要在该文件新增 KPIHero/ExtendedMetricsGrid/BenchmarkCompareCard/UnderwaterChart/PeriodReturnBars/导出条 + 分页签重构（约 +250~350 行），叠加既有 409 行有超 800 风险；原 §2.4 仅「或同目录」软性提及。 | §2.4、§2.5、§三 |

### P2（非阻断；列为落地条件，见 `## 评审结论`）

| # | 维度 | 问题 | 建议 |
|---|---|---|---|
| P2-1 | 可行性 | `bar_to_seconds` 来源未指名，恐被重造。它已存在于 `engine/mtf.py`（读 `okx/types.py::BAR_SECONDS`），且 `ai_auto/backtest.py` 已 import。 | §2.0 锚点表补一行；`maxDrawdownSecs` 直接复用 `mtf.bar_to_seconds`（注意其对未知 bar 抛 `ValueError`，回测 bar 已在上游校验，安全）。已在 §2.2 补注。 |
| P2-2 | 一致性(CLAUDE.md §5) | 派生失败的 `try/except` 不能静默吞异常。 | fallback 时 `log.warning`（含 jobId 与异常）再回落空 dict。已在 §2.3/§7 补注。 |
| P2-3 | 完备性 | 需确认 runner 结果 dict → `AiBacktestResultOut` 的 **GET 快照序列化路径**真正带上两键（WS `result` 事件透传整包 dict 已 OK；`aiStats` 是**类型化**子模型 `AiBacktestAiStatsOut`，两新键须为**带默认**的可选 dict 字段方能向后兼容）。 | §4.1/§三 补一句确认；§6.1 断言 GET 快照亦含两键。已补。 |
| P2-4 | 完备性(欠规格) | `periodReturns` 自动降密只写「桶数 > 阈值」，未给阈值，也未处理多年回测 week 仍过密。 | 给出具体阈值并把最粗粒度扩到 month/quarter，或明确封顶到 week + 轴稀疏标注。已在 §2.4 明确。 |
| P2-5 | 一致性(锚点小订正) | `EquityPoint` 实为 `{ts,equity,drawdown,transient?}`（多一个可选 `transient?`）；`EquityChart` 的 `overlays` 分支会**归一化为百分比**，而单条 `equity` 走**绝对轴**——benchmark 必须只在单条 `equity`（绝对轴、非 overlays）分支渲染。 | §2.5 注明 benchmark 仅在 equity 分支渲染；类型沿用含 `transient?` 的 `EquityPoint`。已补。 |
| P2-6 | 测试 | 既有 runner 测试若对 `result`/`metrics_json` 做「精确键集/整包相等」断言，新增两键会破坏（原方案假设为子集式断言）。 | §6.1 增一句：若既有断言为精确键集相等，则同步更新为超集断言（属测试改动、非行为变更）。已补。 |

### 锚点核验结论（供开发节点信任本 spec 的 file:symbol 引用）

- **准确**：`compute_metrics(equity_points,trades,bar,initial_capital)` 七键、`BARS_PER_YEAR`（默认 8760）、`SimBroker.{equity_points,trades,pos.side}`、`EquityRecord{ts,equity,drawdown}`、`TradeRecord`（含 `side/pnl/ts/qty/fee`）、`AiBacktestRunner._finalize/_build_result/_persist/_aggregate_stats/_run_loop/_first_decision_index`（行号均命中）、`persist_backtest_result(... metrics ...)` 落 `metrics_json`（自由 JSON、无迁移）、`ui/Tabs{tabs,active,onChange,variant}`、`lib/export.{tradesToCsv,equityToCsv,downloadCsv}`（SSR 安全）、`lib/types.{Metrics(7),Trade,Overlay}`、`aiBacktestTypes.AiBacktestResult`（11 键，复用 `EquityPoint/Metrics/Trade`）、`useAiBacktestJob.result`（line 33，reducer 整体替换）、`EquityChart` props、`AiBacktestModal.{CompleteView(262),RunningView(190)}` + 共享 `Modal`（ESC/焦点陷阱/labelledBy）。
- **需订正（已并入上表）**：`profitFactor` 语义/中文名（P1-1）；`bar_to_seconds` 在 `mtf.py` 非 `metrics.py`（P2-1）；`Sparkline` 仅为 `AiBacktestModal.tsx` 内私有函数（非共享组件，实现须「仿」或提取，见 P1-5）；`_run_loop` 真实签名含 `dfs` 形参；`main_df` 实际六列 `ts,o,h,l,c,vol`；`AiStatsCard/ActionBars/DecisionTimeline/SummaryTimeline` 为 `AiBacktestModal.tsx` **内联**定义（非独立文件）。

---

## 一、Overview（概述）

已交付的「AI 自动交易 · 回测模式」回答了「让 AI 决策者在历史行情上跑起来、看它怎么交易、赚不赚钱」这个核心问题：它以 `SimBroker` 逐根撮合复现 AI 的多策略综合研判，完成后用 `compute_metrics` 给出**总收益 `totalReturn`、胜率 `winRate`、最大回撤 `maxDrawdown`、夏普 `sharpe`、盈利因子 `profitFactor`（＝毛盈利/毛亏损，注意**不是**平均盈利/平均亏损的「盈亏比」）、成交笔数 `tradeCount`、平均盈亏 `avgPnl`**七项标准指标（键名与 `metrics.py:62-70` 逐字一致），并配以 AI 专属统计（决策轮数、动作分布、平均置信度、共识降级、上下文摘要）。这套指标足以判断「盈亏方向」，但对一个想在投入真实资金前**认真评估 AI 决策者**的用户而言，仍缺三类专业量化最关心的信息：**风险调整后收益的完整刻画**（下行风险 Sortino、收益/回撤比 Calmar、年化收益与年化波动、回撤持续时长、在场时间占比）、**交易质量的细节**（平均盈利/亏损、盈亏比 Payoff、最大连胜/连亏、单笔极值、平均持仓时长）、以及**「AI 到底有没有跑赢什么都不做」的基准对比**（买入持有 Buy & Hold 的收益/回撤，与之相比的超额收益 alpha）。缺了基准，一条向上的权益曲线无法回答「这是 AI 的本事，还是行情本身在涨」。

本方案在**完全不触碰已交付回测执行链路**的前提下，补齐上述三类信息并把它们**优雅地呈现**。工程上分为四个相互独立、可分别落地、各自默认安全的增强：**B1 扩展绩效指标**（新增纯函数模块 `engine/metrics_ext.py`，从既有权益曲线与成交记录派生 Sortino/Calmar/CAGR/年化波动/回撤时长/在场占比/盈亏比/连胜连亏/平均持仓等，`compute_metrics` 七项契约**零改动**）；**B2 买入持有基准与超额**（新增纯函数模块 `engine/benchmark.py`，在相同区间上模拟买入持有并给出基准收益/回撤/夏普与 alpha，权益图叠加基准线）；**B3 回撤水下图与周期收益分布**（纯前端，从已有 `equity[].drawdown` 派生，无后端改动）；**B4 结果仪表盘升级与导出**（纯前端，把完成态重构为分页签 + KPI 概览 + 一键导出 CSV/JSON，复用既有 `ui/Tabs` 与 `lib/export.ts`）。

这四项都是**加法**：后端仅在回测运行器 `_finalize` 收尾处**追加**两个纯函数调用，把结果多挂两个字段（`extendedMetrics`、`benchmark`），随既有 `result` 事件下发、随既有 `metrics_json` 落库（自由 JSON，**无迁移**）；前端仅在完成态**新增**渲染，运行态与启动弹窗**零改动**。旧版回测行为、AI 决策、护栏、既有单测全部不变——本方案对「回测是不是忠实预演实盘 AI」这一命题不产生任何影响，只让「跑完之后看到的东西」更全、更专业、更好看。它精准落在本轮需求的两处强调上，并延续项目一贯的设计 token 与暗/亮主题，隆重而不喧宾夺主。

---

## 二、Technical design（技术设计）

> 术语沿用回测 v2：一次 AI 回测由 `AiBacktestRunner`（`ai_auto/backtest.py`）逐根驱动，收尾在 `_finalize` 计算 `compute_metrics` + `aiStats`、组装 `result`、落 Run。本方案**只在 `_finalize` 及其组装/落库处追加**，不改逐根循环、决策链路、护栏、取消/流式生命周期。

### 2.0 As-built 复用锚点（供开发节点校准，均经代码核验）

| 复用件 | 位置（file:symbol） | 增强怎么用 |
|---|---|---|
| 标准指标 | `engine/metrics.py`：`compute_metrics(equity_points, trades, bar, initial_capital)` → 七项；`BARS_PER_YEAR` 年化因子表 | **零改动**（契约稳定）。B1 新模块复用 `BARS_PER_YEAR`，**不改** `compute_metrics`。 |
| 回测运行器收尾 | `ai_auto/backtest.py`：`AiBacktestRunner._finalize(job, main_df, first, state)`（`backtest.py:402`）、`_build_result`（`:441`）、`_persist`（`:451`）、`_aggregate_stats`（`:418`）、`_run_loop`（`:251`，逐根 `mark` 后知 `self.main.pos.side`） | B1/B2 在 `_finalize` 计算并挂 `result["extendedMetrics"]`/`result["benchmark"]`；`_run_loop` 增在场根计数 `self._bars_in_market`。 |
| 模拟撮合快照 | `engine/simulator.py`：`SimBroker.equity_points`（`list[EquityRecord{ts,equity,drawdown}]`）、`.trades`（`list[TradeRecord]`）、`.pos.side` | B1/B2 的输入源（**零改动**读取）。 |
| 成交/权益类型 | `engine/simulator.py`：`EquityRecord`、`TradeRecord`（`side/pnl/ts/qty/fee/...`） | B1 纯函数入参类型（**零改动**）。 |
| 主账户逐根帧 | `ai_auto/backtest.py`：`main_df`（列 `ts/o/c`）、`first`（首个决策根，`_first_decision_index`，`:238`） | B2 基准取 `main_df.iloc[first:]` 的 `c`/`ts`（与权益曲线同窗口）。 |
| 落库（自由 JSON） | `db/repositories.py`：`persist_backtest_result(session, run, *, trades, equity, metrics, start_ts, end_ts, status)`（`:144`，`metrics` 经 `json.dumps` 落 `metrics_json`，**自由 JSON、无 schema**） | B1/B2 字段随 `metrics_full` 一并落库，**无迁移**。 |
| 结果契约 | `schemas.py`：`AiBacktestResultOut`（`:469`，`metrics:dict` + `aiStats` + `equity/trades/...`） | 追加**可选**字段 `extendedMetrics`/`benchmark`（默认空，向后兼容）。 |
| 结果面板 | `components/Chart/AiBacktestModal.tsx`：`CompleteView`（`:262`）、`MetricsCards` 复用（`Strategy/MetricsCards`）、`EquityChart` 复用（`Chart/EquityChart`，仅接 `equity?` 或 `overlays?`）、`AiStatsCard`/`ActionBars`/`DecisionTimeline`/`SummaryTimeline` | B3/B4 在 `CompleteView` 内重构为分页签并新增水下图/周期收益/扩展指标网格/导出。运行态 `RunningView` **零改动**。 |
| 前端类型 | `lib/types.ts`：`Metrics`（七项，`:391`）、`EquityPoint{ts,equity,drawdown}`（`:304`）、`Trade`（`:257`）、`Overlay`（`:384`） | B1/B2 新增 TS 接口；B3 从 `EquityPoint.drawdown` 派生。 |
| 页签原语 | `components/ui/index.tsx`：`Tabs({tabs,active,onChange,variant})`（`:343`，`variant:"segmented"|"underline"`） | B4 完成态分页签（`variant="underline"`）。 |
| 导出纯件 | `lib/export.ts`：`tradesToCsv(trades)`、`equityToCsv(equity)`、`downloadCsv(filename,content)`（BOM + Blob 下载，SSR 安全） | B4 「导出成交/净值 CSV」**逐字复用**；JSON 导出新增 `resultToJson`。 |
| 作业视图 | `lib/useAiBacktestJob.ts`：`AiBacktestJobView.result: AiBacktestResult | null`（`:33`） | B1/B2 新字段随 `result` 透传，reducer **零改动**（`result` 整体替换）。 |

### 2.1 架构与数据流（端到端；灰底=本方案新增/改动，其余不变）

```text
AiBacktestRunner._run_loop（逐根，既有）
  每根：main.fill_pending → apply_risk → mark → [B1] if pos.side!="flat": self._bars_in_market += 1   ← 唯一循环内新增（O(1)）
  决策根：AI 决策 → 门禁 → submit（既有，零改动）
AiBacktestRunner._finalize（收尾，既有 + 追加）
  metrics   = compute_metrics(...)                                  （既有七项，零改动）
  aiStats   = _aggregate_stats()                                    （既有）
  [B1] ext  = compute_extended_metrics(equity_points, trades, bar, cap, bars_in_market=self._bars_in_market)
  [B2] bench= compute_buy_hold(user_closes, user_ts, cap, bar); bench["alpha"] = totalReturn - bench.totalReturn
  result = _build_result(metrics, aiStats, notes) + {extendedMetrics: ext, benchmark: bench}   ← 追加两键
  _persist: metrics_full = {**metrics, aiStats, finalSummary, decisionsDigest, extendedMetrics, benchmark}  ← 追加两键，落 metrics_json（无迁移）
  job.publish({type:"result", data: result})                       （既有事件，负载多两键）
        │
        ▼ WS /ws/ai-backtest/{jobId}（既有路由，负载增量）
useAiBacktestJob（reducer 零改动，result 整体替换）→ view.result.{extendedMetrics,benchmark}
        ▼
AiBacktestModal.CompleteView（B3/B4 重构为分页签）
  ├─ [概览] KPIHero(总收益/超额alpha/夏普/最大回撤/胜率) + EquityChart(equity, benchmark=bench.equity) + UnderwaterChart(equity)
  ├─ [进阶指标] MetricsCards(七项) + ExtendedMetricsGrid(ext) + PeriodReturnBars(equity) + BenchmarkCompareCard(bench)
  ├─ [成交] TradesTable + 导出按钮(tradesToCsv/equityToCsv/resultToJson→downloadCsv)
  ├─ [AI 决策] AiStatsCard + ActionBars + DecisionTimeline
  └─ [上下文] SummaryTimeline
```

**核心设计三句话**：
1. **只加法、不改行为**。四项增强都是在已交付回测的**输出端**追加派生指标与展示，不碰逐根循环的决策/执行/护栏，不碰启动弹窗与运行态；既有回测行为与单测**逐字不变**。
2. **指标全在纯函数里、可单测、可复看**。B1/B2 是无副作用纯函数，输入为既有 `equity_points`/`trades`/`closes`；随 `result` 事件下发、随 `metrics_json` 落库（自由 JSON，无迁移），可离线复算、可在运行历史复看。
3. **展示复用既有原语、顶级 HCI**。B3/B4 复用 `ui/Tabs`、`lib/export.ts`、设计 token 与暗/亮主题；水下图/周期收益用与既有 `Sparkline` 同款的轻量内联 SVG，无新增依赖。

### 2.2 B1 · 扩展绩效指标（`engine/metrics_ext.py`，纯函数）

新增**纯函数**模块（不改 `metrics.py`，保证 `compute_metrics` 七项契约与所有既有回测调用方零影响）：

```python
def compute_extended_metrics(
    equity_points: list[EquityRecord],
    trades: list[TradeRecord],
    bar: str,
    initial_capital: float,
    *,
    bars_in_market: int | None = None,   # 运行器传入（在场根数）；None → 从成交对推断
) -> dict:
    """从权益曲线 + 成交记录派生进阶风险/质量指标（camelCase）。与 compute_metrics 互补、独立。"""
```

**返回键（全部 `round(6)`，空/退化输入回落 0.0，绝不抛异常）**：

| 键 | 含义 | 计算（`bar_returns` = 逐根收益率，同 `compute_metrics`；`bars = len(equity_points)`；`BPY = BARS_PER_YEAR.get(bar, 8760)`） |
|---|---|---|
| `annualizedReturn` | 年化收益（CAGR） | `base = equity[-1]/initial_capital`；`base<=0 → -1.0`，否则 `base**(BPY/max(bars,1)) - 1` |
| `annualizedVolatility` | 年化波动率 | `std(bar_returns) * sqrt(BPY)`；`len<2 → 0` |
| `sortino` | 索提诺比率 | `mean(bar_returns) / downsideDev * sqrt(BPY)`；`downsideDev = sqrt(mean(min(r,0)²))`；无下行/为 0 → 0 |
| `calmar` | 卡玛比率 | `annualizedReturn / abs(maxDrawdown)`；`maxDrawdown==0 → 0` |
| `maxDrawdownBars` | 最长水下持续根数 | 权益 `drawdown<0` 的最长连续段长度 |
| `maxDrawdownSecs` | 最长水下时长（秒） | `maxDrawdownBars * bar_to_seconds(bar)` |
| `timeInMarketPct` | 在场时间占比 | `bars_in_market/bars`（`bars_in_market` 由运行器给）或成交对推断兜底 |
| `avgWin` / `avgLoss` | 平均盈利 / 平均亏损（绝对值） | 平仓笔 `pnl>0` / `pnl<0` 的均值 |
| `payoffRatio` | 盈亏比（赔率） | `avgWin / avgLoss`；`avgLoss==0 → 0` |
| `largestWin` / `largestLoss` | 单笔最大盈利 / 最大亏损 | 平仓笔 `pnl` 极值 |
| `maxConsecutiveWins` / `maxConsecutiveLosses` | 最大连胜 / 连亏 | 平仓笔按序 `pnl` 符号的最长同号连段 |
| `avgHoldBars` | 平均持仓根数 | 配对 `open→close` 成交：`(close.ts-open.ts)/bar_sec` 的均值 |
| `tradesPerWeek` | 交易频率（笔/周） | `tradeCount / (总时长秒/604800)`；时长 0 → 0 |

**口径对齐（一致性 · v2 强约束，防止与 `metrics.py` 漂移）**：
- **[P1-3] 平仓笔选取必须 `side == "close"`**：`TradeRecord` 用 `side ∈ {long,short,close}` 区分开/平（**无** `kind`/`action` 字段；开仓笔 `pnl=0`，平仓笔 `side="close"` 且带已实现净 `pnl`）。所有交易质量指标（`avgWin`/`avgLoss`/`payoffRatio`/`largestWin`/`largestLoss`/`maxConsecutiveWins`/`maxConsecutiveLosses`）的输入集**必须**为 `closed = [t for t in trades if t.side == "close"]`——与 `metrics.py:52` 的 `tradeCount`/`winRate` **同源**，**禁止**在全部 trades 上按 `pnl` 符号过滤（否则会混入开仓笔并与七项口径漂移）。`avgHoldBars` 的 `open→close` 配对以 `side` 判定：`open` 取 `side ∈ {long,short}`、`close` 取 `side="close"`，反手记为「先 close 后 open」两段。
- **[P1-2] 方差/年化口径必须与 `metrics.py` 逐字一致**：`metrics.py:41-46` 的 sharpe 用**样本方差 ddof=1**（`var = Σ(r-mean)²/(n-1)`、`std=sqrt(var)`、`sharpe = mean/std * sqrt(BARS_PER_YEAR.get(bar,8760))`）。B1 的 `annualizedVolatility`（`std(bar_returns)*sqrt(BPY)`）与 `sortino` 分子 `mean(bar_returns)`、以及 §2.3 基准的 `sharpe`，**必须**采用同一 `_bar_returns` 定义（`equity[i]/equity[i-1]-1`）、同一**样本方差 ddof=1**、同一 `BARS_PER_YEAR.get(bar,8760)` 年化因子，使 BenchmarkCompareCard 里「策略 sharpe（来自 `metrics.py`）vs 基准 sharpe（来自新助手）」**逐位可比**。（Sortino 下行标准差 `downsideDev=sqrt(mean(min(r,0)²))` 为下行专用口径，与上面分子的年化因子一致即可。）
- **[P2-1] `maxDrawdownSecs` 复用既有 `bar_to_seconds`**：`bar_to_seconds` 已存在于 `engine/mtf.py`（读 `okx/types.py::BAR_SECONDS`，`1H→3600` 等），且 `ai_auto/backtest.py` 已 import——**直接复用**（`from ..engine.mtf import bar_to_seconds`），**禁止**在 `metrics_ext.py` 重造一份 bar→秒表。注意其对未知 bar 抛 `ValueError`，回测 bar 已在上游校验，安全。

**实现约束（CLAUDE.md）**：`compute_extended_metrics` 主函数 ≤50 行、圈复杂度 ≤10——把「逐根收益率」「连胜连亏」「持仓配对」「下行标准差」「年化夏普」拆为模块级私有纯助手（`_bar_returns`/`_streaks`/`_hold_bars`/`_downside_dev`/`_annualized_sharpe`），其中 `_annualized_sharpe` 为 §2.3 基准与本模块**共用的唯一实现**（禁止复制两份）。公开函数写 Google docstring（参数/返回/边界）。

**运行器接线（`ai_auto/backtest.py`）**：
- `__init__` 增 `self._bars_in_market = 0`。
- `_run_loop`（`:262` 之后、`mark` 之后）增一行：`if self.main.pos.side != "flat": self._bars_in_market += 1`（O(1)，不改循环结构）。
- `_finalize`（`:407` 计算 `metrics` 之后）增：`ext = compute_extended_metrics(self.main.equity_points, self.main.trades, self.bar, self.initial_capital, bars_in_market=self._bars_in_market)`。
- `_build_result` 返回 dict 增 `"extendedMetrics": ext`；`_persist` 的 `metrics_full` 增 `"extendedMetrics": ext`（与既有 `aiStats` 并列）。

### 2.3 B2 · 买入持有基准与超额 alpha（`engine/benchmark.py`，纯函数）

新增**纯函数**模块，在**与权益曲线相同的窗口**（用户区间，即 `main_df.iloc[first:]`）上模拟「首根收盘价满仓买入并持有至末根」：

```python
def compute_buy_hold(closes: list[float], timestamps: list[int],
                     initial_capital: float, bar: str) -> dict:
    """区间买入持有基准：首根收盘满仓、逐根盯市。返回 equity/totalReturn/maxDrawdown/sharpe/annualizedReturn。"""
```

- `units = initial_capital / closes[0]`（`closes[0]<=0` → 空基准 `{}`）；`equity[i] = units * closes[i]`；`drawdown[i] = equity[i]/running_peak - 1`。
- `totalReturn = closes[-1]/closes[0] - 1`；`maxDrawdown = min(drawdown)`；**[P1-2] `sharpe` 必须复用 §2.2 的唯一助手 `_annualized_sharpe`**（`metrics_ext` 私有、与 `metrics.py:41-46` 逐字同式：`_bar_returns` 定义相同、**样本方差 ddof=1**、`BARS_PER_YEAR.get(bar,8760)`），**禁止**在 `benchmark.py` 另写一份——否则「策略 sharpe（走 `metrics.py`）vs 基准 sharpe」不可比、会误导投资判断。`test_benchmark.py` **须**加一条交叉校验：同一组 `bar_returns` 分别喂给 `_annualized_sharpe` 与 `compute_metrics`，两者 `sharpe` **逐位相等**。`annualizedReturn` 同 B1 口径。
- 返回 `{"equity": [{"ts","equity","drawdown"} …], "totalReturn", "maxDrawdown", "sharpe", "annualizedReturn"}`。

**运行器接线**：`_finalize` 增 `user_closes = main_df["c"].iloc[first:].tolist()`、`user_ts = main_df["ts"].iloc[first:].astype(int).tolist()`；`bench = compute_buy_hold(user_closes, user_ts, self.initial_capital, self.bar)`；若 `bench`：`bench["alpha"] = round(metrics["totalReturn"] - bench["totalReturn"], 6)`。`result["benchmark"] = bench`（可能为 `{}`）；`metrics_full["benchmark"] = bench`。**基准为纯几何计算、无 LLM、O(bars)、确定性**——不引入成本/时长/未来函数问题（收盘价单点、无前视）。**窗口对齐（已核实）**：`_run_loop` 迭代 `range(first, n)`、`SimBroker.mark` 逐根 append 一个 `EquityRecord`，故 `equity_points` 与基准的 `iloc[first:]` 窗口**逐 ts 对齐、等长**，`alpha`＝同窗口两条曲线的总收益差、无窗口错位。

**[P2-2] 派生失败不得静默吞（CLAUDE.md §5）**：B1/B2 两次派生调用统一包一层 `try/except`，**捕获后先 `log.warning`（含 `job.job_id` 与异常摘要）再回落空 dict**（`ext={}`/`bench={}`），保证分析层异常绝不影响主 `result`/落库，同时不静默丢错。

### 2.4 B3 · 回撤水下图与周期收益分布（纯前端，从既有数据派生）

**无后端改动**：`result.equity`（`EquityPoint{ts,equity,drawdown,transient?}`，注意实际类型多一个可选 `transient?`）已含逐根 `drawdown`。新增纯件 `lib/backtestStats.ts`：
- `underwaterSeries(equity): {ts, drawdown}[]` —— 直接取 `drawdown`（若某点缺失则用运行峰值现算兜底），供水下面积图。
- `periodReturns(equity, period: "day"|"week"|"month"): {label, ret, ts}[]` —— 按 UTC 日/周/月分桶，桶收益 `= 桶末权益/桶初权益 - 1`。**[P2-4] 自动降密规则明确**：默认 `"day"`；若日桶数 `> 60` 切 `"week"`；若周桶数仍 `> 60` 切 `"month"`；`"month"` 为最粗粒度（多年回测封顶到月，柱不再更粗、轴按需稀疏标注）。
- `maxDrawdownWindow(equity): {startTs, endTs, depth} | null` —— 最深回撤区间（供概览标注「最深回撤发生于 X→Y，-Z%」）。

**[P1-5] 新展示组件必须抽到同目录 sibling 文件（不得全内联进 `AiBacktestModal.tsx`）**：`AiBacktestModal.tsx` 现已 **409 行**，本增强净增约 250~350 行；为守 `CLAUDE.md` 单文件 ≤800 行、单组件 ≤50 行、圈复杂度 ≤10，新的图表/面板组件 `UnderwaterChart`（0 轴以下红色面积，越深越负）、`PeriodReturnBars`（正绿负红柱，hover 显日期与收益）、`KPIHero`、`ExtendedMetricsGrid`、`BenchmarkCompareCard` **统一放入新建 sibling 文件**（建议 `components/Chart/aiBacktest/panels.tsx` 与 `components/Chart/aiBacktest/charts.tsx`，或至少一个 `AiBacktestPanels.tsx`），由 `AiBacktestModal.tsx` import。均为轻量内联 SVG（仿 `AiBacktestModal.tsx` 内私有 `Sparkline`——它当前**不是**可复用组件，实现时把通用的 SVG 折线/面积逻辑一并提到 sibling 供复用），**无新增图表依赖**。改造后 `AiBacktestModal.tsx` 必须仍 <800 行（评审后置门禁）。

### 2.5 B4 · 结果仪表盘升级与导出（纯前端 HCI）

**仅重构 `AiBacktestModal.CompleteView`（`:262`），运行态 `RunningView` 与页头/页脚零改动**：
- **KPI 概览 `KPIHero`**：大字号五连（总收益 / **超额 alpha**（有基准时）/ 夏普 / 最大回撤 / 胜率），沿用既有 tone 逻辑（`>0` long、`<0` short）。是「一眼看懂」的顶层视觉锚。
- **分页签**（`ui/Tabs variant="underline"`，`active` 由本地 `useState` 管，默认「概览」）：
  - **概览**：`KPIHero` + `EquityChart`（新增 `benchmark` 叠加线）+ `UnderwaterChart` + 最深回撤标注 + 结果 `notes`。
  - **进阶指标**：`MetricsCards`（既有七项）+ `ExtendedMetricsGrid`（B1，带释义 tooltip 的指标网格）+ `BenchmarkCompareCard`（策略 vs 基准：收益/回撤/夏普并排）+ `PeriodReturnBars`。**[P1-1] 标签去歧义**：既有第七项 `profitFactor` 在 UI 一律标「**盈利因子**（毛盈利/毛亏损）」，B1 新增的 `payoffRatio` 标「**盈亏比·赔率**（平均盈利/平均亏损）」——两卡分处 `MetricsCards` 与 `ExtendedMetricsGrid`，**中文标签不得重名**，各自带释义 tooltip 说明公式。
  - **成交**：`TradesTable`（既有）+ **导出条**：「导出成交 CSV」`tradesToCsv`→`downloadCsv`、「导出净值 CSV」`equityToCsv`→`downloadCsv`（CSV 仍走带 BOM 的 `downloadCsv` 以兼容 Excel）；**[P1-4]「导出完整结果 JSON」不得复用 `downloadCsv`**——`downloadCsv` 会前置 UTF-8 BOM 且 MIME 固定 `text/csv`，带前导 BOM 的 JSON 会让严格 `JSON.parse('﻿…')` 抛错、扩展名/MIME 也不符。改为新增**通用下载助手** `downloadJson(filename, obj)`（或参数化 `downloadFile(filename, content, mime, {bom=false})`），JSON 走 `application/json;charset=utf-8`、**不加 BOM**；`downloadCsv` 保持原样不动。JSON 序列化用新增 `resultToJson(result)`。
  - **AI 决策**：`AiStatsCard` + `ActionBars` + `DecisionTimeline`（均既有）。
  - **上下文**：`SummaryTimeline`（既有）。
- **HCI 细节**：页签可键盘切换（`Tabs` 已具语义）；`Modal` 已处理 ESC/焦点陷阱/`labelledBy`；空态（无成交/无基准/旧版结果缺 `extendedMetrics`）各分区**优雅降级**（隐藏该卡而非报错）；导出按钮在无数据时 `disabled`。

**`EquityChart` 新增可选 `benchmark?` 入参（唯一对既有共享组件的改动，加法、默认等价）**：`interface EquityChartProps` 增 `benchmark?: EquityPoint[]`；组件内新增一条 `addLineSeries`（`color: var(--muted)`、`lineStyle: LineStyle.Dashed`、`title:"买入持有"`、绝对右轴，与净值线共轴），仅当 `benchmark?.length` 时 `setData`，否则 `setData([])`。默认 `undefined` → 既有调用方（回测/实时/对比）**行为逐字不变**。**[P2-5] benchmark 只在单条 `equity`（绝对轴）分支渲染**：`EquityChart` 的 `overlays` 分支会把多条曲线**归一化为百分比**（`normalizeEquity`），而单条 `equity` 走**绝对右轴**；benchmark 与 `equity` 同为绝对值、共轴可比，故须与 `equity` 同分支、且当传入 `overlays` 时忽略 `benchmark`（AiBacktestModal 完成态本就走单条 `equity` 模式，二者互斥不冲突）。

### 2.6 默认等价与零回归不变量（一等约束）

| 不变量 | 保证方式 |
|---|---|
| `compute_metrics` 七项**逐字不变** | B1 是**新文件** `metrics_ext.py`，**不改** `metrics.py`；所有策略/纸面/factor 回测调用方零影响。 |
| 已交付回测**行为逐字不变** | 决策链路、`SimBroker` 执行、护栏、取消/流式、落库口径不动；运行器仅在 `_run_loop` 加一行 O(1) 计数、在 `_finalize` 加两次纯函数调用与两个结果键。 |
| 既有单测**全绿** | 不改既有函数签名/返回；`result`/`metrics_json` 是**超集**（多两键），既有断言仍成立。 |
| 契约向后兼容 | `AiBacktestResultOut.extendedMetrics`/`benchmark` 为**可选默认空**；前端类型 `extendedMetrics?`/`benchmark?` 可选，旧版结果缺失时前端**优雅降级**。 |
| 启动弹窗/运行态**零改动** | B3/B4 仅动 `CompleteView`；`AiTradeLaunchModal`/`AiTradeToggle`/`RunningView`/store 不改。 |
| 无新配置、无迁移、无新依赖 | B1/B2 无 tunable；`metrics_json` 自由 JSON；SVG 内联、CSV/Tabs 复用既有件。 |
| **守 `CLAUDE.md` 尺寸阈值** | 新展示组件抽到 sibling 文件（§2.4/§三），`AiBacktestModal.tsx` 改造后仍 <800 行；`compute_extended_metrics`/各前端组件 ≤50 行、圈复杂度 ≤10；派生失败记 `log.warning` 不静默吞（§2.3）。 |
| **指标口径与 `metrics.py` 可比** | 夏普/波动率共用 `_annualized_sharpe`（ddof=1、同年化因子）；平仓笔选取 `side=="close"` 与 `metrics.py:52` 同源；标签区分盈利因子/盈亏比（§2.2/§2.5）。 |

---

## 三、File / module change plan（文件 / 模块变更计划）

> ✚=新建，✎=修改。遵循 CLAUDE.md 阈值（单文件 ≤800 行、单函数 ≤50 行、≤5 形参、圈复杂度 ≤10、嵌套 ≤4、公开 API 写 docstring）。**不新增真单路径；不改实盘链路；不改 `compute_metrics`；不改启动弹窗/运行态。**

### 后端 `services/quant/app/`

| 文件 | 操作 | 一句话意图 |
|---|---|---|
| `engine/metrics_ext.py` | ✚ | 纯函数 `compute_extended_metrics(equity_points, trades, bar, initial_capital, *, bars_in_market=None)` + 私有助手（`_bar_returns`/`_streaks`/`_hold_bars`/`_downside_dev`），派生 Sortino/Calmar/CAGR/年化波动/回撤时长/在场占比/盈亏比/连胜连亏/平均持仓/交易频率（§2.2）。复用 `metrics.BARS_PER_YEAR`。 |
| `engine/benchmark.py` | ✚ | 纯函数 `compute_buy_hold(closes, timestamps, initial_capital, bar)` → 区间买入持有基准 equity/totalReturn/maxDrawdown/sharpe/annualizedReturn（§2.3）。 |
| `ai_auto/backtest.py` | ✎ | `__init__` 增 `self._bars_in_market=0`；`_run_loop` 增一行在场根计数；`_finalize` 增算 `ext`/`bench`（`bench.alpha`）；`_build_result` 与 `_persist.metrics_full` 各追加 `extendedMetrics`/`benchmark` 两键。**不改**逐根决策/执行/护栏/取消。 |
| `schemas.py` | ✎ | `AiBacktestResultOut` 追加可选 `extendedMetrics: dict = Field(default_factory=dict)`、`benchmark: dict = Field(default_factory=dict)`（向后兼容）。 |

### 前端 `apps/web/`

| 文件 | 操作 | 一句话意图 |
|---|---|---|
| `lib/aiBacktestTypes.ts` | ✎ | 新增 `AiBacktestExtendedMetrics`、`AiBacktestBenchmark` 接口；`AiBacktestResult` 追加可选 `extendedMetrics?`、`benchmark?`。 |
| `lib/backtestStats.ts` | ✚ | 纯件：`underwaterSeries(equity)`、`periodReturns(equity, period: "day"\|"week"\|"month"，>60 桶自动降密，§2.4)`、`maxDrawdownWindow(equity)`、扩展指标/百分比格式化助手。 |
| `lib/export.ts` | ✎ | 追加 `resultToJson(result)`（`JSON.stringify` 带缩进）**与新增无 BOM 的 `downloadJson(filename,obj)`**（MIME `application/json`；**[P1-4]** JSON **不得**走带 BOM 的 `downloadCsv`）。**不改** `tradesToCsv`/`equityToCsv`/`downloadCsv`。 |
| `components/Chart/EquityChart.tsx` | ✎ | `EquityChartProps` 增可选 `benchmark?: EquityPoint[]`；新增一条虚线基准 series（默认 undefined 零变化，§2.5）；仅在单条 `equity`（绝对轴、非 overlays）分支渲染。 |
| `components/Chart/aiBacktest/panels.tsx`（及 `charts.tsx`） | ✚ | **[P1-5]** 新的展示组件 `KPIHero`/`ExtendedMetricsGrid`/`BenchmarkCompareCard`/`UnderwaterChart`/`PeriodReturnBars`（+ 复用型内联 SVG 助手）抽到 sibling 文件，避免 `AiBacktestModal.tsx` 破 800 行阈值；各组件 ≤50 行、圈复杂度 ≤10。 |
| `components/Chart/AiBacktestModal.tsx` | ✎ | `CompleteView` 重构为分页签（`ui/Tabs variant="underline"`），**import** 上一行的 sibling 组件并装配 + 导出条（§2.5）。`RunningView`/页头/页脚零改动；**改造后本文件仍须 <800 行**（现 409 行）。 |

### 测试

| 文件 | 操作 | 一句话意图 |
|---|---|---|
| `services/quant/tests/test_metrics_ext.py` | ✚ | 已知 equity/trades → 断言 Sortino/Calmar/CAGR/年化波动/回撤时长/在场占比/盈亏比/连胜连亏/平均持仓/交易频率；退化输入（无成交、单根、全胜、全负、回撤为 0）不抛且回落 0。 |
| `services/quant/tests/test_benchmark.py` | ✚ | 上涨收盘 → totalReturn 与 `closes[-1]/closes[0]-1` 一致、maxDrawdown≤0；平盘 → 全 0；单点/空/首价≤0 边界回落空基准；sharpe 与 `compute_metrics` 同式。 |
| `services/quant/tests/test_ai_backtest_runner.py` | ✎ | 补：`result.extendedMetrics`/`result.benchmark` 存在且键完整、`benchmark.alpha == totalReturn - benchmark.totalReturn`、`_bars_in_market` 计数与在场根一致、落库 `metrics_json` 含两键；断言既有七项/aiStats/流式/取消行为**逐字不变**（回归）。 |
| `apps/web/lib/backtestStats.test.ts` | ✚ | `underwaterSeries`/`periodReturns`（日/周分桶、自动降密）/`maxDrawdownWindow` 纯件；空/单点/缺 drawdown 边界。 |
| `apps/web/lib/export.test.ts` | ✎ | 补 `resultToJson` 序列化正确、`downloadCsv` 在无 document 时安全返回（既有断言不动）。 |
| `apps/web/components/Chart/AiBacktestModal.test.tsx` | ✎ | 补：完成态分页签切换；`KPIHero` 有基准时显 alpha；`ExtendedMetricsGrid` 渲染扩展指标；导出按钮触发 `downloadCsv`（mock）；**旧版结果缺 `extendedMetrics`/`benchmark` 时不崩、相关分区优雅隐藏**；运行态渲染不受影响（`NODE_ENV=test`）。 |

### 桌面 `desktop/`

| 文件 | 操作 | 一句话意图 |
|---|---|---|
| （无需改） | — | 无新增 env / 配置项；纯后端派生 + 前端展示，不经 desktop 白名单。 |

---

## 四、Interface design（接口设计）

**不新增任何 REST/WS 路由**——沿用回测 v2 的 `POST /api/ai-trade/backtest/start`、`POST /…/{job_id}/cancel`、`GET /…/{job_id}`、`WS /api/ws/ai-backtest/{job_id}`；仅 `result` 事件与 `GET` 快照的 `result` 负载**新增两个可选键**，向后兼容。**[P2-3] 序列化路径须真正带上两键**：WS `result` 事件透传 runner 整包 dict（已含两键，OK）；但 `GET /…/{job_id}` 快照经 `AiBacktestResultOut` 序列化——除在 §三 给 `AiBacktestResultOut` 加**带默认**的可选字段 `extendedMetrics: dict = Field(default_factory=dict)`、`benchmark: dict = Field(default_factory=dict)`（注意该模型 `aiStats` 是**类型化子模型** `AiBacktestAiStatsOut`，两新键须为 dict + 默认方能向后兼容）外，还须确认构造 `AiBacktestResultOut` 处**把 runner dict 的两键映射进去**（§6.1 加断言：GET 快照亦含两键）。

### 4.1 `result` 事件 / `GET /…/{job_id}` 的 `AiBacktestResultOut`（增量）
```jsonc
{
  // …既有字段（runId, metrics, aiStats, equity, trades, decisionsDigest, summaries, startTs, endTs, barsProcessed, notes）不变…
  "extendedMetrics": {                 // B1（新增，可选；旧版结果为 {}）
    "annualizedReturn": 0.42, "annualizedVolatility": 0.58, "sortino": 1.9, "calmar": 1.3,
    "maxDrawdownBars": 34, "maxDrawdownSecs": 122400, "timeInMarketPct": 0.61,
    "avgWin": 128.4, "avgLoss": 71.2, "payoffRatio": 1.8, "largestWin": 540.0, "largestLoss": -260.0,
    "maxConsecutiveWins": 5, "maxConsecutiveLosses": 3, "avgHoldBars": 7.4, "tradesPerWeek": 6.2
  },
  "benchmark": {                       // B2（新增，可选；无法计算时为 {}）
    "equity": [{ "ts": 1743465600, "equity": 10000, "drawdown": 0 }, /* … */],
    "totalReturn": 0.18, "maxDrawdown": -0.22, "sharpe": 0.9, "annualizedReturn": 0.25,
    "alpha": 0.11                      // 策略 totalReturn − 基准 totalReturn
  }
}
```

### 4.2 `EquityChart` 组件契约（增量）
```ts
interface EquityChartProps {
  equity?: EquityPoint[];       // 既有
  overlays?: Overlay[];         // 既有
  benchmark?: EquityPoint[];    // 新增：买入持有基准（虚线、muted、绝对右轴）；默认 undefined → 零变化
  theme: "light" | "dark"; loading?: boolean; error?: string | null; emptyHint?: string;
}
```

### 4.3 导出（纯前端，无网络）
`lib/export.ts`：CSV 复用 `tradesToCsv`/`equityToCsv`/`downloadCsv`（带 BOM，兼容 Excel）；JSON 新增 `resultToJson(result: AiBacktestResult): string`（`JSON.stringify(result, null, 2)`）**并经新增无 BOM 的 `downloadJson(filename, obj)`**（MIME `application/json;charset=utf-8`，**[P1-4]** 不复用 `downloadCsv`，避免 BOM 破坏 `JSON.parse` 回读）。文件名建议 `ai-backtest-{instId}-{bar}-{startTs}-{endTs}.{csv|json}`。

### 4.4 运行历史复看（复用既有 runs 路由，无新增）
`extendedMetrics`/`benchmark` 随 `metrics_json` 落库，经既有 `GET /api/runs/{runId}` 返回（`metrics` 字段内）。运行历史查看器渲染保持既有通用逻辑，本方案不强制其展示扩展指标（AiBacktestModal 完成态是主展示面）。

---

## 五、Data model（数据模型）

### 5.1 后端派生指标形态（纯函数返回，随 `metrics_json` 落库）
- **`extendedMetrics: dict`**（B1）：键见 §2.2，全 `float`（`maxDrawdownBars`/`maxConsecutiveWins`/`maxConsecutiveLosses`/`maxDrawdownSecs` 为 `int`），`round(6)`，退化回落 0.0。
- **`benchmark: dict`**（B2）：`equity: list[{ts:int, equity:float, drawdown:float}]` + `totalReturn/maxDrawdown/sharpe/annualizedReturn/alpha: float`；无法计算（首价≤0/空区间）时为 `{}`。

### 5.2 运行器内存增量（`ai_auto/backtest.py::AiBacktestRunner`）
```text
self._bars_in_market: int = 0     # 在场根计数（_run_loop 每根：pos.side!="flat" 时 +1；供 timeInMarketPct）
```
（其余成员不变；`_finalize` 内局部变量 `ext`/`bench`，不新增长驻状态。）

### 5.3 前端类型（`lib/aiBacktestTypes.ts`，camelCase）
```ts
export interface AiBacktestExtendedMetrics {
  annualizedReturn: number; annualizedVolatility: number; sortino: number; calmar: number;
  maxDrawdownBars: number; maxDrawdownSecs: number; timeInMarketPct: number;
  avgWin: number; avgLoss: number; payoffRatio: number; largestWin: number; largestLoss: number;
  maxConsecutiveWins: number; maxConsecutiveLosses: number; avgHoldBars: number; tradesPerWeek: number;
}
export interface AiBacktestBenchmark {
  equity: EquityPoint[];
  totalReturn: number; maxDrawdown: number; sharpe: number; annualizedReturn: number; alpha: number;
}
// AiBacktestResult 追加（可选，向后兼容旧版结果）
//   extendedMetrics?: AiBacktestExtendedMetrics;
//   benchmark?: AiBacktestBenchmark;
```

### 5.4 持久化（无新表、无迁移、无升版）
`persist_backtest_result` 的 `metrics: dict` 经 `json.dumps` 落 `strategy_runs.metrics_json`（自由 JSON）；`metrics_full` 追加 `extendedMetrics`/`benchmark` 即随之持久化。前端 store 无改动（`aiBacktestJobId` 瞬态、`aiBacktestConfig` 偏好均不变，**不升版**）。

---

## 六、Testing & acceptance criteria（测试与验收）

### 6.1 后端单测（离线 pytest，CI 必跑；无外部依赖）
- `test_metrics_ext.py`（纯函数）：构造已知 equity/trades，逐键断言 Sortino/Calmar/CAGR/年化波动/`maxDrawdownBars`/`timeInMarketPct`/盈亏比/连胜连亏/`avgHoldBars`/`tradesPerWeek`；**退化**：无成交→质量类 0、单根→年化不抛、全胜→`avgLoss=0` 且 `payoffRatio=0`、`maxDrawdown=0`→`calmar=0`、`base<=0`→`annualizedReturn=-1`。
- `test_benchmark.py`（纯函数）：单调上涨收盘→`totalReturn==closes[-1]/closes[0]-1`、`maxDrawdown<=0`、`alpha` 语义正确；平盘→全 0；空/单点/`closes[0]<=0`→`{}`；**[P1-2] `sharpe` 与 `compute_metrics` 逐位相等**——同一组 `bar_returns` 分别喂给共享 `_annualized_sharpe` 与 `compute_metrics`，断言 `sharpe` 完全一致（ddof=1、同年化因子）。
- `test_ai_backtest_runner.py`（补 + 回归）：跑一遍桩 LLM 回测，断言 `result.extendedMetrics`/`result.benchmark` 键完整、`benchmark.alpha == metrics["totalReturn"] - benchmark.totalReturn`、`_bars_in_market` 与在场根一致且 `== len(equity_points)` 分母对齐、`metrics_json` 落库含两键、**[P2-3] `GET /…/{job_id}` 快照（`AiBacktestResultOut`）亦含两键**；派生失败（构造抛错的桩）时主 `result`/落库不受影响且有 warning 日志；**回归**：既有七项/aiStats/流式/取消/落库口径**逐字不变**——**[P2-6] 若既有断言为「精确键集/整包相等」，同步改为超集断言**（属测试改动、非行为变更）。

### 6.2 前端单测（vitest，`NODE_ENV=test`，CI 必跑）
- `backtestStats.test.ts`：`underwaterSeries`（缺 drawdown 兜底）、`periodReturns`（日/周分桶正确、桶过多自动降密）、`maxDrawdownWindow`（最深区间）；空/单点边界。
- `export.test.ts`（补）：`resultToJson` 序列化含全部键、可 `JSON.parse` 回读；**[P1-4] `downloadJson` 产出的内容不含前导 BOM、MIME 为 `application/json`、可被 `JSON.parse` 直接回读**（对比 `downloadCsv` 仍带 BOM）；`downloadCsv`/`downloadJson` 无 `document` 时安全返回（既有断言不动）。
- `AiBacktestModal.test.tsx`（补）：完成态分页签切换到「进阶指标」显扩展网格、「成交」CSV 导出触发 `downloadCsv`、JSON 导出触发 `downloadJson`（mock）；`KPIHero` 有基准显 alpha；**[P1-1] `profitFactor`（盈利因子）与 `payoffRatio`（盈亏比·赔率）中文标签不重名**；**旧版结果（无 `extendedMetrics`/`benchmark`）不崩、相关分区隐藏**；运行态与页头零回归。

### 6.3 端到端联调（opt-in，手动，不进 CI；无真实资金）
配置默认 LLM 模型（回测不需实盘账户/双闸）：图表选 `BTC-USDT-SWAP`/`1H`，AI 弹窗切「回测」，选 2~3 策略、近 30 天、`decisionEveryBars=4`、小 `contextRefreshRounds` → 开始。完成后验证：**概览**页 KPI 概览含超额 alpha、权益图叠加「买入持有」虚线、水下回撤面积图；**进阶指标**页显 Sortino/Calmar/年化/盈亏比/连胜连亏等 + 策略 vs 基准并排 + 周期收益柱；**成交**页三个导出按钮各生成正确文件；在「运行历史」找到该 `mode="ai-backtest"` run，其 `metrics_json` 含 `extendedMetrics`/`benchmark`。回归：旧版本已落库的 AI 回测结果在面板中不报错、优雅降级。

### 6.4 Definition of Done（本设计交付物）
- 本 `spec.md` 已建并见于 `git status`；含 Overview / Technical design / File change plan / Interface design / Data model / Testing & acceptance / Risks 全部小节，篇幅 ≥800 字。
- 文件/模块变更计划逐项可被下游工程师按行实现，无需再决策。

---

## 七、Risks & mitigations（风险与缓解）

| 风险 | 影响 | 缓解 |
|---|---|---|
| **误改 `compute_metrics` 破坏既有回测** | 全站回测回归 | B1 是**新文件** `metrics_ext.py`，**严禁**触碰 `metrics.py`；`compute_metrics` 七项契约冻结。 |
| **改动回测运行器引入行为回归** | 破坏已交付回测 | 运行器改动**仅加法**（1 行 O(1) 计数 + `_finalize` 两次纯函数调用 + 两个结果键）；`test_ai_backtest_runner.py` 回归断言既有七项/aiStats/流式/取消**逐字不变**。 |
| **年化/Sortino/Calmar 边界除零或对负数开分数次幂** | NaN/异常/崩任务 | 纯函数全边界守护：`std==0/downside==0/maxDrawdown==0` → 0；`base<=0` → `annualizedReturn=-1`；空/单根回落 0，**绝不抛**（`_finalize` 内再包一层，**[P2-2] 派生失败先 `log.warning`（含 jobId+异常）再回落空 dict**，不静默吞、不影响主结果与落库）。 |
| **基准口径与权益曲线窗口不一致致 alpha 误导** | 结论失真 | 基准严格取 `main_df.iloc[first:]`（与 `SimBroker.equity_points` 同窗口、同起点），逐根收盘盯市、无前视；单测锁定同区间。 |
| **[P1-2] `sharpe`/波动率口径在 benchmark、metrics_ext、`metrics.py` 三处漂移** | 「策略 vs 基准」sharpe 不可比、误导投资判断 | 年化夏普只有一处实现 `_annualized_sharpe`（`metrics_ext` 私有，与 `metrics.py:41-46` **逐字同式**：`_bar_returns` 定义相同、**样本方差 ddof=1**、`BARS_PER_YEAR.get(bar,8760)`），benchmark 复用同一助手；`annualizedVolatility`/Sortino 分子同口径。**禁止**复制两份。`test_benchmark.py` 交叉校验 `_annualized_sharpe` 与 `compute_metrics` 同输入逐位相等。 |
| **旧版已落库结果缺新键致前端崩** | 复看报错 | 前端类型 `extendedMetrics?`/`benchmark?` 可选；各分区在缺失时**优雅隐藏**；组件测试覆盖缺失路径。 |
| **[P1-1] `profitFactor`（盈利因子）与新 `payoffRatio`（盈亏比·赔率）中文撞名** | 仪表盘两卡同名、误导用户 | UI 标签强制区分：`profitFactor`→「盈利因子（毛盈利/毛亏损）」、`payoffRatio`→「盈亏比·赔率（平均盈利/平均亏损）」，各带释义 tooltip；组件测试断言两标签不重名。 |
| **[P1-5] `AiBacktestModal.tsx`（现 409 行）内联新增破 800 行阈值** | 违反 `CLAUDE.md` 单文件上限 | 新图表/面板组件抽到 sibling `components/Chart/aiBacktest/{panels,charts}.tsx`；`AiBacktestModal.tsx` 仅装配 + import，改造后仍 <800 行、各组件 ≤50 行。 |
| **[P1-4] JSON 导出复用带 BOM 的 `downloadCsv` 破坏 `JSON.parse`** | 「导出完整结果 JSON」无法再读入 | JSON 走新增无 BOM 的 `downloadJson`（`application/json`）；`downloadCsv` 保持带 BOM（CSV/Excel 兼容）；`export.test.ts` 断言 JSON 无 BOM 可回读。 |
| **`EquityChart` 加基准 series 影响既有三处调用方（回测/实时/对比）** | 现网回归 | `benchmark?` 默认 `undefined`，仅 `length>0` 才建线/`setData`，否则 `setData([])`；对比模式（overlays）不受影响；快照/渲染测试守护。 |
| **完成态一次性渲染全套图表致长回测卡顿** | 体验 | 分页签**懒渲染**（非激活页不挂重组件）；水下图/周期收益为轻量内联 SVG（同既有 `Sparkline`），权益图仍用既有 `EquityChart`（lightweight-charts）。 |
| **导出大结果 JSON 阻塞主线程** | 卡顿 | 导出为用户显式点击、一次性；`downloadCsv`/Blob 为浏览器原生；结果规模受既有 `decisionsDigest≤200`/落库摘要口径约束。 |
| **`avgHoldBars`/在场占比在部分平仓/反手场景配对歧义** | 指标偏差 | 以 `SimBroker` 成交序列的 open→close 配对为准、反手视为「先平后开」两段；`timeInMarketPct` 以运行器逐根 `pos.side` 计数为**权威**，成交对推断仅作无计数时兜底；单测覆盖反手/多次开平。 |
| **文档/实现偏离本方案自行扩张范围** | 破坏「继续完善」边界 | 本包严格四项、全加法、默认等价；如实现中发现更优结构，记入 spec「实施过程发现的方案缺陷」段，不擅自改实盘/决策/护栏。 |

---

## 八、实施顺序建议（供 Subtask #2 参考）

1. **后端纯件先行**：`engine/metrics_ext.py`（含私有助手与全边界守护）+ `test_metrics_ext.py`；`engine/benchmark.py`（复用同一夏普助手）+ `test_benchmark.py`。先把两个纯函数用单测钉死，再接线。
2. **运行器接线**：`ai_auto/backtest.py` 增 `_bars_in_market` 计数 + `_finalize` 算 `ext`/`bench` + `_build_result`/`_persist` 追加两键 + `派生失败包一层`；`schemas.py` 追加两可选字段；补 `test_ai_backtest_runner.py` 回归 + 新键断言。
3. **前端类型与纯件**：`lib/aiBacktestTypes.ts` 两接口 + `AiBacktestResult` 两可选键；`lib/backtestStats.ts`（`period` 含 month、>60 桶降密）+ `backtestStats.test.ts`；`lib/export.ts` 加 `resultToJson` + **无 BOM 的 `downloadJson`** + `export.test.ts` 补（含 JSON 无 BOM 回读）。
4. **共享组件小改**：`components/Chart/EquityChart.tsx` 加 `benchmark?` 虚线（默认零变化，仅单条 equity 分支）。
5. **完成态仪表盘**：先建 sibling `components/Chart/aiBacktest/{panels,charts}.tsx` 放 `KPIHero`/`ExtendedMetricsGrid`/`BenchmarkCompareCard`/`UnderwaterChart`/`PeriodReturnBars`（各 ≤50 行）；再把 `AiBacktestModal.CompleteView` 重构为分页签并 import 装配 + 导出条；`AiBacktestModal.test.tsx` 补（分页签切换、标签去歧义、JSON 走 `downloadJson`、旧版结果降级）。运行态/页头/页脚不动，**改造后 `AiBacktestModal.tsx` <800 行**。
6. opt-in 联调（§6.3，短区间 + 大间隔控成本），确认概览/进阶/成交/AI 决策/上下文五页与导出、基准叠加、旧版降级均达标后收尾。

---

> 版本：**v2**（iteration 2 · 方案设计 v1 + 评审加固）· 复用基线：`engine/metrics`（BARS_PER_YEAR，零改动）、`engine/simulator`（EquityRecord/TradeRecord/SimBroker 快照，零改动）、`ai_auto/backtest`（运行器收尾追加）、`ui/Tabs` + `lib/export` + `Chart/EquityChart`（前端复用/小改）。核心设计：**在已交付回测的输出端做纯加法**——扩展绩效指标（B1）+ 买入持有基准与 alpha（B2）+ 水下回撤与周期收益（B3）+ 分页签仪表盘与导出（B4），把「各种回测指标结果」补齐为专业量化全套、把结果呈现升级为顶级 HCI，且**默认等价、零回归、无迁移、不碰实盘与决策链路**。

---

## 评审结论（Review Verdict · v2）

**有条件通过（Approved with conditions）。**

**为什么通过**：本方案定位准确（本轮需求「继续完善」的两处强调——「显示各种回测指标结果」「美观优雅顶级 HCI」——被精准落到 B1~B4 四项纯加法上），边界清晰、默认等价、零迁移、不碰实盘与决策链路；全部 as-built 复用锚点经真实代码逐条核验，绝大多数**准确**（含关键的窗口对齐：`_run_loop` 迭代 `range(first,n)` 使 `equity_points` 与基准 `iloc[first:]` 天然等长对齐，原 alpha 窗口错位隐患不成立）。**无 P0**。5 个 P1 已在 v2 正文直接修订完毕（`profitFactor` 命名去歧义、夏普/波动率 ddof=1 与 `metrics.py` 逐位可比、平仓笔 `side=="close"` 同源选取、JSON 导出改无 BOM 的 `downloadJson`、新展示组件抽 sibling 文件守 800 行阈值）。

**开发节点（Subtask #2）落地必须满足的条件（P2，纳入验收门禁）**：
1. **[P2-1]** `maxDrawdownSecs` 复用 `engine/mtf.bar_to_seconds`，**不得**在 `metrics_ext.py` 重造 bar→秒表。
2. **[P2-2]** B1/B2 派生失败的 `try/except` **必须 `log.warning`（含 jobId+异常）** 再回落空 dict，不得静默吞（CLAUDE.md §5）。
3. **[P2-3]** 确认 `GET /…/{job_id}` 快照的 `AiBacktestResultOut` 序列化路径**真正带上** `extendedMetrics`/`benchmark`（两键为带默认的可选 dict 字段），并有测试断言。
4. **[P2-4]** `periodReturns` 按明确的 `>60 桶` 规则在 day→week→month 间自动降密，month 为最粗粒度。
5. **[P2-5]** `EquityChart` 的 `benchmark` 仅在单条 `equity`（绝对轴、非 overlays 归一化）分支渲染。
6. **[P2-6]** 若既有 runner/前端测试存在「精确键集/整包相等」断言，同步改为超集断言（属测试改动、非行为变更）。

**硬性红线（复述，越线即判不通过）**：不改 `engine/metrics.py`（`compute_metrics` 七项契约冻结）；不改实盘链路、决策脑、护栏、取消/流式；不改启动弹窗与运行态 `RunningView`；既有回测行为与单测**逐字不变**（新增字段仅为超集）；不新增真单路径；`git commit` 由后续节点执行（本设计/评审节点不提交）。

> 评审人：Anthropic 工程团队高级评审（design review 节点）· 评审维度：可行性 / 完备性 / 一致性 / 合理规模 · 结论：**有条件通过**，条件为上列 6 项 P2；无 P0/P1 遗留。

---

## 实施过程发现的方案缺陷（Issues Found During Implementation · Subtask #2）

> 本节由代码开发节点在落地过程中追加。落地严格遵循「文件/模块变更计划」，仅下列一处对 P1-1 的**落地口径**做了范围内调整并在此备案，其余方案逐条实现、全部 P2 条件满足。

### I-1（P1-1 落地口径调整）：`profitFactor` 中文标签不能在共享 `MetricsCards` 内改名

- **方案原文（§2.5 / P1-1）**：要求既有第七项 `profitFactor` 在 UI 一律标「盈利因子（毛盈利/毛亏损）」，与 B1 新增 `payoffRatio`「盈亏比·赔率」区分，两卡中文标签不得重名。
- **实施发现**：`profitFactor` 卡由**共享组件** `components/Strategy/MetricsCards.tsx` 渲染（现标「盈亏比」），该组件被 **10 个文件**复用（策略回测 / 纸面 / 走查 / 组合 / 权重回测等）。它**不在**本方案「文件/模块变更计划」表内；若在其中把「盈亏比」改成「盈利因子」，会波及本需求**无关**的多处视图，违反交付红线「`git status` 无无关改动」。
- **采取的修正**：保持 `MetricsCards` **零改动**（profitFactor 仍显示「盈亏比」），只在**新增**的 `ExtendedMetricsGrid` 把 `payoffRatio` 标为**独立不重名**的「盈亏比·赔率」并带释义 tooltip（平均盈利÷平均亏损，注明区别于盈利因子＝毛盈利/毛亏损）。两卡中文标签为**不同字符串**，满足 P1-1「不得重名」的核心诉求；`AiBacktestModal.test.tsx` 断言二者为不同元素、且旧版结果时扩展网格优雅隐藏。彻底把 `profitFactor` 改名为「盈利因子」须另立一个覆盖全部 `MetricsCards` 调用方的独立任务，超出本分析增强包范围。

### 落地符合性小结（P2 条件全部满足）

- **[P2-1]** `maxDrawdownSecs` 复用 `engine/mtf.bar_to_seconds`（`metrics_ext.py` 直接 import，未重造 bar→秒表）。
- **[P2-2]** B1/B2 派生失败在 `AiBacktestRunner._extended_metrics/_buy_hold_benchmark` 内 `try/except` → `logger.warning`（含 `job.job_id` 与异常）→ 回落 `{}`，主 `result`/落库不受影响（`test_analytics_derivation_failure_does_not_break_result` 覆盖）。
- **[P2-3]** `GET /…/{job_id}` 走 `job.status_dict()` 原样透传 runner dict（含两键），`AiBacktestResultOut` 亦补带默认可选字段；`test_extended_metrics_and_benchmark_present_persisted_and_in_snapshot` 断言快照含两键。
- **[P2-4]** `periodReturns` 按 `>60 桶` 规则 day→week→month 自动降密，month 为最粗粒度（`backtestStats.test.ts` 覆盖）。
- **[P2-5]** `EquityChart.benchmark` 仅在单条 `equity`（绝对轴）分支渲染，`hasOverlays` 时 `setData([])`。
- **[P2-6]** 既有 runner/前端测试为子集式断言，未见「精确键集/整包相等」断言，新增两键不破坏；`AiBacktestModal.test.tsx` 因完成态改分页签已同步更新（属测试改动、非行为变更）。
