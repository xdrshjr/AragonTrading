# AI 自动交易「盈转亏」与「开仓被静默丢弃」根治方案（spec v2）

> 状态：**v2** · 已评审（Subtask #1 设计评审：3 条 P0、9 条 P1 已在正文修订；见下方「评审记录」），未实施、未 commit
> 基线：`260a613`（master）
> 证据：`evidence/loss_audit.py`（可复算脚本）+ `evidence/audit-output.txt`（本机实跑输出）
> 取证库：`%APPDATA%/aragon-desktop/aragon.db` 的拷贝（2026-09-10 23:49 快照，WAL 一并拷出）

---

## 评审记录

评审口径：可行性 / 完整性 / 一致性（CLAUDE.md 与仓库既有约定）/ 规模是否合适。每一条都在基线代码上
逐行核对过，行号以 `260a613` 加当前工作树为准。**P0 与 P1 已全部在正文修订**（修订处标注
`【v2 · RV-…】`），P2 只登记，由实施节点酌情处理。

### P0（不修即不可实施或结论不可信）

| # | 问题 | 证据 | v2 修订 |
|---|---|---|---|
| RV-P0-1 | 向导表单 hook 越过 800 行红线，且有门禁：`launch/useAiTradeLaunchForm.ts` 现已 **792 行**，v1 要在它里面加 `profitLock` 的 payload 组装和 3 个字段的校验（至少 20 行），而 `launchWizard.source.test.ts:67-77` 的 G-1 用例对 `launch/**` 下每个文件断言 ≤ 800 行。落地即红，上一轮 IMP-GL-1 就是这样踩的 | `wc -l`；G-1 源码门禁 | §3.4 C-2：新增 `launch/useProfitLockFields.ts`，校验、payload 组装和错误文案都放在这里；`useAiTradeLaunchForm.ts` 净增 ≤ 6 行（一次调用 + 两处透传），并写进 §7.4 的行数断言 |
| RV-P0-2 | 验收 §7.5-3「AI 回测开 / 关两臂对拍」既跑不完，也比不出结论。① AI 回测每个决策轮都真调 LLM（`backtest_manager.py:141-151` 注入 `provider`），5 标的 × 30 天 × 30m = 7 200 轮/臂，按实测 75s/轮约 150 小时/臂，还要付真实 token；② LLM 输出非确定，两臂的**开仓序列本身就不同**，净收益差里混着方向噪声，无法归因到收益锁。「不得调参凑数」这条门槛因此没有意义 | `backtest_manager.py`；§2.2 时延 | §7.5-3 改成**确定性反事实回放**：对 §2.1 中 131 笔真实往返，入场完全不变，只按 OKX 1m K 线逐根重放离场，锁的判定调用 `profit_lock_bar` 同一个纯核心，新增 `evidence/lock_replay.py` 与 `evidence/lock-replay.md`。AI 回测降级为接线冒烟：1 个标的、≤ 2 天、只断言锁确实触发过且归因正确 |
| RV-P0-3 | `confirm_ticks` 数的是**调用次数**，不是盯市 tick。`_maybe_profit_exit` 有两个调用点：盯市循环 `session.py:1986`，以及每个决策轮结束时的 `_post_round` `session.py:3897`。同一秒内可能连判两次，所以「连续 2 tick ≈ 10s」这个前提不成立。另外 v1 把收益锁挂在保护层分支里，保护层快照 `unavailable` 时会先 `return`（`session.py:3979`），收益锁那一 tick 被整体跳过，越线计数不会归零：断连前的一次越线加上恢复后的一次越线，就会立即平仓 | `session.py:1983-1986 / 3893-3898 / 3976-3980` | 改成**按墙钟确认**（`confirm_s`，默认 8s）：记录 `breach_since_wall`，任何一次「未越线 / stale / 不可判定」都清零；`_maybe_profit_exit` 改为两步编排，只有一个收益锁调用点，且不受保护层快照可用性影响（§3.3 B-1/B-2/B-3、§5.1、§5.3） |

### P1（不修会静默失效或违反仓库约定）

| # | 问题 | 证据 | v2 修订 |
|---|---|---|---|
| RV-P1-1 | 回测接线缺了三处：`AiBacktestStartRequest` / `backtest_manager.py:151` 的引擎构造 / `api/ai_auto.py:1175` 的解析都不在文件清单里，锁到不了回测引擎。回测平仓没有写打标槽 `_pending_exit_source`，所以 `classify_backtest`（`exit_attribution.py:94-99`）会把锁平归成 `unknown`。锁在回测里的 `cost_bps` 从哪来也没写 | 同左 | §3.3 B-5 与 §4 补齐：打标 `"profitGuard"`；成本改用与 B-4 相同的估算纯核心 |
| RV-P1-2 | v1 说「`_apply_profit_exit` 一行都不用改」，又说「`explain.trail` 增加 lockStage / lockPx / costBps」，两者矛盾。`trail` 在 `session.py:4007-4008` 是写死的两个键 | 同左 | B-3：`_apply_profit_exit` 新增唯一的关键字参数 `trail_extra`（`dict` 或 None，默认 None），合并进 `trail`；保护层调用点不传这个参数，逐字不变 |
| RV-P1-3 | `preSubmitCheck` 声称的两个渲染落点都不成立。① 执行轨迹 `describeExecuted`（`lib/aiTrade.ts:1193-1207`）遇到 `not_submitted` 就走 `skipped` 分支，只显示「已跳过」，**连原因码都不显示**，这也是 172 次丢弃在 UI 上「静默」的直接原因；② `fill_ref_json` 由 `journal/records.py:178` 的**白名单**（`reason/closeBlocked/closedFilled`）组装，新键会被静默丢掉，而且该模块尚未跟踪（在制特性） | 同左 | 新增 §3.4 C-5：纯函数 `lib/aiTradePreSubmit.ts`，`describeExecuted` 的 skipped 分支只加一行委托。执行轨迹走 `_round_trace["executed"]` 整对象透传（`session.py:3264`），这是唯一的主落点；journal 白名单作为**条件项**（在制特性先合入则同一提交补键和用例） |
| RV-P1-4 | 身份复检与成本复检共用 `pre_submit_context_changed` 这一个码（`session.py:1059-1061`）。G1「< 5%」上线后如果没达标，无法区分是身份变化还是成本变化 | 同左 | A-3：身份分支也写 `preSubmitCheck={"kind":"identity","changed":[…]}`，不新增原因码（冻结集合不动） |
| RV-P1-5 | `cost_runtime.py` 已经 **802 行**，v1 还往里加一个完整方法 | `wc -l` | A-1：CostRuntime 只加一个 ≤ 12 行的取数方法 `pre_submit_cost_pair`，重要性判断放在 `pre_submit.py`，与 session.py 的「只允许薄接线」同一纪律 |
| RV-P1-6 | 向导步骤模型漏登记。新增的校验量 `profitLockError` 必须进入 `aiTradeLaunchSteps.ts` 里冻结的 `AI_TRADE_LAUNCH_ERROR_KEYS` / `READY_INPUTS` / `BANNER_INPUTS` / `STEP_ERROR_OWNERS`（U-2 要求无遗漏、无重叠），第 5 步摘要（`:317-321`）也要加「收益锁开/关」。否则第 5 步「下一步」不会被非法字段挡住，确认页也不说原因（与 RV2-P0「义务没有写点」同形） | 同左 | C-2 补齐，§7.4 补用例 |
| RV-P1-7 | `AiTradeStatsDetail.tsx` 已经 **790 行**，v1 直接在里面加一行读数 | `wc -l` | C-3：新增 `aiTradeStats/GreenToRedLine.tsx`，Detail 净增 ≤ 2 行 |
| RV-P1-8 | G3 与 §8.4 的「30–35 笔 / +52.9～+60.7」只按成本项算武装阈值，**没算 ATR 项**（0.35×ATR，30m 约 14–21 bps，4H 可达 50 bps 以上），也没算越线确认。把它写在目标里会高估收益 | §3.3 公式 | G3 改成只写机制，不写收益数字；§8.4 标注「仅成本项的上界」；真实数字以 RV-P0-2 的回放为准 |
| RV-P1-9 | 锁平之后，模型可能立刻同向重开（模型看不到锁，本轮也不开冷却），每次多付一份往返成本，而这种「手续费失血」在 v1 里完全不可观测 | B-3 第 6 条 | 本轮**不加**冷却（理由：加了就得改 prompt，否则模型会反复被降级），但必须可观测：`_summarize_pairs` 加 `lockReentryCount`，统计页显示；评审结论条件 RC-3 给出下一轮启用冷却的触发阈值 |

### P2（登记，不阻断）

- RV-P2-1 收益锁装配后会强制 `peak_tracking_always=True`，`trades.peak_pnl` 的覆盖面变大，捕获率的
  `noPeakCount` 会下降，这是口径扩大，不是回归。实施时在统计页的捕获率说明里写一句。
- RV-P2-2 `lockNetBps` 是**目标**净额。市价离场会叠加确认窗口内的不利移动和滑点，实际净额可能低于它。
  持仓行文案用「目标净 +x bps」，`title` 里写明原因。
- RV-P2-3 `store.ts` 核实过：`setAiTradeMemberPosition` 是整对象透传（`store.ts:1176-1177`），**不用改**，
  已从 §4 删掉；但 `PaperPositionEvent` 类型需要加 `profitLock?`（`lib/types.ts`）。
- RV-P2-4 冷续跑用「键是否存在」区分老 wire，这个判据可靠，因为 `start_wire = body.model_dump(mode="json")`
  （`api/ai_auto.py:696`）会把 `profitLock: null` 也写进去，新 wire 一定有这个键。已补进 B-6 正文。
- RV-P2-5 `_summarize_pairs` 已接近 50 行上限，新键请放进辅助函数 `_green_to_red_keys(trips)`。
- RV-P2-6 `lib/aiTrade.ts`（1637 行）与 `AiTradeTraceRound.tsx`（912 行）都已超过 800 行，本轮只允许往表里加条目，
  或者加一行委托，逻辑放进新文件。
- RV-P2-7 纪律预设视图（`discipline_runtime.facts`）不认识收益锁，本轮只在向导的卡片里说明，不进纪律等级。

---

## 0. 一句话结论

生产数据说明，亏损的主因不是方向判断，而是两个确定性的执行缺陷：

1. **盈转亏（主要亏损来源）**：2026-08-01 以来 131 笔往返，Agent 自主平仓的 92 笔胜率只有 26%，合计
   **−96.74 USDT**。其中 **46 笔在持仓期间曾经浮盈，最后却以亏损离场**，累计回吐 **100.4 USDT**，
   超过同期全部净亏损（−55.17）。这批「曾浮盈的亏损单」峰值浮盈的四分位是
   **11.3 / 20.2 / 32.4 bps**。现有利润保护层的武装门槛是保证金收益率口径：5× 杠杆下保本线要 **60 bps**
   才武装，移动止盈要 **100 bps**，这批单子一次都够不到。另外，最近 70 个 run 里有 55 个根本没装配利润
   保护层（`profitGuard=None`）。
2. **开仓被静默丢弃（让系统偶尔进场、进场时机随机）**：最新一组会话（`aitradegrp_8b8c35…`，
   2026-09-08 至 09-10）共 181 次开仓决策，**172 次（95%）在下单前被 `pre_submit_context_changed` 丢弃**。
   根因是 `CostRuntime._funding_terms_changed` 用逐位相等比较资金费率，而 OKX 的 `fundingRate`
   **大约每 60 秒更新一次**（本机实测：`ts` 相隔 63s，费率从 `3.47233e-05` 变为 `3.45148e-05`）。
   资金费轮询 TTL 是 60s，而 LLM 深度推理的时延中位数是 **74.6s**（p90 108.5s）。所以几乎每一轮在
   推理期间都会读到一个「新」费率，差值只有约 0.003 bps，却被当成「成本变了」，整笔开仓作废。

本方案用三条轴根治：**A 下单前复检改为按重要性判断**（开仓不再被噪声作废，同时新增真正需要的价格漂移
保护）、**B 成本锚定收益锁**（以 bps 计、默认开启、不受利润保护开关影响的确定性保本加锁利层），以及
**C 可观测与交互**（把「锁住了多少」「回吐了多少」「为什么没开」告诉用户）。

---

## 1. 概述

### 1.1 做什么、为什么

【AI 自动交易】已经经过多轮加固：成本真值化、利润保护、持仓周期、离场优先级、经验图、深度思维链等。
但用户的直接感受仍然是「一直亏损」。本轮不再给 LLM 堆更多上下文，而是回到生产库，逐笔拆解往返。
结论很清楚：**模型方向判断的毛胜率是 47.3%，接近硬币；真正把账打成负数的是离场几何**。
确定性通道（利润保护承诺线 / 移动止盈 / 交易所 OCO）的往返胜率在 52%–100% 之间，合计 **+43.9**；
LLM 自主离场的往返合计 **−96.7**，而且其中一半是「先赚后亏」。也就是说，这套系统缺少一个在
**典型波动尺度**（十几到几十 bps）上就生效的确定性锁利机制。现有的利润保护层用保证金收益率口径，
设计目标是锁住「大浮盈」，结构上看不见这个尺度的问题。

另一半问题更隐蔽。执行链在下单前复检「本轮冻结的成本断面」是否仍然成立，这本来是正确的纪律
（不能用过期事实下单）。但这个复检用的是**逐位相等**，而它比较的对象之一是一个每分钟都会变化的
市场量。于是「纪律」变成了「随机开关」：只有在推理期间恰好没有刷新资金费时，开仓才会被放行。
这让系统的进场分布与模型判断脱钩，也让回测（没有这道复检）与实盘失去同构。

### 1.2 目标

- G1：`pre_submit_context_changed` 在同类行情下的占比从 95% 降到 **< 5%**，并且只在**有经济意义的**成本
  变化（默认 > 1.0 bps）时触发。
- G2：新增市价开仓的**不利价格漂移**保护（推理 75s 期间价格已不利移动超过阈值则放弃追单）。现有复检
  反而完全没有这一项。
- G3：新增默认开启的**成本锚定收益锁**：浮盈达到 `max(1.5×往返成本, 0.35×ATR)` 后，把离场线抬到
  「含费保本 + 2 bps」；浮盈达到 `max(3×往返成本, 0.8×ATR)` 后，锁住峰值浮盈的 50%，且只升不降。
  【v2 · RV-P1-8】能转化多少笔、改善多少 USDT，**只以 §7.5-3 的确定性回放为准**。§8.4 的数字只算了
  成本项，属于偏乐观的上界，不作为目标。
- G4：修正现有利润保护层的**保本线低于成本**缺陷（5× 下偏移 0.2%/5 = 4 bps < 往返成本约 10 bps，
  「保本离场」实际是净亏）。
- G5：用户能在持仓行里看到「收益锁」状态，在统计页看到「盈转亏」读数，在启动向导里理解并控制
  这把锁。所有新 wire 键都有渲染落点。

### 1.3 非目标

- 不修改决策 prompt（系统宪章 / 决策纪律 / 审议协议一字不改），收益锁本轮对模型不可见。原因：prompt
  冻结门禁的改动面过大；收益锁是纯确定性层，不需要模型配合就能生效。下一轮再评估是否要告诉模型
  「系统会替你保本」。
- 不改变做多偏置（样本里多 120 : 空 11）。趋势基调层（`trend_verdict`）已在上一轮合入，本轮只在统计里
  如实披露多空分布，不加新的方向门禁。
- 不改 LLM 供应商或推理深度（时延 75s 是事实，本方案让执行链容忍它，而不是假设它会变快）。
- 不新增 DB 表或列（零迁移）。

---

## 2. 证据（全部可复算）

复算命令（先把生产库连同 `-wal/-shm` 一起拷出来）：

```
python docs/plans/ai-trade-profit-lock-and-entry-unblock/evidence/loss_audit.py <aragon.db 拷贝>
```

### 2.1 往返按离场来源拆分（2026-08-01 以来，131 笔，净 −55.17，费 33.76，胜率 38.2%）

| 离场来源 | 笔数 | 胜率 | 合计 USDT | 离场价位中位 (bps) | 峰值中位 (bps) | 盈转亏笔数 | 回吐 USDT |
|---|---|---|---|---|---|---|---|
| `agent_close`（机器码） | 47 | 0.26 | −17.62 | −9.4 | 20.6 | 32 | 37.05 |
| Agent 平仓（旧 prose reason） | 45 | 0.27 | −79.12 | −31.2 | 28.9 | 14 | 63.37 |
| `external_close`（交易所 OCO 等） | 21 | 0.52 | +20.39 | +15.7 | 86.7 | 2 | 6.24 |
| `profit_guard_committed_exit` | 11 | 0.82 | +14.69 | +36.9 | 89.9 | 2 | 5.98 |
| `profit_guard_trailing` | 4 | 1.00 | +8.81 | +63.9 | 125.9 | 0 | 0 |
| `session_stop` | 2 | 1.00 | +0.66 | +23.4 | 27.3 | 0 | 0 |
| `offline_exit` | 1 | 0.00 | −2.98 | −49.6 | — | 0 | 0 |

读法：

- 毛口径胜率 47.3%（按价格方向），扣费后降到 38.2%。有 12 笔毛盈利被手续费翻成净亏损，**说明入场
  方向本身不是主因**。
- 盈转亏的亏损单峰值四分位为 11.3 / 20.2 / 32.4 bps，往返成本中位约 7 bps（taker 双边约 10 bps）。
  也就是说，这些单子**在最高点时已经覆盖了 1.5–4 倍成本**，却没有任何机制把这段利润留下来。
- 现有利润保护默认参数（`config.py:361-366`）：`arm_return_pct=0.05`、`breakeven_arm_return_pct=0.03`，
  均为保证金收益率。在默认 5× 杠杆下折合价格 **100 bps / 60 bps**，高于上表 75% 分位（32.4 bps）。
- `breakeven_offset_return_pct=0.002` ÷ 5 = **4 bps**，低于往返成本，所以「保本线」触发后是净亏损。
- 最近 70 个 ai-trade run 中 55 个 `config_json.profitGuard == null`（未装配）。最新一组
  （run 1640–1644）的 8 笔往返全部是 `agent_close`，其中 6 笔曾浮盈 0.7–3.1 USDT，最后全部亏损离场。

### 2.2 开仓漏斗（最新组 `aitradegrp_8b8c35…`，2 827 个决策轮）

| 非 hold 决策结果 | 次数 |
|---|---|
| `open_*` → `pre_submit_context_changed`（未下单） | **172** |
| `open_*` → `accepted` 且成交 | 4 |
| `open_*` → `accepted` 未成交（限价挂单到期） | 5 |
| `close` 成交 | 7 |

- LLM 时延：中位 74.6s，p90 108.5s（`telemetry_json.latencyMs`，模型 `glm-5.1`，`reasoningDepth=deep`，
  带 reask）。
- 被丢弃与放行的两组时延分布几乎一样（中位 77.2s 对 74.3s），说明丢弃**与推理耗时长短无关**，只与
  「推理窗口内是否跨过一次 60s 资金费刷新」有关。
- 触发代码路径：`session.py:1065` `if self._cost.pre_submit_context_changed(): return
  "pre_submit_context_changed"` → `cost_runtime.py:155-170` → `_funding_terms_changed`
  (`cost_runtime.py:172-182`) 对 `rate` 做逐位 `!=` 比较。
- 本机对 `GET /api/v5/public/funding-rate?instId=SOL-USDT-SWAP` 连续三次采样（间隔 12s）：前两次
  `ts=1790265804045` 相同，第三次 `ts=1790265867053`，`fundingRate` 由 `0.0000347233…` 变为
  `0.0000345148…`。差值 2.1e-7，对应 12h 预期持仓（1.5 个结算周期）的成本变化约 **0.003 bps**。

### 2.3 其他观察（本轮不修，只登记）

- 多空分布 120 : 11，而 BTC 在同期从约 79 088 下行到约 77 065。这可能是做多偏置，趋势基调层已存在，
  本轮只把多空分布写进统计。
- `backend.log` 在 2026-09-02 轮转后只剩 1.2MB，httpx INFO 日志已关闭，所以资金费刷新的时间线无法从
  日志还原。§2.2 的因果链由「代码路径 + 交易所实测 + 时延分布无差异」三方交叉确认。

---

## 3. 技术设计

### 3.1 总体结构

```
                 ┌───────────── 决策轮（LLM ~75s）──────────────┐
payload 冻结 ──▶ │ begin_round_cost() 冻结 RoundCostSnapshot     │
(ref_px, ATR)    │ _round_execution_identity = identity()       │
                 └───────────────────────┬──────────────────────┘
                                         ▼
                     _execute_decision → _prepare_open_context(decision, ref_px)
                        ① 身份复检（持仓序/方向/数量/挂单）   ——不变
                        ② 事实/持久化复检                      ——不变
                        ③ 成本复检：PreSubmitCheck.cost()      ——轴 A：逐位相等 → 按重要性判断
                        ④ 价格漂移复检：PreSubmitCheck.drift() ——轴 A：新增（仅市价开仓）
                                         ▼
                                   executor.apply(...)

盯市 tick（5s）
  _settle_exit_commitment → _maybe_profit_exit
                              ├─ 利润保护层（既有；保本线加成本下界，轴 B-3）
                              └─ 收益锁 ProfitLockRuntime.evaluate（轴 B，新增，与保护层是否装配无关）
                                     └─ 触发 → 同一条 _apply_profit_exit 平仓路径（市价 close）
```

### 3.2 轴 A：下单前复检按重要性判断

#### A-1 成本复检（替换 `CostRuntime.pre_submit_context_changed`）

新增纯函数模块 `ai_auto/pre_submit.py`（只放纯函数与数据类，不做 IO）：

```python
@dataclass(frozen=True)
class PreSubmitVerdict:
    """下单前复检结论；code=None 表示放行。"""
    code: str | None            # None | "pre_submit_context_changed" | "pre_submit_price_drift"
    readings: dict              # camelCase 读数，进 fill_ref / 开仓漏斗 / 决策日志


def cost_materiality(frozen_bps: float | None, current_bps: float | None,
                     *, tolerance_bps: float) -> PreSubmitVerdict:
    """成本复检：只有「现在比冻结时更贵，且贵出 tolerance 以上」才拦。

    - 任一侧为 None（估算不可用）⇒ 放行（fail-open，同 edge 门禁极性；理由见 R-3）。
    - current <= frozen + tolerance ⇒ 放行（变便宜永远不拦）。
    - 否则 ⇒ code="pre_submit_context_changed"，readings 带 frozenBps/currentBps/deltaBps/toleranceBps。
    """


def adverse_drift(*, side: str, ref_px: float, now_px: float | None,
                  limit_bps: float) -> PreSubmitVerdict:
    """价格漂移复检：只看不利方向（long 看上涨、short 看下跌）。

    now_px 为 None / 非有限 / ref_px<=0 ⇒ 放行（fail-open）。
    漂移 bps = (now_px - ref_px) / ref_px * 1e4 * (+1 long / -1 short)；> limit_bps ⇒
    code="pre_submit_price_drift"，readings 带 driftBps/limitBps/refPx/nowPx。
    """


def drift_limit_bps(*, min_bps: float, atr_mult: float,
                    atr_bps: float | None) -> float:
    """阈值 = max(min_bps, atr_mult × atr_bps)；atr 缺失 ⇒ min_bps。"""
```

`CostRuntime` 只新增一个**取数**方法，替换的是原方法的**调用点**；原方法保留给回退档。
【v2 · RV-P1-5】`cost_runtime.py` 已经有 802 行，本方法净增 ≤ 12 行，判定逻辑一律放在 `pre_submit.py`：

```python
def pre_submit_cost_pair(self, decision, bar_seconds: int) -> tuple[float | None, float | None]:
    """(冻结断面的往返成本, 此刻的往返成本)，单位 bps；本轮未冻结 ⇒ (None, None)。

    frozen：self.estimate_cost_only(decision, bar_seconds)。这是既有入口，推理期间 _cost_params()
            与 _round_funding() 读到的正是冻结断面，不另写第二条估算路径
    current：estimate_round_trip_cost(query_for_decision(decision, funding=self.funding.snapshot(now),
             ...), self._current_cost_params() 叠加冻结的 half_spread，与原方法 :161-162 同式).total_bps
    """
```

`pre_submit.PreSubmitRuntime.check()` 取这对数，交给 `cost_materiality(frozen, current,
tolerance_bps=settings.ai_trade_pre_submit_cost_tolerance_bps)`。

关键点：

- **比较对象从「参数」换成「参数导致的成本」**。资金费率每分钟变化、费率档位 TTL 刷新、`stale` 位翻转，
  都只在它们改变了**这一笔开仓的往返成本**且方向不利时才拦。
- `_round_cost is None`（本轮没冻结）⇒ 放行，与现状 `return False` 一致。
- `stale and not snap.stale`（费率档位在推理期间过期）**不再单独触发**。费率档位按月变化，过期只说明
  没刷新到，不说明成本变了；成本估算本身已按 `fee_source` 回落。
- 结算点跨越（推理期间过了 `next_funding_ts`）会自然反映在 `funding_periods` 与费率上，由成本差判断，
  不需要单独分支。

#### A-2 价格漂移复检（新增）

- **只对 `decision.ord_type == "market"` 的开仓**生效。限价单的价格边界本身就是保护：向不利方向漂移时
  限价单不会成交，反向漂移时限价单以更优价格成交。
- `ref_px` 取 `_build_decision_payload` 冻结的那个 `lastPx`（也就是模型看到的价格），`now_px` 取
  `self._current_px()`，阈值取 `drift_limit_bps(min_bps=20, atr_mult=0.5, atr_bps=ATR/ref_px×1e4)`。
- 30m 周期的 BTC ATR 约 40–60 bps，阈值约 20–30 bps。

#### A-3 会话侧改动（`session.py`，净增 ≤ 12 行）

`_prepare_open_context()` → `_prepare_open_context(decision, ref_px)`（唯一调用点 `session.py:3787`
同步改）。第 ③ 步替换为：

```python
if self._pre_submit.enabled:                         # 总闸 ai_trade_pre_submit_materiality_enabled
    verdict = self._pre_submit.check(self._cost, decision, ref_px=ref_px,
                                     now_px=self._current_px(), atr_px=self._atr_px)
    if verdict.code:
        self._pre_submit.last = verdict.readings
        return verdict.code
elif self._cost.pre_submit_context_changed():        # 回退档：逐字保留今天的判据
    return "pre_submit_context_changed"
```

`self._pre_submit` 是新增的 `PreSubmitRuntime`（放在 `pre_submit.py`，持有 settings 读数、`last` 读数与
三个计数 `identityVetoes` / `costVetoes` / `driftVetoes`），会话里只有构造一行和上面这段编排代码。

【v2 · RV-P1-4】身份复检分支（`session.py:1059-1061`）的**返回码不变**，仍是
`pre_submit_context_changed`，冻结集合不动；但返回前写入
`self._pre_submit.last = identity_readings(frozen, current)`，形如
`{"kind": "identity", "changed": ["positionSeq" | "side" | "qty" | "restingClOrdId", …]}`
（纯函数，放在 `pre_submit.py`，逐元素比较 `_execution_identity()` 元组）。这样 G1 上线后如果不达标，
可以从读数分出是「身份变了」还是「成本变了」。`last` 在每轮 `_prepare_open_context` 入口复位为 None，
防止上一轮的读数被带进本轮。

`_execute_decision` 的 not-submitted 返回里加一个键 `preSubmitCheck: self._pre_submit.last`
（与 `executionContextChanged` 并列）。【v2 · RV-P1-3】这个 dict 经 `_round_trace["executed"]`
**整对象透传**（`session.py:3264`）进入执行轨迹，渲染见 §3.4 C-5。决策日志 `fill_ref_json` 由在制特性
`journal/records.build_fill_ref` 按**白名单**组装（`:178`），只会带出 `reason/closeBlocked/closedFilled`：
如果该特性先于本轮合入，就在同一提交里把 `"preSubmitCheck"` 加进那个元组并补一条用例（W-T13）；
如果还没合入，本轮不碰那个文件，因为它尚未被跟踪。

#### A-4 开仓漏斗登记

- `open_funnel.py:111-116` 的 reason 冻结集合新增 `"pre_submit_price_drift"`。
- `integrity_view.py` 标签新增 `"pre_submit_price_drift": "价格已不利移动超过阈值，放弃追单"`。
- 前端 `apps/web/lib/aiTradeIntegrity.ts:170-174` 标签表新增
  `pre_submit_price_drift: "价格已不利移动"`；`pre_submit_context_changed` 文案改为
  `"执行前成本明显上升"`（后端 `integrity_view.py:19` 同步改为「开仓成本在研判期间明显上升，等待重新研判」）。

### 3.3 轴 B：成本锚定收益锁（Profit Lock）

#### B-1 纯核心 `ai_auto/profit_lock.py`（新文件，≤ 300 行）

```python
REASON_LOCK_BREAKEVEN = "profit_lock_breakeven"
REASON_LOCK_TRAILING = "profit_lock_trailing"

@dataclass(frozen=True)
class ProfitLockRule:
    """收益锁规则（价格 bps 口径，与杠杆无关）。"""
    enabled: bool = True
    arm_cost_mult: float = 1.5        # 阶段 1 武装：MFE ≥ max(arm_cost_mult×cost, arm_atr_mult×ATR)
    arm_atr_mult: float = 0.35
    buffer_bps: float = 2.0           # 阶段 1 锁线 = 含费保本 + buffer
    trail_cost_mult: float = 3.0      # 阶段 2 武装：MFE ≥ max(trail_cost_mult×cost, trail_atr_mult×ATR)
    trail_atr_mult: float = 0.8
    keep_ratio: float = 0.5           # 阶段 2 锁线 = entry ± keep_ratio × MFE（不低于阶段 1 线）
    min_hold_s: float = 60.0          # 开仓后多久才开始判定（避开成交瞬间的噪声）
    confirm_s: float = 8.0            # 【v2 · RV-P0-3】越线须持续的墙钟秒数（不是调用次数）


@dataclass(frozen=True)
class LockFacts:
    """一次评估的断面（聚合结构体）。"""
    side: str                 # long | short
    entry_px: float
    peak_px: float            # 取 ProfitGuardState.peak_px（同一个 epoch 峰值，不另算第二份）
    mark_px: float
    opened_wall: float
    now_wall: float
    cost_bps: float | None    # 按持仓方向估的往返成本（None ⇒ 本 tick 不可判定，fail-open）
    atr_bps: float | None     # None ⇒ ATR 项按 0 处理（只用成本项）


@dataclass
class LockState:
    """epoch 作用域状态：只存棘轮后的锁线与越线起点。"""
    epoch: tuple              # (side, seq)，与 ProfitGuardState 同一身份
    lock_px: float | None = None
    stage: int = 0            # 0 观察 / 1 保本锁 / 2 利润锁
    breach_since_wall: float | None = None   # 本段连续越线的起点墙钟；None = 当前未越线


def derive_lock(rule, facts, prev: LockState) -> LockState:
    """纯函数：算 MFE(bps)、两级门槛、候选锁线，按方向单调棘轮（long 只升 / short 只降）。"""


def evaluate_lock(rule, facts, state: LockState) -> tuple[LockState, LockVerdict | None]:
    """纯函数：mark 越过 lock_px，且 now_wall − breach_since_wall ≥ confirm_s ⇒ LockVerdict。

    【v2 · RV-P0-3】确认按**墙钟**计，与每秒被调用几次无关（盯市 tick 与 _post_round 都会调用）。
    首次越线只记 breach_since_wall=now_wall，不触发；confirm_s=0 ⇒ 首次越线即触发。
    任一次「未越线」⇒ breach_since_wall 归 None。min_hold 内不判定、不记越线起点，但会推进锁线（峰值照记）。
    """


def reset_breach(state: LockState) -> LockState:
    """stale / 成本不可判定 / 会话不 active 时调用：越线起点清零，锁线与 stage 保留。"""


def lock_wire(rule, facts, state) -> dict:
    """camelCase 快照（§5.2 形状），供持仓行与 status 使用。"""
```

公式（long；short 取对称）：

```
mfe_bps        = (peak_px − entry_px) / entry_px × 1e4
arm_bps        = max(arm_cost_mult × cost_bps, arm_atr_mult × atr_bps)
trail_arm_bps  = max(trail_cost_mult × cost_bps, trail_atr_mult × atr_bps)
floor_bps      = cost_bps + buffer_bps                      # 含费保本线（净额 ≥ +buffer）
stage1_px      = entry_px × (1 + floor_bps / 1e4)          # mfe_bps ≥ arm_bps 时生效
stage2_px      = entry_px × (1 + max(floor_bps, keep_ratio × mfe_bps) / 1e4)   # mfe ≥ trail_arm
lock_px        = max(prev.lock_px, 生效的最高一级)            # 单调棘轮
触发            : mark_px ≤ lock_px 持续 ≥ confirm_s 秒（期间每次评估都越线）
```

不变式检查：`arm_bps > floor_bps` 必须成立（否则武装的那一刻 mark 已经在锁线下方，会立即平仓）。
`arm_cost_mult ≥ 1.0` 且 `buffer_bps < (arm_cost_mult − 1) × cost_bps + arm_atr 项` 不能靠默认值碰运气，
因此 `derive_lock` 里写成硬约束：`stage1` 只在 `mfe_bps ≥ max(arm_bps, floor_bps + min_gap_bps)` 时生效，
`min_gap_bps = 3.0`（模块常量，不开放配置），保证武装时 mark 至少高出锁线 3 bps。

#### B-2 运行时 `ai_auto/profit_lock_runtime.py`（新文件，≤ 200 行）

```python
class ProfitLockRuntime:
    """收益锁运行时（纯同步；无 IO、不碰 executor）。rule=None ⇒ 全部方法零动作。"""
    def __init__(self, rule: ProfitLockRule | None): ...
    def clear(self) -> None: ...                         # flat / 平仓成交后
    def tick(self, facts: LockFacts, *, epoch: tuple, stale: bool) -> LockVerdict | None: ...
    def snapshot(self) -> dict | None: ...               # 最近一次 lock_wire；无仓 None
    exits: int                                           # 实际成交的锁平次数
    armed_epochs: int                                    # 进入过 stage≥1 的持仓数
```

- `stale=True`（`_stale_degraded` 或判定价年龄 > `ai_trade_profit_guard_max_px_age_s`）⇒ 不判定，
  并且 **`reset_breach`**【v2 · RV-P0-3】。fail-open，与利润保护同极性，复用同一阈值，不新增字段。
  「不推进」是不够的：断连前留下的越线起点，恢复后加上一次越线，就会被当成「已持续 ≥ confirm_s」。
  `cost_bps is None` 同样走 `reset_breach`。
- epoch 变化（反手 / 同向重开）⇒ 状态整体复位。这与 `ProfitGuardRuntime.refresh` 的 `[P0-1]` 教训
  相同：原子反手不经过 flat。

#### B-3 会话接线（`session.py`，净增 ≤ 60 行，全部为薄接线；v1 的 ≤ 25 未计入两步编排与身份读数）

1. 构造：`self._lock = ProfitLockRuntime(profit_lock_rule)`，新增构造形参
   `profit_lock_rule: ProfitLockRule | None = None`（默认 None ⇒ 既有直接构造会话的单测零回归）。
2. **峰值来源**：收益锁需要 `ProfitGuardState.peak_px`。在 `profit_lock_rule is not None` 时，会话构造
   `ProfitGuardRuntime` 必须传 `peak_tracking_always=True`（覆盖 settings）。否则保护层未装配 + 峰值
   追踪总闸关 ⇒ `state` 恒 None ⇒ 收益锁永远看不到峰值。
3. 【v2 · RV-P0-3】`_maybe_profit_exit` 改成**两步编排**，收益锁只有一个调用点：

   ```python
   async def _maybe_profit_exit(self, ts: int) -> None:
       if not self.is_active:
           return
       if await self._maybe_profit_guard_exit(ts):     # 原方法体逐字搬入，只把三处 return 改成 return False/True
           return                                       # 保护层本 tick 已平 ⇒ 收益锁不再判
       await self._maybe_profit_lock_exit(ts)
   ```

   v1 把收益锁挂在保护层的两个分支里，保护层快照 `unavailable` 时的早退（`session.py:3979`）会把收益锁
   整体跳过，越线状态也就无人清理。两步编排之后，收益锁每次都会被评估，stale 与不可判定由它自己的
   `reset_breach` 处理。`_maybe_profit_lock_exit` 只做「组装 LockFacts → `self._lock.tick` → 命中则经
   `_apply_profit_exit` 平仓 → `_report_profit_lock_exit`」。如果保护层在等锁期间抢先平了仓，收益锁
   `_apply_profit_exit` 的内层复检（`projected == "flat"`）返回 None，不会重复平仓。
   两个调用点（盯市循环 `:1986`、`_post_round` `:3897`）都不改。
4. `LockFacts.cost_bps` = `self._cost.estimate_cost_for_position(self.executor.projected,
   BAR_SECONDS[self.bar])`。这是持仓周期 G3 已在用的**同一个**入口（`[P1-2]` 已论证过持仓方向口径），
   不另写估算。`atr_bps = self._atr_px / entry_px × 1e4`。
5. `_apply_profit_exit` 需要 `verdict.reason_code / label / note`，`snap` 需要 `side / entryPx /
   peakUnrealizedPnlUsd / unrealizedPnlUsd`（`givebackRatio / givebackPctEffective / trailBasis` 读不到时
   为 None，这是既有的 `.get` 语义）。收益锁的 `LockVerdict` 与这些键**同名同形**，由
   `profit_lock_runtime.build_exit_snap(facts, qty_coin)` 组装。explain 仍是 `kind="profit_guard"`
   ⇒ `exit_attribution.classify_live` 归入 `profitGuard`，**不新增 EXIT_SOURCES 取值**（避免同步六处手抄镜像）。
   【v2 · RV-P1-2】`_apply_profit_exit` 只新增一个关键字参数 `trail_extra: dict | None = None`，
   写法是 `trail={..., **(trail_extra or {})}`。收益锁传 `{"lockStage", "lockPx", "costBps"}`；
   保护层的调用点不传，`trail` 逐字不变（W-T5b 断言）。v1 说「一行都不用改」，这与 §6 的
   `explain.trail` 新键互相矛盾，以本条为准。
6. 平仓成功后（**以 close 成交事件为唯一判据**，与 `_report_profit_exit` 的 [P1-1] 同一纪律）：
   `self._lock.exits += 1`、`self._lock.clear()`，推一条 status，文案由 `describe_exit_outcome(pnl)` 按
   符号派生：「收益锁触发（保本锁 / 利润锁）：已市价平仓，{结果}」。没成交时每个持仓周期至多推一条
   warn（同 P2-8）。**不开启**利润保护冷却，原因有三：冷却需要保护层规则存在；锁平后回到原方向是合法的；
   更关键的是冷却会进 prompt（`format_profit_cooldown`），与本轮「收益锁对模型不可见」的非目标冲突。
   【v2 · RV-P1-9】代价是可能出现「锁平 → 立即同向重开」的手续费失血，本轮用 §3.4 C-3 的
   `lockReentryCount` 把它变成可观测量，下一轮是否启用冷却按评审结论条件 RC-3 的阈值决定。
7. `_emit_position` 包装里（`session.py:3923-3926`）追加：`pos = {**pos, "profitLock": self._lock.snapshot()}`
   （快照为 None 时不加键）。
8. `_status_payload()` 顶层追加 `"profitLock": {"exits": …, "armedEpochs": …}`（规则未装配时不加键）。
9. 持仓周期层（horizon）**不拦**收益锁平仓。它只约束 Agent 主动发起的平仓（`config.py` 注释已写明
   「止损 / 交易所 OCO / 利润保护层…一律不受影响」），而收益锁走的正是利润保护的同一条路径。

#### B-4 利润保护保本线的成本下界（修 G4）

`profit_guard.PositionFacts` 新增可选字段 `cost_floor_bps: float | None = None`；`_derive` 里把
`breakeven_px` 改为

```
offset_bps = max(rule.breakeven_offset_return_pct / lev × 1e4,
                 (facts.cost_floor_bps or 0) + BREAKEVEN_COST_BUFFER_BPS)   # 常量 2.0
```

`build_position_facts` 在总闸 `ai_trade_profit_guard_breakeven_cost_floor_enabled`（默认 True）开启时
填入 `cost_floor_bps = estimate_cost_for_position(...)`；关闭时保持 None ⇒ 逐字回退。回测侧
`profit_guard_bar.BarFacts` 同步加字段。回测成本由 `backtest.py` 新增的 `_position_cost_bps(side)` 提供，
它用回测会话**自己的** `CostParams` 调用同一个 `estimate_round_trip_cost` 纯核心（资金费按回测既有口径处理），
**不要**手写「taker×2 + half_spread×2」第二套公式。收益锁回测（B-5）也读这个函数。

#### B-5 回测同构（`ai_auto/profit_lock_bar.py`，新文件，≤ 120 行）

沿用 `profit_guard_bar` 的保守成交顺序：① 用本根**不利极值**判越线（状态不推进；越线按
`min(open, lock_px)` 成交，跳空按开盘价）；② 未越线再用**有利极值**推进峰值与锁线。回测里
`confirm_s` 视为 0（一根 K 线远长于 8 秒确认窗口），并在结果 notes 里写明「回测按 K 线极值近似，
低估收益锁收益」。`backtest.py` 在 `_apply_profit_guard_bar(i, row)` 之后调用
`self._apply_profit_lock_bar(i, row)`，净增 ≤ 25 行。运维总闸 `ai_trade_profit_lock_backtest_enabled`。

【v2 · RV-P1-1】回测接线的完整落点（v1 漏了前三处，锁根本到不了回测引擎）：

1. `api/ai_auto.py:1175` 旁：`profit_lock_rule=_resolve_backtest_profit_lock(body.profitLock, settings)`，
   在 `_resolve_profit_lock` 之上再叠加 `ai_trade_profit_lock_backtest_enabled`。
2. `AiBacktestStartRequest`（`backtest_manager.py`）加字段 `profit_lock_rule: ProfitLockRule | None = None`；
   `backtest_manager.py:151` 引擎构造旁透传 `profit_lock_rule=req.profit_lock_rule`。
3. 回测引擎构造形参 `profit_lock_rule=None`，默认 None ⇒ 既有回测用例零回归。
4. `_apply_profit_lock_bar` 命中时，**在 `close_at_price` 之前**写 `self._pending_exit_source = "profitGuard"`，
   与 `_apply_profit_guard_bar:661` 同形。不写的话，`classify_backtest` 查冻结表 `_BACKTEST_REASONS` 查不到，
   打标槽又为空，这笔就会被归成 `unknown`（`exit_attribution.py:94-99`）。**不要**把 `profit_lock_*`
   加进 `_BACKTEST_REASONS`，那张表只收 `SimBroker` 写死的字面量（`is_deterministic_reason` 语义）。
5. 保护层与收益锁同一根 K 线都命中时，保护层先成交；收益锁看到 `pos.side == "flat"` 后复位，不再成交（B-T12b）。

#### B-6 装配与解析（API 层）

- `schemas.py`：新增 `AiTradeProfitLockIn`（`enabled: bool = True`，其余字段均为 `float | None`，带
  `Field` 上下界，见 §5.1）；`AiTradeGroupStartIn` 与回测请求 `AiBacktestStartIn`（`schemas.py:1228`）各加 `profitLock: AiTradeProfitLockIn | None = None`；`AiTradeGroupRow`（`schemas.py:1204`
  附近）加 `profitLock: dict | None = None`（该模型有显式字段清单，**不声明就会被静默剔除**）。
- `api/ai_auto.py`：新增 `_resolve_profit_lock(raw, settings) -> ProfitLockRule | None`，语义与
  `_resolve_profit_guard` 一致：**字段缺省 ⇒ 按 settings 开启**；显式 `enabled=false` ⇒ None；运维总闸关
  ⇒ 恒 None。实盘在 `api/ai_auto.py:624` 旁解析一次（绝不调用两遍），回测另叠
  `ai_trade_profit_lock_backtest_enabled`。
- `manager.py`：`AiTradeGroupRequest`（`:320` 附近）加 `profit_lock_rule`；两处 `profit_rule=req.profit_rule`
  构造点（`:813`、`:1028`）旁加 `profit_lock_rule=req.profit_lock_rule`；`group_status()`（`:2087`）与
  run `config_json`（`:2543`）加 `"profitLock": lock_rule_wire(rule)`。
- **冷续跑**：`resume._rebuild_request`（`resume.py:97`）用 `AiTradeGroupStartIn(**startWire)` 重跑
  **同一条** `build_start_request` 解析链，所以「字段缺省 ⇒ 开启」会让老会话在续跑时突然多出一把锁，
  同一个 run 的前后两段口径也会不一致。修法是在该函数构造请求体之前加一行：
  `wire = {"profitLock": {"enabled": False}, **(row["startWire"] or {})}`（老 wire 缺键 ⇒ 显式关闭；
  新 wire 自带键 ⇒ 原样覆盖）。新启动的 `start_wire` 由前端必发 `profitLock`，服务端不改写原文。
  「键是否存在」这个判据可靠，因为 `start_wire = body.model_dump(mode="json")`（`api/ai_auto.py:696`）
  会把缺省字段写成 `"profitLock": null`。本特性合入后落库的每一条 wire 都**一定**有这个键（null ⇒
  续跑时按默认开启，与原始启动时的解析结果一致）；只有合入前的 wire 才会缺键（RV-P2-4）。
  **不**用 `model_fields_set` 判 legacy（intel 轴的做法）：老桌面客户端发起的**新**启动同样缺键，
  而它们应该按默认开启。

### 3.4 轴 C：可观测与交互

#### C-1 持仓行「收益锁」行

新组件 `apps/web/components/Chart/AiTradeProfitLockRow.tsx`（≤ 120 行），挂在 `AiTradeMemberRow.tsx`
的 `<BreakEvenRow />` 之后。门控与 `ProfitGuardRow` 相同：`positionSide !== "flat"` 且
`member.position?.profitLock` 存在。展示（单行、不折行，遵守工作台零折行契约）：

| stage | 文案（主） | 色调 |
|---|---|---|
| 0 观察 | `收益锁 · 浮盈 {mfe} / 需 {arm} bps 武装` | muted |
| 1 保本锁 | `收益锁 · 保本锁 {lockPx}（目标净 +{lockNetBps} bps）` | accent |
| 2 利润锁 | `收益锁 · 已锁 {lockNetBps} bps · 线 {lockPx}` | success |
| unavailable | `收益锁 · 暂停判定（{原因}）` | warn |

`title` 悬停给出完整公式读数（成本、ATR、两级门槛）。文案与数字格式放在纯函数
`apps/web/lib/aiTradeProfitLock.ts`（`describeProfitLock(snapshot)` → `{ tone, text, title }`），
组件只负责渲染。

#### C-2 启动向导「收益锁」卡片

`launch/StepGuards.tsx` 在利润保护卡片**之前**新增一张卡片（收益锁对多数用户比利润保护更重要，应当
先看到）。组件拆到新文件 `launch/ProfitLockCard.tsx`（≤ 140 行），避免 StepGuards 越过 800 行红线：

- 开关默认开，标题「收益锁（推荐）」，一句话说明：「浮盈覆盖约 1.5 倍往返成本后，把离场线抬到含费
  保本之上；浮盈继续扩大后锁住一半。只会平仓，不会开仓或加仓。」
- 下方显示用当前标的与周期预估的**实际数字**：「按当前成本约 {cost} bps、ATR 约 {atr} bps：浮盈到
  {arm} bps 时保本，到 {trailArm} bps 时开始锁利」。数据来自可交易性预检
  (`/ai-trade/tradability` 已返回 cost 与 ATR)；预检未就绪时只显示公式，不编数字。
- 「高级」折叠区：`arm_cost_mult`、`keep_ratio`、`confirm_s` 三个字段（其余走默认值，不暴露）。
- 关闭开关 ⇒ 在确认步骤的风险清单里出现新条目 `profitLockOff`：「收益锁已关闭：历史上 35% 的亏损单
  曾浮盈超过 1.5 倍成本后回吐翻负」。`AI_TRADE_LAUNCH_RISK_IDS` 冻结列表（`aiTradeLaunchRisk.ts:35`，现 10 条）需要**同步追加**，否则
  I-1「风险零丢失」用例会报红。

状态落在 `useAiTradeLaunchState.ts`（`profitLockEnabled` + 3 个 raw 字符串，**复位只走唯一的
`state.seed()`**，不新增 `useEffect`，遵守 G-2(d) 与 modal-revert 复发教训）。

【v2 · RV-P0-1】校验与 payload **不写进** `useAiTradeLaunchForm.ts`，因为它已有 792 行，而 G-1 门禁要求
`launch/**` ≤ 800。新建 `launch/useProfitLockFields.ts`（≤ 120 行，不含 `useEffect`）：

```ts
export interface ProfitLockFieldsView {
  error: string | null;                    // 首个字段级错误（中文，含字段名与合法区间）
  fieldErrors: Partial<Record<"armCostMult" | "keepRatio" | "confirmSeconds", string>>;
  payload: AiTradeProfitLockIn;            // enabled=false ⇒ { enabled: false }；否则只发用户改过的字段
}
export function useProfitLockFields(state: AiTradeLaunchState): ProfitLockFieldsView;
```

`useAiTradeLaunchForm.ts` 只加三处：一次 hook 调用、在 errors 里填 `profitLockError: view.error`、
在 live 与回测两份 payload 里各加 `profitLock: view.payload`，净增 ≤ 6 行。

【v2 · RV-P1-6】步骤模型必须同步登记，否则非法字段挡不住第 5 步的「下一步」，确认页也不说原因：

- `lib/aiTradeLaunchSteps.ts`：`AI_TRADE_LAUNCH_ERROR_KEYS`、`READY_INPUTS`、`BANNER_INPUTS` 各加
  `"profitLockError"`；`STEP_ERROR_OWNERS.profitLockError = { step: "guards", modes: BOTH }`（与
  `profitGuardError` 同形，U-2「无遗漏无重叠」用例会自动覆盖它）。
- 第 5 步摘要（`:317-321` 的 `case "guards"`）追加 `${DOT}收益锁${onOff(input.profitLockEnabled, "开", "关")}`，
  输入类型加 `profitLockEnabled: boolean`。既有摘要用例的期望串随之更新，这是有意的文案变更，不算回归。

#### C-3 统计页「盈转亏」读数

- 后端 `db/ai_auto_repositories._summarize_pairs` 追加三个**纯加法键**（既有键逐位不变）：
  - `greenToRedCount`：`peak_pnl > 0` 且 `net_pnl <= 0` 的往返数；
  - `greenToRedGivebackUsd`：这些往返的 `Σ(peak_pnl − gross_pnl)`（与捕获率同为毛口径，理由同 P1-3）；
  - `lockExitCount`：`reason` 以 `profit_lock_` 开头的往返数；
  - 【v2 · RV-P1-9】`lockReentryCount`：收益锁离场后，**下一笔**往返与它同向，且开仓时间与锁平时间相差
    ≤ `LOCK_REENTRY_WINDOW_S = 1800` 秒（模块常量，前后端各一份，由双向断言锁住）的次数。按 `trips`
    时间序相邻两笔判定，纯派生，不需要新列。不用「1 根执行周期」作窗口：`_summarize_pairs(trips,
    total_budget_usd)` 拿不到周期，而且多标的分组里各成员的周期可能不同。
  四个键放进辅助函数 `_green_to_red_keys(trips)`，`_summarize_pairs` 只合并一行（RV-P2-5）。
- 前端 `lib/aiTradeCapture.ts` 的 `summarizeProfitCapture` 同式镜像（前后端口径逐字对应，已有双向断言
  范式）。
- 【v2 · RV-P1-7】新组件 `components/Agent/aiTradeStats/GreenToRedLine.tsx`（≤ 80 行）。
  `AiTradeStatsDetail.tsx`（已有 790 行）只在「利润捕获」区块下挂载它，净增 ≤ 2 行。文案：
  `盈转亏 {n} 笔 · 回吐 {usd} USDT · 其中收益锁离场 {m} 笔`；`m > 0` 时追加 `· 锁平后立即同向重开 {r} 笔`；
  `n == 0` 时显示「暂无盈转亏」而不是 0。

#### C-4 成交与退出来源标签

- `apps/web/lib/aiTrade.ts:529-533` 的 reason 前缀表新增
  `{ prefix: "profit_lock_breakeven", label: "收益锁：浮盈回落到含费保本线，已离场" }`、
  `{ prefix: "profit_lock_trailing", label: "收益锁：浮盈自峰值回落到锁利线，已离场" }`；
  `:975-978` 短名表新增 `profit_lock_breakeven: "保本锁"`、`profit_lock_trailing: "利润锁"`。
- `apps/web/lib/types.ts:500-502` 的 reason 联合类型加两个字面量；`PaperPositionEvent` 加可选键
  `profitLock?: AiTradeProfitLockSnapshot`（RV-P2-3：store 是整对象透传，不需要改）；`lib/tradeExplain.ts:59`
  附近的保护码集合加两个码。`lib/aiTrade.ts` 已有 1637 行，本条只允许往表里加条目（RV-P2-6）。

#### C-5 执行轨迹说清「为什么没开」【v2 · RV-P1-3 新增】

现状：下单前复检拦下的开仓，回执是 `status="not_submitted"`。`execution_result_keys` 把它映射成
`skipped=true`，`describeExecuted`（`lib/aiTrade.ts:1193-1198`）遇到 `skipped` 就只显示「已跳过」，
**原因码和读数都不显示**。172 次丢弃在 UI 上完全看不出来，这是「静默丢弃」的最后一环。

- 新纯函数文件 `apps/web/lib/aiTradePreSubmit.ts`（≤ 100 行）：
  `describePreSubmitCheck(executed) → { label, detail, tone } | null`。
  - `kind="cost"` ⇒ `执行前成本上升 {deltaBps} bps > 容忍 {toleranceBps}`；
  - `kind="drift"` ⇒ `价格已不利移动 {driftBps} bps > {limitBps}，放弃追单`；
  - `kind="identity"` ⇒ `研判期间持仓/挂单已变化（{changed 中文}）`；
  - 没有 `preSubmitCheck` 但 `reason` 存在 ⇒ 回落到 `aiTradeIntegrity.ts` 的原因标签（旧会话同样能看懂）；
  - 其他情况 ⇒ null（`describeExecuted` 走原逻辑，「已跳过」逐字不变）。
- `describeExecuted` 的 `skipped` 分支只加一行委托：`const pre = describePreSubmitCheck(executed); if (pre) return …`。
  `AiTradeTraceRound.tsx`（912 行）**不改**，它已经渲染 `describeExecuted` 的 label。
- `detail` 放在 `title` 悬停里，单行不折行（遵守工作台零折行契约）。
- 前端类型 `AiTradeDecision["executed"]` 加可选 `preSubmitCheck?: AiTradePreSubmitCheck`。

---

## 4. 文件 / 模块变更计划

| 文件 | 动作 | 意图 |
|---|---|---|
| `services/quant/app/ai_auto/pre_submit.py` | 新建 | 轴 A 纯核心：`cost_materiality` / `adverse_drift` / `drift_limit_bps` + `PreSubmitRuntime` |
| `services/quant/app/ai_auto/cost_runtime.py` | 修改 | 新增取数方法 `pre_submit_cost_pair(decision, bar_seconds)`（净增 ≤ 12 行，文件已 802 行）；旧 `pre_submit_context_changed` 保留作回退档 |
| `services/quant/app/ai_auto/session.py` | 修改 | `_prepare_open_context(decision, ref_px)` + 身份读数；`_maybe_profit_exit` 两步编排 / `_maybe_profit_lock_exit` / `_report_profit_lock_exit` / `_apply_profit_exit(trail_extra=)` / `_emit_position` / `_status_payload` 接线（净增 ≤ 60 行，全部是薄接线） |
| `services/quant/app/ai_auto/open_funnel.py` | 修改 | reason 集合加 `pre_submit_price_drift` |
| `services/quant/app/ai_auto/integrity_view.py` | 修改 | 两条标签（新增 1 条、改写 1 条） |
| `services/quant/app/ai_auto/profit_lock.py` | 新建 | 轴 B 纯核心：规则、断面、棘轮、判定、wire |
| `services/quant/app/ai_auto/profit_lock_runtime.py` | 新建 | 轴 B 运行时 + `build_exit_snap` + `lock_rule_wire` + `resolve_profit_lock_rule` |
| `services/quant/app/ai_auto/profit_lock_bar.py` | 新建 | 轴 B 回测保守成交顺序 |
| `services/quant/app/ai_auto/profit_guard.py` | 修改 | `PositionFacts.cost_floor_bps` + 保本偏移取成本下界 |
| `services/quant/app/ai_auto/profit_guard_runtime.py` | 修改 | `build_position_facts` 填 `cost_floor_bps`（新增关键字参数，默认 None） |
| `services/quant/app/ai_auto/profit_guard_bar.py` | 修改 | `BarFacts.cost_floor_bps` 透传 |
| `services/quant/app/ai_auto/backtest.py` | 修改 | 构造形参 `profit_lock_rule` + `_apply_profit_lock_bar`（打标 `profitGuard`）+ `_position_cost_bps` + notes 免责（净增 ≤ 25 行） |
| `services/quant/app/ai_auto/backtest_manager.py` | 修改 | 【v2】`AiBacktestStartRequest.profit_lock_rule` + `:151` 引擎构造透传 |
| `services/quant/app/ai_auto/manager.py` | 修改 | 请求字段、两处构造、`group_status` 与 `config_json` 回执 |
| `services/quant/app/ai_auto/resume.py` | 修改 | `_rebuild_request` 对缺 `profitLock` 键的老 `startWire` 显式补 `enabled=false` |
| `services/quant/app/api/ai_auto.py` | 修改 | `_resolve_profit_lock` / `_resolve_backtest_profit_lock` + 实盘（`:624` 旁）与回测（`:1175` 旁）两处装配 |
| `services/quant/app/schemas.py` | 修改 | `AiTradeProfitLockIn`；三个模型各加 `profitLock` |
| `services/quant/app/config.py` | 修改 | §5.3 的 16 个设置项（显式声明，否则 `extra="ignore"` 会静默丢弃 env） |
| `services/quant/app/db/ai_auto_repositories.py` | 修改 | `_green_to_red_keys` 四个加法键（`_summarize_pairs` 合并一行） |
| `services/quant/app/ai_auto/journal/records.py` | 条件修改 | 【v2】仅当在制 journal 特性先合入：`build_fill_ref` 白名单加 `preSubmitCheck` |
| `.env.example` | 修改 | 16 个 env 键双向登记 |
| `apps/web/lib/aiTradeTypes.ts` | 修改 | `AiTradeProfitLockRule` / `AiTradeProfitLockSnapshot` / status 与 stats 新键 |
| `apps/web/lib/aiTradeProfitLock.ts` | 新建 | `describeProfitLock` / `AI_TRADE_PROFIT_LOCK_DEFAULTS` / `previewLockThresholds` |
| `apps/web/components/Chart/AiTradeProfitLockRow.tsx` | 新建 | 持仓行收益锁状态 |
| `apps/web/components/Chart/AiTradeMemberRow.tsx` | 修改 | 挂载 `<ProfitLockRow />` |
| `apps/web/components/Agent/launch/ProfitLockCard.tsx` | 新建 | 向导收益锁卡片 |
| `apps/web/components/Agent/launch/StepGuards.tsx` | 修改 | 挂载卡片 |
| `apps/web/components/Agent/launch/useAiTradeLaunchState.ts` | 修改 | 4 个 state + `seed()` 复位 |
| `apps/web/components/Agent/launch/useAiTradeLaunchForm.ts` | 修改 | 【v2】只挂 `useProfitLockFields`：净增 ≤ 6 行（文件已 792 行，G-1 ≤ 800） |
| `apps/web/components/Agent/launch/useProfitLockFields.ts` | 新建 | 【v2】收益锁字段校验 + payload 组装 |
| `apps/web/lib/aiTradeLaunchSteps.ts` | 修改 | 【v2】`profitLockError` 四处登记 + guards 摘要 |
| `apps/web/lib/aiTradeLaunchRisk.ts` | 修改 | `profitLockOff` 风险条目 + `AI_TRADE_LAUNCH_RISK_IDS` 追加 |
| `apps/web/lib/aiTradeCapture.ts` | 修改 | 盈转亏四键镜像 + `LOCK_REENTRY_WINDOW_S` |
| `apps/web/components/Agent/aiTradeStats/AiTradeStatsDetail.tsx` | 修改 | 挂载 `GreenToRedLine`（净增 ≤ 2 行，文件已 790 行） |
| `apps/web/components/Agent/aiTradeStats/GreenToRedLine.tsx` | 新建 | 【v2】盈转亏 / 锁平 / 重开读数行 |
| `apps/web/lib/aiTrade.ts` | 修改 | reason 标签与短名；`describeExecuted` skipped 分支一行委托 |
| `apps/web/lib/aiTradePreSubmit.ts` | 新建 | 【v2】`describePreSubmitCheck` |
| `apps/web/lib/types.ts` | 修改 | reason 联合类型 + `PaperPositionEvent.profitLock?` |
| `apps/web/lib/tradeExplain.ts` | 修改 | 保护码集合 |
| `apps/web/lib/aiTradeIntegrity.ts` | 修改 | `pre_submit_price_drift` 标签 + 改写旧标签 |
| `services/quant/tests/test_ai_trade_pre_submit.py` | 新建 | 轴 A 用例（§7.1） |
| `services/quant/tests/test_ai_trade_profit_lock.py` | 新建 | 轴 B 纯核心 + 运行时 + 回测用例（§7.2） |
| `services/quant/tests/test_ai_trade_profit_lock_wiring.py` | 新建 | 会话 / API / 续跑 / 统计接线用例（§7.3） |
| `apps/web/lib/aiTradeProfitLock.test.ts` | 新建 | 文案 / 色调 / 阈值预览 |
| `apps/web/components/Chart/AiTradeProfitLockRow.test.tsx` | 新建 | 渲染门控与四态 |
| `apps/web/components/Agent/launch/ProfitLockCard.test.tsx` | 新建 | 开关 / 高级区 / 风险条目 / 步骤登记 |
| `apps/web/lib/aiTradePreSubmit.test.ts` | 新建 | 【v2】三种 kind + 旧会话回落 + null 逐字不变 |
| `docs/plans/ai-trade-profit-lock-and-entry-unblock/evidence/lock_replay.py` | 新建 | 【v2 · RV-P0-2】确定性反事实回放（验收 §7.5-3） |

session.py、backtest.py、cost_runtime.py、lib/aiTrade.ts 已经超过 800 行红线（4881 / 1764 / 802 / 1637 行），
本轮**只允许薄接线**，所有逻辑进新模块；useAiTradeLaunchForm.ts（792）与 AiTradeStatsDetail.tsx（790）逼近红线，同样只挂载。
新建文件全部 < 300 行。

---

## 5. 接口设计

### 5.1 REST：启动与回测请求（新增字段，向后兼容）

`POST /ai-trade/group/start`、`POST /ai-trade/backtest`：

```jsonc
{
  // …既有字段…
  "profitLock": {                  // 缺省 ⇒ 按 settings 开启；null 同缺省
    "enabled": true,               // false ⇒ 本会话不装配
    "armCostMult": 1.5,            // [1.0, 10]，null ⇒ 默认
    "armAtrMult": 0.35,            // [0, 5]
    "bufferBps": 2.0,              // [0, 50]
    "trailCostMult": 3.0,          // [1.5, 20]，且必须 ≥ armCostMult（否则 422）
    "trailAtrMult": 0.8,           // [0, 10]
    "keepRatio": 0.5,              // (0, 0.95]
    "minHoldSeconds": 60,          // [0, 3600]
    "confirmSeconds": 8            // [0, 60]（v2：墙钟秒，取代 v1 的 confirmTicks）
  }
}
```

422 条件：`trailCostMult < armCostMult`；`keepRatio` 越界；任一数值非有限。错误信息格式
`"profitLock.trailCostMult 必须 ≥ armCostMult（收到 1.2 < 1.5）"`。

### 5.2 WebSocket / status wire

`position` 事件（`_emit_position`）增加：

```jsonc
"profitLock": {
  "stage": 0 | 1 | 2,
  "status": "watching" | "breakeven" | "trailing" | "unavailable",
  "unavailableReason": null | "stale_px" | "cost_unknown" | "min_hold",
  "side": "long",
  "entryPx": 77414.9,
  "peakPx": 77600.0,
  "mfeBps": 23.9,
  "costBps": 10.4,
  "atrBps": 48.0,
  "armBps": 16.8,
  "trailArmBps": 38.4,
  "lockPx": 77514.0,        // stage 0 ⇒ null
  "lockNetBps": 2.0,        // 锁线相对「含费保本」的净 bps；stage 0 ⇒ null
  "breachSeconds": 0,        // 本段连续越线已持续秒数；未越线 ⇒ 0
  "confirmSeconds": 8
}
```

`status` 顶层：`"profitLock": {"exits": 3, "armedEpochs": 5}`（规则未装配 ⇒ 无此键）。

`group_status()` / run `config_json`：`"profitLock": {…规则 camelCase…} | null`。

决策日志 `fill_ref_json`（未提交开仓时）：
`"preSubmitCheck": {"kind": "cost", "frozenBps": 10.41, "currentBps": 11.9, "deltaBps": 1.49,
"toleranceBps": 1.0}` 或 `{"kind": "drift", "driftBps": 31.2, "limitBps": 24.0, "refPx": …, "nowPx": …}`，
或（v2 · RV-P1-4）`{"kind": "identity", "changed": ["restingClOrdId"]}`。主落点是执行轨迹的 `executed`
（整对象透传）；`fill_ref_json` 是否带出这个键取决于在制 journal 特性的白名单（见 A-3）。

### 5.3 设置项（`config.py`，全部显式声明，`.env.example` 双向登记）

| 设置 | 默认 | 约束 | 说明 |
|---|---|---|---|
| `ai_trade_pre_submit_materiality_enabled` | `True` | — | 轴 A 总闸；False ⇒ 逐字回退到逐位相等判据 |
| `ai_trade_pre_submit_cost_tolerance_bps` | `1.0` | `ge=0, le=50` | 成本上升容忍 |
| `ai_trade_pre_submit_drift_guard_enabled` | `True` | — | 价格漂移复检开关 |
| `ai_trade_pre_submit_drift_min_bps` | `20.0` | `gt=0, le=500` | 漂移阈值下限 |
| `ai_trade_pre_submit_drift_atr_mult` | `0.5` | `ge=0, le=5` | 漂移阈值 ATR 项 |
| `ai_trade_profit_lock_enabled` | `True` | — | 收益锁运维总闸（关 ⇒ 恒不装配） |
| `ai_trade_profit_lock_arm_cost_mult` | `1.5` | `ge=1, le=10` | |
| `ai_trade_profit_lock_arm_atr_mult` | `0.35` | `ge=0, le=5` | |
| `ai_trade_profit_lock_buffer_bps` | `2.0` | `ge=0, le=50` | |
| `ai_trade_profit_lock_trail_cost_mult` | `3.0` | `ge=1.5, le=20` | |
| `ai_trade_profit_lock_trail_atr_mult` | `0.8` | `ge=0, le=10` | |
| `ai_trade_profit_lock_keep_ratio` | `0.5` | `gt=0, le=0.95` | |
| `ai_trade_profit_lock_min_hold_s` | `60.0` | `ge=0, le=3600` | |
| `ai_trade_profit_lock_confirm_s` | `8.0` | `ge=0, le=60` | v2：墙钟确认秒数（取代 v1 的 `confirm_ticks`） |
| `ai_trade_profit_lock_backtest_enabled` | `True` | — | 回测侧运维总闸 |
| `ai_trade_profit_guard_breakeven_cost_floor_enabled` | `True` | — | 轴 B-4 总闸 |

（共 16 项。）

---

## 6. 数据模型

- **零 DB 迁移**。收益锁平仓写入既有 `trades` 行：`reason ∈ {profit_lock_breakeven, profit_lock_trailing}`，
  `explain_json.kind = "profit_guard"`（复用），`explain_json.trail` 增加 `{"lockStage": 1|2, "lockPx": …,
  "costBps": …}`；`peak_pnl` 照常由 `take_peak_pnl()` 写入。
- `strategy_runs.config_json` 增加 `profitLock` 键（规则 wire，或 null）。
- 决策日志 `ai_trade_decision_logs.fill_ref_json` 增加 `preSubmitCheck` 键（JSON 列，无 schema 变更）。
- 内存结构：`LockState`（每会话 1 份，epoch 作用域）、`PreSubmitRuntime`（每会话 1 份：`last` 读数 +
  两个计数）。

---

## 7. 测试与验收

### 7.1 轴 A（`test_ai_trade_pre_submit.py`）

- A-T1 `funding_rate_tick_does_not_veto_open`（**本轮缺陷的复现用例，先写先红**）：冻结断面 rate=3.47e-5，
  推理期间 FundingCache 刷新为 3.45e-5 ⇒ 旧判据返回 `pre_submit_context_changed`（断言旧判据为红），
  新判据放行。
- A-T2 `material_cost_rise_still_vetoes`：费率档位 taker 从 5 升到 7 bps ⇒ deltaBps≈4 > 1 ⇒ 拦，readings 齐全。
- A-T3 `cost_drop_never_vetoes`；A-T4 `unknown_cost_fails_open`；A-T5 `stale_flip_alone_does_not_veto`。
- A-T6 `market_open_adverse_drift_vetoes`：long，ref 100，now 100.3（30 bps > 20）⇒ `pre_submit_price_drift`。
- A-T7 `favorable_drift_passes`；A-T8 `limit_open_skips_drift`；A-T9 `drift_limit_uses_atr_when_larger`。
- A-T10 `materiality_switch_off_is_byte_identical`：总闸关 ⇒ 与旧函数逐次同判（参数化 20 组断面）。
- A-T11 `identity_change_still_vetoes`：持仓序变化 ⇒ 仍返回 `pre_submit_context_changed`（身份复检不受影响）。
- A-T12 `funnel_registers_new_reason`：`open_funnel` 集合与 `integrity_view` 标签表、前端
  `aiTradeIntegrity.ts` 标签表三处都含 `pre_submit_price_drift`（源码扫描，CRLF 用 `\r?\n`）。

### 7.2 轴 B（`test_ai_trade_profit_lock.py`）

- B-T1 `stage1_arms_at_cost_multiple`：cost=10、ATR=40 ⇒ arm=max(15,14)=15；MFE 14.9 不武装、15 武装；
  锁线 = entry×(1+12/1e4)。
- B-T2 `arm_never_places_mark_below_line`：遍历 cost∈[2,40]、buffer∈[0,10]，武装瞬间 mark − lock ≥ 3 bps。
- B-T3 `stage2_keeps_half_of_mfe_monotonic`：峰值 60 bps ⇒ 锁 30；回落后峰值不变、锁线不降。
- B-T4 `short_side_symmetric`；B-T5 `confirm_requires_continuous_breach_by_wall_clock`（越线→回线→越线不触发；
  同一墙钟秒内连续两次评估越线也不触发，只有持续 ≥ confirm_s 才触发）。
- B-T5b `stale_gap_resets_breach`【v2 · RV-P0-3 复现用例，先写先红】：越线 → stale 3 次 → 恢复后越线一次
  ⇒ 不触发（越线起点已被 `reset_breach` 清零）。
- B-T6 `min_hold_blocks_trigger_but_tracks_peak`；B-T7 `stale_price_fails_open`；B-T8 `cost_unknown_fails_open`。
- B-T9 `reverse_resets_epoch`：long→short 原子反手，旧锁线不得带入新 epoch（对应 `[P0-1]` 教训）。
- B-T10 `green_to_red_replay`：用 §2.1 中 run 1640 的真实价格路径（从 `candles_cache` 取 30m OHLC，
  固定为夹具 JSON），在回测保守成交顺序下，该往返以 ≥ 0 的净额离场。
- B-T11 `breakeven_offset_never_below_cost`（B-4）：5× 杠杆、cost=10 ⇒ 保本线净额 ≥ +2 bps；总闸关 ⇒
  与旧值逐位相等。
- B-T12 `backtest_bar_conservative_order`：同一根内先触及锁线再创新高 ⇒ 按锁线离场，不按新高重算。
- B-T12b `backtest_lock_exit_attributed_profit_guard`【v2 · RV-P1-1】：回测锁平 ⇒ `classify_backtest` 归入
  `profitGuard` 而不是 `unknown`；同一根保护层与收益锁都命中 ⇒ 只成交一次。
- B-T12c `backtest_request_carries_lock_rule`【v2 · RV-P1-1】：`POST /ai-trade/backtest` 带 `profitLock` ⇒
  引擎构造时拿到的 `profit_lock_rule` 非 None；回测总闸关 ⇒ None。

### 7.3 接线（`test_ai_trade_profit_lock_wiring.py`）

- W-T1 `default_start_arms_lock`：请求不带 `profitLock` ⇒ `group_status().profitLock` 非空，`config_json`
  有键。W-T2 `explicit_disable`；W-T3 `ops_switch_off_forces_none`。
- W-T4 `lock_arms_without_profit_guard`：`profitGuard.enabled=false` + 收益锁开 ⇒ 峰值被追踪，锁可触发。
- W-T5 `lock_exit_goes_through_single_close_path`：monkeypatch `executor.apply` 断言收到
  `Signal(side="close", reason="profit_lock_breakeven")`，explain.kind=="profit_guard"，
  `explain.trail` 含 `lockStage/lockPx/costBps`。
- W-T5b `guard_exit_trail_unchanged`【v2 · RV-P1-2】：保护层平仓的 `explain.trail` 与改前逐键相等。
- W-T5c `lock_evaluated_when_guard_unavailable`【v2 · RV-P0-3】：保护层装配且快照 `unavailable` ⇒
  收益锁仍被评估，并执行 `reset_breach`。
- W-T6 `horizon_does_not_block_lock_exit`：`min_hold 120m` 内锁仍可平。
- W-T7 `guard_and_lock_same_tick_close_once`。
- W-T8 `resume_old_run_without_key_stays_unarmed`（startWire 无 `profitLock` ⇒ 续跑后规则为 None）；
  W-T9 `resume_new_run_restores_rule`；W-T9b `old_client_new_start_without_key_arms`（非续跑缺键 ⇒ 开启）。
- W-T10 `group_row_declares_profit_lock`：经 FastAPI `response_model` 走一遍后 `profitLock` 键仍在。
- W-T11 `summarize_pairs_green_to_red_keys`：既有键逐位不变（对拍夹具），新键值正确；前端
  `aiTradeCapture.test.ts` 同一组夹具对拍。
- W-T12 `lock_status_text_has_no_markdown_stars`：后端推送的收益锁 status message 不含 `**`
  （后端文案 `**` 已复发 4 次，门禁放在文案所在的文件旁边）。
- W-T13 `identity_veto_carries_readings`【v2 · RV-P1-4】：持仓序变化 ⇒ 码仍是 `pre_submit_context_changed`，
  `executed.preSubmitCheck.kind == "identity"`，`changed` 含 `positionSeq`；下一轮 `last` 已复位。
  如果 journal 特性已合入，再加一条：`build_fill_ref` 带出 `preSubmitCheck`。
- W-T14 `lock_reentry_count`【v2 · RV-P1-9】：锁平后 1800s 内同向重开 ⇒ `lockReentryCount=1`；反向或超窗 ⇒ 0。

### 7.4 前端

- `aiTradeProfitLock.test.ts`：四态文案、数值格式、`title` 含公式读数、极小价（SATS 1e-8）用科学计数。
- `AiTradeProfitLockRow.test.tsx`：flat 时不渲染（即使 `position.profitLock` 残留）；四态色调。
- `ProfitLockCard.test.tsx`：默认开；关闭后确认步骤出现 `profitLockOff`；高级字段非法时禁用 CTA 且给出
  字段级提示；预检未就绪只显示公式。
- `AiTradeLaunchModal.test.tsx`：`AI_TRADE_LAUNCH_RISK_IDS` 冻结列表更新为 11 条，W-13/W-14 用例同步。
- 【v2 · RV-P0-1】`launchWizard.source.test.ts` 的 G-1（≤ 800 行）必须保持绿：实施完成后
  `useAiTradeLaunchForm.ts` ≤ 798 行，`useProfitLockFields.ts` 不含 `useEffect`。
- 【v2 · RV-P1-6】`ProfitLockCard.test.tsx` 补两条：`keepRatio=1.2` ⇒ 第 5 步「下一步」被挡且就地提示；
  第 5 步摘要含「收益锁开」。U-2 冻结用例会自动覆盖 `profitLockError` 的归属。
- 【v2 · RV-P1-3】`aiTradePreSubmit.test.ts`：cost / drift / identity 三种文案；旧会话（只有 `reason`）
  回落到原因标签；既不是复检也没有 reason ⇒ `describeExecuted` 仍返回「已跳过」，逐字不变。
- vitest 必须在 `apps/web` 目录内运行：`NODE_ENV=test npx vitest run`。

### 7.5 验收标准（全部满足才算完成）

1. §7.1–§7.4 新增用例全绿；后端全量 pytest 与前端全量 vitest 相对基线 `260a613` **无新增红**（本机既有红
   按记忆登记的清单排除）。
2. `ruff check` 与 `tsc --noEmit` 对本轮新增 / 修改文件零新增告警。
3. 【v2 · RV-P0-2】**确定性反事实回放**（取代 v1 的「AI 回测两臂对拍」）。脚本
   `evidence/lock_replay.py <aragon.db 拷贝>`，报告写进 `evidence/lock-replay.md`：
   - **样本**：§2.1 的全部往返（2026-08-01 以来，131 笔），**入场完全沿用真实成交**，包括时刻、方向、
     入场价、数量和真实手续费。只改变离场，所以两臂的差异只来自收益锁，可以归因。
   - **行情**：对每笔往返，从 OKX 公共接口 `GET /api/v5/market/history-candles` 取入场到真实离场之间的
     **1m** K 线，另取入场前 14 根执行周期 K 线用来算 ATR。首次抓取后缓存到 `evidence/candles/`
     （gitignore，不入库），再跑时离线复算。
   - **判定**：直接 import 实施后的 `profit_lock` / `profit_lock_bar` 纯核心，**不另写第二份公式**。
     参数用 §5.3 的默认值，`confirm_s=0`，按 1m 保守成交顺序。`cost_bps` 取该笔真实手续费 bps 加
     配置的双边半点差；锁线在真实离场之前被触发 ⇒ 按成交价重算该笔毛额与净额，手续费沿用真实值。
   - **报告须给出**（关臂即真实结果，开臂即反事实结果）：净收益、胜率、盈转亏笔数与回吐 USDT、
     收益锁离场笔数（分 stage）、**被锁线提前扫出的赢家笔数及其少赚的 USDT**（R-1 的代价，必须单列，
     不得与收益相抵后只报净值）。
   - **验收门槛**：开臂**盈转亏笔数下降 ≥ 50%**，且**净收益 ≥ 关臂**。参数在跑之前冻结（即 §5.3 默认值）。
     任一门槛不满足 ⇒ 停下，回到参数评审，写出新的假设后再换一组参数跑**一次**；**不得**在同一样本上
     网格搜索凑数。
   - **已知局限**（报告首段必须写明）：锁平之后，真实世界里的后续决策可能不同（路径依赖），回放把
     各笔往返视为独立；1m 极值近似偏保守。
   - AI 回测只做**接线冒烟**：1 个标的、≤ 2 天，断言锁至少触发 1 次，且 `bySource.profitGuard` 计数增加。
     它不是收益门槛。
4. 实盘观察窗（合入后首个 48h 会话，由用户执行）：`openFunnel` 中 `pre_submit_context_changed` 占全部开仓
   意图 < 5%；`status.profitLock.armedEpochs > 0`。
5. 所有新 wire 键都有渲染落点：`position.profitLock` → 持仓行；`status.profitLock` → 持仓行 `title`
   里的「本会话锁平 {exits} 次」（统计页的离场数来自 `lockExitCount`，是另一条通路）；
   `executed.preSubmitCheck` → 执行轨迹的执行段【v2 · RV-P1-3】。渲染经 `describeExecuted` 委托
   `describePreSubmitCheck`，显示「执行前成本上升 1.49 bps > 容忍 1.0」或「价格已不利移动 31.2 bps > 24.0」；
   `stats.lockReentryCount` → `GreenToRedLine`。

---

## 8. 风险与缓解

| # | 风险 | 缓解 |
|---|---|---|
| R-1 | 收益锁把本来会走成大趋势的仓位在保本线扫掉，降低盈亏比 | 阶段 1 锁线只在 MFE ≥ 1.5×成本 且 ≥ 0.35 ATR 后才武装，并且要求越线持续 8s；阶段 2 只锁 50%。§7.5-3 的确定性回放以「净收益 ≥ 关臂」作为硬门槛，并单列「被扫出的赢家少赚了多少」，不达标不上线 |
| R-2 | 放宽成本复检后，用过期成本下单 | 只放宽「成本更低或差 ≤ 1 bps」的情形；成本明显上升仍拦截，并新增价格漂移复检，覆盖以前完全没有的风险 |
| R-3 | 成本估算不可用时放行（fail-open） | 与 edge 门禁、利润保护同极性；fail-closed 在暖机段会复现「永不开单」事故。放行时 readings 标 `costUnknown: true`，开仓漏斗可见 |
| R-4 | 收益锁与利润保护、承诺退出线、交易所 OCO 多线并存，用户困惑 | 持仓行分行显示；锁平与保护平都走同一条平仓路径，并在同一 tick 内去重（W-T7）；启动向导说明「收益锁管小浮盈，利润保护管大浮盈」 |
| R-5 | 本地盯市断连时收益锁失效 | 交易所侧兜底 OCO 不受影响；收益锁本轮不下交易所条件单（避免改动 order-algo 写路径）。在「风险」里如实说明：收益锁只在应用运行时生效 |
| R-6 | `_prepare_open_context` 改签名，遗漏调用点 | 全仓只有 `session.py:3787` 一处调用；W 用例里 grep 断言 |
| R-7 | 老 run 冷续跑突然多出一把锁 | `_rebuild_request` 对缺键 wire 显式补 `enabled=false`（W-T8） |
| R-8 | 新 wire 键被 `response_model` 吞掉，或 store 逐键手抄漏键 | W-T10 走真实 FastAPI 序列化；`store.ts` 的 position 透传路径在实现时核对（仓库有过「逐键手抄静默吞 WS 新键」的先例） |
| R-9 | 向导冻结风险列表未同步导致 I-1 用例红 | §7.4 明确列出要改的冻结列表与用例 |
| R-10 | 回测 `confirm_s` 近似为 0，结果偏乐观还是偏保守不明 | 不利极值先判，属于偏保守：锁平更早、少赚，与 profit_guard_bar 同口径；notes 免责 |
| R-11 | 做多偏置（120:11）在下行市里继续亏损 | 本轮不修，统计页如实披露；如果收益锁上线后盈转亏已收敛而亏损仍集中在逆势多单，下一轮再处理方向 |
| R-12 | CRLF 工作树 + 多个在制特性脏改（`config.py`、`session.py` 等已有未提交改动） | 提交时逐路径 `git add`，混合文件按行精确暂存；严禁 `git add -A` |

### 8.4 收益估算（上界，仅供评审参考，不作为验收）

按 `evidence/audit-output.txt` 的 84 笔可换算往返：如果所有「峰值 ≥ X bps 的亏损单」都以 +2 bps 净额离场，

| 武装阈值 X | 被转化的亏损单 | 上界改善 USDT |
|---|---|---|
| 12 bps | 35 | +60.7 |
| 15 bps | 30 | +52.9 |
| 20 bps | 25 | +32.9 |
| 30 bps | 14 | +18.9 |

同期净亏损为 −55.17（全部 131 笔）。上界没有扣除「被锁线提前扫出的赢家」的机会成本，因此以 §7.5-3
的确定性回放为准。

【v2 · RV-P1-8】上表**只按成本项**算了武装阈值。真实规则还要求 MFE ≥ 0.35×ATR（30m 约 14–21 bps），
越线也要持续确认，所以实际能转化的笔数只会更少。本表仅供量级参考，不写进目标，也不作为验收依据。

---

## 9. 实施顺序

1. 轴 A 纯核心 + A-T1 复现用例（先红后绿）→ 会话接线 → 漏斗标签三处。
2. 轴 B 纯核心 + 运行时 + B-T1..B-T9 → B-4 保本下界 → 会话接线 → API / manager / resume / schemas。
3. 回测 `profit_lock_bar` + 回测接线（RV-P1-1 五处）→ **确定性回放 `lock_replay.py`（§7.5-3）**，不达标则停下回到参数评审；
   达标后才进入前端工作。
4. 前端：类型 → `aiTradeProfitLock.ts` / `aiTradePreSubmit.ts` → 持仓行 → `useProfitLockFields` + 向导卡片 +
   步骤登记 + 风险列表 → `GreenToRedLine` → 标签表与 `describeExecuted` 委托。
5. 全量回归（后端 pytest 约 14 分钟，**不要与 vitest 并发**），`.env.example` 登记，逐路径暂存。

---

## 评审结论

**有条件通过**（spec v2）。

两条根因（资金费噪声导致开仓被丢弃、盈转亏）的取证链是成立的：代码路径、交易所实测和时延分布三方
一致。三条轴的方向也对：执行链容忍 LLM 时延；在典型波动尺度上加确定性锁利；让用户看见「为什么没开、
锁住了多少」。v1 的 3 条 P0 与 9 条 P1 已在正文修订（`【v2 · RV-…】`），**没有未解决的 P0/P1**。以下条件
由实施与终审节点兑现，任一条不满足都不得合入：

- **RC-1 回放门槛先行**（RV-P0-2）：§9 第 3 步的确定性回放 `lock_replay.py` 必须在前端工作开始**之前**
  跑完，并满足 §7.5-3 的两道门槛（盈转亏下降 ≥ 50%、净收益 ≥ 关臂），同时单列被扫出的赢家的代价。
  不达标 ⇒ 轴 B 停在参数评审，轴 A 与 B-4 可以单独合入（三者由各自的总闸独立控制）。
- **RC-2 复现用例先红**：A-T1（资金费噪声）与 B-T5b（stale 间隙残留越线）必须先在基线上写成红，再改业务
  代码（CLAUDE.md 七）。
- **RC-3 锁平重开的后续判据**（RV-P1-9）：实盘观察窗里如果 `lockReentryCount / lockExitCount > 30%`，且这些
  重开往返合计为负，下一轮就启用「锁平后同向冷却」，同时把冷却写进 prompt。本轮只做观测。
- **RC-4 行数红线**：`useAiTradeLaunchForm.ts` ≤ 798、`AiTradeStatsDetail.tsx` ≤ 792、`cost_runtime.py`
  净增 ≤ 12、`session.py` 净增 ≤ 60。G-1 源码门禁保持绿。
- **RC-5 在制文件隔离**：工作树里有大量在制改动（`session.py`、`config.py`、`.env.example`、未跟踪的
  `journal/`）。提交时逐路径暂存，混合文件按行精确暂存；`journal/records.py` 只在 journal 特性已合入时才改。
- **RC-6 实盘观察窗**（§7.5-4）由用户执行，不阻断合入，但结果要回填到本目录 `evidence/live-window.md`。

---

## 实施过程发现的方案缺陷

实施节点（loop tge-462dc593 第 1 轮 Subtask #2）在基线 `260a613` + 当前工作树上逐条核对后的发现。
每条都给出「方案原文 → 实际情况 → 采用的修正」。

| # | 级别 | 方案原文 | 实际情况 | 采用的修正 |
|---|---|---|---|---|
| IMP-1 | P0 | B-5：回测两步保守序（先不利极值判越线、再有利极值推锁线） | 用 §2.1 run 1640 的真实 30m 路径写 B-T10（先写先红）后实测红：武装那根 K 线**先冲高再回落、收盘已在新锁线下方**，两步序只能拖到下一根开盘成交（+4.8 bps 毛 / −5.6 bps 净），系统性地把本该保本的往返记成亏损 | `profit_lock_bar` 加第 3 步**收盘复核**：收盘价落到第 2 步新锁线的不利一侧 ⇒ 价格在「极值 → 收盘」途中必然穿过锁线，按锁线成交（收盘晚于根内任何极值，不含未来信息）。`LockBar.close_px` 缺省 ⇒ 逐字回退两步序；专用用例 `test_backtest_close_below_new_lock_fills_at_line` |
| IMP-2 | P0 | B-3 第 5 条：`_apply_profit_exit(trail_extra=)` 合并进 `trail` ⇒ `explain.trail` 带 `lockStage/lockPx/costBps` | `build_profit_guard_explain` **从不把 `trail` 落进 explain**，只用它生成一条 metric——三个键会被静默丢弃（又一处「有写点没读点」） | `live_auto/explain.py` 在 `trail` 带 `lockStage` 时追加顶层 `profitLock` 键 + 一条「收益锁线」metric；保护层平仓不带该键 ⇒ explain 逐字不变（W-T5b 断言）。§6「`explain_json.trail` 增加…」以本条为准：落点是 `explain_json.profitLock` |
| IMP-3 | P1 | A-4：后端 `pre_submit_context_changed` 文案改为「开仓成本在研判期间明显上升」 | 身份复检（RV-P1-4）**共用同一个码**：持仓/挂单变化被拦时，这句文案是假话 | 后端改为「研判期间开仓成本明显上升或持仓已变化，等待重新研判」，前端「执行前成本明显上升或持仓已变化」；具体是哪一种由 `executed.preSubmitCheck.kind` 在执行轨迹上说清 |
| IMP-4 | P1 | C-2：`profitLockError` 进 `AI_TRADE_LAUNCH_ERROR_KEYS` / `READY_INPUTS` / `BANNER_INPUTS` 三处 | `BANNER_INPUTS` = `firstError` 读到的全部，而 `firstError` 表达式被注释冻结「逐字节不改」（守 79 条用例的次序） | 与 `directionError` 同形：进 `ERROR_KEYS` + `READY_INPUTS` + `STEP_ERROR_OWNERS(guards)`，**不进** `BANNER_INPUTS`；错误在卡片字段旁就地渲染，第 6 步兜底行自动覆盖。用例：`ProfitLockCard.test.tsx` 断言 keepRatio=1.2 ⇒ `firstErroredStep` = guards |
| IMP-5 | P1 | §4 文件清单 | 清单外、但不改就静默失效的落点共 8 处：① `AiTradeToggle.tsx` 的实盘 / 回测两个 mutation 体是**逐键手抄**，不加 `profitLock` 键前端开关形同虚设；② `AiTradeLaunchModal.tsx` 的 `AiTradeLaunchOpts` 类型；③ `aiBacktestTypes.ts`；④ `schemas.AiTradeHistorySummary`（历史详情走 `response_model`，四个盈转亏键不声明即被剔除）+ `aiTradeHistoryTypes.ts`；⑤ `AiTradeTraceRound.tsx` 给执行段 label 加一个 `title` 属性（否则 C-5 的 `detail` 没有渲染落点）；⑥ `AiTradeStatsLive.tsx` 挂 `GreenToRedLine`（否则前端镜像 `summarizeGreenToRed` 是无读点死码）；⑦ `live_auto/explain.py`（IMP-2）；⑧ 既有用例两处：`test_ai_trade_integrity_api.py` 的 `_prepare_open_context()` 调用改签名、`test_ai_trade_backstop_repair_session.py` 的 group 替身补 `profit_lock_rule=None`（与「纪律预设三键」同一先例） | 全部按最小改动补齐 |
| IMP-6 | P2 | B-3 第 7 条：`_emit_position` 包装（`session.py:3923-3926`） | 那个包装叫 `_position_payload`（`_emit_position` 调它） | 在 `_position_payload` 里追加 `profitLock`，语义不变 |
| IMP-7 | P2 | C-4：`lib/types.ts:500-502` 的 reason 联合类型加两个字面量 | 那是利润保护快照的 `effectiveExitSource`（有效退出线由哪条保护线定住），收益锁码永远不会出现在那里 | 不加（加了是一个永远为假的类型分支）。`AiTradeProfitLockSnapshot` 定义在 `types.ts`（该文件注明反向 import 会成环），`aiTradeTypes.ts` 转导出 |
| IMP-8 | P2 | A-3：`PreSubmitRuntime` 持有三个计数 | 方案没有给这三个计数任何 wire 落点 | 只作进程内计数（供排障与用例断言），**不**新增 status 键——无渲染落点的键即死数据 |
| IMP-9 | P2 | C-3：`lockReentryCount` 按 trips 相邻两笔判定 | `_aggregate_group` 把各成员 run 的往返**逐 run 拼接**，run 边界上的相邻两笔不是先后关系 | 判定追加「同一 run」条件（W-T14 覆盖跨 run 不计）；前端镜像逐成员判定 |
| IMP-10 | P2 | RC-4 行数红线 | `cost_runtime.py` 净增 12（达标）；`useAiTradeLaunchForm.ts` 794（≤ 798 达标）；`AiTradeStatsDetail.tsx` 792（≤ 792 达标）。`session.py` 净增约 74 行，超出 A-3 ≤12 + B-3 ≤60 的合计约 2 行：B-4 保本下界的取数（6 行）不在两者的预算里。`backtest.py` 净增 48 行，超出 ≤25：B-4 在回测里有两个消费点（`_apply_profit_guard_bar` 与决策 payload 快照），需要 `_guard_cost_floor` 单点，再加 `_position_cost_bps`（从 `_horizon_facts` 抽出、后者因此净减 7 行）、免责常量与导入 | 两个文件本就是「只允许薄接线」：新增部分全部是接线，判定逻辑都在新模块里。如实登记，不为凑行数把接线塞进一行 |
| IMP-11 | P2 | §7.5-3 回放「对每笔往返取入场到真实离场之间的 1m K 线」 | ① 新会话的 `trades.ts` 是 **K 线时间戳**（双时钟写侧未修），不是成交墙钟；② OKX CDN 对无 User-Agent 的请求一律 403；③ 本机公共接口单请求 6–11 秒 | ① 成交分钟按「该根 K 线内首根价格区间覆盖成交价的 1m K 线」近似，写进报告首段的已知局限；② 请求带 UA；③ 按往返 8 路并发取数，缓存到 `evidence/candles/`（目录内自带 `.gitignore`，不入库） |

### RC-1 确定性回放结果（`evidence/lock-replay.md`，参数跑前冻结为 §5.3 默认值）

| 指标 | 关臂（真实） | 开臂（收益锁） |
|---|---|---|
| 净收益 USDT | −55.17 | −20.69 |
| 胜率 | 38.2% | 69.5% |
| 盈转亏笔数 | 67 | 26（−61%） |
| 盈转亏回吐 USDT | 197.56 | 50.14 |

收益锁离场 84 笔（保本锁 66 / 利润锁 18）；被改善 50 笔合计 +96.20；**被锁线提前扫出的赢家 33 笔，
合计少赚 61.36 USDT**（R-1 的代价，单列）。两道门槛（盈转亏下降 ≥ 50%、开臂净收益 ≥ 关臂）均通过，
前端工作在回放达标之后才开始收尾。注意：开臂仍为负——收益锁把「先赚后亏」这一块堵上了，但它不修
方向判断（R-11），也不修锁平后的路径依赖（回放把各笔往返视为独立）。

### 既有红（与本特性无关，已在 HEAD 复现）

- `test_ai_trade_backstop_repair_session.py::test_the_session_keeps_only_a_thin_seam`：断言
  `session.py` 里 `self._repair` ≤ 6 处，HEAD 即为 7 处（本轮未增加该标识）。

---

## 终审记录（loop tge-462dc593 第 1 轮 Subtask #3）

逐条对照 §4 文件清单、§7.5 验收与 RC-1..RC-6：覆盖完整，IMP-1..11 的修正均已落地并有用例。终审直接修了三处：

| # | 级别 | 问题 | 修正 |
|---|---|---|---|
| FR-1 | P1 | B-4 保本线成本下界没有「武装点间距」约束。保证金口径的武装点在高杠杆下很低（20× 下 3% ≈ 15 bps），往返成本高时（如 20 bps）抬高后的保本线（22 bps）会落在峰值**上方**：mark 此刻已越线，保本线一武装就市价平仓，正在走强的仓位被扫掉。收益锁有 `MIN_GAP_BPS` 管这件事，B-4 没有 | `profit_guard._derive`：成本下界确实抬高了偏移时，峰值须高出保本线 ≥ `BREAKEVEN_MIN_GAP_BPS`（3 bps，与收益锁同一不变式）才武装。未抬高 ⇒ 判定逐字不变。复现用例 `test_floored_breakeven_waits_until_peak_clears_the_line`（先红后绿） |
| FR-2 | P2 | `lock_wire.trailArmBps` 与前端 `previewLockThresholds` 在成本很低时显示的锁利门槛**低于**保本武装点（cost=2 ⇒ 保本 7 / 锁利 6），可 `derive_lock` 要先过阶段 1 才判阶段 2，界面上显示的是一个到不了的数 | 两侧展示值都取 `max(trail, stage1_at)`，后端抽出 `_stage1_at` 单点；前端补用例 |
| FR-3 | P2 | 向导卡片的阈值预估恒按默认倍数算，用户在「高级」里改了保本锁倍数后，预估数字不跟着变 | `Preview` 接 `view.payload`；`aiBacktestTypes.ts` 的内联 `import()` 类型改成顶部具名导入 |

**提交范围（RC-5）**：`session.py` / `manager.py` / `config.py` / `.env.example` / `lib/types.ts` 同时带着在制
`ai-trade-audit-journal` 的改动，按行精确暂存（只进索引，工作树不动）；`decider.py`、`models.py`、
`repositories.py`、`api/market.py`、`main.py`、`store.ts`、`page.tsx`、`NavRail*`、`httpTimeout.ts`、
`contrastTokens.source.test.ts` 与两个测试文件整份属于 journal，不在本提交。`journal/records.py` 的条件修改
按 A-3 不做（journal 尚未合入）。

