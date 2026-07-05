# AI 自动交易 · 回测模式（AI Auto-Trading · Backtest Mode）开发方案

- **Feature key**: `ai-auto-trading-backtest`
- **文档版本**: **v2**（iteration 2 · 方案设计 → 评审节点复核并加固；含 `## 评审记录` 与 `## 评审结论`。评审只改本 `spec.md`，不写实现代码、不改任何源文件、不 `git commit`）。
- **作者**: 方案设计节点（solution architect）
- **上游基线（均已实现并 commit）**:
  - **AI 自动交易（第三种自动交易类型）** — `docs/plans/ai-auto-trading/spec.md` v2，commit `4b13b69`。后端 `services/quant/app/ai_auto/`（`signals`/`decider`/`prompts`/`session`/`market_window`/`market_context`/`manager`/`admission`/`__init__`）+ `api/ai_auto.py`；前端 `apps/web/lib/aiTrade*`/`useAiTradeStream` + `components/Agent/AiTradeToggle|AiTradeLaunchModal` + `components/Chart/AiTradePanel` + store（v25）。
  - **综合研判增强包** — `docs/plans/ai-auto-trading-enhancements/spec.md` v4，commit `953cd5d`。E1 市场体制上下文（`market_context.build_market_context`）、E2 策略共识度 + 门禁（`signals.compute_consensus` + `session._apply_consensus_gate`）、E3 决策可观测性、E4 会话级单仓 OCO 可配。
- **本轮需求（原文）**: 「AI 自动交易，对应的弹窗面板中，可以选择『回测模式』。如果选择回测模式，就启动对应的回测，但是是 AI 自动交易这种形式的回测，AI 要在回测数据上去跑，并且显示对应的执行情况，完成后也要显示各种回测指标结果。美观优雅，顶级，符合人机交互最佳实践。」
- **安全定位（与 v2/增强包的根本区别，一等约束）**: **回测模式不下任何真单、不动任何真实资金**。它把 AI 决策脑（`AiTradeDecider` + 信号板 + 体制/共识上下文）**逐字复用**，但把执行落地从「实盘 `LiveExecutor` → `okx/trading.py` 真单」**整体替换**为「模拟撮合 `engine/simulator.SimBroker`（回测/纸面同款、已测、确定性）」，数据源从「OKX 公共 WS 实时 K 线」替换为「`agent/services.load_range_candles` 历史区间」。因此回测模式**不触碰任何真实资金护栏，也不需要它们**：`AI_TRADE_ENABLED`/`LIVE_TRADING_ENABLED` 双闸、就绪实盘账户、与 live-auto 原子互斥、无既有持仓门禁——**全部不适用、一律不校验**。回测模式**唯一前置**是「Agent 已配置模型」（决策需要 LLM）。这带来一个明确的正向价值：**用户可以在开启真实资金前，先用回测评估 AI 决策者在历史行情上的表现**。

---

## 评审记录（Review Notes · v2）

> 评审节点（senior reviewer）于 iteration 2 对 v1 逐节复核，维度：**可行性 / 完整性 / 一致性 / 合理规模**。全部 as-built 锚点已对代码核验（`ai_auto/{signals,session,decider,market_context,market_window,prompts}`、`engine/{simulator,backtester,metrics,mtf}`、`db/repositories.py`、`db/models.py`、`config.py`、`factors/weight_train_manager.py`、`api/{ai_auto,weight_models}.py`、前端 `AiTrade*`/`lib/{ws,aiTrade,store}`）。
> **总评**：设计根基成立、复用锚点绝大多数属实、可落地；核心命题（决策脑零改动复用 + 执行落地换 `SimBroker` + 数据源换历史区间 + 三重成本护栏）站得住。下列 **P1 已在本 v2 正文逐一修正**，P2 已修正或在表内登记。**无 P0**（无根本性阻断）。

| # | 维度 | 严重度 | 问题（v1） | v2 处置 |
|---|---|---|---|---|
| R1 | 可行性/正确性 | **P1** | §2.8 称 `_apply_consensus_gate`/`_decision_to_signals` 为「纯函数、行为逐字不变的搬迁」——**不准确**。二者均为 `self` 实例方法：`_apply_consensus_gate(self,…)` 读 `self.executor.settings.ai_trade_min_consensus`（`session.py:356`）；`_decision_to_signals(self, decision, *, board_digest)`（`session.py:389`）需 kw 参 `board_digest`（由 `self._shadows` 派生）且调 `self._ai_explain(...)`。 | 改 §2.8/§三：`apply_consensus_gate(decision,consensus,min_consensus)` 由调用方**显式传** `settings.ai_trade_min_consensus`；仅抽 `decision_to_signals(decision)->list[Signal]` **纯核心映射**，session 保留 `board_digest`/`_ai_explain` 富化（调纯核心后再富化，对外行为不变），回测直接用纯核心。`TradeDecision.size_fraction` 为 snake_case。 |
| R2 | 可行性 | **P1** | 落库 API 位置/签名错误：`create_run`/`persist_backtest_result`/`finalize_run` 在 **`db/repositories.py`（`repo`，async）**，非 `agent/services.py`；`create_run(session,*,strategy_id:int,…)` 的 `strategy_id` 为 **NOT NULL FK**（`db/models.py:49`，不可 None）；`persist_backtest_result(session, run, *, trades, equity, metrics, start_ts, end_ts, status="done")` 取 **`run` 对象** + `start_ts`/`end_ts`，非 `run_id`。 | 改 §2.0/§2.7/§三/§5.3：更正模块与签名（按 `_execute_and_persist` 范式经 `repo` 调用）；`strategy_id` 用**主策略占位** `strategyIds[0]`（禁 None），完整 `strategyIds` 存 `config`。 |
| R3 | 正确性/完整性 | **P1** | 逐策略帧 `dfs` 的**全区间**构造未指定，且实盘建帧件 `MarketWindow` **不可复用于回测**：它是**有界滚动窗口**（≤`PAPER_WINDOW_MAX`）且经**实盘 REST** 取 HTF（`prefill_htf`/`maybe_refresh_htf`→`get_rest_client().get_candles`，取近端行情）——在历史回测中即**未来函数/错时污染**，且每根重 `prepare`（O(n²)）。 | 改 §2.2/§5.2 + 新增 `ai_auto/backtest_frames.py`：**全区间 prepare-once**——每策略 `prepare(candles_to_df(range),bar)`+`attach_timeframes(load_htf_frames(…历史区间…))`，按绝对 `i` 索引，镜像 `_execute_and_persist` 的 MTF 路径；**明确不复用** `MarketWindow`。 |
| R4 | 可行性/一致性 | **P1** | 异步运行器把同步重活压在共享事件循环：`run_backtest` 整体在 `run_in_threadpool` 跑，而本运行器为 async（须 await LLM），其全区间 `prepare`（每策略 O(n)）+ 暖机回放循环**内联**在 FastAPI 事件循环上，会拖住其它 WS/请求。 | 改 §2.2/§2.6：重同步 setup（取数/建帧/暖机）经 `run_in_threadpool`/`run_in_executor` **卸载**；逐根 `mark/fill` 循环周期性 `await asyncio.sleep(0)` 让出；仅 LLM `await` 内联。 |
| R5 | 合理规模/UX | **P1** | 默认 `decisionEveryBars=1` 与 `ai_backtest_max_rounds=400` 组合，使**默认配置对常见区间即被拒**（30 天 1H≈720 根>400→`AI_BACKTEST_TOO_MANY_ROUNDS`；§7.3 自身用间隔 4）。 | 改 §2.5/§2.10：弹窗按所选区间把决策间隔 **UI 默认**取 `max(config默认, ceil(tradableBars/max_rounds))`（默认永不自拒）；config 默认保留；手动越界仍红字预览 + 禁 CTA。 |
| R6 | 一致性 | **P1** | 前端门禁被过简：`AiTradeToggle` 经 `resolveAiTradeGate()` **6 条矩阵**（会话在跑/accountMode/就绪账户/`AI_TRADE_ENABLED`/agent 配置/live-auto 互斥）判定，`AiTradeLaunchModal.ready` **硬依赖 `acknowledged`**。仅翻 accountMode/隐藏勾选不够。 | 改 §2.10：回测走**独立门禁**（仅 agent 配置），不复用 `resolveAiTradeGate`；回测 `ready` **去掉 `acknowledged`**（不止隐藏 Badge/勾选）；`acknowledged` 态在 `AiTradeToggle`（store `aiTradeAcknowledged`）。 |
| R7 | 准确性 | P2 | `SimBroker.context()` **无未实现盈亏字段**（仅 `position_side/bars_in_position/entry_price/equity`，且 `equity==cash` 非盯市）。 | 改 §2.2：`position` 取 context() 的 side/entry/bars；盯市权益/`sessionPnlUsd` 用 `equity_at(close)`（原已如此）。 |
| R8 | 准确性 | P2 | 引用位置误：`_map_trade_code` 在 `okx/trading.py`（非 ai_auto 层）；实盘双闸守卫名为 `_require_ai_enabled`（非 `_require_ai_trade_enabled`）。 | 改 §六/§三 引用。 |
| R9 | 准确性 | P2 | §2.6「镜像其经测骨架」与实测有出入：weight-train `_buffer` 为**无界 list**（全回放）、`_cancel` 为 **bool**、心跳在 **WS 路由**（非 manager）。本方案的**有界环形缓冲/`asyncio.Event`** 是有意改良而非忠实镜像。 | 改 §2.6：措辞改为「镜像骨架 + 有意改良」——有界缓冲⇒重连仅回放近端决策，全量在落库/`result`；心跳归 WS 路由。 |
| R10 | 一致性 | P2 | `plannedRounds` 估算在 §2.5 与 §2.9 表述略异，且与运行期实际（`i<n-1` 排除末根、对齐首个决策根、实取根数可能更少）不完全一致。 | 改 §2.5/§2.9：归一为单一估算式（用 `BAR_SECONDS`/`bar_to_seconds`），并注明「实际 ≤ 估算，运行期硬上限兜底、绝不静默截断」。 |
| R11 | 完整性 | P2 | 会话级 USD 止盈止损触发后的收尾语义未定（结束/继续盯市）。 | 改 §2.3：触发→次根开盘平仓→继续逐根盯市至区间末（权益走平）→正常 finalize + `result.notes` 记因。 |
| R12 | 合理规模 | P2 | `ai_auto/backtest.py`（runner+`_BacktestJob`+helpers）有超 **800 行**风险（CLAUDE.md 阈值）。 | 改 §三：manager 已独立文件；建帧助手（prepare-once）另拆 `ai_auto/backtest_frames.py`，runner 按职责拆 helper。 |

---

## 一、Overview（概述）

当前「AI 自动交易」把 AI Agent 约束为「多策略信号板 → 单一有界 `submit_trade_decision` 决策 → 既有 `LiveExecutor` 守护执行」，并实现了轮次滚动摘要式上下文压缩。它解决了「让 AI 在真实行情上实时决策」，但**只能在实盘上跑**——用户无法在投入真实资金前，先看看「这套 AI 决策者 + 我选的这几个策略，在过去三个月的 BTC 行情上会怎么交易、赚不赚钱」。策略回测早已是本工作台的一等能力（`/strategies/{id}/backtest` → `run_backtest` → 指标），但它是**确定性执行单一策略**；「AI 作为决策者综合研判多策略」这种新的自动交易类型，此前**没有对应的回测**。

本方案新增 **AI 自动交易的回测模式**：在既有「AI 自动交易」启动弹窗中加一个模式选择（**实盘 / 回测**）；选「回测」时，用户改为填「回测区间（起止时间）+ 周期 + 决策间隔 + 初始资金 + 杠杆 + 单仓 OCO% + 会话止盈止损 + 上下文刷新轮数 + 共识门槛」，点「开始 AI 回测」后，后端启动一个**流式回测任务**：在指定历史区间上**逐根**驱动——每个「决策根」为所选多策略计算信号板（复用**每策略影子 `SimBroker` 回放**求持久立场）、叠加市场体制与共识度、交给 `AiTradeDecider` 出一个受约束决策（开多/开空/平仓/观望），把决策映射为一个 `Signal` 落到**一个模拟主账户 `SimBroker`**（次根开盘成交，无未来函数），逐根盯市。任务**边跑边把执行情况流式推给前端**（进度条、当前根/时间、滚动权益、逐轮决策卡与理由、每 N 轮的上下文摘要），跑完后**计算并展示全套回测指标**（总收益/夏普/最大回撤/胜率/盈亏比/成交数）外加**AI 专属统计**（决策轮数、动作分布、平均置信度、降级轮数、摘要次数、最终长期记忆），并把结果作为一条运行落库（`mode="ai-backtest"`）进「运行历史」，可复看。

工程上，本方案的关键是**最大化复用、且严格隔离实盘链路**：AI 决策脑（`ai_auto/decider.py`、`signals.py`、`prompts.py`、`market_context.py`）**零改动复用**；模拟执行、历史取数、指标计算全部复用既有回测设施（`engine/simulator.SimBroker`、`engine/backtester.run_backtest` 的逐根流水线范式、`engine/metrics.compute_metrics`、`agent/services.load_range_candles`、`persist_backtest_result`）；流式长任务的生命周期（任务注册表 + 并发闸 + 订阅/快照/心跳 + 取消 + 落库 + 断线重连回放）**镜像已实现且经测的权重模型流式训练**（`WeightTrainManager` + `/api/ws/weight-train/{job_id}`）。**不复用、也绝不触碰** `ai_auto/session.py`/`manager.py`/`live_auto/executor.py` 这条经审计的实盘下单路径——回测走一条全新的、纯模拟的 `ai_auto/backtest.py` 运行器，避免为回测的便利去动真实资金代码。因 AI 回测**每根都要调一次 LLM**，成本与时长敏感，故设计内置三重成本护栏：**决策间隔 `decisionEveryBars`**（AI 每 K 根决策一次，SimBroker 每根仍盯市/风控）、**决策轮数硬上限 `ai_backtest_max_rounds`**（越界在启动前即拒绝并给出明确指引，绝不静默截断）、**并发任务闸 `ai_backtest_max_jobs`**（限同时进行的 LLM 成本）。

需求强调「美观优雅、顶级、符合人机交互最佳实践」。回测的可观测性正是最佳落点：一个专用的 **AI 回测结果面板 `AiBacktestModal`**——运行态显示进度头（进度条 / 当前根与时间 / 滚动权益迷你图 / 阶段）、逐轮决策日志流（新在上，含动作/理由/置信度/体制/共识/成交结果）、可随时取消；完成态转为结果仪表盘（指标卡 + 权益曲线与买卖平标记的 K 线回放 + 成交表 + AI 决策时间线与动作分布 + 上下文摘要时间线），全程沿用工作台既有设计 token 与暗/亮主题，隆重而不喧宾夺主。

---

## 二、Technical design（技术设计）

> 术语沿用 v2/增强包：一个「决策轮」= 一根「决策根」上的一次完整 AI 决策（建信号板 → 装配上下文 → `decider.decide` → 共识门禁 → 执行 → `commit_round`/摘要）。回测模式把这条既有决策链路原样搬到**历史逐根循环**里，只换执行落地与数据源。

### 2.0 As-built 复用锚点（供开发节点校准，均经代码核验）

| 复用件 | 位置（file:symbol） | 回测模式怎么用 |
|---|---|---|
| 每策略影子回放 | `ai_auto/signals.py`：`make_shadow_broker`、`step_strategy_shadow(spec, shadow, df, i)`、`evaluate_signal_board(specs, shadows, dfs, i)` | 逐根建信号板（**零改动**）。 |
| 共识度 + wire | `ai_auto/signals.py`：`Consensus`、`compute_consensus(board)` | 每轮算一次共识（**零改动**）。 |
| AI 决策器 | `ai_auto/decider.py`：`AiTradeDecider`、`decide(payload)->(decision,degrade)`、`commit_round(payload,decision,executed)`、`summarize()`、`TradeDecision` | 逐轮决策 + 滚动摘要（**零改动**，回测构造一个同款 decider）。 |
| 市场体制 | `ai_auto/market_context.py`：`build_market_context(df, bar, settings)` | 每轮注入体制（**零改动**）。 |
| 决策 prompt / 工具 | `ai_auto/prompts.py`：`build_ai_trade_system_prompt`、`build_decision_prompt`、`build_summary_prompt`、`DECISION_TOOL_SCHEMA` | 决策脑 prompt/工具**逐字复用**（保证回测忠实反映实盘 AI 行为）。 |
| 共识门禁 + 决策→信号 | `ai_auto/session.py`：`_apply_consensus_gate(decision,consensus,degrade)`、`_decision_to_signals(decision)` | **须抽到 `signals.py` 成纯函数**（§2.8 小重构），供 session 与 backtest 共用。 |
| 模拟撮合 | `engine/simulator.py`：`SimBroker`（`set_risk`/`fill_pending`/`apply_risk`/`mark`/`submit`/`finalize`/`context`/`trades`/`equity_points`）、`RiskParams`、`TradeRecord`、`EquityRecord` | 主账户模拟执行（**零改动**）。 |
| 回测逐根范式 | `engine/backtester.py`：`run_backtest` 主循环（L97-106：`fill_pending(open)→apply_risk→mark(close)→[warmup≤i<n-1]on_candle→submit`）、`candles_to_df` | AI 回测运行器**镜像这段流水线**，把 `on_candle→submit` 换成「AI 决策→`_decision_to_signals`→submit」。 |
| 指标 | `engine/metrics.py`：`compute_metrics(equity_points, trades, bar, initial_capital)` → `{totalReturn,winRate,profitFactor,maxDrawdown,sharpe,tradeCount,avgPnl}` | 完成态标准指标（**零改动**）。 |
| 历史取数 | `agent/services.py`：`load_range_candles(rest,*,inst_id,bar,start_ts,end_ts,max_bars=None)`（区间无确认根→400 `EMPTY_RANGE`）、`load_htf_frames(...)`、`_coverage_fields` | 拉历史区间 + 高周期帧（**零改动**）。 |
| 落库 | **`db/repositories.py`（`repo`，async）**：`create_run(session,*,strategy_id:int,inst_id,bar,mode,config,status="running")`、`persist_backtest_result(session,run,*,trades,equity,metrics,start_ts,end_ts,status="done")`（取 **`run` 对象** + start/end）、`finalize_run(session,run_id,*,status,…)`；接线范式见 `agent/services.py:_execute_and_persist`；`api/runs.py`：`GET /runs/{id}`、`/runs/{id}/trades`、`/runs/{id}/equity` | Run（`mode="ai-backtest"`，`strategy_id`=**主策略占位** `strategyIds[0]`，禁 None——`db/models.py:49` 为 NOT NULL FK）+ trades + equity 落库，走既有 runs 检索复看。 |
| LLM 接入 | `agent/providers.py`：`resolve_active_provider`；`agent/llm/factory.py`：`make_llm_client_from_config`；`agent/llm/base.py`：`ToolSchema`、`LLMClient.stream` | 与实盘决策器同一 LLM 抽象造 client（**零改动**）。 |
| 流式长任务范式 | `factors/weight_train_manager.py`：`WeightTrainManager`/`_TrainJob`（订阅/快照/心跳/取消/finalize/并发闸）；`api/weight_models.py`：`/api/ws/weight-train/{job_id}` | AI 回测任务/管理器/WS **镜像其骨架**。 |

### 2.1 架构与数据流（端到端）

```text
浏览器/Electron（Agent 已配置模型即可；实盘/模拟账户模式均可，回测不需真钱）
  ├─ 右侧 AgentPanel「新对话」右侧：<AiTradeToggle> → 打开 <AiTradeLaunchModal>
  │     └─ 模式选择（实盘 / 回测）——本方案新增。选「回测」：
  │          多选策略(≥1) + 标的/周期 + 回测区间[start,end] + 决策间隔 decisionEveryBars
  │          + 初始资金 + 杠杆 + 单仓 OCO(SL%/TP%) + 会话止盈/止损 + 上下文刷新轮数 + 共识门槛
  │          + 只读预览（计划决策轮数=ceil(可交易根/间隔)、手续费/滑点、初始资金）
  │          → aiBacktestApi.start({...})            （不需真实资金勾选）
  ├─ 经 Next 同源代理 /api/py/* → FastAPI :18760
  │     └─ api/ai_auto.py（扩展；回测子路由**仅** _require_agent_configured 守门，不校验 AI/LIVE 双闸）
  │           └─ ai_auto/backtest_manager.AiBacktestManager.start(req)
  │                 ├─ 前置：Agent 已配置 / 策略合法 / 区间合法 / 轮数合法 / 计划决策轮数≤max_rounds / 并发未满
  │                 ├─ 造 LLMClient（resolve_active_provider + make_llm_client_from_config）
  │                 ├─ 造 AiBacktestRunner（含 decider + specs + 主 SimBroker + 每策略影子 SimBroker）
  │                 └─ 注册 job（cap=ai_backtest_max_jobs）+ asyncio.create_task(runner.run())
  │                       ├─ phase=loading：load_range_candles([start-暖机, end]) + load_htf_frames
  │                       ├─ phase=priming：暖机段逐根回放种入每策略影子 broker 内部状态（不决策、主账户 flat）
  │                       ├─ phase=running：逐根
  │                       │    每根：main.fill_pending(open) → main.apply_risk → main.mark(close)
  │                       │    决策根(i≥暖机 且 ts≥start 且 对齐间隔 且 轮数<max 且 i<n-1)：
  │                       │      1) board = evaluate_signal_board(specs, shadows, dfs, i)
  │                       │      2) payload = build_decision_payload(...)  + marketContext(E1) + consensus(E2)
  │                       │      3) decision, degrade = await decider.decide(payload)   （超时/失败→hold）
  │                       │      4) decision, degrade = apply_consensus_gate(decision, consensus, degrade)
  │                       │      5) signals = decision_to_signals(decision) → main.submit(signals)  （次根开盘成交）
  │                       │      6) decider.commit_round(payload, decision, executed) / 每 N 轮 summarize()
  │                       │      7) 推 decision/summary/progress 事件
  │                       └─ phase=finalizing：main.finalize → compute_metrics + aiStats → 落 Run/trades/equity → 推 result/done
  └─ 前端 <AiBacktestModal>（活跃 job 时）
        └─ /ws/ai-backtest/{jobId}（事件 run/snapshot/phase/progress/decision/summary/trade/result/done/cancelled/error/ping）
        ├─ 运行态：进度头（进度条/当前根·时间/滚动权益迷你图/阶段）+ 逐轮决策日志流 + 取消
        └─ 完成态：指标卡 + 权益曲线K线回放(买卖平标记) + 成交表 + AI决策时间线/动作分布 + 上下文摘要时间线
```

**核心设计三句话**：
1. **决策脑不变、执行与数据源变**。回测复用与实盘**逐字相同**的决策链路（信号板 + 体制 + 共识 + `AiTradeDecider` + 门禁 + 滚动摘要），只把「决策 → 真单」换成「决策 → `SimBroker` 模拟撮合」、把「WS 实时 K 线」换成「历史区间逐根」。这保证回测是实盘 AI 行为的**忠实预演**。
2. **不碰实盘路径**。新增独立运行器 `ai_auto/backtest.py`，不改 `ai_auto/session.py`/`manager.py`/`live_auto/executor.py`。回测不经原子互斥、不需双闸/账户/无持仓门禁——它没有真实资金面。
3. **成本有界、可观测、可取消、可复看**。决策间隔 + 决策轮数硬上限 + 并发闸控成本；流式推进度/决策/摘要；随时取消；落 Run 进历史。

### 2.2 AI 回测逐根驱动循环（`ai_auto/backtest.py::AiBacktestRunner`）

运行器**镜像** `engine/backtester.run_backtest` 的逐根四阶段（`fill_pending → apply_risk → mark → 决策`），但因决策要 `await` LLM，运行器整体是 **async 协程**（不是 `run_backtest` 那种在 threadpool 里跑的纯同步函数）。

```text
run_backtest 单策略（既有，同步）        AiBacktestRunner（本方案，async）
  for i in range(n):                        for i in range(n):
    fill_pending(open); apply_risk; mark      main.fill_pending(open); main.apply_risk; main.mark
    if warmup≤i<n-1:                          # 每根都盯市/风控（OCO 逐根生效）
      ctx=context();                          if is_decision_bar(i):        # ← 间隔/暖机/区间/轮数门控
      sig=strategy.on_candle(df,i,ctx)          board=evaluate_signal_board(specs,shadows,dfs,i)
      if sig: submit(sig)                       decision=await decider.decide(payload)  # ← 唯一 async
                                                decision=apply_consensus_gate(...)
                                                main.submit(decision_to_signals(decision))
                                                decider.commit_round(...) / summarize()
                                                emit decision/summary
  finalize; compute_metrics                   emit progress(每根/节流)
                                              finalize; compute_metrics + aiStats; persist; emit result
```

**决策根门控 `is_decision_bar(i)`**（严格无未来函数）：
- `i >= warmup`（暖机足够，指标/体制/影子立场就绪）；
- `candles[i].ts >= user_start_ts`（暖机段在用户区间之前，不产决策）；
- `i < n - 1`（最后一根不决策——无次根可成交，对齐 `run_backtest` 的 `i<n-1`）；
- `(i - first_decision_index) % decisionEveryBars == 0`（决策间隔下采样；`first_decision_index` = 首个满足前两条的根索引）；
- `rounds_done < ai_backtest_max_rounds`（轮数硬上限；到顶后停止决策但继续逐根盯市至区间末，并在 `result.notes` 明示「已达决策上限、末段仅盯市」——**不静默截断**）。

**暖机窗口**：`warmup = max(max(spec.strategy.warmup() for spec in specs), max(spec.strategy.htf_warmup_bars() for spec in specs), regime_min_bars if regime启用 else 0, 1)`（`regime_min_bars=60`，见 `engine/regime`）。取数把左界前移到 `start_ts - warmup*bar_sec`（`bar_sec` 经 `engine/mtf.bar_to_seconds` / `okx/types.BAR_SECONDS`），`load_range_candles(start_ts=start_ts - warmup*bar_sec, end_ts=end_ts, max_bars=backtest_max_bars)`，使**用户区间第一根即可决策**；若历史深度不足（实取根数 < 期望），首几根决策的指标暖机不足——须在 `result.notes` 提示。

**逐策略帧 `dfs` 的构造（R3，须显式）**：**不复用实盘 `ai_auto/market_window.MarketWindow`**——它是**有界滚动窗口**（`deque(maxlen=window_max)`，`window_max ≤ PAPER_WINDOW_MAX`）且经**实盘 REST**（`prefill_htf`/`maybe_refresh_htf`→`get_rest_client().get_candles`）取**近端** HTF，用于历史回测将引入**未来函数/错时污染**，且 `build_strategy_frames` 每次重跑 `prepare`（O(n²)）。改为**全区间 prepare-once**（新增助手 `ai_auto/backtest_frames.py`）：一次 `df = candles_to_df(range_rows)`；每策略 `df_k = spec.strategy.prepare(df.copy(), bar)`，含高周期则 `df_k = spec.strategy.attach_timeframes(df_k, bar, htf_frames)`，其中 `htf_frames = load_htf_frames(rest, inst_id=…, start_ts=start_ts-warmup*bar_sec, end_ts=end_ts, base_seconds=bar_sec, timeframes=union(spec.strategy.timeframes()), htf_warmup_bars=max(htf_warmup))`（**历史区间**取数，无未来函数），镜像 `agent/services._execute_and_persist` 的 MTF 装配。所得 `dfs=[df_k…]` 按**绝对索引 `i`** 供 `evaluate_signal_board(specs, shadows, dfs, i)`。

**事件循环友好（R4）**：`run_backtest` 整体在 `run_in_threadpool` 跑；本运行器为 async（须 await LLM），故把**重同步 setup**（`candles_to_df` + 逐策略 `prepare`/`attach_timeframes` + 暖机逐根回放种影子）经 `await run_in_threadpool(...)` / `loop.run_in_executor` **卸载出事件循环**；逐根 `fill/apply_risk/mark` 循环中周期性 `await asyncio.sleep(0)` 让出；仅 `decider.decide`/`summarize`/`commit_round` 的 LLM `await` 内联，避免拖住 FastAPI 事件循环上的其它 WS/请求。

**暖机段**：逐根 `step_strategy_shadow` 种入每策略影子内部状态（`self._peak/_trough` 等），主账户 `SimBroker` 只 `mark`、恒 flat（决策从用户区间首根起）。

**决策 payload 装配**（键集对齐实盘 `_run_decision_round`；注意 `_build_decision_payload` 是 session 实例方法，回测**自建**同键 dict，非直调）：`round`、`barTs`、`lastPx`、`position`（由主 broker `context()` 取 `position_side`/`entry_price`/`bars_in_position`——**更正 R7**：`context().equity == cash`（非盯市），`context()` **无未实现盈亏字段**，如需未实现盈亏由 `equity_at(close) - cash` 现算）、`sessionPnlUsd`（`main.equity_at(close) - initial_capital`，盯市口径）、`budgetUsd`（=初始资金）、`takeProfitUsd`/`stopLossUsd`、`signalBoard`（逐策略 stance+latestSignal+reason+riskParams）、`marketContext`（E1，注入自基础帧 `df.iloc[:i+1]` 单点因果判定）、`consensus`（E2 wire——实盘在 `_run_decision_round` 注入而非 `_build_decision_payload`，回测每轮自算注入；`build_decision_prompt` 容忍 payload 无 `summary` 键）。

### 2.3 执行落地替换：决策 → `Signal` → `SimBroker`（对比实盘 `LiveExecutor`）

实盘把决策映射为 `Signal` 交 `LiveExecutor.apply`（真单，近即时市价成交）。回测**完全复用同一「决策→`Signal`」映射**（抽到 `signals.decision_to_signals`，§2.8），但把落地换成 `SimBroker.submit`：

| `TradeDecision.action` | 映射 `Signal`（`strategies.base.Signal`） | `SimBroker` 行为（既有语义，零改动） |
|---|---|---|
| `open_long`  | `Signal(side="long",  strength=size_fraction, reason)` | flat→次根开盘开多；同向→跳过；反向→次根先平后开 |
| `open_short` | `Signal(side="short", strength=size_fraction, reason)` | 同上（对称） |
| `close`      | `Signal(side="close", reason)` | 有仓→次根开盘平；flat→跳过 |
| `hold`       | `[]` | 无动作 |

**关键语义差异（须在 spec 明示）**：`SimBroker.submit` 的信号**在次根开盘价成交**（`fill_pending`，`engine/simulator.py:156`），这与实盘 `LiveExecutor` 的近即时市价成交不同——但**这正是所有回测（策略/纸面/factor_score）的标准无未来函数语义**，AI 回测沿用它才与其它回测口径一致、可比。`size_fraction` 直接进 `SimBroker._open` 的名义敞口 `strength·equity·leverage`（`simulator.py:250`）；`open_*` 缺 `size_fraction` 时 `decider._map_decision` 已回落 hold（复用，无需回测另处理）。会话止盈止损（USD 阈值）与单仓 OCO（SL%/TP%）：单仓 OCO 经 `main_broker.set_risk(RiskParams(stop_loss_pct, take_profit_pct, max_hold_bars))`（E4 语义，构造期传入，`SimBroker.apply_risk` 逐根触发）；会话级 USD 止盈止损由运行器每根（用 `equity_at(close)` 口径）检查 `sessionPnlUsd ≥ +takeProfitUsd 或 ≤ -stopLossUsd` → 主动 `main.submit([Signal(side="close", reason="session_take_profit"/"session_stop_loss")])`（**次根开盘平仓，无未来函数**）→ **此后停止决策、继续逐根 `mark` 盯市至区间末（权益走平）** → 正常 `finalize` + `compute_metrics`，并在 `result.notes` 记「已触发会话级止盈/止损、末段仅盯市」（R11）。

### 2.4 上下文管理：滚动摘要（回测中原样复用，天然更透明）

回测**逐字复用** `AiTradeDecider` 的滚动摘要机制（`commit_round` 追加 `_context`/`_journal` → 每 `contextRefreshRounds` 轮 `summarize()` 以完整 `_journal` 为素材摘要、重置 `_context`、清 `_journal`；见增强包 §2.4）。回测里这一机制**天然更可观测**：每次 `summarize()` 推一条 `summary` 事件，前端「上下文摘要时间线」把 AI 在回测过程中的**长期记忆演进**完整呈现——这既验证了「每 N 轮纯净上下文」承诺，也是极佳的可解释性素材。`contextRefreshRounds` 沿用实盘的服务端区间校验 `[ai_trade_min_context_rounds, ai_trade_max_context_rounds]`、默认 `ai_trade_default_context_rounds=10`。

### 2.5 决策成本护栏（回测特有，一等设计约束）

AI 回测**每决策根调一次 LLM**（决策），外加每 `contextRefreshRounds` 轮一次 LLM（摘要）。若在 500 根 1H 区间每根决策，即约 500+ 次 LLM 调用、单次数秒→整个回测数十分钟乃至小时级，且成本高。故三重护栏：

1. **决策间隔 `decisionEveryBars`（用户可设，config 默认 `ai_backtest_default_decision_every_bars=1`）**：AI 每 K 根决策一次；SimBroker **每根**仍 `fill_pending/apply_risk/mark`（OCO/盯市不降频，权益曲线仍逐根）。`K=4` 即把 LLM 调用降到 1/4。**弹窗按所选区间把间隔的 UI 默认取 `max(ai_backtest_default_decision_every_bars, ceil(tradableBars / ai_backtest_max_rounds))`**（R5），使默认配置**永不因越界自拒**（避免「30 天 1H≈720 根 > 400 上限」这类默认即被拒的坏体验）；用户手动调小致越界时仍红字预览 + 禁 CTA，并给「建议增大间隔」软提示。
2. **决策轮数硬上限 `ai_backtest_max_rounds`（默认 400）**：归一估算式（R10）`tradableBars = (end_ts - start_ts)//bar_sec + 1`、`plannedRounds = ceil(tradableBars / decisionEveryBars)`（与 §2.9 前置校验**同式**，`bar_sec` 经 `BAR_SECONDS`/`bar_to_seconds`）；启动前若 `plannedRounds > ai_backtest_max_rounds` → **拒绝** `400 AI_BACKTEST_TOO_MANY_ROUNDS`，错误信息含 `plannedRounds/limit` 与明确指引（缩短区间或增大决策间隔）。**注**：`plannedRounds` 为估算，运行期实际决策轮数 ≤ 估算（末根 `i<n-1` 不决策、对齐首个决策根、`load_range_candles` 实取根数可能更少）；即便实际到顶亦**停止决策、继续盯市到区间末**并在 `result.notes` 明示——**绝不静默截断**。
3. **并发任务闸 `ai_backtest_max_jobs`（默认 2）**：`AiBacktestManager` 限同时进行的回测 job 数（镜像 `WeightTrainManager` 的并发预留闸），超出 → `429 AI_BACKTEST_BUSY`。控住同时消耗 LLM 的量级。

另：单次决策沿用 `ai_trade_decision_timeout_s`（默认 60s）超时→hold；取数根数受 `settings.backtest_max_bars` 上限（`load_range_candles` 内建）。

### 2.6 流式任务生命周期（镜像 `WeightTrainManager`）

回测是「一次性、长时、可取消、需断线重连回放」的后台任务——与权重模型流式训练同构，故**镜像其经测骨架**，而非镜像 live-auto 的 socket 生命周期会话：

- **`AiBacktestManager`（`ai_auto/backtest_manager.py`）**：进程级单例（`get_ai_backtest_manager()`），持 `jobs: dict[str, _BacktestJob]`；并发预留闸（`ai_backtest_max_jobs`）；`start(req) -> job_id`（`job_id=f"aibt_{uuid.hex}"`）、`get(job_id)`、`cancel(job_id)`、`shutdown()`（进程退出经 `main.py` lifespan 调，取消所有在跑 job）。
- **`_BacktestJob`（`ai_auto/backtest.py` 内）**：持 `AiBacktestRunner` + 事件缓冲 + 订阅者集合（`set[asyncio.Queue]`）+ `state`（`running`/`completed`/`cancelled`/`failed`）+ `_cancel`（`asyncio.Event`）。**与 weight-train 骨架的有意差异（非忠实镜像，R9）**：weight-train `_TrainJob._buffer` 是**无界 list**（全事件重放）、`_cancel` 是 **bool**、心跳在 **WS 路由**（`_WS_HEARTBEAT_SECONDS`）。本方案采**有界环形缓冲**（`deque(maxlen=…)`，决策近 200、进度只留最新）以控长回测内存——代价是**重连仅回放近端决策**，完整决策/权益/成交在**落库**与完成 `result` 事件里给全量；`_cancel` 用 `asyncio.Event`（等价、更清晰）；心跳同样归 **WS 路由**（连接侧空闲发 `ping`，与 weight-train 一致）。WS 路由内 `subscribe()`+`snapshot()` 原子回放「已缓冲 run/phase/progress + 决策/摘要近 N 条 + 若已完成 result」再续订新事件（防丢事件）；`publish(event)` 广播 + 入环形缓冲。运行器每根/每决策检查 `_cancel.is_set()`，取消则停循环、`finalize` 已成交部分、落 Run（`status="cancelled"`）、推 `cancelled`。
- **断线重连**：前端 `AiBacktestModal` 持 `jobId`；重连即 `GET /ai-trade/backtest/{jobId}`（取当前 phase/progress/result）+ 重开 WS 回放。任务独立于 socket 存活（镜像 weight-train）。
- **内存**：决策/权益等长数组在**缓冲环**里按上限保留（如决策近 200 条、进度只留最新），完整数组在**落库**（trades/equity）与**完成 `result` 事件**里给全量；长区间不撑爆内存。

### 2.7 指标与 AI 统计与落库

- **标准回测指标**：`compute_metrics(main.equity_points, main.trades, bar, initial_capital)` → `{totalReturn, winRate, profitFactor, maxDrawdown, sharpe, tradeCount, avgPnl}`（**零改动复用**）。
- **AI 专属统计 `aiStats`**（运行器聚合）：`decisionRounds`、`actionCounts{open_long,open_short,close,hold}`、`holdRate`、`avgConfidence`、`degradedRounds`（共识门禁把 open_* 降级为 hold 的轮数）、`timeoutRounds`（LLM 超时/失败回落 hold 的轮数）、`summaryCount`、`finalSummary`（最后一段长期记忆）、`decisionEveryBars`、`plannedRounds`、`reachedRoundCap`（bool）。
- **落库（`db/repositories.py` 的 `repo.*` async 函数，按 `agent/services._execute_and_persist` 范式接线；R2）**：`run = await repo.create_run(session, strategy_id=strategyIds[0]（**主策略占位，禁 None——`strategy_id` 为 NOT NULL FK**）, inst_id=inst_id, bar=bar, mode="ai-backtest", config={strategyIds, startTs, endTs, decisionEveryBars, contextRefreshRounds, initialCapital, leverage, stopLossPct, takeProfitPct, takeProfitUsd, stopLossUsd, minConsensus})` → 跑完 `await repo.persist_backtest_result(session, run, trades=main.trades, equity=main.equity_points, metrics={**compute_metrics, "aiStats": aiStats, "finalSummary": …, "decisionsDigest": […≤200]}, start_ts=startTs, end_ts=endTs, status="completed"|"cancelled"|"failed")`（**取 `run` 对象 + `start_ts`/`end_ts`，非 `run_id`**；取消/失败路径亦可 `await repo.finalize_run(session, run.id, status=…)`）。Run/trades/equity **走既有 `GET /runs/{id}` `/runs/{id}/trades` `/runs/{id}/equity` 检索**，回测结果因此可在「运行历史」复看（与策略回测同构）。`mode="ai-backtest"`（11 字符，`String(16)`）是自由字符串列（已核验无 enum/CHECK、无迁移）；`strategy_id` 列 **NOT NULL**，故存 `strategyIds[0]` 占位、完整策略集在 `config.strategyIds`；`decisionsDigest` 为近 N 条决策的紧凑数组，供复看时无需 WS 也能画决策时间线。**决策日志/摘要的实时全量走 WS `result` 事件**；落库存**摘要版**（`aiStats + finalSummary + decisionsDigest≤200`）避免 metrics_json 过大。

### 2.8 复用小重构（把两纯函数从 session 抽到 signals，零行为变更）

**现状核验（更正 v1 的「纯函数」说法，R1）**：二者现为 `ai_auto/session.py`（`:349`/`:389`）的 **`self` 实例方法**，均有真实依赖，**并非**可字节级搬迁的纯函数：
- `_apply_consensus_gate(self, decision, consensus, degrade)` 读 `self.executor.settings.ai_trade_min_consensus`（`session.py:356`）；
- `_decision_to_signals(self, decision, *, board_digest)` 需 kw 参 `board_digest`（由 `self._board_digest()`→`self._shadows` 派生）并调 `self._ai_explain(...)`（`session.py:389-394`）。

为让回测与实盘**共用核心逻辑**又**不改实盘行为**，抽取方式修正为：
- **共识门禁**：上移为模块级 `apply_consensus_gate(decision, consensus, min_consensus) -> (decision, degrade)`（把 `min_consensus` **显式入参**）。`session._apply_consensus_gate` 改为**薄封装**，调用时传 `self.executor.settings.ai_trade_min_consensus`；回测直接传 `settings.ai_trade_min_consensus`（或用户覆盖的 `minConsensus`）。
- **决策→信号**：只上移**纯核心映射** `decision_to_signals(decision) -> list[Signal]`（`action`→`Signal(side, strength=size_fraction, reason)`，注意 `TradeDecision.size_fraction` 为 **snake_case**）。session 的 `_decision_to_signals(self, decision, *, board_digest)` **对外行为逐字保留**：内部改为「先调纯核心 `decision_to_signals(decision)`，再补 `self._ai_explain(...)` 富化 `Signal.explain`」——实盘的 `board_digest`/atomic-explain 富化不变。回测**不需**该富化，直接用纯核心 `Signal`。

`session.py` 增 `from .signals import apply_consensus_gate, decision_to_signals`。**行为不变由回归保证**：`test_ai_trade_session.py`/`test_ai_trade_signals.py` 断言 session 转调后「决策→执行→富化 `explain`」链路逐字不变。这是本方案对实盘代码的**唯一**改动，纯抽取 + 薄封装，不改逻辑、不碰下单/护栏。

### 2.9 门禁（相对实盘大幅收敛——回测无真实资金面）

`AiBacktestManager.start(req)` 顺序前置校验（任一不过即拒、绝不假启动），**显著少于**实盘 §2.5：

1. **Agent 已配置模型**：`resolve_active_provider(...) is not None`，否则 `400 AGENT_NOT_CONFIGURED`（决策需 LLM——这是回测**唯一**的能力前置）。
2. **策略集合合法**：`1 ≤ len(strategyIds) ≤ ai_trade_max_strategies(8)`，逐个 `get_strategy_row` 存在且 `build_paper_strategy_spec` 可实例化，否则 `400 AI_TRADE_STRATEGIES_INVALID`（复用码）。
3. **标的为 USDT 本位 SWAP/FUTURES**（供取数一致）：可选，回测取历史 K 线不下单，主要保证 `bar`/`instId` 有效；无效标的会在 `load_range_candles` 400 `EMPTY_RANGE`。
4. **区间合法**：`start_ts < end_ts`，且非未来；否则 `400 AI_BACKTEST_RANGE_INVALID`。
5. **轮数合法**：`contextRefreshRounds ∈ [min,max]`，否则 `400 AI_TRADE_CONTEXT_ROUNDS_INVALID`（复用码）。
6. **决策轮数上限**：`tradableBars = (end_ts-start_ts)//bar_sec + 1`、`plannedRounds = ceil(tradableBars / decisionEveryBars)`（与 §2.5 **同式**；`bar_sec` 经 `BAR_SECONDS`/`bar_to_seconds`）须 `≤ ai_backtest_max_rounds`，否则 `400 AI_BACKTEST_TOO_MANY_ROUNDS`（含 `plannedRounds/limit` 与指引）。此为估算，运行期实际 ≤ 估算（末根不决策等），到顶按「停决策续盯市 + `result.notes`」兜底（R10）。
7. **并发未满**：`len(running jobs) < ai_backtest_max_jobs`，否则 `429 AI_BACKTEST_BUSY`（计数闸；镜像 weight-train 的 `reserve()` 预留计数，非 `Semaphore`）。

**明确不校验**（与实盘的根本区别）：`AI_TRADE_ENABLED`/`LIVE_TRADING_ENABLED` 双闸、就绪实盘账户、与 live-auto 原子互斥、无既有持仓门禁——回测不下真单，一律不适用。**因此回测在 `accountMode="sim"`（模拟账户模式）也可用**（前端门禁相应放宽，§2.10）。

### 2.10 前端交互设计（美观优雅、符合 HCI 最佳实践）

- **入口沿用同一开关与弹窗**（满足需求「对应的弹窗面板中，可以选择回测模式」）：`AiTradeToggle`（`AgentPanel.tsx:300`）点开 `AiTradeLaunchModal`；弹窗**顶部新增模式段控**（`ui/Tabs` 或段控：**实盘 / 回测**）。
  - **门禁放宽（更正 v1，R6）**：现开关经 `resolveAiTradeGate()`（`lib/aiTrade.ts`）**6 条矩阵**判定（会话在跑 / `accountMode==="live"` / 就绪默认实盘账户 / 后端 `AI_TRADE_ENABLED` / Agent 已配置 / 无 live-auto 占用），**并非**单一 `accountMode==="live"`。改为：**只要 Agent 已配置模型即可点开弹窗**（新增一条**独立于 `resolveAiTradeGate` 的回测门禁**，仅校验 Agent 配置，不复用实盘 6 矩阵）；进入弹窗后按模式分别守门——**实盘**段仍走完整 `resolveAiTradeGate`（缺账户/双闸未开/live-auto 占用等就地禁用 + 悬浮文案，逻辑不变）；**回测**段仅需「Agent 已配置」。于是模拟账户模式用户也能开 AI 回测。若实盘前置不满足，弹窗**默认落到「回测」段并禁用「实盘」段 + 悬浮说明**。
- **回测模式弹窗字段**（复用既有输入原语 `Field`/`Row`/checkbox 清单）：多选策略（复用）、标的（复用）、**回测区间起止**（`datetime-local` × 2，默认取当前图表可见区间或近 N 天）、周期（复用当前 `bar`）、**决策间隔 `decisionEveryBars`**（number，UI 默认按所选区间取 `max(config默认, ceil(tradableBars/ai_backtest_max_rounds))` 使默认永不自拒，含「越大越省 LLM」提示；R5）、**初始资金**（复用「总预算」字段，回测语义为初始资金，留空＝`settings.initial_capital`）、杠杆、**单仓止损%/止盈%**（复用 E4 两字段，回测＝`SimBroker` OCO）、会话总止盈/总止损（USD，可选）、上下文刷新轮数、**共识门槛 `minConsensus`**（可选）。**回测段不渲染**「真实资金」`Badge`（`tone="short"`）与「我已知晓真实资金」`acknowledged` 勾选，并**在回测 `ready` 门禁中去掉 `acknowledged` 依赖**（R6——该态 lifted 于 `AiTradeToggle`→store `aiTradeAcknowledged`，仅实盘段用，非仅隐藏 UI）；改标一枚 `Badge tone="accent">模拟回测`。只读预览区：**计划决策轮数** `plannedRounds`（据区间/间隔实时算，超 `max_rounds` 高亮红字并禁用 CTA）、手续费/滑点、初始资金。主 CTA「**开始 AI 回测**」（`ready = 选≥1策略 && 区间合法 && 轮数不越界 && !inputError`；**无需 acknowledged**）。
- **AI 回测结果面板 `components/Chart/AiBacktestModal.tsx`**（大号 `ui/Modal` 或抽屉，全宽）——活跃 job 时挂载：
  - **运行态**：进度头（`progress` 事件驱动的进度条 + 「第 i/n 根 · {日期}」+ 滚动权益迷你图 + 阶段徽标 loading/priming/running/finalizing）；**逐轮决策日志流**（`decision` 事件，新在上：动作中文化「开多/开空/平仓/观望」+ 置信度 + 理由 + 体制徽标（`describeRegime`）+ 共识计（`describeConsensus`）+ 成交结果（`describeExecuted`）+ 降级原因）；**上下文摘要卡**（最新 `summary`）；**取消**按钮（`aiBacktestApi.cancel`）。
  - **完成态**（`result`/`done` 到达后原地转换）：**指标卡区**（复用 `Strategy/MetricsCards` 视觉：总收益/夏普/最大回撤/胜率/盈亏比/成交数）+ **AI 统计卡**（决策轮数/动作分布条/平均置信度/降级轮数/摘要次数）；**权益曲线与 K 线回放**（复用 `Factor/WeightBacktestChart` 的 K 线 + 买/卖/平标记 + 权益叠加 或 `Chart/EquityChart`）；**成交表**（复用 `Strategy/TradesTable`）；**AI 决策时间线**（动作/置信度/体制/共识逐轮，可与权益曲线时间对齐）；**上下文摘要时间线**（历轮 `summary` 折叠列表，呈现长期记忆演进）；页脚「在运行历史中查看（runId）」+「关闭」。
- 全程沿用既有设计 token（`--accent`/`--surface`/`--border`/`--long`/`--short`）、暗/亮主题、`ui` 原语；与工作台一致、隆重不喧宾夺主。

---

## 三、File / module change plan（文件 / 模块变更计划）

> 约定：✚=新建，✎=修改。遵循 CLAUDE.md 阈值（单文件 ≤800 行、单函数 ≤50 行、≤5 形参、圈复杂度 ≤10、嵌套 ≤4、公开 API 写 docstring）。回测**绝不新增真单路径**；对实盘代码的唯一改动是 §2.8 两纯函数搬迁（行为不变）。

### 后端 `services/quant/app/`

| 文件 | 操作 | 一句话意图 |
|---|---|---|
| `ai_auto/backtest.py` | ✚ | `AiBacktestRunner`（async 逐根循环：取数→暖机→逐根 fill/risk/mark→决策根跑 `evaluate_signal_board`+`decider.decide`+门禁+`decision_to_signals`→`main.submit`+`commit_round`/`summarize`→emit progress/decision/summary→finalize+`compute_metrics`+aiStats+落库）+ `_BacktestJob`（事件缓冲环/订阅/取消/state）。**按阈值拆 helper**（取数/暖机/单根步进/决策轮/聚合/落库分函数）。 |
| `ai_auto/backtest_manager.py` | ✚ | `AiBacktestManager`（进程级单例，`jobs` 注册表 + 并发闸 `ai_backtest_max_jobs`（计数 `reserve()` 式，非 `Semaphore`）+ `start/get/cancel/shutdown`；`start` 内做 §2.9 前置门禁、造 LLMClient、造 runner、`asyncio.create_task`）；`get_ai_backtest_manager()`。镜像 `factors/weight_train_manager.py` 骨架。 |
| `ai_auto/backtest_frames.py` | ✚ | 全区间 **prepare-once** 建帧助手（R3/R12）：`build_backtest_frames(specs, range_rows, htf_frames, bar) -> list[pd.DataFrame]`（每策略 `prepare`+`attach_timeframes`，一次性、按绝对索引），**替代** live `MarketWindow` 于回测（后者滚动窗口 + 实盘 REST HTF，回测会引入未来函数）。分拆以控 `ai_auto/backtest.py` ≤800 行。 |
| `ai_auto/signals.py` | ✎ | 新增模块级 `apply_consensus_gate(decision,consensus,min_consensus)`（`min_consensus` **显式入参**）与**纯核心** `decision_to_signals(decision)->list[Signal]`（§2.8/R1）；供 session 与 backtest 共用。 |
| `ai_auto/session.py` | ✎ | `_apply_consensus_gate` 改**薄封装**（传 `self.executor.settings.ai_trade_min_consensus` 给模块函数）；`_decision_to_signals(self, decision, *, board_digest)` 内部改为「调纯核心 `decision_to_signals` + 补 `self._ai_explain` 富化」（§2.8/R1）。**对外行为逐字不变**，回归 `test_ai_trade_session.py` 守。 |
| `api/ai_auto.py` | ✎ | 新增回测子路由：`POST /ai-trade/backtest/start`、`POST /ai-trade/backtest/{job_id}/cancel`、`GET /ai-trade/backtest/{job_id}`、`WS /ws/ai-backtest/{job_id}`；新增 `_require_agent_configured` 守门（复用 `resolve_active_provider(session_factory, settings) is not None`，**仅**校验 Agent 配置，**不**调实盘双闸守卫 `_require_ai_enabled`；R8）；错误体沿用 `{error:{code,message}}`。 |
| `config.py` | ✎ | 新增 `ai_backtest_max_rounds:int=400`、`ai_backtest_default_decision_every_bars:int=1`、`ai_backtest_max_jobs:int=2`。复用 `initial_capital`/`default_taker_fee_bps`/`default_slippage_bps`/`backtest_max_bars` 及全部 `ai_trade_*`（决策超时/上下文轮数区间/最大策略数/OCO 默认与上限/min_consensus/regime_context）。**须真的声明**（`Settings` 为 `extra="ignore"`，未声明的 env 静默丢弃）。 |
| `schemas.py` | ✎ | 新增 `AiBacktestStartIn`/`AiBacktestStartOut`/`AiBacktestStatusOut`/`AiBacktestResultOut`/`AiBacktestAiStatsOut`/`AiBacktestProgressOut`（camelCase wire，见 §五）。决策事件复用既有 `AiTradeDecisionOut` 作契约镜像（真上线是运行器手工 dict）。 |
| `main.py` | ✎ | lifespan 关停增 `await get_ai_backtest_manager().shutdown()`（紧随 `get_ai_trade_manager().shutdown()`）。（路由已随 `ai_auto.router` 注册，无需另挂。） |
| `agent/services.py` + `db/repositories.py` | （复用） | `agent/services`：`load_range_candles`/`load_htf_frames`/`_coverage_fields`/`_resolve_costs` + `_execute_and_persist`（落库接线**范式**）。**落库函数在 `db/repositories.py`（`repo`，async）**：`create_run`/`persist_backtest_result`（取 `run` 对象 + `start_ts`/`end_ts`）/`finalize_run`；`get_strategy_row` 亦在此。不改其签名（R2，见 §2.7/§5.3）。 |

### 前端 `apps/web/`

| 文件 | 操作 | 一句话意图 |
|---|---|---|
| `lib/aiBacktestTypes.ts` | ✚ | camelCase 类型：`AiBacktestStartReq`/`AiBacktestStartResult`/`AiBacktestStatus`/`AiBacktestPhase`/`AiBacktestProgress`/`AiBacktestResult`/`AiBacktestAiStats`/`AiBacktestJobState`（决策/摘要复用 `aiTradeTypes` 的 `AiTradeDecision`）。 |
| `lib/aiBacktestApi.ts` | ✚ | `/api/py/ai-trade/backtest/*` REST（`start`/`cancel`/`status`）+ `aiBacktestWS(jobId,onEvent,onState)`（复用 `ReconnectingWS`，`/api/ws/ai-backtest/{jobId}`）。 |
| `lib/aiBacktest.ts` | ✚ | 纯件：`planRounds(startTs,endTs,bar,every)`、`validateBacktestRange`、`describeBacktestPhase`、`actionDistribution(decisions)`、指标/百分比格式化、进度%。 |
| `lib/aiBacktest.test.ts` | ✚ | 上述纯件单测（轮数估算/区间校验/阶段文案/动作分布/边界）。 |
| `components/Agent/AiTradeLaunchModal.tsx` | ✎ | 顶部加「实盘/回测」段控；回测段渲染区间/间隔/初始资金/OCO%/会话止盈损/轮数/共识/只读预览（计划轮数），移除真实资金勾选与 Badge；`onConfirm` 按模式分派 `aiTradeApi.start` 或 `aiBacktestApi.start`。 |
| `components/Agent/AiTradeToggle.tsx` | ✎ | 放宽门禁：Agent 已配置即可开弹窗；实盘段门禁不变；回测确认→启动 job + 打开 `AiBacktestModal`（开关不 latch，回测非持久会话）。 |
| `components/Chart/AiBacktestModal.tsx` | ✚ | AI 回测结果面板：运行态（进度头 + 决策日志流 + 摘要 + 取消）/ 完成态（指标卡 + AI 统计 + 权益K线回放 + 成交表 + 决策时间线 + 摘要时间线）。复用 `MetricsCards`/`TradesTable`/`WeightBacktestChart`/`EquityChart` 视觉。 |
| `lib/useAiBacktestJob.ts` | ✚ | 挂于 `AiBacktestModal`：持 `jobId`，`GET /ai-trade/backtest/{id}` 重发现 + `aiBacktestWS` 订阅 + 事件按 type 路由进本地/瞬态状态（phase/progress/decision/summary/trade/result）。 |
| `app/page.tsx` | ✎ | 渲染 `<AiBacktestModal>`（活跃 `aiBacktestJobId` 时）；`overlayTrades` 可选叠加回测成交 marker（仅回测面板打开时）。 |
| `lib/store.ts` | ✎ | 新增**瞬态**（不入 `partialize`）：`aiBacktestJobId:string|null` + setter（供断线重连重发现）。可选持久化偏好 `aiBacktestConfig{decisionEveryBars,contextRefreshRounds}`（进 `partialize`）。**store 无需升版**（v25 瞬态切片可承载）。 |

### 测试

| 文件 | 操作 | 一句话意图 |
|---|---|---|
| `services/quant/tests/test_ai_backtest_runner.py` | ✚ | 桩 `LLMClient`（脚本化 `tool_use_start/args_delta/message_done` 决策序列）+ 合成/replay 行情：暖机不决策；决策根对齐 `decisionEveryBars`；决策→`main.submit`→次根开盘成交（断言 trades/equity）；`compute_metrics` 与 aiStats 正确；共识门禁降级记入 aiStats.degradedRounds 且 `commit_round` 记降级后 hold；每 N 轮 `summary` 事件；到 `max_rounds` 停决策续盯市 + `result.notes` 标注；取消 mid-run → `status=cancelled` 且已成交部分被 finalize。 |
| `services/quant/tests/test_ai_backtest_manager.py` | ✚ | 并发闸（第 `max_jobs+1` 个 start→429）；job 注册/`get`/`cancel`；`subscribe` 回放（已缓冲 phase/progress/决策 + 完成后 result）。 |
| `services/quant/tests/test_ai_backtest_api.py` | ✚ | `ASGITransport`+桩：Agent 未配置→400；区间非法→400；`plannedRounds>max`→400（含指引）；策略越界→400；start→status→cancel 生命周期；WS 连接原子 subscribe→snapshot；**回测路由不校验 AI/LIVE 双闸**（`AI_TRADE_ENABLED=false` 时回测仍可启动）。 |
| `services/quant/tests/test_ai_trade_signals.py` | ✎ | 补 `apply_consensus_gate`/`decision_to_signals` 上移后的单测（纯函数：门禁降级、映射 Signal），并回归 session 转调后行为不变。 |
| `apps/web/lib/aiBacktest.test.ts` | ✚ | 见上。 |
| `apps/web/components/Agent/AiTradeLaunchModal.test.tsx` | ✎ | 补：切到回测段隐藏真实资金勾选、显区间/间隔字段；计划轮数越界禁用 CTA；回测确认调 `aiBacktestApi.start` 入参正确（`NODE_ENV=test`）。 |
| `apps/web/components/Chart/AiBacktestModal.test.tsx` | ✚ | 运行态 vs 完成态渲染；指标/AI 统计渲染；取消调用；决策日志流。 |

### 桌面 `desktop/`

| 文件 | 操作 | 一句话意图 |
|---|---|---|
| （无需改） | — | 回测无新增 env 总闸；`ai_backtest_*` 为服务端配置项，dev 读 `services/quant/.env`、prod 用默认即可，**不经 desktop 白名单**（不下真单、无需 `AI_TRADE_`/`LIVE_` 透传）。 |

---

## 四、Interface design（接口设计）

回测子路由**仅** `_require_agent_configured` 守门；**不**校验 `AI_TRADE_ENABLED`/`LIVE_TRADING_ENABLED`。错误体统一 `{error:{code,message}}`。

### 4.1 `POST /api/ai-trade/backtest/start`
```jsonc
// AiBacktestStartIn（金额/杠杆/OCO 可选，留空→保守默认）
{
  "instId": "BTC-USDT-SWAP",
  "bar": "1H",
  "strategyIds": [12, 7, 23],
  "startTs": 1743465600,             // 回测区间起（UTC 秒）
  "endTs":   1746057600,             // 回测区间止（UTC 秒）
  "decisionEveryBars": 1,            // AI 每 K 根决策一次；留空→config 默认(1)
  "initialCapital": null,            // 初始资金；留空→settings.initial_capital
  "leverage": null,                  // 留空→default_leverage
  "stopLossPct": null,               // 单仓 OCO 止损%（fraction）；留空→ai_trade_default_sl_pct
  "takeProfitPct": null,             // 单仓 OCO 止盈%
  "takeProfitUsd": null,             // 会话级止盈金额阈值；留空→不触发
  "stopLossUsd": null,               // 会话级止损金额阈值；留空→不触发
  "contextRefreshRounds": 10,        // ∈[min,max]
  "minConsensus": null               // 共识门槛；留空→ai_trade_min_consensus(0)
}
// → AiBacktestStartOut
{ "jobId": "aibt_9c2f…", "instId": "BTC-USDT-SWAP", "bar": "1H",
  "strategyIds": [12,7,23], "startTs": 1743465600, "endTs": 1746057600,
  "decisionEveryBars": 1, "plannedRounds": 720, "initialCapital": 10000, "leverage": 3 }
```
前置见 §2.9；任一不过回对应码：`AGENT_NOT_CONFIGURED`/`AI_TRADE_STRATEGIES_INVALID`/`AI_TRADE_CONTEXT_ROUNDS_INVALID`/`AI_BACKTEST_RANGE_INVALID`=400、`AI_BACKTEST_TOO_MANY_ROUNDS`=400（含 `plannedRounds/limit` 与指引）、`AI_BACKTEST_BUSY`=429。

### 4.2 `POST /api/ai-trade/backtest/{job_id}/cancel`
```jsonc
{ "jobId": "aibt_9c2f…", "state": "cancelled" }
```

### 4.3 `GET /api/ai-trade/backtest/{job_id}`
```jsonc
{ "job": {
    "jobId": "aibt_9c2f…", "state": "running",       // running|completed|cancelled|failed
    "phase": "running", "progress": 0.42,
    "barIndex": 305, "totalBars": 726, "decisionRound": 300, "plannedRounds": 720,
    "instId": "BTC-USDT-SWAP", "bar": "1H", "strategyIds": [12,7,23],
    "startTs": 1743465600, "endTs": 1746057600, "startedTs": 1751000000,
    "runId": null,                                    // 完成后为落库 run id
    "result": null } }                                // 完成后为 AiBacktestResultOut
```

### 4.4 `WS /api/ws/ai-backtest/{job_id}`（同信封 `{type,data}`，镜像 weight-train）
连接即原子「subscribe → 回放缓冲（run/phase/progress + 决策/摘要近 N 条 + 若已完成 result）→ 续订新事件」。事件 `type`：
- `run`/`snapshot`：`{ job: AiBacktestStatus, decisions: AiTradeDecision[], summaries: {round,ts,summary}[], equity: {ts,equity}[] }`（重连回放）。
- `phase`：`{ phase: "loading"|"priming"|"running"|"finalizing" }`。
- `progress`：`{ barIndex, totalBars, ts, lastPx, pct, decisionRound, plannedRounds, equity, positionSide, sessionPnlUsd }`（驱动进度头/滚动权益）。
- `decision`：与实盘决策事件**同构**（复用 `AiTradeDecisionOut` 形）：`{ ts, round, barIndex, action, sizeFraction, confidence, reason, signalBoard:[{strategyId,name,stance,latestSignal,strength,reason}], marketContext, consensus, degradeReason, executed:{ ok, side?, qty?, fillTsNext? } }`。
- `summary`：`{ ts, round, summary }`（每 `contextRefreshRounds` 轮）。
- `trade`：模拟成交 `Trade`（camelCase `TradeRecord`：`ts,side,price,qty,fee,pnl,positionAfter,reason,stopLossPrice,takeProfitPrice,...`）→ 图表 markers。
- `result`（**完成**）：`AiBacktestResultOut`（§5.1，含 metrics/aiStats/equity/trades/decisionsDigest/summaries/runId/notes）。
- `done`/`cancelled`/`error`/`ping`：生命周期（`error` 为脱敏失败提示，任务转 `failed` 并落 `finalize_run(status="failed")`）。

### 4.5 结果复看（复用既有 runs 路由，无新增）
落库后经 `GET /api/runs/{runId}`（`{id,strategyId,instId,bar,mode:"ai-backtest",status,startTs,endTs,metrics}`）、`GET /api/runs/{runId}/trades`、`GET /api/runs/{runId}/equity` 检索，可在「运行历史」复看 AI 回测结果（AI 专属统计存于 `metrics`）。

### 4.6 后端 → LLM（复用既有抽象，无新增上游端点）
决策/摘要经既有 `LLMClient.stream`（keyword-only）→ `resolve_active_provider` 解析的默认 provider，**零新增模型接入代码**；历史 K 线经 `agent/services.load_range_candles`（既有 OKX REST 分页），**零新增取数代码**。

---

## 五、Data model（数据模型）

### 5.1 后端 Pydantic（`schemas.py`，camelCase wire）
```python
class AiBacktestStartIn(BaseModel):
    instId: str = Field(min_length=1, max_length=32)
    bar: str = "1H"
    strategyIds: list[int] = Field(min_length=1)          # 上限按 ai_trade_max_strategies 服务端校验
    startTs: int = Field(gt=0)
    endTs: int = Field(gt=0)
    decisionEveryBars: int | None = Field(default=None, ge=1, le=1000)
    initialCapital: float | None = Field(default=None, gt=0)
    leverage: float | None = Field(default=None, gt=0, le=125)
    stopLossPct: float | None = Field(default=None, gt=0)     # 夹紧到 ai_trade_max_sl_pct
    takeProfitPct: float | None = Field(default=None, gt=0)   # 夹紧到 ai_trade_max_tp_pct
    takeProfitUsd: float | None = Field(default=None, gt=0)
    stopLossUsd: float | None = Field(default=None, gt=0)
    contextRefreshRounds: int = Field(default=10, ge=1)       # 精确区间服务端按 min/max 校验
    minConsensus: int | None = Field(default=None, ge=0)

class AiBacktestStartOut(BaseModel):
    jobId: str; instId: str; bar: str; strategyIds: list[int]
    startTs: int; endTs: int; decisionEveryBars: int
    plannedRounds: int; initialCapital: float; leverage: float

class AiBacktestAiStatsOut(BaseModel):
    decisionRounds: int
    actionCounts: dict                # {open_long,open_short,close,hold}
    holdRate: float; avgConfidence: float
    degradedRounds: int; timeoutRounds: int
    summaryCount: int; finalSummary: str | None
    decisionEveryBars: int; plannedRounds: int; reachedRoundCap: bool

class AiBacktestResultOut(BaseModel):
    runId: int | None
    metrics: dict                     # compute_metrics 七项
    aiStats: AiBacktestAiStatsOut
    equity: list[dict]                # [{ts,equity,drawdown}]
    trades: list[dict]                # camelCase TradeRecord
    decisionsDigest: list[dict]       # 近 N 条决策（时间线复看）
    summaries: list[dict]             # [{round,ts,summary}]
    startTs: int; endTs: int; barsProcessed: int; notes: list[str]

class AiBacktestStatusOut(BaseModel):
    jobId: str; state: str; phase: str; progress: float
    barIndex: int; totalBars: int; decisionRound: int; plannedRounds: int
    instId: str; bar: str; strategyIds: list[int]
    startTs: int; endTs: int; startedTs: int
    runId: int | None; result: AiBacktestResultOut | None
```

### 5.2 会话内存形态（`ai_auto/backtest.py`）
```text
AiBacktestRunner:
  job_id, inst_id, bar, strategy_ids, strategy_names
  specs: list[PaperStrategySpec]        # build_paper_strategy_spec 逐策略（复用）
  dfs: list[pd.DataFrame]               # 每策略全区间 prepare-once 帧（backtest_frames.build_backtest_frames；R3，不用 MarketWindow）
  shadows: list[SimBroker]              # 每策略影子（读持久立场，绝不下单；复用 make_shadow_broker）
  main: SimBroker                       # 模拟主账户（决策落地；set_risk=单仓 OCO）
  decider: AiTradeDecider               # 复用（同款 system_prompt + 滚动摘要）
  settings, decision_every_bars, context_refresh_rounds, min_consensus
  initial_capital, leverage, take_profit_usd, stop_loss_usd
  rounds_done, action_counts, degraded_rounds, timeout_rounds, summary_count
_BacktestJob:
  job_id, runner, state                 # running|completed|cancelled|failed
  phase, progress, bar_index, total_bars, decision_round, planned_rounds
  _buffer: deque(事件环形≤上限)          # 回放
  _subscribers: set[asyncio.Queue]
  _cancel: asyncio.Event
  run_id|None, result|None
```

### 5.3 持久化（无新表、无迁移）
- **Run**：`repo.create_run(session, strategy_id=strategyIds[0]（主策略占位，**禁 None**——`db/models.py:49` 为 NOT NULL FK）, inst_id=inst_id, bar=bar, mode="ai-backtest", config={strategyIds,startTs,endTs,decisionEveryBars,contextRefreshRounds,initialCapital,leverage,stopLossPct,takeProfitPct,takeProfitUsd,stopLossUsd,minConsensus})`（`mode` 自由字符串列 `String(16)`，已核验无 enum/CHECK、无迁移；R2）。
- **结果**：跑完 `repo.persist_backtest_result(session, run, trades=main.trades, equity=main.equity_points, metrics={**compute_metrics, "aiStats": aiStats, "finalSummary": …, "decisionsDigest": […≤200]}, start_ts=startTs, end_ts=endTs, status="completed"|"cancelled"|"failed")`（**取 `run` 对象 + `start_ts`/`end_ts`，非 `run_id`**；取消/失败可 `repo.finalize_run(session, run.id, status=…)`）。trades/equity 走既有 `/runs/{id}/trades`、`/runs/{id}/equity` 复看。
- **前端**：`aiBacktestJobId` 瞬态不持久化（断线经 `GET /ai-trade/backtest/{id}` 重发现）；可选偏好 `aiBacktestConfig{decisionEveryBars,contextRefreshRounds}` 进 `partialize`。

### 5.4 store 增量（`lib/store.ts`）
```ts
// 瞬态（不入 partialize，刷新即复位，GET 重发现）
aiBacktestJobId: string | null;
// 可选持久化偏好（进 partialize；无需升版，v25 承载）
aiBacktestConfig: { decisionEveryBars: number; contextRefreshRounds: number } | null;
```

---

## 六、错误码归一（复用既有 `{error:{code,message}}`；R8：`_map_trade_code` 位于 `okx/trading.py` 非 ai_auto 层，回测新码在 `api/ai_auto.py` 直接构造 `detail={code,message}`）

| 码 | HTTP | 语义 / 中文提示 |
|---|---|---|
| `AGENT_NOT_CONFIGURED` | 400 | 未配置 AI 模型，无法回测决策（请在设置中心配置默认模型；复用码） |
| `AI_TRADE_STRATEGIES_INVALID` | 400 | 策略选择非法（数量越界或存在不可实例化策略；复用码） |
| `AI_TRADE_CONTEXT_ROUNDS_INVALID` | 400 | 上下文刷新轮数越界（复用码） |
| `AI_BACKTEST_RANGE_INVALID` | 400 | 回测区间非法（起止顺序 / 未来时间） |
| `AI_BACKTEST_TOO_MANY_ROUNDS` | 400 | 计划决策轮数超上限（含 `plannedRounds/limit`；请缩短区间或增大决策间隔） |
| `AI_BACKTEST_BUSY` | 429 | 同时进行的 AI 回测数已达上限（`ai_backtest_max_jobs`，请稍后或取消其它回测） |
| `EMPTY_RANGE` | 400 | 区间内无已确认 K 线（复用 `load_range_candles` 码） |
| `AI_BACKTEST_JOB_NOT_FOUND` | 404 | 无此回测任务（status/cancel/WS 时） |

---

## 七、Testing & acceptance criteria（测试与验收）

### 7.1 后端单测（离线 pytest，CI 必跑；OKX/LLM 全桩，绝不写真实凭证）
- `test_ai_backtest_runner.py`（核心）：桩 `LLMClient` 产 `tool_use_start/args_delta/message_done` 脚本化决策序列；合成/replay 行情下——**暖机段不决策、主账户恒 flat**；决策根**对齐 `decisionEveryBars`**（`K=2/3` 时每隔 K 根一次）；决策→`main.submit`→**次根开盘成交**（断言 trades 时序与 pnl、equity 逐根）；`compute_metrics` 七项与 `aiStats`（actionCounts/holdRate/avgConfidence/summaryCount）正确；**共识门禁把 open_* 降级 hold** 时 `degradedRounds++` 且 `commit_round`/journal 记的是**降级后的 hold**（不「记住从未开的仓」）；LLM 超时→hold 且 `timeoutRounds++`；每 `contextRefreshRounds` 轮推一次 `summary` 且 `_context` 被摘要种子重置；**到 `max_rounds` 停决策、续逐根盯市到区间末、`result.notes` 标注**；**取消 mid-run**→`state="cancelled"`、已成交部分 finalize、落 `status="cancelled"`。
- `test_ai_backtest_manager.py`：并发闸（第 `max_jobs+1` 个 `start`→`AI_BACKTEST_BUSY`）；`get`/`cancel`；`subscribe` **回放**（订阅即收缓冲的 phase/progress/决策，完成后收 result）。
- `test_ai_backtest_api.py`：`ASGITransport`+桩——Agent 未配置→400；区间非法/轮数越界（含指引）/策略越界→400；start→GET status→cancel 生命周期；WS 连接原子 `subscribe→snapshot`；**`AI_TRADE_ENABLED=false` 时回测仍可启动**（回测不校验双闸）。
- `test_ai_trade_signals.py`（补/回归）：`apply_consensus_gate`/`decision_to_signals` 上移后纯函数单测；`test_ai_trade_session.py` 回归 session 转调后行为逐字不变。

### 7.2 前端单测（vitest，`NODE_ENV=test`，CI 必跑）
- `aiBacktest.test.ts`：`planRounds`（区间/间隔→轮数）、`validateBacktestRange`、`describeBacktestPhase`、`actionDistribution`、进度% 边界。
- `AiTradeLaunchModal.test.tsx`（补）：切回测段→隐藏真实资金勾选、显区间/间隔/初始资金字段；计划轮数越界→CTA 禁用；回测确认→`aiBacktestApi.start` 入参正确；实盘段门禁不受影响。
- `AiBacktestModal.test.tsx`：运行态（进度头 + 决策日志流 + 取消）vs 完成态（指标卡 + AI 统计 + 成交表）渲染；取消调用 `aiBacktestApi.cancel`。

### 7.3 端到端联调（opt-in，手动，不进 CI；无真实资金风险）
按 README「真实数据硬约束 + 代理」前置，配置一个默认 LLM 模型（回测**不需**实盘账户/`AI_TRADE_ENABLED`）：
1. 图表选 `BTC-USDT-SWAP`、`1H`；开 AI 弹窗切「回测」，选 2~3 策略、区间近 30 天、`decisionEveryBars=4`（控成本）、小 `contextRefreshRounds`（如 5）→「开始 AI 回测」。
2. 观察：`AiBacktestModal` 进度头逐步推进（loading→priming→running）、决策日志流逐轮出（动作/理由/体制/共识/成交）、每 5 轮出一条 `summary`；随后 finalizing→完成态出指标卡 + AI 统计 + 权益 K 线回放 + 成交表 + 决策/摘要时间线。
3. 取消：运行中点「取消」→任务转 `cancelled`、已成交部分入结果、可复看。
4. 越界：设超长区间 + 间隔 1 使 `plannedRounds>max_rounds`→启动即拒并给指引；缩短或增大间隔后可启动。
5. 复看：在「运行历史」找到 `mode="ai-backtest"` 的 run，`/runs/{id}` 指标与面板一致。
6. 双闸无关性：`AI_TRADE_ENABLED=false` 下回测仍可跑（回测无真单）。

### 7.4 Definition of Done（本设计交付物）
- 本 `spec.md` 已建并见于 `git status`；含 Overview / Technical design / File change plan / Interface design / Data model / Testing & acceptance / Risks 全部小节，篇幅 ≥800 字。
- 文件/模块变更计划逐项可被下游工程师按行实现，无需再决策。

---

## 八、Risks & mitigations（风险与缓解）

| 风险 | 影响 | 缓解 |
|---|---|---|
| **每根一次 LLM，长回测成本高 / 时长长** | 成本、体验 | **决策间隔** `decisionEveryBars`（AI 降频、盯市不降频）+ **决策轮数硬上限** `ai_backtest_max_rounds`（越界启动前拒绝并指引，绝不静默截断）+ **并发闸** `ai_backtest_max_jobs`；流式进度 + 随时取消；单决策 `ai_trade_decision_timeout_s` 超时回落 hold。 |
| **AI 决策非确定性 → 回测不可字节复现**（不同于策略回测） | 复现性 | 明确记录：**SimBroker 执行确定**，仅 LLM 决策随机（provider 无统一 seed/temperature 控制）；单测用**桩 LLM** 保证确定；真 LLM 回测定位为「代表性预演」，非可复现基准；结果落 Run 保留当次全量。 |
| **误碰实盘下单路径** | 资损 | **零真单面**：回测走独立 `ai_auto/backtest.py`，**不改** `session.py`/`manager.py`/`live_auto/executor.py`（唯一改动为 §2.8 两纯函数搬迁 + 引用，行为不变，回归测试守住）；执行落地是 `SimBroker`（模拟）；回测**不校验、也不触碰** AI/LIVE 双闸与真实账户。 |
| **未来函数（look-ahead）污染回测** | 结果虚高 | 复用 `SimBroker` 的**次根开盘成交**（`fill_pending`，无未来函数）+ `i<n-1` 不在末根开仓；`build_market_context`/`classify_regime` 严格因果、单点判定（`min_bars=60`）；信号板每策略影子回放严格逐根因果；口径与既有策略/纸面回测一致、可比。 |
| **误用 live `MarketWindow` 取 HTF 致回测未来函数/错时污染**（R3） | 结果失真 | 回测**不复用** `MarketWindow`（滚动窗口 + 实盘 REST 取近端 HTF）；改 `ai_auto/backtest_frames.build_backtest_frames`：全区间 `load_htf_frames`（历史区间）+ prepare-once 建帧、按绝对 `i` 索引（§2.2）。 |
| **异步运行器阻塞共享事件循环**（R4） | 拖慢其它 WS/请求 | 重同步 setup（取数/建帧/暖机）经 `run_in_threadpool`/`run_in_executor` 卸载；逐根循环周期 `await asyncio.sleep(0)` 让出；仅 LLM `await` 内联（§2.2/§2.6）。 |
| **`strategy_id` 列 NOT NULL，`strategy_id=None` 落库崩溃**（R2） | 无法落库 | 用主策略占位 `strategyIds[0]`，完整策略集存 `config.strategyIds`（§2.7/§5.3）。 |
| **决策轮数上限被静默触达致回测「缩水」** | 误读结果 | 到 `max_rounds` **停决策但续逐根盯市到区间末**，并在 `result.notes`/前端明示「已达决策上限、末段仅盯市」；启动前 `plannedRounds` 预览 + 越界拒绝。 |
| **长回测决策/权益数组撑内存** | 稳定性 | 事件缓冲**环形**按上限保留（决策近 200、进度留最新）；全量走**落库**（trades/equity）与完成 `result`；前端完成态从 `result`/`/runs` 渲染。 |
| **前端重载致 WS 孤儿** | UI 与任务脱节 | `aiBacktestJobId` 瞬态；`AiBacktestModal` 挂载 `GET /ai-trade/backtest/{id}` 重发现 + 重连 WS 回放；任务独立于 socket 存活（镜像 weight-train）。 |
| **上下文摘要在回测中丢关键信息** | 决策质量 | 复用增强包 §2.4 已加固的「以完整 `_journal` 为素材、与模型窗口 40 条解耦」摘要；摘要随 WS 下发，时间线可人工审阅；摘要失败不崩任务（保留上一段）。 |
| **共识门禁降级后摘要污染上下文** | 决策漂移 | 复用增强包 R-P1-3 修复：`commit_round` 收**门禁后**最终决策，journal/摘要记录真实动作（含 `degradeReason`），杜绝「记住从未开的仓」。 |
| **进程退出残留在跑回测** | 悬挂任务 | `main.py` lifespan `get_ai_backtest_manager().shutdown()` 取消所有在跑 job、落 `failed`。 |
| **回测把标的/区间取错致空** | 无结果 | `load_range_candles` 对空区间 400 `EMPTY_RANGE`；弹窗二次校验区间与标的（`isAutoTradable`）。 |

---

## 九、实施顺序建议（供 Subtask #2 参考）

1. **小重构先行（R1）**：`ai_auto/signals.py` 新增模块级 `apply_consensus_gate(…, min_consensus)` 与**纯核心** `decision_to_signals(decision)`；`session.py` 的 `_apply_consensus_gate` 改薄封装、`_decision_to_signals` 改「调纯核心 + 补 `_ai_explain`」，回归 `test_ai_trade_session.py`/`test_ai_trade_signals.py`（对外行为逐字不变）。
2. **后端纯/编排件**：`config.py` 三项 + `schemas.py` 契约；`ai_auto/backtest_frames.py`（全区间 prepare-once 建帧，R3）+ `ai_auto/backtest.py`（`AiBacktestRunner` 逐根循环 + `_BacktestJob`；重同步件经 `run_in_threadpool` 卸载，R4）+ `ai_auto/backtest_manager.py`（并发闸 + 前置门禁 + 生命周期）+ `test_ai_backtest_runner.py`/`test_ai_backtest_manager.py`（桩 LLM，复用 `test_ai_trade_decider.py` 的桩流范式）。落库经 `db/repositories.py` 的 `repo.create_run`/`persist_backtest_result`（`strategy_id` 占位、`run` 对象 + start/end，R2）。
3. **API 接线**：`api/ai_auto.py` 回测子路由（start/cancel/status/WS，`_require_agent_configured`）+ `main.py` shutdown 挂钩 + `test_ai_backtest_api.py`。
4. **前端纯件**：`lib/aiBacktestTypes.ts`/`aiBacktestApi.ts`/`aiBacktest.ts` + `aiBacktest.test.ts`。
5. **UI**：`AiTradeLaunchModal` 加模式段控 + 回测字段；`AiTradeToggle` 放宽门禁 + 回测分派；`AiBacktestModal`（运行态/完成态）+ `useAiBacktestJob`（page.tsx）+ store 瞬态/偏好 + 组件测试（复用 `MetricsCards`/`TradesTable`/`WeightBacktestChart`/`EquityChart` 视觉）。
6. opt-in 联调（§7.3，短区间 + 大间隔控成本），通过后收尾。

---

## 评审结论（Review Verdict · v2）

**结论：有条件通过（Approved with conditions）。**

设计根基成立、方向正确、复用锚点绝大多数经代码核验属实，核心命题（决策脑零改动复用 + 执行落地换 `SimBroker` + 数据源换历史区间 + 决策间隔/轮数上限/并发闸三重成本护栏 + 流式可观测/可取消/可复看）**可落地**。评审发现的 **6 项 P1 + 6 项 P2 已在本 v2 正文逐一修正**（见 `## 评审记录` 与各 §），**无 P0**、无遗留 P1。放行下游 **Subtask #2（代码开发）**，须在实现时**逐条落实**以下由评审固化的约束（否则回归到 v1 的隐患）：

1. **§2.8 重构按 R1 修正版实现**：`decision_to_signals` 只抽**纯核心** `Signal` 映射；`apply_consensus_gate` 的 `min_consensus` **显式入参**；session 的 `_decision_to_signals` 保留 `board_digest`/`_ai_explain` 富化（调纯核心后再富化）。**门禁**：`test_ai_trade_session.py`/`test_ai_trade_signals.py` 必须证明实盘链路（含 `explain` 富化）**逐字不变**。
2. **落库按真实 `repo` 签名（R2）**：`create_run`/`persist_backtest_result`/`finalize_run` 在 `db/repositories.py`；`strategy_id` 用 `strategyIds[0]` 占位（**严禁 None**，列 NOT NULL）；`persist_backtest_result` 传 **`run` 对象 + `start_ts`/`end_ts`**。
3. **逐策略帧全区间 prepare-once（R3）**：新增 `ai_auto/backtest_frames.py`，HTF 走 `load_htf_frames`（**历史区间**）；**禁止**在回测路径复用 `MarketWindow`（滚动窗口 + 实盘 REST HTF = 未来函数）。**门禁**：回测 runner 单测须断言无未来函数（决策仅依赖 `≤ i` 的数据、成交在 `i+1` 开盘）。
4. **事件循环不被阻塞（R4）**：重同步 setup/暖机经 `run_in_threadpool`/`run_in_executor` 卸载，逐根循环周期 `await asyncio.sleep(0)`；仅 LLM `await` 内联。
5. **默认成本安全（R5）**：弹窗决策间隔 UI 默认按区间取 `max(config默认, ceil(tradableBars/max_rounds))`，默认配置永不因越界自拒。
6. **前端门禁独立于 `resolveAiTradeGate`（R6）**：回测门禁仅校验「Agent 已配置」，回测 `ready` **去 `acknowledged`**（非仅隐藏 UI）。
7. **P2 项（R7–R12）**已在正文修正，实现时对齐：`context()` 无未实现盈亏字段（盯市用 `equity_at(close)`）、`_map_trade_code` 在 `okx/trading.py`、有界环形缓冲的重连语义、`plannedRounds` 归一式、会话止盈损收尾语义、`ai_auto/backtest.py` 按职责拆分控 ≤800 行。

下游若对上述任一约束有偏离，须回评审节点复核，勿在代码节点自行放宽。

---

> 版本：**v2**（iteration 2 · 方案设计 → 评审加固） · 复用基线：`ai_auto/`（decider/signals/prompts/market_context 决策脑，零改动）、`engine/`（simulator/backtester/metrics 回测设施，零改动）、`agent/services`（load_range_candles/落库，复用）、`factors/weight_train_manager`（流式长任务范式，镜像）。核心设计：**决策脑不变、执行落地从 `LiveExecutor` 真单换为 `SimBroker` 模拟、数据源从实时 WS 换为历史区间**，把「AI 综合研判多策略」这一自动交易类型带上「无真实资金、可观测、可取消、可复看」的回测能力，并以决策间隔 / 轮数上限 / 并发闸三重护栏控住 LLM 成本。
