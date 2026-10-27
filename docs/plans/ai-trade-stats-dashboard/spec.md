# AI 自动交易统计看板（ai-trade-stats-dashboard）开发方案

> 版本：**v2**（方案评审与修复，Subtask #1 产出；v1 为 Subtask #0 方案设计）
> 作者：solution architect（Anthropic 工程团队）；评审：senior reviewer（Anthropic 工程团队）
> v2 变更摘要：逐节对照真实源码核对 v1 全部代码断言（行号 / 函数签名 / 数据结构 / 落库路径），修复 5 个 P1 与 8 个 P2，详见文首《评审记录》与文末《评审结论》。
> 适用仓库：`M:/takoAI/AragonTrading`（monorepo：`apps/web` Next.js 14 + `services/quant` FastAPI + SQLite）
> 关联既有方案：`docs/plans/ai-auto-trading/spec.md`、`docs/plans/ai-trade-multi-symbol-and-timeframe/spec.md`、`docs/plans/auto-trade-pnl-records/spec.md`、`docs/plans/ai-auto-trading-backtest-analytics/spec.md`
> 参考实现（同仓在制、镜像蓝本）：`services/quant/app/db/live_auto_repositories.py`（live-auto「历史会话」聚合读层，未提交）、`services/quant/app/api/live_auto.py` 的 `/live-auto/history*` 端点、`services/quant/app/schemas.py` 的 `AutoTradeHistory*` 系列

---

## 评审记录（Review Notes）

> 本节由 Subtask #1（方案评审与修复）产出。评审方法：以四个并行只读核查通道逐文件核对 v1 的**全部代码断言**（后端 `ai_auto/manager.py`+`session.py`、`db/repositories.py`+`models.py`+`session.py`+`live_auto_repositories.py`、`api/ai_auto.py`+`api/live_auto.py`+`schemas.py`，前端 `AgentPanel/store/aiTradeTypes/autoTradePnl/aiBacktest/ui/format/aiTradeApi/globals.css`）。
> **核查总体结论**：v1 的架构判断与「零 DB 迁移、镜像 live-auto 持久化模式」的核心接地**全部属实且可落地**——`create_run` 的三个 group_* kwargs、`_ensure_live_auto_group_columns` 建的三列 + `ix_strategy_runs_group` 索引、`_reconcile_orphan_live_auto_runs` 接线点、`live_auto_repositories` 五函数、`schemas.py:697-790` 的 `AutoTradeHistory*` + `trade_out`、`api/live_auto.py:174-197` 端点、前端 `autoTradePnl`/`AutoTradeRoundTripRow`/`aiBacktest`/`Modal`/`Tabs`/设计 token/TanStack Query 均**逐一核实存在且签名吻合**。但发现 **5 个 P1**（其中 3 处为 v1 代码 sketch 直接对不上真实符号、会在开发期即报错，2 处为正确性缺口）与 **8 个 P2**，均已在下方就地修复或明确接地。**无 P0**。

### 严重度定义
- **P0**：方案根本不可行 / 会造成数据错误或资金安全风险，必须修复否则不可进入开发。
- **P1**：代码断言与真实源码不符（会在开发期即失败）或存在正确性缺口，必须就地修复。
- **P2**：完整性 / 一致性 / 措辞欠精确，就地澄清以免开发期误解。

### P1（已就地修复）

| 编号 | 维度 | 问题（v1） | 真实源码 | 修复（v2） |
|---|---|---|---|---|
| **P1-1** | 可行性 / 正确性 | §3.3 / R-4 假设「`_finalize_run` 调用点被包裹或就地 try 兜底」待开发期确认 | `session.py:190-210` 的 `stop()` 中，`try/except` **只包住 flatten**；`await self._finalize_run(ts)`（:208）在其**之外、无任何 try**，`_finalize_run`（:679-693）内部亦无 try——富化段一旦抛错会中断 `stop()` 收尾（跳过 :209 的 status push、并使 run 卡在 `running`）| §3.3 改为**强制**在 `_finalize_run` 内对三段 digest 构造就地 `try/except`（异常→退回不含富化字段的基础 metrics dict，仍照常 `finalize_run`）；R-4 由「待确认」升级为「必做」|
| **P1-2** | 可行性 | §3.8 前端 sketch 调用 `apiGet<...>`，并称「复用既有 apiFetch/apiGet」 | 仓库**无** `apiGet`/`apiFetch` 导出；`lib/api.ts` 的 `request`/`rawRequest` 为**模块私有**（仅导出 `api` 对象与 `ApiRequestError`）；`aiTradeApi.ts` 自带私有 `request<T>`（`BASE="/api/py"`、`cache:"no-store"`）| §3.8 重写为**镜像 `aiTradeApi.ts`**：本地私有 `request<T>` + `BASE="/api/py"` + 复用 `ApiRequestError`；不再引用不存在的 helper |
| **P1-3** | 可行性 | §3.2 / §11 sketch 用 `group_started_ts=ctx.started_ts` 且把「ctx 是否有组级时间戳」列为待确认 | `_GroupBuildCtx`（`manager.py:160-172`）**无** `started_ts`；组级秒级起点是 **`created_ts: int`**（`:308` `int(time.time())`，`group_id` 在 `:312` 生成）| §3.2 改为 `group_started_ts=ctx.created_ts`；§11 该待确认项标记为**已解决** |
| **P1-4** | 可行性 / 一致性 | §3.10.1 / §6.3「当前持仓浮盈条 = `Σ member.position?.unrealizedPnl ?? 0`」 | `AiTradeGroupMemberState.position` 及所有相关字段类型均为 **`unknown \| null`**（`aiTradeTypes.ts:143/158/262/338`），**无** typed `unrealizedPnl`——直接取属性既过不了 `tsc`（不能索引 `unknown`），字段是否存在亦未证实（触碰「绝不伪造」红线）| §3.10.1/§6.3 改为：定义窄化守卫 `PositionLike`（可选数值字段），**仅当各成员均取到真实数值时才渲染浮盈条、否则整条略去**（诚实降级）；仍禁用 `sessionPnlUsd` 减法式；§11 增「确认 WS `position` 载荷承载未实现盈亏的真实字段名」|
| **P1-5** | 正确性 | §3.10.3「合并各成员 `equityDigest` 画权益 sparkline + UnderwaterChart」未定义合并口径 | 本 feature 面向**多标的分组**、多成员是一等场景；各成员 `equityDigest` 是**独立 run 的独立时间序列**，朴素拼接会产生锯齿错图，且各成员 `drawdown` **不可相加** | §3.10.3 明确：把各成员 `equityDigest` **对齐到并集时间轴、按成员「前值填充」求和**得组合权益，再由 `underwaterSeries` **在求和曲线上重算回撤**；或退化为逐成员 sparkline。明令禁止拼接 / 相加 drawdown |

### P2（已就地澄清 / 接地）

| 编号 | 维度 | 问题 | 修复（v2） |
|---|---|---|---|
| **P2-1** | 完整性 | §3.10.3「复用 `KPIHero`」不匹配：`KPIHero` 需完整 `Metrics`（sharpe/maxDrawdown/profitFactor…），历史 `summary` 只有 realized/returnPct/winRate/tradeCount/best/worst | 改为**自绘 KPI 网格**（或 `equityDigest` 存在时由其派生扩展指标），不硬套 `KPIHero` |
| **P2-2** | 完整性 | §3.10.2 拟显示 `reconciled` 状态 Badge，但对账后 `run.status` 仍是 `"stopped"`、`metrics.reconciled=True` 才是标记，而 `AiTradeHistoryEntry` 无该字段 | 列表卡不承载；改在**详情页**据 `member.metrics.reconciled` 呈现「已对账」chip（§3.10.3） |
| **P2-3** | 完整性 | §3.4 对账 `end_ts=max(成交/权益/group_started/start)` 含「权益」项，但 AI-trade **无持久化 equity 表**（仅 finalize 一次性 digest），崩溃 run 无 metrics | 改为 `end_ts=max(最后成交 ts, group_started_ts, start_ts)`，去掉「权益」项 |
| **P2-4** | 完整性 | 历史详情把 `trade_out` dict 当 `Trade[]` 喂 `buildLedger`；`trade_out` 出 `pnl`（非 `pnlPct`）、无 `seeded`/`pnlPct` | §7.2 增断言用例：`buildRoundTrips` 消费字段（side/ts/price/pnl）在 `trade_out` 全有；缺 `pnlPct`→`?? derivePct`、缺 `seeded`→按 false 处理均安全 |
| **P2-5** | 完整性 | `_compute_ai_stats` 的 `actionCounts` / `degradedRounds` 口径未接地 | §3.3 注明：`actionCounts` 键匹配 `_decisions[].action` 实际枚举（open_long/open_short/close/hold），`degradedRounds` 用 `degradeReason` **非空**计数（`_decisions` 无布尔 `degraded`） |
| **P2-6** | 一致性 | `Tabs` 是**数据驱动**（`tabs:{key,label}[]`+`active`+`onChange`），非 children | §3.10 注明：面板本地持 `active` state，按 key **条件渲染**两页内容 |
| **P2-7** | 一致性 | 测试环境约定未写明 | §7 注明：组件测试需 `NODE_ENV=test`（否则 React act() 报错）；后端 `test_ai_auto_history.py` 与既有 quant 测试共享临时 DB，须用既有 conftest fixture 并自清理 |
| **P2-8** | 完整性 | 时间戳单位未点明 | §6 注明：`startedTs/endedTs/trade.ts/equityDigest.ts` **全为秒**，`fmtTime` 按秒渲染 |

---

## 1. 概述（Overview）

本项目近期上线了 **AI 自动交易**（AI-trade）：用户在右侧「AI 助手」横栏点开 `AiTradeToggle` → `AiTradeLaunchModal`，选定标的（可多标的分组）、K 线周期、若干策略、总预算 / 杠杆 / 会话止盈止损 / 每仓 OCO 后，由 Agent LLM 作为「链式思考决策者」在每根确认 K 线上综合多策略信号板 + 市场行情摘要给出开多 / 开空 / 平仓 / 观望决策，并经既有 `LiveExecutor` 下真实 OKX 订单。运行期的成交、决策、盈亏通过 `/api/ws/ai-trade/{sessionId}` 逐成员流入前端 `store.aiTradeGroup`，由左侧 `AiTradePanel`（分组盈亏）与右侧 `AiTradeAgentTrace`（执行轨迹）实时呈现。

但当前**没有为 AI 自动交易提供一个可点击、可回溯、可持久化的「统计看板」**。具体缺口有三：其一，**盈亏 / 概况没有正确聚合**——前端只有 `Σ sessionPnlUsd`（已实现+未实现混合的会话级实时数字），没有「每次完整交易（开→平往返）的收益与收益率」「按胜率 / 最佳 / 最差 / 交易笔数」等口径清晰的统计；其二，**没有查看入口**——`aiTradeGroup` 是纯瞬态状态（不入 `partialize`），刷新即失、整组停止即清，`aiTradeApi` 仅有 `start/stopGroup/stopMember/status` 四个方法，`status` 只回**当前活跃**的那一组，没有任何「历史会话列表 / 单会话详情」接口或界面；其三，**没有持久化回溯**——尽管每个 AI-trade 会话在启动时已落一行 `strategy_runs(mode="ai-trade")`、每笔成交经 `append_live_trade` 落 `trades` 行、停止时经 `_finalize_run` 落终态 `metrics_json`，但这些持久化数据**从未被任何读端点 / UI 消费**，用户无法在停止或重启后查看「我历史上跑过哪些 AI 自动交易、各自赚亏多少」。

本方案为 AI 自动交易补齐**统计看板**能力：在右侧「AI 助手」横栏 `AiTradeToggle` **左侧**新增一个「统计」按钮，点击打开一个美观、信息密度合理、符合 HCI 最佳实践的看板 `Modal`，含「**当前**」（正在运行的分组实时统计，复用瞬态 `aiTradeGroup`）与「**历史**」（已持久化的跨会话列表 + 单会话详情下钻，来自新的后端只读端点）两个视图。设计的核心策略是**最大化复用已存在的真实数据与既有实现**：后端**不新建任何存储位置、零 DB 迁移**——AI-trade 的 run / trade 已落库，只需（a）让 AI-trade 的 run 填上既有但当前为 NULL 的 `group_id` 分组列以支持按会话聚合、（b）新增一个**逐字镜像** `live_auto_repositories.py` 的只读聚合层 `ai_auto_repositories.py`、（c）新增两个只读 REST 端点、（d）把 AI-trade 纳入启动期孤儿对账、（e）在 `_finalize_run` **加法**富化 `metrics_json`（权益摘要 / 决策摘要 / AI 计数），使详情页能画出权益曲线与 AI 决策概况。前端复用经测的纯函数 `lib/autoTradePnl.ts`（往返配对 + 汇总）、`AutoTradeRoundTripRow`（单条完整交易行）、AiBacktest 看板的 `KPIHero`/指标网格 / 自绘 SVG 图与统一的设计 token（profit=`--long` / loss=`--short` / `colorForReturn`），使新看板与全站「最左侧自动交易统计面板」及 AI 回测分析看板的观感一脉相承。全程严格遵循 CLAUDE.md「绝不伪造数据 / 单一数据源 / 显式降级 / 因果时序」约定：所有盈亏与统计均由真实成交（真实 OKX 回执）派生，历史数据来自真实落库行，无任何模拟 / 估算的「看起来像收益」的数字。

---

## 2. 现状与关键代码路径（Grounding）

> 本节所有结论均经源码核对（行号以核对时为准，实现时以符号为锚二次确认）。

### 2.1 后端：AI-trade 已经在持久化，但「读侧」完全缺失

- **run 落库（每成员一行）**：`ai_auto/manager.py::_create_group_run`（约 `:525-546`）在启动时对每个成员调 `repo.create_run(..., mode="ai-trade", config={...groupId/strategyIds/strategyNames/budgetUsd/leverage/allocationPct/allocationMode/totalBudgetUsd/tp/sl...}, status="running")`。**关键缺陷**：它**未传** `create_run` 已支持的 `group_id=/group_started_ts=/group_total_budget_usd=` 三个 kwargs（`repositories.py:132-147`），故 AI-trade 的这三列**恒为 NULL**——组标识只藏在 `config_json.groupId`。而 live-auto 走同一 `create_run` 却填了这三列。
- **trade 落库（每笔成交）**：`ai_auto/session.py::_persist_trade`（约 `:157-163`）→ `repo.append_live_trade(s, run_id, trade)`（`repositories.py:275-293`），best-effort、失败仅告警不阻断交易；`trade.explain`（`source:"ai"` + 置信度 + 信号板摘要）序列化进 `Trade.explain_json`。**开仓 `pnl=0`，平仓 `pnl=已实现盈亏`**，与 live-auto 同构；执行器内存字典里的 `clOrdId/ordId/pnlPct/seeded` 不入库（`append_live_trade` 逐字段显式映射，忽略未知键）。
- **终态落库（停止时）**：`ai_auto/session.py::_finalize_run`（约 `:679-693`）→ `repo.finalize_run(..., status="stopped", metrics={totalReturn=realized/budget, tradeCount, avgPnl, roundsCompleted, finalSummary, flattenFailed, degradedSeen}, end_ts=...)`（`repositories.py:301-317`）。**权益曲线（`self._equity` deque，maxlen=1000）、决策（`self._decisions` deque，maxlen=50）、执行轨迹（`self._traces`）全部仅在内存 / WS，从不落库。**
- **孤儿对账缺 AI-trade**：`db/session.py::init_db`（`:68-81`）在 `create_all` + 4 个 `_ensure_*_columns` 幂等补列后，`:80` 调 `_reconcile_orphan_live_auto_runs()`（`:167-182` → `repo.reconcile_orphan_live_auto_runs`），后者**仅** `mode=="live-auto" AND status=="running"`（`live_auto_repositories.py:243-255`）。**AI-trade 进程崩溃 / 强杀而未走 `_finalize_run` 的 run 会永久卡在 `status="running"`（「幽灵进行中」）**，历史列表将失真。
- **读端点缺失**：`api/ai_auto.py` 仅 `POST /ai-trade/start`、`GET /ai-trade/status`（回**当前活跃组**或 null）、组 / 成员 `stop`、`WS /ws/ai-trade/{sessionId}`，以及一套 backtest 端点。**没有任何 `/ai-trade/history*`。**

### 2.2 后端：可直接镜像的「历史会话聚合读层」（同仓在制）

同仓正在实现 live-auto 的「历史会话」持久化（feature `auto-trading-earnings-persistence`，当前**未提交**：`git status` 显示 `?? services/quant/app/db/live_auto_repositories.py`、` M services/quant/app/api/live_auto.py`、` M services/quant/app/live_auto/manager.py`），是本方案后端的**逐字镜像蓝本**：

- `db/live_auto_repositories.py`（新，未提交）：以 `strategy_runs.group_id` 为「一次交易会话」一等实体。
  - `_pair_round_trips(trades)`：按 `(ts, id)` 排序、单持仓 `open→close` 配对（逐字移植前端 `tradeTrails`），**逐成员分桶后调用、绝不跨 instId**。
  - `_summarize_pairs(pairs, total_budget)`：`realized=Σexit.pnl`、`returnPct=realized/total_budget`、`tradeCount/winRate/bestUsd/worstUsd`，**口径与前端 `autoTradePnl.summarizeRoundTrips` 严格一致**。
  - `list_live_auto_groups(*, limit, offset, exclude_group_id)`：`mode=="live-auto" AND group_id IS NOT NULL`，`group by group_id`、`order by max(group_started_ts) DESC`、分页；3 查询/页（去重分组分页 + 成员 runs + 成交）；返回 `{items, total, limit, offset}`。
  - `get_live_auto_group_detail(group_id)`：会话头 + `members[]`（`runId/instId/leverage/budgetUsd/trades(ORM Trade)`）+ 后端聚合 `summary`；组不存在 → None。
  - `reconcile_orphan_live_auto_runs()`：`running` 的 live-auto run 回填 `end_ts`（`max(最后成交 / 最后权益 / group_started_ts / start_ts)`）+ 置 `stopped` + `metrics.reconciled=True`；幂等。
  - 经 `repositories.py:1002-1008` re-export 为 `repo.list_live_auto_groups` / `repo.get_live_auto_group_detail` / `repo.reconcile_orphan_live_auto_runs`。
- `schemas.py:697-765`：`AutoTradeHistoryEntry`（列表行）/ `AutoTradeHistoryListOut`（`{items,total,limit,offset}`）/ `AutoTradeHistorySummary` / `AutoTradeHistoryMember`（`trades: list[dict]` 透传 `trade_out` 全字段）/ `AutoTradeHistoryDetailOut`，全 camelCase。
- `schemas.py:781-790`：`trade_out(t)` 序列化器（恒含 `explain` 键，缺列 / 坏 JSON → None），可**直接复用**。
- `api/live_auto.py:174-197`：`GET /live-auto/history`（从 `get_live_auto_manager().group_status()` 取活跃组 id 作 `exclude`）、`GET /live-auto/history/{group_id}`（`trade_out` 序列化 members[].trades，None → 404）。

> **决策 D-0（自包含，不依赖在制模块）**：`live_auto_repositories.py` 尚未提交、可能变动。本方案**不复用 / 不 import** 该模块，而是新建**自包含**的 `ai_auto_repositories.py` 镜像其结构与口径（配对 / 汇总数学与前端 `autoTradePnl` 逐值对齐）。这与仓库既有「`repositories.py` 按 factor/strategy/weight/live_auto 拆子仓、各自相对自包含」的范式一致；代价是配对 / 汇总数学的一处刻意重复（已在 §10 R-6 记录，待 `auto-trading-earnings-persistence` 落地稳定后可抽公共 helper）。

### 2.3 前端：可复用的实现与设计语言

- **入口位置**：`components/Agent/AgentPanel.tsx` 头部右簇 `<div className="flex items-center gap-2">`（约 `:325-330`）内，顺序为 `<Button …>新对话</Button>` 后紧跟 `<AiTradeToggle />`（`:329`）。**新按钮插在 `:328` 与 `:329` 之间**即落在 `AiTradeToggle` 左侧，继承父级 `gap-2`。`Button`（`components/ui`）已 import。
- **瞬态运行态 store**：`store.aiTradeGroup: AiTradeGroupState | null`（`store.ts:253`，默认 `null` `:514`，**不入 `partialize`**，仅 `aiTradeConfig` 持久化）。成员 `AiTradeGroupMemberState`（`aiTradeTypes.ts:332`）含 `instId/allocationPct/session(AiTradeSessionRow)/trades:Trade[]/position/decisions/traces/summary`；PnL 在 `session.sessionPnlUsd`（已实现+未实现），持仓浮盈在 `position.unrealizedPnl`。聚合 PnL = `Σ members[].session.sessionPnlUsd`（`AiTradePanel.tsx:33`）。
- **纯函数收益聚合（可原样复用）**：`lib/autoTradePnl.ts`——`buildRoundTrips(instId, trades, leverage)`（先滤 `seeded`、复用 `buildTradeTrailSegments` 因果配对、`pnlPct = exit.pnlPct ?? derivePct(...)`）、`buildLedger(members[])`（逐成员、不跨标的）、`resolveRangeBounds(range, nowSec, custom)`、`filterRoundTrips`、`summarizeRoundTrips(rts, capitalBaseUsd)`（`count===0→winRate=0`、`capitalBase<=0→returnPct=0`）。**AI-trade 成交与 live-auto 同构，可原样喂入。**
- **可复用组件**：`components/Chart/AutoTradeRoundTripRow.tsx`（`{rt, open, onToggle}`，折叠态显示方向 Badge / 开平价 / 时长 / `signed(pnlUsd)` / `fmtPct(pnlPct)`，可展开 `TradeExplainDetail`）；`components/Chart/aiBacktest/panels.tsx`（`KPIHero`{metrics,benchmark?} / `ExtendedMetricsGrid` / `BenchmarkCompareCard`）；`components/Chart/aiBacktest/charts.tsx`（自绘 SVG `UnderwaterChart`{equity} / flex `PeriodReturnBars`{equity}）；`components/Strategy/MetricsCards.tsx`（核心指标卡 + `Skeleton` 加载态）；`components/ui`（`Modal`{open,onClose,labelledBy?,className?}、`Button`、`Badge`、`Tabs variant="underline"`）。
- **格式化器**：`lib/format.ts`——`fmtPct/signed/colorForReturn/fmtPrice/fmtTime/fmtNum/fmtMoney`；`lib/backtestStats.ts`——`underwaterSeries/fmtDuration` 等由 `equity` 派生（喂 `equityDigest` 亦可）。
- **设计 token**（`app/globals.css`）：`--long`（盈 / 绿）、`--short`（亏 / 锈红）、`--accent`（进度 / 权益线）、`--surface-2`（卡片底）、`--muted`（次要文字）、`--border/--bg/--surface/--text`；卡片惯用 `rounded-[12px]/[10px] border bg-[var(--surface-2)] px-3 py-2(.5)`，label `text-[10px] uppercase tracking-wide text-[var(--muted)]`，value `tabular font-semibold`，色值一律走 `colorForReturn(v)`。
- **数据获取约定**：DB 数据经 TanStack Query（非 localStorage）。历史看板用 `useQuery` + 新 `aiTradeHistoryApi`。

### 2.4 数据可达性结论

- **realizedPnlUsd / returnPct / tradeCount / winRate / best / worst**：可由持久化 `trades`（每 run）逐成员配对得出——**历史数据充分**。
- **每笔完整交易收益率 `pnlPct`**：`trades` 未存 `pnlPct`，详情页前端经 `derivePct`（价格变动% × 杠杆，杠杆取自 `config_json.leverage`）派生——与运行期口径一致，误差有界。
- **权益曲线 / 最大回撤 / 夏普 / 决策概况**：当前**不落库**。方案在 `_finalize_run` **加法**存 `equityDigest`（下采样 ≤500 点）+ `decisionsDigest`（≤50）+ `aiStats`（计数）进既有 `metrics_json`，使**正常停止**的历史会话详情可画权益 / 回撤图与 AI 决策概况；**崩溃 / 对账**会话缺这些字段时**优雅降级**（只显示由成交派生的 KPI + 往返，不画权益图）。

---

## 3. 技术设计（Technical Design）

### 3.1 架构总览

```
后端（真实数据源，零 DB 迁移）
  ai_auto/manager.py::_create_group_run
      └─(改)传 group_id / group_started_ts / group_total_budget_usd → create_run   # 让 ai-trade run 可按组聚合
  ai_auto/session.py::_finalize_run
      └─(改,加法)metrics_json 追加 equityDigest / decisionsDigest / aiStats           # 详情权益图 + AI 概况
  db/ai_auto_repositories.py（新，镜像 live_auto_repositories）
      ├─ list_ai_trade_groups(limit, offset, exclude_group_id) → {items,total,limit,offset}
      ├─ get_ai_trade_group_detail(group_id) → {会话头 + members[] + summary} | None
      └─ reconcile_orphan_ai_trade_runs() → 修「幽灵进行中」
  db/repositories.py            └─(改)re-export 上面 3 个符号
  db/session.py::init_db        └─(改)create_all 后再调 _reconcile_orphan_ai_trade_runs()
  schemas.py                    └─(改,加法)AiTradeHistory* 系列 + 复用 trade_out
  api/ai_auto.py                └─(改,加法)GET /ai-trade/history、GET /ai-trade/history/{groupId}
        │  只读、不守门（历史查看不下单）；exclude=活跃组 id（get_ai_trade_manager().group_status()）
        ▼
前端
  lib/aiTradeHistoryTypes.ts / aiTradeHistoryApi.ts（新）：wire 类型 + REST 客户端
  components/Agent/AiTradeStatsButton.tsx（新，自封装：按钮 + 自持 open state + 挂 Modal）
      └─ 插入 AgentPanel 头部 AiTradeToggle 左侧（AgentPanel 仅 +1 import +1 行）
  components/Agent/AiTradeStatsPanel.tsx（新）：看板 Modal 壳 + Tabs[当前 | 历史] + 详情下钻
      ├─ AiTradeStatsLive（当前）：读 store.aiTradeGroup → buildLedger/summarizeRoundTrips（复用 autoTradePnl）
      ├─ AiTradeStatsHistoryList（历史）：useQuery(/ai-trade/history) → KPI 卡列表 + 分页 + 空/加载态
      └─ AiTradeStatsDetail（详情）：useQuery(/ai-trade/history/{id}) → KPIHero + members + 往返行(AutoTradeRoundTripRow) + 权益图(equityDigest) + AI 概况
```

### 3.2 后端改动 1：让 AI-trade run 可按组聚合（`ai_auto/manager.py`）

`_create_group_run` 在 `repo.create_run(...)` 调用上**追加 3 个既有 kwargs**（列已由 `_ensure_live_auto_group_columns` 幂等建好，**零迁移**）：

```python
run = await repo.create_run(
    s, strategy_id=req.specs[0].strategy_id, inst_id=plan.inst_id, bar=req.bar,
    mode="ai-trade",
    config={...原样不动，保留 groupId 便于向后兼容...},
    status="running",
    group_id=ctx.group_id,                       # 新：填索引列 ix_strategy_runs_group
    group_started_ts=ctx.created_ts,             # 新：组起点（各成员冗余同值）— 见 P1-3
    group_total_budget_usd=ctx.total_budget_usd) # 新：收益率分母（冗余同值）
```

- **P1-3（已核实、已修）**：`ctx.group_id`（`manager.py:312` 生成）/ `ctx.total_budget_usd` / **`ctx.created_ts`**（`manager.py:308` = `int(time.time())`，秒级）三者**均已存在**于 `_GroupBuildCtx`（`manager.py:160-172`）。v1 sketch 误用的 `ctx.started_ts` **不存在**，组级秒级起点字段名为 **`created_ts`**（同名字段亦冗余落在 `AiTradeGroup` 数据类上）——故填 `group_started_ts=ctx.created_ts` 即可，**无需**在 `start_group` 新造字段。§11 该待确认项已解决。
- 效果：新会话的成员 run 全部带同一 `group_id`，`list_ai_trade_groups` 可走与 live-auto 完全一致的 `group by group_id / order by max(group_started_ts)` 索引聚合。
- **向后兼容**：本改动前的旧 ai-trade run（`group_id` 为 NULL）**不进历史列表**（`WHERE group_id IS NOT NULL`）；AI-trade 是极新功能、此类行仅为早期联调残留，作为**已知限制**记录（§9），不伪造分组。

### 3.3 后端改动 2：`_finalize_run` 加法富化 `metrics_json`（`ai_auto/session.py`）

在既有 metrics dict 上**加法**追加 3 个可选字段（不改既有键，不改 `finalize_run` 签名，不动 DB schema）：

```python
metrics = {**_empty_metrics(),
           "totalReturn": (realized / budget) if budget > 0 else 0.0,
           "tradeCount": len(self.executor.trades),
           "avgPnl": realized / max(1, len(self.executor.trades)),
           "roundsCompleted": self.rounds_completed,
           "finalSummary": self.decider.summary,
           "flattenFailed": self.flatten_failed,
           "degradedSeen": self._degraded_seen,
           # ↓ 新增（加法，供历史详情富展示；缺失即降级）
           "equityDigest": _downsample_equity(self._equity, cap=500),   # [{ts,equity,drawdown}]
           "decisionsDigest": _digest_decisions(self._decisions, cap=50),
           "aiStats": _compute_ai_stats(self._decisions, self.rounds_completed)}
```

- `_downsample_equity(deque, cap)`：≤cap 原样，>cap 等距抽样（保留首尾），纯函数、≤12 行。
- `_digest_decisions(deque, cap)`：取最近 ≤cap 条 `{ts, round, action, executed, confidence, reason(截断)}`，剥离大字段（signalBoard / analysis 全文）。
- `_compute_ai_stats(decisions, rounds)`：镜像 backtest `_aggregate_stats` 的口径子集——`{decisionRounds, actionCounts{open_long,open_short,close,hold}, holdRate, avgConfidence, degradedRounds}`。
  - **P2-5（接地）**：`_decisions` 条目实测字段为 `{ts, round, action, sizeFraction, confidence, reason, signalBoard, executed, marketContext, consensus, degradeReason, analysis, marketDigest}`（`session.py:586-591`）。故 `actionCounts` 的键须匹配 `action` 的**实际枚举值**（open_long / open_short / close / hold），`avgConfidence` 取 `confidence` 均值（`None` 跳过），`degradedRounds` = **`degradeReason` 非空**的条数（`_decisions` 无布尔 `degraded` 字段）。
- **P1-1（已核实、已修）**：三个 digest helper 均为**纯函数、可单测、绝不抛**（空输入回空/零）。但核查发现 `_finalize_run` 的调用点 **并未** 被 `try/except` 包裹——`stop()`（`session.py:190-210`）的 `try/except` **只覆盖 flatten**（:202-206），`await self._finalize_run(ts)`（:208）在其**之外、无兜底**，`_finalize_run`（:679-693）内部亦无 try。因此富化段一旦抛错，会中断 `stop()` 收尾（跳过 :209 的 status push，并使 run 卡在 `running` 直到下次启动对账）。**强制要求**：在 `_finalize_run` 内**把三段 digest 构造包进就地 `try/except`**——构造成功则并入 metrics dict，任一异常则记 `logger.warning` 并**退回不含富化字段的基础 metrics dict**，无论如何都照常调用 `repo.finalize_run(...)`。即富化对 finalize 是**加法且可失败**的，绝不阻断真实资金平仓后的落库收尾。

### 3.4 后端改动 3：只读聚合读层 `db/ai_auto_repositories.py`（新）

**逐字镜像** `live_auto_repositories.py` 的结构与口径，仅换判据常量与「策略名」取法。核心函数：

```python
_AI_TRADE_MODE = "ai-trade"
_TERMINAL_STATUSES = {"stopped", "done", "error"}

# 与 live_auto_repositories 同款（数学一致、绝不跨 instId）：
def _pair_round_trips(trades) -> list[tuple[Trade, Trade]]: ...      # (ts,id) 排序、单持仓 open→close
def _summarize_pairs(pairs, total_budget) -> dict: ...              # realized/returnPct/tradeCount/winRate/best/worst
def _load_config(raw) / _num(value) / _distinct_inst_ids(runs): ...  # 容错工具
async def _load_trades_by_run(session, run_ids) -> dict[int, list[Trade]]: ...

# AI-trade 特化：策略名取 config.strategyNames（列表）join，而非 live-auto 的 config.strategyName（单值）
def _group_strategy_name(runs) -> str:
    for run in runs:
        names = _load_config(run.config_json).get("strategyNames")
        if isinstance(names, list) and names:
            return " / ".join(str(n) for n in names if n)
    return ""

async def list_ai_trade_groups(session, *, limit=20, offset=0, exclude_group_id=None) -> dict:
    # WHERE mode=="ai-trade" AND group_id IS NOT NULL [AND group_id != exclude]
    # 去重分组分页 (order by max(group_started_ts) DESC) → 成员 runs → 成交 → 逐成员配对聚合
    # return {"items":[{groupId, strategyName, bar, instIds, startedTs, endedTs, status,
    #                   totalBudgetUsd, realizedPnlUsd, returnPct, tradeCount, winRate, bestUsd, worstUsd}],
    #         "total", "limit", "offset"}

async def get_ai_trade_group_detail(session, group_id) -> dict | None:
    # runs(group_id) 升序 → 逐成员 {runId,instId,leverage,budgetUsd,allocationPct,strategyNames,status,
    #   metrics(=解析 metrics_json，含 equityDigest/decisionsDigest/aiStats/finalSummary), trades(ORM Trade)}
    # + 会话头(strategyName,bar,startedTs,endedTs,status,totalBudgetUsd) + 后端聚合 summary；无 → None

async def reconcile_orphan_ai_trade_runs(session) -> int:
    # mode=="ai-trade" AND status=="running" → 置 stopped + 回填 end_ts + metrics.reconciled=True；幂等
    # P2-3：AI-trade 无持久化 equity 表（equity 仅 finalize 时一次性 digest 进 metrics_json），崩溃 run 连 metrics 都没有，
    #        故 end_ts = max(最后成交 ts, group_started_ts, start_ts)（去掉 live-auto 版里的「最后权益」项）。
```

- **detail 的 members 比 live-auto 多带 `allocationPct / strategyNames / status / metrics`**：`allocationPct` 从 `config_json.allocationPct` 取；`metrics` 让详情页画权益图 + AI 概况。live-auto 版 members 只有 `runId/instId/leverage/budgetUsd/trades`——AI-trade 特化扩这几个字段，仍以 ORM `Trade` 出 `trades`（API 层 `trade_out` 序列化）。
- `_resolve_total_budget`：优先 `group_total_budget_usd`（本方案已填），回退 `Σ config.budgetUsd`——与 live-auto 同。
- 在 `db/repositories.py` 末尾**新增 re-export 块**（仿 `:1002-1008`）：`from .ai_auto_repositories import (list_ai_trade_groups, get_ai_trade_group_detail, reconcile_orphan_ai_trade_runs)`，调用点用 `repo.X`。

### 3.5 后端改动 4：孤儿对账接线（`db/session.py`）

仿 `_reconcile_orphan_live_auto_runs`（`:167-182`）新增私有包装 `_reconcile_orphan_ai_trade_runs()`（开 session → `repo.reconcile_orphan_ai_trade_runs(session)` → 记 count 日志），并在 `init_db` 内 `:80` 的 live-auto 对账**之后**追加一行 `await _reconcile_orphan_ai_trade_runs()`。**不新增列 / 不动 `create_all` / 不动 4 个 `_ensure_*`**。

### 3.6 后端改动 5：Pydantic 契约（`schemas.py`）

在 `AutoTradeHistory*`（`:697-765`）之后**加法**新增镜像系列（camelCase）：

- `AiTradeHistoryEntry`：`{groupId, strategyName, bar, instIds:list[str], startedTs, endedTs, status, totalBudgetUsd, realizedPnlUsd, returnPct, tradeCount, winRate, bestUsd, worstUsd}`（同 `AutoTradeHistoryEntry`）。
- `AiTradeHistoryListOut`：`{items:list[AiTradeHistoryEntry], total, limit, offset}`。
- `AiTradeHistorySummary`：`{realizedPnlUsd, returnPct, tradeCount, winRate, bestUsd, worstUsd}`。
- `AiTradeHistoryMember`：`{runId, instId, leverage, budgetUsd, allocationPct, strategyNames:list[str], status, metrics:dict, trades:list[dict]}`（`trades` 透传 `trade_out` 全字段；`metrics` 用 `dict` 透传，含可选 `equityDigest/decisionsDigest/aiStats/finalSummary`）。
- `AiTradeHistoryDetailOut`：`{groupId, strategyName, bar, startedTs, endedTs, status, totalBudgetUsd, members:list[AiTradeHistoryMember], summary:AiTradeHistorySummary}`。

**复用** `trade_out`（`:781-790`），不新增序列化器。

### 3.7 后端改动 6：REST 端点（`api/ai_auto.py`）

镜像 `api/live_auto.py:174-197`，**只读、不守门**（历史查看不涉及下单，无需 `AI_TRADE_ENABLED`/`LIVE_TRADING_ENABLED`）：

```python
@router.get("/ai-trade/history", response_model=AiTradeHistoryListOut)
async def ai_trade_history(limit: int = Query(20, ge=1, le=100),
                           offset: int = Query(0, ge=0),
                           session: AsyncSession = Depends(get_session)) -> dict:
    active = get_ai_trade_manager().group_status()   # 排除当前运行组（在「当前」视图展示）
    exclude = active["groupId"] if active else None
    return await repo.list_ai_trade_groups(session, limit=limit, offset=offset, exclude_group_id=exclude)

@router.get("/ai-trade/history/{group_id}", response_model=AiTradeHistoryDetailOut)
async def ai_trade_history_detail(group_id: str,
                                  session: AsyncSession = Depends(get_session)) -> dict:
    detail = await repo.get_ai_trade_group_detail(session, group_id)
    if detail is None:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "历史会话不存在"})
    for member in detail["members"]:
        member["trades"] = [trade_out(t) for t in member["trades"]]
    return detail
```

- `get_ai_trade_manager()` 已在 `api/ai_auto.py` 使用；`group_status()` 回活跃组 dict 或 None（Agent 审计确认）。

### 3.8 前端改动 1：数据层（`lib/aiTradeHistoryTypes.ts` / `lib/aiTradeHistoryApi.ts`，新）

```ts
// aiTradeHistoryTypes.ts —— 与后端 schemas 逐字对齐
export interface AiTradeHistoryEntry {
  groupId: string; strategyName: string; bar: string; instIds: string[];
  startedTs: number; endedTs: number; status: string; totalBudgetUsd: number;
  realizedPnlUsd: number; returnPct: number; tradeCount: number;
  winRate: number; bestUsd: number; worstUsd: number;
}
export interface AiTradeHistoryListOut { items: AiTradeHistoryEntry[]; total: number; limit: number; offset: number; }
export interface AiTradeHistorySummary { realizedPnlUsd: number; returnPct: number; tradeCount: number; winRate: number; bestUsd: number; worstUsd: number; }
export interface AiTradeHistoryMember {
  runId: number; instId: string; leverage: number; budgetUsd: number; allocationPct: number;
  strategyNames: string[]; status: string; metrics: AiTradeRunMetrics; trades: Trade[];
}
export interface AiTradeRunMetrics {   // metrics_json 解析（全可选，缺失即降级）
  totalReturn?: number; tradeCount?: number; winRate?: number; avgPnl?: number;
  finalSummary?: string | null; roundsCompleted?: number; reconciled?: boolean;
  equityDigest?: { ts: number; equity: number; drawdown: number }[];
  decisionsDigest?: { ts: number; round: number; action: string; executed?: boolean; confidence?: number | null; reason?: string }[];
  aiStats?: { decisionRounds: number; actionCounts: Record<string, number>; holdRate: number; avgConfidence: number; degradedRounds: number };
}
export interface AiTradeHistoryDetailOut {
  groupId: string; strategyName: string; bar: string; startedTs: number; endedTs: number;
  status: string; totalBudgetUsd: number; members: AiTradeHistoryMember[]; summary: AiTradeHistorySummary;
}

// aiTradeHistoryApi.ts —— P1-2（已修）：仓库无 apiGet/apiFetch 导出（lib/api.ts 的 request/rawRequest
// 为模块私有，仅导出 api 对象与 ApiRequestError）。故**镜像 aiTradeApi.ts 的自持范式**：本地私有
// request<T>（BASE="/api/py"、cache:"no-store"），复用 ./api 的 ApiRequestError；不引用任何不存在的 helper。
import { ApiRequestError } from "./api";
import type { AiTradeHistoryListOut, AiTradeHistoryDetailOut } from "./aiTradeHistoryTypes";

const BASE = "/api/py";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { cache: "no-store" });
  if (!res.ok) {
    let code = String(res.status);
    let message = res.statusText;
    try {
      const body = await res.json();
      if (body?.error) { code = body.error.code ?? code; message = body.error.message ?? message; }
    } catch { /* 非 JSON 错误体：保留状态码文案 */ }
    throw new ApiRequestError(code, message, res.status);
  }
  return (await res.json()) as T;
}

export const aiTradeHistoryApi = {
  list: (limit = 20, offset = 0) =>
    get<AiTradeHistoryListOut>(`/ai-trade/history?limit=${limit}&offset=${offset}`),
  detail: (groupId: string) =>
    get<AiTradeHistoryDetailOut>(`/ai-trade/history/${encodeURIComponent(groupId)}`),
};
```

### 3.9 前端改动 2：入口按钮（`components/Agent/AiTradeStatsButton.tsx` 新 + `AgentPanel.tsx` 改）

**自封装**（仿 `AiTradeToggle` 自持 state + 自挂 modal，避免污染 `AgentPanel` 状态）：

```tsx
export function AiTradeStatsButton() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button variant="ghost" size="sm" onClick={() => setOpen(true)}
              aria-label="AI 交易统计" title="查看 AI 自动交易的统计与历史回溯">
        {/* 迷你条形图 icon（复用 AutoTradePanel 触发按钮的 SVG 风格）+ 文案「统计」 */}
        统计
      </Button>
      <AiTradeStatsPanel open={open} onClose={() => setOpen(false)} />
    </>
  );
}
```

`AgentPanel.tsx` 仅：`import { AiTradeStatsButton } from "./AiTradeStatsButton";` + 在 `:329 <AiTradeToggle />` **前**插一行 `<AiTradeStatsButton />`。

### 3.10 前端改动 3：看板 `AiTradeStatsPanel.tsx`（新）+ 子组件

`Modal` 壳（`labelledBy="ai-trade-stats-title"`，`className="max-w-3xl"`；`Modal` 实测 props = `{open,onClose,labelledBy?,className?}` 且已渲染 `role="dialog"`+`aria-modal`+`aria-labelledby`），头部标题「AI 自动交易统计」+ 关闭。**P2-6**：`Tabs` 是**数据驱动**组件（props = `{tabs:{key,label}[], active, onChange, variant?}`，非 children），故面板本地持 `active` state，用 `Tabs variant="underline"` 渲染「当前 / 历史」两个 key，并**按 `active` 条件渲染**对应页体；详情为「历史」内下钻（局部 `selectedGroupId` state，返回按钮回列表）。

1. **`aiTradeStats/AiTradeStatsLive.tsx`（当前）**——`const group = useAppStore(s => s.aiTradeGroup)`：
   - `null` → 空态引导「尚未启动 AI 自动交易；启动后这里按时间维度汇总每次完整交易的收益与总收益率，停止后可在『历史』标签回溯」。
   - 有组：`members = Object.values(group.members).map(m => ({instId: m.instId, trades: m.trades, leverage: group.leverage}))`（注：`group.members` 是 `Record<string, ...>`，须 `Object.values`）；顶部时间 segmented control（复用 `autoTradePnl.resolveRangeBounds`）→ `buildLedger`→`filterRoundTrips`→`summarizeRoundTrips(visible, group.totalBudgetUsd)`；KPI 卡（总收益 / 收益率 / 笔数 / 胜率 / 最佳 / 最差，色走 `colorForReturn`）+ **可选**「当前持仓浮盈」条（见 P1-4 降级规则）+ 倒序 `AutoTradeRoundTripRow` 列表 + 常驻诚实条「运行期态，停止后转入『历史』持久保存」。
   - **P1-4（已修）持仓浮盈条的诚实降级**：`AiTradeGroupMemberState.position` 实测类型为 **`unknown | null`**（`aiTradeTypes.ts:338`，全仓同），**无** typed `unrealizedPnl` 字段——直接写 `member.position?.unrealizedPnl` 既过不了 `tsc`（不能索引 `unknown`）又可能取到不存在的字段（违反「绝不伪造」）。改为：先定义窄化守卫读取真实数值，`function readUpl(p: unknown): number | null { return p && typeof p === "object" && typeof (p as {unrealizedPnl?: unknown}).unrealizedPnl === "number" ? (p as {unrealizedPnl: number}).unrealizedPnl : null; }`；**仅当每个持仓成员都能取到数值时**才渲染该条（`Σ readUpl(member.position)`，**与窗口解耦**、标注「未实现，全局」），任一成员取不到即**整条略去**（不显示占位 0，不猜测）。**仍禁用** `sessionPnlUsd − Σ realized` 的减法式（§6.3）。⚠️ 开发期须先确认 WS `position` 载荷里承载未实现盈亏的**真实字段名**（可能是 `unrealizedPnl` / `upl` / 其它），据实调整守卫读取的键；若确无此字段，则**本期不做浮盈条**（§11 待确认项）。
2. **`aiTradeStats/AiTradeStatsHistoryList.tsx`（历史）**——`useQuery(["ai-trade-history", offset], () => aiTradeHistoryApi.list(20, offset))`：
   - 加载态 `Skeleton` 卡；错误态 `role="alert"`；空态「暂无历史 AI 自动交易记录」。
   - 每 `item` 一张卡：起止时间（`fmtTime`）+ `instIds`（≤3 显示 + 「+N」）+ `strategyName` + `status` Badge（stopped/error/reconciled 幽灵已修）+ 右侧 **realizedPnlUsd（`signed`，`colorForReturn`）/ returnPct（`fmtPct`）** + 笔数 / 胜率 小字；整卡 `button`、点击 → `onSelect(groupId)`。
   - 底部分页（上一页 / 下一页，依 `total`）。
3. **`aiTradeStats/AiTradeStatsDetail.tsx`（详情）**——`useQuery(["ai-trade-history-detail", groupId], () => aiTradeHistoryApi.detail(groupId))`：
   - 会话头（groupId 略 / 时间 / bar / status / totalBudgetUsd）+ 返回按钮；**P2-2**：若**任一** `member.metrics.reconciled === true`，头部加「已对账」chip（对账后 `run.status` 仍是 `"stopped"`，故不能靠 `status` 判定，须读 `metrics.reconciled`）。
   - **KPI 概览**（**P2-1**：**自绘 KPI 网格**，不复用 `KPIHero`——后者要求完整 `Metrics{sharpe/maxDrawdown/profitFactor/…}`，而历史 `summary` 只有下列六项）：总收益（`summary.realizedPnlUsd`）/ 收益率（`summary.returnPct`）/ 胜率 / 交易笔数 / 最佳 / 最差，色走 `colorForReturn`。（sharpe/maxDrawdown 等扩展指标仅在合并权益曲线可得时由其派生，见下条；不可得则不展示，不填 0 冒充。）
   - **权益 / 回撤图（P1-5：合并口径已明确）**：取各成员 `metrics.equityDigest`（`[{ts,equity,drawdown}]`，正常停止会话才有）——**绝不朴素拼接、绝不相加各成员 drawdown**。合并算法：① 收集所有成员所有 `ts` 的并集并升序去重；② 对每个 `ts`，各成员取其「≤ts 的最后一个 equity」（前值填充；成员首个样本前记 0 或其初始预算，取全期一致口径）；③ 组合权益 = Σ 各成员填充后 equity；④ 组合回撤由 `backtestStats.underwaterSeries` **在组合权益曲线上重算**（不用成员 drawdown）。以此喂 `UnderwaterChart`/`PeriodReturnBars`（其 `EquityPoint` 需 `{ts,equity}`；实现时核对字段名与 `equityDigest` 对齐）。单成员组即退化为该成员自身曲线。**全部成员均缺 `equityDigest`**（崩溃 / 对账会话）→ 显示「本次会话未落权益快照，仅按成交汇总」提示，**不画图**（诚实降级）。
   - **成员明细 + 完整交易往返**：`members.map(m => ({instId: m.instId, trades: m.trades, leverage: m.leverage}))` → `buildLedger` → 按成员分组的 `AutoTradeRoundTripRow` 列表（`pnlPct` 由 `derivePct` 用 `m.leverage` 派生；`trades` 为 `trade_out` 出的 wire dict，缺 `pnlPct`/`seeded` 的安全性见 P2-4）。
   - **AI 决策概况**（`metrics.aiStats` 存在时）：`AiStatsCard` 版式 6 格（决策轮数 / 观望占比 / 平均置信度 / 共识降级 …）+ `finalSummary` 折叠；缺失则略。

---

## 4. 文件 / 模块变更计划（File / Module Change Plan）

| 文件 | 动作 | 一句话意图 |
|---|---|---|
| `services/quant/app/ai_auto/manager.py` | 改 | `_create_group_run` 向 `create_run` 传 `group_id/group_started_ts/group_total_budget_usd`（填既有 NULL 列，零迁移）|
| `services/quant/app/ai_auto/session.py` | 改 | `_finalize_run` 加法富化 `metrics_json`：`equityDigest`（下采样）/`decisionsDigest`/`aiStats`；新增 3 纯函数 helper |
| `services/quant/app/db/ai_auto_repositories.py` | 新增 | 镜像 `live_auto_repositories`：`list_ai_trade_groups`/`get_ai_trade_group_detail`/`reconcile_orphan_ai_trade_runs`（口径与前端 `autoTradePnl` 一致）|
| `services/quant/app/db/repositories.py` | 改 | 末尾 re-export 上述 3 个符号（仿 live_auto re-export 块）|
| `services/quant/app/db/session.py` | 改 | `init_db` 追加 `_reconcile_orphan_ai_trade_runs()` 包装 + 调用（修 ai-trade 幽灵进行中）|
| `services/quant/app/schemas.py` | 改 | 加法新增 `AiTradeHistoryEntry/ListOut/Summary/Member/DetailOut`（镜像 `AutoTradeHistory*`，复用 `trade_out`）|
| `services/quant/app/api/ai_auto.py` | 改 | 加法新增 `GET /ai-trade/history`、`GET /ai-trade/history/{group_id}`（只读、排除活跃组、404）|
| `services/quant/tests/test_ai_auto_history.py` | 新增 | 后端：run 分组列已填、列表 / 详情聚合口径、孤儿对账、finalize 富化字段、空 / 分页 / 404 |
| `apps/web/lib/aiTradeHistoryTypes.ts` | 新增 | 历史 wire 类型（与 schemas 逐字对齐）|
| `apps/web/lib/aiTradeHistoryApi.ts` | 新增 | 历史 REST 客户端（`list`/`detail`，复用既有 apiFetch/错误封装）|
| `apps/web/components/Agent/AiTradeStatsButton.tsx` | 新增 | 自封装入口按钮（自持 open state + 挂 `AiTradeStatsPanel`）|
| `apps/web/components/Agent/AiTradeStatsPanel.tsx` | 新增 | 看板 Modal 壳 + Tabs[当前｜历史] + 详情下钻编排 |
| `apps/web/components/Agent/aiTradeStats/AiTradeStatsLive.tsx` | 新增 | 「当前」视图：复用 `autoTradePnl` 聚合 `store.aiTradeGroup` + 时间筛选 + 浮盈条 + 往返行 |
| `apps/web/components/Agent/aiTradeStats/AiTradeStatsHistoryList.tsx` | 新增 | 「历史」列表：`useQuery` 拉取、KPI 卡、状态 Badge、分页、空 / 加载 / 错误态 |
| `apps/web/components/Agent/aiTradeStats/AiTradeStatsDetail.tsx` | 新增 | 单会话详情：KPI 概览 + 权益 / 回撤图（equityDigest 降级）+ 成员往返 + AI 决策概况 |
| `apps/web/components/Agent/AgentPanel.tsx` | 改 | 头部 `AiTradeToggle` 左侧插 `<AiTradeStatsButton />`（+1 import +1 行）|
| `apps/web/lib/aiTradeHistory.test.ts` | 新增 | 前端纯逻辑：类型映射 / 复用 `autoTradePnl` 对 detail members 复算与 backend summary 逐值吻合 |
| `apps/web/components/Agent/AiTradeStatsPanel.test.tsx` | 新增 | 组件：空态、Tabs 切换、列表→详情下钻、equityDigest 缺失降级、当前视图浮盈与窗口解耦 |

> 说明：**零 DB 迁移**（仅填既有列 + 加法写既有 TEXT 列）、**零 store 形状 / 持久化 version 变更**（历史走 TanStack Query，当前读既有 `aiTradeGroup`）、**不新增 WS**、**不动 `SimBroker`/回测 / live-auto 任何文件**。新文件均控制在 CLAUDE.md 阈值内（≤800 行 / 方法 ≤50 行 / 形参 ≤5 / 嵌套 ≤4）；`ai_auto_repositories.py` 与 `AiTradeStatsPanel.tsx` 拆子组件以守文件行数阈值。

---

## 5. 接口设计（Interface Design）

### 5.1 REST（新增两个只读端点，前端经 `/api/py/*` 代理）

| Method | Path | Query | 200 响应 | 错误 |
|---|---|---|---|---|
| GET | `/api/ai-trade/history` | `limit`(1..100,默认 20)、`offset`(≥0,默认 0) | `AiTradeHistoryListOut` = `{items: AiTradeHistoryEntry[], total, limit, offset}`（按 `startedTs` DESC，**排除当前运行组**）| — |
| GET | `/api/ai-trade/history/{group_id}` | — | `AiTradeHistoryDetailOut` = `{会话头, members[], summary}` | `404 {code:"NOT_FOUND"}` 组不存在 |

`AiTradeHistoryEntry`（列表行 KPI）：见 §3.6。`AiTradeHistoryMember`：`{runId, instId, leverage, budgetUsd, allocationPct, strategyNames[], status, metrics{...含可选 equityDigest/decisionsDigest/aiStats/finalSummary}, trades[](trade_out 全字段含 explain)}`。

**口径**：`realizedPnlUsd = Σ 平仓 exit.pnl`（逐成员配对、不跨 instId）；`returnPct = realizedPnlUsd / totalBudgetUsd`（分母 = 原始投入预算，与 `_finalize_run.totalReturn` 同源）；`winRate = 盈利往返数 / 总往返数`（`count===0→0`）；`bestUsd/worstUsd` 无样本取 0。

### 5.2 前端 REST 客户端 `lib/aiTradeHistoryApi.ts`

`aiTradeHistoryApi.list(limit, offset) → Promise<AiTradeHistoryListOut>`；`aiTradeHistoryApi.detail(groupId) → Promise<AiTradeHistoryDetailOut>`。错误抛既有 `ApiRequestError`（复用 `aiTradeApi.ts` 的 fetch/错误范式）。

### 5.3 组件 props 契约

- `AiTradeStatsButton`：`()`（无 props，自封装）。
- `AiTradeStatsPanel`：`{ open: boolean; onClose: () => void }`。
- `AiTradeStatsLive`：`()`（读 store）。
- `AiTradeStatsHistoryList`：`{ onSelect: (groupId: string) => void }`。
- `AiTradeStatsDetail`：`{ groupId: string; onBack: () => void }`。

### 5.4 WS / CLI

无新增。历史为一次性只读拉取，不需要 WS；「当前」复用既有 `/ws/ai-trade/{sessionId}` 已注入 `store.aiTradeGroup` 的数据。

---

## 6. 数据模型（Data Model）

### 6.1 DB（零迁移，仅「填列」+「加法写既有 TEXT 列」）

- `strategy_runs`（`db/models.py:43-72`）：**列不变**。AI-trade run 改为**填**既有 `group_id`(VARCHAR48，索引 `ix_strategy_runs_group`) / `group_started_ts`(INTEGER) / `group_total_budget_usd`(FLOAT)——这三列由 `_ensure_live_auto_group_columns` 已幂等建好，**无需新 `_ensure_*`**。
- `metrics_json`(Text)：`_finalize_run` 加法写 `equityDigest`/`decisionsDigest`/`aiStats`（既有列，无 schema 变更）。
- `trades`(`db/models.py:75-97`)：**完全不变**，历史详情直接读既有行经 `trade_out` 出线。

### 6.2 运行期 / wire 形状

- 后端 repo 中间态：`_pair_round_trips` 出 `list[(Trade, Trade)]`；`_summarize_pairs` 出 `dict`；detail members 出 `dict`（trades 为 ORM `Trade`，API 层 `trade_out` 序列化）。
- 前端类型：`AiTradeHistory*`（§3.8）皆组件 / lib 局部类型，**不入 store**。看板开关 / 选中会话 / 分页 offset / 时间窗口均组件局部 `useState`（瞬态、不持久化）。
- **P2-8（时间戳单位）**：`startedTs`/`endedTs`（源自 `group_started_ts=ctx.created_ts=int(time.time())`）、`trade.ts`、`equityDigest[].ts` **全为秒**（DB 均 `Integer` 秒级）；前端一律以秒喂 `fmtTime`，不得混入毫秒。

### 6.3 口径单一来源（前后端一致）

| 量 | 后端（历史列表卡 / summary，权威） | 前端（详情往返复算，校验一致） |
|---|---|---|
| 已实现总收益 | `Σ exit.pnl`（`_summarize_pairs`）| `Σ RoundTrip.pnlUsd`（`summarizeRoundTrips`）|
| 收益率 | `realized / totalBudgetUsd` | `totalPnlUsd / totalBudgetUsd` |
| 每笔收益率 | 不出（`trades` 无 `pnlPct`）| `derivePct = sign·(exit/entry−1)·leverage`（`leverage` 取 `member.leverage`）|
| 胜率 / 最佳 / 最差 | `_summarize_pairs` | `summarizeRoundTrips`（逐值吻合）|

「当前」视图的**当前持仓浮盈**单列一条：`Σ member.position?.unrealizedPnl ?? 0`，**与时间窗口解耦**，明确区别于「已实现总收益」，禁用 `sessionPnlUsd` 减法式（避免重复计入）。

---

## 7. 测试与验收标准（Testing & Acceptance Criteria）

### 7.1 后端单测（`test_ai_auto_history.py`，真实 SQLite 集成，不 mock DB）

> **P2-7（测试隔离）**：`services/quant` 既有测试**共享同一临时 DB**，新用例须复用既有 conftest fixture 并**自清理**（每用例后清 `strategy_runs`/`trades` 或用独立 group_id 前缀），避免污染其它套件；不新引全局共享状态。

- **分组列已填**：跑一次 `_create_group_run`（或其单元路径）后，成员 run 的 `group_id/group_started_ts/group_total_budget_usd` 非 NULL 且同组同值。
- **列表聚合**：构造两组、各 1~2 成员 + 若干开 / 平成交，`list_ai_trade_groups` 返回按 `startedTs` DESC、`realizedPnlUsd/returnPct/tradeCount/winRate/bestUsd/worstUsd` 与手算逐值吻合；分页 `limit/offset/total` 正确；`exclude_group_id` 生效。
- **详情**：`get_ai_trade_group_detail` 出会话头 + 逐成员（`leverage/budgetUsd/allocationPct/strategyNames/status/metrics/trades`）+ summary；**逐成员配对不跨 instId**（多标的组各自往返正确）；组不存在 → None。
- **finalize 富化**：`_downsample_equity/_digest_decisions/_compute_ai_stats` 纯函数单测（空输入回空 / 零、下采样保留首尾、`holdRate/avgConfidence` 正确、绝不抛）；含富化字段的 `metrics_json` 可被 detail 正常解析。
- **孤儿对账**：置一条 `mode="ai-trade" status="running"` 无 finalize 的 run，跑 `reconcile_orphan_ai_trade_runs` 后置 `stopped`、`end_ts=max(...)`、`metrics.reconciled=True`；**幂等**（二次运行 0 改）；不误伤其它 mode。
- **落库口径向后兼容**：既有 `append_live_trade`/`finalize_run`/`create_run` 行为不变；既有 AI-trade 相关用例零回归。

### 7.2 前端单测

- `aiTradeHistory.test.ts`：`AiTradeHistoryMember.trades` 喂 `buildLedger`/`summarizeRoundTrips` 复算，与该会话 backend `summary` 逐值吻合；`derivePct` 用 `member.leverage`；空 members / 空 trades 安全。**P2-4**：另断言把 `trade_out` 形状的 wire dict（出 `pnl`、**无** `pnlPct`/`seeded`）喂 `buildRoundTrips` 时——消费字段 `side/ts/price/pnl` 全在、缺 `pnlPct` 走 `?? derivePct` 回退、缺 `seeded` 按 false 处理（不误滤）——往返配对与汇总均正确。**P1-5**：新增权益合并纯函数（并集时间轴 + 前值填充求和 + `underwaterSeries` 重算回撤）单测：多成员错峰样本合并后组合权益单调拼接正确、组合回撤 ≠ 成员回撤之和。
- `AiTradeStatsPanel.test.tsx`：未启动且无历史 → 两页空态；Tabs 切换正确；历史列表点击卡片 → 详情下钻、返回回列表；`equityDigest` 缺失 → 详情不画权益图而显降级提示；「当前」视图切时间窗口 → 已实现汇总随之变、**持仓浮盈不变**（与窗口解耦）；**P1-4** 无可用 `unrealizedPnl` 字段时浮盈条整条不渲染（不显示占位 0）；加载态 `Skeleton`、错误态 `role="alert"`。
- **P2-7（测试环境约定）**：组件测试须以 `NODE_ENV=test` 运行（否则默认按 production 触发 React `act()` 警告 / 报错，本仓既有约定）。既有前端用例零回归；`npx tsc --noEmit` 通过；`next build` 成功（`npm run lint` 在 apps/web 不可用，以 `tsc --noEmit` 为类型门）。

### 7.3 验收标准（Definition of Done）

1. 右侧「AI 助手」横栏 `AiTradeToggle` **左侧**出现「统计」按钮，点击打开 AI 自动交易统计看板 Modal。
2. 看板含「当前」「历史」两页：**当前**页对正在运行的分组按时间维度汇总每次完整交易的收益 / 总收益率 + 当前持仓浮盈 + 逐条往返（可展开触发原因）；未运行显示明确空态。
3. **历史**页列出**已持久化**的过往 AI 自动交易会话（跨刷新 / 跨重启可见），每张卡显示起止时间 / 标的 / 策略 / 状态 / 已实现总收益 / 收益率 / 笔数 / 胜率；分页可翻。
4. 点击历史卡进入**单会话详情**：KPI 概览 + 权益 / 回撤图（正常停止会话）+ 逐成员完整交易往返 + AI 决策概况 + 会话总结；崩溃 / 对账会话优雅降级为「仅按成交汇总」。
5. 后端崩溃 / 强杀遗留的 AI-trade run 经启动对账不再「幽灵进行中」，历史状态诚实。
6. 所有数字均由真实落库成交派生；**零 DB 迁移、零 store version bump、无 WS 变更**；前后端汇总口径逐值一致。
7. 全量前后端测试通过、类型检查与构建通过。

---

## 8. 关键时序（Sequence）

```
启动阶段
  init_db: create_all + _ensure_* + _reconcile_orphan_live_auto_runs + [新]_reconcile_orphan_ai_trade_runs
           └ 修复上次崩溃遗留的 ai-trade「幽灵 running」→ 历史状态诚实

运行→停止（数据落库）
  start_group → _create_group_run(每成员 run，[新]带 group_id/started_ts/total_budget) → status=running
  每根确认 K 线 decide → 执行 → _persist_trade(append_live_trade，含 explain)      # 成交实时落库
  stop_group / stop_member → _finalize_run(status=stopped, [新]metrics 富化 equity/decisions/aiStats)

用户查看（读侧）
  点「统计」→ AiTradeStatsPanel
    · 当前页：读 store.aiTradeGroup → buildLedger/summarize（复用 autoTradePnl）→ KPI + 往返 + 浮盈
    · 历史页：GET /ai-trade/history（排除活跃组）→ 卡列表（后端逐成员配对聚合）
        点卡 → GET /ai-trade/history/{groupId} → 详情
           · summary(后端) 与 members[].trades 前端复算 逐值吻合
           · equityDigest 存在→画权益/回撤；缺失→降级提示
```

---

## 9. 范围边界（Scope）

- **范围内**：AI 自动交易的「当前」实时统计（复用瞬态 `aiTradeGroup`）+「历史」跨会话持久列表 / 单会话详情（新只读端点，读既有落库 run/trade）；入口按钮置于 `AiTradeToggle` 左侧；启动期 ai-trade 孤儿对账；`_finalize_run` 加法富化 metrics 以支撑详情权益图 / AI 概况。
- **已知限制（须对用户诚实）**：
  - **本改动前的旧 ai-trade run**（`group_id` 为 NULL）不进历史列表（`WHERE group_id IS NOT NULL`）。AI-trade 为极新功能、此类行仅早期联调残留，不伪造分组（§3.2）。
  - **崩溃 / 对账会话**缺 `equityDigest/decisionsDigest/aiStats`（未走 finalize），详情**不画权益图**、只按成交汇总 KPI + 往返（诚实降级），并显式提示。
  - 每笔收益率 `pnlPct` 由 `derivePct`（价格变动% × 杠杆）派生（`trades` 不存 `pnlPct`），忽略手续费、误差有界，与运行期口径一致。
- **范围外（明确不做，留作后续）**：
  - AI 决策 / CoT 执行轨迹的**逐轮全量**落库与历史回放（仅存 `decisionsDigest` 摘要 + `finalSummary`；全量轨迹需新表 + 热路径写库，成本 / 风险不划算）。
  - 逐 tick 实时落权益 `equity_points`（仅 finalize 时一次性存下采样 digest；避免真实资金热路径的高频写库）。
  - 跨多组的组合 / 汇总统计（如「全部 AI 交易累计盈亏」总账），本期以「按会话」粒度为主。
  - 与 `auto-trading-earnings-persistence` 抽取公共聚合 helper 的重构（待其提交稳定后再议，见 R-6）。
  - 不动 `SimBroker`/回测 / live-auto 任何文件、不复活单会话死件、不新增 WS / store 持久化字段。

---

## 10. 风险与缓解（Risks & Mitigations）

| 编号 | 风险 | 说明 | 缓解 |
|---|---|---|---|
| R-1 | 依赖在制模块 | `live_auto_repositories.py` 未提交、可能变动 | **不 import** 它，新建自包含 `ai_auto_repositories.py` 镜像（§2.2 D-0）；配对 / 汇总数学与前端 `autoTradePnl` 对齐、各自可测 |
| R-2 | 跨标的错误配对 | 多成员组若跨 instId 配对会算错盈亏 | `_pair_round_trips` **逐成员 run 分桶**后调用、绝不跨 instId（镜像 live-auto P0-3）；测试覆盖多标的组 |
| R-3 | 幽灵进行中 | 崩溃 ai-trade run 卡 `running` 污染历史 | 新增 `reconcile_orphan_ai_trade_runs` 并接入 `init_db`；幂等、回填 `end_ts`；测试覆盖 |
| R-4 | finalize 富化抛错阻断停止 | 富化 helper 异常波及真实资金平仓收尾 | **P1-1 核实：调用点 `session.py:208` 确无 try 包裹**，故**强制**在 `_finalize_run` 内把三段 digest 构造包进就地 `try/except`——异常仅 `logger.warning` + 退回基础 metrics dict，照常 `finalize_run`，绝不阻断（保持既有 best-effort 落库风格）。三 helper 亦纯函数 / 绝不抛（空回空）|
| R-5 | 前后端口径漂移 | 后端 summary 与前端复算不一致会误导 | 数学逐字对齐 `autoTradePnl.summarizeRoundTrips`；`aiTradeHistory.test.ts` 断言 detail 复算 == backend summary；`returnPct` 分母统一 `totalBudgetUsd` |
| R-6 | 代码重复 | 配对 / 汇总在 ai_auto 与 live_auto 两处重复 | 有意为之以解耦在制模块；显式记录，待 earnings-persistence 稳定后抽公共 helper（范围外）|
| R-7 | 旧库无 group_id 行 | 早期 ai-trade run 不显示 | 已知限制、显式记录（§9）；不伪造分组，不回填历史（可作后续增量）|
| R-8 | 权益/决策 digest 体积 | 长会话 metrics_json 膨胀 | `equityDigest` 下采样 ≤500 点、`decisionsDigest` ≤50 条、`reason` 截断；一次性写、不入热路径 |
| R-9 | 只读端点越权 / 泄密 | 历史端点是否需守门 / 是否含密钥 | 只读、不下单，故不守 `AI/LIVE_ENABLED`（同 live-auto history）；`trade_out`/`metrics` 不含任何 OKX 密钥（既有落库本就无密钥）|
| R-10 | 看板信息过载 | KPI + 图 + 往返 + AI 概况可能拥挤 | 视觉层级（主指标大字置左）、Tabs 分「当前 / 历史」、详情分区、响应式 grid、渐进披露（可展开行 / 折叠总结），遵循 HCI 最佳实践 |

---

## 11. 实施过程发现的方案缺陷（Implementation Deviations）

> 预留给 Subtask #2（代码开发）。如实现中发现方案缺陷或需偏离，在此记录原因与回退条件。

**已由 Subtask #1 评审核实解决（源码已确认，开发期无需再查）**：
- ✅ `_GroupBuildCtx` 组级秒级起点字段 = **`created_ts`**（`manager.py:308/172`；v1 的 `started_ts` 不存在），`_create_group_run` 填 `group_started_ts=ctx.created_ts`（P1-3 / §3.2）。
- ✅ `get_ai_trade_manager().group_status()` 空时返回 **`None`**、活跃组 dict 首键为 **`"groupId"`**（`manager.py:409/414`），历史端点 `exclude=active["groupId"] if active else None` 成立（§3.7）。
- ✅ `_finalize_run` 调用点（`session.py:208`）**确无 try 包裹**——富化段的兜底必须落在 `_finalize_run` 内的就地 `try/except`（P1-1 / §3.3 / R-4）。

**仍待开发节点核对（评审无法从静态源码定论）**：
- ⚠️ **WS `position` 载荷承载「未实现盈亏」的真实字段名**（`store.aiTradeGroup` 成员 `position` 类型为 `unknown | null`，无 typed `unrealizedPnl`）：据实调整 P1-4 守卫读取的键；若确无此字段，本期**不做**「当前」视图浮盈条（§3.10.1 / §6.3）。
- `UnderwaterChart`/`PeriodReturnBars` 的 `EquityPoint` 实际字段名与 `equityDigest`（`{ts,equity,drawdown}`）对齐核对（§3.10.3 / P1-5）。
- `AiTradeToggle` 的 icon / 尺寸细节，确保「统计」按钮与其在 `gap-2` 下视觉对齐（§3.9）。

**Subtask #2（代码开发）实施结论——3 个待核对项均已就地核实、无需偏离方案**：
- ✅ **WS `position` 未实现盈亏字段名 = `unrealizedPnl`（number）**：核实 `live_auto/executor.py::position_payload`（`:107-112`）返回 `{side,size,avgEntry,markPrice,unrealizedPnl}`，AI-trade 复用同一 `LiveExecutor`。故 P1-4 浮盈条**已实现**（窄化守卫 `readUnrealizedPnl` 读 `unrealizedPnl`；flat/null→0，有持仓但读不到数值→整条略去，绝不占位 0）。放行条件 1 满足。
- ✅ **`UnderwaterChart`/`PeriodReturnBars` 均接 `equity: EquityPoint[]`（`{ts,equity,drawdown}`）**，与 `equityDigest` 字段名逐字对齐（`charts.tsx:16/38`）。P1-5 合并纯函数 `mergeMemberEquity`（导出于 `AiTradeStatsDetail.tsx`）按「并集时间轴 + 前值填充求和（首样本前用成员预算基线）+ 求和曲线上重算 drawdown」实现，绝不拼接 / 绝不相加成员 drawdown；`aiTradeHistory.test.ts` 断言组合回撤（-0.0909）≠ 成员回撤之和（-0.20）。放行条件 3 满足。
- ✅ **入口按钮**：`AiTradeStatsButton` 用 `Button variant="ghost" size="sm"` + 迷你条形图 SVG + 文案「统计」，插在 `AgentPanel` 头部 `<AiTradeToggle />` 左侧（继承父 `gap-2`），视觉对齐。
- ✅ **放行条件 2（finalize 富化就地兜底）**：`_finalize_run` 内三段 digest 构造包在 `try/except`，异常 `logger.warning` + `metrics.pop` 退回基础 dict 并照常 `finalize_run`（`session.py`）。
- ✅ **放行条件 4（口径逐值一致门禁）**：`aiTradeHistory.test.ts` 断言「前端 `buildLedger/summarizeRoundTrips` 复算 detail == backend summary」逐值吻合，并覆盖 P2-4（`trade_out`→`buildRoundTrips` 缺 `pnlPct`/`seeded` 安全）。
- **验证**：后端 `test_ai_auto_history.py` 10/10 + 邻近 ai-trade/live-auto 回归 71/71 通过；前端新增 11 用例全绿 + 复用组件（`AutoTradePnlPanel`/`AiTradeToggle`）零回归；`tsc --noEmit` 我方文件 0 错（仅既有 `WeightBatchArena.test.tsx` 预存错，非本次回归）。**方案 v2 无缺陷、无需偏离，已完整落地。**

---

## 12. 评审说明（Review — 详见文首《评审记录》）

> Subtask #1（方案评审与修复）已完成：以四个并行只读核查通道逐文件核对了 v1 的**全部代码断言**（行号 / 函数签名 / 数据结构 / 落库路径），问题清单（5 P1 + 8 P2，无 P0）与就地修复均见**文首《评审记录》**，全部 P1/P2 已在文档正文对应小节修复或明确接地，版本升至 **v2**。评审结论见下节。

---

## 评审结论（Review Verdict）

**结论：有条件通过（Approved with Conditions）**

### 评审判断
v1 方案的**核心工程判断经全量源码核实成立**：AI-trade 的 run/trade 确已持久化、`create_run` 确已支持三个 group_* kwargs、三列 + `ix_strategy_runs_group` 索引确由 `_ensure_live_auto_group_columns` 幂等建好、`live_auto_repositories` 五函数 / `schemas.py:697-790` / `api/live_auto.py:174-197` 确为可逐字镜像的蓝本、前端 `autoTradePnl`/`AutoTradeRoundTripRow`/`aiBacktest`/`Modal`/`Tabs`/设计 token/TanStack Query 确均存在且签名吻合。因此「**零 DB 迁移、镜像 live-auto 持久化模式、复用既有纯函数与设计语言**」的整体路线**可行、右尺寸（未过度设计、未欠规格）、与 CLAUDE.md 及既有约定一致**，可进入开发。

发现的 5 个 P1 中，3 个是 v1 代码 sketch 与真实符号对不上（`apiGet` 不存在、`ctx.started_ts` 不存在、`position.unrealizedPnl` 不可索引 `unknown`），会在开发期即报错；2 个是正确性缺口（finalize 富化无兜底、多成员权益合并口径缺失）。**全部 5 个 P1 与 8 个 P2 均已在本 v2 文档正文就地修复 / 接地**，文档层面**无遗留 P0/P1**。

### 放行条件（开发节点必须落实，见 §11「仍待核对」）
1. **WS `position` 未实现盈亏字段名**：开发期先确认 `store.aiTradeGroup` 成员 `position` 载荷里承载浮盈的真实数值字段名，据实实现 P1-4 的窄化守卫；**若确无该字段，本期不做「当前」视图浮盈条**（不得以 0 或减法式冒充）。
2. **`_finalize_run` 富化就地兜底（P1-1）**：三段 digest 构造必须包在 `_finalize_run` 内的 `try/except`，异常退回基础 metrics dict 并照常 `finalize_run`，严禁阻断真实资金平仓后的落库收尾。
3. **多成员权益合并（P1-5）**：详情页权益 / 回撤图须按「并集时间轴 + 前值填充求和 + `underwaterSeries` 重算回撤」实现，禁止朴素拼接或相加各成员 drawdown；`EquityPoint` 字段名与 `equityDigest` 对齐后再喂图。
4. **口径逐值一致性门禁**：`aiTradeHistory.test.ts` 必须断言「前端复算 detail == backend summary」逐值吻合，并覆盖 P2-4 的 `trade_out`→`buildRoundTrips` 缺字段安全性。

满足以上 4 条即视为完全落地本方案；其余 P2 已在正文澄清，属实现须知而非阻塞项。
