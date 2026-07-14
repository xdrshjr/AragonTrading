# AI 自动交易 · 下单前二次反思校验（ReAct 校验闭环）设计方案

> Feature slug: `ai-trade-pre-trade-reflection`
> 版本：**v2**（Subtask #1 方案评审与修复产出；v1 为 Subtask #0 方案设计初稿）
> 关联既有特性：ai-auto-trading（v2 已上线）、ai-trade-reasoning-chain（思维链研判）、
> ai-trade-decision-llm-retry（决策重试）、ai-auto-trading-agent-trace（执行轨迹面板）、
> ai-auto-trading-backtest（回测复用同一决策脑）。

---

## 评审记录（Review Notes · Subtask #1）

> 评审人：Anthropic 工程团队资深评审。评审维度：可行性（Feasibility）/ 完整性（Completeness）/
> 一致性（Consistency，含 CLAUDE.md）/ 规模适配（Right-sizing）。已逐节对照仓库现状核对
> （`session.py`/`decider.py`/`backtest.py`/`backtest_manager.py`/`manager.py`/`signals.py`/`trace.py`/
> `config.py`/`desktop/src/lib/config.js` 及 5 个前端文件均已比对）。所有 P0/P1 已就地修复并标注锚点。

**核对通过项（无需改动）**：插入点 `session.py::_run_decision_round`（decide→共识门禁→execute，约
399–402 行）与后端 `decide/_map_decision/_RETRYABLE_DEGRADE_REASONS/CancelledError` 重抛及重试环、
`signals.quantize_size_fraction / apply_consensus_gate / decision_to_signals`、`manager._build_member`
的 `req.settings` 可达、`trace.PHASES/new_round_trace`、config 的 `ai_trade_default_position_step_pct`
先例、`desktop/src/lib/config.js:72` 的 `AI_TRADE_` 前缀透传白名单（故「不改 config.js」成立）、
5 个前端文件路径——均与仓库现状一致。

| # | 维度 | 严重度 | 问题 | 处置 |
|---|---|---|---|---|
| RV-1 | 可行性/完整性 | **P1** | 回测反思装配链路缺失且定位错误：文件清单称在 `_run_decision` 构造 decider 传 `reflection_enabled`，但决策器实际在 `AiBacktestRunner.__init__`（backtest.py:164）一次性构造、跨轮复用；且缺少 `position_step_pct` 那样的传参链路（`AiBacktestSpec` 字段 + `backtest_manager._build_spec` 从 `req.settings` 注入）。若让 runner 直接读 `self.settings.ai_trade_reflection_enabled`（默认 True），所有「直接构造 `AiBacktestSpec/AiBacktestRunner` 且用默认 Settings」的既有回测测试会平白多出一次未脚本化反思调用而变红，破坏 DoD#5 与 §6.2「reflection_enabled=False 回测逐字一致」。 | 已修复：§2.5、§三 增补 `AiBacktestSpec.reflection_enabled: bool=False` 字段、`backtest_manager.py` 装配行，并把构造点更正为 `__init__`。 |
| RV-2 | 一致性 | **P1** | `session.py` 现已 **872 行**，已越 CLAUDE.md §二「单文件 ≤800 行」上限；本特性再向其追加反思编排 + 三个轨迹发射方法会加剧越限。 | 已修复：§三/§八 明确反思轨迹装配下沉 `trace.py`（镜像 `new_round_trace/trace_event`），`apply_reflection` 落新 `reflection.py`，`_reflect_before_execute` 仅保留精简编排；并显式声明既有越限、本特性净增须最小化。 |
| RV-3 | 完整性/正确性 | **P1** | §2.5 回测伪代码在 `hold`（else 分支）未定义 `verdict`，末尾 `self._tally_reflection(verdict)` 触发 `NameError`（Subtask #2 若照抄即崩）。 | 已修复：§2.5 在 if 前置 `verdict = None`，`_tally_reflection` 对 None 空转。 |
| RV-4 | 正确性 | P2 | 收缩仓位的「只减不增」与量化顺序：`quantize_size_fraction` 用 `round()` 就近取整，可能把调整值吸附到 ≥ 原档位；须保证最终值恒 `= min(原档位, quantize(调整值))`（先量化，再对原值取 min 作最后一步）。 | 已修复：§2.3 明确该顺序。 |
| RV-5 | 完整性 | P2 | `reflect()` 的重试须自带一份与 `decide()` 同构的有界重试环 + 反思专属可重试原因集（abort 除外），而非「复用同一循环」（decide 的循环内联、不可直接共享）。 | 已修复：§2.4 补明。 |
| RV-6 | 完整性 | P2 | `_emit_degraded`（session.py:696）把原始 `reason` 直接插值进状态条文案，不会自动产出 §2.7 承诺的「二次反思否决为观望」友好中文；此与既有 `low_confidence` 原样展示一致（非回归）。 | 已修复：§2.7 校正——状态条沿用既有 `(reason)` 格式，友好文案由前端 `describeReflection`/`degradeReason` 展示层承载。 |
| RV-7 | 规模适配 | P2 | R-1 提到的 `ai_trade_reflection_on_close` 未进 §4.3 config 表，易致范围蔓延。 | 已修复：§4.3/R-1 标注为「v1 不实现的未来可选项」。 |
| RV-8 | 完整性 | P2 | manager 级集成测试若经 `_build_member` 且用默认 Settings，反思默认开后会多一次反思调用。 | 已修复：§6.2/R-5 补明须在这些用例显式关反思或脚本化反思调用。 |

**结论**：见文末「评审结论」——**有条件通过**。P0：无；P1（RV-1/2/3）：均已就地修复；P2 已注明处置。

---

## 一、Overview（概述）

本项目的「AI 自动交易」让 LLM Agent 扮演多策略之上的决策脑：每个已确认 K 线（或墙钟定时唤醒）
触发一轮决策，Agent 先做**分步思维链研判**（价格走势 / 成交量 / 市场体制 / 技术因子 / 逐策略批判 /
持仓与盈亏），再调用唯一放行工具 `submit_trade_decision` 产出 `open_long / open_short / close / hold`
之一，随后经共识门禁校验，映射为 `Signal`，由守护执行器 `LiveExecutor` 下**真实资金**订单。目前这
条链路是「一次研判 → 一次决策 → 立即下单」——研判虽然充分，但**真正不可逆的下单动作前没有一道
独立的自我复核**。真实合约交易一旦下单即产生手续费、滑点与敞口，判断偏差的代价是即时且真金白银的。

本方案在「决策」与「真实下单」之间插入一个**二次反思（reflection）校验环节**，形成
**ReAct（Reason → Act → Observe/Reason → Act）闭环**：第一轮研判是「提议（propose）」，二次反思是
「复核（verify）」，真实下单是「提交（commit）」。当且仅当 Agent 本轮产生了**实际交易意愿**
（`open_long / open_short / close`）时，才发起一次**独立的**反思 LLM 子调用——它拿到「本轮全部证据 +
Agent 刚给出的决策」，以资深风控/交易员的批判视角重新审视：方向对不对、是否与高周期背景冲突、
当前持仓与会话盈亏下这笔交易的风险是否可接受、仓位是否过重、有没有更稳妥的选择。反思给出
`confirm`（确认执行）或 `abort`（否决 → 本轮降级为 `hold` 不下单），并可选择性地**收缩**仓位。
若本轮决策是 `hold`（无交易意愿），则**完全跳过**反思——不做无谓的 LLM 调用（符合需求「没有触发买卖
操作意愿时不需要反思」）。

设计的**首要不变式是「反思只会让执行更保守，绝不会更激进」**：反思可以把一笔交易否决成 `hold`、
或把仓位调小，但**永远不能**把 `hold` 变成交易、不能反转方向、不能放大仓位。反思子调用一旦超时 /
报错 / 未产出合法裁决，一律**失败关闭（fail-closed）→ 降级 `hold`**（本轮不下单），因为「校验环节
自身不可用」时最安全的选择是不执行这笔未经复核的交易；会话级止盈止损与交易所侧 OCO 作为独立于
LLM 的兜底继续生效。该机制默认在实盘生效，并在回测中同步复用（保持「决策脑不变」不变式），可由
单一开关整体关闭以逐字回退到当前行为。

---

## 二、Technical design（技术设计）

### 2.1 核心插入点

唯一的控制流插入点位于 `services/quant/app/ai_auto/session.py::AiTradeSession._run_decision_round`，
在**共识门禁之后、真实执行之前**：

```
# 现状（session.py 约 399–402 行）
decision, degrade = await self.decider.decide(payload, emit=...)          # ① Reason → Act（提议）
decision, degrade = self._apply_consensus_gate(decision, consensus, degrade)  # 共识补偿门禁
# —— 新增：② 二次反思（Observe/Reason → Act 复核），仅交易意愿触发 ——
decision, degrade = await self._reflect_before_execute(
    round_no, ts, payload, decision, degrade)
executed = await self._execute_decision(decision, ref_px, i, ts)          # ③ Act（真实提交）
```

反思是**决策链的一个纯附加层**：它消费 `decision`，产出一个「同方向或更保守」的 `decision`。
下游 `_execute_decision / commit_round / _emit_*` 全部保持不变——它们看到的始终是「反思之后的最终
决策」，因此 journal / 滚动摘要 / decision 事件 / tool_call 轨迹自然记录的是真实落地的动作（延续
既有「commit_round 收门禁后最终 decision」的语义）。

### 2.2 反思逻辑归位于 decider（内聚 + 回测共享）

把反思 LLM 调用实现为 `AiTradeDecider` 的新方法 `reflect(...)`，与 `decide(...)` 并列，理由：
（a）一切 LLM 决策/校验逻辑集中在决策器，`session` 只负责编排与轨迹；（b）回测运行器
`backtest.py::AiBacktestRunner` 复用同一个 `AiTradeDecider`，`reflect` 落在决策器上即天然被回测复用，
无需第二份实现，保持「回测与实盘逐字相同的决策脑」不变式。

**反思是无状态子调用**：它**不**向 `_context` / `_journal` 追加任何消息（反思是对本轮决策的一次性
复核，不是新的决策轮），也**不**参与滚动摘要——上下文纯净性完全不受影响。反思的输入通过参数一次性
传入（本轮 `payload` + 提议 `decision`），输出即返回，`decide()` 的滚动上下文机制零改动。

**围栏与安全姿态**：完全镜像 `decide` 的「唯一放行工具」范式——只把 `submit_trade_reflection` 一个
工具传给 `llm.stream(tools=[...])`，无 `tool_choice` 强制，正确性由**安全回落**兜底：反思超时 /
LLM 错误 / 无工具调用 / 入参非法 / verdict 非法 → 一律 `fail-closed`（等价 abort → hold）。
`asyncio.CancelledError` 立即上抛（绝不吞、绝不降级），使 `stop()` 能干净取消在飞的反思。

### 2.3 零回归开关（镜像 position_step 先例）

沿用本仓库对「会改变控制流的增强」的既定范式（参照 `ai_trade_default_position_step_pct`）：

- **config 默认开**：`Settings.ai_trade_reflection_enabled: bool = True`——真实交易理应经过校验，这是
  用户明确诉求的默认行为。
- **直接构造默认关**：`AiTradeDecider.__init__(..., reflection_enabled: bool = False)`——所有既有
  「直接 `AiTradeDecider(...)` / `AiTradeSession(...)`」的测试位置默认拿到「关闭」语义，`reflect` 成为
  无副作用直通，**逐字零回归**。
- **生产装配显式开**：`manager.py::_build_member` 与 `backtest.py` 构造决策器时显式传入
  `reflection_enabled=req.settings.ai_trade_reflection_enabled`。

`session` 通过只读属性 `decider.reflection_enabled` 决定是否进入反思分支：

```
async def _reflect_before_execute(self, round_no, ts, payload, decision, degrade):
    if not self.decider.reflection_enabled or decision.action == "hold":
        return decision, degrade                      # 关闭 或 无交易意愿 → 原样直通（无 LLM、无轨迹）
    await self._begin_reflection_trace(round_no, ts, decision)     # reflection_start
    verdict = await self.decider.reflect(
        payload, decision, emit=self._make_trace_emit(round_no, ts))
    final, new_degrade = self._apply_reflection(decision, degrade, verdict)  # 只会更保守
    await self._emit_reflection_verdict_trace(round_no, ts, verdict)
    return final, new_degrade
```

`_apply_reflection`（纯函数，可单测）：

- `verdict.degrade_reason` 非空（fail-closed）或 `verdict.verdict == "abort"` →
  返回 `(_hold(reason), degrade or reason)`，其中
  `reason ∈ {"reflection_abort", "reflection_timeout", "reflection_llm_error", "reflection_no_tool", "reflection_invalid"}`。
- `verdict.verdict == "confirm"`：
  - 若 `verdict.adjusted_size_fraction` 有值且 `< decision.size_fraction`（仅对 `open_*`）→ **先**用
    `quantize_size_fraction(adjusted, step)` 吸附到合法档位，**再**对原档位取
    `min(decision.size_fraction, quantized)` 作为**最后一步**替换 `size_fraction`（RV-4：因 `quantize`
    用 `round()` 就近取整、可能把调整值抬到 ≥ 原档位，故 `min` 收口是「只减不增」不变式的硬保证——
    量化在前、对原值 clamp 在后，无论舍入方向最终值都 ≤ 原档位）。
  - 否则原样返回 `decision`。
- 无论如何**不改** `action` 方向、不把 `hold` 变交易、不放大仓位。

### 2.4 反思重试（复用决策重试基建）

反思的瞬时失败（超时 / LLM 错误 / 无工具 / 非法裁决）先带**有界指数退避**重试至多
`ai_trade_reflection_max_retries` 次（复用 `agent.llm.retry.backoff_delay_ms` 与
`ai_trade_retry_base_ms/max_ms`，与 decider 重试同基建、同抖动语义），穷尽仍失败才 fail-closed。
`verdict == "abort"` 是模型的**真实终态裁决**，零重试（对齐 decider 对 `low_confidence` 的处理）。

**实现说明（RV-5）**：`decide()` 的重试环是**内联**在 `decide` 内、不可直接复用，故 `reflect()` 须
**自带**一份与之同构的有界重试环——首次 `_attempt_reflection` → 判定可重试 → 退避 → 再试，穷尽降级。
「可重试」由反思专属白名单判定：`_RETRYABLE_REFLECTION_REASONS = {"reflection_timeout",
"reflection_llm_error", "reflection_no_tool", "reflection_invalid"}`；`reflection_abort` **不在集内**
（终态裁决，零重试）。退避辅助与抖动常量直接复用 decider 的 `_retry_backoff` 同款实现（`backoff_delay_ms`
+ `_RETRY_JITTER_MS`），避免第二套退避语义漂移。

### 2.5 回测同步（决策脑不变式）

**装配链路（RV-1，镜像 `position_step_pct` 先例）**：回测的决策器在 `AiBacktestRunner.__init__`
（backtest.py:164）**一次性构造、跨轮复用**——反思开关须像 `position_step_pct` 一样经数据类字段传参，
**不能**让 runner 直接读 `self.settings.ai_trade_reflection_enabled`（默认 `True`），否则所有「直接构造
`AiBacktestSpec/AiBacktestRunner` 且用默认 Settings」的既有回测测试会平白多一次未脚本化反思调用而变红。
具体三处（与 `position_step_pct=0.0` 的默认关语义逐字对齐）：

1. `AiBacktestSpec` 新增 `reflection_enabled: bool = False`（默认关 → 直接构造该 dataclass 的既有测试零回归）。
2. `backtest_manager._build_spec` 用 `reflection_enabled=req.settings.ai_trade_reflection_enabled` 填该字段
   （生产路径显式开）。
3. `AiBacktestRunner.__init__` 把 `spec.reflection_enabled` 存 `self.reflection_enabled`，并在**构造决策器时**
   传 `reflection_enabled=self.reflection_enabled`（**不是**在 `_run_decision` 里构造 decider）。

**逐轮调用**：在 `AiBacktestRunner._run_decision` 的 `apply_consensus_gate` 之后、`_execute` 之前，
插入等价的反思调用（`emit=None`，回测无订阅、轨迹全程空转）；`verdict` 须在分支外**预置 `None`**，
否则 `hold` 轮走 else 分支时 `_tally_reflection(verdict)` 会 `NameError`（RV-3）：

```
decision, gate_reason = apply_consensus_gate(decision, consensus, self.min_consensus)
verdict = None                                     # RV-3：预置，hold 轮 else 分支也有定义
reflect_reason = gate_reason
if self.decider.reflection_enabled and decision.action != "hold":
    verdict = await self.decider.reflect(payload, decision)         # emit 缺省 None
    decision, reflect_reason = apply_reflection(decision, gate_reason, verdict)  # 复用 session 同款纯函数
executed = self._execute(decision)
...
self._tally_reflection(verdict)   # verdict is None → 空转；否则统计 reflectionRuns/Aborts/Adjustments
```

反思裁决的降级并入既有 `degradeReason`（`decide_degrade or gate_reason or reflect_reason`），回测
`_aggregate_stats` 追加 `reflectionRuns` / `reflectionAborts` / `reflectionAdjustments` 三个计数
（`_tally_reflection(None)` 不递增任何计数，保持 `reflection_enabled=False` 时逐字等价旧行为）。

### 2.6 轨迹与可观测性（ReAct 过程留痕）

复用既有 `agent_trace` WS + `_TraceCoalescer` + 在飞轮次机制，新增三个阶段常量（前后端镜像）：

- `reflection_start`：反思开始（仅交易意愿轮出现），携带被复核的 `decision` 快照。
- `reflection`：反思推理流式增量（经合并器按阈值冲刷；单 phase 累计沿用 `_TRACE_MAX_CHARS` 硬上限）。
- `reflection_verdict`：最终裁决（`confirm/abort`）+ 收缩后仓位（若有）+ 理由。

`session._make_trace_emit` 扩展：把 `phase == "reflection"` 的 delta 累积进 `trace["reflection"]`；
`reflection_start` 置 `trace["reflectionPending"]=True`；`reflection_verdict` 落
`trace["reflectionVerdict"] / trace["reflectionReason"] / trace["reflectionAdjustedSize"]`
并清 `reflectionPending`。左栏 decision 事件与 `decisionsDigest` 附带
`reflectionVerdict / reflectionReason`（旧前端忽略未知字段，向前兼容）。

### 2.7 关键调用时序（open_long 被反思否决示例）

```
确认 K 线 → feed_candle → _run_decision_round
  ├─ build payload + consensus + round_start(trace)
  ├─ decider.decide()      → 研判(analysis/thinking 流) → submit_trade_decision(open_long, 0.5)
  ├─ apply_consensus_gate  → (仍 open_long)
  ├─ _reflect_before_execute（enabled 且 action≠hold）
  │    ├─ reflection_start(trace: 复核 open_long 0.5)
  │    ├─ decider.reflect() → 反思流(reflection) → submit_trade_reflection(abort, "高周期顶背离，追多风险高")
  │    ├─ _apply_reflection → decision=hold, degrade="reflection_abort"
  │    └─ reflection_verdict(trace: abort)
  ├─ _execute_decision(hold) → 空信号 → 不下单（真实资金零动作）
  ├─ commit_round(hold) / tool_call(hold) / result / decision 事件(reflectionVerdict=abort)
  └─ _emit_degraded("reflection_abort")：状态条沿用既有格式「AI 决策降级为观望（reflection_abort）：本轮未下单」
```

> **RV-6**：`_emit_degraded`（session.py:696）把原始 `reason` 直接插值进状态条（与既有
> `low_confidence`/`decision_timeout` 原样展示一致，非回归）；「二次反思否决」的**友好中文**由前端
> `describeReflection` 与左栏 `degradeReason` 展示层承载（§三前端清单）。状态条本身**不**在本特性引入
> reason→文案映射（避免为单条文案再动 session.py，加剧 RV-2 越限）；如后续要友好化，另起小改。

---

## 三、File / module change plan（文件 / 模块变更计划）

### 后端（services/quant）

| 文件 | 变更类型 | 一句话意图 |
|---|---|---|
| `app/config.py` | 修改 | 新增 `ai_trade_reflection_enabled`(True) / `ai_trade_reflection_timeout_s`(45.0) / `ai_trade_reflection_max_retries`(2)；复用既有 retry_base/max_ms。 |
| `app/ai_auto/prompts.py` | 修改 | 新增 `REFLECTION_TOOL_NAME` / `REFLECTION_TOOL_SCHEMA` / `build_reflection_system_prompt` / `build_reflection_prompt`（把「本轮证据 + 提议决策」压成复核输入）。 |
| `app/ai_auto/decider.py` | 修改 | 新增 `ReflectionVerdict` dataclass、`reflection_enabled` 只读属性、`reflect()` 方法（唯一放行反思工具 + 安全回落 + 重试 + 流式 emit）、`_map_reflection` 纯映射；`__init__` 加 `reflection_enabled: bool=False` 形参。 |
| `app/ai_auto/reflection.py` | **新建** | `apply_reflection(decision, degrade, verdict, position_step)` 纯函数（session/backtest 共用「只会更保守」的裁决落地逻辑），避免 decider 膨胀、便于单测。 |
| `app/ai_auto/session.py` | 修改 | `_run_decision_round` 插入 `_reflect_before_execute`（精简编排）；反思轨迹装配**下沉 `trace.py`**（session 仅薄封装 `_begin_reflection_trace` / `_emit_reflection_verdict_trace` 调 trace.py builder，**RV-2**：session.py 已 872 行越 CLAUDE.md 800 上限，本特性净增须最小化、禁止内联大段装配）；`_make_trace_emit` 扩展 reflection 阶段；decision 事件/`_view_*` 带 `reflectionVerdict/Reason`。 |
| `app/ai_auto/trace.py` | 修改 | `PHASES` 追加 `reflection_start` / `reflection` / `reflection_verdict`；`new_round_trace` 增 `reflection`/`reflectionPending`/`reflectionVerdict`/`reflectionReason`/`reflectionAdjustedSize` 初值；**RV-2**：承接从 session 下沉的反思 `trace_event` 装配（镜像既有 `new_round_trace/trace_event` 风格），控 session.py 增量。 |
| `app/ai_auto/backtest.py` | 修改 | `_run_decision` 插入等价反思调用（`emit=None`）+ 复用 `apply_reflection`，`verdict` 预置 `None`（RV-3）；**RV-1**：决策器在 `__init__`（非 `_run_decision`）构造，`__init__` 存 `self.reflection_enabled=spec.reflection_enabled` 并据此传 `reflection_enabled`；`AiBacktestSpec` 加 `reflection_enabled: bool=False` 字段；`_aggregate_stats` 加 `reflectionRuns/Aborts/Adjustments`；decision 事件带 `reflectionVerdict`。 |
| `app/ai_auto/backtest_manager.py` | **修改（RV-1 补漏）** | `_build_spec` 用 `reflection_enabled=req.settings.ai_trade_reflection_enabled` 填 `AiBacktestSpec.reflection_enabled`（镜像既有 `position_step_pct=req.position_step_pct` 装配行；生产回测显式开、直接构造 dataclass 的既有测试默认关）。 |
| `app/ai_auto/manager.py` | 修改 | `_build_member` 构造 `AiTradeDecider` 时传 `reflection_enabled=req.settings.ai_trade_reflection_enabled`（`req.settings` 已可达，manager.py:342；每成员各一决策器，多标的分组自动覆盖）。 |

### 前端（apps/web）

| 文件 | 变更类型 | 一句话意图 |
|---|---|---|
| `lib/aiTradeTypes.ts` | 修改 | `AiTradeTracePhase` 加三阶段；`AiTradeTraceEvent` 加 `verdict/adjustedSizeFraction`；`AiTradeRoundTrace` 加 `reflection/reflectionPending/reflectionVerdict/reflectionReason/reflectionAdjustedSize`；`AiTradeDecision` 加 `reflectionVerdict/reflectionReason`。 |
| `lib/store.ts` | 修改 | `emptyRoundTrace` 增反思初值；`reduceRoundPhase` 加 `reflection_start`/`reflection`/`reflection_verdict` 三 case（reflection 累积文本，verdict 落裁决/清 pending）。 |
| `lib/aiTrade.ts` | 修改 | 新增 `describeReflection(trace)`（确认执行 / 否决→观望 / 复核中 的标签 + tone），中文文案单点。 |
| `components/Agent/AiTradeTraceRound.tsx` | 修改 | tool_call 卡与 result 之间插入「二次反思（ReAct 校验）」段：流式反思文本（markdown）+ 裁决 chip + 理由 + 收缩后仓位。 |
| `components/Chart/AiTradePanel.tsx`（或决策卡片处） | 修改（可选） | 左栏最近决策行在被反思否决时展示「⛔ 二次反思否决」小标，与 `degradeReason` 展示一致。 |

### 文档

| 文件 | 变更类型 | 一句话意图 |
|---|---|---|
| `docs/plans/ai-trade-pre-trade-reflection/spec.md` | 新建（本文件） | 本设计方案。 |
| `README.md` | 修改（可选，Subtask #2 末） | 在「AI 自动交易」段补一句「下单前二次反思校验」能力说明。 |

> **无需改动**：`desktop/src/lib/config.js`（env 透传白名单为 `AI_TRADE_` 前缀匹配，新增
> `AI_TRADE_REFLECTION_*` 自动覆盖）；`signals.py`（`quantize_size_fraction` 直接复用）；
> `live_auto/executor.py`（真实下单路径完全不变，反思只作用于「送进 executor 之前」的 decision）。

---

## 四、Interface design（接口设计）

### 4.1 反思决策工具（LLM 唯一放行工具）

```python
REFLECTION_TOOL_NAME = "submit_trade_reflection"
REFLECTION_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["confirm", "abort"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "adjustedSizeFraction": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string", "maxLength": 500},
    },
    "required": ["verdict", "reason"],
}
```

- `verdict=confirm`：确认原决策；`adjustedSizeFraction` 可选，**仅当 < 原仓位时收缩**（否则忽略）。
- `verdict=abort`：否决 → 本轮降级 `hold`（`adjustedSizeFraction` 忽略）。
- 缺失 / 非法 / 无工具调用 / 超时 / 报错 → fail-closed（等价 abort）。

### 4.2 反思系统 prompt 要点（`build_reflection_system_prompt`）

「你是一名独立的下单前风控复核官。刚才另一名交易员基于同样的证据给出了一个**交易决策**（会真实
下单、动用真金）。你的职责**不是**重新交易，而是**批判性地复核这个决策是否稳健**：① 方向是否被证据
支持、有无更强的反向信号被忽略；② 是否与更高周期中线背景 / 市场体制冲突；③ 结合当前真实持仓与
会话累计盈亏，这笔交易的风险敞口是否可接受、是否在追高/杀跌/频繁反手；④ 仓位是否偏重、能否更保守；
⑤ 是否存在证据不足却强行交易。若发现任何足以令你不放心的问题，选择 `abort`（宁可错过，不可做错）；
只有当你确信该决策稳健时才 `confirm`。你**只能**收紧（否决或调小仓位），无权放大或改变方向。收尾
必须且只能调用一次 `submit_trade_reflection`。」

### 4.3 config（`Settings`，字段名即 `AI_TRADE_*` 环境变量名）

| 字段 | 默认 | 含义 |
|---|---|---|
| `ai_trade_reflection_enabled` | `True` | 反思总闸；`False` 逐字回退当前「决策即下单」行为。 |
| `ai_trade_reflection_timeout_s` | `45.0` | 单次反思 LLM 超时（超时 fail-closed）。 |
| `ai_trade_reflection_max_retries` | `2` | 瞬时失败重试次数（复用退避基建），穷尽 fail-closed。 |

> **RV-7（范围界定）**：R-1 提到的按动作粒度开关 `ai_trade_reflection_on_close`（单独关闭 `close` 复核）
> **不在 v1 交付范围**，本表刻意不列——v1 只交付「总闸 + 超时 + 重试次数」三个字段，避免范围蔓延。
> `close` 被反思否决的风险已由「会话级止盈止损 + 交易所侧 OCO」独立兜底（见 R-1）。该按动作开关列为
> 未来可选增强，届时再按需补入。

### 4.4 WS `agent_trace` 新阶段

| phase | 触发 | 关键字段 |
|---|---|---|
| `reflection_start` | 交易意愿决策进入反思 | `input`（被复核决策 `{action,sizeFraction,confidence,reason}`） |
| `reflection` | 反思推理流式增量 | `delta` |
| `reflection_verdict` | 反思裁决 | `verdict`、`adjustedSizeFraction?`、`reason` |

`decision` 事件与 `decisionsDigest` 追加：`reflectionVerdict: "confirm"|"abort"|null`、
`reflectionReason: string|null`。

---

## 五、Data model（数据模型）

### 5.1 后端 dataclass（decider.py）

```python
@dataclass
class ReflectionVerdict:
    verdict: str                       # "confirm" | "abort"
    adjusted_size_fraction: float | None   # 仅 confirm 且收缩时有值，否则 None
    confidence: float | None
    reason: str
    degrade_reason: str | None         # 非空 = fail-closed（等价 abort），如 reflection_timeout
    ran: bool                          # True=真跑过反思；False=关闭/未触发（供轨迹判定）
```

### 5.2 轨迹轮次新增字段（trace.new_round_trace / 前端 AiTradeRoundTrace 镜像）

```
reflection: str            # 累积反思推理文本（默认 ""）
reflectionPending: bool    # 反思在飞指示（默认 False）
reflectionVerdict: "confirm" | "abort" | null   # 默认 null
reflectionReason: str | null
reflectionAdjustedSize: number | null           # 收缩后的仓位占比（无收缩为 null）
```

### 5.3 无数据库迁移

反思是**纯瞬态 / 内存 + WS**能力：不新增任何表、列或索引。落库仅通过既有 `metrics_json` 的加法富化——
`finalize_run` 的 `aiStats` 与回测 `_aggregate_stats` 追加 `reflectionRuns/Aborts/Adjustments` 计数
（延续既有「只加法填 `metrics_json`、零 DB 迁移」范式）。`decision.reason` 在被反思否决时改写为反思
理由，随既有 journal / explain 路径落地，无新增持久化结构。

---

## 六、Testing & acceptance criteria（测试与验收）

### 6.1 后端单测（新增 `tests/test_ai_trade_reflection.py`，纯离线、桩 LLM）

沿用 `test_ai_trade_decider.py` 的 `_ScriptedLLM`（每次 `stream()` 弹一段脚本）：

1. `confirm` → 原 `open_long` 决策与仓位原样通过、下游照常执行。
2. `abort` → 决策降级 `hold`、`degradeReason="reflection_abort"`、不产生开仓信号。
3. **决策为 `hold` 时 `reflect` 从不被调用**（`_ScriptedLLM.calls` 断言：hold 轮只 1 次 LLM 调用）。
4. 反思超时 / LLM 错误 / 无工具 / 非法 verdict → 一律 fail-closed 降级 `hold`（对应 `reflection_*` 原因）。
5. `confirm` + `adjustedSizeFraction` 小于原值 → 仓位被收缩且吸附到合法档位；大于/等于原值 → **忽略**（不放大）。
6. `abort` 不重试；瞬时失败按 `ai_trade_reflection_max_retries` 重试后才降级（断言 `calls`）。
7. `asyncio.CancelledError` 从 `reflect` 原样上抛（不被吞成 hold）。
8. **零回归门禁**：`reflection_enabled=False`（直接构造默认）时 `reflect` 直通、无 LLM 调用，
   既有 `test_ai_trade_decider.py` / `test_ai_trade_session.py` 全绿。

### 6.2 会话 / 回测集成测

- `test_ai_trade_session.py`：新增「反思否决 open → executor 未被调用（无真实下单）+ decision 事件
  `reflectionVerdict=abort`」；既有开仓用例更新脚本，补上反思子调用（`confirm`），证明打开反思后开仓仍可落地。
- `test_ai_trade_reflection.py` 覆盖 `apply_reflection` 纯函数的全部分支（confirm/abort/收缩/fail-closed）。
- 回测：`reflection_enabled=True` 时反思 abort 使该决策根不 `submit`，`_aggregate_stats.reflectionAborts` 正确计数；
  `reflection_enabled=False` 回测结果与当前逐字一致（决策脑不变式回归）。
- **RV-8（manager 级用例）**：任何经 `_build_member` 且用默认 `Settings`（`ai_trade_reflection_enabled=True`）
  的既有集成测试，在反思默认开后每笔交易意愿会多一次反思子调用。这些用例须二选一：（a）测试 `Settings`
  显式置 `ai_trade_reflection_enabled=False`；或（b）在脚本 LLM 中补上反思子调用。直接构造
  `AiTradeSession/AiTradeDecider`（决策器默认 `reflection_enabled=False`）的用例不受影响、零回归。

### 6.3 前端测试

- `store` reducer 单测：`reflection_start`/`reflection`(累积)/`reflection_verdict` 三 case 归约正确、容乱序。
- `AiTradeAgentTrace.test.tsx`：交易意愿轮渲染出「二次反思」段与裁决 chip；`hold` 轮不渲染反思段。
- 组件测试遵循 `NODE_ENV=test`（既知：component vitest 需 `NODE_ENV=test`，否则 React act() 报错）。

### 6.4 验收标准（Definition of Done for 特性）

1. 打开反思（默认）时：`open_long/open_short/close` 决策在下单前**必经**一次独立反思子调用；`hold` **绝不**触发反思。
2. 反思只会更保守：可否决为 `hold` 或收缩仓位，**永不**放大 / 反向 / 把 hold 变交易（单测证明）。
3. 反思不可用（超时/错误）时 fail-closed 不下单；会话止盈止损与交易所 OCO 兜底不受影响。
4. 反思过程在右侧执行轨迹面板可视（ReAct 复核留痕）；被否决时左栏与状态条有明确提示。
5. `ai_trade_reflection_enabled=False` 时全链路（含回测）与当前版本**逐字等价**。
6. 后端 pytest（不含 network）+ 前端 tsc/vitest 全绿；无新增 DB 迁移。

---

## 七、Risks & mitigations（风险与缓解）

| # | 风险 | 缓解 |
|---|---|---|
| R-1 | **平仓被反思否决延迟保护性退出**：`close` 被 abort → 保持持仓，可能延后一根/一轮才平。 | 平仓的 fail-closed 语义是「本轮不动、下一轮再评估」，且**会话级止盈止损 + 交易所侧 OCO 独立于 LLM 持续生效**作为兜底；风险仅为「保护性平仓最多延后一个决策周期」，在 Risks 中显式声明，接受该权衡（宁可让独立护栏兜底，也不让未复核判断误动）。按动作粒度开关 `ai_trade_reflection_on_close` 列为**未来可选增强、v1 不实现**（RV-7），避免范围蔓延。 |
| R-2 | **LLM 调用翻倍→成本/时延**：每笔交易意愿多一次 LLM 调用。 | 反思**仅对交易意愿**触发（`hold` 跳过），实际频率远低于决策轮；`hold` 占比通常高。反思超时更短（45s < 决策 60s）。总闸可关。回测侧 `decision_every_bars + max_rounds` 既有护栏封顶。 |
| R-3 | **决策轮总时长变长→占用 `_deciding` 槽更久**，期间新确认根被跳过。 | 既有 `_deciding` in-flight 守卫本就设计为「上一轮未完成则跳过本轮」，反思延长的是同一轮，语义一致；`_exec_lock` 只在真实写时短暂持有，反思是纯 LLM 无锁，不阻塞 monitor 盯市/reconcile。 |
| R-4 | **反思与决策自相矛盾造成频繁否决→几乎不交易**。 | 反思 prompt 明确「只在确有不放心处才 abort」；`reflectionAborts` 计数入 stats/回测可观测，便于调参；abort 只影响本轮，下一轮重新研判，不产生死锁。 |
| R-5 | **既有 session/backtest 测试因默认打开反思而红**（多出未脚本化的 LLM 子调用）。 | 采用 position_step 同款「直接构造默认关」：所有直接构造 `AiTradeDecider/AiTradeSession`（决策器 `reflection_enabled=False` 默认）与直接构造 `AiBacktestSpec`（`reflection_enabled=False` 默认，RV-1）的既有测试 → 反思直通、零回归；仅 `manager._build_member` / `backtest_manager._build_spec` 生产装配从 `req.settings` 显式打开。需在 Subtask #2 更新少量「验证打开后行为」及 **manager 级默认 Settings** 用例并补反思脚本（RV-8）。 |
| R-6 | **`CancelledError` 被误吞→ stop() 无法及时取消在飞反思**。 | `reflect()` 逐字镜像 `decide()` 的异常处理：`except asyncio.CancelledError: raise` 先于兜底 `except Exception`，保证会话停止时反思立即上抛。 |
| R-7 | **反思放大仓位或反向的越权风险**（若映射有 bug）。 | `apply_reflection` 为纯函数且**结构上只实现「否决 / 收缩」两种收敛操作**：仓位取 `min(原值, 调整值)` 且仅 `open_*`，`action` 方向永不改写；配 6.1-5 专项单测断言「放大被忽略」。 |
| R-8 | **前端旧客户端不识别新 phase/字段**。 | `trace_event` 沿用 `{type,data}` 信封、decision 事件加法带 `reflection*` 字段，旧前端忽略未知 type/字段（既有向前兼容范式）；reducer 对未知 phase 走 `default` 原样返回。 |
| R-9 | **上下文污染**：反思消息误入滚动上下文。 | `reflect()` **不触碰** `_context/_journal`；仅 `commit_round` 记录反思后的最终 decision（与既有「记最终决策」一致），滚动摘要素材纯净性零影响，专门单测断言反思后 `_context` 长度与内容不含反思消息。 |

---

## 八、实施注意（供 Subtask #2）

- **常量归位**：反思相关阈值（如反思文本硬上限）与 `trace.py` 现有 `_TRACE_MAX_CHARS` 同风格，作模块级
  常量，规避「`AI_TRADE_` env 三处联动」既知易踩点；仅「开关 / 超时 / 重试次数」进 `Settings`。
- **延迟导入**：若 `reflection.py` ↔ `signals.py`(`quantize_size_fraction`) 或 `decider` 出现模块级环，
  沿用既有「函数内延迟导入打破环」手法（参照 `_map_decision` 里对 `signals.quantize_size_fraction` 的延迟导入）。
- **精确暂存**：本仓库工作树常同时含多特性未提交改动，Subtask #3 提交须 `git add <具体路径>` 精确暂存
  本特性文件，严禁 `git add -A`（既知教训）。
- **测试隔离**：`services/quant` 测试共享一个临时 DB，注意既有一条时序 flaky 测试，勿误判为本特性回归。
- **文件尺寸红线（RV-2）**：`session.py` 本次核对时已 **872 行**，越 CLAUDE.md §二「单文件 ≤800 行」上限。
  本特性对 session.py 的净增须**最小化**：反思轨迹 `trace_event` 装配一律走 `trace.py`（镜像
  `new_round_trace/trace_event`），`_reflect_before_execute` 只保留「判分支 → 调 decider.reflect → 调
  `apply_reflection` → 发两条轨迹」的精简编排，`apply_reflection` 落 `reflection.py`。若 Subtask #2 完成后
  session.py 行数进一步恶化，应优先把既有轨迹发射方法（如 `_emit_*_trace` 系列）外提到 `trace.py`，而非
  为腾挪空间牺牲本特性正确性。
- **反思 LLM 调用形态（RV-5 落地）**：`reflect()` 用**独立** system prompt（`build_reflection_system_prompt`）
  与**单轮** `messages=[build_reflection_prompt(payload, decision)]`（**不拼 `self._context`**），只传
  `submit_trade_reflection` 一个工具、无 `tool_choice`；`_stream_reflection` 复用 `_stream_decision` 的
  partial_json 累积范式取首条有效工具入参。确保「无状态、零上下文污染」（R-9）。

---

## 评审结论（Review Verdict · Subtask #1）

**结论：有条件通过（Approved with conditions）。**

本方案的**核心设计是稳健的**：插入点选取（共识门禁后、真实执行前）、安全不变式（fail-closed、只更保守、
永不放大/反向/把 hold 变交易、`CancelledError` 立即上抛）、零 DB 迁移、决策脑回测复用、以及「决策器默认关 +
生产装配显式开」的零回归范式，均与仓库现状和既有约定（`ai_trade_default_position_step_pct` /
`ai-trade-decision-llm-retry` / `ai-auto-trading-agent-trace`）严丝合缝。逐节核对的插入点、函数签名、
config 先例、env 透传白名单、前端文件路径全部属实。**未发现 P0。**

**放行条件（Subtask #2 代码开发必须逐条落实）**：

1. **【RV-1】回测反思装配走数据类字段链路**：`AiBacktestSpec` 加 `reflection_enabled: bool = False`，
   `backtest_manager._build_spec` 从 `req.settings.ai_trade_reflection_enabled` 注入，决策器在
   `AiBacktestRunner.__init__`（**非** `_run_decision`）构造时接收——严禁让 runner 直接读默认 True 的
   settings，以免既有回测测试平白变红、破坏 DoD#5。文件清单已补 `backtest_manager.py` 一行。
2. **【RV-2】守住 CLAUDE.md 800 行红线**：session.py 已 872 行；反思轨迹装配下沉 `trace.py`、
   `apply_reflection` 落 `reflection.py`，`_reflect_before_execute` 保持精简，禁止内联大段装配。
3. **【RV-3】回测逐轮 `verdict` 预置 `None`**：`hold` 轮 else 分支不得触发 `NameError`；`_tally_reflection(None)`
   须空转、不递增计数，保证 `reflection_enabled=False` 逐字等价旧行为。
4. **【RV-4】收缩仓位顺序**：最终值恒 `= min(原档位, quantize(调整值))`（先量化、再对原值 clamp 收口），
   以 6.1-5 单测断言「放大/等值被忽略」。
5. **【RV-5/RV-8】反思自带重试环 + 反思专属可重试原因集**；manager 级默认 Settings 集成测试须关反思或
   脚本化反思调用。

以上 5 条落实后，本方案满足 Definition of Done，可进入代码开发。P2 项（RV-6/RV-7）已在正文注明处置，
不构成放行障碍。

---

## 实施过程发现的方案缺陷（Issues Found During Implementation · Subtask #2）

> 以下为代码开发（Subtask #2）过程中相对 v2 方案的**微调与补充**，均在方案意图内、不改变核心不变式，
> 记录于此供 Subtask #3 评审知悉。**未发现需要推翻方案的实质缺陷。**

| # | 位置 | 发现 | 处置 |
|---|---|---|---|
| I-1 | `build_reflection_prompt`（§4.2/RV-5「复用决策 prompt 的证据装配」）| 直接整体复用 `build_decision_prompt` 会把其**末句「必须且只能调用一次 `submit_trade_decision`」**一并带入反思输入，与反思「只能调用 `submit_trade_reflection`」的指令**自相矛盾**，可能诱导复核官尝试调用不在其工具表内的决策工具（继而被判 `reflection_no_tool` fail-closed）。 | 已在实现中处置：`build_reflection_prompt` 在嵌入证据前加一段**中和说明**（「其中调用 submit_trade_decision 的指令是给原交易员的、与你无关，你不要调用它」），并把反思指令置于最末（近因位）+ 强调「唯一任务是调用 submit_trade_reflection，也不得调用其它工具」。system prompt 亦 100% 反思导向、只传反思一个工具（物理围栏）。新增 `test_ai_trade_prompts.py::test_reflection_prompt_carries_evidence_and_proposed_decision` 守门。 |
| I-2 | `reflection_verdict` 事件 / 轨迹 `reflectionAdjustedSize`（§2.6/§4.4「收缩后仓位」）| 方案未明确 `adjustedSizeFraction` 是**模型原始提议值**还是**量化并 `min` 收口后的实际生效值**。前者会与实际下单档位不符（误导 UI）。 | 已取**实际生效值**：新增纯函数 `reflection.applied_shrink(original, final)`（仅 `open_*` 且严格变小才返回 `final.size_fraction`，否则 `None`），session 用其派生 `reflectionAdjustedSize` 与 WS 事件字段，保证「收缩后仓位」= 真实落地档位。 |
| I-3 | abort 时 `_hold(reason)` 的 `reason`（§2.3 用降级 code vs §5.3「decision.reason 改写为反思理由」）| §2.3 伪代码 `_hold(reason)` 的 reason 取降级 code，与 §5.3「否决时决策理由改写为反思理由」措辞不一致。 | 已两全：`apply_reflection` 对 abort 取 `_hold(verdict.reason or code)`——决策 `reason` 展示**人类可读的反思理由**（§5.3），降级返回值与状态条仍用**稳定 code**（`reflection_abort`，§2.7）。fail-closed 时 `verdict.reason` 即 code，回落一致。 |
| I-4 | 前端 `AiTradePanel.tsx`（§三「修改（可选）」，标注「**或决策卡片处**」）| AiTradePanel 现只渲染「标的行」，无「最近决策行」可挂反思否决小标；强加会制造不存在的 UI 元素（范围蔓延）。 | 采方案给出的**「或决策卡片处」替代位置**：反思展示落在决策卡片 `AiTradeTraceRound`（新增「二次反思（ReAct 校验）」段 + 裁决 chip + 收缩后仓位），否决态并经既有 `degradeReason → describeExecuted`「已降级观望（reflection_abort）」呈现。故 `AiTradePanel.tsx` 本身**无需改动**（可选项经替代位置满足）。`README.md`（亦标「可选」）未改：该 README 为 Phase 1 平台总览，未文档化 AI 自动交易特性，无自然落点，加一句反思会成孤儿段落。 |
| I-5 | `session.py` 行数（RV-2 800 行红线）| 本特性落地后 session.py 由 872 → 932 行（+60），仍越 CLAUDE.md 800 上限（**越限为既有、非本特性引入**）。 | 已按 RV-2 最小化净增：反思三阶段 `trace_event` 装配全部下沉 `trace.py`（`reflection_start_event`/`reflection_verdict_event`），`apply_reflection`/`applied_shrink` 落 `reflection.py`，session 只保留精简编排（`_reflect_before_execute` 等 4 个薄方法）。RV-2 明确「不得为腾挪空间牺牲本特性正确性」，故未对既有 `_emit_*_trace` 做超范围外提（属独立重构、触碰工作代码）。 |
