# AI 自动交易 · 执行周期趋势基调（ai-trade-execution-trend-verdict）

> **版本**：**v2**（评审节点修订，2026-09-01；v1 = 方案设计节点产出）
> **状态**：评审完成 · **有条件通过**（见文末《评审结论》）
> **作者**：方案设计节点（Subtask #0）；**评审与 v2 修订**：评审节点（Subtask #1），2026-09-01
> **需求来源**（用户原话，逐字保留，勿转述）：
> 「当前项目的 AI 自动交易功能，目前交易的时候的这样的一个 Agent 研判和思考的这样的一个过程存在问题。就是比如说当前现在开启了 15 分钟级别的这样的一个自动交易的时候，那么它对应于交易研判的时候的第一步，应该要先判断当前 15 分钟是处于一个上升趋势还是下降趋势。第一步的研判将决定后续它交易的买多或者卖空是到底操作哪个方向。就如果整体处于上升势的话，那么它就尽量要去买多，后续开仓。如果整体处于下降势的话，那它整体后续的这样的一个开仓就要尽量偏向于卖空。这个是作为一个基本的研判依据，就是总的一个基调。当然呢，这个不是固定死的，还是要根据 Agent 后续的这样的一个思考，做出最终的一个方向决定。目前呢，就是它是一直发生了，对应的情况是一直在买多，这个是不对的，需要把这个问题纠正修复一下。
> 另外呢，就是用户所选择的这样的一个自动交易的级别是 15 分钟级或者半小时级或者是一小时级或者是等等之类的。用户选择哪种级别，那么它对应于这样的一个趋势判断，就要在哪一个级别上去判断当前所处的这样的一个市场趋势。当然呢，它这个研判市场趋势的时候，是要综合各个级别的线去分析的，但是最终它判定的就是用户所选择启动自动交易，AI 自动交易的这样的一个 K 线级别。比如说他选择了 15 分钟级，那么他就要去判断 15 分钟级当前所处的市场是上跌、上涨趋势还是下降趋势。但是它研判的时候是要综合去研判，结合其他的 K 线图去一起去看的。」
>
> **唯一实施依据**：本文自包含。实现者不需要读任何历史 spec；本文引用历史 spec 只是为了说明「为什么某个写法在本仓库是硬约束」。

---

## 评审记录（Review Notes · v1 → v2）

> 评审口径：逐节对照仓库**实际代码**核实，每一条都给出可复核的代码位置；**P0 / P1 已直接改进正文**，
> 正文（而非本节）是唯一实施依据。本节只是差异索引，供实现者知道「v1 的哪一句已经不作数了」。
> P2 只登记、不改，留给实现节点自行取舍。

### P0（阻断级：不改，本特性会在它宣称要修的那个断面上失效）

| id | 位置 | 结论与修法 |
|---|---|---|
| **RP0-1** | v1 §3.3.2 / §3.6.2 | **顺势追加票的判据不携带本轮动作**。v1 的 `trend_consensus_bonus(verdict, consensus, rule)` 只吃基调与信号板，产出的整数却被 `resolve_consensus_gate` 无条件加到**本轮决策自己那一侧**的票上（`signals.py:367-372` 的 `votes` 取自 `decision.action`）。于是「基调偏多、模型开空」这一轮，空头方向会白拿一票——与本通路声称的「顺势才追加」正好相反，且**不报任何错**。修法：`trend_consensus_bonus` 改为**首参吃 `action`**，内部先过 `alignment_of(action, verdict) == "aligned"`。已改 §3.3 签名、§3.3.2、§3.6.2、§7.2 D-11。 |
| **RP0-2** | v1 §2.4 / §2.5 / §3.3.2 | **D-1 恰恰在它被指派去解决的断面上恒不生效，且它的残余效果偏向用户抱怨的那个方向**。v1 §2.4 说「信号板一边倒时做空被共识门禁按住 ⇒ 轴 D-1 正是为这一段而设」，而 §3.3.2 的第三条硬约束是「该方向上信号板已有 ≥1 票」——板子一边倒看多时，做空的原生票数恰好是 0，追加票被自己的护栏挡死。更糟的是反向：上行行情里「强多基调 + 已有 ≥1 张多票」是常态，于是**本方案唯一放宽门禁的改动只会为做多点亮**。修法：① 如实改写 §2.4 / §2.5（D-1 的作用域只有「差一票」的断面）；② 另立 **D-1b「顺势放宽逆势通路的反向票上限 +1」**——它**不发免费票**，仍要走既有逆势通路的全部代价（`contrarianEvidence` / 置信度下界 / 会话配额 / 强制 ×0.5 降档），只把 `contrarian_max_opposing_votes` 抬高 1，正对 `_contrarian_block` 首行那条 `opposing > max_opposing` 的死路（`signals.py:399-403`）。两条通路各带独立总闸。 |
| **RP0-3** | v1 §3.2.4 判定顺序 4 / 5 + §3.3.1 | **强度取自含高周期的合成分，再叠加「冲突降档」，使两条调制通路在目标断面上同时熄火**。v1 的 `strength` 由 `&#124;score&#124;`（含 `htf`）判，而规则 5 又在 `conflict != "none"` 时把 `strong` 降为 `moderate`；轴 D-1 与 D-2 的触发条件都是 `strength == "strong"`。于是「日线看多、15m 翻空」——**正是本需求的目标断面**——基调必然带 `htf_conflict`、必然被降档、两条通路必然不咬。等价于本特性只剩文案。修法：新增 **`execScore`（只由执行周期五项合成）**，`strength` **一律取自 `&#124;execScore&#124;`**；`conflict` 不再改写 `strength`，改为按**极性**分流：冲突只**收紧**（撤销 D-1 的放宽），绝不**放松**（不豁免 D-2 的逆势门槛）。已改 §3.2.3 / §3.2.4 / §3.2.5 / §3.3.1 / §3.3.2 / §7.1。 |
| **RP0-4** | v1 §3.5 / §九-6 | **最后一道闸仍被逐字要求否掉本特性想放行的单**。复核官的评级标准写着「与**高周期**强冲突且无对冲理由 ⇒ `abort`」——中英两处逐字存在：`prompts_en.render_review_spine_en` 的 GRADING STANDARD 2(b)、`prompts.build_reflection_prompt`（`prompts.py:1037-1040`）的中文散文，以及协议 R4 的 mandate。而反思层位于**整条链的最下游**（`session.py:1696`，共识与 edge 之后）。v1 却在 §3.5 与非目标 6 里明令「复核协议一个字节都不改」。结果：15m 翻空、模型据此开空、前面每一层都放行，最后被复核官按「与高周期冲突」否掉，且这是它被**明文指示**要做的事。修法：给 R4 与两条评级标准各追加一句**条件化**子句（仅在 `payload["trendVerdict"]` 在场时装配 ⇒ 缺席即逐字节回退）：与高周期冲突**只有在同时与执行周期基调冲突时**才算实质性缺陷。复核官「只能收紧、不能改方向、不能放大」的安全语义**一个字节不动**。已改 §3.5、§九-6、§4.1、§7.4。 |
| **RP0-5** | v1 §3.7.1 / §3.7.5 / §7.3 S-6 | **顺势 / 逆势的成交与盈亏没有跨轮载体**。v1 的 `note_fill(side, alignment, pnl)` 只能读 `TrendRuntime.verdict`，而那是**当前轮**的基调；开仓腿可能跨轮到达（限价挂单，`attribution._take_estimate` 就是为这件事存在的），已实现净额盈亏更是要到**平仓结算**（`attribution.settle_close`）才出现，那时距开仓可能隔了几十轮、基调早已翻面。照 v1 实现，卡面第 4 行「顺势 p 笔净额 ±U / 逆势 q 笔净额 ±V」会被系统性地记到错误的一侧，而单轮用例（S-6）恒绿。修法：alignment **在开仓委托发出的那一刻定格**，随 `clOrdId` 挂进一张与 `AttributionLedger._pending_estimates` 同款的认领表，成交与结算都从**那张表**取；认领不到 ⇒ 计入 `unknown`，**绝不猜**。已改 §3.7.1 / §3.7.5 / §5.4 / §7.3。 |

### P1（会让某段功能静默失效、或让实现者按字面写出错误代码）

| id | 位置 | 结论与修法 |
|---|---|---|
| **RP1-1** | v1 §3.7.1 | `TrendRuntime.note_intent(action, verdict)` **缺 `degrade` 判据**。开仓漏斗的分母判据是 `wants_open = (action in _OPEN_ACTIONS and degrade is None) or degrade in _OPEN_ONLY_DEGRADES`（`open_funnel.py:163-179`）——`low_confidence` 轮此刻 `action` 已被 `_map_decision` 洗成 `hold`（生产实测占开仓意愿 21%）。照 v1 签名实现，`intents` 会把**每一轮**（含纯观望轮）都计进去，卡面那行「开仓意愿」就退化成轮数计数器。修法：签名补 `degrade`，判据**逐字复用** `open_funnel` 的那一条，不另写。 |
| **RP1-2** | v1 §3.7.5 | **实时统计卡没有数据源**。`AiTradeStatsLive.tsx:234` 读的是 `m.session?.openFunnel`（**成员级 status 快照**），`AiTradeStatsDetail.tsx:164` 读的才是 `aiStats?.openFunnel`。v1 只把 wire 落进 `aiStats`，于是实时页那张卡恒为空——`session.py:3066-3070` 上方注释已白纸黑字写着「后端算了、类型补了、没有渲染落点 = 死数据」。修法：`_status_payload` 同步新增 `"trendVerdict": self._trend.stats_wire()`，与 `openFunnel` 并列。 |
| **RP1-3** | v1 §3.7.2 | `discipline_runtime._DEGRADE_PREFIXES` 那条新增是**有写点没读点**。它只喂 `self._degrades`，而 `_gate_counts()` 的产物被 `build_discipline_digest` 按**固定 13 行** `discipline_view.ROW_KEYS` 取用（`discipline_view.py:23-33/81`）——没有 `trendBias` 这一行，计数写进去就蒸发。v1 说「症状是纪律自检里少一格计数」，实情是「一格都不会多，改了等于没改」。修法：要么补齐第 14 行（`ROW_KEYS` / `_LABELS` / `_BUILDERS` + 一个 row builder + 前端 `DisciplineCard`），要么**不加这条前缀**。本 v2 选前者并把四个落点写进 §3.7.2 与 §4.1。 |
| **RP1-4** | v1 §3.4.3(b) | **决策纪律 #1 的改写文本没给**。v1 只说「插在『输入提供可用多周期证据时，』之后、原句之前，并把原句改写为其续写」。按字面实现会渲染出「…再按 1D → 12H → 4H → 1H 把中线背景当作佐证来读，**先按 1D → 12H → 4H → 1H 研判中线背景**…」——同一句读序连说两遍且互相矛盾。修法：§3.4.3(b) 现给出中英两条脊柱**armed / disarmed 两态的逐字全文**，并把它收成**一个**函数（返回整条子句），只留一处接缝。 |
| **RP1-5** | v1 §八 RK-9 | **未承接 `.claude-index/config.md` 的硬要求**。该文件第 241 行（第四次登记）写着：「把 `_format_*` 家族整体外移到 `ai_auto/prompt_sections.py`……**下一个触碰本文件的特性必须先还这笔债**」。本方案要动 `prompts.py`（`format_trend_charter` / `_trend_read_order` / `build_decision_prompt` 插段），RK-9 却只承诺「把增量登记进欠账表」。修法：§十 交付顺序新增**第 0 步**——先做一次**零行为变更**的外移提交（`_format_*` → `prompt_sections.py`，同一批 sha256 冻结断言证明输出逐字节不变），再动本特性；若实现节点判定该外移风险高于收益，**必须**在 `.claude-index/config.md` 写第五次登记并说明理由，不得静默跳过。 |
| **RP1-6** | v1 §3.2.4 / §3.4.2 / §7.1 A-7 | **三处算例互相矛盾**，而本仓库的实现者会把 spec 的算例直接抄进 fixture。① §3.2.4 的示例 wire：五个成分按其自报权重合成得 `score ≈ −0.21`，wire 却写 `−0.41`；`availableWeight` 应为 `1.02/1.18 ≈ 0.86`，wire 写 `0.82`；`confidence = &#124;score&#124;/0.55 ≈ 0.38`，wire 写 `0.75`；`downgraded: true` 却又写 `strength: "strong"`。② §7.1 A-7 的 `0.48/1.02`：分母应是**全部权重和 1.18**（五项 1.00 + `htf` 0.18），`1.02` 是把「可用权重」误当成了分母。③ §3.4.2 的渲染样例复读了同一批错数。修法：三处算例全部重算并互相对齐（见 v2 正文）。 |
| **RP1-7** | v1 §3.2.5 I-1 | **I-1 的证明不成立于「部分成分缺失」的断面**。v1 称主导性「由 `_HTF_WEIGHT_CAP` 保证」，但只覆盖了「执行周期五项全在」的情形。若只剩 `regime`（0.26）可用而 `htf`（封顶 0.30）在场，`htf` 权重**大于**执行周期可用权重，合成分当场被高周期决定。它今天不炸，只是因为 `_MIN_AVAILABLE_WEIGHT=0.5` 与 `_HTF_WEIGHT_CAP=0.30` 之间恰好留了余量（成立条件是 `h < m/(2−m) = 1/3`）——一条**没人写下来、没人测**的隐含契约。修法：把主导性做成**构造性保证**——新增 `_MIN_EXEC_WEIGHT = 0.5`（执行周期可用权重占五项总和的下界），不达标即 `degraded`；于是任何非降级的基调都满足「执行周期可用权重 ≥ 0.5 > 0.30 ≥ htf 权重」，与配置无关。用例 A-3b 冻结缺失断面。 |
| **RP1-8** | v1 §3.3.1 ① | **逆势闸比的是谁的 `confidence` 没写**。`verdict["confidence"]` 与 `decision.confidence` 都叫 confidence，而 §5.3 的 wire 两个都在。取错方向相反：取 verdict 的，等于「基调越强越容易开逆势单」。修法：正文明确为 **`decision.confidence`（模型自报置信度）**，与既有逆势通路 `_contrarian_block` 的判据同源；`verdict["confidence"]` 只用于呈现。wire 字段改名为 `modelConfidence` / `verdictConfidence` 消歧。 |
| **RP1-9** | v1 §3.8-3 | **回测的 `gate_reason` 合成未加新项**。`backtest.py:918-921` 是一条「八元合成、先执行者胜出」的显式表达式；只插一个 `apply_trend_bias_gate` 调用而不把 `trend_reason` 并进那条链，被拦轮会变成「决策是 hold、没有任何降级码」——回测的漏斗与统计当场对不上，且**不报错**。修法：§3.8 明写九元合成及其位置（刹车之后、共识之前）。 |
| **RP1-10** | v1 §5.1 | **回测启动路径没有 `_resolve_trend`；两个请求体没有 `trend` 字段**。实盘走 `api/ai_auto.py:394`，回测走同文件 `:846`，`_resolve_direction` 两处都调了。v1 只写了 `POST /ai-trade/start`，于是「回测里调出来的参数在实盘生效」这条承诺在**反方向**上破了（回测永远只吃 settings 默认值）。修法：§5.1 补齐两个接线点与两个请求体字段。 |
| **RP1-11** | v1 §八 RK-4 | **RK-4 的缓解措施没有实现落点**。它承诺「`contextDisclosure="repeat"` 的压缩轮同样压缩基调段」，而 §3.4.2 给的签名 `format_trend_verdict_for_prompt(verdict)` 根本拿不到档位。压缩轮的既有做法是 `_multi_timeframe_lines` / `_digest_lines` 各自读 `payload.get("contextDisclosure")`（`prompts.py:658-682`）。修法：改为 `format_trend_verdict_for_prompt(verdict, tier)` 并给出 `brief` 形态的逐字文案。 |
| **RP1-12** | v1 §八 RK-1 缓解④ / §5.1 | **「用户能立刻关掉逆势闸」没有接口**。RK-1 把它列为最后一道防线，但 `AiTradeTrendIn` 与启动弹窗都没有 `counterGateEnabled`，只有一个运维 env。修法：`AiTradeTrendIn` 补 `counterGateEnabled` 与 `consensusVoteEnabled`（后者 v1 已有）并进弹窗；env 仍是运维总闸。 |
| **RP1-13** | v1 §3.5 | **`_with_trend_first` 与 `_with_symmetry` 的复合未定义**。`build_deliberation_protocol` 今天只有一条 `stages = _with_symmetry(...) if symmetry else DECISION_STAGES`（`reasoning_protocol.py:449`）。两个总闸互相独立，四种组合都必须成立。修法：正文写明**先 symmetry 后 trend_first** 的固定复合顺序（两者的阶段集合 S7/S10/S11 与 S1/S2/S12 不相交，顺序不影响产物，但顺序必须被写死并冻结），用例 P-4b 覆盖四种组合。 |

### P2（登记不改，留给实现节点）

| id | 结论 |
|---|---|
| RP2-1 | `_consensus_wire` 今天已是 6 形参（CLAUDE.md 上限 5），本轮再加三键会到 9。建议同轮收成一个 frozen 聚合入参，别再往位置参数上堆。 |
| RP2-2 | `.env.example` 在**仓库根**，不在 `services/quant/`；§4.1 的表格把它列在 `services/quant/` 段落下，照字面找会找不到文件。 |
| RP2-3 | v1 §7.4 P-6 用「当前自管持仓」作下标锚点，但空仓轮 `_format_position` 渲染的是「当前无自管持仓（flat）」（`prompts.py:450-455`），该断言在空仓轮会取不到下标。v2 已改用「自管持仓」这一在两态下都出现的子串。 |
| RP2-4 | 回测 payload **没有** `tacticalContext`（`backtest._build_payload` 全文无该键，也不调 `_wake.inject`），故 `tacticalAlignment` / `tactical_conflict` 在回测侧结构性不可达。v1 称回测「与实盘逐字同构」是过度承诺；v2 已在 §3.8 如实披露。 |
| RP2-5 | 统计桶名 `neutral` 与卡面文案「无基调 / `unknown` 单列」不是同一个词，两处将来必然分叉。建议统一为 `unknown`（与 `open_funnel.SIDES` 的既有姿态一致）。 |
| RP2-6 | v1 §3.7.3 的第三条裸 `counter_trend` 兜底条目**永不命中**（后端只产出两个细分码）。留着是廉价保险，但按 CLAUDE.md「删除僵尸代码」应显式说明它是有意为之。 |
| RP2-7 | `session.py` 净增「≤ 30 行」在补齐 RP0-5 的认领表、RP1-1 的判据与 RP1-2 的 status 键之后不现实，实测预算约 45–55 行。请照实登记，别为了守住一个报出来的数字而把纯核心塞进会话。 |
| RP2-8 | 「补上 `LAYER_LABELS["trendBias"]` 中文层名就自动到位」只在**方向对称总闸开着**时成立——`tool_call` 相位的 `layerLabel` 由 `ai_trade_directional_symmetry_enabled` 门控（`session.py:2223-2226`）。总闸关的会话仍退化成泛指的「门禁」。 |

---

## 一、概述（Overview）

【AI 自动交易】是一个由 LLM 担任决策者、由十余层确定性门禁担任安全带的永续合约自动交易 Agent。它每一轮拿到三层时间尺度的证据（中线背景 1D/12H/4H/1H、执行周期、战术周期 1m/5m），按一份《审议协议》分十二个阶段（S1…S12）写出思维链，最后调用一次 `submit_trade_decision` 给出 `open_long / open_short / close / hold` 之一。

本轮要修的是这套研判的**第一步锚点错了**。用户选择 15m 启动自动交易，系统却把「先读 1D → 12H → 4H → 1H 的中线体制」写成了协议的 S1、写成了决策纪律 #1 的第一句、也写成了决策 prompt 里排在最前的那一段证据；用户所选的 15m 级别只落到 S2「执行周期结构」，而它的 mandate 原文是「**说明执行周期与 S1 是否一致，并量化分歧**」——即执行周期在协议里的定位是「对 S1 的附议或异议」，不是「结论本身」。再叠加协议 S3 那句硬约束「战术周期**绝不能**单独反转 S1 的结论」，整条链路在结构上就把「基调」永久性地钉死在了日线级。于是在一段以周为尺度的上涨行情里，1D/12H/4H/1H 长期同为 `trend_up`，模型每一轮的第一步都得到「整体=bullish」，后面十一个阶段都在这条基调下做加减法——**「一直在买多」不是模型的偏好，是协议的结构性产物**。这与用户「15m 级别自己是涨是跌，才是这一轮的基调」的诉求正好相反。

本方案因此做五件事，并且刻意**不**做第六件（不设任何方向配额、不强行拉平多空比）：

1. **轴 A**：新增一个纯核心 `trend_verdict.py`，在**用户所选执行周期**上算出一份确定性的《趋势基调》——方向（up / down / range）、强度、**两个连续分**、以及逐成分可解释的证据。两个分是刻意分开的（评审 RP0-3）：`execScore` **只由执行周期五项**算出、决定**强度**；`score` 含高周期调制、决定**方向**。高周期与战术周期只作为**调制项**参与，权重被硬性封顶且执行周期可用权重有独立下界，**永远无法翻转执行周期的结论，也永远不能抬高或压低它的强度**（这正是需求那句「综合各级别去看，但判在用户所选级别」的可执行译文）。
2. **轴 B / C**：把这份基调作为决策 prompt 的**第一段证据**注入，并把《审议协议》的 S1 改成「先在执行周期上给出趋势判定，再把 1D→12H→4H→1H 当作对该判定的佐证读」，S2 追加「执行周期结构若与 S1 冲突，必须就地重算 S1 并说明改了什么」，S12 追加「必须写明本轮基调、本次动作是顺势还是逆势、逆势则给出理由」。基调是**软引导**：模型仍有权推翻它，但必须出声地推翻。
3. **轴 D**：把「尽量偏向」落成三条**方向严格对称**的确定性调制（评审 RP0-2 把 v1 的两条拆成三条）——(a) **D-1a 顺势追加一票**：顺势且基调强、且信号板已有 ≥1 票同向时，为共识门禁追加一票（「锦上添花，不雪中送炭」，只解决「差一票」）；(b) **D-1b 顺势放宽反向票上限 +1**：顺势且基调强时，把既有逆势通路的反向票上限抬高 1——它**不发票**，逆势通路的逐字证据、置信度下界、会话配额与强制 ×0.5 降档一条不减，专治「信号板一票都没有」那个断面；(c) **D-2 逆势收紧**：逆势且基调强时，开仓需过一道模型自报置信度下界并**强制降档**，不达标退回 hold。三条通路对 `open_long` 与 `open_short` 走**同一段代码**，不含任何按 side 的字面分支；「放宽」的两条与「收紧」的那条各有独立总闸，可分别回退。
4. **轴 C′（评审 RP0-4 新增）**：复核官（反思侧）的三处「与高周期强冲突 ⇒ abort」判据必须**条件化**——顺着执行周期基调的提议，高周期分歧不单独构成实质性缺陷。它是决策链的最后一米；不改它，前面十层放行的单会全部死在这里，而那是复核官被明文指示要做的事。复核官「只能收紧、不能改方向、不能放大」的安全语义一个字节不改。
5. **轴 E / F**：把「本轮基调是什么、动作顺势还是逆势、逆势被拦了几次、顺势与逆势各自的成交与盈亏」做成一等的、可证伪的产品事实（trace 相位 + 开仓漏斗新层 + 统计卡片），并让实盘 session 与 AI 回测 runner **共用同一份纯核心**——用户在回测里调出来的基调参数，在实盘必须逐字生效。

修完之后，「上涨行情里做多占多数」应当仍然成立，但它必须是**在 15m 上论证出来的结论**，而不是**协议结构强加的唯一出口**；而当 15m 转入下行段时，系统必须在 prompt 的第一段就说出「基调=偏空」，并让做空在门禁上与做多享有对称待遇。

---

## 二、故障分析（根因）

### 2.1 根因 R-1：协议的第一步锚在高周期，执行周期被降格为「附议」

`services/quant/app/ai_auto/reasoning_protocol.py` 的 `DECISION_STAGES`：

- **S1 `REGIME` / "Higher-timeframe regime"**：`Read the higher-timeframe context strictly in the order 1D -> 12H -> 4H -> 1H … End with one sentence naming the dominant regime …`
- **S2 `EXECUTION` / "Execution-bar structure"**：`… State whether the execution bar agrees or disagrees with S1, and quantify the disagreement.`
- **S3 `TACTICAL`**：`Tactical frames … may **never** on their own reverse the S1 conclusion.`

三句连起来的语义是：**基调由 S1（高周期）产生，S2/S3 只能附议或提异议**。用户的诉求是基调由执行周期产生。这是根因，且它是纯文本层面的，不涉及任何数值计算。

同一句锚点在系统 prompt 里还有两份镜像，改协议不改它们等于没改：

- `prompts_en.py::render_spine_en` 决策纪律 #1：`Work the higher-timeframe context first (1D -> 12H -> 4H -> 1H) …`
- `prompts.py::_render_spine_zh` 决策纪律 #1：`先按 1D → 12H → 4H → 1H 研判中线背景…`（中文脊柱是 `off` 档真源，**另有一份内联拼接、不调 `charter()`**，这是 directional-symmetry 评审 P1-4 的成文教训）

### 2.2 根因 R-2：执行周期在多周期证据里**根本不存在**

`multi_timeframe_context.py:13-21`：

```python
_CONTEXT_POLICY = {
    "1m":  ("1H", "4H", "12H", "1D"),
    "5m":  ("1H", "4H", "12H", "1D"),
    "15m": ("1H", "4H", "12H", "1D"),
    "30m": ("1H", "4H", "12H", "1D"),
    "1H":  ("4H", "12H", "1D"),
    "4H":  ("12H", "1D"),
    "1D":  (),
}
```

策略是「**严格高于**执行周期」，执行周期本身不入帧。于是：

- `trendAlignment.status`（渲染成 prompt 里那句「**中线背景：整体=bullish**」）是**只由高周期算出来的**——它读起来像「本轮的总基调」，实际上里面**一根 15m K 线都没有**。
- 15m 自己的方向只存在于两个位置：`payload["marketContext"].primary`（渲染成一行「市场体制：趋势结构=range，波动=…」）与 `payload["marketDigest"].priceAction`（三行读数）。两者都排在中线背景**之后**，且都没有「这是本轮基调」的语义标注。

### 2.3 根因 R-3：执行周期的 `primary` 大多数时候给不出方向

`marketContext.primary` 来自 `engine/regime.py::_decide_structure`：

```python
if er >= params.er_trend_min and net_change > 0 and align_up:   return "trend_up"
if er >= params.er_trend_min and net_change < 0 and not align_up: return "trend_down"
return "range"
```

`er_trend_min = 0.35` 是一个相当高的方向效率门槛（30 根窗口内净位移 / 路径长度 ≥ 0.35）。短周期上大部分时间落在 `range`。也就是说，**即使模型去看执行周期，它拿到的也常常是一个不表态的标签**，自然会退回去用会表态的那个（高周期）。本方案的《趋势基调》不是再造一个 regime，而是把 regime、均线结构、净变化、区间结构、动量五类**已经算好**的读数按权重合成一个**连续分**，让「弱上行」这种 regime 判不出来的态也能表态。

### 2.4 根因 R-4（并发因素，已有前轮修复但不足）：共识门禁在单边行情里只拦逆势那一侧

`signals.resolve_consensus_gate` 的 docstring 已明写这条（生产实测：回归窗口 1540 次开仓意愿里 832 次死在本层），并已提供一条「逆势通路」（`contrarianEvidence` + 置信度下界 + 会话配额 + 强制降档）。但那条通路的判据是**信号板净立场**，与本轮要引入的「执行周期趋势基调」是两个不同的量：策略信号板一边倒的时候，即使 15m 基调已经翻空，做空仍会被共识门禁按住。

**这里必须把两种「一边倒」分开说清楚（评审 RP0-2 的修订）**，因为它们的出口完全不同：

- **差一票**（`steady` 档 `min_consensus=2`，该方向已有 1 票）：这是**轴 D-1a（顺势追加一票）**的全部作用域。它只把 1 票补成 2 票，绝不无中生有。
- **一票都没有**（板子清一色看多，做空原生票数 = 0，且反向票数 ≥ 2）：D-1a 的第三条硬约束（「该方向须已有 ≥1 票」）在这里**恒不满足**，追加票被自己的护栏挡死；同时既有逆势通路的第一道判据 `opposing > contrarian_max_opposing_votes`（默认 1，`signals.py:399-403`）也把它挡死。**两条出口同时关闭**——这才是「做空被结构性排除」的真正物理位置。为它另设 **轴 D-1b（顺势放宽反向票上限 +1）**：它**不发免费票**，只把逆势通路的反向票上限抬高 1，该通路的其余全部代价（`contrarianEvidence` 逐字证据、置信度下界、会话配额、强制 ×0.5 降档）一条不减。

两条通路各带**独立总闸**，且都只在基调 `strength == "strong"` 时生效；它们是本方案里仅有的两处会让门禁变宽的改动，硬约束见 §3.3.2。

### 2.5 根因 R-5（评审 RP0-4 新增）：最后一道闸被明文指示否掉本特性想放行的单

反思复核官位于整条决策链的**最下游**（`session.py:1696`，共识与 edge 门禁之后、真实下单之前），而它的评级标准里逐字写着「与**高周期**强冲突且无对冲理由 ⇒ `abort`」。这句话在仓库里有**三份**：

- `prompts_en.render_review_spine_en` 的 `GRADING STANDARD` 第 2(b) 条：`it strongly conflicts with the higher-timeframe context and the decision offers no hedge rationale`；
- `prompts.build_reflection_prompt`（`prompts.py:1037-1040`）的中文散文：「与高周期强冲突且无对冲理由」；
- 协议 R4 `CONFLICT` 的 mandate：`Check the proposal against the higher-timeframe regime …`。

于是本特性的目标断面会走出这样一条完整链路：15m 翻空 → 基调判偏空 → 模型据此提议 `open_short` → 基础策略 / 持仓周期 / 冷却 / 换手 / 刹车 / 基调闸 / 共识 / edge **全部放行** → 复核官按「与高周期冲突」`abort`。它不是 bug，是复核官**被明文要求**做的事。v1 §3.5 与非目标 6 却明令「复核协议一个字节都不改」——那等于把本特性的最后一米交给一条与它方向相反的指令。

修法是**条件化**而不是删除（详见 §3.5）：与高周期冲突，只有在**同时与执行周期基调冲突**时才算实质性缺陷；当提议与执行周期基调一致时，高周期的分歧已经由基调段如实披露过一次，不再第二次构成否决理由。复核官「只能收紧、不能改方向、不能放大」的安全语义**一个字节不动**。

### 2.6 结论

R-1 / R-2 / R-3 是本轮的主线（协议 + 证据 + 判定）；R-4 与 R-5 分别是这条主线在**开仓门禁**与**下单前复核**这两道下游闸上的必要条件。五者缺一：只改协议，模型说了偏空也开不出空单；只改共识门禁，单子会死在复核官手里；只改门禁不改协议，模型从来不说偏空。

---

## 三、技术设计（Technical Design）

### 3.1 分层与数据流

```
                    ┌──────────────────────── 已存在，本轮不改取数 ────────────────────────┐
MarketWindow ──►  base_df (执行周期窗口)  ──►  build_market_context()  ──►  marketContext
             ├─►  context_frames()        ──►  build_multi_timeframe_context() ──► multiTimeframeContext
             └─►  tactical_frames()       ──►  build_tactical_context()  ──►  tacticalContext
                    base_df                ──►  build_market_digest()    ──►  marketDigest
                    └──────────────────────────────────────────────────────────────────────┘
                                     │  （四份 wire，已在 payload 里）
                                     ▼
                      ★ trend_verdict.build_trend_verdict(TrendInputs, TrendVerdictRule)
                                     │
                                     ├─► payload["trendVerdict"]  ──► prompts 第一段证据（轴 B）
                                     ├─► trace round_start 相位   ──► 前端证据行（轴 E）
                                     └─► 门禁与统计（轴 D / 轴 E）
```

**关键设计取舍（务必照做）**：`build_trend_verdict` 的入参是**四份已经算好的 wire**，不是 DataFrame。理由有三：

1. **零新增计算**：不再开第二个因果窗口、不再跑一遍 `classify_regime`（`min_bars=60`，是本链路里最贵的一次纯计算）。一个决策轮的 CPU 增量约等于二十几次字典取值。
2. **零口径漂移**：本仓库已有成文教训——同一个量在两处各算一次，一旦漂移就会出现「窗口在看 A、prompt 却在讲 B」，且**不报任何错**（见 `tactical_context.py` 顶部为什么跨模块引用 `_causal_frame`）。
3. **实盘与回测天然同源**：`session._build_decision_payload`（`session.py:2853-2857`）与 `backtest.py:1082-1085` 各自都已经把这四份 wire 装进 payload，轴 A 在两侧的接入各只有 3 行。

### 3.2 轴 A：`trend_verdict.py` 纯核心

新建 `services/quant/app/ai_auto/trend_verdict.py`（纯函数、无 IO、无 await、可离线单测；预计 ~300 行，远在 800 行线内）。

#### 3.2.1 规则对象与唯一转换点

```python
@dataclass(frozen=True)
class TrendVerdictRule:
    enabled: bool                 # 总闸；False ⇒ build_trend_verdict 恒返回 None
    enter_threshold: float        # |score| ≥ 该值才给方向（对称死区）
    strong_threshold: float       # **|execScore|** ≥ 该值记 strong（评审 RP0-3：不含高周期）
    htf_weight: float             # 高周期调制项权重（**被封顶**，见 I-1）
    counter_gate_enabled: bool    # 轴 D-2 逆势降档闸
    counter_min_confidence: float # 逆势开仓的**模型自报**置信度下界（RP1-8）
    counter_size_mult: float
    consensus_vote_enabled: bool     # 轴 D-1a 顺势追加一票
    contrarian_allowance_enabled: bool  # 轴 D-1b 顺势放宽逆势通路反向票上限（评审 RP0-2 新增）


def trend_rule_from_settings(settings) -> TrendVerdictRule:
    """settings → TrendVerdictRule 的**唯一**转换点（镜像 signals.consensus_rule_from_settings）。"""
```

`trend_rule_from_settings` 内做两处**夹紧**（不抛异常，一个拼错的运维开关不该让会话起不来）：

- `strong = max(strong_threshold, enter_threshold + 0.05)`——两个阈值倒挂时 `strength` 语义会崩。
- `htf_weight = min(htf_weight, _HTF_WEIGHT_CAP)`，`_HTF_WEIGHT_CAP = 0.30`。

**不变式 I-1（执行周期主导性）的证明方式已按评审 RP1-7 改为构造性**：v1 只说「由 `_HTF_WEIGHT_CAP` 保证」，那只覆盖「执行周期五项全在」的情形；只剩 `regime`（0.26）可用而 `htf`（封顶 0.30）在场时，高周期权重反而**大于**执行周期可用权重，合成分当场被高周期决定。v2 因此不再依赖 `_MIN_AVAILABLE_WEIGHT` 与 `_HTF_WEIGHT_CAP` 之间那条没人写下来的隐含余量（其成立条件是 `htf_weight < _MIN_AVAILABLE_WEIGHT / (2 − _MIN_AVAILABLE_WEIGHT) = 1/3`），改为显式新增一条门槛：

```python
_MIN_EXEC_WEIGHT = 0.5   # 执行周期五项的**可用权重之和**下界（相对五项总和 1.00）
```

`execAvailableWeight < _MIN_EXEC_WEIGHT` ⇒ 整段 `degraded`。于是**任何非降级的基调**都满足「执行周期可用权重 ≥ 0.50 > 0.30 ≥ `htf_weight`」，主导性与配置取值**无关**、可一句话证明、由用例 A-3 / A-3b 冻结。

`consensus_rule_from_settings` 的那条成文警告在此**逐字适用**：传进来的必须是 `_resolve_discipline` **之后**那份派生 settings 副本（`executor.settings` / `runner.settings`），读进程级单例会拿到一组死字母阈值。

#### 3.2.2 入参聚合结构（守 `CLAUDE.md` 形参 ≤5）

```python
@dataclass(frozen=True)
class TrendInputs:
    execution_bar: str          # 用户所选级别，判定所在的级别（wire 里的 `bar`）
    regime: dict | None         # payload["marketContext"]
    digest: dict | None         # payload["marketDigest"]
    multi_timeframe: dict | None  # payload["multiTimeframeContext"]
    tactical: dict | None       # payload["tacticalContext"]（可选，只参与 conflict，不参与打分）
    as_of_ts: int
```

#### 3.2.3 六个成分与打分（全部为奇函数）

| key | 中文标签 | 取数 | 打分 `s ∈ [-1,1]` | 权重（模块级常量） |
|---|---|---|---|---|
| `regime` | 体制结构 | `regime.primary` + `regime.confidence` | `trend_up → +max(conf, _REGIME_FLOOR)`；`trend_down → −…`；`range → 0.0` | `0.26` |
| `ema` | 均线结构 | `digest.priceAction.emaFast/emaSlow/emaSlopePct` | `0.5·sign(emaFast−emaSlow) + 0.5·clamp(emaSlopePct/_SLOPE_FULL,−1,1)` | `0.22` |
| `netChange` | 净变化 | `digest.priceAction.changePct` | `clamp(changePct/_CHANGE_FULL_PCT, −1, 1)` | `0.20` |
| `structure` | 区间与连阳连阴 | `priceAction.rangePos` / `consecUp` / `consecDown` | `0.5·(2·rangePos−1) + 0.5·clamp((consecUp−consecDown)/_CONSEC_FULL,−1,1)` | `0.16` |
| `momentum` | 动量 | `digest.factors.macdHist.value` / `factors.rocPct.value` | `0.5·sign(macdHist) + 0.5·clamp(rocPct/_ROC_FULL,−1,1)` | `0.16` |
| `htf` | 高周期背景（**调制项**） | `multiTimeframe.trendAlignment.status` | `bullish → +1`；`bearish → −1`；`mixed`/`range` → `0`；`insufficient` → **缺失** | `rule.htf_weight`（默认 `0.18`，封顶 `0.30`） |

模块级常量（**刻意不做成 `AI_TRADE_` env**，沿用 `market_digest.py` 顶部「指标周期与阈值为模块级常量，规避三处联动」的成文姿态）：

```python
_REGIME_FLOOR = 0.35        # regime 表了态但 confidence 很低时的分数地板
_SLOPE_FULL = 0.6           # emaSlopePct 打满的绝对值（%）
_CHANGE_FULL_PCT = 2.0      # changePct 打满的绝对值（%）
_CONSEC_FULL = 4.0          # 连阳/连阴差打满的根数
_ROC_FULL = 3.0             # rocPct 打满的绝对值（%）
_MIN_AVAILABLE_WEIGHT = 0.5 # 全部成分的可用权重占比下界，低于它整段判 degraded
_MIN_EXEC_WEIGHT = 0.5      # **执行周期五项**的可用权重下界（评审 RP1-7；I-1 的构造性保证）
_HTF_WEIGHT_CAP = 0.30
```

**两个合成分（评审 RP0-3 新增，实现前务必读完本段）**：

```
execScore = Σ(wᵢ·sᵢ) / Σ(wᵢ)      # 只累加执行周期五项（regime / ema / netChange / structure / momentum）
score     = Σ(wⱼ·sⱼ) / Σ(wⱼ)      # 五项 + htf，即含高周期调制的合成分
```

- `direction` / `bias` 取自 **`score`**——高周期因此仍能把一个边缘的方向拉回死区（「削弱」），但永远无法翻转（I-1）。
- `strength` 取自 **`|execScore|`**——它是轴 D 两条通路**唯一**的触发条件，绝不能含高周期。v1 把 `strength` 建在 `|score|` 上，再叠加「冲突降档」，其净效果是：**只要高周期与执行周期不一致，两条通路就同时熄火**，而那恰恰是本需求的目标断面（日线看多、15m 翻空）。
- 五项权重之和恒为 `1.00`（`0.26+0.22+0.20+0.16+0.16`），加上 `htf` 默认 `0.18` 后全部权重之和为 **`1.18`**。这个数在 §7.1 的算例里要用到，**不是 1.02**（v1 的 `1.02` 是把某个断面的「可用权重」误当成了分母，评审 RP1-6）。

**⚠️ 三条实现期必踩的坑，必须照本节写法实现，否则对称性用例 I-2 必红：**

1. **`ema` 成分不得读 `emaRelation` 标签**。`market_digest._ema_structure` 的 `relation = "fast_above" if fast_last >= slow_last else "fast_below"`——**相等时判多头**，这是一个真实存在的（极小的）单向阀。必须按 `emaFast − emaSlow` 的**数值符号**打分（相等 ⇒ 0）。
2. **`momentum` 成分不得读 `macdHist.label`**。`_macd_reading` 的 `label = "bullish" if last > 0 else "bearish"`——**hist 恰为 0 时判空**，同样不对称。必须按 `macdHist.value` 的数值符号打分（`0 → 0`，`None → 该子项缺失`）。
3. **缺失成分不得按 0 计入分母**。`score = Σ(wᵢ·sᵢ) / Σ(wᵢ)`，分母只累加**实际可用**的成分权重。把「不知道」当成「中性」会系统性地把分数往 0 稀释，表现是「数据越缺，基调越像 range」，而这正是本仓库明令禁止的「把没有的事实画出来」。

#### 3.2.4 产物 wire（camelCase，`build_trend_verdict` 的返回值）

```jsonc
{
  "bar": "15m",                       // 判定所在的级别 = 用户所选执行周期
  "asOfTs": 1756700000,
  "score": -0.32,                     // 含 htf 的合成分 ∈ [-1,1]，正=偏多 —— 决定 direction
  "execScore": -0.61,                 // **只由执行周期五项**合成 —— 决定 strength（评审 RP0-3）
  "direction": "down",                // "up" | "down" | "range" | "unknown"(degraded)
  "bias": "short",                    // "long" | "short" | "neutral"
  "strength": "strong",               // "strong" | "moderate" | "weak"；判据是 |execScore|
  "verdictConfidence": 0.59,          // = min(1, |score| / strong_threshold)；**纯呈现**，
                                      //   绝不参与轴 D 的任何判定（RP1-8）
  "components": [                     // 逐成分可解释；顺序固定，缺失项也在列（score=null）
    {"key": "regime",    "label": "体制结构",     "score": -0.72, "weight": 0.26,
     "detail": "primary=trend_down，判定置信 0.72"},
    {"key": "ema",       "label": "均线结构",     "score": -0.68, "weight": 0.22, "detail": "…"},
    {"key": "netChange", "label": "净变化",       "score": -0.55, "weight": 0.20, "detail": "…"},
    {"key": "structure", "label": "区间与连阳连阴","score": -0.40, "weight": 0.16, "detail": "…"},
    {"key": "momentum",  "label": "动量",         "score": null,  "weight": 0.16,
     "detail": "macdHist 与 rocPct 均不可用"},
    {"key": "htf",       "label": "高周期背景",   "score":  1.0,  "weight": 0.18,
     "detail": "1D/4H 同为 trend_up；仅作调制，不决定 15m 基调"}
  ],
  "missing": ["momentum"],            // 不可用的成分 key（绝不补猜）
  "availableWeight": 0.86,            // Σ 可用权重 / Σ 全部权重 = 1.02 / 1.18
  "execAvailableWeight": 0.84,        // Σ 可用的**执行周期**权重 / 1.00（RP1-7 的 I-1 判据）
  "higherTimeframe": {"status": "bullish", "agree": false,
                      "bullishBars": ["1D","4H"], "bearishBars": []},
  "tacticalAlignment": "diverged",    // 直接透传 tacticalContext.alignment；缺席 ⇒ null
  "conflict": "htf_conflict",         // "none" | "htf_conflict" | "tactical_conflict" | "both"
  "bonusWithheld": true,              // 冲突 ⇒ 撤销 D-1a/D-1b 的放宽（**只收紧，不放松**）
  "degraded": false,                  // 可用权重不足 ⇒ true，且 direction="unknown"、bias="neutral"
  "degradeReason": null               // "insufficient_components" | "disabled" 之一或 null
}
```

> **算例自洽性（评审 RP1-6）**：上面这组数是**算得出来的**，实现者可以直接抄进 fixture。
> `Σw·s = 0.26·(−0.72) + 0.22·(−0.68) + 0.20·(−0.55) + 0.16·(−0.40) + 0.18·(+1.00) = −0.3308`；
> 可用权重 `Σw = 1.02` ⇒ `score = −0.3243 → −0.32`；`availableWeight = 1.02/1.18 = 0.864 → 0.86`；
> 执行周期部分 `Σw·s = −0.5108`、`Σw = 0.84` ⇒ `execScore = −0.608 → −0.61`，
> `execAvailableWeight = 0.84/1.00 = 0.84`；`|execScore| ≥ 0.55 ⇒ strength = "strong"`；
> `verdictConfidence = 0.3243/0.55 = 0.59`。v1 那组数（`score −0.41` / `availableWeight 0.82` /
> `confidence 0.75` / `strong` 与 `downgraded:true` 并存）互相矛盾，已作废。

判定顺序（唯一真源，前端只做布局、一个判据都不复制）：

1. `rule.enabled` 为假 ⇒ **返回 `None`**（不是空 dict）⇒ payload 整键缺席 ⇒ prompt / trace / 门禁全链路**逐字节回退**到本特性合入前。
2. `availableWeight < _MIN_AVAILABLE_WEIGHT` **或** `execAvailableWeight < _MIN_EXEC_WEIGHT` ⇒ `degraded=True`、`direction="unknown"`、`bias="neutral"`、`strength="weak"`、`degradeReason="insufficient_components"`。**注意它返回的是一个对象而不是 None**——「算不出来」和「没装配」是两个不同的事实，前者必须让模型和用户看见。第二个判据是 I-1 的构造性保证（评审 RP1-7）：过了它，执行周期可用权重必 ≥ 0.50 > `htf_weight` 的封顶 0.30。
3. `score ≥ enter_threshold → up/long`；`≤ −enter_threshold → down/short`；否则 `range/neutral`。**取 `score`（含 htf）**：高周期因此可以把一个边缘方向拉回死区，但翻不动符号。
4. `strength`：`|execScore| ≥ strong_threshold → strong`；`≥ enter_threshold → moderate`；否则 `weak`。**取 `execScore`（不含 htf）**——理由见 §3.2.3 与评审 RP0-3：`strength` 是轴 D 两条通路唯一的触发条件，让高周期参与它，等于让高周期在目标断面上一键关停整个特性。
5. **冲突的作用被收敛为「只收紧、不放松」（评审 RP0-3 修订）**。`conflict != "none"` 时：
   - **撤销放宽**：`bonusWithheld=True` ⇒ 轴 D-1a 的追加票与 D-1b 的反向票放宽**当轮一律不生效**；
   - **不豁免收紧**：`strength` **不再被降档**，轴 D-2 的逆势门槛照常生效。
   - `direction` 在任何情况下都不翻转。
   v1 的「冲突把 strong 降为 moderate」被删除：它同时关掉了放宽与收紧两条通路，净效果是「高周期一有分歧，逆势开仓就免检」——极性正好反了。

#### 3.2.5 三条不可动摇的不变式（改本文件前先读）

- **I-1 执行周期主导性**：任何 `degraded=False` 的基调，其执行周期可用权重必 ≥ `_MIN_EXEC_WEIGHT`(0.5) > `_HTF_WEIGHT_CAP`(0.30) ≥ `htf_weight`；因此「执行周期各项一致看空 + 高周期满分看多」的 `score` 必然 < 0。**两个断面都要测**（评审 RP1-7）：`test_htf_can_never_flip_execution_bar_verdict`（五项全在）与 `test_htf_cannot_flip_when_components_are_partially_missing`（只剩两项、刚好过 `_MIN_EXEC_WEIGHT`）。
- **I-1b 强度不含高周期**：`strength` 只由 `|execScore|` 判。把任一执行周期成分取反会改变 `strength`；把 `htf` 从 `+1` 改到 `−1` **必须**保持 `strength` 逐字不变（用例 `test_strength_is_independent_of_htf`）。这是评审 RP0-3 的回归护栏。
- **I-2 符号对称性**：把所有输入的方向取反（`primary` 互换、`emaFast/emaSlow` 互换、`changePct`/`slope`/`macd`/`roc` 取负、`rangePos → 1−rangePos`、`consecUp ↔ consecDown`、`trendAlignment` 互换），产出的 `score` **与 `execScore`** 必须**严格等于原值取负**，`direction`/`bias`/`strength` 严格镜像。用例 `test_trend_verdict_is_sign_symmetric` 用构造 wire 对拍（**不跑 `classify_regime`**，理由见 §九 RK-2）。
- **I-3 只读**：本模块不持任何状态、不接受任何会话对象、不产生任何 degrade 码。所有会话级计数住在轴 E 的 `TrendRuntime`。

### 3.3 轴 A-2：顺势/逆势判定与两条调制通路（同文件，纯函数）

```python
# 纯函数的三态。**注意它与统计桶名的映射**（评审 RP2-5）：`TrendCounters` 的第三格叫
# `unknown`（与 `open_funnel.SIDES` 同姿态），承接的是「本轮确实是一次开仓意愿，但方向不可辨
# （`low_confidence`）或基调不可用」的那一格；`close` / `hold` 这类**根本不是开仓意愿**的轮次
# 压根不进 `TrendCounters.intents_by_alignment`（判据见 §3.7.1 的 `wants_open`）。
ALIGNMENTS = ("aligned", "counter", "neutral")

def alignment_of(action: str, verdict: dict | None) -> str:
    """本轮动作与基调的关系。close / hold / cancel_order 恒 `neutral`（**永不**被本层触碰）。

    `verdict is None` / `bias == "neutral"` / `strength == "weak"` / `degraded` ⇒ `neutral`。
    """

@dataclass(frozen=True)
class TrendGateOutcome:
    decision: TradeDecision
    reason: str | None       # 非 None ⇒ 本轮被降级为 hold
    lane: str                # "exempt" | "aligned" | "counter" | "veto"
    wire: dict               # 门禁判定的只读快照（trace / 注记 / payload 共用同一份）

def apply_trend_bias_gate(decision, verdict, rule, *, position_step: float) -> TrendGateOutcome:
    """逆势开仓的置信度下界 + 强制降档（轴 D-2）。"""

def trend_consensus_bonus(action: str, verdict: dict | None, consensus, rule) -> int:
    """轴 D-1a：顺势且基调强时给共识门禁追加的票数（0 或 1）。

    **首参必须是本轮动作**（评审 RP0-1）：`resolve_consensus_gate` 把返回值加到
    `decision.action` 那一侧的票上，判据不含动作 ⇒ 逆势开仓会白拿这一票。
    """


def trend_contrarian_allowance(action: str, verdict: dict | None, rule) -> int:
    """轴 D-1b：顺势且基调强时给**逆势通路**的反向票上限追加的额度（0 或 1，评审 RP0-2 新增）。

    它**不发票**，只把 `_contrarian_block` 首行那条 `opposing > contrarian_max_opposing_votes`
    的上限抬高 1；逆势通路的其余全部代价（逐字证据、置信度下界、会话配额、强制 ×0.5 降档）
    一条不减。这是「信号板一票都没有」那个断面唯一的出口（§2.4）。
    """
```

#### 3.3.1 `apply_trend_bias_gate` 判定顺序（顺序即降级码）

```
if not rule.counter_gate_enabled            -> lane="exempt"，原对象直通（is 相等）
if action not in ("open_long","open_short") -> lane="exempt"，原对象直通
if alignment != "counter"                   -> lane="aligned"/"exempt"，原对象直通
if verdict["strength"] != "strong"          -> lane="exempt"，原对象直通   # 只在基调强时才咬
                                            # `strength` 取自 |execScore|，与高周期无关（RP0-3）；
                                            # `conflict` 在这里**不豁免**任何东西（只收紧不放松）
① decision.confidence 非有限 或 < counter_min_confidence
      -> veto，degrade = f"counter_trend_confidence(score={execScore},conf={c}/{floor})"
② size = quantize_size_fraction(proposed * counter_size_mult, position_step)
   if size <= 0 or size > proposed or (mult < 1 and size >= proposed)
      -> veto，degrade = f"counter_trend_size(score={score})"
③ 放行 -> replace(decision, size_fraction=size)，lane="counter"
```

**②的三判据不得简化成 `size <= 0`**：这是 directional-symmetry 实施期发现的方案缺陷 I-1 的同款形状——在 `step=0.5` 这类粗档位上 `0.5×0.5=0.25` 会被量化吸附回 `0.5`，承诺的「强制降档」当场失效**且不报任何错**。

**①比的是谁的置信度（评审 RP1-8）**：是 **`decision.confidence`——模型本轮自报的置信度**，与既有逆势通路 `_contrarian_block` 的判据（`signals.py:405-407`）**同源**。**绝不是** `verdict["verdictConfidence"]`：取后者等于「基调越强越容易开逆势单」，极性正好反了。wire 里两个数各自有名字（`modelConfidence` / `verdictConfidence`），不得共用 `confidence` 一个键。

**fail-closed**：`decision.confidence` 非有限、`size_fraction` 缺失一律走否决分支（「无法验证」不等于「合规」）。

**方向对称性（I-4）**：本函数体内**不允许出现任何 `action == "open_short"` 的字面判断**；多空的差异只经 `alignment_of` 的一次比较产生。用例 `test_counter_trend_gate_is_side_symmetric` 把 verdict 与 action 同时镜像，断言两次 outcome 的 `lane` / `reason` 前缀 / `size_fraction` 逐字对称。

#### 3.3.2 两条顺势放宽通路及其硬约束（评审 RP0-1 / RP0-2 重写）

两条通路**各带独立总闸**，共享四条前置条件，且都对 `open_long` / `open_short` 走同一段代码：

```
共享前置（缺一即返回 0）：
  verdict 可用、not degraded、bias != "neutral"、strength == "strong"
  且 alignment_of(action, verdict) == "aligned"     # ★ 判据必须含本轮动作（RP0-1）
```

**D-1a 顺势追加一票**（`trend_consensus_bonus`，总闸 `rule.consensus_vote_enabled`）：

```
再额外要求：
  verdict["conflict"] == "none"                    # 冲突 ⇒ bonusWithheld ⇒ 不放宽（只收紧不放松）
  且 该方向上信号板已有 >= 1 票                     # 「锦上添花，不雪中送炭」
满足则返回 1，否则 0（上限恒为 1，不随强度放大）
```

它的作用域**只有「差一票」**：在 `balanced`（`min_consensus=1`）下它只影响本来就要放行的轮；真正吃到红利的是 `steady`（`min_consensus=2`）——一条策略同向 + 强基调同向 ⇒ 放行。**它解决不了「信号板一票都没有」那个断面**（v1 §2.4 声称它能，是错的，见评审 RP0-2）。

**D-1b 顺势放宽逆势通路的反向票上限**（`trend_contrarian_allowance`，总闸 `rule.contrarian_allowance_enabled`）：

```
再额外要求：
  verdict["conflict"] == "none" 或 verdict["conflict"] == "htf_conflict"
      # 战术周期背离（tactical_conflict / both）⇒ 不放宽：战术分歧说的是「此刻不是入场时机」，
      # 而本通路放宽的恰恰是入场资格
满足则返回 1（即 `contrarian_max_opposing_votes + 1`），否则 0
```

它是「板子清一色看多、做空原生票数 0、反向票数 ≥ 2」这个断面的**唯一**出口。要点：

1. **它不发票**。`votes` 一张不加，`effective` 一张不涨；它只把 `_contrarian_block` 首行那条上限抬高 1。因此一条「零策略支持、纯靠基调」的单**仍然开不出来**——逆势通路的 `contrarianEvidence`（≥60 字符逐字证据）、置信度下界、会话配额（默认 3 次）与强制 ×0.5 降档一条不减。
2. **`htf_conflict` 不构成阻断**，这正是与 D-1a 的分野。理由是需求原话：判定级别是用户所选的执行周期，高周期只是调制项；而这条通路放行的单已经付了逆势通路的全部代价。
3. **两条通路各自的效果必须能被单独关掉**，也必须能被单独归因（`consensus` wire 的 `trendBonusLane` / `trendBonusVotes` 与 `trendOpposingAllowance` / `effectiveMaxOpposingVotes`，§3.6.2）。

**为什么这不是「把偏斜换个方向」**：两条通路的判据只经 `alignment_of` 一次比较产生方向差异，代码里没有任何 `action == "open_short"` 的字面分支（I-4）；而 `strength` 现在只由执行周期证据决定（RP0-3），15m 上的多空切换频率远高于日线。统计卡把「顺势 / 逆势各自的成交与净额盈亏」摆在同一张卡上（§3.7.5），若长期只为一侧点亮，用户能立刻看到并关掉对应总闸。

### 3.4 轴 B：证据注入与 prompt 渲染

#### 3.4.1 payload 键

`payload["trendVerdict"]`：**整键缺席**（不是 `None`）表示未装配 ⇒ `build_decision_prompt` 输出与本特性合入前**逐字节相同**。这是本仓库贯穿全仓的降级范式（`baseRules` / `sizingPlan` / `tradingHorizon` / `intel` 全同款）。

装配点两处，**必须都填**（只填一处会让实盘与回测分叉，`base_rules` P1-4 的成文教训）：

- `session._build_decision_payload`（`session.py:2853` 之后、`_wake.inject` **之前**）。注意排序：`trendVerdict` 需要 `tacticalContext`（`conflict` 判据），而 `tacticalContext` 由 `self._wake.inject(...)` 写入 ⇒ **必须排在 `_wake.inject` 之后、`_experience.inject_round` 之前**。经验层那句「必须是本方法的最后一步」不受影响。
- `backtest.py` 的 payload 装配（`backtest.py:1085` 之后）。回测走独立总闸 `ai_trade_trend_verdict_backtest_enabled`。

#### 3.4.2 渲染模块 `trend_text.py`

新建 `services/quant/app/ai_auto/trend_text.py`（沿用 `market_text.py` / `cost_text.py` / `wake_text.py` 的「文案模块 + 延迟导入」先例，`prompts.py` 已 1116 行、不得再涨）。

```python
def format_trend_verdict_for_prompt(verdict: dict | None, tier: str = "full") -> list[str]:
    """《本轮趋势基调》段；verdict 为 None ⇒ 空表（整段省略）。

    `tier`（评审 RP1-11）：`"full"` = 下方全量样例；`"brief"` = 压缩轮的两行形态。
    调用方按 `payload.get("contextDisclosure") == "repeat"` 取档，与 `_multi_timeframe_lines`
    / `_digest_lines` 的既有姿态逐字同构（`prompts.py:658-682`）——RK-4 承诺的「压缩轮同样压缩
    基调段」必须有这个形参才落得下来，否则每一个重复轮的 prompt 顶部都会多出 8 行。
    """
```

渲染样例（`degraded=False`）：

```
【本轮趋势基调 · 判定级别 15m（= 你正在交易的级别）】
基调：偏空（direction=down，强度=强）。执行周期分 -0.61（决定强度，只由 15m 证据算出）；
      含高周期调制后的合成分 -0.32（决定方向）。
判据（按权重）：体制结构 -0.72(0.26) · 均线结构 -0.68(0.22) · 净变化 -0.55(0.20) ·
              区间与连阳连阴 -0.40(0.16) · 高周期背景 +1.00(0.18，仅调制、不参与强度)
高周期背景：整体=bullish（1D/4H 为 trend_up）——与 15m 基调**冲突**。高周期可以把本级别基调的
              方向拉向中性，但**不能反转它**；本轮它也不会削弱 15m 基调的强度。
战术周期：与执行周期体制背离（diverged）。
成分缺失：动量（不得补猜）。
说明：这是系统按确定性规则在**你所选级别**上给出的总基调，是本轮研判的起点而不是命令。
      顺势开仓与逆势开仓在本系统里共用同一套仓位、成本、止损与门禁标准；逆势开仓需你自报更高的
      置信度并会被强制降档（当前：置信度 ≥0.65，仓位 ×0.5）。你有权推翻这个基调，但必须在 S12
      说明理由。
```

`tier="brief"`（`contextDisclosure="repeat"` 的压缩轮，评审 RP1-11）只渲染两行：

```
【本轮趋势基调 · 15m】偏空（强度=强，执行周期分 -0.61 / 合成分 -0.32），高周期 bullish 与之冲突。
逐成分明细与上一轮相同，本轮最新变化见下方执行周期证据段。
```

`degraded=True` 时（两档同形）只渲染两行：「基调不可用（成分缺失：…）；本轮不给方向基调，请自行从执行周期证据推导，**不得**用高周期背景冒充本级别基调。」

**位置**：`build_decision_prompt` 的 `parts` 列表中，插在**首行（决策轮/barTs/最新价/唤醒原因）之后、`_format_position` 之前**。它是本轮第一段实质证据，这是「第一步」在证据排序上的兑现。

#### 3.4.3 系统 prompt：第七段宪章 + 决策纪律 #1 的读序改写

**(a) 第七段宪章 `trend_text`**（软引导层）：给 `SystemPromptTexts` 增第七个文本字段 `trend_text`，并在**两处**追加——`prompts_en.py::SystemPromptTexts.charter()` 与 `prompts.py::_render_spine_zh` 里那份**内联的同款拼接**。只改一处，条款就只出现在一条语言路径上（directional-symmetry 评审 P1-4 的原文教训，本轮必须避免重演）。

新增 `prompts.py::format_trend_charter(bar: str, rule: TrendVerdictRule) -> str`，未装配 ⇒ **空串垫片** ⇒ 两条脊柱逐字节回退。文案（中文，与既有六段宪章同构）：

```
**趋势基调条款**：你正在交易 {bar} 级别，因此本轮的方向基调**判定在 {bar} 上**。
系统每轮会给你一份《本轮趋势基调》：它综合了 {bar} 的体制结构、均线结构、净变化、区间结构与动量，
并把更高周期作为**权重受限的调制项**——高周期可以削弱这份基调的强度，但**不能替你决定 {bar} 的方向**。
基调为偏多时，你的开仓应优先考虑做多；基调为偏空时，应优先考虑做空。这是起点，不是命令：
你可以逆着基调开仓，但必须在 S12 指名是哪一条 {bar} 级别的证据强到足以推翻它，
并接受更高的置信度门槛与强制降档。基调为中性 / 不可用时，本条款不产生任何偏好。
```

**(b) 决策纪律 #1 的读序**（评审 RP1-4 重写：v1 只说「插在某句之后、并把原句改写为其续写」，按字面实现会把「按 1D → 12H → 4H → 1H」的读序**连说两遍且互相矛盾**）。

修法是**把整条子句收成一个函数**，只留一处接缝、两态各给逐字全文：

```python
def _trend_read_order(trend_bar: str) -> str:
    """决策纪律 #1 的多周期读序子句（**整条**，含尾部逗号）。

    `trend_bar == ""` ⇒ 返回合入前的原句，逐字节回退（G-1 的 sha256 冻结据此成立）。
    """
```

- `trend_bar == ""`（**逐字回退**，一个字符都不能改）：

  ```
  先按 1D → 12H → 4H → 1H 研判中线背景、趋势一致性、候选支撑阻力与超买/超卖，
  ```

- `trend_bar == "15m"`（armed，**整条替换**上面那句）：

  ```
  先在执行周期 15m 上给出本轮趋势基调（方向 + 强度 + 最强反证），再按 1D → 12H → 4H → 1H 把中线背景、趋势一致性、候选支撑阻力与超买/超卖当作对该基调的**佐证**来读——高周期可以削弱这份基调的强度，但不能替你决定 15m 的方向，
  ```

调用点：`_render_spine_zh` 里把今天那段字面量替换为 `f"输入提供可用多周期证据时，{_trend_read_order(trend_bar)}{_tactical_discipline(...)}再依次思考："`。**原句不再以字面量出现在脊柱里**——它搬进了函数的 disarmed 分支，那是它唯一的落点，因此不可能出现「两句读序同时在场」。

- `prompts_en.py::_trend_read_order_en(trend_bar: str)`：英文孪生，同样**整条替换**今天那句 `Work the higher-timeframe context first (1D -> 12H -> 4H -> 1H): trend alignment, candidate\n   support and resistance, overbought/oversold. `，armed 全文：

  ```
  First state the trend verdict for the execution bar 15m (direction, strength, and
     the strongest evidence against it), then read the higher-timeframe context
     (1D -> 12H -> 4H -> 1H) as corroboration for that verdict: trend alignment,
     candidate support and resistance, overbought/oversold. Higher frames may weaken
     the verdict; they may not decide the direction on 15m for you.
  ```

  折行宽度与本模块其余各条纪律一致（渲染后 ~75 列，续行缩进 3 空格）——脊柱是一整块等宽正文，混进一条 140 列的长行会让模型读到的段落结构与其它条目不同（workstation 那轮的同款教训）。

`trend_bar` 的真源：`manager` / `backtest` 装配时取 `req.bar` / `self.bar`，**且总闸关 ⇒ 传空串**（不是传 bar 再在函数里判），使回退路径只有一条。

装配点两处（`manager.py:685-705` 与 `backtest.py:355-365`），与既有 `direction_text` / `reasoning_text` 并列填入。

### 3.5 轴 C：《审议协议》的 mandate 改写（决策侧三处 + 复核侧一处）

沿用 `_with_symmetry` 的既有范式，新增 `_with_trend_first(stages, bar)`，并给 `build_deliberation_protocol` 增一个 keyword-only 形参 `trend_bar: str = ""`（空串 ⇒ 三处 mandate **逐字节回退**，用例冻结）。

**与方向对称总闸的复合顺序（评审 RP1-13）**：今天那一行是 `stages = _with_symmetry(DECISION_STAGES, _SYMMETRY_MANDATES) if symmetry else DECISION_STAGES`（`reasoning_protocol.py:449`）。两个总闸互相独立，四种组合都必须成立，故复合顺序必须**写死**：

```python
stages = DECISION_STAGES
if symmetry:
    stages = _with_symmetry(stages, _SYMMETRY_MANDATES)   # S7 / S10 / S11
if trend_bar:
    stages = _with_trend_first(stages, trend_bar)          # S1 / S2 / S12
```

两者的阶段集合不相交（S7/S10/S11 vs S1/S2/S12），故顺序不影响产物——但**顺序必须被写死并冻结**，否则下一轮往任一集合里加一个阶段就会静默改变产物。用例 P-4b 覆盖 `(symmetry, trend)` 的四种组合。

- **S1（`REGIME`，题面由「高周期体制」改为「基调 + 佐证」；`id` / `code` **不变**——`id` 是稳定契约，前端与 `STAGE_HEADING_RE` 三处消费者都按它对拍）**，armed 版 mandate：

  > `First state the trend verdict for the execution bar {bar}, quoting the system-supplied 《本轮趋势基调》 block: direction, strength, the continuous score, and the single strongest piece of evidence against it. You may disagree with the supplied verdict, but you must say so explicitly and name the {bar} evidence that overrides it. Only then read the higher-timeframe context in the order 1D -> 12H -> 4H -> 1H, and for each frame state trend direction, structure (higher-highs / lower-lows / range), the nearest support and resistance with its distance in %, and whether RSI is at an extreme. Higher frames are corroboration for the {bar} verdict: they may weaken its strength, they may not by themselves reverse its direction - only stronger evidence on {bar} itself can. Mark any missing or stale frame explicitly; never infer a frame that was not given.`

- **S2（`EXECUTION`）**，armed 版**追加**一句（原文一字不改）：

  > ` If the structure you have just described contradicts the S1 verdict, re-derive the verdict here and state plainly what changed and why; do not carry a verdict forward that your own S2 analysis has just falsified.`

- **S12（`VERDICT`）**，armed 版**追加**一句：

  > ` State the round's trend verdict, whether your chosen action is with it (aligned) or against it (counter-trend), and - if counter-trend - the specific {bar} evidence that justifies going against it, together with the confidence floor and size multiplier you are accepting.`

前端阶段中文名（`apps/web/lib/aiTradeReasoning.ts::DECISION_TITLES`）：`1: "多周期背景"` → `1: "趋势基调与多周期背景"`。**S2 的中文名不变**。这个改名在总闸开与关两种形态下都是真话，因此不需要随总闸联动（后端总闸是运维回退开关，前端静态表跟着它联动只会多一条不必要的耦合）。

#### 3.5.1 复核侧（评审 RP0-4：v1 说「一个字节都不改」，那会让本特性死在最后一米）

**好消息先说**：基调证据段会**自动**进入复核官视野——`prompts.py::build_reflection_prompt` 第一行就是 `evidence = build_decision_prompt(payload)`，而反思用的是**同一个 payload**。这是一条已知的连锁通路（market-intel 那轮吃过一次亏：改决策 prompt 会同时改反思 prompt），本轮它正好是我们想要的，但**必须被用例钉死而不是碰巧成立**：`test_ai_trade_prompts.py` 补 `test_reflection_prompt_carries_trend_verdict`。

**坏消息**：复核官的**评级标准**仍逐字要求它否掉本特性想放行的那种单（根因 R-5，§2.5）。三份原文：

| 位置 | 原文 |
|---|---|
| `prompts_en.render_review_spine_en` GRADING STANDARD 2(b) | `it strongly conflicts with the higher-timeframe context and the decision offers no hedge rationale` |
| `prompts.build_reflection_prompt`（`prompts.py:1037-1040`） | 「与高周期强冲突且无对冲理由」 |
| `REVIEW_STAGES` R4 `CONFLICT` 的 mandate | `Check the proposal against the higher-timeframe regime and against every armed hard-constraint section …` |

三处各追加**一句条件化子句**，装配判据是「本轮 payload 里有 `trendVerdict`」⇒ **整键缺席即逐字节回退**。复核官「只能收紧、不能改方向、不能放大、`abort` 须指名实质性缺陷」的安全语义**一个字节不动**——改的只是「高周期分歧本身算不算一条实质性缺陷」这一个判据。

**(a) R4 mandate 追加句**（armed 版，`build_review_protocol(profile, *, symmetry, trend_bar="")`，空串 ⇒ 逐字节回退，用例 P-8 冻结）：

> ` When a trend verdict for the execution bar {bar} was supplied, judge the conflict against **that** verdict first. A disagreement with a higher timeframe is a substantive defect only when the proposal **also** runs against the {bar} verdict; when the proposal is aligned with the {bar} verdict, the higher-timeframe disagreement has already been disclosed in the verdict block and is not on its own a reason to abort. Say explicitly which of the two cases you are in.`

**(b) 英文评级标准 2(b)**：`render_review_spine_en` 的该条改为按 `trend_text` 是否装配分叉（新增 `SystemPromptTexts.trend_text` 已由 §3.4.3(a) 引入，此处**复用同一个字段**，不新增第八个字段）。armed 版把 `(b)` 改写为：

> `(b) it strongly conflicts with the execution-bar trend verdict and the decision offers no hedge rationale (a conflict with a higher timeframe alone, when the proposal follows the execution-bar verdict, is not a defect);`

**(c) 中文散文**：`build_reflection_prompt` 里那句「与高周期强冲突且无对冲理由」在 `payload.get("trendVerdict")` 在场时改渲染为「与**执行周期趋势基调**强冲突且无对冲理由（仅与高周期冲突、但顺着执行周期基调的提议，不构成缺陷）」；键缺席 ⇒ 原句逐字保留。

**装配点只有一个**：`decider._stream_reflection`（`decider.py:954-956`）是 `build_review_protocol` 在全仓的**唯一**调用点，且它手上就有当轮 `payload`。故 `trend_bar` 从那里现取：

```python
trend_bar = str((payload.get("trendVerdict") or {}).get("bar") or "")
```

**绝不**从 settings 再读一次（两处各读一次必漂移；且总闸关时 payload 本就无键，判据天然同源）。manager / backtest **从未**调用过 `build_reflection_system_prompt`——在那里注入是一条不报错、不判红、整条死掉的死路（`reasoning_protocol.build_review_protocol` 的 docstring 已成文警示）。

### 3.6 轴 D：门禁接线

#### 3.6.1 逆势降档闸（新层）

插入点：`session._run_decision_round` 里 `self._sizing.gate(...)`（回撤刹车）**之后**、`self._apply_consensus_gate(...)` **之前**。

```python
decision, degrade = self._sizing.gate(decision, degrade)          # 既有第 7 步
decision, degrade = self._apply_trend_bias_gate(decision, degrade)  # ★ 新增
decision, degrade = self._apply_consensus_gate(decision, consensus, degrade)
```

`session._apply_trend_bias_gate` 是薄封装（≤ 12 行，`session.py` 已 3390 行、**不得再往里塞纯核心**）：调 `apply_trend_bias_gate`，`degrade = degrade or out.reason`，把 `out.wire` 挂进 `self._round_trace["trendGate"]`，并推进 `self._trend.note_gate(out)`。

排在共识之前的理由：基调是「方向层」的判据、共识是「证据数量层」的判据，方向层先判使漏斗层序读起来是「先问往哪边、再问有几个人同意」；且被基调拦下的轮此时已是 hold，共识门禁对 hold 本就豁免（`resolve_consensus_gate` 首行 `decision.action not in _OPEN_ACTIONS` 直通），自动零成本。

#### 3.6.2 两条顺势放宽通路的接线（评审 RP0-1 / RP0-2）

`signals.resolve_consensus_gate` 增**两个** keyword-only 形参，**默认全 0 ⇒ 既有全部调用与单测逐字节回退**（含 `apply_consensus_gate` 那个三参薄封装）：

```python
def resolve_consensus_gate(decision, consensus, rule, *, position_step, used_contrarian,
                           bonus_votes: int = 0, opposing_allowance: int = 0): ...

effective = votes + max(0, int(bonus_votes))                                   # D-1a
max_opposing = rule.contrarian_max_opposing_votes + max(0, int(opposing_allowance))  # D-1b
```

- `effective` 参与 `effective >= rule.min_consensus` 的判定与 `low_consensus(k/n)` 的 `k`。
- `max_opposing` **只**替换 `_contrarian_block` 首行那条 `opposing > rule.contrarian_max_opposing_votes`；该函数其余三条判据（会话配额 / 置信度下界 / 逐字证据长度）与其后的强制 ×0.5 降档**一个字节不动**。

`_consensus_wire` 新增**五键**：`trendBonusVotes` / `effectiveVotes` / `trendBonusLane`（`"none" | "applied" | "blocked_no_native_vote" | "blocked_conflict" | "blocked_not_aligned"`）/ `trendOpposingAllowance` / `effectiveMaxOpposingVotes`。`votes` 与 `contrarianMaxOpposingVotes` 两键**语义不变**（仍是策略板原始票数与配置原值）。原值与合成值同时下发是硬要求：只发合成值，用户就无法回答「这一票是策略给的还是趋势给的」「这单是本来就够格还是被基调放宽了」。

> **形参预算（评审 RP2-1）**：`_consensus_wire` 今天已是 6 形参（CLAUDE.md 上限 5），本轮再加会到 9。实现时请把它收成一个 frozen 聚合入参（`ConsensusWireFacts`），并在 `.claude-index/config.md` 把这条偏离改成「已还」。

`session._apply_consensus_gate` 的传参（**首参必须是本轮动作**，RP0-1）：

```python
out = resolve_consensus_gate(
    decision, consensus, self._consensus_rule,
    position_step=self.decider.position_step, used_contrarian=self._contrarian_used,
    bonus_votes=trend_consensus_bonus(decision.action, self._trend.verdict, consensus,
                                      self._trend_rule),
    opposing_allowance=trend_contrarian_allowance(decision.action, self._trend.verdict,
                                                  self._trend_rule))
```

此处的 `decision` 是**过了基调闸之后**的那一个：被基调闸拦下的轮此刻已是 `hold`，`resolve_consensus_gate` 首行的 `_OPEN_ACTIONS` 判据会让它直通 `exempt`，两条放宽通路自动不生效。这是排序（基调闸在共识之前）带来的免费护栏，用例 S-2 冻结。

### 3.7 轴 E：观测

#### 3.7.1 会话运行时 `trend_runtime.py`

新建 `services/quant/app/ai_auto/trend_runtime.py`（会话门面，镜像 `discipline_runtime` / `prior_runtime` / `horizon_runtime` 的既有形态）：

```python
@dataclass
class TrendCounters:
    verdict_rounds: dict[str, int]       # {"up","down","range","unknown"} 各判了几轮
    intents_by_alignment: dict[str, int] # {"aligned","counter","unknown"} 开仓意愿
    blocked_counter: dict[str, int]      # {"confidence","size"} 逆势闸各拦了几次
    passed_counter: int                  # 逆势放行（**不是降级、不进 blocked**）
    bonus_lanes: dict[str, int]          # D-1a/D-1b 各点亮 / 各被哪条护栏挡下几次
    filled_by_alignment: dict[str, int]  # 开仓腿成交（alignment 取自认领表，不是当轮基调）
    pnl_by_alignment: dict[str, float]   # 已实现净额盈亏（USDT，结算时回流）

class TrendRuntime:
    verdict: dict | None                 # 本轮基调（只读出口，供门禁与统计）
    def note_verdict(self, verdict) -> None
    def note_intent(self, action: str, degrade: str | None, verdict) -> None
    def note_gate(self, outcome) -> None
    def note_submitted(self, cl_ord_id: str | None, alignment: str) -> None  # ★ alignment 定格
    def note_fill(self, trade: dict) -> None          # 认领 alignment 后计 filled_by_alignment
    def note_settled(self, trade: dict, net_pnl_usd: float) -> None  # 结算时回流净额盈亏
    def stats_wire(self) -> dict | None  # 未装配 / 零样本 ⇒ None（整键缺席）
```

**`note_intent` 必须带 `degrade`（评审 RP1-1）**：开仓意愿的判据是开仓漏斗那一条，**逐字复用、不另写**——

```python
wants_open = (action in _OPEN_ACTIONS and degrade is None) or degrade in _OPEN_ONLY_DEGRADES
```

（`open_funnel.py:163-179`）。`low_confidence` 轮此刻 `action` 已被 `_map_decision` 洗成 `hold`（生产实测占开仓意愿 21%），照 v1 的 `note_intent(action, verdict)` 实现会把**每一轮**（含纯观望轮）都计进 `intents`，卡面那行「开仓意愿」当场退化成轮数计数器。方向不可辨的开仓意愿（`low_confidence`）计入 `unknown` 一格，**绝不猜方向**（与 `open_funnel.SIDES` 的 `unknown` 同姿态）。

**alignment 的跨轮载体（评审 RP0-5，本节最重要的一条）**：`note_fill(side, alignment, pnl)` 这个 v1 签名在物理上做不到它承诺的事——

- 开仓腿**可能跨轮到达**（限价挂单；`attribution._take_estimate` 就是为这件事存在的，`attribution.py:108-112`）；
- 已实现**净额盈亏**要到**平仓结算**（`attribution.settle_close`）才出现，那时距开仓可能隔了几十轮、基调早已翻面。

读 `self._trend.verdict`（当轮基调）去归因，会把成绩系统性地记到错误的一侧，而单轮用例恒绿。故：

1. **alignment 在开仓委托发出的那一刻定格**：`_execute_decision` 拿到回执后 `note_submitted(cl_ord_id, alignment)`，把 `alignment` 挂进一张 `dict[str, str]` 认领表（形状与 `AttributionLedger._pending_estimates` 逐字同款，含同款的**轮末兜底清理**与容量上限）。
2. **成交与结算都从认领表取**：`note_fill` / `note_settled` 按 `trade["clOrdId"]` 认领；市价单没有 `clOrdId` 时回退到「本轮槽位」（同 `_take_estimate` 的两级姿态）。
3. **认领不到 ⇒ 计入 `unknown`，绝不猜**（接管仓位 / 离线平仓 / 重启后续跑都会命中这一格）。`unknown` 单列，不并进任何一侧。
4. 结算的净额盈亏口径**取自 `AttributionLedger` 已有的 `net`**（`record_close` 里那一份），不另算——两处各算一次必漂移。

**`passed_counter` 不进 `blocked`**：镜像 `open_funnel` 模块 docstring 第 2 条「`sizing_zeroed` 不是一道闸」与 `resolve_consensus_gate` 不变式 5 的成文纪律——把放行塞进拦截计数，会让闸计数把一次成功开仓记成一次被拦。

#### 3.7.2 开仓漏斗新层

- `open_funnel.LAYERS`：在 `"consensus"` **之前**插入 `"trendBias"`（`LAYERS` 的顺序即漏斗层序，必须与 `session._run_decision_round` 的调用序逐字同构）。
- `open_funnel.LAYER_LABELS["trendBias"] = "趋势基调·逆势门槛"`。
- `open_funnel._DEGRADE_PREFIXES`：新增 `("counter_trend_", "trendBias")`。放在 `("low_consensus", "consensus")` 之前（`counter_trend_` 与既有任何前缀都不撞，位置只影响可读性，但顺序即优先级的纪律要求显式声明位置）。
- `discipline_runtime._DEGRADE_PREFIXES`：同步新增 `("counter_trend_", "trendBias")`。**⚠️ 评审 RP1-3：只加这一条等于没加**——它只喂 `self._degrades`，而 `_gate_counts()` 的产物被 `build_discipline_digest` 按**固定 13 行** `discipline_view.ROW_KEYS` 取用（`discipline_view.py:23-33/81`），没有 `trendBias` 这一行，计数写进去就蒸发。要让它有读点，**四个落点缺一不可**：`discipline_view.ROW_KEYS` 追加 `"trendBias"`、`_LABELS` 追加 `"trendBias": "趋势基调·逆势门槛"`、`_BUILDERS` 追加 `_trend_bias_row`（`armed` 取 `rule.enabled and rule.counter_gate_enabled`，`threshold` 取 `counter_min_confidence` / `counter_size_mult`，`source="preset"`，算不出一律 `None` 不填 0）、以及前端 `DisciplineCard` 的行数断言（该卡按后端下发的行渲染，13→14 行须同步用例）。若实现节点判定不值得开这第 14 行，**就把这条前缀一并删掉**，别留一个有写点没读点的计数。
- `apps/web/lib/aiTradeFunnel.ts::LAYER_LABEL`：新增同名条目（该文件第 26 行已写明「**这份表与后端 `open_funnel.LAYER_LABELS` 同源，改一处必须改两处**」）。

#### 3.7.3 降级码中文化

`apps/web/lib/aiTrade.ts::DEGRADE_REASON_LABELS`（第 503 行起，第 651 行 `find((item) => reason.startsWith(item.prefix))` ⇒ **顺序敏感**）新增三条，**必须按此顺序**：

```ts
{ prefix: "counter_trend_confidence",
  label: "逆势开仓：与本级别趋势基调相反，置信度未达门槛，本轮观望（平仓不受影响）" },
{ prefix: "counter_trend_size",
  label: "逆势开仓：强制降档后仓位不合法（被档位吸附回原值或归零），本轮观望" },
{ prefix: "counter_trend",
  label: "逆势开仓未通过趋势基调门槛，本轮观望（平仓不受影响）" },   // 裸码兜底，必须排最后
```

**漏加不会报错，只会把英文原始码怼到用户脸上**——directional-symmetry P0-4 与 base_rules 第 1 轮 P1-4 的同款坑，已发生两次。

#### 3.7.4 trace 相位与前端镜像（逐一列出；每一处漏抄的症状都是「跑不出错、界面静默空白」）

1. `trace.py::new_round_trace`：`"trendVerdict": payload.get("trendVerdict")`（`trace.py:164-168` 同款写法）；另加 `"trendGate": None`（轮中由 `_apply_trend_bias_gate` 原地写入）。
2. `session._begin_round_trace` 的 `trace_event("round_start", ...)`（`session.py:1953-1962`）：新增 `trendVerdict=trace["trendVerdict"]`。
3. `session._emit_tool_call_trace`（`session.py:2219-2232`）：`tool_call` 相位新增兄弟键 `trendGate=out.wire`，与既有 `consensusGate` 走同一条轨。**门控判据是「本层真的判过」（`out.lane != "exempt"`）**，与 `_apply_consensus_gate` 里那条判据逐字同构（`session.py:2535-2538`）——按总闸门控会让绝大多数轮次发一个 `lane="exempt"` 的空壳。
3b. **`layerLabel` 的自动到位有前提（评审 RP2-8）**：`extra["layerLabel"] = veto_layer_label(layer)` 由 `ai_trade_directional_symmetry_enabled` 门控（`session.py:2223-2226`）。方向对称总闸关闭的会话，被基调闸拦下时前端仍会退化成泛指的「门禁」。这是既有形态，本轮不改，但**不得**在文档里承诺「补上 `LAYER_LABELS` 就一定到位」。
4. `apps/web/lib/store.ts::emptyRoundTrace`（`store.ts:137-160`，**逐键手抄**；该函数内已写明「漏抄这四键……后端发什么前端都收不到（跑不出错、功能静默缺失）」）：新增 `trendVerdict: evt.trendVerdict ?? null`。**`round_start` 只经过这一个装配点**——`applyAiTradeTracePhase`（`store.ts:386-402`）在 `phase === "round_start"` 分支里直接 `emptyRoundTrace(evt)`，**不走** `reduceRoundPhase`，所以这里只需改一行。
5. `store.ts::reduceRoundPhase` 的 `case "tool_call"`：新增 `trendGate: evt.trendGate ?? base.trendGate ?? null`。`trendGate` 走 `tool_call` 相位、`trendVerdict` 走 `round_start` 相位，是**两条不同的手抄轨**，不可互相顶替。
6. `apps/web/lib/aiTradeTypes.ts`：新增 `TrendVerdict` / `TrendGateWire` / `TrendStats` 接口，并在三处已有 `multiTimeframeContext?:` 的结构（`:293` / `:999` / `:1106`）旁各加一行 `trendVerdict?: TrendVerdict | null;`；`AiTradeRoundTrace` 另加 `trendGate?: TrendGateWire | null;`。
7. `apps/web/components/Agent/AiTradeTraceRound.tsx`：新增 `TrendVerdictEvidence` 组件，渲染在 `SignalContext`（`AiTradeTraceRound.tsx:655` 起）里的 `MultiTimeframeEvidence` **之前**（阅读顺序即证据顺序）；**`SignalContext` 的早退门控必须加上 `!trendVerdict`**——该函数里已写明「漏了它们，只有战术数据 / 只有唤醒断面的轮次会让整段『输入信号板』消失（跑不出错、永远空白）」。
8. `apps/web/lib/aiTradeTrend.ts`（新建）：`describeTrendVerdict` / `describeTrendAlignment` / `trendVerdictTone` 三个纯函数 + 单测，供 trace 行与统计卡片共用一份中文化。

**「提议被拦下」那一行不需要前端改动（评审节点已核实，勿再改）**：`aiTradeExit.ts::describeVetoedProposal` 在 directional-symmetry B-3 里**已经**泛化为 `const by = layerLabel ?? (vetoedBy === "horizon" ? "交易周期层" : "门禁")`，函数上方并写明「**绝不允许**回退到 `vetoedBy ?? "门禁"`」。后端 `gate_veto.py:76` 的 `note["layerLabel"] = LAYER_LABELS.get(key, key)` 直接读 `open_funnel.LAYER_LABELS`——**因此只要 §3.7.2 那条 `LAYER_LABELS["trendBias"]` 补上，中文层名就自动到位**。漏补的症状不是露英文 key，而是退化成泛指的「门禁」：用户看得到被拦、看不出被哪一层拦。用例 F-4 守这一点。

#### 3.7.5 统计卡片

- 后端**两个落点，缺一即有一页是空的（评审 RP1-2）**：
  1. **落库**：`stats_ext.build_trend_stats(trend_wire) -> dict` + `merge_trend_stats(base, ext)`（镜像 `build_funnel_stats` / `merge_funnel_stats`，理由同该文件第 6 行注释——`_compute_ai_stats` 是**模块级纯函数**、拿不到 `self`，所以合并只能在外层做）。落进 `aiStats.trendVerdict`，随 `strategy_runs.metrics_json` 持久化，**零 DB 迁移**。供**历史详情页**（`AiTradeStatsDetail.tsx:164` 读的正是 `aiStats?.openFunnel` 这一族）。
  2. **实时 status**：`session._status_payload` 新增 `"trendVerdict": self._trend.stats_wire()`，与 `"openFunnel"` 并列（`session.py:3070`）。**实时页读的是成员级 status 快照**（`AiTradeStatsLive.tsx:234` 的 `m.session?.openFunnel`），不是 `aiStats`——只落 `aiStats` 会让实时页那张卡恒为空，而 `session.py:3066-3068` 上方注释已白纸黑字写着「后端算了、类型补了、没有渲染落点 = 死数据」。
- 前端：新建 `apps/web/components/Agent/aiTradeStats/TrendAlignmentCard.tsx`，挂在 `AiTradeStatsLive.tsx` / `AiTradeStatsDetail.tsx` 的 `DirectionBalanceCard` 之后（两张卡是同一问题的两个切面：一张答「多空意愿比」，一张答「顺势逆势比与各自的成绩」）。卡面四行：
  1. 本会话基调分布：偏多 N 轮 / 偏空 M 轮 / 中性 K 轮 / 不可用 J 轮；
  2. 开仓意愿：顺势 a / 逆势 b / 无基调 c（`unknown` 单列，**绝不猜方向**）；
  3. 逆势门槛：拦下 x 次（置信度 x1 / 降档 x2），放行 y 次；顺势放宽：D-1a 追加票 a1 次 / D-1b 放宽反向票上限 a2 次；
  4. 成绩：顺势 p 笔净额 ±U USDT，逆势 q 笔净额 ±V USDT，归属不明 r 笔（**单列，绝不并进任何一侧**）。样本 < 阈值时写「样本 3/8，尚不足以下结论」，**不画趋势**（沿用信源先验那轮的成文口径）。

  第 4 行的 `alignment` 一律取自 §3.7.1 的**认领表**（开仓委托发出那一刻定格），不是当轮基调；认领不到即进 `unknown`（评审 RP0-5）。第 3 行第二句是 RK-1 缓解④ 的读点：若长期只有一侧被放宽，用户在同一张卡上就能看出来并关掉对应总闸。

### 3.8 轴 F：回测同源

`backtest.py` **四处**接线：

1. payload 装配（`backtest._build_payload`，`backtest.py:1085` 之后）：`trendVerdict`，总闸取 `ai_trade_trend_verdict_backtest_enabled`。
2. `SystemPromptTexts(trend_text=format_trend_charter(...))` + `build_deliberation_protocol(..., trend_bar=...)`（`backtest.py:355-365`）。
3. 决策链里同位置（`self._sizing.gate` **之后**、`resolve_consensus_gate` **之前**）插入 `apply_trend_bias_gate`，并把 `bonus_votes` / `opposing_allowance` 传进 `resolve_consensus_gate`（`backtest.py:893-897`）。
4. **`gate_reason` 的合成链必须同时加一项（评审 RP1-9）**。`backtest.py:918-921` 是一条显式的「八元合成、先执行者胜出」表达式；只插调用而不并进这条链，被基调闸拦下的轮会变成「决策是 hold、没有任何降级码」——回测的漏斗与统计当场对不上，**且不报任何错**。改为九元，位置与实盘 `degrade or reason` 的链序逐字同构：

```python
gate_reason = (base_reason or horizon_reason or cooldown_reason
               or turnover_reason or backstop_reason
               or brake_reason or trend_reason or consensus_reason or edge_reason)
#                              ^^^^^^^^^^^^ 刹车之后、共识之前，与 LAYERS 的层序同构
```

**两处必须如实披露的回测/实盘差异（评审 RP2-4，不得写成「逐字同构」）**：

- `backtest._build_payload` **没有** `tacticalContext`（全文无该键，也不调 `_wake.inject`），故 `tacticalAlignment` 恒为 `null`、`conflict` 在回测侧永远不会取到 `tactical_conflict` / `both`。轴 D-1b 那条「战术背离即不放宽」的护栏因此在回测里恒不触发——回测会比实盘**略宽**。
- 回测的 `now_wall` 取根时间戳而非墙钟（既有 D-4 双时钟披露），本特性不引入新的时间判据，不受影响。

两条差异都要写进 `format_backtest_notes` 的回测说明里，与既有的 D-4 披露并列。**理由**：本轮唯一的产品承诺是「用户在回测里调出来的基调参数在实盘逐字生效」；把一条结构性不可达的差异说成「同构」，会让用户拿回测里的多空比去推断实盘。

### 3.9 一个决策轮的调用序（改动后，★ = 本轮新增）

```
K 线收线 / 定时唤醒
 └─ _build_decision_payload
     ├─ marketContext / marketDigest / multiTimeframeContext        （既有）
     ├─ _wake.inject → tacticalContext / contextDisclosure          （既有）
     ├─ ★ trendVerdict = build_trend_verdict(TrendInputs(...), rule)
     ├─ ★ self._trend.note_verdict(trendVerdict)
     └─ _experience.inject_round                                    （既有，仍是最后一步）
 └─ compute_consensus → payload["consensus"]                        （既有）
 └─ _begin_round_trace（round_start 相位，★ 带 trendVerdict）
 └─ decider.decide  →  (decision, degrade)
 └─ ★ self._trend.note_intent(stage1_decision.action, llm_degrade, trendVerdict)   # 判据同 open_funnel
 └─ base_rule → horizon → profitCooldown → turnover → backstop → sizing/brake （既有）
 └─ ★ _apply_trend_bias_gate                                        （逆势置信度 + 强制降档）
 └─ _apply_consensus_gate(..., ★ bonus_votes=trend_consensus_bonus(decision.action, …),
                               ★ opposing_allowance=trend_contrarian_allowance(decision.action, …))
 └─ edge_gate → reflection（★ R4 与两条评级标准按 payload["trendVerdict"] 条件化）
 └─ _apply_sizing → _execute_decision
 └─ ★ self._trend.note_submitted(clOrdId, alignment)   # alignment **在此定格**（RP0-5）
（后续、可能跨轮）
 └─ ★ self._trend.note_fill(trade)      # 按 clOrdId 认领 alignment，认领不到 ⇒ unknown
 └─ ★ self._trend.note_settled(trade, net)  # 平仓结算时才回流净额盈亏，与 attribution 同一落点
```

---

## 四、文件 / 模块变更计划

### 4.1 后端（`services/quant/`）

| 文件 | 新建/修改 | 一句话意图 |
|---|---|---|
| `app/ai_auto/trend_verdict.py` | **新建** | 轴 A 纯核心：`TrendVerdictRule` / `trend_rule_from_settings` / `TrendInputs` / `build_trend_verdict` / `alignment_of` / `apply_trend_bias_gate` / `trend_consensus_bonus` |
| `app/ai_auto/trend_text.py` | **新建** | 《本轮趋势基调》段的 prompt 渲染（`format_trend_verdict_for_prompt`），`prompts.py` 经延迟导入消费 |
| `app/ai_auto/trend_runtime.py` | **新建** | 会话门面 `TrendRuntime` + `TrendCounters`（基调分布 / 顺势逆势意愿 / 逆势闸计数 / 分组盈亏） |
| `app/config.py` | 修改 | 新增 **10** 个 `ai_trade_trend_*` 配置项（§六；v1 的 9 项 + 轴 D-1b 的独立总闸） |
| `app/ai_auto/prompts.py` | 修改 | 新增 `format_trend_charter`；`_render_spine_zh` 拼接第七段 + `_trend_read_order` 子句；`build_decision_prompt` 插入基调段（延迟导入 `trend_text`） |
| `app/ai_auto/prompts_en.py` | 修改 | `SystemPromptTexts` 增 `trend_text` 字段并进 `charter()`；`render_spine_en` 决策纪律 #1 插 `_trend_read_order_en` |
| `app/ai_auto/reasoning_protocol.py` | 修改 | 新增 `_TREND_FIRST_S1` / `_TREND_MANDATES` 与 `_with_trend_first`；`build_deliberation_protocol` 与 **`build_review_protocol`** 各增 `trend_bar` 形参（后者承载 R4 的条件化子句，评审 RP0-4）；复合顺序写死为「先 symmetry 后 trend_first」（RP1-13） |
| `app/ai_auto/decider.py` | 修改 | `_stream_reflection` 的 `build_review_protocol(...)` 传 `trend_bar`，取自**当轮 payload** 的 `trendVerdict.bar`（全仓唯一装配点，绝不再读一次 settings） |
| `app/ai_auto/discipline_view.py` | 修改 | 纪律自检第 14 行 `trendBias`：`ROW_KEYS` / `_LABELS` / `_BUILDERS` + `_trend_bias_row`（评审 RP1-3：只改 `discipline_runtime` 是有写点没读点） |
| `app/ai_auto/attribution.py` | **不改** | alignment 认领表住 `trend_runtime.py`，形状镜像 `_pending_estimates`；净额盈亏口径**取自**本模块 `record_close` 已算好的 `net`，不另算 |
| `app/ai_auto/signals.py` | 修改 | `resolve_consensus_gate` 增 `bonus_votes` / `opposing_allowance` 两个 keyword-only 形参（默认全 0 ⇒ 逐字节回退）；`_consensus_wire` 增五键并收成聚合入参（RP2-1） |
| `app/ai_auto/session.py` | 修改 | payload 注入 `trendVerdict`；`_apply_trend_bias_gate` 薄封装；`_apply_consensus_gate` 传两个放宽入参；`round_start` / `tool_call` 两相位各带新键；`_status_payload` 增 `trendVerdict`（RP1-2）；`_execute_decision` 回执后 `note_submitted` 定格 alignment、`_dispatch` 的成交与结算钩子回流（RP0-5）。**净增预算据实修正为 ≤ 55 行**（v1 的「≤ 30 行」在补齐上述三条后不现实，评审 RP2-7），仍**一律是薄封装、不得下沉纯核心**，并须在 `.claude-index/config.md` 照实登记 |
| `app/ai_auto/backtest.py` | 修改 | 三处接线（payload / 两段 prompt / 门禁），与实盘逐字同构 |
| `app/ai_auto/trace.py` | 修改 | `new_round_trace` 增 `trendVerdict` / `trendGate` 两键（`trendGate` 轮中由门禁原地写入） |
| `app/ai_auto/open_funnel.py` | 修改 | `LAYERS` 插 `"trendBias"`（consensus 之前）；`LAYER_LABELS` / `_DEGRADE_PREFIXES` 各增一条 |
| `app/ai_auto/discipline_runtime.py` | 修改 | `_DEGRADE_PREFIXES` 增 `("counter_trend_", "trendBias")` |
| `app/ai_auto/stats_ext.py` | 修改 | `build_trend_stats` / `merge_trend_stats`（`_compute_ai_stats` 是模块级纯函数，只能在外层合并） |
| `app/ai_auto/manager.py` | 修改 | 装配 `trend_text` / `trend_bar` 两段；会话构造 `TrendRuntime` 与 `TrendVerdictRule` |
| `app/schemas.py` | 修改 | 新增 `AiTradeTrendIn`（整键缺省即按 settings 装配）**并把 `trend` 字段加进实盘与回测两个启动请求体**（评审 RP1-10） |
| `app/api/ai_auto.py` | 修改 | `_resolve_trend`（唯一解析点，**必须排在 `_resolve_discipline` 之后**）+ `_TREND_TO_SETTING` 映射；**两个调用点**：实盘 `:394` 一侧与回测 `:846` 一侧，与 `_resolve_direction` 的既有姿态逐字同构（RP1-10） |
| `.env.example`（**仓库根目录**，不在 `services/quant/` 下，评审 RP2-2） | 修改 | 登记 10 个新键（**无用例扫描该文件，漏登记是静默的**） |

### 4.2 前端（`apps/web/`）

| 文件 | 新建/修改 | 一句话意图 |
|---|---|---|
| `lib/aiTradeTrend.ts` | **新建** | `describeTrendVerdict` / `describeTrendAlignment` / `trendVerdictTone` 纯函数（trace 与统计卡共用一份中文化） |
| `lib/aiTradeTrend.test.ts` | **新建** | 上述三函数的用例（含 degraded / 冲突降档 / 缺失成分三条边缘态） |
| `lib/aiTradeTypes.ts` | 修改 | `TrendVerdict` / `TrendGateWire` / `TrendStats` 接口 + 三处 round trace 结构各加一行 |
| `lib/store.ts` | 修改 | `emptyRoundTrace` 手抄 `trendVerdict`（`round_start` 的唯一装配点）；`reduceRoundPhase` 的 `case "tool_call"` 手抄 `trendGate` |
| `lib/aiTrade.ts` | 修改 | `DEGRADE_REASON_LABELS` 增三条（顺序：两个细分码在前、裸码兜底在后） |
| `lib/aiTradeFunnel.ts` | 修改 | `LAYER_LABEL` 增 `trendBias`（与后端同源，改一处必须改两处） |
| `lib/aiTradeReasoning.ts` | 修改 | `DECISION_TITLES[1]` → `"趋势基调与多周期背景"` |
| `components/Agent/AiTradeTraceRound.tsx` | 修改 | 新增 `TrendVerdictEvidence`；**早退门控必须加 `!trendVerdict`** |
| `components/Agent/aiTradeStats/TrendAlignmentCard.tsx` | **新建** | 基调分布 / 顺势逆势意愿 / 逆势闸 / 分组成绩 四行卡片 |
| `components/Agent/aiTradeStats/TrendAlignmentCard.test.tsx` | **新建** | 卡片用例（含「样本不足不下结论」与「unknown 单列」两条） |
| `components/Agent/aiTradeStats/AiTradeStatsLive.tsx` / `AiTradeStatsDetail.tsx` | 修改 | 挂载新卡片（`DirectionBalanceCard` 之后） |
| `components/Agent/AiTradeDirectionFields.tsx` | 修改 | 启动弹窗内新增《趋势基调》分组：总闸勾选 + **逆势闸勾选**（`counterGateEnabled`，评审 RP1-12：RK-1 的缓解④「用户能立刻关掉」此前只有 env、没有接口）+ 逆势置信度 / 降档倍数两个可选覆盖 + 顺势追加票勾选 + 顺势放宽反向票上限勾选（D-1b） |
| `components/Agent/aiTradeStats/DisciplineCard.tsx`（或其行数断言所在用例） | 修改 | 纪律自检 13 → 14 行（`trendBias`，评审 RP1-3） |
| `components/Agent/AiTradeLaunchModal.tsx` | 修改 | 把 `trend` 入参并进启动请求体（沿用 `direction` / `discipline` 的既有姿态） |

### 4.3 测试（`services/quant/tests/`、`apps/web/lib|components/*.test.*`）

| 文件 | 新建/修改 | 一句话意图 |
|---|---|---|
| `tests/test_ai_trade_trend_verdict.py` | **新建** | 轴 A 纯核心用例（打分、死区、**强度取自 `execScore`**、冲突只撤放宽不降强度、缺失、两道 degraded 门槛、I-1 / I-1b / I-2） |
| `tests/test_ai_trade_trend_gate.py` | **新建** | 轴 D 用例（逆势置信度取**模型自报值** / 强制降档三判据 / fail-closed / I-4 对称性 / D-1a 五条硬约束 / D-1b 的目标断面 D-12 / 「判据必须含 action」的回归护栏 D-11） |
| `tests/test_ai_trade_trend_session.py` | **新建** | 会话集成（payload 注入顺序、门禁插点、漏斗计数闭合、trace 键、总闸关闭的逐字节回退） |
| `tests/test_ai_trade_prompts.py` | 修改 | 基调段渲染（`full` / `brief` 两档）/ 第七段宪章 / 决策纪律 #1 读序（P-10：armed 版里 `1D → 12H → 4H → 1H` **只出现一次**）/ 反思侧三处条件化子句（P-9）/ **总闸关时 sha256 冻结** |
| `tests/test_ai_trade_reasoning.py`（或既有协议用例文件） | 修改 | 决策侧 S1/S2/S12 与**复核侧 R4** 的 armed / disarmed 两态冻结断言；`(symmetry, trend_bar)` 四种组合（P-4b） |
| `tests/test_ai_trade_open_funnel.py` | 修改 | `trendBias` 层归类 + `sum(blocked)` 仍与 `intents` 闭合 |
| `tests/test_ai_trade_signals.py` | 修改 | `bonus_votes=0` **且** `opposing_allowance=0` 时 `resolve_consensus_gate` 逐字节回退（放行轮 `is` 相等）；两者各为 1 时 wire 五键与 `_contrarian_block` 的上限抬高 |
| `tests/test_ai_backtest_*`（既有回测用例文件） | 修改 | 回测侧**四处**接线的装配判据（**必须同时覆盖 system 与 messages 两侧**），含 `gate_reason` 九元合成里基调项的位置（评审 RP1-9：被基调闸拦下的回测轮必须带 `counter_trend_*` 降级码） |
| `tests/test_ai_trade_discipline.py`（纪律自检既有用例） | 修改 | 13 → 14 行；`trendBias` 行的 `armed` / `threshold` / `firedCount` 三值与 `counter_trend_*` 的计数闭合（评审 RP1-3） |

---

## 五、接口设计

### 5.1 启动请求（POST `/ai-trade/start`，新增可选字段 `trend`）

```jsonc
{
  "instId": "BTC-USDT-SWAP",
  "bar": "15m",
  "trend": {                            // 整键缺省 ⇒ 服务端按 settings 装配开启态
    "enabled": true,                    // 显式关闭的唯一途径：{"enabled": false}
    "counterGateEnabled": null,         // 逆势降档闸（RP1-12）；null ⇒ 用 settings
    "counterMinConfidence": 0.65,       // null ⇒ 用 settings
    "counterSizeMult": 0.5,             // null ⇒ 用 settings
    "consensusVoteEnabled": true,       // 轴 D-1a；null ⇒ 用 settings
    "contrarianAllowanceEnabled": true  // 轴 D-1b（RP0-2）；null ⇒ 用 settings
  }
}
```

**同一个字段必须同时挂在回测启动请求体上**（评审 RP1-10）：实盘走 `api/ai_auto.py:394` 一侧、回测走 `:846` 一侧，`_resolve_direction` 两处都调了。只接实盘一侧，会让「用户在回测里调出来的参数在实盘生效」这条承诺在**反方向**上破掉（回测永远只吃 settings 默认值），而 RK-8 正是为这件事设的。

`app/schemas.py::AiTradeTrendIn`（值域**一次性在本层校验**，越界 422；`_resolve_trend` 只做缺省回填、不复夹——`_resolve_position_step` 的 [R-2] 惯例）：

```python
class AiTradeTrendIn(BaseModel):
    enabled: bool = True
    counterGateEnabled: bool | None = None
    counterMinConfidence: float | None = Field(default=None, gt=0, le=1)
    counterSizeMult: float | None = Field(default=None, gt=0, le=1)
    consensusVoteEnabled: bool | None = None
    contrarianAllowanceEnabled: bool | None = None

    def overrides(self) -> dict: ...   # 键集与 api/ai_auto._TREND_TO_SETTING 一一对应
                                       # （`enabled` 与 `_DIRECTION_TO_SETTING` 同姿态，单独处理）
```

`_resolve_trend(raw, settings) -> Settings` 产出**又一层派生 settings 副本**（镜像 `_resolve_direction`）；运维总闸 `AI_TRADE_TREND_VERDICT_ENABLED=false` ⇒ 恒不装配（`raw` 一律被忽略）。**调用点必须排在 `_resolve_discipline` 之后**。

### 5.2 WebSocket：`round_start` 相位新增键

```jsonc
{ "type": "trace", "phase": "round_start", "round": 12, "ts": 1756700000,
  "trendVerdict": { /* §3.2.4 的完整 wire */ } }
```

`trendVerdict` 可为 `null`（未装配）。走 WS 原始 dict，**不经任何 `response_model`**——本仓库有 `response_model` 静默吞新 wire 键的成文教训，若实现者把它接到任何带 `response_model` 的回执上，必须同步登记该模型。

### 5.3 WebSocket：`decision` 事件新增键

```jsonc
{ "type": "decision", "trendGate": {
    "lane": "counter", "alignment": "counter", "strength": "strong",
    "score": -0.32, "execScore": -0.61,
    "minConfidence": 0.65,
    "modelConfidence": 0.72,        // ★ 判据取这个（模型自报），评审 RP1-8
    "verdictConfidence": 0.59,      // ★ 纯呈现，绝不参与判定
    "sizeMult": 0.5, "sizeBefore": 0.5, "sizeAfter": 0.25, "reason": null } }
```

**两个 confidence 必须各有其名**：v1 只有一个 `confidence` 键，而 `verdict` 与 `decision` 各有一个同名量，取错方向相反（详见 §3.3.1）。

`consensus` wire 同时新增五键：`trendBonusVotes` / `effectiveVotes` / `trendBonusLane` / `trendOpposingAllowance` / `effectiveMaxOpposingVotes`；`votes` 与 `contrarianMaxOpposingVotes` 两键语义不变。

### 5.4 会话统计（`GET /ai-trade/stats`，`aiStats.trendVerdict`）

```jsonc
{ "trendVerdict": {
    "armed": true, "bar": "15m",
    "verdictRounds": {"up": 31, "down": 44, "range": 18, "unknown": 2},
    "intents": {"aligned": 9, "counter": 4, "unknown": 3},
    "counterBlocked": {"confidence": 3, "size": 0}, "counterPassed": 1,
    "bonusLanes": {"voteApplied": 5, "allowanceApplied": 2,
                   "blockedNoNativeVote": 7, "blockedConflict": 11},
    "filled": {"aligned": 6, "counter": 1, "unknown": 2},
    "netPnlUsd": {"aligned": 41.2, "counter": -8.7, "unknown": 3.1},
    "minSamples": 8 } }
```

- `intents` 的分母判据**逐字复用** `open_funnel.note_intent`（评审 RP1-1），第三格叫 `unknown` 而不是 `neutral`（与 `open_funnel.SIDES` 同姿态；v1 两处用词不一致，评审 RP2-5）。
- `filled` / `netPnlUsd` 的 `alignment` 取自**开仓委托发出时定格的认领表**，不是当轮基调（评审 RP0-5）；认领不到一律进 `unknown`，**绝不猜**。
- `bonusLanes` 是 RK-1 缓解④ 的读点：它回答「两条放宽通路各为哪一侧点亮过几次、被哪条护栏挡下过几次」。
- 未装配 / 零样本 ⇒ **整键缺席**（不是 `null`）。**同一份 wire 必须同时挂在 `_status_payload` 上**（RP1-2），否则实时页那张卡恒空。

---

## 六、数据模型与配置

### 6.1 数据库

**零迁移**。会话级统计随既有 `strategy_runs.metrics_json` 的 `aiStats` 落库（`stats_ext` 合并路径），基调断面随既有 trace 事件走 WS，不落表。

### 6.2 内存形状

- `TrendVerdictRule`（frozen dataclass，会话生命周期内不变）
- `TrendInputs`（frozen，每轮现造）
- `TrendGateOutcome`（frozen，每轮至多一个）
- `TrendCounters`（会话级可变累计，住 `TrendRuntime`）
- `payload["trendVerdict"]` / `trace["trendVerdict"]` / `trace["trendGate"]`（只读 dict）

### 6.3 新增配置项（`app/config.py` + `.env.example`）

| 键 | 默认 | 约束 | 语义 |
|---|---|---|---|
| `ai_trade_trend_verdict_enabled` | `True` | bool | **总闸**：False ⇒ 全链路（payload / prompt / 协议 / 门禁 / trace / 统计）逐字节回退 |
| `ai_trade_trend_verdict_backtest_enabled` | `True` | bool | 回测侧独立总闸（沿用 `horizon` / `profit_guard` 的既有先例） |
| `ai_trade_trend_enter_threshold` | `0.22` | `gt=0, le=1` | 对称死区：**`&#124;score&#124;`**（含 htf）达该值才给方向 |
| `ai_trade_trend_strong_threshold` | `0.55` | `gt=0, le=1` | 记 `strong` 的下界，判据是 **`&#124;execScore&#124;`**（不含 htf，评审 RP0-3）；解析点夹紧到 `> enter + 0.05` |
| `ai_trade_trend_htf_weight` | `0.18` | `ge=0, le=0.4` | 高周期调制项权重（解析点封顶到 `_HTF_WEIGHT_CAP=0.30`，保 I-1） |
| `ai_trade_trend_counter_gate_enabled` | `True` | bool | 轴 D-2 逆势降档闸独立总闸 |
| `ai_trade_trend_counter_min_confidence` | `0.65` | `gt=0, le=1` | 逆势开仓的置信度下界 |
| `ai_trade_trend_counter_size_mult` | `0.5` | `gt=0, le=1` | 逆势开仓的强制降档倍数 |
| `ai_trade_trend_consensus_vote` | `True` | bool | 轴 **D-1a** 顺势追加一票独立总闸 |
| `ai_trade_trend_contrarian_allowance` | `True` | bool | 轴 **D-1b** 顺势放宽逆势通路反向票上限（+1）独立总闸（评审 RP0-2 新增）。它**不发票**，只抬高 `_contrarian_block` 首行那条上限；逆势通路的其余代价一条不减 |

**四把独立总闸**（`verdict_enabled` / `counter_gate_enabled` / `consensus_vote` / `contrarian_allowance`）的语义边界必须写进代码注释：

- 关掉 `verdict_enabled` ⇒ 另外三把无论开关一律不生效（它们的判据全部来自 verdict），且 payload / prompt / 协议（含复核侧 R4）/ 门禁 / trace / 统计**逐字节回退**；
- 只关 `counter_gate_enabled` ⇒ 基调仍进 prompt 与统计，只是不再拦任何一笔（**收紧**通路关闭）；
- 只关 `consensus_vote` / `contrarian_allowance` ⇒ 对应的**放宽**通路关闭，其余不变。

**三者的极性必须分开记**：`counter_gate` 是唯一会**减少**成交的闸，另两把是唯一会**增加**成交的通路。把它们绑在一把总闸上，运维就无法只回退「变宽」的那一半（RK-3 的可回退性依赖这一点）。

---

## 七、测试与验收标准

### 7.1 轴 A 纯核心（`test_ai_trade_trend_verdict.py`）

| id | 断言 |
|---|---|
| A-1 | `rule.enabled=False` ⇒ `build_trend_verdict(...) is None` |
| A-2 | 五个执行周期成分一致看多 ⇒ `direction="up"`、`bias="long"`、`strength="strong"` |
| A-3 | **I-1**：执行周期五项各 −1、`htf` 满分 +1 ⇒ `score < 0` 且 `direction="down"` |
| A-4 | **I-2**：构造 wire 与其镜像的 `score` **与 `execScore`** 均严格反号，`direction`/`bias`/`strength` 严格镜像 |
| A-5 | `emaFast == emaSlow` ⇒ `ema` 成分得 `0.0`（**不得**因 `emaRelation="fast_above"` 得正分） |
| A-6 | `macdHist.value == 0` ⇒ `momentum` 的 macd 半项得 `0.0`（不得因 `label="bearish"` 得负分） |
| A-7 | 缺失成分不进分母：只给 `regime` 与 `ema` ⇒ `availableWeight = 0.48/1.18 ≈ 0.41 < 0.5` ⇒ `degraded=True`、`direction="unknown"`（**分母是全部权重和 1.18**，v1 的 `1.02` 是把某断面的「可用权重」误当成了分母，评审 RP1-6） |
| A-7b | `execAvailableWeight` 门槛：给满 `htf` + 只给 `regime`（`execAvailableWeight = 0.26 < 0.5`）⇒ `degraded=True`，**即使** `availableWeight = 0.44/1.18` 之外的配置能把第一道门槛凑过 |
| A-3b | **I-1 的缺失断面**（评审 RP1-7）：只给 `regime`(-1) + `ema`(-1)（`execAvailableWeight = 0.48`，刚过 0.5 需再补一项到 `0.64`）+ `htf` 满分 `+1` ⇒ `score < 0`。构造成刚好过 `_MIN_EXEC_WEIGHT` 的最小组合 |
| A-11 | **强度与高周期无关**（评审 RP0-3）：同一组执行周期成分，`htf` 从 `+1` 改到 `−1` ⇒ `strength` 逐字不变、`execScore` 逐字不变，只有 `score` / `direction` 可能变 |
| A-12 | **冲突不再降档**：`conflict="htf_conflict"` + `&#124;execScore&#124; ≥ strong` ⇒ `strength` 仍为 `"strong"`、`bonusWithheld=True`；`direction` 不变 |
| A-8 | 死区：`score` 落在 `(−enter, +enter)` ⇒ `direction="range"`、`bias="neutral"` |
| A-9 | ~~冲突降档~~ **已按评审 RP0-3 作废并由 A-12 取代**：v1 的降档会同时关掉「放宽」与「收紧」两条通路，净效果是「高周期一有分歧，逆势开仓就免检」，极性正好反了 |
| A-10 | `trend_rule_from_settings` 对 `strong <= enter` 与 `htf_weight > 0.30` 两种越界配置各自夹紧且不抛异常 |

### 7.2 轴 D 门禁（`test_ai_trade_trend_gate.py`）

| id | 断言 |
|---|---|
| D-1 | `action in ("close","hold","cancel_order")` ⇒ `lane="exempt"` 且返回**同一个对象**（`is` 相等） |
| D-2 | `strength != "strong"` ⇒ 逆势轮同样直通（闸只在基调强时咬） |
| D-3 | 逆势 + `confidence` 低于下界 ⇒ `counter_trend_confidence(...)`，决策变 hold |
| D-4 | 逆势 + 置信度达标 ⇒ 仓位变为 `quantize(proposed × mult)` 且严格小于 `proposed` |
| D-5 | **粗档位陷阱**：`position_step=0.5`、`proposed=0.5`、`mult=0.5` ⇒ 量化吸附回 `0.5` ⇒ 必须报 `counter_trend_size` 而**不是**按原仓位放行 |
| D-6 | fail-closed：`confidence=None` / `NaN` / `size_fraction=None` 三种输入一律走否决分支 |
| D-7 | **I-4**：verdict 与 action 同时镜像 ⇒ 两次 outcome 的 `lane` / `reason` 前缀 / `size_fraction` 逐字对称 |
| D-8 | `trend_consensus_bonus`：信号板该方向 0 票 ⇒ 恒返回 0（D-1a 不雪中送炭） |
| D-9 | `trend_consensus_bonus`：`strength="moderate"` ⇒ 0；`strong` + 1 票 + `conflict="none"` ⇒ 1；`strong` + 1 票 + `conflict="htf_conflict"` ⇒ **0**（冲突只收紧不放松） |
| D-10 | `resolve_consensus_gate(..., bonus_votes=0, opposing_allowance=0)` 与合入前**逐字节相同**（放行轮返回同一对象，`is` 相等） |
| D-11 | **评审 RP0-1 的回归护栏**：`verdict.bias="long"`、`strength="strong"`、`action="open_short"` ⇒ `trend_consensus_bonus` 与 `trend_contrarian_allowance` **双双返回 0**。（v1 的签名不含 `action`，此用例必红。） |
| D-12 | **评审 RP0-2 的目标断面**：`min_consensus=1`、板子 3 票看多 0 票看空、`open_short` 与`strong` 空基调同向、`opposing=3 > contrarian_max_opposing_votes=1` ⇒ 开启 `contrarian_allowance` 后**仍被拦**（3 > 1+1），阈值调到 2 后走逆势通路放行且仓位被 ×0.5；全程 `contrarianEvidence` 与置信度下界照常生效。用例同时断言：`trend_consensus_bonus` 在该断面恒为 0 |
| D-13 | `trend_contrarian_allowance`：`conflict="tactical_conflict"` / `"both"` ⇒ 0；`"htf_conflict"` ⇒ 1（与 D-1a 的判据**故意不同**，见 §3.3.2） |
| D-14 | **评审 RP1-8**：`decision.confidence=0.4` / `verdict.verdictConfidence=0.99` ⇒ 逆势轮被拦（判据取模型自报值）；反过来 `decision.confidence=0.9` / `verdictConfidence=0.1` ⇒ 放行 |

### 7.3 会话与漏斗（`test_ai_trade_trend_session.py` / `test_ai_trade_open_funnel.py`）

| id | 断言 |
|---|---|
| S-1 | payload 装配顺序：`trendVerdict` 在 `tacticalContext` 之后写入（否则 `conflict` 恒 `"none"` 且不报错） |
| S-2 | 门禁插点：逆势被拦轮的 `degrade` 是 `counter_trend_*`，且**共识门禁未被调用到开仓分支** |
| S-3 | 漏斗闭合：`sum(blocked.values()) + sizingZeroed + submitted == intents`（新层加入后仍闭合） |
| S-4 | `classify_block("counter_trend_confidence(...)") == "trendBias"` |
| S-5 | **总闸关闭 ⇒ 逐字节回退**：`ai_trade_trend_verdict_enabled=False` 时，payload 无 `trendVerdict` 键、`build_decision_prompt` 产出 sha256 与合入前相同、`round_start` 事件不含新键 |
| S-6 | 成交回流：一笔顺势成交与一笔逆势成交分别落进 `filled.aligned` / `filled.counter`，净额盈亏各自累计 |
| S-6b | **评审 RP0-5 的回归护栏（跨轮）**：第 3 轮基调偏空、开出一笔顺势空单（限价，未成交）；第 4 轮基调翻多；第 5 轮该挂单成交；第 40 轮平仓结算。断言 `filled.aligned == 1`、`netPnlUsd.aligned` 收到那笔净额——**取的是第 3 轮定格的 alignment，不是成交轮/结算轮的基调**。（v1 的 `note_fill(side, alignment, pnl)` 在此必红。） |
| S-6c | 认领不到 ⇒ `unknown`：构造一笔没有 `clOrdId`、也不属于任何本轮槽位的成交（接管仓位 / 离线平仓）⇒ 计入 `filled.unknown`，**不落进任何一侧** |
| S-7 | **评审 RP1-1**：一轮 `low_confidence`（`action` 已被洗成 `hold`）计进 `intents.unknown`；一轮真实观望（`degrade is None`、`action="hold"`）**不进** `intents` 任何一格。`sum(intents.values())` 必须等于 `openFunnel.intents` |
| S-8 | **评审 RP1-2**：`_status_payload()["trendVerdict"]` 与 `aiStats["trendVerdict"]` 是**同一次装配**的同一份 wire（同一 `stats_wire()` 调用产物对拍） |

### 7.4 prompt 与协议（`test_ai_trade_prompts.py` / 协议用例）

| id | 断言 |
|---|---|
| P-1 | `format_trend_charter(bar, rule)` 未装配 ⇒ `""`；装配 ⇒ 含 `bar` 字面量与「不能替你决定」一句 |
| P-2 | **两条脊柱都装了第七段**：中文脊柱与英文脊柱的产出各自含该条款（只改一处即红——P1-4 的回归护栏） |
| P-3 | `build_deliberation_protocol(profile, trend_bar="")` 与合入前**逐字节相同** |
| P-4 | `trend_bar="15m"` ⇒ S1 mandate 含 `execution bar 15m` 与 `may not by themselves reverse`；S2 / S12 各含新增句；**其余九个阶段逐字节不变** |
| P-5 | 基调段渲染：`degraded=True` 时只出两行且**不含任何方向词**；缺失成分被显式列出 |
| P-6 | 基调段位置：在 `build_decision_prompt` 的产出里，`【本轮趋势基调` 的下标小于 `自管持仓` 与 `中线背景` 两者。**锚点用 `自管持仓` 而不是 `当前自管持仓`**（评审 RP2-3：空仓轮渲染的是「当前无自管持仓（flat）」，后者取不到下标） |
| P-6b | **压缩轮形态**（评审 RP1-11）：`contextDisclosure="repeat"` ⇒ 基调段只剩两行，且**不含**逐成分明细；`"full"` ⇒ 全量八行 |
| P-7 | **反思侧连锁通路**：`build_reflection_prompt(payload, decision)` 的产出含基调段（它第一行就是 `build_decision_prompt(payload)`）——这条通路是我们要的，但必须被用例钉死而不是碰巧成立 |
| P-8 | `build_review_protocol(profile, symmetry=…, trend_bar="")` 的产出与合入前**逐字节相同**（R1–R7 的 disarmed 冻结路径） |
| P-9 | **评审 RP0-4 的三处**：`trend_bar="15m"` ⇒ ① R4 mandate 含新增条件化句、其余六个复核阶段逐字节不变；② `render_review_spine_en` 的 `GRADING STANDARD` 2(b) 出现 `execution-bar trend verdict` 且**不再**把 higher-timeframe 冲突单独列为缺陷；③ `build_reflection_prompt` 的中文散文同款分叉。三处**各自**再补一条「键缺席 ⇒ 原句逐字保留」的断言 |
| P-10 | **评审 RP1-4**：`_trend_read_order("")` 的产出与合入前那句**逐字节相同**；`_trend_read_order("15m")` 的产出里 `1D → 12H → 4H → 1H` **只出现一次**（v1 的写法会出现两次） |
| P-4b | **评审 RP1-13**：`(symmetry, trend_bar)` 四种组合各产出一次，断言 S1/S2/S12 与 S7/S10/S11 两组 mandate 互不干扰，且 `(True, "15m")` 的产出等于两个变换按写死顺序复合的结果 |

### 7.5 前端

| id | 断言 |
|---|---|
| F-1 | `describeTrendVerdict` 对 `degraded` / 冲突降档 / 缺失成分三态各出一句不含臆造数字的中文 |
| F-2 | `AiTradeTraceRound` 在**只有** `trendVerdict` 的轮次仍渲染出「输入信号板」段（早退门控回归护栏） |
| F-3 | `describeDegradeReason("counter_trend_confidence(conf=0.4/0.65)")` 返回中文 + 原样保留参数后缀 |
| F-4 | `layerLabel("trendBias")` 返回中文（前后端两张表同源） |
| F-5 | `TrendAlignmentCard` 在样本 < `minSamples` 时渲染「样本 3/8，尚不足以下结论」而**不是** 0% |
| F-6 | `DECISION_TITLES[1] === "趋势基调与多周期背景"` |

### 7.6 回归门禁（合并前必须全绿）

- `cd services/quant && python -u -m pytest -q`（**全量，不得用 `-k` 子集判回归**；本仓库有 `-k` 子集绿、全量红的成文前科）。跑测须 `python -u`，且**不得并发**（tests 共用一个临时 DB）。
- `cd apps/web && NODE_ENV=test pnpm vitest run`（组件用例缺 `NODE_ENV=test` 会报 React act() 错）。
- `pnpm -C apps/web tsc --noEmit`。
- 全量基线：合入前先跑一次记下既有红的条数与文件，合入后逐条对拍——本仓库已有多次「全量 7 条红来自未跟踪的在制文件」的记录。
- **第 0 步的等价性门禁（评审 RP1-5）**：`prompts.py` 的 `_format_*` 外移提交必须**先于**本特性合入，且以既有的整批 sha256 冻结断言证明系统 prompt 与决策 prompt 的产出**逐字节不变**。它是一次纯搬运，不得与本特性的任何行为改动混在同一个提交里——混在一起，G-1 / S-5 的零回归判据就失去全部说服力。

### 7.7 验收标准（产品层，用户可自查）

1. 用 15m 启动一次 AI 自动交易，展开任意一轮的「输入信号板」，第一行必须是《本轮趋势基调 · 判定级别 15m》，且其中的方向由 15m 的证据决定——把 1D 换成相反方向的构造数据后，基调方向**不改变**、只有强度下降。
2. 展开该轮研判全文，S1 的第一段必须先给出 15m 的趋势判定，再读 1D→12H→4H→1H；S12 必须写明「基调=X，本次动作顺势/逆势」。
3. 在一段 15m 下行段里，做空不再被系统结构性排除：统计卡片的「开仓意愿：顺势 a / 逆势 b」中 `a` 侧应出现空单意愿，且若被拦，卡片必须指名是被哪一层（`趋势基调·逆势门槛` 或 `同向共识门槛`）拦下的。
3b. **评审 RP0-2 / RP0-4 的可证伪验收**：构造一轮「日线 bullish + 15m 强空基调 + 信号板 3 票看多 0 票看空 + 模型提议 `open_short`」，逐层核对它到底死在哪里，并要求界面把那一层的名字**写出来**：
   - 关掉 `contrarian_allowance` ⇒ 应死在 `同向共识门槛`，注记写明「差 1 票 / 反向票 3 > 上限 1」；
   - 打开 `contrarian_allowance` 且把 `contrarianMaxOpposingVotes` 调到 2 ⇒ 应走逆势通路放行，仓位被 ×0.5，`trendOpposingAllowance=1` 出现在 `consensus` wire 里；
   - 复核官那一轮的 R4 段必须写明「本提议顺着 15m 基调，高周期分歧不单独构成缺陷」，而**不是** `abort`。
   这三条任一不成立，本特性在它要修的那个断面上就没有生效——**验收以此为准，不以多空比为准**。
4. 关闭 `AI_TRADE_TREND_VERDICT_ENABLED` 后，界面与 prompt（**含复核官的系统 prompt 与 R4 段**）逐字回到本特性合入前的形态（运维回退可用）。
5. **只关放宽、不关收紧**：只关 `AI_TRADE_TREND_CONSENSUS_VOTE` 与 `AI_TRADE_TREND_CONTRARIAN_ALLOWANCE` 两把，基调、逆势门槛、trace 与统计全部照常工作，`consensus` wire 的 `effectiveVotes == votes`、`effectiveMaxOpposingVotes == contrarianMaxOpposingVotes`。

---

## 八、风险与缓解（Risks & Mitigations）

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| RK-1 | **新闸变成新的单向阀**：逆势降档闸在单边行情里只会拦一侧，与它要修的病同形 | 把「总是做多」换成「总是顺势」，方向偏斜换了个名字 | ① 闸只在 `strength="strong"` 时咬，而 `strength` **只由执行周期证据算出**（评审 RP0-3）——高周期不再能把它抬高；② 代码路径无任何按 side 的字面分支（I-4，用例 D-7 冻结）；③ 基调本身在 15m 上算，上下切换频率远高于日线；④ 统计卡把「顺势/逆势各自的成交与净额盈亏」摆在同一张卡上（数据取自**开仓时定格**的 alignment，RP0-5），若逆势样本长期为 0 而顺势不赚钱，用户能在**启动弹窗**里直接关掉逆势闸（`counterGateEnabled`，RP1-12 补的接口），不必去改 env |
| RK-1b | **放宽通路本身带方向偏斜**（评审 RP0-2 新增）：上行行情里「强多基调 + 已有 ≥1 张多票」是常态，而强空基调难得一见 ⇒ 唯一放宽门禁的改动只为做多点亮 | 修完之后多空比更偏 | ① `strength` 去掉高周期分量后，空侧的可达性显著提高（RP0-3）；② D-1b 专为「板子一边倒」断面设，它放宽的是**逆势通路**，在单边看多的板子上只可能为**做空**点亮；③ `bonusLanes` 五个计数分别下发，两条通路各为哪一侧点亮过几次是可查的；④ 两把总闸独立，任一可单独关 |
| RK-2 | **`engine/regime.py` 自身有一处极小的多头偏置**：`align_up = fast >= slow`，`fast == slow` 且净变化为负时判 `range` 而非 `trend_down` | `regime` 成分在临界点上偏多，幅度极小 | 本轮**不改** `engine/regime.py`（它被策略层、组合轮动、回测三条链路共用，改动面远超本需求）；`trend_verdict` 的 I-2 对称性用例用**构造 wire** 对拍而不跑 `classify_regime`；该偏置作为 P2 记录，留给后续独立 spec |
| RK-3 | **两条顺势放宽通路让共识门禁变宽**，可能提高交易频次并被成本吃掉 | 净盈利下降 | ① **D-1a** 五条硬约束（独立总闸 / 顺势 / 仅 `strong` / `conflict=="none"` / 信号板须已有 ≥1 票），至多 +1 票且不随强度放大；② **D-1b** 不发任何票，只抬高反向票上限 1，逆势通路的逐字证据、置信度下界、会话配额（默认 3 次/会话）与强制 ×0.5 降档一条不减——它抬高的是**门槛之上的天花板**，不是门槛本身；③ 原值与合成值全部同时下发（`votes`/`effectiveVotes`、`contrarianMaxOpposingVotes`/`effectiveMaxOpposingVotes`、`bonusLanes` 五计数），事后可完整归因；④ 既有的 edge 门禁、换手预算、成本记账三层全部在其下游，未被触碰；⑤ 两把总闸独立，可只回退「变宽」的那一半（§6.3 的极性说明） |
| RK-4 | **基调与模型判断长期打架**，模型每轮都在解释为什么不听 | 研判篇幅膨胀、token 成本上升 | 基调段被设计为 ~8 行的紧凑块；S12 的追加句只要求一句结论；`contextDisclosure="repeat"` 的压缩轮**同样压缩基调段**——该形态的**实现落点已在 §3.4.2 补齐**（`format_trend_verdict_for_prompt(verdict, tier)`；v1 给的签名拿不到档位，这条缓解措施当时是悬空的，评审 RP1-11），文案与断言见 §3.4.2 与用例 P-6b |
| RK-5 | **多处手抄点漏一处**（前端 8 个镜像点、两张 LAYER 表、降级码表、`.env.example`） | 症状全是「跑不出错、界面空白 / 露英文码」 | §4 的清单逐行列出；§7 为每一处漏抄给了专属用例（F-2 / F-3 / F-4 / S-4）；`.env.example` 无用例可扫，故在 §四 与本表各点名一次 |
| RK-6 | **payload 装配顺序错**（`trendVerdict` 排在 `_wake.inject` 之前） | `conflict` 恒 `"none"`、战术背离永不生效，**且不报任何错** | 用例 S-1 直接断言顺序；§3.4.1 与 §3.9 两处写明。这是经验层 W-8 那条「必须是最后一步」的同款形状，本仓库已踩过一次 |
| RK-7 | **协议改动打破既有冻结断言** | 实现期大面积红 | `trend_bar=""` 为逐字节回退路径（P-3）；S2 / S12 只**追加**不改写；S1 是整体替换但只在 armed 时发生，冻结用例限定在 disarmed |
| RK-8 | **回测与实盘分叉**（只在一侧接线） | 用户在回测里调出来的参数在实盘不生效，且不报错 | §3.8 列出回测三处接线；§7.6 要求回测用例**同时覆盖 system 与 messages 两侧**（deep-reasoning-chain 的成文教训） |
| RK-9 | 文件尺寸：`session.py` 3390 行、`prompts.py` 1116 行、`decider.py` 1215 行均已越 `CLAUDE.md` 的 800 行线 | 技术债继续累积 | ① 本轮新增纯核心全部落在三个**新建**文件（各 < 350 行）；`session.py` 净增 ≤ **55** 行且只含薄封装（预算据实修正，RP2-7）；渲染文案外移到 `trend_text.py`。② **`prompts.py` 有一条已登记四次的硬要求必须先还**（评审 RP1-5）：`.claude-index/config.md` 第 241 行写着「把 `_format_*` 家族整体外移到 `ai_auto/prompt_sections.py`……**下一个触碰本文件的特性必须先还这笔债**」。本特性正是那个特性。故 §十 新增**第 0 步**：先做一次零行为变更的外移提交，再动本特性；若判定不做，**必须**写第五次登记并说明理由，不得静默跳过 |
| RK-10 | 用户可能期望「立刻看到空单」，而合入后行情仍是单边上行 | 认为修复无效 | 验收标准 §7.7 明确以**可证伪的机制**为准（基调由 15m 决定、逆势不被结构性排除、拦截层可指名），而不是以多空比为准；统计卡片提供「本会话基调分布」让用户看到 15m 究竟有多少轮是偏空的 |

---

## 九、非目标（Non-Goals）

1. **不设方向配额、不强行拉平多空比**。用配额掩盖问题会直接损害盈利能力（directional-symmetry 已就此定调，本轮沿用）。
2. **不改 `engine/regime.py`**（含 RK-2 那处偏置）——它被策略层、组合轮动、回测共用，改动面远超本需求。
3. **不改 `_CONTEXT_POLICY`**（不把执行周期塞进多周期帧）。执行周期的证据已由 `marketContext` / `marketDigest` 完整提供；把它塞进 `frames` 会同时改变 `trendAlignment` / `nearestSupport` / `nearestResistance` 三个既有量的语义，是一次范围外的破坏性变更。
4. **不新增任何 LLM 车道**。基调判定全程为确定性纯函数，不多花一次模型调用。
5. **不做基调的历史回填 / 迁移**。统计只覆盖本特性合入后的新会话，卡片在无样本时显式写「本会话尚无样本」。
6. **不改复核官（反思侧）的安全语义**——但**要改它的一条判据**（评审 RP0-4 修订）。「只能收紧、不能改方向、不能放大、`abort` 须指名实质性缺陷、fail-closed」这五条安全语义**一个字节不改**；改的只有「与高周期冲突本身算不算一条实质性缺陷」，改法是**条件化**（顺着执行周期基调时不算），落在 R4 mandate 与两条评级标准共三处，均随 `payload["trendVerdict"]` 装配、缺席即逐字节回退。v1 写的「R1–R7 一个字节不改」已作废：那会让本特性放行的每一笔单都死在链路最后一米，而那是复核官**被明文指示**要做的事（根因 R-5，§2.5）。

---

## 十、交付顺序（建议）

0. **先还 `prompts.py` 的外移债**（评审 RP1-5，`.claude-index/config.md` 第四次登记的硬要求）：`_format_*` 家族整体外移到 `ai_auto/prompt_sections.py`，`prompts.py` 只留 schema 与三个 `build_*` 入口。**零行为变更**，以既有整批 sha256 冻结断言证明产出逐字节不变，**单独一个提交**。若实现节点判定该外移的风险高于收益，必须写第五次登记并说明理由——不得静默跳过。
1. **轴 A** `trend_verdict.py` + `test_ai_trade_trend_verdict.py`（纯核心先立，主导性 I-1 / 对称性 I-2 / 强度与高周期无关 I-1b 三条不变式先锁死）。
2. **轴 B** payload 注入 + `trend_text.py` + prompt 三处（含 sha256 冻结用例）。此时**行为零变化**，只是模型多看到一段证据——可先单独观察若干轮。
3. **轴 C** 决策侧三处 mandate + **复核侧 R4 与两条评级标准的条件化子句**（评审 RP0-4）。复核侧必须与决策侧**同轮**上线：只改决策侧，模型会更常提议顺着 15m 基调的单，而它们会更常死在复核官手里，净效果比不改还差。
4. **轴 E** 观测（trace / 漏斗 / 统计 / 前端）——**必须先于轴 D 落地**：先有可证伪的观测，再上会改变下单行为的闸，否则新闸的效果不可验证。
5. **轴 D** 三条调制通路，**按极性分两步上**：先上**收紧**的那条（D-2 逆势闸），观察若干轮；再上两条**放宽**的（D-1a 追加票、D-1b 反向票上限）。默认开启但阈值保守。先收紧后放宽的顺序是刻意的：放宽通路的效果只有在收紧通路已经有计数之后才可比。
6. **轴 F** 回测同源接线。
7. 全量回归（§7.6）+ 产品验收（§7.7）。

---

## 十一、术语对照（供实现者与评审共用）

| 术语 | 本文含义 | 代码落点 |
|---|---|---|
| 执行周期 / 判定级别 | 用户启动 AI 自动交易时选的 K 线级别（15m / 30m / 1H …） | `session.bar` / `payload["trendVerdict"]["bar"]` |
| 中线背景 / 高周期 | 严格高于执行周期的 1H / 4H / 12H / 1D | `multiTimeframeContext` |
| 战术周期 | 严格低于执行周期的 1m / 5m … | `tacticalContext` |
| 趋势基调 | 本轮在**执行周期**上算出的方向倾向（本特性新增） | `payload["trendVerdict"]` |
| 顺势 / 逆势 | 开仓方向与基调同向 / 反向 | `alignment_of()` |
| 基调强度 | `strong` / `moderate` / `weak`，判据是 **`&#124;execScore&#124;`（不含高周期）**；只有 `strong` 会触发轴 D 的三条通路 | `verdict["strength"]` |
| 执行周期分 / 合成分 | `execScore` 只由执行周期五项算出，决定**强度**；`score` 含高周期调制，决定**方向** | `verdict["execScore"]` / `verdict["score"]` |
| 模型置信度 / 基调置信度 | 前者是模型自报、是逆势闸的判据；后者纯呈现，**绝不参与判定** | `decision.confidence` / `verdict["verdictConfidence"]` |
| 定格 alignment | 开仓委托发出那一刻记下的顺势/逆势归属，随 `clOrdId` 认领，用于跨轮的成交与盈亏归因 | `TrendRuntime.note_submitted()` |

---

## 评审结论（Review Verdict）

### 结论：**有条件通过**（Approved with conditions）

方案的**主线判断是对的**，而且诊断得比大多数同类 spec 更深：它没有停在「让模型多看一眼 15m」，而是把
「一直在买多」正确地归因为**协议结构性产物**（S1 锚在高周期、S2 被定义为「对 S1 的附议」、S3 明令战术
周期不得反转 S1），并给出了「确定性纯核心 + 软引导 + 方向对称的两条调制通路 + 可证伪观测 + 三把总闸」
这套与本仓库既有姿态完全同构的解法。四份 wire 直接复用、不开第二个因果窗口、不新增 LLM 车道、零 DB
迁移——这些取舍都站得住。§3.2.3 里那三条「必踩的坑」（`emaRelation` 相等判多、`macdHist.label` 零判空、
缺失成分不得按 0 计入分母）是本文最有价值的段落之一：它们都被逐一核实过，确实存在，且确实会毁掉对称性。

但 v1 有**五条阻断级缺陷**，它们共享同一个形状——**「修法落在它以为的位置之外」**：

1. 顺势追加票的判据不携带本轮动作，于是它会给**逆势**开仓发票（RP0-1）；
2. 它唯一被指派去解决的断面（信号板一边倒），恰恰被它自己的第三条护栏挡死，而它的残余效果只为**做多**
   点亮（RP0-2）；
3. 强度取自含高周期的合成分再叠加冲突降档，使两条调制通路**在目标断面上同时熄火**（RP0-3）；
4. 链路最下游的复核官仍被**明文要求**否掉「与高周期冲突」的单，而 v1 明令不许改它（RP0-4）；
5. 顺势/逆势的成绩没有跨轮载体，卡面第 4 行会被系统性地记错一侧，而单轮用例恒绿（RP0-5）。

五条已在 v2 正文中逐条改掉；另有 13 条 P1 同样已改，8 条 P2 登记不改。改动集中在四处：
`execScore` 与 `strength` 的解耦、冲突「只收紧不放松」的极性收敛、新增 D-1b 与复核侧条件化子句、
以及 alignment 的定格认领表。

### 放行条件（合入前必须全部满足）

| # | 条件 | 判据 |
|---|---|---|
| C-1 | **先还 `prompts.py` 的外移债**（`.claude-index/config.md` 第四次登记的硬要求），或写第五次登记并说明理由 | 一个零行为变更的独立提交 + 既有 sha256 冻结全绿；或 config.md 的新登记条目 |
| C-2 | **轴 C 的决策侧与复核侧同轮上线** | 用例 P-9 三处断言全绿；只改决策侧不改复核侧的中间态**不得**合入 |
| C-3 | **RP0-1 / RP0-2 / RP0-3 / RP0-5 各自的回归护栏用例先红后绿** | D-11 / D-12 / A-11 / A-12 / S-6b 五条；照 v1 的写法实现，这五条必红 |
| C-4 | **三把「放宽 / 收紧」总闸的极性可分别回退** | 验收标准 §7.7-5 |
| C-5 | **§7.7-3b 的三条可证伪验收在真实会话上跑过一遍** | 逐层核对那一轮死在哪里，且界面把层名写出来 |
| C-6 | **全量回归按 §7.6 跑**（`python -u -m pytest -q` 不并发、不用 `-k` 子集；`NODE_ENV=test pnpm vitest run`；`tsc --noEmit`），并与合入前基线逐条对拍 | 新增红条数为 0 |

### 仍需实现节点自行判断的（P2，不阻断）

`_consensus_wire` 的形参收敛（RP2-1）、纪律自检第 14 行到底开不开（RP1-3 给的是「要么开齐四个落点、
要么把前缀一并删掉」，两条都可接受）、`intents` 第三格的命名（RP2-5）、以及裸 `counter_trend` 兜底条目
留不留（RP2-6）。

### 一条给实现节点的提醒

本轮改的是**决策链最上游的锚点**与**最下游的复核判据**，中间十来层一个都没动。这意味着任何一处漏改都
不会报错，只会表现为「模型说了偏空、系统还是只开多」。§7.7 的四条产品验收和 §7.3 的 S-6b / S-7 / S-8
是唯一能把这件事证伪的东西——**先把它们跑通，再看多空比**。多空比是结果，不是判据；用它做验收，就会
在下一段单边行情里再一次得出「修好了」或「没修好」这两个都不可靠的结论。


---

## 实施过程发现的方案缺陷（Issues Found During Implementation）

> 本节由**实现节点**追加（2026-09-01）。每一条都是「照 v2 正文的字面写法实现，会得到一个
> 不报错但不对的东西」。已按下述修法实现，正文未改——本节是正文的增补而非替代。

### F-1（P1）：`format_trend_verdict_for_prompt(verdict, tier)` 的签名拿不到它要渲染的两个阈值

**问题**：§3.4.2 给出的渲染样例最后一段逐字写着「逆势开仓需你自报更高的置信度并会被强制降档
（当前：**置信度 ≥0.65，仓位 ×0.5**）」，而该函数的签名（§3.4.2 明确给出）**只吃 `verdict`**。
`verdict` 的 wire（§3.2.4）里没有 `counter_min_confidence` / `counter_size_mult` 这两个数——
它们住在 `TrendVerdictRule` 上。照字面实现只有两条路：① 把 `0.65 / ×0.5` 写死进文案（一个改过
`AI_TRADE_TREND_COUNTER_MIN_CONFIDENCE` 的会话会读到一句**假话**，而本仓库对「绝不写死算例」
已有成文纪律，`format_horizon_charter` 就是为此按本会话真实杠杆现算的）；② 给函数加第三个形参
（那要把 rule 一路透传到 `prompts._trend_verdict_lines`，而它只吃 payload）。

**修法**：把逆势闸的**生效阈值**随基调 wire 一同下发，新增 `counterGate` 子块：

```jsonc
"counterGate": {"armed": true, "minConfidence": 0.65, "sizeMult": 0.5}
```

真源仍是会话那份 `TrendVerdictRule`（`build_trend_verdict` 装配时读一次），渲染层只读不算。
闸未装配时 `armed=false` 且两个数为 `null`，文案改口为「本会话未装配逆势降档闸，逆势开仓不
额外收紧」——**如实说它没装配**，不冒充生效。该子块同时被前端类型与纪律自检第 14 行消费。
用例：`test_ai_trade_prompts.py::test_trend_section_never_hardcodes_the_counter_gate_thresholds`
与 `test_ai_trade_trend_verdict.py::test_counter_gate_thresholds_ride_along_the_wire`。

### F-2（P1）：`_consensus_wire` 的 `trendBonusLane` 五态无法由 §3.6.2 给出的签名产生

**问题**：§3.6.2 给 `resolve_consensus_gate` 的新签名是两个整数
（`bonus_votes` / `opposing_allowance`），而同一节又要求 `_consensus_wire` 下发
`trendBonusLane ∈ {none, applied, blocked_no_native_vote, blocked_conflict, blocked_not_aligned}`。
`bonus_votes == 0` 无法区分后四态，且门禁纯核心**拿不到 verdict**——要在那里重判一次，就必须
把 verdict 传进去，那就造出了第二份会漂移的判据（本仓库对「同一个量在两处各算一次」已有成文
教训）。只发 `applied / none` 又会让「本轮为什么没放宽」这个问题在界面上无法回答，而 §3.3.2
第 3 条明确要求两条通路**各自可归因**。

**修法**：`resolve_consensus_gate` 再加一个 **keyword-only** 形参 `bonus_lane: str = "none"`
（默认值 ⇒ 既有全部调用逐字节回退）。判定源仍是唯一的
`trend_verdict.trend_bonus_lane(action, verdict, consensus, rule)`，门禁只负责把它原样落进 wire。
位置参数仍是 3 个，超出的全是 keyword-only（与 `.claude-index/config.md` 里 `intel/**` 那条
已登记的同族许可一致）。

### F-3（P1）：`attribution.py` 标为「不改」，但它**没有**净额盈亏的公开读点

**问题**：§3.7.1 第 4 条要求「结算的净额盈亏口径**取自 `AttributionLedger` 已有的 `net`**
（`record_close` 里那一份），不另算」，而 §4.1 又把 `attribution.py` 标为「不改」。该模块把
`net` 存进私有 `self._trips`，**没有任何公开访问器**；`trip_meta_wire` 的 17 个键里也没有
净额（它发的是 bps 与费用）。照字面实现只能去摸 `_trips[-1]`（破坏封装），或在会话侧按
`pnl ± fee` 再算一遍（正是这条纪律禁止的事）。

**修法**：给 `AttributionLedger` 加一个**只读**访问器 `last_close_net_usd()`（4 行，纯读、
无副作用），`CostRuntime` 加一个同名薄转发。这是对 §4.1 那格「不改」的最小偏离，且它服务的
正是同一节自己写下的口径要求。

### F-4（P2）：复核官的中文系统 prompt 是 §3.5.1 三处之外的**第四份镜像**

**问题**：§3.5.1 列了三处「与高周期强冲突 ⇒ abort」的原文，但
`prompts.build_reflection_system_prompt` 的**中文**分支（`off` 档的真源）另有一份
「(b) 与更高周期背景强冲突且决策未给出对冲理由」。它与 §3.5.1(b) 点名的英文
`render_review_spine_en` 是两条**并列**的语言路径（`reasoning_text` 为空时走中文）。只改英文
那一份，`off` 档会话的复核官仍被明文要求否掉本特性想放行的单——正是根因 R-5 的原样存活。

**修法**：`build_reflection_system_prompt` 增第三个形参 `trend_bar: str = ""`（默认 ⇒ 逐字节
回退），中文与英文两条分支**共用同一个条件化子句**；`decider._stream_reflection` 是它全仓唯一
的调用点，从当轮 payload 现取一次同时喂两处。另：两处的 disarmed 原句**措辞不同**
（系统 prompt 是「决策未给出对冲理由」，任务 prompt 的散文是「无对冲理由」），故
`_review_conflict_clause(trend_bar, legacy)` 由调用方传入自己的逐字原句——共用一个写死的
disarmed 串会让其中一处在总闸关时**静默改文案**，而 sha256 冻结门禁恰好只守另一处。

### F-5（P2）：`_render_spine_zh` 拿不到 `trend_bar`，但它**已经**有 `bar`

**问题**：§3.4.3(b) 要求中文脊柱按 `trend_bar` 分叉，而 `_render_spine_zh` 的形参里没有这个键；
照字面新增一个 `SystemPromptTexts.trend_bar` 字段，就有了**两个**装配判据（`trend_text` 与
`trend_bar`），它们可能不同步，而不同步的表现是「宪章说基调判在 15m、纪律 #1 却仍要求先读
日线」且不报任何错。

**修法**：装配判据**收敛为一个**——第七段宪章装没装（`texts.trend_text`）。`bar` 本就是
`_render_spine_zh` / `render_spine_en` 的位置形参，故
`trend_bar = bar if texts.trend_text else ""`，零新字段。这与 §3.5.1(b) 自己对复核侧采取的
「复用同一个字段、不新增第八个字段」是同一条纪律。

### F-6（P2）：本节点**无法**兑现 §十 第 0 步的「单独一个提交」

`prompts.py` 的 `_format_*` 外移债（评审 RP1-5 / 放行条件 C-1）要求一次**零行为变更的独立
提交**。本实施节点被显式禁止 `git commit`（提交是下游节点的职责），故那个提交在本节点物理上
不可能产生；而把纯搬运与本特性的行为改动混进同一份工作树，恰恰会让 §7.6 的 G-1 / S-5
「总闸关 ⇒ 决策 prompt sha256 与合入前逐字节相同」失去全部说服力——那正是 RP1-5 自己写明要
避免的事。故按 C-1 允许的第二条路径处理：**已在 `.claude-index/config.md` 写第五次登记**，
逐条说明理由，并把本轮对 `prompts.py` 的净增压到最小（基调段的整段文案外移到新文件
`ai_auto/trend_text.py`，本文件只留 `format_trend_charter` / `_trend_read_order` /
`_review_conflict_clause` 三个短函数与两处插入点）。**下一个触碰 `prompts.py` 的特性仍须先还
这笔债。**

### F-7（P0，实施期自查发现）：`note_submitted` 排在 `_execute_decision` **之后**，市价开仓腿必落 `unknown`

**问题**：§3.7.1 与 §3.9 都把 alignment 的定格写成**一个**入口
`note_submitted(clOrdId, alignment)`，且把它排在 `_execute_decision` **之后**（挂单的
`clOrdId` 只在 `executor.resting` 上，而它要到下单之后才存在）。但**市价单的成交事件是在
`_execute_decision` 内部经 `_dispatch` 发出的**——`note_fill` 在那一刻就跑了，而轮级槽此时
还是空的。净效果：**每一笔市价开仓腿都落进 `unknown`**，卡面照样有数、不报任何错，且
S-6b（限价跨轮）那条护栏**恒绿**，因为它走的正好是另一条路径。

这与评审 RP0-5 自己指出的形状**完全同款**（「单轮用例恒绿、成绩记到错误的一侧」），只是
错的那一格从 `counter` 换成了 `unknown`。

**修法**：拆成两个入口，与 `AttributionLedger` 早就为同一件事确立的姿态**逐字同构**：

| 步 | 方法 | 落点 | 对照 |
|---|---|---|---|
| ① | `note_alignment(alignment)` | `_execute_decision` **之前** | `attribution.note_estimate` |
| ② | `claim_alignment(resting)` | `_execute_decision` **之后** | `attribution.claim_estimate` |

①置轮级槽（市价单的成交与本轮同步到达，够用）；②把轮级值冻结进按 `clOrdId` 的认领表（限价
单跨轮）。`alignment` 为 `neutral`（非开仓 / 基调不可用）时**不置位**——那一轮的成交本就该落
进 `unknown`，绝不猜。用例
`test_ai_trade_trend_session.py::test_round_slot_is_set_before_execution_so_market_fills_are_attributed`
是它的专属回归护栏。

### F-8（P1）：§3.8 那条「写进 `format_backtest_notes`」的披露，照字面写会**弄红一道既有冻结门禁**

**问题**：§3.8 要求把「回测侧没有战术周期证据、因此比实盘略宽」这条差异写进回测说明。照字面
把那个 camelCase 键名写进 `backtest.py`（无论写在注释里还是写在 notes 文案里），会立刻撞上
`test_ai_backtest_runner.py::test_g2_backtest_payload_never_carries_wake_or_tactical_keys`——它是
一道**源码字面扫描**门禁（`inspect.getsource(backtest_module)` 里不得出现那四个 token），存在的
理由是「防止将来有人顺手把战术段接进回测」，而战术段在回测里就是未来函数。它不区分代码与注释。

**修法**：披露文案改用中文说法「**没有战术周期证据段**」，并在 `backtest.py` 与本轮新增用例两处
都写清「此处刻意不写出那几个 camelCase 键名，因为 G-2 是字面扫描门禁」。**绝不**为了让自己的
注释好看而去削弱一道既有的冻结门禁——那道门禁守的东西比这条注释重要得多。

### F-9（P1）：`_apply_consensus_gate` 的**替身 self** 必须随会话新属性同步补齐

**问题**：`test_ai_trade_consensus.py` 用 `SimpleNamespace` 造一个替身 self、**未绑定**调用
`AiTradeSession._apply_consensus_gate(host, ...)`。本轮该方法开始读 `self._trend` /
`self._trend_rule`，六条既有用例当场 `AttributeError`。这与本仓库已成文的「冻结注册表 / snapshot
替身 self」是同一个形状：**会话新增一个被薄封装读到的属性，替身就必须同步补齐**。

**修法**：替身补上 `_trend_rule=TrendVerdictRule()` 与 `_trend=TrendRuntime(TrendVerdictRule())`
（**未装配**的规则）。这样补而不是加 `getattr(self, "_trend", None)` 兜底，是刻意的：默认未装配
的规则让两条放宽通路恒返回 0，于是那六条用例的断言与本特性合入前**逐字相同**——它顺带成了
「总闸关即逐字节回退」的一个会话级证明；而一个 `getattr` 兜底会在真实会话里把整个特性静默关掉
且不报任何错。

### 未做的一项（如实登记）

§7.7-3b 与放行条件 C-5 要求「在**真实会话**上跑一遍三条可证伪验收」（需要真实交易所凭证与一段
真实行情）。本节点是离线实施节点，无法起真实会话，故该项**未执行**；它对应的机制已由
`test_ai_trade_trend_gate.py::test_board_one_sided_short_is_the_target_cross_section`（D-12 的
三段式断言）与 `test_ai_trade_prompts.py::test_reviewer_conflict_clause_is_conditional_on_the_verdict`
（P-9 三处）在离线断面上覆盖。

---

## 终审记录（Final Review · 合入前）

> 本节由**终审节点**追加（2026-09-02）。逐条对照 §四《文件 / 模块变更计划》核过覆盖度、
> 回归风险与命名/风格，改动**全部落在源码里**，不留 TODO。§四的每一格都有对应实现，
> 无遗漏项；下列是终审自己发现并已修掉的 8 条。

### R-1（P1，行为）：`alignment_of` 把强度并进了判据，观测侧因此系统性丢样本

`trend_verdict._usable` 一个谓词同时承担了两件事——「基调给不给得出方向」与「强不强到能触发
轴 D」——而 `alignment_of` 用的是后者。可 §3.3.1 的第 ④ 步与 §3.3.2 的共享前置**各自单列**了
`strength == "strong"`，正说明这两条判据在方案里本就是分开的。

合并的后果不在门禁上（那里怎么算都是 exempt），而在**观测**上：一个「偏空·强度中」的轮次开出
的空单，`alignment_of` 返回 `neutral` ⇒ 统计卡第 2 行（开仓意愿）与第 4 行（顺势 / 逆势成绩）
把它记进 `unknown` 格。而 §7.7-3 的产品验收恰恰要在那两行上读出「空单意愿到底有没有出现」——
本特性唯一可证伪的产品判据，会被自己的分桶判据稀释掉，且**不报任何错**。

**修法**：拆成 `_directional`（方向，不含强度，供 `alignment_of` 与观测分桶）与 `_usable`
（`_directional` + `strong`，供轴 D 三条通路），并把 §3.3.1 第 ④ 步的强度判定**显式写回**
`apply_trend_bias_gate`（此前它是靠 `alignment_of` 顺带实现的）。护栏：
`test_ai_trade_trend_gate.py::test_moderate_verdict_still_has_an_alignment_even_though_no_lane_arms`
与 `test_ai_trade_trend_session.py::test_moderate_verdict_intents_are_still_bucketed_by_side`。

### R-2（P1）：`trendGate` 是一份「有写点、没读点」的死数据

`tool_call` 相位下发了 `trendGate`、`aiTradeTypes.ts` 补了类型、`store.ts` 逐键手抄了它、
`aiTradeTrace.test.ts` 还冻结了那条手抄轨——但**没有任何组件渲染它**，`aiTradeTrend.ts` 里
为它写好的 `describeTrendGateAlignment` 因此零消费者（死导出）。这正是 `session.py` 自己注释里
写着的那句「后端算了、类型补了、没有渲染落点 = 死数据」。

被**拦下**那一档尚可从降级码文案与层名读到；**逆势放行并被 ×0.5 降档**那一档不行——那一轮没有
任何降级码，界面上完全看不见仓位被砍了一半。

**修法**：`AiTradeTraceRound.tsx` 新增 `TrendGateRow`，挂在 `ProposedVetoRow` 之后（卡片顺序即
管线顺序）。只显示 `modelConfidence`（本层唯一的判据），`verdictConfidence` 不并进同一句——
两个数取错方向正好相反。护栏：`AiTradeAgentTrace.test.tsx` 的两档断言（放行档验「50% → 25%」，
否决档验「模型自报置信 40% / 门槛 65%」且**不出现** `verdictConfidence` 的 99%）。

### R-3（P1）：S-1 与实施期发现 F-7 守的是「一行代码的位置」，而用例守的是产物

两条都属于「挪一行就静默失效、且不报错」：`trendVerdict` 装配点若上移到 `_wake.inject` 之前，
`conflict` 恒 `"none"`；`note_alignment` 若下移到 `_execute_decision` 之后，每一笔市价开仓腿都
落进 `unknown`。而现有的两条用例分别只断言了「纯核心读得到 tactical」与「运行时两步入口按顺序
调用时结果正确」——把源码里那一行挪走，两条照样绿。

**修法**：新增 `test_session_call_order_is_frozen_in_source`，用 `inspect.getsource` 冻结三组
顺序（本仓库已有十余处同款源码门禁先例）：
`_wake.inject < build_trend_verdict < _experience.inject_round`、
`_sizing.gate < _apply_trend_bias_gate < _apply_consensus_gate`、
`note_alignment < _execute_decision < claim_alignment`。

### R-4 … R-8（P2，风格与注释）

- `signals.py`：新增五个 wire 键后留下的三连空行，收回两行。
- `manager.py` / `backtest.py` / `api/ai_auto.py` / `AiTradeLaunchModal.tsx`：四处新增 import
  插在了字母序中间（`.trend_verdict` 落在 `.session` 与 `.signals` 之间等），各自归位。
- `discipline_view.DisciplineFacts.trend_rule`：注释把 dataclass 的约束说反了（写成「必须排在
  全部**带**默认值的字段之后」），改为如实说明「追加在末位，既满足『无默认值字段必须在前』，
  也让既有的按位置构造点一个都不用改」。
- `store.ts`：注释引用了本文件里不存在的变量名 `base.trendGate`（实际是 `t.trendGate`）。
- `api/ai_auto._resolve_trend`：`raw.overrides()` 在循环体内被重复求值 5 次，提到循环外。

### 终审判定为「不改」的三条（登记，避免下一轮重复讨论）

1. **`render_spine_en` armed 分支的行尾空格**（`"...without exception. " + "
"`）：要去掉它就得
   动 disarmed 分支的字面量，而那条路径正被 sha256 冻结门禁按字节守着。收益远小于代价。
2. **`_status_payload["trendVerdict"]` 恒有键、值可为 `None`**（§5.4 写的是「整键缺席」）：它与
   紧邻的 `openFunnel` / `tradability` 姿态一致，前端类型也是 `?: T | null`。落库那一侧
   （`merge_into`）是真正的整键缺席，与 spec 一致。
3. **裸 `counter_trend` 兜底文案条目今天永不命中**（后端只产出两个细分码，RP2-6 允许两条路）：
   保留是一条廉价保险——下一轮新增第三个细分码时，用户看到的是中文而不是英文原始码。
   该意图已写进 `aiTrade.ts` 的注释里。

### 未执行项（如实登记，与实施节点同一条）

§7.7-3b 与放行条件 C-5 要求的「在**真实会话**上跑一遍三条可证伪验收」需要真实交易所凭证与一段
真实行情，终审节点同样是离线节点，**未执行**。离线断面由 `test_ai_trade_trend_gate.py::
test_board_one_sided_short_is_the_target_cross_section`（D-12 三段式）与
`test_ai_trade_prompts.py::test_reviewer_conflict_clause_is_conditional_on_the_verdict`（P-9 三处）
覆盖。**第一次实盘启用后应优先按 §7.7-3b 逐层核对一轮。**

### 回归门禁（合入前实测）

| 门禁 | 结果 |
|---|---|
| `services/quant` 全量 `python -u -m pytest -q`（不并发、不用 `-k` 子集） | 见提交说明；失败集合与合入前基线**逐条相同**（全在未跟踪的 `test_factor_library_expansion.py`，与本特性无关），净新增红 **0** |
| `apps/web` `NODE_ENV=test vitest run` | 223 文件 / **2567** 用例全绿（含终审新增 1 条） |
| `apps/web` `tsc --noEmit` | 干净 |
| `ruff check services/quant/app services/quant/tests` | All checks passed |
| `desktop` `node --test scripts/config-passthrough.test.mjs` | 2/2 绿（`AI_TRADE_*` 是前缀透传，10 个新键自动到位，无需改桌面端） |
