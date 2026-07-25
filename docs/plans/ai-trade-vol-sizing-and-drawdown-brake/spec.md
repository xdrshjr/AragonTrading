# AI 自动交易 · 波动率目标化仓位 + ATR 自适应止损 + 组合相关性预算 + 回撤刹车

> **版本**：**v2**（已评审；v1 的全部 P0 / P1 已在正文内修正，见 §〇 评审记录与 §十三 评审结论）
> **归属**：`docs/improve/01-ai-autotrading` 路线图 **阶段二 · 第二步（拆分后的 2a）**
> **对应根因**：D-6（仓位与风控都是固定量）、D-12（多标的无相关性约束）、D-13（无回撤刹车）
> **上游依赖**：阶段一（commit `96f554d`，成本记账 + 最小 edge 门禁）、阶段二第一步（commit `c6db95d`，信号先验 + EV 分子）
> **前序设计**：[`ai-trade-cost-accounting-and-edge-gate/spec.md`](../ai-trade-cost-accounting-and-edge-gate/spec.md)、[`ai-trade-signal-prior-and-ev-gate/spec.md`](../ai-trade-signal-prior-and-ev-gate/spec.md)

---

## 〇、评审记录（v2）

> 评审方式：**逐条回源核验**。v1 引用的每一处源码坐标都对着当前工作树复核过一遍——除 §6.2 的
> `manager.py` 行数（651 → 实际 **650**）外**全部准确**，这一点值得肯定：v1 不存在「引用漂移」类问题。
> 因此下面 3 个 P0 全部是**推理层缺陷**：照着 v1 逐字实现，代码能跑、测试能绿，但功能静默失效或数据静默错误。
> 这正是本仓库前两轮评审定下的 P0 判据（「follow it literally and it breaks while tests stay green」）。

### P0（阻断，必须在实现前修正；已在正文修正）

| # | 问题 | 证据 | 正文修正处 |
|---|---|---|---|
| **P0-1** | **`apply_risk_override` 的两条正确性论证与代码不符，会把「交易所侧根本不存在的止损价」写进落库成交回执。** v1 §3.4 论证 1 声称「与 `_execute_decision` 在同一个协程内顺序执行，中间无 `await`」——`_execute_decision` 第一件事就是 `async with self._exec_lock`（`session.py:879`），而 5s 盯市循环也在同一把锁上（`session.py:395`），**这是一个真实的让出点**。论证 2 声称「下游读点全部自动一致」——`_adopt_fill`（`executor.py:494`）在**限价挂单成交被采纳时**用**当时**的 `self.risk` 重算 `tp/sl` 并写进 `_make_trade` → `trade["stopLossPrice"]` → `_persist_trade` 落库；而限价委托默认开启，挂单可以跨若干轮才成交，届时 `risk` 早已被后续轮次的 ATR 覆盖。更糟的是 `profit_guard_runtime.exchange_oco_px`（`profit_guard_runtime.py:81-87`）的**首选真源**恰恰就是这条回执——`ai-trade-limit-orders` P1-4 当初引入它，就是为了避免「照 `entry_price` 现算会得到一个交易所侧根本不存在的价」，本设计把同一个 bug 从另一侧原样重新引入。此外：在**持仓存续期间**覆盖 `risk` 会让 `exchange_oco_px` 的 `derived` 回落分支凭空造出一对并不存在的 OCO 价（`ai-trade-profit-protection` 终审 RV-1 的同一条教训）。 | `session.py:879/395`、`executor.py:269/494`、`profit_guard_runtime.py:81-89` | §3.4 全面改写（新增写入前置条件 S-11、`stopSource` 五态、T-17b/T-17c） |
| **P0-2** | **凯利赔率 `b` 的分子取错了估计量，两处系统性偏差叠加后 `f_kelly` 恒为 0，等于每笔都按最小档下单。** v1 §3.3 定义 `b = (edge_bps − cost_bps) / stop_bps` 且 §4.2 明写 `edge_bps` 取 `CostRuntime` 的 `EdgeGateOutcome.edge_bps`。① **语义与总闸③耦合且会重复计价 `p`**：该字段是 `EdgeEstimate.target_bps`，其语义随 `source` 变——`atr`/`take_profit` 时是「若走对能吃到多少」的**条件幅度**（凯利的 `b` 要的正是这个），而 `prior` 时是**已按胜率加权的期望** `p·W−(1−p)·L`（`edge_gate.py:97-99` 自己写明「数量级天然差一档」）。把它当 `b` 再乘一次 `p`，就是对 `p` 收两次税。② **即便总闸③关闭（默认）仍然错**：`ai_trade_edge_atr_target_mult` 默认 **1.0**（`config.py:275`），而本层要挂的止盈是 `stop_rr × stop_atr_mult × ATR = 2.0 × 1.5 × ATR = 3×ATR`——分子比自己实际挂出去的止盈**小 3 倍**，`b < 1` 恒成立，凯利要求 `p > b/(1+b) ≈ 0.4` 才为正。两项叠加 ⇒ `kelly ≤ 0` ⇒ `f_kelly = max(0,·)×λ = 0` ⇒ `f_core = 0` ⇒ 经 `quantize_size_fraction` 的 `max(1,…)` 地板落到**最小一格**。系统不报错、不降级、`kellyStatus` 还是 `"ok"`。 | `edge_gate.py:97-99/113-129`、`config.py:275`、`signals.py:319-334` | §3.3 步骤 3 全面改写（`b` 改由**本层自己要挂的括号**定义，与 §3.4 同源）、§4.2 取数契约改写、新增 S-12/T-11b/T-12b |
| **P0-3** | **回测侧 ATR 止损没有任何写入点，照 v1 实现是彻底空转——与 §3.10 自己警告的 I-5 复发点同款静默。** v1 §3.10 只说「同一批纯函数在语义等价位置插入」并列了三个喂数点，**没有一条说止损距离怎么写进回测**。而 `SimBroker` 的止损价是在 `_open` 时由 `self._risk` **冻结快照**进 `pos`（`simulator.py:269/277-293`），`apply_risk(bar_index, row, _risk)` 的**第三个形参根本没被使用**（`simulator.py:169`，形参名带下划线）。因此实现者最自然的写法「改 `self._oco_risk`」（它正是传给 `apply_risk` 的那个）**一行都不会生效**；必须同时调 `self.main.set_risk(...)`（`simulator.py:123`，该方法已存在且 `backtest.py:257` 已在用）。且 `_oco_risk.take_profit_pct` 还被 edge 门禁当上界读（`backtest.py:534`），两者必须同步更新，否则门禁上界用的是失效常数。 | `simulator.py:123/169/269/277-293`、`backtest.py:251/257/386/534` | §3.10 新增「回测止损写入点」小节 + S-13 + T-19b |

### P1（重要，已在正文修正）

| # | 问题 | 修正 |
|---|---|---|
| **P1-1** | **总闸①与 runbook L1 互斥，第一级放量步骤恒不可达。** `stop_atr_mult` 被放进 `SizingRule`，而 §八 的 L1 是「只开 `ai_trade_stop_atr_mult = 1.5`、不开 `ai_trade_sizing_enabled`」。若 `sizing_enabled=False` 时规则整体为 `None`（这是本仓库既有 `parse_*_rule` 的一贯语义），L1 观察不到任何东西。另：§4.1 接口清单里**根本没有 `parse_sizing_rule`**，而 §4.4 有 8 个仓位字段需要落成规则。 | §3.3 把规则拆成 `SizingRule`（仓位，受总闸①）与 `StopRule`（ATR 止损，受总闸②）两个**各自独立解析**的结构；§4.1 补 `parse_sizing_rule` / `parse_stop_rule` 两个签名 |
| **P1-2** | **档位地板会把第一档减速与相关性缩减整个吃掉。** `quantize_size_fraction` 的 `max(1, …)`（`signals.py:334`）保证任何 `>0` 的意图至少落到最小一格。默认 `step=0.25` 且系统 prompt 明写「sizeFraction 保守（默认 ≤0.5）」，模型高频给出 0.25：`0.25 × brake_mult(0.5) = 0.125 → 吸附回 0.25`。**第一档减速在最常见断面上是纯粹的 no-op**，`brakeSlowRounds` 却照样计数，运维会据此认为刹车生效了。相关性缩减同理。v1 §3.3 只对「分辨率」做了声明，没有意识到这条声明**直接反噬本设计自己的两个机制**。 | §3.3 新增「地板反噬」小节 + 新增 `quantizeErasedRounds` 计数与 `scaleErased` wire 键；§3.6 第一档语义修正；§7.3 新增验收行；T-23 |
| **P1-3** | **`corr_scale = min(1, cap/E)` 数学上不满足「有效敞口 ≤ cap」，§7.3 把它写成了可验收指标。** `E(w)=sqrt(wᵀΣw)` 对单个分量**非齐次**：只缩成员 i 的名义额时，`E(s)² = s²a + 2sb + c`（`c` = 其余成员自身的二次型 ≥ 0）。取 `s = cap/E(1)` 后 `E(s) ≤ cap` 要求 `(s−1)[2bs + c(s+1)] ≥ 0`，在 `c>0`、`b≥0`（同向高相关，正是本层要治的场景）时**恒不成立**。 | §3.5 改为**闭式求解**满足 `E(s·proposed) ≤ cap` 的最大 `s`（一个二次不等式，零新依赖）；并处理 `c ≥ cap²`（其余成员自身已越界，`s=0` 也救不回来）—— 该态与 S-3「本层永不否决」冲突，改为**如实标记 `exposureCapUnreachable` 并放行**，同时修正 §7.3 的验收口径 |
| **P1-4** | **刹车阈值对杠杆盲，建议值在默认杠杆下会被普通波动打穿。** `equity = budget_usd + session_pnl_usd`，而 `session_pnl` 落在**已含杠杆**的名义额上。3× 杠杆下，标的价反向 2.7% 即 8% 权益回撤（第一档）、5% 即 15%（第二档「只平不开」）。分册 05 给的 8%/15% 是按无杠杆权益口径写的，直接照搬会让一次普通回调把会话打成僵尸。 | §3.6 新增杠杆折算表与「按杠杆定标」的建议值公式；§4.4 建议生产值改为杠杆相关；§八 L3 新增标定前置步骤 |
| **P1-5** | **刹车权益含未实现盈亏，与利润保护层的「允许回吐」设计直接互踩。** `session_pnl_usd = realized_pnl_usd + unrealized_usd`（`executor.py:157-159`），故 `_peak_equity` 会随**浮盈**推进。利润保护层**按设计允许**浮盈自峰值回吐 `giveback_pct` 再离场；这段回吐在本层眼里就是一次实打实的会话回撤，足以把仓位系数打到 0.5 甚至触发 halt。v1 §3.6 直接复用 `self._peak_equity` 却完全没讨论这件事。 | §3.6 新增「峰值口径与利润保护层的相互作用」小节：口径**保留** mark-to-market（风控上正确），但 ① 显式声明、② `sizingPlan` / trace 新增 `peakEquityBasis` 与 `unrealizedShare` 两个读数、③ §八 L3 新增「halt 触发轮的 `unrealizedShare` 分布」观察项、④ 新增自我反驳六 |
| **P1-6** | **`vol_window=96` / `corr_min_samples=96` 可能超过行情窗口容量，两层永久静默空转。** `MarketWindow._window_max = min(max(warmup+5, priming_bars, htf_warmup+5, 80), 1200)`（`market_window.py:36-40`），而 `primingBars` 的 schema 下界是 **10**（`schemas.py`），故窗口最小可到 **80 < 97**。此时 `realized_vol_bar` 恒 `None`、相关性恒 `ρ=1`，两个旗舰机制**永久不生效且不报任何错**——与 P0-3 同一种静默。 | §3.3 / §3.5 新增**启动期对账**：`max(vol_window, corr_min_samples) + 1 > window_max` 即启动失败（422），错误码 `AI_TRADE_SIZING_WINDOW_TOO_SMALL`；trace 新增 `volSource`（`realized` / `insufficient_samples` / `disabled`）；T-24 |
| **P1-7** | **§五 的数据模型三条自相矛盾。** 同时写了「`AiTradeGroupStartRequest` 新增 `sizing_rule` / `brake_rule` 两个字段」「规则一律由 `manager` 从 `settings` 解析」「API 层与前端启动请求零改动」。而 `base_rule` / `profit_rule` 实际上是在 **API 层**解析的（`api/ai_auto.py:228-229` 的 `_resolve_base_rules(body.baseRules, settings)`），`AiTradeGroupStartRequest` 的唯一构造方就是那里。照 v1 实现，新增的两个字段**永远没人填**，规则恒 `None`，整个特性是死的。 | §五 改为：**不给启动请求加字段**，由 `manager` / `AiBacktestRunner` 各自从已持有的 `settings` 解析（两侧都已有 `settings`：`manager.py:599`、`backtest.py:334`），这才是真正的「API 零改动」 |
| **P1-8** | **`publish_notional` 的喂数点在限价链路上系统性少记敞口。** v1 让会话「在每轮 `_execute_decision` 之后」取 `entry_qty_sz` 上报。但限价挂单期间 `projected` / `entry_qty_sz` **刻意不写**（I-2a 幻影成交防线，`executor.py:477-489`），成交由 `_poll_resting → _adopt_fill` 在**决策轮之外**采纳。故一笔已经真实成交的持仓，在下一轮该成员的决策之前，对敞口簿贡献恒为 0——而其余成员正是在这段时间里读它。 | §3.5 把喂数点移到 `_dispatch`（`session.py:243`，**全部 executor 事件的唯一收口**，`note_close` 本来就挂在这里）+ 每轮末尾兜底一次；新增 S-14 与 T-25 |

### P2（改进建议，已一并落进正文）

| # | 问题 | 处理 |
|---|---|---|
| P2-1 | `manager.py` 现行数 651 → 实际 **650** | §6.2 已改 |
| P2-2 | §3.9 说「要动 6 个前端镜像点」，§6.3 表实为 **7** 处（表下注解自己也说了 7） | §3.9 已改为 7 |
| P2-3 | §6.3 第 7 项「新增 `SizingBlock`」没有把**挂载点**单列——组件定义了但不在渲染树里是本仓库的既有前科（基础策略终审 RV-1） | 拆成 7a（定义，`AiTradeTraceRound.tsx:247` 之后）/ 7b（挂载，`:536` `<ProfitGuardBlock/>` 之后），共 **8** 处 |
| P2-4 | `SizingContext.step` 的取数点未钉死 | 已钉：`self.decider.position_step`（公开属性，`session.py:676` 已有同款先例） |
| P2-5 | 既有 `equity` 记录里的 `drawdown` 是**负号**口径（`session.py:1261` `min(dd, 0.0)`），本设计的 `dd = 1 − equity/peak` 是**正号** | §3.6 已加量纲行，进 §3.2 的量纲表 |
| P2-6 | §7.3 的「仓位恒等于 `f_max` 不得出现」近乎恒真（`f_max=1.0` 且还要与模型提议取小，模型被 prompt 引导 ≤0.5），作为「本层是装饰品」的监控是失效的 | 已替换为 §9.1 自我反驳二给的正确对偶量：`scaleSource` 分布 |
| P2-7 | 「`f` 与 `σ_bar` 的秩相关 < −0.3」在默认 `step=0.25` 下只有 2~3 个取值，统计上不可评估 | 已改为对**吸附前**的 `fFinalRaw` 计算，并把该字段加进 trace |
| P2-8 | `session.py` 已 **1468** 行、超 `CLAUDE.md` 800 行红线 668 行，而 §2.2 只对 `executor.py` 的 895 行表态 | §6.2 已补欠账行（不阻断本轮，但须记账） |
| P2-9 | 回撤刹车是**成员级**的，分组整体回撤无人判定（3 个成员各 7% 不触发，但组合已 −7%） | §9.1 新增自我反驳七 + §十 欠账条目，本轮显式不做 |

---

## 一、Overview

**在建什么。** 本增量把 AI 自动交易的**下注规模**从「模型叙事决定」改造为「确定性计算决定」，并补上仓库当前完全缺失的两道组合级护栏。具体是四件事：① **波动率目标化仓位**——下单占比 `f` 由已实现波动率、先验胜率 `p`、edge 与成本共同解出，模型给出的 `sizeFraction` 降级为「上限提议」，只能把结果调小、绝不能调大；② **ATR 自适应止损止盈**——交易所侧 OCO 的 `stopLossPct` / `takeProfitPct` 从常数 `0.05 / 0.10`（`services/quant/app/config.py:104-105`）改为按确认根 ATR 定标，与利润保护层共用同一套 ATR 参数；③ **组合相关性预算**——AI 分组的多个成员用滚动收益率相关矩阵算出「有效敞口」，超过上限即按比例缩减各成员本轮的 `f`；④ **回撤刹车与连亏熔断**——会话权益自峰值回撤触及第一档即把仓位系数乘 0.5，触及第二档转为「只平不开」，连续亏损达阈值即进入冷却，三级全部**只作用于开新仓**。

**为什么现在做这个。** 归因恒等式 `E = p·W − (1−p)·L − C` 里，阶段一把 `C` 记进了账并让它进了门禁，阶段二第一步把 `p`（以及经 `p` 折算的 EV 分子）建成了可测量的先验体系。到此为止，**每一笔交易的"值不值得做"已经可判定，但"该下多大注"仍然完全由 LLM 的一句 `sizeFraction` 决定**——`_compute_sz`（`services/quant/app/live_auto/executor.py:711-723`）的核心恒等式至今是 `target_notional = clamp(strength,0,1) × budget_usd × leverage`，`strength` 直接来自模型输出经 `quantize_size_fraction`（`services/quant/app/ai_auto/signals.py:316`）吸附后的值，与当下波动率无关、与刚测出来的胜率先验无关、与账户已有敞口无关。这意味着一个正期望的系统仍然可以因为"在最糟的时候押得最大"而亏钱；同样，仓库唯一的止损机制是会话级绝对金额阈值 `_maybe_session_exit`（`services/quant/app/ai_auto/session.py:955-964`），它是**终局开关而非减速带**——在它触发之前，连亏五笔与连亏一笔的系统行为完全相同。`p` 已经可得而 `f` 仍是常数，是当前这条链路上唯一剩下的、可以用确定性代码消除的系统性风险敞口。

**本轮的范围裁剪（重要，须评审拍板）。** 路线图 08 号 §3.2 把阶段二第二步定义为四项：因子快照接入（D-4）、仓位重构（D-6）、回撤刹车（D-12/D-13）、五道闸门报告。本设计**只覆盖其中的仓位与风险三项**（即分册 [05 · 风险与仓位管理](../../improve/01-ai-autotrading/05-风险与仓位管理.md) 的全部内容），把因子快照（分册 03）与闸门报告（分册 06）留给第三步。理由有三条：**其一是依赖顺序**——仓位公式的 `p` 项依赖阶段二第一步刚落地的先验体系，这是四项里唯一「输入刚刚就位、现在做正好」的一项；因子快照的主路径（03 §3.3 第 3 条）是元标签特征，而元标签属于阶段三，本阶段接入它只剩 prompt 展示这条被 03 自己标为「辅助」的支路。**其二是可回滚性**——本三项共享同一个运行时载体与同一组开关，可以整体一键回退；把因子快照混进来会让回滚边界横跨 prompt 装配与决策证据两个不同层面。**其三是提交纪律**——工作树历来同时存在多个在制特性，路线图 08 §四明写每阶段须按精确路径 `git add`，四项一次落地会把混合文件的行级暂存难度推到不可控。第三步的范围与顺序在 §十 的标记方案里写清楚，不留悬案。

---

## 二、目标与不做什么

### 2.1 目标

1. **仓位由可测量的量决定**：`f = min(凯利项 × λ, 波动率目标项, f_max) × 刹车系数 × 相关性系数`，模型的 `sizeFraction` 参与最后一次 `min`。
2. **止损距离随波动率呼吸**：`stop_pct = stop_atr_mult × ATR / px`，与 `ai_trade_profit_guard_atr_period = 14`（`config.py:242`）同源，**不新造第三套 ATR 口径**。
3. **多标的敞口有上限**：有效敞口 = 带符号名义额的相关加权二次型的平方根，超上限即等比缩减。
4. **亏损有减速带**：回撤两档 + 连亏一档，全部只拦开新仓。
5. **全部开关默认关闭**：任意一项出问题都能单独关掉；全关时系统**逐字节回退**到 commit `c6db95d` 的行为。

### 2.2 不做什么（逐条冻结）

| 不做 | 理由 |
|---|---|
| 不重建利润保护棘轮 | `ai_auto/profit_guard.py` 已上线且默认开启；本层管「开仓前下多大注」，它管「已开仓之后何时锁定」，两层职责不重叠 |
| 不改 `position_ladder` / `quantize_size_fraction` 单一真源 | `signals.py:297,316`；本层输出**必须**再经它吸附，档位仍是唯一硬约束点 |
| 不新增决策工具字段 | 05 §3.2 的 `invalidationPx`（模型给出的失效价）属于分册 04，**本轮显式不做**，故 `invalidation_priority = 模型优先` 这一条降级为「留待 04 号落地」，见 §9.3 偏离清单 |
| 不动交易所侧 OCO 的既有挂法 | 只改传给 `_risk_trigger_px` 的两个 pct，挂法、时机、撤法一字不动 |
| 不引入组合优化求解器 | 相关性约束用「有效敞口上限 + 等比缩减」实现，零新依赖（`requirements.txt` 计算侧仍只有 pandas/numpy） |
| 不改 `LiveExecutor` 任何一行 | 见 §3.4 的构造缝论证；`executor.py` 已 895 行、超 `CLAUDE.md` 800 行红线，本轮对它的净增行数**硬性为 0** |
| 不新增数据库表 / 列 | 全部状态为进程内瞬态，统计经既有 `metrics_json` 加法键落库 |
| 不做因子快照（D-4）与闸门报告 | 见 §一第三段的范围裁剪；标记方案见 §十 |

---

## 三、技术设计

### 3.1 分层：纯核心 + 运行时载体 + 会话薄封装

严格镜像仓库已经跑了两轮的同款范式（`profit_guard.py` + `profit_guard_runtime.py`、`cost_model.py`/`edge_gate.py` + `cost_runtime.py`）：

```
纯核心（无状态、无 IO、无 await、可被回测与实盘同 import 同调）
  ai_auto/sizing.py           波动率目标化仓位公式 + ATR 止损距离 + 有效敞口缩减
  ai_auto/drawdown_brake.py   三级刹车状态机（纯函数推进 + 纯函数判定）
  ai_auto/exposure_book.py    分组共享的收益率 / 名义额登记簿（纯同步容器）
  ai_auto/sizing_text.py      宪章段 + 每轮上下文段 + 中文文案（渲染与判定同源）

运行时载体（纯同步、持状态、不持 session 引用）
  ai_auto/sizing_runtime.py   SizingRuntime：规则 + 刹车态 + 波动率缓存 + 计数 + wire 装配

会话（只允许薄封装调用；session.py 净增行数硬上限 +24）
  ai_auto/session.py          两个门控挂点 + 两个喂数点 + 一个统计合并点
  ai_auto/backtest.py         同位插入，同一纯核心（净增行数硬上限 +20）
```

**为什么 `exposure_book` 要独立成模块而不是塞进 runtime**：它的生命周期是**分组级**（`AiTradeGroup`，`ai_auto/manager.py:154-169`），而 runtime 是**会话级**（每个成员一个）。把分组级共享状态放进会话级对象会立刻产生「谁是所有者」的二义，且 N 个成员各持一份就等于没共享。

### 3.2 估计量定义与量纲（先钉死，再写代码）

本增量同时出现三种「百分比」，历史上仓库已经因为量纲混用付出过代价（利润保护层的诊断根因 ② 就是「用标的口径去判保证金收益」），故先立表：

| 量 | 符号 | 口径 | 消费方 | 绝不能混用的对象 |
|---|---|---|---|---|
| 下单占比 | `f` | 满仓名义额的比例 ∈ [0,1] | `_compute_sz` 的 `strength` | 与 `allocation_pct`（分组预算切分比例）无关 |
| 止损 / 止盈距离 | `stop_pct` / `take_pct` | **标的价** fraction（`fill × (1−slp)`，`executor.py:741-750`） | `RiskParams` → 交易所侧 OCO | **绝不**是保证金收益率——利润保护层的全部阈值是保证金口径（`config.py:210-232`），两者相差一个 `leverage` |
| 已实现波动率 | `σ_bar` | **每根** K 线对数收益的样本标准差 | 波动率目标项分母 | 绝不与年化值直接相除 |
| 波动率目标 | `vol_target` | **年化** fraction（默认 0.20） | 折算成 `σ_target_bar` 后才参与除法 | 见下方折算式，禁止省略折算 |
| 成本 | `costBps` | bps，毛口径 | 凯利项的赔率 `b` 的分子扣减项 | 沿用阶段一 `cost_model.estimate_round_trip_cost` 的既有定义，本轮**不新增第二份** |
| 凯利赔率 | `b` | 无量纲 = 本轮**净期望赢**距离 ÷ 本轮**止损**距离 | 凯利公式 | **绝不**取 `EdgeGateOutcome.edge_bps`——见 [P0-2]：那个量的语义随 `edge.source` 变，`prior` 时已是 `p` 加权期望，再乘一次 `p` 即重复计价 |
| 会话回撤 | `dd` | **正号** `1 − equity/peak`（0.08 = 回撤 8%） | 刹车判定 | 既有 `equity` 事件里的 `drawdown` 字段是**负号**口径（`session.py:1261` 的 `min(dd, 0.0)`）；两者绝不可互相赋值 [P2-5] |
| 会话权益 | `equity` | `budget_usd + session_pnl_usd`，**含未实现**（`executor.py:157-159`） | 峰值与回撤 | 含浮盈意味着峰值会随浮盈推进，与利润保护层的「允许回吐」直接相互作用，见 §3.6 末 [P1-5] |

年化 ⇄ 单根折算的唯一真源（写在 `sizing.py`，禁止第二处）：

```
bars_per_year = 31_536_000 / BAR_SECONDS[bar]          # 365 天，加密永续 7×24
σ_target_bar  = vol_target_annual / sqrt(bars_per_year)
```

15m 周期下 `bars_per_year = 35040`，`sqrt ≈ 187.2`，`σ_target_bar ≈ 0.107%`；BTC 15m 的 `σ_bar` 常态区间约 0.25%~0.60%，故波动率目标项 `f_vol = σ_target_bar / σ_bar` 常态落在 **0.18 ~ 0.43**。这个落点是刻意标定的：它必须落在档位阶梯（默认 `step=0.25` ⇒ `{0.25,0.5,0.75,1.0}`）的低两档附近，否则本层要么恒不生效、要么恒把仓位压到最小格。**该标定必须在 L1 观察窗口用实测 `σ_bar` 分布复核一次**（§八）。

**杠杆刻意不进分母**（须写进 `sizing.py` 模块 docstring）：`f` 是「已含杠杆的满仓名义额」的比例，账户级敞口上限由既有的 `budget × leverage` 与分组天花板 `min(maxBudgetPct/100, ai_trade_max_capital_frac, live_auto_group_max_capital_frac)`（`manager.py:557-567`）负责。把 `leverage` 再乘进波动率目标项的分母，等于对同一条红线收两次税——3× 杠杆下 `f_vol` 会被压到 0.06，永远落到最小格，本层退化为常数。

### 3.3 波动率目标化仓位（D-6 a）

**规则拆成两个各自独立解析的结构 [P1-1]**：v1 把 `stop_atr_mult` 塞进 `SizingRule`，而 §八 的 L1 是「只开 ATR 止损、不开仓位层」——若 `sizing_enabled=False` 时整条规则为 `None`（这是本仓库 `parse_*_rule` 的一贯语义），L1 就恒不可达，整个放量阶梯从第一级起就是空话。故：

```python
# ai_auto/sizing.py
@dataclass(frozen=True)
class SizingRule:
    """仓位求解规则（总闸① `ai_trade_sizing_enabled`；关 ⇒ parse_sizing_rule 返回 None）。"""
    vol_target_annual: float       # 0 ⇒ 波动率目标项不参与 min
    vol_window: int                # 已实现波动率回看根数（默认 96）
    kelly_lambda: float            # 0 ⇒ 凯利项不参与 min
    f_max: float                   # 沿用 1.0（既有 clamp01 上界），不新增红线
    exposure_cap_mult: float       # 0 ⇒ 相关性预算不生效（§3.5）
    corr_min_samples: int

@dataclass(frozen=True)
class StopRule:
    """ATR 止损规则（总闸② `ai_trade_stop_atr_mult > 0`；**与总闸①完全独立**，L1 只开这一个）。"""
    stop_atr_mult: float           # >0 才装配；parse_stop_rule 在 <=0 时返回 None
    stop_rr: float                 # 止盈 = rr × 止损

@dataclass(frozen=True)
class SizingInputs:
    action: str                    # 仅 open_long / open_short 需要求解
    model_fraction: float | None   # 模型给出、已过反思与档位吸附的值
    p_win: float | None            # 先验胜率；None ⇒ 凯利项不参与 min
    cost_bps: float | None         # 往返成本；None ⇒ 凯利项不参与 min
    stop_bps: float | None         # 本轮**实际要挂的**止损距离（bps），来自 §3.4
    take_bps: float | None         # 本轮**实际要挂的**止盈距离（bps），来自 §3.4；[P0-2] 的分子
    realized_vol_bar: float | None # None ⇒ 波动率目标项不参与 min
    brake_mult: float              # 刹车系数 ∈ (0,1]
    corr_scale: float              # 相关性缩减系数 ∈ (0,1]
    step: float                    # 档位步长（吸附用），取自 `decider.position_step` [P2-4]

@dataclass(frozen=True)
class SizingOutcome:
    fraction: float | None         # 最终占比（已吸附）；None ⇒ 本层不作用（原值直通）
    fraction_raw: float | None     # **吸附前**的解（验收指标只能用它，[P2-7]）
    f_vol: float | None
    f_kelly: float | None
    kelly_status: str              # ok | no_prior | no_cost | no_bracket | disabled
    scale_source: str              # model | vol | kelly | brake | correlation
    scale_erased: bool             # 档位地板把缩减吃掉了（[P1-2] 的观测量）
    vol_source: str                # realized | insufficient_samples | disabled
    wire: dict
```

**`SizingInputs` 不再收 `edge_bps` [P0-2]**：赔率的分子必须与本层**自己要挂的括号**同源，见步骤 3。

求解顺序（`resolve_size_fraction`）：

1. **动作白名单**：`action not in ("open_long","open_short")` ⇒ 返回 `SizingOutcome(fraction=None, …)`，调用方据此**原样直通**（`decision` 对象 `is` 相等）。
2. **波动率目标项**：`f_vol = σ_target_bar / σ_bar`；`vol_target_annual <= 0` 或 `σ_bar` 不可用（非有限 / ≤0 / 样本不足 `vol_window`）⇒ 该项不参与 `min`（记 `f_vol = None`）。
3. **凯利项（[P0-2] 已重写）**：

   ```
   b = (take_bps − cost_bps) / stop_bps          # 净期望赢距离 ÷ 止损距离
   kelly = (p·b − (1−p)) / b   ≡  p − (1−p)/b
   f_kelly = max(0, kelly) × λ
   ```

   **`take_bps` / `stop_bps` 必须是本轮 §3.4 解出来、马上就要挂到交易所侧的那一对括号**（ATR 止损未装配时退回会话构造期的固定 `stop_loss_pct` / `take_profit_pct`）。这条是硬约束，理由有三：

   - **不能取 `EdgeGateOutcome.edge_bps`**：该字段是 `EdgeEstimate.target_bps`，语义随 `edge.source` 变。`source="prior"` 时它已经是 `p·W−(1−p)·L` 的**概率加权期望**（`edge_gate.py:97-99` 自己写明「数量级天然差一档」），当成 `b` 再乘一次 `p` 就是对同一个胜率收两次税。
   - **即便总闸③关闭（默认）也不能取它**：`ai_trade_edge_atr_target_mult` 默认 1.0（`config.py:275`），而本层挂的止盈是 `stop_rr × stop_atr_mult × ATR = 3×ATR`，两者差 3 倍。用一个比自己实际止盈小 3 倍的分子解凯利，`b<1` 恒成立，凯利要求 `p>0.4` 才为正——`f_kelly` 会长期为 0 而 `kellyStatus` 仍报 `"ok"`。
   - **估计量同源律**：`p` 来自先验（"走对的概率"），`W` 必须是"走对能吃到多少"，`L` 必须是"走错要付多少"。本层同时决定了后两者（就是那对 OCO 括号），它们才是唯一自洽的 `W`/`L`。

   降级分支（一律**不参与 min**，绝不翻译成"仓位归零"）：
   - `p_win is None` ⇒ `kelly_status="no_prior"`——绝不用 `0.5` 之类的默认值凭空造先验（阶段二第一步 C-2 契约）。
   - `cost_bps is None` ⇒ `kelly_status="no_cost"`。
   - `take_bps` / `stop_bps` 任一缺失或 ≤0 ⇒ `kelly_status="no_bracket"`。
   - `b <= 0`（成本吃掉了整个止盈距离）⇒ 同样归 `"no_bracket"` 且**不参与 min**。刻意不把它翻译成否决：那是**最小 edge 门禁的判定域**（`edge_gate.py`，已在管线第 9 步），在这里再判一次就是双重计费——阶段一 §六 与路线图 08 §六 都把「双重计费导致开仓率骤降为 0」列为明确的回滚触发条件。
   - `λ <= 0` ⇒ `kelly_status="disabled"`。

   **诚实声明（写进 docstring）**：把 `take_bps` 当作 `W` 是一个**乐观上界**——真实持仓常常被模型主动平仓或被利润保护层提前锁定，realized 的赢面小于挂出去的止盈距离。缓解有三层：λ 默认 0.25（四分之一凯利）、波动率目标项的独立 `min`、以及 §7.3 新增的「realized RR vs 名义 RR」验收指标（实测 RR 长期 < 1.0 而名义 RR = 2.0 ⇒ 下调 λ，而不是继续用乐观分子）。
4. **取小 + 夹紧**：`f_core = min(存在的项…, f_max)`；若两项都不存在 ⇒ `f_core = f_max`（本层退化为只做刹车与相关性缩减）。
5. **刹车与相关性**：`f_scaled = f_core × brake_mult × corr_scale`。
6. **与模型提议取小（单调收敛的最后一道）**：`f_final_raw = min(model_fraction, f_scaled)`；`model_fraction is None`（理论上不会——`_map_decision` 已保证开仓必有 size）⇒ 取 `f_scaled`。
7. **吸附**：`fraction = quantize_size_fraction(f_final_raw, step)`（既有真源，`signals.py:316`）。

**分辨率的诚实声明（必须写进 docstring 与 UI 文案）**：`quantize_size_fraction` 内含 `max(1, …)`（`signals.py:334`），任何 `>0` 的开仓意图至少落到最小一格。这意味着 `step=0.25`（默认）时本层的实际分辨率只有 4 档，一切 `f_final_raw < 0.25` 的结果都会落到 `0.25`。想要更细的波动率响应，运维须把启动弹窗的 `positionStepPct` 调到 0.1。**不得**为本层单开一条绕过档位的路径——档位是用户显式配置的硬约束，绕过它就是用系统内部逻辑推翻用户设置。

**地板反噬：它会吃掉本设计自己的两个机制 [P1-2]。** v1 只把上面那段当作"分辨率有限"来声明，没有算清它的直接后果：系统 prompt 明写「sizeFraction 保守（默认 ≤0.5）」（`prompts.py:150`），模型高频给出 `0.25`；此时

```
0.25(模型) × 0.5(第一档减速) = 0.125  →  quantize(0.125, 0.25) = 0.25
```

**第一档减速在最常见的断面上是纯粹的 no-op**，相关性缩减同理，而 `brakeSlowRounds` / `corrScaledRounds` 照样计数——运维会据此认定"刹车生效了"，这正是本仓库最忌讳的那种假阳性观测。三条应对（全部落进本设计，**不**绕过档位）：

1. `SizingOutcome.scale_erased`：当 `quantize(f_final_raw) >= quantize(进入本层时的值)` 而 `f_final_raw < 进入值` 时置真，独立计入 `sizing.quantizeErasedRounds`。
2. 每轮 `sizing` trace 同时下发 `fFinalRaw` 与 `finalFraction`，两者的差就是被地板吃掉的量，UI 上可见。
3. §八 L3 的放行判据从"第一档必须触发过"改为"**第一档触发轮里 `scaleErased=false` 的占比 ≥ 50%**"——否则先把 `positionStepPct` 调到 0.1 再观察，而不是宣称刹车已验证。

**窗口容量的启动期对账 [P1-6]**：`realized_vol_bar` 需要 `vol_window+1` 根收盘价，而行情窗口容量是 `_window_max = min(max(warmup+5, priming_bars, htf_warmup+5, 80), 1200)`（`market_window.py:36-40`），`primingBars` 的 schema 下界是 **10**，故窗口最小可到 **80 < 97**。此时波动率项恒不可用、相关性恒 `ρ=1`，两个旗舰机制永久静默失效。故：**启动期校验** `max(vol_window, corr_min_samples) + 1 <= window.window_max`，越界即启动失败（422，错误码 `AI_TRADE_SIZING_WINDOW_TOO_SMALL`，文案给出"把 primingBars 调大到 N 或把 vol_window 调小到 M"两条出路）。运行期再另加一道如实标注：`vol_source ∈ {realized, insufficient_samples, disabled}` 进 wire 与 trace——暖机期样本还没攒够是**正常**的，但它必须看得见，不能与"配错了"混为一谈。

### 3.4 ATR 自适应止损止盈（D-6 b）

```python
def resolve_stop_distance(*, atr_px, ref_px, rule: StopRule | None,
                          max_sl_pct, max_tp_pct) -> StopPlan | None:
    """返回 {stop_pct, take_pct, stop_bps, take_bps, source}；不可用 ⇒ None ⇒ 逐字回退固定 pct。"""
```

- `stop_pct = rule.stop_atr_mult × sanitize_atr(atr_px) / ref_px`，`take_pct = rule.stop_rr × stop_pct`。
- `stop_bps = stop_pct × 1e4`、`take_bps = take_pct × 1e4`——这一对**同时**是 §3.3 凯利赔率的 `L` 与 `W`（[P0-2]）。`StopPlan is None`（ATR 止损未装配 / 不可用）时，凯利的括号退回会话构造期的 `executor.risk.stop_loss_pct` / `take_profit_pct`；两者都为 `None` ⇒ `kelly_status="no_bracket"`。
- `sanitize_atr` 直接复用 `profit_guard.sanitize_atr`（与实盘 `_atr_px`、回测 `_atr_at` 同一条降级判据），**禁止另写**。
- 夹紧：`stop_pct` 夹到 `(0, ai_trade_max_sl_pct]`（`config.py:147`，默认 0.20），`take_pct` 夹到 `(0, ai_trade_max_tp_pct]`（`config.py:148`，默认 0.50）。这两个上限是既有的用户可见红线，本层只能在其内活动。
- `rule.stop_atr_mult <= 0` / `atr_px` 不可用 / `ref_px` 不可用 ⇒ 返回 `None` ⇒ **不改写 `executor.risk`** ⇒ OCO 逐字回退到会话构造期解析出的固定 pct。

**落地缝（本轮 `executor.py` 净增 0 行的关键）**：`LiveExecutor.risk` 是一个 `RiskParams` dataclass（`services/quant/app/engine/simulator.py:17-22`，三字段：`stop_loss_pct` / `take_profit_pct` / `max_hold_bars`），`_do_open` 在下单前调 `self._risk_trigger_px(side, ref_px if limit_px is None else limit_px)`（`executor.py:269`）现算随单 OCO 触发价。因此**会话侧在执行之前替换 `executor.risk` 即可**：

```python
# ai_auto/sizing_runtime.py（模块级函数，镜像 profit_guard_runtime.build_position_facts 收 executor 形参的先例）
def can_override_risk(executor) -> str | None:
    """能否安全改写 `executor.risk`；返回 None=可以，否则返回拒绝原因（进 wire 的 stopSource）。

    [P0-1] 这两条前置条件不是防御性编程，是**正确性条件**：`executor.risk` 一旦被改写，
    `_adopt_fill`（`executor.py:494`）与 `exchange_oco_px` 的 derived 分支就会用新值去描述
    一个按旧值挂在交易所侧的 OCO——那是一个交易所侧根本不存在的价（`ai-trade-limit-orders`
    P1-4 的原坑）。
    """
    if executor.resting is not None:
        return "skipped_resting"          # 有挂单在飞：它的 OCO 已按旧 risk 附于交易所侧
    if executor.projected != "flat":
        return "skipped_position_live"    # 有持仓：其 OCO 已挂，改 risk 只会让展示口径撒谎
    return None


def apply_risk_override(executor, plan: StopPlan | None) -> str:
    """把本轮 ATR 止损距离写进 executor.risk；返回实际生效的 `stopSource`。

    `plan is None` 或前置条件不满足 ⇒ **一行都不动**，逐字回退到当前 risk（构造期固定 pct，
    或上一次开仓时写入的那一份——两者都与交易所侧现存 OCO 一致）。
    """
    if plan is None:
        return "fixed"
    blocked = can_override_risk(executor)
    if blocked is not None:
        return blocked
    executor.risk = replace(executor.risk, stop_loss_pct=plan.stop_pct,
                            take_profit_pct=plan.take_pct)   # max_hold_bars 原样保留
    return "atr"
```

**`stopSource` 五态**（进 payload / trace / 统计，运维据此分辨"没生效"的三种不同原因）：`atr`（本轮按 ATR 改写）｜`fixed`（ATR 不可用或总闸②关，走固定 pct）｜`skipped_resting`｜`skipped_position_live`｜`skipped_reversal`（`skipped_position_live` 的子情形：本轮是反手，见下）。

这个缝的正确性论证（v1 的论证 1 与 2 已被证伪，此处重写）：

1. **~~中间无 `await`~~ → 不成立，改用状态前置条件**。`_execute_decision` 的第一件事就是 `async with self._exec_lock`（`session.py:879`），而 5s 盯市循环在同一把锁上（`session.py:395`）——**这是一个真实的让出点**，盯市循环完全可能在 override 与 `_do_open` 之间抢到锁、跑完 `reconcile → _poll_resting → _adopt_fill`。因此正确性**不能**建立在"无 await"上，只能建立在 `can_override_risk` 的两条状态前置条件上：`flat` 且无挂单时，交易所侧**没有任何**按旧 risk 挂着的 OCO，改写它对任何在飞对象都无影响。
2. **下游读点在前置条件下才一致**。三个读点——`_risk_trigger_px`（下单时挂 OCO，`executor.py:269`）、`_adopt_fill` 的成交回执重算（`executor.py:494`）、`_make_trade` 的 `stopLossPct`/`takeProfitPct` 展示字段（`executor.py:810-811`）——读的都是同一个 `self.risk`。**关键在 `_adopt_fill`**：限价委托默认开启，`_do_open` 下单时按当轮 risk 附了 OCO，但挂单可以跨若干轮才成交，采纳时才重算 tp/sl 并落库；若期间 risk 被改写，落库的 `stopLossPrice` 就是一个交易所侧不存在的价，而 `profit_guard_runtime.exchange_oco_px`（`profit_guard_runtime.py:81-87`）的**首选真源**恰恰是这条回执。`skipped_resting` 前置条件正是为堵这条路径存在的。
3. **反手轮不改写**（`skipped_reversal`）：`_actions_for` 在反手时先 `close` 再 `open`，进入第 11 步时 `projected != "flat"`，故落进 `skipped_position_live`。代价是反手开仓拿不到 ATR 止损、退回当前固定 pct——**这是 fail-safe 而非 fail-open**（拿到的是一个真实、且与随后挂出的 OCO 一致的距离），且反手轮占比进 `sizing.reversalSkips` 供观测。若 L1 观察显示该占比过高（> 20%），再单独立项处理，本轮不做。
4. **`max_hold_bars` 必须原样保留**：`_maybe_time_exit`（`executor.py:243-250`）读它，改动会静默改变时间止盈行为。
5. **不影响非 AI 链路**：`live_auto` 的 executor 由 `live_auto/manager.py` 构造、其会话从不进入 `ai_auto/session.py` 的决策轮，本函数没有任何调用点能触及它（与阶段一 `fee_ledger=None` 构造缝同款作用域论证，Z-7）。

**残留态的诚实声明**：写入后的 `risk` 会一直生效到下一次满足前置条件的开仓被覆盖为止。这是**期望行为**——OCO 已按该距离挂在交易所侧，会话内的展示口径必须跟着它走。而**正因为**它是残留态，改写窗口才必须收窄到"手上什么都没有"的那一刻。

### 3.5 组合相关性预算（D-12）

```python
# ai_auto/exposure_book.py
class GroupExposureBook:
    """AI 分组内跨成员共享的收益率 / 名义额登记簿（纯同步容器，无锁）。"""
    def publish_returns(self, inst_id: str, closes: list[float], as_of_ts: int) -> None
    def publish_notional(self, inst_id: str, signed_notional: float) -> None
    def effective_exposure(self, *, inst_id: str, proposed_notional: float,
                           min_samples: int) -> ExposureView | None
```

- `effective_exposure` 的定义：设成员集合的**带符号名义额**向量 `w`（`long` 为正、`short` 为负、`flat` 为 0），当前求解成员的分量替换为 `proposed_notional`，则

  ```
  E = sqrt( Σᵢ Σⱼ wᵢ · wⱼ · ρᵢⱼ )
  ```

  取符号相乘是刻意的：两个成员一多一空且高相关时，`wᵢwⱼρᵢⱼ < 0`，有效敞口**应当**下降——那才是真的对冲。
- `ρᵢⱼ` 由两成员各自 `publish_returns` 的对数收益序列在**共同时间戳上对齐后**计算；对齐后重叠样本数 `< min_samples` ⇒ 该对 **`ρ=1.0`**（最保守），绝不按 0 处理，绝不跳过该对。
- 上限：`unit = (Σ budget_i) × leverage / n`（平均单标的满仓名义额），`cap_abs = exposure_cap_mult × unit`。
- **缩减系数必须闭式求解，不能用 `cap/E` [P1-3]**。v1 取 `corr_scale = min(1, cap_abs / E)`，但 `E` 对单个分量**非齐次**：只缩成员 i 的名义额时

  ```
  E(s)² = s²·a + 2s·b + c        a = ρᵢᵢwᵢ² = wᵢ² > 0
                                 b = wᵢ · Σ_{j≠i} wⱼρᵢⱼ      （交叉项）
                                 c = Σ_{j≠i} Σ_{k≠i} wⱼwₖρⱼₖ ≥ 0  （其余成员自身的二次型）
  ```

  取 `s = cap/E(1)` 后 `E(s) ≤ cap` 需要 `(s−1)·[2bs + c(s+1)] ≥ 0`，在 `c>0`、`b≥0`（同向高相关——**正是本层要治的场景**）时恒不成立。§7.3 把「有效敞口 ≤ cap_abs」写成实时验收指标，照 v1 实现该指标必然长期不达标。正确解法是直接解那个二次不等式 `s²a + 2sb + (c − cap²) ≤ 0`：

  ```
  disc = b² − a·(c − cap_abs²)
  s    = clamp01( (−b + sqrt(disc)) / a )        # a > 0，取正根
  ```

  零新依赖、一次 `sqrt`，且 `E(s·proposed) ≤ cap_abs` 变成**可证明**的不变式（S-15/T-26）。
- **`c ≥ cap_abs²` 的边缘态（其余成员自身已越界）**：此时 `disc < 0`，`s=0` 也救不回来。这与 S-3「本层永不改写 action」直接冲突，故**不否决**：置 `corr_scale = 0`（经档位地板仍会落到最小一格，见 §3.3 [P1-2]），同时在 wire 与 trace 置 `exposureCapUnreachable = true` 并计入 `sizing.exposureUnreachableRounds`。**必须如实标注而不是假装缩到位了**——一个算得出来却做不到的上限，比没有上限更危险（`ai-trade-profit-protection` 终审 RV-1 的同一条教训）。§7.3 的验收口径随之改为「`exposureCapUnreachable=false` 的轮次里 `E ≤ cap_abs` 恒成立」。
- 关闭值：`exposure_cap_mult = 0` ⇒ `effective_exposure` 根本不被调用，`corr_scale ≡ 1.0`。
- **启动期校验**：`exposure_cap_mult ∈ {0} ∪ [1.0, 10.0]`，越界即启动失败（422）。`≥ 1.0` 的下界保证了**单成员分组恒不受约束**（`n=1` 时 `E = |w| ≤ unit ≤ cap_abs`），这是零回归的结构性保证，而不是靠默认值碰巧成立。

**并发安全（S-7）**：`GroupExposureBook` 的三个方法全部是纯同步、无 `await`。一个 AI 分组的全部成员会话跑在同一个 asyncio 事件循环里，同步方法之间不可能交错，因此**不需要锁**。任何后续给这些方法加 `await` 的改动都会破坏该不变式，必须在 docstring 里写死这条禁令。

**喂数点**：
- `publish_returns`：会话在 `feed_candle` 处理完确认根后调用一次，传 `MarketWindow` 最近 `max(vol_window, corr_min_samples)+1` 根收盘价（`market_window.py:63` 的 `candles` 已是 deque，取尾部即可，零额外 IO）。容量对账见 §3.3 末 [P1-6]。
- `publish_notional`：**挂在 `session._dispatch`（`session.py:243`）[P1-8]**，即全部 executor 事件的唯一收口（`note_close` 本来就挂在这里），每收到一条 `trade` 事件就按当前 `executor` 断面上报 `signed = ±(entry_qty_sz × ct_val × 现价)`，`flat` 传 `0.0`；决策轮末尾再兜底上报一次（覆盖"本轮无成交但持仓浮动"的情形）。

  **为什么不能只挂在 `_execute_decision` 之后（v1 的写法）**：限价挂单期间 `projected` / `entry_qty_sz` **刻意不写**（I-2a 幻影成交防线，`executor.py:477-489`），成交由 `_poll_resting → _adopt_fill` 在**决策轮之外**采纳。故一笔已经真实成交的持仓，在该成员下一轮决策之前对敞口簿的贡献恒为 `0`——而其余成员正是在这段时间里读它。挂到唯一收口上，任何路径产生的成交（限价采纳 / OCO 合成 / 外部干预）都能立刻反映进敞口，且不需要新增第二条侦测路径。

### 3.6 回撤刹车与连亏熔断（D-13）

```python
# ai_auto/drawdown_brake.py
@dataclass(frozen=True)
class BrakeRule:
    slow_dd_pct: float          # 0 ⇒ 第一档关闭
    slow_size_mult: float       # 默认 0.5
    halt_dd_pct: float          # 0 ⇒ 第二档关闭
    loss_streak: int            # 0 ⇒ 第三档关闭
    loss_cooldown_s: float      # = loss_cooldown_bars × BAR_SECONDS[bar]

@dataclass
class BrakeState:
    peak_equity: float
    equity: float
    loss_streak: int
    halted: bool                # 第二档滞回锁存
    cooldown_until_wall: float
    slow_rounds: int; halt_rounds: int; cooldown_hits: int; max_dd: float
```

三档语义：

| 档 | 触发条件 | 动作 | 解除 |
|---|---|---|---|
| 1 · 减速 | `dd ≥ slow_dd_pct`（建议值按杠杆定标，见下） | `brake_mult = slow_size_mult`（0.5）；**注意档位地板可能吃掉它**，见 [P1-2] | `dd < slow_dd_pct` 即自动恢复 |
| 2 · 只平不开 | `dd ≥ halt_dd_pct` | 开仓动作硬降级 `hold`，码 `drawdown_brake_halt(dd=16.2%)` | **滞回**：`dd ≤ slow_dd_pct`（第一档关时取 `halt_dd_pct/2`）才解除 |
| 3 · 连亏冷却 | 连续亏损平仓笔数 `≥ loss_streak`（默认建议 4） | 开仓动作硬降级 `hold`，码 `loss_streak_cooldown(4,3600s)`；同时 `loss_streak` 归零 | 墙钟 `cooldown_until_wall` 到期 |

- `dd = 1 − equity / peak_equity`（**正号**口径，与既有 `equity` 事件里那个负号的 `drawdown` 字段绝不互换，[P2-5]），`equity = budget_usd + session_pnl_usd`，`peak_equity` 复用会话**已有**的 `self._peak_equity`（`session.py:203` 初始化、`session.py:1259` 推进）——本轮**不新建第二份峰值**。

**阈值必须按杠杆定标 [P1-4]。** `session_pnl_usd` 落在**已含杠杆**的名义额上，而 `dd` 的分母是保证金口径的 `budget`，故一次标的价波动被放大 `leverage` 倍才进 `dd`。分册 05 给的 8% / 15% 是按无杠杆权益写的，直接照搬会让一次普通回调把会话打成僵尸：

| 杠杆 | 触发第一档（8%）所需的**标的价**反向幅度 | 触发第二档（15%）所需幅度 |
|---|---|---|
| 1× | 8.0% | 15.0% |
| 3×（AI 分组常用） | **2.7%** | **5.0%** |
| 5× | 1.6% | 3.0% |

BTC 15m 在一天内走出 3% 回调是常态，即"3× 杠杆下按 05 号原值配置 ⇒ 半数交易日都会进只平不开"。故建议生产值改为**按杠杆折算**：

```
slow_dd_pct  = 0.08 × min(leverage, 5) / 1.0 ÷ leverage × k   →  实践简化为下式
slow_dd_pct  ≈ 0.08 × (3 / leverage)      halt_dd_pct ≈ 0.15 × (3 / leverage)
```

即以 3× 为基准锚点（`slow=8% / halt=15%` 恰对应标的价 2.7% / 5.0%）。**这两个值仍必须在 L3 用实测回撤分布复核**（§八）：判据是"第一档在观察窗口内触发过、但触发轮的后续 24 根内**未**继续扩大亏损的比例 < 50%"——若一触发就恢复，说明阈值过紧、在正常噪声上刹车。

**峰值口径与利润保护层的相互作用 [P1-5]。** `session_pnl_usd = realized_pnl_usd + unrealized_usd`（`executor.py:157-159`），故 `_peak_equity` 会随**浮盈**推进。而利润保护层**按设计允许**浮盈自峰值回吐 `giveback_pct` 之后才离场——这段"设计内的回吐"在本层眼里是一次实打实的会话回撤，足以把仓位系数打到 0.5、甚至在高杠杆下触发 halt。两层于是相互踩：保护层让利润跑，刹车因为让它跑而收紧下一笔。

口径**保留** mark-to-market（风控上这是正确的：未实现亏损也是真亏损，只按已实现算会让刹车在最需要它的时候瞎掉），但必须做到三件事：
1. **显式声明**，写进 `drawdown_brake.py` 模块 docstring 与宪章段。
2. **可观测**：`sizingPlan` / trace 新增 `peakEquityBasis`（`markToMarket`）与 `unrealizedShare = unrealized_usd / max(|session_pnl|, ε)` 两个读数。
3. **L3 观察项**：halt 触发轮的 `unrealizedShare` 分布——若长期 > 0.8，说明刹车几乎只被浮盈回吐触发而非真实亏损，正确动作是**上调 `halt_dd_pct`**（或把口径改为已实现，作为下一轮的独立立项），而不是关掉利润保护层。
- 第二档用滞回而非锁存到会话结束：纯锁存会让一个仍在运行的会话变成「活着但永远不开仓」的僵尸（本仓库有过「反思致永不开单」的同类事故）；纯阈值又会在阈值附近反复抖动。滞回带是两者之间唯一正确的选择。
- 连亏计数的推进点：`session._dispatch`（`session.py:243-261`）是**全部 executor 事件的唯一收口**，`side == "close"` 的成交在此已被用于 `_position_seq` 自增与 `_cost.record_close`，连亏计数挂同一个点。判据用 `trade["pnl"]`（A0-2 开启时即净额、关闭时为毛额——两种口径下「亏」的语义都成立，无需分叉）。`pnl > 0` ⇒ 归零；`pnl < 0` ⇒ +1；`pnl == 0` ⇒ 不动（平局不该算连亏，也不该清算已有连亏）。
- **只拦开仓（S-9）**：`apply_brake_gate` 的动作白名单与 `edge_gate._GATED_ACTIONS` / `base_rules._GATED_ACTIONS` 逐字同构，仅 `("open_long","open_short")`。`close` / `cancel_order` / `hold` 永不被触碰；利润保护层的平仓走 `_maybe_profit_exit`（`session.py:1003`）根本不经决策管线，物理上不可能被本层拦住。
- **与利润保护冷却取并集**：两层各自独立判定、各自返回降级码，先命中者写进 `degrade`（管线顺序见 §3.7）。这天然就是并集而非累加，无需额外代码，但须写进测试（T-14）。

### 3.7 决策轮插入位置（完整管线）

```
_run_decision_round（session.py:563-638）
  1  _build_decision_payload  →  2  compute_consensus  →  3  decide（LLM）
  4  _update_hold_streak / _update_profit_hold_streak / _record_profit_commitment
  5  _apply_base_rule_gate                      铁律层
  6  _apply_profit_cooldown_gate                保护层冷却
  7 ★_apply_brake_gate                          【新增】回撤刹车 / 连亏熔断（只降级为 hold）
  8  _apply_consensus_gate                      共识
  9  _apply_edge_gate                           最小 edge 门禁
 10  _reflect_before_execute                    下单前二次反思
 11 ★_apply_sizing                              【新增】仓位求解 + ATR 止损覆盖（只缩不放，绝不改 action）
 12  _execute_decision                          真实下单
```

**第 7 步位置的三条论证**：
- 排在**反思之前**：被刹车拦下的轮次此时已是 `hold`，而反思对 `hold` 本就直通 ⇒ 自动省掉一次反思 LLM 调用（与铁律层、edge 门禁的同款省钱论证）。
- 排在**共识与 edge 门禁之前**：被拦轮不会进 `edgeGate` 的计数分母，避免稀释 `vetoRate`（阶段一 `cost_runtime.note_gate` 的 P1 教训）。
- 与第 6 步相邻：两者都是「系统自己刚做过的事导致的暂停」，语义同族，降级码在 UI 上也应当相邻呈现。

**第 11 步位置的两条论证**：
- 排在**反思之后**：反思可能收缩仓位（`apply_reflection`），本层与它取 `min`，因此最终执行的规模是**全部层的最小值**，单调收敛不变式（S-1）与顺序无关但在此处最易证明。
- 排在**执行之前**：ATR 止损覆盖必须在 `_do_open` 现算 OCO 触发价之前落地（§3.4）。

**第 11 步绝不改写 action（S-3）**：本层只解 `f`。「不该开」这件事由第 5/7/8/9/10 层负责；本层再加一次否决就是在同一件事上收第二次税，也违反路线图 08 §一「阶段一之后不再加新的保护层」的纪律。

### 3.8 payload / prompt 段

新增 `payload["sizingPlan"]`（**加法键**；规则未装配或全关 ⇒ **整键缺席** ⇒ prompt 逐字节回退，沿用 `baseRules` / `profitGuard` 的既有降级范式）：

```jsonc
{
  "volTargetAnnual": 0.20, "realizedVolBar": 0.0038, "realizedVolAnnual": 0.71,
  "volSource": "realized",        // realized | insufficient_samples | disabled  [P1-6]
  "fVol": 0.28, "fKelly": 0.21, "kellyStatus": "ok", "fCap": 0.21,
  "stopPct": 0.021, "takePct": 0.042,
  "stopSource": "atr",            // atr | fixed | skipped_resting | skipped_position_live  [P0-1]
  "brakeLevel": "none",           // none | slow | halt | cooldown
  "drawdownPct": 0.031, "lossStreak": 1,
  "peakEquityBasis": "markToMarket", "unrealizedShare": 0.62,   // [P1-5]
  "effectiveExposure": 12500.0, "effectiveExposureCap": 18000.0, "corrScale": 1.0,
  "exposureCapUnreachable": false                                // [P1-3]
}
```

Prompt 段由 `sizing_text.format_sizing_for_prompt(plan)` 渲染（渲染与判定同源，杜绝「UI 说 0.5、系统执行 0.25」的信任崩塌），插在 `_format_profit_cooldown` 之后、会话盈亏行之前（`prompts.py:499-504`）。文案要点：

> 【仓位约束】系统已按当前波动率与胜率先验解出本轮**上限占比 ≈0.21**（波动率目标项 0.28 / 凯利项 0.21，当前回撤 3.1%）。你给出的 sizeFraction **只会被调小、不会被调大**；给出高于上限的值不会报错，但会被系统按上限执行。止损距离按 ATR 定标为 ±2.1%（止盈 4.2%）。

系统 prompt 侧新增一句纪律（挂在既有 `_size_discipline`，`prompts.py:146-154` 的返回值后追加一句，**不替换原句**——原句讲的是档位、新句讲的是上限，两者不冲突）：

> 你的 sizeFraction 是**上限提议**：系统会按波动率、胜率先验、成本与组合敞口独立解一个上限，并取两者较小值执行。

### 3.9 轨迹与可观测

新增**一个** trace phase `sizing`（每轮一条，仅规则装配时发；未装配的会话根本收不到该相位）。承认代价：新增一个相位要动 **7** 个前端镜像点（[P2-2]：v1 正文写 6、§6.3 表列 7，以表为准；经 [P2-3] 拆分挂载点后共 **8** 处）。之所以仍然选它而不是「加法键搭 `round_start`」：仓位求解发生在**轮次末尾**（第 11 步），`round_start` 早已发出，搭不上；而 `edge_gate` 相位已经证明了「每轮一条门禁事件」的模式在 UI 上是可用的。

事件形状（16 个键；`emptyRoundTrace` 与 `AiTradeRoundTrace` 须逐键对齐，见 §6.3）：

```jsonc
{ "type": "agent_trace", "data": {
    "phase": "sizing", "round": 42, "ts": 1785000000000,
    "brakeLevel": "slow", "brakeReason": null, "drawdownPct": 0.092, "lossStreak": 0,
    "modelFraction": 0.75, "fVol": 0.28, "fKelly": 0.21, "kellyStatus": "ok",
    "fFinalRaw": 0.135, "finalFraction": 0.25,        // 两者之差 = 被档位地板吃掉的量 [P1-2]
    "scaleSource": "brake", "scaleErased": true,      // [P1-2]
    "volSource": "realized",                          // [P1-6]
    "stopPct": 0.021, "takePct": 0.042, "stopSource": "atr",   // 五态 [P0-1]
    "effectiveExposure": 12500.0, "effectiveExposureCap": 18000.0,
    "corrScale": 1.0, "exposureCapUnreachable": false } }       // [P1-3]
```

会话统计（`aiStats` 的**加法键**，经 `merge_sizing_stats` 合并，键冲突即抛，镜像 `stats_ext.merge_cost_stats`）：

```jsonc
"sizing": { "scaledRounds": 37, "scaleRate": 0.42, "avgScaleRatio": 0.61,
            "quantizeErasedRounds": 21,        // [P1-2] 缩减被档位地板吃掉的轮数
            "brakeSlowRounds": 12, "brakeHaltRounds": 3, "lossStreakCooldowns": 1,
            "maxDrawdownPct": 0.118, "haltUnrealizedShareAvg": 0.71,   // [P1-5]
            "atrStopRounds": 88, "reversalSkips": 3, "restingSkips": 9, // [P0-1]
            "corrScaledRounds": 4, "exposureUnreachableRounds": 0 }     // [P1-3]
```

合并落点与阶段一逐字相同：`_finalize_run` 的调用点合并（`_compute_ai_stats` 是模块级纯函数，签名不动——阶段一 P0-1 已经踩过这个坑）。

### 3.10 回测同构（闸 0 的前置条件）

同一批纯函数在 `backtest.py::_run_decision` 的**语义等价位置**插入，与实盘同序（第 7 步在冷却之后共识之前、第 11 步在反思之后 `_execute` 之前）。

**必须处理的复发点（阶段一 I-5 同款）**：`_setup` 里 ATR 序列的构造条件目前是

```python
if self.profit_rule is not None or self.settings.ai_trade_edge_gate_k_min > 0:   # backtest.py:334
```

本轮的 ATR 止损同样按 `_atr_at(i)` 取数，故该条件**必须扩展**为再或上 `self.settings.ai_trade_stop_atr_mult > 0`。不扩展的后果与 I-5 逐字相同：回测里 `_atr_series is None` ⇒ `_atr_at` 恒 `None` ⇒ ATR 止损恒不生效 ⇒ 「回测同位插入」变成空话，**且不报任何错**。

**必须写清的第二个复发点：回测侧 ATR 止损的写入点 [P0-3]。** v1 只说"同一批纯函数同位插入"，没有一条讲止损距离怎么写进回测——而回测的止损价**不是**每根按 `_oco_risk` 现算的：

- `SimBroker._open` 在开仓那一刻用 **`self._risk`** 冻结出 `stop_loss_price` / `take_profit_price` 塞进 `self.pos`（`simulator.py:269/277-293`）；
- `apply_risk(bar_index, row, _risk)` 的**第三个形参根本没被使用**（`simulator.py:169`，形参名带下划线就是证据），它只读 `self.pos.stop_loss_price`。

因此实现者最自然的写法——"改 `self._oco_risk`"，毕竟它正是传给 `apply_risk` 的那个，也是 `_build_result` 回显的那个——**一行都不会生效**，且不报任何错。正确的写入点是**两条一起改**：

```python
# backtest.py::_run_decision 第 11 步（反思之后、_execute 之前）
if plan is not None:
    self._oco_risk = replace(self._oco_risk, stop_loss_pct=plan.stop_pct,
                             take_profit_pct=plan.take_pct)
    self.main.set_risk(self._oco_risk)        # ← 缺这一行 = 彻底空转（simulator.py:123 已存在）
```

`set_risk` 已存在且 `backtest.py:257` 已在构造期调用过一次，故**零 `engine/simulator.py` 改动**。两条都要改的原因：`_oco_risk.take_profit_pct` 还被 edge 门禁当上界读（`backtest.py:534`）、被 `_build_result` 回显（`backtest.py:833-834`），只改一边会让门禁上界与展示用失效常数。

**时序上恰好安全**：订单在**次根开盘**成交（`fill_pending`，`backtest.py:385`）而 `_run_decision` 在同一根的**更后面**（`backtest.py:399`），故第 i 根写入的 risk 正好被第 i+1 根开盘的那次 `_open` 快照走，中间不会被别的轮次覆盖。这也让回测与实盘在语义上真正同构：**两侧都是"下单时冻结、之后不再变"**——而不是回测变成了追踪止损、实盘是固定止损（若照 v1 的模糊描述实现，很容易滑向后者）。

回测侧的三个喂数点：
- 权益：`_run_loop` 在 `self.main.mark(...)`（`backtest.py:390`）之后一行，用 `self.main.equity_at(close)` 推进刹车状态。
- 平仓：`_emit_new_trades`（`backtest.py:697-701`）是回测侧成交的唯一收口，`side == "close"` 的记录在此喂连亏计数。
- 相关性预算：**回测侧显式不实现**——AI 回测是单标的运行器（`AiBacktestRunner` 只有一个 `inst_id`），组合相关性在结构上不存在。这是「回测与实盘同构」的一个真实例外，必须写进 `_build_notes` 的偏差免责文案（`backtest.py:780`，与既有 `_PROFIT_GUARD_BACKTEST_NOTE` / `_EDGE_GATE_BACKTEST_NOTE` 同源同展示）。

新增回测免责条款（`_SIZING_BACKTEST_NOTE`）：

> 仓位与刹车层在回测中的**五**条已知偏差：① 组合相关性预算不生效（回测为单标的）；② 已实现波动率取自本根之前的收盘序列，与实盘的滚动窗口一致，但实盘可能在两根之间被墙钟唤醒并用同一根的旧波动率求解；③ ATR 止损经 `SimBroker.apply_risk` 的**根内保守成交序**判定（止损优先于止盈），与交易所侧 OCO 的实时触发不同构；④ 连亏冷却按根 ts 计时，实盘按墙钟计时，定时唤醒会使实盘的冷却覆盖更多决策轮；⑤ 回测无限价挂单与反手在飞态，故实盘的 `skipped_resting` / `skipped_position_live` 两条**跳过改写**分支在回测中永不出现——回测里 ATR 止损的覆盖率天然高于实盘。

### 3.11 不变式清单（改代码前先读；每条对应一个测试）

| 编号 | 不变式 | 验证 |
|---|---|---|
| S-1 | **只收不放**：任何路径下最终 `size_fraction ≤` 进入第 11 步时的值 | T-1 属性测试（随机 500 组输入） |
| S-2 | 动作白名单 `("open_long","open_short")` 与 `edge_gate._GATED_ACTIONS` 逐字同构 | T-2 |
| S-3 | 第 11 步永不改写 `action`；唯一能改 `action` 的是第 7 步的 halt / cooldown 两档 | T-3 |
| S-4 | 全部开关取关闭值 ⇒ 逐字节回退：`decision` 对象 `is` 相等、payload 无 `sizingPlan` 键、trace 恒初值、`executor.risk` 不被赋值 | T-4（对象恒等断言） |
| S-5 | `stop_pct` 是标的价口径，绝不与利润保护层的保证金收益率口径互换 | T-5（同一 ATR + 5× 杠杆下两层的数值必须不同且比值 ≈ leverage） |
| S-6 | 相关样本不足 ⇒ `ρ=1`，绝不按 0、绝不跳过该对 | T-6 |
| S-7 | `GroupExposureBook` 全部方法纯同步（源码级断言：模块内无 `await`） | T-7（AST 扫描） |
| S-8 | `exposure_cap_mult ≥ 1.0` ⇒ 单成员分组恒不受约束 | T-8 |
| S-9 | `close` / `cancel_order` 被本增量任何机制拦截的次数 = 0 | T-9（穷举四动作 × 三档刹车） |
| S-10 | `executor.py` 净增行数 = 0；`session.py ≤ +26`；`backtest.py ≤ +24` | 合并前 `git diff --numstat` 机械核对 |
| S-11 | **`executor.risk` 只在 `projected == "flat"` 且 `resting is None` 时被改写**；任一不满足即一行不动并如实置 `stopSource` [P0-1] | T-17b（有挂单）/ T-17c（有持仓）：断言 `executor.risk` 对象 `is` 相等 |
| S-12 | 凯利赔率的 `W`/`L` 恒取本轮**实际要挂的**括号；源码级 grep 确认 `sizing.py` **不 import** `edge_gate` 且不读 `edge_bps` [P0-2] | T-11b（AST/grep）+ T-12b（数值：`take_bps` 变则 `b` 变、`edge_bps` 变则 `b` 不变） |
| S-13 | 回测 ATR 止损**真的改变了成交价**：`stop_atr_mult>0` 的回测里 `pos.stop_loss_price` 随 ATR 变动 [P0-3] | T-19b（断言两根不同 ATR 下开的仓，`stop_loss_price/entry` 比值不同） |
| S-14 | 敞口簿的名义额来自 `_dispatch` 唯一收口；限价采纳成交后**下一次读簿**即可见 [P1-8] | T-25（模拟 `_adopt_fill` 事件流） |
| S-15 | `exposureCapUnreachable=false` 的轮次里 `E(corr_scale × proposed) ≤ cap_abs` **可证明**成立 [P1-3] | T-26 属性测试（随机 200 组 3 成员相关矩阵） |

---

## 四、接口设计

### 4.1 新增纯核心签名（全部无 IO、无 await）

```python
# ai_auto/sizing.py
def parse_sizing_rule(settings) -> SizingRule | None       # 总闸① 关 ⇒ None      [P1-1]
def parse_stop_rule(settings) -> StopRule | None           # 总闸② =0 ⇒ None，**与总闸①独立** [P1-1]
def realized_vol_bar(closes: Sequence[float], window: int) -> tuple[float | None, str]  # (σ_bar, vol_source)
def annual_to_bar_vol(vol_target_annual: float, bar_seconds: int) -> float | None
def kelly_fraction(*, p_win: float, take_bps: float, cost_bps: float,
                   stop_bps: float) -> tuple[float | None, str]      # [P0-2] 收括号，不收 edge_bps
def resolve_stop_distance(*, atr_px, ref_px, rule: StopRule | None,
                          max_sl_pct: float, max_tp_pct: float) -> StopPlan | None
def resolve_size_fraction(inputs: SizingInputs, rule: SizingRule,
                          bar_seconds: int) -> SizingOutcome
def sizing_wire(outcome: SizingOutcome, plan: StopPlan | None, stop_source: str,
                brake: BrakeView, exposure: ExposureView | None) -> dict
def check_window_capacity(rule: SizingRule, window_max: int) -> str | None   # 启动期对账 [P1-6]

# ai_auto/drawdown_brake.py
def parse_brake_rule(settings, bar_seconds: int) -> BrakeRule | None      # 全关 ⇒ None
def advance_equity(state: BrakeState, equity: float, rule: BrakeRule) -> BrakeState
def note_close(state: BrakeState, pnl: float, rule: BrakeRule, *, now_wall: float) -> BrakeState
def evaluate_brake(state: BrakeState, rule: BrakeRule, *, now_wall: float) -> BrakeView
def apply_brake_gate(decision: TradeDecision, view: BrakeView
                     ) -> tuple[TradeDecision, str | None]
def brake_wire(view: BrakeView) -> dict

# ai_auto/exposure_book.py
class GroupExposureBook: ...        # 见 §3.5
def solve_exposure_scale(*, a: float, b: float, c: float,
                         cap_abs: float) -> tuple[float, bool]    # (s, unreachable)  [P1-3]

# ai_auto/sizing_runtime.py（模块级；见 §3.4）
def can_override_risk(executor) -> str | None                     # [P0-1]
def apply_risk_override(executor, plan: StopPlan | None) -> str    # 返回 stopSource

# ai_auto/sizing_text.py
def format_sizing_charter(rule: SizingRule | None, stop: StopRule | None) -> str  # 两者皆 None ⇒ ""
def format_sizing_for_prompt(plan: dict | None) -> str           # 键缺失 ⇒ ""
def describe_brake_level(level: str) -> str                      # 中文（后端侧文案真源）
```

### 4.2 运行时载体（会话唯一调用面）

```python
# ai_auto/sizing_runtime.py
class SizingRuntime:
    def __init__(self, rule: SizingRule | None, stop_rule: StopRule | None,
                 brake_rule: BrakeRule | None,
                 *, inst_id: str, bar_seconds: int,
                 book: GroupExposureBook | None = None) -> None

    # 门控（第 7 步）——session 侧一行
    def gate(self, decision, degrade: str | None) -> tuple[TradeDecision, str | None]

    # 求解（第 11 步）——session 侧两行（求解 + apply_risk_override）
    def resolve(self, decision, ctx: SizingContext) -> tuple[TradeDecision, StopPlan | None, dict]

    # 喂数
    def note_equity(self, equity: float) -> None
    def note_close(self, trade: dict, *, now_wall: float) -> None
    def note_candles(self, closes: Sequence[float], as_of_ts: int) -> None
    def note_notional(self, signed_notional: float) -> None

    # 只读
    def plan_wire(self) -> dict | None       # payload["sizingPlan"]（未装配 ⇒ None ⇒ 整键缺席）
    def merge_into(self, ai_stats: dict) -> dict
```

`SizingContext` 是聚合形参（满足 `CLAUDE.md` 形参 ≤5）：`{ref_px, atr_px, p_win, cost_bps, step, now_wall, notional_full}`。

**取数契约（[P0-2] 后重写）**：

| 量 | 取自 | 是否受 `k_min` 门控 |
|---|---|---|
| `p_win` | `PriorRuntime.coverage_wire()`（`prior_runtime.py:120`）按方向取 `p`；先验层未装配 ⇒ `None` ⇒ `kellyStatus="no_prior"` | 否 |
| `cost_bps` | `CostRuntime.estimate_cost_only(decision, bar_seconds=…)`——**新增的只读入口**，内部直接调**同一个**纯核心 `estimate_round_trip_cost(query_for_decision(...), resolve_cost_params(settings))`（`cost_model.py`，`edge_gate.evaluate_edge_gate` 用的也是这两行） | **否**（见下） |
| `take_bps` / `stop_bps` | §3.4 本轮 `StopPlan`，取不到则退回 `executor.risk` 的当前一对 | 否 |

**v1 的 `k_min` 耦合已被消除，Q-2 判定为「加」。** v1 让 `edge_bps` / `cost_bps` 都走 `CostRuntime.evaluate`，而该方法在 `k_min <= 0`（**默认值**）时返回 `None`（`cost_runtime.py:61`、`edge_gate.py:210-212`），于是凯利项默认恒不可算，runbook 只好加一级 L2.5「先把 `k_min` 打开」。那是一笔糟糕的交易：**为了让一个仓位特性生效，运维必须先打开另一层的、会真实否决开仓的资金门禁**。而消除耦合的代价极低——`k_min` 早退只发生在编排函数 `evaluate_edge_gate` 里，两个估算纯核心 `estimate_round_trip_cost` / `estimate_gross_edge_bps` 都是模块级、无门控、可直接 import 的。故：

- `cost_runtime.py` 新增 `estimate_cost_only()`（约 +12 行），**复用同一对纯函数、同一份 `resolve_cost_params(settings)`**，因此**不是第二条估算路径、不存在口径漂移**——这正是 v1 拒绝该方案时担心的唯一风险，而它建立在"新增入口=新增估算"的误判上。
- 资金费率快照在 `k_min=0` 时不刷新（`cost_runtime.py:31-34` 的 `enabled` 条件），故此路径拿到的成本 `confidence="degraded"`。**如实透传**进 `sizingPlan.costConfidence`，不做任何补齐；`degraded` 不影响凯利可算性（成本只是偏保守）。
- `edge_bps` **根本不再需要**（[P0-2]：赔率的分子改由本层自己的括号给出），故 `SizingRuntime` 与 `edge_gate` 之间**零依赖**——S-12 用源码级 grep 守这条。

**净收益**：四个总闸恢复真正相互独立（§4.4 的独立性声明因此才成立），runbook 的 L2.5 一级被删除，凯利项不再要求运维先开一道会拦单的门禁。

### 4.3 WebSocket

- 新增 phase：`agent_trace` 事件的 `phase: "sizing"`（形状见 §3.9）。老前端忽略未知 phase，向后兼容。
- 无新增事件类型、无新增 REST 端点、无既有端点的响应形状变更。

### 4.4 配置（全部 `AI_TRADE_` 前缀，已在 `desktop/src/lib/config.js` 的 prod 透传白名单内）

| 字段 | 默认 | 关闭值 | 建议生产值 | 说明 |
|---|---|---|---|---|
| `ai_trade_sizing_enabled` | `False` | `False` | `True` | 总闸①：波动率目标化仓位 |
| `ai_trade_vol_target_annual` | `0.20` | `0.0` | `0.20` | 年化波动率目标（fraction） |
| `ai_trade_realized_vol_window` | `96` | — | `96` | 已实现波动率回看根数（15m ⇒ 一整天） |
| `ai_trade_kelly_lambda` | `0.25` | `0.0` | `0.25` | 凯利缩放（四分之一凯利） |
| `ai_trade_stop_atr_mult` | `0.0` | `0.0` | `1.5` | 总闸②：ATR 止损倍数；0 ⇒ 逐字回退固定 pct |
| `ai_trade_stop_rr` | `2.0` | — | `2.0` | 盈亏比（止盈 = rr × 止损） |
| `ai_trade_effective_exposure_cap_mult` | `0.0` | `0.0` | `1.5` | 总闸③：有效敞口上限（单标的满仓名义额的倍数）；`{0} ∪ [1,10]` |
| `ai_trade_correlation_min_samples` | `96` | — | `96` | 相关性最小重叠样本；不足 ⇒ ρ=1 |
| `ai_trade_brake_slow_dd_pct` | `0.0` | `0.0` | `0.08 × 3/leverage` | 总闸④a：第一档减速回撤阈值（**按杠杆定标**，[P1-4]） |
| `ai_trade_brake_slow_size_mult` | `0.5` | `1.0` | `0.5` | 第一档仓位系数（注意档位地板可能吃掉它，[P1-2]） |
| `ai_trade_brake_halt_dd_pct` | `0.0` | `0.0` | `0.15 × 3/leverage` | 总闸④b：第二档「只平不开」回撤阈值（**按杠杆定标**） |
| `ai_trade_brake_loss_streak` | `0` | `0` | `4` | 总闸④c：连亏熔断笔数 |
| `ai_trade_brake_loss_cooldown_bars` | `4.0` | — | `4.0` | 连亏冷却时长（× 一根 K 线周期） |

**四个总闸相互独立、可分别回退**，与路线图 08 §五的回滚开关清单逐条对应（`sizing_enabled` / `stop_atr_mult=0` / `effective_exposure_cap` 置关闭 / 刹车三级各自独立）。

**独立性的两条硬保证（[P1-1]/[P0-2] 之后才真正成立）**：
1. `stop_atr_mult` 由 `parse_stop_rule` **独立**解析，**不受** `sizing_enabled` 门控——否则 runbook 的 L1「只开 ATR 止损」恒不可达。
2. 凯利项**不再依赖** `ai_trade_edge_gate_k_min > 0`（§4.2）——否则总闸①实际上有一个跨层的隐藏前置开关，"可分别回退"是假的。

---

## 五、数据模型

**零数据库迁移、零新表、零新列。** 全部新增状态为进程内瞬态：

| 结构 | 生命周期 | 持有者 | 落库 |
|---|---|---|---|
| `SizingRule` / `StopRule` / `BrakeRule` | 分组存续期，不可变 | `AiTradeGroup` → 各成员 `SizingRuntime` | 随既有 `strategy_runs.config_json` 的 free-form 键落库（`manager._create_group_run`，`manager.py:608-640`，镜像 `baseRules` / `profitGuard` 的既有写法） |
| `BrakeState` | 会话存续期 | `SizingRuntime` | 不落库；聚合读数经 `metricsJson.aiStats.sizing` 落库 |
| `GroupExposureBook` | 分组存续期 | `AiTradeGroup` | 不落库 |
| `sizingPlan` wire | 单轮 | payload / trace | 随既有决策记录落库（无新字段） |

`AiTradeGroup`（`manager.py:154-169`）新增三个可选字段：`sizing_rule: SizingRule | None = None`、`stop_rule: StopRule | None = None`、`exposure_book: GroupExposureBook | None = None`。

**`AiTradeGroupStartRequest` 一个字段都不加 [P1-7]。** v1 同时写了「给启动请求加两个字段」「规则由 manager 从 settings 解析」「API 层零改动」——这三条互斥。事实是 `base_rule` / `profit_rule` 是在 **API 层**解析后塞进请求的（`api/ai_auto.py:228-229` 的 `_resolve_base_rules(body.baseRules, settings)`），而那里正是 `AiTradeGroupStartRequest` 的**唯一**构造方；照 v1 加的两个字段将永远没人填，规则恒 `None`，整个特性是死的且不报错。

正确写法：本轮**不给启动弹窗暴露**（与阶段一 `k_min` 的 env-only 落地方式一致），故三条规则由**已经持有 `settings` 的两侧各自解析**：

- 实盘：`AiTradeManager._build_member` 内 `parse_sizing_rule(settings)` / `parse_stop_rule(settings)` / `parse_brake_rule(settings, bar_seconds)`——`settings` 在 `manager.py:599` 已在作用域内。
- 回测：`AiBacktestRunner.__init__` 内同三行——`self.settings` 已存在（`backtest.py:334` 已在用）。

这才是真正的「API 层与前端启动请求零改动」，且未来要把规则提到弹窗时，只需在 API 层加一次解析、与 `baseRules` 走同一条既有路径。

---

## 六、文件 / 模块改动计划

### 6.1 新增（后端 5 个模块）

| 文件 | 预估行数 | 一句话意图 |
|---|---|---|
| `services/quant/app/ai_auto/sizing.py` | ~215 | 仓位公式纯核心：两条规则解析、已实现波动率（含 `vol_source`）、年化折算、凯利项（括号同源）、ATR 止损距离、取小与吸附、地板反噬检测、窗口容量对账、wire |
| `services/quant/app/ai_auto/drawdown_brake.py` | ~120 | 三级刹车纯核心：规则解析、权益/平仓推进、判定、门禁、wire（含 `unrealizedShare`） |
| `services/quant/app/ai_auto/exposure_book.py` | ~110 | 分组共享的收益率 / 名义额登记簿与有效敞口计算（纯同步，无锁）+ `solve_exposure_scale` 闭式解 |
| `services/quant/app/ai_auto/sizing_runtime.py` | ~165 | 会话级运行时载体（镜像 `ProfitGuardRuntime` / `CostRuntime`）+ `can_override_risk` / `apply_risk_override` |
| `services/quant/app/ai_auto/sizing_text.py` | ~55 | 宪章段 / 每轮 prompt 段 / 中文文案（渲染与判定同源） |

### 6.2 修改（后端 8 个真改 + 2 个「明确不改」的守门行，全部标注硬性行数上限）

| 文件 | 现行数 | 净增上限 | 改动点 |
|---|---|---|---|
| `services/quant/app/config.py` | 414 | +30 | §4.4 的 14 个字段 + 段注释 |
| `services/quant/app/ai_auto/session.py` | 1468 | **+26（硬）** | 构造 runtime；第 7/11 步两个薄封装；`_emit_equity` / `_dispatch` / `feed_candle` 三个喂数点各一行；`_finalize_run` 统计合并一行；`_emit_sizing_trace`。[P1-8] 的 `_dispatch` 喂数点比 v1 多一行，故上限由 +24 放到 **+26** |
| `services/quant/app/ai_auto/manager.py` | **650** | +32 | 解析三条规则（[P1-7]：**在 manager 内从 `settings` 解析**，不经启动请求）、建 `GroupExposureBook`、启动期窗口容量对账（[P1-6]）、透传进 `AiTradeSession`、`config_json` 落库、宪章段注入 |
| `services/quant/app/ai_auto/backtest.py` | 985 | **+24（硬）** | 同位插入两步；`_atr_series` 构造条件扩展（**复发点一**）；**`set_risk` 双写**（**复发点二**，[P0-3]）；三条规则解析；两个喂数点；`_SIZING_BACKTEST_NOTE` |
| `services/quant/app/ai_auto/trace.py` | 200 | +34 | `sizing_event` / `apply_sizing_trace` / `PHASES` 追加 `"sizing"` / `new_round_trace` 新增 **16** 个初值键 |
| `services/quant/app/ai_auto/prompts.py` | 703 | +18 | `_format_sizing` 渲染段 + `build_decision_prompt` 插入点（`prompts.py:501` 之后）+ `_size_discipline` 追加一句 + 系统 prompt 宪章形参 |
| `services/quant/app/ai_auto/cost_runtime.py` | 130 | +14 | `estimate_cost_only()` 只读估算入口（[P0-2]/§4.2：复用同一对纯核心，非第二条路径） |
| `services/quant/app/ai_auto/stats_ext.py` | 63 | +25 | `build_sizing_stats` / `merge_sizing_stats`（键冲突即抛） |
| `services/quant/app/ai_auto/signals.py` | 335 | **0** | **不改**（`quantize_size_fraction` 直接被新模块 import） |
| `services/quant/app/ai_auto/edge_gate.py` | 266 | **0（硬）** | **不改，且新模块不得 import 它**（S-12） |
| `services/quant/app/engine/simulator.py` | 302 | **0（硬）** | **不改**——`set_risk` 已存在（`simulator.py:123`），[P0-3] 只需在 `backtest.py` 调它 |
| `services/quant/app/live_auto/executor.py` | 895 | **0（硬）** | **一行不改**（§3.4 构造缝） |
| `.env.example` | — | +18 | 14 个 env + 分段注释 |

> **欠账登记 [P2-8]**：`session.py` 现已 **1468** 行，超 `CLAUDE.md` 的 800 行红线 **668** 行；`backtest.py` 985、`executor.py` 895 同样超线。本轮通过「纯核心 + 运行时载体」把新逻辑全部挡在 `ai_auto/*` 的新模块里，对三个超线文件只留薄封装，但**债务本身在扩大**。§十 的标记方案已把「`session.py` 拆分」列入 `00` 号已知欠账，本轮不做。

### 6.3 前端（4 个文件，**8** 个镜像点——阶段二第一步 P0-1 的清单逐条对齐）

| # | 文件 | 编辑点 | 不做会怎样 |
|---|---|---|---|
| 1 | `apps/web/lib/aiTradeTypes.ts:186` | `AiTradeTracePhase` 联合类型加 `"sizing"` | 事件被 TS 判为非法 phase |
| 2 | `apps/web/lib/aiTradeTypes.ts:255` | `AiTradeTraceEvent` 加 **16** 个可选字段（§3.9 的事件形状） | 事件字段读不到 |
| 3 | `apps/web/lib/aiTradeTypes.ts:320` | `AiTradeRoundTrace` 加同名 16 个字段 | 轮次对象存不下 |
| 4 | `apps/web/lib/store.ts:119` | `emptyRoundTrace` **逐键手抄**加 16 个 `null` 初值 | **后端发什么前端都收不到，跑不出错、功能静默缺失**（这是第一步踩过的原坑） |
| 5 | `apps/web/lib/store.ts:168` | `reduceRoundPhase` 加 `case "sizing"` 分支 | 同上 |
| 6a | `apps/web/lib/aiTrade.ts` | 新增 `describeSizing()`（返回 `{label, tone}`，未启用 ⇒ `null`） | 无中文化视图 |
| 6b | `apps/web/lib/aiTrade.ts:441-458` | `DEGRADE_REASON_LABELS` 追加两条**前缀**：`drawdown_brake_halt` / `loss_streak_cooldown`（两码都带参数后缀，与既有 `edge_below_cost` 同风格） | 降级码以裸英文怼到用户脸上 |
| 7a | `apps/web/components/Agent/AiTradeTraceRound.tsx` | 在 `EdgeGateBlock`（`:247-275`）之后**定义** `SizingBlock`；未启用 ⇒ `return null` 整段不渲染 | 无组件 |
| 7b | `apps/web/components/Agent/AiTradeTraceRound.tsx:536` | 在 `<ProfitGuardBlock trace={trace} />` **之后挂载** `<SizingBlock trace={trace} />` | **组件写好了但不在渲染树里——数据到了、代码全绿、界面上什么都没有** |

> **注 [P2-3]**：第 6、7 两项各自都是**两个**编辑点。基础策略铁律层终审 RV-1 的教训正是「新中文化 helper 漏接展示点」，本仓库也有过「组件已定义但未挂载」的同类事故——凡"新增一个展示单元"，**定义**与**挂载**必须分两行验收，`tsc` 与 vitest 都不会替你发现少挂了一个。

### 6.4 测试（新增 6 个后端 + 2 个前端）

| 文件 | 覆盖 |
|---|---|
| `services/quant/tests/test_ai_trade_sizing.py` | T-1/T-2/T-3/T-5、公式数值、量纲折算、档位吸附、分辨率声明 |
| `services/quant/tests/test_ai_trade_drawdown_brake.py` | 三档触发/解除、滞回、连亏计数三分支、并集不累加（T-14） |
| `services/quant/tests/test_ai_trade_exposure_book.py` | T-6/T-7/T-8、符号相消、对齐、缩减系数只缩不放 |
| `services/quant/tests/test_ai_trade_sizing_session.py` | 会话集成：插入位置、`executor.risk` 覆盖生效、T-4 零回归、T-9 平仓永不被拦 |
| `services/quant/tests/test_ai_trade_sizing_backtest.py` | 回测同位插入、`_atr_series` 构造条件（**复发点回归测试**）、免责文案 |
| `services/quant/tests/test_ai_trade_sizing_stats.py` | 统计加法键、键冲突即抛、`_finalize_run` 调用点合并 |
| `apps/web/lib/aiTradeSizing.test.ts` | `describeSizing` 四态 + 两条降级码中文化 + store 归约（含 `emptyRoundTrace` 硬断言） |
| `apps/web/components/Agent/AiTradeAgentTrace.test.tsx`（改） | `SizingBlock` 渲染门控：未启用整段不渲染 |

---

## 七、测试与验收标准

### 7.1 单元测试清单（**30** 条：v1 的 22 条 + 评审新增 8 条，编号供评审引用）

| # | 名称 | 判据 |
|---|---|---|
| T-1 | `never_enlarges_model_fraction` | 500 组随机输入，输出 ≤ 输入（属性测试） |
| T-2 | `gated_actions_identical_to_edge_gate` | `sizing._GATED_ACTIONS == edge_gate._GATED_ACTIONS`（源码级） |
| T-3 | `sizing_never_rewrites_action` | 四动作 × 全参数组合，`out.action == in.action` |
| T-4 | `all_switches_off_is_byte_identical` | `decision is` 相等、payload 无键、`executor.risk` 对象未被替换 |
| T-5 | `stop_pct_is_underlying_not_margin` | 同 ATR、5× 杠杆下与 `profit_guard` 阈值比值 ≈ leverage |
| T-6 | `insufficient_overlap_uses_rho_one` | 重叠 < min_samples ⇒ ρ=1，有效敞口取最大值 |
| T-7 | `exposure_book_has_no_await` | AST 扫描模块内无 `AsyncFunctionDef` / `Await` |
| T-8 | `single_member_group_never_capped` | n=1、cap_mult=1.0 ⇒ corr_scale ≡ 1.0 |
| T-9 | `close_and_cancel_never_blocked` | 四动作 × 三档刹车 ⇒ `close`/`cancel_order` 被拦次数 = 0 |
| T-10 | `vol_target_annual_to_bar_conversion` | 15m/1h/4h 三周期折算值与手算一致 |
| T-11 | `kelly_missing_prior_is_not_binding` | `p_win=None` ⇒ 凯利项不参与 min、`kellyStatus="no_prior"` |
| T-12 | `kelly_negative_odds_is_not_binding` | `b<=0` ⇒ `no_bracket`，**不归零、不否决** |
| **T-11b** | `kelly_never_reads_edge_gate` | 源码级：`sizing.py` 的 AST 里无 `edge_gate` import、无 `edge_bps` 标识符（S-12，[P0-2]） |
| **T-12b** | `kelly_odds_track_the_placed_bracket` | 改 `take_bps`/`stop_bps` ⇒ `b` 随之变；构造一个 `edge_bps` 与括号严重背离的断面 ⇒ `f_kelly` **不受影响** |
| **T-17b** | `risk_override_skipped_while_resting` | `executor.resting is not None` ⇒ `executor.risk` 对象 `is` 相等、`stopSource=="skipped_resting"`（S-11，[P0-1]） |
| **T-17c** | `risk_override_skipped_while_position_live` | `projected != "flat"` ⇒ 同上，`stopSource=="skipped_position_live"` |
| **T-19b** | `backtest_atr_stop_actually_moves_fill` | 两根不同 ATR 下开的仓，`pos.stop_loss_price/entry_price` 比值**不同**；只改 `_oco_risk` 不调 `set_risk` 的写法必须让该测试**红**（S-13，[P0-3]） |
| **T-23** | `quantize_floor_erases_brake_is_detected` | `model=0.25, brake=0.5` ⇒ `finalFraction==0.25` 且 `scaleErased is True`、计数 +1（[P1-2]） |
| **T-24** | `window_capacity_checked_at_startup` | `vol_window+1 > window_max` ⇒ 启动抛 `AI_TRADE_SIZING_WINDOW_TOO_SMALL`（422），**不是**运行期静默 `None`（[P1-6]） |
| **T-25** | `notional_published_from_dispatch` | 走 `_adopt_fill` 路径的成交事件 ⇒ 敞口簿立刻可见（不必等下一轮决策，[P1-8]） |
| **T-26** | `exposure_scale_satisfies_cap` | 200 组随机 3 成员相关矩阵：`unreachable=False` 时 `E(s·proposed) ≤ cap_abs` 恒成立（S-15，[P1-3]） |
| **T-27** | `exposure_cap_unreachable_never_vetoes` | `c ≥ cap²` ⇒ `corr_scale==0`、`exposureCapUnreachable is True`、**`action` 不变**（S-3 与 [P1-3] 的交叉点） |
| T-13 | `brake_halt_uses_hysteresis` | 跨越 halt 后在 `slow~halt` 之间震荡不解除；跌回 slow 以下才解除 |
| T-14 | `brake_and_profit_cooldown_are_union` | 两层同时激活 ⇒ 只出现先命中者的码，且总拦截效果 = 并集 |
| T-15 | `loss_streak_three_branches` | `pnl>0` 归零 / `<0` 累加 / `==0` 不动 |
| T-16 | `atr_stop_clamped_to_existing_caps` | 极大 ATR ⇒ `stop_pct` 被夹到 `ai_trade_max_sl_pct` |
| T-17 | `atr_unavailable_falls_back_verbatim` | ATR 为 NaN ⇒ `executor.risk` 一行不动 |
| T-18 | `quantize_resolution_documented` | `f_raw=0.03, step=0.25` ⇒ 结果 0.25（分辨率声明的可执行版本） |
| T-19 | `backtest_atr_series_built_for_stop` | `stop_atr_mult>0` 且无 `profit_rule` 且 `k_min=0` ⇒ `_atr_series is not None` |
| T-20 | `backtest_same_insertion_order` | 回测与实盘的门禁调用序列一致（记录调用顺序比对） |
| T-21 | `sizing_stats_merge_conflict_raises` | 键冲突 ⇒ 抛，不静默覆盖 |
| T-22 | `payload_key_absent_when_unconfigured` | 未装配 ⇒ `"sizingPlan" not in payload` 且 prompt 逐字节等于基线 |
| **T-28** | `stop_rule_independent_of_sizing_switch` | `sizing_enabled=False` 且 `stop_atr_mult=1.5` ⇒ `parse_stop_rule` 非 None、ATR 止损**生效**（L1 可达，[P1-1]） |
| **T-29** | `kelly_works_with_k_min_zero` | `k_min=0`（默认）⇒ `estimate_cost_only` 仍返回成本、`kellyStatus=="ok"`（[P0-2] 解耦，§4.2） |
| **T-30** | `brake_threshold_scaled_by_leverage` | 同一标的价反向幅度下，1× 与 3× 会话的 `brakeLevel` 按 §3.6 折算表一致（[P1-4]） |

### 7.2 合并放行条件（G-1 ~ G-14）

| # | 条件 |
|---|---|
| G-1 | **零回归**：全部开关取默认（关闭）值时，后端全量测试与合并前的红灯基线**逐条相同**（当前基线：7 个 `test_factor_library_expansion` 孤儿测试 + 2 个 agent 计时 flake） |
| G-2 | S-1 属性测试（T-1）在产出层成立，且仓库全局 grep 确认**不存在**任何为本层单设的放大路径 |
| G-3 | 行数硬上限（S-10）经 `git diff --numstat` 机械核对：`executor.py` / `edge_gate.py` / `engine/simulator.py` 净增 **0**、`session.py ≤ +26`、`backtest.py ≤ +24` |
| G-4 | 6.3 表的 **8 个前端编辑点全部落地**，其中三处为硬判据（有独立断言，不靠 grep）：`store.ts` 的 `emptyRoundTrace`、`reduceRoundPhase`，以及 `AiTradeTraceRound.tsx:536` 的 **`<SizingBlock/>` 挂载**（[P2-3]） |
| G-5 | `_atr_series` 构造条件扩展有独立回归测试（T-19）——阶段一 I-5 的复发防线 |
| G-6 | 回测免责文案 `_SIZING_BACKTEST_NOTE` 与前端展示同源（沿用既有同源机制），四条偏差逐条可见 |
| G-7 | 平仓永不被拦（T-9）+ 撤单永不被拦，二者均有测试；`profit_guard` 的平仓路径不经决策管线一事在代码注释中显式说明 |
| G-8 | `exposure_cap_mult` 的 `{0} ∪ [1,10]` 校验在**启动期**生效（越界 422），不是运行期静默夹紧 |
| G-9 | 前端 `tsc` 只剩既有的 `WeightBatchArena.test.tsx` pre-existing 错误；`ruff` 在全部改动文件上干净 |
| G-10 | 提交只含本特性精确路径；混合文件按 hunk 行级暂存（`git apply --cached --recount`，patch 文件须用 **bytes 读写**，`write_text` 在 Windows 会翻 CRLF 弄坏 patch） |
| **G-11** | **[P0-1]** `apply_risk_override` 的两条状态前置条件有独立测试（T-17b/T-17c，断言 `executor.risk` 对象 `is` 相等）；仓库全局 grep 确认 `executor.risk` 的赋值点**有且只有一处** |
| **G-12** | **[P0-2]** S-12 的源码级判据通过：`sizing.py` 不 import `edge_gate`、不出现 `edge_bps`；且 T-29 证明 `k_min=0` 下凯利项可算（否则四总闸独立性是假的） |
| **G-13** | **[P0-3]** T-19b 存在且**先红后绿**：先按「只改 `_oco_risk`」实现让它红，再补 `set_risk` 让它绿——这是复发点回归测试有效性的唯一证明（与 G-5 的 `_atr_series` 同款验收方式） |
| **G-14** | **[P1-1]/[P1-6]** T-28 证明 L1 可达；T-24 证明窗口容量在启动期拦截（422）而非运行期静默 `None`。这两条是「runbook 写的步骤真的能被执行」的前置条件 |

### 7.3 上线验收指标（对齐 05 §五）

| 指标 | 阈值 | 采样窗口 | 不达标处置 |
|---|---|---|---|
| 仓位-波动率相关性 | **`fFinalRaw`**（吸附前）与 `σ_bar` 的秩相关 < −0.3 | 滚动 100 笔 | 目标化未生效，检查 `vol_target` 标定。**[P2-7]：绝不能对吸附后的 `finalFraction` 算**——默认 `step=0.25` 下它只有 2~3 个取值，秩相关在统计上不可评估 |
| 被扫损率 | 止损触发后 24 根内价格回到原方向的比例 < 40% | 滚动 100 笔 | 上调 `stop_atr_mult` 至 2.0 |
| 名义 RR vs 实测 RR | 实测「均盈/均亏」≥ 名义 `stop_rr` 的 50% | 滚动 100 笔 | **[P0-2] 的诚实性校验**：凯利用挂出去的止盈当 `W` 是乐观上界；实测长期低于一半说明持仓多被提前平掉，**下调 `kelly_lambda`**（而不是继续用乐观分子） |
| 有效敞口 | `exposureCapUnreachable=false` 的轮次里 `E ≤ cap_abs` **恒**成立 | 实时 | 闭式解有 bug（[P1-3]）；`unreachable` 轮单独计数并观察，不计入本判据 |
| 最大回撤 | < 预算的 `15% × 3/leverage` | 实时 | 刹车必须已在第一档阈值处减速过；未减速即判定刹车失效（阈值按 §3.6 折算表，[P1-4]） |
| 第一档减速的**有效**触发 | 触发轮里 `scaleErased=false` 占比 ≥ 50% | 滚动 100 轮 | **[P1-2]**：否则减速被档位地板吃掉了，把 `positionStepPct` 调到 0.1 再观察，**不得**据 `brakeSlowRounds` 宣称刹车已验证 |
| halt 触发的浮盈占比 | 触发轮 `unrealizedShare` 均值 < 0.8 | 滚动 200 轮 | **[P1-5]**：说明刹车几乎只被利润保护层设计内的浮盈回吐触发，上调 `halt_dd_pct` |
| 连亏熔断误触率 | 每 200 轮触发 < 3 次 | 滚动 200 轮 | 上调 `loss_streak` 至 5 |
| `scaleSource` 分布 | `"model"` 占比 < 80% | 滚动 100 轮 | **[P2-6]**：两项约束从未真正绑定 ⇒ 本层是装饰品（§9.1 自我反驳二的对偶监控）。v1 用的「仓位恒等于 `f_max` 不得出现」近乎恒真——`f_max=1.0` 还要与被 prompt 引导到 ≤0.5 的模型提议取小——作为监控是失效的 |
| 平仓被拦次数 | = 0 | 每次提交 | 阻断合并 |

---

## 八、上线 runbook（L0 → L4）

沿用阶段一 / 阶段二第一步的分级观察范式：**合并即全关，逐级打开，每级有独立判据与回滚动作**。

| 级 | 打开什么 | 观察什么（在哪看） | 放行判据 | 不达标动作 |
|---|---|---|---|---|
| **L0** | 全关（合并态） | 全量测试红灯基线一致；`sizingPlan` 键不存在 | 与 §7.2 G-1 相同 | 阻断 |
| **L1** | `ai_trade_stop_atr_mult = 1.5`（**只开 ATR 止损，不开总闸①**——[P1-1] 之后这一级才真的可达，见 T-28） | `trade.stopLossPct` 分布；`sizing` 轨迹的 `stopPct` / **`stopSource` 五态分布** | 50 笔内 `stopSource="atr"` 覆盖率 ≥ 90%；被扫损率 < 40% | 回 0，逐字回退固定 pct |
| **L1.5** | 不改配置，**只看 `stopSource` 的三个 skip 态** | `restingSkips` / `reversalSkips` 占比（[P0-1] 的代价度量） | 两者合计 < 20%——超过说明限价挂单/反手太频繁，ATR 止损实际覆盖不到多数开仓 | 合计 > 20% 时**不要**放宽前置条件（那会写坏成交回执），改为单独立项处理挂单期的止损更新 |
| **L2** | 追加 `ai_trade_sizing_enabled = True`（**`k_min` 保持 0 即可**——[P0-2] 解耦后凯利项不再依赖它，见 T-29） | `fVol` / `fKelly` / `kellyStatus` 分布；`fFinalRaw` vs `finalFraction`；`scaleRate` | `σ_bar` 实测分布使 `fVol` 落在 0.15~0.6；`kellyStatus="ok"` 占比 ≥ 50%；`scaleRate ∈ [20%, 80%]` | `fVol` 恒 < 最小档 ⇒ 上调 `vol_target`；恒 = 1.0 ⇒ 下调；`kellyStatus` 长期 `no_prior` ⇒ 先验覆盖率不足，回到阶段二第一步的 L3 |
| **L3** | 追加刹车三档，**阈值按 §3.6 折算表取**（3× 杠杆 ⇒ `0.08 / 0.15 / 4`；5× ⇒ `0.048 / 0.09 / 4`，[P1-4]） | `brakeSlowRounds` / `brakeHaltRounds` / `lossStreakCooldowns`；`quantizeErasedRounds`；halt 轮的 `unrealizedShare`；最大回撤 | **三条同时成立**才算验证：① 第一档真的触发过；② 触发轮里 `scaleErased=false` 占比 ≥ 50%（[P1-2]）；③ halt 轮 `unrealizedShare` 均值 < 0.8（[P1-5]） | 只满足 ① 时**不得**声称 M2 达标——那正是「计数在涨但什么也没发生」；先按 §7.3 的处置调 `positionStepPct` 或 `halt_dd_pct` |
| **L4** | 多标的分组时追加 `exposure_cap_mult = 1.5` | `corrScaledRounds`、`effectiveExposure/Cap`、**`exposureUnreachableRounds`** | 缩减发生过；单成员分组读数恒 `corrScale=1.0`；`unreachable` 轮占比 < 5% | `unreachable` 占比高 ⇒ 上调 `exposure_cap_mult` 或减少同向成员数；仍不行则置 0 关闭 |

> **L2.5 已删除 [P0-2]**：v1 的 L2.5 是「先打开 `k_min` 让凯利项可算」。那要求运维为了一个仓位特性去打开另一层**会真实否决开仓**的资金门禁，是本设计里最贵的一个隐藏耦合。§4.2 用一个复用同一对纯核心的只读入口（+14 行）消除了它，这一级随之取消。

**观察通道**（与阶段一相同的既有限制，不要指望 `GET /ai-trade/status`——它只返回 `{group, aiTradeEnabled}`）：
- 会话运行中：订阅 `ws /ws/ai-trade/{sid}`，读 `agent_trace` 的 `phase="sizing"` 事件。
- 会话停止后：`GET /ai-trade/history/{group_id}` 的 `metricsJson.aiStats.sizing`。

---

## 九、风险与回滚

### 9.1 自我反驳（必须写明，逐条给监控信号）

**自我反驳一：本改动可能反而降低收益。** 波动率目标化在趋势行情中会**主动缩小**仓位——而加密永续最赚钱的行情恰恰是高波动的单边趋势。若实测显示缩仓轮次的事后收益显著高于满仓轮次，说明 `vol_target` 对本策略族过低。**监控信号**：按 `finalFraction` 分位分箱统计事后收益的单调性；若最低仓位分箱的事后收益最高，即判定目标化方向错误，正确动作是**回退固定占比**（`sizing_enabled=False`）并改用其他方式控风险，而不是继续调 `vol_target`。

**自我反驳二：四分之一凯利仍可能过激。** 凯利对 `p` 的估计误差极其敏感；阶段二第一步的先验样本量下限只有 `ai_trade_prior_min_samples = 30`，若 `p` 高估 5 个百分点，最优仓位会被高估近一倍。**缓解**：`min()` 里的波动率目标项本身就是一道独立上限，两者取小即使凯利项失真也不会失控。**监控信号的对偶**：`scaleSource` 的分布——若 `scaleSource` 恒为 `"model"`，说明两项约束从未真正绑定，本层是装饰品。

**自我反驳三：三层降级叠加可能把开仓率压到 0。** 本增量在铁律层、冷却层、共识层、edge 门禁、反思层之上再加一道刹车。路线图 08 §六已把「连续 200 轮零开仓」列为明确的回滚触发信号。**缓解**：① 刹车的两个阻断档默认关闭，L3 才打开；② 第 11 步的仓位求解**永不否决**（S-3），把「不该开」这件事完整留给已有的层；③ `sizing` 轨迹每轮都发一条——静默的保守会被用户当成故障，这是本仓库的前科（`ai-trade-reflection-hold-inertia`）。

**自我反驳四：`executor.risk` 的可变替换是一个共享可变状态。**（**[P0-1] 评审后重写——v1 的缓解论证是错的**）它同时被盯市循环里的 `profit_guard_runtime.exchange_oco_px` 读到，且这确实把一个此前只在构造期写一次的字段变成了运行期可写。v1 给的缓解有两条，**两条都不成立**：① "同协程顺序执行、无撕裂读"——`_execute_decision` 第一行就是 `async with self._exec_lock`（`session.py:879`），盯市循环在同一把锁上（`session.py:395`），那是真实的让出点；② "`exchange_oco_px` 的首选真源是成交回执故不受影响"——恰恰相反，那条回执里的 `stopLossPrice` 是 `_adopt_fill`（`executor.py:494`）在**采纳挂单成交时**用**当时**的 `self.risk` 重算的，改写 `risk` 会直接污染这个所谓的"真源"，并随 `_persist_trade` 落库。

**真正的缓解**：把改写窗口收窄到"手上什么都没有"的那一刻——`projected == "flat"` 且 `resting is None`（S-11 / `can_override_risk`）。此时交易所侧不存在任何按旧 risk 挂着的 OCO，改写对任何在飞对象都无影响；不满足即一行不动并如实置 `stopSource`。**监控信号**：`restingSkips + reversalSkips` 占比（L1.5）——若长期 > 20%，说明 ATR 止损覆盖不到多数开仓，正确动作是**单独立项**处理挂单期的止损更新，**绝不是**放宽前置条件。

**自我反驳五：相关性预算的样本来自各成员自己的窗口，跨成员对齐可能长期失败。** 不同标的的 WS 推送节奏不同，确认根落地时刻可能差若干秒。若对齐后重叠样本长期不足，本层将长期按 `ρ=1` 运行——那是**最保守**假设，会持续压低多标的分组的仓位。**监控信号**：`corrScaledRounds` 与 `effectiveExposure` 同时长期贴顶 ⇒ 检查对齐率；对齐率 < 80% 即置 `exposure_cap_mult = 0` 关闭本层，而不是放宽 `ρ`。

**自我反驳六（评审新增，[P1-5]）：刹车可能在惩罚利润保护层的正常工作。** 会话权益含未实现盈亏，故峰值随浮盈推进；而利润保护层**按设计允许**浮盈自峰值回吐 `giveback_pct` 才离场。那段"设计内的回吐"在刹车眼里就是一次真实回撤——于是一个刚刚**赚到钱**的会话，可能因为赚完又吐回一部分而被降速甚至只平不开。两层的目标（让利润跑 / 亏了要减速）在浮盈区间上是直接冲突的。**监控信号**：halt 触发轮的 `unrealizedShare` 均值；> 0.8 说明刹车几乎只被浮盈回吐触发，正确动作是上调 `halt_dd_pct`（或把峰值口径改为已实现，作为独立立项），**不是**关掉利润保护层。**为什么仍然保留 mark-to-market 口径**：只按已实现算会让刹车在最需要它的时刻——一个正在扩大的浮亏持仓——完全瞎掉，那比误触严重得多。

**自我反驳七（评审新增，[P2-9]）：刹车是成员级的，分组整体回撤无人判定。** 三个成员各自 −7% 都不触发第一档，但分组整体已经 −7%。本轮**显式不做**组级刹车：`GroupExposureBook` 已经是分组级共享状态，加一个组级权益聚合在结构上可行，但会引入"谁先判定、判定后谁被降速"的分配问题（等比降？先到先得？），那是一个独立的设计题。**缓解**：`exposure_cap_mult` 从名义额侧限制了分组总敞口，间接压住了组级回撤的上界。**监控信号**：分组内多成员同时进 `slow` 档的轮次占比；若长期 > 30%，说明成员间回撤高度同步，组级刹车值得单独立项。已写进 §十 的欠账条目。

### 9.2 总回滚

四个总闸全部取关闭值 ⇒ 系统**逐字节**回退到 commit `c6db95d` 的行为：无 `sizingPlan` 键、prompt 逐字节相同、`executor.risk` 从不被替换、`decision` 对象在管线两个新挂点上 `is` 相等、trace 恒为初值、无残留状态、**无数据库变更**。

### 9.3 与分册 05 的显式偏离（三条，评审须逐条确认）

| # | 分册 05 原文 | 本设计 | 理由 |
|---|---|---|---|
| O-1 | `invalidation_priority = 模型优先`（模型给出 `invalidationPx` 且更近时取模型值） | **不做** | `invalidationPx` 是分册 04 的决策字段，仓库尚未实现；`TradeDecision.hold_exit_px` 是**利润承诺退出线**（盈利后的退出），语义与「失效价」不同，硬接会把两个不同概念混成一个字段 |
| O-2 | `effective_exposure_cap = 1.5 × 单标的满仓名义额` | 同义，但把「单标的」明确定义为 `(Σ budget_i) × leverage / n`，并加 `{0} ∪ [1,10]` 的启动期校验 | 原文在成员预算不等时有二义；下界 1.0 是单成员零回归的结构性保证 |
| O-3 | 「当日只平不开」 | 改为**滞回带**解除（`dd ≤ slow_dd_pct` 才解除） | 会话生命周期通常短于一天，「当日」无法映射；纯锁存会造出「活着但永不开仓」的僵尸会话 |

---

## 十、`docs/improve/01-ai-autotrading` 标记方案（逐字，实现节点照抄）

> 纪律沿用既有约定：**状态看板的单一真源是 00 号 §七**，各分册只放一行指针 banner，绝不各记一份状态。

**① `00-总纲与现状诊断.md` §七 状态看板表**——新增三行（D-6 / D-12 / D-13 从 `D-3 ~ D-14` 合并行中单列出来，并把合并行的排除项从「除上列，**D-5 已单列**」改为「除上列，**D-5 / D-6 / D-12 / D-13 已单列**」）：

```markdown
| D-6 | 已实现(未提交) | `ai_auto/sizing.py` + `ai_auto/sizing_runtime.py` + `executor.risk` 覆盖缝 | — | 阶段二第二步 a；`AI_TRADE_SIZING_ENABLED` / `AI_TRADE_STOP_ATR_MULT` 默认关；`executor.py` 净增 0 行 |
| D-12 | 已实现(未提交) | `ai_auto/exposure_book.py` | — | 有效敞口上限；样本不足按 ρ=1；单成员分组恒不受约束；回测侧结构上不适用 |
| D-13 | 已实现(未提交) | `ai_auto/drawdown_brake.py` | — | 三级刹车全部只拦开仓；第二档用滞回带解除；与保护层冷却取并集 |
```

**② `00` §七 阶段里程碑表**——`M2 · 阶段二` 行的备注更新为：

```markdown
| M2 · 阶段二 | 进行中（第一步先验体系已合并；第二步 a 仓位与刹车已实现未提交，闸门默认关；第二步 b 因子快照 + 闸门报告未开始） | — |
```

**③ `00` §七「已知欠账」**——追加一条：

```markdown
- 因子快照（D-4）与五道闸门报告（D-14/06 号）仍未开始，故 M2 的「闸 0~闸 3 全绿」出口条件当前**无法评估**；仓位与刹车层的验收只能先看 05 §五的六项运行指标。
- 回撤刹车目前是**成员级**的：分组内各成员各自判定各自的会话权益回撤，组级整体回撤无人判定（3 个成员各 −7% 都不触发第一档，但分组已 −7%）。组级刹车需要先解决「判定后按什么规则分配降速」，属独立立项。见 `docs/plans/ai-trade-vol-sizing-and-drawdown-brake/spec.md` 自我反驳七。
- 刹车的峰值权益是 **mark-to-market** 口径（含未实现），与利润保护层「允许浮盈回吐」的设计在浮盈区间上直接冲突。已用 `unrealizedShare` 读数与 L3 判据兜住，但「浮盈回吐是否应计入会话回撤」本身是一个未决的口径问题。
- `session.py` 已 1468 行、超 `CLAUDE.md` 800 行红线 668 行（`backtest.py` 985 / `executor.py` 895 同样超线）。本轮把新逻辑全部挡在 `ai_auto/*` 新模块里，只留薄封装，但拆分本身仍未做。
```

**④ `05-风险与仓位管理.md`**——在标题下一行插入指针 banner（与 08 号既有 banner 同款）：

```markdown
> **实施状态**：D-6 / D-12 / D-13 见 [00 · 七、实施状态看板](./00-总纲与现状诊断.md#七实施状态看板由实现节点维护单一真源)；设计文档 [`docs/plans/ai-trade-vol-sizing-and-drawdown-brake/spec.md`](../../plans/ai-trade-vol-sizing-and-drawdown-brake/spec.md)。已实现的三条与本篇原文的三处偏离见该 spec §9.3。
```

**⑤ `08-实施路线图与里程碑.md` §3.2 表**——三行状态由 `未开始` 改为 `已实现(未提交)`：

```markdown
| 波动率目标化仓位 + ATR 自适应止损 | 新增 `ai_auto/sizing.py`（约 190 行） | 先验 | 已实现(未提交) |
| 组合相关性预算 + 回撤刹车 | 新增 `ai_auto/drawdown_brake.py` + `ai_auto/exposure_book.py`（约 200 行） | 仓位 | 已实现(未提交) |
```

**⑥ `08` §3.2 表下的拆步说明**——整句替换为：

```markdown
阶段二按依赖拆成三步：**第一步**＝先验体系 + 加权共识 + EV 分子升级（设计见 `docs/plans/ai-trade-signal-prior-and-ev-gate/spec.md`，已合并 `c6db95d`）；**第二步 a**＝波动率目标化仓位 + ATR 自适应止损 + 组合相关性预算 + 回撤刹车（设计见 `docs/plans/ai-trade-vol-sizing-and-drawdown-brake/spec.md`）；**第二步 b**＝因子快照接入 + 五道闸门报告（未开始）。第二步 a 与 b 的拆分理由：仓位公式的 `p` 项依赖第一步刚落地的先验，是「输入刚就位」的一项；而因子快照的主路径是元标签特征（阶段三），本阶段接入只剩 prompt 展示这条辅助支路。
```

**⑦ `08-实施路线图与里程碑.md` §五 回滚开关清单**——把原表中 `sizing_enabled` / `effective_exposure_cap` 两行的占位名替换为真实实现名，并补齐刹车三级：

```markdown
| `ai_trade_sizing_enabled` | 回退 `_compute_sz`（`executor.py:711-723`） | 05 |
| `ai_trade_stop_atr_mult = 0` | 回退固定百分比止损（`config.py:104-105`） | 05 |
| `ai_trade_effective_exposure_cap_mult = 0` | 关闭相关性约束 | 05 |
| `ai_trade_brake_slow_dd_pct / halt_dd_pct / loss_streak = 0` | 三级刹车各自独立关闭 | 05 |
```

---

## 十一、落地顺序（实现节点照此推进，每步可独立跑测）

1. **纯核心先行**：`sizing.py`（含 `parse_sizing_rule` / `parse_stop_rule` 两条独立解析）→ `drawdown_brake.py` → `exposure_book.py`（含 `solve_exposure_scale` 闭式解），配套 T-1~T-18 + T-11b/T-12b/T-23/T-26/T-27/T-28/T-30 单测。此时零集成、零风险。
2. **配置与文案**：`config.py` 14 字段 + `.env.example` + `sizing_text.py`。跑 `test_config.py` 确认无 `extra="ignore"` 静默吞掉。
3. **运行时载体**：`sizing_runtime.py`（含 `can_override_risk` / `apply_risk_override`）+ `cost_runtime.estimate_cost_only`，配 T-17b/T-17c/T-29。
4. **会话集成**：`session.py` 两个挂点 + 三个喂数点（`_dispatch` 喂名义额与连亏，[P1-8]）+ 统计合并；**立刻**核对 `git diff --numstat` 的 +26 上限。
5. **回测同构**：`backtest.py` 同位插入 + **两个复发点**——`_atr_series` 条件扩展（先写 T-19 再改代码）与 `set_risk` 双写（先写 T-19b 让它**红**，再补 `set_risk` 让它绿，[P0-3]/G-13）+ 免责文案（五条偏差）。
6. **分组装配**：`manager.py` 三条规则解析（从 `settings`，**不碰 `AiTradeGroupStartRequest`**，[P1-7]）+ `GroupExposureBook` + 启动期窗口容量对账（T-24）+ `config_json` 落库。
7. **可观测**：`trace.py` + `stats_ext.py` + `prompts.py`。
8. **前端 8 个编辑点**：按 6.3 表逐条落地，**7b 挂载点单独打勾**；最后跑 `tsc` 与 vitest。
9. **全量回归**：后端 `python -u -m pytest -q > log`（**不能并发跑，共用临时 DB**；分块跑绿、全量跑对基线）、前端 vitest + tsc、`ruff`。
10. **文档标记**：§十 的 7 处逐字落地。
11. **精确路径提交**：混合文件按 hunk 行级暂存；工作树已知的他人在制内容（`ai_trade_enabled` 总闸翻转 hunk、`desktop/src/lib/config.js`、`services/quant/app/db/live_auto_repositories.py`、`test_factor_library_expansion.py`、`docs/diagnoses/*`）**一律不带入**。

---

## 十二、待评审拍板项（**评审已逐条裁决**）

| # | 问题 | 设计倾向 | 评审裁决 |
|---|---|---|---|
| Q-1 | 范围裁剪（只做 05，把因子快照与闸门报告推到第二步 b）是否成立？ | **成立**，理由见 §一第三段 | **通过。** 三条理由（依赖顺序 / 回滚边界 / 提交纪律）都站得住，尤其第二条：本三项共享同一个运行时载体与同一组开关，回滚边界干净；把 prompt 装配层的因子快照混进来会让"一键回退"横跨两个语义层。§十 的拆步说明已把第二步 b 的范围与理由写死，不留悬案。 |
| Q-2 | 凯利项依赖 `k_min > 0` 才有 edge/cost 可用（§4.2）。是否要给 `CostRuntime` 加一个不受 `k_min` 门控的只读估算入口？ | ~~倾向不加~~ | **否决原倾向，判定为「加」。** 拒绝的唯一理由是"多一条估算路径 ⇒ 口径漂移"，但这建立在"新增入口 = 新增估算"的误判上：`estimate_round_trip_cost` / `resolve_cost_params` 都是模块级无门控纯函数，`k_min` 早退只发生在编排层 `evaluate_edge_gate`（`edge_gate.py:210-212`）。复用同一对纯函数**不产生第二份口径**。而不加的代价极重：运维为了一个仓位特性必须先打开另一层**会真实否决开仓**的资金门禁，且 §4.4 的"四总闸相互独立"因此是假的。已在 §4.2 落地（`cost_runtime.py` +14 行），L2.5 一级随之删除。与 [P0-2] 合并处理。 |
| Q-3 | `f_max` 是否要新增独立配置？ | **不新增**，沿用 1.0（既有 `clamp01` 上界） | **通过。** 路线图明写「不新增红线」；且 `f` 还要与模型提议取小、再经档位吸附、最后受 `manager.py:557-567` 的分组天花板约束，四道封顶已经足够。 |
| Q-4 | 刹车的两个阻断档默认关闭（`0.0`），而 05 号给的建议值是 8%/15%。是否直接默认开启？ | **倾向默认关** | **通过（默认关），但建议值本身必须改。** 默认关是本仓库全部确定性层的既有约定，G-1 的零回归判据依赖它。**但** 05 号那两个值是按无杠杆权益写的，3× 下等价于标的价 2.7% / 5.0%——照搬会让普通回调把会话打成僵尸。见 [P1-4]：建议值改为按杠杆折算，并在 L3 用实测回撤分布复核。 |
| Q-5 | `sizing` 是否值得新增一个 trace phase（代价 = 7 个前端编辑点）？ | **值得** | **通过。** 求解在轮次末尾、搭不上 `round_start` 是硬约束；且本层新增了 5 个"看起来生效但可能没生效"的观测量（`scaleErased` / `stopSource` 的三个 skip 态 / `exposureCapUnreachable` / `volSource` / `unrealizedShare`），它们**必须**每轮可见——静默的保守是本仓库的前科。代价经 [P2-3] 拆分后是 **8** 个编辑点，仍然值。 |

**评审新增的两个已裁决项**（原设计未列出，但影响实现路径）：

| # | 问题 | 裁决 |
|---|---|---|
| Q-6 | 反手轮（`projected != "flat"` 时开新仓）拿不到 ATR 止损，是否要为它开一条例外路径？ | **不开。** 例外路径意味着在持仓存续期改写 `risk`，会污染成交回执与 `exchange_oco_px`（[P0-1]）。退回当前固定 pct 是 fail-safe：拿到的是一个真实、且与随后挂出的 OCO 一致的距离。占比进 `reversalSkips` 观测，L1.5 超 20% 再单独立项。 |
| Q-7 | 档位地板吃掉刹车（[P1-2]）是否要为本层单开一条绕过 `quantize_size_fraction` 的路径？ | **绝不。** 档位是用户显式配置的硬约束，绕过它就是用系统内部逻辑推翻用户设置——这条在 §3.3 已是冻结项。正确做法是**如实暴露**（`scaleErased` + `fFinalRaw`）并在 runbook 里把"调 `positionStepPct`"写成运维动作。 |

---

## 十三、评审结论

### 结论：**有条件通过**

v1 是一份质量相当高的设计文档：分层范式与仓库已跑了两轮的 `ProfitGuardRuntime` / `CostRuntime` 严格同构；量纲表、不变式清单、自我反驳、回滚开关、标记方案、落地顺序一应俱全；**引用的每一处源码坐标都对着当前工作树核验过，除 `manager.py` 行数差 1 外全部准确**。它的问题不在"引用漂移"，而集中在同一个类型：**把「代码看起来会这样工作」当成了「代码确实这样工作」**——三个 P0 都是照着它逐字实现后能跑、能绿、但功能静默失效或数据静默错误。

范围裁剪（Q-1）成立，右尺寸判断基本正确：既没有把因子快照与闸门报告硬塞进来，也没有把「不重建利润保护棘轮」「不引入组合优化求解器」「不改 `LiveExecutor` 一行」这些边界让掉。技术选型（波动率目标 + 四分之一凯利 + 相关加权二次型 + 三级滞回刹车）与项目现有栈完全兼容，零新依赖、零数据库迁移。

**全部 3 个 P0 与 8 个 P1 已在正文修正完毕，无遗留。** 本文档版本已升至 **v2**。

### 放行条件（12 条，实现节点须逐条兑现；未兑现者视为未完成）

| # | 条件 | 对应 |
|---|---|---|
| 1 | `executor.risk` 的改写窗口收窄为 `projected=="flat"` 且 `resting is None`，`stopSource` 落五态，T-17b/T-17c 断言"不满足即对象 `is` 相等"；仓库全局 grep 确认 `executor.risk` 赋值点**有且只有一处** | [P0-1] / S-11 / G-11 |
| 2 | 凯利赔率的 `W`/`L` 取本轮**实际要挂的括号**；`sizing.py` **不 import `edge_gate`、不出现 `edge_bps`**（AST/grep 级判据） | [P0-2] / S-12 / G-12 |
| 3 | 回测 ATR 止损**双写** `self._oco_risk` + `self.main.set_risk(...)`；T-19b 必须**先红后绿**（只改 `_oco_risk` 的写法必须让它红） | [P0-3] / S-13 / G-13 |
| 4 | `parse_stop_rule` 与 `parse_sizing_rule` **各自独立**，总闸②不受总闸①门控；T-28 证明 runbook 的 L1 真的可达 | [P1-1] / G-14 |
| 5 | 窗口容量在**启动期**对账（越界 422 `AI_TRADE_SIZING_WINDOW_TOO_SMALL`），不是运行期静默 `None`；`volSource` 三态进 wire | [P1-6] / T-24 / G-14 |
| 6 | 档位地板反噬有观测量：`fFinalRaw` + `scaleErased` + `quantizeErasedRounds`；L3 的放行判据用"触发轮里 `scaleErased=false` 占比 ≥ 50%"，**不得**只凭 `brakeSlowRounds` 宣称刹车已验证 | [P1-2] / T-23 |
| 7 | 相关性缩减用**闭式解**（二次不等式），S-15 属性测试通过；`c ≥ cap²` 的边缘态置 `exposureCapUnreachable` 并**放行**（不否决，守 S-3） | [P1-3] / T-26 / T-27 |
| 8 | 刹车阈值按 §3.6 折算表**按杠杆定标**，`.env.example` 与 runbook L3 同步给出 3×/5× 的具体值 | [P1-4] / T-30 |
| 9 | 峰值权益的 mark-to-market 口径显式声明；`peakEquityBasis` / `unrealizedShare` 进 wire；L3 增加 halt 轮 `unrealizedShare` < 0.8 的判据 | [P1-5] / 自我反驳六 |
| 10 | `AiTradeGroupStartRequest` **一个字段都不加**，三条规则由 `manager` / `AiBacktestRunner` 各自从 `settings` 解析 | [P1-7] |
| 11 | `publish_notional` 挂在 `session._dispatch` 唯一收口 + 轮末兜底 | [P1-8] / S-14 / T-25 |
| 12 | 前端 **8** 个编辑点全落地，其中 `emptyRoundTrace`、`reduceRoundPhase`、`AiTradeTraceRound.tsx:536` 的 `<SizingBlock/>` **挂载**三处为硬断言判据（不靠 grep） | [P2-3] / G-4 |

### 给实现节点的三句话

1. **先写 T-19b 和 T-17b/T-17c，再写实现。** 这三条测试各自守着一个"能跑能绿但没生效/写错数据"的坑；反过来做，你不会知道自己踩没踩上。
2. **`sizing.py` 与 `edge_gate.py` 之间必须零依赖。** 这不是洁癖：两个模块的 `edge` 是**不同的估计量**（一个是条件幅度、一个是概率加权期望），让它们互相 import 迟早会把两者混起来。
3. **凡是"本层生效了"的读数，都要能回答"是真生效还是被下游吃掉了"。** 本轮新增的 `scaleErased` / `stopSource` 三个 skip 态 / `exposureCapUnreachable` / `volSource` 全是为这一件事存在的——本仓库栽在"计数在涨但什么也没发生"上不止一次。

---

## 十四、实施过程发现的方案缺陷

> 实现节点按 spec v2 逐条落地时发现的问题。**每一条都已在代码中按修正后的做法实现**，此处登记原因、
> 证据与偏离幅度，供终审逐条核对。编号 D-x。

### D-1（阻断级，已按修正实现）：§3.3 步骤 5/6 的顺序与它自己的 [P1-2] 互相矛盾，照字面实现会让刹车与相关性在最常见断面上彻底空转

**证据。** §3.3 求解顺序写的是：

```
5. f_scaled  = f_core × brake_mult × corr_scale        # f_core = min(存在的项…, f_max)
6. f_final_raw = min(model_fraction, f_scaled)
```

而同一节的 [P1-2] 与 §3.6 档位表、§7.1 T-23、§八 L3 判据算的却是另一件事：

```
0.25(模型) × 0.5(第一档减速) = 0.125  →  quantize(0.125, 0.25) = 0.25
```

两者不可能同时成立。按步骤 5/6 的字面顺序：`f_core` 在波动率与凯利两项都不可算时**退化为 `f_max = 1.0`**
（这是 §3.3 步骤 4 自己写的），于是 `f_scaled = 1.0 × 0.5 × 1.0 = 0.5`，再与模型的 0.25 取小仍是 **0.25**
——第一档减速对结果的影响是 **0**，连 [P1-2] 说的「被档位地板吃掉」都轮不上，因为它压根没作用到。
更糟的是这个断面**不是边缘情况而是常态**：系统 prompt 把模型引导到 `sizeFraction ≤ 0.5`，而先验层默认
关闭（`ai_trade_signal_prior_enabled=False`）⇒ `p_win is None` ⇒ 凯利项恒不参与，L3 打开刹车时波动率项
也常在 0.18~0.43 之间——只要模型给的值小于 `f_vol × 0.5`，刹车就是纯装饰。而 `brakeSlowRounds` 照样计数。

这正是 [P1-2] 自己命名的那类缺陷（「计数在涨但什么也没发生」），只不过 v2 把它修在了观测量上，没修在
公式顺序上。

**修正（已实现）。** 两个乘性系数作用在**与模型提议取小之后**的结果上：

```python
f_core = min(存在的项…, f_max)
f_raw  = min(model_fraction, f_core) × brake_mult × corr_scale
```

理由：刹车的语义是「把**本轮实际要下的注**缩一半」（§一：「触及第一档即把仓位系数乘 0.5」），不是「把
一个通常不绑定的上限缩一半」。单调收敛不变式 S-1 不受影响（两个系数恒 ≤ 1，T-1 的 500 组属性测试覆盖）。
`scale_source` 的优先级随之调整为「乘性系数优先」（它们只要 <1 就一定改变了结果）。落点：
`sizing.resolve_size_fraction` 与其模块 docstring。

### D-2（记账级）：§3.9 的事件形状是 20 个键而正文三处都写「16 个键」；实现取 23 个

**证据。** §3.9 正文与 §6.3 表的第 2、3 项都写「新增 **16** 个可选字段」，但同一节列出的 JSONC 事件形状
数下来是 **20** 个键（`brakeLevel` … `exposureCapUnreachable`）。而 [P1-5] 又要求 `peakEquityBasis` 与
`unrealizedShare` 进 trace、§3.8 的 `sizingPlan` 还有 `fCap`——三者都不在那 20 个里。

**修正（已实现）。** 取**并集 23 键**，并把键集合收敛成后端的单一真源
`trace.SIZING_TRACE_KEYS`：`sizing_event`（事件装配）、`new_round_trace`（轮次初值）两处都从它派生，
前端 `store.emptyRoundTrace` 的逐键手抄由 `apps/web/lib/aiTradeSizing.test.ts` 的
`SIZING_KEYS` 硬断言守住（漏抄任一键即红）。这比「三处各抄一份 16/20/23」更能防住 §6.3 表下注解自己
警告的那类漂移。

### D-3（记账级）：§6.2 的行数上限是按「纯逻辑行」估的，四个文件的实际净增超表

**证据。** 硬闸（§7.2 G-3）只约束 `executor.py` / `edge_gate.py` / `engine/simulator.py` 净增 **0**、
`session.py ≤ +26`、`backtest.py ≤ +24`——**这五条全部达标**（`git diff --numstat` 实测 0/0/0/+26/+24）。
但 §6.2 表里其余四行的估值与实际净增出入较大：

| 文件 | 表列上限 | 实际净增 | 超出部分的成因 |
|---|---|---|---|
| `manager.py` | +32 | +46 | `_validate_sizing` 的两条启动期对账（含中文错误文案与 `MarketWindow` 探针）本身就近 20 行 |
| `stats_ext.py` | +25 | +48 | 两个函数的 Google 风格 docstring（`CLAUDE.md` 强制「公开 API 必须写 docstring」） |
| `prompts.py` | +18 | +32 | 同上（新增 `_format_sizing` + `_size_discipline` 的追加分支 + 两处签名 docstring） |
| `cost_runtime.py` | +14 | +23 | `estimate_cost_only` 的 docstring 要解释「为什么这不是第二条估算路径」，占 11 行 |
| `trace.py` | +34 | +37 | `SIZING_TRACE_KEYS` 单一真源常量（D-2） |

这与阶段二第一步的同款发现一致（该轮记录为「spec 行数预算按纯逻辑行估 → 6 处超标」）。**未压缩注释与
docstring**：本仓库的既有代码密度就是如此，把解释砍掉换行数达标会直接违反 `CLAUDE.md` 第四节。建议后续
spec 的行数预算改按「纯逻辑行 + 1.8 倍注释系数」估，或只对超红线文件（`session.py` / `backtest.py` /
`executor.py`）设硬闸——本轮的做法正是后者。

### D-4（澄清级）：`SizingContext` 与 `SizingRuntime` 的签名较 §4.2 有三处**加法**扩展

三处都是「spec 要求了产物、但没给出它怎么流到那里」的补齐，**无一改变判定语义**：

1. `SizingContext` 增 `notional_full` / `fallback_stop_pct` / `fallback_take_pct` / `closes` 四个可选
   字段：实盘侧由 `executor` 断面派生（故会话侧不必写），回测侧无 executor，必须显式给。
2. `SizingRuntime.resolve(decision, ctx, executor=None)` 增第三个可选形参：`executor` 非空时本方法自己
   完成 `apply_risk_override` 并把 `stopSource` 写进 wire（会话侧因此只需一行）；回测侧不传，改由调用方
   拿返回的 `plan` 做 [P0-3] 的 `_oco_risk` + `main.set_risk(...)` 双写。
3. `note_equity` 增 `unrealized_usd` / `session_pnl_usd` 两个 keyword-only 形参，`can_override_risk` /
   `apply_risk_override` 增可选 `action` 形参：前者是 [P1-5] 要求的 `unrealizedShare` 读数的唯一来源，
   后者是 `stopSource` 五态里 `skipped_reversal` 与 `skipped_position_live` 的唯一区分依据（§3.4 定义了
   这两个态，但 `can_override_risk(executor)` 的单形参签名拿不到动作，无法区分）。
4. `format_sizing_charter(rule, stop)` 增第三个可选形参 `brake`：§3.8 的宪章段要求把「三档全部只拦开
   新仓 + 回撤按 mark-to-market 口径」写进宪章（[P1-5] 第 1 条），而前两个形参拿不到刹车规则。
5. 新增两个 spec 未列但必需的装配函数：`sizing_runtime.build_sizing_runtime`（三条规则的单点解析，
   实盘与回测各调一次，[P1-7] 的落地形式）与 `sizing_runtime.sizing_config_wire`（run `config_json`
   回填，§五要求「随既有 config_json 的 free-form 键落库」但未给出 wire 形状）。

### D-5（澄清级）：回测侧的三处显式非目标

1. **不注入 `sizingPlan` / prompt 段**：§6.2 的回测改动点里没有这一项（只有「同位插入两步 + 两个复发点
   + 三条规则解析 + 两个喂数点 + 免责文案」），故回测的 payload 与 prompt **逐字节回退**。
2. **凯利项恒不参与**：`AiBacktestRunner` 不加载信号先验快照（那是 `AiTradeGroupStartRequest.prior_snapshots`
   的事），故 `p_win is None` ⇒ `kellyStatus == "no_prior"`。这是结构性事实而非缺陷，已作为**第六条**写进
   `SIZING_BACKTEST_NOTE`（§3.10 原文只有五条）。
3. **不装配敞口簿**：§3.10 已明写「回测侧显式不实现」，实现照办（`build_sizing_runtime` 的 `book` 留空）。

### D-6（欠账登记）：`session.py` 现 1494 行、`backtest.py` 1009 行

本轮把新逻辑全部挡在 `ai_auto/*` 的 5 个新模块里，对三个超线文件只留薄封装（净增 +26 / +24 / 0），
但 [P2-8] 登记的债务本身仍在扩大。已随 §十 的标记方案写进 `00` 号「已知欠账」。

---

## 十五、终审记录（评审节点，合并前）

> 逐条比对 §6.2/§6.3 的改动计划、§7.2 的 G-1~G-14 与 §十三 的 12 条放行条件。12 条**全部兑现**
> （硬闸经 `git diff --numstat` 机械核对：`executor.py` / `edge_gate.py` / `engine/simulator.py` 净增
> **0**、`session.py` **+26**、`backtest.py` **+24**）。终审自己发现并**已修复**两处缺陷：

### RV-1（阻断级，已修）：回测侧的连亏冷却判定用墙钟、开冷却用根 ts —— 第三档在回测里恒不触发

**证据。** `SizingRuntime.gate` 无条件取 `time.time()`，而回测的 `note_close` 传的是 `float(trade.ts)`
（根 ts）。历史行情的 `cooldown_until_wall = 根ts + 冷却秒数` 早已落在墙钟之后方，故
`remaining = max(0, 该值 − time.time())` **恒为 0** ⇒ 第三档在回测里永不触发、不报任何错，而
`SIZING_BACKTEST_NOTE` 的第 ④ 条却明写「连亏冷却按根 ts 计时」——一个不存在的行为被写进了对用户的披露。
这正是本 spec 反复点名的那类缺陷（「计数在涨但什么也没发生」），只不过这次连计数都没有。

**修正。** `gate(decision, degrade, *, now_wall=None)`：留空取墙钟（实盘逐字不变），回测显式传
`float(ts)`，与 `note_close` / `resolve` 的时钟同源。回归测试
`test_backtest_cooldown_is_timed_by_bar_ts_not_wall_clock`（修前红：`gate` 连该形参都不收）。

### RV-2（P1，已修）：`_validate_sizing` 排在 `_validate_members` 之前却直接取 `members_in[0]`

**证据。** 空分组是**可达**输入（`_validate_members` 为它保留了 `AI_TRADE_GROUP_EMPTY` / 400 的稳定
错误码），而 `_validate_sizing` 在它之前跑并取下标 ⇒ 总闸①开启时空分组会以 `IndexError`（500）收场，
既有的 400 契约被换掉。**修正**：`rule is None or not req.members_in` 即早退，把该态还给它的所有者。
回归测试 `test_sizing_validation_leaves_empty_group_to_its_own_error_code`。

### RV-3（测试缺口，已补）：两条启动期对账此前只在纯函数层被断言

T-24 的判据是「**启动**抛 422」，但实现只有 `check_window_capacity` 的纯函数用例；G-8 要求的
「`exposure_cap_mult` 越界在启动期 422」同样没有任何用例覆盖 `manager` 那一侧。已补
`test_window_capacity_rejected_at_startup` / `test_exposure_cap_out_of_range_rejected_at_startup`
（`primingBars=10` ⇒ 窗口上限 80 < 97，正是 [P1-6] 描述的真实断面）。

### RV-4（记账级）：§4.4 表是 **13** 个配置字段，正文与 §6.2 的「14 个字段」是计数笔误

实现按表落 13 个（`config.py` 与 `.env.example` 两侧一致），无字段遗漏。

### 合并时的文档状态

§十 的 7 处标记已由实现节点逐字落地，终审按既有约定（`D-5` 在 `c6db95d` 的同款处理）把
`D-6 / D-12 / D-13` 三行、`M2` 里程碑行与 `08` §3.2 的两行由「已实现(未提交)」改为「**已合并**」。
