# AI 自动交易：可交易性预检、成本感知止损下界与开仓漏斗

> **版本**：**v2**（设计评审后修订；v1 = 设计节点产出）
> **迭代**：loop `tge-dffbcee0` 第 2 轮（第 1 轮 = `ai-trade-discipline-preset-and-integrity`，已合入 `633f404`）
> **特性目录**：`docs/plans/ai-trade-tradability-and-open-funnel/`
> **红灯基线（本轮实测）**：`pytest tests -q -k "ai_trade or ai_auto or ai_backtest"` →
> **1487 passed / 0 failed**（259s）。全量下另有 7 条红灯来自未跟踪孤儿文件
> `tests/test_factor_library_expansion.py`（不在 HEAD 中，非本轮引入，见第 1 轮记录）。
> **评审状态**：**有条件通过**（4 P0 / 11 P1 / 9 P2，P0+P1 已在正文闭合；放行条件见文末 §评审结论）。

---

## 评审记录（Review Notes，v1 → v2）

> 评审方法：**不信任任何未经核实的引用**。v1 引的每一处 `file:line` 都在仓库里逐条打开对照；
> 每一条新判据都沿着「数据从产生到上屏的每一跳」走了一遍（本仓库最常复发的缺陷形状就长在这条链上）。
> 核实结论先说好的一半：**v1 的八条缺陷证据（D-1~D-8）全部命中真实代码位置，无一条虚指**——
> `edge_gate.py:113-118` 的分子式、`sizing.py:311-313` 的「只有上界没有下界」、
> `decider.py:554/561/575` 的成功分支才清空、`decider.py:211` 的无 maxlen `list`、
> `session.py:771-772` 早于 `:776`、`session.py:1604/1610/1657/1666` 的两处装配点、
> `discipline_runtime.py:123-139` 没有开仓意愿分母、`backtest.py:835-839` 缺 `p_win`/`cost_bps`、
> 以及 `AiTradeLaunchModal.tsx:83` 的 `BARS` 第一项确实是 `1m`——逐条属实。
> 下面列的是**在此之上**发现的问题：4 条 P0 全部长在「新判据 ↔ 既有代码」的接缝上，
> 与本仓库前几轮的统计一致。

### P0（阻断级：不改就会交付一个假装在工作的功能）

**P0-1 探针判据漏掉 `degraded_multiplier`，在默认路径上恒定低估否决率 20%，方向恰好与 R-2 相反。**
`edge_gate.py:148` 的真实判据是
`required = k_min × cost.total_bps × degraded_multiplier(cost, degraded_mult)`，
而 `degraded_multiplier`（`edge_gate.py:249-251`）在 `cost.confidence == "degraded"` 时返回
`ai_trade_edge_gate_degraded_mult`（默认 **1.2**，`config.py:458`）。
v1 §3.1.1 的探针式子是 `atr_bps × mult < k_min × cost_bps`，**没有这个因子**。
更要命的是它在探针路径上**恒定生效**：v1 §3.1.2 构造的是一个
`CostRuntime(..., fee_ledger=None, creds=None)`——这个实例的 `FundingCache` 从未被
`schedule_maintenance` 轮询过，`snapshot()` 恒返回 `None` ⇒
`query_for_preview(funding=None, funding_enabled=True)` 走 `cost_model.py:229-231` 给出
`funding_rate=None` ⇒ `_funding_carry_bps` 返回 `None`（`cost_model.py:154-156`）⇒
`estimate_round_trip_cost` 落 `confidence="degraded"`（`cost_model.py:191`）。
于是探针算 `required = 1.5 × 13.0 = 19.5`，而实盘门禁要的是 `1.5 × 1.2 × 13.0 = 23.4`。
**探针会说「能开」而门禁实际否决**——这正是 v1 自己在 R-2 里写下的、比不给探针更坏的那一种失败。
而 G-2 若只随机 `(atr, cost, k, mult)` 四元组、把 `confidence` 固定在 `"ok"`，这条漂移**测不出来**。
→ 已在 §3.1.1 / §3.1.2 / §5.1 / G-2 闭合。

**P0-2 轴 B 的 `floor_above_cap` 是一个在当前设计下**产生不出来**的状态。**
v1 §3.2.1 让 `resolve_stop_distance` 在 `floor_pct > sl_cap` 时 `return None`，并承诺
`stopSource` 记为新态 `"floor_above_cap"`。但 `None` 把「为什么没覆盖」这个信息**擦掉了**：
`sizing_runtime._apply_stop`（:250-262）对 `plan is None` 的两条路径都只能得到 `"fixed"`——
`executor is None ⇒ source = "fixed"`（:253）；`executor` 非空 ⇒
`apply_risk_override(executor, None, action)` 在 `sizing_runtime.py:94-95` 第一行就
`return "fixed"`。调用方**无从区分**「ATR 不可用」与「下界高于用户红线」。
连带作废的有：`stopSource` 的第七态、`counters.floor_above_cap_rounds`、
`aiStats.stopFloored.floorAboveCapRounds`、§3.2.1 说的「由轴 C 的漏斗与 digest 如实呈现」、
以及 G-6 的第三分支——**全部恒不可达**。这是本仓库成文缺陷模式「后端算了但没有产出通道」的
镜像版本：*有读点、没写点*。
→ 已在 §3.2.1 / §3.2.1a 改为 `(plan, skip_reason)` 二元返回并闭合。

**P0-3 开仓漏斗会把**平仓侧**的降级计进开仓分母，漏斗在算术上不闭合。**
v1 §3.3.1 把首拦截层的归类点放在 `session.py:788` 的 `note_round(degrade)` 旁，理由是
「那里的 `degrade` 天然就是第一个拦下它的层（后续层看到的已是 hold）」。前半句对，后半句
**不成立**：持仓周期铁律层的作用域是**平仓与反手**，不是开仓——
`hold_horizon.gated_kind`（:558-573）第一条就是
`if action == "close" and position_side in ("long","short"): return "close"`。
一轮 `close` 意图被 `horizon_min_hold` 拦下时，`degrade` 非空、而 `intents` **没有 +1**
（第一阶段动作不是 `open_*`），归类却会给 `blocked["horizon"] += 1`。
于是 `sum(blocked)` 可以超过 `intents`，而「意愿 → 各层拦截 → 委托 → 成交」这张图的**全部**
价值就是它闭合。`exchangeBackstop`（`backstop_gate`）同源同病。
对照组说明这不是一刀切按层名就能解决的：`base_rules.py:211` / `drawdown_brake.py:196` /
`profit_cooldown.py:74` / `edge_gate.py:144` / `sizing.py:332` 的白名单都是
`("open_long","open_short")`，只有 horizon / backstop 越界；而**反手**（`open_long` 遇空头仓）
被 horizon 拦下时确实是一次开仓意图、**应当**计入。判据必须来自「本轮记没记过 intent」这个事实，
而不是来自层名。
→ 已在 §3.3.1 引入轮级 `_intent_pending` 标志并加 G-10b 闭合不变式。

**P0-4 `sizingZero` 是一条永远点不亮的层；`low_confidence` 会在漏斗里被计两次。**
(a) `LAYERS` 含 `"sizingZero"`，但 `_apply_sizing`（`session.py:763` 调用、`:1112-1122` 定义）
**不返回 `degrade`**——`degrade` 在第 788 行之前最后一次被赋值是反思层（`session.py:761`）。
仓位解到 0 在全仓不产生任何 degrade 码，`classify_block` 永远归不到它。
(b) `low_confidence` 被 v1 §3.3.1 算进 `llm_degraded`（"第一阶段就因 llm_error / timeout /
low_confidence 降级"），又会在第 788 行被 `_DEGRADE_PREFIXES` 的
`("low_confidence", "confidence")`（`discipline_runtime.py:25`）归进 `blocked["confidence"]`
——**同一轮在漏斗的两个格子里各出现一次**。而且它本就不是 LLM 故障：`config.py:157` 明确把
`low_confidence` 归为 genuine 决策、不计入重试。
→ 已在 §3.3.1 收窄 `llmDegraded` 口径、给 `sizingZero` 一个真实产生点。

### P1（必须在本轮闭合）

**P1-1 D-8 只被修了一半，而 §3.5 的整条论证建立在「已修好」之上。**
§3.2.3 自己写着「回测没有先验运行时 ⇒ 传 `None`」；而 `sizing.kelly_fraction` 的第一条判据
（`sizing.py:278-280`）就是 `p is None ⇒ ("no_prior")`，**短路在 `cost_bps` 检查之前**
（`sizing.py:281`）。所以补上 `cost_bps` 之后，回测里凯利项**依然恒不参与 `min`**——
D-8 标题所指的「仓位求解不同构」在轴 B 之外**一字未改**。
连带的实施陷阱：单补 `cost_bps` 对回测**仓位**是零行为变化，唯一的行为变化来自轴 B 的止损下界；
实现节点若期待仓位数字变化，会把「没变化」误判成没接上。
→ 已在 D-8 / §3.2.3 / §3.5 分层如实改写，`p_win` 记为本轮欠账。

**P1-2 轴 B 默认开启会改写既有 AI 回测的止损位，G-7 的冻结门禁盖不住默认路径。**
`ai_trade_stop_cost_floor_mult` 默认 `2.0`，且 §3.2.3 要求回测也传 `cost_bps`，
⇒ 所有既有 AI 回测的 `_oco_risk`（`backtest.py:840-843`）会被改写 ⇒ 依赖回测数值的既有用例
可能整片翻红。G-7 只冻结了 `mult=0` 那一支。
→ 已加 G-7b 与 §十 的实施注意。

**P1-3 两处会撞 CLAUDE.md 800 行硬线的落点没有行数预算。**
`api/ai_auto.py` 现 **671** 行，而 v1 把 `_probe_window`（REST/缓存双路 + `alternatives` 的
`asyncio.gather` + 逐周期窗口裁剪）塞进它；`AiTradeLaunchModal.tsx` 现 **1542** 行，
还要再加一整块预检分区 + 二次确认 + 一键换周期。v1 §八 R-6 只给 `session.py` 立了预算。
→ 已在 §四 / §八 R-6 下沉为新模块 `app/ai_auto/tradability_probe.py` 与新组件
`AiTradeTradabilityFields.tsx`（后者与同文件 `:77-78` 已 import 的 `AiTradeHorizonFields` /
`ProfitGuardFields` 同款范式）。

**P1-4 `allow_cached_fallback` 默认 `False`，G-3 的「有缓存 ⇒ 200 + `source==cache`」在默认配置下不可达。**
`config.py:633` `allow_cached_fallback: bool = False`。v1 §3.1.2 镜像 `api/market.py:100` 的姿态
⇒ 默认配置下 OKX 一不可达，探针直接 `unknown`——而用户本机 `candles_cache` 里**正躺着**本轮
实测用的 39764 根 BTC 1m。探针会在数据就在本地时说「算不出」。
→ 已在 §3.1.2 改为**无条件读缓存**并写明这是一次**有意偏离**及其理由（探针算的是历史窗口统计量、
不驱动任何交易判定，与那个开关要防的「用陈旧价格下单」正交）。

**P1-5 §3.2.1 的伪码有两处硬错。**
`floored` 只在 `if floor_pct > stop_pct` 分支里被赋值，却在
`source = "atr_floored" if floored else "atr"` 无条件读 ⇒ `NameError`；
正文写「增两个 keyword-only 形参」却只列出 `cost_bps` 一个。
→ 已在 §3.2.1 修正。

**P1-6 附录「1m 上资金费期数为 0，carry=0」与 `funding_periods` 的 `max(1, …)` 相矛盾。**
`cost_model.py:143-145`：`return max(1, math.ceil(hold_s / interval)) if interval > 0 else 1`
——期数**恒 ≥ 1**，1m × `expected_hold_bars=8`（`config.py:454`）同样按 1 期计。
故实盘 `total_bps = 13.0 + carry(≥0)`：**13.0 是下界，不是等式**。
结论方向不变（真实否决率只会**更高**），但 §十.9 要求「数字是判据的依据，不是装饰」，口径必须准确。
→ 已在 §二 前言与附录 A 改述。

**P1-7 `alternatives` 各周期的成本没有跟着周期换算，会系统性低估长周期的否决率。**
`estimate_preview_cost` 的 `bar_seconds` 直接决定 `funding_periods`
（`expected_hold_bars × bar_seconds / interval`，`cost_model.py:143-145`）——
`1D × 8 根 = 8 天 ⇒ 24 期资金费`，与 `1m` 的 1 期完全不是一个数。v1 §3.1.2 只说「对更长周期各跑
一次探针」，没说成本要不要跟着换。沿用当前 bar 的成本 ⇒ 长周期看起来比真实更可行，
而 `advice` 的第一条规则**正是**据此推荐周期的。
→ 已在 §3.1.2 / §5.1 要求每项 `alternatives` 用自己的 `bar_seconds` 重算并回带 `costBpsMarket`。

**P1-8 `strictness_hint` 的新签名踩 CLAUDE.md「形参 ≤ 5」，且与本仓库已建立的聚合范式冲突。**
`strictness_hint(rows, *, funnel, tradability_level, rounds_completed, opens)` 恰好 5 个。
`discipline_view.py:41` 的 `DisciplineFacts` docstring 里白纸黑字写着这条规则和它的解法
（「聚合结构体，遵守 CLAUDE.md……入参 ≤ 5」）——本轮不该在同一个文件里另立一套。
→ 已在 §3.3.2 收成 `strictness_hint(rows, facts: StrictnessFacts)` 两参。

**P1-9 `summaryHealth` 的第二个装配点 `info()` 没有指定消费者与渲染落点，会变成第二份死数据。**
§十.7 要求 `_status_payload`（`session.py:1610`）与 `info()`（`:1666`）都挂——这条判断是对的
（同一读数两处装配、只改一处是成文缺陷模式）。但 §3.4.4 的呈现落点只写了 `AiTradePanel` 与
`DisciplineCard`，两者都走 WS `status`；`info()` 这一路的消费者与渲染点文档里是空的。
→ 已在 §3.4.4 / §十.7 二选一地写死（点名消费者，或明确「只服务只读快照与落库、不要求渲染落点」）。

**P1-10 三个 `tradability` 断面（弹窗 / 启动回执 / 会话 status）没说清是不是同一份。**
§3.1.3 第 3 点说会话 status 增 `tradability`，§六.3 说「启动后不变」，R-10 说「按 `instId` 逐成员
算并各自落成员 status」，§六.2 又说落库的是「启动断面」。而 AI-trade **没有分组级实时通道**
（`group_status()` 只在挂载拉一次），弹窗那次探针发生在会话还不存在的时刻。三个断面必须指明同源关系，
否则用户会看到两个不同的否决率而无从判断哪个是真的。
→ 已在 §3.1.3 / §六.3 写死：启动回执与会话 status **共用** `manager.start` 算的那一份（同一 `asOfTs`），
弹窗那份是独立的事前预览、允许不同，各自带 `asOfTs` 供对照；落库的是启动那份。

**P1-11 D-5 与 D-3 的退避改的是同一个 `if`，v1 分列两节且没给合并后的最终形态。**
`decider.py:547-548` 是 `if self._rounds_since_summary >= self.context_refresh_rounds:
return await self.summarize()`。§3.4.2 第 2 条要把它改成 `>= refresh + skip`，§3.4.3 又要加
`defer_summary` 分支。实现节点很容易只改一处，而两处都错的表现是**静默的**（摘要不再触发，
或退避不生效）。
→ 已在 §3.4.2a 给出合并后的唯一最终判定式。

### P2（记录，不阻塞本轮；实现节点酌情吸收）

- **P2-1** 探针分子忽略 OCO 止盈上界（`edge_gate.py:117-118`）与信号先验 `min`（`:123-124`），
  两者都只会让门禁**更严**。故探针是一个**乐观下界**——这句必须写进 §3.1.1 的 docstring 与
  `disclaimer`，且 G-2 的对拍须显式固定 `take_profit_pct=None, prior_ev_bps=None` 并注明理由。
  （已在 v2 §3.1.1 顺手写入。）
- **P2-2** 探针用 `close` 算 `atr_bps`，门禁用 `ref_px`（限价即委托价，`edge_gate.py:93`）；
  bps 尺度下差异可忽略，但要在 docstring 里写明是两个价。
- **P2-3** `config.py` 现 **740** 行，+8 个带注释的字段逼近 800 硬线；§六.1 已补行数预算与超线方案。
- **P2-4** §5.1 示例里 `samples: 200` 与 §二 表里的 39764 根不是同一口径（探针窗口 vs 全量缓存），
  并排放容易被读成「200 根就复算出了 98.6%」。已在 §5.1 加一行注释。
- **P2-5** `veto_rate` 的分母是**全部 K 线断面**，而门禁只在开仓意图轮上判定；探针度量的是条件概率
  「若在这一根上想开仓会不会被否」。本仓库已有「观望轮稀释 `edgeGate` 分母」的前科，文案须写成条件式。
- **P2-6** `OpenFunnelCounters` 是全程累计，`AttributionLedger._trips` 是 `deque(maxlen=200)` 滚动窗口；
  R-9 已提到，建议把两处口径词固化成共享常量文案，避免两张卡并排时读者串口径。
- **P2-7** §四 的测试表漏了 `apps/web/lib/aiTradeSizing.test.ts`，而 R-5 明确它会被 `stopSource`
  七态打红（`:128-132` 是按数组遍历 source 的）。已补进表。
- **P2-8** G-19 写「≥ 1487 passed」与「0 failed」并存时，无法区分「新增用例通过」与「既有用例被改写」。
  已改为「AI 子集 0 failed，且逐条列出因口径变更而修改的既有用例」。
- **P2-9** `infeasible` 的二次确认没说是否记忆用户选择（反复切 bar 会不会反复要求确认）。
  已在 §3.1.3 写明「`level` 由非 `infeasible` 变为 `infeasible` 时重置确认态」。

---

## 零、本轮结论摘要（先读这一段）

第 1 轮把「盈利所需的机制」从默认关闭状态**真正装配**了起来（`balanced` 预设默认生效：
edge 门禁 `k=1.5`、共识门槛、置信度门槛、波动率目标化仓位、ATR 止损覆盖、回撤刹车、连亏熔断）。
第 1 轮同时**如实记下了一笔欠账**：`G-17 校准证据未取得`——两臂真实 LLM 回测跑了 50 分钟没跑完，
因此「这组阈值到底是让系统赚钱还是让系统停摆」当时没有任何证据。

本轮把那笔欠账当作调查起点，**不靠推测、靠用户本机 `%APPDATA%/aragon-desktop/aragon.db` 的
`candles_cache` 真实 K 线实测**，得到了一个必须立刻修的结论：

> **在 1m / 5m 周期上，第 1 轮装配的默认纪律使 AI 自动交易在数学上几乎不可能开仓。**
> BTC-USDT-SWAP，`balanced` 预设，市价开仓口径：**98.6%（1m）/ 65.0%（5m）** 的 K 线断面上，
> 最小 edge 门禁的分子（`1.0 × ATR(14)`）达不到分母要求（`1.5 × 13.0 bps 往返成本`）。
> 而 `1m` 正是启动弹窗时间级别下拉框的**第一个选项**（`AiTradeLaunchModal.tsx:83`）。

用户看到的现象就是任务描述里那句「AI 自动交易现在应该还有问题」：**开着、在跑、每轮都有分析、
就是从不开单**。这在本仓库有四次前科（「恒真判据 ⇒ 永不开单」），第 1 轮为此专门做了
`strictness_hint` 自检——但那条自检给出的补救建议（「改选更宽松的预设」）在这个断面上是**错的**：
同一批 K 线上，最宽松的 `aggressive`（`k=1.0`）仍有 **93.0%** 的否决率。真正的补救是**换时间级别**。

本轮据此设计五轴，全部围绕「让它真的能交易，并且交易得起」：

| 轴 | 名称 | 一句话 |
|---|---|---|
| A | 可交易性预检 | 按下启动键**之前**就用真实 K 线 + 真实成本算出「这个周期上有多少比例的开仓意图会被否决」，并给出对症建议；只提示、不阻断 |
| B | 成本感知的止损下界 | ATR 止损距离不得落进往返成本带内（今天只有上界夹紧，没有下界） |
| C | 开仓漏斗 | 补上全仓缺失的**分母**（模型开仓意愿轮数），把「想开 N 次 → 各层拦掉多少 → 真成交多少」做成一屏可见 + 可落库；并据此把 `strictness_hint` 从一句猜测升级为对症建议 |
| D | 决策记忆完整性 | 修掉滚动摘要的失败螺旋（journal 无界增长）、空诊断串、阻塞决策事件、以及「停摆了但没有任何呈现落点」 |
| E | 校准闭环 | 把 G-17 欠账从「再跑一次 50 分钟 LLM 回测」改成「读既有历史 + 秒级确定性探针」，并修掉回测与实盘仓位求解不同构的偏差源 |

---

## 一、概述

AI 自动交易在本仓库已经迭代了十余轮，机制层面（成本记账、edge 门禁、持仓周期铁律、利润保护棘轮、
换手预算、交易所侧兜底、唤醒调度、纪律预设）是完整的。本轮**不新增任何交易机制**，只做三件事：
把已经装配的机制里**会导致它不工作的缺陷**修掉；把「它为什么不工作」变成用户**在动手之前**就能看见
的一句中文；把「它到底赚不赚钱」变成**不依赖 LLM、秒级可跑**的证据。

之所以这三件事是同一个特性而不是三个，是因为它们共用同一条因果链：**开仓意图 → 八层门禁 → 委托 →
成交 → 净额盈亏**。今天这条链上有分母缺失（没人数「模型想开几次」）、有量纲不可行（短周期的 ATR
幅度打不过成本）、有安全阀方向错误（止损可以比成本还窄）、有记忆链路静默停摆（滚动摘要失败后越失败
越容易失败）。任何一处单独修都无法回答用户那句「确保能够正确的自动交易，并且稳定盈利」。

设计上有一条贯穿全轴的极性纪律，写在这里，实现节点不得违反：**预检与漏斗一律 fail-open，只提示、
不阻断**。本仓库四次「永不开单」事故全都源于把「不确定」翻译成了「否决」；一个把用户挡在启动之外的
探针，比一个给错建议的探针更坏。唯一的例外是轴 B 的止损下界——它不改变「开不开仓」，只改变「止损挂
在哪里」，且当下界与用户可见红线（`ai_trade_max_sl_pct`）冲突时**整条覆盖退回固定 pct 并如实回执**。

本轮同样承接第 1 轮立下的文案纪律：**任何 UI 文案与文档都不得出现「已校准 / 经回测验证」**。轴 A 的
探针输出的是「在最近 N 根真实 K 线上，按当前成本常数与门禁阈值算出的否决率」——这是一个**可复算的
确定性统计量**，不是收益承诺，文案必须写清它是什么、不是什么。

---

## 二、勘察实证：八条缺陷（逐条附 `file:line` 与实测数字）

> 实测方法：读 `%APPDATA%/aragon-desktop/aragon.db` 的 `candles_cache`（用户本机真实 OKX 历史），
> 按 `engine.indicators.atr` 同款 Wilder 递推算 ATR(14)，逐根算 `ATR/close × 1e4`，
> 再按 `cost_model.estimate_round_trip_cost` 的默认常数算往返成本。
> 成本常数出处：`config.py:449` `maker=2.0`、`config.py:450` `taker=5.0`、`config.py:451`
> `half_spread_fallback=1.0`、`config.py:452` `impact=0.5`；`cost_model.py:34`
> `_CROSSING_LEGS={"limit":1,"market":2}`；`cost_model.py:184` `total = fee_open+fee_close+legs×(spread+impact)+carry`。
> ⇒ **市价往返 13.0 bps、限价开仓 8.5 bps**——这两个数是 `carry=0` 的口径。
>
> **[v2 更正，P1-6] 13.0 / 8.5 是下界而不是等式。** v1 写的「1m 上资金费期数为 0」不成立：
> `cost_model.py:143-145` 的 `funding_periods` 返回 `max(1, ceil(hold_s / interval))`——
> **期数恒 ≥ 1**，1m × `expected_hold_bars=8`（`config.py:454`）同样按 1 期计；
> 而 `_funding_carry_bps` 取 `max(0.0, signed)`（`cost_model.py:159`，只取不利侧）⇒ `carry ≥ 0`。
> 故实盘真实 `total_bps = 13.0 + carry(≥0)`。**结论方向不变且更强**：真实往返成本只会更贵，
> 下表的否决率只会更高。另需注意 `funding_rate` 取不到时 `carry is None` ⇒
> `confidence == "degraded"`（`cost_model.py:191`），门禁分母还要再乘一个 `1.2`（P0-1，见 §3.1.1）。
> 本文档一律以 `carry=0` 的保守下界作为对外引用值；**任何 UI 数字都必须由运行期现算，不得抄本表**。

### D-1（阻断级）时间级别与成本结构不匹配时，系统静默地永不开单

`edge_gate.py:114-129` 的分子是 `min(atr_target_mult × ATR_bps, OCO 止盈距离)`，
`atr_target_mult` 默认 `1.0`（`config.py:459`）；分母是 `k_min × cost_bps`，`k_min` 由
`balanced` 预设置为 `1.5`（`discipline.py:67`）。实测（BTC-USDT-SWAP，样本为本机全部缓存根数）：

| 周期 | 样本 | ATR(14) p10 / 中位 / p90（bps） | 否决率（市价） | 否决率（限价） |
|---|---:|---|---:|---:|
| 1m  | 39764 | 2.9 / **5.6** / 11.3 | **98.6%** | 92.6% |
| 5m  | 13714 | 8.8 / **15.9** / 31.6 | **65.0%** | 31.0% |
| 15m | 5943 | 15.1 / 28.1 / 50.4 | 20.3% | 5.9% |
| 30m | 8359 | 24.9 / 44.4 / 74.9 | 4.3% | 0.2% |
| 1H  | 3737 | 33.1 / 56.5 / 86.7 | 0.8% | 0.0% |

按预设横切（BTC，市价口径）：

| 周期 | steady(k=2.0) | balanced(k=1.5) | aggressive(k=1.0) |
|---|---:|---:|---:|
| 1m | 99.8% | 98.6% | **93.0%** |
| 5m | 82.6% | 65.0% | 32.5% |
| 15m | 41.6% | 20.3% | 6.1% |

**结论**：`1m` 上换预设解决不了问题（最宽松仍 93%）；`5m` 上 `steady` 同样接近停摆（82.6%）。
门禁的判定本身**是对的**（短周期的单笔幅度确实覆盖不了成本），错的是**产品从不在事前说这件事**，
而且事后给出的唯一建议指向了错误的旋钮。ETH 同形（1m 97.8% / 5m 43.9%）。

### D-2（阻断级）ATR 止损没有成本下界，止损可以挂在成本带以内

`sizing.py:294-321` `resolve_stop_distance`：第 311 行 `stop_pct = rule.stop_atr_mult * atr / px`，
第 312-313 行**只有上界夹紧** `min(stop_pct, sl_cap)`，**全函数没有任何下界**。
`balanced` 装配 `stop_atr_mult=2.0`、`stop_rr=2.0`（`discipline.py:75-76`）。实测 2×ATR 的止损距离：

| 周期 | 止损距离中位（bps） | 落在市价成本(13bps)以内的占比 | 落在限价成本(8.5bps)以内 |
|---|---:|---:|---:|
| BTC 1m | 11.2 | **62.3%** | 28.7% |
| BTC 5m | 31.9 | 2.9% | 0.1% |
| ETH 1m | 14.1 | 42.5% | 19.7% |

止损距离小于往返成本意味着：**这一单只要被止损打掉，亏掉的价格幅度还不如手续费多**；同时
`stop_rr=2.0` 派生的止盈距离（22.4 bps 中位）也只有成本的 1.7 倍——这是一台结构性的手续费机器。
`executor.risk.stop_loss_pct` 是**价格**百分比（`live_auto/executor.py:757-766`
`sl = fill*(1-slp)`），因此这里没有杠杆放大的解释空间。

### D-3（阻断级）滚动摘要失败后 `_journal` 无界增长，形成越失败越容易失败的螺旋

`decider.py:551-576` `summarize()`：第 554 行 `journal = list(self._journal)` 取全量素材，
第 575 行 `self._journal = []` **只在成功分支**执行；第 559-562 的失败分支只做
`self._rounds_since_summary = 0` 并返回 `None`。于是每失败一次，下一次摘要的素材就多
`context_refresh_rounds`（默认 10，`config.py:148`）轮，`build_summary_prompt`
（`prompts.py:815-835`）把全量 journal 塞进一条 user 消息 ⇒ prompt 单调变长 ⇒ 更容易再超时。
第 1 轮的 G-17 记录里实测出现了 **4 条连续的 `ai-trade summarize failed:`**，正是这个螺旋的现场。
`_journal` 在 `decider.py:211` 是普通 `list`，**没有 maxlen**。

### D-4 摘要失败的日志诊断串恒为空

`decider.py:556-560`：`asyncio.wait_for(..., timeout=self.settings.ai_trade_decision_timeout_s)`
超时抛 `TimeoutError`，而 `str(TimeoutError()) == ''`（本轮已实跑验证）。第 560 行
`logger.warning("ai-trade summarize failed: %s", exc)` 因此打印出的整行是
`ai-trade summarize failed: `——**看不出是超时还是 LLM 报错**，这正是第 1 轮记录里
「4 条空错误串」查不下去的原因。

### D-5 摘要在决策事件之前被 await，超时即把本轮结果压后一分钟才上屏

`session.py:771-772` 的 `await self.decider.commit_round(...)` 排在
`session.py:776` 的 `_emit_decision(...)` **之前**。摘要走 60s 超时（`ai_trade_decision_timeout_s`，
`config.py:154`），于是「成交已经发生、决策已经完成」之后，界面上的决策事件（动作 / 理由 / 研判摘录）
还要再等最多 60 秒才出现。成交事件走 `_dispatch` 不受影响，因此现象是**成交先到、解释后到**。

### D-6 摘要健康度没有任何呈现落点

`_status_payload`（`session.py:1604-1627`）只有 `roundsSinceSummary`。失败次数、连续失败、
「长期记忆已停摆」这件事在全仓**没有任何 wire / 事件 / 前端渲染点**。按本仓库反复出现的形状
（「后端算了但用户看不见」＝死数据），这条必须补齐。

### D-7 开仓漏斗缺分母：全仓没有「模型开仓意愿轮数」这个计数

`discipline_runtime.py:123-139` `_gate_counts()` 提供 11 个**拦截计数**，但没有任何一处统计
「第一阶段决策是 `open_long`/`open_short` 且非降级」的轮数。`session.py:726` 的
`_update_hold_streak` 与 `session.py:739` 的 `_horizon.note_intent` 都在正确的落点上看到了这个
断面，但都只用于各自的状态机、不计数。后果：
`discipline_view.py:84-99` 的 `strictness_hint` 只能在「成交数 == 0 且轮数 ≥ 8
（`discipline_view.py:36`）」时给出一句概括性提示，且其建议固定为「改选更宽松的预设（或关闭）」
——在 D-1 断面上这是**错误的补救方向**（见上表）。

### D-8 回测与实盘的仓位求解不同构，使「用回测校准实盘阈值」的结论不可迁移

`backtest.py:835-839` 构造 `SizingContext` 时**没有传 `p_win`，也没有传 `cost_bps`**
（两者在 `sizing_runtime.py:131-132` 默认 `None`）。而实盘侧 `session.py:1115-1118` 两者都传。
`sizing.py:279-290` `kelly_fraction` 对 `p_win is None` 返回 `no_prior`、对 `cost_bps is None`
返回 `no_cost` ⇒ **回测里凯利项恒不参与 `min`**。这意味着同一预设在回测里的仓位系统性大于实盘，
而 G-17 欠账要走的正是「用回测对照校准这组阈值」这条路。

> **[v2 更正，P1-1] 本轮只修得了一半，剩下一半是欠账，必须说清楚。**
> `kelly_fraction` 的判据是**有序**的：`sizing.py:278-280` 先判 `p is None ⇒ ("no_prior")`，
> `cost_bps` 的检查在 `sizing.py:281`——**短路在后**。而回测侧根本没有 `PriorRuntime`
> （先验快照由 `manager._resolve_prior_snapshots` 在实盘启动流程里装配），`p_win` 只能传 `None`。
> 因此：
>
> 1. **补 `cost_bps` 之后，回测里凯利项依然恒 `no_prior`、依然不参与 `min`。** 本条缺陷的标题
>    「仓位求解不同构」在轴 B 之外**一字未改**。
> 2. 反过来说，补 `cost_bps` 对回测**仓位**是**零行为变化**（它在 `no_prior` 之后才被读到）；
>    唯一的行为变化来自轴 B 的止损下界。**实现节点若期待仓位数字变化，会把「没变化」误判成没接上**——
>    这一条必须写进实现注释。
> 3. 故本轮把 D-8 拆成两半：`cost_bps` **本轮补齐**（它是轴 B 在回测侧生效的必要条件，见 §3.2.3）；
>    `p_win` 的不同构**本轮不修**，如实记为欠账（修它意味着给回测引入一条先验装配链路，
>    那是一次独立的特性，混进本轮会让回归面失控）。
> 4. §3.5 的论证据此改写：**正因为回测的仓位求解与实盘仍不同构**，本轮才不用它做阈值校准
>    ——结论不变，前提要说实话。

---

## 三、技术设计

### 3.1 轴 A —— 可交易性预检（Tradability Probe）

#### 3.1.1 新纯核心 `app/ai_auto/tradability.py`（零 IO、零 pandas 依赖，≤ 300 行）

```python
@dataclass(frozen=True)
class TradabilityInputs:
    """探针入参：**已经算好的** ATR bps 序列 + 两个成本读数。本模块不取数、不算 ATR。"""
    atr_bps: tuple[float, ...]        # 每根的 ATR(period)/close × 1e4，已剔除暖机段
    cost_bps_market: float | None
    cost_bps_limit: float | None
    k_min: float                      # 生效预设的 ai_trade_edge_gate_k_min
    atr_target_mult: float            # ai_trade_edge_atr_target_mult
    stop_atr_mult: float              # 0 ⇒ 止损带判据整体缺席（不填 0 冒充）
    # [v2/P0-1] 成本估算置信度与降级收紧倍数：门禁的分母是
    # `k_min × cost × degraded_multiplier(cost, degraded_mult)`（edge_gate.py:148），
    # 少这一项探针会恒定低估 20% 的否决率。**两个字段都必须由调用方从真实 CostBreakdown 取，
    # 严禁在本模块里假设 "ok"。**
    cost_confidence_market: str       # "ok" | "degraded"（CostBreakdown.confidence 原样）
    cost_confidence_limit: str
    degraded_mult: float              # ai_trade_edge_gate_degraded_mult（默认 1.2）

@dataclass(frozen=True)
class TradabilityVerdict:
    level: str            # "ok" | "marginal" | "infeasible" | "unknown"
    samples: int
    atr_p10/atr_p50/atr_p90: float | None
    required_bps_market/required_bps_limit: float | None
    veto_rate_market/veto_rate_limit: float | None
    stop_bps_p50: float | None
    stop_inside_cost_rate_market/…_limit: float | None
    cost_confidence_market/…_limit: str    # [v2/P0-1] 与 required_bps 同源下发，供前端解释「为什么阈值是这个数」
    degraded_mult_applied: float | None    # [v2/P0-1] 实际乘进 required 的那个倍数（1.0 或 degraded_mult）
    headline: str         # 一句话结论（中文，后端下发，前端一个字都不重写）
    advice: str | None    # 对症建议（中文）；level=="ok" ⇒ None
```

判据（冻结常量写在模块顶部，**并在 docstring 里写明这组数字来自本轮用本机 `candles_cache` 的实测**）：

- `samples < _MIN_SAMPLES(=60)` 或 `cost_bps_market is None` ⇒ `unknown`（**绝不**回落 `ok`）。
- 判定用**市价口径**（保守：`_CROSSING_LEGS["market"]=2`，且 Agent 在回测里恒市价）。
- `veto_rate_market >= ai_trade_tradability_infeasible_veto_rate`（默认 0.80）⇒ `infeasible`。
- `>= ai_trade_tradability_marginal_veto_rate`（默认 0.40）⇒ `marginal`。
- 否则 `ok`。

**[v2/P0-1 修订]** 判定式（`required_bps` 与 `veto_rate` 是同一条式子的两面）：

```python
required_bps = k_min * cost_bps * degraded_multiplier_from(cost_confidence, degraded_mult)
veto_rate    = |{ i : atr_bps[i] * atr_target_mult < required_bps }| / samples
```

`degraded_multiplier_from` **必须直接复用 `edge_gate.degraded_multiplier`**（它已是模块级公开纯函数，
`edge_gate.py:249-251`），不得在本模块重写一份「`"degraded"` 就乘 1.2」的等价逻辑——
本仓库的成文纪律是「别人拥有的值域必须单点转发」。

**这个式子必须与 `edge_gate.estimate_gross_edge_bps` + `apply_edge_gate` 的判据同构**——
探针说「会被否 98%」而门禁实际放行，比不给探针更坏。实现节点须把这一条做成用例
（G-2：随机 200 组 `(atr, cost, k, mult, confidence)` 上探针判据与 `apply_edge_gate` 的结论逐个相等；
**`confidence` 必须在 `{"ok","degraded"}` 两态上都取到**，否则 P0-1 这条漂移测不出来）。

**诚实边界（P2-1，必须进 docstring 与 `disclaimer`）**：探针的分子只取 `atr_bps × atr_target_mult`，
而门禁的分子还会再对两项取 `min`——OCO 止盈距离（`edge_gate.py:117-118`）与信号先验 EV
（`edge_gate.py:123-124`）。两者都只会让分子**更小**、让否决**更多**。
故**探针给出的否决率是一个乐观下界：真实否决率只会 ≥ 探针的读数**。
这个方向是刻意选的（探针宁可说「偏紧」也不该说「不可行」而把用户吓退），但必须写明，
且 G-2 的对拍必须显式固定 `take_profit_pct=None, prior_ev_bps=None` 并在用例注释里写清这一条。
同理 `atr_bps` 用 `close` 算、门禁用 `ref_px`（限价即委托价，`edge_gate.py:93`）——
bps 尺度下差异可忽略，但 docstring 要写明它们是两个价（P2-2）。

`advice` 的生成规则（有序规则链，第一条命中即返回；每条都必须能自证「为什么是这个旋钮」）：

1. `infeasible` 且存在更长周期使 `veto_rate < marginal 阈值` ⇒
   「建议改用 **{bar}** 或更长的时间级别：本标的在 {当前bar} 上 ATR 中位数约 {x} bps，
   而一笔市价往返成本约 {c} bps，最小 edge 门禁要求 ≥ {r} bps。**换预设解决不了**——
   最宽松的「激进」档在同一批 K 线上仍有约 {y}% 的开仓意图会被否决。」
   （更长周期的候选与其否决率由调用方按同一探针对 `BARS` 逐个算，见 3.1.2 的 `alternatives`。）
2. `infeasible` 且所有周期都不可行 ⇒ 「本标的当前波动率整体覆盖不了交易成本，建议换标的或等待波动
   回升；也可在弹窗把纪律预设调到「关闭」自担风险（不推荐）。」
3. `marginal` ⇒ 「约 {v}% 的开仓意图会被最小 edge 门禁否决，属可交易但偏紧；若希望更活跃可改用
   「激进」预设（否决率约降至 {y}%）或改用更长周期。」

**极性**：`TradabilityVerdict` 永远不参与任何交易判定，不改变任何 decision，不进 `degrade`。

#### 3.1.2 取数层：**新模块** `app/ai_auto/tradability_probe.py`（[v2/P1-3] 不放进 `api/ai_auto.py`）

**为什么另起一个模块**：`api/ai_auto.py` 现 **671 行**，而本层要含 REST/缓存双路 + `alternatives` 的
`asyncio.gather` + 逐周期窗口裁剪 + 逐周期成本重算，保守估 100–150 行 ⇒ 直接把它推过 CLAUDE.md 的
**800 行硬线**。下沉还顺带满足本仓库已成文的另一条纪律（`discipline_runtime.py:7-8`）：
**判据在纯核心、取数在运行时层，两者分开**——判据能被纯函数单测逐行钉死，取数只能靠集成测试。
`api/ai_auto.py` 里只留两个路由函数（各 ≤ 15 行）：解析 query → 调本模块 → 装 wire。

取数姿态镜像 `api/market.py:80-107`：先 `rest.get_candles(instId, bar, limit)`；
`OKXError` 时回落 `repo.get_cached_candles`，并在 wire 里如实标 `"source": "live" | "cache"`；
两者皆空 ⇒ `level="unknown"`，**返回 200 而不是 5xx**（探针失败绝不能挡住启动流程）。

> **[v2/P1-4] 有意偏离：缓存回落不受 `settings.allow_cached_fallback` 门控。**
> `config.py:633` 的 `allow_cached_fallback` 默认 **False**，若照抄 `api/market.py:100` 的门控，
> OKX 一不可达探针就只会说 `unknown`——而用户本机 `candles_cache` 里**正躺着**本轮实测用的
> 39764 根 BTC 1m。偏离的理由是这两条路径的风险根本不同源：那个开关要防的是
> **用陈旧价格去下单**，而探针算的是一个**历史窗口统计量**、不驱动任何交易判定、
> 也不参与任何 decision（见 §3.1.1 末的极性声明）。故本模块**无条件**读缓存，代价是必须如实标注：
> wire 上 `source:"cache"` + `asOfTs`（缓存最后一根的 ts），文案写「基于本地缓存的历史 K 线」。
> 这条偏离必须写进实现注释，防止后来者按「与 market.py 保持一致」把它改回去。

ATR 序列复用 `engine.indicators.atr(df, period)` 的**同一函数**（`profit_guard.safe_atr` 的底层，
`session.py:674-675` 与 `backtest.py:418` 都是它的既有消费者），
period 取 `ai_trade_profit_guard_atr_period`（默认 14，`config.py:330`）——全仓 ATR 只允许有这一份公式。
暖机段（前 `period` 行）按 `dropna()` 剔除。

成本读数复用 `CostRuntime.estimate_preview_cost(...)`（`cost_runtime.py:300-317`），分别取
`ord_type="market"` 与 `"limit"`；返回 `None` ⇒ 对应口径整体缺席。**构造与调用的三条硬约束**：

1. **构造签名是 `CostRuntime(inst_id, settings, fee_ledger, creds=None, *, allow_limit=..., expected_hold_bars=...)`**
   （`cost_runtime.py:44-45`）——第一个位置参数是 `inst_id`，**不是 `bar`**（v1 写反了）。
   探针传 `CostRuntime(inst_id, settings, None, None)`。
2. `estimate_preview_cost` 的签名是 `(*, ord_type, side, bar_seconds)`——**`side` 必填**
   （资金费只取不利侧），`bar_seconds` 取 `BAR_SECONDS[bar]`。探针按两侧各算一次取
   `total_bps` 较大者，与 `_preview_side` 的「绝不因方向假设而让成本看起来更便宜」同向。
3. **[P0-1] 必须把 `CostBreakdown.confidence` 原样带进 `TradabilityInputs`。** 这个实例的
   `FundingCache` 从未被 `schedule_maintenance` 轮询过 ⇒ `snapshot()` 恒 `None` ⇒
   `confidence == "degraded"` 在探针路径上**恒成立**（推导链见评审记录 P0-1）。
   丢掉它 = 探针恒定低估 20% 否决率。

`alternatives`：对 `BARS = ("1m","5m","15m","30m","1H","4H","1D")` 中**比当前 bar 更长**的项各跑一次
探针（同一 `instId`，各自取 `min(window_bars, 可得根数)`），回
`{bar, vetoRateMarket, costBpsMarket, level}` 四键。取数成本可控（每个周期一次 200 根请求，
与弹窗打开时的既有请求量同量级），且**必须并发**（`asyncio.gather`）以免弹窗卡顿；
任一周期失败即从 `alternatives` 里省略该项（不填 null 冒充）。

> **[v2/P1-7] 每一项 `alternatives` 必须用它自己的 `bar_seconds` 重算成本，不得沿用当前 bar 的。**
> `funding_periods = max(1, ceil(expected_hold_bars × bar_seconds / interval))`
> （`cost_model.py:143-145`）——`1D × 8 根 = 8 天 ⇒ 24 期资金费`，与 `1m` 的 1 期不是一个数量级。
> 沿用当前 bar 的成本会让长周期看起来比真实**更可行**，而 `advice` 的第一条规则**正是**据此推荐周期的
> ——那就变成了「用一个算错的数把用户劝去另一个周期」。`costBpsMarket` 随每项下发，
> 就是为了让这条能被前端与用例双向核对。

#### 3.1.3 三个落点

1. **启动弹窗**：新增子组件 **`components/Agent/AiTradeTradabilityFields.tsx`**，由
   `AiTradeLaunchModal.tsx` 在「交易纪律」分区下方挂载。
   **[v2/P1-3] 必须是子组件而不是往弹窗里内联**：`AiTradeLaunchModal.tsx` 现 **1542 行**，
   而这一块要含取数 effect + debounce + 四档渲染 + 二次确认 + 一键换周期。同文件 `:77-78` 已经
   import 了 `AiTradeHorizonFields` 与 `ProfitGuardFields`——这是本仓库为同一问题建立的成文范式，
   照抄即可。弹窗自身的净增预算 **≤ 20 行**（一个 import、一次挂载、两个受控 state 的透传）。
   `instId` / `bar` / `preset` 任一变化即 debounce **350ms** 重取。
   - `ok` ⇒ 一行绿色摘要（ATR 中位 / 成本 / 预计否决率）。
   - `marginal` ⇒ 黄色，摘要 + 建议。
   - `infeasible` ⇒ **红色块**，摘要 + 建议 + 一个更长周期的**一键切换**按钮（直接改本弹窗的
     时间级别下拉，不发任何请求）。启动按钮**不禁用**，但按钮文案改为「仍要启动」，
     且需一次显式点击确认（`window.confirm` 不可用——桌面端体验差；用弹窗内联的二次确认行）。
     **[v2/P2-9] 确认态的生命周期**：确认一次后在本次弹窗会话内保持，但只要 `level` 发生
     **由非 `infeasible` 变为 `infeasible`** 的跃迁（用户换了标的/周期/预设又换回来），
     确认态**必须重置**——否则用户在 A 周期上确认过的风险会被静默地套用到 B 周期上。
   - `unknown` ⇒ 灰色，「暂时算不出（行情不可达且无本地缓存）」，**不阻碍任何操作**。
2. **启动回执**：`POST /ai-trade/start` 的响应体在既有 `discipline` 旁新增 `tradability`
   （同一个 `TradabilityVerdict` wire + `asOfTs`）。理由：桌面旧包 / 直接调 API 的用户拿不到弹窗那块，
   回执是他们唯一的告知面。（该端点 `-> dict` 且无 `response_model`（`api/ai_auto.py:292-294`），
   故新键不会被 FastAPI 静默吞掉——**这一点已核实**，不必再加 schema 字段。）
3. **运行期**：会话 `_status_payload` 增 `tradability` 键（启动时算一次，**不做周期性重算**——
   它是一个窗口统计量，分钟级重算只会制造抖动）。前端在 `AiTradePanel` 顶部与统计看板的
   `TradabilityCard` 渲染；`infeasible` 且 `openFunnel.filled == 0` 时是整块面板最显眼的一行。

> **[v2/P1-10] 三个断面的同源关系（必须写死，否则用户会看到两个否决率而不知道信哪个）**
>
> | 断面 | 何时算 | 与其他断面的关系 |
> |---|---|---|
> | 弹窗预检 | 会话**尚不存在**时，按用户当前选的 `instId/bar/preset` | **独立的事前预览**，允许与下面两者不同（时间不同、窗口不同）；带自己的 `asOfTs` |
> | 启动回执 | `manager.start` 内，逐成员各算一次 | **与会话 status 是同一份对象**（同一个 `asOfTs`），不是重算 |
> | 会话 `status.tradability` | 即启动回执那一份，挂 `session._tradability` 只读快照 | 启动后**不变**；落库（§六.2）落的也是它 |
>
> 前端在两个数字并存时（弹窗 vs 面板）**必须各自显示 `asOfTs`**，并在 `TradabilityCard` 上写一句
> 「这是本会话启动时的断面」。分组级**不做**任何合并（R-10）：AI-trade 没有分组级实时通道
> （`group_status()` 只在挂载拉一次），做一个「平均否决率」既没有操作含义、也没有可信的下发通道。

### 3.2 轴 B —— 成本感知的止损下界

#### 3.2.1 纯核心改动（`sizing.py`）

`StopRule` 增字段 `cost_floor_mult: float`（`parse_stop_rule` 从
`settings.ai_trade_stop_cost_floor_mult` 读，默认 2.0，`0` ⇒ 逐字回退今日行为）。

`resolve_stop_distance` 增**一个** keyword-only 形参 `cost_bps: float | None = None`
（`max_sl_pct` / `max_tp_pct` 是既有形参，v1 误记为「两个新形参」，[P1-5] 已更正），
返回类型由 `StopPlan | None` 改为 **`tuple[StopPlan | None, str | None]`**（`(plan, skip_reason)`，
理由见 §3.2.1a），并把第 311-316 行改为**下界 → 上界 → 冲突判定**三段（顺序不可交换）：

```python
stop_pct = rule.stop_atr_mult * atr / px
floor_pct = None
floored = False                 # ← [P1-5] v1 漏了这行初始化，下面无条件读它会 NameError
if rule.cost_floor_mult > 0 and _finite(cost_bps) and cost_bps > 0:
    floor_pct = rule.cost_floor_mult * cost_bps / 1e4
    if floor_pct > stop_pct:
        stop_pct, floored = floor_pct, True
if sl_cap is not None:
    if floor_pct is not None and floor_pct > sl_cap:
        return None, "floor_above_cap"    # ← 下界与用户可见红线冲突（见下）
    stop_pct = min(stop_pct, sl_cap)
take_pct = rule.stop_rr * stop_pct
...
source = "atr_floored" if floored else "atr"
return StopPlan(..., source=source), None
```

**为什么冲突时不落 `StopPlan` 而是回退**：`sl_cap`（`ai_trade_max_sl_pct`，默认 0.20）是用户
可见红线；若成本下界比红线还大，说明这个标的/周期的成本结构已经彻底不可行，此时挂一个「刚好等于
红线」的止损是**假装可行**。此时 `plan is None` ⇒ `apply_risk_override` 一行都不动 ⇒ 逐字回退固定 pct。

#### 3.2.1a [v2/P0-2] 为什么必须回 `(plan, skip_reason)` 而不能只回 `None`

v1 说「返回 `None` ⇒ `stopSource` 记为新态 `floor_above_cap`」。**在当前代码上这个状态产生不出来。**
`sizing_runtime._apply_stop`（`:250-262`）对 `plan is None` 的两条路径都只能得到 `"fixed"`：

- `executor is None`（回测）⇒ `source = "atr" if plan is not None else "fixed"`（`:253`）；
- `executor` 非空（实盘）⇒ `apply_risk_override(executor, None, action)` 在 `sizing_runtime.py:94-95`
  第一行就 `return "fixed"`。

裸 `None` 把「为什么没覆盖」这个信息**擦掉了**，调用方无从区分「ATR 不可用」与「下界高于红线」。
连带作废的是：`stopSource` 的第七态、`counters.floor_above_cap_rounds`、
`aiStats.stopFloored.floorAboveCapRounds`、以及 G-6 的第三分支——**全部恒不可达**。
这是本仓库成文缺陷模式「后端算了但没有产出通道」的镜像版本（*有读点、没写点*）。

**修法**（最小且不破坏既有不变式）：

```python
# sizing.py
def resolve_stop_distance(...) -> tuple[StopPlan | None, str | None]: ...
    # 不可用（rule 缺席 / ATR 无效 / px 无效）⇒ (None, None)         ← 与今天语义逐字相同
    # 下界高于红线                              ⇒ (None, "floor_above_cap")

# sizing_runtime.py
plan, skip = resolve_stop_distance(atr_px=ctx.atr_px, ref_px=ctx.ref_px,
                                   rule=self.stop_rule, cost_bps=ctx.cost_bps)
stop_source = self._apply_stop(executor, plan, decision.action, skip_reason=skip)
```

`_apply_stop` 里：先照旧算 `source`，**若 `source == "fixed"` 且 `skip_reason` 非空则取 `skip_reason`**。
`apply_risk_override` **一行都不动**——「plan 为 None 就不碰 `executor.risk`」这条不变式必须原样保留
（它守的是「会话展示口径与交易所侧现存 OCO 一致」，`sizing_runtime.py:90-92` 有成文论证）。
所有既有调用点须同步改成解元组：本轮**必须 grep 全仓 `resolve_stop_distance(` 的每一个调用点与
每一条用例**，漏一处就是 `tuple` 被当成 `StopPlan` 用，报错点会离现场很远。

`StopPlan.source` 由 `"atr"` 扩为 `"atr" | "atr_floored"`；`_apply_stop` 的 `stopSource` 五态扩为
**七态**：`atr | atr_floored | fixed | floor_above_cap |
skipped_resting | skipped_position_live | skipped_reversal`。
`counters.atr_stop_rounds` 对 `atr` 与 `atr_floored` **都要 +1**（它度量的是「ATR 覆盖到了多少开仓」，
`discipline_runtime.py:132` 是它唯一的读点），
另增 `counters.stop_floored_rounds` 与 `counters.floor_above_cap_rounds` 两个计数。

#### 3.2.2 取数：零新增

`sizing_runtime.resolve`（第 239 行）已经在同一作用域里持有 `ctx.cost_bps`——实盘由
`session.py:1118` 的 `self._cost.estimate_cost_only(...)` 传入。因此改动是**一行**：

```python
plan, skip = resolve_stop_distance(atr_px=ctx.atr_px, ref_px=ctx.ref_px, rule=self.stop_rule,
                                   cost_bps=ctx.cost_bps)
```

（`sizing_runtime.py:239` 这一行同时服务实盘 `resolve_live` 与回测 `resolve`——**它是双通道共用的
纯核心入口**。本仓库有过「双通道共用纯核心时只有一侧被实现」的阻断级事故，故轴 B 的回测侧
不是可选项，见 §3.2.3。）

#### 3.2.3 回测侧必须同步（与 D-8 合并处理）

`backtest.py:835-839` 的 `SizingContext` 补两键：

- `cost_bps=` 用**回测世界的常数**：`estimate_round_trip_cost(query_for_preview(ord_type="market",
  side=..., bar_seconds=BAR_SECONDS[self.bar]), backtest_params(self.settings,
  expected_hold_bars=...)).total_bps`。`backtest_params`（`cost_model.py:108`）已存在，
  **严禁**在回测里用实盘的 `resolve_cost_params`（两个世界的常数必须各自演进）。
- `p_win=`：**[v2/P1-1] 本轮不动，如实记为欠账。** 回测侧没有 `PriorRuntime`（先验快照由
  `manager._resolve_prior_snapshots` 在实盘启动流程里装配），只能传 `None`；而
  `kelly_fraction` 的判据是**有序**的——`sizing.py:278-280` 先判 `p is None ⇒ ("no_prior")`，
  `cost_bps` 的检查在 `sizing.py:281`、**短路在后**。所以补 `cost_bps` 之后回测里凯利项
  **依然恒不参与 `min`**，D-8 标题所指的不同构在轴 B 之外一字未改。给回测引入一条先验装配链路
  是一次独立特性，混进本轮会让回归面失控。

**[v2/P1-1] 实现节点必须知道的两个反直觉后果**（都要写进实现注释）：

1. **补 `cost_bps` 对回测的「仓位」是零行为变化**——它在 `no_prior` 短路之后才会被读到。
   唯一的行为变化来自轴 B 的止损下界。**若你期待仓位数字变化、结果没变，那不是没接上，那是对的。**
2. **补 `cost_bps` 是轴 B 在回测侧生效的必要条件**：不补，`ctx.cost_bps` 恒 `None` ⇒
   `resolve_stop_distance` 的下界分支恒不进 ⇒ 回测与实盘的止损位系统性分叉。
   这正是本仓库「双通道共用纯核心时只有一侧被实现」的成文缺陷模式，**这一条不是可选项**。

**[v2/P1-2] 默认开启会改写既有回测数值，必须分两步验证。**
`ai_trade_stop_cost_floor_mult` 默认 `2.0`，配合本节的 `cost_bps` ⇒ 所有既有 AI 回测的
`_oco_risk`（`backtest.py:840-843`）会被改写 ⇒ 依赖回测数值的既有用例可能整片翻红。
G-7 只冻结了 `mult=0` 那一支，盖不住默认路径。故验证顺序固定为：

1. 先把 `ai_trade_stop_cost_floor_mult` 置 `0` 跑通**全部**既有 AI 回测用例——证明改动本身零回归
   （这一步就是 G-7 与新增的 G-7b）；
2. 再用默认值 `2.0` 跑一次，**逐条列出**翻红用例，对每一条判定「是期望的口径变更（改断言）」
   还是「是缺陷（改代码）」，并把判定写进实施记录。**严禁**用「批量更新快照」一笔带过。

### 3.3 轴 C —— 开仓漏斗（Open Funnel）

#### 3.3.1 新纯核心 `app/ai_auto/open_funnel.py`（≤ 260 行）

```python
# 决策链顺序（与 session._run_decision_round 的调用序逐字同构；顺序即漏斗层序）
LAYERS = ("baseRuleMaBand", "horizon", "profitCooldown", "turnover", "exchangeBackstop",
          "drawdownBrake", "consensus", "confidence", "edgeGate", "reflection", "other")

_DEGRADE_PREFIXES: tuple[tuple[str, str], ...] = (...)   # 前缀 → 层 key，单点归类

@dataclass
class OpenFunnelCounters:
    intents: int = 0          # 第一阶段动作 ∈ open_* 且 degrade is None（模型的真实开仓意愿）
    blocked: dict[str, int]   # 首个拦截层 → 次数（**只统计开仓意愿轮**，见下）
    llm_degraded: int = 0     # 第一阶段因 **LLM 故障**（llm_error / timeout / 空产出）降级
    submitted: int = 0        # 真的发出了开仓委托
    filled: int = 0           # 真的成交了开仓腿
    sizing_zeroed: int = 0    # [v2/P0-4] 过了全部门禁但仓位解到 0/None（不是 degrade，另一条通道）
    _intent_pending: bool = False   # 轮级标志：本轮记过 intent 吗（note_intent 置位，收口消费）

def note_intent(self, action: str, degrade: str | None) -> None: ...
def note_block(self, degrade: str | None) -> None: ...       # 非开仓意愿轮 ⇒ 直接 return
def classify_block(degrade: str | None) -> str | None: ...
def funnel_wire(c: OpenFunnelCounters) -> dict: ...
def top_blocker(c: OpenFunnelCounters) -> tuple[str, int] | None: ...
def funnel_advice(c, verdict_level: str | None) -> str | None: ...
```

**分母的唯一落点**：`session.py:726` 的 `_update_hold_streak` 旁边（第一阶段决策的既有收口，
`decide` 返回后、任何门禁之前）加一行 `self._funnel.note_intent(decision.action, degrade)`。
选这个落点的理由与 `HorizonRuntime.note_intent`（`horizon_runtime.py:197`，签名
`(stage1_action, stage1_degrade, *, …)` ——**核实无误**）完全一致：**门禁后的动作已被洗成
hold，在那里数分母永远是 0**。`note_intent` 同时**置位轮级标志** `_intent_pending`
（`action ∈ open_* 且 degrade is None` 时为真，否则为假）。

**首拦截层归类**：沿用第 1 轮 `discipline_runtime._DEGRADE_PREFIXES` 的成文范式（在决策轮的唯一收口
按前缀归类，而不是在 11 个纯核心里各加一个计数器），把三条前缀扩成完整的一张表。归类点在
`session.py:788` 的 `self._discipline.note_round(degrade)` 旁——那里的 `degrade` 已是
`degrade or gate_reason` 的归并结果，是「第一个拦下它的层」。

> **[v2/P0-3 阻断级修正] 归类必须以「本轮记过 intent」为前提，不能只看 `degrade`。**
>
> v1 的论证是「后续层看到的已是 hold，所以第 788 行的 `degrade` 天然就是首拦截层」。
> 前半句对，**后半句不成立**：持仓周期铁律层的作用域是**平仓与反手**，不是开仓——
> `hold_horizon.gated_kind`（`:558-573`）第一条判据就是
> `if action == "close" and position_side in ("long","short"): return "close"`。
> 一轮 `close` 意图被 `horizon_min_hold` 拦下时 `degrade` 非空、而 `intents` **没有 +1**，
> 归类却会给 `blocked["horizon"] += 1`。于是 `sum(blocked)` 可以超过 `intents`，
> 而「意愿 → 各层拦截 → 委托 → 成交」这张图的**全部**价值就是它闭合。
> `exchangeBackstop`（`backstop_gate`）同源同病。
>
> **这不是「按层名把 horizon / backstop 排除」能解决的**：对照组显示
> `base_rules.py:211` / `drawdown_brake.py:196` / `profit_cooldown.py:74` / `edge_gate.py:144` /
> `sizing.py:332` 的动作白名单都是 `("open_long","open_short")`，只有 horizon / backstop 越界；
> 而**反手**（`open_long` 遇空头持仓，`gated_kind` 归 `"reverse"`）被 horizon 拦下时
> **确实是一次开仓意图、应当计入**。判据只能来自「本轮记没记过 intent」这个事实。
>
> 落地：`note_block(degrade)` 第一行 `if not self._intent_pending: return`；
> 消费后**必须清零** `_intent_pending`（在 `note_block` / `note_submitted` /
> 轮末 `finally` 三处任一先到者清零，防止跨轮泄漏）。
> 不变式做成用例（**G-10b**）：任意事件序列下恒有
> `sum(blocked.values()) + submitted + sizing_zeroed <= intents`。

> **[v2/P0-4] 两处口径修正。**
>
> **(a) `sizingZero` 从 `LAYERS` 里删掉，改由独立计数 `sizing_zeroed` 承载。**
> `_apply_sizing`（`session.py:763` 调用、`:1112-1122` 定义）**不返回 `degrade`**——
> `degrade` 在第 788 行之前最后一次被赋值是反思层（`session.py:761`）。仓位解到 0 在全仓
> 不产生任何 degrade 码，`classify_block` 永远归不到它，把它留在 `LAYERS` 里就是一条
> **永远点不亮的层**（本仓库成文的「死枚举」形状）。改法：`_apply_sizing` 返回后由 session
> 判一次 `decision.action ∈ open_* and (size_fraction is None or == 0)` ⇒
> `self._funnel.note_sizing_zeroed()`。它在漏斗图上画在「委托」之前的最后一格，
> **不进 `blocked`**（它不是一道闸，是求解结果）。
> `LAYERS` 末位改为 `"other"`，承接未知前缀（G-11 要求归 `other` 而非静默丢弃）。
>
> **(b) `low_confidence` 只能被计一次，且它不属于 `llmDegraded`。**
> v1 把 `low_confidence` 算进 `llm_degraded`，而它同时会在第 788 行被
> `_DEGRADE_PREFIXES` 的 `("low_confidence", "confidence")`（`discipline_runtime.py:25`）
> 归进 `blocked["confidence"]`——**同一轮在漏斗的两个格子里各出现一次**。
> 而且它本就不是 LLM 故障：`config.py:157` 明确把 `low_confidence` 与「合法动作 / 刻意 hold」
> 并列为 genuine 决策、不计入重试。
> 故 `llm_degraded` 的口径收窄为**仅** LLM 故障类（`llm_error` / `timeout` / 空产出），
> `low_confidence` 的**唯一**落点是 `blocked["confidence"]`。
> 口径写进 wire 的字段注释与卡片文案（`llmDegraded` 的中文标签用「模型调用失败」而不是「降级」）。

**`submitted` / `filled`**：`submitted` 取 `_execute_decision` 返回的 `executed`
（`session.py:1163`，`action ∈ open_* 且 skipped 为假`）；`filled` 取 `_dispatch` 里
`side ∈ ("long","short")` 的 trade 事件——与 `discipline_runtime.hint` 数开仓成交的既有口径
（`discipline_runtime.py:66`）**共用同一判据**，不得另写。

#### 3.3.2 `strictness_hint` 升级为对症建议

`discipline_view.strictness_hint`（第 84-99 行）改签名为
**`strictness_hint(rows: list[dict], facts: StrictnessFacts) -> str | None`**（两参）。

> **[v2/P1-8] 为什么是聚合结构体而不是五个 keyword-only 形参。**
> v1 的 `strictness_hint(rows, *, funnel, tradability_level, rounds_completed, opens)` 恰好 5 个形参，
> 顶着 CLAUDE.md「函数形参不超过 5 个」的红线；而**同一个文件**的 `DisciplineFacts`
> （`discipline_view.py:41`）docstring 里已经白纸黑字写了这条规则和它的解法
> （「13 行 digest 的全部入参（聚合结构体，遵守 CLAUDE.md『函数形参 ≤ 5』）」）。
> 在同一文件里另立一套散装形参，就是本仓库反复记录的「制造新孤岛」。
> `StrictnessFacts` 与 `DisciplineFacts` 同文件、同 `@dataclass(frozen=True)` 范式，
> 字段 = `{funnel: dict | None, tradability_level: str | None, rounds_completed: int, opens: int}`，
> 由 `discipline_runtime.hint()` 单点装配（取数面仍只在那一个函数里）。

规则链（有序，第一条命中即返回）：

1. `opens > 0` 或 `rounds_completed < 8` ⇒ `None`（既有行为不变）。
2. `tradability_level == "infeasible"` ⇒ **首要建议是换周期**，文案里明确写「换预设解决不了」
   并带上探针给的数字。（这条必须排在第 3 条之前——D-1 的实测表就是它的依据。）
3. `funnel.intents == 0` ⇒ 「模型 {N} 轮里一次都没提出开仓」——这是**模型侧**问题而非纪律侧，
   建议检查策略信号板 / 提示词，**不要**去放宽纪律（放宽一个从未触发的闸没有任何作用）。
4. `top_blocker` 存在 ⇒ 「{N} 次开仓意图里 {M} 次被「{层中文名}」拦下」+ 该层专属的对症建议。
5. 兜底 ⇒ 保留今日那句「{n} 条纪律可能过严……」（作为最后一档，不删）。

#### 3.3.3 呈现

- `status.openFunnel` 新键（wire 见 §五）；
- 统计看板新增 `OpenFunnelCard.tsx`：一条横向漏斗（意愿 → 各层拦截 → 委托 → 成交）+ 逐层数字与占比，
  零意愿 / 零轮数时显式写「样本不足，不下结论」（**绝不**把 0/0 画成 0%）；
- `aiStats.openFunnel` 落库（§六），使历史会话可回答「上次为什么没开单」。

### 3.4 轴 D —— 决策记忆完整性

#### 3.4.1 新纯核心 `app/ai_auto/summary_health.py`（≤ 120 行）

```python
@dataclass
class SummaryHealth:
    failures: int = 0            # 连续失败次数（成功即清零）
    total_failures: int = 0      # 全程累计（诊断用，不清零）
    last_error: str | None = None
    backoff_rounds: int = 0      # 本次成功前还要跳过多少个到期点

def describe_exc(exc: BaseException) -> str:
    """异常 → 非空诊断串。`str(exc)` 为空（TimeoutError 恒如此）⇒ 回落 `类名`，
    超时再补上超时秒数：`TimeoutError(30.0s)`。D-4 的唯一修点。"""

def next_backoff(failures: int, refresh_rounds: int, cap: int = 8) -> int:
    """指数退避的轮数：min(2**(failures-1), cap) × refresh_rounds。"""

def is_stalled(h: SummaryHealth, max_failures: int) -> bool: ...
def health_wire(h, *, rounds_since_summary: int, refresh_rounds: int) -> dict: ...
```

#### 3.4.2 `decider.py` 四修

1. **journal 有界**（D-3）：失败分支改为
   `self._journal = self._journal[-(self.context_refresh_rounds * settings.ai_trade_summary_journal_cap_mult):]`
   （默认 mult=2 ⇒ 保留最近 20 条）。**保留最近而非清空**：素材还有用，只是不能无界。
2. **退避**（D-3 的第二半）：失败后 `self._summary_skip_rounds = next_backoff(...)`；
   `commit_round` 的到期判定改为
   `self._rounds_since_summary >= self.context_refresh_rounds + self._summary_skip_rounds`。
   成功即 `failures=0, skip=0`。这样连续失败时不会每 10 轮就白烧一次 30s 超时。

#### 3.4.2a [v2/P1-11] 到期判定的**唯一最终形态**（§3.4.2 第 2 条与 §3.4.3 改的是同一个 `if`）

`decider.py:547-548` 今天是：

```python
if self._rounds_since_summary >= self.context_refresh_rounds:
    return await self.summarize()
```

§3.4.2 第 2 条（退避）与 §3.4.3（`defer_summary`）都要改这一处。v1 把它们分列两节、没有给出合并后的
形态，而**两处都错的表现是静默的**（要么摘要再也不触发，要么退避恒不生效，两种都不会报错）。
合并后**只允许存在这一份**：

```python
due = self._rounds_since_summary >= (self.context_refresh_rounds + self._summary_skip_rounds)
if not due:
    return None
if defer_summary:
    self._summary_due = True      # 只记账，真正的 LLM 调用挪到 flush_summary（D-5）
    return None
return await self.summarize()     # 回测 / 单测的逐字回退路径（defer_summary 默认 False）
```

`summarize()` 内部**不再**自己判到期；`flush_summary()` 的第一行是
`if not self._summary_due: return None`，随后 `self._summary_due = False` 再 `await self.summarize()`。
`_summary_due` 必须在 `summarize()` 的**成功与失败两条分支都已归位之后**才复位，
否则一次失败会让下一轮误以为还欠着一次摘要而立刻重试——那正是 D-3 螺旋的另一种写法。
3. **诊断串**（D-4）：`logger.warning("ai-trade summarize failed: %s", describe_exc(exc))`。
4. **独立超时**：新 `ai_trade_summary_timeout_s`（默认 **30.0**）取代复用决策超时——摘要是背景任务，
   不该占用决策的 60s 预算。

#### 3.4.3 摘要不再阻塞决策事件（D-5）

`commit_round` 增 keyword-only 形参 `defer_summary: bool = False`（默认 False ⇒ 回测/单测**逐字回退**）：
为真时只记账、置 `self._summary_due = True` 并返回 `None`。新增
`async def flush_summary(self) -> str | None`：`_summary_due` 为假 ⇒ 直接 `None`。

`session.py` 侧：第 771 行传 `defer_summary=True`；把摘要的 await 挪到
`_emit_decision` / `_emit_degraded` **之后**、`_post_round` 之前：

```python
summary = await self.decider.flush_summary()
if summary is not None:
    await self._emit_summary_trace(round_no, ts, summary)
    await self._emit_summary(round_no, ts, summary)
```

**为什么不是 `create_task` 甩到后台**：`_deciding` 标志在 `finally` 里清零，后台摘要会与下一轮
`decide` 并发写同一个 `_context` / `_journal`——那是一个真实的数据竞争。保持串行、只调整顺序，
是这里唯一安全的改法。（这条必须写进实现注释，防止下一个人"顺手优化"。）

#### 3.4.4 呈现（D-6）

- `status.summaryHealth`（§五）；
- 轨迹新增 `phase="summary_failed"`（复用既有 `trace.py` 的合并器，与 `retry` 面包屑同款）；
- `AiTradePanel` 在 `stalled` 为真时挂一枚醒目徽标「长期记忆已停摆（连续 N 次摘要失败）」，
  `DisciplineCard` 下方同款一行。文案必须说明**后果**：「模型仍在按当前行情决策，但会逐渐忘记
  更早的执行历程」——只报「失败」不报后果，用户无法判断要不要停。

> **[v2/P1-9] 两个装配点各自的定位（不留悬空）。**
> §十.7 要求 `_status_payload`（`session.py:1610`）与 `info()`（`:1666`）**都**挂 `summaryHealth`
> ——这条判断是对的（同一读数两处装配、只改一处是本仓库的成文缺陷模式）。但两者的**义务不同**，
> 必须写死，否则 `info()` 那一份会变成第二块死数据：
>
> | 装配点 | 载体 | 消费者 | 渲染义务 |
> |---|---|---|---|
> | `_status_payload`（`:1610`） | WS `status` 事件 + `snapshot()` | `AiTradePanel` / `DisciplineCard` | **有**：本轮必须落 `stalled` 徽标（G-17 守门） |
> | `info()`（`:1666`） | `manager.group_status()` → `GET /ai-trade/status` 只读快照 | 挂载时的一次性状态拉取、以及落库 | **无**：本轮明确**不要求**新渲染点；挂它是为了「同一读数两处装配」的一致性，以及让排障时从任一路径都读得到停摆事实 |
>
> 这张表要照抄进实现注释。**不写清楚的后果**是下一轮有人发现 `info()` 里有个没人渲染的键，
> 把它当死码删掉，然后「从 `info()` 拿状态的那条路径看不到停摆」这个坑第二次复发。

### 3.5 轴 E —— 校准闭环（偿还 G-17 欠账，不依赖 LLM）

第 1 轮已把 `disciplinePreset` / `disciplineArmedKeys` 落进 `metricsJson.aiStats`。本轮补两件：

1. **本轮新增落库键**（§六）使历史可比：`openFunnel`、`tradability`（启动断面）、
   `stopFloored`（轴 B 的两个计数）。
2. **历史预设对照卡**：新端点 `GET /ai-trade/history/preset-comparison`，
   按 `aiStats.disciplinePreset` 分组聚合历史 run 的
   `netPnlUsd / roundTrips / winRate / edgeGate.vetoRate / openFunnel.conversion`，
   每组带 `samples`；`samples < 3` ⇒ 该组所有指标照常给出但 `conclusive=false`，
   前端显式写「样本不足，不下结论」。

**为什么不做「两臂 LLM 回测对照」**：第 1 轮实测 50 分钟跑不完两臂（约 30 轮/臂），
且 D-8 证明回测的仓位求解与实盘不同构。**[v2/P1-1] 注意这条论证的前提在本轮之后依然成立**：
本轮只把 `cost_bps` 补齐（服务轴 B 的止损下界），`p_win` 的不同构**不修**（理由见 §3.2.3），
故「回测里凯利项恒不参与 `min`、仓位系统性大于实盘」这件事**本轮结束后仍然为真**——
正因为如此，才不能用回测去校准实盘阈值。（v1 把这条写成了「已修好所以不需要」，是反的；
结论一样，前提必须说实话。）
相比之下，轴 A 的确定性探针**秒级**就能回答
「这组阈值在这个周期上会拦掉多少」，历史对照卡则用**真实资金的历史**回答「哪一档更赚」。
两者合起来比一次 50 分钟的 LLM 回测更可信，也可反复复算。

**文案纪律照旧**：对照卡与探针的任何文案都不得出现「已校准 / 经回测验证」；探针文案必须写明
「这是按最近 {N} 根真实 K 线与当前成本常数算出的确定性统计，不是收益预测」。

---

## 四、文件 / 模块变更计划

### 后端（`services/quant`）

| 文件 | 新建/修改 | 一句话意图 |
|---|---|---|
| `app/ai_auto/tradability.py` | 新建 | 可交易性判据纯核心 + 中文文案 + 对症建议规则链（轴 A，≤ 300 行） |
| `app/ai_auto/tradability_probe.py` | **新建**（[v2/P1-3]） | 轴 A 取数层：REST/缓存双路、ATR 序列、逐周期成本、`alternatives` 并发（≤ 200 行）。**不放进 `api/ai_auto.py`**（它已 671 行） |
| `app/ai_auto/open_funnel.py` | 新建 | 开仓漏斗计数器、层前缀归类表、wire、对症建议（轴 C，≤ 260 行） |
| `app/ai_auto/summary_health.py` | 新建 | 摘要健康度状态机、`describe_exc`、指数退避（轴 D，≤ 120 行） |
| `app/config.py` | 修改 | 新增 8 个 `ai_trade_*` 字段（§六.1）；现 **740** 行，净增预算 **≤ 40 行**（[v2/P2-3]）；**混合文件**，见 §十 |
| `app/schemas.py` | 修改 | `AiTradeTradabilityOut` / `AiTradePresetComparisonOut`；status 三新键的类型 |
| `app/api/ai_auto.py` | 修改 | 两个路由函数（各 ≤ 15 行，只解析 query → 调 `tradability_probe` → 装 wire）+ `start` 回执增 `tradability`；净增预算 **≤ 50 行**（[v2/P1-3]） |
| `app/ai_auto/decider.py` | 修改 | journal 有界 + 退避 + 独立超时 + `describe_exc` + `defer_summary`/`flush_summary`（D-3~D-5） |
| `app/ai_auto/session.py` | 修改 | 薄封装：`_funnel` 三个记点、`tradability` 落 status、摘要顺序调整（**净增预算 ≤ 22 行**，见 §八 R-6） |
| `app/ai_auto/discipline_runtime.py` | 修改 | `facts()` 增 `funnel` / `tradability_level`；`hint()` 转发新形参 |
| `app/ai_auto/discipline_view.py` | 修改 | `strictness_hint` 升级为五档有序规则链 + 新 `StrictnessFacts` 聚合入参（[v2/P1-8]）；现 304 行 |
| `app/ai_auto/sizing.py` | 修改 | `StopRule.cost_floor_mult`；`resolve_stop_distance` 下界三段式 + 新 `source` + **返回 `(plan, skip_reason)` 二元组**（[v2/P0-2]） |
| `app/ai_auto/sizing_runtime.py` | 修改 | `resolve` 把 `ctx.cost_bps` 传下去、解元组；`_apply_stop` 收 `skip_reason` ⇒ 七态 + 三个新计数 |
| `app/ai_auto/backtest.py` | 修改 | `SizingContext` 补 `cost_bps`（回测世界常数）与 `p_win`（D-8） |
| `app/ai_auto/manager.py` | 修改 | `start` 时算一次 tradability，塞进回执与会话（与 `discipline` 同落点） |
| `app/ai_auto/stats_ext.py` | 修改 | `aiStats` 合并 `openFunnel` / `tradability` / `stopFloored` |
| `app/ai_auto/cost_view.py` | 修改 | 成本卡增一行「止损下界是否绑定」（轴 B 的呈现落点之一） |
| `app/db/repositories.py` | 修改 | `list_ai_trade_preset_comparison(session)` 聚合查询（只读 `metrics_json`，零迁移） |

### 前端（`apps/web`）

| 文件 | 新建/修改 | 一句话意图 |
|---|---|---|
| `lib/aiTradeTradability.ts` | 新建 | 只读渲染助手（**绝不重算判据**，只读后端下发的 level/headline/advice） |
| `lib/aiTradeFunnel.ts` | 新建 | 漏斗条的几何计算 + 层中文名映射（层名由后端下发，前端只做布局） |
| `lib/aiTradeTypes.ts` | 修改 | 三个新 status 键 + 两个新响应体的类型；`stopSource` 联合扩为七态 |
| `lib/aiTradeHorizonApi.ts` | 修改 | 新增 `aiTradeTradabilityApi.probe(instId, bar, preset)`（与既有两个只读客户端同文件同纪律） |
| `lib/aiTradeHistoryApi.ts` | 修改 | 新增 `presetComparison()` |
| `lib/aiTrade.ts` | 修改 | `STOP_SOURCE_LABEL` 增 `atr_floored` / `floor_above_cap` 两态中文 |
| `components/Agent/AiTradeTradabilityFields.tsx` | **新建**（[v2/P1-3]） | 「可交易性预检」整块：取数 effect + debounce 350ms + 四档渲染 + 二次确认 + 一键换周期。与同目录 `AiTradeHorizonFields` / `ProfitGuardFields` 同款范式 |
| `components/Agent/AiTradeLaunchModal.tsx` | 修改 | **仅**挂载上面的子组件并透传受控 state；现 **1542** 行，净增预算 **≤ 20 行**（[v2/P1-3]） |
| `components/Agent/aiTradeStats/TradabilityCard.tsx` | 新建 | 运行期探针卡 |
| `components/Agent/aiTradeStats/OpenFunnelCard.tsx` | 新建 | 开仓漏斗卡 |
| `components/Agent/aiTradeStats/PresetComparisonCard.tsx` | 新建 | 历史预设对照卡（轴 E） |
| `components/Agent/aiTradeStats/AiTradeStatsLive.tsx` | 修改 | 挂载两张新卡 |
| `components/Agent/aiTradeStats/AiTradeStatsHistoryList.tsx` | 修改 | 挂载对照卡 |
| `components/Agent/aiTradeStats/DisciplineCard.tsx` | 修改 | `hint` 渲染改为结构化（主句 + 建议 + 数字），保留 `data-testid` |
| `components/Chart/AiTradePanel.tsx` | 修改 | `infeasible` 红条 + 「长期记忆已停摆」徽标 |

### 测试

| 文件 | 新建/修改 | 覆盖 |
|---|---|---|
| `tests/test_ai_trade_tradability.py` | 新建 | 纯核心判据、四档 level、与 `apply_edge_gate` 的同构对拍（G-2）、建议规则链 |
| `tests/test_ai_trade_tradability_api.py` | 新建 | 端点 200/422、缓存回落、取数失败 ⇒ `unknown` 且仍 200、`alternatives` 并发 |
| `tests/test_ai_trade_open_funnel.py` | 新建 | 分母口径、`LAYERS` 11 项（含兜底 `other`、**已移除 `sizingZero`**）逐条归类、`top_blocker`、零样本不造百分比、闭合不变式 G-10b |
| `tests/test_ai_trade_funnel_session.py` | 新建 | 会话级：三个记点、被拦轮不进 `filled`、`status.openFunnel` 形状 |
| `tests/test_ai_trade_stop_floor.py` | 新建 | 下界绑定 / 未绑定 / 与 `sl_cap` 冲突三分支；`stopSource` 七态 |
| `tests/test_ai_trade_summary_health.py` | 新建 | journal 有界、退避轮数、`describe_exc` 对 `TimeoutError` 非空、`stalled` |
| `tests/test_ai_trade_summary_session.py` | 新建 | `defer_summary` 后决策事件先于摘要事件推出（D-5 的回归门禁） |
| `tests/test_ai_trade_preset_comparison.py` | 新建 | 聚合分组、`samples<3 ⇒ conclusive=false` |
| `tests/test_ai_trade_sizing_backtest.py` | 修改 | 回测 `SizingContext` 现在带 `cost_bps`；下界在回测同样生效（D-8） |
| `tests/test_ai_trade_discipline.py` | 修改 | `strictness_hint` 五档规则链 |
| `tests/test_ai_trade_api.py` | 修改 | `start` 回执含 `tradability` |
| `tests/test_ai_trade_tradability_probe.py` | **新建**（[v2/P1-3]） | 取数层：缓存无条件回落、逐周期成本重算（`alternatives` 各项 `costBpsMarket` 不相等）、并发不递归 |
| `apps/web/lib/aiTradeTradability.test.ts` | 新建 | 只读断言：前端不重算 level（给一个与数字矛盾的 level，渲染必须服从后端） |
| `apps/web/lib/aiTradeFunnel.test.ts` | 新建 | 漏斗几何、零样本不画 0%、闭合不变式 |
| `apps/web/lib/aiTradeSizing.test.ts` | **修改**（[v2/P2-7]） | `stopSource` 由五态扩到七态：`:128-132` 是按数组遍历 source 的，不同步更新即翻红（R-5 已预告，v1 的变更表漏列） |
| `apps/web/components/Agent/AiTradeTradabilityFields.test.tsx` | **新建**（[v2/P1-3]） | 四档渲染、`infeasible` 二次确认与确认态重置（P2-9）、切 bar 重取（debounce 350ms） |
| `apps/web/components/Agent/AiTradeLaunchModal.test.tsx` | 修改 | 只断言「子组件被挂载且受控 state 正确透传」，具体渲染断言归上一行 |
| `apps/web/components/Agent/aiTradeStats/OpenFunnelCard.test.tsx` | 新建 | 渲染门控：新键缺席不崩、样本不足文案 |

---

## 五、接口设计

### 5.1 `GET /api/py/ai-trade/tradability`

**不守 AI 总闸**（沿用 `/ai-trade/horizon-profiles`、`/ai-trade/discipline-presets` 的成文先例：
读一张表不下任何单）。

Query：`instId`（必填）、`bar`（必填，须在 `BAR_SECONDS` 键集内，否则 422）、
`preset`（可选，缺省取 `ai_trade_discipline_default_preset`；未知值按 `off` 处理）。

200 响应：

```jsonc
{
  "instId": "BTC-USDT-SWAP", "bar": "1m", "preset": "balanced",
  "level": "infeasible",              // ok | marginal | infeasible | unknown
  "samples": 200, "source": "live",   // live | cache
  "asOfTs": 1786600000,
  "atrBps":   { "p10": 2.9,  "p50": 5.6,  "p90": 11.3 },
  "costBps":  { "market": 13.0, "limit": 8.5 },
  // [v2/P0-1] required = k_min × cost × degradedMultApplied，与 edge_gate.py:148 同构。
  // 两个 confidence 原样来自 CostBreakdown.confidence，供前端解释「阈值为什么是这个数」。
  "costConfidence": { "market": "degraded", "limit": "degraded" },
  "degradedMultApplied": 1.2,
  "requiredBps": { "market": 23.4, "limit": 15.3 },
  "vetoRate": { "market": 0.986, "limit": 0.926 },
  "stopBps":  { "p50": 11.2 },
  "stopInsideCostRate": { "market": 0.623, "limit": 0.287 },
  // [v2/P1-7] 每项的 costBpsMarket 用该周期自己的 bar_seconds 重算（资金费期数随周期变）
  "alternatives": [ { "bar": "15m", "vetoRateMarket": 0.203, "costBpsMarket": 13.0, "level": "marginal" },
                    { "bar": "1H",  "vetoRateMarket": 0.008, "costBpsMarket": 13.2, "level": "ok" } ],
  "headline": "在 1m 周期上，最近 200 根 K 线的 ATR 中位数约 5.6 bps，而一笔市价往返成本约 13.0 bps；最小 edge 门禁要求 ≥ 23.4 bps，据此约 98.6% 的开仓意图会被否决。",
  "advice": "建议改用 15m 或更长的时间级别。换预设解决不了：最宽松的「激进」档在同一批 K 线上仍有约 93.0% 的开仓意图会被否决。",
  "disclaimer": "以上是按最近 200 根真实 K 线与当前成本常数算出的确定性统计，不是收益预测；它衡量的是「若在某根 K 线上想开仓、会不会被最小 edge 门禁否决」，且未计入止盈上界与信号先验带来的额外收紧，故为乐观下界。"
}
```

> **[v2/P2-4] `samples` 与 §二 表格是两个口径**：这里的 `samples: 200` 是探针窗口
> （`ai_trade_tradability_window_bars`），而 §二 / 附录 A 里的 39764 是**本机缓存全量**——
> 前者用于线上判定、后者用于本轮的一次性论证。两个数字并排出现时极易被读成
> 「200 根就复算出了 98.6%」，故 `headline` 必须像上面这样把「最近 {samples} 根」写进句子里，
> 而附录 A 的表头必须标「全量缓存样本」。
>
> **[v2/P2-5] `vetoRate` 是条件概率**：分母是窗口内**全部** K 线断面，而门禁只在**开仓意图轮**上判定。
> 它回答的是「若在这一根上想开仓，会不会被否」，**不是**「模型的开仓意图里有多大比例被否」
> （后者是轴 C 漏斗的 `blocked["edgeGate"] / intents`）。两个数会不一样，而且都对。
> 本仓库已有「观望轮稀释 `edgeGate` 分母」的前科，故两处文案都必须带口径词，
> `TradabilityCard` 与 `OpenFunnelCard` 并排时尤其要写清。

**缺席语义**：任一子块算不出即该键为 `null`（`stopBps` 在 `stop_atr_mult<=0` 时为 `null`），
**绝不填 0 冒充**。`level=="unknown"` 时除 `headline` / `samples` / `source` 外允许全 `null`。

错误：`bar` 非法 ⇒ 422 `{"error":{"code":"BAD_BAR",...}}`。**取数失败不报错**（见 §3.1.2）。

### 5.2 `GET /api/py/ai-trade/history/preset-comparison`

```jsonc
{ "groups": [
  { "preset": "balanced", "label": "均衡", "samples": 5, "conclusive": true,
    "netPnlUsd": 12.4, "roundTrips": 37, "winRate": 0.51,
    "edgeVetoRate": 0.34, "openConversion": 0.41 },
  { "preset": "off", "label": "关闭", "samples": 2, "conclusive": false, ... } ] }
```

### 5.3 WS `status` 新增三键（成员级，`patchAiTradeMemberStatus` 是整体 spread，新键不会被吞）

```jsonc
"tradability": { "level": "infeasible", "vetoRateMarket": 0.986, "headline": "…", "asOfTs": 1786600000 },
"openFunnel": { "intents": 12, "blocked": { "edgeGate": 11, "consensus": 1 },
                "topBlocker": { "key": "edgeGate", "label": "最小 edge 门禁", "count": 11 },
                "llmDegraded": 0, "sizingZeroed": 0,
                "submitted": 0, "filled": 0, "conversion": null },
"summaryHealth": { "roundsSinceSummary": 4, "failures": 3, "totalFailures": 7,
                   "lastError": "TimeoutError(30.0s)", "stalled": true, "backoffRounds": 40 }
```

三键在未装配 / 零样本时为 `null`（不是缺席），与既有 `horizon` / `acceptance` 四键同款。
`conversion` 在 `intents == 0` 时为 `null`（**不是 0.0**）。

**[v2] `openFunnel` 的三条 wire 级不变式**（做成用例，见 G-10b）：

1. `sum(blocked.values()) + submitted + sizingZeroed <= intents` —— 漏斗必须闭合（P0-3）。
   `blocked` **只统计开仓意愿轮**：平仓侧被 `horizon` / `exchangeBackstop` 拦下的轮次一律不进。
2. `llmDegraded` 只计 **LLM 故障**（`llm_error` / `timeout` / 空产出）；`low_confidence` 的唯一落点
   是 `blocked["confidence"]`，**不得两处都计**（P0-4b）。中文标签用「模型调用失败」。
3. `sizingZeroed` 不是一道闸而是求解结果，**不进 `blocked`**，在漏斗图上画在「委托」前的最后一格
   （P0-4a）。`blocked` 的未知前缀归 `other`，**不得静默丢弃**（G-11）。

### 5.4 `POST /api/py/ai-trade/start` 回执

在既有 `discipline` 同级新增 `tradability`（与 5.1 同一 wire，去掉 `alternatives`）。

### 5.5 轨迹事件

`agent_trace` 新 `phase: "summary_failed"`，`data: {round, reason, failures, stalled}`。
合并器 `trace.py` 按既有 `retry` 面包屑同款处理；前端 `AiTradeTraceRound` 渲染为一条灰色注记。

---

## 六、数据模型

### 6.1 新增 `Settings` 字段（`app/config.py` 末尾追加，**零 DB 迁移**）

| 字段 | 默认 | 约束 | 作用 |
|---|---|---|---|
| `ai_trade_stop_cost_floor_mult` | `2.0` | `ge=0, le=20` | ATR 止损下界 = 该倍数 × 往返成本；`0` ⇒ 逐字回退今日行为 |
| `ai_trade_tradability_enabled` | `True` | — | 轴 A 总闸；`False` ⇒ 端点仍在但返回 `level:"unknown"`，前端整段不渲染 |
| `ai_trade_tradability_window_bars` | `200` | `ge=60, le=500` | 探针窗口根数 |
| `ai_trade_tradability_infeasible_veto_rate` | `0.80` | `gt=0, le=1` | `infeasible` 阈 |
| `ai_trade_tradability_marginal_veto_rate` | `0.40` | `gt=0, le=1` | `marginal` 阈（须 < 上一条，构造期校验） |
| `ai_trade_summary_timeout_s` | `30.0` | `gt=0, le=300` | 摘要独立超时（不再复用决策的 60s） |
| `ai_trade_summary_max_failures` | `3` | `ge=1, le=20` | 连续失败达该数即 `stalled=true` |
| `ai_trade_summary_journal_cap_mult` | `2` | `ge=1, le=10` | 失败后保留的 journal 条数 = 该倍数 × `context_refresh_rounds` |

**[v2/P2-3] 行数预算**：`app/config.py` 现 **740** 行，CLAUDE.md 的硬线是 800。
8 个字段 + 必要注释的净增预算是 **≤ 40 行**（平均每字段 5 行）。若写完超线，
**不得靠删注释压行**（第 1 轮已有「按纯逻辑行估导致 6 处超标、最后靠新建模块解决」的成文记录），
而应把这 8 个字段整体下沉为一个 `AiTradeTradabilitySettings` 嵌套模型或独立配置模块，
并在实施记录里写明这次下沉。

### 6.2 `metricsJson.aiStats` 新增（**加法键，零迁移**，落 `strategy_runs.metrics_json`）

```jsonc
"openFunnel": { "intents": 12, "blocked": {...}, "submitted": 0, "filled": 0, "conversion": null },
"tradability": { "level": "infeasible", "bar": "1m", "vetoRateMarket": 0.986, "samples": 200 },
"stopFloored": { "flooredRounds": 4, "floorAboveCapRounds": 0 }
```

**为什么落 `tradability` 的启动断面而不是结束断面**：用户回看历史时想问的是「我当时是在什么条件下
开的这一局」，而不是「现在的行情如何」。

### 6.3 内存形状

- `OpenFunnelCounters`：会话级单实例，挂 `session._funnel`；**不进任何 deque**（全程累计口径，
  与 `AttributionLedger._trips` 的 `deque(maxlen=200)` 滚动窗口口径**不同**，两处文案都必须带口径词，
  且 [v2/P2-6] 建议把两个口径词各固化成一个共享常量，避免两张卡并排时读者串口径）。
  轮级标志 `_intent_pending` 是**唯一**的跨调用状态，必须在轮末 `finally` 兜底清零（P0-3）。
- `SummaryHealth`：挂 `decider._summary_health`，随 decider 生命周期。
- `TradabilityVerdict`：会话级只读快照，挂 `session._tradability`，**启动后不变**；
  它与启动回执里的那份是**同一个对象**（同一 `asOfTs`），弹窗那份是独立预览（[v2/P1-10] 的表）。

---

## 七、测试与验收判据

> 记号：G-n 为放行判据。任一条不达成即视为本轮未完成；确实做不到的必须在文末**如实记欠账**
> （第 1 轮 G-17 的处理方式是本仓库的成文范例，不得用凑数字代替）。

**轴 A**

- **G-1** 四档 `level` 各有一条纯核心用例；`samples<60` / 成本缺席 ⇒ `unknown`（**不得**回落 `ok`）。
- **G-2**（同构门禁，[v2/P0-1] 已加严）随机 200 组
  `(atr_bps, cost_bps, k_min, atr_target_mult, confidence)`，其中
  **`confidence` 必须在 `{"ok","degraded"}` 两态上都取到**，探针的「是否否决」与
  `edge_gate.apply_edge_gate` 对同一组输入的结论**逐个相等**。
  对拍时显式固定 `take_profit_pct=None, prior_ev_bps=None`，并在用例注释里写明
  「探针是乐观下界，这两项只会让门禁更严」（P2-1）。
- **G-2b**（[v2/P0-1] 回归门禁）构造一个 `confidence=="degraded"` 的成本读数，断言
  `requiredBps == k_min × cost × degraded_mult`（而**不是** `k_min × cost`）；
  把 `degraded_mult` 从 1.2 改成 1.5 时 `requiredBps` 必须跟着变——
  这条用例直接钉死「探针复用了 `edge_gate.degraded_multiplier` 而不是自己写了一份」。
- **G-3** 端点在 OKX 不可达且有缓存时返回 200 且 `source=="cache"`——
  **[v2/P1-4] 在 `allow_cached_fallback` 保持默认 `False` 的配置下就要通过**（探针无条件读缓存）；
  无缓存时返回 200 且 `level=="unknown"`——**任何情况下都不得 5xx**。
- **G-3b**（[v2/P1-7]）`alternatives` 的各项 `costBpsMarket` 在跨越资金费结算期数边界的两个周期上
  （如 `1m` 与 `1D`，`expected_hold_bars=8`）**必须不相等**——相等即说明成本没有按周期重算。
- **G-4** 弹窗用例：`bar` 从 `1H` 切到 `1m` 后重新取一次（debounce 350ms）、`infeasible` 时启动按钮
  **仍可点**但需二次确认；`unknown` 时不出现任何阻碍性文案。
- **G-5** 全仓 grep：`level` / `headline` / `advice` 的判据与文案**只在后端出现一次**；
  前端用例给一个与数字矛盾的 `level`，渲染必须服从后端（禁止前端重算）。

**轴 B**

- **G-6** 下界三分支各一条用例：未绑定（ATR 宽）⇒ `source=="atr"`；绑定 ⇒ `"atr_floored"` 且
  `stop_pct == floor_mult × cost_bps / 1e4`；`floor > sl_cap` ⇒ 返回 `(None, "floor_above_cap")` 且
  **`stopSource=="floor_above_cap"` 一路传到 wire**（[v2/P0-2]：断言的是 `_apply_stop` 的返回值与
  `status`/trace 上的 `stopSource`，**不是**只断言 `resolve_stop_distance` 回了 `None`——
  v1 的写法里这个状态根本到不了 wire）且 `executor.risk` **逐字段不变**。
- **G-6b**（[v2/P0-2]）`counters.floor_above_cap_rounds` 与 `aiStats.stopFloored.floorAboveCapRounds`
  在上一条的场景下 `== 1`；前端 `STOP_SOURCE_LABEL["floor_above_cap"]` 渲染出非空中文
  （**不是空串**——R-5 的三处漏一处即静默空串）。
- **G-7** `ai_trade_stop_cost_floor_mult=0` 时 `resolve_stop_distance` 的输出与合入前**逐字段相等**
  （冻结门禁；注意返回值已是二元组，冻结的是 `plan` 那一元且 `skip_reason is None`）。
- **G-7b**（[v2/P1-2] 回归分两步）先在 `ai_trade_stop_cost_floor_mult=0` 下跑通**全部**既有 AI 回测
  用例（证明改动本身零回归）；再用默认 `2.0` 跑一次，**逐条列出**翻红用例并对每条判定
  「期望的口径变更（改断言）」还是「缺陷（改代码）」，判定写进实施记录。**严禁批量更新快照**。
- **G-8** 回测侧：同一组 `(atr, cost)` 下回测与实盘解出**同一个** `StopPlan`（轴 B 的同构门禁）。
  **[v2/P1-1] 本条只覆盖 `StopPlan`，不覆盖 `size_fraction`**：`p_win` 在回测侧仍恒 `None`
  ⇒ 凯利项恒 `no_prior`，两侧仓位**依然不同构**，这是本轮如实记下的欠账（§3.2.3 / §十一）。
  用例注释里必须写明这条边界，否则下一个人会把 G-8 误读成「回测与实盘仓位已对齐」。
- **G-9** 用 BTC 1m 的真实断面（ATR≈5.6bps、cost=13bps）构造用例：合入前止损 11.2bps < 成本；
  合入后 ≥ 26bps。这条用例的注释里必须写明数字来自本轮实测。

**轴 C**

- **G-10** 分母口径：第一阶段 `open_long` + `degrade=None` ⇒ `intents+1`；
  第一阶段 `hold` 或 `degrade="llm_error"` ⇒ `intents` 不变、`llmDegraded` 相应 +1。
- **G-10b**（[v2/P0-3] 阻断级不变式）**会话级**用例，按真实调用序喂一段混合事件流
  （含「持仓中模型要 `close`、被 `horizon_min_hold` 拦下」的轮次）后断言：
  `sum(blocked.values()) + submitted + sizingZeroed <= intents`，
  且 `blocked` 里**没有** `horizon` 这一项（那一轮压根没记 intent）。
  再补一条对照：`open_long` 遇空头持仓（反手）被 `horizon` 拦下时 `blocked["horizon"] == 1`
  且 `intents == 1`——**反手是开仓意图、必须计入**，这条防止实现节点一刀切按层名排除。
- **G-10c**（[v2/P0-4b]）`degrade=="low_confidence"` 的一轮结束后：
  `blocked["confidence"] == 1` 且 `llmDegraded == 0`（**不得两处都计**）。
- **G-11** `LAYERS` 各层前缀各一条归类用例；未知前缀 ⇒ 归 `other` 而**不是**静默丢弃。
  **[v2/P0-4a]** `sizingZero` 已不在 `LAYERS` 里；补一条用例断言「过了全部门禁但 `size_fraction`
  解到 0」时 `sizingZeroed == 1` 且 `blocked` **不变**（它不是一道闸）。
- **G-12** `intents==0` 时 `conversion` 为 `null`、前端不渲染任何百分比。
- **G-13** `strictness_hint` 五档规则链各一条用例；`tradability_level=="infeasible"` 时
  返回文案**必须包含「时间级别」且不得只建议改预设**（D-1 的回归门禁）。

**轴 D**

- **G-14** 连续 3 次摘要失败后 `len(decider._journal) <= cap`（有界，D-3 的回归门禁）。
- **G-15** `describe_exc(asyncio.TimeoutError())` 非空且含 `"TimeoutError"`（D-4）。
- **G-16** 会话级顺序门禁：摘要 LLM 桩挂起 5s 时，`decision` 事件**先于** `summary` 事件推出（D-5）。
- **G-17** `summaryHealth.stalled` 为真时前端出现「长期记忆已停摆」徽标（用例断言 `data-testid`）。

**轴 E / 全局**

- **G-18** `aiStats` 三个新键在 `stop()` 落库后可读回；老 run 缺键时前端不崩（渲染门控用例）。
- **G-19** 回归基线：`pytest tests -q -k "ai_trade or ai_auto or ai_backtest"` ⇒ **0 failed**，
  且**逐条列出**因本轮口径变更（轴 B 默认下界）而修改的既有用例及其判定理由
  （[v2/P2-8]：v1 写「≥ 1487 passed 且 0 failed」时，无法区分「新增用例通过」与「既有用例被改写」，
  而后者恰恰是本轮最需要被人看见的东西）。
  `NODE_ENV=test npx vitest run components/Agent components/Chart lib` 全绿；
  `ruff` 与 `tsc --noEmit` 对本轮文件全清（既有 `WeightBatchArena.test.tsx` 类型错为存量，不计）。
- **G-19b**（[v2/P1-3] 行数门禁）合入后逐个核对：`api/ai_auto.py`、`config.py`、
  `AiTradeLaunchModal.tsx` 三个文件**不得**因本轮跨过各自在 §四 里写的净增预算；
  `session.py` 净增 ≤ 22 行（R-6）。超预算即按 §四 的下沉方案处理，**不得靠删注释压行**。
- **G-19c**（[v2/P0-2] 调用点扫描）全仓 grep `resolve_stop_distance(`：每一个调用点与每一条用例
  都已改成解二元组；断言没有任何一处把 `tuple` 当 `StopPlan` 用（这种错的报错点会离现场很远）。
- **G-20** 文案纪律：全仓 grep 本轮新增文案，**不得**出现「已校准 / 经回测验证 / 稳定盈利」等
  收益承诺措辞；探针文案必须带 `disclaimer` 那句。

---

## 八、风险与缓解

- **R-1 探针把用户挡在门外**。缓解：全轴 fail-open，`infeasible` 只降为二次确认，
  启动按钮**永不禁用**；`unknown` 一律不阻碍。用例 G-4 守门。
- **R-2 探针判据与门禁真实判据漂移**。缓解：G-2 / G-2b 的对拍用例把两者钉死；探针**不复制**任何
  成本公式，一律经 `CostRuntime.estimate_preview_cost` 与 `edge_gate` 的纯核心
  （含 `degraded_multiplier` 这个**极易被漏掉、且在探针路径上恒定生效**的因子，见 [v2/P0-1]）。
  **这条风险在 v1 里已经真实发生了一次**（式子里就少了那个因子），故 G-2 的随机组必须覆盖
  `confidence` 两态——只随机数值而把枚举固定在 happy path，是本仓库的成文测试盲区。
- **R-3 弹窗多周期探测打爆 REST 配额**。缓解：`alternatives` 只探测比当前更长的周期（≤6 个）、
  `asyncio.gather` 并发一次、debounce 350ms、单次 `limit=200`（在 `_SINGLE_PAGE_LIMIT_MAX=300`
  的单发区间内，不触发 history-candles 翻页）。任一失败即省略该项。
- **R-4 止损下界把止损放宽后单笔亏损变大**。缓解：仓位层的凯利项以 `stop_bps` 为分母
  （`sizing.py:287`），止损放宽自动缩小仓位；且下界与 `sl_cap` 冲突时整条覆盖退回（不硬顶红线）。
  验收看 `aiStats.stopFloored` 与净额期望值，而不是单笔亏损绝对值。
- **R-5 `stopSource` 从五态扩到七态打红既有前端用例**。缓解：`aiTradeSizing.test.ts:132` 是按数组
  遍历 source 的，新增两态须同时更新 `lib/aiTradeTypes.ts:880` 与 `:967` 两处联合类型
  与 `lib/aiTrade.ts` 的 `STOP_SOURCE_LABEL`——**三处漏一处即静默显示空串**。
- **R-6 四个文件都在 CLAUDE.md 的 800 行硬规则附近或之上**（[v2/P1-3] 已从「只盯 `session.py`」
  扩为四条）。实测行数与本轮预算：

  | 文件 | 现行数 | 本轮净增预算 | 超线时的下沉方案 |
  |---|---:|---:|---|
  | `app/ai_auto/session.py` | 1819 | ≤ 22 | 判据与装配全部下沉到四个新纯核心/运行时模块 |
  | `apps/web/components/Agent/AiTradeLaunchModal.tsx` | 1542 | ≤ 20 | 预检整块进 `AiTradeTradabilityFields.tsx`（同文件 `:77-78` 已有两个同款先例） |
  | `app/api/ai_auto.py` | 671 | ≤ 50 | 取数层进 `app/ai_auto/tradability_probe.py`，路由只留解析与装 wire |
  | `app/config.py` | 740 | ≤ 40 | 8 个字段整体下沉为嵌套配置模型 |

  沿用 `CostRuntime` / `ProfitGuardRuntime` / `DisciplineRuntime` 的成文范式。
  **`session.py` 与 `AiTradeLaunchModal.tsx` 的存量债本轮不还**（拆分它们是独立的高风险重构，
  混进本轮会让回归面失控），须在文末如实记为欠账；但**本轮不得让任何一个文件因本轮而新跨过 800**
  （G-19b 守门）。
- **R-7 `_journal` 截断丢失早期素材，摘要质量下降**。缓解：保留最近 `2 × refresh_rounds` 条，
  且上一段摘要 `prev` 始终随 prompt 一起传（`prompts.py:833`）——长期记忆由摘要滚动承载，
  journal 只是本段素材。
- **R-8 摘要退避导致长时间没有新摘要**。缓解：`backoffRounds` 与 `stalled` 一并进 wire 并在
  面板显示；退避上限 `cap=8`（最多 80 轮），不会永久静默。
- **R-9 漏斗的「首拦截层」口径被误读为「该层的总拦截能力」**。缓解：wire 键名与文案统一用
  「首个拦下它的层」，卡片标题写明「按决策链顺序，只归给第一个拦下它的闸」；
  这与 `AttributionLedger` 滚动窗口口径的区别同样要写进文案（本仓库已有 `lifetime` vs 滚动窗口
  两口径混淆的前科）。
- **R-10 多标的分组下探针只对首个成员算**。缓解：探针按 `instId` 逐成员算并各自落成员 status；
  分组级不做合并（合并一个「平均否决率」没有任何操作含义，且 AI-trade **没有分组级实时通道**
  ——`group_status()` 只在挂载拉一次，合并出来的数也没有可信的下发通道）。弹窗侧对 `members[0]`
  之外的标的同样逐个探测并列出，`infeasible` 的成员逐个点名。
- **R-11（[v2/P0-2] 新增）改返回类型是一次跨文件的破坏性签名变更**。
  `resolve_stop_distance` 由 `StopPlan | None` 改为 `(plan, skip_reason)` 二元组，
  而它是**实盘与回测双通道共用**的纯核心入口（`sizing_runtime.py:239`）。
  缓解：G-19c 的全仓 grep 门禁 + 一次性改完所有调用点与用例；
  失败模式很难查（`tuple` 被当 `StopPlan` 用时报错点离现场很远），故这一条必须**先改签名、
  再跑一次全 AI 子集**，而不是边改边跑。
- **R-12（[v2/P0-3] 新增）漏斗的轮级标志会跨轮泄漏**。
  `_intent_pending` 是 `OpenFunnelCounters` 里唯一的跨调用状态；若某轮在
  `note_intent` 之后、`note_block` 之前抛异常（`_run_decision_round` 的 `except` 分支就是这种路径，
  `session.py:797-801`），标志会带进下一轮，把下一轮的平仓侧降级错记成开仓拦截。
  缓解：轮末 `finally`（`session.py:802-804`）兜底清零，并补一条「决策轮抛异常后标志已清零」的用例。

---

## 九、非目标（本轮明确不做）

1. **不拆分 `session.py`**（见 R-6）。
2. **不改任何门禁的判定逻辑**——`edge_gate` / `consensus` / `base_rules` / `horizon` / `turnover`
   的纯核心逐字节不动。本轮只补分母、补预检、补下界。
3. **不做自动调参**：探针给建议，**绝不**自动改用户选的周期或预设。自动改参数是本仓库明令禁止的
   「静默夹紧」形状。
4. **不新增 LLM 调用**：轴 A / C / E 全部是确定性计算。
5. **不动 `live_auto` 链路**：轴 B 的新 settings 经会话私有 `Settings` 副本生效
   （第 1 轮 `discipline.py` 已建立该机制），非 AI 链路逐字节不受影响。
6. **不做两臂 LLM 回测对照**（理由见 §3.5）。

---

## 十、给下游节点的实施注意

1. **`app/config.py` 是混合文件**：工作树里另有一处**不属于本轮**的在制改动
   （`ai_trade_enabled` 默认值翻转，及其配套 `.env.example`、`desktop/src/lib/config.js`、
   `tests/test_config.py`）。本轮只在文件末尾追加字段，与那处 hunk 不重叠，**须按 hunk 精确暂存**。
2. **红灯基线**：本轮实测 AI 子集 **1487 passed / 0 failed**。全量下的 7 条红灯全部来自未跟踪孤儿
   文件 `tests/test_factor_library_expansion.py`（不在 HEAD 中）。验证时若发现新红灯，
   **必须用 `git worktree add <HEAD-sha>` 在干净树上对账**后再判定是否为本轮引入
   （并记得 `cp services/quant/.env` —— 它是 gitignore 的 Agent provider 源，缺它
   `test_ai_backtest_manager` 会挂红且父提交同样复现）。
3. **全量 pytest 不可并发**（共用临时 DB），且上一次跑完进程可能不退出导致锁不释放；
   分块跑用 `python -u -m pytest ... > log`（`pytest -q` 块缓冲）。
4. **前端源码扫描类冻结用例**：本仓库 `core.autocrlf=true`，正则须写 `\r?\n`，否则干净检出上恒红。
5. **`status` 新键的呈现落点必须在同一个 Subtask 内做完**：本仓库最常见的缺陷形状是
   「后端算了、类型补了、但没有渲染落点 ⇒ 死数据」。三个新 status 键各自的渲染点已在 §四 列出，
   实现时请**自己走一遍数据从产生到上屏的每一跳**。
6. **AI-trade 前端的 `status` 是整体 spread**（`patchAiTradeMemberStatus`），新键不会被静默吞掉；
   但 `trades` 相关通道是逐列手抄的，本轮不碰。
7. **`roundsSinceSummary` 在全仓有两个装配点**：`session.py:1610`（`_status_payload`，WS status，
   `snapshot()` 经它带出）与 `session.py:1666`（`info()`，另一条只读载体）。轴 D 的 `summaryHealth`
   **两处都要挂**，否则「从 `info()` 拿状态的那条路径」会看不到停摆事实——这正是本仓库
   「同一读数两处装配、只改了一处」的成文缺陷模式。轴 A / C 的两个新键只挂 `_status_payload`
   （`info()` 今天不带 `disciplines` / `horizon`，保持一致）。
   **[v2/P1-9]** 两处的**渲染义务不同**：`_status_payload` 那份必须有渲染落点（G-17 守门），
   `info()` 那份本轮**明确不要求**新渲染点。这条区别要照抄进实现注释，
   否则下一轮会有人把 `info()` 里「没人渲染的键」当死码删掉，让这个坑第二次复发（见 §3.4.4 的表）。
8. **`alternatives` 不得递归**：探针对更长周期算 `vetoRate` 时只调纯核心 + 取数，
   **绝不**再去算那些周期各自的 `alternatives`（否则一次弹窗打开会放大成 O(n²) 次请求）。
9. **本轮的实测数字是可复算的**：复算脚本读
   `%APPDATA%/aragon-desktop/aragon.db` 的 `candles_cache`，按 Wilder ATR(14) 逐根算
   `ATR/close×1e4`。若实现节点复算出的数字与 §二 的表有出入，**以复算为准并更新本文档**——
   数字是判据的依据，不是装饰。
   **[v2/P1-6] 复算时注意成本口径**：`funding_periods` 恒 ≥ 1（`cost_model.py:143-145` 的
   `max(1, …)`），故 13.0 / 8.5 bps 是 `carry=0` 的**下界**；且探针路径上 `confidence` 恒 `degraded`
   ⇒ 门禁分母还要再乘 `ai_trade_edge_gate_degraded_mult`（1.2）。两项都只会让否决率**更高**，
   本文档的结论方向不变，但 UI 上的任何数字都必须由运行期现算、**不得抄表**。
10. **[v2/P0-2] 先改签名再跑测**：`resolve_stop_distance` 的返回类型变更是一次跨文件破坏性改动
    （实盘与回测双通道共用同一入口 `sizing_runtime.py:239`）。请一次性改完所有调用点与用例，
    再跑一次全 AI 子集；边改边跑会得到一堆离现场很远的报错。
11. **[v2] 本轮的四条新纯核心模块与既有代码的接缝就是风险面**：本轮评审的 4 条 P0 **全部**长在
    「新判据 ↔ 既有代码」的接缝上（探针 ↔ `edge_gate` 的降级倍数、止损下界 ↔ `apply_risk_override`
    的返回值、漏斗 ↔ `hold_horizon` 的动作作用域、漏斗 ↔ `_apply_sizing` 不产生 degrade）。
    实现时请对每一个「我以为它是这样的」既有函数**打开它读一遍**，不要凭记忆下判断。

---

## 附录 A：实测原始数据（可复算）

样本来源：`%APPDATA%/aragon-desktop/aragon.db` → `candles_cache`（用户本机真实 OKX 历史）。
**「样本」列是本机缓存的全量根数**，与线上探针的窗口（`ai_trade_tradability_window_bars`，默认 200）
**不是同一口径**（[v2/P2-4]）——本表是一次性论证，探针是线上判定，两者不可互相引用。
ATR = Wilder ATR(14)，逐根取 `ATR/close × 1e4`（bps）。
成本 = `cost_model` 默认常数：市价 `5+5+2×(1.0+0.5)=13.0 bps`；限价 `2+5+1×(1.0+0.5)=8.5 bps`。

> **[v2/P1-6] 本表的成本口径是 `carry=0` 的下界，不是等式。** `funding_periods` 恒 ≥ 1
> （`cost_model.py:143-145` 的 `max(1, …)`），`_funding_carry_bps` 取不利侧且 ≥ 0
> （`cost_model.py:159`），故实盘 `total_bps = 13.0 + carry(≥0)`；
> 若 `funding_rate` 取不到则 `confidence=="degraded"`，门禁分母还要再乘 1.2（`config.py:458`）。
> **两项都只让否决率更高**：下表的否决率是**下界**，v1 的定性结论因此只会更强、不会被推翻。

| 标的 | 周期 | 样本(全量缓存) | ATR p10 | ATR 中位 | ATR p90 | 否决率(市价, k=1.5) | 否决率(限价, k=1.5) | 2×ATR 止损中位 | 止损<市价成本占比 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| BTC-USDT-SWAP | 1m | 39764 | 2.9 | 5.6 | 11.3 | 98.6% | 92.6% | 11.2 | 62.3% |
| BTC-USDT-SWAP | 5m | 13714 | 8.8 | 15.9 | 31.6 | 65.0% | 31.0% | 31.9 | 2.9% |
| BTC-USDT-SWAP | 15m | 5943 | 15.1 | 28.1 | 50.4 | 20.3% | 5.9% | 56.2 | 0.1% |
| BTC-USDT-SWAP | 30m | 8359 | 24.9 | 44.4 | 74.9 | 4.3% | 0.2% | — | — |
| BTC-USDT-SWAP | 1H | 3737 | 33.1 | 56.5 | 86.7 | 0.8% | 0.0% | 113.0 | 0.0% |
| ETH-USDT-SWAP | 1m | 6709 | 3.6 | 7.1 | 13.4 | 97.8% | 88.8% | 14.1 | 42.5% |
| ETH-USDT-SWAP | 5m | 14467 | 11.8 | 21.0 | 40.5 | 43.9% | 13.2% | 42.0 | 0.4% |
| ETH-USDT-SWAP | 15m | 4579 | 22.2 | 39.5 | 69.0 | 6.4% | 0.1% | 78.9 | 0.0% |
| ETH-USDT-SWAP | 30m | 9103 | 32.8 | 59.2 | 99.7 | 0.8% | 0.0% | — | — |
| ETH-USDT-SWAP | 1H | 2411 | 52.0 | 76.4 | 126.7 | 0.0% | 0.0% | — | — |

按预设横切（BTC-USDT-SWAP，市价口径）：

| 周期 | steady (k=2.0, 需≥26.0bps) | balanced (k=1.5, 需≥19.5bps) | aggressive (k=1.0, 需≥13.0bps) |
|---|---:|---:|---:|
| 1m | 99.8% | 98.6% | 93.0% |
| 5m | 82.6% | 65.0% | 32.5% |
| 15m | 41.6% | 20.3% | 6.1% |
| 30m | 11.4% | 4.3% | 0.2% |
| 1H | 2.4% | 0.8% | 0.0% |

**读法**：这张表说的是「按当前成本常数与门禁阈值，历史上有多大比例的 K 线断面会让一次开仓意图被
最小 edge 门禁否决」。它**不是**收益预测，也**不意味着** 1H 就会赚钱——它只回答「这个组合下，
系统还有没有机会开单」。
**[v2/P2-5] 它是一个条件概率**：分母是**全部** K 线断面，不是「模型的开仓意图数」。
「模型想开 N 次、其中 M 次被 edge 门禁拦下」是轴 C 漏斗的 `blocked["edgeGate"] / intents`，
是另一个数——两个都对，但绝不能互相替代（本仓库已有「观望轮稀释 `edgeGate` 分母」的前科）。

---

## 十一、本轮如实记下的欠账（评审确认，不得用凑数字代替）

> 记账纪律沿用第 1 轮 G-17 的处理方式：做不到的事写清楚「做不到什么、为什么、下一轮怎么还」，
> 而不是把判据改松到能通过为止。

1. **[P1-1] 回测与实盘的仓位求解仍不同构**：本轮只补 `cost_bps`；`p_win` 在回测侧恒 `None`
   ⇒ `kelly_fraction` 恒 `no_prior` ⇒ 凯利项恒不参与 `min`。修它需要给回测引入一条先验装配链路
   （`manager._resolve_prior_snapshots` 今天只在实盘启动流程里），是一次独立特性。
   **影响面**：任何「用 AI 回测校准实盘阈值」的结论都不可迁移——这正是本轮改走轴 E
   （确定性探针 + 真实资金历史对照）的原因。
2. **[R-6] `session.py`（1819 行）与 `AiTradeLaunchModal.tsx`（1542 行）的 800 行硬规则存量债**：
   本轮只保证「不因本轮而更糟」（净增预算 + G-19b 守门），拆分留给独立的重构轮。
3. **[第 1 轮遗留] G-17 的两臂 LLM 回测校准证据仍未取得**：本轮**有意不还这笔债的原形**，
   改用轴 A 的确定性探针 + 轴 E 的历史预设对照卡替代（理由见 §3.5）。
   若未来仍要做两臂对照，必须先还清上面第 1 条，否则对照结论无效。

---

## 实施过程发现的方案缺陷

> 记账纪律同 §十一：做不到 / 改了口径的事写清楚「改了什么、为什么、影响面」，
> 而不是把判据改松到能通过为止。以下 8 条按「阻断级 → 记录级」排序。

### I-1（阻断级）`low_confidence` 在 `note_intent` 那一刻**看不到**开仓动作，只按 `action` 判会让置信度门禁在漏斗里恒不可见

**发现**：§3.3.1 要求 `note_intent` 按 `action ∈ open_* 且 degrade is None` 置位 `_intent_pending`，
而 §5.3 不变式 2 又要求 `low_confidence` 的**唯一**落点是 `blocked["confidence"]`（G-10c）。
这两条在当前代码上**互相矛盾**：`decider._map_decision`（`decider.py:393-398`）只在
`if action in ("open_long","open_short")` 分支里产出 `low_confidence`，且产出时已把决策洗成
`_hold("low_confidence")` —— `note_intent` 收到的 `action` 恒是 `"hold"`。于是
`_intent_pending` 为假 ⇒ `note_block` 第一行就 return ⇒ **G-10c 恒不可能通过**，
置信度门禁在漏斗里是一条永远点不亮的层（与 P0-4a 判死 `sizingZero` 的形状完全一样）。

**修法**：`open_funnel` 新增一张 `_OPEN_ONLY_DEGRADES = ("low_confidence",)` 白名单——
「只可能由开仓分支产出的第一阶段降级码」。命中即视为一次真实开仓意愿：`intents += 1` 且置位标志。
`missing_size` 等码**刻意不在集内**（它们是 LLM 产出格式问题、在可重试白名单里，归 `llmDegraded`
才是实情）。闭合不变式不受影响：这一轮记 1 个 intent、记 1 个 `blocked["confidence"]`。

### I-2（阻断级）冻结门禁 `test_backtest_cost_model_funding_args_come_from_one_source` 的判据是一个写死的「恰好 1 处」

**发现**：轴 B 要求回测侧传 `cost_bps`（§3.2.3，且它是「双通道共用纯核心时只有一侧被实现」这条
成文缺陷模式的正面回应），于是 `backtest.py` 里「按 `_cost_funding` 取资金费的判定现场」从 1 处
变成 2 处，而上一轮的冻结用例断言的是 `len(judged) == 1`。

**修法**：把断言从「恰好 1 处」改为「**每一处**都合规」+「现场数 == `_cost_funding` 调用数」。
它守的东西一个没少——「不得出现第二条估算路径」这条不变式由后者精确表达，而写死的 `1` 只是那条
不变式在当时的一个快照。已在用例里写明改动理由。

### I-3（记录级）`floor_above_cap` 与三个 `skipped_*` 的优先级：**有没有产生过 plan** 是判据

**发现**：§3.2.1a 只说「`source == "fixed"` 且 `skip_reason` 非空则取 `skip_reason`」，没说
「既有挂单在飞、下界又高于红线」时哪个赢。

**结论（已做成用例）**：`skipped_*` 的语义是「**有** plan 但落地那一步被前置条件挡住」，而
`floor_above_cap` 的语义是「plan **从未产生**」。后者发生时说「因为有挂单所以跳过」是一句假话
（根本没有可跳过的计划），故 `floor_above_cap` 赢。实现上这是自然结果：`plan is None` 时
`apply_risk_override` 第一行就回 `"fixed"`，`skip_reason` 随即顶上。

### I-4（记录级）`session.py` 净增 **26** 行，超 §四 的 ≤22 预算 4 行

**构成**（全部为不可再下沉的项）：轴 C 的 5 个记点各 1 行（`note_intent` / `note_sizing` /
`note_executed` / `note_filled` / `end_round`——每一个都必须落在决策链的那个确切位置）、
D-5 的摘要顺序调整 4 行、`status` 三新键 3 行 + 3 行口径注释、`info()` 1 行 + 3 行注释、
`note_tradability` 3 行、1 行 import、2 行 D-5 理由注释。

**已做的下沉**（按 §四 的方案，**没有靠删注释压行**）：漏斗计数器与启动断面整体住进
`DisciplineRuntime`（它本就是「会话 → 纯核心」的适配器，且漏斗与 13 行自检是同一件事的两面）；
`summary_failed` 轨迹装配下沉 `trace.maybe_emit_summary_failed`；[P1-9] 的渲染义务对照表下沉
`summary_health.health_wire` 的 docstring（session 里只留 3 行指针）。其余 4 行的代价是
**每个记点必须在它自己的位置上**——再下沉就要引入一个只为省行数而存在的间接层。
`session.py` 由 1819 → 1849 行，**本轮没有让它新跨过任何一条线**（它早已是 §十一 记下的存量债）。

### I-5（记录级）`tradability.py` 378 行 / `tradability_probe.py` 220 行，超 §四 的 ≤300 / ≤200 估算

两者都远在 CLAUDE.md 的 800 行硬线之内，超出的部分是判据 docstring 与缺席语义的逐条说明
（尤其 `TradabilityInputs` 上那段 [P0-1] 的推导链——它是本轮最容易被下一个人「顺手简化」掉的
东西）。**不为凑估算数字删这些注释**。

### I-6（记录级）启动回执的 `tradability` 是**逐成员的列表**，不是单个对象

§5.4 的写法读起来像单个对象，但 R-10 明令「分组级不做任何合并」，而 [P1-10] 又要求「逐成员各算
一份、与会话 status 是同一份对象」。单个对象只能是**合并**（R-10 禁止）或**静默取 members[0]**
（多标的分组下是误导）。故 `group_status()["tradability"]` 落成 `[{instId, …verdict wire}]`，
每项自带 `instId` 与 `asOfTs`。已在 `manager.group_status` 与 `AiTradeGroup.tradability` 写明。

### I-7（记录级）`list_ai_trade_preset_comparison` 落在 `ai_auto_repositories.py` 而不是 `repositories.py`

§四 的变更表写的是 `app/db/repositories.py`，但本仓库的既有拆分范式是：AI-trade 的聚合读端点住在
自包含的 `ai_auto_repositories.py`，再由 `repositories.py` re-export（调用点仍用 `repo.X`）。
按表面写法把查询塞进 `repositories.py`（已 1025 行）与那条成文范式冲突。已按范式实现 + re-export，
对调用方**零差异**。

### I-8（记录级）因本轮口径变更而修改的既有用例（G-19 要求逐条列出）

| 用例 | 改了什么 | 判定 |
|---|---|---|
| `test_ai_trade_sizing.py`（4 处） | `resolve_stop_distance` 返回二元组，调用点改解包 / 断言 `(None, None)` | **期望的签名变更**（[P0-2]），非缺陷 |
| `test_ai_trade_acceptance_wiring.py::test_backtest_cost_model_funding_args_come_from_one_source` | 「恰好 1 处」→「每一处都合规 + 现场数 == `_cost_funding` 调用数」 | **期望的口径变更**（见 I-2），守的不变式一个没少 |
| `test_ai_trade_discipline_session.py::test_strictness_hint_fires_only_after_enough_rounds_with_zero_opens` | 断言串由「尚未开过任何仓」改为「一次都没有提出开仓」 | **期望的文案变更**（§3.3.2 规则链第 3 条）：该会话 `intents == 0`，新文案才是这个断面的真实原因 |
| `apps/web/lib/aiTradeSizing.test.ts` | `stopSource` 五态 → 七态遍历 | **期望的口径变更**（R-5 已预告） |
| `apps/web/components/Agent/AiTradeLaunchModal.test.tsx` | 补 `aiTradeTradabilityApi` 模块级 mock + 3 条挂载断言 | 新增，非改写 |

**G-7b 的两步验证结果**：`ai_trade_stop_cost_floor_mult=0` 下既有 AI 回测用例**全绿**（改动本身
零回归）；默认 `2.0` 下同样全绿——因为 `ai_trade_stop_atr_mult` 的**出厂默认仍是 0**（轴 B 只在
装配了纪律预设的会话上生效），既有回测用例里显式开总闸②的那几条又都用宽 ATR 断面，下界不咬合。
故本轮**没有出现任何因默认下界而翻红的既有用例**，无需「逐条判定」。

---

## 终审逮到的缺陷（合入前已逐条修掉）

> 记账纪律同上：写清「原来错在哪、为什么会错、改成了什么」。五条里有四条是本仓库的**成文复发
> 形状**——这本身就是这一节存在的理由。

### RV-1（阻断级）`infeasible` 的二次确认是一行**装饰**：勾选态只有写点、没有读点

`tradabilityConfirmed` 在 `AiTradeLaunchModal.tsx` 里被 `useState` 持有、被 `[open]` effect 重置、
被透传给子组件——**然后全仓再没有第二处读它**。`handleConfirm` 的放行判据里没有它，
启动按钮的 `disabled` 里也没有它。于是 §3.1.3 的「需一次显式点击确认」在真实弹窗上是不存在的：
用户在一个实测 98.6% 开不出单的周期上，一次点击就能起一个真实资金会话。
G-4 只断言了「按钮仍可点」，恰好把这半边漏在判据之外。

这是本仓库成文缺陷模式「后端算了但没有呈现落点」的**镜像**：*有写点、没读点*——而本轮的
P0-2（`floor_above_cap` 有读点没写点）已经是它的一次现场复发，两者在同一轮里各出现了一次。

**修法**（保住 fail-open 极性，不靠禁用按钮）：子组件新增 `onNeedsConfirmChange` 向上报
「本块是否要求确认」（判据仍只有 `level === "infeasible"` 一处），父组件据此派生
`tradabilityBlocked` 并拦在**提交路径**上，**刻意不并进 `ready`**（`ready` 直接驱动
`disabled=`，而 R-1 的极性纪律是「启动按钮永不禁用」）；同屏用既有错误横幅说明该做什么；
live 按钮文案在 `infeasible` 时改为「仍要启动 AI 自动交易」（§3.1.3 的原话）。
`tradabilityNeedsConfirm` **刻意不进 `[open]` 重置 effect**：它是探针的派生事实而非用户输入，
重置它会在「关掉又打开、参数没变故 level 没变故子组件不再上报」时让拦截静默失效。
三条用例守门（不禁用 / 未勾选点击不提交且有文案 / 勾选后可提交）。

### RV-2（用户可见）后端文案里的 `**粗体**` 在界面上会显示成两个字面星号

`tradability.build_advice` 的「`**换预设解决不了**`」、`discipline_view` 三条新 hint 与
`_BLOCKER_ADVICE` 的六处 `**…**`，全部经 `status` 下发后**直接进 JSX 文本节点**渲染
（`TradabilityCard` / `AiTradePanel` 红条 / 启动弹窗预检块 / `DisciplineCard.hint`）。
全仓的 markdown 渲染器 `lib/markdown.ts` 只被 `AgentMessageBubble` 与 `AiTradeTraceRound` 用，
这四个渲染点一个都没走它。本仓库为「JSX 里的 `**` 显示成字面星号」已经栽过一次
（holding-horizon 轮的终审）。

**修法**：这四条链路上的文案一律改纯文本（强调改用「」与句式），并把「这几串是纯文本、不是
markdown」写进两个模块的 docstring；同时固化成判据——`tradability` 四档文案、`strictness_hint`
五档规则链（含逐层 `_BLOCKER_ADVICE` 全表）、`funnel_advice` 都断言不含 `**`。
**注意区分**：`cost_text` / `base_rules` / `acceptance_text` 里的 `**` 是**给模型看的 prompt**，
不在此列，不得顺手一起改。

### RV-3（记录级）`cost_view.stop_floor_view` 是一个没有任何消费者的死键，且它唯一的落点是冻结文件

轴 B 按 §四 在 `cost_view.py` 造了 `stop_floor_view` 并挂上 `cost_view()["stopFloor"]`——
但全仓**没有任何一处读它**：`cost_text.format_cost_charter` / `format_cost_for_prompt` 不读，
前端 `types.ts` 的 `costAwareness` 是逐键手抄的类型、也没有这一键。即「轴 B 的呈现落点之一」是空的。

更要命的是它**没法就地补上**：`cost_text.py` 在 `test_shared_execution_files_have_zero_diff`
的跨轮冻结集里（那是 backstop-repair 轮为「成本层的资金费口径不得再动」立的门禁）。为了给本轮
新造的一个键找消费者去解冻别的轮次立下的门禁，正是 §七 明令禁止的「把判据改松到能通过为止」。

**修法**（换落点，不解冻）：删掉 `stop_floor_view` 与 `cost_runtime` 的挂载（新造的死码直接移除，
`cost_view.py` / `cost_text.py` 双双回到零 diff），改由
**`sizing_text.format_sizing_charter`** 在**止损那一句的同段**披露——那本就是 `StopRule` 自己的
宪章章，且未被冻结。只写倍数不写绝对 bps：宪章在会话启动时装配一次，而往返成本逐轮现算，
写死一个数就会与门禁那侧说两套话（`sizing_text` 的第一条纪律是「渲染与判定同源」）。
两条用例守门（装配时说出下界与红线冲突后果 / `mult=0` 时整章逐字回退，含冻结串断言）。
**§四 的偏离记在这里**：变更表写的是 `cost_view.py`，实际落点是 `sizing_text.py`。

### RV-4（记录级）[P0-1] 修好的那条漂移，在**运行期真正走的那条路径**上又被抄了一遍

`required_bps(cost, …)` 确实复用了 `edge_gate.degraded_multiplier`——但 `evaluate()` 走的是
`_required_from_readings`，它自己写了 `degraded_mult if confidence == "degraded" else 1.0`；
`degraded_mult_applied` 那一行是第三份。于是 G-2 的随机对拍钉死的是一个**生产路径从不调用**的
函数：改坏 `_required_from_readings`，G-2 照样全绿。放行条件 1 要求的「必须复用」在文档意义上
成立、在代码意义上不成立。

**修法**：新增最小载体 `_ConfidenceOnly`，让两处标量路径都经 `edge_gate.degraded_multiplier`
转发（别人拥有的值域单点转发，本仓库成文纪律）。行为逐字不变，G-2b 的数值断言原样通过。

### RV-5（记录级）本轮新增的前端用例有 5 处 `tsc` 报错，而验证结论写的是「仅剩存量错误」

`AiTradeTradabilityFields.test.tsx` 用 `Record<string, unknown>` + `{...(props as never)}`
渲染组件，`as never` 让每一处展开都报 TS2698（62 / 83 / 84 / 95 / 144 行）。
它同时把这个文件的门禁价值挖空了：这里本该是那两个受控回调的**契约门禁**，
用 `never` 绕开类型检查等于把「回调签名改了没人发现」原地放行——而 RV-1 新增的
`onNeedsConfirmChange` 正是又一个这样的回调。

**修法**：`renderFields` 改按组件导出的 `AiTradeTradabilityFieldsProps` 标注，去掉全部
`as never`。改完 `tsc --noEmit` 只剩 `components/Factor/WeightBatchArena.test.tsx` 那条存量错误。

---

### 验证结果

> 下面这组数字是**实现节点自报**的；终审复跑后的数字见本节末尾的「终审复跑」。

- 后端 AI 子集：`pytest tests -q -k "ai_trade or ai_auto or ai_backtest"` ⇒ **1573 passed / 0 failed**
  （合入前基线 1487 passed / 0 failed，净增 86 条新用例）。
- 前端：`NODE_ENV=test npx vitest run components/Agent components/Chart lib` ⇒
  **94 files / 1332 passed / 0 failed**。
- `ruff` 对本轮全部改动文件全清；`tsc --noEmit` 仅剩存量错误
  （`components/Factor/WeightBatchArena.test.tsx`，与本轮无关）。
- 行数门禁（G-19b）：`config.py` 740→770（预算 ≤40，实增 30 ✓）、
  `api/ai_auto.py` 671→719（≤50，实增 48 ✓）、
  `AiTradeLaunchModal.tsx` 1542→1559（≤20，实增 17 ✓）、
  `session.py` 1819→1849（≤22，**实增 26，超 4 行**，见 I-4）。

### 终审复跑（合入前，含 RV-1~RV-5 的修改）

- 后端 AI 子集：`pytest tests -q -k "ai_trade or ai_auto or ai_backtest"` ⇒
  **1577 passed / 0 failed**（1573 + 终审新增 4 条：`tradability` 与 `strictness_hint` 的
  纯文本判据各 1 条、仓位宪章的止损下界披露与零回归冻结各 1 条）。
  中途一次红灯是 `test_shared_execution_files_have_zero_diff`——终审自己碰了冻结文件
  `cost_text.py`，见 RV-3，已换落点后回到零 diff。
- 前端：`NODE_ENV=test npx vitest run components/Agent components/Chart lib` ⇒
  **94 files / 1334 passed / 0 failed**（+3 条 RV-1 的确认门禁用例，−1 条被其取代的旧断言）。
- `ruff` 对终审改动文件全清；`tsc --noEmit` 现在**只剩** `WeightBatchArena.test.tsx` 一条存量错误
  （RV-5 修掉了本轮自己引入的 5 条）。
- 行数复核（实测，非估算）：`config.py` 739→770（+31 ✓）、`api/ai_auto.py` 671→719（+48 ✓）、
  `session.py` 1819→1845（+26，超 4 行，与 I-4 一致；上面写的 1849 是笔误）、
  `AiTradeLaunchModal.tsx` 1542→1577（**+35，超 ≤20 预算 15 行**）。
  超出部分全部来自 RV-1 的拦截接线（受控 state / 派生判据 / 错误横幅 / 按钮文案各一处，
  且判据文案已按 §四 的下沉方案放进 `AiTradeTradabilityFields.tsx` 导出）。
  按 §八 R-6 的口径：本轮**没有让任何文件新跨过 800 行硬线**，该文件的存量债仍记在 §十一.2；
  预算超出如实记在这里，**没有靠删注释压行**。

---

## 评审结论

### **有条件通过**（Approved with conditions）

**先说这份设计对在哪里。** 它做了本仓库十几轮 AI-trade 迭代里最难得的一件事：**没有再加一个机制，
而是去证明已有的机制到底有没有在工作**。而且它的证明方式是对的——不是推测、不是跑一次 LLM 回测看
个总数，而是拿用户本机的真实 K 线，按门禁的**同一条式子**逐根复算，算出「98.6% 的开仓意图会被否决」
这个可复核的数。这个数直接解释了用户那句「AI 自动交易现在应该还有问题」，也直接推翻了第 1 轮自己
给出的补救方向（`strictness_hint` 建议「改预设」，而实测最宽松预设在 1m 上仍有 93.0% 否决率）。
**一个能推翻自己上一轮结论的设计文档，比一个自洽的设计文档有价值得多。**
v1 引的每一处 `file:line` 也都经得起逐条打开核对——这在本仓库的 spec 里不是常态。

**再说为什么是「有条件」。** 4 条 P0 全部长在同一个位置：**新判据与既有代码的接缝**。
探针漏了 `degraded_multiplier`（恒定低估 20% 否决率，方向恰好与 R-2 想防的相反）、
止损下界的 `floor_above_cap` 状态在当前返回类型下**产生不出来**、
开仓漏斗会把平仓侧的降级计进开仓分母（图不闭合）、
`sizingZero` 是一条永远点不亮的死层且 `low_confidence` 被计两次。
四条的共同形状是：**设计者对一个既有函数的行为下了一个没有打开它读过的判断**。
这与本仓库前几轮评审的统计完全一致，也是 §十.11 之所以被加进去的原因。
它们已在 v2 正文全部闭合，但闭合的是**文档**——真正的验证要发生在实现节点。

### 放行条件（12 条，逐条对应 P0/P1，实现节点须逐条回执）

1. **[P0-1]** 探针的 `required_bps` 必须复用 `edge_gate.degraded_multiplier`；
   G-2 的随机组覆盖 `confidence ∈ {"ok","degraded"}` 两态；G-2b 通过。
2. **[P0-2]** `resolve_stop_distance` 返回 `(plan, skip_reason)`；`stopSource=="floor_above_cap"`
   **一路走到 wire 并渲染出非空中文**（G-6 / G-6b）；G-19c 的全仓调用点扫描无遗漏。
3. **[P0-3]** 漏斗归类以 `_intent_pending` 为前提；G-10b 的闭合不变式与「反手必须计入」对照用例通过；
   轮末 `finally` 兜底清零（R-12）。
4. **[P0-4]** `sizingZero` 移出 `LAYERS` 改由 `sizing_zeroed` 承载；`low_confidence` 只计一次
   （G-10c / G-11）。
5. **[P1-1]** D-8 / §3.2.3 / §3.5 三处的说法一致：`cost_bps` 本轮补、`p_win` 记欠账；
   G-8 的用例注释写明「只覆盖 StopPlan，不覆盖 size_fraction」；欠账进 §十一。
6. **[P1-2]** 轴 B 的回归分两步走（G-7 → G-7b），翻红用例**逐条列出并判定**，严禁批量更新快照。
7. **[P1-3]** 新建 `tradability_probe.py` 与 `AiTradeTradabilityFields.tsx`；
   四个文件的净增预算全部达标（G-19b）。**不得靠删注释压行。**
8. **[P1-4]** 探针无条件读缓存，偏离理由写进实现注释；G-3 在 `allow_cached_fallback` 默认值下通过。
9. **[P1-7]** `alternatives` 每项用自己的 `bar_seconds` 重算成本并回带 `costBpsMarket`；G-3b 通过。
10. **[P1-8/P1-11]** `strictness_hint` 收成两参聚合入参；`decider` 的到期判定**全仓只有一份**
    （§3.4.2a 的最终形态），退避与 `defer_summary` 不得各改一处。
11. **[P1-9/P1-10]** `summaryHealth` 两个装配点的渲染义务表、`tradability` 三个断面的同源表，
    两张表都照抄进实现注释；`asOfTs` 在弹窗与面板两处都要显示。
12. **[全局]** 三个新 `status` 键各自的渲染落点必须在同一个 Subtask 内做完，
    实现者**自己走一遍数据从产生到上屏的每一跳**（§十.5）——本仓库最常复发的缺陷就是
    「后端算了、类型补了、没有渲染落点」，而本轮 P0-2 已经是它的一次现场复发。

### 未阻塞但强烈建议

- 9 条 P2 已在 v2 正文顺手闭合（P2-1/2/3/4/5/6/7/8/9），实现节点照做即可，不另设回执。
- §十一的三笔欠账在合入说明里必须原样带出，尤其第 1 条——它决定了未来任何「回测校准」类结论的效力。

### 一句话总结

**这份设计解决的是对的问题，用的是对的方法，但它对既有代码的四处假设是错的；
四处已在 v2 修正并配了回归门禁。按上述 12 条放行条件实施，本轮可以进入编码。**
