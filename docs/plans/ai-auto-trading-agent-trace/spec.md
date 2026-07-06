# AI 自动交易 · Agent 执行轨迹面板（ai-auto-trading-agent-trace）

- **文档版本**：v2（Subtask #1 评审已完成；见文首「评审记录」与文末「评审结论」，所有 P0/P1 已就地修复）
- **所属迭代**：第 4 轮（「开启 AI 自动交易以后，Agent 执行的相关记录 —— 思考过程、工具调用、分析、结果等 —— 都显示在右侧『AI 助手』面板中，美观优雅顶级，符合人机交互最佳实践」）
- **前序基线**：`ai-auto-trading`（v2，已上线 commit `4b13b69`）、`ai-auto-trading-enhancements`（v3/v4，commit `953cd5d`）、`ai-auto-trading-backtest`（commit `b9f1783`）、`ai-auto-trading-backtest-analytics`（commit `603faa1`）
- **变更基调**：纯增量、向后兼容、零 DB 迁移、零新增依赖、不改既有下单/安全链路的任何决定性守卫

---

## 〇、评审记录（Review Notes · Subtask #1）

> 评审方式：逐节对照真实代码核验（可行性 / 完整性 / 一致性 / 规模适配四维）。已核验通过的关键技术断言列在末尾「已验证的正确假设」；下表仅列**问题**。P0=阻断、P1=必须修复、P2=建议。**所有 P0/P1 已在 v2 正文就地修复**（各行「就地修复」列指明落点）。

| # | 严重度 | 维度 | 问题 | 就地修复（v2 落点） |
|---|--------|------|------|---------------------|
| RV-1 | **P1** | 正确性 | **在飞轮次快照回放会重复计字**。原 §3.3 设计把 `_round_trace` **引用**入 `_traces` 并原地 mutate，`snapshot()` 返回 `list(self._traces)`（浅拷贝，dict 仍共享）。WS 连接握手为 `subscribe()`→`snapshot()`→`await send_text(status)`→`json.dumps(snapshot)`；在两次 `send_text` 之间的 `await` 里，决策任务新产出的 `analysis` delta 会**同时**①原地改写被快照引用的 `_round_trace["analysis"]`、②入订阅队列作为 live `agent_trace` 再次下发。重连客户端遂对同一段文字**追加两次**（快照累计文本 + 重放 live delta）→ 在飞轮文本翻倍/错乱。 | §3.3 快照改为**防御性拷贝**冻结在飞轮的累积串；§三-3.3 与 §六 明确「`snapshot()` 深浅拷贝各轮 dict」；§九 T4 增「在飞轮快照后再来 delta 不重复」用例。 |
| RV-2 | **P1** | 完整性/正确性 | **跳过轮的轨迹会用错误轮号覆盖已完成轮**。原 §3.3 步骤 6 说「跳过轮复用 `_emit_decision_skipped` 语义另发一条 `result{skipped}`」，但该路径在 `feed_candle`/`_emit_decision_skipped`（**不在**文件计划 #3 所列的 `_run_decision_round`），且其 `round` 取 `self.rounds_completed`——即**上一已完成轮**的轮号。前端 reducer 按轮号定位并写 `executed`，一条带旧轮号的 `result{skipped}` 会把那条**已完成轮**的真实 `executed` 覆盖成「已跳过」。 | §3.3 步骤 6 改为**默认不为守卫跳过的 K 线发 agent_trace**（在飞轮卡 + 左栏决策日志已表达「AI 忙碌/跳过」）；§3.6 reducer 去掉对跳过态的特判；文件计划 #3 明确本轮**不触碰** `feed_candle`/`_emit_decision_skipped`；§九 T5 相应改写。 |
| RV-3 | **P1** | 一致性/规模 | **头部出现两个同名「AI 自动交易」控件**。`AgentPanel` 头部现已渲染 `AiTradeToggle`（`<span>AI 自动交易</span>` + 开关＝启停会话）。原 §3.7 又在同一头部加一个同样叫「AI 自动交易」的**分段控件**（＝切视图），两个同名控件语义不同，违反 CLAUDE.md §八.1/§八.8（命名自解释、风格一致），且右侧窄面板易拥挤。 | §3.7 分段控件改名为**无歧义的视图名**「对话 / 执行轨迹」，并置于标题行**下方子行**（不与启停开关同行）；§七 同步措辞。 |
| RV-4 | **P1** | 完整性 | **prompt 微调可能抬高「未调用工具→hold」降级率**。原 §3.4 删除既有「不要只回文字」硬约束。默认 provider（KIMI）等较弱模型在被要求「先写分析」后，存在**写完分析却漏掉工具调用**的风险 → `no_tool_call → hold`，即更多**空转不交易**轮（这是超出 R4「时延」的行为回归）。 | §3.4 新措辞**保留**「必须以工具调用收尾、不得只输出分析而不调用工具」的硬约束；§十 新增 R10 风险行，并要求既有决策器测试确认工具调用率不劣化。 |
| RV-5 | P2 | 正确性 | §3.3 步骤 6 的 error phase 需引用 `round_no`，但它是 `try` 内局部变量；`except` 引用前须确保已绑定。 | §3.3 注明「`round_no` 在 `try` 首行即绑定，`except` 可安全引用；异常发生在其后」。 |
| RV-6 | P2 | 一致性 | 文件计划 #5 写 `AiTradeAgentTraceOut`，但 §5.4 只定义 `AiTradeRoundTraceOut`，命名不一致；且该文档镜像运行期不参与序列化。 | 统一为 `AiTradeRoundTraceOut`（沿用 `AiTradeDecisionOut` 既有「文档镜像」范式，保留但仅此一个类，不新增未用类）。 |
| RV-7 | P2 | 正确性 | round_start 载荷含 `consensus`（§5.1），但 `consensus` 在 `_build_decision_payload` 之后才算出并写回 payload；若 round_start 推早于此会缺 consensus。 | §3.3 步骤 1 注明「round_start 必须在 `payload["consensus"]=…` 之后、`decide` 之前推送」。 |
| RV-8 | P2 | 完整性 | `unconfigured`（聊天 Agent 未配置）与 `panelView==="aitrade"` 的优先级未定义：现有 body 只要 `unconfigured` 就渲染 `AgentUnconfigured`。 | §3.7 注明「会话存在（后端 LLM 必已配置）时，aitrade 视图优先于 `unconfigured` 空态渲染」。 |

**已验证的正确假设（评审通过，无需改动）**：①`StreamEvent` 确有 `text_delta`/`thinking_delta` 两种 kind 且 `.text` 字段承载文本（`agent/llm/base.py`）；②`AiTradeDecider._stream_decision` 现确实**只**处理 `tool_use_*`/`error`/`message_done`，`text_delta`/`thinking_delta` 未接分支即被丢弃；③`AnthropicClient._body` **未**发 `thinking` 载荷、OpenAI 侧无 `reasoning` 设置 → `thinking_delta` 实践不触发（R1 成立）；④`WsEvent.type` 为 `string`（`ws.ts`），新增 `"agent_trace"` case 无需改类型；⑤`/ws/ai-trade/{sid}` 处理器以 `json.dumps(event)` **原样**转发队列事件、并原样回放 snapshot（`api/ai_auto.py`，文件计划 #6「不改」成立）；⑥`decide()` 全部调用点（`session.py:327`、`backtest.py:300`、`tests/test_ai_trade_decider.py` 多处）均为**位置调用**，新增 keyword-only `emit=None` 向后兼容、零回归；⑦`store.ts` 有 `clearAiTradeRuntime`、`partialize` 仅含 `aiTradeConfig`（瞬态 `aiTradeTraces` 无需升版）；⑧`lib/aiTrade.ts` 的 `describeRegime/describeConsensus/describeStance/describeExecuted/AiExecTone` 均存在可复用；⑨`schemas.py` 已有 `AiTradeDecisionOut`/`StrategySignalViewOut` 文档镜像先例；⑩`ToolCallCard.tsx`、`AgentMessageBubble`（用 `renderMarkdown`）、`AgentMessageList` 均存在可复用。

---

## 一、Overview（概述）

当前系统已经拥有完整的「AI 自动交易」能力：`AiTradeSession`（后端）以每根已确认 K 线为一个决策轮，装配「多策略影子信号板 + 真实持仓 + 会话盈亏 + 市场体制 + 策略共识」为决策输入，交给 `AiTradeDecider` 调用 LLM，产出一个受约束的 `submit_trade_decision` 工具调用，再经 `LiveExecutor` 守护执行。会话通过 WebSocket 向前端扇出 `status / candle / decision / summary / equity / position / trade / error` 等事件；前端 `useAiTradeStream` 把这些事件路由进全局 store，最终在**左侧** `AiTradePanel`（挂 `WatchlistTab` 左栏）呈现会话概览、信号板、最近决策与滚动摘要。

但是，用户看不到 **AI 究竟是怎么想的**。决策器 `AiTradeDecider._stream_decision` 在流式消费 LLM 输出时，只累积 `tool_use_*`（工具入参）并在 `message_done` 时结束，**把 `text_delta`（模型的自然语言分析）与 `thinking_delta`（扩展思考）直接丢弃**。因此外部唯一可见的产物是每轮最终那条 `decision` 事件（action / reason / confidence / 信号板 / 执行结果）—— 这是一个「结论」，而不是「思考过程」。用户无法在右侧「AI 助手」面板里看到 Agent 的**思考过程、工具调用、分析研判与执行结果**的连续轨迹。第 3 轮曾提出同一诉求但未落地（无 git 提交记录），第 4 轮为其正式设计与实现。

本方案在**不改动任何真实下单/安全守卫**的前提下，补齐「决策认知轨迹」这条数据通路：后端捕获决策器流式输出中的思考/分析文本（并按轮装配为结构化「轮次轨迹」对象），通过既有 AI 自动交易 WS 以一个**新的 `agent_trace` 事件**（按 `phase` 判别）实时扇出，并在 `snapshot` 中携带已装配的历史轮次以支持重连回放；前端在右侧「AI 助手」面板（`AgentPanel`）新增一个「AI 自动交易」分段视图，用**轮次时间线**优雅地呈现每一轮的「输入 → 思考/分析 → 工具调用 → 执行结果 →（到期）上下文摘要刷新」，与既有的交互式对话视图共存、互不干扰。

设计的一个关键洞察决定了实现形态：项目内两个 LLM 客户端（`anthropic_client` / `openai_client`）在构造请求时**均未开启扩展思考**（无 `thinking` / `reasoning_effort` 载荷），所以 `thinking_delta` 在实践中不会触发；Agent 真正可见的「思考/分析」来自 `text_delta`——即模型在调用工具前后写下的自然语言研判。而当前决策系统 prompt 明确写着「不要只回文字」，会抑制模型产出文本。因此本方案包含一处**精确、保持安全契约不变**的 prompt 微调：要求模型「先用 2–4 句话简述综合研判，再调用一次决策工具」。此改动仅影响模型产出的文本内容（让「分析」有真实内容可展示），**不改变**决策 schema、`_map_decision` 的任何决定性守卫，也不改变「超时 / 无有效工具调用 / 入参非法 → 一律 hold」的安全回落。`thinking_delta` 仍被前向兼容地捕获，一旦未来开启扩展思考即自动作为「深度思考」独立呈现。

---

## 二、Goals / Non-Goals（目标与非目标）

### 2.1 Goals

- G1：开启 AI 自动交易（实盘会话）后，右侧「AI 助手」面板可切换到「AI 自动交易」视图，**实时**显示 Agent 每一轮的执行轨迹：思考/分析（流式）、工具调用（`submit_trade_decision`）、执行结果、以及信号板/体制/共识等上下文。
- G2：到达上下文刷新轮数时，把「历史执行摘要」作为轨迹中的一个显著节点呈现，直观体现「上下文清理与纯净」机制。
- G3：页面刷新 / WS 重连后，能通过 `snapshot` 回放最近若干轮的完整轨迹（不丢历史、不重复、不闪烁）。
- G4：视觉美观优雅、顶级，符合人机交互最佳实践（清晰的分段切换、流式打字光标、可折叠工具卡、贴底跟随且尊重用户上滚、无障碍 `aria-live`）。
- G5：对既有交互式对话（`AgentPanel` 聊天）零回归——两套数据流、两个视图、互不干扰。

### 2.2 Non-Goals（本轮不做，见 §十一 Deferred）

- N1：回测模式（`AiBacktestModal`）的 Agent 轨迹——回测已有独立弹窗与决策/摘要时间线，本轮聚焦「开启 AI 自动交易」的实盘会话。
- N2：把轨迹持久化落库或做跨会话历史检索——轨迹为瞬态运行期状态（与既有 `_decisions`/`_equity` 一致），会话终态仅保留 `finalSummary`（已有）。
- N3：把 AI 自动交易的轨迹注入交互式对话消息流（会污染 DB 历史与续流对账）——采用独立视图而非混入聊天。
- N4：让用户在轨迹视图里与自动交易 Agent 对话/下指令——决策由信号板驱动，非交互式。

---

## 三、Technical Design（技术设计）

### 3.1 架构总览与数据流

```
                        ┌─────────────────────────── 后端 services/quant/app ───────────────────────────┐
 OKX WS 确认根 ──feed_candle──► AiTradeSession._run_decision_round(board,i,ref_px,ts)
                                   │  1) 装配 payload（信号板/持仓/体制/共识…）
                                   │  2) _push agent_trace{phase:round_start}          ── 新
                                   │  3) decider.decide(payload, emit=_make_trace_emit(round,ts))
                                   │         └─ _stream_decision 逐事件：
                                   │              text_delta      ─(coalesce)─► emit{phase:analysis, delta}   ── 新
                                   │              thinking_delta  ─(coalesce)─► emit{phase:thinking, delta}   ── 新
                                   │              tool_use_start  ────────────► emit{phase:tool_call_start}   ── 新(可选)
                                   │  4) 共识门禁 → _execute_decision（守护执行，既有链路不变）
                                   │  5) _push agent_trace{phase:tool_call}  + {phase:result}                ── 新
                                   │  6) commit_round → 到期 summarize；_push agent_trace{phase:summary}       ── 新
                                   │  7) _push decision / summary / equity …（既有事件，保持不变）
                                   │  ─ 每轮把装配好的 round-trace 对象写入 self._traces（环形≤20）
                                   ▼
                            snapshot()  ── 追加 agentTraces:[已装配轮次]  （既有 WS 连接即重放）
                                   │
        /ws/ai-trade/{sid}  ◄──────┘  （api/ai_auto.py 无需改动：原样扇出 + 重放 snapshot）
                                   │
 ┌──────────────────────────────── 前端 apps/web ────────────────────────────────┐
 │ useAiTradeStream（page.tsx 全局挂载）                                            │
 │   snapshot.agentTraces ─► store.setAiTradeTraces(list)                          │
 │   agent_trace 事件      ─► store.upsertAiTradeTracePhase(evt)                    │
 │                                   │                                             │
 │ AgentPanel（RightPanel）读取 store.aiTradeSession + aiTradeTraces               │
 │   header 分段控件：[对话] [AI 自动交易 · 第N轮 ●]                                 │
 │     view=chat     → 既有 AgentMessageList（零改动）                              │
 │     view=aitrade  → <AiTradeAgentTrace session traces />（新）：轮次时间线        │
 └────────────────────────────────────────────────────────────────────────────────┘
```

**核心不变量**：真实下单只经 `LiveExecutor.apply`（`_exec_lock` 串行、共识门禁、hold 回落、会话止盈止损、mutual-exclusion）——本方案**只读地**旁路捕获决策器的流式认知输出并新增一条扇出通道，不触碰上述任何写路径与守卫。

### 3.2 后端：决策器捕获流式认知（`decider.py`）

在 `AiTradeDecider.decide` 增加**可选**关键字参数 `emit`（默认 `None`，保证 `ai_auto/backtest.py` 等既有位置调用零改动）：

```python
TraceEmit = Callable[[dict], Awaitable[None]]   # 由 session 注入的 push 闭包

async def decide(self, payload: dict, *, emit: TraceEmit | None = None
                 ) -> tuple[TradeDecision, str | None]:
    user_msg = LlmMessage("user", [TextBlock(build_decision_prompt(payload))])
    try:
        raw = await asyncio.wait_for(
            self._stream_decision(user_msg, emit),           # ← 透传 emit
            timeout=self.settings.ai_trade_decision_timeout_s)
    except asyncio.TimeoutError:
        return _hold("decision_timeout"), "decision_timeout"
    except Exception as exc:                                  # noqa: BLE001
        logger.warning("ai-trade decide failed: %s", exc)
        return _hold("llm_error"), "llm_error"
    return self._map_decision(raw)
```

`_stream_decision` 内用一个**小合并缓冲器** `_TraceCoalescer`（新增到 `ai_auto/trace.py`，见 §3.5）把高频 delta 合并后再 `emit`，避免撑爆 WS 队列（`_push` 满队列会丢帧）：

```python
async def _stream_decision(self, user_msg, emit):
    accs, order = {}, []
    pump = _TraceCoalescer(emit, flush_chars=_TRACE_FLUSH_CHARS)   # emit=None → 空转
    async for ev in self._llm.stream(system=self.system_prompt,
                                     messages=self._context + [user_msg],
                                     tools=[self._tool]):
        if ev.kind == "text_delta":
            await pump.feed("analysis", ev.text or "")
        elif ev.kind == "thinking_delta":
            await pump.feed("thinking", ev.text or "")
        elif ev.kind == "tool_use_start":
            await pump.flush()                                # 文本段结束，先冲刷
            accs[ev.id] = {"name": ev.name or "", "parts": []}; order.append(ev.id)
            await pump.mark(ev.name or "")                    # emit{phase:tool_call_start,name}
        elif ev.kind == "tool_use_args_delta":
            if ev.id in accs: accs[ev.id]["parts"].append(ev.partial_json or "")
        elif ev.kind == "error":
            await pump.flush(); raise RuntimeError(ev.message or ev.code or "LLM_ERROR")
        elif ev.kind == "message_done":
            await pump.flush(); break
    # …（取首条有效 submit_trade_decision 并 json.loads，与既有逻辑逐字不变）
```

要点：
- `emit is None`（回测 / 单测无订阅）时 `_TraceCoalescer` 全程空转，行为与今日逐字一致。
- 合并策略：按 `phase`（thinking / analysis）分别累积；当累计 ≥ `_TRACE_FLUSH_CHARS`（默认 48）、或 `phase` 切换、或遇 `tool_use_start` / `message_done` 时冲刷一条 `{phase, delta}`。整轮单 phase 的累计文本上限 `_TRACE_MAX_CHARS`（默认 4000）后截断（防滥用）。
- `summarize()` 的流式（`_stream_summary`）**不**接 emit：摘要最终文本已有独立事件通路（见 §3.3 summary phase），逐 token 流式非必要，保持改动最小。

### 3.3 后端：会话装配轮次轨迹 + 扇出 + 快照（`session.py`）

新增运行期状态（与既有 `_decisions`/`_equity` 同风格的瞬态环形缓冲）：

```python
_TRACES_MAX = 20                                  # 轮次轨迹环形容量（仅内存/快照回放）

self._traces: deque[dict] = deque(maxlen=_TRACES_MAX)
self._round_trace: dict | None = None             # 当前在飞轮次（原地累积）
```

在 `_run_decision_round` 里织入轨迹（不改既有决策/执行/摘要顺序）：

1. **round_start**（**RV-7：必须在 `payload["consensus"]=self._consensus_wire(consensus)` 之后、`decide` 之前**，否则 round_start 缺 consensus）：`self._round_trace = _new_round_trace(round_no, ts, ref_px, payload)`；`self._traces.append(self._round_trace)`（引用入环、后续原地累积；快照侧由 §3.3 的**防御性拷贝**冻结，见 RV-1，不再依赖「引用天然含在飞轮」这一会导致重放重复的写法）；`_push({"type":"agent_trace","data":{phase:"round_start", ...}})`。载荷含 `lastPx / position / signalBoard / marketContext / consensus`（复用既有 `_view_slim` / `_consensus_wire`）。
2. **thinking / analysis / tool_call_start**：由注入 `decide` 的 `emit=self._make_trace_emit(round_no, ts)` 闭包驱动——闭包把决策器给的 `{phase, delta|name}` 补上 `round/ts`，同时**原地累积**到 `self._round_trace["thinking"|"analysis"]`，再 `_push({"type":"agent_trace","data":{...}})`。
3. **tool_call**（映射+门禁后、执行后）：`_push` `{phase:"tool_call", round, ts, name:"submit_trade_decision", input:{action,sizeFraction,confidence,reason}}`，`input` 取**门禁后**的最终 decision（若被降级为 hold，如实呈现 hold + degradeReason，保证展示与真实执行一致）；同步写 `self._round_trace["toolCall"]`。
4. **result**：`_push` `{phase:"result", round, ts, executed, degradeReason}`；写 `self._round_trace["executed"|"degradeReason"|"state"]`（`state`: skipped→"skipped"，否则"done"）。
5. **summary**（仅当 `commit_round` 返回新摘要）：`_push` `{phase:"summary", round, ts, summary}`；写 `self._round_trace["summary"]`。既有顶层 `summary` 事件仍照发（左栏 `aiTradeSummary` 不受影响）。
6. **error**（轮异常兜底 `except` 分支）：`_push` `{phase:"error", round:round_no, ts, message}`；`state="error"`。**RV-5**：`round_no = self.rounds_completed + 1` 是 `try` 体首行即绑定的局部量，异常只可能发生在其后，故 `except` 引用 `round_no`安全（如需绝对稳妥可将其提到 `try` 之前计算）。
   - **RV-2（跳过轮不发轨迹）**：`_deciding` 守卫触发的跳过发生在 `feed_candle`/`_emit_decision_skipped`（**不在** `_run_decision_round`），且其轮号取 `self.rounds_completed`＝**上一已完成轮**号。若为其补发一条 `result{skipped}`，前端 reducer 会按该轮号定位并把**那条已完成轮**的真实 `executed` 覆盖成「已跳过」——数据错乱。故本轮**默认不为守卫跳过的 K 线发 `agent_trace`**：右栏在飞轮卡本身即表达「AI 正在研判（忙碌）」，左栏决策日志已记录 `decision{skipped:"decision_in_flight"}`，信息不缺失。**本方案因此完全不触碰 `feed_candle`/`_emit_decision_skipped`**（见文件计划 #3 修订）。

`snapshot()` 追加一个键（其余不变）——**RV-1 修复：必须防御性拷贝各轮 dict**，冻结在飞轮的累积 `thinking`/`analysis` 串，避免快照握手 `await` 期间决策任务原地改写导致重连客户端对同段文字二次追加：

```python
def snapshot(self) -> dict:
    # RV-1：在飞轮 _round_trace 是被原地 mutate 的引用；此处对每轮做浅拷贝（顶层键即冻结，
    # thinking/analysis 为不可变 str，reassign 不回灌拷贝；signalBoard 等在 round_start 一次性写入不再变）。
    return {..., "decisions": list(self._decisions),
            "contextSummary": self.decider.summary,
            "agentTraces": [dict(t) for t in self._traces]}   # ← 新增：已装配轮次（含在飞轮次，已冻结）
```

**为何浅拷贝足够**：唯二会在快照后继续变化的字段是 `thinking`/`analysis`，它们是 `str`，mutate 走 `t["analysis"] += delta` 的**键重绑定**，不会回写已拷贝出的 `dict(t)`；其余字段（signalBoard/marketContext/consensus/toolCall/executed/state…）在对应 phase 一次性赋值后不再变。配合 WS 处理器现有的「`subscribe()`→`snapshot()` 之间无 `await`」原子性（`api/ai_auto.py:137-138`），可保证在飞轮的每段增量**恰好下发一次**（快照冻结点之前的入拷贝、之后的入队列 live 重放），不重、不丢。

为满足 CLAUDE.md「单方法 ≤50 行 / 单文件 ≤800 行」，把 `_new_round_trace` / `_make_trace_emit` / `_push_trace(phase, **data)` 等纯装配/推送小工具放入 `ai_auto/trace.py`（§3.5），`session.py` 仅做编排（`session.py` 现 573 行，净增 ≈ 50–70 行仍 <800；如逼近阈值则把轨迹装配整体迁入 `trace.py`，session 持有一个 `TraceRecorder` 实例）。

### 3.4 后端：prompt 微调（`prompts.py`，安全契约不变）

`build_ai_trade_system_prompt` 决策纪律第 1 条改写（其余逐字不变）：

- 原：「**每轮必须且只能调用一次** `submit_trade_decision` 工具提交决策；不要只回文字。」
- 新（**RV-4：保留「不得只回文字」硬约束**，避免弱模型写完分析漏调工具而抬高 `no_tool_call→hold` 空转率）：「**每轮先用 2–4 句话简要说明你的综合研判分析（作为可观测的思考过程），随后必须且只能调用一次** `submit_trade_decision` 工具提交本轮决策。**分析之后必须以工具调用收尾——绝不允许只输出分析文字而不调用决策工具**；分析仅供人类观察，真正生效的只有工具调用。」

> RV-4 说明：新措辞在「让分析可见」与「强制工具收尾」之间取平衡——保留原「不得只回文字」的强制力，仅额外允许简短前导分析。落地时须以既有决策器测试（`tests/test_ai_trade_decider.py` 的桩 stream 用例）确认「有前导 `text_delta` + 随后 `tool_use`」路径仍正确取到工具入参、且 `no_tool_call→hold` 回落率不劣化（见 §九 T1/T2 与 §十 R10）。

安全边界声明（写入方案与代码注释）：该改动**只**改变模型产出的文本内容，使「分析/思考」有真实内容可展示；`DECISION_TOOL_SCHEMA`、`_map_decision` 的动作枚举校验、`sizeFraction`/`confidence` 夹紧、`missing_size`/`low_confidence`/`invalid_action`/`no_tool_call` → hold 回落、共识门禁、`_exec_lock` 串行、会话止盈止损、与 live-auto 的互斥——**全部逐字不变**。多产出的分析文本会略增单轮 LLM 时延，已被 `ai_trade_decision_timeout_s` + hold 回落覆盖，故要求「简要」。

### 3.5 新模块 `ai_auto/trace.py`（保持文件/函数阈值）

集中承载轨迹相关纯逻辑，避免 `session.py`/`decider.py` 膨胀：

- `PHASES`：`round_start / thinking / analysis / tool_call_start / tool_call / result / summary / error` 常量。
- `class _TraceCoalescer`：`feed(phase, text)` / `mark(name)` / `flush()`；持有 `emit`、`flush_chars`、按 phase 的累积缓冲与已发字符计数；`emit is None` 时全空转。
- `new_round_trace(round_no, ts, ref_px, payload) -> dict`：装配在飞轮次初值（thinking/analysis 空串、state="deciding"）。
- `trace_event(phase, round_no, ts, **data) -> dict`：构造 `{"type":"agent_trace","data":{"phase":phase,"round":round_no,"ts":ts,**data}}`。
- 常量：`_TRACE_FLUSH_CHARS=48`、`_TRACE_MAX_CHARS=4000`、`_TRACES_MAX=20`（模块级常量而非 `Settings` 字段——避免 `config.py` 声明 + `desktop/src/lib/config.js` 白名单 + `build-agent-env.mjs` 三处联动的既知易踩点；与 `_DECISIONS_MAX` 等既有常量同风格）。

### 3.6 前端：store 切片与事件路由

`store.ts` 新增瞬态切片（**不**进 `partialize`，刷新由 `snapshot` 重发现，与 `aiTradeDecisions` 一致）：

```ts
aiTradeTraces: AiTradeRoundTrace[];              // 轮次轨迹（环形 ≤ AI_TRADE_TRACES_MAX=20）
setAiTradeTraces: (traces: AiTradeRoundTrace[]) => void;                 // snapshot 全量替换
upsertAiTradeTracePhase: (evt: AiTradeTraceEvent) => void;              // 增量 phase 应用
// clearAiTradeRuntime 追加清空 aiTradeTraces:[]
```

`upsertAiTradeTracePhase` 归约规则（纯函数、幂等、容乱序）：
- `round_start`：按 `round` 去重后新建轮次对象；超环形容量则丢最旧。
- `thinking`/`analysis`：定位该 `round`，追加 `delta`；若该轮不存在（丢过 round_start）则以事件补建桩轮次（鲁棒）。
- `tool_call_start`：置该轮 `toolCallPending=true`（UI 显示「调用决策工具…」）。
- `tool_call`：写 `toolCall`。
- `result`：写 `executed`/`degradeReason`，据 `executed.skipped` 设 `state`。**RV-2**：`result` 恒由 `_run_decision_round` 用**本轮自己的** `round_no` 发出（含停止竞态下 `_execute_decision` 返回的 `skipped:"stopped"` 合法态），与其它轮号不撞车；`_deciding` 守卫的跨轮跳过**不发** `result`（见 §3.3 步骤 6），故不存在「用旧轮号覆盖已完成轮」的路径。
- `summary`：写 `summary`。
- `error`：`state="error"`，写 `errorMessage`。

`useAiTradeStream.ts` 的 `routeEvent`：
- `case "snapshot"`：追加 `setters.setAiTradeTraces(snap.agentTraces ?? [])`。
- `case "agent_trace"`：`setters.upsertAiTradeTracePhase(event.data as AiTradeTraceEvent)`。
- 其余分支不变；`AiTradeSetters` 接口 + hook 依赖数组补两个 setter。

### 3.7 前端：右侧面板分段视图（`AgentPanel.tsx` + 新组件）

`AgentPanel` 改动（聊天逻辑与其 WS 生命周期**零改动**）：
- 从 store 读 `aiTradeSession`、`aiTradeTraces`；本地 `panelView: "chat" | "aitrade"`。
- **RV-3：分段控件避免与既有 `AiTradeToggle` 同名**。头部**标题行右侧已有** `AiTradeToggle`（渲染 `AI 自动交易` 文案 + 启停开关，来自第 1 轮）；本轮新增的是**切视图**的分段控件，故其两段命名为无歧义的**视图名**：`对话` / `执行轨迹`（避免第二个「AI 自动交易」造成语义混淆），并置于**标题行下方的独立子行**（不与启停开关同行，规避右侧窄面板拥挤）。仅当 `aiTradeSession` 存在时显示「执行轨迹」段；运行中在该段上加**脉冲圆点 + 轮次徽标**（`第 N 轮`）。
- `useEffect`：`aiTradeSession?.sessionId` 由无变有时自动切到 `"aitrade"`；会话清空（变 `null`）时回落 `"chat"`。允许用户手动来回切换（自动切换只在「首次出现」触发一次，用 ref 记忆已自动切过的 sessionId，避免与用户手动切换打架）。
- body：`panelView==="aitrade"` → `<AiTradeAgentTrace session={aiTradeSession} traces={aiTradeTraces} />`，并隐藏聊天 `AgentComposer` / 空态 / 错误条；`"chat"` → 现有渲染，逐字不变。
- **RV-8：`unconfigured` 与 aitrade 视图的优先级**。现有 body 只要 `unconfigured`（聊天 Agent 探针 `config.configured===false`）就渲染 `AgentUnconfigured`。规定：**当 `aiTradeSession` 存在时（后端 LLM 必已配置，否则会话无法启动），aitrade 视图优先于 `unconfigured` 空态渲染**——即 `panelView==="aitrade" && aiTradeSession` 时直接渲染轨迹，不因聊天探针未就绪而挡住轨迹；`chat` 视图仍保持 `unconfigured→AgentUnconfigured` 的既有逻辑逐字不变。

新组件 `components/Agent/AiTradeAgentTrace.tsx`（+ 子组件 `AiTradeTraceRound.tsx` 拆分以守 50 行/800 行阈值）：
- **会话概览条**：标的·周期、持仓、累计盈亏（涨绿跌红）、`第N轮 · 距下次摘要M轮`、体制/共识徽标、运行中/已停止、降级提示。复用 `describeRegime/describeConsensus/describeStance/describeExecuted/AiExecTone`（`lib/aiTrade.ts`）与 `Badge`、`fmtMoney/fmtNum/fmtTime`。
- **轮次时间线**（`role="log" aria-live="polite"`，贴底跟随 + 「↓ 回到最新」，镜像 `AgentMessageList` 的 pin 逻辑）。每轮一张卡 `AiTradeTraceRound`：
  1. **轮头**：`第 N 轮 · 时间 · 最新价` + 状态 chip（研判中… / 已决策 / 降级观望 / 已执行 / 跳过 / 异常）。
  2. **思考/分析**：流式文本（`analysis` 为主，`thinking` 若有则折叠为「深度思考」置于其上）。流式中走 `whitespace-pre-wrap` + 打字光标 + 「正在研判…」；轮结束后走 `renderMarkdown` 安全富文本（镜像 `AgentMessageBubble` 的「流式走纯文本、settle 走 markdown」）。
  3. **工具调用卡**：复用 `ToolCallCard` 的视觉语言——`submit_trade_decision` 卡显示动作（开多/开空/平仓/观望）+ 仓位% + 置信% + reason，可展开原始 `input` JSON；`toolCallPending` 时显示「调用决策工具…」占位。
  4. **执行结果卡**：`已开多/已平仓/未成交/跳过/降级` + 结果持仓方向/张数，按 `AiExecTone`（long/short/muted）着色；降级时并显 `degradeReason`（中文化）。
  5. **信号板/体制/共识**：紧凑呈现，复用左栏同款 describe。
  6. **摘要节点**：当该轮 `summary` 非空 → 一条醒目「🔄 上下文已刷新（第N轮摘要）」分隔，可展开摘要全文，直观体现上下文纯净机制（G2）。
- **空态**：会话运行中但 `traces` 为空 → 「AI 正在等待首个确认 K 线…（已暖机 X 根）」。
- **停止态**：会话 `state==="stopped"` → 顶部一条「会话已停止」条，时间线只读保留。

---

## 四、File / Module Change Plan（文件变更计划）

| # | 文件 | 动作 | 意图（一句话） |
|---|------|------|----------------|
| 1 | `services/quant/app/ai_auto/trace.py` | 新增 | `_TraceCoalescer` 合并器 + `new_round_trace`/`trace_event` 装配 + phase 常量与阈值常量，集中轨迹纯逻辑 |
| 2 | `services/quant/app/ai_auto/decider.py` | 改 | `decide(payload,*,emit=None)`；`_stream_decision` 捕获 text/thinking delta 经合并器 emit；取工具入参逻辑逐字不变 |
| 3 | `services/quant/app/ai_auto/session.py` | 改 | 新增 `_traces`/`_round_trace`；**仅在** `_run_decision_round` 织入 round_start/tool_call/result/summary/error phase（**RV-2：不触碰 `feed_candle`/`_emit_decision_skipped`**，守卫跳过不发轨迹）；`snapshot()` 追加 `agentTraces`（**RV-1：`[dict(t) for t in self._traces]` 防御性拷贝**） |
| 4 | `services/quant/app/ai_auto/prompts.py` | 改 | 决策纪律第 1 条：先简述研判分析再调用工具，**保留「不得只回文字、必须以工具收尾」硬约束**（RV-4；安全守卫不变） |
| 5 | `services/quant/app/schemas.py` | 改 | 新增 `AiTradeRoundTraceOut` 单一文档镜像（RV-6：与 §5.4 命名统一，沿用 `AiTradeDecisionOut` 既有范式）；`snapshot.agentTraces` 契约标注（WS 事件仍手工 dict） |
| 6 | `services/quant/app/api/ai_auto.py` | 不改 | WS 已原样扇出 + 重放 snapshot，无需改动（记录于方案以示已核查） |
| 7 | `apps/web/lib/aiTradeTypes.ts` | 改 | 新增 `AiTradeTracePhase`/`AiTradeTraceEvent`/`AiTradeRoundTrace`；`AiTradeSnapshot` 加 `agentTraces?` |
| 8 | `apps/web/lib/useAiTradeStream.ts` | 改 | `routeEvent` 路由 `agent_trace` 与 `snapshot.agentTraces`；`AiTradeSetters` + 依赖数组补两 setter |
| 9 | `apps/web/lib/store.ts` | 改 | 新增瞬态 `aiTradeTraces` + `setAiTradeTraces`/`upsertAiTradeTracePhase`；`clearAiTradeRuntime` 清空；不进 partialize |
| 10 | `apps/web/lib/aiTrade.ts` | 改 | 新增 `describeTraceState`（状态 chip 文案）/ `describeToolCallInput` 等纯 helper（如现有不足） |
| 11 | `apps/web/components/Agent/AgentPanel.tsx` | 改 | header 分段控件 + `panelView` 切换 + 自动切换；body 按视图渲染；聊天路径零改动 |
| 12 | `apps/web/components/Agent/AiTradeAgentTrace.tsx` | 新增 | 轨迹视图：概览条 + 轮次时间线（贴底跟随/aria-live/空态/停止态） |
| 13 | `apps/web/components/Agent/AiTradeTraceRound.tsx` | 新增 | 单轮卡：轮头 + 思考/分析（流式↔markdown）+ 工具调用卡 + 结果卡 + 信号板 + 摘要节点 |
| 14 | `services/quant/tests/test_ai_trade_agent_trace.py` | 新增 | 决策器 emit 合并/空转、会话六 phase 装配、snapshot.agentTraces、降级/跳过路径 |
| 15 | `apps/web/lib/aiTradeTrace.test.ts` | 新增 | `upsertAiTradeTracePhase` 归约（新建/追加/乱序补桩/环形容量/skipped 态） |
| 16 | `apps/web/components/Agent/AiTradeAgentTrace.test.tsx` | 新增 | 渲染轮卡、流式光标、工具卡、结果着色、摘要分隔、空态；`AgentPanel` 分段出现/自动切换/聊天不回归 |

> 注：`ai_auto/backtest.py` 调用 `decider.decide(payload)` 为**位置调用、无 emit**，因 `emit` 为可选默认 `None`，回测路径零改动、零回归（本轮不为回测接线轨迹，见 §十一 N1）。

---

## 五、Interface Design（接口设计）

### 5.1 WS 事件：`agent_trace`（新增，沿用 `{type,data}` 信封）

同一 `/ws/ai-trade/{session_id}` 通道下发；老前端忽略未知 `type`，向后兼容。`data` 按 `phase` 判别：

```jsonc
// phase=round_start —— 本轮 AI 将研判的输入快照
{ "type":"agent_trace", "data":{ "phase":"round_start", "round":12, "ts":1720000000,
  "lastPx":63120.5, "position":{...}|null,
  "signalBoard":[{"strategyId":3,"name":"趋势回踩","stance":"long","latestSignal":"none","strength":0.7,"reason":"…"}],
  "marketContext":{"primary":"trend_up",...}|null, "consensus":{"longVotes":2,...}|null } }

// phase=thinking / analysis —— 合并后的流式增量（前端按 round 追加）
{ "type":"agent_trace", "data":{ "phase":"analysis", "round":12, "ts":1720000000, "delta":"多数策略维持多头立场，" } }

// phase=tool_call_start —— 模型开始调用决策工具（可选占位）
{ "type":"agent_trace", "data":{ "phase":"tool_call_start", "round":12, "ts":1720000000, "name":"submit_trade_decision" } }

// phase=tool_call —— 门禁后最终决策工具入参（与真实执行一致）
{ "type":"agent_trace", "data":{ "phase":"tool_call", "round":12, "ts":1720000000, "name":"submit_trade_decision",
  "input":{"action":"open_long","sizeFraction":0.4,"confidence":0.72,"reason":"…"} } }

// phase=result —— 守护执行结果
{ "type":"agent_trace", "data":{ "phase":"result", "round":12, "ts":1720000000,
  "executed":{"ok":true,"skipped":false,"traded":true,"side":"long","qty":12,"positionAfter":"long","action":"open_long"},
  "degradeReason":null } }

// phase=summary —— 到期上下文刷新（历史执行摘要）
{ "type":"agent_trace", "data":{ "phase":"summary", "round":12, "ts":1720000000, "summary":"截至第12轮：…" } }

// phase=error —— 本轮决策异常（不崩会话）
{ "type":"agent_trace", "data":{ "phase":"error", "round":12, "ts":1720000000, "message":"本轮决策异常，已跳过" } }
```

### 5.2 WS `snapshot`（既有事件，扩展一个键）

```jsonc
{ "type":"snapshot", "data":{ "trades":[...], "equity":[...], "position":{...}|null,
  "status":{...}, "decisions":[...], "contextSummary":"…",
  "agentTraces":[ /* 见 §六 AiTradeRoundTrace，最近 ≤20 轮已装配对象 */ ] } }
```

### 5.3 前端类型（`aiTradeTypes.ts`，camelCase，与 wire 同构）

```ts
export type AiTradeTracePhase =
  | "round_start" | "thinking" | "analysis" | "tool_call_start"
  | "tool_call" | "result" | "summary" | "error";

export interface AiTradeTraceEvent {          // agent_trace 事件 data（各字段按 phase 可选）
  phase: AiTradeTracePhase;
  round: number;
  ts: number;
  delta?: string;                              // thinking / analysis
  name?: string;                               // tool_call_start / tool_call
  input?: { action: AiTradeAction; sizeFraction?: number | null;
            confidence?: number | null; reason: string };   // tool_call
  executed?: AiTradeDecision["executed"];      // result（复用既有 executed 形状）
  degradeReason?: string | null;               // result
  summary?: string;                            // summary
  message?: string;                            // error
  lastPx?: number;                             // round_start
  position?: unknown | null;                   // round_start
  signalBoard?: StrategySignalView[];          // round_start
  marketContext?: MarketContext | null;        // round_start
  consensus?: Consensus | null;                // round_start
}

export interface AiTradeRoundTrace {           // store/snapshot 的已装配轮次
  round: number;
  ts: number;
  lastPx?: number;
  position?: unknown | null;
  signalBoard: StrategySignalView[];
  marketContext?: MarketContext | null;
  consensus?: Consensus | null;
  thinking: string;                            // 累积
  analysis: string;                            // 累积
  toolCallPending?: boolean;
  toolCall?: AiTradeTraceEvent["input"] | null;
  executed?: AiTradeDecision["executed"];
  degradeReason?: string | null;
  summary?: string | null;
  errorMessage?: string | null;
  state: "deciding" | "done" | "skipped" | "error";
}

// AiTradeSnapshot 追加：
export interface AiTradeSnapshot { /* …既有… */ agentTraces?: AiTradeRoundTrace[]; }
```

### 5.4 Python 文档镜像（`schemas.py`，与 `AiTradeDecisionOut` 同风格）

```python
class AiTradeRoundTraceOut(BaseModel):
    """快照 agentTraces 的单轮已装配轨迹（契约/文档镜像；真正 WS 由 session 手工构 dict）。"""
    round: int
    ts: int
    lastPx: float | None = None
    position: dict | None = None
    signalBoard: list[StrategySignalViewOut] = Field(default_factory=list)
    marketContext: dict | None = None
    consensus: dict | None = None
    thinking: str = ""
    analysis: str = ""
    toolCall: dict | None = None
    executed: dict | None = None
    degradeReason: str | None = None
    summary: str | None = None
    state: str = "deciding"     # deciding|done|skipped|error
```

---

## 六、Data Model（数据模型）

- **无 DB 变更、无迁移**。`strategy_runs.mode="ai-trade"` 与既有 `config_json`/`metrics_json` 足够；会话终态仍只落 `finalSummary`（已有）。
- **新增内存态**：`AiTradeSession._traces: deque[dict]`（`maxlen=_TRACES_MAX=20`）+ `_round_trace: dict|None`（在飞轮次，引用入环、原地累积）。与 `_decisions`/`_equity` 同为瞬态运行期缓冲，进程内、随会话结束回收。**RV-1**：内部原地累积不变，但 `snapshot()` 出口对每轮做 `dict(t)` 防御性拷贝（见 §3.3），冻结在飞轮的 `thinking`/`analysis` 累积串，杜绝快照握手 `await` 期间的二次追加。
- **前端态**：`store.aiTradeTraces: AiTradeRoundTrace[]`（瞬态、不 partialize、环形 ≤20），刷新由 `snapshot.agentTraces` 重建。

---

## 七、UI/UX Design（界面与交互，美观优雅顶级）

- **分段切换而非混流**：右侧面板标题行下方子行两段 `对话 / 执行轨迹`（RV-3：与标题行右侧既有「AI 自动交易」启停开关区分命名，避免同名歧义），语义清晰、心智负担低；运行中在「执行轨迹」段加脉冲点 + 轮次徽标，用户一眼知道「Agent 正在工作 + 进行到第几轮」。会话启动自动切入、结束回落对话，且尊重用户手动切换。
- **轮次时间线**：以「轮」为叙事单元，天然对应用户对「每根 K 线一次决策」的心智模型；每轮按「输入 → 思考/分析 → 工具调用 → 结果 →（到期）摘要」自上而下讲一个完整故事。
- **流式打字体验**：思考/分析实时逐段浮现 + 光标 + 「正在研判…」，轮结束转 markdown 富文本；贴底跟随但用户上滚阅读历史时不打断，配「↓ 回到最新」。
- **工具与结果可视化**：复用 `ToolCallCard` 与左栏 `ExecDot`/`AiExecTone` 的既有视觉语言，全站一致；动作/结果按 long(绿)/short(红)/muted(灰) 着色，降级/跳过有明确中文说明，绝不让用户困惑「AI 到底做了没」。
- **上下文纯净可视**：摘要刷新以醒目分隔节点呈现，把「每 N 轮压缩历史、清理上下文」这一后端机制变成用户可感知的信息，呼应第 1 轮需求里的上下文管理设计。
- **无障碍**：时间线 `role="log" aria-live="polite" aria-relevant="additions text"`，屏幕阅读器可播报流式增量（与聊天视图一致）。
- **主题/令牌**：全部使用既有 CSS 变量（`--long/--short/--muted/--accent/--surface/--border/...`），暗色/亮色自适应，零硬编码色值。

---

## 八、Config（配置）

- **不新增 `Settings` 字段 / 不改 `desktop/src/lib/config.js` 白名单 / 不改 `build-agent-env.mjs`**：`_TRACE_FLUSH_CHARS`/`_TRACE_MAX_CHARS`/`_TRACES_MAX` 为 `ai_auto/trace.py` 模块级常量（与 `_DECISIONS_MAX`/`_SNAPSHOT_EQUITY_MAX` 同风格），规避「AI_TRADE_ 前缀 env 三处联动」的既知易踩点。
- **不改 store `partialize` 版本**：`aiTradeTraces` 为瞬态运行期态，不入持久化，无需 store version bump（与既有 aiTrade 运行期态一致）。

---

## 九、Testing & Acceptance Criteria（测试与验收）

### 9.1 后端单测（`tests/test_ai_trade_agent_trace.py`，真实对象 + 桩 LLM 流）
- T1 决策器 emit：桩 `LLMClient.stream` 依次产出 `text_delta*N → tool_use_start → tool_use_args_delta* → message_done`，断言 `emit` 收到合并后的 `analysis` deltas（条数 = 冲刷次数，远少于 token 数）、`tool_call_start`，且拼接文本 = 原始文本。
- T2 空转等价：`emit=None` 时 `decide` 的返回 `(decision, degrade)` 与不接 emit 时逐字一致（保护回测/既有路径零回归）。
- T3 会话六 phase：注入两根确认根，断言 `_push` 依次含 `round_start → analysis* →（tool_call_start）→ tool_call → result`；`tool_call.input` == 门禁后 decision；`result.executed.action` 与执行一致。
- T4 快照回放：`snapshot()["agentTraces"]` 含最近轮（含在飞轮 thinking/analysis 累积）；环形容量 `_TRACES_MAX` 生效（>20 轮丢最旧）。**RV-1 防重复**：取一次 `snapshot()` 冻结在飞轮的 `analysis` 后，再向决策器喂若干 delta，断言快照对象里的 `analysis` **不随后续 delta 变化**（防御性拷贝生效）——即「快照文本 + 后续 live delta」无重叠区间。
- T5 降级（**RV-2：不再断言跳过发轨迹**）：共识门禁把 open_* 降级为 hold 时 `tool_call.input.action=="hold"` 且 `degradeReason` 非空（展示 = 真实执行）；停止竞态下 `_execute_decision` 返回 `skipped:"stopped"` 时该轮 `result.executed.skipped==true` 且键在**本轮自己的** `round_no`（不撞已完成轮）。另加**回归断言**：`_deciding` 守卫跳过 K 线时**不产生** `agent_trace` 事件（仅左栏 `decision{skipped}` 照旧）。
- T6 摘要 phase：到达 `context_refresh_rounds` 时既发顶层 `summary` 也发 `agent_trace{phase:summary}`，文本一致。
- T7 异常兜底：桩 stream 抛错 → 会话发 `agent_trace{phase:error}`、`state=="error"`，会话不崩、下一轮正常。

### 9.2 前端单测
- F1（`aiTradeTrace.test.ts`）：`upsertAiTradeTracePhase` —— round_start 建轮、analysis/thinking 追加、乱序（先到 analysis 后到 round_start）补桩、环形容量、result→state、summary 附着、error→state。
- F2（`AiTradeAgentTrace.test.tsx`）：运行中空态文案；一轮流式（analysis 增量 → 光标）；工具卡展开 input JSON；结果卡着色（long/short/muted）；摘要分隔可展开；停止态只读。
- F3（`AgentPanel` 分段）：无会话时不显示第二段且聊天渲染逐字不变；会话出现→自动切到「AI 自动交易」；手动切回「对话」不被再次自动抢走；`aiTradeSession` 清空→回落对话。

### 9.3 验收标准（Definition of Done for 本功能）
- A1：实盘开启 AI 自动交易后，右侧「AI 助手」面板出现「AI 自动交易」段并自动切入；每轮可见**思考/分析（流式）、工具调用、执行结果**，到期可见摘要刷新节点。
- A2：刷新页面 / WS 重连后，经 `snapshot.agentTraces` 完整回放最近轮次，不丢/不重/不闪。
- A3：交互式对话（聊天）功能零回归；关闭会话后面板回落对话视图。
- A4：`ruff` clean；后端 pytest 全绿（新增用例 + 既有 ai-trade/regime/live-auto 回归）；前端 `vitest` 全绿、`tsc` 干净（除既有无关的 `WeightBatchArena.test.tsx` 预存错误）。
- A5：所有新增/改动文件满足 CLAUDE.md 阈值（文件 ≤800 行、方法 ≤50 行、形参 ≤5、嵌套 ≤4）。

---

## 十、Risks & Mitigations（风险与缓解）

| # | 风险 | 影响 | 缓解 |
|---|------|------|------|
| R1 | 未开启扩展思考，`thinking_delta` 不触发，「思考过程」空 | 需求未达成 | 以 `text_delta`（分析）为主可见内容 + §3.4 prompt 要求先写研判；`thinking` 存在即折叠为「深度思考」，前向兼容 |
| R2 | prompt 让模型先写文本，被误读为放宽安全 | 评审质疑 | 明确声明：仅改文本产出，`_map_decision`/门禁/hold 回落逐字不变；决定性守卫有 T2 等价测试护栏 |
| R3 | 高频 delta 撑爆 WS 队列（`_push` 满队列丢帧） | 轨迹缺字/跳变 | `_TraceCoalescer` 合并（≥48 字符/相变冲刷）+ 轮间隔以分钟计，量级极低；`snapshot` 重连回放兜底 |
| R4 | 多写分析文本增加单轮 LLM 时延，逼近 `decision_timeout` | 偶发降级 hold | prompt 要求「2–4 句、简要」；超时已有 hold 回落，安全优先，如实展示降级 |
| R5 | `session.py` 增行逼近 800 行阈值 | 违反规范 | 轨迹纯逻辑下沉 `ai_auto/trace.py`；必要时以 `TraceRecorder` 聚合，session 仅编排 |
| R6 | `AiTradeAgentTrace` 单文件过大 | 违反规范 | 拆 `AiTradeTraceRound` 子组件；describe 逻辑入 `lib/aiTrade.ts` |
| R7 | 自动切换视图与用户手动切换打架 | 体感差 | 用 ref 记忆「已自动切过的 sessionId」，自动切换每会话仅一次；用户手动切换后不再自动抢 |
| R8 | 轨迹与左栏 `AiTradePanel` 信息重复 | 冗余 | 左栏保持「概览/决策结论」定位，右栏定位「过程/认知轨迹」；二者互补，不改左栏 |
| R9 | 回测路径回归 | 已上线功能受损 | `emit` 可选默认 `None`，回测位置调用零改动；T2 等价测试 + 既有回测测试回归 |
| R10 | prompt 允许前导分析后，弱模型漏调工具 → `no_tool_call→hold` 空转率上升（RV-4） | 交易频次下降/需求打折 | 新措辞**保留**「必须以工具收尾、不得只回文字」硬约束；`no_tool_call` 仍安全回落 hold（不误下单）；以既有决策器桩 stream 测试确认「前导文本 + tool_use」路径取参正确、回落率不劣化 |
| R11 | 在飞轮次快照重放导致文本重复（RV-1） | 重连后轨迹错乱 | `snapshot()` 对每轮 `dict(t)` 防御性拷贝冻结累积串；配合 `subscribe→snapshot` 无 await 原子性，保证每段增量恰好下发一次；T4 防重复用例护栏 |
| R12 | 跳过轮以旧轮号覆盖已完成轮（RV-2） | 已完成轮结果被污染 | 守卫跳过**不发**轨迹（在飞轮卡 + 左栏日志已表达）；`result` 恒用本轮 `round_no`；T5 回归断言护栏 |

---

## 十一、Deferred / Out of Scope（明确不做，附理由）

- N1 回测模式轨迹接线：回测已有 `AiBacktestModal` 的决策/摘要时间线；把 `emit` 接到回测 job WS + 弹窗是独立增量，避免本轮扇出面过大。（未来可复用同一 `_TraceCoalescer` 与 phase 模型。）
- N2 轨迹持久化/历史检索：轨迹为瞬态运行期态；跨会话审计另需 DB 设计，超出「显示在面板」诉求。
- N3 轨迹注入交互式对话流：会污染 DB 历史与续流对账（seq/resume），采用独立视图规避。
- N4 摘要生成逐 token 流式：`summary` phase 已给最终文本；逐 token 流式为可选打磨，非本轮必需。

---

## 十二、评审结论（Review Verdict · Subtask #1）

**结论：有条件通过（approved with conditions）**，文档已就地升级为 **v2**。

### 12.1 总体评价

方案与代码库高度吻合：核心数据通路（决策器捕获 `text_delta`/`thinking_delta` → 合并器 emit → 会话按轮装配 → 新 `agent_trace` WS 事件扇出 → snapshot 回放 → 右栏分段视图）在**现有架构上完全可行**，且忠实遵守「纯增量、零 DB 迁移、零新依赖、不触碰任何下单/安全守卫」的基调。评审逐节核验了 10 条关键技术断言（见文首「已验证的正确假设」），全部成立——尤其 R1（未开启扩展思考、以 `text_delta` 为主可见内容）、文件计划 #6（WS 原样转发无需改动）、`decide()` 全部位置调用向后兼容，这些是方案可行性的基石，均已在真实代码中确认。

评审共发现 **4 个 P1 + 4 个 P2，0 个 P0**。4 个 P1 均为**真实缺陷**（非风格问题），已在 v2 正文就地修复：

- **RV-1（在飞轮快照重放重复计字）**——最实质的正确性缺陷：原「引用入环 + 快照浅拷贝」写法会让重连客户端把在飞轮的同段分析文本追加两次。已改为 `snapshot()` 防御性拷贝并补 T4 防重复用例。
- **RV-2（跳过轮以旧轮号覆盖已完成轮）**——原设计要为 `_deciding` 守卫跳过补发 `result{skipped}`，但其轮号＝上一已完成轮号，会污染前端已完成轮。已改为守卫跳过**不发**轨迹，并把 `feed_candle`/`_emit_decision_skipped` 明确移出改动面。
- **RV-3（头部同名「AI 自动交易」控件）**——与第 1 轮既有 `AiTradeToggle` 撞名。视图分段已改名「对话 / 执行轨迹」并下移子行。
- **RV-4（prompt 去掉防空文守卫抬高空转率）**——新措辞保留「必须以工具收尾、不得只回文字」硬约束，并加 R10 风险与测试要求。

### 12.2 放行条件（Subtask #2 代码开发必须落实）

1. **[RV-1]** `snapshot()` 必须对 `_traces` 逐轮做 `dict(t)` 防御性拷贝；新增「快照冻结后再喂 delta，快照对象 analysis 不变」的测试（T4）。
2. **[RV-2]** `_run_decision_round` 之外的 `feed_candle`/`_emit_decision_skipped` **一律不触碰**、守卫跳过不发 `agent_trace`；`result` 恒用本轮 `round_no`；补「守卫跳过不产生 agent_trace」回归断言（T5）。
3. **[RV-3]** 右栏视图分段命名为「对话 / 执行轨迹」（不得再叫「AI 自动交易」），置于标题行下方子行。
4. **[RV-4]** prompt 决策纪律第 1 条保留「必须以工具调用收尾、不得只输出分析文字」；以既有决策器桩 stream 测试确认「前导 `text_delta` + `tool_use`」取参正确、`no_tool_call→hold` 回落率不劣化。
5. **[RV-7]** round_start 事件必须在 `payload["consensus"]=…` 之后、`decide` 之前推送。
6. **[通用]** 遵守 §九 A4/A5：`ruff` clean、后端 pytest（新增 + ai-trade/regime/live-auto 回归）全绿、前端 `vitest` 全绿 + `tsc` 干净（除既有无关 `WeightBatchArena.test.tsx`）；新增/改动文件满足 CLAUDE.md 阈值（文件 ≤800 行、方法 ≤50 行、形参 ≤5、嵌套 ≤4）。特别注意 `session.py` 现 572 行，织入后逼近阈值——按 §3.3/R5 把轨迹纯逻辑下沉 `ai_auto/trace.py`，必要时以 `TraceRecorder` 聚合。

### 12.3 P2（建议在开发中一并处理，非放行阻断）

RV-5（`round_no` 绑定）、RV-6（schemas 命名统一为 `AiTradeRoundTraceOut`）、RV-7（round_start/consensus 顺序，已并入放行条件 5）、RV-8（`unconfigured` 与 aitrade 视图优先级）——均已在 v2 正文相应小节注明，开发时照做即可。

### 12.4 Sign-off

在 §12.2 的六项条件被 Subtask #2 落实并通过 §九 验收的前提下，本方案**批准进入实现**。除上述条件外，不要求对既有设计做结构性返工——方案的分层、事件模型、回放策略与安全边界处理均已达标。
