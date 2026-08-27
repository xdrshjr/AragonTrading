# AI 自动交易 · 行情断连自愈（ai-trade-feed-self-healing）

> **版本**：v2（评审节点修订；v1 = 方案设计节点产出）
> **作者**：Solution Architect（Anthropic Engineering）
> **评审**：Senior Reviewer（Anthropic Engineering），2026-08-27
> **日期**：2026-08-27
> **状态**：已评审 —— **有条件通过**（条件见文末《评审结论》）
> **本文是唯一实施依据**，自包含——实现者不需要读任何历史 spec。文中每一处「今天的行为」都标注了
> 可核对的文件名与行号（基于 `master @ 039fa80` 的工作树）。

---

## 〇、评审记录（Review Notes · v2）

> 评审口径：**可行性 / 完整性 / 一致性 / 规模适配**四维，逐节对照 `master @ 039fa80` 工作树实读
> （不采信 v1 的任何行号与结论，全部重新取证）。下表 **P0 / P1 已在本文正文中逐条修正**，修正处
> 均以 `【R:P0-x】` / 【R:P1-x】 就地标注；**P2 不阻断**，原样列出供实现者判断。
>
> v1 的根因链（RC-1 ~ RC-7）**全部复核成立**，行号逐条命中；复核明细见 §〇.3。

### 〇.1 P0（阻断级：不改就会造出比现状更坏的形态）

| 编号 | 维度 | 问题 | 取证 | 修正落点 |
|---|---|---|---|---|
| **R:P0-1** | 完整性 | **`plan()` 判定序把「WS 已死」压在「行情看起来新鲜」之下 ⇒ REST 长生保活、WS 永不重建。** v1 判定表第 2 行（`feed_age <= stale_after ⇒ clear/none`）排在第 3 行（`not ws_alive ⇒ relink`）之前。一旦 L2 补取写了 `_last_candle_wall = now`，此后**每一个 tick 都在第 2 行返回**，`ws_alive` 再也不会被读到。后果：一条死掉的 WS 被 60 秒一次的 REST 轮询永久掩盖，成员在 `classify_member` 里回到 `armed`，恢复卡因 stage 回到 `fresh` 而消失——**把一个「看得见的停摆」变成了「看不见的停摆」**，比现状严重。 | `ai_auto/lifecycle.py:120-123`（`feed_stale` 只看 `feed_age_s`）；`session.py:1412-1427` | §3.3.1 判定表重排 |
| **R:P0-2** | 可行性 | **自愈成功后没有任何一条唤醒源会接手。** v1 §3.3.2 称「清 `_stale_degraded` 后让既有的 `_maybe_catchup_wake()` / 下一个 `_maybe_interval_wake()` 自然接手」——实读为假：① `_maybe_catchup_wake` 全仓**唯一调用点**是 `_run_decision_round` 的 `finally`（`session.py:1631`），自愈路径根本走不到它；② 即便直调，`plan_catchup` 的第一道判据是 `if not self.backlog_enabled or not self._backlog.pending: return None`（`wake_runtime.py:278`），而 REST 补取不产生任何待办；③ `_maybe_interval_wake` 的第一道判据是 `self.wake_interval_s <= 0`（`session.py:1320`），而 `wake_interval_s = 0`（`trend_hold` 档的常见配置）下它恒不唤醒；④ `_maybe_signal_flip_wake` 要求 `_live_row is not None`（`session.py:1686`），而补取从不写 `_live_row`。**净结果：`wake_interval_s = 0` 时自愈会抹掉横幅、且产出零个决策轮。** | `session.py:1320 / 1631 / 1686`、`wake_runtime.py:271-295` | §3.3.2「唤醒交接」段 + §7.3 新用例 |
| **R:P0-3** | 一致性（安全） | **活性补取把 `_last_px_wall` 写成 `now`，会用最多 3 根 K 线之前的价解锁利润保护层。** `ProfitGuardRule.max_px_age_s = 90.0`（`profit_guard.py:109`）：判定价超龄 ⇒ `unavailable` ⇒ 本轮零动作（fail-open）。v1 的新鲜判据是 `now - (ts + bar_seconds) <= stale_after_s`（= `bar × 3`），1H 周期下允许一根**3 小时前**收线的根被判「新鲜」，随后 `_last_px_wall = now` 让保护层认为判定价是「0 秒前的」，据此执行棘轮与兑现——**正是 `okx/session_link.py` 模块 docstring 专门写下的那条禁令（profit-protection D-1）在新通道上的原样复发**，也正是 v1 自己 R-3 想防的事。 | `ai_auto/profit_guard.py:89 / 109 / 154`、`okx/session_link.py:14-20` | §3.3.2 新鲜判据 + 写值口径 |

### 〇.2 P1（必须修：会让特性在真实现场空转、说谎或假超时）

| 编号 | 维度 | 问题 | 取证 | 修正落点 |
|---|---|---|---|---|
| **R:P1-1** | 完整性 | **`FeedReadings.link_state` 有写点没读点。** 它被装进读数结构体，8 条判定规则**一条都没读它**——这正是本仓库反复出现的「算了但没人看」形状。而 `link_state == "down"` 是登记簿已经算好的、比 `feed_age` 更硬的证据。 | v1 §3.3.1 判定表全表 | 并入 §3.3.1 第 2 行 |
| **R:P1-2** | 一致性 | **§3.5.3「轴 A 的重连会把真正坏掉的通道推向 `down`，芯片自然变红」与登记簿实现相反。** `note_ws(event="connected")` 会把 `consecutive_failures` 清零并把 `last_message_ts` 推到 `now`（`link_state.py:133-138`），而 `down` 需要 `consecutive_failures >= down_failures(=2)`。数据静默重连的每一轮都夹着一次 `connected`，故计数**恒不超过 1**，`derive_state` 对这一形态**永远返回 `degraded`（"不稳"）而不是 `down`**。真正会变红的只有「连都连不上」那一形态（`_open` 前抛异常 ⇒ 无 `connected` ⇒ 两次即 `down`）。 | `link_state.py:84-93 / 129-141`、`ws_supervisor.py:108-111` | §3.5.3 + §7.6 A3 |
| **R:P1-3** | 一致性 | **`feedRecovery`「整键缺席」的纪律与它所在的那个 wire 自己的纪律相反。** `member_lifecycle_wire` 的 docstring 明写「**全部键恒下发，绝不因为『本轮没数据』而整键缺席**——前端要能区分『没数据』与『后端没上线这个键』」（`lifecycle.py:141-143`）；v1 引用的**正是这同一句话**，却用它论证了相反的做法。`exitCommitment` 的键缺席纪律属于另一个 wire（`session.horizon`），不能跨对象套用。 | `ai_auto/lifecycle.py:141-160` | §3.5.1 / §4.1 |
| **R:P1-4** | 完整性（HCI） | **REST 生命维持期间界面会显示「已武装、一切正常」。** 见 R:P0-1 的后果链：`link_state = degraded` 不在 `_LINK_DOWN_STATES` 里、`feed_age` 被补取压住 ⇒ `classify_member` 判 `ARMED_OK` ⇒ 横幅消失、恢复卡消失。用户没有任何一条路知道「这个标的现在靠 60 秒一次的 REST 吊着」。 | `lifecycle.py:49 / 118-124` | §3.3.1（随 R:P0-1 重排解决）+ §3.5.2 文案 |
| **R:P1-5** | 可行性 | **`/relink` 会在它最该起作用的场景下假超时。** `apps/web/lib/httpTimeout.ts` 的长跑白名单里没有它 ⇒ 落 `FAST_MS = 20_000`。而 A4 的剧本正是「网络仍不通时点【立即重连】」，此时服务端 `fetch_missing` 要走完整的 REST 重试 / 退避。超过 20 秒 ⇒ 客户端抛超时 ⇒ 用户在唯一一个兜底按钮上读到「本次操作结果未知」——**恰是 `httpTimeout.ts` 那段 docstring 写下来要消灭的失信形态**。 | `apps/web/lib/httpTimeout.ts:17-70`、`aiTradeApi.ts:22-24` | §3.3.4 + §4.2 + §5.2 新增死线 |
| **R:P1-6** | 完整性 | **`_last_board` / `_last_bar_index` 的刷新方式未定义，而它无法在 `okx/session_link.py` 里正确完成。** 它们由 `evaluate_signal_board(self._specs, self._shadows, frames, i)` 产出（`session.py:1476-1481`），而该函数是**有状态的逐根影子回放**（`signals.py:225-231`：「暖机段调用本函数但丢弃返回值，仅为逐根种入影子与策略内部状态」）。只在最后一根算一次 board，等于让影子**跳过**了中间的根；不算，则 `_maybe_interval_wake` 会拿一份 21 小时前的化石信号板起真实资金决策。两条都不可接受。 | `ai_auto/signals.py:223-231`、`session.py:1476-1481` | §3.3.2 新增 `note_live_bar` 回调 |
| **R:P1-7** | 一致性 | **§3.2 的阈值算式与它自己的默认值、以及 §7.1 的冻结用例互相矛盾。** `min(max(bar × 1.0, 120), 900)` 在 `bar ≥ 15m` 时**恒等于 900**；故 §3.2 的「1m～6H 全部落在 120～900 秒之间」是一句同义反复，而 §7.1 的 `bar_seconds=3600 ⇒ 3600` **算出来是 900**，是一条写进「唯一实施依据」的、必然变红的断言。 | v1 §3.2 / §7.1 | §3.2 说明 + §7.1 用例表 |
| **R:P1-8** | 一致性 | **§7.6 A1 的验收数字错了 7.5 倍。** A1 用的是 15m 会话 ⇒ `data_silence` 预算 = `min(max(900, 120), 900)` = **900 秒**，不是 A1 写的 120 秒。按 120 秒验收会把一次**正确**的实现判成不合格。 | 同上 | §7.6 A1 |
| **R:P1-9** | 规模适配 | **恢复卡的挂载契约与「`AiTradePanel.tsx` 净增 2 行」互斥。** `feedRecovery` 是**成员级**的，而 v1 指定的挂载位（横幅 `:153` 与 `TradabilityStrip` `:159` 之间）是**分组级**上下文；一个卡片挂在那里无法表达 5 个成员。要么组件自己吃 `members` 数组，要么面板里写 `.map()`——后者在 797/800 行的文件上会直接顶穿 CLAUDE.md 红线。 | `AiTradePanel.tsx:153 / 159 / 174`（797 行） | §3.5.2 明确入参契约 |
| **R:P1-10** | 规模适配 | **`session.py` 的净增被显著低估。** v1 R-6 称「会话侧净增 ≈ 90 行」并把拆分留成「超出即再拆」。按 v1 自己的清单实算：`_apply_feed_plan` + 五个动作方法（≤25 行 × 5）+ `_feed_readings` / `_feed_config` / `_feed_recovery_key` / `relink_feed` / `note_live_bar` + 7 个字段 + `_rearm_feed(force=)` + `_latest_mark_px` 重写 ≈ **+190 ~ 220 行**，把 2923 行推到 ~3130。「超出即再拆」这条逃生门在本仓库历史上从未被真正执行过。 | `session.py`（2923 行）、既有范式 `horizon_runtime.py` / `discipline_runtime.py` / `cost_runtime.py` | §3.3.3 + §六 清单：`feed_runtime.py` **前置为必做** |
| **R:P1-11** | 完整性 | **`relink_attempts` 的复位点选错了。** v1 定为 `_feed_clear`；但 L2 补取成功时是 `_feed_backfill` 自己清 `_stale_degraded`，`_feed_clear` 此后再也不会被走到 ⇒ 计数**永不复位**，一次早已痊愈的会话会带着陈年计数走到 `escalate`。 | v1 §3.3.3 末段 | §3.3.3：复位点改为 `feed_candle` 确认根分支 |

### 〇.3 P2（观察项，不阻断）

| 编号 | 观察 | 建议 |
|---|---|---|
| **R:P2-1** | `_PumpState` 是无默认值在前、有默认值在后的 dataclass；`last_data: float` 若不带默认值就必须同时改 `_open()` 的构造点（`ws_supervisor.py:145`）。 | 实现时二选一即可，用例会立刻发现。 |
| **R:P2-2** | `ws_supervisor.py` 模块 docstring 说「应用层每 20s 发一个 ping」，而 `okx_link_ws_ping_interval_s` 的默认值是 **15.0**（`config.py:50`）。v1 沿用了这个旧说法。 | 属既有文档漂移，不在本轮范围；实现者不要按 20s 算延迟预算。检测延迟的真上界是 `tick_timeout` 的 `ceil_s = 1.0`（`retry.py:112-121`），与 ping 周期无关。 |
| **R:P2-3** | `public` 端点（trades/tickers/books5）的预算恒为下界 120 秒。冷门标的深夜可能真的 120 秒无盘口变动 ⇒ 周期性重连。 | 无害：`_on_recovered` 的补根判据是 `_pending_repair or downtime_s >= bar_seconds`，快速重连两者皆假 ⇒ 不打 REST、不推文案（`session_link.py:_on_recovered`）。但请在实现时把这条写进注释，否则下一个人会以为它在烧配额。 |
| **R:P2-4** | 会话级换链的门槛 `feed_age > stale_after × relink_factor` = `bar × 6`：1H 周期下要 6 小时。 | 修正后的第 2 行（`not ws_alive` / `link_state == "down"`）已覆盖绝大多数现场，该门槛只作为最后一层兜底；保留，但 §5.2 请显式写出这个换算结果。 |
| **R:P2-5** | §六 清单第 3 项要求 `SessionLink` 暴露 `force_repair()`，但全文再无第二处提到它，§3.3.4 描述的人工路径也不需要它。 | 已在 v2 的 §六 中删除该项（死条目进「唯一实施依据」比漏条目更危险）。 |
| **R:P2-6** | `ingest_ai_auto`（断线补根）同样不步进影子（`session_link.py:66-85`），故一次补根之后信号板同样是跳根算出来的。 | **既有缺陷，不在本轮范围**，但与 R:P1-6 同形；本轮新增的 `note_live_bar` 逐根回调可在下一轮直接复用到补根路径上。 |

### 〇.4 复核通过（逐条取证，实现者可直接采信）

- **RC-1 ~ RC-7 全部成立**，v1 引用的行号逐条命中：`ws_supervisor.py:174`（任何入站帧刷 `last_inbound`）、`:184`（`silence_timeout` 读的正是它）、`:202`（`pong` 提前 return）、`:222`（业务数据帧分支）；`session.py:1320 / 1405-1435 / 1459 / 1643 / 1686`；`ws_client.py:121-145 / 196-201`（`_run_endpoint` 只吞 `CancelledError`）。
- **§2.1 的锚点推理成立**：`derive_state` 的 WS 分支只到 `degraded`（`link_state.py:88-93`），`last_message_ts` 只由 `note_ws(event="message")` 刷新（`:143-145`），而那一行只在业务数据帧分支被调用 ⇒ `classify_member` 顺延到 `feed_stale`，与用户看到的那句话逐字吻合。
- **轴 A 的检测延迟不依赖 ping 周期**：`tick_timeout` 把每跳等待夹在 `[0.05, 1.0]` 秒（`retry.py:111-121`），故 `_expired` 每秒至少被求值一次，新判据的检测延迟 ≤1 秒。
- **`WSStreamConfig` 全仓只有一个构造点**（`ws_client.py:181`），`WSSupervisor` 也只有一个（`:192`），且**全仓不存在第二条走 supervisor 的连接**（私有账户侧只有 REST，`okx/private_client.py` 里没有任何 WS）⇒ 加 `bar_seconds` 字段不会波及订单 / 持仓通道，R-8 成立。
- **`_reason_code("data_silence")` 确为 `WS_DATA_SILENCE`**（`ws_supervisor.py:279-281`），无需改动。
- **成员级 wire 只有一个装配点**：`manager.py:1570` 走 `getattr(member.session, "lifecycle_wire", None)`，会话侧 `session.py:1153` 转发到纯核心 ⇒ v1「三处共用同一次装配」成立，`feedRecovery` 挂在会话侧不会漏。
- **前端 store 不逐键手抄成员 `lifecycle`**：`aiTrade.ts:1369-1387` 是整对象透传（`lifecycle: row.lifecycle ?? null`），故只要 `aiTradeTypes.ts` 的 `AiTradeMemberLifecycle` 声明了新键，它就能穿到组件——v1 的判断正确。
- **v1 引用的两条前端既有规则真实存在**：单标的不显示分数（`aiTrade.ts:1210-1215`）、不对不会到来的时刻读秒（`aiTrade.test.ts:1246-1249`）。
- **桌面打包透传确实无需改动**：`desktop/src/lib/config.js:80-82` 的白名单已含 `OKX_` 与 `AI_TRADE_`（实读确认，非推断）。
- **`_check_staleness` 重写不改 `live_auto_flatten_on_stale` 的可达性**：今天的 `elapsed <= stale_after` 早退排在强平分支之前，而 `elapsed > stale_after × 4` 蕴含 `elapsed > stale_after`（`stale_after >= 0` 时），故把强平分支提到最前是**逐字等价**的重写，R-5 成立。
- **测试落点全部存在**：`test_okx_ws_supervisor.py` / `test_okx_session_link.py` / `test_ai_trade_api.py` 在库；`_FakeWS` 替身在 `test_ai_trade_pause_session.py:476-487`（v1 引的 `:478` 落在类体内）。
- **`fetch_missing` 可直接复用为活性补取的取数层**（`gap_repair.py:30-68`）：它已带 `probe=True` 熔断豁免、已过滤未收线根、已按 `max_bars` 截断，本轮**不需要新增任何取数路径**（与该模块「不新增取数路径」的硬边界一致）。

---

## 一、概览（Overview）

AI 自动交易在多标的运行时，会出现「某一个标的的 K 线永久断连、Agent 从此不再为该标的做任何决策」
的现象。用户看到的原文是：

```
⚠ Agent 暂时不会做出新的决策（1/5 个标的未武装，其余照常交易）
ETH-USDT-SWAP：行情已 20 小时 59 分钟没有新 K 线，Agent 暂不响应；交易所侧止盈止损仍生效。
```

这句话本身是**正确且诚实**的——它由 `ai_auto/lifecycle.py:120-123` 的 `feed_stale` 分支生成，是上一轮
「生命周期真值化」的成果：界面没有撒谎，它如实说出了「这个标的的行情已经死了 21 小时」。真正的缺陷
不在这句话，而在**它后面什么都没有发生**：整整 21 小时里，没有任何一段代码尝试过把这条行情救回来。
一个持有真实资金仓位的量化 Agent，在行情通道静默之后进入了一个**没有出口的吸收态**——三条唤醒源
（K 线 / 墙钟定时 / 信号翻转）全部被同一个 `_stale_degraded` 标志挡住，而唯一能清除这个标志的，恰恰是
那条已经死掉的 WebSocket 通道自己。这构成一个闭环自锁：**feed 死 ⇒ 会话降级 ⇒ 所有唤醒停摆 ⇒ 只有
feed 能解锁 ⇒ 永不恢复**。

本轮要做的是把这条链路从「观测」升级为「自愈」，并且**只在证据允许的范围内自愈**。方案分四条轴：
轴 A 在 `okx/ws_supervisor.py` 补上**通道级数据静默**判据——今天的活性判据 `last_inbound` 被 `pong`
心跳刷新，于是「socket 活着、心跳正常、但 `candle*` 频道再也不推数据」这一形态永远不会触发重连；
轴 B 在会话侧把 `_check_staleness` 从「置个标志、推句文案」升级为**四级自愈阶梯**（降级 → REST 活性
补取 → 强制换链 → 升级告警），并明确「哪一根 K 线算当下的证据」这条语义边界；轴 C 修掉断流期间
`_latest_mark_px()` 恒返回 21 小时前收盘价这个**风控读数冻结**问题；轴 D 把横幅从一条死路变成一个
有恢复进度、有下一步预告、有兜底按钮的人机交互闭环。

本轮的立论有两条，写在最前面以防后人改坏它们：

- **L-1：`_last_candle_wall` 的语义是「我最后一次拿到*当下这一根*的时刻」，而不是「WS 最后一次推给
  我的时刻」。** 数据的**来源**（WS 推送 / REST 拉取）不该改变这个语义，只有数据的**时效性**（是不是
  刚刚收线的那一根）才该。既有 `okx/session_link.py` 模块 docstring 里那条「ai_auto 四字段禁写」的纪律
  针对的是**断线补根**（一次性补一段**历史**证据）；本轮新增的是**周期性活性补取**（每 60 秒取**最新
  一根**）。两者的证据性质不同，故必须是**两个函数、两条注释**，绝不复用同一个 ingest。
- **L-2：自愈绝不越权成风控。** 本轮不新增任何一条会导致自动平仓的路径；`live_auto_flatten_on_stale`
  的既有语义（默认 `False`）一字不动。自愈的全部权力边界是「重新拿到数据」，拿不到就**如实说不知道**，
  把主动权交回用户，而不是替用户猜一个价然后动真金白银。

---

## 二、现场取证与根因分析

### 2.1 症状为什么是 `feed_stale` 而不是 `feed_down`

`ai_auto/lifecycle.py:classify_member` 的判定顺序是
`stopped → paused → not_warmed → feed_down → feed_stale → armed`。用户看到的是 `feed_stale`，说明
`link_state` **不是** `"down"`。而 `okx/link_state.py:derive_state` 对 WS 通道的判据是：

```python
idle_since = h.last_message_ts if h.last_message_ts is not None else h.last_ok_ts
if thresholds.ws_silence_s > 0 and idle_since is not None and now - idle_since > thresholds.ws_silence_s:
    return "degraded"        # ← 只到 degraded，永远不会自己升级成 down
return "up"
```

`last_message_ts` **只**由 `note_ws(event="message")` 刷新，而那一行只在 `ws_supervisor.py:222-225` 的
**业务数据帧**分支里被调用（`pong` 在 `:202` 就 `return` 了）。也就是说：

> **登记簿早就知道这条通道 21 小时没有数据了，它把它记成了 `degraded`；
> 而 `classify_member` 只把 `down` 当作「链路不可达」，于是判定顺延到了 `feed_stale`。
> 事实一直在进程内存里躺着，只是没有任何人消费它。**

这条推理与用户看到的那句话**逐字吻合**，是本次根因定位的锚点。

### 2.2 七条根因

| 编号 | 根因 | 证据 | 级别 |
|---|---|---|---|
| **RC-1** | **通道级静默被连接级心跳掩盖（主因）**。`ws_supervisor._pump` 在**任何**入站帧后刷新 `state.last_inbound`（`:174`），包括 `"pong"` 与 `event` 帧；而 `_expired` 的 `silence_timeout`（45s，`:184`）读的正是这个量。于是「socket 活着、ping/pong 正常、`candle*` 频道静默」这一形态**永不触发重连**，也就永不产生 `down → live` 的状态迁移，`SessionLink` 的补根与告警全部空转。 | `okx/ws_supervisor.py:174 / 181-189 / 202 / 222` | P0 |
| **RC-2** | **会话看门狗只观测不自愈**。`_check_staleness` 全部动作只有两条：置 `_stale_degraded = True`、推一条 status 文案。它从不重订阅、不重建 WS、不走一次 REST。 | `ai_auto/session.py:1405-1435` | P0 |
| **RC-3** | **`_stale_degraded` 是吸收态（闭环自锁）**。三条唤醒源全部被它挡住——`_maybe_interval_wake`（`:1320`）、`_maybe_catchup_wake`（`:1643`）、`_preview_ready`（`:1686`）；而清除它的唯一路径 `_clear_stale_if_recovered` 要求 `_last_candle_wall` 前移，后者的**唯一写点**是 `feed_candle` 的确认根分支（`:1459`），即那条已经死掉的 WS。 | `ai_auto/session.py:1320 / 1429-1435 / 1459 / 1643 / 1686` | P0 |
| **RC-4** | **`_rearm_feed()` 只在 `resume()` 上被调用**。`ws_client._run_endpoint` 只吞 `CancelledError`，`supervisor.run` 抛别的异常时 endpoint task **静默死亡**、`is_alive` 转假；而运行中的会话**从不读** `is_alive`。`is_alive` 的 docstring 自己写着「这个属性就是那条静默的出口」——今天没有任何常驻代码去读它。 | `ai_auto/session.py:1081-1094`（唯一调用点在 `resume()` `:1067`）、`okx/ws_client.py:121-145 / 196-201` | P0 |
| **RC-5** | **补根重试依赖一次 down→live 迁移**。`SessionLink._pending_repair` 只在下一次 `_on_recovered` 才重试；若链路从此不再迁移（正是 RC-1 的形态），洞永远补不上。 | `okx/session_link.py:_on_recovered / _repair` | P1 |
| **RC-6** | **断流期间风控判定价一并冻结（安全）**。`_latest_mark_px()` 在窗口非空时**直接返回最后一根确认根收盘价**，永不回落 REST。于是断流期间 `executor.reconcile(mark, now_ts)` 与 `_maybe_session_exit(mark, now_ts)` 全程吃一个 21 小时前的价。利润保护有 `_last_px_wall` 新鲜度闸（会正确地判 `unavailable`），但**会话级止盈止损没有**。 | `ai_auto/session.py:1395-1403`、`_monitor_tick` `:1265-1275` | P1 |
| **RC-7** | **横幅是一条死路（HCI）**。它把事实说清楚了，但没有**动作**、没有**恢复进度**、没有**下一步会发生什么**。21 小时里用户唯一能做的是停掉整组——而那会连同其余 4 个正常交易的标的一起停掉。 | `apps/web/components/Chart/AiTradePanel.tsx:301-321` | P1 |

### 2.3 一句话总结

> 上一轮把「它没在交易」这件事**说清楚了**；本轮要把它**修好**。
> 今天的系统有三个各自独立、彼此不通电的真相持有者：`ws_supervisor` 知道连接活着、`link_state` 知道
> 通道没数据、`session` 知道 K 线不动了。**没有任何一条线把「没数据」翻译成「去把它拿回来」。**

---

## 三、技术设计

### 3.1 总体架构

```
                         ┌──────────────────────────────────────────┐
   OKX business WS ─────▶│ WSSupervisor._pump                        │
                         │  last_inbound (任何帧, 含 pong) ─ 45s     │  轴 A 新增：
                         │  last_data    (仅业务数据帧)   ─ N s ★    │  数据静默判据
                         └───────────────┬──────────────────────────┘
                                         │ 命中 ⇒ 返回 "data_silence" ⇒ 走既有重连路径
                                         │ ⇒ 自然产生 degraded → connecting → live 迁移
                                         ▼
                         ┌──────────────────────────────────────────┐
                         │ SessionLink.on_stream → _on_recovered     │ 既有补根（免费复活）
                         └───────────────┬──────────────────────────┘
                                         ▼
   monitor tick (5s) ───▶ ┌──────────────────────────────────────────┐
                         │ FeedWatchdog.plan(readings)  ★ 新纯核心    │  轴 B：四级自愈阶梯
                         │  L0 fresh / L1 degraded /                 │
                         │  L2 rest_backfill / L3 relink / L4 stuck  │
                         └───────────────┬──────────────────────────┘
                                         │ 会话侧只执行动作，不做判定
              ┌──────────────────────────┼─────────────────────────┐
              ▼                          ▼                         ▼
   ingest_ai_auto_live()          _rearm_feed(force=True)    push feedRecovery wire
   （REST 取最新根 → 若"当下" 才                                  ▼
     写四个墙钟字段 + 解锁唤醒）                        ┌────────────────────────┐
                                                      │ NotArmedBanner + 恢复行 │ 轴 D
                                                      │ +【立即重连】按钮       │
                                                      └────────────────────────┘
```

### 3.2 轴 A —— 通道级数据静默判据（`okx/ws_supervisor.py`）

**改动本体只有三处，全部在 `_PumpState` / `_expired` / `_consume` 上。**

1. `_PumpState` 新增字段 `last_data: float`（构造时 `= opened_at`：**连上了但从没推过数据同样要被判
   失活**，否则一次「握手成功、订阅 ack 成功、数据一帧不来」的连接会长命百岁）。
2. `_consume` 中，**只在业务数据帧分支**（今天的 `state.got_data = True` 那一行，`:222`）追加
   `state.last_data = self._clock()`。`pong`（`:202`）、`event: subscribe`、`event: error` 三条分支
   **一律不动它**——这正是与 `last_inbound` 的全部差别所在。
3. `_expired` 追加第四条判据，**排在 `silence_timeout` 之后、`pong_timeout` 之前**（顺序即优先级；
   连接级的硬失活比通道级的软静默更致命，应先被报出来）：

```python
data_silence = self._data_silence_budget()
if data_silence > 0 and now - state.last_data >= data_silence:
    return "data_silence"
```

**阈值不能是一个常数。** `candle1D` 之类的频道在极端行情下确实可以久不推送；但 OKX 的 candle 频道在
**任何**成交/价格变动时都会推未确认根更新，对 SWAP 主流标的实际是亚秒级。故按周期派生并夹取：

```python
def _data_silence_budget(self) -> float:
    """本连接的『数据静默』预算（秒）。0 ⇒ 整条判据关闭。

    `bar` 由 cfg.args[0]['channel'] 反解不可靠（channel 名是 candle1H 这类字符串），故
    WSStreamConfig 新增一个显式字段 `bar_seconds`（由 ws_client._stream_config 填）。
    """
    cfg = self._settings
    if cfg.okx_link_ws_data_silence_factor <= 0:
        return 0.0
    raw = self._cfg.bar_seconds * cfg.okx_link_ws_data_silence_factor
    return min(max(raw, cfg.okx_link_ws_data_silence_min_s), cfg.okx_link_ws_data_silence_max_s)
```

默认 `factor=1.0 / min=120 / max=900`。**【R:P1-7】这个算式对 `bar >= 15m` 恒等于上界 900 秒，
这不是缺陷、也不要试图去「修」它**：candle 频道推送的是**未确认根**，推送密度只与成交密度相关、
与 K 线周期无关，故 900 秒是一条**与周期无关的绝对静默上限**；`bar_seconds` 派生只在 1m～15m 区间
真正起作用（让极短周期不必干等 15 分钟）。换算表（实现者按此写死用例）：

| `bar` | `bar_seconds` | 预算 |
|---|---|---|
| 1m | 60 | **120**（下界） |
| 5m | 300 | 300 |
| 15m | 900 | 900 |
| 1H / 4H / 6H / 12H / 1D | ≥3600 | **900**（上界） |
| `public` 端点 | 0 | **120**（下界；盘口频道推送密度高，与其实际形态一致） |

**取 `factor = 0` 即整条机制关闭**（与本仓「每个机制都有一个关得掉的取值」的纪律一致，见
`config.py:49`）。

**检测延迟与 ping 周期无关**（【R:P2-2】）：`_pump` 每跳的等待上限由 `tick_timeout` 夹在
`[0.05, 1.0]` 秒（`retry.py:111-121`），故 `_expired` 每秒至少被求值一次，新判据的检测延迟 ≤1 秒。

`_REASON_TEXT` 追加 `"data_silence": "订阅频道长时间无数据，已强制重连"`；`_reason_code` 无需改动
（它按第一个冒号前的头部大写，自动得到 `WS_DATA_SILENCE`）。

**为什么这条修复"顺带"解决了 RC-5**：数据静默一旦触发重连，就会走完整的
`degraded → connecting → subscribing → live` 迁移；`SessionLink._on_recovered` 会在 `live` 上被叫醒，
`_pending_repair` 得到重试机会，缺根补齐。**轴 A 不需要为补根写任何一行新代码。**

**`_alive_enough` 的既有语义必须保持**：`data_silence` 结束的连接若存活超过 `healthy_after_s`（20s）
仍会被判「健康过」并复位退避。这是正确的——数据静默不是建连失败，退避到 30 秒会让恢复变慢。

### 3.3 轴 B —— 会话级自愈阶梯

#### 3.3.1 新纯核心 `services/quant/app/ai_auto/feed_watchdog.py`

`ai_auto/session.py` 今天 2923 行，是 CLAUDE.md 800 行红线的 3.6 倍；本轮对它的**净增必须压到最小**。
判定逻辑整体下沉到本文件，范式镜像 `ai_auto/lifecycle.py`（**纯判定 + 判定性文案单点生成，零 IO、
零 import 会话对象**）；**动作与状态另行下沉 `ai_auto/feed_runtime.py`**（【R:P1-10】，见 §3.3.3），
会话侧只留一行装配与一处调用。

```python
# ---------- 阶段码（稳定语义码，进 wire，前端按它选颜色，绝不自己拼中文）----------
STAGE_FRESH     = "fresh"        # 行情正常
STAGE_DEGRADED  = "degraded"     # 刚超过 stale_after，先说话，还没动手
STAGE_BACKFILL  = "backfill"     # 正在用 REST 取最新一根
STAGE_RELINK    = "relink"       # 正在强制换一条 WS
STAGE_ESCALATED = "escalated"    # 换了 N 次仍不新鲜，交回用户

ACTION_NONE = "none"; ACTION_CLEAR = "clear"; ACTION_DEGRADE = "degrade"
ACTION_BACKFILL = "backfill"; ACTION_RELINK = "relink"; ACTION_ESCALATE = "escalate"

@dataclass(frozen=True)
class FeedReadings:
    now_wall: float
    feed_age_s: float          # now - _last_candle_wall（<=0 视为 0）
    stale_after_s: float       # BAR_SECONDS[bar] * live_auto_staleness_factor
    ws_alive: bool             # self._ws is not None and self._ws.is_alive
    link_state: str            # SessionLink.channel_state(owner)；【R:P1-1】必须被 plan() 真正读到
    is_degraded: bool          # 会话当前 _stale_degraded
    last_backfill_wall: float
    last_relink_wall: float
    relink_attempts: int

@dataclass(frozen=True)
class FeedPlan:
    action: str                # ACTION_*
    stage: str                 # STAGE_*
    reason_text: str           # 判定性文案的**唯一**生成点
    next_attempt_in_s: float | None

def plan(r: FeedReadings, cfg: FeedConfig) -> FeedPlan: ...
```

**判定顺序（固定，先致命后次要）**：

| 序 | 条件 | `action` | 说明 |
|---|---|---|---|
| 1 | `stale_after_s <= 0` 或总闸关 | `none` | 整层空转，逐字回落到本轮之前 |
| 2 | **`not ws_alive` 或 `link_state == "down"`** | 冷却到期 ⇒ `relink`；冷却中 ⇒ **不返回，继续向下判定**（把 `next_attempt_in_s` 带上） | **【R:P0-1】【R:P1-1】本行必须排在新鲜度判据之前**，理由见下方「为什么第 2 行不能往下挪」。`link_state == "down"` 是登记簿已经算好的、比 `feed_age` 更硬的证据，不读它就是「有写点没读点」 |
| 3 | `feed_age <= stale_after` | `clear`（仅当 `is_degraded`）否则 `none` | L0：恢复出声，文案逐字沿用今天的「行情已恢复，AI 自动交易盯市恢复正常」 |
| 4 | `not is_degraded` | `degrade` | L1：首次超阈，先说话（文案逐字沿用今天的） |
| 5 | `relink_attempts >= escalate_after > 0` 且（`feed_age > stale_after * relink_factor` **或 `not ws_alive`**） | `escalate` | L4。**`or not ws_alive` 是【R:P0-1】的配套**：一条被 REST 吊着、换了 N 次仍换不活的 WS，`feed_age` 永远越不过门槛，没有这一支就会无限 relink 而永不升级 |
| 6 | `feed_age > stale_after * relink_factor` 且 `now - last_relink >= relink_min_interval` | `relink` | L3 |
| 7 | `now - last_backfill >= backfill_interval` | `backfill` | L2 |
| 8 | 其余 | `none`（带 `next_attempt_in_s`） | 冷却中，wire 里给倒计时 |

**为什么第 2 行不能往下挪（【R:P0-1】，本轮最容易被「顺手整理」掉的一条）**：L2 补取成功会把
`_last_candle_wall` 推到 `now`，于是第 3 行的 `feed_age <= stale_after` **从此永真**。第 2 行若排在它
后面，`ws_alive` 就再也不会被求值——一条死掉的 WS 会被 60 秒一次的 REST 轮询**永久掩盖**：
`classify_member` 因 `feed_age` 被压住而回到 `armed`，横幅消失，stage 回到 `fresh` 导致恢复卡也消失。
**那不是自愈，那是把一个看得见的停摆变成了一个看不见的停摆**，比现状严重。第 2 行前置之后，
「WS 死了」这件事永远有人看见：要么被换活（`clear`），要么走到 `escalate` 并把主动权交回用户
（【R:P1-4】随之解决——恢复卡在整个 REST 吊命期间保持可见）。

**为什么 L2 排在 L3 之后被判定、却在阶梯上编号更小**：`plan` 的顺序是「本 tick 该干哪一件事」，
relink 比 backfill 昂贵得多且有更长的冷却，所以在双方都到期时优先 relink（换链是根治，补取是维持）；
但常态下 backfill 的冷却（60s）远短于 relink（120s + `relink_factor` 门槛），所以实际执行序列是
`degrade → backfill × N → relink → backfill × N → relink → … → escalate`，与阶梯编号一致。

#### 3.3.2 REST 活性补取 `ingest_ai_auto_live()`（`okx/session_link.py`）

这是本轮**最需要小心**的一段，L-1 的全部重量都压在这里。

取数层**直接复用 `gap_repair.fetch_missing(inst_id, bar, since_ts=..., max_bars=...)`**
（`since_ts = session._window.last_confirmed_ts`）：它已带 `probe=True` 熔断豁免、已过滤未收线根、
已按上限截断——本轮**不新增任何取数路径**，与 `gap_repair.py` 的硬边界一致。

```python
async def ingest_ai_auto_live(session, candles: list[Candle], *, bar_seconds: int,
                              now_wall: float) -> dict:
    """ai_auto 的**活性补取**入窗：与 `ingest_ai_auto`（断线补根）刻意分成两个函数。

    两者的差别**只有一条**：这里补进来的最后一根有可能是「刚刚收线的那一根」，即**当下的证据**，
    而断线补根拿到的一律是历史证据。故：

    · **每一根**（含最后一根）都要走 `session.note_live_bar(row)` 逐根步进影子——
      **【R:P1-6】不可只在最后一根算一次信号板**：`evaluate_signal_board` 是有状态的逐根回放
      （`signals.py:225-231` 的 docstring 自己写着「暖机段调用本函数但丢弃返回值，仅为逐根种入
      影子与策略内部状态」），跳根算出来的 `_last_board` 是一份**被悄悄改写过的立场**，而
      `_maybe_interval_wake` 会拿它去起真实资金决策。
    · 除最后一根外的所有根 ⇒ 逐字走 `ingest_ai_auto` 的既有语义（只进窗、刷 ATR / sizing、推 candle，
      **不写四个墙钟字段、不 note_new_bar、不驱动决策**）。
    · 最后一根**若且仅若** `now_wall - (ts + bar_seconds) <= bar_seconds`
      （**【R:P0-3】判据收紧到「一根」，不是 `stale_after_s`（= 三根）**：REST 健康时最新已收线根
      的年龄天然 < 一根，用三根的容差只会把「行情真的停了」误判成「我刚拿到数据」），
      才写 `_last_candle_wall` / `_last_px_wall` / `_last_live_px` / `executor.bar_anchor_ts`
      并 `note_new_bar()`。
    · **写值口径（【R:P0-3】的核心，绝不可简化成 `now_wall`）**：
        `_last_candle_wall = now_wall`     # 语义 = 「我最后一次拿到当下这一根的时刻」（L-1）
        `_last_px_wall     = ts + bar_seconds`   # 语义 = 「这个价**真**的那一刻」
      两者语义不同，取值也必须不同。`_last_px_wall` 是利润保护层新鲜度豁免的唯一真源，而
      `ProfitGuardRule.max_px_age_s = 90.0`（`profit_guard.py:109`）——把它写成 `now_wall` 等于告诉
      保护层「这个价是 0 秒前的」，据此对真实资金执行棘轮与兑现。`ts + bar_seconds` 让 `px_age_s`
      恰好等于真实价龄：价真的新 ⇒ 保护层照常工作；价其实旧 ⇒ 保护层自己判 `unavailable` 并零动作
      （fail-open），**不需要本层替它做任何决定**。
    · 最后一根**不新鲜** ⇒ 一个字段都不写，`_stale_degraded` 保持为真。
      **诚实优先于自愈**：REST 也拿不到当下的根，说明是真的行情中断 / 标的停牌 / 交易所故障，
      此时假装「我刚拿到数据」比什么都不做更危险。

    Returns:
        {"appended": int, "fresh": bool, "lastTs": int|None} —— 会话侧据 `fresh` 决定要不要解锁唤醒。
    """
```

**`note_live_bar` 是会话侧的一个方法，不是 `session_link` 的**（【R:P1-6】）：影子步进要读
`self._specs` / `self._shadows` / `self._window.build_strategy_frames()`，这些是会话的实体。它复用
`feed_candle` 确认根分支的**同一段尾巴**（`session.py:1476-1481`）——步影子 → 写 `_last_board` /
`_last_bar_index` / `_last_live_px`——从而**只有一条步进路径**，而不是第二份：

```python
def note_live_bar(self, row: dict) -> None:
    """活性补取的逐根步进（与 `feed_candle` 确认根分支共用同一段尾巴，绝不另起一套）。

    纯同步、零 IO、**不起决策轮**（起不起由 `_feed_backfill` 在补完之后统一判定）。
    """
```

**决策的驱动方式（【R:P0-2】v1 在这里写错了，本段是重写）**。

原则不变：`fresh == True` 时**绝不**从自愈层直接 `create_task(_run_decision_round(...))`——唤醒预算、
空仓重复唤醒守卫、`_deciding` 槽位认领、待办账本全部挂在既有唤醒路径上，绕开它们就是
**新通道绕过自己声称的约束**（本仓库反复出现的缺陷形状，见 exit-priority 评审的 6 条 P0）。

但 v1 那句「让既有的 `_maybe_catchup_wake()` / 下一个 `_maybe_interval_wake()` 自然接手」是**假的**，
四条通路实读全部走不通：

| 通路 | 为什么走不通 | 取证 |
|---|---|---|
| `_maybe_catchup_wake()` | 全仓**唯一调用点**是 `_run_decision_round` 的 `finally`——自愈路径根本到不了 | `session.py:1631` |
| 即便直调 `_maybe_catchup_wake()` | `plan_catchup` 第一道判据是 `if not self.backlog_enabled or not self._backlog.pending: return None`，而 REST 补取不产生任何待办 | `wake_runtime.py:278` |
| `_maybe_interval_wake()` | 第一道判据是 `self.wake_interval_s <= 0`；`trend_hold` 档常配 0 ⇒ 恒不唤醒 | `session.py:1320` |
| `_maybe_signal_flip_wake()` | 要求 `_live_row is not None`，而补取从不写 `_live_row`（那是未确认根的东西） | `session.py:1686` |

**净后果**：照 v1 实现，`wake_interval_s = 0` 的会话在自愈之后会**抹掉横幅、且产出零个决策轮**——
比今天诚实的停摆更糟。

**正确做法**：`_feed_backfill` 在 `fresh == True` 时按顺序做三件事，一件都不能少：

1. `await self._clear_stale_if_recovered()` —— 复用既有方法与既有文案（判定性文案仍是单点）；
2. `self._wake.note_bar_drop(time.time())` —— **登记一条待办**。语义完全贴切：确实有一根确认根落地
   了、确实没有为它起决策轮，这正是 `note_bar_drop` 的定义（`feed_candle` 撞上在飞轮时走的就是它）；
3. `self._maybe_catchup_wake()` —— **纯同步直调**。它不是旁路：`state != "running"` / `_deciding` /
   `_stale_degraded` / `_last_board is None` 四道守卫连同 `plan_catchup` 的待办上限、`should_skip`
   全部写在这个函数**内部**（`session.py:1633-1660`），直调它 = 走完全部既有守卫，而不是绕过它们。

这三步之后，唤醒是否真的发生仍由既有唤醒器说了算（空仓 + 输入没真变 ⇒ `should_skip` ⇒ 不唤醒且
不留悬空待办），**自愈层不对「该不该交易」有任何发言权**——与 L-2 同极性。

**去重护栏**：`_window.is_duplicate` 是既有护栏，WS 与 REST 同时送同一根时后到的那条被丢弃，行的形状
始终只有 `append_confirmed` 一个真源。

#### 3.3.3 会话侧的改动（`ai_auto/session.py`）

`_check_staleness` 整体重写为**薄执行器**（≤50 行，守 CLAUDE.md 阈值），判定全部来自 `feed_watchdog.plan`：

```python
async def _check_staleness(self, now_ts: int) -> None:
    """行情停滞看门狗 → **自愈阶梯**（feed-self-healing 轴 B）。判据仍是 `is_active`。

    ⚠️ `live_auto_flatten_on_stale` 那条既有分支**逐字保留在最前面**（L-2：本轮不新增任何自动平仓
    路径，也不删除既有的）。
    """
    if not self.is_active or self._last_candle_wall <= 0:
        return
    settings = self.executor.settings
    stale_after = BAR_SECONDS.get(self.bar, 60) * settings.live_auto_staleness_factor
    elapsed = now_ts - self._last_candle_wall
    if settings.live_auto_flatten_on_stale and elapsed > stale_after * 4:
        await self.stop("stale_feed")
        return
    plan = feed_watchdog.plan(self._feed_readings(elapsed, stale_after), self._feed_config())
    await self._apply_feed_plan(plan)
```

**【R:P1-10】动作层整体住在新文件 `ai_auto/feed_runtime.py`，不是 `session.py`。** 这不是「超出
再拆」的逃生门，而是本轮的**硬约束**：按上面的清单实算，会话侧净增约 **190~220 行**（`_apply_feed_plan`
+ 五个动作方法 ≤25×5 + `_feed_readings` / `_feed_config` / `_feed_recovery_key` / `relink_feed` /
`note_live_bar` + 7 个字段 + `_rearm_feed(force=)` + `_latest_mark_px` 重写），会把 2923 行推到 ~3130。
范式**逐字镜像既有的 `horizon_runtime.py` / `discipline_runtime.py` / `cost_runtime.py`**：运行时对象
持有自愈状态与动作实现，会话只持有一个 `self._feed = FeedRuntime(...)` 并在 `_check_staleness` 里
调它一次。

`FeedRuntime` 按 `action` 分派到五个动作方法（每个 ≤25 行）：`_feed_clear` / `_feed_degrade`
/ `_feed_backfill` / `_feed_relink` / `_feed_escalate`。**每一个动作都必须自带 `try/except Exception`
并只降级不外抛**——它跑在 `_monitor_tick` 里，而那条循环守着真实资金（`ai-trade-interval-wake` 那一轮
的全部教训）。

会话侧因此只剩四处净增：`self._feed = FeedRuntime(...)`（`__init__` 一行）、`_check_staleness`
的薄执行器（上面那段）、`note_live_bar`（【R:P1-6】，必须在会话侧）、`relink_feed()` 的一行转发。
**会话侧净增 ≤ 40 行**，这是可验收的数字（§7.7 加一条源码扫描门禁）。

`_rearm_feed` 签名扩展为 `async def _rearm_feed(self, *, force: bool = False)`：
`force=False` 时行为**逐字不变**（`is_alive` 为真即零动作，`resume()` 的调用点一个字都不用改）；
`force=True` 时跳过 `is_alive` 短路，无条件 `await old.stop()` → 新建 → `start()`。
`link is None` 的既有早退分支保留（那条注释解释得很清楚：会造出一条没人处理链路事件的野连接）。

新增会话字段（全部在 `__init__` 中初始化，紧邻 `_stale_degraded`）：

```python
self._feed_last_backfill_wall = 0.0
self._feed_last_relink_wall = 0.0
self._feed_relink_attempts = 0
self._feed_stage = feed_watchdog.STAGE_FRESH
self._feed_last_outcome: str | None = None       # "ok" | "no_data" | "failed"
self._feed_last_outcome_text: str | None = None
self._feed_last_source: str | None = None        # "ws" | "rest"
```

`feed_candle` 的确认根分支追加一行 `self._feed_last_source = "ws"`（与 `_last_candle_wall` 同址，
**同进同出**）；`ingest_ai_auto_live` 的 fresh 分支写 `"rest"`。

`relink_attempts` 的复位点**只有一个**：**`feed_candle` 的确认根分支**（与 `_feed_last_source = "ws"`
同址、同进同出）。

> **【R:P1-11】为什么不是 `_feed_clear`**（v1 的选择）：L2 补取成功时是 `_feed_backfill` 自己调
> `_clear_stale_if_recovered()` 清掉 `_stale_degraded`，此后 `plan()` 的第 3 行只会返回 `none`，
> `_feed_clear` **再也不会被走到** ⇒ 计数永不复位 ⇒ 一个早已痊愈的会话会带着陈年计数走到 `escalate`。
> 改用 `feed_candle` 确认根分支的另一重好处是它是**证据驱动**的：只有「WS 真的又送来了一根确认根」
> 才复位，而「换一次链、活 30 秒、又死」的抖动形态（从不送出确认根）**仍然**能累积到 escalate ——
> v1 想守住的那条性质完整保留。

#### 3.3.4 手动兜底 `relink_feed()`

```python
async def relink_feed(self) -> dict:
    """人工授权的**单次**强制重连 + 活性补取（feed-self-healing §4.2）。

    语义：先 `_rearm_feed(force=True)` 换一条新 WS，再立刻走一次 `_feed_backfill`，
    **不停止会话、不平仓、不改分组生命周期**（与 `/exit`、`/backstop/repair` 的姿态同源）。
    冷却对人工路径**不生效**（用户点了就是要现在试），但计数照常累加。

    **【R:P1-5】整个调用被一条硬死线包住**：
    `await asyncio.wait_for(self._relink_once(), settings.ai_trade_feed_relink_deadline_s)`
    （默认 12.0 秒）。超时 ⇒ **不外抛**，返回与「重连了但没拿到更新的根」逐字相同的失败回执。

    理由：前端 `request()` 对本路径落 `FAST_MS = 20_000`（`apps/web/lib/httpTimeout.ts`，
    `/relink` 不在长跑白名单里），而本方法最该被点击的场景恰恰是 **A4「网络仍不通」**——此时
    `fetch_missing` 会走完整的 REST 重试 / 退避，很容易越过 20 秒。越过之后用户在**唯一一个兜底
    按钮**上读到的是「本次操作结果未知」，正是 `httpTimeout.ts` 那段 docstring 写下来要消灭的失信
    形态。把死线放在服务端而不是去改前端超时档，是因为「12 秒内一定给你一个诚实的回答」比
    「让用户多等一分钟再告诉他失败」更接近本轮的立论。
    """
```

### 3.4 轴 C —— 断流期间的判定价回落（`_latest_mark_px`）

```python
async def _latest_mark_px(self) -> float:
    """盯市判定价。窗口非空**且判定价未超龄**时用最后收盘价；超龄则回落 REST ticker。

    RC-6：窗口非空时直接返回 `candles[-1]["c"]` 会让断流期间的 `executor.reconcile` 与
    `_maybe_session_exit` 全程吃一个 21 小时前的价。`ai_trade_mark_px_fallback_after_s = 0`
    ⇒ 逐字回退到本轮之前的行为。
    """
    fallback_after = float(self.executor.settings.ai_trade_mark_px_fallback_after_s)
    fresh = (fallback_after <= 0 or self._last_px_wall <= 0
             or (time.time() - self._last_px_wall) <= fallback_after)
    c = self._window.candles
    if c and fresh:
        return float(c[-1]["c"])
    try:
        px = await get_rest_client().get_ticker(self.inst_id)
    except Exception:  # noqa: BLE001 —— 取价失败回落既有口径，不阻塞盯市
        px = None
    if px is not None:
        return float(px)
    return float(c[-1]["c"]) if c else self.executor.entry_price
```

**注意判据用 `_last_px_wall` 而不是 `_last_candle_wall`**：后者在 12H/1D 执行周期下几十小时不跳，
用它做新鲜度判据会让这条回落在正常运行时也恒生效，每 5 秒打一次 REST（`market_window.py:215-216` 已经
为同一个坑写过一次注释；这是本仓库第三次遇到它）。默认 `180.0` 秒。

**这条回落路径【绝不写】 `_last_px_wall` / `_last_live_px` / `_last_candle_wall`**（评审补充的硬约束，
与【R:P0-3】同一条理由的另一面）。两个后果缺一不可：

1. **正确性**：写了 `_last_px_wall` 会让下一个 tick 的 `fresh` 判定转真 ⇒ 回落停止 ⇒ 又用回 21 小时前
   的收盘价 ⇒ 180 秒后再次超龄 ⇒ **每 180 秒在「真实价」与「化石价」之间来回摆一次**，比不回落更坏。
2. **安全**：`_last_px_wall` 是利润保护层新鲜度豁免的唯一真源。盯市判定价与保护层判定价是**两个量**，
   本轮只修前者；让一个 ticker 取数顺手解锁保护层，等于在一条风控读数上开 fail-open。

即：`_latest_mark_px()` 是**纯读**——它取一个更好的价交给 `reconcile` / `_maybe_session_exit`，
但不改变系统对「我们的判定价有多新」这件事的记账。

### 3.5 轴 D —— 呈现与人机交互

#### 3.5.1 新 wire：成员级 `feedRecovery`

挂在既有 `member_lifecycle_wire()` 的**同一层**（`ai_auto/lifecycle.py`），但**装配在会话侧**
（`lifecycle.py` 是零 IO 纯核心，不认识 `_feed_*` 字段）：`AiTradeSession.lifecycle_wire()` 在既有
wire 之上做一次 `{**wire, **self._feed_recovery_key()}`。

**【R:P1-3】键的存在性纪律，逐字服从它所在的那个 wire，而不是别的 wire**：

| 情形 | `feedRecovery` |
|---|---|
| 总闸关（`ai_trade_feed_selfheal_enabled = False`）或 `ai_trade_lifecycle_truth_enabled = False` | **整键缺席** |
| 已装配、行情正常（stage = `fresh`） | **`null`**（键在，值为空） |
| 已装配、正在自愈 / 已升级 | 对象 |

v1 写的是「行情正常 ⇒ 整键缺席」并引用 `exitCommitment` 的纪律，但 `feedRecovery` 挂在
`member_lifecycle_wire` 的返回对象里，而那个函数的 docstring 明写：「**全部键恒下发，绝不因为『本轮
没数据』而整键缺席——前端要能区分『没数据』与『后端没上线这个键』**」（`lifecycle.py:141-143`）。
v1 引用的正是这同一句话，却用它论证了相反的做法。`exitCommitment` 的键缺席纪律属于另一个对象
（`session.horizon`），跨对象套用会让同一个 wire 里两条相反的纪律并存——那是下一次漂移的种子。

**总闸关仍是整键缺席**：那一档回答的是「后端有没有上线这个特性」，与「本轮有没有数据」是两件事，
正好对应上表的两行。前端 `describeFeedRecovery(undefined | null) ⇒ null ⇒ 不渲染`，两档表现一致，
但**可被反证**——这正是那句 docstring 想要的。

#### 3.5.2 `NotArmedBanner` 升级

今天它是一个纯展示组件（`AiTradePanel.tsx:301-321`）。本轮**不把动作塞进它**（`AiTradePanel.tsx`
今天 797 行、距 800 红线只剩 3 行，且 `NotArmedBanner` 的 `<span>` 结构不适合再嵌按钮），而是新建独立
组件文件 `apps/web/components/Chart/AiTradeFeedRecoveryCard.tsx`，挂在 banner **下方**、
`TradabilityStrip` 上方。

**【R:P1-9】入参契约必须是 `{ groupId, members }`，组件内部自己 `.map()`。** `feedRecovery` 是
**成员级**的（一组可以有 5 个），而这个挂载位是**分组级**上下文（`NotArmedBanner` 在 `:153`、
`TradabilityStrip` 在 `:159`，中间没有成员循环）——挂一个只吃单成员的卡在这里无法表达 5 个标的；
而在面板里补一段 `.map()` 会直接顶穿 800 行红线。故：

```tsx
<AiTradeFeedRecoveryCard groupId={group.groupId} members={members} />
```

`AiTradePanel.tsx` 的净增因此**确实**是「一行 `import` + 一行挂载」（797 → 799），与 `AiTradeExitCard`
的既有做法逐字同源。组件内部对 `members` 逐个取 `m.lifecycle?.feedRecovery`，`null` / `undefined`
的成员直接跳过；全部跳过 ⇒ 整个卡不渲染（连外框都不渲染，不占位）。

三态视觉语言与 `AiTradeExitCard` 同源（不发明第二套）：

| `state` | 视觉 | 文案（示例） | 动作 |
|---|---|---|---|
| `healthy` | 不渲染 | — | — |
| `recovering` | `accent` 边框 + 细进度条 | 「正在自动恢复行情连接 · 已重连 2 次 · 下一次尝试 38 秒后 · 上次结果：REST 未取到更新的 K 线」 | 【立即重连】（次要按钮） |
| `recovering` 且 `source === "rest"` | 同上 | 同上，**并追加一句**「当前 K 线由 REST 补取维持，实时通道尚未恢复」（【R:P1-4】） | 同上 |
| `stuck` | `danger`（`var(--short)`） | 「自动恢复已尝试 3 次仍未拿到新 K 线（21 小时无数据）。交易所侧止盈止损仍生效；该标的的 Agent 决策已停摆。」 | 【立即重连】（主按钮）+ 【仅停止该标的】（次要，指向既有 `/member/{inst}/stop`） |

**设计纪律（顶级产品的判据，逐条可验收）**：

1. **绝不出现第二处数数**：`已重连 N 次` / `下一次 M 秒后` / `21 小时无数据` 全部取自 wire
   （`attempts.relink` / `nextAttemptInS` / `feedAgeS`），前端一个数都不自己算。倒计时的锚点用
   `lifecycle.asOfTs` + 本地单调时钟推进（既有 `serverTs` 双时钟纪律）。
   **多标的时每行必须点名标的**（复用 `NotArmedBanner` 的既有规则）：整组抹掉具体是哪一个标的在
   自愈，与「2 个标的死 1 个时只说未武装」是同一种失信（`lifecycle.py:167-171` 已论证过一次）。
2. **绝不对着一个不会到来的时刻读秒**：`nextAttemptInS` 为 `null`（冷却外 / 已 escalate）时整行不渲染
   倒计时，与 `aiTrade.test.ts:1246` 冻结的那条既有规则同极性。
3. **文案里绝不写 markdown 星号**：后端下发的串直接进状态行，`**粗体**` 会显示成字面星号（本仓库已复发
   两次，见 `session_link.py:_repair` 的注释）。
4. **单标的时不显示分数**：`membersTotal === 1` 时不出现 `1/1`（既有 `aiTrade.ts:1210` 的规则复用）。
5. **按钮 pending 态必须可见**：点击后立即禁用 + 文案改「正在重连…」，回执到达前不得允许第二次点击
   （镜像 `AiTradeExitCard` 的 `pending` 状态机）。
6. **`role="alert"` 只留在 banner 上**：恢复卡是 `role="status"`（`aria-live="polite"`）——同一屏两个
   `alert` 会让屏幕阅读器把用户按在原地读两遍。

#### 3.5.3 顶栏链路芯片

**不改** `classify_member` 的 `feed_down` 极性（把 `degraded` 也算成不可达会让每次重连的头几秒挂一条
假红条，`lifecycle.py:44-48` 已经论证过）。

**【R:P1-2】但必须如实说清楚芯片会变成什么颜色——v1 在这里说反了。** 实读 `link_state.py`：

- `note_ws(event="connected")` 会把 `consecutive_failures` **清零**并把 `last_message_ts` 推到 `now`
  （`:133-138`），而 `down` 的判据是 `consecutive_failures >= down_failures(=2)`（`:84`）。
- 数据静默重连的每一轮都夹着一次 `connected`，故计数**恒不超过 1**。

⇒ **「socket 活着、频道静默」这一形态的通道，芯片停在 `degraded`（"不稳"）而不是 `down`（"不可达"）**，
`classify_member` 也仍然判 `feed_stale`（而不是 `feed_down`）。这**不是缺陷**：横幅与恢复卡都由
`feed_stale` 驱动，用户该看到的信息一条不少；「不稳」也确实比「不可达」更准确地描述了一条握手正常、
只是不推数据的连接。

真正会把芯片推红的是**另一种**形态：连都连不上（`_open` 之前抛异常 ⇒ 没有 `connected` ⇒ 两次失败
即 `down`）。§7.6 的 A3 按这一条验收（它断的是网，不是频道），A1 / A2 则**不应**期待红条。

---

## 四、接口设计

### 4.1 WS / HTTP 载荷（加法键，总闸关 ⇒ 整键缺席）

`GET /ai-trade/status` 的 `group.members[].lifecycle`、WS `status` 事件、WS `lifecycle` 事件三处**共用
同一次装配**（既有纪律），新增：

```jsonc
"lifecycle": {
  "state": "running", "armed": false, "reason": "feed_stale",
  "reasonText": "行情已 20 小时 59 分钟没有新 K 线，Agent 暂不响应；交易所侧止盈止损仍生效。",
  "feedAgeS": 75540.0, "staleAfterS": 10800.0, "linkState": "degraded",
  // ↓ 本轮新增。【R:P1-3】总闸关 ⇒ 整键缺席；已装配且行情正常 ⇒ `"feedRecovery": null`（键在、值空）
  "feedRecovery": {
    "state": "recovering",              // "recovering" | "stuck"
    "stage": "backfill",                // "degraded" | "backfill" | "relink" | "escalated"
    "attempts": { "backfill": 17, "relink": 2 },
    "lastAttemptTs": 1756270000,
    "nextAttemptInS": 38.0,             // null = 无待办倒计时（冷却外 / 已 escalate）
    "lastOutcome": "no_data",           // "ok" | "no_data" | "failed" | null
    "lastOutcomeText": "REST 未取到更新的 K 线",
    "source": "ws",                     // 最后一根确认根的来源；null = 尚无
    "recoveryText": "正在自动恢复行情连接 · 已重连 2 次 · 下一次尝试 38 秒后"
  }
}
```

`recoveryText` 由 `feed_watchdog.describe(...)` **单点生成**（判定性文案的唯一生成点，镜像
`lifecycle.classify_member`）——前端绝不自己拼中文。

### 4.2 新 REST 端点

```
POST /ai-trade/{group_id}/member/{inst_id}/relink
```

| 项 | 值 |
|---|---|
| 语义 | 强制换一条 K 线 WS + 立刻走一次 REST 活性补取。**不停会话、不平仓、不改分组生命周期。** |
| 守门 | **不守 AI 总闸**（`ai_trade_enabled`）——恢复观测是收敛风险的动作，任何时候都该能执行（与 `/member/{inst}/exit`、`/backstop/repair` 同理）。守运维总闸 `ai_trade_feed_selfheal_enabled`：关 ⇒ 409 `AI_TRADE_FEED_SELFHEAL_DISABLED`。 |
| 404 | 分组 / 成员不存在（镜像 `stop_member`） |
| 返回 | 裸 `dict`，**不绑 `response_model`**（本仓库已为「`response_model` 静默吞新 wire 键」栽过一次） |
| 死线 | **【R:P1-5】服务端 `asyncio.wait_for(..., ai_trade_feed_relink_deadline_s = 12.0)`**：前端对本路径落 `FAST_MS = 20_000`（`httpTimeout.ts` 的长跑白名单不含它），而本端点最该被点击的场景就是「网络不通」。12 秒内必须给出一个诚实的回执，绝不让用户在唯一的兜底按钮上读到「本次操作结果未知」。超时 ⇒ 走下面那条失败回执，**不映射 504** |
| 异常 | `ai_trade_feed_selfheal_enabled = False` ⇒ 会话侧抛 `AiTradeFeedSelfHealDisabled`（新异常类，逐字镜像 `session.py:147` 的 `AiTradeManualExitDisabled`），端点层译成 409。**异常类住在 `ai_auto/feed_runtime.py`**，与判定同址 |

成功回执：

```json
{ "ok": true, "instId": "ETH-USDT-SWAP", "relinked": true,
  "appended": 3, "fresh": true, "lastTs": 1756269900,
  "message": "已重建行情连接并补齐 3 根 K 线，Agent 已恢复响应" }
```

失败回执（**200 + `ok:false`**，不映射 502——与 `/exit` 同理：会话侧不抛 `OKXError`，写一个
`except OKXPrivateError` 只会得到一段不可达的死代码）：

```json
{ "ok": false, "instId": "ETH-USDT-SWAP", "relinked": true,
  "appended": 0, "fresh": false, "lastTs": null,
  "message": "已重建行情连接，但交易所仍未返回更新的 K 线；该标的可能已停牌或交易所行情中断" }
```

同时推一条 WS `error` 帧 `AI_TRADE_FEED_RELINK_FAILED`（面板状态行的既有落点）。

### 4.3 前端 API 客户端

`apps/web/lib/aiTradeApi.ts` 追加（与 `exitMember` 逐字同形）：

```ts
relinkMember: (groupId: string, instId: string) =>
  request<AiTradeRelinkResult>(`/ai-trade/${groupId}/member/${instId}/relink`, { method: "POST" }),
```

---

## 五、数据模型

### 5.1 进程内形状（无 DB 迁移）

**本轮零 DB 迁移。** 自愈状态全部是易失的会话内存（与 `link_state` 登记簿的既有立场一致：链路健康是
「此刻的事实」，重启后从零重新观测才是诚实的）。落库的只有既有的 `lastSeenTs` 心跳，一个字段都不加。

| 载体 | 字段 | 生命周期 |
|---|---|---|
| `AiTradeSession` | `_feed_last_backfill_wall` / `_feed_last_relink_wall` / `_feed_relink_attempts` / `_feed_stage` / `_feed_last_outcome` / `_feed_last_outcome_text` / `_feed_last_source` | 会话内存；`_feed_clear` 复位计数 |
| `_PumpState`（ws_supervisor） | `last_data: float` | 单次连接生命周期 |
| `WSStreamConfig` | `bar_seconds: int = 0`（加法字段，带默认值 ⇒ 既有构造点零改动） | 静态 |

### 5.2 新增配置项（`services/quant/app/config.py`）

```python
# ---- WS 通道级数据静默（ai-trade-feed-self-healing 轴 A）----
# 与 okx_link_ws_silence_timeout_s 的差别是**唯一**的：那条读 last_inbound（含 pong），
# 这条读 last_data（仅业务数据帧）。socket 活着而频道静默是最常见、也最难查的一类断线。
okx_link_ws_data_silence_factor: float = 1.0    # × bar 秒数；0 = 整条判据关闭
okx_link_ws_data_silence_min_s: float = 120.0   # 夹取下界（盘口频道与 1m 周期用这个）
okx_link_ws_data_silence_max_s: float = 900.0   # 夹取上界（6H/12H/1D 不至于等一整根）

# ---- AI 自动交易行情自愈（ai-trade-feed-self-healing 轴 B / 轴 C）----
ai_trade_feed_selfheal_enabled: bool = True                                   # 整轴总闸
ai_trade_feed_backfill_interval_s: float = Field(default=60.0, gt=0)          # REST 活性补取冷却
ai_trade_feed_relink_factor: float = Field(default=2.0, ge=1.0)               # × stale_after 才换链
ai_trade_feed_relink_min_interval_s: float = Field(default=120.0, gt=0)       # 换链冷却
ai_trade_feed_escalate_after_relinks: int = Field(default=3, ge=0)            # 0 = 永不升级
ai_trade_feed_relink_deadline_s: float = Field(default=12.0, gt=0)            # 人工 /relink 硬死线（R:P1-5）
ai_trade_mark_px_fallback_after_s: float = Field(default=180.0, ge=0)         # 0 = 关（旧行为）
```

**`relink_factor` 的换算结果请写进注释**（【R:P2-4】）：`stale_after = bar × live_auto_staleness_factor(3)`，
故会话级换链门槛 = `bar × 6` —— 1H 周期下是 **6 小时**。这不是笔误：修正后的判定表第 2 行
（`not ws_alive` / `link_state == "down"`）才是常态下的换链触发点，`bar × 6` 只是最后一层兜底，
用于「WS 任务活着、登记簿也没判 down、但根就是不来」的残余形态。

**桌面打包透传无需改动**：`desktop/src/lib/config.js:80-82` 的白名单已含 `OKX_` 与 `AI_TRADE_` 两个
前缀。（此处显式写出来，是因为本仓库已为这类漏改栽过两次；实现者请**确认**而不是**再加一遍**。）

**总闸关（`ai_trade_feed_selfheal_enabled = False`）的回落必须是逐字的**：
`plan()` 恒返回 `clear`/`degrade`/`none` 三态（即今天的行为）、`feedRecovery` 整键缺席、
`/relink` 端点 409、前端因键缺席而不渲染恢复卡。**四处回落缺一，这个开关就只是一句声明。**

---

## 六、文件 / 模块变更清单

| # | 文件 | 新建/修改 | 一句话意图 |
|---|---|---|---|
| 1 | `services/quant/app/okx/ws_supervisor.py` | 修改 | `_PumpState` 加 `last_data`；`_consume` 数据帧分支刷新它；`_expired` 加 `data_silence` 判据；`_data_silence_budget()`；`_REASON_TEXT` 加一条 |
| 2 | `services/quant/app/okx/ws_client.py` | 修改 | `_stream_config` 填 `bar_seconds`（business 取 `BAR_SECONDS[bar]`，public 填 0） |
| 3 | `services/quant/app/okx/session_link.py` | 修改 | 新增 `ingest_ai_auto_live()`（活性补取，L-1 的语义边界全部落在这里）。**【R:P2-5】v1 清单里的 `force_repair()` 已删除**：全文再无第二处提到它，§3.3.4 的人工路径也不需要它——死条目写进「唯一实施依据」比漏条目更危险 |
| 4 | `services/quant/app/ai_auto/feed_watchdog.py` | **新建** | 自愈阶梯的纯核心：`FeedReadings` / `FeedPlan` / `plan()` / `describe()`；零 IO、零 import 会话对象 |
| 4b | `services/quant/app/ai_auto/feed_runtime.py` | **新建** | **【R:P1-10】前置必做**（不是「超出再拆」）：`FeedRuntime` 持有 7 个自愈状态字段 + 五个动作方法 + `_feed_readings` / `_feed_config` / `_feed_recovery_key` + `AiTradeFeedSelfHealDisabled`。范式逐字镜像 `horizon_runtime.py` / `discipline_runtime.py` / `cost_runtime.py` |
| 5 | `services/quant/app/ai_auto/session.py` | 修改 | **净增 ≤ 40 行**：`self._feed = FeedRuntime(...)` 一行；`_check_staleness` 改薄执行器；`note_live_bar()`（【R:P1-6】影子逐根步进，与 `feed_candle` 确认根分支共用同一段尾巴）；`_rearm_feed(force=)`；`_latest_mark_px` 回落；`relink_feed()` 一行转发；`feed_candle` 确认根分支追加 `_feed_last_source = "ws"` + 复位 `relink_attempts`（【R:P1-11】） |
| 6 | `services/quant/app/ai_auto/manager.py` | 修改 | `relink_member(group_id, inst_id)`（逐字镜像 `exit_member` 的定位与转发姿态） |
| 7 | `services/quant/app/api/ai_auto.py` | 修改 | `POST /ai-trade/{group_id}/member/{inst_id}/relink` 端点 + 错误映射 |
| 8 | `services/quant/app/config.py` | 修改 | 3 + 6 = 9 个新配置项（含总闸与全部「关得掉的取值」） |
| 9 | `apps/web/lib/aiTradeTypes.ts` | 修改 | `AiTradeFeedRecovery` 接口 + `AiTradeMemberLifecycle.feedRecovery?`（可选，键缺席即 `undefined`） |
| 10 | `apps/web/lib/aiTradeFeed.ts` | **新建** | `describeFeedRecovery(wire)` 纯函数：三态 → 视图模型（颜色 / 文案 / 是否显示倒计时 / 按钮集合）；单标的不显示分数等既有规则在此复用 |
| 11 | `apps/web/lib/aiTradeApi.ts` | 修改 | `relinkMember(groupId, instId)` |
| 12 | `apps/web/components/Chart/AiTradeFeedRecoveryCard.tsx` | **新建** | 恢复卡组件（三态视觉 + pending 状态机 + 两个按钮） |
| 13 | `apps/web/components/Chart/AiTradePanel.tsx` | 修改 | 一行 `import` + 一处挂载（净增 2 行，守 800 行红线） |
| 14 | `services/quant/tests/test_okx_ws_supervisor.py` | 修改 | 追加 4 条数据静默用例（见 §7.1） |
| 15 | `services/quant/tests/test_ai_trade_feed_watchdog.py` | **新建** | 纯核心离线用例（阶梯 8 条判定顺序 × 冷却 × 总闸） |
| 16 | `services/quant/tests/test_ai_trade_feed_selfheal.py` | **新建** | 会话级集成用例（含 §7.3 的自锁复现用例） |
| 17 | `services/quant/tests/test_okx_session_link.py` | 修改 | 追加 `ingest_ai_auto_live` 的「新鲜 / 不新鲜」两条对拍用例 |
| 18 | `services/quant/tests/test_ai_trade_api.py` | 修改 | `/relink` 的 200 / 404 / 409 三条 |
| 19 | `apps/web/lib/aiTradeFeed.test.ts` | **新建** | 视图模型纯函数用例（含「不对不会到来的时刻读秒」） |
| 20 | `apps/web/components/Chart/AiTradeFeedRecoveryCard.test.tsx` | **新建** | 组件用例（三态渲染 + pending 禁用 + 键缺席/`null` 均不渲染 + 多标的逐行点名） |
| 20b | `services/quant/tests/test_ai_trade_feed_runtime.py` | **新建** | 动作层用例（五个动作各自吞异常、`/relink` 死线、`relink_attempts` 复位点） |
| 21 | `docs/plans/ai-trade-feed-self-healing/spec.md` | **本文件** | 设计文档 |

**`live_auto` / `paper` 两处 session 本轮不改一行**：它们通过轴 A 的共享 supervisor **自动获得**通道级
静默判据与随之而来的补根，这正是把实体放在 `okx/` 下的既有理由。它们不需要轴 B（`live_auto` 的确认根
直接下真单，给它一条 REST 驱动的决策路径是另一个量级的风险决策，不在本轮范围）。

---

## 七、测试与验收标准

### 7.1 轴 A · `test_okx_ws_supervisor.py`（新增 4 条）

| 用例 | 断言 |
|---|---|
| `test_pong_alone_does_not_keep_a_data_silent_channel_alive` | 替身连接只回 `"pong"`、不推任何数据帧；推进假时钟至 `data_silence` 预算 ⇒ `_pump` 返回 `"data_silence"`，且**在 `silence_timeout` 之前**没有返回过任何原因（证明今天这条路径确实活不下去） |
| `test_data_frame_refreshes_data_clock_but_pong_does_not` | 交替送 `pong` 与数据帧 ⇒ 连接不被判失活；只送 `pong` ⇒ 被判失活 |
| `test_data_silence_budget_is_clamped_by_bar_seconds` | **【R:P1-7】按算式实算，不是按直觉**：`bar_seconds=0 ⇒ 120`（public 走下界）、`60 ⇒ 120`（下界）、`300 ⇒ 300`、`900 ⇒ 900`、`3600 ⇒ 900`（上界，**不是 3600**）、`86400 ⇒ 900`（上界）、`factor=0 ⇒ 0`（关闭） |
| `test_data_silence_reconnect_emits_a_full_down_to_live_cycle` | 数据静默重连后 `on_stream` 依次收到 `degraded → connecting → subscribing → live`，且 `live` 事件的 `downtime_s > 0`（证明 RC-5 被顺带修复） |

### 7.2 轴 B · `test_ai_trade_feed_watchdog.py`（纯核心，离线）

覆盖 §3.3.1 判定表的 8 行 × 三组边界（冷却内/外、总闸开/关、`stale_after=0`）；额外钉死：

- `not ws_alive` ⇒ 立刻 `relink`，**不等** `stale_after`（RC-4 的正面用例）。
- **`feed_age = 0`（行情看起来完全新鲜）+ `not ws_alive` ⇒ 仍然返回 `relink`**
  （**【R:P0-1】的冻结用例**：这条一红，就说明有人把判定表第 2 行挪回了新鲜度判据之后，
  REST 长生保活的形态会立刻复活）。
- `link_state == "down"` 且 `ws_alive` 为真 ⇒ 仍然返回 `relink`（**【R:P1-1】**：`link_state`
  必须被读到；这条一红说明它又变成了一个只写不读的字段）。
- `not ws_alive` 但换链冷却未到 ⇒ **不返回 `relink` 也不早退**，继续按第 3 行以后判定，
  且 `next_attempt_in_s` 非空（冷却中的行为必须是「继续往下看」而不是「什么都不干」）。
- `relink_attempts >= escalate_after` 且 `not ws_alive`（`feed_age` 仍然很小）⇒ `escalate`
  （**【R:P0-1】配套**：被 REST 吊着的死链必须能升级，否则会无限 relink）。
- `escalate_after = 0` ⇒ 永不返回 `escalate`（「关得掉的取值」）。

### 7.3 轴 B · `test_ai_trade_feed_selfheal.py`（**必须先写、必须能稳定复现原 bug**）

按 CLAUDE.md §七「修 bug 必须先写一个能稳定复现该 bug 的测试用例」：

```
test_a_dead_feed_locks_the_session_forever_without_selfheal   # 复现用例（总闸关时必须仍然复现）
  1. 起一个 1H 会话，喂 1 根确认根（_last_candle_wall = t0）
  2. 假时钟推进 21 小时，WS 替身一帧不送
  3. 跑 N 个 monitor tick
  4. 断言（总闸关）：_stale_degraded 为真、rounds_completed 未增、_last_candle_wall 仍为 t0
  5. 断言（总闸开）：REST 替身返回「刚刚收线的那一根」⇒ _stale_degraded 转假、
     _last_candle_wall 前移到 now、下一个 tick 触发了一轮决策
```

**【R:P0-2】上面第 5 步必须跑两遍，两遍的断言不同——这是本轮最容易实现成「绿着但没修好」的一处**：

```
  5a. wake_interval_s > 0  ⇒ 断言：确实起了一轮决策（rounds_completed 前移）
  5b. wake_interval_s == 0 ⇒ 断言：**同样**起了一轮决策
      （靠 note_bar_drop + 直调 _maybe_catchup_wake 的补跑链，而不是靠墙钟定时唤醒）
```

只写 5a 的话，实现者照 v1 那句「让既有唤醒自然接手」写出来的代码**会通过 5a**（墙钟定时唤醒恰好
盖住了它），而 `trend_hold` 档（`wake_interval_s = 0`）的现场会是「横幅消失了、但一轮决策都没有」——
比修复前更糟，且**没有任何用例会红**。

其余用例：

| 用例 | 断言 |
|---|---|
| `rest_backfill_with_a_stale_last_bar_writes_nothing` | REST 只返回 20 小时前的根 ⇒ 四个墙钟字段一个字节不变、`_stale_degraded` 仍为真、`lastOutcome == "no_data"`（**L-1 的核心护栏**） |
| `rest_backfill_freshness_window_is_one_bar_not_three` | **【R:P0-3】** 1H 会话，REST 返回一根「2 小时前收线」的根（< `stale_after` = 3 小时，但 > 1 根）⇒ **一个字段都不写**、`fresh` 为假。这条用 v1 的 `stale_after_s` 判据会绿，用修正后的 `bar_seconds` 判据才红——它是这条收紧的唯一护栏 |
| `rest_backfill_stamps_px_wall_at_bar_close_not_now` | **【R:P0-3】** fresh 补取后 `now - _last_px_wall` == 该根的**真实**价龄（`now - (ts + bar_seconds)`），**不是 0**；且 `_last_candle_wall` == `now`。两个字段取值不同这件事本身就是断言的一部分 |
| `rest_backfill_does_not_unlock_a_stale_profit_guard` | **【R:P0-3】** 承接上一条：补取后立刻取一次保护快照 ⇒ 判定价年龄 > `max_px_age_s(90)` 时快照仍为 `unavailable`、`executor.flatten` 零调用 |
| `rest_backfill_steps_every_shadow_bar_not_only_the_last` | **【R:P1-6】** 补 5 根 ⇒ `note_live_bar` 被调 5 次、影子索引前移 5 格；`_last_board` 与「逐根喂 `feed_candle` 5 次」的对拍结果**逐字相等** |
| `rest_backfill_never_drives_a_decision_directly` | fresh 补取后 `_run_decision_round` 不被直接 `create_task`；决策只经 `_maybe_catchup_wake` / `_maybe_interval_wake` 发生（断言调用来源） |
| `a_dead_ws_masked_by_rest_still_gets_relinked` | **【R:P0-1】的会话级冻结用例**：WS 替身 `is_alive = False`、REST 替身每次都返回「刚刚收线的那一根」⇒ 跑 20 个 tick ⇒ `_rearm_feed(force=True)` 至少被调一次、且 `feedRecovery` 键在整段期间**从未变成 `null`**（横幅可以消失，但恢复卡不许消失） |
| `relink_attempts_reset_only_on_a_ws_confirmed_bar` | **【R:P1-11】** 连续 backfill 清了 `_stale_degraded` 之后计数**不复位**；喂一根 WS 确认根之后**才**复位 |
| `manual_relink_returns_within_the_deadline` | **【R:P1-5】** REST 替身永挂（`asyncio.Event` 永不 set）⇒ `relink_feed()` 在 `ai_trade_feed_relink_deadline_s` 内返回 `ok: false` 的完整回执，**不抛、不 504** |
| `relink_replaces_the_client_object_and_stops_the_old_one` | `force=True` 时**换新对象**且旧对象 `stop()` 被调用一次（复用既有 `test_ai_trade_pause_session.py:478` 的 `_FakeWs` 替身） |
| `backfill_and_relink_respect_their_cooldowns` | 冷却内的 tick 一次 REST 都不打（用替身计数） |
| `every_feed_action_failure_is_swallowed` | 让 REST / `stop()` / `start()` 各抛一次异常 ⇒ `_monitor_loop` 仍在跑、`_tick_fail_streak` 未增（动作层自己吞） |
| `escalated_never_flattens` | 走到 `escalate` 后 `executor.flatten` 零调用（**L-2 的护栏**） |
| `selfheal_disabled_is_byte_identical` | 总闸关时 `lifecycle_wire()` 的键集合与本特性合入前**逐字相等**；一次 REST 都不打 |

### 7.4 轴 C

| 用例 | 断言 |
|---|---|
| `mark_px_falls_back_to_ticker_when_the_decision_price_is_stale` | `_last_px_wall` 超龄 ⇒ 走 `get_ticker`；未超龄 ⇒ 零 REST |
| `mark_px_fallback_uses_last_px_wall_not_last_candle_wall` | 12H 周期、`_last_px_wall` 新鲜但 `_last_candle_wall` 已 6 小时 ⇒ **不**打 REST（本仓库第三次踩这个坑的护栏） |
| `mark_px_fallback_after_zero_is_byte_identical` | `= 0` ⇒ 逐字回退旧行为 |

### 7.5 轴 D（web，`NODE_ENV=test`）

| 用例 | 断言 |
|---|---|
| feedRecovery 键缺席 **或值为 `null`** ⇒ 整卡不渲染 | 两档表现一致、不占位（常态多一行只会稀释真正要紧的信息）；**【R:P1-3】** 但 wire 里两者可区分 |
| `多标的 ⇒ 每行点名标的；单标的 ⇒ 无前缀` | 复用 `NotArmedBanner` 的既有规则 |
| `source === "rest" ⇒ 追加「由 REST 补取维持」一句` | **【R:P1-4】** 用户必须能知道自己正跑在 REST 生命维持上 |
| `recovering ⇒ accent + 倒计时；stuck ⇒ danger + 两个按钮` | 颜色与按钮集合 |
| `nextAttemptInS === null ⇒ 不渲染倒计时` | 绝不对着一个不会到来的时刻读秒 |
| `点击【立即重连】后按钮禁用且文案变化，回执前不可二次点击` | pending 状态机 |
| `所有数字来自 wire` | 传入 `attempts.relink = 7` ⇒ 文案显示 7；组件内不得出现任何计数状态 |
| `单标的 ⇒ 不出现 1/1` | 复用既有规则 |
| `文案不含字面星号` | 正则冻结（源码扫描用例须写 `\r?\n`，`core.autocrlf=true` 下否则恒红） |

### 7.6 人工验收剧本（真实环境）

| # | 操作 | 期望 |
|---|---|---|
| A1 | 5 标的 **15m** 会话运行中，用防火墙**只**丢弃其中一个标的所在连接的入站数据包（保留 TCP 连接不 RST） | **【R:P1-8】** ≤ `data_silence` 预算内出现「实时链路已断开…正在重连」——15m 的预算是 `min(max(900,120),900)` = **900 秒**，**不是 120 秒**（120 是下界，只对 1m 与 public 端点成立）。想在两分钟内看到结果请改用 1m 会话。重连成功后补根，**Agent 在下一根 K 线恢复决策**；其余 4 个标的的成交与决策**全程不受影响**；顶栏芯片显示「不稳」而**非**「不可达」（【R:P1-2】） |
| A2 | 断网 30 分钟后恢复 | 恢复卡在断网期间显示 `recovering` + 递增的重连次数；恢复后自动转 `healthy`、卡片消失，横幅同时消失 |
| A3 | 断网并让 REST 也不可达，持续 10 分钟 | 走到 `stuck`；显示【立即重连】与【仅停止该标的】；**不发生任何自动平仓**；顶栏链路芯片变红（【R:P1-2】：本剧本断的是**网**，`_open` 之前即失败 ⇒ 无 `connected` 夹在中间 ⇒ 两次失败后确实到 `down`。A1 / A2 那种「频道静默但 socket 活着」的形态**不会**变红，别按红条验收） |
| **A3b** | 只让 WS 不可达、REST 保持畅通，持续 30 分钟 | **【R:P0-1】【R:P1-4】的现场验收**：K 线靠 REST 补取持续前移、横幅可以消失，但**恢复卡必须全程可见**并显示「当前 K 线由 REST 补取维持，实时通道尚未恢复」+ 递增的重连次数，最终走到 `stuck`。**若此时面板显示「一切正常」，本轮就等于没做**（那正是修复前的形态换了个更隐蔽的壳） |
| **A3c** | `wake_interval_s = 0`（`trend_hold` 档）下重跑 A2 | **【R:P0-2】的现场验收**：恢复之后**必须真的发生一轮决策**（轨迹面板出现新一轮），而不是只把横幅抹掉 |
| A4 | 在 `stuck` 态点【立即重连】（网络仍不通） | 按钮 pending → 回执「已重建行情连接，但交易所仍未返回更新的 K 线…」；计数 +1；**不崩、不重复弹窗** |
| A5 | 在 `stuck` 态恢复网络后点【立即重连】 | 一次点击内完成换链 + 补根，卡片转 `healthy`，横幅消失，下一轮决策正常发生 |
| A6 | 全程 `ai_trade_feed_selfheal_enabled=false` 重跑 A1 | 行为与本特性合入前**完全一致**（含那句 21 小时文案） |

### 7.7 回归门禁

- 全量 `pytest`（**不可并发**，且须 `python -u`；`services/quant` 的测试共用一个临时 DB）。
  **不得用 `-k` 子集跑法判回归**（本仓库已为此栽过一次）。
- 全量 `vitest`（`NODE_ENV=test`）。
- **行数门禁（【R:P1-10】可验收）**：`services/quant/app/ai_auto/session.py` 合入后 ≤ **2965 行**
  （今天 2923 + 净增 ≤ 40 + 2 行余量）。超出即说明动作层没有真正下沉到 `feed_runtime.py`，
  「超出即再拆」在本仓库历史上从未被真正执行过，故这里把它变成一个会红的数字。
- 源码扫描用例的正则一律写 `\r?\n`（`core.autocrlf=true` 下否则在干净检出上恒红）。
- 基线：合入前后**红用例集合必须相同**；已知的既有偶发红（时序类）须在评审节点单独列出，不得混入。

---

## 八、风险与缓解

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| **R-1** | **数据静默阈值过紧 ⇒ 重连风暴**。冷门标的 / 深夜低波动时段，candle 频道可能真的几分钟不推。 | 频繁重连烧配额、反而制造断连 | 下界 120s 是「OKX SWAP 主流标的实测亚秒级推送」的 2 个数量级余量；重连本身受既有全局建连令牌桶（`okx_link_ws_connect_rate_per_s`）与退避约束；`factor=0` 可一键关闭。**评审节点须复核这个下界。** |
| **R-2** | **REST 活性补取烧公共配额**。5 标的 × 每 60s 一次 = 5 次/分钟。 | 触发限速，殃及其他公共读 | 只在**已降级**时才发（正常运行零成本）；走既有令牌桶与熔断；`probe=True` 豁免（与补根同源，见 `gap_repair.py`）；冷却可调。 |
| **R-3** | **L-1 被后人改坏**：有人「顺手统一」两个 ingest 函数，把历史根当当下证据写四个墙钟字段。 | 断线后 Agent 拿 21 小时前的价做真实资金决策——**比现状严重得多** | ① 两个函数分开、各自 docstring 写明理由；② `rest_backfill_with_a_stale_last_bar_writes_nothing` 是冻结用例；③ 本文 §一 L-1 是立论级条目 |
| **R-4** | **换链竞态**：`_rearm_feed(force=True)` 与在飞的旧 supervisor 并存，同一根被喂两次。 | 窗口重复根 / 影子信号被双重步进 | `await old.stop()` 会 cancel 并 await 全部 endpoint task（既有语义）；`_window.is_duplicate` 是既有去重护栏；`_exec_lock` 不涉及（补取不写 executor） |
| **R-5** | **`_check_staleness` 重写引入回归**：它今天还承载 `live_auto_flatten_on_stale` 的强平分支。 | 一个「保留持仓的暂停」被误平 | 该分支**逐字保留在最前面**且不进 `plan()`；`escalated_never_flattens` 用例钉死自愈层不平仓 |
| **R-6** | **`session.py` 继续膨胀**（今天 2923 行）。 | 违反 CLAUDE.md 800 行红线更远 | **【R:P1-10】评审已复核：v1 的「净增 ≈ 90 行」低估了约一倍**（实算 190~220 行）。缓解措施因此从「超出即再拆」升级为**前置必做**：判定与文案下沉 `feed_watchdog.py`，**动作与状态下沉 `feed_runtime.py`**，会话侧净增 ≤ 40 行，并由 §7.7 的行数门禁（≤2965 行）钉死 |
| **R-7** | **轴 C 的 REST 回落在盯市热路径上**：每 5 秒一次 `get_ticker`。 | 断流期间额外配额消耗 | 只在 `_last_px_wall` 超龄（默认 180s）时才走；正常运行零成本；`= 0` 可关 |
| **R-8** | **`WSStreamConfig` 加字段打破既有测试替身**。 | 既有 WS 用例整片红 | 加法字段带默认值 `bar_seconds: int = 0` ⇒ 既有构造点零改动（与 `MarketWSHandlers.on_stream` 的既有做法同源） |
| **R-9** | **恢复卡与横幅同时出现 ⇒ 信息重复、噪音**。 | 稀释真正要紧的信息 | 横幅保留「是什么」，恢复卡负责「正在怎么办」，两者文案**不重复同一个数字**。**【R:P1-4】渲染条件由 `feedRecovery` 自己说了算，不再挂 reason**：REST 吊命期成员会回到 `armed`（横幅消失）而恢复卡**必须**继续在场——把渲染条件绑在 `feed_stale` / `feed_down` 上正好会在最需要它的那一刻把它关掉 |
| **R-11** | **【R:P0-1】自愈把「看得见的停摆」变成「看不见的停摆」**：REST 补取压住 `feed_age` ⇒ `classify_member` 回到 `armed` ⇒ 横幅与恢复卡一起消失，而 WS 仍然是死的。 | **比现状严重**：用户失去了唯一的知情通道 | 判定表第 2 行前置（`not ws_alive` / `link_state == "down"` 先于新鲜度判据）；`escalate` 增加 `or not ws_alive` 支；恢复卡渲染条件与 reason 解绑；`a_dead_ws_masked_by_rest_still_gets_relinked` + A3b 双重钉死 |
| **R-12** | **【R:P0-2】自愈成功但没有任何唤醒源接手**（`wake_interval_s = 0` 档）。 | 抹掉横幅、零决策——同样比现状严重 | `_feed_backfill` 的 fresh 分支显式走 `note_bar_drop` → `_maybe_catchup_wake()`（**经**既有全部守卫，不绕过）；§7.3 的 5b 用例在 `wake_interval_s = 0` 下断言决策确实发生 |
| **R-10** | **多标的下一个成员的自愈影响另一个**。 | 一个标的的重连风暴拖垮全组 | 每个会话有自己的 `OKXWSClient` 与自己的冷却计时；建连令牌桶是进程级的**闸**而不是**锁**，只排队不阻断；A1 验收剧本显式检查「其余 4 个标的全程不受影响」 |

---

## 九、非目标（本轮明确不做）

1. **不改 `classify_member` 的判定顺序或 `feed_down` 的极性。** 让判据回到唯一真源，而不是再加一个判据。
2. **不给 `live_auto` / `paper` 增加 REST 驱动的决策路径。** 它们通过轴 A 免费获得链路层修复；把「按
   REST 拉到的根直接下真单」这件事引入 `live_auto` 是另一个量级的风险决策。
3. **不新增任何自动平仓路径**，也不修改 `live_auto_flatten_on_stale` 的默认值与语义。
4. **不落库自愈状态**，不做 DB 迁移。
5. **不改前端图表侧（`owner="chart"`）的订阅行为**——它会被轴 A 一并改善，但不是本轮的验收对象。

---

## 十、实施顺序建议

1. 轴 A（`ws_supervisor` + `ws_client` + config 3 项）+ §7.1 用例 —— **可独立合入、独立验收**，它单独
   就能解决相当比例的现场（RC-1 是主因，且顺带修 RC-5）。
2. `feed_watchdog.py` 纯核心 + §7.2 用例 —— 零 IO，可完全离线开发。**先写 §7.2 里那两条带
   【R:P0-1】/【R:P1-1】标记的用例**，它们是判定表顺序的唯一护栏。
3. `ingest_ai_auto_live` + `note_live_bar` + §7.3 的复现用例 —— **先写复现用例**，且 5a / 5b 两遍都要写。
4. `feed_runtime.py`（动作层 + 状态字段 + 异常类）与会话侧接线（`_check_staleness` 薄执行器 /
   `_rearm_feed(force=)` / `relink_feed`）—— 完成后立刻跑一次 §7.7 的行数门禁。
5. 轴 C（`_latest_mark_px`）+ §7.4。
6. manager / 端点 / 前端类型 / API 客户端。
7. 轴 D 组件 + §7.5。
8. 全量回归 + §7.6 人工验收剧本。

---

## 附录 A · 名词与既有真源对照

| 名词 | 唯一真源 | 说明 |
|---|---|---|
| `armed` | `ai_auto/lifecycle.py:classify_member` | **呈现层真值，不是风控闸**（`lifecycle.py:12-15` 已钉死）。本轮不动它。 |
| `_stale_degraded` | `ai_auto/session.py` | 会话级行情降级标志；本轮**新增了它的出口**，未改它的入口判据。 |
| `_last_candle_wall` | `feed_candle` 确认根分支 + **本轮新增** `ingest_ai_auto_live` 的 fresh 分支 | 语义见 §一 L-1。 |
| `_last_px_wall` | `feed_candle` 两个分支 + `ingest_ai_auto_live` fresh 分支（**取值 = `ts + bar_seconds`，不是 `now`**，【R:P0-3】） | 利润保护新鲜度豁免的唯一真源（`max_px_age_s = 90s`）；轴 C 的判据也用它，但轴 C **只读不写**。 |
| `link_state` | `okx/link_state.py:derive_state` | 由**数据帧**驱动（`last_message_ts`），不由「多久没被调用」驱动。 |
| 判定性文案 | `lifecycle.classify_member` / **本轮新增** `feed_watchdog.describe` | 前端**绝不**自己拼中文。 |

---

## 评审结论（Review Verdict）

### **有条件通过**

v1 的**根因定位是这份文档最强的部分**，评审逐条重新取证后**全部成立**：RC-1 ~ RC-7 的行号无一虚指，
§2.1「登记簿早就知道，只是没人消费」那条推理与用户看到的那句话逐字吻合，四条修复轴的切分
（连接层 / 会话层 / 风控读数 / 呈现层）也切在了正确的接缝上。L-1（`_last_candle_wall` 的语义由**时效性**
而非**来源**定义）与 L-2（自愈不越权成风控）两条立论是对的，v2 全部保留、未做任何削弱。

但 v1 有**三处会造出比现状更坏形态**的缺陷，它们有一个共同的形状——**新通道绕过了自己声称的约束**：

- **R:P0-1**：判定表把「WS 已死」排在「行情看起来新鲜」之后 ⇒ REST 长生保活，一个看得见的停摆变成
  看不见的停摆；
- **R:P0-2**：自愈之后没有任何一条唤醒源真的会接手（`_maybe_catchup_wake` 全仓只有一个调用点，
  `plan_catchup` 要待办，`_maybe_interval_wake` 要 `wake_interval_s > 0`）⇒ 抹掉横幅、零决策；
- **R:P0-3**：活性补取把 `_last_px_wall` 写成 `now` ⇒ 用最多三根 K 线之前的价解锁利润保护层，正是
  `session_link.py` 模块 docstring 专门写下那条禁令要防的事，也正是 v1 自己 R-3 想防的事。

三条已在 v2 正文中修正，另有 11 条 P1 一并修正（判定表读 `link_state`、`down` 芯片的真实极性、
`feedRecovery` 的键存在性纪律、`/relink` 的服务端死线、影子逐根步进、阈值算式的自相矛盾、A1 的验收
数字、恢复卡的挂载契约、`session.py` 的真实增量与前置拆分、`relink_attempts` 的复位点）。
**本文档现已无未解决的 P0 / P1。**

### 放行条件（实现节点必须逐条满足，缺一即不算完成）

1. **判定表第 2 行不得下移。** `test_ai_trade_feed_watchdog.py` 里那条
   「`feed_age = 0` + `not ws_alive` ⇒ 仍 `relink`」是它的唯一护栏，**必须先于实现写出来**。
2. **§7.3 的复现用例第 5 步必须跑 5a / 5b 两遍**（`wake_interval_s > 0` 与 `== 0`）。只写 5a 的实现
   会通过全部用例却在 `trend_hold` 档现场失效。
3. **`_last_px_wall` 与 `_last_candle_wall` 在活性补取里取两个不同的值**，并有一条断言「两者不相等」
   的用例。任何把它们合并成一行赋值的「简化」都必须被这条用例挡住。
4. **`feed_runtime.py` 前置落地**，并跑通 §7.7 的行数门禁（`session.py` ≤ 2965 行）。
5. **A3b（只断 WS、REST 通）与 A3c（`wake_interval_s = 0`）两个新剧本必须真跑**——它们是 P0-1 与 P0-2
   在真实环境里的唯一验收点，离线用例替代不了。
6. **总闸关的四处回落逐字验证**（`plan()` 三态、`feedRecovery` 整键缺席、`/relink` 409、前端不渲染）。
   §5.2 已写明「四处回落缺一，这个开关就只是一句声明」——请把它当作一条会红的用例，而不是一句承诺。

### 建议但不阻断

- 【R:P2-6】`ingest_ai_auto`（断线补根）同样不逐根步进影子，故一次补根之后的信号板同样是跳根算出来
  的。这是**既有缺陷**，不在本轮范围；但本轮新增的 `note_live_bar` 逐根回调可在下一轮直接复用过去，
  建议在实现时给它留一条注释指向这个欠账。
- 【R:P2-2】`ws_supervisor.py` 模块 docstring 里「每 20s 发一个 ping」与 `config.py:50` 的 15.0 已经
  漂移，顺手改掉成本极低。

---

> **评审人签署**：本方案在采纳上述修正后，技术路径清晰、风险边界明确、回落开关完备，
> **同意进入实现节点**。

---

## 实施过程发现的方案缺陷（Issues Found During Implementation）

> 实施节点在按 §六 的清单逐项落地时发现下列条目：v2 正文在这些点上**自相矛盾或不可实现**。
> 每一条都给出「按字面实现会发生什么」与「实际采用的修正」，且修正一律取**与 §7 的冻结用例
> 一致**的那一支——用例是这份文档里更硬的那半边契约。

### F-1（阻断级）：`note_live_bar` 写 `_last_live_px` 会破坏「不新鲜就一个字段都不写」

§3.3.2 有两句直接冲突的话：

- 「`note_live_bar` **复用 `feed_candle` 确认根分支的同一段尾巴**（`session.py:1476-1481`）——
  步影子 → 写 `_last_board` / `_last_bar_index` / **`_last_live_px`**」；
- 「最后一根**不新鲜** ⇒ **一个字段都不写**」，而 §7.3 的冻结用例
  `rest_backfill_with_a_stale_last_bar_writes_nothing` 断言的是「**四个**墙钟字段一个字节不变」，
  `_last_live_px` 正是那四个字段之一（见 `okx/session_link.py` 模块 docstring 的禁写清单）。

按字面实现的后果：一次拿到「20 小时前那根」的补取会把 `_current_px()` 的返回值改写成 20 小时前的
收盘价，而 `_last_px_wall` 并不同步前移 —— 系统对「判定价有多新」的记账与它实际用的价分叉，正是
本轮 §3.4 花了整整一节去避免的那个形状。

**采用的修正**：`note_live_bar` 只写 `_last_board` / `_last_bar_index`；`_last_live_px` 由
`ingest_ai_auto_live` 的 fresh 分支**单点**写（与另外三个字段同进同出）。会话侧就地留了注释指向本条。

### F-2：`note_live_bar(row)` 的签名会在补 300 根时把盯市 tick 拖垮

按 §3.3.2 的签名逐根调用，每一根都要 `build_strategy_frames()`（逐策略 `prepare` + MTF
`attach_timeframes`）。活性补取的取数上限沿用 `okx_link_gap_repair_max_bars`（默认 300），于是一次
补取最坏会建 300 次帧 —— 而它跑在 5 秒周期、守着真实资金的 `_monitor_tick` 上。

**采用的修正**：签名改为 `note_live_bar(row, *, index, frames)`，由 `ingest_ai_auto_live` **一次建帧、
逐根步进索引**。这与 `session._prime()` 的既有做法逐字同源（「各策略在整段暖机窗口上的因果帧」）：
`prepare` 是因果的，故批量帧在索引 i 上的取值与「喂到第 i 根时建帧」相同。§7.3 的
`rest_backfill_steps_every_shadow_bar_not_only_the_last`（「`note_live_bar` 被调 5 次、影子索引前移
5 格」）仍然逐字成立。

### F-3（阻断级）：`stage` 与 `action` 合并会让恢复卡在 REST 吊命期间消失

§3.3.1 的 `FeedPlan` 只有一个 `stage`，而判定表第 2 行规定「冷却中 ⇒ 不返回，继续向下判定」。
两者组合起来：WS 已死 + 换链冷却中 + REST 把 `feed_age` 压成新鲜 ⇒ 走到第 3 行 ⇒ 返回
`clear`/`none` 且 `stage = fresh` ⇒ `feedRecovery` 变成 `null` ⇒ **恢复卡消失**。

这恰恰是 §7.3 的 `a_dead_ws_masked_by_rest_still_gets_relinked`（「`feedRecovery` 键在整段期间从未
变成 `null`」）与人工剧本 A3b 明令要防的形态，也就是 R-11 描述的那条「把看得见的停摆变成看不见的
停摆」。

**采用的修正**：`action` 与 `stage` 是**两个量**。链路已死时把 `stage` 锁在 `relink` /
`escalated`（并带上 `next_attempt_in_s`），而 `action` 仍可以是 `clear` —— 行情确实新鲜，Agent 该
解锁；实时通道确实没恢复，恢复卡该在场。**横幅可以消失，恢复卡不许消失**。`plan()` 的 docstring
就地写下了这条，`test_a_dead_ws_inside_the_relink_cooldown_keeps_judging_instead_of_bailing_out`
是它的护栏。

### F-4（阻断级）：第 5 行（escalate）排在第 3/4 行之后 ⇒ 被 REST 吊着的死链永不升级

§3.3.1 把 escalate 放在第 5 行，但在「WS 死、REST 通」这一形态下第 3 行（`feed_age <= stale_after`）
**永远先返回**，于是第 5 行不可达 —— 会话会无限 `relink → 冷却 → relink`，`escalate` 一次都不发生，
用户永远等不到 §3.5.2 承诺的那个「主动权交回给你」的时刻。这与 §3.3.1 自己为第 5 行写的理由
（「没有这一支就会无限 relink 而永不升级」）**方向一致、位置矛盾**。

**采用的修正**：链路已死的那一支里，**先判 escalate、再判 relink**（其余形态下 escalate 仍留在第
5 行的位置）。`test_a_rest_propped_dead_link_can_still_escalate` 与
`test_a_dead_ws_masked_by_rest_still_gets_relinked` 双重钉死。

### F-5：`_should_escalate` 不得要求 `is_degraded`

沿 F-4：REST 吊命形态下 `_stale_degraded` 会被 `_feed_backfill` 自己清掉，若给升级判据加一条
`is_degraded` 前置（第 5 行的位置隐含了这一点），升级同样永不发生。**采用的修正**：逐字按第 5 行
写出的条件实现（`relink_attempts >= escalate_after > 0` 且「换链门槛已过 **或** `not ws_alive`」），
不引入 `is_degraded`。

### F-6：`ai_auto/session.py` 里**不得出现字面串 `ingest_ai_auto`**（连注释都不行）

既有冻结用例 `test_okx_session_link.py::test_sessions_only_bind_and_never_reimplement_the_link_logic`
做的是**源码子串扫描**（`assert name not in source`），`ingest_ai_auto` 在禁用名单里。会话侧的注释
若提到这个函数名（哪怕是 `ingest_ai_auto_live`）就会把这条既有门禁打红。**采用的修正**：会话侧一律
以「`okx/session_link.py` 的活性补取入窗」指代它。

### F-7：`"rest"` 这个来源标记不能由 `okx/session_link.py` 写

§3.3.3 说「`ingest_ai_auto_live` 的 fresh 分支写 `"rest"`」，但 `_feed_last_source` 是自愈层的状态，
而 `okx/session_link.py` 是链路层、按本仓库的既有纪律**不认识** `ai_auto` 的运行时对象（它连
`ingest_ai_auto` 的四字段禁写都是靠 docstring 约束的）。**采用的修正**：`ingest_ai_auto_live` 只返回
`{"appended", "fresh", "lastTs"}`，由 `FeedRuntime._backfill` 据 `fresh` 写 `last_source = "rest"`。
`feed_candle` 侧对称：会话调一行 `self._feed.note_ws_bar()`（同址同进同出）。

### F-8：`attempts.backfill` 在 §4.1 的 wire 里，却不在 §3.3.3 的字段清单里

§4.1 的载荷有 `"attempts": {"backfill": 17, "relink": 2}`，而 §3.3.3 列出的 7 个新增字段里没有补取
计数。**采用的修正**：`FeedRuntime` 多持一个 `backfill_attempts`，与 `relink_attempts` 在
`note_ws_bar()` 同址复位。

### F-9（观察项）：`escalate` 需要「只出声一次」

§3.3.1 没有规定 `escalate` 的推送节流，而它在 `plan()` 里是一个**稳定态**（条件一旦成立就每个 tick
都成立）。按字面实现会每 5 秒推一条 status。**采用的修正**：`FeedRuntime._escalate` 自带一个
`_escalated_announced` 闩，`note_ws_bar()`（WS 真的又送来一根确认根）与 `_clear` 时复位。

### F-10（回归级）：判定表不得给一个「执行器有文档地拒绝执行」的状态下动作指令

§3.3.1 把 `ws_alive` 定义为 `self._ws is not None and self._ws.is_alive`，逐字实现之后，一个
**从未 `start()` 过**的会话（`_link is None` / `_ws is None`）在第 2 行上恒判「链路已死」⇒ 每个
tick 都下 `relink`。而 `_rearm_feed` 对这一档是一个**有文档的零动作**（v2 §3.3.3 自己保留了那条
早退：「重建会造出一条没人处理其链路事件的野连接」）。净后果有两条：

1. 界面上会出现一句「正在重建实时行情连接」，而实际上什么都没有被重建 —— 一句无法被反证的假话；
2. 第 2 行前置之后它**吃掉了第 4 行**，`_stale_degraded` 再也不会被置位 —— 两条既有冻结用例
   `test_ai_trade_pause_session.py::test_resume_does_not_reset_the_stale_clock` 与
   `..._never_pushes_the_feed_clock_into_the_future`（上一轮 D-7 的护栏）当场变红。

**采用的修正**：`FeedRuntime.readings()` 在 `_link is None` 时把 `ws_alive` 报成 `True`。生产上这一
档恒不可达（`start()` 同时装 `_link` 与 `_ws`，而 `_check_staleness` 的顶部判据 `_last_candle_wall > 0`
也是 `start()` 写的），故它只影响「会话还没武装过任何一条流」这一形态 —— 那一档本就该由
`classify_member` 的 `not_warmed` / `feed_down` 去说，而不是由自愈层去空转。
新用例 `test_a_session_that_never_started_is_not_reported_as_a_dead_link` 是它的护栏。

### 未偏离的部分（供终审复核时快速排除）

- §3.2 的算式、默认值与 §7.1 的换算表**逐字实现**（`bar >= 15m` 恒取上界 900，用例按算式实算）。
- §3.3.2 的写值口径（`_last_candle_wall = now_wall`、`_last_px_wall = ts + bar_seconds`）与「新鲜判据
  收紧到一根」**逐字实现**，并各有一条断言两者取值**不相等**的用例。
- §3.3.2 的「唤醒交接三步」（`_clear_stale_if_recovered` → `note_bar_drop` → 直调
  `_maybe_catchup_wake`）**逐字实现**，且 §7.3 的 5a / 5b 两遍都写了。
- §3.4 的「纯读、三个墙钟字段一个都不写」**逐字实现**，判据用 `_last_px_wall`。
- §7.7 的行数门禁：`ai_auto/session.py` 合入后 **2965 行**（原 2923，恰好落在门禁 ≤2965 上）。
- §五的「四处回落」全部有用例：`plan()` 三态、`feedRecovery` 整键缺席、`/relink` 409、前端因键缺席
  不渲染。

---

## 终审复核记录（Final Review · 2026-08-27）

> 终审节点逐条对照 §六 的 20 个落点复核了实现，**覆盖完整、无遗漏**；§7.1~§7.5 的冻结用例逐条命中，
> §7.7 的三道门禁（全量 pytest / 全量 vitest / 行数）复跑通过。下列 7 条是终审当场修掉的问题，
> 已直接改在源码里，不留 TODO。

### R-F1（正确性）：`_rearm_feed` 的换代不可重入 —— 会漏下一条没人停得掉的野连接

本轮把 `_rearm_feed` 从「只有 `resume()` 一个调用点」变成三个（盯市 tick 的 L3、人工【立即重连】、
续跑），而它中途会先把 `session._ws` 置空、再 `await old.stop()`。两条并发进来时，后到的那条看见
`None` ⇒ **跳过收尸** ⇒ 两条新连接同时在线，先到的那条的赋值随即被覆盖——留下一条**没人持有、也
没人停得掉**的 WS 一直烧配额直到进程退出。两条并发都是真实可达的：`pause()` 不取消 `_monitor_task`，
故续跑与盯市 tick 的 L3 会重叠；人工端点跑在另一个 task 上，与盯市 tick 同样会重叠。

**修法**：换代全程持 `FeedRuntime.rearm_lock`（闸住在自愈运行时上，`session.py` 净增为 0 行，
行数门禁仍为 2965）。新用例
`test_ai_trade_feed_selfheal.py::test_two_concurrent_rearms_never_leak_a_client` 是它的护栏——
它要求替身的 `stop()` **真的让出一次事件循环**，否则用例会绿在一段从没被走到的路上。

### R-F2（HCI / 注释）：恢复卡的挂载点把「可交易性红条」那段注释挤成了孤儿

`AiTradePanel.tsx` 里 `<AiTradeFeedRecoveryCard />` 被插在注释块与它所描述的 `<TradabilityStrip />`
之间，于是那段注释读起来像是在描述恢复卡。已把挂载点上移到 `NotArmedBanner` 之后（挂载位与 §3.5.2
一致：横幅之下、可交易性红条之上），注释与它描述的组件重新相邻。文件仍为 799 行。

### R-F3（一致性）：`AiTradeFeedRecoveryView` 的 `relinkAttempts` / `onRest` 有写点没读点

两个字段被算出来、被用例断言，却**没有任何组件读它们**——重连次数与「当前 K 线由 REST 补取维持」
那一句都已由后端 `feed_watchdog.describe` 拼进 `recoveryText`。这正是本仓库反复出事的「算了但没人看」
形状。已从视图模型删除（只留 `instId` / `label` / `stuck` / `nextAttemptInS` 四个真的被读的字段），
两条相关用例改为守真正的纪律：文案逐字来自 wire、前端一个字都不自己加。

### R-F4（诚实性）：`_rearm_feed` 的日志仍写着 "on resume"

它现在也服务自愈与人工兜底两条路径。已改为 `ai-trade feed re-armed: session=%s inst=%s force=%s`。

### R-F5（风格）：`api/ai_auto.py` 的新 import 破了既有的字典序

`..ai_auto.feed_runtime` 被插在 `profit_guard` 与 `session` 之间。已移到 `experience` 与
`hold_horizon` 之间。

### R-F6（完整性）：9 个新配置项一个都没进 `.env.example`

§六 的清单漏了 `.env.example`，而它是本仓库**唯一**的运维旋钮目录（上一轮合入 `039fa80` 就带了
`.env.example +37`）。9 个新项一个都没登记，其中包括整轴总闸 `AI_TRADE_FEED_SELFHEAL_ENABLED`
——§5.2 花了一整段论证「四处回落缺一，这个开关就只是一句声明」，而一个**运维员找不到**的开关
同样只是一句声明。仓库里没有任何用例扫这个文件，故这个缺口是静默的。已按既有分节风格补齐：
3 个 `OKX_LINK_WS_DATA_SILENCE_*` 并入既有的「OKX 链路韧性加固」节，6 个
`AI_TRADE_FEED_*` / `AI_TRADE_MARK_PX_FALLBACK_AFTER_S` 新起一节（含总闸的四处回落说明与
「本层绝不平仓」那条边界）。

### R-F7（正确性）：L2 补取拿**取数之前**的时钟去判「最后一根是不是当下这根」

`FeedRuntime._backfill` 把冷却戳的 `now` 一路传进活性补取入窗做 `now_wall`，而中间隔着一次
`fetch_missing`——它会走完整的 REST 重试 / 退避，最坏烧掉 `okx_link_rest_budget_s`（25 秒）。
用一个偏旧的 `now_wall` 判新鲜度，等于给「一根」的窗口凭空多送一份容差：1H 下可忽略，1m 下是
**40% 的窗口**——恰好抵消掉 §3.3.2 把判据从三根收紧到一根所买到的那份安全。人工路径
（`_relink_once`）本来就是取数之后再读一次时钟，两条路径口径不一致。

**修法**：`_backfill` 的 ingest 改用 `now_wall=time.time()`（与 `_relink_once` 同口径）；冷却戳
仍用取数之前的 `now`（冷却本就该从「我开始尝试」起算）。`ingest_ai_auto_live` 的 `now_wall`
docstring 写的「判定与写值共用同一个数」因此才真的成立。

### 判定为「不改」的一条（供后人快速排除）

- **§3.5.2 纪律 1 后半句「倒计时的锚点用 `lifecycle.asOfTs` + 本地单调时钟推进」未实现，判定为
  正确取舍。** 该纪律的前半句（「前端一个数都不自己算」「中文由 `feed_watchdog.describe` 单点生成」）
  与后半句互斥：要让秒数在本地逐秒前进，前端就必须自己拼出「下一次尝试 N 秒后」这句中文。实现选了
  前半句那一侧——恢复卡只渲染后端下发的整句，数字随 status 推送与既有 15 秒轮询刷新，而
  `nextAttemptInS === null`（冷却外 / 已升级）时整行不读秒的纪律**逐字保留**。成员级 `lifecycle`
  本身也没有 `asOfTs`（那是分组级 wire 的键），故后半句照字面实现还要新增一个跨层的键。
