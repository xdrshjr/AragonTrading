# AI 自动交易 · 综合研判增强包（AI Auto-Trading · Enhanced Deliberation Pack）开发方案

- **Feature key**: `ai-auto-trading-enhancements`
- **文档版本**: **v4**（iteration 2 · Subtask #1 方案评审节点产出；在 Subtask #0 设计稿 **v3** 上完成逐节评审、就地修复全部 P0/P1，并附评审记录与评审结论）。**版本号说明**：本增强包沿用「已实现并提交的 AI 自动交易基线功能 v2（commit `4b13b69`）」的连续修订编号——设计稿从 v3 起、经本次评审为 **v4**；正文中的「本 v3」「Why v3」是本增强包这一**设计迭代的代号**（第三版设计，承接基线 v2），与文档修订版本号 v4 不冲突、亦不与基线功能的 v2 混淆。
- **作者**: 方案设计节点（solution architect）
- **上游基线（已实现且已 commit）**: 本方案的全部前置——「AI 自动交易」第三种自动交易类型——已在 iteration 1 完成设计（`docs/plans/ai-auto-trading/spec.md` v2）、实现、评审并提交（commit `4b13b69`：35 文件 / 4614 行）。后端 `services/quant/app/ai_auto/`（`signals`/`decider`/`prompts`/`session`/`market_window`/`manager`/`admission`/`__init__`）+ `api/ai_auto.py` + `config.py`/`schemas.py`/`main.py` 接线；前端 `apps/web/lib/aiTrade*`/`useAiTradeStream` + `components/Agent/AiTradeToggle|AiTradeLaunchModal` + `components/Chart/AiTradePanel` + store（已在 **v25**）；桌面 `desktop/src/lib/config.js` 已透传 `AI_TRADE_` 前缀。**本 v3 不重造 v2，只在其之上做增量增强。**
- **关联红线（真实资金安全，一等需求 · 与 v2 完全一致）**: 本增强包**不新增任何绕过 `okx/trading.py` 服务层的下单路径**，一切真单仍经 `LiveExecutor` → `okx/trading.py`（继承 `LIVE_TRADING_ENABLED` 守门、单笔名义上限、净仓反向拦截、唯一 clOrdId、`live_order_audit` 留痕、脱敏），叠加既有 `AI_TRADE_ENABLED` 双闸、与 live-auto 原子互斥、预算/名义双上限、会话级止盈止损、无既有持仓门禁、LLM 失败/超时回落 `hold` 等全部补偿控制。**本 v3 的每一项增强要么是只读的决策输入增强（零新增下单面），要么是严格「更保守」的补偿控制（只会把开仓降级为 hold），要么是构造期风控参数化（不改 `LiveExecutor` 代码）——没有任何一项会放大真实资金敞口。**

---

## 评审记录（Review Notes · Subtask #1 方案评审）

> 评审人：资深评审节点（senior reviewer）。评审维度：**可行性 / 完备性 / 一致性 / 规模合理性**。方法：不凭 v3 纸面签名，而是把 spec 的每一处 as-built 断言**回对当前工作树的真实代码**（后端 `services/quant/app/*`、前端 `apps/web/*`）逐条复核后再判级。下述结论均附真实代码依据（`file:line`）。凡标「**已就地修复**」者，本 v4 正文对应小节已按修复方案改写；凡标「**下游须遵守**」者，是 Subtask #2（代码开发）落地时必须逐字兑现的约束。
>
> **总体评价**：设计骨架扎实、边界清晰、默认安全姿态正确，四项增强（E1–E4）确实内聚、可独立开关、且真实资金敞口不被放大。§2.0 的 as-built 对齐表**大部分准确**（`classify_regime`/`RegimeParams.min_bars=60`/`decide()` 返回 `(decision, degrade)` 元组/`commit_round` 与 `decide` 拆分/`_exec_lock`+`_deciding`/`LiveExecutor(risk=…)`/`RiskParams` 三字段/store v25 瞬态切片/`useAiTradeStream` 整体透传 均**复核为真**）。但复核也暴露了 4 个必须先修的缺陷与若干一致性瑕疵，详见下。

### P0（阻断级）

- **无。** 设计整体可在现有栈上落地，未发现任何一项在技术上不可实现或会绕过 `okx/trading.py` 护栏。

### P1（须就地修复，且下游须遵守）

| 编号 | 维度 | 问题 | 代码依据 | 处置 |
|---|---|---|---|---|
| **R-P1-1** | 可行性/完备性 | **E3 决策事件补字段的数据管线欠说明**。spec 称「在 `_emit_decision` 追加 `marketContext/consensus/degradeReason` 三个可选字段」，但 as-built `_emit_decision(self, round_no, ts, decision, board, executed)` **既不接收 payload、也无这三项数据**；且 `_build_decision_payload` 当前只产出 `round/barTs/lastPx/position/sessionPnlUsd/budgetUsd/takeProfitUsd/stopLossUsd/signalBoard`，**根本没有 `marketContext`/`consensus` 键**。照 spec「按行实现、无需再决策」是**做不到**的——必须扩 `_emit_decision` 签名并从 `_run_decision_round` 内的局部变量把值线程进去。 | `session.py:428`（`_emit_decision` 签名）、`session.py:404-412`（payload 键集）、`prompts.py:73`（`build_decision_prompt` 收 payload） | **已就地修复**：§2.1 流程、§2.4、§2.0 表、§三 change-plan 均给出 `_emit_decision` 新增 3 个 keyword 形参、payload 补 2 键、`AiTradeDecisionOut` 仅为文档镜像（真正上线的是 `_emit_decision` 手工 dict）的确切写法。 |
| **R-P1-2** | 一致性/正确性 | **市场体制标签枚举与真实 `engine/regime` 不符**。spec 的 wire 示例与前端映射用了**不存在的**标签 `uptrend`/`high_vol`。真实枚举为 `primary∈{trend_up,trend_down,range}`、`volatility∈{high_volatility,low_volatility,normal}`、`event∈{crash,breakout,none}`。若开发照抄示例写 `describeRegime`，中文映射会对真实数据**全部落空**（回退成裸英文标签），E1 单测也会断言错值。 | `regime.py:161-168/184-189/245-246`（三组判定的真实取值）、`regime.py:89-100`（`RegimeResult` 字段） | **已就地修复**：§2.2/§2.4/§4.2/§5.1/§7.1 全部改用真实枚举，并在 §2.4 增补 `describeRegime` 映射表、把 E1 断言钉死为 `primary=="trend_up"`。 |
| **R-P1-3** | 完备性/正确性（触及本功能核心「上下文纯净」需求） | **共识门禁降级后，摘要/日志须记录『真实动作』**。门禁把 `open_*→hold` 后，若 `commit_round`/`_journal`/`summarize` 记录的仍是**门禁前的开仓**，则 AI 的滚动摘要会「记住一笔从未开过的仓」，直接污染后续轮次上下文——这与原始需求「保持上下文纯净」相悖。spec 未显式规定 `commit_round` 必须拿门禁后的最终 `decision`，也无对应断言。 | `session.py:310-325`（`_run_decision_round`：decide→execute→commit_round 顺序）、`decider.py:156`（`commit_round(payload, decision, executed)`） | **已就地修复**：§2.1 流程钉死「门禁重赋值 `decision` 后再 `_execute_decision` 与 `commit_round`」；§2.3、§三、§7.1 增「journal/摘要记录降级后 hold（含 degradeReason）」的实现约束与断言。 |
| **R-P1-4** | 一致性（安全契约用词与事实不符） | **「默认下单行为与 v2 完全一致」为过度断言**。E1 默认 `ai_trade_regime_context=True`、E2 共识度 always-on，二者默认各向决策 prompt 注入一行只读上下文，故**决策者（LLM）看到的输入与 v2 不同，其输出（乃至真单）可能不同**。确定性护栏（门禁/夹紧/互斥/名义上限）才是与 v2 逐字一致的部分。此句出现在 §八「风险缓解」这一安全契约里，Subtask #3 将「按 spec 核验」，一个不可核验的安全声明是隐患。 | `config.py`（拟新增 `ai_trade_regime_context: bool=True`）、§2.6/§八 措辞 | **已就地修复**：§2.6 关键不变式与 §八 风险表逐字改为「**确定性护栏**与 v2 完全一致；默认 prompt 多两行只读上下文，LLM 决策可能因此不同（属预期的『综合研判增强』，非缺陷）」。保留 regime 默认开——它正服务用户明确的『综合研判』诉求。 |

### P2（建议 / 一致性瑕疵；本 v4 已一并就地修复以免下游踩坑）

| 编号 | 维度 | 问题 | 代码依据 | 处置 |
|---|---|---|---|---|
| **R-P2-1** | 一致性 | **共识门禁入参的 dataclass ↔ wire-dict 混用**。§2.1 把 `compute_consensus(board)`（`Consensus` dataclass）传给 `_apply_consensus_gate`，但 §2.3 门禁体却按 `consensus["longVotes"]`（wire dict）取值——照 §2.1 直译会 `TypeError`。 | 设计内部不一致 | **已就地修复**：门禁统一收 `Consensus` dataclass、用 `.long_votes/.short_votes`；wire 形（`_consensus_wire`）只供 prompt/事件，`compute_consensus` 每轮只算一次。 |
| **R-P2-2** | 一致性（避免造孤岛，CLAUDE.md §八-8） | **`parseOptionalPct`/`clampPct` 落点错**。spec 说放 `lib/aiTrade.ts`，但其对标的 `parseOptionalUsd`/`parseOptionalLeverage` 实际住在 `lib/liveAuto.ts`，且 `AiTradeLaunchModal` 已 `from "@/lib/liveAuto"` 导入。放 `aiTrade.ts` 会与既有解析器割裂。 | `liveAuto.ts:45/56`、`AiTradeLaunchModal.tsx:17-22` | **已就地修复**：§2.5、§三把 `parseOptionalPct`/`clampPct` 归到 `lib/liveAuto.ts`（与 USD/杠杆解析器同处）；AI-trade 专属的 `describeRegime/describeConsensus/describeExecuted` 仍留 `lib/aiTrade.ts`。 |
| **R-P2-3** | 完备性 | **未提既有 `ai_trade_min_confidence` 置信度门**。v2 已有一个开仓置信度门（`config.py:111`），命名与新增 `ai_trade_min_consensus` 极近，易混；应说明二者**独立叠加**（开仓须同时过置信度与共识两关）。 | `config.py:111` | **已就地修复**：§2.3 增说明。 |
| **R-P2-4** | 一致性 | **新 `degradeReason`(string) 与既有 `degraded`(boolean) 区分 + 降级串格式**。前端 `AiTradeSessionRow`/`AiTradeStatusEvent` 已有布尔 `degraded`；新增字段是**独立的字符串原因**、非重命名。且门禁降级串宜带计数 `low_consensus(votes/need)`（与 §四/§六示例一致）。 | `aiTradeTypes.ts:52/64` | **已就地修复**：§2.3 门禁返回 `f"low_consensus({votes}/{need})"`；§2.4 注明二字段之别。 |
| **R-P2-5** | 规模合理性（提示，无需改稿） | `base_df()` 与 `build_strategy_frames` 内联的 `candles_to_df` 略有重复；`classify_regime` 尚有可选第三参 `params: RegimeParams\|None`（spec 省略、用默认即可）。均属轻微、不影响落地。 | `market_window.py:102-108`、`regime.py:310` | 保留，记录备考；开发可用公有 `self.candles` 属性构造 `base_df` 以少碰私有字段。 |

**规模合理性总评**：四项增强边界清晰、无新表无迁移、store 不升版、defer 项（多标的 / 逐单 AI SL-TP / 体制驱动动态仓位）理由充分。既不过度设计、也未欠规格。**通过规模审查。**

---

## 〇、增强动机与总纲（Why v3）

iteration 1 的 v2 已完整满足原始需求：AI 助手面板「新对话」右侧的「AI 自动交易」开关、多选策略启动弹窗（总预算/杠杆/总止盈/总止损）、AI 作为决策者综合研判多策略信号板出「开多/开空/平仓/观望」、真单经 `LiveExecutor` 守护执行、轮次滚动摘要式上下文压缩、与 live-auto 互斥独占。核心链路稳健、经测（后端 39 AI 用例 + 前端 29 用例绿）。

原始需求同时强调三点，v2 已达标但**仍有明确的增量空间**：**「综合研判」**（AI 应尽可能拿到高质量、全面的输入）、**「完善、稳健、可靠」**（风控与补偿控制越细越好）、**「美观优雅、顶级设计、符合人机交互最佳实践」**（运行态可观测性越透明越好）。据此，本 v3 设计四项彼此内聚、边界清晰、逐项可独立开关的增强（下称 **E1–E4**），共同把「AI 综合研判」的**输入质量、安全下限、用户控制、运行透明度**再抬升一档：

- **E1 · 市场体制上下文（Market-Regime Context）**：把系统已有的、纯计算且严格因果的行情体制识别（`engine/regime.classify_regime`）注入每轮决策输入，让 AI 在「多策略立场」之外再叠加「当前是趋势/震荡/高低波动/突破/急跌」这层市场态势，做出更贴合行情的综合研判。**只读增强，零新增下单面。**
- **E2 · 策略共识度 + 可选共识门禁（Consensus meter & optional gate）**：把信号板压成一个可量化的「共识度」（多少策略看多/看空/空仓、净立场、一致度）随决策输入下发；并提供一个**默认关闭**的补偿控制——当开仓方向的同向策略数不足 `ai_trade_min_consensus` 时，会话层**硬性把开仓降级为 hold**（独立于 LLM）。**开启只会更保守，绝不更激进。**
- **E3 · 决策可观测性增强（Decision Observability）**：左侧 `AiTradePanel` 与决策日志把「本轮到底成交了没/是否跳过/是否降级」「开仓占用了多少预算」「距下次摘要还剩几轮」「当前体制/共识」等透明呈现，符合 HCI 最佳实践。**纯前端只读 + wire 增补字段。**
- **E4 · 会话级单仓风控可配（Per-session OCO risk config）**：启动弹窗新增「单仓止损%/单仓止盈%」两项（留空＝沿用 config 默认），服务端夹紧到安全上限后，作为**构造期** `RiskParams` 传给该会话独占的 `LiveExecutor`——**不改 `LiveExecutor` 任何代码**（其构造本就接受 `risk`），只是把原先写死的 config 默认变成用户可在夹紧区间内设定。满足「填入相关参数、稳健可靠」。

四项相互独立、可分别通过 config/入参开关，任何一项失败或降级都不影响其余与既有 v2 主链路（安全降级优先）。

**明确不做（Deliberately deferred，见 §八）**：多标的 AI 自动交易、AI 在决策工具里自定义**逐单**SL/TP（需改 `LiveExecutor.apply` 的共享代码或跨仓语义）。二者均会**放大真实资金敞口或触碰共享执行器**，不适合在一次自动化的「设计→评审→开发→提交」迭代里对真实资金功能落地，故本 v3 明确列为后续增强并给出理由。

---

## 一、Overview（概述）

本方案是「AI 自动交易」功能的**第二迭代增强**。第一迭代（v2）解决了「有没有」——把 AI Agent 约束为「多策略信号板 → 单一有界 `submit_trade_decision` 决策 → 既有 `LiveExecutor` 守护执行」，并实现了轮次滚动摘要式上下文压缩，安全姿态扎实。第二迭代（本 v3）解决「好不好、稳不稳、透不透」——在**不放大任何真实资金敞口、不新增绕过护栏的下单路径**的前提下，围绕「综合研判质量、安全下限、用户控制、运行透明度」做四项内聚增强。

其一，**决策输入更全面（E1/E2）**。v2 喂给 AI 的输入是「每策略影子立场 + 本根边沿信号 + 真实持仓 + 会话盈亏」。本 v3 再叠加两层：一是**市场体制**——复用系统已有的 `engine/regime.classify_regime`（纯计算、严格因果、`min_bars=60`），在每个决策轮对当前 K 线窗口做一次单点体制判定（primary 趋势结构 / volatility 波动档位 / event 突破或急跌 + 置信度），把结论压成一行注入决策 prompt；二是**策略共识度**——把信号板逐策略立场聚合为「N 多 / M 空 / K 空仓 + 净立场 + 一致度」，让 AI 一眼看到「多数策略此刻站哪边、分歧有多大」。二者都是**只读**的输入增强，不触任何下单路径。

其二，**安全下限更硬（E2 门禁）**。v2 的安全回落只覆盖「LLM 失败/超时/无工具/非法」。本 v3 增加一个**默认关闭**的补偿控制：当 AI 给出开仓决策、但该方向的同向策略数不足用户/运维设定的 `ai_trade_min_consensus` 时，会话层在 `LiveExecutor.apply` 之前**硬性把决策降级为 hold**——这是独立于 LLM 的、纯确定性的「共识不足不开仓」闸，开启后系统只会更保守。

其三，**用户控制更细（E4）**。v2 的单仓交易所侧 OCO 用的是写死的 config 默认 SL%/TP%。本 v3 在启动弹窗暴露「单仓止损%/止盈%」两项（留空＝默认），服务端夹紧到 `ai_trade_max_sl_pct`/`ai_trade_max_tp_pct` 后作为构造期 `RiskParams` 传给会话 executor。这**不改 `LiveExecutor` 任何行**（构造本就收 `risk`），却让用户能在安全区间内把握「bot 死/断线后交易所端兜底止损止盈」的松紧。

其四，**运行更透明（E3）**。左侧 `AiTradePanel` 与决策日志把「本轮是否真的成交/跳过/降级、开仓占了多少预算、当前体制与共识、距下次摘要还剩几轮」以工作台既有设计 token 优雅呈现，符合人机交互最佳实践。

工程层面，改动**集中且小**：后端仅新增一个纯件 `ai_auto/market_context.py`（薄封装 `classify_regime`）、给 `ai_auto/signals.py` 加一个纯函数 `compute_consensus`、给 `ai_auto/prompts.py` 的决策 prompt 增两段格式化、给 `ai_auto/session.py` 的决策轮增「装配上下文 + 共识门禁 + 事件补字段」、给 `manager.py`/`schemas.py`/`config.py` 增少量参数化与配置字段；前端在 `AiTradeLaunchModal` 增两字段、`AiTradePanel` 增三块只读展示、`aiTradeTypes.ts`/`aiTrade.ts` 增类型与纯件 helper。**无新表、无 DB 迁移**（沿用 v2 的 `runs`/`live_order_audit`/`trades`；`mode="ai-trade"` 仍是自由字符串列，v2 已核验无需迁移）。store 无需再升版（v25 的瞬态切片已能承载增补字段——它们随 `decision`/`status`/`snapshot` 事件流转，不入 `partialize`）。

---

## 二、Technical design（技术设计）

> 术语沿用 v2：一个「决策轮」= 所选 `bar` 的一根确认 K 线；`AiTradeSession.feed_candle`（`session.py:284`）在确认根步进各策略影子 `SimBroker`（`evaluate_signal_board`）后，经 `_run_decision_round`（`session.py:310`）跑一轮 AI 决策。本 v3 的增强全部挂在这条既有链路上，不改其骨架与并发模型（`_exec_lock`/`_deciding` 保持不变）。

### 2.0 As-built 对齐（Reconciliation · 供评审/开发节点校准基线）

v2 实现相对其 spec 有若干**实现级差异**，本 v3 以**as-built 代码**为准（评审节点须以此为基线，勿据 v2 spec 纸面签名）：

| 项 | v2 spec 表述 | as-built（本 v3 依据） |
|---|---|---|
| 决策器流事件终止 | `tool_use_end` | 实为 `message_done`（`decider.py:126`）；累积 `tool_use_start`→`tool_use_args_delta`→`message_done`。 |
| 决策器职责拆分 | `decide()` 内含记录/摘要 | as-built 拆为 `decide()`（纯出决策）+ `commit_round()`（记 `_context`/`_journal` + 到期 `summarize()`）（`decider.py:97/156/180`）。**本 v3 的门禁在 `session._run_decision_round` 里、`decide` 之后 `commit_round` 之前介入。** |
| 行情窗口 | 内联在 session | as-built 抽为 `ai_auto/market_window.MarketWindow`（`market_window.py`），持 `_candles` deque + 多周期窗口 + `build_strategy_frames`。**E1 复用它取基础窗口。** |
| executor 风控 | 会话固定默认 `RiskParams` | as-built `manager._build_executor`（`manager.py:222`）用 `settings.ai_trade_default_sl_pct/tp_pct/max_hold_bars` 造 `RiskParams`；**E4 把前两者变为「入参夹紧后覆盖」**。 |
| store 版本 | 24→25 | as-built 已在 **v25**（`store.ts:770`）；本 v3 增补字段走瞬态切片，**不再升版**。 |
| 决策事件字段 | action/sizeFraction/confidence/reason/signalBoard/executed | as-built `_emit_decision(self, round_no, ts, decision, board, executed)`（`session.py:428`）**只收上述 5 参、不收 payload**，事件 dict 由 `TradeDecision` + 精简 board + `executed` 手工拼出；**E1/E2/E3 需给 `_emit_decision` 增 3 个 keyword 形参 `market_context/consensus/degrade_reason`，并在 `_run_decision_round` 内把 payload 键与门禁 `degrade` 线程进去**（详见 §2.1/§2.4，R-P1-1）。 |

### 2.1 端到端数据流（增量视角）

```text
每根确认 K 线（AiTradeSession.feed_candle, session.py:284）
  └─ evaluate_signal_board(...) → board: list[StrategySignalView]      （既有，P0-1 影子回放）
  └─ _run_decision_round(board, i, ref_px, ts)                          （既有骨架）
        1) payload = _build_decision_payload(round, ts, ref_px, board)  （既有）
             + payload["marketContext"] = build_market_context(self._window.base_df(), self.bar, self.executor.settings)  ← E1（只读；不足/禁用→None）
             + consensus = compute_consensus(board)          ← E2（Consensus dataclass，供门禁；每轮只算一次）
             + payload["consensus"] = _consensus_wire(consensus)    ← E2（wire dict，供 prompt/事件）
        2) decision, degrade = await decider.decide(payload)            （既有；prompt 内含 E1/E2 两段）
        3) decision, degrade = _apply_consensus_gate(decision, consensus, degrade)     ← E2 门禁（入参为 Consensus dataclass；仅降级 open_*→hold）
        4) executed = await _execute_decision(decision, ref_px, i, ts)  （既有；此处 decision 已是门禁后最终决策；hold→不下单）
        5) commit_round(payload, decision, executed) / summarize        ← 关键(R-P1-3)：commit_round 收门禁后的 decision，journal/摘要据此记录真实动作，杜绝「记住了从未开的仓」
        6) _emit_decision(round, ts, decision, board, executed, market_context=payload["marketContext"], consensus=payload["consensus"], degrade_reason=degrade)   ← E3（_emit_decision 新增 3 个 keyword 形参，R-P1-1）
  └─ monitor 循环 / reconcile / 会话止盈止损 / stop                      （既有，_exec_lock 串行，不变）

启动（POST /ai-trade/start）
  └─ AiTradeStartIn { …v2字段…, +stopLossPct?, +takeProfitPct? }        ← E4
        └─ manager._build_executor(risk = clamp(req.sl_pct/tp_pct or config默认))       ← E4（构造期，不改 LiveExecutor）

前端（活跃 AI 会话）
  └─ AiTradeLaunchModal: +「单仓止损%」「单仓止盈%」两字段（夹紧预览）  ← E4
  └─ AiTradePanel + 决策日志: 体制徽标 / 共识度计 / 执行结果态 / sizeFraction / 距下次摘要   ← E3
```

### 2.2 E1 · 市场体制上下文（`ai_auto/market_context.py` 新增 + prompts 注入）

**目标**：每决策轮给 AI 多一层「当前市场态势」输入，提升综合研判贴合度。**手段**：复用 `engine/regime.classify_regime(df, bar) -> RegimeResult`（`regime.py:310`，纯计算、严格因果、`RegimeParams.min_bars=60`，不足即 `raise ValueError`）。

- **新增纯件** `ai_auto/market_context.py`：
  ```python
  def build_market_context(df: pd.DataFrame, bar: str, settings: Settings) -> dict | None:
      """把当前窗口末根的体制判定压成紧凑上下文；禁用/数据不足/异常一律返回 None（安全降级）。"""
      if not settings.ai_trade_regime_context:
          return None
      try:
          r = classify_regime(df, bar)          # 严格因果单点判定
      except Exception:                          # 数据不足(<60)/计算异常 → 省略该层，不崩决策轮
          return None
      return {"primary": r.primary, "volatility": r.volatility, "event": r.event,
              "confidence": round(r.confidence, 3), "labels": list(r.labels)}
  ```
  > **R-P1-2 · 真实标签枚举（下游必须以此为准，勿臆造）**：`classify_regime` 的产出取值是固定的（`regime.py:161-168/184-189/245-246`）——`primary ∈ {"trend_up","trend_down","range"}`、`volatility ∈ {"high_volatility","low_volatility","normal"}`、`event ∈ {"crash","breakout","none"}`。**不存在** `uptrend`/`high_vol` 之类拼写；前端 `describeRegime` 与后端单测都必须映射/断言这组真值（见 §2.4 映射表、§7.1）。`classify_regime` 尚有可选第三参 `params: RegimeParams|None`（用默认 `min_bars=60` 即可，E1 不传）。
- **`MarketWindow` 增只读 helper**（`market_window.py`）：
  ```python
  def base_df(self) -> "pd.DataFrame":
      """当前基础周期窗口 → candles_to_df（供 E1 体制判定，与逐策略帧解耦）。"""
      return candles_to_df(list(self.candles))     # 用公有 candles 属性，少碰私有字段（R-P2-5）
  ```
  （`candles_to_df` 已 import，`market_window.py:14`；`candles` 是既有 `@property`，`market_window.py:52`。）
- **会话装配**（`session._build_decision_payload`, `session.py:404`）：追加
  ```python
  "marketContext": build_market_context(self._window.base_df(), self.bar, self.executor.settings),
  ```
  性能：`classify_regime` 对 ~300 根窗口做 EMA/ATR 特征，量级亚毫秒、同步、无 LLM、无 IO，落在既有「步进影子」同一快同步阶段，不影响决策轮预算。
- **prompt 注入**（`prompts.build_decision_prompt`, `prompts.py:73`）：新增 `_format_market_context(mc)` 并在信号板之前插一行：
  ```text
  市场体制：趋势结构=<primary>，波动=<volatility>，事件=<event>（判定置信 <confidence>）
  ```
  `mc is None` 时**整段省略**（决策 prompt 不出现半截空信息，保持纯净）。系统 prompt（`build_ai_trade_system_prompt`）第 2 条纪律追加半句：「同时参考本轮『市场体制』作为宏观背景，与策略立场综合研判」。

### 2.3 E2 · 策略共识度 + 可选共识门禁（`signals.compute_consensus` + session 门禁）

**共识度（只读，always-on）**——纯函数，`ai_auto/signals.py` 新增：
```python
@dataclass
class Consensus:
    long_votes: int; short_votes: int; flat_votes: int
    total: int; net_stance: str        # long|short|flat（多空票差决定；相等→flat）
    agreement: float                   # max(long,short)/total ∈ [0,1]（最大同向占比）

def compute_consensus(board: list[StrategySignalView]) -> Consensus:
    """把信号板逐策略『持久立场 stance』聚合为共识度（纯聚合，无副作用，易单测）。"""
```
- 会话装配：`_run_decision_round` 内 `consensus = compute_consensus(board)` **每轮只算一次**——`Consensus` dataclass 实例留给门禁（R-P2-1），并 `payload["consensus"] = _consensus_wire(consensus)`（wire dict `{longVotes,shortVotes,flatVotes,total,netStance,agreement}`，供 prompt 与 `decision` 事件）。
- prompt 注入（`_format_consensus`）在信号板表头前一行：`策略共识：<N>多 / <M>空 / <K>空仓，净立场=<netStance>，一致度=<agreement>`。

**共识门禁（补偿控制，默认关闭）**——新增 config `ai_trade_min_consensus: int = 0`（0=不启用）。在**会话层**（非决策器、非 LLM）实现硬门，`session._run_decision_round` 在 `decider.decide` 之后、`_execute_decision` 之前调用：
```python
def _apply_consensus_gate(self, decision, consensus: Consensus, degrade):
    """共识不足则把开仓硬降级为 hold（独立于 LLM 的补偿控制，仅更保守）。
    入参 consensus 为 Consensus dataclass（与 §2.1 一致，R-P2-1）；close/hold 永不受限。"""
    need = self.executor.settings.ai_trade_min_consensus
    if need <= 0 or decision.action not in ("open_long", "open_short"):
        return decision, degrade
    votes = consensus.long_votes if decision.action == "open_long" else consensus.short_votes
    if votes < need:
        reason = f"low_consensus({votes}/{need})"     # 带计数，作 degradeReason（R-P2-4）
        return _hold(reason), (degrade or reason)
    return decision, degrade
```
`_hold` 复用 `decider._hold`（`decider.py:58`，或在 session 内构造等价 `TradeDecision(action="hold", ...)`）。门禁只作用于 `open_long/open_short`；`close/hold` 从不被此门拦（保证「共识不足也永远能平仓保护」）。降级同样推 `_emit_degraded`（既有 `session.py:447`）并把 `degradeReason` 带进 `decision` 事件（E3）。

> 设计要点：门禁放**会话层**而非决策器/prompt，是因为它是**确定性安全闸**——即便 LLM 无视 prompt 也必被拦；且 `close` 永不受限，杜绝「共识不足导致该止损时不平仓」的反向风险。

> **R-P1-3 · 上下文纯净不变式（下游必须兑现）**：门禁在 `_run_decision_round` 内**重赋值 `decision`**（`open_*→hold`）后，`_execute_decision`、`commit_round(payload, decision, executed)`、`_emit_decision` **一律使用门禁后的最终 `decision`**。因 `commit_round` 负责写 `_context`/`_journal` 并到期 `summarize`（`decider.py:156/180`），若它拿到门禁前的开仓，滚动摘要会把「从未开过的仓」灌进后续轮次上下文——直接违背原始需求「保持上下文纯净」。§7.1 增断言：低共识开仓被降级时，`executor.apply` 未被调用 **且** journal/摘要记录的是 hold（含 `low_consensus(k/n)`）。

> **R-P2-3 · 与既有置信度门的关系**：v2 已存在开仓置信度门 `ai_trade_min_confidence`（`config.py:111`），与本 E2 新增的 `ai_trade_min_consensus` **命名相近但语义不同、互相独立、叠加生效**——开仓须**同时**通过「置信度 ≥ min_confidence」与「同向共识 ≥ min_consensus」两关（任一不足即降级 hold）。二者勿混淆；本门禁是在既有置信度门**之外**再加的一道确定性闸。

### 2.4 E3 · 决策可观测性增强（前端只读 + wire 增补）

- **wire 增补（R-P1-1，实现要点）**：`decision` 事件的 `data` 增三个**可选**字段——`marketContext?`（E1）、`consensus?`（E2）、`degradeReason?: string|null`（安全回落/共识门禁的具体原因，如 `decision_timeout`/`low_consensus(k/n)`）。**真正上线的是 `_emit_decision` 手工拼的 dict**（`session.py:430-433`），故落点是：① `_emit_decision` 增 3 个 keyword 形参 `market_context/consensus/degrade_reason`，把它们写进事件 dict；② 值来自 `_run_decision_round` 局部（`payload["marketContext"]`/`payload["consensus"]`/门禁返回的 `degrade`）；③ `schemas.AiTradeDecisionOut` 同步补三键**仅作文档/契约镜像**（该事件不经 `AiTradeDecisionOut` 序列化）。既有前端对未知字段向前兼容，旧会话无此字段亦不报错。注意 `degradeReason`(string) 与前端既有的 `degraded`(boolean，`aiTradeTypes.ts:52/64`) 是**不同字段、非重命名**（R-P2-4）。
- **`AiTradePanel`（`components/Chart/AiTradePanel.tsx`）**：
  1. **体制徽标**：`lastDecision.marketContext` 存在时，在会话概览行加一枚 `Badge`（`describeRegime` 中文化，如「上升趋势·高波动·突破」）。**`describeRegime` 必须映射真实枚举（R-P1-2）**，缺省回原值不臆造：

     | 字段 | 真值→中文 |
     |---|---|
     | `primary` | `trend_up`→上升趋势 · `trend_down`→下降趋势 · `range`→震荡 |
     | `volatility` | `high_volatility`→高波动 · `low_volatility`→低波动 · `normal`→常波动 |
     | `event` | `breakout`→突破 · `crash`→急跌 · `none`→（省略/无事件） |
  2. **共识度计**：信号板表头右侧加一条紧凑计（`describeConsensus(consensus)`→「3多/1空/1空仓 · 一致度 60%」），净立场用 `--long`/`--short`/`--muted` 着色。
  3. **执行结果态**：`LastDecision` 与 `DecisionLog` 每条按 `executed`/`degradeReason` 渲染一枚状态点——`已成交`(long/short 色)/`已跳过`(muted，`decision_in_flight`)/`已降级观望`(short 色，附 reason)/`观望`(muted)；开仓另显 `sizeFraction`（`describeExecuted` 统一映射）。
  4. **距下次摘要**：概览「上下文」格补一句「距下次摘要 `contextRefreshRounds - roundsSinceSummary` 轮」（数据已在 `session` 视图，纯派生）。
- **纯件 helper**（`lib/aiTrade.ts`，均无副作用、易单测）：`describeRegime`、`describeConsensus`、`describeExecuted(action, executed, degradeReason)`。
- **无新 WS 事件类型**：E1/E2 数据搭 `decision` 事件顺风车下发，`useAiTradeStream.routeEvent`（`useAiTradeStream.ts:86`）的 `decision` 分支已 `pushAiTradeDecision` + `patchAiTradeSession({lastDecision})`，字段自然透传，**无需改 stream 路由**。

### 2.5 E4 · 会话级单仓风控可配（构造期参数化，不改 LiveExecutor）

- **入参**：`AiTradeStartIn`（`schemas.py`）+ `AiTradeStartRequest`（`manager.py:55`）各增 `stopLossPct: float|None` / `takeProfitPct: float|None`（可选，留空＝沿用 config 默认）。API `ai_trade_start`（`api/ai_auto.py:72`）把 `body.stopLossPct/takeProfitPct` 透传进 `AiTradeStartRequest`。
- **夹紧 + 构造**（`manager._build_executor`, `manager.py:222`）：新增静态 `_resolve_trade_risk(req, settings) -> RiskParams`——
  ```python
  sl = _clamp(req.stop_loss_pct, 0.0, settings.ai_trade_max_sl_pct) if req.stop_loss_pct is not None \
       else settings.ai_trade_default_sl_pct
  tp = _clamp(req.take_profit_pct, 0.0, settings.ai_trade_max_tp_pct) if req.take_profit_pct is not None \
       else settings.ai_trade_default_tp_pct
  return RiskParams(stop_loss_pct=sl or None, take_profit_pct=tp or None,
                    max_hold_bars=settings.ai_trade_default_max_hold_bars or None)
  ```
  其余构造（budget/leverage/标的元数据）不变。**`LiveExecutor` 代码零改动**——它构造本就收 `risk`（`executor.py:50`），此处只是把「写死 config 默认」换成「入参夹紧后覆盖」。
- **回执**：`start` 响应（`api/ai_auto.py:89`）追加 `stopLossPct`/`takeProfitPct`（executor 生效值，从 `sess.executor.risk` 读回），供前端确认夹紧结果。
- **前端**（`AiTradeLaunchModal.tsx`）：在既有四金额字段（`:214-217`）下方加一行两字段「单仓止损%」「单仓止盈%」（占位「留空＝默认」），经 `parseOptionalPct`（**新增于 `lib/liveAuto.ts`**——与既有 `parseOptionalUsd`/`parseOptionalLeverage` 同处，避免造孤岛；modal 已 `from "@/lib/liveAuto"` 导入这些解析器，R-P2-2；语义同 `parseOptionalUsd`，校验 `0<x≤max`）解析并入 `AiTradeLaunchOpts`；只读预览区补一行「单仓兜底 OCO：SL <x>% / TP <y>%（交易所侧）」。`onConfirm` → `aiTradeApi.start` 增 `stopLossPct/takeProfitPct`。

### 2.6 兼容性与降级矩阵（全部安全默认）

| 增强 | 开关 | 默认 | 失败/不足时行为 |
|---|---|---|---|
| E1 体制上下文 | `ai_trade_regime_context` | **True** | 窗口<60 或计算异常 → `marketContext=None`，prompt 省略该段，决策照常 |
| E2 共识度（只读） | 无（always-on 纯聚合） | — | 空信号板 → total=0/agreement=0，prompt 如实呈现 |
| E2 共识门禁 | `ai_trade_min_consensus` | **0（关闭）** | 0 时完全等价 v2；>0 时仅把 open_* 降级 hold（更保守） |
| E4 单仓风控可配 | 入参 `stopLossPct/takeProfitPct` | 留空＝config 默认 | 越界即夹紧到 `ai_trade_max_sl_pct/tp_pct`，非法解析前端拦下 |

**关键不变式（R-P1-4，措辞已校准）**：`ai_trade_regime_context=True`（唯一默认变化）纯属只读输入增强；`ai_trade_min_consensus=0` 使 E2 门禁默认零行为；E4 留空即 v2 默认。因此在默认配置下：**① 确定性护栏与 v2 逐字一致**——门禁、pct/预算夹紧、与 live-auto 原子互斥、单笔名义上限、`LIVE_TRADING_ENABLED`/`AI_TRADE_ENABLED` 双闸、无既有持仓准入，全部不变；**② 决策 prompt 默认新增两行只读上下文（市场体制 + 共识度），故作为决策者的 LLM 看到的输入与 v2 不同，其输出乃至真单可能因此不同**——这是**预期的『综合研判增强』、非缺陷**（正是用户诉求的『让 AI 综合研判』）。若某场景要求 prompt 亦与 v2 逐字一致，可显式置 `ai_trade_regime_context=False`。面板多几处只读展示。把「更聪明/更保守/更透明」的开合交由运维/用户显式掌控。

---

## 三、File / module change plan（文件 / 模块变更计划）

> 约定：✚=新建，✎=修改。全部遵循 CLAUDE.md 阈值（单文件 ≤800 行、单函数 ≤50 行、≤5 形参、圈复杂度 ≤10、嵌套 ≤4、命名自解释、公开 API 写 docstring）。真实下单**一律经 `LiveExecutor`→`okx/trading.py`**，本 v3 不新增任何绕过路径。行号为当前工作树引用，评审节点须复核。

### 后端 `services/quant/app/`

| 文件 | 操作 | 一句话意图 |
|---|---|---|
| `ai_auto/market_context.py` | ✚ | `build_market_context(df, bar, settings)->dict|None`：薄封装 `engine.regime.classify_regime`，禁用/不足(<60)/异常一律 None（E1，安全降级）。 |
| `ai_auto/market_window.py` | ✎ | 增只读 `base_df(self)->DataFrame`（`candles_to_df(list(self.candles))`，用公有 `candles` 属性，供 E1 取基础窗口，与逐策略帧解耦，R-P2-5）。 |
| `ai_auto/signals.py` | ✎ | 增 `@dataclass Consensus` + 纯函数 `compute_consensus(board)->Consensus`（按持久 `stance` 聚合多/空/空仓票 + 净立场 + 一致度，E2）。 |
| `ai_auto/prompts.py` | ✎ | `build_decision_prompt` 增 `_format_market_context`/`_format_consensus` 两段（None/空即省略，E1/E2）；`build_ai_trade_system_prompt` 第 2 条纪律补「参考市场体制」半句。 |
| `ai_auto/session.py` | ✎ | `_build_decision_payload` 追加 `marketContext`(E1)/`consensus` wire(E2)；`_run_decision_round` 在 `decide` 后、`_execute_decision` 前加 `_apply_consensus_gate(decision, consensus_dataclass, degrade)`（E2 门禁，仅降级 open_*→hold），**并把门禁后 decision 用于 execute+`commit_round`（R-P1-3：journal/摘要记真实动作）**；`_emit_decision` **增 3 个 keyword 形参 `market_context/consensus/degrade_reason`**（R-P1-1），值取自本轮 payload 键与门禁 `degrade`。**不改并发模型/骨架（`_exec_lock`/`_deciding` 不变）。** |
| `ai_auto/manager.py` | ✎ | `AiTradeStartRequest` 增 `stop_loss_pct/take_profit_pct`；`_build_executor` 抽 `_resolve_trade_risk`（入参夹紧到 `ai_trade_max_sl_pct/tp_pct`，留空回 config 默认，E4）；**LiveExecutor 构造不变**。 |
| `api/ai_auto.py` | ✎ | `ai_trade_start` 把 `body.stopLossPct/takeProfitPct` 入 `AiTradeStartRequest`；响应回执追加 executor 生效 `stopLossPct/takeProfitPct`（E4）。 |
| `config.py` | ✎ | 新增 `ai_trade_regime_context: bool=True`（E1 开关）、`ai_trade_min_consensus: int=Field(default=0, ge=0)`（E2 门禁，0=关闭）、`ai_trade_max_sl_pct: float=0.20`、`ai_trade_max_tp_pct: float=0.50`（E4 夹紧上限）。**须真声明**（`Settings` `extra="ignore"` 否则静默丢弃）；桌面 `AI_TRADE_` 前缀已透传（v2 P1-6 已落，本 v3 无需再改 desktop）。 |
| `schemas.py` | ✎ | `AiTradeStartIn` 增 `stopLossPct/takeProfitPct: float|None = Field(default=None, gt=0)`；`AiTradeDecisionOut` 增可选 `marketContext/consensus: dict|None` + `degradeReason: str|None`（camelCase wire）。**注**：`decision` WS 事件由 `_emit_decision` 手工构 dict、不经 `AiTradeDecisionOut` 序列化，故此处补键仅为契约/文档镜像，真正上线以 `_emit_decision` 为准（R-P1-1）。 |

### 前端 `apps/web/`

| 文件 | 操作 | 一句话意图 |
|---|---|---|
| `lib/aiTradeTypes.ts` | ✎ | 增 `MarketContext`/`Consensus` interface；`AiTradeDecision` 增可选 `marketContext?/consensus?/degradeReason?`；`AiTradeStartReq` 增 `stopLossPct?/takeProfitPct?`。 |
| `lib/aiTrade.ts` | ✎ | 增 AI-trade 专属纯件 `describeRegime`（映射真实 regime 枚举，R-P1-2）/`describeConsensus`/`describeExecuted`（无副作用，易单测，E3）。 |
| `lib/liveAuto.ts` | ✎ | 增纯件 `parseOptionalPct`/`clampPct`（与既有 `parseOptionalUsd`/`parseOptionalLeverage` 同处，避免造孤岛，R-P2-2；供 `AiTradeLaunchModal` 复用，E4）。 |
| `lib/aiTradeApi.ts` | ✎ | `start` 入参透传 `stopLossPct/takeProfitPct`（E4，仅类型/透传，无逻辑）。 |
| `components/Agent/AiTradeLaunchModal.tsx` | ✎ | 金额区下方加「单仓止损%/止盈%」两字段 + 夹紧预览行；`AiTradeLaunchOpts` 与 `onConfirm` 增两字段（E4）。 |
| `components/Chart/AiTradePanel.tsx` | ✎ | 概览加体制徽标 + 距下次摘要；信号板表头加共识度计；`LastDecision`/`DecisionLog` 加执行结果态 + sizeFraction（E1/E2/E3，纯只读）。 |
| `lib/store.ts` | （不改） | 增补字段随 `decision`/`snapshot` 事件进既有瞬态 `aiTradeSession.lastDecision`/`aiTradeDecisions`，**不入 partialize、无需升版**（v25 保持）。 |
| `lib/useAiTradeStream.ts` | （不改） | `decision` 分支已透传整个 `data`（含新字段）至 `lastDecision`；无需改路由。 |

### 测试

| 文件 | 操作 | 一句话意图 |
|---|---|---|
| `services/quant/tests/test_ai_trade_market_context.py` | ✚ | `build_market_context`：合成明确上升行情 → `primary=="trend_up"`（钉真实枚举，R-P1-2）；<60 根 → None；`ai_trade_regime_context=False` → None；`classify_regime` 异常被吞为 None（不抛）。 |
| `services/quant/tests/test_ai_trade_consensus.py` | ✚ | `compute_consensus` 计票/净立场/一致度（全多、多空各半→net=flat、空信号板 total=0）；`_apply_consensus_gate`：`min_consensus=0` 恒放行；>阈值放行；<阈值把 open_* 降级 hold 且 close/hold 不受限。 |
| `services/quant/tests/test_ai_trade_decider.py` | ✎ | 断言决策 prompt（`build_decision_prompt`）在有 `marketContext`/`consensus` 时含对应两段、None/空时省略（E1/E2 契约）。 |
| `services/quant/tests/test_ai_trade_api.py` | ✎ | `start` 带越界 `stopLossPct`（如 0.9）→ executor 生效值被夹紧到 `ai_trade_max_sl_pct`；留空→回落 config 默认；回执含生效 pct（E4）。 |
| `services/quant/tests/test_ai_trade_session.py` | ✎ | 一轮决策的 `decision` 事件含 `marketContext/consensus/degradeReason`；`min_consensus` 生效时低共识开仓被降级为 hold 且 `executor.apply` **未被调用**，**且 `commit_round`/journal/摘要记录的是降级后的 hold（含 `low_consensus(k/n)`）、而非原开仓**（R-P1-3，E2/E3）。 |
| `apps/web/lib/aiTrade.test.ts` | ✎ | `describeRegime`（真实枚举→中文 + 缺省回原值，R-P1-2）/`describeConsensus`/`describeExecuted` 文案矩阵。 |
| `apps/web/lib/liveAuto.test.ts` | ✎ | `parseOptionalPct`/`clampPct` 边界（留空→null / 越界→夹紧 / 非法→error），与既有 `parseOptionalUsd` 测试同处（R-P2-2）。 |
| `apps/web/components/Agent/AiTradeLaunchModal.test.tsx` | ✎ | 单仓止损/止盈% 非法输入拦确认、越界预览夹紧、留空不阻断（`NODE_ENV=test`）。 |

---

## 四、Interface design（接口设计）

### 4.1 `POST /api/ai-trade/start`（E4 增量，向后兼容）
```jsonc
// AiTradeStartIn —— 在 v2 字段基础上新增两项（均可选，留空＝config 默认）
{
  "instId": "BTC-USDT-SWAP", "bar": "1H", "strategyIds": [12, 7, 23],
  "capitalUsd": null, "leverage": null,
  "takeProfitUsd": null, "stopLossUsd": null,       // v2：会话级总止盈/止损（USDT）
  "stopLossPct": 0.05,                              // ← E4 新增：单仓交易所侧 OCO 止损%（0<x≤ai_trade_max_sl_pct，越界夹紧）
  "takeProfitPct": 0.10,                            // ← E4 新增：单仓 OCO 止盈%（0<x≤ai_trade_max_tp_pct）
  "contextRefreshRounds": 10, "priming": true, "primingBars": 300
}
// → AiTradeStartOut（追加生效 pct 回执）
{ "sessionId": "aitrade_…", "runId": 512, "instId": "BTC-USDT-SWAP", "bar": "1H",
  "strategyIds": [12,7,23], "strategyNames": [...], "budgetUsd": 12.34, "leverage": 3,
  "contextRefreshRounds": 10, "stopLossPct": 0.05, "takeProfitPct": 0.10 }
```
门禁与 v2 一致（双闸/Agent 已配置/账户/标的/无持仓/互斥/策略数/轮数/预算夹紧）；`stopLossPct/takeProfitPct` 越界不报错、静默夹紧并在回执如实告知。

### 4.2 `WS /api/ws/ai-trade/{session_id}` · `decision` 事件（E1/E2/E3 增量，向后兼容）
```jsonc
{ "type": "decision", "data": {
    "ts": 1751003600, "round": 42,
    "action": "open_long", "sizeFraction": 0.4, "confidence": 0.7, "reason": "…",
    "signalBoard": [ { "strategyId": 12, "name": "…", "stance": "long", "latestSignal": "open_long", "strength": 0.8, "reason": "…" } ],
    "executed": { "ok": true, "skipped": false, "traded": true, "side": "long", "qty": 1, "positionAfter": "long" },
    "marketContext": { "primary": "trend_up", "volatility": "high_volatility", "event": "breakout", "confidence": 0.62, "labels": ["trend_up","high_volatility","breakout"] },   // ← E1（可为 null；取值∈真实 regime 枚举，R-P1-2）
    "consensus": { "longVotes": 3, "shortVotes": 1, "flatVotes": 1, "total": 5, "netStance": "long", "agreement": 0.6 },                                          // ← E2
    "degradeReason": null                                                                                                                                          // ← E3（"low_consensus"/"decision_timeout"/… 或 null）
  } }
```
`snapshot`/`status`/`summary`/`trade`/`position`/`equity`/`candle`/`error` 事件与 v2 完全一致，不变。旧前端忽略新增字段亦可正常工作（渐进增强）。

### 4.3 后端 → OKX / LLM（无新增上游端点）
E1 `classify_regime`、E2 `compute_consensus` 均**纯本地计算**，无 IO；E4 仅改构造期 `RiskParams`。下单/平仓/查仓一律仍经既有 `LiveExecutor`→`okx/trading.py`；LLM 决策/摘要仍经既有 `LLMClient.stream`。**零新增上游签名/端点。**

---

## 五、Data model（数据模型）

### 5.1 后端（`schemas.py` / `ai_auto/*`，camelCase wire）
```python
# schemas.py
class AiTradeStartIn(BaseModel):
    ...  # v2 全部字段不变
    stopLossPct: float | None = Field(default=None, gt=0)      # E4：单仓 OCO 止损%（服务端夹紧到 max）
    takeProfitPct: float | None = Field(default=None, gt=0)    # E4：单仓 OCO 止盈%

class AiTradeDecisionOut(BaseModel):
    ...  # v2 字段不变
    marketContext: dict | None = None      # E1
    consensus: dict | None = None          # E2
    degradeReason: str | None = None       # E3

# ai_auto/signals.py
@dataclass
class Consensus:
    long_votes: int; short_votes: int; flat_votes: int
    total: int; net_stance: str            # long|short|flat
    agreement: float                       # max(long,short)/total ∈ [0,1]

# ai_auto/market_context.py 返回形（dict，随 payload/事件流转，不落表）
# { primary:str, volatility:str, event:str, confidence:float, labels:list[str] }
```

### 5.2 前端（`lib/aiTradeTypes.ts`）
```ts
export interface MarketContext { primary: string; volatility: string; event: string; confidence: number; labels: string[]; }
export interface Consensus { longVotes: number; shortVotes: number; flatVotes: number; total: number; netStance: AiStance; agreement: number; }
export interface AiTradeDecision { /* …v2… */ marketContext?: MarketContext | null; consensus?: Consensus | null; degradeReason?: string | null; }
export interface AiTradeStartReq { /* …v2… */ stopLossPct?: number | null; takeProfitPct?: number | null; }
```

### 5.3 持久化（无新表、无迁移）
- **决策上下文（E1/E2）**：仅存在于内存决策轮与 WS 事件流，**不落表**（与 v2 决策日志同——审计已由 `trades.explain_json` 承载）。
- **会话级（E4）**：生效 `stopLossPct/takeProfitPct` 并入既有 `create_run(config={...})`（`manager._create_run`, `manager.py:240`）的 config JSON，随「运行历史」可复现；沿用 v2 `runs`/`live_order_audit`/`trades`，`mode="ai-trade"` 仍无需迁移（v2 P2-5 已核验）。
- **store**：增补字段随瞬态 `aiTradeSession.lastDecision`/`aiTradeDecisions` 流转，**不入 `partialize`**（`store.ts:799`），**版本保持 v25**（无 schema 变更、无需 migrate）。

---

## 六、错误码与降级（复用 v2，无新增稳定错误码）

本 v3 **不新增**对外错误码。E2 共识门禁、E1 数据不足均为**内部降级**，经既有通道呈现：
- 共识门禁降级 → 既有 `_emit_degraded`（`status.message`）+ `decision.degradeReason="low_consensus(k/n)"`；不下单、不报错、不崩会话。
- E1 数据不足/禁用 → `marketContext=null`，prompt 省略该段，静默。
- E4 pct 越界 → 服务端静默夹紧（回执如实告知），前端非法输入由 `parseOptionalPct` 拦在确认前。
既有 `AI_TRADE_DISABLED`/`AGENT_NOT_CONFIGURED`/`AI_TRADE_PREEXISTING_POSITION`/`AI_TRADE_SESSION_LIMIT`/… 全部沿用 v2 语义。

---

## 七、Testing & acceptance criteria（测试与验收）

### 7.1 后端单测（离线 pytest，CI 必跑；OKX/LLM 全桩，占位假凭证）
- `test_ai_trade_market_context.py`（E1）：合成明确上升趋势帧 → `primary == "trend_up"`（钉真实枚举，非 `uptrend`，R-P1-2）；<60 根 → `None`；`ai_trade_regime_context=False` → `None`；桩 `classify_regime` 抛异常 → 被吞为 `None`（决策轮不崩）。
- `test_ai_trade_consensus.py`（E2）：`compute_consensus` 全多→net=long/agreement=1；2 多 2 空→net=flat；空板→total=0/agreement=0；`_apply_consensus_gate`：`min_consensus=0` 恒放行；`open_long` 且 longVotes≥阈值放行、<阈值→hold 且 degradeReason 前缀 `low_consensus`；`close`/`hold` 任何阈值都不被拦（防「该止损时不平仓」）。
- `test_ai_trade_decider.py`（E1/E2 契约）：`build_decision_prompt` 在含 `marketContext`/`consensus` 时输出对应两段文本、为 None/空时**不输出**残段。
- `test_ai_trade_api.py`（E4）：`start` 传 `stopLossPct=0.9` → `sess.executor.risk.stop_loss_pct == ai_trade_max_sl_pct`（夹紧）；留空 → == `ai_trade_default_sl_pct`；回执 `stopLossPct/takeProfitPct` 为生效值。
- `test_ai_trade_session.py`（E2/E3）：单轮 `decision` 事件含 `marketContext/consensus/degradeReason` 三键；设 `min_consensus` 高于实际同向票时，`open_*` 决策被降级 → 断言 `executor.apply` **未被调用**、推 `status.message` 降级、`degradeReason` 命中 `low_consensus(k/n)`，**且 `commit_round` 收到的 decision 为 hold（journal/摘要据此记录真实动作，R-P1-3）**。

### 7.2 前端单测（vitest，`NODE_ENV=test`，CI 必跑）
- `aiTrade.test.ts`：`describeRegime`（真实枚举 `trend_up/high_volatility/breakout/…` 中文化 + 缺省回原值，R-P1-2）、`describeConsensus`（计数/净立场/一致度百分比）、`describeExecuted`（成交/跳过/降级/观望四态 + sizeFraction）。
- `liveAuto.test.ts`：`parseOptionalPct`/`clampPct`（留空→null、越界→夹紧、非法→error），与既有 `parseOptionalUsd` 测试同处（R-P2-2）。
- `AiTradeLaunchModal.test.tsx`：单仓止损/止盈% 非法输入使确认禁用；越界值预览显示夹紧后数值；留空不阻断确认；`onConfirm` 携 `stopLossPct/takeProfitPct`。

### 7.3 真实账户 opt-in 联调（手动/网络标记，不进 CI；与 v2 §7.3 铁律一致）
承接 v2 联调铁律（≤1% 资金、0.01 手 BTC、`leverage≤3`、`bar≥15m`、启动前确认无持仓）。**增量验收**：
1. 弹窗填「单仓止损 3% / 止盈 6%」→ 启动后在 OKX 端确认随单 OCO 触发价与之相符（E4 夹紧生效）。
2. 决策卡出现「市场体制」徽标与「共识度计」，且体制随行情切换而变化（E1/E3）。
3. 设 `AI_TRADE_MIN_CONSENSUS=3`、选 2 个策略 → 断言任何开仓都被降级为「已降级观望（low_consensus）」、**无真单**（E2 门禁）；改选 ≥3 个同向策略后可正常开仓。
4. 关 `ai_trade_regime_context` 复跑 → 决策卡无体制徽标、决策照常（E1 降级）。
5. 全程无残留持仓/挂单；默认配置（`min_consensus=0`、pct 留空）下行为与 v2 一致。

### 7.4 Definition of Done（本设计交付物）
- 本 `spec.md` 已建并见于 `git status`；含 Overview / Technical design / File change plan / Interface design / Data model / Testing & acceptance / Risks 全部小节，篇幅 ≥800 字。
- 文件/模块变更计划逐项可被下游工程师按行实现，无需再决策；四项增强边界清晰、可独立开关、默认配置与 v2 等价。

---

## 八、Risks & mitigations（风险与缓解）

| 风险 | 影响 | 缓解 |
|---|---|---|
| **增强放大真实资金风险**（第一等红线） | 资损 | 本 v3 **无任何一项新增下单面**：E1/E2 只读输入增强，E2 门禁只把 open_* 降级 hold（更保守），E4 只改构造期 `RiskParams`（夹紧 + `LiveExecutor` 零改动）。默认配置下 **确定性护栏与 v2 逐字一致**（门禁/夹紧/互斥/名义上限/双闸/无持仓准入不变）；决策 prompt 默认多两行只读上下文，LLM 决策可能因此不同——属预期的『综合研判增强』、非放大敞口（R-P1-4）。真单一律仍经 `LiveExecutor`→`okx/trading.py` 全护栏。 |
| **市场体制注入拖慢/拖崩决策轮** | 错过决策 / 崩溃 | `classify_regime` 纯本地、亚毫秒、无 IO；`build_market_context` 全程 try/except → None（数据不足/异常一律降级省略），决策轮从不因 E1 崩溃或超时。 |
| **共识门禁误伤（该开仓时不开 / 该平仓时被拦）** | 错失机会 / 该止损不止损 | 门禁**默认关闭**（0）；仅作用于 `open_long/open_short`，`close`/`hold` **永不受限**（保证任何时候都能平仓保护）；降级如实推 `degradeReason` 与 `status.message`，可人工审阅并调低阈值。 |
| **E4 单仓 pct 设过松/过紧** | 兜底失效 / 过早止损 | 服务端硬夹紧到 `ai_trade_max_sl_pct(0.20)`/`ai_trade_max_tp_pct(0.50)`；留空回落保守 config 默认；回执如实告知生效值；OCO 仍在交易所侧（bot 死也生效）。 |
| **wire 新增字段破坏旧前端 / 旧会话** | UI 报错 | 新字段全部**可选**（`| None`/`?`），旧前端忽略即可；`useAiTradeStream` 无需改路由（`decision` 分支整体透传）；store 无 schema 变更、不升版。 |
| **体制/共识术语误导 AI 或用户** | 决策质量 | 术语取自既有 `engine/regime` 生产标签（系统一致）；prompt 明确「作为背景参考、综合而非盲从」；前端 `describeRegime`/`describeConsensus` 统一中文化，缺省回原值不臆造。 |
| **改动触碰 v2 稳定链路引入回归** | 功能倒退 | 改动**集中在决策轮装配 + 事件补字段 + 构造期风控**，不动 `_exec_lock`/`_deciding` 并发模型、不动 `LiveExecutor`/`admission`/互斥、不动摘要 `_journal` 机制；全部增强逐项有单测；默认配置行为与 v2 等价，回归面小且可测。 |

---

## 九、Deliberately deferred（明确不做，附理由 · 供后续迭代）

| 后续增强 | 为何本 v3 不做 | 落地前置 |
|---|---|---|
| **多标的 AI 自动交易** | 会把真实资金敞口从「单标的单会话」扩为「N 标的并发」，敞口与失败面成倍放大；需重做预算跨标的切分、跨标的互斥/准入、多会话生命周期，工作量与风险远超一次自动化迭代的安全边界。 | 复用 `LiveAutoManager` 分组机制叠加 AI 决策；须先有多会话预算切分与聚合止盈止损设计 + 更强的联调门禁。 |
| **AI 决策工具自定义逐单 SL/TP** | 需在 `submit_trade_decision` schema 加 `stopLossPct/takeProfitPct` 并让 `LiveExecutor.apply` 支持**逐单** `RiskParams` 覆盖——要么改共享 `LiveExecutor`（污染 live-auto 路径），要么在会话内于 `_exec_lock` 下临时改 `executor.risk`（引入跨仓风控漂移风险）。风险收益比在真实资金上不划算。 | E4 已提供**会话级**可配 OCO 作为更安全的替代；逐单覆盖须先给 `LiveExecutor` 设计干净的 per-signal risk 通道 + 跨仓隔离测试。 |
| **体制感知的动态仓位/门禁** | 让 E1 体制直接调节 `sizeFraction` 上限或 `min_consensus`，属策略层择时，需回测验证，不宜在无回测护栏下上真实资金。 | 先在纸面/回测层验证体制→仓位映射的稳健性。 |

---

> 版本：**v4**（iteration 2 · Subtask #1 方案评审，在 Subtask #0 设计稿 v3 上就地修复 P0/P1）· 基线：iteration 1 已实现并提交的 v2（commit `4b13b69`，`docs/plans/ai-auto-trading/spec.md`）。核心：在不放大真实资金敞口的前提下，用四项内聚、有界、默认确定性护栏与 v2 一致的增强（市场体制上下文 / 策略共识度与可选门禁 / 决策可观测性 / 会话级单仓风控可配）把「AI 综合研判」的输入质量、安全下限、用户控制与运行透明度再抬升一档。全部改动 grounded 于 as-built 代码（见 §2.0 对齐表 + 顶部评审记录的逐条复核），逐项可独立开关、逐项有单测、无新表无迁移、store 不升版。

---

## 评审结论（Review Verdict）

**结论：有条件通过（Approved with conditions）。**

本设计稿经资深评审节点对全部 as-built 断言回对真实代码逐条复核，结论为：**架构方向正确、边界清晰、规模合理、真实资金安全姿态扎实**，四项增强（E1–E4）确可在现有栈落地且默认不放大敞口。评审发现 **0 个 P0、4 个 P1、5 个 P2**，**全部 P0/P1（及可一并修的 P2）已在本 v4 正文就地修复**，故不阻断进入 Subtask #2（代码开发）。之所以判「**有条件**」而非无条件通过：以下修复是**纸面修复**，其价值取决于代码开发节点是否逐字兑现——下列即 Subtask #2 落地时**必须满足的验收条件**（对应评审记录编号）：

1. **（R-P1-1）决策事件管线**：`_emit_decision` 必须新增 3 个 keyword 形参 `market_context/consensus/degrade_reason`，值取自 `_run_decision_round` 内的 `payload["marketContext"]`/`payload["consensus"]` 与门禁返回的 `degrade`；`_build_decision_payload` 须真正写入这两个键。补 `AiTradeDecisionOut` 三键仅为契约镜像，不得作为「字段已上线」的凭据。

2. **（R-P1-2）真实体制枚举**：`describeRegime` 与 `test_ai_trade_market_context` 必须映射/断言真实枚举（`primary∈{trend_up,trend_down,range}`、`volatility∈{high_volatility,low_volatility,normal}`、`event∈{crash,breakout,none}`），**严禁**沿用文稿早期示例里的 `uptrend`/`high_vol`。

3. **（R-P1-3）上下文纯净**：共识门禁把 `open_*→hold` 后，`_execute_decision`、`commit_round`、`_emit_decision` 一律用**门禁后**的 `decision`；须有单测断言降级时 journal/摘要记录的是 hold（含 `low_consensus(k/n)`）、且 `executor.apply` 未被调用。

4. **（R-P1-4）安全声明措辞**：代码/文档不得再出现「默认下单行为与 v2 完全一致」这类不可核验断言；一致性只对**确定性护栏**成立，决策 prompt 默认多两行只读上下文属预期增强。Subtask #3 复核时以此为准。

5. **（R-P2-1/2/3/4）一致性**：共识门禁按 `Consensus` dataclass 取值（`.long_votes`）；`parseOptionalPct`/`clampPct` 落 `lib/liveAuto.ts`（与 `parseOptionalUsd` 同处）；文档/实现区分新 `ai_trade_min_consensus` 与既有 `ai_trade_min_confidence`（独立叠加）、区分新 `degradeReason`(string) 与既有 `degraded`(boolean)。

以上条件均已在本 v4 正文写明实现方式，Subtask #2 只需「按行实现」即可满足，无需再做设计决策。**除上述条件外，设计其余部分予以通过。**

---

## 实施过程发现的方案缺陷（Issues Found During Implementation · Subtask #2 代码开发）

> 记录人：代码开发节点（implementation engineer）。开发时逐行落地 §三 change-plan，全部 E1–E4 已实现并单测通过（后端 46 例 + 前端 59 例绿；`ai_trade`/`regime`/`live_auto` 相关 115 例全绿；前端 `tsc --noEmit` 仅剩 1 个与本功能无关的既有 `WeightBatchArena.test.tsx` 错误）。落地中发现 §三 change-plan **一处指向偏差**（不影响设计正确性，已按最小修正落地），据 Subtask #2 约束在此如实记录：

### I-1（E4 单仓 pct 前端透传落点偏差 · 已按最小修正落地）

- **现象**：§三前端表把「start 入参透传 `stopLossPct/takeProfitPct`」记在 `lib/aiTradeApi.ts ✎`。但复核 as-built 代码：`aiTradeApi.start(req: AiTradeStartReq)`（`aiTradeApi.ts:35-39`）**已把整个 `req` 原样 JSON 序列化发送**，而 `AiTradeStartReq` 的两个 pct 字段本迭代已加在 `lib/aiTradeTypes.ts`（§三已列 ✎）。因此 **`aiTradeApi.ts` 无需任何代码改动**——传输层对请求类型是泛型的，字段随类型自然透传。
- **真正缺失的一环**：把 `AiTradeLaunchOpts.stopLossPct/takeProfitPct` 填进 start 请求对象的**取值装配**发生在 `components/Agent/AiTradeToggle.tsx` 的 `startM` mutation（`AiTradeToggle.tsx:69-78`，逐字段构造 `aiTradeApi.start({...})`，非 spread `opts`），而该文件**未列入 §三 change-plan**。若不在此补两字段，E4 弹窗填的 pct 到不了后端，端到端断链。
- **处置（最小修正，不扩范围）**：在 `AiTradeToggle.tsx` 的 start 装配处补 `stopLossPct: opts.stopLossPct, takeProfitPct: opts.takeProfitPct` 两行（纯透传，服务端夹紧）；`aiTradeApi.ts` 保持不动（其 ✎ 意图由 `aiTradeTypes.ts` 的类型扩展承载）。除此之外未新增/改动任何 §三 未列文件。E4 端到端链路（弹窗解析 → opts → toggle 装配 → start → 服务端 `_resolve_trade_risk` 夹紧 → 回执生效 pct）经 `test_ai_trade_api.py` 与 `AiTradeLaunchModal.test.tsx`/`AiTradeToggle.test.tsx` 覆盖。
