# Agent 工具管理（Agent Tool Management）开发方案

> 设计文档 · **v2**（已评审并修复 · Subtask #1 方案评审与修复）
> 所属需求：在设置中心新增「Agent 工具管理」设置项，集中查看 AI 交易助手可用的全部工具，并支持逐项开启 / 关闭。
> 作者：方案设计节点（Subtask #0）；评审与修订：方案评审节点（Subtask #1）
> 单一真源：后端 `services/quant/app/agent/tools/registry.py::_ALL_TOOLS`（13 个工具）。
> 变更（v1 → v2）：修复 P1-1（`Switch` 用 `label` 形参而非 `aria-label`）、P1-2（工具启用态测试隔离）、P1-3（补 `resolve_enabled_tools` 合并逻辑与 loop 启用门控测试）；澄清 P2-1（仓储写入内部 `commit`）、P2-2（全禁用 `tools=[]` 已是既有路径）。逐条详见下方「评审记录」。

---

## 评审记录（Review Notes · Subtask #1）

评审方法：逐节对照**真实仓库代码**核验可行性 / 完整性 / 一致性 / 适度性。已核验的关键事实——
`registry.py::_ALL_TOOLS` 确为 13 个工具且 `__init__` 用 dict 保序（`tool_meta()` 按 `_ALL_TOOLS` 顺序成立）；
`Tool` 基类有 `name/description/input_schema/is_ui_action`，新增 `category` 可行；
`AgentSession.run / _run_rounds / _stream_assistant_turn / _execute_tool_calls` 签名与方案改造点一一对应；
`AgentManager.handle` 与 `_registry` 单例可承接启用集注入；`build_system_prompt(ui)` 可加 `disabled_tools`；
`db/models.py`（`Base` / `_utcnow` / `AgentProvider` 先例）支持新表；`session.init_db()` 的 `Base.metadata.create_all` 对**新表**幂等建表成立（无需迁移框架）；
`components/ui` 确有 `Switch / Card / Badge / Button / Skeleton`；`SettingsNav.SECTIONS` / `SettingsPanel` 三元可扩展；`store` persist `version: 9` 无 section 白名单，故新增 `agent-tools` 持久化后可存活；
`agentApi.request` 读 `body.error.code`，而后端 `main.py::http_exception_handler` 已把 `HTTPException(detail={code,message})` 归一化为 `{"error":{...}}`——故 §四 的 404 形状与既有 provider 路由完全一致（属优点，非风险）。

| 编号 | 维度 | 严重度 | 问题 | 处置 |
|---|---|---|---|---|
| P1-1 | 一致性 / 可行性 | **P1** | §2.9 在 `<Switch>` 上写 `aria-label={…}`。实际 `components/ui` 的 `Switch` 只接收 `label` 形参（内部映射到 `aria-label`），且不透传未知 DOM 属性——`aria-label` 会被**静默丢弃**，使验收项 §6.3-5「读屏可识别开关状态」落空。 | 已修复：§2.9 改用 `label` 形参，并补全 `Switch` 真实签名。 |
| P1-2 | 完整性 / 测试 | **P1** | §6.1 称复用 `test_agent_provider_api.py` 夹具，但其 `_reseed()` 只清 `providers`。`services/quant` 测试**共用同一临时 DB**，而 `agent_tool_states` 为稀疏表；首个用例断言「全部 `enabled` 为真」仅在空表时成立，跨用例 / 跨文件残留（如 bulk 禁用后）将导致顺序相关的偶发失败。 | 已修复：§6.1 增 `_reset_tool_states()`（清空 `AgentToolState` 行）并要求每用例起始调用。 |
| P1-3 | 完整性 / 测试 | **P1** | 「开关真正生效」依赖 `AgentManager.resolve_enabled_tools()`（DB 覆盖项与「缺省即启用」的合并分支）与 loop 注入，但方案只测了 `schemas/execute/disabled_names` 与 REST 持久化，未测这两处——核心链路仅靠手动验收，回归无护栏。 | 已修复：§6.1 增 `resolve_enabled_tools` 合并单测与 loop 级门控单测（复用 `test_agent_loop.py` 的 `_ScriptedLLM` 捕获 `stream(tools=…)`）。 |
| P2-1 | 完整性 | P2 | §2.3 未言明三个仓储写入函数是否内部 `commit`。既有 `create_agent_provider` / `update_agent_provider` 均在内部 `commit`；若新函数漏提交则启用态不落库，"刷新后保持" 验收失败（虽会被 P1-2 修后的持久化测试捕获）。 | 已澄清：§2.3 注明写入函数内部 `commit`、只读函数不提交。 |
| P2-2 | 可行性 | P2 | 「全部禁用」后 `enabled = set()`，`schemas(set())` 返回 `[]` 并以 `tools=[]` 调 LLM。需确认空工具表是受支持路径。 | 已确认（非缺陷）：§2.4 既有 `_finalize_on_limit` 已以 `tools=[]` 调 `_stream_assistant_turn`，LLM client 空工具路径已被现网行使；§七风险表补一行登记。 |

> 结论速览：**0 个 P0**；3 个 P1 已就地修复；2 个 P2 已澄清 / 确认。详见文末「评审结论」。

---

## 一、Overview（概述）

Aragon Trading 在右侧面板内置了一个对话式 **AI 交易助手**（`apps/web/components/Agent/*` + `services/quant/app/agent/*`）。助手通过一组「工具」（tool calling）真实操作系统：查行情、画 K 线、跑回测、对比策略、识别市场体制、统计信号前瞻收益、读运行历史等。这些工具在后端集中注册于 `ToolRegistry`（`_ALL_TOOLS`，当前共 13 个），每轮 run 由 `AgentSession._stream_assistant_turn` 把 `registry.schemas()` 作为可用工具清单喂给 LLM。设置中心（`apps/web/components/Settings/*`）此前只有一项「AI 模型服务」（`ai-models`），用于管理 LLM 服务商；其外壳 `SettingsPanel` + 导航 `SettingsNav` 已按「可扩展注册表」设计，预留了新增设置项的位置。

本方案在设置中心新增第二个设置项 **「Agent 工具管理」**（section key `agent-tools`）。点击后右侧主面板逐个（按能力分组）列出 Agent 当前可用的**全部**工具，每个工具卡片展示：友好中文名 + 图标、原始工具名（等宽 `code`）、一句话能力描述、是否为「界面动作」标记，以及一个 **开关（Switch）** 用于启用 / 禁用；卡片可展开「查看参数」，以只读表格呈现该工具的 JSON Schema 入参（满足"供用户查看"诉求）。顶部提供「已启用 X / 共 Y」概览与「全部启用 / 全部禁用」批量操作；当用户把工具全部关掉时给出明确的能力降级提示。

启用状态**持久化于后端 SQLite**（新表 `agent_tool_states`，默认「不存在即启用」），并在每轮 run 时由 `AgentManager` 解析为「启用工具集合」注入 `AgentSession.run`，从而：（1）禁用的工具**不出现在喂给模型的 schema 列表**里——这是硬闸门，模型在 API 层面即无法调用；（2）`ToolRegistry.execute` 对禁用工具二次兜底拒绝（纵深防御）；（3）系统提示在工具被禁用时追加一行"已禁用工具"说明，避免模型按提示文案臆想调用。该设计与既有「AI 模型服务」一致——配置落库、每轮 run 解析、绝不在前端持有敏感/可变运行态——保证风格统一、可测、可回退。

设计目标是「美观优雅、顶级设计、符合人机交互最佳实践」：复用既有设计 token（`var(--*)`）与 UI 原语（`Switch / Card / Badge / Button / Skeleton`）、与「AI 模型服务」面板视觉同构；交互上采用**乐观更新 + 失败回滚**、清晰的分组与概览、可访问性（`role="switch"` + `aria-label` + 键盘可达 + `aria-live` 反馈），并对"禁用全部"这类破坏性后果显式告知。

---

## 二、Technical design（技术设计）

### 2.1 架构总览

```text
设置中心（前端）                              后端（FastAPI + Agent 子系统）
SettingsNav ──点选 agent-tools──► SettingsPanel
                                     └► AgentToolsSection
                                          ├─ GET  /api/py/agent/tools        ──► api/agent.list_tools
                                          ├─ PUT  /api/py/agent/tools/{name} ──► api/agent.update_tool
                                          └─ POST /api/py/agent/tools/bulk   ──► api/agent.bulk_tools
                                                   │
                                                   ▼
                          ToolRegistry(_ALL_TOOLS)  +  repo.AgentToolState（SQLite）
                                                   │  启用状态合并（默认启用）
                                                   ▼
   run 时：AgentManager.resolve_enabled_tools() ──► AgentSession.run(enabled_tools)
            ├─ _stream_assistant_turn: registry.schemas(enabled)  ← 硬闸门（模型只见启用工具）
            ├─ _execute_tool_calls:    registry.execute(..., enabled) ← 纵深防御
            └─ build_system_prompt(ui, disabled_names)            ← 提示一致性
```

**真源与职责边界**：
- 「有哪些工具、各自能力描述 / 入参 schema / 分组」的唯一真源是后端 `ToolRegistry` / 各 `Tool` 子类（C6，禁止前端硬编码工具清单）。
- 「每个工具是否启用」的唯一真源是 SQLite 表 `agent_tool_states`，规则为 **"无记录即启用"**（稀疏存储，仅落用户改过的项）。
- 「友好中文名 + 图标 + 分组中文标题」属于**前端展示层**关注点，集中在 `apps/web/lib/agentToolMeta.ts`（从既有 `ToolCallCard.tsx` 抽取，消除两处漂移）。后端只回 `name / description / category(key) / isUiAction / paramsSchema / enabled`。

### 2.2 后端：工具元信息与分组

为每个 `Tool` 增加 `category` 类属性（语义分组键），并让 `ToolRegistry` 暴露元信息与启用过滤能力。

- `tools/base.py::Tool` 新增类属性：`category: str = "general"`。
- 各工具类设置 `category`：
  - `market`（行情数据）：`list_instruments` / `get_market_summary` / `get_candles`
  - `ui`（界面控制）：`show_chart`
  - `strategy`（策略与回测）：`list_strategies` / `run_backtest` / `compare_strategies`
  - `analysis`（智能分析）：`label_market_regime` / `test_signal_forward_returns`
  - `runs`（运行历史）：`list_runs` / `get_run_metrics` / `get_run_trades` / `get_run_equity`
- `tools/registry.py::ToolRegistry` 新增 / 改造方法：
  - `all_names() -> set[str]`：注册表内全部工具名。
  - `tool_meta() -> list[dict]`：按 `_ALL_TOOLS` 顺序返回 `{"name","description","category","isUiAction","paramsSchema"}`（`paramsSchema` 即 `tool.input_schema`，稳定展示顺序）。
  - `meta_for(name) -> dict | None`：单个工具元信息（PUT 回包用）。
  - `schemas(enabled: set[str] | None = None)`：`enabled is None` 返回全部（向后兼容既有调用与单测）；否则只返回 `name in enabled` 的 schema。
  - `disabled_names(enabled: set[str] | None) -> list[str]`：`enabled is None` 返回 `[]`；否则返回 `sorted(all_names - enabled)`（喂给系统提示）。
  - `execute(name, args, ctx, *, enabled: set[str] | None = None)`：新增可选 `enabled`；在「未知工具」判定之后、参数校验之前，若 `enabled is not None and name not in enabled`，返回 `ToolResult(False, "工具 {name} 已被禁用，请在设置中心 · Agent 工具管理中开启后再使用。")`。默认 `None` 不改变既有行为。

### 2.3 后端：启用状态持久化与解析

- **新表** `agent_tool_states`（见 §五 数据模型），由 `init_db()` 的 `Base.metadata.create_all` 幂等建表（既有 DB 下次启动自动补表，无需迁移框架）。**无需 seed**——空表即「全部启用」。
- `db/repositories.py` 新增（**写入函数须在内部 `await session.commit()` 后返回，镜像既有 `create_agent_provider` / `update_agent_provider` 的约定；只读函数不提交。否则启用态不落库，「刷新后保持」验收失败，P2-1**）：
  - `get_tool_states(session) -> dict[str, bool]`：读全部覆盖项为 `{name: enabled}`（只读，不提交）。
  - `set_tool_enabled(session, name, enabled) -> None`：按 `name` select-then-update / 否则 insert（upsert）后 `commit`。
  - `set_all_tool_states(session, names, enabled) -> None`：对给定名集合批量 upsert 后 `commit`。
- `agent/manager.py::AgentManager` 新增：
  - `tool_metadata() -> list[dict]`：委托 `self._registry.tool_meta()`（用**运行时同一个 registry 实例**，保证"展示的工具 == 实际可用的工具"）。
  - `has_tool(name) -> bool`：委托 `self._registry.get(name) is not None`。
  - `async resolve_enabled_tools() -> set[str]`：读 DB 覆盖项，返回 `{n for n in registry.all_names() if states.get(n, True)}`（每轮解析，毫秒级，与 `resolve_active_provider` 同模式）。

### 2.4 后端：run 期注入与执行链

- `agent/manager.py::handle()`：在解析 `client` 之后、构造 run task 之前，`enabled = await self.resolve_enabled_tools()`，并 `sess.run(user_text, ui, client=client, enabled_tools=enabled)`。
- `agent/loop.py::AgentSession`：
  - `run(self, user_text, ui=None, sink=None, *, client=None, enabled_tools: set[str] | None = None)`：在 BUSY 守卫**之后**置 `self._enabled_tools = enabled_tools`（与 `client` 绑定同点位，保证赢得 run 槽的这一轮才采用最新启用集，避免并发轮中途替换）。新增实例字段 `self._enabled_tools: set[str] | None = None`（`__init__` 初始化）。
  - `_run_rounds()`：`disabled = self._registry.disabled_names(self._enabled_tools)`；`system = build_system_prompt(ui, disabled)`。
  - `_stream_assistant_turn()`：`schemas = self._registry.schemas(self._enabled_tools) if tools is None else tools`（`tools=[]` 的总结轮不受影响）。
  - `_execute_tool_calls()`：`result = await self._registry.execute(tu.name, tu.input, ctx, enabled=self._enabled_tools)`。
- `agent/prompts.py::build_system_prompt(ui, disabled_tools: list[str] | None = None)`：当 `disabled_tools` 非空，在末尾追加一行：`\n\n（注意：以下工具当前已被用户禁用，本轮不可调用：{', '.join(disabled_tools)}。）`。默认 `None` 行为不变（既有单测不破）。

> **运行态一致性**：启用集在 run 开始时快照，run 进行中的开关变更不影响在途 run（下一轮 run 生效），与「AI 模型服务每轮解析」语义一致——可预期、可测、无脑裂。

### 2.5 后端：REST 路由（`api/agent.py`）

在既有 provider 路由段后追加「Agent 工具」三个端点（详见 §四）。统一错误：未知工具 → 404 `TOOL_NOT_FOUND`。

### 2.6 前端：展示元信息抽取（DRY）

- **新增** `apps/web/lib/agentToolMeta.ts`：把 `ToolCallCard.tsx` 内的 `TOOL_LABELS`（name → `{label, icon}`）整体迁入并 `export`；新增 `TOOL_CATEGORIES`（category key → `{title, desc}`）与 `CATEGORY_ORDER`。
- `ToolCallCard.tsx` 改为从 `agentToolMeta.ts` 导入 `TOOL_LABELS` / `labelFor`（删除本地副本，行为不变）。

### 2.7 前端：API 客户端与类型

- `apps/web/lib/agentTypes.ts` 新增：`AgentToolCategory`（`"market"|"ui"|"strategy"|"analysis"|"runs"|"general"`）、`AgentTool`、`AgentToolsResponse`。
- `apps/web/lib/agentApi.ts` 的 `agentApi` 新增 `tools` 命名空间：`list()` / `setEnabled(name, enabled)` / `setAll(enabled)`。

### 2.8 前端：纯函数 helper（可单测）

- **新增** `apps/web/lib/agentTools.ts`：
  - `groupToolsByCategory(tools: AgentTool[]): { category: AgentToolCategory; tools: AgentTool[] }[]`：按 `CATEGORY_ORDER` 输出非空分组，组内保持后端返回顺序。
  - `enabledSummary(tools: AgentTool[]): { enabled: number; total: number }`。

### 2.9 前端：UI 组件

- `SettingsNav.tsx`：`SECTIONS` 追加 `{ key: "agent-tools", label: "Agent 工具管理", desc: "查看与开关可用工具" }`。
- `SettingsPanel.tsx`：右侧内容区在 `section === "agent-tools"` 时渲染 `<AgentToolsSection />`（与既有 `ai-models` 三元同构；可顺手改成 switch/map 以容纳更多 section）。
- **新增** `apps/web/components/Settings/AgentToolsSection.tsx`（容器）：
  - React Query key `["agent","tools"]`，`queryFn: agentApi.tools.list`。
  - 顶部 `ToolsHeader`：概览「已启用 X / 共 Y」+「全部启用」「全部禁用」按钮（批量走 `setAll`，乐观更新）。
  - `enabledCount === 0` 时显示醒目提示横幅：「已禁用全部工具：AI 助手将只能进行文字对话，无法读取真实行情或运行回测。」
  - 按 `groupToolsByCategory` 渲染若干 `ToolGroup`（标题 + 描述）→ 内含多个 `ToolToggleCard`。
  - 加载态 `Skeleton`、错误态「重试」（镜像 `AiModelsSection`）。
  - 切换 mutation：乐观更新 query 缓存中对应 `enabled`，失败回滚并以 `aria-live` 文案提示；成功后 `invalidate` 校正 `enabledCount`。
- **新增** `apps/web/components/Settings/ToolToggleCard.tsx`（展示项）：
  - 左：图标 + 友好中文名（`label`）+ 等宽工具名（`<code>{name}</code>`，muted）+ `isUiAction` 时一枚 `Badge(tone="accent")`「界面动作」+ 描述（`description`）。
  - 右：`Switch`（来自 `@/components/ui`，真实签名 `{ checked, onChange:(next:boolean)=>void, label?, id?, className?, tabIndex? }`；其内部已提供 `role="switch"` / `aria-checked` 并以原生 `<button>` 支持 Space/Enter 键盘切换，并把 `label` 形参映射为 `aria-label`）。**必须用 `label` 形参承载无障碍文案，不要写 `aria-label`**——组件不透传未知 DOM 属性，写 `aria-label` 会被静默丢弃（P1-1）。用法：`checked={enabled}`、`onChange={(next) => onToggle(name, next)}`（调容器回调）、`label={`${enabled ? "关闭" : "开启"}${友好中文名}`}`。
  - 「查看参数」可折叠区（复用 `ToolCallCard` 的展开范式）：把 `paramsSchema.properties` 渲染为 `字段 / 类型 / 必填 / 说明` 只读小表；无参数时显示「无入参」。
  - 禁用态卡片整体降低不透明度（`opacity-60`）但**开关与展开仍可操作**，传达"已关闭但可恢复"。

---

## 三、File / module change plan（文件 / 模块变更清单）

| 文件 | 动作 | 一句话意图 |
|---|---|---|
| `services/quant/app/agent/tools/base.py` | 改 | `Tool` 增加 `category: str = "general"` 类属性 |
| `services/quant/app/agent/tools/market_tools.py` | 改 | 三个工具设 `category = "market"` |
| `services/quant/app/agent/tools/ui_tools.py` | 改 | `show_chart` 设 `category = "ui"` |
| `services/quant/app/agent/tools/strategy_tools.py` | 改 | 三个工具设 `category = "strategy"` |
| `services/quant/app/agent/tools/regime_tools.py` | 改 | `label_market_regime` 设 `category = "analysis"` |
| `services/quant/app/agent/tools/signal_tools.py` | 改 | `test_signal_forward_returns` 设 `category = "analysis"` |
| `services/quant/app/agent/tools/run_tools.py` | 改 | 四个工具设 `category = "runs"` |
| `services/quant/app/agent/tools/registry.py` | 改 | 新增 `all_names / tool_meta / meta_for / disabled_names`；`schemas`/`execute` 支持 `enabled` 过滤 |
| `services/quant/app/agent/manager.py` | 改 | 新增 `tool_metadata / has_tool / resolve_enabled_tools`；`handle` 注入启用集到 `run` |
| `services/quant/app/agent/loop.py` | 改 | `AgentSession` 记录并应用 `enabled_tools`（schema 过滤 + execute 兜底 + 提示一致） |
| `services/quant/app/agent/prompts.py` | 改 | `build_system_prompt` 接收 `disabled_tools` 并追加禁用说明 |
| `services/quant/app/db/models.py` | 改 | 新增 ORM 模型 `AgentToolState`（表 `agent_tool_states`） |
| `services/quant/app/db/repositories.py` | 改 | 新增 `get_tool_states / set_tool_enabled / set_all_tool_states` |
| `services/quant/app/schemas.py` | 改 | 新增 `AgentToolOut / AgentToolsListOut / AgentToolUpdate / AgentToolBulkUpdate / tool_out` |
| `services/quant/app/api/agent.py` | 改 | 新增 `GET /agent/tools`、`PUT /agent/tools/{name}`、`POST /agent/tools/bulk` |
| `apps/web/lib/agentToolMeta.ts` | 新增 | 集中工具友好名/图标 + 分组中文标题与顺序（展示层单一真源） |
| `apps/web/components/Agent/ToolCallCard.tsx` | 改 | 改从 `agentToolMeta.ts` 导入标签映射（去重，行为不变） |
| `apps/web/lib/agentTypes.ts` | 改 | 新增 `AgentTool / AgentToolsResponse / AgentToolCategory` 类型 |
| `apps/web/lib/agentApi.ts` | 改 | `agentApi` 增 `tools.list / setEnabled / setAll` |
| `apps/web/lib/agentTools.ts` | 新增 | 纯函数：按分类分组、启用计数（可单测） |
| `apps/web/components/Settings/SettingsNav.tsx` | 改 | 导航追加「Agent 工具管理」条目 |
| `apps/web/components/Settings/SettingsPanel.tsx` | 改 | `agent-tools` section 渲染 `AgentToolsSection` |
| `apps/web/components/Settings/AgentToolsSection.tsx` | 新增 | 工具管理主面板（概览 + 分组 + 批量 + 乐观更新） |
| `apps/web/components/Settings/ToolToggleCard.tsx` | 新增 | 单工具卡片（开关 + 描述 + 查看参数） |
| `services/quant/tests/test_agent_tools_api.py` | 新增 | REST 端点 + 持久化 + 404 + 批量 测试 |
| `services/quant/tests/test_agent_tool_registry.py` | 新增 | `schemas/execute/disabled_names` 启用过滤单测 |
| `apps/web/lib/agentTools.test.ts` | 新增 | 分组 / 计数纯函数单测 |

> 约束：不改撮合 / 回测 / OKX 适配等核心数据路径；不新增第三方依赖；不改既有工具的 `name / input_schema / execute` 行为（仅加 `category` 类属性）。

---

## 四、Interface design（接口设计 · REST）

前端经 `/api/py/*` 代理到后端 `/api/agent/*`。全部 camelCase，绝不涉及密钥。

### 4.1 `GET /agent/tools` —— 列出全部工具及启用态

响应 `AgentToolsListOut`：
```jsonc
{
  "tools": [
    {
      "name": "run_backtest",
      "description": "对指定策略在某标的/周期/时间区间上运行回测，返回 runId 与绩效指标。",
      "category": "strategy",
      "isUiAction": false,
      "enabled": true,
      "paramsSchema": { "type": "object", "properties": { "...": {} }, "required": ["..."] }
    }
    // …按 _ALL_TOOLS 顺序，共 13 项
  ],
  "enabledCount": 12,
  "totalCount": 13
}
```

### 4.2 `PUT /agent/tools/{name}` —— 设置单工具启用态

请求 `AgentToolUpdate`：`{ "enabled": false }`
- 成功 200 → 返回该工具的 `AgentToolOut`（含最新 `enabled`）。
- `name` 不在注册表 → 404 `{ "error": { "code": "TOOL_NOT_FOUND", "message": "工具不存在" } }`。

### 4.3 `POST /agent/tools/bulk` —— 批量启用 / 禁用全部

请求 `AgentToolBulkUpdate`：`{ "enabled": true }`
- 成功 200 → 返回最新 `AgentToolsListOut`（与 4.1 同形，便于前端整列刷新）。

### 4.4 前端 API（`agentApi.tools`）

```ts
tools: {
  list: () => request<AgentToolsResponse>("/agent/tools"),
  setEnabled: (name: string, enabled: boolean) =>
    request<AgentTool>(`/agent/tools/${encodeURIComponent(name)}`, {
      method: "PUT", body: JSON.stringify({ enabled }),
    }),
  setAll: (enabled: boolean) =>
    request<AgentToolsResponse>("/agent/tools/bulk", {
      method: "POST", body: JSON.stringify({ enabled }),
    }),
}
```

---

## 五、Data model（数据模型）

### 5.1 DB：新表 `agent_tool_states`

```python
class AgentToolState(Base):
    """Agent 工具启用状态覆盖项（设置中心 · Agent 工具管理）。

    稀疏存储：仅落用户显式改过的工具；表中无该 name 即视为「启用」（默认放行）。
    name 与 ToolRegistry 工具名一一对应；非真源——工具是否存在以注册表为准（C6）。
    """
    __tablename__ = "agent_tool_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # = Tool.name
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)
```

- 建表：`init_db()` 现有 `Base.metadata.create_all` 自动覆盖（幂等，旧库自动补表）。**不 seed**。
- 读取合并规则：`enabled(name) = states.get(name, True)`。

### 5.2 内存 / 传输形状

- 后端 `tool_meta()` 项：`{name, description, category, isUiAction, paramsSchema}`（不含 `enabled`，由路由合并）。
- run 期启用集：`enabled_tools: set[str]`（`AgentSession._enabled_tools`，`None` = 不过滤，兼容直驱单测）。
- 前端类型（`agentTypes.ts`）：
```ts
export type AgentToolCategory = "market" | "ui" | "strategy" | "analysis" | "runs" | "general";
export interface AgentTool {
  name: string;
  description: string;
  category: AgentToolCategory;
  isUiAction: boolean;
  enabled: boolean;
  paramsSchema: { type?: string; properties?: Record<string, any>; required?: string[] };
}
export interface AgentToolsResponse {
  tools: AgentTool[];
  enabledCount: number;
  totalCount: number;
}
```

### 5.3 Pydantic（`schemas.py`）

```python
class AgentToolOut(BaseModel):
    name: str
    description: str
    category: str
    isUiAction: bool
    enabled: bool
    paramsSchema: dict

class AgentToolsListOut(BaseModel):
    tools: list[dict]
    enabledCount: int
    totalCount: int

class AgentToolUpdate(BaseModel):
    enabled: bool

class AgentToolBulkUpdate(BaseModel):
    enabled: bool

def tool_out(meta: dict, enabled: bool) -> dict:
    return {"name": meta["name"], "description": meta["description"],
            "category": meta["category"], "isUiAction": meta["isUiAction"],
            "enabled": enabled, "paramsSchema": meta["paramsSchema"]}
```

---

## 六、Testing & acceptance criteria（测试与验收标准）

### 6.1 后端单测

`services/quant/tests/test_agent_tools_api.py`（复用 `test_agent_provider_api.py` 的 app/client/临时 DB 夹具）：

> **测试隔离（P1-2，必做）**：`services/quant` 测试共用同一临时 DB，`agent_tool_states` 为稀疏表，跨用例 / 跨文件的启用态残留会污染「缺省即启用」断言。故新增 `_reset_tool_states()`（`init_db()` 后 `delete(AgentToolState)` 全表并 `commit`，类比 `_reseed()` 清 providers），**每个用例起始调用**；`finally` 仍 `dispose_engine()`。下列断言均以"已重置"为前提。

- `test_list_tools_returns_all_registry_tools_enabled_by_default`：GET `/api/agent/tools` → `totalCount == len(_ALL_TOOLS)`（13），每项 `enabled is True`，`enabledCount == totalCount`，含 `category / paramsSchema`，顺序与 `_ALL_TOOLS` 一致。
- `test_put_tool_disable_persists`：PUT `/api/agent/tools/run_backtest {"enabled": false}` → 200 且 `enabled false`；再 GET 该项 `enabled false`、`enabledCount == total-1`。
- `test_put_tool_reenable`：禁用后再 `{"enabled": true}` → 恢复。
- `test_put_unknown_tool_returns_404`：PUT `/api/agent/tools/not_a_tool` → 404 `TOOL_NOT_FOUND`。
- `test_bulk_disable_then_enable`：POST bulk `false` → `enabledCount == 0`；再 `true` → `== total`。

`services/quant/tests/test_agent_tool_registry.py`：
- `test_schemas_filters_by_enabled`：`registry.schemas({"get_candles"})` 仅含该工具；`registry.schemas(None)` 含全部。
- `test_execute_rejects_disabled_tool`：`execute("run_backtest", {...}, ctx, enabled=set())` → `ToolResult.ok is False` 且 summary 含「已被禁用」。
- `test_disabled_names`：`disabled_names({a})` == `sorted(all - {a})`；`disabled_names(None) == []`。
- `test_execute_unknown_tool_still_unknown`：未知名 + 任意 enabled → 「未知工具」（404 语义保持，禁用判定不掩盖未知）。

**核心链路单测（P1-3，新增以为「开关真正生效」上回归护栏）**：

`services/quant/tests/test_agent_tools_api.py`（或就近的 manager 测试，复用同一临时 DB 夹具 + `_reset_tool_states()`）：
- `test_resolve_enabled_tools_defaults_all_enabled`：空覆盖表时 `await manager.resolve_enabled_tools() == manager._registry.all_names()`（验证「无记录即启用」分支）。
- `test_resolve_enabled_tools_applies_db_overrides`：`set_tool_enabled(name, False)` 后再解析，结果恰好少了被禁项，其余仍全在（合并逻辑 `states.get(n, True)`）。

`services/quant/tests/test_agent_loop.py`（复用既有 `_ScriptedLLM`，令其 `stream(*, system, messages, tools)` 记录收到的 `tools`）：
- `test_run_filters_schemas_by_enabled_tools`：`session.run(text, ui, emit, enabled_tools={"get_candles"})` 后，断言桩首轮收到的 `tools` 仅含 `get_candles`；且 `build_system_prompt` 注入的 `disabled_tools` 含其余工具名（验证 schema 硬闸门 + 提示一致性这一对外不可见但关键的链路）。
- `test_run_enabled_tools_none_keeps_all`（回归）：`enabled_tools=None`（既有直驱 / 测试路径）时收到的 `tools` 仍为全部 13 项，确认默认参数不改变原行为。

回归护栏：既有 `test_agent_*`（provider / loop / 体制 / 信号）须全绿——`schemas()` / `execute()` / `build_system_prompt()` 的默认参数（分别为 `None` / `enabled=None` / `disabled_tools=None`）均保持原行为不变。

### 6.2 前端单测

`apps/web/lib/agentTools.test.ts`：
- `groupToolsByCategory` 按 `CATEGORY_ORDER` 输出、丢弃空分组、组内顺序稳定。
- `enabledSummary` 计数正确（全开 / 全关 / 混合）。

回归：`pnpm test`（vitest）+ `tsc` 通过；`ToolCallCard` 抽取后渲染行为不变。

### 6.3 手动验收（Definition of Done · 功能）

1. 设置中心导航出现「Agent 工具管理」，点击右侧逐项（分组）列出全部 13 个工具。
2. 每个工具可见：友好名 + 工具名 + 描述 +（界面动作标记）+ 开关；可展开查看入参 schema。
3. 关闭某工具并刷新后状态保持（落库）；对该助手发起会话时，被禁用工具不再被调用（schema 不含）。
4. 「全部禁用」后顶部出现能力降级提示；「全部启用」恢复。
5. 明暗主题下视觉与「AI 模型服务」面板同构；键盘可操作开关、读屏可识别开关状态与分组。
6. `git status` 可见 `docs/plans/agent-tools-management/spec.md` 及上述全部新增 / 修改文件。

---

## 七、Risks & mitigations（风险与缓解）

| 风险 | 说明 | 缓解 |
|---|---|---|
| 系统提示与可用 schema 不一致 | `prompts.py` 以散文枚举工具，禁用后提示仍提及 | schema 为硬闸门（模型只见启用工具）；`build_system_prompt` 追加"已禁用工具"说明降低臆想；散文不再是真源 |
| 禁用全部工具致助手"失能" | 用户误把所有工具关掉 | 不设硬下限（仍可纯文字对话），但 `enabledCount === 0` 时醒目提示能力降级；保留一键「全部启用」 |
| 全禁用时 `tools=[]` 调 LLM 报错 | `enabled = set()` → `schemas(set())` 返回 `[]` | 已确认安全（P2-2）：既有 `_finalize_on_limit` 总结轮已以 `tools=[]` 调 `_stream_assistant_turn`，空工具路径为现网既有行为，无需 LLM client 改动 |
| run 进行中切换不即时生效 | 启用集在 run 开始快照 | 与「AI 模型服务每轮解析」一致，文档化"下一轮生效"；避免中途替换导致的 tool_use↔tool_result 错配 |
| 旧 DB 无新表 | 既有用户 / 桌面端升级 | `create_all` 幂等补表；稀疏存储 + 默认启用 → 升级后零配置即「全部启用」，行为与升级前等价 |
| 前后端工具清单漂移 | 前端硬编码工具名 | 工具清单只来自后端 `GET /agent/tools`；前端仅持有"name→展示chrome"映射，未知 name 回落原名 + 🔧（已有兜底） |
| `registry.execute` 签名变更影响调用方 | 既有调用未传 `enabled` | `enabled` 为关键字默认 `None`，默认不过滤；仅 `loop` 传入，其余零改动 |
| 工具名含特殊字符的路由 | PUT path 参数 | 工具名为受控 `snake_case` 常量；前端仍 `encodeURIComponent`；后端以注册表成员校验，非法名 → 404 |
| 并发开关写入 | 多标签同时改 | 单行 upsert last-write-wins；前端 mutation 成功后 `invalidate` 校正概览，最终一致 |

---

## 八、实施提示（给下游开发节点）

- 严格按 §三 文件清单逐项落地；后端先 `models → repositories → registry/base/各 tool → schemas → manager/loop/prompts → api`，再前端 `agentToolMeta → agentTypes → agentApi → agentTools → 组件 → 导航/面板接线`，最后补测试。
- 复用既有视觉与无障碍范式：直接照搬 `AiModelsSection` 的 loading/error/Group 结构与 `ToolCallCard` 的展开范式；开关用 `components/ui` 的 `Switch`。
- 不引入新依赖；遵循 CLAUDE.md 阈值（单文件 ≤800 行、单函数 ≤50 行、参数 ≤5、嵌套 ≤4）；公开函数写 docstring；错误显式传播；中文注释只解释「为什么」。
- 不运行 `git commit`（由 Subtask #3 完成）。

---

## 九、实施过程发现的方案缺陷

实施过程**未发现方案缺陷**——v2 各关键事实逐一核验均成立并按原设计落地：`Switch` 仅认 `label`
形参并内部映射 `aria-label`（故卡片用 `label` 承载读屏文案）、`ToolRegistry._tools` dict 保序使
`tool_meta()` / 列表接口顺序与 `_ALL_TOOLS` 一致、`init_db()` 的 `create_all` 对新表 `agent_tool_states`
幂等补表、`main.py` 已把 `HTTPException(detail={code,message})` 归一为 `{"error":{...}}`（404
`TOOL_NOT_FOUND` 形状与既有 provider 路由一致）。全部 26 项文件清单 + §6.1 的 P1-3 loop 门控测试
均已落地：后端 `pytest` 全量 286 通过；前端 `vitest` 179 通过、`tsc --noEmit` 与 `next build` 均零错误。

---

## 评审结论（Review Verdict）

**结论：通过（Approved）。**

本方案以真实仓库代码逐节核验后定稿——技术栈完全支撑其全部诉求，且与既有「AI 模型服务」的落库 + 每轮解析 + 脱敏 REST 范式高度同构，无新增依赖、不触碰撮合 / 回测 / OKX 等核心数据路径，适度且可回退。评审发现 **0 个 P0**；3 个 P1 已就地修复并随版本升至 **v2**：

- **P1-1**（一致性）：§2.9 `Switch` 改用真实的 `label` 形参（而非会被静默丢弃的 `aria-label`），守住 §6.3-5 的读屏可达验收。
- **P1-2**（测试隔离）：§6.1 增 `_reset_tool_states()` 并要求每用例起始重置，消除共享临时 DB 下稀疏表的顺序相关偶发失败。
- **P1-3**（核心链路覆盖）：§6.1 增 `resolve_enabled_tools` 合并单测与 loop 级启用门控单测，使「开关真正生效」具备回归护栏。

2 个 P2 已澄清（P2-1 仓储写入内部 `commit`）/ 确认非缺陷（P2-2 全禁用 `tools=[]` 为既有路径）。

**交付下游（Subtask #2 代码开发）的非阻塞约束**——按 v2 落地时务必遵守：
1. `ToolToggleCard` 的 `Switch` 用 `label` 形参传无障碍文案，禁写 `aria-label`。
2. `set_tool_enabled` / `set_all_tool_states` 在仓储函数内部 `commit`；`get_tool_states` 只读不提交。
3. 后端三个端点的单测起始调用 `_reset_tool_states()`；并落 §6.1 列出的 `resolve_enabled_tools` 与 loop 门控测试。
4. 工具元信息只来自后端运行时同一 `ToolRegistry` 实例（`tool_meta()` 保序），前端不硬编码工具清单。

无 P0 / P1 遗留，可进入开发阶段。
