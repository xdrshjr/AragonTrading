# AI 自动交易 · 离场优先级与离场意图兑现（ai-trade-exit-priority-and-commitment）· 方案设计 **v2**

> 本轮 = 标准 4 节点开发流的**节点 #0（方案设计）**。本文档是节点 #1（评审）/ #2（开发）/ #3（Review + 提交）
> 的**唯一实施依据**，自包含——实现者不需要读任何历史 spec。
>
> **编号命名空间是本文档局部的**：不变式 `X-*`、缺陷 `F-*`、接线 `W-*`、放行条件 `G-*`、
> 验收 `A-*`、风险 `R-*`、配置 `C-*`、实施决策 `D-*`。凡引用既有轮次的条目一律带来源前缀
> （如 `horizon:P0-3`），并注明出处文件。
>
> **本文档的每一条缺陷都附可执行取证**：探针脚本全文见 **附录 A**，在 `services/quant/.venv`
> 内可原样重跑，输出逐字贴在 §2。**不采信任何既有文档的结论**；凡与源码不符**以源码为准**。
>
> 取证环境：`services/quant/.venv/Scripts/python.exe`，`PYTHONPATH=services/quant`，
> Windows / Git Bash。控制台为 GBK，故探针一律只打印 ASCII（本仓库既有教训：中文 `⇒` 会让
> repro 脚本在 GBK 控制台崩掉）。
>
> **v2（2026-08-25，节点 #1 评审后）**：§二 取证部分**一字未改**（评审侧逐条复核通过）。修改全部
> 落在修法侧——6 条 P0 与 10 条 P1 已在正文原地修正，正文中凡带 `【R:编号】` 锚点处即为本轮改动点。
> 变更摘要见下一节《评审记录》，放行结论见文末《评审结论》。

---

## 评审记录（Review Notes · 节点 #1）

> 评审人：Anthropic 工程团队资深评审。评审对象为本文档 **v1**（1142 行 / ~75k 字符）。
> 本节列出全部关切；**P0 / P1 已在正文逐条修正**（正文锚点 `【R:编号】`），P2 未改正文者在此说明取舍。
>
> **评审方法**：不采信 v1 的任何结论，对它引用的每一处源码重新实读，凡与源码不符**以源码为准**。
> 复核覆盖：`services/quant/app/ai_auto/{hold_horizon,horizon_runtime,session,decider,reflection,`
> `prompts,trace,exit_attribution,acceptance_text,backtest,discipline_runtime,open_funnel,`
> `turnover_discipline}.py`、`app/ai_auto/experience/taxonomy.py`、`app/live_auto/executor.py`、
> `app/api/ai_auto.py`、`desktop/src/lib/config.js`、
> `apps/web/lib/{store,aiTrade,aiTradeTypes,aiTradeHorizon,aiTradeAcceptance,experience}.ts`、
> `apps/web/components/{Chart/AiTradePanel,Agent/AiTradeTraceRound,Agent/AiTradeHorizonFields}.tsx`、
> `services/quant/tests/test_ai_trade_{horizon_runtime,hold_horizon,prompts,exit_attribution}.py`。
>
> **§二 取证部分复核通过（不改一字）**：豁免矩阵十行读数、`horizon_runtime.py:197-220` 的清零点、
> `reflection.apply_reflection` 的 fail-closed 极性、`executor._prepare_resting_slot` 的 `unknown`
> 分支、`close_position(auto_cxl=True)` 的交易所侧撤单语义、`AI_TRADE_*` 已在
> `desktop/src/lib/config.js:80` 的 prod 透传白名单内——逐条与源码一致。`derive_min_hold_s=7200` /
> `derive_confirm_window_s=3600` 由评审侧独立重算复现。**F-1 到 F-6 六条缺陷全部成立，问题定性准确。**
>
> **问题全部出在修法侧**，且高度同形：**新开的离场通道绕过了它自己声称的约束**——P0-1 让配额 /
> 证据 / 时间地板三道自设护栏可被复核官一句 `confirm` 抹掉；P0-2 让「只帮平仓」的通道去帮了开仓腿；
> P0-3 让「帮你把仓平掉」的兑现层去撤掉你自己挂的限价平仓单。**这三条不修，本轮交付的不是一条
> 优先级通道，而是一个绕过全部风控的旁路。**

### 严重度口径

| 级别 | 含义 | 处置 |
|---|---|---|
| **P0** | 危及资金安全 / 契约正确性，或使本方案自己立下的核心承诺（X-1 / X-4 / X-9）失效 | 不修不得进开发 |
| **P1** | 静默功能缺失、口径漂移、放行条件自相矛盾、越 CLAUDE.md 红线、放行条件不可实现 | 必须在开发前修正文 |
| **P2** | 表述与源码不符、口径未交代、右尺寸取舍 | 不阻塞；已修者标注，未修者说明理由 |

---

### P0（6 条 · 全部已修）

#### R:P0-1 · 「挂起待裁决」一次删掉 X-4 四个合取项中的三个，配额 / 证据 / 时间地板全部可绕过

**证据**：v1 §4.4.1 第 3 行规定「`declared == final` 且**因 §4.3.1 第 3 条的某个合取项不成立而降档**
⇒ 挂起为 `pendingExit`」，而 §4.4.2 的 `confirm` 分支直接「放行 close，`adjudicated=True`，
`overrides_used += 1`」。§4.3.1 第 3 条的四个合取项分别对应四种降档原因，于是：

| 降档原因 | v1 的实际结果 | 被架空的护栏 |
|---|---|---|
| `quota_exhausted` | 挂起 → 复核官 confirm → 放行，且 `overrides_used` 继续自增**越过 `max_per_session`** | R-1 缓解① 会话级硬配额 |
| `evidence_missing` | 挂起 → 放行 | R-1 缓解② `final` 必须带证据 |
| `min_hold_floor` | 挂起 → 放行，**§4.3.1 自称「不可解除内核」的 60s 地板被解除** | R-1 缓解④ 绝对地板 |
| `ledger_short` | 挂起 → 放行（**这一条是本方案的正当设计**） | — |

**影响**：X-4 白纸黑字写着「删掉其中任意一个合取项都会把这条通道变成『模型的自助放行按钮』」，
而 §4.4.1 一次删了三个。更直接的自相矛盾：G-10 断言「连续 8 轮声明 `final` ⇒ 前 6 轮放行、
后 2 轮降档为 `high`」，但后 2 轮恰好命中 `quota_exhausted` ⇒ 按 §4.4.1 挂起 ⇒ 只要复核官
`confirm` 就照样放行——**G-10 这条防滥用门禁在 v1 的设计下必然是假绿**（它只断言了 `downgradeReason`
与计数，没断言「没有成交」）。R-1 五条缓解里三条失效。

**修法（已落 §4.4.1 / §4.4.2 / §8.1 G-10）**：pending **只对 `ledger_short` 开启**；
`quota_exhausted` / `evidence_missing` / `min_hold_floor` 三种降档**一律不挂起**，按 `expedite`
或 `normal` 走今天的路径。裁决放行与 `override` **共用同一份配额**（`overrides_used` 自增前必须
再判一次 `< max_per_session`，超出即拒绝挂起）。G-10 追加断言「后 2 轮 `executed.traded is False`」。

#### R:P0-2 · 优先级施加到「反手」上会让一次 `open_*` 更容易发生 —— 直接违反 X-1

**证据**：`hold_horizon.gated_kind`（`hold_horizon.py:558-573`）返回 `'close' | 'reverse' | None`，
反手（持多时 `open_short` / 持空时 `open_long`）**同样受本层约束**。而 v1 §4.3.1 第 1 行只在
`gated_kind(...) is None` 时给 `none`，故反手会拿到 `expedite` / `override`；§4.3.3 的
`released = set(snapshot["releasedGates"]) | set(priority_gates)` 落在
`evaluate_horizon_gate`（`hold_horizon.py:728-751`）内部，对 close 与 reverse **一视同仁**。
于是一次 `override` 会解开 G1，让一条**开仓腿**在最短持仓窗口内成立。

**影响**：① 违反 X-1（「绝不可能让任何一次 `open_*` 更容易发生」）；② G-5 的 AST 扫描**守不住它**
——泄漏点在 `hold_horizon` 的接线，`exit_priority.py` 里确实一个 `open_long` 都没有；
③ §4.4.1「pending 保持 close 继续下行不会被中途执行掉」的安全论证**只对 close 成立**：反手的
`action` 是 `open_long`/`open_short`，它落在 `base_rules._GATED_ACTIONS`(`base_rules.py:44`) /
`edge_gate._GATED_ACTIONS`(`edge_gate.py:39`) / `drawdown_brake._GATED_ACTIONS`(`drawdown_brake.py:37`) /
`sizing._GATED_ACTIONS`(`sizing.py:45`) / `signals.py:252` **五道闸**的作用域内，会被静默降级成
hold，pending 的裁决通路根本走不到。

**修法（已落 §4.3.1 / §4.3.3 / §8.1 G-5）**：`resolve_exit_priority` 只在 `gated_kind(...) == 'close'`
时返回非 `none`；反手沿用本仓库既有先例——`turnover_discipline.apply_turnover_gate`
（`turnover_discipline.py:323-353`）在命中时把反手**降级为 `close`**（保留退出腿、丢弃开仓腿），
再由本层按 close 处理。G-5 追加断言：`priority_gates` 只在 `kind == 'close'` 分支参与并集。

#### R:P0-3 · 轴 C 会把 Agent 的每一次**限价平仓**在 10 秒后改写成市价平仓

**证据**：v1 §4.5.2 的武装判据是「`decision.action == 'close'` 且未见 close 成交事件」。而限价平仓
走 `executor._do_close` 的 `limit_px is not None` 分支（`executor.py:349-354`）⇒ `_enter_resting`，
**当轮必然没有成交事件**、`projected` 保持原方向。于是轴 C 必然武装；`retry_interval_s=10` 到点后
`executor.apply([Signal(side='close')])` **不带 `order_plan`** ⇒ `_plan_limit_px` 为 `None` ⇒ 市价
⇒ `_prepare_resting_slot(..., limit_px=None)` 与原挂单 `is_same_intent` 不成立 ⇒ **撤掉模型自己挂的
限价平仓单并按市价平掉**。

**影响**：整个 limit-orders 特性（已合入 `3bbc503`）在离场侧被静默废掉。用户界面上看到的是
「限价 0.09330 挂着」，实际 10 秒后按市价成交——这与本轮 §4.6 要修的「界面不诚实」是同一类问题，
只是方向反了。且 TTL（`ai_trade_limit_order_ttl_s`）机制被完全绕过。

**修法（已落 §4.5.1 / §4.5.2）**：武装判据加硬前置——**只对市价平仓武装**
（`decision.ord_type == 'market'` / `limit_px is None`）；`should_retry` 追加一条恒 `False` 的短路：
`executor.resting is not None and executor.resting.intent == 'close'`（挂单在飞就是「正在兑现」，
TTL 到期由既有 `_poll_resting` 收敛）。#30 用例逐条冻结这两条。

#### R:P0-4 · W-5 的成交判据在它自己的落点上**取不到**

**证据**：v1 §4.5.2 写「`executed` 中不存在 `type=='trade' and data.side=='close'` 的事件」，并称
与 `_report_profit_exit`（`session.py:1932-1938`）逐字同源。但两者的载体根本不同：
`_report_profit_exit` 收的是 `events`（`list[dict]`，由 `_apply_profit_exit` 返回），
而 **`_execute_decision`（`session.py`）返回的是 dict**——
`{"ok","action","skipped","traded","side","qty","positionAfter"}`，事件列表 `events` 是函数内的
局部量、已在函数内部 `_dispatch` 掉了。

**影响**：照 v1 写会得到一个**恒成立**的判据（在 dict 上迭代不出任何 `type=='trade'` 的元素），
**每一轮 close 都武装**——叠加 P0-3 后果是每一轮 close 都会在 10s 后再打一次市价单。

**修法（已落 §4.5.2）**：判据改用该落点真实拿得到的量。给 `_execute_decision` 的回执加一个
加法键 `closedFilled: bool`（在函数内部由既有 `events` 算，与 `traded` 并列），武装判据取
`executed.get('closedFilled') is False`；「与 `_report_profit_exit` 逐字同源」改写成
「同一条纪律（以 close 成交事件为唯一判据）、不同载体」。

#### R:P0-5 · 新枚举 / 新豁免原因的**镜像清单不全**，六处会静默显示原始码或整段丢弃

**证据**：v1 §5.4 给 `EXIT_SOURCES` 加 `agentOverride`、§5.5 给 `_RELEASE_LABEL` 加 `exit_pending`，
但 §七 的文件表只列了 `exit_attribution.py` 与 `hold_horizon_text.py`。实读还有**六处镜像**：

| # | 镜像点 | 漏改后果 |
|---|---|---|
| 1 | `ai_auto/acceptance_text.py:25` `EXIT_SOURCE_LABELS` | 周期验收卡显示裸 `agentOverride` |
| 2 | `ai_auto/experience/taxonomy.py:110-113` `VALUE_LABEL_ZH` | 经验图 lesson 标题出现裸英文（`AXIS_VALUES[exit]=tuple(EXIT_SOURCES)` 会自动跟，**标签是手抄的**） |
| 3 | `apps/web/lib/aiTradeAcceptance.ts:35,54` `EXIT_SOURCE_LABELS` / `EXIT_SOURCE_COLORS` | 堆叠条出现一段无标签、无配色的灰块 |
| 4 | `apps/web/lib/experience.ts:67-73` `VALUE_LABEL_ZH` | 同 #2 的前端侧 |
| 5 | `apps/web/lib/experience.ts:93` `AXIS_VALUES.exit`（**硬编码九值数组**） | 经验图预演选择器里**根本选不到这一类** |
| 6 | `apps/web/lib/aiTradeHorizon.ts:280` `HORIZON_RELEASE_LABEL` | `exit_pending` 豁免轮 UI 只显示「限制已解除」，说不出为什么 |

另：回测侧的打标槽 `backtest.py:893-896`（`self._pending_exit_source = "agent"`）也需同步，
否则回测对照臂永远统计不到 `agentOverride`。

**影响**：这一整类**全部不会报错**，只会静默降级为「后端算了、用户看不见」——正是本仓库反复出事
的形态（v1 自己在 R-8 / R-9 里点过名，却没把清单查全）。第 5 条更严重：它让本轮最重要的可证伪
凭据（X-9）在经验图那一侧根本无法被查询。

**修法（已落 §5.4 / §5.5 / §7.1 / §7.2）**：把六处镜像逐条写进文件变更表；G-9 的正则扫描用例
扩为「`EXIT_SOURCES` 的每一个值都能在前端两张标签表与一张 `AXIS_VALUES` 数组里查到」的**双向
覆盖断言**（而非只查「有没有硬编码」）。

#### R:P0-6 · `agentOverride` 打标槽**没有生命周期**，会把别的平仓错记成最高优先级离场

**证据**：v1 §5.4 说「由 `session._inject_explain_meta` 在**单一收口**打标——凡本次 close 的
`priority_level == 'override'` 或 `adjudicated` 为真即归此类」。但
`_inject_explain_meta`（`session.py:430-461`）跑在 **`_dispatch` 的每一条成交事件上**，包括：
利润保护平仓、会话止盈损平仓、外部/OCO 平仓，以及**限价平仓在后续盯市 tick 上由
`_poll_resting → _adopt_close_fill` 异步采纳的那一笔**。若 `priority_level` 存在会话字段上而不随
本次 close 消费清空，一次 override 之后的**下一笔任意平仓**都会被记成 `agentOverride`。

**影响**：X-9 唯一的可证伪凭据（「用最高优先级走掉的那几笔事后净额是正是负」）当场作废，且错误
方向是**系统性高估**——override 之后的仓位往往由保护层平掉，全被计入。
`exit_attribution.classify_backtest` 的 R-6 注释（「槽里若恰好留着上一次的值，以 reason 为准才不会
归错」）正是本仓库为**这同一形态**写下的成文教训。

**修法（已落 §5.4）**：打标做成**一次性槽** `_pending_exit_priority`——在本次 close 的成交事件上
消费即清空；`epoch` 变化 / `projected` 归 flat / 会话暂停 时无条件清空；且只对
`explain['kind'] == 'atomic'` 的 close 生效（保护层的 `profit_guard`、OCO 的 `external_close`
一律不认这个槽）。#31 追加用例：override 之后紧接一次利润保护平仓 ⇒ 后者必须归 `profitGuard`。

---

### P1（10 条 · 全部已修）

#### R:P1-1 · F-4 的放行分支会制造「我方限价平仓被记成交易所 OCO 平仓」+ 二次结算

`_settle_benign_close`（`executor.py:579-592`）**无条件**按 `source="oco"` 合成 `external_close`；
而 `reconcile` 里的同类分支是有 `_external_close_suppressed()`（`executor.py:548-556`，判据正是
「有平仓挂单在飞」）保护的——**`_do_close` 的这一条没有**。F-4 放行后的时序：撤单回 `unknown`
（挂单**保留**）→ 市价 `close_position` → 若那张限价单其实已经成交，OKX 回 `LIVE_NO_POSITION`
→ 走 `_settle_benign_close` → 记一笔 `exchangeOco`（**错归因**）；随后 `_settle_close`
（`executor.py:558-577`）**不清 `self.resting`**，下一轮 `_poll_resting` 读到 `filled` →
`_adopt_close_fill` → 再 `_settle_close` 一次，落一条 `qty=0` 的幽灵成交。
**修法（已落 §4.5.3 / §7.3 #32）**：F-4 分支的 `LIVE_NO_POSITION` 处理改走带
`suppress_external=True` 的 `_settle_benign_close`（复用 `_external_close_suppressed` 的既有判据，
归 `agent` 而非 `exchangeOco`）；市价平仓成功后显式清 `self.resting`。#32 必须覆盖这条时序。

#### R:P1-2 · `adjudicated` 在唯一的判定落点上恒为 `False`，`exitPriority.level` 会长期说谎

§4.3.1 第 3 行把 `adjudicated` 写成 override 的合取项之一，但 §4.1 的管线只有一次
`★W-2 resolve(...)`，位置在**反思之前**；`adjudicated` 由 §4.4.2 的复核官在**反思之后**才产生。
于是这一项在 `resolve_exit_priority` 里恒 `False`，`exitPriority.level` 会停在 `expedite`/`normal`，
而这一轮**实际按 override 放行了**——进 trace、进 `aiStats.horizon.exitOverrideRounds`、进退出归因
的全是错的档位。
**修法（已落 §4.3.1 / §4.4.2）**：删掉 `adjudicated` 合取项，把裁决放行升为**独立档位**
`LEVEL_ADJUDICATED`（判定表继续保持「自上而下取第一条」的纯函数形态，不引入第二次 resolve），
并规定 wire / 统计 / 归因一律取反思之后的最终档位。

#### R:P1-3 · `pending` 轮的 `degrade` 语义没有定义，会把一次成功离场记成一次被拦

§4.4.1 说 pending 轮「`decision` 保持 `close` 继续沿管线下行」，但没说
`HorizonRuntime.gate`（`horizon_runtime.py:229-246`）这一轮返回什么 `degrade`、是否置 `last_reason`、
是否走 `_note_veto`。若沿用今天的 `degrade or reason`，本轮 `degrade` 非空而交易照样发生 ⇒
`self._discipline.note_round(degrade)`（`session.py:1370`）、`_emit_degraded`（推一条降级事件）、
`horizon_gate_status` 显示 `veto`——恰是 §5.5 自己点名禁止的「放行码进 `degrade`」的同一种语义漂移。
**同一条也适用于 `exit_reflection_fail_open(...)`**：v1 §5.5 一边写「放行码绝不进 `degrade`」，
一边把它定义成「**降级码**（放行但如实标注）」并落进 `degrade`。
另有一处落点错误：§4.4.1 的处置发生在 `_apply_horizon_gate` **之后**，而该函数返回时 decision
已被 `_veto()`（`hold_horizon.py:786`）换成新的 hold 对象，调用点**拿不回**原来的 close。
**修法（已落 §4.3.3 / §4.4.1 / §5.5）**：pending 在 `evaluate_horizon_gate` / `HorizonRuntime.gate`
**内部**表达（多回一个 `pending` 位），pending 轮**不 veto、不写 `degrade`、不计 `_note_veto`**；
「差什么」只写进 `exitPriority.reason` 与 trace。`exit_reflection_fail_open` 同样移出 `degrade`，
改挂 `exitPriority.failOpenReason` + trace 反思段。

#### R:P1-4 · `expedite` 顺带解 G3 = 给每一个会话默认放松成本闸，而用户报告的缺陷里 G3 根本没生效

`_first_gate_reason` 的 G2 判据是 `streak < rounds and not confirmWindowElapsed`
（`hold_horizon.py:764-767`）。故 §4.3.1 第 4 行 `intent_streak >= exit_confirm_rounds` 这个析取支
命中时 **G2 本来就已经放行**——该支**唯一的净效果是解开 G3（换手迟滞）**。而 G3 只在
`0 < move < required` 时拦人，解开它就是「在浮盈不足以覆盖一次往返成本时离场」，即**确定性地做一笔
净亏的往返**。A-2 把这条写成「不需要模型学会任何新字段就能兑现的那一半收益」，实际是**把成本闸
默认放松了**。同理 `conviction == 'high'` 只凭模型一句自述就解 G3，没有证据、没有配额、没有 R-6
那样的时间上界。而用户提供的轨迹里 G3 的读数是「需 ≥15.8bps，当前 106.8bps」——**根本没拦人**。
**修法（已落 §4.3.1 / §5.3 / §8.2 A-2）**：`expedite` **只解 G2**；G3 的解除收归两条已经自带约束的
通道——`override`（证据 + 配额 + 60s 地板）与 `exit_pending`（2 小时墙钟上界）。

#### R:P1-5 · G-7 与 §6.2 直接打架，按字面判本轮不可能放行

G-7 冻结「全仓 `place_order(` / `close_position(` / `flatten(` 的调用点计数**净增为 0**」，而 §6.2
明确新增 `session.exit_now() → executor.flatten`。实读全仓 `\.flatten\(` 只有 3 处
（`ai_auto/session.py:614`、`ai_auto/session.py:741`、`live_auto/session.py:199`），本轮必然 +1。
**修法（已落 §8.1 G-7）**：改为「`place_order(` / `close_position(` 净增 0；`flatten(` 允许 +1，
且该新调用点必须复用 `_handle_flatten_result` 的收尾」。

#### R:P1-6 · `/exit` 复用 `_handle_flatten_result` 会给一个**仍在运行**的会话永久盖上「停止时平仓失败」的章

`_handle_flatten_result`（`session.py:659-675`）失败时置 `self.flatten_failed = True` /
`self.residual`，这两个字段被 `_status_payload`（`session.py:2255`、`2307`）读成面板上的终态红条，
且**没有任何复位点**（只在 `__init__` 置 `False`）。`/exit` 是会话**继续运行**的中途动作，一次失败
会让此后每一次 status 都宣称「AI 自动交易停止时平仓失败，请立即到下方实盘面板手动平仓」，即便
下一轮 Agent 自己已经把仓平掉了。
**修法（已落 §6.2）**：`exit_now()` 复用 `_handle_flatten_result` 的**事件扇出与错误推送**，但给它
加 `terminal: bool = True` 形参——`/exit` 传 `False`，只推一条独立的 `AI_TRADE_MANUAL_EXIT_FAILED`，
**不置** `flatten_failed` / `residual`。#33 追加断言：`/exit` 失败后 `status.flattenFailed is False`。

#### R:P1-7 · 行数承诺基于一个**错误的事实**，且会亲手把一个合规文件推过 800 行红线

X-10 / R-7 写「`session.py` 已 2479 行、`hold_horizon.py` 已 796 行，**均已越过** CLAUDE.md 的 800 行
红线」。实测：`session.py` = 2479（确已越线），**`hold_horizon.py` = 796，尚未越线**；v1 承诺的
「净增 ≤55 行」会把它推到约 851 行——**本轮亲手制造一次新的红线违规**。前端同样漏算：
`components/Chart/AiTradePanel.tsx` = **798 行**（§4.6.3 要加三态卡 + 按钮 + 二次确认弹窗，必然越线）、
`components/Agent/AiTradeTraceRound.tsx` = **841 行**（**已越线**，§4.6.1/§4.6.2 还要再加两块），
而 §7.2 的文件表里**一个新组件文件都没有**。
**修法（已落 X-10 / §7.1 / §7.2 / R-7）**：轴 A 的四个纯函数
（`contradicts_exit` / `advance_intent` / `derive_exit_pending_max_s` / `RELEASE_EXIT_PENDING`）
落到**新文件** `ai_auto/exit_intent.py`，`hold_horizon.py` 只保留 `HorizonState` 的两个字段、
`HorizonRule` 的三个投影字段与 `release_reason_for` 末位的 4 行（净增 ≤20 行，**仍在 800 以内**）；
§7.2 追加两个新组件 `components/Chart/AiTradeExitCard.tsx`、
`components/Agent/AiTradeExitPriorityRows.tsx`，并把 `lib/aiTradeHorizon.ts` 补进表里。

#### R:P1-8 · 新增 `exit_priority` 轨迹相位是本轮**唯一的净增静默失败面**，而它承载的数据已经免费搭上了车

§5.3 已经论证得很清楚：`exitPriority` 嵌在 `tradingHorizon` **快照**下，payload / decision / status /
trace 四条载体都是整对象透传，`store.ts` 的 `case "horizon"` 落的正是 `horizonWire: evt.horizon`
（`store.ts:322-327`），**零 reducer 改动**。而 §6.3 又要新开一个 `exit_priority` 相位，代价是
`PHASES` + `AiTradeTraceEvent` 类型 + 轮次初值 + `reduceRoundPhase` 新 case **四处逐键手抄**——
R-9 自己把它列为「漏抄即静默功能缺失」的风险。它唯一的增量收益是「`adjudicated` 在 `horizon`
相位发出之后才产生」。
**修法（已落 §6.3 / §7.2 / R-9）**：删掉 `exit_priority` 相位。最终档位（含 `adjudicated`）
由既有 `reflection_verdict` 相位携带一个加法键（该 case 在 `store.ts:328` 已是手抄区，只多一行），
非反思轮由 `horizon` 相位的整对象透传承载。R-9 随之只剩 `tool_call` 三键一处。

#### R:P1-9 · `_map_decision` 有**两条**构造返回，v1 只提了一条；且 G-2 的早退判据漏了一项

`_map_decision`（`decider.py:404-419`）的 `open_long`/`open_short` 分支是 6 位置参的**提前 return**
（连既有的 `hold_exit_px` 都不带），`close`/`hold`/`cancel_order` 才走末尾那条 7 位置参。反手的动作
字面量是 `open_long`/`open_short`，走的是前一条——只改末尾那条，反手的 `exitConviction` 就永远是
`None`。（与 P0-2 合并后反手先降级为 `close`，本条自然消解，但落点必须写明，否则实施者会照 §6.1
的字面只改一处。）另：§6.1 漏了一句必须改的判据——`build_decision_tool_schema`（`prompts.py:245`）
的恒等早退条件要补成 `if step <= 0 and not allow_limit and not allow_hold_exit and not
allow_exit_priority`，否则 **G-2 必红**。
**修法（已落 §6.1）**：两处落点逐条写明。

#### R:P1-10 · 轴 C 的兑现成交没有说要走 `_dispatch`，成交会落不了库、上不了 WS、进不了经验图

§4.5.2 只写「经 `_exec_lock` 走 `executor.apply([...])`，成交即清空」。而 `executor.apply` 只返回
事件列表；真正把成交落库（`_persist_trade`）/ 打 `exitSource`（`_inject_explain_meta`）/ 喂经验层
（`_experience.note_close`）/ 推 WS 的是 `session._dispatch`（`session.py:388-411`）。参照系
`_maybe_profit_exit`（`session.py:1893-1899`）明确做了 `_dispatch` → `_push_status_if_side_changed`
→ `_report_profit_exit` 三步收尾。漏掉这条尾巴 ⇒ 兑现成功的那笔交易**在所有账本上都不存在**。
**修法（已落 §4.5.2）**：显式写出四步收尾（`apply` → `reconcile` → `_dispatch` →
`_push_status_if_side_changed`），并规定报告函数与 `_report_profit_exit` 同构。

---

### P2（7 条 · 5 条已修、2 条说明取舍）

| # | 关切 | 处置 |
|---|---|---|
| **R:P2-1** | **G-1 的写法不可实现**：「与改动前的 sha256 逐字节一致」在单次提交里没有可执行判据（除非把旧哈希硬编码，那是脆弱反模式）。仓库既有 `tests/test_ai_trade_prompts.py:747 test_unarmed_horizon_keeps_three_prompts_byte_identical` 用的是「**显式传空垫片 vs 完全不传**」的 sha256 相等——这才是可证伪的写法 | **已修** §8.1 G-1，直接点名沿用该模式 |
| **R:P2-2** | **`trace.tool_call_event` 不存在**：`trace.py` 里没有这个函数，`tool_call` 相位由 `session._emit_tool_call_trace`（`session.py:1560-1569`）用通用的 `trace_event("tool_call", ...)` 装配 | **已修** §4.6.1 / §7.1 #11 的指向 |
| **R:P2-3** | **`AiTradeExitOut` 若绑成 `response_model` 会静默吞键**：同类端点 `/stop`(`api/ai_auto.py:402`) 与 `/backstop/repair`(`:411`) 的返回标注都是裸 `dict`，正是为绕开这个本仓库出过事的形态 | **已修** §6.2 显式写「只作文档 schema，端点返回 `dict`」 |
| **R:P2-4** | **`exit_pending` 解 G1 在四个预设上恒为死码、在 `custom` 上是真实的洞**：实算 `pending_max = 2 × confirm_window` 与 `min_hold` 在四个预设上**完全相等**（hourly 180/180、intraday 7200/7200、short 21600/21600、trend_hold 43200/43200），G1 永远解不到东西；而 `custom` 允许 `minHoldSeconds` 与 `targetHoldMinSeconds` 脱钩，一旦脱钩 G1 就会被提前解除，与「G1 是最硬的一条」自相矛盾 | **已修** §4.2.3：`RELEASE_GATES[exit_pending] = (G2, G3)`，G1 只由既有三条豁免解除 |
| **R:P2-5** | **回测侧三处口径没交代**：① `apply_reflection` 的 `fail_open_actions` 在回测调用点（`backtest.py:848`，4 位置参）传不传；② `_execute` 的打标槽（`backtest.py:893-896`）要不要写 `agentOverride`；③ 回测 `note_intent` 用 `now_wall=float(ts)`（**根时间戳而非墙钟**），衰减与 `exit_pending` 在这个时钟上的语义（本仓库已为「双时钟」出过事） | **已修** §7.1 #12 三条逐一写明 |
| **R:P2-6** | **既有用例的兼容性靠一条隐式依赖**：`tests/test_ai_trade_horizon_runtime.py:122 test_note_intent_resets_on_other_actions_but_holds_on_degrade` 断言的正是今天「一轮 hold 即清零」，它靠 `_rule()` 不传新字段、吃 `HorizonRule.exit_intent_decay_rounds` 的默认 `0` 才继续绿。实施者很容易顺手把 `HorizonRule` 的默认也改成 2 | **已修** §7.3 写明「该用例零改动通过，是 C-2 默认值分层（settings=2 / HorizonRule=0）的可证伪凭据」 |
| **R:P2-7** | **F-2 留存点的表述可再收紧**：§4.4.3 说留存 `stage1_decision`（位于 `_apply_base_rule_gate` 之前）。今天基础策略层不拦 close，故两者相同；但这是一条**隐式依赖**，将来任一层开始改写 close 的 `reason` 就会静默失真 | **已修** §4.4.3 改为「留存**进入 `_apply_horizon_gate` 那一刻**的 decision 引用」，并由 G-4 一并冻结 |

---

### 未构成缺陷、但评审侧确认过的三条（供实施者免于重复调查）

1. **`AI_TRADE_*` 的 prod 透传白名单属实**：`desktop/src/lib/config.js:80` 的前缀判据里确有
   `key.startsWith('AI_TRADE_')`，C-1…C-13 十三项无需改桌面端。
2. **`TradeDecision` 的末位加字段模式安全**：`_veto`（`hold_horizon.py:786`）与
   `_hold`（`decider.py:128`）都是**新建对象**而非 `replace`，故被否决轮天然不会携带
   `exit_conviction` 泄漏到下游；而 `reflection.apply_reflection` 的收缩分支用的是
   `replace(decision, size_fraction=...)`（`reflection.py:52`），新字段会被原样保留——两侧都对。
3. **§4.4.2 的 fail-open 极性写对了**：`ReflectionVerdict` 在真实 abort 时 `degrade_reason is None`
   （`decider.py:511-512`），在四种基础设施失败时 `degrade_reason` 非空（`_fail_closed`，
   `decider.py:132-135`）。v1 的判据 `verdict.degrade_reason is not None and action in
   fail_open_actions` **精确地**只放行后者，R-3 缓解①成立。

---

## 一、概述（Overview）

【AI 自动交易】当前存在一条**已被线上轨迹坐实的失效路径**：Agent 已经识别出退场信号、并在多轮
研判中确认「持仓理由已经失效、应当立即市价兑现」，但这次 `close` 被【交易周期（持仓时长铁律）】层
的 **G2（离场连续确认）** 拦下，降级为 `hold`；下一轮 Agent 因为看不到自己上一轮的离场提议、又被
一句「离场连续确认 1/2」误导，给出一轮观望，而这一轮观望**把连续确认计数与确认窗口起点一并清零**。
于是整条链路进入一个**没有出口的活锁**：持仓 10.31 小时、约 20 个决策轮之后，`exitConfirmStreak`
仍然停在 `1`，`confirmWindowRemainingSeconds` 仍然是**满窗 1 小时**——那把本应保证「锁必须有钥匙」
的墙钟上界，被每一次犹豫重新上了一遍。

问题的性质不是「某一个参数设得太紧」，而是**离场这件事在整条决策链上没有优先级**。系统对开仓侧
建了六道闸（基础策略 / 冷却 / 再入场 / 换手预算 / 回撤刹车 / 最小 edge），每一道都写明「平仓
`close` 不受影响」；但真正约束 `close` 的那一层（持仓周期三闸）却把「Agent 的离场确信度」当成一个
不存在的量——无论模型是随口一提，还是在思维链里逐条否掉了持仓理由、又经独立复核官背书，G2 数到的
都只是「轮」。用户的诉求正是这一点：**当 Agent 经过多轮 ReAct 确认应当退场时，应当有一条最高优先级
的通道支持它平仓离场，而不是被硬条件卡死。**

本轮沿着四条轴交付这条通道，四条轴各自独立可回滚、共用一套 wire 与文案真源：

- **轴 A · 离场意图账本**：把脆弱的「一轮非离场即清零」改成**带衰减的跨轮账本**——一次犹豫不再
  抹掉整条论证链，确认窗口的起点也不再被反复重置，`horizon:P0-3` 立下的「锁必须有钥匙」这条不变式
  第一次真正成立。并补一把第二钥匙：离场意图挂起超过上界 ⇒ **G2/G3 解除**（【R:P2-4】G1 不在此列，
  它只由既有三条豁免解除）。
- **轴 B · 离场优先级（五档）**：把「离场确信度」提升为一等公民。它由**模型显式声明**
  （`exitConviction: normal|high|final`）× **跨轮意图账本** × **独立复核官的第二意见**三者合成，
  而不是任何单方说了算。`expedite` **只解 G2**、`adjudicated` 与 `override` 解 G1/G2/G3
  （【R:P1-4】：G3 是成本闸，「浮盈还不够付一次往返成本」不能由一句自述解开），并带证据要求、
  会话配额与完整记账；**作用域钉死在 `close`，反手一律不进本层**（【R:P0-2】，X-1/X-1b）。
  同时反转一条极性错误：**`close` 的反思 fail-closed 改为 fail-open**——一次 LLM 超时
  不该锁死一笔真实持仓。
- **轴 C · 离场意图兑现**：把离场从「一次性动作」升级为**有状态、会重试、有超时告警的意图**。
  今天利润保护层的平仓已经享有「未成交即下一次盯市重试」的待遇，而 Agent 自己的平仓是一次性的，
  且在挂单槽位撤销未获确认时**被静默丢弃**。本轮把这条待遇对齐，并顺手修掉执行侧那条静默路径。
- **轴 D · 可解释性与人机交互**：用户截图里那句「决策工具 · 观望」是本轮最刺眼的一处不诚实——
  模型提交的是 `close`，界面上只剩 `hold`。本轮让轨迹卡片保真「提议了什么、被谁拦下、还差什么」，
  给持仓卡加上「离场进行中 / 离场受阻」的真实状态，并提供一个不经模型的**一键立即离场**兜底。

四条轴合起来回答用户的问题「是否应该有对应的优先级」：**应该，而且优先级必须是可举证、可配额、
可事后核对的**。一条只要模型喊一嗓子就放行的通道，会在三天内退化成「每一轮都喊 final」；一条
永远不放行的通道，就是今天这个 10 小时走不掉的仓位。本方案取的是中间那条：**证据越强、放行越快，
且每一次放行都留下可以回头算账的凭据。**

---

## 二、问题取证（可复现）

### 2.1 线上轨迹与源码的逐字对齐

用户提供的执行记录里有四个数字，探针**逐字复现**了其中三个，第四个由第三个反推：

| 轨迹原文 | 源码出处 | 探针输出 | 结论 |
|---|---|---|---|
| `已持仓 10.31 小时 / 最短 2 小时（已满足）` | `hold_horizon.derive_min_hold_s` = `0.5 × target_hold_min_s`；`intraday_swing.target_hold_min_s = 14400` | `derive_min_hold_s = 7200.0 s` | 2 小时 ✅ |
| `离场确认 1/2（窗口剩余 1 小时）` | `hold_horizon.derive_confirm_window_s` = `min(0.25×14400, 4×3600)` | `derive_confirm_window_s = 3600.0 s = 1.0 h` | 1 小时 ✅ |
| `horizon_exit_unconfirmed(1/2)` | `hold_horizon.py:753 _first_gate_reason` 的 G2 分支 | `gate_reason=horizon_exit_unconfirmed(1/2)` | ✅ |
| `换手迟滞：需 ≥15.8bps，当前 106.8bps` | `requiredMoveBps = hysteresis_k × cost_bps`；`intraday_swing.hysteresis_k = 1.5` | 反推 `cost_bps ≈ 10.53` | 口径自洽 ✅ |

**最关键的一处对齐**：轨迹说「窗口剩余 1 小时」，而窗口本身**就是** 1 小时。这意味着
`first_exit_intent_wall` 是**本轮**才被设上的——即在这次离场之前的整整 10.31 小时里，
账本上一次离场意图都没有幸存下来。`intraday_swing` 的建议唤醒周期是 30 分钟，10.31 小时 ≈ **20 轮**。

### 2.2 F-1（P0）· 一轮非离场动作把连续确认计数与窗口起点一并清零

`horizon_runtime.py:197-220`：

```python
def note_intent(self, stage1_action, stage1_degrade, *, position_side, now_wall) -> None:
    if self.rule is None:
        return
    if gated_kind(stage1_action, position_side, gate_reverse=self.rule.gate_reverse):
        first = self.state.first_exit_intent_wall
        self.state = replace(
            self.state, exit_confirm_streak=self.state.exit_confirm_streak + 1,
            first_exit_intent_wall=float(now_wall) if first is None else first)
        return
    if stage1_degrade is not None:
        return
    self._reset_intent()          # <-- 一轮真实 hold 即走到这里

def _reset_intent(self) -> None:
    self.state = replace(self.state, exit_confirm_streak=0, first_exit_intent_wall=None)
```

探针（附录 A · probe 1）跑「close → hold → close」三轮，唤醒间隔 30 分钟：

```
round1 act=close streak=1 first_wall=1038916.0 windowRemain=3600.0 elapsed=False gate_reason=horizon_exit_unconfirmed(1/2)
round2 act=hold  streak=0 first_wall=None       windowRemain=None   elapsed=False gate_reason=None
round3 act=close streak=1 first_wall=1042516.0  windowRemain=3600.0 elapsed=False gate_reason=horizon_exit_unconfirmed(1/2)
```

`round3` 与 `round1` **逐字段相同**。两个结论：

1. **G2 的计数不是「离场论点的成熟度」，而是「连续两个决策轮恰好都以 close 收尾」**——后者在
   一个 30 分钟唤醒、行情来回震荡的会话里是小概率事件。
2. **更严重的是 `first_exit_intent_wall=None`**：`horizon:P0-3` 明确写下「锁必须有钥匙——连续确认
   带墙钟上界 `confirm_window_s`，超时即视为确认完成。没有它，离场推迟没有上界」。而清零把这把钥匙
   连同锁一起换了新的：**只要模型在任意两次离场提议之间犹豫一轮，上界就永远到不了**。这条不变式
   今天在**实现层被自己违反**，且不会报任何错。

### 2.3 F-2（P0）· 被门禁降级后，模型自己的上下文里没有「我提议过 close」这件事

`session.py:1348` 把**门禁后**的 decision 交给 `decider.commit_round`，`decider.py:519-569`
据它装配 `decision_input` 与 journal 条目：

```python
decision_input = {"action": decision.action, ..., "reason": decision.reason, ...}
if reflection and reflection.get("vetoed"):
    tool_result_text = json.dumps({**executed, "reflection": reflection}, ensure_ascii=False)
else:
    tool_result_text = json.dumps(executed, ensure_ascii=False)
```

被 G2 拦下的那一轮，`decision.action` 已经是 `"hold"`、`decision.reason` 已被 `hold_horizon._veto`
（`hold_horizon.py:780`）改写成降级码。于是模型下一轮读到的自己的历史是「上轮我决定观望」。
**注意这条不对称**：反思否决轮会把 `reflection` 注记合并进 `tool_result`，让模型看见「这轮 hold
是被否决的开仓」——而门禁否决轮**没有对等的注记**。F-1 断的是系统侧的计数，F-2 断的是模型侧的
论证连续性，两者互为因果，共同构成活锁。

### 2.4 F-3（P0）· 反思层对 `close` 是 fail-closed：一次 LLM 超时即锁死一笔真实持仓

`session.py:1466-1490` 的 `_reflect_before_execute` 只对 `hold` / `cancel_order` 直通，`close`
一律进反思；`reflection.py:16-43` 的 `apply_reflection` 在 `degrade_reason` 非空时无差别降级为
`hold`。探针（附录 A · probe 2）：

```
[a] close + reflection_timeout     -> action='hold' degrade='reflection_timeout'
[a] close + reflection_llm_error   -> action='hold' degrade='reflection_llm_error'
[a] close + reflection_no_tool     -> action='hold' degrade='reflection_no_tool'
[a] close + reflection_invalid     -> action='hold' degrade='reflection_invalid'
[a] close + real abort             -> action='hold' degrade='reflection_abort'
```

`ai_trade_reflection_timeout_s = 45.0`、`ai_trade_reflection_max_retries = 2`（`config.py:176-179`）
⇒ 单轮最坏 **3 × 45s ≈ 135s** 的等待之后，这笔离场仍然不发生。这与 `hold_horizon.py` 模块头自己
写下的第 2 条不变式**直接冲突**：

> **fail-open**（§2.3.4）：本层拦的是 `close`，而 `close` 是风险收敛动作。任何「算不出 ⇒ 不许平仓」
> 的设计都会在断网 / 暖机 / 脏数据的瞬间把一笔真实持仓锁死——那比它想解决的问题严重一个数量级。

同一条纪律在**隔壁模块**被写反了。顺带记一条 **F-3b（P2）**：`session.py:1880-1882`
`_maybe_profit_exit` 的 docstring 声称「共识门禁、MA 通道铁律、反思层……后三者一律不拦 `close`，
故不存在『保护要平、门禁不让』的死锁」——前两者属实（`base_rules._GATED_ACTIONS` /
`signals.apply_consensus_gate` 均只作用于 `open_*`），**反思层这一句是假的**。文案必须改。

### 2.5 F-4（P0）· 挂单槽位会把一次市价离场整轮静默丢弃

`executor.py:329-355 _do_close` → `executor.py:377-397 _prepare_resting_slot`：

```python
outcome, events = await self._cancel_resting("replace", ts)
if outcome == "canceled":
    return events, True
if outcome == "filled":
    return events + [...], False
return events + [self._status_event(
    "原挂单撤销未确认，本轮不下新单（绝不双挂），待下轮核对挂单状态")], False
```

而 `_cancel_resting`（`executor.py:409-430`）在 `OKXError`（连通类失败）或 OKX 未确认时返回
`"unknown"`。`_do_close` 收到 `slot_free=False` 后 `return pre`——**这一轮什么也没平，且 `degrade`
为 `None`**：轨迹上会显示「决策工具 · 平仓」而没有任何成交、没有任何降级码。探针（probe 2 · [b]）
对两段源码做结构断言，均为 `True`。

这条路径**不只影响 Agent 的主动离场**：`_apply_profit_exit`（`session.py:1903`）走的是同一个
`executor.apply → _do_close`，所以利润保护层的兜底平仓同样会被丢弃（它至少有 `_report_profit_exit`
如实报「本次平仓未成交，将在下一次盯市重试」——而 Agent 的离场连这句都没有）。

**修法的安全前提已经在仓库里**：市价平仓走的是 `okx/trading.py:250-253`
`client.close_position(..., auto_cxl=True)`，`trade_client.py:204-213` 明确 `autoCxl` 由**交易所侧**
撤掉该合约全部未成交挂单。因此「本地撤单未获确认」并不构成「不能下市价平仓」的理由——重复平仓在
`LIVE_NO_POSITION` 分支（`executor.py:337`）已有良性处理，而挂单会被交易所自己撤掉。**开仓路径
不适用这条论证**（双开风险真实存在），故本轮**只放开 close 的市价分支**。

### 2.6 F-5（P1）· 一轮之内的 ReAct 深度完全不被计量

`decider.decide` 内部是**真步进的思维链**（`ai-trade-reasoning-chain`），`_reflect_before_execute`
之后还有一名独立复核官在同一证据面上重新研判——两次独立推理。但 `_first_gate_reason`
（`hold_horizon.py:753`）只读 `snapshot["exitConfirmStreak"]`，它数的是**轮**。用户诉求里的
「ReAct 多轮确认」在系统里**没有任何落点**，模型也**没有任何途径**表达「这次不是随口一提」。

### 2.7 F-6（P1）· 「决策工具 · 观望」：界面把模型的提议抹掉了

`session.py:1559-1569 _emit_tool_call_trace` 的 docstring 写着「门禁后最终决策工具入参（与真实执行
一致，降级则如实呈现 hold）」。「与真实执行一致」是对的，「如实」是不对的：**模型提交的入参是
`close`**，而卡片上只剩 `hold`。用户唯一能看到的线索是折叠在下面的一行降级码。这正是用户在需求里
贴出那段轨迹时最直观的困惑来源。

### 2.8 现有各层对 `close` 的豁免矩阵（实读结论，供实现者免于重复调查）

| 层 | 文件 | 是否拦 `close` |
|---|---|---|
| 基础策略 MA 通道铁律 | `base_rules.py:44,211` `_GATED_ACTIONS=("open_long","open_short")` | ❌ 不拦 |
| 持仓周期三闸 G1/G2/G3 | `hold_horizon.py:558 gated_kind` | ✅ **拦**（本轮主战场） |
| 利润保护冷却 | `profit_cooldown.py:23,74` | ❌ 不拦 |
| 再入场冷却 / 换手预算 G4/G5 | `turnover_discipline.py:323-353` | ❌ 不拦（反手只降级为 close） |
| 兜底覆盖 G6 | `exchange_backstop.apply_backstop_gate` | ❌ 只拦同向加仓 |
| 回撤刹车 / 连亏熔断 | `drawdown_brake.py:37,196` | ❌ 不拦 |
| 共识门禁 | `signals.py:252` | ❌ 不拦 |
| 最小 edge 门禁 | `edge_gate.py:39,144` | ❌ 不拦 |
| 下单前二次反思 | `session.py:1481` + `reflection.py:41` | ✅ **拦**（F-3） |
| 执行侧挂单槽位 | `executor.py:337` | ✅ **静默丢弃**（F-4） |

⇒ 全链路上只有 **三处**会阻止一次 Agent 主动离场，本轮逐一处理。

---

## 三、不变式（改本特性的任何一行之前先读）

- **X-1 · 离场永远优先于开仓。** 本轮新增的任何逻辑，**只可能让一次 `close` 更容易发生**，
  绝不可能让任何一次 `open_*` 更容易发生。任何一处代码若同时影响两侧，即为设计错误。
  **【R:P0-2】这条不变式的作用域必须显式排除「反手」**：`hold_horizon.gated_kind`
  （`hold_horizon.py:558-573`）返回 `'close' | 'reverse' | None`，反手（持多时 `open_short` /
  持空时 `open_long`）**同样受持仓周期三闸约束**，而它携带一条**开仓腿**。故本层的作用域被钉死为
  `gated_kind(...) == 'close'`——反手一律不进本层（处置见 X-1b）。把优先级施加到反手上，等于用
  「帮你离场」的名义解开 G1 去开一笔新仓，是本不变式最容易被违反的一种形态，且 G-5 的 AST 扫描
  **守不住它**（泄漏点在 `hold_horizon` 的接线，不在 `exit_priority.py`）。
- **X-1b · 反手走「降级为 close」，不走优先级。** 若模型在受约束状态下提出反手且本层判定它应当
  离场，处置沿用本仓库既有先例 `turnover_discipline.apply_turnover_gate`
  （`turnover_discipline.py:323-353`）：**保留退出腿、丢弃开仓腿**，把决策降级为 `close`，再由本层
  按 close 重新判定。这样「退出永远能执行」与「开仓永远受全部六道闸约束」两件事同时成立。
- **X-2 · 止损永不经手。** 交易所侧 OCO、利润保护层、会话级止盈止损、executor 的强制时间平仓
  一律**不经**离场优先级层（它们本就不经持仓周期层）。本轮不新增、不改写这四条路径的任何一行。
- **X-3 · 绝不新增下单写路径。** 轴 C 的兑现重试复用 `executor.apply([Signal(side="close")])`
  → `_do_close`，与 `session._apply_profit_exit` 逐字同款；轴 D 的一键离场复用
  `executor.flatten` + `_handle_flatten_result`。全仓 `place_order` / `close_position` 调用点数量
  **零净增**（G-7 冻结）。
- **X-4 · 确信度不由任何单方决定。** `override` 必须同时满足「模型显式 `final`」×「证据非空」×
  「跨轮账本达门槛」×「会话配额未尽」×「已过绝对地板 `override_min_hold_s`」。删掉其中任意一个
  合取项都会把这条通道变成「模型的自助放行按钮」。
  **【R:P0-1】「独立复核官背书」不是这四项中任何一项的替代品**，只是**账本不足**这一项的替代路径：
  复核官只能替 `ledger_short` 背书（它看得见本轮证据面），**不能**替 `quota_exhausted`（配额是会话级
  的资源约束，与本轮证据无关）、`evidence_missing`（没有证据可看）、`min_hold_floor`（墙钟事实，
  不是判断题）背书。v1 让这三种降档也走裁决放行，等于一次删掉三个合取项——本条即为此而收紧。
- **X-4b · 裁决放行与 `override` 共用同一份配额。** 复核官背书放行同样 `overrides_used += 1`，
  且自增**之前**必须再判一次 `< max_overrides_per_session`。否则「配额用尽 ⇒ 走裁决 ⇒ 配额继续涨」
  会让 `max_overrides_per_session` 变成一个只在文档里存在的数字。
- **X-5 · `override` 级离场跳过反思，其余级别不变。** 理由：`override` 的前置条件已经包含跨多轮
  的持续意图与显式证据，而反思复核官是**无状态单轮**调用（`decider.reflect` 明确不拼 `_context`），
  让它去推翻一个它看不见的跨轮账本，是用更弱的证据推翻更强的证据。副作用是省一次 LLM 调用。
  **【R:P1-2】本条与 X-4 不构成循环**：`override` 的判定里**没有** `adjudicated` 这一项（见 §4.3.1
  第 3 条），复核官背书是**另一个独立档位** `adjudicated`，它当然要经过反思——它就是反思的产物。
  v1 把 `adjudicated` 写进 `override` 的合取项，同时又让 `override` 跳过反思，导致该项在唯一的
  判定落点上恒为 `False`、`exitPriority.level` 长期说谎；本轮拆成两档，判定表恢复为可求值的纯函数。
- **X-6 · fail-open 是 `close` 侧的唯一合法极性。** 本轮之后，凡「算不出 / 调不通 / 超时」导致的
  不确定，一律**放行** `close`；只有**模型的真实终态裁决**（`reflection_abort`）与**确定性的
  硬闸**（未解除的 G1/G2/G3）才可以拦。
- **X-7 · 未装配即逐字节回退。** `ai_trade_exit_priority_enabled=False` ⇒ `exitPriority` 整键缺席、
  决策工具 schema 恒等于 `DECISION_TOOL_SCHEMA`、prompt 三份（系统 / 决策 / 反思）逐字节不变、
  `note_intent` 回退到今天的「一轮即清零」。`ai_trade_exit_commitment_enabled=False` 同理。
  G-1 用 sha256 冻结门禁守这一条。
- **X-8 · 文案与判定共用同一份真源。** 新增的所有中文一律由 `hold_horizon_text` / 后端 wire 下发，
  前端**一个数、一个标签都不硬编**（沿用 `hold_horizon_text` 模块头的 `[R-5]` 教训）。
- **X-9 · 每一次优先放行都留凭据。** `override` / `adjudicated` / `failOpen` 三类放行各自计数，
  进 `aiStats.horizon`、进退出归因、进周期验收卡。**「Agent 用掉了几次最高优先级、其中几次事后
  证明是对的」必须是一个能查的数**——否则这条通道无法被证伪，也就无法被信任。
- **X-10 · 纯核心不依赖调用顺序。** 轴 A/B 的判定全部落在无 IO、无 LLM、无 broker 的纯函数里
  （`exit_intent.py` / `exit_priority.py`），运行时只做状态推进与装配（沿用 `horizon_runtime` 的
  既有分工）。
  **【R:P1-7】行数基线（实测，2026-08-25）与硬承诺**：

  | 文件 | 现状 | 红线 | 本轮承诺 | 结果 |
  |---|---|---|---|---|
  | `ai_auto/session.py` | **2479** | 800 | 净增 ≤ 60 | 已越线（既有欠账，本轮不加剧） |
  | `ai_auto/hold_horizon.py` | **796** | 800 | 净增 **≤ 20** | **仍在 800 以内** |
  | `components/Chart/AiTradePanel.tsx` | **798** | 800 | 净增 **0**（新逻辑全落新组件） | 仍在 800 以内 |
  | `components/Agent/AiTradeTraceRound.tsx` | **841** | 800 | 净增 **0**（同上） | 已越线（既有欠账，本轮不加剧） |

  v1 声称「`hold_horizon.py` 已越过红线」是**事实错误**（796 < 800），而它承诺的「净增 ≤55 行」
  会**亲手把一个合规文件推过红线**。故本轮改为：轴 A 的四个纯函数（`contradicts_exit` /
  `advance_intent` / `derive_exit_pending_max_s` / `RELEASE_EXIT_PENDING` 常量）落到**新文件**
  `ai_auto/exit_intent.py`；`hold_horizon.py` 只保留 `HorizonState` 的两个字段、`HorizonRule` 的
  三个投影字段、`release_reason_for` 末位 4 行与 `evaluate_horizon_gate` 的两行接线。前端同理：
  §4.6.2/§4.6.3 的两块 UI 全部落到两个新组件文件（见 §7.2），既有两个大文件**只加 import 与一行
  挂载**。节点 #3 的 Review 须实测四个文件行数并写进提交说明。

---

## 四、技术设计

### 4.1 总览：四条轴与管线插点

决策轮管线（`session._run_decision_round`，`session.py:1277`）本轮**不改顺序**，只在四处加薄封装：

```
decide()  ← 第一阶段真实意愿（含思维链）
  │
  ├─ _update_hold_streak / discipline.note_intent / _update_profit_hold_streak
  ├─ ★W-1 _horizon.note_intent(...)              轴 A：改为带衰减的账本推进
  ├─ ★W-2 self._exit_priority = resolve(...)     轴 B：算本轮离场优先级（纯同步）
  ├─ _apply_base_rule_gate                       （不拦 close）
  ├─ ★W-3 _apply_horizon_gate(... priority)      轴 B：按 level 解闸；final 但账本不足 ⇒ 挂起 pending
  ├─ _apply_profit_cooldown_gate / turnover_gate / backstop_gate / sizing.gate
  │   / _apply_consensus_gate / _apply_edge_gate （六层，均不拦 close）
  ├─ ★W-4 _reflect_before_execute(... pending)   轴 B2：override 跳过；pending 由复核官裁决；
  │                                                     close 的 fail-closed 反转为 fail-open
  ├─ ★W-4b self._exit_priority = resolve(replace(inp, adjudicated=True))
  │                                              【R:P1-2】复核官 confirm 后**重跑同一个纯函数**
  │                                              取最终档位；wire/统计/归因一律取这一份
  ├─ _apply_sizing                               （不改 action）
  ├─ _execute_decision
  └─ ★W-5 _exit_commit.arm_if_unfilled(...)      轴 C：未成交即武装兑现意图

盯市 tick（session._monitor_tick，session.py:1034）
  ├─ executor.reconcile                          （挂单状态在此收敛）
  ├─ ★W-6 await self._settle_exit_commitment()   轴 C：到点重试 / 完成清空 / 超时告警
  └─ ... 既有各步
```

### 4.2 轴 A · 离场意图账本（修 F-1）

#### 4.2.1 状态字段（`hold_horizon.HorizonState`，`hold_horizon.py:187`）

在 `HorizonState`（`hold_horizon.py:187`）新增两个字段（末位带默认值 ⇒ 既有构造零改动）：

```python
    # 自上一次记到离场意图以来，连续「未提离场」的轮数（衰减计数器）。
    exit_intent_lapses: int = 0
    # 最近一次离场意图的墙钟（供 prompt 说明「你上一轮没提，本轮按衰减计入」）。
    last_exit_intent_wall: float | None = None
```

#### 4.2.2 纯函数（**新文件** `ai_auto/exit_intent.py`，约 60 行 ·【R:P1-7】）

> v1 把这三个纯函数放在 `hold_horizon.py`（796 行）里，会把它推过 CLAUDE.md 的 800 行红线。
> 本节的全部代码落在新文件 `exit_intent.py`；`hold_horizon.py` 只 `from .exit_intent import ...`
> 并在 `RELEASE_GATES` / `release_reason_for` 上接两处线。依赖方向单向：
> `exit_intent` **不 import** `hold_horizon`（它只收 `HorizonState` 的三个标量字段与一个 `bool`，
> 用 `dataclasses.replace` 由调用方回写），故不成环。

```python
def contradicts_exit(action: str, position_side: str) -> bool:
    """本轮动作是否与「离场」直接矛盾（同向加仓）——矛盾即账本立即清零，不走衰减。"""
    return ((action == "open_long" and position_side == "long")
            or (action == "open_short" and position_side == "short"))


def advance_intent(state: HorizonState, *, gated: bool, contradicts: bool,
                   degraded: bool, now_wall: float, decay_rounds: int) -> HorizonState:
    """离场意图账本推进（§4.2.2 冻结判定表）。纯函数：无 IO、不依赖调用顺序。

    | 本轮 | 动作 |
    |---|---|
    | 受控换手（close / 反手） | `streak+1`；`lapses=0`；首次意图锁 `first_exit_intent_wall` |
    | 降级轮（llm_error / timeout / low_confidence） | **整体不变**（既非真实意愿也非真实观望） |
    | 同向加仓（与离场直接矛盾） | **立即清零**（streak / lapses / first 全清） |
    | 其余（hold / cancel_order） | `decay_rounds<=0` ⇒ 立即清零（逐字回退今天）；否则 `lapses+1`，`lapses>=decay_rounds` 才清零 |
    """
```

- `decay_rounds` 由 `HorizonRule.exit_intent_decay_rounds` 携带，来源 `C-2`；**`0` 是逐字回退开关**。
- **`first_exit_intent_wall` 只在真正清零时置 `None`**——这一行就是 F-1 的正修，也是
  `horizon:P0-3` 那把钥匙重新生效的地方。

#### 4.2.3 第二把钥匙：`exit_pending` 豁免

窗口到期（`confirmWindowElapsed`）只放行 **G2**；G1（最短持仓）与 G3（换手迟滞）仍然拦着。而 G3 恰好
在「浮盈从 171bps 缩到 106bps、再缩到 10bps」这条路径的**终点**开始生效——也就是模型在轨迹里担心的
「浮盈坐过山车」正在发生的那一刻。故新增第四条豁免：

```python
RELEASE_EXIT_PENDING = "exit_pending"
# 【R:P2-4】**只解 G2/G3，不解 G1**。理由是一条实算结论：按默认派生式，四个预设的
# `pending_max = 2 x confirm_window` 与 `min_hold` **完全相等**（hourly 180/180、intraday 7200/7200、
# short_swing 21600/21600、trend_hold 43200/43200），故解 G1 在预设上恒为死码；而 `custom` 允许
# `minHoldSeconds` 与 `targetHoldMinSeconds` 脱钩，一旦脱钩，解 G1 就变成一个真实的洞，与 §4.3.1
# 「G1 是最硬的一条、连 override 也留 60s 内核」直接冲突。G1 只由既有三条豁免
# （loss_release / stale_px / max_hold_bars）解除，本轮不新增第四条。
RELEASE_GATES[RELEASE_EXIT_PENDING] = (GATE_EXIT_CONFIRM, GATE_HYSTERESIS)


def derive_exit_pending_max_s(confirm_window_s: float, mult: float) -> float:
    """离场意图挂起上界 = `mult × confirm_window_s`；窗口为 0 或 mult 为 0 ⇒ 0（本豁免关闭）。"""
    window = max(0.0, float(confirm_window_s))
    return 0.0 if window <= 0 or mult <= 0 else float(mult) * window
```

判定落在 `release_reason_for`（`hold_horizon.py:599`）现有的「自上而下取第一条」链的**末位**
（优先级低于 `loss_release` / `stale_px` / `epoch_estimated` / `max_hold_bars`，四条既有豁免的
相对顺序**逐字不变**）：

```python
    pending_max = rule.exit_pending_max_s
    first = state.first_exit_intent_wall
    if (pending_max > 0 and first is not None
            and float(facts.now_wall) - float(first) >= pending_max):
        return RELEASE_EXIT_PENDING
```

`intraday_swing` 默认：`confirm_window_s=3600`、`mult=2.0` ⇒ **2 小时**。语义是「一条离场论点在账本上
活了 2 小时还没走成 ⇒ 本层放手」。

#### 4.2.4 快照加法键（`build_snapshot`，`hold_horizon.py:649`）

```python
        "exitIntentLapses": state.exit_intent_lapses,
        "exitIntentAgeSeconds": (None if state.first_exit_intent_wall is None else
                                 round(float(facts.now_wall) - float(state.first_exit_intent_wall), 3)),
        "exitPendingMaxSeconds": rule.exit_pending_max_s,
```

三键**恒在场**（不是条件写入）：形状稳定是本仓库 journal / trace 的既有纪律（`decider:R-06`）。
未装配时整个 `tradingHorizon` 键本就缺席，故 X-7 不受影响。

### 4.3 轴 B · 离场优先级 P0–P3（新纯核心 `exit_priority.py`，约 200 行）

#### 4.3.1 值域与判定表（**冻结**）

```python
LEVEL_NONE       = "none"        # 本轮不是**平仓**（含 hold / 开仓 / 反手）—— 本层整体空转
LEVEL_NORMAL     = "normal"      # P1：今天的行为，三闸照旧
LEVEL_EXPEDITE   = "expedite"    # P2：只解 G2（【R:P1-4】不解 G3）
LEVEL_ADJUDICATED = "adjudicated"  # P3a：复核官背书，解 G1/G2/G3（【R:P1-2】独立档位）
LEVEL_OVERRIDE   = "override"    # P3b：解 G1 / G2 / G3（G1 仍留一个绝对地板，见下）

CONVICTION_NORMAL, CONVICTION_HIGH, CONVICTION_FINAL = "normal", "high", "final"
_CONVICTIONS = (CONVICTION_NORMAL, CONVICTION_HIGH, CONVICTION_FINAL)
```

| # | 条件（自上而下取第一条命中） | level | 解闸 |
|---|---|---|---|
| 1 | `gated_kind(...) != "close"`（**非平仓**——含 `None` 与 `reverse`，【R:P0-2】） | `none` | — |
| 2 | 未装配（`rule is None` / 总闸关） | `normal` | — |
| 3 | `conviction == final` **且** `evidence_ok` **且** `intent_streak >= override_rounds` **且** `overrides_used < max_per_session` **且** `hold_seconds >= override_min_hold_s` | `override` | `G1,G2,G3` |
| 4 | `adjudicated`（复核官已背书；**只可能由 §4.4.2 的第二段回填**，【R:P1-2】） | `adjudicated` | `G1,G2,G3` |
| 5 | `conviction in (high, final)` **或** `intent_streak >= exit_confirm_rounds` | `expedite` | **`G2`** |
| 6 | 其余 | `normal` | — |

**【R:P0-2】第 1 条改为 `!= "close"`**：`gated_kind`（`hold_horizon.py:558-573`）对反手同样返回非
`None`。反手携带一条开仓腿，给它解闸就是让一次 `open_*` 更容易发生，违反 X-1。反手的处置见 X-1b
（降级为 `close` 后重新进本层），本层对它恒返回 `none`。

**【R:P1-2】第 4 条是独立档位，不是第 3 条的合取项**：`adjudicated` 由复核官在**反思之后**产生，
而 `resolve_exit_priority` 跑在反思**之前**（W-2）。把它写成 `override` 的析取支会让该项在唯一的
判定落点上恒为 `False`，`level` 停在 `expedite` 而这一轮实际按 override 放行——trace、
`aiStats.horizon.exitOverrideRounds`、退出归因三处全错。拆成独立档位后，判定表仍是一次求值的纯函数；
反思之后由 §4.4.2 用 `replace(inp, adjudicated=True)` **重跑同一个纯函数**得到最终档位，wire /
统计 / 归因一律取这一份。

**【R:P1-4】第 5 条 `expedite` 只解 G2，不解 G3**：`_first_gate_reason` 的 G2 判据是
`streak < rounds and not confirmWindowElapsed`（`hold_horizon.py:764-767`），故当第 5 条经
`intent_streak >= exit_confirm_rounds` 命中时 **G2 本来就已经放行**——若同时解 G3，该析取支唯一的
净效果就是**解开换手迟滞**。而 G3 只在 `0 < move < required` 时拦人（浮亏离场恒放行，
`hold_horizon.py:770-776`），解开它等于「在浮盈不足以覆盖一次往返成本时离场」，即**确定性地做一笔
净亏的往返**——那正是本层最初被建起来要挡的东西。用户提供的轨迹里 G3 的读数是
「需 ≥15.8bps，当前 106.8bps」，**根本没拦人**，故解 G3 也不在本轮需求范围内。
G3 的解除只保留两条自带约束的通道：`override`（证据 + 配额 + 60s 地板）与
`exit_pending`（2 小时墙钟上界，§4.2.3）。

**降档必须留痕**：第 3 条的五个合取项任一不成立而落到第 5/6 条时，`downgradeReason` 取
`evidence_missing` / `ledger_short` / `quota_exhausted` / `min_hold_floor` 之一（自上而下取第一条），
并**如实写进 prompt 与轨迹**——「你声明了 final，但因为 X 本轮按 high 处理」是模型下一轮修正行为的
唯一依据。静默降档会让模型在下一轮重复完全相同的错误。

**【R:P0-1】只有 `ledger_short` 可以走裁决路径**：四种降档原因里，只有「跨轮账本还不够厚」是一个
复核官**看得见证据、有资格替代**的判断；另外三种各自的性质决定了它们不可被裁决替代——
`quota_exhausted` 是会话级资源约束（与本轮证据无关）、`evidence_missing` 是根本没有证据可看、
`min_hold_floor` 是墙钟事实而非判断题。故 §4.4.1 的挂起分支**只在 `downgradeReason == ledger_short`
时开启**。

`override_min_hold_s` 是 G1 的**不可解除内核**（默认 60s）：`override` 也不解这 60 秒。它挡的是
「开仓当轮立刻 final 平仓」的病态形态——虽然 `override_rounds=2` 已经隐含「至少跨两轮」，但 K 线
唤醒与信号翻转唤醒可以在数秒内连续触发两轮，故仍需一个墙钟地板。

#### 4.3.2 数据结构

```python
@dataclass(frozen=True)
class ExitPriorityRule:
    """会话级不可变契约；`None` ⇒ 本层不装配 ⇒ 判定恒返回 `normal` 且 wire 整键缺席。"""
    enabled: bool = True
    exit_confirm_rounds: int = 2          # 与 HorizonRule 同源，由装配点原样投影，不复算
    override_rounds: int = 2
    override_min_hold_s: float = 60.0
    # 【R:P0-1 / X-4b】`override` 与 `adjudicated` **共用**这一份配额（裁决不是配额外的第二条通道）。
    max_overrides_per_session: int = 6
    evidence_min_chars: int = 10


@dataclass(frozen=True)
class ExitPriorityInput:
    """一次判定所需的断面（聚合结构体，守 CLAUDE.md「形参 ≤5」）。"""
    action: str
    position_side: str
    gate_reverse: bool
    conviction: str | None                # 模型声明；None / 非法值 ⇒ 视为 normal（绝不据它降级）
    evidence: str | None
    intent_streak: int
    intent_lapses: int
    hold_seconds: float
    overrides_used: int
    # 【R:P1-2】只由 §4.4.2 第二段用 `replace(inp, adjudicated=True)` 回填后**重跑同一个纯函数**；
    # W-2 那次调用恒传 False（复核官此刻还没跑）。它命中的是判定表第 4 条这个**独立档位**，
    # 不是 `override` 的析取支——写成析取支会让它在唯一的判定落点上恒为 False。
    adjudicated: bool = False


@dataclass(frozen=True)
class ExitPriority:
    level: str
    released_gates: tuple[str, ...]
    declared: str                         # 模型声明的原值（归一后）
    downgrade_reason: str | None
    # 【R:P0-1】本轮是否允许挂起待裁决：**恒等于** `downgrade_reason == "ledger_short"`
    # 且 `overrides_used < max_overrides_per_session`。判定收在纯核心里，会话侧只读它、不重算。
    may_pend: bool
    reason: str | None                    # 稳定码，如 exit_priority_override(final,2/2)
    wire: dict
```

`resolve_exit_priority(inp, rule) -> ExitPriority` 是本模块唯一出口（≤50 行，圈复杂度 ≤10；
判定表用**元组序列 + 早返回**表达，不写 if 阶梯）。

#### 4.3.3 与三闸的接线（`hold_horizon.evaluate_horizon_gate`，`hold_horizon.py:728`）

签名**加法式**扩展（keyword-only、默认 `None` ⇒ 既有全部调用点与断言零改动）：

```python
def evaluate_horizon_gate(decision, snapshot, rule, *, position_side,
                          priority_gates: tuple[str, ...] = (),
                          may_pend: bool = False):
    ...
    kind = gated_kind(decision.action, position_side, gate_reverse=rule.gate_reverse)
    if kind is None:
        return decision, None, False
    # 【R:P0-2】优先级解闸**只对平仓生效**：反手携带开仓腿，给它解闸即违反 X-1。
    released = set(snapshot.get("releasedGates") or ())
    if kind == "close":
        released |= set(priority_gates)
    reason = _first_gate_reason(rule, snapshot, released)
    if reason is None:
        return decision, None, False
    # 【R:P1-3】挂起：**不 veto、不写 degrade**，原样返回同一个 decision 对象继续下行，
    # 由第三元告诉调用方「本轮是挂起，不是放行」。
    if may_pend and kind == "close":
        return decision, reason, True
    return _veto(decision, reason), reason, False
```

**返回值由二元组扩为三元组 `(decision, reason, pending)`**（【R:P1-3】）：v1 让「挂起」发生在
`session._apply_horizon_gate` **之后**，但该函数返回时 decision 已被 `_veto`（`hold_horizon.py:786`）
换成一个**新建的 hold 对象**，调用点根本拿不回原来的 close。挂起必须在本函数内部表达。
`HorizonRuntime.gate`（`horizon_runtime.py:229-246`）同步扩为四元组
`(decision, degrade, snapshot, pending)`，且**挂起轮不走 `_note_veto`、不写 `degrade`、
不置 `last_reason`**——否则 `session.py:1370` 的 `_discipline.note_round(degrade)` 会把一次成功离场
记成一次被拦，`_emit_degraded` 还会推一条降级事件（§5.5 自己点名禁止的「放行码进 degrade」）。
「差什么」只写进 `exitPriority.reason` 与 trace。既有全部调用点（`horizon_runtime.py:239`、
`backtest.py:807`、`tests/test_ai_trade_hold_horizon.py` 的 13 处、
`tests/test_ai_trade_horizon_backtest.py:141`）需**逐处解包三元组**——这是本轮唯一一处**非加法式**
签名变更，故必须整仓 grep 一遍（`evaluate_horizon_gate(`），G-11 用 AST 断言零遗漏。

除并集与挂起位之外**不引入第二套解闸概念**，`_first_gate_reason` 一行不改。这是本轮最重要的一处**接线节俭**：`releasedGates` 已经是
payload / decision / status / trace 四条载体上的既有键，前端 `store.ts` 整份透传 `horizonWire`
（`store.ts:318-327`），故**零 reducer 改动**。

快照的 `releasedGates` 与 `released` **不改写**（它们的语义是「豁免矩阵命中」），优先级解的闸
单独落在 `exitPriority.releasedGates`——两者在轨迹上并排展示，用户能分清「是行情替你解的锁」
还是「是你的确信度替你解的锁」。

### 4.4 轴 B2 · 离场直通与反思极性反转（修 F-3 / F-5）

#### 4.4.1 三条分支（`session._apply_horizon_gate` 之后）

| 本轮 level | 门禁结果 | 处置 |
|---|---|---|
| `override` | 三闸已解 ⇒ 恒放行 | 直接执行；**跳过反思**（X-5）；`overrides_used += 1`；trace 记 `exitPriority.reason` |
| `expedite` | G2 已解；若仍被 **G1 或 G3** 拦 | 降级 `hold`（G1 是最硬的一条、G3 是成本闸，确信度不解它们，【R:P1-4】）；如实报原门禁码 |
| 被拦，且 `priority.may_pend` 为真（**即 `downgradeReason == ledger_short` 且配额未尽**） | — | **挂起为 `pendingExit`**：`decision` 保持 `close` 继续沿管线下行，由复核官裁决（下节） |
| 其余被拦 | — | 与今天完全一致：降级 `hold` |

**【R:P0-1】挂起分支被收紧为「只有 `ledger_short` 可挂起」**。v1 让**任何**因合取项不成立而降档的
`final` 都挂起，而 §4.4.2 的 `confirm` 分支会直接放行——于是：

| 降档原因 | v1 的实际结果 | 被架空的护栏 |
|---|---|---|
| `quota_exhausted` | 挂起 → confirm → 放行，`overrides_used` 越过上限 | R-1 缓解① 会话级硬配额 |
| `evidence_missing` | 挂起 → confirm → 放行 | R-1 缓解② `final` 必须带证据 |
| `min_hold_floor` | 挂起 → confirm → 放行，**§4.3.1 的 60s 绝对地板被解除** | R-1 缓解④ 绝对地板 |

X-4 白纸黑字写着「删掉其中任意一个合取项都会把这条通道变成模型的自助放行按钮」——v1 一次删了三个。
更直接的自相矛盾：G-10 断言「连续 8 轮 `final` ⇒ 后 2 轮降档为 `high`」，但后 2 轮恰好命中
`quota_exhausted` ⇒ 按 v1 挂起 ⇒ 复核官一句 `confirm` 就照样成交，**G-10 在 v1 的设计下必然是假绿**
（它只断言了 `downgradeReason` 与计数，没断言「没有成交」）。故本轮：`may_pend` 收在纯核心里
（§4.3.2），只对 `ledger_short` 且配额未尽时为真；G-10 追加断言「后 2 轮 `executed.traded is False`」。

**为什么挂起是安全的**：§2.8 已实测，从持仓周期层到反思层之间的六道闸**没有一道拦 `close`**。
故「保持 close 继续下行」不会在中途被别的层意外执行掉——它一定会走到反思，由反思做最终裁决。
**【R:P0-2】这条论证只对 `close` 成立**：反手的动作字面量是 `open_long`/`open_short`，它落在
`base_rules._GATED_ACTIONS`（`base_rules.py:44`）/ `edge_gate._GATED_ACTIONS`（`edge_gate.py:39`）/
`drawdown_brake._GATED_ACTIONS`（`drawdown_brake.py:37`）/ `sizing._GATED_ACTIONS`（`sizing.py:45`）/
`signals.py:252` **五道闸**的作用域内，会被静默降级成 hold，裁决通路根本走不到。这也是 X-1b 把反手
先降级为 `close` 的第二个理由。
G-4 用一条端到端用例冻结这条通路（任一层将来若开始拦 close，该用例即红）。

#### 4.4.2 反思侧的两处改动（`reflection.py` / `session.py:1466`）

```python
# reflection.py —— 新增 keyword-only 形参，默认空元组 ⇒ 既有调用逐字回退
def apply_reflection(decision, degrade, verdict, position_step=0.0, *,
                     fail_open_actions: tuple[str, ...] = ()) -> tuple[TradeDecision, str | None]:
    ...
    code = verdict.degrade_reason or ("reflection_abort" if verdict.verdict == "abort" else None)
    if code is not None:
        # X-6：fail-closed（基础设施故障）对 `close` 反转为 fail-open——一次 LLM 超时不该锁死持仓。
        # **真实 abort 不在此列**：`verdict.degrade_reason is None` 时 code 恒为 reflection_abort。
        if (verdict.degrade_reason is not None and decision.action in fail_open_actions):
            # 【R:P1-3】放行**不写 degrade**（§5.5：凡本轮真的成交了，degrade 恒为 None）。
            # 「这一笔没被复核过」这条事实由第三元回给调用方，落 exitPriority.failOpenReason
            # + trace 反思段 + aiStats.horizon.exitFailOpenRounds 三处。
            return decision, degrade, code
        return _hold(verdict.reason or code), (degrade or code), None
    ...
    return decision, degrade, None      # 其余分支第三元恒 None
```

⚠ 返回值随之由二元组扩为**三元组** `(decision, degrade, fail_open_code)`。两个既有调用点
（`session.py:1489`、`backtest.py:848-849`）需解包三元组——`fail_open_actions` 为空元组时第三元
**恒为 `None`**，故 `ai_trade_exit_reflection_fail_open=False` 的回退路径逐字节等价（C-8 一键回退
仍然成立）。

会话侧传 `fail_open_actions=("close",) if settings.ai_trade_exit_reflection_fail_open else ()`。
`pendingExit` 轮的裁决映射：

| 复核官裁决 | 处置 | degrade | 放行码落点 |
|---|---|---|---|
| `confirm` | **放行 close**：`replace(inp, adjudicated=True)` 重跑 `resolve_exit_priority` ⇒ `level="adjudicated"`；`overrides_used += 1`（自增前再判配额，X-4b） | `None` | `exitPriority.reason = exit_adjudicated(...)` + trace |
| `abort`（真实终态裁决） | 降级 `hold` | **取原持仓周期门禁码**（更具体、更可解释），复核官理由入注记 | — |
| fail-closed（timeout / llm_error / no_tool / invalid） | **放行 close**（X-6） | **`None`**（【R:P1-3】） | `exitPriority.failOpenReason = "reflection_timeout"` + trace 反思段 |

⚠ **注意 `abort` 分支的 degrade 取原门禁码而非 `reflection_abort`**：用户此刻最需要知道的是
「差几轮确认」，而不是「复核官不同意」——后者进注记与轨迹的反思段，两者都不丢。

**【R:P1-3】fail-open 轮的标注不进 `degrade`**：v1 §5.5 一边立下「放行码绝不进 `degrade`——
`degrade` 的语义是『本轮被降级』，把放行塞进去会让闸计数把一次成功离场记成一次被拦」，一边又把
`exit_reflection_fail_open(...)` 定义成落进 `degrade` 的「降级码」。两者不能同时成立。本轮统一：
**凡本轮真的成交了，`degrade` 恒为 `None`**；「这一笔离场没有被复核官复核过」这条事实挂在
`exitPriority.failOpenReason`（wire 加法键）与 trace 的反思段上，并单独进
`aiStats.horizon.exitFailOpenRounds`（§5.4）——R-3 缓解②要的「有多少笔离场是没被复核的可查」由它兑现，
且不再污染闸计数。前端 `DEGRADE_REASON_LABELS` 因此**不需要**加这一条。

**【R:P1-2】`level` 的最终形态由这一节决定**：`confirm` 分支必须**重跑同一个纯函数**（而不是就地把
`level` 字符串改掉），这样 `released_gates` / `reason` / `wire` 三者恒自洽；wire、
`aiStats.horizon.*`、退出归因打标一律取这一份重跑结果。

#### 4.4.3 F-2 的正修：门禁否决轮也要留注记

`session._run_decision_round` 留存**进入 `_apply_horizon_gate` 那一刻**的 decision 引用
（【R:P2-7】：v1 写的是「门禁链之前的 `stage1_decision`」。今天基础策略层不拦 `close`，两者恰好
相同；但那是一条**隐式依赖**——将来任一层开始改写 close 的 `reason`，注记就会静默失真。取 horizon
入口那一刻的引用才是「被本层拦下的到底是什么」的准确答案，且它与 G-4 的通路用例守的是同一件事），
并在 `commit_round(...)` 处传入新的 keyword-only 参数 `gate_veto=build_gate_veto_note(...)`：

```python
{"vetoedBy": "horizon", "reason": "horizon_exit_unconfirmed(1/2)",
 "proposedAction": "close", "proposedReason": "...(≤200 字截断)",
 "exitConviction": "final", "intentStreak": 1, "intentLapses": 0}
```

`decider.commit_round` 的处理与既有 `reflection` 注记**逐字同款**（`decider.py:551-556`）：
恒入 journal（未命中轮为 `None`，形状稳定）；**仅被否决轮**合并进 `_context` 的 `tool_result`。
两者**互斥不冲突**：门禁否决轮的 decision 此刻已是 `hold`，`_reflect_before_execute`
（`session.py:1479-1481`）对 `hold` 直通并返回 `reflection_note=None`；反之反思否决轮说明门禁没拦。
合并式仍写成 `{**executed, **({"reflection": r} if r else {}), **({"gateVeto": g} if g else {})}`，
不假设互斥（形状稳定优先于省字符）。
这样模型下一轮读自己的历史时，看到的是「上轮我提议 close，被交易周期层以 1/2 拦下」而不是
「上轮我决定观望」——F-2 的因果链在模型侧断开。

### 4.5 轴 C · 离场意图兑现（新纯核心 `exit_commitment.py`，约 160 行 · 修 F-4）

#### 4.5.1 状态机

```python
@dataclass(frozen=True)
class ExitCommitmentRule:
    enabled: bool = True
    retry_interval_s: float = 10.0
    max_attempts: int = 6
    max_age_s: float = 300.0


@dataclass(frozen=True)
class ExitCommitment:
    epoch: tuple[str, int] | None = None   # (side, seq)，与持仓周期层 / 利润保护层完全同源
    armed_wall: float = 0.0
    reason_code: str = ""                  # 触发本次离场的降级/放行码，供成交 explain 与归因
    priority_level: str = "normal"
    attempts: int = 0
    last_attempt_wall: float = 0.0
    last_error: str | None = None
    stuck: bool = False
```

纯函数三个（无 IO）：

- `arm(state, *, epoch, now_wall, reason_code, priority_level) -> ExitCommitment`
- `should_retry(state, rule, *, position_side, epoch, now_wall, resting_close: bool) -> bool`
  —— `flat` / epoch 变化 / 未武装 / `stuck` / 未到重试间隔 ⇒ `False`；
  **`resting_close=True` ⇒ 恒 `False`**（【R:P0-3】：有平仓挂单在飞就是「正在兑现」，
  本层绝不去撤掉模型自己挂的限价平仓单；TTL 到期由既有 `_poll_resting` 收敛）。
- `note_attempt(state, rule, *, now_wall, filled, error) -> ExitCommitment`
  —— `filled` ⇒ 返回空态（完成）；否则 `attempts+1`，并按 `max_attempts` / `max_age_s` 置 `stuck`。

#### 4.5.2 会话侧接线

- **武装（W-5）**：`_execute_decision` 返回后，同时满足三条才 `arm(...)`：
  ① `decision.action == "close"`；② **`decision.ord_type == "market"`**（【R:P0-3】）；
  ③ `executed.get("closedFilled") is False`（【R:P0-4】）。

  **【R:P0-3】②不可省**：限价平仓走 `executor._do_close` 的 `limit_px is not None` 分支
  （`executor.py:349-354`）⇒ `_enter_resting`，**当轮必然没有成交事件**、`projected` 保持原方向。
  照 v1 的判据它必然武装，`retry_interval_s=10` 到点后 `executor.apply([Signal(side="close")])`
  **不带 `order_plan`** ⇒ 市价 ⇒ `_prepare_resting_slot(..., limit_px=None)` 与原挂单
  `is_same_intent` 不成立 ⇒ **撤掉模型自己挂的限价平仓单并按市价平掉**。整个 limit-orders 特性
  （已合入 `3bbc503`）在离场侧被静默废掉，界面上还显示着那张限价单。`should_retry` 的
  `resting_close` 短路（§4.5.1）是同一条防线的第二层，两层都要。

  **【R:P0-4】③是对 v1 判据的正修**：v1 写「`executed` 中不存在 `type=="trade" and
  data.side=="close"` 的事件」，并称与 `_report_profit_exit`（`session.py:1932-1938`）逐字同源——
  但两者的载体根本不同：后者收的是 `events`（`list[dict]`），而 **`_execute_decision` 返回的是
  dict**（`{"ok","action","skipped","traded","side","qty","positionAfter"}`），事件列表是函数内的
  局部量、已在内部 `_dispatch` 掉。照 v1 写会得到一个恒成立的判据（dict 上迭代不出事件），
  **每一轮 close 都武装**。修法：给 `_execute_decision` 的回执加一个**加法键**
  `closedFilled: bool`（在函数内部由既有 `events` 算，与 `traded` 并列，`skipped` 分支恒 `False`），
  武装判据读它。原纪律不变——「以 close 成交事件为唯一成交判据」——只是换了载体。
- **兑现（W-6）**：`_monitor_tick` 在 `executor.reconcile` **之后**（挂单状态在那里收敛）、
  `_maybe_profit_exit` **之前**插入 `await self._settle_exit_commitment()`。它的四步收尾与
  `_maybe_profit_exit`（`session.py:1893-1899`）**逐步同构**（【R:P1-10】：v1 只写了前两步，
  漏掉后两步会让兑现成功的那笔交易在所有账本上都不存在）：

  1. 经 `_exec_lock`：`executor.apply([Signal(side="close", reason=...)])` + `executor.reconcile`；
  2. `await self._dispatch(events)` —— **这一步才是落库（`_persist_trade`）、打 `exitSource`
     （`_inject_explain_meta`）、喂经验层（`_experience.note_close`）、推 WS 的地方**；
  3. `await self._push_status_if_side_changed()`；
  4. 报告：以 close 成交事件为唯一完成判据（与 `_report_profit_exit` 同构）——成交即清空状态、
     未成交即 `note_attempt`、到顶即 `stuck` 并推 `AI_TRADE_EXIT_STUCK`。
- **顺序理由**：排在利润保护之前，因为 Agent 已经确认离场的仓位不该再等一次保护层判定；两者都
  只产生 `close`，先后不产生对冲态（若 Agent 这一次兑现成功，`_maybe_profit_exit` 会因
  `projected == "flat"` 自然空转）。
- **暂停期语义**：与利润保护层同款（`session.py:1887` 的 `is_active` 判据）——**暂停期照旧兑现**。
  用户暂停后去睡觉，一笔已经被 Agent 判定该走的仓位不该失去唯一的本地退出执行者。

#### 4.5.3 执行侧的静默路径正修（`executor.py`）

`_prepare_resting_slot` 返回值由二元组扩为三元组 `(events, slot_free, outcome)`；`_do_close`
的市价分支据 `outcome` 分流：

```python
        pre, slot_free, outcome = await self._prepare_resting_slot(ts, "close", self.projected, limit_px)
        if not slot_free:
            # F-4：**市价平仓**在「撤单未获确认」（unknown）时继续下发——close-position 带
            # autoCxl=True，由交易所侧撤掉该合约全部未成交挂单（okx/trade_client.py:204-213），
            # 且重复平仓走 LIVE_NO_POSITION 良性分支。`filled` 仍必须止步：那是真的已经平掉了。
            # 限价平仓与**全部开仓路径**逐字不变（双开风险不可接受）。
            if limit_px is not None or outcome != "unknown":
                return pre + [self._status_event(_SLOT_BUSY_NOTE)]
            pre = pre + [self._status_event(
                "原挂单撤销未获确认，但本轮是市价离场：仍按市价平仓（交易所侧会同时撤掉该合约未成交挂单）")]
```

`_do_open` 的调用点只需解包三元组，逻辑一行不改（G-6 冻结）。`_prepare_resting_slot` 的另外两条
提前返回（`order is None` ⇒ `True`；同向同价幂等 ⇒ `False`）第三元一律回 `"skipped"`，
故上面的 `outcome != "unknown"` 判据对它们天然止步。

**【R:P1-1】放行分支还必须堵住两个后果**，否则会制造一条比 F-4 更难查的错归因：

`_settle_benign_close`（`executor.py:579-592`）**无条件**按 `source="oco"` 合成 `external_close`；
而 `reconcile` 里的同类分支是有 `_external_close_suppressed()`（`executor.py:548-556`，判据正是
「有平仓挂单在飞」）保护的——**`_do_close` 这一条没有**。放行后的时序是：撤单回 `unknown`
（挂单**保留**）→ 市价 `close_position` → 若那张限价单其实已经成交，OKX 回 `LIVE_NO_POSITION`
→ 走 `_settle_benign_close` → **把我方的限价平仓记成交易所 OCO 平仓**；随后 `_settle_close`
（`executor.py:558-577`）**不清 `self.resting`**，下一轮 `_poll_resting` 读到 `filled` →
`_adopt_close_fill` → 再 `_settle_close` 一次，落一条 `qty=0` 的幽灵成交。

故本轮同时做两件事：

```python
        except OKXError as exc:
            if exc.code == "LIVE_NO_POSITION":
                # F-4 放行分支：挂单还在飞 => 这笔很可能就是我方限价平仓成交的，
                # 绝不能记成 exchangeOco（复用 reconcile 侧已有的同一条判据）。
                return pre + self._settle_benign_close(
                    ref_px, ts, suppress_external=self._external_close_suppressed())
            return pre + [self._error_event(ts, exc)]
```

以及：市价平仓成功后（`_settle_close` 返回前）显式 `self.resting = None`——交易所侧的
`autoCxl=True` 已经撤掉了它，本地留着只会让下一轮 `_poll_resting` 去查一张不存在的单。
`suppress_external=True` 时 `_settle_benign_close` 跳过 `_synthesize_external_close`、其余复位逻辑
逐字不变（归因落回 `agent`）。#32 必须逐条覆盖这条时序。

### 4.6 轴 D · 可解释性与人机交互（修 F-6）

#### 4.6.1 轨迹：决策工具卡片保真被拦意图

`session._emit_tool_call_trace`（`session.py:1560-1569`）新增三个**加法键**（`None` ⇒ 前端逐字节
回退，老快照零影响）。**【R:P2-2】`trace.py` 里没有 `tool_call_event` 这个函数**：`tool_call` 相位
是由 `_emit_tool_call_trace` 用通用的 `trace_event("tool_call", round_no, ts, name=..., input=...)`
直接装配的，v1 §4.6.1 与 §7.1 #11 的指向是错的。三键作为 `trace_event` 的**兄弟键**下发（不塞进
`input`——`input` 的语义是「模型提交的工具入参」，掺进非入参的键会让轨迹卡片本身开始撒谎），
故前端 `store.ts` 的 `case "tool_call"` 需逐键手抄（§7.2 #24，R-9 点名）：

```python
{"proposedAction": "close", "proposedReason": "...", "vetoedBy": "horizon"}
```

前端 `AiTradeTraceRound.tsx` 的决策工具卡片渲染改为：

```
决策工具 · 平仓 → 观望          [被交易周期层降级]
    离场确认 1/2（窗口剩余 1 小时）· 距放行还差 1 轮
```

而不是今天的 `决策工具 · 观望`。**这是本轮对用户那句困惑最直接的回答。**

#### 4.6.2 轨迹：交易周期卡片新增「离场优先级」行

在既有三行读数（`HorizonGateRows`）之后追加一行，`exitPriority` 键缺席 ⇒ 整行省略：

```
离场优先级 P2 · 加速（已解 G2）｜ 账本 2 轮（其间 1 轮未提离场，已按衰减计入）
```

`override` 轮用 accent 徽标（与本层 fail-open 极性一致，**不用红色**——放行不是拦截）：

```
[离场优先通道 · 已放行]  依据：final + 证据 + 账本 2/2 ｜ 本会话已用 1/6 次
```

降档轮必须写清原因：`你声明了 final，但本会话配额已用尽（6/6），本轮按 high 处理`。

#### 4.6.3 面板：持仓卡的离场状态与一键立即离场

`AiTradePanel.tsx` 的持仓卡新增三态（`exitCommitment` 键缺席 ⇒ 整块不渲染）：

| 状态 | 呈现 | 色 |
|---|---|---|
| `armed` 且 `!stuck` | `离场已确认，正在兑现（第 2/6 次尝试 · 已 24s）` + 细进度条 | accent |
| `stuck` | `⚠ 离场受阻：已尝试 6 次仍未成交（交易所侧止损仍生效）` + 【立即离场】按钮 | danger |
| 未武装 | 不渲染 | — |

**【立即离场】**是本轮的人机交互兜底：无论模型怎么想、无论哪一层怎么判，用户永远有一个不经模型的
离场按钮。二次确认弹窗必须写清三件事：①「这会按市价平掉本会话的自管仓位」；
②「**会话继续运行**，不同于【停止】」；③「交易所侧的止损止盈会被一并撤销（平仓即撤单）」。

#### 4.6.4 prompt：宪章新增「离场优先通道」段

`hold_horizon_text.format_charter` 追加（未装配 ⇒ 空串垫片 ⇒ 三份 prompt 逐字节回退）：

```
**离场优先通道**：如果你判断持仓理由已经失效、需要立即离场，可在 `submit_trade_decision` 里带上
`exitConviction`：
  · `high` —— 离场理由已成熟：系统将跳过「离场连续确认」这一条约束。
    （【R:P1-4】**不含换手迟滞**：那一条是「浮盈还不够付一次往返成本」，跳过它就是确定性地做一笔
    净亏的往返，不能由一句自述解开。）
  · `final` —— 你已在多轮研判中确认持仓理由失效：系统将跳过全部三条约束立即执行。
    使用 `final` 必须同时在 `exitEvidence` 里指名「持仓理由失效」的**新证据**（不是重复上一轮的
    判断）。本会话 `final` 配额 6 次，已用 N 次；配额用尽后 `final` 按 `high` 处理，
    **且不再进入复核裁决**（配额是硬的，不是一句「请复核官通融」能绕过的）。
  · 你的每一次 `final` 都会被记录，并在会话结束时与真实结果对照。**它是给真正的转折点准备的，
    不是给每一轮的犹豫准备的。**
```

`_confirm_line` 同步补上衰减说明（数全部取自快照，不重算）：

```
  离场连续确认：2/2（其间 1 轮未提离场，已按衰减计入）；确认窗口剩余 34 分钟（到时即放行）。
```

以及两条新状态行：`_exit_priority_line`（本轮解了哪几道闸 / 为何降档）与
`_exit_commitment_line`（离场正在兑现 / 已受阻——**受阻这一行至关重要**：它是模型唯一能知道
「我已经下过平仓指令但它没成交」的途径，缺了它模型会在下一轮重新研判一遍已经作过的决定）。

---

## 五、数据模型

### 5.1 配置项（`services/quant/app/config.py`，13 项）

```python
    # ---- 离场优先级与离场意图兑现（ai-trade-exit-priority-and-commitment §5.1）----
    # 与持仓周期三闸并列的第四条纪律，极性与它相同（fail-open）但方向相反：三闸约束「什么时候
    # 不许平」，本层约束「什么时候必须让它平」。总闸 False ⇒ 恒不装配、决策工具 schema 恒等于
    # DECISION_TOOL_SCHEMA、prompt 三份逐字节回退、note_intent 回退到「一轮即清零」。
    # env 名 AI_TRADE_EXIT_* 已在 desktop config.js prod 透传白名单内（AI_TRADE_ 前缀）。
    ai_trade_exit_priority_enabled: bool = True                                    # C-1
    ai_trade_exit_priority_backtest_enabled: bool = True                           # C-1b
    # 0 ⇒ 逐字回退今天的「一轮非离场即清零」（F-1 的回滚开关）。
    # 【R:P2-6】**默认值分层是刻意的**：settings 默认 2、`HorizonRule.exit_intent_decay_rounds`
    # 默认 **0**。既有用例 `tests/test_ai_trade_horizon_runtime.py:122` 直接构造 `HorizonRule(...)`
    # 且断言今天的「一轮 hold 即清零」，靠的正是后者。**不要顺手把 HorizonRule 的默认也改成 2**。
    ai_trade_exit_intent_decay_rounds: int = Field(default=2, ge=0, le=10)         # C-2
    # exit_pending 豁免 = mult × confirm_window_s；0 ⇒ 关闭本豁免。
    ai_trade_exit_pending_release_mult: float = Field(default=2.0, ge=0, le=10)    # C-3
    ai_trade_exit_override_rounds: int = Field(default=2, ge=1, le=10)             # C-4
    # G1 的不可解除内核：override 也不解这一段（挡「开仓当轮立刻 final 平仓」）。
    ai_trade_exit_override_min_hold_s: float = Field(default=60.0, ge=0, le=3600)  # C-5
    # 会话级配额；0 ⇒ 关闭 override 档（final 一律按 high 处理，如实写进 prompt）。
    # 【R:P0-1 / X-4b】`override` 与 `adjudicated` **共用**这一份配额——裁决不是配额外的第二条通道，
    # 否则「配额用尽 ⇒ 走裁决 ⇒ 配额继续涨」会让本项变成一个只在文档里存在的数字。
    ai_trade_exit_override_max_per_session: int = Field(default=6, ge=0, le=100)   # C-6
    ai_trade_exit_evidence_min_chars: int = Field(default=10, ge=0, le=200)        # C-7
    # X-6：close 的反思 fail-closed 反转为 fail-open。False ⇒ 逐字回退今天（F-3 的回滚开关）。
    ai_trade_exit_reflection_fail_open: bool = True                                # C-8
    ai_trade_exit_commitment_enabled: bool = True                                  # C-9
    ai_trade_exit_commitment_retry_interval_s: float = Field(default=10.0, ge=1, le=300)   # C-10
    ai_trade_exit_commitment_max_attempts: int = Field(default=6, ge=1, le=50)     # C-11
    ai_trade_exit_commitment_max_age_s: float = Field(default=300.0, ge=10, le=3600)       # C-12
    ai_trade_manual_exit_enabled: bool = True                                      # C-13
```

**C-2 / C-8 是两条一键回滚**：把它们分别设为 `0` / `False`，F-1 与 F-3 的修法逐字回退，其余三条轴
仍然生效——这是本轮四条轴「各自独立可回滚」的落地方式。

### 5.2 `HorizonRule` 的投影字段

`resolve_horizon_rule` 是唯一回填点（沿用 `_resolve_turnover` 的既有姿态），在 `HorizonRule`
（`hold_horizon.py:142`）新增三个字段：

```python
    exit_intent_decay_rounds: int = 0     # 0 = 逐字等价于今天（custom 全默认亦然）
    exit_pending_max_s: float = 0.0       # 0 = exit_pending 豁免关闭
    exit_priority: "ExitPriorityRule | None" = None   # None = 本层不装配
```

`rule_wire` 同步镜像三个**派生后实际值**（沿用 `horizon:P1-9`「只回显入参等于把派生关系藏起来」）：
`exitIntentDecayRounds` / `exitPendingMaxSeconds` / `exitPriority`（后者为
`{"enabled","overrideRounds","overrideMinHoldSeconds","maxOverridesPerSession","evidenceMinChars"}`）。

### 5.3 wire 快照加法键

挂在 `tradingHorizon` **快照**下（不是契约下）——理由与 `turnover` / `backstop` 两键完全同款
（`aiTradeTypes.ts:598-603`）：嵌套即自动搭上 payload / decision / status / trace 四条既有载体
（均为整对象透传），`store.ts` 的逐键手抄 reducer 一行都不必动。

```jsonc
"exitPriority": {                       // 未装配 ⇒ 整键缺席（不是 null）
  "level": "expedite",                  // none | normal | expedite | adjudicated | override
  "declared": "final",                  // 模型声明的原值（归一后；未声明 ⇒ "normal"）
  "downgradeReason": "quota_exhausted", // null | evidence_missing | ledger_short
                                        //      | quota_exhausted | min_hold_floor
  "releasedGates": ["G2"],               // 【R:P1-4】expedite 只解 G2；G3 只由 override /
                                        // exit_pending 解。与豁免矩阵的 releasedGates 并列，不合并
  "intentStreak": 2,
  "intentLapses": 1,
  "intentAgeSeconds": 1830.0,
  "overrideRounds": 2,
  "overridesUsed": 1,
  "overridesRemaining": 5,
  "adjudicated": false,                 // 本轮是否由复核官背书放行（level=="adjudicated" 的镜像位）
  "mayPend": false,                     // 【R:P0-1】本轮是否**允许**挂起（≡ ledger_short 且配额未尽）
  "pending": false,                     // 本轮是否已挂起待裁决
  "failOpenReason": null,               // 【R:P1-3】非 null ⇒ 本笔离场未经复核官复核（基础设施失败）
  "reason": "exit_priority_expedite(high)"
},
"exitCommitment": {                     // 未装配 / 未武装 ⇒ 整键缺席
  "armed": true, "attempts": 2, "maxAttempts": 6, "ageSeconds": 24.0,
  "maxAgeSeconds": 300.0, "priorityLevel": "override",
  "reasonCode": "exit_priority_override(final,2/2)",
  "lastError": null, "stuck": false
}
```

### 5.4 统计与归因

`horizon_runtime.stats()`（`horizon_runtime.py:444`）新增 6 键（**刻意不并进既有计数**——沿用
`horizon:P2-7`「那会让 AST 冻结门禁虽过、语义却已漂移」）：

```python
    "exitExpediteRounds": ..., "exitOverrideRounds": ..., "exitAdjudicatedRounds": ...,
    "exitFinalDeclared": ..., "exitFinalDowngraded": ..., "exitPendingReleases": ...,
    # 【R:P1-3】fail-open 放行不再进 `degrade`，故「有多少笔离场没被复核官复核过」必须有自己的计数，
    # 否则 R-3 缓解② 就没有落点。
    "exitFailOpenRounds": ...,
```

`exit_attribution.EXIT_SOURCES` 新增一值 `agentOverride`（放在 `agent` 之后、`agentReverse` 之前，
枚举顺序即同数时的稳定次序）：凡本次 close 的最终 `level in ("override", "adjudicated")` 即归此类。
**这一条是 X-9 的兑现点**：会话结束时的周期验收卡可以直接回答「用最高优先级走掉的那几笔，事后净额
是正是负」。

**【R:P0-6】打标必须是一个有生命周期的一次性槽，不能是一个会话字段。**
`session._inject_explain_meta`（`session.py:430-461`）跑在 **`_dispatch` 的每一条成交事件上**，
包括利润保护平仓、会话止盈损、外部/OCO 平仓，以及**限价平仓在后续盯市 tick 上由
`_poll_resting → _adopt_close_fill` 异步采纳的那一笔**。若把 `priority_level` 挂成会话字段而不随本次
close 消费清空，一次 override 之后的**下一笔任意平仓**都会被记成 `agentOverride`，且错误方向是
**系统性高估**（override 之后的仓位往往由保护层平掉）。`exit_attribution.classify_backtest` 的
R-6 注释（「槽里若恰好留着上一次的值，以 reason 为准才不会归错」）正是本仓库为**这同一形态**写下的
成文教训。故：

- 槽名 `session._pending_exit_priority: str | None`，在决策轮放行 override/adjudicated 时置位；
- **只对 `explain["kind"] == "atomic"` 且 `trade["side"] == "close"` 的成交生效**（保护层的
  `profit_guard`、OCO 的 `external_close`、会话停止的 `session_stop` 一律不认这个槽）；
- **消费即清空**（`meta.setdefault("exitSource", ...)` 那一行之后立刻置 `None`）；
- `epoch` 变化 / `projected` 归 flat / `pause()` 时无条件清空。

**【R:P0-5】枚举与豁免原因的镜像清单（六处，漏一处即静默失效）**：`EXIT_SOURCES` 与
`_RELEASE_LABEL` 都不是单点真源，实读还有六份手抄镜像，全部必须同步（落在 §7.1 / §7.2 的文件表里）：

| # | 镜像点 | 漏改后果 |
|---|---|---|
| 1 | `ai_auto/acceptance_text.py:25` `EXIT_SOURCE_LABELS` | 周期验收卡显示裸 `agentOverride` |
| 2 | `ai_auto/experience/taxonomy.py:110-113` `VALUE_LABEL_ZH` | 经验图 lesson 标题出现裸英文（`AXIS_VALUES[exit] = tuple(EXIT_SOURCES)` 会自动跟，**标签是手抄的**） |
| 3 | `apps/web/lib/aiTradeAcceptance.ts:35,54` `EXIT_SOURCE_LABELS` / `EXIT_SOURCE_COLORS` | 堆叠比例条出现一段无标签、无配色的灰块 |
| 4 | `apps/web/lib/experience.ts:67-73` `VALUE_LABEL_ZH` | 同 #2 的前端侧 |
| 5 | `apps/web/lib/experience.ts:93` `AXIS_VALUES.exit`（**硬编码九值数组**） | 经验图预演选择器里**根本选不到这一类**——X-9 在经验图那一侧无法被查询 |
| 6 | `apps/web/lib/aiTradeHorizon.ts:280` `HORIZON_RELEASE_LABEL` | `exit_pending` 豁免轮 UI 只显示「限制已解除」，说不出为什么 |

回测侧的打标槽 `backtest.py:893-896`（`self._pending_exit_source = "agent"`）同步（见 §7.1 #12），
否则回测对照臂永远统计不到 `agentOverride`。G-9 因此由「查前端有没有硬编码」扩为**双向覆盖断言**：
`EXIT_SOURCES` 的每一个值都必须能在前端两张标签表与一张 `AXIS_VALUES` 数组里查到。

### 5.5 新增码（前缀，稳定契约）

| 码 | 类别 | 落点 |
|---|---|---|
| `exit_order_slot_busy(...)` | **降级码**（本轮真的没平成） | `degrade` / 前端 `DEGRADE_REASON_LABELS` |
| `exit_priority_expedite(...)` / `exit_priority_override(...)` / `exit_adjudicated(...)` | **放行码** | `exitPriority.reason` + trace，**不进 `degrade`** |
| `reflection_timeout` 等四码在 close 上的 fail-open 放行（【R:P1-3】） | **放行标注** | `exitPriority.failOpenReason` + trace 反思段 + `aiStats.horizon.exitFailOpenRounds`；**不进 `degrade`**、前端 `DEGRADE_REASON_LABELS` **不加条目** |
| `exit_pending` | 豁免原因 | `releaseReason` + 后端 `_RELEASE_LABEL` + **前端 `aiTradeHorizon.ts:280` `HORIZON_RELEASE_LABEL`**（【R:P0-5】#6） |
| `agentOverride` | 退出来源枚举 | `EXIT_SOURCES` + **六处镜像**（§5.4 表） |
| `AI_TRADE_EXIT_STUCK` | WS error/status code | 面板红条 |
| `AI_TRADE_MANUAL_EXIT_FAILED` | WS error code（【R:P1-6】） | 面板红条；**不置** `flatten_failed` / `residual` |

**放行码绝不进 `degrade`**：`degrade` 的语义是「本轮被降级」，把放行塞进去会让
`_discipline.note_round(degrade)`（`session.py:1370`，轴 B/C 的闸计数）把一次成功离场记成一次被拦，
`_emit_degraded` 还会推一条降级事件、`horizon_gate_status` 显示 `veto`——这是本仓库
「有写点没读点 / 语义漂移」类缺陷的典型形态。

**【R:P1-3】v1 在这一段自相矛盾**：它一边立下上面这条纪律，一边把
`exit_reflection_fail_open(reflection_timeout)` 定义成「**降级码**（放行但如实标注）」并落进
`degrade`。两者不能同时成立。本轮统一为一条可执行的判据：**凡本轮真的成交了，`degrade` 恒为
`None`**；「这一笔离场没有被复核官复核过」这条事实挂在 `exitPriority.failOpenReason`、trace 反思段
与 `aiStats.horizon.exitFailOpenRounds` 三处，R-3 缓解② 由它兑现，且不再污染闸计数。
`pendingExit` 轮同理：挂起不写 `degrade`（§4.3.3）。

---

## 六、接口设计

### 6.1 决策工具 schema（`prompts.build_decision_tool_schema`）

新增 keyword-only 形参 `allow_exit_priority: bool = False`（**默认必须 False**：
`build_decision_tool_schema(0.0) is DECISION_TOOL_SCHEMA` 的恒等断言
`tests/test_ai_trade_position_step.py:132` 据此零改动通过——这与 `allow_limit` / `allow_hold_exit`
的既有硬约束同源）。

**【R:P1-9a】早退判据必须同步补一项**，v1 漏了这句而它是 G-2 红绿的直接原因——
`prompts.py:245` 的 `if step <= 0 and not allow_limit and not allow_hold_exit:` 要改成
`if step <= 0 and not allow_limit and not allow_hold_exit and not allow_exit_priority:`。

为真时在**调用方自己的 props 浅拷贝**上补两个可选字段（`required` 不变）：

```python
props["exitConviction"] = {
    "type": "string", "enum": ["normal", "high", "final"],
    "description": "（可选，**仅 action=close 时有意义**）本次离场的确信度。"
                   "normal=常规离场；high=离场理由已成熟，请求跳过离场连续确认；"
                   "final=你已在多轮研判中确认持仓理由失效，请求最高优先级立即离场"
                   "（必须同时给出 exitEvidence，本会话配额有限且会被记录核对）。"}
props["exitEvidence"] = {
    "type": "string", "maxLength": 300,
    "description": "（可选）exitConviction=final 时必填：指名「持仓理由已失效」的新证据"
                   "——不是重复上一轮的判断。"}
```

`TradeDecision`（`decider.py:110`）新增两个**末位带默认值**的字段（既有位置构造与四元组解包
零改动）：`exit_conviction: str | None = None`、`exit_evidence: str | None = None`。
`_map_decision`（`decider.py:374`）解析纪律**沿用 `orderType` 的 F-2 先例**：值域外 / 类型错 /
非受控动作 ⇒ **一律忽略、绝不据它降级**（一个读不懂的确信度不该把最安全的动作降级）。
两者随 journal 落痕（与 `orderType` / `holdExitPx` 同理，`decider.py:544-550`），否则滚动摘要重置后
问责回路断裂。

**【R:P1-9b】`_map_decision` 有两条构造返回，落点必须写死为「只解析 `action == "close"`」**：
该函数（`decider.py:404-419`）的 `open_long`/`open_short` 分支是 **6 位置参的提前 `return`**
（连既有的 `hold_exit_px` 都不带），只有 `close` / `hold` / `cancel_order` 走末尾那条 7 位置参。
按 X-1b，反手一律不进本层（先降级为 `close` 再判），故本轮**只改末尾那条**，并显式以
`action == "close"` 为条件解析（与 C1 给 `holdExitPx` 立的 `[P2-3]` 纪律逐字同构：该函数没有字面
`action == "hold"` 分支，不显式判条件就会让残留值泄漏到别的动作上）。**不要**去动 `open_*` 那条
提前 return——那会把一个开仓决策变成潜在的解闸入口，直接违反 X-1。

### 6.2 REST

```
POST /api/ai-trade/{group_id}/member/{inst_id}/exit
```

- **语义**：人工授权的**单次**市价离场；只平本会话自管仓位，**不停止会话**。
- **不守 AI 总闸**（与 `/ai-trade/{gid}/stop`、`/backstop/repair` 同理）：离场是收敛风险的动作，
  任何时候都该能执行。守的是运维总闸 `ai_trade_manual_exit_enabled`（关 ⇒ 409）。
- **实现**：`manager.exit_member(gid, inst)` → `session.exit_now()` → 经 `_exec_lock` 调
  `executor.flatten` + 复用 `_handle_flatten_result` 的**同一条收尾**（它负责扇出撤单 / 成交事件，
  并在平仓失败时推强提示；各写一遍会造出全仓唯一一条「平仓失败却静默」的路径）。
- **【R:P1-6】但绝不能原样复用它的失败置位。** `_handle_flatten_result`（`session.py:659-675`）
  失败时置 `self.flatten_failed = True` / `self.residual`，这两个字段被
  `_status_payload`（`session.py:2255`、`2307`）读成面板上的**终态红条**，且**全仓没有任何复位点**
  （只在 `__init__` 置 `False`）。而 `/exit` 是会话**继续运行**的中途动作——一次失败会让此后每一次
  status 都宣称「AI 自动交易停止时平仓失败，请立即到下方实盘面板手动平仓」，即便下一轮 Agent 自己
  已经把仓平掉了。故给该方法加 keyword-only 形参 `terminal: bool = True`（既有两个调用点
  `session.py:617` / `session.py:744` 零改动）：`/exit` 传 `False` ⇒ 仍扇出事件、仍推错误，但推的是
  独立的 `AI_TRADE_MANUAL_EXIT_FAILED`，**不置** `flatten_failed` / `residual`。
- **回执**（`AiTradeExitOut`）：`{"ok", "instId", "groupId", "flattened": {...}|null,
  "positionAfter", "restingCancelled", "message"}`。
  **【R:P2-3】只作文档 schema，端点返回裸 `dict`，不绑 `response_model`**：同类端点
  `/ai-trade/{gid}/member/{inst}/stop`（`api/ai_auto.py:402`）与 `.../backstop/repair`（`:411`）的
  返回标注都是裸 `dict`，正是为绕开「`response_model` 静默吞掉新 wire 键」这个本仓库出过事的形态。
- **错误**：分组 / 成员缺失 → 404（镜像 `stop_member`）；运维总闸关 → 409
  `AI_TRADE_MANUAL_EXIT_DISABLED`；OKX 业务失败 → 502 + 原始 sCode + **中文原因在前**
  （沿用 `/backstop/repair` 的既有翻译点纪律）。

`GET /api/ai-trade/horizon-profiles` 的回执新增只读块（前端一个字、一个数都不硬编，X-8）：

```jsonc
"exitPriority": {
  "enabled": true, "overrideRounds": 2, "overrideMinHoldSeconds": 60,
  "maxOverridesPerSession": 6, "evidenceMinChars": 10,
  "levels": [
    {"id": "high",  "label": "加速离场", "detail": "跳过离场连续确认这一条约束（换手迟滞仍生效）"},
    {"id": "final", "label": "最高优先级离场", "detail": "跳过全部三条约束立即执行；须给出新证据，本会话配额有限"}
  ],
  "note": "本通道只放行平仓，永不放行开仓；交易所侧止损止盈不受任何影响。"
}
```

### 6.3 WS 事件与轨迹相位

- **【R:P1-8】不新增 `exit_priority` 轨迹相位。** v1 既在 §5.3 论证了「`exitPriority` 嵌在
  `tradingHorizon` **快照**下 ⇒ payload / decision / status / trace 四条载体整对象透传 ⇒ `store.ts`
  零 reducer 改动」，又在本节新开一个相位，代价是 `trace.PHASES` + `AiTradeTraceEvent` 类型 +
  轮次初值 + `reduceRoundPhase` 新 case **四处逐键手抄**——R-9 自己把它列为「漏抄即静默功能缺失」的
  风险。它唯一的增量收益是「`adjudicated` 在 `horizon` 相位发出之后才产生」，而这一件事有更便宜的
  落点。故本轮：
  - 非反思轮的 `exitPriority` 随 `horizon` 相位的 `horizonWire` **整对象透传**
    （`store.ts:322-327` 的 `horizonWire: evt.horizon ?? null` 一行不改）；
  - 反思之后回填的最终档位（含 `adjudicated` / `failOpenReason`）挂在**既有** `reflection_verdict`
    相位上作为一个加法键——该 `case` 在 `store.ts:328` 已经是手抄区，**只多一行**。
  - R-9 随之只剩 `tool_call` 三键一处手抄。
- `trace` 的 `tool_call` 相位新增三键（§4.6.1），作为 `trace_event` 的**兄弟键**（不塞进 `input`）。
- `status` 载荷经既有 `tradingHorizon` 整对象透传自动带上 `exitPriority` / `exitCommitment`。
- 新 `error` 事件：`{"code": "AI_TRADE_EXIT_STUCK", "message": "离场已尝试 N 次仍未成交…"}`。

### 6.4 prompt 段（全部由后端下发，前端与文案层不重算任何一个数）

| 入口 | 位置 | 未装配 |
|---|---|---|
| `format_charter` 追加「离场优先通道」段 | 系统 prompt | 空串垫片 ⇒ 逐字节回退 |
| `format_horizon_for_prompt` 追加 `_exit_priority_line` / `_exit_commitment_line` | 每轮决策 prompt | 键缺席 ⇒ 整行省略 |
| `_confirm_line` 补衰减说明 | 每轮决策 prompt | `lapses==0` ⇒ 逐字节回退今天那句 |
| `format_reflection_note` 追加一句「本轮是被持仓周期层挂起的离场提议（**账本还差 N 轮确认**），你的裁决将直接决定它是否执行」 | 反思 system prompt | 非 pending 轮 ⇒ 逐字节回退 |

【R:P0-1】最后一行的括号内容不是装饰：pending 现在**只可能**由 `ledger_short` 触发，复核官必须知道
自己被请来替代的到底是哪一项（「账本还不够厚」），而不是被含糊地要求「你看着办」。配额、证据、
时间地板三种降档**根本不会**走到反思，所以反思 prompt 里也不该出现它们。

---

## 七、文件 / 模块变更计划

### 7.1 后端（`services/quant/app`）

| # | 文件 | 动作 | 一句话意图 |
|---|---|---|---|
| 0 | `ai_auto/exit_intent.py` | **新建**（~60 行 ·【R:P1-7】） | 离场意图账本纯核心：`contradicts_exit` / `advance_intent` / `derive_exit_pending_max_s` / `RELEASE_EXIT_PENDING` 常量。**不 import `hold_horizon`**（只收标量，由调用方 `replace` 回写），故不成环 |
| 1 | `ai_auto/exit_priority.py` | **新建**（~210 行） | 离场优先级纯核心：五档冻结判定表、证据校验、配额、`may_pend` 判定、降档留痕、wire 装配 |
| 2 | `ai_auto/exit_commitment.py` | **新建**（~170 行） | 离场意图兑现纯核心：状态机、重试判据（含 `resting_close` 短路）、stuck 判定、wire 装配 |
| 3 | `ai_auto/hold_horizon.py` | 修改（**≤20 行** ·【R:P1-7】：现 796 行，红线 800） | `HorizonState` +2 字段；`HorizonRule` +3 投影字段；`RELEASE_GATES` 接 `exit_pending`（**只解 G2/G3**）；`release_reason_for` 末位 4 行；`evaluate_horizon_gate` 加 `priority_gates` / `may_pend` 并**改回三元组**；快照 +3 键 |
| 4 | `ai_auto/hold_horizon_text.py` | 修改 | 宪章「离场优先通道」段；`_confirm_line` 衰减说明；`_exit_priority_line` / `_exit_commitment_line`；`_RELEASE_LABEL` 补 `exit_pending` |
| 5 | `ai_auto/horizon_runtime.py` | 修改 | `note_intent` 改调 `advance_intent`；`gate` 接 `ExitPriority` 并**改回四元组**（+`pending`）；**挂起轮不走 `_note_veto`、不写 `degrade`、不置 `last_reason`**（【R:P1-3】）；五类计数；`stats()` **+7 键**（含 `exitFailOpenRounds`） |
| 6 | `ai_auto/decider.py` | 修改 | `TradeDecision` +2 字段；`_map_decision` **只在末尾那条返回上、且只对 `action=="close"`** 解析（【R:P1-9b】：`open_*` 分支是另一条 6 位置参的提前 return，**不得改**）；工具装配透传 `allow_exit_priority`；`commit_round` 接 `gate_veto` 注记 |
| 7 | `ai_auto/prompts.py` | 修改 | 工具 schema / description 的 `exitConviction` / `exitEvidence` 加法分支；**早退判据补 `and not allow_exit_priority`**（【R:P1-9a】，G-2 的直接依赖） |
| 8 | `ai_auto/reflection.py` | 修改 | `apply_reflection` 加 `fail_open_actions` 并**改回三元组**（第三元 `fail_open_code`，【R:P1-3】：放行不写 `degrade`）；`build_reflection_note` 带 `pendingExit` 与「账本还差 N 轮」 |
| 9 | `ai_auto/session.py` | 修改（≤60 行） | `_resolve_exit_priority` / `_settle_exit_commitment` 两个薄方法 + 加法接线：`_execute_decision` 回执 +`closedFilled`（【R:P0-4】）、`_handle_flatten_result` +`terminal`（【R:P1-6】）、`_inject_explain_meta` 认一次性打标槽（【R:P0-6】）、W-4b 的档位重算（【R:P1-2】） |
| 10 | `ai_auto/manager.py` | 修改 | 装配 `ExitPriorityRule` / `ExitCommitmentRule`；`exit_member` 入口 |
| 11 | `ai_auto/trace.py` | 修改（**很小**） | **不加相位**（【R:P1-8】）；`reflection_verdict` builder +1 加法键承载最终 `exitPriority`；轮次初值同步 |
| 12 | `ai_auto/backtest.py` | 修改 | 同位接 `ExitPriority`（轴 A/B 生效，轴 C 不装配——SimBroker 每单必成，无兑现语义）。**【R:P2-5】三条口径写死**：① `apply_reflection`（`backtest.py:848`，4 位置参）**同样传** `fail_open_actions`（两侧默认不一致是本仓库 `base_rules` P1-4 / `profit_guard` I-3 的成文教训）；② `_execute` 的打标槽（`backtest.py:893-896`）在 override/adjudicated 轮写 `agentOverride`；③ 回测 `note_intent` 的 `now_wall=float(ts)` 是**根时间戳而非墙钟**，故 `exit_pending_max_s` 在回测里的语义是「根时间跨度」——必须在 §十一 记一条 `D-*`，且 `format_backtest_notes` 如实披露 |
| 13 | `ai_auto/exit_attribution.py` | 修改 | `EXIT_SOURCES` +`agentOverride`（`agent` 之后、`agentReverse` 之前） |
| 13b | `ai_auto/acceptance_text.py` | 修改（【R:P0-5】#1） | `EXIT_SOURCE_LABELS` +`agentOverride` |
| 13c | `ai_auto/experience/taxonomy.py` | 修改（【R:P0-5】#2） | `VALUE_LABEL_ZH` +`agentOverride`（`AXIS_VALUES[exit]` 自动跟随 `EXIT_SOURCES`，**标签是手抄的**） |
| 14 | `ai_auto/horizon_acceptance.py` | 修改 | 验收卡新增「最高优先级离场的事后净额」一行 |
| 15 | `api/ai_auto.py` | 修改 | `POST .../exit` 端点 + `horizon-profiles` 的 `exitPriority` 只读块 |
| 16 | `schemas.py` | 修改 | `AiTradeExitOut`；`AiTradeHorizonProfilesOut` +`exitPriority` |
| 17 | `config.py` | 修改 | C-1…C-13 共 13 项 |
| 18 | `live_auto/executor.py` | 修改 | `_prepare_resting_slot` 返回三元组；`_do_close` 市价 unknown 分支放行（开仓路径逐字不变）；**【R:P1-1】** `_settle_benign_close` 加 `suppress_external` 形参并由 `_do_close` 传 `self._external_close_suppressed()`；市价平仓成功后清 `self.resting` |

### 7.2 前端（`apps/web`）

| # | 文件 | 动作 | 一句话意图 |
|---|---|---|---|
| 19 | `lib/aiTradeExit.ts` | **新建**（~190 行） | 优先级五档 / 降档原因 / 兑现进度 / 受阻文案的中文化与徽标色；一键离场确认文案 |
| 20 | `lib/aiTradeExit.test.ts` | **新建** | 上者的纯逻辑用例（含「未装配 ⇒ 全部返回空」） |
| 21 | `lib/aiTradeTypes.ts` | 修改 | `AiTradeExitPriorityWire`（五档 + `mayPend` / `failOpenReason`）/ `AiTradeExitCommitmentWire`；快照 +2 可选嵌套键；`AiTradeRoundTrace` +4 键（`tool_call` 三键 + `reflection_verdict` 上的最终 `exitPriority`） |
| 22 | `lib/aiTrade.ts` | 修改 | `DEGRADE_REASON_LABELS` **只 +1 条**（`exit_order_slot_busy`）——`exit_reflection_fail_open` 已按【R:P1-3】移出 `degrade`，**不加条目** |
| 23 | `lib/aiTradeApi.ts` | 修改 | `postAiTradeMemberExit` |
| 24 | `lib/store.ts` | 修改（**只剩一处手抄**） | `tool_call` 相位补 3 键、轮次初值补 3 键（**逐键手抄区，漏抄即静默功能缺失**）；`reflection_verdict` 相位 +1 键。**不新增相位**（【R:P1-8】） |
| 24b | `lib/aiTradeHorizon.ts` | 修改（【R:P0-5】#6） | `HORIZON_RELEASE_LABEL` +`exit_pending`（漏改 ⇒ 豁免轮 UI 说不出为什么解除） |
| 24c | `lib/aiTradeAcceptance.ts` | 修改（【R:P0-5】#3） | `EXIT_SOURCE_LABELS` / `EXIT_SOURCE_COLORS` +`agentOverride` |
| 24d | `lib/experience.ts` | 修改（【R:P0-5】#4/#5） | `VALUE_LABEL_ZH` +`agentOverride`；**`AXIS_VALUES.exit` 硬编码九值数组 → 十值**（漏改 ⇒ 经验图预演选择器里根本选不到这一类） |
| 25 | `components/Agent/AiTradeExitPriorityRows.tsx` | **新建**（~120 行 ·【R:P1-7】） | 决策工具卡片的「提议 → 被拦」保真行 + 交易周期卡片的「离场优先级」行。**独立组件**：`AiTradeTraceRound.tsx` 现 841 行、已越 CLAUDE.md 800 红线，本轮**净增 0** |
| 25b | `components/Agent/AiTradeTraceRound.tsx` | 修改（**≤4 行**） | 只加 import 与两处挂载 |
| 26 | `components/Chart/AiTradeExitCard.tsx` | **新建**（~150 行 ·【R:P1-7】） | 持仓卡的离场三态 + 【立即离场】按钮 + 二次确认弹窗。**独立组件**：`AiTradePanel.tsx` 现 798 行、距红线 2 行，本轮**净增 0** |
| 26b | `components/Chart/AiTradePanel.tsx` | 修改（**≤2 行**） | 只加 import 与一处挂载 |
| 27 | `components/Agent/AiTradeHorizonFields.tsx` | 修改 | 弹窗新增「离场优先通道」只读说明块（文案全部来自 `horizon-profiles` 回执） |

### 7.3 测试

| # | 文件 | 覆盖 |
|---|---|---|
| 28 | `tests/test_ai_trade_exit_intent_ledger.py` | **F-1 的回归用例**：`close → hold → close` 后 `streak==2`；`first_exit_intent_wall` 不被重置；`decay_rounds=0` 逐字回退；同向加仓立即清零；`exit_pending` 豁免触发且**只解 G2/G3**（【R:P2-4】）。**【R:P2-6】同时断言既有用例零改动通过**：`tests/test_ai_trade_horizon_runtime.py:122 test_note_intent_resets_on_other_actions_but_holds_on_degrade` 断言的正是今天「一轮 hold 即清零」，它靠 `_rule()` 不传新字段、吃 `HorizonRule.exit_intent_decay_rounds` 的默认 `0` 才继续绿——这条**隐式依赖**必须变成显式断言（`HorizonRule().exit_intent_decay_rounds == 0`），否则实施者顺手把 `HorizonRule` 的默认也改成 2，该用例会红而根因难查 |
| 29 | `tests/test_ai_trade_exit_priority.py` | §4.3.1 **五档判定表逐格**；四种降档原因；**`may_pend` 只对 `ledger_short` 为真**（【R:P0-1】）；配额耗尽；证据不足；**反手（`gated_kind=="reverse"`）恒返回 `none`**（【R:P0-2】）；`level=none` 时 wire 整键缺席 |
| 30 | `tests/test_ai_trade_exit_commitment.py` | 武装 / 重试间隔 / epoch 变化即清空 / `max_attempts` 与 `max_age_s` 双到顶 / stuck 后不再重试；**`resting_close=True` ⇒ `should_retry` 恒 `False`**（【R:P0-3】） |
| 31 | `tests/test_ai_trade_exit_session.py` | 端到端：①被 G2 拦 → 下一轮 `final` → 放行；②`pending`（`ledger_short`）→ 复核官 `confirm` → 放行且 `level=="adjudicated"`（【R:P1-2】）；③复核官 `abort` → 降级且 degrade 取原门禁码；④`reflection_timeout` → **fail-open 放行且 `degrade is None`**（**F-3 的回归用例** +【R:P1-3】）；⑤**`quota_exhausted` / `evidence_missing` / `min_hold_floor` 三种降档一律不挂起**（【R:P0-1】）；⑥**限价 close 不武装轴 C**（【R:P0-3】）；⑦**override 之后紧接一次利润保护平仓 ⇒ 后者归 `profitGuard`**（【R:P0-6】打标槽生命周期） |
| 32 | `tests/test_ai_trade_exit_executor.py` | **F-4 的回归用例**：撤单 unknown + 市价平仓 ⇒ 仍下发；`filled` ⇒ 止步；限价平仓 ⇒ 止步；**开仓路径三态逐字不变**；**【R:P1-1】** unknown + 市价 + 随后 `LIVE_NO_POSITION` ⇒ 归 `agent` 而非 `exchangeOco`，且 `self.resting` 已清、下一轮 `_poll_resting` 不产生第二笔结算 |
| 33 | `tests/test_ai_trade_exit_api.py` | `/exit` 200 / 404 / 409 / 502；不守 AI 总闸；不改会话 state；**失败后 `status.flattenFailed is False`**（【R:P1-6】） |
| 34 | `tests/test_ai_trade_exit_freeze.py` | **X-7 冻结**：总闸关 ⇒ 三份 prompt 走「空垫片 vs 不传」的 sha256 相等（沿用 `tests/test_ai_trade_prompts.py:747` 的既有模式，【R:P2-1】）；`build_decision_tool_schema(0.0) is DECISION_TOOL_SCHEMA`；`tradingHorizon` 两新键整键缺席；**`EXIT_SOURCES` 的每一个值都能在前端两张标签表与 `AXIS_VALUES.exit` 里查到**（双向覆盖，【R:P0-5】） |
| 35 | `tests/test_ai_trade_exit_wiring.py` | **【R:P1-3】签名迁移零遗漏**：AST 扫描全仓 `evaluate_horizon_gate(`（17 处）与 `apply_reflection(`（`session.py:1489`、`backtest.py:848`、`tests/` 若干）的调用点，断言全部按三元组解包。这是本轮**仅有的两处**非加法式签名变更，G-11 |

---

## 八、测试与验收标准

### 8.1 放行条件（G-*，全部满足才算本轮完成）

- **G-1（冻结）**：`ai_trade_exit_priority_enabled=False` **且** `ai_trade_exit_commitment_enabled=False`
  时，系统 / 决策 / 反思三份 prompt **逐字节回退**；`tradingHorizon` 快照的键集合与改动前一致
  （`exitPriority` / `exitCommitment` / 三个 `exitIntent*` 键**全部缺席**）。
  **【R:P2-1】判据沿用仓库既有模式，不得硬编码旧哈希**：「与改动前的 sha256 一致」在单次提交里没有
  可执行判据。仓库既有的 `tests/test_ai_trade_prompts.py:747
  test_unarmed_horizon_keeps_three_prompts_byte_identical` 用的是「**显式传空垫片 vs 完全不传**」的
  sha256 相等——新形参一旦被写成必填、或垫片默认值被改成非空，用例立刻判红。本轮照抄该形态。
- **G-2（恒等）**：`build_decision_tool_schema(0.0) is DECISION_TOOL_SCHEMA` 仍然成立
  （`tests/test_ai_trade_position_step.py:132` 零改动通过）。
- **G-3（回归）**：`test_ai_trade_exit_intent_ledger.py` 里那条 `close → hold → close` 用例，在**修法前**
  必红（`streak==1`）、修法后必绿（`streak==2`）。评审节点须实际确认这条「先红后绿」。
- **G-4（通路）**：一条端到端用例断言「`close` 从持仓周期层到反思层之间**没有任何一层**改写它」——
  将来任何一层若开始拦 `close`，该用例即红。
- **G-5（不变式 X-1）**：两条断言，缺一不可——
  ① AST 扫描断言 `exit_intent.py` / `exit_priority.py` / `exit_commitment.py` 里不出现
  `open_long` / `open_short` 作为**放行**判据（只允许出现在「与离场矛盾」的判定里）；
  ② **【R:P0-2】行为断言**：构造一个「持多 + `open_short` + `exitConviction=final` + 证据 + 账本
  达标 + 配额充足」的断面，断言 `resolve_exit_priority` 返回 `level == "none"`、
  `evaluate_horizon_gate` 的 `priority_gates` **不参与并集**、G1 仍然拦下它。
  **①单独是不够的**：v1 的泄漏点在 `hold_horizon` 的接线上，`exit_priority.py` 里确实一个
  `open_long` 都没有——AST 扫描会全绿而 X-1 已被违反。
- **G-6（零回归）**：`executor.py` 的**开仓**路径在三种撤单结果下的行为与改动前逐字一致。
- **G-7（X-3，【R:P1-5】已按评审改口径）**：全仓 `place_order(` / `close_position(` 的调用点计数
  **净增为 0**；`flatten(` **允许 +1**（`session.exit_now()`，现有 3 处：`ai_auto/session.py:614`、
  `ai_auto/session.py:741`、`live_auto/session.py:199`），且该新调用点必须复用
  `_handle_flatten_result(..., terminal=False)` 的收尾。
  v1 的 G-7 把 `flatten(` 也冻结为净增 0，而 §6.2 明确要新增一处——**按 v1 的字面，本轮不可能放行**。
- **G-8（全量）**：`services/quant` 全量 pytest 与 `apps/web` 全量 vitest + tsc 的红条数与改动前
  **基线一致**（基线须由实施节点在动手前先跑一次并写进 §十一，不得引用本文档的任何数字）。
- **G-9（口径一致）**：两条——
  ① 前端不出现任何硬编码的中文档位标签或阈值数字（`aiTradeExit.ts` 的正则扫描用例守；
  **必须写 `\r?\n`**：本仓库 `core.autocrlf=true`，写死 `\n` 会在干净检出上恒红）；
  ② **【R:P0-5】枚举双向覆盖**：`EXIT_SOURCES` 的每一个值都必须能在
  `apps/web/lib/aiTradeAcceptance.ts` 的 `EXIT_SOURCE_LABELS` / `EXIT_SOURCE_COLORS`、
  `apps/web/lib/experience.ts` 的 `VALUE_LABEL_ZH` / `AXIS_VALUES.exit`、
  `ai_auto/acceptance_text.py` 的 `EXIT_SOURCE_LABELS`、
  `ai_auto/experience/taxonomy.py` 的 `VALUE_LABEL_ZH` 六处里查到；`RELEASE_GATES` 的每一个键都
  必须能在后端 `_RELEASE_LABEL` 与前端 `HORIZON_RELEASE_LABEL` 两处里查到。
  只查「有没有硬编码」是不够的——**漏加一条镜像不会产生硬编码，只会产生一个裸英文码**。
- **G-10（防滥用可证伪，【R:P0-1】已按评审加强）**：一条会话级用例断言「连续 8 轮声明 `final`」的
  最终形态是「前 6 轮放行、后 2 轮降档为 `high` 且 `downgradeReason=quota_exhausted`」，
  `aiStats.horizon.exitFinalDowngraded == 2`，**且后 2 轮 `executed["traded"] is False`**。
  最后半句是本条的要害：v1 只断言了 `downgradeReason` 与计数，而按 v1 §4.4.1 后 2 轮会挂起待裁决、
  复核官一句 `confirm` 就照样成交——**这条门禁在 v1 的设计下必然是假绿**。
- **G-11（签名迁移零遗漏）**：AST 扫描两个函数的全部调用点，断言按新的三元组解包——
  ① `evaluate_horizon_gate(`（现有 17 处：`horizon_runtime.py:239`、`backtest.py:807`、
  `tests/test_ai_trade_hold_horizon.py` 13 处、`tests/test_ai_trade_horizon_backtest.py:141`）；
  ② `apply_reflection(`（`session.py:1489`、`backtest.py:848`，以及 `tests/` 与 §附录A probe 2）。
  这是本轮**仅有的两处**非加法式签名变更（【R:P1-3】），必须有专属门禁：两者都是「漏一处即
  `ValueError: too many values to unpack`」——**会红，但会红在离根因很远的地方**。
  两者的回退等价性同时断言：`priority_gates=()` / `fail_open_actions=()` 时第三元恒为
  `False` / `None`，故 C-1 与 C-8 的一键回退逐字节成立。

### 8.2 验收标准（A-*，产品口径）

- **A-1**：复现用户场景——持仓 `intraday_swing`、G2 未满足、模型在第 2 轮给出 `close` +
  `exitConviction=final` + 证据 ⇒ **当轮成交**，轨迹显示「离场优先通道 · 已放行」。
- **A-2（【R:P1-4】已按评审收窄）**：模型连续两轮给出 `close`（中间夹一轮 `hold`）且未声明确信度
  ⇒ 第三轮 `streak==2` ⇒ **G2 结构性满足**（`_first_gate_reason` 的判据就是 `streak < rounds`）
  ⇒ 成交。**这是不需要模型学会任何新字段就能兑现的那一半收益，而它由轴 A 单独兑现，不需要
  `expedite` 解任何闸。** 同一条用例必须**反向断言**：本轮 `exitPriority.releasedGates` 里
  **不含 `G3`**——v1 让这条路径顺带解开换手迟滞，等于给每一个会话默认放松成本闸（详见 §4.3.1）。
- **A-3**：反思 LLM 超时 3 次 ⇒ `close` **仍然成交**，且 `degrade is None`、
  `exitPriority.failOpenReason == "reflection_timeout"`、`aiStats.horizon.exitFailOpenRounds == 1`
  （【R:P1-3】：放行不进 `degrade`）。
- **A-4**：存在一笔限价平仓挂单、撤单回 `unknown` ⇒ 市价离场**仍然下发**，status 事件如实说明。
- **A-5**：离场下发后未成交 ⇒ 面板出现「离场已确认，正在兑现（第 N/6 次）」，成交后自动消失；
  6 次未果 ⇒ 红条 + 【立即离场】按钮可用。
- **A-6**：【立即离场】平仓成功后会话**仍在运行**（`state == "running"`），且下一轮 prompt 如实
  告知「你的持仓已由用户手动平掉」。**【R:P1-6】追加**：【立即离场】**失败**后会话同样仍在运行，
  且 `status.flattenFailed` **保持 `False`**（推的是 `AI_TRADE_MANUAL_EXIT_FAILED`，不是终态红条）。
- **A-9（【R:P0-3】）**：模型给出**限价** `close`（`orderType=limit`）⇒ 挂单如实挂出，
  10 秒后**不发生任何市价平仓**，挂单直到 TTL 到期或成交为止；面板显示的委托价与交易所侧一致。
- **A-10（【R:P0-1】）**：`max_overrides_per_session` 调到 1 的会话里，第 2 次 `final`（证据充分、
  账本达标）**不成交**——`downgradeReason=quota_exhausted` 且**不进入挂起裁决**。
- **A-7**：模型每轮都声明 `final` 的会话，第 7 轮起自动降档，且会话结束时的周期验收卡能给出
  「最高优先级离场 6 笔，其中净额为正 N 笔」。
- **A-8**：所有新增中文在 UI 上不出现字面 `**`（本仓库已复发两次：后端文案里的 Markdown 粗体
  标记在 JSX 里会原样显示成星号）。

### 8.3 手工验收清单（节点 #3 执行）

1. 启动一个 `intraday_swing` 会话，人为把 `ai_trade_exit_override_max_per_session` 调到 1，
   观察第 2 次 `final` 的降档文案是否出现在 prompt 与轨迹两处。
2. 断开网络 60s 制造 `reflection_timeout`，确认 `close` 仍然成交（A-3）。
3. 手动在 OKX 挂一张同向限价单占住挂单槽位，触发一次 Agent 市价离场（A-4）。
4. **【R:P0-3】**让模型给出一次**限价** `close`，掐表观察 10 秒 / 30 秒 / 60 秒三个时点：
   挂单必须仍在，且**没有任何市价成交**（A-9）。这是本轮最容易被漏测、后果最贵的一条。
5. **【R:P1-6】**把 `/exit` 打到一个不存在的持仓上制造一次失败，确认面板**没有**出现
   「AI 自动交易停止时平仓失败」的终态红条，且会话仍在跑。
6. 全程截图对照 §4.6 的三处 UI 呈现。

---

## 九、风险与缓解

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| **R-1** | **模型把 `final` 当默认值**，把三闸变成摆设，回到「毛利为正、被换手成本吃成净亏」的老形态 | 高 | ①`max_overrides_per_session=6` 会话级硬配额；②`final` 必须带非空证据；③`override_rounds=2` 要求跨轮；④`override_min_hold_s=60` 绝对地板；⑤每一次 `final` 进 `aiStats` 与退出归因，事后可算账（X-9）；⑥宪章明写「它是给转折点准备的」；⑦A-7 / G-10 两条用例守 |
| **R-2** | 衰减账本让「一次随口的离场提议」在两轮后仍然计数，造成**过早离场** | 中 | ①`decay_rounds=2` 只容忍**一次**间断；②同向加仓（与离场直接矛盾的动作）**立即清零**而非衰减；③`decay_rounds=0` 是一键回退开关（C-2） |
| **R-3** | `close` 的反思改 fail-open 后，一次**真实**的坏离场因基础设施故障溜过去 | 中 | ①`reflection_abort`（模型真实裁决）**不在 fail-open 范围内**，逐字不变；②fail-open 轮如实记 degrade 码并进统计，「有多少笔离场是没被复核的」可查；③C-8 一键回退；④对称论证：fail-closed 的代价是**锁死一笔真实持仓**，量级更大（F-3） |
| **R-4** | 执行侧 unknown 分支放行导致**重复平仓** | 中 | ①`close_position` 带 `autoCxl=True`，交易所侧撤掉该合约挂单（已核实 `trade_client.py:204-213`）；②重复平仓走 `LIVE_NO_POSITION` 良性分支（`executor.py:337`）；③`outcome=="filled"` 仍必须止步；④**开仓路径逐字不变**（G-6） |
| **R-5** | 轴 C 的重试在盯市 tick 上打真实下单，可能与决策轮的 `_exec_lock` 竞争 | 中 | ①全部写经 `_exec_lock` 串行（与 `_apply_profit_exit` 同款）；②`retry_interval_s=10` 远大于盯市周期（~5s），不会每 tick 打单；③`should_retry` 先判 `projected`/epoch，空仓即空转 |
| **R-6** | `exit_pending` 豁免解 G3 后，在浮盈不足覆盖成本时离场，**确定性亏掉一次往返成本** | 中 | ①上界默认 2 小时（`mult=2.0`），不是几分钟；②它只在**模型持续两小时坚持要走**时才触发；③`mult=0` 关闭本豁免（C-3）；④验收卡把这类离场单列，事后可评估 |
| **R-7** | 行数红线（【R:P1-7】评审修正：`hold_horizon.py` 796 行**尚未越线**，v1 称「已越线」是事实错误；`AiTradePanel.tsx` 798 行、`AiTradeTraceRound.tsx` 841 行两处 v1 完全漏算） | 中 | ①硬承诺见 X-10 的四行基线表；②轴 A 纯函数改落新文件 `exit_intent.py`，`hold_horizon.py` 净增 ≤20 行、**仍在 800 以内**；③前端两块 UI 落两个新组件，两个既有大文件净增 ≤4 行；④节点 #3 的 Review 须实测**四个**文件行数并写进提交说明 |
| **R-8** | 前后端各存一份档位标签 / 阈值，静默漂移 | 中 | ①X-8：全部文案与数字由 `horizon-profiles` 与快照下发；②G-9 正则扫描门禁（注意 `\r?\n`） |
| **R-9** | `tool_call` 三键前端 `store.ts` 漏抄 ⇒ **静默功能缺失** | 低（【R:P1-8】已从「中」降级） | ①**删掉 `exit_priority` 相位**，`exitPriority` 随 `horizonWire` 整对象透传、最终档位搭 `reflection_verdict` 相位的既有手抄区——手抄面由**四处减为一处**；②`store.ts` 的这一处在 §7.2 #24 明确点名；③一条 store 用例断言三键从事件流到 `AiTradeRoundTrace` 的端到端落地 |
| **R-12（评审新增 ·【R:P0-1】）** | `final` 的**滥用路径从「直接放行」转移到「挂起裁决」**：模型学会「配额用尽就多写点理由让复核官点头」 | 中 | ①挂起**只对 `ledger_short`** 开启（`may_pend` 收在纯核心里，会话侧不重算）；②裁决放行与 override **共用同一份配额**（X-4b）；③`exitAdjudicatedRounds` 单独计数、进退出归因，事后可算账；④G-10 断言后 2 轮真的没成交 |
| **R-13（评审新增 ·【R:P0-3/P1-1】）** | 轴 C 与限价挂单生命周期**争夺同一个挂单槽位**：兑现层去撤模型自己挂的单、或在撤单未确认时与 `_poll_resting` 抢结算 | 中 | ①只对**市价** close 武装（§4.5.2）；②`should_retry` 的 `resting_close` 短路（§4.5.1）；③F-4 放行分支归因用 `_external_close_suppressed()`、成功后清 `self.resting`（§4.5.3）；④#32 逐条冻结这条时序 |
| **R-10** | 回测侧只装轴 A/B、不装轴 C，回测结论与实盘失真 | 低 | ①`format_backtest_notes` 追加一句免责：「回测每单必成，离场兑现重试在回测里恒不触发」；②`ai_trade_exit_priority_backtest_enabled` 独立总闸（C-1b），可跑对照臂 |
| **R-11** | 【立即离场】被误点 | 低 | ①二次确认弹窗写清三件事（§4.6.3）；②按钮默认只在 `stuck` 态显性出现，其余时候收在「更多」里 |

---

## 十、实施顺序与工作量

建议按**可独立验证**的顺序推进，每一步跑完对应用例再进下一步：

0. **签名迁移**（#3 的 `evaluate_horizon_gate` 三元组 + 17 处调用点 + #35）——【R:P1-3】本轮唯一一处
   非加法式变更，先做完并跑绿 G-11，后面每一步才有稳定地基。
1. **轴 A**（#0 新文件 + #3 部分 + #5 + #28）——最小、收益最大、且是唯一一条「不需要模型学会任何
   新东西」的修法。跑通 G-3 的「先红后绿」，并确认 `tests/test_ai_trade_horizon_runtime.py:122`
   零改动仍绿（【R:P2-6】）。
2. **F-3 反转**（#8 + #9 部分 + #31 的 ④）——独立、可单测、风险可控。
3. **F-4 执行侧**（#18 + #32）——独立于前三者，先跑 G-6 的开仓零回归。
4. **轴 B**（#1 + #3 剩余 + #5/#6/#7 + #29 + #31）——本轮主体。
5. **轴 C**（#2 + #9 剩余 + #30）。
6. **轴 D 后端**（#11 + #13 + #14 + #15 + #16）与**前端**（#19…#27）。
7. **冻结与全量**（#34 + G-8）。

粗估（评审后重估）：后端 ~1250 行净增（其中 **440 行是三个新纯核心** `exit_intent.py` /
`exit_priority.py` / `exit_commitment.py`）、前端 ~460 行（其中 **270 行是两个新组件**
`AiTradeExitPriorityRows.tsx` / `AiTradeExitCard.tsx`）、测试 ~800 行（多出 #35 与
§8.1 G-5②/G-9②/G-10 三条加强断言）。**既有大文件的净增被压到：`hold_horizon.py` ≤20、
`AiTradePanel.tsx` ≤2、`AiTradeTraceRound.tsx` ≤4、`session.py` ≤60**（X-10）。

---

## 十一、实施过程发现的方案缺陷

> 本节由**节点 #2（代码开发）**填写：凡本文档的设计与代码现实冲突之处，一律以代码为准并在此逐条
> 记录（编号 `D-*`），供节点 #3 复核。基线数字（§8.1 G-8）也写在这里。

### 基线（G-8 / C-8：**动手前先跑，不得引用本文档的任何数字**）

| 侧 | 命令 | 改动**前**（基线） | 改动**后** |
|---|---|---|---|
| 后端 | `pytest -q -p no:randomly`（`services/quant`） | **7 failed · 3916 passed · 5 skipped · 2 deselected**（959.88s） | **7 failed · 4024 passed · 4 skipped · 2 deselected**（973.34s） |
| 前端 | `NODE_ENV=test vitest run`（`apps/web`） | —（本轮先跑后端基线；前端改动后 184 files / 2136 tests 全绿） | 184 files / **2136 passed** |
| 前端 | `tsc --noEmit` | — | **零错误** |

**基线的 7 条红全部来自未跟踪的在制文件** `services/quant/tests/test_factor_library_expansion.py`
（`git status` 显示为 `??`），与本轮无任何关系：

```
FAILED tests/test_factor_library_expansion.py::test_library_total_is_twenty
FAILED tests/test_factor_library_expansion.py::test_new_factors_category_direction_cn
FAILED tests/test_factor_library_expansion.py::test_new_factors_reuse_existing_categories
FAILED tests/test_factor_library_expansion.py::test_new_factors_finite_and_no_inf_on_synthetic
FAILED tests/test_factor_library_expansion.py::test_bounded_factors_stay_in_range
FAILED tests/test_factor_library_expansion.py::test_rolling_new_factors_have_nan_warmup
FAILED tests/test_factor_library_expansion.py::test_zero_denominator_never_produces_inf
```

---

### D-1（阻断级 · 已按现实实施并留档）· X-10 的行数承诺在**算术上**不成立

**冲突**：X-10 的表格同时承诺 `hold_horizon.py`「净增 ≤ 20」与「仍在 800 以内」。实测基线是
**796 行**，故 `796 + 20 = 816 > 800`——两个半句**不可能同时成立**。评审侧修正了 v1「已越线」
这个事实错误，却没有重算另一半。

**实测**：本轮该文件的**最小必需**改动实测净增 **46 行**（796 → **842**）：

| 改动 | 行数 | 可否再压 |
|---|---|---|
| `HorizonState` +2 字段（含 2 行 Attributes） | 4 | 否（dataclass 定义只能在此） |
| `HorizonRule` +3 投影字段（含 1 行注释） | 4 | 否（同上） |
| `RELEASE_GATES` 注册 + `exit_intent` import | 2 | 否 |
| `release_reason_for` 末位分支（含 1 行注释） | 4 | 否 |
| `build_snapshot` 挂三个加法键 | 2 | 已压到 2（装配下沉 `exit_intent.intent_wire`） |
| `evaluate_horizon_gate` **三元组迁移 + 解闸并集 + 挂起位** | 12 | 已压到最简（Args/Returns 各 3 行） |
| `resolve_horizon_rule` / `rule_wire` / `profiles_wire` 三处延迟导入接线 | 18 | 已压（三个 helper 全部内联，装配下沉 `exit_priority`） |

**已做的全部压缩**：四个纯函数与全部 wire 装配外移到新文件 `exit_intent.py`（122 行）与
`exit_priority.py`（321 行）；三处反向依赖用**函数内延迟导入**内联（沿用 `_acceptance_preview`
打破同类环的既有姿态），不新增任何 helper 函数。

**没做的那件事**：真正回到 800 以内的唯一办法，是把该文件既有的**展示 / 预览半边**
（`rule_wire` / `rule_from_wire` / `profile_wire` / `profiles_wire` / `_backstop_repair_wire` /
`_turnover_preview` / `_acceptance_preview`，约 130 行）切到新模块——那是「切法与
`hold_horizon_text.py` 同款」的正解，但它是**未列入变更表的重构**（本节点的约束明令禁止），且会
触到 `api/ai_auto.py`、`manager.py` 与多个测试的 import。**故本轮不做，如实留给节点 #3 决定。**

> **节点 #3 的处置：已做**（见 §十二 D-13）。实际只需切走 `profile_wire` / `profiles_wire` /
> `_backstop_repair_wire` / `_turnover_preview` / `_acceptance_preview` 五个（`rule_wire` /
> `rule_from_wire` 是会话契约的镜像、与判定同侧，留在原处），**842 → 723**；调用点共 4 处
> （`api/ai_auto.py` 1 处 + 三个测试文件各 1 处），`manager.py` 未受影响。

---

### D-2（阻断级 · 同上）· `session.py` 的「净增 ≤ 60」同样不成立

**实测**：2479 → **2914**（净增 **435**）。§7.1 #9 自己列的清单就远不止「两个薄方法 + 加法接线」：

- 轴 C 的四步收尾：`_settle_exit_commitment` / `_apply_exit_commitment` / `_report_exit_commitment`
  / `_arm_exit_commitment`（§4.5.2 逐步钉死了它们与 `_maybe_profit_exit` 同构）；
- 轴 B 的六个装配点：`_resolve_exit_priority` / `_mark_exit_pending` / `_adjudicate_exit_priority`
  / `_note_exit_priority_round` / `_note_exit_fail_open` / `_attach_exit_wires`；
- `/exit` 的 `exit_now` + `_handle_flatten_result` 的 `terminal` 分支（【R:P1-6】）；
- 一次性打标槽 `_consume_exit_source`（【R:P0-6】）、`closedFilled` 的四处回执（【R:P0-4】）、
  `_settle_pending_exit`（§4.4.2 的三条裁决映射）、`AiTradeManualExitDisabled`。

按本方案自己写死的落点实现，**任何写法都到不了 60**。

**已做的缓解**：判定 100% 在三个新纯核心里（会话侧只做状态推进与装配，X-10 的「纯核心不依赖调用
顺序」完整兑现）；前端两块 UI 全部落新组件，两个既有大文件的承诺**全部兑现**：

| 文件 | 基线 | 承诺 | 实测 | 结论 |
|---|---|---|---|---|
| `ai_auto/hold_horizon.py` | 796 | ≤ +20 且 < 800 | **842**（+46）→ 节点 #3 切分后 **723** | ✅ 见 D-13 |
| `ai_auto/session.py` | 2479 | ≤ +60 | **2914**（+435） | ❌ 见本条 |
| `components/Chart/AiTradePanel.tsx` | 798 | +0 且 < 800 | **797**（**−1**） | ✅ |
| `components/Agent/AiTradeTraceRound.tsx` | 841 | ≤ +4 | **844**（+3） | ✅ |

**建议（留给节点 #3）**：若要收敛 `session.py`，正解是按仓库既有的四个先例
（`horizon_runtime` / `profit_guard_runtime` / `cost_runtime` / `sizing_runtime`）把**纯同步的那
一半**下沉成 `ExitRuntime`（`_resolve` / `_mark_pending` / `_adjudicate` / `_note_*` / `_attach` /
`_arm` / `_consume_exit_source` / `_exit_epoch` / `_resting_close` / `_settle_pending_exit` /
`_pending_exit_note`，约 200 行），async 编排点（`_settle/_apply/_report_exit_commitment`、
`exit_now`）留在会话。本轮同 D-1 的理由不做。

---

### D-3（P0 · 已在实现中修正）· 反思整层关闭时，`may_pend` 会退化成「`ledger_short` 一律放行」

**方案没有覆盖 `reflection_enabled=False` 这条路径。** 按 §4.3.2 / §4.4.1 的字面：`may_pend` 为真
⇒ `evaluate_horizon_gate` 返回 `pending=True` 且**不 veto** ⇒ decision 保持 `close` 一路走到
`_execute_decision`——而**根本没有复核官**去裁决它。净效果是「账本不足的 `final` 一律放行」，即
绕过 X-4 的第三个合取项本身；而 X-4 白纸黑字写着「删掉其中任意一个合取项都会把这条通道变成
模型的自助放行按钮」。

**修法**：`session._resolve_exit_priority` / `backtest._resolve_exit_priority` 在
`not decider.reflection_enabled` 时把 `may_pend` 强制为 `False`（`dataclasses.replace` + 同步
`wire["mayPend"]`），此时 `final` 走既有降档路径（按 `high` 处理）。用例：
`test_ai_trade_exit_priority.py::test_may_pend_is_true_only_for_ledger_short` 守纯核心一侧，
会话一侧由 `_make_session(reflection=...)` 的构造缝覆盖。

---

### D-4（P1 · 方案已点名，此处写死落地口径）· 回测的 `now_wall` 是**根时间戳**，不是墙钟

§7.1 #12 ③ 要求记一条。落地口径：`exit_pending` 的挂起上界与 `override_min_hold_s` 的绝对地板
在回测里衡量的是**根时间跨度**，实际生效粒度是一根 K 线（`intraday_swing` 的 60 秒地板在 1H 回测
里恒被一根 K 线跨过 ⇒ 该合取项在回测中**恒成立**，与实盘存在系统性差异）。
已由 `format_backtest_notes` 追加两句免责，另一句是 R-10 的「轴 C 不在回测装配」。

---

### D-5（P1 · 已在实现中补齐）· `exit_order_slot_busy` 原本**没有产生者**

§5.5 把它列为降级码、§7.2 #22 要求前端 `DEGRADE_REASON_LABELS` 加这一条，但**方案没有写它从哪里
产生**：`_do_close` 只返回事件、不返回降级码，会话侧拿不到任何可写进 `degrade` 的东西。照字面只加
前端标签，会得到一条**永远不会出现**的死标签——而 CLAUDE.md 明令禁止没有展示落点的常量，本仓库
也已为「有写点没读点 / 有读点没写点」出过多次事故。

**修法（三步，全部是加法）**：
1. `LiveExecutor.last_close_block: str | None`——`apply()` 入口**每次清零**（绝不跨轮残留），
   `_do_close` 在「槽位未腾出且不放行」时置 `exit_order_slot_busy(<outcome>)`；
2. `session._execute_decision` 的回执加 `closeBlocked` 键；
3. `_run_decision_round` 在 `_execute_decision` 之后 `degrade = degrade or executed["closeBlocked"]`
   （仍遵循「先执行者胜出」）。

用例：`test_ai_trade_exit_executor.py::test_the_silent_path_now_carries_a_readable_code`（含「每次
apply 先清零」的跨轮断言）。

---

### D-6（P2 · 判定为不改，记录理由）· `_settle_benign_close` 的第二个调用点保持默认

【R:P1-1】只要求 `_do_close` 那一条传 `suppress_external`。`flatten` 那一条
（`executor.py` 的 `LIVE_NO_POSITION` 分支）**刻意保持默认 `False`**：`flatten` 会先撤挂单，走到该
分支时挂单已不在飞，`_external_close_suppressed()` 恒为 `False`，传与不传**行为等价**；显式传只会
让读者误以为那里有不同语义。

> **节点 #3 更正（结论不变，理由要改）**：「走到该分支时挂单已不在飞」**不成立**——`flatten` 的
> `_cancel_resting("session_stop")` 回 `"unknown"` 时 `self.resting` 是**保留**的，故那里
> `_external_close_suppressed()` 可以为真。结论仍是**不改**：`flatten` 是会话终止路径，此后不再有
> 下一轮 `_poll_resting` 来收敛延后的结算，就地合成一条 `external_close` 比什么都不落更接近事实；
> 而 D-11 改掉的 `_do_close` 是**会话继续运行**的路径，延后一轮必定有人来收。两条路径的处置不同
> 源于「之后还有没有下一轮」，不是源于「挂单在不在飞」。

---

### D-7（P2 · 已按方案落地，但当前无读点）· `HorizonState.last_exit_intent_wall`

§4.2.1 给它的用途是「供 prompt 说明『你上一轮没提，本轮按衰减计入』」，但该文案实际取的是快照键
`exitIntentLapses`（`_confirm_line`）。字段仍按方案落地（它由 `advance_intent` 写入，是账本状态的
一部分），但**当前全仓没有读点**。若节点 #3 认为这构成「没有展示落点的常量」，删它是安全的——
无任何消费方，且 `HorizonState` 是末位带默认值的加法字段。

---

### D-8（P2 · 实施细节，供复核）· F-2 注记与 `tool_call` 三键读**同一个引用**

【R:P2-7】要求留存「进入 `_apply_horizon_gate` 那一刻」的 decision。实现上 `gate_entry = decision`
这一个局部量同时供两处消费：`build_gate_veto_note(gate_entry, ...)`（模型侧的论证连续性）与
`_emit_tool_call_trace(..., gate_entry)`（用户侧的「提议 → 被拦」保真行）。**刻意共用一个引用**：
两处各留一份必然在将来某次改动后分叉，届时会出现「注记说 close、卡片说别的」——而这两处恰恰是
本轮要根除的同一种不诚实的两个面。

---

### D-9（P2 · 连带改动清单，供节点 #3 逐条核对）· 既有用例的**必要**改动

本轮**仅有的两处**非加法式签名变更（G-11 / C-1）带来的连带修改，全部是解包元数或枚举登记，
**没有一处改动断言的语义**：

| 文件 | 改动 | 处数 |
|---|---|---|
| `tests/test_ai_trade_hold_horizon.py` | `evaluate_horizon_gate` 二元组 → 三元组 | 13 |
| `tests/test_ai_trade_horizon_backtest.py` | 同上 | 1 |
| `tests/test_ai_trade_horizon_runtime.py` | `HorizonRuntime.gate` 三元组 → 四元组 | 5 |
| `tests/test_ai_trade_reflection.py` | `apply_reflection` 二元组 → 三元组 | 16 |
| `tests/test_ai_trade_horizon_exemptions.py` | `session._apply_horizon_gate` 二元组 → 三元组 | 4 |
| `tests/test_ai_trade_summary_session.py` | 最小 decision 替身补 `exit_conviction` / `exit_evidence` | 1 |
| `tests/test_ai_trade_turnover_session.py` | 豁免矩阵冻结断言登记第五条 `exit_pending`（**四条既有条目逐格未动**） | 1 |
| `tests/test_ai_trade_exit_attribution.py` | 模块 docstring 的「九值」改述（枚举已十值；**断言一行未动**，24 条全绿） | 1 |

其中最后一条值得单独说明：`test_round_one_gate_matrix_is_frozen` 冻结的本意是「三道闸的判定式与
四条既有豁免的**逐格取值**一个字符都不动」，而不是「这张表永远不许增行」。本轮只**追加**了
`exit_pending: ("G2", "G3")`，四条既有条目的取值逐格未变，断言的证伪能力因此未被削弱。

---

### D-10（P1 · 已在实现中修正）· §6.2 的「OKX 业务失败 → 502」在 `/exit` 这条路径上**不可达**

§6.2 要求 `/exit` 把 `OKXPrivateError` 映射成 `502 + 原始 sCode + 中文原因在前`（沿用
`/backstop/repair` 的翻译点纪律）。但两条路径的失败形态**根本不同**：

| | `/backstop/repair` | `/exit` |
|---|---|---|
| 底层调用 | `repair_backstop` → 直接下单，`OKXError` **原样上抛** | `executor.flatten` |
| 失败形态 | 抛异常 | **内部重试后返回结构化失败** `{"ok": False, "code", "message", "residual"}`（`executor.py` 的 `for attempt in range(...)` 循环把 `OKXError` 全部吃掉） |

照字面在端点写 `except OKXPrivateError` 会得到一段**永远不会执行的死代码**——而 CLAUDE.md 明令
「不要为『理论上不会发生』的分支写防御性 `try/except`，那只是噪音」。

**修法（保住 §6.2 的意图、去掉不可达的形式）**：
- 会话侧 `_manual_exit_failure_text(result)` 把 sCode 与原文译成一句 **中文原因在前** 的
  `立即离场未成功：<原文>（<sCode>）`（无 sCode / 无原文时不留空括号、不编一个原因）；
- 端点只保留 409（运维总闸）与 404（分组 / 成员缺失），失败沿 **200 + `{"ok": false, "message"}`**
  返回，并另有一条 `AI_TRADE_MANUAL_EXIT_FAILED` 的 WS error 推给面板（【R:P1-6】）。
- 用例随之从「断言 502」改为「断言那句话中文在前且带 sCode」
  （`test_exchange_failure_text_puts_the_chinese_reason_first`）。

§6.2 真正在意的那件事（「这一发的失败提示往往是用户对本层唯一的一次阅读，绝不能只给一串归一码」）
**完整兑现**，只是载体从 HTTP 状态码换成了回执正文。

---

### 终态回归（改动后）

| 侧 | 结果 |
|---|---|
| `services/quant` 全量 pytest | **7 failed · 4024 passed · 4 skipped · 2 deselected**（973.34s）——**红条集合与基线逐条相同**（同一个未跟踪在制文件的同 7 条），净增 **+108 条绿**（4024−3916） |
| `apps/web` 全量 vitest | **184 files / 2136 tests 全绿** |
| `apps/web` `tsc --noEmit` | **零错误** |
| 本轮新增后端用例 | `exit_intent_ledger` 14 · `exit_priority` 20 · `exit_commitment` 13 · `exit_executor` 9 · `exit_session` 23 · `exit_api` 7 · `exit_freeze` 13 · `exit_wiring` 8 = **107 条全绿** |
| `ruff check app tests` | **All checks passed**（配置见 `pyproject.toml`：`select = ["F", "E9"]`） |
| 本轮新增前端用例 | `lib/aiTradeExit.test.ts` **15 条全绿** |

> **`5 skipped` → `4 skipped` 的那一条已查明，且是个好消息**：
> `tests/test_ai_trade_acceptance_wiring.py::test_frozen_files_are_untouched` 的门是
> `if not _git_diff_names(): pytest.skip(...)`。跑基线时工作树**没有任何被跟踪文件的改动**
> ⇒ `git diff --name-only` 为空 ⇒ 该用例被跳过；终态跑时有 36 个被跟踪文件改动 ⇒ 它**真的执行了**，
> 并断言三个冻结文件（`engine/simulator.py` / `ai_auto/cost_model.py` / `ai_auto/cost_text.py`）
> 一行未碰——**通过**。换言之这一条不是「少跳了一个」，而是「一道此前空转的冻结门禁在本轮第一次
> 真正生效并放行」。绿条净增 **+108** = 本轮新增 107 条 + 这一条从 skip 转绿。

**G-3 的「先红后绿」**：`test_ai_trade_exit_intent_ledger.py::test_a_single_hesitation_no_longer_wipes_the_exit_ledger`
断言 `close → hold → close` 之后 `streak == 2`。修法前 `note_intent` 在 `hold` 那一轮走
`_reset_intent()`，第三轮只能回到 `streak == 1`——该用例在修法前必红、修法后必绿，节点 #3 可用
`git stash` 该文件之外的改动实测。

---

## 十二、节点 #3 复核发现与就地修复

> 本节由**节点 #3（代码 Review、修复与提交）**填写：逐项对照 §七 的变更计划与 C-1…C-9 复核代码，
> 凡发现的问题一律**就地修好**（不留 TODO），并在此逐条留档。编号延续 `D-*`。

### 放行条件复核（C-1…C-9）

| # | 复核结论 | 判据（复核侧实跑 / 实读） |
|---|---|---|
| **C-1** | ✅ | `evaluate_horizon_gate(` / `apply_reflection(` 的全部调用点按新元数解包（#35 的 AST 扫描绿）；两个新形参均 keyword-only 带回退默认值，`priority_gates=()` / `fail_open_actions=()` 时第三元恒 `False` / `None` |
| **C-2** | ✅ | `_may_pend` 只对 `ledger_short` 返回真且要求配额未尽；G-10 绿（含「后 2 轮 `action == "hold"`、`pending is False`」两句） |
| **C-3** | ✅ | `resolve_exit_priority` 第 1 条判据是 `gated_kind(...) != "close"`；#29 的反手参数化用例覆盖持多 `open_short` / 持空 `open_long` 两向恒 `none` |
| **C-4** | ✅ | `_arm_exit_commitment` 要求 `ord_type == "market" and limit_px is None`，`should_retry` 另有 `resting_close` 短路；#30 / #31 ⑥ 两侧都有用例 |
| **C-5** | ✅ | 六处枚举镜像由 G-9 ② 双向覆盖断言守；打标槽一次性消费 + `kind == "atomic"` 双条件，**另修好一处会随机抹掉它的竞态**（D-12） |
| **C-6** | ✅ | 实测四个文件：`hold_horizon.py` **723**（切分后，D-13）、`AiTradePanel.tsx` **797**、`AiTradeTraceRound.tsx` **844**（+3，承诺 ≤4）、`session.py` **2923**（既有欠账，承诺不成立见 D-2；
本节点又 +9 行是 D-12 的竞态修复与注释） |
| **C-7** | ✅ | fail-open 放行走 `apply_reflection` 的第三元、原样返回入参 `degrade`；#31 ④ 四种失败码参数化断言 `degrade is None` |
| **C-8** | ✅ | §十一 的基线由节点 #2 动手前实跑并留档，红条集合逐条可核（同一未跟踪在制文件的 7 条） |
| **C-9** | ✅ | §十一 D-1…D-10 十条 + 本节 D-11…D-19 九条 |

### D-11（P0 · 已修）· F-4 放行分支的 `LIVE_NO_POSITION` 会**丢一笔成交**并在下一轮落一条幽灵成交，而 #32 的对应用例是**假绿**

**取证**（临时探针，跑完即删）：让 `close_position` 在**市价**那一发抛 `LIVE_NO_POSITION`、
`get_order` 回 `filled`，走完 F-4 的完整时序：

```
TRADES-AFTER-APPLY [('long', 10.0, 0.0)]        ← 本轮一条 close 成交都没有
RESTING RestingOrder(intent='close', sz=0.1, adopted_sz=0.0)   ← 挂单指针还留着
PROJECTED flat                                   ← 但仓位已被就地归 flat
TRADES-AFTER-POLL [('long', 10.0, 0.0), ('close', 0.0, 0.0)]   ← 下一轮落了 qty=0 的幽灵成交
```

三条后果，一条比一条难查：① `suppress_external=True` 之后**没有任何 trade 事件**——那笔平仓从
成交列表、退出归因、经验层往返、成本记账里**整个消失**，而 `realized_pnl_usd` 已按 `ref_px`
（不是真实 `avgPx`）加过一次；② 那条 status 文案仍在说「标的已在交易所侧平仓（OCO 触发）」——
错归因只是从 trade 的 `source` 搬到了用户唯一会读的那句话里；③ `self.resting` 未清 ⇒ 下一轮
`_poll_resting` 读到 `filled` 再结算一次，而 entry 断面已被清空，落一条 `qty=0` 的幽灵成交
（`_settle_close` 会连带再动一次已实现盈亏）。#32 的 `test_our_own_limit_close_is_never_recorded_
as_an_exchange_oco` 之所以全绿，是因为 `_long_with_resting_close` 末尾的 `calls["closes"].clear()`
把 `raise_on_close` 的「第 2 次调用才抛」计数**清回了 0**——那一轮走的是 `_settle_close` 成功路径，
`LIVE_NO_POSITION` 分支**一次都没被执行**。

**修法**：此刻本地**分不清**「我方限价单成交了」与「交易所 OCO 先平了」，就地结算无论选哪个
`source` 都有一半概率是假的。故 `_do_close` 在 `_external_close_suppressed()` 为真时**不就地结算**，
只推一条如实的 status，把收敛交给下一轮 `_poll_resting`：读到 `filled` 走 `_adopt_close_fill`
（真实 `avgPx` / 真实张数 / `explain.kind == "atomic"` ⇒ 归 `agent` / `agentOverride`），读到
`canceled` 则由 `_on_reconcile_ok` 合成 `external_close(source="external")`。两条支路都比就地猜一个
来源诚实，代价只是延后一轮——`_external_close_suppressed()` 在 `reconcile` 侧立的正是这条规矩
（「最坏也只是延后一轮采纳」）。`_settle_benign_close` 的 `suppress_external` 形参随之**删除**
（无人再传 `True`，留着就是一个没有写点的形参）。

**用例**：`raise_on_close` 的判据从「第 2 次调用」改为 `close.ord_type == "market"`（计数会被
helper 清零，委托类型不会）；原用例补 `assert [c.ord_type for c in calls["closes"]] == ["market"]`
（**先证明分支真的被走到**）；新增 `test_the_deferred_settlement_lands_on_the_next_poll_as_our_own_
close` 断言下一轮落的是 `qty == 真实张数`、`price == avgPx`、`kind == "atomic"` 的**一笔**平仓。

### D-12（P1 · 已修）· 打标槽会被**任意一个盯市 tick** 抹掉，X-9 的账本从「系统性高估」变成「随机漏记」

`_settle_exit_commitment` 原本在「本轮未武装」时无条件 `self._pending_exit_priority = None`，而
「未武装」在**每一个** tick 上都成立。槽是在 `_note_exit_priority_round()` 里置位、在
`_execute_decision` → `_dispatch` 的成交事件上消费的，两者之间隔着一次**真实下单的 await**——
tick 只要落在那个窗口里（下单耗时 ≈ 百毫秒量级，盯市周期 5s 量级），一次 `override` 平仓就会被
记成普通 `agent`。方向虽然安全（漏记而非高估），但 X-9 要的是一个**能回头算账**的数，随机漏记同样
让这条通道无法被证伪。

**修法**：判据收紧为「**已武装的**离场意图因持仓身份变化而作废」（`before.armed and not
after.armed`）——那才是 P0-6 真正要防的「下一笔持仓继承上一笔的档位」。用例同步改成先武装再变身份
（原用例在空仓上直接调用，恒走「未武装」分支，证伪能力为零），并新增
`test_a_plain_tick_never_wipes_the_stamp_slot_mid_flight` 守住这条竞态。

### D-13（阻断级 · D-1 的了结）· `hold_horizon.py` 切出展示半边，回到 800 行以内

D-1 把「承诺自相矛盾」查清楚了，但把结论留给了本节点。按 D-1 自己写下的「正解」执行：把**没有任何
判定逻辑**的展示 / 预览半边（`profile_wire` / `profiles_wire` / `_backstop_repair_wire` /
`_turnover_preview` / `_acceptance_preview`）整体切到新模块 `ai_auto/hold_horizon_profiles.py`
（151 行），`hold_horizon.py` **842 → 723**，重新回到 CLAUDE.md 的 800 行红线以内。

切分是纯搬运、零逻辑改动，依赖方向单向（新模块 import `hold_horizon` / `exit_priority` /
`horizon_acceptance`，三者都不反向 import 它），顺带把 D-1 为绕环而写的**两处函数内延迟导入**还原
成正常的模块级导入。调用点共 4 处：`api/ai_auto.py` 1 处 + 三个测试文件的 import 各 1 处。

`session.py`（2914 行）**本轮不动**：D-2 建议的 `ExitRuntime` 下沉要动 12 个方法与全部会话侧接线，
是一次独立的重构轮，混进本次特性提交只会让回归面无法界定。它是既有欠账（合入前已 2479 行、早已越线），
本轮不加剧那条**未越线**的（`hold_horizon.py`）即已兑现 C-6 的实质。

### D-14（P2 · 判定为不改，记录理由）· 前端保留了五档 / 降档原因的中文标签

`lib/aiTradeExit.ts` 里有 `LEVEL_LABEL` / `DOWNGRADE_LABEL` / `GATE_LABEL` 三张中文映射表，与
X-8「前端一个字、一个标签都不硬编」的字面冲突，但**与 §7.2 #19 的变更计划（「中文化与徽标色」）
以及仓库既有先例一致**（`aiTradeHorizon.ts` 的 `HORIZON_RELEASE_LABEL`、`aiTradeAcceptance.ts` 的
`EXIT_SOURCE_LABELS` 都是同一形态，本轮 §7.2 #24b/#24c 还要求往里加条目）。判定为不改：标签不是
判定口径、不会造成读数分叉，而**枚举漏抄**这一真正的风险由 G-9 ② 的双向覆盖断言在两侧兜住
（`test_g9_every_exit_source_is_reachable_in_all_six_mirrors` / `..._release_reason_...`）。
阈值与数字则**确实**一个都没有硬编（G-9 ① 的正则扫描守）。

### D-15（P2 · 判定为不改，记录理由）· 三个 `exitIntent*` 快照键**恒在场**

G-1 的字面要求是总闸关时三键「全部缺席」，实现选择了**恒在场 + 中性默认值**
（`exitIntentLapses=0` / `exitIntentAgeSeconds=None` / `exitPendingMaxSeconds=0.0`），理由是本仓库
journal / trace 的既有纪律是形状稳定。G-1 真正要守的那件事——**三份 prompt 逐字节回退**——不受影响
且已被 sha256 门禁独立断言（`_confirm_line` 在 `lapses == 0` 时逐字回退，另有专门用例证伪）。
判定为不改，只在此留档，免得下一轮有人照 G-1 的字面把形状改回条件式而打破 trace 契约。

### D-16（P2 · 只修文档）· pending 轮遇 fail-open 时实际解开的是 G1，而档位仍是 `expedite`

`_settle_pending_exit` 的 docstring 原本写「`confirm` / fail-open 放行 ⇒ 档位重算为 `adjudicated`」，
与代码不符（代码只在 `verdict.degrade_reason is None` 时重算）。**代码是对的**：基础设施失败不是
背书，档位不该升、配额不该扣，事实另挂 `failOpenReason` 与 `exitFailOpenRounds`。但由此产生一个
必须留档的事实：挂起轮在 `evaluate_horizon_gate` 内部**已经跳过了 veto**，故这一轮遇上 fail-open
时连 G1 也一并没拦——而 wire 上的 `releasedGates` 只写着 G2。两者并不矛盾（`pending` / `pendingReason`
/ `failOpenReason` 三个键同时在场，读得出完整时序），但 docstring 那句会把下一位读者引向「把
fail-open 也升档」的错误修法，故就地改写。X-6 与本条的净效果一致：`close` 侧的不确定一律放行。

### D-17（P2 · 已补）· 13 项新配置在 `.env.example` 里没有落点

§7.1 #17 只列了 `config.py`，于是 13 项 `AI_TRADE_EXIT_*` / `AI_TRADE_MANUAL_EXIT_*` 全部没有进
仓库根的 `.env.example`——而那份文件是**运维唯一能看到这些开关存在**的地方（后端真正读的是
`services/quant/.env`，打包后的 PyInstaller 后端只读进程 env，两者都从这份样例抄）。前两轮
（`e76dacf` 的 `AI_TRADE_EXPERIENCE_*`、更早的 `AI_TRADE_HORIZON_*`）都是随特性一并补的，本轮照办：
按同款体例追加一节，每一项写清**关掉它会回退到什么**（回滚粒度才是这份文件的价值）。
`AI_TRADE_` 前缀已在 `desktop/src/lib/config.js` 的 prod 透传白名单内（实测 `config.js:80`），
故桌面端无需再改一行。

### D-18（P1 · 已补）· `aiStats.horizon` 的七个新计数**有写点没读点**，X-9 只兑现了一半

X-9 的原话是「**Agent 用掉了几次最高优先级、其中几次事后证明是对的**必须是一个能查的数——否则
这条通道无法被证伪，也就无法被信任」。**「事后是对是错」那一半兑现了**（周期验收卡的
`exit_override_diagnostics` 那一句，取的是退出分布里 `agentOverride` 行的净额与胜率）；
**「用掉了几次」那一半没有**：`HorizonRuntime.stats()` 里新加的七个计数
（`exitExpediteRounds` / `exitOverrideRounds` / `exitAdjudicatedRounds` / `exitFinalDeclared` /
`exitFinalDowngraded` / `exitPendingReleases` / `exitFailOpenRounds`）落进 `aiStats.horizon` 之后
**全仓没有任何读点**——`AiTradeHorizonStats` 连字段都没声明，统计看板自然一个也不显示。这正是本仓库
反复出事、CLAUDE.md 明令禁止的那一形态（「有写点没读点」），而上一轮的换手计数是**带着
`turnoverVetoLine` 一起进来的**，本轮漏了对称的那一步。

**修法**（沿用 `turnoverVetoLine` 的既有形态，三处加法）：`aiTradeTypes.ts` 补七个可选字段；
`aiTradeHorizon.ts` 新增 `exitPriorityStatsLine`（七个计数全 0 / 全缺席 ⇒ `null` ⇒ 整行不渲染，
绝不显示一排误导性的 0）；`AiTradeStatsDetail.tsx` 的 `HorizonKpiGroup` 加一行渲染。三个呈现取舍
写进了函数 docstring：**不并入**上面四个冻结的「被拦下」计数（求和无意义）、`final` 声明与被降档
笔数**并排**（只报放行数会让「每轮都喊 final」看不见）、fail-open 放行**单独成项**（它既不是模型的
功劳、也不是一次降级）。用例三条落在 `aiTradeHorizon.test.ts`。

### D-19（P2 · 已修）· 两处门禁的**判据与它自己的 docstring 不符**

一、`test_g7_no_new_order_write_paths` 的 docstring 写「调用点**净增为 0**」，但它算完那份
`counts` 之后**从不断言它**——真正被断言的是 `owners`（谁在调用）。算了不用是死码，更要紧的是它会
让下一位读者以为「净增数真的被守住了」。判定：**判据是对的、话是错的**——调用点计数会随任何一次
无关重构漂移（本节点的 D-13 切分就动了两处），而「新纯核心里出现了 `place_order(`」是一个永远不该
发生的事实。故删掉死码、把 docstring 改写成它实际守的那件事。

二、`test_a_failed_exit_never_sets_the_terminal_flatten_flag`（#33）跑在 `_FakeSession` 上——它断言的
`flattenFailed is False` 是**桩自己写死的初值**，真实的 `_handle_flatten_result(..., terminal=False)`
分支一行都没被执行。这与 D-11 是同一种病（用例绿着，而它声称守住的那条路径根本没被走到）。
补：`test_ai_trade_exit_session.py::test_a_mid_flight_exit_failure_never_sets_the_terminal_flag`
在**真实会话**上直接驱动该分支，断言 `flatten_failed is False` / `residual is None` / 推的是
`AI_TRADE_MANUAL_EXIT_FAILED`，并**反向证伪**默认 `terminal=True` 的停止路径逐字未动（仍置终态
字段、仍推 `AI_TRADE_FLATTEN_FAILED`）。

---

## 附录 A：探针脚本全文（可原样重跑）

运行方式（Git Bash / Windows）：

```bash
cd services/quant
PYTHONPATH="$(pwd)" .venv/Scripts/python.exe -u <probe.py>
```

### probe 1 · 复现「一轮 hold 清零离场账本」（§2.2）

```python
"""只读探针：复现「离场确认计数被一轮 hold 清零」与两个派生量的数值。"""
from app.ai_auto.hold_horizon import (
    PROFILES, HorizonRule, HorizonFacts,
    derive_confirm_window_s, derive_min_hold_s, evaluate_horizon_gate,
)
from app.ai_auto.horizon_runtime import HorizonRuntime

p = PROFILES["intraday_swing"]
print("[1] intraday_swing target_min =", p.target_hold_min_s, "bar =", p.bar,
      "wake_min =", p.wake_interval_minutes, "rounds =", p.exit_confirm_rounds, "k =", p.hysteresis_k)
print("[2] derive_min_hold_s      =", derive_min_hold_s(p.target_hold_min_s), "s")
print("[3] derive_confirm_window_s=", derive_confirm_window_s(p.target_hold_min_s, p.bar), "s",
      "=", derive_confirm_window_s(p.target_hold_min_s, p.bar) / 3600.0, "h")

rule = HorizonRule(enabled=True, profile_id="intraday_swing", title="intraday",
                   min_hold_s=derive_min_hold_s(p.target_hold_min_s),
                   exit_confirm_rounds=2,
                   confirm_window_s=derive_confirm_window_s(p.target_hold_min_s, p.bar),
                   hysteresis_k=1.5, target_hold_min_s=p.target_hold_min_s,
                   target_hold_max_s=p.target_hold_max_s)
rt = HorizonRuntime(rule)
T0 = 1_000_000.0

def facts(now, px=0.09085):
    return HorizonFacts(seq=1, side="long", entry_px=0.09, mark_px=px, leverage=3.0,
                        now_wall=now, px_age_s=1.0, cost_bps=10.5)

rt.note_open({"side": "long"}, T0, seq=1)
now = T0 + 10.31 * 3600

class D:
    def __init__(self, a): self.action = a
    size_fraction = None; confidence = None; reason = ""

for i, act in enumerate(["close", "hold", "close"]):
    now += 1800.0
    rt.note_intent(act, None, position_side="long", now_wall=now)
    snap = rt.refresh(facts(now))
    dec, reason = evaluate_horizon_gate(D(act), snap, rule, position_side="long")
    print(f"round{i+1} act={act:5s} streak={snap['exitConfirmStreak']} "
          f"first_wall={rt.state.first_exit_intent_wall} "
          f"windowRemain={snap['confirmWindowRemainingSeconds']} "
          f"elapsed={snap['confirmWindowElapsed']} gate_reason={reason}")
```

实测输出（2026-08-25）：

```
[1] intraday_swing target_min = 14400.0 bar = 1H wake_min = 30.0 rounds = 2 k = 1.5
[2] derive_min_hold_s      = 7200.0 s
[3] derive_confirm_window_s= 3600.0 s = 1.0 h
round1 act=close streak=1 first_wall=1038916.0 windowRemain=3600.0 elapsed=False gate_reason=horizon_exit_unconfirmed(1/2)
round2 act=hold  streak=0 first_wall=None windowRemain=None elapsed=False gate_reason=None
round3 act=close streak=1 first_wall=1042516.0 windowRemain=3600.0 elapsed=False gate_reason=horizon_exit_unconfirmed(1/2)
```

### probe 2 · 反思对 `close` 的 fail-closed 与执行侧静默丢弃（§2.4 / §2.5）

```python
"""probe 2: (a) reflection fail-closed vetoes a close; (b) market close dropped on cancel-unknown."""
import inspect
from app.ai_auto.decider import TradeDecision, ReflectionVerdict
from app.ai_auto.reflection import apply_reflection

close = TradeDecision(action="close", size_fraction=None, confidence=0.9, reason="exit now")
for code in ("reflection_timeout", "reflection_llm_error", "reflection_no_tool", "reflection_invalid"):
    v = ReflectionVerdict("abort", None, None, code, code, ran=True)
    out, deg = apply_reflection(close, None, v, 0.0)
    print(f"[a] close + {code:22s} -> action={out.action!r} degrade={deg!r}")
v = ReflectionVerdict("abort", None, None, "too early", None, ran=True)
out, deg = apply_reflection(close, None, v, 0.0)
print(f"[a] close + real abort           -> action={out.action!r} degrade={deg!r}")

from app.live_auto import executor as ex
src = inspect.getsource(ex.LiveExecutor._prepare_resting_slot)
print("\n[b] _prepare_resting_slot returns 'False' (no new order) on unknown:",
      "return events + [self._status_event(" in src and "绝不双挂" in src)
src2 = inspect.getsource(ex.LiveExecutor._do_close)
print("[b] _do_close aborts silently when slot not free:",
      "if not slot_free:\n            return pre" in src2)
```

实测输出（2026-08-25）：

```
[a] close + reflection_timeout     -> action='hold' degrade='reflection_timeout'
[a] close + reflection_llm_error   -> action='hold' degrade='reflection_llm_error'
[a] close + reflection_no_tool     -> action='hold' degrade='reflection_no_tool'
[a] close + reflection_invalid     -> action='hold' degrade='reflection_invalid'
[a] close + real abort             -> action='hold' degrade='reflection_abort'

[b] _prepare_resting_slot returns 'False' (no new order) on unknown: True
[b] _do_close aborts silently when slot not free: True
```

---

## 附录 B：关键源码引用索引（实现者速查）

| 主题 | 位置 |
|---|---|
| 离场账本清零点 | `services/quant/app/ai_auto/horizon_runtime.py:197-220` |
| 三闸判定 | `services/quant/app/ai_auto/hold_horizon.py:728-782` |
| 豁免矩阵 | `services/quant/app/ai_auto/hold_horizon.py:44-56, 599-621` |
| 快照装配 | `services/quant/app/ai_auto/hold_horizon.py:649-712` |
| 决策轮管线 | `services/quant/app/ai_auto/session.py:1277-1372` |
| 持仓周期门禁薄封装 | `services/quant/app/ai_auto/session.py:1651-1670` |
| 反思编排 | `services/quant/app/ai_auto/session.py:1466-1490` |
| 反思落地纯核心 | `services/quant/app/ai_auto/reflection.py:16-55` |
| 决策工具 schema | `services/quant/app/ai_auto/prompts.py:17-26, 220-279` |
| `TradeDecision` | `services/quant/app/ai_auto/decider.py:110-126` |
| `commit_round` 注记合并 | `services/quant/app/ai_auto/decider.py:519-569` |
| 平仓执行 | `services/quant/app/live_auto/executor.py:329-356` |
| 挂单槽位三态 | `services/quant/app/live_auto/executor.py:377-430` |
| `close_position(autoCxl)` | `services/quant/app/okx/trade_client.py:204-230`、`services/quant/app/okx/trading.py:250-253` |
| 利润保护平仓（可复用的兑现范式） | `services/quant/app/ai_auto/session.py:1877-1960` |
| 盯市 tick | `services/quant/app/ai_auto/session.py:1034-1076` |
| 前端 trace 归约（逐键手抄区） | `apps/web/lib/store.ts:214-327` |
| 前端周期卡片 | `apps/web/components/Agent/AiTradeTraceRound.tsx:239-312` |
| 降级码中文化 | `apps/web/lib/aiTrade.ts:502-566` |
| 快照类型 | `apps/web/lib/aiTradeTypes.ts:564-604` |

---

## 评审结论（Review Verdict · 节点 #1）

### **有条件通过（Approved with Conditions）**

**总体判断**：**问题定性准确、取证扎实、修法方向正确，可以进入开发。**

v1 的 §二 取证部分是本文档最扎实的一节——F-1 到 F-6 六条缺陷经评审侧逐条重读源码后全部成立，
两条探针的输出可原样复现，`derive_min_hold_s=7200` / `derive_confirm_window_s=3600` 由评审侧独立
重算一致。**「离场这件事在整条决策链上没有优先级」这个定性抓住了用户问题的本质**：用户的仓位不是
被某一个参数卡死的，是被一个「计数只认连续轮、而连续性一犹豫就清零」的活锁卡死的；而 §4.2 的
带衰减账本 + 让 `first_exit_intent_wall` 活下来，是对这条活锁的**正修**——它甚至不需要模型学会任何
新字段就能在 1 小时内自行解开今天那笔走不掉的仓位。四条轴的切分（账本 / 优先级 / 兑现 / 可解释性）
各自独立可回滚，C-2 与 C-8 两条一键回退设计得很好。

**但 v1 不可直接进开发**：6 条 P0 与 10 条 P1 高度同形——**新开的离场通道绕过了它自己声称的约束**。
其中三条是资金安全级：

- **P0-1** 让「挂起待裁决」一次删掉 X-4 四个合取项中的三个，会话配额、证据要求、60 秒绝对地板
  全部可被复核官一句 `confirm` 抹掉，R-1 五条缓解里三条失效，G-10 这条防滥用门禁必然是假绿。
- **P0-2** 让「只帮平仓」的通道去解开了**开仓腿**的闸（`gated_kind` 对反手同样返回非 `None`），
  直接违反 X-1，而 G-5 的 AST 扫描守不住它。
- **P0-3** 让「帮你把仓平掉」的兑现层在 10 秒后**撤掉模型自己挂的限价平仓单**并改市价成交，
  把已合入的 limit-orders 特性在离场侧静默废掉。

另有两条属于「本仓库反复出事的形态」：P0-5 的六处镜像漏抄（其中 `experience.ts:93` 那条会让 X-9
在经验图侧根本无法查询）、P0-6 的打标槽没有生命周期（会把 override 之后的每一笔保护层平仓都错记成
最高优先级离场，且错误方向是系统性高估）。P0-4 与 P1-9 则是两处**照着写就跑不通**的落点错误。

**全部 6 条 P0 与 10 条 P1 已在正文原地修正**（锚点 `【R:编号】`），文档升版 **v2**，
**无未解决的 P0 / P1**。

### 放行条件（节点 #2 开发必须逐条兑现，节点 #3 逐条复核）

| # | 条件 | 复核判据 |
|---|---|---|
| **C-1** | 先做签名迁移再做别的（§十 第 0 步） | G-11 绿；全仓 `evaluate_horizon_gate(` 17 处调用点全部三元组解包 |
| **C-2** | 挂起只对 `ledger_short` 开启，裁决与 override 共用配额 | G-10 绿（**含「后 2 轮 `traded is False`」那半句**）；#31 的 ⑤ |
| **C-3** | 优先级作用域钉死在 `gated_kind == "close"` | G-5 的**②行为断言**绿（①单独不算数）；#29 的反手用例 |
| **C-4** | 轴 C 只对市价 close 武装，且有 `resting_close` 短路 | A-9 手工验收（§8.3 第 4 条，掐表 10/30/60 秒）；#30、#31 的 ⑥ |
| **C-5** | 六处枚举镜像 + 打标槽生命周期 | G-9 的**②双向覆盖断言**绿；#31 的 ⑦ |
| **C-6** | 行数硬承诺 | 节点 #3 实测四个文件行数并写进提交说明：`hold_horizon.py` < 800、`AiTradePanel.tsx` < 800、`AiTradeTraceRound.tsx` 净增 ≤4、`session.py` 净增 ≤60 |
| **C-7** | 放行不进 `degrade` | 凡本轮真的成交，`degrade is None`（A-3、#31 的 ④） |
| **C-8** | 基线先跑后写 | §8.1 G-8 的红条基线**必须由节点 #2 在动手前先跑一次**并写进 §十一，**不得引用本文档的任何数字** |
| **C-9** | 实施发现回填 | 凡本文档与代码现实冲突，一律以代码为准并在 §十一 逐条记 `D-*`（尤其 §7.1 #12 的回测双时钟一条） |

### 评审侧对「用户诉求是否被真正回答」的判断

用户问的是：「是否应该有对应的优先级？如果 Agent 多轮思考确定应该退场了，ReAct 多轮确认该退场的
时候，那么就应该最高优先级能够支持其平仓离场。」

v2 的回答是**应该，而且分四层给**：① 轴 A 让「多轮确认」这件事第一次真的被记住（今天它一犹豫就
清零）；② `expedite` 让成熟的离场理由跳过连续确认；③ `override` 让带证据的终局判断跳过全部三条闸；
④ `exit_pending` 与确认窗口两把墙钟钥匙保证**即使模型什么都不声明，锁也一定会到期**。
四层之外还有第五层——不经模型的【立即离场】按钮。

**这是一个好的答案，因为它没有把「优先级」做成一个开关。**评审侧唯一坚持的收紧是：优先级越高，
举证责任越重，且**每一次高优先级放行都必须留下能回头算账的凭据**（X-9）。v1 在这一点上写对了原则、
写错了实现（P0-1 让举证责任可以被绕过、P0-6 让凭据会记错人）——v2 修好之后，这条通道才是可信的。

---

**评审人签署**：Anthropic 工程团队 · 资深评审
**评审日期**：2026-08-25
**评审对象**：`docs/plans/ai-trade-exit-priority-and-commitment/spec.md` v1 → **v2**
**结论**：**有条件通过**，C-1…C-9 九条放行条件随开发一并交付。
