# AI 交易经验上下文图（ai-trade-experience-context-graph）· 方案设计 **v2**

> 节点 #0（方案设计）产物，经节点 #1（设计评审）修订。本文档只产出设计，不含实现代码。
> 下游节点（#2 开发 / #3 终审）以本文件为唯一实施依据。
>
> **v1 → v2 变更**：评审提出 3 条 P0、11 条 P1、9 条 P2；P0/P1 已逐条改进文档正文（改动处以
> 「〔评审 P0-n / P1-n〕」标注），P2 一并记录于 §评审记录。评审结论见文末 §评审结论。
> 阅读顺序建议：先读 §评审记录（知道 v1 错在哪），再读正文。

---

## 评审记录（节点 #1 · 设计评审）

> 评审口径：**只采信本轮实读的源码**，不采信 v1 正文的取证表（该表约九成行经复核为真，但下面
> 逐条列出的几处不真，恰好落在设计的承重位置上）。每条给出取证位置与判定理由。
> P0 = 会让特性「装配了却不工作」，或损坏用户数据 / 真实资金操作路径；P1 = 会让实现走偏，
> 或让文档里一条会被下游当作事实引用的陈述是错的；P2 = 表述 / 边界 / 打磨，不阻断实施。
> **P0 与 P1 已全部在正文改掉**（改动处标注「〔评审 P0-n〕/〔评审 P1-n〕」）；P2 记录在案。

### P0（3 条，阻断级）

| # | 结论 | 取证与理由 |
|---|---|---|
| **P0-1** | **会话末 LLM 蒸馏挂在 `stop()` 的 await 链上，会让「停止」按钮必定超时**；且 v1 所称的「既有 best-effort 收尾块」不存在 | 实读 `session.stop()`（`session.py:541-579`）：`_finalize_run(ts)` 之后只剩 `_notify_stopped()` 与一次 `_push`，**没有**任何 best-effort try 块可挂。更致命的是量纲：`manager.stop_group`（`manager.py:745-757`）**串行**遍历成员逐个 `await self._stop_session(...)`；而前端 `/ai-trade/{groupId}/stop` 命中 `WRITE_ACTION_FAMILIES` + `/stop`，超时档是 `FAST_MS = 20_000`（`lib/httpTimeout.ts`）。一个 3 标的分组 × 45s 蒸馏 ≥ 135 秒，客户端 20 秒就放弃并弹出**「本次操作结果未知，请刷新持仓/会话状态核对」**——那正是本仓库写死「最不能犯」的一类误报（`timeoutMessage` 的注释直接点名提交 `7b55d16`）。**在真实资金的停止/平仓按钮上制造一次假警报，代价远大于这个特性的全部收益。** |
| **P0-2** | **§4.5 的「全量替换 `stat` 子图」会在每次物化 / 重建时静默抹掉用户意志，并使 `iron` 档在结构上永不可达** | 两个后果都是静默的。① §5 的 `DELETE /nodes/{id}` 对 `stat` 节点回 409 并**明确让用户「改用静音」**——用户照做，下一次物化把 `stat` 子图删掉重建，`muted` / `mute_reason` / `pinned` / `first_ts` / `created_ts` 一起消失，那条被静音的经验**原样复活**并重新进 prompt。这是「产品告诉用户的做法，产品自己不兑现」。② §4.7 的 `iron` 门槛第三条是「最近两次物化中效应符号一致」，而全量替换之后**没有任何地方存得下上一次的符号**——每次物化都看不到历史 ⇒ `iron` 永远晋升不了 ⇒ L0 恒空。整个特性会「装配成功、日志全绿、一个字都不喂」。 |
| **P0-3** | **§4.3 的「只读 `explain.meta`、绝不再算一遍」拿不到它自己要求的字段** | 实读 `_WIRE_FIELDS`（`trip_ledger.py:239-249`）：`trip_meta_wire` 只发 17 个 camelCase 键，**不含** `fee_known`、`side`、`entry_ts`、`exit_ts`、`gross_pnl_usd` / `net_pnl_usd`、`edge_error_bps`、`cost_error_bps`。而 I-7 的过滤依据就是 `fee_known`、§6.1 的列里有 `side` / `entry_ts`、§4.4 的统计量里有 `meanEdgeErrorBps`、§4.4 的六模板里整整一个是「估计偏差型」。按 v1 字面实现的结果：I-7 无从判定（那道口径闸空转，R-5 要防的失真原样发生）、`entry_ts` 只能瞎填、「估计偏差型」模板永不触发。三者**都不会报错**。 |

### P1（11 条，须在文档定稿前改正）

| # | 结论 | 取证与理由 |
|---|---|---|
| **P1-1** | §4.2 的 `wake` 轴取值是臆造的 | 真值是 `WAKE_KINDS = ("candle_close", "bar_advanced", "signal_flip", "resting_order", "profit_tier", "price_breakout", "position_watch", "heartbeat")`（`wake_reason.py:22-24`，八值）。v1 写的 `candle / interval / signal / catchup / backlog` 一个都不在里面（`candle` / `interval` 是 `payload["trigger"]` 的二值，不是 `wake_kind`）。I-5 声明语境是取自仓库既有枚举的闭合词表——词表本身写错，就是 I-5 的第一次违反。 |
| **P1-2** | 检索种子只有 **7** 轴，不是 9 轴 | `ContextSeed` 在 `_build_decision_payload` 时刻装配，而 `side`（本轮要开多还是开空）与 `confidence`（模型置信度）此刻**都还不存在**——两者要到 `decide()` 返回之后才有。故决策轮可用的种子只有 `symbol / bar / regime / volatility / event / wake / horizon`。这直接决定 §4.4 的三阶桶（四组里三组由 `side` 键控）在 L0/L1 上能拿到多少 `match`。 |
| **P1-3** | `inject` 的调用位置有硬顺序约束，v1 未写 | §4.8 的档位判定读 `payload["contextDisclosure"]`，而那个键由 `self._wake.inject(...)` 写入，**位于 `_build_decision_payload` 的最后一段**（`session.py:2003-2009` 实读）。经验层 inject 若排在它之前，`context_disclosure` 恒缺席 ⇒ 档位恒 `full` ⇒ §4.8 的整套压缩静默失效——而那正是本需求点名要解决的「上下文爆炸」。 |
| **P1-4** | §4.8 的留存窗口乘数算错约 4 倍 | `ai_trade_max_context_messages` 默认 40（`config.py:160`）计的是**消息**不是**轮次**，而 `commit_round` 每轮 append **三条**（user prompt / assistant tool_use / tool_result，`decider.py:546-551`）。更关键的是 `summarize()` 会**整体重置** `_context`（`decider.py:624`），而 `context_refresh_rounds` 默认 10（`schemas.py:357`）⇒ 稳态下 40 这顶帽子**几乎从不生效**，真实驻留约 10 轮。故「420 × 40 ≈ 16.8 KB」应为「420 × 10 ≈ 4.2 KB」。设计结论不变，但一个会被下游逐字引用的数字错 4 倍，必须改。 |
| **P1-5** | I-1 的「爆炸半径为零」不成立 | §4.1 明写把「与提议动作**最相冲突**的 1 条」注入 `build_reflection_prompt`，而反思官的 `abort` 会**真的否掉一笔交易**。此外 `build_reflection_prompt` 的第一行就是 `evidence = build_decision_prompt(payload)`（`prompts.py:761`）——L0/L1 本来也会原样出现在复核输入里。所以经验层**能**改变「这一单下不下」。真正成立的安全论证是**方向单侧**（反思只会 confirm / abort / 缩仓，不会放大或改向），而不是「零影响」。把一条不成立的强断言写进不变式，会让下游把它当免检牌。 |
| **P1-6** | L0 的字符预算与框架句没有对齐，且零条目时会渲染出一个空壳 | 按 §4.8 的渲染样例实数：页眉 ≈33 字 + 页脚 ≈37 字 + 3 条 ×（120 字上限 + 序号 3 字 + 换行）≈372 字 = **约 445 字 > 420**。即默认预算下 3 条满长铁律本就装不下。更糟的是 v1 没规定框架句算不算预算：不算则产出会超 `max_chars`（I-6 破）；算且先拼框架，则可能产出「只有页眉页脚、一条经验都没有」的空壳。I-6 那句「取第一条就超预算在结构上不可能」也只对第一条成立，没把框架开销算进去。 |
| **P1-7** | `last_seen_ts` 的语义会让时间衰减永不生效 | §4.6 的 `recency(l) = 0.5 ** ((now_wall − last_seen_wall)/(halflife·86400))`，而 §6.2 把 `last_seen_ts` 列为节点字段却没说它取什么。若它随物化刷新（最自然的写法），每次重建都把时钟拨回 now ⇒ `recency ≡ 1` ⇒ 三年前的教训与昨天的同权。它必须是「该经验**最近一条支撑事件**的墙钟」（即 `stats.lastTs`），物化时刻另由 `updated_ts` 承载。§4.7 的 `stale` 判据用的正是 `lastTs`——两处同义不同名，实现期一定会被合成一个字段。 |
| **P1-8** | 离线平仓与续跑后的持仓不产生经验事件，与「不丢事件（丢事件会造成幸存者偏差）」自相矛盾 | 两条真实路径：① `resume.settle_closed_offline`（`resume.py:302-334`）把暂停期间被交易所平掉的那一笔直接 `repo.append_live_trade(...)` 落库，**完全不经 `session._dispatch`** ⇒ 无 `record_close`、无 `apply_trip_meta`、无 `note_close`。② 续跑接管既有持仓走 `adopt_position(...)`（`resume.py:294`），**不调** `_cost.note_open` ⇒ `attribution._open` 为 None ⇒ 该持仓最终平仓时 `settle_close` 直接返回 `{}`，连 `SettledTrip` 都不存在。这两条**恰好偏向亏损样本**（被强平的、长期持有跨越暂停的），是教科书式的幸存者偏差。而 v1 §4.11 写的是「续跑…无需任何额外代码」。 |
| **P1-9** | `costRegime` 用「全库三分位 + 写入时冻结」，会让这条坐标轴跨时不可比 | §4.2 说分位点「由全库事件周期性重算并落 `app_settings`」，§4.3 说 `costRegime` 在 `note_close` 时补进事件语境签名。两者合起来 = 同一个 `realized_cost_bps` 在不同时间写入会得到不同标签，而标签一旦冻结就不再更新 ⇒ 半年后的 `costRegime:low` 与今天的不是一回事，桶聚合却把它们当同一格。缓慢失真且不可察觉。 |
| **P1-10** | 「检索预演 = 所见即 Agent 所得」在**运行中的会话**上是假的 | §4.1 明写图在会话启动时一次性载入内存（I-3），而 `POST /api/experience/preview` 读的是**当前 DB 里的图**。会话跑起来之后，两者因本会话增量物化与其它成员会话的写入而必然分叉。A-25 的「逐值相等」只在同一份图快照下成立。UI 上那句「逐字看到 Agent 下一轮会被喂什么」在最需要它的时刻（会话正在跑）是错的——而这一屏的全部说服力都押在这句话的可信度上。 |
| **P1-11** | 前端与数据层的超阈文件没有净增预算，R-11 只守了后端 | 实测行数：`lib/aiTradeTypes.ts` **1787**、`components/Agent/AiTradeLaunchModal.tsx` **1578**、`lib/aiTrade.ts` **1412**、`db/repositories.py` **1029**——四个都已超 CLAUDE.md 的 800 行阈值，而 §8.3 对它们只字未提，还计划往 `AiTradeLaunchModal.tsx` 加约 35 行、往另两个各加一批类型与常量。§8.2 对后端四个超阈文件做到了逐行预算，前端不做就是双标，且节点 #3 终审会按同一把尺子量。 |

### P2（9 条，记录在案，不阻断实施）

| # | 结论 | 处置 |
|---|---|---|
| P2-1 | §4.2 写 `HorizonRule.profile`，实际属性名是 `profile_id`（`hold_horizon.py:151`），`profile` 只是它的 wire 键（`:404`） | 正文已改。 |
| P2-2 | §8.2 断言 `ai_auto/decider.py` **无改动**，但决策器的 LLM 客户端是私有 `self._llm`（`decider.py:178`），「从外部持引用」拿不到 | 正文已把该行改为 +2（一个只读属性）。 |
| P2-3 | §6.1 称 `origin` 是「真实读点，非预留列」——但 v1 明确不做回测侧写入（§十一.2），故该列在 v1 恒为 `"live"`，`WHERE origin='live'` 是恒真过滤 | 保留该列**是对的**（`create_all` 不补列，事后加列要写 ALTER 补丁），但理由应写成「为 v2 预留的判别列，代价为零、收益是免掉一次 ALTER」，而不是谎称它已有第二取值。正文已改。 |
| P2-4 | §6.5 标题写「20 项」、正文列 25 项、§8.2 写「+25 个」 | 正文已统一为 27 项（含本轮新增 2 项）。 |
| P2-5 | §4.6 步骤 5 的 `norm(score(l))` 在「全部候选 score 均为 0」（种子标的在库里一条经验都没有、只匹配到别的标的）时除零 | 正文已加：`score <= 0` 的候选进 MMR 前一律剔除；剔空即整层不渲染。 |
| P2-6 | §4.6 步骤 4 的「`match` 差异 > 0.25」是绝对阈值，而 `match = Σ A(c)·w_applies` 无量纲上界（随激活语境数增长） | 正文已改为相对判据。 |
| P2-7 | `GET /api/experience/export` 一次性返回全部 nodes + edges + **events**；跑了半年的库是几万条事件的单个 JSON，桌面端内存里拼一份、再序列化一份 | 正文已加：分页 / 流式（`StreamingResponse` NDJSON）+ `since` / `instId` 过滤，UI 明示体量。 |
| P2-8 | A-20「坐标逐值相等」隐含一个前提：布局的输入顺序必须稳定 | 正文已加：布局前按 `node.key` 全序排序，边同理。 |
| P2-9 | `costRegime` 三分位跨标的合并计算，会把 BTC 与 PEPE 的成本量纲混进同一格 | 与 P1-9 一并处置：分位点按 `(inst_id, bar)` 分组算，样本不足回落全库分位并在 UI 标注。 |

### 评审同时确认为「真」的关键取证（下游不必重复核实）

以下 v1 陈述经本轮逐条实读**属实**，可直接依赖：`create_all` 建新表但不补列（`db/session.py:90-98` 的四个 `_ensure_*_columns`）；`AttributionLedger._CAPACITY = 200`（`attribution.py:29`）；`EXIT_SOURCES` 九值（`exit_attribution.py:23-26`）；`market_context.py` 的 regime / volatility / event 三组标签；`build_start_request` 是 `async` 且已持有 `AsyncSession`，`resolve_track_record` 就是「启动期一次性取数」的现成先例（`api/ai_auto.py:322-364`）；`start_wire=body.model_dump(mode="json")` 使续跑自动带上新键（`:371`）；设置中心确为「`SettingsNav.SECTIONS` + `SettingsPanel` 分支」两处登记；`OriginGuardMiddleware` 是 ASGI 中间件、新路由自动受保护（`main.py:387`）；`run_in_threadpool` 有现成先例（`agent/services.py`）；`--warn` 令牌已存在（`globals.css:48`）；`ui.Tabs` 已有 `role="tab"` 与方向键 roving（`ui/index.tsx:432-460`）；`trace.py` 的 `round_start` 确是显式取键装配，且 `AiTradeRoundTraceOut` 只是文档镜像不参与序列化（`schemas.py:421-426`，故新键不会被 `response_model` 吞掉）；`store.patchAiTradeMemberStatus` 用的是 `{...m.session, ...status}` 展开而非逐键手抄（`store.ts:963-970`，故 `status.experience` 能安全流到前端）；四个后端超阈文件行数（`session.py` 2409 / `manager.py` 1774 / `prompts.py` 846 / `api/ai_auto.py` 803）。

---

## 一、概览（Overview）

【AI 自动交易】今天**每一轮都在产生经验，每一次停止都把它全部丢掉**。取证如下：决策器
`AiTradeDecider` 有一份滚动摘要 `_summary` 与一条决策日志 `_journal`（`ai_auto/decider.py:509`
`commit_round`），它们是纯内存对象；会话终止时 `_build_run_metrics()` 只把最后一段摘要文本塞进
`metrics_json.finalSummary`（`ai_auto/session.py:2272`），此后再没有任何读点——下一次启动的
`AiTradeSession` 拿到的是一个空 `_journal` 与 `summary=None` 的全新决策器。同样，
`AttributionLedger` 每笔平仓都会结算出一份信息极其丰富的 `SettledTrip`（净额 bps、实现成本
bps、R 倍数、计划盈亏比、止损 ATR 倍数、退出来源、估计 edge 与实测 edge 的误差、决策置信度、
唤醒来源；`ai_auto/trip_ledger.py:94`），但它住在一个容量 200 的内存环形缓冲里，随进程消亡。
**换句话说：这个系统每天都在用真金白银买教训，然后在关掉窗口的那一刻把收据烧了。**

本特性把「每一次 AI 自动交易产生的经验与教训」变成一份**可累积、可检索、可视化、可携带**的
数字资产，并把它以**受控字符预算**回喂给下一次决策。核心结构是一张**上下文图**：图里有两类
节点——**语境节点**（`symbol:BTC-USDT-SWAP`、`regime:trend_up`、`volatility:high_volatility`、
`exit:profitGuard` 这类闭合词表里的坐标轴取值）与**经验节点**（一条被证据支撑的教训），以及四
类有向加权边（`applies_in` / `co_occurs` / `contradicts` / `refines`）。取经验不是 SQL 过滤，
而是一次**有界的种子激活扩散 + 加权打分 + 反例压制 + MMR 去冗**：把「本轮的语境」当作种子集合
点亮图上的若干语境节点，沿 `co_occurs` 边做一跳软扩散（于是「震荡+高波动」学到的教训能在
「震荡+常波动」下以衰减权重浮现，而不是一无所获），再按 `applies_in` 边权 × 证据强度 × 时间衰减
打分。这既满足「高效快捷精准」，也天然给出**渐进式披露**的分层：常驻纲要（L0）只放最强的 3 条、
按语境变化触发的展开（L1）只在图判定语境真的移动了才发、完整证据（L2）只在下单前二次反思与设置
中心的可视化面板里展开。三层各有**硬字符上限**，且「本轮检索结果与上轮完全相同」时整段压成一行
指路——因为决策器的 `_context` 会把**每一轮渲染好的 prompt 原文**留在窗口里（每轮三条消息，
`ai_trade_max_context_messages` 默认 40 条，而 `summarize()` 每 `context_refresh_rounds`（默认 10）
轮整体重置一次），一段每轮重复的 480 字经验块（L0 的默认预算，含框架句）在稳态下会被放大约
**10 倍**，这是本设计必须正面处理的真实成本。〔评审 P1-4：v1 写「放大 40 倍」，把「消息数」当成了「轮数」，且忽略了摘要重置
会先于 40 条帽子生效，量级错约 4 倍。〕

产品面上新增两个入口：**设置中心 →「交易经验库」**（图谱视图 / 列表视图双形态、筛选与搜索、
详情抽屉含证据与溯源、置顶 / 静音 / 手工新增 / 编辑 / 删除、从事件重建、导入导出 JSON、以及一个
「检索预演」——用户可以选一组语境，**逐字看到 Agent 下一轮会被喂什么、占多少字符**），以及
**启动弹窗的「经验模式」开关**（默认开启，遵循本仓库既有的「字段缺省 ⇒ 开启、显式 `{enabled:false}`
⇒ 关闭」语义，解析点单一）。贯穿全局的一条硬不变式：**经验层只影响 prompt，绝不进入任何确定性门禁、
风控、仓位或下单判据**——它与 `signals` 一样是 advisory。

〔评审 P1-5〕这条不变式**不等于「爆炸半径为零」**，v1 那句话是错的，这里必须说准：经验文本会
进入两个**由 LLM 做判断**的位点（决策与下单前反思），因此它**能**改变「这一单下不下」——
`build_reflection_prompt` 的第一行就是 `evidence = build_decision_prompt(payload)`
（`prompts.py:761`），L0/L1 本来就会原样出现在复核输入里，而 §4.1 更是刻意把「与提议动作最相
冲突的一条」喂给复核官。真正成立的安全论证是**方向单侧**：反思官的裁决空间只有
`confirm` / `abort` / 缩仓（`REFLECTION_TOOL_SCHEMA` 的 `verdict` 是二值枚举，
`adjustedSizeFraction` 经 `apply_reflection` 对原值取 `min`，只减不增），所以经验层能造成的
最坏后果是**少做一笔本该做的交易**，而不是多做一笔、做反一笔或做大一笔。这才是它敢默认开启的
理由；I-4 / R-1 / 反例压制 / L0 末句声明存在的意义，正是把「少做」这个残余风险也压到最低。

---

## 二、现状取证（不采信推断，全部为本轮实读）

| 事实 | 取证位置 |
|---|---|
| 滚动摘要是会话内存对象，会话终止即消失 | `ai_auto/decider.py:509-574`（`commit_round` / `flush_summary`），`_summary` / `_journal` 为实例字段 |
| 往返结算读数极丰富但只进 200 容量环形缓冲 | `ai_auto/attribution.py:52-83`（`_CAPACITY = 200`），`ai_auto/trip_ledger.py:94-124`（`SettledTrip` 27 个字段） |
| 会话末只把摘要文本落进 `metrics_json`，无下游读点 | `ai_auto/session.py:2261-2300`（`_build_run_metrics` 的 `finalSummary`） |
| 决策 prompt 的装配是「取键 → 渲染 → 缺键即整段省略」 | `ai_auto/prompts.py:605-682`（`build_decision_prompt`），15 个 `_format_*` 全部走该范式 |
| 长文本渲染一律**延迟导入**下沉到专门模块，`prompts.py` 只留 3 行 | `prompts.py:504`（`_format_base_rules`）/ `:597`（`_format_track_record`）/ `:440`（`_format_tactical`） |
| 已存在渐进式披露机制（`contextDisclosure ∈ {full, repeat}`） | `ai_auto/tactical_context.py:170`（`execution_disclosure`）+ `prompts.py:455-478` 的两处 `== "repeat"` 分支 |
| 「启动期一次性快照 + 会话内零 IO」是既有硬纪律 | `ai_auto/track_record_service.py:63-87`（`resolve_track_record` docstring 明写「决策轮零 IO 是硬约束」） |
| 「运行时门面对象 + `inject(payload)`」是既有装配范式 | `PriorRuntime` / `TrackRecordRuntime` / `CostRuntime` / `HorizonRuntime`，均在 `session.__init__` 构造 |
| `create_all` **会**建缺失的表、**不会**给既有表补列 | `db/session.py:90-98` 的四个 `_ensure_*_columns` ALTER 补丁 + `models.py:603` `AiTradeGroupState` 的成文理由 |
| 平仓成交的唯一收口在 `_dispatch`，且该处**已有** DB 写（`_persist_trade`） | `ai_auto/session.py:343-380` |
| 开仓断面在 `_dispatch` 的 else 分支冻结（`(side, seq)` 为 epoch 身份） | `ai_auto/session.py:367-371` |
| 后端零图数据库 / 零图算法依赖 | `services/quant/requirements.txt`（19 行，只有 fastapi/httpx/pandas/numpy/pydantic/SQLAlchemy/pytest/ruff） |
| 设置中心是「注册表 + 分支」两处登记 | `components/Settings/SettingsNav.tsx:7-13` + `SettingsPanel.tsx:58-70` |
| 三个文件已超 CLAUDE.md 的 800 行阈值 | `session.py` 2409 / `manager.py` 1774 / `prompts.py` 846 / `api/ai_auto.py` 803 |

**结论：不能把逻辑写进上面那四个文件**。本设计因此把全部逻辑放进一个新包
`services/quant/app/ai_auto/experience/`，四个既有文件合计净增 ≤ 45 行（预算见 §八）。先例是
`prior_service.py` 的开篇注释：「超了就再下沉一层而不是再放宽」。

---

## 三、核心概念与不变式

### 3.1 三层实体

1. **经验事件（Event）**——原子事实，append-only，**唯一真源**。一条 = 一笔已结算往返
   （`SettledTrip`）+ 它在**开仓那一刻**冻结的语境签名 + 溯源（groupId / runId / instId / bar / seq）。
2. **上下文图（Graph）**——由事件**物化派生**的节点与边。可随时从事件全量重建（设置中心有
   「重建」按钮）。这一性质是本设计的可修复性来源：图坏了不是数据丢了，重算即可。
3. **经验节点（Lesson）**——三种来源：
   - `stat`：由事件按语境桶聚合、通过显著性门槛后**模板化**生成的中文陈述（无 LLM、可复现）；
   - `narrative`：会话末一次有界 LLM 蒸馏产出的叙事教训（必须挂闭合词表里的语境 + 引用具体 trip seq）；
   - `manual`：用户在设置中心手写的经验；`imported`：从 JSON 导入的经验。

### 3.2 七条不变式（实施期不可违反）

- **I-1 · 不进确定性门禁 + 影响方向单侧**〔评审 P1-5 重写〕：经验层的产物**只**进入 prompt
  文本与 UI，**绝不**参与 `edge_gate` / `base_rules` / `profit_guard` / `horizon` / `sizing` /
  `discipline` 任何一道门禁的判据，也绝不改写 `TradeDecision` 的任何字段。
  **但要说准**：它进入决策与反思两个 LLM 判断位点，因此**可以**让一笔本会下的单不下——
  这不是零影响。可依赖的是**方向**：反思官只能 `confirm` / `abort` / 缩仓（裁决 schema 二值 +
  `adjustedSizeFraction` 经 `apply_reflection` 取 `min`），故经验层的最坏后果恒为「少做一笔」，
  永远不会是「多做、做反、做大」。实施期的可验证形态：**任何门禁函数的入参与返回值都不得出现
  `experience` 字样**（G-6 用源码扫描钉死），这是 I-1 的机械判据。
- **I-2 · 零回归**：`experience` 未装配（总闸关 / 启动请求显式关 / 图为空）⇒ payload 无
  `experience` 键 ⇒ `build_decision_prompt` 输出**逐字节**等于本特性合入前。这是必须写用例钉死的
  验收项（§九 G-2）。
- **I-3 · 决策轮零 IO**：`_build_decision_payload` 与其下游不得开任何 DB 会话。图在会话启动时
  一次性载入内存；会话内的增量只在**已有 IO 点**（`_dispatch` 的成交路径）落盘。
- **I-4 · 叙事不得独立成铁律**：`source ∈ {narrative, manual, imported}` 的经验**永远**不能自动
  晋升到 `iron`（L0 常驻层）。要进 L0，要么被 ≥ `min_samples` 条确定性事件佐证，要么由用户**显式
  置顶**（一次人类动作，UI 上明示「置顶的经验不受证据门槛约束」）。
- **I-5 · 语境是闭合词表**：语境轴与取值来自 `taxonomy.py` 的常量表。LLM 蒸馏的输出 schema 用
  `enum` 限死，越界值整条丢弃并记 `rejected` 计数（在设置中心可见）。开放词表会让图不可查询，也会
  让模型自己发明坐标轴。
- **I-6 · 字符预算是硬上限（含框架句）**〔评审 P1-6 重写〕：每一层披露都有 `max_chars`，
  且**页眉与页脚（框架句）计入同一份预算**。渲染顺序是「先扣框架、再按分数装条目」：
  `item_budget = max_chars − len(header) − len(footer)`；条目按 §4.6 的 `score` 降序累加，
  第一条会超 `item_budget` 的即停止，**绝不**在句中截断、**绝不**跳过它去装后面更短的。
  三条硬规则：
  1. **零条目即整段省略**——`item_budget` 装不下任何一条时，整层**一个字都不渲染**（不得只输出
     页眉页脚那个空壳）；
  2. **配置自洽性**：`l0_max_chars` / `l1_max_chars` 必须 ≥ `框架开销 + 124`（单条上限 120 +
     序号与换行 4），由 `config.py` 的 `Field(ge=...)` 与一条启动期断言共同守住；违反即拒绝启动，
     不静默降级；
  3. **默认值按实数校准**：L0 页眉 ≈33 字 + 页脚 ≈37 字 ⇒ 三条满长铁律需要 33+37+372 = **442 字**，
     故 `l0_max_chars` 默认由 v1 的 420 改为 **480**（留 38 字裕量），L1 同理由 600 改为 **660**。
     v1 那句「取第一条就超预算在结构上不可能」只对第一条成立，没把框架开销算进去。
- **I-7 · 成本口径不混用**：事件落 `cost_basis`（`net` | `gross`，取自会话的
  `settings.ai_trade_net_pnl_enabled`——与 `manager.py:1736` 写进 run metrics 的 `costBasis`
  同源同值）与 `fee_known`。**注意取数口径**〔评审 P0-3〕：`fee_known` **不在** `trip_meta_wire`
  的 17 键里（`trip_ledger.py:239-249` 实读），必须从 `SettledTrip` 本体读，见 §4.3。
  任何 bps 类统计**只**聚合 `cost_basis == "net"` 且 `fee_known == True` 的事件；被排除的条数
  如实计入 `excluded` 并在 UI 显示。（成文教训：口径混用会让整张表整体偏移一个往返成本，而那正是
  本仓库另一个特性刚刚修掉的误差。）

---

## 四、技术设计

### 4.1 端到端时序

```
【启动】POST /ai-trade/start  { experience: {enabled:true, ...} | 缺省 }
  api/ai_auto.py::build_start_request
      └─ experience/rules.py::resolve_experience(body.experience, settings)   ← 唯一解析点
      └─ experience/service.py::load_session_graph(db, inst_ids, bar, rule)   ← 唯一取数点（一次）
  manager.py::_build_member
      └─ ExperienceRuntime(subgraph_for(instId), rule, settings)  → AiTradeSession(experience=…)

【每一决策轮】session._build_decision_payload（**必须是本方法的最后一步，排在 `self._wake.inject(...)` 之后**〔评审 P1-3〕）
      └─ self._experience.inject(payload, seed=ContextSeed(...))     ← 纯内存，零 IO；种子只有 7 轴（P1-2）
             ├─ retrieval.retrieve(graph, seed, cfg) → RetrievalResult
             ├─ disclosure.tier(result, prev_result, context_disclosure) → full|brief|repeat
             └─ payload["experience"] = wire（含 maxChars，随证据下发，同 tacticalContext 范式）
      prompts.build_decision_prompt → _format_experience(payload) → 延迟导入 disclosure.format_*

【下单前反思】session._reflect_before_execute
      └─ payload["experienceCounter"] = 与提议动作**最相冲突**的 1 条（≤240 字）  ← L2 的一半

【平仓成交】session._dispatch（side=="close"，已有 IO 点）
      ★ 落点必须在 `self._inject_explain_meta(trade)` **之后**〔评审 P0-3〕——`exitSource` 与
        往返结算都在那一步才写（`session.py:398-411`：`classify_live` → `apply_trip_meta`）；
        挂在更早的 `if side == "close"` 块里读到的 meta 是空的。
      └─ self._experience.note_close(trade, meta, trip)   ← trip = self._cost.last_settled_trip()
      └─ await self._experience.persist_pending()   ← INSERT OR IGNORE，幂等键见 §六
      └─ 在内存图上做增量物化（纯函数），使**同一会话后续轮次立刻用得上**

【暂停 / 停止】session.pause() / stop()
      └─ await self._experience.flush()             ← 剩余事件落盘 + 触发物化（**有界、纯本地 IO**）
      ★ 蒸馏**不在**这条 await 链上〔评审 P0-1〕：`stop_group` 串行遍历成员，而
        `/ai-trade/{groupId}/stop` 的客户端超时是 20 秒（`FAST_MS`）且被判为写路径——
        一次 45 秒的 LLM 调用会让停止按钮弹出「结果未知」。见 §4.9。

【会话终结后（脱离 stop 的 await 链）】manager 侧的有界后台任务
      └─ distill.run(...)                           ← 有界 LLM 蒸馏，wait_for 包裹，失败即放弃

【设置中心】GET /api/experience/graph|overview|nodes/{id}  ·  POST rebuild|preview|import  ·  GET export
```

### 4.2 语境闭合词表（`experience/taxonomy.py`）

11 条坐标轴。取值全部来自仓库里**已经存在且已被验证**的枚举，不新造分类器：

| 轴 `axis` | 取值 `value` | 来源 |
|---|---|---|
| `symbol` | instId 原文 | 会话字段 |
| `bar` | `1m…1D` | `AiExecutionBar` |
| `regime` | `trend_up` / `trend_down` / `range` | `market_context.py` docstring 明列 |
| `volatility` | `high_volatility` / `low_volatility` / `normal` | 同上 |
| `event` | `crash` / `breakout` / `none` | 同上 |
| `side` | `long` / `short` | `SettledTrip.side` |
| `wake` | `candle_close` / `bar_advanced` / `signal_flip` / `resting_order` / `profit_tier` / `price_breakout` / `position_watch` / `heartbeat`（八值，**逐字取自 `wake_reason.WAKE_KINDS`**〔评审 P1-1〕；v1 写的 `candle`/`interval`/`signal`/`catchup`/`backlog` 全部不存在——前两个是 `payload["trigger"]` 的二值，不是 `wake_kind`） | `SettledTrip.wake_kind` |
| `exit` | `agent` / `agentReverse` / `profitGuard` / `exchangeOco` / `timeExit` / `sessionStop` / `windowEnd` / `external` / `unknown` | `exit_attribution.EXIT_SOURCES`（九值，直接复用） |
| `horizon` | 契约 profile id（`PROFILES` 的键）/ `none` | `HorizonRule.profile_id`〔评审 P2-1：属性名是 `profile_id`，`profile` 只是它的 wire 键〕 |
| `costRegime` | `low` / `mid` / `high`（按 `realized_cost_bps` 三分位）〔评审 P1-9 / P2-9 改：**不冻结进事件**，见下〕 | `SettledTrip.realized_cost_bps`（提列） |
| `confidence` | `low`(<0.5) / `mid`(0.5–0.75) / `high`(≥0.75) | `SettledTrip.decision_confidence` |

**`costRegime` 是唯一一条「聚合期现算、不写进事件」的轴**〔评审 P1-9 / P2-9〕。v1 的写法是
「分位点由全库事件周期性重算并落 `app_settings`」+「`note_close` 时把标签冻结进事件语境签名」，
两者合起来会让同一个 `realized_cost_bps` 因写入时刻不同拿到不同标签，而标签一旦冻结就不再更新
⇒ 半年后的 `costRegime:low` 与今天的不是一回事，桶聚合却把它们当同一格，**缓慢失真且不可察觉**。
改正后的口径：
- 事件只落**原始量** `realized_cost_bps`（§6.1 已提列，聚合热路径不必解 JSON）；
- 分位点在**每次物化 / 重建时**按 `(inst_id, bar)` 分组从当前事件全集现算（样本 < 30 时回落全库
  分位并把 `costRegimeFallback: true` 如实带进 `stats_json`，UI 明示）；
- 于是**整个语料在每次重建后恒用同一把尺子**，A-15 的「重建幂等」照旧成立（同一批事件 ⇒ 同一
  组分位点 ⇒ 同一批标签），而跨时可比性得以保留。分位点仍回写 `app_settings.experience.cost_regime_cuts`，
  但那是**只读展示与预演用的缓存**，不是聚合的真源。

规范化：`canonical_key(axis, value) -> "axis:value"`（小写轴名 + 原样取值）。中文展示名走
`AXIS_LABEL_ZH` / `VALUE_LABEL_ZH` 两张常量表，**前后端各一份、由一条源码扫描用例钉死同步**
（仓库既有范式：`wireCaps.source.test.ts`、`contrastTokens.source.test.ts`；注意 CRLF——
正则须写 `\r?\n`，否则干净检出上恒红）。

未知取值一律折成 `unknown`，**不丢事件**（丢事件会造成幸存者偏差）。

### 4.3 事件采集（`experience/events.py`，纯函数）

- **语境冻结在开仓时**：`ExperienceRuntime.note_open(trade, seq, ctx)` 把当轮的
  `{symbol, bar, regime, volatility, event, side, wake, horizon, confidence}` 九轴冻结进一个
  `dict[str, str]`，按 `(side, seq)` 存槽（与 `OpenSnapshot` 同一 epoch 身份，不另造身份）。
  理由：出场时体制可能已经变了，用出场语境标注一笔入场决策的教训是错的。
- **结果补齐在平仓时**〔评审 P0-3 重写〕：`note_close(trade, meta, trip)` 的三个入参各有分工，
  **不能只读 meta**：
  - `meta` = `trade["explain"]["meta"]`，本层**只读不判**的部分是 `exitSource`（沿用
    `apply_trip_meta` 只读该键、绝不自己调 `classify_live` 的先例）与 `wallTs`；
  - `trip` = **`SettledTrip` 本体**，由新增只读访问器 `CostRuntime.last_settled_trip()` 取得
    （它转发 `AttributionLedger.last_settled()`，两者都是纯读、无副作用）。
  - **为什么非取本体不可**：`trip_meta_wire` 只发 17 个键（`trip_ledger.py:239-249` 实读），
    **不含** `fee_known` / `side` / `entry_ts` / `exit_ts` / `edge_error_bps` / `cost_error_bps` /
    `gross_pnl_usd` / `net_pnl_usd`；而 I-7 的过滤依据是 `fee_known`、§6.1 的列要 `side` 与
    `entry_ts`、§4.4 的「估计偏差型」模板要 `edge_error_bps`。按 v1 的「只读 meta」实现，
    这三处会**静默**失效：口径闸空转、`entry_ts` 只能瞎填、一个模板永不触发，且全程不报错。
  - `trip is None`（无开仓断面 ⇒ `settle_close` 返回 `{}` ⇒ 未结算）⇒ **不落事件**，改计
    `harvestGap += 1`（见 §4.11 与 §五 的 `status.experience.harvestGap`）。宁可缺一条并说明，
    也不编一条语境或读数是猜的事件——同 `settle_closed_offline`「绝不写 pnl=0.0」的成文纪律。
  - 补上 `exit` 轴（取自 `meta["exitSource"]`）后组装成 `ExperienceEvent`。`costRegime` **不在**
    这里冻结（§4.2 已改：它是聚合期现算的轴）。
- **双时钟**：事件同时落 `exit_ts`（秒级 bar 时钟，取 `trip.exit_ts`，与全系统 C2 一致）与
  `exit_wall_ts`（墙钟，取 `meta["wallTs"]`；缺席则回落 `time.time()`）；`entry_ts` 取
  `trip.entry_ts`，`entry_wall_ts` 由 `note_open` 当场记下 `time.time()`——**`SettledTrip` 不带
  任何墙钟字段**（`OpenSnapshot.wall_ts` 在 `settle()` 里被消费掉了），这是入场墙钟必须由
  `note_open` 自己冻结的原因。时间衰减用墙钟，历史查询用 bar 钟。（成文教训：双时钟混用是本仓库
  反复出现的缺陷形状。）
- 幂等键：`(group_id, inst_id, seq, exit_ts)` UNIQUE ⇒ 续跑 / 重放 / 并发重复写全部安全。

### 4.4 统计层聚合（`experience/aggregate.py`，纯函数）

对事件集合枚举**语境桶**。桶 = 语境签名的一个**子集**，枚举范围刻意受限，防组合爆炸：

- 一阶桶：`{symbol}` × 每一条其他轴 → 10 类
- 二阶桶：`{symbol, bar}` × 每一条其他轴 → 9 类
- 三阶桶（只枚举 4 组**业务上确有意义**的组合）：
  `{symbol,bar,regime,side}`、`{symbol,bar,volatility,side}`、`{symbol,bar,wake,side}`、
  `{symbol,bar,exit}`
- 跨标的桶（`scope="global"` 候选）：把 `symbol` 轴摘掉的同形桶

每桶算：`n`（样本数）、`wins`、`winRate`、`meanNetBps`、`medianNetBps`、`stdNetBps`、
`tStat = mean/(std/√n)`（复用 `attribution._t_stat` 的同一口径）、`meanHoldS`、
`meanCostBps`、`costShare = meanCostBps/mean|grossBps|`、`meanEdgeErrorBps`、`firstTs`、`lastTs`、
`symbols`（去重标的数）。**只统计 `cost_basis=="net" && fee_known` 的事件（I-7）**，被排除条数
落 `excluded`。

模板化陈述（`aggregate.render_label`）按桶的**主导信号**在 6 个模板里择一，例如：

- 亏损型：`「{标的} {周期} · {语境}：{n} 笔中亏 {loss_n} 笔，净额均值 {x} bps（t={t}）；成本吃掉毛额的 {share}%。」`
- 盈利型：`「{标的} {周期} · {语境}：{n} 笔净额均值 +{x} bps（t={t}），R 倍数中位 {r}。」`
- 退出型：`「{标的} {周期} · 由「{退出来源}」了结的 {n} 笔：净额均值 {x} bps，平均持仓 {h}；{对照句}」`
- 估计偏差型：`「{标的} {周期} · {语境}：事前 edge 估计比实测系统性高 {d} bps（{n} 笔）。」`
- 换手型 / 置信度型同理。

模板产出**恒 ≤ 120 字**（各字段有格式化上限），满足 I-6。

### 4.5 图谱物化（`experience/graph.py`）

- **语境节点**：桶签名里出现过的每个 `axis:value`。`support` = 该语境下的事件数。
- **经验节点**：每个通过 §4.7 门槛的桶一个，`key = "lesson:" + sha1(桶签名 + 模板 id)[:12]`。
  同一桶重算时 `key` 不变 ⇒ 更新而非新增（累积语义）。
- **`applies_in(lesson → context)`**：`w = 桶内该语境的事件数 / 桶的事件数`（一阶桶恒为 1，
  高阶桶给出该语境在这条经验里的份额）。
- **`co_occurs(context ↔ context)`**：归一化点互信息
  `npmi = ln(p(a,b)/(p(a)p(b))) / (−ln p(a,b))`，只保留 `npmi > 0` 且 `p(a,b)` 支撑数
  ≥ `co_min_support`（默认 5）。**无向存两条有向边**（读侧只做单向索引查找，省一次 UNION）。
- **`contradicts(l1 ↔ l2)`**：两条经验语境签名的 Jaccard ≥ 0.6 但 `meanNetBps` 符号相反且两侧
  `|t|` 均 ≥ `t_threshold`。
- **`refines(l_new → l_old)`**：同 `key` 的新版本覆盖旧版本时，旧版本被复制为一个 `archived`
  节点并被指向。**绝不静默覆盖**——版本链是审计线索，也是「经验不断更新」这条需求的诚实实现。

#### 物化必须是「按 `key` upsert」，**不是**「全量替换」〔评审 P0-2〕

v1 写的是「全量替换 `source=="stat"` 子图 + 保留其余来源节点」。**这条会同时制造两个静默故障**：

1. **抹掉用户意志**。`pinned` / `muted` / `mute_reason` 是长在 `stat` 节点上的**人类动作**，删表
   重建就没了。而 §五 的 `DELETE /nodes/{id}` 对 `stat` 节点回 409 时，给出的可执行提示正是
   **「改用静音」**——用户照做，下一次物化那条经验原样复活并重新进 prompt。产品告诉用户的做法，
   产品自己不兑现，这是比丢数据更糟的一类缺陷。
2. **让 `iron` 永不可达**。§4.7 的 `iron` 第三条门槛是「最近两次物化中效应符号一致」，全量替换后
   **没有任何地方存得下上一次的符号**，于是每次物化都看不到历史、`iron` 永远晋升不了、L0 恒空。
   整个特性会「装配成功、日志全绿、一个字都不喂」。

改正后的物化语义（一个 DB 事务内完成）：

- **按 `key` upsert**：新桶 ⇒ INSERT；已存在 ⇒ UPDATE `label` / `weight` / `support` /
  `stats_json` / `signature_json` / `last_seen_ts` / `updated_ts`。
- **绝不覆盖的六个字段**（一旦存在即原样保留）：`pinned`、`muted`、`mute_reason`（`user` 来源的）、
  `first_ts`、`created_ts`、以及 §4.7 用于符号一致性判定的 `sign_streak` / `prev_sign`
  （随 `stats_json` 一起前滚，见 §6.2）。
- **本轮未出现的 `stat` 节点不删**：改置 `muted=True, mute_reason="stale"` 并保留证据链
  （删除会让「这条经验去哪了」变成一个无法回答的问题；`archived` 与 `stale` 两态在 UI 都可筛）。
- **`source != "stat"` 的节点完全不动**（narrative / manual / imported 只由用户与蒸馏改写）。
- 由仓库层一个模块级 `asyncio.Lock` 串行化（桌面端单进程，多标的分组会有 N 个会话同时触发）。
- 这样 A-15 的「重建幂等」不但仍成立，而且**变强**了：重建两次不仅节点/边集合逐值相等，用户的
  置顶与静音也必须一字不差地活着（A-15 的断言按此加强）。

### 4.6 检索算法（`experience/retrieval.py`，纯函数、零 IO）

输入 `ContextSeed` 与 `Graph`，输出 `RetrievalResult`。

**种子在决策轮只有 7 轴，不是 9 轴**〔评审 P1-2〕：`ContextSeed` 在 `_build_decision_payload`
时刻装配，而 `side`（本轮要开多还是开空）与 `confidence`（模型的置信度）**此刻都还不存在**——
两者要到 `decide()` 返回之后才有。故：

| 位点 | 可用轴 |
|---|---|
| 决策轮（L0 / L1） | `symbol` / `bar` / `regime` / `volatility` / `event` / `wake` / `horizon`（7 轴）；**已有持仓时**额外把 `side` 置为**当前持仓方向**（那是一个已发生的事实，不是提议） |
| 反思轮（L2） | 上述 7～8 轴 + **提议动作的 `side`**（`decision.action` 已知）；`confidence` 同理可用 |
| 检索预演（设置中心） | 用户手选，全 11 轴均可 |

这条约束是有代价的、必须写明：§4.4 的四组三阶桶里有三组由 `side` 键控，它们在决策轮只能靠
其余轴拿 `match`（`applies_in` 的边权本就是「该语境在这条经验里的份额」，故不匹配的轴自然减权，
无需额外补丁）；到了反思轮 `side` 才补齐——这也正好是 L2 反例最需要精准的时刻。

**步骤 1 · 种子激活扩散（一跳，有界）**

```
A(c) = 1.0                                        c ∈ seed
A(c') = max_{c ∈ seed} α · w_co(c, c')            c' ∉ seed 且 w_co ≥ θ_co
```

`α = ai_trade_experience_seed_expand_alpha`（默认 0.5）、`θ_co`（默认 0.15）。按 `A` 降序保留
前 `M_ctx`（默认 24）个扩散语境。**刻意只做一跳**：两跳的收益是把「震荡→高波动→急跌」这种
传递性带进来，而代价是相关性被稀释到无法解释，且会把不同标的的语境串起来（I-1 的爆炸半径虽为
零，但一条不可解释的经验仍然是产品缺陷）。

**步骤 2 · 候选收集**：所有与激活语境有 `applies_in` 边、且 `muted == False` 的经验节点，按
`weight` 降序取前 `candidate_cap`（默认 200）。

**步骤 3 · 打分**

```
match(l)   = Σ_c A(c) · w_applies(l, c)
evidence(l)= sat(support) · conf(|t|)
             sat(n) = clip( ln(1+n) / ln(1+N_iron), 0, 1 )
             conf(t) = clip( |t| / t_iron, 0, 1 )
recency(l) = 0.5 ** ( (now_wall − last_seen_wall) / (halflife_days · 86400) )
scope(l)   = 1.0   若 l 的 symbol 语境 ∈ seed
             0.7   若 l.scope == "global"（其支撑跨 ≥ global_min_symbols 个标的）
             0.0   否则                       ← 跨标的污染的硬闸（§十 R-4）
score(l)   = match(l) · evidence(l) · recency(l) · scope(l) · (pinned ? 1.5 : 1.0)
```

**步骤 4 · 反例压制**：对 `contradicts` 边相连且同时进入候选前列的两条经验，判据用**相对差异**
（〔评审 P2-6〕：`match = Σ A(c)·w_applies` 没有量纲上界，会随激活语境数增长，绝对阈值 0.25
在种子多的轮次形同虚设、在种子少的轮次又过于严苛）：

```
rel = |m₁ − m₂| / max(m₁, m₂, ε)        ε = 1e-9
```

- 若 `rel > 0.25` → 保留高分者，低分者记入 `suppressed`（UI 可见，prompt 不出现）；
- 若 `rel ≤ 0.25`（势均力敌）→ **两条都不进 L0/L1**，而是打包成一条「争议」记录送 L2（反思层）。
  理由：把一个未解决的冲突当成事实讲给交易员是产品在撒谎；讲给风控复核官才是对的。

**步骤 5 · MMR 去冗**

```
候选先过滤：drop l if score(l) <= 0          ← 〔评审 P2-5〕
若过滤后为空 ⇒ 该层整段不渲染（不是渲染空壳）
pick = argmax_l  (1−λ)·norm(score(l)) − λ·max_{p∈picked} jaccard(ctx(l), ctx(p))
norm(x) = x / max(scores)                    ← 分母恒 > 0（上一行已保证）
```
`λ = ai_trade_experience_mmr_lambda`（默认 0.5）。保证 3 条 L0 覆盖不同坐标轴，而不是同一条
教训的三个切面。**零分候选必须先剔除**：`scope` 硬闸（§步骤 3）会把所有非本标的的经验打到
`score = 0`，一个「库里只有别的标的经验」的新标的会让整个候选集全零，`norm` 直接除零。

**复杂度**：`O(|seed|·deg_co + |candidate|·|A| + k²)`。实测量级下（数千节点、`M_ctx=24`、
`candidate_cap=200`、`k≤6`）为微秒级纯内存运算，满足「高效快捷」。**决定性**：所有排序均以
`(score, node.key)` 二元组为键，杜绝浮点并列时的不稳定顺序（用例 G-5 钉死）。

### 4.7 晋升 / 衰减 / 版本链（`experience/promote.py`，纯函数）

| 档位 `tier` | 门槛 | 披露 |
|---|---|---|
| `candidate` | `n < min_samples`(8) 或 `|t| < t_threshold`(2.0) | 不进 prompt，UI 可见 |
| `normal` | `n ≥ 8` 且 `|t| ≥ 2.0` | L1 可选 |
| `iron` | `n ≥ iron_samples`(20) 且 `|t| ≥ iron_t`(2.5) 且 `sign_streak ≥ 2`（**最近两次物化中效应符号一致**，见下） | L0 可选 |
| `archived` | 被 `refines` 指向的旧版本 | 仅 UI 版本链 |
| `muted` | 用户静音，或 `now − lastTs > stale_days`(90) 且未被新证据佐证 | 全部不披露；UI「已沉降」筛选可见 |

**符号一致性必须有一个活得过物化的落点**〔评审 P0-2 配套〕：物化按 `key` upsert 时，先读出
既有行的 `stats_json.prevSign`，与本次的 `sign(meanNetBps)` 比较——相同则 `signStreak += 1`，
不同则 `signStreak = 1` 并触发下一段的翻转处理；两者随 `stats_json` 一起写回。没有这一步，
`iron` 的第三条门槛在结构上永远判不成立（v1 的「全量替换」正是这么塌的）。

**符号翻转 = 降档 + 建 `contradicts` 边 + 建 `refines` 链**，而不是就地改数。用户在 UI 上因此
能看到「这条经验曾经是反过来的，在 X 月被 N 笔新样本推翻」——这正是「经验是累积的、不断更新」
所要求的东西，而静默覆盖只会给出一个无法追责的当前值。

**`last_seen_ts` 的语义是硬约定**〔评审 P1-7〕：它是该经验**最近一条支撑事件的墙钟**
（`max(event.exit_wall_ts)`，与 `stats_json.lastTs` 同值同源），**不是**最近一次物化的时刻——
后者由 `updated_ts` 单独承载。这两个字段在实现期极易被合成一个，而合成的后果是 §4.6 的
`recency(l)` 在每次重建后被拨回 now ⇒ 衰减因子恒为 1 ⇒ 三年前的教训与昨天的同权，且不会报错。
`stale` 判据（`now − lastTs > stale_days`）用的也是这同一个量。

`narrative` / `manual` / `imported` 来源：起始档 `candidate`，被 ≥ `min_samples` 条同桶事件佐证
后可升 `normal`；**升 `iron` 只有一条路径——用户显式置顶**（I-4）。

### 4.8 渐进式披露（`experience/disclosure.py`，纯函数）

三层 + 一个档位开关：

| 层 | 触发 | 条数 | 字符上限（含框架句，默认） | 落点 |
|---|---|---|---|---|
| **L0 常驻纲要** | 每轮 | ≤3（`iron` + `pinned`） | **480**（v1 的 420 装不下 3 条满长铁律，见 I-6） | `build_decision_prompt` |
| **L1 语境展开** | 仅 `tier=="full"` 轮 | ≤3（`normal`，与 L0 去重） | **660** | 同上，紧随 L0 |
| **L2 反例/证据** | 下单前反思轮 | 1 条（最相冲突者或争议对） | 240 | `build_reflection_prompt` 的**追加段** |

**关于 L2 的一条必须写明的事实**〔评审 P1-5 配套〕：`build_reflection_prompt` 的第一行是
`evidence = build_decision_prompt(payload)`（`prompts.py:761`），所以反思轮看到的**不是** 240 字，
而是「决策轮那份 L0/L1（按当轮档位渲染）+ 这 240 字的反例」。这是**有意**的——复核官需要看到
交易员看到的同一份证据才能复核；但它意味着：① 反思轮的经验字符预算是 `L0+L1+L2` 的和，
成本核算须按此计；② 若当轮档位是 `repeat`，复核官看到的 L0 也是那一行压缩形态，**反例段因此
必须自带足够的自解释**（不得写「见上文第 2 条」这种指向可能已被压缩掉的内容的引用）。

**档位判定** `experience_tier(result, prev_result, context_disclosure)`：

- `full` — 会话首轮、或本轮 L0∪L1 的经验 id 元组与上轮**不同**、或既有 `contextDisclosure == "full"`
  且发生了 regime / side / wake 档升；
- `brief` — id 元组不变且 `contextDisclosure == "full"` ⇒ 只渲染 L0；
- `repeat` — id 元组不变且 `contextDisclosure == "repeat"` ⇒ **整段压成一行**
  `「【交易经验】同上 3 条铁律（未变化）」`。

这一档位机制是本设计对「防止上下文爆炸」的正面回答，理由是一个非显然的乘数——但**这个乘数是
约 10，不是 40**〔评审 P1-4：v1 把「消息数」当成了「轮数」，并忽略了摘要重置先于消息帽生效〕：

- `commit_round` 每轮往 `_context` append **三条**消息（user prompt / assistant tool_use /
  tool_result，`decider.py:546-551`），而 `build_decision_prompt(payload)` 在那里被**再渲染一次**
  原样塞进 user 消息；
- `_trim_context` 的帽子 `ai_trade_max_context_messages` 默认 **40 条消息** ≈ 13 轮
  （`config.py:160`）；
- 但 `summarize()` 会**整体重置** `_context`（`decider.py:624`），周期是 `context_refresh_rounds`
  默认 **10 轮**（`schemas.py:357`）⇒ 稳态下 40 条那顶帽子**几乎从不生效**，真实驻留 ≈ 10 轮。

于是：一段每轮 480 字的经验块在留存窗口里的真实占用是 `480 × 10 ≈ 4.8 KB`，而不是 480 字。
加上档位机制后，稳态（语境未变）每轮只留约 22 字，**留存窗口占用从约 4.8 KB 降到约 0.7 KB**
（首轮付全额 480 + 其后 9 轮各 22），仅在语境真的移动的那几轮付全额。

**这个数字必须写对的理由**：它是本设计说服自己「档位机制值得这份复杂度」的唯一依据，也会被
节点 #2 / #3 逐字引用。7 倍的压缩比足以支撑这个结论；虚报成 19 倍则让下游无法核对。
另外注意 `commit_round` 的**二次渲染**：档位判定的结果必须住在 `payload["experience"]["tier"]`
里（本设计已如此），**绝不能**在渲染函数内部现场重算——两次渲染之间任何状态漂移都会让
`_context` 里的那一份与模型真正看到的那一份不一致。

**预算裁剪**〔评审 P1-6 重写〕：`item_budget = max_chars − len(header) − len(footer)`；先按
§4.6 的 `score` 排序渲染条目，累加字符；第一条会超 `item_budget` 的即停止（不截句、不跳过后续
更短的条目——跳过会让输出顺序依赖预算余量，难以测试）。三条硬规则：

1. **框架句计入预算**，且**先扣后装**——否则最终输出会超 `max_chars`，I-6 当场破。
2. **零条目 ⇒ 整层一个字都不渲染**（连页眉页脚都不出），绝不产出「有标题没内容」的空壳。
3. **配置自洽性由启动期断言守**：`max_chars ≥ 框架开销 + 124`（单条 120 + 序号与换行 4）。
   由于 `label ≤ 120 字` 是写入期 schema 硬限，满足该式即可保证「第一条一定装得下」——
   v1 那句「结构上不可能」漏掉了框架开销，故 420 的默认值实际装不下 3 条满长铁律
   （33 + 37 + 3×124 = 442 > 420），默认值已随之上调到 480 / 660。

**渲染形态**（L0 示例，实际由 `format_l0` 生成）：

```
【交易经验 · 铁律】（来自你过往 N 次自动交易的 M 笔已结算往返）
1. BTC-USDT-SWAP 15m · 高波动+突破 追多：23 笔净额均值 −31bps（t=−2.8），成本吃掉毛额 68%。
2. BTC-USDT-SWAP 15m · 由「利润保护」了结的 41 笔净额 +54bps；由「Agent 主动平」了结的 37 笔 −12bps。
3. 全标的 · 置信度<0.5 的开仓：58 笔胜率 38%，显著低于 ≥0.75 组的 61%。
以上为统计结论，非指令；与当前证据冲突时以当前证据为准，并在研判中说明理由。
```

最后一句是必须的：没有它，模型会把统计结论当成硬规则执行，那等于让经验层绕过 I-1 变成一道
影子门禁。

### 4.9 会话末叙事蒸馏（`experience/distill.py`）

- **时机：脱离 `stop()` 的 await 链，作为 manager 侧的一个有界后台任务**〔评审 P0-1 重写〕。

  **v1 的写法（「挂在 `session.stop()` 的既有 best-effort 收尾块内、`_finalize_run` 之后」）
  有两处硬伤**：
  1. `session.stop()`（`session.py:541-579` 实读）在 `_finalize_run(ts)` 之后**只有**
     `_notify_stopped()` 与一次 `_push`，**根本不存在**那个「既有 best-effort 收尾块」；
  2. 更致命的是量纲：`manager.stop_group`（`manager.py:745-757`）**串行**遍历成员逐个
     `await self._stop_session(...)`，而前端 `/ai-trade/{groupId}/stop` 命中
     `WRITE_ACTION_FAMILIES + "/stop"`，超时档是 `FAST_MS = 20_000`（`lib/httpTimeout.ts`）。
     3 标的 × 45s ≥ 135 秒，客户端 20 秒放弃并弹出**「本次操作结果未知，请刷新持仓/会话状态核对
     后再决定是否重试」**——在真实资金的停止/平仓按钮上制造一次假警报，代价远大于本特性的
     全部收益（`timeoutMessage` 的注释直接点名提交 `7b55d16`）。

  **改正后的落点**：

  ```
  session.stop()            ← 逐字不变，只多一行 await self._experience.flush()（纯本地 IO，毫秒级）
  manager._stop_session()   ← 返回前 spawn 一个 tracked task：_schedule_distill(member)
        └─ asyncio.create_task(...) 存进 manager 的模块级 _distill_tasks: set[Task]
        └─ 任务体：asyncio.wait_for(distill.run(...), timeout=ai_trade_experience_distill_timeout_s)
        └─ 全异常吞掉只记 warning；done_callback 从 set 里摘掉自己（防泄漏）
  app shutdown              ← 既有有界关停链里追加一次
                              await asyncio.wait(_distill_tasks, timeout=distill_timeout + 5)
                              超时即 cancel（关停链仍然有界，符合 a8ed650 确立的纪律）
  ```

  于是：停止请求的时延**逐字不变**；蒸馏结果通过下一次 `GET /api/experience/overview` 轮询
  或一条 `experience_distilled` WS 事件到达 UI；进程被杀 ⇒ 最多丢这一次叙事蒸馏，
  **确定性事件早已落盘，零数据损失**。
- 超时 / 异常 / 无 LLM 客户端 ⇒ 记 warning 后放弃。（成文教训：关停链必须有界且每步异常隔离，
  一步失败不得吃掉后面的清理。）
- 输入：`compact_journal_for_summary(journal)`（复用 `prompts.py:788` 的既有压缩器）+ 本会话
  `settled_trips()` 的紧凑表 + 本会话已注入过的经验 id（防止模型把喂进去的话原样吐回来）。
- 输出：强制工具调用 `submit_trade_lessons`，schema：
  ```
  { lessons: [ { statement: str(≤120),
                 detail: str(≤600),
                 context: { axis: enum-value, ... }   ← 轴名与取值均为 enum，越界即整条丢弃
                 tripSeqs: [int] (≥1),                ← 无引用即整条丢弃
                 polarity: "positive"|"negative" } ]  (≤5 条) }
  ```
- 落地：`source="narrative"`, `tier="candidate"`。**不参与统计层的重建**（重建只重算 `stat` 子图）。

### 4.10 运行时装配（`experience/runtime.py`）

`ExperienceRuntime` 严格镜像 `TrackRecordRuntime` / `PriorRuntime` 的既有形态：

```python
class ExperienceRuntime:
    def __init__(self, graph: Graph | None, rule: ExperienceRule | None, settings) -> None: ...
    # 未装配（graph is None / rule is None / rule.enabled is False）⇒ 以下全部空转
    def note_open(self, trade: dict, *, seq: int, ctx: dict[str, str]) -> None: ...
    # trip = SettledTrip | None（由调用方从 CostRuntime.last_settled_trip() 取；None ⇒ 计 harvestGap 不落事件，P0-3）
    def note_close(self, trade: dict, meta: dict, trip) -> None: ... # 组装事件 + 内存增量物化
    async def persist_pending(self) -> None: ...                    # INSERT OR IGNORE，best-effort
    async def flush(self) -> None: ...                              # pause/stop 收尾（纯本地 IO，毫秒级）
    def inject(self, payload: dict, *, seed: ContextSeed) -> None: ...   # 写 payload["experience"]
    def inject_reflection(self, payload: dict, decision) -> None: ...    # 写 payload["experienceCounter"]
    def distill_inputs(self) -> dict | None: ...                    # 供 manager 侧后台蒸馏取素材（P0-1）
    def stats(self) -> dict | None: ...                             # status / metrics 观测五键（含 harvestGap）
```

`inject` 写入的 wire 形状（**随证据下发 `maxChars`**，与 `tacticalContext` 同范式，使渲染层只吃
payload、不必拿 `Settings`）。新增的 `graphAsOfTs` 是**本会话那份图快照的建立时刻**——它是
§五「检索预演」诚实性的凭据（〔评审 P1-10〕：预演读的是当前 DB 的图，运行中的会话用的是启动时
的快照，两者会分叉；把快照时刻发出来，UI 才能如实说「该会话使用的是 X 时刻的经验库」）：

```json
{
  "tier": "full|brief|repeat",
  "l0": [{"id": 12, "key": "lesson:ab12…", "text": "…", "support": 23, "tStat": -2.8, "source": "stat"}],
  "l1": [ … ],
  "disputed": [{"a": 12, "b": 30, "axis": "regime"}],
  "totals": {"lessons": 148, "events": 2043, "sessions": 37},
  "maxChars": {"l0": 480, "l1": 660},
  "graphAsOfTs": 1755000000
}
```

### 4.11 暂停 / 续跑 / 多标的并发

- **续跑（配置）**：`resume.py` 的 S2 会用 `start_wire` 原文重跑 `build_start_request`
  （`api/ai_auto.py:371` 实读 `start_wire=body.model_dump(mode="json")`），`experience` 键在原文里
  ⇒ 续跑自动同配置，**这一半确实无需额外代码**。图会被重新载入（拿到暂停期间其他会话沉淀的
  新经验），这是期望行为。
- **续跑与离线平仓的采集缺口（v1 漏掉，必须正视）**〔评审 P1-8〕：有两条真实路径**不会**产生
  经验事件，而它们**恰好偏向亏损样本**（被强平的、长期持有跨越暂停的），不处理就是教科书式的
  幸存者偏差——正是本设计在 §4.2 用「未知取值折成 `unknown`、不丢事件」努力避免的那一种：

  | 路径 | 取证 | 为什么采不到 |
  |---|---|---|
  | 暂停期间被交易所平掉的持仓 | `resume.settle_closed_offline`（`resume.py:302-334`） | 它直接 `repo.append_live_trade(...)` 落库，**完全不经 `session._dispatch`** ⇒ 无 `record_close` / `apply_trip_meta` / `note_close` |
  | 续跑接管的既有持仓 | `adopt_position(...)`（`resume.py:294`） | 接管**不调** `_cost.note_open` ⇒ `attribution._open` 为 None ⇒ 该持仓最终平仓时 `settle_close` 返回 `{}`，连 `SettledTrip` 都不存在，更没有开仓瞬刻的语境签名 |

  **v1 的处置（本轮采纳）**：**不补采、但如实计数并露出**。理由是补采要么得复刻一遍开仓语境
  （猜的语境比没有语境更坏），要么得在 `resume` 路径新开一条采集通路（触碰真实资金的续跑链，
  收益远不抵风险）。故：
  - `ExperienceRuntime` 维护 `harvestGap` 计数（§4.3 已定义：`trip is None` 即 +1）；
  - `status.experience.harvestGap` 与 `aiStats.experience.harvestGap` 如实下发；
  - 设置中心概览条显示「另有 N 笔往返因续跑/离线平仓未能采集」并给一句说明。
  - **这是一条明确的 v1 欠账，写进 §十一「明确不做」第 7 条**，v2 若要补采，正确的做法是让
    `adopt_position` 在接管时用**接管那一刻**的语境建一个 `origin="adopted"` 的降级签名，
    并在经验节点上标注该来源，而不是假装它与正常开仓同质。
- **幂等**：事件唯一键 `(group_id, inst_id, seq, exit_ts)` 使续跑重放不会双计。
- **并发**：一个分组 N 个成员会话并行写事件。写入用 `INSERT ... ON CONFLICT DO NOTHING`（SQLite
  `INSERT OR IGNORE`），物化用模块级 `asyncio.Lock` 串行。载入是只读，无锁。

---

## 五、接口设计（wire）

全部走 `/api` 前缀、经既有 `OriginGuardMiddleware`（ASGI 中间件，新路由自动受保护，无需逐路由接线）。
错误体统一 `{error:{code,message}}`（main 全局处理器）。

| 方法 · 路径 | 入参 | 回执 | 说明 |
|---|---|---|---|
| `GET /api/experience/overview` | — | `{lessons:{iron,normal,candidate,muted}, events, sessions, symbols:[…], lastUpdatedTs, rejectedNarrative, excludedByCostBasis}` | 概览条 |
| `GET /api/experience/graph` | `instId? bar? axis? tier? source? q? limit=120` | `{nodes:[…], edges:[…], total, truncated}` | 图谱视图取数；按 `weight` 降序截断，`truncated` 如实下发 |
| `GET /api/experience/lessons` | 同上 + `offset/limit` | `{items:[…], total}` | 列表视图（无障碍等价形态） |
| `GET /api/experience/nodes/{id}` | `evidenceLimit=20` | `{node, stats, contexts:[…], versions:[…], contradicts:[…], evidence:[{groupId,instId,bar,entryTs,exitTs,netBps,exitSource}]}` | 详情抽屉 |
| `POST /api/experience/nodes` | `{statement, detail, context:{axis:value}, polarity}` | `{node}` | 手工新增（`source="manual"`, `tier="candidate"`） |
| `PATCH /api/experience/nodes/{id}` | `{statement? detail? pinned? muted? tier?}` | `{node}` | `tier` 只允许在 `candidate/normal/iron` 间**降档**或对 `pinned` 升 `iron` |
| `DELETE /api/experience/nodes/{id}` | — | `204` | 仅 `source ∈ {manual, narrative, imported}`；`stat` 类返回 `409 EXPERIENCE_STAT_NODE_UNDELETABLE`（重建会复活，删除是假动作），提示改用「静音」 |
| `POST /api/experience/rebuild` | `{scope:"stat"}` | `{nodes, edges, elapsedMs}` | 从事件全量重建统计子图；CPU 密集，走 `run_in_threadpool`（沿用 `run_backtest` / `prior_service` 范式） |
| `POST /api/experience/preview` | `{instId, bar, regime?, volatility?, event?, side?, wake?, horizon?, confidence?, costRegime?}` | `{tier, l0:[…], l1:[…], disputed:[…], chars:{l0,l1,total,budget}, seedActivation:[{key,a}], graphAsOfTs, liveSessions:[{instId, graphAsOfTs, diverged:bool}]}` | **检索预演**。回执**必须**带 `graphAsOfTs` 与运行中会话的快照时刻对照，见下 |
| `GET /api/experience/export` | `instId? since? until? kind=all\|graph\|events` | **NDJSON 流**（`StreamingResponse`，首行 `{"version":1,"exportedTs":…,"counts":{…}}`，其后每行一个 node / edge / event） | 数字资产的可携带性。〔评审 P2-7〕**必须流式**：跑半年的库是几万条事件，一次性拼进内存再序列化会在桌面端把整个进程顶起来；`counts` 让接收方能校验完整性 |
| `POST /api/experience/import` | `{version, nodes, edges, events, mode:"merge"\|"replace"}` | `{imported:{nodes,edges,events}, rejected:{…}}` | 见 §十 R-8 的不可信输入处理 |
| `GET /api/settings/experience` | — | `{enabled, defaultOn, minSamples, ironSamples, tThreshold, halfLifeDays, staleDays, l0Max, l0MaxChars, l1Max, l1MaxChars, distillEnabled}` | 放在 `api/app_settings.py`，与 `/settings/auto-trade` 并列（一致性优先） |
| `PUT /api/settings/experience` | 同上可写子集 | 同上 | 越界由 schema 触发 422 |

**「所见即 Agent 所得」必须说准**〔评审 P1-10〕：`preview` 读的是**当前 DB 里的图**，而运行中的
会话用的是**启动那一刻载入内存的快照**（I-3 的零 IO 约束使然），两者会因本会话增量物化与其它
成员会话的写入而分叉。所以：

- 回执带 `graphAsOfTs`（预演用的图快照时刻）与 `liveSessions[]`（每个运行中会话的
  `graphAsOfTs` 与 `diverged` 标志，后者由 manager 现查内存会话得到，不查 DB）；
- **UI 必须在分叉时明说**：「BTC-USDT-SWAP 的会话正在使用 08-24 21:30 的经验库快照，与下方
  预演（当前库）不同；下一次启动会话时才会用上新沉淀的 3 条经验。」不分叉时才可以显示
  「这就是 Agent 下一轮会看到的内容」；
- A-25 的等值断言相应收窄为**同一份图快照下**的逐值相等（这才是可测且为真的命题）。

**启动请求增量**（`AiTradeGroupStartIn` / `AiBacktestStartIn` 同款可选字段）：

```jsonc
"experience": {
  "enabled": true,        // 缺省整键 ⇒ 按 ai_trade_experience_default_on（默认 true）装配
  "scope": "symbol",      // "symbol"（默认，只用本标的 + global 经验）| "global"（放宽跨标的权重到 1.0）
  "write": true           // 是否把本次会话的经验写回资产（默认 true；回测恒 false，见 §十一）
}
```

**会话状态增量**（`_status_payload` / `snapshot`）：`"experience": {"enabled":bool, "tier":str,
"l0":int, "l1":int, "chars":int, "eventsHarvested":int, "harvestGap":int, "graphAsOfTs":int}`——
让运行中的面板能说真话：「本次会话已沉淀 N 条经验事件，本轮喂入 3 条铁律（共 312 字）；另有
K 笔往返因续跑/离线平仓未能采集」。`harvestGap`〔评审 P1-8〕与 `graphAsOfTs`〔评审 P1-10〕
是本轮新增的两个诚实性键——**后端算了却没有呈现落点的观测量等于死码**，故两者在 §7.3 的
`ExperienceCard` 各有明确渲染位置。
（`store.patchAiTradeMemberStatus` 实读为 `{...m.session, ...status}` 展开而非逐键手抄
（`store.ts:963-970`），故只要 `AiTradeSessionRow` 的 TS 类型补上该字段，新键即可安全流到前端。）

**run metrics 增量**：`aiStats.experience = runtime.stats()`（会话终结后事后对账可按「有没有喂
经验」分臂，与 `[P1-9]` 的 `trackRecordFed` 同款动机）。

---

## 六、数据模型

三张新表。**全部是新表，不给既有表加列** ⇒ `create_all` 自动建、老库首次启动即补、零迁移代码、
不可能 `no such column`（`models.py:603` `AiTradeGroupState` 的成文理由）。

### 6.1 `ai_experience_events`（原子事实，append-only，唯一真源）

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | Integer PK autoincr | |
| `group_id` | String(48) index | 溯源到一次 AI 交易 |
| `run_id` | Integer index | |
| `inst_id` | String(32) | |
| `bar` | String(8) | |
| `seq` | Integer | 会话内持仓序号（epoch 身份的一半） |
| `origin` | String(8) default `"live"` | `live` \| `backtest` \| `adopted`。**v1 恒为 `"live"`**〔评审 P2-3：v1 称它是「真实读点、非预留列」不实——§十一.2 明确不做回测侧写入，故该列在 v1 只有一个取值，`WHERE origin='live'` 是恒真过滤〕。**仍然保留，理由要写对**：`create_all` 建表但不补列（`db/session.py:90-98` 的四个 `_ensure_*_columns` 就是为此存在的补丁），事后加列必须写 ALTER；现在加它代价为零，v2 开回测写入或续跑补采（§4.11）时免掉一次迁移。聚合层照写 `WHERE origin='live'`，语义是「口径闸」而非死码 |
| `entry_ts` / `exit_ts` | Integer | 秒级 UTC（bar 时钟，C2） |
| `entry_wall_ts` / `exit_wall_ts` | Integer | 墙钟（时间衰减用，双时钟不混用） |
| `side` | String(8) | long \| short |
| `context_json` | Text | 开仓瞬刻冻结的九轴语境签名 `{axis: value}` |
| `outcome_json` | Text | `SettledTrip` 的 wire（净额/毛额 bps、成本 bps、R 倍数、计划 RR、持仓秒、edge 误差、退出来源…） |
| `cost_basis` | String(8) | `net` \| `gross`（I-7 的过滤依据） |
| `fee_known` | Boolean | 同上 |
| `net_bps` | Float nullable | 从 `outcome_json` 提列（聚合热路径避免逐行解 JSON） |
| `gross_bps` | Float nullable | 同上，提列（成本占比 `costShare` 的分母） |
| `cost_bps` | Float nullable | 同上，提列。**`costRegime` 轴的分位点在聚合期从这一列现算**（§4.2 已改：不冻结进 `context_json`）〔评审 P1-9〕 |
| `edge_error_bps` | Float nullable | 同上，提列（「估计偏差型」模板的输入；取自 `SettledTrip.edge_error_bps`，**该字段不在 `trip_meta_wire` 里**，见 §4.3）〔评审 P0-3〕 |
| `hold_seconds` | Float | 同上，提列 |
| `exit_source` | String(16) | `EXIT_SOURCES` 九值之一；同时进 `context_json` 的 `exit` 轴（提列供筛选与索引） |
| `created_ts` | Integer | |

约束：`UniqueConstraint(group_id, inst_id, seq, exit_ts, name="uq_ai_exp_event")`；
`Index("ix_ai_exp_event_scope", inst_id, bar, exit_ts)`；`Index("ix_ai_exp_event_group", group_id)`。

### 6.2 `ai_experience_nodes`（物化图节点）

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | Integer PK | |
| `kind` | String(8) | `context` \| `lesson` |
| `key` | String(96) **unique** index | `axis:value` 或 `lesson:<12hex>` |
| `axis` / `value` | String(24) / String(64) nullable | 仅 context 节点 |
| `label` | String(200) | 展示句（lesson 的 ≤120 字陈述） |
| `detail` | Text | lesson 的长文（≤600 字）；context 为 `""` |
| `source` | String(12) | `stat` \| `narrative` \| `manual` \| `imported` \| `context` |
| `scope` | String(8) | `symbol` \| `global` |
| `tier` | String(12) | `candidate` \| `normal` \| `iron` \| `archived` |
| `pinned` / `muted` | Boolean | 用户意志（覆盖自动档位） |
| `mute_reason` | String(24) nullable | `user` \| `stale` \| `flipped` |
| `support` | Integer | 证据条数（排序热路径，去规范化） |
| `weight` | Float | `evidence(l)` 派生值 ∈ [0,1] |
| `stats_json` | Text | 桶统计全量（`n/win/mean/median/std/t/hold/cost/edgeErr/symbols/excluded/firstTs/lastTs`） |
| `signature_json` | Text | lesson 的语境签名（重建时按它比对 `key`） |
| `first_ts` / `last_seen_ts` | Integer | **墙钟秒**。`last_seen_ts` = 该经验**最近一条支撑事件**的 `exit_wall_ts`（与 `stats_json.lastTs` 同值同源），**不是**最近一次物化的时刻——后者由 `updated_ts` 承载。〔评审 P1-7：两者若被合成一个字段，§4.6 的 `recency` 会在每次重建后被拨回 now ⇒ 衰减恒为 1 ⇒ 三年前的教训与昨天同权，且不会报错〕 |
| `prev_sign` | Integer | `-1` \| `0` \| `+1`，上一次物化时 `meanNetBps` 的符号 |
| `sign_streak` | Integer | 符号连续一致的物化次数；`iron` 门槛第三条读它。〔评审 P0-2：v1 的「全量替换」使这段历史无处存放，`iron` 因此永不可达〕 |
| `created_ts` / `updated_ts` | Integer | `created_ts` 在 upsert 时**绝不覆盖**；`updated_ts` 每次物化刷新 |

`Index("ix_ai_exp_node_pick", kind, tier, muted)`、`Index("ix_ai_exp_node_axis", axis, value)`。

**upsert 时绝不覆盖的字段（§4.5 的落地清单）**〔评审 P0-2〕：`pinned`、`muted`、`mute_reason`
（`user` 来源的）、`first_ts`、`created_ts`；`prev_sign` / `sign_streak` 按 §4.7 的规则前滚而非
重置。数据层需为此提供一个**单点** upsert 函数（`ai_experience_repositories.upsert_lesson_nodes`），
禁止在编排层写第二份 SQL——两份必漂移，而这里漂移的是用户亲手按下的「静音」。

### 6.3 `ai_experience_edges`（物化图边）

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | Integer PK | |
| `src_id` / `dst_id` | Integer ForeignKey(`ai_experience_nodes.id`) index | |
| `rel` | String(16) | `applies_in` \| `co_occurs` \| `contradicts` \| `refines` |
| `weight` | Float | §4.5 各自的定义域 |
| `support` | Integer | 共现 / 支撑条数 |
| `updated_ts` | Integer | |

`UniqueConstraint(src_id, dst_id, rel, name="uq_ai_exp_edge")`、`Index("ix_ai_exp_edge_src", src_id, rel)`。

### 6.4 设置持久化

复用既有 `app_settings` 表（稀疏 KV，`models.py:387`）：键
`experience.enabled` / `experience.default_on` / `experience.min_samples` / `experience.iron_samples` /
`experience.t_threshold` / `experience.half_life_days` / `experience.stale_days` /
`experience.l0_max` / `experience.l0_max_chars` / `experience.l1_max` / `experience.l1_max_chars` /
`experience.distill_enabled` / `experience.cost_regime_cuts`（三分位点，物化时重算并回写）。
读取方缺记录即回落 `config.py` 默认（沿用 `get_auto_trade_settings` 的稀疏语义）。

### 6.5 `config.py` 新增（**27 项**，全部带默认，均可被 `app_settings` 覆盖）

〔评审 P2-4：v1 标题写「20 项」、正文列 25 项、§8.2 写「+25 个」，三处互不相符；本节为唯一真源。〕

```
ai_trade_experience_enabled: bool = True             # 服务端总闸；False ⇒ 全链路空转 + I-2 零回归
ai_trade_experience_default_on: bool = True          # 启动请求缺省语义（需求：默认开启）
ai_trade_experience_write_enabled: bool = True       # 采集写闸（只读模式：用经验但不沉淀）
ai_trade_experience_min_samples: int = Field(8, ge=1)
ai_trade_experience_iron_samples: int = Field(20, ge=1)
ai_trade_experience_t_threshold: float = Field(2.0, gt=0)
ai_trade_experience_iron_t_threshold: float = Field(2.5, gt=0)
ai_trade_experience_half_life_days: float = Field(30.0, gt=0, le=3650)
ai_trade_experience_stale_days: float = Field(90.0, gt=0, le=3650)
ai_trade_experience_l0_max: int = Field(3, ge=0, le=10)
ai_trade_experience_l0_max_chars: int = Field(480, ge=160, le=4000)   # 见 I-6：须 ≥ 框架开销 + 124
ai_trade_experience_l1_max: int = Field(3, ge=0, le=10)
ai_trade_experience_l1_max_chars: int = Field(660, ge=160, le=4000)   # 同上
ai_trade_experience_reflection_max_chars: int = Field(240, ge=0, le=2000)
ai_trade_experience_seed_expand_alpha: float = Field(0.5, ge=0, le=1)
ai_trade_experience_co_edge_min_weight: float = Field(0.15, ge=0, le=1)
ai_trade_experience_co_edge_min_support: int = Field(5, ge=1)
ai_trade_experience_max_expanded_contexts: int = Field(24, ge=0, le=200)
ai_trade_experience_candidate_cap: int = Field(200, ge=1, le=5000)
ai_trade_experience_mmr_lambda: float = Field(0.5, ge=0, le=1)
ai_trade_experience_graph_node_cap: int = Field(4000, ge=100, le=100000)
ai_trade_experience_global_min_symbols: int = Field(3, ge=1, le=50)
ai_trade_experience_distill_enabled: bool = True
ai_trade_experience_distill_timeout_s: float = Field(45.0, gt=0, le=300)
ai_trade_experience_distill_max_lessons: int = Field(5, ge=1, le=20)
# ---- 本轮评审新增 ----
ai_trade_experience_cost_regime_min_samples: int = Field(30, ge=1, le=5000)   # P2-9：按 (instId,bar) 分组算分位的最小样本；不足即回落全库并标注
ai_trade_experience_export_page_size: int = Field(500, ge=50, le=5000)        # P2-7：NDJSON 流的批大小
```

**两条由 `Field` 与启动期断言共同守住的自洽性约束**（违反即拒绝启动，不静默降级）：

1. `l0_max_chars ≥ L0 框架开销 + 124` 且 `l1_max_chars ≥ L1 框架开销 + 124`（I-6 / 评审 P1-6）；
2. `distill_timeout_s` **只被后台任务读**，绝不出现在任何同步等待 `stop()` 的路径上（评审 P0-1）——
   由一条源码扫描用例钉死（G-6）。

---

## 七、前端设计

### 7.1 设置中心 →「交易经验库」

`SettingsNav.SECTIONS` 追加一项 `{ key: "experience", label: "交易经验库", desc: "AI 沉淀的经验与教训" }`，
`SettingsPanel` 追加一条分支（两处登记，缺一即静默不可达）。

布局（自上而下，沿用 `card` / `--surface` / `--border` 设计令牌，不引入新色）：

1. **概览条**：五个统计块（铁律 / 常规 / 候选 / 已沉降 / 事件样本）+ 覆盖标的 chips + 「最近更新」
   相对时间。右侧工具区：`重建图谱` / `导出` / `导入` / `设置`（阈值面板，折叠）。
2. **工具栏**：标的下拉、周期下拉、坐标轴多选 chips、档位段控（全部/铁律/常规/候选/已沉降）、
   来源段控（统计/叙事/手工/导入）、搜索框（对 `label` + `detail` 做子串匹配，300ms 防抖）。
3. **主区 `Tabs`**（复用 `ui/index.tsx` 的 `Tabs`，该原语已有 `role="tablist"` 与方向键语义）：
   - **图谱**：SVG 力导向图。
     - 布局：**确定性**——种子取自 `node.key` 的哈希，固定迭代 300 步的 Fruchterman-Reingold
       简化版（`lib/experienceGraph.ts`，纯函数、可单测），因此**同一份数据每次渲染位置完全一致**，
       不会在筛选切换时乱跳（这是图可视化最常见的体验缺陷）。
       **确定性的两个前提必须写死**〔评审 P2-8〕：① 布局前把节点按 `node.key`、边按
       `(src, dst, rel)` 做**全序排序**——后端回执的顺序不是契约，输入顺序一变坐标就变，
       A-20 会成为一条时红时绿的用例；② 布局是 `useMemo(…, [nodes, edges])` 的产物，
       **绝不在 render 里跑**（120 节点 × 300 迭代是 O(n²·iter) ≈ 4×10⁶ 次浮点运算，
       落在 render 路径上会让筛选切换掉帧）。
     - 视觉：语境节点 = 小圆角矩形 chip，按 `axis` 取 11 种色相（全部来自既有语义色令牌的
       色相环推导，不硬编码新颜色）；经验节点 = 圆点，半径 `∝ √support`，环色表示 `tier`
       （铁律 = `--accent`，常规 = `--muted`，候选 = 虚线环，已沉降 = 30% 透明）。
       边：`applies_in` 实线（不透明度 = weight）、`co_occurs` 细虚线、`contradicts` 双色警示线
       （用本仓库已有的 `--warn` 令牌，不新造）、`refines` 箭头。
     - 交互：滚轮缩放（0.4×–3×）、拖拽平移、点击节点 → 高亮其 1 跳邻域并打开详情抽屉、
       双击语境节点 → 把它加入筛选。**键盘**：`Tab` 在节点间按 `weight` 序移动（每个节点是
       `<g role="button" tabIndex={0}>`），`Enter` 打开抽屉，`Esc` 关闭。
     - 性能与诚实：默认渲染上限 120 个节点（`limit`），后端回 `truncated:true` 时**在图上明示**
       「已按权重展示前 120 / 共 N 个节点」并给「提高上限」按钮——**绝不静默截断**（成文教训：
       静默截断读起来像「全都在这儿了」）。
   - **列表**：与图谱**同数据源同筛选**的可访问表格（陈述 / 档位 / 来源 / 样本 n / 净额均值 /
     t / 覆盖标的 / 最近证据时间 / 操作）。这是图谱的无障碍等价形态——只有图的界面对读屏用户
     不可用，而「符合人机交互最佳实践」要求二者并存。行操作：置顶 / 静音 / 编辑 / 删除。
   - **检索预演**：左侧一组语境选择器（标的 / 周期 / 体制 / 波动 / 事件 / 方向 / 唤醒 / 置信度），
     右侧**逐字渲染**下一轮 Agent 会看到的 L0 / L1 文本块，底部一条字符预算进度条
     （`312 / 480 字`）与被压制条目的说明。这是本特性最有说服力的一屏：它把「Agent 到底记住了
     什么」从黑箱变成可核对的事实。
     **但这一屏的可信度取决于它说不说实话**〔评审 P1-10〕：预演读的是**当前**经验库，而运行中的
     会话用的是**启动那一刻**的快照（I-3 的零 IO 约束）。故顶部必须有一条状态行，取
     `preview.liveSessions[]` 现渲染：
     - 无运行中会话 ⇒ 「这就是下一次启动会话时 Agent 会看到的内容」；
     - 有会话且 `diverged=false` ⇒ 「这就是 BTC-USDT-SWAP 的 Agent 下一轮会看到的内容」；
     - 有会话且 `diverged=true` ⇒ **「BTC-USDT-SWAP 的会话正在使用 08-24 21:30 的经验库快照，
       与下方预演（当前库）不同——新沉淀的 3 条经验要到下次启动会话时才会生效。」**
     少了这条状态行，这一屏在最需要它的时刻（会话正在跑）说的是假话，而一屏假话比没有这一屏更坏。
4. **详情抽屉**（右侧 `Modal` 变体，宽 460px）：陈述 / 长文 / 统计（n、胜率、净额均值与中位、t、
   平均持仓、成本占比、edge 误差）/ 语境签名 chips / 版本链（`refines`）/ 冲突（`contradicts`，
   附「两条都不进纲要」的说明）/ 溯源证据表（最近 20 笔，点击可跳到 AI 交易历史详情）/ 操作区。

破坏性操作（删除、替换式导入、重建）一律走既有 `ui/ConfirmDialog`，**不得**使用 `window.confirm`
（成文教训：全仓已清零原生阻塞对话框）。所有异步按钮走既有 `httpTimeout` 包装，超时文案说
「未知」而不是「失败」（成文教训：写操作超时不等于没写成）。

### 7.2 启动弹窗「经验模式」

新增组件 `components/Agent/AiTradeExperienceFields.tsx`（镜像 `ProfitGuardFields` /
`AiTradeHorizonFields` 的既有形态），插在【交易周期模式】之后、【上下文轮数】之前：

```
☑ 启用经验模式（推荐）                                   [查看经验库 →]
  让 Agent 参考过往 N 次自动交易沉淀的 M 条经验（当前标的 K 条铁律）。
  ○ 仅本标的经验（推荐）   ○ 含跨标的通用经验
  ☑ 把本次会话的经验写回经验库
  预计每轮额外上下文：约 312 字（上限 480 字）          [预演 →]
```

- 默认勾选（`AI_TRADE_EXPERIENCE_DEFAULTS.enabled = true`），落 `AiTradeLaunchOpts.experience`；
  取消勾选 ⇒ 提交 `{enabled:false}`（**显式关闭**，与「字段缺省 = 默认开启」严格区分——这是本仓库
  在 `baseRules` / `profitGuard` / `tradingHorizon` 上已经踩过三次的语义）。
- 条数与字符预估来自 `GET /api/experience/overview` + `POST /api/experience/preview`（`useQuery`，
  弹窗打开时取一次；接口失败 ⇒ 只隐藏这两行数字，**不影响勾选与启动**）。
- 偏好记忆：`AiTradeConfig.experience?: AiTradeExperienceRule`（**可选字段**，读点一律
  `?? AI_TRADE_EXPERIENCE_DEFAULTS` 兜底，**不改 persist version**——沿用 `baseRules` 先例，
  避免动 migration 链）。
- 回测 tab：显示同一张卡但 `write` 恒关且不可勾（附说明「回测样本不写入经验库」），`enabled`
  在 v1 亦恒关（理由见 §十一）。

### 7.3 运行中的可见性

- `AiTradeStatsLive` 追加一张 `ExperienceCard`：「经验模式 已启用 · 本轮喂入 3 条（312 字）·
  本次会话已沉淀 7 条经验事件」，未启用时整卡不渲染。卡片还必须承载 §五 新增的两个诚实性键
  （**后端算了却没有渲染落点的观测量等于死码**，这是本仓库反复付过学费的一条）：
  - `harvestGap > 0` ⇒ 追加一行灰色注记「另有 2 笔往返因续跑/离线平仓未能采集」+ 一个说明气泡
    指向 §4.11〔评审 P1-8〕；
  - `graphAsOfTs` ⇒ 卡片副标题显示「经验库快照：08-24 21:30」，与设置中心的预演状态行同一口径
    〔评审 P1-10〕。
- `AiTradeAgentTrace` 的 `round_start` 追加 `experience` 键（**加法键，不新增 trace phase**——
  新增相位的代价是前端 6 处镜像点，先例见 `trace.py:143` 的成文决策），在轮卡里以一枚可展开的
  「经验 ×3」徽标呈现，展开即逐条显示本轮实际喂入的文本。**用户必须能看到 Agent 到底被喂了什么**。

---

## 八、文件 / 模块变更计划

### 8.1 后端新建（新包 `services/quant/app/ai_auto/experience/`）

| 文件 | 意图 | 预估行 |
|---|---|---|
| `experience/__init__.py` | 对外再导出（`ExperienceRuntime` / `resolve_experience` / 三个 service 入口） | 30 |
| `experience/taxonomy.py` | 11 条语境轴的闭合词表 + `canonical_key` + 中文展示名 + 未知折叠 | 180 |
| `experience/events.py` | `ExperienceEvent` 数据类 + 由 `(冻结语境, SettledTrip wire)` 组装（纯函数） | 200 |
| `experience/aggregate.py` | 事件 → 语境桶 → 统计量 → 模板化陈述（纯函数，6 个模板） | 420 |
| `experience/graph.py` | `Graph` 内存结构 + 由 DB 行构建 + 增量并入 + npmi 共现边计算 | 380 |
| `experience/promote.py` | 档位晋升 / 衰减 / 符号翻转 → `refines`+`contradicts` 版本链（纯函数） | 240 |
| `experience/retrieval.py` | 种子激活扩散 + 打分 + 反例压制 + MMR（纯函数、零 IO） | 320 |
| `experience/disclosure.py` | 三层渲染 + 档位判定 + 硬字符预算裁剪（纯函数） | 300 |
| `experience/runtime.py` | `ExperienceRuntime` 会话门面（载入一次 / 逐轮 inject / 增量 / flush） | 330 |
| `experience/distill.py` | 会话末 LLM 蒸馏（system prompt + tool schema + 校验 + fail-open） | 280 |
| `experience/rules.py` | `ExperienceRule` + `resolve_experience`（「缺省即开启」的**唯一**解析点） | 120 |
| `experience/service.py` | API 编排：graph 查询 / 详情 / 重建 / 预演 / 导入导出（含线程池卸载） | 480 |
| `db/ai_experience_repositories.py` | 数据层（自包含，经 `repositories.py` 再导出，沿用既有拆分范式） | 520 |
| `api/experience.py` | 新路由（11 个端点） | 330 |

> 全部文件 ≤ 800 行、单方法 ≤ 50 行、形参 ≤ 5（超出即用聚合数据类，先例 `SettleContext`）。

### 8.2 后端修改（净增预算硬约束）

| 文件 | 改动 | 净增 |
|---|---|---|
| `db/models.py`（634 行） | +3 张表 | +95 |
| `db/repositories.py` | 再导出一行 import 块 | +12 |
| `db/session.py` | **无改动**（`create_all` 自动建新表，不需要 `_ensure_*` 补丁） | 0 |
| `config.py`（845 行） | +27 个 `ai_trade_experience_*` 配置项 + 两条自洽性断言 | +46 |
| `schemas.py` | `AiTradeExperienceIn` + 11 个端点的 In/Out 模型 + `AiTradeGroupStartIn.experience` | +180 |
| `main.py` | `include_router(experience.router, prefix="/api")` 一行 | +2 |
| `api/app_settings.py`（42 行） | `GET/PUT /settings/experience` 两个端点（与 auto-trade 并列） | +45 |
| `api/ai_auto.py`（**803 行，已超阈**） | `build_start_request` 内 2 行：`experience_rule=resolve_experience(body.experience, settings)` + `experience_graph=await load_session_graph(...)` | **+6** |
| `ai_auto/manager.py`（**1774 行，已超阈**） | `AiTradeGroupStartRequest` +2 字段；`_build_member` 构造 `ExperienceRuntime` 并透传；`_stop_session` 返回前 spawn 有界蒸馏任务（**任务体本身在 `experience/distill.py`，这里只有 2 行调度**，评审 P0-1） | **+18** |
| `ai_auto/session.py`（**2409 行，已超阈**） | `__init__` +1 形参 +3 行；`_dispatch` 两处 hook（+4）；`_build_decision_payload` +2；`_reflect_before_execute` +2；`stop`/`pause` +4；`_status_payload` +2；`_build_run_metrics` +2 | **+20** |
| `ai_auto/prompts.py`（**846 行，已超阈**） | `_format_experience(payload)` 延迟导入下沉（+8）+ `build_decision_prompt` 插入点（+3）+ `build_reflection_prompt` 插入点（+3） | **+14** |
| `ai_auto/trace.py` | `round_start` 初值 +1 键（显式取键装配，漏抄即静默缺失） | +3 |
| `ai_auto/decider.py`（686 行） | 一个只读属性 `llm`（`self._llm` 是私有的，「从外部持引用」拿不到——〔评审 P2-2〕v1 的「无改动」不成立） | **+2** |
| `ai_auto/cost_runtime.py`（629 行） | 只读访问器 `last_settled_trip()`（转发 `AttributionLedger.last_settled()`）〔评审 P0-3〕 | **+5** |
| `ai_auto/attribution.py`（287 行） | 只读访问器 `last_settled()`（返回 `self._settled[-1] if self._settled else None`） | **+4** |
| `main.py` 的关停链 | 追加一次有界 `asyncio.wait(_distill_tasks, timeout=…)`〔评审 P0-1〕 | **+4** |

四个超阈后端文件合计净增 **58 行**（`session.py` 20 + `manager.py` 18 + `prompts.py` 14 +
`api/ai_auto.py` 6）；`db/repositories.py`（**1029 行，同样已超阈**，v1 漏列）净增 **12**（只有
再导出块）。三个未超阈的新接线点（`decider` / `cost_runtime` / `attribution`）合计 **+11**。
这是本设计最重要的结构约束：**所有逻辑必须在新包里，超阈文件只做接线**；任何超出都改为再下沉
一层，而不是放宽阈值（`prior_service.py` 的成文先例）。

### 8.3 前端

**先说预算**〔评审 P1-11〕。v1 的 §8.2 对后端四个超阈文件做到了逐行预算，§8.3 对前端只字未提，
而实测这三个前端文件**同样已经超了 CLAUDE.md 的 800 行阈值**：

| 已超阈文件 | 现行数 | 本特性允许的净增 | 硬约束 |
|---|---|---|---|
| `lib/aiTradeTypes.ts` | **1787** | **0** | 经验层的 TS 类型**全部**住在新文件 `lib/experienceTypes.ts`；此处只允许 `AiTradeSessionRow` 补一个 `experience?: AiTradeExperienceStatus`（该类型从 `experienceTypes.ts` import），净增 2 行**已含在下面的 +2 里** |
| `components/Agent/AiTradeLaunchModal.tsx` | **1578** | **≤ 12** | 卡片本体在 `AiTradeExperienceFields.tsx`；此处只允许「一条 state + 一个渲染插入点 + 一处 body 组装」。v1 估的「约 35 行」必须压到 12 以内，做不到就把 `experience` 的状态整体下沉进 `AiTradeExperienceFields` 的受控组件契约 |
| `lib/aiTrade.ts` | **1412** | **0** | `AI_TRADE_EXPERIENCE_DEFAULTS` 与所有格式化函数住在新文件 `lib/experience.ts`；`aiTrade.ts` 一行不加 |
| `lib/aiTradeTypes.ts`（合计） | — | **+2** | 见上 |

其余全部为新建文件，各自 ≤ 800 行、单函数 ≤ 50 行。节点 #3 终审会按同一把尺子量前后端，
所以这张表与 §8.2 一样是**硬判据**，不是建议。

| 文件 | 意图 |
|---|---|
| `lib/experienceTypes.ts` | wire 类型镜像（node / edge / overview / preview / rule） |
| `lib/experienceApi.ts` | REST 客户端（沿用 `autoTradeSettingsApi` 的 `httpTimeout` + 错误体解析范式） |
| `lib/experienceGraph.ts` | **确定性**力导向布局 + 邻域高亮 + 视口变换（纯函数，可单测） |
| `lib/experience.ts` | 轴/取值中文展示名、档位与来源标签、字符预算格式化 |
| `lib/experience.test.ts` / `experienceGraph.test.ts` / `experienceLabels.source.test.ts` | 单测 + 前后端词表同步的源码扫描用例（正则须 `\r?\n`） |
| `components/Settings/ExperienceSection.tsx` | 区块外壳：概览条 + 工具栏 + Tabs |
| `components/Settings/experience/ExperienceGraphCanvas.tsx` | SVG 图谱（缩放/平移/键盘/截断提示） |
| `components/Settings/experience/ExperienceList.tsx` | 可访问表格视图 |
| `components/Settings/experience/ExperienceDetailDrawer.tsx` | 详情抽屉（统计 / 版本链 / 冲突 / 溯源） |
| `components/Settings/experience/ExperienceEditor.tsx` | 手工新增 / 编辑表单（语境选择器受闭合词表约束） |
| `components/Settings/experience/ExperienceRetrievalPreview.tsx` | 检索预演 + 字符预算条 |
| `components/Settings/experience/ExperienceToolbar.tsx` | 筛选 / 搜索 / 重建 / 导入导出 |
| `components/Settings/SettingsNav.tsx` · `SettingsPanel.tsx` | 两处登记（各 +1 行 / +2 行） |
| `components/Agent/AiTradeExperienceFields.tsx` | 启动弹窗「经验模式」卡片 |
| `components/Agent/AiTradeLaunchModal.tsx` | `AiTradeLaunchOpts.experience` + 状态 + 渲染插入点（+~35 行） |
| `components/Agent/aiTradeStats/ExperienceCard.tsx` | 运行中会话的经验卡 |
| `components/Agent/AiTradeTraceRound.tsx` | 「经验 ×3」可展开徽标 |
| `lib/experienceTypes.ts`（**不是** `aiTradeTypes.ts`） | `AiTradeExperienceRule` / `AiTradeExperienceStatus` / node / edge / overview / preview 全部 wire 类型〔评审 P1-11〕 |
| `lib/aiTradeTypes.ts` | **仅 +2 行**：`AiTradeConfig.experience?` 与 `AiTradeSessionRow.experience?`（均从 `experienceTypes.ts` import；**不改 persist version**——沿用 `baseRules` 先例，`store.ts` 现为 `version: 30`） |
| `lib/experience.ts` | `AI_TRADE_EXPERIENCE_DEFAULTS` + 轴/取值中文展示名 + 档位与来源标签 + 字符预算格式化（**`lib/aiTrade.ts` 一行不加**） |
| `lib/aiTradeApi.ts` | start body 透传（既有 body 组装处 +1 行） |
| 各组件的 `*.test.tsx` | 见 §九 |

---

## 九、测试与验收标准

### 9.1 门禁（每条须贴实测输出，不采信上游报告）

- **G-1** 后端全量：`services/quant/.venv/Scripts/python -u -m pytest -q`（**必须 `-u`**，否则
  Windows 控制台缓冲会让长跑看起来像挂死；**不得并发跑全量**——`services/quant` 的用例共享同一个
  临时 DB）。基线红条数须与改动前一致。
- **G-2 · 零回归（本轮最重要的一条）**：新增
  `test_ai_trade_experience_zero_regression.py`——同一 payload 在「未装配经验层」下走
  `build_decision_prompt`，断言输出与冻结样本**逐字节相等**；`build_reflection_prompt` 同款。
- **G-3** 前端：`NODE_ENV=test pnpm --filter web vitest run`（**必须带 `NODE_ENV=test`**，否则
  组件用例报 React act() 错）。
- **G-4** `pnpm --filter web exec tsc --noEmit` 与 `ruff check services/quant` 无新增。
- **G-5 · 停止路径时延不回归**〔评审 P0-1〕：一条用例把 `distill.run` 打成 `await asyncio.sleep(120)`
  的桩，起一个 3 成员分组，断言 `stop_group(...)` 的墙钟耗时 **< 2 秒**、三个 run 均落终态、
  且蒸馏任务仍在 `_distill_tasks` 里在飞。这条用例是 P0-1 的机械判据——没有它，「蒸馏不在停止
  链上」只是一句口号。
- **G-6 · I-1 与 P0-1 的源码扫描门禁**：两条正则用例（`\r?\n`，CRLF 环境，见
  `contrastTokens.source.test.ts` 先例）——① 六个门禁模块（`edge_gate` / `base_rules` /
  `profit_guard` / `hold_horizon` / `sizing` / `discipline`）的源码里**不得出现** `experience`
  字样；② `ai_auto/session.py` 与 `ai_auto/manager.py` 的 `stop` / `pause` 方法体内**不得出现**
  `distill`。静态判据比人工复核可靠，且不会随人员更替失效。

### 9.2 逐条验收

| 编号 | 验收项 | 手段 |
|---|---|---|
| A-1 | 一笔平仓成交 ⇒ 恰好落 1 条事件，语境取自**开仓**瞬刻 | `test_experience_events.py`：构造开仓时 regime=range、平仓时 regime=trend_up，断言事件语境为 `range` |
| A-2 | 重复 dispatch / 续跑重放 ⇒ 事件不双计 | 同一 `(group,inst,seq,exit_ts)` 写两次，断言 `count == 1` |
| A-3 | `cost_basis=="gross"` 或 `fee_known==False` 的事件不进 bps 统计，且 `excluded` 如实计数 | `test_experience_aggregate.py` |
| A-4 | 桶 `n < min_samples` ⇒ `candidate`，不出现在任何 prompt 层 | `test_experience_promote.py` |
| A-5 | 效应符号翻转 ⇒ 降档 + 建 `refines` + 建 `contradicts`，旧版本仍可查 | 同上 |
| A-6 | 检索**确定性**：同图同种子跑 100 次，结果序列完全一致 | `test_experience_retrieval.py` |
| A-7 | 共现扩散生效：`regime:range+volatility:high` 学到的经验，在 `regime:range+volatility:normal` 下以衰减权重出现 | 同上 |
| A-8 | 跨标的硬闸：PEPE 的 symbol 域经验在 BTC 种子下 `score == 0` | 同上 |
| A-9 | **字符预算是硬上限（含框架句）**：构造 500 条各 120 字的铁律，断言 L0 渲染**整段**（页眉+条目+页脚）≤ `l0_max_chars`、条数 ≤ 3、不在句中截断；再把 `l0_max_chars` 调到刚好装不下第一条，断言**整段一个字都不渲染**（不是只剩页眉页脚）〔评审 P1-6〕 | `test_experience_disclosure.py` |
| A-10 | 档位机制：id 元组不变 + `contextDisclosure=="repeat"` ⇒ 整段压成一行且 ≤ 40 字 | 同上 |
| A-11 | **决策轮零 IO**：把 `get_sessionmaker` 打成会抛的桩，跑完整一轮 `_run_decision_round`，断言不抛 | `test_experience_session.py` |
| A-12 | 争议对（`contradicts` 且 match 接近）⇒ 两条都不进 L0/L1，进反思层 | `test_experience_retrieval.py` |
| A-13 | 蒸馏超时 / LLM 报错 ⇒ `stop()` 正常收尾，run 落终态，事件已落盘 | `test_experience_distill.py` |
| A-14 | 蒸馏输出含闭合词表外的轴/值 ⇒ 整条丢弃并计入 `rejected` | 同上 |
| A-15 | **重建幂等**：同一批事件重建两次 ⇒ 节点/边集合逐值相等；且 `manual`/`narrative` 节点存活 | `test_experience_rebuild.py` |
| A-16 | 导入不可信 JSON：越界轴、超长文本、`tier:"iron"`、注入型字符串 ⇒ 全部被拒或夹紧，`source` 强制 `imported` | `test_experience_import.py` |
| A-17 | `stat` 节点 DELETE ⇒ 409 且给出「改用静音」的可执行提示 | `test_experience_api.py` |
| A-18 | 启动请求整键缺省 ⇒ 按 `default_on` 装配；`{"enabled":false}` ⇒ 不装配 | `test_experience_start_wire.py` |
| A-19 | 续跑（`start_wire` 重跑解析链）⇒ 经验配置逐字段与首次启动相同 | 同上 |
| A-20 | 前端图布局确定性：同数据两次布局，坐标逐值相等 | `experienceGraph.test.ts` |
| A-21 | 前端截断诚实性：`truncated:true` ⇒ 必须渲染「已展示前 N / 共 M」文案 | `ExperienceGraphCanvas.test.tsx` |
| A-22 | 列表视图与图谱**同数据源**：同一筛选下条数一致 | `ExperienceSection.test.tsx` |
| A-23 | 启动弹窗默认勾选；取消 ⇒ 提交 `{enabled:false}` 而非省略整键 | `AiTradeLaunchModal.test.tsx` |
| A-24 | 前后端中文词表同步 | `experienceLabels.source.test.ts`（正则 `\r?\n`） |
| A-25 | 预演接口回执与真实注入**同源**：**同一份图快照下**，同一 seed 的 `preview.l0` 与 `runtime.inject` 的 `l0` 逐值相等〔评审 P1-10：跨快照不成立，断言必须收窄，否则这条用例迟早会因为「预演读 DB、会话读内存」而变成一条时红时绿的谎言〕 | `test_experience_preview_parity.py` |
| **A-26** | **停止路径不被蒸馏拖长**（P0-1 的验收面） | 见 G-5 |
| **A-27** | **物化保留用户意志**（P0-2）：置顶 + 静音一条 `stat` 经验 → 触发两次物化 → 断言 `pinned` / `muted` / `mute_reason` / `first_ts` / `created_ts` 逐值不变，且该经验**没有**回到 L0/L1 | `test_experience_rebuild.py` |
| **A-28** | **`iron` 真的能晋升**（P0-2 的另一半）：喂 25 笔同符号事件 → 连续两次物化 → 断言 `sign_streak == 2` 且 `tier == "iron"` 且该条出现在 L0；第三次物化喂入反号事件 → 断言降档 + 建 `contradicts` + 建 `refines` | `test_experience_promote.py` |
| **A-29** | **`note_close` 取得到全部字段**（P0-3）：构造一笔 `fee_known=False` 的往返，断言事件的 `fee_known` 为 False、`edge_error_bps` / `side` / `entry_ts` 均非空，且该事件**不进** bps 统计而进 `excluded` | `test_experience_events.py` |
| **A-30** | **`trip is None` 不编事件**（P0-3）：无开仓断面时平仓 → 断言不落事件、`harvestGap == 1`、且该计数出现在 `status.experience` 与 `aiStats.experience` | 同上 |
| **A-31** | **档位机制真的生效**（P1-3）：把 `experience.inject` 排到 `_wake.inject` 之前 → 断言用例**失败**（`contextDisclosure` 缺席 ⇒ 档位恒 `full`）；正确顺序下 `repeat` 轮整段 ≤ 40 字 | `test_experience_session.py` |
| **A-32** | **`recency` 真的会衰减**（P1-7）：同一批事件重建两次（间隔一年的墙钟桩）→ 断言 `last_seen_ts` 不变、`recency` 单调下降 | `test_experience_retrieval.py` |
| **A-33** | **`costRegime` 跨时可比**（P1-9）：先写 100 条低成本事件建库，再写 100 条高成本事件后重建 → 断言**全部** 200 条的 `costRegime` 标签按同一组分位点重算，且分组不足 30 条时 `costRegimeFallback` 为真并出现在 `stats_json` | `test_experience_aggregate.py` |
| **A-34** | **导出是流式的**（P2-7）：造 20000 条事件 → 断言导出端点回 `StreamingResponse`、首行 `counts` 与实际行数一致、且进程 RSS 峰值不超过基线 + 50MB | `test_experience_export.py` |

### 9.3 定义完成（DoD）

1. G-1～**G-6 六道**门禁全绿，且贴出实测输出。
2. A-1～**A-34** 全部有对应用例且通过。
3. 端到端手测：起一次真实小额 AI 自动交易 → 平掉一笔 → 设置中心「交易经验库」立刻能看到新事件
   → **停止会话（掐表：按钮必须在 2 秒内给出结果，不得出现「结果未知」文案）** → 稍后蒸馏产出
   可见 → 重启一次会话，轨迹面板的「经验 ×N」徽标能展开看到被喂入的原文。
4. 关闭经验模式的会话，其决策 prompt 与本特性合入前逐字节相同（G-2 已自动化，手测复核一次）。
5. 手测复核 §7.1 预演状态行：会话跑起来后在设置中心手工新增一条经验，预演页必须显示
   「该会话使用的是 X 时刻的快照，与当前库不同」，而**不是**「这就是 Agent 下一轮会看到的内容」。
6. 手测复核 §7.3 的两个诚实性键：制造一次 `harvestGap`（暂停 → 交易所侧平仓 → 续跑）后，
   卡片必须出现那行灰色注记；`graphAsOfTs` 必须与该会话的启动时刻一致。

---

## 十、风险与缓解

| # | 风险 | 后果 | 缓解 |
|---|---|---|---|
| R-1 | **自我强化的幻觉回路**：模型写一条经验 → 下次读到 → 照做 → 再写一遍，最终把一个从未被验证的偏见铸成铁律 | 真实资金按幻觉交易 | I-4（叙事永不自动进 L0）+ 蒸馏输入里排除本会话被喂过的经验 id + 蒸馏 schema 强制引用具体 `tripSeqs` + L0 文案末句显式声明「统计结论非指令」 |
| R-2 | **上下文爆炸**（本需求点名的风险） | 决策 prompt 膨胀 → 成本上升 / 超时 / 模型注意力稀释 | 三层硬字符预算（I-6，含框架句）+ 档位压缩（§4.8，留存窗口占用 **约 4.8KB → 约 0.7KB**，评审 P1-4 已修正量级）+ A-9/A-10/A-31 用例钉死 |
| R-3 | **决策轮引入新 IO 面** | 决策轮阻塞 / 超时回落 hold（真实资金错过行情） | I-3：启动期一次载入 + 会话内纯内存；写只挂在**已有** IO 点；A-11 用抛异常的桩钉死 |
| R-4 | **跨标的污染**：PEPE 1m 的教训用在 BTC 1H | 用别的标的的历史开真实仓位，且不会报错 | `scope` 硬闸（symbol 域不匹配即 `score=0`）+ `global` 需跨 ≥3 标的支撑且额外 ×0.7 惩罚（成文教训：`prior_snapshots` / `track_records` 按 instId 键的同款理由） |
| R-5 | **成本口径混用**导致整张表偏移一个往返成本 | 所有 bps 类经验系统性失真 | I-7：事件落 `cost_basis`，聚合只吃 `net`+`fee_known`，被排除条数在 UI 明示 |
| R-6 | **双时钟混用**（bar 秒 vs 墙钟） | 时间衰减算错、历史查询错位 | 事件同时落两套时钟，衰减用墙钟、查询用 bar 钟，字段名分别为 `*_ts` / `*_wall_ts`（仓库既有命名） |
| R-7 | **蒸馏拖垮关停链**〔评审 P0-1 提级：这不是「可能」，v1 的写法是**必然**触发〕 | 停止按钮在客户端 20 秒超时并弹出「本次操作结果未知」——在真实资金的平仓按钮上制造假警报 | 蒸馏**移出** `stop()` 的 await 链，改为 manager 侧的 tracked 后台任务（§4.9）；`wait_for` 有界；app 关停链追加一次有界 `asyncio.wait` 后 cancel；失败只 warning；确定性事件在此之前**已经**落盘。**G-5 用一条时延用例把这条钉死** |
| R-8 | **导入是不可信输入** | 越界档位/超长文本/注入型内容进入 prompt | 强制 `source="imported"`、`tier ≤ normal`、轴与取值走闭合词表校验、长度夹紧、不解析任何可执行内容；导入前 `ConfirmDialog` 明示「导入的经验会进入 Agent 的上下文」 |
| R-9 | **SQLite 并发写**（多标的分组 N 会话同时落事件 / 物化） | `database is locked` / 图半成品 | 事件写 `INSERT OR IGNORE`（幂等键保护）；物化走模块级 `asyncio.Lock` + 单事务原子替换；两者都是 best-effort，失败只 warning 不阻断交易 |
| R-10 | **图谱渲染卡顿** | 设置页假死 | 默认 120 节点上限 + 确定性布局离线计算（纯函数、可 memo）+ 截断如实提示 + 列表视图作为等价出口 |
| R-11 | **四个超阈文件继续膨胀** | 触碰 CLAUDE.md 硬阈值、评审不通过 | §8.2 的净增预算是硬判据（合计 54 行）；任何超出必须改为再下沉一层，而不是放宽阈值（`prior_service.py` 的成文先例） |
| R-12 | **经验为空时的冷启动**（新用户没有任何历史） | 卡片显示空、用户以为坏了 | 空图 ⇒ payload 无 `experience` 键（I-2 零回归路径）；UI 显示引导态「还没有经验——完成第一次自动交易后，这里会出现你的第一条」，而不是空白或报错 |
| R-13 | **回测样本污染真实经验资产** | 用模拟成交的成本口径推翻真实教训 | `origin` 列 + 聚合 `WHERE origin='live'`；v1 回测侧 `write` 恒 false 且 UI 不可勾。〔评审 P2-3：v1 该列在本版本只有一个取值，它是**为 v2 预留的口径闸**而非已生效的读点；保留的理由是 `create_all` 不补列、事后加列要写 ALTER〕 |
| **R-14** | **物化抹掉用户意志**〔评审 P0-2〕 | 用户静音的经验在下一次重建后原样复活并重新进 prompt；`iron` 因符号历史丢失而永不可达 ⇒ L0 恒空、特性「装配了却一个字都不喂」 | 物化改为**按 `key` upsert**（§4.5），六个字段绝不覆盖；符号历史落 `prev_sign` / `sign_streak` 两列（§6.2）；数据层单点 upsert 函数，禁止第二份 SQL；A-27 / A-28 钉死 |
| **R-15** | **采集缺口造成幸存者偏差**〔评审 P1-8〕 | 续跑接管的持仓与暂停期间被交易所平掉的持仓（**恰好偏向亏损样本**）永不进经验库，整张表系统性偏乐观 | v1 **不补采、但如实计数并露出**：`harvestGap` 进 `status` / `aiStats` / 概览条（§4.11、§7.3）；写进 §十一 作为明确欠账；A-30 钉死计数不为零时确实可见 |
| **R-16** | **经验层通过反思官否掉本该做的交易**〔评审 P1-5〕 | 一条错误的铁律让复核官持续 `abort` ⇒ 用户看到「Agent 一直不开单」 | 影响方向单侧（只会少做，不会做反/做大）；I-4 叙事永不自动进 L0；反例压制把争议对挡在 L0/L1 之外；`aiStats.experience` 记录本会话经验注入次数与 `abort` 次数，供事后分臂对账；用户可一键关闭经验模式（启动弹窗与设置中心各一处入口） |

---

## 十一、明确不做（Out of scope，v1）

1. **Agent 可调用的 `expand_experience` 工具**（让模型自己决定展开哪条）。理由：`decider._stream_decision`
   目前是「必须且只能调用一次 `submit_trade_decision`」的强制单工具循环，是一条被大量用例冻结的
   契约；加第二个工具需要改成多轮工具循环。v1 用**确定性三层披露 + 反思层反例**达成同等的
   「按需展开」效果，工具化留待下一轮，且必须单开一份 spec 处理那条循环契约。
2. **回测侧读经验**。理由：回测的成本口径（`costBasis`）与实盘不同，把实盘经验喂进回测会让回测
   结论无法与历史回测对比；且回测跑得快，会在几分钟内生成上万条事件淹没资产。v1 只做实盘。
   `origin` 列与 `write` 开关已为下一轮铺好路。
3. **向量检索 / embedding 语义相似**。理由：会引入新的三方依赖与模型调用，而闭合词表 + 图结构
   已足够精准；语义相似留给「同义经验合并」这个后续问题。
4. **多经验库（book）与团队共享**。理由：当前是单机桌面应用，无多用户模型；导入导出已提供
   资产的可携带性。
5. **经验对门禁的影响**（例如「这条铁律说不要在高波动追突破 ⇒ 直接拦截」）。理由：I-1。
   这类能力应当走已有的门禁体系（`base_rules` / `edge_gate`）并经独立评审，而不是让一段 LLM
   产出的文本获得下单否决权。
6. **经验的自动 A/B 归因**（「开了经验模式赚得更多吗」）。已落 `aiStats.experience` 的观测键，
   分臂对账留给下一轮的盈利体检面板。
7. **续跑接管持仓与离线平仓的经验补采**〔评审 P1-8，本轮新增的明确欠账〕。`resume.settle_closed_offline`
   与 `adopt_position` 两条路径不经 `_dispatch`、也不建开仓断面，故它们的往返在 v1 采不到。
   **不做的理由**：补采要么得复刻一份开仓语境（猜出来的语境比没有语境更坏），要么得在续跑链上
   新开一条采集通路（那条链跑在真实资金上，收益不抵风险）。**v1 的处置是如实计数并露出**
   （`harvestGap`）。v2 的正确做法是给 `adopt_position` 建一个 `origin="adopted"` 的降级签名并在
   经验节点上标注来源，而不是假装它与正常开仓同质——`origin` 列已为此留好位置。

---

## 十二、实施顺序建议（节点 #2 的推进次序）

1. **P0 · 骨架与真源**：`taxonomy.py`（**词表逐字对齐 `WAKE_KINDS` / `EXIT_SOURCES` / regime 三组**，
   评审 P1-1）→ `models.py` 三表 → `db/ai_experience_repositories.py`（**含单点 upsert 函数**，
   评审 P0-2）→ `attribution.last_settled()` + `cost_runtime.last_settled_trip()`（评审 P0-3）
   → `events.py` → `runtime.note_open/note_close/persist_pending` → `session._dispatch` 两处 hook
   （**close 侧必须挂在 `_inject_explain_meta` 之后**）。
   此步完成后：**经验已经在被真实采集**，即使还没有任何读点。先把收据保住。
   验收：A-1 / A-2 / A-3 / A-29 / A-30。
2. **P1 · 统计与图**：`aggregate.py` → `promote.py` → `graph.py` → 物化与重建 → `service.rebuild`。
   可用 `POST /api/experience/rebuild` + `GET /api/experience/graph` 单独验证，无需前端。
3. **P2 · 检索与披露**：`retrieval.py` → `disclosure.py` → `prompts.py` 三个插入点 →
   `runtime.inject`（**接在 `_wake.inject` 之后**，评审 P1-3）→ G-2 零回归门禁 + G-6 源码扫描。
   **这一步必须先绿 G-2 再继续。** 验收：A-6～A-10 / A-31 / A-32。
4. **P3 · 启动开关**：`rules.py` → `schemas.py` → `api/ai_auto.py` 接线 → `manager` 透传 →
   续跑路径复核（A-18/A-19）。
5. **P4 · 蒸馏**：`distill.py` → **manager 侧后台任务 + app 关停链的有界等待**接线
   （**不是** `stop()` 接线，评审 P0-1）→ A-13 / A-14 / A-26，且 **G-5 时延门禁必须先绿**。
6. **P5 · API 与设置**：`api/experience.py` 11 端点 → `app_settings` 两端点 → `main.py`。
7. **P6 · 前端**：`lib/*` 与纯函数单测先行 → 设置中心区块（列表视图先于图谱视图，保证无障碍
   等价形态不是事后补的）→ 图谱 → 检索预演 → 启动弹窗卡片 → 运行中卡片与轨迹徽标。
8. **P7 · 收口**：四道门禁 + A-1～A-25 + DoD 第 3 条端到端手测。

> 每一步都可独立回退：P0～P2 之间任一步停下，系统的外部行为与合入前**逐字节相同**（I-2），
> 只是多了一张在后台默默积累的事件表——这正是我们希望的失败模式。

---

## 评审结论

### **有条件通过**

这份设计的骨架是对的，而且对得很扎实：**「事件是 append-only 唯一真源、图是可重建的物化派生物」**
这一条把整个特性的可修复性问题一次性解决了——图算错了不是数据丢了，重算即可；**「经验只进
prompt、不进确定性门禁」**给出了在真实资金上敢默认开启的边界；**「三层披露 + 档位压缩」**是对
需求点名的「上下文爆炸」的正面回答，而不是绕过去；**「闭合词表」**挡住了让模型自己发明坐标轴
这条最容易走上的歪路。§十一「明确不做」六条也写得克制而有理由，尤其是拒绝把经验工具化成
第二个 LLM 工具（那会撞上 `_stream_decision` 的单工具循环契约）。

但 v1 有三处**会让特性静默失效或损坏用户操作路径**的缺陷，它们的共同形状值得单独点出来——
**三条全都是「照着 v1 实现，跑完全绿，功能却不工作，而且没有任何地方会报错」**：

- **P0-1**：45 秒的 LLM 蒸馏挂在 `stop()` 的 await 链上，而 `stop_group` 串行遍历成员、客户端
  超时是 20 秒——**在真实资金的停止/平仓按钮上必然弹出「本次操作结果未知」**。
- **P0-2**：「全量替换 `stat` 子图」会抹掉用户亲手按下的静音（而 409 提示恰恰让用户去按它），
  并让 `iron` 档因符号历史无处存放而**永不可达 ⇒ L0 恒空 ⇒ 整个特性一个字都不喂**。
- **P0-3**：`trip_meta_wire` 的 17 个键里**没有** `fee_known` / `side` / `entry_ts` /
  `edge_error_bps`，而 I-7 的口径闸、§6.1 的列、§4.4 的一整个模板全靠它们——「只读 meta」
  这条纪律本身是对的，只是这一处的 meta 里没有它要的东西。

三条 P0 与 11 条 P1 **已全部在正文改掉**（改动处标注「〔评审 P0-n〕/〔评审 P1-n〕」），
9 条 P2 记录在 §评审记录并已顺手在正文修正。

### 放行条件（节点 #2 必须逐条满足，节点 #3 逐条复核）

1. **G-5 停止时延门禁必须先于蒸馏功能落地**。把 `distill.run` 打成 `sleep(120)` 的桩，
   3 成员分组的 `stop_group` 墙钟耗时必须 < 2 秒。这条用例不绿，蒸馏一行都不许接进去。
2. **物化的单点 upsert 函数是唯一写路径**。编排层不得出现第二份 SQL；A-27（用户意志存活）与
   A-28（`iron` 真能晋升）必须先绿，再做前端。
3. **P2 阶段先绿 G-2 与 G-6 再继续**。G-2 是逐字节零回归（未装配 ⇒ prompt 与合入前完全相同），
   G-6 是两条源码扫描（门禁模块里不得出现 `experience`；`stop`/`pause` 方法体内不得出现
   `distill`）——它们把两条最重要的不变式变成机械判据，而不是靠人记得住。
4. **前端净增预算（§8.3 那张表）与后端 §8.2 同为硬判据**。`lib/aiTradeTypes.ts` +2、
   `lib/aiTrade.ts` +0、`AiTradeLaunchModal.tsx` ≤ +12。做不到就继续下沉，不许放宽阈值。
5. **两个诚实性键必须有渲染落点**：`harvestGap`（§7.3 的灰色注记）与 `graphAsOfTs`
   （§7.1 预演状态行 + §7.3 卡片副标题）。本仓库反复付过学费的一条是「后端算了、类型补了、
   没有渲染落点 = 死数据」——这两个键存在的全部意义就是让产品在两处**不能说满话**的地方
   如实说话，砍掉渲染等于砍掉它们本身。
6. **实施期若发现本文档与源码不符，以源码为准，并把差异回写进本文件的「实施过程发现的方案缺陷」
   一节**（沿用本仓库既有范式）。本次评审已逐条实读并列出 15 项「确认为真」的取证（见 §评审记录
   末尾），节点 #2 不必重复核实那些；但**未列出的任何取证都要自己再读一遍**。

### 未阻断、但建议节点 #2 主动权衡的两点

- **`narrative` 蒸馏在 v1 是否值得做**。它是唯一引入 LLM 不确定性的子系统，也是 R-1（自我强化
  幻觉回路）的唯一来源；而 `stat` 子图在没有它的情况下已经能独立交付需求描述的全部四条
  （存储 / 图检索 / 渐进式披露 / 累积更新）。若 P0～P3 的实测工期吃紧，**优先砍 P4 而不是砍
  P6 的前端可视化**——后者是需求明写的「管理员在设置中能可视化管理」，前者不是。
- **`preview` 与运行中会话的分叉是否该收敛**。本轮给的是「如实披露」（成本最低、绝不撒谎）。
  更彻底的做法是让 `status.experience` 直接下发本会话当轮真实注入的 L0/L1 原文，让预演页在有
  运行中会话时改读那一份——那样「所见即所得」就是字面为真的。代价是 wire 变大，故本轮不强制，
  但如果 §7.3 的轨迹徽标本来就要展开显示原文（它要），这份数据其实已经在路上了。

---

> 评审人：Anthropic 工程团队 · 资深评审（节点 #1）
> 评审方式：逐节对照实读源码，不采信上游文档的取证结论
> 文档版本：**v2**（本次评审产物）· 下一站：节点 #2 开发
