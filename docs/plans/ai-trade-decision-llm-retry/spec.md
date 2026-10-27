# AI 自动交易 · 决策 LLM 失败重试后再降级（ai-trade-decision-llm-retry）

- **版本**: v2（Subtask #1 评审：P0/P1 已就地修复；见下「评审记录」与文末「评审结论」）
- **作者**: Solution Architect（Subtask #0）；Senior Reviewer（Subtask #1 · v2）
- **状态**: Reviewed（有条件通过）
- **关联迭代**: 第 2 轮需求——「LLM 调用失败/相关错误时先重试三次，再降级，不要直接降级」
- **前序上下文**: 第 1 轮已把决策器从「信号跟随」升级为「思维链独立研判」（commit `d2a4a82` / `1dbf909`）。本轮**不回滚**既有研判链路，只在其失败兜底路径上加「重试后再降级」。

---

## 评审记录（Review Notes · Subtask #1 · v2）

> 评审人：Senior Reviewer（Anthropic 工程团队）。已逐节对照四维——**可行性 / 完整性 / 一致性（对 CLAUDE.md 与现有代码）/ 合理规模**——并核对真实代码：
> `ai_auto/decider.py`、`ai_auto/trace.py`、`ai_auto/session.py`、`agent/llm/retry.py`、`config.py`、`apps/web/lib/{aiTradeTypes,store}.ts`、`components/Agent/AiTradeAgentTrace.tsx`、既有单测。
> 下表 P0/P1 已在 **v2 正文就地修复**；P2 亦一并修正，力求给下游「代码开发」节点一份无歧义契约。总体评价：设计方向正确、签名/迁移零改动、有真正的回退开关，工程质量高；修复项集中在「重试期轨迹重置的完整性」「文档内部一致性」与「把留给评审的开放决策拍死」。

| 编号 | 维度 | 严重度 | 问题 | 处置（v2） |
|---|---|---|---|---|
| R-1 | 完整性/一致性 | **P1** | §4.4 重试重置集**漏了 `toolCallPending`**。核对真实流程：`invalid_action`/`missing_size` 这两类**可重试**失败，其失败尝试**已**发过 `tool_call_start`（`_stream_decision` 里 `pump.mark` → session `_emit` 置 `_round_trace["toolCallPending"]=True`）。仅重置 `thinking/analysis` 会让「正在调用工具」挂起指示从失败尝试残留到重试期，与本特性「丢弃失败尝试痕迹」的核心目标自相矛盾。 | §4.4 后端 `retry` 分支与前端 `case "retry"` 均补 `toolCallPending=False`；§七数据模型同步；新增 TS 用例断言重置。 |
| R-2 | 一致性 | **P1** | 可选重试提示（§4.5）与 §4.2 `decide()` 示例、§六接口**三处互相矛盾**：§4.2 未传 `hint`、§4.5 称「循环把上一次 degrade 作 hint 传入」、§六签名带 `hint`。下游节点无从取舍。 | v2 定调**保留 hint**（低风险、提升 `no_tool_call/invalid_action/missing_size` 自愈率），并把 §4.2/§4.5/§六三处改成一致：`decide()` 把上一次 `degrade` 作 `hint` 传入下一次 `_attempt_decision`，首次 `hint=None`，hint 只进**瞬时** `user_msg`（不落 `_context/_journal`）。 |
| R-3 | 合理规模/完整性 | **P1** | §十把三项关键取舍（默认重试次数、§4.5 是否保留、最坏时延可接受度）**留给评审**。Definition of Done 要求「无 P0/P1 悬置」，这些悬置正是本节点须收敛的交付物，不能再下传。 | v2 就地拍板：`ai_trade_decision_max_retries` 默认 **3**（忠实第 2 轮需求「重试三次」= 最多 4 次 LLM 调用）；保留 hint（见 R-2）；最坏时延**有界且可取消**，§九补调参指引；§十开放问题改为「评审已定」。 |
| R-4 | 可行性 | P2 | §4.3 用 `random.uniform`，但 `decider.py` 现**未 import `random`**，§五变更清单也只提「导入 `backoff_delay_ms`」，照此实现会 `NameError`。 | §4.3/§五补 `import random`（与 `agent/llm/retry.py` 既有用法一致）。 |
| R-5 | 完整性/测试 | P2 | 超时用例 #7 与跨尝试 #10 **确定性未钉死**：`_ScriptedLLM` 逐脚本弹出且事件零耗时，**无法**制造 `wait_for` 超时；若不显式调小 `ai_trade_decision_timeout_s` 并置 `base/max_ms=0`，#7 要么等满 60s 要么偶发。 | §八 #7 明确：另写有状态 stub（首次 `asyncio.sleep` > 调小后的 timeout，其后产 valid），`ai_trade_decision_timeout_s=0.05`、`base/max_ms=0`；并注明 `_ScriptedLLM` 不能模拟挂起。 |
| R-6 | 一致性 | P2 | `last_analysis` 属性 docstring 与相关注释仍写「每轮 `decide()` 入口清零」，而 v2 把清零下沉到 `_attempt_decision`（每次尝试）。 | §4.2 注明清零语义变为「每次尝试入口」，实现须同步该 docstring 措辞（对外行为不变）。 |
| R-7 | 一致性 | P2 | 前端 `emptyRoundTrace` 拟只初始化 `retries:0`，与后端 `new_round_trace` 同时初始化 `retries=0/lastRetryReason=None` 不对称。 | §五/§七要求前端 `emptyRoundTrace` 同时初始化 `retries:0, lastRetryReason:null`。 |
| R-8 | 可行性（复核 ✅ 无需改动） | — | 复核确认无回归/无隐藏耦合：`new_round_trace`（Py）与 `emptyRoundTrace`/轨迹（TS）的现有单测均为**逐字段**断言，非整体 `toEqual`/键集断言；快照为**裸 dict**（无 Pydantic 严格模型）→ 追加 `retries/lastRetryReason` 键**不破坏**既有测试与序列化。`backoff_delay_ms(attempt,base,max)` 为 0-based；`_ScriptedLLM` 逐脚本弹出；`config` `case_sensitive=False` 的 env 覆盖成立；`AI_TRADE_*` 设置**全仓仅后端消费、无跨服务镜像**（grep 证实），不存在 `trace.py` 注释所指「三处联动」硬约束。 | 记录以佐证「可行」，不改文档。 |

---

## 一、概述（Overview）

当前右侧 AI 助手开启自动交易后，每根确认 K 线触发一次 `AiTradeDecider.decide()`：向 LLM 流式请求一次「思维链研判 + 单次 `submit_trade_decision` 工具调用」，把工具入参映射成受约束的 `TradeDecision`。出于「不确定绝不下单」的资金安全姿态，**任何**失败（LLM 超时、网络/流式错误、未产出有效工具调用、入参非法）都会**立即**回落为 `hold`，并在前端弹出常驻横幅「决策降级中（LLM 超时 / 未产出有效决策 → 本轮回落观望，资金安全优先）」。

这套「一次失败即降级」的策略过于激进：LLM 网关的瞬时抖动（429/5xx、连接重置、读超时）、偶发的空工具调用或半截 JSON，都是**瞬时、可自愈**的故障，本应重试而非直接放弃一整轮交易机会。本轮需求要求：**当 LLM 调用失败或产出无效时，先带退避地重试若干次（默认 3 次重试），全部失败后才降级到观望**；同时，模型**真正做出的决策**（包括刻意的 `hold`、以及置信度不足被风控门禁拦下的 `low_confidence`）不是「失败」，绝不重试。

本设计在**不改动** `decide()` 对外签名（`tuple[TradeDecision, str | None]`）的前提下，把 `decide()` 内的「单次尝试」抽成 `_attempt_decision()`，外面套一个有界指数退避重试循环；引入「可重试 degrade 原因」白名单区分**瞬时失败**与**终态决策**；复用既有 `agent/llm/retry.py::backoff_delay_ms` 计算退避；并在决策认知轨迹（agent_trace）中新增 `retry` 阶段，保证重试时前端不重复累积上一次失败尝试的研判文本、且以面包屑如实呈现「正在第 n/m 次重试」。所有新增行为在 `max_retries=0` 时逐字回退到今日直接降级的旧行为，保证可回退、零强制回归。

---

## 二、背景与现状（Root Cause）

关键代码 `services/quant/app/ai_auto/decider.py:108-126`：

```python
async def decide(self, payload, *, emit=None) -> tuple[TradeDecision, str | None]:
    self._last_analysis = ""
    user_msg = LlmMessage("user", [TextBlock(build_decision_prompt(payload))])
    try:
        raw = await asyncio.wait_for(
            self._stream_decision(user_msg, emit),
            timeout=self.settings.ai_trade_decision_timeout_s)
    except asyncio.TimeoutError:
        return _hold("decision_timeout"), "decision_timeout"   # ← 直接降级
    except Exception as exc:  # noqa: BLE001
        logger.warning("ai-trade decide failed: %s", exc)
        return _hold("llm_error"), "llm_error"                 # ← 直接降级
    return self._map_decision(raw)                             # ← 失败也直接降级
```

`decide()` 产生的 `degrade` 取值全集与来源：

| degrade 原因 | 来源 | 语义 | 本设计分类 |
|---|---|---|---|
| `decision_timeout` | `asyncio.wait_for` 超时 | 单轮 LLM 调用超时 | **可重试** |
| `llm_error` | `_stream_decision` 抛异常（含流内 `error` 事件 → `RuntimeError`） | LLM/网络/流式失败 | **可重试** |
| `no_tool_call` | `_map_decision(None)`（只回文本无工具 / 空 order） | 未产出有效决策 | **可重试** |
| `invalid_action` | action 不在 `_ACTIONS` | 产出畸形（动作非法） | **可重试** |
| `missing_size` | `open_*` 缺 `sizeFraction` 或 ≤0 | 产出畸形（缺仓位） | **可重试** |
| `low_confidence` | 合法开仓但置信度 < `ai_trade_min_confidence` | **模型真实决策**，被风控门禁降级 | **终态·不重试** |
| `None` | 合法决策（含刻意 `hold`） | 模型真实决策 | **终态·不重试** |

> 注：`_stream_decision` 内 `json.JSONDecodeError`（半截/畸形 JSON）走 `return None` → `_map_decision` 得 `no_tool_call`，因此归入「未产出有效决策」的可重试集，无需单列。

> 注：`_apply_consensus_gate`（共识门禁，session/backtest 层）在 `decide()` **之后**执行，是独立于 LLM 的确定性风控，其 `low_consensus(k/n)` 原因**不属于** `decide()` 的重试范畴，本设计不触碰。

**现状问题**：可重试集里的瞬时故障与终态决策被一视同仁地「首次即降级」。本轮只需让**可重试集**在降级前先重试，其余保持不变。

同时，`agent/llm/retry.py::open_stream_with_retry` 只覆盖 **pre-stream** 阶段（建连 + 读状态码）的重试，一旦开始产出 SSE 事件即遵循「铁律：绝不重试防重复输出」。因此**流开始后**的中断、`asyncio.wait_for` 触发的整流超时、以及「产出成功但语义无效」三类失败，传输层不会重试——**必须**在决策器这一层补重试。

---

## 三、设计目标与非目标

**目标**
1. `decide()` 遇「可重试」失败时，带有界指数退避重试，默认最多重试 3 次（即最多 4 次 LLM 调用），全部失败才降级 `hold`。
2. 终态决策（合法动作、刻意 `hold`、`low_confidence`）**零重试**、行为逐字不变。
3. `asyncio.CancelledError`（会话停止/任务取消）**立即向上抛**，绝不吞、绝不重试、绝不降级。
4. 重试对认知轨迹透明：不重复累积失败尝试的研判文本；以 `retry` 面包屑如实呈现重试进度。
5. `decide()` 对外签名不变；`session.py`/`backtest.py` 及既有单测**零改动可通过**（除新增测试）。
6. 全部新增行为可经 config 关闭（`ai_trade_decision_max_retries=0` 回退旧行为）。

**非目标**
- 不改 `summarize()` 的失败处理（它已优雅降级：失败即保留上段摘要、不影响交易，无需重试）。
- 不改传输层 `open_stream_with_retry` 的 pre-stream 重试铁律。
- 不改共识门禁 / 置信度门禁 / OCO 等风控语义。
- 不新增数据库列 / 不做任何迁移。

---

## 四、技术设计（Technical Design）

### 4.1 失败分类白名单

在 `decider.py` 模块级新增常量（与既有 `_ACTIONS` 同风格）：

```python
# 「可重试」degrade 原因白名单（瞬时 LLM 失败 / 未产出有效决策）；其余（low_confidence 等）为终态决策，绝不重试。
_RETRYABLE_DEGRADE_REASONS = frozenset(
    {"decision_timeout", "llm_error", "no_tool_call", "invalid_action", "missing_size"})


def _is_retryable_degrade(reason: str | None) -> bool:
    """该 degrade 原因是否属于瞬时/无有效产出（可重试）；None 与 low_confidence 返回 False。"""
    return reason in _RETRYABLE_DEGRADE_REASONS
```

### 4.2 `decide()` 重构为「单次尝试 + 重试循环」

把今日 `decide()` 主体原封不动抽成 `_attempt_decision()`（一次尝试的语义与现状**逐字一致**），`decide()` 变为薄重试编排。拆分后各函数均 ≤50 行、圈复杂度 ≤10、嵌套 ≤4，满足 CLAUDE.md 阈值。

```python
async def decide(self, payload, *, emit=None) -> tuple[TradeDecision, str | None]:
    """出本轮决策；可重试失败先带退避重试，穷尽后才降级 hold。签名不变（RC/RV 契约）。"""
    max_retries = max(0, int(self.settings.ai_trade_decision_max_retries))
    decision, degrade = await self._attempt_decision(payload, emit)               # 首次：hint=None
    attempt = 0
    while degrade is not None and _is_retryable_degrade(degrade) and attempt < max_retries:
        attempt += 1
        await self._emit_retry(emit, attempt, max_retries, degrade)   # 面包屑 + 前端本轮累积重置
        await self._retry_backoff(attempt)                            # 可被 cancel 中断
        decision, degrade = await self._attempt_decision(payload, emit, hint=degrade)  # R-2：带上一次原因
    return decision, degrade


async def _attempt_decision(self, payload, emit, *, hint: str | None = None
                            ) -> tuple[TradeDecision, str | None]:
    """单次决策尝试（= 今日 decide 主体）：入口清零 last_analysis → 流式研判 → 映射。

    `hint`（keyword-only，默认 None → 与今日首次尝试逐字一致）：上一次失败的 degrade 原因，
    仅追加进**当次瞬时** user_msg（§4.5），绝不落 `_context/_journal`——上下文零污染。
    """
    self._last_analysis = ""     # RC-1（跨尝试版）：每次尝试入口清零，重试不残留上一次尝试的研判
    prompt_text = build_decision_prompt(payload)
    if hint:
        prompt_text += _retry_hint_suffix(hint)   # §4.5：仅当次重试的一次性纠偏提示（首次 hint=None 不触发）
    user_msg = LlmMessage("user", [TextBlock(prompt_text)])
    try:
        raw = await asyncio.wait_for(
            self._stream_decision(user_msg, emit),
            timeout=self.settings.ai_trade_decision_timeout_s)
    except asyncio.TimeoutError:
        return _hold("decision_timeout"), "decision_timeout"
    except asyncio.CancelledError:
        raise                    # 会话停止：绝不吞、绝不降级，向上抛（BaseException，不入下方 except）
    except Exception as exc:  # noqa: BLE001
        logger.warning("ai-trade decide attempt failed: %s", exc)
        return _hold("llm_error"), "llm_error"
    return self._map_decision(raw)
```

> **CancelledError 说明**：Py3.8+ 中 `asyncio.CancelledError` 继承 `BaseException`，今日 `except Exception` 本就不捕获它；本设计**显式**加一条 `except asyncio.CancelledError: raise` 作为可读性护栏与回归防线，语义与今日一致（取消照常向上传播）。退避里的 `asyncio.sleep` 同样在取消时抛 `CancelledError`，自然中断重试。

> **R-6 清零语义下沉**：`_last_analysis=""` 从今日的 `decide()` 入口移到 `_attempt_decision()` 入口——由「每轮清零一次」变为「每次尝试清零一次」。实现须同步更新 `last_analysis` 属性 docstring 与相关注释的措辞（「每次尝试入口清零」而非「每轮 decide() 入口清零」），**对外行为不变**（RC-1 不变式：降级/异常/取消轮 `last_analysis` 恒为空串，只是把不变式扩展到每次尝试）。

### 4.3 退避与取消

复用 `agent/llm/retry.py::backoff_delay_ms`（纯函数、已被单测覆盖），保持与传输层退避同一套语义；抖动镜像 `retry.py` 的 `random.uniform`：

```python
import random                                     # R-4：抖动源，decider.py 现无此 import，须补
from ..agent.llm.retry import backoff_delay_ms   # 复用既有纯函数（不复制退避公式）

_RETRY_JITTER_MS = 250

async def _retry_backoff(self, attempt: int) -> None:
    """第 attempt 次（1-based）重试前的有界指数退避 + 抖动；可被 cancel / 会话停止中断。"""
    base = backoff_delay_ms(attempt - 1, self.settings.ai_trade_retry_base_ms,
                            self.settings.ai_trade_retry_max_ms)
    await asyncio.sleep((base + random.uniform(0, _RETRY_JITTER_MS)) / 1000.0)
```

- `attempt` 从 1 起（第 1 次重试），传给 `backoff_delay_ms` 的是 `attempt-1`（0-based），使第 1 次重试退避 = `base_ms`，指数递增至 `max_ms` 封顶。
- 单测把 `ai_trade_retry_base_ms=0` / `ai_trade_retry_max_ms=0` 即可让退避 ≈0（仅 ≤250ms 抖动），保证测试快速确定。

### 4.4 认知轨迹（agent_trace）：`retry` 阶段

**问题**：重试会让第 2 次尝试的 `analysis/thinking` 增量继续经 `emit` 累积到**同一轮** `_round_trace` 上（`trace[phase] += delta`），前端 `reduceRoundPhase` 同样是追加语义——不处理则 UI 会把「失败尝试的半截研判 + 成功尝试的研判」拼接呈现，误导用户。

> **R-1 补充**：失败尝试残留的不只是文本。对 `invalid_action`/`missing_size` 这两类可重试失败，其失败尝试**已**在 `_stream_decision` 里经 `pump.mark(name)` 发过 `tool_call_start` → session `_emit` 置 `trace["toolCallPending"]=True`。因此重置集**必须同时清 `toolCallPending`**，否则「正在调用工具」的挂起指示会从失败尝试残留到重试期，与本特性目标矛盾。（`toolCall` 仅由决策后 `_emit_tool_call_trace` 写入、重试期恒为 `None`，无需重置。）

**方案**：新增 `retry` 轨迹阶段。`decider._emit_retry` 在每次重试**前**发一条 `{"phase":"retry","attempt":n,"maxAttempts":m,"reason":degrade}`；`session._make_trace_emit` 收到 `phase=="retry"` 时：① 把 `trace["thinking"]`/`trace["analysis"]` **重置为空串**（丢弃失败尝试的累积）；② `trace["toolCallPending"]` **复位 `False`**（R-1：清失败尝试残留的工具挂起指示）；③ `trace["state"]` 复位 `"deciding"`；④ 累加 `trace` 的重试计数并记 `lastRetryReason`；⑤ 照常 `_push` 出 `agent_trace` 事件。前端 `reduceRoundPhase` 的 `case "retry"` 做**同一套**重置 + 计数，保证快照回放与实时一致。

```python
# decider.py
async def _emit_retry(self, emit, attempt: int, max_retries: int, reason: str) -> None:
    """重试前发一条 retry 轨迹（emit=None 空转，回测/单测零副作用）。"""
    if emit is None:
        return
    await emit({"phase": "retry", "attempt": attempt,
                "maxAttempts": max_retries, "reason": reason})
```

```python
# session.py::_make_trace_emit 内新增分支（保持函数 ≤50 行，必要时抽 _apply_retry_marker 助手）
if phase == "retry":
    data["attempt"] = part.get("attempt")
    data["maxAttempts"] = part.get("maxAttempts")
    data["reason"] = part.get("reason")
    if trace is not None:
        trace["thinking"] = ""
        trace["analysis"] = ""
        trace["toolCallPending"] = False          # R-1：清失败尝试残留的工具挂起指示
        trace["state"] = "deciding"
        trace["retries"] = int(trace.get("retries", 0)) + 1
        trace["lastRetryReason"] = part.get("reason")
```

前端 `store.ts::reduceRoundPhase` 对应新增分支（与后端逐字对齐）：

```ts
case "retry":
  return {
    ...t,
    thinking: "",
    analysis: "",
    toolCallPending: false,            // R-1：与后端一致，清失败尝试残留的工具挂起指示
    state: "deciding",
    retries: (t.retries ?? 0) + 1,
    lastRetryReason: evt.reason ?? null,
  };
```

> `trace.py::PHASES` 追加 `"retry"`（前后端镜像常量），`new_round_trace` 初始化 `"retries": 0, "lastRetryReason": None`。

### 4.5 重试时的提示微调（v2 定调：**保留**，低风险）

> **R-2 定调**：v1 把此增强标为「P2 可选」，却在 §4.2 示例里未传 `hint`、§六接口又带 `hint`，三处矛盾。v2 明确**保留**该增强（提升 `no_tool_call/invalid_action/missing_size` 自愈率、且经 §九「上下文污染」风险项确认零污染），并把 §4.2/§4.5/§六三处**统一**为下述形态；不再作为可删项下传。

对 `no_tool_call`/`invalid_action`/`missing_size` 这类「产出无效」的重试，在**当次重试的** user 消息尾部追加一句一次性提示。抽成模块级纯函数 `_retry_hint_suffix`（供 §4.2 `_attempt_decision` 调用，便于单测与复用）：

```python
def _retry_hint_suffix(reason: str) -> str:
    """重试时追加进瞬时 user_msg 的一次性纠偏提示（不落 _context/_journal）。"""
    return (f"\n\n【系统提示】上一次未产出有效决策（{reason}）。"
            f"请务必**只调用一次** submit_trade_decision 并给出完整合法入参。")
```

- 该提示只进**当次重试的瞬时** `user_msg`（`_stream_decision` 用 `self._context + [user_msg]`，不落 `_context`），`commit_round` 仍以无提示的 `build_decision_prompt(payload)` 写 `_context`/`_journal`——**上下文零污染**。
- `decide()` 循环把上一次的 `degrade` 作为 `hint` 传入下一次 `_attempt_decision`（见 §4.2）；首次调用 `hint=None`，与今日逐字一致。
- 该纯函数应有一条单测（断言输出含 `reason` 且提示「只调用一次」），归入 §八。

### 4.6 降级文案更新

`_emit_degraded` 与前端常驻横幅当前文案暗示「一次超时即降级」，与新行为不符。更新为如实覆盖三类终态：

- 前端 `AiTradeAgentTrace.tsx` 横幅（`session.degraded` 为真时）改为：
  「决策降级观望（多次重试仍失败 / 置信度不足 / 未产出有效决策 → 本轮回落观望，资金安全优先）」。
- `session._emit_degraded` 的 status 文案保持 `f"AI 决策降级为观望（{reason}）：本轮未下单"`（已带具体 `reason`，无需改）。

### 4.7 时序（成功于第 2 次尝试）

```
K线确认 → decide()
  ├─ 尝试#1: stream → 流内 error → _hold("llm_error"), "llm_error"   [可重试]
  ├─ _emit_retry(1,3,"llm_error")  → 前端重置本轮 analysis/thinking，显示「重试中 1/3」
  ├─ _retry_backoff(1) → sleep(base_ms + jitter)   [可被会话停止 cancel]
  ├─ 尝试#2: stream → submit_trade_decision(open_long, 0.4) → 合法
  └─ return (open_long 决策, None)   → 正常执行，无降级横幅
```

失败穷尽（4 次全失败）→ 返回最后一次 `(_hold, "llm_error")` → `session` 照旧 `_emit_degraded` + 横幅。

---

## 五、文件 / 模块变更计划

| 文件 | 变更 | 一句话意图 |
|---|---|---|
| `services/quant/app/config.py` | 改 | 新增 `ai_trade_decision_max_retries`(默认3)、`ai_trade_retry_base_ms`(500)、`ai_trade_retry_max_ms`(8000) 三个 Settings 字段 |
| `services/quant/app/ai_auto/decider.py` | 改 | `import random`（R-4）；抽 `_attempt_decision(*, hint=None)`；`decide` 套重试循环；加 `_RETRYABLE_DEGRADE_REASONS`/`_is_retryable_degrade`/`_retry_backoff`/`_emit_retry`/`_retry_hint_suffix`；导入 `backoff_delay_ms`；`last_analysis` docstring 措辞随清零下沉更新（R-6） |
| `services/quant/app/ai_auto/trace.py` | 改 | `PHASES` 追加 `"retry"`；`new_round_trace` 初始化 `retries=0`/`lastRetryReason=None` |
| `services/quant/app/ai_auto/session.py` | 改 | `_make_trace_emit` 加 `phase=="retry"` 分支：重置本轮 thinking/analysis/**toolCallPending**（R-1）+ 计数 + state 复位 deciding |
| `apps/web/lib/aiTradeTypes.ts` | 改 | `AiTradeTracePhase` 加 `"retry"`；`AiTradeTraceEvent` 加 `attempt?/maxAttempts?/reason?`；`AiTradeRoundTrace` 加 `retries?/lastRetryReason?` |
| `apps/web/lib/store.ts` | 改 | `emptyRoundTrace` 初始化 `retries:0, lastRetryReason:null`（R-7，与后端对称）；`reduceRoundPhase` 加 `case "retry"`：重置 thinking/analysis/**toolCallPending**（R-1）、`retries+1`、`state:"deciding"`、记 `lastRetryReason` |
| `apps/web/components/Agent/AiTradeAgentTrace.tsx` | 改 | 每轮渲染重试面包屑「⚠ 重试中 (n/m)…（reason）」；更新降级横幅静态文案 |
| `services/quant/tests/test_ai_trade_decider.py` | 改 | 新增重试行为单测（见 §七） |
| `apps/web/lib/aiTradeTrace.test.ts` | 改 | 新增 `retry` 阶段重置累积 + 计数的归约单测 |

> **不改**：`backtest.py`（透明复用新 `decide()`，`emit=None` 无面包屑，重试使回测更稳健）；`manager.py`（decider 构造不变）；`decide()` 对外签名与所有既有调用点。

---

## 六、接口设计（Interface Design）

**Python（内部）**
- `AiTradeDecider.decide(payload: dict, *, emit: TraceEmit | None = None) -> tuple[TradeDecision, str | None]` — **签名不变**。
- 新增 `AiTradeDecider._attempt_decision(payload, emit, *, hint: str | None = None) -> tuple[TradeDecision, str | None]`。
- 新增 `AiTradeDecider._retry_backoff(attempt: int) -> None`、`AiTradeDecider._emit_retry(emit, attempt, max_retries, reason) -> None`。
- 新增模块级 `_is_retryable_degrade(reason: str | None) -> bool`、`_retry_hint_suffix(reason: str) -> str`、常量 `_RETRYABLE_DEGRADE_REASONS`。

**WebSocket（agent_trace 事件，新增 phase）**
```jsonc
{ "type": "agent_trace",
  "data": { "phase": "retry", "round": 12, "ts": 1700000000,
            "attempt": 1, "maxAttempts": 3, "reason": "llm_error" } }
```
向后兼容：老前端遇未知 phase 走 `reduceRoundPhase` 的 `default: return t`，忽略即可，不崩。

**配置（env 覆盖，字段名即环境变量名，大小写不敏感）**
- `AI_TRADE_DECISION_MAX_RETRIES`（int，默认 3，`ge=0`）
- `AI_TRADE_RETRY_BASE_MS`（int，默认 500）
- `AI_TRADE_RETRY_MAX_MS`（int，默认 8000）

---

## 七、数据模型（Data Model）

**Settings 新增字段**（`config.py`，置于 `ai_trade_decision_timeout_s` 邻近）
```python
# 单轮决策重试护栏（round-2）：仅对「瞬时 LLM 失败 / 未产出有效决策」类回落重试，穷尽后才降级 hold。
# 语义：首次尝试之外的最多重试次数——默认 3（即最多 4 次 LLM 调用）；0=沿用旧行为「首次失败即降级」。
# genuine 决策（合法动作 / 刻意 hold / low_confidence 风险门禁）不计入、不重试。
ai_trade_decision_max_retries: int = Field(default=3, ge=0)
ai_trade_retry_base_ms: int = 500     # 指数退避基数（复用 agent.llm.retry.backoff_delay_ms 语义）
ai_trade_retry_max_ms: int = 8000     # 单次退避封顶
```

**轨迹形状增量**
- 后端 `trace.PHASES`: `(... , "error", "retry")`。
- 后端 `new_round_trace` 返回 dict 增 `"retries": 0, "lastRetryReason": None`。
- 前端 `AiTradeRoundTrace` 增 `retries?: number`、`lastRetryReason?: string | null`；`emptyRoundTrace` 同时初始化 `retries: 0, lastRetryReason: null`（R-7，与后端 `new_round_trace` 对称）。
- 前端 `AiTradeTraceEvent` 增 `attempt?: number`、`maxAttempts?: number`、`reason?: string`。
- `retry` phase 复位的 `toolCallPending`（后端 dict / 前端 `AiTradeRoundTrace`）为**既有字段**，无形状变更，仅 `retry` 分支把其原地置 `False`（R-1）。
- **复核（R-8）**：`new_round_trace`/`emptyRoundTrace` 的既有单测均**逐字段**断言、快照为裸 dict（无 Pydantic 严格模型），追加上述键**零回归**。

**无 DB 变更**：不新增/修改任何 SQLAlchemy 模型或列，零迁移（对齐 CLAUDE.md 与既有 AI-trade 迭代「零 DB 列迁移」惯例）。

---

## 八、测试与验收（Testing & Acceptance）

**Python 单测**（`services/quant/tests/test_ai_trade_decider.py`，复用 `_ScriptedLLM` 逐脚本弹出的既有桩；每次 `stream()` 调用弹出一条脚本，故消费的脚本数 = LLM 调用次数）：

1. `test_retries_then_succeeds`: 脚本 `[error, error, valid_open_long]`，`max_retries=3, base/max=0` → 结果为 `open_long`、`degrade is None`、消费 3 条脚本。
2. `test_degrades_after_exhausting_retries`: 4 条 `error` 脚本，`max_retries=3` → `degrade=="llm_error"`、动作 `hold`、消费 4 条脚本（1 首次 + 3 重试）。
3. `test_no_retry_on_valid_first_attempt`: 单条 valid → 消费 1 条脚本、`degrade is None`。
4. `test_no_retry_on_low_confidence`: `Settings(ai_trade_min_confidence=0.9)`，脚本 `open_long, confidence=0.5` → `degrade=="low_confidence"`、**消费 1 条脚本**（终态不重试）。
5. `test_retries_on_no_tool_call_then_succeeds`: 脚本 `[text-only, valid]` → 消费 2 条、`degrade is None`。
6. `test_retries_on_missing_size_then_succeeds`: 脚本 `[open_long 无 sizeFraction, valid]` → 消费 2 条、成功。
7. `test_retries_on_timeout_then_succeeds`: **另写有状态 stub**（非 `_ScriptedLLM`——后者事件零耗时，无法制造超时）：首次 `stream()` `await asyncio.sleep(1.0)` 触发 `wait_for` 超时，其后产出 valid。**必须**同时置 `Settings(ai_trade_decision_timeout_s=0.05, ai_trade_retry_base_ms=0, ai_trade_retry_max_ms=0)` 以保证用例**快速且确定**（否则等满 60s / 偶发）→ 成功、`degrade is None`、调用 2 次。
8. `test_cancelled_error_propagates_without_retry_or_degrade`: stream 抛 `asyncio.CancelledError` → `pytest.raises(asyncio.CancelledError)`；断言只尝试 1 次、无降级。
9. `test_max_retries_zero_preserves_legacy_direct_degrade`: `max_retries=0` + 单条 error → 消费 1 条、`degrade=="llm_error"`（旧行为逐字回归）。
10. `test_last_analysis_reset_between_retries`: 首次尝试产半截 `text_delta` 后 error、次次 valid 带新研判 → `d.last_analysis` 仅含**成功尝试**的研判（不含失败尝试残段，RC-1 跨尝试版）。

**TS 单测**（`apps/web/lib/aiTradeTrace.test.ts`）：

11. `retry 阶段重置累积研判并计数`: `round_start → tool_call_start → analysis("A") → retry → analysis("B")` ⇒ `trace.analysis==="B"`（已重置非 `"AB"`）、`trace.toolCallPending===false`（R-1：失败尝试的工具挂起指示已清）、`trace.retries===1`、`trace.lastRetryReason` 记录、`state==="deciding"`。
12. `未知/兼容`: 保持既有 `default` 分支容错测试通过（回归）。
13. `test_retry_hint_suffix_mentions_reason`（Python，§4.5）: `_retry_hint_suffix("missing_size")` 含 `"missing_size"` 且含「只调用一次」，`hint=None` 路径下 prompt 与首次逐字一致（不含系统提示）。

**验收标准（Definition of Done for 后续开发节点）**
- `pytest services/quant/tests/test_ai_trade_decider.py` 全绿；上述 Python 用例（#1–#10 + #13）通过。
- `pnpm --filter web test`（或既有 vitest 命令，`NODE_ENV=test`）中 `aiTradeTrace.test.ts` 全绿。
- 关闭开关（`AI_TRADE_DECISION_MAX_RETRIES=0`）时全部旧单测逐字通过（零回归门禁）。
- 手动/桩验证：LLM 连续瞬时失败时，前端出现「重试中 (n/m)」面包屑，穷尽后才出降级横幅；单次成功不出现任何重试痕迹。

---

## 九、风险与缓解（Risks & Mitigations）

| 风险 | 说明 | 缓解 |
|---|---|---|
| **最坏时延放大** | `(max_retries+1) × timeout + Σ退避`，默认最坏 ≈ `4×60s + <25s ≈ 265s`，可能拖长本轮、致后续 K 线被 `_deciding` 跳过 | 退避封顶 8s；`timeout`/`max_retries` 均可 config 调小；provider 真挂时「拖后降级」正是需求所要（优于秒级放弃）；跳过后续轮是既有行为、不新增故障面。**R-3 锚定**：默认 `max_retries=3` 忠实第 2 轮「重试三次」需求，不下调；短周期（≤1m）下 `4×60s` 可能跨越 1 根 bar，**建议运维**把 `ai_trade_decision_timeout_s` 降至 15–20s 与高 `max_retries` 搭配；全程 **cancel-safe**——STOP/会话停止经 `CancelledError` 立即中断退避与在飞流，不会挂起停机 |
| **与传输层重试叠乘** | `open_stream_with_retry` 对 pre-stream 错误已重试（默认最多 3 次尝试）；叠上决策层重试，纯建连失败最坏 `(1+agent_max_retries)×(1+decision_max_retries)` 次 | 两层均有界且内层快速失败；文档标注；运维可将任一层设 0；决策层是面向用户的主护栏，传输层是内层快速自愈，语义不冲突 |
| **轨迹重复研判** | 重试若不重置，前端累积「失败尝试半截 + 成功尝试」拼接文本 | §4.4 `retry` 阶段前后端双端重置 `thinking/analysis`；单测 #11 守护 |
| **CancelledError 被吞** | 会话停止时若被 `except Exception` 吞并重试，会挂起停机 | `CancelledError` 是 `BaseException` 本就不入 `except Exception`；再显式 `except CancelledError: raise` 双保险；退避 `sleep` 亦随取消抛出；单测 #8 守护 |
| **误重试真实决策** | 把 `low_confidence`/刻意 `hold` 当失败重试，浪费成本且语义错误 | 白名单 `_RETRYABLE_DEGRADE_REASONS` 精确排除；单测 #3/#4 守护 |
| **上下文污染** | 重试提示或重复 user 消息落入 `_context`/`_journal` 破坏滚动摘要 | 重试全程在 `commit_round` **之前**、仅用瞬时 `user_msg`；`commit_round` 用无提示 `build_decision_prompt`；§4.5 明确零污染 |
| **回测变慢** | 回测共用 `decide()`，LLM 抖动时重试拖慢批量回测 | `emit=None` 无面包屑开销；回测本就离线可容忍；如需可在回测配置里把 `max_retries` 调低，属运维旋钮 |
| **文件行数阈值** | `decider.py` 现 267 行，新增逻辑逼近 800 行上限 | 新增约 40-60 行仍远低于 800；`session.py` 若 `_make_trace_emit` 超 50 行则抽 `_apply_retry_marker` 助手 |

---

## 十、附录：实施顺序建议

1. `config.py` 加 3 个字段（无依赖）。
2. `decider.py`：常量 + `_is_retryable_degrade` + `_attempt_decision`（抽取，行为不变）→ 跑既有单测确认零回归 → 再加 `decide` 循环 + `_retry_backoff` + `_emit_retry` + 可选提示。
3. `trace.py` + `session.py`：`retry` 阶段与累积重置。
4. 前端 `aiTradeTypes.ts` → `store.ts` → `AiTradeAgentTrace.tsx`。
5. 补 Python + TS 单测；跑全绿；`max_retries=0` 回归门禁。

> **v2 评审已定（原 v1 留给评审的三项开放问题现全部拍死，不再下传）**：
> 1. **默认重试次数**：`ai_trade_decision_max_retries = 3`（= 首次 + 3 次重试 = 最多 4 次 LLM 调用）——忠实第 2 轮需求「重试三次，之后再降级」。**不**采用「总共 3 次尝试（=设 2）」的解读。
> 2. **§4.5 重试提示**：**保留**（低风险、提升产出无效类的自愈率、上下文零污染），并已把 §4.2/§4.5/§六统一（R-2）。
> 3. **最坏时延**：接受（有界 + cancel-safe），并在 §九给出短周期下降 `timeout` 的运维指引（R-3）。

---

## 实施过程发现的方案缺陷（Issues Found During Implementation · Subtask #2）

> 由「代码开发」节点在落地时记录。均为 §五 文件清单 / §五↔§七 之间的**轻微不完整**，不影响核心契约；
> 已按下述更贴合现有组件结构与「已评审数据模型契约」的方式收敛实现，未偏离方案目标。

- **I-1（§五 前端文件粒度）**：§五把「每轮渲染重试面包屑」记在 `AiTradeAgentTrace.tsx`，但该组件只装配概览条并把**逐轮卡片**委派给既有子组件 `AiTradeTraceRound.tsx`（`traces.map(t => <AiTradeTraceRound trace={t}/>)`）。父组件无法优雅地把面包屑注入卡片内部。**处置**：降级横幅静态文案改在 `AiTradeAgentTrace.tsx`（§4.6，横幅确在此），**逐轮重试面包屑**实现在 `AiTradeTraceRound.tsx`（本就是逐轮渲染者，最贴合「每轮渲染」语义）。即前端实际改动 3 个文件（`aiTradeTypes.ts`/`store.ts`/`AiTradeAgentTrace.tsx` + 新增触及 `AiTradeTraceRound.tsx`），后者是 §五 的隐含遗漏。

- **I-2（§五「(n/m)」↔ §七/§4.4 数据模型不对称）**：§五 面包屑文案写「⚠ 重试中 (n/m)…」需要 `m`(maxAttempts)，但 §七 明确 `AiTradeRoundTrace` 只新增 `retries?/lastRetryReason?`、§4.4 前端 `reduceRoundPhase` 的 `case "retry"` 也只写这两个键——**轮次快照并不持久化 `maxAttempts`**（它只在瞬态 `AiTradeTraceEvent` 上随实时事件下发）。**处置**：为不引入 §七 未评审的新持久字段（避免 scope 蔓延），面包屑改以持久化的重试计数 `n`(=retries) + 上次原因呈现——研判中显示「⚠ 重试中 · 第 n 次…（reason）」、已结算显示「↻ 本轮重试 n 次（reason）」，**省略 m**。用户仍能如实看到「本轮正在/已重试第几次及原因」，满足需求意图；`maxAttempts` 保留在 `AiTradeTraceEvent` 上不删（向后兼容）。TS 用例 #11 仅断言 `retries/lastRetryReason/toolCallPending/analysis/state`，不受影响。

---

## 评审结论（Review Verdict）

**结论：有条件通过（Approved with Conditions）。**

设计方向正确、与代码现状吻合、工程质量高：`decide()` 对外签名零改、零 DB 迁移、`max_retries=0` 逐字回退旧行为，且以白名单精确区分「瞬时可重试失败」与「模型真实终态决策（`low_confidence`/刻意 `hold`）」，忠实实现第 2 轮「先重试三次再降级」的需求。四维评估：**可行性**——所依赖的 `backoff_delay_ms`（0-based）、`_ScriptedLLM`（逐脚本弹出）、`case_sensitive=False` env 覆盖均经真实代码核对成立；**一致性**——满足 CLAUDE.md 的函数尺寸/复杂度阈值、零 DB 迁移惯例、TDD 用例覆盖，且 `AI_TRADE_*` 设置无跨服务镜像（无「三处联动」硬约束）；**完整性 / 合理规模**——补齐了重试期轨迹重置的缺口并把开放决策收敛，未过度设计。

本次评审的 **P1（R-1/R-2/R-3）与 P2（R-4/R-5/R-6/R-7）已全部在 v2 正文就地修复，无悬置 P0/P1**。

「有条件」指下游「代码开发」节点落地时**必须逐字兑现**以下已在文档中写明的验收门禁（否则即为偏离方案）：

1. **零回归门禁**：`AI_TRADE_DECISION_MAX_RETRIES=0` 时全部既有单测逐字通过（§八）。
2. **重试期轨迹重置须含 `toolCallPending`**（R-1）：后端 `session._make_trace_emit` 的 `retry` 分支与前端 `reduceRoundPhase` 的 `case "retry"` 同时重置 `thinking/analysis/toolCallPending` 并复位 `state="deciding"`；TS 用例 #11 守护。
3. **`CancelledError` 直抛不吞不降级**（§4.2 + 单测 #8），保证会话 STOP 即时中断。
4. **上下文零污染**：重试 `hint` 只进瞬时 `user_msg`，`commit_round` 仍写无提示 prompt（§4.5 + §九）。
5. **测试确定性**：超时用例 #7 用有状态 stub + 调小 `ai_trade_decision_timeout_s` + `base/max_ms=0`（R-5）。
6. **默认值锚定**：`ai_trade_decision_max_retries=3`（R-3），不得擅自改解读为「总共 3 次」。

以上条件均已在 v2 正文对应章节给出可执行细节，开发节点照此实现即可满足 Definition of Done。

— Senior Reviewer, Subtask #1（v2）
