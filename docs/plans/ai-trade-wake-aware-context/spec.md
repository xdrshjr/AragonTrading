# AI 自动交易 · 唤醒感知上下文（ai-trade-wake-aware-context）设计方案 v2

> 状态：**v2**（Subtask #1 评审完成：2 条 P0 + 7 条 P1 已在正文修复，8 条 P2 记录待决；结论见 §十）
> 归属：AI 自动交易（`services/quant/app/ai_auto/*` + `apps/web` 决策轨迹面板）
> 前序：`ai-trade-reflection-hold-inertia-fix`（F4c 空仓重复唤醒守卫）、`ai-auto-trading-enhancements`
> （多周期中线背景 E1/MTF）、`ai-trade-reasoning-chain`（R1 市场证据摘要）、`ai-trade-profit-protection*`

---

## 〇、评审记录（v2，Subtask #1 填写）

> 评审姿态：逐节对照**真实代码**核实（行号为评审当时 `master` 工作树实测值），不接受任何未落到代码的
> 断言。下表每条都带「核实证据」——凡我没能在代码里读到的，一律不写成结论。
> P0/P1 已在正文直接修复（正文以 **[评审 P0-x/P1-x]** 标记改动点）；P2 只记录、由 Subtask #2 拍板。

### 0.1 P0（阻断级：不改则功能在主路径上自相矛盾或静默失效）

| # | 位置 | 问题 | 核实证据 | 处置 |
|---|---|---|---|---|
| **P0-1** | §2.3 / §2.6 `inject(as_of_ts=…)` | **战术段 `as_of_ts` 的时钟没有定义，两种可选写法都会让定时唤醒轮的全部战术周期恒被判为「数据陈旧」**——而系统 prompt 决策纪律 #2 明写「陈旧或缺失周期不得补猜」，等于亲手命令模型忽略本方案新增的唯一证据。 | `_maybe_interval_wake` 传给决策轮的 `ts` 是**墙钟** `int(now)`（`session.py:455`），而 `feed_candle` 传的是**根开盘 ts**（`session.py:556`）。既有 MTF 无差别地用 `as_of_ts = ts + BAR_SECONDS[bar]`（`session.py:1188`）：定时轮下它等于 `now + 1800`（30m），代入 `_build_frame` 的 `ageSeconds = as_of_ts - close_ts`（`multi_timeframe_context.py:95`）与 `stale = age >= 2*BAR_SECONDS[bar]`（:97），1m 的 stale 阈值仅 120s ⇒ **恒 stale**；若改传裸 `ts`，K 线轮又变成 `as_of_ts` = 根开盘时刻 ⇒ 丢掉整根执行周期内的战术数据。仓库已有同型前科（memory: AI-trade `trades.ts` 双时钟）。 | 正文 §2.3 新增「决策瞬刻单一真源」小节：`decision_as_of_ts` 按 trigger 分支求得并**同时**喂 MTF 与战术段；`reference` 改存上一轮的 `asOfTs`（同钟）而非 `time.time()`；`closedBars` 判据从 `ts >= wall` 改为 `ts + BAR_SECONDS[bar] > reference_as_of`（按**收线时刻**计数）。新增 T-19 断言定时轮 1m/5m `stale is False` 且 `staleBars == []`。 |
| **P0-2** | §2.4 `moved()` / §2.5 / §4.2 | **ATR 判据跨时间尺度失配**：用「执行周期一根的 ATR」去量「唤醒间隔内的位移」，两者时间跨度可差 3 个数量级。4H / 1D 执行周期下阈值高到永不可能被越过 ⇒ **空仓定时唤醒被永久跳过**，AI 一天只决策一次；且这恰好抽掉了轴 A 的服务对象（战术证据本就是为定时轮准备的），两轴自相矛盾。 | `self._atr_px = safe_atr(self._window.base_df(), …)`（`session.py:548`）——`base_df()` 是**执行周期**窗口（`market_window.py:74`）。1D 执行 + 默认 2 分钟唤醒下，`0.5 × ATR(1D)`≈1.5% 的位移要在 120 秒内发生。而 1D/4H 是**用户可选的合法配置**：`AiTradeLaunchModal.tsx:69` 的 `BARS = ["1m","5m","15m","30m","1H","4H","1D"]`，`AiTradeLaunchModal.test.tsx:250` 已断言 4H 可选。 | 正文 §2.4 新增 `horizon_scale()` 纯函数按随机游走 √t 律把执行周期 ATR 折算到唤醒窗口（`sqrt(min(1, wake_s/bar_s))`）；判据改 `move_atr_mult × atr_px × horizon_scale`。校准：30m/3min ⇒ 0.158×ATR、1D/2min ⇒ 0.019×ATR，两者折合的价格位移都落在今日 0.05% 同一量级，但**随波动率自适应**（正是诊断书办法一的目标）。另增 `ai_trade_wake_max_skip_streak`（默认 5）连续跳过上限作为饿死兜底，并把 `moved()` 签名收敛为 `(facts, *, …)` 以守 ≤5 形参。 |

### 0.2 P1（必须修，已在正文落实）

| # | 位置 | 问题 | 核实证据 | 处置 |
|---|---|---|---|---|
| **P1-1** | §2.2 vs §2.6 | 战术周期集合被**算两次**（`MarketWindow.__init__` 一次、`WakeRuntime.__init__` 一次），违背本方案自己立的「同源」原则；且 `MarketWindow` 根本拿不到 `wake_interval_s`，补形参会把它顶到 6 个、破 CLAUDE.md ≤5。 | `MarketWindow(inst_id, bar, specs, priming_bars, *, context_enabled)` 已占 5 形参（`market_window.py:27`）；`wake_interval_s` 只在 `AiTradeSession.__init__` 可见（`session.py:113`）。 | 正文 §2.2 引入 `WindowOptions` 冻结 dataclass（聚合 `context_enabled` + 已解析的 `tactical_bars` + 三个窗口参数），`MarketWindow` 形参回落到 5；战术集合的**唯一真源**是 `WakeRuntime`，窗口只消费不自算。§2.6 补「`WakeRuntime` 必须在 `MarketWindow` **之前**构造」。两处既有调用点（`session.py:174` / `manager.py:595`）与 `test_ai_trade_market_window.py:23` 的改法在 §3.1 写明。 |
| **P1-2** | §3.3 「`describeTrigger` 优先读 `trace.wake.kind`」 | 会**打掉**既有更有信息量的轮头标签，并让一条既有测试转红——而方案却把它描述为「自动升级」。 | `describeTrigger` 现行优先级是「信号板边沿 > trigger」（`aiTrade.ts:944-968`）；`aiTrade.test.ts:444-447` 带注释显式断言「有边沿信号时，即便 trigger=interval 仍按信号板派生」。若 wake 抢先，`开多信号 · 均线突破` 会退化成 `价格异动`。 | 正文 §3.3 改为明确三级优先：**信号板边沿 > `wake.kind` > `trigger`**；`wake.kind` 只细化「无边沿」分支（把原来单调的两个兜底文案扩成七态）。新增 G-8：`aiTrade.test.ts` 既有 6 条 `describeTrigger` 断言**一字不改**且全绿。 |
| **P1-3** | §3.3 `AiTradeTraceRound.tsx` | 只说「插入战术周期块」，**漏了整段的早退门控**——只有战术数据的轮次会让「输入信号板」整段消失（功能跑不出错、永远空白）。这正是本仓库 RV-2 的复发形态（memory: 新增边缘态字段须验渲染门控没排除掉该态）。 | `SignalContext` 第 520 行：`if (!board.length && !regime && !consensus && !price && !volume && !factors && !multiTimeframe) return null;` —— 条件里没有 tactical 项。 | 正文 §3.3 明列「早退门控必须补 `&& !tactical`」，并把它写进 T-18 的断言（构造仅含 `tacticalContext` 的轮次，断言整段仍渲染）。 |
| **P1-4** | §2.2 调用点 2 | 把最多 N 次 REST 塞进 **5 秒盯市 tick 且在唤醒之前**，一次交易所慢响应会阻塞整个盯市循环 ⇒ **利润保护棘轮 / 会话止损延迟**（真实资金路径）。方案只谈了 429 配额，没谈延迟。 | `_monitor_loop` 顺序 `await self._monitor_tick()`（`session.py:395`），tick 内部串行（:399-418）；REST 单次 `timeout=httpx.Timeout(20.0)` 且 `retries: int = 3`（`rest_client.py:85,96`）⇒ 单周期最坏 ~60s，2 周期 ~120s。 | 正文 §2.2 双闸门升级为**三闸门**：加「每 tick 至多刷新一个战术周期」的节流闸；`_safe_get_candles` 外再套 `asyncio.wait_for(..., ai_trade_tactical_fetch_timeout_s)`（默认 8s，超时归入既有 `except Exception` 降级路径、不推进锚点）。新增 T-20 断言单 tick 最多一次取数、T-21 断言取数挂住不阻塞 tick。 |
| **P1-5** | §2.2 | 取数 `limit` 与窗口容量的关系未定义：`maxlen=120` 但 `_htf_windows` 用的是 `_window_max`（最大 1200），预填/刷新各拉多少根、跨越窗口容量后如何去重，全是下游自由发挥。 | `prefill_htf` 用 `limit = min(PAPER_WINDOW_MAX, warmup + 5)`、`maybe_refresh_htf` 用另一套 limit（`market_window.py:158,181`）；`PAPER_WINDOW_MAX = 1200`（`paper/session.py:22`）；`get_candles` 在 `limit<=300` 时走单发 `/market/candles`，否则**翻页**（`rest_client.py:155`）。 | 正文 §2.2 写死两个 limit：预填 `min(300, window_bars + 5)`（**必须 ≤300 以保持单发、绝不触发翻页**），刷新 `min(300, max(20, min_refresh_s // BAR_SECONDS[b] + 10))`；入窗一律按 `ts` 去重覆盖，与 `prefill_htf` 的 `existing` 集合同姿态。 |
| **P1-6** | §2.6 / §2.9 | `WakeRuntime` 的公开面不自洽：§2.9 用了 `self._wake.last_round_kind` 但 §2.6 的类骨架没有这个属性；`note_guard_status()`（每 tick 记）与 `WakeFacts.guard_status`（每轮传）职责重叠、真源不明。 | §2.9 与 §2.6 直接对读即见。保护快照的真源是 `_refresh_profit_state` → `ProfitGuardRuntime.refresh`（`session.py:1012` / `profit_guard_runtime.py:138`），它已在 `_maybe_profit_exit` 内于**唤醒之前**跑过（`session.py:414` vs :418），故 tick 上确有可读的新鲜 status。 | 正文 §2.6 补 `last_round_kind` / `last_reference` 两个只读属性，并把 guard status 的真源钉死：`note_guard_status()` 只负责记录「本 tick 快照 status」，`WakeFacts.guard_status` **必须**由它回读（不得由调用方另取一次），`last_round_guard_status` 由 `note_round_start` 冻结。 |
| **P1-7** | §1.2 vs §6.3 A-4 | 上下文预算口径自相矛盾（§1.2「重复轮 prompt 字符数**不高于**今日水平」vs A-4「不得超过 **115%**」），且 A-4 只约束重复轮 ⇒ `candle_close` / `full` 轮**没有任何上限**，战术段可让首轮单调膨胀。渐进式披露是用户明确提出的约束，不能只管一半。 | 两处正文直接对读。§2.8 的 `full` 档为每周期两行、外加背离句与截断句。 | 正文 §1.2 改为与 A-4 同口径（重复轮 ≤115%、brief ≤100%），并在 A-4 新增 **A-4b**：`full` 轮总字符数 ≤ 改动前同轮次的 **135%**，超出即视为披露分档失效；`ai_trade_tactical_max_chars` 的截断标记同时作为该指标的兜底。 |

### 0.3 P2（记录，Subtask #2 自行拍板；不阻塞放行）

| # | 位置 | 观察 |
|---|---|---|
| P2-1 | §2.8 系统 prompt | `build_ai_trade_system_prompt` 现已 **10 个形参**（`prompts.py:70-76`），新增 `tactical_bars_text` 后 11 个。方案一边为 `session.py` 严守 CLAUDE.md 的 ≤5（§2.7 用 `wake` 换 `trigger`），一边在此处默认破规。建议：**要么**把三段宪章文案 + 本参数收进一个 `SystemPromptTexts` 聚合结构体（一次性还清），**要么**按 CLAUDE.md §二在 `.claude-index/config.md` 显式记录该文件的阈值偏离。评审不强制，但不允许静默。 |
| P2-2 | §2.3 `alignment` | 战术 `regime` 来自 `build_market_context`，而它受**另一个**总闸 `ai_trade_regime_context` 管制（`market_context.py:33` 直接 `return None`）。该闸关闭时 `alignment` 恒 `"insufficient"`、`disclosure_tier` 的第 3 条判据恒假、prompt 的背离裁决句永不出现。建议在 §4.2 表格注明这层耦合，并在 T-8 补一例「regime 总闸关 ⇒ 仍按其余三条判档」。 |
| P2-3 | §2.3 `changeSinceLastRound` | `pct` 各周期**同值**（它只由 `last_px` 与 `reference.px` 决定，与周期无关），逐周期重复渲染同一个数是纯 token 浪费；且 `atrMult` 的 ATR 是**执行周期** ATR，文案写成「≈0.91×ATR」会被模型误读为战术周期自身的 ATR。已在正文把 `pct`/`atrMult` 上提到 `tacticalContext` 顶层、周期级只留 `closedBars`，文案改为「≈0.91×30m ATR」——若 Subtask #2 认为前端就近展示更重要，可保留周期级冗余但**必须**改文案标明量纲。 |
| P2-4 | §2.8 repeat 档 | `_format_multi_timeframe` 在 `frames` 为空时返回降级行「中线背景暂不可用，已回退基础周期」（`prompts.py:403`）。`format_multi_timeframe_repeat` 对这一支的行为未定义。建议：repeat 档遇 `frames` 为空时**原样复用**该降级行（不要压成表头，表头此时全是 `None`）。 |
| P2-5 | §6.2 G-5 | `grep -rn "ALTER TABLE\|add_column" services/quant/app/db` 在今日就有 5 处命中（`db/models.py:345`、`db/session.py:103/109/124/132`）。门禁若按「无匹配」判定会**永远红**，若按「有匹配就算通过」则**永远空转**——本仓库有 grep 式门禁空转前科。已在正文把 G-5 改为「匹配行集合与改动前**逐行相同**」。 |
| P2-6 | §2.1 `_TACTICAL_LADDER` | 梯子缺 `12H`（`BAR_SECONDS` 有它、`_CONTEXT_POLICY` 也在用、REST 也能取），1D 执行周期因此从 4H 直接跳到 1D。已在正文补入 `12H` 并把选取表补齐 4H / 1D 两行（原表 7 行只覆盖到 1H 执行周期，而 4H/1D 是合法可选值）。 |
| P2-7 | §3.1 文件表 | 新增 8→11 个 `AI_TRADE_*` 设置**无需**改 `desktop/src/lib/config.js`：该文件是**前缀白名单**（`config.js:72` `key.startsWith('AI_TRADE_')`），已核实覆盖。建议在 §3.1 表下写明这一条，避免下游误以为要动这个混合在制文件（memory 记录它当前有他人未提交改动）。 |
| P2-8 | §4.1 | `tacticalTier` / `contextDisclosure` 两键在总闸关闭时是否缺席，原文只给了 `wake` / `tacticalContext` 的「整键缺席」注释。已在正文补齐：三总闸各自独立控制自己的键，`contextDisclosure` 由 `ai_trade_progressive_disclosure` 单独把门。 |

### 0.4 评审同时核实为**正确**的关键断言（不改，留证）

- `session.py` 1494 行、`prompts.py` 735 行 —— 均实测相符，下沉新模块的决定成立。
- 战术周期与 `_htf_bars` 集合**不可能相交**：`_htf_bars` 只收 `BAR_SECONDS[tf] > base_seconds` 的周期（`market_window.py:45-50`），战术周期严格 `< exec_s`。✅
- `describeTrigger` 的消费点确实**只有** `AiTradeTraceRound.tsx:50` 一处（`grep` 实测），§3.3 的展示点清点无遗漏。✅
- `store.ts` 的 `emptyRoundTrace` 确实是**逐键手抄**（`store.ts:119-190`），漏抄即静默吞键；`trace.new_round_trace` 同样是显式取键（`trace.py:107-144`）。两处点名正确。✅
- prompt 段落顺序断言逐行成立：MTF(546) → 体制(547) → 共识(550) → 先验(553) → digest 三行(556) → 铁律(562) → 信号板(566) → 摘要(569)，故「digest 之后、`base_rules_line` 之前」是正确插点。✅
- `ARMED_STATUSES = (STATUS_ARMED, STATUS_BREAKEVEN_ARMED)` 真实存在（`profit_guard.py:73`）；`digest.volume.spike`（`market_digest.py:157`）与 `factors.rsi14.label`（:177,:196）真实存在。✅
- 回测 payload 确实不含 `trigger` 键（`backtest.py:615-640`），故 G-2 的「回测天然不受影响」成立。✅
- §2.9 的归因白名单**真实可达**：AI 自己的开/平仓走 `build_atomic_explain` ⇒ `kind="atomic"`
  （`live_auto/explain.py:74`，经 `session._ai_explain:963`）；反手平仓腿走 `_base("reverse", …)`
  ⇒ `kind="reverse"`（`explain.py:101`，`live_auto/executor.py:854`）。而利润保护平仓走
  `build_profit_guard_explain` ⇒ `kind="profit_guard"`（`explain.py:181`，`session.py:1057`），
  天然落在白名单外。故「保护层平仓不打 `wakeKind`」（A-3）是**真的**成立，不是一个空转的白名单。✅
- `/market/candles` 确实会返回未收线当前根（`rest_client.py:157` 直出 `parse_okx_candle`，不过滤 `confirm`），故「入窗只收 `confirm`」的要求是必需的，不是防御性噪音。✅
- G-6 的红灯基线（7 个 `factor_library_expansion` 孤儿 + `test_agent_ws` 顺序 flake、全量不得并发）与仓库已知基线一致。✅

---

## 一、概述

### 1.1 问题：AI 被反复问同一个问题

当前一个 AI 决策会话有两个触发源。**K 线唤醒**：执行周期（用户选的 `bar`，例如 30m）的确认根落地，
`session.feed_candle` 重算信号板并起一轮决策。**定时唤醒**：5 秒盯市循环末尾的 `_maybe_interval_wake`
检查「距上一决策轮是否已超过 `wake_interval_s`」（默认 2 分钟），到点即用**最近一次确认根的信号板快照**
加当前实时价起一轮决策。

后果是结构性的：30m 执行周期 + 3 分钟唤醒下，一根 K 线期间 AI 被叫醒约 10 次，其中 9 次拿到的
`signalBoard` / `marketContext` / `marketDigest` / `multiTimeframeContext` **逐字节相同**，唯一变化的是
`lastPx` 一个数。模型被要求对同一份证据反复表态，只能反复输出 `hold`；而这些 `hold` 会进 `_journal`
与滚动摘要，被模型自己当成「观望这个判断已被反复验证」的独立证据——`wake_guard.py` 的模块 docstring
把这个后果写得很清楚，本仓库也为此打过一次补丁（F4c 守卫）。补丁只减少了**静止行情**下的重复轮次，
没有改变「重复轮拿到的证据是重复的」这个事实。

同时，模型**不知道自己为什么被叫醒**。`payload["trigger"]` 只有 `"candle"` / `"interval"` 两个值，
`prompts._trigger_suffix` 也只在 `interval` 时追加一句条件化措辞。模型无法区分「新 K 线刚收、信号板刚
重算」「上一轮 LLM 还在跑、这根 K 线的首次研判被推迟到现在」「价格刚走出一个 1 倍 ATR 的异动」
「你有一笔限价单挂了 4 分钟还没成交」「什么都没发生，只是心跳」——这五种局面对应完全不同的注意力
分配，却共用一句话。

### 1.2 方案要旨

本方案沿两条正交的轴改造决策输入，**不改动任何下单、风控、门禁与保护层判定**：

**轴 A · 战术周期证据（用户诉求第 1 条）**。在既有「中线背景（高于执行周期：1H/4H/12H/1D）」与
「执行周期（`bar` 自身的体制 + 证据摘要）」之间，补上缺失的第三层：**战术周期**——严格短于执行周期、
且时间尺度最贴近唤醒节奏的 1~2 个周期。选取规则是纯函数：唤醒 3 分钟 + 执行 30m ⇒ 战术周期 =
`("1m", "5m")`，与用户描述逐字一致。每个战术周期复用**同一套**指标真源（`market_context.build_market_context`
+ `market_digest.build_market_digest`），额外给出「自上一决策轮以来收了几根、涨跌多少、折合几倍 ATR」，
这正是重复轮里唯一真实变化的那部分信息。

**轴 B · 唤醒原因（用户诉求第 2 条）**。把 `trigger` 的二值扩成一个**结构化唤醒对象** `wake`：七种
`kind`（`candle_close` / `bar_advanced` / `resting_order` / `profit_tier` / `price_breakout` /
`position_watch` / `heartbeat`）按固定优先级判定，附带 `alsoTrue` 列出全部同时成立的条件（判定只决定
标签，绝不丢证据）、`sinceLastRoundS`、`priceMovePct`、`priceMoveAtr`。它进 prompt（让 Agent 知道）、进
`decision` 事件与 `agent_trace`（让人在轨迹面板上知道）、进 `trades.explain_json.meta.wakeKind`
（让事后归因能按唤醒来源切开胜率）。`heartbeat` 这个 kind 同时成为 F4c 跳过守卫的**唯一判据**——守卫
的阈值从写死的 0.05% 改为「`priceMoveAtr < ai_trade_wake_move_atr_mult`（默认 0.5）」，ATR 不可用时
逐字回退现有 pct 判据。判定与措辞从此同源，杜绝「prompt 说数据没变、守卫却认为变了」。

**贯穿两轴的约束 · 渐进式上下文披露**。新增证据不得让 prompt 单调膨胀。执行周期段（中线背景 + 证据
摘要）在**重复轮**（同一根执行根的第 2 次及以后决策）降级为压缩形态：中线背景只留「整体一致性 +
候选支撑/阻力」表头，证据摘要压成一行；战术周期段自身也分两档——默认 `brief`（每周期一行），
仅当本轮「有料」（唤醒原因非心跳 / 持仓中 / 战术与执行周期体制背离 / 出现超买超卖或异常放量）
才升 `full`（每周期两行）。**[评审 P1-7]** 预算口径统一为 §6.3 的 A-4/A-4b 三条硬指标：`brief` 档
≤ 今日 100%、重复轮 ≤ 115%、`full` 轮 ≤ 135%——三档各自有上限，杜绝「只管重复轮、首轮任意膨胀」。

### 1.3 目标 / 非目标

**目标**
1. 定时唤醒轮拿到的行情证据**真的不同于**上一轮：战术周期 K 线 + 指标 + 自上轮增量。
2. 模型每轮明确知道自己为什么被叫醒，以及这次唤醒「值不值得动手」。
3. 唤醒来源可事后归因：每笔 AI 成交带 `wakeKind`。
4. 上下文预算受控：分档披露 + 硬字符上限 + 重复轮压缩。
5. 全部新增能力可经开关**逐字节回退**到今日行为（G-1 冻结门禁作证）。

**非目标（明确不做，避免 Subtask #2 扩张）**
- **不做**诊断书「办法五」的信号驱动唤醒（未确认根上预演影子 broker + 防抖）。它需要影子 broker 的
  无副作用预演模式，是独立开发项，本轮不碰。
- **不做**诊断书「办法四」的完整补跑队列。本轮只做**可观测**部分：被 `_deciding` 丢弃的 K 线轮在
  `decision{skipped}` 事件里如实标注，且下一次定时唤醒会被判定为 `bar_advanced`（「补看新 K 线」）
  并在 prompt 中如实告知模型「这根 K 线是第一次被研判」。真正的立即补跑留待后续。
- **不改**任何下单 / 风控判定：铁律层、edge 门禁、仓位层、利润保护层、反思层的输入输出与调用顺序
  逐字不变。本方案只在 payload 里**加键**、在 prompt 里**加段**。
- **不接**AI 回测（`backtest.py`）。回测引擎只有执行周期的 OHLCV，注入战术周期需要引擎不具备的
  日内细粒度数据，强行注入等于制造未来函数；回测 payload / prompt 保持逐字节不变（G-2）。
- **不加**数据库表 / 列 / 迁移。`wakeKind` 走既有 `trades.explain_json` 的 `meta` 自由字典。

---

## 二、技术设计

### 2.1 三层时间尺度与选取策略（新模块 `ai_auto/tactical_context.py`）

```
中线背景（已有 multi_timeframe_context）   1D  12H  4H  1H     ← 严格高于执行周期
执行周期（已有 market_context/digest）           30m           ← 用户所选 bar
战术周期（本方案新增 tactical_context）      5m   1m           ← 严格低于执行周期，贴唤醒节奏
```

选取纯函数（同文件，无 IO、无 settings 之外的隐藏输入）：

```python
_TACTICAL_LADDER = ("1m", "5m", "15m", "30m", "1H", "4H", "12H")   # 可 REST 取数的候选梯子（升序）
# [评审 P2-6] 含 12H：它在 BAR_SECONDS 里、既有 MTF policy 也在用它、REST 也能取；缺它会让 1D
# 执行周期从 4H 直接跳到 1D。刻意**不含** 1D 及以上：战术层的定义是「严格短于执行周期」。

def tactical_timeframes(execution_bar: str, wake_interval_s: float,
                        max_bars: int = 2) -> tuple[str, ...]:
    """按「最贴近唤醒节奏」选战术周期，升序返回，全部严格短于执行周期。"""
    if execution_bar not in BAR_TO_WS_CHANNEL:
        raise ValueError(f"unsupported AI execution bar: {execution_bar}")
    if max_bars <= 0:
        return ()
    exec_s = BAR_SECONDS[execution_bar]
    candidates = [b for b in _TACTICAL_LADDER if BAR_SECONDS[b] < exec_s]
    if not candidates:
        return ()
    wake_s = wake_interval_s if wake_interval_s > 0 else exec_s
    coarse = next((i for i, b in enumerate(candidates) if BAR_SECONDS[b] >= wake_s),
                  len(candidates) - 1)
    return tuple(candidates[max(0, coarse - (max_bars - 1)): coarse + 1])
```

判定口径：`coarse` = **第一个不短于唤醒周期**的候选（「最接近的时间维度」），再向下取 `max_bars-1` 个
更细周期；无候选满足时退到最粗候选。全部取值必须能被下表反算：

| 执行周期 | 唤醒周期 | candidates | coarse | 结果 | 说明 |
|---|---|---|---|---|---|
| 30m | 3min (180s) | 1m/5m/15m | 5m | `("1m","5m")` | **用户原话的用例**，逐字命中 |
| 15m | 2min (120s) | 1m/5m | 5m | `("1m","5m")` | 默认唤醒周期下的常见配置 |
| 1H | 5min (300s) | 1m/5m/15m/30m | 5m | `("1m","5m")` | |
| 30m | 20min (1200s) | 1m/5m/15m | 15m（无≥1200s，退最粗） | `("5m","15m")` | 慢唤醒 ⇒ 战术周期同步变粗 |
| 30m | 0（关定时唤醒） | 1m/5m/15m | 15m（`wake_s`=1800 退最粗） | `("5m","15m")` | 只有 K 线轮，仍给根内结构 |
| 5m | 3min | 1m | 1m（无≥180s，退最粗） | `("1m",)` | |
| 1m | 任意 | ∅ | — | `()` | 天然关闭，整段省略 |
| 4H | 2min (120s) | 1m/5m/15m/30m/1H | 5m | `("1m","5m")` | **[评审 P2-6]** 4H 是合法可选执行周期（`BARS` 含它） |
| 1D | 2min (120s) | 1m/…/4H/12H | 5m | `("1m","5m")` | 同上；`12H` 已入梯子，慢唤醒时才会被选中 |
| 1D | 60min (3600s) | 1m/…/4H/12H | 1H | `("30m","1H")` | 慢唤醒 ⇒ 战术周期同步变粗（12H 仍不入选，因 coarse 落在 1H） |

战术周期与既有 `_htf_bars`（严格高于执行周期）在集合上**不可能相交**（`_htf_bars` 只收
`BAR_SECONDS[tf] > base_seconds`，`market_window.py:45-50`；战术周期严格 `< exec_s`），不存在窗口争用。

**[评审 P1-1] 唯一真源纪律**：`tactical_timeframes` 在一个会话内**只被调用一次**——由 `WakeRuntime.__init__`
调用并缓存；`MarketWindow` **不得**自行调用它，只能接收已解析好的 `tuple`（见 §2.2 的 `WindowOptions`）。
理由与本仓库的「估计量同源律」一致：两处各算一次，一旦某处的 `wake_interval_s` 口径漂移（例如一处
用秒、一处用分钟），会出现「窗口在取 1m/5m，prompt 却宣称在看 5m/15m」这类无声失真。

### 2.2 战术窗口取数（`ai_auto/market_window.py` 扩展）

镜像既有 `prefill_htf` / `maybe_refresh_htf` 的「锚点刷新」范式。

**[评审 P1-1] 形参收敛（CLAUDE.md ≤5）**：`MarketWindow.__init__` 今日已占满 5 个形参
（`inst_id, bar, specs, priming_bars, *, context_enabled`，`market_window.py:27`），再加战术参数必然破规，
且它拿不到 `wake_interval_s`。故把可选装配项聚合成一个冻结 dataclass（同文件）：

```python
@dataclass(frozen=True)
class WindowOptions:
    """MarketWindow 的可选装配项（聚合结构体，守 CLAUDE.md「形参 ≤5」）。"""
    context_enabled: bool = True
    tactical_bars: tuple[str, ...] = ()      # 由 WakeRuntime 解析后**传入**（唯一真源，窗口不自算）
    tactical_window_bars: int = 120
    tactical_min_refresh_s: float = 20.0
    tactical_fetch_timeout_s: float = 8.0

class MarketWindow:
    def __init__(self, inst_id: str, bar: str, specs: list[PaperStrategySpec],
                 priming_bars: int, *, options: WindowOptions = WindowOptions()):
        ...
        self._opt = options
        self._context_bars = decision_timeframes(bar) if options.context_enabled else ()
        self._tactical_bars = options.tactical_bars
```

`context_enabled` 的两处既有调用点必须同步改写（**否则 TypeError**）：`session.py:174` 与
`manager.py:595`（`grep -rn "context_enabled" --include=*.py` 实测只有这两处 + 定义处）；
`test_ai_trade_market_window.py:23` 的夹具不传该 kwarg，天然不受影响。

新增成员：

- `self._tactical_bars: tuple[str, ...]`（来自 `options`，不自算）。
- `self._tactical_windows: dict[str, deque]`，每个 `maxlen = options.tactical_window_bars`（默认 120）。
  刻意**不复用** `_window_max`（它是多策略并集上界，最大 1200 根），战术窗口只服务指标计算，
  120 根已 ≥ `market_digest._MIN_BARS=35` 且 ≥ `classify_regime` 的 60。
- `self._tactical_next_refresh_ts: dict[str, int]`、`self._tactical_last_fetch_wall: dict[str, float]`。

```python
async def prefill_tactical(self) -> None:
    """启动时一次性预填各战术周期窗口（取数失败留空，安全降级，绝不阻塞暖机）。

    [评审 P1-5] limit = min(300, tactical_window_bars + 5)。**300 是硬上限**：`get_candles` 在
    `limit<=300` 且无游标时走单发 `/market/candles`，超过即改走 history-candles **翻页**
    （`rest_client.py:155`），一次预填可能打出 4~5 个请求，直接违背 §7 R-2 的配额目标。
    """

async def maybe_refresh_tactical(self, now_ts: int, now_wall: float) -> None:
    """按交易所报出的收线锚点刷新；失败不推进锚点（下 tick 重试）。

    三闸门（缺一不可，按序判定）：
    ① 锚点闸：`now_ts < _tactical_next_refresh_ts[b]` ⇒ 跳过（正常节奏 = 每收一根请求一次）；
    ② 配额闸：`now_wall - _tactical_last_fetch_wall[b] < min_refresh_s` ⇒ 跳过。这一闸是硬护栏，
       即便锚点算错也不会让 5 秒盯市把 REST 打成每 5 秒一发（多标的分组会直接撞 429，本仓库
       `docs/diagnoses/live-auto-multi-symbol-rate-limit-429` 有前科）。
    ③ **[评审 P1-4] 单 tick 节流闸**：一次调用**最多刷新一个**战术周期（过完前两闸的候选按
       `BAR_SECONDS` 升序取第一个，其余留给下一 tick）。理由见下方「盯市延迟」。

    [评审 P1-5] 刷新 limit = `min(300, max(20, int(min_refresh_s // BAR_SECONDS[b]) + 10))`
    ——只需覆盖「上次刷新以来可能新收的根数」加余量；入窗按 `ts` 去重覆盖，与 `prefill_htf` 的
    `existing` 集合同姿态，故 limit 偏小只会少补历史、绝不会造成窗口空洞或重复根。
    """
```

**[评审 P1-4] 盯市延迟护栏（真实资金路径）**：`_monitor_loop` 是 `await self._monitor_tick()` 串行驱动
（`session.py:395`），tick 内部亦串行（:399-418）。REST 单次 `timeout=httpx.Timeout(20.0)` 且
`retries: int = 3`（`rest_client.py:85,96`）⇒ 单周期最坏 ~60s。若一次 tick 里刷 2 个周期，最坏 ~120s
不返回——期间**利润保护棘轮、会话止盈止损、staleness 判定全部停摆**，而它们守的是真实持仓。故：

1. 闸③把单 tick 的取数上界钉在 1 次；
2. 战术取数**必须**再套一层硬超时：
   `await asyncio.wait_for(self._safe_get_candles(b, limit), self._opt.tactical_fetch_timeout_s)`
   ——`TimeoutError` 是 `Exception` 子类，落进 `_safe_get_candles` 既有的 `except Exception` 降级路径
   （返回 `None` ⇒ 不推进锚点 ⇒ 下 tick 重试），无需新错误分支。默认 8s ⇒ 单 tick 最坏增量 8s，
   仍在 5 秒盯市可容忍的抖动量级内，且不会与 `live_auto_staleness_factor` 的判定打架。

入窗只收 `getattr(c, "confirm", True)` 为真的根（已核实 OKX `/market/candles` 会把**未收线的当前根**
作为最新一条返回、`rest_client.py:157` 不过滤，收进来会让指标 repaint——这正是本方案最需要避免的失真）。
稳态请求量：每个战术周期每收线一次一发，`("1m","5m")` ⇒ 1.2 req/min/会话；5 标的分组 ⇒ 6 req/min，
相对 OKX `/market/candles`（40 req/2s per IP）可忽略。

`tactical_frames() -> dict[str, pd.DataFrame]` 镜像 `context_frames()`，返回 `candles_to_df` 拷贝。

**调用点（共 3 处，顺序敏感）**：
1. `session._prime()`：`await self._window.prefill_tactical()`（紧随 `prefill_htf` 之后）。
2. `session._monitor_tick()`：`await self._window.maybe_refresh_tactical(now_ts, time.time())`
   **必须放在 `await self._maybe_interval_wake()` 之前**——否则定时唤醒轮拿到的是上一分钟的战术数据，
   整个特性在最该生效的路径上空转。
3. `session.feed_candle()`：紧随既有 `await self._window.maybe_refresh_htf(decision_as_of_ts)` 之后
   追加一行 `await self._window.maybe_refresh_tactical(decision_as_of_ts, time.time())`（锚点闸使其
   在盯市已刷新时为零成本空转）。

### 2.3 战术证据装配（`ai_auto/tactical_context.py` 主函数）

#### 2.3.0 决策瞬刻的单一真源（**[评审 P0-1]，阻断级，务必先读**）

会话里存在**两个时钟**，且既有代码在两条唤醒路径上给 `_run_decision_round` 的 `ts` 是不同语义的：

| 唤醒路径 | 传入的 `ts` | 语义 | 代码位置 |
|---|---|---|---|
| K 线轮 | `int(row["ts"])` | 该确认根的**开盘**时刻 | `session.py:556` |
| 定时轮 | `int(now)` | **墙钟**当前时刻 | `session.py:455` |

既有 MTF 无差别地取 `as_of_ts = ts + BAR_SECONDS[self.bar]`（`session.py:1188`）。这个表达式**只对 K 线轮
正确**（根开盘 + 一根 = 该根收盘 = 决策瞬刻）；在定时轮它等于「现在 + 一整根」，是一个**未来时刻**。
把它喂给战术段会直接毁掉本特性：`ageSeconds = as_of_ts - close_ts`（`multi_timeframe_context.py:95`）
凭空多出一整根执行周期的量，而 `stale = age >= 2 * BAR_SECONDS[bar]`（:97）对 1m 只有 120 秒、5m 只有
600 秒 ⇒ **每个定时轮的每个战术周期都会被标成「数据陈旧」**。而系统 prompt 决策纪律 #2 明写
「陈旧或缺失周期不得补猜」（`prompts.py:125`）——等于我们花了一整个特性把新证据取回来，再亲手命令模型
忽略它。反过来若传裸 `ts`，K 线轮的 `as_of_ts` 变成根**开盘**时刻，会把整根执行周期内的战术根全部裁掉。

故本方案钉死一条纪律：**决策瞬刻只有一个真源，按 trigger 分支求得一次，同时喂 MTF 与战术段。**

```python
# session.py（_run_decision_round 内，装配 payload 之前求一次）
def _decision_as_of_ts(self, ts: int, wake: dict) -> int:
    """本轮决策瞬刻（epoch 秒，墙钟口径）：K 线轮=该根收盘时刻；定时轮=ts 本身（已是墙钟）。"""
    if wake.get("trigger") == "candle":
        return ts + BAR_SECONDS[self.bar]
    return ts
```

- 战术段与 `changeSinceLastRound` 一律用它。
- **既有 MTF 的调用刻意保持原样**（`_build_multi_timeframe_context(ts, ref_px)` 内部仍是
  `ts + BAR_SECONDS[bar]`）——它是本方案**范围外**的存量偏差，且 MTF 最细周期是 1H（stale 阈值 7200s），
  1800s 的偏差通常不足以翻转它的 `stale`。改它会动 G-1 的冻结基线，不在本轮做；已在 §7 R-13 留档。
- 单测必须钉住这条：见 §6.1 T-19。

#### 2.3.1 主函数

```python
def build_tactical_context(*, execution_bar: str, tactical_bars: tuple[str, ...],
                           frames: dict[str, pd.DataFrame], as_of_ts: int,
                           last_px: float, reference: dict | None,
                           settings: Settings) -> dict | None:
```

- 逐周期走 `_causal_frame`（**逐字复制** `multi_timeframe_context._causal_frame` 的因果裁剪：数值化 →
  丢非有限值 → `ts + BAR_SECONDS[bar] <= as_of_ts` → 排序去重），再喂 `build_market_context` /
  `build_market_digest`。单周期异常 `except Exception -> None`（整周期省略），与 MTF 同姿态：一个坏
  周期绝不拖垮决策轮。
- `as_of_ts` **必须**是 §2.3.0 的 `_decision_as_of_ts` 产物（墙钟口径的决策瞬刻）。
- `reference`（可为 `None`，首轮无参照）：`{"px": float, "asOfTs": int, "atrPx": float | None}`，
  取自 `WakeRuntime` 记录的上一决策轮断面。**[评审 P0-1]** 第二个字段刻意是上一轮的 `asOfTs`（与本轮
  `as_of_ts` **同一时钟**），不是 `time.time()` ——混钟会让 K 线轮与定时轮交替时 `closedBars` 系统性偏
  一根。据它算：
  - 顶层 `changeSinceLastRound = {"pct": …, "atrMult": … | None, "atrBar": execution_bar}`
    **[评审 P2-3]** 上提到顶层：`pct = (last_px / reference["px"] - 1) * 100` 与
    `atrMult = abs(last_px - reference["px"]) / atrPx` 都只由 `last_px` 与 `reference` 决定，**与周期无关**，
    逐周期重复渲染同一个数是纯 token 浪费。`atrBar` 显式声明这个 ATR 是**执行周期**的（`_atr_px` 来自
    `safe_atr(self._window.base_df(), …)`，`session.py:548`），文案必须写成「≈0.91×30m ATR」而非
    「≈0.91×ATR」，否则模型会误读为战术周期自身的 ATR。`atrPx` 缺失或 ≤0 ⇒ `atrMult=None`，绝不假数据。
  - 周期级只保留 `closedBars` = 该周期窗口中 **`ts + BAR_SECONDS[bar] > reference["asOfTs"]`** 的确认
    根数（「上轮之后这个级别**收了**几根」）。**[评审 P0-1]** 判据用**收线时刻**而非开盘 `ts >= wall`：
    后者会漏掉「上轮唤醒时正在走、随后立刻收线」的那一根，在 1m 级别恰好是最有信息量的一根。
- 顶层附 `alignment`：战术周期 `regime.primary` 与执行周期 `regime.primary` 的关系，取值
  `"aligned"`（全部相同）/ `"diverged"`（至少一个不同）/ `"insufficient"`（无可用体制）。
  注（P2-2）：`regime` 由 `build_market_context` 产出，而它受**另一个**总闸 `ai_trade_regime_context`
  管制（`market_context.py:33`）；该闸关闭时 `alignment` 恒 `"insufficient"`，分档判据第 3 条恒假——
  这是可接受的降级（其余三条判据仍生效），但必须在 §4.2 表格与单测里如实标注，不得当成 bug 追。
- 返回：`ai_trade_tactical_context=False` / `tactical_bars` 为空 / 全部周期不可用 ⇒ `None`
  （prompt 整段省略，逐字节回退）。

**披露分档**（同文件纯函数，判定与渲染分离）：

```python
def disclosure_tier(*, wake_kind: str, position_side: str,
                    execution_primary: str | None, context: dict) -> str:
    """"full" 当且仅当以下任一成立，否则 "brief"。"""
```
1. `wake_kind` ∈ `{"candle_close", "bar_advanced", "price_breakout", "profit_tier", "resting_order"}`；
2. `position_side != "flat"`（真金白银在场，多看一眼的钱值得花）；
3. `context["alignment"] == "diverged"`（战术与执行周期体制背离——最需要展开的一种局面）；
4. 任一战术周期 `digest.factors.rsi14.label ∈ {"overbought","oversold"}` 或 `digest.volume.spike` 为真。

即：只有「空仓 + 心跳 / 持仓盯市 + 战术与执行同向 + 无极端读数」这种确实没料的轮次才走 `brief`。

**执行周期段的披露档**（同文件，服务「渐进式披露」的另一半）：

```python
def execution_disclosure(*, wake_kind: str, enabled: bool) -> str:
    """"full"：wake_kind ∈ {"candle_close","bar_advanced"} 或 enabled=False；否则 "repeat"。"""
```
语义：执行周期的证据只在「这根 K 线第一次被研判」时全量披露；同一根的后续唤醒一律压缩。

### 2.4 唤醒原因分类（新模块 `ai_auto/wake_reason.py`，纯函数）

```python
WAKE_KINDS = ("candle_close", "bar_advanced", "resting_order", "profit_tier",
              "price_breakout", "position_watch", "heartbeat")

@dataclass(frozen=True)
class WakeFacts:
    trigger: str                 # "candle" | "interval"（既有二值，wire 不变）
    position_side: str           # executor.projected
    has_resting: bool
    bar_index: int
    last_round_bar_index: int | None
    px: float | None
    last_round_px: float | None
    atr_px: float | None
    guard_status: str | None         # 本 tick 的利润保护快照 status
    last_round_guard_status: str | None
    since_last_round_s: float
    resting_age_s: float | None

# [评审 P1-6] `WakeFacts` 只由 `WakeRuntime.classify` 组装：`guard_status` / `atr_px` 由运行时从
# `note_guard_status` / `note_atr` 记下的值**回读**，`last_round_*` 由 `note_round_start` 冻结的断面
# 提供。调用方（session）只喂 executor 与窗口的现成读数（`projected` / `resting` / `bar_index` / `px`），
# **不得**自行再取一次保护快照或 ATR——两处各取一次是本仓库反复踩过的漂移源。

def classify_wake(facts: WakeFacts, *, move_atr_mult: float, move_pct: float,
                  horizon_scale: float = 1.0) -> dict:
```

判定顺序（**首个命中即为 `kind`**，其余成立项进 `alsoTrue`，绝不丢证据）：

| # | kind | 判据 |
|---|---|---|
| 1 | `candle_close` | `trigger == "candle"` |
| 2 | `bar_advanced` | `last_round_bar_index is None or bar_index != last_round_bar_index`（定时轮却发现执行根已推进 ⇒ 上一轮 K 线决策曾被 `_deciding` 丢弃，本轮是该根首次研判） |
| 3 | `resting_order` | `has_resting` |
| 4 | `profit_tier` | `position_side != "flat"` 且 `guard_status != last_round_guard_status` 且 `guard_status ∈ ARMED_STATUSES`（新进入 armed / breakeven_armed ⇒ 浮盈跨档） |
| 5 | `price_breakout` | `moved()` 为真（见下） |
| 6 | `position_watch` | `position_side != "flat"` |
| 7 | `heartbeat` | 兜底（空仓 + 无挂单 + 无新根 + 未越阈） |

#### 2.4.1 ATR 判据的时间尺度折算（**[评审 P0-2]，阻断级**）

原方案直接用「`|Δpx| >= 0.5 × ATR(执行周期)`」，这在量纲上是错的：**ATR 度量的是「执行周期一根」的
典型波幅，而 `Δpx` 度量的是「一个唤醒间隔」内的位移**，两者的时间跨度可以差三个数量级。后果不是精度
问题，而是功能被静默关掉：

| 执行周期 | 唤醒 | `0.5×ATR` 折合价格位移（BTC 典型 ATR%） | 结果 |
|---|---|---|---|
| 30m | 3min | ≈0.18%（ATR≈0.35%） | 比今日 0.05% 严 3.5 倍，跳过率大涨 |
| 4H | 2min | ≈0.6%（ATR≈1.2%） | 2 分钟内几乎不可能 ⇒ 空仓定时唤醒**近乎全跳** |
| 1D | 2min | ≈1.5%（ATR≈3%） | 2 分钟内**不可能** ⇒ 空仓定时唤醒**永久跳过** |

4H / 1D 是**合法可选**的执行周期（`AiTradeLaunchModal.tsx:69` 的 `BARS`，且 `AiTradeLaunchModal.test.tsx:250`
已断言 4H 可选），所以这不是理论边角。更糟的是它与轴 A **自相矛盾**：战术证据存在的全部理由就是让
定时轮拿到新东西，而这一改会先把定时轮本身取消掉。

修复（纯函数，同文件）：按随机游走的 √t 律把执行周期 ATR 折算到唤醒窗口的时间尺度。

```python
def horizon_scale(wake_interval_s: float, bar_seconds: int) -> float:
    """把「执行周期一根的 ATR」折算到「一个唤醒间隔」的时间尺度（随机游走 √t 律）。

    wake >= bar ⇒ 1.0（**只缩不放**：唤醒比 K 线还慢时不该让阈值超过一根的典型波幅）；
    任一入参非正 ⇒ 1.0（退化为原始 ATR 判据，与 fail-open 极性一致）。
    """
    if wake_interval_s <= 0 or bar_seconds <= 0:
        return 1.0
    return math.sqrt(min(1.0, wake_interval_s / bar_seconds))
```

校准（`move_atr_mult=0.5`）：30m/3min ⇒ `scale=0.316` ⇒ 有效判据 `0.158×ATR`；1D/2min ⇒ `scale=0.037`
⇒ `0.019×ATR`。两者折合的价格位移都落在今日固定 0.05% 的**同一量级**，但阈值从此**随波动率自适应**
（静止行情 ATR 小 ⇒ 阈值小、剧烈行情 ATR 大 ⇒ 阈值大）——这正是诊断书办法一想要的性质，而原写法
在追求这个性质时把绝对水位抬高了 1~2 个数量级。

`moved()` 的**单一真源**（同时供守卫与措辞使用；**[评审 P0-2]** 签名改为吃 `facts` 以守
CLAUDE.md「形参 ≤5」——原 5 个位置参数再加 `horizon_scale` 就是 6 个）：

```python
def moved(facts: WakeFacts, *, move_atr_mult: float, move_pct: float,
          horizon_scale: float = 1.0) -> tuple[bool, float | None, float | None]:
    """返回 (是否越阈, 相对变动百分数, 折合 ATR 倍数——**未**经 scale 折算的原始倍数)。

    价不可信（px / last_round_px 任一 None 或 <=0）⇒ (True, None, None)：价拿不准时宁可唤醒
    （与 F4c 现行极性逐字一致）。
    ATR 可用（atr_px 有值且 >0）且 move_atr_mult > 0 ⇒
        判据 = |px - last| >= move_atr_mult * atr_px * horizon_scale（诊断书办法一 + 尺度折算）。
    否则 ⇒ 逐字回退既有 pct 判据 |px/last - 1| >= move_pct（与 should_skip_interval_wake 同式）。

    返回的第三元刻意是**原始** |Δpx|/atr_px：它是给人和模型看的物理量（「走了 0.91 根 30m ATR」），
    折算只发生在判据侧。两者都随 wire 下发，读者能自行反算门槛，杜绝「静默门禁」。
    """
```

产物 wire（camelCase，进 payload / 事件 / 轨迹）：

```python
{"kind": "price_breakout", "trigger": "interval", "sinceLastRoundS": 182,
 "priceMovePct": 0.42, "priceMoveAtr": 0.91, "barAdvanced": False,
 "alsoTrue": ["resting_order"], "restingAgeS": 240.0,
 "moveThresholdAtr": 0.5, "moveThresholdPct": 0.0005,
 "horizonScale": 0.316, "moveThresholdAtrEff": 0.158,   # [评审 P0-2] 折算后的实际门槛
 "skipStreak": 0}                                        # [评审 P0-2] 连续跳过计数（饿死兜底可观测）
```

阈值随 wire 下发是刻意的：静默的门禁在本仓库有前科，用户必须能看到「判据是多少、实际是多少」。
`horizonScale` / `moveThresholdAtrEff` 必须一起下发——只发 `moveThresholdAtr=0.5` 而实际按 0.158 判，
就是新造一次「说的和做的不一致」。

### 2.5 跳过守卫收敛到同一判据（`ai_auto/wake_guard.py`）

`should_skip_interval_wake` 的既有签名与语义**一字不改**（其单测继续绿），新增一个**并列**的薄函数：

```python
def should_skip_wake(wake: dict, *, threshold_pct: float, threshold_atr_mult: float,
                     max_skip_streak: int = 0) -> bool:
    """True 当且仅当守卫开启、`wake["kind"] == "heartbeat"`、且未触到连续跳过上限。

    守卫开启 = `threshold_pct > 0 or threshold_atr_mult > 0`（任一为正即开；两者皆 0 = 关守卫，
    恒不跳过，逐字回退「每次到点都叫」的最早行为）。判据不在本函数里重算——`heartbeat` 这个
    kind 本身就是「空仓 + 无挂单 + 无新根 + 未越阈」的合取，`classify_wake` 已经算过一次。
    两处各算一次是「prompt 说没变、守卫认为变了」这类信任崩塌的经典成因。

    [评审 P0-2] 饿死兜底：`max_skip_streak > 0 and wake["skipStreak"] >= max_skip_streak` ⇒ 返回
    False（强制放行一轮）。ATR 判据把跳过率显著推高后，需要一条**上界**保证模型在长时间静止行情里
    不被完全饿死（否则「AI 自动交易」会在盘整期变成事实上的停机，而用户看不到任何原因）。放行轮的
    `kind` 仍如实是 `heartbeat`——文案条件化措辞正是为这种轮次写的，不需要新 kind。
    """
```

**[评审 P0-2] `skipStreak` 的计数口径（易踩点）**：它**只能**在既有 `_count_wake_skip` 返回 `True` 时
自增（即与 `wake_skips` 共用 R-02 那套「每唤醒周期至多计一次」的节流），并在任一决策轮启动时归零。
若按 monitor tick 裸计数，5 秒 tick 会在一个 3 分钟唤醒周期内把它打到 36，`max_skip_streak=5` 会
立刻退化成「每 25 秒强制唤醒一次」——比不做兜底更糟。

`session._maybe_interval_wake` 改为：先 `classify_wake`（纯同步）→ `should_skip_wake` → 命中则计数并
返回；未命中则**同步认领 `_deciding`**（check→set 之间仍无 `await`，P1-2 范式不破）并把 `wake` 对象
透传进决策轮。

### 2.6 会话侧下沉（新模块 `ai_auto/wake_runtime.py`）

`session.py` 已 1494 行、远超本仓库 800 行红线（`ai-trade-profit-protection` 的 D-6 已记欠账）。故本方案
**不往 session.py 加逻辑**，一律下沉到 `WakeRuntime`，镜像 `ProfitGuardRuntime` / `CostRuntime` /
`PriorRuntime` / `SizingRuntime` 的既有范式：

```python
class WakeRuntime:
    """唤醒判定 + 战术证据装配的会话级门面（纯同步；网络 IO 全在 MarketWindow）。"""

    def __init__(self, execution_bar: str, wake_interval_s: float, settings: Settings): ...

    @property
    def tactical_bars(self) -> tuple[str, ...]: ...
    @property
    def tactical_bars_text(self) -> str:        # "5m → 1m"（system prompt 用；空 ⇒ ""）
    # [评审 P1-6] 以下两个只读属性是 §2.9 归因与 §2.3 参照的公开面，原稿漏声明：
    @property
    def last_round_kind(self) -> str | None: ...   # 上一决策轮的 wake kind（供 _inject_explain_meta）
    @property
    def last_reference(self) -> dict | None: ...   # {"px","asOfTs","atrPx"}；None=尚无决策轮

    def classify(self, facts: WakeFacts) -> dict: ...          # → wake wire（内部带 horizon_scale）
    def should_skip(self, wake: dict) -> bool: ...             # → wake_guard.should_skip_wake
    def note_skip(self, counted: bool) -> None:
        """[评审 P0-2] 跳过计数：`counted=True`（既有 `_count_wake_skip` 的返回值）才 +1；
        任一决策轮启动即归零。口径与 `wake_skips` 完全一致，见 §2.5 的易踩点说明。"""
    def note_round_start(self, wake: dict, *, px, bar_index, as_of_ts) -> None:
        """冻结本决策轮断面，供下一轮做参照（reference 供 changeSinceLastRound；kind 供归因）。

        [评审 P0-1] 第四个参数是 `as_of_ts`（§2.3.0 的决策瞬刻）而非 `wall=time.time()`——参照与
        本轮 `as_of_ts` 必须同钟，否则 K 线轮/定时轮交替时 `closedBars` 会系统性偏一根。
        [评审 P1-6] `guard_status` 不再由调用方传入：本方法从 `note_guard_status` 记下的**本 tick
        快照值**回读并冻结为 `_last_round_guard_status`，保证「分类看到的」与「冻结的」是同一个数。
        `atrPx` 同理由 `note_atr` 记录（会话在确认根上刷新 `_atr_px` 时顺手告知）。
        """
    def note_guard_status(self, status: str | None) -> None:
        """[评审 P1-6] 利润保护快照 status 的**唯一入口**（盯市 tick 每次 `_refresh_profit_state`
        之后即记）。`WakeFacts.guard_status` 必须由本运行时回读，调用方**不得**另取一次——
        `_maybe_profit_exit`（session.py:414）已在 `_maybe_interval_wake`（:418）之前跑过，
        故 tick 上恒有新鲜值可读；两处各取一次是本仓库反复踩过的漂移源。"""
    def note_atr(self, atr_px: float | None) -> None: ...      # 确认根刷新 ATR 时同步告知
    def inject(self, payload: dict, *, wake: dict, frames, as_of_ts: int,
               last_px: float, position_side: str, execution_primary: str | None) -> None:
        """写 payload["wake"] / ["tacticalContext"] / ["tacticalTier"] / ["contextDisclosure"]。

        总闸关闭 ⇒ **整键缺席**（不是 None），与 baseRules / profitGuard / sizingPlan 同款范式，
        使 prompt 渲染与既有 payload 冻结断言逐字节回退。**[评审 P2-8]** 三个总闸各管自己的键：
        `ai_trade_wake_reason_enabled=False` ⇒ 无 `wake`；`ai_trade_tactical_context=False`（或无候选
        周期 / 全周期不可用）⇒ 无 `tacticalContext` 与 `tacticalTier`；
        `ai_trade_progressive_disclosure=False` ⇒ 无 `contextDisclosure`（渲染恒 full）。
        `as_of_ts` 必须是 §2.3.0 的 `_decision_as_of_ts` 产物。
        """
```

`AiTradeSession.__init__` 只加一行 `self._wake = WakeRuntime(bar, wake_interval_s, executor.settings)`。
**[评审 P1-1] 构造顺序**：这一行必须落在 `self._window = MarketWindow(...)`（`session.py:169`）**之前**
——窗口的 `WindowOptions.tactical_bars` 要吃 `self._wake.tactical_bars`，战术集合的唯一真源在此。

### 2.7 决策轮参数收敛（`session.py` 的两处签名调整）

CLAUDE.md 规定形参 ≤5。`_run_decision_round(self, board, i, ref_px, ts, trigger)` 已占满，
`_build_decision_payload(self, round_no, ts, ref_px, board, trigger)` 亦然。故**用 `wake` 替换 `trigger`**
（`wake["trigger"]` 承载原值，信息不丢、参数不增）：

```python
async def _run_decision_round(self, board, i, ref_px, ts, wake: dict) -> None
def _build_decision_payload(self, round_no, ts, ref_px, board, wake: dict) -> dict
```

`_build_decision_payload` 内：`payload["trigger"] = wake["trigger"]`（wire 逐字不变）+
`self._wake.inject(payload, wake=wake, as_of_ts=self._decision_as_of_ts(ts, wake), …)`。
**[评审 P0-1]** `_decision_as_of_ts` 是 §2.3.0 的新私有方法（`self` + 2 形参，不破 ≤5）；它是本轮
决策瞬刻的唯一真源，`frames` 取 `self._window.tactical_frames()`。

`_emit_decision` 同理：把既有关键字参数 `trigger: str = "candle"` **替换**为 `wake: dict | None = None`，
事件内 `"trigger": (wake or {}).get("trigger", "candle")` 与 `"wake": wake`。参数总数不增。

`_emit_decision_skipped`（被 `_deciding` 丢弃的 K 线轮）新增
`"wake": {"kind": "candle_close", "trigger": "candle", "dropped": True}`——问题 ④ 的用户可见面从此
自解释，且下一次定时唤醒必被判为 `bar_advanced`，模型会被明确告知「这根 K 线是第一次被研判」。

### 2.8 prompt 渲染（`ai_auto/wake_text.py` 新模块 + `prompts.py` 薄接线）

`prompts.py` 现 735 行，逼近红线。故渲染主体落新模块 `wake_text.py`（镜像 `profit_guard_text.py` /
`sizing_text.py` 的「文案模块 + 延迟导入」先例），`prompts.py` 只加三个薄壳与接线（约 +38 行 → 773 行）。
**若实施时 prompts.py 超过 790 行，把既有 `_format_multi_timeframe*` 三个函数整体搬进新建
`market_text.py` 并延迟导入**（这是给下游的确定性逃生口，不必再决策）。

`wake_text.py` 四个导出：

```python
def format_wake_for_prompt(wake: dict | None) -> str
def format_tactical_for_prompt(context: dict | None, tier: str, *, max_chars: int) -> list[str]
def format_multi_timeframe_repeat(context: dict | None) -> list[str]   # 重复轮的压缩中线背景
def format_digest_repeat(digest: dict | None, bar: str) -> str          # 重复轮的压缩执行周期证据
```

**唤醒原因文案**（拼在决策 prompt 首行之后，替代 `_trigger_suffix`；`wake` 键缺席时 `_trigger_suffix`
原样保留 ⇒ 回测与老 payload 逐字节不变）：

| kind | 文案（示例值可反算） |
|---|---|
| `candle_close` | `｜唤醒原因=新 K 线收盘（30m 确认根落地，信号板已按新根重算）` |
| `bar_advanced` | `｜唤醒原因=补看新 K 线（上一轮研判仍在进行时该根已收线，本轮是它的**首次**研判——不要把上一轮结论当作对这根 K 线的判断）` |
| `resting_order` | `｜唤醒原因=挂单盯守（你有一笔未成交限价委托，已挂 4 分 0 秒；若行情已走反，可用 cancel_order 撤回）` |
| `profit_tier` | `｜唤醒原因=浮盈跨档（保护层状态 idle→armed，移动止盈线已激活）` |
| `price_breakout` | `｜唤醒原因=价格异动（距上轮 3 分 2 秒，价格 +0.42%，约 0.91×30m ATR，已越过 0.16×30m ATR 的唤醒阈值）` |
| `position_watch` | `｜唤醒原因=持仓盯市（距上轮 3 分 2 秒，价格 +0.08%）。退出保护（移动止盈 / 保本 / 交易所侧 OCO）由系统 5 秒盯市层确定性执行，无需你重复确认；你本轮的价值在于判断是否应**提前**离场或反手` |
| `heartbeat` | `｜唤醒原因=心跳复盘（距上轮 3 分 2 秒；无新收线、无挂单、价格变动 0.03% 未达 0.16×30m ATR 阈值——**若**本轮证据确与上轮相同，重复数据下的观望不构成新的独立证据）` |

措辞纪律（沿用 `_trigger_suffix` 的 R-01 教训）：凡断言「数据未变」一律**条件化**；`position_watch`
必须如实说明保护层已接管（否则模型会把「反复确认保护线」当成自己的职责，正是问题 ③）；
`bar_advanced` 必须明说「首次研判」（否则模型会误以为自己已经看过这根）。

**[评审 P0-2 / P2-3] 数值量纲纪律（新增，与判定同源）**：文案里出现的每一个 ATR 倍数都必须带
**周期名**（`0.91×30m ATR`），阈值必须是 `moveThresholdAtrEff`（折算后的实际门槛）而**不是**配置里的
`moveThresholdAtr`。写「0.5×ATR」而实际按 0.158 判，是本仓库最典型的「说的和做的不一致」；写裸「ATR」
会让模型误以为那是它刚看到的战术周期 ATR。文案取值一律从 `wake` 对象直读，不在渲染层重算。

**战术周期段**（`brief` 每周期一行；`full` 追加第二行）：

```
战术周期（贴合 3 分钟唤醒节奏；仅用于择时与确认，不得单独反转中线判断）：
自上一决策轮：价 +0.42%（≈0.91×30m ATR）
- 5m：体制=trend_up，方向=up（近 20 根 +0.42%），RSI14=58(neutral)，量比=1.31，
      自上轮收 1 根，样本=120 根
- 1m：体制=range，方向=flat（近 20 根 +0.03%），RSI14=49(neutral)，量比=0.88，
      自上轮收 3 根，样本=120 根
```
（**[评审 P2-3]** `价 +0.42% / ≈0.91×30m ATR` 与周期无关，故上提为段内一行；周期级只留 `自上轮收 N 根`
这个**真正逐周期不同**的量。原稿逐周期重复同一对数字，既浪费 token 又会让模型以为 1m 与 5m 各有一套
不同的 ATR 口径。）
`full` 追加：`  └ 均线=fast_above（斜率 0.021%），区间位置=0.78，MACD柱=1.42(bullish)，布林%B=0.93(upper_break)，ATR%=0.31(low)，末根 O/H/L/C=…`

段末按需追加：`战术与执行周期体制背离（30m=trend_up，1m=range）：优先以执行周期与中线背景为准，
战术周期只回答「现在是不是好的进场点」。` / `缺失周期=1m；不得补猜。` /
截断时追加 `…（战术周期证据已按 700 字上限截断）`——**绝不静默截断**。

**重复轮压缩**（`payload["contextDisclosure"] == "repeat"` 时生效）：
- 中线背景：只渲染表头行（整体一致性 + 候选支撑 + 候选阻力）+ 一句
  `（各周期明细与本根 K 线首次研判时相同，未重复列出）`；
  **[评审 P2-4]** 但 `context["frames"]` 为空时**原样复用**既有降级行
  `中线背景暂不可用，已回退基础周期；缺失或陈旧周期不得补猜。`（`prompts.py:403`）——此时表头三项
  全是 `None`，压成表头等于输出 `整体=None；候选支撑=None；候选阻力=None` 这种噪音行；
  `context is None` 时仍返回空列表（整段省略）。
- 执行周期证据：三行（价格走势 / 成交量 / 技术因子）压成一行
  `执行周期（30m）证据：与本根首次研判相同（趋势=up，RSI14=58(neutral)，量比=1.31，ATR%=0.42）；本轮最新变化见战术周期`。

**段落顺序**（时间尺度由远及近单调收缩，与系统 prompt 的思维链步骤同序）：
中线背景 → 市场体制 → 共识 → 信号先验 → 执行周期证据摘要 → **战术周期** → 基础策略铁律 → 信号板 → 摘要。
即插在 `build_decision_prompt` 中 `digest` 三行之后、`base_rules_line` 之前。

**系统 prompt**（`build_ai_trade_system_prompt` 末位加形参 `tactical_bars_text: str = ""`，默认空串 ⇒
渲染逐字节不变）。**[评审 P2-1]** 该函数今日已有 **10 个形参**（`prompts.py:70-76`），加本参数为 11，
远超 CLAUDE.md 的 ≤5；本方案在 §2.7 为 `session.py` 严守该阈值，这里却默认破规，属**存量债的延续而非
新债**。Subtask #2 二选一、不得静默：**(a)** 把 `base_rules_text / profit_guard_text / sizing_text /
tactical_bars_text` 四段收进一个 `SystemPromptTexts` 冻结 dataclass（形参回落到 5，一次性还清，代价是
改 `manager` 装配点与相关单测）；**(b)** 按 CLAUDE.md §二在 `.claude-index/config.md` 显式记录该文件的
阈值偏离与理由。评审倾向 (a)，但不作为放行条件。渲染规则：非空时在决策纪律 #1 的「…候选支撑阻力与超买/超卖，」之后**插入**
`再用战术周期（5m → 1m）核对执行时机（本级别是否已形成有效突破/回踩、量能是否配合、还是仅为噪音；战术周期只用于择时与确认，不得单独反转中线判断），`。
生产装配点唯一：`manager._build_member` 的 `build_ai_trade_system_prompt(...)` 调用，传
`tactical_bars_text=wake_runtime_text`（由 §2.6 的 `WakeRuntime.tactical_bars_text` 派生，与实际注入
的周期集合**同源**——说的和给的必须是同一批周期）。

### 2.9 归因落地（`session._inject_explain_meta`）

```python
_WAKE_ATTRIBUTED_KINDS = ("atomic", "reverse")   # 仅 AI 决策直接产生的成交

# _inject_explain_meta 内新增两行：
if explain.get("kind") in _WAKE_ATTRIBUTED_KINDS and self._wake.last_round_kind:
    meta.setdefault("wakeKind", self._wake.last_round_kind)
```

刻意**不给** `profit_guard` / `session_stop` / `external_close` / `risk_exit` 打 `wakeKind`——它们由确定性
层触发，与唤醒无关；打上去会让「按唤醒来源切胜率」这张表整体失真，而那正是本条要解决的问题。

### 2.10 一条端到端时间线（30m 执行 / 3 分钟唤醒，可反算）

阈值口径：`move_atr_mult=0.5`、`horizon_scale = sqrt(180/1800) = 0.316` ⇒ **有效门槛 = 0.158×30m ATR**。

```
10:00:00  30m 确认根落地 → 信号板重算 → wake{kind:candle_close}
          as_of_ts = barTs + 1800 = 10:00:00（该根收盘 = 决策瞬刻，§2.3.0）
          执行披露=full、战术档=full（kind 在升档集合内）
          prompt：中线背景 5 行 + 执行周期证据 3 行 + 战术周期 1 + 2 行
10:03:00  定时到点，空仓、无挂单、价 +0.03%（0.07×ATR < 0.158）→ wake{kind:heartbeat}
          → should_skip_wake=True → **不调 LLM**，wakeSkips+1、skipStreak=1，推一次 status
10:06:00  定时到点，价 +0.42%（0.91×ATR ≥ 0.158）→ wake{kind:price_breakout}，skipStreak 归零
          as_of_ts = ts = 10:06:00（**不是** 10:36:00——否则 1m/5m 全被判 stale，§2.3.0 P0-1）
          执行披露=repeat（中线背景压成 3 行、执行周期证据压成 1 行）
          战术档=full（price_breakout 在升档集合内）
          prompt：模型第一次看到「1m 已连收 3 根阳、5m 放量 1.31 倍」这类**新**证据
10:09:00  定时到点，已开多、保护层 idle→armed → wake{kind:profit_tier}
10:12:00  定时到点，持仓、无跨档、价微动 → wake{kind:position_watch}
          文案明确告知「保护层已接管退出，你的价值在于判断是否提前离场」
10:30:00  新确认根落地，但上一轮 LLM 仍在跑 → _emit_decision_skipped(wake.dropped=True)
10:31:30  定时到点，bar_index 已推进 → wake{kind:bar_advanced}
          执行披露=full（这根 K 线的首次研判），文案明说「本轮是它的首次研判」
```

**[评审 P0-2] 静止行情兜底时间线**（同配置，`ai_trade_wake_max_skip_streak=5`）：

```
14:00:00  确认根 → 决策（skipStreak 归零）
14:03 / 14:06 / 14:09 / 14:12 / 14:15   连续 5 次 heartbeat → 全跳过，skipStreak 1→5
14:18:00  heartbeat 但 skipStreak(5) >= 5 → should_skip_wake=False → **强制放行一轮**
          kind 仍如实是 heartbeat，文案仍是条件化措辞；轮启动后 skipStreak 归零
```
即：静止行情下 AI 从「每 3 分钟必叫」降到「每 18 分钟至少叫一次」，既省钱又不会变成事实停机。

---

## 三、文件 / 模块变更计划

### 3.1 后端源文件（`services/quant/app`）

| 文件 | 动作 | 一句话意图 |
|---|---|---|
| `ai_auto/tactical_context.py` | **新建**（约 210 行） | 战术周期选取 + 因果建帧 + 证据装配 + 两个披露分档纯函数 |
| `ai_auto/wake_reason.py` | **新建**（约 130 行） | `WakeFacts` / `classify_wake` / `moved`：七种唤醒原因的唯一判定真源 |
| `ai_auto/wake_runtime.py` | **新建**（约 180 行） | 会话级门面：状态记账 + 分类 + 跳过 + payload 注入（不让 session.py 再长） |
| `ai_auto/wake_text.py` | **新建**（约 150 行） | 唤醒原因 / 战术周期 / 重复轮压缩三类 prompt 文案（判定与文案同源） |
| `ai_auto/wake_guard.py` | 改（+~24 行） | 并列新增 `should_skip_wake(wake, …, max_skip_streak)`；既有函数与语义一字不改 |
| `ai_auto/market_window.py` | 改（+~95 行） | 新 `WindowOptions` dataclass（**[P1-1]** 形参回落到 5）+ 战术窗口：`prefill_tactical` / `maybe_refresh_tactical`（**三闸门 + `wait_for` 硬超时**）/ `tactical_frames` |
| `ai_auto/session.py` | 改（+~38 行 / −~12 行） | `WakeRuntime` **先于** `MarketWindow` 装配；`WindowOptions` 改写调用点；三处刷新调用点；新 `_decision_as_of_ts`（**[P0-1]**）；`trigger` 形参换 `wake`；`note_guard_status` / `note_atr` / `note_skip` 三处记账；`_inject_explain_meta` 加 `wakeKind` |
| `ai_auto/prompts.py` | 改（+~38 行） | 三个薄壳 `_format_wake` / `_format_tactical` / 重复轮分支 + 系统 prompt 新形参 |
| `ai_auto/trace.py` | 改（+4 行） | `new_round_trace` 显式取 `wake` / `tacticalContext` / `tacticalTier` / `contextDisclosure` 四键 |
| `ai_auto/manager.py` | 改（+~6 行） | 生产装配处传 `tactical_bars_text`（与注入周期同源）+ 预检 `probe` 改用 `WindowOptions`（`manager.py:595`，**不改即 TypeError**） |
| `ai_auto/market_text.py` | **新建（50 行，实施期追加）** | **[D-13]** §2.8 行数逃生口：既有 `_format_multi_timeframe*` 三函数**逐字**搬家，把 `prompts.py` 从 810 行拉回 775 行 |
| `config.py` | 改（+10 项，见 D-12） | 新增 11 个 `ai_trade_*` 设置（见 §4.2；原 8 项 + P0-2 的 `wake_max_skip_streak` + P1-4 的 `tactical_fetch_timeout_s`；`horizon_scale` 无配置项，是纯函数） |
| `.env.example` | 改（+1 段） | 镜像新设置并注明默认值与关闭方式 |

**[评审 P2-7] `desktop/src/lib/config.js` 明确不在变更范围**：它是**前缀白名单**透传
（`config.js:72` `key.startsWith('AI_TRADE_')`，已核实），11 个新 `AI_TRADE_*` env 天然被覆盖，
无需逐项登记。该文件当前含他人未提交改动，**严禁**为本特性去碰它。

### 3.2 后端测试（`services/quant/tests`）

| 文件 | 动作 | 覆盖 |
|---|---|---|
| `test_ai_trade_tactical_context.py` | 新建 | 选取表 **10 行**全覆盖（含 4H/1D 三行）、因果裁剪、**决策瞬刻双钟（T-19）**、分档判定、降级为 None、`changeSinceLastRound` 反算 |
| `test_ai_trade_wake_reason.py` | 新建 | 七种 kind 的优先级表、`alsoTrue` 不丢证据、ATR/pct 双判据与不可信价 fail-open、**`horizon_scale` 校准表（T-22）** |
| `test_ai_trade_wake_text.py` | 新建 | 七条文案含关键短语、`brief`/`full` 行数、截断标记、重复轮压缩、`wake` 缺席 ⇒ 空串 |
| `test_ai_trade_wake_context_session.py` | 新建 | 会话级：定时轮注入战术段、`heartbeat` 跳过、`bar_advanced` 复现、`wakeKind` 落 explain |
| `test_ai_trade_market_window.py` | 改 | 战术窗口预填 / 锚点闸 / 配额闸 / **单 tick 节流闸（T-20）** / **`wait_for` 硬超时不阻塞（T-21）** / 只收确认根 / 取数失败不推进锚点 / **预填 limit ≤300 不触发翻页（T-23）** |
| `test_ai_trade_prompts.py` | 改 | G-1 冻结门禁：三开关全关时 `build_decision_prompt` 与 `build_ai_trade_system_prompt` 的 sha256 不变 |
| `test_ai_trade_wake_guard.py` | 不改 | 既有守卫签名与语义零回归的活证据 |
| `test_ai_trade_wake_interval.py` | 改（加法） | 既有断言全保留，追加「wake 对象透传到 decision 事件」 |
| `test_ai_backtest_runner.py` | 改（加法） | G-2：回测 payload 无 `wake` / `tacticalContext` 键、prompt 逐字节不变 |

### 3.3 前端（`apps/web`）

| 文件 | 动作 | 一句话意图 |
|---|---|---|
| `lib/aiTradeTypes.ts` | 改 | 新增 `WakeInfo` / `TacticalFrame` / `TacticalContext`；三处宿主（`AiTradeDecision` / `AiTradeTraceEvent` / `AiTradeRoundTrace`）各加 `wake?` / `tacticalContext?` / `tacticalTier?` / `contextDisclosure?` |
| `lib/store.ts` | 改 | **`emptyRoundTrace` 逐键手抄处补 4 个新键**（`wake` / `tacticalContext` / `tacticalTier` / `contextDisclosure`）——本仓库前科：漏抄即静默吞掉 WS 新键，功能跑不出错却永远空白（`store.ts:119-190`） |
| `lib/aiTrade.ts` | 改 | `describeTrigger` 按**[P1-2]**的三级优先扩展（见下）；新增 `describeWakeReason` / `describeTacticalFrame` / `describeTacticalAlignment` |
| `components/Agent/AiTradeTraceRound.tsx` | 改 | `SignalContext` 内在中线背景之后、体制/共识之前插入战术周期块；**同时把 tactical 纳入整段早退门控（[P1-3]）**；轮头 chip 沿用 `describeTrigger` |
| `lib/aiTrade.test.ts` | 改（**纯加法**） | 既有 6 条 `describeTrigger` 断言**一字不改**（G-8）+ 无边沿分支的七态新断言 + 两个新 describe helper |
| `lib/aiTradeTrace.test.ts` | 改 | reducer 保留四个新键（防「后端发了前端收不到」） |
| `components/Agent/AiTradeAgentTrace.test.tsx` | 改 | 战术周期块渲染 + 唤醒原因轮头文案 + **仅含 tacticalContext 的轮次整段仍渲染** |

**[评审 P1-2] `describeTrigger` 的优先级（必须三级，不得让 wake 抢先）**

原稿写「优先读 `trace.wake.kind`」会造成功能退化 + 既有测试转红：现行实现是「信号板边沿 > trigger」
（`aiTrade.ts:944-968`），`aiTrade.test.ts:444-447` 带注释显式断言「有边沿信号时，即便 trigger=interval
仍按信号板派生」。若 wake 抢先，轮头会从信息量更高的 `开多信号 · 均线突破` 退化成 `价格异动`。

正确设计：**信号板边沿 > `wake.kind` > `trigger`**。即 `wake.kind` 只把原来单调的两个兜底文案
（`周期唤醒 · 常规复盘` / `K 线收盘 · 常规复盘`）细化成七态，**不改动**有边沿信号时的任何分支：

```ts
// 无边沿分支内（fired.length === 0）：
const kind = trace.wake?.kind;
if (kind) return WAKE_LABELS[kind];        // 七态中文标签 + tone（缺 kind 时才走下面两行）
if (trace.trigger === "interval") return { label: "周期唤醒 · 常规复盘", tone: "accent" };
return { label: "K 线收盘 · 常规复盘", tone: "muted" };
```

`WAKE_LABELS` 的 tone 约定：`bar_advanced` / `price_breakout` / `profit_tier` → `accent`（值得注意）；
`resting_order` → `accent`；`position_watch` → `muted`；`candle_close` → `muted`；`heartbeat` → `muted`。
`dropped:true` 的丢弃轮沿用既有 `state` 徽标，不额外造标签。

**[评审 P1-3] `SignalContext` 的早退门控必须补 tactical**

`AiTradeTraceRound.tsx:520` 现为：

```ts
if (!board.length && !regime && !consensus && !price && !volume && !factors && !multiTimeframe) return null;
```

条件里没有 tactical 项 ⇒ 只有战术数据的轮次会让「输入信号板」**整段消失**（跑不出错、永远空白）。
这正是本仓库 RV-2 的复发形态（新增边缘态字段必须验证渲染门控没把该态排除掉）。必须改为
`… && !multiTimeframe && !tactical`，并由 T-18 用「仅含 `tacticalContext` 的轮次」把它钉住。

**中文化展示点清点（RV-1 教训：新 helper 必须接全展示点）**：`describeTrigger` 的消费点当前仅
`AiTradeTraceRound.RoundHeader`（`AiTradeTraceRound.tsx:50`）一处（`grep` 实测），战术周期块为新增点，
故本轮展示点合计 2 处，无遗漏风险；实施后须以
`grep -rn "describeTrigger\|describeTacticalFrame\|describeWakeReason" apps/web` 复核。

---

## 四、接口设计

### 4.1 payload 新键（决策器输入，camelCase）

```jsonc
{
  "trigger": "interval",              // 既有二值，wire 不变（前端 / 回测兼容）
  "wake": {                            // 总闸关闭 ⇒ 整键缺席
    "kind": "price_breakout",
    "trigger": "interval",
    "sinceLastRoundS": 182.4,
    "priceMovePct": 0.42,
    "priceMoveAtr": 0.91,
    "moveThresholdAtr": 0.5,           // 配置值（未折算）
    "moveThresholdPct": 0.0005,
    "horizonScale": 0.316,             // [P0-2] sqrt(min(1, wake_s / bar_s))
    "moveThresholdAtrEff": 0.158,      // [P0-2] 实际判据 = moveThresholdAtr × horizonScale
    "skipStreak": 0,                   // [P0-2] 连续跳过计数（触到 max 即强制放行）
    "barAdvanced": false,
    "restingAgeS": null,
    "alsoTrue": []
  },
  "tacticalContext": {                 // 总闸关闭 / 无候选周期 / 全周期不可用 ⇒ 整键缺席
    "executionBar": "30m",
    "wakeIntervalS": 180,
    "asOfTs": 1754038800,              // [P0-1] = _decision_as_of_ts（墙钟口径的决策瞬刻）
    "alignment": "aligned",
    "changeSinceLastRound": {          // [P2-3] 与周期无关，上提到顶层
      "pct": 0.42, "atrMult": 0.91, "atrBar": "30m"
    },
    "frames": [
      {"bar": "5m", "sampleCount": 120, "closeTs": 1754038800, "ageSeconds": 42,
       "stale": false, "regime": {...}, "digest": {...}, "closedBars": 1},
      {"bar": "1m", "...": "..."}
    ],
    "missingBars": [], "staleBars": []
  },
  "tacticalTier": "full",              // "full" | "brief"；随 tacticalContext 一同缺席
  "contextDisclosure": "repeat"        // "full" | "repeat"；ai_trade_progressive_disclosure=False ⇒ 缺席
}
```

`frames` 顺序：**由粗到细**（`5m` 在前、`1m` 在后），与 prompt 渲染、前端列表同序，与 MTF 的
「由大到小」阅读方向一致。

**[评审 P2-8] 缺席矩阵**（三总闸互相独立，各管自己的键）：

| 总闸 | 关闭后缺席的键 |
|---|---|
| `ai_trade_wake_reason_enabled=False` | `wake`（⇒ prompt 回退 `_trigger_suffix`、`explain.meta` 无 `wakeKind`、守卫回退 `should_skip_interval_wake`） |
| `ai_trade_tactical_context=False`（或 `max_bars=0` / 无候选 / 全周期不可用） | `tacticalContext` + `tacticalTier` |
| `ai_trade_progressive_disclosure=False` | `contextDisclosure`（渲染恒 `full`，中线背景与执行周期证据不压缩） |

### 4.2 配置（`config.py` → `Settings`；`.env` 前缀 `AI_TRADE_`）

| 设置 | 类型 / 约束 | 默认 | 语义 |
|---|---|---|---|
| `ai_trade_tactical_context` | bool | `True` | 战术周期总闸；`False` ⇒ 整键缺席 + prompt 逐字节回退 |
| `ai_trade_tactical_max_bars` | int, `ge=0, le=3` | `2` | 注入的战术周期数；`0` 等价关闭 |
| `ai_trade_tactical_window_bars` | int, `ge=40, le=300` | `120` | 每个战术周期窗口根数（≥`market_digest._MIN_BARS=35`） |
| `ai_trade_tactical_min_refresh_s` | float, `gt=0, le=600` | `20.0` | REST 刷新硬下限（配额护栏，防 429） |
| `ai_trade_tactical_fetch_timeout_s` | float, `gt=0, le=60` | `8.0` | **[P1-4]** 单次战术取数硬超时（护 5 秒盯市循环不被交易所慢响应拖停；超时归入既有降级路径，不推进锚点） |
| `ai_trade_tactical_max_chars` | int, `ge=0, le=4000` | `700` | 战术段渲染字符上限；`0` = 不截断 |
| `ai_trade_wake_reason_enabled` | bool | `True` | 唤醒原因总闸（payload / prompt / 事件 / 归因） |
| `ai_trade_wake_move_atr_mult` | float, `ge=0, le=10` | `0.5` | 价格异动判据（×执行周期 ATR，**经 `horizon_scale` 折算**，见 §2.4.1）；`0` ⇒ 逐字回退纯 pct 判据 |
| `ai_trade_wake_max_skip_streak` | int, `ge=0, le=100` | `5` | **[P0-2]** 连续跳过上限，到即强制放行一轮（饿死兜底）；`0` = 不封顶（回退纯守卫行为） |
| `ai_trade_progressive_disclosure` | bool | `True` | 重复轮压缩总闸；`False` ⇒ 执行披露恒 `full` |

既有 `ai_trade_wake_skip_unchanged_pct`（默认 `0.0005`，`config.py:191`）**保留不动**，作为 ATR 不可用时
的回退判据。`horizon_scale` **刻意不做成配置项**：它是量纲折算（√t 律），不是调参旋钮；把它开放成配置
等于允许用户重新制造 P0-2 的失配。

注（P2-2）：`ai_trade_tactical_context=True` 但既有 `ai_trade_regime_context=False` 时，战术帧的 `regime`
恒 `None` ⇒ `alignment` 恒 `"insufficient"` ⇒ `disclosure_tier` 第 3 条判据恒假、prompt 无背离裁决句。
这是**可接受的降级**（其余三条判据与 digest 证据仍全量生效），不是缺陷；T-8 须有一例覆盖它，避免
下游把它当 bug 追。

### 4.3 WS 事件（加法，老前端忽略未知键）

- `decision`：`data.wake`（对象 | `null`）、`data.tacticalContext`、`data.tacticalTier`、
  `data.contextDisclosure`；`data.trigger` 语义与取值不变。
- `agent_trace / round_start`：同上三键随 `new_round_trace` 装配（**显式取键**，见 §3.1 trace.py）。
- `status`：不变（`wakeSkips` 语义不变，仍是「被抑制的、原本会发生的唤醒次数」）。

### 4.4 REST / DB

- **无新增 REST 端点**；`/ai-trade/*` 请求体与响应体不变（战术周期由服务端按 `bar` + `wakeIntervalMinutes` 自解析，
  沿用「不给启动请求加字段，否则永远没人填」的既有拍板）。
- **无新增表 / 列 / 迁移**；`trades.explain_json.meta.wakeKind`（字符串 | 缺席）是唯一持久化面。

---

## 五、数据模型

### 5.1 内存态（会话生命周期，进程内）

| 载体 | 字段 | 说明 |
|---|---|---|
| `WakeRuntime` | `_last_round_px` / `_last_round_bar_index` / `_last_round_as_of_ts` / `_last_round_guard_status` / `_last_round_kind` | 上一决策轮断面；`None` = 尚无决策轮 ⇒ 分类走 `bar_advanced`（首轮如实标注为「首次研判」）。**[P0-1]** 第三项是 `as_of_ts`（决策瞬刻，与本轮同钟），**不是** `time.time()` |
| `WakeRuntime` | `_tactical_bars` / `_tactical_bars_text` | 构造期一次求得，会话内不变；**是战术集合的唯一真源**（`MarketWindow` 从此处取，不自算，[P1-1]） |
| `WakeRuntime` | `_guard_status` / `_atr_px` | 盯市 tick 记账（`note_guard_status` / `note_atr` 的落点），`WakeFacts` 的**唯一**回读来源（[P1-6]） |
| `WakeRuntime` | `_skip_streak` | **[P0-2]** 连续跳过计数；只在 `_count_wake_skip` 返回 `True` 时 +1，任一决策轮启动即归零 |
| `MarketWindow` | `_tactical_windows[bar]: deque(maxlen=tactical_window_bars)` | 每周期确认根滚动窗口（`{ts,o,h,l,c,vol}`）；刻意不复用 `_window_max`（最大 1200） |
| `MarketWindow` | `_tactical_next_refresh_ts[bar]` / `_tactical_last_fetch_wall[bar]` | 锚点闸 / 配额闸（单 tick 节流闸无需状态，是调用内的一次排序取首） |

内存上限：2 周期 × 120 根 × 6 字段 ≈ 数十 KB/会话，可忽略。

### 5.2 持久化

- `runs.metrics_json.aiStats`：**不新增字段**（`wakeSkips` / `maxHoldStreak` 语义不变）。
- `trades.explain_json.meta.wakeKind`：`"candle_close" | "bar_advanced" | "resting_order" |
  "profit_tier" | "price_breakout" | "position_watch" | "heartbeat"`，仅
  `explain.kind ∈ {"atomic","reverse"}` 的成交带此键；老数据无此键（查询侧按缺席处理为「未知」）。

### 5.3 前端类型（`lib/aiTradeTypes.ts`）

```ts
export type WakeKind =
  | "candle_close" | "bar_advanced" | "resting_order"
  | "profit_tier" | "price_breakout" | "position_watch" | "heartbeat";

export interface WakeInfo {
  kind: WakeKind;
  trigger: "candle" | "interval";
  sinceLastRoundS?: number | null;
  priceMovePct?: number | null;
  priceMoveAtr?: number | null;
  moveThresholdAtr?: number | null;
  moveThresholdPct?: number | null;
  barAdvanced?: boolean;
  restingAgeS?: number | null;
  alsoTrue?: WakeKind[];
  dropped?: boolean;            // 被 in-flight 守卫丢弃的 K 线轮
}

// [评审 P2-3] 与周期无关，故挂在 TacticalContext 顶层；atrBar 显式声明 ATR 的量纲周期。
export interface TacticalChange { pct: number | null; atrMult: number | null; atrBar: string }

export interface TacticalFrame {
  bar: string; sampleCount: number; closeTs: number; ageSeconds: number; stale: boolean;
  regime?: MarketContext | null; digest?: MarketDigest | null;
  closedBars?: number | null;          // 上一决策轮之后该级别**收线**的根数（[P0-1] 按收线时刻计）
}

export interface TacticalContext {
  executionBar: string; wakeIntervalS: number; asOfTs: number;
  alignment: "aligned" | "diverged" | "insufficient";
  changeSinceLastRound?: TacticalChange | null;
  frames: TacticalFrame[]; missingBars: string[]; staleBars: string[];
}
```

`WakeInfo` 同步补 `horizonScale?: number | null` / `moveThresholdAtrEff?: number | null` /
`skipStreak?: number | null` 三个 **[评审 P0-2]** 新键（可选，老事件缺席即 `undefined`）。

---

## 六、测试与验收标准

### 6.1 必须先红后绿的用例（T-x；改业务码之前先写）

| # | 用例 | 断言要点 |
|---|---|---|
| T-1 | `tactical_timeframes` 选取表 | §2.1 表格 **10 行**逐行相等（含 30m/3min ⇒ `("1m","5m")` 这一用户原话用例，及 4H/1D 三行） |
| T-2 | 因果裁剪 | 构造含未来根的帧，断言 `ts + BAR_SECONDS[bar] > as_of_ts` 的根不进任何指标 |
| T-3 | 未确认根不入窗 | 喂 `confirm=False` 的 candle，断言窗口长度不变、指标不 repaint |
| T-4 | `classify_wake` 优先级 | 七种 kind 各一例 + 一例「`resting_order` 与 `price_breakout` 同时成立」断言 `kind="resting_order"` 且 `alsoTrue=["price_breakout"]` |
| T-5 | ATR 判据 | `atr_px=100, mult=0.5, px 移动 60` ⇒ 越阈；移动 40 ⇒ 未越阈；`atr_px=None` ⇒ 回退 pct 判据且结果与既有守卫一致 |
| T-6 | 价不可信 fail-open | `px=None` / `px<=0` ⇒ 判为越阈（唤醒），绝不跳过 |
| T-7 | `should_skip_wake` | 仅 `heartbeat` 跳过；两阈值皆 0 ⇒ 恒不跳过 |
| T-8 | 分档判定 | 空仓+heartbeat+aligned+无极端读数 ⇒ `brief`；改任一条件 ⇒ `full`（4 个单条件用例） |
| T-9 | 重复轮压缩 | `contextDisclosure="repeat"` 时中线背景仅表头 3 行、执行周期证据 1 行，且**必含**「与本根首次研判相同」字样 |
| T-10 | 截断可见 | 构造超 `max_chars` 的战术段，断言结尾含「已按 700 字上限截断」 |
| T-11 | 文案关键短语 | 七条唤醒文案各含其判定短语；`position_watch` 必含「保护」「提前」；`bar_advanced` 必含「首次」；`heartbeat` 必含条件词「若」 |
| T-12 | 会话贯通 | 注入两根 30m 根 + 一次定时唤醒，断言 `decision` 事件 `data.wake.kind` / `data.tacticalContext.frames` 非空 |
| T-13 | `bar_advanced` 复现 | 令 `_deciding=True` 时喂确认根（触发 skipped 事件），随后放开并触发定时唤醒，断言 `kind=="bar_advanced"` 且 `contextDisclosure=="full"` |
| T-14 | 归因落地 | AI 开仓成交 ⇒ `explain.meta.wakeKind` 存在；利润保护平仓 ⇒ **不存在** |
| T-15 | 取数韧性 | `get_candles` 抛异常 ⇒ 窗口不变、锚点不推进、决策轮照常完成（`tacticalContext` 为 `None`） |
| T-16 | 配额闸 | 连续 4 个 5 秒 tick 内最多一次 REST 调用（`min_refresh_s=20`） |
| T-17 | 前端 reducer | `round_start` 事件带**四**新键 ⇒ `AiTradeRoundTrace` 上可读（防 store.ts 漏抄） |
| T-18 | 前端渲染 | 战术周期块出现两条 `data-testid="tactical-frame"`；轮头显示唤醒原因中文；**[P1-3]** 另构造一个「板空 + 无 regime/consensus/digest/MTF、**仅有** tacticalContext」的轮次，断言「输入信号板」整段**仍渲染**（钉住早退门控） |

**[评审] 新增用例（对应本轮 P0/P1 修复，同样先红后绿）**

| # | 用例 | 断言要点 |
|---|---|---|
| **T-19** | **[P0-1] 决策瞬刻双钟** | 同一份战术窗口（1m 末根 42 秒前收线）分别走「K 线轮（`ts`=根开盘）」与「定时轮（`ts`=墙钟）」两条入口：两轮的 `tacticalContext.frames[*].ageSeconds` 均 < 120，`stale` 均为 `False`，`staleBars == []`；再显式断言若误用 `ts + BAR_SECONDS[bar]` 走定时轮则 `stale` 会为 `True`（把回归写成反例，防未来重犯） |
| **T-20** | **[P1-4] 单 tick 节流闸** | 两个战术周期同时过锚点闸与配额闸时，一次 `maybe_refresh_tactical` 只发生 **1 次** `get_candles`（且取 `BAR_SECONDS` 最小的那个）；下一 tick 刷另一个 |
| **T-21** | **[P1-4] 取数挂住不阻塞** | 桩 `get_candles` 睡超过 `tactical_fetch_timeout_s` ⇒ `maybe_refresh_tactical` 在超时内返回、窗口不变、锚点不推进、无异常外泄 |
| **T-22** | **[P0-2] `horizon_scale` 校准** | `horizon_scale(180,1800)≈0.316`、`horizon_scale(120,86400)≈0.037`、`wake>=bar ⇒ 1.0`、任一入参 ≤0 ⇒ `1.0`；并断言 1D/2min + `atr_px=3%×px` 下一个 0.06% 的位移**被判为越阈**（原写法会判未越阈 ⇒ 永久跳过） |
| **T-23** | **[P1-5] limit 不触发翻页** | `tactical_window_bars=300`（上界）时预填 `limit == 300`，桩客户端断言只发生 1 次 `/market/candles`、`before/after` 均为 `None` |
| **T-24** | **[P0-2] 跳过上限兜底** | 连续 5 次 heartbeat（每次 `_count_wake_skip` 计数成功）后第 6 次 `should_skip_wake` 返回 `False`；且断言「5 秒 tick 裸调用不会推进 `skipStreak`」（口径易踩点） |
| **T-25** | **[P1-6] guard status 单一真源** | `note_guard_status("armed")` 后 `classify` 的 `WakeFacts.guard_status` 即为 `"armed"`；`note_round_start` 冻结的 `last_round_guard_status` 与分类当时所见**一致**（不允许调用方另传） |

### 6.2 零回归门禁（G-x；全绿方可提交）

| # | 门禁 |
|---|---|
| G-1 | `ai_trade_tactical_context=False` + `ai_trade_wake_reason_enabled=False` + `ai_trade_progressive_disclosure=False` ⇒ 固定 payload 下 `build_decision_prompt` 输出 **sha256 与改动前逐字节相同**；`tactical_bars_text=""` ⇒ `build_ai_trade_system_prompt` 同样逐字节相同 |
| G-2 | 回测：`backtest.py` 自建 payload 不含 `wake` / `tacticalContext` 键，回测 prompt sha256 不变 |
| G-3 | `test_ai_trade_wake_guard.py` **一字不改**且全绿（既有守卫函数语义未动的活证据） |
| G-4 | `test_ai_trade_wake_interval.py` 既有断言全保留且全绿 |
| G-5 | 无新增 DB 表 / 列 / 迁移：`grep -rn "ALTER TABLE\|add_column" services/quant/app/db` 的**匹配行集合与改动前逐行相同**（今日基线已有 5 处命中：`db/models.py:345`、`db/session.py:103/109/124/132`）。**[评审 P2-5]** 原措辞「无本轮新增」会让门禁在「无匹配=通过」与「有匹配=失败」之间二义化——本仓库有 grep 式门禁空转前科，故改为逐行比对 |
| G-6 | 后端全量 `python -u -m pytest services/quant/tests -q` 红灯集合**不多于**基线（基线已知：7 个 `factor_library_expansion` 孤儿测试 + `test_agent_ws` 顺序 flake；全量不得并发跑，共用临时 DB） |
| G-7 | 前端 `NODE_ENV=test npx vitest run`（组件测试必须置 `NODE_ENV=test`，否则 React act() 报错）全绿；`npx tsc --noEmit` 不新增错误（`WeightBatchArena.test` 既有错误为存量，非本轮回归） |
| **G-8** | **[评审 P1-2]** `apps/web/lib/aiTrade.test.ts` 中既有的 6 条 `describeTrigger` 断言（第 428~485 行 `describe` 块）**一字不改**且全绿——特别是第 444-447 行「有边沿信号时即便 `trigger=interval` 仍按信号板派生」。这是「wake 未抢先」的活证据 |
| **G-9** | **[评审 P1-1]** `grep -rn "context_enabled" --include=*.py .` 改动后只应命中 `WindowOptions` 定义处；`session.py:174` 与 `manager.py:595` 两处调用点均已改为 `options=WindowOptions(...)`（漏改即 `TypeError`，且 `manager` 那处在**启动预检**路径上，会让整个启动 500） |

### 6.3 验收标准（A-x）

- **A-1（功能，用户第 1 条）**：30m 执行 + 3 分钟唤醒的会话，任取一个定时唤醒轮的 prompt，必须能读到
  `1m` 与 `5m` 两个周期的方向 / RSI / 量比 / 自上轮增量，且这些数值在相邻两个定时轮之间**确有变化**。
- **A-2（功能，用户第 2 条）**：同一轮 prompt 首行必须带「唤醒原因=…」，且原因与 `decision` 事件的
  `data.wake.kind`、轨迹面板轮头显示的中文标签**三者一致**。
- **A-3（归因）**：会话产生的每笔 AI 开/平仓成交，其 `explain_json.meta.wakeKind` 落在七值枚举内；
  保护层平仓无此键。
- **A-4（上下文预算，硬指标）**：同一会话配置下，**重复轮**（`contextDisclosure=="repeat"`）的
  `build_decision_prompt` 字符数不得超过改动前同轮次的 **115%**；`brief` 档下不得超过 **100%**。
  以 §6.1 的固定 payload 夹具离线比对（不依赖真实行情）。
- **A-4b（上下文预算，`full` 轮，[评审 P1-7] 新增）**：`full` 档（`candle_close` / `bar_advanced` 等升档轮）
  的 prompt 字符数不得超过改动前同轮次的 **135%**。原方案只对重复轮设上限，等于允许首轮无界膨胀——
  而用户明确提出的约束是「渐进式上下文披露」，三档都要有天花板。超标即视为分档策略失效（应先收紧
  `full` 档的每周期第二行，而不是调高本阈值）；`ai_trade_tactical_max_chars` 的可见截断是最后兜底。
- **A-5（配额与延迟）**：单会话稳态 REST 增量 ≤ `len(tactical_bars)` 次/最小战术周期，5 标的分组下
  ≤ 12 req/min（实测日志计数）。**[评审 P1-4] 追加**：单个 `_monitor_tick` 的墙钟耗时 p99 增量
  ≤ `ai_trade_tactical_fetch_timeout_s`（8s），且任意一次交易所超时**不得**推迟利润保护判定超过一个
  poll 周期（T-20/T-21 离线守门，实盘以盯市日志时间戳复核）。
- **A-6（回退）**：三总闸全关时，会话行为、prompt、事件 wire 与改动前逐字节一致（G-1/G-2 覆盖）。
- **A-7（观感）**：轨迹面板「输入信号板」展开后，战术周期块与既有中线背景块视觉同构（同字号、同
  行距、同 `var(--muted)` 色阶），不引入新组件语言；缺失 / 陈旧周期如实标注，**不假数据**。

---

## 七、风险与缓解

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R-1 | **战术周期噪音诱发过度交易**——1m 级别信号横跳，模型被短周期带偏，交易频次上升、被成本吃掉 | 高（直接反向恶化「总是亏损」） | ① 文案硬约束「战术周期只用于择时与确认，**不得单独反转中线判断**」写进系统 prompt 与每轮战术段抬头；② 战术周期**不参与**任何门禁（铁律层 / edge / 仓位 / 保护层判定的输入一律不变）；③ 战术周期一律只用确认根，杜绝 repaint；④ A-4 之外增设灰度建议：先在纸面/小预算会话跑满一日再放开 |
| R-2 | **REST 配额**：多标的分组 × 战术周期 ⇒ 429 | 中（本仓库有 429 前科） | 锚点闸 + `min_refresh_s` 配额闸双保险；取数失败不推进锚点、静默降级为 `tacticalContext=None`；T-15/T-16 守门 |
| R-3 | **上下文膨胀**推高 token 成本与延迟 | 中 | 两级披露分档 + 重复轮压缩 + `max_chars` 硬截断（可见标记）；A-4 把「不得比今天更贵」变成可执行断言 |
| R-4 | **判定与文案漂移**（守卫说跳过、prompt 说没变，两处各算一次） | 高（信任崩塌，本仓库反复踩过） | `moved()` / `classify_wake` 是唯一判定真源，守卫与文案都只读 `wake` 对象；阈值随 wire 下发，用户能看到判据 |
| R-5 | **前端漏抄新键**导致功能静默缺失（`store.ts` 逐键手抄、`new_round_trace` 显式取键） | 中 | §3.1/§3.3 已把两处点名；T-17 reducer 测试是门禁；实施后须 `grep -rn "tacticalContext" apps/web services/quant` 复核三处宿主全覆盖 |
| R-6 | `session.py` 已 1494 行，再改再胖 | 中 | 逻辑全下沉 `WakeRuntime`；session 净增 ≤30 行且以调用为主；`prompts.py` 逼近 800 行时按 §2.8 逃生口拆 `market_text.py` |
| R-7 | **`profit_tier` 依赖保护层状态**，保护层未装配时恒不触发 | 低 | 未装配 ⇒ `guard_status=None` ⇒ 该分支恒假，自然退化到 `position_watch`；文案不提保护线 |
| R-8 | **`bar_advanced` 误判**：首轮 `last_round_bar_index is None` 也归此类 | 低 | 有意为之——首轮确实是「这根 K 线的首次研判」，文案成立；T-13 显式覆盖首轮与丢弃后两种入口 |
| R-9 | 战术周期与执行周期**体制背离**时模型无所适从 | 中 | 装配 `alignment` 显式标注背离，并在段末给出优先级裁决句（以执行周期与中线为准，战术只回答「是不是好进场点」） |
| R-10 | 本方案**不解决**诊断书问题 ④ 的丢轮本身 | 中 | 已在 §1.3 明列非目标；本轮把它变成**可见**（`dropped` 标记 + `bar_advanced` 告知），为后续「补跑一轮」留出干净接口 |
| **R-11** | **[评审 P1-4]** 战术取数落在 5 秒盯市 tick 上，交易所慢响应会连带推迟**利润保护棘轮 / 会话止损 / staleness** 判定（真实资金） | 高 | 三闸门的单 tick 节流（每 tick 至多 1 次取数）+ `asyncio.wait_for(8s)` 硬超时 + 超时归入既有降级路径（不推进锚点、下 tick 重试）；T-20/T-21 守门；A-5 追加延迟指标 |
| **R-12** | **[评审 P0-2]** ATR 判据把跳过率推高后，静止行情下 AI 可能长时间完全不被唤醒，用户看到的是「自动交易像停机了」且没有原因 | 中 | `horizon_scale` 把绝对水位拉回今日量级 + `ai_trade_wake_max_skip_streak=5` 强制放行兜底 + `skipStreak` / `moveThresholdAtrEff` 随 wire 下发（面板可见「为什么没叫」）；T-22/T-24 守门 |
| **R-13** | **存量偏差留档**：既有 MTF 在**定时轮**同样用 `ts + BAR_SECONDS[bar]` 作 `as_of_ts`（`session.py:1188`），故其 `ageSeconds` 在定时轮偏大一整根 | 低 | 本轮**刻意不改**（改它会动 G-1 冻结基线）。MTF 最细周期为 1H、stale 阈值 7200s，1800s 偏差通常不足以翻转 `stale`；战术段已按 §2.3.0 走独立正确时钟。后续若单独修 MTF，须同批更新 G-1 基线 sha256 |

---

## 八、实现顺序与提交注意

1. **纯函数先行（可独立单测）**：`wake_reason.py`（含 `horizon_scale`，**先写 T-22**）→
   `tactical_context.py`（**先写 T-19**）→ `wake_text.py`，对应 T-1~T-11 + T-19/T-22 先红后绿。
2. **窗口层**：`market_window.py` 的 `WindowOptions` + 三方法 + T-3/T-15/T-16/T-20/T-21/T-23。
   ⚠️ 引入 `WindowOptions` 的同一次改动里必须改完 `session.py:174` 与 `manager.py:595`（G-9）。
3. **门面层**：`wake_runtime.py` + `wake_guard.should_skip_wake` + T-7/T-24/T-25。
4. **接线**：`session.py`（`WakeRuntime` 先于 `MarketWindow` 装配 + `_decision_as_of_ts` + 三处刷新点 +
   两处签名换参 + 三处记账 + `_inject_explain_meta`）→ `prompts.py` → `trace.py` → `manager.py` →
   `config.py` / `.env.example`，对应 T-12~T-14 与 G-1~G-5。
5. **前端**：`aiTradeTypes.ts` → `store.ts`（四键手抄）→ `aiTrade.ts`（**三级优先级，勿让 wake 抢先**）→
   `AiTradeTraceRound.tsx`（**早退门控补 tactical**）→ 三份测试 + G-8。
6. **全量回归**：后端 `python -u -m pytest services/quant/tests -q > log`（**不可并发**，共用临时 DB；
   `-q` 块缓冲须 `python -u` 重定向到文件）；前端 `NODE_ENV=test npx vitest run` + `npx tsc --noEmit`。

**提交纪律（Subtask #3）**：当前工作树含多个在制特性（`services/quant/app/config.py`、
`desktop/src/lib/config.js`、`.env.example` 均为**混合文件**）。必须按 hunk 行级精确暂存
（`git add <具体路径>`，混合文件用 `git apply --cached --recount` 切 patch；Windows 下 patch 文件须以
**bytes** 读写，`write_text` 会翻 CRLF 弄坏 patch），**严禁 `git add -A`**。

---

## 九、实施过程发现的方案缺陷（Subtask #2 填写）

> 代码开发节点若发现本方案与代码现实冲突，在此逐条记录「原方案 → 实际做法 → 理由」，不得静默偏离。
> 下列 D-1~D-11 全部已在实现中落地并有单测守门；12 条放行条件的落实情况见本节末尾对照表。

### D-1（阻断级）`asyncio.wait_for` 的超时**不会**落进 `_safe_get_candles` 的降级路径

- **原方案**（§2.2 P1-4）：「`TimeoutError` 是 `Exception` 子类，落进 `_safe_get_candles` 既有的
  `except Exception` 降级路径（返回 `None`），无需新错误分支」。
- **代码现实**：`asyncio.wait_for` 超时的机制是**取消内层协程**——内层收到的是 `CancelledError`，
  而 Python 3.8+ 起 `CancelledError` 继承 `BaseException`，`except Exception` **捕不到**它；随后
  `wait_for` 在**调用点**抛 `TimeoutError`。照原文写，超时会直接穿透 `maybe_refresh_tactical` 冒到
  `_monitor_tick`，把整个 5 秒盯市 tick 打断（正是 P1-4 要防的那件事，方向还反了）。
- **实际做法**：新增 `MarketWindow._fetch_tactical(bar, limit, now_wall)`，在**调用点**
  `except (TimeoutError, asyncio.TimeoutError)` 收口并返回 `None`（不推进锚点 ⇒ 下 tick 重试）。
  T-21 用「桩 `get_candles` 睡 5s、超时设 0.05s」把它钉死。

### D-2 `build_tactical_context` 的签名缺 `execution_primary`，且 7 个形参破 CLAUDE.md ≤5

- **原方案**（§2.3.1）：`build_tactical_context(*, execution_bar, tactical_bars, frames, as_of_ts,
  last_px, reference, settings)` —— 7 个关键字形参；但同节又要求顶层 `alignment` 是「战术周期
  `regime.primary` 与**执行周期** `regime.primary` 的关系」，而执行周期体制**不在入参里**，算不出来。
- **实际做法**：按本方案自己为 P1-1 立的 `WindowOptions` 先例，聚合成冻结 dataclass
  `TacticalInputs(execution_bar, tactical_bars, as_of_ts, last_px, execution_primary, reference)`，
  主函数签名收敛为 `build_tactical_context(inputs, *, frames, settings)`（3 形参，守 ≤5），并补上缺失的
  `execution_primary`。`disclosure_tier` 同步去掉冗余的 `execution_primary` 形参（`alignment` 已经把它
  编码进去了，留着就是无信息量的死参）。

### D-3 `_causal_frame` 选择**复用**而非「逐字复制」

- **原方案**（§2.3.1）：「逐字复制 `multi_timeframe_context._causal_frame` 的因果裁剪」。
- **实际做法**：`from .multi_timeframe_context import _causal_frame` 直接复用。理由即本方案自己反复
  强调的「估计量同源律」——复制一份就是两处各算一次，一旦某处口径漂移就会出现「窗口在看 A、prompt
  却在讲 B」的无声失真。跨模块引用包内私有名不改动 `multi_timeframe_context` 的任何行为，G-1 冻结基线
  不受影响。

### D-4 `wake` wire 补三个键，否则「文案从 `wake` 直读」根本做不到

- **原方案**（§2.4）的 wire 没有执行周期名、也没有保护层状态，但 §2.8 的文案表里 `candle_close` 要写
  「30m 确认根落地」、`price_breakout` 要写「0.91×**30m** ATR」、`profit_tier` 要写「idle→armed」。
- **实际做法**：wire 增 `executionBar` / `guardStatus` / `lastGuardStatus` 三键。不加就只能在渲染层
  另取一次量纲与状态，直接违背 §2.8 立的「文案取值一律从 `wake` 对象直读」纪律（R-4）。
  前端 `WakeInfo` 同步补这三个可选键。

### D-5 `tacticalContext` 增 `maxChars` / `executionPrimary` 两个只读键

- **原因**：`build_decision_prompt(payload)` 只吃 payload、拿不到 `Settings`，而
  `format_tactical_for_prompt` 需要 `ai_trade_tactical_max_chars`；背离裁决句需要执行周期体制名。
- **实际做法**：装配期把两者写进 `tacticalContext`（随总闸一同缺席，不新增顶层键），渲染层从此仍然
  「只吃 payload」，与本模块其余 `_format_*` 的既有形态一致。

### D-6 `WakeRuntime.classify` 改吃 `WakeReadings`；`inject` 改吃 `InjectionContext`

- **原方案**（§2.6）：`classify(self, facts: WakeFacts)` 与 `inject(payload, *, wake, frames, as_of_ts,
  last_px, position_side, execution_primary)`（8 形参）。前者与 §2.4 的 P1-6 注记**自相矛盾**——那里明写
  「`WakeFacts` 只由 `WakeRuntime.classify` 组装」，可签名却要求调用方先造一个完整 `WakeFacts`。
- **实际做法**：新增 `WakeReadings`（只收调用方真能读到的 6 项：trigger / projected / resting /
  bar_index / px / resting_age_s），`classify(readings, *, now_wall)` 在内部补齐
  `guard_status` / `atr_px` / `last_round_*` 后组装 `WakeFacts`；`inject(payload, ctx)` 吃冻结
  `InjectionContext`。两者都回到 ≤5 形参，且 P1-6 的「调用方不得另取一次」从**约定**变成**签名上做不到**。
- 附带：`note_round_start` 增 `now_wall` 形参（5 个，仍守阈值）承载 `sinceLastRoundS` 的墙钟基准，
  取代原稿未定义来源的「距上轮多久」。

### D-7 `tactical_bars_for` / `format_tactical_bars_text` 提为模块级函数

- **原方案**（§2.8）：manager 装配 system prompt 时传 `tactical_bars_text=wake_runtime_text`，「由
  `WakeRuntime.tactical_bars_text` 派生」。
- **代码现实**：`manager._build_member` 是**先建 decider（含 system prompt）再建 session**，此刻
  `WakeRuntime` 还不存在；为拿这个串再 new 一个 `WakeRuntime` 就是「两处各算一次」。
- **实际做法**：把解析下沉为模块级 `tactical_bars_for(settings, bar, wake_interval_s)` +
  `format_tactical_bars_text(bars)`，`WakeRuntime.__init__` 与 manager **共用同一个函数**。同源由
  函数身份保证，而不是靠纪律。

### D-8 `decision` 事件补三键 ⇒ `_emit_decision` 形参净增 3

- §4.3 要求 `decision` 事件带 `data.tacticalContext` / `tacticalTier` / `contextDisclosure`，但 §2.7 只
  说了「`trigger` 换 `wake`，参数总数不增」。二者不可兼得。
- **实际做法**：按该函数「一个 wire 键一个关键字形参」的既有形态追加三个关键字形参（形态一致优于在
  同一函数里混用两种风格），并在 `.claude-index/config.md` **显式登记**该函数的阈值偏离。

### D-9 P2-1 拍板：选 **(b)** —— 在 `.claude-index/config.md` 登记阈值偏离

`build_ai_trade_system_prompt` 加 `tactical_bars_text` 后 11 个形参。**不选 (a)**（收进
`SystemPromptTexts` 聚合结构体）的理由：该函数被十余处既有单测以位置/关键字参数直连调用，改签名会
打翻一批与本特性无关的冻结断言——用回归风险换风格分不划算。已按 CLAUDE.md §二在
`.claude-index/config.md` 新增「显式记录的阈值偏离」小节，连同 `_emit_decision`、`session.py` 行数一并登记。

### D-10 三个既有测试文件必须同批改（§3.2 表格漏列）

`_build_decision_payload` 的第 5 形参由 `trigger: str` 换成 `wake: dict` 后，
`test_ai_trade_session.py`（9 处）/ `test_ai_trade_prior_session.py`（1 处）/
`test_ai_trade_sizing_session.py`（1 处）共 **11 个调用点**会 `AttributeError: 'str' object has no
attribute 'get'`。§3.2 的测试表没列这三个文件。**实际做法**：每文件加一个
`_CANDLE_WAKE = {"trigger": "candle", "kind": "candle_close"}` 常量并平移调用点，**断言本体一字未改**。
刻意**不做**「形参兼容 str」的垫片——那等于永久保留一条谁也不会再走的死分支。

### D-11 A-4b 的比对夹具与 `full` 档收紧（按 §6.3 的指示：先收紧，不调阈值）

- 用 §6.1 那份最小 payload（477 字，无中线背景、无信号板、无摘要）当分母时，`full` 轮实测 157.7%。
  但那测出来的是**夹具的小**，不是特性的贵：任何绝对增量在 477 字上都会被放大。
- **实际做法**：① 新建 `_production_payload()` 夹具，形状贴近真实生产轮（中线背景 4 周期 + 体制 +
  共识 + 三策略板 + 摘要 + **默认开启**的 `baseRules` 段）；② 按 A-4b 的明确指示**收紧 `full` 档的
  每周期第二行**——剔除「布林%B」（与区间位置高度重合）与「末根 O/H/L/C」（四个数、字符成本最高而
  对择时几乎不增信息），并从周期行剔除 `sampleCount`（数据充足度诊断，模型对它无从下手，且 `stale`
  与「缺失周期」已承担诚实标注职责；它仍随 wire 下发、仍在轨迹面板可见）。
- **实测（离线硬断言，均已入 `test_ai_trade_prompts.py`）**：基线 1284 字 ⇒ `brief` **99.4%**（≤100%）、
  `repeat` **97.3%**（≤115%）、`full` **130.2%**（≤135%）。
- **附加指标**：30m/3min 下一根 K 线约 1 个 `full` + 9 个 `repeat`，**摊薄后 100.6%**——即本特性在
  几乎不加钱的前提下，把 9 个「证据逐字节相同」的轮次换成了带新证据的轮次。这条才是有经济含义的
  指标，已同样写成断言。

### D-13 触发了 §2.8 的行数逃生口：新建 `ai_auto/market_text.py`

- 接线完成后 `prompts.py` 实测 **810 行**，越过 800 红线。§2.8 已为此备好确定性逃生口
  （「若实施时 prompts.py 超过 790 行，把既有 `_format_multi_timeframe*` 三个函数整体搬进新建
  `market_text.py` 并延迟导入」），故按其执行、无需再决策。
- **实际做法**：`_format_candidate` / `_format_multi_timeframe_frame` / `_format_multi_timeframe`
  三个函数**函数体逐字未改**地搬入 `ai_auto/market_text.py`（50 行），`prompts.py` 留一个延迟导入
  薄壳 ⇒ **775 行**（回到红线内）。`wake_text.format_multi_timeframe_repeat` 的表头渲染同步改从
  `market_text` 导入——重复轮压缩形态与全量形态必须共用同一个候选支撑阻力渲染真源。
- 纯搬家，渲染输出逐字节不变；`test_ai_trade_prompts.py` 既有的多周期断言零改动全绿即活证据。

### D-14（RV-1 复发的自查捕获）`describeWakeReason` 差点成为「造了没接」的孤儿 helper

- 实施末期用 `grep -rn "describeTrigger\|describeTacticalFrame\|describeWakeReason" apps/web` 复核展示点
  （§3.3 明列的动作）时发现：`describeWakeReason` 只被**测试**引用，**没有任何生产展示点**——正是
  RV-1「新 helper 必须接全展示点」的原型复发。§3.3 的展示点清点只数了 `describeTrigger`（1 处）与
  战术块（1 处），漏掉了这个新 helper 自己。
- **实际做法**：接进 `SignalContext` 展开区（轮头 chip 只给标签、展开区给**判据数值**，UI 侧的渐进式
  披露），并**同步把 `wake` 纳入整段早退门控**——与 P1-3 对 tactical 的要求同一条教训，不能只修一半。
- 附带修正：`describeWakeReason` 在「无任何数值可讲」时返回空串。否则它会渲染出与轮头 chip **逐字
  相同**的一行（组件测试直接报 `Found multiple elements`），那是纯噪音而非信息。
- 新增断言：`wake-reason` 行必须显示**折算后**的 `阈值 0.16×1H ATR`（而非配置里的 0.5）。

### D-12（观察，非偏离）`config.py` 实增 **10** 项而非 §3.1 表头写的「+11 项」

§4.2 的设置表本身就是 10 行（原 8 + `wake_max_skip_streak` + `tactical_fetch_timeout_s`），§3.1 表格
的「+11 项」是笔误。以 §4.2 为准，实增 10 项。

### 放行条件对照

| # | 条件 | 落实 |
|---|---|---|
| 1 | [P0-1] `_decision_as_of_ts` 唯一真源 + `reference` 存 `asOfTs` + `closedBars` 按收线时刻 + T-19 含反例 | ✅ `session._decision_as_of_ts`；`WakeRuntime.last_reference`；`tactical_context._closed_bars`；`test_decision_instant_is_one_clock_for_both_wake_paths` + `test_reusing_the_mtf_expression_on_interval_rounds_marks_every_frame_stale`（反例） |
| 2 | [P0-2] `horizon_scale` 纯函数不开放配置；`moved()` 吃 `WakeFacts`；三键随 wire；`max_skip_streak` 默认 5 且只在计数成功时推进 | ✅ `wake_reason.horizon_scale`（无对应 Settings 项）；T-22 校准表；T-24 含「5 秒 tick 不推进」 |
| 3 | [P1-1] 战术集合唯一真源 = `WakeRuntime`；`WindowOptions` 形参 5；两处调用点同批改；`WakeRuntime` 先于 `MarketWindow` 构造 | ✅ `session.py:__init__` 顺序 + 注释；`manager._validate_sizing` 已改 `options=`；G-9 |
| 4 | [P1-2] `describeTrigger` 三级优先；既有 6 条断言一字不改 | ✅ 既有 `describe` 块零改动，全绿（G-8） |
| 5 | [P1-3] `SignalContext` 早退门控补 `&& !tactical` + T-18 钉住 | ✅ `AiTradeTraceRound.tsx`；`keeps the whole 输入信号板 section alive on a tactical-only round` |
| 6 | [P1-4] 单 tick 至多 1 次取数 + `wait_for` 硬超时；T-20/T-21 绿 | ✅ 三闸门 + `_fetch_tactical`（见 D-1）；两用例绿 |
| 7 | [P1-5] 预填 `limit=min(300, window_bars+5)`；刷新按公式；T-23 绿 | ✅ `_SINGLE_PAGE_LIMIT_MAX=300`；`test_prefill_limit_stays_single_page_and_skips_unconfirmed_bars` |
| 8 | [P1-6] 补 `last_round_kind` / `last_reference`；guard status 与 atr 单入口；T-25 | ✅ 两个只读属性 + `note_guard_status` 挂在 `_refresh_profit_state` 唯一产出点；见 D-6（签名上做不到另取） |
| 9 | [P1-7] A-4 / A-4b 三档预算全部离线硬断言 | ✅ 见 D-11（三条 + 摊薄指标共四条断言） |
| 10 | G-1~G-9 全绿；后端全量串行、红灯集合不多于基线 | ✅ 见 §十一「验证记录」 |
| 11 | 提交纪律（行级暂存、不碰 `desktop/src/lib/config.js`） | ✅ 本节点不 commit；`desktop/src/lib/config.js` **零改动**（前缀白名单已覆盖 11 个新 env） |
| 12 | 偏离留痕 + P2-1 拍板记录 | ✅ 本节 D-1~D-12，P2-1 见 D-9 |

---

## 十、评审结论（Subtask #1）

### 结论：**有条件通过**

方案的骨架是对的，而且对得很具体：三层时间尺度的划分（中线 / 执行 / 战术）直接命中用户诉求第 1 条；
把 `trigger` 二值升成结构化 `wake` 对象、并让**同一个 `kind` 同时驱动守卫与措辞**，从结构上消灭了
「prompt 说没变、守卫认为变了」这类信任崩塌；逻辑全下沉到 `WakeRuntime`（不再喂已 1494 行的
`session.py`）、总闸关闭即整键缺席、回测显式列为非目标、`grep` 式展示点清点——这些都是本仓库反复
付过学费才形成的范式，方案沿用得相当准确。§0.4 记了 9 条我逐一核实为**真**的关键断言。

但**原稿有两处会让特性在主路径上自我抵消**，都不是风格问题：

1. **P0-1**：战术段的 `as_of_ts` 没定义时钟。会话在两条唤醒路径上给出的 `ts` 语义不同（根开盘 vs 墙钟），
   沿用既有 MTF 的表达式会让**每一个定时唤醒轮的每一个战术周期都被标成「数据陈旧」**，而系统 prompt
   偏偏写着「陈旧或缺失周期不得补猜」——我们会花整个特性把新证据取回来，再亲手命令模型忽略它。
2. **P0-2**：ATR 判据跨时间尺度失配。用「执行周期一根的 ATR」量「一个唤醒间隔的位移」，在 4H / 1D
   执行周期（**用户可选的合法配置**）下阈值高到不可能被越过，**空仓定时唤醒被永久跳过**——而定时轮
   正是轴 A 服务的对象，两轴互相取消。

两条都已在正文修复（§2.3.0 决策瞬刻单一真源；§2.4.1 `horizon_scale` √t 折算 + 连续跳过兜底），
并各配了先红后绿的用例（T-19 / T-22 / T-24）。另 7 条 P1（同源律、`describeTrigger` 优先级、渲染早退
门控、盯市延迟、取数 limit、`WakeRuntime` 公开面、预算口径）亦已逐条落到正文。8 条 P2 只记录，不阻塞。

### 放行条件（Subtask #2 必须逐条落实，缺一即视为未完成）

1. **[P0-1]** `_decision_as_of_ts` 是本轮决策瞬刻的**唯一**来源，战术段与 `changeSinceLastRound` 都只用它；
   `reference` 存 `asOfTs` 而非 `time.time()`；`closedBars` 按**收线时刻**计数。**T-19 必须含反例断言**
   （误用 `ts + BAR_SECONDS[bar]` 会让定时轮 `stale=True`），把这次的坑变成未来的护栏。
2. **[P0-2]** `horizon_scale` 落地为纯函数、**不开放为配置项**；`moved()` 改吃 `WakeFacts`（守 ≤5 形参）；
   `horizonScale` / `moveThresholdAtrEff` / `skipStreak` 三键随 wire 下发；`ai_trade_wake_max_skip_streak`
   默认 5 且**只在 `_count_wake_skip` 计数成功时**推进（T-24 含「5 秒 tick 不推进」的断言）。
3. **[P1-1]** 战术周期集合的唯一真源是 `WakeRuntime`；`MarketWindow` 经 `WindowOptions` 接收已解析的
   tuple、形参保持 5 个；`session.py:174` 与 `manager.py:595` 两处调用点同批改完（G-9）。
   `WakeRuntime` 必须在 `MarketWindow` **之前**构造。
4. **[P1-2]** `describeTrigger` 严守「信号板边沿 > `wake.kind` > `trigger`」三级优先；
   `aiTrade.test.ts` 既有 6 条断言**一字不改**且全绿（G-8）。
5. **[P1-3]** `SignalContext` 的整段早退门控补 `&& !tactical`，并由 T-18 用「仅含 tacticalContext 的轮次」
   钉住（这是本仓库 RV-2 的原型复发，不容再犯一次）。
6. **[P1-4]** 战术取数 = 单 tick 至多 1 次 + `asyncio.wait_for(ai_trade_tactical_fetch_timeout_s)` 硬超时；
   T-20/T-21 绿。**盯市循环的延迟不得为了取数而让步**——它守的是真实持仓。
7. **[P1-5]** 预填 `limit = min(300, window_bars + 5)`（**必须 ≤300 以免触发 history-candles 翻页**），
   刷新 limit 按 §2.2 公式；T-23 绿。
8. **[P1-6]** `WakeRuntime` 补 `last_round_kind` / `last_reference` 两个只读属性；guard status 与 atr
   只有 `note_guard_status` / `note_atr` 一条入口，`WakeFacts` 由运行时回读，调用方不得另取（T-25）。
9. **[P1-7]** A-4 / A-4b 三档预算上限（brief 100% / repeat 115% / full 135%）全部作为离线硬断言执行；
   §1.2 的口径与 A-x 一致。
10. **零回归门禁 G-1~G-9 全绿**方可提交；后端全量必须 `python -u -m pytest services/quant/tests -q > log`
    **串行**跑（共用临时 DB，并发会挂住），红灯集合不多于已知基线（7 个 `factor_library_expansion` 孤儿
    + `test_agent_ws` 顺序 flake）。
11. **提交纪律**：工作树含多个在制特性，`config.py` / `.env.example` 为混合文件，须按 hunk 行级精确暂存
    （Windows 下 patch 文件以 **bytes** 读写，`write_text` 会翻 CRLF 弄坏 patch）；**严禁 `git add -A`**；
    **严禁**为本特性触碰 `desktop/src/lib/config.js`（前缀白名单已覆盖，见 P2-7）。
12. **偏离必须留痕**：若与代码现实冲突而偏离本方案，逐条写进 §九「原方案 → 实际做法 → 理由」，
    不得静默偏离。P2-1（系统 prompt 11 形参）无论选 (a) 还是 (b) 都要在 §九留一句拍板记录。

### 未纳入本轮、但建议后续单独立项

- 诊断书**办法四**的真正补跑队列（本轮只做到「可见」）与**办法五**的信号驱动唤醒（需影子 broker
  无副作用预演 + 防抖），方案已正确列为非目标，评审同意。
- R-13 记录的既有 MTF 定时轮 `as_of_ts` 存量偏差（修它要同批更新 G-1 基线 sha256）。
- 用户诉求的终局验证只能靠数据：`wakeKind` 落进 `explain_json` 后，应在跑满一段样本后按唤醒来源切开
  胜率，用它决定「哪种唤醒值得留」。这一步不属本轮实现范围，但它是本轮归因设计存在的全部理由。

---

## 十一、实施与验证记录（Subtask #2）

### 11.1 落地清单（全部已写入磁盘，**未 commit**——提交是下一节点的职责）

**后端新建（5）**：`ai_auto/wake_reason.py`(159) / `ai_auto/tactical_context.py`(257) /
`ai_auto/wake_runtime.py`(233) / `ai_auto/wake_text.py`(253) / `ai_auto/market_text.py`(50，D-13)。
**后端改（8）**：`market_window.py`（`WindowOptions` + 三闸门战术窗口）、`wake_guard.py`（并列新增
`should_skip_wake`，既有函数**一字未改**）、`session.py`、`prompts.py`、`trace.py`、`manager.py`、
`config.py`（+10 项）、`.env.example`（+1 段）。
**后端测试新建（4）**：`test_ai_trade_tactical_context.py`(27) / `test_ai_trade_wake_reason.py`(20) /
`test_ai_trade_wake_text.py`(21) / `test_ai_trade_wake_context_session.py`(19)。
**后端测试改（7）**：`test_ai_trade_market_window.py`(+8) / `test_ai_trade_prompts.py`(+13) /
`test_ai_trade_wake_interval.py`(+2) / `test_ai_backtest_runner.py`(+1) + 三个受签名变更波及的文件
（D-10，**断言本体一字未改**）。`test_ai_trade_wake_guard.py` **零改动**（G-3 活证据）。
**前端改（4 + 3 测试）**：`aiTradeTypes.ts` / `store.ts` / `aiTrade.ts` /
`components/Agent/AiTradeTraceRound.tsx` + `aiTrade.test.ts` / `aiTradeTrace.test.ts` /
`AiTradeAgentTrace.test.tsx`。
**文档**：`.claude-index/config.md` 新增「显式记录的阈值偏离」小节（D-9）。
**零改动（刻意）**：`desktop/src/lib/config.js`（前缀白名单已覆盖 10 个新 env；该文件当前含他人
未提交改动，本节点未触碰）、`services/quant/app/db/**`（G-5：`git diff` 为空）。

### 11.2 门禁实测

| 门禁 | 结果 |
|---|---|
| G-1 | 三总闸全关 ⇒ 四键整键缺席、决策 prompt 与系统 prompt 逐字节回退（`test_g1_*` 两条 + `test_all_three_switches_off_leaves_the_payload_free_of_new_keys`）✅ |
| G-2 | 回测 payload 无四键、`backtest.py` 源码不含任何战术符号（`test_g2_backtest_payload_never_carries_wake_or_tactical_keys`）✅ |
| G-3 | `test_ai_trade_wake_guard.py` **零改动**全绿 ✅ |
| G-4 | `test_ai_trade_wake_interval.py` 既有断言全保留、全绿（17 passed）✅ |
| G-5 | `git diff -- services/quant/app/db` **为空**（比 grep 逐行比对更强的判据）✅ |
| G-6 | 后端全量串行 `python -u -m pytest tests -q`：**1856 passed / 8 failed**，红灯集合 = 已知基线（7 个 `factor_library_expansion` 孤儿 + 1 个 `test_agent_ws` 顺序 flake），**零新增红灯** ✅ |
| G-7 | 前端 `NODE_ENV=test npx vitest run`：**119 文件 / 1260 tests 全绿**；`npx tsc --noEmit` 仅剩 `WeightBatchArena.test` 的存量错误，**无新增** ✅ |
| G-8 | `aiTrade.test.ts` 既有 6 条 `describeTrigger` 断言**一字未改**且全绿（wake 未抢先的活证据）✅ |
| G-9 | `MarketWindow(` 的两处生产调用点均已走 `options=WindowOptions(...)`；无任何调用点直传 `context_enabled=` ✅ |

### 11.3 验收指标实测

- **A-1**：定时轮 payload 带 `tacticalContext.frames`（由粗到细 `["5m","1m"]`）、各带 `closedBars`
  与顶层 `changeSinceLastRound` —— `test_interval_round_carries_wake_reason_and_fresh_tactical_evidence`。
- **A-2**：prompt 首行「唤醒原因=…」、`decision.wake.kind`、轨迹面板中文标签**三者同源**（都读同一个
  `wake` 对象）。
- **A-3**：`explain.meta.wakeKind` 仅落 `atomic` / `reverse`；`profit_guard` 平仓**无**此键。
- **A-4 / A-4b**：生产形状夹具 1284 字基线 ⇒ `brief` **99.4%**（≤100%）、`repeat` **97.3%**（≤115%）、
  `full` **130.2%**（≤135%）；**每根 K 线摊薄 100.6%**（1 个 full + 9 个 repeat）。四条均为离线硬断言。
- **A-5**：单 tick 至多 1 次 REST（T-20）+ 8s 硬超时（T-21，实测挂住的取数在超时内返回且不推进锚点）；
  稳态 `("1m","5m")` ⇒ 1.2 req/min/会话。
- **A-6**：三总闸全关逐字节回退（G-1/G-2 + `test_reason_switch_off_leaves_the_decision_event_wake_free`）。
- **A-7**：唤醒判据明细行（`wake-reason`，D-14）+ 战术块与既有中线背景块同构（同 `text-[11px]` / 同 `var(--muted)` / 同 `flex-col gap-0.5`），
  未引入新组件语言；缺失周期如实标注。

### 11.4 留给下游的三件事

1. **提交纪律**：`config.py` / `.env.example` 是混合文件（含他人在制改动），须按 hunk 行级精确暂存；
   **严禁 `git add -A`**；`desktop/src/lib/config.js` 的改动**不属本特性**，不得混入。
2. **灰度建议（R-1）**：战术周期噪音诱发过度交易是本特性最大的反向风险。建议先在纸面 / 小预算会话
   跑满一日，按 `explain_json.meta.wakeKind` 切开胜率后再放开——这正是本轮归因设计存在的全部理由。
3. **存量欠账**：`session.py` 1583 行、`prompts.py` 775 行（贴线），下次动这两个文件时应继续下沉；
   `build_ai_trade_system_prompt` 的 11 形参已在 `.claude-index/config.md` 登记，建议下次一次性还清。

---

## 十二、终审记录（Subtask #3）

> 逐条对照 §三变更计划核实覆盖面、逐文件读 diff 核实回归风险与风格一致性，并实跑全量门禁。
> 结论：**通过，已直接修完全部发现**（不留 TODO）。

### 12.1 终审发现与处置

| # | 位置 | 问题 | 处置 |
|---|---|---|---|
| **RV-1** | `wake_text.format_digest_repeat` / `prompts._digest_lines` | **压缩文案指向一段可能不存在的证据**。重复轮的压缩执行周期证据行**无条件**以「；本轮最新变化见战术周期」结尾，但三个总闸互相独立（§4.1 缺席矩阵），「`ai_trade_progressive_disclosure=True` + 战术段缺席」是**可达**组合：既可由 `ai_trade_tactical_context=False` 配出，也会在**运行期降级**时自然发生——`1m` 执行周期的 `tactical_timeframes` 恒返回 `()`（§2.1 表末行），以及全部战术周期取数失败时 `build_tactical_context` 返回 `None`。此时模型被压缩掉了中线明细与三行证据，却被指向一段根本没渲染的段落，正是本方案 §2.8 措辞纪律要杜绝的「说的和做的不一致」。对照证据：系统 prompt 的 `_tactical_discipline` 已经是条件化的（`tactical_bars_text` 为空即不渲染），只有这一处漏了同一条件，属实现疏漏而非设计取舍。 | `format_digest_repeat` 增**必填**关键字 `has_tactical`（刻意不给默认值：默认 `True` 会让未来的新调用方静默复现本缺陷），指路句仅在真有路时追加；`prompts._digest_lines` 传 `bool(payload.get("tacticalContext"))`。补两条断言：`test_repeat_round_digest_never_points_at_an_absent_tactical_section`（单元）与 `test_repeat_disclosure_without_tactical_never_points_at_an_absent_section`（prompt 端到端）。 |
| **RV-2** | `.env.example` 战术窗口根数注释 | 注释写「≥60 才够 `classify_regime`」，而 `Settings` 的实际约束是 `ge=40`（§4.2 明定）——文档与校验器口径不一致，用户按注释理解会以为 40~59 被拦截。 | 改为如实说明：默认 120，调到 60 以下时 `classify_regime` 判不出体制、战术帧只剩证据摘要，是**可接受的降级**而非被拒的配置。校验器不动（§4.2 为准）。 |
| **RV-3** | `market_text.py` / `wake_reason.py` | 风格：前者 `from __future__` 与首个 `def` 之间只有 1 个空行（PEP 8 要求 2）；后者 `[name for name in fired[1:]]` 是恒等推导式。 | 各修一处（`list(fired[1:])`）。 |

### 12.2 核实为正确、**不改**的关键点（留证，防未来误判为缺陷）

- **`session._last_round_bar_index` / `_last_round_px` 现已「只写不读」**：F4c 守卫的读取方已收敛到
  `WakeRuntime`。**刻意保留**——`test_ai_trade_wake_interval.py:360` 对它有既有断言，删除即破 G-4；
  且两份副本在正常流下同值、会话侧那份无任何消费者，不构成漂移面。下次动 F4c 时一并清理。
- **`note_round_start` 在 `_build_decision_payload` **之后**调用**：看似「断面冻结晚了一步」，实为必需
  ——`changeSinceLastRound` / `closedBars` 参照的是**上一**轮断面，先冻结会让每轮的增量恒为 0。
  代价是 payload 装配抛异常时断面不推进，后续轮被判为 `bar_advanced` ⇒ 恒不跳过（**fail-open**，
  与本层极性一致）。
- **`format_multi_timeframe_repeat` 返回 `list[str] | None`**（§2.8 声明的是 `list[str]`）：`None` 表示
  「交回既有全量渲染」，正是 P2-4 要求的「`frames` 为空时原样复用降级行」的最省实现——降级文案因此
  只有一处真源。属**更严格**地满足评审条件，不是偏离。
- `_absorb_tactical` 的入窗 / 锚点推进与既有 `maybe_refresh_htf` **逐行同构**（含 `existing` 去重集合与
  `latest_raw.ts + secs` 锚点），故「交易所把未收线当前根作为最新一条返回」这一前提下，稳态恰好是
  每收一根请求一次。
- `_monitor_tick` 传给 `maybe_refresh_tactical` 的 `now_ts` 是 `int(time.time())`（**墙钟**），锚点闸
  口径正确；`feed_candle` 传的 `decision_as_of_ts` 是该根收盘时刻，同为墙钟口径。

### 12.3 门禁复跑（终审实测，非引述上游）

| 门禁 | 终审实测 |
|---|---|
| G-6 | 后端全量串行 `python -u -m pytest services/quant/tests -q`：**1858 passed / 8 failed / 1 skipped**。8 条红灯**逐条对账为基线**（见下方 12.3.1），**零新增** ✅ |
| G-7 | 前端 `NODE_ENV=test npx vitest run` 全绿；`npx tsc --noEmit` 仅剩 `WeightBatchArena.test` 存量错误 ✅ |
| G-3 / G-4 / G-8 | `test_ai_trade_wake_guard.py` 零改动全绿；`test_ai_trade_wake_interval.py` 与 `aiTrade.test.ts` 的既有断言 `git diff` 实测**零删除行** ✅ |
| G-5 | `git diff -- services/quant/app/db` 为空 ✅ |
| G-9 | `grep` 实测 `MarketWindow(` 仅 2 处生产调用点，均走 `options=WindowOptions(...)` ✅ |

#### 12.3.1 红灯基线对账（**用干净 HEAD worktree 实测**，不靠记忆引述）

§11.2 记的基线是「7 个 `factor_library_expansion` 孤儿 + `test_agent_ws` 顺序 flake」。终审首次全量得
7 红、复跑却得 **8** 红——多出的
`test_ai_backtest_manager.py::test_profit_rule_is_forwarded_to_spec`。它单跑必绿，失败点是
`AiBacktestManager.start` 第一行的 `resolve_active_provider(...) → None ⇒ AGENT_NOT_CONFIGURED`，
**先于**任何 payload / prompt 装配，与本特性无任何调用关系。但「看起来无关」不是证据，故建
`git worktree add <tmp> HEAD --detach` 跑了一份**完全不含本特性**的全量：

```
HEAD（干净）  : 1 failed, 1744 passed —— 唯一红灯就是 test_ai_backtest_manager 那条
本特性工作树  : 8 failed, 1858 passed —— 同一条 + 7 个 factor_library_expansion
```

结论：该红灯是**既有基线**（共用临时 DB 下的跨用例顺序敏感，时快时慢地翻转），§11.2 的基线描述
不完整；7 个 `factor_library_expansion` 在 HEAD 上根本不出现，因为那个测试文件属另一个在制特性、
至今未跟踪。**本特性零新增红灯**成立。附带教训：红灯基线只能靠干净 worktree 实测对账，不能引述
历史记录——基线本身会随工作树里其他人的在制改动漂移。

### 12.4 刻意未纳入本次提交

- `.claude-index/config.md`（D-9 的阈值偏离登记）：`.claude-index/` 整目录在本仓库**从未被跟踪**
  （与 `CLAUDE.md` 同属 project-indexer 生成物）。只把其中一个文件塞进本特性提交会造成生成物的
  部分入库，故留在工作树。P2-1 的拍板记录本身已随本 spec 的 §九 D-9 入库，放行条件 12 不受影响。
- `desktop/src/lib/config.js`、`.env.example` 的 `AI_TRADE_ENABLED=true` 行、`config.py` 的
  `ai_trade_enabled` 默认值翻转：均属**他人在制**的「AI 交易总闸默认开启」特性，已按 hunk 行级剔除。
