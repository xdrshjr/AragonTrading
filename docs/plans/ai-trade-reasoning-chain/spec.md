# AI 自动交易 · 思维链研判增强（Reasoning Chain）开发方案

- **Feature key**: `ai-trade-reasoning-chain`
- **文档版本**: **v2**（经 Subtask #1 方案评审节点逐节核验真实代码后就地修复 P0/P1，见下方「评审记录」与文末「评审结论」。v1 由 Subtask #0 方案设计节点产出。本链路**只**产出/修订本 `spec.md`，不写实现代码、不改任何源文件、不 `git commit`。下游 #2 开发 → #3 提交依此 v2 推进。）
- **作者**: 方案设计节点（solution architect）
- **关联红线（真实资金安全，一等约束）**: 本功能是对**已上线的第三类自动交易「AI 自动交易」**（`docs/plans/ai-auto-trading/spec.md` v2 已实现并提交，commit `4b13b69`；其后 enhancements v4 / agent-trace / backtest / multi-symbol 增量均已在工作树落地）的**决策质量增强**，**不新增任何下单面、不改任何资金护栏**。一切真单仍只经 `LiveExecutor` → `okx/trading.py`（继承 `LIVE_TRADING_ENABLED` 守门、单笔名义上限、净仓反向拦截、唯一 clOrdId、`live_order_audit` 留痕、脱敏），叠加既有 `AI_TRADE_ENABLED` 总闸、live-auto 互斥独占、预算/名义双上限、会话级止盈止损、无既有持仓门禁、**LLM 决策失败/超时/非法一律回落 `hold`**。本方案**只改「喂给决策模型的输入」与「系统 prompt 的研判纪律」，一切确定性安全守卫（`_map_decision` / `apply_consensus_gate` / `decision_to_signals` / 前置门禁 / 执行器构造）保持逐字节不变**。

---

## 评审记录（Review Notes · v2，方案评审节点）

> 评审维度：可行性（Feasibility）/ 完整性（Completeness）/ 一致性（Consistency）/ 合理规模（Right-sizing）。已逐节对照真实代码核验：`ai_auto/decider.py`、`prompts.py`、`session.py`、`market_context.py`、`market_window.py`、`backtest.py`、`engine/indicators.py`、`config.py`、`schemas.py`，以及前端 `aiTrade.ts` / `aiTradeTypes.ts` / `AiTradeTraceRound.tsx`。核验结论：方案骨架**可行且与既有约定高度一致**，`build_market_digest` 的所有依赖（`ema/sma/rsi/bollinger/atr` 签名、`base_df()`、`build_market_context` 降级范式、config `AI_TRADE_` 字段名即 env 名、前端 `describeRegime` 模板、`SignalContext` 渲染位）均已核实存在。

**未发现 P0。** 本方案冻结全部确定性安全守卫（`_map_decision` / `apply_consensus_gate` / `decision_to_signals` / 前置门禁 / `LiveExecutor` 构造），改动限于「喂给 LLM 的 prompt 输入」与「只读研判留痕」；最坏退化是**更多 hold**，物理上无法绕过资金护栏或破坏构建。故无红线级/构建阻断问题。

**P1（已在正文就地修复，标注 v2）：**

| # | 维度 | 严重度 | 问题 | 处置 |
|---|---|---|---|---|
| RC-1 | 完整性/正确性 | **P1** | `_last_analysis` 仅在 `_stream_decision` **末尾**赋值；本轮 LLM **超时 / 异常 / 被 `wait_for` 取消**回落 hold 时，它保留**上一轮**文本，journal 与 decision 事件会把上一轮研判错贴到本轮 hold（串轮污染）。 | 已修（§2.5）：`decide()` 入口先 `self._last_analysis=""` 清零，降级轮 analysis 恒为空。 |
| RC-2 | 完整性/稳健性 | **P1** | journal 每条附**完整 ≤4000 字 CoT**，`build_summary_prompt` 对整册 `json.dumps`；默认 `context_refresh_rounds=10` → 单次摘要输入 ~40KB（万级 token），拖慢/挤占摘要 LLM，甚至触发其超时回落。 | 已修（§2.4 R5 / §2.5 / §2.8）：journal 只存**有界摘录**（`_ANALYSIS_EXCERPT_CHARS≈400`）；「采纳/推翻」后验靠强化后的 summary prompt + 既有 `decision.reason(≤500)`，不灌全文。 |
| RC-3 | 完整性/稳健性 | **P1** | decision 事件若带完整 CoT，则 `_decisions`(maxlen=50) 与每次 WS 重连 `snapshot()` 膨胀至 ~200KB，且与已承载全量 CoT 的 `_traces`(≤20) 冗余重复。 | 已修（§2.5 / §四）：decision 事件 `analysis` 只带同一**有界摘录**（供左栏决策卡首段）；完整思维链仅经 `agent_trace` 流与 `_traces` 快照承载；`marketDigest`（小、~10 数值）照带。 |
| RC-4 | 一致性/可行性 | **P1** | 8 步 CoT 使系统/决策 prompt 显著拉长；若「必须以一次 `submit_trade_decision` 收尾」被埋在中部，长输出下模型更易只回研判文字（虽有 `no_tool_call→hold` 兜底，但抬高 hold 率、削弱功能本身）。 | 已修（§2.4）：该硬约束置于系统 prompt 纪律**收尾**与决策 prompt **末句**双处（近因效应），RV-4 措辞逐字保留。 |

**P2（改进建议，留给 #2 开发酌情落实，不阻断通过）：**

| # | 维度 | 问题 | 建议 |
|---|---|---|---|
| RC-5 | 合理规模/一致性 | R-1「因子」scope：本方案取通用技术因子读数，不接 `engine/factors` 重系统。 | 保留 §2.3/§七 R-1 的 scope 决策；已在**评审结论**列为需**上游确认**的条件。 |
| RC-6 | 正确性 | `_MIN_BARS=30` 对 MACD（慢线 26 + signal 9）收敛偏紧。 | §2.3 已将 `_MIN_BARS` 提到 **35**；或对未成熟 MACD 柱返回 None-label。 |
| RC-7 | 完整性/测试 | §五「因果性」单测「改 `df[k:]` 不影响 `build(df[:k])`」因 `.iloc[:k]` 已丢弃后缀而近乎恒真、检验力弱。 | 主因果保证改由「与 `engine/indicators` 直算对拍」（spec 已含）承担，§五已补注；可另加 session/backtest 切片位点用例。 |
| RC-8 | 合理规模 | `_price_action` 计算 ~10 个字段，单函数或逾 50 行 / 圈复杂度 10（CLAUDE.md 阈值）。 | §2.3 已注明可再拆 `_ema_structure` / `_range_structure` 子助手。 |
| RC-9 | 一致性 | 文档镜像 schema：除 `AiTradeDecisionOut` 外，`AiTradeRoundTraceOut` 也承载 `marketDigest`（round_start）；`test_ai_trade_prompts.py` 当前不存在（应标「新增」）。 | §3 file-plan 已更正标注并补 `AiTradeRoundTraceOut.marketDigest`（可选、文档镜像）。 |
| RC-10 | 一致性 | 回测 `_emit_decision`（backtest.py:370）已带 marketContext/consensus，但 §2.6 不带 marketDigest，与实盘 decision 事件不对称。 | §2.6 允许「仅实盘」降级；已建议顺带在回测 decision 事件补 `marketDigest`（一行，对称回测分析前端）。 |

---

## 一、Overview（概述）

当前「AI 自动交易」的决策器（`ai_auto/decider.py` + `ai_auto/prompts.py`）虽然已经把「AI Agent 作为决策者」这一骨架搭起来了——每个决策轮把多策略信号板 + 真实持仓 + 会话盈亏 + 可选市场体制/共识喂给 LLM，LLM 产出一个受约束的 `submit_trade_decision`——但它的**研判深度不足**，与用户需求存在明确落差。具体地：**(1)** 现行系统 prompt 只要求「每轮先用 2–4 句话简要说明综合研判分析」（`prompts.py:43-45`），这是**浅层一次性说明**，不是需求要的**一步一步、思维链（chain-of-thought）式**的分步推理；**(2)** 现行决策输入把策略信号板当作近乎「准确定性」的主输入，模型很容易退化为「多数策略看多我就开多」的信号跟随者，而需求明确指出**策略与因子信号只是决策的基础、参考与触发线索，它们可能对、也可能错，Agent 必须自己独立研判、有权推翻任何单一信号**；**(3)** 决策输入**缺失需求点名的关键证据维度**——原始**价格走势**、**成交量**、以及**各个（技术）因子读数**都没有进入 prompt，模型无从「综合价格走势、成交量、各策略、各因子的情况一步步思考」。

因此本方案在**不动任何资金执行链路**的前提下，围绕「决策的输入证据」和「决策的研判方式」两处做一次**有界、以增量为主、确定性行为零回归**的增强，把 AI 从「信号执行器」升级为「信号批判者 + 独立研判者」：

1. **补齐证据维度**——新增一个纯计算模块，从会话已有的行情窗口 `MarketWindow.base_df()` 因果地算出三段紧凑证据：**价格走势摘要**（趋势方向/动量/均线结构/摆动结构/区间位置）、**成交量摘要**（量能水平/量比/放缩趋势/异常放量）、**技术因子读数**（RSI / MACD 柱 / 布林 %B / ATR% / 动量 ROC 等一组固定、经典、可复用 `engine/indicators.py` 的读数），注入决策 prompt。这些正是需求点名的「价格走势、成交量、各个因子」。
2. **重写研判纪律**——把系统 prompt 从「2–4 句说明 + 调工具」升级为**显式分步思维链**：先声明「策略/因子信号是参考与触发线索、非确定性指令、可能错误、你可推翻」，再要求模型**逐步走过**价格走势 → 成交量 → 市场体制 → 因子读数 → **逐策略批判研判**（每个策略/因子信号在当前语境下是否可信：采纳 / 存疑 / 推翻，并给出理由）→ 冲突与权衡 → 风险与持仓 → 结论，**最后必须且只能调用一次决策工具收尾**（保留既有硬约束，绝不允许只回文字）。
3. **让研判可观测、可沉淀**——决策器把本轮完整研判文本（思维链）捕获为 `last_analysis`：**完整文本经既有 `agent_trace` 流式与 `_traces` 快照**下发前端右侧「AI 助手」执行轨迹面板（该面板已存在，会自动展示更完整的思维链）；其**有界摘录**（`_ANALYSIS_EXCERPT_CHARS≈400`）写入 `_journal` 与 decision 事件（RC-2/RC-3：避免摘要素材与 snapshot 膨胀），使**每 N 轮的滚动摘要能反映「哪些信号被采纳/推翻、事后对错」**，把「信号是参考、Agent 自主研判」的过程沉淀为长期记忆。

工程上这是一个**小而聚焦**的增强包：后端新增 1 个纯模块 `ai_auto/market_digest.py` + 少量 `config.py` 字段，改写 `prompts.py` 的 2 个 prompt 构造函数并在 `decider.py` / `session.py` / `backtest.py` 做**增量接线**；前端新增 3 个 `describe*` 纯函数与若干只读类型字段、在既有轨迹卡里多渲染几行摘要。**所有确定性安全守卫、执行器、门禁、互斥、上下文压缩机制保持不变**；唯一「行为变化」是喂给 LLM 的 prompt 内容变丰富、研判纪律变严谨——这正是本需求的目标，且默认开启（`ai_trade_market_digest=True`）。

---

## 二、Technical design（技术设计）

### 2.1 现状与差距（grounded，逐一核对真实代码）

| 环节 | 现状（真实代码） | 差距 / 本方案动作 |
|---|---|---|
| 决策输入装配 | `session.py:510 _build_decision_payload` → `{round, barTs, lastPx, position, sessionPnlUsd, budgetUsd, tp/sl, marketContext(E1), signalBoard}`；`_run_decision_round` 再补 `consensus`（`session.py:331`）。**无价格走势/成交量/因子读数**。 | 新增 `marketDigest`（priceAction+volume+factors）注入 payload（§2.3）。 |
| 系统 prompt | `prompts.py:35 build_ai_trade_system_prompt`：纪律 1「先 2–4 句分析再调工具」；纪律 2「综合而非盲从」。**研判浅、信号权重过高、无分步 CoT、未显式声明信号仅供参考**。 | 重写为显式分步思维链 + 声明信号为参考/触发（§2.4）。 |
| 决策工具 | `prompts.py:14-32`：`submit_trade_decision{action, sizeFraction, confidence, reason}`，`required=[action,reason]`。 | **schema 不改**（避免动确定性映射）；仅更新 `DECISION_TOOL_DESCRIPTION` 措辞（§2.4）。 |
| 研判文本流 | `decider._stream_decision`：`text_delta`→coalescer「analysis」，`thinking_delta`→「thinking」；仅经 `emit` 扇出轨迹，**不留存**、不入 journal/decision。 | 捕获 `self._last_analysis`（无论 emit 与否；`decide()` 入口清零防串轮，RC-1）；**有界摘录**入 journal + decision 事件、**全文只走 trace**（RC-2/RC-3，§2.5）。 |
| 安全守卫 | `_map_decision`/`_hold`/`_clamp01`/`apply_consensus_gate`/`decision_to_signals`/前置门禁/`LiveExecutor` 构造。 | **全部逐字节不变**（零回归门禁，§六）。 |

### 2.2 端到端数据流（改动点以 ★ 标注）

```text
每根确认 K 线（session._on_confirmed:299）
  1) window.append_confirmed → build_strategy_frames → i=cur_index → ref_px/ts
  2) evaluate_signal_board(specs, shadows, frames, i) → board（影子立场，不变）
  3) _run_decision_round(board, i, ref_px, ts)：
       payload = _build_decision_payload(...)                       # 不变字段
       ★ payload["marketDigest"] = build_market_digest(window.base_df(), bar, settings)  # 新增（R1）
       consensus = compute_consensus(board); payload["consensus"] = _consensus_wire(...)  # 不变
       _begin_round_trace(...)  ★ round_start 事件带 marketDigest（R4）
       decision, degrade = decider.decide(payload, emit=_make_trace_emit(...))
            └ decide() 入口 ★ self._last_analysis="" 清零（RC-1）；_stream_decision ★ 累积 text 到 self._last_analysis（R3，末尾赋值）；safety 映射不变
       decision, degrade = _apply_consensus_gate(...)               # 不变（门禁）
       executed = _execute_decision(...)                            # 不变（LiveExecutor.apply）
       ★ excerpt = decider.last_analysis[:_ANALYSIS_EXCERPT_CHARS]  # RC-2/RC-3：有界摘录（全文只走 trace）
       summary = decider.commit_round(payload, decision, executed, analysis=excerpt)  # ★ 摘录入 journal
       _emit_tool_call_trace / _emit_result_trace                    # 不变（trace 已含全文 analysis）
       _emit_decision(..., ★ analysis=excerpt, ★ market_digest=payload["marketDigest"])
```

一句话：**只在 payload 里加一段证据、在 prompt 里换一套研判纪律、把研判文本留痕**；`decide()` 的返回签名 `(TradeDecision, str|None)` **不变**（回测 `backtest.py` 的位置调用零回归），一切执行/门禁保持原状。

### 2.3 R1 · 市场证据摘要（新纯模块 `ai_auto/market_digest.py`）

**职责**：从当前基础周期窗口 `df`（`candles_to_df` 产物，列 `ts/o/h/l/c/vol`，升序，末行=当前确认根）**因果地**（只用 ≤ 当前根的数据）算出三段紧凑证据，压成可注入 prompt 的 dict。**纯函数、无副作用、无网络、无 broker**，复用 `engine/indicators.py` 的 `ema/sma/rsi/bollinger/atr`（自实现、已测、规避 pandas-ta 兼容问题）。**全程安全降级**：任一段数据不足或计算异常 → 该段返回 `None`（prompt 省略该段），整个函数在 `ai_trade_market_digest=False` 或 `len(df) < _MIN_BARS` 时返回 `None`（与 `market_context.build_market_context` 同姿态）。

**顶层函数**：

```python
def build_market_digest(df: pd.DataFrame, bar: str, settings: Settings) -> dict | None:
    """价格走势 + 成交量 + 技术因子的紧凑证据摘要；禁用/数据不足/异常一律 None（安全降级）。"""
    if not settings.ai_trade_market_digest:
        return None
    if df is None or len(df) < _MIN_BARS:          # _MIN_BARS=35（够 RSI14/BB20/EMA21 及 MACD 慢线26+signal9 收敛，RC-6）
        return None
    try:
        return {
            "priceAction": _price_action(df, settings),
            "volume": _volume(df, settings),
            "factors": _factors(df),
        }
    except Exception:  # noqa: BLE001 —— 任一子段异常不崩决策轮：整段省略（绝不假数据）
        return None
```

**三个子构造器（各 ≤50 行，返回 dict 或 None）**：

- `_price_action(df, settings) -> dict`（字段较多，落地时若逼近 50 行 / 圈复杂度 10，拆 `_ema_structure` / `_range_structure` 子助手，RC-8）：
  - `changePct`：`(c[-1]/c[-1-N] - 1)` × 100，N = `ai_trade_digest_trend_window`（默认 20），保留 2 位。
  - `trend`：由 `changePct` + EMA 结构定标签 ∈ `{"up","down","flat"}`（阈值：`|changePct|<0.3%` 记 flat）。
  - `emaFast/emaSlow`：`ema(c,9).iloc[-1]` / `ema(c,21).iloc[-1]`；`emaRelation` ∈ `{"fast_above","fast_below"}`；`emaSlopePct`：`(emaFast - ema(c,9).iloc[-4]) / emaFast` × 100（近 3 根斜率）。
  - `rangePos`：`(c[-1] - low_N) / (high_N - low_N)`（N 根内位置，∈[0,1]，分母 0 时 None）；`swingHigh`/`swingLow` = 近 N 根 `h.max()`/`l.min()`。
  - `consecUp`/`consecDown`：末端连续阳/阴根数。
  - `lastBar`：`{o,h,l,c}` 保留必要位数 + `bodyPct=|c-o|/o×100`。
- `_volume(df, settings) -> dict | None`：
  - `last`：`vol[-1]`；`sma`：`sma(vol, M).iloc[-1]`，M = `ai_trade_digest_vol_window`（默认 20）；`ratio`：`last/sma`（sma≤0→None）。
  - `zscore`：`(last - mean_M) / std_M`（std 0→None）。
  - `trend` ∈ `{"rising","falling","flat"}`：近 5 根 vol 线性方向（`vol[-1]` vs `vol[-5]` 的相对变化，阈值 ±15%）。
  - `spike`：`bool(ratio >= _VOL_SPIKE_RATIO)`（默认 2.0）。
- `_factors(df) -> dict`：一组固定、经典技术因子读数，每项 `{value, label}`（label 为定性中文/枚举，供 prompt 直读，前端另做中文化）：
  - `rsi14`：`rsi(c,14).iloc[-1]`；label ∈ `{"overbought"(>70),"oversold"(<30),"neutral"}`。
  - `macdHist`：`ema(c,12) - ema(c,26)` 减 `ema(diff,9)` 的末值；label ∈ `{"bullish"(>0),"bearish"(<0)}` + 是否放大（`|hist[-1]|>|hist[-2]|`）。
  - `bollPctB`：`(c[-1]-lower)/(upper-lower)`（`bollinger(c,20,2)`；分母 0→None）；label ∈ `{"upper_break"(>1),"lower_break"(<0),"upper_half"(≥0.5),"lower_half"}`。
  - `atrPct`：`atr(df,14).iloc[-1] / c[-1]` × 100；label ∈ `{"high"(>阈),"low"(<阈),"normal"}`（阈值相对量纲，取 1.5% / 0.5%，可后续调）。
  - `rocPct`：`(c[-1]/c[-1-10]-1)×100`（10 根动量）；label 由符号定 `{"positive","negative"}`。

  > **与「因子层」子系统的边界（重要 scope 决策，见 §七 R-1）**：此处「因子」取**通用技术因子**（RSI/MACD/BB/ATR/ROC 等经典读数），**不接入**重型 `engine/factors/*`（因子挖掘/评分/权重模型）子系统——后者依赖标的样本构建、正规化契约与训练产物，代价与耦合远超本增强所需。本模块给 AI 的是「独立于所选策略的、可解释的原始市场因子证据」，正合需求「综合各因子情况一步步研判」之意；与「因子层」子系统的深度对接列为后续可选增强。

**为何放在 session 层注入而非 decider**：与既有 `marketContext`（E1）一致——decider 是纯粹「拿 payload 出决策」的无状态推理器，行情证据由持有 `MarketWindow` 的 session 装配。回测同理由 runner 装配（§2.6）。

### 2.4 R2 · 思维链研判纪律（重写 `prompts.py`）

**`build_ai_trade_system_prompt(inst_id, bar, budget_usd, leverage)`**（重写正文，签名不变）。关键改写点：

1. **开宗明义：信号是参考，不是指令**。新增一段角色声明：
   > 「交给你的**多个策略信号、因子读数、市场体制、共识度都只是决策的参考与触发线索，不是必须执行的确定性指令**。它们可能正确，也可能错误、滞后或相互矛盾。你的职责不是复述或跟随它们，而是**像一名资深交易员那样，独立地、一步一步地综合研判所有证据后自己下判断**——你有权采纳、存疑或**推翻**任何单一信号。」
2. **纪律 1 改为「显式分步思维链」**（替换原「2–4 句」）：
   > 「**每轮必须先输出一段分步骤的研判（思维链），依次思考：① 价格走势（方向、动量、均线结构、区间位置）；② 成交量（量能、量比、放缩、异常放量）；③ 市场体制（趋势/震荡/高低波动/事件）；④ 技术因子读数（RSI/MACD/布林/ATR/动量各说明什么）；⑤ 逐一批判各策略信号——每个策略此刻的立场与本轮意图在当前语境下**是否可信**，你采纳、存疑还是推翻，为什么；⑥ 信号冲突与权衡；⑦ 当前真实持仓、会话盈亏与风险约束；⑧ 结论。**随后必须且只能调用一次** `submit_trade_decision` 提交决策。**绝不允许只输出研判文字而不调用决策工具**；研判供人类观察，真正生效的只有工具调用。」
3. 保留并强化既有纪律：**只用输入中的真实数据、不臆造**；**不确定时 hold**；**风险优先**（持仓且多数证据转反向 → 优先 close 保护）；**开仓保守**（`sizeFraction` 默认 ≤0.5）；`action` 取值域与 close/hold 忽略 sizeFraction 的约束。
4. **保留硬约束（RV-4，不得回退）**：「分析之后必须以工具调用收尾、不得只回文字」——这是既有 agent-trace 功能的放行条件，且是安全回落之外的第二重保障，**必须保留**。
5. **收尾指令置末，利用近因效应（RC-4，评审新增）**：8 步 CoT 让 prompt 显著变长，为降低「模型只回研判文字、忘记调工具」的概率（虽有 `no_tool_call → hold` 兜底，但会抬高 hold 率、削弱功能），**「必须且只能调用一次 `submit_trade_decision` 收尾」这句硬约束必须同时作为系统 prompt 决策纪律的最后一条、与决策 prompt（`build_decision_prompt`）的末句**——放在两处输入的最末位（近因位），措辞强度与 RV-4 逐字一致。禁止把它埋在中部被 CoT 步骤淹没。

**`build_decision_prompt(payload)`**（`prompts.py:93`）**增量**：在既有 `marketContext` / `consensus` 段之后、`signalBoard` 之前，插入三段可选证据（`_format_price_action` / `_format_volume` / `_format_factors`，各自 `mc is None → ""` 时整段省略，与 `_format_market_context` 同模式），并把「策略信号板」小标题改为**明确其参考属性**：`"策略信号板（仅供参考的触发线索，请逐条批判研判，可推翻）："`。末句（**必须为整段 prompt 的最后一行**，RC-4 近因位）由「请综合上述信息，调用一次…」改为「**请先按上述纪律逐步研判（思维链），再必须且只能调用一次 {DECISION_TOOL_NAME} 提交本轮决策——不得只输出研判文字。**」

**`DECISION_TOOL_DESCRIPTION`**（`prompts.py:28`）：措辞更新为「……综合并**独立批判研判**所有策略信号、因子读数与真实持仓后（信号仅供参考、可推翻），选择 … 之一……」。schema 本体不改。

**`build_summary_prompt` / `build_summary_system_prompt`**（R5，`prompts.py:125-140`）：摘要 head 增补「**记录本阶段哪些策略/因子信号被采纳或被推翻、事后被行情验证为对或错**，作为经验教训」，让滚动长期记忆沉淀「信号是参考、Agent 自主研判」的后验。**摘要素材来源（RC-2 修正）**：滚动摘要的「采纳/推翻」后验主要取自每轮 `decision.reason`（≤500 字，模型自陈的核心研判结论，journal 早已含）与 journal 新增的 **analysis 有界摘录**（`_ANALYSIS_EXCERPT_CHARS≈400`，§2.5）；**绝不把完整 ≤4000 字 CoT 灌入 journal / 摘要输入**——否则默认 10 轮刷新时 `json.dumps(journal)` 将膨胀至 ~40KB / 万级 token，拖慢并挤占摘要 LLM。完整思维链只经 `agent_trace` 流与 `_traces` 承载，不进摘要素材。

### 2.5 R3 · 研判文本留痕（`decider.py` + `session.py` 增量）

- `AiTradeDecider.__init__`：新增 `self._last_analysis: str = ""`。
- **`decide()` 入口先清零（RC-1，评审新增，安全关键）**：`async def decide(...)` 的**第一行**执行 `self._last_analysis = ""`。理由：`_last_analysis` 只在 `_stream_decision` 末尾赋值，一旦本轮 LLM **超时 / 异常 / 被 `asyncio.wait_for` 取消**（`decide` 回落 `hold`），若不清零则 `_last_analysis` 会保留**上一轮**文本，导致 journal 与 decision 事件把上一轮研判错贴到本轮 hold（串轮污染）。入口清零后，任何降级轮的 `last_analysis` 恒为空串，语义正确。
- `_stream_decision`：在累积 `text_delta` 时，**除**喂 coalescer 外**同时**追加进一个本地 `analysis_parts: list[str]`；函数正常结束时把 `self._last_analysis = "".join(analysis_parts).strip()`（硬上限 `_ANALYSIS_MAX_CHARS=4000`，超出截断）。**关键**：此累积**与 `emit` 无关**（回测 `emit=None` 也留痕），且**不影响**取工具入参的既有逻辑（安全契约逐字不变）。
- 新增只读属性 `last_analysis -> str`（供 session 读取，返回本轮完整累积文本，供派生摘录）。
- **有界摘录是唯一「落盘/落事件」形态（RC-2 / RC-3，评审新增）**：完整 `last_analysis`（≤4000 字）**只**经 `agent_trace` 流式与 `_traces`（既有路径，coalescer 已限 4000、环形 ≤20）承载给 UI；凡进入 **journal（摘要素材）** 与 **decision 事件（`_decisions` 环形 ≤50 + WS snapshot 重放）** 的，一律取**有界摘录** `excerpt = last_analysis[:_ANALYSIS_EXCERPT_CHARS]`（模块级常量 `_ANALYSIS_EXCERPT_CHARS = 400`）。这样 journal（默认 10 轮）与每次重连 snapshot 都不会被 10×4000 / 50×4000 的全文撑爆（详见 RC-2/RC-3 处置）。
- `commit_round(payload, decision, executed, *, analysis: str | None = None)`：新增 keyword-only 参 `analysis`（默认 `None` → 回测/单测既有调用零回归）；journal 条目新增 `"analysis": analysis`——**由调用方传入已截断的 excerpt**（见下）。
- `session._run_decision_round`：`decide()` 后取 `full = self.decider.last_analysis`，派生 `excerpt = full[:_ANALYSIS_EXCERPT_CHARS]`；`commit_round(payload, decision, executed, analysis=excerpt)`；`_emit_decision(..., analysis=excerpt, market_digest=payload.get("marketDigest"))`。（`_ANALYSIS_EXCERPT_CHARS` 常量置于 `session.py` 或 `trace.py` 模块级，与既有阈值常量同风格。）
- `session._emit_decision`：新增 keyword-only 参 `analysis: str | None = None`、`market_digest: dict | None = None`，写入 `data["analysis"]`（**excerpt**，供左栏决策卡首段）/ `data["marketDigest"]`（旧前端忽略未知字段，向前兼容）。
- `trace.new_round_trace`：`payload.get("marketDigest")` 写入 `marketDigest` 键；`_begin_round_trace` 的 `round_start` 事件带 `marketDigest=trace["marketDigest"]`。（完整思维链仍走既有 `_make_trace_emit` 的 analysis phase 累积到 `trace["analysis"]`，不受本节摘录影响。）

### 2.6 回测一致性（`ai_auto/backtest.py` 增量，可选但建议）

回测 runner 逐根决策时同样应享受更好的研判（需求指向决策质量，回测是其离线验证）。回测 `AiBacktestRunner._build_payload`（`backtest.py:338`）**已**在同一位置调 `build_market_context(main_df.iloc[:i + 1], self.bar, self.settings)`，故本增量与之**逐字同构**：在其后**增量**加 `"marketDigest": build_market_digest(main_df.iloc[:i + 1], self.bar, self.settings)`（严格因果切片 `iloc[:i+1]`，末行=当前决策根，与既有 marketContext 切片完全一致，不引入未来函数）。回测**不接线轨迹/前端**（agent-trace 的 backtest 分支本就 N1 deferred），故 R3/R4 对回测无涉；`decide()` / `commit_round` 位置调用不变（`commit_round` 新增 kw `analysis` 默认 None → 回测零回归）。
- **对称性（RC-10，评审新增，可选）**：回测 `_emit_decision`（`backtest.py:370`）已在 decision 事件带 `marketContext` / `consensus`；建议顺带补一行 `"marketDigest": payload.get("marketDigest")`，使回测 decision 事件与实盘对称，便于回测分析前端未来直接消费。此为一行增量、无行为风险。
- 本节整体为**加分项**：若 #2 开发认为超出本轮最小闭环，可降级为「仅实盘会话」并在「## 实施过程发现的方案缺陷」段记录（实盘主目标不受影响）。

### 2.7 R4 · 前端观测（只读、增量、向前兼容）

右侧「AI 助手」执行轨迹面板（`AiTradeAgentTrace.tsx` + `AiTradeTraceRound.tsx`）**已存在**并展示 `trace.analysis`——思维链变长后**自动**呈现更完整推理，无需改渲染主体。增量只做两件事：

1. **类型扩展**（`aiTradeTypes.ts`）：新增 `PriceAction` / `VolumeContext` / `FactorReadings`（或合并为 `MarketDigest`）接口；在 `AiTradeDecision`、`AiTradeTraceEvent`、`AiTradeRoundTrace` 上增可选 `marketDigest?: MarketDigest | null` 与 `AiTradeDecision.analysis?: string | null`。
2. **中文化 + 渲染**（`aiTrade.ts` + `AiTradeTraceRound.tsx`）：新增 `describePriceAction` / `describeVolume` / `describeFactors` 三个纯函数（`mc is null → ""`，缺省回原值不臆造，镜像 `describeRegime`）；在 `AiTradeTraceRound.tsx` 的 `SignalContext` 折叠块内，`regime`/`consensus` 同排下方多渲染这三行摘要（若非空）。左侧 `AiTradePanel` 决策卡可选展示 `lastDecision.analysis` 首段（可延后）。

### 2.8 配置项（`config.py` 新增，`AI_TRADE_` 前缀，桌面透传已覆盖）

| 字段 | 默认 | 含义 |
|---|---|---|
| `ai_trade_market_digest: bool` | `True` | 总开关：注入价格走势/成交量/因子证据摘要；`False`=不注入（prompt 回退到无证据段） |
| `ai_trade_digest_trend_window: int` | `20`（`ge=2`） | 价格走势/区间位置的回看根数 |
| `ai_trade_digest_vol_window: int` | `20`（`ge=2`） | 成交量均值/zscore 回看根数 |

> 均为 `AI_TRADE_` 前缀，`desktop/src/lib/config.js backendEnv()` 的 prod 白名单已按前缀透传（见 [[aragon-ai-auto-trading]] P1-6 结论），**无需改** `desktop` 白名单；`config.py` **必须声明**这三字段（`BaseSettings extra="ignore"` 会静默丢弃未声明 env）。默认值使打包桌面无需注入任何 `??=` 默认行（默认即生效）。指标周期（RSI 14 / EMA 9,21 / MACD 12,26,9 / BB 20,2 / ATR 14）与 `_MIN_BARS=35`（RC-6）作为**模块级常量**写在 `market_digest.py`（规避「`AI_TRADE_` env 三处联动」易踩点，与 `trace.py` 阈值常量同风格）。**研判留痕相关阈值**同样为模块级常量、不入 config：`_ANALYSIS_MAX_CHARS=4000`（`decider.py`，`_last_analysis` 全文上限）、`_ANALYSIS_EXCERPT_CHARS=400`（journal / decision 事件的有界摘录长度，RC-2/RC-3；置于 `session.py` 或 `trace.py` 模块级）。

---

## 三、File / module change plan（文件 / 模块变更计划）

| 文件 | 动作 | 一句话意图 |
|---|---|---|
| `services/quant/app/ai_auto/market_digest.py` | **新增** | 纯函数 `build_market_digest` + `_price_action`/`_volume`/`_factors`：从 `base_df` 因果算价格走势/成交量/技术因子证据（复用 `engine/indicators`）；禁用/不足/异常→None |
| `services/quant/app/ai_auto/prompts.py` | 改 | 重写系统 prompt 为「信号仅供参考 + 分步思维链」；`build_decision_prompt` 插入三段证据 + 改小标题/末句；更新 `DECISION_TOOL_DESCRIPTION`；强化 `build_summary_prompt` 记录采纳/推翻后验；新增 `_format_price_action`/`_format_volume`/`_format_factors` |
| `services/quant/app/ai_auto/decider.py` | 改 | `_last_analysis` 累积（与 emit 解耦）+ `last_analysis` 属性；`commit_round` 增 `analysis` kw 入 journal。**安全映射逐字不变** |
| `services/quant/app/ai_auto/session.py` | 改 | `_build_decision_payload` 注入 `marketDigest`；`_run_decision_round` 取 `last_analysis` 并接线 commit/emit；`_emit_decision` 增 `analysis`/`market_digest` kw |
| `services/quant/app/ai_auto/trace.py` | 改 | `new_round_trace` 带 `marketDigest`；`_begin_round_trace` 的 `round_start` 事件带 `marketDigest`（在 `_begin_round_trace` 处，属 session.py） |
| `services/quant/app/ai_auto/backtest.py` | 改（可选） | 决策轮 payload 增 `marketDigest`（因果切片 `main_df.iloc[:i+1]`）——回测决策同享，不接线轨迹 |
| `services/quant/app/config.py` | 改 | 新增 `ai_trade_market_digest` / `ai_trade_digest_trend_window` / `ai_trade_digest_vol_window` 三字段 |
| `services/quant/app/schemas.py` | 改（可选） | `AiTradeDecisionOut`（`schemas.py:378`）追加可选 `analysis`/`marketDigest`；`AiTradeRoundTraceOut`（`schemas.py:394`，**已含 `analysis`**）追加可选 `marketDigest`（RC-9，round_start/trace 承载它）。decision/trace 事件均手搭 dict、schema 仅文档镜像（见既有 R-P1-1 约定） |
| `apps/web/lib/aiTradeTypes.ts` | 改 | 新增 `MarketDigest`(含 `PriceAction`/`VolumeContext`/`FactorReadings`) 接口；`AiTradeDecision`/`AiTradeTraceEvent`/`AiTradeRoundTrace` 增可选 `marketDigest`；`AiTradeDecision` 增可选 `analysis` |
| `apps/web/lib/aiTrade.ts` | 改 | 新增 `describePriceAction`/`describeVolume`/`describeFactors` 纯函数（中文化、空→""、缺省回原值） |
| `apps/web/components/Agent/AiTradeTraceRound.tsx` | 改 | `SignalContext` 折叠块多渲染证据三行（非空时） |
| **测试（新增/补充）** | | 见 §五 |
| `services/quant/tests/test_ai_trade_market_digest.py` | **新增** | `build_market_digest` 各子段：正常/边界/不足→None/禁用→None/因果性/异常降级 |
| `services/quant/tests/test_ai_trade_prompts.py` | **新增**（当前不存在，RC-9） | 断言系统 prompt 含「参考/推翻/思维链」关键语且**收尾即工具调用硬约束**（RC-4）；决策 prompt 含证据三段且 None 时省略、末句为工具调用指令 |
| `services/quant/tests/test_ai_trade_decider.py`（**已存在**） | 改 | 断言 `last_analysis` 捕获、`decide()` 入口清零使**超时/异常轮 `last_analysis==""`**（RC-1）、`commit_round(analysis=excerpt)` 入 journal 且长度 ≤ `_ANALYSIS_EXCERPT_CHARS`（RC-2/RC-3）；安全回落用例保持全绿（回归门禁） |
| `apps/web/lib/aiTrade.test.ts` | 改 | `describePriceAction`/`describeVolume`/`describeFactors` 正常/空/未知枚举 |

> **不改清单（显式冻结）**：`ai_auto/signals.py`（`apply_consensus_gate`/`decision_to_signals`/`evaluate_signal_board`）、`ai_auto/market_context.py`、`ai_auto/market_window.py`、`ai_auto/manager.py`（门禁/互斥/预算）、`live_auto/executor.py`、`okx/trading.py`、`engine/indicators.py`（只读复用）、前端 store/`useAiTradeStream`/`useAiTradeGroupStream`（决策事件透传已带新可选字段）。

---

## 四、Interface design（接口设计）

**无新增 REST / WS 端点**。既有 `/api/ai-trade/*` 与 `/ws/ai-trade/{session_id}` 契约不变；仅**在既有 `decision` 与 `agent_trace(round_start)` 事件负载上追加可选字段**（旧前端忽略未知字段，向前兼容；无 store 版本 bump）：

`decision` 事件新增（可选）：
```jsonc
{
  "...": "既有字段不变（ts,round,action,sizeFraction,confidence,reason,signalBoard,executed,marketContext,consensus,degradeReason）",
  "analysis": "本轮研判思维链的【有界摘录】（≤_ANALYSIS_EXCERPT_CHARS≈400 字符，供左栏决策卡首段；无/降级轮为 null。RC-3：完整 CoT 不走 decision 事件——见 agent_trace 流与 snapshot.agentTraces[].analysis）",
  "marketDigest": {
    "priceAction": { "trend": "up|down|flat", "changePct": 1.23, "emaRelation": "fast_above|fast_below",
                     "emaSlopePct": 0.42, "rangePos": 0.78, "swingHigh": 0, "swingLow": 0,
                     "consecUp": 3, "consecDown": 0, "lastBar": { "o":0,"h":0,"l":0,"c":0,"bodyPct":0.3 } },
    "volume":      { "last": 0, "sma": 0, "ratio": 1.8, "zscore": 1.2, "trend": "rising|falling|flat", "spike": false },
    "factors":     { "rsi14": {"value":63.2,"label":"neutral"}, "macdHist": {"value":12.4,"label":"bullish"},
                     "bollPctB": {"value":0.82,"label":"upper_half"}, "atrPct": {"value":1.1,"label":"normal"},
                     "rocPct": {"value":2.3,"label":"positive"} }
  }
}
```
`marketDigest` 任一子段可为 `null`（数据不足）；整体在禁用/窗口不足时为 `null`（该段不出现于 prompt）。

**决策工具 schema 不变**（`submit_trade_decision{action,sizeFraction,confidence,reason}`，`required=[action,reason]`）——研判分步经**流式文本**产出并留痕，不塞进工具入参（避免动确定性映射的稳健性）。

---

## 五、Data model（数据模型）

- **无 DB 迁移**。会话运行态（`_decisions`/`_traces`/`_journal`）为进程内瞬态；`marketDigest`/`analysis` 随 `decision`/`round_start`/journal 内存流转，停止时经既有 `finalize_run(metrics=...)` 落 `strategy_runs.metrics_json`（自由 JSON，无需列变更，见 [[aragon-ai-auto-trading]] P2-5）。
- **decider 内存态新增**：`_last_analysis: str`（每轮 `decide()` 入口清零，RC-1）；`_journal` 条目 shape 由 `{round,signalBoard,decision,executed,positionAfter,sessionPnl}` 扩为附加 `analysis`（**有界摘录 ≤`_ANALYSIS_EXCERPT_CHARS`**，非全文，RC-2）。
- **前端瞬态**：`AiTradeRoundTrace.marketDigest` / `AiTradeDecision.analysis` 为可选字段，**不入 partialize**（运行态瞬态，随会话结束回收）；`aiTradeConfig` 持久化偏好**不变**（无版本 bump）。
- **`marketDigest` 内存 dict 形状**：见 §四（后端 `build_market_digest` 产物 = 前端 `MarketDigest` wire 镜像）。

---

## 六、Testing & acceptance（测试与验收标准）

**零回归门禁（最高优先，先跑）**：既有 `ai_auto` 全部单测（决策安全回落：超时/无工具/非法入参/缺 size/低置信/低共识 → hold；`decision_to_signals` 映射；`apply_consensus_gate` 门禁；backtest runner）**必须保持全绿且断言值不变**——证明本方案未触动任何确定性安全路径。全量 `pytest`（既有 ~924 passed/1 skipped 基线，那 1 个 skipped 是既知 flaky 计时测试，见 [[aragon-quant-test-isolation]]）与 `vitest`、`tsc` 保持基线（既知无关 `WeightBatchArena.test.tsx` 预存 tsc 错误除外）。

**新增/修改测试（先写测试后写实现，遵 CLAUDE.md）**：
1. `test_ai_trade_market_digest.py`：
   - `build_market_digest` 正常返回三段且键齐全；`ai_trade_market_digest=False` → `None`；`len(df) < _MIN_BARS` → `None`；子段异常（如全 0 成交量致除零）→ 该段 `None` 而非抛错。
   - **因果性（RC-7 校正）**：主保证由「因子读数与 `engine/indicators` 直算对拍」承担——`ema/sma/rsi/bollinger/atr` 均为 `ewm`(从首行递推、`adjust=False`)/`rolling`(尾窗) 的纯因果算子，且窗口位置类（`rangePos`/`swingHigh/Low`）只取**尾 N 根**（`c[-N:]` 等），故只要**调用方传因果切片**即无未来泄漏。测试断言：① 逐子段读数 == 对同一 `df` 直接调 `engine/indicators` 取末值；② 追加任意「未来根」再切回 `df.iloc[:k]`，结果与仅 `df.iloc[:k]` 一致（弱恒真，仅作回归护栏）；③ 用 session/backtest 的实际切片位点（`base_df()` / `main_df.iloc[:i+1]`）验证末行=当前决策根。
   - 因子读数与 `engine/indicators` 直算一致（RSI14/ATR14/BB20 抽样对拍）。
2. `test_ai_trade_prompts.py`：系统 prompt 含「参考」「推翻」「一步一步 / 思维链」「必须……调用一次」等关键约束语；`build_decision_prompt` 在 `marketDigest` 非空时含价格/成交量/因子三段、为 `None` 时**不**出现半截空信息；小标题含「仅供参考」。
3. decider 测试：`last_analysis` 在流式 `text_delta` 后被捕获（含 `emit=None` 分支）；**RC-1 串轮防护**——先跑一轮有分析文本、再跑一轮令 `_stream_decision` 超时/抛错，断言第二轮 `last_analysis == ""`（不残留上一轮）；`commit_round(analysis=excerpt)` 使 journal 末条含该 `analysis` 且 `len ≤ _ANALYSIS_EXCERPT_CHARS`（RC-2/RC-3）；**安全回落用例断言值全不变**。
4. `aiTrade.test.ts`：`describePriceAction`/`describeVolume`/`describeFactors` 正常/空对象→""/未知枚举回原值。

**验收标准（Definition of Done for #2 开发）**：
- 开启 AI 自动交易后，决策 prompt 中可见「价格走势 / 成交量 / 技术因子」三段真实证据与「信号仅供参考、请逐条批判研判」的措辞；右侧「AI 助手」轨迹面板每轮展示**分步骤思维链**研判（而非单段 2–4 句）。
- 关闭 `ai_trade_market_digest` 时，prompt 回退到无证据段（可对比验证），确定性行为无差异。
- 全部零回归门禁通过；`ruff check`（改动文件）与 `tsc` 干净。
- 单文件 ≤800 行、单函数 ≤50 行（`market_digest.py` 预拆三个子构造器；`prompts.py` 若逼近 800 行则拆分证据格式化 helper）。

---

## 七、Risks & mitigations（风险与缓解）

| # | 风险 | 缓解 |
|---|---|---|
| R-1 | **「因子」语义歧义**：需求「各个因子」可能指重型 `engine/factors` 子系统，而本方案用通用技术因子读数。 | §2.3 明确 scope 边界：给 AI 独立于策略的经典技术因子证据即满足「综合各因子研判」之意；与因子层子系统深度对接列为后续可选增强，并在 spec 显式记录该决策供 #1 评审确认。 |
| R-2 | **prompt 变长/思维链更啰嗦 → 单轮 LLM 延迟上升**，慢于出 bar 间隔致 `_deciding` 跳过增多。 | 证据摘要刻意**紧凑**（每段几行、数值+短标签）；沿用既有 `ai_trade_decision_timeout_s`（超时回落 hold）与 `_deciding` in-flight 跳过（P1-2 不变）；启动建议 `bar ≥ 15m` 的既有提示继续适用。 |
| R-3 | **决策行为改变（非默认等价）**：更严研判 + 更多证据会改变 LLM 输出分布。 | 这是**需求目标**、非缺陷；但**所有确定性守卫零回归**（§六门禁）确保「行为变化」被严格限制在「LLM 在更好输入 + 更严纪律下自主决策」层，资金护栏/门禁/映射不变；`ai_trade_market_digest` 开关提供一键回退对照。 |
| R-4 | **证据计算异常/数据不足**污染决策或崩决策轮。 | `build_market_digest` 全程 try/except + `_MIN_BARS` + 逐段 None 降级（镜像 `build_market_context`）；异常整段省略、绝不假数据、绝不抛到决策轮外。 |
| R-5 | **因果泄漏（未来函数）**：证据摘要误用当前根之后的数据。 | 所有子构造器只用 `df` 末行及之前；回测切片 `iloc[:i+1]`；`test_*_market_digest` 专测因果性（改未来根不影响结果）。 |
| R-6 | **`analysis` 文本膨胀撑爆 journal / 摘要输入 / WS snapshot**（评审 RC-2/RC-3 深化）。 | 全文 `_ANALYSIS_MAX_CHARS=4000` 仅走 trace 流与 `_traces`(≤20)；**进 journal 与 decision 事件的一律为 `_ANALYSIS_EXCERPT_CHARS≈400` 有界摘录**（journal 默认 10 轮≈4KB、`_decisions`(≤50)≈20KB，均受控）；摘要输入不含全文；journal 随每 N 轮摘要清空（既有机制）；`_TraceCoalescer` 合并/上限不变。 |
| R-7 | **回测 runner 接线越界本轮最小闭环**。 | §2.6 标为「可选加分项」，若 #1/#2 认为超范围即降级为「仅实盘」并记 §实施缺陷；不影响实盘主目标。 |
| R-8 | **规模/风格越界 CLAUDE.md 阈值**。 | `market_digest.py` 预拆三子函数（各 ≤50 行）；`prompts.py` 证据格式化抽 helper；改动文件走 `ruff`/`tsc` 门禁；不引入新第三方依赖（仅 `pandas`/`numpy` + 既有 `engine/indicators`）。 |

---

## 八、实施顺序建议（供 #2 开发节点）

1. `config.py` 加三字段 → `market_digest.py` 新模块 + 单测（先测因果/降级）。
2. `prompts.py` 重写系统 prompt + 决策 prompt 证据段 + summary 强化 + 单测。
3. `decider.py` `decide()` 入口 `_last_analysis=""` 清零（RC-1）+ `last_analysis` 捕获 + `commit_round(analysis=excerpt)` + 单测（含串轮防护 RC-1、摘录长度 RC-2/3、安全回落回归）。
4. `session.py` payload 注入 `marketDigest` + 接线 `analysis`/emit + `trace.new_round_trace`。
5. （可选）`backtest.py` payload 注入 `marketDigest`。
6. 前端类型 + `describe*` + `AiTradeTraceRound` 渲染 + 单测。
7. 跑零回归门禁（pytest / vitest / tsc / ruff）→ 记录任何偏差到「## 实施过程发现的方案缺陷」段。

---

## 实施过程发现的方案缺陷（Issues Found During Implementation · #2 开发节点）

> 本段由 Subtask #2 代码开发节点在落地过程中追加，记录 spec v2 与真实实现之间的偏差与就地修正。

**F-1（一致性 · 轻微，已就地修正）：§三「不改清单」冻结 `apps/web/lib/store.ts`，但 R4 前端观测实际需要一处 store 增量。**
依据：§2.7 / R4 要求右侧「AI 助手」轨迹面板（`AiTradeTraceRound.tsx`）渲染 `AiTradeRoundTrace.marketDigest`。
该轮次对象由 store 归约器 `applyAiTradeTracePhase` → `emptyRoundTrace` 从 `round_start` 事件**逐字段挑取**装配
（并非整体 spread 事件——`marketContext` / `consensus` 亦是显式拷贝）。若不在 `emptyRoundTrace` 补
`marketDigest: evt.marketDigest ?? null`，则**实时累积**的轮次 `trace.marketDigest` 恒为 `undefined`，证据三行
在轨迹面板永不出现（R4 前端观测形同虚设）。§三「不改清单」把 store 判为「决策事件透传已带新可选字段」——该判断对
`decision` 事件（store 整体存 `AiTradeDecision`，新字段随对象顺风车）成立，但对 `agent_trace(round_start) →
AiTradeRoundTrace` 归约路径不成立。**修正**：在 `store.ts` `emptyRoundTrace` 补一行
`marketDigest: evt.marketDigest ?? null`（一处、只读、向后兼容，不动其余归约 / partialize / 版本）。此为使 R4
生效的必要最小接线，非重构；决策卡另经 `decision` 事件顺风车字段已自动覆盖，无需再改 store。

**F-2（一致性 · 记录，不修）：`services/quant/app/ai_auto/backtest.py:20` 预存 `from dataclasses import dataclass, field`
的 `field` 为未使用导入（`ruff F401`），在 HEAD 即存在、与本方案无关。** 依「保持 diff 聚焦、不做方案外清理」
未一并移除（属既有技术债，留给独立清理）；本方案对该文件的两处增量（payload 注入 `marketDigest`、decision 事件补
`marketDigest`）本身 ruff 干净。

---

*（本 `spec.md` 为设计文档，篇幅已覆盖 Overview / 技术设计 / 文件变更计划 / 接口 / 数据模型 / 测试与验收 / 风险缓解 全部要求；下游可据此逐行实现，无需再做架构决策。）*

---

## 评审结论（Review Verdict · v2）

**结论：有条件通过（Approved with Conditions）。**

方案已对照仓库真实代码逐节核验，四个维度评价如下：

- **可行性（Feasibility）：通过。** `build_market_digest` 依赖的 `engine/indicators.ema/sma/rsi/bollinger/atr` 签名、`MarketWindow.base_df()`、回测 `main_df.iloc[:i+1]` 切片、`build_market_context` 安全降级范式、config「字段名即 `AI_TRADE_` env 名」机制、前端 `describeRegime`/`SignalContext` 模板均已核实存在；新增件为 1 个纯模块 + 增量接线，无新第三方依赖，可直接落地。
- **一致性（Consistency）：通过。** 与既有 E1/E2/E3 增量的注入位、降级姿态、wire「文档镜像」约定、CLAUDE.md（≤800 行/≤50 行/PEP8/Google docstring/先测后码）一致；所有确定性安全守卫冻结，无破坏性变更。
- **完整性（Completeness）：修复后通过。** 评审补齐了 4 个 P1 边界/稳健性缺口（串轮 analysis 污染、摘要素材膨胀、snapshot 膨胀、长 prompt 下工具收尾指令位次），均已在正文就地修复。
- **合理规模（Right-sizing）：通过。** 增强有界、以增量为主、默认可一键回退（`ai_trade_market_digest`），未过度接入重型 `engine/factors` 子系统，规模与「决策质量增强」目标匹配。

**已就地修复并纳入 v2 的 P0/P1：** 无 P0；P1 共 4 项（RC-1 串轮清零、RC-2 摘要素材有界摘录、RC-3 decision/snapshot 有界摘录、RC-4 工具收尾指令置末），详见「评审记录」与正文 §2.1/§2.2/§2.4/§2.5/§2.8/§三/§四/§五/§七。

**放行条件（供 #2 开发前确认 / 落实）：**

1. **【需上游确认 · RC-5】** 「因子」scope 取**通用技术因子读数**（RSI/MACD/BB/ATR/ROC），**不**接入重型 `engine/factors` 挖掘/评分/权重子系统。此为满足用户「综合各因子一步步研判」的对齐取舍；若产品意图是后者，需回退 #0 重做方案。默认按本方案（通用技术因子）推进。
2. **【开发落实 · P1 门禁】** RC-1~RC-4 的实现必须配套对应单测（串轮防护、摘录长度 ≤ `_ANALYSIS_EXCERPT_CHARS`、prompt 收尾即工具调用断言），并纳入 §六「零回归门禁」一同验收。
3. **【开发酌情 · P2】** RC-6（`_MIN_BARS≥35`）、RC-7（因果测试以 `engine/indicators` 直算对拍为主）、RC-8（`_price_action` 视行数拆子助手）、RC-9（schema 镜像补 `AiTradeRoundTraceOut.marketDigest`；`test_ai_trade_prompts.py` 为新增）、RC-10（回测 decision 事件对称补 `marketDigest`）已写入正文，落地时遵循即可，不阻断通过。
4. **【硬红线 · 不可回退】** 全部确定性安全守卫（`_map_decision` / `apply_consensus_gate` / `decision_to_signals` / 前置门禁 / `LiveExecutor`）与「分析后必须以一次 `submit_trade_decision` 收尾」硬约束逐字节保持；§六零回归门禁（既有 `ai_auto` 全部单测断言值不变）为合并前置条件。

> 评审人：方案评审节点（senior reviewer）。修复范围仅限本 `spec.md`，未触碰任何源码，未 `git commit`。
