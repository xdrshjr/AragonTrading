# AI 自动交易 · 成本真值化与净额盈亏（官方 API 考证 + 全链净额 + Agent 成本意识）

> **版本：v2**（v1 设计初稿 → 本轮设计评审就地修正；本节点只出设计、不写实现代码）
> 需求来源：用户报告「AI 自动交易显示的盈亏与真实情况差了大概一定比例的一个数额；需要做官方 API 考证、修正成本、确保最后的盈亏测算准确，并且 agent 执行买卖时要能意识到合理的成本」
> 迭代：loop `tge-1c4cdcf0` 第 1 轮 · Subtask #0（方案设计）→ Subtask #1（设计评审，产出 §评审记录 / §评审结论）
> 评审状态：**有条件通过**（3 P0 / 8 P1 已在正文就地修正，7 P2 记录待办；放行条件见文末 §评审结论）
> 上游相关：[`ai-trade-cost-accounting-and-edge-gate`](../ai-trade-cost-accounting-and-edge-gate/spec.md)（阶段一 · 已合入 commit `96f554d`）、[`ai-trade-signal-prior-and-ev-gate`](../ai-trade-signal-prior-and-ev-gate/spec.md)（commit `c6db95d`）、[`ai-trade-vol-sizing-and-drawdown-brake`](../ai-trade-vol-sizing-and-drawdown-brake/spec.md)（commit `ee6640d`）
> feature 目录：`docs/plans/ai-trade-cost-truth-and-net-pnl/`

---

## 〇、评审记录（Review Notes · v1 → v2）

> 评审姿态：**先复核事实，再评设计**。v1 的 §2.1 F-1~F-18 十八条源码事实与 §3.7 的八个文件当前行数
> 已在 `HEAD = 359f19a` 的工作树上逐条回读复核，**全部属实**（含 `session.py` 1655 / `executor.py` 895 /
> `prompts.py` 778 / `cost_runtime.py` 153 / `manager.py` 704 / `okx/trading.py` 659 /
> `ai_auto_repositories.py` 308 / `fee_ledger.py` 350 / `autoTradePnl.ts` 152）。四条轴的问题定位、
> §2.2 的偏差量化、以及「不做什么」四条排除项，评审均予认可。下列问题针对的是**设计的可实现性与
> 口径闭合性**，不针对选题。
>
> 全部 P0 / P1 已在正文对应小节就地修正（修正处以 `【v2】` 标注），P2 记录在案、由实施节点在
> §十二 回执处置结论。

### P0（阻断级：不修则本轮交付要么建不出来、要么产出新的错数）

| 编号 | 位置 | 问题 | 处置 |
|---|---|---|---|
| **P0-1** | §3.4 D-3 | **轴 D 的成本数在注入点根本取不到。** D-3 规定 `CostRuntime.inject` 挂在 `session._build_decision_payload` 末尾（`session.py:1280-1325`），成本值取自 `estimate_cost_only`。但 `estimate_cost_only(decision, bar_seconds)` 的**第一个必填入参就是 `decision`**（`cost_runtime.py:81`），而 `query_for_decision` 要从 `decision.action` 取资金费方向、从 `decision.ord_type` 取开仓腿类型（`cost_model.py:166-187`）——payload 是**喂给模型去产生决策的输入**，装配时决策尚不存在（其唯一现有调用点 `session.py:983` 在 `_apply_sizing` 内，那里决策已产出）。宪章章在会话启动时装配，同理更无决策。按 v1 写法实现节点会在第一行就卡住，或被迫伪造一个假 decision（那会让 prompt 里的数字与门禁真实判定的数字不同源，直接摧毁 A-6 与 G-3）。 | 正文新增 §3.4 D-3a：`cost_model` 抽出 `query_for_preview(...)`，`query_for_decision` 改为它的薄包装（保持唯一真源）；`CostRuntime.estimate_preview_cost(...)` 用**会话已知**的开仓腿意图与方向假设产出预览成本，并把假设本身写进 wire。G-3 同步改判据。 |
| **P0-2** | §3.3 C-2 / §5.4 | **轴 C 的对账两侧口径不可比，`drift` 告警会按设计恒响。** §十一 自己写明「净额 ≈ 交易所口径 + 资金费 + 强平罚金」，而 C-2 的差额式是 `diffUsd = local_net_usd − exchangeRealizedUsd` ——本地净额**按定义不含**资金费与强平罚金（§一「不做什么」①、§十一），交易所 `realizedPnl` **按定义含**。于是任何跨过一次结算点的持仓都会让 `diffUsd` 恒等于资金费本身；`abs_tol=0.5 USD` + `rel_tol=0.05`（§5.5 默认）下，`L=3` 的一天持仓就足以连续 3 轮触发 `AI_TRADE_PNL_RECONCILE_DRIFT`。**一个按设计必然误报的告警，比没有告警更糟**——它会训练运维忽略它。此外 `local_net_usd` 全文未定义：若取 `session_pnl_usd`（含未实现）则与只覆盖**已平仓**的 `positions-history` 再叠一层结构性差额；若 `net_pnl_enabled=false` 则本地 realized 是毛额，与交易所侧再差全部手续费。 | 正文 C-2 改为对**可比口径**作差：`exchangeComparableUsd = Σ(realizedPnl + fundingFee + liqPenalty)`（归一后「支出为正」，即把本地不记的两项加回去），`diffUsd = local_net_usd − exchangeComparableUsd`；`localNetUsd` 硬定义为「窗口内**已平仓往返**的净额之和」（basis 为 gross 的会话按 `pnl − legFee` 还原），**绝不取 `session_pnl_usd`**。原始 `exchangeRealizedUsd` 照发但不参与判定。 |
| **P0-3** | §3.2 B-4 | **老 run 缺 `costBasis` 一律判 `"gross"`，会对一部分历史行二次扣费。** B-4 的三代行模型漏了第四代：`config.py:317` 上方注释与阶段一 spec §7.5 给出的**是仓库自己的启用 runbook**——「先满 50 笔费率入账 → 开 A0-2 → 观察 100 笔 → 再置 `k_min=2.0`」。也就是说本轮之前把 `ai_trade_net_pnl_enabled` 打开是被**明确鼓励**的操作路径，那些 run 的 `trades.pnl` 已经是净额、`fee != 0`、且 `config_json` 里**没有** `costBasis` 键。新聚合会把它们再减一次 `legFee`，历史看板凭空多亏一个往返成本——与本 spec 要修的缺陷同一量级、方向相反。在一个专门以「盈亏显示准确性」为交付物的 spec 里引入反向的系统性错数，是不可接受的。 | 正文 B-4 增第四代行与 `ai_trade_history_legacy_cost_basis` 配置项（默认 `"gross"`，运维可置 `"net"`），并强制回执与前端携带 `costBasisAssumed` 标记，把「推定」与「已知」在 UI 上分开。G-6 增一条判据。 |

### P1（必须修：口径漏洞、缺失的接缝、与既有约定冲突）

| 编号 | 位置 | 问题 | 处置 |
|---|---|---|---|
| **P1-1** | §3.0 / §3.5 F-5 / §5.4 / §六 | **`reconcile` / `feeRates` 的挂载层级两处自相矛盾。** §3.0 架构图与 §六 数据模型表写 `metricsJson.aiStats.cost.reconcile`（挂在 `cost` 子对象下），§5.4 wire 与 §3.5 F-5 写 `costSnapshot.feeRates` / `costSnapshot.reconcile`（顶层）。而 `build_cost_stats` 是会话内快照与落库 `aiStats` 的**唯一**真源（`stats_ext.py:22-38` 的模块 docstring 明写「绝不另算一份」），两条通道必然同形——两种写法不可能同时成立。本仓库刚在信号先验特性上踩过同款坑（两条快照通道互斥导致运维按 runbook 永远找不到 `priorCoverage`）。 | 统一为**顶层**：`costSnapshot.feeRates` / `costSnapshot.reconcile`，落库即 `aiStats.feeRates` / `aiStats.reconcile`；`sessionFeeUsd` 归入 `cost` 子对象（与 `measuredFeeBps` 同族）。§六 表格与 §3.0 图同步改写，并增一条「新顶层键须先确认不与既有 `aiStats` 键冲突（`merge_cost_stats` 冲突即抛）」。 |
| **P1-2** | §3.1 A-3/A-4、§3.3 C-4、§5.2 | **`CostRuntime` 没有凭证接缝。** 轴 A 的 `trade-fee` 与轴 C 的 `positions-history` **都是签名私有 GET**，而 `CostRuntime.__init__(inst_id, settings, fee_ledger)`（`cost_runtime.py:27`）拿不到 `AccountCredentials`；既有 `refresh_funding` 之所以能只用 `get_rest_client()`，是因为资金费率是**公共**端点。v1 全文未提这条缝，`refresh_fee_rates` / `reconcile_if_due` 按现签名无法实现，且 §3.7 给 `cost_runtime.py` 的 `≤ +90` 行预算也未含它。 | 正文 A-4 / C-4 / §5.2 增第四个构造入参 `creds`（会话已持有 `executor.creds`），并明确 `creds is None ⇒ 两条私有取数一个请求都不发`（G-9 的实现依据）。§3.7 预算相应上调至 `≤ +95`。 |
| **P1-3** | §3.2 B-5 | **`captureRatio` 在净额切换后跨口径，且老会话的该 KPI 会被追溯改写。** `_summarize_pairs` 同时产出利润捕获率五键（`ai_auto_repositories.py:74-119`）：分母 `peakPnlUsd` 来自 `trades.peak_pnl`，而该列由利润保护层按**本地派生毛浮盈**落库（`profit_guard.py:518` 的 `qty_coin × (mark_px − entry)`，不经 `unrealized_usd`，本 spec 也有意不动它）。v1 把 `_summarize_pairs` 的全部 `exit.pnl` 读点换成 `trip.net_pnl`，于是分子变净、分母仍毛——比值系统性下移，且**老会话（basis=gross）的历史 `captureRatio` 也会被追溯改小**。`schemas.py:922` 已经用注释记过这条不对称并说它「会让小额往返系统性偏低」；本轮再把它放大一个往返成本而不声明，等于用一个正确的修复打坏另一个已发布的 KPI。 | 正文 B-5 明确：`realizedPnlUsd` / `bestUsd` / `worstUsd` / `winRate` 走**净额**；`capturedPnlUsd` 与分母 `peakPnlUsd` **同为毛额**（用 `trip.gross_pnl`），使比值维度自洽；`schemas.py:922` 的注释同步更新。新增 G-14 双向断言。 |
| **P1-4** | §3.1 A-2 第 3 步 | **返佣档位会把负 fee 写进记账链。** A-2 只说「供**模型消费**的一侧按 `max(0.0, bps)` 夹紧」，而 `FeeLedger` 的记账链不是「模型消费」——按字面实现，`rate > 0` 时 `retune` 会拿到负 bps，`leg_fee` 产出负费用，净额反而**高于**毛额。这既可能是真实返佣，也可能是一次符号解析错误（`maker`/`makerU` 的符号约定正是 §2.3 ① 标了 ⚠️ 待联调确认的部分），而两者在数值上不可区分。本 spec 的全部立场是「宁可保守、绝不虚增盈亏」，此处却留了唯一一处能让盈亏被凭空抬高的口子。 | 正文 A-2 改为：**记账链与决策链一律夹紧到 `≥ 0`**，原始有符号值只进 wire 供审计；`rebate=true` 时额外推一条 status 提示。真实返佣带来的正向差额会在轴 C 的对账里作为可解释的正 `diffUsd` 出现——那正是轴 C 存在的意义。 |
| **P1-5** | §3.3 C-4 | **`stop()` 收尾的强制对账既无超时上界、又必然赶不上最后一笔。** ① C-4 要求在 `_finalize_run` 之前**忽略节流**强跑一次对账，而 `stop()`（`session.py:355-375`）此刻刚做完真实资金平仓、正要落库终态；`_get` 自带 3 次退避重试（`private_client.py:103+`），一次限流就能把 stop 拖住数秒到十几秒，`stop()` 全程持有 `_exec_lock` 语义且是用户点「停止」后的可见路径。② 顺序上强制对账排在 flatten **之后**，而 `positions-history` 的 `uTime` 对刚平的那一笔存在结算延迟——落库 `metricsJson` 里的**终态**对账会系统性少一笔，`coverageMismatch=true` 且 `diffUsd` 恰好等于最后一笔往返，看板上呈现为一个伪 drift。 | 正文 C-4 增两条硬约束：强制对账整体包在 `asyncio.wait_for(..., timeout=ai_trade_reconcile_stop_timeout_s)`（新配置，默认 3.0s，超时即 `status="unavailable"` 落库）；且**终态对账不得推进 `alert_streak`、不得推 drift 事件**，`coverageMismatch` 时 `status` 降为 `"partial"` 而非 `"drift"`。 |
| **P1-6** | §3.5 F-2 | **前端函数签名写错，且腿费归集拿不到所需数据。** ① 现网签名是 `buildRoundTrips(instId: string, trades: Trade[], leverage: number)`（`autoTradePnl.ts:53-57`，被 `buildLedger` 与多处调用），v1 写成 `buildRoundTrips(trades, opts?)` ——按 v1 实现会连带改坏全部调用点。② B-5 的费用归集规则要求「上一笔平仓（不含）到本笔平仓（含）的**全部腿**」，但 `buildTradeTrailSegments` 只回 `{entry, exit, direction}` 段、丢弃被覆盖的先前开仓腿，正是 B-5 要防的漏记；同时 `buildRoundTrips` 在配对前 `filter(!t.seeded)`，归集必须用**同一份过滤后的原始数组**。 | 正文 F-2 改为在**末位加可选参**：`buildRoundTrips(instId, trades, leverage, opts?: { costBasis?: "net" \| "gross" })`（默认 `"gross"`，非 AI 调用点逐字节不变）；并明确归集在 `real` 数组上按索引区间累计 `fee`，与后端 `_Trip.leg_fee_usd` 同规则、同一组夹具双侧断言（G-7）。 |
| **P1-7** | §3.2 B-4 / §4.1 #11 | **`feeRates` 落 run config 的取数路径不存在。** B-4 要求在 `_create_group_run` 的 `config` dict 里写 `"feeRates": fee_rates_wire`，但该函数是 `@staticmethod _create_group_run(ctx: _GroupBuildCtx, plan: _MemberPlan, take_profit_usd, stop_loss_usd)`（`manager.py:657-695`）——它既拿不到 `config_map`、也拿不到执行器，v1 未说明这个值怎么传进去。同款问题在 `_build_executor` 侧不存在（它本就收 `config`），故很容易被误判为「两处都通」。 | 正文 B-4 明确：官方费率快照随成员计划下传——在 `_MemberPlan` 上加一个只读字段 `fee_rates_wire`（组装点即 `_gather_context` 之后的成员规划处），`_create_group_run` 直接读 `plan.fee_rates_wire`；§4.1 #11 的意图句同步补上这条。 |
| **P1-8** | §3.2 B-3 | **净额传导面表漏列三个真实消费方。** B-3 自称「逐条确认」，但漏了：① `LiveExecutor.context()`（`executor.py:161-165`）用 `_unrealized_at(mark_px)` 拼 `BrokerContext.equity`，而 B-2 恰恰要在 `_unrealized_at` 末行套 `_net_unrealized` ——该权益每根 K 线喂给**每一个影子策略**的 `on_candle`，按权益缩仓的策略会因此改变信号；② `_finalize_run` 的 `totalReturn = realized / budget` 与 `avgPnl`（`session.py:1513-1520`）随 `realized_pnl_usd` 变净，是落库的历史 KPI；③ `position_payload` 的 `self.unrealized_usd or self._unrealized_at(mark_px)` 回退支（`executor.py:172`）在净额下有一个 `0.0` 触发回退的边缘态。一张自称穷举的确认表漏项，比没有表更危险——它会让下游默认「已经想过了」。 | 正文 B-3 补三行，并对 ① 给出显式判定（期望：影子策略的权益基准与真实到手金额一致），对 ③ 给出判据（回退支两侧同为净额，`0.0` 回退不产生口径跳变）。 |

### P2（记录在案，不阻断；由实施节点在 §十二 回执）

| 编号 | 位置 | 问题 |
|---|---|---|
| P2-1 | §3.1 A-4 | 方法名笔误：预取点真名是 `AiTradeManager._gather_context`（`manager.py:268`），v1 写作 `_prefetch_account_context`（行号 269-289 是对的）。 |
| P2-2 | §7.3 | 回归基线里的 `tests/test_prior_session.py` 真名是 `tests/test_ai_trade_prior_session.py`；`test_factor_library_expansion.py` 的孤儿测试基线属实。 |
| P2-3 | §3.7 | 轴 A 需要给 `build_fee_ledger` / `resolve_fee_params`（`fee_ledger.py:259-261` / `:70-76`）加 `rates=` 入参，§3.7 的 `fee_ledger.py ≤ +45` 预算条目未列该项，实施时请一并核。 |
| P2-4 | §3.2 B-5 | `_pair_round_trips` 的排序键是 `trades.id` 而非 `ts`（双时钟，见该函数 docstring）。B-5 的「上一笔平仓之后」窗口必须钉死**同一个**排序键，否则腿费会串窗。 |
| P2-5 | §5.3 | `AI_TRADE_FEE_RATE_UNAVAILABLE` 被定义为 `type: "status"` 却携带 `code`；仓库既有带 `code` 的事件（`AI_TRADE_EDGE_GATE_UNAVAILABLE`、`AI_TRADE_FLATTEN_FAILED`、`AI_TRADE_ROUND_FAILED`）一律 `type: "error"`。建议要么改 `type: "error"`（并接受它在 UI 上更醒目），要么去掉 `code` 只留文案。 |
| P2-6 | §2.3 ① | 「⚠️ 需联调确认 `makerU`/`takerU` 是否恒非空」没有指定回执落点与责任节点；建议明确由 Subtask #2 在首次真实账户联调后写进 §十二，并附脱敏后的字段快照。 |
| P2-7 | §8 R-2 | 对账挂在 5s 盯市 tick 上、`ai_trade_reconcile_interval_s` 默认 300s：N 标的分组稳态期每 5 分钟 N 次签名 GET。R-2 只覆盖了启动瞬间的限频，未覆盖稳态期的私有配额，建议在 R-2 补一句稳态期估算。 |

---

## 一、概述（Overview）

用户看到的「AI 自动交易盈亏」与交易所账户里的真实数字之间存在一个**系统性的、与交易笔数成正比的正偏差**。回读当前工作树可以把这个偏差定位到一处配置和三条读数通道，而不是某个偶发 bug：阶段一（commit `96f554d`）已经把「每笔成交按确定性模型记 `trade["fee"]`」做完了（`ai_trade_fee_accounting_enabled` 默认 `True`，`services/quant/app/config.py:315`），**但把这笔费用真正扣进盈亏的那个总闸至今默认关闭**——`ai_trade_net_pnl_enabled: bool = False`（`config.py:317`），于是 `FeeLedger.net_close_pnl` 恒返回毛额（`services/quant/app/live_auto/fee_ledger.py:170-179`），`LiveExecutor._close_pnl` 恒走毛口径（`live_auto/executor.py:752-759`），`session_pnl_usd = 已实现 + 未实现`（`executor.py:157-159`）继续按毛额累加。这个被高估的数字同时驱动：决策 prompt 里的「会话累计盈亏」（`ai_auto/session.py:1290`）、会话级金额止盈止损（`session.py:1094`）、权益曲线与回撤刹车（`session.py:1440`、`ai_auto/sizing_runtime.py:354-356`）、实时状态栏（`session.py:1462`）、以及统计看板与历史列表（`db/ai_auto_repositories.py:74-121` 的 `realized = Σ exit.pnl`、前端 `apps/web/lib/autoTradePnl.ts:60-71` 同口径镜像）。**「显示的盈亏」与「真实的盈亏」之间差的正是每笔往返的双边手续费**，而这恰好是一个可以精确算出来的比例。

偏差的第二个来源是**未实现盈亏取的是交易所的 `upl`**（`executor.py:611` 的 `_net_upl`，OKX `account/positions` 的 `upl` 字段按标记价算价差、不含任何费用），因此哪怕把已实现那一侧切成净额，持仓期间显示的浮盈仍然比「此刻平掉能拿到手的钱」多出一个开仓费加一个预估平仓费。第三个来源是**资金费率从未进入任何盈亏读数**：`ai_auto/funding.py` 取到的费率只喂给 edge 门禁的成本模型（而该门禁 `ai_trade_edge_gate_k_min` 默认 `0.0` 即空转，`config.py:328`），持仓跨越结算点时交易所真实扣走的钱，本系统在任何地方都看不到。第四个来源是**费率常数是硬编码的猜测**：`ai_trade_maker_fee_bps=2.0` / `ai_trade_taker_fee_bps=5.0`（`config.py:320-321`）是 OKX Lv1 的挂牌费率，但真实账户的档位、OKB 抵扣、返佣与 USDT/USDC/币本位合约的分档全都可能不同——**从来没有任何一行代码去问过交易所"我这个账户这个合约到底收我多少"**。

本 spec 的交付就是把这四条一次性补齐，并且**用交易所自己的数字来证明我们算对了**。四条轴：**轴 A（官方费率真源）**——接入 OKX 官方 `GET /api/v5/account/trade-fee`，在会话启动时按 `instId` 取真实 maker/taker 档位，成为 `FeeLedger`（记账）与 `CostParams`（门禁 / prompt）的**同一个**真源，取数失败回落现有常数并如实标注来源；**轴 B（净额口径全链贯通）**——`ai_trade_net_pnl_enabled` 默认翻为 `True`，同时把未实现盈亏、权益曲线、历史聚合、前端往返汇总一并切到净额，并用落在 `strategy_runs.config_json` 的 `costBasis` 标记解决"同一个库里既有毛额老行、又有净额新行"的口径断层；**轴 C（交易所对账）**——接入官方 `GET /api/v5/account/positions-history`，用交易所自己结算的 `realizedPnl` / `fee` / `fundingFee` / `liqPenalty` 与本地净额逐窗对账，差额与差额比例作为一等公民下发到统计看板，漂移超阈即告警；**轴 D（Agent 成本意识）**——新增 `cost_text.py`，把「本次往返的确定性成本是多少 bps、折成价格是多少、盈亏平衡价在哪、它占当前 ATR 的几成、本会话已经付掉多少手续费」写进系统 prompt 的宪章章与每轮决策输入，让模型在下单之前就看得见成本，而不是只在事后被一个它看不见的确定性门禁否决。

**不做什么（明确排除）**：① 不把资金费率**记进**本地已实现盈亏——本地账本必须保持"离线可复现、不依赖网络可用性"这条阶段一就立下的不变式（`fee_ledger.py:15-20`），资金费率作为**交易所侧读数**在对账与统计里单列呈现，并已通过成本模型进入决策；② 不改利润保护层（`ai_auto/profit_guard.py`）的判定口径——它按 `_current_px()` 本地派生浮盈、走保证金收益率口径，与本 spec 的净额切换正交，混进去会破坏它"never-worse vs v2"的单调棘轮承诺；③ 不改回测世界的成本常数（`default_taker_fee_bps` / `default_slippage_bps`）与 `cost_model.backtest_params`（`ai_auto/cost_model.py:84-96`），回测继续对自己的世界说真话；④ 不动 edge 门禁的判定式与 `k_min` 默认值（本轮只让它的成本入参变准、变得可解释）。

---

## 二、现状核实（回读源码 + 官方文档考证）

### 2.1 代码事实（逐条回读当前工作树，均为 `HEAD = 359f19a` 的真实行号）

| 编号 | 事实 | 位置 |
|---|---|---|
| F-1 | `ai_trade_net_pnl_enabled` 默认 `False`，注释自称「破坏性变更，默认关」 | `config.py:316-317` |
| F-2 | `FeeLedger.net_close_pnl` 在总闸关时**恒返回入参 `gross` 本身** | `fee_ledger.py:170-179` |
| F-3 | `LiveExecutor._close_pnl` 是 `_gross_close_pnl` 经账本过滤；账本为 `None`（`live_auto` 全部路径）恒毛口径 | `executor.py:752-759` |
| F-4 | `unrealized_usd` 直接来自 OKX `upl` 求和，**不含费用** | `executor.py:611`、`:797-799` |
| F-5 | `session_pnl_usd` = 已实现 + 未实现，是 6 处读数的唯一源 | `executor.py:157-159` |
| F-6 | `trades.fee` 列早已存在且 AI 链路已按确定性模型落库 | `db/models.py:84`、`db/repositories.py:297` |
| F-7 | 历史聚合 `realized = Σ exit.pnl`，**从不读 `fee` 列** | `db/ai_auto_repositories.py:74-121` |
| F-8 | 前端往返汇总同口径（`pnlUsd = seg.exit.pnl`） | `apps/web/lib/autoTradePnl.ts:60-71` |
| F-9 | `Trade` wire 已含 `fee` 字段，前端类型已声明 | `schemas.py:1378`、`apps/web/lib/types.ts:313` |
| F-10 | 成本模型 / 费率账本的费率常数唯一来自 settings，**全仓库无任何交易所费率查询** | `cost_model.py:74-81`、`fee_ledger.py:70-76`；`grep -r "trade-fee"` 无命中 |
| F-11 | `OKXTradeClient` 已有签名 GET 基建（`_get`），加一个端点是 5 行 | `okx/private_client.py:103`、`okx/trade_client.py:184-189` |
| F-12 | `_normalize_fill` 已回带 `fee` / `feeCcy` / `clOrdId`，实测审计通路已在跑（`ai_trade_fill_audit_enabled` 默认 `True`） | `okx/trading.py:468-492`、`fee_ledger.py:287-310` |
| F-13 | 决策 prompt 与系统 prompt **一个字都没提成本**（只有仓位层开启时 `sizing_text` 有一句定性描述） | `prompts.py:70-190`、`:553-616`；`grep 成本 prompts.py` 仅 4 处非成本语境 |
| F-14 | edge 门禁 `k_min` 默认 `0.0` ⇒ 整条门禁空转 ⇒ 资金费率取数也被构造期条件关掉 | `config.py:328`、`cost_runtime.py:32-35` |
| F-15 | `evaluate_edge_gate` 已有 `params=None` 覆盖缝（回测在用），可直接承接官方费率覆盖 | `edge_gate.py:185-208` |
| F-16 | `CostRuntime` 已是会话成本层的编排落点，`refresh_funding` 已确立「网络 IO 只跑盯市 tick」的范式 | `cost_runtime.py:49-51`、`session.py:438` |
| F-17 | `LiveExecutor` 被 `ai_auto` 与 `live_auto` **共用同一份 settings**，作用域只能由构造缝隔离 | `executor.py:89`、`ai_auto/manager.py:656`（`live_auto` 不传 ⇒ `fee_ledger is None`） |
| F-18 | 会话启动已有「整组一次、按 `live_auto_start_concurrency` 限流」的私有请求预取点 | `ai_auto/manager.py:269-289` |

### 2.2 偏差的量化（这就是用户说的「一定比例」）

设会话预算 `B`、杠杆 `L`、单次开满仓，则名义额 `N = B × L`；一笔往返的确定性手续费为 `N × (feeOpen + feeClose) / 1e4`。按当前默认常数：

| 场景 | 双边费率 | 单笔往返费用 / 预算 | 20 笔往返累计 / 预算 | 50 笔往返累计 / 预算 |
|---|---|---|---|---|
| 市价开 + 市价平（`L=3`） | 5 + 5 = 10 bps | 0.30% | 6.0% | 15.0% |
| 限价开 + 市价平（`L=3`） | 2 + 5 = 7 bps | 0.21% | 4.2% | 10.5% |
| 市价开 + 市价平（`L=5`） | 10 bps | 0.50% | 10.0% | 25.0% |

**这就是"显示的盈亏"高于"真实盈亏"的确切数额**：口径切换后，看板上的数字会一次性下移这个量级；再叠加资金费率（`0.01%/8h` 的常见水平在 `L=3` 下相当于每 8 小时 0.03% 的预算），跨日持仓还会再多一块。用户报告的"差了大概一定的比例"与本表在量级上自洽，且该偏差**与交易笔数成正比**——这也解释了为什么高频轮次的会话看起来偏差更大。

### 2.3 官方 API 考证（本轮"考证"的落点）

本节列出实现节点必须依赖的 OKX 官方端点与字段。**考证结论中带 ⚠️ 的条目要求实现节点在首次联调时用真实账户回执二次确认，并把确认结果写进 §十二**——本 spec 采用"代码在运行期自检、不盲信文档措辞"的姿态（见 C-3 的恒等式自检）。

**① `GET /api/v5/account/trade-fee`（签名 GET，读权限即可）** — 查询本账户在指定合约上的**真实**费率档位。
- 请求参数：`instType`（必填，本项目恒 `SWAP`）、`instId`（本项目按标的传，如 `BTC-USDT-SWAP`）。
- 关键响应字段：`level`（费率档位，如 `Lv1`）、`ruleType`（`normal` / `vip`）、`maker` / `taker`（**币本位**合约与现货）、`makerU` / `takerU`（**USDT 保证金**合约）、`makerUSDC` / `takerUSDC`（USDC 保证金合约）、`ts`。较新版本另有 `rpiMaker`（2026-07-28 起，替代 2026-05-20 引入的 `elpMaker`；不适用时返回空串）——本 spec **不消费** RPI/ELP 字段，只作为"字段可能新增"的兼容性依据（解析必须容忍未知键）。
- 符号约定：费率为**有符号字符串**，**负数 = 平台向你收取**，正数 = 返佣。`"-0.0005"` 即 5 bps 的 taker 费。
- ⚠️ 需联调确认：`makerU` / `takerU` 在 USDT 永续上是否恒非空（历史上曾出现空串回落到 `maker`/`taker` 的形态）。本 spec 的归一函数按"优先取分档字段、为空回落通用字段"实现，两种形态都能正确工作。

**② `GET /api/v5/account/positions-history`（签名 GET）** — 交易所侧**已结算持仓**的权威盈亏，本轮对账的锚点。
- 请求参数：`instType=SWAP`、`instId`、`limit`（≤100）、可选 `after`/`before`（毫秒时间戳，按 `uTime` 过滤）。
- 关键响应字段：`posId`、`instId`、`direction`（`long`/`short`）、`openAvgPx`、`closeAvgPx`、`cTime`（开仓时刻，ms）、`uTime`（最后更新 / 平仓时刻，ms）、`pnl`（价差口径盈亏）、`fee`（累计手续费，**负数 = 支出**）、`fundingFee`（累计资金费，**负数 = 支出**）、`liqPenalty`（强平罚金，有值时为负）、`realizedPnl`（**已实现盈亏，交易所口径**）。
- ⚠️ 官方文档给出的关系式是 `realizedPnl = pnl + fee + fundingFee + liqPenalty`（四项按上述符号相加）。本次考证未能从公开页面逐字取回该措辞（`docs-v5` 为 SPA，抓取到的是渲染前骨架），**故本 spec 不把该式当作前提**：`reconcile.py` 以 `realizedPnl` 为对账锚点，同时**在运行期数值自检该恒等式**并把最大偏离量 `identityMaxGapUsd` 与布尔 `identityHolds` 一并下发（C-3）。恒等式若不成立，看板会如实显示"交易所口径自检未通过"，而不是给出一个建立在错误假设上的差额。
- 历史深度：该端点覆盖最近 3 个月，对会话级（小时~天级）对账绰绰有余。

**③ `GET /api/v5/public/funding-rate`（公共）** — 已接入（`okx/rest_client.py:259-287`），本轮不改，仅在 §3.2 放宽其取数条件（见 B-6）。

**④ `GET /api/v5/trade/fills`（签名 GET）** — 已接入（`okx/trading.py:454-465`），实测费率审计已在跑，本轮不改契约，仅把它产出的 `measuredFeeBps` 用作轴 A 的**第二重校验**（§3.2 A-5）。

---

## 三、技术设计（Technical Design）

### 3.0 总体架构

```
                          ┌─────────────────── 会话启动（AiTradeManager）───────────────────┐
  OKX /account/trade-fee ─┤ _gather_context → config_map[inst]["feeRates"] → plan.feeRates  │
                          │        ↓                                                        │
                          │ _build_executor → build_fee_ledger(settings, ct_val, rates=…)   │
                          └────────────────────────────┬────────────────────────────────────┘
                                                       │  单一真源（记账 + 门禁 + prompt 同读）
   ┌───────────────────────────────────────────────────┴─────────────────────────────────────┐
   │ FeeLedger.params(maker/taker bps, source)  ⇄  CostParams（cost_model.resolve_cost_params）│
   └───────┬──────────────────────────────┬───────────────────────────┬──────────────────────┘
           │ 轴 B：记账并扣减              │ 轴 D：写进 prompt          │ 轴 A：盯市 tick 按 TTL 刷新
           ▼                              ▼                           ▼
   trade["fee"] / _close_pnl        cost_text.format_*        FeeRateCache.refresh()
   / _net_unrealized                （宪章章 + 每轮成本段）      （复用 FundingCache 范式）
           │                              │
           ▼                              ▼
   session_pnl_usd（净额）          decision payload["costAwareness"]
   equity / TP-SL / 刹车 / 看板      → build_decision_prompt → 反思 prompt（自动继承）
           │
           ▼  轴 C：与交易所自己的数字对账
   reconcile.py ← OKX /account/positions-history（realizedPnl / fee / fundingFee / liqPenalty）
           │
           ▼
   costSnapshot.reconcile → WS status/edge_gate 事件 + metricsJson.aiStats.reconcile → 前端看板
```

**【v2 · P1-1】挂载层级的唯一约定**：`build_cost_stats` 是会话内 `costSnapshot` 与落库
`metricsJson.aiStats` 的**同一个**产物（`stats_ext.py` 模块 docstring：「绝不另算一份」），故两条通道
必然同形。全文统一为：`feeRates` / `reconcile` 挂**顶层**（`costSnapshot.feeRates`、落库即
`aiStats.feeRates`），`sessionFeeUsd` 挂 `cost` 子对象（与 `measuredFeeBps` / `feeDeviationBps` 同族）。
新增两个顶层键前须先确认不与既有 `aiStats` 键冲突——`merge_cost_stats` 键冲突即 `raise ValueError`
（`stats_ext.py:41-53`），冲突会被 `_finalize_run` 的 best-effort 兜底吞成「整块富化丢失」，即
**统计静默消失而不是报错**，故这是一条必须前置核对的硬约束。

四条轴各自独立开关、各自可单独回退（§3.6）。全部新增业务逻辑落在**新模块**里，`session.py`（1655 行）与 `executor.py`（895 行）已双双超 `CLAUDE.md` 的 800 行红线，本轮只允许薄封装调用（§3.7 行数预算）。

### 3.1 轴 A：官方费率真源

**A-1 客户端方法（新增，`okx/trade_client.py`）**

```python
async def get_trade_fee(self, inst_type: str, inst_id: str | None = None) -> dict:
    """``GET /api/v5/account/trade-fee``：本账户在该合约上的真实费率档位（签名 GET，复用基类 _get）。"""
    params = {"instType": inst_type}
    if inst_id:
        params["instId"] = inst_id
    rows = await self._get("/api/v5/account/trade-fee", params)
    return rows[0] if rows else {}
```

**A-2 归一（新增，`okx/trading.py`）** `fetch_trade_fee(creds, inst_id, *, inst_type="SWAP") -> dict`，错误映射与 `fetch_live_fills` 逐字同构（`_map_trade_code` + `_redact`）。归一函数 `_normalize_trade_fee(raw, inst_id)` 的判定链（**顺序不可换**）：

1. 按 `inst_id` 后缀选分档字段：`-USDC-SWAP` ⇒ (`makerUSDC`, `takerUSDC`)；`-USDT-SWAP` ⇒ (`makerU`, `takerU`)；其余（币本位）⇒ (`maker`, `taker`)。
2. 分档字段为空串 / 缺失 ⇒ **回落** (`maker`, `taker`)；仍为空 ⇒ 该侧返回 `None`（**不返回 0**——0 bps 是一个会静默放松门禁的危险默认）。
3. 数值化：`bps = -rate × 1e4`（收取为负 ⇒ bps 为正=支出）。`rate > 0`（返佣）⇒ `bps` 为负。
   **【v2 · P1-4】负 bps 一律夹紧到 `0.0` 后再交给任何下游——记账链与决策链一视同仁**，原始有符号值只以 `makerBpsRaw` / `takerBpsRaw` 进 wire 供事后审计，并置 `rebate: True` + 推一条 `AI_TRADE_FEE_RATE_UNAVAILABLE` 同族的 status 提示。
   v1 只说「供模型消费的一侧夹紧」，而 `FeeLedger` 的记账链并不属于「模型消费」——按字面实现，`retune` 会拿到负 bps ⇒ `leg_fee` 产出负费用 ⇒ **净额高于毛额**，这是全 spec 唯一一处能让盈亏被凭空抬高的口子。更关键的是：真实返佣与一次符号解析错误在数值上**不可区分**，而符号约定正是 §2.3 ① 标了 ⚠️ 待联调确认的部分。夹紧的代价只是「真有返佣时本地略偏保守」，而那笔差额会在轴 C 的对账里作为可解释的正 `diffUsd` 浮出来——那正是轴 C 存在的意义；不夹紧的代价则是「盈亏可能被静默抬高」，与本 spec 的交付目标直接冲突。
4. 产出：`{"instId", "makerBps", "takerBps", "makerBpsRaw", "takerBpsRaw", "level", "ruleType", "rebate", "ts", "source": "exchange"}`；任一侧为 `None` ⇒ 整体判定为不可用（返回 `None`，调用方回落配置常数）。

**A-3 会话级缓存（新模块 `ai_auto/fee_rates.py`）** —— `FeeRateSnapshot`（frozen dataclass）+ `FeeRateCache`，**逐字复用 `ai_auto/funding.py` 的三条不变式**（异常一律吞、陈旧绝不冒充新鲜、`enabled=False` 一个请求都不发）。差异只有两点：TTL 默认 3600s（费率档位按日变），以及**没有 stale ⇒ None 的降级**——费率快照陈旧了仍然比配置常数准，故陈旧只影响 `ageS` 读数与 wire 上的 `stale` 标记，不撤回数值。

```python
@dataclass(frozen=True)
class FeeRateSnapshot:
    maker_bps: float
    taker_bps: float
    level: str
    rule_type: str
    rebate: bool
    source: str          # "exchange" | "config"
    fetched_wall: float

def resolve_fee_rates(settings, snapshot: FeeRateSnapshot | None) -> tuple[float, float, str]:
    """(maker_bps, taker_bps, source)：快照缺席 ⇒ 配置常数 + source="config"（唯一回落点）。"""
```

**A-4 装配与热更新（两个落点）**
- **启动**：`AiTradeManager._gather_context`（**【v2 · P2-1】** v1 误写作 `_prefetch_account_context`；真名见 `manager.py:268`，行区间 269-289 无误）在既有 `sem` 并发预算内并发取每个成员的 `fetch_trade_fee`，把归一结果**合并进 `config_map[inst_id]["feeRates"]`**（零签名改动：`_build_executor` 本就从 `config` 读 `instMeta`，`manager.py:643-657`）。任一标的取数失败 ⇒ 该键为 `None` ⇒ 回落常数，**绝不阻断启动**（可用性不得成为开仓能力的函数）。
- **热更新**：`CostRuntime.refresh_fee_rates(now_wall)` 挂在盯市 tick 上，紧邻既有 `refresh_funding`（`session.py:438`）。
  **【v2 · P1-2】凭证接缝（本轮新增，轴 A 与轴 C 共用）**：`trade-fee` 与 `positions-history` **都是签名私有 GET**，而既有 `refresh_funding` 之所以只要 `get_rest_client()`，是因为资金费率是**公共**端点。`CostRuntime.__init__` 因此新增第四个入参 `creds: AccountCredentials | None`（会话侧传 `executor.creds`，`session.py:156` 的构造点一行改动）。**`creds is None` ⇒ 轴 A 与轴 C 的私有取数一个请求都不发**（既有单测直接构造会话时不带凭证，这条同时是 G-9 的实现依据与那批单测的零回归保证）。`FeeRateCache.refresh(fetch_fn, now_wall)` 的 `fetch_fn` 由 `CostRuntime` 用 `creds` 绑好后传入，缓存本身不认识凭证（镜像 `FundingCache` 只认 `inst_id` 的姿态）。刷新成功 ⇒ 调 `FeeLedger.retune(maker_bps, taker_bps, source)`。**不变式 A-4a：`retune` 只改变此后新腿的计费，绝不回改任何已记账的 `trade["fee"]` 或已累计的 `entry_fee_usd`**（否则一笔持仓的开仓费会在持仓期间被悄悄改写，平仓时净额与开仓时的承诺对不上）。实现上 `FeeParams` 是 frozen dataclass，`retune` 用 `dataclasses.replace` 换一个新对象即可，天然满足该不变式。

**A-5 与实测审计的三方校验（零新代码，只改文案落点）** 现有 `FeeLedger.audit_stats()` 已产出 `measuredFeeBps` / `feeDeviationBps`（`fee_ledger.py:244-252`）。轴 A 落地后，`feeDeviationBps` 的含义从"配置常数偏离实测多少"升级为"**官方档位偏离实测多少**"——理论上应收敛到 0；若持续显著为正（约等于 `taker − maker`），仍指向 §8 R-4 的挂价过于激进，处置不变。该读数与 `source` 一并进 `costSnapshot.cost`。

**A-6 单一真源约束（硬）** 官方费率一旦装配，必须同时覆盖**两条**消费链，不得只覆盖一条：
- 记账链：`FeeLedger.params`（决定 `trade["fee"]` 与净额扣减）。
- 决策链：`cost_model.resolve_cost_params` 产出的 `CostParams`（决定 edge 门禁阈值、仓位层凯利项的 `cost_bps`、以及轴 D 写进 prompt 的数字）。
实现方式：`CostRuntime` 新增 `_cost_params()`，返回 `dataclasses.replace(resolve_cost_params(settings), maker_fee_bps=…, taker_fee_bps=…)`，并把它经既有 `params=` 缝（`edge_gate.py:208`，回测已在用）传给 `evaluate_edge_gate` 与 `estimate_round_trip_cost`。**验收判据 G-3 会断言两条链读到同一对数字。**

### 3.2 轴 B：净额口径全链贯通

**B-1 总闸默认翻转** `ai_trade_net_pnl_enabled: bool = True`（`config.py:317`）。这是本 spec **唯一**一处有意偏离仓库"每个上线项默认关"纪律的地方，理由写在配置注释里：本项就是用户报告的缺陷本身，默认关等于交付一个默认不生效的修复。回退路径保留且被测试覆盖（置 `false` ⇒ 逐字回到今天的行为）。

**B-2 未实现盈亏净额化（`fee_ledger.py` + `executor.py`）** 新增与 `net_close_pnl` 严格同构的：

```python
def net_unrealized(self, gross: float, *, entry_fee_usd: float, mark_px: float,
                   sz: float) -> float:
    """浮盈的净额口径 = 毛浮盈 − 已付开仓费 − 按标记价预估的平仓腿 taker 费。
    总闸关 ⇒ 恒返回 gross 本身（位相同）。"""
```

`executor.py` 的改动收敛到**一个新私有方法 + 两个调用点**：
```python
def _net_unrealized(self, gross: float, mark_px: float) -> float:
    if self.fee_ledger is None or self.projected == "flat":
        return gross                       # live_auto 全部路径逐字节回退
    return self.fee_ledger.net_unrealized(gross, entry_fee_usd=self.entry_fee_usd,
                                          mark_px=mark_px, sz=self.entry_qty_sz)
```
- 调用点 1：`_on_reconcile_ok`（`executor.py:611`）→ `self.gross_unrealized_usd = self._net_upl(positions)`；`self.unrealized_usd = self._net_unrealized(self.gross_unrealized_usd, mark_px)`。新增只读字段 `gross_unrealized_usd`（供轴 C 对账与排查，不进任何判定）。
- 调用点 2：`_unrealized_at`（`executor.py:780-785`，`context()` 与 `position_payload()` 的本地回退路径）末行套一层 `self._net_unrealized(...)`。

三处既有的 `unrealized_usd = 0.0` 复位点（`:535` / `:556` / `:617`）同步复位 `gross_unrealized_usd`——**漏掉即在下一次持仓期把上一次的毛浮盈泄漏给对账层**。

**B-3 净额的传导面（有意为之，逐条确认）**

| 消费方 | 位置 | 切换后的语义变化 | 判定 |
|---|---|---|---|
| 决策 prompt「会话累计盈亏」 | `session.py:1290` | 变净额（并由轴 D 明写口径） | ✅ 期望 |
| 会话级金额止盈止损 | `session.py:1094` | 止损略微提前、止盈略微推后 | ✅ 期望（阈值本就该按到手金额判） |
| 权益曲线 / `equityDigest` | `session.py:1440` | 曲线整体下移一个累计成本 | ✅ 期望 |
| 回撤刹车 / `unrealized_share` | `sizing_runtime.py:354-356` | 回撤按净额算，刹车更早触发 | ✅ 期望 |
| 实时状态栏 / 前端持仓卡 | `session.py:1462`、`executor.py:167-175` | 显示"此刻平掉能拿到手的钱" | ✅ 期望 |
| 利润保护棘轮 | `ai_auto/profit_guard*.py` | **不变**（它读 `_current_px()` 本地派生浮盈，不读 `unrealized_usd`；已复核 `profit_guard.py:518` 的 `qty_coin × (mark_px − entry)` 自算一份） | ✅ 有意不动（§一"不做什么"②） |
| **【v2 · P1-8】影子策略权益** | `executor.py:161-165` `context()` → `BrokerContext.equity` | `equity = budget + realized + _unrealized_at(mark)`，而 B-2 恰恰要在 `_unrealized_at` 末行套 `_net_unrealized` ⇒ **每根 K 线喂给每一个影子策略的权益基准变净额** | ✅ 期望（策略看到的权益应与真实到手金额同口径）；须在 §7.2 补一条「按权益缩仓的策略在净额下信号可变」的显式确认用例 |
| **【v2 · P1-8】落库历史 KPI** | `session.py:1513-1520` `_finalize_run` | `totalReturn = realized/budget` 与 `avgPnl` 随 `realized_pnl_usd` 变净额 | ✅ 期望（历史列表与详情本就该是净额） |
| **【v2 · P1-8】持仓卡回退支** | `executor.py:172` `self.unrealized_usd or self._unrealized_at(mark_px)` | 两侧在净额下**同为净额**，`unrealized_usd` 恰为 `0.0` 时触发的回退不产生口径跳变 | ✅ 结构性保证（两侧同经 `_net_unrealized`）；G-2 增一条该边缘态断言 |
| `live_auto`（非 AI）全链路 | `fee_ledger is None` | **逐字节不变**（含上面三行：`_net_unrealized` 在 `fee_ledger is None` 时恒返回 `gross`） | ✅ 结构性保证（F-17） |

**B-4 历史口径断层与 `costBasis` 标记（关键）** 同一个 `trades` 表里会同时存在**四**代行：① 阶段一之前 `fee = 0.0` 的行（毛额且**不可还原**）；② 阶段一之后、本轮之前 `fee != 0` 但 `pnl` 是毛额的行（**可还原**）；③ 本轮之后 `pnl` 已是净额且 `config.costBasis == "net"` 的行；**④【v2 · P0-3】本轮之前运维已按仓库自带 runbook 打开 `ai_trade_net_pnl_enabled` 的行——`pnl` 已是净额、`fee != 0`、但 `config_json` 里没有 `costBasis` 键**。聚合层必须能区分，否则会把净额行再扣一次费。

第 ④ 代不是理论边角：`config.py:317` 上方注释与阶段一 spec §7.5 给出的**正是**「先满 50 笔费率入账 → 开 A0-2 → 观察 100 笔 → 再置 `k_min=2.0`」这条启用 runbook，即在本轮之前把总闸打开是被**明确鼓励**的操作路径。v1 的「缺失 ⇒ `"gross"`」会对这批行二次扣费，让历史看板凭空多亏一个往返成本——在一个以「盈亏显示准确性」为交付物的 spec 里引入方向相反、量级相同的系统性错数，不可接受。

解法（**零迁移**）：在 `AiTradeManager._create_group_run` 的 `config` dict（`manager.py:660-695`，已是自由 JSON）追加两个键：
```python
"costBasis": "net" if settings.ai_trade_net_pnl_enabled else "gross",
"feeRates": plan.fee_rates_wire,   # 轴 A 快照（含 level / source），供事后审计"当时按什么费率记的账"
```
**【v2 · P1-7】`feeRates` 的下传路径**：`_create_group_run` 是 `@staticmethod(ctx: _GroupBuildCtx, plan: _MemberPlan, take_profit_usd, stop_loss_usd)`（`manager.py:657`），既拿不到 `config_map` 也拿不到执行器，v1 未说明这个值从哪来。落点定为**成员计划**：`_MemberPlan` 增一个只读字段 `fee_rates_wire: dict | None`，在 `_gather_context` 之后的成员规划处由 `config_map[inst_id].get("feeRates")` 填入，`_create_group_run` 直接读 `plan.fee_rates_wire`。（`_build_executor` 侧不存在此问题——它本就收 `config`，故很容易误判为「两处都通」。）

聚合层 `db/ai_auto_repositories.py` 经既有 `_load_config(run.config_json)`（`:30-39`）读出 `costBasis`。**【v2 · P0-3】缺失时不再硬判 `"gross"`**，而是取新配置项 `ai_trade_history_legacy_cost_basis`（`"gross"` | `"net"`，默认 `"gross"`——它对绝大多数库是对的，因为总闸出厂即关），并在回执与前端强制携带 `costBasisAssumed: true`：老 run 的口径是**推定**而非已知，UI 必须把「推定」与「已知」分开显示（沿用 `costBasisMixed` 徽标的同一条降级范式）。已按 runbook 提前开启净额的运维把该配置置 `"net"` 即可让第 ④ 代行不再被二次扣费；这是一次性的、可观测的、可回退的运维动作，好过在数据上做一个不可验证的猜测。

**B-5 往返净额的计算规则（前后端逐字同构）** 定义"一笔往返的费用" = **自上一笔平仓（不含）起、到本笔平仓（含）止的全部成交腿的 `fee` 之和**。按"配对的那条开仓腿的 fee"计是错的：部分成交会产生多条开仓腿（`executor.py:497-501` 的增量采纳），而现有配对函数 `_pair_round_trips`（`ai_auto_repositories.py:50-71`）只保留**最后一条**开仓腿，会漏记先前分批的费用。

后端 `_pair_round_trips` 改为返回 `list[_Trip]`：
```python
@dataclass(frozen=True)
class _Trip:
    entry: Trade
    exit: Trade
    leg_fee_usd: float      # Σ 上一笔平仓之后至本笔平仓（含）的全部腿 fee
    cost_basis: str         # 所属 run 的 costBasis（"net" | "gross"）

    @property
    def net_pnl(self) -> float:
        pnl = self.exit.pnl or 0.0
        return pnl if self.cost_basis == "net" else pnl - self.leg_fee_usd

    @property
    def gross_pnl(self) -> float:
        """毛额还原（净额行加回腿费）——捕获率分子必须与毛额分母同口径，见下方 P1-3。"""
        pnl = self.exit.pnl or 0.0
        return pnl + self.leg_fee_usd if self.cost_basis == "net" else pnl
```
`_summarize_pairs` 把 `realizedPnlUsd` / `winRate` / `bestUsd` / `worstUsd` 四个读点换成 `trip.net_pnl`，并**新增三个加法键**：`feeUsd`（Σ `leg_fee_usd`）、`grossPnlUsd`（Σ `trip.gross_pnl`）、`costBasisMixed`（该组跨 run 出现两种 basis ⇒ `True`，前端据此打"口径混合"徽标）。前端 `autoTradePnl.ts` 逐字镜像该规则（§3.5 F-2）。

**【v2 · P1-3】利润捕获率五键必须留在毛额口径。** `_summarize_pairs` 同时产出 `peakPnlUsd` / `capturedPnlUsd` / `captureRatio` / `capturePairs` / `noPeakCount`（`ai_auto_repositories.py:74-119`）。分母 `peakPnlUsd` 来自 `trades.peak_pnl`，而该列由利润保护层按**本地派生毛浮盈**落库（`profit_guard.py:518`，不经 `unrealized_usd`；本 spec §一"不做什么"② 也有意不动它）。若按 v1 的「全部 `exit.pnl` 读点换成 `trip.net_pnl`」实现，分子变净、分母仍毛，比值会系统性下移**一个往返成本**，且**老会话（basis=gross）的历史 `captureRatio` 会被追溯改小** ——`schemas.py:922` 的注释已经记过这条不对称并说它「会让小额往返系统性偏低」，本轮再把它放大而不声明，等于用一个正确的修复打坏另一个已发布的 KPI。

故：`capturedPnlUsd` 恒取 `trip.gross_pnl`（与分母同为毛额，比值维度自洽），`schemas.py:922` 的注释同步更新为「分子分母**同为毛额**（不含手续费），手续费口径请看同级 `feeUsd` / `realizedPnlUsd`」。新增 G-14 双向断言：同一组夹具下 `captureRatio` 在 `net_pnl_enabled` 真假两侧**逐位相同**。

**B-6 资金费率取数条件放宽** `CostRuntime.__init__` 现按 `funding_enabled and k_min > 0` 构造 `FundingCache`（`cost_runtime.py:32-35`）。轴 D 让 prompt 消费成本读数后，`k_min = 0`（默认）下资金费率**已有消费方**，故条件放宽为 `funding_enabled and (k_min > 0 or cost_awareness_enabled)`。这不违反当初 I-2 的初衷（"没有消费方就别发请求"），而是消费方真的出现了。

### 3.3 轴 C：交易所对账

**C-1 取数（`okx/trade_client.py` + `okx/trading.py`）** `get_position_history(inst_type, inst_id, limit, after=None)` + `fetch_position_history(creds, inst_id, *, limit=50, after_ms=None) -> list[dict]`，归一 `_normalize_position_history(r)`：秒级 `cTime`/`uTime`、数值容错、**符号统一为"正数 = 支出"**（`fee` / `fundingFee` / `liqPenalty` 三项取反），`pnl` 与 `realizedPnl` 保持"正数 = 盈利"原义。归一后附 `identityGapUsd = realizedPnl − (pnl − fee − fundingFee − liqPenalty)`（三项已取反，故此处为减）。

**C-2 纯核心（新模块 `ai_auto/reconcile.py`，无 IO、可单测）**

```python
@dataclass(frozen=True)
class ReconcileParams:
    abs_tol_usd: float
    rel_tol: float

def build_reconciliation(*, local_net_usd: float, local_trips: int,
                         rows: list[dict], window_start_ts: int,
                         params: ReconcileParams, final: bool = False) -> dict:
    """本地净额 vs 交易所**可比口径**的窗口对账；绝不抛，样本不足即 status="unavailable"。"""
```

**【v2 · P0-2】`local_net_usd` 的硬定义（v1 全文未定义，这是差额可信度的前提）**：恒取「窗口内**已平仓往返**的净额之和」，即 `Σ trip.net_pnl`（basis 为 `"gross"` 的会话按 `pnl − legFee` 还原，与 §3.2 B-5 同一条规则、同一份 `_Trip`）。**绝不取 `executor.session_pnl_usd`** ——后者含**未实现**盈亏，而 `positions-history` 只覆盖**已结算**持仓，混用会在差额里叠一层与持仓浮盈同幅的结构性偏差；也绝不在 `net_pnl_enabled=false` 时直接用毛额 realized，否则差额恒等于全部手续费。

**【v2 · P0-2】差额必须对**可比**口径作差。** §十一 自己写明「净额 ≈ 交易所口径 + 资金费 + 强平罚金」，而 v1 的 `diffUsd = local_net_usd − exchangeRealizedUsd` 没有兑现这条：本地净额**按定义不含**资金费与强平罚金（§一"不做什么"①），交易所 `realizedPnl` **按定义含**。于是任何跨过一次结算点的持仓都会让 `diffUsd` 恒等于资金费本身；按 §5.5 的默认容差（`abs_tol=0.5 USD` / `rel_tol=0.05`），`L=3` 的一天持仓就足以连续 3 轮触发 `AI_TRADE_PNL_RECONCILE_DRIFT`。**一个按设计必然误报的告警比没有告警更糟**——它训练运维忽略它，等真的漂移时没人再看。

判定链（v2）：
① 过滤 `uTime >= window_start_ts` 且 `instId` 匹配的行 ⇒ `samples`；
② `samples == 0` ⇒ `status="unavailable"`（**不报 0 差额**——没样本不等于对上了）；
③ 三个原始读数照发：`exchangeRealizedUsd = Σ realizedPnl`、`exchangeFeeUsd = Σ fee`、`exchangeFundingFeeUsd = Σ fundingFee`、`exchangeLiqPenaltyUsd = Σ liqPenalty`（后三项已在 C-1 归一为「支出为正」）；
④ **可比口径**：`exchangeComparableUsd = exchangeRealizedUsd + exchangeFundingFeeUsd + exchangeLiqPenaltyUsd`（把本地不记的两项加回去，使两侧都只含「价差 − 手续费」）；
⑤ `diffUsd = local_net_usd − exchangeComparableUsd`，`diffRatio = diffUsd / max(1e-9, abs(exchangeComparableUsd))`；
⑥ `drift` 判据：`abs(diffUsd) > max(abs_tol_usd, rel_tol × abs(exchangeComparableUsd))`；
⑦ `coverageMismatch = (samples != local_trips)`（本地往返数与交易所结算数不等 ⇒ 差额可解释性下降，**照发但打标**，最常见成因是同标的上存在非本会话的手工持仓）；
⑧ **`final=True`（stop 收尾那一次）时永不判 `drift`**：`coverageMismatch` ⇒ `status="partial"`，否则按 ④~⑥ 给 `ok`（理由见 C-4 的【v2 · P1-5】）。

`exchangeRealizedUsd` 与两项加回量同时下发，看板据此可以一眼回答「差额里有多少是资金费」——这恰好也把 §8 R-12「资金费率仍未进本地已实现盈亏」从一句免责变成一个可读的数字。

**C-3 恒等式运行期自检** `identityMaxGapUsd = max(abs(identityGapUsd))`，`identityHolds = identityMaxGapUsd <= max(abs_tol_usd, 1e-6 × Σ|realizedPnl|)`。这是 §2.3 ⚠️ 的兑现方式：文档措辞无法逐字取回时，**让代码自己去证**；不成立即在看板上如实标注，绝不用一个未经验证的公式去解释差额。

**C-4 编排（`CostRuntime` + `session.py` 各一处）** `CostRuntime.reconcile_if_due(now_wall, window_start_ts, local_view)`：总闸关 / 未到 `ai_trade_reconcile_interval_s` / **`creds is None`**（【v2 · P1-2】的凭证缝）⇒ `None`（一个请求都不发）；否则取数 + 建对账 + 记 `self.last_reconcile`。异常全吞（`logger.debug`），与 `audit_fills` 同姿态。挂点两处：① 盯市 tick，紧随 `refresh_funding`（`session.py:438` 之后）；② `stop()` 收尾、`_finalize_run` **之前**强制跑一次（忽略节流），使落库的 `metricsJson` 携带最终对账。连续 `drift` 达 `ai_trade_reconcile_alert_streak` 轮推一条 `error` 事件（码 `AI_TRADE_PNL_RECONCILE_DRIFT`），文案必须点名差额与可能成因，**不得宣称已自动修正**（本层只观测、不回写盈亏）。

**【v2 · P1-5】stop 收尾那一次必须另立两条硬约束**，v1 只写了「强制跑一次」，而那条路径的两个性质与盯市 tick 完全不同：

1. **必须有超时上界。** `stop()`（`session.py:355-375`）此刻刚做完**真实资金平仓**、正要落库终态，而 `_get` 自带 3 次指数退避重试（`private_client.py:103+`）——一次限流就能把用户点下「停止」后的可见路径拖住数秒到十几秒。故强制对账整体包在 `asyncio.wait_for(..., timeout=settings.ai_trade_reconcile_stop_timeout_s)`（新配置，默认 `3.0`），超时即以 `status="unavailable"` 落库。**对账是观测层，任何情况下都不得延长真实资金平仓后的收尾**（沿用 `_finalize_run` 富化「可失败、绝不阻断落库」的既有姿态）。
2. **终态对账不得推 drift。** 强制对账排在 flatten **之后**，而 `positions-history` 的 `uTime` 对刚平的那一笔存在结算延迟 ⇒ 落库 `metricsJson` 里的终态对账会**系统性少一笔**：`coverageMismatch=true` 且 `diffUsd` 恰好等于最后一笔往返，在看板上呈现为一个伪 drift，而这偏偏是用户最常看的那一屏。故 `build_reconciliation(..., final=True)`：**不推进 `alert_streak`、不推 `AI_TRADE_PNL_RECONCILE_DRIFT` 事件**，`coverageMismatch` 时 `status` 降为 `"partial"`（前端文案：「最后一笔尚未在交易所侧结算，稍后可在历史详情重新对账」）。

**C-5 为什么不回写** 与阶段一 §3.2.1 同一条理由并再加一条：`positions-history` 是**持仓级**结算而本地账本是**往返级**，`net_mode` 与 `long_short_mode` 下二者的边界并不总是一一对应；把交易所读数直接灌回本地盈亏，会让"盈亏正确性 = 网络可用性 × 持仓模式假设"。对账层的价值是**把不可见的偏差变成可见的数字**，这已经足以回答"显示的准不准"。

### 3.4 轴 D：Agent 成本意识

**D-1 新模块 `ai_auto/cost_text.py`（中文文案唯一真源，镜像 `sizing_text.py` 范式）**

```python
def format_cost_charter(view: dict | None) -> str: ...       # 系统 prompt 宪章章
def format_cost_for_prompt(view: dict | None) -> str: ...    # 每轮决策段
COST_BACKTEST_NOTE = "…"                                     # 回测偏差免责（与既有三条同源同展示）
```
宪章章（会话启动时装配一次，`view` 为 `None` ⇒ `""` ⇒ 系统 prompt 逐字节回退）内容要点：
> 【交易成本 · 系统确定性扣减】
> · 本会话每笔往返的确定性成本约 **X bps**（开仓 A bps + 平仓 B bps + 冲击与价差 C bps + 预估资金费 D bps），费率取自交易所官方接口（档位 `Lv1`，maker 2.0 / taker 5.0 bps）。
> · 折算到价格：**价格必须朝你的方向走过约 X bps（≈ 0.0X%）你才回本**；反手（先平后开）要付两倍。
> · 你看到的"会话累计盈亏"**已扣除**手续费（净额口径）；持仓浮盈同样按"此刻平掉能拿到手的钱"呈现。
> · 频繁进出会把成本乘以次数——每一次"再看看"的开平都在真实扣钱。

每轮段（`payload["costAwareness"]` 缺席 ⇒ `""` ⇒ 整段省略）内容要点：往返成本 bps 与折价、**当前持仓的盈亏平衡价**、成本占当根 ATR 的比例、本会话累计已付手续费、以及（有持仓时）距平衡价还差多少。

**D-2 盈亏平衡价（新纯函数，落在 `cost_model.py`）**
```python
def break_even_px(entry_px: float, *, side: str, cost_bps: float) -> float | None:
    """开仓价 × (1 ± cost/1e4)；入参非法 ⇒ None。
    注意：平衡价与杠杆无关（杠杆只放大保证金收益率，不改变回本所需的价格距离）——
    这一点必须写进文案，否则模型会用杠杆去"稀释"成本。"""
```

**D-3 payload 注入（`CostRuntime.inject(payload, …)`，镜像 `_sizing.inject`）** 在 `session._build_decision_payload`（`session.py:1283-1325`）末尾加一行 `self._cost.inject(payload, last_px=ref_px, atr_px=self._atr_px, bar_seconds=BAR_SECONDS[self.bar])`。总闸关 ⇒ **整键缺席**（不是 `None`），prompt 渲染与既有 payload 冻结断言逐字节回退。wire 形状见 §5.4。

**D-3a【v2 · P0-1】成本数值的来源必须是「预览成本」，不能是 `estimate_cost_only`。**
v1 写「成本数值取自 `CostRuntime.estimate_cost_only`」，但那个方法的**第一个必填入参就是 `decision`**（`cost_runtime.py:81`），而 `query_for_decision` 要从 `decision.action` 取资金费方向、从 `decision.ord_type` 取开仓腿类型（`cost_model.py:166-187`）。`_build_decision_payload` 装配的正是**喂给模型去产生决策的输入**，此刻决策尚不存在（`estimate_cost_only` 唯一现有调用点 `session.py:983` 在 `_apply_sizing` 内，那里决策已产出）；宪章章在会话启动时装配，更无决策。按 v1 字面实现，实施节点要么卡死，要么被迫伪造一个假 decision——后者会让 prompt 里的数字与门禁真实判定的数字不同源，**直接摧毁 A-6 与 G-3**。

修正（保持唯一真源，不新增第二条估算路径）：

```python
# cost_model.py：把「装配 CostQuery」与「从 decision 取值」拆开，query_for_decision 变薄包装
def query_for_preview(*, ord_type: str, side: str, bar_seconds: int, funding,
                      funding_enabled: bool, half_spread_bps: float | None = None) -> CostQuery
def query_for_decision(decision, *, bar_seconds, funding, funding_enabled,
                       half_spread_bps=None) -> CostQuery:
    """薄包装：side/ord_type 由 decision 取，其余逐字委托 query_for_preview（行为逐字节不变）。"""

# cost_runtime.py：决策前的只读预览成本（与 estimate_cost_only 共用同一对纯核心）
def estimate_preview_cost(self, *, ord_type: str, side: str, bar_seconds: int) -> CostBreakdown | None
```

**预览假设的取值规则（必须写进 wire，不得隐式）**：
- `ord_type`：取会话装配时已知的开仓腿意图——`settings.ai_trade_limit_orders_enabled`（`config.py:163`，同一个真源经 `manager.py:417/437` 的 `allow_limit=` 已经喂给 `build_ai_trade_system_prompt` 与 `AiTradeDecider`）为真取 `"limit"`，否则 `"market"`。**必须复用会话装配时算出的那个 `allow_limit` 值本身，不得在 `CostRuntime` 里重新读一次 settings** ——否则一旦将来 `allow_limit` 变成按会话可覆盖，prompt 说的与模型实际能下的就会分叉。
- `side`：**有持仓时取持仓方向**（此时模型最需要知道的是"这笔平掉要付多少"）；**空仓时取资金费的不利侧**，即 `long` / `short` 两侧各算一次取 `total_bps` 较大者——保守方向与 `_funding_carry_bps` 的"只取不利侧"不变式同向，绝不因方向假设而让成本看起来更便宜。
- 两个假设以 `previewOrdType` / `previewSide` 进 `payload["costAwareness"]`，文案里如实说明"按 X 腿、Y 方向估算"。

**G-3 相应改判**（v1 的"三者逐位相同"在假设不一致时不成立，会变成一条必然失败或必须靠巧合通过的判据）：`prompt` 的 `roundTripBps` 与 `edge_gate` wire 的 `costBps`、仓位层的 `cost_bps` 在 **`(ord_type, side)` 一致的轮次**逐位相同；不一致的轮次则断言两者**读同一份 `CostParams`**（即 A-6 的单一真源仍成立），且 payload 里 `previewOrdType` / `previewSide` 如实标出了差异。

`estimate_preview_cost` 与 `estimate_cost_only` 复用**同一对**纯核心（`estimate_round_trip_cost` + `_cost_params()`），**不受 `k_min` 门控**（`cost_runtime.py:81-101` 的既有理由原样适用），因此轴 D 在 edge 门禁默认空转时照常工作。

**D-4 prompt 接线（`prompts.py` 两处，均为末位默认空参）**
- `build_ai_trade_system_prompt(..., cost_text: str = "")`：加入 `charter` 拼接元组（`prompts.py:108-109`），**置于 `sizing_text` 之后**——四段宪章的顺序是"在哪开（铁律）→ 何时锁（保护）→ 下多大注（仓位）→ 每笔要付多少（成本）"。
- `build_decision_prompt`：新增 `_format_cost(payload.get("costAwareness"))`，插在 `sizing_line` 之后、"会话累计盈亏"那一行**之前**——模型先知道成本，再看盈亏，才能正确解读那个数字。
- **反思链自动继承**：`build_reflection_prompt` 内部复用 `build_decision_prompt`（`prompts.py:693`），故下单前反思天然看得见同一份成本证据，零额外接线。

**D-5 决策工具契约不动** 不新增字段、不改 `DECISION_TOOL_DESCRIPTION` 主体（避免动已冻结的工具契约）。成本是**输入侧**的证据，不是输出侧的字段。

**D-6 与 edge 门禁的分工** 轴 D 是**软引导**（模型可以在成本很高时自己选择 hold），edge 门禁是**硬门禁**（`k_min > 0` 时确定性否决）。两者读同一份 `CostBreakdown`，故不会出现"prompt 说 8 bps、门禁按 12 bps 判"的信任崩塌。这也是 A-6 单一真源约束的直接收益。

### 3.5 前端镜像

- **F-1 新 lib `apps/web/lib/aiTradeCost.ts`**：`describeCostBasis(basis)`、`formatCostBps`、`summarizeCostSnapshot(snap)`、`describeReconcile(rec)`（把 `status`/`diffUsd`/`diffRatio`/`identityHolds` 翻成中文短语）。纯函数 + 单测。
- **F-2 `apps/web/lib/autoTradePnl.ts`（152 行）【v2 · P1-6 已修正签名与数据源】**：现网签名是 `buildRoundTrips(instId: string, trades: Trade[], leverage: number)`（`autoTradePnl.ts:53-57`，被 `buildLedger` 与多处调用），v1 写成 `buildRoundTrips(trades, opts?)` 会连带改坏全部调用点。改为**末位加可选参**：`buildRoundTrips(instId, trades, leverage, opts?: { costBasis?: "net" | "gross" })`。按 §3.2 B-5 的规则累计 `feeUsd`；`RoundTrip` 增 `feeUsd` / `grossPnlUsd`，`pnlUsd` 变净额。**默认 `costBasis = "gross"`**——`live_auto`（非 AI）成交的 `fee` 恒为 `0.0`（F-17），减零即恒等，故非 AI 调用点逐字节不变（这是结构性保证，不是靠默认值碰巧成立）。
  **腿费归集的数据源**：B-5 的规则要求「上一笔平仓（不含）到本笔平仓（含）的**全部腿**」，而 `buildTradeTrailSegments` 只回 `{entry, exit, direction}`、**丢弃被覆盖的先前开仓腿**——那正是 B-5 要防的漏记。故归集必须在 `const real = trades.filter(t => !t.seeded)` 这份**过滤后的原始数组**上按索引区间累计（与后端在 `sorted(trades, key=id)` 上做的是同一件事），再按 `exit` 回填到对应段；G-7 的双侧夹具必须**包含一笔分两次开仓的部分成交样本**，否则这条规则等于没被验收。
- **F-3 `apps/web/components/Agent/aiTradeStats/AiTradeStatsDetail.tsx`（351 行）**：左栏 KPI 增"已付手续费 / 净额盈亏 / 成本占毛利比"三项；新增一张"交易所对账"卡（差额、差额比例、交易所口径手续费与资金费、样本数、恒等式自检徽标）。老记录缺键 ⇒ **整段隐藏**（绝不显示一排误导性的 0，沿用既有 `wakeScheduler` 的降级范式）。
- **F-4 `apps/web/components/Chart/AiTradePanel.tsx`**：会话盈亏旁加"净额"徽标 + tooltip（口径说明）；有持仓时显示盈亏平衡价。
- **F-5 类型**：`aiTradeTypes.ts:420` 的 `costSnapshot?: Record<string, unknown> | null` 收紧为具名接口 `AiTradeCostSnapshot`（**顶层**含 `cost` / `edgeGate` / `reconcile` / `feeRates`，与 §3.0【v2 · P1-1】的唯一约定一致）；`aiTradeHistoryTypes.ts` 的 `AiTradeHistorySummary` 增 `feeUsd?` / `grossPnlUsd?` / `costBasisMixed?`，详情增 `costBasis?` / `costBasisAssumed?`（【v2 · P0-3】）。全部可选 ⇒ 老后端不下发时优雅降级。
  **收紧类型的一条既有教训**：本仓库的 store 存在「逐键手抄 WS payload」的写法，会**静默吞掉**新增键。收紧 `costSnapshot` 类型时必须同时确认 `reconcile` / `feeRates` 真的透传到了组件（以渲染断言验收，不以 `grep` 到类型声明为准）。

### 3.6 开关矩阵与回退

| 开关 | 默认 | 关闭时的行为 |
|---|---|---|
| `ai_trade_net_pnl_enabled` | **`True`（本轮翻转）** | 逐字回到今天：`_close_pnl` 毛额、`net_unrealized` 恒等、`costBasis` 落 `"gross"` |
| `ai_trade_fee_rate_source_enabled` | `True` | 不发 `trade-fee` 请求，费率恒取配置常数，`source="config"` |
| `ai_trade_cost_awareness_enabled` | `True` | `costAwareness` 整键缺席 + 宪章章为 `""` ⇒ prompt 逐字节回退 |
| `ai_trade_reconcile_enabled` | `True` | 不发 `positions-history` 请求，`reconcile` 键缺席，前端整段隐藏 |
| 既有 `ai_trade_fee_accounting_enabled` | `True`（不改） | 四个费率加法键整键缺席、`fee` 恒 `0.0` ⇒ 净额切换自动退化为恒等 |
| 既有 `ai_trade_edge_gate_k_min` | `0.0`（不改） | 门禁继续空转；轴 D 与轴 A 不受影响（走 `estimate_preview_cost` / `estimate_cost_only`，两者皆不受 `k_min` 门控） |

**总回退**：四个新开关 + 一个翻转项全部置反 ⇒ 系统逐字节回到 commit `359f19a` 的行为（G-8 会以冻结断言验收）。

### 3.7 文件尺寸与行数预算（硬验收判据）

`CLAUDE.md` 红线：单文件 ≤ 800 行、单函数 ≤ 50 行、形参 ≤ 5、圈复杂度 ≤ 10、嵌套 ≤ 4。既有超线文件（`session.py` 1655、`executor.py` 895、`prompts.py` 778、`aiTradeTypes.ts` 783）**本轮只允许薄封装**，业务逻辑一律进新模块。

| 文件 | 当前行数 | 本轮预算 | 判据 |
|---|---|---|---|
| `ai_auto/session.py` | 1655 | **≤ +12** | 3 处调用（inject / refresh_fee_rates / reconcile_if_due）+ 1 处 stop 收尾 + 注释 |
| `live_auto/executor.py` | 895 | **≤ +14** | `_net_unrealized` 方法 + 2 调用点 + 1 字段 + 3 复位点 |
| `live_auto/fee_ledger.py` | 350 | ≤ +50 | `retune` / `net_unrealized` / `session_fee_usd` +【v2 · P2-3】`build_fee_ledger` / `resolve_fee_params` 的 `rates=` 入参 |
| `ai_auto/cost_runtime.py` | 153 | **≤ +95** | 费率刷新 + 对账编排 + inject + `_cost_params` +【v2 · P1-2】`creds` 缝 |
| `ai_auto/cost_model.py` | 187 | ≤ +40 | `break_even_px` +【v2 · P0-1】`query_for_preview` 抽取（`query_for_decision` 变薄包装，净增有限） |
| `ai_auto/prompts.py` | 778 | **≤ +18** | 1 形参 + 1 拼接项 + `_format_cost` 延迟导入委托（渲染主体在 `cost_text.py`） |
| `ai_auto/manager.py` | 704 | ≤ +25 | 预取费率 + 两个 config 键 |
| `okx/trading.py` | 659 | ≤ +75 | 两个 fetch + 两个归一 |
| `db/ai_auto_repositories.py` | 308 | ≤ +55 | `_Trip` + 配对改造 + 三个加法键 |
| 新模块 `ai_auto/fee_rates.py` | — | ≤ 130 | |
| 新模块 `ai_auto/reconcile.py` | — | ≤ 150 | |
| 新模块 `ai_auto/cost_text.py` | — | ≤ 170 | |
| 新 lib `apps/web/lib/aiTradeCost.ts` | — | ≤ 140 | |

机械判据：`git diff --numstat` 逐文件核对上表，任一项超出即判不合格（沿用阶段一 Z-8 的验收方式）。

---

## 四、文件 / 模块变更计划

### 4.1 后端（`services/quant`）

| # | 文件 | 动作 | 一句话意图 |
|---|---|---|---|
| 1 | `app/okx/trade_client.py` | 改 | 新增 `get_trade_fee` / `get_position_history` 两个签名 GET（复用基类 `_get`） |
| 2 | `app/okx/trading.py` | 改 | 新增 `fetch_trade_fee` / `fetch_position_history` 与两个归一函数（错误映射复用 `_map_trade_code`/`_redact`） |
| 3 | `app/ai_auto/fee_rates.py` | **新增** | `FeeRateSnapshot` / `FeeRateCache` / `resolve_fee_rates`（镜像 `funding.py` 三条不变式） |
| 4 | `app/ai_auto/reconcile.py` | **新增** | 交易所对账纯核心：窗口过滤、差额、漂移判定、恒等式自检 |
| 5 | `app/ai_auto/cost_text.py` | **新增** | 成本宪章章 + 每轮成本段 + 回测免责（中文文案唯一真源） |
| 6 | `app/ai_auto/cost_model.py` | 改 | 新增 `break_even_px`；抽出 `query_for_preview`（`query_for_decision` 变薄包装，P0-1）；`resolve_cost_params` 支持官方费率覆盖 |
| 7 | `app/ai_auto/cost_runtime.py` | 改 | 费率刷新 / 对账编排 / `inject` / `estimate_preview_cost` / `_cost_params` + 新增 `creds` 构造缝（P1-2） |
| 8 | `app/live_auto/fee_ledger.py` | 改 | `retune`（热更新，不回改已记账）、`net_unrealized`、`session_fee_usd` 累计 |
| 9 | `app/live_auto/executor.py` | 改 | `_net_unrealized` + 两调用点 + `gross_unrealized_usd` 字段 + 三处复位 |
| 10 | `app/ai_auto/session.py` | 改 | 四行薄封装：payload 注入、费率刷新、对账节流、stop 收尾强制对账 |
| 11 | `app/ai_auto/manager.py` | 改 | `_gather_context` 预取官方费率并注入 `config_map`；经 `_MemberPlan.fee_rates_wire` 下传（P1-7）；run config 落 `costBasis` / `feeRates` |
| 12 | `app/ai_auto/prompts.py` | 改 | `cost_text` 宪章形参 + `_format_cost` 每轮段（渲染委托 `cost_text.py`） |
| 13 | `app/ai_auto/trace.py` | 改 | `costSnapshot` 增 `reconcile` / `feeRates` 段（`PHASES` 不变，不新增阶段） |
| 14 | `app/ai_auto/stats_ext.py` | 改 | `build_cost_stats` 并入 `feeRates` / `reconcile` / `sessionFeeUsd` |
| 15 | `app/db/ai_auto_repositories.py` | 改 | `_Trip` 配对改造 + 按 `costBasis` 还原净额 + 三个加法键 |
| 16 | `app/schemas.py` | 改 | 历史汇总 / 详情增可选成本字段（`feeUsd` / `grossPnlUsd` / `costBasisMixed` / `costBasis`） |
| 17 | `app/config.py` | 改 | 翻转 `ai_trade_net_pnl_enabled`；新增 8 个 `ai_trade_*` 配置项（§5.5） |
| 18 | `.env.example` | 改 | 追加新开关的注释样例（`AI_TRADE_` 前缀已在 desktop 透传白名单内，`desktop/src/lib/config.js` 无需改） |

### 4.2 前端（`apps/web`）

| # | 文件 | 动作 | 一句话意图 |
|---|---|---|---|
| 19 | `lib/aiTradeCost.ts` | **新增** | 成本 / 对账读数的中文化与派生（纯函数） |
| 20 | `lib/autoTradePnl.ts` | 改 | 往返净额规则镜像（`costBasis` 可选参 + `feeUsd` / `grossPnlUsd`） |
| 21 | `lib/aiTradeTypes.ts` | 改 | `AiTradeCostSnapshot` 具名接口（含 `reconcile` / `feeRates`） |
| 22 | `lib/aiTradeHistoryTypes.ts` | 改 | 历史汇总 / 详情的成本可选键 |
| 23 | `components/Agent/aiTradeStats/AiTradeStatsDetail.tsx` | 改 | 成本 KPI 三项 + 交易所对账卡（缺键整段隐藏） |
| 24 | `components/Chart/AiTradePanel.tsx` | 改 | 会话盈亏"净额"徽标 + 持仓盈亏平衡价 |

### 4.3 测试

| # | 文件 | 动作 | 覆盖 |
|---|---|---|---|
| 25 | `tests/test_ai_trade_fee_rates.py` | **新增** | 归一四分支 / 空串回落 / 符号 / 返佣夹紧 / TTL / 禁用零请求 / `retune` 不回改 |
| 26 | `tests/test_ai_trade_reconcile.py` | **新增** | ok / drift / partial / unavailable / coverageMismatch / 恒等式自检真假两侧 / **可比口径的资金费夹具（G-15）** / **`final=True` 零事件（G-16）** |
| 27 | `tests/test_ai_trade_cost_text.py` | **新增** | 宪章与每轮段渲染、缺键整段省略、平衡价与杠杆无关 |
| 28 | `tests/test_ai_trade_net_pnl.py` | **新增** | 已实现/未实现净额、三处复位、`live_auto` 逐字节回退、总闸关恒等 |
| 29 | `tests/test_ai_trade_fee_ledger.py` | 改 | 扩 `retune` / `net_unrealized` / `session_fee_usd` |
| 30 | `tests/test_ai_trade_cost_gate_session.py` | 改 | 扩 payload `costAwareness` 键、对账节流、stop 收尾强制对账 |
| 31 | `tests/test_ai_auto_history.py` | 改 | 扩 `costBasis` **四代行**聚合（P0-3）+ `captureRatio` 毛额口径（P1-3）；**该文件已有 4 键形状断言，必须同步** |
| 32 | `apps/web/lib/aiTradeCost.test.ts` | **新增** | 中文化与派生纯函数 |
| 33 | `apps/web/lib/autoTradePnl.test.ts` | 改 | 净额镜像 + 非 AI 逐字节回退 |
| 34 | `apps/web/components/Agent/AiTradeStatsPanel.test.tsx` | 改 | 成本 KPI 与对账卡的渲染 / 缺键隐藏 |

---

## 五、接口设计

### 5.1 OKX 官方端点（新增消费两条）

```
GET /api/v5/account/trade-fee?instType=SWAP&instId=BTC-USDT-SWAP        （签名，读权限）
  → data[0]: { level, ruleType, maker, taker, makerU, takerU, makerUSDC, takerUSDC, ts, … }
GET /api/v5/account/positions-history?instType=SWAP&instId=…&limit=50   （签名）
  → data[]:  { posId, instId, direction, openAvgPx, closeAvgPx, cTime, uTime,
               pnl, fee, fundingFee, liqPenalty, realizedPnl, … }
```

### 5.2 内部函数签名（新增）

```python
# okx/trading.py
async def fetch_trade_fee(creds, inst_id: str, *, inst_type: str = "SWAP") -> dict | None
async def fetch_position_history(creds, inst_id: str, *, limit: int = 50,
                                 after_ms: int | None = None) -> list[dict]

# ai_auto/fee_rates.py
def resolve_fee_rates(settings, snapshot) -> tuple[float, float, str]
class FeeRateCache:  # __init__(inst_id, *, ttl_s, enabled); async refresh(fetch_fn, now_wall) -> bool
    def snapshot(self) -> FeeRateSnapshot | None
    def wire(self) -> dict | None

# ai_auto/reconcile.py
def build_reconciliation(*, local_net_usd, local_trips, rows, window_start_ts, params,
                         final: bool = False) -> dict      # final=True ⇒ 永不判 drift（P1-5）

# ai_auto/cost_model.py
def break_even_px(entry_px: float, *, side: str, cost_bps: float) -> float | None
def query_for_preview(*, ord_type, side, bar_seconds, funding, funding_enabled,
                      half_spread_bps=None) -> CostQuery   # P0-1；query_for_decision 变其薄包装

# live_auto/fee_ledger.py
def retune(self, *, maker_bps: float, taker_bps: float, source: str) -> None
def net_unrealized(self, gross: float, *, entry_fee_usd, mark_px, sz) -> float
@property
def session_fee_usd(self) -> float

# ai_auto/cost_runtime.py
def __init__(self, inst_id, settings, fee_ledger, creds=None, *,
             allow_limit: bool = False) -> None            # P1-2 凭证缝 + P0-1 开仓腿意图
async def refresh_fee_rates(self, now_wall: float) -> bool
async def reconcile_if_due(self, now_wall: float, *, window_start_ts: int,
                           local_view: dict, force: bool = False) -> dict | None
def estimate_preview_cost(self, *, ord_type: str, side: str,
                          bar_seconds: int) -> CostBreakdown | None      # P0-1
def inject(self, payload: dict, *, last_px, atr_px, bar_seconds) -> None
def charter_text(self, bar_seconds: int) -> str
```

**`local_view` 的形状（【v2 · P0-2】，v1 未定义）**：
```python
{"netUsd": float,     # 窗口内**已平仓往返**净额之和（Σ trip.net_pnl），绝不含未实现
 "trips": int,        # 同一窗口内的本地往返笔数（coverageMismatch 的分母）
 "windowStartTs": int}
```

### 5.3 WebSocket 事件（无新 `type`，全部为既有事件的加法键）

- `status` / `edge_gate` 事件上的 `costSnapshot` 增两段：`feeRates`、`reconcile`（形状见 §5.4）。
- 新错误码 `AI_TRADE_PNL_RECONCILE_DRIFT`（`type: "error"`）：连续 N 次对账漂移。文案模板：
  `「本地盈亏与交易所结算连续 {n} 次对不上：本地 {local} USDT / 交易所 {exch} USDT（差 {diff}，{ratio}）。已付手续费 {fee}、资金费 {funding}。系统只观测不改写盈亏，请核对是否存在非本会话的手工持仓」`
- 新错误码 `AI_TRADE_FEE_RATE_UNAVAILABLE`（`type: "status"`，会话内至多一次）：官方费率取数失败已回落配置常数，附当前 maker/taker 值。

### 5.4 wire 形状（camelCase，前后端逐字对齐）

```jsonc
// payload["costAwareness"]（决策轮；总闸关 ⇒ 整键缺席）
{ "roundTripBps": 12.5, "roundTripPct": 0.00125, "feeOpenBps": 2.0, "feeCloseBps": 5.0,
  "halfSpreadBps": 1.0, "impactBps": 0.5, "crossingLegs": 1, "fundingCarryBps": 1.0,
  "confidence": "ok", "source": "exchange", "level": "Lv1",
  "previewOrdType": "limit", "previewSide": "long",     // 【v2 · P0-1】预览假设，必须显式下发
  "breakEvenPx": 68432.1, "breakEvenGapPct": 0.0009,   // 无持仓 ⇒ 两键为 null
  "atrShare": 0.18,                                     // 成本 / ATR(bps)；ATR 不可用 ⇒ null
  "sessionFeeUsd": 12.34, "netBasis": true }

// costSnapshot.feeRates（顶层，【v2 · P1-1】）
{ "makerBps": 2.0, "takerBps": 5.0, "level": "Lv1", "ruleType": "normal",
  "source": "exchange", "rebate": false, "ageS": 42.0, "stale": false,
  "makerBpsRaw": 2.0, "takerBpsRaw": 5.0 }             // 【v2 · P1-4】未夹紧的原值，仅供审计

// costSnapshot.reconcile（顶层，【v2 · P1-1】）
{ "status": "ok",                    // ok | drift | partial | unavailable（partial = P1-5 的终态少一笔）
  "samples": 7, "localTrips": 7, "coverageMismatch": false, "final": false,
  "localNetUsd": -8.42,                                 // 已平仓往返净额之和，绝不含未实现（P0-2）
  "exchangeRealizedUsd": -8.55,                         // 原始读数，含资金费与罚金，**不参与判定**
  "exchangeComparableUsd": -8.30,                       // = realized + funding + liqPenalty（P0-2）
  "diffUsd": -0.12, "diffRatio": 0.0145,                // 对 comparable 作差
  "exchangeFeeUsd": 21.4, "exchangeFundingFeeUsd": 1.2, "exchangeLiqPenaltyUsd": 0.0,
  "identityHolds": true, "identityMaxGapUsd": 0.000004,
  "windowStartTs": 1754400000, "asOfTs": 1754431200 }

// costSnapshot.cost（既有对象的加法键）
{ /* …既有 measuredFeeBps / feeDeviationBps / slipBps 等… */ "sessionFeeUsd": 12.34 }
```

### 5.5 配置项（`services/quant/app/config.py`，全部 `AI_TRADE_` 前缀，desktop 白名单已覆盖）

```python
ai_trade_net_pnl_enabled: bool = True                                   # 翻转（B-1）
ai_trade_fee_rate_source_enabled: bool = True                           # 轴 A 总闸
ai_trade_fee_rate_ttl_s: float = Field(default=3600.0, gt=0, le=86400)
ai_trade_cost_awareness_enabled: bool = True                            # 轴 D 总闸
ai_trade_reconcile_enabled: bool = True                                 # 轴 C 总闸
ai_trade_reconcile_interval_s: float = Field(default=300.0, gt=0, le=86400)
ai_trade_reconcile_abs_tol_usd: float = Field(default=0.5, ge=0)
ai_trade_reconcile_rel_tol: float = Field(default=0.05, ge=0, le=1)
ai_trade_reconcile_alert_streak: int = Field(default=3, ge=1, le=100)
# 【v2 · P1-5】stop 收尾强制对账的硬超时：观测层绝不延长真实资金平仓后的落库收尾。
ai_trade_reconcile_stop_timeout_s: float = Field(default=3.0, gt=0, le=30)
# 【v2 · P0-3】老 run（config_json 缺 costBasis）的口径推定。默认 "gross" 对出厂配置正确；
# 已按阶段一 §7.5 runbook 提前打开 ai_trade_net_pnl_enabled 的部署置 "net"，否则那批行会被二次扣费。
# 回执恒带 costBasisAssumed=true，UI 必须把「推定」与「已知」分开显示。
ai_trade_history_legacy_cost_basis: str = "gross"        # gross | net
```

共 **11** 个配置项（v1 为 9 项 + v2 新增 2 项）。全部 `AI_TRADE_` 前缀，desktop `config.js` 的 prod 透传白名单按前缀已覆盖（`desktop/src/lib/config.js:74` 实测）；但 `Settings` 的 `extra="ignore"` 会**静默丢弃未声明的 env**，故 11 项必须逐个真的声明在 `config.py` 里。

### 5.6 HTTP（无新端点）

`GET /api/ai-trade/history` 与 `/history/{groupId}` 的回执**只增可选字段**（§4.1 #16）：汇总加 `feeUsd` / `grossPnlUsd` / `costBasisMixed`，详情加 `costBasis`。老前端读不到新键即降级，不破坏契约。

---

## 六、数据模型

**零 DB 迁移**——本 spec 不新增任何表或列。

| 载体 | 键 | 说明 |
|---|---|---|
| `strategy_runs.config_json`（既有 JSON 列） | `costBasis` | `"net"` / `"gross"`，会话级静态口径标记（B-4） |
| 同上 | `feeRates` | 启动时的官方费率快照（含 `level` / `source`），事后审计"当时按什么费率记的账" |
| `strategy_runs.metrics_json.aiStats`（既有） | `feeRates` / `reconcile` | 会话终态的费率与对账落库（**顶层**加法键，与 `costSnapshot` 同形，【v2 · P1-1】；`merge_cost_stats` 键冲突即抛，须前置核对不与既有 `aiStats` 键撞名） |
| `strategy_runs.metrics_json.aiStats.cost`（既有） | `sessionFeeUsd` | 会话累计已付手续费（与 `measuredFeeBps` 同族，故挂 `cost` 子对象） |
| `trades.fee`（既有列） | — | 每腿确定性费率，已在写；本轮成为净额还原的唯一依据 |
| 进程内 | `FeeRateCache` / `AttributionLedger` / `reconcile` 最近读数 | 会话生命周期，随会话回收 |

**老数据兼容**：四代行的处置见 §3.2 B-4（【v2 · P0-3】把 v1 的三代补成四代）。第 ① 代（`fee = 0`）在净额还原后与今天数值相同——**这是诚实的降级**：那些会话确实没有记过费用，任何"补算"都是伪造。第 ④ 代（缺 `costBasis` 但已是净额）靠 `ai_trade_history_legacy_cost_basis` 一次性声明，回执恒带 `costBasisAssumed`。前端对 `costBasisMixed` 与 `costBasisAssumed` 分别打徽标：前者说"这组里有两种口径"，后者说"这组的口径是推定的"——两件事不可合并成一个徽标。

---

## 七、测试与验收标准

### 7.1 验收判据（G 系列，逐条可机械核对）

| 编号 | 判据 |
|---|---|
| **G-1** | 开启净额口径后，一笔完整往返的 `trade["pnl"]` 恰等于 `毛价差 − 开仓腿费 − 平仓腿费`，误差 < 1e-9；`session_pnl_usd` 与之一致 |
| **G-2** | 持仓期间 `position_payload().unrealizedPnl` 恰等于 `upl − entry_fee_usd − 按标记价估的平仓腿费`；**【v2 · P1-8】** 且 `unrealized_usd == 0.0` 触发 `or` 回退支时，回退值与主值同为净额（无口径跳变） |
| **G-3** | **【v2 · P0-1 改判】** `(previewOrdType, previewSide)` 与本轮决策的 `(ord_type, side)` 一致时，prompt 的 `roundTripBps`、edge 门禁 wire 的 `costBps`、仓位层收到的 `cost_bps` 三者**逐位相同**；不一致时断言三者读同一份 `CostParams`（A-6 单一真源仍成立）且 payload 已如实标出两个预览假设 |
| **G-4** | 官方费率取数成功 ⇒ `FeeLedger.params` 与 `CostParams` 的 maker/taker **同为**交易所返回值；取数失败 ⇒ 两者同为配置常数且 `source == "config"`，会话照常启动 |
| **G-5** | `retune` 之后，此前已记账的 `trade["fee"]` 与 `entry_fee_usd` **一位不变**（A-4a） |
| **G-6** | 历史聚合对**四**代行分别给出：①=今日值、②=`Σ(pnl − legFee)`、③=`Σpnl`、**④【v2 · P0-3】** `ai_trade_history_legacy_cost_basis="net"` 时 = `Σpnl`（不二次扣费）、为 `"gross"` 时 = `Σ(pnl − legFee)`；混合组 `costBasisMixed == true`，缺 `costBasis` 的组 `costBasisAssumed == true` |
| **G-7** | 前端 `buildRoundTrips` 与后端 `_Trip.net_pnl` 对同一批 `trades` 给出**逐笔相同**的净额（同一组夹具双侧断言） |
| **G-8** | 四个新开关全关 + `net_pnl` 置 `false` ⇒ 决策 payload 与系统 prompt 与 `359f19a` **逐字节相同**（冻结断言，沿用既有 sha256 门禁范式） |
| **G-9** | `ai_trade_reconcile_enabled=false` 或无凭证 ⇒ `positions-history` **一个请求都不发**（调用计数上界断言） |
| **G-10** | 对账 `samples == 0` ⇒ `status == "unavailable"`，且 `diffUsd` 键为 `null`（不报 0 差额） |
| **G-11** | 恒等式自检对构造出的违例样本给出 `identityHolds == false`，且不影响 `diffUsd` 的计算 |
| **G-12** | `live_auto`（非 AI）链路的成交记录、会话盈亏、WS payload **逐字节不变**（`fee_ledger is None` 全路径） |
| **G-13** | §3.7 行数预算逐文件满足（`git diff --numstat` 核对） |
| **G-14【v2 · P1-3】** | 同一组夹具在 `net_pnl_enabled` 真假两侧，`captureRatio` / `capturedPnlUsd` / `peakPnlUsd` **逐位相同**（捕获率留在毛额口径），而 `realizedPnlUsd` 相差恰好一个 `feeUsd` |
| **G-15【v2 · P0-2】** | 构造「一笔持仓跨过 1 次资金费结算、资金费支出 X USDT」的对账夹具：`diffUsd` ≈ 0（不因资金费误报），`exchangeFundingFeeUsd == X`，且把 X 调到远超 `abs_tol` 时 `status` 仍为 `"ok"` |
| **G-16【v2 · P1-5】** | `final=True` 的对账在 `samples < local_trips` 时给出 `status == "partial"`、**不推进** `alert_streak`、**不发** `AI_TRADE_PNL_RECONCILE_DRIFT`；且强制对账被人为拖到超时时 `stop()` 的总耗时增量 ≤ `ai_trade_reconcile_stop_timeout_s` |
| **G-17【v2 · P1-4】** | 交易所返回返佣档位（`rate > 0`）时，`FeeLedger.params` 的两个 bps 与 `CostParams` 的两个 bps **同为 `0.0` 或正数**，`trade["fee"] >= 0` 恒成立；原始负值只出现在 `makerBpsRaw` / `takerBpsRaw` |
| **G-18【v2 · P1-2】** | `creds is None` 构造的 `CostRuntime`（既有直接构造会话的单测路径）⇒ `trade-fee` 与 `positions-history` **一个请求都不发**，且既有单测逐条仍绿 |

### 7.2 关键用例（节选）

**费率归一**：`makerU="-0.0002"` / `takerU="-0.0005"` ⇒ `(2.0, 5.0)`；`makerU=""` 且 `maker="-0.0002"` ⇒ 回落成功；两者皆空 ⇒ `None` ⇒ 回落配置常数；`maker="0.0001"`（返佣）⇒ wire 记 `-1.0` 且 `rebate=true`，供模型消费侧夹紧为 `0.0`；`BTC-USDC-SWAP` 走 `makerUSDC/takerUSDC`。

**净额记账**：`ct_val=0.01`、`sz=10`、`entry=60000`、`exit=60600`、taker 双边 5 bps ⇒ 毛 60.0、双边费 `6000×5/1e4 + 6060×5/1e4 = 3.0 + 3.03 = 6.03` ⇒ 净 53.97。同一夹具在总闸关时必须回到 60.0。

**部分成交的费用归集**：开仓分两笔（3 张 + 7 张）后一次平仓 ⇒ `leg_fee_usd` 为三腿之和；断言不等于"仅最后一条开仓腿 + 平仓腿"（这正是 B-5 要防的漏记）。

**对账**：本地净 −8.42、交易所 `realizedPnl` 合计 −8.55 且 `fundingFee` 支出 0.25 ⇒ `exchangeComparableUsd = −8.30`、`diffUsd = −0.12`、`abs_tol=0.5` ⇒ `ok`（**【v2 · P0-2】** 按 v1 的公式此处 `diffUsd = 0.13`，看似也过，但把资金费调到 0.8 就会在 v1 下误报 `drift` 而在 v2 下仍为 `ok` —— 这正是 G-15 的夹具）；把交易所可比口径改为 −20.0 ⇒ `drift` 且连续 3 次推 `AI_TRADE_PNL_RECONCILE_DRIFT`；`samples=0` ⇒ `unavailable`；`localTrips=7 / samples=9` ⇒ `coverageMismatch=true` 但仍出差额；同一组入参加 `final=True` ⇒ `partial` 且零事件（G-16）。

**【v2 · P1-8】影子策略权益**：同一根 K 线、同一持仓下对拍 `context().equity` 在 `fee_ledger is None` 与已装配两种执行器上的取值，断言前者逐位不变、后者恰少一个「已付开仓费 + 预估平仓费」；并补一条"按权益缩仓的策略在净额下产出不同 sizeFraction"的显式用例——**这不是回归，是本轮有意的语义变更**，用例存在的意义是让它变化时有人看得见。

**prompt**：`costAwareness` 齐全 ⇒ 段内出现往返 bps、折价、平衡价三要素；`atrShare` 为 `null` ⇒ 该子句省略而非写"未知"；总闸关 ⇒ 整段消失且 `build_decision_prompt` 输出与冻结样本逐字节相同。

### 7.3 回归基线（跑测前必须先知道，否则会误判为自己的回归）

- `services/quant` 全量存在 **pre-existing 红灯**：`tests/test_factor_library_expansion.py`（未跟踪的孤儿测试）与 `tests/test_ai_trade_prior_session.py` 系列（**【v2 · P2-2】** v1 误写作 `test_prior_session.py`）；`test_agent_ws` / `test_agent_manager_detach` 在全量负载下偶发计时 flake、单跑必绿。
- **全量 pytest 不可并发**（共用临时 DB），且 `pytest -q` 块缓冲，须 `python -u … > log`。
- 前端组件测试须 `NODE_ENV=test`；`WeightBatchArena.test.tsx` 的 `tsc` 报错是 pre-existing。
- 实施节点必须**先在干净 HEAD worktree 实测一次红灯基线并写进 §十二**，再开始改动。

---

## 八、风险与缓解

| 编号 | 风险 | 影响 | 缓解 |
|---|---|---|---|
| **R-1** | 净额默认开 = 破坏性口径变更，用户会看到"盈亏突然变差了" | 误判为新 bug | ① 前端明确的"净额（已扣手续费）"徽标 + tooltip；② 同时露出 `grossPnlUsd` 与 `feeUsd`，让下降量可被逐笔解释；③ 发布说明写清"数字下移的正是你本来就已经付掉的钱" |
| **R-2** | 官方费率接口不可用 / API Key 权限不足 / 限频 429 | 启动被拖慢或失败 | 取数并入既有 `live_auto_start_concurrency` 信号量（F-18）；失败一律回落常数 + 一条 status 提示，**绝不阻断启动**；TTL 3600s 使稳态期每小时至多 1 次/标的。**【v2 · P2-7】稳态期私有配额估算**：轴 A 每标的每小时 1 次 + 轴 C 每标的每 `ai_trade_reconcile_interval_s`（默认 300s）1 次 ⇒ 5 标的分组稳态约 1 req/min，远低于 OKX 私有限频，但实施节点须在 §十二 回执实测值 |
| **R-3** | 同标的上存在**非本会话**的手工持仓 | 对账差额被污染 | `coverageMismatch` 标记 + 告警文案点名该成因；对账只观测不回写（C-5），故污染不会传导进任何判定 |
| **R-4** | `positions-history` 的持仓级边界与本地往返级不一一对应（`net_mode` 反手、部分平仓） | 差额出现结构性偏差 | 以**窗口合计**而非逐笔匹配对账；`samples` / `localTrips` 双计数并列下发；容差按绝对值与比例取大 |
| **R-5** | 恒等式 `realizedPnl = pnl + fee + fundingFee + liqPenalty` 未能从官方页面逐字取回 | 依赖错误假设 | **不把它当前提**：以 `realizedPnl` 为锚点 + 运行期数值自检（C-3），自检不过如实标注 |
| **R-6** | 未实现盈亏净额化会让会话止损略微提前 | 行为变化 | 变化量恰为一个往返成本，属期望语义（B-3 逐条确认表）；利润保护层刻意不动，避免破坏其单调棘轮承诺 |
| **R-7** | prompt 变长 ⇒ token 成本与首字延迟上升 | 运营成本 | 成本段压到 3~4 行；宪章章只在会话启动装配一次；总闸可单独关 |
| **R-8** | 币本位 / USDC 合约的费率与 fee 计价币量纲 | 记账量纲错误 | 归一按 `instId` 后缀选分档字段（A-2 第 1 步）；实测审计侧既有的 `_FEE_CCY` 过滤保持不变（非 USDT 计价样本计入 `feeAuditSkipped`） |
| **R-9** | 费率热更新回改历史记账 | 平仓净额与开仓承诺不符 | A-4a 不变式 + G-5 断言 |
| **R-10** | 前后端两套净额公式漂移 | 看板与后端对不上 | 同一组夹具双侧断言（G-7）；前端默认 `costBasis="gross"` 且非 AI 场景 `fee≡0` ⇒ 结构性零回归（F-2） |
| **R-11** | `session.py` / `executor.py` 继续增肥 | 触碰 800 行红线更深 | §3.7 行数预算 + `git diff --numstat` 机械判据；业务逻辑一律进新模块 |
| **R-12** | 资金费率仍未进本地已实现盈亏 | 长持仓的本地净额仍偏乐观 | 有意选择（§一"不做什么"①）；由对账层的 `exchangeFundingFeeUsd` 显式呈现，并已通过成本模型进入决策；若后续要入账，唯一正确的落点是本 spec 的对账层而非本地账本 |

---

## 九、实施顺序与放行条件

顺序**不可颠倒**（每一步都为下一步提供可验证的地基）：

1. **轴 A**（费率真源）——先让"成本是多少"这个数变准，否则后面三轴都建在猜测上。放行：G-4 / G-5 / **G-17**。
2. **轴 B**（净额贯通）——`executor` → 历史聚合 → 前端镜像。放行：G-1 / G-2 / G-6 / G-7 / G-12 / **G-14**。
3. **轴 D**（Agent 成本意识）——依赖轴 A 的数与轴 B 的口径声明。放行：G-3 / G-8。
4. **轴 C**（交易所对账）——最后接，因为它的价值是**验证前三轴**。放行：G-9 / G-10 / G-11 / **G-15** / **G-16** / **G-18**。

**总放行条件**（Subtask #2 完成的定义）：**G-1 ~ G-18**（v2 由 13 条增至 18 条）全部通过；后端与前端测试相对 §7.3 基线**零新增红灯**；`ruff` 无新增告警；`tsc` 无新增错误；§3.7 行数预算逐项达标；§〇 的 7 条 P2 逐条在 §十二 给出处置结论；实施中发现的方案缺陷全部写入 §十二。

---

## 十、与既有确定性层的关系（避免重复造层）

| 既有层 | 回答的问题 | 与本 spec 的关系 |
|---|---|---|
| 基础策略铁律 `base_rules.py` | 能不能在这里开 | 正交，不改 |
| 下单前反思 `reflection.py` | 这笔有没有实质缺陷 | 自动继承成本证据（D-4） |
| 利润保护棘轮 `profit_guard.py` | 利润何时必须锁 | 有意不动（口径独立，§一"不做什么"②） |
| 仓位 / 刹车 `sizing.py` | 下多大注 | 消费同一份 `cost_bps`（A-6 单一真源受益方） |
| 最小 edge 门禁 `edge_gate.py` | 期望收益盖不盖得住成本 | 成本入参变准、变得可解释；判定式与 `k_min` 默认值不动 |
| **本 spec** | **成本到底是多少、盈亏到底是多少、模型知不知道** | 前五层共同依赖的**计量基座** |

---

## 十一、术语与口径约定（防止下游误读）

- **毛额（gross）**：纯价差 × 数量 × 合约乘数，不含任何费用。
- **净额（net）**：毛额 − 开仓腿费 − 平仓腿费（**不含**资金费与强平罚金——那两项只出现在交易所对账口径）。
- **交易所口径（exchangeRealized）**：OKX `positions-history.realizedPnl`，含手续费、资金费与强平罚金。
- **往返成本 C（bps）**：`feeOpen + feeClose + crossingLegs × (halfSpread + impact) + fundingCarry`，`cost_model.estimate_round_trip_cost` 是唯一真源。
- **盈亏平衡价**：价格朝持仓方向走过 C bps 的价位，**与杠杆无关**。
- **交易所可比口径（exchangeComparable）【v2 · P0-2】**：`exchangeRealizedUsd + exchangeFundingFeeUsd + exchangeLiqPenaltyUsd`（后两项已归一为"支出为正"），即把本地账本按定义不记的两项加回去，使其与本地**净额**同维。
- 三个口径的关系：`净额 ≈ 交易所可比口径`，展开即 `净额 ≈ 交易所口径 + 资金费 + 强平罚金`（符号按"支出为正"）。对账层的 `diffUsd` 度量的是**这个"≈"**，而**不是**本地净额与 `realizedPnl` 的原始之差——后者恒含一整个资金费，用它作差会让 drift 告警按设计恒响（v1 的 C-2 即如此，已修）。

---

## 十二、实施过程发现的方案缺陷

> 本段由 Subtask #2（代码开发）在实施过程中就地追加：编号 I-1、I-2…，标注级别（阻断 / 一般），写清"方案怎么说的、实际怎么样、如何处置"。设计节点留空。
> 评审补充：§〇 的 7 条 P2 亦在此回执（每条一行："处置 / 不处置 + 理由"），这是总放行条件之一。

### 12.0 前置动作：干净 HEAD worktree 的红灯基线（放行条件 #12，实测）

在 `HEAD = 359f19a` 新建 detached worktree 实跑 `python -u -m pytest -q`（`services/quant`），
**480.58s，8 failed / 2545 passed / 1 skipped / 2 deselected**：

| # | 用例 | 说明 |
|---|---|---|
| 1-7 | `tests/test_ai_trade_prior_session.py`（7 条全红） | 与 §7.3 的记载一致（【v2 · P2-2】的真名亦属实） |
| 8 | `tests/test_ai_backtest_manager.py::test_profit_rule_is_forwarded_to_spec` | §7.3 **未记载**的第 8 条 pre-existing 红灯，实测 HEAD 即红 |

**一条对下游有用的差异**：§7.3 提到的 `tests/test_factor_library_expansion.py` 是**未跟踪文件**，
故它在干净 worktree 里根本不存在、也就不会红；在**当前工作树**跑全量时它会额外贡献 7 条红灯。

### 12.0b 实施后的全量回归（同一工作树，`285.46s`）

**15 failed / 2621 passed / 1 skipped / 2 deselected**，逐条与基线对齐、**零新增红灯**：

| 类别 | 条数 | 判定 |
|---|---|---|
| `tests/test_ai_trade_prior_session.py` | 7 | 与 §12.0 基线逐条相同（pre-existing） |
| `tests/test_factor_library_expansion.py` | 7 | 未跟踪孤儿测试，只在本工作树出现（pre-existing） |
| `tests/test_agent_ws.py::test_ws_emits_ready_history_and_full_run` | 1 | §7.3 已记载的全量负载计时 flake；**单跑会挂住**（本仓库既有现象），故不可用单跑证伪 |

`tests/test_ai_backtest_manager.py::test_profit_rule_is_forwarded_to_spec` 在干净 HEAD 上红、在本次
工作树全量里**绿**——它是顺序相关的既有不稳定用例，两侧都与本轮改动无关。

通过数由基线的 2545 增至 2621（+76 条本轮新增用例）。前端 `vitest` 136 文件 / **1470 passed**；
`tsc` 仅剩 §7.3 记载的 pre-existing `WeightBatchArena.test.tsx` 错误；`ruff` 对本轮触及的全部
26 个文件 **All checks passed**。

---

### 12.1 实施过程发现的方案缺陷

**I-1（阻断级）· 盯市 tick 的网络 IO 预算被三条串行 await 击穿**

- **方案怎么说的**：§3.1 A-4 与 §3.3 C-4 都把新的网络刷新挂在 5s 盯市 tick 上「紧邻既有
  `refresh_funding`（`session.py:438`）」，理由是「网络 IO 只跑在盯市 tick 上」这条既有范式。
- **实际怎么样**：阶段一之所以可以直接 `await refresh_funding`，是因为它在默认配置下**一个请求都不发**
  （`k_min=0` ⇒ `FundingCache(enabled=False)`）。本轮 §3.2 B-6 把它打开、又新增了轴 A 的 `trade-fee`
  与轴 C 的 `positions-history` **两条签名私有 GET**（`private_client._get` 各自带 3 次指数退避重试）。
  实测三条串行耗时 **1.75s + 1.36s + 1.39s ≈ 4.5s**，在一个 5s 周期的 tick 上等于把盯市周期翻倍，
  并推迟下一轮利润保护判定——既有用例
  `test_ai_trade_wake_context_session.py::test_g7_a_cpu_bound_preview_never_stalls_the_profit_guard_on_the_same_tick`
  断言单 tick ≤ 1.0s，按 spec 字面实现后实测 **5.1s，必红**。这不是测试太严，而是那条用例守的正是
  「盯市 tick 不得被拖停」这条不变式。
- **如何处置**：新增 `CostRuntime.schedule_maintenance(now_wall, *, window_start_ts, trades, push)`
  ——**同步函数**，把三条刷新排进一个后台任务（单飞：上一批未完成即跳过本轮，绝不堆积）；`session`
  用一条非阻塞调用替换原先的 `await refresh_funding`，tick 关键路径回到零网络等待。`stop()` 收尾的
  强制对账先 `cancel()` 并 `gather()` 在途任务再跑（它可能正卡在退避重试上；不 gather 会在事件循环
  关闭时留下未展开的协程），随后仍按 P1-5 包 `wait_for` 硬超时。
  受影响的既有断言（`io_calls["funding"] == 1`）改为先 `await session._cost.drain_maintenance()` 再断言
  ——**「只在盯市 tick 上触网」与「调用次数上界」两条性质都原样成立**，只是需要等后台那一批落地。
- **随之暴露的第二个洞（同属 I-1）**：单条私有 GET 的上界是
  `okx_account_timeout_seconds`(15s) × 3 次退避重试，三条串起来最坏可达数分钟——那期间**单飞槽位
  一直被占**，等于整个成本层此后静默停更。故整批再包一层 `_MAINTENANCE_BUDGET_S = 30s` 的总上界，
  超时即放弃本批（三步各自幂等且按 TTL 节流，下一轮照常重试）。这条在实测中不是理论风险：未加
  stub 时 `test_ai_trade_wake_context_session` / `test_ai_trade_session` 会真的去打 OKX，全量回归
  在该处停滞逾 5 分钟且 CPU 归零（实测 PID 级确认）。两处会话用例已按本文件既有的「脱网、桩回调」
  约定补上 `_fetch_fee_rate` / `_fetch_position_history` 两个桩。

**I-2（一般）· `build_reconciliation` 的签名缺 `inst_id`，而判定链①要求按 instId 过滤**

§5.2 给的签名没有标的入参，§3.3 C-2 的判定链①却写「过滤 `uTime >= window_start_ts` **且 `instId` 匹配**
的行」。处置：末位加可选 `inst_id: str | None = None`（签名的严格超集，不传即不过滤），`CostRuntime`
恒传自己的标的。取数本就按 instId 发起，这层是二次保险，用例
`test_rows_before_the_window_and_other_symbols_are_filtered_out` 钉住。

**I-3（一般）· 轴 D 的注入需要当前持仓，而 §5.2 的 `inject` 签名里没有**

D-3a 的预览方向规则是「**有持仓取持仓方向**」、平衡价也要 `avgEntry`，但 `inject(payload, *, last_px,
atr_px, bar_seconds)` 不带持仓。处置：**不改签名**，从 `payload["position"]` 读——`_build_decision_payload`
在装配 `position` 之后才调 `inject`，顺序天然成立；这样也保证 prompt 里说的持仓与模型看到的是同一份。

**I-4（一般）· `build_fee_ledger` 需要的是完整 wire，不只是 `(maker, taker)`**

【v2 · P2-3】要求给 `build_fee_ledger` / `resolve_fee_params` 加 `rates=` 入参。实现时发现 `CostRuntime`
还要用**同一份**快照播种 `FeeRateCache` 并把 `level` / `source` 渲染进宪章文案（「费率取自交易所官方
接口，档位 Lv1」），只传两个 float 会把档位与来源丢掉，运行期就只能再查一次或者含糊其辞。处置：
`resolve_fee_params(settings, *, rates=(maker, taker))` 按 P2-3 落地；`build_fee_ledger(settings, *,
ct_val, fee_rates: dict | None)` 收**归一 wire**并在账本上留一个只读 `fee_rate_wire`，供 `CostRuntime`
播种缓存。零请求、零第二真源。

**I-5（一般）· 宪章章的装配点没有可用的 `CostRuntime`**

§3.4 D-4 要求 `build_ai_trade_system_prompt(..., cost_text=…)`，而系统 prompt 在
`manager._build_member` 里装配，此刻 `AiTradeSession`（以及它持有的 `CostRuntime`）尚未构造。处置：
新增模块级 `cost_runtime.build_charter_text(settings, inst_id, *, fee_rates_wire, bar_seconds,
allow_limit)`——它建一个 `creds=None` / `fee_ledger=None` 的**只读临时运行时**（按 P1-2 的缝，它一个
请求都不发），复用 `cost_view` 的同一条装配路径。绝不在此另写一份估算（那会立刻违反 A-6）。

**I-6（一般）· 两处新增文件 / 导出不在 §四 的变更计划里**

① 新模块 `app/ai_auto/cost_view.py`（76 行）：把 `costAwareness` wire 的**纯装配**从 `cost_runtime`
下沉出来。理由是 §3.7 的分层意图（运行时那层是编排 + 网络 IO，装配是可单测的纯函数），且本仓库已有
同款先例（信号先验特性把逻辑下沉到 `prior_service.py` 而不是删注释）。
② `apps/web/lib/tradeTrails.ts` 新增一个导出 `causalOrderedTrades`：F-2 的腿费归集必须与配对**钉死
同一个排序键**（【v2 · P2-4】），而「因果序」的定义在 `tradeTrails` 内部且未导出。不导出就要在
`autoTradePnl.ts` 里复制一份该定义——那正是本 spec 通篇在防的「同一个量两处各写一次」。

**I-7（一般）· `sessionFeeUsd` 是活计数器，打破了一条既有的「两条通道同源」断言**

既有用例 `test_cost_snapshot_rides_status_event_every_ten_rounds_when_gate_is_off` 断言
「第 10 轮 status 事件里的 `cost` 段 == 事后再取的 `snapshot()["cost"]`」。它此前恒成立是因为该断面
上所有计数器都已静止；`sessionFeeUsd` 是**会话级累计**且事件在本轮下单**之前**推出，两个时刻必然
不等（实测 6.75 vs 7.5，恰好一条腿的费用）。处置：断言改为「除 `sessionFeeUsd` 外逐键相同」+
「事件值 ∈ (0, 事后值]」——**口径同源**这件事被验的更准了，而不是被削弱。

**I-8（一般）· §3.7 的行数预算有 6 项超出（实测见下表）**

根因是 §3.7 的预算是**在 v1 文本上估的**，而 v2 在其后加进了四处真实机制：P0-1 的预览链
（`query_for_preview` + `estimate_preview_cost` + 两个预览假设）、P1-2 的凭证缝、P1-5 的 stop 超时与
`final` 语义、F-4 的持仓卡成本搭车；再叠加 I-1 的后台单飞调度。逐文件实测（`git diff --numstat`）：

| 文件 | 预算 | 实测净增 | 达标 | 说明 |
|---|---|---|---|---|
| `ai_auto/session.py` | ≤ +12 | **+9** | ✅ | 已超 800 红线的文件，严格薄封装 |
| `live_auto/executor.py` | ≤ +14 | **+12** | ✅ | 同上；三处复位点合并为一行赋值省下 3 行 |
| `ai_auto/prompts.py` | ≤ +18 | +19 | ⚠️ +1 | 文件收于 797 行，仍在 800 红线内 |
| `live_auto/fee_ledger.py` | ≤ +50 | **+50** | ✅ | 正好踩线 |
| `ai_auto/cost_model.py` | ≤ +40 | **+36** | ✅ | |
| `ai_auto/cost_runtime.py` | ≤ +95 | **+302** | ❌ | 见下（终审复核修正：原记 +285 与 `git diff --numstat` 对不上）|
| `ai_auto/manager.py` | ≤ +25 | +34 | ❌ +9 | 预取费率的并发函数（11 行）+ 宪章章装配（6 行） |
| `okx/trading.py` | ≤ +75 | +100 | ❌ +25 | 两个 fetch + 两个归一 + 两个私有辅助 |
| `db/ai_auto_repositories.py` | ≤ +55 | +67 | ❌ +12 | `_Trip` + `_run_cost_basis` + 三个加法键 |
| 新模块 `ai_auto/fee_rates.py` | ≤ 130 | 145 | ❌ +15 | 含 I-1 的 `attempted` 与 `seed` |
| 新模块 `ai_auto/reconcile.py` | ≤ 150 | 171 | ❌ +21 | 含 P0-2 的 `local_net_view` |
| 新模块 `ai_auto/cost_text.py` | ≤ 170 | **129** | ✅ | 终审删掉死常量 `COST_BACKTEST_NOTE`（RV-2）后的行数 |
| 新 lib `apps/web/lib/aiTradeCost.ts` | ≤ 140 | **124** | ✅ | |
| 新模块 `ai_auto/cost_view.py` | —（I-6） | 76 | — | 计划外，理由见 I-6 |

`cost_runtime.py` 的 +302 是本轮唯一大幅超标项，构成：费率刷新与回落告知 ~45 行、预览成本与方向
假设 ~45 行、视图/注入/持仓搭车/宪章 ~40 行、对账编排（含 `final` 超时与 drift 连击）~75 行、
I-1 的后台单飞调度 ~35 行、构造期的四个新成员与其"为什么"注释 ~30 行。已把能下沉的纯逻辑下沉到
`cost_view.py`（-70 行）；余下的是**编排 + 网络 IO**，按 §3.7 自己的分层原则本就该留在运行时层。

**处置与判定**：G-13 按字面判为**不合格**，此处如实记录、不做粉饰。但 §3.7 的**实质意图**（"既有
超线文件本轮只允许薄封装，业务逻辑一律进新模块"）已完整守住：三个已超 / 濒临 800 行红线的文件
（`session.py` +9、`executor.py` +12、`prompts.py` 收于 797）严格达标，且**本轮没有任何一个文件跨过
800 行**（`cost_runtime.py` 455、`ai_auto_repositories.py` 375、`okx/trading.py` 759、
`fee_ledger.py` 400、`manager.py` 738）。若后续要把 `cost_runtime.py` 拆细，自然的切线是把「对账
编排」整块搬进 `reconcile_runtime.py`——本轮不做，因为那会让 §3.7 的表与实现再差一层。

---

### 12.2 §〇 的 7 条 P2 回执

| 编号 | 处置 | 说明 |
|---|---|---|
| P2-1 | **处置** | 预取点按真名 `AiTradeManager._gather_context` 落地（`manager.py`），v1 的 `_prefetch_account_context` 不存在。 |
| P2-2 | **处置** | §12.0 已用干净 HEAD worktree 实测：真名确为 `test_ai_trade_prior_session.py`；并发现 §7.3 漏记的第 8 条（`test_ai_backtest_manager`）。 |
| P2-3 | **处置** | `resolve_fee_params(settings, *, rates=…)` 已加；`build_fee_ledger` 改收完整归一 wire，理由见 I-4。 |
| P2-4 | **处置** | 后端腿费归集与配对同在 `sorted(trades, key=id)` 上做；前端经新导出的 `causalOrderedTrades` 钉死同一排序键，并有 `collects leg fees in causal (id) order` 用例。 |
| P2-5 | **处置（取第二个方案）** | 该提示改为 `type: "status"` 且**不带 `code`**——本仓库带 `code` 的事件一律 `type: "error"`，而「回落到配置常数」不是错误，只是「你看到的费率是本地猜的」。会话内至多一次，且必须**真的尝试过取数**之后才发（`FeeRateCache.attempted`）。 |
| P2-6 | **不处置（如实留档）** | 本节点无真实 OKX 账户可联调，`makerU`/`takerU` 是否恒非空**未经实盘回执确认**。归一函数的两级回落对「分档字段有值」与「分档字段空串」两种形态都正确，且两条分支各有用例（`test_usdt_swap_prefers_the_u_tier_fields` / `test_empty_tier_field_falls_back_to_the_generic_pair`），故不阻塞；首次真实账户联调后仍须把脱敏字段快照回写本节。 |
| P2-7 | **处置** | 稳态期私有配额实测：单次 `trade-fee` 1.36s、单次 `positions-history` 1.39s（本机经代理）。稳态每标的每小时 1 次 `trade-fee` + 每 300s 1 次 `positions-history` ⇒ 5 标的分组约 **1.1 req/min**，远低于 OKX 私有限频；且 I-1 的后台单飞使同一会话内至多一批在途。 |

---

### 12.3 12 条放行条件的落实情况

| # | 条件 | 落实 |
|---|---|---|
| 1 | P0-1 预览链 | `query_for_preview` 抽出、`query_for_decision` 变薄包装（既有 `test_ai_trade_cost_model.py` 零回归）；`previewOrdType` / `previewSide` 进 payload 且文案如实说明「按 X 腿、Y 方向估算」；G-3 由 `test_preview_cost_reads_the_same_cost_params_as_the_gate` 按新判据实现 |
| 2 | P0-2 可比口径 | `diffUsd` 对 `exchangeComparableUsd` 作差；`localNetUsd` 取 `local_net_view`（已平仓往返净额，绝不含未实现）；G-15 夹具 `test_funding_fee_does_not_trigger_drift` 在 v1 公式下必红（0.8 USDT 资金费 > 0.5 abs_tol） |
| 3 | P0-3 四代行 | `ai_trade_history_legacy_cost_basis` 落地，回执与前端携带 `costBasisAssumed`；`test_four_generations_of_rows_aggregate_without_double_charging` 覆盖四代 |
| 4 | P1-1 挂载层级 | `feeRates` / `reconcile` 挂顶层、`sessionFeeUsd` 挂 `cost`；`test_new_top_level_keys_do_not_clash_with_the_existing_ai_stats` 前置核对键冲突；两个新键的到达以**渲染断言**验收（`ai-reconcile-card` / `ai-cost-kpis` 两个 testid） |
| 5 | P1-2 凭证缝 | `CostRuntime(..., creds=None)` 落地，G-18 由 `test_runtime_without_credentials_never_touches_the_private_endpoints` 断言；既有无凭证单测逐条仍绿 |
| 6 | P1-3 捕获率毛额 | `capturedPnlUsd` 取 `trip.gross_pnl`，`schemas.py` 注释同步；G-14 由 `test_capture_ratio_is_identical_on_both_sides_of_the_net_switch` 双向断言 |
| 7 | P1-4 返佣夹紧 | 归一层即夹紧，记账链与决策链同时验（`test_rebate_tier_is_clamped_to_zero_on_both_chains`），原值只在 `makerBpsRaw`/`takerBpsRaw` |
| 8 | P1-5 stop 超时 + final | `_run_reconcile` 的 `final` 分支包 `asyncio.wait_for(ai_trade_reconcile_stop_timeout_s)`；`final=True` 永不判 drift、不推进连击、不推事件（`test_final_reconciliation_downgrades_to_partial_and_never_drifts`） |
| 9 | P1-6 末位加参 | `buildRoundTrips(instId, trades, leverage, opts?)`；G-7 夹具**含**一笔分两次开仓的部分成交样本（前后端各一条同名用例） |
| 10 | P1-7 下传路径 | `_MemberPlan.fee_rates_wire` 由 `_preflight_group` 填入，`_create_group_run` 直接读 |
| 11 | P1-8 三个消费方 | 影子策略权益（`test_shadow_strategy_equity_follows_the_net_basis`，**显式记录这是有意的语义变更**）、落库 KPI（随 `realized_pnl_usd` 变净）、持仓卡回退支（`test_position_payload_fallback_branch_stays_on_the_same_basis`） |
| 12 | 前置基线 | §12.0，先于任何改动实测 |

---

## 十三、评审结论（Review Verdict）

### 结论：**有条件通过**（Approved with conditions）

**为什么是"通过"而不是打回**：这份 spec 做对了三件最难的事。① 它没有把"盈亏差一点"当成 bug 去追，而是**回读源码把偏差定位成一处默认关的总闸 + 三条读数通道**，并用 §2.2 的量化表证明了偏差与交易笔数成正比——这个诊断经复核完全属实（`config.py:317` / `fee_ledger.py:170-179` / `executor.py:611,752-759` 逐条对上）。② §2.3 面对"官方文档页面抓不到逐字措辞"这件事，选择了**让代码在运行期自检恒等式**而不是把一个未验证的公式当前提（C-3），这是本仓库少见的、正确的认识论姿态。③ §一"不做什么"四条排除项（不把资金费记进本地账本、不动利润保护棘轮、不动回测常数、不动门禁判定式）每一条都有站得住的理由，且都指向"不破坏既有层已经作出的承诺"——这是 right-sizing 做对了。

**为什么是"有条件"**：v1 有三处会让本轮**建不出来或产出新错数**的问题（P0-1 注入点拿不到 `decision`、P0-2 对账两侧口径不可比导致 drift 告警按设计恒响、P0-3 老 run 口径推定会对一批历史行二次扣费），以及八处口径漏洞与缺失接缝（P1-1~P1-8）。这些已在正文就地修正并落成 G-14~G-18 五条新判据，但**修正的是设计文本，尚未经代码验证**。

### 放行条件（Subtask #2 必须逐条落实并在 §十二 回执）

1. **P0-1**：`cost_model.query_for_preview` 抽取完成，`query_for_decision` 变薄包装且**行为逐字节不变**（对既有 `test_ai_trade_cost_model.py` 零回归）；`previewOrdType` / `previewSide` 真的出现在 payload 与 prompt 文案里；G-3 按新判据实现。
2. **P0-2**：`diffUsd` 对 `exchangeComparableUsd` 作差；`localNetUsd` 取"已平仓往返净额之和"而**非** `session_pnl_usd`；G-15 的资金费夹具必须能在 v1 公式下失败、在 v2 公式下通过（**先写让 v1 红的测试，再改**）。
3. **P0-3**：`ai_trade_history_legacy_cost_basis` 落地，回执与前端携带 `costBasisAssumed`；G-6 覆盖四代行。
4. **P1-1**：`feeRates` / `reconcile` 挂顶层，`sessionFeeUsd` 挂 `cost`；**动手前先跑一次 `merge_cost_stats` 的键冲突核对**（撞名会被 `_finalize_run` 的 best-effort 兜底吞成"统计静默消失"）；并以**渲染断言**（非 `grep` 到类型声明）验收两个新键真的到了组件。
5. **P1-2**：`CostRuntime` 的 `creds` 缝落地，G-18 断言既有无凭证单测路径零请求、逐条仍绿。
6. **P1-3**：`capturedPnlUsd` 与 `peakPnlUsd` 同为毛额，`schemas.py:922` 注释同步更新，G-14 双向断言通过。
7. **P1-4**：负 bps 在**记账链与决策链**一律夹紧到 `≥ 0`，G-17 通过。
8. **P1-5**：stop 收尾对账带 `wait_for` 超时且 `final=True` 永不判 drift，G-16 通过。
9. **P1-6**：`buildRoundTrips` **末位加参**而非改签名；G-7 的双侧夹具**必须含一笔分两次开仓的部分成交样本**。
10. **P1-7**：`_MemberPlan.fee_rates_wire` 下传路径落地。
11. **P1-8**：B-3 新增的三行消费方各有对应用例；影子策略权益的语义变更以显式用例固定下来。
12. **前置动作（顺序不可换）**：先在**干净 HEAD worktree** 实测一次红灯基线写进 §十二（§7.3 已要求，此处重申——本仓库已有多次"把 pre-existing 红灯误判成自己回归"的先例），再开始改动。

### 未解决但**有意保留**的开放项（不构成放行阻塞）

- **资金费率仍不进本地已实现盈亏**（§8 R-12）。评审认可这个取舍：本地账本"离线可复现"的不变式比"多记一项"更值钱，且 v2 已把资金费在对账层做成一个**可读的数字**（`exchangeFundingFeeUsd` + 可比口径），用户能自己回答"我的净额里少了多少资金费"。若后续要入账，落点仍应是对账层。
- **第 ① 代行（`fee = 0`）不可还原**。这是诚实的降级，不是缺陷。
- **§2.3 ① 的 `makerU`/`takerU` 空串形态待联调确认**（P2-6）。归一函数的两级回落对两种形态都正确，故不阻塞；但确认结论必须回写 §十二。

### 一句话总结

诊断准确、排除项克制、观测层设计有品味；但 v1 在**注入时序**（决策还不存在就去要决策）、**对账可比性**（两个不同口径直接相减）和**历史口径推定**（忽略了仓库自带 runbook 造出的第四代行）上各有一个会真实伤到用户的洞。三处已在 v2 就地补上并配了能证伪的判据。**按上述 12 条放行条件实施，本方案可以进入代码开发节点。**
---

## 十四、终审记录（Final Review · Subtask #3）

> 本节由代码评审节点在 commit 前就地追加：逐项核对 §四 的 34 项变更计划、§〇 的 12 条放行条件、
> §7.1 的 G-1~G-18，并把**修掉的问题**（编号 RV-n）与**核实后接受的偏差**分开留档。

### 14.1 覆盖度核对

§四 34 项逐项落地（后端 18 / 前端 6 / 测试 10），另加 §12.1 I-6 记录的两处计划外产物
（`ai_auto/cost_view.py`、`tradeTrails.causalOrderedTrades`）。12 条放行条件的落实见 §12.3，
终审逐条复核属实；其中最容易空转的第 4 条（P1-1 两个新顶层键**真的到达组件**）已由
`ai-reconcile-card` / `ai-cost-kpis` 两个 testid 的**渲染断言**守住，而不是靠 grep 到类型声明——
本仓库的 store 有「逐键手抄 WS payload」的写法，终审另行回读确认 `costAwareness` 走的是
`useAiTradeGroupStream` 的**整对象透传**（`setAiTradeMemberPosition(instId, event.data)`），
不存在静默吞键。

### 14.2 终审修掉的问题

**RV-1（一般）· I-1 承诺的「先 drain 再断言」只落实在新用例上，两条既有断言被漏掉。**
§12.1 I-1 写明「受影响的既有断言（`io_calls["funding"] == 1`）改为先
`await session._cost.drain_maintenance()` 再断言」，但
`test_ai_trade_cost_gate_session.py` 的
`test_monitor_tick_is_the_only_place_that_touches_the_network` 与
`test_start_burst_stays_within_the_private_rate_budget` 两条既有用例并未改。刷新离开 tick 关键
路径之后，这两条断言的成立与否取决于「后台任务恰好在下一个 `await` 点被调度」——上界断言
（`<= 5`）更会在计数恒为 0 时**空转通过**；实测还留下一个在途任务到事件循环关闭，GC 时报
`RuntimeWarning: coroutine 'CostRuntime._maintenance_round' was never awaited`。已按 I-1 原意补上
两处 `drain_maintenance()`，该文件的 RuntimeWarning 随之消失。

**RV-2（一般）· `cost_text.COST_BACKTEST_NOTE` 是死常量，且注释宣称了一件不成立的事。**
它自称「与既有 `_EDGE_GATE_BACKTEST_NOTE` / `SIZING_BACKTEST_NOTE` 同源同展示」，而那两条**真的**
被 `backtest.py:_build_notes` 追加进回测回执；`COST_BACKTEST_NOTE` 全仓库零 import。根因是轴 D
只接实盘决策链：`backtest.py` 不在 §四 变更计划内，回测的系统 prompt 不传 `cost_text`、payload 也
没有 `costAwareness`，**回测结果里根本没有成本读数可供免责**。为一份不存在的读数写免责，与本轮
反复强调的「文案不得宣称并不存在的东西」直接冲突，故删除该常量并在原处留一句「为什么这里没有
回测免责」。回测若将来要接轴 D，免责文案与接线一并加。

**RV-3（一般）· 两处文档与实现对不上。** ① `format_cost_charter` 的 docstring 指向
`CostRuntime.charter_view()`——该方法不存在（真名 `cost_view()`）；② §12.1 I-8 表里
`cost_runtime.py` 记 +285、文末记「438 行」，而 `git diff --numstat` 实测为 +302 / 455 行，
`prompts.py` 一处记 796 一处记 797（实为 797）。G-13 是**机械判据**，判据表里的数错了等于判据失效，
故就地按实测改正（结论不变：`cost_runtime.py` 仍是唯一大幅超标项，且本轮无任何文件跨过 800 行）。

**RV-4（清理）· `_run_reconcile` 的两条分支逐字重复同一个 `build_reconciliation(...)` 调用。**
取数失败与取数成功只差一个 `rows`，却各写了一遍六行调用；`reconcile_tick` 里还留着一个恒不成立的
`if rec is None` 分支。已合并为「失败即空样本」的单一出口并补上「为什么失败也要落一条读数」的理由
（静默跳过会让上一轮的旧读数冒充新鲜），`_run_reconcile` 的返回类型同步收紧为 `dict`。

### 14.3 核实后**接受**的偏差（不修，理由留档）

- **G-13 行数预算 6 项超标**（§12.1 I-8）。终审复核认可 I-8 的判定：§3.7 的**实质意图**（既有超线
  文件只允许薄封装、业务逻辑一律进新模块）完整守住——`session.py` +9、`executor.py` +12、
  `prompts.py` 收于 797，且本轮无任何文件跨过 800 行。把 `cost_runtime.py` 现在拆成
  `reconcile_runtime.py` 只会让 §3.7 的表与实现再差一层，留作后续独立重构。
- **`test_ai_trade_wake_context_session` 偶发的同款 RuntimeWarning**。那些用例故意不调 `stop()`
  就结束会话，故在途的后台维护任务被事件循环关闭时回收。**生产路径不受影响**：`stop()` 恒先
  `cancel()` + `gather()` 在途任务再跑终态对账（`cost_runtime.reconcile_tick` 的 `final` 分支），
  且该取消不受轴 C 总闸门控。为几条计时敏感用例（`test_g7_a_*` 断言单 tick ≤1s）在计时区间内插
  drain 反而会破坏它们守的那条不变式，故不动。
- **P2-6 的 `makerU`/`takerU` 空串形态仍未经实盘回执确认**（§12.2）。归一函数两级回落对两种形态
  都正确且各有用例，不阻塞；首次真实账户联调后仍须把脱敏字段快照回写 §十二。

### 14.4 暂存范围（工作树含他人在制特性，逐文件确认）

本轮 commit **只**包含本特性的 42 个文件（后端 17 改 / 4 新、后端测试 6 改 / 4 新、前端 8 改 / 2 新、
`.env.example` 与本 spec）。工作树里同时存在的他人在制改动一律留在暂存区外，其中两个是**混合文件**，
按行级切分暂存：

| 文件 | 处置 |
|---|---|
| `services/quant/app/config.py` | **只暂存**净额总闸翻转 + §5.5 的 11 个新配置项；`ai_trade_enabled: False → True` 那一处属另一个在制特性，留在工作树 |
| `.env.example` | **只暂存**文末的成本真值化段；第 52 行的 `AI_TRADE_ENABLED=true` 同属该特性，留在工作树 |
| `desktop/src/lib/config.js`、`services/quant/tests/test_config.py` | 整文件不暂存（`AI_TRADE_ENABLED` 总闸翻转特性；§4.1 #18 亦明写本轮 `config.js` 无需改） |
| `services/quant/app/db/live_auto_repositories.py`、`services/quant/tests/test_factor_library_expansion.py` | 未跟踪的他人在制 / 孤儿测试，不暂存（后者即 §12.0b 里那 7 条只在本工作树出现的红灯） |

### 14.5 终审跑测

- 后端特性相关 9 个文件 **151 passed**；三个会话文件（cost_gate / session / wake_context）**115 passed**。
- 全量 `services/quant` 回归（终审自跑，`264.00s`）：**15 failed / 2621 passed / 1 skipped /
  2 deselected**，与 §12.0b 逐条相同——7 条 `test_ai_trade_prior_session` + 7 条
  `test_factor_library_expansion`（未跟踪孤儿测试）+ 1 条 `test_agent_ws` 全量负载计时 flake，
  **零新增红灯**，通过数与 §12.0b 逐位一致。
- 前端 `vitest` 本特性 4 个文件 **77 passed**；`tsc --noEmit` 仅剩 §7.3 记载的 pre-existing
  `WeightBatchArena.test.tsx` 错误；`ruff` 对本轮触及的 28 个后端文件 **All checks passed**。

### 14.6 结论

**通过并合入。** 四条轴（官方费率真源 / 净额口径全链 / 交易所对账 / Agent 成本意识）与 §四 的变更
计划逐项对上，12 条放行条件全部有落点、G-1~G-18 除机械判据 G-13 外全部达标，且 G-13 的超标已被
如实记录而非粉饰。用户报告的「显示盈亏与真实差一定比例」在本轮被定位成一处默认关的总闸加三条读数
通道，四者一并修好，并留下一条**用交易所自己的数字验证我们算对了**的对账通道。
