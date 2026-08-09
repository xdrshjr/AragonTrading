# AI 自动交易 · 换手预算与交易所兜底真值化（交易周期模式 · 第三轮）

> 文档版本 **v2**（评审后修订；v1 → v2 的差异见 §评审记录）｜特性目录
> `docs/plans/ai-trade-turnover-budget-and-backstop/`
> 上游：`ai-trade-holding-horizon-modes`（第 1 轮，已合入 `11246ae`）、
> `ai-trade-horizon-acceptance-loop`（第 2 轮，**已实现但尚未提交**，见 §1.4）
> 一句话：**把「交易周期模式」的换手纪律补到开仓侧，并把「交易所侧还有兜底」这句话从本地记忆
> 升级为交易所核实过的事实。**

---

## 评审记录（v1 → v2）

评审范围：逐节按**可行性 / 完备性 / 一致性 / 规模合适性**四维核对，并把 v1 §1.4 的 18 条依赖
**逐行回到源码复核**。

**先说结论中好的那一半**：v1 的事实表 18 条**逐条属实**（`gated_kind` 的豁免矩阵确在
`hold_horizon.py:429-444`、`derive_min_hold_s` 确在 `225-231`、`TURNOVER_HARD_CAP` 确在
`acceptance_text.py:37` 且该模块确实**零 ai_auto import**、`get_algo_pending_orders` 确在
`trade_client.py:239-247`、`_normalize_algo_order` 确在 `okx/trading.py:689-714`、
`exchange_oco_px` 确实首选本地成交回执……）；§1.1 的两个关键算术也对
（`intraday_swing` 的 `86400/7200 = 12` 次/日 vs 验收阈 `min(86400/14400,24) = 6`）。四个预设的
派生值表与 `PROFILES`（`hold_horizon.py:98-131`）逐格相符。故**本轮的问题全部出在设计层，不在
考证层**——这在本仓库的历次评审里是少见的好起点。

以下按 P0（阻断，必须改）/ P1（必须改）/ P2（建议）分列。**P0 与 P1 已在本文档正文中逐条修复**，
修复落点写在每条末尾。

### P0（4 条，全部已修）

| # | 问题 | 为什么是 P0 |
|---|---|---|
| **P0-1** | **G4/G5 会拦下反手，等于把「保持持仓」的权力交给了换手预算。** v1 §2.2.2 写死 `_GATED_ACTIONS = ("open_long","open_short")` 且判定式**不看当前持仓方向**。但在本系统里，「持有空头时决策 `open_long`」**就是反手**——它含一条平仓腿。被降级为 `hold` 之后那条平仓腿不发生，**持仓被留在原地**。`trend_hold` 的速率是 1 次/日、容量 2，两次往返之后令牌要 ~20 小时才补满一枚：这段时间内**所有反手都会被拦**。这与本模块自己写在 docstring 顶部的不变式 1（「只拦开仓」）和放行条件 6 正面冲突，且是**真实资金**上的安全债 | 一个统计口径获得了持仓生杀权 |
| **P0-2** | **「覆盖率不可知」这个状态表达不出来，会被写成「覆盖率 0」⇒ 误报裸露 + G6 误拦。** `_normalize_algo_order`（`okx/trading.py:689-714`）只透出 `sz` / `reduceOnly` / `posSide` / `side`；而随单 TP/SL 转成算法委托后，OKX 常见形态是 **`sz` 为 0（配 `closeFraction=1` 表示全仓）**、`reduceOnly` 在单向持仓模式下也可能缺省为 `false`。v1 的判据是「方向正确 + `reduceOnly` + 带 `slTriggerPx` 的**张数**求和」——这三条里有两条会在真实数据上落空，结果是 `coveredSz=0 ⇒ coverageRatio=0 ⇒ mismatch=True`：prompt 会对模型高喊「此刻没有任何持久化兜底」、G6 会拦掉全部加仓。**本轮唯一的存在理由是消除一句没核实的话，结果却造出一句核实错的话，比原状更坏**。另有一个除零：限价挂单期间 `projected` 被刻意不写（`executor.py:279` I-2a），`position_sz` 可能为 0 | 把「不知道」渲染成「确定的坏消息」，且违反本文档自己的 §5.4 零样本口径 |
| **P0-3** | **G4/G5 唯一真正生效的状态（空仓）恰好是 prompt 里那条「本层不约束开仓」的分支。** `format_horizon_for_prompt`（`hold_horizon_text.py:145-146`）在 `status == flat` 时**直接 return** 一行「当前空仓：本层**不约束开仓**」。v1 §2.2.7 只说「在持仓周期段追加一行」，没有触及这条早退——落地后模型每一轮都会被明确告知「开仓不受限」，然后开仓被拦，再把「被拦」当新证据自我怀疑。这正是 v1 自己在 §2.2.7 引用的 `hold-inertia` 教训 | 后端算了、模型看不见，且是主动说反话 |
| **P0-4** | **`note_close` 拿不到「被平掉的方向」，`same_side` 作用域会恒不匹配 ⇒ G4 静默空转。** 实盘 `_dispatch` 传进来的 close 成交 `trade["side"] == "close"`（`session.py:297`、`executor.py:819-821`，`positionAfter` 为 0）；回测更直接——`_track_horizon_epoch` 传的是**空字典** `note_close({}, bar_ts)`（`backtest.py:588/596`）。若实现按 v1 §4.5 的 `on_close_fill(..., side=...)` 顺手取 `trade["side"]`，`cooldown_side` 会变成字符串 `"close"`，而 `_blocks` 比对的是 `open_long`/`open_short` ⇒ **永远不拦、永远不报错** | 与仓库既有的「G1 在回测里恒不生效且不报错」同一类静默失效 |

### P1（10 条，全部已修）

| # | 问题 | 修复落点 |
|---|---|---|
| **P1-1** | **wire 键名认错了。** v1 §4.3 说「`tradingHorizon` 快照新增两个嵌套对象」。实际上 `tradingHorizon` 只出现在**两个地方**：① 启动请求 / `config_json` 里的**契约**（`AiTradeHorizonRule`）；② 决策 payload（`HorizonRuntime.inject` 写给 LLM 的）。前端能看到的运行期**快照**键叫 **`horizon`**（`session.py:1557` `_status_payload`、`1485` decision 事件、`trace.horizon_event`；类型在 `aiTradeTypes.ts:683/816/853`）。照 v1 写会做出一个前端永远读不到的键 | §4.3 / §5.4 / §2.4.2 |
| **P1-2** | **运行期卡片挂错了层级。** `AiTradeStatsLive` 收到的是 `AiTradeGroupState`，其 `tradingHorizon` 是**分组共享的契约**；实时快照在 `member.session.horizon`（`patchAiTradeMemberStatus` 整体 spread）。而换手状态与兜底覆盖率是**每成员一份**（每个成员是独立 `AiTradeSession`、独立持仓、独立令牌桶）。v1 画的单张卡没有定义多标的下显示谁 | §2.4.2 |
| **P1-3** | **冷却到期不主动补推 status ⇒ 卡片会一直挂着「冷却中」。** 仓库已为同款问题写过专门的补丁：`_expire_profit_cooldown`（`session.py:1333-1336`）「不推则前端 chip 会一直挂着（后端已不再拦，UI 却说在拦）」。G4 是同一形状，v1 未安排 | §2.4.2 / §3.2 |
| **P1-4** | **放行条件 3 按 v1 的函数集是不可满足的。** `resolve_thresholds` 除 `max_turnover_per_day` 外还产出 `derived_turnover_per_day`（未夹硬上限的原值），它是 `acceptance_text.turnover_note:114-118` 那句弱门槛自认的唯一依据。只搬 `derive_max_turnover_per_day` 一个函数，`86400/lo` 必然仍留在 `horizon_acceptance.py` 里 ⇒ 「grep 自证不存在第二处」永远过不了 | §2.2.1 / §4.5 / §6.3 |
| **P1-5** | **令牌桶是会话内存态、启动即满桶 ⇒ 「每日换手预算」实为「每会话预算」，停会话重开即清零。** 这不是缺陷（重启满桶是 R-1 的必要缓解），但它使弹窗那句「每日最多 N 次完整往返」在字面上不成立。本轮的全部论点都建立在「不许说没核实过的话」上，这一句必须自己先做到 | §2.2.4 / §2.2.6 / §2.4.1 / §6.4 |
| **P1-6** | **`BackstopRuntime` 的签名在文档内部自相矛盾。** §2.3.1 写 `schedule_check(now_wall, *, position, push)`，§4.5 写 `schedule_check(now_wall, *, side, position_sz, local_sl_px, local_tp_px)`；`apply_backstop_gate` 在 §2.3.4 无 `min_coverage`、§4.5 有。下游按哪份实现都会与另一份对不上 | §2.3.1 / §2.3.4 / §4.5 |
| **P1-7** | **轴 B 的取数要把 `_normalize_algo_order` 再抄一遍。** v1 说「复用同一条路径」，但紧接着定义了**第三种形状** `{"algoId","side","sz","slTriggerPx","tpTriggerPx","reduceOnly"}`——既有归一产物是 `{"kind": "tp"/"sl", "triggerPx", ...}`。两处各归一一份正是本仓库反复出事的形态 | §2.3.1 / §2.3.2 |
| **P1-8** | **`refill` 的经过时间可能为负。** 回测 `note_open` 传的是**开仓根 ts**（`backtest.py:597-599` 的 `_bar_ts(pos.entry_bar)`），它可能**早于**上一次 refill 的时刻（epoch 迟采纳时）⇒ `now - last_refill < 0` ⇒ 令牌不增反减，且不报错 | §2.2.2 |
| **P1-9** | **装配路径与模块依赖方向没定。** 回测侧 `HorizonRuntime(spec.horizon_rule, record_exits=False)`（`backtest.py:249`）**不传 settings**，而 v1 §4.5 的 `rule_from_horizon(rule, settings)` 需要它；同时 `hold_horizon` 要 import `turnover_discipline`（派生式），若 `turnover_discipline` 反向 import `hold_horizon` 取 `HorizonRule` 就成环（`hold_horizon._acceptance_preview:417` 已经在用函数内延迟 import 绕同一类环） | §2.2.2 / §3.2 / §4.5 |
| **P1-10** | **回测 `gate_reason` 的元数记错。** v1 §2.2.5 写「五元扩为七元」，实际现状是**六元**（`base / horizon / cooldown / brake / consensus / edge`，`backtest.py:776-777`；代码自己的注释也写错成「五元」）。机械判据的数字算错，判据本身就失效——本仓库刚在上一轮吃过这个亏 | §2.2.5 |

### P2（8 条，建议采纳；未逐条改写正文，除注明外）

| # | 建议 |
|---|---|
| P2-1 | `trend_hold` 下「两次快速止损 ⇒ 约 20 小时开不出单」是 D-2 的**已知代价**，但 v1 只写在拍板记录里。已在 §2.4.1 的契约摘要与 §6.4 runbook 各加一句人话（**已改**） |
| P2-2 | 轮内轨迹的 `horizonWire.turnover` 是**门禁前**快照（`apply_horizon_trace` 在 `_apply_horizon_gate` 内调用，早于换手闸），两个 veto 计数会慢一轮。最省的修法是让 `_apply_turnover_gate` **原地 mutate** 同一个快照 dict（trace 存的是同一个引用）（**已在 §2.4.3 注明**） |
| P2-3 | 放行条件 13 的正则「扫描 `3600`」会误伤既有时间换算 helper，改为只扫本轮新增的三个契约值来源（**已改**） |
| P2-4 | §2.4.1 那句「每 2 分钟核实一次」是把 `ai_trade_backstop_poll_ttl_s` 硬抄进文案；按本文档自己的前端零计算纪律，该数应由 `horizon-profiles` 的运维标志一并下发（**已改**） |
| P2-5 | 反手被降级为 `close`（P0-1 的修法）会让该笔退出在 `exit_attribution` 里落 `agent` 而非 `agentReverse`，验收卡的退出来源分布随之变化。属可接受偏移，但需在 §2.4.4 的免责里点名 |
| P2-6 | `exchange_backstop.py` 一个文件装下 IO + 纯判定 + 运行时 + 门禁，与仓库既有的 `cost_model` / `cost_runtime` / `cost_text` 三分法不同构。行数若超 400 行请按同款三分法拆开；文案层本就已经在 `hold_horizon_text`，不必新建 |
| P2-7 | `HorizonState.blocked_streak` / `vetoes` 与 §2.9(e) 的自察注记**属于第 1 轮的冻结口径**，本轮两个新闸的拦截**不得**并入它们（否则 G-2 的 AST 冻结门禁虽过、语义却已漂移）。新计数一律走 `TurnoverState` 自己的三个字段 |
| P2-8 | L0 runbook 把「`budgetVetoes` 非零」当作「闸真的在跑」的判据不成立——一个健康的 `trend_hold` 会话本就可能一次都不撞闸。改用 `opensCounted` 与 `tokens` 快照做在跑判据（**已改**） |

---

## 一、概述

### 1.1 要解决的问题

调查原文的结论是一句话：**「交易本身略有毛利润，但被频繁开平的成本吞掉」**（16 次往返、15 次由
Agent 主动平仓、毛利 +0.5845 USDT 被 3.0586 USDT 双边手续费吃成 −2.4741 USDT）。第 1 轮建了
持仓周期铁律层（G1 最短主动平仓窗口 / G2 离场连续确认 / G3 换手迟滞），第 2 轮把验收指标做实
（资金费真值化 / 退出来源归因 / 五行验收卡）。对着代码逐条核对，本轮要解决的是**三个仍然张着口
的缺口**，每一条都能在仓库里指到行：

**缺口 #1 —— 换手纪律只做了一半：三道闸全部只约束「平」，没有一条约束「开」。**

`hold_horizon.gated_kind`（`hold_horizon.py:429-444`）的冻结豁免矩阵写得很清楚：受约束的只有
`close` 与反手，`hold` / `cancel_order` / **空仓开仓永不被拦**。这在第 1 轮是正确的取舍（那一轮
要解决的是「持得太短」），但它留下了一条完整的绕行路径：

> 开仓 → 持够 G1 的最短窗口 → 主动平仓（合法放行）→ **下一轮立刻同向重开** → 再持一个最短窗口 → 再平。

这条路径上每一步都合法，而它产生的换手频率恰好是 `86400 / min_hold_s`：`intraday_swing` 的
`min_hold_s` 是 7200s（`derive_min_hold_s` = 目标下界的一半，`hold_horizon.py:225-231`），故理论
上限是 **12 次/日往返**，而同一份契约在验收卡上给自己定的门槛是
`min(86400/14400, 24) = 6 次/日`（`horizon_acceptance.py:231-237`）。**一个会话可以在完全不违反任何
一道闸的前提下，稳定地跑出两倍于自己验收门槛的换手率**——这不是理论推演，它正是调查样本里
「11/14 笔持仓不足 1 小时」的那种形态在装上 G1 之后的合法变体。

仓库里现有的两条开仓侧闸都不覆盖它：`profit_cooldown` 的开启点唯一，只在**保护层自己平仓**时
（`profit_cooldown.py:11-13` 的铁律 2：「Agent 主动 `close`、会话级止盈止损、外部平仓一律不开启」）；
`drawdown_brake` 的第三档冷却要**连亏达阈**才触发（`drawdown_brake.py:153-167`）。一串
「小赚 → 立刻重开 → 小赚」的高频往返，两条闸一条都不会响。

**缺口 #2 —— 「交易所侧还有兜底」是一句从未被核实过的话。**

`hold_horizon_text._exchange_snapshot_line`（`hold_horizon_text.py:205-210`）每轮都对模型说：

> 交易所侧兜底：止盈 X / 止损 Y（**进程退出后仍生效**）。

而这两个数的真源是 `profit_guard_runtime.exchange_oco_px`（`profit_guard_runtime.py:74-89`）：
**首选最近一笔开仓成交回执里的 `takeProfitPrice` / `stopLossPrice`，取不到就退回本地公式现算
（`oco_source="derived"`）**。也就是说，这句承诺的证据链是「我下单时**打算**附上这两个价」，
而不是「交易所现在**确实**挂着这两张单」。两者之间至少有四条会分叉的路径：

| # | 分叉场景 | 当前系统的表现 |
|---|---|---|
| a | 随单 `attachAlgoOrds` 被交易所拒绝 / 触发价不合法 | 本地回执照样记着两个价，prompt 与 UI 继续宣称兜底存在 |
| b | 算法委托被手动撤单 / 被交易所因保证金变动清理 | 同上，且**永远不会**被发现 |
| c | 加仓（同向再开一张）后某一腿的附单失败 | 覆盖张数 < 持仓张数，UI 无任何度量 |
| d | 会话重连后采纳一笔**没有本地成交记录**的持仓 | 走 `derived` 分支，展示出一个交易所侧根本不存在的价 |

第 1 轮终审的 RV-1 已经定过一条纪律：**「不得宣称并不存在的交易所兜底」**（当两个 pct 都是 0 时
必须如实说没挂）。本轮把同一条纪律推进一层：**pct 非 0 也不等于单还在**。而这条承诺恰恰是
`trend_hold`（目标持仓 1–3 天、唤醒周期 0）能够成立的**唯一前提**——第 1 轮的宪章原话是
「即使本进程退出它们依然生效，故你**不必每轮盯盘**」（`hold_horizon_text.py:132-133`）。让模型据
一条未经核实的承诺去放松盯盘，是本特性目前最大的一处安全债。

**缺口 #3 —— 用户在弹窗里读到的门槛，运行期没有任何一层在执行。**

第 2 轮把五行验收门槛提前显示在了启动弹窗（`AiTradeHorizonFields.tsx:295-335` 的
`AcceptancePreview`），这是对的；但其中「每日换手次数 ≤ N」这一行在运行期**只被观测、从不被执行**。
用户读到的是一个承诺，系统给的是一份事后报告。缺口 #1 又恰好让这一行成为最容易被突破的一行。

### 1.2 要建的东西

三条轴，全部是**加法**，全部**默认装配但可一键回到不装配**，全部**只会让系统更保守**：

**轴 A · 开仓侧换手纪律（G4 再入场冷却 + G5 换手预算）。** 新纯核心
`ai_auto/turnover_discipline.py`，与 `base_rules` / `profit_cooldown` / `hold_horizon` 逐字同构：
纯函数 + 一个降级码 + 把**开仓**降级为 `hold`，绝不新增任何下单路径。

- **G4 再入场冷却**：一次平仓成交后，同向再开仓需等 `reentry_cooldown_s = frac × min_hold_s`
  （`frac` 默认 0.5，即目标持仓下界的四分之一）。
- **G5 换手预算**：一个**令牌桶**，速率 = 验收卡第 3 行的同一个阈值
  `min(86400 / target_hold_min_s, 24)`，容量 = `max(2, ceil(速率 × 0.5))`。每建立一个新持仓
  epoch 消耗一枚令牌；令牌不足即拦开仓，并如实告知下一枚令牌何时到账。

两道闸都**永不拦 `close` / `hold` / `cancel_order`**，都在契约未设目标区间时（`custom` 全默认）
结构性空转，都与实盘 / 回测共用同一份纯核心。

**轴 B · 交易所侧兜底真值化（`backstop`）+ G6 覆盖闸。** 新模块
`ai_auto/exchange_backstop.py`：一条低频、单飞、**只读**的私有 GET（复用既有
`OKXTradeClient.get_algo_pending_orders`，`trade_client.py:239-247`），把「交易所侧现在到底挂着
什么」变成一份带三态 `basis`（`exchange` / `local` / `unavailable`）的快照，并据此：

1. 改写 prompt 的兜底行——**核实到才敢说「进程退出后仍生效」**，核实不到就如实说「未核实」，
   核实为空就说「交易所侧现在没有任何持久化兜底，本进程是唯一的退出执行者」；
2. 度量**覆盖率**（已被算法委托覆盖的张数 ÷ 持仓张数），并在覆盖不足时由 G6 拦下**继续加仓**
   （只拦加仓，永不拦平仓、永不拦新开仓——新开仓自带随单 OCO，拦它只会制造裸露）；
3. 在运行期 UI 上给出一个用户能一眼看懂的三态徽标。

**本轮明确不做自动补挂 / 重挂**（理由见 §十）。

**轴 C · 呈现（后端算了 ≠ 用户看得见）。** 弹窗契约摘要新增两行、运行期新增一张「换手纪律与
兜底」卡、轨迹与降级码新增三条标签、回测面板新增两个拦截计数与一句口径免责、验收卡第 3 行
新增一句**自指披露**（见 §2.2.6：本轮把该行的门槛做成了运行期的闸，它因此接近恒真，必须自认）。

### 1.3 为什么这样切

- **对称性**：第 1 轮建的是「不许过早平」，本轮补的是「不许过快重开」。同一个成本论据、同一份
  契约派生、同一套 fail-open 姿态、同一个纯核心 + 运行时 + 文案的三段式。不补这一半，第 1 轮的
  三道闸在换手成本这个目标上只能拿到一半的分。
- **不引入任何新魔数**：G4 的时长从 `min_hold_s` 派生，G5 的速率**直接复用**验收卡的
  `max_turnover_per_day`。用户在弹窗里看到的那个数，就是运行期真正在执行的那个数——本仓库
  已经为「前后端 / 两层各存一份口径」出过事故（`edgeSource` / `costSnapshot` 两次），本轮从
  一开始就把它做成一个函数。
- **轴 B 是轴 A 的安全前提**：换手预算会**延长**平均持仓时间（这正是目标），而持仓时间越长、
  越无人值守，交易所侧兜底的真实性就越是唯一重要的事。先做 A 不做 B，等于在一个未经核实的
  安全网上加大跳跃高度。
- **规模自我克制**：三条轴各自都能独立回退（三个总闸），不动任何既有门禁的判定式，不动
  `live_auto` 与 `engine/simulator`，不新增 DB 列，不新增下单路径。

### 1.4 前置事实核对（本轮依赖的既有构件，逐条已按行核实）

| 依赖 | 位置 | 核实结论 |
|---|---|---|
| 豁免矩阵 `gated_kind` | `hold_horizon.py:429-444` | 只覆盖 `close` / 反手；开仓侧确无任何约束 |
| `derive_min_hold_s` | `hold_horizon.py:225-231` | `0.5 × target_hold_min_s`，G4 的派生源 |
| 验收阈 `resolve_thresholds` | `horizon_acceptance.py:219-239` | `max_turnover_per_day = min(86400/lo, 24)`；`lo<=0 ⇒ None` |
| 硬上限常量 | `acceptance_text.py:37` | `TURNOVER_HARD_CAP = 24.0`；`acceptance_text` **不 import 任何 ai_auto 模块**（新纯核心可安全引用，无环） |
| 成交事件收口 | `horizon_runtime.py:226-273` | `note_open` / `note_close` 已是实盘唯一的开 / 平仓成交挂点 |
| 回测 epoch 钩子 | `backtest.py:576-599` `_track_horizon_epoch` | 回测侧同样走 `note_open` / `note_close`（时钟为 bar ts） |
| 开仓侧闸的既有范式 | `profit_cooldown.py:58-81` | 纯函数 + `(decision, reason)` 二元返回 + 同一对象直通 |
| 决策链插入点（实盘） | `session.py:697-710` | `note_intent → base → horizon → profit_cooldown → sizing → consensus → edge` |
| 决策链插入点（回测） | `backtest.py:755-777` | 同序；`gate_reason` 实为**六元** `or` 合成（代码注释自称「五元」是既有笔误，[P1-10]） |
| 算法委托查询 | `trade_client.py:239-247` | `get_algo_pending_orders(ord_type, inst_id)` 已存在（签名 GET） |
| 算法委托归一 | `okx/trading.py:597-614 / 689-714` | `fetch_live_open_orders` 已在并发查 `oco` + `conditional` 并归一，含 `sz` / 触发价 |
| 兜底价现状真源 | `profit_guard_runtime.py:74-89` | 首选**本地成交回执**，取不到则本地现算 —— 从不查交易所 |
| 私有取数的凭证缝 | `cost_runtime.py:44-56`、`session.py:175-177` | `executor.creds`；`creds is None ⇒ 一个请求都不发` |
| 后台单飞维护任务 | `cost_runtime.py:105-117`、`session.py:476-478` | 网络 IO 一律离开 5s 盯市 tick 的关键路径 |
| 请求入参 | `schemas.py:555-581` `AiTradeHorizonIn` | 校验只在本层做（422），服务端只回填不复夹 |
| 前端降级码标签 | `apps/web/lib/aiTrade.ts:495-539` | `DEGRADE_REASON_LABELS` 前缀匹配表，新码必须在此登记 |
| **运行期快照的真实键名** | `session.py:1557`（status）/ `:1485`（decision）/ `trace.py:266` | 全是 **`horizon`**；`tradingHorizon` 只在启动请求 / `config_json` / 分组契约 / LLM payload 出现（[P1-1]，v1 §4.3 认错） |
| **成员级前端载体** | `aiTradeTypes.ts:816 / 853`、`store.ts:316` | `member.session.horizon` 与 `trace.horizonWire`，均**整对象透传**（不吞键）；分组的 `tradingHorizon` 是契约不是快照（[P1-2]） |
| **平仓成交不携带持仓方向** | `executor.py:819-821`、`session.py:297`、`backtest.py:588/596` | close 成交 `side == "close"`、`positionAfter == 0`，回测干脆传 `{}`（[P0-4]） |
| **算法委托归一缺 `closeFraction`** | `okx/trading.py:689-714` | 只透出 `sz` / `reduceOnly` / `posSide` / `side` / `triggerPx`；随单 TP/SL 常见 `sz=0`（[P0-2]） |
| **prompt 空仓分支早退** | `hold_horizon_text.py:145-146` | flat 时**直接 return**「本层不约束开仓」——而 flat 正是 G4/G5 唯一生效的状态（[P0-3]） |
| 冷却到期补推 status 的既有先例 | `session.py:1333-1336` | `_expire_profit_cooldown`：「不推则前端 chip 会一直挂着」（[P1-3] 按同款实现） |

> **⚠️ 实现节点必读**：第 2 轮（`ai-trade-horizon-acceptance-loop`）的整批变更
> **目前在工作树中尚未提交**——31 个修改文件（含 `session.py` / `backtest.py` / `hold_horizon.py` /
> `store.ts` / `aiTradeTypes.ts`）+ 一批新增文件（后端 `horizon_acceptance.py`、`acceptance_text.py`、
> `funding_ledger.py`、`exit_attribution.py` 与 5 个 `test_ai_trade_*` 用例；前端
> `HorizonAcceptanceCard.tsx`、`ExitSourceCard.tsx`、`aiTradeAcceptance.ts` 等）。本轮全部建立在
> 它们之上。开工第一步必须先确认这些文件在场，且下列用例为绿：
> `test_ai_trade_horizon_acceptance.py` / `test_ai_trade_acceptance_wiring.py` /
> `test_ai_trade_acceptance_backtest.py` / `test_ai_trade_funding_ledger.py` /
> `test_ai_trade_exit_attribution.py` 与前端 `aiTradeAcceptance.test.ts`。
> 若不在场，**立即停止并上报**，不得凭空重建。
>
> 同一工作树里还混着**不属于本特性**的在制变更（`db/live_auto_repositories.py`、
> `test_factor_library_expansion.py` 孤儿用例、`desktop/scripts/*` 打包脚本）——Subtask#3 精确暂存
> 时必须把它们剔除（见 §七 R-8）。

---

## 二、技术设计

### 2.1 架构位置与数据流

```
                       ┌──────────────────── 决策轮（session.py / backtest.py 同序）────────────────────┐
 signals/board ──► decider.decide ──► note_intent ──► base_rules ──► horizon(G1/G2/G3) ──►
     profit_cooldown ──► ★turnover(G4/G5) ──► ★backstop(G6) ──► sizing/brake ──► consensus ──► edge ──► reflect ──► execute
                       └───────────────────────────────────────────────────────────────────────────────┘
                                                    ▲                    ▲
   成交事件（_dispatch 唯一收口）                     │                    │
   ├─ 开仓成交 ─► HorizonRuntime.note_open ─────► TurnoverState.on_open  （消耗 1 枚令牌）
   └─ 平仓成交 ─► HorizonRuntime.note_close ────► TurnoverState.on_close （开启 G4 冷却）
                                                                          │
   盯市 tick（5s，只读、无判定）                                            │
   ├─ HorizonRuntime.refresh（既有）                                       │
   └─ ★BackstopRuntime.schedule_check ──(单飞后台任务, TTL 120s, 仅持仓时)──► OKX orders-algo-pending
                                                                          └─► BackstopSnapshot（三态 basis + 覆盖率）
```

三条纪律与既有各层完全一致：

1. **纯核心无 IO / 无 LLM / 无 broker**：`turnover_discipline.py` 全纯函数；
   `exchange_backstop.py` 把唯一那条 IO 收在一个 `fetch_*` 函数里，判定部分是纯函数。
2. **实盘与回测共用同一个纯核心**，差别只在「谁提供时钟与断面」（墙钟 ↔ bar ts）。
   **轴 B 在回测侧整层不装配**（回测没有交易所，声称「已向交易所核实」是假话）。
3. **fail-open**：任何一层算不出来（阈为 `None`、取数失败、契约未装配）一律**放行**并如实说明。
   本轮两道新闸拦的是「开仓 / 加仓」——把它们做成 fail-closed 会在断网瞬间把系统变成一台
   永不交易的机器，这与 `hold-inertia` 那一轮的教训同源。

### 2.2 轴 A：开仓侧换手纪律

#### 2.2.1 派生规则（唯一真源，不得手抄第二遍）

三条式子全部落在 `turnover_discipline.py` 的模块级函数里，`hold_horizon.resolve_horizon_rule` 与
`horizon_acceptance.resolve_thresholds` **都调用它们**，前端一个数都不算：

```python
def derive_turnover_per_day(target_hold_min_s: float | None) -> tuple[float | None, float | None]:
    """（[P1-4]）**一次调用同时给出未夹与已夹两个值**：`(capped, raw)`。

    `raw = 86400 / 目标下界`（下界未设 ⇒ `(None, None)`）；`capped = min(raw, TURNOVER_HARD_CAP)`。

    为什么必须返回两个：`horizon_acceptance.AcceptanceThresholds` 有 **两个** 字段——
    `max_turnover_per_day`（已夹，判定用）与 `derived_turnover_per_day`（未夹，只给
    `acceptance_text.turnover_note:114-118` 那句「本模式派生阈高达 240 次/日、实质恒真」的自认用）。
    v1 只搬走已夹的那一个，`86400 / lo` 就必然仍留在 `horizon_acceptance` 里，放行条件 3
    的「grep 自证不存在第二处」永远过不了。

    这是**验收卡第 3 行的同一个阈值**：`horizon_acceptance.resolve_thresholds` 本轮改为调用
    本函数（原地内联式删除），使「弹窗里承诺的门槛」「运行期执行的预算」「事后验收的判据」
    三处恒为同一个数。任何一处自己再乘一遍，都会在用户眼里变成系统自相矛盾。
    """


def derive_max_turnover_per_day(target_hold_min_s: float | None) -> float | None:
    """`derive_turnover_per_day(...)[0]` 的便捷别名（G5 速率的唯一读点）。"""


def derive_reentry_cooldown_s(min_hold_s: float | None, *, frac: float) -> float:
    """再入场冷却 = frac × 最短主动平仓窗口（frac 默认 0.5 ⇒ 目标下界的 1/4）。

    取 `min_hold_s` 而非目标下界：G4 与 G1 是同一件事的两半（一次往返的「不许太早平」与
    「不许太快重开」），用同一个基准才能让两者的量纲天然一致；`min_hold_s <= 0`（custom
    全默认）⇒ 0 ⇒ 本闸结构性关闭，与「不装配」逐字等价。
    """


def derive_turnover_capacity(per_day: float | None, *, burst_frac: float,
                             min_capacity: float) -> float:
    """令牌桶容量 = max(min_capacity, ceil(per_day × burst_frac))；per_day 为 None ⇒ 0（关闭）。

    `min_capacity` 默认 **2** 是一个刻意的安全阀，不是凑整：`trend_hold` 的速率是 1 次/日，
    若容量也取 1，则「开仓 → 2 小时后止损出场」之后，会话要**整整 22 小时**开不出下一单。
    那既不符合用户对「趋势持仓」的预期，也正是本仓库 `hold-inertia` 那一轮踩过的「永不开单」
    形态。容量只影响**突发**，不影响长期速率——留 2 枚突发额度，长期换手率仍恒 ≤ per_day。
    """
```

四个预设按上式的落地值（**实现后必须与本表逐格相等，`test_turnover_discipline.py` 硬断言**）：

| 模式 | 目标下界 | `min_hold_s` | **G4 冷却** | **G5 速率**（次/日） | **G5 容量** |
|---|---|---|---|---|---|
| `hourly_swing` | 360s | 180s | 90s | `min(240,24)` = 24 | `max(2, ceil(12))` = 12 |
| `intraday_swing` | 14400s | 7200s | 3600s（1h） | 6 | `max(2, ceil(3))` = 3 |
| `short_swing` | 43200s | 21600s | 10800s（3h） | 2 | `max(2, ceil(1))` = 2 |
| `trend_hold` | 86400s | 43200s | 21600s（6h） | 1 | `max(2, ceil(0.5))` = 2 |
| `custom`（全默认） | 0 | 0 | **0（关）** | **None（关）** | **0（关）** |

> `hourly_swing` 的速率被硬上限夹到 24：这与验收卡第 3 行的既有自认完全一致
> （`acceptance_text.py:114-118`「实质恒真，故由固定上限兜底，不构成周期契约意义上的门槛」）。
> 在该模式下 G5 是一道**极弱**的闸（一天 24 次往返才拦），这不是缺陷而是诚实——它对应的
> 目标持仓本来就是 6 分钟起。该模式的换手纪律主要由 G4（90s 冷却）承担。

#### 2.2.2 纯核心 `ai_auto/turnover_discipline.py`（新建）

```python
"""开仓侧换手纪律纯核心（ai-trade-turnover-budget-and-backstop §2.2）。

与 `hold_horizon`（只约束平仓）严格互补：本模块**只约束建立新敞口的那一腿**，
`close` / `hold` / `cancel_order` 永不被拦。三条不可动摇的不变式：

1. **绝不阻止任何一次退出**——把平仓能力交给一个换手预算去管，等于让一个统计口径获得持仓
   生杀权，那是另一个特性、需要单独的安全评审（第 2 轮 §十 已定过同款纪律）。
   ⚠ [P0-1] 这条不变式**不能**靠「动作名不是 `close`」来保证：在本系统里「持空头时
   `open_long`」就是**反手**，它含一条平仓腿；把它降级为 `hold` 会把持仓留在原地。故本模块
   的判定式**必须看当前持仓方向**（见 `apply_turnover_gate` 的三分支）。
2. **fail-open**——阈为 None / 契约未装配 / 时钟异常一律放行。本层拦的是「再开一单」，
   而 fail-closed 的代价是一台永不交易的机器。
3. **本模块不 import `hold_horizon`**（[P1-9]）：依赖方向恒为 `hold_horizon → turnover_discipline`
   （`resolve_horizon_rule` 要调这里的派生式）。本模块只 import `acceptance_text`（拿
   `TURNOVER_HARD_CAP`，该模块零 ai_auto 依赖）与 `decider.TradeDecision`（与 `profit_cooldown`
   同款）。`rule_from_horizon` 的入参按**鸭子类型**读 `target_hold_min_s` / `min_hold_s`，
   不做 isinstance 检查、不 import 类型。
"""

# 建立**新敞口**的两个动作。注意它们只是候选集：是否真的受约束，取决于当前持仓方向（见下）。
_OPEN_ACTIONS = ("open_long", "open_short")

REASON_REENTRY = "horizon_reentry_cooldown"
REASON_BUDGET = "horizon_turnover_budget"


@dataclass(frozen=True)
class TurnoverRule:
    """会话级不可变契约（由 `HorizonRule` 的三个新字段直接投影，不二次解析）。"""
    reentry_cooldown_s: float = 0.0
    reentry_scope: str = "same_side"          # same_side | both
    per_day: float | None = None              # None ⇒ G5 关闭
    capacity: float = 0.0


@dataclass(frozen=True)
class TurnoverState:
    """跨 epoch 存活的运行态（**绝不可挂进 `HorizonState`**——后者在每次平仓/空仓被整体重置，
    令牌桶挂进去会在每笔平仓时被清空，G5 变成一个永远满桶的摆设）。"""
    tokens: float = 0.0                       # 当前令牌（装配时初始化为 capacity）
    last_refill_wall: float = 0.0
    cooldown_until_wall: float = 0.0
    cooldown_side: str = ""                   # 被平掉的方向（same_side 作用域用）
    reentry_vetoes: int = 0
    budget_vetoes: int = 0
    opens_counted: int = 0


def init_state(rule: TurnoverRule, *, now_wall: float) -> TurnoverState: ...
def refill(state, rule, *, now_wall) -> TurnoverState: ...          # 纯：按经过时间补令牌并夹到容量
def on_open_fill(state, rule, *, now_wall) -> TurnoverState: ...    # 消耗 1 枚（先 refill 再扣，夹到 ≥0）
def on_close_fill(state, rule, *, closed_side, now_wall) -> TurnoverState: ...  # 开启 G4 冷却
def build_wire(state, rule, *, now_wall) -> dict | None: ...        # §5.4；未装配 ⇒ None ⇒ 整键缺席
def apply_turnover_gate(decision, wire, rule, *,
                        position_side: str) -> tuple[object, str | None]: ...
```

判定与推进的**九**条硬约束（**评审与实现都按这九条对**）：

1. **`refill` 只在读取点调用，不在后台跑定时器**：
   `elapsed = max(0.0, now - last_refill)`（[P1-8] **必须夹到非负**：回测的 `note_open` 传的是
   **开仓根 ts**（`backtest.py:597-599`），epoch 迟采纳时它会早于上一次 refill 的时刻，不夹就会
   出现「令牌不增反减且不报错」）；`tokens += elapsed × per_day / 86400`，再 `min(tokens, capacity)`。
   纯函数、幂等、无需清理任务（沿用 `profit_cooldown._is_active` 的「纯时间驱动」姿态）。
2. **消耗发生在开仓成交、且只对新 epoch 计一次**：挂在 `HorizonRuntime.note_open` 里
   （它已经保证了「同 `(side, seq)` 不重复建 epoch」，`horizon_runtime.py:240-244`）。
   **加仓（同 epoch 的后续成交）不再消耗**——预算的单位是「往返」，与验收卡第 3 行的分子
   `roundTrips` 严格同单位；换了单位，弹窗承诺的那个数就对不上运行期的行为了。
3. **扣减后允许为 0，不允许为负**：`tokens = max(0.0, tokens - 1.0)`。允许为负会让一次
   「令牌恰好 1.0 的开仓」把桶打到 −1 并额外多冻结一个补充周期，用户无法从任何一个数上推出
   这段多出来的等待是怎么来的。
4. **G4 与 G5 的判定顺序：先 G4 后 G5**。冷却是「刚刚发生了一件事」，预算是「一段时间的总量」；
   前者对用户更具体、更可解释，故先出它的降级码（沿用「先执行者胜出」下**更具体的原因优先**
   这一既有取舍）。
5. **`reentry_scope` 默认 `same_side`**：只拦被平掉的那一侧。反向开仓是「结构翻转」的合法表达，
   把它一并拦掉会把一个换手纪律变成一个方向禁令（`profit_cooldown._blocks` 的同款取舍，
   `profit_cooldown.py:51-55`）。
6. **降级码带参数后缀，且参数必须够用户自查**：
   `horizon_reentry_cooldown(1800s,long)` / `horizon_turnover_budget(0.4/3,next=42m)`。
   第二个参数是「下一枚令牌还有多久到账」——没有它，用户与模型都只能看到一句「被拦了」
   而无法判断该等还是该改配置。**反手被降级时后缀追加 `,reverse→close`**（见约束 7）。
7. **[P0-1] 判定式按当前持仓方向三分支**——`apply_turnover_gate` 的 `position_side`
   实盘取 `executor.projected`（投影立场，与 `_apply_horizon_gate` 同源）、回测取 `main.pos.side`：

   | 断面 | 语义 | 本层动作 |
   |---|---|---|
   | `position_side == "flat"` 且 `action ∈ _OPEN_ACTIONS` | **新建敞口**（唯一会新建一个 epoch 的门） | 命中 G4/G5 ⇒ 降级为 `hold` |
   | `position_side` 与 `action` **反向**（持空 `open_long` / 持多 `open_short`） | **反手**：含一条平仓腿 | 命中 G4/G5 ⇒ 降级为 **`close`**（保留退出腿、只否掉再入场腿），**绝不降级为 `hold`** |
   | `position_side` 与 `action` **同向**（加仓） | 加仓不建新 epoch、不消耗令牌 | **恒直通**（同一对象返回）——它归 G6 管（§2.3.4），一个不消耗预算的动作不该被预算拦 |

   反手为什么是 `close` 而不是放行：若整类放行，「开多 → 持够 G1 → 反手做空 → 持够 G1 → 反手
   做多」这条链的换手率仍是 `86400 / min_hold_s`，缺口 #1 只被堵掉一半。降级为 `close` 同时满足
   两件事：**退出永远能执行**（不变式 1），**再入场受预算约束**（本轮目标）。且 `flat` 是三种
   持仓态里风险最低的一种，故本降级严格单向更保守。
   ⚠ 该 `close` 会被 `exit_attribution` 记为 `agent` 而非 `agentReverse`（[P2-5]），须在
   §2.4.4 的回测免责与验收卡 note 里点名。
8. **[P0-4] `closed_side` 的真源是 epoch，不是成交字典。** 实盘 close 成交的 `trade["side"]`
   恒为字符串 `"close"`（`executor.py:819-821`），回测干脆传空字典（`backtest.py:588/596`）。
   故 `HorizonRuntime.note_close` 必须**在调用 `_consume_epoch_hold()` 之前**先取
   `self.state.epoch[0]` 作为 `closed_side`（该方法会把 `state` 整体重置）；`epoch is None`
   （从未建立 / 未装配）⇒ **不开启冷却**（宁可不拦，也不拿一个猜的方向去拦）。
   `test_turnover_discipline` 与 S-2 各有一条用例专门守「传 `{"side": "close"}` 与 `{}` 两种
   真实入参时，`cooldownSide` 仍为 `long`/`short`」。
9. **`position_side` 的值域只有 `long` / `short` / `flat`**；出现其它值（未知/降级读数）
   ⇒ **恒放行**（fail-open），并在 wire 里如实记 `gateSkipped: "unknown_side"`。

#### 2.2.3 G4 再入场冷却

- **开启点唯一**：`HorizonRuntime.note_close`（`horizon_runtime.py:246-255`）——它是实盘与回测
  **共用**的平仓成交挂点，且已经在做「结算 epoch 持仓时长 + 退出来源归因」。在这里再挂一件事
  不引入任何新的调用顺序假设，**但引入一条新的顺序硬约束**：`closed_side` 必须在
  `_consume_epoch_hold()` 之前从 `self.state.epoch[0]` 取（约束 8）。这是本轮唯一一处
  「纯核心之外的调用顺序依赖」，故它同时出现在 docstring、§6.1 的 S-2、与放行条件里。
- **与 `profit_cooldown` 并存不冲突**：后者只由保护层平仓开启、默认 180s、作用域可配；本层由
  **任何**平仓成交开启、时长由周期契约派生。两者同时激活时，先判到的（`profit_cooldown` 在链上
  更靠前）出降级码，另一层不重复计数（`vetoes` 只在自己真的拦下时才 +1）。
- **豁免**：无。G4 是纯时间闸，钥匙就是时间本身，且它**从不阻止离场**——没有任何一种风险场景
  需要「立刻再开一单」来收敛。

#### 2.2.4 G5 换手预算（令牌桶）

- **初值满桶**：会话启动即 `tokens = capacity`。冷启动惩罚会让一个刚启动的会话在最需要建立第一个
  持仓的时刻被自己拦住，那是纯粹的负价值。
- **[P1-5] 作用域是「本会话」而不是「本自然日」，必须逐处说出来。** 令牌桶是会话内存态
  （§5.2），且初值满桶 ⇒ **停会话重开即回满**。这是 R-1 的必要缓解（重启后不该背着上一个会话的
  账），但它使「每日最多 N 次往返」在字面上不成立。三处文案一律改用**同一个措辞**：
  「**本会话**每日最多 N 次完整往返（会话重启后预算重新计算）」——弹窗契约摘要（§2.4.1）、
  运行期卡片（§2.4.2）、验收卡自认（§2.2.6）。wire 里同时下发 `budgetScope: "session"`，
  给未来真做成跨会话持久化时留一个可演进的字段（本轮**不做**跨会话持久化：那要落库、要定义
  「同一契约」的身份，属于另一轮）。
- **消耗点**：`note_open` 建立新 epoch 时。反手（`close` + `open` 两腿）会自然产生一个新 epoch，
  故**反手照样消耗一枚**——这正确，它确实是一次完整的换手。注意「消耗」与「拦截」在反手上是
  不对称的：反手**成交后照常扣令牌**，而反手**被拦时降级为 `close`**（约束 7）——两者一致，
  因为扣的是「已经发生的换手」，拦的是「即将发生的再入场」。
- **拦截语义**：`tokens < 1.0` ⇒ 开仓降级为 `hold`，并在 wire 与 prompt 里给出
  `nextTokenSeconds = ceil((1 - tokens) × 86400 / per_day)`。
- **`per_day is None` 或 `capacity <= 0` ⇒ 本闸结构性空转**（`custom` 全默认走这条）。
- **与 `enforceMaxHold` / `max_hold_bars` 无关**：那是持仓上限，本闸是开仓频率，两者不相互豁免。

#### 2.2.5 决策链插入位置（实盘与回测**同位**）

实盘 `session.py`，插在 `_apply_profit_cooldown_gate` 之后、`self._sizing.gate` 之前：

```python
decision, degrade = self._apply_profit_cooldown_gate(decision, degrade)
# ★ 换手纪律门禁（turnover-budget §2.2.5）：冷却之后、仓位/刹车之前。两条理由：
#   ① profit_cooldown 是「刚被保护层平掉」这一具体事件，更具体者先出降级码；
#   ② sizing/brake 之前 ⇒ 被本闸拦下的轮次不再进入仓位计算，与既有各闸姿态一致。
decision, degrade = self._apply_turnover_gate(decision, degrade)
decision, degrade = self._apply_backstop_gate(decision, degrade)      # 轴 B（§2.3.4）
decision, degrade = self._sizing.gate(decision, degrade)
```

两个薄封装各自把 `position_side` 喂进纯核心（[P0-1]）：实盘恒取 `self.executor.projected`
（投影立场，与 `_apply_horizon_gate` / `note_intent` 同源——`SimBroker.submit` 只排队，用真实
持仓做判据会看不见同根新边沿，这是第 1 轮 P0-1 定下的口径）。

回测 `backtest.py:761` 之后同位插入，`gate_reason` 由**六元扩为八元**（[P1-10]：现状是六元
`base / horizon / cooldown / brake / consensus / edge`，`backtest.py:776-777`；该行上方代码注释
自称「五元」是既有笔误，本轮**顺手改正注释但不改判定式**）：

```python
gate_reason = (base_reason or horizon_reason or cooldown_reason
               or turnover_reason or backstop_reason        # ★ 新增两元（回测侧 backstop 恒 None）
               or brake_reason or consensus_reason or edge_reason)
```

回测侧 `position_side` 取 `self.main.pos.side`（与 `note_intent` 同源，`backtest.py:757-758`）。

> 回测侧 `backstop_reason` **恒为 `None`**（轴 B 不在回测装配）。它仍然出现在合成式里，是为了让
> 两侧的表达式逐字同形——第 1 轮 G-3「两侧 reason 逐字相等」的冻结门禁按同一姿态验收。

#### 2.2.6 自指问题：本轮把验收卡第 3 行变成了一道闸（必须自认）

一旦 G5 在运行期执行 `turnoverPerDay ≤ 阈`，验收卡第 3 行就从「独立观测」退化为「执行结果」，
**它会近乎恒真**。本仓库已经为「恒真的判据」栽过跟头（`horizon.turnoverPerDay` 与 `avgHold`
代数等价、`rankIc` 在 auto 方向下两段同号恒真）。处置办法**不是**把闸关掉，而是**说出来**：

1. `AcceptanceInput` 新增两个只增字段 `turnover_gate_armed: bool` / `turnover_budget_vetoes: int`；
2. `acceptance_text.turnover_note` 在 `armed` 时**追加**一句（[P1-5] 口径必须自认到「会话」这一层，
   否则这句自认本身又是一句没核实的话）：
   > 本会话开启了换手预算闸（**本会话**上限 N 次/日、会话重启后预算重新计算；实际拦下 K 次），
   > 故本行接近恒真，**请以第 1、4、5 行为准**；K 越大说明模型仍在试图高频换手，本身就是一条
   > 需要看的读数。
3. 未武装时该 note **逐字节不变**（冻结用例守）。

这条自认同时给出了一个真正有信息量的新读数：**`budgetVetoes` 是「模型想换手而被制度拦住」的
次数**，它比换手率本身更能说明周期模式是否与该标的、该行情匹配。

#### 2.2.7 Prompt 段（`turnover_text` 并入 `hold_horizon_text.py`）

**[P0-3] 第一件必须改的事：空仓分支的早退。** `format_horizon_for_prompt`
（`hold_horizon_text.py:145-146`）今天在 `status == flat` 时**直接 return** 一句：

> 当前空仓：本层**不约束开仓**；开仓后将按上述目标周期约束你的主动平仓。

而空仓恰恰是 G4/G5 **唯一真正会拦人**的状态。不改这条早退，落地后的形态是：每轮告诉模型
「开仓不受限」→ 模型开仓 → 被降级为 `hold` → 模型把「被拦」当成新证据自我怀疑——正是本节
下面要引用的那条 `hold-inertia` 教训，而且是系统主动说反话。**改法**：flat 分支改为

```python
if wire.get("status") == STATUS_FLAT:
    lines = [f"{head}\n  当前空仓：本层**不约束你的平仓能力**；开仓后将按上述目标周期约束主动平仓。",
             _turnover_line(wire)]          # ← 冷却 / 预算激活时才产出，未激活 ⇒ 空串 ⇒ 整行省略
    return "\n".join(part for part in lines if part)
```

即：**换手纪律行在 flat 与持仓两个分支里都要出**；两个闸都未激活时 `_turnover_line` 返回空串，
拼接后**与今天逐字节相同**（冻结用例守）。原句里「不约束开仓」四个字必须改掉——它现在是假的。

其余沿用 `format_profit_cooldown`（`profit_cooldown.py:98-116`）的既有教训：**只说「被拦」而不说
「还剩多久 / 什么不受限」，会让模型每轮重复提议、每轮被拦、每轮把被拦当成新证据**——既烧 LLM
又污染上下文。故每轮持仓周期段在**冷却或预算真的处于激活态时**追加一行（未激活 ⇒ 整行省略 ⇒
逐字节回退）：

> 【交易周期 · 换手纪律】刚平掉一笔多头持仓，接下来 **48 分钟**内做多开仓会被系统拦截并降级为
> 观望（做空不受影响）；**本会话**换手预算余 **0.4 / 3** 枚（上限 6 次/日，会话重启后重新计算），
> 下一枚约 **42 分钟**后到账。
> **平仓 `close`、观望 `hold`、撤单 `cancel_order` 不受任何影响；若你此刻持仓并提议反手，
> 平仓那一腿一定会执行，只有反向的新开仓那一腿会被推迟。**
> 这两条限制的目的与你的目标持仓周期一致：一次往返的手续费是确定的，而一次立刻的重开需要一个
> 比上一次更强的理由。若你认为现在确实存在这样的理由，请把它写进 `reason`——它会被记录下来，
> 用于事后核对预算闸是否设得过紧。

宪章（会话启动一次，`format_charter`）在 `_gate_lines` 后追加两行，**空转的闸整行省略**（与既有
`_gate_lines` 同姿态，`hold_horizon_text.py:99-113`）。

### 2.3 轴 B：交易所侧兜底真值化

#### 2.3.1 取数（唯一一条 IO）

新建 `ai_auto/exchange_backstop.py`：

```python
async def fetch_algo_backstop(creds, inst_id: str, *, timeout_s: float) -> list[dict] | None:
    """并发查 `oco` 与 `conditional` 两类未触发算法委托；失败 ⇒ None（绝不抛给调用方）。

    **不新建任何客户端、端点与归一函数**：`OKXTradeClient.get_algo_pending_orders`
    （`trade_client.py:239`）已存在，`okx/trading.py:593-615` 的 `fetch_live_open_orders` 已经在
    以完全相同的方式使用它（并发查两类 + `_normalize_algo_order` 归一）。

    [P1-7] **直接复用 `okx.trading._normalize_algo_order` 的产物形状，不再定义第三种形状**：
    它已经把一条 OCO 展开成 `kind="tp"` / `kind="sl"` 两条，每条带
    `{"instId","posSide","side","sz","state","reduceOnly","algoId","triggerPx","px","createdTs"}`。
    本函数只做三件事：① 过滤 `instId`；② 原样返回列表；③ 附带把每条的 `closeFraction` 透传出来
    （见 `classify_backstop` 的覆盖判据）——`_normalize_algo_order` 今天不带这个键，故本轮在
    `okx/trading.py` 里**只加这一个键**（加法、不改既有键、既有调用方零影响），而不是在 ai_auto
    侧再抄一份解析。

    与 `fetch_live_open_orders` 的两点差异（都必须实现）：
      · **不查 `orders-pending`**（普通挂单与兜底无关，省一条请求）；
      · **不上抛**：`fetch_live_open_orders` 会把 `OKXError` 翻成 `OKXPrivateError` 抛出，本函数
        必须 `except Exception` 全吞并返回 `None`（约束 4）。

    `creds is None`（模拟盘 / 无凭证）⇒ 一个请求都不发，直接返回 None（`no_creds`）。
    """
```

**调度**：签名以本节为准（[P1-6]，§4.5 与本节已对齐）：

```python
def schedule_check(self, now_wall: float, *, side: str, position_sz: float,
                   local_sl_px: float | None, local_tp_px: float | None,
                   push=None) -> None
```

由 `session._monitor_tick` 在既有 `self._cost.schedule_maintenance(...)` 之后调用
（`session.py:476`）。四条约束：

1. **单飞**：上一批在途即跳过本轮（`cost_runtime.py:116-117` 的既有姿态）；
2. **只在持仓时查**：`executor.projected` 为 `flat` ⇒ 一个请求都不发，并把快照置为
   `basis="local"` 的空壳（无仓时「有没有兜底」这个问题没有意义）；
3. **TTL 节流**：默认 120s；`note_open` 之后置 `force_next=True`，使新开仓在下一个盯市 tick
   （≤5s）内就被核实一次——**随单 OCO 是否真的挂上，是开仓后最值得立刻知道的一件事**；
4. **异常全吞**：任何异常 ⇒ `basis="unavailable"` + `unavailableReason="error"`，绝不打断盯市，
   绝不污染 `executor._reconcile_fail_streak`（那条计数只属于持仓读取，混用会触发假降级——
   `executor.py` 的 reconcile 失败分支已为同款问题写过注释）。
5. **生命周期**：单飞句柄的形状与 `CostRuntime._maintenance` 完全一致（`asyncio.create_task` +
   `done()` 判活）。`AiTradeSession.stop()` 今天**不**取消 `_cost._maintenance`（只取消
   `_monitor_task` / `_decision_task`，`session.py:386-392`），本轮沿用该先例**但补一条硬约束**：
   `BackstopRuntime` 必须持有一个 `self._closed` 标志，`stop()` 时置真，后台任务回来后
   **不再 `push` 任何事件、不再写 `last`**——一个已停会话向 WS 追发事件是本仓库还没犯过的错，
   不要从本轮开始犯。

#### 2.3.2 纯核心分类（三态 `basis` + 覆盖率）

```python
def classify_backstop(orders: list[dict] | None, *, side: str, position_sz: float,
                      local_sl_px, local_tp_px, checked_at_wall: float | None,
                      now_wall: float, max_age_s: float,
                      unavailable_reason: str | None) -> dict:
    """把一次核实结果分类成 §5.4 的 wire；纯函数、无 IO。

    三态 `basis`（沿用第 2 轮资金费账本 `settled+accrued / accrued / unavailable` 的同款分级）：
      · `exchange`   —— 在 `max_age_s` 内向交易所核实过，触发价为**交易所读数**；
      · `local`      —— 未核实（未开启 / 无仓 / 尚未跑过一次），只有本地回执口径，**不得**宣称持久性；
      · `unavailable`—— 尝试过但失败（`no_creds` / `error` / `stale`），同样不得宣称。

    **只认止损腿**：止盈腿再多也不构成兜底——兜底的定义是「最坏情况下有人替你退出」。
    """
```

#### 2.3.2a [P0-2] 覆盖率是**三态**，不是一个数：`covered` / `uncovered` / `unknown`

v1 把覆盖率写成一个必然算得出来的比值，判据是「方向正确 **且** `reduceOnly` **且** 带
`slTriggerPx` 的**张数**求和 ÷ 持仓张数」。回到源码逐条核对，这三条里有两条会在真实数据上落空：

| 事实 | 位置 | 后果 |
|---|---|---|
| 归一产物的 `sz` 直接来自 OKX 的 `sz` 字段；随单 TP/SL 转成的算法委托常见形态是 **`sz` 为 0 / 缺省，靠 `closeFraction=1` 表示「全仓」** | `okx/trading.py:695-705` | `coveredSz` 恒 0 |
| `reduceOnly` 走 `_as_bool`，缺省即 `False`；单向持仓（net）模式下随单 OCO 未必带该字段 | `okx/trading.py:618-620 / 701` | 过滤器把真实的止损腿全部剔掉 |
| 限价挂单期间 `projected` 被**刻意不写**（I-2a），`entry_qty_sz` 可能仍是 0 | `executor.py:279-283` | `position_sz == 0` ⇒ 除零 |

三条任一命中，v1 的结论都是 `coverageRatio = 0 ⇒ mismatch = True`：prompt 会对模型高喊
「此刻没有任何持久化兜底」、G6 会拦掉全部加仓。**本轮唯一的存在理由是消除一句没核实的话，
落地却造出一句核实错的话——那比原状更坏。** 故：

1. **覆盖判据分两级，先认「存不存在」再认「够不够」**：
   - `has_stop = 存在至少一条 kind=="sl"、方向正确的委托`。**方向正确**按仓库既有的
     `_classify_pending_kind`（`okx/trading.py:643-651`）同款判据：
     `reduceOnly` 为真，**或** (`posSide=="long"` 且 `side=="sell"`)，**或**
     (`posSide=="short"` 且 `side=="buy"`)，**或** (`posSide=="net"` 且 `side` 与持仓方向相反)。
     ——`reduceOnly` 只作为**充分条件之一**，绝不作为必要条件。
   - `coveredSz` 只在**能算**时才算：委托的 `sz > 0` ⇒ 累加 `sz`；`sz` 不可用但
     `closeFraction == "1"` ⇒ 视为**全仓覆盖**（`coverageRatio = 1.0`）；两者都不可用 ⇒
     **`coverageRatio = None`**。
2. **`position_sz <= 0`（无仓 / 挂单期 / 读数缺失）⇒ 不做除法**，`coverageRatio = None`、
   `mismatch = False`、G6 恒放行。
3. **`coverageRatio: null` 是一等公民**（这正是本文档 §5.4 自己定的零样本口径：「`0` 与 `null`
   在这张卡上是『确认裸露』与『还不知道』两个结论」）。三处消费方一律按三态分支：
   - `mismatch` **只在** `basis=="exchange"` **且** `has_stop is False` **且** 本地回执声称有止损价
     时为真——即「本地说挂了、交易所侧一条都查不到」。**`coverageRatio is None` 永不构成
     `mismatch`**。
   - G6 只在 `coverageRatio is not None and coverageRatio < min_coverage` 时拦（§2.3.4）。
   - 文案见 §2.3.3 新增的第 5 行。
4. **实现顺序硬约束**：§八 第 7 步（打一发真实 `orders-algo-pending` 核对键名）**必须先做**，
   并把实测到的 `sz` / `closeFraction` / `reduceOnly` / `posSide` 四个字段的真实取值写进实施
   发现段。若实测表明 `sz` 恒可用，本节的 `None` 分支仍然保留（它是防御，不是猜测）。

`mismatch`（最危险的一格）：定义见上（本地声称有止损 + 交易所侧**确认**一条都没有）。这一格是
本轮存在的全部理由，故它在 wire、prompt、UI 三处**都必须有独立的呈现**，不得与「未核实」、也
不得与「覆盖未知」共用同一句话或同一个颜色。

#### 2.3.3 文案改写（诚实约束；未装配即逐字节回退）

`hold_horizon_text._exchange_snapshot_line`（`hold_horizon_text.py:205-210`）改为按 `basis` 分支。
**总闸关或 `backstop` 键缺席时，逐字返回今天这两句**（冻结用例守，保证「不开轴 B 的会话
prompt 一个字节都不变」）：

| `basis` / 状态 | 文案 |
|---|---|
| `exchange`，覆盖充分 | 交易所侧兜底：止损 X（已于 42s 前向交易所核实，覆盖 100%）——**进程退出后仍生效**。 |
| `exchange`，`mismatch` | ⚠ **本地记录声称挂过止损，但向交易所核实的结果是未覆盖（0%）**——此刻没有任何持久化兜底，本进程是唯一的退出执行者。请据此收紧你的退出计划，不要按「有兜底」规划持有。 |
| `exchange`，覆盖不足 | ⚠ 交易所侧止损只覆盖了当前持仓的 62%——未覆盖部分在本进程退出后**没有**兜底。 |
| `exchange`，**覆盖未知**（[P0-2] `coverageRatio is None`） | 交易所侧确认存在一张止损委托 X（已于 42s 前核实）——**但交易所未回报可核对的委托张数，本会话无法确认它是否覆盖了全部持仓**。请按「至少有一张、但未必覆盖全部」规划退出。 |
| `local` / `unavailable` | 交易所侧兜底（本地回执口径）：止损 X / 止盈 Y——**本会话尚未向交易所核实**（原因：未接入凭证），故不能据此假定它在进程退出后仍然生效。 |

同理，**跨日宪章那一句必须联动**：`_exchange_line`（`hold_horizon_text.py:116-133`）今天在跨日
模式下无条件写「即使本进程退出它们依然生效，故你**不必每轮盯盘**」。改为：只有当会话开启了
轴 B（有核实能力）时才保留该句；否则改写为「随单挂出的止盈止损**尚无核实通道**，请不要把
『不必盯盘』当作前提」。这一句是模型放松盯盘的直接授权来源，必须与证据强度一致。

#### 2.3.4 G6 覆盖闸（只拦加仓）

```python
def apply_backstop_gate(decision, wire, *, position_side: str, enabled: bool,
                        min_coverage: float) -> tuple[object, str | None]:
    """兜底覆盖闸：**只拦「在已有持仓上继续加仓」**，其余一律直通。（签名以 §4.5 为准，[P1-6]）

    判据（**五**条同时成立才拦）：① 总闸开；② `basis == "exchange"`（**只有核实过才敢拦**——
    未核实就拦等于用一个不确定性剥夺交易能力）；③ `coverageRatio is not None`
    （[P0-2] **覆盖未知一律放行**——「算不出」绝不能变成「不许加」，这与 `hold_horizon._is_bad_px`
    的既有极性逐字同构）；④ `coverageRatio < min_coverage`；
    ⑤ `decision.action` 是与当前持仓**同向**的开仓。

    反向开仓（反手）不拦：它由 `hold_horizon` 的 `gate_reverse` 负责，且反手会先平掉旧仓。
    空仓开仓不拦：新单自带随单 OCO（`executor.py:270-274`），拦它只会制造一个本来不存在的裸露。
    """
```

降级码：`horizon_backstop_uncovered(62%)`。

> **为什么只拦加仓而不是「拦一切并报警」**：裸露已经发生的那一部分，无论拦不拦都已经裸着；
> 唯一还能由系统决定的是「要不要让它变得更大」。把范围限死在这一格，本闸就永远不会成为
> 「明明该开仓却开不出来」的来源。

#### 2.3.5 明确不做：自动补挂 / 重挂

第 2 轮 §十 已把它登记为「另一轮的安全边界」，本轮维持该结论，理由三条：

1. 补挂需要新增 `POST /api/v5/trade/order-algo`——一条**全新的下单写路径**。本仓库现有的所有
   AI 侧安全层（`base_rules` / `profit_guard` / `hold_horizon` / `profit_cooldown` / `drawdown_brake`）
   的共同纪律是**绝不新增下单路径**，破例需要单独的安全评审与真实小额验证。
2. 补挂的正确参数（`sz` vs `closeFraction`、`reduceOnly`、`posSide` 在双向持仓下的取值、
   触发价类型）必须**先用真实/模拟盘打一发核对**（第 2 轮放行条件 19 的同款纪律），这本身就是
   一个独立工作量。
3. 本轮先把「知道有没有」做扎实。**没有可信的检测，任何自动补挂都是在盲目下单**——先检测后
   执行的顺序不能反。

### 2.4 轴 C：呈现

#### 2.4.1 启动弹窗（`AiTradeHorizonFields.tsx` / `aiTradeHorizon.ts`）

- `ContractSummary`（「本模式下系统将强制执行」）新增两行，数值由 `horizonContractLines` 按
  **服务端下发的预览值**渲染（前端不算，R-3 / G-10）：
  - 「平仓后 **1 小时**内不再开同向仓（反向不受限）」
  - 「**本会话**每日最多 **6 次**完整往返，突发额度 **3** 次（会话重启后预算重新计算；超出即暂停
    开新仓，**平仓与反手的平仓腿永不受限**）」（[P1-5] / [P0-1] 两处口径同时落在这一行）
- **[P2-1] 长周期模式追加一句代价披露**（`trend_hold` / `short_swing`，即 `perDay <= 2` 时才出）：
  「本模式每日只补 **1** 枚令牌：若连续两次快速离场用光突发额度，最长可能需要等待约 **20 小时**
  才能再次开仓（平仓不受影响）。这是长周期契约的代价，不是故障。」——这句话必须在**选模式那一刻**
  就出现，事后再解释就是投诉。
- `AcceptancePreview` 第 2 行的「每日换手次数」追加一枚**内联徽标**「运行期强制」，并在 tooltip
  写明 §2.2.6 的自指结论（含「本会话口径」）。这不是装饰：用户必须知道这一行从「事后判据」变成了
  「运行期约束」。
- 跨日模式（`short_swing` / `trend_hold`）在既有止损校验之外，新增一条**非阻断**提示：
  「本会话将每 **{backstopPollMinutes}** 分钟向交易所核实一次止损是否仍然挂着；核实不到时系统会
  如实告知 AI，并停止在未覆盖的持仓上加仓。」
  **[P2-4] 那个分钟数由 §4.1 的运维标志下发，前端不得硬抄 `2`**——它是
  `ai_trade_backstop_poll_ttl_s` 的函数，运维改了配置而文案不动，就又造出一句没核实的话。

#### 2.4.2 运行期（新建 `aiTradeStats/TurnoverBackstopCard.tsx`）

一张卡、三块，**全部读后端下发的数，前端零计算**：

```
┌ 换手纪律与兜底 ───────────────────────────────────────────┐
│ 再入场冷却   ● 冷却中 48:12        做多开仓暂停 · 做空不受限   │
│ 换手预算     ▓▓▓▓▓▓░░░  0.4 / 3    下一枚 42 分钟后到账       │
│              已拦下 7 次开仓意图（模型仍在试图高频换手）        │
│ 交易所兜底   ✓ 已核实 · 覆盖 100%   止损 62,140（42s 前核实）  │
└──────────────────────────────────────────────────────────┘
```

色彩纪律（沿用第 2 轮验收卡 D-6）：**「未核实」与「覆盖未知」都用灰色，不用黄色**——黄色会被
读成「快好了」，而 `local` / `unavailable` / `coverageRatio == null` 的语义都是「还不知道」。
只有 `mismatch` 与「覆盖不足（有数且低于阈）」用红色，因为它们是**确定的坏消息**。

#### 数据从哪来（[P1-1] / [P1-2]：v1 认错了载体，本节是修正后的唯一口径）

| 承载 | 真实键名 | 说明 |
|---|---|---|
| WS / REST 运行期快照 | **`horizon`** | `session._status_payload()`（`session.py:1557`）与 decision 事件（`:1485`）用的都是 `horizon`；类型在 `aiTradeTypes.ts:816`（`AiTradeSessionRow.horizon`）与 `:853`（`AiTradeStatusEvent.horizon`） |
| 轮内轨迹 | `horizonWire` | `trace.horizon_event(..., horizon=wire)` → `store.ts:316` `horizonWire: evt.horizon ?? null`（**整份落**） |
| 决策 payload（给 LLM） | `tradingHorizon` | `HorizonRuntime.inject` 写的那一份，**只给模型看** |
| 启动请求 / `config_json` / 分组视图 | `tradingHorizon` | 这是**契约**（`AiTradeHorizonRule`），不是快照 |

**v1 §4.3 说「`tradingHorizon` 快照新增两个嵌套对象」是错的**：那样加出来的键前端一个读点都没有。
两个新对象一律挂在 **`horizon.turnover` / `horizon.backstop`**（即 `build_snapshot` 的产物内），
从而自动搭上 status / decision / trace 三条既有载体，且 `store.ts` 与
`patchAiTradeMemberStatus` 都是整对象透传，无需逐键补抄（D-10 的结论仍然成立，只是键名换对）。

**渲染层级**：`AiTradeStatsLive` 收到的是 `AiTradeGroupState`（分组），而快照住在
`member.session.horizon`（每个成员一个独立会话、独立持仓、**独立令牌桶与独立兜底覆盖率**）。故：

- 卡片按**成员**渲染：单标的分组 ⇒ 就是上面那张三行卡；多标的分组 ⇒ 每成员一行，行首带标的名，
  **绝不把 N 个成员的令牌数加起来**（预算是每会话的，求和后的数不对应任何一条判定）。
- 排序按「最紧要优先」：`mismatch` > 覆盖不足 > 冷却中 > 预算耗尽 > 正常。
- 全部成员都无 `horizon.turnover` / `horizon.backstop` ⇒ **整卡不渲染**（沿用同页 `HorizonLiveCard`
  的既有纪律：绝不在没有契约的会话上挂一排 0）。

**[P1-3] 冷却到期必须主动补推一次 `status`**：仓库已为完全同款的问题写过专门补丁——
`_expire_profit_cooldown`（`session.py:1333-1336`）的注释原话是「不推则前端 chip 会一直挂着
（后端已不再拦，UI 却说在拦）」。G4 是同一形状：`_monitor_tick` 里在既有
`await self._expire_profit_cooldown()` 之后加一个同款的 `_expire_turnover_cooldown()`，
冷却由「激活」翻为「未激活」的那一 tick 补推一次 `status`。**预算不需要补推**（它是连续量，
每次 status 下发的 `tokens` / `nextTokenSeconds` 都是瞬时快照，前端只显示不自行推进）。

落点：`AiTradeStatsLive.tsx`（当前页）与 `AiTradePanel.tsx`（右侧面板 chip，只显示最紧要的一格）。

#### 2.4.3 轨迹与降级码

- `apps/web/lib/aiTrade.ts:495` 的 `DEGRADE_REASON_LABELS` 新增三条前缀条目：
  `horizon_reentry_cooldown` →「再入场冷却」、`horizon_turnover_budget` →「换手预算用尽」、
  `horizon_backstop_uncovered` →「交易所兜底未覆盖」。**漏加不会报错、只会静默显示原始码**
  （第 1 轮 P1-4 的同款坑），故有专属用例守。
- `trace.py` 的 `horizon` 相位复用，不新增相位：新 wire 作为 `horizonWire.turnover` /
  `horizonWire.backstop` 的嵌套键搭车（§5.4），**因此不需要动 `store.ts` 的逐键手抄 reducer**
  （`store.ts:316` 是 `horizonWire: evt.horizon ?? null`，整份落）。这是本轮刻意选择的形状——
  `edgeSource` 那次的教训是「新键要么整对象透传、要么必须逐处补抄」。
- **[P2-2] 轨迹里的两个 veto 计数会慢一轮，必须原地修正**：`apply_horizon_trace` 在
  `_apply_horizon_gate` 内部就把快照挂进了本轮 trace（`session.py:1013`），而换手闸排在它之后
  （§2.2.5）。由于 trace 存的是**同一个 dict 引用**（`trace["horizonWire"] = wire`），
  `_apply_turnover_gate` 只要**原地 mutate** `snapshot["turnover"]`（而不是重建一个新 dict），
  轨迹与 decision 事件就自动拿到判定后的值。这一条写进 `_apply_turnover_gate` 的 docstring，
  并由 S-3 断言「被拦轮的 trace 里 `budgetVetoes` 已经 +1」。

#### 2.4.4 回测

- `aiStats.horizon` 追加 `reentryVetoes` / `budgetVetoes` / `opensCounted` 三个只增计数；
  `panels.tsx` 的周期模式面板显示前两个。
- `format_backtest_notes` 追加**三**句免责：
  1. 「回测按根推进，再入场冷却的实际生效粒度是 `ceil(cooldown_s / bar_seconds)` 根；
     本模式冷却 90s < 一根 5m，**回测中恒不生效**」（点名具体模式，沿用第 1 轮 P2-7）；
  2. 「回测**不装配**交易所侧兜底核实（回测没有交易所），G6 恒放行；回测结论不覆盖轴 B」；
  3. **[P2-5]**「被换手闸拦下的**反手**会降级为平仓（退出腿保留、再入场腿否掉），故这类退出在
     退出来源分布里记为『Agent 主动平仓』而非『Agent 反手』；两轮之间对比该分布时须知道这一点。」

### 2.5 明确不碰的东西（边界）

- 不改 `hold_horizon.evaluate_horizon_gate` / `gated_kind` / `RELEASE_GATES` 的任何判定式
  （G1/G2/G3 与豁免矩阵**逐字节冻结**，AST + 文本双重断言守）。
- 不改 `live_auto/executor.py`、`engine/simulator.py` 一个字符（`LiveExecutor` 为两条链路共用，
  这是本仓库反复出事的地方）。
- 不改 `profit_cooldown.py` / `drawdown_brake.py` 的任何行为。
- `okx/trading.py` **只允许一处加法**：`_normalize_algo_order` 的返回字典新增 `closeFraction`
  一个键（[P0-2]）。既有键一个不动、既有调用方（`fetch_live_open_orders` → 委托页签 / 图表虚线 /
  持仓子行）读的都是具名键，故加键零影响；须补一条「既有归一结果的其余键逐键相等」的用例。
- 不新增 DB 列、不新增迁移（新数据全部走既有 `metrics_json` / `config_json` 的加法键）。
- 不新增任何下单 / 撤单 / 改单路径（§2.3.5）。
- 不改动第 2 轮验收卡的**判定式**；只加两个输入字段与一句 note（§2.2.6）。

---

## 三、文件 / 模块变更计划

### 3.1 后端 · 新建

| 文件 | 意图 |
|---|---|
| `services/quant/app/ai_auto/turnover_discipline.py` | 轴 A 纯核心：三条派生式 + `TurnoverRule/State` + G4/G5 判定 + wire |
| `services/quant/app/ai_auto/exchange_backstop.py` | 轴 B：唯一一条只读 IO（`fetch_algo_backstop`）+ 纯核心分类 + `BackstopRuntime`（单飞调度）+ G6 判定 |
| `services/quant/tests/test_turnover_discipline.py` | 轴 A 纯核心用例（§6.1 T 组） |
| `services/quant/tests/test_exchange_backstop.py` | 轴 B 分类 / 三态 / G6 用例（§6.1 B 组） |
| `services/quant/tests/test_ai_trade_turnover_session.py` | 装配 + 决策链 + 成交事件推进的会话级用例（§6.1 S 组） |

### 3.2 后端 · 修改

| 文件 | 改动 | 行数预算 |
|---|---|---|
| `ai_auto/hold_horizon.py` | `HorizonRule` 加 4 字段；`resolve_horizon_rule` 回填（调 `turnover_discipline` 的派生式，**单向依赖**，[P1-9]）；`rule_wire` / `profile_wire` 镜像；`build_snapshot` 加**关键字形参** `turnover` / `backstop`（`None` ⇒ 整键缺席；**空仓分支同样要带 `turnover`**，[P0-3] 的前提） | ≤ +45 |
| `ai_auto/hold_horizon_text.py` | `_gate_lines` 追加两行；新增 `_turnover_line`；**flat 分支改为拼接而非早退**（[P0-3]）；`_exchange_snapshot_line` 按 `basis` **五**分支；`_exchange_line` 联动；`format_backtest_notes` 追加三句 | ≤ +80 |
| `ai_auto/horizon_runtime.py` | 持有 `TurnoverState` / `TurnoverRule`；`note_open` 加一次纯状态推进；`note_close` **先取 `state.epoch[0]` 再结算**（[P0-4]）；`gate` 之外新增 `turnover_gate(decision, degrade, *, position_side)`；`expire_cooldown(now_wall) -> bool`（[P1-3]）；`stats()` 加三个计数；`refresh` 把两个新 wire 传进 `build_snapshot` | ≤ +65 |
| `ai_auto/horizon_acceptance.py` | `resolve_thresholds` 改调 `derive_turnover_per_day`（**两个值一次取回**，删内联式，[P1-4]）；`AcceptanceInput` 加 2 字段；`build_input` 接线；`merge_inputs` 逐字段合并（`armed` 取 `or`、`vetoes` 相加） | ≤ +20 |
| `ai_auto/acceptance_text.py` | `turnover_note` 增两个可选形参 + 追加一句自认（含「本会话口径」，[P1-5]；未武装 ⇒ 逐字节不变） | ≤ +12 |
| `ai_auto/session.py` | `_apply_turnover_gate` / `_apply_backstop_gate` 两个**薄封装**（各 ≤4 行，均传 `position_side=self.executor.projected`）+ 盯市 tick 两行（调度 + 冷却到期补推）+ 装配一行 | **≤ +18（硬预算，v1 的 14 行未含 [P1-3] 的到期补推与 `position_side` 实参）** |
| `ai_auto/backtest.py` | 同位插入轴 A 两行（传 `position_side=self.main.pos.side`）+ `gate_reason` 合成式**六元扩八元** + 既有「五元合成」注释笔误顺手改正 + notes 三句；**轴 B 不装配** | ≤ +14 |
| `okx/trading.py` | `_normalize_algo_order` **只加一个键** `closeFraction`（[P0-2] 覆盖判据要用；加法、不改既有键、既有调用方零影响） | ≤ +2 |
| `ai_auto/trace.py` | 无需改动（新 wire 搭 `horizonWire` 嵌套键） | 0 |
| `api/ai_auto.py` | `horizon-profiles` 回执透传三个新预览字段（由 `profile_wire` 产出，本文件零逻辑） | ≤ +2 |
| `schemas.py` | `AiTradeHorizonIn` 加 2 个可选覆盖字段 + 值域 | ≤ +10 |
| `config.py` | §5.1 的 11 个新配置 | ≤ +30 |
| `.env.example` | 同步 11 项注释（含 §2.2.6 自指披露与 §2.3.5 的不做声明） | ≤ +25 |

### 3.3 前端 · 新建

| 文件 | 意图 |
|---|---|
| `apps/web/components/Agent/aiTradeStats/TurnoverBackstopCard.tsx` | §2.4.2 的三块卡（纯展示，零计算） |
| `apps/web/components/Agent/aiTradeStats/TurnoverBackstopCard.test.tsx` | 三态渲染 + 灰/红色彩纪律 + 零样本不编数 |
| `apps/web/lib/aiTradeTurnover.ts` | 纯格式化 helper（`describeCooldown` / `describeBudget` / `describeBackstop`），供卡片与 chip 共用 |
| `apps/web/lib/aiTradeTurnover.test.ts` | helper 用例（含 `null` 与 `0` 可分） |

### 3.4 前端 · 修改

| 文件 | 改动 |
|---|---|
| `lib/aiTradeTypes.ts` | `AiTradeHorizonSnapshot` 加 `turnover?` / `backstop?`；`AiTradeHorizonProfile` 加三个预览字段；`AiTradeHorizonStats` 加三个计数 |
| `lib/aiTradeHistoryTypes.ts` | 历史 `aiStats.horizon` 同步三个计数（**第 2 轮 P1-10 的教训：类型要改五处，漏一处历史页就读不到**） |
| `lib/aiBacktestTypes.ts` | 回测 `aiStats.horizon` 同上 |
| `lib/aiTrade.ts` | `DEGRADE_REASON_LABELS` 新增三条前缀 |
| `lib/aiTradeHorizon.ts` | `horizonContractLines` 新增两行；`horizonKpis` / `horizonLiveKpis` 追加拦截计数 |
| `components/Agent/AiTradeHorizonFields.tsx` | 契约摘要两行 + 验收预览「运行期强制」徽标 + 跨日提示 |
| `components/Agent/aiTradeStats/AiTradeStatsLive.tsx` | 挂 `TurnoverBackstopCard`，数据源是 **`Object.values(group.members).map(m => m.session.horizon)`**（[P1-2]：`group.tradingHorizon` 是契约不是快照），按成员渲染、按「最紧要优先」排序 |
| `components/Agent/aiTradeStats/AiTradeStatsDetail.tsx` | 历史页显示三个计数（无实时兜底态 ⇒ 该块整块不渲染，**不显示一个假的「已核实」**） |
| `components/Chart/AiTradePanel.tsx` | 最紧要一格的 chip（优先级：`mismatch` > 冷却中 > 预算耗尽 > 正常不显示） |
| `components/Chart/aiBacktest/panels.tsx` | 两个拦截计数 + 两句免责 |

---

## 四、接口设计

### 4.1 `GET /api/ai-trade/horizon-profiles`（既有端点 · 加法字段）

每个 profile 条目新增三个**预览**字段（`custom` 恒 `null`，沿用第 1 轮 P1-8）：

```jsonc
{
  "id": "intraday_swing",
  "reentryCooldownSeconds": 3600,      // = 0.5 × minHoldSeconds
  "turnoverBudgetPerDay": 6,           // = min(86400/targetHoldMinSeconds, 24)
  "turnoverBudgetCapacity": 3          // = max(2, ceil(perDay × 0.5))
}
```

顶层新增**三**个只读运维标志，供 UI 如实说明「这两道闸是否真的开着 / 多久核实一次」
（[P2-4]：第三个是为了让弹窗那句「每 N 分钟核实一次」不再硬抄配置值）：

```jsonc
{ "turnoverGateEnabled": true, "backstopCheckEnabled": true, "backstopPollSeconds": 120 }
```

### 4.2 `POST /api/ai-trade/start`（既有 · `tradingHorizon` 加法字段）

```jsonc
{
  "tradingHorizon": {
    "profile": "intraday_swing",
    "reentryCooldownSeconds": null,   // null ⇒ 派生；0 ⇒ 显式关闭 G4；ge=0 le=604800
    "turnoverBudgetPerDay": null      // null ⇒ 派生；0 ⇒ 显式关闭 G5；ge=0 le=1000
  }
}
```

回执 `config_json.tradingHorizon` 镜像**派生后的实际值**（含 `turnoverBudgetCapacity`）——
第 1 轮 P1-9 的纪律：只回显入参等于把派生关系藏起来。

### 4.3 `GET /api/ai-trade/status` 与 WebSocket（既有 · 加法键）

**[P1-1] 键名以本节为准（v1 认错了载体）**：运行期快照在 status / decision / trace 三条载体上
一律叫 **`horizon`**（`session.py:1557` / `:1485` / `trace.horizon_event`），前端类型分别是
`AiTradeStatusEvent.horizon`（`aiTradeTypes.ts:853`）、`AiTradeSessionRow.horizon`（`:816`）、
`AiTradeRoundTrace.horizonWire`（`:773`）。而 `tradingHorizon` 是**契约**（`AiTradeHorizonRule`），
出现在启动请求 / `config_json` / 分组视图 / 决策 payload——**两者不是一个东西，不可互换**。

故：`horizon` 快照新增两个嵌套对象 `horizon.turnover` / `horizon.backstop`（形状见 §5.4）。
**未装配 / 总闸关 ⇒ 整键缺席**（不是 `null`）——键缺席是本仓库「prompt 逐字节回退」的既定判据。
成员级实时通路是 `patchAiTradeMemberStatus` 的整体 spread（不吞键），故前端无需逐键补抄；
但**类型必须补**（`AiTradeHorizonSnapshot` 加两个可选字段），否则 tsc 报错而非静默丢数据。

`aiStats.horizon` 新增 `reentryVetoes` / `budgetVetoes` / `opensCounted` 三个只增计数。

### 4.4 回测结果（既有形状 · 加法键）

`aiStats.horizon` 同 §4.3 三键；`notes` 追加 §2.4.4 的两句。`backstop` 键**在回测结果中恒缺席**。

### 4.5 内部函数签名（下游按此实现，不再二次决策）

```python
# turnover_discipline.py —— 依赖方向：只 import `acceptance_text` 与 `decider`，
# **绝不 import `hold_horizon`**（后者要 import 本模块，反向即成环，[P1-9]）。
def derive_turnover_per_day(
        target_hold_min_s: float | None) -> tuple[float | None, float | None]   # (capped, raw)
def derive_max_turnover_per_day(target_hold_min_s: float | None) -> float | None  # = [0] 别名
def derive_reentry_cooldown_s(min_hold_s: float | None, *, frac: float) -> float
def derive_turnover_capacity(per_day: float | None, *, burst_frac: float,
                             min_capacity: float) -> float
def rule_from_horizon(rule, settings) -> TurnoverRule | None       # 鸭子类型读 rule，不 isinstance
def init_state(rule: TurnoverRule, *, now_wall: float) -> TurnoverState
def refill(state: TurnoverState, rule: TurnoverRule, *, now_wall: float) -> TurnoverState
def on_open_fill(state, rule, *, now_wall: float) -> TurnoverState
def on_close_fill(state, rule, *, closed_side: str, now_wall: float) -> TurnoverState
def build_wire(state, rule, *, now_wall: float) -> dict | None
def apply_turnover_gate(decision, wire: dict | None, rule: TurnoverRule | None, *,
                        position_side: str) -> tuple[object, str | None]

# exchange_backstop.py
async def fetch_algo_backstop(creds, inst_id: str, *, timeout_s: float) -> list[dict] | None
def classify_backstop(orders, *, side, position_sz, local_sl_px, local_tp_px,
                      checked_at_wall, now_wall, max_age_s, unavailable_reason) -> dict
def apply_backstop_gate(decision, wire, *, position_side: str,
                        enabled: bool, min_coverage: float) -> tuple[object, str | None]

class BackstopRuntime:
    def __init__(self, settings, creds, inst_id: str) -> None
    def schedule_check(self, now_wall: float, *, side: str, position_sz: float,
                       local_sl_px, local_tp_px, push=None) -> None   # 同步、单飞、绝不阻塞
    def force_next(self) -> None                              # note_open 之后调用
    def close(self) -> None                                   # 会话 stop 时置 `_closed`（约束 5）
    def wire(self, now_wall: float) -> dict | None            # 最近一次分类结果（未启用 ⇒ None）
```

**装配路径（[P1-9]，v1 未定，下游不得自行发挥）**：

- `HorizonRuntime.__init__` 增一个**关键字形参** `turnover_rule: TurnoverRule | None = None`
  （默认 `None` ⇒ 整层空转 ⇒ 既有直接构造 `HorizonRuntime(rule)` 的单测逐字零回归）。
  **运行时不读 settings**——它今天就不持有 settings，加一个只为读两个开关的依赖是倒退。
- `TurnoverRule` 由**调用方**构造：实盘在 `session.__init__` 里
  `rule_from_horizon(horizon_rule, executor.settings)`（settings 经 `executor.settings` 取，
  与 `CostRuntime` / `PriorRuntime` 同款，`session.py:172-180`）；回测在
  `AiBacktestRunner.__init__` 里用 `spec.settings`，并在那里应用**回测侧独立总闸**
  `ai_trade_horizon_turnover_backtest_enabled`（关 ⇒ 传 `None`）。
- `BackstopRuntime` 只在 `session` 装配（回测不装配，D-5），`creds` 取 `executor.creds`
  （`creds is None` ⇒ 一个请求都不发）。

---

## 五、数据模型

### 5.1 配置（`services/quant/app/config.py`，全部新增）

```python
# ---- 交易周期模式 · 开仓侧换手纪律（ai-trade-turnover-budget-and-backstop §2.2）----
# 总闸关 ⇒ G4/G5 整层不装配、`tradingHorizon.turnover` 整键缺席、prompt 逐字节回退。
ai_trade_horizon_turnover_enabled: bool = True
ai_trade_horizon_turnover_backtest_enabled: bool = True        # 回测侧独立总闸（沿用既有先例）
# G4：再入场冷却 = frac × min_hold_s。0 ⇒ 关闭本闸（不是「无限长」）。
ai_trade_horizon_reentry_frac: float = Field(default=0.5, ge=0, le=4)
ai_trade_horizon_reentry_scope: Literal["same_side", "both"] = "same_side"
# G5：令牌桶。速率**不在此处配置**——它恒等于验收卡第 3 行的阈值（§2.2.1），
# 另开一个旋钮就等于允许「弹窗承诺 6 次、运行期执行 12 次」这种自相矛盾。
ai_trade_horizon_turnover_burst_frac: float = Field(default=0.5, gt=0, le=1)
ai_trade_horizon_turnover_min_capacity: float = Field(default=2.0, ge=1, le=100)

# ---- 交易所侧兜底真值化（§2.3）----
# 总闸关 ⇒ 一个请求都不发、`backstop` 整键缺席、兜底文案逐字回退到本地回执口径。
ai_trade_backstop_check_enabled: bool = True
ai_trade_backstop_poll_ttl_s: float = Field(default=120.0, ge=30, le=3600)
ai_trade_backstop_timeout_s: float = Field(default=6.0, gt=0, le=30)
# 核实结果的最大可信年龄；超过即从 `exchange` 降级为 `unavailable(stale)`（绝不拿旧读数背书）。
ai_trade_backstop_max_age_s: float = Field(default=300.0, ge=60, le=86400)
ai_trade_backstop_min_coverage: float = Field(default=0.999, gt=0, le=1)
ai_trade_backstop_gate_scale_in: bool = True                   # G6；关 ⇒ 只报不拦
```

### 5.2 内存态

| 归属 | 字段 | 作用域 | 说明 |
|---|---|---|---|
| `HorizonRuntime` | `turnover_state: TurnoverState` | **会话级**（跨 epoch 存活） | 令牌桶与冷却；**绝不随 `HorizonState` 重置** |
| `HorizonRuntime` | `turnover_rule: TurnoverRule \| None` | 会话级不变 | 由 `HorizonRule` 投影，`None` ⇒ 整层空转 |
| `BackstopRuntime` | `last: dict \| None` / `checked_at_wall` / `_task` / `_force` | 会话级 | 单飞句柄 + 最近一次分类结果 |

### 5.3 落库（**零迁移**）

- 三个新计数进 `Run.metrics_json.aiStats.horizon`（既有加法键路径）。
- 两个新入参进 `config_json.tradingHorizon`（既有 JSON 列）。
- `backstop` 是实时态，**不落库**——一个 3 天前的「已核实」读数没有任何意义，落它只会让历史页
  显示一个骗人的绿勾（§2.4.2 已规定历史页整块不渲染）。

### 5.4 wire 形状

```jsonc
// horizon.turnover（[P1-1] 挂在 `horizon` 快照下，不是 `tradingHorizon`；未装配 ⇒ 整键缺席）
{
  "cooldownActive": true,
  "cooldownRemainingSeconds": 2892,
  "cooldownSide": "long",                // 真源是 epoch[0]，不是成交字典的 side（[P0-4]）
  "cooldownScope": "same_side",
  "cooldownSeconds": 3600,               // 本会话的冷却时长（契约值，供 UI 画进度）
  "budgetEnabled": true,
  "budgetScope": "session",              // [P1-5] 恒 "session"：会话重启即回满，文案必须照此措辞
  "tokens": 0.42,                        // 当前令牌（下发瞬间快照，前端不自行推进）
  "capacity": 3,
  "perDay": 6,
  "nextTokenSeconds": 2520,              // null ⇒ 已有可用令牌
  "blocked": true,                       // 「若本轮从空仓提议开新仓是否会被拦」（G4 或 G5 任一）
  "reverseWouldDowngrade": true,         // [P0-1] 「若本轮提议反手，再入场腿会被否、只执行平仓腿」
  "reentryVetoes": 3, "budgetVetoes": 7, "opensCounted": 11
}

// horizon.backstop（未装配 / 回测 ⇒ 整键缺席）
{
  "basis": "exchange",                   // exchange | local | unavailable
  "unavailableReason": null,             // disabled | no_creds | not_run | error | stale | null
  "checkedAgoSeconds": 42,               // null ⇒ 从未核实
  "stopLossPx": 62140.0, "takeProfitPx": null,
  "hasStop": true,                       // [P0-2] 「交易所侧确认存在一张方向正确的止损委托」
  "coveredSz": 3.0,                      // null ⇒ 交易所未回报可核对的张数
  "positionSz": 3.0,                     // null ⇒ 本地持仓读数不可用（挂单期 / 无仓）
  "coverageRatio": 1.0,                  // **null 是合法值**＝「还不知道」，与 0＝「确认裸露」不同
  "orders": 1,
  "localStopLossPx": 62140.0, "localTakeProfitPx": 68000.0,
  "mismatch": false                      // 仅当 basis=="exchange" && !hasStop && 本地声称有止损
}
```

**零样本口径（沿用第 1 轮 P2-3）**：没有读数的字段一律下发 `null`，**绝不用 `0` 冒充**。
`coverageRatio: 0` 与 `coverageRatio: null` 在这张卡上是「确认裸露」与「还不知道」两个结论。

---

## 六、测试与验收标准

### 6.1 后端单测（新增约 60 条）

**T 组 · `test_turnover_discipline.py`（纯核心，约 26 条）**

| # | 用例 | 断言 |
|---|---|---|
| T-1 | 四个预设的三条派生值 | 与 §2.2.1 表**逐格相等**（含 `hourly_swing` 被硬上限夹到 24） |
| T-2 | `custom` 全默认 | `rule_from_horizon` 返回 `None` ⇒ 三个函数全部零动作、`build_wire` 为 `None` |
| T-3 | `refill` 幂等 | 同一 `now_wall` 调两次结果相同；跨 24h 后 `tokens == capacity`（不溢出） |
| T-4 | `on_open_fill` 扣减下界 | `tokens` 恒 `>= 0`，绝不为负 |
| T-5 | G4 只拦同向 | `cooldown_side="long"` 时 `open_long` 被拦、`open_short` 直通 |
| T-6 | `scope="both"` | 两向都拦 |
| T-7 | G4/G5 永不拦 close | `close` / `hold` / `cancel_order` 在任何状态下返回**同一个对象**（`is` 相等） |
| **T-14** | **[P0-1] 反手降级为 `close` 而非 `hold`** | 持空 + `open_long` + 令牌耗尽 ⇒ 返回 `action == "close"`；持多 + `open_short` 同理；降级码后缀含 `reverse→close` |
| **T-15** | **[P0-1] 同向加仓恒直通** | 持多 + `open_long`（任何冷却 / 令牌状态）⇒ 返回**同一个对象**（`is` 相等） |
| **T-16** | **[P0-1] 只有 flat 开仓才降级为 `hold`** | `position_side="flat"` + 令牌耗尽 ⇒ `hold`；`position_side="weird"` ⇒ 恒放行 + `gateSkipped=="unknown_side"` |
| **T-17** | **[P0-4] `closed_side` 不取自成交字典** | 用 `{"side": "close"}` 与 `{}` 两种真实入参驱动 `note_close`，`cooldownSide` 仍为 `long`/`short`；`epoch is None` ⇒ **不开启冷却** |
| **T-18** | **[P1-8] `refill` 的时间倒流保护** | `now_wall < last_refill_wall` ⇒ `tokens` **不减少**（elapsed 夹到 0） |
| **T-19** | **[P1-4] 两个派生值同源** | `derive_turnover_per_day(360)` 返回 `(24.0, 240.0)`；`hourly_swing` 的 raw 与 capped 不相等（弱门槛自认句据此产出） |
| T-8 | G4 先于 G5 | 两者同时激活时降级码前缀为 `horizon_reentry_cooldown` |
| T-9 | 降级码格式 | 正则匹配 `horizon_turnover_budget\(0\.4/3,next=42m\)` 形状 |
| T-10 | `per_day=None` | G5 结构性空转（哪怕 `tokens=0`） |
| T-11 | 长期速率 | 模拟 7 天、每次有令牌就开仓 ⇒ 总开仓数 ≤ `per_day × 7 + capacity` |
| T-12 | `min_capacity=2` 的安全阀 | `trend_hold` 下「开→立刻平→再开」不被 G5 拦（只被 G4 拦） |
| T-13 | wire 零样本 | 未激活时 `cooldownRemainingSeconds` 为 `null` 而非 `0` |

**B 组 · `test_exchange_backstop.py`（约 20 条）**

| # | 用例 | 断言 |
|---|---|---|
| B-1 | 三态 `basis` | `no_creds` / `error` / `stale` 三条路径各自的 `unavailableReason` |
| B-2 | 只认止损腿 | 只有止盈腿的委托 ⇒ `coverageRatio == 0`、`stopLossPx is None` |
| B-3 | 方向过滤 | 反向 / 非 reduceOnly 的委托不计入覆盖 |
| B-4 | `mismatch` 判据 | 本地有价 + 交易所覆盖 0 ⇒ `mismatch is True` |
| B-5 | 部分覆盖 | 两腿共 2 张 / 持仓 3 张 ⇒ `coverageRatio == 2/3` |
| B-6 | `stale` 降级 | `checked_at_wall` 超 `max_age_s` ⇒ `basis == "unavailable"` 且**不再下发交易所价** |
| B-7 | G6 只拦同向加仓 | 空仓开仓 / 反向开仓 / `close` 全部直通（同一对象） |
| B-8 | G6 未核实不拦 | `basis != "exchange"` ⇒ 恒放行（fail-open） |
| B-9 | `fetch` 异常吞 | 客户端抛（含 `OKXError` / `OKXPrivateError` / 超时）⇒ 返回 `None`，绝不外泄异常 |
| B-10 | `creds is None` | 一个请求都不发（用假客户端断言调用次数为 0） |
| **B-11** | **[P0-2] `sz` 缺省 + `closeFraction="1"`** | ⇒ `coverageRatio == 1.0`、`mismatch is False`（**不得**判成 0） |
| **B-12** | **[P0-2] `sz` 与 `closeFraction` 都不可用** | ⇒ `coverageRatio is None`、`hasStop is True`、`mismatch is False`、G6 **放行** |
| **B-13** | **[P0-2] `reduceOnly` 缺省但方向正确** | 净持仓多头 + `side=="sell"` + `posSide=="net"` ⇒ 计入覆盖（`reduceOnly` 只是充分条件之一） |
| **B-14** | **[P0-2] `position_sz <= 0`** | ⇒ 不做除法、`coverageRatio is None`、不抛 `ZeroDivisionError` |
| **B-15** | **[P1-7] 复用既有归一** | 用真实形状的 OKX 行喂 `_normalize_algo_order`，断言 `exchange_backstop` 不再定义第二个解析函数（AST 扫描：本模块无 `slTriggerPx` 字面量解析） |
| **B-16** | 既有归一零回归 | `_normalize_algo_order` 加 `closeFraction` 后，其余键与加键前**逐键相等** |

**S 组 · `test_ai_trade_turnover_session.py`（装配与链路，约 14 条）**

| # | 用例 | 断言 |
|---|---|---|
| S-1 | 端到端消耗 | 一次开仓成交后 `tokens` 减 1；**同 epoch 的加仓成交不再减** |
| S-2 | 平仓开启冷却 | `note_close` 后 `cooldownActive is True`、方向正确 |
| S-3 | 决策链位置 | 用假决策器强制 `open_long`，验证降级码与 `gate_reason` 合成顺序 |
| S-4 | 反手消耗一枚 | 反手产生新 epoch ⇒ `opensCounted` +1 |
| S-5 | 冻结门禁 G-1 | 三个总闸全关 ⇒ payload / prompt / status **逐字节**与 HEAD 相同（`sha256` 断言） |
| S-6 | 冻结门禁 G-2 | `evaluate_horizon_gate` / `gated_kind` / `RELEASE_GATES` 源码 AST 与文本零 diff |
| S-7 | 冻结门禁 G-3 | 实盘与回测在同一断面下 `gate_reason` **逐字相等** |
| S-8 | 单一真源 G-4 | `resolve_thresholds().max_turnover_per_day` 与 `rule.turnover_per_day` 对四个预设逐格相等 |
| S-9 | 验收自认 | `armed=True` 时第 3 行 note 含自认句；`armed=False` 时 note **逐字节**与 HEAD 相同 |
| S-10 | 回测无 backstop | 回测结果 `aiStats` / `tradingHorizon` 中 `backstop` 键**缺席** |
| S-11 | 盯市 tick 不阻塞 | 造一个 3s 才返回的假取数，断言 `_monitor_tick` 耗时 < 1s（单飞后台） |
| S-12 | 无仓不发请求 | `projected == "flat"` 时假客户端调用次数为 0 |
| **S-13** | **[P0-3] 空仓 prompt 不再说反话** | 冷却激活 + 空仓 ⇒ `format_horizon_for_prompt` 含换手纪律行、且**不含**「不约束开仓」；两闸皆空转 + 空仓 ⇒ 与 HEAD **逐字节相同**（`sha256`） |
| **S-14** | **[P0-1] 反手退出腿真的执行了** | 持多 + 令牌耗尽 + 模型提议 `open_short` ⇒ 会话最终**产生一笔平仓成交**、`projected` 转 `flat`（断言到 executor 层，不只断言 decision） |
| **S-15** | **[P1-3] 冷却到期补推 status** | 冷却由激活翻为未激活的那一 tick 恰好推一次 `status`，且其 `horizon.turnover.cooldownActive is False` |
| **S-16** | **[P1-5] 会话重启预算回满** | 新建会话 ⇒ `tokens == capacity`；用例名与文案里的「本会话口径」措辞对齐（这条用例的存在本身就是那句自认的凭据） |
| **S-17** | **[P2-2] 轨迹计数不落后一轮** | 被 G5 拦下的那一轮，`trace.horizonWire.turnover.budgetVetoes` 已 +1 |
| **S-18** | **[P1-9] 无循环 import** | `import services.quant.app.ai_auto.turnover_discipline` 单独可导入；AST 断言其 import 列表不含 `hold_horizon` |

### 6.2 前端单测（新增约 18 条）

- `aiTradeTurnover.test.ts`：三个 helper 的 `null` / `0` 可分；冷却 / 预算 / 三态兜底的文案分支。
- `TurnoverBackstopCard.test.tsx`：`mismatch` 渲染红色且文案含「没有任何持久化兜底」；
  `local` / `unavailable` 渲染**灰色**（断言不含黄色 class）；`backstop` 键缺席时整块不渲染。
- `aiTradeHorizon.test.ts`（既有文件加用例）：`describeDegradeReason` 认识三个新前缀；
  `horizonContractLines` 在 `custom` 全默认时**不产出**那两行。
- `AiTradeHorizonFields.test.tsx`（既有）：契约摘要两行的数值来自 props 下发值（改一个数即变），
  且**不存在任何硬编码的 3600 / 6 / 3**（正则扫描，沿用第 1 轮 G-10）。
- `AiTradeStatsDetail.test.tsx`：历史页在无 `backstop` 时不渲染兜底块（不显示假绿勾）。

### 6.3 放行条件（Definition of Done，逐条可核对）

| # | 条件 |
|---|---|
| 1 | 三个总闸全关时，S-5 / S-6 两条冻结门禁全绿；`live_auto/executor.py` 与 `engine/simulator.py` **零 diff** |
| 2 | `custom` 全默认跑通一轮完整 status 装配不抛异常，且 `turnover` / `backstop` 两键**缺席** |
| 3 | §2.2.1 的四模式落地值表逐格通过（T-1）；`derive_turnover_per_day` **一次返回两个值**（T-19），且**代码中不存在第二处**计算 `86400 / lo` 的地方（grep 自证，含 `horizon_acceptance.py` 的 `derived`） |
| 4 | `session.py` 净增 **≤ 18 行**；超出即在本文档末尾登记欠账并说明，**不得默默超支** |
| 5 | 令牌只在 `note_open` 消耗、且加仓不重复消耗（S-1）；`tokens` 恒非负（T-4）；时间倒流不减令牌（T-18） |
| 6 | **[P0-1]** 三道闸对 `close` / `hold` / `cancel_order` **与同向加仓**返回**同一个对象**（`is` 相等，T-7 / T-15 / B-7）；**被拦的反手降级为 `close` 且退出腿真的成交**（T-14 / S-14）；**任何一道新闸都不得使某笔已有持仓比不装本轮时更晚离场**（这是本轮的一句话安全不变式） |
| 7 | G6 在 `basis != "exchange"` **或** `coverageRatio is None` 时恒放行（B-8 / B-12）；**未核实、算不出，都绝不剥夺交易能力** |
| 8 | 兜底文案**五**个分支各一条用例（含 [P0-2] 的「覆盖未知」）；总闸关时 `_exchange_snapshot_line` 与 `_exchange_line` **逐字节**与 HEAD 相同 |
| 8b | **[P0-3]** 空仓 + 两闸空转时 `format_horizon_for_prompt` 与 HEAD **逐字节相同**；空仓 + 闸激活时**不含**「不约束开仓」字样（S-13） |
| 8c | **[P0-4]** 用真实入参（`{"side":"close"}` / `{}`）驱动的冷却方向正确（T-17 / S-2）；`epoch is None` 时不开启冷却 |
| 8d | **[P0-2]** §八 第 7 步的真实 `orders-algo-pending` 实测已完成，`sz` / `closeFraction` / `reduceOnly` / `posSide` 四字段的实测取值写进实施发现段；**未做此步不得合入轴 B** |
| 9 | 盯市 tick 不被网络 IO 阻塞（S-11）；无仓 / 无凭证时请求数为 0（S-12 / B-10） |
| 10 | 验收卡第 3 行的自指披露已实现（S-9）**且含「本会话口径」措辞**（[P1-5]），`budgetVetoes` 在**运行期 UI 与回测面板**都有呈现落点 |
| 11 | 三个新降级码已进 `DEGRADE_REASON_LABELS`，并有前端用例守 |
| 12 | 前端类型改**五处**（`aiTradeTypes` / `aiTradeHistoryTypes` / `aiBacktestTypes` + 两处组件 props），历史页与回测页都能读到三个新计数；**新快照字段挂在 `AiTradeHorizonSnapshot` 下**（[P1-1]，不是 `AiTradeHorizonRule`） |
| 12b | **[P1-2]** 多标的分组下卡片按成员渲染、按「最紧要优先」排序、**不做跨成员求和**；有一条两成员的组件用例守 |
| 12c | **[P1-3]** 冷却到期补推一次 `status`（S-15），前端 chip 不会停在「冷却中」 |
| 13 | 前端**不存在**任何本轮新数值的硬编码：正则只扫**本轮三个契约值的来源**（`reentryCooldownSeconds` / `turnoverBudgetPerDay` / `turnoverBudgetCapacity` 的字面量赋值）与 §2.4.1 那句核实周期文案（[P2-3]：v1 的「扫 `3600`」会误伤既有时间换算 helper） |
| 14 | 全量 `pytest` 与**干净 HEAD worktree 实测**的红灯基线逐条一致（不要靠记忆；已知基线：`test_ai_trade_prior_session.py` 7 条、`test_factor_library_expansion.py` 7 条孤儿文件、`test_agent_ws.py` 顺序 flake），并把实测结果写进实施发现段 |
| 15 | **L1 纪律不可绕过**：样本外净额回测的验收卡结论为 `pass` 之前不得放大实盘资金；`insufficient` 时只能延长回测区间，不得下调 `sampleFloor`；本轮新增的两道闸**不得**被用作「反正有闸兜着」的放大理由 |

### 6.4 上线后的观察窗口（runbook）

- **L0（1 天，模拟盘）**：判据用 **`opensCounted` 递增 + `tokens` 随时间回补**（[P2-8]：v1 用
  「`budgetVetoes` 非零」当作「闸在跑」的判据不成立——一个健康的 `trend_hold` 会话本就可能一次
  都不撞闸，把「没撞墙」读成「没装上」会引出一次没必要的回滚）。同时确认 `backstop.basis` 稳定为
  `exchange`、`coverageRatio` **不是长期 `null`**（长期 `null` 说明 [P0-2] 的 `sz` 不可得分支
  恒命中，此时覆盖率一栏应如实显示「未知」而非绿勾）。若 `basis` 长期是 `unavailable(error)`，
  先查凭证与端点，**不要**先关闭轴 B——那正是它要暴露的问题。
- **L0 必查的第二件事（[P0-1]）**：在模拟盘上人为把令牌打空，再让模型提议一次反手，确认
  **平仓腿真的成交了**。这条不变式只在真实事件序列上才算被验证过，用例只是它的下界。
- **L1（≥2 周样本外净额回测 + 小额实盘）**：对比开 / 关 `ai_trade_horizon_turnover_enabled`
  两臂的：平均持仓时间、每日换手次数（墙钟）、手续费占毛利润、资金费后净收益、退出来源分布。
  **判据是第 1、4、5 行**（第 3 行因 §2.2.6 已接近恒真）。
- **重点观察一个反向风险**：`budgetVetoes` 极高（例如 > 每日往返数的 3 倍）说明模型持续想换手
  而被制度压着——这时正确的动作是**换一个更短的周期模式**，而不是调大预算。调大预算等于把
  用户自己选的周期契约悄悄改掉。
- **最坏离场延迟核算**：G4/G5 **不延长任何一笔已有持仓**——按 [P0-1] 修订后的判定式，从空仓
  开仓被拦不涉及任何持仓，反手被拦只否掉再入场腿、平仓腿照常执行，同向加仓恒直通。故第 1 轮
  §6.4 的最坏离场延迟核算表**保持不变**，无需重算。（v1 的判定式**不满足**这一条，这正是 P0-1。）

---

## 七、风险与缓解

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R-1 | 两道开仓闸叠加既有的 `profit_cooldown` / `drawdown_brake` / `edge_gate`，把系统压成**永不开单** | 直接复发 `hold-inertia` 事故 | ① 令牌桶初值满桶；② `min_capacity=2` 安全阀（§2.2.1）；③ 每一次拦截都在 prompt 与 UI 里给出**剩余时间**；④ `budgetVetoes` 进 runbook 的重点观察项；⑤ 两个总闸可一键回退 |
| R-2 | 换手预算把验收卡第 3 行变成恒真判据 | 一个恒真的门槛比没有门槛更危险 | §2.2.6 的三段处置（字段 + 自认文案 + 冻结用例）；runbook 明确以第 1、4、5 行为准 |
| R-3 | 新增一条私有 GET 增加了网络面与被限频的可能 | 触发 OKX 限频会连累既有取数 | ① 仅持仓时查；② TTL 120s + 单飞；③ 硬超时 6s；④ 异常全吞且**不污染** `_reconcile_fail_streak`；⑤ 与既有 `fetch_live_open_orders` 走同一端点，限频行为已知 |
| R-4 | `orders-algo-pending` 的真实字段名 / 随单 OCO 的可见性与假设不符 | 覆盖率恒 0 ⇒ G6 误拦加仓、文案误报裸露 | ① `okx/trading.py:661-666` 已明确记载「随单 `attachAlgoOrds` 在**挂单成交前**不出现在 orders-algo-pending」——实现前**必须**用一笔真实/模拟持仓打一发核对成交后的可见性与键名，结果写进实施发现段；② 不符即整层如实降级为 `unavailable`，**绝不硬编猜测映射**；③ G6 默认可单独关闭（`ai_trade_backstop_gate_scale_in`） |
| R-5 | `TurnoverState` 被误挂进 `HorizonState` | 令牌桶每次平仓被清空 ⇒ G5 静默失效且不报错 | 纯核心 docstring 顶部写死该约束；S-1 用例跨两次往返断言 `tokens` 单调消耗 |
| R-6 | 回测与实盘的冷却粒度差（按根 vs 按秒） | 用户拿回测结论为实盘 G4 背书 | §2.4.4 的免责**点名具体模式**（`hourly_swing` 的 90s < 一根 5m ⇒ 回测恒不生效），notes 与 UI 双处显示 |
| R-7 | 弹窗多两行、卡片多一张 ⇒ 信息过载 | 「顶级设计」的反面 | 契约摘要两行是**一句话人话**、不展开参数；新卡片只有三行且默认只在有持仓时显示；chip 按 §2.4.3 的优先级**只显示最紧要的一格**，正常态不占位 |
| R-8 | 第 2 轮变更尚未提交，本轮在其之上叠加 | 两轮混在同一工作树，提交时切不干净 | §1.4 的开工前置检查；Subtask#3 必须按 hunk 精确暂存（本仓库已有成文做法：`git hash-object -w --path` 直造 blob + `update-index --cacheinfo`，或按行号切 patch 后 `git apply --cached --recount`，**patch 文件必须以 bytes 读写**，`write_text` 在 Windows 会翻 CRLF 弄坏 patch） |
| **R-9** | **反手降级为 `close`（[P0-1] 的修法）本身改变了系统行为**：一个「想反手」的信号变成「先平掉」 | 模型的方向观点被丢弃一轮 | ① 它是**单向更保守**的（flat 是三种持仓态里风险最低的一种）；② prompt 明确告诉模型这条规则（§2.2.7），模型可据此选择先 `close` 再等令牌；③ 降级码后缀 `reverse→close` 使其在轨迹里可数、可事后核对；④ 退出来源分布的偏移已在 §2.4.4 免责与 [P2-5] 登记 |
| **R-10** | **`closeFraction` 是 OKX 的字符串字段，取值未必只有 `"1"`**（部分平仓可能是 `"0.5"`） | 覆盖率被高估为全仓 | 只在**逐字等于 `"1"`** 时判全仓；其余可解析且落在 `(0,1)` 的按该比例计；不可解析 ⇒ 落 `None`（B-12 那条路径）。**绝不把不认识的取值当成 1** |
| **R-11** | 本轮把 `_normalize_algo_order` 加了一个键，而它同时服务于**非 AI** 的委托页签 / 图表虚线 / 持仓子行 | 加键影响面外溢 | 纯加法 + 既有键逐键相等用例（B-16）；不改 `fetch_live_open_orders` 的任何既有分支 |

---

## 八、实施顺序（给下游代码开发节点）

1. **前置核对**（§1.4 的警示）：确认第 2 轮的文件在场、相关用例为绿；跑一次干净 HEAD worktree
   的红灯基线并记录（放行条件 14）。
2. **`turnover_discipline.py` 纯核心 + T 组用例**（先测后码，四模式落地值表是第一条断言）。
3. **单一真源改造**：`horizon_acceptance.resolve_thresholds` 改调 `derive_turnover_per_day`
   （**两个值一次取回**，[P1-4]），跑既有验收卡全部用例证明**零行为变化**（这一步必须独立成一次
   可回滚的改动）。
4. **`HorizonRule` 四字段 + `resolve_horizon_rule` 回填 + `rule_wire` / `profile_wire` 镜像**。
5. **`HorizonRuntime` 接线**（`note_open` / `note_close` / `turnover_gate` / `stats`）+ S-1、S-2。
6. **决策链插入**（实盘 + 回测同位）+ S-3、S-7、G-3 两侧 reason 逐字相等。
7. **R-4 的真实键名核对**（打一发 `orders-algo-pending`，结果写进实施发现段）——**在写
   `exchange_backstop.py` 的分类逻辑之前**做，不符即调整形状。
8. **`exchange_backstop.py` + B 组用例 + 盯市 tick 调度**（S-11、S-12）。
9. **文案层**（`hold_horizon_text` 四个分支 + 宪章联动 + 回测 notes）+ 冻结用例（放行条件 8）。
10. **验收卡自认**（§2.2.6）+ S-9。
11. **前端**：类型五处 → helper → 卡片 → 弹窗两行 → chip → 回测面板；每步各自的用例。
12. **全量回归**：后端 `python -u -m pytest` 分块跑（**全量并发会共用临时 DB 导致挂住**，见既有
    纪律）+ 前端 `NODE_ENV=test` 跑 vitest；与第 1 步的红灯基线逐条对账。

---

## 九、有意偏离与拍板记录

| # | 偏离 | 拍板理由 |
|---|---|---|
| D-1 | G5 的速率**不给独立配置项**，恒等于验收卡第 3 行阈值 | 多一个旋钮就多一次「弹窗承诺 6 次、运行期执行 12 次」的机会。本仓库已为「同一口径两处各存一份」出过两次事故 |
| D-2 | 令牌桶容量下限 **2** | 单纯的 `per_day` 容量会让 `trend_hold` 在一次早期止损后冻结 22 小时。容量只影响突发、不影响长期速率，代价上限是「多一次往返」，收益是消除一整类「永不开单」投诉 |
| D-3 | G4 的作用域默认 `same_side` 而非 `both` | 反向开仓是结构翻转的合法表达；拦掉它会把换手纪律变成方向禁令（`profit_cooldown` 的同款取舍） |
| D-4 | 预算单位是**往返（新 epoch）**而非「成交笔数」 | 与验收卡分子 `roundTrips` 严格同单位；换单位就对不上弹窗里承诺的那个数 |
| D-5 | 轴 B **不在回测装配** | 回测没有交易所。让回测显示一个「已核实」的绿勾，是本轮要根除的那类谎言的镜像 |
| D-6 | `backstop` **不落库** | 一个 3 天前的「已核实」在历史页上只会骗人；历史页整块不渲染 |
| D-7 | 覆盖率**只认止损腿** | 兜底的定义是「最坏情况下有人替你退出」。止盈腿不构成兜底 |
| D-8 | G6 只拦「同向加仓」这一格 | 已经裸露的部分拦不拦都已裸着；系统唯一还能决定的是要不要让它变大。范围越窄，误伤越少 |
| D-9 | 本轮**不修**上一轮登记的欠账 D-8（回测成本估算不含资金费） | 修它会当场改变 G3 迟滞与 edge 门禁在回测里的判定，与本轮「不动任何既有门禁判定式」的边界正面冲突。继续登记为欠账（见 §十） |
| D-10 | 新 wire 作为**周期快照的嵌套键**而非顶层键（键名是 `horizon.turnover` / `horizon.backstop`，[P1-1] 修正） | 嵌套即可搭既有的 payload / decision / status / trace 四条载体，不必动 `store.ts` 的逐键手抄 reducer（`edgeSource` 那次的教训） |
| **D-11** | **被换手闸拦下的反手降级为 `close`，而不是 `hold`、也不是整类放行**（[P0-1]） | 三选一里只有它同时守住两条：**退出永远能执行**（本模块的第一不变式）与**再入场受预算约束**（本轮目标）。整类放行会让「反手 → 反手」成为绕过预算的等价通道，缺口 #1 只堵一半；降级为 `hold` 则把持仓生杀权交给了一个统计口径，那是 v1 的阻断级缺陷 |
| **D-12** | **同向加仓完全不归 G4/G5 管**（[P0-1]） | 加仓不建新 epoch、不消耗令牌。一个不消耗预算的动作被预算拦住，用户无法从任何一个数上推出原因。加仓的安全性由 G6（兜底覆盖）负责，职责不重叠 |
| **D-13** | **令牌桶不跨会话持久化，重启即回满；但必须逐处自认是「本会话」口径**（[P1-5]） | 跨会话持久化要落库、要定义「同一契约」的身份（换标的？换预算？换模式？），属于另一轮。而本轮的全部论点建立在「不许说没核实过的话」上，故宁可承认口径弱，也不许弹窗里写一句做不到的「每日」 |
| **D-14** | **「覆盖率算不出」是第三态，且一律 fail-open**（[P0-2]） | 与 `hold_horizon._is_bad_px`（「算不出」绝不能变成「不许平」）逐字同极性。把「不知道」渲染成红色的确定坏消息，比原来那句没核实的话更坏——它会稳定地误导模型收紧退出计划 |

---

## 十、非目标（本轮明确不做）

- **不做交易所侧兜底的自动补挂 / 重挂**（§2.3.5 三条理由）。这是本轮之后**最该做的下一件事**，
  且已具备前提：本轮建成的检测层就是它的触发器。
- 不做运行中动态改周期模式 / 改预算（沿用第 1 轮结论：改模式 = 停会话重开）。
- 不做「验收未通过即自动停止会话」的自动干预（沿用第 2 轮结论）。
- 不做按标的分别设置周期模式（分组共享一份契约）。
- 不修上一轮欠账 D-8（回测成本估算不含资金费）与 D-9（`cost_model` 在结算周期未知时按 8h 保守
  估算）——两者都必须与 G3 阈值重估一起做。
- 不做 `session.py` / `attribution.py` 的拆分（既有行数债，本轮同样用硬预算压住，不新增）。
- 不改 `live_auto`（非 AI）链路的任何行为。

---

## 附：本轮与前两轮的关系（给评审节点的一页速览）

| | 第 1 轮 `holding-horizon-modes` | 第 2 轮 `horizon-acceptance-loop` | **本轮** |
|---|---|---|---|
| 解决什么 | 持得太短（没有任何一层约束主动平仓） | 指标不真（资金费缺、退出来源缺、判据恒真） | 开得太快（三道闸只管平不管开）+ 兜底是句没核实的话 |
| 手段 | G1/G2/G3 三道**平仓侧**确定性闸 | 三本账 + 五行验收卡 | G4/G5 两道**开仓侧**闸 + G6 覆盖闸 + 三态 `basis` |
| 调查约束覆盖 | #1 #2 #3 #4 #6 #7（估算） | #7（真值） | **#5（换手迟滞的另一半）#8（交易所侧持久化兜底）** |
| 状态 | 已合入 `11246ae` | 已实现、**未提交** | 本文档 |

---

## 评审结论

**有条件通过（approved with conditions）。**

方向、切法与规模都对：本轮补的是第 1 轮的另一半（不许过快重开），复用的是同一份契约派生、同一个
纯核心 + 运行时 + 文案三段式、同一套 fail-open 姿态，不新增下单路径、不新增 DB 列、不动既有门禁
判定式。18 条依赖考证**逐条属实**，四个预设的派生值表与源码逐格相符，缺口 #1 的算术（12 次/日 vs
自定的 6 次/日门槛）成立。轴 B 更是把本仓库一条真实的安全债（一句从未核实、却被用来授权模型
「不必盯盘」的话）摆到了台面上——这件事本身就值得做。

v1 的四条 P0 有一个共同形状，值得下游记住：**新加的判定式，判据取自一个「看起来对但实际不携带
该信息」的入参**——`_GATED_ACTIONS` 不看持仓方向（于是拦下了含平仓腿的反手）、`trade["side"]`
在平仓时恒为 `"close"`（于是冷却方向恒不匹配）、算法委托的 `sz` 常为 0（于是覆盖率恒 0）、
prompt 的 flat 分支早退（于是唯一生效的状态里模型被告知「不受限」）。四条都**不会报错**，
只会安静地要么空转、要么误伤。P0/P1 已全部在正文中修复并配了守门用例。

### 放行条件（合入前逐条可核对）

1. §6.3 的全部 15+4 条放行条件（含新增的 8b / 8c / 8d / 12b / 12c）为绿。
2. **[P0-1]** 安全不变式落到用例与模拟盘两处：T-14 / T-15 / T-16 + S-14 + §6.4 的 L0 人工验证；
   「本轮新增的任何一道闸都不得让某笔已有持仓比不装本轮时更晚离场」写进 `turnover_discipline`
   的模块 docstring 顶部。
3. **[P0-2]** §八 第 7 步（真实 `orders-algo-pending` 打一发）**先于** `classify_backstop` 的实现
   完成，四个字段的实测取值写进实施发现段；若实测与假设不符，按 §2.3.2a 第 4 条如实降级，
   **不得硬编猜测映射**。
4. **[P0-3]** 空仓分支的冻结用例双向都要有：两闸空转 ⇒ 逐字节等于 HEAD；闸激活 ⇒ 不含
   「不约束开仓」。
5. **[P0-4]** `note_close` 里「先取 `epoch[0]` 再结算」的顺序依赖，同时出现在 docstring、
   用例名与 §2.2.3 三处——这是本轮唯一一处纯核心之外的调用顺序依赖，必须刺眼。
6. **[P1-1]/[P1-2]** 前端接线以 `horizon.turnover` / `horizon.backstop` 为准，卡片按成员渲染；
   合入前在真实运行的会话上**肉眼确认**卡片有数（后端算了 ≠ 用户看得见，本仓库已为此栽过四次）。
7. **[P1-5]** 「本会话口径」的措辞在弹窗、卡片、验收卡自认三处**逐字一致**。
8. `session.py` 硬预算 ≤ +18 行；超支即在文末登记欠账并说明，不得默默超支。
9. 全量回归与**干净 HEAD worktree 实测**的红灯基线逐条对账（不要靠记忆），结果写进实施发现段。
10. P2 的 8 条：P2-1 / P2-3 / P2-4 / P2-8 已改入正文，P2-2 已写进 §2.4.3；**P2-5 / P2-6 / P2-7
    留给实现节点自行判断**，若不采纳需在实施发现段写一句理由。

### 明确不阻塞合入的两件事

- `trend_hold` 在两次快速离场后可能等待约 20 小时才能再开仓（D-2 的代价）：已在弹窗与 runbook
  披露，属**契约本身的语义**，不是缺陷。
- 令牌桶不跨会话持久化（D-13）：本轮以「说清楚」代替「做到」，是正确的取舍顺序。

---

## 十一、实施过程发现的方案缺陷

> 本节由实现节点在落地过程中追加。按 spec §Constraints 的要求：**发现方案有误不得静默偏离**，
> 一律先记在这里，再按修正后的做法实现。以下每条都给出「spec 原文怎么说 / 实际是什么 / 落地怎么做」。

### I-1（阻断级，spec 内部自相矛盾）§2.2.7 的空仓那一句：既要改掉又要逐字节不变

- **spec 怎么说**：§2.2.7 一方面写「原句里『不约束开仓』四个字**必须改掉**——它现在是假的」，
  另一方面写「两个闸都未激活时 `_turnover_line` 返回空串，拼接后**与今天逐字节相同**（冻结用例
  守）」；放行条件 8b 同样要求「空仓 + 两闸空转 ⇒ 与 HEAD 逐字节相同」。
- **实际**：这两条不可兼得。只要把那句话改掉，任何「两闸此刻未激活」的空仓轮就**不可能**再与
  HEAD 逐字节相同。
- **落地取舍**：措辞按**本层是否装配**分支，而不是按「两道闸此刻是否激活」分支
  （`hold_horizon_text._flat_line`）：
  - 未装配（总闸关 / `custom` 全默认 ⇒ `horizon.turnover` **整键缺席**）⇒ 逐字返回今天那句，
    G-1 冻结门禁与放行条件 8b 前半成立；
  - 已装配 ⇒ 绝不再出现「不约束开仓」四个字，放行条件 8b 后半成立。
- **为什么不按「此刻是否激活」分支**：那样在「装配了、但此刻令牌恰好是满的」这一轮里，系统仍会
  对模型说「本层不约束开仓」——而**下一轮它就可能拦**。那是一句随时会变成假话的承诺，正是本轮
  要根除的东西。守门用例：`test_ai_trade_turnover_session.py` 的两条 S-13。

### I-2（阻断级）§2.3.2a 的「算不出即 None」若照字面实现，会让 `mismatch` 永远无法出现

- **spec 怎么说**：§2.3.2a 第 1 条「`sz` 与 `closeFraction` 两者都不可用 ⇒ `coverageRatio = None`」。
- **实际**：照字面实现时，「交易所侧确认一张止损都没有」（`stops == []`）会走进同一个分支——
  空列表当然既没有 `sz` 也没有 `closeFraction`——于是 `coverageRatio` 恒为 `None`、
  `mismatch` 恒不成立。**而 `mismatch` 正是本模块存在的全部理由**（§2.3.2a 原话：「这一格是本轮
  存在的全部理由」）。这与 §2.3.2a 自己给的 B-2 / B-4 两条用例（「只有止盈腿 ⇒ `coverageRatio == 0`」、
  「本地有价 + 交易所覆盖 0 ⇒ `mismatch is True`」）直接冲突。
- **落地做法**：`_covered_size` 对**空列表**返回 `0.0`（确定的结论），只有「有止损腿、但它们的
  张数与 `closeFraction` 都不可解析」才返回 `None`（真正的「不知道」）。三态语义因此是：
  `0` = 核实过、确认裸露；`None` = 核实过、但交易所没回报可核对的张数；有值 = 实测覆盖率。
- **发现方式**：B-2 / B-4 两条用例**先写、先红**，据此改的实现（而不是反过来把用例改绿）。

### I-3（阻断级）`reduceOnly` 作为「充分条件」必须先限定到**本仓**

- **spec 怎么说**：§2.3.2a「`reduceOnly` 只作为**充分条件之一**，绝不作为必要条件」。
- **实际**：这条只讲了「不能漏算」，没讲「不能多算」。照字面把 `reduceOnly` 放在最前面短路，
  双向持仓（`long_short_mode`）下**另一侧持仓**的止损委托（`posSide == "short"` 而当前持多）
  会因为带着 `reduceOnly` 而被算成本仓的兜底。那比「没有兜底」更危险：它会让一个真正裸露的多头
  显示成「已核实 · 覆盖 100%」。
- **落地做法**：`_is_closing_leg` **先判 `posSide` 归属、再判方向**——`posSide` 是 `long`/`short`
  且与当前持仓方向不符 ⇒ 直接不计入；只有 `net`（单向持仓）模式下 `reduceOnly` 才作为充分条件
  （此时它已被交易所限定为本仓）。守门用例：B-3 / B-13。

### I-4（欠账登记，非缺陷）放行条件 8d 的真实 `orders-algo-pending` 实测**未执行**

- **spec 怎么说**：§八 第 7 步与放行条件 8d 要求「先打一发真实 `orders-algo-pending`，把 `sz` /
  `closeFraction` / `reduceOnly` / `posSide` 四字段的实测取值写进本段；**未做此步不得合入轴 B**」。
- **实际**：本节点在离线环境下实现，**没有被授权、也不具备条件**向用户的真实 / 模拟 OKX 账户发起
  私有签名请求。凭空编一组「实测值」写进本段，恰恰是本轮要根除的那类谎言的镜像，故不做。
- **落地状态**：轴 B 的代码已完成且默认开启，但**放行条件 8d 仍未闭合**。合入前必须由人执行：
  持有一笔真实 / 模拟持仓 → 打一发 `GET /api/v5/trade/orders-algo-pending?ordType=oco` →
  把四个字段的真实取值补写到本段 → 若与假设不符，按 §2.3.2a 第 4 条如实降级，**不得硬编猜测映射**。
- **为什么可以先写实现再补实测**：本实现对四个字段**没有任何一处硬编码假设**——`sz` 不可用走
  `closeFraction`、两者都不可用落 `None` 且一律 fail-open、`reduceOnly` 只作充分条件、
  `posSide` 三种取值各有分支。实测的作用是**确认哪条分支在跑**，而不是让代码能跑。
  L0 runbook 已写明判据：`coverageRatio` 长期为 `null` 即说明 `sz` 不可得分支恒命中。

### I-5（口径澄清）前端类型「改五处」实际只需改**三处**

- **spec 怎么说**：放行条件 12「前端类型改**五处**（`aiTradeTypes` / `aiTradeHistoryTypes` /
  `aiBacktestTypes` + 两处组件 props）」。
- **实际**：`aiTradeHistoryTypes.ts:119` 与 `aiBacktestTypes.ts:80` 都是**直接复用**
  `AiTradeHorizonStats` 这一个 interface（`horizon?: AiTradeHorizonStats`），不各存一份字段表。
  故三个新计数只需加在 `aiTradeTypes.AiTradeHorizonStats` 上，历史页与回测页自动读得到。
- **落地做法**：按实际的单一真源改（`aiTradeTypes` 一处 + 两处组件读点），并保留放行条件 12 的
  **实质**——历史页与回测页都有展示落点（`AiTradeStatsDetail` 的 `turnoverVetoLine` 行、
  `panels.tsx` 的 `TurnoverBacktestCard`），用例与 tsc 双守。这比机械改五处更符合本轮
  「同一口径只有一处」的纪律。

### I-6（有意偏离）`classify_backstop` 的形参数超 CLAUDE.md 的「≤5」

- `classify_backstop` 有 9 个形参。CLAUDE.md 规定「函数形参不超过 5 个」，但 spec §4.5 明写
  「内部函数签名（**下游按此实现，不再二次决策**）」并逐字给出了该签名。
- **取舍**：遵循 spec 的钉死签名（改成聚合结构体会与 §4.5 对不上，而 §4.5 的存在理由正是防止
  下游各自发挥）。本条登记为**已知偏离**，若后续按 P2-6 把 `exchange_backstop.py` 三分，
  该函数的入参聚合可以一并做掉。

### P2-5 / P2-6 / P2-7 的处置（放行条件 10 要求逐条表态）

- **P2-5（反手降级改变退出来源分布）：已采纳。** 免责写进了 `_turnover_backtest_notes` 的第三句、
  回测面板 `TURNOVER_BACKTEST_CAVEATS` 的第三条，并在 `apply_turnover_gate` 的 docstring 里点名。
- **P2-6（`exchange_backstop.py` 按 cost_model/cost_runtime/cost_text 三分）：暂不采纳。**
  文案层本就已在 `hold_horizon_text`，强行三分会为了对称而对称。
  **[终审 RV-3 更正]** 本条原写「该文件落地后 ~330 行，未达 400 行触发线」——实测是
  **437 行原始行 / 218 行代码行**（其余为 65 空行 + 14 注释行 + 140 行 docstring）。原始行口径下
  400 行触发线**已越过**，故该数字与结论在合入时逐条重记如下：文件的硬阈是 CLAUDE.md 的
  **800 行**（437 行远在其内），P2-6 的 400 行是本节点自定的一条更紧的建议线，其立意是「代码密度
  上来了就该拆」——按**代码行 218** 衡量并未达到。故维持不拆，并把触发条件重钉为
  **「代码行越过 400」或「原始行越过 800」，两者任一即按 `cost_model` / `cost_runtime` / `cost_text`
  三分法拆开**。写下这条更正本身就是本轮的纪律：一个没核对过的行数与一句没核实过的兜底承诺是同
  一类错误。
- **P2-7（新计数不得并入第 1 轮的冻结计数）：已采纳。** 三个新计数全部走 `TurnoverState` 自己的
  字段，`HorizonState.blocked_streak` 与 `HorizonRuntime.vetoes` 一个字符未动；
  `turnover_discipline.note_veto` 的 docstring 与 `horizon_runtime.stats()` 的注释各写了一次理由。

### 行数预算对账（放行条件 4 / 8）

| 文件 | 预算 | 实测净增 | 结论 |
|---|---|---|---|
| `ai_auto/session.py` | **≤ +18（硬）** | **+17**（+21 / −4） | 在预算内 |
| `ai_auto/backtest.py` | ≤ +14 | +14 | 恰在预算内 |
| `okx/trading.py` | ≤ +2 | +1（`closeFraction` 一个键） | 在预算内 |

> 测法：与干净 HEAD worktree（`11246ae`）逐行 diff 后，**扣除第 2 轮尚未提交的那部分**（session.py
> 对 HEAD 的总 diff 是 +43/−4，其中 −4 全部是本轮的两处改写）。

### 红灯基线对账（放行条件 14 / 9 —— **干净 HEAD worktree 实测**，不靠记忆）

测法：`git worktree add <tmp> HEAD --detach`（`11246ae`）后在该干净树里跑一次全量
`python -u -m pytest tests/ -q -p no:randomly`，再在本工作树跑同一条命令，逐条对账。
**两次全量不可并发**：首次并发跑时工作树那一次挂住不产出（本仓库既有纪律，此处再次实证）。

| 用例 | 干净 HEAD | 本工作树 | 判定 |
|---|---|---|---|
| 合计 | **8 failed / 2709 passed / 1 skipped**（369.9s） | **15 failed / 2913 passed / 1 skipped**（401.5s） | —— |
| `test_ai_trade_prior_session.py`（7 条） | 红 | 红 | **既有基线**，与本轮无关 |
| `test_factor_library_expansion.py`（7 条） | 不存在（该文件未纳入 git，HEAD 树里没有） | 红 | **孤儿文件**，与本轮无关 |
| `test_ai_backtest_manager.py::test_profit_rule_is_forwarded_to_spec` | 红（单跑仍红：「未配置 AI 模型」） | **绿** | 被**第 2 轮尚未提交**的改动修好，与本轮无关 |
| `test_agent_manager_detach.py::test_resume_after_completion_acks_false_completed_without_replay` | 绿 | 全量红 / **单跑绿** | **顺序 flake**（与既有 `test_agent_ws.py` 同一族），非回归 |

**结论：本轮新增 0 条红灯。** 新增用例 91 条全绿（T 组 43 / B 组 27 / S 组 21）。
前端：`NODE_ENV=test` 全量 **145 文件 / 1582 用例全绿**；`tsc --noEmit` 除既有
`WeightBatchArena.test.tsx`（本轮之前即存在，与本特性无关）外**零错误**。

### 变更计划与实际落点的两处出入（§三 对账）

- **`api/ai_auto.py`：实际改动 0 行**（§3.2 预算 ≤ +2）。该端点本就直接返回 `profiles_wire(...)`
  且不做任何字段裁剪，三个预览字段与三个运维标志只要在 `hold_horizon.profiles_wire` 产出、并在
  `schemas.AiTradeHorizonProfileOut` / `AiTradeHorizonProfilesOut` 声明即可透传（response_model
  会丢弃**未声明**的键，故 schema 那两处是必需的、api 那处不是）。已实测回执含全部六个新键。
- **`ai_auto/manager.py`：新增 6 行（§三 未列该文件）**。`format_charter` 的 `backstop_armed`
  必须由**装配处**传入——跨日那句「即使本进程退出它们依然生效，故你不必每轮盯盘」是模型放松盯盘
  的直接授权来源，它只有在**真的有核实通道**（总闸开 **且** `creds is not None`）时才成立。
  这一实参只有 `manager._build_member` 拿得到，`hold_horizon_text` 自己判不了。回测侧走默认
  `False`（回测没有交易所），故 `backtest.py` 的调用点一字未动。

---

## 十二、终审记录（合入前的最后一道复核）

> 本节由终审节点在 review + 提交时追加。**只记真的改了源码的那几条**，以及一条被明确判定为
> 「不改」的取舍——没有落到代码上的评审意见等于没有发生。

### RV-1（真缺陷，已修）弹窗把长周期契约的等待上限**夸大了一倍**

- **现象**：`aiTradeHorizon.turnoverContractLines` 的代价披露句按
  `Math.round((capacity / perDay) * 24)` 算等待上限，`trend_hold`（速率 1 次/日、容量 2）因此写出
  「最长可能需要等待约 **48 小时**才能再次开仓」，`short_swing`（速率 2、容量 2）写出 24 小时。
- **为什么是错的**：`apply_turnover_gate` 的拦截判据是 `tokens < 1`——**再次开仓只需要一枚令牌**，
  故等待上限是补一枚的时间 `24 / perDay` 小时（trend_hold 24h、short_swing 12h）；
  `capacity / perDay × 24` 算的是把**整桶补满**的时间，与「能不能开仓」无关。spec §九 D-2 与
  「明确不阻塞合入的两件事」给的口径也都是「约 20 小时」（24h 减去持仓期间已流逝的那段）。
- **为什么必须在合入前改**：本轮的全部论点建立在「不许说没核实过的话」上，而这是**唯一一个直接
  写在启动弹窗里、用户选模式那一刻就会读到的数**。把约束夸大一倍会让用户误判周期契约的代价，
  正是本轮要根除的那类失真的镜像。
- **落点**：`apps/web/lib/aiTradeHorizon.ts` 改判定式与措辞（「最长需等约 N 小时**补回一枚令牌**
  才能再次开仓」，把语义写进句子而不是留给读者推）；
  `aiTradeHorizon.test.ts` 的守门用例从断言 48h 改为断言 24h，并**新增 `short_swing`（速率 2、
  容量同为 2）⇒ 12h 一格**——两格容量相同而等待减半，正是「等待时间只由速率决定、与容量无关」
  这条判据；照旧式算法在这一格同样会得出 24h，故它是这条缺陷的精确回归护栏。

### RV-4（真缺陷，已修）第 1 轮的 G-10 冻结门禁在**任何一次干净检出**上都是红的

- **现象**：`aiTradeHorizon.test.ts` 的「实现文件不得硬编码模式预设数值」用例，单独跑绿、全量跑红
  （`expected … not to contain '14400'`）。
- **根因**：本仓库 `core.autocrlf=true` ⇒ **检出到工作树的文件是 CRLF**（作者机上看到 LF 只因这些
  文件是就地写出来的、还没被 git 重写过）。该用例先用 `/…[\s\S]*?
};
/` 把周期换算表
  `HORIZON_BAR_SECONDS` 剔掉再扫描，CRLF 下这条正则**一个字符都匹配不到** ⇒ 剔除静默失效 ⇒
  换算表里的 `14400` / `43200` 被当成硬编码预设。本次是 `git stash --keep-index`（终审为了在
  **精确的暂存内容**上跑一遍回归）顺手把工作树重写成了 CRLF，才第一次暴露出来。
- **为什么必须修**：这道门禁守的是「前端不许手抄后端的预设表」这条真纪律，而它在每一台正常检出的
  机器上都因为一个与该纪律无关的理由变红——一条恒红的门禁与一条恒绿的门禁一样没有价值。
- **附带发现**：同一用例的 `expect(source).not.toContain("HORIZON_BAR_SECONDS =")` 是一条**恒真**
  断言（声明写的是 `HORIZON_BAR_SECONDS: Record<string, number> =`，永远不含 `SECONDS =`），
  故它过去从不能证明「剔除真的发生了」。已一并改为断言声明头 `HORIZON_BAR_SECONDS: Record`。
- **落点**：`apps/web/lib/aiTradeHorizon.test.ts`（正则改 `?
`、断言换判据、两处各留一句理由）。
  修后全量前端 **145 文件 / 1582 用例全绿**（此前是 1 红），`tsc --noEmit` 仍只剩既有的
  `WeightBatchArena.test.tsx` 一条。

### RV-2（记录更正）`exchange_backstop.py` 的行数

§十一 P2-6 记的「~330 行」与实测不符（437 原始行 / 218 代码行），触发线的口径也未写清。已在该
条内逐条更正并重钉触发条件，见上。

### RV-3（复核通过，不改）三处曾被怀疑、核对后确认无误

- **反手降级为 `close` 之后退出腿真的会成交**：`decision_to_signals` 对 `close` 产出
  `Signal(side="close")`（`signals.py:288-289`），`size_fraction=None` 在平仓腿上本就不消费；且
  换手闸排在周期闸**之后**——反手能走到这里说明 G1/G2/G3 已经放行了那条平仓腿，降级为纯平仓
  严格不比它更宽松。放行条件 6 的安全不变式成立。
- **`turnover` 快照在空仓轮确实在场**：`HorizonRuntime.refresh` 的 `flat` 分支同样走
  `self._snapshot(facts)`（而不是直接 `build_snapshot`），故 [P0-3] 的前提没有在实现里被绕过。
- **`schedule_from_executor` 读的 `executor.entry_qty_sz` 与 `exchange_oco_px` 均确为既有属性**
  （`live_auto/executor.py:108`、`profit_guard_runtime.py:74`）——覆盖率分母不是一个恒为 0 的
  幻影字段，L0 runbook 的「`coverageRatio` 长期 null 即说明 `sz` 不可得分支恒命中」这条判据可用。

### 合入时的口径澄清（不是缺陷，供后续节点读）

本次提交**同时包含第 2 轮 `ai-trade-horizon-acceptance-loop` 的全量变更**：§1.4 已记载它「已实现
但尚未提交」，而本轮的后端 import、决策链插入点、验收卡自认与前端类型全部建立在其上——只提交本轮
会得到一个 `ImportError` 即挂的 HEAD。故两轮作为同一个「交易周期模式」特性一次合入；
`db/live_auto_repositories.py`、`test_factor_library_expansion.py`、`desktop/scripts/*` 与
`ai_trade_enabled` 总闸翻转（`config.py` 顶部 / `desktop/src/lib/config.js` / `test_config.py` 的两条
新用例）四组**不属于本特性**的在制变更已按 §七 R-8 逐 hunk 剔除，留在工作树里。
