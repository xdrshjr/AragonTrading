# Agent Trading Copilot — 韧性流式与体验收尾方案 (spec, iteration 3)

> 文档版本：**v2**（迭代 3 · v1 由 Subtask #0「方案设计」产出；v2 由 Subtask #1「方案评审与修复」就地评审修订：本文顶部已新增「## 评审记录」、文末已新增「## 评审结论」，全部 P0/P1 已就地修复）。
> Feature slug：`agent-copilot-resilient-streaming`
> 作者 / 角色：Solution Architect（Anthropic Engineering）
> 迭代：Iteration 3 / 3（**收官迭代**）
> 全局目标：**完成所有开发，稳健可靠，能够正确进行 Agent 交互。**
> 基线：
> - 迭代 1 已交付并提交（commit `b574d34`）：全栈 Agent Trading Copilot，设计见 `docs/plans/agent-trading-copilot/spec.md`（v2）。
> - 迭代 2 已交付并提交（commit `84cd141`）：稳健性加固（LLM 重试 / 工具入参校验 / 单会话锁 / usage 透传 / 前端重连韧性），设计见 `docs/plans/agent-copilot-hardening/spec.md`（v2）。
>
> **关系声明**：本文档只规划在迭代 1+2 基线之上的「收官」增量——把**前两迭代显式留待后续的最大可靠性缺口（run 与 socket 解耦、网络抖动中续流）补齐**，并兑现原始需求中**「美观、优雅、顶级设计、符合人机交互最佳实践」**里尚未落地的体验项（assistant markdown 富文本至今仍为纯文本渲染）。不重述、不推翻迭代 1+2 的架构、WS/REST 契约、工具集与数据模型。迭代 1+2 的硬约束在本迭代**继续生效、不得回退**：密钥只在后端 / 不入库 / 不进日志、真实 OKX 数据绝不假数据、秒级 UTC ts、双标准 provider 抽象、ui-action 驱动左图、复用 `ReconnectingWS`/`Tabs`/设计 token、零新增三方依赖、CLAUDE.md 阈值（≤800 行 / ≤50 行 / 形参 ≤5 / 圈复杂度 ≤10 / 嵌套 ≤4）。

---

## 评审记录（Review Notes · Subtask #1 · v2）

> 评审人 / 角色：Senior Reviewer（Anthropic Engineering）。评审方法：逐节对 spec 的每条 `file:line` 声明**回到真实源码核验**（`loop.py` / `manager.py` / `api/agent.py` / `config.py` / `AgentMessageBubble.tsx` / `AgentMessageList.tsx` / `AgentPanel.tsx` / `agentReducer.ts` / `agentTypes.ts` / `ws.ts` / `agentWs.ts` / `market_tools.py` / `run_tools.py`），再按**可行性 / 完备性 / 一致性 / 合理规模**四维评估。
>
> **核验结论（事实层）**：spec v1 引用的所有源码锚点**全部准确**——`run(self, user_text, ui, emit)`@`loop.py:115`、helper 行号（128/139/179/245）、`emit` 闭包@`api/agent.py:53-54`、`finally`→`cancel+_drain`@`63-66`、BUSY 局部 `run_task`@`80-81`、`get_or_create` 加锁@`manager.py:52-60`、`AgentMessageBubble.tsx:24,30` 纯文本渲染、`AgentMessageList.tsx:13-15` 每增量贴底、`AgentPanel.tsx:106-117/112/113`、`reduceAgentEvent` 未知 type 走 `default`@`agentReducer.ts:122-124`、`RS-15` 截断点@`market_tools.py:66-67`/`run_tools.py:89-90,112` 均与现码一致。整体方案**可行、规模适当、与既有约定一致**；以下为发现的问题清单（已就地修复 P0/P1）。

| # | 维度 | 严重度 | 位置 | 问题 | 处置 |
|---|---|---|---|---|---|
| RN-1 | 完备性 / 正确性 | **P0** | RS-2 `resume` + §4.4 + `reconcileHistory` | **「run 在宽限窗口内完成」会产生重复气泡**：v1 的 `resume` 只要缓冲非空就重放（含无活跃 run 时），而 `reconcileHistory` 无条件保留在途 uuid 气泡；但若该轮在断连宽限内**已完成**，DB `history` 已权威重建同一轮（DB-int id），重放又把同样内容补完到保留的 uuid 气泡 → **同一回答出现两条气泡**。这直接违背本方案自列的最高正确性目标（R1「重复=最严重正确性风险」）与全局目标「正确进行 Agent 交互」。 | **已修复**：`resume` 改为**仅当 `sess.running` 为真**才重放并续流（`resumed{ok:true}`）；run 已完成 / 已取消 / 不存在一律 `resumed{ok:false, reason}`，前端据此**丢弃**保留的在途 uuid 气泡（DB/history 为权威；`loop.py:147-148` 证实**取消时部分文本亦已落库**，故丢弃无损）。详见 RS-2 / RS-11 / §4.1 / §4.4。 |
| RN-2 | 完备性 / 正确性 | **P1** | RS-7 / RS-1 / R5 | **心跳入重放缓冲会挤占真实事件，宽限内缓冲溢出致重放空洞**：v1 让 `heartbeat` 走 `_emit`→既占 `seq` 又入 `deque(maxlen=512)`；长回测每 20s 一跳叠加快速 `text_delta`，可能把宽限窗口内**尚未重放**的真实事件挤出缓冲，重放出现空洞且续流的 uuid 气泡无法补全。 | **已修复**：`heartbeat` 降为**纯活性帧——不计 `seq`、不入缓冲**（前端用任意入站帧刷新半开计时器，`lastSeq` 只由 run 事件推进）；并显式定义**缓冲空洞兜底**：当 `resume.lastSeq` 落在缓冲保留窗口之前，服务端回 `resumed{ok:false}`，前端丢弃在途气泡并在 `done` 后经**既有 REST** `/agent/conversations/{id}/messages` 拉取权威历史重建（零新端点）。详见 RS-7 / RS-11 / §4.4 / R4-R5。 |
| RN-3 | 合理规模 / 一致性 | **P1** | §3.2 `ws.ts`/`agentWs.ts` 行、RS-12 | **过度修改被 market/paper 复用的共享 `ws.ts`**：`ReconnectingWS` 现已在 `onopen` 调用 `onState?.(true)`（`ws.ts:37-40`），`AgentPanel.onConnState(true)` 早已能感知重连——发 `resume` 无需改动共享层。v1 将 `ws.ts` 列为 [改] 既无必要又给 market/paper 引入回归面。 | **已修复**：把 `ws.ts`/`agentWs.ts` 移出变更集（标注「无需修改」）；`resume` 直接由 `AgentPanel` 现有 `onConnState(true)` 重连分支发出（且仅当本地存在在途 streaming 气泡时才发）。详见 §3.2 / RS-12 / R10。 |
| RN-4 | 完备性 / 健壮性 | **P1** | RS-2 / RS-4 | **会话级 BUSY 判定存在调度时序窗口 + run task 异常未被检索**：`handle` 改为 `create_task(sess.run(...))` 后返回时，`sess.running`（在任务体内置位）可能**瞬时仍为 False**；且正常完成路径无人 `await sess._run_task`，run 抛错会触发 "Task exception was never retrieved"。 | **已修复**：明确 **`run()` 自身的同步 check-and-set BUSY 守卫（`loop.py:116-119`，检查与置位间无 await）才是权威**，WS 层 `sess.running` 仅作 best-effort 快路；为 `_run_task` 挂 `add_done_callback` 检索并记录异常。详见 RS-2 / RS-4 / R-新增。 |
| RN-5 | 一致性（文档） | P2 | §4.1 信封表 | `ready`/`history` 经 `_send`（非 `_emit`）直发，**不会带 `seq`**；「所有 S→C 事件带 seq」表述不准。 | **已修正**：§4.1 澄清 `seq` 仅注入经 `_emit` 下发的 **run 生命周期事件**，连接级 `ready`/`history` 不带 `seq`。 |
| RN-6 | 一致性（文档） | P2 | R12 默认值清单 | R12 列出 `(30/20/512/12/40/2/500/8000)` 与 RS-13 校验集不一致（漏 `drain_timeout=8.0`，多列未校验的 `retry_base/retry_max`）。 | **已修正**：对齐 R12 清单与 RS-13 校验集。 |
| RN-7 | 完备性 | P2 | RS-7 | 心跳协程的**取消时机**未明确（run 结束 / 取消 / shutdown 均须停）。 | **已补**：RS-7 明确心跳任务在 `run()` 的 `finally` 与 `manager.shutdown` 中取消，避免泄漏。 |

---

## 1. Overview（概述）

迭代 1 交付了**功能完整**的对话式 AI 助手，迭代 2 把它**加固到生产级稳健**（传输层有界重试、工具入参边界校验、单会话并发锁、usage 可观测、断连后前端不假死与 DB 权威重建）。但两迭代都**显式地把一个最大的可靠性缺口留给了后续**：当前架构里 **run 与单一 WS socket 强耦合**——`AgentSession.run` 的事件经由"绑定到当前 WS 连接"的 `emit` 闭包逐层透传（`loop.py:115` 的 `run(..., emit)` → `api/agent.py:84-85` 的 `asyncio.create_task(manager.handle(..., emit))`），而 WS 一旦断开，`finally` 即 `manager.cancel()` + `_drain()`（`api/agent.py:63-66`），**正在进行的回答被整体取消**。迭代 2 的 spec 在 H8b「范围说明」中明确写道：「『网络抖动中存活并续流』需把 run 与 socket 解耦 + 事件缓冲重放，属更大重构，明确留待后续迭代」。本收官迭代正是来**完成这件被推迟的开发**——它直接服务全局目标的三个词：**完成所有开发**（补齐被显式推迟的项）、**稳健可靠**（一次 2 秒的网络抖动不再毁掉一整轮昂贵的模型回答）、**正确进行 Agent 交互**（用户的回答不因瞬断而丢失或重复）。

与此同时，原始需求对交互面板提出了硬性的体验要求——**「美观、优雅、顶级设计、符合人机交互最佳实践」**。迭代 1 的 spec 承诺过 assistant 气泡「markdown 轻渲染」，但**实现至今仍是纯文本**：`AgentMessageBubble.tsx:24,30` 用 `whitespace-pre-wrap` 直接渲染 `{message.text}`，代码块、列表、表格、行内代码、粗体一概不成形；`AgentMessageList.tsx:13-15` 在**每一个流式增量**到达时都把视口拽到底部，用户向上翻看历史时会被反复打断；助手消息无一键复制、无可访问性（ARIA live region / 键盘语义）。对一个以"对话"为核心交互的 AI 产品，这些是"顶级设计"与"完成所有开发"都绕不开的收尾项。

因此本迭代以**最小、向后兼容、可逐行实施、零新增依赖**为原则，分四组推进，并严格分级 **P0 / P1 / P2**：**(A) 运行韧性（P0）**——把 run 与 socket 解耦，断连即"脱离（detach）"而非取消，run 在后台带序号缓冲事件，重连进入宽限窗口内即**重放遗漏事件并续流**，仅当宽限耗尽才取消；**(B) 连接活性（P1）**——服务端心跳保活，使长回测 / 远程部署不被中间层 idle 超时切断；**(C) 顶级交互体验收尾（P1）**——无依赖的安全 markdown 富文本渲染 + 一键复制 + 滚动锚定（不打断向上翻看）+ 可访问性 + "续流中"状态文案；**(D) 收官加固（P2）**——配置边界校验、用户输入长度护栏、截断透明化。所有改动为**追加式协议**（事件信封加可选 `seq` 字段、新增可选 `resume`/`resumed`/`heartbeat` 事件），不破坏迭代 1+2 的对外契约现有字段语义、不改 DB schema、不引入三方依赖，并保证两迭代既有的 **151 项后端 + 139 项前端**测试在迁移后保持全绿。

---

## 2. Technical design（技术设计）

> 改动按主题分 A–D 四组。优先级：**P0**（直接服务全局目标、必做）｜**P1**（应做）｜**P2**（加固未来、可裁）。每组先列「现状（已核验事实，file:line）」再列「加固设计」。

### A. 运行韧性：断连即脱离 + 宽限窗口 + 事件重放（P0，本迭代核心）

**现状（已核验）：**
- `loop.py:115` `AgentSession.run(self, user_text, ui, emit)` 把 `emit` 一路透传给每个内部方法（`_run_rounds:128` / `_iterate_tool_rounds:139` / `_stream_assistant_turn:179` / `_execute_tool_calls:245`）。`emit` 是 `api/agent.py:53-54` 里**绑定到当前 WebSocket** 的闭包。
- `api/agent.py:84-85` 在 WS 的接收循环里 `run_task = asyncio.create_task(manager.handle(conversation_id, ..., emit))`——run 的生命周期与**这条**连接耦合；`run_task` 是 `ws_agent` 的局部变量。
- `api/agent.py:63-66`：任何断连（`WebSocketDisconnect`）走 `finally` → `manager.cancel(conversation_id)`（置 `sess._cancel=True`，`loop.py:105-106`）+ `_drain(run_task, ...)`。**结论：瞬断 = 整轮 run 取消，在途回答丢失。** 迭代 2 的 H8a/H8b（`settleStreaming`/`reconcileHistory`，`agentReducer.ts:138-157`）只是把"丢失"处理得不闪烁、可重试，并未让回答"活下来"。
- `api/agent.py:80-81`：BUSY 守卫用**每连接局部** `run_task`，跨重连不可见——重连后旧 run 若仍在跑，新连接的 BUSY 检测不到（潜在缺陷，本组顺带修正）。

**加固设计：**

1. **[P0｜RS-1] 事件经会话级"带序号缓冲 + 可切换 sink"间接发出。** 在 `AgentSession`（`loop.py:82-95`）新增运行态：
   - `self._seq: int = 0`（会话级单调递增，跨重连不重置）。
   - `self._buffer: collections.deque[tuple[int, dict]]`（`maxlen=settings.agent_event_buffer_size`，默认 512；**当前 run 的事件窗口**，新 run 开始时 `clear()`）。
   - `self._sink: Emit | None = None`（当前挂载的 WS 发送闭包；断连即置 `None`）。
   - 新增私有协程 `async def _emit(self, event: dict) -> None`：① `self._seq += 1`；② 给事件信封注入 `seq`（`event["seq"] = self._seq`）；③ `self._buffer.append((self._seq, event))`；④ 若 `self._sink` 非空，`try: await self._sink(event) except Exception: pass`（sink 失效不打断 run，呼应既有 `_send_raw` 容错 `api/agent.py:107-111`）。
   - **改造点**：`run()` 与全部 `_*` helper **移除 `emit` 形参**，一律改调 `self._emit(...)`。`run()` 仍接受一个**可选**初始 sink 以兼容既有单测（`async def run(self, user_text, ui, sink=None)`：进入时 `if sink is not None: self._sink = sink`）。**关键**：helper 必须读"当前" `self._sink`（动态），不可捕获旧闭包——这正是支持"重连时热切换 sink"的前提。
   - 形参收敛：`ToolContext` 注入不变；`_emit` 是会话方法、零额外形参，守住 CLAUDE.md 形参 ≤5 / 函数 ≤50 行阈值。

2. **[P0｜RS-2] AgentManager 提供 attach / detach / resume，run 归属会话而非连接。**
   - `manager.handle`（`manager.py:68-78`）改为**在会话上启动并持有** run task：`sess._run_task = asyncio.create_task(sess.run(user_text, ui))`（task 句柄存于会话，跨连接存活）。`handle` 不再接收 `emit`。
   - **BUSY 权威与异常检索（RN-4）**：`run()` 自身的同步 check-and-set 守卫（`loop.py:116-119`：`if self._running: …; return` 与 `self._running = True` 之间**无 `await`**，故并发任务不会交错穿过）才是 BUSY 的**唯一权威**；WS 端点的 `if sess.running`（RS-4）仅作 best-effort 快路（`create_task` 后 `running` 可能瞬时仍为 False，但纵使漏判，`run()` 内层守卫仍会回 BUSY，行为正确）。`handle` 须给 `sess._run_task` 挂 `add_done_callback`：在回调里 `task.result()` 检索并 `logger` 记录后台 run 的未处理异常（正常完成无人 `await` 时也不留 "Task exception was never retrieved" 噪音）。
   - 新增 `async def attach(self, conv_id, sink) -> AgentSession`：`get_or_create`（已加锁 `manager.py:52-60`）后 `sess.set_sink(sink)`，并**取消该会话待决的宽限计时器**（见 RS-3）。返回会话，供 WS 端点查询 `sess.running` 做 BUSY 判定与按需 `resume`。
   - 新增 `async def detach(self, conv_id, sink) -> None`：仅当 `sess._sink is sink`（避免误摘新连接的 sink）时置 `sess._sink=None` 并启动宽限计时器（RS-3）。
   - 新增 `async def resume(self, conv_id, last_seq, sink) -> None`（**RN-1 修订：续流与"权威历史"严格分工，杜绝重复气泡**）：
     - **仅当 `sess.running` 为真**（run 仍在跑）才续流：先 `set_sink(sink)`，再把 `sess._buffer` 中 `seq > last_seq` 的事件**按序重放**到 `sink`，重放后无缝转入实时；发 `resumed{ok:true, fromSeq:last_seq}`。
     - **缓冲空洞兜底（RN-2）**：若 `last_seq` 落在缓冲保留窗口之前（即 `last_seq < 最旧缓冲 seq - 1`，宽限内事件已被淘汰，无法无损续流），不强行重放半截，直接发 `resumed{ok:false, reason:"gap"}`。
     - **run 已终结 / 不存在**：`sess` 无活跃 run（该轮在宽限内**已自然完成**或已被取消，或会话不存在）→ 发 `resumed{ok:false, reason:"completed"|"cancelled"|"none"}`，**不重放**。
     - **关键正确性论证**：助手轮无论自然完成还是中途取消，其文本都已逐轮落库（`loop.py:147-148` 在流结束后 `_append_and_persist`，取消亦走此路径）。因此 run 终结后 DB `history` 即权威且完整——此时**绝不**用缓冲重放去补保留的在途 uuid 气泡（否则与 history 重建的同一轮 DB-int 气泡**重复**），而是令前端**丢弃**在途 uuid 气泡、单以 history 为准（见 RS-11）。"重放续流"仅服务"run 仍在跑"这一种情形。
   - `shutdown`（`manager.py:95-98`）一并取消所有会话的 `_run_task` 与宽限计时器。

3. **[P0｜RS-3] 宽限窗口：断连不取消，超时才取消。** `AgentManager` 维护 `self._grace_timers: dict[str, asyncio.TimerHandle | asyncio.Task]`。`detach` 时若 `sess.running`，起一个 `agent_detach_grace_seconds`（默认 30.0）的延时任务：到点仍未 `attach`（未被取消）→ `sess.cancel()` + 落盘已完成轮（既有 `_append_and_persist` 已逐轮持久化 `loop.py:275-280`）+ 记 `logger.info("agent run cancelled after detach grace", ...)`（仅元数据，C7）。`attach` 命中即 `cancel()` 该计时器。
   - **优雅降级（关键安全阀）**：`agent_detach_grace_seconds = 0` 时，`detach` **立即**取消——**行为与迭代 2 完全一致**。该配置即"一键回退"开关，把本组从"新行为"降级为"旧行为"，是规避回归风险的兜底。
   - **退避墙钟边界**：宽限计时用 `asyncio` 延时任务，可被 `attach`/`shutdown` 取消；不与 LLM `agent_timeout_seconds=120`（`config.py:69`）冲突——run 自身的总超时仍由 LLM 客户端与既有 drain 兜底。

4. **[P0｜RS-4] WS 端点改造（`api/agent.py`）：连接即 attach、断连即 detach、收 resume 即重放。**
   - `ws_agent`（`api/agent.py:45-66`）：`accept` 后仍先发 `ready` + `history`（`:49-51`，DB 权威历史不变）；随后 `sess = await manager.attach(conversation_id, emit)`（注册 sink）。
   - 接收循环 `_dispatch`（`:69-86`）：
     - `user_message`：BUSY 判定由"局部 `run_task`"改为**会话级** `if sess.running`（`loop.py:97-99`）——跨重连正确生效（顺带修正现状缺陷）；非 BUSY 则 `await manager.handle(...)`（会话内起 task）。
     - 新增 `resume`：`await manager.resume(conversation_id, data["lastSeq"], emit)`。
     - `cancel`：不变（`manager.cancel`）。
   - `finally`（`:63-66`）：由 `cancel + _drain` 改为 **`await manager.detach(conversation_id, emit)`**——脱离而非取消。`_drain`（`:89-100`）保留，移作**宽限超时取消路径**的兜底（detach 计时器到点取消 run 后，仍用有界 `wait_for` 收束，永不挂死，承袭 H4）。
   - **孤儿防护**：宽限窗口本身即"用户真的离开"的判定——30s 内无任何重连即取消，配额不会无限烧；`shutdown` 在进程退出时兜底清场。

5. **[P0｜RS-5] 时序（用户回答流式中途发生 2 秒网络抖动）：**
   ```text
   run 进行中 → _emit(text_delta seq=41..58) 缓冲并发往 sink_A
   ── 网络抖动：sink_A 所在 WS onclose ──
   api/agent.py finally → manager.detach(conv, sink_A) → sess._sink=None, 起 30s 宽限计时器
   run 不取消：继续 _emit(text_delta seq=59..77) 仅入 buffer（无 sink，静默续算）
   ── 2s 后 ReconnectingWS 重连成功（ws.ts:37-40 onopen）──
   新 WS：server 发 ready+history → 前端 reconcileHistory 保留在途 streaming 气泡
   server attach(conv, sink_B) → 取消宽限计时器
   前端 onConnState(true) 后发 resume{lastSeq:58}
   server resume → 重放 buffer[seq>58]=59..77 到 sink_B → resumed{ok:true} → 转实时续流
   run 继续 _emit(...message_end, done) → 前端正常收束
   结果：用户仅见短暂"重连中（回答已保留）"，回答无丢失、无重复、无重启。
   ```

### B. 连接活性：服务端心跳保活（P1）

**现状（已核验）：** 迭代 1 P2-6 / 迭代 2 RN-9 均把"长回测期间 WS 不被中间层 idle 超时断开"列为 P2 未落地。当前 WS 在工具执行（线程池回测可达分钟级，`services.py:108-208`）期间**无任何下行帧**；远程 / 反代部署下易被 idle 超时切断，触发不必要的 detach/宽限。

**加固设计：**

6. **[P1｜RS-7] 服务端周期心跳（RN-2 修订：心跳为纯活性帧，不入缓冲、不占 `seq`）。** `AgentSession` 在 run 期间起一个 `asyncio` 心跳协程，每 `agent_heartbeat_seconds`（默认 20.0，须 < 常见 idle 超时）**直接经当前 `self._sink` 下发** `{"type":"heartbeat","data":{}}`（**绕过 `_seq`/`_buffer`**——心跳不参与重放对账，故**不消耗序号、不挤占重放缓冲**；`sink` 为空即跳过本次心跳）。前端把 `heartbeat` 仅用于刷新"半开检测"计时器与活性指示，**不入消息流、不推进 `lastSeq`**（`lastSeq` 只由真正的 run 事件推进）。**取消时机（RN-7）**：心跳任务句柄存于会话，在 `run()` 的 `finally`、`sess.cancel()`、`manager.shutdown` 三处均显式取消，杜绝泄漏。老前端忽略未知 `heartbeat` 类型（既有 reducer 白名单分派 `agentReducer.ts:102-125` 对未知 type 走 `default` 原样返回，不崩）。心跳同时让前端能识别"半开 socket"（一定时间收不到心跳即主动重连），与 A 组宽限/重放形成闭环。

   > **为何心跳不入缓冲**：若心跳占 `seq` 并入 `deque(maxlen=512)`，长回测期间（每 20s 一跳）叠加快速 `text_delta`，宽限窗口内**尚未重放**的真实事件可能被挤出缓冲，导致重放空洞、续流气泡缺字。剥离心跳后，缓冲只承载当前 run 的真实 run 事件，`agent_event_buffer_size` 与宽限窗口的关系更可控（见 §4.3 与 R4）。

### C. 顶级交互体验收尾（P1，兑现「美观、优雅、顶级设计」）

**现状（已核验）：**
- `AgentMessageBubble.tsx:24,30`：assistant 文本以 `whitespace-pre-wrap` **纯文本**渲染——代码块 / 列表 / 表格 / 行内代码 / 粗体 / 链接均不成形，与"顶级 AI 对话产品"体验差距明显；迭代 1 spec 承诺的"markdown 轻渲染"**未兑现**。
- `AgentMessageList.tsx:13-15`：`signature` 每变（每个 `text_delta`）即 `scrollIntoView({block:"end"})`——用户向上翻看历史时被**反复拽回底部**，违背 HCI 最佳实践（应"贴底跟随，但用户上滚即让出控制权"）。
- 无助手消息 / 代码块**一键复制**；无 ARIA live region / 角色语义（屏幕阅读器无法播报流式增量）。
- A 组引入"续流"后，迭代 2 的瞬断文案"连接中断，正在重连…"（`AgentPanel.tsx:112`）+ 立即 `settleStreaming`（`:113`）**语义已过时**——回答其实没断，气泡不该立刻标"已中断"。

**加固设计：**

7. **[P1｜RS-8] 无依赖、安全的 markdown 轻渲染。** 新增纯函数模块 `lib/markdown.ts`，把 assistant 文本解析为**最小富文本子集**：围栏代码块 ```` ``` ````（等宽、横向滚动、复用 `--surface-2`/`--border`）、行内代码 `` ` ``、粗体 `**`、斜体 `*`、无序 / 有序列表、标题 `#`、链接 `[t](url)`（仅 `http(s)`，`rel="noopener noreferrer" target="_blank"`）、段落与换行。**安全铁律**：先对原文 HTML 转义（`& < > " '`），再施加格式化——**绝不渲染原始 HTML**，杜绝 XSS（模型输出即不可信输入，对齐 CLAUDE.md §五"外部输入边界校验"）。渲染产物为 React 元素树（不用 `dangerouslySetInnerHTML`）。解析器拆为小函数（块级扫描 + 行内扫描），守住 ≤50 行 / 圈复杂度 ≤10。`AgentMessageBubble.tsx` 仅 assistant 气泡走 markdown（`isUser` 仍纯文本）；流式途中可降级为纯文本、`message_end` 后再 markdown 化（避免半截围栏闪烁），或对未闭合围栏做容错收尾。**零新增三方依赖**——自研最小渲染器与既有"裸 httpx、不引 SDK"风格一致。

8. **[P1｜RS-9] 滚动锚定（贴底跟随但尊重用户上滚）。** 改 `AgentMessageList.tsx`：维护 `isPinnedToBottom`（监听容器 `scroll`，距底 < ~48px 即 pinned）；仅当 `pinned` 才在 `signature` 变化时 `scrollIntoView`。用户上滚即 `pinned=false`、停止自动跟随；右下角浮出"↓ 回到最新"按钮（点按回到底并重新 pinned）。复用设计 token（`--surface-2`/`--border`/`--accent`），紧凑、暗/亮自适应。

9. **[P1｜RS-10] 一键复制 + 可访问性。** assistant 气泡（及代码块）悬浮"复制"按钮（`navigator.clipboard.writeText`，复制原始 markdown 文本而非渲染后 DOM）；复制后 1.5s 内显示"已复制"反馈。消息流容器加 `role="log" aria-live="polite" aria-relevant="additions text"`，使屏幕阅读器播报流式增量；composer 与按钮补 `aria-label`；保证 Tab 焦点顺序合理（沿用既有 `Button`/`AgentComposer`）。

10. **[P1｜RS-11] "续流"感知的连接状态文案 + 在途气泡收束/丢弃判定（RN-1/RN-2 修订）。** 对齐 A 组：`AgentPanel.tsx` 的 `onConnState`（`:106-117`）瞬断（true→false）时**不再立即 `settleStreaming`**（`:113`），而是：若本地有在途 `streaming` 气泡，仅置一条轻状态 `"连接中断，正在重连…（回答已保留）"`并**保留**该气泡的流式态；重连成功后**仅当本地确有在途 streaming 气泡**才发 `resume{lastSeq}`（见 RS-12；无在途气泡则不发，避免协议噪音）。收到 server 回应后按 `resumed` 分流：
    - `resumed{ok:true}`：run 仍在跑，重放已补齐遗漏增量并转实时续流——**保留并继续**该在途 uuid 气泡。
    - `resumed{ok:false, reason:"completed"|"cancelled"}`：run 已在宽限内终结，DB `history`（本次重连已 `reconcileHistory` 重建）即权威且完整——**丢弃**保留的在途 uuid 气泡（**而非 `settleStreaming` 标"已中断"**：标中断会与 history 重建的同一轮 DB-int 气泡**重复**，正是 RN-1 要消除的缺陷；取消时部分文本亦已落库，丢弃无损）。
    - `resumed{ok:false, reason:"gap"}`：缓冲已淘汰待重放事件、无法无损续流——**丢弃**在途 uuid 气泡，并在该会话下一个 `done`（或若已不在跑则立即）经**既有 REST** `/agent/conversations/{id}/messages` 拉取权威历史 `reconcileHistory` 重建（零新端点，最终一致）。
    - `resumed{ok:false, reason:"none"}`（会话不存在等极端态）：等价 `cancelled` 处置（丢弃，history 为准）。

    即把迭代 2 的"瞬断即收束"升级为"瞬断先保留、按 server 终态决定续流/丢弃"；**DB 始终是最终权威，在途 uuid 气泡仅作 best-effort 实时续流载体**。

### D. 收官加固（P2）

11. **[P2｜RS-13] 配置边界校验。** `config.py` 给新增与既有数值字段加 Pydantic `@field_validator`（或 `Field(ge=…, le=…)`）：`agent_max_retries ≥ 0`、`agent_drain_timeout_seconds > 0`、`agent_detach_grace_seconds ≥ 0`、`agent_heartbeat_seconds > 0`、`agent_event_buffer_size ≥ 64`、`agent_max_tool_rounds ≥ 1`、`agent_max_context_messages ≥ 2`。防止用户在 `.env` 误填 0 / 负值导致脱裤子放屁的运行期故障。仅边界校验、不改语义、不破坏既有默认值。
12. **[P2｜RS-14] 用户输入长度护栏。** `AgentPanel.send`（`AgentPanel.tsx:135-162`）发送前对 `trimmed` 做上限（默认 16k 字符）软校验，超限提示"消息过长，请精简"，避免单条用户输入直接撑爆模型上下文；后端 `manager.handle` 同步做一次防御性截断 + 记日志（边界一次性校验，CLAUDE.md §五）。
13. **[P2｜RS-15] 截断透明化。** Explore 审计指出列表 / 序列工具（`market_tools.py:66-67`、`run_tools.py:89-90,112`）截断 / 下采样时 summary 表述不一。统一在 payload 增 `truncated: bool` 与 `totalCount?`，并在 summary 末尾固定追加"（已截断，共 N 条，仅返回前 M / 下采样 K 点）"，让模型与用户都明确知道数据被裁剪，避免据残缺数据下错结论（对齐"正确进行 Agent 交互"）。

---

## 3. File / module change plan（文件 / 模块变更计划）

> 约定：修改标 **[改]**，新建标 **[新]**。后端 PEP8 + Google docstring；前端 camelCase。单文件 ≤800 行、单函数 ≤50 行、形参 ≤5、嵌套 ≤4、圈复杂度 ≤10。**全部改动向后兼容**，不动既有 WS/REST 契约现有字段语义、不改 DB schema、零新增三方依赖。

### 3.1 后端（services/quant）

| 文件 | 类型 | 一句话意图 | 关联项 |
|---|---|---|---|
| `app/config.py` | [改] | 新增 `agent_detach_grace_seconds:float=30.0` / `agent_heartbeat_seconds:float=20.0` / `agent_event_buffer_size:int=512`（字段名即 `AGENT_*` 环境变量名）；为数值字段加边界 `@field_validator` | RS-3/RS-7/RS-1/RS-13 |
| `app/agent/loop.py` | [改] | `AgentSession` 增 `_seq`/`_buffer`/`_sink`/`_run_task`/心跳协程与 `_emit`/`set_sink`/`replay_from`；`run` 及 helper 去 `emit` 形参改用 `self._emit`（动态读 sink）；run 期起停心跳 | RS-1/RS-2/RS-7 |
| `app/agent/manager.py` | [改] | 新增 `attach`/`detach`/`resume`/宽限计时器字典；`handle` 在会话上起持有式 run task（不再透传 emit）；`shutdown` 兜底清计时器 | RS-2/RS-3 |
| `app/api/agent.py` | [改] | 连接 `attach`、`finally` 改 `detach`（非 cancel）、新增 `resume` 分发、BUSY 改会话级 `sess.running`；`_drain` 移作宽限取消路径兜底 | RS-2/RS-4 |
| `app/agent/tools/market_tools.py` | [改] | 截断时 payload 增 `truncated`/`totalCount` + summary 统一文案 | RS-15 |
| `app/agent/tools/run_tools.py` | [改] | 同上（trades 截断 / equity 下采样透明化） | RS-15 |
| `app/agent/__init__.py` | [改] | 如导出新异常 / 类型则补（保持聚焦） | RS-1 |

### 3.2 前端（apps/web）

| 文件 | 类型 | 一句话意图 | 关联项 |
|---|---|---|---|
| `lib/markdown.ts` | [新] | 无依赖、先转义后格式化的安全 markdown→React 元素渲染（代码块 / 行内代码 / 粗斜体 / 列表 / 标题 / 安全链接） | RS-8 |
| `components/Agent/AgentMessageBubble.tsx` | [改] | assistant 气泡走 `lib/markdown` 渲染（user 仍纯文本）；悬浮"复制"按钮（复制原始 markdown） | RS-8/RS-10 |
| `components/Agent/AgentMessageList.tsx` | [改] | 贴底跟随但尊重用户上滚（`isPinnedToBottom`）；"↓ 回到最新"浮钮；容器加 `role="log" aria-live` | RS-9/RS-10 |
| `components/Agent/AgentPanel.tsx` | [改] | `onConnState` 升级为"续流感知"（瞬断先保留在途气泡 + 文案"回答已保留"）；**复用现有 `onConnState(true)` 重连分支**发 `resume{lastSeq}`（仅当本地有在途 streaming 气泡）；处理 `resumed`(按 reason 续流/丢弃 + gap 时 REST 兜底)/`heartbeat`(刷活性)；维护 `lastSeqRef`、`lastUserText`/会话 id 供 REST 兜底 | RS-11/RS-12 |
| `lib/agentTypes.ts` | [改] | 事件信封增可选 `seq`；新增 `resume`(C→S `{lastSeq}`)/`resumed`(S→C `{ok, reason?, fromSeq?}`)/`heartbeat`(S→C) 事件类型 | RS-1/RS-4/RS-7 |
| `lib/agentReducer.ts` | [改] | `reduceAgentEvent` 对 `heartbeat` 走 `default` 原样返回；新增 `dropStreaming(state)` 纯函数（移除在途 streaming uuid 气泡，供 `resumed{ok:false}` 用，**区别于 `settleStreaming` 的"标中断保留"**，杜绝与 history 重复，RN-1）；`reconcileHistory`/`settleStreaming` 保持，仅由 `AgentPanel` 按 `resumed.reason` 调度 | RS-11 |
| ~~`lib/agentWs.ts` / `lib/ws.ts`~~ | **无需修改（RN-3）** | `ReconnectingWS` 现已在 `onopen` 调用 `onState?.(true)`（`ws.ts:37-40`）、`agentWS` 已转发该回调（`agentWs.ts:4-14`）——`AgentPanel.onConnState(true)` 早已能感知重连，发 `resume` 无需触碰被 market/paper 复用的共享层，避免无谓回归面 | RS-12/R10 |

### 3.3 配置 / 文档

| 文件 | 类型 | 一句话意图 |
|---|---|---|
| `.env.example` | [改] | Agent 段补注释：`AGENT_DETACH_GRACE_SECONDS` / `AGENT_HEARTBEAT_SECONDS` / `AGENT_EVENT_BUFFER_SIZE`（占位 + 默认值说明，**不写真实密钥**；不存在则新建 Agent 段仅占位值） |
| `desktop/scripts/build-agent-env.mjs` | [改] | 把 3 个新键追加进 `AGENT_KEYS` 白名单（承袭迭代 2 IMPL-1：缺省即不写、回落 `config.py` 默认，零破坏既有打包） |

> **不改动项（明确边界）**：不新增 DB 表 / 列（§5）；不改 WS 事件**类型集语义**（仅给信封增可选 `seq`，新增 `resume`/`resumed`/`heartbeat` 三个**追加**事件，老前端忽略）；不引入新三方依赖（`asyncio`/`deque` 标准库 + 自研 markdown 渲染）；REST 契约零变更；不改 `agent/llm/*` 重试与 usage 链路、不改工具集与回测逻辑（仅工具截断文案透明化）。

---

## 4. Interface design（接口设计变更）

**全部为追加式，向后兼容。** 既有 `{type,data}` 信封不变，新增**可选** `seq` 顶层字段。

### 4.1 WS 事件（`/api/ws/agent/{convId}`）增量

**信封增量（RN-5 澄清）**：`seq:int` **仅注入经 `self._emit` 下发的 run 生命周期事件**（`message_start`/`text_delta`/`thinking_delta`/`tool_call`/`tool_result`/`ui_action`/`message_end`/`done`/`error`）；**连接级 `ready`/`history`（经 `_send` 直发）与纯活性 `heartbeat` 均不带 `seq`**。老前端忽略未知顶层字段。

| 方向 | type | data | 变更 |
|---|---|---|---|
| C→S | `resume` | `{lastSeq:int}` | **新增**：重连后请求重放 `seq>lastSeq` 的缓冲事件并续流（`lastSeq=0` 即"给我缓冲区全部"）；**仅当本地有在途 streaming 气泡时才发**（RS-11） |
| S→C | `resumed` | `{ok:boolean, reason?:"completed"\|"cancelled"\|"gap"\|"none", fromSeq?:int}` | **新增控制事件（RN-1 修订）**：`ok:true`（`sess.running` 且缓冲可无损覆盖）表示已重放并转入实时续流，前端**保留续流**在途气泡；`ok:false` 表示无法续流——`reason` 区分 `completed`/`cancelled`（run 已终结、DB history 权威，前端**丢弃**在途气泡）、`gap`（缓冲已淘汰待重放事件，前端丢弃并 REST 兜底）、`none`（会话不存在）。**杜绝与 history 重建重复**（RS-11） |
| S→C | `heartbeat` | `{}` | **新增可选心跳**：run 期间周期下行，保活 + 半开检测；**不带 `seq`、不入缓冲、不入消息流**；老前端忽略（RS-7/RN-2） |
| S→C | *（既有 run 事件）* | *（既有 data 不变）* | **仅信封追加 `seq`**：`message_start`/`text_delta`/`thinking_delta`/`tool_call`/`tool_result`/`ui_action`/`message_end`/`done`/`error` data 形状一律不变（`ready`/`history` 不带 `seq`，见上） |

> 既有码 `AGENT_NOT_CONFIGURED` / `LLM_HTTP_ERROR` / `LLM_NETWORK_ERROR` / `AGENT_AUTH_ERROR` / `TOOL_LOOP_LIMIT` / `BUSY` 语义不变。BUSY 触发条件由"每连接局部 run_task"改为"会话级 `sess.running`"——**对外行为更正确**（跨重连可见），错误码与文案不变。

### 4.2 REST

**无变更。** `/api/agent/config`、`/api/agent/conversations/{id}/messages`、`DELETE /api/agent/conversations/{id}` 签名与响应不变。

### 4.3 配置项（`AGENT_*`，可经 `.env` / userData/agent.env 覆盖）

| 字段 | 默认 | 含义 |
|---|---|---|
| `agent_detach_grace_seconds` | `30.0` | 断连后 run 的存活宽限；`0` 即"断连立即取消"（回退迭代 2 行为） |
| `agent_heartbeat_seconds` | `20.0` | run 期心跳间隔（须 < 中间层 idle 超时） |
| `agent_event_buffer_size` | `512` | 会话事件缓冲上限（重放窗口） |

### 4.4 续流握手（纯协议时序，无新依赖）

服务端每条新连接仍先 `ready` 再 `history`（不变）；前端在 `reconcileHistory` 保留在途 `streaming` 气泡后、于现有 `onConnState(true)`（即 `ws.ts` 的 `onopen→onState(true)`）发 `resume{lastSeq}`（仅当本地有在途气泡）。**TCP 顺序保证**：服务端在进入接收循环前已发 `ready`+`history`，故客户端必先处理完 `history`（保留在途气泡）再收到随后的重放增量，`text_delta` 的 `patchById` 总能命中保留的气泡（消除 R9 竞态）。服务端按 `sess.running` 严格分流（RS-2）：仍在跑且缓冲可无损覆盖 → 重放 `seq>lastSeq` + `resumed{ok:true}` 转实时；run 已终结 / 缓冲空洞 → `resumed{ok:false, reason}`，前端**丢弃**在途 uuid 气泡、单以 `history`（必要时 REST 兜底）为权威（RN-1）。

> **id 空间纪律（承袭迭代 2 RN-1）**：重放事件用客户端流式 uuid msg_id（`loop.py:182`），与 DB-int history id 是不同 id 空间。**续流（`ok:true`）时**在途 uuid 气泡由本地保留、不参与 history 重建；**终结（`ok:false`）时**则直接丢弃该 uuid 气泡而非跨 id 去重——两路都**绝不跨 id 空间按 id 去重**，与"禁止跨 id 空间去重"原则一致。这正是 v1 在"宽限内完成"路径会出重复的根因，本版以"按 `sess.running` 分流 + 终结即丢弃"根治。

---

## 5. Data model（数据模型）

**本迭代不改持久化 schema。** `AgentMessage` 表（`db/models.py`：`id/conversation_id/role/content_json/created_ts`）保持原样；逐轮持久化（`loop.py:275-280`）不变。

- **会话级运行态（内存，AgentSession）**：新增 `_seq:int`、`_buffer:deque[(seq,event)]`（`maxlen=agent_event_buffer_size`，新 run 起始 `clear`）、`_sink:Emit|None`、`_run_task:asyncio.Task|None`、心跳任务句柄。均为**运行期态、不落库**（与迭代 1+2"仅落对话可读历史、usage 不落库"一致；事件缓冲是为重放，回答最终仍逐轮持久化在 DB）。
- **会话级宽限态（内存，AgentManager）**：`_grace_timers:dict[convId, Task]`。
- **前端运行态（ChatMessage / AgentPanel）**：新增 `lastSeqRef`（最近收到的最大 `seq`，重连重放对账用，**仅由带 `seq` 的 run 事件推进、`heartbeat` 不推进**，**不入 Zustand 持久化**）；`ChatMessage` 形状不变（`agentTypes.ts:67-76` 已有 `streaming`/`interrupted`/`usage`，足够支撑续流；`resumed{ok:false}` 时在途 uuid 气泡由 `dropStreaming` 就地移除、不落库——DB history 为权威）。`agentConversationId` 仍是唯一持久化项（迭代 1 既有口径，不变）。

> 结论：**零 DB 迁移、零 store 版本号变更**——把回归面压到最小，符合"最小改动"原则。

---

## 6. Testing & acceptance criteria（测试与验收）

### 6.1 单元 / 集成测试（沿用 pytest / vitest，离线、不依赖真实网络）

**后端（`services/quant/tests/`）：**
- `test_agent_session_buffer.py`（[新]）：`_emit` 给事件注入单调 `seq` 且入 `_buffer`；`replay_from(last_seq)` 仅回放 `seq>last_seq`、按序、不重复；缓冲超 `maxlen` 时旧事件淘汰且重放仍正确。
- `test_agent_manager_detach.py`（[新]）：① `detach` 不立即取消、run 继续；②宽限内 `attach` 取消计时器、run 存活；③宽限耗尽 `sess._cancel` 置位、落盘半轮、记录日志；④ `agent_detach_grace_seconds=0` 时 `detach` **立即**取消（等价迭代 2 行为，回退开关生效）。
- `test_agent_ws.py`（[改]）：**语义迁移**——原"断连即 drain 取消"用例改为断言"断连即 detach、run 在宽限内存活"；新增三条端到端用例（FastAPI TestClient WS，注入假 LLM 分多片 text_delta 并在中途模拟断连）：①"断连→宽限内重连（run 仍在跑）→`resume{lastSeq}`→收到 `seq>lastSeq` 重放且无重复→续流至 `done`"；②**（RN-1 防回归）**"断连→宽限内 run 自然完成→重连 `resume`→得 `resumed{ok:false, reason:'completed'}` 且**不重放**，前端侧 `dropStreaming` 后与 `history` 重建**无重复**"；③"`heartbeat` 帧不带 `seq`、不进缓冲（`resume` 重放序列里无 heartbeat）"。BUSY 改会话级后跨"伪重连"仍命中。
- `test_agent_loop.py`（[改]）：断言 `run` 去 `emit` 形参后经 `self._emit` 产出事件序列与迭代 2 等价（既有断言用 capture sink 适配）；`_emit` 给 run 事件注入单调 `seq`、`heartbeat` 经 sink 直发**不带 `seq`/不入 `_buffer`**；心跳协程在 run 期周期下行，且 run `finally`/`cancel`/`shutdown` 三处均被取消（RN-7）。
- `test_agent_config.py`（[新或改]）：边界 `@field_validator` 正反例（`agent_detach_grace_seconds<0`、`agent_heartbeat_seconds<=0`、`agent_event_buffer_size<64` 报错；合法值通过）。
- `test_agent_tools.py`（[改]）：截断 / 下采样工具 payload 含 `truncated/totalCount`、summary 文案统一（RS-15 正反例）。
- 既有 `test_agent_network.py`（`-m network`）保持绿（真实 KIMI 往返不受本迭代影响）。

**前端（`apps/web/**/*.test.ts`）：**
- `lib/markdown.test.ts`（[新]）：代码块 / 行内代码 / 粗斜体 / 列表 / 标题 / 安全链接渲染正确；**安全回归**——含 `<script>`/`<img onerror>`/`javascript:` 的输入被转义、绝不产出可执行 HTML / 危险链接（核心安全护栏）；未闭合围栏容错收尾不抛错。
- `lib/agentReducer.test.ts`（[改]）：`heartbeat` 走 `default` 不破坏消息流；新增 `dropStreaming` 纯函数用例（移除在途 streaming uuid 气泡、保留已完成轮）；"瞬断保留在途气泡 → `resume`(`ok:true`) 续流不重复"用例；**（RN-1 防回归）**"`resumed{ok:false, reason:'completed'}` → `dropStreaming` 丢弃在途、与 `reconcileHistory` 重建结果**无重复气泡**"用例（区别于旧的 `settleStreaming` 标中断）。
- `AgentPanel` 行为（组件/集成级，[新或改]）：`resumed.reason` 分流——`completed`/`cancelled`/`none` → 丢弃在途；`gap` → 丢弃并于下个 `done` 经 REST `/agent/conversations/{id}/messages` 重建（mock fetch 断言被调用一次且结果无重复）；仅当有在途 streaming 气泡时才发 `resume`。
- `components`/`lib` 级：`AgentMessageList` 用户上滚后停止自动贴底、"回到最新"按钮恢复 pinned（RS-9）；瞬断文案为"回答已保留"而非立即"已中断"（RS-11）。

### 6.2 验收标准（Definition of Done，对齐"稳健可靠、正确交互、顶级设计"）

1. **续流可靠性（核心）**：回答流式途中模拟 ≤宽限秒数的 WS 瞬断并重连，回答**自动续流至完成**，无丢失、无重复、无重启；用户仅见"重连中（回答已保留）"。
2. **宽限取消**：断连后超过 `agent_detach_grace_seconds` 仍无重连，run 被取消、已完成轮落盘、孤儿配额回收、记录日志；`grace=0` 时行为回退为迭代 2 的"断连即取消"。
3. **活性**：长回测（分钟级）期间 WS 持续收到 `heartbeat`，不被 idle 超时切断。
4. **顶级体验**：assistant markdown（代码块 / 列表 / 行内代码 / 粗体 / 链接）正确成形且**安全**（无 XSS）；用户上滚阅读历史不被流式增量打断、"回到最新"可用；助手消息可一键复制；消息流对屏幕阅读器可播报。
5. **交互正确性**：工具截断 / 下采样对模型与用户透明（`truncated`/总数 / 文案）；超长用户输入被前后端护栏拦截并提示。
6. **回归零破坏**：迭代 1+2 的后端 **151** + 前端 **139** 既有测试在语义迁移后**保持全绿**（受影响仅 `test_agent_ws.py` 的断连语义、`test_agent_loop.py` 的 emit→sink 适配）；`tsc` 干净、`next build` 成功；桌面 `verify-dist` / `smoke` 不受影响（新键经白名单缺省不写、回落默认）。
7. **门禁不回退**：迭代 1+2 全部硬约束（密钥只在后端 / 不入库 / 不进日志、OKX 不假数据、秒级 UTC、双标准抽象、ui-action 驱动左图、零新增依赖、CLAUDE.md 阈值）继续成立。

---

## 7. Risks & mitigations（风险与缓解）

| # | 风险 | 缓解 |
|---|---|---|
| R1 | **续流重放产生重复气泡/事件**（最严重正确性风险；RN-1 即此风险的一个 v1 漏网路径）| ①续流仅在 `sess.running` 为真时发生，严格按 `seq>lastSeq` 重放、缓冲单调递增、复用本地同一 uuid 气泡（不新建）；②**run 在宽限内已终结**则 `resumed{ok:false}`、前端 `dropStreaming` 丢弃在途气泡、单以 DB history 为权威（根治"完成态重复"，RN-1）；③缓冲空洞 `gap` 走 REST 兜底重建。`test_agent_session_buffer.py` + `test_agent_ws.py` 完成态防回归用例 + reducer `dropStreaming` 用例三重护栏 |
| R2 | run/socket 解耦改动面大、伤及绿测 | **优雅降级开关** `agent_detach_grace_seconds=0` 一键回退迭代 2 行为；`run()` 保留可选初始 sink 兼容既有单测；受影响测试明确（`test_agent_ws`/`test_agent_loop`）并给出迁移断言；P0 先行、C/D 可独立落地 |
| R3 | 宽限窗口致"用户已离开仍烧配额" | 宽限默认仅 30s、到点必取消并落盘 + 日志；`shutdown` 进程级兜底；远程场景配合心跳更快识别真断连 |
| R4 | 事件缓冲无界增长 / 内存膨胀；宽限内缓冲溢出致重放空洞 | `deque(maxlen=agent_event_buffer_size=512)` 硬上限、新 run 起始 `clear`；缓冲只存当前 run 的**真实事件**（`heartbeat` 已剥离，RN-2），故 512 槽位与宽限窗口的关系更可控；万一仍溢出（`resume.lastSeq` 落在保留窗口前）→ `resumed{ok:false, reason:'gap'}` + 前端 REST 兜底重建，**不产出半截/重复气泡** |
| R5 | 心跳冲刷前端 / 干扰 reducer / 挤占重放缓冲 | `heartbeat` **不带 `seq`、不入 `_buffer`、不入消息流、不推进 `lastSeq`**（RN-2），仅刷半开检测计时器与活性指示；reducer `default` 原样返回；老前端忽略未知类型 |
| R6 | 自研 markdown 渲染引入 **XSS** | **先 HTML 转义后格式化、绝不渲染原始 HTML、不用 `dangerouslySetInnerHTML`**；链接仅 `http(s)`、`rel=noopener`；`lib/markdown.test.ts` 专测注入向量被中和 |
| R7 | markdown 流式途中半截围栏闪烁 | 流式途中降级纯文本、`message_end` 后富文本化，或对未闭合围栏容错收尾；单测覆盖 |
| R8 | 滚动锚定改动误伤"新消息贴底"直觉 | 默认 pinned；仅用户主动上滚才让出；距底阈值（~48px）+ "回到最新"浮钮；组件级单测 |
| R9 | `resume` 与 server 先发的 `ready`/`history` 竞态 | 时序固定：先 `history`（前端 reconcile 保留在途气泡）→ 前端 `onState(true)` 才发 `resume` → server 重放；`resumed{ok:false}` 兜底收束，无悬挂 |
| R10 | `ReconnectingWS` 改动波及 market/paper 复用方 | **RN-3：根本不改 `ws.ts`/`agentWs.ts`**——现有 `onopen→onState(true)`（`ws.ts:37-40`）已足够，`resume` 由 `AgentPanel` 现有 `onConnState` 发出；shared 层零改动、market/paper 行为零变化 |
| R11 | 新增 `seq` 字段 / 事件破坏老前端 | 纯追加可选字段 + 三个可选事件；reducer 白名单分派对未知类型 `default` 原样返回（`agentReducer.ts:122-124`）；TS 类型设可选 |
| R12 | 配置边界校验误拒既有合法值 | 边界设宽松下限（仅拒 0 / 负 / 过小），RS-13 校验集对应默认值（grace 30 / heartbeat 20 / buffer 512 / tool_rounds 12 / context 40 / retries 2 / drain 8.0）全部合法通过；正反例单测（未纳入 RS-13 的 `retry_base_ms`/`retry_max_ms` 维持迭代 2 现状不新增校验，RN-6） |
| R13 | 桌面端拿不到新配置键 | 承袭迭代 2 IMPL-1：3 个新键入 `build-agent-env.mjs` 白名单，缺省即不写、回落 `config.py` 默认，零破坏既有打包链 |
| R14 | 范围蔓延成大重构 | 严守"增量、向后兼容、不新增依赖、不改 DB / REST 契约"；P0（A 组）为收官核心，B/C/D 分级可裁；本迭代不碰 `llm/*` 重试与工具回测逻辑 |
| R15 | 会话级 BUSY 时序窗口 / `_run_task` 异常未检索（RN-4）| BUSY 权威仍是 `run()` 内层同步 check-and-set 守卫（`loop.py:116-119`，检查与置位间无 `await`，并发任务不交错）；WS 层 `sess.running` 仅快路、漏判亦由内层兜底回 BUSY；`sess._run_task` 挂 `add_done_callback` 检索并记录后台异常，杜绝 "Task exception never retrieved" |

---

## 8. 附：实施注意事项（给下游 Subtask #1 / #2）

- **跨平台命令**：建目录用 `python -c "from pathlib import Path; Path('…').mkdir(parents=True, exist_ok=True)"`；勿用 `mkdir -p` / `&&` / 反斜杠续行（见任务环境约定）。
- **守阈值**：`_emit`/`replay`/markdown 解析等新 helper 控制在 ≤50 行 / 形参 ≤5 / 圈复杂度 ≤10 / 嵌套 ≤4；用 guard clause + 查找表表达分支。
- **测试先行**：对每个加固缺口**先写能复现的失败用例再改实现**（CLAUDE.md §七）——例如先写"瞬断后 run 被取消、回答丢失"的失败断言，再落 detach+宽限+重放令其转绿；markdown 安全用例先于渲染器实现。
- **错误显式传播**：sink 发送失败仅吞在 `_emit`/`_send_raw` 边界（连接已死），run 自身错误仍显式 emit `error`；宽限取消显式记录、不静默。
- **契约稳定**：所有改动追加式；不改既有 WS/REST 字段语义、不改 store 版本、不改 DB schema——把回归面压到最小。BUSY 改会话级属"行为更正确"而非破坏（错误码 / 文案不变）。
- **凭证不入库**：本迭代不应触碰真实 key；真实 key 仅在 gitignored `services/quant/.env` 与 gitignored `desktop/resources/agent/agent.env`（构建产物）；下游 Subtask #3 提交务必**精确 `git add <具体路径>`、严禁 `git add -A`**，复核 diff 无任何 `sk-kimi-`（CLAUDE.md §八第 7 条）。
- **设计 token 复用**：markdown 富文本 / 代码块 / 复制按钮 / "回到最新"浮钮 / "回答已保留"文案全部用 `var(--surface-2)/--border/--accent/--muted/--text)`，自动适配明暗主题，沿用 `transition-* ease-smooth`，保持顶级设计一致性。

---

（全文覆盖 Overview / 技术设计（A–D 四组，逐项锚定 file:line）/ 文件清单 / 接口变更 / 数据模型 / 测试验收 / 风险缓解 / 实施注意，远超 800 字。本迭代为**收官增量**：补齐迭代 1+2 显式留待后续的 run/socket 解耦续流可靠性，兑现原始需求的顶级交互体验，附少量收官加固——所有改动向后兼容、零新增依赖、零 DB 迁移、不破坏既有对外契约，并以 `agent_detach_grace_seconds=0` 提供一键回退安全阀。**v1 由 Subtask #0 方案设计产出，v2 由 Subtask #1 评审修复完成。**）

---

## 评审结论（Review Verdict · Subtask #1 · v2）

**结论：有条件通过（Approved with conditions）。**

本方案设计扎实、规模适当、与迭代 1+2 既有架构与 CLAUDE.md 阈值一致，且所有 `file:line` 声明均经回源核验属实。评审共发现 **1 个 P0 + 3 个 P1 + 3 个 P2**，其中**全部 P0/P1 已在本文 v2 正文就地修复，P2 文档类问题亦一并修正**，无遗留 P0/P1。整体可行性高、可逐行实施、零新增依赖、零 DB 迁移，并保留 `agent_detach_grace_seconds=0` 一键回退安全阀。

**核心改动（评审版定型）：续流与"DB 权威历史"严格分工——"重放续流"只在 `sess.running` 为真时发生；run 一旦在宽限内终结（完成/取消/空洞），一律 `resumed{ok:false}`，前端丢弃在途 uuid 气泡、单以 DB history（必要时 REST 兜底）为权威。** 这从根上消除了 v1"宽限内完成"路径会出现的重复气泡（RN-1），并以"心跳剥离缓冲 + 缓冲空洞 REST 兜底"补齐了重放完备性（RN-2）。

**放行条件（移交 Subtask #2 代码开发，须逐条落实，否则视为未达 DoD）：**

1. **【对应 RN-1，最高优先】** `resume` 必须按 `sess.running` 严格分流；run 终结路径前端必须 `dropStreaming`（丢弃）而非 `settleStreaming`（标中断保留），并以 `test_agent_ws.py` 的"宽限内完成→无重复气泡"用例**先红后绿**地证明（CLAUDE.md §七：先写复现用例再改实现）。
2. **【对应 RN-2】** `heartbeat` 必须经 `self._sink` 直发，**不经 `_seq`/`_buffer`**；缓冲空洞须走 `resumed{ok:false, reason:"gap"}` + 前端既有 REST `/agent/conversations/{id}/messages` 兜底重建（不得新增端点）。
3. **【对应 RN-3】** **不得修改** `lib/ws.ts` / `lib/agentWs.ts`；`resume` 一律由 `AgentPanel` 现有 `onConnState(true)` 重连分支发出（且仅当本地有在途 streaming 气泡）。
4. **【对应 RN-4】** BUSY 以 `run()` 内层同步守卫为权威，WS 层 `sess.running` 仅快路；`sess._run_task` 必须 `add_done_callback` 检索并记录后台异常。
5. **【对应 RN-7】** 心跳任务须在 `run()` 的 `finally`、`sess.cancel()`、`manager.shutdown` 三处均被取消。
6. **【硬约束承袭】** 迭代 1+2 全部门禁不回退（密钥只在后端/不入库/不进日志、OKX 不假数据、秒级 UTC、双标准 provider、ui-action 驱动左图、零新增依赖、CLAUDE.md 阈值）；既有 **151 后端 + 139 前端**测试在语义迁移后保持全绿，且 `markdown` 安全用例（注入向量被中和）必须随渲染器一并落地。

满足以上条件即达本方案 DoD；条件均为对 v2 正文已写明设计的忠实落地，不引入新的设计决策。**评审通过，准予进入代码开发（Subtask #2）。**

---

## 实施过程发现的方案缺陷（Issues Found During Implementation · Subtask #2）

> 落代码时逐条核验后发现 1 处会破坏 RN-1 正确性目标的**机制级遗漏**（已就地以更强不变量修正），及 2 处不影响正确性的实现取舍记录如下，供 Subtask #3 评审复核。所有修正均忠实于 v2 正文的**意图**（续流无丢失 / 无重复 / 无乱序），未引入新对外契约。

### IMPL-FIX-1（P0 · 机制级修正）：`attach` 无条件挂 sink + `resume` 重放，对**正在跑**的会话存在「实时事件抢跑 → 乱序 / 重复 / 丢失」竞态

- **现象（按 v2 正文字面实现会出问题）**：v2 §2-RS-2 写 `attach` 后即 `sess.set_sink(sink)`，而 `resume` 再重放 `seq>last_seq` 的缓冲。但 `attach`（连接建立时）与 `resume`（前端 `onState(true)` 后才发）之间存在时间窗：若此间正在跑的 run 经 `_emit` 把**新事件**实时发往刚挂的 `sink_B` 并同时入缓冲，则随后 `resume` 重放 `seq>last_seq` 会把这些事件**再发一次**（重复），且实时事件先于重放到达会造成 `text_delta` **乱序**；若改为只重放未发事件又会在 `set_sink` 与重放快照之间**丢事件**。这正面冲击 R1/RN-1「续流无重复=最高正确性目标」。
- **根因**：`_emit` 的实时下发与 `resume` 的重放是两个并发协程，缺乏互斥与「先重放后转实时」的原子性保证；`attach` 过早开启实时通道。
- **修正（已落地，强化不变量，不改对外契约）**：
  1. `AgentSession` 新增 `self._sink_lock`；`_emit`（增 seq + 入缓冲 + 发 sink）与 `resume_to`（重放 + 转实时）**全程持同一把锁**，二者严格串行，杜绝交错。
  2. `resume_to` 在锁内**先**把 `seq>last_seq` 的缓冲事件重放到新 sink、**再** `self._sink = sink` 转实时——保证「先重放后实时」的原子顺序（消除乱序）；锁的互斥保证重放期间无 `_emit` 穿插（消除丢失），且转实时后的新事件不在重放快照内（消除重复）。
  3. `manager.attach` 仅在 `not sess.running or not sess.has_buffered` 时 `set_sink`——对「正在跑且已产事件」的会话**不立即开实时通道**，交由 `resume_to` 原子地「重放后转实时」；对 fresh run / run 尚未产事件（缓冲空）的会话则照常立即挂 sink（无重放无乱序风险，亦覆盖「断连早于首个 `message_start`」窄窗）。
- **影响**：完全实现 v2 §4.4「TCP 顺序 + 严格分流」想要达到的「无丢失 / 无重复 / 无乱序」续流；`test_agent_session_buffer.py` / `test_agent_manager_detach.py` / `test_agent_ws.py`（含 RN-1「宽限内完成→不重放→无重复」端到端）三层用例护栏。**对外 WS/REST 契约、`resume`/`resumed` 信封、回退开关 `agent_detach_grace_seconds=0` 全部不变。**

### IMPL-NOTE-2（实现取舍 · 不影响正确性）：`_drain` 单一实现，`api/agent.py:_drain` 委托 `AgentManager.drain`

- v2 §3.1 要求「`_drain` 保留，移作宽限超时取消路径兜底」。落地时把有界 drain 的**唯一实现**置于 `AgentManager.drain`（静态方法，宽限耗尽路径直接调用），`app/api/agent.py` 保留同名 `_drain` 作**薄委托**（既有 `test_agent_ws.py::test_drain_*` 仍 import 该符号、语义不变），避免内外层重复实现与 `api→manager` 循环依赖。

### IMPL-NOTE-3（聚焦取舍）：未改 `app/agent/__init__.py`

- v2 §3.1 把 `app/agent/__init__.py` 标 [改] 且注明「**如导出新异常 / 类型则补**（保持聚焦）」。本迭代未新增需要从包级对外导出的异常 / 公共类型（新增的 `_emit`/`resume_to`/`set_sink` 等为会话内部方法，`_error`/`_resumed` 为 `manager` 模块私有），故按其条件「保持聚焦」未改该文件，避免无意义 diff。
