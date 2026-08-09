# AI 自动交易 · 持仓周期验收闭环（资金费真值化 · 退出来源归因 · 净额验收卡）

- **文档版本**：**v2**（已评审 · 全部 P0 / P1 已在正文闭合，闭合处带 `[P0-x]` / `[P1-x]` 反查标记）
- **特性目录**：`docs/plans/ai-trade-horizon-acceptance-loop/`
- **上游特性**：`docs/plans/ai-trade-holding-horizon-modes/`（交易周期模式 / 持仓周期铁律层，已合入 `11246ae`）
- **同族特性**：`docs/plans/ai-trade-cost-truth-and-net-pnl/`（成本真值化，已合入 `cebee54`）
- **本轮定位**：上一轮把「让持仓活得够久」的**机制**建起来了；本轮建的是「它到底有没有把钱赚回来」的
  **判据**——把上一轮明确列为非目标、以及调查原文最后一段点名要求但至今无处可读的三样东西补上：
  真实资金费、退出来源分布、以及一张能直接照着放行的净额验收卡。

---

## 评审记录（v1 → v2）

评审范围：可行性 / 完整性 / 一致性 / 规模适当性四维，逐节对照仓库源码核实（`cost_runtime.py`、
`funding.py`、`reconcile.py`、`attribution.py`、`horizon_runtime.py`、`hold_horizon.py`、
`session.py`、`manager.py`、`backtest.py`、`engine/simulator.py`、`live_auto/explain.py`、
`live_auto/executor.py`、`okx/rest_client.py`、`okx/trading.py`、`api/ai_auto.py`、`schemas.py`、
`config.py`，以及 `useAiTradeGroupStream.ts` / `store.ts` / `aiTradeHistoryTypes.ts` /
`AiTradeStatsLive.tsx` / `WakeAttributionCard.tsx`）。**共 4 P0 / 11 P1 / 8 P2，P0 与 P1 已全部在
正文修复**；P2 为不阻断放行的记录项，其中已顺手修掉的在正文标注。

### P0（阻断：不修就会造出静默失效或运行期异常）

| # | 结论 | 证据 | 闭合位置 |
|---|---|---|---|
| **P0-1** | **「结算周期取不到就不计提」这条核心不变式在现有取数链上不可能生效**——`rest_client.get_funding_rate` 已经在 `span <= 0` 时**静默兜底 8h**（`rest_client.py:284` `_DEFAULT_FUNDING_INTERVAL_S = 28800`），而 `cost_model.py:234` 又把 `funding_interval_known=True` 写死。于是 `FundingSnapshot.interval_s` **永远是正数**，轴 A 的 `interval_s <= 0 ⇒ 不计提` 分支与用例 F-1 **永远走不到**：真的取不到周期时系统会照 8h 计提并宣称这是真值，正好违反调查约束 #7 原文「不能固定假设结算频率」。这与上一轮实施发现「有默认值的字段无法表达『不知道』（`funding_interval_s` 恒 28800）」是同一个坑的第二次复发。 | `okx/rest_client.py:278-286`、`ai_auto/funding.py:70-75`、`ai_auto/cost_model.py:234` | §2.2.1 / §2.2.2 / §3.2 / §6.1 F-1 |
| **P0-2** | **验收卡阈值在 `target_hold_min_s == 0` 时除零，且第 1 行恒 pass**。`HorizonRule.target_hold_min_s` / `target_hold_max_s` 默认均为 `0.0`，且 `custom` 全默认是**文档化的安全退出口**（`hold_horizon.py:138-141`）。此时第 3 行的 `86400 / rule.target_hold_min_s` 直接 `ZeroDivisionError`（一个观测层的异常会打断整条 status 装配），第 1 行 `avgHold ≥ 0` 恒真，第 2 行的 `withinTargetRate` 在 `target_hold_max_s <= 0` 时本就是 `None`（`horizon_runtime.py:310-311`）。 | `hold_horizon.py:147-155`、`horizon_runtime.py:307-313` | §2.4.2 |
| **P0-3** | **运行期验收卡没有一条活的数据通道**。设计把运行期落点定在 `group_status().acceptance`，但前端只在**挂载时拉一次** `GET /ai-trade/status`，且同组时直接 `return`（`useAiTradeGroupStream.ts:80-85`）；此后所有实时更新都走**成员级** WS。⇒ 卡片会永久停在会话启动那一刻（几乎必然是 `null` / `insufficient`）。更危险的是：若实现者顺手加一个轮询去调 `setAiTradeGroup(groupStateFromStatus(...))`，会**每次轮询把成交流 `trades` 清空**（该映射写死 `trades: []`）。 | `useAiTradeGroupStream.ts:31-90` | §2.4.3 / §2.5 |
| **P0-4** | **`exitMix` 只挂在 `acceptance` 内，而 `acceptance` 在 `rule is None` 时为 `None`**——这与 §2.3.3 专门论证的「退出账本必须与周期模式是否装配无关，因为关掉周期模式跑对照臂正是 L1 最需要做的事」**直接矛盾**：对照臂会记下数据却在 UI 上一个字都看不到。同时实盘（嵌套 `acceptance.exitMix`）与历史（§5.3 的顶层 `aiStats.exits`）是**两种形状**，违反「三处共用同一对组件、同一份 props 类型」。 | 本文档 §2.3.3 vs §2.4.3 / §2.4.4 / §5.3 自相矛盾 | §2.3.3 / §2.4.3 / §2.4.4 / §2.5 |

### P1（必须修：会让读数失真、门禁空转或实现走偏）

| # | 结论 | 证据 | 闭合位置 |
|---|---|---|---|
| **P1-1** | **验收卡把「近 200 笔」的成本与「全程」的持仓/资金费混成一张卡**。`AttributionLedger._trips` 是 `deque(maxlen=200)` 的环形缓冲（`attribution.py:19,44`），故 `cost.grossPnlUsd/feeUsd/netPnlUsd/costRatio` 都是**滚动窗口口径**；而 `HorizonRuntime.hold_samples` / `round_trips` 与资金费账本都是**全程累计**。超过 200 笔往返的会话——恰恰就是本卡要抓的高换手失败态——第 4、5 行会悄悄换成另一个窗口。 | `attribution.py:19,44,95-121` | §2.4.1 / §2.4.2 / §3.2 / §6.1 A-6 |
| **P1-2** | **`build_input(horizon_stats: dict)` 与「原始计数器可精确合并」自相矛盾**。`HorizonRuntime.stats()` 只下发 `avgHoldSeconds`（round 3 位）与 `withinTargetRate`（round 4 位），**反推不出** `hold_seconds_sum` 与 `within_target_samples`；按比例反乘会引入舍入误差，多成员合并后误差累积。§3.2 已经写了要新增 `acceptance_input_parts()`，但 §4.5 的签名没跟上。 | `horizon_runtime.py:289-313`、本文档 §3.2 vs §4.5 | §4.5 / §2.4.1 |
| **P1-3** | **`wall_seconds` 在三个世界的定义整篇缺失**。它是本轮唯一新增的「能否掉东西」的判据的分母，口径不钉死等于把这条判据交给实现者即兴发挥；回测世界尤其危险（模拟跨度 vs 真实耗时、含不含暖机段，差一个量级）。 | 本文档 §2.4.1 / §2.4.3 均未定义 | §2.4.1 |
| **P1-4** | **回测侧资金费计提挂在 `_track_horizon_epoch` 里会被周期模式的开关连坐**：该方法首行就是 `if self.horizon_rule is None: return`（`backtest.py:518-519`）。⇒ 没装周期模式的 AI 回测拿不到任何资金费，而「装 vs 不装的对照回测」正是 L1 要做的事。 | `backtest.py:511-519` | §2.2.4 |
| **P1-5** | **§2.2.4 把 `backtest.py:548-549` / `:626` 认成「回测资金费恒为 0」的现场，实际是成本模型的入参**——那两处是 `query_for_preview` / `query_for_decision` 的 `funding=None, funding_enabled=False`，喂的是 **G3 换手迟滞与 edge 门禁的成本估算**。照字面去「修」它们会当场改变门禁判定，与本轮 G-2 冻结门禁和「只加观测」的非目标正面冲突。 | `backtest.py:544-556`、`backtest.py:620-628` | §2.2.4 / §九 D-8 |
| **P1-6** | **合成口径会在结算延迟窗口内让资金费凭空缩水**。`fundingCostUsd = settledUsd + accruedOpenUsd` 一旦 `settledUsd` 可得就整个丢掉 `accruedClosedUsd`；而 `reconcile.py` 自己写明**刚平的那笔在 `positions-history` 上有结算延迟**（`build_reconciliation` 的 `final` 参数注释）。于是每平一笔仓，资金费读数会先跳水再补回——一个会「倒退」的累计成本读数比不显示更糟。 | `reconcile.py:120-127`、`reconcile.py:139-152` | §2.2.1 / §6.1 F-11 |
| **P1-7** | **`settledUsd` 的可得性有两道文档未提的门**：`ai_trade_reconcile_enabled` 与 `creds is not None`（`cost_runtime.py:381-387`）——模拟盘 / 无凭证会话**恒为 `accrued`**。另有一处更隐蔽的连坐：`FundingCache` 的构造条件是 `ai_trade_funding_enabled and (k_min > 0 or ai_trade_cost_awareness_enabled)`（`cost_runtime.py:61-65`），新总闸 `ai_trade_funding_ledger_enabled` **不在这个析取式里** ⇒ 运维关掉轴 D 的成本意识就会把资金费数据源一起静默关死，而验收卡只会显示「口径不可得」，不会说出原因。 | `cost_runtime.py:61-65,381-387` | §2.2.1 / §2.2.3 / §5.1 |
| **P1-8** | **回测收尾平仓 `session_end` 不在 8 值枚举里**（`SimBroker.finalize` → `_close_at(close, ts, "session_end")`），`trend_hold` 下**每次回测必有一笔**；它会落进 `unknown`，让回测的退出分布凭空多一个「未记录来源」。更要紧的是：它与 `sessionStop` 一样是**被区间边界截断**的持仓样本，会系统性压低第 1、2 行——把一次「持仓还没走完」误判成「持仓太短」。 | `engine/simulator.py:226-234` | §2.3.1 / §2.4.1 / §2.4.2 |
| **P1-9** | **历史·详情是按成员（run）落库的，多标的分组没有合并落点**。`metrics.aiStats.horizonAcceptance` 是单 run 口径，而 `build_acceptance` 的产物是**已判定的卡**，前端无法合并；`merge_inputs` 只在后端存在。⇒ 一个 3 标的分组的历史页要么挂 3 张互相矛盾的卡，要么什么都不挂。变更计划里也漏了承载合并的文件。 | `api/ai_auto.py:330-341`、`aiTradeHistoryTypes.ts:112-137` | §2.4.3 / §3.2 |
| **P1-10** | **前端类型落点写错了两处**：(a) `AiTradeHistoryAiStats` 这个类型**不存在**——历史 aiStats 在 `apps/web/lib/aiTradeHistoryTypes.ts` 的 `AiTradeRunMetrics["aiStats"]` 里，不在 `aiTradeTypes.ts`；(b) §2.5 让去补 `store.ts` 的逐键合并，但 `patchAiTradeMemberStatus` 实际是**整体 spread**（`store.ts:904-913`，`{...m.session, ...status}`）本就不吞新键，真正逐键手抄的是 `groupStateFromStatus`（`useAiTradeGroupStream.ts:41-56`）。验收用例守错了对象就等于没守。 | `aiTradeHistoryTypes.ts:76-108`、`store.ts:904-913`、`useAiTradeGroupStream.ts:41-56` | §2.5 / §3.4 / §6.2 |
| **P1-11** | **§2.3.2 的代码片段里 `self.settings` 在 `AiTradeSession` 上不存在**。会话读配置一律走 `self.executor.settings`（`session.py:171/1017/1059/1365` 等全部如此）。本文档 §4.5 自称「下游按此实现，不再二次决策」，片段会被逐字抄进去 ⇒ 首个平仓成交当场 `AttributeError`，而它跑在 `_dispatch` 的成交收口上。 | `session.py:171,1017,1365` | §2.3.2 |

### P2（记录项，不阻断放行）

| # | 结论 | 处置 |
|---|---|---|
| P2-1 | `GET /ai-trade/status` **没有 `response_model`**（`api/ai_auto.py:291-295` 返回裸 `dict`），§4.2 「因为该端点是强类型 Pydantic 出参」的理由不成立。 | 已在 §4.2 改正理由；结论（恒下发 `null`）保留 |
| P2-2 | §2.3.2 用 `simulator.py:195-197` 的 `any(o.action == "close")` 论证单槽安全——那条去重**只在 `max_hold` 分支**，`submit()` 并没有。单槽真正安全的理由是 `_close_at` 在 `pos.side == "flat"` 时空转，故一根内至多产出一条 close `TradeRecord`。 | 已在 §2.3.2 换成正确论据 |
| P2-3 | 第 3 行门槛 `≤ 86400 / target_hold_min_s` 在 `hourly_swing`（下界 360s）下等于 **240 次/日**，实质恒真。 | 已在 §2.4.2 加一条硬上限并要求 UI 明示该行何时不构成有效门槛 |
| P2-4 | 计提用的 `mark_px` 应取会话既有的 `self._current_px()`（判定价同源纪律），而不是 `_latest_mark_px()` 的返回值——后者在无 K 线时**回落 `entry_price`**（`session.py:563-571`），会让名义额悄悄按开仓价计。 | 已在 §2.2.3 钉死取值 |
| P2-5 | `_MAX_SETTLEMENTS` 触顶后「只取最近那批」与「本来就没跨那么多点」在 wire 上不可分。 | 已在 §2.2.2 加 `capHits` 观测键 |
| P2-6 | `_fetch_position_history` 取 `limit=100` 且**无分页**（`cost_runtime.py:273-275`），长会话的 `settledUsd` 会被静默截断。 | 已在 §2.2.1 要求 `samples >= 100` 时降级 `basis` 并出 note |
| P2-7 | §6.4 的 L0 判据「`unknown` 占比 = 0」与 R-11「explain 合成是 best-effort、少数成交落 unknown」自相矛盾——一个按设计必然会被违反的判据会训练运维忽略它。 | 已在 §6.4 改为「< 2% 且每笔可在日志定位」 |
| P2-8 | 本地计提只覆盖**会话生命周期内**的结算点（非目标①），而 `settledUsd` 来自 `positions-history` 的**整个持仓**，可能含会话启动前的资金费；两个来源时间边界不同。 | 已在 §2.2.1 写明并要求 note 披露 |

---

## 一、概述

### 1.1 要解决的问题

上一轮（`ai-trade-holding-horizon-modes`）已经把调查列出的 8 条核心约束里的 #1～#6 落地：G1 最短主动
平仓窗口、G2 离场连续确认、G3 换手迟滞、prompt 抢跑偏置矫正、成本与先验前瞻窗口改由真实目标持仓
驱动。合入后 Agent 不再能在开仓两分钟后随手平掉一笔仓。

但调查原文的最后一段是这么收尾的：

> 建议先采用「4H + 唤醒周期 0 + 低杠杆」做净额回测，验收指标**至少包含平均持仓时间、每日换手次数、
> 手续费占毛利润比例、资金费后净收益和不同退出来源**。没有通过样本外回测前，不宜直接放大实盘资金。

对着仓库逐条核实，这五个验收指标当前的可得性是：

| # | 验收指标 | 现状 | 证据 |
|---|---|---|---|
| 1 | 平均持仓时间 | ✅ 已有 | `horizon_runtime.py:300` `avgHoldSeconds` |
| 2 | 每日换手次数 | ⚠️ **只有「在场口径」，且与 #1 代数等价** | `horizon_runtime.py:315-323`：`turnoverPerDay = n /(Σhold/86400) = 86400/avgHold`——它恒等于平均持仓时长的倒数，**不是一个独立读数**，无法回答「这套配置一天开平几次」 |
| 3 | 手续费占毛利润比例 | ✅ 已有 | `attribution.py:113` `cost.costRatio = fee / Σ|gross|` |
| 4 | **资金费后净收益** | ❌ **不存在** | 本地净额**按定义不含资金费与强平罚金**（`reconcile.py:10-13`）；回测侧则是**整条资金费账根本不存在**——`SimBroker` 没有任何资金费扣减路径（`engine/simulator.py` 全文无 funding），故回测净额恒等于「毛额 − 双边手续费」。**注意区分**：`backtest.py:548-549` / `:626` 的 `funding=None, funding_enabled=False` 是**成本模型的入参**（G3 迟滞与 edge 门禁用的往返成本估算），不是资金费记账路径，本轮**不动它们**（[P1-5]，见 §2.2.4 第 0 点与 §九 D-8） |
| 5 | **不同退出来源** | ❌ **不存在** | 全仓库 grep 无 `exitSource` / `exit_source`；退出路径只散落在 6 个 explain kind 里，从未被聚合过 |

第 4、5 两项的缺失不是「少了两个数字」，而是**让上一轮的成果无法被证伪**：

- **缺资金费 ⇒ 越长的持仓，账面越假。** 本轮主推的两个模式（`short_swing` 12–48h、`trend_hold`
  1–3d）恰好横跨 3～9 个资金费结算点。`trend_hold` 下一笔 5× 杠杆的持仓，按 0.01%/8h 的常见费率，
  3 天资金费约等于 **0.27% 的名义额、1.35% 的保证金**——与调查里那笔 3.06 USDT 手续费同一量级。
  当前实盘看板与回测结果**都把它记为 0**。也就是说：上一轮为了少付手续费而拉长持仓，换来的资金费
  成本在系统里**根本没有落点**，「持仓拉长是否真的更划算」这个问题现在无法回答。
- **缺退出来源 ⇒ prompt 偏置矫正的效果不可观测。** 上一轮改了三处文案（`profit_guard_text` /
  `prompts` / `decider`），把「主动 close 永远优先于等待保护层」改成「让保护线执行退出同样正确」。
  这条改动的**唯一**验收方式就是「保护层平仓占比是否上升」——而这个数今天算不出来。调查里那组
  「16 次往返、15 次 Agent 主动平仓、唯一一次保护层平仓反而净赚 1.87 USDT」的对比，正是靠人肉翻
  流水才得到的；它必须变成一个每次运行都自动产出的读数，否则下一轮又只能靠人肉复盘。

此外还有一个**上一轮遗留的口径缺陷**（上表 #2）：`turnoverPerDay` 的分母是「在场时长」，这是当时
刻意的选择（避免空仓等待稀释读数），但它导致该指标与平均持仓时长在代数上完全等价，作为一个独立的
验收判据是**恒真的**——一个永远不会否掉任何东西的门槛，比没有门槛更危险。

### 1.2 要建的东西

三条轴，一张卡：

**轴 A · 资金费真值化。** 新增会话级资金费账本（纯核心 `funding_ledger.py`）：按持仓 epoch 逐个
结算点计提本地资金费；平仓后以交易所 `positions-history.fundingFee` 为准（**这份数据仓库每 300 秒
已经在拉了**，见 `cost_runtime.py:398` → `reconcile.py:149` `exchangeFundingFeeUsd`，**零新增网络
调用**）。回测侧新增公共端点 `funding-rate-history` 取真实历史费率曲线，与实盘同一纯核心计提。
资金费**只作为独立一项账呈现，绝不回写 `trade["pnl"]`**——沿用阶段一「交易所读数只进归因、不回写
盈亏」的既定纪律（`reconcile.py:5-6`）。

**轴 B · 退出来源归因。** 定义 8 值枚举（`agent` / `agentReverse` / `profitGuard` / `exchangeOco` /
`timeExit` / `sessionStop` / `external` / `unknown`），在**单一收口处打标**（`session._inject_explain_meta`，
与既有 `meta.wakeKind` 完全同款），聚合出「每种退出的笔数 / 净额 / 平均持仓时长 / 胜率」。

**轴 C · 周期验收卡。** 一个纯核心 `horizon_acceptance.py`，把上面三轴 + 既有 horizon/cost 统计
汇成**一张五行判据卡 + 一张退出来源分布表**，回测与实盘**同一个 builder、同一个 wire 形状**，
分组多标的按**原始计数器可加合并**后再判定一次。前端两处（运行期看板 / 历史详情）+ 回测结果面板
共用同一个组件。

### 1.3 为什么这样切

1. **切口对齐调查原文，不自造需求。** 三条轴严格对应调查里点名的验收指标 #4、#5 和「先做净额回测
   再放大资金」这条放行纪律，没有一条是从现象跳出来的臆测。
2. **零新增私有端点、零 DB 迁移。** 资金费的交易所真值复用**已经在跑**的对账取数；退出来源复用
   **已经在落库**的 `explain` JSON；验收卡落在 `metrics_json` 的加法键上。整轮不新增一张表、不加一列。
3. **不碰共用链路。** `LiveExecutor` 与 `SimBroker` 被 AI / live_auto / 全部策略回测共用，是本仓库
   反复出事的地方（见上一轮非目标第 4 条）。本轮**一行都不改它们的既有分支**：资金费不进权益曲线、
   不进 `trade["pnl"]`；退出来源靠一个纯加法的 explain 键传递。
4. **判据必须能否掉东西。** 顺手修掉 §1.1 表里 #2 那个恒真指标：新增**墙钟口径**的每日换手次数
   （分母 = 会话在线时长，含空仓等待），它才是成本相关的那个换手率，且与平均持仓时长**互相独立**。

---

## 二、技术设计

### 2.1 架构位置与数据流

```
                    ┌───────────────────────────────────────────────┐
   5s 盯市 tick ───▶│ CostRuntime.schedule_maintenance(mark_px=…)   │
                    │  ├ FundingCache.refresh   （公共端点，已有）  │
                    │  ├ FundingLedger.accrue   （★ 轴 A 新增，纯算）│
                    │  ├ FeeRateCache.refresh   （私有，已有）      │
                    │  └ reconcile_tick         （私有，已有）──────┼──▶ last_reconcile
                    └───────────────────────────────────────────────┘        .exchangeFundingFeeUsd
                                                                                    │（轴 A 真值）
   executor 事件 ──▶ session._dispatch  （全部成交的唯一收口）                       │
        │              ├ _cost.note_open / record_close        （已有 + 轴 A 挂点）  │
        │              ├ _horizon.note_open / note_close        （已有）             │
        │              │     └─▶ ExitLedger.record(source, hold_s, pnl)  ★ 轴 B      │
        │              └ _inject_explain_meta                                        │
        │                    └─▶ meta["exitSource"] = classify_live(explain)  ★ 轴 B │
        ▼                                                                            │
   HorizonRuntime.stats() ─┐                                                         │
   AttributionLedger.stats()├──▶ horizon_acceptance.build_input(...) ──▶ AcceptanceInput
   FundingLedger.stats() ───┘                     （★ 轴 C 纯核心，可加）            │
   ExitLedger.stats() ──────┘                                    │                   │
                                                                 ▼                   │
                       manager.group_status() ──▶ merge_inputs([成员…]) ──▶ build_acceptance()
                                                                 │                   │
                        ┌────────────────────────────────────────┴──────────┐        │
                        ▼                     ▼                             ▼        │
              group_status().acceptance   metrics_json.aiStats        回测 aiStats    │
                   （运行期看板）        .horizonAcceptance（历史）  .horizonAcceptance
```

三条不变式贯穿全设计：

1. **资金费永不回写盈亏。** 它只以独立键呈现，并由验收卡做一次显式减法得到 `netAfterFundingUsd`。
   理由与 `reconcile.py:5-6` 完全一致：把交易所读数灌回本地盈亏会让「盈亏正确性 = 网络可用性 ×
   持仓模式假设」。
2. **每个量只算一次。** 退出来源在 `_inject_explain_meta` 打一次标，此后落库、看板、回测、导出
   全部**读**这个标；绝不在前端再写一遍分类逻辑（这正是 `edgeSource` 与 `aiTradeObservability.ts`
   踩过的坑）。
3. **不知道就说不知道。** 资金费结算周期取不到 ⇒ **不计提**（绝不按 8h 猜），验收卡该行显示
   「口径不可得」；样本不足 ⇒ 验收结论是 `insufficient` 而**永远不是** `pass`。
   **[P0-1] 这条不变式当前在取数链上是被架空的，必须先把它修回来**：`rest_client.get_funding_rate`
   在两个结算时点相减 `span <= 0` 时**静默兜底 8h**（`rest_client.py:284`），`cost_model.py:234` 又
   把 `funding_interval_known=True` 写死。于是 `FundingSnapshot.interval_s` 永远是正数，本层写多少
   个「`interval_s <= 0` ⇒ 不计提」的分支都是死码。修法与传播边界见 §2.2.1「结算周期已知性」。

### 2.2 轴 A：资金费真值化

#### 2.2.1 口径与符号约定（先钉死，后面全篇按此）

- **符号：支出为正。** 与 `okx/trading.py:576` 的归一保持一致（该处已把 OKX 的「负数=支出」取反）。
  因此 `netAfterFundingUsd = netPnlUsd − fundingCostUsd`。
- **多空极性：** OKX 永续 `fundingRate > 0` ⇒ 多头付给空头。故
  `单次支出 = rate × notional × (+1 if side == "long" else −1)`。
- **名义额：** 结算按标记价名义额计，取 `qty_coin × mark_px`（`qty_coin = Σ 成交张数 × ctVal`）。
  实现取**结算时刻最近一次可得的标记价**（盯市 tick 的 `mark_px`）；回测取结算点所在那根 K 线的收盘价。
- **结算周期已知性（[P0-1]，本轮必须新建的一条信息通路）：**
  `interval_s` 只有「取到」和「没取到」两种状态，而当前链路把「没取到」翻译成了 8h。修法是**加法**、
  不动任何既有键的值：
  1. `OKXRestClient.get_funding_rate` 的返回 dict 追加 `"intervalKnown": bool(span > 0)`；
     `"intervalS"` 的既有兜底值**保持不变**（改它会连带改写 `cost_model` 的资金费期数与 prompt 文案，
     越过本轮「只加观测」的边界，并打破 G-1 逐字节冻结）。
  2. `FundingSnapshot` 追加 `interval_known: bool = False`，`FundingCache.refresh` 从 `row` 填。
     `wire()` 同步追加该键（trace 排查要能看见）。
  3. **只有 `FundingLedger` 认这个键**：`interval_known is False` ⇒ 本次**不计提**、记一次
     `unknownIntervalTicks`、`intervalKnown` 下发 `false`、验收卡第 5 行判 `insufficient`。
     `cost_model` 一行不改（它继续按 8h 保守估算成本，这是它自己的既有口径）——两层对同一个未知
     给出不同处置是**有意的**：成本估算宁可保守高估，记账宁可不记，绝不猜一个数当真值。
     该不对称在 §九 D-9 登记为拍板项，并在 `.env.example` 的注释里写明。
- **两个来源，永不混淆：**
  - `accrued`（本地计提）：实时可得，覆盖**当前未平仓段**与取不到交易所读数时的兜底；
  - `settled`（交易所结算）：`positions-history.fundingFee` 之和，只覆盖**已结算持仓**，是权威值。
  - **合成口径（[P1-6] 修正）：**
    ```
    settled 可信 ⇒ fundingCostUsd = max(settledUsd, accruedClosedUsd) + accruedOpenUsd
    否则        ⇒ fundingCostUsd = accruedClosedUsd + accruedOpenUsd，basis = "accrued"
    ```
    「settled 可信」= 最近一次对账 `status ∈ {"ok", "drift", "partial"}` **且** `coverageMismatch`
    为假 **且** `samples < 100`。取 `max` 而不是直接用 `settledUsd` 的理由：`reconcile.py` 自己写明
    **刚平的那笔在 `positions-history` 上有结算延迟**（`build_reconciliation` 的 `final` 参数注释），
    直接替换会让每平一笔仓时资金费先跳水再补回——一个会**倒退**的累计成本读数比不显示更糟。
    `max` 保证该读数**单调不减**（F-11 用例守它）。
  - **`settledUsd` 的可得性有三道门（[P1-7]，文案必须说出是哪一道没过）：**
    `ai_trade_reconcile_enabled` 关 / `creds is None`（模拟盘、无凭证会话）/ 未到对账节流间隔
    （`cost_runtime.py:381-387`）。三者任一不满足 ⇒ `basis="accrued"` 且 `note` 明写
    「未接入交易所结算读数（模拟盘或对账未启用），资金费为本地计提值」。**绝不**让运维在一个恒
    `accrued` 的会话上去比对 L2 的「实盘 vs 回测 netAfterFunding 差异」。
  - **两个来源的时间边界不同（P2-8）：** 本地计提只覆盖**会话生命周期内**的结算点（非目标①），
    而 `positions-history` 的 `fundingFee` 是**整个持仓**的（可能开仓于会话启动之前）。`max` 合成
    在这种会话上会偏保守（偏大）。note 须披露：「交易所结算口径覆盖该持仓的完整生命周期，可能含
    会话启动前的结算点」。
  - **截断（P2-6）：** `_fetch_position_history` 取 `limit=100` 且**无分页**。`samples >= 100` ⇒
    视为可能截断 ⇒ `basis` 降为 `"accrued"` 并出 note，**绝不**拿一份被截断的合计当权威值。
  - **任何一次退化都必须改 `basis` 字段并在文案里说出来**，绝不静默替换。

#### 2.2.2 纯核心 `funding_ledger.py`（新建，无 IO、可单测、绝不抛）

```python
# 结算点平铺的最大数量：坏读数（interval 极小）下防止一次 tick 造出上万次计提。
_MAX_SETTLEMENTS = 64

@dataclass(frozen=True)
class FundingWindow:
    """一次计提所需的全部外生量（全部来自 FundingSnapshot，本模块不发任何请求）。"""
    rate: float            # 当期资金费率（小数，非 bps）
    anchor_ts: int         # 交易所给的 nextFundingTs（秒）
    interval_s: float      # 交易所给的 intervalS（秒）
    # [P0-1] 结算周期是否**真的**来自交易所。`False` ⇒ 本模块一律不计提。
    # 绝不用 `interval_s <= 0` 当判据——上游 `rest_client.py:284` 会把未知兜底成 28800，
    # 那个判据在生产里恒不成立，写了等于没写。
    interval_known: bool = False

@dataclass
class FundingEpoch:
    side: str              # long | short
    seq: int               # 与 HorizonRuntime 同源的持仓身份（side, seq）
    qty_coin: float        # 累计成交数量（币本位；部分成交累加，不重置）
    opened_wall: float
    settled_through_ts: int = 0    # 已计提到的最后一个结算点（0 = 尚未计提过）
    accrued_usd: float = 0.0       # 支出为正
    crossings: int = 0

def settlement_points(*, from_ts: float, to_ts: float, window: FundingWindow) -> list[int]:
    """(from_ts, to_ts] 内的结算时刻升序列表。

    `anchor_ts` 是交易所给的**下一次**结算时刻，按 `interval_s` 向前后平铺即得整条网格。
    `window.interval_known is False` / `interval_s` 非有限 / 越出 [min_interval, max_interval]
    合理区间 ⇒ 返回 `[]`——**不知道结算周期就不计提**（调查约束 #7 原文：不能固定假设结算频率）。
    平铺数量硬封顶 `_MAX_SETTLEMENTS`，超出即只取**最近的**那批，并由调用方记一次 `capHits`
    （P2-5：不记的话，「只取了最近 64 个」与「本来就只跨了这么多」在 wire 上无法区分）。
    """

def accrue(epoch: FundingEpoch, *, window: FundingWindow, now_wall: float,
           mark_px: float) -> tuple[FundingEpoch, int]:
    """把 (settled_through_ts, now_wall] 内跨过的结算点逐个计提，返回 (新 epoch, 本次跨点数)。

    首次调用（settled_through_ts == 0）时把起点取为 `opened_wall`——开仓那一刻之前的结算点
    与本仓无关。`mark_px` 非正 / 非有限 ⇒ 本次不计提且**不推进** `settled_through_ts`
    （下一次 tick 会补上；绝不因为一次坏读数永久丢掉一个结算点）。
    """
```

会话级账本：

```python
class FundingLedger:
    """一个会话的资金费账本；`enabled=False` ⇒ 全部方法空转、`stats()` 恒 None（逐字回退）。"""

    def __init__(self, *, enabled: bool, ct_val: float,
                 min_interval_s: float, max_interval_s: float) -> None: ...

    def note_open(self, trade: dict, *, seq: int, now_wall: float) -> None:
        """开仓成交：建/续 epoch。身份判据 `(side, seq)` 与 HorizonRuntime 逐字相同；
        **部分成交累加 qty_coin 而不是重置**（重置会把已计提额清零，等于漏记）。"""

    def note_close(self, trade: dict, now_wall: float) -> None:
        """平仓成交：把本 epoch 的 accrued 结转进 `closed_accrued_usd`，清空 epoch。"""

    def accrue(self, snapshot, *, now_wall: float, mark_px: float) -> None:
        """盯市挂点；`snapshot is None`（未取到 / 已陈旧）⇒ 本次不计提并记一次 `stale_ticks`。"""

    def stats(self, *, settled_usd: float | None) -> dict | None: ...
```

`stats()` wire（`aiStats.funding` / `costSnapshot.funding` 共用）：

```jsonc
{
  "enabled": true,
  "basis": "settled+accrued",        // settled+accrued | accrued | unavailable
  "accruedClosedUsd": 0.8421,        // 已平仓段本地计提合计（支出为正）
  "accruedOpenUsd": 0.1903,          // 当前持仓段计提；空仓 ⇒ null
  "settledUsd": 0.8397,              // 交易所结算合计；不可得 ⇒ null
  "fundingCostUsd": 1.0300,          // §2.2.1 的合成口径；三源全不可得 ⇒ null
  "crossings": 5,                    // 本地计提跨点次数
  "intervalKnown": true,             // [P0-1] 取自 FundingSnapshot.interval_known，**不是** intervalS>0
  "intervalS": 28800,                // intervalKnown=false 时下发 null（绝不把兜底值当读数展示）
  "lastRateBps": 1.0,                // 最近一次快照费率（bps，展示用）
  "staleTicks": 3,                   // 因快照陈旧而跳过计提的 tick 数（诚实度读数）
  "unknownIntervalTicks": 0,         // [P0-1] 因结算周期未知而跳过计提的 tick 数
  "capHits": 0,                      // P2-5：_MAX_SETTLEMENTS 触顶次数（>0 ⇒ 计提可能少记）
  "settledTrusted": true,            // [P1-6] settled 是否通过三道可信判据（false ⇒ basis 已降级）
  "note": "已按交易所结算口径计入 0.84，当前持仓另计提 0.19"
}
```

> **`fundingCostUsd` 为 `null` 与为 `0` 是两个结论**，前者是「没算成」，后者是「真的没跨过结算点」。
> 验收卡的 `netAfterFunding` 行在 `null` 时判 `insufficient` 而非 `pass`。

#### 2.2.3 会话接线（`session.py` 净增预算 ≤ 8 行）

`session.py` 已 **1727 行**、远超 800 行红线（上一轮 I-7 / G-5 已登记该债）。本轮**不得**再把业务
状态塞进会话：`FundingLedger` 由 **`CostRuntime` 持有**——它已经持有 `FundingCache`
（`cost_runtime.py:61`）、`AttributionLedger`、`last_reconcile`，是资金费三个输入的天然汇合点。

| 挂点 | 改动 | 净增行 |
|---|---|---|
| `CostRuntime.__init__` | 构造 `self.funding_ledger = FundingLedger(...)`；**同时把 `ai_trade_funding_ledger_enabled` 并进 `FundingCache` 的 `enabled` 析取式**（`cost_runtime.py:61-65`，[P1-7]）——否则本层的总闸开着、数据源却被轴 D 的开关连坐关死，卡片只会说「口径不可得」而不会说是被谁关的 | 0（在 cost_runtime） |
| `CostRuntime.schedule_maintenance(..., mark_px: float \| None = None)` | 新增关键字形参；**在排后台任务之前同步**调用 `self.funding_ledger.accrue(self.funding.snapshot(now_wall), now_wall=now_wall, mark_px=mark_px)`（纯算，零 IO，绝不进后台任务——后台批次有 30s 上界且可能整批超时被丢弃，计提丢一次就永久少一笔） | session 侧 **0**（改既有调用点实参） |
| 上一行的实参取值 | **恒取 `self._current_px()`**（P2-4）：会话内「判定价」只能有一个答案，利润保护层与持仓周期层都取它。`_monitor_tick` 局部的 `mark` 来自 `_latest_mark_px()`，其兜底分支会**回落 `entry_price`**（`session.py:563-571`），拿它算名义额等于悄悄按开仓价计 | 0（同上一行） |
| `CostRuntime.note_open(trade, *, seq, now_wall)` | 新方法，转发给账本 | session `_dispatch` else 分支 **+1** |
| `CostRuntime.record_close(trade)`（已有） | 内部追加 `self.funding_ledger.note_close(trade, time.time())` | session **0** |
| `session._status_payload()` | 加 `"acceptance": self._acceptance_wire()`（轴 C，见 §2.4.3） | **+1** |
| `session.acceptance_input()` | 新薄方法（4 行），供 `manager.group_status()` 合并 | **+4** |
| `session._finalize_run` | `metrics["aiStats"] = self._cost.merge_funding_into(...)`（与既有 `merge_into` 同款） | **+1** |

合计 **+7 行**，留 1 行余量。任何超出必须在实现阶段按上一轮 I-7 的姿态登记为欠账并说明。

#### 2.2.4 回测侧：真实历史资金费曲线

现状：`SimBroker` 全文没有任何资金费扣减路径 ⇒ **回测里资金费恒为 0**。在 `trend_hold`（1–3 天，
跨 3～9 个结算点）下这会让回测系统性优于实盘，而这恰恰是 L1 放行门槛所依赖的那份回测。

设计：

0. **[P1-5] 先划清一条容易踩的边界：`backtest.py:548-549` 与 `:626` 的
   `funding=None, funding_enabled=False` 本轮一行都不动。** 那两处是
   `query_for_preview` / `query_for_decision` 的入参，喂的是**G3 换手迟滞与 edge 门禁用的往返成本
   估算**，不是资金费记账。照字面把它们「修」成真实快照会当场改变门禁判定，与 G-2 冻结门禁、以及
   §十「不改动 G1/G2/G3 任何门禁行为」正面冲突。由此产生的「回测的成本估算不含资金费、实盘含」这条
   不对称，本轮**如实登记为已知欠账**（§九 D-8），并在回测 notes 里写明，留给下一轮连同 G3 阈值
   一起重估。
1. **新增公共端点** `OKXRestClient.get_funding_rate_history(inst_id, *, before_ms=None, after_ms=None,
   limit=100) -> list[dict]`，走既有 `self._request("/api/v5/public/funding-rate-history", params)`
   （公共无签名，与 `get_funding_rate` 同姿态，`rest_client.py:259`）。归一为
   `{"fundingTs": int(秒), "fundingRate": float, "realizedRate": float | None}` 并**按 ts 升序**返回。
   分页：按 `after`（更早）翻页，最多 `ai_trade_funding_history_max_pages` 页（默认 8 ⇒ 800 点 ⇒
   8h 周期下约 266 天，足够覆盖任何回测窗口）；取不到即空列表。
   **字段名以线上返回为准**：实现时必须先打一发真实响应核对键名（`fundingTime` / `fundingRate` /
   `realizedRate`），与线上不符即在 notes 里如实降级，**不得**硬编一份猜出来的映射。
2. **回测运行时**在 `_setup` 阶段拉一次覆盖 `[start_ts, end_ts]` 的费率曲线，构造
   `BacktestFundingCurve`（新建于 `funding_ledger.py`，纯核心）：
   ```python
   class BacktestFundingCurve:
       """历史结算点 → 费率的只读查表；相邻两点之差即该期的真实 interval（不假设 8h）。"""
       def __init__(self, rows: list[dict]) -> None: ...
       def points_in(self, from_ts: int, to_ts: int) -> list[tuple[int, float]]: ...
       @property
       def available(self) -> bool: ...
   ```
   > 这里刻意**不复用** `settlement_points` 的平铺逻辑：历史数据自带每一次真实结算时刻，平铺反而
   > 会在交易所动态改周期（1/2/4/8h，调查约束 #7 明写）的那一段造出错位。两条路径的差别必须在
   > 代码注释里写清楚，否则下一个人一定会去「统一」它们。
3. **计提点（[P1-4] 修正）**：**不放进 `_track_horizon_epoch` 内部**——该方法首行就是
   `if self.horizon_rule is None: return`（`backtest.py:518-519`），挂在里面会让「没装周期模式的
   回测」拿不到任何资金费，而**装 vs 不装的对照回测正是 L1 要做的事**。计提独立挂在 `_run_loop`
   里、紧接 `_track_horizon_epoch(...)` 调用之后的一行：
   ```python
   self._track_horizon_epoch(int(row["ts"]))
   self._accrue_funding_bar(int(row["ts"]), float(row["c"]))   # 只受 funding 总闸门控
   ```
   `_accrue_funding_bar` 内部自己维护 epoch（身份取 `(pos.side, pos.entry_bar)`，与
   `_track_horizon_epoch` 同源但**不共享早退条件**），并调用
   `self._funding.accrue_bars(curve, from_ts=上一根 ts, to_ts=本根 ts, mark_px=本根收盘)`。
   门控只有一条：`ai_trade_funding_backtest_enabled` 且 `curve.available`。
4. **绝不进权益曲线。** `SimBroker` 被全仓库所有回测共用（`simulator.py:97`），在其中扣资金费会
   改变**每一个**既有策略回测的结果。资金费只进 `aiStats.funding` 与验收卡的 `netAfterFundingUsd`，
   并由 `_build_notes` 追加一条免责：「资金费未计入权益曲线与 metrics，仅在验收卡以净额单列」。
5. **取不到曲线** ⇒ `available=False` ⇒ `basis="unavailable"`、验收卡该行 `insufficient`，
   notes 写「本次回测未能取到历史资金费，净额未含资金费」。**绝不按 0 呈现**。

#### 2.2.5 prompt 披露（加法，受总闸 + 有持仓双重门控）

`cost_view.build_cost_view` 增加只读键 `fundingAccrued`；`cost_text` 在既有成本段后追加一行：

> 本仓已实际计提资金费 **0.19 USDT**（已跨 1 个结算点，当前周期 8h、费率 1.0 bps/期）。该项是持有的
> 既定成本、已计入下方净额；**它本身不构成离场理由**——离场要另付两笔手续费，只有「持仓理由已失效」
> 才是离场理由。

后半句是**必须**的：单独把一个随时间单调增长的成本摆在模型面前，会造出一条新的抢跑偏置，恰好抵消
上一轮 §2.9 的三处矫正。此句与上一轮的宪章口径一致（`profit_guard_text` 改后文案）。
未装配 / 无持仓 / 账本关 ⇒ **整键缺席** ⇒ 渲染管线整段省略 ⇒ prompt 逐字节回退（冻结断言可证）。

### 2.3 轴 B：退出来源归因

#### 2.3.1 枚举与两个世界的映射（冻结表）

```python
EXIT_SOURCES = ("agent", "agentReverse", "profitGuard", "exchangeOco",
                "timeExit", "sessionStop", "windowEnd", "external", "unknown")
```

**[P1-8] `windowEnd` 是评审新增的第 9 个枚举**，不是凑数：`SimBroker.finalize` 在回测区间末对仍持
仓的头寸按收盘价平仓并记 `reason="session_end"`（`engine/simulator.py:226-234`）——`trend_hold`
（1–3 天持仓）下**每一次回测必然产生一笔**。原 8 值表会把它扫进 `unknown`，让回测的退出分布凭空
多出一个「未记录来源」，而它其实是已知得不能再已知的一类退出。它同时是**被区间边界截断**的持仓
样本（`sessionStop` 同理），必须在验收卡里单独计数，见 §2.4.1 的 `truncated_samples`。

| 枚举 | 中文 | 实盘判据（`explain`） | 回测判据 |
|---|---|---|---|
| `agent` | Agent 主动平仓 | `kind == "atomic"` 且 `side == "close"`（`explain.py:74`） | 决策 `action == "close"` 提交的那笔（打标槽） |
| `agentReverse` | Agent 反手 | `kind == "reverse"`（`explain.py:101`） | 决策 `open_*` 触发的反手平仓腿（打标槽） |
| `profitGuard` | 利润保护层 | `kind == "profit_guard"`（`explain.py:181`） | `_apply_profit_guard_bar` 提交（打标槽） |
| `exchangeOco` | 交易所侧止盈止损 | `kind == "external_close"` 且 `source == "oco"`（`executor.py:555`） | `reason ∈ {"stop_loss", "take_profit"}`（`simulator.py:180-192`） |
| `timeExit` | 强制时间平仓 | `kind == "risk_exit"`（`explain.py:87`） | `reason == "max_hold"`（`simulator.py:197`） |
| `sessionStop` | 会话级止盈损 / 停止 | `kind == "session_stop"`（`explain.py:116`） | `reason ∈ {"session_take_profit", "session_stop_loss"}`（`backtest.py:687-689`） |
| `windowEnd` | 区间 / 会话收尾强制平仓 | 实盘无此路径（会话停止走 `session_stop`） | `reason == "session_end"`（`simulator.py:234`，[P1-8]） |
| `external` | 外部 / 手动 / 强平 | `kind == "external_close"` 且 `source == "external"`（`executor.py:617`） | 回测无此路径 |
| `unknown` | 未记录来源 | 无 explain / 未知 kind / 本特性上线前的历史成交 | 打标槽为空且 reason 不在上表 |

**一处必须的共用文件改动：** `build_external_close_explain`（`explain.py:121-137`）当前把 oco / external
的区别**只写在中文 metric 文案里**（`explain.py:124-127`），机器读不到。改动 = 在返回的 dict 上加一个
**顶层加法键** `"source": source`。判据：

- 该函数被 `live_auto` 与 `ai_auto` 共用，但**加一个键不改任何既有键的值**；`live_auto` 的渲染
  按 `metrics` 数组走，对未知顶层键无感。
- 必须补一条 `live_auto` 冻结用例：除 `source` 外，返回 dict 与改动前**逐键相等**。
- 绝不去解析中文文案做分类——文案是展示层，随时可能改；把展示层当契约是本仓库反复出事的模式。

#### 2.3.2 打标点（单一真源）

实盘：`session._inject_explain_meta`（`session.py:308-326`）——它已经是**唯一**给成交 explain 补
`meta` 的地方，且已有 `meta.wakeKind` 这个完全同款的先例（`session.py:324-325`）。追加：

```python
# 退出来源归因（horizon-acceptance 轴 B）：只给平仓成交打，开仓腿无「退出来源」可言。
# 总闸关 ⇒ 整键缺席（老数据同样缺席，查询侧一律按 "unknown" 处理）。
# [P1-11] 配置**必须**走 `self.executor.settings`——`AiTradeSession` 上没有 `self.settings`
# （全文件一律 `self.executor.settings`，见 session.py:171 / 1017 / 1365）。写错会在第一笔
# 平仓成交上 AttributeError，而这行跑在 `_dispatch` 的成交收口里。
if (trade.get("side") == "close"
        and self.executor.settings.ai_trade_exit_attribution_enabled):
    meta.setdefault("exitSource", classify_live(explain))
```

打完标后：落库（`_persist_trade` → `append_live_trade`，explain 整体入 JSON 列）、WS 扇出、
历史查询、CSV 导出**全部自动带上**，无需任何额外通路。

回测：`AiBacktestRunner` 维护一个**单槽** `self._pending_exit_source: str | None`。凡是运行时**自己**
提交的平仓（`_execute` 的 `close` / 反手、`_apply_profit_guard_bar`、`_maybe_session_exit`）在
`submit` 之前写槽；`_track_horizon_epoch` 观察到新的 close `TradeRecord` 时按
`classify_backtest(reason, stamped=self._pending_exit_source)` 定源并**消费槽**。
SimBroker 自身的盘中止损止盈 / max_hold / 区间收尾（`session_end`）不经运行时，故槽为空、由
`reason` 字面量兜住。

**单槽为什么安全（P2-2，v1 的论据是错的）：** v1 引用 `simulator.py:195-197` 的
`any(o.action == "close" ...)` 去重——那条去重**只在 `max_hold` 分支**，`submit()`（`simulator.py:206-224`）
并没有它，完全可以在已有一笔 pending close 的情况下再排一笔。真正的安全性来自下一层：
`_close_at` 在 `pos.side == "flat"` 时**直接空转**（`simulator.py:298-299`），所以一根内**至多产出
一条 close `TradeRecord`**——单槽与「至多一条待归因的平仓成交」是一一对应的。

#### 2.3.3 `ExitLedger`（`exit_attribution.py`，纯核心）

```python
class ExitLedger:
    """按退出来源聚合的只读账本；无任何样本 ⇒ `stats()` 返回 None（整键缺席）。"""

    def record(self, trade: dict, *, hold_seconds: float | None) -> None:
        """记一笔平仓成交。`pnl` 口径随 `net_mode` 还原（与 attribution.py:64-68 同一条规则，
        绝不在此另写第三种口径）。`hold_seconds is None`（未装配 horizon / 估算起点）⇒ 该笔
        只进笔数与净额，不进时长均值。"""

    def stats(self) -> dict | None: ...
```

wire：

```jsonc
{
  "totalExits": 16,
  "bySource": [
    {"source": "agent", "count": 15, "share": 0.9375, "netUsd": -2.4741,
     "avgHoldSeconds": 2140.5, "winRate": 0.2667},
    {"source": "profitGuard", "count": 1, "share": 0.0625, "netUsd": 1.8655,
     "avgHoldSeconds": 9820.0, "winRate": 1.0}
  ]
}
```

**[P0-4] `exits` 是三个世界的顶层键，不是 `acceptance` 的私有子块。** v1 把 `exitMix` 只挂在
`acceptance` 里，而 `acceptance` 在 `rule is None` 时整体为 `None`——这与本节下面那段「退出账本必须
与周期模式是否装配无关，因为关掉周期模式跑对照臂正是 L1 最需要做的事」**直接自相矛盾**：对照臂会
老老实实记下数据，然后在 UI 上一个字都看不到。而且 §5.3 已经把历史落点写成顶层 `aiStats.exits`，
两条通道形状不一致，`ExitSourceCard` 就没法「三处共用同一份 props」。定死如下：

| 世界 | 顶层键（`ExitSourceCard` 的唯一数据源） | acceptance 内的镜像 |
|---|---|---|
| 实盘 · 运行期 | `group_status().exits` / `status` 事件的 `data.exits` | `acceptance.exitMix`（**同一个 dict 对象**，由 `build_acceptance` 原样引用） |
| 实盘 · 历史 | `aiStats.exits` | 同上 |
| 回测 | `aiStats.exits` | 同上 |

`funding` 同此办理（顶层 + acceptance 内镜像）。镜像保留的理由：`diagnostics` 与第 5 行的 tooltip
要读它们，卡片自带一份可以少一次 props 穿透；**但它必须是同一个对象引用，不得二次装配**——两处
各算一份就是本仓库反复出事的那类分叉。前端一律读顶层键，`acceptance == null` 时退出来源卡照常渲染。

- 数组按 `count` 降序、同数按枚举序，**保证前端渲染顺序稳定**（否则每次刷新条目会跳）。
- `winRate` 在 `count < 5` 时下发 `null`——沿用 `WakeAttributionCard` 已经立好的诚实性纪律
  （`aiTradeWakeAttribution.ts` 的 `WAKE_ATTRIBUTION_MIN_SAMPLES`），绝不用 2 笔样本冒充统计结论。
- **归属点在 `HorizonRuntime.note_close`**：它是唯一同时握有「本次持仓时长」与「本次平仓成交」的
  地方（`horizon_runtime.py:236-250`，该方法当前刻意不读 trade 字段，本轮开始读）。为此把该方法
  拆成两段，**退出账本的记录必须发生在 `rule is None` 早退之前**：
  ```python
  def note_close(self, trade, now_wall):
      hold_s = self._consume_epoch_hold(now_wall)   # rule is None ⇒ (None, 不改状态)
      self.exits.record(trade, hold_seconds=hold_s)  # 恒记录，与 rule 是否装配无关
  ```
  否则关掉周期模式的会话会连退出来源一起消失——而「关掉周期模式跑一段对照组」恰恰是运维最需要
  做的事（L1 观察窗口的对照臂）。

### 2.4 轴 C：周期验收卡

#### 2.4.1 `AcceptanceInput`：原始计数器，可精确合并

```python
@dataclass(frozen=True)
class AcceptanceInput:
    round_trips: int
    hold_seconds_sum: float
    hold_samples: int
    within_target_samples: int
    # [P1-8] 其中被区间 / 会话边界截断的样本数（退出来源 ∈ {windowEnd, sessionStop}）。
    # **不从样本里剔除**——剔除会造幸存者偏差（专挑走完全程的持仓算平均持仓时间）；
    # 只在第 1 行的 note 里如实披露「含 N 笔被收尾截断的样本，实际持仓时长被低估」。
    truncated_samples: int
    gross_pnl_usd: float                # [P1-1] 全程累计口径，**不是** attribution 的 200 环形窗口
    fee_usd: float
    net_pnl_usd: float
    abs_gross_pnl_usd: float            # feeToGross 的分母（Σ|gross|），与上面三项同窗口
    funding_cost_usd: float | None
    funding_basis: str                  # settled+accrued | accrued | unavailable
    wall_seconds: float                 # 见下方「wall_seconds 的三个世界」
    exit_counts: dict[str, int]
    exit_net_usd: dict[str, float]
    exit_hold_sum: dict[str, float]
    exit_wins: dict[str, int]

def merge_inputs(items: list[AcceptanceInput]) -> AcceptanceInput:
    """多成员（多标的分组）合并。

    **`wall_seconds` 取 `max` 而不是求和**——成员是并发跑的，求和会把一个 2 成员的分组的墙钟
    翻倍，进而把每日换手次数腰斩（一个恰好总是通过的门槛）。其余标量相加、字典逐键相加。
    `funding_basis` 取**最弱**的那一档（任一成员 unavailable ⇒ 合并结果 unavailable），
    绝不让一个取到数的成员替另一个没取到的成员背书。
    """
```

选择「原始计数器 + 可加合并」而不是「各成员各判一次再投票」的理由：比例类指标（费/毛、命中率、
换手率）**不可平均**，按成员平均会得到一个谁都不对应的数。

#### [P1-2] 原始计数器只能取自运行时对象，不能取自 `stats()`

`HorizonRuntime.stats()` 下发的是 `avgHoldSeconds`（round 3 位）与 `withinTargetRate`（round 4 位）
——**反推不出** `hold_seconds_sum` 与 `within_target_samples`。按 `avg × n` 反乘会引入舍入误差，
多成员合并后误差累积，那就与本节标题「可精确合并」互相矛盾了。故：

- `HorizonRuntime` 新增 `acceptance_input_parts() -> dict | None`，**直接返回内部原始计数器**
  （`round_trips` / `sum(hold_samples)` / `len(hold_samples)` / 命中目标区间的条数 / 截断条数），
  未装配 ⇒ `None`。`stats()` 一字不改（它是展示口径，两者分工明确）。
- §4.5 的 `build_input` 形参随之改为吃这个 parts，而不是 `horizon_stats` 字典。

#### [P1-1] 成本三项必须是全程累计，不能是 200 笔环形窗口

`AttributionLedger._trips` 是 `deque(maxlen=200)`（`attribution.py:19,44`），故
`stats()["cost"]` 的 `grossPnlUsd / feeUsd / netPnlUsd / costRatio` 全是**滚动窗口口径**。而持仓
样本与资金费账本是全程累计。超过 200 笔往返的会话——**恰恰就是本卡要抓的高换手失败态**（调查样本
按 3 分钟唤醒推算，一周就能跑过 200 笔）——第 4、5 行会悄悄换成另一个时间窗，而卡片上没有任何东西
提示这件事。修法（加法、不动既有键）：

```python
# attribution.py：四个只增累加器（与环形缓冲并存，互不影响）
self._lifetime = {"trips": 0, "gross": 0.0, "fee": 0.0, "net": 0.0, "absGross": 0.0}
# stats()["cost"] 追加一个加法子块：
"lifetime": {"roundTrips": …, "grossPnlUsd": …, "feeUsd": …, "netPnlUsd": …,
             "absGrossPnlUsd": …, "costRatio": …}
```

`AcceptanceInput` 的四个成本字段一律取 `cost.lifetime.*`；`cost.costRatio`（环形口径）保持原样
只作展示，两者在 ≤200 笔时逐位相等（A-6 用例按此对拍，见 §6.1）。

#### [P1-3] `wall_seconds` 的三个世界（钉死，不留给实现即兴发挥）

它是本轮唯一新增的「能否掉东西」的判据的分母，口径摇摆一个量级这条判据就废了。

| 世界 | 取值 | 理由 |
|---|---|---|
| 实盘 · 运行期 | `time.time() - session.started_ts` | 会话在线墙钟，含空仓等待——这正是墙钟口径要的 |
| 实盘 · 历史 | `run.end_ts - run.started_ts`（`_finalize_run` 里现算并落 `AcceptanceInput`） | 与运行期同一条时间轴，停止那一刻定格 |
| 回测 | `main_df.iloc[-1].ts - main_df.iloc[first].ts` | **模拟跨度**且**从首个决策根起算**：暖机段既不决策也不持仓，算进去会把换手率稀释成一个恒过的数（`first` 就是 `_setup` 已经算好的那个首决策索引） |

三处都必须 `max(1.0, …)` 兜底，`wall_seconds <= 0` ⇒ 第 3 行判 `insufficient` 而非除零。

#### 2.4.2 五行判据（阈值表冻结）

| # | 行 key | 读数 | 判据 | 判 `fail` 的含义 |
|---|---|---|---|---|
| 1 | `avgHold` | `hold_seconds_sum / hold_samples` | `≥ rule.target_hold_min_s` | 持仓仍然太短，铁律层没起到作用（或豁免命中太频繁） |
| 2 | `withinTargetRate` | `within_target_samples / hold_samples` | `≥ min_within_target_rate`（默认 0.60） | 平均值达标但分布散，说明是少数长持仓拉起来的 |
| 3 | `turnoverPerDay` | **墙钟口径** `round_trips / (wall_seconds / 86400)`；同时下发在场口径供对照 | `≤ min(86400 / rule.target_hold_min_s, 24)` | 换手率高于「每笔都只持有目标下界」所能达到的上限 |
| 4 | `feeToGross` | `fee_usd / abs_gross_pnl_usd`（**全程累计口径**，[P1-1]） | `≤ max_fee_to_gross`（默认 0.35） | 手续费吃掉超过 1/3 的毛利——调查样本此值为 **5.23** |
| 5 | `netAfterFunding` | `net_pnl_usd − funding_cost_usd` | `> 0` | 扣完全部成本后仍然亏钱 |

**[P0-2] 目标区间缺失时的行为（必须先于上表判定）。** `HorizonRule.target_hold_min_s` 与
`target_hold_max_s` 的默认值都是 `0.0`，且 `custom` 全默认是**文档化的安全退出口**
（`hold_horizon.py:138-141`）。v1 的公式在这种契约下会：第 3 行 `86400 / 0` 直接
`ZeroDivisionError`（一个观测层的异常打断整条 status 装配），第 1 行 `avgHold ≥ 0` 恒真变绿，
第 2 行的 `withinTargetRate` 本就是 `None`（`horizon_runtime.py:310-311`）。定死：

```
rule.target_hold_min_s <= 0  ⇒ 第 1、3 行 verdict = "insufficient"，
                               value 照常下发、target 显示「未设目标区间」，绝不 pass、绝不除零
rule.target_hold_max_s <= 0  ⇒ 第 2 行 verdict = "insufficient"（没有靶子就没有命中率）
以上任一命中             ⇒ 整卡 verdict 不得为 "pass"（沿用「任一行 insufficient 且无 fail ⇒ insufficient」）
```

阈值来源与刻意的取值说明：

- 第 1、3 行的阈值**全部从周期契约派生**（`rule.target_hold_min_s`），不引入新魔数——换模式即换
  阈值，天然自洽。
- 第 3 行的门槛在数学上是**弱界**（墙钟分母含空仓等待，故实测值恒 ≤ 在场口径）。这是刻意的：
  它守的是「不许出现比契约还密的换手」这条底线，真正吃紧的判据是第 2 行。**必须在卡片 tooltip 里
  写清这一点**，否则运维会把一个宽门槛当成强保证。
- **（P2-3）该弱界在短周期 profile 上会退化成恒真**：`hourly_swing` 的目标下界是 360s ⇒ 纯派生
  阈值等于 **240 次/日**，任何配置都不可能违反。故加一条硬上限 `24 次/日`（每小时一次往返已是
  「小时波段」的物理极限），并要求卡片在
  `86400 / target_hold_min_s > 24` 时在该行 note 里直说「本模式下该行由固定上限 24 次/日 兜底，
  不构成周期契约意义上的门槛」——一个恒真的门槛必须自己承认自己恒真。
- 第 4 行的 0.35 是**唯一**一个经验常数，配置可调（`ai_trade_acceptance_max_fee_to_gross`）。
  取值理由：调查样本 5.23 是灾难，1.0 是「白干」，0.35 给出足以覆盖滑点波动的余量。
- 第 5 行是硬判据，不可配置为 `≤ 0`。

**总判定：**

```
samples < sample_floor(默认 20)                     ⇒ "insufficient"
任一行 fail                                          ⇒ "fail"
任一行 insufficient 且无 fail                        ⇒ "insufficient"
全部 pass                                            ⇒ "pass"
```

`insufficient` **永远不显示为绿色**。这是 L1 放行门槛（「未通过样本外净额回测前不得放大实盘资金」）
唯一的技术保证——一个样本不足就变绿的卡片会直接把这条纪律作废。

#### 2.4.3 装配点

| 世界 | 装配 | 落点 |
|---|---|---|
| 实盘 · 运行期 | `manager.group_status()` 里 `merge_inputs([m.session.acceptance_input() …])` → `build_acceptance()` | `group_status().acceptance` / `.exits` / `.funding` |
| 实盘 · 单会话 | `session._status_payload()["acceptance" / "exits" / "funding"]`（单成员口径） | WS status 事件 |
| 实盘 · 历史 | `session._finalize_run` → `metrics["aiStats"]["horizonAcceptance" / "exits" / "funding" / "acceptanceInput"]` | `metrics_json`（零迁移，加法键） |
| 实盘 · 历史 · 分组 | `db/ai_auto_repositories.get_ai_trade_group_detail` → `merge_inputs([各 run 的 acceptanceInput …])` → `build_acceptance()` | `detail.summary.horizonAcceptance` / `.exits` |
| 回测 | `AiBacktestRunner._aggregate_stats()` → `aiStats.horizonAcceptance` / `.exits` / `.funding` | 回测结果 + 落库 |

五处**共用同一个 `build_acceptance`**，形状逐字节相同。这是 G-3 类等价门禁的前提。

**[P0-3] 运行期卡片必须有一条活的数据通道。** `group_status()` 的产物只经
`GET /ai-trade/status` 下发，而前端**只在挂载时拉一次**、且同组时直接早退
（`useAiTradeGroupStream.ts:80-85`）；此后所有实时更新都走**成员级 WS**。照 v1 实现，运行期验收卡
会永久停在会话启动那一刻（几乎必然是 `null` / `insufficient`）——一张永远不更新的验收卡比没有卡
更危险，它会被当成「一直没通过」。定死两条并行通路：

1. **成员级（主通路，实时）**：`session._status_payload()` 每次 status 事件随附本成员的
   `acceptance` / `exits` / `funding`。成员卡片据此实时刷新。这条通路复用既有的
   `patchAiTradeMemberStatus`——它是**整体 spread**（`store.ts:904-913`），新键天然不会被吞。
2. **分组级（合并口径，轮询）**：`useAiTradeGroupStream` 增加一个 **15 秒**轮询
   `aiTradeApi.status()`（仅当 `accountMode === "live"` 且存在运行中分组），把
   `acceptance` / `exits` / `funding` 三个键**补丁**进 `state.aiTradeGroup`。
   **绝对禁止**在轮询里调 `setAiTradeGroup(groupStateFromStatus(...))`：该映射写死 `trades: []`，
   每轮询一次就会把成交流清空一次（这正是既有代码在同组时早退的原因）。必须新增一个窄 action
   `patchAiTradeGroupAcceptance(patch)`，只覆盖这三个键，**不碰 `members`**。
   轮询周期取 15s 的理由：验收卡的分子是往返笔数，最快也是分钟级变化；`group_status()` 是纯内存
   读取，成本可忽略。

**[P1-9] 历史·详情是按成员（run）落库的，必须在后端合并。** `metrics.aiStats.horizonAcceptance`
是**单 run 口径**，而 `build_acceptance` 的产物是**已判定的卡**——前端无法把 3 张卡合并成 1 张，
`merge_inputs` 只存在于后端。故：

- `_finalize_run` 除了落 `horizonAcceptance`（单 run 卡，成员级排查用），**还要落
  `aiStats.acceptanceInput`**（原始计数器的 camelCase wire，是合并的唯一可能输入）。
- `get_ai_trade_group_detail` 读各 run 的 `acceptanceInput` → `merge_inputs` → `build_acceptance`
  → 放 `summary.horizonAcceptance` 与 `summary.exits`。老记录缺 `acceptanceInput` ⇒ 整键缺席
  ⇒ 历史页不渲染该卡（**绝不**用部分成员的数据冒充整组结论）。
- 该文件（`services/quant/app/db/ai_auto_repositories.py`）必须进 §3.2 变更计划。

#### 2.4.4 wire 形状

```jsonc
{
  "profileId": "short_swing",
  "title": "短波段",
  "verdict": "fail",
  "sampleFloor": 20,
  "roundTrips": 16,
  "truncatedSamples": 1,             // [P1-8] 被区间 / 会话收尾截断的持仓样本数
  "rows": [
    {"key": "avgHold", "label": "平均持仓时间", "verdict": "fail",
     "value": 2140.5, "unit": "seconds", "display": "35m 41s",
     "target": "≥ 12h",
     "note": "低于目标下界，铁律层未生效或豁免命中过频；含 1 笔被收尾截断的样本（时长被低估）"},
    {"key": "withinTargetRate", "label": "落在目标区间的比例", "verdict": "fail",
     "value": 0.0625, "unit": "ratio", "display": "6.3%", "target": "≥ 60%", "note": null},
    {"key": "turnoverPerDay", "label": "每日换手次数（墙钟）", "verdict": "fail",
     "value": 8.4, "unit": "count", "display": "8.4 次/日", "target": "≤ 2.0 次/日",
     "inPositionValue": 40.4, "note": "在场口径 40.4 次/日 —— 分母为在场时长，仅供对照"},
    {"key": "feeToGross", "label": "手续费占毛利润", "verdict": "fail",
     "value": 5.234, "unit": "ratio", "display": "523%", "target": "≤ 35%", "note": null},
    {"key": "netAfterFunding", "label": "资金费后净收益", "verdict": "fail",
     "value": -2.6144, "unit": "usd", "display": "-2.61 USDT", "target": "> 0",
     "basis": "settled+accrued", "note": "= 净额 -2.47 − 资金费 0.14"}
  ],
  // 以下两块是**顶层同名键的同一对象引用**（[P0-4]），前端一律读顶层，此处仅供 diagnostics /
  // tooltip 就近取用；二次装配即视为实现缺陷（W-9 用例断言两处 `is` 相等的序列化结果一致）。
  "exitMix": { /* §2.3.3 的 ExitLedger wire */ },
  "funding": { /* §2.2.2 的 FundingLedger wire */ },
  "diagnostics": [
    "Agent 主动平仓占 93.8%，且手续费已吃掉 523% 的毛利润 —— 抢跑退出仍占主导。"
  ]
}
```

`diagnostics` 是**派生文案**（纯函数、可单测），只在条件成立时出现，条件冻结为三条：

1. `agentShare > 0.8 且 feeToGross > 阈值` ⇒ 抢跑退出仍占主导；
2. `withinTargetRate` 达标但 `netAfterFunding ≤ 0` ⇒ 持仓周期已达标但仍亏损，问题在方向判断而非换手；
3. `funding_basis == "unavailable"` 且 `avgHold ≥ 4h` ⇒ 跨结算点持仓但资金费口径不可得，净额偏乐观。

**`exitMix` 不参与判定**（无 verdict）。理由：保护层平仓占比高本身既不是好事也不是坏事，把它做成
门槛会诱导出「故意不主动平仓等保护层」这种劣化行为。它是**记录项 + 诊断输入**。

### 2.5 前端交互设计

#### 落点

| 位置 | 组件 | 数据源 |
|---|---|---|
| 统计看板 · 「当前」页 | `HorizonAcceptanceCard` | `group.acceptance`（15s 轮询补丁，[P0-3]） |
| 统计看板 · 「当前」页 | `ExitSourceCard` | `group.exits`（**顶层键**，[P0-4]：`acceptance == null` 时照常渲染） |
| 统计看板 · 「历史·详情」页 | 同上两个组件 | `detail.summary.horizonAcceptance` / `detail.summary.exits`（后端已合并，[P1-9]） |
| AI 回测结果面板 | 同上两个组件 | `result.aiStats.horizonAcceptance` / `result.aiStats.exits` |

**三处共用同一对组件、同一份 props 类型**——形状本来就同源，任何一处分叉都会立刻在 tsc 上报错。
两个组件的 props 一律是 `{acceptance: AiTradeAcceptance | null}` 与 `{exits: AiTradeExitMix | null}`
**两个独立入参**，绝不让退出来源卡去 `acceptance?.exitMix` 里掏——那样写 `acceptance` 为空时卡片
就消失了，正是 [P0-4] 要修的那个缺陷。

#### `HorizonAcceptanceCard` 视觉规格

```
┌─ 周期验收 · 短波段 ────────────────────── ⛔ 未通过 ─┐
│                                                      │
│  平均持仓时间          35m 41s      ≥ 12h      ⛔     │
│  落在目标区间的比例      6.3%       ≥ 60%      ⛔     │
│  每日换手次数（墙钟）   8.4 次/日   ≤ 2.0      ⛔  ⓘ  │
│  手续费占毛利润          523%       ≤ 35%      ⛔     │
│  资金费后净收益       -2.61 USDT     > 0       ⛔     │
│                                                      │
│  ⚠ Agent 主动平仓占 93.8%，手续费已吃掉 523% 毛利润   │
│    —— 抢跑退出仍占主导。                              │
│                                                      │
│  样本 16 / 20 —— 未达样本下限，结论仅供参考           │
└──────────────────────────────────────────────────────┘
```

设计约束（对应「美观、优雅、符合人机交互最佳实践」）：

1. **一屏一结论。** 卡头右上角是整卡结论徽标，三态三色：`pass` 用 `--success`、`fail` 用 `--danger`、
   `insufficient` 用 `--muted`（**灰，不是黄**——黄会被读成「快好了」，而样本不足什么都不是）。
2. **每行五列固定栅格**：标签 / 实测值 / 门槛 / 徽标 / 可选 ⓘ。数值列一律 `tabular` 等宽数字，
   否则刷新时数字宽度跳动，是本仓库既有看板已经踩过的观感问题。
3. **门槛与实测并排**，不用进度条：五行的量纲互不相同（秒 / 比例 / 次数 / 比例 / USD），
   同一根进度条会暗示可比性。
4. **`insufficient` 时整卡降饱和**（`opacity-70`）并把样本进度写在卡底，
   **绝不**把任一行渲染成绿色。
5. **ⓘ 只挂在第 3 行**（墙钟 vs 在场口径），tooltip 直接写「分母为会话在线时长（含空仓等待）；
   在场口径 40.4 次/日 仅供对照，它与平均持仓时长互为倒数，不是独立读数」。
6. **未装配周期模式 / 无验收数据 ⇒ 整卡不渲染**，与既有 `HorizonLiveCard`
   （`AiTradeStatsLive.tsx:261-269`）的纪律逐字一致——绝不在一个没有周期契约的会话上挂空卡。

#### `ExitSourceCard` 视觉规格

```
┌─ 退出来源 ─────────────────── 样本 ≥ 5 才给胜率 ─┐
│  ████████████████████████████████████░░  16 笔    │
│  ■ Agent 主动 15 · 胜率 26.7%        -2.47 USDT   │
│  ■ 利润保护    1 · 样本不足           +1.87 USDT  │
│                                                   │
│  平均持仓：Agent 主动 35m / 利润保护 2h 44m       │
└───────────────────────────────────────────────────┘
```

- 顶部一条**堆叠比例条**（每段一色，色值取自现有 `--chart-*` 变量，不新增设计 token）。
- 净额列沿用 `colorForReturn` + `signed`（与 `WakeAttributionCard.tsx:47-51` 逐字同款）。
- `unknown` 段固定为最后一段、灰色、标签「未记录来源」，并在只有 `unknown` 时**整卡不渲染**
  （老会话不该看到一张全是「未记录」的表——同样是 `WakeAttributionCard` 已立的纪律）。

#### store / 类型接线（防「逐键手抄静默吞新键」复发）

**[P1-10] v1 这一段点错了两个对象，按错的实现会造出一条守不住任何东西的验收用例。** 逐字核实后
的实际情况：

| 位置 | 实际写法 | 会不会吞新键 |
|---|---|---|
| `useAiTradeGroupStream.ts:41-56`（`groupStateFromStatus`） | **逐键手抄** | **会**——`acceptance` / `exits` / `funding` 不补就永远拿不到 |
| `store.ts:904-913`（`patchAiTradeMemberStatus`） | `{...m.session, ...(status as Partial<AiTradeSessionRow>)}` **整体 spread** | **不会**——但 TS 类型不补，成员卡片读不到（编译期就报） |
| 历史 aiStats 类型 | 在 `apps/web/lib/aiTradeHistoryTypes.ts` 的 `AiTradeRunMetrics["aiStats"]` | `AiTradeHistoryAiStats` **这个类型根本不存在** |

**必须**改这五处：

1. `apps/web/lib/aiTradeTypes.ts`：`AiTradeGroupState` 加
   `acceptance?: AiTradeAcceptance | null` / `exits?: AiTradeExitMix | null` /
   `funding?: AiTradeFundingStats | null`；新增 `AiTradeAcceptance` / `AiTradeAcceptanceRow` /
   `AiTradeExitMix` / `AiTradeFundingStats` 四个 interface；`AiTradeStatusEvent` 与
   `AiTradeSessionRow` 各加同样三个可选键（成员级实时通路）。
2. `apps/web/lib/aiTradeHistoryTypes.ts`：`AiTradeRunMetrics["aiStats"]` 加
   `horizonAcceptance?` / `funding?` / `exits?` / `acceptanceInput?`；`AiTradeHistorySummary` 加
   `horizonAcceptance?` / `exits?`（[P1-9] 的分组合并落点）。
3. `apps/web/lib/useAiTradeGroupStream.ts`：`groupStateFromStatus` 逐键映射补三键；并新增
   [P0-3] 的 15s 轮询 effect。
4. `apps/web/lib/store.ts`：新增窄 action `patchAiTradeGroupAcceptance(patch)`，**只覆盖三个加法
   键、不碰 `members`**（用 `setAiTradeGroup` 会清空 `trades`）。
5. `apps/web/lib/aiBacktestTypes.ts`：`AiBacktestAiStats` 加三键。

**验收用例（守对对象）**：
- `useAiTradeGroupStream.test.ts`（或等价）：一条含 `acceptance` / `exits` 的 mock `/ai-trade/status`
  回执经 `groupStateFromStatus` 后三键非空——**这才是会吞键的那个函数**。
- `store.test.ts`：连续两次 `patchAiTradeGroupAcceptance` 后 `members` 与 `trades` **引用不变**
  （守 [P0-3] 那个「轮询清空成交流」的坑）。

### 2.6 明确不碰的东西（边界）

| 文件 | 为什么不碰 |
|---|---|
| `live_auto/executor.py` | AI 与 live_auto 共用；上一轮非目标第 4 条已钉死。资金费不进 `_close_pnl` / `_net_unrealized`，退出来源不在此打标 |
| `engine/simulator.py` | 全仓库所有回测共用；资金费不进权益曲线（§2.2.4 第 4 点） |
| `ai_auto/hold_horizon.py` 的三道闸判据 | 本轮只加观测，不动任何门禁行为。G1/G2/G3 的输入输出逐字节不变，由冻结用例守 |
| `ai_auto/profit_guard.py` | 同上，保护层行为零改动 |
| `attribution.py` 的既有键 | 只读不改；验收卡复用 `costRatio` 口径而不是另算一份 |

---

## 三、文件 / 模块变更计划

### 3.1 后端 · 新建

| 文件 | 意图 |
|---|---|
| `services/quant/app/ai_auto/funding_ledger.py` | 轴 A 纯核心：结算点平铺 + 逐点计提 + 会话账本 + 回测历史费率曲线 |
| `services/quant/app/ai_auto/exit_attribution.py` | 轴 B 纯核心：8 值枚举 + 两世界分类器 + `ExitLedger` |
| `services/quant/app/ai_auto/horizon_acceptance.py` | 轴 C 纯核心：`AcceptanceInput` / `merge_inputs` / `build_acceptance` / `diagnostics` |
| `services/quant/app/ai_auto/acceptance_text.py` | 验收卡与资金费的中文渲染（display / target / note / diagnostics 文案），与纯核心分离 |

### 3.2 后端 · 修改

| 文件 | 改动 |
|---|---|
| `services/quant/app/okx/rest_client.py` | 新增 `get_funding_rate_history`（公共端点，与 `get_funding_rate` 同姿态、同 TTL 缓存风格）；`get_funding_rate` 返回值**加法**追加 `"intervalKnown": span > 0`（[P0-1]，`intervalS` 的兜底值一字不改） |
| `services/quant/app/ai_auto/funding.py` | `FundingSnapshot` 加 `interval_known: bool = False`，`refresh()` 从 row 填，`wire()` 同步下发（[P0-1]） |
| `services/quant/app/ai_auto/attribution.py` | **加法**：四个只增累加器 + `stats()["cost"]["lifetime"]` 子块（[P1-1]）；既有键与环形缓冲一字不改 |
| `services/quant/app/db/ai_auto_repositories.py` | `get_ai_trade_group_detail` 合并各 run 的 `acceptanceInput` → `summary.horizonAcceptance` / `summary.exits`（[P1-9]） |
| `services/quant/app/live_auto/explain.py` | `build_external_close_explain` 返回值加**顶层加法键** `"source"`（唯一共用文件改动，附冻结用例） |
| `services/quant/app/ai_auto/cost_runtime.py` | 持有 `FundingLedger`；`schedule_maintenance` 加 `mark_px` 形参并在**同步段**计提；新增 `note_open`、`merge_funding_into`、`funding_stats()`；`record_close` 内追加账本收口 |
| `services/quant/app/ai_auto/cost_view.py` | 加只读键 `fundingAccrued`（未装配 / 空仓 ⇒ 整键缺席） |
| `services/quant/app/ai_auto/cost_text.py` | 成本段追加资金费已计提一行（含「不构成离场理由」的反偏置措辞） |
| `services/quant/app/ai_auto/horizon_runtime.py` | `note_close` 拆两段，退出账本记录移到 `rule is None` 早退**之前**；新增 `exits` 成员与 `exit_stats()`；新增 `acceptance_input_parts()` |
| `services/quant/app/ai_auto/session.py` | 净增 ≤8 行：`_dispatch` 加 `note_open`；`_status_payload` 加 `acceptance`；新增 `acceptance_input()`；`_finalize_run` 合并资金费/退出/验收三键 |
| `services/quant/app/ai_auto/manager.py` | `group_status()` 加 `acceptance`（`merge_inputs` + `build_acceptance`） |
| `services/quant/app/ai_auto/backtest.py` | `_setup` 拉历史费率曲线；**新增 `_accrue_funding_bar`（独立于 `_track_horizon_epoch`，[P1-4]）**；`_track_horizon_epoch` 处消费退出打标槽；`_execute` / `_apply_profit_guard_bar` / `_maybe_session_exit` 写槽；`_aggregate_stats` 加四键（含 `acceptanceInput`）；`_build_notes` 加资金费免责 + G3 成本估算不含资金费的欠账披露。**`:548-549` 与 `:626` 的 `funding=None` 一行不动（[P1-5]）** |
| `services/quant/app/ai_auto/backtest_manager.py` | 透传新总闸（回测资金费运维开关） |
| `services/quant/app/api/ai_auto.py` | `GET /ai-trade/horizon-profiles` 每条 profile 加 `acceptance` 阈值预览块（加法） |
| `services/quant/app/schemas.py` | 新增 `AiTradeAcceptanceRowOut` / `AiTradeAcceptanceOut` / `AiTradeExitMixOut` / `AiTradeFundingOut`；`AiTradeHorizonProfileOut` 加 `acceptance` |
| `services/quant/app/config.py` | 9 个新配置（§5.1） |
| `.env.example` | 对应 9 行 + 一段中文小标题（沿用 `AI_TRADE_HORIZON_*` 块的写法） |

### 3.3 前端 · 新建

| 文件 | 意图 |
|---|---|
| `apps/web/lib/aiTradeAcceptance.ts` | 验收卡的类型别名、枚举中文标签、格式化器（**不含任何阈值判定逻辑**——判定只在后端） |
| `apps/web/components/Agent/aiTradeStats/HorizonAcceptanceCard.tsx` | 五行判据卡 + 结论徽标 + 诊断行 |
| `apps/web/components/Agent/aiTradeStats/ExitSourceCard.tsx` | 退出来源堆叠条 + 明细行 |
| `apps/web/lib/aiTradeAcceptance.test.ts` | 格式化 / 标签 / 三态徽标的单测 |
| `apps/web/components/Agent/aiTradeStats/HorizonAcceptanceCard.test.tsx` | 三态渲染 + 不渲染条件 |
| `apps/web/components/Agent/aiTradeStats/ExitSourceCard.test.tsx` | 只有 unknown ⇒ 不渲染；胜率样本门槛 |

### 3.4 前端 · 修改

| 文件 | 改动 |
|---|---|
| `apps/web/lib/aiTradeTypes.ts` | 4 个新 interface；`AiTradeGroupState` / `AiTradeStatusEvent` / `AiTradeSessionRow` 各加三个加法键（[P1-10]） |
| `apps/web/lib/aiTradeHistoryTypes.ts` | `AiTradeRunMetrics["aiStats"]` 加四键、`AiTradeHistorySummary` 加两键（[P1-9] / [P1-10]；**历史 aiStats 类型在这个文件，不在 `aiTradeTypes.ts`**） |
| `apps/web/lib/aiBacktestTypes.ts` | `AiBacktestAiStats` 加 `horizonAcceptance` / `funding` / `exits` |
| `apps/web/lib/useAiTradeGroupStream.ts` | `groupStateFromStatus` 补三键；新增 15s 分组轮询 effect（[P0-3]） |
| `apps/web/lib/store.ts` | 新增窄 action `patchAiTradeGroupAcceptance`（只覆盖三个加法键、不碰 members，[P0-3]） |
| `apps/web/components/Agent/aiTradeStats/AiTradeStatsLive.tsx` | 挂两张新卡（`group.acceptance` / `group.exits`，两个独立入参） |
| `apps/web/components/Agent/aiTradeStats/AiTradeStatsDetail.tsx` | 挂两张新卡（`summary.horizonAcceptance` / `summary.exits`） |
| `apps/web/components/Chart/aiBacktest/panels.tsx` | 回测结果面板挂同两张卡 + 资金费免责 note |
| `apps/web/components/Agent/AiTradeHorizonFields.tsx` | 模式卡片下方增加一行「验收门槛预览」（读 `profiles[].acceptance`，纯展示） |

---

## 四、接口设计

### 4.1 `GET /api/ai-trade/horizon-profiles`（既有端点 · 加法字段）

每个 profile 条目追加：

```jsonc
{
  "id": "short_swing",
  "title": "短波段",
  // …既有字段一字不改…
  "acceptance": {
    "sampleFloor": 20,
    "minAvgHoldSeconds": 43200,
    "minWithinTargetRate": 0.6,
    "maxTurnoverPerDay": 2.0,
    "maxFeeToGross": 0.35,
    "requiresPositiveNetAfterFunding": true
  }
}
```

`custom` 条目的派生阈值在 `targetHold*` 未填时为 `null`，前端对 `null` 显示「按你填的目标区间派生」。

### 4.2 `GET /api/ai-trade/status`（既有 · `group_status()` 加法字段）

```jsonc
{ "groupId": "...", "tradingHorizon": { … },
  "acceptance": { …§2.4.4… },      // 未装配周期契约 / 零样本 ⇒ null
  "exits": { …§2.3.3… },           // [P0-4] 顶层键：acceptance 为 null 时它仍可非空
  "funding": { …§2.2.2… },
  "members": [ … ] }
```

未装配 / 总闸关 / 零样本 ⇒ 三键**恒下发 `null` 而非省略**。理由**不是**「强类型出参」——该端点
返回的是裸 `dict`、**没有 `response_model`**（`api/ai_auto.py:291-295`，P2-1 更正）；真正的理由是
前端 `groupStateFromStatus` 是逐键手抄映射，`?? null` 的写法要求键**恒存在**才能把「本轮没数据」
与「后端还没上线这个键」区分开。

该端点由 [P0-3] 的 15s 轮询消费，是分组合并口径的唯一实时来源。

### 4.3 WebSocket（既有通道 · 加法键）

- `status` 事件的 `data` 加 `acceptance`（单会话口径）；旧前端忽略即可。
- `trade` 事件的 `data.explain.meta` 加 `exitSource`（只在平仓成交上出现）。

### 4.4 回测结果（既有形状 · 加法键）

`result.aiStats` 追加 `horizonAcceptance` / `funding` / `exits` 三键；`result.notes` 追加资金费口径
免责一条。旧前端不读即无感。

### 4.5 内部函数签名（下游按此实现，不再二次决策）

```python
# funding_ledger.py（`window.interval_known is False` ⇒ settlement_points 恒 []，[P0-1]）
def settlement_points(*, from_ts: float, to_ts: float, window: FundingWindow) -> list[int]
def accrue(epoch: FundingEpoch, *, window: FundingWindow, now_wall: float,
           mark_px: float) -> tuple[FundingEpoch, int]
class FundingLedger:
    def __init__(self, *, enabled: bool, ct_val: float,
                 min_interval_s: float, max_interval_s: float) -> None
    def note_open(self, trade: dict, *, seq: int, now_wall: float) -> None
    def note_close(self, trade: dict, now_wall: float) -> None
    def accrue(self, snapshot, *, now_wall: float, mark_px: float | None) -> None
    def accrue_bars(self, curve: "BacktestFundingCurve", *, from_ts: int, to_ts: int,
                    mark_px: float) -> None
    def stats(self, *, settled_usd: float | None) -> dict | None
class BacktestFundingCurve:
    def __init__(self, rows: list[dict]) -> None
    def points_in(self, from_ts: int, to_ts: int) -> list[tuple[int, float]]
    @property
    def available(self) -> bool

# exit_attribution.py
def classify_live(explain: dict | None) -> str
def classify_backtest(reason: str, *, stamped: str | None) -> str
class ExitLedger:
    def __init__(self, *, net_mode: bool, min_win_rate_samples: int = 5) -> None
    def record(self, trade: dict, *, hold_seconds: float | None) -> None
    def stats(self) -> dict | None

# horizon_runtime.py（[P1-2] 新增：原始计数器的唯一出口，与展示口径 stats() 分工）
class HorizonRuntime:
    def acceptance_input_parts(self) -> dict | None
    # {"roundTrips", "holdSecondsSum", "holdSamples", "withinTargetSamples", "truncatedSamples"}
    # 未装配 ⇒ None。**全部为未 round 的原始值**——stats() 的 round 产物反推不回来（见 §2.4.1）。

# attribution.py（[P1-1] 新增：全程累计口径，与 200 容量环形缓冲并存）
class AttributionLedger:
    def lifetime_cost(self) -> dict
    # {"roundTrips", "grossPnlUsd", "feeUsd", "netPnlUsd", "absGrossPnlUsd", "costRatio"}

# horizon_acceptance.py
def build_input(*, horizon_parts: dict | None, lifetime_cost: dict, exit_stats: dict | None,
                funding_stats: dict | None, wall_seconds: float) -> AcceptanceInput | None
    # [P1-2] 第一个形参吃 `HorizonRuntime.acceptance_input_parts()` 的**原始计数器**，
    # 不是 `stats()` 的 round 产物；[P1-1] 第二个吃 lifetime 口径而非环形窗口口径。
    # [P1-3] `wall_seconds` 的三世界取值见 §2.4.1，调用方负责按表传，本函数只做 max(1.0, …) 兜底。
def merge_inputs(items: list[AcceptanceInput]) -> AcceptanceInput
def resolve_thresholds(settings, rule) -> AcceptanceThresholds
    # [P0-2] `rule.target_hold_min_s <= 0` / `target_hold_max_s <= 0` ⇒ 对应阈值置 None，
    # 由 build_acceptance 翻成该行 "insufficient"；**绝不在此处除零或回落到任何默认目标区间**。
def build_acceptance(inp: AcceptanceInput | None, rule, thresholds,
                     *, exits: dict | None, funding: dict | None) -> dict | None
    # [P0-4] `exits` / `funding` 由调用方传入并**原样引用**进 wire 的 exitMix / funding，
    # 本函数不重新装配它们（二次装配 = 两处各算一份 = 本仓库反复出事的分叉）。
def build_diagnostics(inp: AcceptanceInput, rows: list[dict]) -> list[str]
```

---

## 五、数据模型

### 5.1 配置（`services/quant/app/config.py`，全部新增）

```python
# ---- AI 自动交易 · 持仓周期验收闭环（ai-trade-horizon-acceptance-loop §2）----
# 轴 A：资金费真值化。总闸关 ⇒ 账本恒空转、aiStats.funding 整键缺席、验收卡第 5 行判 insufficient。
# [P1-7] 本开关**必须**并进 `CostRuntime` 里 `FundingCache` 的 enabled 析取式
# （cost_runtime.py:61-65），否则本层开着、资金费率快照却被 `ai_trade_cost_awareness_enabled`
# 连坐关死，卡片只会说「口径不可得」而说不出是被谁关的。
ai_trade_funding_ledger_enabled: bool = True
# 结算周期合理性区间：交易所读数落在区间外即视为坏读数 ⇒ 本次不计提（不猜 8h，调查约束 #7）。
ai_trade_funding_min_interval_s: float = Field(default=1800.0, gt=0, le=86400)
ai_trade_funding_max_interval_s: float = Field(default=28800.0, gt=0, le=86400)
ai_trade_funding_backtest_enabled: bool = True          # 回测侧历史费率曲线运维总闸
ai_trade_funding_history_max_pages: int = Field(default=8, ge=1, le=50)
# 轴 B：退出来源归因。关 ⇒ explain.meta.exitSource 整键缺席（老数据同样缺席，查询侧按 unknown）。
ai_trade_exit_attribution_enabled: bool = True
# 轴 C：验收卡。
ai_trade_acceptance_enabled: bool = True
ai_trade_acceptance_min_round_trips: int = Field(default=20, ge=1, le=1000)
ai_trade_acceptance_min_within_target_rate: float = Field(default=0.6, ge=0, le=1)
ai_trade_acceptance_max_fee_to_gross: float = Field(default=0.35, gt=0, le=10)
```

`.env.example` 同步 9 行（键名全大写蛇形，沿用 `AI_TRADE_HORIZON_*` 块的注释密度）。

### 5.2 内存态

| 归属 | 字段 | 说明 |
|---|---|---|
| `CostRuntime` | `funding_ledger: FundingLedger` | 会话级；与 `FundingCache` 同居一层 |
| `FundingLedger` | `epoch / closed_accrued_usd / crossings / stale_ticks` | epoch 身份 `(side, seq)` 与 horizon 同源 |
| `HorizonRuntime` | `exits: ExitLedger` | **恒构造**，不受 `rule is None` 影响 |
| `AiBacktestRunner` | `_funding: FundingLedger` / `_curve: BacktestFundingCurve` / `_pending_exit_source: str \| None` | 单槽打标见 §2.3.2 |

### 5.3 落库（**零迁移**）

- `Run.metrics_json.aiStats` 追加**四**个加法键：`funding` / `exits` / `horizonAcceptance` /
  `acceptanceInput`（[P1-9]：最后一项是原始计数器，分组历史详情靠它在后端合并；缺它多标的分组的
  历史页只能挂 N 张互相矛盾的卡）。
- 成交 `explain.meta` 追加 `exitSource`（已有 JSON 列，与 `wakeKind` 同款）。
- 无新表、无新列、无 ALTER。历史数据缺键 ⇒ 前端按「未记录 / 不可得」渲染，绝不当 0。

---

## 六、测试与验收标准

### 6.1 后端单测（新增文件，约 96 条）

**`tests/test_ai_trade_funding_ledger.py`（约 30 条）**

- F-1 **`interval_known is False` ⇒ `settlement_points` 返回 `[]` 且不计提**（[P0-1]；判据是
  `interval_known` 而**不是** `interval_s <= 0`——后者在生产里恒不成立，写成那样等于用例空转）。
  配套一条 `rest_client` 用例：`fundingTime == nextFundingTime` 的响应 ⇒ `intervalKnown is False`
  且 `intervalS` 仍为既有兜底值（守住不改既有键的承诺）。
- F-2 anchor 在未来、持仓跨 3 个结算点 ⇒ 恰计提 3 次，`crossings == 3`。
- F-3 多头 + `rate > 0` ⇒ 支出为正；空头同费率 ⇒ 支出为负（收取）。
- F-4 部分成交两次 `note_open`（同 `(side, seq)`）⇒ `qty_coin` 累加、`accrued_usd` **不清零**。
- F-5 `mark_px` 非正 ⇒ 本次不计提且 `settled_through_ts` 不推进；下一次 tick 补上那个结算点。
- F-6 `_MAX_SETTLEMENTS` 封顶：坏 interval（1 秒）不产生上万次计提。
- F-7 `enabled=False` ⇒ 全部方法空转、`stats()` 恒 `None`。
- F-8 `stats()` 的 `basis` 三态：settled 可得 / 仅 accrued / 全不可得，且 `fundingCostUsd` 分别为
  `settled+open` / `closedAccrued+open` / `None`。
- F-9 **`fundingCostUsd is None` 与 `== 0.0` 在 wire 上可区分**（`None` 不得被 `round` 成 0.0）。
- F-10 `BacktestFundingCurve` 用真实变周期序列（8h→4h→8h）⇒ `points_in` 逐点取真实 ts，不平铺。
- F-11 **[P1-6] 资金费单调不减**：构造「本地已计提 0.84 → 平仓 → `settledUsd` 因结算延迟仍为 0.0」
  的序列，断言 `fundingCostUsd` **不下降**（`max(settled, accruedClosed)` 生效），且
  `coverageMismatch=True` 时 `basis == "accrued"`、`settledTrusted is False`。
- F-12 `samples >= 100`（`positions-history` 可能被截断，P2-6）⇒ `basis` 降级为 `"accrued"` 且
  `note` 含截断说明。
- F-13 `interval_known is False` 的 tick ⇒ `unknownIntervalTicks` 递增且 `intervalS` 下发 `null`
  （不得把兜底的 28800 当读数展示）。

**`tests/test_ai_trade_exit_attribution.py`（约 22 条）**

- E-1～E-8 §2.3.1 冻结表的 8 个实盘可达枚举各一条实盘用例（构造对应 explain）。
- E-9～E-13 回测侧 5 条（含单槽被消费后置空、槽空且 reason 未知 ⇒ `unknown`）。
- E-19 **[P1-8] `reason == "session_end"` ⇒ `windowEnd`**（不是 `unknown`），且该笔进
  `truncated_samples`；`windowEnd` 在实盘分类器上**永不产出**（实盘会话停止走 `sessionStop`）。
- E-14 `count < 5` ⇒ `winRate is None`。
- E-15 `bySource` 排序稳定（count 降序 → 枚举序）。
- E-16 全部样本为 `unknown` ⇒ `stats()` 仍返回数据（前端负责不渲染），但 `totalExits` 正确。
- E-17 **`hold_seconds is None` 的样本不进时长均值**，但进笔数与净额。
- E-18 `net_mode` 两种口径下同一笔成交的 `netUsd` 与 `attribution.record_close` **逐位相等**。

**`tests/test_ai_trade_horizon_acceptance.py`（约 26 条）**

- A-1 五行判据各一条 pass / 一条 fail。
- A-2 样本 < `sampleFloor` ⇒ 整卡 `insufficient`，且**没有任何一行是 pass 绿色**（断言 verdict 值）。
- A-3 `merge_inputs`：两成员 `wall_seconds` 取 max 而非 sum（**回归守门**：写成 sum 会让换手率减半）。
- A-4 `merge_inputs`：一个成员 `funding_basis="unavailable"` ⇒ 合并后为 `unavailable`。
- A-5 `turnoverPerDay` 的墙钟口径与在场口径**同时下发且不相等**（守 §1.1 表 #2 那个恒真缺陷不复发）。
- A-6 **[P1-1]** ≤200 笔往返时 `feeToGross` 与 `attribution.stats()["cost"]["costRatio"]` 逐位
  相等；**>200 笔时二者不等**，且验收卡取的是 `cost.lifetime.costRatio`（回归守门：写成环形口径
  会让高换手会话的第 4 行悄悄换窗口）。
- A-7 `funding_cost_usd is None` ⇒ 第 5 行 `insufficient`，整卡不得 `pass`。
- A-8 三条 `diagnostics` 各一条命中 / 各一条不命中。
- A-9 `rule is None`（未装配周期模式）⇒ `build_acceptance` 返回 `None`（整键缺席），
  **但 `exits` 顶层键仍非空**（[P0-4] 对照臂回归守门）。
- A-10 阈值全部由 `rule` 派生的两行（avgHold / turnover）在换模式后跟着变。
- A-11 **[P0-2]** `target_hold_min_s == 0`（`custom` 全默认）⇒ 第 1、3 行 `insufficient`、
  **不抛异常**、整卡不得 `pass`；`target_hold_max_s == 0` ⇒ 第 2 行 `insufficient`。
- A-12 **[P0-2]/P2-3** `86400 / target_hold_min_s > 24` 时第 3 行阈值取 24 且 note 自认弱门槛
  （`hourly_swing` 下拿 240 次/日当门槛等于没有门槛）。
- A-13 **[P1-3]** `wall_seconds <= 0` ⇒ 第 3 行 `insufficient`（不除零）；回测口径取
  「首决策根 → 末根」而非全区间（用例用一段含暖机的 df 对拍两个数不相等）。
- A-14 **[P1-8]** `truncated_samples > 0` ⇒ 第 1 行 note 含截断披露，且截断样本**仍在**
  `hold_samples` 里（剔除即幸存者偏差，用例断言分母未变）。
- A-15 **[P1-2]** `build_input` 吃原始计数器：构造 `hold_seconds_sum` 使 `avg` 恰好落在 round
  边界上，断言合并两成员后的 `avgHold` 与「按 round 后的 avg 反乘再合并」**不相等**（守住
  「可精确合并」这句话）。

**`tests/test_ai_trade_acceptance_wiring.py`（约 18 条）**

- W-1 `session._dispatch` 一次开仓 + 一次平仓 ⇒ 账本 epoch 建立与结转各一次。
- W-2 `_inject_explain_meta` 只给 `side == "close"` 打 `exitSource`，开仓腿无该键。
- W-3 总闸关 ⇒ `meta` 与改动前**逐键相等**（冻结断言）。
- W-4 `manager.group_status()` 双成员 ⇒ `acceptance.roundTrips` 为两者之和。
- W-5 回测 `_aggregate_stats` 的三个新键形状与实盘 `_finalize_run` 的**逐键相同**（同源门禁）。
- W-6 回测取不到费率曲线 ⇒ `basis == "unavailable"` 且 notes 含免责，**`fundingCostUsd` 不为 0**。
- W-7 `build_external_close_explain` 除 `source` 外与改动前逐键相等（`live_auto` 冻结）。
- W-8 `schedule_maintenance` 未传 `mark_px` ⇒ 不计提且不抛（既有调用点零回归）。
- W-9 **[P0-4]** `group_status()` / `_status_payload()` / `_aggregate_stats()` / `_finalize_run`
  四处的 `exits` 与 `acceptance.exitMix` 序列化后逐键相等（同一对象引用，不得二次装配）；
  且 `horizon_rule is None` 的会话上 `acceptance is None` 而 `exits` 非空。
- W-10 **[P0-3]** `session._status_payload()` 含 `acceptance` / `exits` / `funding` 三键（成员级
  实时通路存在的证据）；未装配 / 零样本时三键为 `None` 而非缺席。
- W-11 **[P1-4]** `horizon_rule=None` 的回测 ⇒ `aiStats.funding` **非空**（资金费不被周期模式的
  开关连坐）。
- W-12 **[P1-9]** 双 run 的历史详情 ⇒ `summary.horizonAcceptance.roundTrips` 为两者之和；
  任一 run 缺 `acceptanceInput` ⇒ `summary.horizonAcceptance` 整键缺席（不拿半组数据冒充整组）。
- W-13 **[P1-7]** `ai_trade_cost_awareness_enabled=False` 且 `k_min=0` 且
  `ai_trade_funding_ledger_enabled=True` ⇒ `FundingCache.enabled is True`（新总闸并进了析取式）。

**冻结 / 零回归门禁（沿用上一轮的 sha256 冻结手法）**

- G-1 关掉三个总闸（`funding_ledger` / `exit_attribution` / `acceptance`）后，一轮完整决策的
  `payload`、`prompt` 全文、`trade` 事件 JSON 与合入前**逐字节相同**。
- G-2 `hold_horizon.evaluate_horizon_gate` 的输入输出在本轮**零改动**（G1/G2/G3 行为冻结）。
- G-3 `profit_guard` 全部用例零改动通过。
- G-4 `SimBroker` 与 `LiveExecutor` 无任何 diff（`git diff --stat` 断言）。
- G-5 **[P1-5]** `backtest.py` 的两处 `funding=None, funding_enabled=False`（成本模型入参）
  在本轮 diff 中**零改动**——按 AST 断言这两个调用的实参字面量未变；改了它就是改门禁行为。
- G-6 **[P0-1]** `cost_model.py` 与 `cost_text` 在本轮 diff 中零改动（`intervalKnown` 只被
  `FundingLedger` 消费，成本层继续走它自己的 8h 保守估算，见 §九 D-9）。

### 6.2 前端单测

- `aiTradeAcceptance.test.ts`：三态徽标映射、五种 unit 的 display 格式化、`null` 值不渲染成 0。
- `HorizonAcceptanceCard.test.tsx`：pass/fail/insufficient 三态类名；`insufficient` 时无任何绿色行；
  `acceptance == null` ⇒ 不渲染。
- `ExitSourceCard.test.tsx`：只有 unknown ⇒ 不渲染；`winRate == null` ⇒ 显示「样本不足」；
  **`acceptance == null` 但 `exits` 非空 ⇒ 照常渲染**（[P0-4] 对照臂）。
- `useAiTradeGroupStream.test.ts` 新增用例（[P1-10]：守**会吞键的那个函数**）：一条含
  `acceptance` / `exits` / `funding` 的 mock `/ai-trade/status` 回执经 `groupStateFromStatus`
  后三键非空。
- `store.test.ts` 新增用例（[P0-3]）：连续两次 `patchAiTradeGroupAcceptance` 后
  `state.aiTradeGroup.members` 与各成员 `trades` **引用不变**（守「轮询清空成交流」的坑）。
- `panels.test.tsx`（回测面板）：`aiStats.horizonAcceptance` 存在即渲染，缺席即不渲染；
  `aiStats.exits` 独立渲染（不依赖 acceptance）。

### 6.3 放行条件（Definition of Done，逐条可核对）

| # | 条件 |
|---|---|
| 1 | 三个总闸全关时，后端 G-1～G-4 四条冻结门禁全绿（逐字节零回归） |
| 2 | `git diff` 中 `live_auto/executor.py` 与 `engine/simulator.py` **零改动**；`live_auto/explain.py` 仅一处加法键且有冻结用例 |
| 3 | `session.py` 净增 ≤ 8 行；超出即在 spec 末尾登记欠账并说明 |
| 4 | 资金费三态（settled / accrued / unavailable）在实盘与回测**各**有一条端到端用例 |
| 5 | `fundingCostUsd` 的 `null` 与 `0.0` 在 wire 与 UI 上**可区分**，UI 文案分别为「口径不可得」与「未跨结算点」 |
| 6 | 8 个退出来源枚举各有一条用例；`exitSource` 出现在落库 explain 与 WS `trade` 事件里（实测） |
| 7 | 验收卡在样本不足时**没有任何一行是绿色**，整卡结论为 `insufficient`（用例断言颜色类名） |
| 8 | `merge_inputs` 的 `wall_seconds = max` 有专属回归用例 |
| 9 | 墙钟口径与在场口径的换手率同时下发且在同一份样本上**不相等** |
| 10 | 三处 UI（运行期 / 历史详情 / 回测面板）共用同一对组件与同一份 props 类型；两卡为**两个独立入参**，`acceptance == null` 时退出来源卡仍渲染（[P0-4]） |
| 11 | store / stream / types 五处接线齐全（含 `aiTradeHistoryTypes.ts`），且专属用例守的是 `groupStateFromStatus` 而非 `patchAiTradeMemberStatus`（[P1-10]） |
| 12 | 全量 `pytest` 结果与干净 HEAD worktree 实测的红灯基线**逐条一致**，无新增红灯 |
| 13 | 前端 `vitest` 全绿；`tsc` 除既有 `WeightBatchArena.test.tsx` 外无新增错误 |
| 14 | **L1 门槛仍然有效**：在样本外净额回测的验收卡结论为 `pass` 之前不得放大实盘资金（写入 runbook） |
| 15 | **[P0-1]** `intervalKnown` 贯通 `rest_client → FundingSnapshot → FundingLedger`；F-1 用例的判据是 `interval_known`，且 `cost_model` / `cost_text` 零 diff（G-6） |
| 16 | **[P0-2]** `custom` 全默认（`target_hold_min_s == 0`）跑一轮完整 status 装配不抛异常，验收卡三行 `insufficient`、整卡非 `pass` |
| 17 | **[P0-3]** 运行期卡片实测会更新：一个跑着的分组在 ≤30s 内看到 `roundTrips` 变化；轮询期间成交流不被清空（前后端各一条用例） |
| 18 | **[P0-4]** `horizon_rule=None` 的会话 / 回测上退出来源卡照常渲染（对照臂可观测）；`exits` 顶层键与 `acceptance.exitMix` 序列化后逐键相等（W-9） |
| 19 | **[P1-1]** 验收卡第 4、5 行取 `cost.lifetime.*`；构造 >200 笔往返的用例断言它与 `costRatio` 不等 |
| 20 | **[P1-2]** `build_input` 吃 `acceptance_input_parts()` 的原始计数器；A-15 的「round 反乘 ≠ 精确合并」用例通过 |
| 21 | **[P1-3]** 三个世界的 `wall_seconds` 各有一条用例，回测口径从首决策根起算 |
| 22 | **[P1-4]** 未装配周期模式的回测 `aiStats.funding` 非空（W-11） |
| 23 | **[P1-5]** `backtest.py` 两处 `funding=None` 实参零改动（G-5 的 AST 断言通过） |
| 24 | **[P1-6]** `fundingCostUsd` 在「平仓 → 结算延迟 → 结算到账」的三步序列中单调不减（F-11） |
| 25 | **[P1-7]** 新总闸并进 `FundingCache.enabled` 析取式（W-13）；模拟盘 / 无凭证会话的 `basis` 恒为 `accrued` 且 note 说明原因 |
| 26 | **[P1-8]** `session_end` 归 `windowEnd` 且计入 `truncatedSamples`；第 1 行 note 披露截断（E-19 / A-14） |
| 27 | **[P1-9]** 双成员分组的历史详情只挂**一张**合并卡；任一成员缺 `acceptanceInput` 即整卡缺席 |
| 28 | **[P1-11]** 全仓库 grep：本轮新增代码中不存在 `self.settings`（会话内配置一律 `self.executor.settings`） |

### 6.4 上线后的观察窗口（runbook）

| 级别 | 配置 | 观察什么 | 通过判据 |
|---|---|---|---|
| **L0**（24h，纸面） | 三总闸开，`ai_trade_acceptance_min_round_trips=20` | `funding.staleTicks` / `unknownIntervalTicks` 占比、`exitSource` 的 `unknown` 泄漏率 | `staleTicks / ticks < 5%`；`unknownIntervalTicks == 0`（永续标的的结算周期应当恒可得，不为 0 即说明 [P0-1] 的通路没接对）；**`unknown` 占比 < 2% 且每一笔都能在日志里定位到是哪条路径漏了 explain**（P2-7：v1 的「= 0」与 R-11「explain 合成是 best-effort」自相矛盾，一个按设计必然被违反的判据只会训练运维忽略它） |
| **L1**（样本外回测） | `4H + 唤醒周期 0 + 低杠杆`（调查原文推荐组合），`trend_hold` 与 `short_swing` 各一次 | 验收卡五行 | 结论 `pass`；若 `insufficient` 则延长回测区间，**不得**下调 `sampleFloor` 蒙混过关 |
| **L2**（小额实盘 ≥7 天） | 同 L1 配置，最小预算 | 实盘验收卡 vs 回测验收卡 | 五行同向；`netAfterFunding` 差异 < 50%；`exitMix` 中 `profitGuard + exchangeOco` 占比较调查基线（6.25%）上升 |

---

## 七、风险与缓解

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R-1 | 计提用的 `rate` 是**当期预告值**，与实际结算值可能不同 | 本地计提与交易所结算存在小额偏差 | 已平仓段**一律以 `settledUsd` 为准**；`accrued` 只覆盖在场段。`basis` 字段如实标注，并在卡片 tooltip 说明 |
| R-2 | `positions-history` 是**持仓级**、本地是**往返级**，二者不一一对应（`reconcile.py:4`） | `settledUsd` 与本地 epoch 无法逐笔对齐 | 本设计**只做窗口级**资金费合计，不做逐笔归属。验收卡的第 5 行本来就是会话级读数 |
| R-3 | 把「已计提资金费」摆给模型看，可能造出新的抢跑偏置 | 抵消上一轮的偏置矫正 | §2.2.5 的文案强制带「不构成离场理由」的后半句；并把 `exitMix` 的 `agentShare` 纳入 L2 观察项，一旦回升即回滚该披露 |
| R-4 | `funding-rate-history` 端点字段名与假设不符 | 回测资金费全为空 | 实现阶段必须先打真实响应核对键名；不符即在 notes 如实降级为 `unavailable`，**绝不硬编猜测映射**（并把核对结果写进实施发现段） |
| R-5 | 回测资金费不进权益曲线 ⇒ `metrics.totalReturn` 与验收卡第 5 行口径不一致 | 用户看到两个「收益」数字 | notes 与卡片同时写明；两个数字在 UI 上**不并排放置**，验收卡自带 `= 净额 X − 资金费 Y` 的算式行 |
| R-6 | 单槽打标在未来新增「同一根内两笔平仓」的路径时会错配 | 回测退出来源归错 | 槽被消费即置空；消费时若槽非空但 reason 命中确定性字面量，**以 reason 为准**并记一次 `stampConflict` 计数（进 notes，可观测） |
| R-7 | `explain.py` 加键影响 `live_auto` | 非 AI 链路回归 | 冻结用例 W-7；且该键不参与任何 live_auto 分支判断 |
| R-8 | 验收阈值（0.35 / 0.60 / 20）过严或过松，导致长期恒 fail 或恒 pass | 门槛失去意义 | 三者全部可配置；L1/L2 观察窗口要求记录实测分布，首个季度后按实测重新拍板（写入 runbook） |
| R-9 | `session.py` 已 1727 行，本轮再加会加剧拆分债 | 可维护性 | 硬预算 ≤8 行（放行条件 3），业务状态一律下沉到 `CostRuntime` / `HorizonRuntime` / 新纯核心 |
| R-10 | 多标的分组的样本被合并后掩盖单标的差异 | 一个赚一个亏被平均成「还行」 | `acceptance` 同时下发成员级 `bySymbol` 摘要（笔数 + 净额 + 结论），卡片可展开；合并只用于总判定 |
| R-11 | `exitSource` 依赖 `explain` 存在，而 explain 合成是 best-effort（`executor.py:889`） | 少数成交落 `unknown` | `unknown` 是一等公民枚举、如实呈现；L0 判据即「新会话 unknown 占比 = 0」，一旦不为 0 立刻暴露 |
| R-12 | 资金费计提发生在 5s tick 上，进程重启会丢失在场段计提 | 重启后 `accruedOpenUsd` 归零 | 重启后 epoch 由 `note_open` 的估算路径重建，`accruedOpenUsd` 从重建时刻起算并置 `restarted=true`；平仓后以 `settledUsd` 覆盖，最终值仍正确 |
| R-13 | **[P1-1] 评审新增**：`AttributionLedger` 的 200 容量环形缓冲是既有设计，本轮加的 lifetime 累加器与它并存，两套数在长会话上必然分叉 | 运维在两个地方看到两个「手续费占毛利」 | 两者**用途分开且都标注口径**：卡片第 4 行固定用 lifetime 并在 note 写「全程累计」；既有成本卡的 `costRatio` 文案补「近 200 笔」。W-14：断言两处文案都出现口径词，不允许出现无口径的裸比例 |
| R-14 | **[P0-3] 评审新增**：15s 轮询在长期挂着的看板上会持续打 `GET /ai-trade/status` | 无谓的服务端负载 / 电池消耗 | 轮询只在 `accountMode === "live"` **且**存在运行中分组时启动，页面隐藏（`document.hidden`）时暂停；`group_status()` 是纯内存读取，单次成本可忽略 |
| R-15 | **[P0-1] 评审新增**：`intervalKnown` 是加法键，老部署 / 单测里构造的 `FundingSnapshot` 会拿到默认 `False` ⇒ 一律不计提 | 升级后资金费短时全为 `unavailable` | 默认值刻意取 `False`（「不知道就说不知道」的方向安全）；`FundingCache.refresh` 是唯一生产写入点，正常链路必然填真值。L0 的 `unknownIntervalTicks == 0` 判据就是这条通路是否接通的验证 |

---

## 八、实施顺序（给下游代码开发节点）

0. **[P0-1] 先把「不知道」这条信息通路打通**（其余一切资金费工作都建立在它之上）：
   `rest_client.get_funding_rate` 加 `intervalKnown` → `FundingSnapshot.interval_known` →
   `FundingCache.refresh/wire`。配套 F-1 与 G-6（`cost_model` / `cost_text` 零 diff）先跑绿。
   **顺序不可调**：先写账本再补这条通路，账本的核心不变式会先以死码形态跑绿一遍，之后再没人回头看。
1. **纯核心三件套**（无接线，可独立单测跑通）：`funding_ledger.py` → `exit_attribution.py` →
   `horizon_acceptance.py` + `acceptance_text.py`。先把 §6.1 的纯核心用例（含新增 F-11~F-13 /
   E-19 / A-11~A-15）全部写完跑绿。
2. **`explain.py` 加法键 + 冻结用例**（最小、最先验证共用文件安全）。
3. **[P1-1] `attribution.py` 的 lifetime 累加器**（加法、独立可测，先于装配点落地）。
4. **实盘接线**：`cost_runtime`（含 [P1-7] 的 `FundingCache.enabled` 析取式）→ `horizon_runtime`
   （含 [P1-2] 的 `acceptance_input_parts()`）→ `session`（严守 8 行预算）→ `manager`。
5. **回测接线**：`rest_client.get_funding_rate_history`（先打真实响应核对键名并记录）→
   `backtest`（[P1-4] 的独立 `_accrue_funding_bar`；**不碰** [P1-5] 的两处成本入参）。
6. **落库与历史合并**：`_finalize_run` 落 `acceptanceInput` → `db/ai_auto_repositories`
   的分组合并（[P1-9]）。
7. **schemas / config / .env.example / api**。
8. **前端**：types（五处，含 `aiTradeHistoryTypes.ts`）→ store 窄 action + stream 轮询（[P0-3]）
   → 两个新组件（两个独立入参）→ 三处挂载 → 弹窗阈值预览。
9. **全量回归 + 红灯基线对账**（用 `git worktree add --detach` 建干净 HEAD 实测基线，**不要靠记忆**；
   已知基线：`test_ai_trade_prior_session.py` 7 条 HEAD 已红、`test_factor_library_expansion.py` 7 条为
   未提交孤儿文件、`test_agent_ws.py` 一条共用临时 DB 的顺序 flake）。

---

## 九、有意偏离与拍板记录

| # | 偏离 | 拍板理由 |
|---|---|---|
| D-1 | 资金费**不回写** `trade["pnl"]` / 权益曲线，只做独立一项账 | 沿用阶段一 `reconcile.py:5-6` 的既定纪律；回写会让盈亏正确性依赖网络可用性，且会改动全仓库共用的 `SimBroker` |
| D-2 | 不新增 `/account/bills` 私有端点 | `positions-history` 已在每 300s 拉取且已算出 `exchangeFundingFeeUsd`（`reconcile.py:149`），零新增网络面即可拿到权威值 |
| D-3 | 退出来源在 `explain.meta` 打标，而不是新增 DB 列 | 与 `meta.wakeKind` 完全同款、零迁移，且自动获得落库 / WS / 导出三条通路 |
| D-4 | `exitMix` **不参与**验收判定 | 把「保护层占比」做成门槛会诱导「故意不主动平仓等保护层」这种劣化行为 |
| D-5 | 新增墙钟口径换手率，保留在场口径 | 在场口径与平均持仓时长代数等价（§1.1 表 #2），单独用它做判据是恒真的；但它对「持仓期内换手」仍有解释力，故并列保留并在 tooltip 说清 |
| D-6 | 验收卡在样本不足时不显示黄色而显示灰色 | 黄色会被读成「快通过了」；`insufficient` 的语义是「什么都还不知道」 |
| D-7 | 回测的历史费率曲线**不复用** `settlement_points` 的平铺逻辑 | 历史数据自带真实结算时刻；交易所动态改周期（1/2/4/8h）时平铺会错位 |
| D-8 | **[P1-5] 评审拍板**：回测的**成本估算**（`backtest.py:548-549` / `:626`）继续不含资金费，与实盘含资金费**保持不对称**，本轮如实登记为欠账而不修 | 改它会当场改变 G3 换手迟滞与 edge 门禁在回测里的判定，与 G-2 冻结门禁、以及「本轮只加观测、不动任何门禁行为」的非目标正面冲突。修它属于下一轮「G3 阈值重估」的范围，且必须与阈值一起改，否则只是把偏差换了个方向 |
| D-9 | **[P0-1] 评审拍板**：同一个「结算周期未知」，**记账层不计提、成本层继续按 8h 保守估算** | 两层的失败方向不同：成本估算宁可高估（保守 ⇒ 门禁更严），记账宁可不记（绝不把猜的数当真值上报）。让成本层也改会动 `cost_text` 文案 ⇒ 打破 G-1 逐字节冻结，且把一个观测特性扩散成决策链改动。该不对称写进 `.env.example` 注释与卡片 tooltip |
| D-10 | **[P0-3] 评审拍板**：分组级验收卡走 **15s REST 轮询**而不是新建一条分组 WS | 本仓库的 AI-trade WS 是**按成员会话**建的（`useAiTradeGroupStream` 逐成员 `aiTradeWS(sessionId)`），加一条分组通道要动会话管理与重连逻辑，代价远大于收益；而验收卡的分子是往返笔数，分钟级刷新已远超需要。成员级实时数据仍走既有 WS status |
| D-11 | **[P0-4] 评审拍板**：`exits` / `funding` 同时作为顶层键与 `acceptance` 内的镜像存在 | 顶层键保证「关掉周期模式的对照臂」可见（这是 L1 的必要臂）；镜像保证 `diagnostics` 与 tooltip 就近取数。约束是**同一对象引用、不得二次装配**（W-9 守它） |

---

## 十、非目标（本轮明确不做）

- 不做资金费的**历史回补**（只覆盖会话生命周期内的结算点；开会话之前的持仓不在本系统管辖内）。
- 不做逐笔（往返级）资金费归属——`positions-history` 是持仓级，强行对齐会引入 R-2 的结构性偏差。
- 不改动 G1/G2/G3 任何门禁行为，不调整任何周期预设的目标区间（本轮只加观测与判据）。
- 不做「验收未通过即自动停止会话」的自动干预——验收卡是**给人看的放行门槛**，把它接成自动执行器
  会让一个统计读数获得平仓权限，那是另一个特性、需要单独的安全评审。
- 不做交易所侧持久化止盈止损的**自动补挂 / 重挂**（上一轮 §2.8 只做了三条提示型防线；自动重挂
  涉及下真单，属于另一轮的安全边界）。
- 不改 `live_auto`（非 AI）链路的任何行为。

---

## 评审结论

### **有条件通过**（approved with conditions）

**总体判断。** 选题是对的：调查原文点名的五个验收指标里，缺的两个（资金费后净收益、退出来源）
确实缺，恒真的那个（`turnoverPerDay` 与 `avgHoldSeconds` 代数等价）确实恒真——三条轴与一张卡都对着
真实缺口，没有一条是从现象跳出来的臆测。四维评估：

- **可行性**：底层能力都在。资金费的交易所真值确实是零新增网络面（`exchangeFundingFeeUsd` 每 300s
  已在算），退出来源确实能靠 `explain.meta` 一处打标拿到落库 / WS / 导出三条通路，回测侧
  `_setup` 是 async 且已持有 `self.rest`，公共端点可达。**唯一被高估的是「不知道就说不知道」这条
  能力**——它在当前取数链上根本不存在（P0-1），必须先建。
- **完整性**：v1 的边界覆盖在纯核心层面相当扎实（三态 `basis`、样本不足恒 `insufficient`、
  `null` 与 `0` 可分），**但装配层与呈现层有系统性缺口**：运行期卡片没有活的数据通道（P0-3）、
  对照臂的数据记了却看不见（P0-4）、多成员历史没有合并落点（P1-9）、`wall_seconds` 三世界口径
  未定义（P1-3）。这四条的共性是同一个失败模式——**后端算出来了 ≠ 用户看得见**，与上一轮终审
  记下的教训完全同款。
- **一致性**：与 `CLAUDE.md` 的阈值纪律和仓库既有范式（`ProfitGuardRuntime` / `CostRuntime` 同构、
  纯核心与文案分离、加法键 + 冻结用例、整键缺席即逐字节回退）高度吻合。`session.py` 的 8 行硬预算
  与「业务状态一律下沉」是正确的姿态。不一致处已在 P1-10 / P1-11 逐条改正（类型文件、配置读取路径）。
- **规模适当性**：4 新建后端 + 若干修改 + 6 新建前端，对「三条轴 + 一张卡」是**恰当**的，没有
  过度设计；`exitMix` 不参与判定、不做自动干预、不做逐笔资金费归属这三条自我克制尤其正确。
  评审在此基础上只增加了必要的接线（lifetime 累加器、合并落点、轮询通道），没有扩大功能面。

**已闭合。** 4 个 P0 与 11 个 P1 全部在正文逐条修复并带 `[P0-x]` / `[P1-x]` 反查标记；8 个 P2 中
有 6 条顺手改进了正文，2 条（P2-1 / P2-2）是论据更正。**当前无未解决的 P0 / P1。**

### 放行条件（下游代码开发节点必须逐条落实并在实施报告中逐条回答）

| # | 条件 | 对应 |
|---|---|---|
| 1 | **先做第 0 步**：`intervalKnown` 的信息通路先于 `funding_ledger.py` 落地，F-1 的判据必须是 `interval_known` 而非 `interval_s <= 0`；`cost_model.py` / `cost_text.py` 零 diff | P0-1 / G-6 |
| 2 | `custom` 全默认（`target_hold_min_s == 0`）跑通一轮完整 status 装配**不抛异常**，验收卡对应三行 `insufficient`，整卡非 `pass` | P0-2 / A-11 |
| 3 | 运行期验收卡实测会更新（≤30s 内看到 `roundTrips` 变化）；轮询**只走窄 action**，前后端各一条用例证明成交流不被清空 | P0-3 / D-10 |
| 4 | `exits` / `funding` 在**四个装配点**都是顶层键；`horizon_rule=None` 的会话与回测上退出来源卡照常渲染 | P0-4 / W-9 / W-11 |
| 5 | 验收卡的成本三项取 `cost.lifetime.*`；`>200` 笔往返的用例断言它与环形口径 `costRatio` 不等；两处文案都带口径词 | P1-1 / A-6 / R-13 |
| 6 | `build_input` 吃 `acceptance_input_parts()` 的**原始计数器**；A-15 的「round 反乘 ≠ 精确合并」用例通过 | P1-2 |
| 7 | `wall_seconds` 三世界口径按 §2.4.1 表实现，回测从**首决策根**起算，各一条用例 | P1-3 |
| 8 | 回测资金费计提**独立于** `_track_horizon_epoch`，未装配周期模式的回测 `aiStats.funding` 非空 | P1-4 / W-11 |
| 9 | `backtest.py` 两处 `funding=None` 实参**零改动**（AST 断言）；不对称在 notes 与 §九 D-8 双处披露 | P1-5 / G-5 |
| 10 | `fundingCostUsd` 在「平仓 → 结算延迟 → 结算到账」序列中**单调不减**；三道可信判据全部实现 | P1-6 / F-11 |
| 11 | 新总闸并进 `FundingCache.enabled` 析取式；模拟盘 / 无凭证会话的 note 说出**是哪一道门没过** | P1-7 / W-13 |
| 12 | `session_end` 归 `windowEnd` 并计入 `truncatedSamples`；截断样本**不剔除**、只在 note 披露 | P1-8 / E-19 / A-14 |
| 13 | `_finalize_run` 落 `acceptanceInput`；分组历史在**后端**合并成一张卡；`db/ai_auto_repositories.py` 进变更计划 | P1-9 / W-12 |
| 14 | 前端类型改**五处**（含 `aiTradeHistoryTypes.ts`）；防吞键用例守的是 `groupStateFromStatus` | P1-10 |
| 15 | 本轮新增代码中不存在 `self.settings`（会话内配置一律 `self.executor.settings`），提交前 grep 自证 | P1-11 |
| 16 | `session.py` 净增 ≤ 8 行（原放行条件 3 保留）；超出即在本文档末尾登记欠账并说明，**不得**默默超支 | §2.2.3 / R-9 |
| 17 | 三总闸全关时 G-1～G-6 六条冻结门禁全绿；`live_auto/executor.py` 与 `engine/simulator.py` 零 diff | §6.1 |
| 18 | 全量 `pytest` 与**干净 HEAD worktree 实测**的红灯基线逐条一致（不要靠记忆；已知基线：`test_ai_trade_prior_session.py` 7 条、`test_factor_library_expansion.py` 7 条孤儿文件、`test_agent_ws.py` 顺序 flake） | §八 第 9 步 |
| 19 | `funding-rate-history` 的**真实响应键名**必须先打一发核对并把结果写进实施发现段；不符即如实降级为 `unavailable`，**绝不硬编猜测映射** | R-4 |
| 20 | **L1 纪律不可绕过**：样本外净额回测的验收卡结论为 `pass` 之前不得放大实盘资金；`insufficient` 时只能延长回测区间，**不得下调 `sampleFloor`** | §6.4 |

### 留给下一轮的欠账（本轮有意不做，已在 §九 登记）

1. **D-8**：回测的成本估算（G3 迟滞 / edge 门禁）不含资金费而实盘含——修它必须与 G3 阈值一起重估，
   属于下一轮「门禁行为」范围。
2. **D-9**：`cost_model` 在结算周期未知时继续按 8h 保守估算——与记账层的处置不对称，是有意的，
   但下一轮若要统一，必须连 `cost_text` 的冻结基线一起重做。
3. `session.py` 已 1727 行、`attribution.py` 引入了第二套口径——两者都在既有拆分债上再加一笔，
   本轮用硬预算与口径标注压住，但拆分本身仍未做。

---

## 实施过程发现的方案缺陷

按 CLAUDE.md 与本文档《约束》的要求逐条登记：凡是与 v2 正文不一致的地方，都在这里写清楚
「原文怎么写、为什么不能照写、实际怎么做」，并给出可核对的证据。**共 12 条，其中 4 条是阻断级**
（照原文实现会造出死码、静默失真、或一个恒不成立的门禁）。

### I-1（阻断级）：§3.2 要改 `cost_text.py`，与 G-6 / 放行条件 15 的「`cost_text` 零 diff」直接冲突

- **原文**：§3.2 变更计划列「`ai_auto/cost_text.py` | 成本段追加资金费已计提一行」；§2.2.5 也写
  「`cost_text` 在既有成本段后追加一行」。
- **冲突**：§6.1 的 G-6 与《放行条件》第 15 条同时要求「`cost_model.py` / `cost_text` 在本轮 diff
  中**零改动**」。两条判据不可能同时满足，而后者是**机械可验证**的那一条（`git diff --name-only`）。
- **处置**：取 G-6。资金费已计提那一句渲染放进 `acceptance_text.format_funding_accrued_for_prompt`，
  由 `prompts._format_cost` 在既有成本段**之后**追加。用户可见结果与 §2.2.5 逐字一致（同一句话、
  同一位置、同样带「它本身不构成离场理由」的反偏置后半句），而 `cost_text.py` / `cost_model.py`
  逐字节零 diff（`test_shared_execution_files_have_zero_diff` 按 `git diff` 断言）。
- **代价**：变更面从 `cost_text.py` 挪到 `prompts.py`（+3 行，含一段说明为什么不在 cost_text）。

### I-2：`merge_funding_into` 与 W-9「同一对象引用」互斥，故未实现

- **原文**：§2.2.3 / §3.2 让 `CostRuntime` 新增 `merge_funding_into`，由 `_finalize_run` 调用。
- **问题**：`acceptance` / `exits` / `funding` 若各自走一条 merge，每处都会**各装配一次**，与 W-9
  「四个装配点的 `exits` 与 `acceptance.exitMix` 必须是同一个对象」直接矛盾。
- **处置**：改为**单一装配点** `horizon_acceptance.build_bundle(horizon, cost, …)`，一次产出四键；
  `session.acceptance_bundle()` 是它唯一的会话侧入口，status / 落库 / 分组合并三处共用同一次调用。
  `merge_funding_into` 未实现——实现了也没有调用点，只会是死码（CLAUDE.md §四）。

### I-3（欠账登记）：`session.py` 净增 **15 个代码行**，超出 ≤8 行硬预算

放行条件 16 明确「超出即在本文档末尾登记欠账并说明，**不得**默默超支」。逐条明细：

| 挂点 | 代码行 | 说明 |
|---|---|---|
| `HorizonRuntime(rule, net_mode=…)` | +1 | 退出账本的净额口径必须与 `AttributionLedger` 同源 |
| `_dispatch` 的 `self._cost.note_open(...)` | +1 | §2.2.3 预算表已计入 |
| `_inject_explain_meta` 的 exitSource 打标 | +3 | **预算表遗漏**：§2.2.3 的表里没有这一项，它写在 §2.3.2 |
| `schedule_maintenance(..., mark_px=…)` | +1 | 实参换行 |
| `_status_payload` 的 `**self.acceptance_bundle()` | +1 | §2.2.3 只算了 `acceptance` 一键，而 [P0-3]/[P0-4] 把它扩成三键 |
| `acceptance_bundle()` 新方法 | +5 | §2.2.3 预算里的 `acceptance_input()` 是 4 行；本方法多一行因为它同时承载四键 |
| `_finalize_run` 的 `update(ai_stats_keys(...))` | +1 | §2.2.3 预算表已计入 |
| import 两行 | +2 | 预算表未计 |

合计 **+15**，超支 **+7**。超支的三个来源都是 v2 正文自身的口径不一致（预算表按 v1 的「只加
`acceptance` 一键」算，而 [P0-3] / [P0-4] 在评审阶段把它扩成了三键 + 一处打标）。业务状态**没有**
被塞进会话：账本住在 `CostRuntime` / `HorizonRuntime`，装配住在 `horizon_acceptance`，会话只留薄
封装调用。`session.py` 由 1727 行增至 1749 行（含注释），该拆分债与上一轮 I-7 合并计入。

### I-4（阻断级）：回测的退出来源不能挂在 `HorizonRuntime.note_close` 上

- **原文**：§2.3.3 让退出账本住在 `HorizonRuntime`，回测经 `_track_horizon_epoch` 消费打标槽。
- **问题一（连坐）**：`_track_horizon_epoch` 首行是 `if self.horizon_rule is None: return`，挂在它
  里面 ⇒ **未装配周期模式的对照回测拿不到任何退出分布**，而 [P0-4] 与 §6.4 的 L1 对照臂正是要跑
  那个配置。这与 [P1-4] 对资金费的判断是同一个连坐，v2 只修了资金费那一半。
- **问题二（空成交）**：回测里 `_track_horizon_epoch` 调的是 `note_close({}, bar_ts)`——传的是
  **空 dict**，账本拿不到 `pnl` / `fee` / `reason`，净额与来源会恒为 0 / unknown。
- **处置**：`HorizonRuntime` 增 `record_exits: bool = True`；回测构造时传 `False`，由 runner 自己
  在新钩子 `_track_ledgers(bar_ts, close)` 里记账——该钩子**只受各自总闸门控，与 `horizon_rule`
  无关**。实盘侧一字不变（仍在 `note_close` 里记，且记录发生在 `rule is None` 早退之前）。
  用例：`test_funding_and_exits_survive_without_a_horizon_rule`、
  `test_horizon_runtime_backtest_mode_does_not_double_record`。

### I-5：`_bar_ts_index` 必须无条件构建

原先它只在「装了利润保护 / edge 门禁 / ATR 止损 / 周期模式」四者之一时才建。资金费 epoch 的
`opened_wall` 与退出样本的持仓时长都要它，且按 I-4 必须与周期模式无关 ⇒ 提到那个条件之外无条件建
（它只是一个 int 列表，代价可忽略；`_atr_series` 的既有条件一字不动）。

### I-6（阻断级）：`AcceptanceInput` 必须单独携带 `exit_hold_samples`

- **原文**：§2.4.1 的 `AcceptanceInput` 只有 `exit_hold_sum`。
- **问题**：合并后重建退出分布时，若按 `exit_hold_sum` 的键集反推每个来源的时长样本数，就会把
  `hold_seconds is None` 的样本算进分母。而在**未装配周期模式的对照臂上，每一笔的 `hold_seconds`
  都是 `None`**（时长由 `HorizonRuntime` 提供，它此时不记录）——平均持仓时长会当场被拉向 0，
  于是「对照臂看起来持仓极短」这个结论完全是统计口径造出来的。
- **处置**：`exit_counters()` / `AcceptanceInput` / `input_wire` 各增一项 `exitHoldSamples`，
  `ExitLedger.from_counters` 直接读它。E-17 与
  `test_from_counters_round_trips_through_exit_counters` 守这条。

### I-7：`FundingLedger.stats()` 增一个可选形参 `unavailable_reason`

§4.5 的签名是 `stats(self, *, settled_usd)`，但放行条件 11 要求「模拟盘 / 无凭证会话的 note 说出
**是哪一道门没过**」。三道门的判据（对账总闸 / 凭证 / 样本截断）只有 `CostRuntime` 知道（它才持有
`last_reconcile` 与 `creds`），纯核心无从得知。故增一个**可选**关键字形参承载原因短语；不传即退回
§4.5 的原签名，既有调用与用例零影响。判据实现在 `CostRuntime._settled_funding_usd()`（返回
`(金额|None, 原因|None)`），`test_settled_funding_gate_names_the_door_that_failed` 逐门断言。

### I-8：`backtest_manager.py` 无需改动（§3.2 该行是 no-op）

§3.2 列它「透传新总闸（回测资金费运维开关）」。实测：`settings` 已作为整体透传进 `AiBacktestSpec`
（`_build_spec` 里 `settings=settings`），runner 直接读 `spec.settings.ai_trade_funding_backtest_enabled`。
再加一层字段转发只会造出第二个真源。**该文件零改动**。

### I-9：新增 `hold_horizon.rule_from_wire`（§3.2 未列）

[P1-9] 要求历史·分组详情在**后端**合并并判定一次，而判定需要周期契约；历史侧唯一能拿到契约的地方
是 `config_json.tradingHorizon` 这份 wire，而仓库里此前只有正向的 `rule_wire`。故新增纯反序列化
函数，只复原验收判定用得到的字段（目标区间 / 标识 / 标题）——三道闸的运行期参数在一次**已结束**的
会话上没有消费方，凭空复原它们只会给人「这条历史记录还能重跑」的错觉。它不参与任何门禁判定，
`evaluate_horizon_gate` 输入输出零改动（G-2 仍成立）。

### I-10（阻断级）：G-5 的 AST 断言按原文字面写会**恒不成立**

- **原文**：§6.1 G-5 / 放行条件 9 说「`backtest.py` 的**两处** `funding=None, funding_enabled=False`
  （成本模型入参）在本轮 diff 中零改动——按 AST 断言这两个调用的实参字面量未变」。
- **实际**：`backtest.py` 里 `query_for_preview` 只有**一处**（`_horizon_facts`，带
  `funding=None, funding_enabled=False`）；另一处 `funding_enabled=False` 在
  `evaluate_edge_gate(...)` 的调用上（`_run_decision`），它**没有** `funding=` 实参（走默认 `None`）。
  照原文去找「两个 `query_for_preview` 调用」的断言会恒 `assert 1 >= 2` 失败——一个恒失败的门禁
  与一个恒通过的门禁一样没用。
- **处置**：按两处**真实现场**分别断言（`query_for_preview` 断言三项；`evaluate_edge_gate` 断言
  `funding_enabled is False` 且**不出现** `funding=` 实参）。两处实参本轮均零改动。

### I-11：单槽打标改为「游标 drain + 逐笔归因」

- **原文**：§2.3.2 用一个单槽 `self._pending_exit_source`，并论证「一根内至多产出一条 close
  `TradeRecord`」（P2-2 修正后的论据是 `_close_at` 在 `pos.side == "flat"` 时空转）。
- **问题**：**反手**会在同一根内先由 `fill_pending` 产出一条 close（平旧仓）再开新仓，随后
  `apply_risk` 完全可以对**新仓**触发止损再产出第二条 close。此时单槽已被第一条消费，第二条会落进
  「槽为空」分支——所幸它的 `reason` 是 `stop_loss`（确定性字面量）故仍能定源，但那是运气不是设计。
- **处置**：改为游标 `_trade_scan_idx` 逐笔 drain `main.trades`，按发生序逐条归因。槽仍只有一个
  （运行时一根内至多提交一次平仓意图），但消费是**逐笔**的：第一条拿槽，其余落回 reason 字面量。
  这比单槽更强且代码更短。R-6 的 `stampConflict` 计数照常实现（进 notes）。

### I-12（阻断级）：回测的退出账本必须 `net_mode=True` 且喂**整笔往返腿费**

`SimBroker._close_at` 里 `net_pnl = gross − (entry_fee + exit_fee)`，而 `TradeRecord.fee` **只是
平仓腿**。若照实盘那样把 `fee` 直接喂进账本：

- `net_mode=False` ⇒ 净额会被**再减一次**平仓腿费（双重扣费）；
- `gross = pnl + fee` 会**少算一条开仓腿** ⇒ `feeToGross` 系统性偏小 ⇒ 第 4 行门槛形同虚设，
  而调查样本里这一行的实测值是 **5.23**（灾难级），正是本卡最需要抓的那一项。

处置：回测账本 `net_mode=True`，`fee` 传自上一笔平仓（不含）起累计的**全部腿费**（与
`reconcile.local_net_view` / `db/ai_auto_repositories._pair_round_trips` 的腿费累计规则同源）。

---

### 附一：R-4 / 放行条件 19 要求的真实响应键名核对结果

对 `GET https://www.okx.com/api/v5/public/funding-rate-history?instId=BTC-USDT-SWAP&limit=3`
打了一发真实请求，返回样本（2026-08-09 实测）：

```jsonc
{"code":"0","data":[
  {"instId":"BTC-USDT-SWAP","instType":"SWAP","fundingRate":"0.000007399680428",
   "realizedRate":"0.000007399680428","fundingTime":"1786262400000",
   "formulaType":"withRate","method":"current_period"}, …]}
```

结论：**键名与 §2.2.4 第 1 点的假设完全一致**（`fundingTime` 毫秒字符串 / `fundingRate` /
`realizedRate`），无需降级为 `unavailable`。两点补充，均已落进实现：

1. OKX 原样返回是**按 ts 倒序**（1786262400000 > 1786233600000 > 1786204800000），故
   `get_funding_rate_history` 归一后**强制按 ts 升序**返回；
2. 实测相邻两点差为 28800s（8h），但该值**不得**被硬编码——`BacktestFundingCurve` 只读真实结算
   时刻、不做任何平铺（§九 D-7）。`realizedRate`（结算实际值）优先于 `fundingRate`（当期预告值）。

### 附二：干净 HEAD worktree 的红灯基线实测（放行条件 18，非记忆）

`git worktree add --detach <tmp> HEAD` 建干净工作树后跑全量 `pytest`，与本轮工作树逐条对账。

**过程中发现一条会让基线读错的陷阱，一并记下**：干净 worktree **没有** `services/quant/.env`
（该文件被 `.gitignore` 排除、只存在于真实仓库）。缺它会让 `resolve_active_provider` 取不到默认
模型，于是 `test_ai_backtest_manager::test_profit_rule_is_forwarded_to_spec` 等 3 条在基线里**额外
变红**——那不是代码差异，而是环境差异。把 `.env` 复制进 worktree 后 3 条全绿（已实测）。
**取基线必须连同被 gitignore 的运行期文件一起对齐，否则基线本身就是假的。**

对齐环境后的实测对账（两次全量各跑一遍，均为串行、独占）：

| 红灯来源 | 本轮工作树 | 干净 HEAD（已补 `.env`） | 结论 |
|---|---|---|---|
| `test_ai_trade_prior_session.py` | 7 failed | 7 failed | HEAD 已红，非本轮引入 |
| `test_factor_library_expansion.py` | 7 failed | 文件不存在 | 未提交的孤儿测试文件，属另一特性 |
| `test_agent_ws.py`（1 条） | 0 failed | 1 failed | 共用临时 DB 的**顺序 flake**（单文件跑必绿，两侧各出现过一次） |
| 其它 | 0 failed | 0 failed | — |

**无新增红灯。** 干净 HEAD 全量：**8 failed / 2709 passed / 1 skipped**；本轮工作树全量：
**14 failed / 2931 passed / 1 skipped**。多出的 7 条红灯全部来自那个孤儿文件；多出的 222 条绿灯
是本轮新增用例（5 个新后端测试文件 111 条）与既有文件补桩。

前端 `vitest`：**143 文件 / 1548 用例全绿**（本轮新增 5 文件 / 32 用例）；`tsc` 除既有
`WeightBatchArena.test.tsx` 一条外无新增错误。
