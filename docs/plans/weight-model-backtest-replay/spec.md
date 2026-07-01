# 权重模型「一键回测 + K 线回测回放」方案（weight-model-backtest-replay）

> 版本：v2（评审修复）
> 所属迭代：权重模型工作区 第 2 轮
> 状态：已评审（Subtask #1 就地评审并修复 P1，出 v2；评审记录见下、评审结论见文末）
> 关联前序：`docs/plans/weight-model-workspace/spec.md`、`-hardening`、`-refinement`

---

## 评审记录（Review Notes，v2）

> 评审人：Anthropic 资深工程评审（Subtask #1）。评审维度：**可行性 / 完整性 / 一致性 / 合理规模**。
> 评审方法：对 spec 中每一处「复用点 / 函数签名 / 字段名 / 路由」claim，均**并行对照现网代码逐条核验**（后端 `services/quant`、前端 `apps/web`、数据模型三路）。
> 结论速览：**无 P0**；3 项 P1 已在本 v2 就地修复；其余为 P2 实施须知（详见文末《评审结论》放行条件）。整体判断——「复用 `run_backtest` + `factor_score`、零 schema 迁移、纯加法」经核验**成立**，方案可行、规模得当、与现有约定一致。

### P1（已在本 v2 就地修复）

- **P1-1 ｜可行性 ｜ slim 图表必须把设计令牌解析为具体色值。** lightweight-charts **v4**（`apps/web/package.json` 锁 `^4.2.0`）不接受 `var(--long)` 之类 CSS 变量，蜡烛 / markers / 净值线颜色须传具体 hex/rgb。原 §2.3 与文件清单未提及 `WeightBacktestChart` 需要一个本地 `cssVar()`（`getComputedStyle` 读令牌）助手，并在 `theme` 变化时重解析建图。→ 已在 §2.3、§六 UI 段、文件清单补明（参照 `CandleChart` 私有 `cssVar` 同款做法）。
- **P1-2 ｜完整性 ｜回测面板真正的渲染点是 `RunReplay` 子组件，接线未写全。** 现网 `WeightModelDetail.RunReplay` 仅接收 `{modelId, runId}`，且已在作用域持有完整 `data`（含 `status`/`weights`）。原文件清单只写了 `WeightModelDetail` 改动，漏写 `RunReplay` 需新增 `autoBacktestRunId` 入参、用 `data` 走 `runIsBacktestable(data)` 门禁、以及 `selectedRunId` 由「以 `autoBacktestRunId` 为依赖的 effect」播种（注意 `WeightModelPanel` 现有聚焦 effect 仅依赖 `view.kind`，不可复用其触发时机）。→ 已在 §2.3、文件清单补全。
- **P1-3 ｜合理规模/DRY ｜ `_bt_equity_out` 与现有 `equity_out` 重复。** `schemas.equity_out`（`schemas.py:437`）已存在且**已被复用于内存态 `EquityRecord`**（`agent/services.py:452`），再造 `_bt_equity_out` 冗余且易与既有口径漂移。`trade_out`（`schemas.py:425`）因读取 ORM 独有的 `id`/`run_id`/`explain_json`，**不能**直接套用内存态 `TradeRecord`，故 `_bt_trade_out` 保留（镜像 `trade_out` 去掉三字段）；`candle_out` 现网无命名助手，`_bt_candle_out` 保留。→ 已把「新增 `_bt_equity_out`」改为「复用 `equity_out`」。

### P2（实施须知，已就地校正措辞 / 或列为放行条件）

- **P2-1 ｜签名精度：** §2.2 时序伪代码中 `load_range_candles` 实为 `async` 且 `rest` 之后**全关键字**（`max_bars=` 可选）；`repo.get_weight_model` / `get_weight_run` 首参为 `session`；`run_backtest` 成本参数为关键字。→ 已就地校正伪代码，勿逐字实现。
- **P2-2 ｜ `BacktestResult` 实为 5 字段**（多 `start_ts`/`end_ts`），序列化器有意只取 `trades`/`equity_points`/`metrics`。→ 已在 §2.2 要点注明。
- **P2-3 ｜「与 `resolve_set_to_spec` 同构」措辞过强：** 现网 `resolve_set_to_spec` 产出 **4 键** `{members,thresholds,normStats,provenance}`；本方案手装 `params` 仅 3 键，`provenance` 为纯 UI、不参与打分，**有意省略**。→ 已在 §五注明。
- **P2-4 ｜ step-4 的 `normWindow` 对齐是冗余保险：** `resolve_norm_contract`（`scoring.py:62`）本就优先 `normStats.normWindow`，覆盖 `thresholds.normWindow` 仅让两份表征自洽，非正确性必需。→ 已在 §2.2 要点注明。
- **P2-5 ｜ 413 复用了训练错误码：** 错误表把 `FACTOR_TRAIN_TOO_LARGE`（训练守门）挪用于回测，语义不符；且 `maxBars` 既已被 `settings.backtest_max_bars`（默认 20000）钳制，413 行自相矛盾。→ 已改为「静默钳制、删除 413 训练码行」。
- **P2-6 ｜ `MetricsCards` 只渲染 6/7 键**（不显示 `avgPnl`），但 `Metrics` TS 接口要求 7 键、序列化器仍须输出 `avgPnl`（示例 JSON 已含），无缺陷；另 `MetricsCards`/`TradesTable` 均有 `loading?` 入参，面板可直接借力做骨架态。
- **P2-7 ｜ `runIsBacktestable` 应复用现有 `runToTrained` 守卫**（`weightModel.ts:311` 已是 `status==="succeeded" && run.weights && Object.keys(weights).length>0` 同款判据，含 `!run.weights` 防御），避免双份判据漂移。→ 已在文件清单注明。
- **P2-8 ｜ `markerForEvent` 本地复刻实为 ~40 行**（依赖私有 `cssVar`、`shortStrategyLabel` 与已导出的 `tradeToneForEvent`），非「~15 行」；单模型回测图可**省去 per-strategy 的 `shortStrategyLabel`**、复用已导出的 `tradeToneForEvent`。→ 已在 §2.3 校正。
- **P2-9 ｜极旧无 `normStats` 记录的回落：** 若某 succeeded run 的 `norm_stats_json` 为空，装配出的 `normStats=None` 会让 `resolve_norm_contract` 回落 `thresholds.normWindow`（打分或与训练期不同）。现网工作区所有训练均落 `normStats`，风险极低；实施时补一条保守守门（`normStats` 缺失 ⇒ 409 不可回测）或至少注释。
- **P2-10 ｜测试落点：** 路由须留在 `api/weight_models.py`，现有 `patch_okx` fixture 已 monkeypatch `app.api.weight_models.get_rest_client`，无需新落点；新回测测试须遵循**共享进程 DB**纪律（按返回 id 断言、勿断言全局计数，见 [[aragon-quant-test-isolation]]）。

---

## 一、概述（Overview）

第 1 轮已经交付了完整的「权重模型」工作区：左侧功能列表新增「权重模型」类别，右侧可对模型增删改查、点进任一模型查看历史训练记录，并可实时流式观测每次训练的 loss / IC 学习曲线与权重收敛（`WeightModelPanel` → `WeightModelList` / `WeightModelDetail` / `WeightTrainLive` / `WeightModelEditor`）。训练产物（归一化权重 `weights` + 归一化契约 `normStats{normWindow,clip}` + 指标 `metrics`）已随每条 `WeightTrainRun` 持久化，且与「权重训练与组合」搭建台（`FactorSetBuilder`）双向打通。

本轮（第 2 轮）在此之上补齐**「训练完就能立刻回测、并在同一面板里看到 K 线级别的买卖回放」**这一闭环。具体目标：

1. **卡片列表可直接回测**：`WeightModelList` 的每张模型卡片新增「回测」按钮。当模型已完成至少一次成功训练（`latestRunId != null`）时按钮可用，点击后进入该模型详情并自动对**最近一次成功训练**（`latestRun`）的权重发起回测。
2. **详情页「学习曲线回放」下方展示 K 线回测**：在 `WeightModelDetail` 的 `RunReplay`（即「学习曲线回放」卡片所在区域）下方，新增「K 线回测」区块。回测完成后展示：一张带**买卖 markers 与成交连线**的蜡烛图、叠加的**净值/收益率曲线**、一组**统计信息卡片**（总收益率 / 胜率 / 最大回撤 / 夏普 / 盈亏比 / 成交笔数），以及一张**逐笔成交明细表**。

关键的架构判断：权重模型的一条成功训练记录本质上就是一份「成员 + 训练权重 + 归一化契约 + 阈值」的自包含快照，这与既有 `factor_score`（因子组合得分）策略消费的内联快照**完全同构**。因此本轮**不新增回测引擎、不新增策略类型、不改动 `run_backtest` / 打分函数**，而是把训练记录组装成 `factor_score` 快照后复用既有回测链路（`run_backtest` → `SimBroker` → `compute_metrics`）。这样既严格保持了「训练期与回测期共用同一因果打分」的既有不变量，又把新增代码收敛为「一段装配 + 一个只读回测端点 + 一组前端展示组件」。

回测采用**同步、一次性、不落库**的形态：后端即时算完直接返回 `{candles, trades, equity, metrics}` 打包体，不写 `strategy_runs / trades / equity_points` 表、不建新表、零 schema 迁移（延续第 1 轮「纯加法、零迁移」的工程约束）。前端用 TanStack Query 以 `(modelId, runId, range)` 为键缓存结果，实现「本会话内切走再切回即时可见」的记录体验。

---

## 二、技术设计（Technical Design）

### 2.1 复用点盘查（结论先行）

| 能力 | 复用对象 | 位置 | 耦合度 |
|---|---|---|---|
| 装配自包含快照（烘焙权重） | `apply_weights` / `normalize_thresholds` / `assert_weights_cover` | `services/quant/app/factors/specs.py` | 纯函数，直接复用 |
| 因果打分 → 多空信号 | `FactorScoreStrategy`（`factor_score`） | `services/quant/app/strategies/factor_score_strategy.py` | 只读 params，直接实例化 |
| 真实区间 K 线分页加载 | `load_range_candles` | `services/quant/app/agent/services.py:81` | 直接复用（`rest` 注入） |
| 成本解析 | `_resolve_costs` | `agent/services.py:139` | 直接复用 |
| 单策略回测执行 | `run_backtest` → `BacktestResult{trades, equity_points, metrics}` | `engine/backtester.py:66` | 直接复用（`htf_frames={}`） |
| 蜡烛图 / markers / 成交连线 | `createChart`/`addCandlestickSeries`/`setMarkers` + `buildTradeMarkerEvents` / `buildTradeTrailSegments` | `apps/web/lib/tradeTrails.ts`（已导出） | 在新 slim 组件里复用助手 |
| 净值曲线 | `addLineSeries`（左价轴，`--accent`） | 仿 `CandleChart` 净值叠加 | 内嵌于新组件 |
| 指标卡片 | `MetricsCards`（`metrics: Metrics`） | `apps/web/components/Strategy/MetricsCards.tsx` | 纯 props，原样复用 |
| 成交明细表 | `TradesTable`（`trades: Trade[]`） | `apps/web/components/Strategy/TradesTable.tsx` | 纯 props，原样复用 |
| 领域类型 | `Candle` / `Trade` / `EquityPoint` / `Metrics` | `apps/web/lib/types.ts` | 直接 import |

> 注：`_factor_score_template_strategy_id`（`agent/services.py:549`）当前**无调用方**（预留/死代码），且因子路由仅有 `score-preview`（只出打分点，不出 trades/equity），**不存在**现成的「内联因子快照回测」端点——故本轮新增一段不落库的装配 + 回测编排。

### 2.2 后端数据流（Sequence）

```
前端 POST /api/weight-models/{modelId}/runs/{runId}/backtest  {startTs?, endTs?, initialCapital?, fees?, slippage?, leverage?, maxBars?}
  └─ api/weight_models.py: backtest_weight_run
      └─ weight_model_service.backtest_run(session, rest, modelId, runId, ...)
          1. repo.get_weight_model(session, modelId)          → None ⇒ 404 NOT_FOUND
          2. repo.get_weight_run(session, modelId, runId)     → None ⇒ 404 NOT_FOUND   # 首参 session
          3. 守门：run.status != "succeeded" 或 weights 为空 ⇒ 409 WEIGHT_RUN_NOT_BACKTESTABLE
                    （建议再判 norm_stats 为空亦 409，见评审 P2-9）
          4. 组装快照：
             members    = model.members_json
             weights    = run.weights_json
             normStats  = run.norm_stats_json                 # {normWindow, clip}
             thresholds = normalize_thresholds(model.config.thresholds)  # config.thresholds 可缺，normalize(None)=纯默认
                          令 thresholds.normWindow = normStats.normWindow # 冗余保险（自洽），非正确性必需，见要点
             assert_weights_cover(members, {"weights": weights})   # 成员漂移 ⇒ 400 FACTOR_WEIGHTS_STALE
             baked      = apply_weights(members, weights)          # 权重烘焙进各成员 weight
             params     = {"members": baked, "thresholds": thresholds, "normStats": normStats}
          5. 区间：startTs/endTs 缺省回落 run.start_ts / run.end_ts（训练区间，样本内，确定性可复现）
          6. services.run_factor_score_backtest(session, rest, params=params, inst_id=run.inst_id, bar=run.bar,
                 start_ts=..., end_ts=..., costs..., limit=maxBars)
               ├─ (initial_capital, taker_fee_bps, slippage_bps, leverage) = _resolve_costs(settings, ...)
               ├─ strategy = get_strategy_class("factor_score")({**default_params, **params})   # 不建策略行
               ├─ candles  = await load_range_candles(rest, inst_id=..., bar=..., start_ts=..., end_ts=..., max_bars=...)
               │                                                # async + 全关键字；空 ⇒ 400 EMPTY_RANGE（透传）
               ├─ await repo.upsert_candles(...)                # 仅暖进程内 K 线缓存（与既有回测一致）
               └─ result   = await run_in_threadpool(                             # BacktestResult 实有 5 字段
                                 lambda: run_backtest(strategy, candles, bar,     #   (trades,equity_points,metrics,start_ts,end_ts)
                                     initial_capital=initial_capital, taker_fee_bps=taker_fee_bps,
                                     slippage_bps=slippage_bps, leverage=leverage, htf_frames={}))
          7. return weight_backtest_out(candles, result, meta={instId,bar,startTs,endTs,horizon,effectiveCandleCount,initialCapital})
  ← 200 {candles[], trades[], equity[], metrics{}, meta{}}
```

要点：
- **不落库**：不调用 `repo.create_run` / `persist_backtest_result`，不产生 `runId`，也不污染 seed 的 `factor_score` builtin 策略的运行历史（该 builtin 对用户在策略面板可见，若把权重模型回测挂到它名下会造成困惑）。
- **不改打分**：`FactorScoreStrategy.prepare` 优先用快照 `normStats` 走 `resolve_norm_contract`（训练窗口锁定），与训练期字节级一致；`on_candle` 按 `thresholds` 产出 long/short/close，`SimBroker` 撮合成 `TradeRecord`。
- **快照为新式、守住后向兼容（R-P0-1）**：装配出的 `params` 是**新式快照**——权重已由 `apply_weights` 烘焙进各成员 `weight`、**不带顶层 `weights`**，与 `resolve_set_to_spec` 同形，故 `factor_score` 打分正常。**绝不可**因此删除 `FactorScoreStrategy.prepare` 中 `self.params.get("weights")` 的旧式顶层读取路径（否则旧快照全 NaN 静默停单）。step-4 的 `thresholds.normWindow=normStats.normWindow` 对齐仅为两份表征自洽——`resolve_norm_contract`（`scoring.py:62`）本就优先 `normStats.normWindow`，对齐非正确性必需（见评审 P2-4）。序列化只读 `BacktestResult` 的 `trades`/`equity_points`/`metrics` 三字段（其另有 `start_ts`/`end_ts` 忽略即可，P2-2）。
- **诚实报错**：区间无已确认行情 → 透传 `EMPTY_RANGE`（400）；OKX 不可达 → 既有 `OKXError` 归一化处理器兜底；绝不假数据、绝不静默空仓掩盖配置错误（成员漂移在边界即 400）。
- **循环依赖规避**：`weight_model_service` 调 `agent.services.run_factor_score_backtest` 采用**函数内局部 import**（`from ..agent import services as bt_services`），避免装载期环（`agent.services` 不反向依赖 `weight_model_service`，方向安全，但仍以局部 import 加固，仿 `factor_score_strategy` 延迟 import 破环的既有做法）。

### 2.3 前端数据流

```
[卡片列表] WeightModelList.ModelCard「回测」(仅 canBacktestModel(model)，即 latestRunId!=null 可用)
   → onBacktest(model)
   → WeightModelPanel.setView({kind:"detail", modelId, autoBacktestRunId: model.latestRunId ?? undefined})
        · （注：Panel 现有聚焦 effect 仅依赖 view.kind，不承担触发回测之责）
   → WeightModelDetail(autoBacktestRunId)
        · effect(依赖 [autoBacktestRunId]): autoBacktestRunId!=null 时 setSelectedRunId(autoBacktestRunId)
        · <RunReplay modelId runId={selectedRunId} autoBacktestRunId/>   ← RunReplay 新增入参 autoBacktestRunId
             · RunReplay 已在作用域持有 data（完整 run，含 status/weights）
             · 「学习曲线回放」卡 → 「训练权重」卡 →
               【新】runIsBacktestable(data) && <WeightBacktestPanel modelId runId autoRun={runId===autoBacktestRunId}/>
                    · useQuery(["weight-models",modelId,"runs",runId,"backtest",{range}], enabled=已触发, staleTime 长)
                       └─ weightModelApi.backtest(modelId, runId, body)
                    · 成功：MetricsCards(metrics) + <WeightBacktestChart candles trades equity theme/> + TradesTable(trades)
                    · autoRun（来自卡片「回测」）：挂载即置 enabled=true 自动发起，仅消费一次
                      （列表→详情为全新挂载，故 autoRun 语义成立，无需依赖 Panel 的聚焦 effect）
```

`WeightBacktestChart` 为**新的轻量客户端组件**（lightweight-charts **v4** API，`apps/web/package.json` 锁 `^4.2.0`）：`"use client"`，由父组件以 `next/dynamic(() => import("./WeightBacktestChart"), { ssr: false })` 动态引入（与 `ChartWorkspace` 引入 `CandleChart` 同范式，规避 lightweight-charts 的 SSR 问题）。它只做「蜡烛 + 买卖 markers + 成交连线 + 净值左轴叠加」，不含指标副图 / chartSync / 挂单价线等 `CandleChart` 的重装配，保持 Factor Lab 面板轻量。markers/连线复用 `lib/tradeTrails.ts` 已导出的 `buildTradeMarkerEvents` / `buildTradeTrailSegments` / `tradeToneForEvent`；`markerForEvent`（BUY=arrowUp/belowBar/`--long`、SELL=arrowDown/aboveBar/`--short`、CLOSE=circle/aboveBar/含 `pnl` 文案）在本组件内本地复刻——`CandleChart` 中此函数为模块私有，**实测约 40 行且依赖私有 `cssVar`/`shortStrategyLabel`**（评审 P2-8），本 slim 组件**省去 per-strategy 的 `shortStrategyLabel`**、复用已导出的 `tradeToneForEvent`，仅保留单模型所需部分，属可接受的少量重复。

> **P1-1（关键实现约束）：lightweight-charts v4 不接受 `var(--long)` 之类 CSS 变量**——蜡烛 / markers / 净值线颜色须传具体 hex/rgb。故本组件须内置一个本地 `cssVar(name)`（`getComputedStyle(document.documentElement).getPropertyValue(name)`，仿 `CandleChart` 私有 `cssVar`）把 `--long/--short/--accent/--muted/--border` 令牌解析为具体色值；`theme` 变化时须**重新解析并重建图**（在建图 effect 的依赖里带上 `theme`），确保明暗主题切换正确。

---

## 三、文件 / 模块变更计划（File / Module Change Plan）

### 后端（`services/quant/app/`）

| 文件 | 变更 | 意图（一行） |
|---|---|---|
| `agent/services.py` | 新增 `run_factor_score_backtest(session, rest, *, params, inst_id, bar, start_ts, end_ts, initial_capital, fees, slippage, leverage, limit)` | 内联 factor_score 快照的一次性回测编排（不落库，返回 `(candles, result, costs)`）；零新增 import（`run_backtest`/`BacktestResult`/`Candle`/`get_strategy_class`/`load_range_candles`/`_resolve_costs` 均已在文件内）。 |
| `factors/weight_model_service.py` | 新增 `backtest_run(session, rest, model_id, run_id, *, start_ts, end_ts, initial_capital, fees, slippage, leverage, max_bars) -> dict | None` | 组装快照（`apply_weights`/`normalize_thresholds`/`assert_weights_cover`）→ 局部 import 调 `services.run_factor_score_backtest` → `weight_backtest_out` 打包；模型/记录缺失回 `None`（路由 404），非成功记录 409，成员漂移 400。 |
| `api/weight_models.py` | 新增路由 `POST /weight-models/{model_id}/runs/{run_id}/backtest` | 解析 `WeightBacktestReq` → 调 `service.backtest_run` → `None ⇒ 404`；置于 runs GET/DELETE 之后、WS 之前（路径多一段 `/backtest`，不与 `/{run_id}` 冲突）。 |
| `schemas.py` | 新增 `WeightBacktestReq`（含 both-or-neither / `startTs<endTs` 校验）、`weight_backtest_out(candles, result, *, meta)` 及序列化器 `_bt_candle_out` / `_bt_trade_out`；**`equity` 复用现有 `equity_out`**（`schemas.py:437`，已被 `agent/services.py:452` 用于内存态 `EquityRecord`，**勿再造 `_bt_equity_out`**，P1-3） | 请求契约 + 打包体序列化（camelCase；内存态 `TradeRecord`→`Trade` 用 `_bt_trade_out`——因 `trade_out`(`:425`) 读 ORM 独有 `id`/`run_id`/`explain_json` 不能直接套，故镜像其余字段；`EquityRecord`→`EquityPoint` 复用 `equity_out`；`Candle`→ `{ts,o,h,l,c,vol}`（现网无 `candle_out` 助手，故新建 `_bt_candle_out`）；`metrics` 透传 `compute_metrics` 的 7 键，**含 `avgPnl`**——`Metrics` 接口要求 7 键，即便 `MetricsCards` 只显示 6，P2-6）。 |

### 前端（`apps/web/`）

| 文件 | 变更 | 意图（一行） |
|---|---|---|
| `lib/weightModelTypes.ts` | 新增 `WeightBacktestMeta` / `WeightBacktestResult`（`import type {Candle,Trade,EquityPoint,Metrics} from "./types"`） | 回测打包体 TS 契约。 |
| `lib/weightModelApi.ts` | 新增 `WeightBacktestBody` 接口 + `weightModelApi.backtest(modelId, runId, body?)` | REST 客户端：`POST /weight-models/{id}/runs/{runId}/backtest`。 |
| `lib/weightModel.ts` | 新增纯助手 `canBacktestModel(model): boolean`（`latestRunId != null`）与 `runIsBacktestable(run): boolean`（复用现有 `runToTrained`(`:311`) 同款判据 `status==="succeeded" && run.weights && Object.keys(run.weights).length>0`，含 `!run.weights` 运行时防御，P2-7） | 卡片按钮可用性 / 详情回测区块可见性单一口径（配套 `weightModel.test.ts`）。 |
| `components/Factor/WeightBacktestChart.tsx`（新） | slim 蜡烛图组件（lightweight-charts **v4** API）：`createChart`+`addCandlestickSeries`+`setMarkers`(买卖)+成交连线+净值左轴 `addLineSeries`；**内置本地 `cssVar()`**（`getComputedStyle` 解析 `--long/--short/--accent/…` 令牌为具体色值，v4 不吃 `var(--x)`，P1-1），建图 effect 依赖带 `theme`、变化即重建 | Factor Lab 内嵌 K 线回测回放视图；props `{candles, trades, equity, theme}`；挂载建图、卸载 `chart.remove()`；`candles=[]`/`trades=[]` 空态不崩。 |
| `components/Factor/WeightBacktestPanel.tsx`（新） | 回测区块编排：`useQuery` 触发/缓存 + 状态机（idle/loading/success/error）+ 区间提示 + 「回测 / 重新回测」按钮 | 渲染 `MetricsCards` + 动态引入的 `WeightBacktestChart` + `TradesTable`；`autoRun` 时挂载即发起（消费一次）。 |
| `components/Factor/WeightModelList.tsx` | `ModelCard` 按钮行新增「回测」按钮 + 组件 props 新增 `onBacktest(model)` | 卡片一键回测入口（`canBacktestModel` 决定可用/禁用 + tooltip「请先完成一次成功训练」）。 |
| `components/Factor/WeightModelPanel.tsx` | `View.detail` 变体加 `autoBacktestRunId?: number`；接线 `onBacktest` → `setView({kind:"detail", modelId, autoBacktestRunId: model.latestRunId ?? undefined})` | 卡片「回测」→ 详情 + 自动回测最近成功记录。 |
| `components/Factor/WeightModelDetail.tsx` | 新增 prop `autoBacktestRunId?`；以 `[autoBacktestRunId]` 为依赖的 effect 播种 `selectedRunId`；**`RunReplay` 子组件新增入参 `autoBacktestRunId`**（现网仅收 `{modelId, runId}`，P1-2），在「训练权重」卡后按 `runIsBacktestable(data)`（`data` 已在 `RunReplay` 作用域）渲染 `<WeightBacktestPanel modelId runId autoRun={runId===autoBacktestRunId}/>` | 把回测区块嵌入「学习曲线回放」下方，并把卡片自动回测意图透传到目标 run（失败/取消/进行中记录经 `data` 门禁不渲染）。 |

### 测试（`services/quant/tests/`、`apps/web/**`）

| 文件 | 变更 | 意图 |
|---|---|---|
| `services/quant/tests/test_weight_backtest_api.py`（新） | 成功回测出 `candles/trades/equity/metrics`；404（缺模型/记录）；409（running/failed/cancelled/无权重）；400 `FACTOR_WEIGHTS_STALE`（改成员后回测旧记录）；`EMPTY_RANGE` 透传 | 端到端守门 + 契约（monkeypatch `app.api.weight_models.get_rest_client` 注桩行情，仿既有权重测试）。 |
| `services/quant/tests/test_weight_backtest_equivalence.py`（新，可选） | 同一 `(members+weights+thresholds+normStats+区间+成本)` 下，`backtest_run` 的 trades/metrics 与「保存为 factor_set → 伴生 factor_score 策略 → `POST /strategies/{id}/backtest`」逐笔一致 | 守护「复用既有链路、无第二套引擎」不变量。 |
| `apps/web/lib/weightModel.test.ts` | 补 `canBacktestModel` / `runIsBacktestable` 正反例 | 纯助手单测。 |
| `apps/web/components/Factor/WeightModelList.test.tsx` | 无 `latestRunId` 时「回测」禁用；有则点击调 `onBacktest` | 列表交互。 |
| `apps/web/components/Factor/WeightBacktestPanel.test.tsx`（新） | mock `weightModelApi.backtest`：成功渲染 metrics/trades、失败错误态、`autoRun` 自动发起一次 | 回测区块行为（`NODE_ENV=test` 见 [[aragon-web-vitest-node-env]]）。 |

---

## 四、接口设计（Interface Design）

### `POST /api/weight-models/{model_id}/runs/{run_id}/backtest`

对某模型的**某条成功训练记录**的权重做一次性回测。camelCase、秒级 UTC、错误体统一 `{error:{code,message}}`（沿用 `main.py` 处理器）。

**Request Body（`WeightBacktestReq`，全部可选）**

| 字段 | 类型 | 约束 | 缺省行为 |
|---|---|---|---|
| `startTs` | int | `ge=0`；与 `endTs` 同现同缺（both-or-neither）；`startTs < endTs` | 回落 `run.start_ts`（训练区间） |
| `endTs` | int | `ge=0` | 回落 `run.end_ts` |
| `initialCapital` | float | `gt=0` | `settings` 默认本金 |
| `fees` | float | `ge=0` | `settings` taker 费率 |
| `slippage` | float | `ge=0` | `settings` 滑点 |
| `leverage` | float | `gt=0` | `settings` 杠杆 |
| `maxBars` | int | `ge=10, le=200000`（请求外层上界） | 缺省 / 超限均**钳制**到 `settings.backtest_max_bars`（默认 20000）；不报 413 |

**Response 200**

```json
{
  "candles": [{"ts":1719705600,"o":1.0,"h":1.1,"l":0.9,"c":1.05,"vol":1234.5}],
  "trades":  [{"ts":1719709200,"side":"long","price":1.05,"qty":10.0,"fee":0.01,"pnl":0.0,
               "positionAfter":10.0,"reason":"复合得分多头",
               "stopLossPct":null,"takeProfitPct":null,"maxHoldBars":null,
               "stopLossPrice":null,"takeProfitPrice":null}],
  "equity":  [{"ts":1719709200,"equity":10000.0,"drawdown":0.0}],
  "metrics": {"totalReturn":0.0123,"winRate":0.6,"profitFactor":1.8,
              "maxDrawdown":-0.045,"sharpe":1.2,"tradeCount":5,"avgPnl":2.4},
  "meta":    {"instId":"BTC-USDT-SWAP","bar":"1H","startTs":...,"endTs":...,
              "horizon":6,"effectiveCandleCount":800,"initialCapital":10000.0}
}
```

**错误码**

| HTTP | code | 触发 |
|---|---|---|
| 404 | `NOT_FOUND` | 模型或训练记录不存在 |
| 409 | `WEIGHT_RUN_NOT_BACKTESTABLE` | 记录非 `succeeded` 或未产出权重（running/failed/cancelled/空权重） |
| 400 | `FACTOR_WEIGHTS_STALE` | 模型成员已变化、该记录权重与当前成员不匹配（`assert_weights_cover`），需重新训练后回测 |
| 400 | `EMPTY_RANGE` | 所选区间无已确认行情（`load_range_candles` 透传） |
| —（无 413） | — | 区间过长时 **`maxBars` 静默钳制**到 `min(maxBars, settings.backtest_max_bars=20000)`，**不复用**训练码 `FACTOR_TRAIN_TOO_LARGE`（语义不符，P2-5）；若确需硬失败另立回测专属码，不挪用训练码 |
| 422 | —（Pydantic） | `startTs>=endTs` 或 both-or-neither 违约 |

**前端 REST 客户端**

```ts
// lib/weightModelApi.ts
export interface WeightBacktestBody {
  startTs?: number; endTs?: number;
  initialCapital?: number; fees?: number; slippage?: number; leverage?: number;
  maxBars?: number;
}
backtest: (modelId: number, runId: number, body?: WeightBacktestBody) =>
  request<WeightBacktestResult>(
    `/weight-models/${modelId}/runs/${runId}/backtest`,
    { method: "POST", body: JSON.stringify(body ?? {}) }),
```

---

## 五、数据模型（Data Model）

**无数据库 schema 变更、无迁移、无新表、无新列。** 回测为同步一次性计算，结果不落库；`create_all` 加法建表机制在本轮不涉及（SQLite 下 `create_all` 只建缺表、不补缺列，故本轮刻意不改任何既有表）。

回测所需输入全部来自既有持久化字段：

- `WeightModel.members_json` → 成员快照 `[{factorKey,params,weight,direction}]`
- `WeightTrainRun.weights_json` → `{name: weight}`（训练归一化权重）
- `WeightTrainRun.norm_stats_json` → `{normWindow, clip}`（归一化契约单一真源）
- `WeightModel.config_json.thresholds` → `{enterLong,enterShort,exitBand,normWindow,squash}`（缺则 `DEFAULT_THRESHOLDS`）
- `WeightTrainRun.inst_id / bar / start_ts / end_ts / horizon` → 标的 / 周期 / 缺省回测区间

**内存装配的 factor_score 快照（`params`）**（与 `resolve_set_to_spec` 输出的 `members`/`thresholds`/`normStats` **三键同形**；`resolve_set_to_spec` 另有第 4 键 `provenance`，纯 UI、不参与打分，此处**有意省略**，P2-3）：

```jsonc
{
  "members":   [{ "factorKey": "...", "params": {}, "weight": 0.42, "direction": "long" }],
  "thresholds":{ "enterLong": 0.3, "enterShort": 0.3, "exitBand": 0.1, "normWindow": 200, "squash": true },
  "normStats": { "normWindow": 200, "clip": 4.0 }
}
```

**前端契约（`lib/weightModelTypes.ts`）**

```ts
import type { Candle, Trade, EquityPoint, Metrics } from "./types";
export interface WeightBacktestMeta {
  instId: string; bar: string; startTs: number; endTs: number;
  horizon: number; effectiveCandleCount: number; initialCapital: number;
}
export interface WeightBacktestResult {
  candles: Candle[]; trades: Trade[]; equity: EquityPoint[];
  metrics: Metrics; meta: WeightBacktestMeta;
}
```

（`Candle`/`Trade`/`EquityPoint`/`Metrics` 复用 `lib/types.ts` 既有定义，与策略回测同一套渲染契约。）

---

## 六、UI / 交互设计（美观优雅、顶级设计、符合人机交互最佳实践）

- **卡片「回测」按钮**：置于 `ModelCard` 按钮行（训练 / 查看 / **回测** / 编辑 / 删除），采用 `variant="default"`；`canBacktestModel(model)` 为假时 `disabled` 并 `title="请先完成一次成功训练"`。视觉层级低于主操作「训练」（`variant="primary"`），避免喧宾夺主。
- **详情页回测区块**：`WeightBacktestPanel` 以 `card` 容器承接，标题「K 线回测」，右侧「回测 / 重新回测」按钮 + 一行淡色说明「回测区间 = 训练区间 {start} – {end}（样本内）」。加载态骨架、错误态 `role="alert"` + 友好文案（`codeLabel`）、成功态依次呈现：
  1. **统计信息**：`MetricsCards`（总收益率 / 胜率 / 最大回撤 / 夏普 / 盈亏比 / 成交笔数，收益率按正负着色 `--long`/`--short`）。
  2. **K 线回测**：`WeightBacktestChart`，蜡烛（`--long`/`--short`）+ 买卖 markers（BUY 上箭下沿、SELL 下箭上沿、CLOSE 圆点含 `pnl` 文案）+ 盈亏成交连线（盈实线亏虚线）+ 左价轴净值曲线（`--accent`，`title="净值"`）。
  3. **逐笔明细**：`TradesTable`（时间 / 方向 / 价格 / 数量 / 盈亏 / 止损 / 止盈）。
- **主题联动**：图表读 `useAppStore(s=>s.theme)` 传入 `theme`，所有颜色走 `--long/--short/--accent/--muted/--border` 设计令牌。**注意**：lightweight-charts v4 不吃 `var(--x)`，故 `WeightBacktestChart` 内以本地 `cssVar()` 把令牌解析为具体色值，并在 `theme` 变化时重建图，实现明暗自动适配（P1-1）。
- **无障碍**：视图切换后焦点迁移沿用 `WeightModelPanel` 既有 `containerRef.focus()`；回测按钮 / 状态变更配 `aria-live`；表格沿用 `TradesTable` 既有语义。
- **失败记录不误导**：`RunReplay` 中 `failed`/`cancelled`/`running` 记录**不渲染**回测区块（`runIsBacktestable` 为假），与既有「失败/取消专门提示」一致，绝不出现空图误导。

---

## 七、测试与验收标准（Testing & Acceptance）

**验收（Definition of Done）**

1. 卡片列表：已成功训练的模型卡片出现可用「回测」按钮；未训练模型该按钮禁用并有提示。
2. 点击卡片「回测」→ 进入该模型详情、自动选中最近成功记录、自动发起回测；回测完成后在「学习曲线回放」下方出现「K 线回测」区块。
3. K 线回测区块含：带买卖 markers 的蜡烛图、净值/收益率曲线、统计信息卡片、逐笔成交明细表，四者数据自洽（markers 与明细表笔数一致、净值末值与总收益率自洽）。
4. 详情页任一成功记录的「学习曲线回放」下方均可独立触发「回测」；失败/取消/进行中记录不显示回测区块。
5. 端到端不落库：回测不新增 `strategy_runs/trades/equity_points` 行，不建新表。
6. `git status` 可见新文件；后端 `pytest`（默认集，排除 network）全绿；前端 `vitest` 全绿；`tsc --noEmit` clean；特性文件 `ruff` clean。

**测试用例（先红后绿）**

- 后端：成功回测契约（7 键 metrics + 非空 candles/trades/equity）；404 缺模型/记录；409 非成功记录（含空权重）；400 成员漂移；`EMPTY_RANGE` 透传；both-or-neither / `startTs<endTs` 422；（可选）与既有 factor_score 策略回测等价。
- 前端：`canBacktestModel`/`runIsBacktestable` 正反例；列表按钮禁用/点击；回测区块成功/失败/`autoRun`；图表组件在 `candles=[]`/`trades=[]` 时的空态不崩。
- 复现护栏：任何评审/实施期发现的回归都补测（延续项目「把坑变护栏」约定）。

---

## 八、风险与缓解（Risks & Mitigations）

| 风险 | 影响 | 缓解 |
|---|---|---|
| **样本内回测**（缺省用训练区间） | 收益率偏乐观，易误读为「真实表现」 | UI 明示「回测区间 = 训练区间（样本内）」；`WeightBacktestReq` 支持 `startTs/endTs` 覆盖，后续可加「工作台区间 / 样本外」切换；文案不夸大。 |
| **旧记录成员漂移** | 旧 run 权重与当前成员键集不符，打分错乱 | `assert_weights_cover` 边界 400 `FACTOR_WEIGHTS_STALE`；前端 `codeLabel` 友好提示「成员已变化，请重新训练后回测」；卡片「回测」只用 `latestRunId`（G-B4 保证与当前成员匹配）。 |
| **lightweight-charts 重装配 / SSR** | Factor Lab 面板变重或 SSR 崩 | 新建 slim `WeightBacktestChart`（无副图/无 chartSync/无挂单线）；父组件 `next/dynamic(..., {ssr:false})` 引入；lightweight-charts 锁 v4（禁 v5，`setMarkers`/多序列破坏性变更）。 |
| **`weight_model_service → agent.services` 潜在环** | 装载期 ImportError | 采用函数内局部 import；方向本就单向（`agent.services` 不反依赖权重模型服务）。 |
| **长区间回测阻塞/超时** | 事件循环卡顿、socket 挂断（参照既有 long-range 诊断） | `run_in_threadpool` 执行 `run_backtest`；`maxBars` + `settings.backtest_max_bars` 双重钳制；`load_range_candles` 分页 + `EMPTY_RANGE` 诚实报错。 |
| **OKX 不可达 / 空区间** | 假数据风险 | 复用既有 `OKXError`/`EMPTY_RANGE` 归一化，绝不假造行情；前端错误态显式呈现（新增 `WEIGHT_RUN_NOT_BACKTESTABLE`/`FACTOR_WEIGHTS_STALE` 到 `codeLabel` 映射）。 |
| **偏离 Factor Lab「纯 SVG」自绘惯例** | 与 `TrainingCurveChart`/`FactorScoreChart` 风格不一 | 蜡烛图本就应由 App 既有 lightweight-charts 引擎承担（自绘蜡烛不现实）；仅此一处引入，且沿用同套设计令牌与主题，视觉一致。 |
| **不落库 → 结果不跨会话持久** | 刷新后需重算 | TanStack Query 以 `(modelId,runId,range)` 为键 + 长 `staleTime/gcTime`，本会话切走切回即时可见；回测本身快（分页 + 线程池）；如需跨会话「记录」，后续可加 `weight_backtests` 新表（`create_all` 加法建表，零列迁移）——列为未来增强，本轮不做。 |

---

## 九、实施顺序建议（供 Subtask #2）

1. 后端先行（可独立测）：`schemas.WeightBacktestReq`+序列化器 → `services.run_factor_score_backtest` → `weight_model_service.backtest_run` → 路由 → `test_weight_backtest_api.py`（先红后绿）。
2. 前端类型/客户端/纯助手：`weightModelTypes` → `weightModelApi.backtest` → `weightModel.canBacktestModel/runIsBacktestable` + 单测。
3. 展示组件：`WeightBacktestChart`（dynamic）→ `WeightBacktestPanel` → 接入 `WeightModelDetail`（`autoBacktestRunId`）。
4. 列表/面板接线：`WeightModelList` 回测按钮 → `WeightModelPanel` 路由 `autoBacktestRunId`。
5. 全量校验：`pytest` 默认集 + `vitest` + `tsc --noEmit` + `ruff`（特性文件）。

> 严禁改动：`engine/factors/training.py` 的 `train_weights`/`build_train_result`（f=1.0 等价性门禁）、`factor_score` 打分函数、`factor_set ⇄ strategy` 1:1 同步契约、既有 `run_backtest`/`compute_metrics` 键集。本轮为**纯加法**。

---

## 评审结论（Review Verdict）

**有条件通过（Approved with conditions）。**

方案在四维均达标：

- **可行性**：所有复用点均经逐条对现网核验成立——后端 `apply_weights`/`normalize_thresholds`/`assert_weights_cover`（`specs.py`，成员漂移抛 400 `FACTOR_WEIGHTS_STALE`）、`FactorScoreStrategy`（消费 `members`/`thresholds`/`normStats`，`prepare` 走 `resolve_norm_contract`）、`load_range_candles`（空区间 400 `EMPTY_RANGE`）、`_resolve_costs`、`run_backtest`（接受 `htf_frames={}`）、`compute_metrics`（恰 7 键）、`TradeRecord`/`EquityRecord`（字段齐备）；前端 `buildTradeMarkerEvents`/`buildTradeTrailSegments`/`tradeToneForEvent` 已导出、`MetricsCards`/`TradesTable` 可复用、`Candle`/`Trade`/`EquityPoint`/`Metrics` 类型契约吻合、`latestRunId` 确在模型类型上、lightweight-charts 为 v4 且有 `{ssr:false}` 动态引入先例。
- **一致性**：`norm_stats_json`/`latestRunId`/`{normWindow,clip}`/`DEFAULT_THRESHOLDS` 字段名与现网**逐字吻合**；未与 `CLAUDE.md` 或既有约定冲突；守住 R-P0-1 后向兼容（旧式顶层 `weights` 读取路径不动）与归一化单源（`resolve_norm_contract` 优先 `normStats.normWindow`）。
- **合理规模**：零 schema 迁移、纯加法、不新增回测引擎 / 策略类型、不改打分函数，仅「一段装配 + 一个只读端点 + 一组展示组件」，规模得当，无过度设计。
- **完整性**：错误路径（404 / 409 `WEIGHT_RUN_NOT_BACKTESTABLE` / 400 `FACTOR_WEIGHTS_STALE` / 400 `EMPTY_RANGE` / 422）与样本内回测风险、长区间阻塞、SSR、循环依赖均已覆盖。

**无 P0**；3 项 P1 已在本 v2 就地修复。

**放行条件（Subtask #2 实施时必须遵守，对应上文 P2）：**

1. 序列化：`equity` 复用 `schemas.equity_out`，**不再造** `_bt_equity_out`；`_bt_trade_out` 镜像 `trade_out` 去掉 ORM 独有 `id`/`run_id`/`explain_json`；`metrics` 仍输出 7 键（含 `avgPnl`，即便 `MetricsCards` 只显示 6）。
2. `WeightBacktestChart` 必须内置 `cssVar()` 把设计令牌解析为具体色值（v4 不吃 `var(--x)`），并在 `theme` 变化时重建图。
3. `RunReplay` 新增 `autoBacktestRunId` 入参、用已取到的 `data` 走 `runIsBacktestable(data)` 门禁；`selectedRunId` 由「以 `autoBacktestRunId` 为依赖的 effect」播种。
4. 伪代码按**真实签名**实现：`load_range_candles` 为 async + 全关键字（`max_bars=`）、`repo.get_weight_*` 首参为 `session`、`run_backtest` 成本参数为关键字；`BacktestResult` 只取 `trades`/`equity_points`/`metrics` 三字段。
5. `maxBars` 静默钳制到 `settings.backtest_max_bars`（默认 20000），**不挪用**训练错误码 `FACTOR_TRAIN_TOO_LARGE`。
6. `runIsBacktestable` 复用现有 `runToTrained`(`weightModel.ts:311`) 同款守卫（含 `!run.weights` 防御）。
7. 回测路由留在 `api/weight_models.py`（现有 `patch_okx` fixture 已覆盖其 `get_rest_client`）；新回测测试遵循**共享进程 DB**纪律（按返回 id 断言、勿断言全局计数）；对 `normStats` 缺失的极旧记录补一条保守处理（建议守门 409 或至少注释，P2-9）。

满足以上条件即可进入 Subtask #2 代码开发。

> 评审版本：**v2** ｜ 评审维度：可行性 / 完整性 / 一致性 / 合理规模 ｜ 无 P0；P1（3 项）已就地修复；余为 P2 实施须知。
