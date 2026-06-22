# 设置中心 · AI 模型服务商管理 —— 迭代 2 加固方案 (spec)

> 文档版本：**v2（iter2-hardening · 评审版 / Review Pass）** — 由 Loop Iteration 2·Subtask #1「方案评审与修复」产出，对设计稿逐节评审并就地修复全部 P0/P1。
> 版本号说明：设计稿（Subtask #0）内部沿用「跨迭代连续编号」标作 v3；本仓库每个子任务流统一采用「设计→评审 = v1→v2」约定（下游 Subtask #2 亦按 `spec.md v2` 取用），故评审版在此回归标注为 **v2**，语义上 supersede 设计稿，非降级。
> Feature slug：`agent-model-provider-settings-hardening`
> 作者：Solution Architect（Anthropic Eng）／评审：Senior Reviewer（Anthropic Eng）
> 关联全局目标：完成「设置」功能需求，**可靠、完善、稳健、功能正确**。
> 基线方案（迭代 1，已落地并提交 `020681e`）：`docs/plans/agent-model-provider-settings/spec.md`（v2）。
> 本方案**只做加固增量**，不重写既有功能；下游按本文件 §3「文件 / 模块变更计划」逐项落代码。

---

## 评审记录（Review Notes · v2）

> 评审节点：Loop Iteration 2 · Subtask #1「方案评审与修复」。评审方式：对方案逐节核验（可行性 / 完整性 / 一致性 / 适度性），并对方案所引全部 `file:line` 锚点回到真实源码逐一复核——`providers.py` / `schemas.py` / `manager.py` / `api/agent.py` / 前端 `components/Settings/*` / `components/ui/index.tsx` / 既有 `tests/test_agent_provider_*`。下表 P0/P1 已在本 v2 文档**就地修复**；P2 为下游落地条件，汇总于文末「评审结论」。

**锚点复核结论（构成本评审的事实基线，均已核实为真）：**

- **H1 缺陷属实**：`providers.py:121-129` `resolve_active_provider` 当默认行存在但 `api_key` 为空时，确会跳过库行回落 `settings.agent_api_key`（env/KIMI），与「设默认即用该服务」需求矛盾。
- **H1 修复无需改 `manager.py` / `api/agent.py`**：`manager.active_config()`（98-109）与 `_resolve_client()`（87-96）已对 `resolve_active_provider() → None` 给出 `configured=false` / `AGENT_NOT_CONFIGURED`，§3.1 标 [—] 正确。
- **受 H1 影响的既有测试仅一处**：`tests/test_agent_provider_resolve.py::test_db_default_without_key_falls_back_to_env`（断言旧「缺 key 默认→env」），§3.2 已列入更新；全仓 `api_key=""` + `set_default` 组合未见于其它用例（API 测试均用含 key 行 `sk-zzzz9999`）。
- **H3 属实**：`schemas.py:173-202` 写入类模型仅 `min_length`、**无 `max_length`**，且 `AgentProviderUpdate` 各字段可空且无下限（PUT 可置空串）。
- **H4 属实**：`providers.py:144` `make_llm_client_from_config(probe_cfg)` 位于 `try`（147 起）之外。
- **`/agent/config` 为真实 GET 端点**（`api/agent.py:43`，`response_model=AgentConfigOut`），§4 引用准确。

| # | 维度 | 严重度 | 位置 | 问题 | 处置 |
|---|---|---|---|---|---|
| P1-A | 适度性 / 稳健 | **P1** | §1、§8、H4 | H4（探测期 client 构造在 `try` 外、异常可逃逸为 HTTP 500）被归为「可按工期裁剪的 P2」，但它违反本函数自述不变量（`providers.py:152`「任何异常归一、绝不裸抛」），是真实健壮性缺陷而非交互打磨；若被裁剪将给成品留 500 崩溃面，违背全局目标「稳健可靠」。 | **已修复**：将 H4 提升为与 H1/H2 同列的本轮必做底线（§1 新增 H4 段、§8 范围纪律改 `H1/H2/H4`）。 |
| P1-B | 完整性 / 安全 | **P1** | §2.2 | 密钥红action的「单点收口」描述不精确：`probe_provider` 有三处可携带动态文本的失败 message —— ① `except Exception → str(exc)`（153）、② `_consume_probe` 的 `error` 事件 `ev.message`（162-163，经 148 返回）、③ 超时分支（150，静态无 key）。原文把「单点收口」落在「对 `_consume_probe` 返回结果收口」一句，仅覆盖 ②，① 在 except 分支不经该结果，存在漏 redact 风险；另「可能进入日志」与现状不符（`probe_provider` 当前不写日志，真实暴露面是 API 响应 `ProviderTestResult.message`）。 | **已修复**：§2.2 改为精确单口径——所有 `ok is False` 返回的 message 一律过 `_redact(.., cfg.api_key)[:200]`，三处全覆盖；并把「日志」表述改为「API 响应；未来若新增日志须复用 `_redact`」。 |
| P2-C | 一致性 | P2 | §8 | H1 为正确性 bug；CLAUDE.md §七要求「修 bug 必先写可复现测试，再动业务代码」。§8 原序为「改 resolve + 同步改单测」（并行）。 | 已就地：§8 改为「先写 H1 复现测试（应先红）→ 再改 resolve（转绿）」。 |
| P2-D | 完整性 | P2 | §2.5 | H5「草稿测试按 key 禁用」须**仅**作用于「新增自定义」流程（`AiModelsSection` AddCustom）；编辑态草稿测试（`ProviderCard.tsx:125`）空 `apiKey` 语义是「保留原 key」，一并按空 key 禁用会误锁含 key 服务的测试。 | 已就地：§2.5 增范围限定句。 |
| P2-E | 完整性 | P2 | §3.2 / §6.1 | 评审已核实当前仅一条测试依赖旧行为，但应在落地前对 `tests/` 再做一次「`api_key=""` + `set_default` + 期望 env」grep，防后续新增测试漂移。 | 已就地：§6.1 回归项补一句确认性 grep。 |
| P2-F | 可行性 | P2 | §2.6 | `Field` 的 children 为不透明节点，不 `cloneElement` 无法「注入」`aria-describedby`；原文给「Field 注入 或 显式串联」两可，易诱导过度聪明的 cloneElement。 | 已就地：§2.6 收敛为「由 `ProviderEditForm` 显式把错误 `id` 串到对应 `Input` 的 `aria-describedby`」单一路径。 |

**复核后判定无虞、不另开问题的点：** REST 端点签名 / 响应形状不变、无 schema 迁移、Zustand persist v9 不动、`_redact` 仅长度 ≥6 才替换（避免短串误伤）、纯 env 部署仍回落、`set_default` 并发竞态作为单用户桌面端已知可接受项、密钥明文落本地库的威胁模型记录——均判断得当、right-sized，符合既有约定。

---

## 0. 背景与本轮定位（Why iter2，避免重复劳动）

迭代 1 已端到端交付「设置中心 · AI 模型服务商管理」全部需求并提交（`020681e`，34 文件，后端 218 / 前端 172 测试全绿）：顶栏齿轮入口、15%/75% 左右布局、官方（OpenAI/Anthropic/Gemini）+ 自定义服务的增删改、连通性测试、设默认、Agent 运行期热切默认服务、`.env` KIMI 作内置默认种入、密钥脱敏。**本轮（迭代 2）不重复实现这些已完成功能**，而是在对**真实 as-built 源码逐文件审计**（见下「审计依据」）后，针对全局目标「可靠 / 稳健 / 功能正确 / 人机交互最佳实践」做一批**小而精、低回归风险、零新增依赖**的加固。

**审计依据（已逐一对真实源码核验，构成本方案可行性基线）：**

- `agent/providers.py:121-129` `resolve_active_provider`：三级回落「库默认(含 key) → env → None」；**`agent/providers.py:136-167` `probe_provider`**：边界先校验（空 key / 非 http url）再发极小请求，`max_tokens=min(.,16)`、`asyncio.wait_for(timeout=min(cfg.timeout,15))`，异常归一 `CONNECTION_FAILED` 且 `str(exc)[:200]`。
- `agent/manager.py:87-96 / 98-109 / 210-224`：`_resolve_client` / `active_config`（async）/ `handle` 每轮解析并把 client 传入 `sess.run(..., client=)`。`loop.py` 在 BUSY 守卫后才 `self._llm = client`（P1-Race 已落地）。
- `db/repositories.py:253-388`：provider CRUD + `set_default_agent_provider`（ORM 逐行清后置，避免脏读）+ `_reassign_default_after_delete`（`api_key!='' ORDER BY id ASC LIMIT 1`）+ 幂等 seed。
- `api/agent.py:62-153`：providers REST（GET/POST/PUT/DELETE/default/test/test-draft），`_require_http_url` 边界校验，`provider_out` 脱敏。
- `schemas.py:143-228`：`AgentProviderIn/Update`、`ProviderTestReq/Result`、`ProvidersListOut`、`provider_out`；create 端 `label/baseUrl/model` 有 `min_length=1`，**但无 `max_length`**；`AgentProviderUpdate` 各字段可空且**无 min/max 约束**。
- 前端 `components/Settings/*` + `components/ui/index.tsx`（`Input` 约 `60-79`，`invalid` 仅改边框、**未设 `aria-invalid`**；`Field` 约 `82-112`，错误 `span` 未经 `aria-describedby` 关联）+ `AiModelsSection.tsx`（草稿「测试」按钮**未按 `apiKey` 是否为空禁用**，与 `ProviderCard:141` 存量卡规则不一致）+ `lib/settings.ts`（`validateProviderDraft` / `isDraftValid`）。
- `db/session.py:80-109` seed：官方三项 key 留空 + KIMI（`kind="official"`，`api_key=settings.agent_api_key`，`is_default=True`）。

---

## 1. Overview（概述）

本轮加固围绕一个**核心正确性缺陷**与一组**稳健性 / 安全 / 人机交互**改进展开，目标是让迭代 1 已实现的「设置中心 · AI 模型服务商管理」从「功能可用」升级为「**行为诚实、边界稳固、交互无障碍**」。

**核心正确性缺陷（H1，P1）**：as-built 解析器 `resolve_active_provider` 在「库内存在被标记为默认、但**未填写密钥**的服务行」时，会**静默回落到环境变量（.env / KIMI）**。这与需求「设为默认后，Agent **就使用这个默认的目标服务**」直接冲突——用户把 OpenAI 设为默认（尚未填 key），界面上 OpenAI 卡片显示「默认」徽章，**但 Agent 实际仍在用 KIMI**，且顶部「当前默认服务」横幅会显示「环境变量（.env）· kimi…」，形成**自相矛盾、误导用户**的状态。迭代 1 的 spec §4.1 原本也声明此情形应「`active.configured=false`（提示补 key）」，但落地代码偏离了该意图。本轮把「显式默认」确立为**权威**：含 key 即生效，缺 key 则一律视为「未配置」并引导补 key，**只有在「全无任何默认行」时**才回落 env（纯 env 部署 / 删默认未找到含 key 行的兜底）。

**安全加固（H2，P1）**：连通性探测在异常路径返回 `str(exc)[:200]` 给前端、并可能进入日志。虽然现有 client 以 Authorization 头（而非 URL 查询串）携带密钥、泄露概率低，但为贯彻「密钥绝不外泄」铁律（CLAUDE.md C7），本轮新增**密钥红action（redaction）**：任何对外 / 入日志的探测错误文本，先把其中可能出现的 `api_key` 子串替换为脱敏占位，做到**纵深防御、零泄露**。

**稳健性硬底线（H4，P1，稳健）**：连通性探测把 client 构造（`make_llm_client_from_config`，`providers.py:144`）置于 `try` 之外，违反本函数自述不变量「探测边界任何异常归一、绝不裸抛」（`providers.py:152` 注释）——异常入参（如非法 proxy）会逃逸为 HTTP 500。本轮把构造移入兜底、归一 `CONNECTION_FAILED`；**H4 与 H1/H2 同列为本轮必做底线**（修复成本仅一行入 `try`），不归入可裁剪的 P2。

**稳健性与交互加固（H3、H5–H9，P2）**：边界一次性补齐输入长度上限（避免超长 label/url 撑库）、草稿「测试」按钮按 key 禁用以与存量卡一致、表单错误经 `aria-invalid`/`aria-describedby` 关联、连通性结果用 `aria-live` 朗读、设置面板支持 Esc 关闭与开合焦点管理、密钥输入框增「显示 / 隐藏」切换、列表错误态加「重试」、增删改后给即时内联反馈。以上均为「美观优雅 + 符合人机交互最佳实践」需求的直接兑现。

设计三条硬约束不变：**功能正确**（行为与需求承诺一致）、**稳健可靠**（边界稳、密钥不泄、回退不崩、向后兼容）、**美观优雅**（无障碍、即时反馈、明暗主题一致）。所有改动**复用既有文件与原语、零新增三方依赖、不动架构**，控制回归面。

---

## 2. Technical design（技术设计）

### 2.1 H1 — 解析器诚实化：显式默认权威，缺 key 即「未配置」（P1，正确性）

`agent/providers.py · resolve_active_provider`（121-129）改为：

```python
async def resolve_active_provider(session_factory, settings: Settings) -> LlmProviderConfig | None:
    """显式默认权威：含 key 即生效；缺 key 视为「未配置」(引导补 key)，不静默回退 env。
    仅当「全无任何默认行」时才回落 env（纯 env 部署 / 删默认无含 key 行的兜底）。"""
    async with session_factory() as session:
        row = await repo.get_default_agent_provider(session)
    if row is not None:
        if (row.api_key or "").strip():
            return config_from_row(row, settings)
        return None                      # ← 关键改动：缺 key 的显式默认不再回退 env
    if (settings.agent_api_key or "").strip():
        return provider_config_from_settings(settings)
    return None
```

**行为影响（全部为期望中的修正）：**

- 用户把某服务设为默认却未填 key → `active.configured=false` → 横幅显示「未配置可用服务 / 待配置」，WS `ready`、`/agent/config` 同步；Agent 发消息回 `AGENT_NOT_CONFIGURED`「请在设置中心填写并选择默认服务」。**不再静默改用 env**，消除矛盾 UI。
- KIMI 内置默认在 `.env` 有 key 时照常生效（seed 已把 env key 写入 KIMI 行）；`.env` 无 key 时 KIMI 行 key 空 → 视为未配置 → UI 引导在设置中心补 key（路径单一、可发现）。
- **向后兼容**：纯 env 部署（无任何 provider 行被标默认）仍走 env 回落，零改动可用。
- **可见性配套**：见 §2.7（前端在「被标默认但 `hasKey=false`」的卡片上显式提示「默认服务未填写密钥，填写后生效」），把 H1 的后端语义转化为用户可理解的引导。

> 仅此一处逻辑改动；`config_from_row` / `provider_config_from_settings` / `manager` 调用点签名不变。

### 2.2 H2 — 探测 / 日志密钥红action（P1，安全）

`agent/providers.py` 新增纯函数并在错误路径调用：

```python
def _redact(text: str, secret: str) -> str:
    """把可能出现在错误文本中的密钥子串替换为脱敏占位（纵深防御，C7）。"""
    s = (secret or "").strip()
    if s and len(s) >= 6:
        text = text.replace(s, "····")
    return text
```

**精确单口径（评审 P1-B 修订）**：`probe_provider` 共有三处可携带动态文本的失败返回——① `except Exception → str(exc)`（`providers.py:153`）、② `_consume_probe` 的 `error` 事件 `ev.message`（`162-163`，经 `148` 透传回来）、③ 超时分支（`150`，文本静态、本不含 key）。要做到「所有 `ok is False` 的对外 message 一律脱敏、零散落」，落法固定为：

- 局部失败返回（①、③）改走小助手 `_fail(code, message, cfg.api_key)`，其内统一 `_redact(message, key)[:200]`；
- `_consume_probe` 路径（②）在 `probe_provider` 内末次 redact：`result = await asyncio.wait_for(...); if not result.get("ok"): result["message"] = _redact(result.get("message", ""), cfg.api_key)[:200]; return result`。

如此三处经构造即被同一 `_redact` 收口，不依赖「记得在每个 return 手动调用」。**关于「日志」**：`probe_provider` 现状**并不写日志**，密钥的真实对外暴露面是 API 响应 `ProviderTestResult.message`（流向前端），故 H2 的核心收益与断言对象是该响应文本；若未来在此路径新增任何 `logger` 调用，须同样复用 `_redact`（在注释中记此约定）。早返回的两条静态文案（`138`「未填写密钥」、`141`「APIURL…」）不含动态文本，无需 redact。

### 2.3 H3 — 输入长度边界（P2，稳健）

`schemas.py` 为写入类模型补 `max_length`（SQLite 不强制 VARCHAR 长度，须在边界一次性校验，C5）：

- `AgentProviderIn`：`label: Field(min_length=1, max_length=128)`、`baseUrl: Field(min_length=1, max_length=512)`、`model: Field(min_length=1, max_length=128)`、`apiKey: Field(default="", max_length=512)`、`userAgent: str | None`（`Field(default=None, max_length=128)`）。
- `AgentProviderUpdate`：可空字段在「显式给出」时校验——`label: Field(default=None, min_length=1, max_length=128)`、`baseUrl: Field(default=None, min_length=1, max_length=512)`、`model: Field(default=None, min_length=1, max_length=128)`、`apiKey: Field(default=None, max_length=512)`、`userAgent: Field(default=None, max_length=128)`。修复「PUT 可把 label/model 置空串」的隐患（给出即非空）。
- `ProviderTestReq`：`baseUrl/model` 同 `max_length`；`apiKey: Field(default="", max_length=512)`。

> Pydantic 对 `None` 默认值不触发 `min_length`，故 Update 的「缺省=不改」语义不变，仅「显式给空串」被拒。

### 2.4 H4 — 探测期 client 构造纳入兜底（P2，稳健）

`probe_provider` 现把 `make_llm_client_from_config(probe_cfg)`（providers.py:144）置于 `try` 之外；若 factory 因异常入参（如非法 proxy）抛错，会逃逸为 500。改为把 client 构造移入 `try`（或独立 try）→ 归一 `{"ok": False, "code": "CONNECTION_FAILED", "message": _redact(str(exc), cfg.api_key)[:200]}`，与「探测边界任何异常不裸抛」原则一致。

### 2.5 H5 — 草稿「测试」按钮按 key 禁用（P2，交互一致）

`AiModelsSection.tsx`（新增自定义流程，约 line 174）的「测试」按钮当前**未**按 `draft.apiKey` 是否为空禁用，与存量卡 `ProviderCard.tsx:141`（`disabled={testing || !provider.hasKey}`）规则不一致。改为 `disabled={testing || busy || !draft.apiKey.trim() || !isDraftValid(draft)}`，并在禁用时给 `title="请先填写 API Key"`，避免无谓地向后端发一次必然 `AGENT_NOT_CONFIGURED` 的请求。

> **范围限定（评审 P2-D）**：本规则**仅**作用于「新增自定义」流程（`AiModelsSection` 的 `AddCustom`，无 stored key）。**不要**把同一「空 key 即禁用」套到编辑态草稿测试（`ProviderCard.tsx:125` `runTest("draft")`）——编辑态空 `draft.apiKey` 语义是「保留原 key」，按空 key 禁用会误锁一个已含 key 服务的连通性测试。编辑态测试的口径不在本轮变更范围内，保持原样。

### 2.6 H6 — 表单无障碍：aria-invalid / aria-describedby / aria-live（P2，a11y）

- `components/ui/index.tsx · Input`：增 `aria-invalid={invalid || undefined}`；接受可选 `aria-describedby` 透传。
- `components/ui/index.tsx · Field`：当有 `error` 时，为错误 `span` 生成稳定 `id`（如 `${htmlFor}-err`）。**串联口径固定（评审 P2-F）**：`Field` 的 `children` 是不透明节点，**不**用 `React.cloneElement` 向其注入属性；改由调用方 `ProviderEditForm` 显式把每个字段错误 `id` 作为 `aria-describedby` 透传给对应 `Input`（`Field` 仅负责渲染带该 `id` 的错误 `span`，`Input` 经 §2.6 第一条的透传接收）。单一路径，避免过度聪明的子节点改写。
- `components/Settings/ConnectivityResult.tsx`：根容器加 `role="status" aria-live="polite"`，使「测试中… / ✓ 连通 / ✗ 错误」对读屏用户即时朗读。

### 2.7 H7 — 设置面板交互：Esc 关闭 + 焦点管理 + 默认缺 key 提示（P2，a11y/UX）

- `components/Settings/SettingsPanel.tsx`：
  - 根容器加 `role="region" aria-label="设置"`；标题 `<h2>` 加 `tabIndex={-1}` 与 `ref`，挂载时 `focus()`（开合焦点入面板）。
  - 监听 `keydown`（`Escape`）→ `setShowSettings(false)`；卸载时清理监听。返回工作台时把焦点交还齿轮按钮（齿轮按钮 `id="settings-gear"`，关闭后 `document.getElementById('settings-gear')?.focus()`，或经 store 标志 + `page.tsx` `useEffect` 还焦）。
- `components/Settings/ProviderCard.tsx`：当 `provider.isDefault && !provider.hasKey` 时，在卡内显示醒目提示行「该默认服务未填写密钥，填写后生效」（`text-[var(--short)]`），把 §2.1 的后端语义可视化（与横幅「待配置」呼应）。

### 2.8 H8 — 密钥输入框「显示 / 隐藏」切换（P2，UX）

`components/Settings/ProviderEditForm.tsx` 的 API Key 字段（type=password）增本地 `reveal` 状态：尾部加一个 `ghost/sm` 图标按钮（内联 SVG 眼睛，沿用「无三方图标」约定），切换 `type` 在 `password`/`text` 间；`aria-label` 在「显示密钥 / 隐藏密钥」间切换，`aria-pressed={reveal}`。默认隐藏；切换不改变「留空=保留原 key」语义。

### 2.9 H9 — 列表错误重试 + 增删改即时反馈（P2，UX）

- `components/Settings/AiModelsSection.tsx` 列表错误态（约 35-40）加「重试」按钮 → `useQuery` 的 `refetch()`；按钮 `variant="ghost" size="sm"`。
- 增 / 删 / 改 / 设默认成功后，除既有 `invalidateQueries`，在对应卡 / 区给一条**轻量内联**短暂反馈（如 `已保存 ✓ / 已删除 / 已设为默认`，1.5s 后淡出；用组件本地 state + `setTimeout`，**不引入全局 toast 体系**以控制范围）。失败已有 `error` 行兜底。

### 2.10 关键时序（H1 修正后）

```text
用户在设置中心把 OpenAI（未填 key）设为默认
  → POST /agent/providers/{id}/default（事务清其余 is_default、置 OpenAI）
  → list_providers 返回：OpenAI.isDefault=true, hasKey=false；active=resolve_active_provider()
  → 解析：默认行存在但 key 空 → 返回 None → active.configured=false
  → 横幅显示「未配置可用服务 / 待配置」；OpenAI 卡显示「该默认服务未填写密钥，填写后生效」（H7）
  → 用户回工作台发消息 → manager.handle → _resolve_client()=None → 回 AGENT_NOT_CONFIGURED（不静默用 KIMI）
  → 用户填入 OpenAI key 并保存 → 解析命中含 key 默认 → Agent 下一轮即用 OpenAI（需求闭环）
```

---

## 3. File / module change plan（文件 / 模块变更计划）

> 约定不变：单文件 ≤800 行、单函数 ≤50 行、参数 ≤5、嵌套 ≤4、圈复杂度 ≤10（CLAUDE.md）。仅修改既有文件 + 新增对应测试；**零新增源码模块、零新增三方依赖**。标 **[改]** / **[新]**。

### 3.1 后端（services/quant）

| 文件 | 类型 | 一句话意图 | 关联项 |
|---|---|---|---|
| `app/agent/providers.py` | [改] | `resolve_active_provider` 缺 key 默认不回退 env；新增 `_redact`，`probe_provider` 单点红action错误文本 + client 构造纳入兜底 | H1/H2/H4 |
| `app/schemas.py` | [改] | `AgentProviderIn/Update/ProviderTestReq` 补 `max_length`；Update 给出即非空（min_length） | H3 |
| `app/agent/manager.py` | [—] | 无需改（`active_config`/`_resolve_client` 经 `resolve_active_provider` 自动获益 H1） | — |
| `app/api/agent.py` | [—] | 无需改（schema 校验自动生效） | — |

### 3.2 后端测试（services/quant/tests）

| 文件 | 类型 | 一句话意图 |
|---|---|---|
| `tests/test_agent_provider_resolve.py` | [改] | 将「缺 key 默认 → 回退 env」用例改为「缺 key 默认 → None」；新增「无默认行 + env 有 key → env」「KIMI 默认含 key → KIMI」 |
| `tests/test_agent_provider_probe.py` | [改] | 新增：异常 message 含 key 时返回与日志均不含明文（断言 `_redact`）；factory 抛错归一 `CONNECTION_FAILED` 不裸抛 |
| `tests/test_agent_provider_api.py` | [改] | 新增：超长 label/url/model → 422；PUT 显式空 label/model → 422；设默认到「缺 key 服务」后 `active.configured=false` |
| `tests/test_settings_redact.py` *(可并入 probe 测试)* | [新·可选] | `_redact` 纯函数：含 key→脱敏、短串 / 空串→原样、不误伤无关文本 |

### 3.3 前端（apps/web）

| 文件 | 类型 | 一句话意图 | 关联项 |
|---|---|---|---|
| `components/ui/index.tsx` | [改] | `Input` 增 `aria-invalid` + `aria-describedby` 透传；`Field` 串联错误 `id` 到控件 | H6 |
| `components/Settings/ConnectivityResult.tsx` | [改] | 根容器 `role="status" aria-live="polite"` | H6 |
| `components/Settings/SettingsPanel.tsx` | [改] | `role="region"`+标题受焦；Esc 关闭；关闭还焦齿轮 | H7 |
| `components/Settings/SettingsGearButton.tsx` | [改] | 加 `id="settings-gear"`（供关闭还焦定位） | H7 |
| `components/Settings/ProviderCard.tsx` | [改] | `isDefault && !hasKey` 时显示「默认服务未填写密钥」提示；增删改/设默认成功内联短反馈 | H7/H9 |
| `components/Settings/ProviderEditForm.tsx` | [改] | API Key 字段「显示/隐藏」切换；错误 `id` 关联 `aria-describedby` | H8/H6 |
| `components/Settings/AiModelsSection.tsx` | [改] | 草稿「测试」按 key 禁用；列表错误态加「重试」(refetch)；新增卡成功内联反馈 | H5/H9 |
| `lib/settings.ts` | [—] | `validateProviderDraft`/`isDraftValid` 复用，无需改 | — |
| `lib/store.ts` | [—] | 无需改（不新增持久化字段；还焦用 DOM 定位，避免无谓 migrate） | — |

### 3.4 前端测试（apps/web）

| 文件 | 类型 | 一句话意图 |
|---|---|---|
| `lib/settings.test.ts` | [—] | 既有纯函数测试不受影响 |
| `components/Settings/*.test.tsx` | [新·可选] | 若引入组件测试：草稿「测试」按钮在空 key 时 `disabled`；`isDefault && !hasKey` 渲染提示文案（轻量、不强制，视既有前端测试基建而定） |

---

## 4. Interface design（接口设计）

**REST 端点签名全部不变**（GET/POST/PUT/DELETE `/agent/providers*`、`/default`、`/test`、`/test`(draft)、GET `/agent/config`），仅以下**行为 / 校验**收紧（向后兼容，不破坏既有契约）：

| 端点 | 变化 | 说明 |
|---|---|---|
| `POST /agent/providers` | 校验收紧 | `label/baseUrl/model/apiKey/userAgent` 超 `max_length` → `422`（FastAPI 校验体）；既有合法请求不受影响 |
| `PUT /agent/providers/{id}` | 校验收紧 | 同上；且**显式给出**的 `label/baseUrl/model` 为空串 → `422`（缺省=不改，语义不变）|
| `POST /agent/providers/{id}/test`、`/test`(draft) | 错误文本红action | `ProviderTestResult.message` 经 `_redact` 脱敏；factory 异常归一 `CONNECTION_FAILED` |
| `GET /agent/config`、`GET /agent/providers`(`active`)、WS `ready` | 语义修正 | 「被标默认但缺 key」时 `configured=false`、`providerId=null`、`label=""`（H1）；字段形状不变 |

错误体沿用 `{error:{code,message}}` / FastAPI `422`。响应 / 请求 TS 形状（`AgentProviderOut/In/Update`、`ProviderTestResult`、`ProvidersListOut`）**字段不变**，前端无需改类型定义。

---

## 5. Data model（数据模型）

**无 schema 变更**：`agent_provider` 表结构、`AgentProvider` 模型、Zustand persist 版本（v9）与 `partialize` 均**保持不变**——本轮不新增列、不新增持久化字段，故**无迁移**。

仅以下**校验 / 解析层**的不变量被强化：

- **解析不变量（H1）**：当且仅当「默认行存在且 `api_key` 非空」时返回库默认；默认行存在但缺 key → `None`；无默认行且 env 有 key → env；其余 → `None`。「全表至多一行 `is_default=true`」由既有 `set_default_agent_provider` 事务保证，本轮不动。
- **输入边界不变量（H3）**：写入字段长度上限——`label ≤128`、`baseUrl ≤512`、`model ≤128`、`apiKey ≤512`、`userAgent ≤128`（与 DB `String(.)` 声明对齐，杜绝 SQLite 不强制长度导致的撑库）。
- **密钥外泄不变量（H2）**：任何**对外响应 / 日志**文本不得包含 `api_key` 明文——既有 `provider_out` 仅回 `hasKey/keyPreview`（不变），本轮补齐**探测错误文本**这一最后缺口。

前端状态：`showSettings`（瞬态）/`settingsSection`（持久，默认 `ai-models`）不变；H7 的「关闭还焦」用 DOM `getElementById('settings-gear').focus()`，**不新增 store 字段**（避免无谓 v10 迁移）。

---

## 6. Testing & acceptance criteria（测试与验收）

### 6.1 单元 / 集成测试（离线，不依赖真实网络；沿用既有 async pytest + 内存 sqlite + 假 client 注入）

- **H1**：`test_agent_provider_resolve.py` —「缺 key 显式默认 → `None`（不回退 env）」「无默认行 + env 有 key → env」「KIMI 默认含 key → 取库」「全空 → None」。
- **H2**：`test_agent_provider_probe.py` — 注入抛 `Exception("auth failed key=sk-XXXXabcd")` 的假 client，断言返回 `message` 与捕获日志**均不含** `sk-XXXXabcd`；`_redact` 对短串 / 空串 / 无关文本不误伤。
- **H4**：注入「构造即抛」的假 factory 路径 → `probe_provider` 返回 `CONNECTION_FAILED`，**不抛 500**。
- **H3**：`test_agent_provider_api.py` — POST/PUT 超长字段 → `422`；PUT 显式空 `label`/`model` → `422`；合法请求仍 `201/200`。
- **回归**：迭代 1 既有后端 218 测试全绿（仅 resolve 的一条用例 `test_db_default_without_key_falls_back_to_env` 语义按 H1 更新，其余不变）。**落地前确认性 grep（评审 P2-E）**：对 `services/quant/tests` 检索 `api_key=""` 与 `set_default` 同现且期望 env 的用例，确保除上述一条外无其它依赖旧行为者（评审时已核实仅此一条，grep 仅防新增漂移）。
- **前端**：既有 `settings.test.ts` / `store.test.ts` 全绿；新增（若引入组件测试）草稿「测试」空 key 禁用、默认缺 key 提示渲染。`pnpm -C apps/web test` + `tsc --noEmit` 通过。

### 6.2 验收标准（Definition of Done，对齐全局目标逐条）

1. **H1 闭环**：把任一**未填 key** 的服务设为默认后，横幅显示「未配置可用服务 / 待配置」、该卡显示「默认服务未填写密钥」提示、Agent 发消息回 `AGENT_NOT_CONFIGURED`（**不再静默改用 .env/KIMI**）；填入有效 key 后 Agent 下一轮即改用该服务——与需求「设默认后 Agent 用该服务」一致。
2. **H2 安全**：构造必然报错的连通性测试（错误信息含密钥），前端结果与后端日志**均不出现密钥明文**。
3. **H3 边界**：超长 / 空串非法输入被 `422` 拒绝，合法输入照常；既有数据与流程不受影响。
4. **H4 稳健**：异常配置下「测试连通性」返回结构化失败而非 500 崩溃。
5. **H5–H9 交互**：草稿无 key 时「测试」禁用并提示；表单错误对读屏可达（`aria-invalid`/`aria-describedby`）、连通性结果可朗读（`aria-live`）；设置面板可 Esc 关闭、开合焦点正确流转；密钥框可显示 / 隐藏；列表错误可「重试」、增删改有即时反馈——符合「美观优雅 + 人机交互最佳实践」。
6. **零回归 / 零新依赖**：后端 pytest（非 network）+ 前端 vitest/tsc 全绿；无新增三方依赖；`git diff` 不含任何 key、不含 `aragon.db` 与构建产物。

---

## 7. Risks & mitigations（风险与缓解）

| # | 风险 | 缓解 |
|---|---|---|
| R-H1-Compat | H1 改变「缺 key 默认回退 env」既有行为，可能影响「首启后再补 `.env` key」工作流 | 该工作流改为「在设置中心编辑 KIMI 行补 key」（单击可达、更可发现）；纯 env 部署（无默认行）仍回退 env，**零改动**；resolve 单测显式覆盖四分支，防回归 |
| R-H1-Test | 迭代 1 的 resolve 测试断言旧行为 | 本方案 §3.2 明列「将该用例语义更新为 None」，属**有意**的契约修正，评审知情放行 |
| R-H2-False | 红action误伤无关文本 / 漏 redact | `_redact` 仅当 key 长度 ≥6 才替换（避免短串误伤）；在 `probe_provider` 顶层**单点收口**所有错误 message，杜绝散落遗漏 |
| R-H3-Reject | 收紧校验可能拒绝既有「边缘合法」请求 | 上限取宽松值（url 512 / label 128 等，远超正常用量）；仅拦截异常超长 / 空串，正常流程无感 |
| R-Focus | Esc 关闭 / 还焦与既有键盘流冲突 | 监听限定 `Escape` 且仅在设置面板挂载期生效，卸载即解绑；还焦用既有齿轮按钮 id，失败（元素不存在）静默降级，不抛错 |
| R-Scope | 加固项膨胀为过度工程（违背「适度性」） | 严格限定为「改既有文件 + 补测试」，**不新增源码模块 / 依赖 / 持久化字段 / 全局 toast 体系**；P2 中交互项可按评审取舍，P1（H1/H2）为必做底线 |
| R-Race-Accept | `set_default` 并发竞态（两请求同时清后置）理论存在 | **接受**：单用户桌面端、手动 UI 串行操作，概率极低；既有 ORM「清后置」已防常见场景；引入分布式锁属过度工程，记为已知可接受项 |
| R-KeyAtRest-Accept | 密钥明文落 SQLite（`userData` / gitignored `aragon.db`）| **接受并记录威胁模型**：单用户本地库、不入版本库；OS keychain 加密需平台原生依赖、属更大改造，本轮不纳入（未来增强可评估 `cryptography` Fernet + userData 派生密钥）|

---

## 8. 实现注意事项（给下游工程师）

- **跨平台**：建目录 `python -c "from pathlib import Path; Path('...').mkdir(parents=True, exist_ok=True)"`；勿用 `mkdir -p` / `&&` / 反斜杠续行（见任务环境约定）。
- **改动顺序建议（测试先行，评审 P2-C，遵 CLAUDE.md §七）**：H1 属正确性 bug，**先**写复现测试——「缺 key 显式默认 → 期望 `None`」（对当前代码应**先红**）→ 再改 `resolve_active_provider`（转绿）+ 同步把旧用例 `test_db_default_without_key_falls_back_to_env` 语义更新为 None → 跑后端测试确认绿 → 再 H2（`_redact` 单口径）/H4（构造入 `try`）→ H3（schemas `max_length`）→ 前端 H5、H6–H9。每步小提交粒度便于回退。
- **密钥红线（C7）**：`_redact` 落地后，复核 `probe_provider` 所有 `return` 分支 message 均经收口；下游 Subtask #3 提交务必**精确 `git add <具体路径>`、严禁 `git add -A`**，复核 diff 不含任何 key 与 `aragon.db`。
- **不破坏既有契约**：REST 路径 / 响应字段形状不变，仅收紧校验与修正解析语义；`make_llm_client(settings)` 等既有签名不动；前端 TS 类型无需改。
- **范围纪律（评审 P1-A 调整）**：H1/H2/**H4** 为本轮**必做**正确性 / 安全 / 稳健底线（H4 修补一条真实 500 逃逸面，不可裁剪）；H3、H5–H9 为交互 / 无障碍加固，评审可据工期取舍，但**不得**新增依赖或改架构。

（全文 > 2000 字，覆盖 Overview / 技术设计 / 文件清单 / 接口 / 数据模型 / 测试验收 / 风险，满足 Definition of Done；所有改动点均以真实源码 `file:line` 锚定，可被下游逐行落地。）

---

## 评审结论（Review Verdict）

**有条件通过（Approved with Conditions）。**

方案设计扎实、锚点真实（评审已对全部引用 `file:line` 回源复核）、范围克制（零新依赖 / 零架构改动 / 零迁移），正确性核心缺陷 H1 定位准确且修复闭环，整体与全局目标「可靠 / 完善 / 稳健 / 功能正确 + 人机交互最佳实践」高度对齐。本次评审已将全部 **P0（无）/ P1（P1-A、P1-B）** 在 v2 文档**就地修复**，**无遗留 P0/P1**；另就地收敛 4 项 P2（P2-C/D/E/F）以降低下游误实现概率。

下游 Subtask #2「代码开发」须满足以下条件方为达标（均为 P2 级落地纪律，不阻断进入开发）：

1. **必做底线不可裁剪**：H1（解析诚实化）、H2（密钥红action单口径）、H4（探测构造纳入兜底）三项为本轮非协商底线；P2 交互项（H3、H5–H9）可按工期取舍，但**不得**新增三方依赖 / 改架构 / 加全局 toast 体系 / 动持久化版本。
2. **H1 测试先行**：先提交「缺 key 默认 → `None`」复现测试（对现码应先红），再改 `resolve_active_provider`；同步更新 `test_db_default_without_key_falls_back_to_env`，并对 `tests/` 复核无其它依赖旧行为的用例。
3. **密钥红action单口径**：按 v2 §2.2 让 `probe_provider` 所有 `ok is False` 返回的 message 统一过 `_redact`，断言 API 响应不含密钥明文；该路径若新增日志须复用 `_redact`。
4. **H5 范围**：草稿测试按 key 禁用仅限「新增自定义」流程，勿误及编辑态「留空=保留原 key」。
5. **验收回归**：后端 pytest（非 network）+ 前端 vitest / `tsc --noEmit` 全绿；提交用 `git add <具体路径>`（严禁 `-A`），diff 不含任何 key / `aragon.db` / 构建产物。

满足以上条件即视为本迭代加固达成。评审签发：Senior Reviewer（Anthropic Eng）。
