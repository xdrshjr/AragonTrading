# AI 自动交易 · 利润保护层（ai-trade-profit-protection）设计方案 v2

- 特性目录：`docs/plans/ai-trade-profit-protection/`
- 归属迭代：AgentMesh loop `tge-4ad08bb1` 第 1/3 轮 · Subtask #0（方案设计）→ **Subtask #1（方案评审）已完成，本文为评审后 v2**
- 上游诊断：`.bug-diagnosis/27942cda-31ea-4f31-8740-2c11783d9327/report.md`（结论「已确认」）
- 本节点产物：**仅本文件**。不写实现代码、不改 `docs/plans/ai-trade-profit-protection/` 之外的任何源文件、不 `git commit`。

---

## 〇、评审记录（Subtask #1，v1 → v2）

评审方式：逐节比对 v1 与仓库现状（`ai_auto/session.py` 1168 行、`live_auto/executor.py` 851 行、
`ai_auto/base_rules.py` / `manager.py` / `prompts.py` / `api/ai_auto.py` / `schemas.py` / `config.py`，
以及 `apps/web` 的 `AiTradeLaunchModal.tsx` / `AiTradeToggle.tsx` / `AiTradePanel.tsx` /
`store.ts` / `useAiTradeGroupStream.ts` / `types.ts` / `tradeExplain.ts`）。四个维度：可行性、完备性、
一致性、规模适配。

**总体判断**：分层落点（状态与判定全在 `ai_auto/`、`LiveExecutor` 只加只读转发）、平仓复用
`executor.apply` 唯一路径、fail-open 极性、回测显式非目标——这四条主干经代码核对全部成立，是本方案
最有价值的部分，v2 原样保留。缺陷集中在**状态机的生命周期边界**与**「显示 / 计数 / 文案」与事实的
一致性**上：一条 P0、九条 P1、八条 P2，全部已在 v2 正文就地修复。

### P0

| # | 位置 | 问题 | v2 修复 |
|---|---|---|---|
| **P0-1** | §2.3 epoch 生命周期 | epoch 边界只靠「`projected != prev_side`」采样判定，**保护层自己平仓后不清状态**。可达路径：monitor tick 内 `_maybe_profit_exit` 平掉多头 → 同一 tick 的 `_maybe_interval_wake` 起一轮决策 → 该轮 `open_long` 重新建仓 → `_post_round` 里 `_refresh_profit_state` 看到 `projected("long") == state.side("long")` ⇒ **沿用旧 epoch**，新仓继承上一仓的 `peak_px` 与 `opened_wall` ⇒ `giveback_ratio` 立刻 ≈100%、`min_hold_s` 也已过期 ⇒ **刚建的真实仓位当场被强平**。真实资金、可复现、v1 无任何防线 | §2.3 引入会话级单调 `_position_seq`（`_dispatch` 观察到任一 `side=="close"` 成交即 +1，覆盖保护平仓 / LLM 平仓 / 反手 / `external_close` / OCO 良性平仓五条路径），epoch 身份改为 `(side, seq)`；§3.1 增字段与 `_dispatch` 改动；§7.1 增 T-3 复现用例 |

### P1

| # | 位置 | 问题 | v2 修复 |
|---|---|---|---|
| **P1-1** | §2.5 伪码 | `executor.apply` 之后**无条件** `profit_guard_exits += 1` 并推送「已市价平仓锁定利润」。但 `_do_close` 有三条不平仓的返回路径：`_prepare_resting_slot` 撤单回 `"unknown"`/`"filled"` ⇒ `slot_free=False`（`executor.py:342`）、`_close_position` 抛 `OKXError` ⇒ 只回 error 事件、`projected` 已 flat ⇒ 回 `[]`。v1 会在没平仓时报「已平仓」并计数——假报，违反仓库「绝不假数据」硬约束，且直接使验收 #9 失真 | §2.5 伪码改为**以 `events` 中是否出现 `side=="close"` 的 `trade` 事件为唯一成交判据**；未成交则推「保护触发但平仓未成功」的 warn status、不计数、不写 `lastExit`；§7.3 验收 #9 与 #11 同步改写 |
| **P1-2** | §2.2 / §2.6 / §4.2 | 判定价与展示价双源。`_monitor_tick` 里 `_emit_position(mark)` 的 `mark` 来自 `_latest_mark_px()`＝**最近确认根收盘价**，`snapshot()` 用 `_ref_px()` 同理；若快照按各调用方传入的 `ref_px` 现算，UI / prompt 显示的 `givebackRatio`、`effectiveExitPx` 与保护层实际判定**不同源**，正是 `base_rules` [R-5]「UI 说合规、门禁判否决」的原样复发 | §2.2 增硬约束：`profitGuard` 快照的 `livePx` **一律取 `_current_px()`**，与调用方传入的 `ref_px` 无关；§3.1 明确 `_position_payload` 富化包装忽略 `mark_px` 参数用于保护段；§7.2 增 G-6 门禁 |
| **P1-3** | §2.6(b) / §4.2 | 样例数值内部不自洽，而 T-1 会照抄：① `entry=0.07245, peakPx=0.07290, leverage=5` ⇒ 峰值保证金收益只有 **+3.11%**，不是标注的 +5.20%；② `livePx=0.072600` **已低于**所示有效退出线 `0.072720`，即该断面本应已触发，却渲染成「未触发」；③ `breakevenPx=0.072595` 用的是 `entry×(1+0.002)`，与 §2.3 的 `entry×(1+offset/leverage)` 差 5 倍；④ `givebackRatio=0.404` 与 `peakReturnPct/returnPct` 反算出的 0.385 不符 | §2.6(b) / §4.2 换成一组**逐项自洽**的数值（entry 0.07245 / lev 5 / peak 0.073200 / live 0.073050），并在 §7.1 钉死 T-1 的构造式而非字面数字 |
| **P1-4** | §2.1 / §2.6(b) | 「交易所侧兜底 OCO 触发价」对**限价成交**失真：`_adopt_fill`（`executor.py:464`）把 `entry_price` 置为真实成交价 `avgPx`，而随单 OCO 在下单时是按 `order.px`（挂单价）附的（`executor.py:247/471`）。`risk_trigger_px(side, entry_price)` 返回的不是真实挂在交易所的那两个价 | §2.6(b) 改为**优先读最近一笔开仓 `trade` 的 `takeProfitPrice` / `stopLossPrice`**（`_make_trade` 已如实落这两键，是零改动的事实真源），只有取不到时才回落只读转发现算，并标注「现算」 |
| **P1-5** | §2.6(a) 宪章 | 保护层极性是 **fail-open**（价格 / 持仓读数不可用即不动作），但 v1 宪章只写「系统会…直接市价平仓」，未告知它**可能不可用**。这正是 R-2 道德风险的最短路径：模型据一条它以为无条件成立的兜底去延长持有 | §2.6(a) 定稿文案增一句「保护层在行情或持仓读数异常时会**主动不动作**（宁可不动也不据脏数据平掉真仓），因此它不是一条你可以依赖的保证」 |
| **P1-6** | §2.4 / §2.5 | 行情陈旧时保护层行为未定义。`_current_px()` 只在 WS 有推送时更新（`feed_candle`），断流后会**长期冻结在旧价**；15m 周期下 `_check_staleness` 的阈值是 `900 × live_auto_staleness_factor` 秒，期间保护层会持续拿冻结价做判定 | §2.4 前置豁免增第 6 条：`now_wall - _last_candle_wall > profit_guard_max_px_age_s`（默认 90s）或 `_stale_degraded` 为真 ⇒ 返回 `None` 并在快照里如实标 `status="unavailable"` + 原因；§4.3 增 `ai_trade_profit_guard_max_px_age_s`；§7.3 增验收 #12 |
| **P1-7** | §2.5 / §2.6(d) | `_pending_protect_note` 只写不清 ⇒ 每一轮 payload 都带 `lastExit` ⇒ 经 `commit_round` **反复**进 `_context`/`_journal`，模型会持续读到同一次早已过去的强平 | §2.6(d) 明确「**读取即消费**」：`_build_decision_payload` 取值后立即置 `None`（单轮一次性），并写明它与 `holdStreak` 的注入范式差异 |
| **P1-8** | §3.3 / §六.4 | 前端 `member.position` 在平仓后**不会被清空**：`_emit_position` 无仓时不发事件（`session.py:976`），`store.setAiTradeMemberPosition` 也没有复位路径（`store.ts:795`）。v1 让 `AiTradePanel` 直接读 `position.profitGuard`，空仓后会继续显示峰值浮盈与退出线——一个已经不存在的持仓的「保护状态」 | §3.3 / §六.4 增硬约束：保护行的渲染门控**必须**是 `session.positionSide !== "flat"` 且快照存在，二者同时成立；§3.3 前端测试项增「平仓后保护行消失」用例 |
| **P1-9** | §六.2 | 单仓 TP/SL 的「双口径」换算需要杠杆，但用户留空杠杆时后端回落 `settings.live_default_leverage`（3.0，`manager.py:211`），而前端**无任何途径**获知该值——`tradingApi.config` 只回 `instMeta.maxLever`，`autoTradeSettingsApi` 也不含。硬编码一个默认值即制造第二真源，与 R-10 自相矛盾 | §六.2 改为**诚实降级**：杠杆输入为空时只显示价格口径并附「按下单杠杆换算，填写杠杆后显示保证金口径」；`marginReturnFromPricePct` 的杠杆形参改为 `number \| null`，为 `null` 时返回 `null`，由渲染层省略该段 |

### P2（不阻塞，记录在案；v2 已一并就地澄清）

| # | 位置 | 问题 | v2 处置 |
|---|---|---|---|
| P2-1 | §4.1 vs §4.3 | 硬止盈「关闭」的编码不一致：请求体写 `null`，配置项写 `0` | §4.1 注明「`null` 与 `0` 均表关闭，解析点统一归一为 `0.0`」 |
| P2-2 | §2.7 vs §3.1 | `profitGuardArmedRounds` 在 §2.7 出现，§3.1 无对应会话字段与递增点 | §3.1 补 `profit_guard_armed_rounds` 字段；§2.7 写明递增时机（每轮装配 payload 时快照 `status != "idle"` 即 +1） |
| P2-3 | §3.1 / §4.1 | `schemas.AiTradeGroupRow` 未加 `profitGuard`（`baseRules` 有）。路由 `-> dict` 无 `response_model`，故不是功能缺陷，但契约文档失真 | §3.1 `schemas.py` 行补该字段 |
| P2-4 | §3.1 | `_resolve_base_rules` 被 `ai_trade_start` 与 `ai_backtest_start` **共用**（`api/ai_auto.py:164/265`）；新解析器若照抄接线会把保护层接进回测，与 §1.3 非目标冲突 | §3.1 明确「`_resolve_profit_guard` **只**接 `ai_trade_start`」 |
| P2-5 | §2.6(b) | 「有效退出线」只取 trail/breakeven 的 max（long）是**对的**（硬止盈是上方目标不是下方止损），但文案「价格触及即平仓」未标方向，硬止盈也未单列 | §2.6(b) 文案标明「向下触及」，硬止盈单列一行 |
| P2-6 | §2.6(c) | 「其余（含平仓、降级轮）→ 归零 / 不变」二义 | 改为逐分支枚举 |
| P2-7 | §3.3 | 弹窗回填所需的新 props 未点名 | §3.3 列出 `defaultStopLossPct` / `defaultTakeProfitPct` / `defaultProfitGuard` 三个新 prop |
| P2-8 | §2.5 | 保护平仓失败后每 5s 无限重试且每次推 status，无节流 | §2.5 增：失败重试保留（正确），但 status 每个 epoch 至多推一条（镜像 `_count_wake_skip` 的节流范式） |

### 经核对成立、v2 原样保留的关键判断（供 Subtask #2 免于重复论证）

- `_exec_lock` **不会**自死锁：`_execute_decision` 在返回前已释放锁，`_post_round` 在其之后执行。
- `_project_signals`（`executor.py:207-210`）对 `projected == "flat"` 的 `close` 直接跳过 ⇒ 保护先平后，在飞决策轮的 `close` 天然 no-op，不会重复下单。§2.5 该论证成立。
- `build_ai_trade_system_prompt` 的 `base_rules_text` 垫片范式（`prompts.py:61/80`）可原样复制；生产装配点 `manager.py:379-386`、回测装配点 `backtest.py:182-186` 各一处，回测不传新形参即天然零回归（G-3 成立），**无需改动 `decider.py`**。
- `PaperPositionEvent` 加可选键即可让 `useAiTradeGroupStream`（`case "position"` 整体转存，`useAiTradeGroupStream.ts:167`）零改动，v1 该结论成立。
- `TriggerExplainKind` 新增成员会**强制** `tradeExplain.ts` 的 `KIND_LABEL` / `KIND_TONE` 两个 `Record<TriggerExplainKind, …>` 同步补全（否则 `tsc` 失败）——这是好事，v1 已列入改动清单。
- `AiTradeLaunchModal` 的 `[open]` seed effect（`AiTradeLaunchModal.tsx:207-238`）确为 inputs-revert 的根治点，v1 的 R-8 硬约束措辞准确，原样保留。
- `_make_trade` 是显式字段映射、`repositories.append_live_trade` 同理，新增 trade 键不会落库也不会报错（§5.2 结论成立）。

---

## 一、概述

### 1.1 要解决的问题

AI 自动交易目前**能开仓、能平仓，但不会「保住利润」**。真实运行记录（DOGE run 1561）可直接复现：R74 在 0.07245 开多 → R78 浮盈达 +0.302 USDT → R79~R90 连续 `hold` → R88 浮盈缩到 +0.065 USDT 仍 `hold` → R91 浮盈翻负成 −0.108 USDT 才 `close`，且 R91 的模型理由原文写着「浮亏 −0.108 由 +0.194 翻负」。**模型看得见浮盈在回吐，系统却没有任何一条规则要求它把利润锁住。**

诊断报告定位的五条根因，本方案逐条对应：

| # | 根因 | 现状证据 | 本方案对策 |
|---|---|---|---|
| ① | 无移动止盈 / 浮盈回撤保护 | `live_auto/executor.py:157` 的 `position_payload` 只有 side/size/avgEntry/markPrice/unrealizedPnl/positionPct，全仓库无 trailing / breakeven / MFE | **F1** 新增确定性利润保护层（峰值价 + 移动止盈 + 保本 + 硬止盈），跑在 5s 盯市循环上，不依赖 LLM |
| ② | 默认单仓 OCO 太远 | `config.py:104-106` 默认 SL 5% / TP 10%（**标的价格口径**），5× 杠杆下 TP ≈ 保证金收益 +50% | **F1** 保护层阈值一律用**保证金收益率口径**；**F4** UI 把两种口径同时显性化 |
| ③ | 总止盈 / 总止损默认不启用、启动参数不记忆 | `AiTradeLaunchModal.tsx:225-230` 每次 open 都把四个风控输入清空；`AiTradeToggle.tsx:107-113` 只持久化策略 / 轮数 / 档位 / 唤醒周期 / 基础规则 | **F4** 把 `stopLossPct`/`takeProfitPct`/`profitGuard` 纳入 `aiTradeConfig` 持久化并回填 |
| ④ | 15m 未收盘时只有实时价更新，指标证据仍是上一根 | `session.py:446-455` 未确认根只写 `_last_live_px`；`prompts.py:335-336` 明确告诉模型「仅实时价微动不构成新证据」 | **F1** 保护层判定**只用实时价**（`_current_px()`），与 K 线确认完全解耦；**F2** 把实时价与保护快照一并喂给模型 |
| ⑤ | 现有「打破 hold 惯性」只针对不开仓 | `prompts.py:339-351` 的自察段要求模型指名「反对开仓的证据」 | **F3** 新增**盈利持仓自察段**：持仓且浮盈为正时，每轮必须给出「本轮退出线」与「为何允许利润继续回吐」 |

### 1.2 方案要旨

核心判断：**这不是执行故障，是设计缺口**（诊断已排除共识门禁、MA 通道门禁、反思层、`decision_to_signals` 映射，且 179 条既有测试全绿）。因此不去修任何既有链路，而是**加一层**——与【基础策略】铁律层（`ai_auto/base_rules.py`）完全同构的第二条铁律：

> **软引导（宪章 prompt + 每轮上下文）让模型主动管理退出；硬保证（确定性纯函数 + 5s 盯市循环）让利润无论如何都不会从峰值无限回吐。**

与基础策略层的一个关键差异必须先钉死：基础策略门禁的极性是 **fail-closed**（通道算不出 ⇒ 拒绝开仓，"无法验证合规" ≠ "合规"）；利润保护层的极性必须是 **fail-open**（价格 / 持仓读数不可用 ⇒ 本轮不动作，绝不据脏数据强平真实持仓，交易所侧 OCO 仍是最后兜底）。两者极性相反且都正确——因为一个在管「要不要新增敞口」，另一个在管「要不要动已存在的真实仓位」。

### 1.3 目标 / 非目标

**目标（本轮交付）**
- F1 执行侧确定性利润保护：峰值浮盈追踪 → 移动止盈 / 保本 / 可选硬止盈 → 触发即市价平仓。
- F2 把 `peakUnrealizedPnl`、回撤比例、持仓时长 / 根数、**有效退出线**、交易所侧兜底 OCO 触发价喂给 AI。
- F3 盈利持仓自察：持仓浮盈为正且连续观望时，prompt 要求模型给出退出线与继续持有的理由（措辞对称，不得变成强制平仓指令）。
- F4 UI：启动弹窗新增「利润保护」分区（默认开启）+ 单仓 TP/SL 的双口径显性化 + 参数持久化；运行期面板与轨迹展示保护状态。
- F5 可观测：保护触发落 `trade`（新 explain kind）+ `status` 事件 + `metrics.aiStats.profitGuardExits`。

**非目标（本轮明确不做，避免范围蔓延）**
- **AI 回测侧（`ai_auto/backtest.py`）不接入保护层。** 理由：回测按根推进，峰值浮盈的真值需要 bar 内 OHLC 级建模；用收盘价近似会**低估**触发频次，产出一份「比实盘保守、却看起来同构」的回测曲线，属于诊断报告最忌讳的假数据。本轮回测 payload 保持 **`profitGuard` 键缺席** ⇒ prompt 逐字节回退 ⇒ 回测既不模拟也不宣称有保护层。**验收要求：任何 UI/文案不得暗示回测结果已包含利润保护。** 建议第 2/3 轮以「bar 内极值 + 保守成交假设」单独立项。
- 不改 `ai_trade_default_sl_pct=0.05` / `ai_trade_default_tp_pct=0.10` 的**数值**。它们是进程崩溃 / 断网时唯一还生效的交易所侧兜底；静默收紧会让所有存量会话的止损前移、可能被正常波动扫损。本轮只做「显性化 + 可记忆」，改数值交给用户在 UI 上知情决定。
- 不引入新 DB 表 / 新列（零迁移，沿用 `strategy_runs.config_json` / `metrics_json` 的自由形状）。
- 不新增 WS 事件类型（保护状态搭 `position` 事件顺风车，触发搭 `trade` + `status`）。

---

## 二、技术设计

### 2.1 层次落点：为什么在 `AiTradeSession` 而不是 `LiveExecutor`

`LiveExecutor` 被 **live-auto（策略自动交易）与 ai-trade 共用**。若把峰值状态机塞进执行器，live-auto 的每一条路径都要重新做零回归论证（`executor.py` 的 R-1/O-1/O-2/O-3 是一串用血换来的时序不变式）。

本方案的落点：

- **状态与判定全部在 `ai_auto/`**：新纯核心模块 `ai_auto/profit_guard.py` + `AiTradeSession` 上的两个新字段（规则 + 每仓状态）。
- **`LiveExecutor` 的唯一改动是一个只读转发方法** `risk_trigger_px(side, fill)`（转调既有私有 `_risk_trigger_px`，`executor.py:709`），使「交易所侧 OCO 触发价」在 UI / prompt 与真实下单处**共用同一份公式**。零行为改动、零调用点变更，live-auto payload 逐字节相同。
- **平仓复用既有唯一路径**：`executor.apply([Signal(side="close", …)], ref_px=…, bar_index=…, ts=…)`，`order_plan=None` ⇒ 市价。绝不新写一条平仓路径（`_settle_close` 封装了「先算 pnl/pnlPct 再复位 entry」这个顺序敏感约束，另起炉灶必然重蹈覆辙）。挂单冲突由 `_do_close → _prepare_resting_slot` 既有逻辑处理（先撤后平）。

### 2.2 口径与真源（本方案最容易做错的地方，先钉死）

| 量 | 真源 | 理由 |
|---|---|---|
| **判定价** | `AiTradeSession._current_px()`（未确认根实时价，回落最近确认收盘价） | 诊断根因 ④：保护必须在 15m 收线之前生效。`_latest_mark_px()` / `_ref_px()` 返回的是**最近确认收盘价**，最坏可滞后一整根，绝不可用于保护判定 |
| **浮盈口径** | 本地派生 `qty_coin × (mark − entry)`（方向化），由判定价推出 | 交易所 `upl`（`executor.unrealized_usd`）只在 reconcile 成功时更新，且 `_on_reconcile_failure` 会**保留旧值**——降级期间保护会静默失灵。本地派生与判定价严格同步、纯函数可单测 |
| **收益率口径** | `returnPct = 方向化价格变动% × leverage`（≡ 浮盈 / 开仓占用保证金） | 诊断根因 ②：用户直觉是保证金收益率；标的价格 10% 在 5× 下是 +50%，阈值必须说人话 |
| **回撤比例** | `givebackRatio = (peakReturnPct − returnPct) / peakReturnPct` | 无量纲，两种口径下数值相同，天然不受杠杆影响 |
| **时钟** | 一律 `time.time()` 墙钟（`holdSeconds`） | 本仓库有 AI-trade 双时钟前科（成交 `ts` 与墙钟混用导致配对丢单，commit `5173d88`）。`barsInPosition` 另由 `self._last_bar_index − executor.entry_bar` 派生，**只用于展示**，绝不参与保护判定 |
| **交易所侧 OCO 触发价** | 最近一笔开仓 `trade` 的 `takeProfitPrice` / `stopLossPrice`；取不到才回落 `executor.risk_trigger_px(side, entry_price)` 现算并标注 | **[P1-4]** 限价成交路径下 `entry_price` 被置为真实成交价 `avgPx`（`executor.py:464`），而随单 OCO 是按**挂单价** `order.px` 附的（`executor.py:247/471`）——照 `entry_price` 现算会得到一个交易所侧根本不存在的价。`_make_trade` 已把下单时的真实触发价如实落进 trade（`executor.py:771`），那才是事实真源 |

> **[P1-2] 判定价单源硬约束（本节最容易被实现绕过的一条）**：`profitGuard` 快照的 `livePx` 与全部派生量
> **一律取 `AiTradeSession._current_px()`**，与调用方传给 `_emit_position(ref_px)` / `snapshot()` /
> `_build_decision_payload(ref_px)` 的价格**无关**。理由：`_monitor_tick` 传的 `mark` 来自
> `_latest_mark_px()`（`session.py:410-418`，取 `_window.candles[-1]["c"]` ＝**最近确认根收盘价**），
> `snapshot()` 传的是 `_ref_px()`（同源）。若快照按调用方的 `ref_px` 现算，UI 与 prompt 上的
> `givebackRatio` / `effectiveExitPx` 会与保护层真正用来判定的数字不同源——这正是 `base_rules` 的 [R-5]
> 教训（「UI 说合规、门禁判否决」）在本特性上的原样复发。**富化包装接受 `mark_px` 形参只是为了不改
> 既有调用点签名，保护段必须忽略它。**

> 已知偏差（须在 §7 风险与 UI 文案中如实交代）：本地浮盈**不含手续费与资金费**，故比交易所 `upl` 略乐观。保本线的 `breakevenOffsetReturnPct`（默认 0.002）正是为覆盖这段成本而设。`position.unrealizedPnl`（交易所 upl）保持不变、继续并列展示，两个数字都真实，口径不同——UI 必须标注，不得二选一冒充对方。

### 2.3 状态机：只存四个字段，其余全部现算

每个持仓周期（epoch）只持久化 **`(seq, side, opened_wall, peak_px)`**，其余（峰值收益、回撤、各条退出线）每次评估从 `(entry_px, leverage, qty_coin, mark_px, peak_px)` 现算。

这样设计的收益：`_adopt_partial` / `_adopt_fill` 会在部分成交时改写 `entry_price`（均价漂移），若把 `peak_return_pct` 存成状态就必须处理"基准变了、峰值要不要 rebase"这个必错的分支；而 **`peak_px` 是与 entry 无关的市场量**，均价变化后所有派生量自动一致，分支消失。

#### 2.3.1 epoch 身份必须是 `(side, seq)`，不能只靠 side 采样比对 —— [P0-1]

v1 的 epoch 边界只有「`projected != prev_side`」这一条采样判据。它有一条**真实资金上的可达失效路径**：

```
monitor tick T:
  _maybe_profit_exit  → 保护层平掉多头（executor 归 flat），但**没有任何人清掉 _profit_state**
  _maybe_interval_wake→ 起一轮决策任务（异步）
决策轮（T+2s，尚在同一个 5s tick 间隔内）:
  _execute_decision   → open_long 重新建仓
  _post_round         → _refresh_profit_state：projected("long") == state.side("long")
                        ⇒ 判为「沿用」⇒ 新仓继承上一仓的 peak_px 与 opened_wall
                        ⇒ giveback_ratio ≈ 100%（早已 armed）、min_hold_s 也早已过期
                        ⇒ _maybe_profit_exit 当场把这笔刚建的真实仓位强平
```

根因是「flat 断面必须被某次采样看见」这个**隐式前提**，而 5s 盯市 + 异步决策轮根本不保证它成立。

修复：会话上引入单调计数器 **`_position_seq`**，在 `_dispatch`（`session.py:187`，全部 executor 事件的
唯一收口）里做一次纯同步递增——

```python
# _dispatch 内，紧邻既有 trade 分支：
if event.get("type") == "trade" and (event.get("data") or {}).get("side") == "close":
    self._position_seq += 1     # 任一平仓成交 = 一个持仓周期终结
```

`side == "close"` 覆盖**全部五条**平仓路径且无一遗漏：`_settle_close`（保护平仓 / LLM 平仓 / 反手腿 /
限价平仓采纳）、`_settle_benign_close` → `_synthesize_external_close`（OCO 抢先成交）、
`_on_reconcile_ok` → `_synthesize_external_close`（外部干预）——三处都经 `_make_trade(ts, "close", …)`
产出（`executor.py:514/526/828`）。而**开仓部分成交**（`_adopt_partial` → `_adopt_fill`）发的是
`side in ("long","short")` 的 trade，`seq` 不动 ⇒ 同一 epoch 的 `peak_px` 正确保留，恰是 §2.3 想要的语义。

```
epoch 生命周期（在 _refresh_profit_state 内判定，纯同步、无 await）：
  executor.projected == "flat"                          → 丢弃状态，返回 None
  state is None
    或 state.side != projected                          → 新 epoch：seq=self._position_seq,
    或 state.seq  != self._position_seq                     opened_wall=now, peak_px=entry_px
  否则（side 与 seq 双双相同）                            → 沿用：peak_px = max/min(peak_px, mark_px)
```

> 三条判据缺一不可：`side` 变化捕获反手，`seq` 变化捕获「平了再同向开」，`state is None` 捕获首仓。
> 复现用例见 §7.1 T-3。

派生量（`profit_guard.py` 纯函数，`long` 示例，`short` 全对称）：

```
move_pct       = (mark - entry) / entry
return_pct     = move_pct * leverage
peak_move_pct  = (peak_px - entry) / entry            # ≥ 0（peak_px 初值 = entry 且单调）
peak_return_pct= peak_move_pct * leverage
giveback_ratio = (peak_return_pct - return_pct) / peak_return_pct   if peak_return_pct > 0 else 0.0
unrealized_usd = qty_coin * (mark - entry)            # qty_coin = entry_qty_sz * ct_val
peak_unrl_usd  = qty_coin * (peak_px - entry)

trail_stop_px  = entry + (peak_px - entry) * (1 - giveback_pct)          # 仅 armed 时有效，否则 None
breakeven_px   = entry * (1 + breakeven_offset_return_pct / leverage)    # 仅 breakeven_armed 时有效
hard_tp_px     = entry * (1 + hard_tp_return_pct / leverage)             # hard_tp>0 时有效
effective_exit_px = max(x for x in (trail_stop_px, breakeven_px) if x is not None)   # long 取 max，short 取 min
```

### 2.4 三条规则、优先级与豁免

```
evaluate_profit_guard(snapshot, rule) -> ProtectVerdict | None
```

按固定优先级短路，第一个命中即返回；**任一分支不成立一律返回 `None`（不动作）**。

0. **前置豁免（fail-open）**：`rule is None` / `not rule.enabled` / 无持仓 / `entry_px<=0` / `leverage<=0` / `mark_px` 非有限数 / `qty_coin<=0` → `None`。
0b. **[P1-6] 价格新鲜度豁免（fail-open，会话侧判定，快照标 `status="unavailable"` + `unavailableReason`）**：
   `session._stale_degraded` 为真，或 `now_wall - session._last_candle_wall > rule.max_px_age_s`（默认 90s）→ `None`。
   理由：`_current_px()` 只在 `feed_candle` 有推送时更新（`session.py:451/469`），WS 断流后它会**冻结在旧价**；
   而 `_check_staleness` 的阈值是 `BAR_SECONDS[bar] × live_auto_staleness_factor`（15m 周期即 900×factor 秒），
   期间保护层若照冻结价判定，等于用一个可能已与市场脱节数分钟的价格去平掉真实仓位——与本层 fail-open
   的极性直接冲突。此时兜底责任如实交还交易所侧 OCO，并在 prompt / UI 里**明说保护层当前不可用**
   （绝不显示一条算得出来但不作数的退出线）。该判据放在会话侧（纯函数 `evaluate_profit_guard` 拿不到
   `_last_candle_wall`），但 `unavailable` 状态必须进快照，由 §2.6(b) 如实渲染。
1. **最短持有期**：`now_wall - opened_wall < rule.min_hold_s`（默认 30s）→ `None`。防止开仓瞬间的买卖价差 / 一跳噪音把刚建的仓打掉。
2. **`profit_guard_hard_take_profit`**（默认关，`hard_tp_return_pct = 0`）：`return_pct >= hard_tp_return_pct` → 平仓。
3. **`profit_guard_trailing`**（默认开）：`peak_return_pct >= arm_return_pct`（默认 0.05 = 保证金收益 5%）即 **armed**；armed 后 `mark_px <= trail_stop_px` → 平仓（等价于 `giveback_ratio >= giveback_pct`，默认 0.4）。
4. **`profit_guard_breakeven`**（默认开）：`peak_return_pct >= breakeven_arm_return_pct`（默认 0.03）即 **breakeven_armed**；armed 后 `mark_px <= breakeven_px` → 平仓。这一条专治 DOGE R91 那种「一路回吐直到翻负才平」。

回放校验（诊断样本）：DOGE 峰值 +0.302 → 若保证金收益峰值 ≈ 5.2%（arm 5% 命中）→ 移动止盈线对应浮盈 ≈ +0.181；R79~R80 的 +0.194 尚未触发，此后价格继续回落，**在跌到 +0.181 的那一跳即市价平仓**，锁定约 +0.18 而不是最终成交的 −0.108。即使 arm 阈值未达（浮盈占比更小的情形），保本线（arm 3%）也会在浮盈归零前先行离场。**这条回放必须成为一条回归测试**（§7.1 T-1）。

**豁免矩阵**：保护层只会产生 `close`，**永远不会**阻止 / 改写模型的任何决策；它与共识门禁、MA 通道铁律、反思层**互不干涉**（后三者一律不拦 `close`，故不存在「保护要平、门禁不让」的死锁）。

### 2.5 触发路径与并发

新增会话方法 `_maybe_profit_exit(now_ts)`，插入两处（均为既有编排点，镜像 `_maybe_session_exit` 的姿态）：

- `_monitor_tick()`：`reconcile → …→ _check_staleness → **_maybe_profit_exit** → _maybe_session_exit → _maybe_interval_wake`（保护先于会话止盈：一次保护性平仓可能正好把会话累计盈亏推过 TP，随后 `_maybe_session_exit` 自然停会话）。
- `_post_round()`：`_emit_equity → _emit_position → **_maybe_profit_exit** → _maybe_session_exit`（刚开仓的轮次被 `min_hold_s` 天然短路）。

```
async def _maybe_profit_exit(now_ts):
    if self.profit_rule is None or self.state != "running": return
    px   = self._current_px()                        # 纯同步、实时价（§2.2 单源硬约束）
    snap = self._refresh_profit_state(px, now_ts)    # 纯同步：epoch/peak 推进 + 组装 wire；无仓 → None
    if snap is None or snap["status"] == "unavailable": return   # 含 §2.4 第 0b 条新鲜度豁免
    verdict = evaluate_profit_guard(snap, self.profit_rule)
    if verdict is None: return
    async with self._exec_lock:                      # P1-1：一切 executor 写路径串行
        if self.state != "running" or self.executor.projected == "flat":
            return                                   # 取锁期间局面已变（复检，非防御性噪音）
        signal = Signal(side="close", reason=verdict.reason_code)
        signal.explain = build_profit_guard_explain(...)   # 保护层专属 explain（§2.7）
        events  = await self.executor.apply([signal], ref_px=px,
                                            bar_index=self._last_bar_index, ts=now_ts)
        events += await self.executor.reconcile(px, now_ts)
    await self._dispatch(events)                     # ← 此处 _position_seq 随 close 成交自增（§2.3.1）
    await self._push_status_if_side_changed()

    # ---- [P1-1] 成交判据：只有真的出现平仓成交才算「触发成功」，绝不据「已调用 apply」报成功 ----
    closed = any(e.get("type") == "trade" and (e.get("data") or {}).get("side") == "close"
                 for e in events)
    if closed:
        self.profit_guard_exits += 1
        self._pending_protect_note = verdict.note    # 下一轮 payload 一次性带给模型（§2.6 d）
        await self._push({"type": "status", "data": {**self._status_payload(),
                          "message": f"利润保护触发（{verdict.label}）：已市价平仓锁定利润"}})
        return
    # 未成交：三条既有路径都会走到这里（见下），只在**本 epoch 首次**推一条 warn，避免 5s 刷屏
    if self._profit_exit_warned_seq != self._position_seq:
        self._profit_exit_warned_seq = self._position_seq
        await self._push({"type": "status", "data": {**self._status_payload(),
                          "message": f"利润保护已触发（{verdict.label}）但本次平仓未成交，"
                                     "将在下一次盯市重试（交易所侧止盈止损仍生效）"}})
```

**[P1-1] 为什么必须验成交**：`_do_close` 有三条**不平仓**的返回路径，v1 的无条件计数会在这三条路径上
报出一条不存在的平仓——

| 路径 | 触发条件 | `apply` 返回 |
|---|---|---|
| `_prepare_resting_slot` 撤单回 `"unknown"` | 撤单请求未获交易所确认（`executor.py:342`） | 只有 status 事件，`slot_free=False`，**不平仓** |
| `_prepare_resting_slot` 撤单回 `"filled"` | 撤单撞成交 51402（`executor.py:339`） | 已采纳该成交，**本轮不再下新单** |
| `_close_position` 抛 `OKXError` | 网络 / 交易所侧拒绝（`executor.py:294`） | 只有 error 事件 |

外加 `projected == "flat"` 时 `_do_close` 直接回 `[]`（`executor.py:277`）。仓库对「绝不假报」有硬约束
（`stop()` 的 `residual`、`_settle_benign_close`、`flatten` 的有界重试全是这条约束的产物），保护层不能
成为唯一的例外。**失败即保留状态、下一次盯市自然重试**——这是正确行为，只是不许对外宣称成功。

并发正确性论证：
- `_exec_lock` 已串行化「决策轮 apply / 会话止盈平仓 / stop flatten / reconcile 写回」，保护性平仓加入同一把锁即安全。**已核对不存在自死锁**：`_execute_decision` 在返回前释放锁，`_post_round`（进而 `_maybe_profit_exit`）在其之后执行。
- 取锁后**必须复检** `projected != "flat"`：LLM 决策轮可能在等锁期间已经平仓。复检失败即静默返回（局面已达成目的）。
- 若一轮 LLM 决策正在飞、且其结论是 `close`：保护先平 → `_project_signals` 见 flat ⇒ 该轮 close 自然成为 no-op，不会重复下单（`executor.py:207-210` 已核对）。
- 若其结论是 `open_*`：会在保护平仓后重新建仓。这是**有意行为**（保护层不是禁止交易的闸），但两件事必须同时成立：① `_position_seq` 已在 `_dispatch` 里自增 ⇒ 新仓拿到全新 epoch（§2.3.1 的 P0-1 修复，否则新仓开出来就会被强平）；② `_pending_protect_note` 在下一轮 payload 中如实呈现「上一次持仓因利润保护被强制平仓」，使模型知情。
- **刻意不加"保护后冷却期"**：那会变成一个新的开仓门禁，与既有三层门禁语义重叠、且默认值无从论证。列为 §7 待观测项。

### 2.6 喂给 AI 的上下文（F2 / F3）

三段全部**条件渲染**，规则未装配 ⇒ `payload["profitGuard"]` **整键缺席** ⇒ prompt 逐字节回退 ⇒ 既有冻结断言与回测 payload 零影响（`base_rules` 同款范式，`prompts.py:354-364`）。

**(a) 宪章段**（system prompt，`format_profit_guard_charter(rule)`，插在 base_rules 宪章之后、决策纪律之前）

措辞必须防「道德风险」：若写成「系统会保护你的利润」，模型会更敢于无限期持有。定稿方向：

> 本会话启用了**利润保护层（移动止盈 + 保本）**：持仓浮盈达到阈值后系统会记录峰值，并在利润回吐超过设定比例、或价格跌回保本线时**由系统直接市价平仓**。它是**兜底底线，不是你的止盈策略**——被保护层强制平仓意味着你没有在更好的位置主动离场，应视为一次决策失误。主动的 `close` 永远优先于等待保护层触发。
> **另须知悉：保护层在行情或持仓读数异常时会主动不动作**（宁可不动，也绝不据脏数据平掉一笔真实持仓），届时上下文会明确标注「保护层当前不可用」。因此它**不是一条你可以依赖的保证**，你的退出计划必须在没有它的前提下也成立。

> **[P1-5] 为什么必须加第二段**：本层极性是 fail-open（§1.2、§2.4 第 0/0b 条），即"算不出来就不动"。若宪章
> 只宣布"系统会平仓"，模型会据一条它以为无条件成立的兜底去延长持有——这恰是 R-2 道德风险的最短路径，
> 且比"没有保护层"更糟（没有时它至少知道自己是唯一防线）。**评审已逐字复核本段，Subtask #2 若改写措辞
> 须保留这两层语义：① 被强平 = 决策失误；② 它可能不可用、不可依赖。**

**(b) 每轮持仓上下文段**（`format_profit_guard_for_prompt(snapshot)`，紧跟 `_format_position` 之后）

```
【利润保护 · 移动止盈 / 保本（系统强制执行）】
  持仓 930s（4 根）｜实时价 0.073050｜当前浮盈 +0.242 USDT（保证金收益 +4.14%）
  峰值浮盈 +0.302 USDT（保证金收益 +5.18%，峰值价 0.073200）→ 已回吐 20.0%
  移动止盈线 0.072900（已武装）｜保本线 0.072479（已武装）｜硬止盈：未启用
  ★ 有效退出线 0.072900：价格**向下触及**即由系统市价平仓，你无需等待
  交易所侧兜底：止盈 0.079695 / 止损 0.068828（≈ 保证金收益 +50% / −25%）
```

> **[P1-3] 本组数值逐项自洽，Subtask #2 可直接用作渲染断言的基准**（`entry=0.07245`、`leverage=5`、
> `qty_coin≈402.7`、`giveback_pct=0.40`、`breakeven_offset_return_pct=0.002`）：
> `peak_move=(0.073200−0.07245)/0.07245=1.0352%` → `peak_return=5.18%`；
> `move=(0.073050−0.07245)/0.07245=0.8282%` → `return=4.14%`；
> `giveback=1−0.00060/0.00075=20.0%`；
> `trail=0.07245+0.00075×0.60=0.072900`；
> `breakeven=0.07245×(1+0.002/5)=0.072479`（**注意除以杠杆**，§2.3 公式）；
> `exchange_tp=0.07245×1.10=0.079695`、`exchange_sl=0.07245×0.95=0.068828`。
> v1 的样例四处互相矛盾（详见 §〇 P1-3），已作废；**任何新增样例都必须先过这套反算**。

无持仓 / 未武装时如实降级（「尚未达到武装阈值（需保证金收益 ≥ 5%）」），**绝不假数据**。
`status == "unavailable"` 时（§2.4 第 0b 条）整段替换为一行如实告知，**不渲染任何退出线**：

```
【利润保护 · 当前不可用】行情数据陈旧（最近确认根已 214s 未更新），保护层本轮不做任何判定；
  仅交易所侧止盈止损仍生效（止盈 0.079695 / 止损 0.068828）。请据此自行管理退出。
```

**(c) 盈利持仓自察段**（`format_profit_hold_note(snapshot, rounds)`，仅当 `returnPct > 0` 且 `holdWhileProfitRounds >= ai_trade_profit_guard_note_after_rounds` 时注入）

沿用 `_format_hold_streak`（`prompts.py:339-351`）踩过的坑：**措辞必须对称**，既打断「盈利也无脑持有」，也不得暗示「必须马上平仓」。

> 盈利持仓自察：你已连续 N 轮在浮盈状态下选择继续持有。若本轮仍选择持有，请在研判中明确给出：① **你的退出线**（具体价位或条件），② 为什么此刻继续承担利润回吐风险优于兑现。这不是必须平仓的指令；但「浮盈还在」不构成继续持有的理由。

计数器 `self._profit_hold_streak`：镜像 `_update_hold_streak`（`session.py:388`）的语义，以**第一阶段**决策
（`decider.decide` 直接返回值，共识门禁 / 反思之前）为准。**[P2-6] 逐分支枚举，不留二义**：

| 第一阶段结果 | 持仓浮盈 | `_profit_hold_streak` |
|---|---|---|
| `hold`，无降级 | `returnPct > 0` | **+1** |
| `hold`，无降级 | `returnPct <= 0` 或无持仓 | **归零** |
| `hold`，有降级（`llm_error` / `timeout` / `low_confidence` 等） | 任意 | **不变**（既非真实观望也非交易意愿，与 `_update_hold_streak` 的 R2-03 语义钉一致） |
| 非 `hold`（`open_*` / `close` / `cancel_order`） | 任意 | **归零** |

另：保护层强制平仓也**归零**（该仓已不存在，连续持有链条终结）——落点在 `_maybe_profit_exit` 的成交分支内。

**(d) 保护性平仓回执**：`_pending_protect_note` 在下一轮 `_build_decision_payload` 中作为
`profitGuard.lastExit` 注入并被 `build_decision_prompt` 渲染一行，随 `commit_round` 自然进入 `_context`
与 `_journal`（诊断报告要求的「让模型知道它的仓位为何消失」）。

> **[P1-7] 读取即消费（单轮一次性）**：`_build_decision_payload` 取到 `_pending_protect_note` 后**必须
> 立即置回 `None`**。它与 `holdStreak` 的注入范式**不同**——`holdStreak` 是一个每轮重算的状态量，重复
> 出现是对的；`lastExit` 是一次性事件回执，若只写不清会每一轮都注入同一条早已过去的强平，并经
> `commit_round` 反复写进 `_context`/`_journal`，既污染上下文窗口又会让模型误以为反复被强平。
> 用例见 §7.3 验收 #13。

### 2.7 事件、解释与观测（F5）

- **成交解释**：新增纯构造器 `build_profit_guard_explain(...)`（`live_auto/explain.py`，加法，不动既有 5 个构造器），`kind="profit_guard"`，metrics = 峰值浮盈 / 已回吐% / 有效退出线 / 入场价 / 平仓价 / 本笔盈亏。前端 `tradeExplain.ts` 同步登记 `KIND_LABEL["profit_guard"]="利润保护"`、`KIND_TONE="warn"`，并把三个 reason 码加入 `inferKindFromReason`（老成交无 explain 时也能正确归类）。
- **WS**：不新增事件类型。保护快照搭 `position` 事件（`data.profitGuard`）；触发时发 `trade` + `status`（`message` 已被前端渲染）。旧前端忽略未知键，向前兼容。
- **轨迹**：`new_round_trace` 增 `"profitGuard": payload.get("profitGuard")`（`trace.py:93`），`AiTradeTraceRound` 渲染为与「基础策略」并列的一段。**不新增 trace phase**——基础策略之所以每轮一条 `base_rule` 事件，是因为它是逐轮门禁；利润保护跑在 5s 盯市上，不是轮级判定。
- **落库**：`_finalize_run` 的 `aiStats` 加两个加法键，沿用既有 try/except 富化包（`session.py:1050`），失败只 warning、绝不阻断平仓后的落库收尾：
  - `profitGuardExits` ← `self.profit_guard_exits`，语义 = **实际产生了平仓成交**的保护触发次数（[P1-1]：`apply` 调用次数不是它）；
  - `profitGuardArmedRounds` ← `self.profit_guard_armed_rounds`。**[P2-2] 递增点必须写死**：在 `_build_decision_payload` 装配 `profitGuard` 快照时，若 `snapshot["status"] in ("armed", "breakeven_armed")` 则 +1（**轮级**口径，与 `profitGuardExits` 的**事件级**口径刻意不同，二者不可互相换算，UI 文案须分别标注）。

### 2.8 装配与默认开启（三层语义，镜像 `resolve_ma_band_rule`）

`resolve_profit_guard_rule(raw, settings) -> ProfitGuardRule | None`（`profit_guard.py`，**唯一解析点**）：

1. 运维总闸 `settings.ai_trade_profit_guard_enabled = False` ⇒ 恒 `None`（逐字回退到本特性合入前）。
2. 请求字段缺省 ⇒ 按 settings 装配**开启态**（老客户端 / 桌面旧包不发该字段也默认开启）。
3. 显式 `{"profitGuard": {"enabled": false}}` ⇒ `None`（该会话不装配）。

`AiTradeSession.__init__` 的新形参 `profit_rule: ProfitGuardRule | None = None` **默认 None** —— 既有直接构造会话的单测行为逐字不变（与 `base_rule` 完全同款零回归垫片）。生产装配由 `manager` 从 API 层解析结果显式传入。

### 2.9 时序图（一次典型的移动止盈触发）

数值沿用 §2.6(b) 的自洽基准（`entry=0.07245`、`leverage=5`、`peak_px=0.073200`、`giveback_pct=0.40`
⇒ `trail_stop_px=0.072900`）：价格自峰值回落到 0.072900 的那一跳即触发。

```
OKX WS ──未确认根──> feed_candle: _last_live_px = 0.072900       (亚秒级)
monitor_loop (5s)
  └─ _monitor_tick
       ├─ executor.reconcile(...)                 [_exec_lock]  持仓真源校正 / upl 更新
       ├─ _emit_equity / _emit_position   ← position.data.profitGuard（快照搭车，livePx 取 _current_px()）
       ├─ _check_staleness
       ├─ _maybe_profit_exit
       │    ├─ px = _current_px()                 = 0.072900（实时，不等 15m 收线）
       │    ├─ 新鲜度校验                          now − _last_candle_wall ≤ 90s ⇒ 继续（P1-6）
       │    ├─ _refresh_profit_state              (side,seq) 未变 ⇒ 沿用；peak_px=0.073200
       │    │                                     giveback = 1 − 0/0.00075 = 100% ≥ 40%
       │    │                                     （mark 恰落在 trail_stop_px 上，等价判据）
       │    ├─ evaluate_profit_guard              → ProtectVerdict(profit_guard_trailing)
       │    └─ [_exec_lock] 复检 projected≠flat
       │         executor.apply([close], ref_px=0.072900, order_plan=None) → 市价平仓
       │         → trade 事件（side="close", explain.kind=profit_guard）
       ├─ _dispatch(events)                       ← 观察到 close 成交 ⇒ _position_seq += 1（P0-1）
       ├─ 成交判据 closed=True                     ⇒ profit_guard_exits += 1 + status 事件（P1-1）
       └─ _maybe_session_exit → _maybe_interval_wake
下一决策轮
  └─ _build_decision_payload: profitGuard.lastExit = {...} 并**立即置 None**（读取即消费，P1-7）
     → prompt 如实告知「上一仓已被保护层平掉」；若本轮又开同向仓，因 _position_seq 已变 ⇒ 全新 epoch
```

---

## 三、文件 / 模块变更计划

### 3.1 后端（`services/quant`）

| 文件 | 动作 | 意图（一句话） |
|---|---|---|
| `app/ai_auto/profit_guard.py` | **新建** | 利润保护纯核心：`ProfitGuardRule` / `ProfitGuardState` / `ProfitGuardSnapshot` / `ProtectVerdict` + `resolve_profit_guard_rule` / `advance_state` / `build_snapshot` / `evaluate_profit_guard` / `format_profit_guard_charter` / `format_profit_guard_for_prompt` / `format_profit_hold_note`（算状态、判触发、渲文案三件事的唯一真源） |
| `app/config.py` | 修改 | 追加 9 个 `ai_trade_profit_guard_*` 配置项（总闸 + 5 个阈值 + 最短持有 + **价格新鲜度上限[P1-6]** + 自察轮阈），全部带 `Field` 边界 |
| `app/ai_auto/session.py` | 修改 | 新增 `profit_rule` 形参（默认 None）；新增字段 `_profit_state` / `_profit_hold_streak` / `profit_guard_exits` / `profit_guard_armed_rounds`[P2-2] / `_pending_protect_note` / **`_position_seq`[P0-1]** / `_profit_exit_warned_seq`[P2-8]；新增 `_refresh_profit_state`、`_maybe_profit_exit`、`_position_payload` 富化包装（**保护段忽略 `mark_px` 形参、一律用 `_current_px()`**[P1-2]）；**`_dispatch` 内对 `side=="close"` 的 trade 自增 `_position_seq`**[P0-1]；在 `_monitor_tick` / `_post_round` 挂点；payload 注入 `profitGuard` 并**消费** `_pending_protect_note`[P1-7]；`_update_profit_hold_streak`；`_finalize_run` 加两个 aiStats 键；`snapshot()`/`_emit_position` 改走富化包装 |
| `app/ai_auto/prompts.py` | 修改 | `build_ai_trade_system_prompt` 增末位 `profit_guard_text: str = ""` 垫片（空串 ⇒ 渲染逐字节不变）；`build_decision_prompt` 条件插入保护段与自察段（键缺席即整段省略） |
| `app/ai_auto/trace.py` | 修改 | `new_round_trace` 增 `"profitGuard": payload.get("profitGuard")` 一个键 |
| `app/ai_auto/manager.py` | 修改 | `AiTradeGroupStartRequest` 增 `profit_rule` 字段（默认 None）；`_build_session` 透传；`_create_group_run` 的 `config` 落 `profitGuard` 回执镜像（零列迁移）；`group_status()` 回执增 `profitGuard` |
| `app/api/ai_auto.py` | 修改 | 新增 `_resolve_profit_guard(body.profitGuard, settings)` 单点解析（镜像 `_resolve_base_rules`），接进 `AiTradeGroupStartRequest`。**[P2-4] 只接 `ai_trade_start`（第 164 行一带），绝不接 `ai_backtest_start`（第 265 行）**——`_resolve_base_rules` 是两处共用的，照抄接线会把保护层接进回测，与 §1.3 非目标直接冲突 |
| `app/schemas.py` | 修改 | 新增 `AiTradeProfitGuardIn`（7 字段带边界）；`AiTradeGroupStartIn` 增 `profitGuard: AiTradeProfitGuardIn \| None = None`；**[P2-3] `AiTradeGroupRow` 增 `profitGuard: dict \| None = None`**（与既有 `baseRules: dict \| None` 对称。路由签名是 `-> dict` 无 `response_model`，故不加也不会丢字段，但契约文档必须与回执一致） |
| `app/live_auto/executor.py` | 修改（**加法 2 行**） | 新增只读转发 `risk_trigger_px(side, fill)` → `self._risk_trigger_px(...)`（无行为改动，live-auto 逐字节不变）。**[P1-4] 它只是回落路径**：交易所侧 OCO 触发价的**首选真源是最近一笔开仓 `trade` 的 `takeProfitPrice`/`stopLossPrice`**——限价成交时 `entry_price` 被置为 `avgPx` 而 OCO 是按挂单价 `order.px` 附的，照 `entry_price` 现算会得到一个交易所侧不存在的价 |
| `app/live_auto/explain.py` | 修改（加法） | 新增 `build_profit_guard_explain(...)`（`kind="profit_guard"`），既有 5 个构造器一字不改 |

### 3.2 后端测试（`services/quant/tests`）

| 文件 | 动作 | 意图 |
|---|---|---|
| `tests/test_ai_trade_profit_guard.py` | **新建** | 纯核心全覆盖：三层解析语义、epoch 推进与 peak 单调、多空对称、三条规则优先级、min_hold、fail-open 分支、退出线反解、文案渲染、**DOGE R74→R91 回放（T-1）** |
| `tests/test_ai_trade_session.py` | 修改 | 会话级：`profit_rule=None` 零回归（payload 无键 / 无新事件）、触发路径经 `_exec_lock`、取锁后复检 flat、`_post_round` 与 `_monitor_tick` 双挂点、`profitGuardExits` 落 metrics、`_pending_protect_note` 进下一轮 payload |
| `tests/test_ai_trade_prompts.py` | 修改 | 键缺席时决策 prompt / 系统 prompt **逐字节冻结**；键存在时三段渲染断言；自察段的对称措辞断言 |
| `tests/test_ai_trade_api.py` | 修改 | `profitGuard` 缺省 ⇒ 默认开启；`{"enabled":false}` ⇒ 不装配；总闸关 ⇒ 恒不装配；越界入参 422 |
| `tests/test_ai_trade_group.py` | 修改 | `group_status()` / run `config_json` 的 `profitGuard` 回执镜像 |
| `tests/test_live_executor.py` | 修改 | `risk_trigger_px` 公开转发与 `_risk_trigger_px` 同值；**live-auto 全链路零回归**（既有用例不改一行仍全绿） |
| `tests/test_live_auto_explain.py` | 修改 | `build_profit_guard_explain` 形状 / 数值 / 不含任何凭证 |

### 3.3 前端（`apps/web`）

| 文件 | 动作 | 意图 |
|---|---|---|
| `lib/types.ts` | 修改 | 新增 `ProfitGuardSnapshot` 接口（**必须定义在此处**：`aiTradeTypes.ts` 已 import 本文件，反向 import 会成环）；`PaperPositionEvent` 增 `profitGuard?: ProfitGuardSnapshot \| null`；`TriggerExplainKind` 增 `"profit_guard"` |
| `lib/aiTradeTypes.ts` | 修改 | 新增 `AiTradeProfitGuardRule`；`AiTradeGroupStartReq` / `AiTradeGroupRow` 增 `profitGuard?`；`AiTradeConfig` 增 `profitGuard?` / `stopLossPct?` / `takeProfitPct?`（全可选 ⇒ **不动 persist version**，沿用 baseRules 先例）；`AiTradeRoundTrace` 增 `profitGuard?`；re-export `ProfitGuardSnapshot` |
| `lib/aiTrade.ts` | 修改 | `AI_TRADE_PROFIT_GUARD_DEFAULTS`；`validateProfitGuard`；`describeProfitGuardStatus`（含 `"unavailable"` 态[P1-6]） / `describeProfitGuardReason`；**`marginReturnFromPricePct(pct: number \| null, leverage: number \| null): number \| null`**（单仓 TP/SL 双口径换算的唯一真源；**[P1-9] 杠杆为 `null` 时必须返回 `null`**，见下）；`describeDegradeReason` 补三个保护码 |
| `lib/aiTrade.test.ts` | 修改 | 上述纯件的正常 / 边界 / 异常路径 |
| `lib/tradeExplain.ts` | 修改 | `KIND_LABEL.profit_guard="利润保护"`、`KIND_TONE="warn"`、`inferKindFromReason` 认三个保护 reason 码 |
| `lib/tradeExplain.test.ts` | 修改 | 新 kind 的标签 / 色调 / legacy 回落推断 |
| `components/Agent/AiTradeLaunchModal.tsx` | 修改 | 新增「利润保护」分区（开关 + 武装阈值 + 回吐比例 + 保本阈值 + 最短持有）；单仓 TP/SL 的 placeholder 与摘要 Badge 改为**双口径**（`SL 5%（≈保证金 −25%）/ TP 10%（≈保证金 +50%）`）；`onConfirm` 透传 `profitGuard`。**新 state 必须只在 `[open]` 依赖的既有 seed effect（`AiTradeLaunchModal.tsx:207-238`）内初始化**——该 effect 的依赖已收敛为 `[open]`，是 inputs-revert 复发缺陷的根治点，新增字段若另写 effect 或加依赖会立刻复发 |
| `components/Agent/AiTradeLaunchModal.test.tsx` | 修改 | 分区渲染 / 校验 / 提交体形状 / **「输入后 2s 不回退」回归用例** |
| `components/Agent/AiTradeToggle.tsx` | 修改 | 启动请求带 `profitGuard`；`setAiTradeConfig` 持久化 `profitGuard`/`stopLossPct`/`takeProfitPct`（取自 `opts`，不取回执——回执不回显这三键，`setAiTradeConfig` 又是整对象替换，取回执会让偏好每次重开都回落默认，[R-4] 先例）；**[P2-7]** 向弹窗传三个新 prop：`defaultStopLossPct` / `defaultTakeProfitPct`（`aiTradeConfig?.*`，`?? undefined`）与 `defaultProfitGuard`（`aiTradeConfig?.profitGuard ?? AI_TRADE_PROFIT_GUARD_DEFAULTS`） |
| `components/Agent/AiTradeToggle.test.tsx` | 修改 | 持久化 → 回填闭环（诊断根因 ③ 的回归防线） |
| `components/Agent/AiTradeTraceRound.tsx` | 修改 | 新增「利润保护」段（与既有基础策略段并列；快照缺失 ⇒ 整段不渲染） |
| `components/Chart/AiTradePanel.tsx` | 修改 | 持仓时展示：峰值浮盈 / 已回吐% / **有效退出线** / 武装状态徽标。**[P1-8] 渲染门控必须是 `session.positionSide !== "flat"` 且 `position?.profitGuard` 存在，二者同时成立**——`member.position` 在平仓后不会被清空（`_emit_position` 无仓时不发事件、`store.setAiTradeMemberPosition` 无复位路径），只判快照存在会让空仓后继续显示一个已不存在持仓的峰值与退出线 |
| `components/Chart/AiTradePanel.test.tsx` | 修改 | 三态渲染：无快照 / 有快照且持仓 / **有快照但 `positionSide === "flat"`（保护行必须消失，P1-8 回归防线）** |

> `lib/aiTradeApi.ts`、`lib/useAiTradeGroupStream.ts`、`lib/store.ts` **无需改动**（请求体透传 `AiTradeGroupStartReq`；`position` 事件整体转存；`AiTradeConfig` 新增键均为可选）。

---

## 四、接口设计

### 4.1 `POST /api/ai-trade/start` — 加法字段

```jsonc
{
  "members": [{"instId": "DOGE-USDT-SWAP", "allocationPct": 100}],
  "bar": "15m", "strategyIds": [1,2,3], "leverage": 5,
  "stopLossPct": 0.05, "takeProfitPct": 0.10,
  "baseRules": { "maBandEntry": { "enabled": true } },

  // ↓ 本特性新增；整个字段省略 ⇒ 服务端默认**开启**（老客户端零改动即受保护）
  "profitGuard": {
    "enabled": true,
    "armReturnPct": 0.05,                 // (0, 5]   武装移动止盈的峰值保证金收益率
    "givebackPct": 0.40,                  // (0, 1]   自峰值允许回吐的比例
    "breakevenArmReturnPct": 0.03,        // [0, 5]   0 = 关闭保本
    "breakevenOffsetReturnPct": 0.002,    // [0, 1]   保本线相对入场的正向偏移（覆盖费用）
    "minHoldSeconds": 30,                 // [0, 3600]
    "hardTakeProfitReturnPct": null       // [0, 20]；**null 与 0 等价，均表关闭**（P2-1）
  }
}
```

> **[P2-1] 硬止盈的「关闭」有两种编码**：请求体允许 `null`（字段省略 / 显式 null），配置项与内存
> `ProfitGuardRule.hard_tp_return_pct` 只认 `0.0`。`resolve_profit_guard_rule` 是**唯一归一点**：
> `None → settings 默认（0.0）`，`0 → 0.0`，二者此后不可再分辨。`maxPriceAgeSeconds` 同理走 settings 回填。

校验分层沿用既有惯例：**边界在 `schemas.py` 的 `Field` 上（越界 422），API 层只做「缺省回填」不复夹**（同 `_resolve_position_step` 的 `[R-2]`）。

回执（`group_status()` / `GET /ai-trade/status`）镜像同一形状：`"profitGuard": {...} | null`（`null` = 未装配）。

### 4.2 WebSocket `/api/ws/ai-trade/{sessionId}` — 加法负载

`position` 事件 `data` 增（无持仓时无该事件；未装配规则时该键为 `null`）：

```jsonc
"profitGuard": {
  "ruleId": "profit_trailing", "status": "idle|armed|breakeven_armed|unavailable",
  "unavailableReason": null,  // status=="unavailable" 时必填："stale_feed" | "no_price" | "bad_position"
  "side": "long", "entryPx": 0.07245, "livePx": 0.073050,
  "holdSeconds": 930, "barsInPosition": 4,
  "returnPct": 0.0414, "priceMovePct": 0.008282, "unrealizedPnlUsd": 0.242,
  "peakPx": 0.073200, "peakReturnPct": 0.0518, "peakUnrealizedPnlUsd": 0.302,
  "givebackRatio": 0.200,
  "trailStopPx": 0.072900, "breakevenPx": 0.072479, "hardTakeProfitPx": null,
  "effectiveExitPx": 0.072900,
  "exchangeTakeProfitPx": 0.079695, "exchangeStopLossPx": 0.068828,
  "exchangeOcoSource": "trade",   // "trade"=取自开仓成交回执（首选真源）｜"derived"=只读转发现算（P1-4）
  "armReturnPct": 0.05, "givebackPct": 0.40, "minHoldSeconds": 30,
  "lastExit": null            // 或 {"ts":…, "reasonCode":"profit_guard_trailing", "pnlUsd":0.181}
}
```

> **[P1-3] 上面这组数字与 §2.6(b) 同源、逐项自洽**（`leverage=5`、`qty_coin≈402.7`、
> `giveback_pct=0.40`、`breakeven_offset_return_pct=0.002`）。v1 的样例四处互相矛盾且 `livePx` 已在
> 有效退出线之下却标为未触发，已作废。
> **`livePx` 恒为 `_current_px()`**（§2.2 单源硬约束），不是 `position.markPrice`——后者由调用方传入的
> `ref_px` 决定，可能是上一根确认收盘价，二者在 15m 周期上最坏差一整根。

保护触发时额外发：`{"type":"trade", …, "reason":"profit_guard_trailing", "explain":{"kind":"profit_guard", …}}` 与一条 `status`（`message` 含中文原因）。`agent_trace` 的 `round_start` 增 `profitGuard` 同形状快照。

### 4.3 配置（`config.py`，env 前缀沿用既有约定）

| 键 | 默认 | 边界 | 语义 |
|---|---|---|---|
| `ai_trade_profit_guard_enabled` | `True` | — | 运维总闸；`False` 逐字回退到本特性合入前 |
| `ai_trade_profit_guard_arm_return_pct` | `0.05` | `gt=0, le=5` | 武装移动止盈的峰值保证金收益率 |
| `ai_trade_profit_guard_giveback_pct` | `0.40` | `gt=0, le=1` | 自峰值允许回吐比例 |
| `ai_trade_profit_guard_breakeven_arm_return_pct` | `0.03` | `ge=0, le=5` | `0` = 关闭保本 |
| `ai_trade_profit_guard_breakeven_offset_return_pct` | `0.002` | `ge=0, le=1` | 保本线正向偏移（覆盖手续费） |
| `ai_trade_profit_guard_min_hold_s` | `30.0` | `ge=0, le=3600` | 最短持有期（墙钟秒） |
| `ai_trade_profit_guard_hard_tp_return_pct` | `0.0` | `ge=0, le=20` | `0` = 关闭硬止盈（请求体的 `null` 在解析点归一为此值，P2-1） |
| `ai_trade_profit_guard_max_px_age_s` | `90.0` | `gt=0, le=3600` | **[P1-6]** 判定价最大允许年龄（`now − session._last_candle_wall`）。超龄或 `_stale_degraded` ⇒ 本轮 `status="unavailable"` 且**零动作**。默认 90s 的取值理由：`live_auto_poll_interval_s=5`，正常 WS 每秒级推未确认根，90s 已是 18 个盯市周期无任何行情，足以判定断流；同时远小于 15m 周期的既有 staleness 阈值（900×factor 秒），使保护层在 `_check_staleness` 反应之前就先行停手 |
| `ai_trade_profit_guard_note_after_rounds` | `1` | `ge=0` | 盈利持仓自察注记阈值（`0` = 关闭） |

---

## 五、数据模型

### 5.1 内存形状（`profit_guard.py`）

```python
@dataclass(frozen=True)
class ProfitGuardRule:                 # 会话级不可变配置
    enabled: bool = True
    arm_return_pct: float = 0.05
    giveback_pct: float = 0.40
    breakeven_arm_return_pct: float = 0.03
    breakeven_offset_return_pct: float = 0.002
    min_hold_s: float = 30.0
    hard_tp_return_pct: float = 0.0    # 0 = 关闭（请求体 null 在解析点归一至此，P2-1）
    max_px_age_s: float = 90.0         # P1-6：判定价最大允许年龄，超龄 ⇒ unavailable ⇒ 零动作

@dataclass
class ProfitGuardState:                # 每持仓周期，会话内唯一实例（无仓时为 None）
    seq: int                           # P0-1：建仓时的 session._position_seq 快照，epoch 身份的一半
    side: str                          # long | short，epoch 身份的另一半
    opened_wall: float                 # time.time()，单一时钟
    peak_px: float                     # 方向化最优价（long=max, short=min），初值 = entry_px

@dataclass(frozen=True)
class ProtectVerdict:
    reason_code: str                   # profit_guard_trailing | _breakeven | _hard_take_profit
    label: str                         # 中文短标签（status 事件 / explain headline 共用）
    note: dict                         # 落 lastExit 与下一轮 prompt 的结构化注记
```

`ProfitGuardSnapshot` 直接返回 §4.2 的 camelCase `dict`（与 `build_ma_band` 同款「纯函数产 wire dict」姿态，避免 dataclass ↔ wire 的第二次映射）。

### 5.2 持久化：零迁移

- `strategy_runs.config_json` 追加 `"profitGuard": {...}`（自由形状，`_create_group_run` 已在写 `baseRules`，同一处加一键）。
- `strategy_runs.metrics_json.aiStats` 追加 `profitGuardExits` / `profitGuardArmedRounds`（加法键，历史行缺键 ⇒ 前端 `?? 0`）。
- `live_trades` **无新增列**：保护性平仓复用既有 `_make_trade`，`reason` 取保护码、`explain` 取新 kind。注意 `repositories.append_live_trade` 是**显式字段映射**（无 dict splat），新增的 trade 键不会落库也不会报错——与 `pnlPct` 先例一致。
- 前端 `aiTradeConfig` 新增三个**可选**键 ⇒ **不 bump persist version**（沿用 baseRules 先例，避免动 migration 链）。

---

## 六、渲染与文案（F4 细则）

1. **启动弹窗新分区「利润保护（推荐开启）」**，位置在「基础策略」之后、真实资金勾选之前：
   - 开关（默认开）；关闭时其余输入禁用且提交体为 `{"enabled": false}`（**显式关闭**，与「字段缺省=默认开启」严格区分）。
   - 四个数值输入：武装阈值（保证金收益 %）、回吐比例（%）、保本阈值（保证金收益 %，0=关闭）、最短持有（秒）。
   - 一行**实时预览**（纯前端派生，无请求）：`5× 杠杆下：浮盈达 5%（≈标的 1.0%）武装 → 回吐 40% 或 跌回保本即平仓`。
2. **单仓 TP/SL 双口径**：placeholder 由「留空＝默认」改为「留空＝默认 10%（≈保证金 +50%）」；既有摘要 Badge（`AiTradeLaunchModal.tsx:766/782`）同步显示两种口径。换算走 `marginReturnFromPricePct` 单点真源。
   > **[P1-9] 杠杆留空时必须诚实降级，不得猜。** 用户留空杠杆时后端回落 `settings.live_default_leverage`
   > （`manager.py:211`，当前 3.0），而前端**没有任何途径**读到它：`tradingApi.config` 只回
   > `instMeta.maxLever`，`autoTradeSettingsApi` 也不含该项。在前端硬编码一个默认杠杆即制造第二真源，
   > 与 R-10 的「单点真源」自相矛盾，且一旦运维改了 `LIVE_DEFAULT_LEVERAGE`，UI 会**静默**显示错误的
   > 保证金收益率——正是本特性要根治的那类信任崩塌。
   > 定稿行为：`marginReturnFromPricePct(pct, leverage)` 在 `leverage == null` 时返回 `null`；渲染层据此
   > 只显示价格口径，并附一行「保证金口径按下单杠杆换算，填写杠杆后显示」。同一规则适用于第 1 条的
   > 实时预览行（未填杠杆时预览只写「浮盈达 5%（保证金口径）武装 → 回吐 40% 或跌回保本即平仓」，
   > 不给标的涨跌幅换算）。
3. **持久化闭环**：`AiTradeToggle` 把 `stopLossPct`/`takeProfitPct`/`profitGuard` 写入 `aiTradeConfig`，弹窗下次 open 回填——直接消灭诊断根因 ③ 的「每次都是空 ⇒ 悄悄用 10%/5%」。
4. **运行期展示**：`AiTradePanel` 持仓行下方一行「峰值 +0.302｜已回吐 20.0%｜退出线 0.072900（已武装）」；`AiTradeTraceRound` 新增同数据的一段。
   > **[P1-8] 渲染门控** = `session.positionSide !== "flat"` **且** `position?.profitGuard` 存在。
   > `member.position` 平仓后不会被清空（后端 `_emit_position` 无仓时不发事件，前端
   > `store.setAiTradeMemberPosition` 也无复位路径），只判快照存在会在空仓后继续渲染一个已不存在持仓的
   > 峰值与退出线。`status === "unavailable"` 时渲染为一枚 warn 徽标 +「保护层暂不可用」，**不显示退出线**。
5. **诚实约束**：所有浮盈数字必须标注口径（本地派生 vs 交易所 upl）；交易所侧 OCO 价若来自现算而非成交回执（`exchangeOcoSource === "derived"`）须标「估算」；回测模式下**不得**显示任何利润保护 UI（§1.3 非目标）。

---

## 七、测试与验收标准

### 7.1 必须先写的复现测试（CLAUDE.md 硬规矩：修 bug 先写能稳定复现的用例）

- **T-1「DOGE 回放」**（`tests/test_ai_trade_profit_guard.py`）：构造 `entry=0.07245, side=long, leverage=5, qty_coin≈402.7`（使峰值浮盈 = +0.302 USDT ⇔ 峰值保证金收益 ≈ 5.18%，达 arm 5%），按 R74→R78→R80→R88→R91 的价格序列逐点喂 `advance_state` + `evaluate_profit_guard`。
  - **[P1-3] 断言必须写成构造式，不得照抄任何字面价格**：触发点 = `entry + (peak_px − entry) × (1 − giveback_pct)`，用例先算出该值再断言「第一个 ≤ 它的价格点返回 `profit_guard_trailing`」。v1 §2.6(b)/§4.2 的样例数字四处互相矛盾（详见 §〇 P1-3），照抄即把矛盾固化进测试。
  - **修复前**：不存在该模块 ⇒ 用例导入失败（红）。
  - **修复后**：断言在浮盈仍为正的那一跳返回 `profit_guard_trailing`，且此后的负浮盈点位**永不被到达**（序列在触发处终止）。
- **T-2「翻负前保本」**：峰值只到 3.2%（未达 arm 5%）但过了保本阈值 3%，价格回落穿越保本线 ⇒ 返回 `profit_guard_breakeven`，结算浮盈 ≥ 0。
- **T-3「[P0-1] 平仓后同向立即重开必须是新 epoch」**（`tests/test_ai_trade_session.py`，会话级）：
  桩 executor 走完「建多头 → 保护层触发平仓（`_position_seq` +1）→ **同一 monitor 间隔内**再开同向多头」，
  断言 ①新仓的 `profitGuard.holdSeconds` 从 0 起算（`opened_wall` 已重置）、②`peakPx == 新仓 entryPx`
  （旧峰值未被继承）、③紧接着的 `_maybe_profit_exit` **不产生任何平仓事件**（`min_hold_s` 重新生效）。
  修复前该用例必红（新仓开出即被强平），是 P0-1 的唯一护栏。
- **T-4「[P1-1] 平仓未成交不得计数、不得报成功」**：桩 `close_position_fn` 抛 `OKXError`（及 `cancel_order_fn`
  回 `outcome="unknown"` 的第二个变体），断言 `profit_guard_exits` 保持 0、`_pending_protect_note` 仍为 `None`、
  推出的 status **不含**「已市价平仓」字样，且下一次 `_maybe_profit_exit` 会重试。
- **T-5「[P1-6] 行情陈旧即零动作」**：把 `_last_candle_wall` 回拨到超过 `max_px_age_s`，断言在一个本应触发
  移动止盈的价位上 `_maybe_profit_exit` 零事件、快照 `status == "unavailable"` 且 `unavailableReason == "stale_feed"`、
  prompt 段渲染为「保护层当前不可用」且**不含任何退出线数字**。
- **T-6「[P1-7] lastExit 读取即消费」**：一次保护平仓后连续装配两轮 payload，断言第一轮含 `profitGuard.lastExit`、
  第二轮该键为 `None`。

### 7.2 零回归门禁（硬性，任一不过即视为未完成）

| 门禁 | 判据 |
|---|---|
| G-1 live-auto 逐字节不变 | `test_live_executor.py` / `test_live_executor_limit.py` / `test_live_auto_*.py` **一行不改**全绿；`executor.py` 的 diff 只含新增的只读转发方法 |
| G-2 prompt 冻结 | `profit_rule=None` 时 `build_ai_trade_system_prompt` / `build_decision_prompt` 输出与合入前**逐字节相同**（沿用既有冻结断言，必要时补 sha256 门禁） |
| G-3 回测 payload 冻结 | `ai_auto/backtest.py` 零改动；其 `_build_payload` 产物不含 `profitGuard` 键 |
| G-4 既有会话单测零改 | 直接构造 `AiTradeSession(...)` 的既有用例（不传 `profit_rule`）全部原样通过 |
| G-5 全量回归 | `pytest -q`（`services/quant`）全绿；`npx tsc --noEmit` + `vitest`（`apps/web`）全绿。注意 `npm run lint` 在 `apps/web` **非功能性**（未装 ESLint），类型门禁走 `tsc --noEmit`。**工作树已有他人在制的未提交改动**，全量必红的既有用例须先取基线再比对，不得把他人红项算作本特性回归 |
| G-6 判定价单源 | **[P1-2]** 用不同的 `mark_px` 调 `_position_payload(mark_px=X)` 与 `_position_payload(mark_px=Y)`（`X ≠ Y ≠ _current_px()`），断言两次返回的 `profitGuard` 段**逐键相等**且 `livePx == _current_px()`；同一断面下 `evaluate_profit_guard` 的判定与快照 `effectiveExitPx` 严格一致 |

### 7.3 功能验收标准

1. 持仓浮盈达武装阈值后回吐超阈 ⇒ **在浮盈仍为正时**产生 `close` 成交，`reason` 为三个保护码之一，`explain.kind == "profit_guard"`。
2. 保护判定**不等待 K 线确认**：在 15m 周期上、两根 K 线之间注入实时价即可触发（用例直接调 `feed_candle(confirm=False)` + `_monitor_tick`）。
3. `min_hold_s` 内的任何价格波动**不会**触发保护。
4. 无持仓 / 规则未装配 / 价格非有限数 / 杠杆非正 ⇒ **零动作、零事件**（fail-open）。
5. 保护触发与 LLM 决策轮并发时不产生重复平仓、不产生 `LIVE_WOULD_REDUCE_POSITION` 类错误（取锁复检覆盖）。
6. 每轮 prompt 在持仓时包含峰值浮盈、回吐比例、持仓时长与**有效退出线**四项（诊断建议 #2 的验收原文）。
7. 持仓浮盈为正且连续观望 ⇒ prompt 出现自察段；措辞不含任何「必须平仓」的指令性表述。
8. 启动弹窗填入的 TP/SL 与利润保护参数，在下一次打开弹窗时**被原值回填**；单仓 TP/SL 同时显示价格口径与保证金口径。
9. 会话停止后 `metrics_json.aiStats.profitGuardExits` 与**实际产生了平仓成交**的保护触发次数一致（[P1-1]：`apply` 调用次数不算）；`profitGuardArmedRounds` 与快照曾处于 `armed`/`breakeven_armed` 的轮数一致（轮级口径，与前者不可换算）。
10. 关闭总闸（`AI_TRADE_PROFIT_GUARD_ENABLED=false`）后，系统行为与本特性合入前**不可区分**（事件流 / prompt / 回执逐字节比对）。
11. **[P1-1]** 平仓请求失败（`OKXError` / 撤单未确认 / 挂单撞成交）时：不计数、不写 `lastExit`、status 文案**不得**出现「已平仓」，且每个持仓周期至多推一条该 warn；下一次盯市自动重试。
12. **[P1-6]** 行情陈旧或判定价超龄时：保护层零动作、零事件，快照 `status == "unavailable"`，prompt 明确告知不可用且不渲染任何退出线。
13. **[P1-7]** 一次保护平仓只在**紧接的下一轮** prompt 出现一次 `lastExit`，再下一轮不再出现。
14. **[P0-1]** 保护平仓后同向立即重开的新仓，`holdSeconds` 从 0 起算、`peakPx` 等于新仓入场价，且在 `min_hold_s` 内不会被保护层平掉。

---

## 八、风险与缓解

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R-1 | **过早止盈**：阈值偏紧导致在趋势行情中被反复扫出，收益反而下降 | 中高 | 阈值全部可配（会话级 + 运维级）；默认 arm 5% / giveback 40% 是「让出峰值的四成」而非「一动就跑」；保本线只在已有 3% 浮盈后才武装；`min_hold_s` 抑制入场噪音；上线后以 `profitGuardExits` + 逐笔 `pnl` 复盘调参 |
| R-2 | **宪章诱发道德风险**：模型看到「系统会保护我」后更敢无限期持有 | 高 | 宪章措辞定稿为「保护层是兜底底线，不是你的止盈策略；被强制平仓 = 你的决策失误」；并由 F3 自察段每轮要求主动退出线。**评审须逐字复核这段文案** |
| R-3 | **本地浮盈口径乐观**（不含手续费 / 资金费）导致保本线实际略亏 | 中 | `breakeven_offset_return_pct` 默认 0.002 覆盖双边费用；UI/prompt 标注口径；两个浮盈数字并列展示，不互相冒充 |
| R-4 | **实时价异常**（脏 tick / 闪针）触发误平 | 中 | fail-open 前置校验（有限数 / 正数 / `entry>0`）；`min_hold_s`；判定价只取 `_current_px()`（来自 OKX WS 未确认根收盘，非逐笔），本身已是聚合值；不引入更激进的逐笔源 |
| R-5 | **保护平仓后模型立刻反手重开**，形成来回摩擦 | 中 | `lastExit` 注记进下一轮 prompt 使模型知情；MA 通道铁律仍约束开仓位置；**刻意不加冷却门禁**（会与既有三层门禁语义重叠），列为上线后观测项，必要时以配置项形式补 |
| R-6 | **并发**：保护平仓与在飞决策轮竞态 | 高（真实资金） | 全程 `_exec_lock`；取锁后复检 `state`/`projected`；平仓复用 `executor.apply` 唯一路径（自带 `_project_signals` 幂等、`LIVE_NO_POSITION` 良性归 flat、挂单先撤后平） |
| R-7 | **回测 / 实盘不对称**：用户拿回测调参却发现实盘行为不同 | 中 | §1.3 显式列为非目标并写明理由；UI 在回测模式隐藏保护分区；spec 要求后续迭代补齐。**不得**让回测悄悄"看起来像"有保护 |
| R-8 | 新增受控输入触发 `AiTradeLaunchModal` 的 inputs-revert 复发缺陷 | 中 | 硬约束：新 state 只能在既有 `[open]` 依赖的 seed effect 内初始化，禁止新增 effect / 新增依赖；补一条「输入后 2s 不回退」回归用例 |
| R-9 | 交易所侧 OCO 与保护层的**双重平仓** | 低 | 二者阈值量级差一个数量级（OCO ≈ 保证金 ±50%/−25%，保护 ≈ ±5%），实践中保护恒先触发；即便 OCO 先成交，`_settle_benign_close`（`LIVE_NO_POSITION`）与 `_on_reconcile_ok` 的 `external_close` 合成已覆盖 |
| R-10 | 9 个新配置项 + 一组新 wire 字段带来的表面积 | 低 | 全部走「单点解析 + 单点渲染 + 单点判定」三真源约束；文案 / 判据 / UI 共用 `profit_guard.py`，杜绝「UI 显示已保护、实际未触发」的信任崩塌（`base_rules` 的 [R-5] 教训） |
| R-11 | **[P0-1] 持仓周期身份判错** ⇒ 新仓继承旧峰值被当场强平 | 高（真实资金） | epoch 身份改为 `(side, seq)`，`seq` 由 `_dispatch` 观察到的 `close` 成交驱动（§2.3.1），不依赖「flat 断面恰好被采样到」这个不成立的隐式前提；T-3 为专项回归护栏 |
| R-12 | **[P1-6] 判定价冻结**（WS 断流）⇒ 拿脱节的旧价平掉真实仓位 | 中高 | `max_px_age_s`（默认 90s）+ `_stale_degraded` 双判据 ⇒ `status="unavailable"` + 零动作；不可用状态如实进 prompt/UI，兜底责任明确交还交易所侧 OCO；T-5 为回归护栏 |
| R-13 | **[P1-1] 假报平仓** ⇒ 用户以为利润已锁定、实际仓位还在裸奔 | 高 | 以 `close` 成交事件为唯一成交判据；未成交只推 warn 且每周期一条；`profitGuardExits` 语义收紧为「实际成交次数」；T-4 为回归护栏 |

---

## 九、实施顺序（供 Subtask #2）

1. `profit_guard.py` 纯核心 + `test_ai_trade_profit_guard.py`（**先写 T-1/T-2 复现测试**，红 → 绿）。
2. `config.py` 9 项 + `schemas.py` + `api/ai_auto.py` 单点解析（**只接 start，不接 backtest**，P2-4）+ `manager.py` 透传（含 API 层用例）。
3. `session.py` 接线（`_position_seq` / 状态推进 / 触发 / payload / streak / metrics）+ `executor.py` 只读转发 + `explain.py` 构造器；**先写 T-3/T-4/T-5/T-6 再接线**（它们分别对应 P0-1 / P1-1 / P1-6 / P1-7，是本轮四条最容易在实现时被绕过的约束）；跑 G-1/G-4/G-6。
4. `prompts.py` 三段（含 `unavailable` 降级段）+ `trace.py` 一键；跑 G-2/G-3 冻结门禁。
5. 前端类型 → 纯件（含 `marginReturnFromPricePct` 的 `null` 杠杆分支，P1-9）→ 弹窗 / Toggle → 面板 / 轨迹（含 P1-8 的 flat 门控用例）；跑 `tsc --noEmit` + `vitest`。
6. 全量回归 G-5，回填 §11。

> Subtask #2 不 `git commit`（由 Subtask #3 提交）。工作树当前**已有他人在制的未提交改动**（`services/quant/app/config.py`、`app/api/live_auto.py`、`app/engine/factors/*`、`desktop/*`、`.env.example` 等），其中 **`config.py` 是与本特性重叠的混合文件**——提交时必须按 hunk 行级暂存，不得整文件 `git add`。

---

## 十、评审记录（Subtask #1，已完成）

见文首 **〇、评审记录**：1 条 P0、9 条 P1、8 条 P2，全部已在正文就地修复；结论见 §十二。

---

## 十一、实施过程发现的方案缺陷（Subtask #2 回填）

### D-1【阻断级】[P1-6] 的新鲜度判据用 `_last_candle_wall` 会把整个特性废掉

- **方案原文**（§2.4 第 0b 条 / §4.3）：`now_wall − session._last_candle_wall > max_px_age_s`（默认 90s）
  ⇒ `status="unavailable"` + 零动作，并称 90s「远小于 15m 周期的既有 staleness 阈值（900×factor 秒）」。
- **实际代码**：`_last_candle_wall` **只在确认根落地时**更新（`session.py:459`，`feed_candle` 的
  `confirm=True` 分支）。15m 周期上它每根才刷新一次 ⇒ 每根 K 线的头 90 秒之外，保护层恒为
  `unavailable`、恒零动作——**覆盖率约 10%**，而未确认根期间正是本特性唯一要生效的窗口（诊断根因 ④）。
  设计文档在同一段里既引用了这个字段、又论证了「远小于 15m 阈值」，两者不可兼得。
- **修正**：新增会话字段 **`_last_px_wall`**，在**每一次 `_last_live_px` 被写入时**刷新（`feed_candle`
  的未确认根分支与确认根分支各一处，`start()` 给初值），语义是「判定价的真实年龄」——与
  `_current_px()` 严格同源，这才是 §2.4 第 0b 条真正想判的量。`_last_candle_wall` 保持原样零改动
  （仍归 `_check_staleness` 专用）。默认 90s 与其余取值理由不变。
- **护栏**：`test_ai_trade_session.py::test_profit_guard_stale_price_takes_no_action`（T-5）回拨的是
  `_last_px_wall`；`test_profit_guard_triggers_between_confirmed_candles`（验收 #2）反向证明未确认根
  推送后保护层仍可用。

### D-2 快照新增 `status="flat"` 与 `pxAgeSeconds` 两个键

- §4.2 的 `status` 枚举只有 `idle|armed|breakeven_armed|unavailable`，但 [P1-7] 的 `lastExit` 必须在
  **保护平仓之后**的那一轮送达模型——彼时 `projected` 已 flat，`_refresh_profit_state` 返回 `None`，
  按原枚举无处承载回执 ⇒ 模型永远不知道仓位为何消失，P1-7 与 §2.6(d) 自相矛盾。
- **修正**：`build_idle_snapshot()` 产出一份 `status="flat"` 的空壳快照，**只承载 `lastExit`**；
  `format_profit_guard_for_prompt` 对该态只渲染回执行，`evaluate_profit_guard` 对该态恒返回 `None`，
  前端 `describeProfitGuardStatus` 对该态返回 `null`（整行不渲染）。
- `pxAgeSeconds` 为加法只读键，使 §2.6(b) 的不可用文案能如实写出「判定价已 214s 未更新」而不必让
  渲染层二次估算。

### D-3 决策 payload 新增 `profitHoldRounds` 键（自察段的注入开关）

§2.6(c) 的自察段渲染需要「连续轮数」与「阈值判定」，而阈值是 settings、`prompts.py` 拿不到。沿用
`holdStreak` 的既有范式：**由会话侧判定阈值后才注入** `payload["profitHoldRounds"]`，`prompts` 只做
「有键即渲染、无键即整段省略」。该键只存在于决策 payload，不进 §4.2 的 WS wire。

### D-4 `lib/aiTrade.ts` 补一个与 `marginReturnFromPricePct` 成对的反向换算

§六.1 的实时预览行需要「保证金 5% ≈ 标的 1.0%」这一**反向**换算（§六.2 的 TP/SL 双口径是正向）。
若在组件内就地除以杠杆即产生第二真源，故在同一模块补 `priceMoveFromMarginPct(pct, leverage)`，
并逐字沿用 [P1-9] 的 null 纪律（杠杆未知 ⇒ 返回 `null` ⇒ 预览只写保证金口径）。

### D-5 `AiTradeLaunchModal` 的 `Field` 补 `aria-label`（与两条既有用例的选择器更新）

§六.2 要求把单仓 TP/SL 的 placeholder 由「留空＝默认」改为具体数值 + 保证金口径，这使两条既有用例
（`blocks confirm on an invalid per-position stop-loss pct` / `clamps an out-of-range per-position pct`）
按 `getAllByPlaceholderText("留空＝默认")[2]` 取元素的选择器失效。同时 [P1-9] 要求在 SL 字段下挂一句
hint，而 `Field` 的 `<label>` 同时包着 hint 文本 ⇒ 由包裹关系解析出的可访问名被 hint 污染。
**修正**：`Field` 的 `<input>` 显式声明 `aria-label={label}`（读屏与测试同时受益），两条既有用例的
选择器改为按标签取；断言随双口径文案同步更新。这是**文案有意变更导致的用例更新**，非行为回归。

### D-6 `session.py` 已远超 CLAUDE.md 的 800 行红线（**未在本轮处理**，留待立项）

接线后 `ai_auto/session.py` 为 **1398 行**（本特性接入前已 1168 行，本轮 +230）。CLAUDE.md 的硬阈值是
800 行/文件。本轮**刻意不拆**：§3.1 明确把状态与判定的落点定在 `session.py`，而拆分会同时触碰
`_monitor_tick` / `_post_round` / `_dispatch` 三处时序敏感编排点，与「保持 diff 聚焦、不顺手重构」
直接冲突，且拆分本身需要独立的零回归论证。建议按职责单独立项（候选切面：盯市循环 / 决策轮编排 /
事件扇出与落库 / 风控层挂点）。新增的纯核心 `profit_guard.py` 为 535 行，在阈值内。

### D-7 实施顺序偏差（如实记录）

§九 要求「先写 T-1/T-2 复现测试（红 → 绿）」。实际先落了 `profit_guard.py` 纯核心再写测试，故 T-1/T-2
未经历「因模块不存在而导入失败」的红态。补偿：T-1/T-2 的断言**全部写成构造式**（由
`entry/peak/giveback/leverage` 反算触发点，见 §7.1 [P1-3] 的硬要求），不含任何照抄自文档的字面价格，
故不存在「照着实现写断言」的自证风险；T-3~T-6 与 G-6 均为会话级用例，覆盖的四条约束
（P0-1 / P1-1 / P1-6 / P1-7）在实现中各有独立落点，用例与实现非同一处代码。

---

## 十一之二、终审发现并就地修复的缺陷（Subtask #3 回填）

终审逐条比对 §三 变更计划与实际 diff（30 源/测试文件 + 本文），覆盖 / 命名 / 风格三维度全部满足，
10 条放行条件逐条核到落点。另发现两条**方案与实现都未覆盖**的缺陷，已在源文件就地修复并补回归护栏：

### RV-1【诚实性】保护层停手时宣称了一条并不存在的兜底

- **现象**：`status="unavailable"` 的三处渲染（prompt 的 `format_profit_guard_for_prompt`、
  `AiTradePanel` 的保护行、`AiTradeTraceRound` 的保护段）都**无条件**写「仅交易所侧止盈止损仍生效」。
- **可达性**：`manager._resolve_risk` 的 `sl or None`（`manager.py:576`）使「两个 pct 都为 0 ⇒ 交易所侧
  完全不挂 OCO」可达（运维置 `AI_TRADE_DEFAULT_SL_PCT=0` / `_TP_PCT=0` 即可）。彼时 `_exchange_oco_px`
  两路都回 `(None, None)`，而 `_format_exchange_oco` 本已正确产出「未挂止盈止损」——错在**上层把它
  拼进了「仍生效」的句式**，得到自相矛盾的「未挂止盈止损 仍生效」。
- **为什么阻塞**：这正是 [P1-5] 道德风险的**最坏形态**——保护层已经停手、交易所侧也没有防线，模型却
  被告知还有兜底。比「没有保护层」更糟（没有时它至少知道自己是唯一防线），与 §六.5 的诚实约束直接冲突。
- **修复**：`profit_guard.py` 新增 `_has_exchange_oco` / `_format_unavailable_fallback` 两个纯函数，
  按事实二选一措辞；前端在 `lib/aiTrade.ts` 新增**同语义单点真源** `describeProfitGuardFallback`，
  两个组件一并改走它（不在组件内就地拼串，避免第三份文案真源，[R-10]）。
- **护栏**：`test_ai_trade_profit_guard.py::test_unavailable_text_only_claims_a_backstop_that_actually_exists`
  （参数化覆盖有 / 无 OCO 两态）+ `aiTrade.test.ts::describeProfitGuardFallback` +
  `AiTradeAgentTrace.test.tsx` 的「未挂 OCO 时不得宣称仍生效」。

### RV-2【功能缺失】`lastExit` 回执在轨迹 UI 上被整段吞掉

- **现象**：`AiTradeTraceRound.ProfitGuardBlock` 以 `if (!guard || !view) return null` 早退，而
  `describeProfitGuardStatus` 对 `status="flat"` **恒返回 null**（§十一 D-2 的有意设计）。
- **后果**：`lastExit` 只在保护平仓后的**紧接下一轮**出现（[P1-7] 读取即消费），彼时仓位已平、
  `_inject_profit_guard` 走的正是 `build_idle_snapshot` 的 `status="flat"` 空壳 ⇒ 组件里那段
  `guard.lastExit && (…)` 在其**唯一**发生场景下永远不可达。§2.6(d) 要求的「让模型/用户知道仓位为何
  消失」在 prompt 侧成立、在 UI 侧被静默丢弃，且 `lastExit` 在全前端只此一个渲染点。
- **修复**：早退判据改为 `!guard || (!view && !guard.lastExit)`，状态徽标与状态正文按 `view` 条件渲染，
  回执行独立于 `view` 存在。
- **护栏**：`AiTradeAgentTrace.test.tsx::「平仓后的 flat 空壳快照仍渲染 lastExit 回执」`
  （该文件此前对保护段**零覆盖**，本轮一并补齐 armed / flat+回执 / 未挂 OCO / 快照缺失四态）。

> 两条修复均为加法、不改判定逻辑与任何 wire 形状，后端 1421 passed（7 failed 全属他人在制的
> `test_factor_library_expansion.py` 孤儿用例，改动前后同一集合）、前端 1178 passed / 116 files 全绿、
> `tsc --noEmit` 仅剩既有 `WeightBatchArena.test` 报错、ruff 对本特性 17 个文件全过。

---

## 十二、评审结论

### **有条件通过**

方案主干经代码逐点核对成立，规模适配得当（一个新纯核心模块 + 一处只读转发 + 条件渲染三段，
没有触碰 `live_auto` 的任何时序不变式，AI 回测显式列为非目标并给出了"收盘价近似 = 假数据"这一
经得起追问的理由）。缺陷全部集中在状态机生命周期边界与"显示 / 计数 / 文案"的诚实性上，已在
v2 正文逐条修复。**无 P0 / P1 遗留。**

放行条件（Subtask #2 必须逐条落实，缺一即视为未完成）：

1. **[P0-1] epoch 身份必须是 `(side, seq)`**，`_position_seq` 由 `_dispatch` 观察到的 `side=="close"`
   成交驱动（§2.3.1）。**T-3 必须先写、先红、再绿**——这是整个方案里唯一一条会在真实资金上把刚建的
   仓位当场打掉的缺陷，且 v1 完全没有防线。
2. **[P1-1] 以 `close` 成交事件为唯一成交判据**（§2.5）。绝不在 `apply` 返回后无条件计数或宣称"已平仓"；
   `_do_close` 有四条不平仓的返回路径，全部已在 §2.5 列表。
3. **[P1-2] 判定价单源** = `_current_px()`，与调用方传入的 `ref_px` 无关（§2.2 硬约束 + G-6 门禁）。
4. **[P1-6] 判定价超龄 / 行情陈旧 ⇒ `status="unavailable"` + 零动作**，并如实进 prompt 与 UI（§2.4 第 0b 条）。
5. **[P1-5] 宪章文案的两层语义不可删**：① 被强平 = 决策失误；② 保护层可能不可用、不可依赖。
   改写措辞可以，丢掉任一层不行。
6. **[P1-3] 所有样例数值必须先过反算**（§2.6(b) 已给出自洽基准与反算式），T-1 的断言写成构造式而非字面价。
7. **[P1-8] 前端保护行的门控是 `positionSide !== "flat"` 且快照存在**，二者同时成立；
   **[P1-9]** 杠杆留空时保证金口径诚实缺省，不得在前端硬编码默认杠杆。
8. **[P1-4]** 交易所侧 OCO 触发价首选取自开仓成交回执，现算只作回落并在 wire 上标 `exchangeOcoSource`。
9. **[P1-7]** `lastExit` 读取即消费（单轮一次性）。
10. 提交时（Subtask #3）注意工作树已有他人在制的未提交改动，`services/quant/app/config.py` 是与本特性
    **重叠的混合文件**，必须按 hunk 行级暂存，不得整文件 `git add`（§九末段）。

### 未纳入本轮、建议列为下一轮候选（不阻塞放行）

- **AI 回测接入利润保护**（§1.3 非目标）：需 bar 内 OHLC 级的峰值建模 + 保守成交假设，建议第 2/3 轮单独立项。
- **`ai_trade_default_sl_pct=0.05` / `ai_trade_default_tp_pct=0.10` 的默认值本身**：本轮只做"显性化 + 可记忆"，
  数值改动交给用户在 UI 上知情决定（§1.3）。上线后若 `profitGuardExits` 显示保护层几乎每笔都先于 OCO 触发，
  说明这两个默认值已形同虚设，届时再以配置项形式收紧。
- **保护平仓后的冷却期**（§2.5 / R-5）：刻意不加，列为上线后观测项。观测口径已由
  `profitGuardExits` + 逐笔 `pnl` 提供。
