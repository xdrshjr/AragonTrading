# Agent Trading Copilot — 加固增强方案 (spec, iteration 2)

> 文档版本：**v2**（迭代 2 · v1 由 Subtask #0「方案设计」产出；本 **v2** 由 Subtask #1「方案评审与修复」就地评审并修订——见下方「## 评审记录」与文末「## 评审结论」。所有 P0 / P1 问题已在正文就地修复并标注 `[v2 评审修复]`。）
> Feature slug：`agent-copilot-hardening`
> 作者 / 角色：Solution Architect（Anthropic Engineering）
> 迭代：Iteration 2 / 3
> 全局目标：**完成所有开发，稳健可靠，能够正确进行 Agent 交互。**
> 基线：迭代 1 已交付并提交（commit `b574d34`，`feat: add Agent Trading Copilot — tabbed right panel with tool-driven AI chat`）的全栈 Agent Trading Copilot，其设计见 `docs/plans/agent-trading-copilot/spec.md`（v2，已评审）。
> 关系声明：本文档**只规划在迭代 1 基线之上的「稳健性 / 可靠性 / 交互正确性」增量加固**，不重述、不推翻迭代 1 的架构、WS/REST 契约、工具集与数据模型。迭代 1 的硬约束（密钥只在后端 / 不入库 / 不进日志、真实 OKX 数据绝不假数据、秒级 UTC ts、双标准 provider 抽象、ui-action 驱动左图、复用 `ReconnectingWS`/`Tabs`/设计 token）在本迭代**继续生效，不得回退**。

---

## 评审记录（Review Notes · Subtask #1）

> 评审人 / 角色：Senior Reviewer（Anthropic Engineering）。评审维度：**可行性 Feasibility / 完备性 Completeness / 一致性 Consistency / 适度性 Right-sizing**。
> 方法：**逐文件回到真实源码核验** v1 的全部 `file:line` 锚点与行为断言（`anthropic_client.py` / `openai_client.py` / `loop.py` / `manager.py` / `registry.py` / `tools/base.py` 与各 `*_tools.py` / `api/agent.py` / `conversation.py` / `apps/web` 的 `AgentPanel.tsx` / `agentReducer.ts` / `agentTypes.ts` / `ws.ts`）。
>
> **核验结论（正面）**：v1 的源码锚点与行为断言**高度准确**——所有引用行号误差 ≤3 行；11 个工具名与入参名（`run_backtest`/`compare_strategies`/`get_candles`/`get_run_*`/`list_instruments`/`get_market_summary`/`show_chart` 等）与真实代码**逐一吻合**；`message_end` 丢弃 usage（loop.py:146）与迭代 1 spec §4.1 既有契约 `{id, usage?}`（已核对该 spec 第 314 行）确属契约缺口；`get_or_create` 无锁脑裂（manager.py:49-55）、`_drain` 无超时（api/agent.py:88-93）、前端 `running` 瞬断永真（AgentPanel.tsx:85-92）等缺口**均真实存在**。**并发补充核验**：`AgentSession.run`（loop.py:91-101）的 `if self._running` 检查与 `self._running=True` 置位之间**无 `await`**，故 H3 锁保证单会话后，BUSY 准入是原子的、无 TOCTOU——此点 v1 隐含正确，本评审予以确认。

| # | 维度 | 位置 | 严重度 | 问题 | 处置 |
|---|---|---|---|---|---|
| RN-1 | 完备性 / 正确性 | E 组 H8b（§2.E item 12）、§4、§6 | **P0** | **重连合并 `mergeHistory` 的去重键不成立**：在途 / 本会话刚流式完成的气泡用客户端 `uuid`（`message_start.id`，loop.py:130），而 `history` 来自 DB 经 `row_to_client_dict`（conversation.py:59）携 **DB 自增整型 id**——两者**不同 id 空间**，"按 message id 去重合并"对**本会话流式产生的每一轮**都无法命中 → 重连后**整轮重复**。叠加 H4「断连即 cancel」会把**被取消的半轮 assistant 落盘**（loop.py:111-112），该半轮在重连 `history` 里再现一次，与本地仍 `streaming` 的 `uuid` 气泡**双重叠加**。直接破坏 H8b 自身要解决的"重连不闪烁 / 不丢在途"目标。 | **已就地修复**：重写 H8b 为「DB 历史为已完成轮的权威来源、整体重建；仅保留唯一仍 `streaming` 且 history 中无对应项的在途气泡；**绝不跨 uuid/DB-int 边界按 id 去重**」，并与 H8a 联动（见 RN-3）。同步改 §3.2 `reconcileHistory`、§4、§6 测试。`[v2 评审修复]` |
| RN-2 | 完备性 / 一致性 | D 组 H5（§2.D item 10）、A 组、§4.1、§3.1 | **P1** | **usage 形状跨 provider 不一致且与对外契约不符**：Anthropic 原始 usage 为 `{input_tokens, output_tokens}`（snake_case，anthropic_client.py message_done 透传原始 dict），OpenAI 标准为 `{prompt_tokens, completion_tokens}`，而 §4.1 对外声明 `{inputTokens?, outputTokens?}`（camelCase）。v1「把 usage 原样并入 message_end」会**违背声明契约**且令结构化日志的 `in_tokens/out_tokens` 取不到值。**附加**：OpenAI 标准流式**默认不返回 usage**，需在请求体显式 `stream_options.include_usage=true` 才有。 | **已就地修复**：H5 增 `normalize_usage(provider, raw)→{inputTokens?, outputTokens?}` 归一函数（两 provider 映射 + 缺失即 None），WS `message_end.usage` 与日志均用归一结果；openai_client 请求增 `stream_options.include_usage`。同步改 §4.1、§3.1、§6。`[v2 评审修复]` |
| RN-3 | 完备性 / 正确性 | E 组 H8a（§2.E item 11）+ C 组 H4 联动 | **P1** | **H8a 与「H4 断连即 cancel run」未对齐**：任何断连都触发 api/agent.py:64 `cancel`+`_drain`，**服务端 run 被取消**，故重连后**永不再有 `done` 到达原 `msg_id`**。v1 H8a 只复位 `running`，未**收束在途气泡**——该气泡将永远停在"流式中"（光标闪烁），且正是 RN-1 双重叠加的源头。 | **已就地修复**：H8a 在瞬断（true→false）时除复位 `running` 外，**就地收束在途 `streaming` 气泡**（置 `streaming=false`、标记"已中断"、由 H12 提供"重试"），作为 RN-1 重连重建不重复的前置条件。`[v2 评审修复]` |
| RN-4 | 一致性 / 适度性 | C 组 H3（§2.C item 7）、§7 R4 | **P2** | **R4 表述与源码不符**：v1 R4 称"`cancel`/`clear` 读字典无需锁（读安全）"，但 `manager.clear`（manager.py:80-85）实为 `self._sessions.pop()`——**写操作**。H3 引入锁后，`clear` 的无锁写与有锁建会在极小窗口内竞争（后果良性但论证错误）。 | **已就地修复**：H3 将 `clear` 的字典变更一并纳入同一 `asyncio.Lock`；R4 表述更正为"`cancel` 仅 `.get()` 读安全；`clear` 的 `pop()` 写入随 H3 一并加锁"。`[v2 评审修复]` |
| RN-5 | 完备性 | A 组 H1（§2.A item 1） | **P2** | `Retry-After` 头可为「整数秒」或「HTTP-date」两种格式；v1 未指明解析口径，HTTP-date 解析失败可能抛错或被误读。 | **已就地修复**：H1 明确"仅采纳整数秒形式，封顶 `agent_retry_max_ms`；非整数（HTTP-date 等）一律回退到计算退避，不抛错"。`[v2 评审修复]` |
| RN-6 | 完备性 / 适度性 | A 组 H1 + C 组（§2.C） | **P2** | 未点明 pre-stream 重试的**累计退避墙钟**与 `agent_timeout_seconds`(=120) / `agent_drain_timeout_seconds`(=8) 的关系；退避 `asyncio.sleep` 须可被 cancel 打断，否则断连时 drain 仍可能被退避卡住。 | **已就地修复**：H1 注明"2 次重试累计退避 ≤ ~16s+jitter，远小于 run 超时；退避用 `asyncio.sleep`（受 cancel/`_drain` 中断），断连时立即让出"。`[v2 评审修复]` |
| RN-7 | 一致性 | H9（§2.B item 6）、§3.3 `.env.example` | **P2** | (a) H9 的"禁用工具终轮总结"须自身**检查 cancel** 且不得重新进入工具轮，否则与取消语义冲突；(b) §3.3 计划改 `.env.example`，下游须先确认该文件存在（不存在则新建 Agent 段，勿写真实 key）。 | **已就地修复**：H9 增"终轮总结前后检查 `self._cancel`、`tools=[]` 单轮不回环"；§3.3 注明"`.env.example` 不存在则新建 Agent 段、仅占位值"。`[v2 评审修复]` |

> **可行性总评**：技术栈完全支撑（`httpx`/`asyncio`/`pydantic-settings` 均已在用，零新增三方依赖），全部为增量、向后兼容改动，无 DB 迁移。**适度性总评**：范围收敛得当，P0/P1/P2 分层合理；唯一"过度设计"风险是 v1 H8b 的 merge 方案——它比迭代 1 既有的 overwrite **更复杂且引入 RN-1 缺陷**；v2 已将其回正为"DB 权威重建 + 仅保唯一在途气泡"的更简且正确方案。**一致性总评**：与 `CLAUDE.md` 阈值（≤800 行 / ≤50 行 / 圈复杂度 ≤10 / 嵌套 ≤4）、错误显式传播、对外错误码稳定、密钥不入库不进日志等约定一致，无冲突。

---

## 1. Overview（概述）

迭代 1 已交付一个**功能完整**的对话式 AI 助手：右侧面板多页签（AI 助手 / 交易策略）、后端 agentic loop（`services/quant/app/agent/loop.py`）、双标准 provider 适配（`agent/llm/anthropic_client.py` / `openai_client.py`）、11 个工具的注册表（`agent/tools/registry.py`）、`AgentManager` 单例（`agent/manager.py`）、WS `/api/ws/agent/{convId}` 与 REST（`api/agent.py`）、`AgentMessage` 持久化、桌面端凭证打包链，并以真实 KIMI 凭证经 `-m network` 端到端实证通过。后端 8 个 agent 测试文件（`tests/test_agent_*.py`）+ 前端 reducer/actions 单测均在迭代 1 落地。

但「功能完整」不等于「稳健可靠」。本迭代对照真实源码逐文件审查，发现 Agent 子系统在**可靠性（LLM 传输层无重试、无连接复用）**、**交互正确性（模型产出的工具入参未做边界校验、错误根因被泛化吞没、`message_end` 丢弃 usage 与 §4.1 接口契约不符）**、**并发与生命周期（`get_or_create` 无并发锁导致同会话双连接脑裂、断连 `_drain` 无超时可挂死清理路径）**、**前端韧性（瞬断后 `running` 永真、同会话重连 `history` 覆盖致在途气泡丢失闪烁、`send` 未校验连接态）** 与 **可观测性（无结构化运行日志 / token 用量不可见）** 五个维度存在明确缺口。这些缺口在"愉快路径"下不可见，但恰是"稳健可靠、正确交互"全局目标的命门——网络抖动、模型偶发限流、用户多窗口、长会话、密钥过期等真实场景一旦触发，当前实现会表现为静默卡死、配额空烧、误导性报错或体验断裂。

本方案以**最小、向后兼容、可逐行实施**的加固为原则：所有改动均为**增量**（新增配置字段、新增错误码、新增可选事件字段、新增校验钩子、新增前端守卫），不改既有 WS/REST 对外契约的现有字段语义，不破坏迭代 1 的 123 项后端 + 131 项前端测试。每一项加固都锚定具体 `file:line`、给出确定性的实现指引，并附对应回归测试。本迭代刻意**不做**大重构、不引入新三方依赖、不扩展工具集——范围严格收敛于"把已有能力打磨到生产级稳健"。

---

## 2. Technical design（技术设计）

> 改动按主题分 A–E 五组。优先级：**P0**（直接服务全局目标、必做）｜**P1**（应做）｜**P2**（加固未来、可选）。每组先列"现状（已核验事实）"再列"加固设计"。

### A. LLM 传输层可靠性（reliability）

**现状（已核验）：**
- `agent/llm/anthropic_client.py:67-82`：`stream()` 每次调用都 `async with httpx.AsyncClient(...)` 新建客户端（无连接复用 / keep-alive）；对 `status_code >= 400` 一律 emit `LLM_HTTP_ERROR` 后 `return`；对 `httpx.HTTPError`（连接 / 读超时 / 网络抖动）emit `LLM_NETWORK_ERROR`。**两类瞬时错误都无任何重试**。`openai_client.py` 同构（pre-stream 状态检查 + 网络异常兜底）。
- KIMI Coding 端点会限流（429）/ 偶发 5xx；当前一次失败即把硬错误抛给用户，违背"稳健可靠"。

**加固设计：**

1. **[P0｜H1] pre-stream 有界重试 + 指数退避抖动。** 在两个 client 的 `stream()` 中，把"建立连接 + 读取响应状态码"阶段（anthropic_client.py:72-78 的 `async with` 进入到 `status_code` 判定）包进重试循环：
   - **可重试集**：`status_code ∈ {429, 500, 502, 503, 504}` 或 `httpx.ConnectError / httpx.ReadTimeout / httpx.ConnectTimeout / httpx.RemoteProtocolError`。
   - **退避**：第 `i` 次（0-based）等待 `min(agent_retry_base_ms * 2**i, agent_retry_max_ms)` 毫秒 + `[0, 250ms]` 抖动；上限 `agent_max_retries` 次（默认 2，即最多 3 次尝试）。退避用 `asyncio.sleep`（**可被 cancel / `_drain` 中断**——断连时立即让出，不阻塞清理，呼应 RN-6）。
   - **`[v2 评审修复 RN-5]` `Retry-After` 解析口径**：检查 `429` 响应的 `Retry-After` 头时**仅采纳「整数秒」形式**（存在则优先、封顶 `agent_retry_max_ms`）；HTTP-date 等非整数形式解析失败时**回退到上面的计算退避，绝不抛错**。
   - **`[v2 评审修复 RN-6]` 退避墙钟边界**：`agent_max_retries=2` 时累计退避 ≤ `~2*agent_retry_max_ms + jitter`（≈16s+），远小于 `agent_timeout_seconds=120`；不与 run 总超时冲突，亦不会把 `agent_drain_timeout_seconds=8` 卡死（退避在 cancel 时被打断）。
   - **铁律（防重复输出）**：**只在尚未 yield 任何归一化事件之前重试**。一旦进入 SSE 解析（`parse_anthropic_sse` 已产出过 `text_delta`/`tool_use_*`），中途网络断开**不重试**，按现状 emit `LLM_NETWORK_ERROR`（loop 已落盘本轮已完成文本，用户可经 H12 重试）。这保证重试绝不产生重复 token，是该设计的正确性核心。
   - **非可重试 4xx**：`400/422` → `LLM_HTTP_ERROR`（请求体问题，重试无益）；`401/403` → 见 H6（`AGENT_AUTH_ERROR`）；`404`（model-not-found）→ `LLM_HTTP_ERROR`（提示切换 model）。
   - 拆出私有 helper `_open_stream_with_retry(client_factory, ...)` 守住 50 行 / 圈复杂度 10 阈值（CLAUDE.md §二/§六）。

2. **[P1｜H6] 鉴权错误专属错误码。** `status_code ∈ {401, 403}` 时 emit `StreamEvent(kind="error", code="AGENT_AUTH_ERROR", message="模型密钥无效 / 过期 / 无权限（HTTP {code}）。请更新 .env 或 userData/agent.env 中的 AGENT_API_KEY 后重启应用。")`。前端据此码渲染**可操作的引导态**（指向凭证文件），区别于泛化的 `LLM_HTTP_ERROR`。不重试（重试 401 无意义）。

3. **[P2｜H10] 连接复用。** 将 `httpx.AsyncClient` 提升为 client 实例级长生命周期对象（`__init__` 建、`aclose()` 释放，`AgentManager.shutdown()` 触发），配 `httpx.Limits(max_keepalive_connections=…)`，降低每轮 TLS 握手时延。注意保持 `proxy` 注入不变（R5）。属优化项，不影响正确性。

### B. 工具调用交互正确性（correctness）

**现状（已核验）：**
- `agent/loop.py:49-55`：`_ToolAcc.to_block()` 对模型产出的 `input_json` 做 `json.loads`，**解析失败静默回退 `{}`**——工具随后以空参执行，得到令人困惑的 `ok=False` 或异常。
- `agent/tools/registry.py:59-72`：`execute` 对 `Exception` 统一兜底为 `ToolResult(False, f"工具 {name} 执行出错：{exc}")`；对**模型产出的非法入参**（缺必填、枚举越界、`startTs > endTs`、`limit` 过大）**无前置校验**——直接进 `tool.execute` 撞运行期异常，根因被泛化措辞掩盖，模型无法据此自我纠正。
- `agent/tools/base.py:62-71`：`Tool` 基类仅 `name/description/input_schema/execute`，**无入参校验钩子**。

**加固设计：**

4. **[P0｜H2] 工具入参边界校验钩子。** 遵循 CLAUDE.md §五"外部输入在边界处一次性校验"——**模型产出的工具入参就是外部输入**：
   - `Tool` 基类新增 `def validate(self, args: dict) -> str | None: return None`（默认放行，返回错误信息字符串即视为非法）。
   - 在 `ToolRegistry.execute`（registry.py:63 `try` 之前）调用 `msg = tool.validate(args or {})`；非 `None` 则**不执行**，直接 `return ToolResult(False, f"参数无效：{msg}")`。该结果照常作为 `tool_result` 回喂模型，使其**下一轮自我修正**（这正是"正确进行 Agent 交互"的关键闭环）。
   - 为有约束的工具实现 `validate`：
     - `run_backtest` / `compare_strategies` / `get_candles`：必填 `instId`（非空字符串）、`bar ∈ {1m,5m,15m,1H,4H,1D}`、`startTs/endTs` 为正整数且 `startTs < endTs`、`limit`（若给）`1..1000`；`compare_strategies` 的 `strategyIds` 非空且元素为正整数。
     - `get_run_metrics/trades/equity`：`runId` 为正整数。
     - `list_instruments`：`instType ∈ {SPOT,SWAP,FUTURES}`（缺省 `SPOT`）。
     - `show_chart`：若给 `bar` 则校验枚举；若同时给 `startTs/endTs` 则校验 `startTs < endTs`。
   - **malformed-JSON 自然覆盖**：模型若产出非法 JSON，`to_block()` 回退 `{}`，必填校验即报"缺少必填参数 X"——无需改 `ToolUseBlock` 数据结构即可让模型获知入参有误。校验逻辑集中在各 `*_tools.py`，每个 `validate` ≤ 50 行、用查找表 / guard clause 控制圈复杂度（CLAUDE.md §六）。

5. **[P1｜H7] 错误根因可读化（脱敏、有界）。** registry.py:70-72 的兜底改为携带**安全的结构化原因**：`ToolResult(False, f"工具 {name} 执行出错：{type(exc).__name__}: {str(exc)[:200]}")`——仅类型名 + 截断消息（杜绝栈、杜绝密钥；OKX/HTTP 分支已分别处理）。使模型 / 用户看到"`KeyError: 'instId'`"而非黑盒，便于模型纠正与用户报障。日志侧保留 `logger.exception` 全栈（仅后端日志，C7 不含密钥）。

6. **[P2｜H9] 轮数耗尽优雅收尾。** loop.py:123-125 当前命中 `agent_max_tool_rounds` 仅 emit `TOOL_LOOP_LIMIT` 错误。加固：命中上限后**追加一次"禁用工具"的 LLM 终轮**（`tools=[]`），让模型用已得结果给出文字总结，再 emit `done`；总结失败才回 `TOOL_LOOP_LIMIT`。提升"正确交互"体感而非半路硬断。
   - **`[v2 评审修复 RN-7]` 取消语义对齐**：终轮总结**前先检查 `self._cancel`**（已取消则直接走 `done{cancelled:True}`，不再发起 LLM 调用）；`tools=[]` 的终轮是**单轮、不回环**（拿到的任何 `tool_use` 都忽略，不再进入工具执行），避免与轮数上限 / 取消语义冲突。

### C. 并发与生命周期（robustness）

**现状（已核验）：**
- `agent/manager.py:49-55`：`get_or_create` 为同步、**无并发锁**。`api/agent.py:55-85` 的 BUSY 守卫是 `_dispatch` 内的**局部 `run_task` 变量**（每个 WS 连接独立），**非会话级**。⇒ 同一 `convId` 被两个 WS（如用户开两个窗口）同时打开时，会创建**两个 `AgentSession` 对象**，各自持有独立内存 `messages`，BUSY 守卫互不可见——**脑裂**：消息错乱、并发烧配额、`_running` 形同虚设。
- `api/agent.py:62-66, 88-93`：断连 `finally` 调 `manager.cancel()` 后 `await _drain(run_task)`——`_drain` **无超时**。cancel 仅在轮间 / 工具间 / 流事件间被检查（loop.py:108/120/138/187）；一个**正在线程池跑的长回测**在返回前不响应 cancel，于是 `_drain` 会阻塞 WS 清理直至该工具 / 整个 LLM 流自然结束（最坏到 `agent_timeout_seconds=120s`），清理路径被挂住。

**加固设计：**

7. **[P0｜H3] 会话注册并发锁，确保每 convId 唯一会话。** `AgentManager` 增 `self._lock = asyncio.Lock()`，把 `get_or_create` 改为 `async def`，临界区（`dict.get` → 新建 → `dict[...]=` ）置于 `async with self._lock`。`handle` 改 `await self.get_or_create(...)`。如此同 `convId` 永远复用**同一** `AgentSession`，会话级 `_running`（loop.py:92-94）即对所有连接生效，第二条 `user_message` 正确命中 BUSY。锁仅护内存字典极短临界区，零性能影响。
   - **`[v2 评审修复 RN-4]`** `manager.clear`（manager.py:80-85）实为 `self._sessions.pop()`——**写操作**，故其字典变更**一并纳入同一 `asyncio.Lock`**（`async with self._lock: self._sessions.pop(...)`）。`cancel`（manager.py:75-78）仅 `.get()` 读取，读安全、无需加锁。（v1 §7 R4 原称"cancel/clear 读字典无需锁"对 `clear` 表述有误，已于 R4 更正。）
   - **并发准入原子性（评审确认）**：`AgentSession.run`（loop.py:91-101）的 `if self._running` 检查与 `self._running=True` 置位之间**无 `await`**，在单线程事件循环下为原子操作；H3 锁保证单会话后，BUSY 准入无 TOCTOU 竞争。

8. **[P0｜H4] 断连 drain 有界超时。** `api/agent.py:65` 改为 `await _drain(run_task, timeout=settings.agent_drain_timeout_seconds)`；`_drain` 内用 `asyncio.wait_for(run_task, timeout)` 包裹，`TimeoutError` 时**不再等待**、`run_task.cancel()` 并 `logger.warning("agent run task drained by timeout; possible orphan", ...)`。保证清理路径**永不挂死**；孤儿任务被显式 cancel + 记录（可观测）。默认 `agent_drain_timeout_seconds=8.0`。

9. **[P2｜H11] 长工具协作式取消 + 进行中心跳。** `ToolContext` 增 `cancel_check: Callable[[], bool]`（由 `AgentSession` 注入 `lambda: self._cancel`）；长回测 helper（`agent/services.py`）在分块循环里周期性检查，命中即抛 `AgentCancelled` 提前返回。同时工具执行期已 emit 的 `tool_call` 事件即天然"进行中"信号；如需，可在工具执行超过 N 秒时 emit 一次 `tool_progress{id,name}` 心跳，规避远程部署中间层 idle 超时（迭代 1 spec 遗留的 P2-6）。桌面 / localhost 场景非必需，列为加固未来。

### D. 可观测性（observability）

**现状（已核验）：**
- loop.py:128-148：`_stream_assistant_turn` 在 `message_done` 事件里**拿到了 `usage`**（anthropic_client.py:139-142 已解析 `usage`），但 `message_end` 仅 emit `{"id": msg_id}`——**usage 被丢弃**。这与迭代 1 spec §4.1 承诺的 `message_end {id, usage?}` **契约不符**，且 token 用量对前端 / 日志完全不可见。
- 全 agent 子系统**无结构化运行日志**（轮数、每工具耗时、最终 stop_reason、错误码）——生产期难诊断可靠性问题。

**加固设计：**

10. **[P1｜H5] 透传 usage（含跨 provider 归一）+ 结构化运行日志。**
    - **`[v2 评审修复 RN-2]` usage 归一是前置必做项。** 两个 provider 的原始 usage 字段名不同——Anthropic message_done 透传的是原始 `{input_tokens, output_tokens}`（snake_case，anthropic_client.py），OpenAI 标准为 `{prompt_tokens, completion_tokens}`；而 §4.1 对外契约声明为 camelCase `{inputTokens?, outputTokens?}`。**直接把原始 dict 并入 `message_end` 会违背声明契约、且令下方日志的 `in_tokens/out_tokens` 取空。** 因此在 `agent/llm/retry.py`（或 `agent/llm/base.py`）新增纯函数 `normalize_usage(provider: str, raw: dict | None) -> dict | None`，映射两 provider 到 `{"inputTokens": int|None, "outputTokens": int|None}`，缺失键回退 `None`、`raw` 为空回退 `None`。
    - **OpenAI 流式 usage 需显式开启**：OpenAI 标准流式默认**不返回** usage，须在 `openai_client.py` 请求体加 `stream_options={"include_usage": True}`；KIMI（anthropic 标准）天然带 usage。两路均经 `normalize_usage` 收敛，`usage?` 仍为可选——取不到即不带，前端 / 日志容忍缺失。
    - `_on_stream_event`（loop.py:165-166）在收到 `message_done` 时把 `ev.usage` 经返回值 / 闭包带出；`_stream_assistant_turn` 把 `normalize_usage(provider, ev.usage)` 并入 `message_end` 的 data：`{"id": msg_id, "usage": normalized}`（**兑现** §4.1 契约，前端可选展示）。
    - 每轮工具执行用 `time.perf_counter()` 量耗时；run 结束 emit `done` 前 `logger.info` 一条**结构化行**：`{"conv": convId, "rounds": n, "tools": [{"name","ok","ms"}], "stop_reason", "in_tokens", "out_tokens"}`（`in_tokens/out_tokens` 取自归一后的 usage）——**绝不含密钥 / 用户原文敏感内容**（C7），仅元数据。便于定位慢工具与模型行为。

### E. 前端韧性与交互体验（UX robustness，对齐"美观、优雅、顶级设计"）

**现状（已核验，`apps/web/components/Agent/AgentPanel.tsx`）：**
- L85-92：仅 `done` / `error` 事件清 `running`。**WS 瞬断（同会话重连）无 `done` 到达** ⇒ `running` 永真 ⇒ composer 停在"运行中"、发送被 L114 `running` 拦死，面板假死。
- L67-68：`history` 事件 `setMessages(reduceAgentEvent([], ev))` **整体覆盖**消息列表；`ReconnectingWS` 同会话自动重连后会再次推 `ready`+`history`，**覆盖在途流式气泡** ⇒ 闪烁 + 在途回复短暂消失。
- L110-131：`send` 仅校验 `!ws || !trimmed || running`，**未校验 `connected`**；空态 `onPick={send}` / Enter 可在断线时触发——乐观追加 user 气泡却 `ws.send` 到未就绪 socket（`ReconnectingWS` 无发件队列）⇒ 消息石沉大海。

**加固设计：**

11. **[P0｜H8a] 瞬断即复位 running + 收束在途气泡。** `setConnected` 回调（传入 `agentWS` 的第三参，L101）包一层：当连接态由 `true→false` 且本地 `running` 为真时，`setRunning(false)` 并设一条轻提示 `error="连接中断，正在重连…"`；重连成功（`false→true`）清该提示。
    - **`[v2 评审修复 RN-3]` 必须同时收束在途气泡。** 已核验：**任何断连都触发服务端 `cancel`+`_drain`**（api/agent.py:64）——run 被取消，重连后**永不再有 `done` 到达原 `msg_id`**。因此瞬断时除复位 `running` 外，**就地把仍 `streaming` 的在途气泡收束**（置 `streaming=false`、标记"已中断"，由 H12 提供"重试"）。否则该气泡将永远停在"流式中"，且会成为重连重复（见 H8b/RN-1）的根源。此收束是 H8b 重建不重复的**前置条件**，二者必须联动实现。保证面板**永不假死**。

12. **[P0｜H8b] 重连以 DB 历史为权威重建（替代 v1 的 merge-by-id）。**
    - **`[v2 评审修复 RN-1]` 撤销"按 message id 去重合并"。** 已核验**致命前提缺陷**：在途 / 本会话刚流式完成的气泡用客户端 `uuid`（`message_start.id`，loop.py:130），而 `history` 来自 DB 经 `row_to_client_dict`（conversation.py:59）携 **DB 自增整型 id**——**两者不同 id 空间**，"按 id 去重"对**本会话每一轮流式产物**都无法命中 → 重连后**整轮重复**；叠加"断连即 cancel 落盘半轮"（loop.py:111-112）会令半轮 assistant 在重连 history 里再现并与本地 `uuid` 气泡双重叠加。**故 merge-by-id 既复杂又错误，予以撤销。**
    - **正确设计（落 `lib/agentReducer.ts` 新纯函数 `reconcileHistory(prev, history)`）**：
      1. **已完成轮以 DB 历史为唯一权威**：用 `history`（DB-int id）整体重建已完成消息列表（等价迭代 1 既有 overwrite 的正确性，天然无重复、无 id 空间错配）。
      2. **仅保留唯一在途气泡**：若 `prev` 中存在恰一条 `streaming===true` 且 history 中无对应内容的气泡，则**追加保留**它；否则不保留。
      3. **绝不跨 `uuid`/`DB-int` 边界按 id 去重**——这是 v1 缺陷根因，明令禁止。
    - **与 H8a 的闭环**：因 H8a 已在断连时把在途气泡 `streaming` 收束为 `false`，重连 `history` 到达时已无 `streaming` 气泡可保留 → 直接由 DB 权威重建（其中含被取消落盘的半轮，仅出现一次）→ **既不闪烁、不重复、不孤儿**。第 2 步的"保留在途"是 history 罕见地在流式中途到达时的兜底。
    - **范围说明（适度性）**：本迭代**不**改"run 绑定单一 socket、断连即取消"的架构——"网络抖动中存活并续流"需把 run 与 socket 解耦 + 事件缓冲重放，属更大重构，明确**留待后续迭代**；当前以"取消 + DB 权威重建 + 可重试"达成稳健，符合最小改动原则。
13. **[P1｜H8c] 发送前连接守卫。** `send`（L114）增 `if (!connected) { setError("未连接，无法发送，请稍候重连"); return; }`，并在乐观追加 user 气泡**之前**校验，避免"幽灵消息"。
14. **[P2｜H12] 失败重试 + token 透明化。** 错误气泡旁加"重试"按钮（重发上一条 user 文本）；H5 透传 usage 后，在消息尾 / 面板底栏以极简样式（`var(--muted)` 10px）展示本轮 / 累计 token，符合顶级 AI 对话产品的透明度实践。沿用既有设计 token，不新增视觉语言。

---

## 3. File / module change plan（文件 / 模块变更计划）

> 约定：修改标 **[改]**，新建标 **[新]**。所有后端改动 PEP8 + Google docstring；前端 camelCase。单文件 ≤800 行、单函数 ≤50 行、嵌套 ≤4、圈复杂度 ≤10（CLAUDE.md 阈值）。**全部改动向后兼容**，不动既有契约现有字段。

### 3.1 后端（services/quant）

| 文件 | 类型 | 一句话意图 | 关联项 |
|---|---|---|---|
| `app/config.py` | [改] | `Settings` 增 `agent_max_retries:int=2` / `agent_retry_base_ms:int=500` / `agent_retry_max_ms:int=8000` / `agent_drain_timeout_seconds:float=8.0`（字段名即 `AGENT_*` 环境变量名，可经 .env 覆盖） | H1/H4 |
| `app/agent/llm/anthropic_client.py` | [改] | `stream()` pre-stream 有界重试 + 退避抖动；401/403→`AGENT_AUTH_ERROR`；抽 `_open_stream_with_retry` helper | H1/H6 |
| `app/agent/llm/openai_client.py` | [改] | 同构 pre-stream 重试 + 鉴权码（共用退避策略，建议抽 `agent/llm/retry.py` 复用）；**`[v2 RN-2]` 请求增 `stream_options.include_usage=true` 以取得流式 usage** | H1/H6/H5 |
| `app/agent/llm/retry.py` | [新] | 退避计算（指数 + 抖动 + `Retry-After` **仅整数秒**封顶）与可重试判定的纯函数，两 client 复用，便于单测；**`[v2 RN-2]` 兼放 `normalize_usage(provider, raw)` usage 归一纯函数** | H1/H5 |
| `app/agent/tools/base.py` | [改] | `Tool` 基类增 `validate(args)->str|None`（默认 None）；`ToolContext` 增可选 `cancel_check` | H2/H11 |
| `app/agent/tools/registry.py` | [改] | `execute` 前置 `tool.validate`；兜底错误携带 `type(exc).__name__: msg[:200]`（脱敏有界） | H2/H7 |
| `app/agent/tools/market_tools.py` | [改] | 为 `list_instruments`/`get_candles`/`get_market_summary` 实现 `validate` | H2 |
| `app/agent/tools/strategy_tools.py` | [改] | 为 `run_backtest`/`compare_strategies` 实现 `validate`；回测 helper 接入 `cancel_check`（P2） | H2/H11 |
| `app/agent/tools/run_tools.py` | [改] | 为 `get_run_*` 实现 `runId` 正整数校验 | H2 |
| `app/agent/tools/ui_tools.py` | [改] | 为 `show_chart` 实现 bar 枚举 / ts 顺序校验 | H2 |
| `app/agent/manager.py` | [改] | `get_or_create` 改 `async` + `asyncio.Lock`，确保每 convId 唯一会话；`handle` await 之；**`[v2 RN-4]` `clear` 的 `pop()` 写入纳入同一锁** | H3 |
| `app/agent/loop.py` | [改] | `message_done.usage` 经 **`normalize_usage`** 透传到 `message_end`（`[v2 RN-2]`）；run 结束结构化 `logger.info`；命中轮上限追加禁用工具的终轮总结（终轮检查 `cancel`、不回环，`[v2 RN-7]`） | H5/H9 |
| `app/agent/services.py` | [改] | 长回测分块循环周期性 `cancel_check`（P2，协作式取消） | H11 |
| `app/api/agent.py` | [改] | `_drain` 加 `asyncio.wait_for` 超时 + 孤儿 cancel/warning | H4 |
| `app/agent/__init__.py` | [改] | 如新增 `AgentCancelled` 异常 / retry 导出则补 | H11 |

### 3.2 前端（apps/web）

| 文件 | 类型 | 一句话意图 | 关联项 |
|---|---|---|---|
| `components/Agent/AgentPanel.tsx` | [改] | `setConnected` 包装：瞬断复位 `running` **+ 收束在途气泡为"已中断"**（`[v2 RN-3]`）；`history` 走 `reconcileHistory`；`send` 增 `connected` 守卫；错误气泡"重试" | H8a/H8b/H8c/H12 |
| `lib/agentReducer.ts` | [改] | **`[v2 RN-1]` 新增 `reconcileHistory(prev, history)` 纯函数**：DB 历史整体重建已完成轮 + 仅保留唯一仍 `streaming` 气泡；**禁止跨 `uuid`/`DB-int` 边界按 id 去重**（撤销 v1 的 `mergeHistory` merge-by-id） | H8b |
| `lib/agentTypes.ts` | [改] | `message_end` 事件 data 增可选 `usage`；`ChatMessage` 增可选 `usage`/`error`/`canRetry` 字段 | H5/H12 |
| `components/Agent/AgentMessageList.tsx` / `AgentMessageBubble.tsx` | [改] | 渲染可选 usage 行 / 错误重试入口（极简、复用设计 token） | H5/H12 |

### 3.3 配置 / 文档

| 文件 | 类型 | 一句话意图 |
|---|---|---|
| `.env.example` | [改/新] | Agent 段补注释：`AGENT_MAX_RETRIES` / `AGENT_RETRY_BASE_MS` / `AGENT_RETRY_MAX_MS` / `AGENT_DRAIN_TIMEOUT_SECONDS`（占位 + 默认值说明，**不写真实密钥**）。**`[v2 RN-7]` 若 `.env.example` 不存在则新建并仅含占位值** |
| `README.md`（Agent 段，若有） | [改] | 补一句重试 / 鉴权错误码 / token 透明化的运维说明（可选，保持聚焦） |

> **不改动项（明确边界）**：不新增 DB 表 / 列（见 §5）；不改 WS 事件**类型集**与既有字段语义（仅给 `message_end` 增可选 `usage`、新增 `AGENT_AUTH_ERROR` / `tool_progress` 两个**追加**码，老前端忽略不崩）；不引入新三方依赖（`httpx`/`asyncio` 已具备）；不改桌面打包链（凭证机制迭代 1 已稳，本迭代零桌面改动）。

---

## 4. Interface design（接口设计变更）

**全部为追加式，向后兼容。** 既有 `{type,data}` 信封不变。

### 4.1 WS 事件（`/api/ws/agent/{convId}`）增量

| 方向 | type | data | 变更 |
|---|---|---|---|
| S→C | `message_end` | `{id, usage?: {inputTokens?, outputTokens?}}` | **兑现** §4.1 既有契约：补 `usage`。**`[v2 RN-2]` `usage` 为 `normalize_usage` 归一后的 camelCase 形状**（Anthropic `input_tokens`/OpenAI `prompt_tokens` 等原始字段名**不外泄**）；取不到即省略 `usage`；老前端忽略 |
| S→C | `error` | `{code:"AGENT_AUTH_ERROR", message}` | **新增稳定错误码**：401/403 鉴权失败，前端渲染凭证引导态 |
| S→C | `tool_progress`（P2） | `{id, name}` | **新增可选心跳**：长工具进行中信号；老前端忽略 |

> 既有码 `AGENT_NOT_CONFIGURED` / `LLM_HTTP_ERROR` / `LLM_NETWORK_ERROR` / `TOOL_LOOP_LIMIT` / `BUSY` 语义不变。新增码命名遵循"大写蛇形、稳定不改名"（CLAUDE.md §五：对外错误码更名等同破坏性变更）。

### 4.2 REST

**无变更。** `/api/agent/config`、`/api/agent/conversations/{id}/messages`、`DELETE /api/agent/conversations/{id}` 签名与响应不变。

### 4.3 工具校验失败的回喂形态

校验失败经 `tool_result` 事件回流：`{id, name, ok:false, summary:"参数无效：bar 必须为 1m/5m/15m/1H/4H/1D 之一"}`，并作为 `ToolResultBlock(is_error=True)` 喂回模型（与现有 OKX/异常失败同一通路），模型据此在下一轮纠正入参——**不新增事件类型**。

### 4.4 重连重建（`[v2 评审修复 RN-1]`，纯前端、零协议变更）

服务端**每条新 WS 连接**都会先 `ready` 再 `history`（api/agent.py:46-50，已核验），重连即收到一次权威 `history`。**本迭代不改这一服务端行为、不加任何新 WS 事件**——重建逻辑全部落在前端 `reconcileHistory`（§3.2）：以 DB `history` 整体重建已完成轮、仅保留唯一仍 `streaming` 的在途气泡、**绝不跨 `uuid`(客户端流式 id)/`DB-int`(history id) 边界按 id 去重**。配合 H8a 在断连时收束在途气泡，达成"重连不闪烁 / 不重复 / 不孤儿"。

---

## 5. Data model（数据模型）

**本迭代不改持久化 schema。** `AgentMessage` 表（`db/models.py`，`id/conversation_id/role/content_json/created_ts`）保持原样：

- usage 仅作为**运行期可观测信息**经 WS `message_end` 透传 + 后端结构化日志，**不落库**（避免库膨胀，且 token 计数非"对话可读历史"必需；与迭代 1"仅落对话可读历史"一致）。
- 工具入参校验、重试、drain 超时均为**运行期行为**，无持久化形态。
- 前端运行态（`messages` 流、`running`、`connected`、`usage` 展示）仍**不入** Zustand 持久化；仅 `agentConversationId` 持久化以便重连恢复（迭代 1 既有口径，不变）。

> 结论：**零 DB 迁移、零 store 版本号变更**——降低本加固迭代的回归面，符合"最小改动"原则。

---

## 6. Testing & acceptance criteria（测试与验收）

### 6.1 单元 / 集成测试（沿用 pytest / vitest，离线、不依赖真实网络）

**后端（`services/quant/tests/`）：**
- `test_agent_llm_retry.py`（[新]）：用可编排的假 `httpx`（`httpx.MockTransport` 或注入桩）模拟 `429→200`、`503→503→200`、`ConnectError→200`，断言**最终成功且只产出一份事件**（不重复）；模拟"已 yield text_delta 后断连"断言**不重试**、emit `LLM_NETWORK_ERROR`；`401/403` 断言 emit `AGENT_AUTH_ERROR` 且**零重试**；退避计算纯函数（`retry.py`）覆盖指数 / 抖动上界 / `Retry-After` **整数秒封顶 + HTTP-date 回退不抛错**（`[v2 RN-5]`）。
- `test_agent_usage_normalize.py`（[新，`[v2 RN-2]`]）：`normalize_usage` 正例——Anthropic `{input_tokens,output_tokens}`、OpenAI `{prompt_tokens,completion_tokens}` 均归一为 `{inputTokens,outputTokens}`；缺键 / `None` / 空 dict 回退 `None`；断言**不外泄原始 snake_case 字段名**。
- `test_agent_tools.py`（[改]）：补每个有约束工具的 `validate` 正反例——缺必填、`bar` 越界、`startTs≥endTs`、`limit` 越界、`runId≤0`、`instType` 非法→断言 `ok=False` 且 summary 指明原因，且 `execute` **未被调用**（用 spy）。
- `test_agent_loop.py`（[改]）：断言 `message_end` 携带**归一后 camelCase** `usage`（假 LLM 在 `message_done` 给原始 snake_case usage，断言外发为 `{inputTokens,outputTokens}`，`[v2 RN-2]`）；断言命中 `max_tool_rounds` 时触发**禁用工具的终轮总结**再 `done`，且终轮**已 cancel 时不再调用 LLM**（H9 / `[v2 RN-7]`）。
- `test_agent_manager.py`（[新]）：并发 `asyncio.gather` 多次 `get_or_create(same_conv)` 断言**返回同一对象**（H3 锁生效，无脑裂）。
- `test_agent_ws.py`（[改]）：模拟"run 进行中客户端断连"断言 `_drain` 在 `agent_drain_timeout_seconds` 内返回、孤儿被 cancel（H4，不挂死）。
- `test_agent_network.py`（[改，opt-in `-m network`]）：真实 KIMI 往返保持绿；可加一次"故意触发限流后退避成功"的弱断言（若环境允许）。

**前端（`apps/web/**/*.test.ts`）：**
- `lib/agentReducer.test.ts`（[改]，`[v2 RN-1]`）：`reconcileHistory`——(a) 本地为空时用 history 初始化（等价 overwrite）；(b) 本地含 DB-int id 的已完成轮 + history 同轮 → **重建后不重复**（核心回归：旧 `mergeHistory` 在此用例会因 uuid≠DB-int 而重复）；(c) 本地含唯一 `streaming` 气泡 → 保留；(d) **"断连取消落盘半轮后重连"专项用例**：本地半轮气泡已被 H8a 收束为 `streaming:false` + history 含该半轮 → 断言**只出现一次、无孤儿**。
- `components/Agent/*.test.tsx` 或 `lib` 级：瞬断（connected true→false）复位 `running` **且把在途 `streaming` 气泡收束为"已中断"**（H8a / `[v2 RN-3]`）；断线 `send` 被守卫拦截不产生幽灵气泡（H8c）。

### 6.2 验收标准（Definition of Done，对齐"稳健可靠、正确交互"）

1. **可靠性**：注入 `429/503/网络瞬断`（pre-stream）时，Agent 自动退避重试并最终成功返回，用户无感；超过重试上限才如实报错。已 yield token 后断连**不**产生重复输出。
2. **鉴权**：配置错误 / 过期 key 时，前端显示**可操作引导**（指向凭证文件 + 提示重启），而非泛化 HTTP 报错；该路径零无谓重试。
3. **交互正确性**：模型产出非法 / 缺失工具入参时，得到明确"参数无效：…"回喂并能在下一轮**自我纠正**；工具内部异常的根因（类型 + 简短消息）对模型 / 用户可见且**不泄露**密钥 / 栈。
4. **并发**：同一会话双窗口打开，复用同一会话、BUSY 守卫生效、无消息错乱 / 无并发烧配额。
5. **生命周期**：运行中关闭页面 / 断连，后端在 `agent_drain_timeout_seconds` 内完成清理、孤儿任务被 cancel 并记录，**无挂死**。
6. **可观测**：`message_end` 携带 `usage`；后端每 run 输出一条结构化日志（轮数 / 每工具耗时 / stop_reason / token），**不含密钥**。
7. **前端韧性**：瞬断后面板不假死（`running` 复位）、重连不闪烁 / 不丢在途气泡、断线发送被守卫且有提示。
8. **回归零破坏**：迭代 1 的后端 123 + 前端 131 既有测试**保持全绿**；`pnpm test`（后端 pytest 非 network + 前端 tsc/vitest）通过；桌面 `npm run smoke` / `verify-dist` 不受影响（本迭代零桌面改动）。
9. **门禁不回退**：迭代 1 全部硬约束（密钥只在后端 / 不入库 / 不进日志、OKX 不可达不假数据、秒级 UTC、双标准抽象、ui-action 驱动左图）继续成立。

---

## 7. Risks & mitigations（风险与缓解）

| # | 风险 | 缓解 |
|---|---|---|
| R1 | 重试导致**重复输出**（最严重正确性风险）| 铁律：**仅 pre-stream 重试**，一旦 yield 过任何归一化事件即不重试；`test_agent_llm_retry.py` 专测"已流式后断连不重试" |
| R2 | 重试放大对限流端点的压力 | 指数退避 + 抖动 + 尊重 `Retry-After`；`agent_max_retries` 默认仅 2；429 不立即重打 |
| R3 | 校验过严误杀模型合法调用 | 校验只覆盖**确定性非法**（缺必填 / 枚举越界 / ts 逆序 / 范围越界），不臆测语义；正反例单测护栏；放行集尽量宽 |
| R4 | `get_or_create` 改 `async` 牵连调用方 | 仅 `manager.handle` 一处调用（已核验 manager.py:70）；改 `await` 即可。**`[v2 评审修复 RN-4]`：`cancel` 仅 `.get()` 读安全；`clear` 实为 `.pop()` 写入（manager.py:80-85），随 H3 一并加锁**（原 v1 表述"cancel/clear 读字典无需锁"对 `clear` 有误，已更正） |
| R5 | drain 超时误杀正常收尾的慢回测 | 默认 8s 足够"轮间响应 cancel"；长回测经 H11 协作式取消可更快响应；超时仅作**兜底**，并记 warning 可观测 |
| R6 | `message_end` 增 `usage` 破坏老前端 | 纯**追加**可选字段，前端 reducer 对未知字段忽略（既有 `reduceAgentEvent` 已是白名单分派）；TS 类型设 `usage?` |
| R7 | 新错误码 `AGENT_AUTH_ERROR` 老前端不识别 | 前端 `error` 分支统一展示 `message`（AgentPanel.tsx:88-91 已是通用兜底）；新增专属渲染为增强、非必需 |
| R8 | 结构化日志误含敏感信息 | 只记元数据（名 / 布尔 / 数值 / 截断 stop_reason），**不记** key / 用户原文 / 工具原始 payload；评审 checklist 第 7 条复核（CLAUDE.md §八） |
| R9 | 前端 `mergeHistory` 合并逻辑引入消息错位 | 纯函数 + 充分单测（去重 / 顺序 / 在途保留三类用例）；合并键用稳定 message id |
| R10 | 范围蔓延成大重构 | 严守"增量、向后兼容、不新增依赖、不改契约现有字段"；P0/P1/P2 分级，P0 先行，P2 可裁 |
| R11 `[v2 RN-1]` | `reconcileHistory` 误把已完成轮 / 在途气泡重复或丢失 | **DB 历史为已完成轮唯一权威、仅保唯一 `streaming` 气泡、禁止跨 uuid/DB-int 按 id 去重**；专项单测覆盖"重连不重复"与"取消落盘半轮重连只现一次"两类回归（§6.1） |
| R12 `[v2 RN-2]` | usage 归一遗漏某 provider 字段或外泄原始字段名 | `normalize_usage` 纯函数 + 正反例单测；只产出 camelCase `{inputTokens,outputTokens}`，缺失回退 `None`；OpenAI 需 `stream_options.include_usage` 否则 usage 缺省（容忍） |
| R13 `[v2 RN-3]` | H8a 收束在途气泡误杀正常完成的气泡 | 仅在连接 `true→false` 且气泡仍 `streaming` 时收束；正常 `done` 路径先于断连置 `streaming=false`，不受影响；组件级单测覆盖 |

---

## 8. 附：实施注意事项（给下游 Subtask #2）

- **跨平台命令**：建目录用 `python -c "from pathlib import Path; Path('…').mkdir(parents=True, exist_ok=True)"`；勿用 `mkdir -p` / `&&` / 反斜杠续行（见任务环境约定）。
- **守阈值**：新增 helper 控制在 50 行 / 圈复杂度 10 / 嵌套 4 内；重试循环用 guard clause + 查找表表达可重试集，避免布尔阶梯（CLAUDE.md §六）。
- **错误显式传播**：校验失败返回结构化 `ToolResult(ok=False)` 而非吞掉；重试穷尽后**显式**报错；勿为"理论不会发生"分支加防御 `try`（CLAUDE.md §五）。
- **测试先行**：每个被加固的缺口**先写能复现的失败用例再改实现**（CLAUDE.md §七：修 bug 必先写复现测试）——例如先写"429 不重试导致直接报错"的失败断言，再加重试令其转绿。
- **契约稳定**：所有改动追加式；不改既有 WS/REST 字段语义、不改 store 版本、不改 DB schema——把回归面压到最小。
- **凭证不入库**：本迭代不应触碰真实 key；真实 key 仅在 gitignored `services/quant/.env`；下游 Subtask #3 提交务必**精确 `git add <具体路径>`、严禁 `git add -A`**，复核 diff 无任何 `sk-kimi-`（CLAUDE.md §八第 7 条 / 迭代 1 既定规则）。
- **设计 token 复用**：前端新增 usage 行 / 重试入口 / 断连提示全部用 `var(--muted)/--accent/--short/--border`，自动适配明暗主题，沿用 `transition-* ease-smooth`，保持顶级设计一致性。

---

（全文覆盖 Overview / 技术设计（A–E 五组，逐项锚定 file:line）/ 文件清单 / 接口变更 / 数据模型 / 测试验收 / 风险缓解 / 实施注意，远超 800 字。本迭代为**加固增强**，所有改动向后兼容、零新增依赖、零 DB 迁移、不破坏迭代 1 既有测试与对外契约。**v2 由 Subtask #1 评审修复完成**——见上方「## 评审记录」与下方「## 评审结论」。）

---

## 评审结论（Review Verdict · Subtask #1）

**结论：有条件通过（Approved with Conditions）。**

本方案**整体质量高**：v1 的源码锚点经逐文件回核**高度准确**（行号误差 ≤3 行、11 个工具名 / 入参名逐一吻合），五维缺口（可靠性 / 正确性 / 并发生命周期 / 可观测 / 前端韧性）定位真实、设计向后兼容、范围收敛、与 `CLAUDE.md` 阈值与约定一致。评审中发现 **1 个 P0、2 个 P1、4 个 P2**，**全部 P0 / P1 已在正文就地修复并标注 `[v2 评审修复]`**：

- **P0 RN-1**（重连 `mergeHistory` 去重键跨 `uuid`/`DB-int` 不成立 → 整轮重复）：已重写 H8b 为「DB 历史权威重建 + 仅保唯一在途气泡 + 禁止跨 id 空间去重」（`reconcileHistory`），并补"重连不重复 / 取消落盘半轮只现一次"两类回归测试。
- **P1 RN-2**（usage 跨 provider 字段名不一致且与 camelCase 契约不符 / OpenAI 流式需 `include_usage`）：已加 `normalize_usage` 归一 + `stream_options.include_usage` + 测试。
- **P1 RN-3**（H8a 未与"H4 断连即 cancel run"对齐 → 在途气泡永悬）：已令 H8a 断连时收束在途气泡为"已中断"，作为 RN-1 重建不重复的前置。
- **P2 RN-4 / RN-5 / RN-6 / RN-7**：clear 写入纳锁并更正 R4 表述；`Retry-After` 仅整数秒解析、HTTP-date 回退；退避墙钟边界与可取消性点明；H9 终轮检查 cancel / 不回环、`.env.example` 不存在则新建——均已就地补强。

**放行条件（交由下游 Subtask #2 落实，作为验收红线）：**
1. **H8b 必须按 v2 修订实现**——使用 `reconcileHistory`（DB 权威重建 + 仅保唯一在途气泡），**严禁**沿用 v1 的 merge-by-id；并落地 §6.1 的"重连不重复 / 取消半轮只现一次"两类回归测试，**这两条测试必须实绿**。
2. **usage 必须经 `normalize_usage` 归一后外发**，`message_end.usage` 只出现 camelCase 字段、绝不外泄原始 snake_case；OpenAI 路径置 `stream_options.include_usage`。
3. H8a 与 H4 联动：断连时**既复位 `running` 又收束在途气泡**，二者缺一不可。
4. 守住 v1 既有硬约束（密钥只在后端 / 不入库 / 不进日志、OKX 不假数据、秒级 UTC、双标准抽象、ui-action 驱动左图）与 `CLAUDE.md` 阈值；迭代 1 后端 123 + 前端 131 既有测试保持全绿。

满足上述条件即视为达成"稳健可靠、正确进行 Agent 交互"的全局目标。**本评审仅修改 `spec.md`，未触碰任何源码，未执行 `git commit`。**

---

## 实施过程发现的方案缺陷（Issues Found During Implementation · Subtask #2）

> 由下游实现工程师（Subtask #2）在落地 v2 时记录。下列均已按"修正后的方案"实现，未静默偏离。

| # | 维度 | 位置 | 问题 | 修正处置 |
|---|---|---|---|---|
| IMPL-1 | 完备性 / 一致性 | §3.3 + `desktop/scripts/build-agent-env.mjs` | **新增 `AGENT_*` 知钮与"零桌面改动"声明相冲突**：§3.1 新增 4 个用户可调环境变量（`AGENT_MAX_RETRIES`/`AGENT_RETRY_BASE_MS`/`AGENT_RETRY_MAX_MS`/`AGENT_DRAIN_TIMEOUT_SECONDS`），但桌面打包脚本 `build-agent-env.mjs` 用**显式 `AGENT_KEYS` 白名单**仅拷固定几个键。若用户在 `services/quant/.env` 设置新知钮，打包链**不会**把它们写入 `agent.env` → 桌面端永远回落默认值、用户无法修改，违背全局目标"凭证 / 配置能正确打包到桌面端、后续用户可继续修改"。 | **已修正（最小、向后兼容）**：把 4 个新键追加进 `AGENT_KEYS` 白名单。脚本逻辑 `if (parsed[key] != null)` 保证**缺省即不写**（回落 `config.py` 默认值），无占位污染、零破坏既有打包行为。这是对"零桌面改动"的**必要例外**——属一行级白名单扩列，非打包链重构。 |
| IMPL-2 | 适度性 / 正确性 | §2.B H2、各 `*_tools.py` | **H2 示例的 `bar` 枚举窄于工具实际支持集**：§2.B 列举 `bar ∈ {1m,5m,15m,1H,4H,1D}`（缺 `30m`），但 `market_tools`/`strategy_tools`/`ui_tools` 的真实 `_BAR_ENUM` **含 `30m`**。若按示例严格校验会**误杀合法的 30m 调用**（触犯 R3"校验过严误杀"）。 | **已修正**：各 `validate` 一律校验**工具自身已声明的 `_BAR_ENUM`**（含 `30m`），与 `input_schema` 对外契约一致，避免内外不一致与误杀。§2.B 的枚举仅作示意，不作为校验口径。 |
| IMPL-3 | 适度性（范围收敛） | §2.A H10 / §2.C H11 / §4.1 `tool_progress` | 三个 **P2「加固未来」**项的落地边界确认（均为设计明示可选）：H10 连接复用、H11 长回测线程池内协作式取消、`tool_progress` 心跳。 | **按设计的 P2 定位收敛**：① **H10** 暂不实现（设计明示"属优化项，不影响正确性"；新建 `httpx.AsyncClient` 每次尝试新建，正确性不受影响）；② **H11** 实现"插桩"——`ToolContext.cancel_check` 字段 + loop 注入 `lambda: self._cancel` + `run_compare` **逐策略**协作式取消（真实生效，非死代码）；单次回测为 `run_in_threadpool` 原子调用，**线程内取消需改回测引擎**，超出本迭代范围、依 H4 drain 超时兜底，故**留待后续**；③ **`tool_progress` 心跳**暂不发（设计明示"桌面 / localhost 非必需"）。三者均未削弱 P0/P1 的稳健性目标。 |
