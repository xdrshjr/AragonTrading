# AI 自动交易：已实现 edge 的结算、跨会话盈利体检与风险单位对齐

> 版本：**v2**（设计评审后修订；v1 = 设计节点产出）
> 特性目录：`docs/plans/ai-trade-realized-edge-calibration/`
> 所属循环：loop `tge-dffbcee0` 第 3 轮（第 1 轮 = 纪律预设装配 `633f404`；第 2 轮 = 可交易性预检与开仓漏斗 `2274b43`）
> 需求原文：「这个项目，AI 自动交易功能，现在应该还有问题，寻找相关的 BUG 和问题，进行修复，确保能够正确的自动交易，并且稳定盈利。」
> 评审：6 P0 / 12 P1 / 9 P2，**全部 P0+P1 已在正文闭合**；结论见文末 §评审结论（**有条件通过**，13 条放行条件）。

---

## 评审记录（v1 → v2）

> 评审方式：v1 的 8 条缺陷证据**逐条打开源码核对**（全部 `file:line` 属实，无一漂移），
> 并在用户本机生产库上**独立复算**了 §零 与 §附录 A 的每一个数字。核对脚本与 v1 §附录 A 同源。
>
> **先说 v1 站得住的部分**（评审据此没有推翻任何一条主张）：`stop_loss_pct` 193/193 恒 `0.05`、
> `take_profit_pct` 恒 `0.10`、退出 kind 分布 `atomic 184 / session_stop 6 / external_close 2 /
> profit_guard 1`、位移中位 13.9 bps、`|R|<0.2` 占比 100.0%、`R≥1` 占比 0.0%——**逐位复现**。
> 全部行数标注（`session.py` 1845 / `manager.py` 957 / `prompts.py` 835 / `schemas.py` 2427 /
> `api/ai_auto.py` 719 / `ai_auto_repositories.py` 500 / `tradability.py` 401 / `attribution.py` 173
> 及 10 个前端文件）**逐个 `wc -l` 核实无误**。这是一份勘察扎实的设计。
>
> 下面 6 条 P0 全部长在**同一处**：v1 把「这个数算得出来」当成了「这个数会正确地流到用户/模型面前」。
> 与第 2 轮终审的形状一致——**新判据取自不携带该信息的入参 / 后端算了但没有落点**。

### P0（阻断级，6 条，均已闭合）

| # | 问题 | 证据 | v2 的处置 |
|---|---|---|---|
| **P0-1** | **轴 A 的估计写点在 `session.py:760` 拿不到 `out`，§3.1.5 那一行写不出来。** `_apply_edge_gate`（`session.py:1096-1108`）返回的是 `(out.decision, degrade or out.reason)`，`out` 是**函数局部量**；`:760-761` 的调用点只拿得到两个返回值 | `session.py:760-761`（调用点）vs `:1096-1108`（定义）；`note_gate` 的真实转发点在 `_emit_edge_gate_trace`（`session.py:1114`） | §3.1.1 / §3.1.5 改为把写点下沉进 `_apply_edge_gate` 内、紧贴 `_emit_edge_gate_trace`——与 §十.6 接缝 1 要求的「与 `note_gate` 同址」这才真的成立；session 净增仍 +1 |
| **P0-2** | **限价开仓成交落在决策轮之外，轮级估计已被 `end_round()` 清零 ⇒ 估计侧对默认订单类型恒空。** `ai_trade_limit_orders_enabled` **默认 True**；resting 单的采纳成交由 `_monitor_tick → executor.reconcile → _dispatch` 发出（`session.py:487-491`），而 `end_round()` 在 `_run_decision_round` 的 `finally`（`:812-815`）早已执行 | `config.py:196`；`session.py:487-491` vs `:812-815` | §3.1.1 增**待成交估计槽**：估计在**下单那一刻**按 `clOrdId` 冻结（TTL 取 `ai_trade_limit_order_ttl_s`），`note_open` 先按 `clOrdId` 认领、认领不到再回退轮级槽；轮级槽仍在 `finally` 清零。新增 G-3b |
| **P0-3** | **已实现成本在两条路径上是两个口径，且同一个 wire 键有两个写者、无优先级规则。** 实盘 `record_close` 的 `fee` 只是**平仓腿**（`attribution.py:71-75/84-90`），追溯复算的 `leg_fee_usd` 是**双腿**（`ai_auto_repositories.py:92-103`、`_Trip.net_pnl/gross_pnl`）。于是 `explain.meta.realizedCostBps` ≈ 追溯值的一半，而轴 C 问二拿它去比**往返**成本估计（13.0 bps）⇒ 系统性 −50% 误差，会被读成「成本模型高估一倍」。G-6 只钉了 `notionalUsd`，钉不住这条 | `attribution.py:71-75`、`:84-90`；`ai_auto_repositories.py:40-68`、`:92-103`；实测 21 笔带费往返**开平两腿都有 fee**（16+5 笔各自成对） | §3.1.2 约束 2 扩写：`OpenSnapshot.open_fee_usd` 累计开仓腿费（部分成交幂等累加），`settle` 的 `fee_usd` = **往返双腿**；§3.2.2 明确**追溯复算是货币类键的唯一权威**、`meta` 只在展示行使用并须与之对拍；G-6 扩到 `feeUsd` / `realizedCostBps` / `rMultiple` |
| **P0-4** | **89% 的历史往返根本没有费率记录，`medianCostBps` 恒 0 ⇒ 头条比值 `travelToCostRatio` 恒 `None`，而轴 D 会对模型说「一次往返成本约 0.0 bps」。** 费率记账 2026-07-26 才上线（`96f554d`）：386 条成交里只有 42 条带非零 `fee`，193 笔往返里 **172 笔（89.1%）腿费恒 0**。实测全样本 `medianRealizedCostBps = 0.000`（v1 §5.1 写的是 5.8）；只在带费的 21 笔上才是 **7.01 bps** | 见 §二 D-9（本轮新增，附 SQL）；v1 §5.1 / §3.3 问二 | 新增 `feeCoverage` 分母并贯穿 pooled / calibration / 卡片 / prompt：`medianCostBps` 与 `travelToCostRatio` **只在带费子样本上算且必须并排显示覆盖率**；轴 D 在 `feeCoverage < ai_trade_track_record_min_fee_coverage`（默认 0.5）时**整行省略成本句**——绝不下发一个 0.0 bps 的成本 |
| **P0-5** | **全文头条数字用的是「`pnl` 一律视为净额」，与产品自己的 `_run_cost_basis` 推定规则冲突。** 生产库 161 个 run 里 **156 个 `config_json` 无 `costBasis`** ⇒ 走 `ai_trade_history_legacy_cost_basis` 推定 `"gross"` ⇒ 再减一次腿费。而 `ai_trade_net_pnl_enabled` 在 `96f554d`（2026-07-25）引入时默认 **False**、`cebee54`（2026-08-06）才改 True，带费的那 16 笔恰好产生于 2026-07-26 ⇒ **推定 gross 是对的、v1 的假设是错的**。实测两口径：赔率 **1.172 → 1.014**、t **−0.791 → −1.446**、Σ净额 **−4.11 → −7.42** | `ai_auto_repositories.py:167-180`；`config.py:485`、`config.py:446`；`git log -S` 得 `96f554d` 默认 False | §零 / §二 / §五.1 / §附录 A 的全部数字改按 `_run_cost_basis` 复算（结论方向不变、且更强）；§附录 A 的脚本改为逐 run 解析 `costBasis`；`costBasisAssumed` 由布尔升为分母（P1-12） |
| **P0-6** | **`bracket` 会被 `response_model` 静默吞掉 ⇒ 轴 E-0 上线即死键。** `/ai-trade/tradability` 挂 `response_model=AiTradeTradabilityOut`（`api/ai_auto.py:459`），该模型是**显式字段清单**（`schemas.py:1237-1267`），其 docstring 自己就写着「`response_model` 会静默吞掉未声明的 wire 键」。而 §四的 `schemas.py` 预算只列了 `AiTradeProfitabilityOut` 与 `trade_out`，**没有这一行** | `schemas.py:1237-1267`、`:1244-1246`（成文教训）；`api/ai_auto.py:459` | §四 `schemas.py` 预算补 `AiTradeTradabilityOut.bracket: dict \| None = None`；§5.2 补一句「本键必须在 `response_model` 上显式声明」；新增 G-23 |

### P1（12 条，均已闭合）

| # | 问题 · 证据 · v2 的处置 |
|---|---|
| **P1-1** | **`exitSource` 的第二个分类现场**：`settle` 发生在 `_dispatch:322`，而 `meta.exitSource` 在 `_inject_explain_meta:363` 才打标、且受 `ai_trade_exit_attribution_enabled` 门控。轴 A 若自己再 `classify_live` 一次，就同时违反 `exit_attribution` 的「单一收口打标」纪律（`exit_attribution.py:11-14`）并**绕过那个总闸** ⇒ §3.1.2 改为：`apply_trip_meta` 必须排在既有 `exitSource` 打标**之后**，且**读** `meta.get("exitSource")`；缺席 ⇒ `"unknown"`，**绝不自己再判一次** |
| **P1-2** | **`aiStats.realized` 无渲染落点（死数据）**：§四的前端计划里两张新卡读的是**端点**，`AiTradeStatsDetail` 只改 hint 与 R 列，没有任何组件读 `aiStats.realized` / `costSnapshot.realized` ⇒ §3.1.3 给它指定落点（历史详情页 KPI 段的会话级「已实现」三项 + 实时 `AiTradeStatsLive` 复用同一格式化器），并补进 §四与 G-9b |
| **P1-3** | **`rMultiple` 的类型与渲染落点写错**：仓库里**不存在** `AiTradeHistoryTrade`；成员成交类型是 `lib/types.ts` 的共享 `Trade`（`aiTradeHistoryTypes.ts:10` 直接 import），而唯一的往返行渲染器是**共享**组件 `components/Chart/AutoTradeRoundTripRow.tsx`（live-auto / paper 面板同用）⇒ 字段改加在 `lib/types.ts:Trade`（与 `wallTs`/`peakPnlUsd` 同址），渲染沿用该组件既有的**可选形参 `bar?`** 先例（不传即逐字节不变）；§四补这两个文件 |
| **P1-4** | **新卡挂点被空态早返回吞掉**：`AiTradeStatsHistoryList.tsx:44-50` 在 `items.length === 0` 时直接 `return`，`PresetComparisonCard`（`:58`）已经中了这一刀；而 `/ai-trade/history` 会 `exclude_group_id` 掉**正在运行**的那一组——只跑过一组的用户在会话运行期间整页恒空 ⇒ §3.2.3 明确两张新卡挂在**早返回之前**，空态文案下移 |
| **P1-5** | **路由顺序**：`/ai-trade/history/profitability` 必须注册在 `/ai-trade/history/{group_id}` **之前**，否则被路径参数吞掉当成 group_id 查（`api/ai_auto.py:498-499` 已有成文教训）⇒ §5.1 补硬约束 + G-21 |
| **P1-6** | **`verdict()` 未处理 `tStat is None` ⇒ 端点 500**：§3.2.1 约束 4 自己写了「`n<2` 或零方差 ⇒ None」，而结论表的四条判据都在拿 `tStat` 与阈值做大小比较，Python 里 `None < 2.0` 直接 `TypeError` ⇒ 冻结表加一条前置判据：`tStat is None` ⇒ 按**不显著**归类 |
| **P1-7** | **E-2 需要 `wins` 分子，但 §3.5.3 与 G-19 又要求 `_summarize_pairs` 一个字不改**：该函数当前只返回 `winRate`，不返回 `wins`（`ai_auto_repositories.py:106-164`）⇒ 允许它追加一个**加法键** `wins`，G-19 改为断言「**既有键**逐位不变」（口径不变，只是多一个分子） |
| **P1-8** | **`bracket_verdict` 的 `stop_source` 取自不携带该信息的入参**：v1 按「`stop_atr_mult>0 且 sizing_enabled`」判 `atr`，但真实止损距离要过 `resolve_stop_distance`（`sizing.py:303-345`）的**成本下界**与**红线冲突**两段——`floor_above_cap` 时整条 ATR 覆盖回退成 fixed，成本下界胜出时距离也不是 `k×ATR`。第 2 轮实测 BTC 1m 有 62.3% 的断面 `2×ATR` 小于单次成本 ⇒ `stop_source` 改取探针已经算出的**止损来源七态**（与轴 B 的 `stopBps` / `stopInsideCostRate` 同源），文案分「按 ATR 定标 / 按成本下界抬起 / 回退固定百分比」三种说法 |
| **P1-9** | **轴 D 把一份负期望战绩每轮回喂，有「永不开单」复发风险且本轮无任何度量**（本仓库已复发 4 次）⇒ 加 `aiStats.trackRecordShown` 布尔留痕 + G-14b：装配/不装配两臂的 `openFunnel.intents / rounds` 对拍，开仓意图归零即视为回归、总闸回滚 |
| **P1-10** | **`Kpi` 组件没有 `hint` 形参**（`AiTradeStatsDetail.tsx:445` 的签名是 `{label, value, color}`），且**悬停才可见的提示等于没显示**（触屏与用例都读不到）⇒ E-3 改为把口径写进 label 本身（「观望占比（近 50 轮）」）并在下方补一行常显小字；用例断言**可见文本** |
| **P1-11** | **形参数量违反 CLAUDE.md 的「≤5」**：`settle`(6)、`bracket_verdict`(6)、`load_profitability_trips`(6) ⇒ 一律改聚合形参对象（仓库先例：`manager.AiTradeGroupStartRequest:116`「避免位置堆参，满足 CLAUDE.md ≤5 形参」、`tradability.TradabilityInputs:57`） |
| **P1-12** | **`costBasisAssumed` 是布尔，表达不了 156/161 run（172/193 笔）都在推定这件事** ⇒ 升为 `costBasisAssumedTrips` / `roundTrips` 两个数，与 `feeCoverage` 并排上屏；契约纪律 ④ 同步改写 |

### P2（9 条，记录备查；实现节点可自行取舍）

1. `profitability.py` 的 ≤300 行预算偏紧——同量级的 `tradability.py` 是 401 行，而本模块要装 `pooled` + `risk_profile` + 六切片 + 三问 + 冻结结论表。**预授权**按 §十.7 的下沉范式拆出 `calibration.py`，**不要靠删注释压行**。
2. §5.2 说「逐成员回执」，实际 `/ai-trade/tradability` 是单 `(instId, bar)` 回执（`api/ai_auto.py:459-478`），弹窗按成员各请求一次；措辞已在 v2 修正。
3. §3.1.2 的 `SettledTrip` 代码块里 `est_edge_bps / est_cost_bps / …` 那一行不是合法 Python，是示意行——v2 已标注。
4. `expectancyR` 的分母是「R 可得子样本」，与 `expectancyUsd` 的分母不同，必须与 `rSamples` 并排出现（已并入 §3.2.1 约束 1 的「每行必带分母」）。
5. §3.1.5 的表原先漏了轴 D 在 `_build_payload` 的注入行，「合计 +3」与「净增 ≤6」的对应关系没写全——v2 已补齐为 +4/≤6。
6. `_position_seq` 在平仓分支是**先自增再** `record_close`（`session.py:318-322`），故 `SettledTrip.seq` **只能**取 `OpenSnapshot.seq`，不可读当轮 `_position_seq`（差 1）。
7. 本轮自设的六个门禁纯核心冻结（G-13）与既有 `test_shared_execution_files_have_zero_diff`（`test_ai_trade_acceptance_wiring.py:445-451`，冻结集只有 3 个文件）**不是同一条**，用例名与冻结集须分开写，复用既有 `_git_diff_names()`。
8. R-4 的「实测 <50 ms」缺复算口径（当前 161 run / 386 成交），建议在文档里注明测量基数，并给 `sinceTs` 一个默认窗口建议值。
9. D-5 表里 `session_stop` 的中文「会话止盈损 / 停止flatten」缺空格；且 `explain.kind` 与 `exit_attribution` 九值枚举（`sessionStop` / `windowEnd`）的映射关系没写明，切片 `byExitSource` 的键必须是**九值枚举**而不是 kind。

---

## 零、本轮结论摘要（先读这一段）

本轮的勘察结论**不是**「AI 不会交易」，前两轮已经把「能不能开单」修完了。本轮在用户本机生产库
（`%APPDATA%/aragon-desktop/aragon.db`，161 个 `ai-trade` run、28 个分组、**193 笔已完成往返**）上逐笔
复算后得到的结论是三句话：

1. **系统在盈亏平衡线上原地打转。** 193 笔往返：胜率 **40.41%**、赔率（平均盈利/平均亏损）**1.014**，
   而该胜率下的盈亏平衡赔率是 **1.474**；净额期望 **−0.0384 USDT/笔**（t = −1.446），Σ 净额
   **−7.4185 USDT**。方向性 edge 在统计上与 0 不可区分，而赔率连盈亏平衡线的 69% 都没到。

   > **[P0-5] 口径更正**：v1 这三个数用的是「`pnl` 一律视为净额」，得到 40.93% / 1.172 / −0.0213
   > （t = −0.791）。但生产库 161 个 run 里 **156 个的 `config_json` 没有 `costBasis` 键**，产品自己的
   > `_run_cost_basis`（`db/ai_auto_repositories.py:167-180`）会把它们推定为 `"gross"` 并再减一次腿费；
   > 而 `ai_trade_net_pnl_enabled` 在 `96f554d`（2026-07-25）引入时默认 **False**、`cebee54`
   > （2026-08-06）才翻成 True，带费的那 16 笔恰好产生于 2026-07-26 ⇒ **推定 gross 是对的**。
   > 本轮所有数字一律改按产品的解析规则复算（脚本见 §附录 A.5）——**结论方向不变，且更强**。

2. **风险框架与实际交易尺度差了约两个数量级。** 193 笔开仓的止损/止盈**全部**是出厂默认
   `sl=5%`、`tp=10%`（计划盈亏比中位数恰为 2.000），而一笔往返的**实际价格位移中位数只有 13.9 bps**
   （p10 2.3 / p90 37.2）。于是：**191 笔可算 R 倍数的往返里，100% 的 |R| < 0.2，无一笔达到过 ±1R**
   （R 中位 −0.011），「典型交易走完了自己止损距离的 2.83%」。计划中的止损、止盈、盈亏比、
   利润保护武装线——整套风险语言在真实数据上从未被触碰过一次。而 13.9 bps 的中位位移，
   恰好等于第 2 轮实测的一次市价往返成本 13.0 bps：**半数交易的价格位移不够付一次往返成本。**

   > **[P0-4] 这里必须补一句诚实话**：13.0 bps 是**成本模型的常数**，不是实测。真实付掉多少，
   > 193 笔里只有 **21 笔（10.9%）**答得上来——费率记账 2026-07-26 才上线，其余 172 笔的
   > `fee` 恒 0（见 D-9）。那 21 笔的实测**双腿**往返成本中位是 **7.01 bps**（p10 6.99 / p90 10.02），
   > 只有模型常数的 54%。所以本轮的正确说法是「按模型常数，半数交易的位移不够付一次往返成本；
   > 按可得的 21 笔实测，中位位移 13.9 bps 对 7.0 bps 成本只剩不到 2 倍的余量」——
   > 两句话都要带各自的分母，**任何一句单独出现都是误导**。

3. **系统没有能力知道自己赚不赚钱。** 全仓所有盈利读数的作用域最大到**一个分组**，而一个分组的
   往返数中位数只有个位数（28 组里 10 组零成交，18 组的往返数从 1 到 39 不等）。唯一的跨会话卡
   `/ai-trade/history/preset-comparison` 把「比例的均值」当成「均值的比例」——实测池化胜率 40.74%
   vs 各组胜率的算术平均 36.11%，差 4.63 个百分点。更要命的是证据基座：193 笔往返里 **188 笔发生在
   持仓周期铁律层存在之前**，装配了利润保护读数的会话只贡献了 **21 笔**、装配了周期层的只有 **5 笔**。
   三轮迭代造了十余层纪律，而能用来判断它们是否有效的样本是个位数——**任何「稳定盈利」的承诺在
   这样的证据基础上都不可验证。**

因此本轮做五件事，全部确定性、零新增 LLM 调用、零 DB 迁移：

| 轴 | 名称 | 一句话 |
|---|---|---|
| A | 往返结算账本 | 每笔往返在**开仓那一刻**冻结估计（edge / 成本 / 置信度 / 计划风险单位），在**平仓那一刻**结算成已实现 bps 与 R 倍数，落 `explain.meta` |
| B | 跨会话盈利体检 | 新端点 + 新卡：**池化**口径的胜率 / 赔率 / 期望 / 显著性 / R 分布 + 六个分层切片 + 一张冻结的结论表 |
| C | 校准三问 | 门禁分子准不准、成本模型准不准、置信度有没有区分度——每一问都带 `coverage`，样本不足就说「不下结论」 |
| D | 战绩回喂决策 | 把「你的实测胜率、赔率、盈亏平衡赔率、中位位移 vs 往返成本」写进每轮 payload 与 prompt（只读、可关、样本不足即整键缺席） |
| E | 风险单位对齐 + 三条口径修复 | 启动期体检止损距离相对本周期 ATR 的倍数（**只提示不改**）；修孤儿平仓腿费结转、预设对照的比例均值、双分母并排展示 |

**本轮不动任何门禁的判定式**（`edge_gate` / `base_rules` / `consensus` / `hold_horizon` /
`turnover` / `profit_guard` 的纯核心逐字节不变），也**不做自动调参**。理由见 §九。

---

## 一、概述

前两轮把 AI 自动交易从「机制造好了但默认不装配」修到了「装配了但会把 98.6% 的开仓意图拦死」，
再修到「启动前就告诉你这个周期能不能交易」。这条演进线到第 2 轮为止解决的都是**开仓侧**的问题：
系统敢不敢开单、开得出来吗、开之前用户知不知道。第 3 轮的勘察证明，剩下的问题**全部在开仓之后**：
开出来的单值不值得、退出退在什么位置、以及最根本的——这套系统到底赚不赚钱，谁来回答。

今天没有人能回答。`AttributionLedger` 记了每笔往返的美元盈亏，`ExitLedger` 记了按退出来源的胜率，
`horizon_acceptance` 记了持仓周期的验收卡，`preset-comparison` 记了按纪律预设的对照——四套读数，
每一套的作用域都停在**一个会话**或**一个分组**上，而一个分组的往返样本量是个位数。于是每一层新纪律
上线时都带着 5 笔样本的「证据」，下一轮再叠一层新纪律，证据基座永远追不上机制复杂度。这不是观测
的奢侈品缺失，这是**闭环的缺口**：一个交易系统如果不能把「我预计能赚多少」和「我实际赚了多少」
放在同一张表上对齐，它的所有阈值就都只是**猜**，而猜出来的阈值再多层也不会收敛到盈利。

本轮补的就是这条闭环。轴 A 让每一笔往返在开仓那一刻把「本轮门禁算出来的毛 edge、本轮成本模型
算出来的往返成本、模型给出的置信度、本轮计划的风险单位」一起冻结下来，在平仓那一刻结算成
「实际走了多少 bps、实际付了多少 bps、相当于计划风险单位的几倍（R）」，写进已有的 `explain.meta`
信封随成交落库——**零新列、零迁移**。轴 B 把这些逐笔样本在**全部历史**上池化，产出一张会明确写
「样本不足，不下结论」或者「t = −0.79，没有任何正期望证据」的体检卡。轴 C 拿开仓时刻的估计和平仓
时刻的实现做三个对拍，直接回答「第 2 轮那个 ATR 粗估分子到底准不准」「`min_confidence=0.50` 这个
门槛作用的是不是一个有区分度的量」。轴 D 把结论喂回模型——模型今天在一个**绩效真空**里做决策，
它不知道自己历史胜率 41%、赔率 1.17、而 41% 的胜率要求赔率 1.44。轴 E 修掉三条会让上面四轴的分母
失真的口径缺陷，并在启动弹窗里补一句今天最该说而没说的话：**你设的止损离现价 500 bps，而这个周期
一根 K 线的 ATR 只有 28 bps——你的风险框架和你的交易尺度不在一个量级上。**

一条贯穿本轮的纪律：**所有新读数都必须带 `coverage`，所有结论都必须带样本量门禁**。本仓库已有
「用 2 笔样本冒充统计结论」的成文禁令（`exit_attribution._MIN_WIN_RATE_SAMPLES`、
`aiTradeCapture.PROFIT_CAPTURE_MIN_PAIRS`、`_PRESET_COMPARISON_MIN_SAMPLES`），本轮把它升级成
**产品级不变式**：任何一个比例类读数，若它的分母不是全部往返，就必须在同一处把覆盖率显示出来。
一个在 8% 子样本上算出来的捕获率和一个在 100% 样本上算出来的胜率并排放着而不说明，本身就是误导。

---

## 二、勘察实证：九条缺陷（逐条附 `file:line` 与实测数字）

> 全部数字来自用户本机生产库 `%APPDATA%/aragon-desktop/aragon.db`（161 个 `ai-trade` run），
> 复算脚本见 §附录 A，**可逐条复现**。红灯基线见 §十.2。

### D-1（阻断级）计划风险单位与实际交易尺度差两个数量级，整套风险语言从未被触碰

**证据**：193 笔开仓的 `trades.stop_loss_pct` **全部**为 `0.05`、`take_profit_pct` **全部**为 `0.10`
（出厂默认 `config.py:137-138` 的 `ai_trade_default_sl_pct` / `ai_trade_default_tp_pct`），计划盈亏比
中位数 2.000。而每笔往返的实际价格位移 `|closePx − openPx| / openPx`：

| 统计量 | p10 | 中位 | p90 |
|---|---:|---:|---:|
| 实际位移（bps） | 2.3 | **13.9** | 37.2 |

按 `R = 已实现净额 / (止损距离 × 名义)` 逐笔复算（191 笔可算，`stop_loss_price` 在 193/193 笔上都有）：

| 统计量 | p10 | 中位 | p90 | \|R\|<0.2 占比 | R≥1 占比 |
|---|---:|---:|---:|---:|---:|
| R 倍数 | −0.053 | **−0.011** | 0.063 | **100.0%** | **0.0%** |

**结论**：典型交易走完了它自己止损距离的 **2.83%**。这意味着：
- 交易所侧 OCO 止损止盈（`ai_trade_default_sl/tp_pct`）在这些会话里**从未有过任何生效机会**；
- 利润保护层的武装线 `ai_trade_profit_guard_arm_return_pct=0.05`（`config.py:306`）同理——见 D-5；
- 第 1 轮预设表里的 `ai_trade_stop_rr=2.0`（`discipline.py:58/75/94`）描述的是一个从未发生过的世界。

**同时**：13.9 bps 的中位位移，与第 2 轮实测的一次市价往返成本 **13.0 bps**（`cost_model` 默认常数
`5+5+2×(1.0+0.5)`）几乎相等。**半数交易的毛位移不够付一次往返成本**——这是「毛额期望 ≈ 0、净额期望
为负」最直接的机械解释，也是 §零第 1 条的成因。

**为什么至今没人发现**：全仓库没有任何地方计算 R 倍数，也没有任何地方把「实际位移」与「往返成本」
并排显示。实测
`grep -rnE '\brMultiple\b|\br_multiple\b' services/quant/app apps/web/lib apps/web/components`
**零命中**（注意必须加词边界：`over_multiple` / `atr_multiple` 会产生假阳性）。

### D-2（阻断级）跨会话盈利读数不存在；唯一的跨会话卡把「比例的均值」当成「均值的比例」

**证据 1（不存在）**：`_summarize_pairs`（`db/ai_auto_repositories.py:106-164`）产出的
`realizedPnlUsd` / `winRate` / `captureRatio` 全部以**一个 group** 为作用域；
`list_ai_trade_groups`（同文件 `:277-311`）逐组装配后原样返回，
前端 `AiTradeStatsHistoryList.tsx` 只渲染逐组卡片与 `total`（组数），
**没有任何一处求跨组的累计净额、池化胜率或期望**。实测组级样本分布：

```
28 组，其中 18 组有成交；每组往返数 = 1,1,1,2,3,4,5,6,6,8,9,9,10,12,13,16,17,39
```

**证据 2（算错）**：`_summarize_preset`（`db/ai_auto_repositories.py:452-464`）对
`winRate` / `edgeVetoRate` / `openConversion` 三个比例类读数走的是
`_mean([r["winRate"] for r in rows])`——**各组比值的算术平均**，而各组的分母从 1 笔到 39 笔不等。
实测差异：

| 口径 | 胜率 |
|---|---:|
| 池化（Σwins / Σtrips，162 笔） | **40.74%** |
| 各组胜率的算术平均（18 组） | **36.11%** |

4/18 组只有 ≤2 笔往返，它们对结论的权重与那个 39 笔的组**完全相同**。
`_mean` 的 docstring（`:467-470`）只承诺了「不把 None 当 0 平均进去」，没意识到等权本身就是错的。

### D-3（阻断级）估计↔实现从不结算：两个字段既无消费者，绑定关系也可能错

**证据 1（无消费者）**：`RoundTrip.cost_bps` / `RoundTrip.edge_bps`（`ai_auto/attribution.py:33-34`）
在写入之后**全仓无任何读点**——`AttributionLedger.stats()`（`:106-137`）只读会话级的
`self._last_cost_bps`（`modelCostBps`），从不遍历 `self._trips` 的这两个字段；
`grep -rn "\.edge_bps" services/quant` 除 `edge_gate.py` 自身与 `cost_runtime.py:463` 的写点外零命中。
于是「门禁分子准不准」在结构上无法被证伪。

**证据 2（绑定可能错）**：`CostRuntime.note_gate`（`ai_auto/cost_runtime.py:455-466`）只在
`out.gated`（动作 ∈ `open_long`/`open_short`）时才转发给账本，所以 `_last_edge_bps` 记的是
**最近一次受门禁约束的轮**的读数——它**只在「开仓与平仓之间没有第二次开仓意图」时才等于本笔往返的
开仓估计**。一次加仓尝试、一次被否的反向开仓，都会把读数改绑到一笔**没有发生的交易**上，
而 `record_close`（`attribution.py:90`）照抄不误。

**证据 3（实盘上恒为空）**：`evaluate_edge_gate` 在 `k_min <= 0` 时直接 `return None`
（`edge_gate.py:210-212`）⇒ `note_gate` 永不被调用 ⇒ 两个读数恒 `None`。第 1 轮之前 `k_min` 默认 0，
实测生产库 **114 个带 `aiStats` 的 run 全部** `"modelCostBps": null`。

**证据 4（没有实现侧）**：`RoundTrip` 里根本没有「已实现 bps」这个量；`notional_usd`
（`attribution.py:88-89`）取的是**平仓腿**的 `price × qty × ctVal`，而任何 bps 化的分母都应当是
**开仓名义**。

### D-4 置信度门槛作用在一个从未被校准的量上

`balanced` 预设把 `ai_trade_min_confidence` 装配为 **0.50**（`ai_auto/discipline.py:69`；`steady` 0.60、
`aggressive` 0.35），`decider` 对置信度不达标的开仓意愿直接改写为 `("hold", "low_confidence")`
（`ai_auto/decider.py:359`）。而 `edge_gate.py:19-20` 的模块 docstring 亲口写着：

> 刻意不用 `decision.confidence`——**D-7 已确认置信度未校准**，把未校准的胜率先验当门禁输入会把
> D-7 的错误传播进来且无法证伪。

**同一个量，在一处被判定为「未校准、不可作门禁输入」，在另一处被当成硬门槛用**。而全仓没有任何
把「决策时的 confidence」与「该笔往返的实现盈亏」连起来的读数：`session._decisions`
（`session.py:267`，`deque(maxlen=50)`）记 confidence，`trades` 表记 pnl，两者从不 join；
`_compute_ai_stats`（`session.py:1798-1845`）只产出 `avgConfidence` 这一个**与结果无关**的均值。

### D-5 确定性退出纪律几乎从未参与，95.3% 的退出由模型自己做出

实测 193 笔平仓成交的 `explain.kind` 分布：

| kind | 笔数 | 占比 |
|---|---:|---:|
| `atomic`（Agent 主动平仓） | 184 | **95.3%** |
| `session_stop`（会话止盈损 / 停止flatten） | 6 | 3.1% |
| `external_close`（OCO / 外部干预） | 2 | 1.0% |
| `profit_guard`（利润保护棘轮） | **1** | **0.5%** |

跨 114 个带 `aiStats` 的 run 汇总：`decisionRounds` 合计 **16065**；在**装配了利润保护读数的那 18 个
run（合计 2019 决策轮）**里，`profitGuardArmedRounds` 合计 **22**（**1.09%** 的轮次曾处于武装态），
`profitGuardExits` 合计 **1**。周期铁律层同理：`minHoldVetoes` 1、`unconfirmedVetoes` 6、
`hysteresisVetoes` 2。

**这与 D-1 是同一件事的两面**：武装线按「保证金收益率 5%」（`config.py:306`）设，而典型持仓只走了
止损距离的 2.83%，保护层根本等不到武装的那一刻，模型就已经把仓平了。三轮迭代造出来的退出纪律
（铁律层 / 周期层 / 保护棘轮 / 换手预算 / 交易所兜底）**在真实资金上几乎全部空转**，而赔率 1.014
低于盈亏平衡所需的 1.474——**亏损不在开仓侧，在退出侧**。

### D-6 每一层新纪律都带着个位数样本上线，且这件事没有任何地方被记录

按「该 run 的 `metricsJson.aiStats` 是否含对应读数块」切分 193 笔往返：

| 纪律层 | 该层存在期间产生的往返数 | 中位持仓 |
|---|---:|---:|
| 持仓周期铁律（`aiStats.horizon`） | **5** | 119.3 min |
| 利润保护（`aiStats.profitGuardArmedRounds`） | **21** | — |
| 以上都没有（老会话） | **188** | 19.1 min |

那 5 笔的中位持仓从 19.1 min 抬到 119.3 min，**方向是对的**——但 5 笔样本不构成任何结论，而产品里
没有任何一处告诉用户「你现在看到的这条结论建立在 5 笔样本上」。第 2 轮的 `preset-comparison`
是第一次尝试跨会话取证，但它按预设分桶（4 档），而**第 1 轮之前的全部历史都落进 `unknown` 桶**
（`_group_preset`，`db/ai_auto_repositories.py:423-429`），无法与任何具名档合并。

### D-7 孤儿平仓的腿费会结转到下一笔往返

`_pair_round_trips`（`db/ai_auto_repositories.py:92-103`）：

```python
for t in sorted(trades, key=lambda x: x.id or 0):
    leg_fee += float(getattr(t, "fee", 0.0) or 0.0)
    if t.side in ("long", "short"):
        open_trade = t
    elif open_trade is not None:          # ← 孤儿平仓不进这一支
        trips.append(_Trip(open_trade, t, leg_fee, cost_basis))
        open_trade = None
        leg_fee = 0.0                     # ← 只有配对成功才清零
```

docstring（`:77`）写的是「孤儿平仓 / 悬挂开仓**丢弃**」，但孤儿平仓的 `fee` 已经进了 `leg_fee`
且不会被清零，它会被**记到下一笔往返头上**。当前生产库零孤儿（docstring `:83` 已实测），
故未暴露；但 `flatten` 失败后的残留、外部干预平仓、会话重启后的补记，正是会造出孤儿平仓的路径,
而本轮轴 B/C 的全部 bps 化读数都以 `leg_fee` 为分子。

### D-8 `holdRate` 与 `decisionRounds` 两个分母并排展示，无任何口径标注

`_compute_ai_stats`（`session.py:1798-1845`）里 `holdRate = counts["hold"] / sample`，其中 `sample`
是 `self._decisions` 这个 `deque(maxlen=50)`（`session.py:100` `_DECISIONS_MAX = 50`）的长度，
而同一份 dict 里的 `decisionRounds` 取 `self.rounds_completed`（**全程**真实轮数）。
前端 `AiTradeStatsDetail.tsx:325-326` 把它们并排渲染：

```tsx
<Kpi label="决策轮数" value={`${aiStats.decisionRounds}`} />
<Kpi label="观望占比" value={fmtPct(aiStats.holdRate)} />
```

实测极端断面：某会话 `decisionRounds` 上千而 `holdRate` 的分母恒 ≤50。用户读到的是
「这 N 轮里观望了 X%」，实际是「最近 50 轮里观望了 X%」。docstring 里写清楚了，UI 上一个字没有。

### D-9（阻断级，评审新增 [P0-4]）89% 的历史往返没有费率记录，而「零」与「没记」在数据上不可区分

费率记账随 `96f554d`（2026-07-25）才上线。实测按 run 创建日切分的 `fee != 0` 成交条数：

```sql
select date(r.created_at), t.side, count(*), sum(case when ifnull(t.fee,0)!=0 then 1 else 0 end)
from trades t join strategy_runs r on r.id = t.run_id
where r.mode='ai-trade' group by 1,2;
```

| run 日期 | 成交条数 | 其中 `fee != 0` |
|---|---:|---:|
| 2026-07-06 ~ 2026-07-22（11 天） | 344 | **0** |
| 2026-07-26 | 32 | 32（开 16 / 平 16） |
| 2026-08-12 | 10 | 10（开 5 / 平 5） |

于是 193 笔往返里 **172 笔（89.1%）的腿费恒 0**，21 笔有真实费率（且**开平两腿都有**）。后果有三层：

1. **全样本 `medianRealizedCostBps` = 0.000**——不是「交易免费」，是「没记」。v1 §5.1 与 §3.3 问二
   写的 5.8 bps 只可能来自带费子样本（实测该子样本中位是 **7.01 bps**，p10 6.99 / p90 10.02）。
2. **`travelToCostRatio = medianMoveBps / medianCostBps` 在全样本上恒 `None`**（分母 ≤0 即 None）——
   §零第 2 条点名「必须上屏」的那个数，在默认视图上一个都看不到。
3. **轴 D 会对模型说「一次往返成本约 0.0 bps」**——把「交易成本不会因为方向判断正确而消失」这句
   纪律，变成一句「交易几乎不要钱」的反向暗示。这是本轮唯一一条会**直接把模型推向更高换手**的路径。

**这与 `closeFeeCoverage`（`attribution.py:118-119`）是同一个问题的两个时代**：会话内早就有了覆盖率
分母，跨会话侧一个都没有。故本轮所有 bps 化成本读数一律带 `feeCoverage`，见 §3.2.1 与 §3.3 问二。

---

## 三、技术设计

### 3.1 轴 A —— 往返结算账本（Trip Ledger）

#### 3.1.1 机制：单点冻结、单点结算、搭既有信封落库

三个落点全部复用**已经存在**的唯一收口，不新增任何遍历现场：

```
决策轮（session._apply_edge_gate 内部，紧贴 _emit_edge_gate_trace）      ← [P0-1] 不是 :760 那个调用点
  └─ cost.note_estimate(build_round_estimate(out, decision, wake_kind))  ← 冻结本轮估计（轮级，轮末清零）
下单那一刻（session._execute_decision 拿到 clOrdId 之后）                 ← [P0-2] 限价单的唯一出路
  └─ cost.claim_estimate(cl_ord_id)                                      ← 轮级槽 → 待成交槽（按 clOrdId）
成交事件（session._dispatch，全部 executor 事件的唯一收口）
  ├─ side ∈ {long,short}  → cost.note_open(trade, seq=...)        ← 已存在的调用点，本轮扩参
  └─ side == "close"      → cost.record_close(trade)              ← 已存在的调用点，本轮返回结算产物
解释信封（session._inject_explain_meta，**在既有 exitSource 打标之后**）  ← [P1-1] 顺序是硬约束
  └─ cost.apply_trip_meta(meta, trade)                            ← 新增一行，与既有 apply_explain_meta 同款
```

**[P0-1] 写点为什么必须下沉进 `_apply_edge_gate`**：v1 把它放在 `_run_decision_round` 的
`:760-761`（`_apply_edge_gate` 的调用点），但那个函数返回的是 `(out.decision, degrade or out.reason)`
——`out` 是**函数局部量**，调用点根本拿不到（`session.py:1096-1108`）。写点下沉到
`_apply_edge_gate` 内部后，它与 `note_gate` 的真实转发点（`_emit_edge_gate_trace`，`session.py:1114`）
同址，§十.6 接缝 1 那条「必须与 `note_gate` 同址」的要求这才真的成立；session 净增仍是 **+1 行**。

**[P0-2] 为什么必须再有一个「待成交槽」**：`ai_trade_limit_orders_enabled` **默认 True**
（`config.py:196`），限价开仓单会 resting 若干轮，其采纳成交由盯市 tick 发出
（`_monitor_tick → executor.reconcile → _dispatch`，`session.py:487-491`）——那时轮级槽早已被
`finally` 里的 `end_round()` 清零（`session.py:812-815`）。只有轮级槽的话，**默认订单类型的每一笔
开仓都拿不到估计**，轴 C 问一与问三永远是空表，而 §十一.1 会把这件事误记成「等新会话积累即可」。
故：下单时按 `clOrdId` 把估计从轮级槽移进**待成交槽**（`dict[str, RoundEstimate]`，TTL 取
`ai_trade_limit_order_ttl_s`，容量上限 8，超时/超量按 FIFO 丢弃）；`note_open` 先按
`trade["clOrdId"]` 认领，认领不到再回退轮级槽（市价单恒走回退路径，行为与 v1 一致）。

**为什么这样修 D-3 的错绑**：`note_estimate` 写的是**轮级**槽位，`_run_decision_round` 的 `finally`
里（已有 `self._discipline.funnel.end_round()` 那一行的同一处）清零。于是只有「本轮真的产生了开仓
成交、或本轮真的发出了一张仍在挂的开仓单」才能把估计绑上去；一次被否的开仓意图在轮末就被清掉，
绝无可能改绑到别的往返上（G-3 / G-3b 双向钉住）。

#### 3.1.2 纯核心 `ai_auto/trip_ledger.py`（新文件，≤240 行）

```python
@dataclass(frozen=True)
class RoundEstimate:
    """一轮受门禁约束的开仓意图在**决策那一刻**的全部估计（轮级，绝不跨轮存活）。"""
    est_edge_bps: float | None       # edge_gate 的毛 edge 粗估（k_min<=0 ⇒ None）
    est_cost_bps: float | None       # cost_model 的往返成本
    edge_source: str | None          # atr | take_profit | prior
    cost_confidence: str | None      # ok | degraded
    decision_confidence: float | None
    wake_kind: str | None
    ord_type: str                    # market | limit

@dataclass(frozen=True)
class OpenSnapshot:
    """开仓成交那一刻冻结的断面（`(side, seq)` 是 epoch 身份，与 profit_guard / horizon 同源）。"""
    seq: int; side: str; ts: int; wall_ts: float
    entry_px: float; qty: float; notional_usd: float
    open_fee_usd: float              # [P0-3] 开仓腿费**累加**（部分成交多腿；缺失记 0.0 但另置 flag）
    open_fee_known: bool             # 任一开仓腿的 fee 缺失 ⇒ False ⇒ 全部成本类 bps 一律 None
    stop_px: float | None; take_px: float | None
    estimate: RoundEstimate | None

@dataclass(frozen=True)
class SettledTrip:
    """一笔已结算往返。所有 bps 量的分母恒为**开仓名义**（entry_notional_usd）。"""
    seq: int; side: str; entry_ts: int; exit_ts: int; hold_seconds: float
    entry_px: float; exit_px: float; notional_usd: float
    gross_pnl_usd: float; net_pnl_usd: float
    fee_usd: float | None            # [P0-3] **往返双腿**腿费；`open_fee_known=False` ⇒ None
    fee_known: bool                  # 双腿费率齐备才为 True（D-9 的老数据恒 False）
    realized_gross_bps: float | None     # gross / notional × 1e4（带符号）
    realized_net_bps: float | None
    realized_cost_bps: float | None      # fee  / notional × 1e4（恒 ≥ 0）
    move_bps: float | None               # |exit_px − entry_px| / entry_px × 1e4（无符号，D-1 的那个量）
    risk_usd: float | None               # |entry_px − stop_px| × qty × ct_val
    r_multiple: float | None             # net_pnl_usd / risk_usd
    planned_rr: float | None             # |take_px − entry_px| / |entry_px − stop_px|
    stop_atr_mult: float | None          # 计划止损距离 / 开仓时 ATR（None = 无 ATR 读数）
    exit_source: str                     # [P1-1] **读** meta.exitSource（九值枚举），本模块不调 classify_live
    # 以下六项为示意行（非合法 Python，[P2-3]）：字段名与 RoundEstimate 同名同类型直接透传
    # est_edge_bps / est_cost_bps / edge_source / cost_confidence / decision_confidence / wake_kind
    edge_error_bps: float | None         # realized_gross_bps − est_edge_bps（None = 缺估计）
    cost_error_bps: float | None         # realized_cost_bps  − est_cost_bps（fee_known=False ⇒ None）
```

纯函数（**形参一律聚合，满足 CLAUDE.md 的「≤5」**，[P1-11]；仓库先例
`manager.AiTradeGroupStartRequest:116` / `tradability.TradabilityInputs:57`）：

```python
@dataclass(frozen=True)
class SettleContext:
    """结算的全部环境量（口径开关 / 合约乘数 / ATR / 退出来源）。"""
    net_mode: bool; ct_val: float; atr_px: float | None; exit_source: str

def settle(open_snap: OpenSnapshot, close_trade: dict, ctx: SettleContext) -> SettledTrip | None: ...
def trip_meta_wire(trip: SettledTrip) -> dict: ...   # → explain.meta 的加法键（缺失即省略）
def trip_from_meta(meta: dict, trade_row) -> dict | None: ...  # 查询侧回读（轴 B 用）
```

**十条硬约束（写进 docstring，评审逐条核；`1b` / `2b` / `7` / `8` 为评审新增）**：

1. **口径单点**：毛/净的换算规则**只有一份**——`net_mode=True` ⇒ `trade["pnl"]` 已是净额、
   毛额 = `pnl + fee`；否则相反。与 `attribution.record_close`（`attribution.py:71-75`）与
   `exit_attribution.ExitLedger.record`（`exit_attribution.py:120-122`）**逐字同构**，
   本模块**绝不写第三种口径**（该文件 `:99-100` 已立下这条禁令）。
1b. **[P0-3] 成本口径必须是「往返双腿」，而这与约束 1 不矛盾**：`RoundTrip.fee_usd`
   （`attribution.py:84-90`）记的是**平仓腿**费率，而查询层的 `_Trip.leg_fee_usd`
   （`ai_auto_repositories.py:92-103`）记的是**上一笔平仓之后到本笔平仓为止的全部腿费**。
   两者相差整整一条开仓腿——实测 21 笔带费往返**开平两腿都有 fee**，若 `settle` 沿用平仓腿，
   `realizedCostBps` 会恰好只有追溯复算的一半，而轴 C 问二拿它去比**往返**成本估计（13.0 bps），
   得到的「成本模型高估一倍」将完全是口径造成的假象。故：`SettledTrip.fee_usd`
   = `open_snap.open_fee_usd + close_fee`，**并在 docstring 里显式写明它与 `RoundTrip.fee_usd`
   不同名不同义**；wire 键名用 `roundTripFeeUsd` 以杜绝误读。`net_pnl_usd` / `gross_pnl_usd`
   仍严格走约束 1（`trade["pnl"]` 的口径由 `net_mode` 决定，**不因本条而改变**）。
2. **bps 分母恒为开仓名义**：`notional_usd = entry_px × qty × ct_val`。`ct_val` 取
   `AttributionLedger.ct_val`（`attribution.py:46`，未知合约 ⇒ 0）；`ct_val == 0` ⇒ 全部 bps 量
   与 `risk_usd` / `r_multiple` **一律 None**，**绝不填 0**（0 会被聚合读成「成本为零」）。
2b. **[P0-4] 「没记费率」必须与「费率为零」可区分**：任一腿的 `fee` 缺失 / 非有限
   ⇒ `fee_known=False` ⇒ `fee_usd` / `realized_cost_bps` / `cost_error_bps` **一律 None**。
   `fee == 0.0` 且该腿确实带了这个键 ⇒ `fee_known=True`（真实的零费率成交是合法样本）。
3. **`r_multiple` 缺任一输入即 None**：`stop_px` 缺席、`|entry_px − stop_px| == 0`、`ct_val == 0`
   任一成立 ⇒ None。R 是本轮最重要的读数，一个被 0 兜底的 R 比没有 R 更糟。
4. **`stop_atr_mult` 的 ATR 取开仓那一刻的 `session._atr_px`**（确认根 ATR，价格单位），
   经 `profit_guard.sanitize_atr` 归一（与 `edge_gate.py:109` 同一条降级判据）；不可用 ⇒ None。
5. **结算失败恒返回 None 且绝不抛**：本模块跑在真实资金成交的落库路径上
   （`_dispatch` → `_persist_trade`），任何异常都会中断成交落库。全部计算包在
   `_finite()` 归一里，无 `raise` 语句（用例 G-4 用 AST 断言）。
6. **`trip_meta_wire` 的键一律「算不出即省略」**，不是 `None`。理由：`explain.meta` 是历史查询侧
   `trip_from_meta` 的输入，缺键与 `null` 在老数据上必须表现一致，且省略键让 `explain_json`
   在低价值往返上不膨胀。
7. **[P1-1] `exit_source` 只读、不判**：`SettledTrip.exit_source` 必须取
   `explain["meta"]["exitSource"]`——那是 `session._inject_explain_meta:361-363` 打的**唯一**标，
   且受 `ai_trade_exit_attribution_enabled` 门控。本模块**绝不自己调 `classify_live`**：那会同时
   （a）造出第二个分类现场，违反 `exit_attribution.py:11-14` 的「单一收口打标」纪律；
   （b）绕过那个总闸，让关掉归因的部署反而多出一份归因。缺席 ⇒ `"unknown"`。
   **由此推出一条顺序硬约束**：`apply_trip_meta` 必须写在 `_inject_explain_meta` 里既有
   `exitSource` 打标**之后**（即 `explain["meta"] = meta` 之前的最后一步），不能与
   `apply_explain_meta`（`session.py:353`）并排——那里 `exitSource` 还没写进去。
8. **[P2-6] `seq` 只能取 `OpenSnapshot.seq`**：`_dispatch` 的平仓分支是**先** `_position_seq += 1`
   （`session.py:318`）**再** `record_close`（`:322`），当轮读 `self._position_seq` 恒比本笔往返大 1。

#### 3.1.3 `AttributionLedger` 的扩展（`attribution.py`，+~70 行 → ~245 行）

```python
def note_estimate(self, est: RoundEstimate | None) -> None:      # 轮级槽位（不进 _trips）
def claim_estimate(self, cl_ord_id: str | None) -> None:         # [P0-2] 轮级槽 → 待成交槽（按 clOrdId）
def end_round(self) -> None:                                     # 清零轮级槽位（D-3 的错绑修复）
def note_open(self, trade: dict, *, seq: int, now_wall: float,
              atr_px: float | None) -> None:                     # 建 / 续 OpenSnapshot（幂等累加腿费）
def record_close(self, trade, *, net_mode=None) -> dict | None:  # 结算 + 返回 meta wire
def settled_trips(self) -> list[SettledTrip]:                    # 会话级只读（轴 D 的会话段）
def realized_stats(self) -> dict:                                # aiStats.realized 加法块
```

**`note_open` 的幂等语义（[P0-3] × §十.6 接缝 2）**：同一 `seq` 的第二次调用（开仓部分成交）
**只累加 `qty` / `notional_usd` / `open_fee_usd` 并按成交量加权更新 `entry_px`**，绝不重置
`estimate` / `ts` / `wall_ts` / `stop_px`。任一腿缺 `fee` 键 ⇒ `open_fee_known=False` 且此后不可再翻真。

`record_close` 的既有行为**逐字保留**（`_lifetime` 四累加器、`_close_samples`、`_trips` 环形缓冲
全部不动），只**追加**结算并返回 meta wire——调用方拿不到 wire（None）时行为与合入前逐字相同。

`realized_stats()` 产出的 `aiStats.realized` 加法块（会话级，落 `metrics_json`）：

```json
{"realized": {"trips": 12, "medianMoveBps": 13.9, "medianRealizedNetBps": -1.2,
              "medianRMultiple": -0.01, "reachedOneRShare": 0.0,
              "medianStopAtrMult": 17.8, "edgeErrorMedianBps": null,
              "costErrorMedianBps": 0.4, "estimateCoverage": 0.83, "feeCoverage": 1.0}}
```

`estimateCoverage` = 带 `est_edge_bps` 的往返数 / 总往返数；`feeCoverage` = `fee_known` 的往返数 /
总往返数（[P0-4]）——**这两个分母就是 §一 那条产品级不变式的落地**。

**[P1-2] 这个块必须有渲染落点，否则它就是死键。** v1 只写了「随 `_finalize_run` 落库」，而 §四的
前端计划里两张新卡读的是**端点**、`AiTradeStatsDetail` 只改 hint 与 R 列——**全仓没有任何一处读
`aiStats.realized`**。本仓库最高频的缺陷形状（已复发 ≥4 次）就是这个。故本轮指定两个落点，
且**共用 `lib/aiTradeProfitability.ts` 的同一套格式化器**（绝不在两处各写一份口径）：

| 通道 | 落点 | 内容 |
|---|---|---|
| 历史 | `AiTradeStatsDetail` 的 KPI 段（`aiStats.realized`） | 「本会话已实现」三项：位移中位 / R 中位 / 已实现净额 bps 中位 + 两个 coverage |
| 实时 | `AiTradeStatsLive` 读 `costSnapshot.realized`（既有通道，§5.4） | 同三项，样本 <3 笔即整段不渲染（不显示一个 1 笔样本的中位数） |

#### 3.1.4 `CostRuntime` 的薄转发（`cost_runtime.py`，+~35 行 → ~596 行）

`note_estimate` / `claim_estimate` / `end_round` / `apply_trip_meta` / `realized_stats` 五个薄方法；
`note_open` 扩一个形参（`atr_px`）。`merge_into`（`:544`）追加 `realized` 块——
经既有的 `merge_cost_stats`（键冲突即抛，`stats_ext.py:53-64`），新键 `realized` 不与任何既有键撞。

#### 3.1.5 `session.py` 的净增：**≤ 6 行**（硬预算，G-12）

`session.py` 今天 **1845 行**，早已越过 CLAUDE.md 的 800 行硬线（存量债 R-6，见 §十一）。本轮承诺
「不因本轮而更糟」：

| 位置 | 改动 | 行数 |
|---|---|---:|
| `_apply_edge_gate` 内、`_emit_edge_gate_trace` 之后（`:1105-1107`）**[P0-1]** | `self._cost.note_estimate(build_round_estimate(out, decision, self._wake.last_round_kind))` | +1 |
| `_execute_decision` 拿到回执之后（`:988` 附近）**[P0-2]** | `self._cost.claim_estimate(executed.get("clOrdId"))` | +1 |
| `_run_decision_round` 的 `finally`（`:812-815`） | `self._cost.end_round()` | +1 |
| `_dispatch` 开仓支（`:330`） | `note_open(...)` 追加 `atr_px=self._atr_px` | +0（改既有行） |
| `_inject_explain_meta`（`:361-364`，**exitSource 打标之后**）**[P1-1]** | `self._cost.apply_trip_meta(meta, trade)` | +1 |
| `_build_payload`（`:1474` 的 `self._cost.inject(...)` 同址）**[P2-5] 轴 D** | `self._track.inject(payload)` | +1 |
| 合计 | | **+5**（预算 ≤6，留 1 行余量） |

`build_round_estimate` 是 `trip_ledger` 的模块级纯函数（**不在 session 里拼 dict**）。
轴 D 的 `_track` 是 `TrackRecordRuntime`（`track_record.py`），沿用 `PriorRuntime` / `CostRuntime`
的同款「运行时对象 + `inject(payload)`，总闸关 ⇒ 整键缺席」范式，session 里只留这一行。

### 3.2 轴 B —— 跨会话盈利体检（Profitability Physical）

#### 3.2.1 纯核心 `ai_auto/profitability.py`（新文件，≤300 行）

输入是一串**已归一的往返 dict**（由查询层从 `trades` + `explain.meta` 装配，见 3.2.2），
输出是端点回执的完整形状。全部纯函数、零 IO、零 pandas。

```python
POOLED_KEYS = ("roundTrips","netPnlUsd","grossPnlUsd","feeUsd","winRate","avgWinUsd",
               "avgLossUsd","payoff","breakevenPayoff","expectancyUsd","expectancyR","rSamples",
               "tStat","medianHoldSeconds","medianMoveBps","medianCostBps","travelToCostRatio",
               "feeCoverage","feeTrips","costBasisAssumedTrips")

def pooled(trips: list[dict]) -> dict: ...
def risk_profile(trips: list[dict]) -> dict: ...          # R 分布 + 止损利用率
def slice_by(trips: list[dict], key: str) -> list[dict]:  # 六个分层切片共用
def calibration(trips: list[dict]) -> dict: ...           # 轴 C
def verdict(pooled_out: dict, gates: VerdictGates) -> dict: ...   # [P1-11] 形参聚合
```

**八条硬约束**：

1. **一切比例都池化**：`winRate = Σwins / Σtrips`，**永远不对比值求平均**（D-2）。
   `slice_by` 的每一行同样池化，且每行必带 `trips` 让读者看到分母。
   **每个比例/中位数类读数都必须与它自己的分母同行出现**——`expectancyR` 的分母是 `rSamples`
   （R 可得子样本），与 `expectancyUsd` 的 `roundTrips` **不是同一个数**（[P2-4]）。
2. **`breakevenPayoff = (1 − winRate) / winRate`**，`winRate ∈ {0, 1}` ⇒ None（不造 ∞）。
   它与 `payoff` 必须**成对**出现在同一行——单独一个 1.014 没有任何意义。
3. **[P0-4] 成本类读数只在带费子样本上算，且分母必须同行上屏**：
   `feeTrips` = `feeKnown` 且 `roundTripFeeUsd` 可得的往返数，`feeCoverage = feeTrips / roundTrips`；
   `medianCostBps` 的分母**是 `feeTrips` 而不是 `roundTrips`**——实测 193 笔里 172 笔（89.1%）
   的腿费恒 0 且那是「没记」不是「零」（D-9），全样本中位会算出 **0.000**。
   `travelToCostRatio = medianMoveBps / medianCostBps`，任一为 None、`medianCostBps <= 0`
   或 `feeCoverage < 0.05` ⇒ None。这是 §零第 2 条的那个数，**必须上屏且必须带 `feeCoverage`**（G-9）。
4. **`tStat` 复用 `attribution._t_stat`**（`attribution.py:156-165`）——**绝不另写一份**
   （两份 t 统计量迟早分叉）。`n < 2` 或零方差 ⇒ None。
5. **结论表冻结**（下表逐字进代码常量，前端只读不复制；**判据自上而下取首个命中**）：

| # | code | 触发条件 | 中文标题 |
|---:|---|---|---|
| 1 | `insufficient_samples` | `roundTrips < min_trips` | 样本不足，不下结论 |
| 2 | `no_edge_yet` | **`tStat is None`** 且 `expectancyUsd <= 0` | 尚无正期望证据 |
| 3 | `positive_not_significant` | **`tStat is None`** 且 `expectancyUsd > 0` | 有正期望迹象，但尚不显著 |
| 4 | `negative_significant` | `expectancyUsd < 0` 且 `tStat <= -t_threshold` | 已实现负期望，且统计显著 |
| 5 | `no_edge_yet` | `\|tStat\| < t_threshold` 且 `expectancyUsd <= 0` | 尚无正期望证据 |
| 6 | `positive_not_significant` | `expectancyUsd > 0` 且 `tStat < t_threshold` | 有正期望迹象，但尚不显著 |
| 7 | `positive_significant` | `expectancyUsd > 0` 且 `tStat >= t_threshold` | 已实现正期望，且统计显著 |

> **[P1-6] 第 2、3 行是评审补的，不是冗余**：约束 4 自己写明 `tStat` 会是 `None`，而第 4~7 行全是
> 与阈值做大小比较——Python 里 `None <= -2.0` 直接抛 `TypeError`，端点当场 500。
> `min_trips` 可低到 5（§六.1 的 `ge=5`），5 笔零方差并非不可能，合成用例更是随手就能构造。
6. **[P0-5] 口径推定必须随读数一起上屏**：`costBasisAssumedTrips` = 所属 run 的 `config_json`
   无 `costBasis` 键、走 `ai_trade_history_legacy_cost_basis` 推定的往返数（实测 **172/193**）。
   一个 89% 靠推定的净额与一个 100% 已知的净额并排放着而不说明，本身就是误导（§一 的不变式）。
7. **`verdict` 的 `detail` 必须复述分母**：至少含 `roundTrips`、`tStat`、以及 `feeCoverage < 1` 时
   的覆盖率一句。前端不拼数字（§5.1 契约纪律 ③）。
8. **本模块的 `winRate` 未做样本外验证，`docstring` 里写死「不得作为 `kelly_fraction` 的 `p_win`
   输入」**（R-10 的落地）。

**文案禁令（沿用第 1 轮 [P1-10]）**：结论表与卡片文案**一律不得**出现「已校准 / 经回测验证 /
预期年化 / 稳定盈利」等措辞。这是一张历史统计，不是收益承诺。用例 G-10 用源码正则守门
（注意 `core.autocrlf=true`，正则须写 `\r?\n`）。

#### 3.2.2 查询层（`db/ai_auto_repositories.py`，+~60 行 → ~560 行）

```python
@dataclass(frozen=True)
class TripQuery:
    """轴 B 端点的过滤条件（[P1-11]：形参聚合，满足 CLAUDE.md 的「≤5」）。"""
    inst_id: str | None = None; bar: str | None = None; since_ts: int | None = None
    preset: str | None = None; horizon_profile: str | None = None

async def load_profitability_trips(session, query: TripQuery) -> list[dict]:
    """全部 ai-trade run 的往返样本（逐 run 分桶 → `_pair_round_trips` → 并入 explain.meta 结算键）。"""
```

**[P0-3] 货币类键的唯一权威是追溯复算，`explain.meta` 只作展示与对拍**：
`netPnlUsd` / `grossPnlUsd` / `roundTripFeeUsd` / `notionalUsd` / `rMultiple` / `moveBps`
六个键**一律由查询层按 `_Trip` + `_run_cost_basis` 现算**；`meta` 里的同名键**只用于成交行展示**
（§5.3）并在 G-6 里逐笔对拍（相对误差 < 1e-6，`fee_known=False` 的往返豁免）。
理由：这六个键在两条路径上都算得出来，而「两条路径各算一份」正是本仓库反复出事的形状——
必须**先定权威、再谈对拍**，不能让「谁先写谁赢」决定用户看到哪一个数。
`estEdgeBps` / `estCostBps` / `edgeSource` / `costConfidence` / `decisionConfidence` / `wakeKind` /
`stopAtrMult` 七个键则**只有 `meta` 一个来源**（追溯不可得）。

**单一配对真源**：**必须**复用 `_pair_round_trips`（`:74-103`），不得另写配对
（该函数的 docstring `:80-85` 记着「按 `ts` 排序会破坏真实开→平交替」这条已踩过的坑）。
run 级维度（`preset` / `horizonProfile` / `bar` / `instId` / `costBasis`）从 `StrategyRun` 与
`metricsJson.aiStats` 取；trip 级维度（`estEdgeBps` / `decisionConfidence` / `rMultiple` / …）
从 `exit.explain_json → meta` 取，缺键 ⇒ 该维度上该笔不计入分母（**而不是计入并填 0**）。

**老数据的退化路径（关键）**：轴 A 之前的 193 笔往返没有 `explain.meta` 结算键，但下列量
**可以逐笔追溯复算**，本轮**必须**复算而不是留空——否则新卡片在上线当天是一张空表：

| 量 | 追溯公式 | 覆盖 |
|---|---|---|
| `netPnlUsd` / `roundTripFeeUsd` / `grossPnlUsd` | 既有 `_Trip.net_pnl` / `leg_fee_usd` / `gross_pnl`（**按 `_run_cost_basis` 逐 run 解析口径**，[P0-5]） | 193/193（其中 **172 笔口径靠推定**） |
| `moveBps` | `\|exitPx − entryPx\| / entryPx × 1e4` | 193/193 |
| `unitUsd`（= `qty × ctVal`） | `\|grossPnl\| / \|exitPx − entryPx\|`（`ct_val` 在此**代数抵消**） | 191/193（位移为 0 的两笔除外） |
| `notionalUsd` | `entryPx × unitUsd` | 191/193 |
| `realizedCostBps` | `legFee / notionalUsd × 1e4`，**仅在 `feeKnown` 时** | **21/193**（[P0-4]：其余 172 笔腿费恒 0 = 没记，见 D-9） |
| `riskUsd` / `rMultiple` | `\|entryPx − stopPx\| × unitUsd`；`net / riskUsd` | 191/193 |
| `plannedRr` | `\|takePx − entryPx\| / \|entryPx − stopPx\|` | 193/193 |
| `exitSource` | **只读** `explain.meta.exitSource`，缺则 `"unknown"`（[P1-1]：查询侧同样不得另判） | 193/193 |
| `estEdgeBps` / `decisionConfidence` / `stopAtrMult` | **无法追溯** ⇒ 缺席 ⇒ 只影响轴 C 的 `coverage` | 0/193 |

**`feeKnown` 的追溯判据（[P0-4]）**：该往返区间内**开、平两腿都存在非 `NULL` 的 `fee`** ⇒ True。
实测生产库上只有 2026-07-26 与 2026-08-12 两批共 21 笔满足（且都是开平成对带费）。
`feeKnown=False` 的往返**不进任何成本类分母**，也不因此被丢弃——它们照常进 `winRate` / `expectancy`
/ `rMultiple`（那三个量与费率记录无关）。

`unitUsd` 的代数抵消是本轮的关键实现技巧：`ctVal` 不在 `trades` 表里，但
`grossPnl = ±(exitPx − entryPx) × qty × ctVal` ⇒ `qty × ctVal = |grossPnl| / |exitPx − entryPx|`。
**它必须与 3.1.2 约束 2 的实盘路径产出同一个 `notionalUsd`**——两条路径各算一个名义额就是两个口径
（G-6 双向对拍：同一笔往返走实盘结算与走追溯复算，`notionalUsd` 相对误差 < 1e-6）。

#### 3.2.3 端点与前端

见 §五.1 与 §四。新卡 `ProfitabilityCard` 挂在 `AiTradeStatsHistoryList` 的**列表之上**——
用户打开历史页第一眼看到的应该是「这套系统到底赚不赚钱」，而不是第 1 页的 20 张卡片。

**[P1-4] 挂点必须在空态早返回之前。** `AiTradeStatsHistoryList.tsx:44-50` 在 `items.length === 0`
时**直接 `return` 一段空态文案**，挂在 `:58` 的 `PresetComparisonCard` 已经中了这一刀；而
`/ai-trade/history` 会 `exclude_group_id` 掉**正在运行**的那一组——只跑过一组的用户在会话运行期间
整个历史页恒空，两张新卡一个都看不到。故：两张新卡（各自 `useQuery` 自取数、无历史即整卡不渲染，
沿用 `PresetComparisonCard` 的成文范式）必须提到 `items.length === 0` 判断**之前**，空态文案下移为
列表区的空态而不是整页的空态。

### 3.3 轴 C —— 校准三问

三问共用一个形状：`{n, coverage, ...读数, conclusive}`。`coverage` = 有该读数的往返数 / 总往返数。
**`coverage < 1` 时前端必须显示覆盖率**（G-8 用例守门）。

**问一：门禁分子准不准？**

```json
"edgeNumerator": {"n": 0, "coverage": 0.0, "medianEstEdgeBps": null,
                  "medianRealizedGrossBps": null, "medianErrorBps": null,
                  "signAgreementRate": null, "conclusive": false}
```

`medianErrorBps = median(realized_gross_bps − est_edge_bps)`。第 2 轮的分子是
`min(atr_target_mult × ATR, OCO 止盈距离)`，是「若走对能吃到多少」的**幅度**而非期望，
故理论上应当**系统性高于**已实现毛额中位（后者含走错的那一半）。`signAgreementRate` =
「估计为正且实现为正」的比例——**分子恒正**（幅度），故这一项实际度量的是胜率，
必须在 UI hint 里写清楚，不得被读成「门禁预测准确率」。

**问二：成本模型准不准？**

```json
"costModel": {"n": 21, "coverage": 0.1088, "medianEstCostBps": null,
              "medianRealizedCostBps": 7.01, "medianErrorBps": null, "conclusive": false,
              "modelConstantBps": 13.0, "note": "193 笔里只有 21 笔带费率记录（2026-07-26 起）"}
```

**[P0-4] 已实现侧的真实覆盖是 21/193（10.9%），不是 191/193。** v1 把「名义额算得出来」当成了
「费率记得下来」：`realizedCostBps` 的分子是腿费，而 172 笔往返的腿费恒 0 —— 那是**没记**，
不是**免费**（D-9）。故本块的 `n` / `coverage` 一律按 `feeTrips` 计，`conclusive` 要求
`feeTrips >= ai_trade_profitability_bucket_min_trips`（默认 10）**且** `coverage >= 0.05`。

上线当天用户能看到的是「在有费率记录的 21 笔上，你实际每笔往返付了 **7.01 bps**（p10 6.99 /
p90 10.02），而成本模型按 **13.0 bps** 估」——这个 1.9 倍的差距本身就是结论，
**但必须与那个 10.9% 的分母同框出现**，否则它就是一个用 11% 子样本冒充全局的断言。

**问三：置信度有没有区分度？**

```json
"confidenceReliability": {"n": 0, "coverage": 0.0, "buckets": [], "monotonic": null,
                          "conclusive": false}
```

桶边界冻结为 `[0.0,0.35) [0.35,0.5) [0.5,0.7) [0.7,0.9) [0.9,1.0]`（与三档预设的
`min_confidence` 0.35/0.50/0.60 对齐，让「若改档会怎样」可读）。每桶给 `trips / winRate /
expectancyUsd / medianRMultiple`，桶内 `trips < min_bucket_trips`（默认 10）⇒ 该桶比例类读数 None。
`monotonic` = 胜率是否随桶单调不减（只在**全部桶都可得**时给结论，否则 None）。

**这一问是 D-4 的唯一出路**：`min_confidence=0.50` 该不该存在，只能由「0.35~0.5 桶与 0.5~0.7 桶的
胜率差」回答。本轮**只出读数，不改门槛**（改门槛是下一轮的事，且必须以本读数为依据）。

### 3.4 轴 D —— 战绩回喂决策

#### 3.4.1 两段口径，各有各的来源

```python
payload["trackRecord"] = {
  "session": {...},   # 本会话至今：AttributionLedger.settled_trips() —— 零 IO，每轮现算
  "history": {...},   # 同 (instId, bar) 的历史池化：启动期一次性快照，会话内不刷新
}
```

**决策轮零 IO 是硬约束**（沿用 `PriorRuntime` 的同款纪律，`prior_runtime.py:7`）：`history` 段由
`api/ai_auto._resolve_track_record` 在**启动流程**里查一次（与 `_resolve_prior_snapshots`
`api/ai_auto.py:274` 并列），经 `AiTradeGroupStartRequest` → `manager._build_member` →
`AiTradeSession.__init__` 注入。**会话内不刷新**——刷新需要在盯市 tick 上开 DB 会话，
那是一条今天不存在的 IO 面，收益（几小时内战绩几乎不变）远小于风险。这条取舍写进 docstring。

#### 3.4.2 装配门禁（四条，任一不满足 ⇒ **整键缺席** ⇒ prompt 逐字回退）

1. `ai_trade_track_record_enabled == False`；
2. `history.roundTrips < ai_trade_track_record_min_trips`（默认 20）；
3. `verdict.code == "insufficient_samples"`；
4. 该 `(instId, bar)` 组合无任何历史往返。

**绝不用「全标的合计」冒充本标的战绩**——BTC 15m 的胜率与 PEPE 1m 的胜率不是同一个东西
（实测按标的切分：ETH −5.30 / SOL +1.17 / BTC +0.44 / DOGE +1.06 / PEPE −1.48 USDT）。

#### 3.4.3 prompt 段（`ai_auto/track_record.py` 的纯函数产出，`prompts.py` 只加装配行）

```
【你的实测战绩】（{instId} {bar}，历史 {n} 笔往返 + 本会话 {m} 笔）
- 胜率 {winRate}，赔率 {payoff}；该胜率下的盈亏平衡赔率是 {breakevenPayoff}。
- 一笔往返的价格位移中位数 {medianMoveBps} bps。            ← 成本句见下（可整行缺席）
- 已实现 R 倍数中位 {medianR}；{reachedOneRShare} 的往返触及过 ±1R。
纪律：交易成本不会因为方向判断正确而消失；位移撑不过成本的交易，方向对了也是亏。
以上是历史统计，不是对未来的承诺；样本不足时本段不出现。
```

**成本句是一条独立的、可整行缺席的行（[P0-4] 阻断级）**：

```
- 而一次往返成本约 {medianCostBps} bps（取自 {feeTrips} 笔有费率记录的往返）。
```

装配判据：`feeCoverage >= ai_trade_track_record_min_fee_coverage`（默认 **0.5**）
**且** `feeTrips >= 10`。不满足 ⇒ **整行省略**。理由：生产库上 89% 的往返腿费恒 0（D-9），
若照 v1 的模板渲染，这一行会变成「一次往返成本约 **0.0** bps」——把本轮唯一一条
「别做撑不过成本的交易」的纪律，翻译成「交易几乎不要钱」的反向暗示。
**本轮唯一一条会直接把模型推向更高换手的路径就在这里，故它是硬门禁而不是文案偏好。**

**四条文案硬约束**：① 数值全部由后端格式化好下发，`prompts.py` 不做任何算术
（沿用 `/ai-trade/horizon-profiles` 的既有纪律）；② **禁止 markdown 粗体**——后端文案里的 `**`
在四个渲染点会显示成字面星号，本仓库已复发 **3 次**（第 2 轮 RV-2、第 5 轮终审）；
③ 不得出现任何指令性措辞让模型「必须减少交易」——这一段是**证据**，不是门禁；
④ **每一个数都必须带它自己的分母**（历史段 `{n}` 笔、会话段 `{m}` 笔、成本句 `{feeTrips}` 笔）。

#### 3.4.4 [P1-9] 「负期望战绩回喂」的回归风险必须被度量

把一份 t = −1.45 的战绩每轮喂给模型，最坏结果是模型**再也不开单**——本仓库已经因为
「把不确定翻译成否决」出过 **4 次**永不开单事故（第 2 轮 §3.1 的 98.6% 拦截、反思致永不开单等）。
本轮不因此放弃轴 D（让模型在绩效真空里决策更糟），但**必须留下可回滚的度量**：

1. `aiStats.trackRecordShown: bool` 随会话落库（加法键），使任何一次事后对账都能按「有没有喂」分臂；
2. **G-14b**：同一组 payload 在装配 / 不装配两臂上跑满 20 轮，比较
   `openFunnel.intents / rounds`——若装配臂的开仓意图率**归零**，视为回归，
   `ai_trade_track_record_enabled` 立即回滚为 `False`（该总闸就是为此而设）；
3. 文案里**不出现任何否定式祈使句**（约束 ③ 的用例化：G-16 的词表增加「不要 / 停止 / 必须减少」）。

### 3.5 轴 E —— 风险单位对齐 + 三条口径修复

#### 3.5.1 E-0（主项）启动期风险单位体检：**只提示，不改**

复用第 2 轮已经建好的可交易性探针窗口（`ai_trade_tradability_window_bars`，默认 200 根，
`tradability_probe.py` 已取过数）——**零新增取数、零新增网络面**。新纯函数：

```python
# ai_auto/tradability.py（+~55 行 → ~456 行）
@dataclass(frozen=True)
class BracketInputs:
    """[P1-11] 形参聚合（CLAUDE.md ≤5）：全部取自探针已算出的那一份断面，零新增取数。"""
    atr_p50_bps: float | None; stop_source: str; stop_bps_p50: float | None
    stop_pct: float | None; take_pct: float | None
    stop_atr_mult: float | None; max_atr_mult: float

def bracket_verdict(inputs: BracketInputs) -> dict | None: ...
```

**必须先判「止损从哪来」，再判倍数（否则会在一个已经正确定标的会话上报警）**：

> **[P1-8] `stop_source` 不能从配置反推。** v1 按「`ai_trade_stop_atr_mult > 0` 且 `sizing_enabled`」
> 判 `atr`，但真实止损距离要过 `resolve_stop_distance`（`sizing.py:303-345`）的三段：
> **成本下界 → 用户红线上界 → 冲突判定**。`floor_above_cap` 时**整条 ATR 覆盖回退成用户的固定
> `slPct`**（该函数注释：「挂一个刚好等于红线的止损是假装可行」），成本下界胜出时距离也不是
> `k×ATR`；第 2 轮实测 BTC 1m 有 **62.3%** 的断面 `2×ATR` 比单次成本还小。于是「按配置反推」会在
> 恰恰最该报警的断面上说「已按 ATR 定标，不报警」——**判据取自不携带该信息的入参**，
> 与第 3 轮 4 条 P0 同形。故 `stop_source` 与 `stop_bps_p50` **一律取探针已经算出的止损来源七态**
> （轴 B 的 `stopBps` / `stopInsideCostRate` 同源，`tradability.py:234-249`），本轮零新增计算。

| `stop_source`（七态归并） | 判定 | 结论 |
|---|---|---|
| `atr` / `atr_capped`（ATR 真的胜出） | 止损距离 ≡ `stop_bps_p50` | `level="atr_scaled"`，陈述「已按 ATR 定标（{k}×ATR ≈ {x} bps）」，**不报警** |
| `atr_floored`（成本下界抬起了 ATR） | 同上，但来源是成本 | `level="cost_floored"`，陈述「止损被成本下界抬到 {x} bps —— 这个周期的 ATR 比一次往返成本还小」，**不报警**（它已经是对齐后的结果） |
| `fixed` / `floor_above_cap`（含 `stop_atr_mult == 0`、预设 `off`、下界高于红线整条回退） | `ratio = stop_pct × 1e4 / atr_p50_bps` | `ratio > max_atr_mult`（默认 8）⇒ `level="oversized"` |
| ATR 不可得 / `stop_pct` 为 0 / 七态为 `unknown` | — | `None` ⇒ 整块缺席（fail-open，与探针同极性） |

`oversized` 的文案（纯文本、无 `**`）：

> 止损离现价约 500 bps，而这个周期一根 K 线的波动（ATR 中位）只有 28 bps ——
> 相当于 17.8 根 K 线的波动幅度。按历史行为，这样的止损几乎不会被触发，
> 交易所侧止损、止盈与利润保护的武装线都不会有生效机会。
> 若希望风险框架与交易尺度对齐，可考虑把单仓止损设到约 0.56%（= 2×ATR），
> 或改用「均衡 / 稳健」纪律预设（它们会按 2×ATR 自动定标）。

**三条硬约束**：① **绝不自动改用户填的 `slPct`**（第 2 轮 §九 非目标 3 的成文禁令：
「自动改参数是本仓库明令禁止的静默夹紧形状」）；② 建议值 `suggestedStopPct` 由后端算好下发，
前端不做任何换算；③ 它**不是**启动阻断——按钮永不禁用（沿用第 2 轮 RV-1 确立的 fail-open 极性）。

呈现落点：`AiTradeTradabilityFields.tsx`（294 行，已是第 2 轮为规避 800 行硬线下沉出来的组件）
在既有可交易性结论下方追加一行，**与既有结论共用同一个折叠区**。

#### 3.5.2 E-1 孤儿平仓腿费不结转（修 D-7）

```python
    elif open_trade is not None:
        trips.append(_Trip(open_trade, t, leg_fee, cost_basis))
        open_trade = None
        leg_fee = 0.0
    else:                       # ← 新增：孤儿平仓，按 docstring 的承诺「丢弃」
        leg_fee = 0.0
```

用例：`open, close, close, open, close` ⇒ 第二笔往返的 `leg_fee` **不含**那笔孤儿平仓的费用。

#### 3.5.3 E-2 预设对照改池化（修 D-2 的另一半）

`_group_comparison_row` 改为返回**分子分母**而非比值：

```python
return {"netPnlUsd": ..., "roundTrips": ..., "wins": wins,
        "edgeGated": gated, "edgeVeto": veto, "openIntents": intents, "openFilled": filled}
```

`_summarize_preset` 求和后再除。**`winRate` 的定义因此变了**——这是一次**有意的口径变更**，
必须在卡片上标注「池化口径」，并在 spec §十一 如实记账（老截图上的数字会变）。
`samples`（局数）保留不变，`conclusive` 判据不变。

**[P1-7] `wins` 这个分子今天不存在，故 `_summarize_pairs` 必须加一个加法键。**
`_summarize_pairs`（`ai_auto_repositories.py:106-164`）只返回 `winRate`，`_group_comparison_row`
（`:432-449`）就是从它取数的；v1 一边要求返回 `wins`、一边又写「`_summarize_pairs` 一个字不改」，
两条无法同时成立（靠 `round(winRate × tradeCount)` 反推分子是把浮点误差引进判据，禁止）。
处置：允许 `_summarize_pairs` **追加**一个 `wins: int` 键（纯加法，既有键的值逐位不变），
**G-19 的断言相应改为「既有键逐位不变」**；`_aggregate_group` 仍一个字不改。

#### 3.5.4 E-3 双分母标注（修 D-8）

**只改前端标签，不改后端口径**（改后端口径 = 追溯改写已落库的历史数字）：

```tsx
<Kpi label="决策轮数（全程）" value={`${aiStats.decisionRounds}`} />
<Kpi label="观望占比（近 50 轮）" value={fmtPct(aiStats.holdRate)} />
```

> **[P1-10] 为什么不是 `hint`。** 其一，`Kpi` 的签名是 `{label, value, color}`
> （`AiTradeStatsDetail.tsx:445`），根本没有 `hint` 形参；其二——更要紧——**悬停才可见的提示
> 等于没显示**：触屏读不到、组件用例的 `getByText` 也断言不到，那正是本轮要修的
> 「口径只写在 docstring 里、UI 上一个字没有」的同一个病。故口径直接写进两个 label，
> 用例断言**可见文本**含「近 50 轮」与「全程」。若未来要补更长的解释，再给 `Kpi`
> 加一行常显小字（不是 `title` 属性）。

---

## 四、文件 / 模块变更计划

> 行数预算是**硬验收判据**（G-12）。括号内为当前行数。CLAUDE.md 的 800 行硬线在
> `session.py`(1845) / `manager.py`(957) / `prompts.py`(835) / `schemas.py`(2427) 上**已是存量债**，
> 本轮承诺「不因本轮而更糟」，故这四个文件的净增预算极小（见 §十一）。

### 后端（`services/quant`）

| 文件 | 类型 | 预算 | 意图 |
|---|---|---:|---|
| `app/ai_auto/trip_ledger.py` | 新 | ≤240 | 轴 A 纯核心：`RoundEstimate` / `OpenSnapshot` / `SettledTrip` / `SettleContext` / `settle` / `trip_meta_wire` / `trip_from_meta` / `build_round_estimate` |
| `app/ai_auto/profitability.py` | 新 | ≤300 | 轴 B 纯核心：`pooled` / `risk_profile` / `slice_by` / `verdict` + 冻结结论表 |
| `app/ai_auto/calibration.py` | 新（**[P2-1] 预授权**） | ≤160 | 轴 C 纯核心：三问 + `coverage` 规则。**只在 `profitability.py` 触 300 行时才建**；宁可拆文件也不许删注释压行（先例：`prior_service.py`） |
| `app/ai_auto/track_record.py` | 新 | ≤130 | 轴 D 纯核心：`build_track_record` / `format_track_record_text`（prompt 段全文在此）+ `TrackRecordRuntime`（`inject(payload)`，镜像 `PriorRuntime`） |
| `app/ai_auto/attribution.py` (173) | 改 | +≤75 | `note_estimate` / `end_round` / `note_open` / `record_close` 追加结算 / `settled_trips` / `realized_stats` |
| `app/ai_auto/cost_runtime.py` (561) | 改 | +≤35 | 四个薄转发 + `note_open` 扩参 + `merge_into` 追加 `realized` 块 |
| `app/ai_auto/tradability.py` (401) | 改 | +≤55 | E-0 `bracket_verdict` + 进 `stats_wire` |
| `app/ai_auto/tradability_probe.py` (220) | 改 | +≤25 | 把 `bracket_verdict` 装进探针回执（复用既有 ATR 窗口，零新增取数） |
| `app/ai_auto/session.py` (1845) | 改 | **净增 ≤6** | 三处薄调用（§3.1.5 的表） |
| `app/ai_auto/prompts.py` (835) | 改 | **净增 ≤4** | `trackRecord` 段装配一行 + import（全文在 `track_record.py`） |
| `app/ai_auto/manager.py` (957) | 改 | **净增 ≤10** | `track_record` 快照透传到 `AiTradeSession` |
| `app/api/ai_auto.py` (719) | 改 | **净增 ≤40** | 新端点 1 个 + `_resolve_track_record` 装配（**超预算即下沉到新 `ai_auto/track_record_service.py`**，沿用第 2 轮 `tradability_probe.py` 的先例） |
| `app/db/ai_auto_repositories.py` (500) | 改 | +≤65 | `load_profitability_trips`（复用 `_pair_round_trips`）+ E-1 + E-2 |
| `app/schemas.py` (2427) | 改 | +≤40 | `AiTradeProfitabilityOut` 及其子模型；`trade_out` 提 `rMultiple` 到顶层；**[P0-6] `AiTradeTradabilityOut` 追加 `bracket: dict \| None = None`** —— 该模型是显式字段清单（`:1237-1267`），不声明就会被 `response_model` 静默吞掉，其 docstring `:1244-1246` 已把这条教训写死 |
| `app/config.py` (770) | 改 | +≤8 | 7 个新 settings（见 §六.1） |

**冻结（本轮零改动，G-13）**：`app/engine/simulator.py`、`app/ai_auto/cost_model.py`、
`app/ai_auto/cost_text.py`（既有冻结门禁
`test_ai_trade_acceptance_wiring.py::test_shared_execution_files_have_zero_diff` `:445-451`）；
外加本轮自设：`edge_gate.py` / `base_rules.py` / `hold_horizon.py` / `profit_guard.py` /
`turnover_discipline.py` / `drawdown_brake.py`（六个门禁纯核心的判定式一个字节不动）。

### 前端（`apps/web`）

| 文件 | 类型 | 预算 | 意图 |
|---|---|---:|---|
| `lib/aiTradeProfitability.ts` | 新 | ≤180 | 类型 + 纯格式化 + 结论表中文映射 + `coverage` 显示规则 |
| `components/Agent/aiTradeStats/ProfitabilityCard.tsx` | 新 | ≤200 | 轴 B 卡：结论徽标 + 池化 KPI + R 分布 + 六个切片折叠 |
| `components/Agent/aiTradeStats/CalibrationCard.tsx` | 新 | ≤160 | 轴 C 卡：三问三块，每块带 `coverage` 与「不下结论」态 |
| `components/Agent/aiTradeStats/AiTradeStatsHistoryList.tsx` (132) | 改 | +≤15 | 在列表**之上、空态早返回之前**挂两张新卡（[P1-4]），空态文案下移 |
| `components/Agent/aiTradeStats/AiTradeStatsDetail.tsx` (489) | 改 | +≤30 | E-3 两个 label 改口径词（[P1-10]）+ `aiStats.realized` 三项 KPI（[P1-2]）+ 往返行传 `rMultiple` |
| `components/Agent/aiTradeStats/AiTradeStatsLive.tsx` | 改 | +≤15 | [P1-2] 实时侧读 `costSnapshot.realized`，与历史侧共用同一格式化器 |
| `components/Chart/AutoTradeRoundTripRow.tsx` (90) | 改 | +≤12 | **[P1-3] 唯一的往返行渲染器（live-auto / paper 同用）**：新增**可选** `showR?: boolean`，不传即逐字节不变（沿用该文件既有 `bar?` 的成文先例） |
| `components/Agent/aiTradeStats/PresetComparisonCard.tsx` (99) | 改 | +≤8 | E-2 的「池化口径」标注 |
| `components/Agent/AiTradeTradabilityFields.tsx` (294) | 改 | +≤45 | E-0 风险单位体检行 |
| `lib/aiTradeHistoryApi.ts` (41) | 改 | +≤10 | 新端点方法 |
| `lib/aiTradeHistoryTypes.ts` (156) | 改 | +≤25 | 新回执类型（**仓库里没有 `AiTradeHistoryTrade` 这个类型**，成员成交是 `lib/types.ts` 的共享 `Trade`，见下一行） |
| `lib/types.ts` | 改 | +≤6 | **[P1-3] `Trade.rMultiple?: number \| null`** —— 与 `wallTs` / `peakPnlUsd` 同址（`:306-332`），那两个键就是同一条「搭 explain 信封落库、`trade_out` 提回顶层」范式的先例 |
| `lib/aiTradeTypes.ts` (1413) | 改 | +≤20 | `tradability.bracket` 类型 |

### 测试（新增）

| 文件 | 覆盖 |
|---|---|
| `tests/test_ai_trade_trip_ledger.py` | 轴 A 纯核心：结算算术、`ct_val==0`/无止损/零位移的 None 退化、口径同构对拍、AST 断言无 `raise` |
| `tests/test_ai_trade_trip_session.py` | 轴 A 装配：估计→开仓→平仓的绑定链；**D-3 回归**（开仓后插一轮被否的开仓意图，结算仍绑对） |
| `tests/test_ai_trade_profitability.py` | 轴 B/C 纯核心：池化 vs 均值反例、结论表五态、`breakevenPayoff` 边界、切片分母、`coverage` |
| `tests/test_ai_trade_profitability_api.py` | 端点：空历史 / 样本不足 / 过滤参数 / 追溯复算路径（含 G-6 双向对拍） |
| `tests/test_ai_trade_track_record.py` | 轴 D：四条装配门禁、整键缺席时 prompt 逐字回退、文案无 `**`、无收益承诺词 |
| `tests/test_ai_trade_bracket_verdict.py` | 轴 E-0：`atr_scaled` 不报警、`fixed` 超倍报警、ATR 缺失整块缺席、建议值算术 |
| `tests/test_ai_trade_history_pooling.py` | 轴 E-1/E-2：孤儿平仓腿费不结转；预设对照池化与等权反例 |
| `apps/web/lib/aiTradeProfitability.test.ts` | 结论表映射、`coverage<1` 必显、无禁用词 |
| `apps/web/components/Agent/aiTradeStats/ProfitabilityCard.test.tsx` | 样本不足态、结论徽标、切片渲染 |

---

## 五、接口设计

### 5.1 `GET /api/py/ai-trade/history/profitability`

**不守 AI 总闸**（沿用 `/ai-trade/horizon-profiles`、`/ai-trade/priors/rebuild`、
`/ai-trade/history/preset-comparison` 的先例：读一张历史表不下任何单）。

> **[P1-5] 路由注册顺序是硬约束**：本条**必须**排在 `/ai-trade/history/{group_id}`（`api/ai_auto.py:516`）
> **之前**，否则会被那条路径参数吞掉、当成一个 `group_id` 去查并返回 404。
> `preset-comparison` 已经踩过这一次并把教训写在 `api/ai_auto.py:498-499`；G-21 钉住。

查询参数（全部可选，缺省 = 全部历史）：`instId`、`bar`、`sinceTs`、`preset`、`horizonProfile`。

```json
{
  "sampleTrips": 193,
  "minTrips": 30,
  "conclusive": true,
  "costBasisAssumedTrips": 172,
  "pooled": {
    "roundTrips": 193, "netPnlUsd": -7.4185, "grossPnlUsd": -3.5437, "feeUsd": 3.8748,
    "winRate": 0.4041, "avgWinUsd": 0.2092, "avgLossUsd": -0.2064,
    "payoff": 1.014, "breakevenPayoff": 1.474,
    "expectancyUsd": -0.0384, "expectancyR": -0.011, "rSamples": 191, "tStat": -1.446,
    "medianHoldSeconds": 1271, "medianMoveBps": 13.9,
    "medianCostBps": 7.01, "travelToCostRatio": 1.98,
    "feeTrips": 21, "feeCoverage": 0.1088, "costBasisAssumedTrips": 172
  },
  "risk": {
    "rSamples": 191, "rP10": -0.053, "rP50": -0.011, "rP90": 0.063,
    "reachedOneRShare": 0.0, "withinFifthRShare": 1.0,
    "plannedRrMedian": 2.0, "stopUtilizationP50": 0.0283,
    "medianStopAtrMult": null, "stopAtrCoverage": 0.0
  },
  "slices": {
    "byBar": [{"key": "15m", "trips": 98, "netPnlUsd": 2.759, "winRate": 0.44, "expectancyUsd": 0.028, "medianRMultiple": 0.004}],
    "byInstId": [...], "byExitSource": [...], "byPreset": [...],
    "byHorizonProfile": [...], "byConfidenceBucket": [...]
  },
  "calibration": {
    "edgeNumerator": {"n": 0, "coverage": 0.0, "conclusive": false, "medianEstEdgeBps": null,
                      "medianRealizedGrossBps": -0.6, "medianErrorBps": null, "signAgreementRate": null},
    "costModel": {"n": 21, "coverage": 0.1088, "conclusive": false, "medianEstCostBps": null,
                  "medianRealizedCostBps": 7.01, "medianErrorBps": null, "modelConstantBps": 13.0},
    "confidenceReliability": {"n": 0, "coverage": 0.0, "conclusive": false, "buckets": [], "monotonic": null}
  },
  "verdict": {"code": "no_edge_yet", "title": "尚无正期望证据",
              "detail": "193 笔往返的净额期望为 -0.0384 USDT/笔（t=-1.45），统计上与 0 不可区分；其中 172 笔的盈亏口径为推定、仅 21 笔带费率记录。"}
}
```

**五条契约纪律**：① 算不出的量一律 `null`，**绝不填 0**；② 每个比例类读数所在的对象必带它自己的
分母（`trips` / `n` / `coverage` / `rSamples` / `feeTrips`）；③ `verdict.detail` 由后端生成
（前端不拼数字）；④ **[P1-12]** 口径推定用 `costBasisAssumedTrips`（**计数**，实测 172/193）而不是
一个布尔——布尔表达不了「89% 的样本靠推定」这件事；语义沿用 `_run_cost_basis`
（`db/ai_auto_repositories.py:167-180`），UI 必须把「推定」与「已知」分开显示；
⑤ **[P0-4]** `medianCostBps` / `travelToCostRatio` 的分母是 `feeTrips`，它们与 `feeCoverage`
**必须在同一个对象里**下发，前端也**必须在同一行渲染**（G-9）。

### 5.2 `GET /api/py/ai-trade/tradability` 回执扩展（加法）

该端点是**单 `(instId, bar)` 回执**（弹窗按成员各请求一次，`api/ai_auto.py:459-478`；v1 写的
「逐成员回执」措辞不准，[P2-2]）。在其中追加 `bracket`（算不出 ⇒ **整键缺席**）：

> **[P0-6] 必须同时在 `schemas.AiTradeTradabilityOut` 上显式声明 `bracket: dict | None = None`。**
> 该端点挂着 `response_model=AiTradeTradabilityOut`，而那是一张**显式字段清单**
> （`schemas.py:1237-1267`）——未声明的 wire 键会被 FastAPI **静默裁掉**，轴 E-0 上线即死键。
> 这条教训就写在该模型自己的 docstring `:1244-1246` 里（「子块用宽类型可保证后端新增读数时不会被
> 悄悄裁掉」），本轮照办：用 `dict | None` 而不是嵌套模型。G-23 钉住。

```json
{"bracket": {"level": "oversized", "stopSource": "fixed", "stopBps": 500.0,
             "atrP50Bps": 28.1, "stopAtrMult": 17.8, "suggestedStopPct": 0.0056,
             "text": "止损离现价约 500 bps，而这个周期一根 K 线的波动（ATR 中位）只有 28 bps……"}}
```

### 5.3 成交 wire 扩展（加法，零迁移）

**新键一律走 `explain.meta`**（`_BASE_OPEN_KEYS` 冻结了 executor 成交 dict 的键集，
`tests/test_ai_trade_fee_ledger.py:287-289`；给成交 dict 加顶层键会当场打红）：

```json
{"explain": {"kind": "atomic", "meta": {
  "exitSource": "agent", "wallTs": 1786536012,
  "tripSeq": 3, "holdSeconds": 1271.4, "notionalUsd": 145.44,
  "moveBps": 13.9, "realizedNetBps": -1.2, "realizedCostBps": 7.0, "roundTripFeeUsd": 0.102,
  "rMultiple": -0.008, "plannedRr": 2.0, "stopAtrMult": 17.8,
  "estEdgeBps": 41.2, "estCostBps": 13.0, "edgeSource": "atr",
  "costConfidence": "ok", "decisionConfidence": 0.62, "wakeKind": "candle"}}}
```

`exitSource` 这一键**是既有的、由 `_inject_explain_meta:363` 写**——上表列出它只是为了说明
`apply_trip_meta` 跑在它之后并**读**它（[P1-1]），轴 A **不写**这个键。
`roundTripFeeUsd` / `realizedCostBps` 在 `fee_known=False` 时**整键省略**（[P0-4]）。

`schemas.trade_out`（`:1384-1403`）**只把 `rMultiple` 提到顶层**（往返行要显示的列），
与既有 `wallTs` / `peakPnlUsd` 的提法逐字同款；其余键由查询层直接读 `explain.meta`。
前端侧对应的类型落点是 `lib/types.ts` 的共享 `Trade`（[P1-3]），渲染落点是
`components/Chart/AutoTradeRoundTripRow.tsx` 的可选 `showR?`——**非 AI 调用点不传即逐字节不变**。

### 5.4 WS `status` / 落库 `aiStats`

- `aiStats.realized`（§3.1.3 的加法块）随 `_finalize_run` 落库，并进 `costSnapshot` 两条通道
  （`snapshot()` 与 `snapshot_if_due()` 同源，`cost_runtime.py:519-529`）。
- **本轮不新增 WS `status` 键**——`realized` 走既有 `costSnapshot` 通道即可，
  少一个「后端算了但没有渲染落点」的机会（本仓库最高频的缺陷形状）。

---

## 六、数据模型

### 6.1 新增 `Settings` 字段（`app/config.py` 末尾追加，**零 DB 迁移**）

```python
# ---- 已实现 edge 校准与盈利体检（ai-trade-realized-edge-calibration）----
ai_trade_trip_ledger_enabled: bool = True          # 轴 A 总闸；False ⇒ explain.meta 逐字回退
ai_trade_profitability_min_trips: int = Field(default=30, ge=5, le=1000)     # 轴 B 下结论下限
ai_trade_profitability_t_threshold: float = Field(default=2.0, ge=0, le=10)  # 显著性判据
ai_trade_profitability_bucket_min_trips: int = Field(default=10, ge=2, le=200)  # 轴 C 桶内下限
ai_trade_track_record_enabled: bool = True         # 轴 D 总闸；False ⇒ payload 整键缺席
ai_trade_track_record_min_trips: int = Field(default=20, ge=5, le=1000)      # 轴 D 装配下限
# [P0-4] 成本句的独立门禁：带费率记录的往返占比低于此值 ⇒ prompt **整行省略**成本句。
# 绝不向模型下发一个由「没记费率」算出来的 0.0 bps 往返成本（D-9）。
ai_trade_track_record_min_fee_coverage: float = Field(default=0.5, ge=0, le=1)
ai_trade_bracket_check_enabled: bool = True        # 轴 E-0 总闸；False ⇒ bracket 整键缺席
ai_trade_bracket_max_atr_mult: float = Field(default=8.0, gt=0, le=100)      # 固定 pct 止损的告警上限
```

**`8.0` 这个数的依据**：出厂默认 500 bps 止损对 15m BTC 的 ATR 中位 28.1 bps 是 **17.8 倍**；
对 1H 的 56.5 bps 是 8.8 倍；对 5m 的 15.9 bps 是 31 倍。取 8 使 1H 及以上的长周期恰好不报警、
而实测「止损从未被触碰」的 5m/15m 全部报警。**这个数没有经过回测校准**，UI 与文档不得声称
已校准（沿用第 1 轮 [P1-10]）。

### 6.2 `metricsJson.aiStats` 新增（**加法键，零迁移**）

`aiStats.realized`（§3.1.3）。经 `merge_cost_stats` 的键冲突即抛守门（`stats_ext.py:53-64`）。

### 6.3 `trades.explain_json` 的 `meta` 新增（**加法键，零迁移**）

§5.3 的 14 个新键（`exitSource` / `wallTs` 是既有键，不计），**算不出即省略**；
`roundTripFeeUsd` / `realizedCostBps` 在 `fee_known=False` 时同样整键省略（[P0-4]）。
`explain_json` 是既有列（`db/models.py`），
`append_live_trade`（`db/repositories.py:285-309`）已把整个 explain 序列化落库——
**这正是第 1 轮轴 D 为 `wallTs` 建立的搭车范式**（`session.py:345-348` 的注释里写着
「`append_live_trade` 是逐列手抄，新键自动随行落库不成立，故搭 explain 信封的车」）。

### 6.4 内存形状

`AttributionLedger` 新增三个字段：`_round_estimate: RoundEstimate | None`（轮级）、
`_open: OpenSnapshot | None`（持仓周期级）、`_settled: deque[SettledTrip]`（容量沿用 `_CAPACITY=200`）。
**与既有 `_trips` 并存不合并**——`_trips` 是已发布 `stats()["cost"]` 的口径来源，动它就是
追溯改写已发布 KPI（第 2 轮 RV-3 的教训）。

---

## 七、测试与验收判据

> 20 条。每条都必须能指到一个具体用例；「实现了」不等于「达成了」。

**轴 A**

- **G-1** 结算算术：给定 `entry=100, exit=101, qty=10, ctVal=0.01, fee=0.05, netMode=True`，
  `notionalUsd==10.0`、`moveBps==100.0`、`realizedCostBps==50.0`、`grossPnl==net+fee`，逐位相等。
- **G-2** None 退化三态：`ct_val==0` / 无 `stop_px` / `entry_px==exit_px` 时，
  对应的 bps 量与 `r_multiple` **恒 None 且绝不为 0**。
- **G-3（D-3 回归，阻断级）**：开仓成交后插入一轮「被 edge 门禁否决的反向开仓意图」，
  再平仓——结算出的 `estEdgeBps` 仍是**开仓那一轮**的值。删掉 `end_round()` 清零逻辑，本用例必红。
- **G-3b（[P0-2] 限价回归，阻断级）**：`ai_trade_limit_orders_enabled=True` 下发一张限价开仓单，
  **跨过本轮的 `finally`**（显式调用 `end_round()`）后再由盯市 tick 送达成交，结算出的
  `estEdgeBps` / `decisionConfidence` **仍是下单那一轮**的值；同一用例断言市价路径逐字不变。
  删掉 `claim_estimate` 的按 `clOrdId` 认领，本用例必红。
- **G-4** 结算路径绝不抛：AST 断言 `trip_ledger.py` 无 `raise`；且喂一组畸形 trade dict
  （缺 `price` / `qty` 为字符串 / `pnl` 为 NaN）时 `settle` 返回 None 且不抛。
- **G-5** 口径同构：同一笔 `close_trade` 分别喂 `attribution.record_close`、`ExitLedger.record`、
  `trip_ledger.settle`，三者算出的**净额**逐位相等（守「绝不写第三种口径」）。
- **G-5b（[P0-3] 成本口径，阻断级）**：构造 `open(fee=a) → close(fee=b)` 的往返，断言
  `SettledTrip.fee_usd == a + b`（**双腿**）且与查询层 `_Trip.leg_fee_usd` 逐位相等；
  同时断言 `RoundTrip.fee_usd == b`（既有平仓腿口径**未被改动**）。
  再构造缺 `fee` 键的开仓腿：`fee_known=False`、`realizedCostBps` / `costErrorBps` **恒 None**
  且 wire 里**整键省略**（[P0-4]）。

**轴 B/C**

- **G-6（阻断级）双向对拍**：同一笔往返分别走「实盘结算路径」与「历史追溯复算路径」，
  **`notionalUsd` / `roundTripFeeUsd` / `realizedCostBps` / `rMultiple` 四个键**的相对误差
  均 < 1e-6（`fee_known=False` 的往返在成本两键上豁免）。v1 只钉了 `notionalUsd`，
  钉不住 [P0-3] 那条「平仓腿 vs 双腿」的分叉——而那条分叉恰好会让轴 C 问二得出
  「成本模型高估一倍」的假结论。这条同时守 §3.2.2 那个 `ct_val` 代数抵消不产生第二个口径。
- **G-6b（[P0-4] 覆盖率不可被 0 冒充）**：喂 100 笔往返、其中 90 笔无费率记录，断言
  `feeTrips == 10`、`feeCoverage == 0.1`、`medianCostBps` 取自那 10 笔（**不是 0.0**）、
  且 `travelToCostRatio` 在 `feeCoverage < 0.05` 时为 `None`。
- **G-6c（[P0-5] 口径解析）**：两个 run，一个 `config_json` 带 `costBasis="net"`、一个不带，
  断言不带的那个走 `ai_trade_history_legacy_cost_basis` 推定且 `costBasisAssumedTrips`
  如实计数；再断言端点的 `pooled.netPnlUsd` 与直接调 `_aggregate_group` 的既有口径**同号同量级**
  （防止本轮在聚合层引入第二套还原规则）。
- **G-7（D-2 回归）** 等权反例：构造 2 组（1 笔全胜 + 10 笔 3 胜），池化胜率 = 4/11 = 0.3636，
  等权均值 = (1.0+0.3)/2 = 0.65；断言端点返回 0.3636。
- **G-8** `coverage` 必显：任一 `calibration` 子块 `coverage < 1` 时，前端渲染必含覆盖率文本
  （组件用例 + `getByText` 断言）。
- **G-9** `travelToCostRatio` 有呈现落点：卡片渲染快照里必须出现该数值**与它同行的 `feeCoverage`**
  （**守「后端算了但没有渲染落点 ⇒ 死数据」**，本仓库最高频缺陷形状）。
- **G-9b（[P1-2]）** `aiStats.realized` / `costSnapshot.realized` 各自有渲染落点：
  历史详情页与实时统计页的组件用例分别断言三项读数可见；两处**共用同一个格式化器**
  （AST 或 import 断言：两个组件都从 `lib/aiTradeProfitability.ts` 取，不得各写一份）。
- **G-10** 文案禁令：源码正则扫描 `profitability.py` + `aiTradeProfitability.ts` +
  两个卡片组件，**不得**出现「已校准 / 经回测验证 / 预期年化 / 稳定盈利 / 保证」
  （`core.autocrlf=true`，正则写 `\r?\n`）。
- **G-11** 结论表五态各有一条用例，且 `insufficient_samples` 时 `pooled` 的数字**照常返回**
  （「样本不足」不等于「藏起来」，沿用 `PresetComparisonCard` 的既有纪律）。

**轴 D**

- **G-12（硬预算）** 行数：`session.py` 净增 ≤6、`prompts.py` ≤4、`manager.py` ≤10、
  `api/ai_auto.py` ≤40 且**总行数 < 800**；四个新文件各自 < 各自预算。用例读 `git diff --numstat`。
- **G-13（冻结）** §四列出的九个文件 `git diff --name-only HEAD` 零命中。
- **G-14** 四条装配门禁逐条：任一不满足 ⇒ `payload` 无 `trackRecord` 键 ⇒
  `build_decision_prompt` 输出与合入前**逐字节相同**。
- **G-14b（[P1-9] 永不开单回归）** 装配 / 不装配两臂各跑 20 轮同一组 payload，
  比较 `openFunnel.intents / rounds`：装配臂的开仓意图率**归零**即判回归。
  同时断言 `aiStats.trackRecordShown` 如实落库（事后可按臂对账）。
- **G-14c（[P0-4] 成本句门禁）** `feeCoverage = 0.11`（生产库实况）时 prompt **不含**成本句；
  `feeCoverage = 0.8` 时含且数值取自 `feeTrips` 子样本。**断言 prompt 里永不出现 "0.0 bps"。**
- **G-15** 决策轮零 IO：patch `get_sessionmaker` 为「一被调用就抛」，跑完整 3 轮决策不抛。
- **G-16** prompt 文案无 `**`（第 4 次复发防线）、不含收益承诺词，
  且不含否定式祈使词表（「不要 / 停止 / 必须减少」，[P1-9] 约束 ③）。

**轴 E**

- **G-17（阻断级）** `bracket_verdict` 在 ATR 真的胜出时**恒不报警**——
  三档预设（均已装配 `stop_atr_mult=2.0/2.0/2.5`）跑一遍，`level` 恒 `"atr_scaled"`。
  这条守的是「在一个已经正确定标的会话上误报」。
- **G-17b（[P1-8] 反向）** `stop_source` 为 `atr_floored`（成本下界抬起）⇒ `level="cost_floored"`
  且文案说的是**成本下界**而非 `k×ATR`；`floor_above_cap`（整条 ATR 覆盖回退）⇒ 走 `fixed` 分支、
  该报警就报警。**用例必须构造「配置上 `stop_atr_mult=2.0` 但实际回退成固定 5%」的断面**——
  它就是 v1 判据会漏掉的那一类，删掉七态改回按配置反推，本用例必红。
- **G-18（D-7 回归）** `open,close,close,open,close` 序列：第二笔往返的 `leg_fee`
  **不含**孤儿平仓的费用。
- **G-19** E-2 口径变更的双向断言：`_summarize_preset` 池化；同时断言 `_summarize_pairs` 的
  **既有键**（[P1-7]：允许新增 `wins` 加法键）与 `_aggregate_group` 的产出**逐位未变**
  （历史列表卡片的数字不得被追溯改写）。
- **G-20** 红灯基线：`python -u -m pytest tests/test_ai_*.py -q` 相对 §十.2 的基线
  **零新增失败**；前端 `NODE_ENV=test pnpm vitest run` 同理。

**评审新增（三条）**

- **G-21（[P1-5]）** 路由顺序：`GET /ai-trade/history/profitability` 返回 200 且**不是**
  `AiTradeHistoryDetailOut` 的形状（钉住它没有被 `/{group_id}` 吞掉）。
- **G-22（[P1-6]）** `verdict` 的 `tStat is None` 两条分支各一条用例（`n=1` 与零方差），
  断言返回结论码而**不抛 `TypeError`**；端点级再跑一次同样的输入，断言不是 500。
- **G-23（[P0-6]）** `bracket` 穿透 `response_model`：起一个 `TestClient` 请求
  `/ai-trade/tradability`，断言回执 JSON 里**确有** `bracket` 键
  （**手造 wire 的纯前端用例守不住这条**——第 4 轮已有成文教训）。

---

## 八、风险与缓解

| # | 风险 | 缓解 |
|---|---|---|
| R-1 | 结算逻辑跑在**真实资金成交的落库路径**上，一次异常就会中断 `_persist_trade` | `settle` 无 `raise`（G-4 AST 断言）+ `apply_trip_meta` 在 `_inject_explain_meta` 里包 `try/except → warning`，最坏只丢富化键，绝不阻断落库（沿用 `_finalize_run` 富化的既有 best-effort 范式） |
| R-2 | `explain_json` 因 13 个新键膨胀 | 「算不出即省略」；实测单笔 meta 增量 < 300 字节，193 笔 < 60 KB；且它随成交行落库，与 `metrics_json` 的膨胀护栏（`_DECISIONS_MAX`）无关 |
| R-3 | 追溯复算的 `ct_val` 代数抵消在「位移为 0」的往返上退化 | 该分支恒返回 None（G-2），实测 193 笔里仅 2 笔；卡片上以 `rSamples` 如实披露分母 |
| R-4 | 轴 B 端点在大历史上变慢（当前 161 run / 386 成交，未来可能上万） | 三查询/次（runs + trades + 无 N+1），纯内存聚合；端点无分页但有 `sinceTs` 过滤；实测 <50 ms。若未来超阈，缓存留给独立特性（本轮不做） |
| R-5 | 轴 D 的历史段在会话内不刷新 ⇒ 长会话读的是启动时快照 | 会话段（`session`）每轮现算，两段口径在 prompt 里**分别标注**；docstring 写明取舍理由 |
| R-6 | E-2 是一次**口径变更**，老截图上的数字会变 | 卡片显式标「池化口径」；spec §十一 记账；`_summarize_pairs`/`_aggregate_group` 逐位不变（G-19） |
| R-7 | 轴 C 的三问在上线当天 `coverage` 极低（估计侧全空） | 这是**正确行为**，不是缺陷：卡片显式渲染「0/193 笔带估计读数，需要新会话积累」；已实现侧（成本、R、位移）覆盖 191/193，上线当天就有内容 |
| R-8 | `bracket_verdict` 的建议值被用户读成「照这个填就能赚钱」 | 文案只说「让风险框架与交易尺度对齐」，不提收益；G-10 词表守门；不自动填入输入框 |
| R-9 | 新增 4 个纯核心模块与既有代码的接缝就是风险面（第 2 轮 4 条 P0 **全部**长在接缝上） | §十.6 列出本轮的六条接缝，要求实现节点**逐条打开既有函数读一遍**，不凭记忆下判断 |
| R-10 | 本轮读数可能被下一轮直接接进 `kelly_fraction` 的 `p_win` | §九 明确列为非目标，并在 `profitability.py` docstring 里写死「本模块的 `winRate` 未做样本外验证，**不得**作为仓位求解输入」 |
| R-11（评审新增，[P0-4]） | 用户/模型把「没记费率」读成「交易免费」，进而**提高**换手——本轮唯一一条方向相反的风险 | 三道防线：`feeCoverage` 分母贯穿 wire / 卡片 / prompt；成本句在 `feeCoverage < 0.5` 时整行省略（G-14c 断言 prompt 永不出现 "0.0 bps"）；`medianCostBps` 的分母是 `feeTrips` 而非 `roundTrips`（G-6b） |
| R-12（评审新增，[P0-5]） | 端点上线当天的数字与本文档 / 历史列表卡片对不上，实现节点会去追一个并不存在的 bug | 全文数字改按 `_run_cost_basis` 复算并在 §零第 1 条写明更正原委；G-6c 钉住「不引入第二套还原规则」；`costBasisAssumedTrips` 随回执下发，用户侧也看得到 |
| R-13（评审新增，[P1-9]） | 负期望战绩每轮回喂 ⇒ 模型再也不开单（本仓库已复发 4 次的形状） | `aiStats.trackRecordShown` 留痕 + G-14b 两臂开仓意图率对拍 + 文案禁否定式祈使句；总闸 `ai_trade_track_record_enabled` 是即时回滚开关 |

---

## 九、非目标（本轮明确不做）

1. **不改任何门禁的判定式**——六个纯核心（`edge_gate` / `base_rules` / `hold_horizon` /
   `profit_guard` / `turnover_discipline` / `drawdown_brake`）逐字节冻结。本轮只**测量**。
2. **不做自动调参**：`bracket_verdict` 给建议，**绝不**自动改用户填的 `slPct` / `tpPct` / 周期 / 预设
   （第 2 轮 §九 非目标 3 的成文禁令）。
3. **不新增任何 close-blocking 门禁**。D-5 指出退出侧是亏损来源，但「拦住平仓」的极性是
   **更激进**（继续持有敞口），与本仓库「门禁只会更保守」的不变式相反；
   `hold_horizon` 已经owns这条职责且带完整释放阀。本轮只把 R 读数交给模型与用户。
4. **不改 `min_confidence` 的默认值**：D-4 的出路是轴 C 的可靠性表，而表在上线当天 `coverage=0`。
   拿一个还没有数据的结论去改门槛，就是把猜换成另一个猜。
5. **不还「回测与实盘仓位求解不同构」这笔债**（`p_win` 在回测侧恒 `None`）：它是一次独立特性
   （第 2 轮 §十一.1 已如此登记）。本轮产出的实测胜率**正是**那笔债需要的经验 `p_win` 来源，
   但接进 `kelly_fraction` 必须先做样本外验证——见 R-10。
6. **不拆分 `session.py` / `prompts.py` / `manager.py` / `schemas.py`**（存量债 R-6，§十一.2）。
7. **不新增 LLM 调用**：五轴全部确定性。
8. **不动 `live_auto` 链路**：本轮新增 settings 经会话私有 `Settings` 副本生效
   （第 1 轮 `discipline.py` 已建立该机制），非 AI 链路逐字节不受影响。

---

## 十、给下游节点的实施注意

1. **`app/config.py` 是混合文件**：工作树里另有一处**不属于本轮**的在制改动
   （`ai_trade_enabled` 默认值翻转，及其配套 `.env.example`、`desktop/src/lib/config.js`、
   `tests/test_config.py`）。本轮只在文件末尾追加字段，与那处 hunk 不重叠，**须按 hunk 精确暂存**。
   暂存混合文件的推荐手法（本仓库已验证）：用 `Edit` 临时回退外来 hunk → `git add` → 再改回；
   **不要**用 `write_text` 造 patch（Windows 上会翻 CRLF 弄坏 patch）。
2. **红灯基线（本轮实测）**：
   `cd services/quant && python -u -m pytest $(cat ai_subset.txt) -q`
   （`ai_subset.txt` = `tests/test_ai_*.py` 的 82 个文件）⇒ **1555 passed / 0 failed / 397 s**。
   验证时若发现新红灯，**必须**用 `git worktree add <HEAD-sha>` 在干净树上对账后再判定
   （并记得 `cp services/quant/.env`——它是 gitignore 的 Agent provider 源，缺它
   `test_ai_backtest_manager` 会挂红且父提交同样复现）。
3. **全量 pytest 不可并发**（共用临时 DB），且上一次跑完进程可能不退出导致锁不释放；
   分块跑用 `python -u -m pytest ... > log`（`pytest -q` 是块缓冲，直接看不到进度）。
   `--timeout=` 不可用（未装 pytest-timeout，本轮实测报 `unrecognized arguments`）。
4. **前端源码扫描类冻结用例**：本仓库 `core.autocrlf=true`，正则须写 `\r?\n`，否则干净检出上恒红。
5. **新键的呈现落点必须在同一个 Subtask 内做完**：本仓库最常见的缺陷形状是
   「后端算了、类型补了、但没有渲染落点 ⇒ 死数据」（已复发 ≥4 次）。本轮
   `travelToCostRatio`（G-9）、`coverage`（G-8）、`rMultiple`（成交表新列）三个键各自的渲染点
   已在 §四 列出，实现时请**自己走一遍数据从产生到上屏的每一跳**。
6. **本轮的接缝清单**（第 2 轮的 4 条 P0、本轮评审的 6 条 P0 **全部**长在接缝上，
   请逐条打开既有函数读一遍——**不要凭本文档的转述下判断**）：
   1. `note_gate` **只在 `out.gated` 时**转发（`cost_runtime.py:461-462`）——轮级估计的写点
      必须与它同址，否则观望轮会把估计冲掉；
   2. `_dispatch` 的开仓支**对部分成交会多次触发**（`session.py:325-330` 的注释：
      「开仓部分成交发的是 long/short 的 trade，`seq` 不动」）——`note_open` 必须**幂等**，
      同一 `seq` 的第二次调用只更新名义额、不重置估计；
   3. `take_peak_pnl` 是**读取即消费**（`profit_guard_runtime.py:246-253`）——
      轴 A 绝不去碰它，`peakPnlUsd` 的既有语义一个字不改；
   4. `_pair_round_trips` 的排序键是 `trades.id` 而非 `ts`（`ai_auto_repositories.py:80-85`
      记着这条踩过的坑）——查询层复用它，**绝不**另写配对；
   5. `_run_cost_basis` 的「推定」语义（`:167-180`）——追溯复算的毛/净还原必须走它，
      否则已按 net 口径落库的历史行会被二次扣费；
   6. `merge_cost_stats` **键冲突即抛**（`stats_ext.py:53-64`）——`realized` 是新键，
      但若未来有人在别处也叫 `realized`，落库收尾会当场炸；用例 G-1x 钉住。
   7. **[P0-1] `_apply_edge_gate` 不返回 `out`**（`session.py:1096-1108` 只回
      `(out.decision, degrade or out.reason)`）——轮级估计的写点只能在该函数**内部**。
   8. **[P0-2] 限价开仓成交由盯市 tick 送达**（`_monitor_tick → executor.reconcile → _dispatch`，
      `session.py:487-491`），落在决策轮的 `finally` 之后——任何「轮级槽」范式对默认订单类型都失效。
   9. **[P1-1] `exitSource` 在 `_inject_explain_meta` 的最后一步才写**（`session.py:361-363`）且受
      `ai_trade_exit_attribution_enabled` 门控——`apply_trip_meta` 必须排在它**之后**并只读不判。
   10. **[P0-6] `AiTradeTradabilityOut` 是显式字段清单**（`schemas.py:1237-1267`）——
       新 wire 键不声明就被吞；**手造 wire 的前端用例守不住这条**，必须走 `TestClient`。
   11. **[P1-3] `lib/types.ts` 的 `Trade` 与 `AutoTradeRoundTripRow` 是 live-auto / paper 共用的**——
       新键与新列必须走「可选形参、不传即逐字节不变」的既有范式（该文件的 `bar?` 就是先例）。
   12. **[P1-4] `AiTradeStatsHistoryList` 有一个空态早返回**（`:44-50`）挡在两张新卡前面。
7. **`api/ai_auto.py` 只剩 81 行余量**（719/800）。新端点 + `_resolve_track_record` 若超预算，
   **立刻**下沉到 `ai_auto/track_record_service.py`（第 2 轮 `tradability_probe.py` 的先例），
   不要挤。`prompts.py`（835）与 `manager.py`（957）**已经越线**，净增预算是硬判据。
8. **`profitability.py` 的中位数**：Python `statistics.median` 对空列表抛 `StatisticsError`——
   全部包在「空即 None」的守卫里（本模块跑在端点上，抛就是 500）。
9. **本轮的实测数字是可复算的**：复算脚本见 §附录 A。若实现节点复算出的数字与 §二 的表有出入，
   **以复算为准并更新本文档**——数字是判据的依据，不是装饰。
10. **`_summarize_pairs` 与 `_aggregate_group` 是已发布 KPI 的口径来源**，本轮除 E-1 外一律不动。
    E-1 改的是 `_pair_round_trips` 的孤儿分支，它在当前生产库上零命中 ⇒ 已发布数字逐位不变
    （G-19 双向断言）。

---

## 附录 A：实测原始数据（可复算）

样本来源：`%APPDATA%/aragon-desktop/aragon.db`（用户本机真实 OKX 实盘历史），
`strategy_runs.mode == 'ai-trade'` 共 161 个 run / 28 个 group / 386 条成交 / **193 笔已配对往返**
（配对算法逐字采用 `_pair_round_trips` 的 `trades.id` 升序单持仓配对）。

> **[P0-5] 口径（v2 更正）**：`pnl` 的毛/净口径**逐 run** 按 `_run_cost_basis` 解析——
> `config_json` 带 `costBasis` 即已知（实测 **5/161**，均为 2026-08-12 的 run），
> 其余 **156 个**走 `ai_trade_history_legacy_cost_basis` 推定 `"gross"`（⇒ `net = pnl − legFee`）。
> 该推定对本库是**正确的**：`ai_trade_net_pnl_enabled` 在 `96f554d`（2026-07-25）引入时默认 `False`，
> `cebee54`（2026-08-06）才翻 `True`，而带费率的 16 笔往返产生于 2026-07-26。
> v1 用的是「一律视为净额」，两者只在 21 笔带费往返上分叉（其余 172 笔腿费恒 0，两口径逐位相同）。

### A.1 池化盈利读数（按 `_run_cost_basis` 逐 run 解析）

| 量 | 值 | （v1 的「一律净额」口径） |
|---|---:|---:|
| 往返数 | 193 | 193 |
| 胜率 | **40.41%** | 40.93% |
| 平均盈利 / 平均亏损 | +0.2092 / −0.2064 USDT | +0.2247 / −0.1918 |
| 赔率（payoff） | **1.014** | 1.172 |
| 该胜率下的盈亏平衡赔率 | **1.474** | 1.443 |
| 净额期望 / t | **−0.0384 USDT/笔 / −1.446** | −0.0213 / −0.791 |
| Σ净额 / Σ腿费 | **−7.4185 / 3.8748 USDT** | −4.1127 / 3.8748 |
| 中位持仓 | 1271 s（21.2 min），p10 2.4 min / p90 73.9 min | 同 |

**赔率 1.014 只有盈亏平衡所需 1.474 的 69%**——比 v1 的读数更远离盈亏平衡线，§零第 1 条的结论
方向不变且更强。

### A.2 风险单位（D-1）

| 量 | p10 | 中位 | p90 |
|---|---:|---:|---:|
| 实际位移（bps） | 2.3 | **13.9** | 37.2 |
| R 倍数（n=191） | −0.053 | **−0.011** | 0.063 |

`stop_loss_pct` 在 193/193 笔上恒为 `0.05`；`take_profit_pct` 恒为 `0.10`；计划盈亏比中位 2.000。
`|R| < 0.2` 占比 **100.0%**；`R ≥ 1` 占比 **0.0%**；中位「位移/止损距离」= **2.83%**。

### A.2b 已实现成本与费率覆盖（D-9 / [P0-4]）

| 量 | 值 |
|---|---:|
| 带费率记录的往返（`feeTrips`） | **21 / 193（10.88%）** |
| 全样本 `medianRealizedCostBps` | **0.000**（= 没记，不是免费） |
| 带费子样本 `medianRealizedCostBps` | **7.01 bps**（p10 6.99 / p90 10.02） |
| （分母口径）`feeCoverage` = 21/193 = 0.1088；A.5 脚本印的 0.1099 是 21/191，分母是「名义额算得出来」的 191 笔 —— **两个分母都对，但必须各自标明**（§一 的不变式）。wire 上一律用 21/193 | — |
| `cost_model` 的市价往返常数 | 13.0 bps |
| `travelToCostRatio`（13.9 / 7.01） | **1.98**（仅在 21 笔子样本上成立） |

按周期切分的实际位移中位：15m 11.8 bps（n=98）、5m 16.1 bps（n=56）、1m 14.6 bps（n=24）、
30m 14.1 bps（n=15）。

### A.3 退出来源与纪律层参与度（D-5 / D-6）

| explain.kind | 笔数 | 占比 |
|---|---:|---:|
| `atomic` | 184 | 95.3% |
| `session_stop` | 6 | 3.1% |
| `external_close` | 2 | 1.0% |
| `profit_guard` | 1 | 0.5% |

跨 114 个带 `aiStats` 的 run：`decisionRounds` 合计 16065；采样动作计数（≤50 条/run，合计 3949 条）
`hold` 3798（96.2%）/ `open_long` 50 / `open_short` 33 / `close` 56 / `cancel_order` 12；
`reflectionRuns` 105、`reflectionAborts` 26（**24.8% 的交易意愿被反思否决**）；`wakeSkips` 6106。
装配了利润保护读数的 18 个 run（2019 决策轮）：`profitGuardArmedRounds` 22（**1.09%**）、
`profitGuardExits` 1。装配了周期铁律的 run 只贡献 **5 笔**往返（中位持仓 119.3 min，
对照老会话 19.1 min）。

### A.4 池化 vs 等权（D-2）

18 个有成交的 group，往返数 = 1,1,1,2,3,4,5,6,6,8,9,9,10,12,13,16,17,39。
池化胜率 **40.74%**（162 笔，按 `group_id` 非空过滤）vs 各组胜率的算术平均 **36.11%**，差 4.63 pp。

### A.5 复算脚本（v2：逐 run 解析口径 + 费率覆盖）

```python
import os, sqlite3, json, collections, statistics, math
p = os.path.join(os.environ['APPDATA'], 'aragon-desktop', 'aragon.db')
con = sqlite3.connect(p); cur = con.cursor()
# [P0-5] 逐 run 解析 costBasis：缺键 ⇒ 走 ai_trade_history_legacy_cost_basis 的出厂默认 "gross"
basis = {}
for rid, cfg in cur.execute("select id, config_json from strategy_runs where mode='ai-trade'"):
    try:
        cfg = json.loads(cfg or '{}')
    except Exception:
        cfg = {}
    basis[rid] = cfg.get('costBasis') or 'gross'
rows = list(cur.execute(
    'select run_id,id,side,ts,fee,pnl,stop_loss_price,take_profit_price,price '
    'from trades where run_id in (%s) order by run_id,id' % ','.join(map(str, basis))))
byrun = collections.defaultdict(list)
for r in rows:
    byrun[r[0]].append(r)
nets, Rs, moves, cost_all, cost_fee = [], [], [], [], []
for rid, tr in byrun.items():
    b, op, lf = basis[rid], None, 0.0
    for r in tr:
        lf += r[4] or 0.0
        if r[2] in ('long', 'short'):
            op = r
        elif op is not None:
            pnl = r[5] or 0.0
            net = pnl if b == 'net' else pnl - lf      # 与 _Trip.net_pnl 逐字同构
            gross = pnl + lf if b == 'net' else pnl    # 与 _Trip.gross_pnl 逐字同构
            move = abs((r[8] or 0) - (op[8] or 0))
            stopd = abs((op[8] or 0) - (op[6] or 0))
            nets.append(net)
            if op[8]:
                moves.append(move / op[8] * 1e4)
            if move > 0 and gross != 0:
                unit = abs(gross) / move          # = qty × ctVal（ctVal 代数抵消）
                notional = (op[8] or 0) * unit
                if notional > 0:
                    cost_all.append(lf / notional * 1e4)
                    if lf > 0:                    # [P0-4] 腿费恒 0 = 没记，不进成本分母
                        cost_fee.append(lf / notional * 1e4)
                if stopd > 0:
                    Rs.append(net / (stopd * unit))
            op, lf = None, 0.0
n = len(nets)
w = [v for v in nets if v > 0]; l = [v for v in nets if v <= 0]
m = sum(nets) / n; var = sum((x - m) ** 2 for x in nets) / (n - 1)
print('trips %d winRate %.4f payoff %.3f breakeven %.3f exp %.4f t %.3f'
      % (n, len(w) / n, (sum(w) / len(w)) / abs(sum(l) / len(l)),
         (1 - len(w) / n) / (len(w) / n), m, m / (math.sqrt(var) / math.sqrt(n))))
print('move bps p50 %.1f | R p50 %.4f | |R|<0.2 %.3f | R>=1 %.3f'
      % (statistics.median(moves), statistics.median(Rs),
         sum(1 for x in Rs if abs(x) < 0.2) / len(Rs),
         sum(1 for x in Rs if x >= 1) / len(Rs)))
print('cost bps: all p50 %.3f (n=%d) | feeTrips p50 %.3f (n=%d) | feeCoverage %.4f'
      % (statistics.median(cost_all), len(cost_all),
         statistics.median(cost_fee), len(cost_fee), len(cost_fee) / len(cost_all)))
```

**v2 实测输出**（评审节点在同一台机器上跑过，实现节点应逐位复现）：

```
trips 193 winRate 0.4041 payoff 1.014 breakeven 1.474 exp -0.0384 t -1.446
move bps p50 13.9 | R p50 -0.0106 | |R|<0.2 1.000 | R>=1 0.000
cost bps: all p50 0.000 (n=191) | feeTrips p50 7.008 (n=21) | feeCoverage 0.1099
```

---

## 十一、本轮如实记下的欠账

> 记账纪律沿用前两轮：做不到的事写清楚「做不到什么、为什么、下一轮怎么还」，
> 而不是把判据改松到能通过为止。

1. **轴 C 的两问在上线当天 `coverage = 0`**：`edgeNumerator` 与 `confidenceReliability` 需要
   轴 A 落库的开仓时刻估计，历史 193 笔一笔都没有。**这不是缺陷、是事实**，卡片必须显式写
   「需要新会话积累」。可追溯复算的三个量（已实现成本 bps、R 倍数、位移 bps）覆盖 191/193，
   保证卡片上线即有内容。
2. **E-2 是一次有意的口径变更**：`preset-comparison` 的 `winRate` / `edgeVetoRate` /
   `openConversion` 由「各组比值的均值」改为池化，**老截图上的数字会变**。理由见 D-2，
   卡片上标注「池化口径」。`_summarize_pairs` / `_aggregate_group` 逐位不变（G-19）。
3. **`ai_trade_bracket_max_atr_mult = 8.0` 未经回测校准**：取值依据是 §六.1 的横切表
   （让实测「止损从未被触碰」的 5m/15m 全部报警、让 1H 及以上不报警）。UI 与文档不得声称已校准。
4. **[前轮遗留] 回测与实盘的仓位求解仍不同构**（`p_win` 在回测侧恒 `None` ⇒ 凯利项恒不参与 `min`）。
   本轮**有意不还**（§九.5），但本轮产出的实测胜率正是它需要的经验 `p_win` 来源。
5. **[前轮遗留] 800 行硬规则的存量债**：`schemas.py`(2427) / `session.py`(1845) /
   `manager.py`(957) / `prompts.py`(835)。本轮只保证「不因本轮而更糟」（G-12 净增预算守门），
   拆分留给独立的重构轮。
6. **轴 D 的历史段在会话内不刷新**：取舍理由见 R-5。若未来要刷新，必须先解决「盯市 tick 上开 DB
   会话」这条今天不存在的 IO 面。
7. **[评审新增，P0-4] 成本类结论在本库上只有 10.9% 的证据基座**：193 笔里只有 21 笔带费率记录，
   轴 C 问二在上线当天 `conclusive=false`。这**不是缺陷、是事实**——真正的成本校准要等
   2026-07-26 之后的新会话把 `feeTrips` 累到 ≥10 且 `feeCoverage ≥ 0.05`。
   在此之前，任何「实测成本 7.01 bps 低于模型 13.0 bps」的说法都必须带着那个 21 笔的分母出现，
   **更不得据此调低 `cost_model` 的常数**（那是下一轮、且要有 100 笔以上的样本）。
8. **[评审新增，P0-5] 172/193 笔往返的盈亏口径是推定的**：`config_json` 的 `costBasis` 键随
   `cebee54`（2026-08-06）才落地，之前的 run 一律走 `ai_trade_history_legacy_cost_basis`。
   本轮**有意不做**「回填历史 run 的 costBasis」这件事——那是一次追溯改写已发布 KPI 的迁移，
   与「`_summarize_pairs` / `_aggregate_group` 逐位不变」的承诺冲突。代价是 `costBasisAssumedTrips`
   会长期显示一个很大的数；收益是历史列表上的每一个数字都还是用户当初看到的那个。
9. **[评审新增，P1-9] 轴 D 的「回喂是否伤害开仓意愿」只有一条离线用例（G-14b），没有线上 A/B**：
   本仓库没有 AI-trade 的分臂实验设施，建一套是独立特性。故本轮只做到「留痕 + 可一键回滚」。

---

## 评审结论

**有条件通过（Approved with conditions）。**

这份设计的取向是对的，而且**难得地诚实**：它没有再叠一层纪律，而是先去回答「前三轮叠上去的那十几层
到底有没有用」——并且在用户的真实资金历史上给出了「答不上来」这个结论。评审逐条复算了它的实证：
`stop_loss_pct` 193/193 恒 0.05、位移中位 13.9 bps、`|R|<0.2` 占比 100%、退出 kind 分布、
28 组的往返数分布、以及全部 24 个文件的行数标注——**逐位复现，无一漂移**。五轴的边界（全部确定性、
零新增 LLM、零 DB 迁移、六个门禁纯核心逐字节冻结、只测量不调参）也是这个阶段该有的克制。

**放行的前提是下面 13 条**（前 6 条是 P0 的落地，做不到就不叫做完）：

1. **[P0-1]** 轮级估计的写点在 `_apply_edge_gate` **内部**（`out` 在那里才有），不在 `:760` 的调用点。
2. **[P0-2]** 有按 `clOrdId` 的**待成交估计槽**，且 G-3b（限价单跨轮成交仍绑对）绿；
   删掉认领逻辑该用例必红。默认订单类型就是限价，这条不做等于轴 C 的估计侧永远是空表。
3. **[P0-3]** `SettledTrip.fee_usd` 是**往返双腿**，wire 键名 `roundTripFeeUsd`；
   G-5b（双腿 vs 平仓腿双向断言）与扩容后的 G-6（四键对拍）全绿。
4. **[P0-4]** `feeCoverage` / `feeTrips` 贯穿 wire、卡片与 prompt；G-6b、G-14c 绿；
   **prompt 里永不出现 "0.0 bps" 的往返成本**。
5. **[P0-5]** 全文数字与端点产出同走 `_run_cost_basis`；G-6c 绿；
   `costBasisAssumedTrips` 随回执下发并上屏。
6. **[P0-6]** `AiTradeTradabilityOut` 显式声明 `bracket`，G-23 用 `TestClient` 断言穿透。
7. **[P1-1 / P1-2 / P1-3 / P1-4]** 四个「有数没落点 / 落点写错」全部闭合：
   `exitSource` 只读不判且顺序在后、`aiStats.realized` 有两个渲染落点、`rMultiple` 落在
   `lib/types.ts:Trade` + `AutoTradeRoundTripRow` 的可选形参、两张新卡挂在空态早返回**之前**。
8. **[P1-5 / P1-6]** 路由注册顺序（G-21）与 `tStat is None` 分支（G-22）各有用例。
9. **[P1-7]** `_summarize_pairs` 只加 `wins` 这一个加法键，G-19 断言的是「既有键逐位不变」。
10. **[P1-8]** `bracket_verdict` 的 `stop_source` 取探针的止损来源七态，G-17b（配置说 ATR、
    实际回退固定）绿——这是本轮最容易在「看起来对」的实现里溜过去的一条。
11. **[P1-9]** `trackRecordShown` 留痕 + G-14b 两臂开仓意图率对拍；一旦归零立刻回滚总闸。
12. **[P1-10 / P1-11]** 口径写进可见 label（不是 `title`）；三个新纯函数的形参聚合到 ≤5。
13. **红灯基线用干净 worktree 实测对账**（§十.2），并记得 `cp services/quant/.env`；
    G-12 / G-13 的行数与冻结判据在**提交前**跑一遍——本仓库已有「按增量预算对账却撞穿 800 行硬线」
    的先例。

**两句给实现节点的话。** 其一，本轮 6 条 P0 里有 4 条是同一个形状——*后端算得出来 ≠ 用户/模型看得见
那个数*（估计写点拿不到 `out`、限价成交拿不到估计、`realized` 没有读者、`bracket` 被 `response_model`
吞掉）。请**自己走一遍数据从产生到上屏的每一跳**，不要凭本文档的转述下判断。其二，本轮唯一一条
方向相反的风险在轴 D：一个由「没记费率」算出来的 0.0 bps 成本，会把「别做撑不过成本的交易」这句
纪律翻译成「交易几乎不要钱」。宁可整行不说，也不要说一个错的数——
**这套系统现在最缺的不是更多读数，而是每一个读数都带着它的分母。**

---

## 实施过程发现的方案缺陷

> 记账纪律沿用前两轮：**发现设计有错就写下来、改对、并说清楚改成了什么**，而不是静默偏离。
> 下面 6 条里，I-1 / I-2 / I-3 是**阻断级**（照 spec 字面实现会得到一个不工作或恒空转的通路），
> I-4 是口径澄清，I-5 / I-6 是机械判据的如实对账。

### I-1（阻断级）`_execute_decision` 的回执 dict 里没有 `clOrdId`，[P0-2] 那一行写不出来

**spec 原文**（§3.1.5 表第 2 行）：`self._cost.claim_estimate(executed.get("clOrdId"))`。

**实际**：`_execute_decision` 返回的是一个**自造**的 dict（`session.py:1163-1181`），键集恒为
`{ok, action, skipped, traded, side, qty, positionAfter}`——**没有** `clOrdId`。限价单的委托号只
活在 `executor.resting`（`RestingOrder.cl_ord_id`，由 `_enter_resting` 写，`executor.py:342-353`）。
照字面实现的结果是 `claim_estimate(None)` 恒空转 ⇒ 待成交槽永远是空的 ⇒ **[P0-2] 修了个寂寞**，
而且不会报任何错——与 [P0-1] 完全同形（*写点拿不到那个值*）。

**已采用的修正**：`CostRuntime.claim_estimate(resting)` 改收 `executor.resting` 本身，按鸭子类型读
`intent` / `cl_ord_id`，只在 `intent == "open"` 时认领；session 侧仍是一行
`self._cost.claim_estimate(self.executor.resting)`。G-3b 覆盖。

### I-2（阻断级）探针**没有**算过「止损来源七态」，[P1-8] 引用的位置是错的

**spec 原文**（§3.5.1 / 放行条件 10）：「`stop_source` 与 `stop_bps_p50` 一律取探针已经算出的止损
来源七态（与轴 B 的 `stopBps` / `stopInsideCostRate` 同源，`tradability.py:234-249`），本轮零新增计算」。

**实际**：`tradability.py:234-249` 是 `_stop_series` / `_inside_rate`，它们只算
`stop_atr_mult × ATR(bps)` 与「落进成本带的比例」——**没有任何一处产出止损来源**。七态住在
`sizing_runtime._apply_stop`（`sizing_runtime.py:264-288`），那是**决策轮内**的运行时状态，
而探针跑在启动/弹窗期、根本没有决策轮。照字面实现只能退回「按配置反推」，也就是 [P1-8] 明令
禁止的那一件事。

**已采用的修正**：装配层 `tradability_probe._bracket` **现解**来源——调用实盘用的**同一个纯函数**
`sizing.resolve_stop_distance`（成本下界 → 用户红线 → 冲突判定三段），`plan.source` 与 `skip_reason`
合起来即真实来源；`bracket_verdict` 仍是纯核心、`stop_source` 仍是入参（判据在纯核心的纪律不变）。
量纲上传 `ref_px=1e4, atr_px=ATR(bps)`——该函数只用到 `atr_px / ref_px` 这个比值，故尺度无关、
零新增取数。G-17b 的「配置说 ATR、实际回退固定 5%」断面由 `floor_above_cap` 分支覆盖。

### I-3（阻断级）`costSnapshot` 在装了纪律预设的会话上**从不下发**，[P1-2] 的实时落点是死通道

**spec 原文**（§3.1.3 表 / §5.4）：「实时 `AiTradeStatsLive` 读 `costSnapshot.realized`（既有通道）」，
且「本轮不新增 WS `status` 键」。

**实际**：`costSnapshot` 只在 `_emit_cost_snapshot`（`session.py:1120-1126`）里被挂上 status 事件，
而那个函数**只在 `_apply_edge_gate` 拿到 `out is None` 时**才被调用——即 `k_min <= 0`（门禁空转）。
三档纪律预设装配的 `k_min` 全部 > 0，故**默认部署的每一个会话都一次都收不到 `costSnapshot`**。
`_status_payload()` 里也没有这个键。照字面实现，`aiStats.realized` 的实时落点是一个死通道
——正是本轮 [P1-2] 要修的那个形状本身。

**已采用的修正**：`realized` 挂 `_status_payload()` **顶层**（session 净增 +4 行含注释）。
`_push_status_if_side_changed` 在每次持仓方向变化时推 status，而**每一笔往返结算恰好伴随一次
side → flat**，故读数一变就上屏。落库侧仍走既有 `merge_into`（`costSnapshot` 通道保持不变，
门禁空转的会话两条通道都拿得到）。前端类型同时补在 `AiTradeSessionRow` 与 `AiTradeStatusEvent`。

### I-4（口径澄清）实盘的 `gross_pnl_usd` 是**平仓腿**口径，追溯复算是**双腿**口径

约束 1 要求 `gross = pnl + fee`（与 `attribution.record_close` 逐字同构，`fee` 是平仓腿），而查询层
`_Trip.gross_pnl` 加的是**区间双腿**腿费。两者在带费往返上相差整整一条开仓腿。

这**不是缺陷**，但必须写明：轴 B/C 的 `realizedGrossBps` / `edgeErrorBps` 一律由**查询层**现算
（[P0-3] 的权威规则），故分析口径自洽；`explain.meta.realizedGrossBps` 是**展示值**，走约束 1 的
平仓腿口径。G-6 因此只对 `notionalUsd` / `roundTripFeeUsd` / `realizedCostBps` / `rMultiple`
四键对拍（`grossPnlUsd` 有意不在对拍集内）。另：G-6 的用例数据必须**算术自洽**
（`unitUsd = |毛额| / |Δ价|` 是追溯侧代数抵消 `ctVal` 的唯一依据），喂一笔算术上不成立的成交，
对拍失败的是数据而不是代码。

### I-5（判据对账）追溯路径分辨不出「记了一个 0」与「根本没记」

`trades.fee` 是**非空**列（`db/models.py:84`），费率记账 2026-07-26 才上线，之前的行一律落 `0.0`。
故查询层的 `feeKnown` 判据只能是「双腿非 NULL **且** 区间腿费 > 0」（与 §附录 A.5 的 `if lf > 0`
逐字同构）；而实盘结算路径能靠「`fee` 这个键在不在成交 dict 里」分辨，故那边保留了「真实零费率
成交也算已知」的语义。**两条路径的判据不同是有意的**——它们手上的信息本来就不一样，写在
`_fee_known` 与 `trip_ledger._leg_fee` 两处 docstring 里。

### I-6（如实记账）§四的行数预算按「纯逻辑行」估，多处被 CLAUDE.md 的注释规范顶穿

实测净增 / 文件行数（`git diff --numstat`，不含用例）：

| 文件 | spec 预算 | 实测 | 说明 |
|---|---:|---:|---|
| `trip_ledger.py` | ≤240 | **337** | 十条硬约束写进模块 docstring（约 40 行）+ 三个 dataclass 逐字段注释 |
| `profitability.py` | ≤300 | 276 | ✅ |
| `calibration.py` | ≤160 | 120 | ✅ |
| `track_record.py` | ≤130 | **156** | 四条装配门禁 + 四条文案硬约束的成文理由 |
| `track_record_service.py` | （超预算即建） | 87 | §四 预授权的下沉；`api/ai_auto.py` 因此只 +25 |
| `attribution.py` | +≤75 | **+111** | 六个新方法各自的 why 注释 |
| `cost_runtime.py` | +≤35 | **+63** | 同上 |
| `tradability.py` | +≤55 | **+105** | E-0 的四态判定 + 三段文案（后端下发、前端不重写） |
| `tradability_probe.py` | +≤25 | **+42** | I-2 的现解逻辑与其成文理由 |
| `session.py` | 净增 ≤6 | **净增 +22** | 预算漏算了：`import`(1) / 构造形参(2) / `TrackRecordRuntime` 构造(4) / I-3 的 status 键(4) / `trackRecordShown`(3)。**纯调用行是 5 行，与表一致** |
| `prompts.py` | 净增 ≤4 | **净增 +11** | 一个新 section 至少需要「格式化函数 + 调用点」两处 |
| `manager.py` | 净增 ≤10 | 净增 +5 | ✅ |
| `api/ai_auto.py` | 净增 ≤40，总行 <800 | +25，**744** | ✅ |
| `ai_auto_repositories.py` | +≤65 | **+191** | `load_profitability_trips` + 7 个辅助 + E-1/E-2 |
| `schemas.py` | +≤40 | +32 | ✅ |
| `config.py` | +≤8 | 净增 +13 | 9 个字段 + [P0-4] 的成文理由 |

**CLAUDE.md 的 800 行硬线（唯一的硬规则）全部满足**：本轮新建与改动的文件里，只有
`session.py`(1867) / `manager.py`(962) / `prompts.py`(850) / `schemas.py`(2459) 越线，四者**合入前
即已越线**（存量债 §十一.5）。`api/ai_auto.py` 744 / `tradability.py` 506 / `cost_runtime.py` 619 /
`ai_auto_repositories.py` 678 全部在线内。前端 `aiTradeProfitability.ts` 192（预算 180）、
`AiTradeStatsLive.tsx` +42（预算 15，多出来的是 `RealizedLiveCard` 整卡）、`AiTradeStatsDetail.tsx`
+40（预算 30，多出来的是 `RealizedKpiGroup` 整块）、`aiTradeTypes.ts` +25（预算 20）同理。

**处置**：按 §四 [P2-1] 的成文纪律——**宁可拆文件也不许删注释压行**——已把轴 C 拆成
`calibration.py`、把端点装配拆成 `track_record_service.py`。其余超出部分是注释与 docstring，
**不做压缩**：G-12 是一条机械判据，而 CLAUDE.md 的注释规范与 800 行硬线才是本仓库的硬规则。
如需继续收敛，正确的做法是下一轮把 `session.py` / `prompts.py` 拆开（§九.6 已明确列为本轮非目标）。

### 验证记录

- 后端 AI 子集 `python -u -m pytest tests/test_ai_*.py -q`（89 个文件）：**1620 passed / 0 failed**
  （基线 1555，本轮新增 65 条；跑完进程 segfault 退出属既有现象，与用例结果无关）。
- 相邻子集 `test_config / test_api / test_live_auto_* / test_live_executor* / test_exchange_backstop /
  test_fills_explain`：**166 passed / 0 failed**（守 §九.8「不动 `live_auto` 链路」）。
- 前端 `NODE_ENV=test pnpm vitest run`：**1736 passed / 0 failed**（155 个文件）。E-3 改了两个
  可见 label，`AiTradeStatsPanel.test.tsx:279` 的断言同步更新为带口径词的新文案（那正是 E-3 的验收）。
- `npx tsc --noEmit`：只剩既有的 `WeightBatchArena.test.tsx` 类型错（合入前即存在，非本轮回归）。
- `ruff check`：本轮全部改动文件通过。
- **G-13 冻结**：`git diff --name-only` 对九个冻结文件（`simulator.py` / `cost_model.py` /
  `cost_text.py` / `edge_gate.py` / `base_rules.py` / `hold_horizon.py` / `profit_guard.py` /
  `turnover_discipline.py` / `drawdown_brake.py`）**零命中**。

---

## 终审记录（Final Review · 2026-08-12）

> 方式：把五轴的 diff 与 §四 的变更计划逐条对拍，然后**自己走一遍每个新读数从产生到上屏的每一跳**
> （§十.5 那条纪律对终审同样有效）。六条 P0 与 12 条 P1 的落地逐条核实**全部属实**：写点确实在
> `_apply_edge_gate` 内部、待成交槽确实按 `clOrdId` 认领（`executor._settle_resting_fill:511-515`
> 确实把 `cl_ord_id` 写进了成交 dict，I-1 的修法成立）、`SettledTrip.fee_usd` 确实是双腿、
> `feeCoverage` 确实贯穿 wire/卡片/prompt、口径确实逐 run 走 `_run_cost_basis`、`bracket` 确实
> 显式声明在 `AiTradeTradabilityOut` 上。九个冻结文件 `git diff` 零命中。
>
> 下面 6 条是终审在源码上逮到、**已就地修掉**的缺陷。前两条会让用户读到一个错的数。

### RV-1（展示级阻断）`共识降级（全程）` 标了一个它没有的分母

E-3 把两个 label 补上口径词是对的，但同一次改动顺手给 `degradedRounds` 也加了「（全程）」——
而 `_compute_ai_stats`（`session.py:1832-1866`）里 `degraded` 与 `holdRate` / `avgConfidence`
**是同一个循环里数出来的**，分母都是 `_decisions` 那个 `deque(maxlen=50)`，只有 `decisionRounds`
取 `rounds_completed`。于是这一轮在修 D-8 的同时，又新造了一个 D-8：把一个近 50 轮的计数标成全程。
**修法**：改为「共识降级（近 50 轮）」，并在旁边留下「与 holdRate / avgConfidence 同源」的行内理由。

### RV-2（判据级）风险单位体检判的是出厂止损，不是用户填的那个止损

`tradability_probe._bracket:204` 取 `settings.ai_trade_default_sl_pct`，而 `/ai-trade/tradability`
的入参只有 `instId` / `bar` / `preset`——**弹窗里那一格止损根本没有传下来**。启动路径取的却是
`req.stop_loss_pct or ai_trade_default_sl_pct`（`manager.py:868`）。后果：一个已经把止损改到 1%
（100 bps，对 28 bps 的 ATR 是 3.6 倍、完全对齐）的用户，仍会被按出厂的 5% 告知
「止损离现价约 500 bps……建议改到 0.56%」——**体检不但没帮上忙，还会把一个已经调对的用户劝回去**。
这正是 [P1-8] 自己点名的形状（判据取自不携带该信息的入参），只是这一次漏在了装配层而不是纯核心。

**修法**：`/ai-trade/tradability` 增可选 `slPct`（fraction，`gt=0, lt=1`），`_preset_settings`
一并派生这个覆盖——与预设阈值走**同一个**派生点，探针算的仍然是「按下启动键之后真正生效的那组值」。
前端 `AiTradeTradabilityFields` 新增可选 `stopLossPct` 并进取数 effect 的依赖（既有 350 ms debounce
吸收逐字输入）。入参**照启动路径同样夹紧**到 `ai_trade_max_sl_pct`（`manager._resolve_risk:867`）——
`parseOptionalPct` 只要求 `> 0`，用户填 `5`（=500%）时预检不该 422，而该说出真正会挂出去的那个止损。用例：后端 `test_the_probe_judges_the_stop_the_user_typed_not_the_factory_default`
（500 bps → 100 bps、`oversized` → `aligned`，且两个入参皆缺省时 Settings 原对象直通）；
前端「改止损之后重新取一次并把现值带下去」。

### RV-3（文案级）prompt 把 R 倍数的**均值**写成了「中位」

`format_track_record_text` 渲染的是 `pooled.expectancyR`——那是 `sum(r)/len(r)`（`profitability.py:102`
的算术均值），而模板写的是「已实现 R 倍数中位」。历史快照里根本没有 R 的中位数。给模型一个它无法
核对的错标签，与本轴「每个数都带着它的口径与分母」的立意直接冲突。**修法**：如实改成「均值」
（分母 `rSamples` 本来就在同一行）。

### RV-4（韧性级）R-1 承诺的 best-effort 包装没有落地

§八 R-1 写明「`apply_trip_meta` 包 `try/except → warning`，最坏只丢富化键，绝不阻断落库」，而实现
是裸调用。它的调用点在 `_inject_explain_meta`，跑在 `_dispatch → _persist_trade` 之间——**真实资金
成交的落库路径**。`settle` 自己无 `raise`（G-4 守住），但「结算是富化、落库是账实相符」这条优先级
必须由代码而不是由推理来保证。**修法**：包在 `CostRuntime.apply_trip_meta` 里（放这里而不是 session
里，`session.py` 净增因此不变），范式沿用 `_finalize_run` 的富化 best-effort。

### RV-5（死数据）`risk` 块六个键算了却没有任何读点，其中一个就是本轮的头条结论

端点如约下发 `rP10` / `rP90` / `withinFifthRShare` / `plannedRrMedian` / `medianStopAtrMult` /
`stopAtrCoverage`，而 `ProfitabilityCard` 只读了 `rSamples` / `rP50` / `reachedOneRShare` /
`stopUtilizationP50`。**`withinFifthRShare` 恰恰是 §零第 2 条那句「193 笔里 100% 的 |R| < 0.2」**——
本轮最该让用户看见的一个数，落在了本仓库最高频的那个坑里（后端算了、没有渲染落点 ⇒ 死数据）。
**修法**：新增 `riskDistributionText()`（与两张卡共用同一套格式化器）并在卡上多渲染一行 R 分布，
每一项照例自带分母；`medianStopAtrMult` 不可得时**整项省略**而不是显示一个 0 倍。

顺带修掉同一形状的一处**反向**问题：实时卡的标题写着「本会话已实现（估计 ↔ 实现）」，而卡里三项
全是已实现侧的读数——`realized.edgeErrorMedianBps` / `costErrorMedianBps` 并没有落点。这里**不补
渲染**：估计与实现的对拍按 §3.3 就该在轴 C 的校准卡上做（跨会话、带 coverage），一个会话的样本量
撑不起那个结论。故改的是标题（「逐笔往返结算」），把那句许诺收回来——两个 error 中位数作为随会话
落库的统计保留。

同样补上 `realized.rSamples`：R 的分母不是 `trips`（无止损 / 合约乘数未知的往返算不出 R），
而卡上那个「R 倍数中位」原本没有自己的分母——§一 的产品级不变式对本轮自己的卡同样适用。

### RV-6（呈现）零历史时的翻页条

[P1-4] 把空态早返回改成了「列表区空态」，但翻页条留在了早返回之外——一个从未跑过 AI 自动交易的
用户会看到「暂无历史……」下面跟着「共 0 次 · 第 1 页」和两个禁用按钮，把「这里什么都还没有」说成了
「这一页什么都没有」。**修法**：`total > 0` 才挂翻页条。

### 终审如实记下的覆盖欠账

- **G-14b 没有离线用例**：`trackRecordShown` 的留痕与总闸回滚都在（`test_track_record_shown_is_
  recorded_for_arm_reconciliation`），但「装配 / 不装配两臂各跑 20 轮、比较 `openFunnel.intents`」
  这条对拍**没有实现**——它需要一个能跑满 20 轮真实决策的会话夹具（LLM 决策器），本仓库今天没有。
  终审**不补一条证明不了任何事的替身用例**：轴 D 的回归防线因此实际上只有「总闸一键回滚」这一层，
  §十一.9 的欠账应按这个口径读。
- **§四 的 `trip_ledger.trip_from_meta` 未实现**：查询侧改由 `ai_auto_repositories._trip_meta`
  直接读平仓腿的 `explain.meta`，语义等价、少一层转发，属**有意偏离**，不是欠账。

### 终审复跑（合入前，含 RV-1~RV-6 的修改）

- 后端 AI 子集 `python -u -m pytest tests/test_ai_*.py -q`：**1621 passed / 0 failed / 420 s**（实现节点自报 1620，
  终审新增 1 条 RV-2 用例）。相邻子集（`test_config` / `test_api` / `test_live_auto_*` /
  `test_live_executor*` / `test_exchange_backstop` / `test_fills_explain`）**183 passed / 0 failed**
  ——守 §九.8「不动 `live_auto` 链路」，本轮唯一碰到的共用面是 `api/ai_auto._preset_settings`。
- 前端 `NODE_ENV=test npx vitest run`：**1741 passed / 0 failed**（155 文件；1736 + 终审新增 5 条：
  G-9b 的两处 `realized` 渲染断言 ×3、R 分布一行 ×1、止损透传 ×1）。
  **G-9b 的组件用例本来是缺的**——两张 `realized` 落点此前只有类型与代码，没有一条断言它们真的
  画得出来；那正是 [P1-2] 要防的形状，故补齐视为放行条件而非加分项。
- `npx tsc --noEmit`：只剩既有的 `WeightBatchArena.test.tsx`（合入前即存在，非本轮回归）。
- `ruff check`：终审改动文件全清。
- **G-13 冻结**：九个文件 `git diff --name-only` 仍**零命中**（终审的改动一个都没落在它们上面）。
- 暂存范围：本特性 45 个文件。`config.py` 是混合文件——工作树里另有一处**不属于本轮**的
  `ai_trade_enabled` 默认值翻转（连同 `.env.example` / `desktop/src/lib/config.js` /
  `tests/test_config.py`），按 §十.1 的手法逐 hunk 精确暂存，那四处**留在工作树未提交**。
