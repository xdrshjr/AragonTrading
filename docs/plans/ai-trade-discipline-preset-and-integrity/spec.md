# AI 自动交易：交易纪律预设 + 实盘完整性修复（spec v2）

> 特性 slug：`ai-trade-discipline-preset-and-integrity`
> 目标需求：「AI 自动交易功能现在应该还有问题，寻找相关的 BUG 和问题进行修复，确保能够正确的自动交易，并且稳定盈利」
> 本文档为**方案设计**产物，不含实现代码。下游实现节点须逐条落实第四节的文件变更计划与第七节的验收判据。
> **v2（2026-08-12 设计评审后）**：4 条 P0 + 11 条 P1 已在正文逐条闭合，P2 登记欠账；评审结论见文末。

---

## 评审记录（Review Notes · 2026-08-12）

**评审方式**：逐节对照仓库实测——v1 给出的 8 条缺陷证据（file:line）全部按行复核，并实跑
`python -u -m pytest tests/test_ai_trade_prior_session.py -q` 复现 D-6（**7 failed, 7 passed**，与 v1 所述一致）。

**总体判断**：8 条缺陷定位**全部属实**，五轴的机制选型（API 层派生 Settings 副本 / 单写点时钟对齐 /
纪律 digest 呈现）也站得住，是一条风险可控的路径。下列问题**无一例外**长在「新设计与既有代码的接缝」上：
四条 P0 里有三条是同一形状——**判据取自不携带该信息的字段，或产出落在没有消费者的通道上**，落地后表现为
「代码写了、测试也许还绿、但生产上那条安全线恒不生效」。这正是本仓库反复复发的缺陷形状，故一律判 P0。

### P0（阻断级，不修则本轮特性在生产上等于没做）

**P0-1　轴 D 的 bar 对齐公式会在 `1D` 上把 K 线驱动轮的 ts 一起改错**
§3.4 的 `bar_ts = (wall // bar_seconds) * bar_seconds` 隐含「交易所 K 线以 UTC 整点相位开盘」这个前提，
而 OKX 的 `12H` / `1D` 是**港时（UTC+8）开盘**——本仓库早已把这件事写成文：
`app/strategies/_band_structure.py:154`「OKX 的 `12H`/`1D` 是港时（UTC+8）开盘价 K 线，而这里是 UTC 整除分桶，
两者差 4 小时」，`app/strategies/trend_band_swing.py:330`「整除分桶会横跨两根真实根」。而 `1D` 是合法的执行周期
（`app/schemas.py:341 AiExecutionBar = Literal["1m","5m","15m","30m","1H","4H","1D"]`）。后果有两条，第二条更重：
① 1D 会话对齐后的 ts 仍然不落在任何一根真实 K 线上，D-3 原样残留；
② UTC 整除会**改写 K 线驱动轮自己的 ts**（`session.py:673 ts = int(row["ts"])` 是港时相位的 16:00 系列，
被 floor 成 00:00），直接推翻 §7.1 自称的「K 线轮逐字节不变」这一零回归支点，并打红既有断言。
**已修**：§3.4 改为**锚点式对齐**（相位由最近一根已确认 K 线给出，与交易所对齐时区彻底解耦），
判据见 §7.2 G-5 / G-5b。

**P0-2　轴 C 的告警落点是一条死通道：AI-trade 前端根本不消费 `error` 事件**
`apps/web/lib/useAiTradeGroupStream.ts:198-235` 的 `routeMemberEvent` 只有
`snapshot / agent_trace / trade / position / status / decision / summary` 七个分支，
第 234 行的收尾注释写着「equity / candle / error：…… error 由 status.message 呈现」——
但 `status.message`（`session.py:631` 行情异常那句、`:788` 轮次异常那句）在 AI-trade 的**任何**组件里
都没有渲染落点（全仓 grep：`.message` 的渲染只出现在 `AiBacktestModal` / `ConnectionStatus` /
`InstrumentSidebar` / `WatchlistTab` / `TurnoverBackstopCard`，无一在 AI-trade 主链路上）。
故 §3.3 计划新推的 `AI_TRADE_BACKSTOP_MISSING` **会被前端静默丢弃**，裸仓告警等于没做。
**已修**：§3.3 改为「裸仓事实挂在 `status.disciplines.exchangeBackstop` 行 + 翻转时补推一次 status」
（成员 status 走整体 spread 的 `patchAiTradeMemberStatus`，天然不吞新键），`error` 事件降为可选审计留痕，
且**若保留就必须同时补 `routeMemberEvent` 的 `error` 分支与可见落点**——宁可不发，也不发一条没人收的事件。

**P0-3　D-5 / §3.3 / G-7 的判据用了两个并不存在的字段名，实现后告警恒不出现**
v1 写的是 `backstop.coverage < ai_trade_backstop_min_coverage` 或 `status="missing"`。真实 wire 是
`AiTradeBackstopWire { basis, mismatch, coverageRatio, coveredSz, positionSz, repair… }`
（`apps/web/lib/aiTradeTypes.ts:430-449`），既没有 `coverage` 也没有 `status`。按原文实现，
`undefined < 0.999` 恒为 `false` ⇒ 红条**永远不出现**；而且该 wire 已成文规定
「`null` 是合法值＝『还不知道』，与 `0`＝『确认裸露』是两个结论（[P0-2]/D-14）：前者一律灰色、G6 放行」。
**已修**：判据订正为 `basis === "exchange" && (mismatch === true || (coverageRatio != null && coverageRatio < min_coverage))`；
空仓 / 未核实 / `coverageRatio == null` ⇒ 灰色 + note，**绝不报红**（假警报会把真警报一起废掉）。

**P0-4　默认装配仓位层后，「单笔不足最小下单量」会从预检盲区变成常态，且该失败没有任何呈现落点**
启动期 dust 预检 `_budget_too_small`（`manager.py:388-398`）按 **f = 1.0**（全额预算 × 杠杆）算可下张数；
而装配 `sizing` 后运行期真实下单量是 `f × budget`（波动率目标化下 f 常落在 0.2~0.4），
`sizing.py` / `sizing_runtime.py` 里**没有任何 dust 下限保护**（grep `min_sz|dust|f_min` 无命中）。
运行期真的算小了，`executor.py:258-260` 只发一条 `status` 文案「预算 / 杠杆过小，单笔不足最小下单量，已跳过该信号」
——而这条 message 前端不渲染（同 P0-2）。合起来：**预检放行 → 每一轮开仓静默不下单 → 用户只看到「AI 一直不开单」**，
恰好会被误判成本轮纪律预设「把 AI 弄坏了」。
**已修**：新增 §3.1.5「装配后的下单量可行性复核」——启动期按预设的保守 f 下界复核 dust 并如实回执，
运行期把该跳过计数落到纪律条上；判据见 G-14。

### P1（本轮必须闭合）

**P1-1　`build_discipline_digest` 的 8 个关键字形参违反 CLAUDE.md「形参 ≤ 5」硬阈值**
本仓库已有三处聚合结构体范式可直接沿用（`WindowOptions` / `WakeReadings` / `SizingContext`）。
**已修**：§3.2 改为 `build_discipline_digest(facts: DisciplineFacts) -> list[dict]`。

**P1-2　`_resolve_discipline` 的执行时序没写死，预设表里的利润保护那一行会静默失效**
`_resolve_base_rules` / `_resolve_profit_guard` / `_resolve_horizon` / `_resolve_position_step` /
`_resolve_wake_interval_s` 全都在 `AiTradeGroupStartRequest(...)` 的**实参位置**读 `settings`
（`api/ai_auto.py:259-263`），而预设表里含 `ai_trade_profit_guard_hard_tp_return_pct`（`config.py:314`）。
若派生副本晚于这些解析器产生，该行预设就是死字母，且不报任何错。
**已修**：§3.1.1 写死「`_resolve_discipline` 必须排在**所有** `_resolve_*` 之前」+ G-15 用例。

**P1-3　R-2 的 `97` 是硬编码的派生量，会随 env 漂移**
`check_window_capacity` 的 need = `max(vol_window, corr_min_samples) + 1`，其中
`ai_trade_correlation_min_samples`（`config.py:523`，默认 96）**不在预设表里**——运维把它调到 200，
预设仍抬到 97 ⇒ 启动被自己的 422 打回。本仓库对这类复制已有成文纪律（`manager.py:696`「绝不在此复制一份
`_window_max` 公式（复制即漂移）」）。
**已修**：§3.1.3 R-2 改为从**派生后的 settings** 现算 need 并复用 `check_window_capacity` 同一个式子。
附带澄清：`primingBars` 默认 300（`schemas.py:814`），故这条抬升在默认路径上不触发，G-4 必须显式构造 `primingBars=60`。

**P1-4　回测侧 `minConsensus` 与预设的优先级未定义，且回测从来没有做过 R-1 夹紧**
`AiBacktestStartIn.minConsensus`（`schemas.py:963`）经 `backtest_manager.py:139` 走
`req.min_consensus if not None else settings.ai_trade_min_consensus`——装配预设后两者会打架；
更要命的是回测侧**根本没有** `min(value, len(specs))` 的夹紧，预设一上，单策略回测立刻变成
「开仓永久不可达」（本仓库四次前科的同一形状）。
**已修**：§3.1.3 R-3 写死优先级「显式请求字段 > 预设 > config 默认」，并把 R-1 夹紧同样施加到回测侧。

**P1-5　`off` / `enabled=false` 与覆盖字段的冲突语义未定义**
按 §3.1.1，`{"preset":"off","edgeGateK":2.0}` 会返回原对象、**静默丢弃**用户显式填的覆盖值。
**已修**：§5.1 写死——矛盾组合在入口 422 拒绝（`enabled=false` / `preset="off"` 时不允许同时给覆盖字段）。

**P1-6　`ai_trade_discipline_enabled` 的语义全文从未定义**
它在 §4.1 出现一次就再无下文，而它是本特性唯一的一键回滚路径。
**已修**：§3.1.4 写死为**运维总闸**：`False ⇒ 整轴不装配 ⇒ 等价 `off``，优先级高于请求体
（沿用 `*_backtest_enabled` 的既有惯例）。

**P1-7　接口段的端点名与承载模型与代码对不上**
实际端点是 `POST /ai-trade/start`（`api/ai_auto.py:225`），不是 `/ai-trade/group/start`；
且 `/ai-trade/start` 与 `/ai-trade/status` **都没有 `response_model`**，返回的是 `group_status()` 原始 dict
（`manager.py:583-604`）。故 §4.1「`AiTradeStatusOut` 加 `disciplines`」放错了层：
分组级回显应加在 `AiTradeGroupRow`（`schemas.py:919`，`AiTradeGroupStartOut` 继承它）与 `group_status()` 里，
会话级 `disciplines` 走 `_status_payload()`（`session.py:1586`）。
**已修**：§4.1 / §5.1 / §5.2 全部订正。

**P1-8　D-4 的影响陈述里有一句与代码不符的「用户承诺」**
「面板刚刚才承诺『已暂停』」——那句承诺走的是 `session.py:631` 的 `status.message`，
而该 message 在 AI-trade 前端无渲染落点（同 P0-2）。缺陷本身（陈旧期用陈旧价起真实资金决策）**成立且严重**，
但理由必须改写，否则实现节点会去守一个并不存在的 UI 承诺。
**已修**：§2 D-4 重写影响措辞。同时坐实 E-1 的实现约束：守卫**必须前置在 `plan_catchup` 之前**——
`wake_runtime.py:278-294` 有三条分支会调 `self._backlog.consume()`，进函数即可能扣掉待办。

**P1-9　digest 的 `armed` 只回答「装没装配」，不回答「有没有真的拦下过东西」**
本轮一次性打开五道闸，而本仓库已有四次「恒真判据 ⇒ 永不开单」的前科。只报 armed 的面板，
在「闸门把每一单都拦了」这种最危险的断面上与「一切正常」长得一模一样。
**已修**：§3.2 每行增加**本会话累计触发次数**（数据源全部现成：`AttributionLedger._gate`（`attribution.py:57`）、
`sizing` 的刹车/连亏计数、`apply_consensus_gate` 的降级理由、`decider.py:391` 的 `low_confidence`），
并加 G-16：任一闸 armed 且「从未放行过任何开仓」时纪律条须给出显著提示。

**P1-10　预设阈值没有任何经验证据，而本轮的目标句是「稳定盈利」**
v1 的风险表已诚实写明「本轮不承诺阈值最优」，这个态度是对的；但本仓库同时有一条成文纪律：
**不得宣称不存在的东西**。既然 R-3 已经让回测吃同一份 `_resolve_discipline`，就存在一条零额外成本的证据路径。
**已修**：§7.2 新增 G-17——同区间同标的跑 `off` 与 `balanced` 两次 AI 回测，把两条净额曲线 +
`vetoRate` + 成交笔数写进 §九 的实施记录；provider 不可用则记为欠账，且 UI 与文档**一律不得**出现
「已校准 / 经回测验证」这类措辞。

**P1-11　§4.1 的 `app/config.py` 一行与 §3.1.4 自相矛盾**
表里写「不动任何既有默认值」，但新增的 `ai_trade_discipline_default_preset = "balanced"` 本身
就是在改变**默认行为**（这正是本轮的价值所在，不是缺陷），措辞会误导实现节点以为「什么都没变」。
**已修**：§4.1 改写为「不动任何**既有** `ai_trade_*` 阈值默认值；行为默认值的改变**集中且只发生在**新增的
`ai_trade_discipline_default_preset` 这一处，env 仍是最终运维退出口」。

### P2（登记欠账，本轮不做）

- **P2-1**　`_emit_equity(mark, now_ts)`（`session.py:484`）的权益点仍是墙钟秒，与对齐后的 trade `ts` 不同源，
  跨图对拍仍有半根偏差。轴 D 只统一了成交流，权益流留待下一轮。
- **P2-2**　图表周期可由用户切换：会话跑 1H、图表切 4H 时，1H 对齐的 ts 仍落在 4H bar 内。
  根治须在读侧按**图表 bar** 再 floor 一次（`markerForEvent` 兜底），本轮先只保证「会话 bar == 图表 bar」这条主路径。
- **P2-3**　§一的「20342 行 / 63 个模块」「`session.py` 1794 行」（实测 1795）属易腐数字，建议只留量级。
- **P2-4**　G-11 的 `Settings(**{**base.model_dump(), **updates})` 在 `BaseSettings` 下会再读一次 env/.env，
  测试须显式隔离（`_env_file=None`），否则本机 `.env` 会让该门禁时绿时红。
- **P2-5**　digest 里 `signalPrior` 恒 `false` 且与其他 12 行**不同源**（它由三条先验总闸控制），
  note 必须写「怎么开」而不是只写 false。
- **P2-6**　§六落库的 `disciplineArmedCount` 只是个整数，事后无法回答「当时开着哪几条」；
  建议同一 JSON 列里再落一份 armed 的 key 列表（零成本）。
- **P2-7**　`manager.py:610-611` 的注释「settings 取自成员会话的 executor（**进程内同一个 Settings 实例**）」
  在本特性合入后会变成一句假话（会话持派生副本）。实现节点须顺手订正——
  一条在事实变化后没被撤下的注释，比没有注释更坏（同仓 `_cost_basis_note` 的成文教训）。

### 评审确认无误、无需改动的部分

- D-1 / D-2 / D-3 / D-4 / D-5 / D-6 / D-7 的 file:line 证据**逐条复核属实**（含 `config.py` 的
  512/457/178/161/518/522/527/529/530/400 十个默认值、`sizing.py:167/178/192`、`session.py:529/556/673/799/812/837/841`、
  `executor.py:820`、`CandleChart.tsx:154/620/629/638-644`、`AiTradeStatsLive.tsx:57`、`AiTradePanel.tsx:214`）。
- 「API 层派生会话私有 Settings 副本」这一机制**成立且额外收敛了一个历史坑**：`get_settings()` 是
  `Depends` 注入的进程级单例，而 `LiveExecutor` 被 `ai_auto` 与 `live_auto` 共用同一个 settings 对象——
  改用副本后，AI 侧的纪律阈值**再也不会**溢出到非 AI 链路。`AiTradeManager` 同时只持一个 `self._group`
  （`manager.py:583`），故副本无并发歧义；回测侧每作业各持一份，同样安全。
- 回测链路**确实**已接三层纯核心（`backtest.py:96/326` 的 `build_sizing_runtime`、`:50/806` 的
  `evaluate_edge_gate`、`:797` 的刹车 gate、`:412` 的 ATR 序列条件建仓），故 R-3「回测同款解析」不是空头支票。
- 宪章文案已有 `format_sizing_charter`（`sizing_text.py:32`）等按规则现算的落点，
  故「预设只改宪章里已有纪律章节的数值」这一非目标声明成立。
- D-7 的运算符优先级判断正确：`color ?? tone === "long" ? A : B` 解析为 `(color ?? (tone === "long")) ? A : B`，
  `color` 为真值字符串时恒取多头色；补括号后语义与同文件 `trailColor`（`CandleChart.tsx:657`）一致。

---

## 一、概述

AI 自动交易（`services/quant/app/ai_auto/`，20342 行 / 63 个模块）在过去十余轮迭代里已经把「盈利所需的机制」几乎全部造齐了：波动率目标化仓位求解、ATR 止损覆盖、回撤刹车与连亏熔断、成本记账与最小 edge 门禁、信号先验与 EV 分子、利润保护棘轮、持仓周期铁律、换手预算、交易所侧兜底自愈。本轮勘察的结论是：**造齐了，但绝大多数在生产默认路径上是"暗"的**——`ai_trade_sizing_enabled` 默认 `False`（config.py:512），`ai_trade_edge_gate_k_min` 默认 `0.0`（config.py:457，纯核心第一句就 `k_min<=0 ⇒ 直通`），`ai_trade_min_consensus` 默认 `0`（config.py:178），`ai_trade_min_confidence` 默认 `0.0`（config.py:161），`ai_trade_stop_atr_mult` / `brake_slow_dd_pct` / `brake_halt_dd_pct` / `brake_loss_streak` / `effective_exposure_cap_mult` 全部默认 `0`（config.py:518/527/529/530/522）。也就是说：一个用户今天点开弹窗启动 AI 自动交易，拿到的是**一个持有全额预算、没有波动率定标、没有回撤刹车、没有连亏熔断、且明知手续费会吃掉预期收益也照开的 LLM**。这不是"参数保守"，这是"稳定盈利"这件事在默认路径上**结构性不可达**。

同时，勘察定位到 3 条会直接损害「正确自动交易」与「顶级观感」的实打实缺陷：① 由定时唤醒 / 补跑唤醒 / 信号翻转唤醒触发的决策轮，其成交时间戳取的是墙钟秒（session.py:556/812/837），而 K 线驱动轮取的是 K 线开盘秒——`CandleChart` 用 `trade.ts` 直接当 `UTCTimestamp` 画标记（CandleChart.tsx:620/629/647）并按 ts 精确建索引供悬停查询（:154），于是**在默认 2 分钟唤醒 + 1H K 线的配置下，绝大多数真实成交的标记落不到任何一根 K 线上**；② `_maybe_catchup_wake`（session.py:799）漏了 `_stale_degraded` 守卫，而它的同胞 `_maybe_interval_wake`（:529）有——行情断流期间补跑轮仍会拿陈旧价发起真实资金决策；③ 交易所侧兜底止损缺失（`backstopRepair` 默认 `plan` 模式，config.py:400，只出计划不真挂）时，唯一的告警与补挂入口藏在统计弹窗里（`AiTradeStatsLive.tsx:57`），主面板对"你现在是裸仓"这件事一个字都不说。

因此本轮的设计意图是：**不新增任何交易决策逻辑，而是把已经造好、已经过评审、已经有测试的那些层，按一组经过校准的阈值真正装配起来，并让"哪些纪律正在生效"成为一等公民的可见事实**；同时修掉上述三条完整性缺陷、清零已挂红 17 天的测试基线。这条路径的关键优势是风险可控：所有交易语义都走既有纯核心（`sizing.py` / `edge_gate.py` / `drawdown_brake.py` / `signals.apply_consensus_gate`），本轮只做「装配 + 呈现 + 时钟对齐」三件事。

---

## 二、本轮定位的缺陷清单

每条都给出**证据（file:line）→ 影响 → 修复判据**。P0 必须在本轮闭合，P1 本轮闭合，P2 登记欠账。

### D-1（P0）盈利纪律层整体默认不装配

**证据**：`config.py:512 ai_trade_sizing_enabled: bool = False`；`sizing.py:167 parse_sizing_rule` 第一句即 `if not settings.ai_trade_sizing_enabled: return None`；`drawdown_brake.py:110 parse_brake_rule` 在三个阈值皆为 0 时返回 `None`；`sizing.py:parse_stop_rule` 在 `ai_trade_stop_atr_mult <= 0` 时返回 `None`。`AiTradeGroupStartIn`（schemas.py:790）**根本没有露出**这三层的任何入参（manager.py:441 的注释明确写着「不给启动请求加字段，否则永远没人填」——但结果是「没人能填」）。

**影响**：`SizingRuntime.armed` 恒 `False` ⇒ 仓位 = LLM 自报的 `sizeFraction` × 全额预算，`gate()` 直接原样返回、`resolve_live()` 返回同一对象零事件。回撤刹车、连亏熔断、有效敞口上限、ATR 止损覆盖全部不存在。这是「稳定盈利」的头号阻断。

**判据**：默认从 UI 启动的会话，`status.disciplines` 中 `volSizing` / `atrStop` / `drawdownBrake` / `lossStreak` 四行 `armed=true` 且阈值可读；直接构造 `AiTradeSession` / `AiTradeGroupStartRequest` 的既有单测行为**逐字节不变**。

### D-2（P0）最小 edge 门禁默认空转

**证据**：`config.py:457 ai_trade_edge_gate_k_min: float = Field(default=0.0, ...)`；`edge_gate.py:210 k_min = float(settings.ai_trade_edge_gate_k_min)` 后即以 `k_min<=0` 直通；`cost_runtime.py:68` 同款判定决定门禁是否装配。

**影响**：成本层每轮都算出了 `cost_bps`（taker 双边 + 半价差 + 冲击 + 资金费），也把它喂给了 prompt，但**没有任何一条确定性判据会因为「预期毛 edge 覆盖不住成本」而拦下这一单**。模型在震荡行情里高频往返 = 稳定亏手续费。这是「稳定盈利」的第二阻断，且与 D-1 是两条独立的闸（一条管"下多大"，一条管"该不该下"）。

**判据**：`balanced` 预设下 `k_min=1.5`；构造「粗估毛 edge < 1.5 × 成本」的断面时 `open_long` 被降级为 `hold` 且 `degradeReason` 带 edge 门禁码；`off` 预设下逐字回退。

### D-3（P0）非 K 线唤醒产生的成交，时间戳不落在任何一根 K 线上

**证据**：`session.py:556`（定时唤醒）、`:812`（补跑唤醒）、`:837`（信号翻转唤醒）三处一律 `int(now)` 作为决策轮 `ts`；`:478 _monitor_tick` 的 `now_ts = int(time.time())` 同样流入 `executor.reconcile` 与利润保护平仓；`executor.py:820 trade = {"ts": int(ts), ...}` 原样落盘。而 K 线驱动轮走的是 `:673 ts = int(row["ts"])`（K 线开盘秒）。消费侧：`CandleChart.tsx:620/629/647 time: trade.ts as UTCTimestamp`、`:154 map.set(event.trade.ts, ...)` 精确 ts 建索引。

**影响**：默认配置（唤醒 2 分钟 / K 线 1H）下，**绝大多数真实成交**的 ts 是 bar 内的任意一秒，lightweight-charts 的标记因此错位或被丢弃，悬停详情按精确 ts 查表恒查不到；成交列表与权益曲线出现时间倒挂（K 线轮的 ts 可能比其后发生的定时轮 ts 更晚）。读侧曾在 commit 5173d88 用「配对按因果序、显示按时间序」缓解，**写侧从未统一**。

**判据**（v2 按 [P0-1] 订正——**绝不能**写成 `ts % BAR_SECONDS[bar] == 0`）：任一 AI 会话产生的每一笔
`(trade["ts"] - anchor) % BAR_SECONDS[bar] == 0`，其中 `anchor` 为该会话某根真实已确认 K 线的开盘秒；
新增 `trade["wallTs"]` 为真实墙钟秒且单调不减；K 线驱动轮的 `ts` 取值**逐字节不变**（回归护栏，
且该不变性必须对 `1D` 这类**非 UTC 相位**周期同样成立）。

### D-4（P1）补跑唤醒缺少行情陈旧守卫

**证据**：`session.py:799 if self.state != "running" or self._deciding or self._last_board is None: return` —— 对比 `:529` 的 `if (self.wake_interval_s <= 0 or self.state != "running" or self._stale_degraded or self._last_board is None)`。`_maybe_signal_flip_wake` 经 `_preview_ready()`（:841）也带 `not self._stale_degraded`。三条唤醒源里**只有补跑这一条漏了**。

**影响**：行情连接异常（`_check_staleness` 已置 `_stale_degraded=True`，并已把 `degraded=true` 下发到 status）
期间，只要 backlog 里还有待办，轮末 `finally` 仍会用陈旧的 `_last_board` + 陈旧价起一轮**真实资金**决策。
这是一条**系统内部自相矛盾**的安全缺陷：同一时刻，会话对外宣告自己已降级停摆（另外两条唤醒源都已停），
第三条却在照常花钱。（v2 [P1-8] 订正：v1 原文写的「面板刚刚才承诺已暂停」并不成立——那句中文承诺走的是
`session.py:631` 的 `status.message`，而该字段在 AI-trade 前端**没有渲染落点**；缺陷成立，但理由不是「UI 承诺被违背」。）

**判据**：`_stale_degraded=True` 时 `_maybe_catchup_wake` 不认领槽位、**不消耗 backlog 待办**（待办保留，恢复后再补跑）。
实现约束（v2 已按行核实）：`wake_runtime.py:278-294` 的 `plan_catchup` 在**三条分支**上都会调 `self._backlog.consume()`
（触顶弃单 / 输入没真变 / 正常补跑），故陈旧守卫**必须前置在调用 `plan_catchup` 之前**，写在函数内部一律扣待办。

### D-5（P1）交易所侧兜底止损缺失没有一等公民的告警落点

**证据**：`config.py:400 ai_trade_backstop_repair_default_mode: Literal["plan","arm"] = "plan"`（默认只出计划，不向交易所发算法委托——这是上一轮刻意留的安全阀，正确）；补挂入口 `aiTradeApi.repairBackstop` 的唯一调用点在 `AiTradeStatsLive.tsx:57`，即"统计"弹窗内的 `TurnoverBackstopCard`；主面板 `AiTradePanel.tsx:214` 只在 chip 文案里带了一句 `describeTurnoverChip(wire?.turnover, wire?.backstop)`。

**影响**：随单 OCO 未挂上（部分成交、交易所拒单、reduceOnly 冲突）时，持仓在交易所侧**没有任何止损**。此时若客户端关闭 / 崩溃 / 断网，亏损无上界。而用户要发现这件事，必须主动去点开一个统计弹窗。

**判据**（v2 按 [P0-3] 订正字段名——v1 写的 `backstop.coverage` / `status="missing"` **两个键都不存在**，
照写会让 `undefined < 0.999` 恒 false，红条永不出现）：真实 wire 是
`AiTradeBackstopWire { basis, mismatch, coverageRatio, coveredSz, positionSz, repair? }`
（`apps/web/lib/aiTradeTypes.ts:430-449`），裸仓的**唯一判据**为

```
naked = basis === "exchange" && (mismatch === true
        || (coverageRatio != null && coverageRatio < ai_trade_backstop_min_coverage))
```

命中时主面板出现**高显著度**裸仓警示条（红色、常驻、带「立即补挂」按钮直连既有 `repairBackstop`），
`status.disciplines.exchangeBackstop` 行 `armed=false` + 原因文案。
`coverageRatio == null`（还没核实）、`basis !== "exchange"`、以及**当前空仓**三种情况一律**灰色 + note，绝不报红**
——该 wire 已成文规定「`null` 是合法值＝『还不知道』，与 `0`＝『确认裸露』是两个结论（[P0-2]/D-14）」，
一条会误报的红线会把真红线一起废掉。不改 `plan/arm` 默认值（真实 order-algo 未经实测前不得切 `arm`，沿用上一轮结论）。

### D-6（P1）先验层会话级用例是墙钟定时炸弹，红灯基线已持续 17 天

**证据**：`tests/test_ai_trade_prior_session.py:34 _START_TS = 1_785_000_000`（= 2026-07-25 17:20 UTC），夹具 `_snapshot()` 的 `builtAtTs = _START_TS - 3600`；而 `ai_trade_prior_max_age_s` 默认 604800（7 天）。今日（2026-08-12）快照年龄 17.8 天 > 7 天 ⇒ `load_snapshot` 判 `stale` ⇒ `PriorRuntime._entries` 空 ⇒ `build()` 返回 `None` ⇒ `payload["signalPriors"]` 整键缺席。本轮实测：`pytest -k "ai_trade or ai_auto or ai_backtest"` → **7 failed, 1407 passed**，7 条全在该文件。

**影响**：这 7 条红灯被历轮当成"已知红灯基线"绕过，意味着**信号先验 / EV 分子这一层的会话级回归护栏已经失效 17 天**——期间任何真实回归都不会被发现。对一个要求"稳健可靠"的产品，一条长期红的基线比没有测试更糟。

**判据**：该文件不再依赖绝对墙钟；全量 `-k ai_trade` 子集 **0 failed**；新增一条元用例断言"任何先验夹具的 `builtAtTs` 与 `now` 的差 < `ai_trade_prior_max_age_s`"，防复发。

### D-7（P2）图表平仓标记颜色表达式运算符优先级错误

**证据**：`CandleChart.tsx:638-644`
```ts
const closeColor =
  color ?? tone === "long" ? cssVar("--long") : tone === "short" ? cssVar("--short") : cssVar("--muted");
```
`??` 的优先级高于三元条件运算符，实际解析为 `(color ?? (tone === "long")) ? A : B`。当 `color` 为真值字符串（多策略着色路径）时条件恒真 ⇒ **所有平仓标记恒被涂成多头色**；当 `color` 缺席时才退化为原意。`color ??` 这个覆盖意图**完全是死码**。

**影响**：纯显示缺陷，但落在"多策略 / AI 成交在同一张图上区分方向"的主视觉路径上，属于「顶级设计」硬伤。

**判据**：补一条断言 `markerForEvent` 在 `color` 存在且 `tone==="short"` 时返回的颜色不是多头色。

### D-8（P2，登记欠账，本轮只做"不加重"）`session.py` 已 1794 行

**证据**：`services/quant/app/ai_auto/session.py` 1794 行，CLAUDE.md 硬规则为单文件 ≤ 800 行；`backtest.py` 1357 行同样超标。

**本轮约束**：轴 A/B/C/D 在 `session.py` 上的**净增 ≤ 25 行**（新增纯逻辑一律下沉到新模块 `discipline.py` / `discipline_view.py`，沿用 `CostRuntime` / `ProfitGuardRuntime` 的既有下沉范式）。彻底拆分留给下一轮，本 spec 在第八节登记。

---

## 三、技术设计

### 3.1 轴 A — 交易纪律预设（Discipline Preset）

#### 3.1.1 核心机制：派生一份**会话私有的 Settings 副本**

关键发现使这一轴的实现代价极低：`AiTradeGroupStartRequest.settings`（manager.py:127）是**每次启动请求各持一份的引用**，它同时流向 `_build_executor`（manager.py:764 `settings=settings`）、`build_sizing_runtime`（:442）、`parse_sizing_rule` / `parse_stop_rule`（:406/:424）、`AiTradeDecider`（:490）、以及会话内一切 `self.executor.settings.ai_trade_*` 读点（含 `_apply_consensus_gate` session.py:1114 与 `CostRuntime` 的 `k_min`）。

因此纪律预设**不需要**给这十来个读点各加一个形参，只需在 API 层构造 `req` **之前**把 `settings` 换成一份派生副本：

```
settings = _resolve_discipline(body.discipline, settings, spec_count=len(specs), body=body)
```

这条设计有四个必须写死的约束：

- **[P1-2] 时序：`_resolve_discipline` 必须排在所有 `_resolve_*` 之前**。`_resolve_base_rules` /
  `_resolve_profit_guard` / `_resolve_horizon` / `_resolve_position_step` / `_resolve_wake_interval_s`
  全都在 `AiTradeGroupStartRequest(...)` 的**实参位置**读 `settings`（api/ai_auto.py:259-263），
  而预设表里含 `ai_trade_profit_guard_hard_tp_return_pct`（config.py:314）。派生副本晚一步产生，
  这一行预设就是死字母且不报任何错。落地写法：在 `req = AiTradeGroupStartRequest(` 这一行**之前**
  完成 `settings = _resolve_discipline(...)` 的重绑定，此后本函数体内**再无**任何一处读到旧对象
  （含 `cap_frac` 那三个字段——它们不在预设表里，读新读旧同值，但仍以「函数体内只有一个 settings」为纪律）。
  唯一允许排在前面的是 `_resolve_prior_snapshots`（:239，只读三条先验总闸，预设不触及）。
- **绝不改全局**。`get_settings()` 是进程级缓存单例，且 `LiveExecutor` 被 `ai_auto` 与 `live_auto` **共用同一个 settings 对象**（历史坑：`ai_trade_*` 开关会静默改写非 AI 链路）。故必须 `settings.model_copy(update=updates)` 产出**新对象**，绝不 `setattr`。
- **`model_copy(update=)` 绕过校验**，故 `updates` 的每一个值只允许来自两个源：① 冻结的预设常量表（代码内字面量）；② 已过 pydantic `Field` 约束的 `AiTradeDisciplineIn` 覆盖字段。并补一条单测：对每个预设执行 `Settings(**{**base.model_dump(), **updates})` 不抛异常（等价于让真实校验器跑一遍）。
- **`off` 预设 ⇒ `updates` 为空 dict ⇒ 返回原对象本身**（`is` 相等），使"关闭"这条路径与本特性合入前逐字节相同。

#### 3.1.2 预设表（冻结常量，`discipline.py`）

| settings 键 | steady 稳健 | balanced 均衡（默认） | aggressive 激进 | off 关闭 |
|---|---|---|---|---|
| `ai_trade_edge_gate_k_min` | 2.0 | 1.5 | 1.0 | —（不写） |
| `ai_trade_min_consensus` | 2 | 1 | 0 | — |
| `ai_trade_min_confidence` | 0.60 | 0.50 | 0.35 | — |
| `ai_trade_sizing_enabled` | True | True | True | — |
| `ai_trade_vol_target_annual` | 0.15 | 0.20 | 0.35 | — |
| `ai_trade_kelly_lambda` | 0.15 | 0.25 | 0.40 | — |
| `ai_trade_realized_vol_window` | 96 | 96 | 96 | — |
| `ai_trade_effective_exposure_cap_mult` | 1.5 | 2.0 | 3.0 | — |
| `ai_trade_stop_atr_mult` | 2.0 | 2.0 | 2.5 | — |
| `ai_trade_stop_rr` | 2.0 | 2.0 | 1.8 | — |
| `ai_trade_brake_slow_dd_pct` | 0.05 | 0.08 | 0.12 | — |
| `ai_trade_brake_slow_size_mult` | 0.50 | 0.50 | 0.60 | — |
| `ai_trade_brake_halt_dd_pct` | 0.10 | 0.15 | 0.22 | — |
| `ai_trade_brake_loss_streak` | 3 | 4 | 5 | — |
| `ai_trade_brake_loss_cooldown_bars` | 6.0 | 4.0 | 3.0 | — |
| `ai_trade_profit_guard_hard_tp_return_pct` | 0.30 | 0.50 | 0.0（不设硬止盈） | — |

选值依据（须写进 `discipline.py` 的模块 docstring，便于后续用验收卡回看校准）：
- `k_min`：默认成本断面（taker 5bps 双边 + 半价差 1bps + 冲击 0.5bps ≈ 12~15bps）对 1H BTC 的 ATR 粗估毛 edge（≈ 40~60bps）而言，`k=1.5` 只在明显低波动断面拦单，不会把门禁做成恒真。
- `min_consensus`：**必须夹到本会话真实策略数**（见 3.1.3 R-1），否则单策略会话下 `2` 会让开仓永久不可达——这正是本仓库反复踩过的「恒真判据」形状。
- 刹车阈值以「预算的回撤比例」为口径（`drawdown_brake.py` 既有语义），第一档减速、第二档只平不开，两档之间留 5~10 个百分点。

#### 3.1.3 三条派生安全规则（缺一即制造新缺陷）

- **R-1 共识夹紧**：`updates["ai_trade_min_consensus"] = min(preset_value, len(specs))`。策略数不足时不但要夹，还要在 `disciplines` 的 `consensus` 行里把 `note` 写成「已按本会话策略数 N 夹紧」，让"我选的预设为什么没完全生效"可自解释。
- **R-2 窗口容量对账（v2 按 [P1-3] 改为现算，禁止硬编码 97）**：`sizing_enabled=True` 后
  `check_window_capacity`（sizing.py:192，已在 manager.py:709 接线）会要求
  `window_max ≥ need`，`need = max(vol_window, corr_min_samples) + 1`。**这两个数必须从派生后的 settings 现算**——
  `ai_trade_correlation_min_samples`（config.py:523，默认 96）**不在预设表里**，运维把它改成 200 而代码里写死 97，
  启动就会被自己的 422 打回（本仓库对这类复制已有成文纪律：manager.py:696「绝不在此复制一份 `_window_max` 公式
  （复制即漂移）」）。**默认装配绝不允许把用户的启动打回 422**，故 `_resolve_discipline` 同时把 `body.primingBars`
  抬到 `max(body.primingBars, need)`，并在回执 `discipline.adjustments` 里如实列出这次抬升。
  澄清：`primingBars` 默认 300（schemas.py:814）且 `window_max = min(max(warmup+5, primingBars, htf_warmup+5, 80), 1200)`
  （market_window.py:91-95），故默认路径上这条抬升**不触发**；G-4 必须显式构造 `primingBars=60` 才验得到它。
  （备选方案是把 `vol_window` 压到 `window_max-1`，但那会让波动率估计的窗口随用户误配而漂移，口径不稳定，故不采纳。）
- **R-3 回测同款解析（v2 按 [P1-4] 补优先级与夹紧）**：`AiBacktestStartIn` 走**同一个** `_resolve_discipline`
  （沿用 `base_rules` P1-4 / `profit_guard` I-3 的成文教训：两处各解析一次 ⇒ 用户拿回测调出来的参数在实盘失真）。
  两条必须写死的补充：
  ① **优先级**：`显式请求字段（如 AiBacktestStartIn.minConsensus，schemas.py:963） > 预设 > config 默认`。
  今天回测走的是 `backtest_manager.py:139` 的 `req.min_consensus if not None else settings.ai_trade_min_consensus`，
  装配预设后这两个来源会打架，不定优先级就是一处随机行为。
  ② **回测侧同样施加 R-1 夹紧**：回测**今天根本没有** `min(value, len(specs))` 这道夹紧，
  预设一上，单策略回测立刻退化成「开仓永久不可达」——一份全 hold 的回测曲线看上去像「策略太保守」，
  实际是判据恒真。回测侧另有既有的 `*_backtest_enabled` 运维总闸，本轮不动。

#### 3.1.4 装配时机与「缺省即开启」语义

沿用 `baseRules` / `profitGuard` / `tradingHorizon` 的成文惯例：**请求体缺省 ⇒ 服务端按 `ai_trade_discipline_default_preset`（新增 config，默认 `"balanced"`）装配**；`{"enabled": false}` 或 `{"preset": "off"}` 是用户显式关闭的唯一途径。因为解析只发生在 API 层，所有**直接构造** `AiTradeSession` / `AiTradeGroupStartRequest` 的既有单测（数百条）行为逐字节不变——这是本轮零回归的支点。

**[P1-6] 三层优先级（写死，缺一即语义不明）**：

| 层 | 载体 | 语义 |
|---|---|---|
| ① 运维总闸 | `ai_trade_discipline_enabled: bool = True` | `False` ⇒ 整轴不装配、等价 `off`，且**优先级高于请求体**——本特性唯一的一键回滚路径（沿用 `ai_trade_horizon_backtest_enabled` 等既有惯例：总闸关 ⇒ 解析函数恒返回不装配） |
| ② 用户显式 | `discipline.enabled=false` / `preset="off"` / 具名预设 + 覆盖字段 | 用户当次启动的选择 |
| ③ 服务端默认 | `ai_trade_discipline_default_preset="balanced"` | 请求体整键缺省时使用（老客户端 / 桌面旧包） |

**行为默认值的改变只发生在 ③ 这一处**，且它带来的是「今天起点开弹窗启动的会话默认带纪律」——这正是本轮的价值，
但必须在弹窗上**明示**（§3.2 前端呈现）与在回执里**可核对**（§5.2 `discipline.preset` 回显），绝不静默生效。

#### 3.1.5 [P0-4] 装配后的下单量可行性复核（新增，阻断级）

**问题**：启动期 dust 预检 `_budget_too_small`（manager.py:388-398）按 **f = 1.0**（全额预算 × 杠杆）算可下张数；
装配 `sizing` 后运行期真实下单量是 `f × budget`，而波动率目标化 + 凯利缩放下 f 常落在 0.2~0.4。
`sizing.py` / `sizing_runtime.py` 里**没有任何 dust 下限保护**。真的算小了，`executor.py:258-260` 只会发一条
`status` 文案事件「预算 / 杠杆过小，单笔不足最小下单量（{side}），已跳过该信号」——而该 message 在 AI-trade 前端
**没有渲染落点**（同 [P0-2]）。合起来就是：**预检放行 → 每一轮开仓静默不下单 → 用户只看到「AI 一直不开单」**，
并极可能把它误判成「纪律预设把 AI 弄坏了」，从而一键关回 `off`——本轮的价值主张会死在这个断面上。

**修复（三件，缺一不可）**：

1. **启动期按保守 f 复核**：`_resolve_discipline` 产出的每个预设附带一个 `f_floor`（冻结常量，
   `steady=0.15 / balanced=0.20 / aggressive=0.30`，含义是「该预设下解算仓位的保守下界」）。
   `_validate_sizing` 在既有 `_budget_too_small` 之外**再算一次** `budget_i × lever × f_floor`，
   低于 `minSz` ⇒ **不 422**（那会把「今天能启动」的用户挡在门外，是更坏的回归），而是在启动回执
   `discipline.adjustments` 里追加一条 `{"field":"volSizing","reason":"预算偏小：解算仓位可能低于交易所最小下单量"}`，
   并让 digest 的 `volSizing` 行带上同一句 `note`。
2. **运行期计数**：会话侧对「`sz < min_sz` 导致跳过」计数（数据源就是 executor 已经在发的那条 status 事件，
   会话侧 `_dispatch` 之外新增一个纯计数，不新增事件类型），随 `status.disciplines` 的 `volSizing` 行下发。
3. **纪律条呈现**：该计数 > 0 时纪律条把 `volSizing` 行标黄并写明「本会话已有 N 次因不足最小下单量跳过开仓」。

这条与 [P1-9] 是同一条纪律的两面：**只报「装配了」而不报「拦了多少」的面板，在最危险的断面上与一切正常长得一样**。

### 3.2 轴 B — 生效纪律自检（Armed Disciplines Digest）

后端算了、但用户看不见 = 死数据，是本仓库反复出现的缺陷形状。故轴 A 必须配一个呈现落点。

新增纯函数模块 `discipline_view.py`。**[P1-1] 形参必须走聚合结构体**——CLAUDE.md 的硬阈值是「函数形参 ≤ 5」，
v1 的 8 个关键字形参直接违规；本仓库已有三处现成范式（`WindowOptions` / `WakeReadings` / `SizingContext`）：

```python
@dataclass(frozen=True)
class DisciplineFacts:
    settings: Settings          # 会话私有派生副本（阈值的唯一真源）
    sizing: SizingRuntime       # rule / stop_rule / brake_rule / 刹车与连亏计数
    horizon_rule: HorizonRule | None
    base_rule: MaBandRule | None
    profit_rule: ProfitGuardRule | None
    backstop_wire: dict | None  # AiTradeBackstopWire 原样（判据见 §3.3）
    gate_counts: dict           # 各闸本会话累计触发次数（[P1-9]，来源见下表）
    prior_armed: bool
    spec_count: int

def build_discipline_digest(facts: DisciplineFacts) -> list[dict]: ...
```

返回**固定顺序**的 13 行，每行 `{key, label, armed, threshold, source, note, firedCount}`
（`firedCount` 为 [P1-9] 新增；算不出 ⇒ `None`，**绝不填 0 冒充「一次都没触发」**）：

| key | label | armed 判据 | threshold 示例 |
|---|---|---|---|
| `baseRuleMaBand` | 基础策略·MA 通道 | `base_rule is not None` | `20 / 2.0σ / 容差 15%` |
| `consensus` | 同向共识门槛 | `min_consensus > 0` | `≥2 / 共 3 策略` |
| `confidence` | 置信度门槛 | `min_confidence > 0` | `≥0.50` |
| `edgeGate` | 最小 edge 门禁 | `k_min > 0` | `毛 edge ≥ 1.5 × 成本` |
| `volSizing` | 波动率目标仓位 | `sizing.rule is not None` | `年化 20% / λ=0.25` |
| `atrStop` | ATR 止损覆盖 | `sizing.stop_rule is not None` | `2.0×ATR / RR 2.0` |
| `drawdownBrake` | 回撤刹车 | `brake_rule.slow>0 or halt>0` | `减速 8% / 停开 15%` |
| `lossStreak` | 连亏熔断 | `brake_rule.loss_streak > 0` | `连亏 4 笔 / 冷却 4 根` |
| `exposureCap` | 有效敞口上限 | `exposure_cap_mult > 0` | `2.0×单标的满仓` |
| `profitGuard` | 利润保护棘轮 | `profit_rule is not None` | `武装 5% / 回吐 40%` |
| `horizon` | 持仓周期纪律 | `horizon_rule is not None` | `日内波段 / 最短 30min` |
| `exchangeBackstop` | 交易所侧兜底 | `coverage ≥ min_coverage` | `覆盖 100% / plan 模式` |
| `signalPrior` | 信号先验 EV | `prior_armed` | 通常 `false`（无快照） |

**[P1-9] `firedCount` 的数据源（全部现成，零新增统计口径）**：

| key | 计数来源 |
|---|---|
| `edgeGate` | `AttributionLedger._gate["veto"]`（attribution.py:57，`note_gate` 已在逐轮记） |
| `consensus` | 决策降级理由 `low_consensus(...)`（signals.py:256）的累计轮数 |
| `confidence` | 决策降级理由 `low_confidence`（decider.py:391-392）的累计轮数 |
| `volSizing` | `sizing` 的 `scale_erased` / 被压缩轮数 + §3.1.5 的「不足最小下单量跳过」计数 |
| `drawdownBrake` / `lossStreak` | `SizingRuntime.gate()` 返回非空 `brake_reason` 的累计轮数 |
| `profitGuard` | `profit_guard_armed_rounds`（session.py:281，已有只读属性） |
| `baseRuleMaBand` / `horizon` / `exposureCap` | 各自纯核心已有的拦下计数；无则 `None`（**不造数**） |

四条硬约束：
- **算不出 ≠ 0**：任一 `threshold` / `firedCount` 算不出一律 `None` + `note` 说明，绝不填 0 冒充读数。
- **`armed=false` 必须带 `note`**（为什么没生效、怎么开），否则用户只看到一片灰。
  [P2-5] `signalPrior` 那一行与其余 12 行**不同源**（它由三条先验总闸 + 快照新鲜度共同决定），
  其 `note` 必须写「怎么开」，而不是只写一个 false。
- **[P1-9] 恒真自检**：任一闸 `armed=true` 且本会话「从未放行过任何开仓」（决策轮数 ≥ 阈值样本且开仓成交数为 0）
  时，纪律条须显著提示「N 条纪律可能过严：本会话尚未开过任何仓」——本仓库已有四次「恒真判据 ⇒ 永不开单」前科，
  只报 armed 的面板在这个断面上与「一切正常」长得一模一样。
- 该 digest 在两条通道下发：会话级 `status.disciplines`（`_status_payload` 加一个键，1 行；
  前端 `patchAiTradeMemberStatus` 是**整体 spread**，新键不会被吞）与分组级 `group_status().discipline`
  （分组共享同一份预设，故只发一份 + 各成员的 `exchangeBackstop` 差异；该条通道**必须**同步补
  `groupStateFromStatus` 的逐键手抄，见 §4.2）。**不新增任何 WS 事件类型**。

前端呈现（两层，遵循渐进披露）：
- `AiTradePanel` 顶部新增一条**纪律条**：`已生效 9/13` 的紧凑徽标行，未生效项以低饱和灰呈现，整条可点开；裸仓（D-5）时该条右侧升级为红色警示 + 「立即补挂」按钮。
- `AiTradeStatsPanel` → 实时页新增 `DisciplineCard`，13 行明细表（`label / 阈值 / 状态 / 说明`），沿用既有卡片视觉语言。

### 3.3 轴 C — 裸仓一等公民告警

**[P0-2] v1 的落点是一条死通道，本节按评审重写。** `apps/web/lib/useAiTradeGroupStream.ts:198-235` 的
`routeMemberEvent` 只有七个分支，**没有 `error`**（第 234 行注释自称「error 由 status.message 呈现」，
而 `status.message` 在 AI-trade 的任何组件里都没有渲染落点）。把裸仓这件事托付给 `error` 事件，
等于把一条安全告警扔进 `/dev/null`。

- **后端（唯一落点 = status）**：`_status_payload` 补 `disciplines`，其中 `exchangeBackstop` 行按 §2 D-5 的
  三态判据（`naked` / `unknown` / `covered`）产出 `armed + note`；并在该行状态**发生翻转的那一刻补推一次 status**
  ——复用 `session.py:505-508` 已有的 `schedule_repair(..., on_change=lambda: self._push({"type":"status", ...}))`
  同款范式，不新增机制、不新增事件类型。文案必须**精确**：两个 OCO pct 都为 0 时如实说
  「本会话未设置交易所侧止盈止损」，而不是宣称有一个并不存在的兜底（沿用 profit-guard RV-1 教训）。
- **`error` 事件（可选，且带前置条件）**：若实现节点希望额外留一条审计痕迹，可以推
  `{"code":"AI_TRADE_BACKSTOP_MISSING", ...}`，但**必须同时**给 `routeMemberEvent` 补 `error` 分支
  并接到一个真实可见的落点（如 AgentPanel 的事件流）。**不满足这个前置条件就不要推**——
  发一条没人收的事件，只会在下一轮评审里被再抓一次。
- **前端**：`AiTradePanel` 纪律条的红色态**由 `disciplines.exchangeBackstop` 行驱动**（不是由 error 事件驱动），
  「立即补挂」按钮复用 `aiTradeApi.repairBackstop(groupId, instId)`（既有端点
  `POST /ai-trade/{group_id}/member/{inst_id}/backstop/repair`，api/ai_auto.py:294），
  成功/失败均如实反馈（沿用 `AiTradeStatsLive.tsx:45-62` 已有的三态按钮范式，含 502 的中文原因在前）。
  **不改** `plan/arm` 默认值。空仓 / `coverageRatio == null` ⇒ 灰色，红条不出现。

### 3.4 轴 D — 成交时钟对齐（单写点方案）

放弃"逐个改调用点"的方案（`_monitor_tick` 的 `now_ts` 同时喂着 `_check_staleness` 的墙钟语义，逐点改极易改错），改为**在唯一的成交记录构造点做对齐**：

**[P0-1] 对齐必须是「锚点式」，不能是 UTC 整除。** v1 的 `(wall // bar_seconds) * bar_seconds` 隐含
「交易所 K 线以 UTC 整点相位开盘」，而 OKX 的 `12H` / `1D` 是**港时（UTC+8）开盘**，本仓库早已把这件事写成文
（`app/strategies/_band_structure.py:154`、`app/strategies/trend_band_swing.py:330`），而 `1D` 恰是合法执行周期
（`schemas.py:341`）。UTC 整除不但修不好 1D，还会**把 K 线驱动轮自己的 ts 从 16:00 系列改写成 00:00**，
直接打掉本方案自称的零回归支点。

`LiveExecutor.__init__` 新增两个可选形参：`bar_seconds: int = 0` 与 `bar_anchor_ts: int = 0`
（**任一为 0 ⇒ 不对齐** ⇒ `live_auto` 逐字节回退，零回归）；`ai_auto/manager.py:_build_executor` 传
`BAR_SECONDS[req.bar]`，`bar_anchor_ts` 由会话在**每根已确认 K 线**落地时同步一次（`feed_candle` 内 1 行，
计入 §7.2 G-12 的 session 净增预算）：

```python
# executor.py（模块内私有，纯函数）
def _align_bar_ts(self, wall: int) -> int:
    """把墙钟秒吸附到**所属那根真实 K 线**的开盘秒；相位由交易所给的锚点决定，不猜时区。"""
    secs, anchor = self.bar_seconds, self.bar_anchor_ts
    if secs <= 0 or anchor <= 0:          # live_auto / 尚无确认根 ⇒ 逐字回退墙钟
        return wall
    return anchor + ((wall - anchor) // secs) * secs   # 负差走 floor，落到锚点前一根，仍是真实相位

# _make_trade（:820）
wall = int(ts)
trade = {"ts": self._align_bar_ts(wall), "wallTs": wall, ...}
```

由此一次性成立：
- K 线驱动轮：`ts` 即当根开盘秒，恰等于 `anchor` ⇒ `((0) // secs) * secs = 0` ⇒ 返回 `anchor` ⇒
  **对任何相位（含 1D 的港时相位）都逐字节不变**。
- 定时 / 补跑 / 翻转唤醒轮、盯市 tick 的保护平仓、会话止盈止损、`stop()` 的 flatten：一律落到**所属那根 K 线的开盘秒**，标记必上 K 线。
- `wallTs` 单调不减，成交列表排序 / 悬停详情 / CSV 导出改用它，时间倒挂消失。

两条降级须写进注释与判据，**不得静默**：
- **暖机期尚无确认根** ⇒ `bar_anchor_ts` 仍为 0 ⇒ 该阶段成交回退墙钟。这是**有意降级**（此时也确实还没有
  任何一根 K 线可供落点）。注意 `session._ref_ts()`（:461）在无根时会回落 `started_ts`（**非 bar 相位**），
  故写锚点时必须先判 `self._window.candles` 非空，绝不能把 `started_ts` 当锚点。
- **[P2-2] 图表周期可切换**：会话跑 1H、用户把图表切到 4H 时，1H 对齐的 ts 仍落在 4H bar 内。
  彻底根治须在读侧按**图表 bar** 再 floor 一次；本轮只保证「会话 bar == 图表 bar」这条主路径，其余登记为欠账。
- **[P2-1]** `_emit_equity(mark, now_ts)`（session.py:484）的权益点仍是墙钟秒，本轮不动；轴 D 只统一成交流。

**实现前必须先做的核实（阻断级）**：`grep` 全仓所有以 `trade["ts"]` 为**配对键 / 去重键 / 计时源**的读点（重点：`ai_auto/attribution.py`、`funding_ledger.py`、`cost_runtime.record_close`、`horizon_runtime.note_open/note_close`、`live_auto/resting.py`、`db/repositories.append_live_trade`）。对齐后同一根 K 线内的多笔成交会**共享 ts**，任何"按 ts 配对/建字典"的写法都会静默丢单。

评审已代为核实的三项（实现节点仍须自查其余）：
- **安全**：`_dispatch`（session.py:301-328）向 `note_close / note_open / record_close` 传的是独立的
  `time.time()` 墙钟，不读 `trade["ts"]`；`_inject_explain_meta`（:338）的 `meta.barTs` 反而因本轮对齐**变得名副其实**。
- **安全**：`AttributionLedger.record_close`（attribution.py:84-85）把 `trade["ts"]` 存进 `RoundTrip.ts`，
  但全文件无任何按 ts 的配对 / 差分 / 排序读点（只作为随行留痕）。
- **安全**：前端 `CandleChart.tsx:151-158` 的 `tradeEventsByTs` 已经是 `Map<number, TradeMarkerEvent[]>`
  （同 ts 走 `existing.push`），共享 ts 不会互相覆盖。

发现不安全读点 ⇒ 改用 `wallTs` 或索引序，并在本 spec 的"实施过程发现的方案缺陷"段登记。

前端：`Trade` 类型加可选 `wallTs?: number`；`CandleChart.markerForEvent` 继续用 `ts`（正是我们要的 bar 对齐）；成交表 / 悬停 / 导出改用 `wallTs ?? ts`。

### 3.5 轴 E — 完整性修复三件套

- **E-1（D-4）**：`_maybe_catchup_wake`（session.py:799）首个 guard 加 `or self._stale_degraded`。
  评审已核实认领语义：`wake_runtime.py:278-294` 的 `plan_catchup` 在**三条分支**上都会调 `self._backlog.consume()`
  （触顶弃单 / 输入没真变 / 正常补跑），因此守卫**必须前置在调用 `plan_catchup` 之前**——写在它内部一律扣待办，
  行情恢复后就再也补不回来了。
- **E-2（D-6）**：`test_ai_trade_prior_session.py` 的 `_START_TS` 由绝对常量改为**相对当前墙钟**（如 `_START_TS = int(time.time()) // 3600 * 3600 - 3 * 3600`），确保 `builtAtTs` 永远在陈旧窗口内；同文件补一条元用例断言快照年龄 < `ai_trade_prior_max_age_s`。修完后 `-k "ai_trade or ai_auto or ai_backtest"` 必须 0 failed。
- **E-3（D-7）**：`CandleChart.tsx:638-644` 补括号 —— `color ?? (tone === "long" ? long : tone === "short" ? short : muted)`，并补断言用例。

---

## 四、文件 / 模块变更计划

### 4.1 后端（services/quant）

| 文件 | 动作 | 一句话意图 |
|---|---|---|
| `app/ai_auto/discipline.py` | **新建** | 纯核心：冻结预设表 + `DisciplinePreset` 数据类 + `resolve_updates(preset, overrides, spec_count) -> dict` + `PRESET_IDS`；零 IO、零 pandas、可被回测共用 |
| `app/ai_auto/discipline_view.py` | **新建** | 纯核心：`build_discipline_digest(...) -> list[dict]`（13 行固定顺序 wire） |
| `app/config.py` | 修改 | 新增 `ai_trade_discipline_enabled: bool = True`（运维总闸，见 §3.1.4）、`ai_trade_discipline_default_preset: Literal[...] = "balanced"`。**不动任何既有 `ai_trade_*` 阈值默认值**；[P1-11] 行为默认值的改变**集中且只发生在** `default_preset` 这一处，env 仍是最终运维退出口 |
| `app/schemas.py` | 修改 | 新增 `AiTradeDisciplineIn`（`enabled/preset/覆盖字段`）与 `AiTradeDisciplineRowOut`；`AiTradeGroupStartIn` / `AiBacktestStartIn` 各加 `discipline` 字段；**[P1-7]** 分组级回显加在 `AiTradeGroupRow`（:919，`AiTradeGroupStartOut` 继承它）上：`discipline: dict \| None`。**不要**往 `AiTradeStatusOut`（:941，只有 `group` / `aiTradeEnabled` 两个字段）上挂 `disciplines` |
| `app/api/ai_auto.py` | 修改 | 新增 `_resolve_discipline(body, settings, spec_count)`（唯一解析点，含 R-1/R-2/R-3）；**[P1-2] 必须在所有 `_resolve_*` 之前**替换 `settings`；`primingBars` 抬升写回；[P1-5] 矛盾组合 422 |
| `app/ai_auto/manager.py` | 修改 | `_build_executor` 传 `bar_seconds`；`group_status()`（:583-604）输出 `discipline` 段；`AiTradeGroup` 加 `discipline_preset` 字段用于回显；`_validate_sizing` 补 §3.1.5 的 f_floor 复核；顺手订正 :610-611 那句已成假话的注释（[P2-7]） |
| `app/ai_auto/session.py` | 修改（净增 ≤ 25 行） | `_status_payload` 加 `"disciplines"` 一键（调 `discipline_view`）；`_maybe_catchup_wake` 加陈旧守卫（**前置于 `plan_catchup`**）；`feed_candle` 同步 `executor.bar_anchor_ts`；裸仓翻转补推 status（**不是** error 事件，[P0-2]） |
| `app/live_auto/executor.py` | 修改 | `__init__` 加 `bar_seconds: int = 0` + `bar_anchor_ts: int = 0`；新增私有 `_align_bar_ts`（锚点式，[P0-1]）；`_make_trade` 落 `ts`（bar 对齐）+ `wallTs`（墙钟） |
| `app/ai_auto/backtest_manager.py` | 修改 | 回测侧接同一个 `discipline` 解析结果（R-3） |

### 4.2 前端（apps/web）

| 文件 | 动作 | 一句话意图 |
|---|---|---|
| `lib/aiTradeDiscipline.ts` | **新建** | 预设 id / 中文标签 / 13 行 label 映射 + `summarizeDisciplines(rows)`（`已生效 N/13`）+ `isNakedPosition(rows)` |
| `lib/aiTradeTypes.ts` | 修改 | `AiTradeDisciplineRow` / `AiTradeDisciplineRule` 类型；`AiTradeGroupState` 加 `disciplines`；`Trade` 加可选 `wallTs` |
| `lib/useAiTradeGroupStream.ts` | 修改 | `groupStateFromStatus` **逐键手抄**处补 `disciplines`（该函数正是本仓库"静默吞新键"的那一个，必须显式加） |
| `components/Agent/AiTradeLaunchModal.tsx` | 修改 | 新增「交易纪律」分区：4 选 1 预设卡片（稳健/均衡/激进/关闭）+ 每张卡的一句话后果说明 + 展开后的只读阈值表；提交时下发 `discipline` |
| `components/Agent/aiTradeStats/DisciplineCard.tsx` | **新建** | 13 行明细表卡片（label/阈值/状态/说明） |
| `components/Agent/aiTradeStats/AiTradeStatsLive.tsx` | 修改 | 挂 `DisciplineCard` |
| `components/Chart/AiTradePanel.tsx` | 修改 | 顶部纪律条（紧凑徽标 + 可展开）+ 裸仓红色警示态（**由 `disciplines.exchangeBackstop` 行驱动**，[P0-2]）+ 「立即补挂」按钮 |
| `components/Chart/CandleChart.tsx` | 修改 | 修 D-7 括号；成交表/悬停改用 `wallTs ?? ts` |
| `lib/useAiTradeGroupStream.ts`（`routeMemberEvent`） | **仅当**保留 `error` 通道时才改 | [P0-2] 该 switch（:198-235）今天**没有 `error` 分支**，任何依赖 error 事件的新告警都会被静默丢弃。要么不推 error，要么在此补分支 + 接一个真实可见落点，二选一，不许留中间态 |

### 4.3 测试

| 文件 | 动作 | 覆盖 |
|---|---|---|
| `tests/test_ai_trade_discipline.py` | **新建** | 预设表 × 4 的 `resolve_updates` 快照；R-1 夹紧；R-2 **现算 need**（改 `corr_min_samples` 后抬升值随之变，[P1-3]）；`off` 返回同一对象；`off`/`enabled=false` + 覆盖字段 ⇒ 422（[P1-5]）；`ai_trade_discipline_enabled=False` 一票关闭（[P1-6]）；每个预设 `Settings(**updates)` 可校验（`_env_file=None` 隔离，[P2-4]） |
| `tests/test_ai_trade_discipline_session.py` | **新建** | 端到端：装配后 `SizingRuntime.armed=True`、edge 门禁真的拦下低 edge 开仓、`status.disciplines` 13 行齐且 `armed=false` 行必带 note、`profit_rule.hard_tp_return_pct` 取到预设值（守 [P1-2] 时序）、`off` 下逐字回退 |
| `tests/test_ai_trade_backtest_discipline.py` | **新建** | [P1-4]：回测吃同一份解析；`minConsensus` 显式入参 > 预设；单策略回测下预设的 `min_consensus` 被夹到 1（**不得**出现「全 hold」曲线） |
| `tests/test_ai_trade_trade_clock.py` | **新建** | 定时唤醒轮成交落到**所属那根**的开盘秒；**1D 港时锚点专项**（锚 = 16:00 UTC，墙钟 = 锚+3h ⇒ ts 必须回到锚，而不是 UTC 00:00，[P0-1]）；K 线轮 ts 逐字不变；`wallTs` 单调；暖机期无锚点 ⇒ 回退墙钟；`live_auto`（`bar_seconds=0`）逐字回退 |
| `tests/test_ai_trade_wake_context_session.py` | 修改 | 补 D-4：陈旧期不补跑且待办保留 |
| `tests/test_ai_trade_prior_session.py` | 修改 | E-2 时钟相对化 + 防复发元用例 |
| `apps/web/components/Chart/CandleChart.test.tsx` | 修改/新建 | D-7 颜色断言 |
| `apps/web/lib/aiTradeDiscipline.test.ts` | **新建** | 汇总与裸仓判定 |
| `apps/web/components/Agent/AiTradeLaunchModal.test.tsx` | 修改 | 预设选择 → 请求体 `discipline` 正确下发；默认不发字段时 UI 显示"均衡（服务端默认）" |

---

## 五、接口设计

### 5.1 启动请求新增字段（加法，老客户端不发 ⇒ 服务端默认 `balanced`）

```jsonc
// POST /ai-trade/start  （[P1-7] 端点实名，api/ai_auto.py:225；AiBacktestStartIn 同构）
{
  "discipline": {
    "enabled": true,                 // false ⇒ 等价 preset="off"
    "preset": "balanced",            // steady | balanced | aggressive | off
    "edgeGateK": 1.5,                // 可选覆盖，ge=0 le=10；null ⇒ 用预设值
    "minConsensus": 1,               // ge=0 le=8
    "minConfidence": 0.5,            // ge=0 le=1
    "volTargetAnnual": 0.2,          // ge=0 le=3
    "stopAtrMult": 2.0,              // ge=0 le=20（0=显式关闭 ATR 止损覆盖）
    "brakeSlowDdPct": 0.08,          // ge=0 le=1
    "brakeHaltDdPct": 0.15,          // ge=0 le=1
    "brakeLossStreak": 4             // ge=0 le=50（0=显式关闭）
  }
}
```
值域校验一次性在本层做（422），服务端 `_resolve_discipline` **只做缺省回填、不复夹**（沿用 `_resolve_position_step` 的 `[R-2]` 惯例）。`0` 一律表示"显式关闭该闸"，`null` 表示"按预设"。

**[P1-5] 冲突组合一律 422（不静默丢弃）**：`enabled=false` 或 `preset="off"` 时**不允许**同时给任何覆盖字段
（`edgeGateK` / `minConsensus` / …）。v1 的语义会让「关闭」这条路径把用户显式填的数悄悄扔掉——
静默丢弃用户的显式输入是本仓库明令禁止的形状。错误码 `AI_TRADE_DISCIPLINE_CONFLICT`，
文案须点名是哪个字段与哪个开关矛盾。

### 5.2 启动回执 / 状态回显（加法）

```jsonc
// [P1-7] 承载体是 AiTradeGroupRow（AiTradeGroupStartOut 继承它），由 group_status() 现装配下发；
// `POST /ai-trade/start` 与 `GET /ai-trade/status` 都没有 response_model，wire 的真源是 group_status()。
"discipline": {
  "preset": "balanced",
  "adjustments": [ {"field": "primingBars", "from": 60, "to": 97,
                    "reason": "仓位层需要 97 根收盘价"} ],
  "rows": [ { "key": "edgeGate", "label": "最小 edge 门禁", "armed": true,
              "threshold": "毛 edge ≥ 1.5 × 成本", "source": "preset", "note": null } ]
}
```

### 5.3 WebSocket（**不新增事件类型**）

- `status` 事件的 `data` 加 `disciplines`（数组，形状同 5.2 的 `rows`）。前端 `patchAiTradeMemberStatus` 是
  **整体 spread**，故这个新键不会被吞（与逐键手抄的 `groupStateFromStatus` 不同源）。
- **[P0-2]** 裸仓翻转时**补推一次 `status`**（复用 `schedule_repair(..., on_change=...)` 的既有范式），
  由 `disciplines.exchangeBackstop` 行承载事实。**v1 计划的 `error` 事件不能作为唯一落点**——
  `routeMemberEvent`（useAiTradeGroupStream.ts:198-235）没有 `error` 分支，推了也是丢。
  若额外保留 error 留痕，必须同时补该分支与可见落点。

### 5.4 复用的既有接口（零改动）

`POST /ai-trade/{group_id}/member/{inst_id}/backstop/repair`（api/ai_auto.py:294）—— 轴 C 的"立即补挂"按钮直接复用，不新增端点。

---

## 六、数据模型

**零 DB 迁移。** 三处落点全部走既有列的加法写：

1. `strategy_runs.config`（JSON）：`_create_group_run` 追加 `"discipline": {"preset": ..., "rows": [...]}`，使历史会话可回答"当时开着哪些纪律"——这正是后续用验收卡校准预设阈值的前提。
2. `strategy_runs.metrics_json`（JSON）：`_finalize_run` 的 `aiStats` 追加 `disciplinePreset`（字符串）与 `disciplineArmedCount`（整数），供历史列表做"开纪律 vs 不开纪律"的净额对照。**[P2-6]** 建议同时落一份 `disciplineArmedKeys`（字符串数组）——只存一个整数，事后回答不了「当时开着哪几条」，而这正是校准阈值时唯一想问的问题；同一 JSON 列，零额外成本。
3. `live_trades`（既有表）：`append_live_trade` 落的是整个 trade dict，`wallTs` 是新键 ⇒ 自动随行落库，老数据缺键（读侧一律 `wallTs ?? ts` 兜底）。

内存态形状：
```python
@dataclass(frozen=True)
class DisciplinePreset:
    preset_id: str            # steady | balanced | aggressive | off
    updates: dict[str, float | int | bool]   # 直接喂 Settings.model_copy(update=)
    adjustments: list[dict]   # [{field, from, to, reason}]
```

---

## 七、测试与验收标准

### 7.1 零回归门禁（最高优先级）

- `off` 预设 / `discipline.enabled=false`：`_resolve_discipline` 返回**同一个** settings 对象（`is` 相等断言），且启动请求经过整条链路后 `payload` 与 prompt 与本特性合入前**逐字节相同**。
- 所有**直接构造** `AiTradeSession` / `AiTradeGroupStartRequest` / `LiveExecutor`（`bar_seconds` 缺省 0）的既有用例**一行不改即通过**。
- `live_auto` 链路：`_build_executor` 不传 `bar_seconds` ⇒ 成交 `ts` 与 `wallTs` 均为原 `int(ts)` ⇒ 逐字节回退。

### 7.2 功能判据

| 编号 | 判据 |
|---|---|
| G-1 | `balanced` 默认装配后，`SizingRuntime.armed` 为真且 `status.disciplines` 中 `volSizing/atrStop/drawdownBrake/lossStreak` 四行 `armed=true` |
| G-2 | 构造「粗估毛 edge < 1.5×成本」断面 ⇒ `open_long` 被降级 `hold`，`degradeReason` 带 edge 码；构造充裕 edge ⇒ 直通（**不许做成恒真判据**） |
| G-3 | 单策略会话选 `steady`（`min_consensus=2`）⇒ 实际夹到 1，且 `consensus` 行 `note` 如实说明；**不得出现"开仓永久不可达"** |
| G-4 | `primingBars=60` + 默认预设 ⇒ 启动成功（不 422），回执 `adjustments` 列出抬升到 97 |
| G-5 | 定时唤醒轮的成交 `(ts - anchor) % BAR_SECONDS[bar] == 0`（`anchor` 取该会话任一真实确认根的开盘秒）；同一会话 K 线轮的 `ts` 与合入前相同；`wallTs` 序列单调不减 |
| G-5b | **[P0-1] 1D 港时专项**：`bar="1D"`、`anchor = 16:00 UTC` 时，墙钟落在 `anchor+3h` 的成交 `ts` 必须等于 `anchor`（而**不是** UTC 00:00）；同一断面下 K 线驱动轮的 `ts` 逐字节不变。该用例在 v1 的 UTC 整除方案下必红——它就是用来钉死这条的 |
| G-6 | 行情陈旧（`_stale_degraded=True`）时 `_maybe_catchup_wake` 不起轮、backlog 待办数不减；恢复后能补跑 |
| G-7 | **[P0-3]** `basis==="exchange" && (mismatch \|\| (coverageRatio != null && coverageRatio < min_coverage))` ⇒ 主面板红色警示条出现且「立即补挂」可点；两 pct 均为 0 时文案不得宣称存在交易所侧兜底 |
| G-7b | **[P0-3] 反向**：`coverageRatio == null`（未核实）、`basis !== "exchange"`、以及**空仓**三种断面下红条**必须不出现**（灰色 + note）——误报会把真报一起废掉 |
| G-7c | **[P0-2]** 裸仓状态翻转后，仅靠 `status` 通道（不依赖任何 `error` 事件）前端即可拿到该事实：用例直接断言 `patchAiTradeMemberStatus` 后 `member.session.disciplines` 里的 `exchangeBackstop` 行已变。若实现保留了 `error` 事件，须另有一条用例断言 `routeMemberEvent` 的 `error` 分支存在且落到可见状态 |
| G-8 | `pytest -k "ai_trade or ai_auto or ai_backtest"` **0 failed**（当前 7 failed 必须清零） |
| G-9 | `markerForEvent` 在 `color` 存在且 `tone==="short"` 时不返回多头色 |
| G-10 | 13 行 digest 的 `armed=false` 行**全部**带非空 `note` |
| G-11 | 每个预设的 `updates` 通过 `Settings(**{**base, **updates})` 真实校验器（**[P2-4]** 构造时须 `_env_file=None` 隔离本机 `.env`，否则该门禁时绿时红） |
| G-12 | `session.py` 净增 ≤ 25 行；新增两个模块各 ≤ 300 行；无文件因本轮新增而突破 800 行；`build_discipline_digest` 形参 ≤ 5（**[P1-1]** 走 `DisciplineFacts` 聚合体） |
| G-13 | **[P1-2]** `steady` 预设下 `profit_rule.hard_tp_return_pct == 0.30`（钉死 `_resolve_discipline` 排在所有 `_resolve_*` 之前；顺序错则该值恒为 config 默认 0.0，且不报任何错） |
| G-14 | **[P0-4]** 构造「`budget × lever × f_floor < minSz`」的启动断面 ⇒ 启动**不被 422 拒绝**，但回执 `discipline.adjustments` 与 digest 的 `volSizing` 行都带上「解算仓位可能低于最小下单量」；运行期发生一次该跳过后，`volSizing.firedCount` 计数 +1 且纪律条呈现 |
| G-15 | **[P1-4]** 回测：显式 `minConsensus=3` + 预设 `balanced` ⇒ 生效值 3（显式优先）；单策略 + `steady` ⇒ 生效值夹到 1 且回测**产生了非零开仓数**（守「不得做成恒真判据」） |
| G-16 | **[P1-9]** 任一闸 `armed=true` 且本会话开仓成交数为 0（且已过最小样本轮数）⇒ 纪律条给出「N 条纪律可能过严」的显著提示；反向断面不提示 |
| G-17 | **[P1-10] 校准证据**：同区间同标的各跑一次 `discipline=off` 与 `balanced` 的 AI 回测，把两条净额曲线终值、`vetoRate`、成交笔数写入 §九 的实施记录。Agent provider 不可用 ⇒ 记为欠账，且 UI 与文档**一律不得**出现「已校准 / 经回测验证」这类措辞（不得宣称不存在的东西） |
| G-18 | **[P1-6]** `ai_trade_discipline_enabled=False` 时，无论请求体填什么，`_resolve_discipline` 返回**同一个** settings 对象（`is` 相等），整轴不装配 |

### 7.3 命令

```
cd services/quant && python -u -m pytest tests -q -k "ai_trade or ai_auto or ai_backtest"
cd services/quant && python -u -m pytest tests -q            # 全量（不可并发多进程，会共用临时 DB）
cd apps/web && NODE_ENV=test npx vitest run components/Agent components/Chart lib
```
**已知红灯基线**（非本轮回归，须在干净 HEAD worktree 上实测对账后再判定）：`tests/test_factor_library_expansion.py` 7 条（孤儿文件）；全量顺序下 `test_agent_ws` 偶发顺序 flake（分块必绿）。`test_ai_trade_prior_session.py` 的 7 条**不再属于基线**——本轮必须修好。

---

## 八、风险与缓解

| 风险 | 等级 | 缓解 |
|---|---|---|
| **默认装配纪律后 AI 明显更少开单，用户误判为"坏了"** | 高 | 纪律条（轴 B）把"被哪一条拦下"变成可见事实；`degradeReason` 已逐轮下发；弹窗四张预设卡各带一句话后果说明；`off` 一键回到今天的行为 |
| **预设阈值是拍脑袋的，可能不是最优** | 高 | 阈值全部落在 `discipline.py` 的冻结常量表并写明选值依据；落库 `disciplinePreset` + `disciplineArmedCount`，使下一轮能用既有验收卡/历史统计做"开 vs 不开"的净额对照校准。本轮不承诺阈值最优，只承诺**可观测、可回退、可校准** |
| **`model_copy(update=)` 绕过 pydantic 校验，错值静默生效** | 高 | G-11 用真实校验器跑一遍每个预设；用户覆盖字段全部经 `AiTradeDisciplineIn` 的 `Field` 约束 |
| **`ts` 对齐后同一根 K 线内多笔成交共享 ts，按 ts 配对的读点静默丢单** | 高 | 3.4 的阻断级前置核实清单（6 个重点文件）；不安全读点改用 `wallTs`/索引序；G-5 用例守 |
| **`min_consensus` / `k_min` 做成恒真判据（永不开单）** | 高 | R-1 夹紧（**实盘与回测两侧都夹**，[P1-4]）+ G-2/G-3/G-15 用例分别守"该拦要拦"与"该过要过" + G-16 的运行期恒真自检；本仓库已有四次同形状 P0 前科 |
| **[P0-4] 装配仓位层后解算仓位低于交易所最小下单量 ⇒ 静默永不开单** | 高 | §3.1.5 三件套（启动期 f_floor 复核 + 运行期计数 + 纪律条呈现）；G-14 守。**这是本轮最可能把用户直接劝退的断面**：现象与「AI 一直不开单」完全一致，而唯一的诊断信息今天没有渲染落点 |
| **[P0-2] 新告警落在没有消费者的通道上** | 高 | 裸仓事实一律走 `status.disciplines`（整体 spread，不吞键）；error 事件降为可选且带前置条件；G-7c 直接断言前端拿得到 |
| **[P0-1] bar 对齐踩到 OKX 港时相位** | 高 | 锚点式对齐（相位由交易所给的确认根决定，不猜时区）+ G-5b 的 1D 专项用例（v1 方案下必红） |
| **`primingBars` 抬升拉长暖机时间** | 中 | 97 根对 1H 是 4 天历史 K 线，`prefill` 为一次 REST 批量取数，实测代价可忽略；抬升在回执里如实披露 |
| **`groupStateFromStatus` 逐键手抄漏 `disciplines` ⇒ 纪律条恒空** | 中 | 已在 4.2 明确列出该文件；补一条前端用例断言新键不被吞（沿用 `aiTradeAcceptanceStream.test.ts` 的既有范式） |
| **`session.py` 已 1794 行，本轮继续加码** | 中 | G-12 净增 ≤ 25 行硬预算 + 新逻辑一律下沉新模块；文件拆分登记为下一轮欠账（D-8） |
| 交易所侧 `arm` 模式仍未实测 | 中 | 本轮**不动** `plan` 默认值，只补告警与人工补挂可达性；实测留给后续轮次 |

---

## 九、实施顺序（建议）

1. **E 组先行**（E-1/E-2/E-3）：小、独立、立刻把红灯基线清零，为后续所有改动提供可信的绿灯基线。
2. **轴 D**：先做 3.4 的阻断级核实（grep 全部 `trade["ts"]` 读点；评审已代查三项），
   再改 `_make_trade` + `_align_bar_ts` + `bar_seconds`/`bar_anchor_ts` 接线 + `feed_candle` 写锚 + 前端 `wallTs`。
   **先写 G-5b（1D 港时）用例再动代码**——它是这一轴唯一能把「相位」这件事钉死的护栏。
3. **轴 A**：`discipline.py` 纯核心 → schemas → `_resolve_discipline` → manager 接线 → 后端用例。
4. **轴 B**：`discipline_view.py` → `_status_payload` / `group_status` → 前端类型与 `groupStateFromStatus` → `DisciplineCard` → 纪律条。
5. **轴 C**：裸仓翻转 `error` 事件 + 纪律条红色态 + 补挂按钮。
6. 全量回归 + 行数预算对账 + 在本文件追加「实施过程发现的方案缺陷」段。
7. **G-17 校准证据**（`off` vs `balanced` 两次 AI 回测）跑完并写入实施记录；拿不到就如实记欠账，
   **不得**在任何文案里宣称阈值「已校准」。

---

## 十、非目标（本轮明确不做）

- 不新增任何交易决策 / 择时 / 因子逻辑；不改 LLM 提示词的决策语义（预设只影响宪章里已有的纪律章节的**数值**）。
- 不把 `ai_trade_backstop_repair_default_mode` 切到 `arm`（真实 order-algo 未经实测）。
- 不开 `ai_trade_signal_prior_enabled` 等三条先验总闸（需要先有快照重建的运维流程；本轮只保证它在 digest 里如实显示 `armed=false` + 开启方法）。
- 不做 `session.py` / `backtest.py` 的文件拆分（登记为 D-8 欠账）。
- 不改动 `live_auto` / 回测引擎的任何既有行为（除 `bar_seconds=0` 的零回归接缝）。
- 不做多账户 / 多分组并行。

---

**文档版本**：**v2**（设计评审后；v1 为设计节点产出）
**统计**：覆盖评审记录（4 P0 / 11 P1 / 7 P2）/ 概述 / 缺陷清单（8 条，均带 file:line 证据，v2 逐条复核属实）/ 技术设计（5 轴）/ 文件变更计划（29 个文件）/ 接口设计 / 数据模型 / 测试与验收（18 条判据）/ 风险缓解（12 条）/ 实施顺序 / 非目标 / 评审结论。

---

## 评审结论（Review Verdict）

### **有条件通过**（Approved with conditions）

方案的**问题定位**与**机制选型**都成立，且路径是本轮目标下风险最低的一条：不新增任何交易决策逻辑，
只把已经造好、已经过评审、已经有测试的纪律层按一组阈值真正装配起来，并把「哪些纪律正在生效」做成可见事实。
v1 的 8 条缺陷证据经逐行复核**全部属实**（含实跑复现 D-6 的 7 条红灯），
「API 层派生会话私有 Settings 副本」这一机制还顺带收敛了一个历史坑（`ai_trade_*` 阈值溢出到 `live_auto`）。

评审提出的 4 条 P0 与 11 条 P1 已在正文逐条闭合，**无遗留 P0 / P1**。合入前须满足以下**放行条件**：

1. **G-5b（1D 港时锚点）必须先写后改**——它是唯一能证明 [P0-1] 真被修掉的护栏；
   在 v1 的 UTC 整除方案下该用例必红，若它一开始就绿，说明用例写错了。
2. **[P0-2] 二选一，不留中间态**：裸仓告警要么完全走 `status.disciplines`，
   要么在 `routeMemberEvent` 补 `error` 分支并接到真实可见落点。**禁止**推一条前端不消费的事件。
3. **[P0-3] 判据字段名以 `AiTradeBackstopWire` 实名为准**，且 `coverageRatio == null` / 空仓一律不报红（G-7b）。
4. **[P0-4] §3.1.5 三件套缺一不可**。这是本轮最可能把用户直接劝退的断面：
   「解算仓位低于最小下单量」的现象与「AI 一直不开单」完全一致，而唯一的诊断信息今天没有渲染落点。
5. **[P1-2] `_resolve_discipline` 必须排在所有 `_resolve_*` 之前**，并由 G-13 钉死。
6. **[P1-4] 回测侧同样施加 R-1 夹紧**，否则单策略回测会退化成一条「全 hold」的假曲线。
7. **红灯基线必须实测对账**：本轮把 `test_ai_trade_prior_session.py` 的 7 条移出基线（G-8 要求清零）；
   其余基线（`test_factor_library_expansion.py` 等）须在**干净 HEAD worktree** 上实跑后再判定，不得凭历史记忆沿用。
8. **[P1-10] 不得宣称阈值「已校准」**：G-17 的证据拿到就写进实施记录，拿不到就如实记欠账。
   一条在事实变化后没被撤下的承诺，比没有承诺更坏。

P2 七条（权益流时钟、图表 bar 二次对齐、易腐统计数字、`_env_file` 隔离、`signalPrior` 的 note、
落库 armed keys、`manager.py:610-611` 的假注释）登记为欠账，本轮不做；其中 P2-7 建议实现节点顺手订正，
因为它会在本特性合入的同一刻变成一句假话。

**范围判断（right-sizing）**：五轴 29 文件对本仓库的迭代节奏而言属正常量级，且轴 E 独立、可先行清零基线，
拆分点选得合理。**不建议**在本轮再扩范围（尤其是 `session.py` 拆分与 `arm` 模式实测，均已正确列为非目标）。


---

## 实施过程发现的方案缺陷（Issues Found During Implementation）

> 实现节点在落地 §四 变更计划时逐条发现的**方案本身**的问题。凡与 spec 正文冲突之处，以本节为准；
> 每条都写明「原文怎么说 / 为什么不成立 / 实际怎么做」。P0/P1 编号沿用评审记录，本节另起 I- 号。

### I-1（阻断级）§六.3 的「`wallTs` 新键自动随行落库」不成立

**原文**：「`live_trades`（既有表）：`append_live_trade` 落的是整个 trade dict，`wallTs` 是新键 ⇒
自动随行落库」。

**事实**：`db/repositories.py:294-306` 的 `append_live_trade` 是**逐列手抄**到 `Trade` ORM 对象
（`ts` / `side` / `price` / … 一列一列写），没有任何 catch-all 列。照原文实现，`wallTs` 到不了库里，
「历史」页每一笔成交的时间都会回落成 bar 对齐秒——同一根 K 线内的多笔成交显示为同一时刻，
**正是轴 D 要修的症状原封不动地在历史页复现**。

**实际做法**（零迁移）：`session._inject_explain_meta` 把 `wallTs` 搭既有 `explain.meta` 信封落库
（该信封已经在承载 `barTs` / `clOrdId` / `exitSource`）；读侧新增 `schemas._explain_envelope`
（不按 `kind` 过滤的原样解析），`trade_out` 据此把 `wallTs` 提回顶层。`append_live_trade` 的
「仅 meta 内部信封」分支也补上该键，使没有 explain 的合成成交同样带得上。

### I-2（阻断级）§3.4 的读点清单漏了往返配对的时长口径

**原文**：前端「成交表 / 悬停 / 导出改用 `wallTs ?? ts`」。

**事实**：`lib/autoTradePnl.ts::buildRoundTrips` 的 `openedTs` / `closedTs` 也直读 `trade.ts`，而它们
不是展示字段，是**时长与窗口筛选的口径**：`AiTradeStatsLive` 的 `HorizonLiveCard` 三个持仓时长 KPI、
`TradeCandleChartPanel` 的「持仓 N」事实条全部由它派生。对齐之后，1H 周期下一笔 20 分钟的往返会被
算成「持仓 0 秒」——修一个显示缺陷的同时造出一个统计缺陷。

**实际做法**：`openedTs` / `closedTs` 与 `TradeCandleChartPanel` 的时长、两个展示时刻改读
`wallTs ?? ts`；**K 线窗口边界仍用 `ts`**（那本来就是根边界，且已有 padding）。

### I-3　§7.1 的 `live_auto` 零回归判据比既有冻结门禁**弱**

**原文**：「`live_auto` 链路：`_build_executor` 不传 `bar_seconds` ⇒ 成交 `ts` 与 `wallTs` 均为原
`int(ts)`」——即要求非 AI 链路**也带** `wallTs` 键。

**事实**：`test_ai_trade_fee_ledger.py::test_ai_trade_switches_never_leak_into_non_ai_executor`
（4 条参数化）冻结了非 AI executor 成交 dict 的**键集**。无条件多一个键，就是一次非 AI 链路的 wire
变更，四条当场红。

**实际做法**：改为**更强**的零回归——`wallTs` 沿用 `_make_trade` 既有的「缺失即省略键」风格
（`clOrdId` / `ordId` / `pnlPct` / `positionPct` 都是这个风格），只有装配了对齐的链路（= `ai_auto`）
才带该键。读侧一律 `wallTs ?? ts`，故省略无损。判据见
`test_ai_trade_trade_clock.py::test_live_auto_without_bar_seconds_falls_back_verbatim`。

### I-4　§5.3 的 `status.disciplines` 是数组，但 G-16 的提示无处安放

**原文**：「`status` 事件的 `data` 加 `disciplines`（**数组**，形状同 5.2 的 `rows`）」。

**事实**：G-16 要求「N 条纪律可能过严：本会话尚未开过任何仓」这条自检提示有一个显著落点。把它塞成
第 14 个数组元素是造假（它不是一行纪律）；另开一个平级的 `disciplineHint` 顶层键，则两者可能被不同
调用点各算一次而不同源。

**实际做法**：`status.disciplines` 改为 **`{rows, hint}` 对象**，由 `session.discipline_wire()`
**一次装配**产出（`hint` 就是拿同一份 `rows` 算的，结构性同源）。分组级 `group.discipline` 把
`rows` / `hint` 平铺在预设回执同层。前端类型 `AiTradeDisciplineDigest` 承载。

### I-5　G-12 的两条行数约束互相打架，须新建第三个模块

**原文**：§4.1 只列 `discipline.py` / `discipline_view.py` 两个新模块；G-12 同时要求「新增两个模块
各 ≤ 300 行」且「`session.py` 净增 ≤ 25 行」。

**事实**：「会话运行时 → 纯核心入参」的取数适配层（读 `_sizing` / `_cost.attribution` / `_backstop`
/ `_prior` / `_horizon` 的各个计数器）无处安放：塞进 `discipline_view.py` 会让它超 300 行，塞进
`session.py` 会吃掉整个净增预算。

**实际做法**：新建**第三个**模块 `ai_auto/discipline_runtime.py`（124 行），严格镜像仓内既有的
`cost_runtime` / `profit_guard_runtime` / `horizon_runtime` 下沉范式——**判据与文案在纯核心
`discipline_view`（可被纯函数单测逐行钉死），取数在 runtime（只能靠会话级集成测试）**，`session.py`
只留三行薄调用。实测：`session.py` 净增 **25** 行（预算内）、`discipline.py` 189 行、
`discipline_view.py` 300 行、`discipline_runtime.py` 137 行。

### I-6　R-2 要「复用同一个式子」，但那个式子当时有两份拷贝且都不返回数字

**原文**：R-2「从派生后的 settings 现算 need 并复用 `check_window_capacity` 同一个式子」。

**事实**：`check_window_capacity`（`sizing.py`）只返回中文错误文案、不返回 `need`；而
`SizingRuntime.window_need`（`sizing_runtime.py`）已经是同一个式子的**第二份**拷贝。要「复用」，
就必须先把它变成可调用的一份。

**实际做法**：在 `sizing.py` 抽出 `window_need(rule) -> int` 作为**唯一真源**，
`check_window_capacity` 与 `SizingRuntime.window_need` 改为调用它（净减一份既有拷贝），
`_resolve_discipline` 读同一个函数。判据 `test_ai_trade_discipline.py::
test_priming_bars_are_raised_from_the_derived_settings_not_a_hardcoded_97` 显式验证「把
`ai_trade_correlation_min_samples` 调到 200 ⇒ 抬升值随之变成 201」。

### I-7　上一轮留下的跨轮冻结门禁把本轮要改的文件锁死了

**事实**：`test_ai_trade_acceptance_wiring.py::test_shared_execution_files_have_zero_diff`
（horizon-acceptance 轮的 G-4/G-6）断言 `live_auto/executor.py` **零 git diff**，而本轮 §4.1 显式
要改它——轴 D 的唯一成交构造点就在 `_make_trade`。

**实际做法**：把该文件移出冻结集，并在 docstring 里写明**移出的代价必须由等价护栏顶上**：
`live_auto` 的零回归改由「键集冻结」（fee_ledger 那 4 条）+「取值冻结」（trade_clock 新增的 2 条）
两条**行为**断言承担，而不是「文件没被碰过」这种间接证据。`simulator.py` / `cost_model.py` /
`cost_text.py` 三个文件仍在冻结集内且本轮确实零改动。

### I-8　`group_status()` 直读会话对象，每加一个读点就要同步四处测试替身

**事实**：`group_status()` 新读 `session.discipline_wire()` 与 `group.discipline_*` 之后，三个
`_FakeSession`（`test_ai_trade_api` / `test_ai_trade_group` / `test_auto_trade_admission`）与一个
`SimpleNamespace` 分组桩（`test_ai_trade_backstop_repair_session`）集体 AttributeError，25 条红。

**实际做法**：同步四处替身（替身必须跟上被 manager 读到的真实接口，否则守的是一个与生产不同构的
接口）。**登记为下一轮欠账**：这是 `group_status()` 直接读会话对象这一既有耦合的固定成本，
根治是给 `AiTradeSession` 定义一个显式的只读视图协议，本轮不扩范围。

### I-9　一处**有意**的既有断言变更（本轮唯一）

`test_ai_trade_api.py::test_profit_guard_default_on_when_field_absent` 原断言默认
`hardTakeProfitReturnPct == 0.0`。本轮默认预设 `balanced` 把它抬到 `0.50`——这**正是** G-13 要的
证据（预设够得着利润保护那一行，即 `_resolve_discipline` 确实排在 `_resolve_profit_guard` 之前）。
该断言改为 `0.50` 并在用例里写明「顺序一旦写反，这里会静默变回 0.0 且不报任何错」。
除此之外，本轮**没有**修改任何既有断言的期望值。

### I-10（欠账）G-17 校准证据：本轮未取得

见 §九·实施记录。

---

## 实施记录（Implementation Record）

### 判据对账（§7.2）

| 编号 | 状态 | 证据 |
|---|---|---|
| G-1 | ✅ | `test_ai_trade_discipline_session.py::test_balanced_preset_arms_the_four_profit_critical_gates` |
| G-2 | ✅ | 同文件 `test_edge_gate_at_the_balanced_threshold_is_not_a_tautology`（阈值**两侧**都验） |
| G-3 | ✅ | `test_ai_trade_discipline.py::test_min_consensus_is_clamped_to_the_real_strategy_count` + 呈现端 `test_clamped_consensus_explains_itself_in_the_digest` |
| G-4 | ✅ | `test_ai_trade_api.py::test_small_priming_bars_are_raised_instead_of_rejecting_the_start`（60 → 97，不 422） |
| G-5 | ✅ | `test_ai_trade_trade_clock.py::test_interval_wake_trade_lands_on_the_owning_bar_open` / `test_wall_ts_is_monotonic_while_ts_repeats_within_one_bar` |
| G-5b | ✅ | 同文件 `test_one_day_hk_phase_*` 两条（**先写后改**：实现前实跑 8 条全红，实现后全绿） |
| G-6 | ✅ | `test_ai_trade_wake_context_session.py::test_stale_feed_blocks_the_catchup_round_and_keeps_the_backlog` |
| G-7 | ✅ | `test_ai_trade_discipline_session.py::test_naked_backstop_row_is_red_and_says_what_to_do` + 面板 `NakedBackstopAlert`（「立即补挂」复用既有端点） |
| G-7b | ✅ | 同文件 8 组参数化 `test_naked_judgement_uses_the_real_wire_field_names` + `test_unverified_backstop_row_is_grey_with_a_note_never_red` |
| G-7c | ✅ | `aiTradeDiscipline.test.ts::"delivers a covered→naked flip without any error event"`（直接走 store 的 `patchAiTradeMemberStatus`）；**未推任何 `error` 事件**，`routeMemberEvent` 一字未改 |
| G-8 | ✅ | 全量 `pytest tests -q`：**3349 passed / 7 failed**，7 条全部落在 `test_factor_library_expansion.py`（见下「红灯基线实测对账」）。`test_ai_trade_prior_session.py` 由 7 failed → **15 passed** |
| G-9 | ✅ | `CandleChart.test.tsx::"markerForEvent close marker color"` 三条 |
| G-10 | ✅ | `test_digest_has_thirteen_rows_in_a_fixed_order_and_never_leaves_grey_unexplained` |
| G-11 | ✅ | `test_every_preset_survives_the_real_validator`（`_env_file=None` 隔离，[P2-4] 已落实） |
| G-12 | ⚠️ 部分 | `session.py` 净增 **25** 行（`git diff --numstat` = 27 增 / 2 删，终审未再动它）；`discipline.py` 232 行、`discipline_runtime.py` 139 行、`discipline_view.py` **304 行——超出 spec 自设的 300 行子预算 4 行**（终审补 F-1 的 dust 两态文案所致，已就地抵掉 `_row` / `_brake` 两层单点转发）。CLAUDE.md 的**硬规则**（单文件 ≤ 800）无一文件触碰。`build_discipline_digest` 形参 1（`DisciplineFacts` 聚合体） |
| G-13 | ✅ | `test_profit_guard_threshold_comes_from_the_preset_not_the_config_default`（正序 0.50 / 逆序 0.0 对拍）+ `test_ai_trade_api.py` 的既有默认用例改为断言 0.50（见 I-9） |
| G-14 | ✅（终审补齐一半） | `test_dust_risk_is_disclosed_in_the_receipt_but_never_blocks_the_start`（启动期不 422、如实回执 + 复核结论随执行器走）+ `test_dust_risk_warns_on_the_vol_sizing_row_before_the_first_skip_happens`（**终审新增**：第一次跳过发生**之前** digest 就示警）+ `test_dust_skips_surface_on_the_vol_sizing_row`（运行期计数上屏）。终审前 digest 侧是空转的，见 F-1 |
| G-15 | ✅ | `test_ai_trade_backtest_discipline.py` 5 条：显式 3 > 预设、单策略夹到 1 **且该 1 确实放行**、反证不夹紧必被拦死 |
| G-16 | ✅ | `test_strictness_hint_fires_only_after_enough_rounds_with_zero_opens`（正反两个断面） |
| G-17 | ⚠️ **欠账** | 见下「G-17 校准证据」 |
| G-18 | ✅ | `test_ops_master_switch_beats_the_request_body`（`is` 相等） |

### 零回归门禁（§7.1）

- `off` / `enabled=false` / 运维总闸关三条路径均返回**同一个** settings 对象（`is` 相等）：
  `test_user_off_returns_the_very_same_settings_object` / `test_ops_master_switch_beats_the_request_body`。
- 直接构造 `AiTradeSession` / `AiTradeGroupStartRequest` / `LiveExecutor` 的既有用例**一行未改**
  （新形参全部带零回归默认值）。四处**测试替身**因 `group_status()` 新增读点而同步（见 I-8），
  那不是行为变更。
- `live_auto` 链路：`bar_seconds=0` ⇒ `ts` 逐字回退且**整键无 `wallTs`**（比 spec 原文更强，见 I-3）。

### 红灯基线实测对账（放行条件 7）

在 `git worktree add <HEAD>` 的**干净检出**上实跑，不凭历史记忆：

| 文件 | 干净 HEAD | 本轮工作树 | 判定 |
|---|---|---|---|
| `tests/test_ai_trade_prior_session.py` | **7 failed / 7 passed** | **15 passed** | 本轮修好（D-6 / E-2） |
| `tests/test_factor_library_expansion.py` | **文件不存在于 HEAD**（`git status` 显示 `??`，未跟踪的孤儿文件） | 7 failed | **不属于本轮**，也不属于已提交的树 |

故本轮工作树的全量结果 = 3349 passed + 7 failed，且 7 条全部来自一个未跟踪的孤儿文件；
**已提交树内 0 failed**。`test_agent_ws` 的顺序 flake 本次未复现。

前端：`NODE_ENV=test npx vitest run components/Agent components/Chart lib` → **90 文件 / 1291 passed**
（本轮新增用例后重跑相关文件同样全绿）。`tsc --noEmit` 只剩既有的
`components/Factor/WeightBatchArena.test.tsx` 类型错误（合入前即存在，非本轮引入）。

### G-17 校准证据（⚠️ 欠账，如实记录）

**要求**：同区间同标的各跑一次 `discipline=off` 与 `balanced` 的 AI 回测，把两条净额曲线终值、
`vetoRate`、成交笔数写进本节。

**本轮状态**：**未取得**，如实记欠账。

实际做了什么、观察到什么（不含推测）：
- Agent provider **是配置好的**（`resolve_active_provider` 返回 `kimi / kimi-k2.6`），故不能简单
  归因为「provider 不可用」。
- 已按 R-3 的**真实**路径写好对照脚本并实跑：`_resolve_discipline(preset)` →
  `AiBacktestManager._build_spec` → `AiBacktestRunner.run`，两臂（`off` / `balanced`）同标的
  `BTC-USDT-SWAP`、同区间（近 5 天 1H）、`decisionEveryBars=4`（约 30 轮/臂）。
- 运行 **50 分钟后仍未跑完两臂**，期间日志只出现 4 条 `ai-trade summarize failed:`（空错误串）
  ——即上下文摘要那条非工具 LLM 调用在本机持续失败；决策轮本身在推进但速度远低于预算。
- 因**没有任何可信的终值可写**，本节点主动终止该运行，**不写任何数字**。凑一个半程数字或用
  单臂结果代替对照，都比留空更坏。

**这不影响本轮其余判据**：G-17 只是校准证据，五轴的机制正确性由 G-1~G-16 / G-18 的用例承担。

**据此的纪律（[P1-10]）**：本轮交付的任何 UI 文案与文档**都不得**出现「已校准 / 经回测验证」。
已落实的反向保证：
- 弹窗纪律分区显式写「这组阈值……**尚未经回测校准**」，并有用例
  `AiTradeLaunchModal.test.tsx::"不得宣称阈值「已校准」"` 与
  `aiTradeDiscipline.test.ts::"never claims the thresholds are calibrated"` 双向守门；
- `DisciplineCard` 底部同样写明「本组阈值尚未经回测校准」；
- `discipline.py` 模块 docstring 写明「这些数字没有经过回测校准，本轮只承诺**可观测、可回退、可校准**」。

**下一轮怎么还这笔账**（路径已经铺好，不需要新代码）：回测侧已吃同一份 `_resolve_discipline`，
且 `metricsJson.aiStats` 现在会落 `disciplinePreset` / `disciplineArmedKeys` /
`disciplineArmedCount`（[P2-6] 已落实，不是只落一个整数），故「开纪律 vs 不开纪律」的净额对照
可以直接在历史列表上做，无需再造工具。

### 新增 / 变更文件清单

**后端（services/quant）**：新建 `app/ai_auto/discipline.py`、`app/ai_auto/discipline_view.py`、
`app/ai_auto/discipline_runtime.py`（第三个模块的理由见 I-5）；修改 `app/config.py`、
`app/schemas.py`、`app/api/ai_auto.py`、`app/ai_auto/manager.py`、`app/ai_auto/session.py`、
`app/ai_auto/backtest_manager.py`、`app/ai_auto/sizing.py`、`app/ai_auto/sizing_runtime.py`、
`app/live_auto/executor.py`、`app/db/repositories.py`。

**前端（apps/web）**：新建 `lib/aiTradeDiscipline.ts`、
`components/Agent/aiTradeStats/DisciplineCard.tsx`；修改 `lib/aiTradeTypes.ts`、`lib/types.ts`、
`lib/aiBacktestTypes.ts`、`lib/aiTradeHorizonApi.ts`、`lib/autoTradePnl.ts`、
`lib/useAiTradeGroupStream.ts`、`components/Agent/AiTradeLaunchModal.tsx`、
`components/Agent/AiTradeToggle.tsx`、`components/Agent/aiTradeStats/AiTradeStatsLive.tsx`、
`components/Chart/AiTradePanel.tsx`、`components/Chart/CandleChart.tsx`、
`components/Chart/TradeTooltip.tsx`、`components/Chart/TradeCandleChartPanel.tsx`、
`components/Strategy/TradesTable.tsx`、`components/Strategy/AutoTradeTradesTable.tsx`。

**测试**：新建 `tests/test_ai_trade_discipline.py`、`tests/test_ai_trade_discipline_session.py`、
`tests/test_ai_trade_backtest_discipline.py`、`tests/test_ai_trade_trade_clock.py`、
`apps/web/lib/aiTradeDiscipline.test.ts`；修改 `tests/test_ai_trade_prior_session.py`、
`tests/test_ai_trade_wake_context_session.py`、`tests/test_ai_trade_api.py`、
`tests/test_ai_trade_group.py`、`tests/test_auto_trade_admission.py`、
`tests/test_ai_trade_backstop_repair_session.py`、`tests/test_ai_trade_acceptance_wiring.py`、
`apps/web/components/Chart/CandleChart.test.tsx`、
`apps/web/components/Agent/AiTradeLaunchModal.test.tsx`。

**接口增量**：新增只读端点 `GET /ai-trade/discipline-presets`（§4.2 要求弹窗有「展开后的只读阈值
表」，而阈值的唯一真源在后端冻结表——前端抄一份必漂移。沿用 `/ai-trade/horizon-profiles` 的成文
先例：不守总闸、数值与中文标签全部后端下发）。

**提交注意（给下游 commit 节点）**：`services/quant/app/config.py` 是**混合文件**——工作树里
另有一处**不属于本轮**的在制改动（`ai_trade_enabled` 默认值 `False → True`，及其配套的
`services/quant/tests/test_config.py` 两条新用例、`.env.example`、`desktop/src/lib/config.js`）。
本轮只在 `config.py` 末尾新增了 `ai_trade_discipline_*` 两个字段，与那处 hunk **不重叠**，
须按 hunk 精确暂存。

---

## 终审记录（Final Review · 2026-08-12）

**方式**：按 §四 变更计划逐文件对读 diff，并在合入前实跑
`pytest tests -q -k "ai_trade or ai_auto or ai_backtest"`（**1486 passed / 0 failed**）与
`NODE_ENV=test npx vitest run components/Agent components/Chart lib`（**90 文件 / 1296 passed**）。
五轴覆盖属实，18 条判据的证据逐条对得上代码。下列三条为终审**直接改在源码里**的问题（不留 TODO）。

### F-1（阻断级）§3.1.5 的三件套只落地了两件半：digest 那半边是一条**空转的读**

`discipline_view._vol_sizing_row` 读的是 `facts.gate_counts.get("volSizingNote")`，而全仓
**没有任何一处写这个键**（`grep volSizingNote` 只有这一个命中）。后果正是 G-14 想防的断面：
启动期复核出「按预设保守下界解算的委托量可能低于 `minSz`」之后，这句话只进了启动回执
`discipline.adjustments`（用户看一眼就走的一次性文案），而**会一直挂在主面板上**的那一行
`volSizing` 仍然是一片正常的绿——「每一轮开仓都将被静默跳过」与「一切正常」再次长得一模一样。
这是本轮自己反复申明要杜绝的形状，且它就长在 [P0-4] 这条最可能劝退用户的线上。

**已修**（判据只算一次，两个消费者读同一份）：
- `manager._dust_risky(req, config, budget, lever)` 抽为**唯一判据**，`_note_dust_risk`（回执）与
  `_build_executor`（面板）都调它——两处各写一遍，回执与面板迟早给出相反结论；
- `LiveExecutor.dust_risk`（默认 `False`，只有 `ai_auto` 的 `_build_executor` 置真）承载复核结论，
  与同轮已有的 `dust_skips` 计数并排；
- `DisciplineFacts.dust_risk: bool = False` + `_vol_sizing_row` 的**两态**文案：
  运行期已发生 ⇒ 报「已有 N 次跳过」；尚未发生但复核出风险 ⇒ 报「可能低于最小下单量」；两者
  皆无 ⇒ `note=None`、`tone=None`（不吓唬人）。`volSizingNote` 这条死读同时删除。
- 用例：`test_ai_trade_discipline_session.py::test_dust_risk_warns_on_the_vol_sizing_row_before_the_first_skip_happens`
  （事前示警 → 真跳过后改口）+ `test_ai_trade_api.py` 的既有 G-14 用例补一条
  `executor.dust_risk is True`（该文件的会话是桩，digest 在那里验不到，故只守前半段）。

### F-2　`consensus` 行的 note 在默认路径上是一句**假话**

`_consensus_row` 原文在 `need >= total > 0` 时一律写「已按本会话策略数 N **夹紧**」。但 digest
只看得到夹紧**之后**的 settings，区分不出两种断面：`steady`+单策略（门槛 2 → 真被夹到 1）与
`balanced`+单策略（门槛本来就是 1，**一次都没夹**）。后者恰是默认预设最常见的断面，用户在那里
会读到一句服务端根本没做过的动作。**已修**：改写为在两种断面下都成立的措辞（「门槛已到本会话
策略数上限……服务端按策略数做过夹紧，绝不让它高于策略数」），G-3 的既有断言（note 含「夹紧」）
不变。

### F-3　`lib/aiTradeDiscipline.ts` 的文件头注释与实现相反

头注释第 2 条写「`isNakedPosition` 与后端 `naked_backstop` **逐字同构**」，而函数本身（正确地）
只读后端判好的 `tone === "danger"`，并在自己的 docstring 里写明「绝不在前端重算判据」。一条描述
着「本文件采用了一个它其实没有采用的方案」的注释，会把下一个改这里的人直接引到坑里。**已修**：
改写为「不重算、只读后端判好的那一行；判据的唯一真源在 `discipline_view.naked_backstop`」。

### F-4　I-1 的落库改造在 `schemas.py` 留下一个**没有调用者**的 `_parse_explain`

`trade_out` 为了拿 `meta.wallTs` 改走 `_explain_envelope`，并把 `kind` 过滤就地内联了一行，于是
`_parse_explain`（原先唯一的调用点就是它）变成死函数——而它的 docstring 恰好是「对外 explain
口径」这条契约的唯一书面处，留着不动，下一个人会以为那条口径还有人在守。**已修**：改写为
`_public_explain(envelope)`（收**已解析**的信封，让 `trade_out` 只解析一次），`trade_out` 调它；
契约与调用者重新合到一处。

### 终审确认无误、不改的部分

- 轴 D 的锚点式对齐：K 线轮 `wall == anchor ⇒ 返回 anchor`，对 `1D` 的港时相位逐字节不变；
  `_align_bar_ts` 为纯函数，两条降级（`bar_seconds<=0` / `bar_anchor_ts<=0`）都有用例。
- I-3 的「`wallTs` 缺失即省略键」比 spec 原文**更强**，且与 fee_ledger 那条键集冻结门禁相容。
- I-7 把 `live_auto/executor.py` 移出冻结集时，用「键集冻结 + 取值冻结」两条**行为**断言顶上，
  而不是留下一个被悄悄放宽的间接证据——这是移出冻结集的正确姿势。
- `gate_counts` 的 11 个计数来源逐个核对属实（含 `horizon_runtime.backstop_vetoes` 确为
  `apply_backstop_gate` 的否决计数，归到 `exchangeBackstop` 行是对的）。
- G-17 保持 ⚠️ 欠账，四处「尚未经回测校准」的反向文案与两条双向守门用例均在，未见任何
  「已校准 / 经回测验证」措辞。

### 合入范围（本次 commit 只含本特性）

工作树里另有**不属于本轮**的在制改动，终审已逐一剔除、保留未暂存：
`ai_trade_enabled` 默认值翻转（`config.py` 的该 hunk + `.env.example` + `desktop/src/lib/config.js`
+ `tests/test_config.py` 两条新用例）、`db/live_auto_repositories.py`、
`tests/test_factor_library_expansion.py`（未跟踪孤儿文件，全量下的 7 条红灯即来自它）、
`desktop/scripts/*wincodesign*`、以及各类临时目录与诊断文档。
