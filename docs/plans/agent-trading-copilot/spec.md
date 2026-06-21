# Agent Trading Copilot — 设计方案 (spec)

> 文档版本：**v2**（方案评审与修复 / Subtask #1 产出 —— 基于 v1 设计稿就地评审并修复全部 P0/P1）
> Feature slug：`agent-trading-copilot`
> 作者：Solution Architect（Anthropic Eng）｜评审：Senior Reviewer（Anthropic Eng）
> 关联全局目标：完成所有开发，稳健可靠，能够正确进行 Agent 交互。

---

## 评审记录（Review Notes / Subtask #1）

> 评审维度：**可行性 Feasibility**（能否用现有技术栈构建）｜**完备性 Completeness**（边界 / 错误路径 / 上线遗漏）｜**一致性 Consistency**（是否与 `CLAUDE.md` 及既有代码约定冲突）｜**适度性 Right-sizing**（范围是否合理、是否过度 / 欠规格）。

**已对照真实源码核验的事实（均通过，构成本方案的可行性基线）：**

- `db/repositories.py`：`list_strategies` / `get_run` / `list_runs` / `list_trades` / `list_equity` 函数名与签名与方案完全一致；`get_run` 返回 `StrategyRun`，绩效在 `run.metrics_json`（工具读取后解析）。
- `okx/rest_client.py`：`get_instruments`（**实为 OKX `tickers` 端点**，单次即返回该 instType 全量并自带 `last/open24h/vol24h/volCcy24h/changePct`，已按成交额降序——故"列出成交额最高标的"可直接实现，无需额外排序逻辑）、`get_candles`、`get_ticker` 均与方案一致。
- WS 事件信封：`ws_market.py:106` 的 `_send_event` 即 `json.dumps({"type": …, "data": …})`，方案沿用 `{type,data}` 信封一致；`ReconnectingWS(path,onEvent,onState)`（`lib/ws.ts`）可直接派生 `agentWS`。
- `store.ts` 现 `version: 7` 且已有 `migrate` 逐版补默认态机制 → 迁移到 **v8** 正确。
- `config.py`：含 `proxy_url` 属性、`extra="ignore"`、字段名即环境变量名（大小写不敏感）→ 新增 `agent_*` 字段可被 pydantic 直接从 env/`.env` 读取；R5 引用的 `settings.proxy_url` 成立。
- 桌面端：`config.js · backendEnv({mode,dbPath})` 既有 `DB_PATH→userData` 注入机制、`electron-builder.yml · extraResources`（backend/web）均与方案一致，可同构扩展 `AGENT_*` 与 `resources/agent`。
- 安全：`.gitignore` 已覆盖 `.env` / `.env.local`（匹配任意层级，含 `services/quant/.env`）与 `desktop/resources/{web,backend}`；R3 凭证不入库成立。
- `page.tsx:483-485` 右侧 `aside` 仅一处挂 `<StrategyPanel/>` → 改 `<RightPanel/>` 为单点改动；`components/ui/index.tsx` 已含 `Tabs` 可复用。
- 依赖：`httpx`/`websockets`/`pydantic` 已是既有依赖 → "零新增三方依赖"成立。

**问题清单与处置：**

| # | 维度 | 严重度 | 问题 | 处置 |
|---|---|---|---|---|
| P1-A | 一致性 / 需求符合 | **P1** | 默认模型取 `kimi-k2.5`（示例实测值），与任务**显式给定**的 `kimi-k2.6` 冲突；静默偏离会被判为"未按需求集成凭证"。 | ✅ 已修复：默认改为任务给定的 `kimi-k2.6`，`.env.example` 同时标注 `kimi-k2.5` 为"示例已验证回退值"；client 对 model-not-found 显式报错；network 冒烟断言所配 model 真实可用。见 §1 / §2.2 / R1。 |
| P1-B | 完备性 / 一致性 | **P1** | `tool_result` 持久化与"重开会话重建 messages"不自洽：§2.4 的 `LlmMessage.role` 含 `tool`，但 §4.2 / §5.1 的 role 仅 `user/assistant`，未定义工具结果如何落库及按 provider 还原 → 直接实现会导致 resume / 历史重放丢失工具上下文。 | ✅ 已修复：统一 role 取值 `{user,assistant,tool}`；`content_json` 存归一化 block 序列并保留 `tool_use.id ↔ tool_result.tool_use_id` 关联；定义 provider 还原映射（Anthropic：tool_result 装入 user 轮；OpenAI：`role:"tool"` 消息）。见 §4.2 / §5.1 / §5.2。 |
| P1-C | 完备性 / 上线 | **P1** | 打包凭证链未定义失败行为：`build-agent-env.mjs` 在源 `services/quant/.env` 缺 `AGENT_API_KEY` 时若静默通过，会打出"无密钥"的桌面包，违背"真实集成凭证 + 正确打包进桌面端"硬要求。 | ✅ 已修复：构建期源 `.env` 缺 key 时**显著告警**并仍产出占位文件；`verify-dist`/`smoke` 断言 `resources/agent/agent.env` 存在；明确 dev 经 `services/quant/.env`（pydantic `env_file`）、prod 经子进程 env 注入 `AGENT_*`。见 §3.3 / §6.2#7 / R4。 |
| P1-D | 完备性 / 稳健性 | **P1** | 仅定义了显式 `cancel`，未定义 **WS 断连 / 页面关闭** 中途的 run 语义 → 可能产生"用户已离开仍在消耗 LLM 配额"的孤儿 run，违背全局目标"稳健可靠"。 | ✅ 已修复：WS 断连即走与 cancel 同一中断路径（落盘已完成轮次后停止 stream）；重连经 `history` 重放恢复。见 §2.3 / R13。 |
| P2-1 | 可行性 | P2 | 示例仅验证 **文本** SSE，未覆盖 KIMI 端点的 **tool_use** 流式事件（`content_block_start(tool_use)` / `input_json_delta`）；属合理假设但未实证。 | 实现期：network 冒烟覆盖一次"含工具调用"的真实往返；以离线样本固化解析。见 §2.2 / §6.1 / R14。 |
| P2-2 | 完备性 | P2 | 长会话 / resume 时 `messages` 无限增长，最终超模型上下文。 | 重建 / 续接时对历史按近 N 轮裁剪（超限丢弃最旧或摘要化）。见 §5.2 / R15。 |
| P2-3 | 一致性（CLAUDE.md 阈值） | P2 | `AgentSession.run` 含"多轮循环 + 流式累积 + 顺序执行工具"，易破 50 行 / 圈复杂度 10 阈值。 | 拆 `_stream_assistant_turn()` / `_execute_tool_calls()` 等辅助函数。见 §2.3。 |
| P2-4 | 一致性 | P2 | `lib/agentActions.ts` 依赖 store 真实 setter 名，方案所列需在实现时与 `store.ts` 核对，避免漂移。 | 实现期对照 `store.ts` 确认 setter 名 / 签名并单测覆盖（已在 §6.1 规划）。 |
| P2-5 | 可行性 | P2 | `get_market_summary` 写"rest 近端 candles / ticker"，但 `get_ticker` 仅返回 `last`（无 24h 涨跌 / 量）；24h 字段应取自 tickers / `get_instruments` 同源。 | 摘要工具复用 tickers 行情字段，勿依赖只回 `last` 的 `get_ticker`。见 §2.4。 |
| P2-6 | 稳健性 | P2 | 长耗时回测（分钟级）期间 WS 可能空闲，需确保不被中间层 idle 超时断开。 | 工具执行期发"running"心跳或确认无 idle 超时；cancel 检查已具备。 |
| P2-7 | 安全 / 流程 | P2 | 真实 key 在 `services/quant/.env`，下游 Subtask #3 若 `git add -A` 有泄露风险。 | 实现 / 提交期**精确暂存**具体路径，严禁 `git add -A`（与任务板 Subtask #3 规则一致）。见 §8。 |
| P2-8 | 可行性 | P2 | KIMI 仅支持 Anthropic 标准（OpenAI 格式 403）；"支持两标准"中 OpenAI 路径无法用所给凭证 live 实测。 | §2.2 已说明：OpenAI 适配以离线 SSE 样本单测验证（code-complete + unit-tested），非 live 实证；如需 live 可接 DeepSeek / 本地 vLLM。 |
| P2-9 | 一致性 | P2 | Agent WS 无鉴权（与既有 market/paper WS 同为 localhost-only）。 | 与既有约定一致，Phase-1 localhost 可接受；留作未来加固。 |

> **结论摘要**：本方案**无 P0 级阻断项**；**全部 4 个 P1 已在本 v2 文档正文就地修复**（无遗留 P0/P1）。P2 为实现期建议 / 确认项，已在文末 §评审结论 列为放行条件。

---

## 1. Overview（概述）

本方案为 Aragon Trading 量化模拟交易工作台新增一个**对话式 AI 助手（Agent Trading Copilot）**。用户可以在软件右侧面板里用自然语言与 Agent 对话，Agent 通过**工具调用（tool use）**真实地操作本系统：查看现货 / 合约标的与行情、拉取并在左图展示 K 线走势、列出与运行交易策略回测、读取回测 / 历史 run 的绩效指标与成交明细、横向对比多个策略。Agent 不是"另起炉灶"的黑盒，而是把既有后端能力（OKX 适配层、策略引擎、回测器、run 仓储）包装成一组**确定性、可审计**的工具，让模型在受控边界内驱动工作台。

交互形态上，右侧原本只承载 `StrategyPanel`（交易策略）的 `aside` 区被改造成**多页签容器**：第一个页签是「AI 助手」对话面板（默认激活），第二个页签是原「交易策略」面板，两者通过顶部分段页签切换、状态互不破坏。对话面板遵循人机交互最佳实践：流式增量渲染助手回复、工具调用以可折叠卡片可视化（名称 / 入参 / 结果摘要）、空态提供建议提示词、底部 composer 支持多行输入与发送中断（停止），整体沿用项目既有设计 token（`--accent` / `--surface` / `--border` 等）与暗色 / 亮色主题，做到美观、优雅、与现有工作台一致。

模型接入遵循"**密钥只在后端**"的既有铁律：Agent 的 agentic loop 跑在 Python 后端（FastAPI），通过 WebSocket（`/api/ws/agent/{conversationId}`）把流式事件推给前端；前端只渲染与发消息，永不持有 API Key。模型层抽象为**双标准适配**（Anthropic Messages API 与 OpenAI Chat Completions API），由 `.env` 配置切换；本期默认接入 **KIMI（默认 `kimi-k2.6`——任务显式指定值；`kimi-k2.5` 为 `docs/examples` 中已实测可用的回退值，二者同走 Anthropic 标准，`https://api.kimi.com/coding`）**，凭证写入 `.env`（开发）/ 打包进桌面端可写副本（生产），用户可随时修改。整个能力对 Electron 桌面端打包透明：复用既有 `extraResources` + userData 注入机制，确保安装即用、密钥可改。

设计参考开源项目 `earendil-works/pi` 的 `packages/agent` 核心组件：其 **AgentLoop（agentic 迭代器）+ AgentTool（工具接口：name/description/schema/execute）+ 内部消息↔LLM 消息转换 + 流式事件（agent_start / message_update / tool 执行 / turn_end）+ 模型 provider 抽象**。我们将这些概念用 Python 在后端等价实现，并按本项目的工具集与流式协议落地。

---

## 2. Technical design（技术设计）

### 2.1 总体架构

```text
┌───────────────────────────── apps/web (Next.js) ─────────────────────────────┐
│  page.tsx                                                                      │
│   └─ <RightPanel>  ──tabs──▶  [AI 助手] AgentPanel  |  [交易策略] StrategyPanel │
│         AgentPanel ── agentWS(convId) ──┐         ▲ ui_action 应用到 store      │
│         lib/agentWs.ts (ReconnectingWS) │         │ (setInst/setBar/setRunId…)  │
└─────────────────────────────────────────┼─────────┼─────────────────────────-─┘
                 WS  /api/ws/agent/{convId} (浏览器直连 NEXT_PUBLIC_PY_WS_URL)
                                           │
┌────────────────────── services/quant (FastAPI 后端) ─────────────────────────┐
│  api/agent.py        WS 端点 + REST(config/clear/messages)                     │
│   └─ agent/manager.py  AgentManager（进程级单例，convId→AgentSession）          │
│        └─ agent/loop.py  AgentSession：agentic loop（pi AgentLoop 等价物）       │
│             ├─ agent/llm/  provider 抽象：AnthropicClient / OpenAIClient(httpx) │
│             └─ agent/tools/  工具注册表（包装既有能力，统一 schema/execute）     │
│                  ├─ market tools ──▶ okx/rest_client.py                         │
│                  ├─ strategy/backtest tools ──▶ engine/backtester + repositories│
│                  ├─ run/metrics tools ──▶ db/repositories.py                    │
│                  └─ ui-action tools ──▶ 仅产出 directive（前端执行）            │
│  db/models.py + repositories.py  AgentMessage 持久化（可重开恢复会话）          │
│  config.py  Settings 新增 agent_* 字段（从 .env 读）                            │
└──────────────────────────────────────────────────────────────────────────────┘
```

**关键决策（与既有约定一致）：**

- **Agent loop 跑后端**：密钥不下发前端（README §安全 / `.env` 约定 / 索引"Key Conventions"）；工具直接调用后端函数，零额外 HTTP 跳转；流式走 WS，与 `/api/ws/market`、`/api/ws/paper/{sid}` 同构。
- **httpx 直连两标准端点（不引入 anthropic/openai SDK）**：与 OKX 适配层"裸 httpx/websockets、无 SDK"风格一致；可精确控制 KIMI 必需的 `User-Agent: claude-code` 头与 SSE 解析，规避 SDK 版本漂移。仅新增 1 个三方依赖以增强健壮性见 §3 备注（实际可不增）。
- **工具是既有能力的薄包装**：不重写回测 / 取数逻辑，只在 `agent/tools/` 适配为 `name + JSON Schema + async execute(ctx,args)`。
- **ui-action 工具**：Agent"查看 K 线"= 产出 `{action:"show_chart",...}` directive，经 WS `ui_action` 事件由前端应用到 Zustand store，真正驱动左图切换标的 / 周期 / 区间 / 叠加 run markers。

### 2.2 provider 抽象（openai / anthropic 双标准）

`agent/llm/base.py` 定义统一接口：

```python
class LLMClient(Protocol):
    async def stream(self, *, system: str, messages: list[LlmMessage],
                     tools: list[ToolSchema]) -> AsyncIterator[LlmStreamEvent]: ...
```

- `LlmMessage`：内部规范化消息 `{role: "user"|"assistant"|"tool", content: [...blocks]}`，block 为 `text` / `tool_use{id,name,input}` / `tool_result{tool_use_id,content,is_error}`。
- `LlmStreamEvent`：归一化流事件 `text_delta{text}` / `thinking_delta{text}` / `tool_use_start{id,name}` / `tool_use_args_delta{id,partial_json}` / `tool_use_end{id}` / `message_done{stop_reason,usage}` / `error{code,message}`。两个 provider 各自把原生 SSE 翻译成此归一化事件——**loop 与工具层对 provider 完全无感**。

`AnthropicClient`（`agent/llm/anthropic_client.py`）：
- `POST {base_url}/v1/messages`，headers：`x-api-key`、`anthropic-version`、`content-type: application/json`、`User-Agent`（KIMI 必需，默认 `claude-code/1.0`，见 examples/test_kimi_k2.5.py 实测）。
- body：`{model, max_tokens, system, messages(anthropic 格式), tools(anthropic 格式: {name,description,input_schema}), temperature, top_p, stream:true}`。
- SSE 解析（按 examples 中的事件类型）：`content_block_start`（含 `tool_use` → 触发 `tool_use_start`）、`content_block_delta`（`text_delta` / `input_json_delta` → `text_delta` / `tool_use_args_delta`）、`content_block_stop`、`message_delta`（`stop_reason`+`usage`）、`message_stop`。

`OpenAIClient`（`agent/llm/openai_client.py`）：
- `POST {base_url}/v1/chat/completions`，headers：`Authorization: Bearer <key>`。
- body：`{model, max_tokens, messages(openai 格式，含 role:"tool"), tools(openai 格式: {type:"function",function:{name,description,parameters}}), tool_choice:"auto", temperature, top_p, stream:true, stream_options:{include_usage:true}}`。
- SSE 解析：`choices[0].delta.content` → `text_delta`；`choices[0].delta.tool_calls[].function.{name,arguments}`（按 index 累积分片）→ `tool_use_*`；`finish_reason` → `message_done`。

`make_llm_client(settings)`（`agent/llm/factory.py`）按 `settings.agent_provider` 实例化，注入 `base_url / api_key / model / proxy / timeout`。

> KIMI Coding 端点仅支持 Anthropic 标准、拒绝 OpenAI 格式（403，见 examples 注释）；OpenAI 适配为其它兼容厂商（如 DeepSeek/本地 vLLM）保留，"支持两种标准"以离线 SSE 样本单测固化（code-complete + unit-tested，P2-8），不依赖 live 实证。
> **模型名与 tool_use 流（P1-A / P2-1）**：`agent_model` 经 `.env` 配置，默认 `kimi-k2.6`；`AnthropicClient` 对非 2xx 响应解析错误体并 emit `error{code=LLM_HTTP_ERROR, message}`（含 model-not-found），便于一行切回 `kimi-k2.5`。`docs/examples/test_kimi_k2.5.py` 仅实证了 **文本** SSE（`content_block_delta/text_delta`、`message_delta`、`message_stop`），**未覆盖 tool_use 流式事件**——本方案据 Anthropic 标准假定其 `content_block_start{tool_use}` / `input_json_delta` 行为一致，并以离线样本固化解析、以 `-m network` 真实"含工具往返"冒烟兜底验证。

### 2.3 agentic loop（pi AgentLoop 等价实现）

`agent/loop.py · AgentSession.run(user_text) -> AsyncIterator[AgentEvent]`：

```text
1. append user message → 持久化
2. for round in range(max_tool_rounds):                # 防失控护栏（默认 12）
3.   emit message_start(role=assistant)
4.   stream = llm.stream(system, messages, tool_schemas)
5.   累积 assistant 输出：text 片段 → emit text_delta；tool_use 片段累积 input_json
6.   on message_done:
7.     把本轮 assistant 消息（text + 全部 tool_use blocks）落入 messages + 持久化
8.     if 无 tool_use (stop_reason=end_turn): emit message_end + done; return
9.     for each tool_use（顺序执行，金融工具以读为主，顺序更易审计/限频）:
10.        emit tool_call{id,name,input}
11.        result = await registry.execute(name, input, ctx)   # 统一异常→is_error
12.        emit tool_result{id,name,ok,summary}; 若 ui-action 另 emit ui_action
13.        append tool_result block 到 messages（喂回模型）+ 持久化
14.   continue（带工具结果再进入下一轮 LLM 调用）
15. 超过 max_tool_rounds → emit error(code=TOOL_LOOP_LIMIT) + done
```

- **取消**：WS 收到 `{type:"cancel"}` 置 `asyncio.Event`；loop 在每轮 LLM 调用与每次工具执行前检查，命中即中断当前 stream（关闭 httpx 响应）并 emit `done{cancelled:true}`。
- **断连即取消（P1-D）**：WS `receive` 抛 `WebSocketDisconnect` 或连接关闭时，**走与 cancel 同一中断路径**——落盘已完成轮次后停止 in-flight stream，避免"用户已离开仍在烧 LLM 配额"的孤儿 run；重连后经 `history` 事件重放持久化历史恢复上下文。
- **函数拆分（CLAUDE.md 阈值，P2-3）**：`run()` 拆为 `_stream_assistant_turn()`（单轮流式累积）/ `_execute_tool_calls()`（顺序执行 + 回喂）等辅助函数，保证单函数 ≤50 行、圈复杂度 ≤10、嵌套 ≤4 层。
- **并发**：每个 convId 同时只允许一个进行中的 run（`AgentSession._running` 锁）；用户在进行中再发消息→排队或拒绝（emit busy 提示）。
- **system prompt**：`agent/prompts.py` 内置中文系统提示，说明助手身份、可用工具、纪律（"只用真实 OKX 数据，不臆造行情/指标""展示 K 线请调用 show_chart""回测前确认标的/周期/区间""免责声明"），并注入当前 UI 上下文（见 §2.4）。

### 2.4 工具目录（Agent 能力）

`agent/tools/base.py`：

```python
@dataclass
class ToolContext:                 # 每次 run 注入；含 DB session 工厂、当前 UI 上下文
    session_factory: Callable[[], AsyncSession]
    ui: UiContext                  # instId/bar/mode/strategyId/range（前端连接时上报）

class AgentTool(Protocol):
    name: str
    description: str
    input_schema: dict             # JSON Schema（两 provider 各自转换）
    is_ui_action: bool = False
    async def execute(self, args: dict, ctx: ToolContext) -> ToolResult: ...

@dataclass
class ToolResult:
    ok: bool
    summary: str                   # 给模型读 + 前端摘要展示（精简，避免刷爆 token）
    payload: dict | None = None    # ui-action directive 或结构化数据（前端可选用）
```

**数据 / 动作工具清单（v1）：**

| 工具 name | 入参（核心） | 后端实现路径 | 作用 |
|---|---|---|---|
| `list_instruments` | `instType: SPOT\|SWAP\|FUTURES` | `okx.rest_client.get_instruments` | 列出标的+24h行情（截断 Top N，避免超长） |
| `get_market_summary` | `instId` | rest 近端 candles / ticker | 最新价、24h 涨跌幅、量等快照 |
| `get_candles` | `instId,bar,limit,startTs?,endTs?` | `okx.rest_client.get_candles` | 拉 K 线（默认聚合摘要：首尾价、高低、均量；可选 raw 截断） |
| `show_chart` *(ui-action)* | `instId?,bar?,startTs?,endTs?,runId?` | —（仅 directive） | 驱动左图切到指定标的/周期/区间，可叠加 run markers |
| `list_strategies` | — | `repositories.list_strategies` | 列出内置+自定义策略及参数 |
| `run_backtest` | `strategyId,instId,bar,startTs,endTs,params?` | `engine.backtester.run_backtest`（线程池） | 跑回测，落库，返回 runId+metrics |
| `compare_strategies` | `strategyIds[],instId,bar,startTs,endTs` | 同 `strategies.compare` 逻辑 | 多策略横向对比榜单 |
| `get_run_metrics` | `runId` | `repositories.get_run` | run 绩效指标 |
| `get_run_trades` | `runId,limit` | `repositories.list_trades` | run 成交明细（截断） |
| `get_run_equity` | `runId` | `repositories.list_equity` | run 净值/回撤序列（下采样） |
| `list_runs` | `strategyId?,instId?,mode?,limit?` | `repositories.list_runs` | 运行历史列表 |

- **复用而非复制**：`run_backtest` / `compare_strategies` 把 `api/strategies.py` 中"取真实 candles → 预实例化策略 → 线程池跑回测 → 落库"的逻辑抽到 `engine`/`services` 复用函数（见 §3 `agent/services.py` 或直接复用现有 router 内部函数重构为可复用 helper），保证与 UI 触发的回测**结果一致**、不重复实现。
- **OKX 不可达**：工具内沿用既有 `OKXError` 语义——捕获后回 `ToolResult(ok=False, summary="OKX 暂不可达…")`，**绝不假数据**（C1 硬约束），模型据此如实告知用户。
- **输出预算**：所有列表 / 序列工具在边界处截断（Top N / 下采样）并在 summary 注明"已截断"，避免单次 tool_result 撑爆上下文 token。
- **`get_market_summary` 数据源（P2-5）**：24h 涨跌幅 / 成交量需取自 OKX `tickers`（与 `get_instruments` 同源字段 `last/open24h/vol24h/volCcy24h`），**不要**用只返回 `last` 浮点的 `get_ticker`；如需单标的摘要，复用 tickers 行（`/api/v5/market/ticker?instId=` 携带 24h 字段）或从 `get_instruments` 结果过滤。

### 2.5 关键时序（用户："用 EMA 交叉策略回测 BTC 永续最近30天并画出来"）

```text
前端 AgentPanel.send("…") ──WS──▶ AgentSession.run
  → LLM(round1) 决定调用 run_backtest{strategyId,instId:BTC-USDT-SWAP,bar,startTs,endTs}
  → tool_call 事件(前端显示卡片) → 执行(取真实K线→线程池回测→落库) → tool_result{runId,metrics}
  → LLM(round2) 决定调用 show_chart{instId,bar,startTs,endTs,runId}
  → ui_action 事件 → 前端 store: setInst/setBar/setBacktestRange/setMode("backtest")/setRunId/setChartView("run-range")
       → page.tsx 既有逻辑自动加载该区间K线 + 叠加该 run 的买卖点 markers/净值
  → LLM(round3) 输出总结文本(流式) → message_end → done
```

---

## 3. File / module change plan（文件 / 模块变更计划）

> 约定：新建文件标 **[新]**，修改标 **[改]**。前端 TS 用 camelCase，后端 Python 用 PEP8 + Google docstring，单文件 ≤800 行、单函数 ≤50 行（CLAUDE.md 阈值）。

### 3.1 后端（services/quant）

| 文件 | 类型 | 一句话意图 |
|---|---|---|
| `app/config.py` | [改] | `Settings` 新增 `agent_*` 字段（provider/base_url/api_key/model/版本/UA/温度/超时/tool 轮上限/proxy 覆盖）|
| `app/agent/__init__.py` | [新] | 包导出 |
| `app/agent/prompts.py` | [新] | 中文系统提示 + UI 上下文注入模板 |
| `app/agent/llm/base.py` | [新] | `LLMClient` 协议、`LlmMessage`/`LlmStreamEvent`/`ToolSchema` 归一化类型 |
| `app/agent/llm/anthropic_client.py` | [新] | Anthropic Messages API（httpx，SSE 解析，KIMI UA）|
| `app/agent/llm/openai_client.py` | [新] | OpenAI Chat Completions API（httpx，SSE 解析）|
| `app/agent/llm/factory.py` | [新] | 按 `agent_provider` 造对应 client，注入凭证/代理/超时 |
| `app/agent/tools/base.py` | [新] | `AgentTool` 协议、`ToolContext`、`ToolResult`、`ToolRegistry` |
| `app/agent/tools/market_tools.py` | [新] | `list_instruments`/`get_market_summary`/`get_candles` |
| `app/agent/tools/strategy_tools.py` | [新] | `list_strategies`/`run_backtest`/`compare_strategies` |
| `app/agent/tools/run_tools.py` | [新] | `list_runs`/`get_run_metrics`/`get_run_trades`/`get_run_equity` |
| `app/agent/tools/ui_tools.py` | [新] | `show_chart`（ui-action，仅产 directive）|
| `app/agent/tools/registry.py` | [新] | 汇总注册全部工具，提供 `schemas()` 与 `execute(name,args,ctx)` |
| `app/agent/services.py` | [新] | 可复用回测/对比 helper（从 `api/strategies.py` 抽取，UI 与 Agent 共用，避免重复实现）|
| `app/agent/loop.py` | [新] | `AgentSession`：agentic loop、消息累积、事件产出、取消/并发锁 |
| `app/agent/manager.py` | [新] | `AgentManager` 进程级单例：convId→AgentSession，复用 `get_paper_manager` 模式 |
| `app/api/agent.py` | [新] | WS `/ws/agent/{convId}` + REST `/agent/config`、`/agent/conversations/{id}`(GET/DELETE) |
| `app/api/strategies.py` | [改] | 把回测/对比内部逻辑改调 `agent/services.py` 复用 helper（行为不变，单测护航）|
| `app/db/models.py` | [改] | 新增 `AgentMessage` 模型（convId/role/content_json/createdTs）|
| `app/db/repositories.py` | [改] | 新增 `append_agent_message`/`list_agent_messages`/`clear_agent_messages` |
| `app/db/session.py` | [改] | `init_db` 建表自动覆盖新模型（既有 `create_all` 即可，无需手写迁移）|
| `app/main.py` | [改] | `include_router(agent.router, prefix="/api")` |
| `app/schemas.py` | [改] | 新增 `AgentConfigOut` 等响应模型 |
| `requirements.txt` | [改] | 备注：核心用既有 httpx；如选官方 SDK 再加（见备注）。默认不新增依赖 |

> 依赖备注：方案**默认零新增三方依赖**（httpx/websockets/pydantic 已具备）。若后续偏好官方 SDK，可加 `anthropic` / `openai`——但需在 `aragon-quant.spec` 的 hiddenimports 同步，且 KIMI UA 需 SDK 支持自定义 header；故 v1 取 httpx 直连更稳。

### 3.2 前端（apps/web）

| 文件 | 类型 | 一句话意图 |
|---|---|---|
| `components/RightPanel/RightPanel.tsx` | [新] | 多页签容器：AI 助手 / 交易策略，承载于 page.tsx 右侧 aside |
| `components/Agent/AgentPanel.tsx` | [新] | 对话面板根：连接 agentWS、维护消息态、处理 ui_action、渲染列表+composer |
| `components/Agent/AgentMessageList.tsx` | [新] | 消息流容器（自动滚底、加载态、空态）|
| `components/Agent/AgentMessageBubble.tsx` | [新] | 单条 user/assistant 气泡（markdown 轻渲染、流式光标）|
| `components/Agent/ToolCallCard.tsx` | [新] | 工具调用可折叠卡片：图标+name+入参+结果摘要+ok/err 态 |
| `components/Agent/AgentComposer.tsx` | [新] | 底部输入：多行 textarea、Enter 发送/Shift+Enter 换行、发送/停止按钮 |
| `components/Agent/AgentEmptyState.tsx` | [新] | 空态 + 建议提示词（点击即填充并发送）|
| `components/Agent/AgentUnconfigured.tsx` | [新] | 未配置密钥时的引导态（指向 .env / 设置 AGENT_API_KEY）|
| `lib/agentWs.ts` | [新] | `agentWS(convId,onEvent,onState)` 复用 `ReconnectingWS` |
| `lib/agentTypes.ts` | [新] | Agent 事件 / 消息 / 工具调用 TS 类型（与后端事件协议对齐）|
| `lib/agentApi.ts` | [新] | `agentConfig()`/`agentClear(convId)`（经 /api/py 代理）|
| `lib/agentActions.ts` | [新] | 纯函数：把 `ui_action` directive 映射为 store setter 调用序列（带单测）|
| `lib/store.ts` | [改] | 新增 `rightTab`('agent'\|'strategy')、`agentConversationId`，持久化+migrate v8 |
| `app/page.tsx` | [改] | 右侧 `aside` 由 `<StrategyPanel/>` 改为 `<RightPanel/>` |
| `components/ui/index.tsx` | [改] | 如需：复用既有 `Tabs`（segmented）即可，原则上不改 |
| `lib/api.ts` | [改] | 可选：把 agent REST 并入（或独立 agentApi.ts，二选一，推荐独立）|

### 3.3 桌面端打包（desktop）+ 环境

| 文件 | 类型 | 一句话意图 |
|---|---|---|
| `.env.example` | [改] | 新增 `# ---- Agent (LLM) ----` 段（占位+说明，**不写真实密钥**）|
| `services/quant/.env` | [新·gitignored] | 开发期真实 KIMI 凭证（实现者本地创建，不提交）|
| `desktop/scripts/build-agent-env.mjs` | [新] | 构建期把 `services/quant/.env` 的 `AGENT_*` 拷成 `desktop/resources/agent/agent.env`（打包用，不提交）|
| `desktop/src/lib/agent-env.js` | [新] | 运行期：首启从 `resources/agent/agent.env` 播种到 `userData/agent.env`（用户可改），解析为 KEY=VALUE |
| `desktop/src/lib/config.js` | [改] | `backendEnv()` 合并 `agent-env.js` 解析出的 `AGENT_*` 注入后端子进程 |
| `desktop/electron-builder.yml` | [改] | `extraResources` 增 `from: resources/agent → to: agent` |
| `desktop/package.json` | [改] | `dist`/`smoke` 前置链增 `build:agent-env` |
| `.gitignore` | [改] | 增 `desktop/resources/agent/`（构建产物，含密钥，禁提交）|

> **凭证链与失败行为（P1-C 修复）——下游实现必须落实：**
> - **dev**：后端经 pydantic `env_file=services/quant/.env` 直接读取 `AGENT_*`（dev.py 启动时 CWD=services/quant）。
> - **prod（打包）**：`agent-env.js` 首启把 `resources/agent/agent.env` 播种到 `userData/agent.env`（用户可改），`config.js · backendEnv()` 解析后把 `AGENT_*` 注入后端子进程 env——pydantic Settings 从进程 env 读取，**prod 无需 `.env` 文件**（与 `DB_PATH→userData` 既有注入同构）。
> - **build 期保护**：`build-agent-env.mjs` 若源 `services/quant/.env` 缺 `AGENT_API_KEY`，**必须显著告警**（非致命：仍产出含占位的 `agent.env`，使桌面端首启即生成可编辑的 `userData/agent.env`），避免静默打出"无密钥"包；`verify-dist.mjs` / `npm run smoke` 须**断言打包产物含 `resources/agent/agent.env`**。
> - **构建机要求**：出正式桌面包前，构建机需先在 `services/quant/.env` 填入真实 KIMI key（已 gitignored、不提交）；CI / 他机构建无此文件时按"告警 + 占位"降级，用户安装后自行填 `userData/agent.env`。

---

## 4. Interface design（接口设计）

### 4.1 WebSocket：`/api/ws/agent/{conversationId}`

沿用项目 `{type, data}` 事件信封（同 `_send_event`）。浏览器经 `NEXT_PUBLIC_PY_WS_URL` 直连（与 market/paper WS 同路径策略，桌面端构建期烤定 `ws://127.0.0.1:18760`）。

**Client → Server：**

| type | data | 说明 |
|---|---|---|
| `user_message` | `{text: string, ui?: UiContext}` | 发送一条用户消息；`ui` 携当前工作台上下文 |
| `cancel` | `{}` | 取消进行中的 run |

`UiContext = {instId, bar, mode, strategyId?, backtestStartTs?, backtestEndTs?}`。

**Server → Client：**

| type | data | 说明 |
|---|---|---|
| `ready` | `{conversationId, provider, model, configured: boolean}` | 连接就绪 |
| `history` | `{messages: AgentMessage[]}` | 重放持久化历史（连接后一次性）|
| `message_start` | `{id, role:"assistant"}` | 助手消息开始 |
| `text_delta` | `{id, text}` | 文本增量 |
| `thinking_delta` | `{id, text}` | 思维链增量（provider 提供则有；UI 可折叠/弱化）|
| `tool_call` | `{id, name, input}` | 工具调用开始（含完整入参）|
| `tool_result` | `{id, name, ok, summary}` | 工具执行结果摘要 |
| `ui_action` | `{action:"show_chart", instId?,bar?,startTs?,endTs?,runId?}` | 前端应用到 store |
| `message_end` | `{id, usage?}` | 本条助手消息结束 |
| `done` | `{cancelled?: boolean}` | 整轮 run 结束 |
| `error` | `{code, message}` | 错误（code 稳定：`AGENT_NOT_CONFIGURED`/`LLM_HTTP_ERROR`/`TOOL_LOOP_LIMIT`/`BUSY`）|

### 4.2 REST（经 `/api/py/*` 代理 → 后端 `/api/*`）

| Method | Path | 响应 | 说明 |
|---|---|---|---|
| GET | `/api/agent/config` | `{configured:boolean, provider, model}` | 是否已配置密钥 + 当前模型（**绝不返回 key**）|
| GET | `/api/agent/conversations/{id}/messages?limit=` | `AgentMessage[]` | 历史（WS history 的 REST 兜底，可用于 SSR/刷新）|
| DELETE | `/api/agent/conversations/{id}` | `204` | 清空会话（"新对话"）|

`AgentMessage`（对前端）：`{id, role:"user"|"assistant"|"tool", ts, blocks: Block[]}`；`Block = {type:"text",text} | {type:"tool_use",id,name,input} | {type:"tool_result",toolUseId,ok,summary}`。前端按 `role` 渲染：`user`/`assistant` 为气泡，`tool` 行仅承载该轮的 `tool_result` block 并并入其上一条 `assistant` 气泡的工具卡（不单列气泡）；`toolUseId` 与 `assistant` 轮的 `tool_use.id` 配对（P1-B）。

### 4.3 工具 JSON Schema（示例：`run_backtest`）

```json
{
  "name": "run_backtest",
  "description": "对指定策略在某标的/周期/时间区间上运行回测，返回 runId 与绩效指标。",
  "input_schema": {
    "type": "object",
    "properties": {
      "strategyId": {"type": "integer", "description": "策略 id（来自 list_strategies）"},
      "instId": {"type": "string", "description": "如 BTC-USDT-SWAP"},
      "bar": {"type": "string", "enum": ["1m","5m","15m","1H","4H","1D"]},
      "startTs": {"type": "integer", "description": "秒级 UTC 起"},
      "endTs": {"type": "integer", "description": "秒级 UTC 止"},
      "params": {"type": "object", "description": "可选参数覆盖"}
    },
    "required": ["strategyId","instId","bar","startTs","endTs"]
  }
}
```

> Anthropic 用 `input_schema`，OpenAI 转 `parameters`——由各 client 在 `_to_provider_tools()` 转换。

---

## 5. Data model（数据模型）

### 5.1 持久化（SQLite，新增 1 张表）

`app/db/models.py · AgentMessage`：

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | 自增 |
| `conversation_id` | TEXT, index | 会话标识（前端生成 uuid，持久化于 localStorage）|
| `role` | TEXT | `user` / `assistant` / `tool`（`tool` 行承载该轮全部 `tool_result` block，使 resume 可无损还原工具上下文，P1-B）|
| `content_json` | TEXT | 该消息全部 blocks 的 JSON（text/tool_use/tool_result）|
| `created_ts` | INTEGER | 秒级 UTC（全系统统一）|

- 建表沿用 `db/session.py` 既有 `Base.metadata.create_all`（启动期 `init_db`），无需手写迁移。
- 仅落"对话可读历史"（text 与工具摘要）；**不落** API Key、不落原始大体量 payload（避免库膨胀）。
- **tool_use / tool_result 持久化与还原（P1-B）**：`content_json` 存该轮归一化 block 序列并保留 `tool_use.id ↔ tool_result.tool_use_id` 关联——`assistant` 行存 `text + tool_use blocks`，`tool` 行存对应 `tool_result blocks`。重建内部 `messages` 时按 provider 还原：**Anthropic** 把 `tool_result` 装入紧随的 `user` 轮 content；**OpenAI** 还原为独立 `role:"tool"` 消息（`tool_call_id` 对齐）。回喂模型的 `tool_result` 内容用 `summary`（受 §2.4 输出预算截断约束），从而 resume / `history` 重放两条链路共用同一份无损 block 序列。

### 5.2 内存运行态（AgentManager，进程级单例）

```python
class AgentManager:
    _sessions: dict[str, AgentSession]      # convId → 活动会话
    def get_or_create(self, conv_id) -> AgentSession
    async def handle(self, conv_id, user_text, ui, emit) -> None   # 跑一轮 run
    async def cancel(self, conv_id) -> None
```

- 复用 `paper/manager.py · get_paper_manager()` 的"模块级单例 + lifespan shutdown"模式（`main.lifespan` 关闭时 `await get_agent_manager().shutdown()`）。
- `AgentSession` 内存持有 `messages: list[LlmMessage]`（喂模型用，含完整工具上下文）；持久化表只存可读摘要。重开会话时从 DB 读 `content_json`、按 §5.1 的 P1-B 映射无损重建 `messages`（保持 tool_use/tool_result 配对）。
- **上下文增长护栏（P2-2）**：重建 / 续接时对历史 `messages` 按"近 N 轮"裁剪（超限丢弃最旧轮或摘要化），防止长会话超出模型上下文；裁剪只作用于回喂模型的内存 `messages`，DB 完整历史与前端 `history` 重放不受影响。

### 5.3 前端状态（Zustand，store.ts）

新增并持久化（partialize + migrate v8）：

```ts
rightTab: "agent" | "strategy"      // 右侧激活页签，默认 "agent"
agentConversationId: string         // 首次为空 → 挂载时 crypto.randomUUID() 落盘
setRightTab(tab); setAgentConversationId(id); newConversation()  // 生成新 uuid+清理
```

会话内消息流不入 Zustand 持久化（运行期态，按 R6 同口径，仅 conversationId 持久化以便重连恢复）。

---

## 6. Testing & acceptance criteria（测试与验收）

### 6.1 单元测试（沿用 pytest / vitest，离线、不依赖真实网络）

**后端（`services/quant/tests/`）：**
- `test_agent_llm_anthropic.py`：喂 Anthropic SSE 样本字节流（含 text + tool_use + input_json_delta + message_delta），断言归一化事件序列正确（含工具入参 JSON 拼装）。
- `test_agent_llm_openai.py`：喂 OpenAI SSE 样本（含分片 tool_calls.arguments），断言归一化一致。
- `test_agent_loop.py`：用**假 LLMClient**（脚本化：第1轮发 tool_use→第2轮发 end_turn）驱动 loop，断言：工具被执行、tool_result 回喂、最终 done；`max_tool_rounds` 命中报 `TOOL_LOOP_LIMIT`；cancel 中断。
- `test_agent_tools.py`：用假 OKX client / 内存 repo 断言每个工具 `execute` 的 summary/payload 与 `ok=False`（OKX 不可达不假数据，C1）。
- `test_agent_services_backtest.py`：断言抽取后的复用 helper 与原 `api/strategies.py` 回测**结果等价**（回归护栏，保证重构无行为漂移）。
- `test_agent_repo.py`：`append/list/clear_agent_messages` 往返。
- `test_agent_ws.py`（FastAPI TestClient WS）：发 `user_message`（注入假 LLM）→ 收到 `message_start/tool_call/tool_result/ui_action/done` 序列；未配置密钥→ `ready{configured:false}` 且发消息回 `AGENT_NOT_CONFIGURED`。
- 可选 `-m network`：真实 KIMI 端到端冒烟（默认排除，与既有 network 用例同口径）；**须覆盖一次"含工具调用"的真实往返**（验证 KIMI 的 tool_use 流式解析，P2-1），并断言所配 `agent_model`（默认 `kimi-k2.6`）真实可用（P1-A）。

**前端（`apps/web/**/*.test.ts`）：**
- `lib/agentActions.test.ts`：`ui_action{show_chart,...}` → 期望 setter 调用序列（含 runId→run-range、仅 instId→live-follow 等分支）。
- `lib/agentTypes`/事件归约 reducer 测试：流式 `text_delta` 累积、`tool_call`→`tool_result` 配对、消息列表归约正确。

### 6.2 验收标准（Definition of Done，对齐全局目标"稳健可靠、正确交互"）

1. 右侧面板呈现两个页签【AI 助手】【交易策略】；切换不破坏各自状态；默认 AI 助手。
2. 在 AI 助手输入"列出现货里成交额最高的几个标的"→ Agent 调 `list_instruments` 并以自然语言+卡片返回真实 OKX 数据。
3. 输入"画出 BTC 永续最近30天的日线"→ Agent 调 `show_chart`，左图真实切换到对应标的/周期/区间并显示真实 K 线。
4. 输入"用某策略回测这段并展示买卖点"→ Agent 调 `run_backtest` 再 `show_chart{runId}`，左图叠加该 run 的 markers/净值，对话区给出指标解读。
5. KIMI 凭证经 `.env`（开发）/ userData 副本（桌面端）真实生效；缺失时面板优雅引导而非崩溃。
6. `pnpm test`（后端 pytest 非 network + 前端 vitest/tsc）全绿。
7. 桌面端 `npm run smoke` / `npm run dist` 产物内含 `resources/agent/agent.env`（`verify-dist` 显式断言其存在，源 `.env` 缺 key 时构建**告警而非静默通过**，P1-C）；首启播种 `userData/agent.env`；Agent 在打包应用内可用，用户可编辑该文件改 key/model。
8. OKX 不可达时工具如实报错、绝不假数据（C1）；密钥绝不出现在前端、日志、响应体。

---

## 7. Risks & mitigations（风险与缓解）

| # | 风险 | 缓解 |
|---|---|---|
| R1 | 模型名歧义：任务写 `kimi-k2.6`，示例用 `kimi-k2.5` | **修复（P1-A）**：默认 `agent_model=kimi-k2.6`（遵从任务显式指定值）；`.env.example` 注明 `kimi-k2.5` 为示例已验证回退值，`.env` 一行可切换；`AnthropicClient` 对 4xx/`model_not_found` 错误体显式 emit `error{code=LLM_HTTP_ERROR}` 而非静默；network 冒烟断言所配 model 真实可用 |
| R2 | KIMI 端点校验 `User-Agent: claude-code`，普通请求 403 | Anthropic client 默认带 `agent_user_agent=claude-code/1.0`，可经 `.env` 覆盖；单测覆盖 header 装配 |
| R3 | 密钥泄露（提交进仓库 / 下发前端 / 落日志）| `.env*` 与 `desktop/resources/agent/` 全 gitignored；`.env.example` 只放占位；`/api/agent/config` 只回 `configured` 布尔；密钥不入 `AgentMessage`/日志（C7）|
| R4 | 打包后桌面端拿不到密钥 / 用户改不了 | 构建期 `build-agent-env.mjs` 把 `AGENT_*` 拷入 `resources/agent/agent.env`；运行期 `agent-env.js` 首启播种到 userData 可写副本并注入后端子进程 env（复用 DB_PATH→userData 既有模式）|
| R5 | OKX 受限 / 代理：KIMI 与 OKX 代理需求可能不同 | Agent LLM client 独立 `agent_proxy`（缺省回落 `settings.proxy_url`，可设 `none` 强制直连），与 OKX 取数解耦 |
| R6 | 工具失控循环 / 长耗时回测阻塞事件循环 | `max_tool_rounds` 护栏；回测走 `run_in_threadpool`（沿用既有 C12）；LLM httpx 调用带 `agent_timeout_seconds` 与 cancel |
| R7 | tool_result 体量过大撑爆上下文 | 列表/序列工具边界截断（Top N / 下采样）并在 summary 标注"已截断"；原始大数据放 payload 仅前端可选展示、不回喂模型 |
| R8 | 重构 `api/strategies.py` 抽 helper 引入回归 | 抽取为纯函数 + `test_agent_services_backtest.py` 等价性回归护栏；router 仅改为调用 helper，签名/响应不变 |
| R9 | 前端 380px 窄栏放不下复杂对话 UI | 紧凑设计：气泡全宽、工具卡折叠、composer 自适应高度；遵循既有窄栏排版（同 StrategyPanel 已在 380px 工作）|
| R10 | SSE 半包 / provider 流格式差异 | 各 client 用稳健行缓冲解析 `data:` 帧；归一化层吸收差异；以离线样本字节流单测固化 |
| R11 | WS 在桌面端不经 Next 代理 | 与 market/paper WS 同：浏览器直连 `NEXT_PUBLIC_PY_WS_URL`，桌面端构建期已烤定 127.0.0.1:18760，无需额外配置 |
| R12 | Electron `sandbox:true` 下前端能力受限 | Agent 全部经标准 WS/HTTP，无需 Node 能力；不触碰 preload 桥，零桌面特例改后端（符合索引"desktop 打包"约定）|
| R13 | WS 断连 / 页面关闭后 run 成孤儿，继续烧 LLM 配额 | **修复（P1-D）**：断连与 `cancel` 同路——`WebSocketDisconnect` / socket close 时落盘已完成轮次后中断 stream；重连经 `history` 重放恢复 |
| R14 | KIMI 的 tool_use 流式事件未经示例实证（示例仅文本 SSE）| 以离线样本字节流固化 tool_use 流解析；新增"含工具往返"的 `-m network` 真实冒烟用例验证（P2-1）|
| R15 | 长会话 `messages` 无限增长超模型上下文 | 重建 / 续接按"近 N 轮"裁剪、超限丢弃最旧或摘要化（P2-2），仅作用于回喂模型的内存态 |

---

## 8. 附：实现注意事项（给下游工程师）

- **路径/命令跨平台**：建目录用 `python -c "Path(...).mkdir(parents=True,exist_ok=True)"`；勿用 `mkdir -p` / `&&`（见任务环境约定）。
- **时间统一秒级 UTC**：工具入参/出参 `ts` 一律秒级（OKX 适配层已做毫秒转换，勿在工具层重复转换）。
- **camelCase 边界对齐**：后端响应/WS data 字段用 camelCase 与前端 TS 对齐（同 `schemas.py` 既有风格）。
- **system prompt 注入 UI 上下文**：让 Agent 默认理解"当前在看哪个标的/周期"，用户说"回测这个"时无需追问。
- **不改既有 WS/REST 行为**：新增端点独立挂载；对 `api/strategies.py` 的改动限于"内部逻辑抽函数复用"，对外契约不变。
- **设计 token 复用**：对话 UI 全部用 `var(--accent)/--surface/--surface-2/--border/--text/--muted/--long/--short`，自动适配明暗主题；交互动效沿用 `transition-* ease-smooth`。
- **凭证不入版本库（P2-7）**：真实 key 仅写 `services/quant/.env`（已 gitignored）；下游 Subtask #3 提交务必**精确 `git add <具体路径>`、严禁 `git add -A`**（与任务板规则一致），并复核 diff 不含任何 key（C7）。

（全文 > 800 字，覆盖 Overview / 技术设计 / 文件清单 / 接口 / 数据模型 / 测试验收 / 风险，满足 Definition of Done。）

---

## 评审结论（Review Verdict / Subtask #1）

**结论：有条件通过（Approved with conditions）。**

**总体评价**：方案在**可行性**上扎实——所有工具映射、仓储函数名、OKX 数据源、WS 事件信封、store 迁移版本、桌面打包与凭证注入机制均已对照真实源码逐项核验通过（见 §评审记录"已核验事实"）；密钥只在后端、零新增三方依赖、ui-action 驱动左图、复用既有 `ReconnectingWS`/`Tabs`/设计 token，与 `CLAUDE.md` 及代码库既有约定高度**一致**；**适度性**合理——OpenAI 双标准、桌面凭证可改均为任务硬要求而非镀金，唯一带回归风险的 `api/strategies.py` 抽 helper 已有等价性回归测试（R8）护栏。

**放行依据**：本轮评审发现 **0 个 P0、4 个 P1、9 个 P2**。**全部 4 个 P1 已在本 v2 文档正文就地修复**（P1-A 模型默认改 `kimi-k2.6` + 回退/报错；P1-B 统一 role 与 tool_result 无损持久化/还原；P1-C 打包凭证链失败行为与 build 期保护；P1-D 断连即取消防孤儿 run），无遗留 P0/P1。

**放行条件（下游 Subtask #2/#3 实现期须落实的 P2 项）**：

1. **P2-1**：`-m network` 冒烟必须覆盖一次"含工具调用"的真实 KIMI 往返，实证 tool_use 流式解析（不能只测文本）。
2. **P2-2**：实现 `messages` 上下文裁剪护栏（近 N 轮 / 摘要化），防长会话超上下文。
3. **P2-3**：`AgentSession.run` 拆分为 `_stream_assistant_turn()` / `_execute_tool_calls()` 等，守住 50 行 / 圈复杂度 10 / 嵌套 4 层阈值。
4. **P2-4**：`lib/agentActions.ts` 落地前对照 `store.ts` 核实真实 setter 名与签名，单测覆盖各分支。
5. **P2-5**：`get_market_summary` 的 24h 字段取自 tickers 同源，勿用只回 `last` 的 `get_ticker`。
6. **P2-6**：确认长耗时回测期间 Agent WS 不被 idle 超时断开（或加"running"心跳）。
7. **P2-7**：提交期精确暂存、严禁 `git add -A`，复核 diff 无密钥泄露。

P2-8（OpenAI 仅离线单测验证）、P2-9（Agent WS localhost 无鉴权）经评估**与既有约定一致、可接受**，不构成放行阻碍，留作未来加固。

> 满足 Definition of Done：spec 含 `## 评审记录` 与 `## 评审结论`；版本标注为 **v2**；无 P0/P1 遗留。

---

## 实施过程发现的方案缺陷（Issues Found During Implementation / Subtask #2）

> 实现期对照 v2 设计逐文件落地，发现以下与设计描述需澄清/微调之处。均已就地按"修正方案"实现，不影响对外契约与验收标准。

1. **凭证注入分层（§3.3）**：v2 写"`config.js · backendEnv()` 合并 `agent-env.js` 解析出的 `AGENT_*`"。但 `config.js` 必须保持 **electron-free**（`verify-dist.mjs` 以纯 node 进程 `require('../src/lib/config.js')`，而 `agent-env.js` `require('electron')`）。若 `config.js` 直接依赖 `agent-env.js` 会令 `verify-dist`/`check:config` 在无 electron 上下文崩溃。**修正**：`agent-env.js` 负责播种+解析（依赖 electron），`backendEnv({mode,dbPath,agentEnv})` 新增 `agentEnv` 入参只做合并（仍 electron-free）；由 `main.js`（已在 electron 上下文）在 prod 调 `seedUserAgentEnv()/loadAgentEnv()` 并把结果传入 `backendEnv`。对外行为与 §3.3 凭证链完全一致（dev 经 `services/quant/.env`，prod 经 `userData/agent.env` 注入子进程 env）。

2. **`db/session.py` 无需改动（§3.1 表标 [改]）**：`AgentMessage` 挂在既有 `Base` 上，启动期 `init_db` 的 `Base.metadata.create_all` 自动建表（与 §5.1"无需手写迁移"一致）。故 `session.py` **未改动**，符合"保持 diff 聚焦"。

3. **新增两个未在文件表显列的支撑模块**：`agent/conversation.py`（block↔dict↔DB 行序列化，承载 P1-B 无损持久化/还原）与前端 `lib/agentReducer.ts`（§6.1 已规划"事件归约 reducer 测试"，是其落地实现）。二者为设计语义的必要实现拆分，不扩大范围。

4. **`api/strategies.py` 抽 helper 的 OKX 注入方式（§3.1 / R8）**：为不破坏既有测试对 `app.api.strategies.get_rest_client` 的 monkeypatch，`agent/services.py` 的回测/对比 helper 以参数 `rest` 注入（调用方在各自命名空间解析 `get_rest_client` 后传入），而非在 service 内部 import。既保证 UI 与 Agent 共用同一份逻辑，又使既有 `test_api`/`test_compare` 的 monkeypatch 继续生效（9 项回归全绿）。

> 真实凭证已写入 `services/quant/.env`（gitignored）并经 `-m network` 真实 KIMI 端到端冒烟验证：tool_use 流式解析、`list_instruments` 真实 OKX 往返、最终文本总结全链路通过（P1-A / P2-1）。
