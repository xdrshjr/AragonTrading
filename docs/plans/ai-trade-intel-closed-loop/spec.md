# AI 自动交易 · 情报闭环（ai-trade-intel-closed-loop）

| 项 | 值 |
|---|---|
| 版本 | **v2**（评审已完成：5 P0 / 15 P1 / 12 P2 全部在正文修毕，见《评审记录》与《评审结论》） |
| 承接 | `docs/plans/ai-trade-market-intel/spec.md` v2 —— 情报层 v1 已于 **52f466e**（71 文件，+12260/-257）合入 |
| 一句话 | 让每一条情报**可被引用、可被结算、可被证伪、可被拒绝**：情报从「一段喂给模型的文字」变成「一条有编号、有战绩、有判定、有信源账本的证据」 |
| 影响面 | 后端新增 `ai_auto/intel/` 四个模块（`citation.py` / `attribution.py` / `verify.py` / `service_verify.py`）、`db/ai_intel_citation_repositories.py`、一张新表 `ai_intel_citations`；`ai_intel_items` 补 10 列、`ai_intel_watch` 补 2 列（**须 ALTER patcher**）；`prompts.py` / `decider.py` / `session.py` 各三处薄接线；前端新增 `Intel/` 五个组件与两个页签。**评审追加**：`intel/taxonomy.py`（`IntelLimits` 补 6 项 + `DEGRADE_TEXT` 补 4 码）、`intel/llm.py`（核查预算的第二个 client）、`intel/service.py::INTEL_SETTING_BRIDGE`（8 个新键的读点） |
| DB 迁移 | **有，且必须写**。`ai_intel_items` / `ai_intel_watch` 是 **52f466e 已建的既有表**——`db/session.py` 的 `create_all` **不会**给既有表补列（同文件四个 `_ensure_*_columns` 补丁就是这条规律的成文代价）。新增 `_ensure_ai_intel_columns(conn)` 作为**第五个** patcher；新表 `ai_intel_citations` 走 `create_all`，零迁移 |
| 运维回退 | 三把单键，逐层收窄：`AI_TRADE_INTEL_CITE_ENABLED=false` ⇒ 注入行与决策工具 schema **逐字节**回到 52f466e；`AI_TRADE_INTEL_ATTRIBUTION_ENABLED=false` ⇒ 引用照记、不结算不回写；`AI_TRADE_INTEL_VERIFY_ENABLED=false` ⇒ 不跑验证轮、不写 verdict。上一轮的 `AI_TRADE_INTEL_ENABLED=false` 总闸仍是最外层 |
| 外部依赖 | 检索源从 1 个扩到 3 个（`tavily` + 新增 `brave` / `serper`，均为 v1 已登记的接入参数）。**仍是单活**：设置里选一个，不做多源扇出（§十 非目标 3） |
| 新增配置 | 17 项（全部 `ai_trade_intel_` 前缀），总数 34 → **51**；`.env.example` 的家族标题必须同步改数 |

---

## 评审记录（v1 → v2，Anthropic 工程评审）

> 评审口径：**每一条取证都在 2026-08-30 的工作树上重新实读过**，不采信文档转抄。
> §二 的 E-1…E-20 复核结论：**E-1 / E-3 / E-5 / E-6 / E-7 / E-8 / E-10 / E-11 / E-12 / E-13 /
> E-14 / E-15 / E-16 / E-17 / E-18 / E-19 / E-20 成立且行号可对上**；**E-2 不准**（`TradeDecision`
> 末位可选字段实为 7 个而非 5 个，见 P2-4）；**E-9 与本文 §5.5 自相矛盾**（见 P2-5）。
> §十二 登记的全部行数复测无误（`config.py` 1228 / `models.py` 994 / `session.py` 3220 /
> `decider.py` 1169 / `prompts.py` 1098 / `gate.py` 611 / `api/ai_intel.py` 276 / `IntelModal.tsx` 426）。
>
> 下表的每一条都已在正文对应小节改毕；**没有一条只写在这张表里**。

### P0（阻断级：不修即不可实现，或修了也必然静默失效）

| # | 结论 | 取证 | 正文修法 |
|---|---|---|---|
| **P0-1** | **轴 B 的「入场归因」在默认订单类型上恒为 0。** §3.2.1 把 `note_open` 写成「把**本轮**待写行的 `role` 改 `entry`」，这假设了「下单即同轮成交」。但 `ai_trade_limit_orders_enabled` **默认 True**，限价开仓单会 resting 若干轮、其成交由盯市 tick 上另一条 `_dispatch` 路径发出——那时本轮待写行早已被 `persist_pending` 冲掉。症状是 `cite_entry_count` 与 `role="entry"` **在默认路径上恒为 0**，而 G-4 只要用市价单跑就必绿（本仓库成文的「用例绿着但分支从没被走到」） | `session.py:2401`（`_execute_decision` 内的 `_dispatch`）与 `session.py:863`（盯市路径的 `_dispatch`）是两条路；`session.py:2402` 紧跟着 `self._cost.claim_estimate(self.executor.resting)`，其实现 `ai_auto/attribution.py:88-101` 的 docstring 逐字写着同一个坑：「只有轮级槽的话，**默认订单类型的每一笔开仓都拿不到估计**」 | §3.2.1 改为**双槽**（轮级待写行 + 按 `clOrdId` 的跨轮认领槽），逐行镜像 `claim_estimate` / `_take_estimate`；`ai_intel_citations` 补 `cl_ord_id` 列（§5.1）；§7.1 增 `test_limit_open_filled_in_a_later_round_still_marks_entry` |
| **P0-2** | **`note_close` 取错持仓序号，结算 UPDATE 恒命中零行。** §3.2.1 只说「把 `seq` 对应的全部已落库行标记为待结算」，没说 `seq` 从哪来。而 `_dispatch` 的 close 分支**第一件事**就是 `self._position_seq += 1`——`note_close` 跑到时它已经是**下一个** epoch。取它 ⇒ `settled_count` / `net_pnl_usd` 恒 0，且**不报任何错** | `session.py:459` 的 `self._position_seq += 1` 在 `session.py:487` 的 `_experience.note_close(...)` **之前**；经验层正因此**不读** `self._position_seq`，而是从 `trip.seq` 取（`experience/runtime.py:246-252` 的 `_take_slot`）；`SettledTrip.seq` 确实存在（`trip_ledger.py:94-97`） | §3.2.1 / §3.2.2 / §5.5 三处一律改成「**seq 只能取 `trip.seq`**」并写成硬约束；§7.1 增 `test_settle_uses_trip_seq_not_session_counter` |
| **P0-3** | **`bump_intel_watch(kind="verify")` 会改写整理轮的节律真值，且核查轮在非默认配置下永远不跑。** 该函数是 `if kind == "collect": … else: row.last_curate_ts = now` 的**二值 catch-all**，`"verify"` 会落进 `else` 并把 `last_curate_ts` 改成核查时刻。同时 `curates_since_verify` 在全仓库**没有任何自增点**——`_verify_due` 的 `curates_since_verify + 1 >= verify_every_n_curates` 在该字段恒 0 时只有默认值 1 才为真；把它调到 2 或 12（配置表明写允许 `le=12`）核查轮**一次都不会跑**，而且不报错 | `db/ai_intel_repositories.py:180-186`；`_persist_curate` 只传 `kind="curate"`（`intel/service.py:354`） | §3.3.6 / §5.3 / §6.1 #23 三处改：分支必须显式三分（`collect` / `curate` / `verify`），`curate` 分支 `curates_since_verify += 1`，`verify` 分支写 `last_verify_ts` 且**只**在 `verify_reset=True` 时清零；§7.1 增 `test_verify_round_does_not_touch_last_curate_ts` 与 `test_verify_every_n_curates_two_fires_on_the_second_curate` |
| **P0-4** | **「我不同意」是一扇旋转门。** §3.3.4 给出的唯一手动出口是 `PATCH clearVerdict` 清 `verdict`；而 §3.3.1 的 P2 优先级判据正是 `verdict == ""`，且 P2 **不看 `verify_count`**（只有 P3 看）。于是用户点完「我不同意」，该条目立刻回到候选池最前面，下一轮把它重新判成 `refuted`——用户的显式意愿被系统静默撤销。这直接违反本文 §1.3 承诺 3 | 本文 §3.3.1 的 P2 行与 §3.3.4 的手动出口列自相矛盾；`user_edited` 已是仓库既有的第二把免死金牌（`gate.py:78-84` 的 `protected`） | §3.3.1 的 P2 追加 `verify_count == 0`，P2/P3 一律追加 `not user_edited`；§3.3.4 明确 `clearVerdict` **不清 `verify_count`** 且置 `user_edited=True`（=「判定锁」）；§7.1 增 `test_cleared_verdict_item_is_not_reverified` |
| **P0-5** | **三把新单键是「假面板」：能改、永远不读、且不报错。** §4.1 #23 把 cite / attribution / verify 三把单键与 `verifyMaxItems` 等 8 个键露给 `PUT /settings/intel`，但该端点的键映射**唯一真源**是 `intel/service.py::INTEL_SETTING_BRIDGE`，而本文的文件清单**没有一处提到要扩它**。不扩 ⇒ 用户在设置卡上关掉核查轮、系统照跑照降级。把安全开关做成假开关是本轮最不可接受的一种失败 | `intel/service.py:521-536`，该表注释逐字写着：「**单一真源**：读端点、写端点与运行时装配点共用它，避免『面板上改得动、系统永远不读、而且不报错』这类假面板」 | §4.1 #23 与 §6.1 #11 写出「扩 `INTEL_SETTING_BRIDGE` 8 行」；§7.1 增 `test_every_new_intel_setting_key_round_trips_through_the_bridge`（**逐键**断言，不是断言「不报错」） |

### P1（必须在实现前修掉）

| # | 结论 · 取证 · 修法 |
|---|---|
| **P1-1** | **`score_item` 拿不到 `cite_score_bonus`。** 签名是 `score_item(item, *, now)`，四个调用点没有一个传 limits。取证：`gate.py:143`；调用点 `disclosure.py:79` / `gate.py:418` / `gate.py:609` / `service.py:392`。**修法**：§3.2.4 改为 `score_item(item, *, now, limits=None)`，`None` ⇒ 不计 bonus、**逐字节回退**；`cite_score_bonus` 进 `IntelLimits` |
| **P1-2** | **`IntelLimits` 没有任何 verify 阈值**，而 §3.3.1 的 `plan_verification(items, *, limits, now)` 要读四个。取证：`taxonomy.py` 的 `IntelLimits` / `limits_from_settings` 是纯核心**唯一**配置读点。**修法**：§3.3.1 与 §十一 明确列出 `IntelLimits` 新增 6 字段与 `limits_from_settings` 的 6 行映射 |
| **P1-3** | **`ai_trade_intel_verify_max_tokens` 无落点。** `run_round(...)` 没有 `max_tokens` 形参，且 `intel/llm.py` 的 docstring 明写**刻意不改 `LLMClient.stream` 的 Protocol**（32 个测试假客户端）；预算只能走构造参，而 `IntelDeps.llm` 只有一个。取证：`intel/agent.py:113-114`；`intel/llm.py:1-13,29-47`。**修法**：§3.3.5 / §6.1 引入 `IntelDeps.verify_llm`（`build_intel_llm_client` 加 `max_tokens_override` keyword-only 形参，缺省即今日行为）；`verify_llm is None` ⇒ 回落 `deps.llm`，**不报错** |
| **P1-4** | **降级码文案表指错了文件。** §3.6 写「登记进 `disclosure`/`service.degrade_wire` 的文案表」，但 `degrade_wire` 只是转发，文案表是 `intel/taxonomy.py::DEGRADE_TEXT`（`taxonomy.py:112-128`）；漏登记的症状是 UI 显示裸码「情报流水线降级（verify_no_corpus）」。**修法**：§3.6 改指向 `taxonomy.DEGRADE_TEXT`，并要求一条**枚举式**用例逐码断言文案不等于兜底串 |
| **P1-5** | **§3.5 的日配额论断与代码不符。** 配额闸是 `daily_used(watch) >= daily_round_cap` 且**只在 `run_collect_round` 里判一次**（`intel/service.py:172`）；`count_intel_rounds_today` 全仓库**零调用点**（`db/ai_intel_repositories.py:570` 只被 `repositories.py` 再导出）。于是核查轮会**吃掉**采集配额（它 bump `day_rounds`），却**自己不受配额约束**——恰好是最坏的组合。**修法**：§3.5 改写为事实，`run_verify_round` 增步骤 1.5 自判同一条闸 |
| **P1-6** | **10 个统计列绝不能进 `item_to_payload` / `_item_values`。** `upsert_intel_items` 的 `on_conflict_do_update` 用 `_item_values` 减 `_PRESERVED_ON_CONFLICT` 求 `update_cols`，那六个保留列里没有任何统计列——统计列一旦进 `_item_values`，同一条情报被再次报道时全部引用战绩会被**清零**且不报错。取证：`db/ai_intel_repositories.py:31-32,312-341,358-364`。**修法**：§3.2.3 / §6.1 #11 加硬约束与 `test_reupsert_does_not_reset_citation_stats` |
| **P1-7** | **`trust` 是第二个「有写点没读点」。** 仓库唯一的 feedback 读点 `list_intel_blocked_domains` 只读 `action == "block"`（`db/ai_intel_repositories.py:580-588`）；而 `solve_confidence`（`gate.py:174`）与 `corpus.build`（`corpus.py:122-142`）都在纯核心里，信任清单却在 DB。就这么发出去，`trust` 会原样复刻本轮要修的那个缺陷。**修法**：§3.4.2 补两处落点——新增 `list_intel_trusted_domains`；`corpus.build(..., trusted_domains=…)` 只在**排序键**上打破并列（不改截断规则）；`_solve_sources` 把 trust 加成作为 `solve_confidence` 的第 6 个 keyword-only 形参传入 |
| **P1-8** | **存量扫除会静默空转。** `run_curate_round` 的只读会话**不查**黑名单（只有 `run_collect_round` 查：`intel/service.py:170` vs `:287`）。若 `plan_cleanup(blocked_domains=…)` 给形参一个 `frozenset()` 默认值，扫除永远算出空集。**修法**：`blocked_domains` 必须是**无默认值的 keyword-only 必填形参**（少传是 `TypeError`，不是静默空转），并在 `run_curate_round` 的只读会话里补一行取数 |
| **P1-9** | **轮级待写行没有异常路径清理点。** 一次在 `note_decision` 之后、`persist_pending` 之前抛错的轮，会把它的引用行留到下一轮，记成下一轮的 `round_no` 与动作。先例逐字可抄：`AttributionLedger.end_round()`（`ai_auto/attribution.py:103-105`，注释「一次被否的开仓意图绝不可改绑到下一笔真实往返上」）挂在 `_run_decision_round` 的 `finally` 上。**修法**：§3.2.1 增第七个方法 `end_round()` 与同一个 `finally` 落点 |
| **P1-10** | **§3.1.1 的段末新增句会一并进反思轮。** `build_reflection_prompt` 第一行就是 `evidence = build_decision_prompt(payload)`（`prompts.py:1001-1005`），情报段（含 `[Ixx]` 前缀与「请在 intelRefs 里写出编号」）原样进复核官上下文，而复核官的工具 `submit_trade_reflection` **没有** `intelRefs`。**修法**：§3.1.1 / §3.8 明确承认这条通路——(a) 段末句改为不含祈使的中性口径；(b) G-1 的字节冻结**同时覆盖** `build_reflection_prompt`；(c) 用例断言反思工具 schema 逐字节不变 |
| **P1-11** | **核查候选的渲染会自己反驳自己的硬约束。** §3.3.3 写「不得因为一条情报的方向（利多 / 利空）而倾向任何判定」，而候选清单若沿用 `render_listing` 就会带上 `stance` / `impact` / `confidence` / `tier`——把方向摆在核查员眼前再要求他别看方向。**修法**：§3.3.3 明确候选渲染**只给** `C 编号 + headline + summary + 事件时刻`，并用源码扫描用例冻结 |
| **P1-12** | **§3.3.4 与自己的 R-3 缓解③ 矛盾。** R-3③ 写「`refuted` 只降一层、不直接删」，§3.3.4 却写「已在 `ultra` ⇒ `status="archived"`」。按 §3.3.4 实现，一次模型判定就能归档一条情报，而 R-3 正把「一次错误的 refuted」列为本轮**最危险**的失效模式。**修法**：§3.3.4 改为**两击制**——首次 `refuted` 只写 verdict + 罚置信度 + **立即停止注入**，不动 tier / 不动 status；只有在 `verify_recheck_h` 之后被**独立复核第二次**仍判 `refuted` 才降一层；**任何情形都不因 refuted 归档** |
| **P1-13** | **升级即生效，没有观察窗。** verify 默认开 + `verify_every_n_curates=1` ⇒ 升级后约 2 小时内，每个在跑标的开始自动改写存量情报的层级。**修法**：由 P1-12 的两击制天然兑现——第一轮核查**不产生任何层级变更**，只产生 verdict 与「停止注入」。既是观察窗，又不新增任何配置项 |
| **P1-14** | **`refuted` 条目进不了重核队列。** §3.3.1 的 P3 只收 `verdict == "unresolved"`，两击制要求 `refuted` 也能被第二次核查。**修法**：P3 判据改为 `verdict in ("unresolved", "refuted")` |
| **P1-15** | **`build_snapshot` 没说清 `cite` 与 `refuted` 过滤从哪来**（`intel/service.py:456-472`）。`build_digest` 是唯一定稿点，两者都必须在它内部完成，否则 REST 读路径与注入路径会分叉。**修法**：§3.1.1 / §3.3.4 明确 `build_snapshot` 传 `cite=bool(settings.ai_trade_intel_cite_enabled)`，`refuted` 过滤与 `status == "active"` 写在同一行判据里 |

### P2（本轮一并改掉，不留待办）

| # | 结论 | 修法 |
|---|---|---|
| **P2-1** | `AiTradeSession.run_id` 是 `int`（`session.py:206,221`），§5.1 却声明 `String(48)` | 改 `Integer`，索引同改 |
| **P2-2** | `refmap` 整个缺席（未注入 / cite 关）时把全部 token 记成 `fabricated_ref`，会把「这一轮没有编号表」污染成「模型在编造」 | 分成 `fabricated_ref` 与 `ref_without_digest` 两个码 |
| **P2-3** | §3.7.2 的 `✕` 若做成 chip 的**子节点**就是 `<a>` 里套交互元素 | 明确写成 `<a>` 的**兄弟**节点 |
| **P2-4** | E-2 说「已有 5 个同形的可选字段」，实读是 **7 个**（另有 `limit_px` / `exit_evidence`） | E-2 更正 |
| **P2-5** | E-9 写「轴 D **不需要写任何仓库函数**」，§5.5 却新增了两个（P1-7 之后是三个） | E-9 更正为「不需要改任何**既有**仓库函数」 |
| **P2-6** | `AiIntelRound` 的 docstring 与 `kind` 行内注释仍写「collect\|curate」 | §5.6 要求同步（`String(8)` 容得下 `verify`，无须改类型） |
| **P2-7** | §3.3.5 步骤 7 的 `runtime.refresh(inst_id)` 与 `_run → _after_round` 的刷新**重复**（`runtime.py:163-166`） | 删掉显式刷新，改成一句「刷新由 `_after_round` 承担，此处不得再刷一次」 |
| **P2-8** | §十二 对 `api/ai_intel.py` 的行数预估偏乐观（现 276 行 / 9 端点 ≈ 30 行/端点；11 个新端点 ⇒ ≈585 而非 470） | §十二 更正为 ≈585 并保留预警 |
| **P2-9** | `aiStats.intel` 的合并落点未写死 | 明确「合并落在**调用点** `_build_run_metrics`，`_compute_ai_stats` 是模块级函数、一字不动」（先例 `session.py:3099` 的 `self._cost.merge_into`） |
| **P2-10** | `D[0-9]{1,2}` 的上限是 99 篇，与 `corpus_max_docs` 的值域没有成文关系 | §3.3.3 加值域约束与启动期断言 |
| **P2-11** | §4.1 #21 说它是「用户手写条目的**唯一**验证途径」，但 §3.3.1 的候选池过滤 `origin == "agent"` | #21 明确**绕过**候选过滤（它是显式的人类指令） |
| **P2-12** | 停机时未落盘的引用行没有说明 | §3.2.1 明确：按 `dropped` 计并放弃，**不新增关停步骤**（`_shutdown_steps` 第 7 步已被用例冻结） |

---

## 一、概述

### 1.1 上一轮交付了什么，这一轮补什么

52f466e 交付的情报层已经能做四件事：一个**独立于交易 Agent** 的情报 Agent 定期为每个自动
交易标的做后端检索、把网页素材摘成几句话的短条目、过六道服务端闸之后落进三层（超短 / 中期 /
长期）× 六类（市场 / 情绪 / 宏观 / 链上 / 项目 / 风险）的持久化情报库；一个整理轮定期回看整座
库，自主提议升层、合并与清理，服务端逐条复核；情报以定稿文本注入交易决策 prompt；用户在标的
卡片的 hover 按钮里打开弹窗，可以增删改查、置顶、静音、恢复、看审计时间线。

它做不到的，是三件在真实资金上迟早会被问出口的事：

1. **「模型到底有没有用这条情报？」** 情报以匿名文本行注入，模型读没读、采信了哪条，
   全链路没有任何一处能回答。`ai_intel_rounds` 记的是「我采了几条」，不是「它用了几条」。
2. **「用了之后赚钱了吗？」** 上一轮的《遗留待办 2》已经如实登记：「注入是否正向，本轮无法
   离线证伪」，缓解手段只有「关注入跑一次对照」。一个只能靠 A/B 才知道有没有用的模块，
   在它自己的界面上应该至少能显示**它被引用过多少次、那些引用的往返净额是多少**。
3. **「这条情报还是真的吗？」** 情报入库时过了六道闸（引用编号、数字接地、来源独立、时效、
   去重、内容形态），但闸判的是**它当时是不是被如实转述**，不是**它后来是不是被证实**。
   一条「某链下周主网升级」的情报，在升级取消之后仍然会在长期层里躺 400 天，并每一轮都被
   喂进交易 prompt。这是本特性 v1 最大的一处**信任缺口**：入口有闸，出口没有。

再加上一处 v1 自己登记为待办的**静默空转**：`ai_intel_feedback` 表有读点（采集轮的域名过滤器
`service.py:174`）却**没有任何写点**——12 个 REST 端点里没有一个能写它，前端也没有「不再采纳
此源」入口。于是那张表恒为空、那道过滤器恒空转，而**这件事不会报任何错**。

本轮把这四件事一次做完，并顺手兑现 v1 登记的另一条待办（`brave` / `serper` 适配器）。

### 1.2 四条轴

| 轴 | 名字 | 一句话 | 用户能看见什么 |
|---|---|---|---|
| **A** | 可引用 | 注入行带稳定编号 `[I01]`，决策工具露出可选 `intelRefs`，服务端按本轮编号表裁决，编造的编号被丢弃并计数 | 决策卡上出现「本轮采信了哪几条情报」 |
| **B** | 可结算 | 开仓引用冻结进持仓周期，平仓时用**既有**往返结算把净额回写到每一条被引用的情报 | 情报卡上出现「引用 12 次 · 入场 5 · 胜 3 · 净额 +18.4 USDT」；弹窗新增「影响力」页签 |
| **C** | 可证伪 | 新增**验证轮**：为中期 / 长期条目重新检索语料，让情报 Agent 判 `confirmed` / `refuted` / `unresolved`，**判定必须引用检索编号**，服务端据判定做确定性后果（**首击只停注入、复核第二击才降层、任何情形都不归档**，评审 P1-12） | 情报卡上出现「已证实 / 已证伪 / 待验证」徽章与一句话理由；被证伪的条目**立即停止注入** |
| **D** | 可拒绝 | 兑现 `ai_intel_feedback` 的写点：域名级 `block` / `trust`，前端来源 chip 上一键「不再采纳此源」，弹窗新增「信源」页签；并补 `brave` / `serper` 两个适配器 | 用户能把一个天天出软文的域名永久拉黑，且**存量条目会被扫掉**，不只是以后不再采 |

四条轴不是四个独立特性，它们是同一条闭环的四段：
**采集（v1）→ 引用（A）→ 结算（B）→ 证伪（C）→ 拒绝（D）→ 反哺采集与整理（A/B/C/D 全部回流）**。

### 1.3 三个设计承诺

1. **模型只能引用，不能创造。** 这条承诺在 v1 已经用「后端持有语料 + 模型只能写 `D1..Dn`」
   兑现过一次；本轮把同一条纪律**原样复用两次**：交易 Agent 只能写 `I01..I20`（编号表由服务端
   在定稿注入行时生成），验证 Agent 只能写 `D1..Dn`（语料编号，与采集轮同一套 `corpus.build`）。
   一个编号在表里查不到，就是一次 `fabricated_ref`，进直方图、不进业务。
2. **归因只读，绝不成为门禁。** 引用数、胜率、净额、verdict 全部是**观测量**：它们可以改变
   情报在注入段里的**排序**、可以改变它在整理轮里被淘汰的**优先级**、可以决定它是否**继续
   被注入**，但**不产生任何 degrade 码、不改写任何 `TradeDecision` 字段、不参与任何仓位或
   风控判定**。理由是 v1 评审留下的那条：情报是加法流水线，它的任何失败都必须收敛成降级码，
   而不是收敛成一笔没做成或做错方向的真实交易。
3. **每一条自动后果都必须有对应的手动出口。** 被自动降级的条目能被用户升回去（`user_edited`
   免死金牌已经存在）；被自动拉黑的域名能被解除；被证伪的条目能被用户标为「我不同意」并
   恢复注入。v1 终审刚修过一条同形缺陷（RV-2：静音是一条没有回头路的单行道），本轮的每一个
   新增自动动作都必须先回答「用户怎么撤销它」。

### 1.4 验收总闸

| 编号 | 判据 | 失败即不可交付 |
|---|---|---|
| **G-1** | `AI_TRADE_INTEL_CITE_ENABLED=false` 时，`build_decision_tool_schema(...)` 返回值与 52f466e **对象恒等**（`is DECISION_TOOL_SCHEMA`，其余总闸同为默认），且 `disclosure.render_line`、`build_decision_prompt`、**`build_reflection_prompt`** 三者的输出 sha256 各自与冻结值相等（第三个是 P1-10：反思轮的证据段就是 `build_decision_prompt(payload)`，只冻前两个会漏掉复核官那一半） | 是 |
| **G-2** | 开启 cite 后，注入段每一行以 `[Ixx] ` 开头，`xx` 从 `01` 连续递增、与 `wire["refs"]` 的键**一一对应且无空洞** | 是 |
| **G-3** | 模型写了一个编号表里不存在的 `I99` ⇒ 该 token 被丢弃、`fabricated_ref` 计数 +1、`TradeDecision` 的其余字段**逐字段相等**于未写它的那一次 | 是 |
| **G-4** | 一次「开仓 → 持有两轮 → 平仓」之后，开仓轮引用的每一条情报的 `settled_count` +1、`net_pnl_usd` 增量**等于** `trip.net_pnl_usd`（与 `explain.meta` 里那一份同源，故两边都可断言）；持有轮引用的条目 `cite_count` +1 而 `settled_count` **不变**。**结算的 seq 只能取 `trip.seq`**（P0-2） | 是 |
| **G-5** | `AI_TRADE_INTEL_ATTRIBUTION_ENABLED=false` ⇒ 平仓不写任何 citation 结算、`ai_intel_items` 的六个统计列**一个字节都不变** | 是 |
| **G-6** | 核查轮**首次**把一条 `long` 条目判 `refuted` ⇒ `verdict="refuted"`、`confidence` 乘以惩罚系数、**`tier` 与 `status` 一个字节都不动**，且**下一次 `build_digest` 不再包含它**（跨轮断言，不是同轮断言）；`verify_recheck_h` 之后**第二次**仍判 `refuted` ⇒ 才降为 `mid` 并同事务重算 `expires_ts`（两击制，P1-12/P1-13） | 是 |
| **G-7** | 验证 Agent 给出一个**不带任何 `D` 引用**的 `refuted` ⇒ 服务端把它降为 `unresolved`、计 `verdict_no_ref`、**不执行任何降级** | 是 |
| **G-8** | 被 `pinned` 或 `user_edited` 的条目被判 `refuted` ⇒ **不降级、不归档**，但仍**停止注入**并在卡片上显示证伪徽章 | 是 |
| **G-9** | 把某域名 `block` 之后：①下一次采集轮的语料里没有该域名；②**存量**条目中「全部来源都属于被拉黑域名」的那些被归档、理由 `source_blocked`；③解除拉黑后归档的那些**不会**自动复活（只能手动恢复，见 §3.4.2） | 是 |
| **G-10** | `ai_intel_items` / `ai_intel_watch` 的新列在**一个 52f466e 时代建的老库**上首次启动即被 `_ensure_ai_intel_columns` 补齐，读写不抛 `no such column` | 是 |
| **G-11** | 新增 17 项配置在 `config.py`、`.env.example`、`desktop/src/lib/config.js` 前缀白名单三处**一一对齐**（前缀已是 `AI_TRADE_`，白名单无须改，但必须有用例证明这一点） | 是 |
| **G-12** | 三把单键任一关闭时，全链路无异常、无空指针、UI 显示对应的**诚实文案**而不是空白（沿用 v1「五种失败态五段不同文案」的纪律） | 是 |
| **G-13** | 引用行的保留期到期后被裁剪，`ai_intel_citations` 不会无界增长；裁剪**只删明细、不回滚统计列** | 否（P1） |
| **G-14** | `brave` / `serper` 各自的时间字段解析（`page_age` / 相对文案 `"3 days ago"`）有离线用例，解析失败 ⇒ `published_ts=0` ⇒ 走 `undated` 路径，**绝不**把「解析不出来」写成「刚刚发布」 | 是 |
| **G-15** | **限价开仓**（`ai_trade_limit_orders_enabled` 默认 True）在第 N 轮下单、第 N+3 轮成交之后，第 N 轮引用的条目 `cite_entry_count` 仍 +1、`role` 仍是 `entry`（P0-1；这条用例**必须走 resting 通路**，用市价单跑不算数） | 是 |
| **G-16** | `AI_TRADE_INTEL_VERIFY_EVERY_N_CURATES=2` 时，第一次整理轮**不**跑核查、第二次整理轮**跑**核查；核查轮跑完之后 `last_curate_ts` 与跑之前**逐字节相等**（P0-3） | 是 |
| **G-17** | 用户对一条被判 `refuted` 的条目点「我不同意」之后，**连跑三轮核查它都不再进候选池**；`verify_count` 不被清零（P0-4） | 是 |
| **G-18** | 8 个新设置键经 `PUT /settings/intel` 写入后，`resolve_intel_settings` 产出的会话副本上**逐键**取到新值（不是断言「请求不报错」，P0-5） | 是 |
| **G-19** | 四个新降级码在 `taxonomy.DEGRADE_TEXT` 里**逐个**有中文文案（断言 `degrade_message(code)` 不等于兜底串 `f"情报流水线降级（{code}）"`，P1-4） | 是 |
| **G-20** | 同一条情报被再次报道走 `upsert_intel_items` 的冲突分支之后，六个统计列与四个判定列**一个字节都不变**（P1-6） | 是 |

---

## 二、现状取证

> 下面每一条都是**实读**结果（`文件:行` 为 2026-08-30 工作树状态），不是从上一轮文档转抄的。
> 设计里凡是「照着既有先例做」的地方，先例都在这张表里。

| 编号 | 事实 | 位置 | 对本设计的意义 |
|---|---|---|---|
| **E-1** | `build_decision_tool_schema` 已经是「总闸 + keyword-only + 末位可选属性」的成熟范式：早退判据把**每一个**总闸都列进 `if`，漏一个就会「该总闸开着时 schema 原样返回模块常量、新字段永远露不出来、而不报任何错」（原文注释 `【R:P1-9a】`） | `prompts.py:321-400` | 轴 A 的 `allow_intel_refs` **必须**加进那条早退判据；这是 G-1 的直接依赖 |
| **E-2** | `TradeDecision` 是末位带默认值的 dataclass，已有 **7** 个同形的可选字段（`ord_type` / `limit_px` / `hold_exit_px` / `exit_conviction` / `exit_evidence` / `evidence_digest` / `contrarian_evidence`；v1 原文写 5 个，评审 P2-4 更正），每个都有一段「值域外一律忽略、绝不据它降级」的成文纪律 | `decider.py:188-222` | `intel_refs: tuple[str, ...] = ()` 照抄这个位置与这条纪律 |
| **E-3** | `_resolve_evidence_digest` / `_resolve_contrarian_evidence` / `_resolve_exit_intent` 三个解析器共用同一形状：总闸关 ⇒ `None`；类型错 ⇒ 忽略；**且必须显式判 action 作用域**，否则残留值会泄漏到别的动作上 | `decider.py:793-830` | `_resolve_intel_refs` 照抄；但它**不限定 action**——hold 轮的引用同样要记（那正是轴 B 区分 `entry`/`hold` 的前提） |
| **E-4** | 决策轮里有一个明确的「模型真实意愿」留存点：`stage1_decision = decision`，在**任何门禁之前**。八道门禁之后的 `decision` 已经可能被改写成 `hold` | `session.py:1599` | 引用必须取自 `stage1_decision`；取门禁后的那一个会让「模型采信了情报却被冷却门禁拦下」变成「模型没采信情报」 |
| **E-5** | 成交事件里已有一对成熟的开 / 平钩子：`_experience.note_open(trade, seq=self._position_seq, facts=…)` 在开仓分支，`_experience.note_close(trade, meta, self._cost.last_settled_trip())` + `await persist_pending()` 在平仓分支，且平仓钩子**必须排在 `_inject_explain_meta` 之后**（`exitSource` 与往返结算在那一步才写进 meta） | `session.py:477-495` | 轴 B 的 `note_open` / `note_close` **逐行镜像**这两处，连「必须排在 `_inject_explain_meta` 之后」这条约束一起继承 |
| **E-6** | 往返净额的唯一产出点是 `CostRuntime.apply_trip_meta`（把 `settle_close` 的 wire `setdefault` 进 `explain.meta`），只读出口是 `last_settled_trip()`，且注释明写「**必须在 `apply_trip_meta` 之后调用**，否则拿到的是上一笔」 | `cost_runtime.py:512-540` | 轴 B **绝不自己算净额**；它只消费 `last_settled_trip()`。自己再算一次就是造第二个结算现场 |
| **E-7** | `create_all` 建缺失的表但**不给既有表补列**，仓库已有四个 `_ensure_*_columns` 补丁，统一形状是「`PRAGMA table_info` → 求差集 → `ALTER TABLE ADD COLUMN`」，并在 `init_db` 里顺序调用 | `db/session.py:95-98,106-129` | `ai_intel_items` / `ai_intel_watch` 补列**只能**走第五个 patcher；这是 G-10 |
| **E-8** | `ai_intel_items` 已有 30 列、三个索引与 `UniqueConstraint(inst_id, dedup_key)`（跨全部 status）；`ai_intel_watch` 以 `inst_id` 为主键、一行一标的 | `db/models.py:870-982` | 新列**只能追加**，不得改既有列类型；新表 `ai_intel_citations` 的外键语义用普通整型列 + 索引表达（仓库既有风格，不用 ForeignKey 约束） |
| **E-9** | `AiIntelFeedback` 表存在、`set_intel_feedback` / `clear_intel_feedback` / `list_intel_blocked_domains` 三个仓库函数存在并已从 `db/repositories.py` 再导出，采集轮已在 `service.py:170` 读它。**但 `list_intel_blocked_domains` 只读 `action == "block"`——`trust` 在全仓库没有任何读点** | `db/ai_intel_repositories.py:580-609`；`db/repositories.py:1080-1104` | 轴 D **不改任何既有仓库函数**（v1 原文写「不需要写任何仓库函数」，评审 P2-5 更正），但必须**新增** `list_intel_feedback` / `count_items_all_sources_blocked` / `list_intel_trusted_domains` 三个读函数——最后一个是 P1-7 的直接依赖 |
| **E-10** | `resolve_search_provider` 已经把 `brave`（`X-Subscription-Token` / `freshness=pd\|pw\|pm` / `description` / `page_age`）与 `serper`（`X-API-KEY` / `tbs=qdr:d\|w\|m` / `snippet` / `date`）的接入参数登记在模块 docstring 里，`PLANNED_SLUGS` 就是它们；未实现的 slug 走 `logger.info` + 按未配置处理 | `intel/search.py:1-13,38-43,245-262` | 轴 D 只需在 `PROVIDER_SLUGS` 里放行并加两个 `__init__`/`_body`/`_parse` 三件套；`_Breaker` / `SearchRunner` 一行不改 |
| **E-11** | `_Breaker` 刻意**不复用** OKX 的那一个（形状照抄、状态不共享），理由写在 docstring 里 | `intel/search.py:116-158` | 每个 provider 各自持一个 breaker 的做法已经成立；本轮不改这条 |
| **E-12** | `run_curate_round` 的形状是「模型提议 → `gate.review_actions` 服务端复核 → 执行 → `gate.plan_cleanup` 确定性清理 → 一次提交」，且注释明写「确定性清理跑在模型动作执行**之后**，用动作前的快照算清理计划会让一条刚升进中期的情报仍按超短层参与溢出淘汰」 | `intel/service.py:279-360` | 验证轮**排在整理轮之前**（先定 verdict 再整理），否则整理轮会拿着旧 verdict 做升降级判断——与上面那条注释同形的错 |
| **E-13** | 调度是「每分组一个 tick 循环、一个 tick 至多推进一个标的一轮」，整理轮的触发判据是 `collectsSinceCurate + 1 >= curate_every_n_collects` | `intel/scheduler.py:126-146` | 验证轮挂在**整理轮触发的同一个判据里、紧邻其前**；不新建循环、不新建任务集 |
| **E-14** | `build_digest` 在采集 / 整理轮结束时把每一行**定稿成字符串**，理由是 `commit_round` 会把 `build_decision_prompt(payload)` **再渲染一次**塞进 `_context`，两次渲染之间任何状态漂移都会让上下文里那一份与模型看到的那一份不一致 | `intel/disclosure.py:19-27,101-127` | 编号表**必须**与 `lines` 在同一次 `build_digest` 里定稿并一起进 wire；在 `format_intel` 里现场编号会立刻违反这条 |
| **E-15** | `intel_digest_for` 是**同步、读进程内缓存、不触 DB**，未装配 / 零条目 / 尚未成功采集过 ⇒ `None` ⇒ payload 整键缺席 ⇒ prompt 逐字节回退 | `intel/runtime.py:200-212` | 轴 A 的编号表随 digest 一起进 payload；session 侧**不得**为了拿编号表再查一次库 |
| **E-16** | `intel_item_out` 是后端**唯一**的条目 wire 装配点（读端点与 WS 共用），前端 `intelTypes.ts` 与它逐键同构，且注释明写「改这里必须同时改那里」 | `db/ai_intel_repositories.py:57-89`；`apps/web/lib/intelTypes.ts:1-4` | 10 个新列的 wire 键只在这一个函数里加；前端类型同 PR 改 |
| **E-17** | `IntelSourceChips` 已经在做「只显示可注册域名、`rel="noopener noreferrer"`、非 http(s) 不渲染成链接」三件事 | `apps/web/components/Intel/IntelSourceChips.tsx` | 「不再采纳此源」的入口挂在这个组件上，域名已经在手边 |
| **E-18** | 情报按钮是 `AiTradeMemberRow` 的**兄弟节点**而不是子节点（行组件本体是 `<button>`，`aiTradePanelNoNestedButton.source.test.ts` 冻结这条约束） | `apps/web/components/Chart/AiTradeIntelButton.tsx:15-21` | 本轮不动这条布局；新增的徽章一律画在弹窗内，不再往那个按钮上堆 |
| **E-19** | `.env.example` 在**仓库根**（不在 `services/quant/`），情报家族的标题行写着「34 项」 | `.env.example:681` | 加 17 项时**必须**同步改这个数字；v1 评审的 P1-9 正是栽在这个文件上 |
| **E-21** | `bump_intel_watch` 的 kind 分支是**二值 catch-all**：`if kind == "collect": … else: row.last_curate_ts = now`。第三种 kind 会静默落进 `else` | `db/ai_intel_repositories.py:180-186` | P0-3：分支必须显式三分，且 `curates_since_verify` 必须有一个自增点 |
| **E-22** | 日配额闸是 `daily_used(watch) >= ai_trade_intel_daily_round_cap`，**只在 `run_collect_round` 判一次**；`count_intel_rounds_today` 全仓库零调用点 | `intel/service.py:172`；`db/ai_intel_repositories.py:570` | P1-5：核查轮必须自己判一次同一条闸，否则它吃配额却不受配额约束 |
| **E-23** | `INTEL_SETTING_BRIDGE` 是 `GET/PUT /settings/intel` 的**唯一**键映射真源，注释逐字写着「避免『面板上改得动、系统永远不读、而且不报错』这类假面板」 | `intel/service.py:521-536` | P0-5：8 个新键必须扩这张表，否则三把安全单键全是假开关 |
| **E-24** | `AttributionLedger.claim_estimate(cl_ord_id)` / `_take_estimate(trade)` 是「跨轮成交认领」的成文先例，其 docstring 明写「限价开仓单会 resting 若干轮……只有轮级槽的话，**默认订单类型的每一笔开仓都拿不到估计**」；容量上限 `PENDING_ESTIMATE_MAX`，超量 FIFO | `ai_auto/attribution.py:88-115` | P0-1：轴 B 的 entry 绑定逐行照抄这一对方法，不再发明第二套 |
| **E-25** | `_dispatch` 的 close 分支**第一件事**就是 `self._position_seq += 1`；经验层因此从 `trip.seq` 取 epoch 而不是读会话计数器 | `session.py:459`；`experience/runtime.py:246-252` | P0-2：结算 seq 只能取 `trip.seq` |
| **E-26** | `score_item(item, *, now)` 没有 limits 形参；四个调用点分别在 `disclosure.py:79` / `gate.py:418` / `gate.py:609` / `service.py:392` | `gate.py:143-154` | P1-1：`cite_bonus` 只能经一个**带默认值**的新 keyword-only 形参进来 |
| **E-27** | `run_round(llm, *, system, user, tool_name, tool_description, tool_schema, timeout_s)` 没有 `max_tokens`；输出预算住在 `build_intel_llm_client` 的构造参里，且 `intel/llm.py` 明写**刻意不改 `LLMClient.stream` 的 Protocol** | `intel/agent.py:113-114`；`intel/llm.py:1-13,29-47` | P1-3：`verify_max_tokens` 只能走第二个 client |
| **E-28** | `build_reflection_prompt` 的第一行是 `evidence = build_decision_prompt(payload)`——情报段原样进复核官上下文，而复核官的工具没有 `intelRefs` | `prompts.py:1001-1005` | P1-10：段末文案必须中性，G-1 的字节冻结必须覆盖这一条通路 |
| **E-20** | `.claude-index/config.md` 的《显式记录的阈值偏离》已登记 `config.py` 1229 行、`models.py` 994 行、`session.py` 3220 行、`manager.py` 2032 行、`prompts.py` 1098 行，并对每一条写了「下一次触碰应如何还债」 | `.claude-index/config.md:171-200` | §十二 必须逐条更新这几行的复测值，并**新登记** `api/ai_intel.py` 的越线风险 |

---

## 三、技术设计

### 3.0 架构与数据流

```
                            ┌──────────────── 情报 Agent（v1，未改） ────────────────┐
   检索源 tavily/brave/serper │  采集轮 run_collect_round → 六道闸 → ai_intel_items    │
   ─────────────────────────┤  整理轮 run_curate_round  → 复核表 → 升/降/合/清        │
                            └──────────────────────────────────────────────────────┘
                                                    │
                              ┌─────────────────────┼─────────────────────┐
                              ▼                     ▼                     ▼
                    ①【轴 C】验证轮           ②【轴 A】定稿注入        ③【轴 D】信源治理
                    run_verify_round          build_digest             ai_intel_feedback
                    ├ 选candidates(纯核心)     ├ lines[] 带 [Ixx]        ├ block/trust 写点
                    ├ 重新检索语料            └ refs{Ixx→itemId}        ├ 语料过滤(v1 已有)
                    ├ Agent 判 verdict                │                 └ 存量扫除(新)
                    └ 服务端确定性后果                 │
                              │                       ▼
                              │            交易决策 prompt（情报段）
                              │                       │
                              │                       ▼
                              │            LLM → submit_trade_decision
                              │                  { …, intelRefs:["I01","I03"] }
                              │                       │
                              │                       ▼
                              │            ④【轴 A】服务端裁决 resolve_citations
                              │                  合法 → item_id 列表；非法 → fabricated_ref
                              │                       │
                              │                       ▼
                              │            ⑤【轴 B】IntelAttribution
                              │              note_decision → 内存待写行
                              │              note_open(seq) → role=entry，绑定持仓周期
                              │              note_close(trip) → 用**既有**往返净额结算
                              │                       │
                              ▼                       ▼
                    ai_intel_items.verdict     ai_intel_citations（新表）
                              └───────────┬───────────┘
                                          ▼
                          ⑥ 反哺：注入排序 / 整理复核 / 溢出淘汰 / 影响力页签
```

**四条不可越过的方向**（任何实现细节与它们冲突时，改实现）：

1. **热路径不查库。** 决策 payload 的 `intel` 键仍然只读 `runtime` 的进程内快照（E-15）。
   编号表随快照一起来，`session` 不得为了拿它再开一次 DB 会话。
2. **净额只有一个产出点。** 轴 B 消费 `CostRuntime.last_settled_trip()`（E-6），
   **绝不**自己按成交价再算一次。自己算的那一份必然与成本记账口径分叉，而分叉不会报错。
3. **模型只能引用编号。** 交易 Agent 写 `I##`，验证 Agent 写 `D##`；两张表都由服务端在同一次
   定稿里生成，查不到的编号一律丢弃并计数（G-3 / G-7）。
4. **归因不产生 degrade。** 轴 A/B/C/D 的任何异常都收敛成「情报段少几条」「统计列没更新」
   「verdict 停留在 `unresolved`」，**绝不**收敛成一个改变 `TradeDecision` 的降级码。

### 3.1 轴 A：可引用的情报

#### 3.1.1 编号契约（`intel/disclosure.py`）

`build_digest` 新增 keyword-only 形参 `cite: bool = False`（默认 False ⇒ G-1 逐字节回退）。
**取值只有一个来源**（评审 P1-15）：`build_snapshot` 传 `cite=bool(settings.ai_trade_intel_cite_enabled)`。
`build_digest` 是**唯一定稿点**，因此 `verdict == "refuted"` 的过滤也只能写在它内部，与既有的
`it.status == "active"` **同一行判据**——写在 `format_intel` 或读端点上会让 REST 看到的库存与
注入看到的库存分叉，而分叉不报错。

开启时：

- 注入排序不变（长期 → 中期 → 超短，同层按 `score_item` 降序，见 §3.2.4 的新增项）。
- 第 k 条（k 从 1 计）的定稿行由 `render_line(item, now=now, ref="I%02d" % k)` 产出，
  形如 `[I01] · 长期｜宏观·中性·高：… —— …（3 天前｜2 源｜置信 0.72）`。
  **前缀是 `f"[{ref}] "`，紧跟 v1 原文行，中间一个半角空格**——v1 的行体一个字节不改，
  冻结断言只需在前缀上加断言。
- `budget` 计算把前缀长度算进去（`len(line)` 天然含它），「装不下就停、绝不半截截断一条」
  这条 v1 纪律不变。
- wire 结构从 `{"lines": [...], "hidden": n}` 扩为
  `{"lines": [...], "hidden": n, "refs": {"I01": 1234, ...}, "citeEnabled": true}`。
  `refs` 的键序与 `lines` 的顺序**严格一致且无空洞**（G-2）。
- `cite=False` 时 wire **不含** `refs` / `citeEnabled` 两键（整键缺席，沿用仓库「缺席即回退」范式）。

`format_intel(wire)` 不变——它只拼接已定稿的行。段末追加一句（仅 `citeEnabled` 时）：

> `（每条前面的 [Ixx] 是这一轮的情报编号。submit_trade_decision 的 intelRefs 字段用于事后复盘归因：`
> `采信了哪几条就写哪几条，没采信就留空；它不影响本轮任何风控判定。）`

**两处措辞都是有意的**：

1. **必须写「不影响任何风控判定」**。不写，模型会把「我引用了它」当成一种需要论证的承诺，
   进而在证据不足时倾向于不写——那会让引用率系统性偏低，而偏低的引用率会被误读成「情报没用」。
   这是一处**测不出来但可预见**的偏误，用一句话消除它的成本是零。
2. **必须是陈述句而不是祈使句**（评审 P1-10）。`build_reflection_prompt` 的第一行就是
   `evidence = build_decision_prompt(payload)`（`prompts.py:1001-1005`），这一段会**原样**进入
   下单前复核官的上下文，而复核官的工具 `submit_trade_reflection` **没有** `intelRefs` 字段。
   一句「请你写出编号」对它是一条无法执行的指令；一句「这个字段用于……」则是一句它读得懂的
   背景说明。G-1 的字节冻结因此**必须同时覆盖** `build_reflection_prompt`，并另有一条用例断言
   反思工具 schema 逐字节不变。

#### 3.1.2 决策工具的可选字段（`ai_auto/prompts.py`）

```python
def build_decision_tool_schema(position_step_pct, *, allow_limit=False, limit_band_pct=0.02,
                               allow_hold_exit=False, allow_exit_priority=False,
                               allow_reasoning_digest=False, contrarian=None,
                               allow_intel_refs=False, intel_max_refs=8) -> dict:
```

- 早退判据追加 `and not allow_intel_refs`（E-1，G-1 的直接依赖）。
- 新属性（仅 `allow_intel_refs=True` 时露出，`required` 不变）：

```python
props["intelRefs"] = {
    "type": "array",
    "items": {"type": "string", "pattern": "^I[0-9]{2}$"},
    "maxItems": intel_max_refs,
    "description": "（可选）你在本轮研判中实际采信的外部情报编号，如 [\"I01\",\"I03\"]。"
                   "只能写情报段里出现过的编号；写不存在的编号会被系统丢弃。"
                   "它只用于事后复盘归因，不参与任何风控或仓位判定。",
}
```

- 英文脊柱侧（`prompts_en.py`）的工具描述追加同义一句，与中文版**语义一致**；
  `reasoning_protocol.py:228` 的字段清单同步补一行（那份清单是模型看到的可写字段目录，
  漏了它模型会认为这个字段不该写）。

#### 3.1.3 解析与服务端裁决

`decider.py`：

```python
_INTEL_REF_RE = re.compile(r"^I[0-9]{2}$")

def _resolve_intel_refs(self, raw: dict) -> tuple[str, ...]:
    """解析可选的情报引用编号（总闸关 / 缺席 / 类型错 ⇒ 空元组）。**绝不降级**。"""
    if not self.allow_intel_refs:
        return ()
    items = raw.get("intelRefs")
    if not isinstance(items, list):
        return ()
    seen: list[str] = []
    for entry in items:
        token = str(entry or "").strip().upper()
        if _INTEL_REF_RE.match(token) and token not in seen:
            seen.append(token)
        if len(seen) >= self.intel_max_refs:
            break
    return tuple(seen)
```

**不限定 action 作用域**（与 E-3 的三个先例刻意不同，理由写进 docstring）：hold 轮的引用同样
要记，那正是轴 B 区分 `entry` / `hold` 的前提；而「残留值泄漏」在这里不成立——它是一个纯
观测字段，泄漏到别的 action 上只会让统计多一条 `role="hold"` 的行。

`TradeDecision` 末位新增 `intel_refs: tuple[str, ...] = ()`（E-2 的位置与纪律）。
两条返回路径（开仓的提前 return 与文件末尾的 return）**都要带上它**——v1 的
`contrarian_evidence` 注释已经把「改错行的表现是字段恒为 None 且不报任何错」写死在那里。

**裁决在纯核心**（新文件 `intel/citation.py`，无 IO、无时钟）：

```python
@dataclass(frozen=True)
class CitationResolution:
    valid: tuple[tuple[str, int], ...]   # (ref_token, item_id)
    unknown: tuple[str, ...]             # 编号表里查不到的 token

def resolve_citations(refs, refmap: dict[str, int] | None) -> CitationResolution: ...
```

`refmap` 缺席（未注入 / cite 关）⇒ 全部落 `unknown`（而不是全部落 `valid`）：
「模型引用了编号，但这一轮根本没有编号表」是一个**应当可观测**的异常，不是一个可以静默
当成空的情形。

**但它与「编造」是两件事**（评审 P2-2）：`CitationResolution` 因此带**两个**计数出口——
`unknown`（编号表存在、这个号不在里面 ⇒ `fabricated_ref`）与 `no_refmap`（这一轮压根没有
编号表 ⇒ `ref_without_digest`）。把后者也计进 `fabricated_ref`，会让「情报层今天降级了」在
面板上显示成「模型今天一直在编造引用」——一个把系统故障读成模型不诚实的读数比没有读数更坏。

#### 3.1.4 引用落库

引用行在**决策轮内**产生（内存），在**决策事件推送之后**落库。落点见 §3.2.1。

### 3.2 轴 B：可结算的情报

#### 3.2.1 `IntelAttribution` 运行时（新文件 `intel/attribution.py`）

一个每会话一份的轻对象，构造于 `AiTradeSession.__init__`（与 `self._experience` 同处）。
**未装配（`attribution_enabled=False` 或 `intel` 总闸关）⇒ 全部方法空转**，不建任何状态。

| 方法 | 调用点 | 同步/异步 | 干什么 |
|---|---|---|---|
| `note_decision(stage1, *, round_no, ts, refmap)` | `session.py` 紧随 `self._discipline.note_intent(...)` 之后（E-4 的留存点区） | 同步、纯内存 | 用 `resolve_citations` 裁决，为每个 `valid` 生成一条**轮级待写行**（`role="hold"` 暂定，`position_seq` = 当前 epoch），把 `unknown` / `no_refmap` 两个计数累加到本轮直方图 |
| `note_executed(final_action, executed, *, resting)` | 紧随 `self._cost.note_round(...)` 之后 | 同步、纯内存 | ①补齐本轮待写行的 `final_action` / `traded`（模型意愿 vs 实际动作两个都留档）；②**若本轮留下了一张 `intent=="open"` 的挂单，把本轮待写行的 `cl_ord_id` 冻结为 `resting.cl_ord_id` 并同时放进跨轮认领槽**（P0-1，逐行镜像 `CostRuntime.claim_estimate(self.executor.resting)`，同一处调用点、同一个 `intent=="open"` 判据） |
| `note_open(trade, *, seq)` | `_dispatch` 开仓分支，紧随 `self._experience.note_open(...)` | 同步、纯内存 | 把该笔开仓对应的引用行 `role` 改 `entry`、`position_seq` 绑定为 `seq`。**取行顺序逐字镜像 `_take_estimate`：先按 `trade["clOrdId"]` 在跨轮认领槽里认领（限价跨轮），认领不到再回退本轮待写行（市价恒走回退）**；两条路都取不到就什么都不做（不猜、不改绑） |
| `note_close(trade, meta, trip)` | `_dispatch` 平仓分支，紧随 `self._experience.note_close(...)`（**必须在 `_inject_explain_meta` 之后**，E-5） | 同步、纯内存 | 把本轮待写行的 `role` 改 `exit`；把 **`trip.seq`**（★ 见下）对应的全部已落库行 + 本轮待写行标记为待结算，净额取 `trip.net_pnl_usd` |
| `await persist_pending()` | ①`_emit_decision(...)` 之后；②平仓分支 `await self._experience.persist_pending()` 之后 | 异步、毫秒级本地 INSERT/UPDATE | 一次提交：INSERT 新引用行 → UPDATE 待结算行 → 累加 `ai_intel_items` 六个统计列 |
| `end_round()` | `_run_decision_round` 的 `finally`（与 `self._cost.end_round()` 同处，P1-9） | 同步、纯内存 | 丢弃**尚未落盘的轮级待写行**并计入 `stats().dropped`。**跨轮认领槽不清**——那正是限价单要跨轮活下来的东西 |
| `stats()` | `snapshot()` / `aiStats` | 同步 | 会话内累计：`cited`、`fabricated`、`refWithoutDigest`、`settledTrips`、`netPnlUsd`、`dropped` |

★ **`seq` 只能取 `trip.seq`，绝不取 `self._position_seq`**（评审 P0-2）：`_dispatch` 的 close 分支
**第一件事**就是 `self._position_seq += 1`（`session.py:459`），等 `note_close` 跑到时那个计数器
已经是**下一个** epoch。经验层正因此从 `trip.seq` 取（`experience/runtime.py:246-252`）。取错的
症状是结算 UPDATE 恒命中零行、`settled_count` 与 `net_pnl_usd` 恒 0，而**这件事不会报任何错**。

**跨轮认领槽的容量纪律**（P0-1）：`dict[str, list[PendingRow]]`，上限 `PENDING_CITE_CLAIM_MAX = 8`，
超量按 FIFO 丢弃并计 `dropped`（挂单额度只有一个，正常只会有 1 项；上限存在只是为了让一条
永不成交的挂单不会把内存占住）。逐字沿用 `attribution.PENDING_ESTIMATE_MAX` 的做法。

**为什么 `persist_pending` 挂在 `_emit_decision` 之后而不是之前**：决策卡是用户最在意的那个
延迟；一次本地 INSERT 虽然是毫秒级，但把它排在事件推送之后，最坏情况下也只会让统计晚一
瞬间，绝不会让界面晚一瞬间。这与 v1 把 `push_intel` 定为「绝不在决策链上被 await」同源。

**异常纪律**：`persist_pending` 内部 `try/except Exception` 记 warning 并**丢弃**本批待写行
（不重试、不堆积）。理由与 `_persist_trade` 的 best-effort 同源：归因是富化，成交与决策是
账实相符；一次富化异常绝不允许换来一次没推出去的决策事件。**但**：`except` 里必须把丢弃
条数累加到 `stats().dropped`，并经 `aiStats` 下发——静默丢弃正是本仓库反复栽的那个坑。

**关停纪律**（评审 P2-12）：`stop()` 时尚未落盘的待写行与认领槽**一并丢弃并计入 `dropped`**，
**不新增任何关停步骤**——`_shutdown_steps` 的第 7 步（`ai_trade_intel`）已被 `test_ai_intel_wiring.py`
冻结，为了几条归因明细去动那张表，风险远大于收益。

#### 3.2.2 结算与 outcome 判定（纯核心，`intel/citation.py`）

```python
def classify_outcome(net_pnl_usd: float | None, *, eps: float = 1e-9) -> str:
    """净额 → 结果码。`None` ⇒ "void"（往返未结算 / 结算不可得），绝不写成 0。"""
```

取值：`win`（>eps）/ `loss`（<-eps）/ `flat`（|x|≤eps）/ `void`（None）。
`trip is None`（接管仓 / 离线平仓，`CostRuntime` 没有它的开仓断面）⇒ 该 epoch 的引用行写
`settled=True, outcome="void", net_pnl_usd=NULL`：**结算尝试确实发生过、净额确实不可得**。
不写 `settled=True` 的话那些行会永远挂在待结算集合里，而它们的 epoch 已经不会再回来了——
一个永远不收敛的待结算集合比一个诚实的 `void` 更难解释。
**`void` 不计入 `settled_count`，也不计入 `win_count`**——「没结算成功」与「结算成功但打平」
是两件事，写成同一个数就等于把一次失败伪装成一次平局。

结算只作用于 `role in ("entry", "exit")` 且 `position_seq == trip.seq` 的行（★ P0-2）；
`role="hold"` 的行永远 `settled=False`、`outcome="void"`（§1.2 轴 B 的判据：「情报影响了这笔开仓 / 这次离场」可证伪，「情报影响了
这次继续持有」不可证伪）。

#### 3.2.3 回写条目统计（`ai_intel_items` 六个新列）

| 列 | 何时 +1 / 累加 | 读点 |
|---|---|---|
| `cite_count` | 每写入一条引用行（含 `role="hold"`） | 卡片、影响力页签、注入排序 |
| `cite_entry_count` | 写入 `role="entry"` 的行 | 卡片、影响力页签 |
| `settled_count` | 结算且 `outcome != "void"` | 卡片、胜率分母 |
| `win_count` | 结算且 `outcome == "win"` | 卡片、胜率分子 |
| `net_pnl_usd` | 累加已结算净额（可正可负） | 卡片、影响力页签、注入排序 |
| `last_cited_ts` | 每写入一条引用行时取 max | 排序、整理复核表 |

累加走一条 `UPDATE … SET cite_count = cite_count + :n` 的原子语句（**不做「读-改-写」**：
情报轮与决策轮跑在不同的 DB 会话里，读改写会丢更新，而丢更新不会报错）。

> ⚠️ **这 10 个新列绝不允许出现在 `item_to_payload` / `_item_values` 里**（评审 P1-6）。
> `upsert_intel_items` 的冲突分支用 `_item_values` 的键集**减去** `_PRESERVED_ON_CONFLICT`
> （`tier` / `pinned` / `user_edited` / `promote_count` / `demote_count` / `created_ts`）求
> `update_cols`——统计列一旦进了 `_item_values`，同一条情报被再次报道时全部引用战绩会被
> **清零**，且不报任何错（`db/ai_intel_repositories.py:31-32,358-364`）。
> `item_from_wire` 是**读**方向，必须带上这 10 个字段（`score_item` 的 `cite_bonus` 与
> `build_digest` 的 `refuted` 过滤都依赖它们）；`item_to_payload` 是**写**方向，一个都不许带。
> G-20 冻结这条。

#### 3.2.4 反哺

三处，全部**只改排序与优先级，不改准入**：

1. **注入排序**（`gate.score_item`）：签名改为
   `score_item(item, *, now, limits: IntelLimits | None = None)`——**`limits is None` ⇒ 不计
   bonus、返回值与今日逐字节相同**（评审 P1-1：现签名是 `(item, *, now)`，四个调用点
   `disclosure.py:79` / `gate.py:418` / `gate.py:609` / `service.py:392` 没有一个能拿到
   `cite_score_bonus`；给新形参一个默认值，`admit` 与 `render_listing` 两处就零改动）。
   `cite_score_bonus` 住进 `IntelLimits`（纯核心的唯一配置读点）。
   传了 limits 时新增一项 `cite_bonus = limits.cite_score_bonus *
   tanh(cite_entry_count / 3) * sign_bias`，其中 `sign_bias = 1.0` 若 `net_pnl_usd >= 0`
   否则 `0.0`。**`render_listing`（整理轮的清单渲染）刻意不传 limits**：整理轮该看的是情报
   本身，不是它的战绩；战绩在整理轮里的作用是第 2 条的举证责任，而不是把它排到模型眼前。即：**被引用过且赚过钱的情报排前面；被引用过但亏钱的情报不加分，也不减分**。
   *不减分的理由*：一条情报被引用后亏钱，可能是情报错了，也可能是仓位错了、时机错了。
   把它降权是可辩护的，把它降到「不如从没被引用过」不可辩护。
2. **整理轮复核表**（`gate.review_actions`）：`archive` 动作对
   `cite_entry_count > 0 且 net_pnl_usd > 0` 的条目**要求模型给出更长的理由**（≥30 字，
   否则驳回并计 `rejected_archive_profitable`）。这不是免死金牌，是一道**举证责任的抬高**。
3. **溢出淘汰**（`gate.plan_cleanup`）：淘汰候选按 `score_item` 升序，`plan_cleanup` 手里有
   `limits`，**必须把它显式传下去**（`score_item(it, now=now, limits=limits)`）——不传就是
   「第 1 条写了但溢出淘汰这一路没走到」，正是本仓库的成文教训「用例绿着但分支从没被走到」。
   用例 `test_overflow_eviction_actually_reads_the_bonus` 冻结这条。

### 3.3 轴 C：可证伪的情报

#### 3.3.1 谁被送去验证（纯核心，`intel/verify.py`）

```python
def plan_verification(items, *, limits: IntelLimits, now: float) -> tuple[IntelItem, ...]:
```

候选池 = `status == "active"` 且 `origin == "agent"` 的条目（**用户手写的条目不送验证**：
用户写下的东西轮不到模型去判真假；用户可以手动点「验证这一条」，见 §4.1）。

优先级（高 → 低，同级按 `event_ts` 升序，即最老的先验）：

> 下表的 P1/P2/P3 是**候选优先级**，与本文《评审记录》里的 P0/P1/P2 严重度是两套编号，别串。

| 级 | 判据 | 为什么它最该被验 |
|---|---|---|
| P1 | `is_scheduled` 且 `event_ts <= now`（已排期事件的时刻已过）且 `not user_edited` | 「解锁到底发生了没有」是本层最硬的可证伪问题，也是 v1 `lapsed_schedule` 清理规则唯一没能回答的那半边 |
| P2 | `tier in ("mid","long")` 且 `verdict == ""` 且 **`verify_count == 0`** 且 **`not user_edited`** 且 `now - captured_ts >= verify_min_age_h` | 中长期条目是**被反复注入**的那些，它们最值得被复核 |
| P3 | **`verdict in ("unresolved", "refuted")`** 且 **`not user_edited`** 且 `now - verdict_ts >= verify_recheck_h` 且 `verify_count < verify_max_attempts` | 上次没判出来 / 只被判过一次 `refuted`，给它有限次重来。收 `refuted` 是**两击制**（§3.3.4）的前提（评审 P1-14） |

截断到 `verify_max_items`（默认 3）。**`pinned` 的条目照样进候选**——免死金牌管的是**后果**
（不被自动降级），不是**检查**（G-8）。

**三条判据里 `verify_count == 0` 与 `not user_edited` 是评审 P0-4 的修法，不是冗余**：
§3.3.4 给用户的唯一撤销出口是「我不同意」⇒ `PATCH clearVerdict` 把 `verdict` 清成 `""`。
若 P2 只看 `verdict == ""`，这条目会**立刻**回到候选池最前面并在下一轮被重新判成 `refuted`——
用户的显式意愿被系统静默撤销，本文 §1.3 承诺 3 当场失效。因此：

- `clearVerdict` **只清 `verdict` / `verdict_ts` / `verdict_note`，绝不清 `verify_count`**；
- `clearVerdict` **同时置 `user_edited=True`**（既有的第二把免死金牌在这里兼任「判定锁」）；
- 于是这条目对三条判据**全部**不再成立 —— G-17 用「连跑三轮都不进候选」冻结它。
- 想让它重新接受核查？路径是明确的一次 `POST /intel/items/{itemId}/verify`（§4.1 #21），
  那是一条**人类显式指令**，与自动池是两回事。

#### 3.3.2 验证语料

- 每条候选生成**一条**查询，由服务端**确定性**构造：
  `f"{item.headline} {inst_base} 最新 进展"`（`inst_base` = `BTC-USDT-SWAP` → `BTC`，
  沿用 v1 `corpus`/`prompts` 已有的 base 解析）。**绝不让模型自己写查询**——那是它
  「先想好结论再去找证据」最短的路。
- 复用 `SearchRunner.search_many(queries, limit=verify_docs_per_item, freshness_days=…)`；
  `freshness_days` 取 `max(1, ceil((now - event_ts)/86400) + 1)`，即只看**事件之后**的报道。
- 复用 `corpus.build(..., blocked_domains=blocked)`（与采集轮同一份拉黑清单，轴 D 立即生效）。
- 语料编号仍是 `D1..Dn`，与采集轮同一套渲染，**模型看不到 URL**。

#### 3.3.3 验证 Agent 与工具（`intel/prompts.py` 追加）

系统 prompt 的角色是「**核查员**，不是分析师」，硬约束五条：

```
## 核查协议（必须逐条执行，最后必须且只能调用一次 submit_intel_verdicts）
## V1 逐条读【待核查条目】，明确它做出了什么**可被后续事实检验**的陈述
## V2 在【新检索到的语料】里找与该陈述**同一件事**的文档，记下编号
## V3 判定：语料明确支持 → confirmed；语料明确否定或事件已确定不会发生 → refuted；
##    语料没说、只说了相关但不同的事、或只有传闻 → unresolved
## V4 为每一条判定写一句 ≤120 字的理由，并列出你依据的语料编号（只能写 D 开头的编号）
## V5 自查：我判 confirmed / refuted 的每一条，是不是都指着一个**具体的** D 编号？
##    指不出来的，改成 unresolved。

## 硬约束
- 你**没有**交易工具，也**不知道**任何持仓、盈亏或方向。你的唯一任务是核对事实。
- 「没找到反证」不是 confirmed，「没找到证据」不是 refuted。两者都是 unresolved。
- 不得因为一条情报的方向（利多 / 利空）而倾向任何判定。
```

工具 `submit_intel_verdicts`：

```python
CURATE_VERIFY_TOOL_SCHEMA = {
  "type": "object",
  "properties": {"verdicts": {"type": "array", "maxItems": <verify_max_items>, "items": {
      "type": "object",
      "properties": {
        "itemRef": {"type": "string", "pattern": "^C[0-9]{1,2}$"},
        "verdict": {"type": "string", "enum": ["confirmed", "refuted", "unresolved"]},
        "reason": {"type": "string", "maxLength": 120},
        "refs": {"type": "array", "items": {"type": "string", "pattern": "^D[0-9]{1,2}$"},
                 "maxItems": 6}},
      "required": ["itemRef", "verdict", "reason"]}}},
  "required": ["verdicts"]}
```

候选条目在 prompt 里以 `C1..Cn` 编号（**与语料的 `D` 编号分开命名空间**，否则模型会在两张
表之间串号，而串号不会报错）。

**候选渲染只给四样东西：`C 编号` / `headline` / `summary` / 事件时刻**（评审 P1-11）。
**绝不复用 `service.render_listing`**，也绝不带 `stance`（利多/利空）、`impact`、`confidence`、
`tier`、`domain_count`：把「这条是利多」摆在核查员眼前，再在硬约束里要求他「不得因为方向
倾向任何判定」，是在同一段 prompt 里用输入反驳自己的指令。这条用源码扫描用例
`test_verify_prompt_omits_stance_and_confidence` 冻结，与 `test_verify_prompt_carries_no_position_facts`
并列——两者是同一条纪律的两端：**核查员既不知道我们的仓位，也不知道这条情报站哪一边**。

**编号值域**（评审 P2-10）：`D[0-9]{1,2}` 的上限是 99 篇。`§十一` 的启动期校验器因此同时
断言 `ai_trade_intel_corpus_max_docs <= 99`——超过就得先改正则，而不是让第 100 篇静默拿不到编号。

#### 3.3.4 服务端复核与四种后果（纯核心 `verify.review_verdicts` + 写段 `service_verify`）

复核表（**全部在纯核心里判完**，写段只执行）：

| 情形 | 判定 | 计数码 |
|---|---|---|
| `itemRef` 不在 `C` 表 | 整条丢弃 | `bad_item_ref` |
| `verdict` 值域外 | 整条丢弃 | `bad_verdict` |
| `refs` 里有不在 `D` 表的编号 | **该编号**丢弃（不丢整条） | `bad_ref` |
| `verdict != "unresolved"` 且**有效 `refs` 为空** | **降级为 `unresolved`** | `verdict_no_ref`（G-7） |
| `reason` 为空或全空白 | 降级为 `unresolved` | `verdict_no_reason` |
| 同一 `itemRef` 出现多次 | 取第一条，其余丢弃 | `dup_item_ref` |

通过复核之后的确定性后果：

| verdict | 对 `ai_intel_items` 的写 | 对注入的影响 | 用户如何撤销 |
|---|---|---|---|
| `confirmed` | `verdict="confirmed"`、`verdict_ts=now`、`verdict_note=reason`、`verify_count+1`、`confidence = min(1.0, confidence + verify_confirm_bonus)` | 不变（仍注入），`score_item` 因 `confidence` 上升而略微前移 | 编辑条目即可（`user_edited` 置位） |
| `refuted` · **第一击**（`verify_count == 0`，未受保护） | `verdict="refuted"`、`verdict_ts`、`verdict_note`、`verify_count=1`、`confidence *= verify_refute_penalty`。**`tier` / `status` / `expires_ts` 一个字节都不动** | **立即停止注入**（`build_digest` 与 `status == "active"` 同一行判据里过滤 `verdict == "refuted"`） | 卡片上点「我不同意」⇒ `PATCH clearVerdict`（清 verdict、**不清 `verify_count`**、置 `user_edited`） |
| `refuted` · **第二击**（`verify_count >= 1` 且本次仍判 `refuted`，未受保护） | 追加 `tier` 降一层（`long→mid→ultra`），**`expires_ts` 必须在同一条 UPDATE 里按新 tier 重算**（v1 不变式 I-3）。**已在 `ultra` 也不归档** | 已经停了 | 同上；降层后仍可手动升回（`PATCH tier`） |
| `refuted`（**`pinned` 或 `user_edited`**） | 无论第几击，只写 `verdict` / `verdict_ts` / `verdict_note` / `verify_count`，**不动 tier、不动 status、不动 confidence** | **同样停止注入**（G-8） | 同上 |
| `unresolved` | 只写 `verdict_ts` / `verify_count`（`verdict` 保持原值，**不覆盖已有的 `confirmed`**） | 不变 | — |

**为什么是两击制，以及为什么 `refuted` 永不归档**（评审 P1-12 / P1-13）：v1 的 §八 R-3 把
「一次错误的 `refuted`」列为本轮**最危险**的失效模式，并把「只降一层、不直接删」写成缓解③；
而原设计的「已在 `ultra` ⇒ `status="archived"`」恰恰让一次模型判定就能归档一条情报——规则与
自己的风险缓解正面冲突。修法是两条：

1. **降级需要两次独立核查**（中间隔着 `verify_recheck_h`、语料是重新检索的、编号表是新的）。
   代价只是「一条真被推翻的情报晚 48 小时降层」，而它**在第一击时就已经停止注入**了——
   也就是说，真正保护真实资金的那一半后果是立即生效的，**只有不可逆的那一半**才需要复核。
2. **`refuted` 永不触发归档**。`ultra` 层的 TTL 本来就是 72 小时，归档买不到任何东西，却把
   一个可逆动作换成了一个要用户去「已归档」里翻的动作。归档留给确定性清理（TTL / 溢出 /
   `lapsed_schedule` / 存量扫除）——那四条都不依赖模型的一次判断。

这同时兑现了一条本轮**没有新增配置项**的观察窗：升级后的第一轮核查对每一条存量情报都只会
产生 verdict 与「停止注入」，不会改动任何层级；运维在这段时间里看着「证伪率」这个读数，
就能在**任何层级被改写之前**判断这个 Agent 判得靠不靠谱。

**「refuted 停止注入」为什么对受保护条目也生效**：置顶与手改保护的是「这条记录别被系统删掉」，
不是「这条记录必须继续影响真实资金」。一条已被后续报道推翻的情报继续进交易 prompt，是本特性
在「真实可信」四个字上唯一不可辩护的失败模式。用户想让它继续注入，路径是明确的一次
「我不同意」（清 verdict），而不是一次沉默的保留。

#### 3.3.5 验证轮的编排（新文件 `intel/service_verify.py`）

```python
async def run_verify_round(deps: IntelDeps, inst_id: str) -> RoundOutcome:
```

步骤（与 `run_curate_round` 同构，**一次提交**）：

1. 读 scope（`repo.load_intel_scope`）→ `plan_verification` 选候选。**候选为空 ⇒ 不写 round 行、
   直接返回 `RoundOutcome(status="ok", actions={"verify_skipped": 1})`**（沿用 v1「未到期不写
   round 行」的纪律：一张全是空轮的审计表等于没有审计表）。
1.5 **日配额自判**（评审 P1-5）：`daily_used(watch) >= ai_trade_intel_daily_round_cap` ⇒
   `degrade_code="budget_exhausted"`，写 round 行、**不调检索也不调 LLM**、返回。
   这一步不能省：配额闸今天**只写在 `run_collect_round` 里**（`intel/service.py:172`），
   核查轮不自判的话它会 bump `day_rounds`（吃掉采集的额度）却自己不受任何约束——
   最坏的组合是「采集因为配额停了，核查还在烧钱」。
2. `deps.search is None` ⇒ `degrade_code="verify_no_provider"`，写 round 行、返回。
3. 检索 + `corpus.build`；**全部 query 失败** ⇒ `degrade_code="verify_search_failed"`。
   语料为空（检索成功但零文档）⇒ `degrade_code="verify_no_corpus"`，**全部候选记
   `unresolved` 并 `verify_count+1`**——「查不到」是一个真实的核查结论，不是一次失败。
4. **LLM 用 `deps.verify_llm`**（评审 P1-3）：`run_round(...)` 没有 `max_tokens` 形参，而
   `intel/llm.py` 的 docstring 明写**刻意不改 `LLMClient.stream` 的 Protocol**（32 个测试文件
   实现了它）。故 `verify_max_tokens` 只能走构造参：`build_intel_llm_client` 增一个
   keyword-only 的 `max_tokens_override=None`（缺省即今日行为、既有调用点零改动），
   装配点造**第二个** client 放进 `IntelDeps.verify_llm`。
   `verify_llm is None` ⇒ **回落 `deps.llm`**（用采集轮的预算跑，而不是报错——一个因为
   预算装配没做好就整轮不跑的核查层，比一个预算稍大的核查层坏得多）；
   两者皆 `None` ⇒ `verify_no_provider`；LLM 失败 / 未调工具 ⇒ `verify_llm_failed`。
5. `verify.review_verdicts(...)` → 后果计划 → `repo.apply_intel_verdicts(...)`（一条批量写）。
6. 写 `ai_intel_rounds`（`kind="verify"`）：`doc_count` / `domain_count` / `actions_json`
   （`{"confirmed":1,"refuted":1,"unresolved":1,"verdict_no_ref":1,...}`）/ token 用量 /
   `degrade_code`；`bump_intel_watch(kind="verify", verify_reset=True)` 写 `last_verify_ts`
   并清 `curates_since_verify`（**必须走新加的第三个分支，见 §3.3.6 的硬约束**）。
7. **不在这里刷新快照**（评审 P2-7）。`IntelRuntime._run` 的 `_after_round` 已经无条件
   `await self.refresh(...)`（`runtime.py:163-166`），`run_verify` 逐行镜像 `run_curate` 也就
   自动继承了它；在编排里再刷一次是同一轮里的第二次全量查库，且两次结果之间的差异**没有
   任何东西会告诉你**。verdict 改变了注入集合这件事由 `_after_round` 兑现，用例
   `test_verify_round_refreshes_snapshot_exactly_once` 冻结「恰好一次」。

#### 3.3.6 调度（`intel/scheduler.py::_tick`，唯一改动点）

```python
        every = int(runtime.deps.settings.ai_trade_intel_curate_every_n_collects)
        if int(watch.get("collectsSinceCurate") or 0) + 1 >= every:
            # 验证轮排在整理轮**之前**：整理轮的升降级复核要读 verdict，读旧 verdict
            # 就是「算了却算错了对象」（E-12 同形）。
            if _verify_due(runtime.deps.settings, watch):
                await runtime.run_verify(inst_id)
            await runtime.run_curate(inst_id)
        return
```

`_verify_due` 是纯函数（`service.py`）：`verify_enabled` 且
`curates_since_verify + 1 >= verify_every_n_curates`。

> ⚠️ **`bump_intel_watch` 必须先改成三分支，否则上面这段代码在两个方向上都是错的**（评审 P0-3）。
> 实读 `db/ai_intel_repositories.py:180-186`，它今天是一个**二值 catch-all**：
>
> ```python
>     if kind == "collect":
>         row.last_collect_ts = now if status == "ok" else int(row.last_collect_ts or 0)
>         row.collects_since_curate = int(row.collects_since_curate or 0) + 1
>     else:                       # ← "verify" 会静默落进这里
>         row.last_curate_ts = now
> ```
>
> 两处后果，都不报错：
>
> 1. **核查轮会改写整理轮的节律真值**（`last_curate_ts` 被当成「刚整理过」），
>    弹窗上的「上次整理」从此指着一个从没整理过的时刻。
> 2. **`curates_since_verify` 在全仓库没有任何自增点**。它恒为 0 ⇒ `_verify_due` 变成
>    `0 + 1 >= verify_every_n_curates`，**只有默认值 1 才为真**。用户把这个
>    「`ge=1, le=12`」的设置调到 2，核查轮从此**一次都不会跑**，界面上也不会有任何异样。
>
> 故 §6.1 #23 的改动是**硬要求**，且必须是显式三分（不是再加一个 `elif` 就完）：
>
> ```python
>     if kind == "collect":
>         row.last_collect_ts = now if status == "ok" else int(row.last_collect_ts or 0)
>         row.collects_since_curate = int(row.collects_since_curate or 0) + 1
>     elif kind == "curate":
>         row.last_curate_ts = now
>         # ★ 核查节律的**唯一**自增点（closed-loop 评审 P0-3）
>         row.curates_since_verify = int(row.curates_since_verify or 0) + 1
>     elif kind == "verify":
>         row.last_verify_ts = now        # **绝不动 last_curate_ts**
>     if curate_reset:
>         row.collects_since_curate = 0
>     if verify_reset:
>         row.curates_since_verify = 0
> ```
>
> 未知 `kind` 一律**只推公共计数**（`day_rounds` / `fail_streak` / `next_due_ts`）而不碰任何
> 节律字段——一个不认识的轮种不该有能力改写别人的节律。G-16 冻结这两条。
`IntelRuntime.run_verify` 逐行镜像 `run_curate`（同一把 `_lock`、同一个 `_in_flight`、
同一个 `_run` 护栏、同一个 `_after_round`）——**不新建任何任务集、不新建任何循环**（E-13）。

### 3.4 轴 D：可拒绝的信源

#### 3.4.1 兑现 `ai_intel_feedback` 的写点

v1 把「表有读点没写点」如实登记为待办（`docs/plans/ai-trade-market-intel/spec.md` 文末）。
本轮补三个端点（§4.1 #13-#15）与两个前端入口，**仓库层一行不改**（E-9）。

#### 3.4.2 `block` / `trust` 的语义边界

| 动作 | 作用域 | 生效面 | 明确**不**做什么 |
|---|---|---|---|
| `block` | 域名，`instId=""` 为全局、否则单标的 | ①采集轮语料过滤（v1 已有）；②验证轮语料过滤（同一份清单）；③**存量扫除**（见下） | **不删除**任何既有条目的 `sources_json` 记录——审计要留住「当时是谁说的」 |
| `trust` | 同上 | ①置信度先验 `+trust_bonus`（默认 0.05，**封顶仍是 1.0**）；②`corpus.build` **排序键**上的并列打破（同分优先留可信源） | **绝不**让可信域名多算一次 `domain_count`。`domain_count` 是「来源独立性」的分母，被信任不等于变成了两个独立信源；混淆这两件事会让「两个独立来源才能升长期」这道 v1 闸被一次点击绕过。**也绝不改 `corpus.build` 的截断规则**（只改排序键上的并列打破）——`trust` 是「同分时优先留谁」，不是「多留一篇」 |

> ⚠️ **`trust` 今天没有任何读点，必须在本轮一并补上，否则它就是第二个 `ai_intel_feedback`**
> （评审 P1-7）。实读 `db/ai_intel_repositories.py:580-588`：仓库里唯一的 feedback 读函数
> `list_intel_blocked_domains` 的 where 子句写死了 `action == "block"`。而两处生效面都在
> **纯核心**里（`gate.solve_confidence` / `corpus.build`），信任清单却在 DB。三个落点缺一不可：
>
> 1. **新增** `list_intel_trusted_domains(session, inst_id="")`（形状与 blocked 那个逐字同构，
>    只换 `action == "trust"`），并从 `db/repositories.py` 再导出。
> 2. `run_collect_round` 的只读会话里与 blocked 一起取，经 `corpus.build(..., trusted_domains=…)`
>    进纯核心；`corpus.build` **只**把它用在 `sorted(...)` 的 key 上。
> 3. `gate._solve_sources` 把「本条来源里有没有可信域名」压成一个 bool，作为
>    `solve_confidence` 的**第 6 个 keyword-only 形参** `has_trusted: bool = False` 传入
>    （默认 False ⇒ 既有全部调用点与全部置信度断言零改动）。
>
> 少任何一个，用户点下的「更信任这个源」都会变成一次**什么都没发生**的点击——本轮开篇
> 列为「静默空转」要修的那件事，会被原样复制一份。

**存量扫除**（`gate.plan_cleanup` 新增第 5 步，受 `blocked_sweep_enabled` 单键控制）：
在 `plan_cleanup` 的入参里新增 `blocked_domains: frozenset[str]`，把「`sources` 非空且
**每一个** source 的 `domain` 都在 `blocked_domains` 里」的 active 条目列入
`plan.blocked_ids`，写段以 `reason="source_blocked"` 归档；`CleanupPlan.counts` 同步补
`"blocked_sweep"` 一键（漏了它，`actions_json` 里那个数永远是 0）。
**受保护条目（`pinned` / `user_edited`）跳过**——与 TTL / 溢出的既有纪律一致。

> ⚠️ **两条不可省的接线**（评审 P1-8）：
> 1. `blocked_domains` 必须是**无默认值的 keyword-only 必填形参**。给它一个 `frozenset()`
>    默认值，漏传的症状就是「扫除永远算出空集」而不是一个 `TypeError`——静默空转正是
>    本轮要修的那类缺陷，不该在修它的同一份设计里再造一个。
> 2. `run_curate_round` 的只读会话今天**不查**黑名单（只有 `run_collect_round` 查：
>    `intel/service.py:170` vs `:287`）。必须在 `load_intel_scope` 旁边补一行
>    `list_intel_blocked_domains`，否则第 1 条只是把错误从静默换成了崩溃。

**解除拉黑不自动复活**（G-9③）：归档是一次带理由的状态迁移，自动复活会让用户在两次点击之间
看到条目数跳来跳去。恢复路径是「已归档」页签里那颗已经存在的恢复按钮（v1 终审 RV-2 修好的
那条回头路）。这条决定必须写进 UI 文案：拉黑确认框里明说「已入库的 N 条将被归档，可在
『已归档』中恢复」。

#### 3.4.3 `brave` / `serper` 适配器（`intel/search.py`）

`PROVIDER_SLUGS` 扩为 `("none", "tavily", "brave", "serper")`，`PLANNED_SLUGS` 清空为 `()`
（v1 的「未实现的检索源」`logger.info` 分支保留——它现在负责的是**将来**的 slug，
以及一个手写错的 slug）。

| | `BraveProvider` | `SerperProvider` |
|---|---|---|
| 端点 | `https://api.search.brave.com/res/v1/web/search`（GET，query 串） | `https://google.serper.dev/search`（POST，JSON body） |
| 鉴权 | 头 `X-Subscription-Token: <key>` | 头 `X-API-KEY: <key>` |
| 时效 | `freshness=pd\|pw\|pm`（由 `freshness_days` 映射：`<=1→pd`，`<=7→pw`，否则 `pm`） | `tbs=qdr:d\|w\|m`（同一映射） |
| 条数 | `count=<limit>`（上限 20，超出先夹紧） | `num=<limit>`（上限 20） |
| 正文字段 | `description` | `snippet` |
| 时间字段 | `page_age`（RFC3339 ⇒ 复用 `_parse_rfc3339`） | `date`（**相对文案**，如 `"3 days ago"` / `"Jan 5, 2026"`） |
| 结果路径 | `web.results[]` | `organic[]` |

`serper` 的相对时间解析新增纯函数 `_parse_relative_date(raw, *, now) -> int`：
支持 `N minute(s)/hour(s)/day(s)/week(s)/month(s)/year(s) ago`（英文，大小写不敏感）
与 `%b %d, %Y` 绝对格式；**任何解析失败一律返回 0**（⇒ `undated=True` ⇒ 该条永不可升级，
v1 既有规则）。**绝不**在解析失败时回落到 `now`（G-14）——把「不知道什么时候发的」写成
「刚刚发的」会让时效闸与升级门槛同时失守，且不报任何错。

`resolve_search_provider` 增两个分支；`_Breaker` / `SearchRunner` / `corpus` **一行不改**（E-10/E-11）。

#### 3.4.4 每源健康（观测，不新建机制）

`ai_intel_rounds.search_provider` 已经逐轮记着实际用的 slug。设置卡（`IntelSourceSettings`）
新增一行只读读数：「最近 20 轮：tavily 18 成功 / 2 失败，熔断剩余 0 秒」，数据来自
`GET /api/intel/settings/health`（§4.1 #16，纯读 `ai_intel_rounds` 聚合 + 进程内 breaker
剩余秒数）。**不新建任何表、不新建任何计数器。**

### 3.5 预算与节律

| 项 | v1 | 本轮 | 增量说明 |
|---|---|---|---|
| 采集轮 | 30 min / 标的 | 不变 | — |
| 整理轮 | 每 4 次采集（≈2 h） | 不变 | — |
| 验证轮 | — | 每 `verify_every_n_curates`（默认 1）次整理，紧邻其前 | 每标的 ≈12 轮/天 |
| 验证轮检索量 | — | `verify_max_items × verify_docs_per_item` = 3×4 = **12 文档/轮** | ≈144 文档/天/标的 |
| 验证轮 LLM | — | 1 次调用 / 轮，`verify_max_tokens` 默认 2048 | ≈12 次/天/标的 |
| 决策轮 LLM | 不变 | 不变（`intelRefs` 只是 schema 里多一个可选字段，**不新增任何一次调用**） | 0 |
| 归因写库 | — | 每决策轮 ≤1 次批量 INSERT + 每平仓 1 次批量 UPDATE | 本地 SQLite，毫秒级 |

日配额 `ai_trade_intel_daily_round_cap`（默认 96）的**真实口径**（评审 P1-5，v1 原文在这里
写错了两处，已更正）：

- 闸是 `daily_used(watch) >= cap`，读的是 `ai_intel_watch.day_rounds`；
  **`count_intel_rounds_today` 全仓库零调用点**（`db/ai_intel_repositories.py:570` 只被
  `db/repositories.py` 再导出），它今天不参与任何判定。
- `day_rounds` 由 `bump_intel_watch` 对**每一种** kind 自增，所以三种轮**都消耗**配额；
- 但闸**只写在 `run_collect_round` 里一处**（`intel/service.py:172`），整理轮与新加的核查轮
  今天都**不受**它约束。

即：不做任何事的话，核查轮会**吃掉**采集的额度、自己却不受额度约束——「采集因为配额停了，
核查还在烧钱」。故 §3.3.5 增第 1.5 步，让核查轮**自己判一次同一条闸**（`budget_exhausted`）。
整理轮维持现状（它不检索、成本只有一次 LLM，且它是确定性清理的载体，停掉它等于停掉噪声治理）。

配额被三种轮共享**是刻意的**：情报预算是一个总量，把它悄悄扩大一倍不是一个可以由实现
自行决定的事。设置卡的 `estimate` 读数必须把验证轮算进去（`estimate_daily_cost` 增两个
keyword-only 形参；它今天是 4 个 keyword-only 形参，加到 6 个，按 §十二 的同族登记豁免）。

### 3.6 观测与审计

| 面 | 变更 |
|---|---|
| `ai_intel_rounds` | 新 `kind="verify"`；`actions_json` 新增 7 个键（`confirmed`/`refuted`/`unresolved`/`bad_item_ref`/`bad_verdict`/`verdict_no_ref`/`verdict_no_reason`）；采集与整理轮的 `actions_json` 新增 `blocked_sweep`（存量扫除条数） |
| 降级码 | 新增 `verify_no_provider` / `verify_search_failed` / `verify_no_corpus` / `verify_llm_failed`（外加核查轮自判配额的 `budget_exhausted`，该码 v1 已登记）。**文案表是 `intel/taxonomy.py::DEGRADE_TEXT`，不是 `service.degrade_wire`**（评审 P1-4：后者只是转发 `tax.degrade_message`）。漏登记不报错——`degrade_message` 会回落成 `f"情报流水线降级（{code}）"`，用户看到的就是裸码。G-19 用**枚举式**断言冻结：四个码逐个断言 `degrade_message(code)` 不等于那个兜底串。四个码长度均 ≤ 24，`AiIntelRound.degrade_code` 的 `String(24)` 容得下，无须改列 |
| WS `intel` 事件 | `summary` 新增两块：`citations: {cited, entryCited, settledTrips, netPnlUsd, fabricated}` 与 `verify: {confirmed, refuted, unresolved, pending, lastVerifyTs}` |
| `snapshot()` 第八键 | 同上（同一个 `intel_summary_for` 出口，**不新增第九键**） |
| `aiStats` | 新增 `intel: {cited, fabricated, refWithoutDigest, settledTrips, netPnlUsd, dropped}`（来自 `IntelAttribution.stats()`），与既有 `experience` 块并列。**合并落在调用点** `_build_run_metrics`（`session.py:3086-3099` 那一段），`_compute_ai_stats` 是模块级函数、**一字不动**——先例是紧邻的 `metrics["aiStats"] = self._cost.merge_into(metrics["aiStats"])`（评审 P2-9） |
| 决策轨迹 | `tool_call` 相位的 `tool_input` 里带上 `intelRefs`（`session.py:2049` 已有同形先例：`tool_input["evidenceDigest"] = decision.evidence_digest`），左栏决策卡因此能显示「本轮采信 I01/I03」 |

### 3.7 前端

#### 3.7.1 情报卡的两条新轨（`IntelCard.tsx`）

在既有的「来源 chips」行**之上**插入一条**条件渲染**的元信息行（无数据时整行省略，
不出现「引用 0 次」这种噪音）：

```
🔗 引用 12 · 入场 5 · 已结算 4 · 胜 3 · 净额 +18.40 USDT        [已证实 · 2 天前]
```

- 左半（归因轨，`IntelAttributionRail.tsx`）：`cite_count > 0` 才渲染。净额用既有的
  盈亏配色变量（`--up` / `--down`），**零值用 `--muted`**。悬停 title 给一句完整口径：
  「入场引用 5 次中已结算 4 次；胜 3 负 1；累计净额按往返结算口径。」
- 右半（判定徽章，`IntelVerdictBadge.tsx`）：四态四色四文案 ——
  `confirmed`→「已证实」`--ok`；`refuted`→「已证伪」`--danger` + **卡片整体降透明度**
  并在摘要上方加一行 `⚠ 已被后续报道推翻，已停止喂给交易 Agent`；
  `unresolved`→「待定」`--muted`；`""`→**不渲染**。
  徽章可点：弹出 `verdict_note` 全文 + 「我不同意」按钮（`PATCH` 清 verdict）。

#### 3.7.2 来源 chip 上的「不再采纳此源」（`IntelSourceChips.tsx`）

每个 chip 右侧一个 hover 才出现的 `✕`（`aria-label="不再采纳 {domain}"`）。
**它必须是 `<a>` 的兄弟节点，不是子节点**（评审 P2-3）——`IntelSourceChips` 的 chip 本体是
一个 `<a target="_blank">`，把一个 `<button>` 塞进去就是「可交互元素套可交互元素」，与
`AiTradeIntelButton` 顶着的那条 `aiTradePanelNoNestedButton` 纪律同形。布局由 chip 外面新包的
一层 `group/chip` 容器承担。键盘可达性同样照抄那颗按钮：**Tab 聚焦即可见**，只在 hover 时
出现的破坏性操作对键盘用户等于不存在。点击 ⇒
`ConfirmDialog`（复用既有组件）：

> **不再采纳 `coinmarketwatch.example`？**
> 之后的采集与核查都会跳过这个来源。已入库的 **3** 条情报（全部来源都是它）会被归档，
> 可在「已归档」中恢复。
> [ ] 对所有标的生效（不勾选则只对 BTC-USDT-SWAP 生效）

**必须显示受影响条数**——一个说不清后果的破坏性操作按钮不该出现在这个产品里。
条数由 `POST /api/intel/feedback/preview`（§4.1 #15b，纯读、不写）返回。

#### 3.7.3 弹窗两个新页签（`IntelModal.tsx`）

页签序变为：`超短 | 中期 | 长期 | 影响力 | 信源 | 已归档 | 审计`。

**「影响力」（`IntelImpactTab.tsx`）** —— 回答「情报到底有没有用」：
- 顶部三张读数卡：`本标的引用次数` / `已结算往返` / `关联净额`（正负配色）。
- 「贡献榜」：按 `net_pnl_usd` 降序前 10 条，每行 = 条目标题 + 迷你归因轨 + 判定徽章。
- 「拖累榜」：按 `net_pnl_usd` 升序前 5 条（仅 `< 0`）。
- 「从未被引用」：`cite_count == 0` 的 active 条目数 + 一句诚实的解释文案
  「它们仍在情报库中并可能被注入，只是模型尚未在决策里点名引用过」。
- **空态四种**：未开启引用 / 已开启但尚无引用 / 归因总闸关闭 / 情报层未装配 —— 四段不同文案
  （沿用 v1「五种失败态五段不同文案」的纪律；一个假装「没有数据」的面板会被读成「情报没用」）。

**「信源」（`IntelSourcesTab.tsx`）** —— 回答「这些消息都是谁说的」：
- 上半：本标的**当前库存**里出现过的域名聚合表（域名 / 条数 / 最近一次 / 状态），
  每行可 `block` / `trust` / 取消。数据来自 `GET /api/intel/{instId}/sources`（§4.1 #17）。
- 下半：**全局与本标的**的黑白名单清单（来自 `GET /api/intel/feedback`），可逐条撤销。

#### 3.7.4 审计页签（`IntelRoundsTab.tsx`）

- `kind` 过滤器从两值扩为三值（采集 / 整理 / **核查**）。
- 核查轮的行摘要：`核查 3 条 → 证实 1 · 证伪 1 · 待定 1`；有 `verdict_no_ref` 时追加
  一行灰字「1 条判定因未指出依据被降为待定」——**这正是本特性最值得让用户看见的那一行**，
  它是「模型不能空口断言」这条承诺的可见证据。

#### 3.7.5 类型与 API 层

`intelTypes.ts` 增：`IntelVerdict`、`IntelItem` 的 10 个新键、`AiTradeIntelSummary` 的
`citations` / `verify` 两块、`IntelImpact`、`IntelSourceRow`、`IntelFeedbackRow`、
`IntelFeedbackInput`、`IntelProviderHealth`。
`intelApi.ts` 增 7 个方法，签名与 §4.1 一一对应。
`intel.ts` 增四个纯函数：`verdictLabel` / `attributionText` / `netPnlTone` / `sourceStateLabel`
（**文案集中在这里**，组件里不散写字符串——v1 已立此规矩）。

### 3.8 安全与提示注入

本轮新增两个不可信输入面，各配一道防护：

1. **模型写回的 `intelRefs`**。它是模型可控的自由文本。防护是**正则 + 编号表双查**：
   `^I[0-9]{2}$` 挡掉一切非编号内容，编号表挡掉一切不存在的编号。`intelRefs` 的任何取值
   **都不会**被拼进任何 prompt、日志或 SQL 字面量（落库走参数化绑定）。
2. **验证轮的语料**。它与采集轮的语料同源同格式，因此继承 v1 的全部防护：
   `<<<UNTRUSTED_SEARCH_RESULTS>>>` 包裹、闸⑥ 的内容形态检查、模型只能写编号不能写网址。
   额外一条：**验证 Agent 没有任何交易工具，也拿不到持仓 / 盈亏 / 方向**（`IntelDeps` 传给
   它的上下文里没有这些键，用例 `test_verify_prompt_carries_no_position_facts` 冻结）。
   这不是可选的洁癖：一个知道「我们现在是多头」的核查员，判 `refuted` 的门槛会不一样。

`verdict_note` 是模型写的自由文本，进 DB、进 wire、进 UI。它**只以纯文本渲染**
（React 默认转义），并纳入既有的 `intelNoDangerousHtml.source.test.ts` 源码扫描门禁
（该门禁的正则必须写 `\r?\n`——本仓库 `core.autocrlf=true`，写死 `\n` 会在干净检出上恒红）。

---

## 四、接口设计

### 4.1 REST

新路由全部落既有 `services/quant/app/api/ai_intel.py`（`prefix="/api"`）。

| # | 方法 / 路径 | 入参 | 出参 | 说明 |
|---|---|---|---|---|
| 1-12 | v1 既有 12 个端点 | — | — | 除 #1 / #2 的 wire 扩键外**不改** |
| **13** | `GET /intel/feedback` | `instId?`（缺省=全部） | `{"rows": IntelFeedbackRow[]}` | 黑白名单清单，含 `scope`/`key`/`action`/`instId`/`createdTs` |
| **14** | `POST /intel/feedback` | `{scope:"domain", key, action:"block"\|"trust", instId?}` | `{"ok":true,"archived":3}` | 写 `set_intel_feedback`；`action="block"` 且 `blocked_sweep_enabled` ⇒ 同事务扫除存量并返回归档条数；随后 `runtime.invalidate(instId)` |
| **15** | `DELETE /intel/feedback` | `?scope=domain&key=…&instId=…` | `204` | `clear_intel_feedback`；**不复活**任何条目（§3.4.2） |
| **15b** | `POST /intel/feedback/preview` | 同 #14 | `{"affected": 3}` | **纯读**。给确认框算受影响条数，绝不写库 |
| **16** | `GET /intel/settings/health` | — | `IntelProviderHealth` | 最近 `n≤50` 轮的 provider 成败聚合 + 熔断剩余秒 |
| **17** | `GET /intel/{instId}/sources` | — | `{"rows": IntelSourceRow[]}` | 当前库存的域名聚合（域名 / 条数 / 最近一次 / 当前 block\|trust 状态） |
| **18** | `GET /intel/{instId}/impact` | `limit?≤50` | `IntelImpact` | 影响力页签的全部读数（三张读数卡 + 贡献榜 + 拖累榜 + 未引用计数） |
| **19** | `GET /intel/{instId}/citations` | `itemId?`, `limit?≤200`, `before?` | `{"rows": IntelCitationRow[], "hasMore": bool}` | 引用明细（游标分页，按 `id` 降序） |
| **20** | `POST /intel/{instId}/verify` | — | `202 {"queued": true}` | 手动触发一次验证轮（复用 `_require_runtime` 与 #3/#4 的 202 范式） |
| **21** | `POST /intel/items/{itemId}/verify` | — | `202` | 只验这一条。**显式绕过 `plan_verification` 的全部候选过滤**（`origin`、`verify_count`、`user_edited`、`verify_min_age_h` 一概不看，评审 P2-11）——它是一条人类显式指令，不是自动池的一员。这也是用户手写条目与「我不同意」过的条目**唯一**的验证途径。仍受日配额与 `_require_runtime` 约束 |
| **22** | `PATCH /intel/items/{itemId}` | 既有 body 追加 `clearVerdict?: true` | `IntelItem` | 「我不同意」：清 `verdict` / `verdict_ts` / `verdict_note`，置 `user_edited=true`，**`verify_count` 保持不变**（评审 P0-4：清了它，这条目下一轮就会被自动重判回去，撤销出口变成旋转门）。随后 `runtime.invalidate(instId)` 让注入立刻恢复 |
| **23** | `PUT /settings/intel` | 既有 body 追加 8 个新键 | `IntelSettings` | provider enum 扩到 `none\|tavily\|brave\|serper`；露出 cite/attribution/verify 三把单键与 `verifyMaxItems` 等。**必须同时给 `intel/service.py::INTEL_SETTING_BRIDGE` 补 8 行**（评审 P0-5） |

> ⚠️ **#23 的 8 个键不扩 `INTEL_SETTING_BRIDGE` 就是三把假开关。** `GET/PUT /settings/intel`
> 的键映射唯一真源是那张 `(wire 键, app_settings 键, Settings 字段, 类型)` 四元组表
> （`intel/service.py:521-536`），`resolve_intel_settings` 也只认它。表不扩 ⇒ 请求 200、
> 面板上开关照动、`Settings` 上的值**永远不变**——用户在设置卡上关掉核查轮，系统照跑照
> 降级。那张表自己的注释逐字写着这件事：「避免『面板上改得动、系统永远不读、而且不报错』
> 这类假面板」。G-18 用**逐键**往返断言冻结（写进去、`resolve_intel_settings` 读出来、
> 逐个比对），**不是**断言「请求不报错」。

**#1 `GET /intel/{instId}` 的 wire 扩键**（`intel_item_out`，E-16）：
`citeCount` / `citeEntryCount` / `settledCount` / `winCount` / `netPnlUsd` / `lastCitedTs` /
`verdict` / `verdictTs` / `verdictNote` / `verifyCount`。
`summary` 追加 `citations` 与 `verify` 两块。

**响应模型纪律**：v1 终审记过一条「`response_model` 静默吞新 wire 键」的教训。本轮所有新键
必须**同时**出现在 `schemas` 的响应模型里；`test_ai_intel_api.py` 增一条用例，
断言 `GET /intel/{instId}` 的 JSON 里这 10 个键**逐个存在**（而不是断言它「不报错」）。

### 4.2 WS

无新事件类型。`intel` 事件与 `snapshot.intel` 的 `summary` 扩两块（§3.6）。
**已实读确认前端零改动即可带上新键**：`store.ts:1107` 的 `setAiTradeIntelSummary` 是**整对象替换**
（`{...s.aiTradeIntel, [instId]: summary}`），而它的两个上游同样是整对象透传 ——
`useAiTradeGroupStream.ts:294` 的快照路径写的是 `(snap as {intel?…}).intel ?? null`，
`:325` 的 `case "intel"` 直接把事件负载交给同一个 setter（注释原文：「两处各写一份必漂移」）。
本仓库「`store.ts` 逐键手抄会静默吞 WS 新键」的教训在这条通路上**不成立**。
但用例仍要冻结这件事：`useAiTradeGroupStream.test.ts` 增一条，喂一个带 `citations`/`verify`
两块的 summary，断言它们**原样**出现在 store 里 —— 否则某次重构把整对象换成逐键解构时，
唯一的症状会是「界面上那两块永远是 0」，而不是一条报错。

---

## 五、数据模型

### 5.1 新表 `ai_intel_citations`（`create_all` 即建，零迁移）

```python
class AiIntelCitation(Base):
    """交易 Agent 对一条情报的一次引用，及其（可能的）往返结算结果。append-only + 一次结算 UPDATE。

    为什么是新表而不是往 `ai_intel_items` 里塞 JSON：引用是**事件流**（一条情报会被引用几十次），
    条目是**状态**。把事件流塞进状态行的 JSON 列，会让每一次引用都变成一次「读-改-写」，
    而情报轮与决策轮跑在不同的 DB 会话里 —— 读改写会丢更新，且丢更新不会报错。
    """
    __tablename__ = "ai_intel_citations"
    __table_args__ = (
        Index("ix_ai_intel_cite_item", "item_id", "id"),
        Index("ix_ai_intel_cite_scope", "inst_id", "cited_ts"),
        Index("ix_ai_intel_cite_epoch", "run_id", "position_seq"),
        Index("ix_ai_intel_cite_claim", "run_id", "cl_ord_id"),   # P0-1 的跨轮认领
    )

    id:            Mapped[int]   = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id:       Mapped[int]   = mapped_column(Integer, index=True, default=0)
    inst_id:       Mapped[str]   = mapped_column(String(32), index=True, default="")
    # **Integer 不是 String**（评审 P2-1）：`AiTradeSession.run_id` 是 `int`（`session.py:206,221`）。
    run_id:        Mapped[int]   = mapped_column(Integer, index=True, default=0)
    round_no:      Mapped[int]   = mapped_column(Integer, default=0)
    cited_ts:      Mapped[int]   = mapped_column(Integer, default=0)   # 墙钟秒 UTC
    ref_token:     Mapped[str]   = mapped_column(String(4), default="")  # "I01"
    # P0-1：限价开仓单会 resting 若干轮，成交由盯市 tick 上另一条 `_dispatch` 发出。下单那一刻
    # 冻结委托号，成交时按它认领 —— 逐行镜像 `AttributionLedger.claim_estimate/_take_estimate`。
    # 市价单恒为空串，走「回退本轮待写行」那条路。
    cl_ord_id:     Mapped[str]   = mapped_column(String(64), default="")
    role:          Mapped[str]   = mapped_column(String(8), default="hold")  # entry|exit|hold
    proposed_action: Mapped[str] = mapped_column(String(16), default="")  # 模型真实意愿（门禁前）
    final_action:  Mapped[str]   = mapped_column(String(16), default="")  # 八道门禁之后
    traded:        Mapped[bool]  = mapped_column(Boolean, default=False)
    position_seq:  Mapped[int]   = mapped_column(Integer, default=0)   # 0 = 未绑定持仓周期
    settled:       Mapped[bool]  = mapped_column(Boolean, default=False)
    settled_ts:    Mapped[int]   = mapped_column(Integer, default=0)
    # **可空**：`None` = 结算不可得 ⇒ outcome="void"。绝不用 0.0 冒充「没结算」（§3.2.2）
    net_pnl_usd:   Mapped[float | None] = mapped_column(Float, nullable=True)
    hold_s:        Mapped[int]   = mapped_column(Integer, default=0)
    exit_source:   Mapped[str]   = mapped_column(String(24), default="")
    outcome:       Mapped[str]   = mapped_column(String(8), default="")  # win|loss|flat|void
    created_ts:    Mapped[int]   = mapped_column(Integer, default=0)
```

### 5.2 `ai_intel_items` 补 10 列（**必须 ALTER**）

| 列 | 类型 | 默认 | 语义 |
|---|---|---|---|
| `cite_count` | INTEGER | 0 | 被引用总次数（含 hold 轮） |
| `cite_entry_count` | INTEGER | 0 | 作为**入场**依据被引用的次数 |
| `settled_count` | INTEGER | 0 | 已结算且非 `void` 的引用数 |
| `win_count` | INTEGER | 0 | 已结算且 `win` 的引用数 |
| `net_pnl_usd` | FLOAT | 0.0 | 已结算净额累加（可负） |
| `last_cited_ts` | INTEGER | 0 | 最近一次被引用的墙钟秒 |
| `verdict` | VARCHAR(12) DEFAULT '' | `''` | `''`\|`confirmed`\|`refuted`\|`unresolved` |
| `verdict_ts` | INTEGER | 0 | 最近一次判定时刻 |
| `verdict_note` | VARCHAR(200) DEFAULT '' | `''` | 模型给的一句话理由（≤120 字，留余量） |
| `verify_count` | INTEGER | 0 | 已核查次数（P3 重试的上限判据） |

### 5.3 `ai_intel_watch` 补 2 列

| 列 | 类型 | 默认 | 语义 |
|---|---|---|---|
| `last_verify_ts` | INTEGER | 0 | 最近一次核查轮完成时刻 |
| `curates_since_verify` | INTEGER | 0 | 距上次核查已跑过几次整理（`_verify_due` 的判据） |

### 5.4 迁移：第五个 patcher（`db/session.py`）

```python
async def _ensure_ai_intel_columns(conn) -> None:
    """老库幂等补列（ai-trade-intel-closed-loop §5.2/§5.3）：``ai_intel_items`` 与
    ``ai_intel_watch`` 是 52f466e 建的**既有**表，``create_all`` 不会为它们补列——须此处
    ``ALTER TABLE ADD COLUMN`` 幂等补齐（仿 ``_ensure_trade_risk_columns``），否则老库
    INSERT/SELECT 立即 ``no such column``、连既有的情报读取一并崩。

    新库同样安全：``create_all`` 先建全列表，``PRAGMA table_info`` 求差集后无一需要补。
    """
    item_columns = {
        "cite_count": "INTEGER DEFAULT 0", "cite_entry_count": "INTEGER DEFAULT 0",
        "settled_count": "INTEGER DEFAULT 0", "win_count": "INTEGER DEFAULT 0",
        "net_pnl_usd": "FLOAT DEFAULT 0.0", "last_cited_ts": "INTEGER DEFAULT 0",
        "verdict": "VARCHAR(12) DEFAULT ''", "verdict_ts": "INTEGER DEFAULT 0",
        "verdict_note": "VARCHAR(200) DEFAULT ''", "verify_count": "INTEGER DEFAULT 0",
    }
    watch_columns = {
        "last_verify_ts": "INTEGER DEFAULT 0", "curates_since_verify": "INTEGER DEFAULT 0",
    }
    for table, columns in (("ai_intel_items", item_columns), ("ai_intel_watch", watch_columns)):
        rows = (await conn.execute(text(f"PRAGMA table_info({table})"))).all()
        existing = {row[1] for row in rows}
        for name, sql_type in columns.items():
            if name not in existing:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}"))
    await conn.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_ai_intel_verdict ON ai_intel_items(inst_id, verdict)"))
```

在 `init_db` 的 `run_sync(create_all)` 之后、`_seed_*` 之前追加
`await _ensure_ai_intel_columns(conn)`（**第五行**，紧跟 `_ensure_live_auto_group_columns`）。

> ⚠️ **两条不可省的实现约束**：
> 1. SQLite 的 `ALTER TABLE ADD COLUMN` 只接受**常量**默认值——上表全部是常量，合规。
> 2. 补列后的**老行**默认值由 SQLite 填充，但历史条目的 `verdict` 会是 `''`（=待验证），
>    这正是我们要的语义；**绝不做任何历史回填**（沿用 `position_pct` 那条「旧库自然补 NULL，
>    不做不可靠历史回填」的成文纪律）。

### 5.5 仓库层（新文件 `db/ai_intel_citation_repositories.py`）

`db/ai_intel_repositories.py` 实读 610 行，再加引用 / 影响力 / 信源聚合三组查询必然越 800 线。
故新开一个文件（与 `ai_intel_repositories` 平级），并**从 `db/repositories.py` 再导出**
（E-9 的既有姿势，导入面单一）。

| 函数 | 签名要点 | 说明 |
|---|---|---|
| `intel_citation_out(row)` | → dict | camelCase wire 的**唯一**装配点 |
| `append_intel_citations(session, rows, *, commit=True)` | 批量 INSERT + 同事务累加 `cite_count`/`cite_entry_count`/`last_cited_ts` | 累加用 `UPDATE … SET x = x + :n`，**不读改写** |
| `promote_intel_citations_to_entry(session, *, run_id, cl_ord_id, position_seq, commit=True)` | 批量 UPDATE `role="entry"` + `position_seq` + 补加 `cite_entry_count` | **P0-1 的落库侧**：限价开仓在成交轮才知道 `seq`，而那时引用行早已 INSERT 过。只更新 `role="hold"` 的行（幂等：部分成交重复调用不二次累加 `cite_entry_count`） |
| `settle_intel_citations(session, *, run_id, position_seq, net_pnl_usd, hold_s, exit_source, outcome, now, commit=True)` | 批量 UPDATE + 累加 `settled_count`/`win_count`/`net_pnl_usd` | 只更新 `settled=0 且 role in (entry,exit)` 的行（幂等：重复调用不二次累加）。**`position_seq` 的实参只能是 `trip.seq`**（★ 评审 P0-2）；`net_pnl_usd=None` ⇒ 写 `settled=True, outcome="void", net_pnl_usd=NULL` 且**不动**四个结算统计列 |
| `read_intel_impact(session, inst_id, *, limit)` | → dict | 三张读数卡 + 两榜 + 未引用计数，**一次 SQL 聚合**（不在 Python 里遍历全库） |
| `list_intel_citations(session, *, inst_id, item_id=None, limit, before=None)` | → (rows, has_more) | 游标分页 |
| `list_intel_source_rows(session, inst_id)` | → list[dict] | 域名聚合；`sources_json` 需在 Python 侧解析（SQLite 无 JSON 聚合保证），**只扫 active + archived，不扫全表** |
| `apply_intel_verdicts(session, plans, *, now, commit=True)` | 批量 UPDATE | 每条按 §3.3.4 的计划写 verdict / tier / status / confidence / **同事务重算 `expires_ts`** |
| `list_intel_feedback(session, inst_id="")` | → list[dict] | #13 |
| `list_intel_trusted_domains(session, inst_id="")` | → list[str] | **评审 P1-7**：与既有 `list_intel_blocked_domains` 逐字同构，只把 `action == "block"` 换成 `"trust"`。没有它，`trust` 就是第二个「有写点没读点」 |
| `count_items_all_sources_blocked(session, inst_id, blocked)` | → int | #15b 的 preview |
| `prune_intel_citations(session, *, before_ts, commit=True)` | → int | G-13；**只删明细，不回滚统计列** |

`prune_intel_citations` 的调用点：`run_curate_round` 的写段末尾（每次整理轮顺手裁一次），
`before_ts = now - cite_retention_d * 86400`。

---

## 六、文件变更清单

### 6.1 后端（新增 6 / 修改 14）

| # | 文件 | 新/改 | 一句话意图 |
|---|---|---|---|
| 1 | `services/quant/app/ai_auto/intel/citation.py` | 新 | **纯核心**：`CitationResolution` / `resolve_citations` / `classify_outcome` / `REF_RE`（≈110 行） |
| 2 | `services/quant/app/ai_auto/intel/attribution.py` | 新 | 每会话一份的 `IntelAttribution`：六个方法 + `stats()`（≈240 行） |
| 3 | `services/quant/app/ai_auto/intel/verify.py` | 新 | **纯核心**：`plan_verification` 候选选取 + `review_verdicts` 复核表 + `VerdictPlan`（≈260 行） |
| 4 | `services/quant/app/ai_auto/intel/service_verify.py` | 新 | `run_verify_round` 七步编排 + `_persist_verify` 一次提交（≈220 行） |
| 5 | `services/quant/app/db/ai_intel_citation_repositories.py` | 新 | §5.5 的 10 个函数（≈420 行） |
| 6 | `services/quant/app/api/schemas_intel_closed_loop.py` | 新 | 新增 8 个 Pydantic 出入参模型（避免把 `schemas.py` 推过 800 线；`api/ai_intel.py` 从这里 import） |
| 7 | `services/quant/app/ai_auto/intel/disclosure.py` | 改 | `build_digest(cite=…)` + `render_line(ref=…)` + `refs` 进 wire + 过滤 `verdict=="refuted"` |
| 8 | `services/quant/app/ai_auto/intel/gate.py` | 改 | `score_item(item, *, now, limits=None)`（**带默认值**，四个既有调用点零改动，评审 P1-1）；`review_actions` 加「盈利条目 archive 的举证责任」；`plan_cleanup(..., blocked_domains)` 第 5 步（**必填 keyword-only**，评审 P1-8）+ `CleanupPlan.blocked_ids` 与 `counts["blocked_sweep"]`；`solve_confidence(..., has_trusted=False)` 第 6 形参（评审 P1-7）；`IntelItem` 加 10 个字段 |
| 9 | `services/quant/app/ai_auto/intel/prompts.py` | 改 | 追加 `build_verify_system_prompt` / `build_verify_user_prompt` / `VERIFY_TOOL_*`（≈95 行） |
| 10 | `services/quant/app/ai_auto/intel/search.py` | 改 | `BraveProvider` / `SerperProvider` / `_parse_relative_date`；`PROVIDER_SLUGS` 扩、`PLANNED_SLUGS` 清空；`resolve_search_provider` 两分支 |
| 11 | `services/quant/app/ai_auto/intel/service.py` | 改 | `item_from_wire` 带 10 个新字段（**`item_to_payload` 一个都不带**，评审 P1-6）；`IntelDeps` 加 `verify_llm`；`_verify_due`；`estimate_daily_cost` 含核查轮；**`INTEL_SETTING_BRIDGE` 补 8 行**（评审 P0-5）；`run_curate_round` 的只读会话补取黑名单（评审 P1-8）；`run_collect_round` 补取信任清单（评审 P1-7）；整理轮末尾调 `prune_intel_citations` 与存量扫除。**降级码文案不在本文件**——它在 `taxonomy.DEGRADE_TEXT`（评审 P1-4） |
| 12 | `services/quant/app/ai_auto/intel/runtime.py` | 改 | `run_verify` 镜像 `run_curate`；`build_snapshot`/`read_summary` 装 `citations`/`verify` 两块 |
| 13 | `services/quant/app/ai_auto/intel/scheduler.py` | 改 | `_tick` 里**唯一**一处：验证轮排在整理轮之前 |
| 14 | `services/quant/app/ai_auto/intel/__init__.py` | 改 | 导出面新增 `IntelAttribution` / `run_verify_round` / `resolve_citations` |
| 14a | `services/quant/app/ai_auto/intel/taxonomy.py` | 改 | **评审 P1-2/P1-4**：`IntelLimits` 补 6 项（`cite_score_bonus` / `verify_min_age_h` / `verify_recheck_h` / `verify_max_attempts` / `verify_max_items` / `verify_confirm_bonus` + `verify_refute_penalty`，共 7 个字段但沿用同一段 `__post_init__`）与 `limits_from_settings` 的对应映射行；`DEGRADE_TEXT` 补 4 个新码的中文文案 |
| 14b | `services/quant/app/ai_auto/intel/llm.py` | 改 | **评审 P1-3**：`build_intel_llm_client(..., *, max_tokens_override: int \| None = None)`（缺省即今日行为、既有调用点零改动），供装配点造**第二个** client 放进 `IntelDeps.verify_llm` |
| 14c | `services/quant/app/ai_auto/intel/corpus.py` | 改 | **评审 P1-7**：`build(..., trusted_domains: frozenset[str] = frozenset())`，**只**用在 `sorted(...)` 的 key 上打破并列，截断规则一个字节不改 |
| 15 | `services/quant/app/ai_auto/prompts.py` | 改 | `build_decision_tool_schema(allow_intel_refs=…, intel_max_refs=…)` + 早退判据 + `intelRefs` 属性（≈25 行） |
| 16 | `services/quant/app/ai_auto/prompts_en.py` | 改 | 英文工具描述追加同义一句 |
| 17 | `services/quant/app/ai_auto/reasoning_protocol.py` | 改 | 可写字段目录补 `intelRefs` 一行 |
| 18 | `services/quant/app/ai_auto/decider.py` | 改 | `TradeDecision.intel_refs` + `_resolve_intel_refs` + 两条 return 都带上 + `allow_intel_refs`/`intel_max_refs` 构造参 |
| 19 | `services/quant/app/ai_auto/session.py` | 改 | **六处薄接线**：构造 `IntelAttribution`；`note_decision`；`note_executed(..., resting=self.executor.resting)`；`_dispatch` 开/平两钩子 + `persist_pending`；`_run_decision_round` 的 `finally` 里 `end_round()`（评审 P1-9，与 `self._cost.end_round()` 同处）；`tool_input["intelRefs"]`；`aiStats.intel` 合并在 `_build_run_metrics`（评审 P2-9）。**零纯核心** |
| 20 | `services/quant/app/api/ai_intel.py` | 改 | 新增 11 个端点（#13-#23），既有 #1/#2 扩键 |
| 21 | `services/quant/app/db/models.py` | 改 | 新表 `AiIntelCitation` + 两表补列声明；`AiIntelRound` 的 docstring 与 `kind` 行内注释补 `verify`（评审 P2-6，`String(8)` 无须改类型） |
| 22 | `services/quant/app/db/session.py` | 改 | 第五个 patcher `_ensure_ai_intel_columns` + `init_db` 里一行调用 |
| 23 | `services/quant/app/db/ai_intel_repositories.py` | 改 | `intel_item_out` 加 10 键；**`bump_intel_watch` 的 kind 分支改成显式三分 + `curates_since_verify` 的唯一自增点 + `verify_reset` 形参**（评审 P0-3，二值 catch-all 会让核查轮改写 `last_curate_ts`）；`intel_watch_out` 加 2 键；`list_intel_trusted_domains` 新增（评审 P1-7）。**`_item_values` 一个新列都不加**（评审 P1-6） |
| 24 | `services/quant/app/db/repositories.py` | 改 | 再导出新仓库文件的 10 个函数 |
| 25 | `services/quant/app/config.py` | 改 | 17 项新设置 + 一条 `verify_max_items × verify_docs_per_item ≤ corpus_max_docs` 的启动期校验器 |
| 26 | `services/quant/app/api/app_settings.py` | 改 | `PUT/GET /settings/intel` 露出 8 个新键；provider enum 扩 |

### 6.2 前端（新增 6 / 修改 8）

| # | 文件 | 新/改 | 意图 |
|---|---|---|---|
| 27 | `apps/web/components/Intel/IntelAttributionRail.tsx` | 新 | 归因轨（≈90 行） |
| 28 | `apps/web/components/Intel/IntelVerdictBadge.tsx` | 新 | 四态判定徽章 + 「我不同意」（≈120 行） |
| 29 | `apps/web/components/Intel/IntelImpactTab.tsx` | 新 | 影响力页签（≈210 行） |
| 30 | `apps/web/components/Intel/IntelSourcesTab.tsx` | 新 | 信源页签（≈200 行） |
| 31 | `apps/web/components/Intel/IntelBlockSourceDialog.tsx` | 新 | 「不再采纳此源」确认框（含受影响条数与作用域勾选，≈130 行） |
| 32 | `apps/web/lib/intelClosedLoop.ts` | 新 | 四个纯文案函数（`verdictLabel` / `attributionText` / `netPnlTone` / `sourceStateLabel`） |
| 33 | `apps/web/components/Intel/IntelCard.tsx` | 改 | 插入归因轨 + 徽章 + `refuted` 的降透明度与警示行 |
| 34 | `apps/web/components/Intel/IntelSourceChips.tsx` | 改 | 每个 chip 加 hover `✕` |
| 35 | `apps/web/components/Intel/IntelModal.tsx` | 改 | 页签从 5 扩到 7；两个新页签的取数与轮询 |
| 36 | `apps/web/components/Intel/IntelRoundsTab.tsx` | 改 | `kind` 过滤三值化 + 核查轮行摘要 |
| 37 | `apps/web/components/Settings/IntelSourceSettings.tsx` | 改 | provider 下拉扩三项 + 三把单键开关 + 健康读数行 + `estimate` 含验证轮 |
| 38 | `apps/web/lib/intelTypes.ts` | 改 | 与 `intel_item_out` 逐键同构（E-16） |
| 39 | `apps/web/lib/intelApi.ts` | 改 | 7 个新方法 |
| 40 | `apps/web/lib/intel.ts` | 改 | 既有文案表补 `verify` 相关 |

### 6.3 桌面壳（修改 0）

**本轮零改动，且已实读确认为什么可以零改动**：`desktop/src/lib/config.js:80` 的透传判据是
`key.startsWith('AGENT_') || key.startsWith('LIVE_') || key.startsWith('AI_TRADE_') || …` ——
它是**按前缀**而不是按键名逐个列举的，17 项新配置沿用 `AI_TRADE_INTEL_` 前缀 ⇒ 打包后端
自动拿到，无需改白名单。

但**必须有一条用例把这件事钉住**（G-11）：`desktop/scripts/config-passthrough.test.mjs`
断言 `AI_TRADE_INTEL_VERIFY_ENABLED` 这样一个具体的新键确实穿过了那段过滤逻辑。
理由是这条结论完全依赖「白名单是前缀式的」这一个事实——一旦有人把它改成逐键枚举
（那是一次看起来更安全的重构），17 项配置会在打包后端里**同时静默消失**，症状是
「桌面端上验证轮永远不跑，而开发机上一切正常」。

### 6.4 文档（修改 3）

| # | 文件 | 意图 |
|---|---|---|
| 41 | `.env.example` | 追加 17 行；家族标题 `34 项` → `51 项`（E-19） |
| 42 | `.claude-index/config.md` | §十二 的阈值偏离逐条更新 + 新登记 |
| 43 | `docs/plans/ai-trade-market-intel/spec.md` | 在《登记为 v1.1 待办》一节下追加一行：「已由 ai-trade-intel-closed-loop 兑现」，并指向本文档。**不改该文档任何既有内容** |

---

## 七、测试与验收

### 7.1 后端 pytest（新增 8 文件 / 修改 4）

| 文件 | 关键用例（每条对应一个 G 或一条本设计的成文纪律） |
|---|---|
| `tests/test_ai_intel_citation.py`（新） | `test_unknown_ref_is_dropped_and_counted`（G-3）；`test_missing_refmap_makes_every_ref_unknown`（§3.1.3 的刻意选择）；`test_classify_outcome_none_is_void_not_flat`（§3.2.2）；`test_ref_regex_rejects_lowercase_and_three_digits` |
| `tests/test_ai_intel_disclosure_cite.py`（新） | `test_cite_off_renders_byte_identical_to_v1`（G-1，sha256 冻结）；`test_ref_tokens_are_contiguous_and_match_wire`（G-2）；`test_refuted_item_is_not_injected`（G-6 的注入侧）；`test_budget_accounts_for_ref_prefix` |
| `tests/test_ai_intel_attribution.py`（新） | `test_entry_citation_settles_with_trip_net_pnl`（G-4）；`test_hold_citation_never_settles`；`test_settle_is_idempotent_on_repeated_close`；`test_attribution_off_writes_nothing`（G-5）；`test_persist_failure_is_counted_not_silent`（§3.2.1 的异常纪律）。**评审追加四条**：`test_limit_open_filled_in_a_later_round_still_marks_entry`（G-15 / P0-1，**必须走 resting 通路**：第 N 轮下单、`persist_pending` 已跑过、第 N+3 轮才 `note_open`）；`test_settle_uses_trip_seq_not_session_counter`（P0-2，构造 `self._position_seq` 已 +1 的现场，断言结算仍命中该往返）；`test_none_trip_settles_as_void_not_pending`（§3.2.2）；`test_end_round_drops_pending_and_counts_it_but_keeps_claims`（P1-9，且断言跨轮认领槽**不被清**） |
| `tests/test_ai_intel_verify.py`（新） | `test_scheduled_event_past_due_ranks_first`（候选 P1 级）；`test_pinned_item_is_still_a_candidate`（G-8 的前半）；`test_refuted_without_refs_becomes_unresolved`（G-7）；`test_duplicate_item_ref_keeps_first`；`test_unresolved_does_not_overwrite_confirmed`；`test_c_ref_is_not_accepted_as_d_ref`（R-11）。**评审追加三条**：`test_cleared_verdict_item_is_not_reverified`（G-17 / P0-4，连跑三轮都不进候选）；`test_refuted_item_reenters_pool_for_the_second_strike`（P1-14）；`test_first_refuted_plans_no_tier_change`（P1-12 的纯核心侧） |
| `tests/test_ai_intel_verify_round.py`（新） | `test_first_refuted_stops_injection_without_touching_tier`（G-6 前半 / P1-12）；`test_second_refuted_demotes_and_recomputes_expires`（**跨轮**：两击之间推过 `verify_recheck_h`，再把时钟推过原 `expires_ts`，断言它按新 tier 存活，G-6 后半 + v1 不变式 I-3）；`test_refuted_never_archives_even_at_ultra`（P1-12）；`test_protected_refuted_item_keeps_tier_but_stops_injecting`（G-8）；`test_empty_corpus_marks_unresolved_not_failed`；`test_no_candidates_writes_no_round_row`。**评审追加四条**：`test_verify_round_does_not_touch_last_curate_ts` 与 `test_verify_every_n_curates_two_fires_on_the_second_curate`（G-16 / P0-3）；`test_verify_round_self_checks_daily_cap`（P1-5）；`test_verify_round_refreshes_snapshot_exactly_once`（P2-7）；`test_verify_prompt_omits_stance_and_confidence` 与 `test_verify_prompt_carries_no_position_facts`（P1-11 / §3.8，源码扫描 + 行为断言双保险） |
| `tests/test_ai_intel_feedback_api.py`（新） | `test_block_archives_items_whose_every_source_is_blocked`（G-9②）；`test_block_keeps_items_with_one_clean_source`；`test_unblock_does_not_revive`（G-9③）；`test_preview_writes_nothing`（#15b） |
| `tests/test_ai_intel_search_providers.py`（新） | `test_brave_parses_page_age_rfc3339`；`test_serper_parses_relative_days_ago`；`test_serper_unparseable_date_is_zero_not_now`（G-14）；`test_freshness_days_maps_to_provider_tokens` |
| `tests/test_ai_intel_migration.py`（新） | `test_old_db_gets_new_columns`（G-10：用 52f466e 的建表 DDL 造一张缺列的表，跑 `init_db`，断言 12 列全在且读写不抛）；`test_patcher_is_idempotent_on_second_start`。**评审追加一条**：`test_reupsert_does_not_reset_citation_stats`（G-20 / P1-6，先写满统计列，再走一次 `upsert_intel_items` 的冲突分支，逐列断言不变） |
| `tests/test_ai_trade_decision_intel_refs.py`（新） | `test_schema_identity_when_cite_off`（G-1，`is DECISION_TOOL_SCHEMA`）；`test_intel_refs_property_appears_only_when_allowed`；`test_both_return_paths_carry_intel_refs`（开仓的提前 return 与末尾 return 各一条——这正是 v1 `contrarian_evidence` 栽过的那一行） |
| `tests/test_ai_intel_wiring.py`（改） | 追加：`note_decision`/`note_open`/`note_close` 三个钩子在 `session` 里**确有调用点**（源码扫描 + 行为断言双保险）；`run_verify` 在 `scheduler._tick` 里排在 `run_curate` **之前**；`_shutdown_steps` 第 7 步不变 |
| `tests/test_ai_intel_api.py`（改） | 10 个新 wire 键**逐个存在**（§4.1 的 `response_model` 纪律）；11 个新端点各一条正常路径 + 一条 404/422。**评审追加两条**：`test_clear_verdict_keeps_verify_count_and_sets_user_edited`（P0-4）；`test_manual_item_verify_bypasses_candidate_filters`（P2-11，用一条 `origin="user"` 且 `user_edited=True` 的条目验证它确实被受理） |
| `tests/test_ai_intel_gate.py`（改） | `test_cite_bonus_moves_profitable_item_up`；`test_losing_cited_item_is_not_penalised_below_uncited`（§3.2.4 的成文选择）；`test_overflow_eviction_actually_reads_the_bonus`（防「用例绿着但分支没被走到」）。**评审追加三条**：`test_score_item_without_limits_is_byte_identical_to_today`（P1-1 的零回归判据）；`test_plan_cleanup_requires_blocked_domains_explicitly`（P1-8，少传即 `TypeError`）；`test_trusted_domain_does_not_raise_domain_count`（§3.4.2 的红线） |
| `tests/test_ai_intel_service.py`（改） | `estimate_daily_cost` 含核查轮；`prune_intel_citations` 在整理轮末尾被调用且只删明细。**评审追加两条**：`test_every_new_intel_setting_key_round_trips_through_the_bridge`（G-18 / P0-5，**逐键**写入→`resolve_intel_settings` 读出→比对，共 8 个键）；`test_every_new_degrade_code_has_copy`（G-19 / P1-4，四个码逐个断言不等于兜底串） |

### 7.2 前端 vitest（新增 5 / 修改 4）

| 文件 | 关键用例 |
|---|---|
| `IntelAttributionRail.test.tsx`（新） | 零引用 ⇒ 整行不渲染；负净额用 `--down` 配色；title 口径文案完整 |
| `IntelVerdictBadge.test.tsx`（新） | 四态四文案；`verdict===""` 不渲染；「我不同意」触发 `clearVerdict` PATCH |
| `IntelImpactTab.test.tsx`（新） | 四种空态各自文案不同（**逐条断言文案不相等**，防四段文案被复制成同一段） |
| `IntelSourcesTab.test.tsx`（新） | block / trust / 撤销三条路径；确认框显示受影响条数 |
| `IntelBlockSourceDialog.test.tsx`（新） | 不勾选「对所有标的生效」⇒ 请求带 `instId`；勾选 ⇒ 不带 |
| `IntelModal.test.tsx`（改） | 7 个页签都可达；**read mock 必须按 `status`/路径入参分发**（v1 终审的成文教训：不分发的 mock 会让用例空跑通过） |
| `IntelCard.test.tsx`（改） | `refuted` ⇒ 出现警示行且卡片带降透明度类名 |
| `IntelRoundsTab.test.tsx`（改） | `kind="verify"` 的行摘要；`verdict_no_ref>0` 时那一行灰字出现 |
| `intelClosedLoop.test.ts`（新） | 四个纯函数的边界（`netPnlTone(0)` 是 muted 而不是 up） |

### 7.3 桌面 node:test（新增 1）

`desktop/scripts/config-passthrough.test.mjs` —— G-11。
**必须能被直跑**：`node --test desktop/scripts/config-passthrough.test.mjs`
（`desktop/package.json` 的 `node --test "scripts/*.test.mjs"` 依赖 Node 21+ 的原生 glob 展开
而 CI 钉的是 20 —— v1 已记录过这条，PR 描述里给出直跑命令）。

### 7.4 手工验收清单（真实网络 + 真实 key）

| # | 步骤 | 期望 |
|---|---|---|
| A-1 | 配 tavily key，起一组 AI 自动交易，等一轮采集完成 | 情报卡上**没有**归因轨（还没被引用过），有「待验证」或无徽章 |
| A-2 | 等一轮决策 | 左栏决策卡显示「本轮采信 I01、I03」；对应两张情报卡出现「引用 1」 |
| A-3 | 等一次开仓成交 | 那两张卡的「入场」计数 +1 |
| A-4 | 等这笔仓平掉 | 两张卡出现「已结算 1 · 胜/负 · 净额 ±X」，且 X 与该笔往返在成交明细里的净额**数值相同** |
| A-5 | 手动 `POST /intel/{instId}/verify` | 审计页签出现一条「核查 N 条 → …」；被判 refuted 的条目立刻出现红色徽章与警示行 |
| A-6 | 打开「影响力」页签 | 三张读数卡与贡献榜非空；「从未被引用」的解释文案在位 |
| A-7 | 在某条情报的来源 chip 上点 ✕ | 确认框显示受影响条数；确认后该域名进「信源」页签的黑名单，受影响条目进「已归档」 |
| A-8 | 把 provider 切到 `serper` 并填 key，点探测 | 探测通过；下一轮采集的审计行 `search_provider` 显示 `serper` |
| A-9 | `AI_TRADE_INTEL_CITE_ENABLED=false` 重启 | 决策 prompt 的情报段**没有** `[Ixx]` 前缀；工具 schema 无 `intelRefs`；已有的统计列原样保留、卡片照常显示 |
| A-10 | 关掉 AI 自动交易，重开 | 全部引用统计、verdict、黑名单**原样还在**（持久化验收，v1 G-4 的延伸） |
| **A-11** | 把订单类型留在默认（限价），等一次**挂了几轮才成交**的开仓 | 该轮引用的情报卡上「入场」计数照样 +1（P0-1；这条必须真的等到一次 resting 成交，用市价单验不算数） |
| **A-12** | 在设置卡上关掉「核查轮」，等两个整理周期 | 审计页签**不再出现**任何 `kind=verify` 的行（P0-5：这一条验的是那三把单键不是假开关） |
| **A-13** | 对一条被判「已证伪」的情报点「我不同意」，再等三轮核查 | 它**不再**被自动重判；徽章保持清空、注入恢复（P0-4） |
| **A-14** | 记下弹窗上的「上次整理」时刻，手动触发一次核查轮 | 「上次整理」**一秒都没变**，新增的是「上次核查」（P0-3） |

### 7.5 回归门禁

- 后端全量 `python -u -m pytest`（**不并发**，仓库的 `services/quant` 用例共用一个临时 DB）。
  已知红基线：`test_factor_library_expansion.py` 的 7 条（未跟踪的在制文件，与本轮无关）——
  交付说明里必须**先跑一次基线**再跑一次改后，两次的红集合逐条比对。
- 前端 `NODE_ENV=test npx vitest run`（组件用例缺这个环境变量会报 React `act()` 错）+ `tsc --noEmit`。
- `ruff check app tests --select F,E9` 必须干净（v1 终审在这上面栽过一次 F811）。

---

## 八、风险与缓解

| # | 风险 | 等级 | 缓解 |
|---|---|---|---|
| **R-1** | **引用数据被误读成因果。** 「引用了这条情报的交易赚了 18 USDT」不等于「这条情报值 18 USDT」——同一轮里还有信号板、因子、体制读数在起作用 | 高 | ①UI 文案一律用「**关联**净额」而非「贡献」「带来」；②影响力页签顶部常驻一句口径说明：「这些数字是**共现**统计，不是因果归因：一笔交易通常同时采信了多条证据。」③`net_pnl_usd` 不参与任何自动淘汰的**唯一**判据（只作为 `score_item` 的一个有界加分项，上限 `cite_score_bonus`） |
| **R-2** | **模型学会为了「看起来有据」而滥引用。** 引用是零成本的，模型可能把每条都写进 `intelRefs` | 中 | ①`maxItems` 硬顶 8；②工具描述明说「没采信就留空」；③审计侧可观测：`ai_intel_rounds` 之外，`aiStats.intel.cited / rounds` 的比值一旦长期贴近 8，就是滥引用的信号 —— 这条**只观测不自动处置**（自动处置会变成一个新的、没人评审过的门禁） |
| **R-3** | **验证轮把「没找到」判成「被推翻」。** 这是本轮最危险的失效模式：一次错误的 `refuted` 会让一条真情报停止注入 | 高 | ①协议 V3/V5 两次显式区分「没找到证据」与「找到反证」；②服务端硬闸：无 `D` 引用的非 `unresolved` 判定**一律降为 `unresolved`**（G-7）；③**两击制**：首击只停注入不动层级，隔 `verify_recheck_h` 的第二次独立核查仍判 `refuted` 才降一层，且**任何情形都不归档**（评审 P1-12，v1 原文的「已在 ultra ⇒ archived」与本条正面冲突，已删）；④受保护条目不降级；⑤用户一键「我不同意」，且该动作会**永久**把这条目移出自动候选池（评审 P0-4）；⑥**验证轮的 Agent 拿不到任何持仓与方向**（§3.8）；⑦**候选渲染不带 `stance`/`impact`/`confidence`**（评审 P1-11：把方向摆在核查员眼前再要求他别看方向，是用输入反驳指令） |
| **R-4** | **ALTER 迁移在真实生产库上失败。** 生产库在 `%APPDATA%/aragon-desktop/aragon.db`，用户不会有备份 | 高 | ①patcher 逐列判存在，天然幂等；②12 列全部是常量默认值（SQLite `ADD COLUMN` 的硬限制）；③`test_ai_intel_migration.py` 用 52f466e 的 DDL 造缺列库做真迁移；④失败时 `init_db` 抛错、后端起不来 —— **这比起来了但读写乱掉要好**，不加 `try/except` 吞掉 |
| **R-5** | **归因写库拖慢决策链。** 决策轮多一次 INSERT | 中 | ①落点在 `_emit_decision` 之后（界面先出）；②批量单条提交；③失败即丢弃并计数，不重试；④用例断言 `persist_pending` 不在 `_execute_decision` 之前被 await |
| **R-6** | **验证轮吃掉采集预算。** 日配额同时覆盖三种轮 | 中 | ①这是**刻意**的（§3.5）；②`estimate` 读数把验证轮算进去，设置卡上用户看得见；③`verify_every_n_curates` 可调到 12（≈一天一次） |
| **R-7** | **`serper` 的相对时间解析漂移。** `"3 days ago"` 的口径随地区/语言变 | 中 | 解析失败一律 0（`undated`），G-14 冻结；非英文文案落 0 而不是猜 |
| **R-8** | **`api/ai_intel.py` 越 800 行。** 实读 276 + 11 个端点 ≈ 470 | 低 | 出参模型外移到 `schemas_intel_closed_loop.py`；若仍逼近，把 `#13-#17`（信源治理族）整体外移到 `api/ai_intel_sources.py`。**§十二 必须登记复测值** |
| **R-9** | **`gate.py` 越 800 行。** 实读 611 + 三处改动 | 中 | 验证的复核表放 `verify.py` 而**不是** `gate.py`（本设计已如此安排）；若 `gate.py` 仍越线，把 `plan_cleanup` 家族整体外移到 `intel/cleanup.py` |
| **R-10** | **`intelRefs` 让决策 JSON 变长，挤占 tool 预算。** deep-reasoning-chain 刚为此把 `agent_max_tokens` 拆成三份 | 低 | 8 个 4 字符 token ≈ 60 字节，相对 `reason`(500) / `evidenceDigest`(400) 可忽略；但用例要断言 `maxItems` 确实进了 schema |
| **R-11** | **两处编号空间串号**（`I##` 与 `D##` 与 `C##`） | 中 | 三套正则互斥且各自锚定 `^…$`；`verify` 的候选用 `C`、语料用 `D`，用例 `test_c_ref_is_not_accepted_as_d_ref` |
| **R-12** | **回测车道的情报重放。** v1 已把它写成红线 | — | **本轮继续是红线**：`backtest.py` 不装配 `IntelAttribution`、不注入编号、不跑验证轮。历史 point-in-time 语料取不回来，接入必然前视偏差 |

---

## 九、实施顺序

每一步独立可验证、独立可提交；**顺序不得压缩**，理由写在括号里。

1. **迁移与模型先行**：`models.py` 新表 + 两表补列 → `db/session.py` 第五个 patcher →
   `test_ai_intel_migration.py`。（老库补列是唯一一处**做错了会毁数据**的改动，
   必须独立提交、独立验证，不与任何业务逻辑混在一起。）
2. **纯核心三件**：`citation.py` / `verify.py` + `gate.py` 的三处改动 + 对应四个测试文件。
   （无 IO、无 DB，全部可离线判定；这一步跑绿之前不碰任何编排。）
3. **仓库层**：`ai_intel_citation_repositories.py` + `repositories.py` 再导出 +
   `intel_item_out` / `intel_watch_out` 扩键。
4. **轴 A 接线**：`disclosure.py` → `prompts.py`/`prompts_en.py`/`reasoning_protocol.py` →
   `decider.py`。跑 G-1 的字节冻结用例。（**先做渲染与 schema，后做消费**：反过来做的话，
   `intelRefs` 会在一个还没有编号表的世界里被解析，用例会以「全部 unknown」的形态假绿。）
5. **轴 B 接线**：`attribution.py` → `session.py` 五处薄接线。跑 G-4 / G-5。
6. **轴 C**：`intel/prompts.py` 验证 prompt → `service_verify.py` → `runtime.run_verify` →
   `scheduler._tick`。跑 G-6 / G-7 / G-8。（`_tick` 是**最后一行**接线：它一接上，
   真实分组就会开始跑验证轮。）
7. **轴 D**：`search.py` 两个适配器 → REST #13-#17 → `plan_cleanup` 第 5 步。跑 G-9 / G-14。
8. **REST 与配置**：`api/ai_intel.py` 的 11 个端点 + `schemas_intel_closed_loop.py` +
   `config.py` 17 项 + `app_settings.py` + `.env.example`。跑 G-11。
9. **前端**：类型 → API 层 → 纯文案函数 → 三个展示组件 → 两个新页签 → `IntelModal` 装配。
10. **文档与登记**：`.claude-index/config.md` 阈值复测、v1 spec 的待办销账行、本文档的
    《实施过程发现的方案缺陷》一节（**发现即写，不留 TODO**）。
11. **全量回归**：先跑基线、再跑改后、逐条比对红集合；`ruff` / `tsc` / `node --test` 各一遍。

---

## 十、非目标（本轮明确不做）

1. **把归因做成门禁。** 不因为一条情报「历史胜率低」就拒绝注入它，也不因为「历史胜率高」
   就放大仓位。理由：样本量在几十笔的量级上，任何据此做的自动决策都是在噪声上做决策。
2. **跨标的情报关联。** v1 的非目标 4 原样保留（情报仍严格按 `instId` 隔离）。
   引用统计同样按标的隔离——同一条新闻在两个标的下是两条记录，各算各的账。
3. **多检索源扇出。** 三个适配器，**单活**。扇出要养三套限流语义、三套配额账、一套跨源去重
   与一套「哪个源该背这次失败的锅」的归因，收益却只是文档数从 16 变成 30。
4. **让模型自己写验证查询。** 查询由服务端从 `headline` 确定性构造（§3.3.2）。
5. **历史回填。** 补列后的老条目 `verdict=''`、统计列全 0，这就是它们真实的状态。
6. **把 `_format_*` 外移到 `prompt_sections.py`。** v1 已两次登记这条指示，本轮**仍不兑现**，
   理由不变：那次外移会触碰上百条 sha256 冻结断言，与本特性混在一起会让 G-1 的零回归判据
   失去说服力。**但本轮把它升级为「下一个触碰 `prompts.py` 的特性必须先还这笔债」**。
7. **回测车道的情报重放。** 见 R-12，红线。

---

## 十一、配置项清单（17 项新增，全部 `ai_trade_intel_` 前缀）

| 键 | 默认 | 约束 | 说明 |
|---|---|---|---|
| `ai_trade_intel_cite_enabled` | `True` | — | 轴 A 单键。`False` ⇒ 注入行无前缀、wire 无 `refs`、工具 schema **对象恒等**回退（G-1） |
| `ai_trade_intel_cite_max_refs` | `8` | `ge=1, le=16` | `intelRefs` 的 `maxItems`，同时是解析器的截断上限 |
| `ai_trade_intel_attribution_enabled` | `True` | — | 轴 B 单键。`False` ⇒ 引用照记（`cite_count` 仍加）、**不结算、不回写四个结算列** |
| `ai_trade_intel_cite_score_bonus` | `0.15` | `ge=0, le=1` | 注入排序里「被引用且盈利」的有界加分上限 |
| `ai_trade_intel_cite_retention_d` | `180.0` | `ge=7, le=3650` | 引用明细保留天数；到期由整理轮裁剪，**只删明细不回滚统计** |
| `ai_trade_intel_verify_enabled` | `True` | — | 轴 C 单键。`False` ⇒ 不跑验证轮、不写 verdict、UI 徽章整体不渲染 |
| `ai_trade_intel_verify_every_n_curates` | `1` | `ge=1, le=12` | 每几次整理轮跑一次核查 |
| `ai_trade_intel_verify_max_items` | `3` | `ge=1, le=10` | 单轮候选上限（同时是工具 schema 的 `maxItems`） |
| `ai_trade_intel_verify_docs_per_item` | `4` | `ge=1, le=12` | 每条候选检索几篇 |
| `ai_trade_intel_verify_min_age_h` | `12.0` | `ge=1, le=720` | 条目多老才值得核查（太新 ⇒ 后续报道还没出来） |
| `ai_trade_intel_verify_recheck_h` | `48.0` | `ge=1, le=720` | `unresolved` 多久后可重试 |
| `ai_trade_intel_verify_max_attempts` | `3` | `ge=1, le=10` | 同一条最多核查几次（防无限烧预算） |
| `ai_trade_intel_verify_confirm_bonus` | `0.05` | `ge=0, le=0.3` | `confirmed` 的置信度加成（**封顶仍是 1.0**） |
| `ai_trade_intel_verify_refute_penalty` | `0.5` | `ge=0.1, le=1.0` | `refuted` 的置信度乘数 |
| `ai_trade_intel_verify_max_tokens` | `2048` | `ge=512, le=16384` | 核查 Agent 的输出预算。**落点是 `build_intel_llm_client(..., max_tokens_override=…)` 造的第二个 client（`IntelDeps.verify_llm`），不是 `run_round` 的形参**——`LLMClient.stream` 是 Protocol，32 个测试文件实现了它，给它加参数会一次性打翻它们全部（评审 P1-3）。装配失败 ⇒ 回落 `deps.llm`，**不报错** |
| `ai_trade_intel_trust_bonus` | `0.05` | `ge=0, le=0.3` | `trust` 域名的置信度先验（**绝不加 `domain_count`**） |
| `ai_trade_intel_blocked_sweep_enabled` | `True` | — | 拉黑时是否扫除存量条目 |

**七项进 `IntelLimits`**（评审 P1-2 / P1-1）：`cite_score_bonus`、`verify_min_age_h`、
`verify_recheck_h`、`verify_max_attempts`、`verify_max_items`、`verify_confirm_bonus`、
`verify_refute_penalty` 必须在 `taxonomy.IntelLimits` 上各加一个带默认值的字段，并在
`limits_from_settings` 里各加一行映射。理由是成文的：**纯核心从不自己读 `Settings`**
（`IntelLimits` 的 docstring：闸层必须在没有配置、没有 DB、没有事件循环的情况下可测），
而 `plan_verification` 与 `score_item` 都是纯核心。不加这七个字段，§3.3.1 的
`plan_verification(items, *, limits, now)` 与 §3.2.4 的 `cite_bonus` **写不出来**。

**启动期校验器**（`config.py`，与既有 `drain_timeout < shutdown_step_timeout` 同处）：

```python
if self.ai_trade_intel_verify_max_items * self.ai_trade_intel_verify_docs_per_item > \
        self.ai_trade_intel_corpus_max_docs:
    raise ValueError(
        f"AI_TRADE_INTEL_VERIFY_MAX_ITEMS({...}) × AI_TRADE_INTEL_VERIFY_DOCS_PER_ITEM({...}) "
        f"必须 ≤ AI_TRADE_INTEL_CORPUS_MAX_DOCS({...})，否则核查语料会被 corpus.build 截断，"
        f"排在后面的候选将拿不到任何文档、被系统性判成 unresolved")
```

**这条校验器不是形式主义**：不加它，一个把 `verify_max_items` 调到 8 的用户会得到一个
「后 4 条永远待定」的系统，而且**不报任何错**——正是本仓库反复记录的那个形状。

同一处再加一条（评审 P2-10）：`ai_trade_intel_corpus_max_docs <= 99`。语料编号的正则是
`^D[0-9]{1,2}$`，第 100 篇会静默拿不到一个模型写得出来的编号。

---

## 十二、阈值偏离登记（`.claude-index/config.md` 需追加/更新）

| 位置 | 阈值 | 预计实际 | 处置 |
|---|---|---|---|
| `services/quant/app/db/models.py` | 800 | 994 → **≈1050** | 存量债延续（新表 + 12 个补列声明）。修法与既有登记一致（拆 `db/models/` 包、保持同一 `Base` 与同一导入面） |
| `services/quant/app/config.py` | 800 | 1228 → **≈1265** | 存量债延续（17 项 + 一条校验器）。修法不变 |
| `services/quant/app/ai_auto/session.py` | 800 | 3220 → **≈3245** | **五处薄接线，零纯核心**（判定全在 `intel/citation.py` 与 `intel/attribution.py`）。既有拆分指示不变 |
| `services/quant/app/ai_auto/decider.py` | 800 | 1169 → **≈1200** | 一个字段 + 一个解析器 + 两条 return 带参。既有指示（外移 `decision_loop.py`）不变 |
| `services/quant/app/ai_auto/prompts.py` | 800 | 1098 → **≈1123** | 只增 `allow_intel_refs` 分支与一段 schema。**「外移 `_format_*` 到 `prompt_sections.py`」的指示本轮第三次未兑现**，理由见 §十 6；本轮把它升级为对**下一个**触碰本文件的特性的硬要求 |
| `services/quant/app/ai_auto/intel/gate.py` | 800 | 611 → **≈675** | 未越线但逼近（评审后多了 `solve_confidence` 的第 6 形参与 `CleanupPlan.blocked_ids`）。预警登记：下一次触碰前先把 `plan_cleanup` 家族外移到 `intel/cleanup.py` |
| `services/quant/app/ai_auto/intel/taxonomy.py` | 800 | 实读 256 → **≈285** | 未越线。7 个 `IntelLimits` 字段 + 7 行映射 + 4 条降级文案（评审 P1-2/P1-4 新增本行） |
| `services/quant/app/db/ai_intel_repositories.py` | 800 | 610 → **≈640** | 未越线。新增读函数与三分支 `bump_intel_watch`（评审 P0-3/P1-7）；引用与影响力查询已外移到新文件，这正是不让它越线的处置 |
| `services/quant/app/api/ai_intel.py` | 800 | 276 → **≈585** | **新登记为预警**（未越线）。v1 估的 470 偏乐观（评审 P2-8：现况是 276 行 / 9 端点 ≈ 30 行/端点，含依赖注入与 404 守卫；11 个新端点按同一密度是 +330）。出参模型已外移到 `schemas_intel_closed_loop.py`；**下一轮再加端点前，先把信源治理族（#13-#17）整体外移到 `api/ai_intel_sources.py`** |
| `services/quant/app/db/ai_intel_citation_repositories.py` | 800 | **≈420**（新文件） | 未越线。它的存在本身就是「不让 `ai_intel_repositories.py` 越线」的处置 |
| `intel/attribution.py` / `intel/verify.py` / `service_verify.py` 的若干函数 | `MAX_FUNCTION_PARAMS=5` | 6–8 | 全部超出的形参**都是 keyword-only**（`*` 之后），位置参数一律 ≤2 —— 与 v1 同族登记同一条许可（CLAUDE.md §二明许「命名参数」） |
| `apps/web/components/Intel/IntelModal.tsx` | 800 | 426 → **≈500** | 未越线。两个新页签各自独立成文件正是为此 |

---

## 十三、留给评审的三个待决问题

| # | 问题 | 设计的倾向 | 需要评审拍板的原因 |
|---|---|---|---|
| **Q1** | `ai_trade_intel_cite_enabled` 的默认值 | **`True`** | 默认 `False` 会让本特性像 v1 的 `search_provider="none"` 一样默认全暗；但 `True` 意味着**每一个**已在跑的真实资金会话在升级后立刻拿到一个改了字面量的决策 prompt。倾向 `True` 的理由是它**不改变任何交易语义**（纯观测字段 + 一行前缀），且 G-1 的单键回退是逐字节的 |
| **Q2** | `refuted` 是否对**受保护条目**也停止注入 | **是**（§3.3.4） | 这是本设计里唯一一处「自动逻辑压过用户显式意愿」的地方。反面理由也成立：用户置顶它，就是在说「我知道我在做什么」。折中方案是「停止注入但在卡片上给一个显眼的『恢复注入』开关」——设计已按此写，但**默认极性**请评审确认 |
| **Q3** | 验证轮是独立的 `kind="verify"` 轮，还是并进整理轮的一个阶段 | **独立轮** | 独立轮的好处是审计可分、失败可分、预算可分、可单独关；代价是日配额下的采集轮变少（§3.5），且 `ai_intel_rounds` 的行数增加约 50%。若评审认为配额挤占不可接受，替代方案是「并进整理轮、共用一次 LLM 调用的两个工具」——但那会让一次核查失败拖垮整理轮，与 v1「确定性清理不依赖模型」的承诺冲突 |

### 评审裁决（v2 已按此定稿，三个问题就此关闭）

| # | 裁决 | 理由 |
|---|---|---|
| **Q1** | **维持 `True`，采纳设计的倾向。** | 三条支撑：①本字段**不改变任何交易语义**（一个可选的观测字段 + 每行 6 个字符的前缀），G-1 的单键回退是逐字节的；②默认 `False` 会让本轮四条轴**同时全暗**——v1 已经在 `search_provider="none"` 上栽过一次「默认全暗、上线三周没人知道它没跑」；③真正需要担心的不是 `cite` 而是 `verify`（它会改写存量），而那一条已由 P1-12 的两击制给出了不需要额外开关的观察窗。**附一个交付条件**：段末文案必须按 P1-10 改成陈述句，且 G-1 的字节冻结必须覆盖 `build_reflection_prompt`——`cite` 默认开意味着**每一个**在跑的会话在升级后立刻拿到一份改了字面量的复核官 prompt，那是本决定唯一的真实代价，必须被用例钉住。 |
| **Q2** | **维持「受保护条目同样停止注入」，但把「恢复注入」做成卡片上的显式一次点击。** | 置顶与手改保护的是「这条记录别被系统删掉」，不是「这条记录必须继续影响真实资金」。一条已被后续报道推翻的情报继续进交易 prompt，是本特性在「真实可信」四个字上唯一不可辩护的失败模式。**但这个默认极性只有在撤销出口真的可用时才站得住**——而 v1 原稿的撤销出口是一扇旋转门（评审 P0-4）。P0-4 修完之后这条裁决才成立：用户点一次「我不同意」，注入立刻恢复，且该条目**永久**移出自动候选池，不会在下一轮被默默判回去。UI 上必须把这两件事一起说清：`⚠ 已被后续报道推翻，已停止喂给交易 Agent` 与紧邻的 `[我不同意，恢复注入]`。 |
| **Q3** | **维持独立轮。** | 「一次核查失败拖垮整理轮」这条代价不可接受——整理轮是确定性清理（TTL / 溢出 / 失效排期 / 孤儿）的载体，而那四步**刻意不依赖模型**，把它绑到一次模型调用上会正面推翻 v1 最重要的一条承诺。配额挤占不是接受它的理由，而是必须**把它算清楚**：P1-5 已经把 §3.5 的口径改成事实，并让核查轮自判同一条闸。`ai_intel_rounds` 多 50% 行数在本地 SQLite 上不是成本。 |

---

## 附录 · 本设计与用户原始需求的逐条对照

| 用户原话 | v1 的落点 | 本轮的增量 |
|---|---|---|
| 「定期的获取正在自动交易的标的的市场情报」 | 采集轮 30 min / 标的 | 新增核查轮，让「获取」不止是一次性的写入 |
| 「**要真实可信**的市场情报」 | 六道服务端闸（入口） | **出口也有闸**：后验证伪 + 无引用不得断言 + 被推翻即停止注入 |
| 「按照市场情报、情绪情报等等类别去划分」 | 六类 × 三层 | 不变 |
| 「Agent 它自己去决定要不要把它移入中期或者长期」 | 整理轮模型提议 + 服务端复核 | 复核表新增「盈利条目 archive 的举证责任」——让 Agent 的自主权在有战绩的条目上受更严的审 |
| 「都是经过 AI 去在网上搜寻过然后并且摘要过的信息」 | 后端检索 + 模型只能引用编号 | 检索源从 1 个扩到 3 个；核查语料同源同闸 |
| 「另外的一个 Agent 去进行市场情报的获取」 | 情报 Agent（无交易工具） | **第三个 Agent**：核查员（无交易工具、且拿不到持仓与方向） |
| 「持久化存储……下一次再重新打开还能够去查看和使用」 | 四张表 + `create_all` | 引用统计、判定、黑白名单同样持久化（A-10） |
| 「每一条情报……以一个几句话以内的短的摘要去进行存储」 | `summary_max_chars=160` + 超长拒收 | 不变；核查理由另有 120 字上限 |
| 「对应于噪声或者无效的短线情报，可以进行一个整理或者清理」 | TTL / 溢出 / 失效排期 / 孤儿四步确定性清理 | **第五步**：全部来源被拉黑的条目扫除。**「被证伪」刻意不是一条归档理由**（评审 P1-12）——它只停止注入并（第二击后）降层，归档仍然只由那五条**不依赖模型判断**的确定性规则做出 |
| 「用户可以去增删改查**以及不断使用**的这样的一个功能」 | 增删改查 + 置顶 + 静音 + 恢复 | 「不断使用」终于有了度量：影响力页签、归因轨、贡献榜；以及用户对信源的取舍能真正回流到下一轮采集 |


---

## 评审结论

### **有条件通过**（Approved with conditions）

这份设计的判断是对的：v1 交付的是「情报能被采集、被整理、被看见」，而真实资金上迟早会被问出口的
是「模型用了没有 / 用了赚没赚 / 现在还真不真」。四条轴精确对上了这三个问题加一处静默空转，
承诺一（模型只能引用不能创造）在两个新 Agent 上原样复用了 v1 已经兑现过的纪律，承诺二
（归因只读、绝不成为门禁）把一个高风险特性钉死在观测面上，承诺三（每一条自动后果都有手动出口）
则是本仓库反复付过学费之后最该写进设计的一条。取证质量也高：20 条 E 里 18 条实读无误，
§十二 的 10 个行数复测**逐个对得上**——这在一份 1100 行的设计里不常见。

**通过是有条件的**，因为评审发现的 5 个 P0 有一个共同形状，而那个形状恰恰是本轮要修的那个：

> **新加的判据取自一个不携带该信息的入参，或写进一张没人读的表。**
>
> - P0-1：入场归因取「本轮待写行」，而默认订单类型的成交根本不在本轮。
> - P0-2：结算取 `self._position_seq`，而那个计数器在 `note_close` 之前就已经 +1 了。
> - P0-3：核查节律读 `curates_since_verify`，而全仓库没有一处写它。
> - P0-5：三把安全单键写进 `PUT /settings/intel`，而读它们的那张桥表没扩。
> - P0-4 是同一形状的镜像：撤销出口写了，但撤销之后的状态**正好**是自动池的入池判据。
>
> 五条的共同症状都是**不报错**：用例会绿，界面会显示 0，运维会以为「模型就是不引用情报」。
> 本文 §1.1 用来立项的那段话——「那张表恒为空、那道过滤器恒空转，而**这件事不会报任何错**」
> ——如果不修这五条，会在同一份设计里被复制五份。

### 交付条件（缺一不可）

| # | 条件 | 判据 |
|---|---|---|
| **C-1** | 5 个 P0 与 15 个 P1 的修法**全部按 §正文的定稿实现**，不得在实现阶段"简化" | G-15…G-20 六条新总闸全绿；其中 **G-15 必须真的走 resting 通路**（用市价单跑出来的绿不算数） |
| **C-2** | 实施顺序的**第 1 步与第 6 步不得压缩**：迁移独立提交、`scheduler._tick` 是最后一行接线 | `_tick` 一接上真实分组就开始跑核查轮；它必须是整条链路全绿之后的最后一次改动 |
| **C-3** | **首个真实分组的第一周期是观察窗**：只看 verdict 分布与「证伪率」，不看层级变更 | 这一条由 P1-12 的两击制**结构性**兑现（首击不动任何层级），不依赖运维记得去看 |
| **C-4** | 全量回归**先跑基线再跑改后**，两次红集合逐条比对 | 已知红基线是未跟踪在制文件 `test_factor_library_expansion.py` 的 7 条；**新增任何一条红都不可交付** |
| **C-5** | 三把单键各关一次、跑通一个完整决策轮 | G-12 的五种失败态五段文案；关掉 `cite` 之后 `build_decision_prompt` **与 `build_reflection_prompt`** 的 sha256 都要回到冻结值 |
| **C-6** | §十 的七条非目标**不得在实现阶段被悄悄突破**，尤其是第 1 条（不把归因做成门禁）与第 7 条（回测车道红线） | 用例 `test_no_degrade_code_originates_from_intel_layer`：断言轴 A/B/C/D 的任何异常路径都不产出新的 `TradeDecision` 降级码 |

### 三个待决问题的裁决

Q1 维持 `True`、Q2 维持「受保护条目同样停止注入」（**以 P0-4 修毕为前提**）、Q3 维持独立轮。
理由见 §十三 的《评审裁决》，三个问题就此关闭，不再回到实现阶段重议。

### 不阻断交付、但请实现者留意的两处

1. **R-1（引用被误读成因果）的缓解只能靠文案，而文案是会被读者跳过的。** 影响力页签的
   「关联净额」四个字与顶部那句口径说明，是这份设计里**唯一**挡在「这条情报值 18 USDT」这个
   误读前面的东西。它们不是装饰，实现时不要为了排版把它折叠起来。
2. **§十 6 的那笔债本轮第三次未还。** 「把 `_format_*` 外移到 `prompt_sections.py`」的理由这次
   仍然成立（G-1 的字节冻结说服力），但**第三次**是一个信号。本文已把它升级为「下一个触碰
   `prompts.py` 的特性必须先还」——评审确认这条升级，并建议在 `.claude-index/config.md` 里把它
   写成一条带日期的硬要求，而不是又一条注释。


---

## 实施过程发现的方案缺陷

> 本节由实施节点在落地过程中追加（**发现即写，不留 TODO**）。每一条都写明：设计怎么说的、
> 实读是什么、实现里改成了什么、以及那个改动为什么不是一次「悄悄的简化」。
> 正文的对应小节**未改**——本节是差异登记，不是二次设计。

| # | 设计原文 | 实读 / 症状 | 已采取的修法 |
|---|---|---|---|
| **I-1** | §4.1 把 `GET /intel/feedback` 与 `GET /intel/settings/health` 列在既有 12 个端点之后 | **FastAPI 按注册顺序匹配**。`api/ai_intel.py` 里 `GET /intel/{instId}` 是第一批注册的，把两个字面量路由追加在文件末尾会让 `GET /intel/feedback` 被 `{instId}` 吃掉（`instId="feedback"`），症状是一次 **500**（`count_intel_by_tier` 拿着 `inst_id="feedback"` 去查库）而不是 404 —— 路由被静默劫持 | 四个 `/intel/feedback*` 与 `/intel/settings/health` 全部**移到 `@router.get("/intel/{instId}")` 之前**，并在那一段上写了不可省的接线注释；`test_ai_intel_feedback_api.py` 的每一条用例都会因为这次回归而变红 |
| **I-2** | §3.7.2 说「点击 ⇒ `ConfirmDialog`（复用既有组件）」，框内含一个作用域勾选框 | 实读 `components/ui/ConfirmDialog.tsx`：它**没有 `children` 插槽**，只有 `title` / `message` 两段纯文本 | 给它加了一个**可选** `children`（渲染在说明与按钮之间，缺省 `undefined` ⇒ 既有全部调用点逐字节不变），而不是另造一个确认弹窗。理由写进了该文件的 prop docstring：焦点陷阱、Esc、遮罩、**以及「初始焦点钉在取消上」这条不可逆动作的默认极性**，都只有一份实现才守得住。这是本轮唯一一处改动了设计文件清单之外的文件 |
| **I-3** | §4.1 #14 只说「`action="block"` 且 `blocked_sweep_enabled` ⇒ 同事务扫除存量」 | `blocked_sweep_enabled` 是**露在 `PUT /settings/intel` 上、存进 `app_settings` 表**的键。端点若读 `Depends(get_settings)` 那一份（进程 env），面板上关掉它系统照扫 —— 正是评审 P0-5 那类假开关，只是换了一个键 | `write_intel_feedback` 改为先 `await intel_service.resolve_intel_settings(session, settings)`，再读派生副本上的那一位；`test_sweep_can_be_turned_off_and_then_nothing_is_archived` 用「关掉之后 `archived == 0` 且条目仍 active」钉住它 |
| **I-4** | §3.1.3 的 `resolve_citations` 只写了正则与编号表双查 | 去重只在 `decider._resolve_intel_refs` 里做了一次。裁决层不去重时，一个（未来的、或测试里的）不走解析器的调用点会把同一编号变成两条引用行 ⇒ `cite_count` **静默翻倍** | `resolve_citations` 自己也去一次重（成本一行），并在注释里写清「两处都做」的理由 |
| **I-5** | §3.7.5 把影响力页签的读数类型命名为 `IntelImpact` | `apps/web/lib/intelTypes.ts` 里**已经有** `export type IntelImpact = "low" \| "medium" \| "high"`（条目的影响力枚举）。同名声明会让 `impact.totals` 在 TS 上解析成 `"low"` 的属性访问 —— `tsc` 会红，但红在一个与根因无关的位置 | 新类型改名 `IntelImpactReport`；`IntelItem.impact` 与 `IntelItemInput.impact` 的既有类型一个字节不改 |
| **I-6** | §3.7.3 的「影响力」页签要按三把单键分出四种空态 | 弹窗此前**不取设置**（它只读 `GET /intel/{instId}`），拿不到 `citeEnabled` / `attributionEnabled` 的生效值 ⇒ 四段文案里有两段永远选不中 | `IntelModal` 增一次 `intelApi.settings()`（**只在打开时取一次**，不进 5 秒轮询：它们是设置不是行情），把两位透给 `IntelImpactTab` |
| **I-7** | §3.3.5 步骤 6 只说「`bump_intel_watch(kind="verify", verify_reset=True)`」，没说 `next_due_ts` 传什么 | 那是一个**必填**形参（它服务的是采集轮的到期时刻） | 与整理轮的写段逐字同构：`next_due_for(deps, fail_streak=0)`。同时把 `service._finish_degraded` / `_next_due` 两个下划线私有名**改成公开名** `finish_degraded` / `next_due_for` —— `service_verify` 要跨模块用它们，而本仓库对「一个下划线开头的名字被两个模块共用」有成文的反对意见（`search.py::_redact` 的注释） |
| **I-8** | §3.2.1 的 `note_executed` 说「若本轮留下了一张 `intent=="open"` 的挂单，把本轮待写行冻结进跨轮认领槽」 | 限价单在**同一轮内**即时成交时，`_dispatch` 已经先把那些行标成 `entry` 并走了回退路径；此时再冻结一份进认领槽，会留下一条永远认领不到的记录，收尾时被计成一次 `dropped` —— 一个正常路径在面板上显示成一次丢弃 | 只冻结 `role == "hold"` 的行；`test_claim_slot_is_capped_and_overflow_is_counted` 与 `test_end_round_drops_pending_and_counts_it_but_keeps_claims` 一起覆盖这两条路 |

### 一条**未改**但登记备查的观察

`intel/citation.py` 的 `is_settleable(role)` 在本轮**只被用例消费**（结算的角色过滤写在
`settle_intel_citations` 的 SQL `where` 里）。它不是死码——它是那条规则的**可测断言点**，
把「只有 entry / exit 参与结算」这句话从一条 SQL 谓词变成一个可以被单测直接问的问题。
若下一轮把结算搬进纯核心，它就是现成的判据；若始终不搬，下一次触碰本文件时应把它与那条
`where` 子句合并到同一处，而不是留两份口径。


---

## 终审记录（Anthropic 工程终审，合入前）

> 口径：**逐条比对 diff 与本文《文件变更清单》《实施顺序》《交付条件》**，每一条结论都在
> 2026-08-30 的工作树上实读复核过，不采信实施节点的自述。
> **覆盖度**：6.1 的 26 项后端全部到位；6.2 的 14 项前端到位 13 项（缺 #40，见 T-1）；
> 6.3 的桌面零改动 + `config-passthrough.test.mjs` 到位；6.4 的三项文档到位。
> **交付条件**：C-1（P0/P1 定稿实现）、C-2（`_tick` 是最后一行接线且只改一处）、C-3（两击制
> 结构性兑现）、C-5（三把单键各有回退落点）、C-6（`test_no_degrade_code_originates_from_intel_layer`
> 与 `test_intel_refs_never_reach_any_gate` 双向冻结）逐条复核通过。
> **C-4（先跑基线再跑改后，两次红集合逐条比对）**：全量 4559 passed / 7 failed / 1 skipped，
> 7 条**全部**来自未跟踪且从未提交的 `test_factor_library_expansion.py`（与本轮无关，合入前即为红），
> **新增 0 条红**。基线里那第 8 条（`test_experience_incremental` 的 G-15 墙钟预算断言）本次为绿，
> 印证了它确属负载敏感的偶发。前端 205 文件 / 2363 用例全绿 + `tsc` 干净；桌面 42 条 node:test
> 全绿；`ruff check --select F,E9` 干净。

### 终审发现并**已直接修毕**的缺陷（不留 TODO）

| # | 位置 | 实读 / 症状 | 修法 |
|---|---|---|---|
| **T-1** | `apps/web/lib/intel.ts`（清单 6.2 #40，**唯一一项未被触碰的文件**） | `WEAK_DEGRADE` 只登记了 v1 的三个弱码。轴 C 新增的 `verify_no_corpus`（=「本轮没检索到可核对的新材料」）因此被 `isSevereDegrade` 判成强提示 —— 一次**例行**的空核查轮会把标的卡片上的情报按钮染成警示色并一直挂到下一轮。该文件自己的注释写着「常亮的警告会失去意义」 | 把 `verify_no_corpus` 补进 `WEAK_DEGRADE`（另三个 `verify_*` 与它们的采集轮同义码同为强提示，逐个断言冻结在 `intel.test.ts`） |
| **T-2** | `apps/web/components/Intel/IntelSourcesTab.tsx::run()` | 收尾无条件 `setNotice(done)`，把 `onConfirm` 里刚写进去的「N 条情报已归档，可在「已归档」中恢复」在同一个 tick 覆盖掉 —— 确认框承诺过的后果，在后果真的发生之后反而看不见了。既有用例 `确认之后回执里的归档条数进提示` **只断言了请求发出**，没断言那句话 | `run` 改成「`task` 返回了文案就用它，`done` 只是回落」；同时把那条用例的断言补到它名字承诺的位置上 |
| **T-3** | `db/ai_intel_citation_repositories.py::list_intel_source_rows` + `IntelSourcesTab` 的「撤销」 | 行状态由「全局 + 本标的」并集算出，而 `clear_intel_feedback` 按 `(key, inst_id)` **精确**删除；界面硬写当前 `instId` ⇒ 一条**全局**拉黑的「撤销」是一次什么都不会发生、也不报任何错的点击。与本轮立项那句「有写点没读点」同形 | 每行下发 `stateInstId`（生效记录的作用域，两条都在时报更具体的那一条），`IntelSourceRowOut` / `IntelSourceRow` 同步扩键，撤销按它发；徽章旁标出「（全局）」。前后端各一条回归用例 |
| **T-4** | `apps/web/components/Intel/IntelBlockSourceDialog.tsx` | `global` 勾选框在弹窗关闭时**不复位**：勾过一次「对所有标的生效」再取消，换一个域名打开时它仍是勾上的 —— 一个不可逆动作的作用域跨调用留存，正好推翻该文件 docstring 自己写的默认极性 | `open` 变 false 即 `setGlobal(false)`；用例走「开→关→换域名再开」三步 |
| **T-5** | `intel/runtime.py::build_group_runtime` | 注释与 §十一 都写着「核查 client 装配失败 ⇒ `verify_llm=None` ⇒ 回落 `deps.llm`，**不报错**」，但那一行没有任何 `try` —— 抛出来会让**整组**情报层装配失败。声明的行为与实际行为相反 | 补 `try/except`（只包核查那一次；采集轮那一次照旧向上抛，两者不同命），并加一条断言「装配失败之后 `runtime.enabled` 仍为真且 `verify_llm is None`」 |
| **T-6** | `app/config.py` 的第二条启动期校验器（本文 §十一 末尾要求的 `corpus_max_docs <= 99`） | `ai_trade_intel_corpus_max_docs` 的 `Field` 已是 `ge=4, le=40`，pydantic 在 `model_validator(mode="after")` **之前**就拦下了 —— 这条 `>99` 分支永远走不到，属 CLAUDE.md §五 明禁的「为理论上不会发生的分支写防御」。评审 P2-10 提出它时没有实读那个 `le=40` | 删掉该分支，把「`le=40` 同时守住 `^D[0-9]{1,2}$` 的 99 篇上限」写成 `Field` 上的一条注释。**第一条校验器（`verify_max_items × verify_docs_per_item ≤ corpus_max_docs`）是可达的，原样保留** |
| **T-7** | `intel/search.py` 模块 docstring | 仍写着「v1 只实现 `tavily` 一个适配器……`brave` / `serper` 的接入参数登记在此，v1.1 补时各约 50 行」，而本轮就是那个 v1.1 —— 文件顶上的一段僵尸文档 | 改写成三个适配器的实读差异（传 key 的位置、时效枚举、日期字段各不相同）+ 一句「仍是单活」 |
| **T-8** | `intel/search.py::PLANNED_SLUGS` 的注释 | 写着「保留这个名字与 `resolve_search_provider` 里的 `logger.info` 分支」，而那条 `logger.info` 本轮已改成打印 `PROVIDER_SLUGS[1:]`，与本表再无关系 | 改写成它现在真正的读点（`test_ai_intel_search_providers.py` 拿它断言那条债确实还清了），并说明拼错的 slug 由谁兜底 |
| **T-9** | `intel/service.py` 的 5 个 `finish_degraded` 调用点 | I-7 把 `_finish_degraded` 改成公开名（少一个字符）后，续行没有跟着左移一格，5 处全部比视觉缩进多一格 | 逐处对齐（`ruff --select E1,E2,E3` 干净） |
| **T-10** | `services/quant/tests/test_ai_intel_wiring.py` | 一条 `assert` 把说明字符串用一长串空格顶到 130 列开外 | 说明改成断言上方的一行注释 |
| **T-11** | `intel/service.py::verify_due` + §3.3.6 的判据原文 | 判据抄了整理轮那条 `collects_since_curate + 1 >= every` 的形状，**时序却是相反的**：核查排在整理**之前**，而 `verify_reset` 清零之后**同一个 tick 的整理轮立刻把计数器加回 1** —— 稳态下它永远不会被观察到 0。于是 `+1` 让稳态周期塌成 `N-1`：`verify_every_n_curates=2` 变成「从第二次整理起每次都核查」，检索与 LLM 账单翻倍，界面上没有任何异样。**这是评审 P0-3 的镜像**（那一条是「一次都不跑」，这一条是「每次都跑」），而原用例只问了计数器的一个点，问不出周期 | 去掉 `+1`（判据改为 `>= every`），并在 docstring 里写清「为什么紧邻那条判据带 `+1`、这条不带」；用例改成**逐周期模拟** `_tick` 的时序（check → verify(reset) → curate(+1)）跑 20 个周期，对 N=1..4 断言相邻两次核查的间隔集合恰为 `{N}` |
| **T-12** | `components/Settings/IntelSourceSettings.tsx` | 轴 D 把下拉从 1 项扩到 3 项，但 API Key 的 `hint` 仍硬写 `APPLY_LINK = "https://tavily.com"` —— 一个选了 Brave 的用户会被指去 tavily.com 申请 key，白跑一趟并断定「这功能没做完」。另：`ToggleRow` 的注释把 `ui/Switch` 说成「一个 `role=switch` 的 div」（实读是 `<button>`），且末句自相矛盾；`disabled` 只忽略回调、没有任何可见失效态 | 申请地址跟着选中的源走（`applyLinkFor`，`none` 时改口说「先选一个源」）；注释改写成实读，并给在飞态补 `pointer-events-none opacity-50` |
| **T-13** | `components/Intel/IntelImpactTab.tsx` | 它自己取数、且**刻意不进 5 秒轮询**（聚合读数不是行情），但没有任何重取入口。用户在贡献榜上点掉一个判定 ⇒ `guard()` 成功、`load()` 重取了列表，而这张榜单纹丝不动 —— 一次成功的写动作在用户眼里就是一次「点了没反应」 | `IntelModal` 每次写动作成功后 `actionToken + 1`，作为可选 `reloadToken` 透给本页签并进 effect 依赖（缺省 0 ⇒ 只在挂载时取一次，既有行为不变）。用例用 `rerender` 走「token 变化 ⇒ 重取一次」 |

### 终审补的一道门禁

`.env.example` 与 `Settings` 的 `ai_trade_intel_*` 家族**双向逐键对齐**
（`test_every_intel_setting_is_registered_in_env_example`，落在 `test_ai_intel_wiring.py`）。
复核时两侧各 51 键、逐个对得上，**本轮没有漏登记**；补这道门禁是因为本文 6.4 #41 那条纪律
（家族标题从 34 改到 51）今天靠的是人记得改，而 `.env.example` 在本仓库**从来没有任何扫描
用例**——它已经为此栽过两次，两次的症状都是「漏登记不报错」。判红之后的正确动作是补那一行，
不是放宽正则。

### §十二 阈值的合入后实测

逐个复测（`grep -c ""`），与 §十二 的预估对照：`gate.py` **710**（预估 675，偏低 35 行，
仍 <800，§十二 那条「下一次触碰前先把 `plan_cleanup` 家族外移到 `intel/cleanup.py`」的预警
**照旧成立且更紧迫**）、`service.py` 687、`api/ai_intel.py` **497**（预估 585，实际比预估好，
出参模型外移到 `schemas_intel_closed_loop.py` 是主因）、`taxonomy.py` 298、
`ai_intel_repositories.py` 677、`IntelModal.tsx` 498 —— 以上全部 <800。
越线的三个是**存量债延续**且都在预估之内：`models.py` 1065、`config.py` 1274、
`session.py` 3264。**本轮没有把任何一个原本合规的文件推过 800 线。**

### 未改、但登记备查的两处

1. **`estimate_daily_cost` 里的核查轮节律硬写成 `rounds / 4.0`**，而真实节律是
   `curate_every_n_collects × verify_every_n_curates`（两者都是可调设置，默认恰为 4×1）。它是
   设置卡上的一个**量级估算**，两个旋钮都调到上限时会偏低。不在终审改动的理由是：改它要么给
   `estimate_daily_cost` 再加两个形参（它已有 6 个 keyword-only），要么让它去读 `Settings`
   （纯函数就此破功）。**登记为下一次触碰该函数时一并还的债。**
2. **`read_summary` 里 `read_intel_impact(..., limit=1)` 会顺带跑掉 `top` / `worst` 两条只为丢弃
   的查询。** 本地 SQLite 上是微秒级，且它与 WS `intel` 事件共用同一个出口（拆开就是第二份口径）。
   登记备查，不在终审动它。
