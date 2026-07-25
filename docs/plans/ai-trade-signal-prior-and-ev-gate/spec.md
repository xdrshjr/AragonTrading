# AI 自动交易 · 信号先验体系与 EV 分子升级（阶段二 · 第一步）

> **状态**：**已合并** · 设计 v2 已评审 · 终审通过
> **归属诊断**：[`docs/improve/01-ai-autotrading`](../../improve/01-ai-autotrading/00-总纲与现状诊断.md) 的
> D-5（信号板无质量先验、共识等权）与 D-1/D-7 的交集（edge 分子仍是 ATR 粗估）。
> **路线图位置**：[08 · 实施路线图](../../improve/01-ai-autotrading/08-实施路线图与里程碑.md) §3.2 阶段二的前两行
> （「信号先验（两路来源）」+「加权共识（并列新函数）」），并把阶段一遗留的欠账「edge 是 ATR 粗估而非概率化先验」补上。
> **前置增量**：[`ai-trade-cost-accounting-and-edge-gate`](../ai-trade-cost-accounting-and-edge-gate/spec.md)（阶段一，已合并 `96f554d`）。

---

## 评审记录（Review Notes · v1 → v2）

评审维度：可行性 / 完备性 / 一致性（`CLAUDE.md` 与仓库既有惯例）/ 规模适配。
**评审方式**：v1 的每一条源码引用都重新对着活树核对了一遍（行数、函数签名、枚举取值、前端出现点），
下表只列**核对后仍然成立**的问题；核对通过的引用不再赘述（见本节末「核对通过项」）。
P0 / P1 已在正文逐条改掉（改动点在「处置」列给出小节号），P2 除注明外亦已顺手改掉。

| 编号 | 级别 | 位置 | 问题 | 处置 |
|---|---|---|---|---|
| **P0-1** | P0 | §6.3 前端镜像点清点 / §8.2 G-8 | **普查数字与真实代码不符，且漏了唯一那个载荷文件**。实测 `edgeSource` 在 `apps/web` 里只出现 **1 处**（`aiTradeTypes.ts:303`，是 WS `edge_gate` 事件类型），不是 3 处；`AiTradeRoundTrace` 里**根本没有** `edgeSource`（现有键为 `edgeGateStatus/edgeBps/edgeCostBps/edgeGateReason`，`aiTradeTypes.ts:343-346`）；而真正的承载点 `apps/web/lib/store.ts` 未被列入改动计划——它在 `buildRoundTrace`（`store.ts:120-158`）与 `reduceRoundPhase` 的 `edge_gate` 分支（`store.ts:209-217`）里**逐键手抄**事件字段，没在这两处加键，新字段就永远到不了组件。按 v1 照做的结果是：类型改了、组件改了、测试全绿、界面永远显示旧文案。更糟的是 G-8 的判据「`grep -rn "edgeSource" apps/web` 每处都已处理」在只有 1 处出现时**天然满足**，是一条空转门禁——而这恰是本仓库已犯过两次的那类错（`profit-protection` RV-2 / `limit-orders` 第 14 镜像点） | §6.3 改为真实的 5 文件 6 编辑点清单；G-8 由 grep 改为逐点核对表（§8.2） |
| **P0-2** | P0 | §3.3 / §3.6 | **策略先验的估计量与它在线被使用的条件不是同一个事件**。v1 的 `strategy_replay` 事件定义是「该根 `latest_signal ∈ {open_long,open_short}`」（**进场边沿**），而在线候选筛选却是「`stance == direction` **或** `latest_signal == open_{direction}`」——`stance` 是影子 broker 的**持久立场**（`signals.py:41`），一段趋势里它会连续为真几十根。于是「进场那一根后 8 根的收益分布」被拿去回答「已经持仓第 30 根时开仓的收益是多少」，两者没有任何数学关系。`p / W / L` 因此是一个**没有定义的数**，而它要去当真实资金开仓门禁的分子 | §3.2 推论 5 + §3.3 事件定义改为**按来源各自同源**（signal_edge=边沿、strategy_replay=立场根）；§3.6 删掉 OR 子句；§3.4 增 `runN` 判据防重叠根虚增样本；§3.8 覆盖率口径同步 |
| **P1-1** | P1 | §3.7 EV-1 | 不变式陈述的**层级错了**：v1 把 EV-1 立在 `target_bps` 上，但真正要保证的是「本增量不会让任何一轮开仓从被否变成放行」，那是**门禁产出**层级的性质。二者在 `required` 恒定时等价，一旦有人（为缓解 R-4）给 prior 分支单独放松 `k_min`，EV-1 字面仍成立而安全性质已被破坏（反例：`atr=20,prior=18,C=13`，`k=2` 时 phase-1 否、`k_prior=1` 时放行）。同理 §10 R-4 的回滚阶梯「`k_min` 下调 1.5 → 0」也**不是**纯回滚：它同时放松了 prior 缺席轮的 ATR 分支 | §3.7 重述 EV-1 为产出级 + 固定阈值前提 + 明令禁止 prior 分支单独降 `k`；§10 R-4 标注 `k_min` 阶梯的副作用 |
| **P1-2** | P1 | §3.10 / §九 runbook | **L1/L2 观察窗口读不到任何先验读数，L3 的放行判据在默认配置下不可评估**。`ai_trade_edge_gate_k_min` 默认 **0.0**（`config.py:273`），`evaluate_edge_gate` 首行即 `k_min<=0 ⇒ return None`（`edge_gate.py:185-187`），此时 `edge_gate` 事件**一条都不发**——`priorP/priorEvBps` 无处落地，L3 的 `vetoRate ∈ [20%,70%]` 也无从算起。且总闸③关闭时 v1 让 `prior_ev_bps` 恒 `None`，于是「开③之前先看看它会拦掉多少」这个最该做的反事实观察**做不了**，运维只能闭眼直接开门禁 | §3.7/§4.3 拆出 `prior_binding` 形参：读数**无条件**进 wire（含 `priorWouldTarget/priorWouldVeto` 反事实），是否进 `min` 链才由总闸③决定；§九 L3 前置补 `k_min > 0` |
| **P1-3** | P1 | §3.10 / §九 | **`costSnapshot.priorCoverage` 挂错了通道**。`status` 事件上的 `costSnapshot` 来自 `CostRuntime.snapshot_if_due` → `cost_snapshot_wire` → `build_cost_stats`（`session.py:836`、`stats_ext.py:57`），**绕开** `snapshot()`；只改 `snapshot()` 的话运维按 runbook 去 `status` 事件里找 `priorCoverage` 会永远找不到。更麻烦的是两条通道**互斥**：`_emit_cost_snapshot` 只在门禁空转（`k_min<=0`）时被调用，`snapshot()` 只在门禁生效时随 `edge_gate` 事件下发。且 v1 没说 `CostRuntime` 从哪拿到先验读数（它不持有 `PriorRuntime`） | §3.10 增 `CostRuntime.set_prior_view()` 缝 + 两条通道同时并入；§九 读数位置按 `k_min` 分档写清 |
| **P1-4** | P1 | §4.3 签名 | 板的类型标成 `list[dict]`，实际是 `list[StrategySignalView]`（dataclass，字段 snake_case：`strategy_id/stance/latest_signal`，`signals.py:34-44`），`compute_consensus` 也是照这个类型写的。按 `dict` 落地会当场 `TypeError`，或诱导实现节点在热路径上多做一次 dict 化 | §4.3 改真实类型；`signal_prior` 侧改吃 `board_view: list[tuple[int,str]]` 投影，既保持零 pandas / 零 `signals` 依赖，也顺带断开潜在的循环 import |
| **P1-5** | P1 | §4.3 / §6.2 manager | **多标的分组会串标的**。v1 说 `prior_snapshots` 「镜像 `base_rule` 的既有姿态」透传，但 `base_rule` 是**分组共享的单个对象**（`manager.py:131`），而先验快照是 `(instId, bar)` 粒度、一个分组最多 5 个标的（`ai_trade_max_symbols=5`）。照字面镜像的结果是全部成员共用一份快照——BTC 的成员拿 ETH 的历史统计去开真实仓位，且**不会报错** | §4.3/§6.2 定死 `prior_snapshots: dict[str, dict] | None`（按 `instId` 键），`_build_member` 按 `plan.inst_id` 取；缺键=该成员无先验；补 T-20 |
| **P1-6** | P1 | §3.5 加载契约 | **策略身份漂移无契约**。条目以 `strategyId` 归集，但 `strategies.params_json` / `template_key` 随时可被用户改（`models.py:33-40` 有 `updated_at`），改完 `strategyId` 不变、快照仍在 7 天新鲜期内 ⇒ C-2/C-3 全过，先验静默地在描述**另一个策略**。这正是 §10 R-3「口径漂移」的同一根因，v1 只防了「另写一份回放」，没防「策略自己变了」 | §3.5 增 **C-6 策略指纹**；§5.1 增 `strategyFingerprint`；在已经持有策略行的 `_resolve_prior_snapshots` 里比对，失配条目丢弃 + 告警 |
| **P2-1** | P2 | §3.1 图 | `CostRuntime.evaluate(prior_edge_bps=…)` 与 §4.3 的 `prior_ev_bps` 命名不一致（同一个量两个名字） | 统一为 `prior_ev_bps` |
| **P2-2** | P2 | §3.6 | 聚合公式里的 `p_i` 从未定义；§5.1 只有 `winRate` | §3.6 显式写 `p_i := entry.winRate`（衰减加权胜率） |
| **P2-3** | P2 | §3.5 分桶 | 两处 regime 降级没写：① `per_bar_primaries` 从 `max(ema_slow_p, trend_win)` 根才起算（`regime.py:336-346`），之前的根**没有标签**；② 在线 regime 取自 `marketContext.primary`，而 `ai_trade_regime_context=False`（默认 True）时 `build_market_context` 直接返回 `None` | §3.5 补两条：无标签根只进 `all` 桶；在线 regime 缺失即走 `all` 桶（不是整键缺席） |
| **P2-4** | P2 | §3.1 / R-6 | 在线 8 检测器求值有一条**静默假阴**路径：`detect_signal` 会把前 `SignalParams.warmup=30` 根强制判 False（`signal_edge.py:202-203`），若按 R-6 的「只对 `df.tail(min_bars)` 求值」切得太短，全部信号恒为 False 且**没有任何提示** | §3.6/§R-6 定死 `_PRIOR_DETECT_MIN_BARS = 60`，不足即 `active_signal_keys` 整体缺席（视同无触发）而非静默 False |
| **P2-5** | P2 | §6.2 / G-6 | `session.py` 净增 **+14** 硬上限不现实：ctor 形参 + 赋值 + 运行时构造 + 启动告警 + payload 装配 + 门禁透传 + 本轮先验视图回灌 `CostRuntime`，还要带本仓库惯例的注释密度 | 放宽到 **+20** 并写明构成；仍由 G-6 机械核对，超了就再下沉一层而不是再放宽 |
| **P2-6** | P2 | §6.2 表头 | 「修改（后端 10）」与表内 12 行不符（`schemas.py` 是后端、`.env.example` 不是） | 表头改为「后端 11 + 配置样例 1」 |
| **P2-7** | P2 | §8.4 工作树纪律 | 未写明 `desktop/src/lib/config.js` 的 prod 透传白名单是**前缀式**（`key.startsWith('AI_TRADE_')`，`config.js:72`），新 `AI_TRADE_PRIOR_*` 自动覆盖 | §8.4 补一句：本增量**不需要**动 config.js（它是在制混合文件，误改会卷进别人的未提交 hunk） |

**核对通过项**（v1 引用逐条对活树复核，全部属实，实现节点可直接采信）：
`session.py` 1452 / `executor.py` 895 / `backtest.py` 985 / `signals.py` 237 / `edge_gate.py` 227 / `prompts.py` 656 /
`trace.py` 193 / `manager.py` 644 / `api/ai_auto.py` 368 / `config.py` 392 / `signal_edge.py` 333；
`estimate_gross_edge_bps` 在 70-108 行且 `min` 链形状与 v1 描述逐字一致；`EdgeGateOutcome.wire` 已有 `edgeSource`；
`apply_edge_gate` 用 `edge.target_bps >= required` 比较，`required > 0` 恒成立，故**负 EV 必被否**（v1 §3.7 的结论正确，
且不存在「负数触发 `None` ⇒ fail-open」的反转）；`regime._decide_structure` 三值 `trend_up/trend_down/range` 且
`classify_regime` 与 `per_bar_primaries` **共用同一个 `_structural_inputs`+`_decide_structure`**（离线分桶与在线取桶同源，
v1 的关键假设成立）；`detect_signal(df, key, params)` / `SIGNAL_LIBRARY` 8 条 / `_forward_return(prices=(opens,closes), …)`
（公开包装可 3 行落地）；`evaluation.DEFAULT_IC_SEGMENTS=6` / `MIN_IC_SEGMENTS=6`；`AppSetting.value` 是 `Text`（20~50KB
JSON 无压力）、`key` `String(64)`；`get_app_setting` 坏 JSON 回落 default；`build_decision_prompt` 里「共识行 → marketDigest
三行」的插入位存在且顺序与 v1 描述一致；`run_in_threadpool` 是本仓库既有重算卸载范式（`factors/service.py`、
`ai_auto/backtest.py:323/339`），同步 rebuild 端点不越界；`test_ai_trade_consensus.py` 确实存在（等权口径有回归断言守着）。

**三个拍板项的评审裁决**见 §十三（O-1 移出本轮、O-2 不做、总闸③ 保留独立）。

---

## 一、概述（Overview）

阶段一把成本记进了账：`live_auto/fee_ledger.py` 让每笔成交带上真实费率与实测滑点，`ai_auto/cost_model.py`
成为「一笔往返要付多少 bps」的唯一真源，`ai_auto/edge_gate.py` 在共识门禁之后、反思之前插入了
`毛 edge(bps) ≥ k_min × C(bps)` 这道判据。归因恒等式 `E = p·W − (1−p)·L − C` 的**右边**（C）从此是真的。
但等式**左边**至今仍是假的：门禁的分子取 `min(atr_target_mult × ATR(bps), 单仓 OCO 止盈距离(bps))`
（`ai_auto/edge_gate.py:70-108`），它是一个**波动预算**而不是**期望值**——它默认「这一笔能吃满一个 ATR」，
既没有胜率项，也没有盈亏幅度项。ATR 预算恒等于「胜率 100% 的 EV」，因此它系统性高估分子，让门禁在
`k_min = 2.0` 下依然放行大量负期望的开仓。阶段一的验收判据之所以「只看结构性指标、不看净期望」
（[08 §六 自我反驳二](../../improve/01-ai-autotrading/08-实施路线图与里程碑.md)），根因就在这里。

与此同时，喂给模型的信号板仍是 `stance / latestSignal / strength / reason` 四元组
（`ai_auto/signals.py:35-44`），没有任何一个字段告诉模型「这个立场历史上值多少钱」；共识是等权投票
（`signals.py:59`），一个连续三个月失效的策略与一个 IR 显著为正的策略贡献完全相同的一票（D-5）。
本仓库为因子挖掘、权重训练、`signal_edge` 事件研究投入的全部工程量，对 AI 决策链路的复用率是零。

**本增量做三件事，且只做三件事**：① 新增一套**离线产出、在线只读**的信号先验体系——对 8 个内置具名信号
（归集键 `signalKey`）与用户选中的每个策略（归集键 `strategyId`）分别做严格因果的事件研究，在
purged 分段上产出 `{winRate, meanRetBps, avgWinBps, avgLossBps, sampleN, segments, ir, tStat, costBasis}`，
按 `(instId, bar, regime)` 分桶落进既有 `app_settings` KV（零新表、零迁移）；② 新增**并列**的
`compute_weighted_consensus`（`compute_consensus` 逐字不动），按 IR 加权的对数几率**均值**聚合出方向概率 `p`；
③ 把 edge 门禁的分子从 ATR 粗估升级为 `p·W − (1−p)·L`，并让它以 `min()` 的形式**加入**既有分母链——
新分子只会让 edge 更小、绝不放大（不变式 **EV-1**），因此本增量对开仓的作用方向是**单调收紧**，任何
原本被否决的轮次仍被否决。三件事各有一个默认关闭的独立总闸，全关时系统逐字节回退到阶段一行为。

**为什么现在做这件事而不是别的**：[00 §1.3 的贡献度排序](../../improve/01-ai-autotrading/00-总纲与现状诊断.md)
把 `D-5 / D-4 信号质量` 排在成本之后的第二位；[08 §一 的依赖表](../../improve/01-ai-autotrading/08-实施路线图与里程碑.md)
把「先验 → 仓位公式的凯利项 p」列为三条硬依赖之一——不先有 `p`，阶段二的波动率目标化仓位（05）与阶段三的
元标签校准（02 §3.3）都无从谈起。本增量产出的 `p` 是那条依赖链的第一环，也是让「亏损究竟是运气还是负期望」
第一次可被证伪的那个量。

---

## 二、目标与不做什么

### 2.1 目标（可证伪）

1. 每条喂给模型的信号（策略立场 / 具名信号触发）都带一个**可证伪的历史先验**，且先验的成本口径
   （`costBasis`）显式记录在数据里。
2. edge 门禁的分子在先验可用时是**概率加权的期望值**，不可用时逐字回退 ATR 粗估（fail-open 极性不变）。
3. 全部新增机制默认关闭；三个总闸各自独立、任一可单独回滚，关闭后**逐字节**回到阶段一行为。
4. 决策轮内**零 DB IO、零网络 IO**：先验在会话启动时一次性加载，轮内只做纯字典与 numpy 计算。

### 2.2 明确不做（本增量的非目标，逐条给出理由与去处）

| 不做 | 理由 | 去处 |
|---|---|---|
| **不改 `signals.compute_consensus`** | 它的等权口径有既有回归断言守着（`test_ai_trade_consensus.py`）；02 §2 明写「新增并列函数而非原地修改」 | 新增 `compute_weighted_consensus` |
| **加权共识本轮不做门禁** | 等权 `apply_consensus_gate` 是当前唯一硬共识门禁；同时上两道会让「哪一道在起作用」不可证伪 | 阶段二第二步，须先过 L2 观察窗口 |
| **不接入因子快照**（03） | 因子的 IC 是横截面统计，与单标的择时口径错配（03 §六 自我反驳一），需独立的时序重统计 | 阶段二第二步（`ai_auto/factor_snapshot.py`） |
| **不做元标签模型**（02 §3.3） | 依赖归因样本 ≥ 200 笔，当前实盘样本量不足 | 阶段三 |
| **不做置信度校准**（04 §3.4） | 依赖 100 笔以上的「自报置信度 ↔ 实际胜率」配对，同样是样本量前置 | 阶段三 |
| **不改仓位与止损**（05） | 依赖本增量产出的 `p`，是下一步 | 阶段二第二步（`ai_auto/sizing.py`） |
| **不改系统 prompt 的决策纪律** | 让「先验是否有用」与「prompt 改了没」两个变量分离，否则 L1 观察窗口的结论不可归因 | 04 §3.1（阶段二第二步） |
| **不改决策工具 schema** | `build_decision_tool_schema(0.0) is DECISION_TOOL_SCHEMA` 恒等断言是硬约束（04 §2） | 04 §3.2 |
| **AI 回测侧不接入先验** | 先验窗口与回测窗口重叠即前视偏差，回测曲线会「变得非常好看」而毫无意义 | 见 §3.11 O-2 与 06 号闸 1 |
| **不新增数据库表、不做迁移** | 复用 `AppSetting`（`db/models.py:384`）稀疏 KV | — |
| **不引入新的第三方 Python 包** | `services/quant/requirements.txt` 计算侧只有 pandas/numpy（00 §2） | — |

---

## 三、技术设计（Technical design）

### 3.1 分层与新增模块

严格镜像阶段一确立的四层姿态（**纯核心 → 编排 → 运行时 facade → 会话薄封装**），理由是
`ai_auto/session.py` 现 **1452 行**、`live_auto/executor.py` 现 **895 行**、`ai_auto/backtest.py` 现 **985 行**，
三者均已超 `CLAUDE.md` 的 800 行红线，本增量承诺「不再恶化」（逐文件净增行数硬上限见 §8.4）。

```
                    ┌──────────────────────── 离线（rebuild 端点，threadpool） ───────────────────────┐
OKX K 线 ──▶ candles_to_df ──▶ ai_auto/prior_builder.build_prior_snapshot
                                    │   ├─ engine/signal_edge.detect_signal        （8 个具名信号事件）
                                    │   ├─ ai_auto/signals.evaluate_signal_board   （逐策略影子立场事件）
                                    │   ├─ engine/regime.per_bar_primaries         （逐根体制标签，分桶）
                                    │   ├─ engine/walk_forward_purge.plan_segments  + purge_crossing_events
                                    │   └─ engine/signal_edge.forward_return_at    （唯一前瞻收益估计量）
                                    ▼
                       PriorSnapshot(JSON) ──▶ db/ai_prior_repositories ──▶ app_settings["ai_prior:{inst}:{bar}"]
                    └──────────────────────────────────────────────────────────────────────────────┘

                    ┌──────────────────────── 在线（决策轮，零 IO） ────────────────────────────────┐
会话启动: api/ai_auto._resolve_prior_snapshots ──▶ manager._build_member(prior_snapshot=…) ──▶ AiTradeSession
决策轮:   PriorRuntime.build(board, marketContext, df)
              ├─ signal_prior.load_snapshot   （五道契约校验；任一不满足 ⇒ 整键/整条目缺席）
              ├─ PriorRuntime._active_signals （8 个检测器只读末根）
              ├─ signal_prior.resolve_direction_prior(long) / (short)
              └─ signals.compute_weighted_consensus
          ──▶ payload["signalPriors"] ──▶ prompts._format_signal_priors ──▶ 决策 LLM
          ──▶ PriorRuntime.last_edge_bps(action) ──▶ CostRuntime.evaluate(prior_ev_bps=…, prior_binding=闸③)
                                                   ──▶ edge_gate.estimate_gross_edge_bps（min 链第三项）
                    └──────────────────────────────────────────────────────────────────────────────┘
```

| 新模块 | 行数预算 | 依赖足迹 | 职责 |
|---|---|---|---|
| `engine/walk_forward_purge.py` | ≤ 90 | 无（纯 Python） | 分段 / 跨段事件 purge-embargo（v2：O-1 的预留窗口函数已裁掉，见 §十三） |
| `ai_auto/signal_prior.py` | ≤ 240 | 纯 Python + math（**无 pandas**） | 先验数据模型、契约校验、方向聚合、wire 序列化 |
| `ai_auto/prior_builder.py` | ≤ 200 | pandas / numpy / engine.* | 离线事件研究与快照装配（**只在 rebuild 端点被调用**） |
| `ai_auto/prior_runtime.py` | ≤ 100 | pandas + 上述两者 | 会话级运行时 facade（镜像 `cost_runtime.CostRuntime`） |
| `db/ai_prior_repositories.py` | ≤ 70 | SQLAlchemy | KV 读写 + 键构造 + 坏 JSON 容错 |

`signal_prior.py` **刻意不 import pandas**：它是决策轮内每轮都跑的纯计算，保持零 DataFrame 依赖既让单测可以
用裸 dict 构造样本，也杜绝「在热路径上顺手 groupby」的诱惑。

### 3.2 估计量（estimand）的定义——本设计最关键的一处口径

先验回答的问题必须与门禁分子问的问题**逐字相同**，否则分子分母各说各话。门禁问的是：
**「本轮若按参考成交价开仓，预期持有 `expected_hold_bars` 根，毛收益率是多少 bps？」**

因此估计量定义为：

> 给定第 `i` 根出现某证据（策略立场为 `d` / 具名信号 `k` 触发），以 `open[i+1]` 入场、`close[i+h]` 出场、
> 按方向 `d` 调整符号的**毛**收益率，`h = settings.ai_trade_cost_expected_hold_bars`（默认 8）。

五条推论，每条都是硬约束：

1. **`h` 必须与成本模型的 `expected_hold_bars` 同源**。成本模型据它推资金费率结算期数
   （`cost_model._funding_carry_bps`，`cost_model.py:108-121`）；分子若用另一个 `h`，EV 与 C 说的就是两段不同的
   持有期。故快照里存 `horizonBars`，加载时与 `settings.ai_trade_cost_expected_hold_bars` **逐值比对**，
   不等即**整键缺席**（契约 C-3，§3.5）。
2. **入场取 `open[i+1]`**，与 `SimBroker` 的次根开盘撮合、与 `signal_edge.forward_returns(entry_at="next_open")`
   的默认口径一致。取 `close[i]` 会引入「同根收盘即成交」的乐观偏差。
3. **`costBasis` 恒为 `"gross"`**。`signal_edge._summarize_horizon` 支持 `cost_bps` 参数并产出 `netMeanRet`
   （`signal_edge.py:241,262`），影子 broker 也已按 5/2 bps 计费（`signals.py:30-31`）——本增量**一律传
   `cost_bps=0.0`、只产毛口径**。理由见 §10 风险 R-1（双重计费 ⇒ 永不开仓，本仓库有同类事故前科）。
   加载侧对 `costBasis != "gross"` 的条目**直接丢弃**（fail-closed），不做「把成本加回去」的反演——反演需要
   知道当时的成本常数，那是一个会静默漂移的假设。
4. **绝不用 `run_backtest` 的成交盈亏做先验**（对 02 §3.1 的显式偏离，见 §3.11 D-1）。
5. **【v2 · P0-2】事件定义必须与它在线被使用的条件同源**。这是本次评审改掉的最重的一处：先验统计的
   条件事件 `A`（「第 `i` 根发生了什么」）与在线取用该先验的条件 `A'`（「本轮该条目为什么被选为贡献者」）
   **必须是同一个断言**，否则 `p / W / L` 不是「估计得不准」，而是**没有定义**。v1 在策略侧犯了这个错：
   离线按**进场边沿**（`latest_signal ∈ {open_long,open_short}`）取事件，在线却按**持久立场**
   （`stance == direction`，一段趋势里连续几十根为真）取贡献者。故 v2 按来源各自钉死（见 §3.3 表）：
   `signal_edge` 两侧都是**本根触发**，`strategy_replay` 两侧都是**本根立场**。

### 3.3 两路来源与事件抽取

| 来源 | 归集键 | 事件定义（离线） | 在线取用条件（**必须同源**） | 抽取路径 |
|---|---|---|---|---|
| `signal_edge` | `signalKey`（8 个内置信号） | `detect_signal(df, key)` 在第 `i` 根为 True | 本根 `key ∈ active_signal_keys` | `engine/signal_edge.py:118` 的 `SIGNAL_LIBRARY`；方向取 `SignalSpec.direction`（固定） |
| `strategy_replay` | `strategyId` | 逐根影子回放中该策略 `stance == d`（**持久立场根**，`d ∈ {long,short}`） | 本轮板上该策略 `stance == direction` | `signals.evaluate_signal_board`（`signals.py:125`）逐根驱动，与实盘信号板**同一函数**；`stance` 即 `StrategySignalView.stance`（`signals.py:41`） |

**为什么策略侧取「立场根」而不是「进场边沿」**（v2 拍板，P0-2）：AI 每轮看到的、也是唯一能拿来做条件的，
就是板上那个持久 `stance`——绝大多数轮次距离该策略的进场边沿已有几根到几十根。度量边沿、应用于立场，
是把两个不同的条件分布强行等同；反过来「在线只认边沿轮」虽然也同源，却会让先验在 90% 的轮次里缺席，
等于本增量白做。取立场根的代价是**样本重叠**（同一段趋势的相邻根高度自相关，`sampleN` 会虚高），
故 §3.4 增设 `runN`（连续立场段数）判据：真正独立的样本单位是**段**，不是根。

> 这条同源要求同样适用于未来任何新来源（因子快照、元标签）：**先写下 `A`，再写下 `A'`，两者字面不同即不合格**。

策略侧刻意复用**实盘信号板本身**的驱动函数：先验度量的对象必须是「实盘每轮真的会喂给模型的那个立场」，
任何另建一条回放路径的做法都会在两侧之间制造口径漂移（这正是阶段一 Z-7 钉死的同类问题）。
影子 broker 由 `signals.make_shadow_broker()` 构造，逐根 `evaluate_signal_board(specs, shadows, dfs, i)`，
其中 `dfs` 由 `ai_auto/backtest_frames.build_backtest_frames` 装配（与 AI 回测 `_setup` 同款，含 HTF 帧）。

> **成本量级**：`n = 4000` 根 × `k ≤ 8` 策略的逐根 `on_candle` ≈ 32k 次纯计算调用，实测量级为秒级，
> 全程在 `run_in_threadpool` 内，且只发生在显式 rebuild 时。决策轮**不做任何回放**。

### 3.4 分段、purge/embargo 与显著性

事件研究里的泄漏不在「训练/测试」而在**段边界**：第 `i` 根的事件其前瞻窗口跨到了下一段，两段的统计量就不独立，
IR 与 t 值会被系统性放大。故：

1. `plan_segments(n, segments)` 把 `[0, n)` 等切成 `segments` 段（默认 `DEFAULT_IC_SEGMENTS = 6`，与
   `engine/factors/evaluation.py:20` 同源），切法与 `evaluation._segment_ir`（`evaluation.py:111-133`）
   的 `j*m//segments` 逐字相同。
2. `purge_crossing_events(indices, seg_end, horizon_bars, embargo_bars)` 丢弃 `i + horizon_bars + embargo_bars > seg_end`
   的事件——**跨段的事件不属于任何一段**。`embargo_bars` 默认 12（08 §3.4 关键参数总表）。
3. 逐段统计 `m_j`（段内事件的方向调整收益均值），随后：
   - `ir = mean_j(m_j) / std_j(m_j, ddof=1)`（**不加时间衰减**：IR 刻画的是跨段一致性，加权会污染 std）；
   - `tStat = ir × sqrt(有效段数)`，且**仅当有效段数 ≥ `MIN_IC_SEGMENTS = 6`**（`evaluation.py:21`）才输出，
     否则 `None`——与既有因子评估的裁决逐字相同，不另立一套；
   - `winRate / meanRetBps / avgWinBps / avgLossBps` 用**段级时间衰减权重**的事件级加权：
     `w_j = 0.5 ** (age_days_j / half_life_days)`，`age_days_j = (built_at_ts − seg_end_ts) / 86400`，
     `half_life_days` 默认 30（02 §3.3 / 08 §3.4）。加密市场体制切换快，三个月前的样本权重降到约 1/8。
4. **可用性判据**（`usable`，**五条**全满足才为真，否则记 `unusableReason` 并**不参与在线聚合**）：
   `sampleN ≥ ai_trade_prior_min_samples(默认 30)` ∧ `有效段数 ≥ 6` ∧ `tStat is not None` ∧ `亏损样本数 ≥ 5`
   ∧ **`runN ≥ ai_trade_prior_min_runs(默认 10)`（仅 `strategy_replay` 条目判此条；`signal_edge` 条目
   恒有 `runN == sampleN`，自动满足）**。
   第四条专治「零亏损样本」：此时 `avgLossBps = 0`，EV 退化为 `p·W`，会给出一个不可能的乐观分子。
   第五条（**v2 · P0-2 连带**）专治「重叠根虚增样本」：立场根事件里，`sampleN` 数的是根、`runN` 数的是
   连续立场**段**（`stance` 从非 `d` 翻到 `d` 即新起一段）。一段 200 根的单边趋势能给出 200 个「样本」，
   但它们讲的是**同一件事**——不设 `runN`，`sampleN ≥ 30` 会被一段行情单独满足，显著性判据形同虚设。

### 3.5 分桶与五道加载契约（v2：C-6 为评审新增）

**分桶键**：`(instId, bar, source, key, direction, regime)`。`regime` 取 `engine/regime.per_bar_primaries`
（`regime.py:348`）的**结构轴三值** `trend_up | trend_down | range`，外加一个恒存在的 `all` 桶。
体制枚举沿用仓库既有真源，**绝不新造一套**（03 §3.4 的硬约束）。

> **离线与在线同源已核对**（评审新增）：离线分桶走 `per_bar_primaries`、在线取桶走
> `market_context.build_market_context` → `classify_regime`，二者最终都落到**同一对**纯函数
> `_structural_inputs` + `_decide_structure`（`regime.py:147-168`、`336-346`），故不存在「离线按 A 标、
> 在线按 B 标」的漂移。实现节点**不得**在任一侧另写判据。

**两处 regime 缺失的降级**（v2 · P2-3，均落 `all` 桶而**不是**整键缺席）：
① 离线：`per_bar_primaries` 只从 `max(ema_slow_p, trend_win)` 根起产标签，起算根之前的事件**没有体制标签**
——这些事件只计入 `all` 桶，绝不补一个中性标签（补出来的标签是假数据，C1）；
② 在线：`ai_trade_regime_context=False` 或数据不足时 `build_market_context` 返回 `None`
（`market_context.py:33-37`），此时 `regime=None` ⇒ 直接取 `all` 桶。

**在线降级链**（三级，逐级更宽）：`当前 regime 桶` → `all` 桶 → **整键缺席**。
理由：体制桶天然稀疏（`range` 通常占多数），若只认当前桶，趋势段会大面积无先验；而 `all` 桶始终由全样本构成。

**五道加载契约**（任一不满足 ⇒ 整条 / 整键缺席，沿用「键缺失即整段省略、prompt 渲染逐字节回退」的既有范式）：

| 编号 | 契约 | 判据 | 不满足时 |
|---|---|---|---|
| C-1 | 总闸 | `settings.ai_trade_signal_prior_enabled` | 整键缺席（连加载都不发生） |
| C-2 | 新鲜度 | `now − snapshot.builtAtTs ≤ ai_trade_prior_max_age_s`（默认 7 天） | 整键缺席 + 会话启动推一条 `AI_TRADE_PRIOR_STALE` 告警 |
| C-3 | 持有期同源 | `snapshot.horizonBars == settings.ai_trade_cost_expected_hold_bars` | 整键缺席 + `AI_TRADE_PRIOR_CONTRACT_MISMATCH` 告警 |
| C-4 | 成本口径 | 逐条目 `costBasis == "gross"` | **丢弃该条目**（不是整键缺席；混入的净额条目单独剔除） |
| **C-6** | **策略身份**（v2 · P1-6） | 逐 `strategy_replay` 条目 `strategyFingerprint == sha1(f"{template_key}|{params_json}")` 的现值 | **丢弃该条目** + 首次发现时推一条 `AI_TRADE_PRIOR_CONTRACT_MISMATCH`（附 `strategyId`） |

另有 C-5（标的/周期一致）由 KV 键本身保证：键即 `ai_prior:{instId}:{bar}`，取错标的在物理上不可能。

**C-6 存在的理由**：条目按 `strategyId` 归集，而 `strategies.params_json` / `template_key` 可被用户随时改
（`db/models.py:33-40`，有 `updated_at` 但**不改 id**）。改完之后 C-2 新鲜度与 C-3 持有期全部照过，先验却已经在
描述另一个策略——这和 §10 R-3 要防的「口径漂移」是同一个根因，只是漂移发生在**策略自己身上**而不是回放路径上。
指纹在 `_resolve_prior_snapshots` 里比对：那一层本来就已经为 `_build_specs` 取过策略行，零额外查询。
**刻意不用 `updated_at`**：改名 / 改 `enabled` 也会推进它，那会造成大量无意义的整条目丢弃；指纹只认真正影响
信号的两个字段。

### 3.6 在线聚合：从先验到方向概率 `p`

```
resolve_direction_prior(entries, *, direction, board_view, active_signal_keys, regime, params)
    -> DirectionPrior | None
```

`board_view: list[tuple[int, str]]` 是 `PriorRuntime` 从本轮信号板投影出的 `[(strategy_id, stance)]`
（v2 · P1-4）。**刻意不直接吃 `list[StrategySignalView]`**：`signal_prior.py` 要保持零 pandas、零
`signals` 依赖（后者 import 了 `pandas` 与 `engine.simulator`），投影一行 `[(v.strategy_id, v.stance) for v in board]`
即可，还顺带断开 `signals ⇄ signal_prior` 的潜在循环 import。**更不是 `list[dict]`**——实盘板的真实类型是
dataclass `StrategySignalView`（`signals.py:34-44`，字段 snake_case）。

1. **候选筛选**（贡献者必须此刻真的在说话，历史统计不能凭空发言；筛选条件与 §3.3 的事件定义**逐字同源**）：
   - `strategy_replay` 条目：`strategyId` 在 `board_view` 上且该项 `stance == direction`
     （**v2 · P0-2 删去了 v1 的 `or latest_signal == open_{direction}` 子句**——那一支度量的是边沿、
     用的是立场，是本次评审的头号缺陷）；
   - `signal_edge` 条目：`signalKey ∈ active_signal_keys`（本根触发），且条目 `direction` 与决策方向一致；
   - 两类都必须 `usable`（§3.4）且过 C-4 / C-6。
   - **`active_signal_keys` 的求值下限**（v2 · P2-4）：`detect_signal` 会把前 `SignalParams.warmup = 30` 根
     强制判 `False`（`signal_edge.py:202-203`），故 `PriorRuntime._active_signals` 只在
     `len(df) >= _PRIOR_DETECT_MIN_BARS = 60` 时求值，否则返回 `None`（= 本轮**不知道**哪些信号触发，
     `signal_edge` 侧整体不参与聚合）。**绝不返回空集**：空集会被下游读成「确定没有信号触发」，
     那是把「不知道」伪装成「知道」。
2. **权重**：`w_i = clamp(ir_i, 0.0, ai_trade_prior_ir_clip)`（默认 2.0，02 §3.5）。`Σw == 0` ⇒ 返回 `None`
   （**绝不因为「有条目」就造一个 p 出来**）。
3. **概率聚合**：`p = sigmoid( Σ w_i · logit(clamp(p_i, 0.02, 0.98)) / Σ w_i )`，末端再 `clamp(p, 0.05, 0.95)`，
   其中 **`p_i := entry.winRate`**（§3.4 的段级衰减加权胜率，∈[0,1]；v2 · P2-2 补全定义）。
4. **幅度聚合**：`W = Σ w_i·avgWinBps_i / Σ w_i`，`L = Σ w_i·|avgLossBps_i| / Σ w_i`。
5. **EV**：`ev_bps = p·W − (1−p)·L`（可为负；负即「这组证据在历史上是亏钱的」，是一个**真实结论**，
   不做截断，由门禁去判）。
6. 返回 `DirectionPrior{p, ev_bps, win_rate_raw, w_sum, contributors[≤top_n], regime_used, sample_n_total}`。

> **对 02 §3.5 公式的显式修正**：02 写的是 `logit_total = Σ wᵢ · logit(pᵢ)`（未归一化）。未归一化的对数几率和
> 会随贡献者数量单调发散——3 条各 `p=0.55` 的证据聚合出 `p≈0.65`，5 条聚合出 `p≈0.73`，与每条证据的质量无关。
> 那会让分子随信号板变大而虚增，恰好把本增量要修的乐观偏差换个方向再犯一次。故改用**加权均值**并记在此处。

### 3.7 EV 分子升级与单调保守不变式

`edge_gate.estimate_gross_edge_bps` 增加**两个** keyword-only 形参（默认值即逐字回退现行为）：
`prior_ev_bps: float | None = None` 与 **`prior_binding: bool = False`**（v2 · P1-2）。前者是读数、
后者才决定它是否进 `min` 链：

```
target = atr_bps × atr_target_mult                    # 既有：ATR 波动预算
if take_profit_bps is not None and take_profit_bps < target:  target = take_profit_bps   # 既有：OCO 止盈上界
would_target = min(target, prior_ev_bps) if prior_ev_bps is not None else target   # 反事实：若绑定会取到多少
if prior_binding and prior_ev_bps is not None and prior_ev_bps < target:
    target, source = prior_ev_bps, "prior"            # 仅总闸③开启时真的收紧
source ∈ {"atr", "take_profit", "prior"}              # 取到最小值的那一项
```

**为什么把「读数」与「绑定」拆开**：总闸③（`ai_trade_edge_prior_enabled`）的全部价值是把「先验只展示」
与「先验进门禁」分成两个可分别归因的观察窗口（L1 vs L3）。但 v1 在③关闭时让 `prior_ev_bps` 恒为 `None`，
于是**开③之前唯一该做的那件事——「先看看它会拦掉多少」——恰恰做不到**，运维只能闭眼直接开门禁。
拆开之后：`prior_ev_bps` 无条件透传、无条件进 wire（`priorEvBps` / `priorWouldTargetBps` / `priorWouldVeto`），
`prior_binding=False` 时 `target` 与 `source` **逐字段等于**合入前——零行为改变，却拿到了完整的反事实读数。
`priorWouldVeto := (would_target < required)`，与真实判据同一个 `required`，不另算一份。

**不变式 EV-1（单调保守）**——**v2 把它从「估算值层级」上提到「门禁产出层级」**（P1-1）：

> 固定 `(settings.k_min, degraded_mult, atr_target_mult)` 与同一轮输入，
> **开启先验后被放行的开仓轮集合 ⊆ 关闭先验时被放行的集合**。等价说法：先验绝不把任何一轮从 `veto` 翻成 `pass`。

v1 只写了 `target_bps(prior=x) ≤ target_bps(prior=None)`。那个式子**不足以**保证安全性质：`required` 也是一个
可动的量，只要有人给 prior 分支单独放松阈值，字面上的 EV-1 仍然成立而真实开仓反而变多。反例（也是 R-4 压力下
最容易被想到的那个「缓解方案」）：`atr=20bps, prior=18bps, C=13bps`；`k_min=2` ⇒ `required=26`，两侧都 veto；
若给 prior 分支单设 `k_prior=1` ⇒ `required=13`，`18 ≥ 13` **放行**——一个本该更保守的机制凭空造出了新仓位。

因此三条硬约束，实现节点不得违反：

1. **禁止任何 prior 专属的阈值放松**：`required = k_min × C × degraded_mult` 是**唯一**阈值公式，不得因
   `source == "prior"` 而改变。要调松紧只能调全局 `k_min`（并接受它同时作用于 ATR 分支，见下）。
2. **EV-1 的前提是阈值固定**。§10 R-4 的回滚阶梯里「`k_min` 下调 1.5 → 0」**不是**纯回滚动作：它同时放松了
   先验缺席轮的 ATR 分支，相对当前生产行为是**更宽**而非更窄。runbook 与告警文案都必须这么说，
   不得把它描述成「回到先验开启前」。
3. **property test 按产出级断言**（§8.2 G-2 / T-11）：随机 200 组 `(atr, tp, prior, cost, k)`，
   断言 `apply_edge_gate(prior_binding=True)` 的 veto 集合 ⊇ `prior_binding=False` 的 veto 集合，
   而不只是比较 `target_bps`。

代价是本增量**只能减少交易、不能增加交易**，因此「过度收紧 ⇒ 永不开仓」是本增量的头号风险，缓解见 §10 R-4。

**量纲提醒（必须写进 L3 的判读里）**：ATR 分支的分子是「若这笔走对能吃到多少」的**幅度**，而先验分支的分子是
**已经按胜率折算过的期望**——同为 bps 毛口径、可比，但**数量级天然差一档**（幅度 O(数十 bps) vs 期望 O(数 bps)）。
现行 `k_min` 是对着幅度分子拍的，直接套到期望分子上，`k_min=2.0` 意味着要求「期望收益 ≥ 2 倍往返成本」，
这是一个非常高的门槛。**L3 不得沿用 L0 的 `k_min` 直接开闸**，必须先读 L1/L2 窗口里 `priorEvBps` 的实际分布
（P1-2 让它可读）再定 `k_min`，并在 §九 L3 的放行判据里核对零开仓监控。

**`prior_ev_bps ≤ 0` 的处理**：不特殊处理。`target` 变成 ≤ 0，而 `required = k_min × C × degraded_mult > 0`
（`k_min > 0` 时），故必然 veto，`gate_reason` 形如 `edge_below_cost(edge=-3.2bps,cost=13.0bps,k=2.0)`——
负数原样呈现，**不夹到 0**：把「历史上这组证据亏 3.2bps」粉饰成「赚 0」是自欺。

**fail-open 极性不变**：`prior_ev_bps is None`（先验缺席 / 无贡献者 / 契约不满足）时整条链退化为阶段一的
ATR 粗估，`apply_edge_gate` 的四条不变式（`edge_gate.py:8-20`）逐字保持。

**一处如实披露的耦合**（评审核对发现，有意不改）：`estimate_gross_edge_bps` 在判定价或 ATR 不可用时
**整体返回 `None`**（`edge_gate.py:93-99`），先验分支在它之后，故 **ATR 不可用的暖机断面上，即便先验说
「这组证据历史上亏钱」，门禁仍然 fail-open 放行**。这不是疏漏而是取舍：把先验单独提到 ATR 判空之前，
等于在暖机断面上新开一条 fail-closed 路径，正是 `edge_gate.py:11-13` 明令避免的「反思致永不开单」型事故。
影响面有限（ATR 不可用只发生在窗口前 `atr_period` 根），且与 EV-1 不冲突（那只会让本轮**更宽松**，
而 EV-1 断言的是「不比关闭先验时更宽松」——关闭时同样放行）。T-13 须为这条路径留一条断言。

### 3.8 加权共识（并列函数）

```
compute_weighted_consensus(board, priors) -> WeightedConsensus | None
```
放在 `ai_auto/signals.py`（现 237 行，+≈70 行后仍 < 800 红线），与 `compute_consensus`（`signals.py:59`）**并列**。
产物 `{p_long, p_short, net_stance, weight_long, weight_short, contributors_long, contributors_short, coverage}`：

- `net_stance`：`p_long − p_short > tie_eps(0.02)` ⇒ `long`；反向 ⇒ `short`；否则 `flat`；
- `coverage`（**v2 · P0-2 连带修正**）：分母**只数本轮真的在说话的策略**，即 `stance ∈ {long, short}` 的板上
  策略数；分子是其中「该 `(strategyId, stance, 命中桶)` 有 `usable` 条目」的个数。v1 的「有先验的板上策略数 /
  板上策略数」把 `stance == flat` 的策略也算进分母——空仓策略本就不该有方向先验，把它记成「未覆盖」会让
  02 §五 的「覆盖率 ≥ 80%」在一个健康系统上永远不达标，运维据此会去做错误的补救（拉长回看窗口）。
  分母为 0（全板空仓）⇒ `coverage = None`，**不是 0.0**：没有分母时报 0% 是在制造一个假的故障信号；
- 两侧都无贡献者 ⇒ 返回 `None` ⇒ payload 不带 `consensusWeighted` 键 ⇒ prompt 逐字回退。

**本轮不接门禁**：`apply_consensus_gate`（`signals.py:137`）的入参与行为逐字不变，加权结果只进 payload / prompt /
轨迹与 EV 分子。理由见 §2.2。

### 3.9 prompt 渲染段

新增 `prompts._format_signal_priors(priors: dict | None) -> str`（约 18 行，与既有 `_format_market_context`
`_format_consensus` `_format_factors` 同构：`if not priors: return ""`），在 `build_decision_prompt`
（`prompts.py:436`）里插在**共识行之后、`marketDigest` 三行之前**（证据相邻性：共识 → 共识的历史质量 → 行情证据）：

```
信号先验（历史统计，非当下保证；毛口径，前瞻 8 根，快照 2026-07-20 · 3 天前 · 体制桶=range）：
做多 p=0.54（3 条证据，IR 加权），做空 p=0.47（1 条）；覆盖率 3/4。
本轮触发证据：RSI 超卖（p=0.56，样本 214，IR 1.2）｜[12] EMA 交叉（p=0.52，样本 88，IR 0.6）｜…（至多 5 条）
```

三条渲染硬约束：① 必须写明「历史统计，不是当下保证」（03 §3.3 对因子 IC 的同款要求，本层同理）；
② 至多 `ai_trade_prior_top_n = 5` 条（prompt 单段信息密度上限，超过稀释注意力）；③ 键缺失 ⇒ 整段省略 ⇒
`build_decision_prompt` 对不含 `signalPriors` 的 payload 输出**逐字节**与合入前相同（G-1 断言）。

**系统 prompt（`build_ai_trade_system_prompt`，`prompts.py:70`）本增量一字不改**，见 §2.2。

### 3.10 轨迹、事件与可观测

不新增 trace phase（镜像利润保护层的判断：新增 phase 的代价是前端 6 处镜像点），改为**加法键**：

| 载体 | 新增键 | 说明 |
|---|---|---|
| `trace.new_round_trace`（`trace.py:91`） | `signalPriors`（取自 payload） | 轮级输入快照；未装配 ⇒ `None` |
| `trace.apply_edge_gate_trace`（`trace.py:186`） | `edgeSource` | 前端据此改判「分子是怎么来的」文案 |
| `edge_gate.evaluate_edge_gate` 的 `wire` | `priorP` / `priorEvBps` / `priorContributors` / **`priorWouldTargetBps`** / **`priorWouldVeto`** | 已有 `edgeSource`，本轮补五个只读读数；**后两个是 P1-2 的反事实读数，总闸③关闭时照发** |
| `CostRuntime.snapshot()` **与** `snapshot_if_due()` | `priorCoverage` / `priorAgeS` | **两条通道都要并入**（见下方 P1-3） |
| 会话启动 | `AI_TRADE_PRIOR_STALE` / `AI_TRADE_PRIOR_CONTRACT_MISMATCH` 两条 `error` 事件 | 静默降级与静默保守一样会被当成故障 |

**P1-3：`costSnapshot` 的两条通道互斥且不同源，必须两条都改。** 核对活树的结果：

| 通道 | 触发条件 | 装配路径 |
|---|---|---|
| `edge_gate` 事件里的 `costSnapshot` | **`k_min > 0`**（门禁生效） | `session.py:830` → `CostRuntime.snapshot()` → `build_cost_stats` |
| `status` 事件里的 `costSnapshot` | **`k_min <= 0`**（门禁空转，每 10 轮一次） | `session.py:836` → `CostRuntime.snapshot_if_due()` → `cost_snapshot_wire` → `build_cost_stats`（**绕开 `snapshot()`**） |

v1 只说改 `snapshot()`，于是默认配置（`k_min = 0.0`，`config.py:273`）下运维按 runbook 去 `status` 事件里找
`priorCoverage` 会**永远找不到**。且 v1 没有交代 `CostRuntime` 从哪拿到先验读数——它不持有 `PriorRuntime`。

**定案**：`CostRuntime` 增一个只写缝 `set_prior_view(view: dict | None) -> None`（会话每轮从
`PriorRuntime.coverage_wire()` 回灌一次，`None` ⇒ 全部先验键缺席），`snapshot()` 与 `snapshot_if_due()`
**各自并入同一份 `self._prior_view`**。落 `metricsJson.aiStats` 的 `merge_into` 走的也是 `snapshot()`，
故会话停止后记的是**最后一轮**的覆盖率读数——这是有意的（覆盖率是轮级瞬时量，不是会话累计量），
须在字段注释里写明，避免被误读成全程平均。

### 3.11 与既有诊断文档的显式偏离（四条，逐条给理由）

| 编号 | 02/08 号文档怎么写 | 本设计怎么做 | 理由 |
|---|---|---|---|
| **D-1** | 策略先验走 `engine/backtester.run_backtest` + `SimBroker` 重放（02 §3.1 表格第二行） | 走**影子信号板逐根回放**（`signals.evaluate_signal_board`）+ 统一前瞻收益估计量 | `run_backtest` 的成交盈亏里混入了策略**自己的**止损/止盈/最大持有根数，度量出来的是「策略的退出策略好不好」；而 AI 自动交易里退出由 AI 决定、不继承策略退出。我们要的 `p` 是「该策略说多时，未来 h 根价格是否真的向上」。另外影子回放与实盘信号板共用同一函数，杜绝口径漂移 |
| **D-2** | 08 §3.2 只说「阶段二」不区分实盘/回测 | AI 回测侧**不接入**先验（`prior_ev_bps=None`） | 先验窗口与回测窗口重叠即前视偏差，且回测阶段完全看不出来（03 §六 自我反驳二）。正确做法是 out-of-fold 先验，属 06 号闸 1 的范围 |
| **D-3** | 02 §3.5 `logit_total = Σ wᵢ · logit(pᵢ)` | 改为加权**均值** | 见 §3.6 末的论证：未归一化会让 p 随贡献者数量发散 |
| **D-4**（v2 · P0-2） | 02 §3.1 把策略先验描述为「**进场信号**后的前瞻收益」 | 策略侧事件改为**持久立场根** | AI 每轮拿到的条件是板上的 `stance`，不是进场边沿；度量边沿、应用于立场，`p` 就是一个没有定义的数（§3.2 推论 5）。代价（样本重叠）由 §3.4 的 `runN` 判据承担 |

**两个可选项的评审裁决**（原文为「交给评审节点拍板」，裁决理由见 §十三，实现节点按此执行）：

- **O-1 `plan_purged_windows`：移出本轮**（否决设计建议）。它在本增量里没有任何消费方，而 `CLAUDE.md` §四
  明令「删除一切僵尸代码」。更实际的理由是：06 号闸 1 到来时才知道 train/test 窗口的真实形状（步长、是否
  anchored、要不要按体制分层），现在先写一个没人用的版本，大概率到时要推翻重写，还得先说服自己「既有单测
  是不是在守一个错的形状」。`plan_segments` + `purge_crossing_events` 本轮**真的被消费**，保留。
- **O-2 前端「重建先验」按钮：不做**（采纳设计建议）。三闸默认关，L0/L1 的读者是运维；做了按钮反而会诱导
  用户在未验证前打开。

---

## 四、接口设计（Interface design）

### 4.1 REST

#### `POST /ai-trade/priors/rebuild`

离线重建某 `(instId, bar)` 的先验快照。**同步执行**（在 `run_in_threadpool` 内），返回快照摘要。

```jsonc
// Request（AiTradePriorRebuildIn）
{
  "instId": "BTC-USDT-SWAP",
  "bar": "15m",
  "strategyIds": [12, 31],        // ≤ settings.ai_trade_max_strategies（8）；空数组=只建 signal_edge 侧
  "lookbackBars": 4000,           // 可选，缺省 = ai_trade_prior_lookback_bars（默认 4000）
  "segments": 6                   // 可选，缺省 = 6（DEFAULT_IC_SEGMENTS）
}
```

```jsonc
// 200（AiTradePriorSnapshotOut）
{
  "instId": "BTC-USDT-SWAP", "bar": "15m",
  "builtAtTs": 1785000000, "asOfTs": 1784996400,
  "horizonBars": 8, "costBasis": "gross",
  "bars": 4000, "segments": 6, "purgeBars": 8, "embargoBars": 12,
  "entryCount": 42, "usableCount": 27,
  "coverage": { "strategies": 2, "strategiesWithPrior": 2, "signals": 8, "signalsWithPrior": 6 },
  "entries": [ /* SignalPriorEntry[]，见 §5.1 */ ]
}
```

错误码（全部走仓库既有 `{error:{code,message}}` 信封）：

| 状态 | code | 触发 |
|---|---|---|
| 400 | `AI_PRIOR_BAR_INVALID` | `bar` 不在 `BAR_SECONDS`（`okx/types.py:93`） |
| 400 | `AI_TRADE_STRATEGIES_INVALID` | 复用 `_build_specs`（`api/ai_auto.py:140`）的既有校验与码。**策略指纹（C-6）在这一步顺带算出**：`_build_specs` 已逐个取过 `repo.get_strategy_row`，`template_key` / `params_json` 就在手上，零额外查询 |
| 413 | `AI_PRIOR_TOO_LARGE` | `lookbackBars > settings.ai_trade_prior_max_bars`（默认 8000）——**取数前**快速失败，绝不无界拉 K 线（与 `factor_train_max_bars` 同款姿态） |
| 409 | `AI_PRIOR_REBUILD_BUSY` | 同一 `(instId,bar)` 已有重建在飞（进程内 `set` 守门） |
| 422 | `AI_PRIOR_INSUFFICIENT_BARS` | 实得根数 < `max(segments × 60, 300)`，**不假数据**（C1 惯例） |

#### `GET /ai-trade/priors?instId=&bar=`

读当前快照。无记录 ⇒ `200 {"exists": false}`（不是 404——「还没建」是正常状态，不是错误）。
有记录 ⇒ `{"exists": true, "snapshot": AiTradePriorSnapshotOut, "contract": {"fresh": true, "horizonMatch": true}}`，
`contract` 直接把 §3.5 的 C-2/C-3 判定结果告诉运维，省得对着时间戳心算。

### 4.2 WebSocket（全部为加法键，老前端忽略未知字段）

- `agent_trace` / `phase="edge_gate"`：`data` 增加 `priorP`、`priorEvBps`、`priorContributors`（`edgeSource` 已存在，取值域扩一个 `"prior"`）。
- `agent_trace` / `phase="round_start"`：`data.signalPriors`（同 payload 键）。
- `status`：`costSnapshot` 增加 `priorCoverage` / `priorAgeS`。
- `error`：新增两个 code（§3.10）。

### 4.3 内部函数签名（实现节点按此逐字落地）

```python
# engine/walk_forward_purge.py
def plan_segments(n: int, segments: int) -> list[tuple[int, int]]: ...
def purge_crossing_events(indices: list[int], *, seg_end: int, horizon_bars: int,
                          embargo_bars: int) -> list[int]: ...
# （O-1 的 plan_purged_windows 已按评审裁决移出本轮，见 §3.11）

# engine/signal_edge.py（新增 3 行公开包装，前瞻收益估计量的唯一真源）
def forward_return_at(opens: tuple, closes: tuple, i: int, h: int, *,
                      entry_at: str = "next_open", direction: str = "long") -> float: ...

# ai_auto/signal_prior.py（纯 Python，无 pandas）
@dataclass(frozen=True)
class SignalPriorEntry: ...            # 字段见 §5.1
@dataclass(frozen=True)
class DirectionPrior: ...              # p / ev_bps / contributors / regime_used / …
@dataclass(frozen=True)
class PriorParams:                     # 从 settings 解析一次，纯函数据此运算
    horizon_bars: int; min_samples: int; ir_clip: float; top_n: int
    max_age_s: float; tie_eps: float
def resolve_prior_params(settings) -> PriorParams: ...
def load_snapshot(raw: dict | None, params: PriorParams, *, now_wall: float,
                  fingerprints: dict[int, str] | None = None
                  ) -> tuple[list[SignalPriorEntry], str | None]: ...   # (entries, reject_reason)
def resolve_direction_prior(entries, *, direction: str,
                            board_view: list[tuple[int, str]],          # [(strategy_id, stance)]，P1-4
                            active_signal_keys: set[str] | None,        # None = 本轮不知道（P2-4）
                            regime: str | None,
                            params: PriorParams) -> DirectionPrior | None: ...
def prior_wire(long_: DirectionPrior | None, short: DirectionPrior | None,
               weighted, meta: dict) -> dict | None: ...

# ai_auto/prior_builder.py（离线）
def build_prior_snapshot(df, specs, frames, *, inst_id: str, bar: str, horizon_bars: int,
                         segments: int, embargo_bars: int, half_life_days: float,
                         min_samples: int, min_runs: int, built_at_ts: int,
                         fingerprints: dict[int, str]) -> dict: ...

# ai_auto/prior_runtime.py
class PriorRuntime:
    def __init__(self, raw_snapshot: dict | None, settings) -> None: ...
    @property
    def alerts(self) -> list[dict]: ...                       # 启动时一次性推的降级告警
    def build(self, board: list["StrategySignalView"], market_context: dict | None,
              df) -> dict | None: ...                          # 内部做 board_view 投影（P1-4）
    def last_edge_bps(self, action: str) -> float | None: ...  # 本轮 build() 的缓存读数
    def coverage_wire(self) -> dict | None: ...                # 回灌 CostRuntime.set_prior_view（P1-3）

# ai_auto/signals.py（并列新函数，compute_consensus 一字不动）
@dataclass
class WeightedConsensus: ...
def compute_weighted_consensus(board: list[StrategySignalView],      # 真实类型，非 list[dict]（P1-4）
                               priors) -> WeightedConsensus | None: ...

# ai_auto/cost_runtime.py（P1-3：先验读数的唯一入口）
class CostRuntime:
    def set_prior_view(self, view: dict | None) -> None: ...   # snapshot() 与 snapshot_if_due() 同时并入
    def evaluate(self, decision, *, last_px, atr_px, take_profit_pct, bar_seconds,
                 prior_ev_bps: float | None = None,
                 prior_binding: bool = False) -> EdgeGateOutcome | None: ...

# ai_auto/edge_gate.py（既有函数加 keyword-only 形参，默认值即零回归）
def estimate_gross_edge_bps(*, action, ref_px, atr_px, take_profit_pct, atr_target_mult,
                            prior_ev_bps: float | None = None,
                            prior_binding: bool = False) -> EdgeEstimate | None: ...
def evaluate_edge_gate(decision, *, settings, last_px, atr_px, take_profit_pct, bar_seconds,
                       funding=None, params=None, funding_enabled=None,
                       prior_ev_bps: float | None = None,
                       prior_binding: bool = False) -> EdgeGateOutcome | None: ...

# ai_auto/manager.py（P1-5：分组是多标的，快照必须按 instId 映射）
@dataclass
class AiTradeGroupStartRequest:
    prior_snapshots: dict[str, dict] | None = None    # {instId: 快照原文}；缺键 ⇒ 该成员无先验（不是错误）

# db/ai_prior_repositories.py
def prior_key(inst_id: str, bar: str) -> str: ...             # f"ai_prior:{inst_id}:{bar}"（≤64）
async def load_prior_snapshot(session, inst_id: str, bar: str) -> dict | None: ...
async def save_prior_snapshot(session, snapshot: dict) -> None: ...
```

---

## 五、数据模型（Data model）

### 5.1 `SignalPriorEntry`（快照条目，camelCase 落库）

| 字段 | 类型 | 说明 |
|---|---|---|
| `source` | `"signal_edge" \| "strategy_replay"` | 两路来源 |
| `event` | `"trigger" \| "stance"` | **v2 · P0-2**：条件事件的名字，必须与在线取用条件同源（§3.3）。`signal_edge` 恒 `trigger`、`strategy_replay` 恒 `stance`。写进数据是为了让口径漂移在**数据层**就能被发现，而不是只活在文档里 |
| `signalKey` | `str \| null` | `source == "signal_edge"` 时非空 |
| `strategyId` / `strategyName` | `int \| null` / `str` | `source == "strategy_replay"` 时非空 |
| `strategyFingerprint` | `str \| null` | **v2 · P1-6**：`sha1(f"{template_key}\|{params_json}")` 前 16 位；C-6 校验字段 |
| `direction` | `"long" \| "short"` | 事件方向（`signal_edge` 侧由 `SIGNAL_LIBRARY` 固定） |
| `regime` | `"all" \| "trend_up" \| "trend_down" \| "range"` | 分桶键 |
| `winRate` | `float` | 衰减加权胜率 ∈ [0,1] |
| `meanRetBps` / `avgWinBps` / `avgLossBps` | `float` | 毛口径 bps；`avgLossBps` 存**正数**（幅度） |
| `sampleN` / `lossN` / `segments` | `int` | 事件总数 / 亏损样本数 / 有效段数 |
| `runN` | `int` | **v2 · P0-2**：独立事件段数（`stance` 连续为 `d` 的段数）；`signal_edge` 条目恒 `== sampleN` |
| `ir` / `tStat` | `float \| null` | 段间一致性；`segments < 6` ⇒ `tStat = null` |
| `usable` / `unusableReason` | `bool` / `str \| null` | §3.4 **五**条判据 |
| `costBasis` | `"gross"` | C-4 校验字段（**恒为 gross**，见 §3.2） |

### 5.2 `PriorSnapshot`（KV value，整体一行 JSON）

存储：`app_settings`（`db/models.py:384`，`key ≤ 64`），键 `ai_prior:{instId}:{bar}`
（`ai_prior:BTC-USDT-SWAP:15m` = 26 字符，余量充足）。读写复用 `repositories.get_app_setting`
（`repositories.py:410`）/ `set_app_setting`（`repositories.py:421`）——**零新表、零迁移、坏 JSON 自动回落 None**。

顶层字段：`version(=1) / instId / bar / builtAtTs / asOfTs / horizonBars / costBasis / bars / segments /
purgeBars / embargoBars / halfLifeDays / minSamples / minRuns / entries[] / coverage{}`
（后两个门槛写进快照是为了让「这份数据是按什么标准判 `usable` 的」可追溯——加载侧按**快照里的值**解释既有
`usable` 标记，按**当前 settings** 判新条目，二者不一致时以快照为准并在 `GET /ai-trade/priors` 的 `contract`
里如实回显）。
一个典型快照（8 信号 + 2 策略 × 2 方向 × 至多 4 桶）约 40~100 条目、20~50 KB JSON，单行 KV 完全够用。

### 5.3 `payload["signalPriors"]`（决策轮 wire，只读）

```jsonc
{
  "asOfTs": 1784996400, "ageS": 259200, "horizonBars": 8, "costBasis": "gross",
  "regimeUsed": "range",                      // 实际命中的桶（可能是降级到的 "all"）
  "long":  { "p": 0.54, "evBps": 11.2, "wSum": 2.4, "sampleN": 302,
             "contributors": [ {"label": "RSI 超卖", "source": "signal_edge", "p": 0.56,
                                "ir": 1.2, "sampleN": 214} ] },
  "short": { "p": 0.47, "evBps": -2.1, "wSum": 0.8, "sampleN": 88, "contributors": [ … ] },
  "weighted": { "pLong": 0.54, "pShort": 0.47, "netStance": "long", "coverage": 0.75 },
  "coverage": { "boardWithPrior": 3, "boardTotal": 4 }
}
```

`long` / `short` 任一为 `null` 表示该方向本轮无贡献者（**不是 p=0.5**）。整个键缺席表示先验层未装配或契约不满足。

---

## 六、文件 / 模块改动计划

### 6.1 新建（后端 5 + 测试 6）

| 文件 | 意图（一行） |
|---|---|
| `services/quant/app/engine/walk_forward_purge.py` | 分段 / 跨段事件 purge-embargo，纯函数（O-1 的窗口函数已按 §十三 裁掉） |
| `services/quant/app/ai_auto/signal_prior.py` | 先验数据模型 + 五道加载契约 + 方向聚合 + wire（纯 Python，无 pandas） |
| `services/quant/app/ai_auto/prior_builder.py` | 离线事件研究：两路事件抽取 → 分段统计 → 衰减加权 → 快照装配 |
| `services/quant/app/ai_auto/prior_runtime.py` | 会话级 facade（镜像 `CostRuntime`）：契约校验 / 逐轮装配 / 缓存本轮 EV / 告警 |
| `services/quant/app/db/ai_prior_repositories.py` | `app_settings` 上的先验 KV 读写与键构造 |
| `services/quant/tests/test_walk_forward_purge.py` | 分段切法与 `_segment_ir` 逐字一致；跨段事件被 purge；退化入参 |
| `services/quant/tests/test_ai_trade_signal_prior.py` | 加载契约 C-1~C-4 **与 C-6**、regime 降级链、logit 加权均值、`Σw==0 ⇒ None`、coverage 口径（T-22） |
| `services/quant/tests/test_ai_trade_prior_builder.py` | 植入已知 edge 的合成序列可被还原；无前视；衰减；`usable` **五**判据；**事件同源与 `runN`（T-21）** |
| `services/quant/tests/test_ai_trade_prior_gate.py` | **产出级 EV-1 property test（T-11）** + 读数/绑定解耦（T-19）+ `source` 标注 + 缺席与 ATR 不可用回退 |
| `services/quant/tests/test_ai_trade_prior_session.py` | 会话零回归（`prior_snapshot=None` ⇒ payload/prompt 逐字节）+ 装配后键与告警 + **分组按 `instId` 取快照（T-20）** |
| `services/quant/tests/test_ai_trade_prior_api.py` | rebuild/get 端点的 400/409/413/422 守门与落库形状 |

### 6.2 修改（后端 11 + 配置样例 1）

| 文件 | 现行数 | 净增上限 | 意图（一行） |
|---|---|---|---|
| `services/quant/app/config.py` | 392 | +30 | 新增 3 个总闸 + **10** 个参数（共 13 个字段，`AI_TRADE_PRIOR_*` 前缀，须**真的声明**否则被 `extra="ignore"` 静默丢弃） |
| `services/quant/app/ai_auto/signals.py` | 237 | +72 | 并列 `compute_weighted_consensus` + `WeightedConsensus`；`compute_consensus` 一字不动 |
| `services/quant/app/ai_auto/edge_gate.py` | 227 | +44 | `estimate_gross_edge_bps` / `evaluate_edge_gate` 增 `prior_ev_bps` + `prior_binding` 形参 + `source="prior"` + wire 五键（含 P1-2 的两个反事实读数） |
| `services/quant/app/ai_auto/cost_runtime.py` | 93 | +14 | `evaluate()` 透传 `prior_ev_bps` / `prior_binding`；新增 `set_prior_view()`；`snapshot()` **与** `snapshot_if_due()` 并入 `priorCoverage/priorAgeS`（P1-3） |
| `services/quant/app/ai_auto/session.py` | 1452 | **+20（硬上限）** | 构造 `PriorRuntime` + payload 一处装配 + 门禁一处透传 + 每轮回灌 `set_prior_view` + 启动推告警，**只允许薄封装**（v2 · P2-5：v1 的 +14 未计入 ctor 形参、先验视图回灌与本仓库的注释密度；仍超即再下沉一层，不得再放宽） |
| `services/quant/app/ai_auto/manager.py` | 644 | +14 | `AiTradeGroupStartRequest.prior_snapshots: dict[str, dict] \| None` **按 `instId` 取**后传 `_build_member`（P1-5：`base_rule` 是分组共享单对象，先验快照不是，不能字面镜像） |
| `services/quant/app/ai_auto/prompts.py` | 656 | +24 | `_format_signal_priors` + `build_decision_prompt` 一处插入；系统 prompt **不改** |
| `services/quant/app/ai_auto/trace.py` | 193 | +6 | `new_round_trace` 带 `signalPriors`；`apply_edge_gate_trace` 带 `edgeSource` |
| `services/quant/app/engine/signal_edge.py` | 333 | +8 | `forward_return_at` 公开包装（前瞻收益估计量单一真源，杜绝在 builder 里复制一份） |
| `services/quant/app/api/ai_auto.py` | 368 | +58 | `_resolve_prior_snapshots` + rebuild/get 两个端点（业务逻辑全在 `prior_builder`，本层只做守门与编排） |
| `services/quant/app/schemas.py` | — | +34 | `AiTradePriorRebuildIn` / `AiTradePriorEntryOut` / `AiTradePriorSnapshotOut` |
| `.env.example` | — | +15 | 13 个新 env 的注释与默认值（与 `config.py` 逐字对齐） |

### 6.3 修改（前端 4 + 测试 1）—— **v2 · P0-1 重写**

| 文件 | 编辑点 | 意图（一行） |
|---|---|---|
| `apps/web/lib/aiTradeTypes.ts` | `:303`（WS `edge_gate` 事件） | `edgeSource` 联合类型加 `"prior"`；事件加 `priorP` / `priorEvBps` / `priorWouldVeto` |
| `apps/web/lib/aiTradeTypes.ts` | `:343-346`（`AiTradeRoundTrace`） | **新增** `edgeSource` / `priorP` / `priorEvBps` / `signalPriors` 四个可选键（此处**现在一个都没有**） |
| **`apps/web/lib/store.ts`** | `:153-156`（`buildRoundTrace` 初值） | 新键的初值 `null`；`signalPriors: evt.signalPriors ?? null`（`round_start` 也是逐键手抄，`:120-158`） |
| **`apps/web/lib/store.ts`** | `:209-217`（`reduceRoundPhase` 的 `edge_gate` 分支） | `edgeSource: evt.edgeSource ?? null` 等四键——**不改这里，后端发什么前端都收不到** |
| `apps/web/lib/aiTrade.ts` | `:752` `describeEdgeGate` | `Pick<…>` 扩到 `edgeSource`/`priorP`，`prior` 时标签带 `p=` 读数 |
| `apps/web/components/Agent/AiTradeTraceRound.tsx` | `:233-259` `EdgeGateBlock` | 说明文案按 `edgeSource` 三分（现文案把「粗估毛收益 = min(ATR 预算, 单仓止盈距离)」写死，接入先验后**它会说谎**） |
| `apps/web/lib/aiTradeEdgeGate.test.ts` | — | 补 `prior` 分支断言；既有四条断言逐字保留（零回归） |

> **前端镜像点清点（v1 的数字是错的，以下为逐条 grep 复核后的真值）**：`edgeSource` 在 `apps/web` 里
> **只出现 1 处**（`aiTradeTypes.ts:303`），`AiTradeRoundTrace` 里**根本没有**这个字段——现有的门禁四键是
> `edgeGateStatus / edgeBps / edgeCostBps / edgeGateReason`（`aiTradeTypes.ts:343-346`）。真正的传递链是
> **后端 wire → WS 事件类型 → `store.ts` 逐键手抄 → `AiTradeRoundTrace` → `describeEdgeGate` → `EdgeGateBlock`**，
> 其中 `store.ts` 是 v1 整个漏掉的一环，也是唯一会**静默吞掉**新字段的一环（它不是 `{...evt}` 展开，
> 是一行一个键的显式赋值）。本仓库历史上**两次**在这类改动里漏掉最后一处渲染门控
> （`ai-trade-profit-protection` RV-2、`ai-trade-limit-orders` 第 14 镜像点），第三次的形状就在这里。
> 对账方式改用 §8.2 G-8 的**逐点核对表**——`grep -rn "edgeSource"` 在只有 1 处出现时天然满足，是空转门禁。

### 6.4 文档标记（3 个文件，逐字方案见 §11）

`docs/improve/01-ai-autotrading/00-总纲与现状诊断.md`、`02-信号层重构与元标签.md`、`08-实施路线图与里程碑.md`。

---

## 七、配置与回滚开关

| 字段（env 同名大写） | 默认 | 关闭语义 | 归属 |
|---|---|---|---|
| `ai_trade_signal_prior_enabled` | **`False`** | 快照不加载、payload 无键、prompt 逐字回退、EV 分子回退 ATR | 总闸①（02 §六） |
| `ai_trade_weighted_consensus_enabled` | **`False`** | `consensusWeighted` 整键缺席，等权共识与门禁逐字不变 | 总闸②（02 §六） |
| `ai_trade_edge_prior_enabled` | **`False`** | 先验只做展示，**不**进 edge 分子（`prior_ev_bps` 恒 `None`） | 总闸③（本设计新增，用于把「展示」与「门禁」分两步验证，落实 04 §3.4「先观测再门禁」） |
| `ai_trade_prior_horizon_bars` | *（不新增字段）* | — | 恒取 `ai_trade_cost_expected_hold_bars`（§3.2 推论 1，单一真源） |
| `ai_trade_prior_min_samples` | `30` | — | 单条先验的事件数下限 |
| `ai_trade_prior_min_runs` | `10` | `0` ⇒ 关闭「独立段数」判据（**不建议**：立场根高度重叠，关掉后一段行情即可满足 `min_samples`） | v2 · P0-2 新增，仅作用于 `strategy_replay` 条目 |
| `ai_trade_prior_min_segments` | `6` | — | 与 `MIN_IC_SEGMENTS` 同源；调低即放松显著性 |
| `ai_trade_prior_decay_half_life_days` | `30` | 置极大 ⇒ 关闭时间衰减 | 02 §3.3 / 08 §3.4 |
| `ai_trade_prior_embargo_bars` | `12` | `0` ⇒ 关闭跨段隔离 | 08 §3.4 |
| `ai_trade_prior_ir_clip` | `2.0` | `0` ⇒ 全部权重为 0 ⇒ 先验恒缺席（等价关闭） | 02 §3.5 |
| `ai_trade_prior_top_n` | `5` | `0` ⇒ prompt 不列证据明细（仍给聚合 p） | 03 §3.3 同款密度上限 |
| `ai_trade_prior_max_age_s` | `604800`（7 天） | 置极大 ⇒ 关闭陈旧度门禁（**不建议**） | 02 §五 |
| `ai_trade_prior_lookback_bars` | `4000` | — | rebuild 缺省回看根数 |
| `ai_trade_prior_max_bars` | `8000` | — | rebuild 取数前硬上限（413 快速失败） |

**总回滚**：三个总闸置 `False` ⇒ 系统逐字节回到 `96f554d` 后的行为；KV 里遗留的快照行不被读取，
也无任何副作用（稀疏表，无外键、无级联）。

---

## 八、测试与验收（Testing & acceptance criteria）

### 8.1 单元测试（新增 6 个文件，逐条列出必须覆盖的行为）

| # | 测试 | 断言 |
|---|---|---|
| T-1 | `plan_segments` 与 `evaluation._segment_ir` 切法一致 | 对 `n ∈ {60, 199, 4000}`、`segments=6`，索引区间逐段相等 |
| T-2 | 跨段事件被 purge | 段末 `horizon+embargo` 内的事件不出现在任何段 |
| T-3 | 合成序列可还原已知 edge | 人工植入「信号后 8 根必涨 50bps」的序列 ⇒ `winRate=1.0`、`meanRetBps≈50`、`usable=False`（`lossN < 5`） |
| T-4 | 无前视 | builder 对 `df` 的最后 `h` 根不产生事件；`asOfTs ≤ df["ts"].iloc[-1]` |
| T-5 | 时间衰减 | 同样本量下，把事件全部搬到更早的段 ⇒ `winRate` 向 0.5 侧收缩 |
| T-6 | **五条** `usable` 判据 | 逐条构造反例，`unusableReason` 取值正确（含 `runN < min_runs`） |
| T-7 | C-1~C-4 **与 C-6** 契约 | 总闸关 / 陈旧 / `horizonBars` 不等 / `costBasis="net@14bps"` / `strategyFingerprint` 失配 ⇒ 分别整键缺席或丢条目 |
| T-8 | regime 降级链 | 当前桶缺 ⇒ 落 `all`；`all` 也缺 ⇒ `None` |
| T-9 | 聚合数学 | 3 条 `p=0.55, ir=1` 的证据聚合出 `p=0.55`（**加权均值**，不是 0.65） |
| T-10 | `Σw == 0 ⇒ None` | 全部 `ir ≤ 0` 时不造 p |
| T-11 | **EV-1 单调保守（产出级）** | property test：随机 200 组 `(atr, tp, prior, cost, k_min)`，断言 `veto(prior_binding=True) ⊇ veto(prior_binding=False)`（**不是**只比 `target_bps`，见 §3.7 的反例） |
| T-12 | `source` 标注 | 取到最小值的项与 `source` 逐组一致；`prior` 为负时 `target` 保留负号 |
| T-13 | 门禁缺席回退 + ATR 不可用 | `prior_ev_bps=None` 时 `estimate_gross_edge_bps` 输出与合入前**逐字段相等**；`atr_px=None` 且 `prior_ev_bps=-50` 时仍返回 `None`（fail-open，§3.7 末的显式取舍） |
| T-19 | **读数与绑定解耦**（P1-2） | `prior_binding=False` 且 `prior_ev_bps=3.0` 时：`target_bps`/`source` 与 `prior_ev_bps=None` 逐字段相等，而 wire 的 `priorEvBps=3.0`、`priorWouldVeto` 按同一 `required` 正确置位 |
| T-20 | **分组按标的取快照**（P1-5） | 两成员分组（BTC/ETH）各给一份快照 ⇒ 各自会话拿到自己的那份；只给 BTC ⇒ ETH 成员 `prior_snapshot is None` 且不报错 |
| T-21 | **事件同源**（P0-2） | 合成序列里策略只在第 10 根出进场边沿、第 10~40 根持 `long`：builder 产出的 `sampleN==31`、`runN==1`、`event=="stance"`；`usable is False`（`runN < 10`） |
| T-22 | **coverage 口径**（P0-2 连带） | 全板 `flat` ⇒ `coverage is None`（不是 0.0）；两策略有立场、其一有先验 ⇒ `0.5` |
| T-14 | `compute_consensus` 零回归 | 既有断言逐字保留；`compute_weighted_consensus(board, None) is None` |
| T-15 | 会话零回归 | `AiTradeSession(..., prior_snapshot=None)` ⇒ payload 无 `signalPriors` 键、`build_decision_prompt` 输出与 golden 逐字节相等 |
| T-16 | 会话装配 | 给一份合法快照 ⇒ payload 键存在、prompt 含「历史统计，非当下保证」、`edge_gate` wire 带 `priorP` |
| T-17 | 端点守门 | 400/409/413/422 四类各一条；成功路径落库后 `get_app_setting` 可读回同一 JSON |
| T-18 | 前端 | `describeEdgeGate({edgeSource:"prior", …})` 标签含 `p=`；四条既有断言不变 |

### 8.2 验收门禁（不达标即阻断合并）

| 编号 | 判据 | 验证方式 |
|---|---|---|
| **G-1** | **零回归**：三总闸全关时，`services/quant` 全量 pytest 与 `apps/web` 全量 vitest 结果与合入前一致 | 全量跑 + 与基线对拍。**已知基线红灯**：`test_factor_library_expansion.py` 的 7 个孤儿测试 + 2 个 agent 计时 flake（单跑必绿）+ `WeightBatchArena.test.tsx` 的 1 个 pre-existing tsc 错误——这些**不是**本增量引入，不得据此阻断，但也不得增加新的红灯 |
| **G-2** | EV-1 单调保守（**产出级**）property test 通过 | T-11 |
| **G-10** | **读数无条件、绑定有条件**（P1-2）：总闸③关闭时 `target_bps`/`source` 逐字段回退，而 `priorEvBps`/`priorWouldVeto` 照发 | T-19 |
| **G-11** | **事件同源**（P0-2）：快照每条 `event` 与 `resolve_direction_prior` 的筛选分支一一对应，不存在「按 A 度量、按 B 取用」 | T-21 + 代码评审逐条对照 §3.3 表 |
| **G-3** | 无前视 | T-4 + builder 内 `assert i + h <= n - 1` |
| **G-4** | 持有期契约 | T-7（`horizonBars` 不等即整键缺席） |
| **G-5** | 双重计费不可能 | T-7（非 `gross` 条目被丢弃）+ builder 恒传 `cost_bps=0.0` 的断言 |
| **G-6** | 行数预算 | `git diff --numstat` 机械核对 §6.2 每行的净增上限；`session.py` **净增 ≤ 20**（v2 · P2-5） |
| **G-7** | 决策轮零 IO | T-15/T-16 用 `get_sessionmaker` 打桩（`session.py:23` 确有此 import，打桩路径成立），断言决策轮内零调用 |
| **G-8** | 前端镜像完备（**v2 · P0-1 重写**） | 按 §6.3 的**编辑点表**逐点核对，6 个编辑点全部完成；其中 `store.ts` 的两处（`:153-156` 初值 / `:209-217` 归约）是硬判据。**手工验收**：跑一次带先验的会话（或注入一条 mock `edge_gate` 事件），在界面上**真的看见** `p=` 读数——`grep` 通过不算通过 |
| **G-9** | 提交纪律 | **精确路径 `git add`**，严禁 `git add -A`；混合文件按 hunk 行级暂存（patch 文件须 **bytes 读写**，`write_text` 在 Windows 会把 LF 翻成 CRLF 弄坏 patch） |

### 8.3 运营指标（**观察项，不作为合并门禁**）

02 §五 的「先验覆盖率 ≥ 80%」「显著性达标率 ≥ 60%」在一个刚建库的系统上无法在合并时评估——
它们是 L1 观察窗口的产出。诚实起见列为观察项，达不到的处置是「拉长回看窗口或合并相邻 regime」，
而不是阻断合并。

### 8.4 已知在制特性（工作树纪律）

本工作树历来同时存在多个在制特性。合入本增量时**不得**顺手提交：`ai_trade_enabled` 总闸翻转相关的
`config.py` / `.env.example` / `desktop/src/lib/config.js` / `test_config.py` / `config.test.mjs` 的既有未提交 hunk、
`live_auto_repositories.py`、`docs/diagnoses/*`、`factor_library_expansion` 孤儿测试。
`config.py` 与 `.env.example` 是**混合文件**，必须按 hunk 行级暂存。

**本增量不需要动 `desktop/src/lib/config.js`**（v2 · P2-7）：它的 prod 环境透传是**前缀式**白名单
（`key.startsWith('AI_TRADE_')`，`config.js:72`），新增的 `AI_TRADE_PRIOR_*` 自动覆盖。而该文件正躺在别人的
未提交改动里（`ai_trade_enabled` 总闸翻转），顺手打开它极易把无关 hunk 卷进本次提交。

---

## 九、上线 runbook（四级观察窗口）与 M1 门禁

> **M1 门禁（不可跳过）**：[08 §3.1](../../improve/01-ai-autotrading/08-实施路线图与里程碑.md) 规定阶段一的三条
> 出口条件（费率入账完整性 = 100%、决策转化率 10%~35%、归因覆盖率 ≥ 95%，且样本量 ≥ 100 笔）全达标才进入阶段二。
> 截至本设计撰写时，[00 §七 里程碑表](../../improve/01-ai-autotrading/00-总纲与现状诊断.md) 记 M1 为「进行中，
> 四项均待观察窗口产出」。
>
> **本设计据此把「代码落地」与「机制上线」显式解耦**：三个总闸默认关闭 ⇒ 合并本增量**不改变任何运行时行为**，
> 因此合并本身不违反阶段门禁；而下方 L1 及之后的每一级，都以「M1 三条出口条件已达标」为硬前置。
> 实现节点可以合并，运维**不得**在 M1 未达标前推进到 L1。

| 级别 | 前置 | 动作 | 观察窗口 | 放行判据 | 回滚 |
|---|---|---|---|---|---|
| **L0** | — | 三闸全关合入 | 一次全量测试 | G-1~G-9 全绿 | — |
| **L1** | M1 三条达标 | 调一次 `POST /ai-trade/priors/rebuild`；开 `signal_prior_enabled` | 200 轮 | 快照装配率 ≥ 95%；动作分布相对 L0 的漂移 < 30%（先验不得喧宾夺主）；决策轮耗时增量 < 50ms（**读数来源见下方注②**） | 关总闸① |
| **L2** | L1 放行 | 开 `weighted_consensus_enabled` | 200 轮 | 加权净立场与等权净立场的分歧率 < 40%；分歧轮的事后胜率不低于同期整体 | 关总闸② |
| **L3** | L2 放行 **且 `k_min > 0`**（见注①） | 先读 L1/L2 记下的 `priorEvBps` 分布**定 `k_min`**，再开 `edge_prior_enabled` | 200 轮 | `vetoRate ∈ [20%, 70%]`；**反事实对拍**：开闸后的真实 veto 集合应与 L2 记录的 `priorWouldVeto` 基本一致（偏差 > 20% 说明有一侧算错了）；**零开仓监控**：连续 200 轮 `open_*` 为 0 即判定过度收紧 | 关总闸③；仍不脱困则下调 `k_min`（**注意注③：这不是纯回滚**） |

**注①（v2 · P1-2）：L3 的硬前置是 `ai_trade_edge_gate_k_min > 0`。** 该字段默认 **0.0**（`config.py:273`），
而 `evaluate_edge_gate` 首行即 `k_min <= 0 ⇒ return None`（`edge_gate.py:185-187`）——门禁整体空转，
总闸③开了也**什么都不会发生**，`vetoRate` 恒为 0 且一条 `edge_gate` 事件都不会发。若阶段一的 L 窗口尚未把
`k_min` 推上去，L3 的正确动作是**先推进阶段一的 `k_min`**（那是阶段一的验收范围），而不是在这里开③。

**注②：运维读数位置按 `k_min` 分档**（v2 · P1-3；两条通道互斥，别在错的那条里找）：

| `k_min` | 先验读数在哪 | 覆盖率在哪 |
|---|---|---|
| `> 0`（门禁生效） | `ws /ws/ai-trade/{sid}` 的 `agent_trace / phase="edge_gate"`：`priorP` / `priorEvBps` / `priorWouldVeto` | 同一条事件里的 `costSnapshot.priorCoverage` |
| `== 0`（门禁空转，默认） | `agent_trace / phase="round_start"` 的 `signalPriors`（每轮都有） | `status` 事件的 `costSnapshot.priorCoverage`（每 10 轮一次） |

会话停止后一律读 `GET /ai-trade/history/{group_id}` 的 `metricsJson.aiStats`（其中 `priorCoverage` 是
**最后一轮**的瞬时读数，不是全程平均）。决策轮耗时增量没有现成指标，L1 的 50ms 判据按
「同一标的、同一周期，开闸前后各 200 轮的 `round_start → result` 事件时间差中位数」人工对拍。

**注③（v2 · P1-1）：下调 `k_min` 不是「回到先验开启前」。** 阈值是全局的，调低它同时放松了**先验缺席轮**的
ATR 分支，相对当前生产行为更宽。要纯粹撤销本增量的收紧，唯一正确动作是**关总闸③**。

**每 7 天须重建一次快照**（C-2 陈旧度 7 天）；**改了 `ai_trade_cost_expected_hold_bars` 或任一策略参数后必须
立即重建**（否则 C-3 / C-6 会让先验整键或整条目静默缺席，只在告警里留一条痕迹）。

---

## 十、风险与缓解（Risks & mitigations）

**自我反驳一（必须写明）：本增量可能反而降低收益。** 先验是对过去的统计，而加密市场体制切换极快；
在体制刚翻转的窗口里，历史 IR 最高的策略恰恰是最容易失效的那个，加权聚合会把错误放大——这比等权更危险
（02 §六 自我反驳二）。**缓解**：30 天半衰期 + 按 regime 分桶 + 7 天陈旧度门禁；**监控信号**是 L2 的
「加权与等权净立场分歧率 > 40% 且分歧轮胜率低于同期整体」，触发即回退等权（关总闸②）。

**R-1 双重计费 ⇒ 永不开仓（最高危）**：若某条先验的 `costBasis` 是 `net@Nbps` 而 EV 不等式又按成本模型扣一次，
任何真实 edge 都会被判死。本仓库有过「反思致永不开单」的同类事故，代价是整整一个迭代。
**缓解**：builder 恒传 `cost_bps=0.0`（结构上产不出净额条目）+ 加载侧对非 `gross` 条目**直接丢弃**（C-4）+
两条断言（G-5）。**监控**：零开仓监控 + `vetoRate > 70%` 双触发。**回滚**：关总闸③（一步脱困，不必动 `k_min`）。

**R-2 先验只是在拟合噪声**：`sampleN=30` 对二分类是下限而非充裕（02 §六 自我反驳三）。立场根事件还额外
带来**样本重叠**（同一段趋势的相邻根不独立）。**缓解**：五条 `usable` 判据（`segments ≥ 6`、`tStat` 非空、
`runN ≥ 10`）；若某桶的 `winRate` 在各段间近似常数（`std_j(m_j) ≈ 0` ⇒ `ir = None`）则整条不可用
——**一个假装有信息的常数比缺席更危险**。

**R-3 影子回放与实盘立场漂移**：策略先验的价值全部建立在「离线回放出的立场 == 实盘每轮的立场」之上。
**缓解**：复用 `signals.evaluate_signal_board` 同一函数、同款 `make_shadow_broker`、同款 `build_backtest_frames`
帧装配；**任何在 builder 里另写一份回放的实现判为不合格**。**v2 补一条同类漂移**：回放路径没变、但**策略自己
被改了**（`params_json` / `template_key`）同样让先验描述另一个对象——由 C-6 策略指纹兜住（§3.5）。

**R-4 过度收紧（EV-1 的代价）**：本增量对开仓是单调收紧的，故失效模式是单向的「交易变少直至为零」。
且量纲上先验分子（期望，O(数 bps)）天然远小于 ATR 分子（幅度，O(数十 bps)），沿用对着 ATR 拍的 `k_min`
几乎必然把开仓全拦（§3.7 末）。**缓解**：L3 开闸**前**先按 L1/L2 记录的 `priorEvBps` 分布重定 `k_min`（P1-2
让这份分布第一次可读）；L3 的双判据（`vetoRate` 上界 70% + 零开仓监控）。
**回滚阶梯（顺序不可颠倒）**：① 关总闸③——**唯一的纯回滚**，一步回到本增量合入前的开仓条件；
② 仍需交易则下调 `k_min`——**这一步同时放松了先验缺席轮的 ATR 分支**，是「比阶段一更宽」而非「回到阶段一」，
必须作为一次显式的风险决策记录在案（§九 注③）。**严禁**给 prior 分支单独设一个更小的 `k`（§3.7 硬约束 1：
那会让本增量凭空造出新仓位，字面上却仍满足旧版 EV-1）。
**注意**：04 §五 的「错失交易率」指标（被拦下但 `horizonBars` 内方向正确的比例 < 60%）在本增量后才第一次
真正可算——`horizonBars` 现在有了唯一定义。

**R-5 快照陈旧 / 契约漂移静默**：改了 `ai_trade_cost_expected_hold_bars` 而忘了重建快照，会让先验层静默整键缺席；
**改了某个策略的参数**同样会让该策略的条目静默消失（C-6）。**缓解**：C-3 / C-6 契约比对 + 会话启动推
`AI_TRADE_PRIOR_CONTRACT_MISMATCH` 告警（附 `strategyId`，静默降级会被当成故障，这是本仓库反复付过学费的教训）。

**R-6 决策轮耗时 + 静默假阴**：新增 8 个信号检测器的末根求值。**缓解**：只对 `df.tail(N)` 求值、纯 pandas
向量化，预算 < 20ms；L1 的放行判据里显式包含「决策轮耗时增量 < 50ms」。**切窗口有一个静默陷阱**（v2 · P2-4）：
`detect_signal` 把前 `SignalParams.warmup = 30` 根强制判 `False`（`signal_edge.py:202-203`），切得太短会让
全部信号恒不触发且毫无提示，故 `N = _PRIOR_DETECT_MIN_BARS = 60` 为硬下限，不足即返回 `None`（本轮不知道）
而**不是**空集（本轮确定没有）。

**R-7 `session.py` 继续膨胀**：它已 1452 行。**缓解**：全部状态与编排下沉 `PriorRuntime`，会话净增 ≤ 14 行且
由 G-6 机械核对；实现节点若发现装不下，正确动作是把逻辑再下沉一层，**不是**放宽预算。

**R-8 混合工作树误提交**：见 §8.4 与 G-9。

---

## 十一、docs/improve 标记方案（逐字，实现节点照做）

「已经完成的开发需要在 `docs/improve/01-ai-autotrading` 中标记」是本轮任务的显式要求。
标记的**单一真源**是 [00 §七 实施状态看板](../../improve/01-ai-autotrading/00-总纲与现状诊断.md)，
词汇表冻结为：`未开始 / 设计完成 / 已实现(未提交) / 已合并 / 已验证`。各分册只放一行 banner 指针，
**绝不各记一份状态**（否则必然漂移）。

1. **`00-总纲与现状诊断.md` §七 状态表**：新增一行

   `| D-5 | 设计完成 | ai_auto/signal_prior.py + prior_builder.py + prior_runtime.py + signals.compute_weighted_consensus | — | 阶段二第一步；三总闸默认关；元标签（02 §3.3）与因子先验（03）未做 |`

   并把「D-3 ~ D-14（除上列）」那一行的说明同步为「除 D-5 外」。
   实现节点落地后把 `设计完成` 改为 `已实现(未提交)`；提交节点改为 `已合并` 并在下一轮回填短 hash。

2. **`00` §七「已知欠账」第三条**改写：把「阶段一的 edge 是 ATR 粗估而非概率化先验（D-5/D-7 未动）」改为
   「edge 分子已具备概率化先验路径（`ai_trade_edge_prior_enabled`，默认关）；D-7 置信度校准仍未动，
   故 M1 的判据仍只看结构性指标」。

3. **`00` §七 里程碑表**：`M2 · 阶段二` 的状态由 `未开始` 改为 `进行中（第一步：先验体系已落地，闸门默认关）`。

4. **`02-信号层重构与元标签.md`**：在标题下方插入一行 banner（镜像 `08` 第 5 行的既有格式）：

   `> **实施状态**：D-5 见 [00 · 七、实施状态看板](./00-总纲与现状诊断.md#七实施状态看板由实现节点维护单一真源)；设计文档 [`docs/plans/ai-trade-signal-prior-and-ev-gate/spec.md`](../../plans/ai-trade-signal-prior-and-ev-gate/spec.md)。`

5. **`08-实施路线图与里程碑.md` §3.2 阶段二表**：给表格补第四列「状态」（与 §3.1 的表格同构），
   前两行（信号先验 / 加权共识）填 `设计完成`，其余五行填 `未开始`；并在表下补一句：
   「阶段二按依赖拆成两步：**第一步**＝先验体系 + 加权共识 + EV 分子升级（本表前两行，设计见
   `docs/plans/ai-trade-signal-prior-and-ev-gate/spec.md`）；**第二步**＝因子快照 + 仓位重构 + 回撤刹车 + 闸门报告。」

---

## 十二、落地顺序（实现节点按此顺序推进，每步可独立跑测试）

1. `engine/walk_forward_purge.py` + T-1/T-2 —— 无依赖，先把切分口径钉死。
2. `engine/signal_edge.forward_return_at` 公开包装 + 既有 `test_engine_signal_edge.py` 跑绿（估计量单一真源）。
3. `ai_auto/signal_prior.py`（纯 Python）+ T-7~T-10 —— 用裸 dict 构造样本，不需要任何 K 线。
4. `ai_auto/prior_builder.py` + T-3~T-6 —— 合成序列驱动，不触网络。
5. `db/ai_prior_repositories.py` + `schemas.py` + `api/ai_auto.py` 两个端点 + T-17。
6. `ai_auto/edge_gate.py` 的 `prior_ev_bps` + `prior_binding` 形参 + T-11~T-13 / T-19 ——
   **产出级 EV-1（G-2）与读数/绑定解耦（G-10）在这一步就必须绿**。
7. `ai_auto/signals.compute_weighted_consensus` + T-14。
8. `ai_auto/prior_runtime.py` + `cost_runtime.py` 透传 + `session.py` / `manager.py` 薄封装 + T-15/T-16。
9. `prompts.py` 渲染段 + `trace.py` 两个加法键（prompt golden 断言在这一步复核）。
10. 前端 4 文件 + T-18 + §6.3 **编辑点表**逐点对账（`store.ts` 两处是硬判据）+ G-8 的手工肉眼验收。
11. `config.py` / `.env.example` 的 13 个字段（**放在最后**：它们是混合文件，越晚改越少的 rebase 冲突）。
12. 全量后端 + 前端测试，与 §8.2 的已知基线红灯对拍；`git diff --numstat` 核对行数预算；按精确路径暂存提交。
13. 按 §11 更新 `docs/improve` 三个文件的状态标记。

---

## 十三、三个拍板项的评审裁决（v2 · 已拍板，实现节点照此执行）

1. **O-1 `plan_purged_windows`：移出本轮**（**否决**设计建议）。理由：本增量没有任何消费方，`CLAUDE.md` §四
   明令删除僵尸代码；且 06 号闸 1 的窗口形状（步长 / 是否 anchored / 要不要按体制分层）现在还不知道，
   先写一版没人用的实现，到时大概率推翻重写，还得先说服自己「既有单测是不是在守一个错的形状」。
   `plan_segments` + `purge_crossing_events` 本轮真的被消费，**保留**。签名已从 §4.3 删除。
2. **O-2 前端「重建先验」按钮：不做**（**采纳**设计建议）。三闸默认关，L0/L1 的读者是运维；做了按钮反而
   诱导用户在未验证前打开。
3. **总闸③保留独立**（**采纳**设计建议，且经 P1-2 修正后它的价值比设计时更高）。原设计里③只是「展示 vs 门禁」
   的开关；v2 把「读数」与「绑定」在 `edge_gate` 层拆开之后，③关闭的 L1/L2 窗口能持续产出
   `priorWouldVeto` 反事实读数，L3 才有据可依地定 `k_min`。合并成一个闸不仅让 L1 观察不可归因，
   还会让 L3 变成一次闭眼开闸。

---

## 评审结论（Review Verdict）

### **有条件通过**（v2）

本设计的骨架是对的，且几处最容易做错的地方在 v1 里就已经做对了：分层与行数纪律沿用了阶段一确立的
「纯核心 → 编排 → 运行时 facade → 会话薄封装」；零新表、零迁移地复用 `app_settings`；离线/在线严格分离，
决策轮零 IO；三个默认关闭的独立总闸让「合并」与「上线」解耦，因此**合并本身不违反 M1 阶段门禁**；
`costBasis` 恒 `gross` + 加载侧 fail-closed 丢弃，把本仓库最贵的那类事故（双重计费致永不开单）从结构上排除；
对 02 号文档的三处偏离（不用 `run_backtest` 成交盈亏、回测侧不接先验、logit 改加权均值）都给了站得住的论证。
规模也合适：5 个新模块 ≈ 700 行预算、11 处后端修改点，没有为「将来可能要用」而超前建设（唯一的一处 O-1
已在本次评审裁掉）。

评审共提 **15 条**：**2 P0 / 6 P1 / 7 P2**，全部已在正文改掉（改动点见 §评审记录 的「处置」列）。
两条 P0 都不是笔误级问题，而是「照着写就会出事、且测试全绿」的那类：
**P0-1** 前端镜像点普查与真实代码不符、漏掉唯一会静默吞字段的 `store.ts`，且 G-8 门禁在真实代码上天然满足；
**P0-2** 策略先验「按进场边沿度量、按持久立场取用」，让要去当真实资金门禁分子的 `p / W / L` 失去定义。
六条 P1 集中在两类：安全性质的**陈述精度**（EV-1 立错层级、`k_min` 阶梯被误称回滚）与**可观测性落点**
（默认 `k_min = 0` 下 L1/L2/L3 的读数通道互斥、反事实读不到、覆盖率挂错通道），另加两条会串标的 / 串策略的
数据契约缺口（分组按 `instId` 取快照、策略指纹 C-6）。

### 放行条件（实现节点必须逐条落实，任一不满足即不得合并）

1. **G-8 按编辑点表逐点核对**，`store.ts` 的两处（`:153-156` 初值、`:209-217` 归约）必须真的改到；
   并完成一次**肉眼验收**：界面上真的看见 `p=` 读数。`grep` 通过不算通过。
2. **G-11 事件同源**：快照每条 `event` 与 `resolve_direction_prior` 的筛选分支一一对应；
   代码评审时逐条对照 §3.3 表，不接受「实现时顺手加回 `latest_signal` 分支」。
3. **G-2 按产出级断言**（veto 集合包含关系），不是只比 `target_bps`；且全仓 `grep` 确认不存在任何
   prior 专属的 `k` / `required` 分支。
4. **G-10 读数与绑定解耦**：总闸③关闭时 `target_bps` / `source` 逐字段回退，`priorEvBps` / `priorWouldVeto` 照发。
5. **C-6 策略指纹**落地并在 `_resolve_prior_snapshots` 处比对（该层已持有策略行，不得为此新增查询）。
6. **`prior_snapshots` 按 `instId` 映射**，`_build_member` 按成员 `inst_id` 取；T-20 守门。
7. **G-1 零回归**：三闸全关时后端 pytest / 前端 vitest 与基线一致；已知基线红灯（`test_factor_library_expansion.py`
   的 7 个孤儿测试、2 个 agent 计时 flake、`WeightBatchArena.test.tsx` 的 1 个 pre-existing tsc 错误）不得据此阻断，
   但也**不得新增红灯**。
8. **G-6 行数预算**：`session.py` 净增 ≤ 20；超了的正确动作是把逻辑再下沉 `PriorRuntime`，不是再放宽预算。
9. **G-9 提交纪律**：精确路径 `git add`，`config.py` / `.env.example` 按 hunk 行级暂存（patch 文件 **bytes 读写**），
   **不动 `desktop/src/lib/config.js`**（前缀白名单已覆盖新 env）。
10. **上线纪律写进交接**：L3 的硬前置是 `k_min > 0`；`k_min` 只能按 §九 注③ 的口径下调并记录在案；
    每 7 天重建快照，改持有期或改策略参数后立即重建。

### 留待运行期证伪（不阻断合并，但必须有人盯）

- 先验的**真实增益**：本增量只能让交易变少。它是否有用，唯一判据是 L3 窗口里「被新拦下的轮次事后方向是否
  真的错」——若拦截集合的事后胜率与放行集合无差异，说明先验没有信息，应关总闸③而不是继续调参。
- **立场根口径的样本重叠**：`runN ≥ 10` 是一个拍出来的下限。若 L1 观察到覆盖率长期偏低，正确处置是拉长回看
  窗口（`lookbackBars`）或合并相邻 regime 桶，**不是**下调 `runN`。
- **量纲落差**：先验分子与 ATR 分子差一个数量级，`k_min` 需要按 EV 分布重定；这件事在 L3 之前没有先验答案，
  设计层面只能保证它**可读**（P1-2），读完怎么定是运维决策。

---

## 实施过程发现的方案缺陷

> 本节由实现节点追加（Subtask #2）。每条给出「设计怎么写 → 照做会怎样 → 实际怎么做」，
> 供评审节点逐条裁决。**未在此列出的部分一律按 v2 正文逐字落地。**

### I-1（阻断级）· `build_prior_snapshot` 缺 `min_segments` 形参 ⇒ `ai_trade_prior_min_segments` 是死配置

§4.3 的 builder 签名列了 8 个 keyword 形参，**没有 `min_segments`**；而 §七 的配置表又声明了
`ai_trade_prior_min_segments`（默认 6，「调低即放松显著性」）。照签名落地的结果是该字段被声明、被
`.env.example` 记录、却**没有任何消费方**——运维调它不会有任何效果，是比缺席更糟的一种静默。
**处置**：签名末尾追加 `min_segments: int = MIN_IC_SEGMENTS`（带默认值，故 §4.3 列出的调用形状不变），
由 `prior_service` 从 settings 透传。

### I-2（阻断级）· `std > 0 ⇒ ir 可用` 在浮点上识别不出「各段均值近似常数」

§3.4 与 R-2 都要求「若某桶的 `winRate` 在各段间近似常数（`std_j(m_j) ≈ 0` ⇒ `ir = None`）则整条不可用」。
但 `std > 0.0` 这个字面判据在一条严格等比的价格序列上会拿到 `std ≈ 1e-13` ⇒ `ir ≈ 2e13`，夹到
`ir_clip` 之后与一条**真有 edge** 的证据完全无法区分——恰好制造出 R-2 说的「一个假装有信息的常数」。
实测由 `test_constant_segment_means_yield_no_ir_and_no_t_stat` 复现。
**处置**：判据改为相对容差 `std <= max(|mean|, 1.0) × 1e-9`（`prior_builder._STD_REL_EPS`），
即按 §3.4 的「≈ 0」而不是「> 0」实现。

### I-3 · 新增 `ai_auto/prior_service.py`（不在 §6.1 新建清单里）

§6.2 给 `api/ai_auto.py` 的净增预算是 **+58**，而「两个端点的守门 + `_resolve_prior_snapshots` +
`_build_specs` 的指纹改动」按本仓库的注释密度已经吃满该预算；取数、HTF 装帧、`run_in_threadpool`
卸载再放进路由层，净增会到 **+156**，且把路由写成了 service。§8.2 G-6 明写「超了就再下沉一层而不是
再放宽」，故按该指令下沉：新增 `ai_auto/prior_service.py`（约 95 行，镜像 `factors/service.py` 与
`ai_auto/backtest.py` 的既有分层），路由层回落到 **+97**。这是「新增未列出文件」与「预算超 3 倍」
两个偏离之间的取舍，按 G-6 的字面指令选了前者。

### I-4 · `priorP` / `priorContributors` 的产出方是 `CostRuntime` 而不是 `edge_gate`

§3.10 要求 `evaluate_edge_gate` 的 wire 带**五个**先验键，但 §4.3 给它的新形参只有
`prior_ev_bps: float | None` 与 `prior_binding: bool`——纯核心根本拿不到 `p` 与 contributors，
照写会得到两个恒 `None` 的键。**处置**：`edge_gate` 产出它算得出的三键（`priorEvBps` /
`priorWouldTargetBps` / `priorWouldVeto`），`CostRuntime.evaluate` 从 `set_prior_view` 回灌的视图里
按**本轮动作方向**补上 `priorP` / `priorContributors`。§4.3 已把 `CostRuntime` 注为「先验读数的唯一
入口」，此处与该定位一致；代价是 `CostRuntime` 对 `EdgeGateOutcome.wire` 做了一次原地富化。

### I-5 · 策略指纹以**内存态键**注入快照，而非 `PriorRuntime` 形参

C-6 要求指纹在 `_resolve_prior_snapshots` 处比对（那一层已持有策略行），而 §4.3 的
`PriorRuntime.__init__(raw_snapshot, settings)` 不接指纹表、`load_snapshot` 又需要它。若在
API 层直接剪枝，`AI_TRADE_PRIOR_CONTRACT_MISMATCH` 告警就没有载体（告警走 `PriorRuntime.alerts`）。
**处置**：`_resolve_prior_snapshots` 把 `{strategyId: 指纹}` 作为 `fingerprints` 键并入**内存态**快照
dict（**不落库**，`save_prior_snapshot` 写的是 builder 原产物），`PriorRuntime._load` 读它并透传给
`load_snapshot`。契约判定与告警仍只有一处。

### I-6 · `DirectionPrior` 增 `contributor_n` / `strategy_ids` 两字段

§3.6 第 6 条的字段列表里 `contributors` 已按 `top_n` 截断，但 §3.8 的覆盖率分子要的是**未截断**的
贡献策略集，prompt 证据行要的是**未截断**的条数。用截断后的列表算覆盖率会让 `top_n` 悄悄改变一个
运维指标。**处置**：新增两个只读派生字段，`contributors` 的截断语义不变。

### I-7 · 行数预算多处超出（G-6 机械核对结果如实记录）

`git diff --numstat` 实测净增（预算 → 实际）：`session.py` **+16 ≤ +20 ✅（硬上限，放行条件 8）**、
`manager.py` +6 ≤ +14 ✅、`config.py` +23 ≤ +30 ✅、`edge_gate.py` +39 ≤ +44 ✅、
`trace.py` +7（预算 +6）、`signal_edge.py` +11（+8）、`prompts.py` +47（+24）、
`signals.py` +98（+72）、`cost_runtime.py` +37（+14）、`schemas.py` +60（+34）、
`api/ai_auto.py` +97（+58，已按 I-3 下沉一次）。超出部分全部是 docstring 与「为什么」注释
（`CLAUDE.md` §四要求公开 API 必须有 docstring），无一处是逻辑膨胀；受影响文件的**绝对行数**
全部远离 800 行红线（最大的 `prompts.py` 为 703 行）。§6.2 的预算是按纯逻辑行估的，
与本仓库的注释密度不匹配——这与 v2 评审在 P2-5 给 `session.py` 放宽预算时的判断是同一件事。

### I-8 · `_active_signals` 的求值窗口取 240 根，硬下限仍是 60

§R-6 / P2-4 定死 `_PRIOR_DETECT_MIN_BARS = 60`（不足即返回 `None`），这一条逐字落地。但把求值窗口
**也**取 60 会引入另一种静默偏差：EMA 是递归量，60 根窗口上的 `ema_slow_p=26` 与离线全区间 EMA 的
差异足以翻转 `ema_golden_cross` 的末根判定，于是在线「本根触发了什么」与离线事件定义悄悄分叉
（正是 §3.2 推论 5 要防的那类事）。**处置**：硬下限保持 60，实际求值窗口取 `df.tail(240)`
（`_PRIOR_DETECT_BARS`），耗时仍在个位数毫秒，远在 R-6 的 20ms 预算内。

### I-9 · T-21 的 `usable` 首个不满足项是 `segments<6` 而非 `run_n<10`

§8.1 T-21 的构造是「第 10~40 根持 `long`」——31 个事件**全部落在第 0 段**，故 `_usability` 按 §3.4
的判据顺序（样本数 → 有效段数 → tStat → 亏损样本 → runN）在第二条就返回了。
**处置**：T-21 保留 `sampleN==31 / runN==1 / event=="stance" / usable is False` 四条断言（P0-2 要守的
正是前三条），`run_n<10` 分支由独立用例
`test_run_floor_is_the_binding_criterion_for_overlapping_stances`（6 段各 6 根）证伪，并另加一条
`test_run_floor_can_be_satisfied_and_entry_becomes_usable` 证明该判据不是恒假的。

### I-10 · `_build_specs` 的返回类型改为 `(specs, fingerprints)`

C-6 要求「不得为指纹新增查询」，而 `api/ai_auto._build_specs` 是唯一已经逐个取过策略行的地方。
**处置**：其返回值由 `list` 改为二元组，两处调用点同步更新（回测侧显式丢弃指纹并注明「回测侧不接
先验（D-2 前视偏差）」）。仓内无其它调用方，无测试依赖旧签名。

### I-11 · 前端 `describeEdgeGate` 的入参 `Pick<…>` 需扩两个键

§6.3 只写了「`Pick<…>` 扩到 `edgeSource`/`priorP`」，落地时确认这两个键必须**同时**加进
`AiTradeRoundTrace`（§6.3 第二行）与 `Pick` 列表，否则 `tsc` 报错而非静默——这一条按设计落地无偏离，
记录在此仅为说明 G-8 编辑点表的 6 个点已逐点核对完成（含 `store.ts` 的两处硬判据）。
