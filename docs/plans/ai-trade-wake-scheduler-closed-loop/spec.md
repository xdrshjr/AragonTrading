# AI 自动交易 · 唤醒调度闭环（ai-trade-wake-scheduler-closed-loop）设计方案 v2

> 迭代定位：本轮是 loop `tge-5ec7c9d3` 的**第 2 轮**。第 1 轮已交付并合入
> `commit a7ed70c`（`docs/plans/ai-trade-wake-aware-context/spec.md`），解决的是「唤醒之后
> **喂什么**」——战术周期证据 + 结构化唤醒原因。本轮解决的是它的另一半：「**何时**唤醒、
> 唤醒**丢了怎么办**、唤醒**值不值**」，并把第 1 轮埋下的归因字段真正**读出来**。
>
> 先读 §2.0（上一轮已交付的地基），它决定了本轮哪些东西**不能重造**。
>
> **v2（评审后）**：2 条 P0 + 9 条 P1 已在正文就地修复，见下方 §评审记录。轴 C 的判据在 v1 里
> 度量错了对象（见 P0-1），修法已写进 §2.3；轴 C 总闸默认改为**关闭**（P1-6）。

---

## 评审记录（Design Review，v1 → v2）

> 评审方法：逐条断言对照 `a7ed70c` 之后的真实代码核实（`session.py` / `wake_reason.py` /
> `wake_guard.py` / `wake_runtime.py` / `wake_text.py` / `market_window.py` / `signals.py` /
> `engine/simulator.py` / `tactical_context.py` / `schemas.py` / `api/ai_auto.py` +
> `apps/web` 的 `store.ts` / `aiTrade.ts` / `aiTradeTypes.ts` / `autoTradePnl.ts` / `types.ts` /
> `AiTradeTraceRound.tsx` / `AiTradeStatsLive.tsx` / `AiTradeStatsDetail.tsx`）。
> **全部 P0 / P1 已在正文对应小节就地改完，本节只留问题陈述与修法索引，不留待办。**

### P0（阻断级：不改则特性不成立或会把假证据送进真实资金决策链）

**P0-1 · 轴 C 度量错了对象：`stance` 在同一根上不可能因为 `on_candle` 而改变。**
`signals.step_strategy_shadow` 的四阶段顺序是
`set_risk → fill_pending → apply_risk → mark → on_candle → submit`，而
`SimBroker.submit`（`engine/simulator.py:206`）**只把信号排进 `_pending`、次根开盘才成交**。
故 `StrategySignalView.stance = shadow.pos.side` 在预演这一步只可能因两件事变化：
① 上一根**已确认根**排队的委托在本根开盘成交（该事实在本根一开盘就已确定、整根不再变），
② `apply_risk` 用本根 `h/l` 触发了止损/止盈。
v1 描述的「价格上穿均线时策略喊多、两秒后跌回去」这种 repaint 根本**改不动 `stance`**——
它只改 `latest_signal`。于是 v1 的轴 C 会：
- 对真正想抓的「立场正在翻转」**完全失明**；
- 在每一根「上一根排过队」的 K 线上，于开盘后 `min_gap_s` 处**必然**多烧一次 LLM
  （签名整根恒定 ⇒ 必过防抖），直接违反 A-4 的花费中性硬性判据；
- 让 prompt 对模型说「这可能 repaint」，而该证据其实是确定性的——正是本方案自己要杜绝的
  「说的和做的不一致」。

**修法（§2.3.3 / §2.3.5 已改）**：判据从 `stance` 换成**投影立场**
`projected_stance(view) = {open_long→long, open_short→short, close→flat, none→stance}`
（纯函数，零改 `signals.py` / `simulator.py`）。基线取**上一确认根板的投影**，预演取**预演板的
投影**。这样 ① 的排队成交被基线自然吸收（不再每根多烧一次），② 与真正由本根 `on_candle`
产生的新边沿都被如实捕捉，且后者确实会 repaint ⇒ 防抖/锁存/`min_gap` 这套机器才名副其实。

**P0-2 · 预演输入未做任何合法性与新鲜度校验，会凭空制造翻转证据。**
两条已核实的真实路径：
- `session.feed_candle` 的未确认根分支（`session.py:565-571`）**不校验** `candle.ts` 与
  `_window.last_confirmed_ts` 的关系。确认根落地后再到达一条同 ts 的未确认推送是可能的，
  v1 会把它作为 `_live_row` 追加到 `rows` 末尾 ⇒ 帧里出现**两根同 ts**的 K 线 ⇒ 指标在重复根
  上计算 ⇒ 产生一个纯属数据缺陷的「翻转」，并据此起一轮真实资金决策。
- `_stale_degraded` 的判据是 `_last_candle_wall`（**只在确认根更新**，`session.py:536`），
  1D/12H 执行周期下它几十小时都不会跳。WS 静默但尚未触发 stale 时，`_live_row` 会**冻结不变**，
  而 `FlipDetector` 仍按墙钟累积 `heldS` / `samples` ⇒ 输出「该翻转已连续保持 45 秒」——
  数据一秒都没动过。这正是上一轮 D-1 已经付过学费的同款错误（保护层新鲜度不得读
  `_last_candle_wall`）。

**修法（§2.3.3 / §2.3.5 已改）**：`preview_snapshot` 增两道前置闸——
① `live_row["ts"] <= last_confirmed_ts` ⇒ 返回 None（不预演）；
② `now_wall - _last_px_wall > ai_trade_signal_wake_stale_s`（新配置，默认 90s）⇒ 返回 None；
并且 `FlipDetector.observe` 只在 `_live_row` 的 `(ts, c, h, l)` 相对上次采样**确实变过**时才计
一次 sample——「保持了 45 秒」必须是 45 秒里真的看了新数据。

### P1（必须修：正确性 / 可执行性 / 安全极性）

| # | 问题 | 修法落点 |
| --- | --- | --- |
| P1-1 | **`droppedIntervals` 的算术自相矛盾。** §2.8 时间线与 T-1 断言「379 秒在飞轮 ⇒ `dropped_intervals == 1`」，但按 §2.1.2 自己定的节流（`now - last_noted_wall >= min_gap_s`，`min_gap_s = wake_interval_s = 120`），379 秒内应计 **3** 次。照 T-1 写测试会逼出一个错误实现，且这个数会**原样进 prompt**（「期间 3 次定时唤醒被丢弃」）。另有两处未定义：`waitedS` 的口径、`total_drops` 的自增点（A-6 的恒等式因此不可验证）。 | §2.1.2（口径三条钉死）、§2.8（时间线数字修正）、§6.1 T-1、§6.3 A-6 |
| P1-2 | **`_preview_inflight` 在超时时被 `finally` 清掉，而工作线程仍在跑**——与 R-6 宣称的「同一时刻至多一个预演」直接矛盾。另外 AI-trade 是**分组多标的**运行（N 个会话各跑各的 `_monitor_tick`），v1 通篇没有分组维度：N 个会话会同时向默认线程池投 CPU 密集预演。 | §2.3.2（shield + done-callback；模块级信号量 + 按 `session_id` 稳定错峰）、§七 R-6 |
| P1-3 | **G-5 的「`session.py` 净增 ≤55 行」在算术上不可能达成**：v1 自己的 `_maybe_signal_flip_wake` 草图就 39 行、`_maybe_catchup_wake` 20 行，合计已 59，而 §2.6 又宣称「两个 ≤20 行的私有方法」。 | §2.3.5（异步编排下沉 `signal_preview.PreviewRunner`）、§2.6、§6.2 G-5（给出逐项预算） |
| P1-4 | **G-7 是一条不可能失败的门禁**：注入「sleep 2 秒的假预演」——`sleep` 会**释放 GIL**，而真实预演是 GIL 密集的纯 Python/pandas 计算。用 sleep 假件测「盯市 tick 不被拖停」测不到真问题。 | §6.2 G-7（改 CPU 密集假件 + 如实写明 `to_thread` 对 GIL 密集负载只能交错、不能真并行） |
| P1-5 | **克隆成本无界且大半是浪费。** `SimBroker.trades` / `equity_points` 随会话长度线性增长（`mark` 每步一条；暖机就先塞 ≤1200 条），而 stance 判定**从不读它们**；`factor_score_strategy._breakdown` 持有整张 DataFrame，而预演的 `prepare` 一进去就把它覆盖掉。v1 却要求「deepcopy 整个 spec 与 shadow、不做浅拷贝优化」，且这一步在**事件循环线程**上每 15 秒跑一次。 | §2.3.3（`clone_shadow` 显式字段克隆 + 等价门禁 T-25；克隆耗时预算 + 超预算自动降级） |
| P1-6 | **旋钮的安全极性反了两处**：`ai_trade_wake_backlog_max_chain=0` 与 `ai_trade_wake_position_watch_max_skip_streak=0` 都被定义成「不封顶」——一个是无限连烧 LLM，一个是持仓期无限期不研判；两者的「关闭」语义各自的总闸已经提供，不需要再留一条无上限的路。另外**轴 C 首发默认开启**与本仓库既有做法相悖（signal-prior 那轮三总闸默认全关），而轴 C 是唯一会占用守着真实资金的 5 秒 tick 的轴。 | §4.2（`ge=1` 下限；`ai_trade_signal_wake_enabled` 默认 `False` + 晋级条件） |
| P1-7 | **A-4 / A-2 不可执行。** A-4 要求「24 小时模拟回放」，而 §1.3 非目标明写不碰 `ai_auto/backtest.py`，仓库里**不存在**能跑实盘调度语义的回放器。不可执行的验收 = 没有验收。 | §6.3（改写成基于既有会话测试夹具 + 合成时钟的确定性轮数对拍） |
| P1-8 | **§2.7.1 事实 ①  与代码不符**：`build_atomic_explain`（`live_auto/explain.py:63`）用 `side` 分支同时构造**开仓与平仓**解释，`build_reverse_explain`（同文件 `:91`）产出的是反手的**平仓腿**（`side="close"`）。故「`atomic`/`reverse` ⇒ 开仓腿」不成立，`wakeKind` 在 AI 主动平仓腿上同样会出现。结论（按开仓腿归因）仍正确，但**理由错了**，且下游节点会引用它。 | §2.7.1（改写事实 ①）、§6.1 T-16（补反手往返用例） |
| P1-9 | **轴 D 的前端接线点与实际代码不符 + 漏了一个已知复发的渲染门控。** ① `AiTradeStatsDetail` 并**没有**用 `buildLedger`，它在成员子组件里逐成员调 `buildRoundTrips`（`AiTradeStatsDetail.tsx:233`）⇒ 组级卡片拿不到 trips；② `AiTradeStatsLive` 的 useMemo 只返回 `visible/summary/bounds/fullSummary`，**未返回**未筛选的 `ledger`；③ `AiTradeTraceRound.tsx:560-572` 的 `SignalContext` 早退门控是上一轮 RV-2 的原址，新增 flip / backlog 展示块若不进这个门控，只有补跑信息的轮次会整段空白。 | §2.7.3、§3.3、§2.4 镜像点清单（补第 12、13 条） |

### P2（记录，不阻塞放行）

- **P2-1** §2.5 的 `_wake_signal_flip` 代码块里 f-string 中间有一个**字面换行**，原样复制即 `SyntaxError`。已在正文改成合法的隐式拼接。
- **P2-2** G-5 写「四个新建后端模块」，而 §3.1 只列了三个。已统一为三个（`PreviewRunner` 归入 `signal_preview.py`）。
- **P2-3** `describeTrigger` 直接返回 `WAKE_LABELS[kind]` 这个**共享常量对象**（`aiTrade.ts:1017`）。追加「· 补跑」必须返回新对象，就地改写会污染全局标签表。已写进 §3.3。
- **P2-4** `_maybe_signal_flip_wake` 的守卫在单个表达式里放了 6 个布尔条件，直接违反 CLAUDE.md §六「超过三个即抽函数」。已抽为 `_preview_ready()`。
- **P2-5** 被记为 drop 的定时唤醒里，含一部分「即便不在飞也会被守卫跳过」的心跳轮，会让 `wakeDrops` 偏高。保留现状（`plan_catchup` 会用同一个分类器把它们判成不补跑），但已在 §4.3 写明该计数的口径是「**到点却撞上在飞轮**」而非「本该真的唤醒」。
- **P2-6** `signal_wake_min_samples` 允许取 1，与 `hold_s=0` 组合即「零防抖」，正是 A-3 明令禁止的形态。已把下限提到 `ge=2`。
- **P2-7** `skipStreak` 被空仓与持仓两条上限共用，会话在两态之间来回时计数会混。已在 §2.2.3 写明：`note_round_start` 每个真实决策轮归零，故混用窗口最长只有一个唤醒周期；不新增计数器（新增会让 `wake_skips` 出现第二个口径）。
- **P2-8** §2.0 的行号引用有一处偏移（`_WAKE_ATTRIBUTED_KINDS` 实际在 `session.py:101`，98 是其注释首行）。其余行号逐条核对无误。

### 核实为**正确**的关键断言（留证，防未来误判为缺陷）

1. `wake_reason.classify_wake` / `horizon_scale` / `wake_guard.should_skip_wake` 的语义、行号与 v1 描述一致；`heartbeat` 确是四条的合取，守卫直接读 `kind` 不重算。
2. `session.py` 确为 **1583 行**；`_maybe_interval_wake:453`、`_count_wake_skip:487`、`feed_candle` 的 `evaluate_signal_board:590` 早于 `if self._deciding:595`——**影子 broker 先于丢弃判断步进**，故补跑时 `_last_board` 恒最新，无需回补步进。
3. `_decision_as_of_ts`（`session.py:1197`）确按 `trigger` 分支；补跑轮取 `"interval"` 以避免战术帧被整体标 stale，是**对的**（上一轮 P0-1 的教训）。
4. `MarketWindow._absorb_tactical:313` 确只收 `confirm` 根；战术窗口与 `_htf_bars` 集合不可能相交。
5. `策略 on_candle 就地改写内部状态`一表属实（`momentum_breakout._peak/_trough`、`factor_score_strategy._thresholds/_breakdown` 等），「真身上预演再回滚」确不可行。
6. 利润保护 `note_guard_status` 确是唯一入口（`session.py:1064-1076`），且 `profit_rule is None` / 空仓一律写 `None` ⇒ 轴 B 在保护层未装配时**天然不跳过**（fail-safe 极性正确）。
7. `trace.py:119` 的 `"wake": payload.get("wake")` 是整对象透传 ⇒ `backlog` / `flip` 无需新形参即可上轨迹与 WS。
8. `store.patchAiTradeMemberStatus` 用展开合并（`store.ts:864-873`）、`appendAiTradeMemberTrade` 整对象入列 ⇒ 新 status 计数与 `trade.explain` **不会被前端静默吞掉**（这是本仓库有前科的失效模式，本轮已复核为安全）。
9. 历史侧 `AiTradeHistoryMember.trades` 声明为 `list[dict]` 透传、`schemas.trade_out` 恒含 `explain` 键 ⇒ 轴 D 在历史页签**确实零后端改动**。
10. `.env` 前缀透传：`desktop/src/lib/config.js:72` 按 `AI_TRADE_` 前缀白名单注入 ⇒ 新配置项**确实无需改 desktop**。
11. `WAKE_LABELS` 声明为 `Record<WakeKind, …>` ⇒ 新增 kind 会被 **tsc 强制**要求补标签（镜像点 9 有编译器兜底）。
12. `tactical_context.disclosure_tier` 在 `position_side != "flat"` 时恒 `full` ⇒ 轴 B 跳掉的持仓轮本来就是最贵的轮次，省钱幅度的估计站得住。

---

## 一、概述

### 1.1 问题：唤醒调度这一层仍是开环的

用户的直观感受是「AI 自动交易不可靠、总在观望、还总亏」。诊断把它拆成四个问题，第 1 轮只
正面解决了其中的 ①（输入重复）与 ②（阈值不随波动率走）。剩下的三处仍然开环：

| 诊断条目 | 现状（a7ed70c 之后实测） | 本轮 |
| --- | --- | --- |
| 问题 ④ / 办法四：唤醒被丢弃 | `feed_candle` 撞上在飞轮 ⇒ **这根确认根的首次研判永久丢失**；`_maybe_interval_wake` 撞上在飞轮 ⇒ 静默 `return`，无计数、无待办 | 轴 A |
| 问题 ③：持仓轮空转 | `classify_wake` 判为 `position_watch`，而 `should_skip_wake` **只**跳 `heartbeat` ⇒ 持仓期间每个唤醒周期必烧一次 LLM，而退出已被 5 秒确定性层接管 | 轴 B |
| 办法五：信号驱动唤醒 | 完全未做。信号板只在确认根更新，两根之间策略立场即将翻转这件事**无人观测** | 轴 C |
| 办法二（读出侧） | `explain.meta.wakeKind` 已随 a7ed70c 落库，但**全仓库没有任何一处读它** ⇒「我赚钱的交易是哪种唤醒触发的」这个问题仍然答不了 | 轴 D |

四条里前三条决定「唤醒调度对不对」，第四条决定「你能不能验证前三条有没有用」。缺了第四
条，本轮就又是一次凭感觉调参——这正是诊断里那句「用数据说话，不靠猜」要防的。

### 1.2 方案要旨：四条正交的轴

**轴 A —— 唤醒补跑（办法四）。** 被在飞轮吞掉的唤醒从「静默消失」改成「记一笔待办」：
决策轮 `finally` 落地即用**同一个分类器**重新判一次，输入确实变了就立刻补跑一轮，并把
「本轮是补跑、上一轮期间丢了几次、其中几根 K 线收线没被研判」如实写进 `wake.backlog` 下发
给模型与前端。补跑链有硬上限，绝不背靠背烧 LLM。

**轴 B —— 持仓轮唤醒经济学（问题 ③）。** 允许跳过 `position_watch` 轮，但**只在确定性退出层
证明在岗时**：利润保护快照必须处于 `armed` / `breakeven_armed`（存在一条只收紧不放松的退出
线），且连续跳过数未触到一条比空仓更紧的上限（默认 3）。保护层 `idle` / `unavailable` 时
**绝不跳过**——那种局面下 AI 是唯一的退出路径。

**轴 C —— 信号驱动唤醒（办法五）。** 在未确认根上做**无副作用预演**：深拷贝一份策略与影子
broker，在克隆体上把当前这根未确认根走一步，得到「若这根就这么收线，信号板会变成什么样」。
只有当**投影净共识立场**（§2.3.1b：`stance` 叠加本根边沿信号后的立场——直接比 `stance` 会
既漏掉本根新边沿、又被上一根的排队成交反复误触发）发生翻转、且该翻转连续保持 `hold_s` 秒
（默认 45）与 `min_samples` 次（默认 3）**有效**观测（行情确实变过才算一次）后，才算数并
唤醒一次。预演的重活跑在 `asyncio.to_thread` 里、带硬超时，绝不
占用守着真实资金的 5 秒盯市 tick。翻转证据以 `provisional=true` 的**建议性**身份进 payload
——信号板本身、共识门禁、铁律层、edge 门禁**一律仍以确认根为准**，故最坏后果只是多烧一次
token，绝不可能让 repaint 直接开出一笔单。

**轴 D —— 唤醒来源胜率归因（办法二的读出侧）。** 第 1 轮已经把**开仓**成交的
`explain.meta.wakeKind` 写进了 `trades.explain_json`，而前端 `autoTradePnl.buildLedger`
产出的 `RoundTrip` 本来就**持有 `entry: Trade`**。因此按唤醒来源切胜率**不需要任何新的后端
统计**：一个纯前端聚合函数 + 一张卡片，同时服务「当前」与「历史」两个页签。刻意不在后端再
造一份统计口径——本仓库已经为「同一个量两处各算一次」付过学费。

### 1.3 目标 / 非目标

**目标**

1. 被丢弃的唤醒不再永久消失；「恰好错过关键那根 K 线」这一类不可靠感消除。
2. 持仓期间的无效唤醒显著减少，且减少的每一次都可解释、有上限、可一键回退。
3. 两根 K 线之间的策略立场翻转能被观测到并触发唤醒，且不被 repaint 噪音带偏。
4. 「哪种唤醒赚钱」在 UI 上可读，样本不足时诚实降级而不是给一个假的胜率。
5. 全部四轴各自有总闸；**全部总闸关闭 ⇒ payload / prompt / 事件 / DB 逐字节回退 a7ed70c 行为**。

**非目标**

1. 不改决策器、反思、铁律层、利润保护、仓位与 edge 门禁的任何判定逻辑。
2. 不改 AI 回测（`ai_auto/backtest.py`）。回测按确认根逐根驱动，没有墙钟、没有在飞轮、没有
   未确认根——本轮四轴在回测语义下**全部不成立**，硬塞进去只会制造一个与实盘不同构的第二套
   调度。这条与 a7ed70c 的同款取舍一致（见该 spec §1.3）。
3. 不新增 REST 端点、不新增 DB 表 / 列、不做数据迁移。
4. 不做「每个价格跳动都预演」。预演有节流、有超时、有退避，是**采样**而非流式。

---

## 二、技术设计

### 2.0 上一轮已交付的地基（**必读**：本轮不得重造）

实现者在动手前必须确认下列事实（本方案全部断言均已对 a7ed70c 之后的真实代码核实）：

| 事实 | 位置 | 对本轮的约束 |
| --- | --- | --- |
| 唤醒原因七态分类器已存在，`kind` 同时驱动跳过守卫与 prompt 措辞 | `ai_auto/wake_reason.py:108 classify_wake` | 轴 A/B/C 的「输入是否真的变了」**一律复用它**，绝不新写判据 |
| `heartbeat` 是「空仓+无挂单+无新根+未越阈」的合取，守卫直接读 `kind` 不重算 | `wake_guard.py:42 should_skip_wake` | 轴 B 加 `position_watch` 分支时保持同款纪律 |
| ATR 判据已按 `horizon_scale` 的 √t 律折算到唤醒窗口 | `wake_reason.py:59` | 本轮不碰折算；轴 A 的「输入变了没」直接吃 `should_skip` 的结论 |
| 会话侧逻辑一律下沉 `WakeRuntime`（`session.py` 已 1583 行，远超 800 行红线） | `wake_runtime.py` | 本轮新增状态机**全部**挂在 `WakeRuntime` 上，`session.py` 只加薄调用 |
| 跳过计数已按「每唤醒周期至多一次」节流（裸 tick 计数会虚计 ~36 倍） | `session.py:487 _count_wake_skip` | 轴 A 的丢弃计数**必须**沿用同一节流口径 |
| `explain.meta.wakeKind` 只打在 `atomic` / `reverse` 成交上 | `session.py:98 _WAKE_ATTRIBUTED_KINDS` | 轴 D 按**开仓腿**归因；保护层平仓不带该键是**设计**不是缺陷 |
| 影子 broker 在 in-flight 丢弃判断**之前**已经步进过 | `session.py:590` 早于 `:595` | 轴 A 补跑时 `self._last_board` 恒是最新确认根的板，无需回补步进 |
| `MarketWindow` 只收确认根（`_absorb_tactical` 显式过滤 `confirm`） | `market_window.py:313` | 轴 C 的未确认根**不入窗**，只作为预演的临时行传入 |

### 2.1 轴 A：唤醒补跑（新模块 `ai_auto/wake_backlog.py`）

#### 2.1.1 两个丢弃点与它们真正的代价

```
feed_candle          :595  if self._deciding: await self._emit_decision_skipped(...); return
_maybe_interval_wake :453  if (... or self._deciding or ...): return          # 静默，无计数
```

一次最坏 LLM 轮 ≈ 379 秒（4×60 决策 + 退避 + 3×45 反思）。在 1m / 5m 执行周期下这会吞掉
6 根确认根；在定时唤醒被用户关闭（`wake_interval_s = 0`）的会话里，这 6 根**永远**不会被
补看——下一次研判要等下一根确认根。即便定时唤醒开着，补看也要等下一个 5 秒 tick 且完全
依赖「定时唤醒恰好开着」这个偶然。

#### 2.1.2 数据结构（纯状态 + 纯函数，无 IO）

```python
# ai_auto/wake_backlog.py
@dataclass
class WakeBacklog:
    """在飞轮期间被丢弃的唤醒请求（轴 A）。只记账与判定，不做任何 IO / 不碰事件循环。"""

    dropped_intervals: int = 0     # 被丢弃的定时唤醒次数（按唤醒周期节流后的口径）
    dropped_bars: int = 0          # 被丢弃的**确认根**首次研判次数（每根真实计一次，不节流）
    first_dropped_wall: float = 0.0
    last_dropped_wall: float = 0.0
    last_noted_wall: float = 0.0   # 定时丢弃的节流基准
    total_drops: int = 0           # 会话累计（观测用，consume 不清零）
    total_catchups: int = 0
    ceiling_hits: int = 0
    chain: int = 0                 # 连续补跑链长度（非补跑轮启动即归零）

    @property
    def pending(self) -> bool:
        return self.dropped_intervals > 0 or self.dropped_bars > 0

    def note_interval(self, now_wall: float, *, min_gap_s: float) -> bool: ...
    def note_bar(self, now_wall: float) -> None: ...
    def wire(self, now_wall: float) -> dict: ...
    def consume(self) -> None: ...
    def reset_chain(self) -> None: ...
```

**三条计数口径（P1-1，必须逐字实现；含糊会同时污染 prompt 文案与 A-6 的恒等式）**：

1. **`note_interval` 的节流基准是 `last_noted_wall`，语义是「被抑制的、原本会发生的唤醒次数」**
   ——与 `session._count_wake_skip` 同款。`now_wall - last_noted_wall >= min_gap_s`
   （`min_gap_s = wake_interval_s`）才 +1 并把基准推到 `now_wall`，返回是否真的计了数
   （供调用方决定要不要推 status）。`last_noted_wall` 由 `consume()` **保留不清**（跨轮连续），
   只有 `note_round_start` 起真实决策轮时才随 `_last_decision_started_wall` 一起前移。
   > **数量级示例（v1 此处算错，勿沿用旧数字）**：`wake_interval_s=120`、在飞轮 379 秒 ⇒
   > 盯市 tick 会到点 ~75 次，但按上式只计 **3** 次（t≈120 / 240 / 360）。「3」正是这段时间里
   > **原本真的会发生**的定时唤醒次数——这才是能如实说给模型听的数。裸 tick 计数会记成 75。
2. **`note_bar` 不节流**：每一根被丢弃的确认根都是一件独立的、真实发生过的事。
   首次丢弃（无论哪一种）写 `first_dropped_wall`，每次丢弃写 `last_dropped_wall`。
3. **`total_drops` 的自增点唯一**：`note_interval` 返回 True 时 +1、`note_bar` 每次 +1。
   即 `total_drops ≡ Σ 计过数的定时丢弃 + Σ 确认根丢弃`，`consume()` 不清零。
   `status.wakeDrops` 与 `aiStats.wakeScheduler.drops` 都**只**读它 ⇒ A-6 的恒等式可验证。

- `wire(now_wall)` 产出下发给模型与前端的只读对象：

```python
{"droppedIntervals": 3, "droppedBars": 1, "waitedS": 259.4, "chain": 1}
```

`waitedS` 的口径是 **`now_wall - first_dropped_wall`**（自**首次**丢弃至今等了多久），
**不是**上一轮的研判耗时——会话没有把「轮耗时」记在 backlog 里，写成「上一轮研判耗时」就是
在给模型一个它无从校验的错数。§2.5 的补跑子句文案已按这个口径改写。

#### 2.1.3 判定：什么叫「输入确实变了」

诊断原话是「如果期间有过唤醒请求**且输入确实变了**，就立刻补跑一轮」。本方案**不为此新写
判据**，而是把这句话直接翻译成既有分类器的结论：

```
补跑 ⇔ backlog.pending
       and chain 未触顶
       and not WakeRuntime.should_skip(classify(现在的读数))
```

`should_skip` 为 False 意味着本次唤醒的 `kind` 不是可跳过的（`heartbeat` / 轴 B 的
`position_watch`），也就是「新根 / 挂单 / 浮盈跨档 / 价格越阈 / 立场翻转」至少成立一条——
这**就是**「输入确实变了」的定义，且与 prompt 文案、跳过守卫、观测计数共用同一个数。两处
各写一套判据是本仓库反复踩过的信任崩塌来源（a7ed70c §2.5 已为此收敛过一次）。

#### 2.1.4 补跑的落点：`_run_decision_round` 的 `finally`

```python
        finally:
            self._deciding = False
            self._maybe_catchup_wake()      # 轴 A：纯同步，check→claim 之间无 await
```

```python
    def _maybe_catchup_wake(self) -> None:
        """上一轮结束即补跑一轮（轴 A）。纯同步：与既有两处槽位认领同范式，无竞态。

        `finally` 在 `stop()` 取消决策任务时同样会跑，故 `state != "running"` 必须显式挡掉
        ——否则停止流程会在 flatten 之后凭空再起一轮真实资金决策。
        """
        if self.state != "running" or self._deciding or self._last_board is None:
            return
        now = time.time()
        ref_px = self._current_px()
        wake = self._wake.plan_catchup(self._wake_readings("interval", ref_px), now_wall=now)
        if wake is None:
            return
        self._deciding = True
        self._last_round_bar_index = self._last_bar_index
        self._last_round_px = ref_px
        self._last_decision_started_wall = now
        self._decision_task = asyncio.create_task(
            self._run_decision_round(self._last_board, self._last_bar_index,
                                     ref_px, int(now), wake))
```

`WakeRuntime.plan_catchup` 内部：

```python
    def plan_catchup(self, readings: WakeReadings, *, now_wall: float) -> dict | None:
        if not self._settings.ai_trade_wake_backlog_enabled or not self._backlog.pending:
            return None
        ceiling = self._settings.ai_trade_wake_backlog_max_chain
        if ceiling > 0 and self._backlog.chain >= ceiling:
            self._backlog.ceiling_hits += 1
            self._backlog.consume()          # 触顶即弃单，绝不让待办无限期堆着
            return None
        wake = self.classify(readings, now_wall=now_wall)
        if self.should_skip(wake):
            self._backlog.consume()          # 输入没真变 ⇒ 不补跑，也不留悬空待办
            return None
        wake["backlog"] = self._backlog.wire(now_wall)
        self._backlog.consume()
        self._backlog.chain += 1
        self._backlog.total_catchups += 1
        return wake
```

- **`trigger` 取 `"interval"`（墙钟口径）而非 `"candle"`**，这是刻意的：`_decision_as_of_ts`
  （`session.py:1197`）按 `trigger` 分支求决策瞬刻，`candle` 会返回「根开盘 ts + 一整根」。
  补跑发生在墙钟**当下**，用 `candle` 口径会让战术周期的 `ageSeconds` 凭空多出一整根、
  每个战术帧被标成 `stale`，而系统 prompt 明写「陈旧周期不得补猜」——等于把刚取回来的证据
  亲手命令模型忽略。这正是 a7ed70c 评审 P0-1 已经付过学费的坑，本轮不得复发。
- 补跑轮的 `kind` 由分类器如实给出（绝大多数是 `bar_advanced`），`backlog` 只是**加法**的
  旁证。刻意**不新增** `catchup` 这个 kind：「为什么值得唤醒」和「这次唤醒是补跑」是两件正交
  的事，把它们塞进同一个枚举会让七态表失去互斥性。
- `chain` 归零点在 `note_round_start`：`wake.get("backlog") is None`（即非补跑轮）时
  `self._backlog.reset_chain()`。

#### 2.1.5 两个丢弃点的改法

```python
    # _maybe_interval_wake：把 `_deciding` 判定挪到计时器之后，只对**真的到点了**的丢弃计数
        if (self.wake_interval_s <= 0 or self.state != "running"
                or self._stale_degraded or self._last_board is None):
            return
        now = time.time()
        if now - self._last_decision_started_wall < self.wake_interval_s:
            return
        if self._deciding:                   # 轴 A：到点却撞上在飞轮 ⇒ 记待办，不再静默丢弃
            if self._wake.note_interval_drop(now):
                await self._push({"type": "status", "data": self._status_payload()})
            return
```

```python
    # feed_candle：确认根的首次研判被丢弃
        if self._deciding:
            self._wake.note_bar_drop(time.time())      # 轴 A：先记待办，再推自解释事件
            await self._emit_decision_skipped(ts, board)
            return
```

`_emit_decision_skipped` 的 `wake` 桩对象补一个 `backlog` 键（既有三键不动）：

```python
                "wake": ({"kind": "candle_close", "trigger": "candle", "dropped": True,
                          "backlog": self._wake.backlog_wire()}
                         if self._wake.reason_enabled else None),
```

### 2.2 轴 B：持仓轮跳过（`ai_auto/wake_guard.py` 扩展）

#### 2.2.1 为什么 `position_watch` 是安全的跳过对象

`classify_wake` 的判定顺序保证：`kind == "position_watch"` 当且仅当

```
持仓中 且 非确认根轮 且 无新根 且 无挂单 且 保护层状态未跨档 且 价格未越阈（且无立场翻转）
```

也就是说，这一轮里**唯一**的新信息是「时间过去了几分钟」。而此期间的退出已由 5 秒确定性
层接管：利润保护棘轮（只收紧不放松）、会话级止盈止损、交易所侧 OCO。

#### 2.2.2 但必须有一条硬前提：确定性层证明在岗

利润保护层是 **fail-open** 的（见 `ai-trade-profit-protection` 的记录）：`unavailable` 只
表示「本层算不出保护线」，**不代表交易所侧有兜底**。若此时还跳过唤醒，就等于在一个既无
本地保护线、又可能无交易所侧挂单的持仓上停止一切研判——这是本轮唯一可能造成真实亏损的
路径，必须堵死。

判据：`wake["guardStatus"] in ARMED_STATUSES`（`armed` / `breakeven_armed`）。

#### 2.2.3 实现（形参聚合为 `SkipPolicy`，守 CLAUDE.md「形参 ≤5」）

```python
# ai_auto/wake_guard.py
@dataclass(frozen=True)
class SkipPolicy:
    """跳过守卫的全部旋钮（聚合结构体；镜像 WindowOptions / WakeReadings 的既有范式）。"""

    threshold_pct: float = 0.0
    threshold_atr_mult: float = 0.0
    max_skip_streak: int = 0                    # 空仓 heartbeat 的饿死上限（既有语义，0=不封顶）
    position_watch_enabled: bool = False        # 轴 B 总闸
    # 持仓轮的饿死上限。**刻意不复用「0=不封顶」这个既有极性**（P1-6）：持仓期无限期不研判是
    # 一个能亏真钱的状态，配置层已把它约束为 `ge=1`；「不跳过」由上面的总闸表达，不需要第二条路。
    position_watch_max_skip_streak: int = 3


def should_skip_wake(wake: dict, policy: SkipPolicy) -> bool:
    """结构化唤醒对象版守卫。判据**不在本函数里重算**——`kind` 已经是判定结果。

    两条可跳过的态：
    - `heartbeat`：空仓 + 无挂单 + 无新根 + 未越阈（既有语义，一字不改）。
    - `position_watch`（轴 B 新增）：持仓 + 上述四条同样成立，**且**利润保护快照处于
      `armed`/`breakeven_armed`。保护层 `idle`/`unavailable` 时 AI 是唯一退出路径，绝不跳过。
    """
    if policy.threshold_pct <= 0 and policy.threshold_atr_mult <= 0:
        return False
    kind = wake.get("kind")
    if kind == "heartbeat":
        return not _streak_exhausted(wake, policy.max_skip_streak)
    if kind == "position_watch" and policy.position_watch_enabled:
        if wake.get("guardStatus") not in ARMED_STATUSES:
            return False
        return not _streak_exhausted(wake, policy.position_watch_max_skip_streak)
    return False


def _streak_exhausted(wake: dict, ceiling: int) -> bool:
    """连续跳过是否已触到上限（True = 必须强制放行一轮，饿死兜底）。"""
    return ceiling > 0 and int(wake.get("skipStreak") or 0) >= ceiling
```

**关于 `position_watch_enabled` 与 `guardStatus` 的取值来源（已核实，实现者勿另取）**：
`wake["guardStatus"]` 来自 `WakeRuntime._guard_status`，其**唯一**写入点是
`session._refresh_profit_state`（`session.py:1064-1076`）——`profit_rule is None` 与空仓两种
情形都显式写 `None`。因此「利润保护层未装配」的会话里 `guardStatus` 恒为 `None`，轴 B
**天然一次都不会跳过**，无需额外守卫。这条 fail-safe 极性是轴 B 能安全默认开启的前提。

- `should_skip_interval_wake`（总闸关闭时的逐字回退路径）**一字不动**。
- `ARMED_STATUSES` 从 `.profit_guard` 导入——`wake_reason.py` 已经这么做，不新增依赖方向。
- `skipStreak` 是共用计数器（`note_round_start` 每个真实决策轮归零）。空仓与持仓的上限不同
  是刻意的：持仓时「什么都不做」的机会成本更高，故上限更紧（默认 3 vs 5）。
  **刻意不为持仓另立一个计数器**（P2-7）：那会让 `wake_skips` 出现第二个口径，而本仓库为
  「同一个量两处各算一次」付过学费。共用带来的唯一副作用是「会话在空仓/持仓之间切换时两条
  上限共读一个数」，而 `note_round_start` 每个真实决策轮归零 ⇒ 混用窗口最长只有一个唤醒周期，
  且方向是**更早强制放行**（更安全的一侧）。
- 调用点（`wake_runtime.should_skip`）改为构造 `SkipPolicy` 后传入，**唯一**改动点。

#### 2.2.4 跳过时的观测

跳过 `position_watch` 复用既有 `_count_wake_skip` 与 `wake_skips` 计数（口径不变：被抑制的、
原本会发生的唤醒次数），但**额外**分列 `positionWatchSkips`，否则「省下来的是哪一类」不可
区分，轴 D 的复盘会得出错误结论。

### 2.3 轴 C：信号驱动唤醒（新模块 `signal_preview.py` + `flip_detector.py`）

#### 2.3.1 为什么必须克隆：策略与影子 broker 都是有状态的（**阻断级事实**）

在真实对象上对未确认根调用 `on_candle` 会**永久污染**策略内部状态。已核实的实例：

| 文件 | 被 `on_candle` 就地改写的状态 |
| --- | --- |
| `strategies/momentum_breakout.py:74/79/93/97` | `_peak` / `_trough`（入场后跟踪止损基准） |
| `strategies/trend_band_swing.py:250/338/342/357/369` | `_entry_zone` / `_bucket_ts` / `_bucket_entries` / `_cooldown_until_ts` / `_extreme_px` |
| `strategies/trend_pullback_long.py:185/192/205` | `_peak` / `_trail_armed` |
| `strategies/trend_pullback_short.py:184/191/204` | `_trough` / `_trail_armed` |
| `strategies/factor_score_strategy.py:124/143`（在 `prepare` 与 `on_candle`） | `_thresholds` / `_breakdown` |

且 `signals.step_strategy_shadow` 还会 `fill_pending` / `apply_risk` / `mark` / `submit`
改写 `SimBroker`。因此「在真身上预演一步再回滚」在物理上不可行——必须深拷贝。

#### 2.3.1b 判据必须是**投影立场**而不是 `stance`（**P0-1，本轴成立与否的前提**）

`step_strategy_shadow` 的四阶段是
`set_risk → fill_pending → apply_risk → mark → on_candle → submit`，而
`SimBroker.submit`（`engine/simulator.py:206`）**只把信号排进 `_pending`，次根开盘才成交**。
于是 `StrategySignalView.stance = shadow.pos.side` 在预演这一步**只**可能因两件事变化：

| 变化源 | 是否 repaint | 对本轴的意义 |
| --- | --- | --- |
| ① 上一确认根排队的委托在本根**开盘**成交（`fill_pending`） | **否**——本根一开盘就已确定，整根不再变 | 若拿它当「翻转」，每一根有排队单的 K 线都会在开盘后 `min_gap_s` 处多烧一次 LLM（签名恒定 ⇒ 必过防抖），直接违反 A-4 |
| ② `apply_risk` 用本根 `h/l` 触发止损/止盈 | 半否——`h/l` 只单调外扩 | 真实且有价值（影子仓被打掉），应当保留 |
| ③ 本根 `on_candle` 产生的新边沿 | **是**（真正的 repaint 源） | **`stance` 完全看不见它**——它只写 `latest_signal` 与 `_pending` |

也就是说：v1 的判据看不见它最想抓的 ③，却会被 ① 反复误触发。**修法**是把判据换成
**投影立场**（projected stance）——「若本根就这么收线，下一根开盘后这个策略会站在哪一边」：

```python
# ai_auto/signal_preview.py（纯函数，零改 signals.py / simulator.py）
_PROJECTED = {"open_long": "long", "open_short": "short", "close": "flat"}


def projected_stance(view: StrategySignalView) -> str:
    """投影立场：本根边沿信号兑现后的立场（`latest_signal == "none"` ⇒ 维持 `stance`）。

    与 `SimBroker.submit` 内部那个 `projected` 变量**同义**（该变量是私有局部量，刻意不去
    改签名把它挤出来——一个观测特性不该动撮合引擎的接口）。单策略一根上至多一条边沿
    （`step_strategy_shadow` 取 `signals[-1]`），故该映射与 `submit` 的逐条推进等价。
    """
    return _PROJECTED.get(view.latest_signal, view.stance)
```

基线与预演**两侧都取投影**：基线 = 上一确认根板的投影，预演 = 预演板的投影。这样

- ① 被基线自然吸收（上一根已经喊了 `open_long`，基线投影就是 `long`，本根成交后仍是
  `long` ⇒ **无翻转、不唤醒**）；
- ② 与 ③ 都被如实捕捉，且 ③ 确实会随价格回落而消失 ⇒ 防抖 / 锁存 / `min_gap_s` 这套机器
  才名副其实，`provisional` 与 repaint 警示也才是诚实的措辞。

#### 2.3.2 线程与快照纪律（守着真实资金的 5 秒 tick 不得被 CPU 占住）

`build_strategy_frames` 会为每个策略在最长 1200 根窗口上跑一次 `prepare`（+ MTF
`attach_timeframes`）。在盯市 tick 上同步跑它，最坏会把利润保护棘轮与会话止损挤停——这与
a7ed70c 给战术取数加硬超时的理由**完全同源**（该 spec §2.2 三闸门）。故：

1. **快照在事件循环线程取**：`MarketWindow.preview_snapshot(live_row)` 把 `_candles` 与各
   `_htf_windows` 拷成普通 `list[dict]`。绝不把 `deque` 交给工作线程——另一侧
   `feed_candle` 随时可能 append，跨线程迭代会抛 `RuntimeError: deque mutated`。
2. **克隆也在事件循环线程取**（读的是真身，必须与 `feed_candle` 互斥于同一线程），
   且必须是 §2.3.3 的**有界克隆**（P1-5：整对象 `deepcopy` 的代价随会话长度线性增长）。
3. **只有纯 pandas 计算进 `asyncio.to_thread`**：`build_preview_frames` + `preview_board`
   全程只碰克隆体与快照，天然线程安全。
4. **硬超时 + 真正的在飞守卫（P1-2）**。`asyncio.wait_for` 会在超时时**取消它 await 的那个
   future**，但 `to_thread` 的**线程本身不可取消**——线程还在烧 CPU。v1 草图把
   `_preview_inflight = False` 放在 `finally` 里，于是超时那一刻守卫就被解除、下一次预演照常
   投递，与 R-6 宣称的「同一时刻至多一个预演」直接矛盾。正确写法是**用 `shield` 保住内层任务、
   由它的 done-callback 解锁**：

```python
    task = asyncio.ensure_future(asyncio.to_thread(run_preview, clones, snapshot))
    self._inflight = True
    task.add_done_callback(self._release_inflight)   # 线程**真的**结束时才解锁
    try:
        return await asyncio.wait_for(asyncio.shield(task), self._timeout_s)
    except (TimeoutError, asyncio.TimeoutError):
        self.note_timeout(now_wall)      # 退避：间隔翻倍，上限 300s。**不在此解锁**
        return None
```

5. **分组维度的并发上限（P1-2）**：AI-trade 是**多标的分组**运行，N 个会话各有自己的
   `_monitor_tick`，且它们由同一次启动请求几乎同时拉起 ⇒ 预演节流会天然对齐、同时向默认
   线程池投 N 个 CPU 密集任务。故：
   - `signal_preview` 持一个**模块级** `asyncio.Semaphore(_PREVIEW_MAX_CONCURRENCY)`
     （常量 2，非配置项——它是护栏不是旋钮），`run_preview` 投递前非阻塞地 `locked()` 探测，
     拿不到就本 tick 直接跳过（**绝不排队等**：排队等于把盯市 tick 挂在别的会话身上）；
   - 首次预演时刻按 `session_id` 的稳定哈希错峰
     `first_due = now + (hash(session_id) % preview_interval_s)`，避免 N 个会话步调一致。
     用稳定哈希而非随机数，测试才可复现。

#### 2.3.3 `signal_preview.py`

```python
@dataclass(frozen=True)
class PreviewSnapshot:
    """预演所需的全部只读数据（事件循环线程取，工作线程消费）。"""

    rows: list[dict]                    # 确认根窗口 + 末尾追加的未确认根
    htf_rows: dict[str, list[dict]]     # 各高周期窗口拷贝（策略 MTF 用）
    bar: str

    @property
    def index(self) -> int:
        return len(self.rows) - 1       # 预演行的位置索引


def clone_for_preview(specs, shadows) -> tuple[list, list] | None:
    """克隆策略与影子 broker；任一对象不可克隆 ⇒ None（**fail-open：本特性整体降级**）。

    返回 None 只让轴 C 失效，绝不影响任何既有路径——一个观测特性没有资格弄坏交易。

    `specs` 走整对象 `copy.deepcopy`：策略的隐藏引用（`_peak` / `_thresholds` /
    `_breakdown` / 组合策略的子策略…）无法逐一枚举，漏一个就是 R-4 那条「污染真身」的路。
    `shadows` **不**走整对象 deepcopy，见 `clone_shadow`。
    """
    try:
        return [copy.deepcopy(s) for s in specs], [clone_shadow(b) for b in shadows]
    except Exception:      # noqa: BLE001 —— 策略可能持有不可拷贝对象（锁 / 客户端）
        return None


def clone_shadow(shadow: SimBroker) -> SimBroker:
    """影子 broker 的**有界**克隆（P1-5）：只复制决定 stance 的状态，观测账本给空表。

    为什么不 `deepcopy` 整个 broker：`trades` / `equity_points` 随会话长度**线性增长**
    （`mark` 每步追加一条；仅暖机就先塞 ≤1200 条 × 策略数），而 stance 判定链
    （`fill_pending` / `apply_risk` / `mark` / `submit` / `context`）**从不读它们**——它们
    只被 append。每 15 秒在事件循环线程上深拷一遍这两张只写表，是纯粹的浪费，且代价没有上界。

    复制的字段就是全部会影响 stance 的状态：`pos`（深拷）、`_pending`（深拷）、`_risk`、
    `_cur_bar`、`cash`、`fees_paid`、`peak_equity`、`initial_capital`、`fee_rate`、`slip`、
    `leverage`。等价性由 T-25 门禁钉死（与整对象 deepcopy 跑 K 步后 stance 轨迹逐步对拍）。
    """


def build_preview_frames(specs, snapshot: PreviewSnapshot) -> list[pd.DataFrame]:
    """逐策略建帧（工作线程内跑；`specs` 必须是**克隆体**——`prepare` 会写策略内部状态）。

    逻辑与 `MarketWindow.build_strategy_frames` 同源——后者重构为
    `build_frames_for(specs, rows, htf_rows, bar)` 后两处共用，绝不复制粘贴。
    """


def preview_board(specs, shadows, frames, index) -> list[StrategySignalView]:
    """= `signals.evaluate_signal_board(specs, shadows, frames, index)`（入参全为克隆体）。

    刻意**直接复用**而非另写一份步进：`step_strategy_shadow` 的四阶段顺序
    （set_risk → fill_pending → apply_risk → mark → on_candle → submit）是本仓库
    付过学费的因果序，预演走另一条路就等于预演的不是同一个系统。
    """


def flip_signature(base: list[StrategySignalView],
                   preview: list[StrategySignalView]) -> tuple[str, dict] | None:
    """**投影**净共识立场是否翻转；未翻转 ⇒ None。

    两侧都先经 `projected_stance` 映射再算 `compute_consensus(...).net_stance`（P0-1）：
    直接比 `stance` 会漏掉本根 `on_candle` 的新边沿、又会被「上一根排队单本根开盘成交」
    这件确定性的事反复误触发。实现上不改 `compute_consensus`——构造一份 `stance` 被替换为
    投影值的**浅副本视图列表**喂给它，票数口径与共识门禁保持逐字同源。

    判据刻意**只看净共识立场**而不是「任一策略立场变了」：后者在多策略板上几乎每根未确认根
    都成立，会把唤醒打成事实上的流式，比现状更糟。

    Returns:
        `(signature, wire)`；`signature` 用于防抖去重，形如
        `"flat>long|7:flat>long,9:short>flat"`（策略 id 排序后拼接，稳定可比）。
    """
```

`MarketWindow.preview_snapshot(live_row, *, now_wall, stale_after_s)` 的**两道前置闸
（P0-2，缺一即会把假证据送进真实资金决策链）**：

```python
    def preview_snapshot(self, live_row: dict | None, *, now_wall: float,
                         last_px_wall: float, stale_after_s: float) -> PreviewSnapshot | None:
        """预演快照；任一闸不过 ⇒ None（不预演，绝不猜）。

        闸 ①（重复根）：`live_row["ts"] <= self._last_confirmed_ts` ⇒ None。确认根落地之后
        仍可能收到一条**同 ts** 的未确认推送（`feed_candle` 的未确认分支不做 ts 校验，
        `session.py:565`），把它追加到 `rows` 末尾会让帧里出现两根同 ts 的 K 线，指标在重复根
        上计算 ⇒ 产生一个纯属数据缺陷的「翻转」。

        闸 ②（判定价新鲜度）：`now_wall - last_px_wall > stale_after_s` ⇒ None。**必须读
        `_last_px_wall`（每次写 `_last_live_px` 即刷新）而不是 `_last_candle_wall`（只在确认根
        更新）**——后者在 12H/1D 执行周期下几十小时不跳，WS 静默时 `_stale_degraded` 还没触发，
        `_live_row` 已冻结不动，而防抖器仍在按墙钟累积「已连续保持 45 秒」。这是上一轮 D-1
        踩过的同款坑，本轮不得复发。
        """
```

`wire`（进 `payload["wake"]["flip"]`）：

```python
{"netStanceFrom": "flat", "netStanceTo": "long",
 "strategies": [{"strategyId": 7, "name": "EMA 交叉短线", "from": "flat", "to": "long"}],
 "provisional": True, "heldS": 47.3, "samples": 4, "barTs": 1785600000}
```

#### 2.3.4 `flip_detector.py`（防抖状态机，纯）

```python
class FlipDetector:
    """「信号连续保持 N 秒才算数」的防抖 + 同一翻转只唤醒一次的锁存（轴 C）。

    repaint 是本轴唯一真正的敌人：价格上穿均线时策略喊多、两秒后跌回去信号消失。没有防抖
    的信号驱动唤醒比定时唤醒更糟——它专挑最抖的时候把 AI 叫起来。
    """

    def __init__(self, *, hold_s: float, min_samples: int): ...

    def observe(self, signature: str | None, now_wall: float, *, row_key: tuple) -> str:
        """返回 `"idle"` / `"pending"` / `"confirmed"`。

        - `signature is None`（无翻转）或与在跟踪的不同 ⇒ 重置计时与样本数，返回 `idle`/`pending`；
        - 同一 signature 已被 `note_fired` 锁存 ⇒ 恒 `idle`（同一次翻转绝不反复开轮）；
        - `row_key` 与上次采样相同 ⇒ **本次不计入 `samples`、也不推进 `last_seen`**（P0-2）；
        - 持续时长 ≥ `hold_s` **且** 观测次数 ≥ `min_samples` ⇒ `confirmed`。

        两个条件缺一不可：只看时长，一次 15 秒间隔的采样就能「保持 45 秒」；只看次数，
        高频 tick 会让它瞬间达标。

        `row_key = (live_row["ts"], live_row["c"], live_row["h"], live_row["l"])`：行情没动过的
        采样**不算证据**。没有这条，一个静默的 WS 就能让「该翻转已连续保持 45 秒」这句话在
        零新数据的情况下成立——而这正是本轴要送进 prompt 的核心论据。
        """

    def note_fired(self) -> None:
        """锁存当前 signature（唤醒已发出）。"""

    def note_new_bar(self) -> None:
        """确认根落地即解锁 + 清空跟踪：新根 = 新证据，同方向的翻转值得再看一次。"""
```

#### 2.3.5 会话接线

`session.feed_candle` 的未确认根分支新增一行（记下预演所需的临时行）：

```python
        if not candle.confirm:
            self._last_live_px = float(candle.c)
            self._last_px_wall = time.time()
            self._live_row = {"ts": candle.ts, "o": candle.o, "h": candle.h,
                              "l": candle.l, "c": candle.c, "vol": candle.vol}
```

确认根分支新增一行：`self._wake.note_new_bar()`（解锁防抖锁存），并把 `self._live_row = None`
（该根已收线，旧的未确认行不再有意义）。

`_monitor_tick` 在 `maybe_refresh_tactical` 之后、`_maybe_interval_wake` **之前**插入：

```python
        await self._maybe_signal_flip_wake()
```

异步编排（节流 / 错峰 / 并发闸 / 克隆 / 超时 / 退避 / 在飞守卫）**全部下沉**
`signal_preview.PreviewRunner`（P1-3）；`WakeRuntime` 的既有契约是「纯同步、网络 IO 全在
MarketWindow」，往它身上挂 `await` 与线程投递会破坏那条 docstring 承诺，故不放在那里。
会话侧只留下面这 16 行：

```python
    async def _maybe_signal_flip_wake(self) -> None:
        """轴 C：未确认根预演 → 防抖 → 投影净共识翻转即唤醒（重活在线程里，不占盯市 tick）。"""
        if not self._preview_ready():
            return
        result = await self._preview.run(self._specs, self._shadows, self._window,
                                         self._live_row, now_wall=time.time())
        if result is None or self._deciding:  # 在飞轮不抢槽位；防抖计时继续累积，下 tick 再看
            return
        ref_px = self._current_px()           # 以下同步认领，check→set 之间无 await
        wake = self._wake.plan_signal_flip(result, self._last_board,
                                           self._wake_readings("interval", ref_px),
                                           now_wall=time.time())
        if wake is None:
            return
        self._deciding = True
        self._last_round_bar_index = self._last_bar_index
        self._last_round_px = ref_px
        now = time.time()
        self._last_decision_started_wall = now
        self._decision_task = asyncio.create_task(
            self._run_decision_round(self._last_board, self._last_bar_index,
                                     ref_px, int(now), wake))

    def _preview_ready(self) -> bool:
        """预演前置条件（P2-4：CLAUDE.md §六「单表达式超过三个布尔条件即抽函数」）。"""
        return (self.state == "running" and not self._stale_degraded
                and self._last_board is not None and self._live_row is not None
                and self._preview.due(time.time()))
```

`PreviewRunner.run(...)` 内部按序：并发信号量非阻塞探测 → `preview_snapshot`（两道前置闸，
§2.3.3）→ `clone_for_preview` → `ensure_future(to_thread(...))` + `shield` + `wait_for`
→ 超时/异常一律返回 `None` 并退避（间隔翻倍，上限 300s）；在飞解锁**只**由内层任务的
done-callback 负责。它自己持有 `_inflight` / `_next_due_wall` / `_interval_s` /
`_timeouts` / `_errors` / `_disabled`，`stats()` 汇给 `WakeRuntime.stats()` 统一上报。

返回值是一个只读小结构体而不是裸 board：

```python
@dataclass(frozen=True)
class PreviewResult:
    board: list[StrategySignalView]
    row_key: tuple            # 预演**实际用的**那一行的 (ts, c, h, l)
    bar_ts: int
```

`row_key` 必须由 `PreviewRunner` 一路带出来、**不能**在 `_maybe_signal_flip_wake` 里回头读
`self._live_row`：预演跑在线程里、期间 `feed_candle` 随时可能写新的 `_live_row`，回头读会把
「这次采样看的是哪一行」记错，直接废掉 T-12b 那条「数据变过才算一次采样」的判据。

`WakeRuntime.plan_signal_flip(result, base_board, readings, *, now_wall)` 内部按序：
总闸 → `flip_signature(base_board, result.board)` → `FlipDetector.observe(sig, now_wall,
row_key=result.row_key)` → `confirmed` → 最小间隔
`now - last_round_wall >= ai_trade_signal_wake_min_gap_s` → `classify(readings, flip=wire)`
→ `note_fired()` → 计数 `signal_flip_wakes += 1` → 返回。
任一环不成立返回 None（且**不**锁存，翻转仍在跟踪）。

**关键不变式（写进 docstring 与测试）**：`plan_signal_flip` 返回的 wake 只影响
`payload["wake"]`。传给 `_run_decision_round` 的 `board` 仍是 `self._last_board`——**确认根
的信号板**。共识门禁、铁律层、edge 门禁、仓位层因此全部仍以确认根为准，预演结果**永远
只是建议性证据**。这条是轴 C 全部安全性的支点。

### 2.4 唤醒原因：七态 → 八态，与镜像点清单

`signal_flip` 插在 `bar_advanced` 之后、`resting_order` 之前：真的收了新根永远优先；除此
之外，「某个策略的立场正在翻转」是信息量最高的一件事。

```python
WAKE_KINDS = ("candle_close", "bar_advanced", "signal_flip", "resting_order",
              "profit_tier", "price_breakout", "position_watch", "heartbeat")
```

`WakeFacts` 增一个字段 `flip: dict | None = None`；`classify_wake` 的 `conditions` 元组增一行
`("signal_flip", facts.flip is not None)`；产出 wire 时 `wake["flip"] = facts.flip`（None 即
**整键缺席**，与既有 `tacticalContext` / `baseRules` 同款范式，保证非翻转轮逐字节回退）。

> **镜像点清单（新增 kind 必须逐条勾掉）** —— a7ed70c 的终审 RV-1 正是「新中文化 helper 漏
> 接一个展示点」，本轮预先把清单列死：
>
> 1. `wake_reason.WAKE_KINDS`（八元组）
> 2. `wake_reason.classify_wake` 的 `conditions`
> 3. `wake_reason.WakeFacts.flip` + `classify_wake` 的 `flip` 出参
> 4. `wake_text._WAKE_BODY` 增 `signal_flip`
> 5. `wake_text._ALSO_TRUE_LABEL` 增 `signal_flip: "立场翻转"`
> 6. `tactical_context._FULL_TIER_KINDS` 增 `signal_flip`（翻转轮值得看全量战术明细）
> 7. `tactical_context._EXECUTION_FULL_KINDS` **不增**——执行周期证据自上一轮起没变，压缩档
>    仍正确；且 `format_digest_repeat` 的指路句由 `has_tactical` 把关（RV-1 已修）
> 8. 前端 `lib/aiTradeTypes.ts` 的 `WakeKind` 联合类型
> 9. 前端 `lib/aiTrade.ts` 的 `WAKE_LABELS`（`describeWakeReason` / `describeTrigger` 共用）
> 10. `tests/test_ai_trade_wake_reason.py:44` 的 `set(WAKE_KINDS) == {...}` 断言
> 11. `tests/test_ai_trade_wake_text.py:77` 的逐 kind 措辞参数化表
> 12. **（P1-9 补）** `components/Agent/AiTradeTraceRound.tsx:560-572` `SignalContext` 的
>     **早退门控**：flip / backlog 展示块若挂在这个 `<details>` 里，就必须同时进那个
>     `if (!board.length && !regime && … )` 判空表——这里正是上一轮 RV-2 的原址，注释里已经
>     写着「新增边缘态字段必须验证渲染门控没把该态排除掉」，本轮不得再犯第三次。
> 13. **（P1-9 补）** `lib/aiTradeTypes.ts` 的 `AiTradeSessionRow`（约 :532，`wakeSkips` 邻位）
>     须补 `wakeDrops? / wakeCatchups? / positionWatchSkips? / signalFlipWakes?`——status 事件
>     经 `store.patchAiTradeMemberStatus` 展开合并进 `m.session`，**类型不补则读点无法通过 tsc**
>     （运行期数据其实已经在了，这是一个纯类型的漏接点，最容易被「后端已下发」骗过去）。

> **勾选纪律**：这 13 条里只有第 8、9、13 条有 tsc 兜底，第 10、11 条有测试兜底，其余 8 条
> 全靠人眼。上一轮终审 RV-1 就是漏在无兜底的那一类里——请逐条 grep 后在 PR 描述里回执。

### 2.5 prompt 渲染（`ai_auto/wake_text.py`）

新增文案（措辞纪律沿用该模块 docstring 的三条）：

```python
def _wake_signal_flip(wake: dict) -> str:
    flip = wake.get("flip") or {}
    names = "、".join(s.get("name", "?") for s in (flip.get("strategies") or [])[:3])
    return (f"策略立场翻转（{flip.get('netStanceFrom')}→{flip.get('netStanceTo')}；"
            f"{names} 在**当前未收线**的这根上改变了立场，已连续保持 "
            f"{_duration(flip.get('heldS'))}）。这是**未确认根**上的预演结果，"
            "可能随收线反复（repaint）；信号板与全部门禁仍以最近确认根为准，"
            "请把它当作择时线索而非既成事实")
```

> P2-1：v1 此处的 f-string 中间有一个**字面换行**，原样复制即 `SyntaxError`；已改成隐式拼接，
> 并把无插值的两段降级为普通字符串（少两个 f 前缀，行宽也回到 80 列内）。

补跑子句（追加在任意 kind 的原因之后，**只在 `backlog` 存在时**出现）：

```python
def _backlog_clause(wake: dict) -> str:
    b = wake.get("backlog") or {}
    bars, ivl = b.get("droppedBars") or 0, b.get("droppedIntervals") or 0
    if not (bars or ivl):
        return ""
    parts = []
    if bars:
        parts.append(f"{bars} 根 K 线收线未被研判")
    if ivl:
        parts.append(f"{ivl} 次定时唤醒被丢弃")
    return (f"｜补跑（上一轮研判期间{'、'.join(parts)}，最早的一次已等待 "
            f"{_duration(b.get('waitedS'))}；本轮是它们的首次研判，"
            "不要把上一轮结论当作对这段行情的判断）")
```

> P1-1：措辞由「上一轮研判耗时 X」改为「最早的一次已等待 X」——`waitedS` 的口径是
> `now - first_dropped_wall`，会话并没有把轮耗时记进 backlog。把一个数说成它不是的东西，
> 正是本方案反复要杜绝的失真。

`format_wake_for_prompt` 的返回改为 `f"{suffix}{_backlog_clause(wake)}{also_clause}"`——
`backlog` 缺席时返回值**逐字节等于**现状。

### 2.6 `WakeRuntime` 公开面扩充（`session.py` 只加薄调用）

`session.py` 现为 1583 行（**已远超 800 行红线，是本仓库的存量欠账**）。本轮 `session.py` 的
净增按下表逐项预算，合计 **≤72 行**（表内 70 + 2 行余量）；其余逻辑一律落在 `WakeRuntime` / `PreviewRunner` 与三个
新模块上。

> **P1-3**：v1 的「≤55 行」与它自己给的两段草图（39 + 20 = 59 行，还没算其余接线）互相矛盾，
> 是个无法达成的预算。把异步预演编排下沉 `PreviewRunner` 之后才真的做得到，下表可逐行核对：

| 落点 | 预算 |
| --- | --- |
| `__init__`：`_live_row` / `_preview = PreviewRunner(...)` | 2 |
| `feed_candle` 未确认根分支：记 `_live_row` | 3 |
| `feed_candle` 确认根分支：`note_new_bar()` + `_live_row = None` | 2 |
| `feed_candle` 丢弃点：`note_bar_drop` | 1 |
| `_maybe_interval_wake` 丢弃点：`_deciding` 分支下移 + 记账推 status | 4 |
| `_emit_decision_skipped`：wake 桩加 `backlog` 键 | 1 |
| `_run_decision_round` 的 `finally`：`_maybe_catchup_wake()` | 1 |
| `_maybe_catchup_wake`（含 docstring） | 20 |
| `_monitor_tick`：`await self._maybe_signal_flip_wake()` | 1 |
| `_maybe_signal_flip_wake` + `_preview_ready`（含 docstring，§2.3.5 草图逐行可数） | 28 |
| `_status_payload` 四个计数 + `_finalize_run` 一行合并 | 3 |
| 方法之间的空行分隔 | 4 |
| **合计** | **70** |

新增 `WakeRuntime` 公开面（**只留纯同步判定**；异步/线程/退避全在 `PreviewRunner`）：

| 方法 / 属性 | 语义 |
| --- | --- |
| `note_interval_drop(now_wall) -> bool` | 记一次定时唤醒丢弃（节流后返回是否真的计数） |
| `note_bar_drop(now_wall) -> None` | 记一根确认根首次研判丢弃（不节流） |
| `backlog_wire() -> dict` | 当前待办快照（供 `decision_skipped` 事件自解释） |
| `plan_catchup(readings, *, now_wall) -> dict \| None` | 轴 A 补跑判定（§2.1.4） |
| `note_new_bar() -> None` | 确认根落地：解锁防抖锁存 |
| `plan_signal_flip(result: PreviewResult, base_board, readings, *, now_wall) -> dict \| None` | 轴 C 唤醒判定（§2.3.5）；`row_key` 由 `result` 带入，**不得**回头读 `session._live_row` |
| `signal_flip_enabled: bool` | 轴 C 总闸（含运行期降级后的关闭态） |
| `alerts: list[dict]` | 启动期一次性告警（镜像 `PriorRuntime.alerts` 的既有范式） |
| `stats() -> dict` | 调度层观测键（§4.3）；预演侧的四个数从 `PreviewRunner.stats()` 合并 |

`WakeRuntime.__init__` 增字段：`_backlog = WakeBacklog()`、`_detector = FlipDetector(...)`、
`_signal_flip_wakes = 0`、`_position_watch_skips = 0`。

`PreviewRunner`（`signal_preview.py`，async）持：`_inflight` / `_next_due_wall`（首次按
`session_id` 稳定哈希错峰）/ `_interval_s`（退避可增长，上限 300s）/ `_timeouts` / `_errors`
/ `_disabled`，公开 `due(now_wall) -> bool`、`run(...) -> list[StrategySignalView] | None`、
`stats() -> dict`、`alerts`。克隆连续失败即 `_disabled = True`（整轴关闭 + 一条告警）。

### 2.7 轴 D：唤醒来源胜率归因（**纯前端**，零后端新统计口径）

#### 2.7.1 已核实的三条事实，决定了这一轴不需要后端改动

1. `session._inject_explain_meta`（`session.py:301`）在 `explain.kind in ("atomic","reverse")`
   时打 `meta.wakeKind`（白名单常量在 `session.py:101`）。
   **（P1-8 修正）** v1 在此写「`build_atomic_explain` 正是开仓成交的解释构造器」——**不成立**：
   `live_auto/explain.py:63` 的 `build_atomic_explain` 用 `side` 分支同时构造**开仓与平仓**
   解释，而 `build_reverse_explain`（同文件 `:91`）产出的恰恰是反手的**平仓腿**
   （`side="close"`）。正确的表述是：**AI 决策直接产生的成交（开仓腿与 AI 主动平仓腿）都带
   `wakeKind`；确定性层触发的成交（保护层平仓 / 会话止损 / 外部干预）不带**。开仓腿带 —— 这
   才是轴 D 需要的那半句，且它依然为真。
2. 前端 `lib/autoTradePnl.ts:12` 的 `RoundTrip` **持有 `entry: Trade`**，而 `Trade.explain`
   已在 `lib/types.ts:330` 上。
   **（P1-9 修正）** v1 称「两个页签都已经在用 `buildLedger`」——**不成立**：`AiTradeStatsLive`
   在组件顶层用 `buildLedger`（`AiTradeStatsLive.tsx:64`），而 `AiTradeStatsDetail`
   **没有**——它在成员子组件里逐成员调 `buildRoundTrips`（`AiTradeStatsDetail.tsx:233`），
   组级拿不到扁平 trips。接线细节见 §2.7.3。
3. 因此「按开仓唤醒来源切胜率」是既有数据的**纯投影**。在后端再算一份会立刻产生第二个口径
   ——本仓库的显式反模式（见 `stats_ext.py` docstring：`build_cost_stats` 是唯一真源）。
4. 后端两条读取通道都已核实**无需改动**：实盘侧 WS `trade` 事件整对象入列
   （`useAiTradeGroupStream.ts:167` → `store.appendAiTradeMemberTrade`，非逐键手抄）；
   历史侧 `AiTradeHistoryMember.trades` 声明为 `list[dict]` 透传，`schemas.trade_out:990`
   恒含 `explain`。（本仓库有过「store 逐键手抄静默吞掉 WS 新键」的前科，故此条专门复核。）

**归因按开仓腿而非平仓腿**是刻意的：诊断问的是「我赚钱的那些交易，是哪种唤醒**触发**的」。
保护层平仓腿的 `wakeKind` 按设计缺席（它与唤醒无关），用平仓腿做归因会把「保护层救回来的
单」整类打进 `unknown` 桶，恰好抹掉最值得复盘的那一类。

#### 2.7.2 `lib/aiTradeWakeAttribution.ts`（新，纯函数）

```ts
export interface WakeAttributionRow {
  kind: WakeKind | "unknown";
  label: string;          // 复用 WAKE_LABELS，未知 → "未记录来源"
  count: number;
  wins: number;
  winRate: number | null;     // count < MIN_SAMPLES ⇒ null（**绝不造假显著性**）
  netUsd: number;
  expectancyUsd: number | null;   // 同上
}

export const WAKE_ATTRIBUTION_MIN_SAMPLES = 5;

export function attributeByWake(trips: RoundTrip[]): WakeAttributionRow[];
export function hasWakeAttribution(rows: WakeAttributionRow[]): boolean;  // 非 unknown 桶是否有样本
```

排序：`count` 降序，`unknown` 恒排末位。`winRate` / `expectancyUsd` 在样本 < 5 时返回 `null`
并由组件渲染成「样本不足（n=3）」——与 `attribution._t_stat` 在 `n<2` 时返回 `None` 同款纪律。

#### 2.7.3 `components/Agent/aiTradeStats/WakeAttributionCard.tsx`（新）

一张只读卡片：表头「按唤醒来源」+ 一行脚注（口径：按**开仓**唤醒来源归集；
`ai_trade_wake_reason_enabled=false` 的会话与 a7ed70c 之前的历史成交落在「未记录来源」）。
`hasWakeAttribution === false` 时**整卡不渲染**（老会话不该看到一张全是「未记录来源」的表）。
同一组件同时挂进 `AiTradeStatsLive` 与 `AiTradeStatsDetail`，props 只吃 `RoundTrip[]`，
两处零分叉。

**两处 trips 的取得方式（P1-9，v1 在此处的事实错误已修）**：

- `AiTradeStatsLive`：现有 useMemo 只返回 `{visible, summary, bounds, fullSummary}`，
  **未返回**未筛选的 `ledger`。须把 `ledger` 一并返回并传给卡片。取**未筛选**的 ledger 是
  刻意的——与 `fullSummary` / `AnnualizedProjectionCard` 同源：胜率归因的样本量本来就少
  （`MIN_SAMPLES=5`），再叠一层时间筛选会让这张表在绝大多数窗口下整卡消失。
- `AiTradeStatsDetail`：该组件**没有**组级 ledger（逐成员在子组件里各自 `buildRoundTrips`）。
  须在详情顶层加一个 useMemo：
  `buildLedger(detail.members.map(m => ({instId: m.instId, trades: m.trades, leverage: m.leverage})))`
  ——`buildLedger` 已经是「跨成员合并、逐成员配对、绝不跨 instId」的现成真源，**不要**在子组件
  里各算一份再拼（那就是又一次「同一个量两处各算一次」）。

### 2.8 一条端到端时间线（15m 执行 / 2 分钟唤醒 / 持仓中）

时间一律记为**自决策轮开始的相对秒数** `t+N`（v1 用 `mm:ss` 且刻度自相矛盾，已改）。
下面每一个数都必须能被 §6.1 的断言复算出来。

```
t+0     确认根到达 → 信号板重算 → 决策轮开始（LLM 卡住，最坏 379s）
t+120   定时到点 + 在飞 → note_interval_drop 计数 ⇒ dropped_intervals=1，first_dropped=t+120
t+125…235  每 5s tick 都到点，但被 min_gap_s=120s 节流吃掉 ⇒ 仍是 1（裸 tick 会记 ~23 次）
t+240   跨过节流窗 ⇒ dropped_intervals=2
t+360   跨过节流窗 ⇒ dropped_intervals=3     ← 379 秒里「原本真的会发生」的定时唤醒恰是 3 次
t+300   该 15m 根收线 + 在飞 → note_bar_drop ⇒ dropped_bars=1；推 decision_skipped（自解释）
t+379   决策轮结束 → finally → _maybe_catchup_wake
          classify ⇒ bar_advanced（有新根）⇒ should_skip=False ⇒ 同一 tick 内立刻补跑
          wake.backlog = {droppedIntervals:3, droppedBars:1, waitedS:259, chain:1}
                         （waitedS = 379 − 120，自**首次**丢弃至今；不是轮耗时）
          prompt：「补看新 K 线 …｜补跑（上一轮研判期间 1 根 K 线收线未被研判、3 次定时唤醒
                    被丢弃，最早的一次已等待 4 分 19 秒…）」
t+560   补跑轮结束（正常）⇒ 无待办 ⇒ 不再链式补跑；chain 由下一个正常轮的 note_round_start 归零
t+565   盯市 tick：持仓 + 保护层 armed + 无新根 + 未越阈 ⇒ position_watch ⇒ 跳过（streak=1）
t+685   同上（streak=2；跳过计数同样按唤醒周期节流，不是每个 5s tick 都计）
t+805   同上（streak=3 = 上限）⇒ **强制放行**一轮，模型仍会被叫起来复盘
t+820   预演：投影净共识 flat→long 首次出现 ⇒ pending（samples=1）
t+835   预演：同一 signature 且 row_key 变过 ⇒ samples=2
t+850   预演：samples=3，但只保持了 30s < hold_s=45 ⇒ 仍 pending
t+865   预演：保持 45s 且 samples=4 ⇒ confirmed ⇒ 距上轮 60s ≥ min_gap 120s？否 ⇒ 不唤醒、不锁存
t+925   预演：仍 confirmed 且距上轮 ≥120s ⇒ 唤醒（kind=signal_flip，flip.provisional=true）
t+960   该根收线 → note_new_bar() 解锁 → 同方向翻转在新根上可再次被跟踪
```

---

## 三、文件 / 模块变更计划

### 3.1 后端源文件（`services/quant/app`）

| 文件 | 新建/修改 | 意图（一行） |
| --- | --- | --- |
| `ai_auto/wake_backlog.py` | 新建 | 轴 A：被丢弃唤醒的待办账本 + 节流计数 + 补跑 wire（纯，无 IO） |
| `ai_auto/signal_preview.py` | 新建 | 轴 C：克隆式无副作用预演（有界克隆 / 建帧 / 投影立场 / 翻转签名）+ `PreviewRunner`（async 编排：错峰、节流、并发闸、硬超时、退避、在飞守卫） |
| `ai_auto/flip_detector.py` | 新建 | 轴 C：翻转防抖状态机 + 同一翻转只唤醒一次的锁存（纯） |
| `ai_auto/wake_guard.py` | 修改 | 增 `SkipPolicy` 聚合结构体与 `position_watch` 跳过分支（轴 B） |
| `ai_auto/wake_reason.py` | 修改 | 八态：增 `signal_flip` kind、`WakeFacts.flip`、wire 的 `flip` 键 |
| `ai_auto/wake_runtime.py` | 修改 | 挂 backlog / detector / 预演节流；新增 §2.6 表列出的公开面 |
| `ai_auto/wake_text.py` | 修改 | 增 `_wake_signal_flip` 文案 + `_backlog_clause` + alsoTrue 标签 |
| `ai_auto/tactical_context.py` | 修改 | `_FULL_TIER_KINDS` 增 `signal_flip`（`_EXECUTION_FULL_KINDS` 刻意不增） |
| `ai_auto/market_window.py` | 修改 | 抽 `build_frames_for(...)` 供两处共用；增 `preview_snapshot(live_row, …)`（含 §2.3.3 两道前置闸） |
| `ai_auto/session.py` | 修改 | 薄接线：`_live_row` 记录、两处丢弃记账、`finally` 补跑、预演 tick、计数入 status / finalize（净增 ≤72 行，逐项预算见 §2.6） |
| `config.py` | 修改 | +11 项 `ai_trade_wake_backlog_* / ai_trade_wake_*position_watch* / ai_trade_signal_wake_*` |
| `.env.example` | 修改 | 新增一段「唤醒调度闭环」注释块，逐项说明默认值与回退方式 |

> 不改：`decider.py` / `prompts.py`（`wake_text` 已是唯一渲染入口）/ `signals.py` /
> `profit_guard*.py` / `edge_gate.py` / `sizing*.py` / `backtest*.py` / 任何 `db/` 文件。

### 3.2 后端测试（`services/quant/tests`）

| 文件 | 新建/修改 | 覆盖 |
| --- | --- | --- |
| `test_ai_trade_wake_backlog.py` | 新建 | T-1/T-2/T-2b~T-5：节流口径与 `waitedS`/`total_drops` 复算、补跑判定、链上限、consume 语义 |
| `test_ai_trade_signal_preview.py` | 新建 | T-6~T-11、**T-10b（投影 vs 裸 stance 的双向断言）**、T-15b（两道前置闸）、T-15c（在飞守卫不随超时解锁）、T-25（有界克隆等价）、G-8（并发闸与错峰） |
| `test_ai_trade_flip_detector.py` | 新建 | T-12/T-12b~T-14：双条件防抖、`row_key` 未变不计采样、signature 变更重置、锁存与新根解锁 |
| `test_ai_trade_wake_guard.py` | 修改 | `SkipPolicy` 签名迁移 + 轴 B 的 armed / idle / unavailable 三分 + 上限 |
| `test_ai_trade_wake_reason.py` | 修改 | 八态集合断言 + `signal_flip` 优先级 + `flip` 键缺席回退 |
| `test_ai_trade_wake_text.py` | 修改 | `signal_flip` 措辞必含 repaint 警示 + `_backlog_clause` 缺席即逐字回退 |
| `test_ai_trade_wake_context_session.py` | 修改 | 会话级：丢弃→补跑端到端、position_watch 跳过、预演唤醒不污染 board |
| `test_ai_trade_tactical_context.py` | 修改 | `signal_flip` 的 tier 归属（full）与执行披露（repeat） |
| `test_config.py` | 修改 | 10 项新设置的默认值与边界校验 |

### 3.3 前端（`apps/web`）

| 文件 | 新建/修改 | 意图 |
| --- | --- | --- |
| `lib/aiTradeWakeAttribution.ts` | 新建 | 轴 D：`RoundTrip[]` → 按开仓唤醒来源的胜率行（纯） |
| `components/Agent/aiTradeStats/WakeAttributionCard.tsx` | 新建 | 轴 D：只读卡片，样本不足即诚实降级、无归因即整卡不渲染 |
| `lib/aiTradeWakeAttribution.test.ts` | 新建 | 轴 D 纯函数用例（含 unknown 桶与最小样本门禁） |
| `lib/types.ts` | 修改 | `TriggerExplain.meta` 增 `wakeKind?: string`（当前类型未声明该键） |
| `lib/aiTradeTypes.ts` | 修改 | `WakeKind` 增 `signal_flip`；`WakeInfo` 增 `backlog?` / `flip?`；`aiStats` 增 `wakeScheduler?` |
| `lib/aiTrade.ts` | 修改 | `WAKE_LABELS` 增 `signal_flip`；`describeTrigger` 在补跑轮追加 `· 补跑` 标记。**（P2-3）** `describeTrigger` 现在直接 `return WAKE_LABELS[kind]` —— 那是个**共享常量对象**，追加标记必须返回 `{...base, label: base.label + " · 补跑"}` 新对象，就地改写会污染全局标签表并让后续轮次全带上补跑字样 |
| `lib/aiTradeHistoryTypes.ts` | 修改 | `aiStats` 增可选 `wakeScheduler`（老记录缺键即降级） |
| `components/Agent/AiTradeTraceRound.tsx` | 修改 | 轮头渲染补跑徽标与翻转明细（含 `provisional` 提示）。**（P1-9）** 若挂进 `SignalContext` 的 `<details>`，必须同时进它 `:560-572` 的早退判空表——那里正是上一轮 RV-2 的原址 |
| `components/Agent/AiTradeAgentTrace.test.tsx` | 修改 | 只有 backlog / flip、其余输入全空的轮次**仍要渲染**（早退门控回归，钉死镜像点 12） |
| `components/Agent/aiTradeStats/AiTradeStatsLive.tsx` | 修改 | 挂 `WakeAttributionCard`（吃已有 `fullSummary` 同源的 trips） |
| `components/Agent/aiTradeStats/AiTradeStatsDetail.tsx` | 修改 | 同上（历史页签） |
| `lib/aiTrade.test.ts` | 修改 | `describeTrigger` 三级优先级不变 + 补跑标记 + 新 kind 标签 |
| `components/Agent/AiTradeStatsPanel.test.tsx` | 修改 | 卡片挂载 / 无归因时整卡不渲染 |

---

## 四、接口设计

### 4.1 payload 新键（决策器输入，camelCase）

全部挂在既有 `payload["wake"]` 之下，**非相关轮次整键缺席**（不是 `null`）：

```jsonc
"wake": {
  "kind": "signal_flip",              // 八态之一（新增 signal_flip）
  // …a7ed70c 既有 14 键一字不动…
  "backlog": {                        // 仅补跑轮出现
    "droppedIntervals": 1,
    "droppedBars": 1,
    "waitedS": 379.4,
    "chain": 1
  },
  "flip": {                           // 仅 signal_flip 轮出现
    "netStanceFrom": "flat",
    "netStanceTo": "long",
    "strategies": [{"strategyId": 7, "name": "EMA 交叉短线", "from": "flat", "to": "long"}],
    "provisional": true,
    "heldS": 47.3,
    "samples": 4,
    "barTs": 1785600000
  }
}
```

### 4.2 配置（`config.py` → `Settings`；env 前缀 `AI_TRADE_`，已在 desktop `config.js` 的
`AI_TRADE_` 前缀透传白名单内，**无需改 desktop**）

| 键 | 默认 | 约束 | 语义 / 关闭效果 |
| --- | --- | --- | --- |
| `ai_trade_wake_backlog_enabled` | `True` | bool | 轴 A 总闸；False ⇒ 只计数不补跑（观测保留，行为回退） |
| `ai_trade_wake_backlog_max_chain` | `3` | `ge=1, le=20` | 连续补跑上限。**刻意无「0=不封顶」**（P1-6）：无上限的补跑链 = 无上限的 LLM 花费，「不补跑」由上一行的总闸表达 |
| `ai_trade_wake_skip_position_watch` | `True` | bool | 轴 B 总闸；False ⇒ 持仓轮逐字回退「永不跳过」 |
| `ai_trade_wake_position_watch_max_skip_streak` | `3` | `ge=1, le=100` | 持仓连续跳过上限（比空仓的 5 更紧）。同样**无「0=不封顶」**：持仓期无限期不研判能亏真钱 |
| `ai_trade_signal_wake_enabled` | **`False`** | bool | 轴 C 总闸。**首发默认关**（P1-6）：本轴是唯一会占用守着真实资金的 5 秒 tick 与线程池的轴，且镜像 signal-prior 那轮「新轴默认全关、验收达标再翻」的既有做法。晋级条件见 §评审结论 C-1 |
| `ai_trade_signal_wake_hold_s` | `45.0` | `ge=0, le=600` | 翻转须连续保持的时长；0 = 只看样本数 |
| `ai_trade_signal_wake_min_samples` | `3` | `ge=2, le=20` | 翻转须被**有效**观测到的次数（`row_key` 变过才算，§2.3.4）。下限取 2 而非 1（P2-6）：`hold_s=0 + min_samples=1` 是「零防抖」，正是 A-3 明令禁止的形态 |
| `ai_trade_signal_wake_min_gap_s` | `120.0` | `ge=30, le=3600` | 两次信号唤醒的最小间隔（LLM 花费上界） |
| `ai_trade_signal_wake_preview_interval_s` | `15.0` | `ge=5, le=600` | 预演节流（≈每 3 个盯市 tick 一次）；首次到期按 `session_id` 稳定哈希在 `[0, interval)` 内错峰 |
| `ai_trade_signal_wake_timeout_s` | `3.0` | `gt=0, le=30` | 单次预演硬超时；超时即退避（间隔翻倍，上限 300s），**不解锁在飞守卫**（§2.3.2） |
| `ai_trade_signal_wake_stale_s` | `90.0` | `gt=0, le=3600` | 判定价新鲜度闸（P0-2）：`now - _last_px_wall` 超过即不预演。**必须读 `_last_px_wall` 而非 `_last_candle_wall`** |

> 并发上限 `_PREVIEW_MAX_CONCURRENCY = 2` 是**模块级常量而非配置项**：它是防止 N 个分组成员
> 同时把线程池打满的护栏，不是让用户调的旋钮。护栏做成旋钮，等于把护栏交给最不该动它的人。

### 4.3 WS 事件（**加法**，老前端忽略未知键）

- `status.data` 增四个计数：`wakeDrops` / `wakeCatchups` / `positionWatchSkips` /
  `signalFlipWakes`（既有 `wakeSkips` 语义与数值口径**不变**）。
  - `wakeDrops` 读 `WakeBacklog.total_drops`，口径是「**到点了却撞上在飞轮**的唤醒次数」
    （P2-5）——其中一部分若不在飞也会被守卫跳过，故 `wakeDrops` 并不等于「本该真的唤醒」。
    这个口径差必须写进前端 tooltip，否则用户会把它读成「系统漏掉了这么多次决策」。
  - 前端读点须同时补 `lib/aiTradeTypes.ts` 的 `AiTradeSessionRow` 四个可选字段
    （镜像点第 13 条）：status 事件经 `store.patchAiTradeMemberStatus` 展开合并进 `m.session`，
    运行期数据本来就在，漏的只是类型 ⇒ 症状是 tsc 报错而非静默，但极易被「后端已下发」骗过。
- `decision.data.wake` 与 `agent_trace` 的 `round_start.wake` 自动携带 §4.1 的两个新子对象
  （它们本就整对象透传，无需新形参）。
- `decision_skipped` 的 wake 桩增 `backlog` 键（§2.1.5）。

### 4.4 REST / DB

**零新增端点、零新表、零新列、零迁移。**

- 会话终结时 `metrics_json.aiStats.wakeScheduler` 增一个嵌套对象（加法写既有 JSON 列，与
  `cost` / `sizing` 同款调用点合并，见 `session._finalize_run`）：

```jsonc
"wakeScheduler": {"drops": 4, "catchups": 3, "catchupCeilingHits": 0,
                  "positionWatchSkips": 22, "signalFlipWakes": 5,
                  "previewTimeouts": 0, "previewDisabled": false}
```

- 轴 D 完全复用既有 `GET /ai-trade/history/{group_id}` 返回的 `members[].trades`，不加字段。

---

## 五、数据模型

### 5.1 内存态（会话生命周期，进程内）

| 归属 | 字段 | 说明 |
| --- | --- | --- |
| `WakeBacklog` | `dropped_intervals` / `dropped_bars` / `first_dropped_wall` / `last_dropped_wall` / `last_noted_wall` / `total_drops` / `total_catchups` / `ceiling_hits` / `chain` | 待办账本。`consume()` **只**清 `dropped_intervals` / `dropped_bars` / `first_dropped_wall` / `last_dropped_wall`；`last_noted_wall`（跨轮节流基准）、三个 `total_*` 与 `chain` 一律保留 |
| `FlipDetector` | `signature` / `first_seen` / `last_seen` / `samples` / `latched` / `last_row_key` | 防抖跟踪与锁存；`last_row_key` 使「行情没动过的采样不算证据」（P0-2） |
| `WakeRuntime` | `_backlog` / `_detector` / `_signal_flip_wakes` / `_position_watch_skips` / `_alerts` | **纯同步**判定与观测（异步/线程/退避一律不在此） |
| `PreviewRunner`（`signal_preview.py`） | `_inflight` / `_next_due_wall`（首次按 `session_id` 稳定哈希错峰）/ `_interval_s`（退避可增长，上限 300s）/ `_timeouts` / `_errors` / `_disabled` | 预演编排态；`_inflight` 只由内层任务的 done-callback 解锁（§2.3.2） |
| `AiTradeSession` | `_live_row: dict \| None` / `_preview: PreviewRunner` | 未确认根临时行 + 预演编排器句柄（**仅这两项**；在飞标志已随编排一起下沉，不再挂在会话上） |

### 5.2 持久化

- `trades.explain_json.meta.wakeKind`：**已存在**（a7ed70c），本轮只**读**不写。
- `runs.metrics_json.aiStats.wakeScheduler`：加法写，老记录缺键 ⇒ 前端整段隐藏。

### 5.3 前端类型（增量）

```ts
// lib/aiTradeTypes.ts
export type WakeKind =
  | "candle_close" | "bar_advanced" | "signal_flip" | "resting_order"
  | "profit_tier" | "price_breakout" | "position_watch" | "heartbeat";

export interface WakeBacklogInfo {
  droppedIntervals?: number; droppedBars?: number; waitedS?: number; chain?: number;
}
export interface WakeFlipInfo {
  netStanceFrom?: string; netStanceTo?: string;
  strategies?: { strategyId?: number; name?: string; from?: string; to?: string }[];
  provisional?: boolean; heldS?: number; samples?: number; barTs?: number;
}
export interface WakeInfo {
  /* …既有字段不动… */
  backlog?: WakeBacklogInfo | null;   // 仅补跑轮
  flip?: WakeFlipInfo | null;         // 仅 signal_flip 轮
}

// lib/types.ts —— TriggerExplain.meta 增一键（当前未声明，轴 D 读它需要）
meta?: { /* …既有… */ wakeKind?: string };
```

---

## 六、测试与验收标准

### 6.1 必须先红后绿的用例（T-x；改业务码之前先写）

**轴 A**

- **T-1** `note_interval` 在 `min_gap_s` 内重复调用只计一次；跨过 `min_gap_s` 才 +1。
  **反例断言（P1-1 修正，v1 此处算错了）**：`min_gap_s=120`，从 `t+0` 起每 5 秒调一次、共
  379 秒（76 次调用）⇒ `dropped_intervals == 3`（而非裸 tick 的 76，也不是 v1 写的 1），
  且 `first_dropped_wall == t+120`、`wire(t+379)["waitedS"] == pytest.approx(259)`。
- **T-2** `note_bar` 每次都计数（3 根被丢弃的确认根 ⇒ `dropped_bars == 3`）。
- **T-2b** `total_drops` 的自增点唯一：跑完 T-1 + T-2 的组合序列后
  `total_drops == 3 + 3`，且 `consume()` 之后 `total_drops` **不变**、`pending is False`。
- **T-3** `plan_catchup`：有待办 + 分类为 `bar_advanced` ⇒ 返回带 `backlog` 的 wake；
  有待办但分类为 `heartbeat`（守卫会跳过）⇒ 返回 None **且待办被清空**（不留悬空）。
- **T-4** 链上限：`max_chain=2` 时第 3 次连续补跑返回 None 且 `ceiling_hits == 1`；
  一个非补跑轮启动后 `chain` 归零。
- **T-5**（会话级）`state != "running"` 时 `_maybe_catchup_wake` 恒不起轮——`stop()` 取消
  决策任务触发的 `finally` **不得**在 flatten 之后凭空再开一轮真实资金决策。

**轴 B**

- **T-6** `position_watch` + `guardStatus="armed"` + 总闸开 ⇒ 跳过；`"idle"` / `"unavailable"`
  / `None` ⇒ **不跳过**（三条各一个断言，杜绝未来把 `unavailable` 误当安全态）。
- **T-7** `skipStreak` 触到 `position_watch_max_skip_streak` ⇒ 强制放行；总闸关 ⇒ 逐字回退
  「持仓轮永不跳过」。
- **T-8** `heartbeat` 分支在 `SkipPolicy` 迁移后行为**逐字不变**（迁移前后同参对拍）。

**轴 C**

- **T-9（零污染，本轴的核心不变式）** 造一个持有内部状态的策略（用真实
  `momentum_breakout`），记录预演前 `strategy._peak` / `shadow.pos` 的深拷贝快照，跑 10 次
  预演，断言真身**逐字段不变**；同时断言克隆体确实被步进过（否则测试会因为「什么都没跑」
  而假绿）。
- **T-10** `flip_signature`：仅单策略投影立场变、**投影净共识**不变 ⇒ None；投影净共识翻转
  ⇒ 稳定 signature（同一组翻转两次调用得到相同字符串，且与策略遍历顺序无关）。
- **T-10b（P0-1 的钉子，本轴最重要的一条）** 用真实 `momentum_breakout` 造两个场景：
  ① **排队成交场景**——上一确认根 `on_candle` 喊了 `open_long`（`_pending` 非空）、本未确认根
  无新边沿。断言 `flip_signature(base, preview) is None`（**投影**基线已经是 `long`）。若实现
  误用裸 `stance`，此处会返回一个 `flat>long` 的翻转 ⇒ 每根有排队单的 K 线多烧一次 LLM。
  ② **本根新边沿场景**——上一确认根无信号、本未确认根 `on_candle` 返回 `open_long`。断言
  `flip_signature` 返回非 None 且 `netStanceTo == "long"`。若实现误用裸 `stance`，此处恒为
  None ⇒ 整个轴 C 对它最想抓的东西完全失明。**两条断言方向相反，任一实现走样都会红。**
- **T-11** `clone_for_preview` 遇不可深拷贝对象 ⇒ 返回 None，且会话其余路径不受影响。
- **T-12** `FlipDetector`：时长够但样本不足 ⇒ `pending`；样本够但时长不足 ⇒ `pending`；
  两者都够 ⇒ `confirmed`；signature 中途变更 ⇒ 计时与样本双重置。
- **T-12b（P0-2）** `row_key` 不变的重复采样**不计 samples、不推进 last_seen**：同一
  `signature` + 同一 `row_key` 连喂 10 次、跨越 `hold_s` ⇒ 仍是 `pending`
  （「保持了 45 秒」必须是 45 秒里真的看了新数据）。
- **T-13** 锁存：`confirmed` → `note_fired` → 同 signature 恒 `idle`；`note_new_bar` 后可再次
  进入跟踪。
- **T-14**（会话级）信号唤醒起的决策轮，`payload["signalBoard"]` **等于确认根信号板**
  （逐条对拍 `self._last_board`），预演结果只出现在 `payload["wake"]["flip"]`。
- **T-15** 最小间隔：`confirmed` 但距上轮 < `min_gap_s` ⇒ 不唤醒**且不锁存**（下次仍可唤醒）。
- **T-15b（P0-2 前置闸）** `preview_snapshot`：`live_row["ts"] == last_confirmed_ts` ⇒ None
  （帧里绝不出现两根同 ts）；`now_wall - last_px_wall > stale_s` ⇒ None。两条各一个断言。
- **T-15c（P1-2 在飞守卫）** 让 `run_preview` 超时一次，断言 `_inflight` 在**内层任务真正结束
  之前**保持 True（下一次 `run()` 直接返回 None），任务结束后自动解锁。这条正是 R-6 的证据；
  照 v1 的 `finally` 写法此断言必红。
- **T-25（P1-5 有界克隆等价门禁）** 同一初始 broker，一路用 `clone_shadow`、一路用
  `copy.deepcopy`，各自跑 K=20 步相同的帧，断言两侧 `stance` / `latest_signal` 序列**逐步相等**
  且 `pos` 逐字段相等。没有这条，「只复制会影响 stance 的字段」就只是一句声明。

**轴 D（前端）**

- **T-16** `attributeByWake`：按开仓腿 `explain.meta.wakeKind` 归集；缺该键的往返落
  `unknown` 桶且排末位。**（P1-8 补）** 追加一条反手往返用例：`entry.explain.kind="atomic"`
  带 `wakeKind="candle_close"`、`exit.explain.kind="reverse"` 带 `wakeKind="signal_flip"`
  ⇒ 该往返必须归到 `candle_close` 桶。`reverse` 是**平仓腿**的解释、同样带 `wakeKind`，
  用它归因就等于按「离场时是什么叫醒的」切表，答非所问。
- **T-17** 样本 < 5 的桶 `winRate === null` / `expectancyUsd === null`（**绝不**返回 0）。
- **T-18** `hasWakeAttribution` 全 unknown ⇒ false ⇒ 组件整卡不渲染。

**跨轴**

- **T-19** 八态优先级表：`candle_close > bar_advanced > signal_flip > resting_order >
  profit_tier > price_breakout > position_watch > heartbeat`，逐行参数化。
- **T-20** prompt：`signal_flip` 文案必含「未确认根」「repaint」「以最近确认根为准」三项语义
  锚点；`backlog` 缺席时 `format_wake_for_prompt` 输出与 a7ed70c **逐字节相同**。

### 6.2 零回归门禁（G-x；全绿方可提交）

- **G-1** 四个总闸全关（`wake_backlog_enabled=False`、`skip_position_watch=False`、
  `signal_wake_enabled=False`、`wake_reason_enabled=False`）⇒ payload / prompt / WS 事件
  与 `a7ed70c` **逐字节一致**（用冻结夹具 sha256 对拍，沿用该特性已建立的冻结门禁范式）。
- **G-2** 四个总闸全开但**无任何丢弃、无翻转、空仓** ⇒ payload 中 `wake.backlog` /
  `wake.flip` 两键**缺席**（不是 `null`）。
- **G-3** `services/quant` 全量 `pytest` 串行跑，红灯集合与干净 `HEAD` worktree 实测基线
  **逐行一致**、零新增。（已知基线：`test_factor_library_expansion` 的孤儿测试 + 共用临时
  DB 的顺序敏感用例；**必须**用干净 worktree 实测对账，不得引述本文档。）
- **G-4** `apps/web` 全量 `vitest` 全绿（`NODE_ENV=test`）；`tsc --noEmit` 无**新增**错误
  （`WeightBatchArena.test` 为存量）。
- **G-5** `session.py` 净增 **≤72 行**（逐项预算见 §2.6，须按表核对而非只看总数）；
  **三个**新建后端模块（P2-2：v1 写「四个」，§3.1 只列了三个）：`wake_backlog.py` ≤160 行、
  `flip_detector.py` ≤140 行、`signal_preview.py` ≤280 行（含 `PreviewRunner`）；
  无文件越 800 行红线（`session.py` 的 1583 行是**存量**欠账，本轮只保证不恶化）。
  `market_window.py` 重构后仍 ≤400 行。
- **G-6** 预演不触碰真身：在会话级测试里对 `self._specs` / `self._shadows` 做深拷贝快照，
  一整轮盯市 + 预演之后逐字段对拍（T-9 的会话级版本）。
- **G-7** 盯市 tick 不被拖停。**（P1-4 修正）** v1 让假预演 `sleep 2` ——`sleep` 会**释放
  GIL**，于是这条门禁在任何实现下都会绿，测不到真问题。假件必须是 **CPU 密集且持有 GIL** 的
  （例如一个 `while time.monotonic() - t0 < 2: x += 1` 的纯 Python 忙循环）。断言：
  `signal_wake_timeout_s=0.2` 下 `_monitor_tick` 在 ≤1s 内返回，且 `_maybe_profit_exit`
  在同一 tick 内**确实被调用过**（用 spy 计数，不看耗时）。
  > **同时如实记录本方案的能力边界**：`asyncio.to_thread` 对 GIL 密集负载**不提供真并行**，
  > 它只保证事件循环能按 `sys.getswitchinterval()`（默认 5ms）粒度**交错**执行，从而让
  > `wait_for` 的超时能按时触发、盯市 tick 不被无限期挂住。它**不**保证盯市 tick 的延迟不受
  > 影响。这就是为什么本轴还需要节流、并发闸、退避与首发默认关闭四道减压，而不是只靠线程。
- **G-8（P1-2 分组维度）** 三个会话共用一个 `PreviewRunner` 并发信号量：同一 tick 让三者都
  到期，断言至多 2 个进入预演、第三个**立即**返回 None（不排队等待）；且三者的
  `_next_due_wall` 初值互不相同（按 `session_id` 稳定哈希错峰，可复现）。

### 6.3 验收标准（A-x）

- **A-1**（轴 A）模拟一次 379 秒在飞轮 + 期间 1 根确认根收线：轮结束后**同一事件循环 tick 内**
  起补跑轮，`wake.backlog.droppedBars == 1`，prompt 含补跑子句。定时唤醒关闭
  （`wake_interval_s=0`）时同样成立——这是旧实现完全做不到的场景。
> **A-2 / A-4 的执行载体（P1-7）**：v1 把它们写成「24 小时模拟回放」，而 §1.3 非目标明写
> 不碰 `ai_auto/backtest.py`，仓库里**不存在**能跑实盘调度语义（墙钟 / 在飞轮 / 未确认根）的
> 回放器——不可执行的验收等于没有验收。两条一律改为**确定性会话级轮数对拍**：沿用
> `tests/test_ai_trade_wake_context_session.py` 既有夹具（假 decider + `_elapse_timer` 推进
> 墙钟 + 直接 `feed_candle` 注入合成 K 线），用 spy 计 `_run_decision_round` 的调用次数，
> **同一条 K 线 / 价格序列**在「四轴全关」与「按 A-x 指定的总闸组合」两次运行下对拍。
> 全程无网络、无真实 LLM、无真实时钟 ⇒ 可复现、可进 CI。

- **A-2**（轴 B）合成序列：持仓 + `guardStatus="armed"` + 价格静止 2 小时（`wake_interval_s=120`
  ⇒ 基线 60 轮）。断言：轮数 ≤ 24（下降 ≥ 60%）；任意两次真实轮之间的间隔
  ≤ `4 × wake_interval_s`（上限 3 的直接推论）；把 `guardStatus` 换成 `idle` /
  `unavailable` 重跑，轮数与基线**逐次相等**（这两态一次都不许跳）。
- **A-3**（轴 C）repaint 压力测试：构造一段每 5 秒在 `flat↔long` 之间抖动的预演序列，
  在 `hold_s=45 / min_samples=3` 下**零唤醒**；构造一段持续 90 秒的单向翻转，恰好唤醒 **1** 次。
- **A-4**（花费中性，**硬性**）同一条 15m / 2 分钟唤醒的合成序列（≥ 24 根 K 线，其中至少
  8 根上有策略排队单——这正是 P0-1 会误触发的形态），四轴全开的总决策轮数 **≤** 四轴全关基线。
  若超出，必须上调 `signal_wake_min_gap_s` 直至满足——本轮的立项理由之一就是省钱，新增一个
  唤醒源不得把它吃回去。
  > 这条同时是 P0-1 的**回归护栏**：若判据退回裸 `stance`，那 8 根排队单会各多出一轮，
  > A-4 必红。请把「至少 8 根带排队单」写进夹具注释，别让后人把它优化掉。
- **A-5**（轴 D）历史页签在含 ≥5 笔同源往返的会话上渲染出胜率数字；在全为老成交的会话上
  整卡不渲染（不出现一张全「未记录来源」的表）。
- **A-6** 观测自洽：`status.wakeDrops == aiStats.wakeScheduler.drops`（两者**同读**
  `WakeBacklog.total_drops`，见 §2.1.2 口径 3——没有那条口径这两个数可以各自漂），
  `wakeCatchups ≤ wakeDrops`，`positionWatchSkips ≤ wakeSkips`。三条恒等/偏序写成断言。
- **A-7**（轴 C 首发形态）`ai_trade_signal_wake_enabled` 默认 `False` 时：`PreviewRunner`
  一次都不构造克隆、不投递线程、`_live_row` 仍照常记录（零成本空转）；`payload` / prompt /
  WS 与只开 A/B/D 三轴时**逐字节相同**。这条是轴 C「默认关也能安全合入」的证据。

---

## 七、风险与缓解

| # | 风险 | 影响 | 缓解 |
| --- | --- | --- | --- |
| R-1 | 补跑与定时唤醒**双起轮**（补跑刚起、下一个 tick 定时又到点） | 并发两轮 LLM，真实资金路径竞态 | 补跑起轮时同步刷新 `_last_decision_started_wall` 并认领 `_deciding`；两处认领的 check→set 之间均无 `await`（既有范式） |
| R-2 | 补跑链失控（慢 LLM + 快 K 线） | 背靠背烧 token | `max_chain=3` 硬上限 + 触顶即弃单 + `ceiling_hits` 计数上报 |
| R-3 | 轴 B 在保护层**误报** armed 时跳过 | 持仓无人看管 | 只认 `armed`/`breakeven_armed` 两态；`unavailable` 是 fail-open 的降级态，明确不跳；且有 3 轮上限强制放行 |
| R-4 | 预演污染真身（漏拷某个引用） | 策略状态错乱，**影响真实下单** | T-9/G-6 双层逐字段对拍；`deepcopy` 整个 spec 与 shadow（不做浅拷贝优化）；克隆失败即整轴关闭 |
| R-5 | 预演占住事件循环 | 盯市停摆、保护层失效 | 快照/深拷贝在循环线程（微秒级），重活进 `to_thread` + 硬超时 + 在飞守卫 + 超时退避；G-7 门禁 |
| R-6 | `to_thread` 超时后线程仍在跑 | 线程堆积 | 在飞守卫**由内层任务的 done-callback 解锁**（`shield` + `add_done_callback`），超时**不**解锁——v1 把它放在 `finally` 里等于没有守卫（P1-2）；再叠模块级并发信号量（≤2）与退避（间隔翻倍，上限 300s），连续失败等价于自动关闭 |
| R-7 | repaint 把 AI 叫醒在最抖的时刻 | 比现状更糟 | 只看**投影净共识**翻转（§2.3.1b）+ 双条件防抖 + 「数据变过才算一次采样」+ 锁存 + `min_gap_s`；A-3 压力测试是放行条件 |
| R-8 | 预演结果被误当作既成事实进入决策 | 在未收线数据上开单 | 结构性隔离：`board` 形参恒为确认根板，`flip` 只进 `payload["wake"]`，文案强制标注 `provisional` / repaint；T-14 钉死 |
| R-9 | 新 kind 漏接某个展示点（a7ed70c RV-1 的复发） | UI 出现裸英文 kind | §2.4 的 11 条镜像点清单逐条勾选，其中两条是**测试**（第 10、11 条）会自然拦截 |
| R-10 | 轴 D 在样本极少时给出误导性胜率 | 用户据 2 笔样本改配置 | `MIN_SAMPLES=5` 以下一律 `null` 并显示「样本不足（n=x）」；与 `_t_stat` 的 `n<2 ⇒ None` 同款纪律 |
| R-11 | 归因口径被误读成「平仓来源」 | 保护层平仓整类被误判 | 卡片脚注写死「按**开仓**唤醒来源归集」；函数名与 docstring 同步声明 |
| R-12 | `SkipPolicy` 签名变更漏改调用点 | 运行期 `TypeError` | 全仓库仅一个调用点（`wake_runtime.should_skip`）+ 一个测试文件；G-3 全量跑覆盖 |
| R-13 | 预演在**未更新**的 `_live_row` 上反复采样，凑够 `hold_s` 后报一个「已保持 45 秒」的假翻转 | 假证据进真实资金决策链 | 两道前置闸（重复 ts / 判定价新鲜度，读 `_last_px_wall`）+ `row_key` 变过才计一次采样（P0-2；T-12b / T-15b） |
| R-14 | 分组 N 个成员的预演同时投递，打满默认线程池、把所有会话的盯市 tick 一起拖慢 | 多标的下保护层集体迟滞 | 模块级并发信号量（≤2，非阻塞探测、拿不到就跳过本 tick）+ 按 `session_id` 稳定哈希错峰；G-8 门禁（P1-2） |
| R-15 | 有界克隆漏掉某个影响 stance 的字段 | 预演结论与真身分叉，模型据一份错板做决策 | T-25 等价门禁（与整对象 deepcopy 跑 K=20 步逐步对拍）；`specs` 侧仍走整对象 deepcopy，只有 `SimBroker` 这个**本仓库自有、字段可枚举**的对象走显式克隆（P1-5） |

---

## 八、实现顺序与提交注意

1. **先写测试**（T-1~T-20 的红灯），再动业务码。修 bug 类改动（轴 A/B）必须先有能稳定复现
   旧行为的用例——CLAUDE.md §七 明文要求。
2. 实现顺序：轴 B（最小、纯函数）→ 轴 A（会话接线）→ 轴 D（纯前端，与后端解耦可并行）→
   轴 C（最大、最需要压力测试）。轴 C 最后做，保证前三轴不被它的返工阻塞。
   **轴 C 首发默认关**（§4.2 / P1-6）：即便它整轴返工或被砍，A/B/D 三轴仍可独立合入并生效。
   请按这个顺序分 commit，不要攒成一个大提交——轴 C 是本轮唯一有可能被要求回退的部分。
3. `market_window.build_frames_for` 的重构必须**先**保证 `build_strategy_frames()` 的行为
   逐字不变（同参对拍一次），再让预演复用。
4. **工作树纪律**（本仓库反复踩过）：`git status` 显示大量**他人在制**的未跟踪与已改文件
   （`.claude-index/`、`docs/diagnoses/*`、`desktop/*`、`live_auto_repositories.py`、
   `test_factor_library_expansion.py` 等）。提交时：
   - 一律 `git add <具体路径>`，**严禁** `git add -A`；
   - 混合文件（如 `config.py` / `.env.example` 若同时含他特性 hunk）必须按 hunk 行级精确
     暂存：切 `@@` 段落 → `git apply --cached --recount`，patch 以 **bytes** 读写
     （Windows 下 `write_text` 会翻 CRLF 弄坏 patch）；
   - `desktop/src/lib/config.js` 本轮**零触碰**（`AI_TRADE_` 前缀已覆盖新 env）。
5. 门禁实测纪律：后端全量 `pytest` **不可并发**（共用临时 DB）；`pytest -q` 有块缓冲，须
   `python -u ... > log`。红灯基线**必须**用干净 `HEAD` worktree 实测对账，不得引述文档。
6. 提交信息以 `feat:` 前缀 + 一句话摘要；本轮不做 `git commit` 的是设计与开发节点，提交由
   Subtask #3 负责。

---

## 九、实施过程发现的方案缺陷（Subtask #2 填写）

> 实现者若发现本方案与真实代码冲突、或存在更优解，**就地修正代码并在此追加一节**说明：
> 冲突点、选择的解法、以及为什么。空着提交视为「实施过程零偏离」，评审会按这个断言核对。

### D-1（阻断级）· §2.1.4 的 `chain` 自增顺序与它自己 §2.8 的期望值矛盾

§2.1.4 的 `plan_catchup` 草图是 `wake["backlog"] = wire(now)` → `consume()` → `chain += 1`，
即 wire **先于**自增；而 §2.8 的时间线明写补跑轮下发 `wake.backlog = {…, chain: 1}`。按草图实现，
第一次补跑下发的是 `chain: 0` —— **一个正在补跑的轮次说自己链长为 0 是无意义的**，前端「补跑链第 N 轮」
的文案也就永远错一位。

**选择的解法**：把 `note_catchup()`（`chain += 1` + `total_catchups += 1`）提到 `wire()` **之前**，
按 §2.8 的期望值实现，并在代码里就地写明这条取舍。会话级用例 `…catchup_round_in_the_same_tick`
断言 `backlog.chain == 1` 钉死。

### D-2 · `waitedS` 在 `decision_skipped` 事件上的参照时刻

§2.1.5 要求丢弃事件的 wake 桩带 `backlog` 键，但没说这一刻 `wire()` 的 `now_wall` 取什么。取
`time.time()` 会让「已等待」在**丢弃发生的同一瞬间**就带上一个与事实无关的抖动值。实现取
`WakeBacklog.last_dropped_wall`（即刚刚这次丢弃的时刻）⇒ 该事件上的 `waitedS` 语义是
「自首次丢弃到**本次**丢弃的间隔」，是一个可复算的量。补跑轮仍按 §2.1.2 用当下墙钟。

### D-3 · 轴 B 的分列计数改由 `should_skip` 就地记录，而非新增一个 session 侧调用

§2.2.4 要求「额外分列 `positionWatchSkips`」。若在会话里再加一行
`self._wake.note_skip_kind(wake.get("kind"), counted)`，就会出现**第二个**读 kind 的地方，且
`session.py` 的行数预算被挤占。实现改为：`WakeRuntime.should_skip` 在判定为「跳过」时就地记下
`_last_skip_kind`，`note_skip(counted)` 按**同一节流口径**消费它。判定与计数因此共用一个数，
`session.py` 零新增行，既有 `note_skip(True/False)` 的测试签名一字不动。

### D-4 · 模块级并发闸用普通计数器而非 `asyncio.Semaphore`

§2.3.2 写「模块级 `asyncio.Semaphore(2)` + 非阻塞 `locked()` 探测」。实测下这带来一个纯粹的风险：
`asyncio` 同步原语在竞争时会绑定**创建时所在的事件循环**，而 pytest 每个用例一个新 loop，
跨用例复用同一个模块级信号量会在偶发竞争时抛 `got Future attached to a different loop`。
由于本闸门**从不阻塞等待**（拿不到就跳过本 tick），一个普通整数计数器与信号量语义完全等价，
且全部读写都发生在单一事件循环线程上（无竞态）。实现用 `_inflight_global: int`，G-8 断言不变。
> 副作用：该计数是**进程共享**态，测试必须 autouse 归零——`test_ai_trade_signal_preview.py` 与
> `test_ai_trade_wake_context_session.py` 各有一个 fixture。漏掉它时 A-4 的轮数对拍会在
> 「单跑」与「全量跑」之间漂 1（前一条用例的残留额度会让某次预演被静默跳过），实测踩过一次。

### D-5 · 错峰哈希必须是 `zlib.crc32` 而非内置 `hash()`

§2.3.2 写「按 `session_id` 的稳定哈希错峰」并强调「用稳定哈希而非随机数，测试才可复现」。
Python 内置 `hash(str)` **按进程随机化**（PYTHONHASHSEED），照字面实现会让 G-8 的错峰断言在
每次进程重启后给出不同结果——正是该条要防的东西。实现用 `zlib.crc32(session_id.encode())`。

### D-6（欠账）· `signal_preview.py` 336 行，超 G-5 的 280 行预算

G-5 给三个新建模块的预算是 `wake_backlog ≤160` / `flip_detector ≤140` / `signal_preview ≤280`。
实测 **118 / 87 / 336**：前两个达标，第三个超 56 行。超出部分全部是 docstring —— 本模块承载两条
**阻断级**理由（「为什么必须克隆」与「为什么判据必须是投影立场」），CLAUDE.md §四 要求「复杂业务
规则与踩坑记录必须写清原因」，C-11 也明写「**不要**靠删注释凑数」。继续下沉的两个候选都不可取：
把 `PreviewRunner` 拆成第四个模块与 §3.1 的三模块变更计划冲突（P2-2 已为此统一过一次）；把
`flip_signature` 挪进 `flip_detector.py` 会让那个**零依赖**的纯状态机被迫 import `signals`
（进而拖进 pandas 与 decider）。故如实记账：文件 336 行，距 CLAUDE.md 的 800 行硬红线仍有大量余量，
`session.py` 的净增预算（本轮真正要守的那条）达标。

### D-10（交给 Subtask #3）· 三个混合文件的暂存边界

`git status` 显示本轮之外仍有他人在制改动，提交时**必须**按 §八-4 逐路径 / 逐 hunk 暂存：

| 文件 | 本轮的 hunk | 属他特性、**不得入库**的 hunk |
| --- | --- | --- |
| `services/quant/app/config.py` | 末段 `@@ -218,4 +219,34 @@`（唤醒调度闭环 11 项） | 首段 `@@ -93,7 +93,8 @@`（`ai_trade_enabled` 默认值 False→True） |
| `.env.example` | 末段 `@@ -160,4 +161,41 @@`（唤醒调度闭环注释块） | 首段 `@@ -50,4 +50,5 @@`（新增 `AI_TRADE_ENABLED=true`） |
| `services/quant/tests/test_config.py` | 文件**末尾**新增的 4 个 `test_wake_scheduler_*` / `test_unsafe_wake_scheduler_*` | 文件本身**未跟踪**（`git ls-files` 无此路径），其开头两个 `test_ai_trade_is_enabled_*` 属「总闸默认开启」特性 |

第三行是本轮新出现的形态、上一轮没有：一个**未跟踪**文件里同时装着两个特性。它无法直接
`git apply --cached` 切 hunk，须先 `git add -N services/quant/tests/test_config.py` 让它进入索引，
再按行号切 patch 暂存本轮那 4 个用例。`desktop/src/lib/config.js` 本轮**零触碰**。
`services/quant/aragon.db-shm` / `-wal` 是跑测生成的 SQLite 边车文件，非源码，不入库。

### D-7 · A-4 的合成序列必须让基线里真的出现定时轮

§6.3 的 A-4 只规定了「≥24 根 K 线、其中至少 8 根带排队单」。按字面构造（空仓 + 价格静止）会得到
一个**退化基线**：既有心跳守卫把两侧的定时轮全部吃掉，基线 = 全开 = 纯 K 线轮数，对拍恒真但毫无
意义。实现把序列改为「持仓 + 保护层 `armed`」——那正是轴 B 该省钱的形态，也是让基线里真的出现
定时轮的前提。夹具用一个每 3 根发一条边沿的确定性策略（25 根 ⇒ 恰好 8 根带排队单）。

**C-6 要求写进本节的两个数（实测，`_FakeClock` 驱动、spy 收集每轮 wake，用例已把构成一并钉死）**：

| | 轮数 | 构成 |
| --- | --- | --- |
| 四轴**全关**基线 | **50** | `candle_close` 25 + `position_watch` 24 + `profit_tier` 1 |
| 四轴**全开** | **34** | `candle_close` 25 + `signal_flip` 9 |

净降 **32%**，满足「花费中性」的硬性判据。两处值得记一笔：
- 那 1 轮 `profit_tier`（`idle→armed` 的真实跨档）在全开侧被**同一时刻**的 `signal_flip` 轮吸收
  ——不是被跳过：模型照样在那一刻被叫醒了，只是标签取了优先级更高的那个。轴 B 的守卫对
  `profit_tier` 恒不跳过，这一点由 `test_position_watch_is_never_skipped_when_the_guard_is_not_armed`
  与守卫用例表覆盖。
- 9 次 `signal_flip` 全部来自**本根 `on_candle` 的新边沿**（夹具策略每 3 根发一条），不是 P0-1 那种
  「上一根排队单本根成交」的误触发——若判据退回裸 `stance`，那 8 根带排队单的 K 线会各多出一轮，
  本用例的 34 会变成 42 而必红。

### D-8 · `PreviewRunner.run` 的入参聚合为 `PreviewRequest`

§2.3.5 的草图是 `run(specs, shadows, window, live_row, now_wall=…)`，但 §2.3.3 的两道前置闸还需要
`last_px_wall`（新鲜度闸的唯一合法读数），照草图会变成 6 个形参、越 CLAUDE.md 的「形参 ≤5」。
实现聚合为 `PreviewRequest` 冻结结构体，镜像 `WindowOptions` / `WakeReadings` / `SkipPolicy` /
`InjectionContext` 的既有范式。会话侧因此多 1 行，仍在 §2.6 的 72 行预算内（实测净增 **70**）。

### D-9 · `PreviewRunner.alerts` 需要一个消费点，否则是死代码

§2.6 把 `alerts` 列进公开面，但整份文档没有任何地方消费它 —— 而「克隆连续失败即整轴关闭」之后
`_preview_ready()` 恒为 False，挂在 ready 之后的推送**永远到不了**。实现加 `take_alerts()`
（取走即清，一次性推送不刷屏），并在 `_maybe_signal_flip_wake` 的**最开头**（ready 门控之前）
排空。静默降级与静默保守一样，都会被用户当成故障。

---

## 十、评审记录与结论（Subtask #1 填写）

问题清单、就地修复索引与「核实为正确的关键断言」见文档开头的 **§评审记录**；结论与放行条件
见下方 **§评审结论**。文档版本已升至 **v2**。

---

## 评审结论

### 结论：**有条件通过**

四条轴的**问题诊断**全部对得上真实代码，轴 A / 轴 B / 轴 D 的设计在核实后成立且右尺寸——
轴 A 补的是一个确实存在、确实会「恰好错过关键那根 K 线」的洞；轴 B 的安全前提
（`guardStatus ∈ ARMED_STATUSES`，且保护层未装配时 `guardStatus` 恒 `None`）经代码核实是
fail-safe 极性；轴 D 经四条通道核实确实是既有数据的纯投影，零后端新口径这个取舍是对的。

轴 C 在 v1 里**度量错了对象**（P0-1）——这不是调参问题，是判据选错了物理量，照原样实现会
同时得到「抓不到想抓的」和「每根有排队单的 K 线多烧一次 LLM」两个结果。改用投影立场后本轴
成立，但它仍是四轴里唯一会占用守着真实资金的 5 秒 tick 与线程池的一条，故**首发默认关闭**。

2 条 P0 + 9 条 P1 已在正文就地改完，无遗留。

### 放行条件（Subtask #2 逐条落实，缺一不得进 Subtask #3）

- **C-1（轴 C 的判据）** `flip_signature` 两侧都必须经 `projected_stance`，且 **T-10b 的两条
  反向断言**必须存在并通过。这是 P0-1 的唯一护栏：只写其中一条，另一种走样仍会静默漏过。
- **C-2（预演输入合法性）** `preview_snapshot` 的两道前置闸 + `FlipDetector` 的 `row_key`
  采样判据必须实现，T-12b / T-15b 必须绿。新鲜度**必须**读 `_last_px_wall`；若在代码里看到
  `_last_candle_wall` 出现在预演路径上，视为未落实。
- **C-3（在飞守卫）** 超时**不得**解锁 `_inflight`；T-15c 必须绿。
- **C-4（有界克隆）** `clone_shadow` + T-25 等价门禁；缺 T-25 则退回整对象 `deepcopy`
  （宁可慢，不可错）——但那样必须在 §九 如实记一笔，并说明克隆耗时的实测量级。
- **C-5（计数口径）** §2.1.2 的三条口径逐字实现；T-1 的 `dropped_intervals == 3` /
  `waitedS ≈ 259` 必须能复算出来；A-6 的三条恒等/偏序写成断言。
- **C-6（花费中性）** A-4 必须以「四轴全关 vs 全开、同一合成序列、spy 计轮数」的形式真的跑出来
  并把两个数字写进 §九。夹具里「至少 8 根带排队单」的注释不得删。
- **C-7（默认值）** `ai_trade_signal_wake_enabled` 默认 `False`；两个 `max_*` 上限
  `ge=1`；`min_samples` `ge=2`。A-7 必须绿。
  轴 C 的晋级（默认改 `True`）需要另一轮**人工**观察窗：一个真实会话跑满 ≥24 小时，
  `previewTimeouts == 0`、`signalFlipWakes` 落在预期量级、A-4 在真实数据上仍成立。
  本轮**不**做这次晋级。
- **C-8（镜像点）** §2.4 的 13 条逐条 grep 后在 PR 描述里回执；其中第 12 条
  （`SignalContext` 早退门控）必须有一条只带 backlog / flip 的渲染回归用例。
- **C-9（前端接线）** `AiTradeStatsLive` 的 useMemo 补返回未筛选 `ledger`；
  `AiTradeStatsDetail` 在**详情顶层**用 `buildLedger` 建组级 trips（不要在子组件里各建一份）；
  `describeTrigger` 追加补跑标记时返回新对象。
- **C-10（门禁诚实性）** G-7 的假件必须是 GIL 密集忙循环而非 `sleep`；G-3 的红灯基线**必须**
  用干净 `HEAD` worktree 实测对账，不得引述本文档或上一轮记录。
- **C-11（行数预算）** G-5 按 §2.6 的逐项表核对，不是只看总数。若某一项超出，优先继续下沉
  到 `PreviewRunner` / `WakeRuntime`，**不要**靠删注释凑数——本仓库上一轮已经为这条踩过坑。

### 遗留观察项（不阻塞本轮，记录以免下次重新发现）

1. `session.py` 1583 行是**存量**红线欠账，本轮四轴之后仍会继续增长。下一轮应把
   `_run_decision_round` 的编排整体拆出去，而不是继续在门面层加薄调用。
2. 轴 B 省下来的是「持仓 + armed + 静止」的轮次，而 `disclosure_tier` 对持仓轮恒给 `full`
   ⇒ 省的正是最贵的轮。这条应在 §九 用实测 token 量核对一次，把「省了多少钱」变成一个数。
3. `wakeDrops` 与「本该真的唤醒次数」的口径差（P2-5）目前只靠 tooltip 说明。若后续发现用户
   仍在误读，再考虑分列 `dropsSuppressible`——但那会引入第二个口径，务必先确认真有人误读。


## 十一、实施验证记录（Subtask #2 填写）

### 11.1 变更覆盖面（逐条对照 §三）

**后端源文件（12/12）**：新建 `wake_backlog.py`（118 行）/ `flip_detector.py`（87）/
`signal_preview.py`（336，见 D-6）；改 `wake_guard.py`（+`SkipPolicy` 与 `position_watch` 分支）/
`wake_reason.py`（八态 + `WakeFacts.flip` + wire 的 `flip` 键）/ `wake_runtime.py`（backlog /
detector / §2.6 全部公开面）/ `wake_text.py`（`_wake_signal_flip` + `_backlog_clause` + alsoTrue 标签）/
`tactical_context.py`（`_FULL_TIER_KINDS` 增 `signal_flip`，`_EXECUTION_FULL_KINDS` 不增）/
`market_window.py`（抽 `build_frames_for` + 增 `preview_snapshot` 两道前置闸）/
`session.py`（**净增 72 行**，`git diff --numstat` 实测 +74 −2）/ `config.py`（+11 项）/
`.env.example`（+1 段）。

**后端测试（3 新 + 6 改）**：新建 `test_ai_trade_wake_backlog.py` / `test_ai_trade_signal_preview.py` /
`test_ai_trade_flip_detector.py`；改 `test_ai_trade_wake_guard.py` / `test_ai_trade_wake_reason.py` /
`test_ai_trade_wake_text.py` / `test_ai_trade_wake_context_session.py` /
`test_ai_trade_tactical_context.py` / `test_config.py`。

**前端（3 新 + 10 改）**：新建 `lib/aiTradeWakeAttribution.ts` /
`components/Agent/aiTradeStats/WakeAttributionCard.tsx` / `lib/aiTradeWakeAttribution.test.ts`；
改 `lib/types.ts` / `lib/aiTradeTypes.ts` / `lib/aiTrade.ts` / `lib/aiTradeHistoryTypes.ts` /
`components/Agent/AiTradeTraceRound.tsx` / `components/Agent/AiTradeAgentTrace.test.tsx` /
`components/Agent/aiTradeStats/AiTradeStatsLive.tsx` /
`components/Agent/aiTradeStats/AiTradeStatsDetail.tsx` / `lib/aiTrade.test.ts` /
`components/Agent/AiTradeStatsPanel.test.tsx`。

### 11.2 §2.4 的 13 条镜像点（逐条 grep 后回执）

| # | 落点 | 状态 |
| --- | --- | --- |
| 1 | `wake_reason.WAKE_KINDS` 八元组 | ✅ |
| 2 | `classify_wake` 的 `conditions` 增 `("signal_flip", facts.flip is not None)` | ✅ |
| 3 | `WakeFacts.flip` + wire 的 `flip` 出参（None ⇒ 整键缺席） | ✅ |
| 4 | `wake_text._WAKE_BODY["signal_flip"]` | ✅ |
| 5 | `wake_text._ALSO_TRUE_LABEL["signal_flip"] = "立场翻转"` | ✅ |
| 6 | `tactical_context._FULL_TIER_KINDS` 增 `signal_flip` | ✅ |
| 7 | `_EXECUTION_FULL_KINDS` **不增**（有断言反向钉死） | ✅ |
| 8 | 前端 `aiTradeTypes.WakeKind` 联合类型 | ✅（tsc 兜底） |
| 9 | 前端 `aiTrade.WAKE_LABELS` | ✅（tsc 兜底；同时改为 `export` 供轴 D 复用） |
| 10 | `test_ai_trade_wake_reason.py` 的 `set(WAKE_KINDS) == {...}` | ✅ |
| 11 | `test_ai_trade_wake_text.py` 的逐 kind 措辞参数化表 | ✅ |
| 12 | `AiTradeTraceRound.SignalContext` 的**早退门控**（RV-2 原址） | ✅ 增 `!backlog && !flip`，并有两条「只带 backlog / 只带 flip」的渲染回归用例 |
| 13 | `aiTradeTypes.AiTradeSessionRow` + `AiTradeStatusEvent` 的四个可选计数 | ✅ |

### 11.3 门禁实测

- **G-1 / G-2 / A-7**：总闸全关 ⇒ 四个新键整键缺席（既有 `test_all_three_switches_off_…` 覆盖）；
  四轴全开但无事发生 ⇒ `wake.backlog` / `wake.flip` **缺席**（`test_g2_…`）；轴 C 默认关 ⇒
  `_live_row` 照常记录但一次都不构造克隆、不投递线程（`test_axis_c_default_off_…`）。
- **G-3（红灯基线用干净 `HEAD` worktree 实测对账，未引述任何文档）**：
  - 本工作树全量串行：**1956 passed / 15 failed / 1 skipped**（201s）
  - 干净 `HEAD`（a7ed70c）worktree 全量串行：**1847 passed / 10 failed / 1 skipped**（201s）
  - 逐行 `comm` 对账：**本轮零新增红灯**。差集全部可归因：
    - 只在本树红的 7 条全部来自 `tests/test_factor_library_expansion.py` —— 该文件**未跟踪**
      （`git ls-files` 无此路径），是另一在制特性的孤儿测试，clean worktree 里根本不存在；
      失败原因是 `assert 12 == 20`（因子库尚无那 8 个因子），与本轮无关。
    - 只在基线红的 2 条（`test_agent_manager_detach` / `test_ai_backtest_manager`）是共用临时 DB
      的顺序敏感 flake，本树该次跑绿。
    - 两侧**都红**的 8 条：`test_agent_ws` 的顺序 flake + `test_ai_trade_prior_session` 的 7 条
      （夹具快照的 `trainedAt` 已过期 ⇒ 告警从 `CONTRACT_MISMATCH` 变成 `PRIOR_STALE`，是**时间
      依赖**的存量红灯，单跑同样红，与本轮无关）。
- **G-4**：`apps/web` 全量 `vitest`（`NODE_ENV=test`）**120 文件 / 1281 tests 全绿**；
  `tsc --noEmit` 仅剩 `WeightBatchArena.test.tsx` 的存量错误，**零新增**。
- **G-5**：`session.py` 净增 **72 ≤ 72**（逐项对照 §2.6 的预算表，无一项超）；三个新模块
  118 / 87 / **336**（第三个超 280，见 D-6 如实记账）；`market_window.py` 387 ≤ 400；
  无文件越 800 行红线。
- **G-6 / G-7**：一整轮盯市 + 预演之后真身逐字段不变；**GIL 密集忙循环**假件下 `_monitor_tick`
  在 ≤1s 内返回且 `_maybe_profit_exit` 在同一 tick 内确实被调用过（spy 计数）。G-7 的能力边界已
  如实写进用例 docstring：`to_thread` 对 GIL 密集负载只保证**交错**、不保证真并行。
- **G-8**：三个会话同 tick 都到期 ⇒ 至多 2 个进入预演、第三个立即返回 None（不排队）；
  三者 `offset_s` 实测 `[6, 0, 7]` 互不相同且可复现（crc32，见 D-5）。
- **A-1~A-7**：A-1（补跑端到端 + prompt 补跑子句）、A-2（60→≤24 轮、最大间隔 ≤4×唤醒周期、
  `idle`/`unavailable` 逐次相等）、A-3（repaint 风暴零唤醒 / 单向 90 秒恰 1 次）、A-4（见 D-7）、
  A-6（三条恒等/偏序断言）、A-7（默认形态零成本）全部有用例并通过。A-5 由前端
  `AiTradeStatsPanel.test.tsx` 的三条卡片用例覆盖（历史页签渲染 + 无归因整卡不渲染 + 样本不足降级）。

### 11.4 放行条件 C-1~C-11 逐条落实

C-1 两侧均经 `projected_stance` + T-10b 的**两条反向断言**（排队成交场景必 None / 本根新边沿场景
必非 None）；C-2 两道前置闸 + `row_key` 采样判据，新鲜度读 `_last_px_wall`（全仓 grep 确认预演路径
上无 `_last_candle_wall`）；C-3 超时不解锁 `_inflight`，T-15c 绿；C-4 `clone_shadow` + T-25 二十步
逐步对拍；C-5 三条计数口径逐字实现，T-1 的 `dropped_intervals == 3` / `waitedS ≈ 259` 可复算；
C-6 见 D-7（两个数已写入）；C-7 轴 C 默认 `False`、两个 `max_*` `ge=1`、`min_samples` `ge=2`，
均有 `test_config` 边界用例；C-8 见 §11.2；C-9 三条全落实（Live 补返回未筛选 `ledger`、Detail 在
详情顶层用 `buildLedger`、`describeTrigger` 返回新对象且有「不得污染共享标签表」的专项用例）；
C-10 假件为 GIL 密集忙循环、红灯基线用干净 worktree 实测；C-11 §2.6 逐项核对，超出的一项（D-6）
如实记账而非删注释凑数。

---

## 十二、终审记录与提交边界（Subtask #3 填写）

### 结论：**通过**（4 处就地修复后提交）

逐条对照 §三 变更计划、§2.4 的 13 条镜像点、§6.2 的 G-1~G-8 与 §评审结论的 C-1~C-11，覆盖面与
安全极性全部成立：轴 C 的判据确实两侧都经 `projected_stance`（C-1）、新鲜度全程读 `_last_px_wall`
且预演路径上 grep 无 `_last_candle_wall`（C-2）、`_inflight` 只由 done-callback 解锁（C-3）、
`should_skip_wake` 全仓库仅 `wake_runtime.should_skip` 一个调用点（R-12）、`build_frames_for` 的
抽取与抽取前逐字等价、`store.patchAiTradeMemberStatus` 仍是整对象展开合并（新 status 键不会被静默吞掉）。

### RV-1（覆盖缺口）· 四个调度计数被算出来、类型也补齐了，却**没有任何消费点**

§4.3 明写「`wakeDrops` 的口径差**必须写进前端 tooltip**」，但 §3.3 的前端文件表里没有任何呈现落点，
实现也就只把四个数接到了 `AiTradeSessionRow` / `AiTradeStatusEvent` 的**类型**上。结果是：后端每轮
都在算、WS 每次都在发、类型也不报错，而用户永远看不见——轴 A/B/C「减少的每一次都可解释」这条目标
（§1.3 目标 2）落不了地，同时四条计数成了事实上的死数据。这与 §九 D-9 判 `PreviewRunner.alerts`
为死代码是同一条标准，实现者在自己的模块里守住了，跨到前端时漏掉了。

**修法**：`AiTradeStatsLive.MemberObservabilityRow` 已有的运行观测条上追加一段
`补跑 A/B · 持仓省 N · 翻转唤 M`，`title` 承载 §4.3 点名的那句口径说明（「B 不等于系统漏掉了这么
多次决策」）。四个数**全为 0 即整段不出现**——轴未生效的会话不该看到一排 0，那会被读成「统计坏了」，
与本页「老记录缺键即整行隐藏」的既有纪律同款。两条回归用例（渲染 + 全零隐藏）钉死。
历史页签的 `metrics_json.aiStats.wakeScheduler` 本轮**刻意只留类型不接呈现**：它是嵌套对象、需要
改 `aggregateAiObservability` 的跨成员合并口径，超出本轮变更计划；已记入下方遗留观察项。

### RV-2 · `PreviewRunner` 的克隆失败计数是**会话累计**而非 docstring 宣称的「连续失败」

`_note_clone_failure` 只增不减，故三次分散在数小时里的偶发克隆失败（策略一时持有不可拷贝对象）
也会永久关掉整条轴，而 docstring 与 §2.6 写的都是「克隆**连续**失败即整轴关闭」。一个 fail-open
的观测特性不该因为瞬时抖动就自杀。**修法**：`run()` 里克隆成功即 `_clone_failures = 0`。

### RV-3 / RV-4（风格）

- `tests/test_ai_trade_signal_preview.py` 残留未使用的 `Candle` 导入（ruff F401）——已删；
  feature 全部后端文件 `ruff check` 现为 All checks passed。
- `AiTradeStatsDetail.tsx` 的 `WakeAttributionCard` 之后多了一个空行——已收敛为一行。

### 终审门禁实测（本工作树，含上述四处修复）

- 后端 feature 子集 210 passed；后端全量串行与 §11.3 的基线逐行对账，零新增红灯。
- 前端全量 `vitest`（`NODE_ENV=test`）**120 文件 / 1283 tests 全绿**（较 §11.3 的 1281 多出的 2 条
  即 RV-1 的回归用例）；`tsc --noEmit` 仅剩 `WeightBatchArena.test.tsx` 的存量错误，零新增。

### 提交边界（D-10 的执行结果）

本工作树同时装着他人在制改动，故三个混合文件按内容精确构造暂存态，其余逐路径 `git add`：

| 文件 | 入库 | **不得入库** |
| --- | --- | --- |
| `services/quant/app/config.py` | 末段唤醒调度闭环 11 项 | `ai_trade_enabled` 默认值 False→True |
| `.env.example` | 末段「唤醒调度闭环」注释块 | 新增 `AI_TRADE_ENABLED=true` |
| `services/quant/tests/test_config.py`（本轮之前未跟踪） | 文件头 + 4 个 `test_wake_scheduler_*` / `test_unsafe_wake_scheduler_*` | 开头两个 `test_ai_trade_is_enabled_*` |

`desktop/src/lib/config.js`、`desktop/scripts/*.mjs`、`app/db/live_auto_repositories.py`、
`tests/test_factor_library_expansion.py`、`aragon.db-shm` / `-wal`、`.claude-index/`、`.agentmesh/`、
`docs/diagnoses/*` 与其余 `docs/plans/*` 一律留在工作区未暂存。

### 遗留观察项（追加）

4. 历史页签尚未呈现 `aiStats.wakeScheduler`（RV-1 只接了「当前」页签）。要接需要把
   `aggregateAiObservability` 从扁平键扩到嵌套对象的跨成员合并，属独立增量。
5. 轴 C 的晋级（默认改 `True`）仍待 C-7 那次 ≥24 小时人工观察窗，本轮未做。

---
