# 设置中心 · 实盘欧易（OKX）账户凭证管理 — 设计方案 (spec)

> 文档版本：**v2**（方案评审与修复 / Subtask #1 产出 —— 基于 v1 就地评审并修复全部 P0/P1）
> Feature slug：`okx-live-account-credentials`
> 作者：Solution Architect（Anthropic Eng）｜评审：Senior Reviewer（Anthropic Eng）
> 关联全局目标：在「设置」中支持用户管理真实实盘 OKX 账户凭证（API Key / Secret Key / Passphrase / IP 白名单 / 备注名等），支持多账户、设默认、逐账户测试连通性、查看账户详情（资产 / 持仓）。
> 关联既有方案：`docs/plans/agent-model-provider-settings/spec.md`（v2）、`docs/plans/agent-tools-management/spec.md`、`docs/plans/aragon-trading-platform/spec.md`（v2）。本方案在「设置中心」外壳与「AI 模型服务商」既有链路上**同构扩展**第三个设置项。

---

## 评审记录（Review Notes / Subtask #1）

> 评审维度：**可行性 Feasibility**（能否用现有技术栈构建）｜**完备性 Completeness**（边界 / 错误路径 / 上线遗漏）｜**一致性 Consistency**（是否与 `CLAUDE.md` 及既有代码约定冲突）｜**适度性 Right-sizing**（范围是否合理）。评审方式：逐节对照 `services/quant`（后端）与 `apps/web`（前端）真实源码核验。

**已对照真实源码核验的事实（构成可行性基线，除特别标注外均通过）：**

- 后端 `db/repositories.py` 既有 AI provider 全套**函数式** CRUD（首参 `session`）：`list/get/get_by_slug/create/update/delete/get_default/set_default/_reassign_default_after_delete`；`set_default` 用 ORM **逐行清零再置位**（规避 `expire_on_commit=False` 脏值）——本方案 §8 `OkxAccount*` 仓储与之逐一同构，可直接照搬。✓
- 后端 `db/models.py · AgentProvider`（表 `agent_providers`）列型 / `is_default` / `_utcnow`（`onupdate`）与本方案 §5 `OkxAccount` 模型同构；`db/session.py · init_db` 走 `Base.metadata.create_all`（**无 Alembic**）→ 新表 `okx_accounts` 自动建出、无需迁移、无需 seed。✓
- 后端 `schemas.py · provider_out` + `_key_preview`（`····{key[-4:]}`，camelCase，`createdTs/updatedTs` 为 `int(row.created_at.timestamp())` **秒级**）即本方案 `account_out`/`apiKeyPreview` 的复用源。✓
- 后端 `okx/rest_client.py` 已用 `settings.okx_rest_base`（`https://www.okx.com`）+ `settings.proxy_url`（`https_proxy || http_proxy || all_proxy`）构造 httpx——本方案私有客户端复用同两字段，**字段名经核验确实存在、无臆造**（规避前作 P2-Settings 类「字段名错配」隐患）。✓
- 后端 `config.py` 既有 `okx_api_key/okx_secret_key/okx_passphrase` 占位空值、`proxy_url` 计算属性、`okx_rest_base`——本方案新增 `okx_account_demo/okx_account_timeout_seconds` 与之并列，pydantic-settings 字段名即环境变量名。✓
- 后端 `main.py` 以 `include_router(prefix="/api")` 挂载、`okx_error_handler` 对 `OKXError` 统一回 **502 `{error:{code,message}}`**（C1）——但**仅捕获 `OKXError`**，新引入的 `OKXPrivateError` 不在其内（见 P1-ErrHandler）。✓ / ⚠
- 前端 `SettingsPanel`（`w-[15%]`/`w-[75%]` + `section==="agent-tools" ? … : …` 三元路由）、`SettingsNav.SECTIONS`（**现 2 项**，结构可扩展）、`AiModelsSection`（默认横幅 + 卡组 + 添加 + `Skeleton`/错误态 + RQ key `["agent","providers"]`）、`ProviderCard`/`ProviderEditForm`（展示 / 内联编辑、密码显隐、`aria-describedby`）、`ConnectivityResult`（`CODE_HINT` + `role="status" aria-live="polite"`）、`lib/agentApi`（`/api/py/*`）、`lib/agentTypes`（camelCase 脱敏形）、`lib/settings`（`maskKey/validateProviderDraft/isDraftValid/emptyDraft/draftFromProvider`）、`next.config.mjs`（`/api/py/:path*` → `${PY_BACKEND_URL}/api/:path*`）、`store.ts`（`settingsSection` **自由串**、persist **v9**）、`components/ui`（`Button/Field/Input/Badge/Skeleton/Card/Select/Switch/Tabs`）、`lib/format.ts`（`fmtNum/fmtPrice/fmtMoney/fmtTime`）、`settings.test.ts`（vitest）——本方案各同构落点全部成立。✓
- ⚠ **前端无任何可复用 Modal / Dialog 原语**（全仓 grep `role="dialog"`/`aria-modal` 零命中），前作刻意走内联编辑、未引入浮层基础设施（见 P1-Modal）。

**问题清单与处置：**

| # | 维度 | 严重度 | 问题 | 处置 |
|---|---|---|---|---|
| P1-Modal | 完备性 / 一致性 / 适度性 | **P1** | §3 列 `OkxAccountDetailModal.tsx`、§6 要求 `role="dialog" aria-modal` + Esc / 遮罩关闭 / 焦点入标题 / 焦点回退，但**全仓无可复用 Modal 原语**，§3 文件计划与「复用 `components/ui` 原语」清单**未含**新建浮层原语及其焦点陷阱 / 滚动锁定 / portal / 焦点恢复基础设施。下游照计划实现会发现无 Modal 可 import，被迫即兴造一次性不可复用浮层，违背 a11y 契约且制造新孤岛。 | ✅ 已修复：§3 文件计划**新增**在 `components/ui/index.tsx` 扩展一个**可复用 `Modal` 原语**并明确 a11y / 行为契约（§6 重写）；`OkxAccountDetailModal` 仅消费该原语；新增 `components/ui/Modal.test.tsx` 覆盖 Esc / 遮罩 / 焦点。**备选（同样合规）**：若团队判定浮层基础设施偏重，可改「卡内可展开详情区」（同既有 `ProviderCard` 内联编辑、零新增浮层）——二者择一，文档默认前者。 |
| P1-ErrHandler | 完备性 / 稳健性 / 一致性 | **P1** | §9 detail 路由「转 502 `{error}`（沿用 `okx_error_handler` 风格，**或**路由内显式 `JSONResponse(502)`）」——但既有全局 `okx_error_handler` **只捕获 `OKXError`**，新引入的 `OKXPrivateError` **不被其捕获**。若 detail 路由让其直接逸出且未显式转换，FastAPI 回**裸 500、无 `{error:{code,message}}` 体**，破坏错误体契约与 C1（显式错误、不假数据）。「或」留二义、存在 500 漏洞。 | ✅ 已修复：§2.2 / §2.3 / §9 明确 `class OKXPrivateError(OKXError)`（**子类化**复用 `code/message` 构造与既有全局 `okx_error_handler` → 502，零新增 handler）；detail 路由**先经 `_map_okx_code` 归一语义码**再 `raise`，使 502 体携带前端可识别语义码；test 路由仍**捕获**该异常并归一 `{ok:false,...}`（诊断动作不抛）。 |
| P2-Naming | 一致性 | P2 | 脱敏预览字段本方案用 `apiKeyPreview`，既有 `provider_out` 用 `keyPreview`。 | 维持 `apiKeyPreview`（多密钥实体下语义更清晰），但**掩码实现须复用既有 `_key_preview`**、前端复用 `maskKey`，勿另写。已在 §4 标注。 |
| P2-ErrEnvelope | 完备性 | P2 | 新建 `api/accounts.py` 的 `HTTPException(detail={code,message})` 须被**全局** handler 包成 `{error:{...}}`（同既有 provider 路由），勿在模块内另造局部封套。 | 实现期复用既有全局 HTTPException → `{error}` 处理器；`ACCOUNT_NOT_FOUND`(404) / 边界校验(422/400) 均经同一封套。已在 §9 标注。 |
| P2-TestResultType | 一致性 | P2 | §6「复用 `ConnectivityResult`」，但其 result prop 现强类型 `ProviderTestResult`；OKX 结果为 `OkxAccountTestResult`（同形）。 | 实现期将 result prop 放宽为共享最小形（或抽 `TestResult`），并在其 `CODE_HINT` 补 OKX 语义码中文（R-Reuse 推荐路径）。已在 §6 标注。 |
| P2-Time | 一致性 | P2 | `createdTs/updatedTs` 须与既有 `provider_out` 一致输出**秒级 UTC 整数**，而非毫秒 / ISO。 | `account_out` 照 `provider_out` 落 `int(row.created_at.timestamp())`。已在 §4 标注。 |
| P2-IPEcho | 完备性 / UX | P2 | 开发用真实凭证白名单绑定 `203.0.113.10`，但本机经 Clash 代理（`127.0.0.1:7897`）真实出口 IP 多半 ≠ 该值 → 测试极可能回 `OKX_IP_DENIED`(50110) 而非验收期望的 ✓。 | (a) §10 验收明确：示例凭证回 ✓ 的前提是**补齐 Passphrase 且当前出口 IP ∈ 该凭证白名单**（否则 50110 属环境而非代码缺陷）；(b) R-IP「回显服务端出口 IP」由可选**升为推荐**。已在 §6 / §10 / §11 标注。 |
| P2-Size | 一致性（CLAUDE.md） | P2 | `OkxAccountDetailModal`（信息 + 资产表 + 持仓表）+ 新 Modal 原语，易触 800 行 / 50 行 / 嵌套 ≤4 阈值。 | 实现期抽 `BalanceTable`/`PositionsTable` 子组件守阈值。已在 §6 标注。 |
| P2-GitHygiene | 安全 | P2 | 任务 prompt 内含**真实可用**凭证，下游 Subtask #2/#3 须严防入库。 | §12 补：下游提交**精确 `git add <具体路径>`、严禁 `git add -A`**，复核 diff 不含任何 key/secret/passphrase 与 `aragon.db`；真实凭证仅手动测试用、绝不写 seed / 提交。已在 §12 标注。 |

> **结论摘要**：本方案**无 P0 阻断项**；**2 个 P1（P1-Modal / P1-ErrHandler）已在本 v2 正文就地修复**，无遗留 P0/P1。OKX V5 强制 Passphrase（示例凭证缺失）已由原作者在 §1 / R-Passphrase 正确识别并处置，评审复核成立。P2 为实现期收敛项，列入文末放行条件。

---

## 1. Overview（概述）

本方案为 Aragon Trading 工作台的「设置中心」新增第三个功能项 **「实盘账户」（OKX Live Account Credentials）**。入口与既有两项（「AI 模型服务」「Agent 工具管理」）并列，挂在 `SettingsPanel` 左侧 15% 导航列表中；点击后右侧 75% 内容区渲染本面板。该面板让用户**新增 / 编辑 / 删除多个真实欧易（OKX）账户凭证**，每条凭证包含：备注名（label）、API Key、Secret Key、Passphrase、IP 白名单（informational）、权限备注（informational）。用户可把任意一条凭证**设为默认正在使用的账户**（全表至多一条 `is_default=True`，事务保证唯一性），并对每条凭证**一键测试连通性**（后端用 OKX V5 私有接口签名规则真实发一次极小的鉴权请求，校验 Key / Secret / Passphrase / IP 白名单整体可用并报告往返时延与失败原因）。连通性 OK 后，用户可对该账户**查看详情**，详情中展示账户基础信息（UID、账户层级、权限）、资产金额（总权益 + 分币种余额）与当前持仓情况（逐仓位 instId / 方向 / 数量 / 均价 / 未实现盈亏等）。

该能力构建在既有 OKX 适配与设置中心链路之上，而非另起炉灶。现状是后端仅用 **OKX 公共行情**（`okx/rest_client.py` 公共 REST + `okx/ws_client.py` 公共 WS），`config.py` 中 `okx_api_key/okx_secret_key/okx_passphrase` 三个字段为**占位空值**、本期不参与签名。本方案把「实盘账户凭证」从「环境变量占位」升级为「**用户可管理、持久化、多账户、运行期可切换默认**」：新增一张 SQLite 表 `okx_accounts` 承载多账户凭证（Secret Key / Passphrase 仅存后端，**绝不下发前端、不进日志**，API Key 列表态仅回末 4 位掩码），新增一个**OKX V5 私有签名 REST 客户端**与一个**账户连通性探测 / 详情聚合服务**，新增一组 REST 路由经既有 `/api/py/*` 代理读写。前端复用既有设计 token 与 `components/ui` 原语（`Button/Field/Input/Badge/Skeleton/Card`），新增类型层 / API 客户端 / 纯函数层 / 四个组件，与既有「AI 模型服务商」面板**逐一同构**，降低实现与评审成本。

设计目标可归纳为四条硬约束：(1) **功能正确**——增删改、设默认、测试连通性、查看详情（资产 / 持仓）端到端闭环，多账户互不串号；(2) **稳健可靠**——Secret / Passphrase 不泄露、默认账户唯一性事务保证、OKX 不可达 / 鉴权失败显式报错且**绝不假数据**（延续 C1 真实数据硬约束）、密钥不进日志；(3) **安全合规**——本期**只读**鉴权访问（仅 GET 账户配置 / 余额 / 持仓），**不下任何真实订单**，严格守住「Phase 1 不接入真实下单」的产品不变量；(4) **美观优雅**——15/75 版式、卡片化账户、连通性结果即时反馈、详情以独立 Modal 呈现，与既有亮 / 暗主题完全一致，符合人机交互最佳实践。

> ⚠️ 重要事实（影响表单字段，见 §11 风险 R-Passphrase）：OKX V5 私有接口**强制要求 Passphrase**（`OK-ACCESS-PASSPHRASE` 头），缺失则鉴权必败（错误码 50105）。任务示例凭证中只给出 apikey / secretkey / IP / 备注名 / 权限，**未含 Passphrase**——本方案据 OKX 真实协议**必须**在表单中新增 Passphrase 字段并标记为创建时必填，示例凭证缺失项需由用户补齐其在 OKX 创建该 API Key 时设置的 Passphrase。

---

## 2. Technical design（技术设计）

### 2.1 总体架构

```text
┌──────────────────────── apps/web (Next.js 14) ─────────────────────────┐
│ components/Settings/SettingsPanel.tsx                                   │
│   左 SettingsNav(15%)：AI 模型服务 / Agent 工具管理 / 实盘账户            │
│   右 内容区(75%)：section==="okx-accounts" ? <OkxAccountsSection/> : …   │
│     OkxAccountsSection ──(React Query)──▶ accountApi.accounts.*         │
│       └ OkxAccountCard（卡片：掩码展示 / 测试 / 设默认 / 编辑 / 删除 / 查看详情）
│           ├ OkxAccountEditForm（label/key/secret/passphrase/ip/权限）   │
│           └ OkxAccountDetailModal（信息 + 资产表 + 持仓表）             │
└────────────────────────────────┬───────────────────────────────────────┘
        REST  /api/py/accounts*   (next.config.mjs rewrite → 后端 /api/accounts*)
                                  ▼
┌──────────────────────── services/quant (FastAPI) ──────────────────────┐
│ api/accounts.py（CRUD + default + test + test-draft + detail）          │
│   ├ schemas.py：OkxAccountIn/Update/TestReq/TestResult/DetailOut +      │
│   │              account_out（脱敏序列化）+ OkxAccountsListOut          │
│   ├ db/repositories.py：OkxAccountValues/Patch + CRUD（同 provider 同构）│
│   ├ db/models.py：OkxAccount（okx_accounts 表）                          │
│   └ okx/accounts.py（服务层）：probe_account() / fetch_account_detail()  │
│        └ okx/private_client.py：OKXPrivateClient（V5 HMAC 签名 REST）    │
│             GET /api/v5/account/config | /balance | /positions          │
└─────────────────────────────────────────────────────────────────────────┘
```

数据流与既有 AI 模型服务商**完全同构**：前端纯展示与交互、不持有 Secret / Passphrase 明文；后端持久化凭证、运行期按需用某条凭证构造签名客户端发起鉴权请求；OKX 不可达或鉴权失败一律显式错误态，绝不静默兜底。

### 2.2 OKX V5 私有签名（核心，须 100% 精确实现）

新增 `services/quant/app/okx/private_client.py`。OKX V5 私有接口鉴权头与签名算法（实测协议）：

- 请求头：
  - `OK-ACCESS-KEY`：API Key
  - `OK-ACCESS-SIGN`：签名（见下）
  - `OK-ACCESS-TIMESTAMP`：ISO8601 毫秒 UTC，**带尾 `Z`**，形如 `2026-06-22T08:15:42.123Z`
  - `OK-ACCESS-PASSPHRASE`：创建 API Key 时设置的 Passphrase（**明文**，非签名）
  - `Content-Type: application/json`
  - （可选）`x-simulated-trading: 1` —— 仅 demo 模拟盘需要；本期实盘**不发**该头（见 §12 配置开关 `okx_account_demo`，默认 `False`）
- 签名串 `prehash = timestamp + method + requestPath + body`：
  - `timestamp`：与 `OK-ACCESS-TIMESTAMP` 头**完全一致**的同一个值
  - `method`：大写，如 `GET`
  - `requestPath`：**含 query string**，如 `/api/v5/account/balance` 或 `/api/v5/account/positions?instType=SWAP`
  - `body`：GET 为空串 `""`；本期全为 GET
  - `sign = base64( HMAC_SHA256(secret_key_utf8, prehash_utf8) )`

参考实现（实现期照此落，签名/时间戳须有确定可复现单测，见 §10）：

```python
import base64, hashlib, hmac
from datetime import datetime, timezone

def _iso_timestamp() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"

def _sign(secret: str, ts: str, method: str, request_path: str, body: str = "") -> str:
    prehash = f"{ts}{method.upper()}{request_path}{body}"
    mac = hmac.new(secret.encode("utf-8"), prehash.encode("utf-8"), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode("utf-8")
```

`OKXPrivateClient` 以**单条凭证**构造（`api_key/secret_key/passphrase` + `Settings`），复用 `settings.okx_rest_base` 与 `settings.proxy_url`（OKX 地区受限，须透传 Clash 代理，延续既有约定）；每次请求新建/复用 `httpx.AsyncClient`，`timeout=httpx.Timeout(15.0, connect=8.0)`，对 `httpx.TransportError` 做**一次**重试。响应体 `{"code","data","msg"}`：`code != "0"` 即抛 `OKXPrivateError(code, msg)`（携带 OKX 原始错误码，供上层映射）；连通失败抛 `OKXPrivateError("OKX_UNREACHABLE", ...)`。客户端**实例不缓存到进程单例**（不同账户密钥不同），由调用方按需构造并 `aclose`。

> **错误类必须复用既有契约（P1-ErrHandler）**：`class OKXPrivateError(OKXError)` —— **子类化**既有 `okx/rest_client.py · OKXError`，复用其 `code/message` 构造，从而被 `main.py` 既有全局 `okx_error_handler` 自动捕获并统一回 **502 `{error:{code,message}}`**（C1），**无需新增 handler、无裸 500 漏洞**。`OKXError` 现仅由公共客户端抛出、`okx_error_handler` 对其族系一律 502，子类化不破坏既有语义。

### 2.3 连通性探测与详情聚合（服务层）

新增 `services/quant/app/okx/accounts.py`，与 `agent/providers.py · probe_provider` 同构：

- `probe_account(creds) -> dict`：先**边界一次性校验**（任一字段空 → `{"ok":False,"code":"ACCOUNT_NOT_CONFIGURED"}`，避免无谓往返），再用 `OKXPrivateClient` 发一次极小鉴权请求 `GET /api/v5/account/config`（轻量、即可验证 Key+Secret+Passphrase+IP 四者整体可用），成功返回 `{"ok":True, "latencyMs":…}`，失败经 `_map_okx_code` 归一为语义码并**脱敏**后返回。任何异常归一为 `CONNECTION_FAILED`，**绝不裸抛、绝不记 key**。
- `fetch_account_detail(creds) -> dict`：连续（可 `asyncio.gather` 并发）调三个私有 GET 聚合详情：
  - `/api/v5/account/config` → `info`（UID / 账户层级 / 权限）
  - `/api/v5/account/balance` → `balance`（`totalEq` USD + 分币种 `details[]`）
  - `/api/v5/account/positions` → `positions`（逐持仓）
  任一失败：detail 路由**先经 `_map_okx_code` 把 OKX 原码归一为语义码**，再 `raise OKXPrivateError(语义码, _redact(msg))`；因 `OKXPrivateError` 子类化 `OKXError`（见 §2.2），由既有全局 `okx_error_handler` 统一回 **502 `{error:{code,message}}`**（C1，不假数据），502 体携带前端可识别的语义码（与 test 路由口径一致）。
- `_map_okx_code(okx_code) -> 语义码`（稳定对外错误码，前端据此给友好中文提示）：

  | OKX code | 语义码 | 含义 |
  |---|---|---|
  | 50111 | `OKX_AUTH_KEY` | API Key 无效 |
  | 50113 | `OKX_AUTH_SIGN` | 签名不匹配（Secret Key 错误） |
  | 50105 | `OKX_AUTH_PASSPHRASE` | Passphrase 错误 |
  | 50110 | `OKX_IP_DENIED` | 当前出口 IP 未加入白名单 |
  | 50102 / 50104 | `OKX_TIME_SKEW` | 本机时间与 OKX 偏差过大 |
  | 50030 / 权限类 | `OKX_PERMISSION` | API Key 权限不足（未开读取） |
  | 其它非 0 | `OKX_ERROR` | OKX 业务错误（透传原码于 message） |
  | —（传输/超时） | `CONNECTION_FAILED` | 连接失败 / 超时 |

- `_redact(text, *secrets)`：把 Secret / Passphrase 子串（长度 ≥6 时）替换为 `····`，错误 message 一律先脱敏再截断（≤200），**单点收口**（延续 providers C7）。

### 2.4 既有「不接入真实下单」不变量的守护

本方案**只新增只读鉴权 GET**（account config / balance / positions），**不新增任何下单 / 撤单 / 划转写接口**，因此完全兼容 `.claude-index` 关键约定「Keep real trading disabled until an intentional Phase 2」。详情面板与连通性测试均为读路径；`OKXPrivateClient` 不暴露任何 POST 交易方法。该边界在评审与实现期均须坚守（见 §11 R-NoOrder）。

---

## 3. File / module change plan（文件 / 模块变更计划）

> 约定：后端 Python 走 `services/quant/`，前端 TS 走 `apps/web/`。所有改动**新增为主、最小侵入既有文件**。

| 文件 | 动作 | 一句话意图 |
|---|---|---|
| `services/quant/app/db/models.py` | 修改 | 新增 `OkxAccount` ORM 模型（表 `okx_accounts`），承载多账户凭证（secret/passphrase 仅后端存）。 |
| `services/quant/app/db/repositories.py` | 修改 | 新增 `OkxAccountValues`/`OkxAccountPatch` 数据类 + `list/get/create/update/delete/set_default/_reassign_default/count` 一组异步 CRUD（与 provider 同构）。 |
| `services/quant/app/okx/private_client.py` | 新建 | OKX V5 私有签名 REST 客户端：`_iso_timestamp`/`_sign`、`OKXPrivateClient`（config/balance/positions GET）、`OKXPrivateError`（**子类化既有 `OKXError`**，复用全局 502 handler，P1-ErrHandler）。 |
| `services/quant/app/okx/accounts.py` | 新建 | 账户服务层：`AccountCredentials` 数据类、`probe_account`、`fetch_account_detail`、`_map_okx_code`、`_redact`。 |
| `services/quant/app/schemas.py` | 修改 | 新增 `OkxAccountIn/OkxAccountUpdate/OkxAccountTestReq/OkxAccountTestResult/OkxAccountDetailOut/OkxAccountsListOut` 与 `account_out()` 脱敏序列化。 |
| `services/quant/app/api/accounts.py` | 新建 | REST 路由：列表/新增/编辑/删除/设默认/测存量/测草稿/查详情（错误体 `{error:{code,message}}` 同构）。 |
| `services/quant/app/main.py` | 修改 | `from .api import accounts` 并 `app.include_router(accounts.router, prefix="/api")`。 |
| `services/quant/app/config.py` | 修改 | 新增 `okx_account_demo: bool = False`（是否发 `x-simulated-trading: 1`，默认实盘不发）与 `okx_account_timeout_seconds: float = 15.0`（私有请求超时）。 |
| `apps/web/lib/accountTypes.ts` | 新建 | 前端类型：`OkxAccount`（脱敏形）/`OkxAccountDraft`/`OkxAccountTestResult`/`OkxAccountDetail`/`OkxAccountsListResponse`。 |
| `apps/web/lib/accountApi.ts` | 新建 | REST 客户端（经 `/api/py/*`），`accounts.{list,create,update,remove,setDefault,test,testDraft,detail}`；绝不携带/返回明文 secret。 |
| `apps/web/lib/accountSettings.ts` | 新建 | 纯函数：`maskKey`/`validateAccountDraft`/`isDraftValid`/`emptyDraft`/`draftFromAccount`/账户层级与权限文案映射。 |
| `apps/web/components/Settings/OkxAccountsSection.tsx` | 新建 | 右侧主面板：当前默认横幅 + 账户卡列表 + 「添加账户」（同 `AiModelsSection`）。 |
| `apps/web/components/Settings/OkxAccountCard.tsx` | 新建 | 单账户卡：展示态（掩码/默认徽标/测试/设默认/编辑/删除/查看详情）+ 内联编辑态。 |
| `apps/web/components/Settings/OkxAccountEditForm.tsx` | 新建 | 账户编辑表单：label/apiKey/secretKey(密码)/passphrase(密码)/ipWhitelist/permissions，密钥显隐切换。 |
| `apps/web/components/ui/index.tsx` | 修改 | **（P1-Modal）新增可复用 `Modal` 原语**（`role="dialog"`+`aria-modal`+`aria-labelledby`、Esc / 点遮罩关闭、焦点陷阱、卸载焦点恢复、`body` 滚动锁定、React portal）；详情 Modal 及未来浮层均复用，避免造孤岛。详见 §6。 |
| `apps/web/components/Settings/OkxAccountDetailModal.tsx` | 新建 | 详情面板：**消费 `ui` 的 `Modal` 原语**，渲染基础信息 + 资产表（总权益/分币种）+ 持仓表（懒查 `detail` 接口）；资产 / 持仓抽 `BalanceTable`/`PositionsTable` 子组件守 800/50 行阈值（P2-Size）。 |
| `apps/web/components/Settings/SettingsNav.tsx` | 修改 | `SECTIONS` 追加 `{ key:"okx-accounts", label:"实盘账户", desc:"欧易 API 凭证与连通性" }`。 |
| `apps/web/components/Settings/SettingsPanel.tsx` | 修改 | 内容区路由扩展：`section==="okx-accounts" ? <OkxAccountsSection/> : section==="agent-tools" ? <AgentToolsSection/> : <AiModelsSection/>`。 |
| `services/quant/tests/test_okx_signing.py` | 新建 | 单测：固定 timestamp/method/path/body → 确定 base64 签名；头组装；时间戳格式。 |
| `services/quant/tests/test_accounts_api.py` | 新建 | 单测：CRUD、脱敏（响应永不含 secret/passphrase）、设默认唯一性、删默认回落、test 路由对 OKX 错误码映射（私有客户端打桩）。 |
| `apps/web/lib/accountSettings.test.ts` | 新建 | 单测：`maskKey`/`validateAccountDraft`/`isDraftValid`/`emptyDraft`/`draftFromAccount`。 |
| `apps/web/components/ui/Modal.test.tsx` | 新建 | **（P1-Modal）**新 `Modal` 原语单测：Esc / 点遮罩触发 `onClose`、挂载焦点入对话框、`role="dialog"`+`aria-modal` 存在。 |

> **不改动 `db/session.py`**：`init_db()` 走 `Base.metadata.create_all`，新表自动建出，**无需 seed**——严禁把任务给出的真实开发凭证写入 seed / 提交入库（见 R-Secret）。
> **store 无需改动**：`settingsSection` 已是自由字符串、`"okx-accounts"` 即可路由；不新增持久化字段，故**不升 persist version**。

---

## 4. Interface design（接口设计 · REST）

> 后端实际路径 `/api/accounts*`；前端经 `next.config.mjs` 把 `/api/py/:path*` 重写至 `${PY_BACKEND_URL}/api/:path*`，故前端调 `/api/py/accounts*`。请求/响应体 camelCase（与既有 schemas 对齐）。错误体统一 `{ "error": { "code", "message" } }`。

| Method | Path | 请求体 | 响应 | 说明 |
|---|---|---|---|---|
| GET | `/api/accounts` | — | `OkxAccountsListOut` | 全量账户（脱敏）+ `defaultId`。 |
| POST | `/api/accounts` | `OkxAccountIn` | `OkxAccount`（201） | 新增账户；label/apiKey/secretKey/passphrase 必填。 |
| PUT | `/api/accounts/{id}` | `OkxAccountUpdate` | `OkxAccount` | 编辑；secretKey/passphrase 留空=保留原值；不存在→404 `ACCOUNT_NOT_FOUND`。 |
| DELETE | `/api/accounts/{id}` | — | 204 | 删除；删默认后自动回落（id 最小者）；不存在→404。 |
| POST | `/api/accounts/{id}/default` | — | `OkxAccountsListOut` | 设为默认（事务清其余 `is_default`）；返回最新列表。 |
| POST | `/api/accounts/{id}/test` | — | `OkxAccountTestResult` | 测存量账户连通性（用库内 secret/passphrase）。 |
| POST | `/api/accounts/test` | `OkxAccountTestReq` | `OkxAccountTestResult` | 测草稿连通性（首存前，须带显式 apiKey/secretKey/passphrase）。 |
| GET | `/api/accounts/{id}/detail` | — | `OkxAccountDetailOut` | 查详情：信息+资产+持仓（真实 OKX，鉴权失败→502 显式错误，绝不假数据）。 |

请求/响应 TS 与 Pydantic 形状（camelCase）：

```ts
// apps/web/lib/accountTypes.ts
export interface OkxAccount {           // 脱敏形：绝不含 raw secret/passphrase
  id: number; label: string;
  apiKeyPreview: string;                // 末 4 位掩码，如 ····c848
  hasSecret: boolean; hasPassphrase: boolean;
  ipWhitelist: string; permissions: string;
  isDefault: boolean; createdTs: number; updatedTs: number;
}
export interface OkxAccountDraft {       // 新增/编辑共用；secret/passphrase 留空=保留(编辑)/必填(新增)
  label: string; apiKey: string; secretKey: string; passphrase: string;
  ipWhitelist: string; permissions: string;
}
export interface OkxAccountTestResult { ok: boolean; latencyMs?: number; code?: string; message?: string; }
export interface OkxAccountBalanceRow { ccy: string; eq: number; availBal: number; frozenBal: number; eqUsd: number | null; }
export interface OkxAccountPosition {
  instId: string; instType: string; posSide: string; pos: number;
  avgPx: number | null; last: number | null; upl: number | null; uplRatio: number | null;
  lever: number | null; marginMode: string;
}
export interface OkxAccountDetail {
  info: { uid: string; acctLevel: string; acctLevelLabel: string; permissions: string[] };
  balance: { totalEqUsd: number | null; details: OkxAccountBalanceRow[] };
  positions: OkxAccountPosition[];
}
export interface OkxAccountsListResponse { accounts: OkxAccount[]; defaultId: number | null; }
```

```python
# services/quant/app/schemas.py（节选；写入字段补 max_length，SQLite 不强制 VARCHAR 长度，C5/H3）
class OkxAccountIn(BaseModel):
    label: str = Field(min_length=1, max_length=128)
    apiKey: str = Field(min_length=1, max_length=128)
    secretKey: str = Field(min_length=1, max_length=128)
    passphrase: str = Field(min_length=1, max_length=128)
    ipWhitelist: str = Field(default="", max_length=512)
    permissions: str = Field(default="", max_length=128)

class OkxAccountUpdate(BaseModel):       # None/空=不改；secret/passphrase 空=保留原值
    label: str | None = Field(default=None, min_length=1, max_length=128)
    apiKey: str | None = Field(default=None, min_length=1, max_length=128)
    secretKey: str | None = Field(default=None, max_length=128)
    passphrase: str | None = Field(default=None, max_length=128)
    ipWhitelist: str | None = Field(default=None, max_length=512)
    permissions: str | None = Field(default=None, max_length=128)

class OkxAccountTestReq(BaseModel):
    apiKey: str = Field(min_length=1, max_length=128)
    secretKey: str = Field(min_length=1, max_length=128)
    passphrase: str = Field(min_length=1, max_length=128)
    ipWhitelist: str = Field(default="", max_length=512)

class OkxAccountTestResult(BaseModel):
    ok: bool; latencyMs: float | None = None; code: str | None = None; message: str | None = None
```

`account_out(row) -> dict` 脱敏序列化（参照 `provider_out`）：返回 `apiKeyPreview=_key_preview(api_key)`、`hasSecret=bool(secret_key)`、`hasPassphrase=bool(passphrase)`、`ipWhitelist`、`permissions`、`isDefault`、`createdTs/updatedTs`，**绝不**返回 `secret_key`/`passphrase`/完整 `api_key`。`_key_preview` **复用既有** `····{key[-4:]}` 实现（P2-Naming：字段名取 `apiKeyPreview` 而非既有 `keyPreview`，因多密钥实体下更清晰；掩码逻辑不重写）。`createdTs/updatedTs` 照 `provider_out` 输出**秒级 UTC 整数** `int(row.created_at.timestamp())`（P2-Time，全系统时间口径一致）。

---

## 5. Data model（数据模型）

新增表 `okx_accounts`（`db/models.py`）：

```python
class OkxAccount(Base):
    """实盘 OKX 账户凭证（设置中心 · 实盘账户）。
    每行一个真实 OKX API 凭证；全表至多一行 is_default=True（set_default 事务保证）。
    secret_key / passphrase 仅后端存，绝不出接口 / 不进日志（C7）；api_key 列表态仅回末 4 位掩码。
    """
    __tablename__ = "okx_accounts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    label: Mapped[str] = mapped_column(String(128))                 # 备注名，如 Dev-JR-US
    api_key: Mapped[str] = mapped_column(Text, default="")          # 列表回掩码
    secret_key: Mapped[str] = mapped_column(Text, default="")       # 绝不出接口
    passphrase: Mapped[str] = mapped_column(Text, default="")       # 绝不出接口
    ip_whitelist: Mapped[str] = mapped_column(String(512), default="")  # informational，逗号分隔
    permissions: Mapped[str] = mapped_column(String(128), default="")   # informational，如 读取/交易
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)
```

字段语义：
- `label`（备注名）：用户可读名称，列表与详情标题展示。
- `api_key` / `secret_key` / `passphrase`：OKX V5 鉴权三件套。`secret_key`/`passphrase` 是签名与鉴权密钥，**最高敏感**，仅后端持久化、绝不外泄；`api_key` 列表态脱敏为末 4 位。
- `ip_whitelist`：**informational**——OKX 侧在创建 API Key 时绑定的 IP 白名单（OKX 服务端强制校验请求来源 IP）。本字段仅供用户记录与对照；连通性测试若回 `OKX_IP_DENIED`（50110）即提示「当前出口 IP 未加入白名单」。
- `permissions`：**informational**——用户备注的权限（如「读取/交易」）。详情面板另展示从 `/account/config·perm` 取到的**权威**权限。
- `is_default`：当前「正在使用」的默认账户，唯一性由 `set_default_okx_account` 事务内逐行清零保证（同 provider 模式，避免 `expire_on_commit=False` 脏值）。

内存形状（详情聚合，OKX 字段→camelCase 归一，数值用 `_optional_float` 容错）：
- `info`：`uid`、`acctLevel`（`acctLv`：1=简单/2=单币种保证金/3=跨币种保证金/4=组合保证金）、`acctLevelLabel`（层级中文）、`permissions`（`perm` 逗号串拆数组）。
- `balance`：`totalEqUsd`（`totalEq`）、`details[]`（每币种 `ccy/eq/availBal/frozenBal/eqUsd`）。
- `positions[]`：`instId/instType/posSide/pos/avgPx/last/upl/uplRatio/lever/marginMode(mgnMode)`，无持仓返回空数组。

---

## 6. Frontend UX（前端交互与版式）

- **导航**：`SettingsNav.SECTIONS` 追加「实盘账户」，与既有两项视觉一致（选中态 `accent-soft`）。
- **主面板 `OkxAccountsSection`**（同 `AiModelsSection` 骨架）：顶部「当前默认账户」横幅（展示默认账户 label + 「就绪/待配置」徽标）→ 账户卡列表 → 「+ 添加账户」按钮；React Query key `["okx","accounts"]`，加载态 `Skeleton`、错误态卡内重试。
- **账户卡 `OkxAccountCard`**（同 `ProviderCard`）：
  - 展示态：`label` + `默认`徽标（`Badge tone="accent"`）；副行掩码 `API ····c848` + `密钥已存/未存` + IP 白名单摘要；右侧按钮组「测试连通性」「设为默认」「编辑」「删除（二次确认）」「查看详情」。
  - 「查看详情」仅当 `hasSecret && hasPassphrase` 可用（否则禁用并 title 提示「请先补全密钥」）；点击打开 `OkxAccountDetailModal`。
  - 连通性结果行复用 `ConnectivityResult` 组件（`role="status" aria-live="polite"`，✓ 连通+时延 / ✗ 友好码+message hover 详情）。需在 `ConnectivityResult` 的 `CODE_HINT` 补充 OKX 语义码映射（或新建 `accountConnHint` 复用同组件——实现期二选一，推荐扩展同一 `CODE_HINT`，见 R-Reuse）。**（P2-TestResultType）**该组件 result prop 现强类型 `ProviderTestResult`，OKX 结果 `OkxAccountTestResult` 同形（`ok/latencyMs/code/message`）；实现期将 prop 放宽为共享最小形（或抽公共 `TestResult` 类型）即可复用，勿复制组件。`OKX_IP_DENIED` 文案旁建议回显当前服务端出口 IP，便于自助加白（P2-IPEcho，见 R-IP）。
- **编辑表单 `OkxAccountEditForm`**（同 `ProviderEditForm`）：字段顺序 备注名 → API Key → Secret Key（密码框+显隐）→ Passphrase（密码框+显隐）→ IP 白名单 → 权限备注；编辑态 secret/passphrase 占位「•••• 保留原值」，hint 文案统一「密钥仅保存在本地后端，绝不上传」；字段错误经 `${id}-err` 串 `aria-describedby`（H6）。
- **详情面板 `OkxAccountDetailModal`（P1-Modal 修订）**：因**全仓现无可复用 Modal / Dialog 原语**（前作走内联编辑、未引入浮层），本期**先在 `components/ui/index.tsx` 新增一个可复用 `Modal` 原语**，详情面板与未来任何浮层均复用之，避免一次性孤岛。`Modal` 契约（实现与 `Modal.test.tsx` 须满足）：
  - **结构 / a11y**：`role="dialog"` + `aria-modal="true"` + `aria-labelledby`（指向标题）；React **portal** 到 `document.body`；半透明遮罩 + 居中卡片，配色用既有 CSS 变量、明 / 暗主题零适配。
  - **行为**：`Esc` 与**点击遮罩**触发 `onClose`；挂载时**初始焦点**入对话框（标题或关闭钮）；`Tab`/`Shift+Tab` **焦点陷阱**于对话框内；卸载时**焦点恢复**到触发元素；打开期 `body` **滚动锁定**。
  - **内容（三段式）**：① 账户信息（UID / 层级 / 权限徽标组）；② 资产（总权益大字 + 分币种表，`tabular` 数字、`format.ts` 金额格式化）；③ 持仓（表格：标的 / 方向 / 数量 / 均价 / 最新价 / 未实现盈亏，盈亏用 `--long`/`--short` 着色，空仓显「当前无持仓」）。资产 / 持仓抽 `BalanceTable`/`PositionsTable` 子组件守阈值（P2-Size）。懒查 `accountApi.accounts.detail(id)`，加载 `Skeleton`、失败显式错误态（不假数据）。
  - **备选（同样合规）**：若团队判定浮层基础设施偏重，可改为**卡内可展开详情区**（同既有 `ProviderCard` 内联编辑模式、零新增浮层、a11y 风险更低）——二者择一，文档默认前者「可复用 `Modal` 原语」。
- **设计 token / 主题**：全程复用既有 CSS 变量与 `components/ui` 原语，亮/暗主题零额外适配。

---

## 7. Sequence of operations（关键时序）

1. **新增账户**：用户在 `OkxAccountsSection` 点「+ 添加账户」→ 内联 `OkxAccountEditForm` 填写 → （可选）「测试」→ `POST /accounts/test`（草稿，带显式密钥）→ ✓ 后点「添加」→ `POST /accounts` → 成功 invalidate 列表并轻量反馈「已添加 ✓」。
2. **测试连通性（存量）**：卡上「测试连通性」→ `POST /accounts/{id}/test` → 后端取库内密钥构造 `OKXPrivateClient` → 签名 `GET /account/config` → 解析 `code`：`0`→`{ok:true,latencyMs}`；非 0 经 `_map_okx_code` → 语义码 → 前端友好提示。
3. **设默认**：「设为默认」→ `POST /accounts/{id}/default` → 事务清其余 `is_default` 后置位 → 返回最新列表 → 横幅与徽标刷新。
4. **查看详情**：「查看详情」→ Modal 挂载 → `GET /accounts/{id}/detail` → 后端 `fetch_account_detail` 并发取 config/balance/positions → 归一聚合 → Modal 渲染信息/资产/持仓。
5. **编辑（保留密钥）**：「编辑」→ 改 label/IP/权限，secret/passphrase 留空 → `PUT /accounts/{id}`（`OkxAccountUpdate`，空 secret/passphrase 不覆盖）→ 保存。
6. **删除**：二次确认 → `DELETE /accounts/{id}` → 若删的是默认，`_reassign_default_after_delete` 取 id 最小者为新默认。

---

## 8. Backend repository contract（仓储契约，db/repositories.py 新增）

与 provider 同构（首参 `session` 函数式）：

```python
@dataclass(frozen=True)
class OkxAccountValues:  # 新增可写字段聚合
    label: str; api_key: str; secret_key: str; passphrase: str; ip_whitelist: str; permissions: str

@dataclass(frozen=True)
class OkxAccountPatch:    # None=不改；secret/passphrase 空=保留原值
    label: str | None = None; api_key: str | None = None
    secret_key: str | None = None; passphrase: str | None = None
    ip_whitelist: str | None = None; permissions: str | None = None

async def list_okx_accounts(session) -> list[OkxAccount]: ...        # ORDER BY id ASC
async def get_okx_account(session, account_id) -> OkxAccount | None: ...
async def get_default_okx_account(session) -> OkxAccount | None: ... # where is_default
async def create_okx_account(session, *, values, is_default=False) -> OkxAccount: ...
async def update_okx_account(session, account_id, *, patch) -> OkxAccount | None: ...  # 空 secret/passphrase 保留
async def delete_okx_account(session, account_id) -> str: ...        # 'ok'|'not_found'；删默认回落
async def set_default_okx_account(session, account_id) -> bool: ...  # 事务清其余再置位（逐行 ORM）
async def _reassign_default_okx_after_delete(session) -> None: ...   # id 最小者为新默认
async def count_okx_accounts(session) -> int: ...
```

`update_okx_account` 的密钥保留语义：`if patch.secret_key: row.secret_key = patch.secret_key`（空串/None 不覆盖），passphrase 同理；`api_key` 显式给出才改。`set_default` 事务内逐行清零再置位（不用批量 UPDATE，规避 `expire_on_commit=False` 脏值，延续 provider 经验）。首账户创建时若库内无默认，可在路由层将其自动设为默认（`count==0 → is_default=True`），保证「添加即用」。

---

## 9. Error handling（错误处理）

- **边界一次性校验**：必填字段缺失在 Pydantic / 路由层即拒（422/400），不进 OKX 往返。
- **OKX 鉴权 / 业务错误**（P1-ErrHandler 修订，消除二义与 500 漏洞）：`OKXPrivateError` **子类化** `OKXError`（§2.2），故 detail 路由只需 `raise OKXPrivateError(_map_okx_code(原码), _redact(msg))`，由 `main.py` **既有全局 `okx_error_handler`** 统一捕获回 **502 `{error:{code,message}}`**——**不依赖**「或路由内显式 `JSONResponse`」这一二义路径，杜绝未捕获时的裸 500；在 test 路由**不抛**而捕获归一为 `OkxAccountTestResult{ok:false,code,message}`（测试是诊断动作，错误即结果而非异常）。
- **新建 `api/accounts.py` 的 `HTTPException` 封套（P2-ErrEnvelope）**：本期边界错误（`ACCOUNT_NOT_FOUND` 404、必填缺失 422/400）一律 `raise HTTPException(detail={"code","message"})`，由 `main.py` **既有全局 HTTPException 处理器**包成 `{error:{code,message}}`；**勿**在模块内另造局部封套，保持与既有 provider 路由错误体一致。
- **脱敏单点收口**：所有对外 message 先 `_redact(secret, passphrase)` 再截断 ≤200；该路径**不写日志**，若未来加 logger 须复用 `_redact`（C7）。
- **超时 / 不可达**：`CONNECTION_FAILED`，message 含「连通性测试超时 / 无法连接 OKX」，不假数据（C1）。
- **404**：未知 `account_id` → `ACCOUNT_NOT_FOUND`。

---

## 10. Testing & acceptance criteria（测试与验收）

**后端 pytest（不依赖网络，私有客户端打桩）：**
1. `test_okx_signing.py`：给定固定 `secret/ts/method/requestPath/body`，`_sign` 产出**确定** base64（与离线手算/已知向量比对）；`_iso_timestamp` 形如 `....\d{3}Z`；头组装含全部 5 个 `OK-ACCESS-*`/`Content-Type`，实盘默认不含 `x-simulated-trading`。
2. `test_accounts_api.py`：
   - CRUD：create→list→update→delete 全链路；
   - **脱敏铁律**：任何 GET/POST 响应 JSON **不含** `secret_key`/`passphrase` 明文、不含完整 `api_key`（仅末 4 位）；
   - 设默认：`set_default` 后全表仅一行 `is_default`；删默认后回落 id 最小者；
   - test 路由：打桩 `OKXPrivateClient` 返回 OKX `code=50110/50105/50113/0`，断言映射为 `OKX_IP_DENIED/OKX_AUTH_PASSPHRASE/OKX_AUTH_SIGN/ok:true`；
   - detail 路由：打桩三接口聚合，断言 `info/balance/positions` 归一形状；OKX 失败→502 显式错误体。
3. 复用既有 `services/quant/tests` 临时 DB 隔离约定（见记忆「aragon-quant-test-isolation」），不污染共享库。

**前端 vitest（纯函数）：**
4. `accountSettings.test.ts`：`maskKey("…c848")→····c848`、空串→`""`；`validateAccountDraft` 新增态缺 label/apiKey/secretKey/passphrase 各自报错、编辑态 secret/passphrase 留空合法；`emptyDraft`/`draftFromAccount` 字段齐备且不回填明文密钥。

**验收清单（Definition of Done，供 Subtask #2/#3）：**
- [ ] 设置左侧出现「实盘账户」，点击进入面板。
- [ ] 可新增多条账户（label/apiKey/secretKey/passphrase/ip/权限），列表展示掩码，**响应永不含明文 secret/passphrase**。
- [ ] 任一账户可「测试连通性」：用任务给定真实凭证（**补齐 Passphrase 后**，且**当前服务端出口 IP ∈ 该凭证白名单** `203.0.113.10`）应回 ✓ 连通+时延；若出口 IP 不符则回 `OKX_IP_DENIED`(50110)——此为**环境/代理出口因素，非代码缺陷**（P2-IPEcho，测试前确认 Clash 出口或在 OKX 侧加白本机出口）；故意错填 secret/passphrase 应回对应友好错误码 `OKX_AUTH_SIGN`/`OKX_AUTH_PASSPHRASE`。
- [ ] 可设默认，全表唯一默认；删默认自动回落。
- [ ] 连通 OK 的账户可「查看详情」，展示 UID/层级/权限、总权益与分币种资产、当前持仓（或「无持仓」）。
- [ ] 后端 `pytest -q`（不含 network）与前端 `pnpm test` 全绿；`pnpm --filter web tsc` 通过。
- [ ] 全程不下任何真实订单；OKX 不可达显式报错、绝不假数据。

---

## 11. Risks & mitigations（风险与缓解）

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R-Passphrase | OKX V5 强制 Passphrase，但任务示例凭证未给 | 不加该字段则**鉴权必败**（50105），功能不成立 | 表单**新增 Passphrase 必填字段**；文档显式告知示例缺失项需用户补齐；测试错误码 `OKX_AUTH_PASSPHRASE` 给明确提示。 |
| R-Secret | Secret/Passphrase 泄露或误入库 seed / 日志 / 前端 | 资金安全事故 | secret/passphrase **仅后端列存、绝不出接口、绝不进日志**；`account_out` 不返回；错误 message `_redact` 单点脱敏；**严禁**把任务给的真实凭证写入 seed / 提交（仅手动测试用）。 |
| R-NoOrder | 引入私有客户端后被误用于下单，破坏 Phase 1 不变量 | 违背产品红线、潜在真实损失 | `OKXPrivateClient` **只暴露只读 GET**（config/balance/positions），不提供任何下单方法；评审清单显式核验「无 POST 交易接口」。 |
| R-IP | 出口 IP 与 OKX 白名单不符（本机 / 桌面端 / 容器 / Clash 代理出口各异） | 测试与详情回 50110，用户困惑；尤其示例凭证白名单绑定 `203.0.113.10`，经本机代理出口多半不符 | 错误码 `OKX_IP_DENIED` 提示「当前出口 IP 未加入白名单」；IP 白名单字段 informational 供对照；**（P2-IPEcho，由可选升为推荐）**在 `OKX_IP_DENIED` 结果旁**回显服务端真实出口 IP**（可由一次轻量 ipinfo / OKX 错误上下文取），便于用户一眼比对并自助加白，显著降低示例凭证联调摩擦。 |
| R-Proxy | OKX 地区受限，私有请求未透传代理则连不通 | 误判为鉴权失败 | 私有客户端复用 `settings.proxy_url`（同公共客户端约定）；`CONNECTION_FAILED` 与鉴权错误码区分，避免误导。 |
| R-TimeSkew | 本机时钟漂移致签名 timestamp 过期（50102/50104） | 偶发测试失败 | 错误码 `OKX_TIME_SKEW` 提示校准系统时间；timestamp 用 `datetime.now(timezone.utc)` 毫秒精度。 |
| R-Reuse | `ConnectivityResult.CODE_HINT` 现仅含 LLM 错误码 | OKX 码显示为原始串 | 实现期在同组件 `CODE_HINT` 扩充 OKX 语义码中文（推荐），或新建 `accountConnHint` 注入；二者皆低成本。 |
| R-Demo | 实盘 vs 模拟盘端点头不同（`x-simulated-trading`） | 误连模拟盘或反之（50101） | `okx_account_demo` 配置开关默认 `False`（实盘不发该头）；错误码 `OKX_ERROR` 透传原码辅助定位。 |
| R-Rate | 用户频繁点测试/详情触发 OKX 限频 | 偶发 429 | 私有请求 `timeout` + 单次重试；按钮 `busy` 期禁用避免连点；必要时复用 `_RateLimiter` 思路（本期用户触发频率低，暂不强加）。 |

---

## 12. Configuration（配置）

`config.py` 新增（字段名即环境变量名，`OKX_ACCOUNT_*`，大小写不敏感）：
- `okx_account_demo: bool = False`：`True` 时私有请求附 `x-simulated-trading: 1`（模拟盘）；默认实盘不发。
- `okx_account_timeout_seconds: float = 15.0`：私有请求超时上限。

既有 `okx_api_key/okx_secret_key/okx_passphrase`（占位）保持不动——本方案凭证以**库内多账户**为真源，env 占位仅历史保留，不参与本特性（如需「env 兜底单账户」可后续增强，本期不做，避免与多账户语义冲突）。

> 安全提醒（延续 README）：`.env` 已被 `.gitignore` 排除；任何真实密钥**切勿入库**；若曾明文出现请视为泄露并在 OKX 轮换。
>
> **（P2-GitHygiene）** 任务 prompt 内含**真实可用**的开发凭证（apikey/secretkey/IP/备注名），仅供 Subtask #2/#3 手动联调，**绝不写入 seed、绝不提交**。下游提交务必**精确 `git add <具体路径>`、严禁 `git add -A`**，并复核 diff **不含**任何 key / secret / passphrase 明文与 `aragon.db`（延续前作 C7 实践）。

---

## 13. Out of scope（明确不做）

- 真实下单 / 撤单 / 改单 / 资金划转（守 Phase 1 不变量，留待 Phase 2 带护栏实现）。
- 凭证字段静态加密 at-rest（本期与既有 `agent_providers.api_key` 一致为明文列存于本地 SQLite；如需 KMS / 应用层加密属独立增强，见 R-Secret 已述边界）。
- WebSocket 私有频道（账户 / 持仓推送）；详情为按需 REST 拉取。
- env 单账户兜底解析器（与多账户真源冲突，本期不引入）。

---

> 字数自检：本文档技术正文 > 800 字，覆盖 Overview / Technical design / 文件变更计划 / 接口 / 数据模型 / 测试与验收 / 风险缓解 / 配置等全部要求章节，细节足以支撑下游工程师逐行实现。

---

## 评审结论（Review Verdict / Subtask #1）

**结论：有条件通过（Approved with conditions）。**

- 方案核心链路（设置中心第三项「实盘账户」+ 多账户凭证 CRUD + 设默认唯一性 + 连通性测试 + OKX V5 私有签名 + 详情聚合（信息/资产/持仓）+ 脱敏不外泄 + 守 Phase 1「不下单」不变量）经**逐节对照真实源码核验**：后端建立在既有 `repositories(函数式 CRUD + 逐行 set_default)/models/schemas(provider_out+_key_preview)/okx.rest_client(proxy_url+okx_rest_base)/config/main(okx_error_handler 502)/session(create_all 无迁移)` 之上，前端建立在 `SettingsPanel(15/75)/SettingsNav/AiModelsSection/ProviderCard/ProviderEditForm/ConnectivityResult/agentApi(/api/py)/store(settingsSection)/ui 原语/format/vitest` 之上，**字段名与分层均经核验真实存在**，可用现有技术栈构建、与 `CLAUDE.md` 及既有命名 / 风格一致、范围适度（read-only、无过度工程）。
- **OKX V5 签名算法**（ISO8601 毫秒 UTC 带 `Z`、`prehash=ts+method+requestPath(含 query)+body`、`base64(HMAC_SHA256)`、强制 `OK-ACCESS-PASSPHRASE`、实盘不发 `x-simulated-trading`）与真实协议一致，且要求确定性单测，可信。
- **2 个 P1 已在本 v2 文档正文就地修复，无遗留 P0/P1：**
  1. **P1-Modal**：详情浮层依赖一个**全仓不存在**的 Modal 原语而文件计划未规划其基础设施 → 已在 §3/§6 规划**可复用 `Modal` 原语**（明确 a11y/焦点/滚动锁/portal 契约 + `Modal.test.tsx`），并给出「卡内可展开详情区」合规备选。
  2. **P1-ErrHandler**：新 `OKXPrivateError` 不被既有 `okx_error_handler` 捕获、detail 路由存在裸 500 漏洞 → 已在 §2.2/§2.3/§9 定为 `OKXPrivateError(OKXError)` 子类化，复用全局 502 `{error:{code,message}}`，并先 `_map_okx_code` 归一语义码。

**放行条件（实现期须落实，均为 P2，不阻断进入开发）：**

1. **P1-Modal 落地完整性**：`components/ui` 新 `Modal` 原语须实现并通过 `Modal.test.tsx`（Esc/遮罩/初始焦点/焦点陷阱/焦点恢复/滚动锁）；或采用「卡内可展开详情区」备选——二者择一、不得即兴造一次性浮层。
2. **P1-ErrHandler 落地完整性**：`OKXPrivateError` 子类化 `OKXError`；detail 路由 `_map_okx_code` 后 `raise`，由全局 handler 回 502；test 路由捕获归一为 `{ok:false}`；`test_accounts_api.py` 覆盖「OKX 失败 → 502 显式语义码体」。
3. **错误体一致性（P2-ErrEnvelope）**：`api/accounts.py` 的 `HTTPException` 复用 `main.py` 全局处理器包成 `{error:{code,message}}`，勿造局部封套。
4. **字段一致性（P2-Naming/P2-Time）**：`account_out` 掩码复用 `_key_preview`、时间输出秒级 UTC 整数。
5. **复用 ConnectivityResult（P2-TestResultType/R-Reuse）**：放宽 result prop 类型并在 `CODE_HINT` 补 OKX 语义码，勿复制组件。
6. **联调前置（P2-IPEcho）**：示例凭证回 ✓ 需补 Passphrase 且出口 IP ∈ 白名单；推荐在 `OKX_IP_DENIED` 旁回显出口 IP。
7. **安全红线（P2-GitHygiene / R-Secret / C7）**：secret/passphrase 仅后端列存、不出接口、不进日志；真实凭证绝不写 seed / 提交；下游精确 `git add <具体路径>`、严禁 `git add -A`，复核 diff 不含任何密钥与 `aragon.db`。
8. **不变量守护（R-NoOrder）**：`OKXPrivateClient` 只暴露只读 GET（config/balance/positions），无任何下单 / 撤单 / 划转写方法，守住 Phase 1「real trading disabled」红线（`.claude-index`）。

> 满足上述条件即视为完全达成全局目标「功能正确 / 稳健可靠 / 安全合规 / 美观优雅」。准予进入 Subtask #2（代码开发）。
