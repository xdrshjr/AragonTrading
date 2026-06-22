# 设置中心 · AI 模型服务商管理 — 设计方案 (spec)

> 文档版本：**v2**（方案评审与修复 / Subtask #1 产出 —— 基于 v1 就地评审并修复全部 P0/P1）
> Feature slug：`agent-model-provider-settings`
> 作者：Solution Architect（Anthropic Eng）｜评审：Senior Reviewer（Anthropic Eng）
> 关联全局目标：完成「设置」功能界面，可靠、完善、稳健，功能正确。
> 关联既有方案：`docs/plans/agent-trading-copilot/spec.md`（v2，本方案在其 Agent LLM 接入链路上扩展）。

---

## 评审记录（Review Notes / Subtask #1）

> 评审维度：**可行性 Feasibility**（能否用现有技术栈构建）｜**完备性 Completeness**（边界 / 错误路径 / 上线遗漏）｜**一致性 Consistency**（是否与 `CLAUDE.md` 及既有代码约定冲突）｜**适度性 Right-sizing**（范围是否合理、是否过度 / 欠规格）。

**已对照真实源码核验的事实（构成本方案的可行性基线，均通过）：**

- `agent/llm/factory.py`：`make_llm_client(settings) -> LLMClient` 按 `settings.agent_provider` 分发 `AnthropicClient` / `OpenAIClient`，与方案薄封装改造前提一致。
- `agent/llm/anthropic_client.py` / `openai_client.py`：两 client 构造参数与方案 `make_llm_client_from_config` 列项**逐一对齐**（anthropic 含 `anthropic_version`/`user_agent`，openai 无）；`RetryConfig`（`retry.py`，`max_retries/base_ms/max_ms`）存在。`OpenAIClient._body` 在 `tools` 为空时**不发** `tools`/`tool_choice`（line 63 `if tools:`）→ 连通性 probe 用 `tools=[]` 安全可行。
- `config.py · Settings`：`agent_provider/base_url/api_key/model/anthropic_version/user_agent/max_tokens/temperature/top_p/max_tool_rounds/max_context_messages` 等字段齐备；超时字段实为 **`agent_timeout_seconds`**、代理为 `agent_proxy` + 计算属性 **`agent_proxy_url`**、重试为 `agent_max_retries/agent_retry_base_ms/agent_retry_max_ms`（方案 `LlmProviderConfig` 的 `timeout/proxy/retry` 须据此映射，见 §2.2 修订）。
- `agent/manager.py`：`_client()` 懒建缓存 `self._llm`；`configured()=bool(settings.agent_api_key)`；`config_info()` 同步返回 `{provider,model,configured}`；`handle()` **不自检 BUSY**，仅 `create_task(sess.run(...))`，权威 BUSY 守卫在 `run()` 内层（见 P1-Race）。`self._llm` 注入口与 `self._session_factory`/`_factory()` 存在。
- `agent/loop.py`：`AgentSession._llm` 字段 + `_stream_assistant_turn` 调 `self._llm.stream(...)`，故"热切 client"可行；会话由 `get_or_create` 按 convId **唯一复用**、跨连接存活；BUSY（`run()` 内层守卫）为权威。
- `api/agent.py`：`/agent/config` 路由为 **`async def`**、WS `ready` 在 async 上下文且均调 `config_info()` —— 二者是**仅有**的两个调用点，故 `config_info()` 改 `async` + `await` 安全无遗漏。
- `db/`：`Base`/`Strategy`/`_utcnow`（`onupdate`）齐备；`init_db` 走 `create_all` + `_seed_builtin_strategies`（方案 `_seed_agent_providers` 与之同构）；`repositories.py` 为**函数式**（首参 `session`）。
- `main.py`：`app.include_router(agent.router, prefix="/api")` → 后端实际路径 `/api/agent/*`；前端 `next.config.mjs` 把 `/api/py/:path*` 重写至 `${PY_BACKEND_URL}/api/:path*` → 前端 `/api/py/agent/providers` 命中后端，方案接口路径成立。
- 错误体 `{error:{code,message}}`（`schemas.ErrorBody/ErrorResponse` + `main.py` 异常处理器）与 `AGENT_NOT_CONFIGURED/AGENT_AUTH_ERROR/BUSY` 错误码均存在。
- 前端：`store.ts` **现 `version: 8`**（方案"升 v9"成立）、`migrate` 逐版补默认、persist key `aragon-ui`、`partialize` 已含 `rightTab/agentConversationId`；`page.tsx` 顶栏顺序 `AccountModeSwitch → 主题 Button(ghost/sm) → WindowControls`、主区为 grid；`components/ui/index.tsx` 已有 `Button(default|primary|ghost|danger, sm|md)/Card/Badge/Switch/Select/Tabs/Skeleton`、**无 `Input`/`Field`**（方案新增成立）；设计 token 全部存在；无三方图标库、既有内联 SVG（`WindowControls`）；`AgentUnconfigured` 组件存在；React Query v5 + Provider + 数组 query key 既有。
- `.env`：`AGENT_*`（真实 KIMI key、`kimi-k2.6`、`https://api.kimi.com/coding`）齐备；`desktop/scripts/build-agent-env.mjs` 拷贝 `AGENT_*` 进打包资源 → seed 可据 Settings 种入真实 KIMI 默认。

**问题清单与处置：**

| # | 维度 | 严重度 | 问题 | 处置 |
|---|---|---|---|---|
| P1-URL | 可行性 / 正确性 | **P1** | 两 client **硬拼** `url = f"{base_url}/v1/chat/completions"`（`openai_client.py:82`）与 `f"{base_url}/v1/messages"`（`anthropic_client.py:81`）—— base_url 是**根**、版本+资源段由 client 追加。但 §5.2 seed 的 OpenAI 预置 `https://api.openai.com/v1` 会被拼成 `…/v1/v1/chat/completions`（双 `/v1`，404）；Gemini 预置 `…/v1beta/openai` 会被拼成 `…/v1beta/openai/v1/chat/completions`（Google OpenAI 兼容真实路径是 `…/v1beta/openai/chat/completions`，因 client 强制插入 `/v1/` 段而**无任何 base_url 能拼出**）。→ 三个必需官方服务中 **OpenAI、Gemini 两者根本连不通**，"测试连通性"必败，违背验收 #3/#4。 | ✅ 已修复：(a) OpenAI 预置 base_url 改为根 `https://api.openai.com`（与 KIMI/anthropic 同为根，client 追加 `/v1/…` ✓）；(b) 为 `OpenAIClient`（及对称的 `AnthropicClient`）加**最小、向后兼容**的"整端点直通"：`base_url` 若已以资源路径结尾（`/chat/completions` 或 `/messages`）则**原样 POST**，否则按现行追加标准后缀；Gemini 预置 base_url 改为完整端点 `https://generativelanguage.googleapis.com/v1beta/openai/chat/completions`。既有根 URL 行为零变化，且**顺带支持自定义 OpenAI 兼容服务的非标准路径**。见 §2.3 / §3.1（新增 client 改动 + 测试）/ §5.2 / R-URL。 |
| P1-Race | 稳健性 / 并发 | **P1** | §2.4 原设计在 `handle()` 内 `sess.set_client(client)` **先于** `create_task(sess.run(...))`。但 WS 层 `_handle_user_message` 的 `if sess.running` 仅 **best-effort**（`agent.py:84` 注释明示"权威是 run() 内层守卫"）。并发/跨重连消息若擦过 best-effort 检查仍进入 `handle`，就会在**进行中 run** 的会话上改写 `self._llm` —— 把正在流式的 run 的 LLM client 中途换掉，行为不可预期，违背"稳健可靠"。 | ✅ 已修复：`handle()` **只解析一次** cfg（轻量 DB 读）：`None`→回 `AGENT_NOT_CONFIGURED` 直接返回；否则把解析出的 client **作为入参传入** `sess.run(user_text, ui, client=client)`，由 `run()` 在**通过其内层权威 BUSY 守卫之后、循环开始之前**才 `self._llm = client` 绑定。落败的并发轮在绑定前即被 BUSY 拒绝，**永不**触碰 in-flight client。"设为默认对下一轮生效"语义不变。见 §2.4 / §3.1（loop.py 改 run 签名）/ R-RunSwitch。 |
| P2-Settings | 一致性 | P2 | `LlmProviderConfig` 的 `timeout/proxy/retry` 字段名与 Settings 实际名不同（`agent_timeout_seconds`/`agent_proxy_url`/`agent_max_retries…`）。 | 实现期 `provider_config_from_settings`/`config_from_row` 显式映射；已在 §2.2 标注，避免取错字段名。 |
| P2-Fallback | 完备性 | P2 | "删默认后回落首个含 key 行"中"首个"未定序，多含 key 行时行为不确定。 | 明确 `ORDER BY id ASC` 取**最小 id 的含 key 行**为新默认（确定、可复现）；无含 key 行则不设默认（解析器回落 env/未配置）。见 §5.1。 |
| P2-Gemini | 可行性 | P2 | Gemini OpenAI 兼容端点对 `stream_options.include_usage` / `max_tokens` 的接受度未实证（OpenAIClient 恒发 `stream_options`）。 | 经"测试连通性"真发一次极小请求即可暴露不兼容并提示；若 Google 端拒收 `stream_options` 再于实现期按 provider 收敛（本期不阻断）。见 R-Gemini。 |
| P2-Kimi | 一致性 / 需求符合 | P2 | 需求列官方为 OPENAI/ANTHROPIC/GEMINI、自定义才可删；方案把 KIMI 作**第 4 个 official（不可删）**。 | 维持：KIMI 设为不可删 official 是**有意**为之——它承载 env 凭据、是解析器 env 回落的锚点，删除会令"内置默认服务"语义自相矛盾；可编辑（改/补 key）已满足管理诉求。见 §5.2 注。 |
| P2-Persist | 一致性 | P2 | v9 迁移须同时：`partialize` **新增** `settingsSection`、**排除** `showSettings`（瞬态）。 | 已在 §5.3 明确；`migrate` `if (version < 9)` 补 `settingsSection:"ai-models"`，`showSettings` 不入 `partialize`。 |
| P2-Schema | 一致性（CLAUDE.md/风格） | P2 | 新增 schema 的 camelCase 落法须随既有 `schemas.py`（alias 还是字面 camelCase 字段名）。 | 实现期对照既有 `trade_out/run_out` 写法保持一致；已在 §8 标注。 |

> **结论摘要**：本方案**无 P0 级阻断项**；**2 个 P1（P1-URL / P1-Race）已在本 v2 文档正文就地修复**（无遗留 P0/P1）。P2 为实现期确认/收敛项，已在文末 §评审结论 列为放行条件。

---

## 1. Overview（概述）

本方案为 Aragon Trading 工作台新增一个**「设置中心」功能界面**，并落地其第一项设置：**AI 模型服务商管理（AI Model Provider Settings）**。入口位于顶栏「亮色 / 暗色」主题切换按钮**右侧的一个齿轮图标**；点击后主内容区由「交易工作台」**切换为「设置面板」**。设置面板采用**左右布局**：左侧约 15% 宽为设置项导航列表，右侧约 75% 宽为该设置项的具体功能界面（两者之间留出对称留白做视觉呼吸，符合顶级设计的版式节奏）。本期左侧仅一项功能性条目「AI 模型服务」，并预留可扩展的条目注册结构。

「AI 模型服务」面板让用户**新增 / 编辑 / 删除 AI 模型的 API 接入配置**。配置默认分两类：**官方模型**（OpenAI / Anthropic / Gemini，三者为固定预置项，可填写 APIKEY / APIURL / MODEL-NAME，但不可删除）与**自定义模型**（用户可任意新增、编辑、删除，同样配置 APIKEY / APIURL / MODEL-NAME，并可选择该自定义服务遵循的 API 标准）。每个服务商均支持**一键测试连通性**（真实向其端点发一次极小请求，校验 APIKEY / APIURL / MODEL 三者整体可用并报告往返时延）。用户可把任一已配置服务**设为默认服务**；设为默认后，对话式 AI 助手（Agent Trading Copilot）的下一轮对话即改用该默认服务的 provider / base_url / api_key / model。**当前 `.env` 中默认的 KIMI 模型服务，会在首次初始化时作为一个内置默认服务自动种入**，并默认选为默认服务，保证「安装即用」。

该能力构建在既有 Agent 接入链路之上而非另起炉灶：现状是 Agent 的 LLM 配置**只从环境变量 / `.env` 一次性读入**（`config.py · Settings.agent_*` → `make_llm_client(settings)` → `AgentManager`）。本方案把「provider 配置」从「仅环境变量」升级为「**用户可管理、持久化、运行期可切换**」，新增一张 SQLite 表 `agent_provider` 承载多服务商配置（密钥仅存后端，绝不下发前端），新增一个**运行期解析器**在每轮对话开始时解析「当前默认服务」构造 LLM 客户端；当库内无默认服务时安全回落到 `.env` 既有配置，保持向后兼容。前端复用既有设计 token 与组件原语，新增「设置中心」外壳与「AI 模型服务」面板，经既有 `/api/py/*` 代理读写后端 REST。

设计目标可归纳为三条硬约束：(1) **功能正确**——增删改、测试、设默认、Agent 实际改用默认服务，端到端闭环；(2) **稳健可靠**——密钥不泄露、默认服务唯一性事务保证、解析器回落不崩、与既有 env-only 行为兼容；(3) **美观优雅**——15/75 版式、卡片化服务商、内联齿轮图标、连通性结果即时反馈，与既有暗 / 亮主题一致。

---

## 2. Technical design（技术设计）

### 2.1 总体架构

```text
┌──────────────────────── apps/web (Next.js 14) ────────────────────────┐
│ app/page.tsx                                                           │
│  header ── [AccountModeSwitch] [亮/暗 Button] [⚙ SettingsGearButton] [WindowControls]
│  main 区：showSettings ? <SettingsPanel/> : <主工作台 grid>             │
│           SettingsPanel = 左 SettingsNav(15%) | 右 AiModelsSection(75%) │
│             AiModelsSection ──(React Query)──▶ agentApi.providers.*     │
└────────────────────────────────┬──────────────────────────────────────┘
                  REST  /api/py/agent/providers*  (Next rewrite → 后端 /api/agent/*)
                                  │
┌──────────────────────── services/quant (FastAPI) ─────────────────────┐
│ api/agent.py   新增 REST：providers CRUD / default / test；config 扩展  │
│  └─ agent/providers.py  LlmProviderConfig + resolve_active_provider +   │
│                          probe_provider + OFFICIAL_PRESETS              │
│  └─ agent/llm/factory.py  make_llm_client_from_config(cfg)（新）         │
│  └─ agent/manager.py  每轮 run 解析默认服务并热切 AgentSession 的 client │
│  db/models.py  新增 AgentProvider 表                                    │
│  db/repositories.py  provider CRUD + seed                              │
│  db/session.py  init_db 调 _seed_agent_providers（种入 .env KIMI 默认）  │
└────────────────────────────────────────────────────────────────────────┘
```

**关键决策（与既有约定一致）：**

- **密钥只在后端**：沿用 README「安全」铁律与 `agent-trading-copilot` 方案。provider 配置（含 api_key）落 SQLite（桌面端 DB 在 `userData`，dev 期 `aragon.db` 已 gitignore）；列表 / 详情接口**绝不返回原始 key**，仅返回 `hasKey` 布尔与末 4 位 `keyPreview`。
- **运行期解析而非启动期固定**：现状 `make_llm_client(settings)` 在 `AgentManager` 首次用时懒构建并缓存。本方案改为**每轮 `handle()` 解析默认服务 → 构造 client → 热切到 `AgentSession`**，使「设为默认」对**下一轮对话即时生效**，无需重启后端。
- **向后兼容回落**：`resolve_active_provider` 优先取库内 `is_default=true` 且含 key 的行；若无（全新库 / 用户删空 / 库内默认无 key），回落 `Settings.agent_*`（env / `.env`）。既有仅靠 `.env` 跑 Agent 的部署零改动可用。
- **复用双标准适配**：既有 `AnthropicClient` / `OpenAIClient` 已覆盖 Anthropic Messages 与 OpenAI Chat Completions 两标准。每个 provider 配置带 `api_standard` 字段决定走哪个 client。**Gemini 经 Google 的 OpenAI 兼容端点**（`https://generativelanguage.googleapis.com/v1beta/openai`）以 `openai` 标准接入，零新增 client（详见 §7 R-Gemini 与未来增强）。
- **零新增三方依赖 / 零桌面源码特例**：复用 httpx / pydantic / drizzle 之外既有栈；桌面端因 provider 落 `userData` DB、env 注入既有，**无需改打包脚本**。

### 2.2 后端：provider 配置模型与解析器（`agent/providers.py` 新）

```python
@dataclass(frozen=True)
class LlmProviderConfig:
    """解析出的「当前生效服务」——喂给 factory 构造 client。tuning 取自 Settings。"""
    provider_id: int | None       # 库内行 id（env 回落为 None）
    slug: str                     # openai|anthropic|gemini|kimi|<uuid>
    label: str
    api_standard: str             # "anthropic" | "openai"
    base_url: str
    api_key: str
    model: str
    user_agent: str               # anthropic/kimi 必需 UA；openai 忽略
    # —— 以下 tuning 统一取自 Settings，provider 行不单独存（避免重复与漂移）——
    max_tokens: int
    temperature: float
    top_p: float
    timeout: float
    anthropic_version: str
    proxy: str | None
    retry: RetryConfig
```

- `OFFICIAL_PRESETS: list[PresetMeta]`：openai / anthropic / gemini 三个固定预置的 `slug / label / api_standard / base_url / 默认 model / user_agent`（key 留空，待用户填）。
- `provider_config_from_settings(settings) -> LlmProviderConfig`：把 `settings.agent_*` 包成 config（env 回落 & probe 复用）。
- `config_from_row(row: AgentProvider, settings) -> LlmProviderConfig`：库行 + Settings tuning 合并；tuning 须按 **Settings 实际字段名**映射（P2-Settings）——`timeout=settings.agent_timeout_seconds`、`proxy=settings.agent_proxy_url`、`retry=RetryConfig(max_retries=settings.agent_max_retries, base_ms=settings.agent_retry_base_ms, max_ms=settings.agent_retry_max_ms)`、`max_tokens=settings.agent_max_tokens` 等；`api_standard / base_url / api_key / model / user_agent` 取行。`provider_config_from_settings(settings)` 同源映射（env 回落与 probe 复用）。
- `async resolve_active_provider(session_factory, settings) -> LlmProviderConfig | None`：
  1. 查 `is_default=true` 行；若存在且 `api_key` 非空 → `config_from_row`。
  2. 否则若 `settings.agent_api_key` 非空 → `provider_config_from_settings`（env 回落）。
  3. 否则 → `None`（未配置，`handle` 回 `AGENT_NOT_CONFIGURED`）。
- `async probe_provider(cfg, settings) -> dict`：连通性测试。**先做边界校验**（C5，避免无谓网络往返）：`cfg.api_key` 为空 → `{ok:False, code:"AGENT_NOT_CONFIGURED", message:"未填写密钥"}`；`cfg.base_url` 缺 `http(s)://` 前缀 → `{ok:False, code:"INVALID_BASE_URL"}`。通过后 `client = make_llm_client_from_config(cfg)`（用更小 `max_tokens`，如 16）；`async for ev in client.stream(system="connectivity probe", messages=[LlmMessage("user",[TextBlock("ping")])], tools=[])`（`tools=[]` 时 client `_body` 不发 `tools/tool_choice`，已核实）：遇 `error` → `{ok:False, code, message}`；遇首个 `text_delta` 或 `message_done` → `{ok:True, latencyMs, model}`；整体包 `asyncio.wait_for(timeout=min(cfg.timeout,15))`；捕获异常 → `{ok:False, code:"CONNECTION_FAILED", message}`。401/403 经既有 retry 层映射为 `AGENT_AUTH_ERROR`，probe 据此提示「密钥无效」。**绝不记录 key**。

### 2.3 后端：factory 改造（`agent/llm/factory.py` 改）

```python
def make_llm_client_from_config(cfg: LlmProviderConfig) -> LLMClient:
    std = (cfg.api_standard or "anthropic").strip().lower()
    if std == "anthropic":
        return AnthropicClient(base_url=cfg.base_url, api_key=cfg.api_key, model=cfg.model,
            anthropic_version=cfg.anthropic_version, user_agent=cfg.user_agent,
            max_tokens=cfg.max_tokens, temperature=cfg.temperature, top_p=cfg.top_p,
            timeout=cfg.timeout, proxy=cfg.proxy, retry=cfg.retry)
    if std == "openai":
        return OpenAIClient(base_url=cfg.base_url, api_key=cfg.api_key, model=cfg.model,
            max_tokens=cfg.max_tokens, temperature=cfg.temperature, top_p=cfg.top_p,
            timeout=cfg.timeout, proxy=cfg.proxy, retry=cfg.retry)
    raise ValueError(f"未知 api_standard: {std!r}（仅支持 anthropic | openai）")

def make_llm_client(settings: Settings) -> LLMClient:    # 既有签名保留，改为薄封装（向后兼容）
    return make_llm_client_from_config(provider_config_from_settings(settings))
```

> 既有调用 `make_llm_client(settings)` 行为不变；新增 `_from_config` 供 manager / probe 复用。

**配套修正：client URL 构造支持整端点直通（P1-URL，`openai_client.py` / `anthropic_client.py` 改）。** 现状两 client 把 base_url 当**根**并硬追加版本+资源段：`url = f"{base_url}/v1/chat/completions"` / `f"{base_url}/v1/messages"`。这对"根 URL"型 provider（OpenAI 根 / KIMI `…/coding` / Anthropic 根）正确，但对 **Gemini OpenAI 兼容端点**（真实路径 `…/v1beta/openai/chat/completions`）因强制插入 `/v1/` 段而**永远拼不出**。最小、向后兼容的修正——在 `stream()` 内按 base_url 是否已含资源段择路：

```python
# openai_client.py（anthropic_client.py 对称，资源段为 "/messages"）
base = self._base_url            # 已在 __init__ 经 rstrip("/")
url = base if base.endswith("/chat/completions") else f"{base}/v1/chat/completions"
```

- 根 URL（`https://api.openai.com`、`https://api.kimi.com/coding`、`https://api.anthropic.com`）不以资源段结尾 → 仍追加 `/v1/…`，**行为零变化**（既有 `make_llm_client(settings)` 路径完全不受影响）。
- Gemini 预置改为**完整端点** `https://generativelanguage.googleapis.com/v1beta/openai/chat/completions` → 以 `/chat/completions` 结尾 → 原样 POST ✓。
- 额外收益：**自定义 OpenAI 兼容服务**（如自建网关）可直接粘贴完整 completions URL，不受 `/v1/` 假设束缚。

### 2.4 后端：AgentManager 改造（`agent/manager.py` 改）

现状：`_client()` 懒建并缓存 `make_llm_client(self._settings)`；`configured()` / `config_info()` 同步读 `self._settings`。改为**运行期解析**：

- `async def _resolve_client(self) -> LLMClient | None`：若注入了 `self._llm`（测试桩）直接返回；否则 `cfg = await resolve_active_provider(self._factory(), self._settings)`；`None` 即未配置返回 `None`，否则 `make_llm_client_from_config(cfg)`。
- `async def active_config(self) -> dict`：`cfg = await resolve_active_provider(...)`；返回 `{configured: cfg is not None, provider: cfg.api_standard if cfg else settings.agent_provider, model: cfg.model if cfg else settings.agent_model, providerId: cfg.provider_id, label: cfg.label}`。用于 WS `ready` 与 REST `/agent/config`。
- `config_info()` → 改为 `async`（调用 `active_config()`）。**已核实** `api/agent.py` 的 WS `ready`（async 上下文）与 `/agent/config`（`async def` 路由）是**仅有**两个调用点，改 `await` 即可，无同步残留调用。
- `handle()`（**P1-Race 修订**）：先 `client = await self._resolve_client()`；`None` → `sess.notify(_error("AGENT_NOT_CONFIGURED", …))` 直接返回；否则**把 client 作为入参传入 run**：`asyncio.create_task(sess.run(user_text, ui, client=client))`。**不在 handle 内改写 `sess._llm`**。
- `AgentSession.run(self, user_text, ui, *, client=None)`（**loop.py 改**）：在其**内层权威 BUSY 守卫通过之后、进入多轮循环之前**，若 `client is not None` 则 `self._llm = client`。这样：①擦过 WS best-effort 检查的并发轮在 BUSY 守卫处即被拒，**绑定永不发生**，进行中 run 的 client 不被中途替换；②真正赢得 run 槽的那一轮才换用最新默认服务，"设为默认对**下一条消息**生效"语义不变。
- `configured()`（被 `handle` 早返用）即上面"`_resolve_client() is None` → AGENT_NOT_CONFIGURED"的内联判定，不再单独读 `settings.agent_api_key`。

> 解析在每轮 `handle` 发生、绑定在 run 内层守卫后发生 → 「设为默认」对**下一条消息**即生效；进行中的 run **既不被打断、也不被换 client**（沿用既有 run 归属会话语义 + 单 run/会话锁）。性能：解析是一次轻量 DB 查询 + 对象构造，毫秒级，可接受。

### 2.5 关键时序

```text
① 用户点齿轮 → store.setShowSettings(true) → main 区渲染 <SettingsPanel/>
② AiModelsSection 挂载 → useQuery(['agent','providers']) → GET /api/py/agent/providers
   → 渲染官方组(OpenAI/Anthropic/Gemini)+自定义组(含内置 KIMI 默认) + 顶部「当前默认」横幅
③ 用户编辑 Anthropic 的 APIKEY/URL/MODEL → 点「测试连通性」
   → POST /agent/providers/{id}/test（库内已存用存量 key；草稿用表单 key）
   → 后端 probe_provider 真发一次极小请求 → {ok,latencyMs} → 卡片显示「✓ 连通 320ms」
④ 用户点该卡「设为默认」→ POST /agent/providers/{id}/default
   → 后端事务：清其余 is_default、置本行 → 返回最新列表 → 横幅更新
⑤ 用户回到工作台，在 AI 助手发消息 → manager.handle 解析默认=Anthropic
   → make_llm_client_from_config → 作为入参传入 sess.run(..., client=)
   → run() 过内层 BUSY 守卫后绑定 self._llm → 本轮对话改用 Anthropic 服务
```

---

## 3. File / module change plan（文件 / 模块变更计划）

> 约定：新建标 **[新]**，修改标 **[改]**。前端 TS camelCase，后端 Python PEP8 + Google docstring；单文件 ≤800 行、单函数 ≤50 行、参数 ≤5、嵌套 ≤4、圈复杂度 ≤10（CLAUDE.md 阈值）。

### 3.1 后端（services/quant）

| 文件 | 类型 | 一句话意图 |
|---|---|---|
| `app/db/models.py` | [改] | 新增 `AgentProvider` 模型（slug/kind/label/api_standard/base_url/api_key/model/user_agent/is_default/时间戳）|
| `app/db/repositories.py` | [改] | 新增 provider CRUD：`list/get/get_by_slug/create/update/delete/get_default/set_default/seed_if_empty` |
| `app/db/session.py` | [改] | `init_db` 增调 `_seed_agent_providers(settings)`（幂等，仅空表时种官方预置 + `.env` KIMI 默认）|
| `app/agent/providers.py` | [新] | `LlmProviderConfig` / `OFFICIAL_PRESETS` / `provider_config_from_settings` / `config_from_row` / `resolve_active_provider` / `probe_provider` |
| `app/agent/llm/factory.py` | [改] | 新增 `make_llm_client_from_config(cfg)`；`make_llm_client(settings)` 改薄封装（兼容）|
| `app/agent/llm/openai_client.py` | [改] | **P1-URL**：`stream()` 整端点直通——base_url 以 `/chat/completions` 结尾即原样 POST，否则按现行追加 `/v1/chat/completions`（根 URL 行为零变化）|
| `app/agent/llm/anthropic_client.py` | [改] | **P1-URL**（对称）：`stream()` base_url 以 `/messages` 结尾即原样 POST，否则追加 `/v1/messages`（KIMI/anthropic 根行为零变化）|
| `app/agent/manager.py` | [改] | `_resolve_client` / `active_config`（async）；`handle` 每轮解析默认并**把 client 传入 `sess.run(..., client=)`**（不再 set_client）；`config_info` 改 async |
| `app/agent/loop.py` | [改] | **P1-Race**：`AgentSession.run(..., *, client=None)` 在内层 BUSY 守卫**通过后**才 `self._llm = client`（杜绝换掉 in-flight run 的 client）|
| `app/api/agent.py` | [改] | 新增 providers REST（GET/POST/PUT/DELETE + default + test）；`/agent/config` 与 WS `ready` 改 `await active_config()` |
| `app/schemas.py` | [改] | 新增 `AgentProviderOut/AgentProviderIn/AgentProviderUpdate/ProviderTestReq/ProviderTestResult/ProvidersListOut`；`AgentConfigOut` 增 `providerId/label`（可选，兼容）|
| `app/main.py` | [—] | 无需改（`agent.router` 已挂载；新路由同 router 内）|

### 3.2 后端测试（services/quant/tests）

| 文件 | 类型 | 一句话意图 |
|---|---|---|
| `tests/test_agent_providers_repo.py` | [新] | CRUD 往返、`set_default` 唯一性、删除默认后回落、`seed_if_empty` 幂等 |
| `tests/test_agent_provider_resolve.py` | [新] | `resolve_active_provider` 三分支（库默认 / env 回落 / None）|
| `tests/test_agent_provider_probe.py` | [新] | 假 LLMClient 注入：ok / error / 异常 → ProviderTestResult；空 key/非法 url 边界先校验不发网络 |
| `tests/test_agent_provider_api.py` | [新] | REST：列表脱敏（无 raw key）、PUT 空 key 保留、删官方 403/拒绝、设默认、test 端点 |
| `tests/test_llm_client_url.py` | [新] | **P1-URL**：openai/anthropic client URL 构造——根 URL（OpenAI 根 / KIMI `…/coding` / anthropic 根）追加 `/v1/…`；整端点（Gemini `…/v1beta/openai/chat/completions`）原样直通 |
| `tests/test_agent_run_client_bind.py` | [新] | **P1-Race**：赢得 run 槽的轮绑定传入 client；并发擦过 best-effort 的第二轮在 BUSY 守卫处被拒、**不改写** in-flight `self._llm` |

### 3.3 前端（apps/web）

| 文件 | 类型 | 一句话意图 |
|---|---|---|
| `components/Settings/SettingsGearButton.tsx` | [新] | 顶栏齿轮按钮（内联 SVG，`Button variant="ghost" size="sm"`，toggle `showSettings`，active 高亮）|
| `components/Settings/SettingsPanel.tsx` | [新] | 设置外壳：顶部标题「设置」+ 关闭返回；body 左右布局（15% 导航 / 75% 内容 + 留白）|
| `components/Settings/SettingsNav.tsx` | [新] | 左侧设置项列表（本期一项「AI 模型服务」，结构可扩展）|
| `components/Settings/AiModelsSection.tsx` | [新] | 右侧主面板：当前默认横幅 + 官方组 + 自定义组 + 「添加自定义模型」|
| `components/Settings/ProviderCard.tsx` | [新] | 单服务商卡：标签/标准徽章/默认徽章/key 状态/测试按钮+结果/设默认/编辑/删除 |
| `components/Settings/ProviderEditForm.tsx` | [新] | 卡内编辑表单（APIKEY 密码框 + APIURL + MODEL；自定义另含 label + api_standard）|
| `components/Settings/ConnectivityResult.tsx` | [新] | 连通性结果行（loading / ✓ 连通+时延 / ✗ 错误码+消息）|
| `components/ui/index.tsx` | [改] | 新增 `Input`（密码/文本，复用设计 token）与 `Field`（label+控件+错误）原语 |
| `app/page.tsx` | [改] | 顶栏插入 `<SettingsGearButton/>`（主题按钮与 `WindowControls` 之间）；main 区 `showSettings ? <SettingsPanel/> : <主工作台>` |
| `lib/store.ts` | [改] | 新增 `showSettings`(瞬态，不持久化) + `setShowSettings`；`settingsSection`(持久化) + `setSettingsSection`；persist 升 v9 + migrate |
| `lib/agentApi.ts` | [改] | `agentApi.providers`：`list/create/update/remove/setDefault/test/testDraft` |
| `lib/agentTypes.ts` | [改] | 新增 `AgentProvider/ProviderDraft/ProviderTestResult/ProvidersListResponse/ApiStandard/ProviderKind` |
| `lib/settings.ts` | [新] | 纯函数：`OFFICIAL_PRESET_UI`、`maskKey`、`validateProviderDraft`、`groupProviders`（带单测）|
| `lib/settings.test.ts` | [新] | 校验 / 分组 / 脱敏纯函数单测 |

### 3.4 桌面端 / 环境

| 文件 | 类型 | 一句话意图 |
|---|---|---|
| （无新增文件）| [—] | provider 配置落 `userData` DB；既有 `build-agent-env.mjs` + env 注入已把 `.env` KIMI 凭据带进打包应用，首次 `init_db` 据此 seed 默认服务，**无需改打包脚本**|
| `.env.example` | [改·可选] | 在 `# ---- Agent ----` 段补一行注释，说明「该 KIMI 服务会被设置中心作为内置默认服务自动种入，可在 UI 改 / 增 / 删」|

---

## 4. Interface design（接口设计）

### 4.1 REST（经 `/api/py/*` 代理 → 后端 `/api/agent/*`，沿用 `{error:{code,message}}` 错误体）

| Method | Path | 请求体 | 响应 | 说明 |
|---|---|---|---|---|
| GET | `/agent/providers` | — | `ProvidersListOut` | 全量服务商（脱敏）+ 当前默认 + active 摘要 |
| POST | `/agent/providers` | `AgentProviderIn` | `AgentProviderOut` | 新增自定义服务（`kind="custom"`）|
| PUT | `/agent/providers/{id}` | `AgentProviderUpdate` | `AgentProviderOut` | 编辑（`apiKey` 留空=保留原 key；官方项不可改 slug/kind）|
| DELETE | `/agent/providers/{id}` | — | `204` | 删除（仅 `deletable=true` 即自定义；删的若是默认→回落另一含 key 项或 env）|
| POST | `/agent/providers/{id}/default` | — | `ProvidersListOut` | 设为默认（事务清其余 `is_default`）|
| POST | `/agent/providers/{id}/test` | — | `ProviderTestResult` | 测存量服务连通性（用库内 key）|
| POST | `/agent/providers/test` | `ProviderTestReq` | `ProviderTestResult` | 测草稿连通性（首存前；须带显式 `apiKey`）|
| GET | `/agent/config` *(改)* | — | `AgentConfigOut`(+`providerId`,`label`) | 反映**当前生效**服务；保持既有字段，仅追加 |

**响应 / 请求形状（camelCase，与前端 TS 对齐）：**

```ts
type ApiStandard = "anthropic" | "openai";
type ProviderKind = "official" | "custom";

interface AgentProviderOut {        // 列表 / 详情：绝不含 raw key（R-Sec）
  id: number; slug: string; kind: ProviderKind; label: string;
  apiStandard: ApiStandard; baseUrl: string; model: string; userAgent: string;
  hasKey: boolean; keyPreview: string;   // 末4位，如 "····a1b2"；无 key 为 ""
  isDefault: boolean; deletable: boolean; createdTs: number; updatedTs: number;
}
interface ProvidersListOut {
  providers: AgentProviderOut[]; defaultId: number | null;
  active: { configured: boolean; providerId: number | null; label: string;
            provider: string; model: string };   // 解析后的当前生效服务
}
interface AgentProviderIn   { label: string; apiStandard: ApiStandard; baseUrl: string;
                              apiKey: string; model: string; userAgent?: string; }
interface AgentProviderUpdate { label?: string; apiStandard?: ApiStandard; baseUrl?: string;
                              apiKey?: string; model?: string; userAgent?: string; } // apiKey 缺/空=保留
interface ProviderTestReq   { apiStandard: ApiStandard; baseUrl: string; apiKey: string;
                              model: string; userAgent?: string; }
interface ProviderTestResult { ok: boolean; latencyMs?: number; model?: string;
                              code?: string; message?: string; } // code: AGENT_AUTH_ERROR|CONNECTION_FAILED|LLM_HTTP_ERROR…
```

**边界与错误约定（边界一次性校验，C5）：**
- `baseUrl` 须 `http(s)://` 前缀且非空；`model` 非空；`label` 非空。官方项 `apiStandard/slug` 锁定不可改。
- PUT / DELETE 不存在 id → `404 {error:{code:"PROVIDER_NOT_FOUND"}}`；DELETE 官方项 → `400 {error:{code:"PROVIDER_NOT_DELETABLE"}}`。
- POST `default` 目标无 key → 允许设默认但 `active.configured=false`（提示用户补 key）；解析器在 `handle` 时回 `AGENT_NOT_CONFIGURED`。

### 4.2 WebSocket（`/api/ws/agent/{convId}`，既有）

无需新增端点。`ready` 事件的 `{provider, model, configured}` 改由 `await manager.active_config()` 提供，反映当前默认服务（前端 `AgentUnconfigured` / 配置展示自动跟随）。

---

## 5. Data model（数据模型）

### 5.1 持久化（SQLite，新增 1 张表 `agent_provider`）

`app/db/models.py · AgentProvider`（挂既有 `Base`，`init_db` 的 `create_all` 自动建表，无需手写迁移）：

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | 自增 |
| `slug` | String(64), unique, index | `openai`/`anthropic`/`gemini`/`kimi`（官方）；自定义为 uuid |
| `kind` | String(16) | `official` \| `custom`（`deletable = kind=="custom"`，派生不入列）|
| `label` | String(128) | 展示名（如「Kimi（内置默认）」「OpenAI」）|
| `api_standard` | String(16) | `anthropic` \| `openai`（决定走哪个 client）|
| `base_url` | String(256) | APIURL |
| `api_key` | Text, nullable | APIKEY —— **仅后端存，不出接口、不进日志**（C7）|
| `model` | String(128) | MODEL-NAME |
| `user_agent` | String(128), default `"claude-code/1.0"` | anthropic/kimi 端点校验 UA；openai 忽略 |
| `is_default` | Boolean, default False | 全表至多一行 true（`set_default` 事务保证）|
| `created_at` / `updated_at` | DateTime | 复用 `Strategy` 的 `_utcnow` / `onupdate` 风格 |

- **`set_default(id)`**：单事务内 `UPDATE agent_provider SET is_default=0` 再 `SET is_default=1 WHERE id=:id`，杜绝多默认。
- **删除默认行**：删后若已无默认，自动把**含 key 且 `id` 最小**的行（`WHERE api_key != '' ORDER BY id ASC LIMIT 1`，确定可复现，P2-Fallback）置默认；若无任何含 key 行，则不设默认（解析器回落 env / 报未配置）。
- **官方预置不可删**：`delete` 前校验 `kind=="custom"`，否则拒绝。

### 5.2 Seed（`_seed_agent_providers(settings)`，幂等，仅空表时执行）

```text
若 count(agent_provider) > 0: 直接返回（保护用户既有编辑，桌面端每次启动注入 env 也不覆盖）
否则插入（base_url 须与 §2.3 client URL 构造匹配——根 URL 由 client 追加 `/v1/…`，整端点原样直通；P1-URL）：
  - openai   : openai 标准, https://api.openai.com  ← 根（client 追加 /v1/chat/completions）, model gpt-4o,          key=""
  - anthropic: anthropic 标准, https://api.anthropic.com  ← 根（client 追加 /v1/messages）,     model claude-sonnet-4-6, key="", UA=claude-code/1.0
  - gemini   : openai 标准, https://generativelanguage.googleapis.com/v1beta/openai/chat/completions  ← 完整端点（原样直通）, model gemini-2.0-flash, key=""
  - kimi     : kind=official, settings.agent_provider 标准, settings.agent_base_url（如 https://api.kimi.com/coding，client 追加 /v1/messages）,
               model=settings.agent_model, key=settings.agent_api_key, UA=settings.agent_user_agent,
               is_default=True   ← .env 默认 KIMI 服务，自动种入并设为默认
```

> 官方四项（openai/anthropic/gemini/kimi）`kind="official"` 不可删但可编辑（填 / 改 key、url、model）。KIMI 即任务要求「`.env` 默认服务作为一个默认服务种入」的落地；若 env 无 key，KIMI 行 key 为空、仍为默认，UI 引导补 key（与 `AgentUnconfigured` 一致）。

### 5.3 前端状态（Zustand，`store.ts`，persist key `aragon-ui` 升 v9）

```ts
showSettings: boolean            // 瞬态：是否处于设置面板（不入 partialize，刷新不残留）
settingsSection: string          // 持久化：当前设置项（默认 "ai-models"，便于扩展）
setShowSettings(b: boolean); setSettingsSection(s: string);
```

- `migrate` v8→v9：补 `settingsSection: "ai-models"` 默认值（沿用既有逐版补默认机制）；`showSettings` 不持久化故 partialize 不含它（每次进应用默认在工作台）。
- 服务商数据**不入 Zustand**，由 React Query（既有 `Providers` QueryClient）按 `['agent','providers']` 缓存，增删改后 `invalidateQueries` 刷新。

### 5.4 版式（15% / 75% 左右布局，精确实现）

```tsx
// SettingsPanel body（填满 header 之下）：
<div className="flex min-h-0 flex-1 justify-center gap-6 px-6 py-5">
  <nav  className="w-[15%] min-w-[160px] shrink-0 …" />     {/* 左：设置项列表 */}
  <section className="w-[75%] min-w-0 grow-0 overflow-y-auto …" />  {/* 右：功能界面 */}
</div>
```

> 左 15% + 右 75% = 90%，其余 10% 由 `gap-6` + `px-6` 对称留白与（宽屏时）`justify-center` 居中分担，构成顶级设计常见的「内容居中 + 呼吸留白」版式，而非铺满边到边。窄屏（<1024px）下右栏 `min-w-0` 自适应，卡片纵向堆叠不溢出（沿用既有响应式约定）。

---

## 6. Testing & acceptance criteria（测试与验收）

### 6.1 单元 / 集成测试（离线，不依赖真实网络）

**后端（pytest，沿用既有 async 测试与内存 sqlite / 假 client 注入）：**
- `test_agent_providers_repo.py`：create→list→update（空 key 保留原 key）→delete；`set_default` 后仅一行 true；删默认后自动回落含 key 行；`seed_if_empty` 二次调用不重复种。
- `test_agent_provider_resolve.py`：库内默认含 key→取库；库空→取 env（`settings.agent_*`）；env 也无 key→`None`。
- `test_agent_provider_probe.py`：注入假 `LLMClient`（脚本化 message_done / error / raise）→ `probe_provider` 返回 `ok=True+latencyMs` / `ok=False+code` / `CONNECTION_FAILED`；断言 key 不出现在返回与日志。
- `test_agent_provider_api.py`（FastAPI TestClient）：GET 列表无 `apiKey` 字段、仅 `hasKey/keyPreview`；POST 建自定义；PUT 空 key 保留；DELETE 官方→400；POST default→active 摘要更新；POST test→注入假 client 走通。

**前端（vitest）：**
- `lib/settings.test.ts`：`maskKey("sk-...wxyz")→"····wxyz"`；`validateProviderDraft` 拒空 url/model/非法 scheme；`groupProviders` 正确分官方 / 自定义。
- `lib/store.test.ts`（既有补充）：`setShowSettings` 切换、v8→v9 migrate 补 `settingsSection`、`showSettings` 不被持久化。

### 6.2 验收标准（Definition of Done，对齐需求逐条）

1. 顶栏「亮/暗」按钮右侧出现齿轮图标；点击主区由工作台切换为设置面板，再点（或面板内关闭）切回，状态不丢。
2. 设置面板为左右布局：左 15% 为设置项列表（含「AI 模型服务」），右 75% 为其功能界面，留白对称、明 / 暗主题一致、美观。
3. 「AI 模型服务」呈现两类：官方（OpenAI/Anthropic/Gemini）与自定义；官方可填 / 改 APIKEY/APIURL/MODEL，自定义可新增、编辑、删除同三项（+ 标准）。
4. 任一服务可「测试连通性」：真实向其端点发极小请求，返回 ✓ 连通+时延 或 ✗ 错误（密钥无效 / 连接失败 / 模型不存在），不泄露 key。
5. 可把任一服务「设为默认」；设为默认后，AI 助手**下一轮对话**改用该服务的 provider/url/key/model（端到端可验证，如换到一个真实可用服务后助手正常回答）。
6. `.env` 的 KIMI 服务在首次初始化时自动种入为内置默认服务并选中；缺 key 时优雅引导而非崩溃。
7. 密钥绝不出现在前端、接口响应、日志；列表仅 `hasKey/keyPreview`。
8. `pnpm test`（后端 pytest 非 network + 前端 vitest/tsc）全绿；桌面端打包应用内设置可读写、Agent 按默认服务工作。

---

## 7. Risks & mitigations（风险与缓解）

| # | 风险 | 缓解 |
|---|---|---|
| R-Sec | API Key 落库 / 泄露（接口、日志、前端）| key 仅存 SQLite（桌面端 `userData`、dev `aragon.db` 已 gitignore）；`AgentProviderOut` 只回 `hasKey/keyPreview`；PUT 空 key 保留原值（前端从不持有明文）；probe / 日志只打元数据（C7）|
| R-Default | 默认服务非唯一 / 删默认后空窗 | `set_default` 单事务清其余再置一；删默认自动回落首个含 key 行；解析器三级回落（库默认→env→未配置），永不崩 |
| R-Compat | 改 `config_info/_client` 破坏既有 Agent / 测试 | `make_llm_client(settings)` 签名保留；manager 保留 `self._llm` 注入口（测试桩优先）；新增解析仅在未注入时触发；既有 env-only 部署经回落零改动可用 |
| R-URL | 预置 base_url 与 client 的 `{base_url}/v1/…` 硬拼接不兼容（OpenAI 双 `/v1`、Gemini 路径拼不出）| **已修复（P1-URL）**：client `stream()` 整端点直通——base_url 含资源段即原样 POST，否则追加标准后缀；OpenAI 预置改根 URL、Gemini 预置改完整端点；根 URL 既有行为零变化，并单测覆盖（`test_llm_client_url.py`）|
| R-Gemini | Gemini 原生 API 与 OpenAI/Anthropic 标准不同 | 经 Google **OpenAI 兼容端点**（完整端点 `…/v1beta/openai/chat/completions`，配合 P1-URL 直通）以 `openai` 标准接入，零新增 client。`stream_options.include_usage`/`max_tokens` 接受度由"测试连通性"真请求暴露（P2-Gemini）；若 Google 端拒收 `stream_options`，实现期按 provider 收敛。**未来增强**：如需原生 `generateContent`，可加 `GeminiClient` 实现同 `LLMClient` 协议，`api_standard` 增 `"gemini"` 分支（不影响本期）|
| R-Probe | 连通性测试耗时 / 阻塞 / 误判 | probe 带 `wait_for(min(timeout,15s))` 上限 + 极小 `max_tokens`；异常 / 非 2xx 经既有 retry 层归一为 `error` 而非裸抛；并发由前端按钮 loading 态串行化 |
| R-Seed | 桌面端每次启动注入 env，是否覆盖用户编辑 | seed **仅空表时执行**；非空即跳过 → 用户在 UI 的改 / 增 / 删持久保留，env 仅决定首次种子 |
| R-Layout | 15/75 在窄屏溢出 / 不美观 | 右栏 `min-w-0` + 卡片纵向堆叠 + `overflow-y-auto`；`justify-center`+`gap`+`px` 构成留白；窄屏断点下左栏可收为图标列（未来增强，本期 `min-w-[160px]` 已避免压扁）|
| R-Icon | 项目无图标库（lucide 等）| 齿轮用**内联 SVG**（与项目既有「无三方图标依赖」一致），`stroke="currentColor"` 自动随主题 |
| R-Migrate | store 升 v9 影响既有持久化 | 沿用既有 `migrate` 逐版补默认；仅新增字段、不改既有字段；`showSettings` 不入 partialize 避免「刷新即停在设置页」|
| R-RunSwitch | 切默认是否影响进行中 run / 并发改写 client | **已加固（P1-Race）**：client 在 `handle` 解析、但仅在 `run()` **过内层权威 BUSY 守卫后**才绑定 `self._llm`；擦过 WS best-effort 检查的并发轮在守卫处即被拒、绑定永不发生 → 进行中 run **既不被打断也不被换 client**；新默认对下一条消息生效，行为可预期。单测 `test_agent_run_client_bind.py` 覆盖 |
| R-Standard | 用户给自定义服务选错标准（如 KIMI 选了 openai）| 自定义表单 `api_standard` 默认 anthropic，下拉可选；保存后「测试连通性」会真实暴露不匹配（403/解析失败），形成闭环纠错 |

---

## 8. 附：实现注意事项（给下游工程师）

- **跨平台路径 / 命令**：建目录用 `python -c "Path(...).mkdir(parents=True,exist_ok=True)"`；勿用 `mkdir -p` / `&&`（见任务环境约定）。
- **时间统一秒级 UTC**：`createdTs/updatedTs` 出接口时转秒级 UTC 整数（与全系统一致，C2）；模型层 `DateTime` 序列化时换算。
- **camelCase 边界对齐**：后端响应字段用 camelCase 与前端 TS 对齐（同 `schemas.py · trade_out/run_out` 既有风格）。
- **复用既有原语 / token**：新 UI 全部用 `Button/Card/Badge/Switch/Select/Tabs` + 新增 `Input/Field`，配色用 `var(--surface/-2)/--border/--text/--muted/--accent/--accent-soft/--long/--short`，自动适配明暗。
- **不破坏既有契约**：providers 为新增端点；对 `/agent/config` 与 WS `ready` 仅**追加**字段、不删既有；对 `manager` 改动保持 `self._llm` 注入口以护既有测试。
- **凭证不入库版本库（C7）**：真实 key 经 UI 写入 `aragon.db`（gitignored）/ 桌面 `userData`；下游 Subtask #3 提交务必**精确 `git add <具体路径>`、严禁 `git add -A`**，复核 diff 不含任何 key 与 `aragon.db`。

（全文 > 1500 字，覆盖 Overview / 技术设计 / 文件清单 / 接口 / 数据模型 / 测试验收 / 风险，满足 Definition of Done。）

---

## 评审结论（Review Verdict / Subtask #1）

**结论：有条件通过（Approved with conditions）。**

- 方案的核心链路（设置中心外壳 + 15/75 版式 + provider CRUD + 设默认 + 连通性测试 + Agent 运行期热切默认服务 + `.env` KIMI 种入）经**逐节对照真实源码核验**，建立在既有 `factory/manager/loop/clients/config/db/api` 与前端 `store/agentApi/ui/tokens/ReactQuery` 之上，**可用现有技术栈构建、与 `CLAUDE.md` 约定及既有命名/分层风格一致、范围适度**（无过度工程、无明显欠规格）。
- **2 个 P1 已在本 v2 文档正文就地修复，无遗留 P0/P1：**
  1. **P1-URL**：预置 base_url 与 client `{base_url}/v1/…` 硬拼接不兼容（OpenAI 双 `/v1`、Gemini 路径不可达）→ 已改 client 为"整端点直通" + 修正 OpenAI/Gemini 预置 URL，根 URL 行为零变化，附单测。
  2. **P1-Race**：每轮 `set_client` 在 `run()` 权威 BUSY 守卫前改写共享会话 client 的竞态 → 已改为"client 传入 `run()`、过守卫后绑定"，进行中 run 不被换 client，附单测。

**放行条件（实现期须落实，均为 P2，不阻断进入开发）：**

1. **P1-URL 落地完整性**：`openai_client.py`/`anthropic_client.py` 的整端点直通改动必须实现并通过 `test_llm_client_url.py`；Gemini/OpenAI 官方预置经"测试连通性"真实可连（具备有效 key 时）。
2. **P1-Race 落地完整性**：`AgentSession.run(..., client=)` 仅在 BUSY 守卫后绑定，`test_agent_run_client_bind.py` 须覆盖"并发第二轮不改写 in-flight client"。
3. **Settings 字段映射（P2-Settings）**：`config_from_row`/`provider_config_from_settings` 须用 `agent_timeout_seconds`/`agent_proxy_url`/`agent_max_retries…` 实名，勿臆造字段。
4. **默认回落确定性（P2-Fallback）**：删默认后按 `api_key!='' ORDER BY id ASC LIMIT 1` 选新默认。
5. **持久化迁移（P2-Persist）**：`store.ts` v8→v9 仅追加 `settingsSection`、`showSettings` 不入 `partialize`；附 migrate/partialize 单测。
6. **安全红线（C7）**：列表/详情接口仅回 `hasKey/keyPreview`，key 不进日志；下游 Subtask #3 精确 `git add <具体路径>`，严禁 `git add -A`，复核 diff 不含任何 key 与 `aragon.db`。

> 满足上述条件即视为完全达成全局目标"功能正确 / 稳健可靠 / 美观优雅"。准予进入 Subtask #2（代码开发）。
