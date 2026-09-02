# AI 自动交易：让每一条「向外界要真话」的通路真的把话要回来 —— 静默降级修复

> 版本：**v2**（评审节点定稿；v1 为设计节点产出）
> 评审：Anthropic 工程团队资深评审，见下方《评审记录》与文末《评审结论》。**P0 五条 / P1 十二条已在正文修毕**。
> 特性目录：`docs/plans/ai-trade-silent-degradation-repair/`
> 所属循环：loop `tge-8712e7ab` 第 2 轮（设计节点 Subtask #0）
> 需求原文：「这个项目，是一个顶级量化 Agent 项目……当前阶段【AI 自动交易】功能，排查其中的 BUG 和不合理的地方，修复完善，确保其稳健可靠顶级，可以看看桌面端的日志，看看之前的交易，看看一直亏损的原因，并查阅代码，诊断可能的 BUG，并进行修复。」
> 基线提交：`8451163`（第 1 轮「离场极性 + 活性看门狗」已合入）。本文所有代码锚点按该提交的工作树读出。
> 勘察范围：用户本机 `%APPDATA%/aragon-desktop/logs/backend.log` **全量 254,662,610 字节 / 2,163,636 行**（不是采样）、生产库 `%APPDATA%/aragon-desktop/aragon.db`（211 条 `ai-trade` run / 36 张表）、`services/quant/app/okx/` 与 `services/quant/app/ai_auto/intel/` 全部模块、`desktop/src/` 与 `apps/web/components/Settings/`。
> 复算：本文每一个数字都能用 §附录 A 的 `evidence/audit.py` 在同一台机器上逐位复算。

---

## 评审记录

> 评审人：Anthropic 工程团队资深评审（loop `tge-8712e7ab` 第 2 轮 · 评审节点 Subtask #1）
> 评审对象：本文件 v1（1,292 行 / 62,090 字符 / 18 节）
> 评审方式：**逐节比对源码锚点**，不接受任何未在工作树里核实过的断言。下列每一条都附了它被核实的文件与行号。
> 处置：**P0 五条 / P1 十二条已在本文正文逐条修毕**（v1 → v2），P2 七条按「登记但不改」处理。

### 评审口径

本轮 spec 的证据质量是仓库里少见的高——全量日志聚合、双时刻对照读数、可复算脚本，
四条根因都是**真的**，没有一条是推断出来的。所以评审没有花在「这些问题存不存在」上，
而是全部花在一个问题上：

> **这份修法，会不会在它自己声称要修的那个形状上再犯一次？**

答案是会，而且有五处。它们的共同形状与本文 §二 R-2 自己总结的那一条**一字不差**：

> 「一道 fail-closed 的闸，如果它的判据在某类环境里恒假，这个功能在该类环境里就是零可用——而『零可用』和『很安全』在日志里长得一模一样。」

- **P0-1** 把「TUN 机器上 0 成功」换成了「配了显式代理的机器上 0 成功」；
- **P0-2** 给一个**声明了 `response_model`** 的既有端点加了四个加法键，它们会被 FastAPI 静默剔除；
- **P0-3 / P0-4** 让「交易所到底说了什么」这条本轮的核心产出，在真实失败路径上**没有任何一处宿主**；
- **P0-5** 让两条声明好的回退开关，在唯一的产品形态（打包桌面端）里**按不下去**。

四者都不会在任何一条离线用例上变红。

### P0（阻断级 · 已在正文修毕）

| 编号 | 位置 | 问题 | 核实依据 | 处置 |
|---|---|---|---|---|
| **P0-1** | §六 B-1c 出网后对端复核 | **对端复核在「配了显式代理」的机器上会把情报抓取变成 100% 拒绝，而且 `strict` 回退档也救不回来。** `intel/evidence.py:44` 把 `settings.proxy_url` 传进 `ReaderConfig.proxy`，`reader.fetch_many` 用 `httpx.AsyncClient(proxy=cfg.proxy)` 出网；此时 TCP 对端是**代理**（本项目目标用户的常见形态是 `127.0.0.1:7890`），`classify_addr` 判回环 ⇒ `private` ⇒ **每一篇都记 `private_peer`**。而 v1 明写「这一条在三个档位下全开，包括 `strict`」——于是本轮把 R-2 的病灶从一类拓扑搬到了另一类拓扑，形状完全相同 | `app/ai_auto/intel/evidence.py:27-44`（`proxy=getattr(settings,"proxy_url",None)`）；`app/ai_auto/intel/reader.py:305`（`httpx.AsyncClient(..., proxy=cfg.proxy or None)`）；`app/config.py:1455` `proxy_url` | §六 B-1c 重写：**`cfg.proxy` 非空 ⇒ 整条对端复核跳过**并如实记 `peer_unverifiable_via_proxy`（只影响可观测性，不影响放行）；`server_addr` 先归一（它是 `(host, port)` 元组）；新增 G-B7b / G-B7c 两条门禁 |
| **P0-2** | §六 B-2 / §十一 11.1 / §十二 12.3 | **`GET /intel/documents/health` 声明了 `response_model=IntelDocumentHealthOut`，B-2 的四个加法键会被 FastAPI 静默剔除。** v1 只对**两个新端点**写了「不声明 `response_model`」的纪律，对这个**既有**端点只字未提，§10.3 的文件清单里也没有 `api/schemas_intel_evidence.py`。这正是本仓为 `edgeSource` 付过一次代价的形状，且它是静默的 | `app/api/ai_intel.py:277`（`@router.get("/intel/documents/health", response_model=IntelDocumentHealthOut)`）；`app/api/schemas_intel_evidence.py:66-74`（严格 5 字段 `BaseModel`） | §六 B-2 增「同改 `schemas_intel_evidence.py`」硬约束；§10.3 补该文件；新增门禁 G-B10（起一次 `TestClient`，断言四个键**真的到达了客户端**，而不是断言处理函数的返回值） |
| **P0-3** | §五 A-2 参数阶梯 | **阶梯的推进判据取自一个不携带该信息的入参。** `private_client._get` 把「HTTP 400 参数错」与「429 退避耗尽」**都**抛成 `OKXPrivateError("OKX_HTTP_ERROR", …)`，而 `OKXPrivateError` 只有 `code`/`message` 两个字段、**没有 HTTP 状态**。v1 写「HTTP 非 2xx ⇒ 跳 ②」，但 `get_trade_fee` 在异常里看不到 HTTP 状态；更糟的是传输层失败抛的是 `OKX_UNREACHABLE`，若也推进阶梯，一次真实断网会在启动期把 N 次请求变成 2N 次（每次内部还有 3 跳重试 + 指数退避），并把一次链路故障记成「费率参数错」 | `app/okx/private_client.py:36-41`（`class OKXPrivateError(OKXError)` 无新增字段）、`:244-249`（400 分支）、`:255-261`（429 耗尽分支同抛 `OKX_HTTP_ERROR`） | §五 A-1 给 `OKXPrivateError` 增**只读加法字段** `http_status: int | None`（默认 `None`，老构造签名一字不动）；§五 A-2 的阶梯推进判据改为 `http_status is not None and 400 <= http_status < 500 and http_status != 429`，**其余一律不推进、原样上抛**；新增门禁 G-A7b / G-A7c |
| **P0-4** | §五 A-2 / A-4 · §十二 12.3 | **O-2 声称的「`config` 时携带最近一次失败的 `code`/`msg`」在现有回落链上没有任何宿主。** 三重截断：①`trading._normalize_trade_fee({}) → None`（`trading.py:611-622`）；②`fetch_trade_fee` 的签名是 `dict \| None`，返回 `None` 时 `_errors` 被丢弃（`trading.py:572-586`）；③`FeeRateCache.refresh` 用 `except Exception … logger.debug` 吞掉一切（`fee_rates.py:114-117`），而 `FeeRateCache.wire()` 在 `snapshot is None` 时**整段返回 `None`**（`fee_rates.py:139-140`）。也就是说：在本轮唯一真实发生的那个场景里（1,524/1,524 全败），`aiStats.feeRates` 这个键**根本不存在**，A-4 要往里加的 `lastError` 没有地方可放。§12.3 的示例只描述了成功态 | `app/okx/trading.py:572-586,611-629`；`app/ai_auto/fee_rates.py:85-145` | 新增 §五 A-5「失败也要有宿主」：`FeeRateCache` 增 `last_error` 状态 + `wire()` 在无快照时返回**降级形状** `{"source":"config","lastError":{…},"attempted":true}`；`fetch_trade_fee` 的返回契约改为 `FeeFetchOutcome`（`wire \| None` + `errors`）；`_bounded_fee_rate` 不再吞掉原因。§12.3 补降级态示例；新增门禁 G-A11 / G-A12 |
| **P0-5** | §八 D-1 / D-2 · §十三 | **两把声明好的回退开关在打包桌面端里按不下去。** ①`desktop/src/lib/config.js:80-82` 的 env 透传白名单是**前缀式**的（`AGENT_ \| LIVE_ \| AI_TRADE_ \| DISCOVERY_ \| OKX_ \| SECURITY_`），`LOG_HTTP_CLIENT_LEVEL` **一个前缀都不匹配** ⇒ 后端 `Settings`（`extra="ignore"`）静默丢弃 ⇒ D-2 声明的「一键恢复 `INFO`」在唯一的产品形态里不存在；②`ARAGON_LOG_MAX_BYTES` / `ARAGON_LOG_KEEP` 读的是 `process.env`，而 `openLogStreams()` 在 `bootstrap()` 的**第一行**执行（`main.js:528-529`），`agentEnv` 要到后端装配时才被读（`main.js:150-155`），GUI 启动的进程 `process.env` 里也不会有它们 | `desktop/src/lib/config.js:78-88`；`desktop/src/main.js:43-49,150-155,528-529`；`desktop/scripts/config-passthrough.test.mjs`（前缀式白名单的既有冻结用例） | 键名改为 **`AI_TRADE_LOG_HTTP_CLIENT_LEVEL`**（沿用硬约束前缀，桌面壳零改动）；`openLogStreams()` 改为先 `agentEnvLib.loadAgentEnv()`（失败即静默回落默认值，日志器绝不成为启动失败的原因）；`config-passthrough.test.mjs` 的探针表补两行；新增门禁 G-D7 |

### P1（已在正文修毕）

| 编号 | 位置 | 问题 | 核实依据 | 处置 |
|---|---|---|---|---|
| **P1-1** | §十 / 全局 | **未登记阈值偏离。** CLAUDE.md §二原文：「所有阈值都允许根据项目实际情况调整，但调整必须在 `.claude-index/config.md` 中显式记录」。本轮往 `executor.py`(1,059) / `session.py`(3,756) / `manager.py`(2,211) / `config.py`(1,470) / `trading.py`(844) 五个**已越 800 行红线**的文件上继续加行，而 §十 只字未提这张登记表 | `.claude-index/config.md:198` 起的偏离表（每一轮都补录，含「本轮新增了什么、下一次触碰应先还什么」） | §10.5 新增「阈值偏离登记（本轮必做）」小节，五个文件逐个给出增量与下一次的还债指示；G-Doc 扩为同时断言登记表已更新 |
| **P1-2** | §五 A-1「硬性约束·脱敏」 | **`_redact` 落在 `private_client` 上会造成循环导入。** v1 写「两个调用点都要再过一次现有的 `_redact`（`trading.py` 已有该单点收口）」——但 `_redact` 实际住在 `okx/accounts.py:67`，而 `accounts.py:16` **反向 import 了 `private_client`**。在 `private_client.py` 里 `from .accounts import _redact` 即 import 期环，症状是整个后端起不来（本仓刚为一次同形的 import 期崩溃付过代价，见 `api/ai_intel.py` 顶部的 204 注释） | `app/okx/accounts.py:16,67-74`；`app/okx/trading.py:23`（`from .accounts import … _redact`） | `_redact` **下沉到新叶子模块 `okx/error_http.py`**，`accounts.py` 改为从那里 re-export（单点收口不变、既有 import 面一字不改）；新增门禁 G-A13（AST 断言 `accounts._redact is error_http._redact`） |
| **P1-3** | §五 A-3 | **把 4xx 记成 `note_ok` 会制造一个新的假阴性。** `LinkRegistry.note_ok` 同时做两件事：推进 `last_ok_ts` **并把 `consecutive_failures` 清零**。私有通道上所有端点共用同一个 key，于是「真失败 → 真失败 → 一次 400 参数错 → 真失败 → 真失败」这条序列**永远达不到降级阈值**。用一个假阴性换掉另一个假阴性不是修复 | `app/okx/link_state.py:109-118`（`:114` 即那一行）（`h.consecutive_failures = 0`） | A-3 改为 `LinkRegistry.note_rejected(key, …)`：**只**累加 `rejected_4xx` 与 `last_rejected_ts`，**不动** `consecutive_failures`、**不动** `last_ok_ts`。语义正确（请求到了 ≠ 有过一次成功）；G-A10 相应改写 |
| **P1-4** | §九 E-1 / §十一 11.2 | **`truthSources` 进不了 `_status_payload()`，且 §九「零 IO」与它自己的数据来源矛盾。** `_status_payload` 是**同步**方法（`session.py:3397 def _status_payload(self) -> dict`），而 `intelReader` 那一行的真源 `repo.document_health()` 是 async DB 读——同步方法里没有 await 的位置；同时 §九 E-1 自称「只读、**零 IO**、永不 5xx」，而它的四行里恰有一行必须查库 | `app/ai_auto/session.py:3397`；`app/db/ai_intel_evidence_repositories.py:209-223` | §九 E-1 的纪律改为「**只读、永不 5xx、至多一次只读 DB**」并显式说明哪一行是 IO；§11.2 的 WS 键收窄为**只由进程内状态派生的三行**（费率 / 配额 / 决策模型），`intelReader` 由 REST 独占，并在 §11.2 明写「WS 的 `degradedCount` 的分母是 3 不是 4」；新增门禁 G-E7 |
| **P1-5** | §六 B-2 · §十二 12.3 | **`blockedByCode` 在零迁移前提下不可计算。** `AiIntelDocument.status` 存的是 `ok\|blocked\|http_error\|timeout\|network_error\|not_html\|too_large\|empty`，**不是** `private_host` 这类拒因；拒因今天只出现在一行 `logger.info` 里，从不落库。而 §12.1 / N-7 声明零迁移，于是它无处可存 | `app/db/models.py:1196`（`status` 的取值域注释）；`app/ai_auto/intel/reader.py:243-247`（拒因只进日志） | 键改名为 **`blockedByStatus`**（从 `status` 直接 GROUP BY，零迁移即可算）；拒因分布改由**进程内计数器** `reader_reject_counter`（重启归零，wire 上如实标 `sinceProcessStart: true`）提供，命名为 `rejectCodesSinceStart`。§12.3 同步改；新增门禁 G-B11 |
| **P1-6** | §10.3 | **两个「唯一真源」的改动点缺席于文件清单。** ①`source` 的**唯一**产出点是 `trading._normalize_trade_fee`（`trading.py:629` 硬编码 `"source": "exchange"`），而 §10.3 只列了 `ai_auto/fee_rates.py`——后者是**读**这个值的（`snapshot_from_wire` 的 `wire.get("source")`），改它不会让 `exchange:family` 出现；②B-2 四个新读数的唯一装配点是 `db/ai_intel_evidence_repositories.py::document_health`，同样未列。这是本仓成文的「有读点没写点」的镜像 | `app/okx/trading.py:611-629`；`app/ai_auto/fee_rates.py:44-70`；`app/db/ai_intel_evidence_repositories.py:209-223` | §10.3 补两行（含 `api/schemas_intel_evidence.py` 与 `okx/accounts.py`），并在 A-2 / B-2 正文里把「谁产出、谁读」写死 |
| **P1-7** | §零 / §一 E-5 / 附录 D F-7 | **「`reader_attempt`/`reader_ok` 落库却没有任何读点」与事实不符。** 它们已被 `db/ai_intel_repositories.py:139-140` 映进轮次 wire，并被 `apps/web/components/Intel/IntelRoundsTab.tsx:172-173` 渲染成「正文 5/6」。B-2 的立论建立在一句能被一眼证伪的话上，会让整节的可信度打折 | `app/db/ai_intel_repositories.py:139-140`；`apps/web/components/Intel/IntelRoundsTab.tsx:172-173`、`IntelRoundsTab.test.tsx:101` | 三处表述统一改为**准确的那一句**：「有逐轮读点（轮次页那一行），但**从不聚合成结论、从不跨轮判趋势、从不告警**」——这恰好也是 B-2 真正在做的事（`zeroOkStreak` + 横幅），改完之后立论比原来更硬 |
| **P1-8** | §八 D-2「信息损失评估：零」 | **该评估不成立，而且证伪它的正是本文自己。** §一 E-2 / E-8 里 `bigmodel · 529 × 779 / 429 × 108` 与 `api.kimi.com · 403 × 408` 这几组读数，**只可能**来自 `httpx` 的 `INFO` 行——LLM 侧没有第二个按状态码计数的地方（`decider` 只有 `SummaryHealth` 的失败计数，不分状态码）。而 D-3 的 `CallStats` 只接了三个 **OKX** 客户端，不接 LLM 客户端。于是压掉 `httpx` 之后，LLM 提供商的状态码分布**同时失去日志与统计两个来源**，而轴 E 第 4 行正要读它 | `app/ai_auto/decider.py:1191-1193`（`summary_health_wire` 只给失败计数）；§八 D-3 的接线清单 | D-3 的接线点从 3 个扩到 **5 个**（补 `agent/llm/anthropic_client` 与 `openai_client` 的响应出口，键归一为 `(method, host)` 而非 path，避免把不同厂商的路径混成一格）；D-2 的「信息损失评估」改为**如实的分项结论**；新增门禁 G-D8 |
| **P1-9** | §七 C-2 | **两处形状错误。** ①消费点清单漏了第 5 处：`api/trading.py:104` 的账户面板 `GET /positions`（用户可见的只读面板，必须显式登记为「不走缓存」，否则下一轮有人会「顺手」接上去）；②`_read_positions` 在今天**是一个属性**而不是方法（`executor.py:157 self._read_positions = positions_fn or self._default_read_positions`），v1 写「增两个方法」，照写会与构造器里的属性赋值打架 | `app/api/trading.py:104`；`app/live_auto/executor.py:91,157` | C-2 补第 5 行并标注「**不缓存**，且不得在未来接入」；接线形状改写为「**两个属性**，由同一个 `positions_fn` 派生两条规则」，并写死注入替身必须同时覆盖二者 |
| **P1-10** | §七 C-3 | **`invalidate(fp)` 的指纹来源未规定，而 C-1 自己把「指纹漂移」列为本轴唯一有资金后果的实现错误。** `close_live_position(s, creds, account_id, close)` 一类函数的签名里**没有 `settings`**，而 `fingerprint(api_key, base_url, demo, timeout)` 需要 `okx_rest_base` / `okx_account_demo` / `okx_account_timeout_seconds` 三个都来自 `Settings` 的值 | `app/okx/client_pool.py:40-47`；`app/okx/private_client.py:82-87`；`app/okx/trading.py`（写函数签名） | C-3 增硬性规定：**指纹一律由 `OKXTradeClient._fingerprint()` 现算**（写函数本来就为这次写建了一个 client 实例），**严禁**在 `trading.py` 里重新拼 `get_settings()` 的三个字段；新增门禁 G-C12（AST 断言 `trading.py` 里不出现第二处 `fingerprint(` 调用） |
| **P1-11** | §五 A-2 | **「最坏 10 次往返仍在 `live_auto_start_concurrency` 信号量内」是量纲误用。** 那是一个**并发度**（默认 2），不是次数预算；把次数说成「在信号量内」等于什么都没约束 | `app/config.py:114`；`app/ai_auto/manager.py:470-494` | 改成可验收的表述：阶梯把启动期私有 GET 的**次数**从 N 提到最多 2N；在账户桶 5 次/秒下，N=5 的最坏增量是 **+1 秒量级的排队**，且 A-2 的第 ② 跳只在第 ① 跳明确回 4xx 时才发生（见 P0-3） |
| **P1-12** | §六 B-1 第 2 条 / G-B5 | **「`synthetic` 档下 IP 字面量一律拒」这一条在真实拓扑下不可达。** IP 字面量的 `getaddrinfo` 返回它自己，`classify_resolution` 恒得 `public`，永远进不了 `synthetic` 分支；G-B5 只能靠注入一个不存在的解析器来构造 | `app/ai_auto/intel/reader.py:144-163`（解析走 `getaddrinfo(host, …)`）；`guard_url` 对 IP 字面量的既有分支 `reader.py:133-134` | 保留该条但**改写它的理由与用例形状**：它守的不是「正常拓扑」，而是「解析器被污染 / 未来引入自定义 resolver」这一类；G-B5 显式改为注入解析替身的白盒用例，并在断言里写清它守的是什么 |

### P2（登记，本轮不改）

| 编号 | 事项 | 说明 |
|---|---|---|
| P2-1 | 锚点漂移 | v1 的行号按 `8451163` 读出，但工作树里 `api/ai_intel.py` 有一处**未提交**的 204 修复（+10 行），使 `278-287` 实际落在 `283-292`；`session.py:1170` 实际为 `1159`；`executor.py:648/671` 实际为 `645/655`。实施节点应以**符号名**（`_read_exchange_position` / `_confirm_net_cleared` / `reconcile`）定位，不要按行号 |
| P2-2 | 第 13 把闸未登记 | 轴 A 实际还受一把**既有**总闸节制：`ai_trade_fee_rate_source_enabled`（`config.py:569`，`_bounded_fee_rate` 的第一行早退）。它不是新键，但 §十三 应在表下写一行「本轴另受既有 X 节制」，否则「关掉 12 把闸即逐字节回退」这句话在读者那里少了一个前提 |
| P2-3 | `PositionsSnapshot.rows` 的容器类型 | v1 写 `tuple[dict, ...]`，而今天 `_read_positions()` 的产物是 `list[dict]`，全部替身也返回 list。不可变是对的，但应在 `get()` 出口 `list(...)` 一次再交给 executor，避免类型注解与 30+ 条替身用例打架 |
| P2-4 | 附录 C 是空占位 | `probe_trade_fee` 的真实输出要到实施节点才有。这是**正确**的做法（不许先编一段），此处只登记：**空的附录 C 是本 spec 唯一一处允许在合入前仍为空的地方**，其余任何「待填」都视为未完成 |
| P2-5 | §14.4 第 6 步与 §16.1 的顺序 | 真机验收第 6 步（老包→新包续跑）要求整轮装好，而 §16.1 让轴 C 单独成一个提交。不矛盾（验收在整轮合入后做），但应在 §14.4 开头写一句「本节在**全部八步合入后**一次性执行」 |
| P2-6 | `zeroOkStreak` 的定义边界 | 「最近连续多少轮 `reader_attempt>0` 且 `reader_ok==0`」需要明确：`reader_attempt==0` 的轮次是**中断连续**还是**跳过不计**。本文选「跳过不计」（一轮没有候选不该洗掉一条真实的连续失败），已写进 B-2 正文 |
| P2-7 | 兄弟 spec 的边界 | §三 N-1 与 §十八 的依赖论证是本文最强的部分之一，评审**完全同意**：轴 A 是 `ai-trade-edge-closed-loop-governor` 轴 A 的前置条件。建议把这条依赖也写进那份 spec 的开头，否则两份文档只有一侧知道 |

### 评审确认为「不是缺陷、不要改」的四条

写下来，防止下一轮有人「顺手」动它们：

1. **§三 N-2 不放松 `unsourced_number`** —— 完全正确。49/58 的拒因是它，而它是对的。放松它就是把「有引证」这件事贬值，且会让 B-1 修好之后的真实改善**无法被测量**。
2. **§六 B-3 不给 reader 加「抓不到就用摘要凑数」的回落** —— 完全正确，而且这条应该被抄进兄弟 spec。
3. **§一 E-10 的两条只记录不改** —— `lifecycle mismatch` 是有意设计、`_ProactorBasePipeTransport` 是 Windows 已知噪声，判定正确。
4. **§七 C-4 与 C-3 的冗余是有意的** —— 「C-3 是记得调用的纪律，C-4 是忘了调用也不会出事的构造性保证」。这是本文写得最好的一段设计论证，不许以「重复」为由删掉任何一半。

---

## 零、先读这一段：本轮的结论

**一句话结论：这个系统里每一条「向外界要真话」的通路，在这台真机上都在静默地返回空——而且每一条的失败都只写进了一行 `INFO` 日志，于是它们已经这样跑了三个星期，没有任何一处界面告诉过用户。**

「静默降级」不是修辞。四条通路，四个 100%：

| # | 真值通路 | 它本该回答的问题 | 真机实测 | 失败的可见性 |
|---|---|---|---|---|
| 1 | 交易所官方费率 `GET /account/trade-fee` | 「这一笔往返到底要付多少手续费」 | **1,524 次请求，1,524 次 HTTP 400，0 次成功**（自 2026-08-11 起） | 一行 `INFO`：`trade-fee prefetch failed: [CONNECTION_FAILED] OKX HTTP 400` |
| 2 | 情报正文取证 `intel.reader` | 「这条情报的原文到底怎么写的」 | **96 次抓取，`reader_ok = 0`，`ai_intel_documents` 29 行全部 `status='blocked'`、`byte_len=0`** | 一行 `INFO`：`intel reader blocked coindesk.com (private_host)` |
| 3 | 账户私有配额 | 「我还有多少额度可以用来下单」 | **`GET /account/positions` 占全部出网请求的 78.3%**（63.5 次/分），5 个成员各自重复拉同一份账户级快照；累计 1,318,294 次 200、**1,010 次 429** | 无。429 被指数退避静默吞掉 |
| 4 | 错误自身 | 「交易所到底说了什么」 | 非 429 的 4xx/5xx 上，OKX 回执体里的 `code` / `msg` **被整个丢弃**，只留下 `OKX HTTP 400` | —— 这正是第 1 条能藏三个星期的原因 |

而让这四条同时看不见的那件元事实是：

> **桌面端日志 254.7 MB、单文件、无轮转，其中 90.9% 的字节（85.7% 的行）是 `httpx` 的 `INFO`，仅 `GET /account/positions` 一行就占了全部日志行的 61.0%。** 用户被要求「看看桌面端的日志」，而这份日志里每 100 行有 61 行是同一个后台轮询。

### 三条必须写在最前面的反直觉结论

> **反直觉 ①：「情报模块没配好」是错的——它配好了、在跑、在花钱，只是永远采不到东西。**
> `app_settings` 里 `ai_intel.provider = "tavily"` 与 API key 都在，16 次采集轮真的发出了 Tavily 请求、真的调了两次 LLM（累计 **63,835 输入 + 16,452 输出 token**），拿回 256 篇候选文档、235 个域名——然后 **0 篇正文抓成功**，80 条引证提案里 **49 条以 `unsourced_number` 被拒**，7/16 轮以 `degrade_code='all_rejected'` 收场。它不是没启动，它是**每一轮都完整地跑完、然后什么都不采纳**。谁要是把本轮做成「教用户去设置里配一下情报源」，就会在一个已经配好的系统上写第二句假话（第 1 轮已经修过一次同形的假话，见 `422c5d8`）。

> **反直觉 ②：「私有接口撞 429 是因为没限速」是错的——令牌桶三周前就装好了，而且生效着。**
> `okx/rate_limit.py` 的 `TokenBucket` 由 `private_client._acquire_slot()` 在**每一跳**取令牌（`private_client.py:151-154`），按账户 5 次/秒、桶容量 5（`config.py:74-75`），`trade_client` 继承同一条缝。真正的问题不是闸没装，而是**闸后面排队的东西有 80% 是重复的**：`fetch_live_positions(creds)` 返回的是**全账户**持仓（`trading.py:520`，签名里根本没有 `instId`），而 5 个成员的盯市循环各自每 5 秒去拉一次**完全相同**的那一份。修法因此不是「把闸调紧」——调紧只会让真正的下单等得更久——而是**把 5 份一模一样的读数合成 1 份**。

> **反直觉 ③：「`private_host` 说明有人在往内网发请求」是错的——被拦下的是 coindesk、coinbase、tradingview 和交易所自己。**
> 本机实测：`coindesk.com → 198.18.0.194`、`coinbase.com → 198.18.0.195`、`binance.com → 198.18.0.196`、`tradingview.com → 198.18.0.197`、**`www.okx.com → 198.18.0.198`**。这是 fake-IP 模式透明代理（Clash / sing-box / Surge 一类，本项目目标用户的标准配置：LLM 走 `open.bigmodel.cn` 与 `api.kimi.com`，行情走 `okx.com`）分配的合成地址段 `198.18.0.0/15`；而 Python 的 `ipaddress.ip_address('198.18.0.194').is_private` **为 `True`**（RFC 2544 基准测试段被计入私有段）。于是 `reader.resolve_is_public` 的「解析出的每一个地址都必须是公网地址」在这类机器上对**每一个域名**都判假。这道闸不是配错了，是它的判据在这类网络拓扑下**结构性地**恒为假。

### 本轮做什么

五条轴，全部围绕同一句话：**把静默降级变成有声降级，再把能修的降级修好。**

- **轴 A｜先让交易所把话说完，再把官方费率真的取回来。** ①4xx/5xx 上把 OKX 回执里的 `code`/`msg` 带进异常（这是本轮唯一一条「必须最先做」的改动，它让 A-2 的假设可以被生产数据当场证实或证伪）；②`trade-fee` 改走**参数阶梯**（`instFamily` → `instType` 通用档 → `None`），无论我的参数假设对不对都能收敛；③一个永久性的参数错不再被记成「链路故障」；④费率来源从历史详情里那张会自我隐藏的卡片，提到**启动弹窗与运行中成本卡**上。
- **轴 B｜让情报的 SSRF 闸在 fake-IP 网络下仍然既安全又有用。** 把「解析结果」三分为 `public / private / synthetic`，`synthetic` 不再等同于 `private`，而是降级为「域名闸 + 出网后对端地址复核」；`strict` 档逐字保留今天的行为。同时把 `reader_attempt / reader_ok` 这对**有逐轮读点、却从不聚合成结论、从不跨轮判趋势、从不告警**的列做成一个结论（【评审 P1-7】：v1 此处写的是「没有任何读点」，那句话可以被 `IntelRoundsTab.tsx:172-173` 一眼证伪），并对「29 篇缓存 / 0 字节正文」这种定义上不可能的组合直接说人话。
- **轴 C｜把账户级持仓快照做成整个进程共享一份。** 新增 `okx/positions_cache.py`（单飞 + TTL + 按凭证指纹分桶），把 63.5 次/分压到 12.7 次/分；**三个消费点按语义分级**：盯市 `reconcile` 用缓存，反手前的净仓确认与暂停时的持仓真值读**强制穿透**；任何一次私有写成功即作废该账户的快照。
- **轴 D｜日志治理 + 调用统计。** 桌面端写入改为带上限的轮转（默认 16 MB × 3 份，零依赖）；`httpx` / `httpcore` 压到 `WARNING`；同轮补一份进程内**按端点的调用与错误计数**，把「只有翻日志才知道」变成「端点上能读到」——否则压掉日志等于把本轮 C 轴的证据来源一起压掉。**该计数必须同时覆盖 OKX 三个客户端与 LLM 两个客户端**（【评审 P1-8】：`bigmodel · 529×779`、`kimi · 403×408` 这几组读数今天**只**来自 `httpx` 的 `INFO` 行，压掉之后 LLM 侧会同时失去日志与统计两个来源）。
- **轴 E｜一张「真值源」卡片。** 在设置中心把上面四条通路做成四行可读、可操作的事实（状态灯 + 读数 + 最近一次原因 + 一个可点的补救动作），并复用仓库既有的《工作台文字零折行契约》与无障碍口径。这是「顶级产品」在本轮的落点：**一处显式的事实，胜过四处沉默的正确。**

**十二个新配置项，任一置回退档即对应链路逐字节回到 `8451163`。**

---

## 一、勘察证据

> 口径说明：日志读数取自 `%APPDATA%/aragon-desktop/logs/backend.log` **全文**（不是尾部采样）；库读数取自 `%APPDATA%/aragon-desktop/aragon.db` 只读连接。复算脚本见 §附录 A。
>
> **⚠️ 这台机器上的会话正在跑，日志与库都在增长。** 本节所有绝对数是 2026-09-02 05:20–06:00 之间的读数；重跑 `evidence/audit.py` 会得到**更大的绝对数与几乎相同的比例**。已核对的一组对照（相隔 40 分钟）：文件 254,662,610 → 254,911,767 字节、总行数 2,163,636 → 2,165,661、`httpx` 占行 85.7% → **85.7%**、占字节 90.9% → **90.9%**、`positions` 占全部行 61.0% → **61.0%**、`trade-fee` 400 次数 1,524 → 1,529 而 **200 次数恒为 0**。**比例是稳态，不是采样噪声**——这正是本轮把它们当作设计依据的理由。

### E-1｜日志构成：61.0% 的行是同一个后台轮询

| 读数 | 值 |
|---|---|
| 文件大小 | **254,662,610 字节（254.7 MB）**，单文件，无轮转 |
| 总行数 | **2,163,636** |
| `INFO` / `WARNING` / `ERROR` | 1,855,449 / 6,540 / **220** |
| `httpx` logger 行数 | **1,854,782（85.7%）** |
| `httpx` logger 字节数 | **231,375,581（90.9%）** |
| 其中 `GET /api/v5/account/positions` | **1,319,400 行 = 全部日志行的 61.0%** |
| 非 `httpx` 的全部 logger 合计 | 308,854 行（`aragon.okx.ws` 2,993 / `aragon` 2,241 / `app.ai_auto.decider` 1,452 / `aragon.security` 236 / `asyncio` 219 / `app.ai_auto.manager` 141 / `app.ai_auto.session` 48 / 其余 < 40） |

写入点：`desktop/src/main.js:47`

```js
const open = (name) => fs.createWriteStream(path.join(dir, `${name}.log`), { flags: 'a' });
```

纯追加，无大小上限、无份数上限、无启动清理。级别来源：`services/quant/app/main.py:58` 的 `logging.basicConfig(level=logging.INFO, ...)` —— `httpx` 的请求日志正是 `INFO`，从未被单独压低。

### E-2｜出网构成：78.3% 的请求是同一份账户级持仓快照

2026-09-02 04:00–05:10（70 分钟，会话正在运行）按端点统计：

| 端点 | 次数 | 次/分 | 占比 |
|---|---:|---:|---:|
| `GET /api/v5/account/positions` | **4,447** | **63.5** | **78.3%** |
| `GET /api/v5/market/candles` | 536 | 7.7 | 9.4% |
| `GET /api/v5/public/funding-rate` | 379 | 5.4 | 6.7% |
| `POST open.bigmodel.cn/.../messages` | 203 | 2.9 | 3.6% |
| `GET /api/v5/account/positions-history` | 80 | 1.1 | 1.4% |
| `POST api.tavily.com/search` | 20 | 0.3 | 0.4% |
| `GET /api/v5/account/trade-fee` | 10 | 0.1 | 0.2% |
| `GET /api/v5/public/time` | 6 | 0.1 | 0.1% |
| **合计** | **5,681** | **81.2** | 100% |

63.5 次/分 = **每 0.95 秒一次**。分组有 5 个成员（`BTC/ETH/SOL/DOGE/SATS-USDT-SWAP`，run 1625–1629，同一个 `aitradegrp_0db79288…`），盯市周期 `live_auto_poll_interval_s = 5`（`config.py:125`），5 ÷ 5 = 1 次/秒 —— 与实测逐位吻合。

全量累计（整份日志）：

| 端点 · 状态 | 次数 |
|---|---:|
| `positions` · 200 | 1,318,294 |
| `positions` · **429** | **1,010** |
| `positions` · 401 | 87 |
| `positions` · 502 | 9 |
| `trade-fee` · **400** | **1,524** |
| `trade-fee` · 200 | **0** |
| `positions-history` · 200 | 15,835 |
| `orders-algo-pending` · 200 | 112,470 |
| `orders-pending` · 200 | 50,560 |
| `account/config` · 200 | 23,681 |
| `account/balance` · 200 | 22,884 |
| `trade/order` · POST 200 | 553 |
| `trade/close-position` · POST 200 | 248 |
| LLM `bigmodel` · 200 / 529 / 429 | 45,836 / 779 / 108 |
| LLM `api.kimi.com` · 200 / **403** | 1,147 / **408** |

### E-3｜`trade-fee`：1,524 次请求，0 次成功，同一凭证下 `positions-history` 全绿

实际发出的请求（日志原文，第一次出现于 2026-08-11 23:46:00）：

```
GET https://www.okx.com/api/v5/account/trade-fee?instType=SWAP&instId=BTC-USDT-SWAP  "HTTP/1.1 400 Bad Request"
```

同一账户、同一进程、同一分钟内：

```
GET https://www.okx.com/api/v5/account/positions-history?instType=SWAP&limit=100&instId=BTC-USDT-SWAP  "HTTP/1.1 200 OK"
```

**这一对读数把「凭证 / 权限 / 网络 / 时钟」四类原因全部排除**：同样的签名、同样的 `instType=SWAP&instId=<...>-USDT-SWAP` 形状，一个端点 200、另一个端点 400。剩下的唯一可能是**这一个端点的参数契约不同**。

调用链：`manager._gather_context._bounded_fee_rate`（`manager.py:477-489`）→ `okx.trading.fetch_trade_fee`（`trading.py:572`）→ `OKXTradeClient.get_trade_fee`（`trade_client.py:283-301`）：

```python
params: dict[str, str] = {"instType": inst_type}
if inst_id:
    params["instId"] = inst_id
rows = await self._get("/api/v5/account/trade-fee", params)
```

其 docstring 写着「`inst_id`: 合约 ID（可选；不传即取该 instType 的账户通用档位）」——这是设计当时对 OKX 参数表的一个假设，而**这个假设从上线那天起就没有被任何一次成功响应验证过**。

后果链：`fetch_trade_fee` 恒抛 → `_bounded_fee_rate` 恒返回 `None` → `fee_rates.snapshot_from_wire(None, …)` 恒返回 `None` → `FeeRateSnapshot.source` 恒为 `"config"` → **成本模型、`edge` 门禁的成本项、净额盈亏的扣减，三周来全部跑在本地保守常数上，而不是这个账户真实的费率档位**。上一轮已经量到「手续费占该会话亏损的 35%–100%，两个会话的毛利被手续费完全吃光」——本条正是那笔账**永远无法被校准**的原因。

### E-4｜4xx 上，交易所自己的解释被整个丢弃

`private_client.py:244-249`（GET）与 `trade_client.py:74-79`（POST）**两处同形**：

```python
try:
    resp.raise_for_status()
except httpx.HTTPStatusError as exc:      # 非 429 的 4xx/5xx：语义不变，立即上抛
    self._note_link_fail("OKX_HTTP_ERROR", f"{path}: HTTP {exc.response.status_code}")
    raise OKXPrivateError("OKX_HTTP_ERROR",
                          f"OKX HTTP {exc.response.status_code}") from exc
```

对比紧随其后的**业务码**分支（`private_client.py:251-254`）——那里是把 `body["code"]` 与 `body["msg"]` 原样带上的。也就是说：**OKX 在 HTTP 200 里说的话我们听，在 HTTP 400 里说的话我们不听**，而 400 恰恰是「你的参数不对，具体是这个」的那一类。1,524 次 400 里，每一次的响应体都写着为什么，我们一次都没读过。

附带后果：`_note_link_fail("OKX_HTTP_ERROR", …)` 把一个**永久性的参数错**记进私有链路健康登记簿（`okx/link_state.py:120`），于是每次启动这条链路都要凭空吃 5 次「失败」，`consecutive_failures` 与 `last_error_code` 因此对真正的链路故障失去分辨力。

### E-5｜情报正文取证：96 次尝试，0 次成功

> **读数时刻：2026-09-02 05:20（分组仍在运行，样本会继续增长）。** 本节末尾附了 40 分钟后的第二次读数——两次读数之间 `reader_attempt` 从 96 涨到 102，而 `reader_ok` **仍然是 0**。这条曲线本身就是证据：这不是一次偶发失败，是一个稳态。

`ai_intel_rounds` 中 `kind='collect'` 的 **16** 轮聚合：

| 读数 | 值 |
|---|---:|
| 候选文档 `doc_count` | 256 |
| 候选域名 `domain_count` | 235 |
| 正文抓取尝试 `reader_attempt` | **96** |
| 正文抓取成功 `reader_ok` | **0** |
| 引证提案 `quotes_proposed` | 80 |
| 引证核验通过 `quotes_verified` | 12 |
| 采纳 `accepted` / 拒绝 `rejected` | 12 / 58 |
| 拒因 `rejects_json` | **`unsourced_number` 49** · `too_long` 5 · `too_short` 3 · `imperative_text` 1 |
| `degrade_code = 'all_rejected'` | **7 / 16 轮** |
| LLM token（输入 / 输出） | 63,835 / 16,452 |

`ai_intel_documents` 共 29 行，**29 行 `status='blocked'`、`body=''`、`byte_len=0`、`http_status=0`**——没有一行是抓下来的正文。

`ai_intel_quotes` 共 12 行，**12 行 `source_field='snippet'`**——每一条「逐字原文引证」引的都是搜索引擎摘要，没有一条引自被抓回来的正文。这把黑板上那条早已登记、当时只是推断的 P1（`a9401532`：「整个情报栈唯一的『事实』就是一段搜索摘要」）**升级为生产实证，并给出了它的成因**。

日志侧（本次重启后 2026-09-02 01:17–05:01）共 29 行 `intel reader` 记录，**29 行全部是 `blocked (private_host)`**，覆盖 26 个互不相同的公开域名：`coindesk.com`、`coinbase.com`、`binance.com`、`tradingview.com`、`theblock.co`、`defillama.com`、`forbes.com`、`yahoo.com`、`bitcoinmagazine.com`、`cryptorank.io`、`glassnode.com`、`robinhood.com` ……

**第二次读数（2026-09-02 06:00，同一份库，`evidence/audit.py db`）**：

| 读数 | 05:20 | 06:00 | 变化 |
|---|---:|---:|---|
| `collect` 轮数 | 16 | **17** | +1 |
| `reader_attempt` | 96 | **102** | +6 |
| `reader_ok` | 0 | **0** | **不变** |
| `ai_intel_documents` 行数 / 全部 `blocked` | 29 / 29 | **30 / 30** | +1，仍是 100% |
| `quotes` 中 `source_field='snippet'` | 12 / 12 | **12 / 12** | 不变（40 分钟里没有产生任何新引证） |
| `degrade_code='all_rejected'` | 7 | **8** | +1 |
| `unsourced_number` 拒因 | 49 | **52** | +3 |
| LLM token（输入 / 输出） | 63,835 / 16,452 | **67,918 / 17,374** | **+4,083 / +922 —— 40 分钟又烧掉这些 token，采纳 0 条** |

**这是本轮最该被记住的一张表**：一个功能完整、配置正确、每 20 分钟准时跑一轮、持续消耗 token 的子系统，它的产出率是恒零，而它自己不知道，用户也不知道。

### E-6｜根因实测：本机每一个域名都解析进 `198.18.0.0/15`

```
coindesk.com    -> ['198.18.0.194']
coinbase.com    -> ['198.18.0.195']
binance.com     -> ['198.18.0.196']
tradingview.com -> ['198.18.0.197']
www.okx.com     -> ['198.18.0.198']
theblock.co     -> ['198.18.0.199']
yahoo.com       -> ['198.18.0.200']

>>> ipaddress.ip_address('198.18.0.194').is_private
True
```

判定点 `reader.py:144-163` 的 `resolve_is_public`：

```python
infos = await asyncio.to_thread(socket.getaddrinfo, host, None, 0, socket.SOCK_STREAM)
...
return bool(addresses) and all(_is_public_ip(addr) for addr in addresses)
```

`_is_public_ip`（`reader.py:101-112`）对 `ip.is_private` 直接判假。函数自己的 docstring 写着「有代理时这一段是『尽力而为』（真正的解析发生在代理侧），但**照常执行**——它仍拦得住直接写死内网域名的那一类，而少跑一次的收益是零」。**这段自陈在事实上是错的**：它不是「尽力而为」，它是 fail-closed 且因此是全称的——在 fake-IP 拓扑下，少跑一次的收益不是零，是「这个功能到底能不能用」。而且注释里的判据是「有没有配代理」，本机 `%APPDATA%/aragon-desktop/agent.env` 与进程环境里**一个代理键都没有**（透明 TUN 不需要），所以任何以「配了代理才放宽」为形状的修法在这台真机上一次都不会生效。

### E-7｜情报告警台账：16 条，`fired = 0`

| `suppressed_code` | 条数 |
|---|---:|
| `impact_below_bar` | 8 |
| `no_candidate` | 7 |
| `category_out` | 1 |
| **`fired = 1`** | **0** |

（06:00 的第二次读数：17 条，`impact_below_bar` 8 / `no_candidate` **8** / `category_out` 1，**`fired` 仍为 0**。）

9 条有候选的记录里，**8 条 `domain_count = 1`**（告警门禁要求「至少两个独立来源」），`confidence` 分布 0.15 / 0.2 / 0.25 / 0.4。也就是说：`d17eed6` 那一轮做的「够格的高危情报当场多问一次 AI」这条通路，**上线至今一次都没有开火**，而它开不了火的上游正是 E-5：没有正文 ⇒ 引证只能来自单一搜索摘要 ⇒ 独立来源数恒为 1。

### E-8｜决策链的失败：只在日志里

| 签名 | 次数 | 最近一次 |
|---|---:|---|
| `ai-trade summarize failed: TimeoutError(30s)` | 94 | **2026-09-02 04:44:16**（进行中） |
| `ai-trade decide attempt failed: LLM_NETWORK_ERROR` | 91 | 2026-09-01 16:45:22 |
| `ai-trade decide attempt failed: peer closed connection …` | 12 | 2026-09-01 12:16:20 |
| `ai-trade summarize failed: RuntimeError: [1301][内容安全]` | 1 | 2026-08-31 23:02:30 |
| LLM `529 Server Error` / `429` | 779 / 108 | 2026-09-02 03:50:12 |
| `api.kimi.com` **403 Forbidden** | 408 | 2026-07-08 |

`summarize` 侧已有 `summaryHealth` wire（`decider.py:1191`）与退避，属于**已可观测**；`decide` 侧的失败与 LLM 的 529/403 则没有任何界面落点。本轮把它并进轴 E 的第 4 行（只做读数，不改任何重试策略）。

### E-9｜已确认修好、本轮不再动的两条

- `ai-trade intel wiring skipped for …: 'AsyncSession' object is not callable`：最后一次出现 **2026-08-31 09:43:15**；`422c5d8` 已把 `sessionmaker=get_sessionmaker`（函数本身）修正，`manager.py:1560-1568` 的注释完整记述了这条根因。**2026-09-02 01:16 重启之后不再出现** ⇒ 判定已修复，本轮登记为 closed，不重复投入。
- `origin rejected: 'http://localhost:18762'` 54 次，全部集中在 2026-08-30，之后未再出现。属兄弟 spec `ai-trade-edge-closed-loop-governor` 轴 E-2 的范围，本轮**不接手**（避免两份 spec 同时改 `security/origins.py`）。

### E-10｜两条只记录、本轮不改的观察

- `ai-trade lifecycle mismatch: group=… declared=running derived=paused` 出现 2 次（08-31 19:44、09-01 19:38）。`manager.py:1317-1330` 的 docstring 明确写了「**只在迁移瞬间记一次**，且是 `warning` 不是 `error`」——这是**有意**的设计，不是缺陷。登记为 P2 观察项。
- `asyncio: Exception in callback _ProactorBasePipeTransport._call_connection_lost` 220 次中的 213 次。Windows `ProactorEventLoop` 在连接被对端重置时的已知噪声，无功能后果。轴 D 压低 `asyncio` 到 `WARNING` **不做**——它已经是 `ERROR`，压不掉，且真正的修法（换事件循环）远超本轮范围。

### E-11｜交易侧现状（与第 1 轮同一份数据，此处只做交叉核对）

`mode='ai-trade'` 共 211 条 run。当前分组 `aitradegrp_0db79288…`（run 1625–1629）：

| run | 标的 | `roundsCompleted` | `tradeCount` |
|---|---|---:|---:|
| 1625 | BTC-USDT-SWAP | 536 | 10 |
| 1626 | ETH-USDT-SWAP | 476 | 4 |
| 1627 | SOL-USDT-SWAP | 484 | 4 |
| 1628 | DOGE-USDT-SWAP | 473 | **0** |
| 1629 | SATS-USDT-SWAP | 456 | **0** |

第 1 轮的活性看门狗（`8451163`）正是针对最后两行；本轮不重复该轴。

### E-12｜`app_settings` 全表 10 行，`ai_prior:%` **0 行**

| key | 说明 |
|---|---|
| `ai_intel.provider` / `ai_intel.search_api_key` | 情报检索源，已配置 |
| `ai_intel.docs_pruned_ts` | 文档清理水位 |
| `experience.*`（6 行） | 经验图谱 |
| `workspace.watchlist_prefs` | 自选 |

**`ai_prior:%` 命中 0 行** —— 黑板 `EC-2` 那条「EV 分子通路上线至今没有水源」在本轮**复核为仍然成立**。该项属兄弟 spec `ai-trade-edge-closed-loop-governor` 轴 A，本轮**明确不接手**（§三·非目标 N-1），此处只做交叉登记。

### E-13｜费率来源在界面上的唯一落点，是一张会自我隐藏的卡片

`describeFeeRates`（`apps/web/lib/aiTradeCost.ts:117-125`）能把 `source` 渲染成「交易所官方档位 Lv1」或「**本地保守常数**」——但它唯一的调用点在 `AiTradeStatsDetail.tsx:291-297` 的 `ReconcileCard` 里，而该卡片开头就是：

```tsx
const view = describeReconcile(rec);
if (!view) return null;      // 老记录缺键 ⇒ 整段隐藏
```

于是：**运行中的会话看不到费率来源，启动前看不到，只有在历史详情里、且那条历史刚好有对账读数时才看得到**。一个能说真话的组件，被放在了一间平时不开门的房间里。

### E-14｜情报文档健康卡片上，那对「定义上不可能」的读数

`IntelDocumentHealth`（`apps/web/lib/intelTypes.ts:145-151`）已含 `cachedDocs` 与 `bodyBytes` 两个键，`IntelReaderSettings.tsx` 已经把它们渲染出来。本机当前值是：

```
cachedDocs = 29,  bodyBytes = 0
```

**「缓存了 29 篇文档、正文合计 0 字节」在一个健康的安装上是不可能出现的组合。** 这个事实三个星期来一直显示在设置页上，只是没有任何一处把它翻译成一句人能读懂的结论。轴 B-2 要做的就是这件事——**不是新增一块面板，而是让已经在屏幕上的两个数字自己开口**。

---

## 二、根因

### R-1｜`trade-fee` 的参数形状与 OKX 的端点契约不符（E-3 / E-4）

OKX v5 各私有端点的参数表并不统一：`positions-history` 接受 `instId`，而 `account/trade-fee` 的 `instId` 适用于 `SPOT`/`MARGIN`，`FUTURES`/`SWAP`/`OPTION` 用的是 **`instFamily`**（`BTC-USDT-SWAP` 的 family 是 `BTC-USDT`）。本仓 `get_trade_fee` 对两者用了同一个形状。

**但本轮不把设计押在这一个假设上。** 因为 E-4 的存在，这个假设三周来无法被证实也无法被证伪；所以轴 A 的第一步是让交易所把 `code`/`msg` 说出来（A-1），第二步才是按阶梯重试（A-2），第三步是把阶梯的结果如实写进 `FeeRateSnapshot.source`（A-4）。**无论 R-1 对不对，A-1 + A-2 的组合都会在下一次生产运行里收敛到正确的参数形状**，且失败路径逐字回到今天。

### R-2｜SSRF 解析闸的判据在 fake-IP 拓扑下结构性恒假（E-5 / E-6）

`resolve_is_public` 把「本机 `getaddrinfo` 的结果是不是公网地址」当作「这个主机能不能访问」的判据。在透明 TUN / fake-IP 代理下，本机解析出来的地址**根本不是**将要连接的真实地址——它是代理分配的一个占位符，且必然落在保留段里。判据与被判定的事实之间没有因果关系，于是闸退化成一个恒为「拒绝」的常量。

**这条根因有一个更一般的形状，值得写进评审结论：一道安全闸如果 fail-closed，那么它的判据一旦在某类环境里恒假，这个功能在该类环境里就是零可用——而「零可用」和「很安全」在日志里长得一模一样。** 本轮的修法因此不是放松闸，而是**换一个与真实连接有因果关系的判据**（连接建立后的对端地址），并把 DNS 预判从「否决权」降为「三分类里的一档」。

### R-3｜账户级读数被按成员重复了 N 遍（E-2）

`fetch_live_positions(creds)` 的签名里没有 `instId`（`trading.py:520`），返回全账户持仓；`LiveExecutor._default_read_positions`（`executor.py:175-176`）直接转发；每个 `AiTradeSession._monitor_loop` 每 `live_auto_poll_interval_s` 秒调一次 `_monitor_tick` → `executor.reconcile` → `_read_positions`。成员数 N ⇒ 请求量 N 倍，而信息量 1 倍。

**这不只是浪费。** 令牌桶是按账户的（`private:{fingerprint}`，5 次/秒），所以一次真实下单在最坏情况下要排在若干份重复快照后面才拿到令牌——**背景轮询与下单竞争的是同一个预算，而下单是有时效的那一个**。

### R-4｜四条降级的可见性等级，全部低于它们的后果等级（E-1 / E-3 / E-5 / E-13 / E-14）

- 费率取不到 ⇒ `logger.info`（`manager.py:488`）
- 正文抓不到 ⇒ `logger.info`（`intel/reader.py` 调用侧）
- 配额被烧 ⇒ 无任何记录，429 被指数退避吞掉
- 交易所的解释 ⇒ 被丢弃

而它们的后果分别是：成本模型失真、情报层空转并持续烧 token、下单排队、诊断成本无穷大。**「后果是钱，记录是 INFO」这条错配本身就是本轮要修的缺陷**，而不只是修复过程中的背景。

### R-5｜日志的信噪比让「看日志」这个动作本身失效（E-1）

用户被要求「看看桌面端的日志」。这份日志里 61.0% 的行是同一个后台轮询、90.9% 的字节是 `httpx` 的 `INFO`。**在这样的信噪比下，上面四条根因中的任何一条都不可能被人眼发现**——本轮能找到它们，靠的是对 216 万行做程序化聚合。修好 D 轴之后，同样的四条根因在日志里会是显眼的少数行。

---

## 三、目标与非目标

### 3.1 目标

| 编号 | 目标 | 可验收判据 |
|---|---|---|
| O-1 | 交易所在 4xx/5xx 上说的话进入异常与日志 | `OKXPrivateError.message` 含 `code` 与截断后的 `msg`；解析不出即逐字回到 `OKX HTTP {status}` |
| O-2 | 官方费率在真机上取得成功，或**如实说明为什么没取到** | `FeeRateSnapshot.source ∈ {exchange:family, exchange:type, config}`，且 `config` 时携带最近一次失败的 `code`/`msg` |
| O-3 | 情报正文在 fake-IP 网络下能被抓回来，且不降低 SSRF 防护强度 | `reader_ok > 0`；`private_host` 仍拦得住 `localhost` / 私有后缀 / IP 字面量 / 真实私有对端 |
| O-4 | 账户私有 REST 请求量下降 ≥ 4 倍，且不改变任何交易语义 | `positions` 调用次/分 ≤ 16；反手确认与暂停读仓两条路径**强制穿透**缓存 |
| O-5 | 桌面端日志可长期运行，且「看日志」重新可行 | 单文件 ≤ 16 MB、保留 3 份；`httpx` 行占比 < 5% |
| O-6 | 四条真值通路的健康在界面上可读、可操作 | 设置中心「真值源」卡片四行，每行含状态、读数、原因、动作 |
| O-7 | 任一新机制可被单独关闭并逐字节回退 | §十三 的 **12** 个开关（v1 此处写 11，与 §十三 自相矛盾，评审更正），每个配一条「关闭即等价」用例；另加桌面端 2 个环境变量 |

### 3.2 非目标（写清楚，防止下游顺手扩大）

| 编号 | 非目标 | 理由 |
|---|---|---|
| N-1 | **不做**入场 edge / EV 分子水源 / 按标的节流器 | 已有兄弟 spec `docs/plans/ai-trade-edge-closed-loop-governor/spec.md`（v1，未实施）。两份 spec 同时改 `signal_prior` 与 `hold_horizon` 必然互相破坏 |
| N-2 | **不放松**情报的 `unsourced_number` 拒因 | 58 条拒绝里 49 条是它，而它是对的：模型引了没有出处的数字就该被拒。轴 B-1 修好之后它会自然下降；若不下降，那是模型的问题，**不许靠放松闸来掩盖** |
| N-3 | **不改**任何重试 / 退避 / 超时策略 | `summarize` 的 30s、`decide` 的 3 次重试、私有 429 的指数退避一律不动。本轮只做可见性 |
| N-4 | **不改** `security/origins.py` | 属兄弟 spec 轴 E-2，且现象已两天未复现（E-9） |
| N-5 | **不改** `lifecycle mismatch` 的日志语义 | 那是有意设计（E-10），改成 `error` 会制造一条会被忽略的告警 |
| N-6 | **不新增任何 LLM 车道** | 本轮五条轴全部是确定性纯函数 + IO 形状调整 |
| N-7 | **不做** DB 迁移 | 本轮零 `ALTER TABLE`、零新表。`ai_intel_rounds.reader_attempt/reader_ok` 已存在，只是没有读点 |

---

## 四、总体设计

### 4.0 五条贯穿全轴的原则

1. **每一个新机制都有一个关得掉的取值**，且关掉之后对应链路**逐字节**回到 `8451163`。
2. **判据必须与被判定的事实有因果关系。** R-2 的教训：DNS 预判与「能不能连上」在 TUN 下没有因果关系，所以它不配拥有否决权。
3. **失败必须比成功更响。** 本轮所有新落点都遵循：成功走 `debug`/无日志，降级走 `warning` + wire 键 + 界面行。
4. **纯核心与 IO 分离。** 新增的四个判定（HTTP 错误描述、instFamily 推导、解析三分类、日志轮转决策）全部是纯函数，各自独立可测，不碰 IO。
5. **绝不为了让读数好看而改变交易行为。** 轴 C 是唯一触碰真实交易路径的轴，它的三个消费点被逐个分级，且两个「有交易后果」的点**强制穿透缓存**。

### 4.1 架构位置

```
                        ┌───────────────────────────────────────────┐
   轴 D ──────────────► │  desktop/src/lib/rotating-log.js (新)     │  日志轮转（16MB × 3）
                        │  services/quant/app/main.py:58            │  httpx/httpcore → WARNING
                        └───────────────────────────────────────────┘
                                          │
   ┌──────────────────────────────────────┼──────────────────────────────────────┐
   │                                      ▼                                      │
   │   okx/error_http.py (新, 纯)  ◄── 轴 A-1 ──►  private_client._get  (:244)   │
   │        describe_http_error()                  trade_client._post   (:74)    │
   │                                                                             │
   │   okx/inst_family.py (新, 纯) ◄── 轴 A-2 ──►  trade_client.get_trade_fee    │
   │        inst_family_of()                        (:283) 参数阶梯 3 跳         │
   │                                                                             │
   │   okx/positions_cache.py(新) ◄── 轴 C  ──►    trading.fetch_live_positions  │
   │        单飞 + TTL + 指纹分桶                    (:520) / executor:176        │
   │                                                                             │
   │   okx/call_stats.py (新)     ◄── 轴 D-3 ──►   private/rest/trade 三个客户端  │
   │        按 (method, path) 计数                                               │
   └─────────────────────────────────────────────────────────────────────────────┘
                                          │
   ┌──────────────────────────────────────┼──────────────────────────────────────┐
   │   ai_auto/intel/netpolicy.py (新, 纯) ◄─ 轴 B-1 ─► intel/reader.py:144/172   │
   │        classify_resolution()                        guard_hop() 三分类       │
   │        is_synthetic_addr()                          + 出网后对端复核          │
   └─────────────────────────────────────────────────────────────────────────────┘
                                          │
                                          ▼
   ┌─────────────────────────────────────────────────────────────────────────────┐
   │  轴 E  GET /api/system/truth-sources  →  apps/web/components/Settings/       │
   │                                          TruthSourcesCard.tsx (新)           │
   │        四行：费率 / 情报正文 / 私有配额 / 决策模型                            │
   └─────────────────────────────────────────────────────────────────────────────┘
```

### 4.2 一次启动的时序（轴 A + 轴 C 生效后）

```
用户点【启动】
  │
  ├─ manager._gather_context
  │    ├─ _bounded_config × N               （不变）
  │    ├─ _bounded_fee_rate × N ────────────► get_trade_fee 阶梯
  │    │                                        ① instType=SWAP & instFamily=BTC-USDT
  │    │                                        ② 失败 ⇒ instType=SWAP（账户通用档）
  │    │                                        ③ 仍失败 ⇒ None + 记 last_error{code,msg}
  │    ├─ fetch_live_positions ─────────────► positions_cache.get(force=True)   ← 启动必须新鲜
  │    └─ _fetch_blocking_pending           （不变）
  │
  ├─ 启动弹窗回执带上 feeRates{source, makerBps, takerBps, level, lastError}
  │    → 弹窗「成本」段新增一行：「费率来源：交易所官方档位 Lv1（maker 2.0 / taker 5.0 bps）」
  │      或「费率来源：本地保守常数 —— 交易所拒绝：51000 Parameter instId error」
  │
  └─ 每个成员起 _monitor_loop（5s）
       └─ _monitor_tick → executor.reconcile
            └─ _read_positions ──────────► positions_cache.get(ttl=4s)   ← N 个成员共享一份
```

---

## 五、轴 A：先让交易所把话说完，再把官方费率真的取回来

### A-1（前置）｜4xx/5xx 上带出 OKX 的 `code` 与 `msg`

**新增纯模块 `services/quant/app/okx/error_http.py`**（叶子，零仓内依赖，避免 `error_text.py` 的展示域被污染）：

```python
def describe_http_error(status: int, body: bytes | str | None, *, max_msg: int = 200) -> str:
    """把一次非 2xx 的 OKX 响应压成一行可诊断的描述。

    形状（三档，逐档退化）：
      1. 体是 JSON 且含 code/msg  ⇒ "OKX HTTP 400 [51000] Parameter instId error"
      2. 体是可打印文本          ⇒ "OKX HTTP 400 <前 200 字符，去掉换行>"
      3. 体缺席 / 解码失败 / 超长 ⇒ "OKX HTTP 400"     ← 与今天逐字一致

    绝不抛：任何解析异常都退到第 3 档。它跑在异常处理路径上，
    一个会抛的诊断函数会把「参数错」变成「诊断函数自己崩了」。
    """
```

**接线（两处，必须同改）**

| 文件 | 行 | 改法 |
|---|---|---|
| `okx/private_client.py` | 244-249 | `raise OKXPrivateError("OKX_HTTP_ERROR", describe_http_error(exc.response.status_code, exc.response.content))`；`_note_link_fail` 的 message 同步换成该串 |
| `okx/trade_client.py` | 74-79 | 同形（POST 侧） |

**硬性约束**

- **脱敏（【评审 P1-2】修正了 v1 的落点）**：`describe_http_error` 的产物在两个调用点都要再过一次 `_redact(text, *secrets)`。
  v1 写的是「`trading.py` 已有该单点收口」——**那个落点会造成 import 期环**：`_redact` 实际住在 `okx/accounts.py:67`，
  而 `accounts.py:16` 反向 `from .private_client import OKXPrivateClient, OKXPrivateError`。在 `private_client.py` 里
  `from .accounts import _redact` 即循环导入，症状是**整个后端起不来**（本仓刚为一次同形的 import 期崩溃付过代价，
  见 `api/ai_intel.py` 顶部关于 `-> None` 与 204 的注释）。
  **修法**：`_redact` **下沉到本轮新增的叶子模块 `okx/error_http.py`**（它零仓内依赖，正是这类工具的正确宿主），
  `accounts.py` 改成 `from .error_http import _redact  # 单点收口不变，只是搬了家` 并原样 re-export。
  `trading.py:23` 的 `from .accounts import … _redact` **一字不改**（它拿到的仍是同一个函数对象），
  由门禁 G-A13 断言 `accounts._redact is error_http._redact`。
  虽然 OKX 的错误体不含密钥，但「凡是要进日志的外部串都过脱敏」是本仓既有纪律，不在这里破例。
- **`msg` 截断按字符不按字节**（第 1 轮 `8451163` 已为同形问题付过一次代价，见 spec `ai-trade-loss-and-liveness-hardening` E-1）。
- **429 分支一字不动**：`private_client.py:257-261` 有一条显式登记的「有意不对齐」（被 `tests/test_okx_signing.py:95` 逐字冻结）。A-1 只改非 429 的那个分支。
- **【评审 P0-3】同轮给 `OKXPrivateError` 增一个只读加法字段 `http_status: int | None = None`**（`private_client.py:36`）。
  这不是可选项，而是 A-2 能不能成立的前提：今天 `_get` 把「HTTP 400 参数错」与「429 退避耗尽」**都**抛成
  `OKXPrivateError("OKX_HTTP_ERROR", …)`，而这个异常类**只有 `code`/`message` 两个字段**——A-2 的阶梯
  若按 v1 那样以「HTTP 非 2xx」为推进判据，它就是在**一个不携带该信息的入参**上做判定（本仓成文的缺陷形状，
  黑板已为它登记过四次）。
  形状约束三条：
  1. **默认 `None`，位置参数签名一字不动**（`OKXPrivateError(code, message)` 的既有构造点有数十处，
     含 `trading.py` 的六个 `_map_trade_code` 映射点）；
  2. 只在 `private_client._get` / `trade_client._post` 的 **HTTP 状态分支**里填（429 耗尽那条分支也填 `429`，
     它是真的知道自己是 429）；传输层失败分支（`OKX_UNREACHABLE`）**留 `None`**——那里确实没有 HTTP 状态，
     编一个进去就是造第二句假话；
  3. **不进任何 wire**：它是给同进程的调用方做分支用的，不是给前端看的。
- **总闸**：`okx_http_error_detail_enabled: bool = True`。置 `False` ⇒ 两处都回到 `f"OKX HTTP {status}"`，逐字节等价。
  **`http_status` 字段不受该总闸节制**（它不改变任何可观察文案，且 A-2 的回退档另有自己的闸）。

### A-2｜`get_trade_fee` 改为参数阶梯

**新增纯模块 `services/quant/app/okx/inst_family.py`**：

```python
def inst_family_of(inst_id: str) -> str | None:
    """`BTC-USDT-SWAP` → `BTC-USDT`；`BTC-USD-260626` → `BTC-USD`。

    判据是「去掉最后一段」而不是「取前两段」：OKX 的交割合约代码是
    `BASE-QUOTE-YYMMDD`（三段），永续是 `BASE-QUOTE-SWAP`（三段），
    而期权是 `BASE-QUOTE-YYMMDD-STRIKE-C/P`（五段）——取前两段在期权上
    恰好也对，但语义是巧合；去掉最后一段在期权上会错。故：**只对三段代码
    生效，段数 ≠ 3 一律返回 None**（返回 None ⇒ 阶梯直接跳到第 ② 跳）。
    """
```

**改 `trade_client.get_trade_fee`（`trade_client.py:283-301`）**：

```
① params = {"instType": inst_type, "instFamily": family}      family 由 inst_family_of(inst_id) 得
   family is None                    ⇒ 直接跳 ②
   ADVANCE(exc) 为真                  ⇒ 记 attempt① 的 (http_status, code, msg)，跳 ②
   否则（含传输层失败 / 429 / 5xx）    ⇒ **原样上抛，绝不跳 ②**
② params = {"instType": inst_type}                             账户在该产品类型上的通用档
   ADVANCE(exc) 为真                  ⇒ 记 attempt② 的 (http_status, code, msg)，跳 ③
   否则                               ⇒ 原样上抛
③ 返回 FeeFetchOutcome(wire=None, errors=[attempt①, attempt②])
```

**【评审 P0-3】`ADVANCE` 是本轴唯一的新判据，它必须写成一个可单测的纯谓词**：

```python
def should_advance(exc: OKXPrivateError) -> bool:
    """这一跳的失败是不是「参数不对」——只有它才配让阶梯往下走一格。

    真值表（判据取自 `exc.http_status`，见 A-1 的加法字段；**不许解析 message 字符串**）：
      http_status is None            ⇒ False   传输层失败 / OKX_UNREACHABLE：换个参数也连不上
      http_status == 429             ⇒ False   退避已耗尽，再发一跳是在给限频火上浇油
      http_status in (401, 403)      ⇒ False   凭证问题，与参数无关
      400 <= http_status < 500       ⇒ True    「你传错了，具体是这个」——正是阶梯要处理的那一类
      http_status >= 500             ⇒ False   交易所侧故障，重试策略归 `_get` 管，不归阶梯管
    """
```

**为什么必须是「不推进即原样上抛」而不是「不推进即返回空」**：后者会把一次真实的链路故障
静默降级成「费率取不到」，于是 §九 的真值源卡片会在断网时说「交易所拒绝了费率查询」——
一句**指着错误方向**的人话，比不说话更糟。

- **`instId` 形参保留**，只是不再作为 query 参数发给 `SWAP`/`FUTURES`/`OPTION`；`SPOT`/`MARGIN` 分支保留 `instId`（本仓今天不走，但契约不该被删掉）。
- **每跳都过令牌桶**（`_get` 内部已有 `_acquire_slot`，无需额外处理）。
  **【评审 P1-11】v1 说「最坏 10 次仍在 `live_auto_start_concurrency` 信号量内」是量纲误用**——那是一个
  **并发度**（默认 2），不是次数预算，把次数说成「在信号量内」等于什么都没约束。正确且可验收的表述是：
  阶梯把启动期的私有 GET **次数**从 N 提到最坏 2N；在按账户 5 次/秒的令牌桶下，N=5 的最坏增量是
  **1 秒量级的排队**（`(10-5)/5 = 1s`），且第 ② 跳**只在第 ① 跳明确回 4xx 时**才发生——也就是说，
  真实断网时次数**一次都不会翻倍**（`ADVANCE` 为假，直接上抛）。
- **总闸**：`ai_trade_fee_rate_family_fallback_enabled: bool = True`。置 `False` ⇒ 只发今天那一跳（`instType` + `instId`），逐字节等价。

**【评审 P1-6】`source` 的唯一产出点是 `trading._normalize_trade_fee`，不是 `fee_rates.py`。**
v1 的 §10.3 只列了 `ai_auto/fee_rates.py`，但那里是**读**这个值的（`snapshot_from_wire` 取
`wire.get("source")`，`fee_rates.py:64`）；真正硬编码 `"source": "exchange"` 的是
`okx/trading.py:629`。只改前者，`exchange:family` 这个值**永远不会出现**。
故 A-2 的落点补一条硬性规定：

| 谁 | 做什么 |
|---|---|
| `trade_client.get_trade_fee` | 返回 `(rows[0], rung)`，`rung ∈ {"family","type"}`——**它是唯一知道哪一跳成功了的地方** |
| `trading._normalize_trade_fee(raw, inst_id, *, rung)` | 把 `"source"` 从硬编码改为 `f"exchange:{rung}"`；`rung=None`（老调用点 / 单测）⇒ 逐字保留 `"exchange"` |
| `fee_rates.snapshot_from_wire` | **一字不改**（它本来就是透传 `wire["source"]`） |
| `apps/web/lib/aiTradeCost.describeFeeRates` | 值域 2 → 4（见 A-4） |

> **为什么阶梯而不是「直接改成 instFamily」**：因为在 A-1 落地之前，`instId` 为什么被拒是一个**未经生产数据证实的推断**（§二 R-1）。阶梯的价值在于：如果推断对，第 ① 跳就成；如果推断错，第 ② 跳的账户通用档大概率仍能拿到一个正确的费率等级（`level`），而 A-1 会把真正的原因写进日志与界面，下一次只需改 `inst_family_of` 一个纯函数。**这份设计在两种可能里都不会空手而归。**

### A-3｜参数错不再计入链路健康

`private_client._note_link_fail` 与 `trade_client` 的同名调用改为按 HTTP 状态分级：

| 状态 | 记法 | 理由 |
|---|---|---|
| 5xx、传输层异常 | `note_failure`（今天的行为） | 真的是链路 |
| 429 | `note_failure`（今天的行为，且已被用例冻结） | 真的是配额 |
| **4xx（非 429、非 401/403）** | **`note_rejected`（新增的中性登记）** | 请求到了、被业务拒了，链路是好的——但这也**不是一次成功** |
| 401 / 403 | `note_failure`，但 `code` 换成 `OKX_AUTH_REJECTED` | 凭证问题，与链路问题在界面上必须分开 |

**【评审 P1-3】v1 把 4xx 记成 `note_ok` 会制造一个新的假阴性，必须改。**
`LinkRegistry.note_ok`（`link_state.py:109-118`）同时做两件事：推进 `last_ok_ts` **并且把
`consecutive_failures` 清零**。而私有通道上**所有端点共用同一个 key**，于是这条序列：

```
真失败 → 真失败 → 一次 400 参数错 → 真失败 → 真失败 → …
```

**永远达不到降级阈值**——因为中间那个 400 每次都把计数洗回 0。用一个假阴性换掉另一个假阴性不是修复。

故新增 `LinkRegistry.note_rejected(key, *, kind, label, status, now=None)`，语义**只有三件事**：

```
h.rejected_4xx += 1
h.last_rejected_ts = ts
h.last_rejected_status = status
# 不动 consecutive_failures（它不是一次失败）
# 不动 last_ok_ts        （它也不是一次成功——「请求到了」≠「拿到过正确答案」）
```

- 需要 `link_state` 增三个不参与 `overall` 的加法字段 `rejected_4xx` / `last_rejected_ts` /
  `last_rejected_status`（默认 `0` / `0` / `0`，老快照缺键即默认值）。
- **总闸**：`okx_link_treat_4xx_as_link_failure: bool = False`（新默认即新行为；置 `True` ⇒ 逐字节回到今天）。
- **验收陷阱**：`link_state.snapshot()` 的 wire 被前端链路面板消费，新增键必须走「加法键」路线；`api/okx_link.py` 顶部已明文写着**不声明 `response_model`**（`okx_link.py:8-11`），本轴不得给它加上。

### A-4｜费率来源提到「它该被看见的地方」

三处落点（**都是加法，都不改既有渲染**）：

1. **启动弹窗**：`ai_trade_start` 的回执 `preflight` 段新增 `feeRates`（`{source, makerBps, takerBps, level, lastError:{code,msg}|null}`），弹窗「成本」分组新增一行。文案两态：
   - `交易所官方档位 Lv1：maker 2.00 / taker 5.00 bps`
   - `本地保守常数：maker 2.00 / taker 5.00 bps —— 交易所拒绝了费率查询（51000 Parameter instId error），成本与净额按保守常数计算`
2. **运行中成本卡**：`aiStats.cost` 段旁边渲染同一行（复用既有 `describeFeeRates`，只把 `source` 的取值域从 2 个扩到 3 个 + 追加 `lastError` 尾注）。
3. **轴 E 的真值源卡片第 1 行**（见 §九）。

- `describeFeeRates`（`apps/web/lib/aiTradeCost.ts:117`）扩展映射：
  | `source` | 文案 |
  |---|---|
  | `exchange:family` | `交易所官方档位{level}` |
  | `exchange:type` | `交易所账户通用档位{level}` |
  | `exchange`（老记录） | `交易所官方档位{level}` ← **必须保留**，历史记录里已经落了这个值 |
  | `config` | `本地保守常数` |
- **总闸**：`ai_trade_fee_rate_origin_visible: bool = True`。置 `False` ⇒ 三处都不渲染新行，回到今天。

### A-5（评审新增，P0-4）｜失败也要有一个宿主

**这是本轮 O-2 能不能兑现的分水岭，v1 漏了它。**

O-2 的验收判据写着「`config` 时携带最近一次失败的 `code`/`msg`」。但在今天的回落链上，
这句话**没有任何一处宿主**——失败信息被**连续三次**丢弃：

| # | 丢弃点 | 代码 | 后果 |
|---|---|---|---|
| ① | `trading._normalize_trade_fee` | `trading.py:611-622`：`maker/taker` 任一缺失 ⇒ `return None` | 阶梯挂在返回值上的 `_errors` 随之蒸发 |
| ② | `trading.fetch_trade_fee` | `trading.py:572-586`：签名是 `dict \| None` | 返回 `None` 时**没有第二个格子**能放 errors |
| ③ | `fee_rates.FeeRateCache.refresh` | `fee_rates.py:114-117`：`except Exception … logger.debug` | 连异常本身都只进 `debug` |
| ④ | `fee_rates.FeeRateCache.wire` | `fee_rates.py:139-140`：`if snap is None: return None` | **在本轮唯一真实发生的那个场景里（1,524/1,524 全败），`aiStats.feeRates` 这个键根本不存在** |

也就是说：v1 §12.3 给出的 `{"source":"exchange:family", …, "lastError": null}` 只描述了**成功态**，
而生产上 100% 是失败态——A-4 要往里加的 `lastError` 连一个容器都没有。

**修法（四处，缺一不可）**：

1. **`fetch_trade_fee` 的返回契约改为一个小结构体**（放在 `okx/types.py`，它已经是叶子）：

   ```python
   @dataclass(frozen=True)
   class FeeFetchOutcome:
       wire: dict | None                      # 归一后的费率 dict；失败即 None
       errors: tuple[FeeAttemptError, ...]    # 每一跳的 (rung, http_status, code, msg)；成功即空
   ```

   **老调用点的兼容**：`fetch_trade_fee` 保留今天的名字与语义（`dict | None`）作为薄封装
   `return (await fetch_trade_fee_ex(...)).wire`，新增 `fetch_trade_fee_ex` 供 A 轴使用。
   这样既有的 `CostRuntime` 绑定与全部替身**一字不改**。

2. **`FeeRateCache` 增一格失败状态**：`last_error: FeeAttemptError | None`，由 `refresh` 在
   `wire is None` 时写入（异常路径同样写入，`except` 里把异常压成一个 `FeeAttemptError`）。
   **`logger.debug` 提升为 `logger.warning`**，与 §4.0 原则 3「失败必须比成功更响」对齐。

3. **`FeeRateCache.wire()` 在无快照时返回降级形状，而不是 `None`**：

   ```jsonc
   // 从未成功取到过官方档位（本机今天的真实状态）
   {"source": "config", "attempted": true, "makerBps": 2.0, "takerBps": 5.0,
    "level": "", "ruleType": "", "rebate": false, "stale": false,
    "lastError": {"rung": "type", "httpStatus": 400,
                  "code": "51000", "msg": "Parameter instId error"}}
   ```

   - `makerBps` / `takerBps` 取**回落后的配置常数**（`resolve_fee_rates(settings, None)` 的产物），
     这样卡片上永远有一组数，且它们与记账链**用的是同一组数**（A-6 单一真源约束不变）。
   - **`attempted=False`（还没轮到取数）⇒ 仍返回 `None`**。这一条不许省：`FeeRateCache.attempted`
     的既有 docstring 已经写死了理由——「还没轮到取数就先告知一遍，说的是一件当时并不成立的事」。
   - **总闸**：受 `ai_trade_fee_rate_origin_visible` 节制；置 `False` ⇒ `wire()` 逐字节回到今天
     （无快照即 `None`）。

4. **`manager._bounded_fee_rate` 不再吞掉原因**（`manager.py:477-489`）：它今天是
   `except Exception … logger.info(...) ; return None`。改为把 `FeeFetchOutcome.errors` 一并带回，
   由 `_gather_context` 塞进 `config_map[inst_id]["feeRates"]` 的同一格里（**已有的 key，不新增**），
   再由会话侧 `seed` 进 `FeeRateCache`。

**一句话判据**：本轮之后，在这台机器上启动一次分组，启动弹窗上必须能读到
`本地保守常数：maker 2.00 / taker 5.00 bps —— 交易所拒绝了费率查询（51000 Parameter instId error）`。
**读不到这句话，轴 A 就没做完**，无论用例多绿。

---

## 六、轴 B：让情报的 SSRF 闸在 fake-IP 网络下仍然既安全又有用

### B-1｜解析结果三分类

**新增纯模块 `services/quant/app/ai_auto/intel/netpolicy.py`**（`intel/` 包里 `search.py` 与 `taxonomy.py` 是仅有的叶子；本模块也做成叶子，**不得 import `service` / `runtime` / `arming`**——`AS-R-P0` 的 `p0_2` 已经为一次 `intel/` 内的循环导入付过代价）：

```python
SYNTHETIC_V4 = (ipaddress.ip_network("198.18.0.0/15"),)   # RFC 2544 基准段：fake-IP 代理的事实标准

Resolution = Literal["public", "private", "synthetic", "unresolved"]

def classify_addr(raw: str) -> Literal["public", "private", "synthetic"]:
    """单个地址三分类。`synthetic` 优先于 `private`（198.18/15 两者都命中）。"""

def classify_resolution(addrs: Sequence[str]) -> Resolution:
    """整组地址 → 一个结论。判据顺序不可换：
       空          ⇒ "unresolved"
       全部 synthetic ⇒ "synthetic"
       任一 private   ⇒ "private"      ← 混合了真实私有地址即拒，不因为有合成地址而放行
       其余           ⇒ "public"
    """
```

**改 `reader.guard_hop`（`reader.py:165-172`）**，把今天的两值判定换成四值：

| `classify_resolution` | `auto`（新默认） | `strict`（今天的行为） |
|---|---|---|
| `public` | 放行 | 放行 |
| `private` | **拒**（`private_host`） | 拒 |
| `synthetic` | **降级放行**，见下 | **拒**（`private_host`） |
| `unresolved` | **拒**（新码 `dns_failed`） | 拒（今天记 `private_host`，本轮拆出新码） |

「降级放行」= `synthetic` 时不再由 DNS 决定，而是要求同时满足：

1. 语法闸已过（`guard_url` 逐字不动：`localhost`、私有后缀、非法端口、userinfo、非法方案照拒）；
2. **主机不是 IP 字面量**——`synthetic` 下没有域名可判，IP 字面量一律拒（`guard_url` 已经会对私有 IP 字面量拒，这里把公网 IP 字面量在 `synthetic` 档下也拒掉）。
   **【评审 P1-12】这一条在正常拓扑下不可达，保留它的理由必须写清楚，否则它是一条没人看得懂的死代码**：
   IP 字面量的 `getaddrinfo` 返回它自己，`classify_resolution` 因此恒得 `public`，**永远进不了
   `synthetic` 分支**。它守的不是今天的任何一条路径，而是两类未来：①解析器被污染（`/etc/hosts`、
   恶意 DNS、企业 DoH 策略）导致一个 IP 字面量被映到别处；②本仓某天引入自定义 resolver 或
   `httpx` 的 `transport` 级解析钩子。故 G-B5 必须写成**注入解析替身的白盒用例**，并在断言里
   写清它守的是什么——一条只能靠替身触发的门禁，如果不说明理由，下一轮一定会被当成噪声删掉；
3. `taxonomy.normalize_domain(host)` 能归一出一个非空注册域（拒绝畸形主机名）。

**并同时开启 B-1c 的出网后复核**（这是把否决权还给「有因果关系的判据」）：

```python
# reader._fetch_one 内，拿到 response 之后、读 body 之前
peer = _peer_addr(response, cfg=cfg)      # 见下方三条硬性约束
if peer is not None and classify_addr(peer) == "private":
    return _result(url, status="blocked", ...)     # 新拒因 private_peer
```

**【评审 P0-1，阻断级】`_peer_addr` 的三条硬性约束——少任何一条，本条就会在一类真实机器上
把情报抓取变成 100% 拒绝，而那正是本轮要修的病灶本身：**

1. **`cfg.proxy` 非空 ⇒ 整条对端复核跳过，返回 `None`。**
   `intel/evidence.py:44` 把 `settings.proxy_url` 原样传进 `ReaderConfig.proxy`，
   `reader.fetch_many` 用 `httpx.AsyncClient(..., proxy=cfg.proxy or None)` 出网
   （`reader.py:303`）。**此时 TCP 对端是代理，不是目标站点**——而本项目目标用户的常见形态
   正是 `http://127.0.0.1:7890` 一类的本地代理，`classify_addr("127.0.0.1")` 判回环 ⇒ `private`
   ⇒ **每一篇都记 `private_peer`**。
   v1 还写着「这一条在三个档位下全开，包括 `strict`」，于是连回退档都救不回来。
   这与 §二 R-2 总结的那条根因**一字不差**：一道 fail-closed 的闸，判据在某类环境里恒假，
   于是「零可用」和「很安全」在日志里长得一模一样。
   跳过时**记一次 `debug` 并在 `ReaderResult` 上带 `peer_checked=False`**，由 B-2 的健康行
   如实显示「经代理出网，对端不可判」——**降级要有声，哪怕降的是一道防护**。
2. **`server_addr` 是 `(host, port)` 元组，不是字符串。** 必须先取 `[0]` 再 `str()` 再分类；
   IPv6 的 `%scope` 后缀要按既有 `resolve_is_public` 的写法 `split("%", 1)[0]` 去掉。
   直接把元组喂给 `ipaddress.ip_address` 会抛 `ValueError`，而这段跑在**已经拿到响应之后**——
   一个会抛的复核函数等于把「抓到了正文」变成「抓取器自己崩了」。
3. **`peer` 取不到（HTTP/2、非 httpcore transport、`MockTransport`、测试替身）⇒ 视为未知，不拒。**
   否则一次 `httpx` 版本变化会静默关掉整个功能——本仓已在「`websockets` 15 的 `proxy` 默认值」
   上栽过一次同形的跟头。

其余语义：

- `synthetic` 的对端**不拒**（TUN 下对端本来就是合成地址）。
- 这一条在三个档位下**全开**，包括 `strict`——它只增强、不放松。
  **但「全开」的含义被上面第 1 条收窄为「在能判的时候全开」**：配了显式代理时它一次都不判，
  且这件事必须在界面上说出来，而不是悄悄不判。

**总闸**：`ai_trade_intel_reader_resolver: Literal["auto","strict"] = "auto"`。`strict` ⇒ `guard_hop` 逐字节回到今天（`synthetic` 与 `unresolved` 都记 `private_host`），B-1c 仍开。

**新增可配置段**：`ai_trade_intel_reader_synthetic_cidrs: str = ""`（逗号分隔，追加到 `SYNTHETIC_V4`）。留给用 `240.0.0.0/4` 或其它段做 fake-IP 的代理实现。解析失败的条目**跳过并 `warning` 一次**，绝不让一个打错的 CIDR 把整个闸变成 `unresolved`。

### B-2｜让已经在屏幕上的数字开口

> **【评审 P1-7】先把这一节的立论改准。** v1（含 §零 与 附录 D 的 F-7）说这两列
> 「已经落库却没有任何读点」——**这句话可以被一眼证伪**：`db/ai_intel_repositories.py:139-140`
> 已经把它们映进轮次 wire，`apps/web/components/Intel/IntelRoundsTab.tsx:172-173` 已经把
> 「正文 5/6」渲染在轮次页上（连用例都有：`IntelRoundsTab.test.tsx:101`）。
> 准确的那一句是：**有逐轮读点，但从不聚合成结论、从不跨轮判趋势、从不告警**——
> 一个人要发现「16 轮全零」，得自己翻 16 行然后自己做加法。
> 改完之后立论比原来更硬：B-2 要做的**正是**那三个「从不」（`fetchAttempt`/`fetchOk` 的聚合、
> `zeroOkStreak` 的跨轮趋势、横幅的告警），而不是「补一个不存在的读点」。

**后端（`read_intel_document_health`，`api/ai_intel.py` 的 `/intel/documents/health`）** 的 wire 增四个加法键：

```jsonc
{
  "cachedDocs": 29, "bodyBytes": 0, "oldestFetchedTs": …, "lastPrunedTs": …, "readerEnabled": true,
  "fetchAttempt": 96,          // Σ ai_intel_rounds.reader_attempt   （kind='collect'）
  "fetchOk": 0,                // Σ ai_intel_rounds.reader_ok
  "blockedByStatus": {"blocked": 29},      // ai_intel_documents 里 status != 'ok' 的分布
  "rejectCodesSinceStart": {"private_host": 29},   // 进程内计数器，见下
  "zeroOkStreak": 16           // 最近连续多少轮 reader_attempt>0 且 reader_ok==0
}
```

**【评审 P0-2，阻断级】这个端点声明了 `response_model`，四个加法键会被 FastAPI 静默剔除。**

```python
@router.get("/intel/documents/health", response_model=IntelDocumentHealthOut)   # ← 就是这一行
```

`IntelDocumentHealthOut`（`api/schemas_intel_evidence.py:66-74`）是一个**只有 5 个字段**的严格
`BaseModel`。往处理函数的返回 dict 里多塞四个键，FastAPI 会在序列化时**一声不响地扔掉它们**——
本仓已为 `edgeSource` 付过一次一模一样的代价，而 v1 的 §11.1 只对**两个新端点**写了
「不声明 `response_model`」的纪律，对这个**既有**端点只字未提。

**修法（两条，缺一不可）**：

1. `api/schemas_intel_evidence.py:66` 的 `IntelDocumentHealthOut` **同步补上这四个字段**，
   全部给默认值（`fetchAttempt: int = 0` / `fetchOk: int = 0` /
   `blockedByStatus: dict[str, int] = {}` / `rejectCodesSinceStart: dict[str, int] = {}` /
   `zeroOkStreak: int = 0`）——**不要**改成不声明 `response_model`：这个端点的 schema 是被
   前端类型文件对着抄的，去掉它会把一个静默剔除换成一个静默漂移。
2. 门禁 **G-B10 必须起一次 `TestClient` 并断言四个键真的到达了客户端**，而不是断言处理函数的
   返回值——后者在 schema 漏改时**照样是绿的**，那正是这类缺陷能藏住的唯一原因。

**【评审 P1-5】`blockedByCode` 在零迁移前提下不可计算，已改名并拆成两个键。**
`AiIntelDocument.status` 存的是 `ok|blocked|http_error|timeout|network_error|not_html|too_large|empty`
（`db/models.py:1196` 的取值域注释），**不是** `private_host` 这类拒因；拒因今天只出现在
`reader.py:240-242` 的一行 `logger.info` 里，从不落库。而 §12.1 / N-7 声明零迁移，于是它无处可存。故：

| 键 | 来源 | 性质 |
|---|---|---|
| `blockedByStatus` | `ai_intel_documents` 按 `status` GROUP BY（零迁移即可算） | **持久**、跨重启 |
| `rejectCodesSinceStart` | 新增进程内计数器 `reader.reject_counter`（`guard_hop` 每返回一个拒因即 +1） | **进程内**、重启归零 |

`rejectCodesSinceStart` 的 wire 上**必须同时带 `sinceProcessStart: true`**，前端文案写
「本次运行以来」。一个重启就归零的计数器，如果不说自己会归零，就是在制造第五条静默降级。

**【评审 P1-6】四个新读数的唯一装配点是 `db/ai_intel_evidence_repositories.py::document_health`
（`:209-223`），v1 的 §10.3 没有列它。** 端点自己只做一件事（叠 `readerEnabled`），判定与取数
全在 repo 层——这是本包的既有分工，本轮不改。`fetchAttempt` / `fetchOk` / `zeroOkStreak` 需要
一次对 `ai_intel_rounds`（`kind='collect'`）的聚合查询，与既有的三条 `document_health` 查询
**合并在同一个 `document_health` 调用里**，不新开第二个 repo 函数（否则端点上会出现两处取数点）。

**【评审 P2-6】`zeroOkStreak` 的边界定义（v1 未写，会导致两种实现）**：
从最近一轮往回数，**只计 `reader_attempt > 0` 的轮次**；`reader_attempt == 0` 的轮次
**跳过不计，也不中断连续**（一轮没有候选不该洗掉一条真实的连续失败）；遇到第一个
`reader_ok > 0` 即停止。上限扫 50 轮（避免全表扫）。

**前端纯函数（`apps/web/lib/intelEvidence.ts`，新增 `describeReaderHealth`）**，三态：

| 判据 | 语气 | 文案 |
|---|---|---|
| `fetchAttempt === 0` | 中性 | `本轮未尝试抓取正文` |
| `fetchOk === 0 && fetchAttempt > 0` | **危险** | `正文抓取 0/{attempt} 全部失败{·主要原因：本机 DNS 把公网域名解析成了合成地址（198.18.0.0/15），可在下方切换解析策略}` |
| 其余 | 正常 | `正文抓取 {ok}/{attempt}` |

**升级为横幅**：`zeroOkStreak >= ai_trade_intel_reader_zero_streak_warn`（默认 3）时，在情报面板顶部渲染一条**琥珀色横幅**（复用第 1 轮 `AiTradeLivenessBanner` 的形状与配色），带一个「查看被拦域名」抽屉，逐条列出 `ai_intel_documents` 里 `status != 'ok'` 的 `domain` + `status`。

**并把「29 篇 / 0 字节」这个组合直接翻译成结论**：`cachedDocs > 0 && bodyBytes === 0` ⇒ 在 `IntelReaderSettings` 的文档健康行后追加一句 `—— 这些文档一篇正文都没抓下来`。

**总闸**：`ai_trade_intel_reader_health_visible: bool = True`。

### B-3｜明确不做的两件事（写进代码注释，防止下一轮有人"顺手"改）

1. **不放松 `unsourced_number`**（§三 N-2）。
2. **不给 `reader` 加「抓不到就用摘要凑数」的回落**。今天 12 条引证全部来自 `source_field='snippet'` 已经是这条回落的结果；把它做得更宽只会让「有引证」这件事更不值钱。修法是让正文真的抓回来，不是让摘要更容易冒充正文。

### B-4｜安全影响评估（必须写进评审）

| 攻击面 | 今天 | 本轮之后 |
|---|---|---|
| `http://localhost/…`、`http://192.168.1.1/…` | 语法闸拒 | **不变**，语法闸拒 |
| `http://internal.corp/…`（解析到 10.x） | 解析闸拒 | `auto` 下：解析是 `private`（不是 `synthetic`）⇒ **仍拒** |
| 同上，但在 TUN 机器上解析到 198.18.x | 解析闸拒（**误拒的同一条通路**） | `auto` 下降级放行 ⇒ **由 B-1c 的对端复核兜底**：真实对端若是 10.x 则拒并记 `private_peer` |
| 重定向到内网 | 每跳都过闸 | **不变**，每跳都过闸 + 每跳都做对端复核 |
| DNS rebinding | 今天没有防护（解析与连接是两次独立解析） | **B-1c 反而补上了**：对端复核发生在连接建立之后 |

**结论：`auto` 档在 TUN 环境下的净安全强度不低于今天，在非 TUN 环境下严格更高（多了一道对端复核）。** 唯一真实的放松是「TUN 机器 + 攻击者能让公司内网域名解析进合成段 + 真实对端却是公网」这一组合，而该组合下攻击者已经控制了解析器，语法闸之外的任何 DNS 判据都已失效。

---

## 七、轴 C：把账户级持仓快照做成整个进程共享一份

### C-1｜新增 `services/quant/app/okx/positions_cache.py`

**为什么是新模块而不是塞进 `trading.py`**：`trading.py` 已 844 行，越过 CLAUDE.md 的 800 行红线（`error_text.py` 的诞生就是同一个理由，见其模块 docstring）。

```python
@dataclass(frozen=True)
class PositionsSnapshot:
    rows: tuple[dict, ...]      # 归一后的持仓行（_normalize_position 产物）
    taken_wall: float           # 这份读数的真实取得时刻（**不是**放进缓存的时刻）

class PositionsCache:
    """按凭证指纹的账户级持仓快照：单飞 + TTL。

    三条不可协商的语义：

    1. **失败不缓存。** 一次失败的读取绝不写进缓存；等在同一次飞行上的每一个调用者
       都拿到**同一个异常实例**并各自上抛——与今天「每个调用者自己发一次、自己吃自己的
       异常」在可观察行为上等价（`executor.reconcile` 的 `except OKXError` 分支、
       `_reconcile_fail_streak` 的累加语义因此逐字不变）。
       ⚠ 唯一差别：N 个成员本来会各记一次失败，现在共享一次飞行 ⇒ 仍各记一次
       （异常被每个等待者独立接住），故 `_reconcile_fail_streak` 的推进速率不变。

    2. **单飞。** TTL 内的并发调用共享同一个 in-flight future，不产生第二次网络往返。
       这是 5× 压降的来源，而不是 TTL 本身。

    3. **写即作废。** `invalidate(fingerprint)` 必须在任何一次**成功的私有写**之后被调用，
       否则一次刚成交的开仓可能撞上一份 4 秒前的空快照，而 `_on_reconcile_ok` 会把它
       合成为 `external_close` 幻影平仓——那正是 `ai-trade-limit-orders` O-3 已经付过
       一次代价的形状，绝不允许因为一个缓存把它请回来。
    """
    async def get(self, creds, *, ttl_s: float, force: bool = False) -> PositionsSnapshot: ...
    def invalidate(self, fingerprint: str) -> None: ...
```

- 指纹**必须**复用 `okx/client_pool.fingerprint(api_key, base, demo, timeout)`（`private_client.py:83-87` 的同一份），**不得**另造一份：两份指纹一旦漂移，缓存就会跨账户串数据——这是本轴唯一一个会造成资金后果的实现错误。
- `ttl_s <= 0` ⇒ **完全旁路**（每次都真发），逐字节回到今天。
- 进程内单例 `get_positions_cache()`，与 `get_client_pool()` / `get_limiters()` 同形。

### C-2｜三个消费点按语义分级

| 消费点 | 文件:行 | 分级 | 理由 |
|---|---|---|---|
| 盯市 `reconcile` | `live_auto/executor.py::reconcile` | **用缓存**（`ttl_s = settings.ai_trade_positions_snapshot_ttl_s`） | 语义是「校正」，本来就是 5 秒一次；N 个成员共享一份不改变任何单个成员看到的内容 |
| 反手前净仓确认 `_confirm_net_cleared` | `live_auto/executor.py::_confirm_net_cleared` | **`force=True`** | 它是一个**轮询等待**：读缓存会让每次重试拿到同一个答案，等于把「等净仓归零」变成一个恒假的死循环 |
| 暂停时读持仓真值 `_read_exchange_position` | `ai_auto/session.py::_read_exchange_position` | **`force=True`** | 其 docstring 已写明「多打一次私有 REST 完全值得，『我关机时到底还有多少仓』这个数不该来自本地投影」——同理不该来自一份 4 秒前的缓存 |
| 启动前置 `_gather_context` | `ai_auto/manager.py::_gather_context`、`live_auto/manager.py` 同名 | **`force=True`** | 前置门禁要判「这个标的现在有没有仓」，用陈旧读数会放过一个已有仓的成员 |
| **账户面板 `GET /positions`** | **`api/trading.py:104`** | **不缓存（既不 `use_cache` 也不 `force`）** | **【评审 P1-9】v1 漏了这第 5 个消费点。** 它是用户主动点开的只读面板，语义是「现在到底有多少仓」；接上缓存会让「刷新」这个动作变得不可信。此处**显式登记为不得接入**，防止下一轮有人「顺手」把它也接上去 |

> **锚点说明（评审 P2-1）**：上表刻意改用**符号名**而不是行号。v1 的行号按 `8451163` 读出，
> 而工作树里 `api/ai_intel.py` 有一处未提交的 204 修复（+10 行），`session.py:1170` 实际为 `1159`，
> `executor.py:648/671` 实际为 `645/655`。实施节点按符号定位，不要按行号。

**接线形状（【评审 P1-9】修正了 v1 的形状错误）**：
`_read_positions` 在今天**是一个属性而不是方法**——`executor.py:157` 是
`self._read_positions = positions_fn or self._default_read_positions`。v1 写「增两个方法」，
照写会与构造器里的这一行属性赋值直接打架（属性会把同名方法遮掉，而遮掉的方向取决于赋值顺序，
是一个会随重构静默翻转的行为）。

正确形状是**两个属性，由同一个 `positions_fn` 派生两条规则**：

```python
self._read_positions       = positions_fn or self._default_read_positions        # 走缓存
self._read_positions_fresh = positions_fn or self._default_read_positions_fresh  # force=True
```

- **测试注入缝必须同时覆盖二者**：`positions_fn` 一旦给出，两条路径都用它（30+ 个用例的替身
  否则只能拦住一半调用，另一半会真的出网）。这也是 G-C7 真正在守的东西。
- **两个默认实现的差别只有一个参数**：`fetch_live_positions(self.creds, use_cache=True, ttl_s=…)`
  与 `fetch_live_positions(self.creds, force=True)`。

**【评审 P2-3】容器类型**：`PositionsSnapshot.rows` 声明为 `tuple[dict, ...]`（不可变是对的），
但 `get()` 的出口必须 `list(snapshot.rows)` 一次再交给 executor——今天 `_read_positions()` 的
产物是 `list[dict]`，全部替身也返回 list，`_net_abs_pos` / `_net_upl` 的类型注解都是 `list[dict]`。
让缓存的内部不可变性泄漏到调用方，只会换来一批与本轴无关的类型噪声。

### C-3｜写即作废的接线点

在下列每一处**成功返回之后**调用 `get_positions_cache().invalidate(fp)`：

| 写操作 | 文件 |
|---|---|
| `place_live_order` | `okx/trading.py` |
| `close_live_position` | `okx/trading.py` |
| `cancel_live_order` | `okx/trading.py` |
| 算法单下 / 撤（兜底挂单） | `okx/trading.py` |
| `set_leverage` | 不需要（不改持仓） |

**判据是「私有写成功」而不是「会话认为自己成交了」**：后者会漏掉交易所侧 OCO 触发那一类我方没有下过单的持仓变化，而那一类正好由 TTL 兜住（≤ 4 秒）。

**【评审 P1-10】指纹从哪来——这是本轴唯一有资金后果的实现细节，v1 没有规定它。**
C-1 自己写着「两份指纹一旦漂移，缓存就会跨账户串数据」，但没说 `invalidate(fp)` 里的 `fp` 怎么算。
而 `close_live_position(s, creds, account_id, close)` 一类写函数的签名里**没有 `settings`**，
`client_pool.fingerprint(api_key, base_url, demo, timeout)` 却需要三个都来自 `Settings` 的值
（`okx_rest_base` / `okx_account_demo` / `okx_account_timeout_seconds`）。任何一处用
`get_settings()` 重新拼一遍，都是在造第二份指纹。

**硬性规定**：

- **指纹一律由 `OKXTradeClient._fingerprint()` 现算**（`private_client.py:82-87`）。
  这些写函数本来就为这一次写建了一个 client 实例（`trading.py` 的既有形状），
  在 `finally: await client.aclose()` **之前**取一次 `client._fingerprint()` 即可，零额外成本。
- **严禁**在 `trading.py` 里出现第二处 `fingerprint(` 调用。
  由门禁 **G-C12**（AST 断言 `okx/trading.py` 的整棵树里 `fingerprint` 只作为
  `client._fingerprint()` 的属性访问出现，不作为自由函数被调用）钉死。
- `PositionsCache` 内部同样**不自己算指纹**，只接受调用方给的字符串——一个缓存不该知道
  凭证长什么样。

### C-4｜防御纵深：陈旧快照不得裁决幻影平仓

`_on_reconcile_ok`（`executor.py:676-700`）的幻影判定（`projected != "flat" and abs_pos <= 0`）额外要求：

```
snapshot.taken_wall >= self._last_private_write_wall
```

- `_last_private_write_wall` 由 `LiveExecutor` 在每次自己发起写之后置位（进程内，重启即 0）。
- 条件不满足 ⇒ 本轮**只更新未实现盈亏，不裁决幻影**（与既有 `_fill_grace` 的「延后一轮」代价完全相同，且两者可叠加）。
- 这条与 C-3 是冗余的（C-3 已经作废了快照）。**冗余是有意的**：C-3 是「记得调用」的纪律，C-4 是「忘了调用也不会出事」的构造性保证。

### C-5｜预期效果与代价

| 读数 | 今天 | 本轮之后（N=5, TTL=4s） |
|---|---:|---:|
| `positions` 次/分 | 63.5 | **≈ 12.7** |
| `positions` 占全部出网 | 78.3% | **≈ 39%** |
| 账户私有令牌桶占用（背景） | ≈ 1.06 tok/s | **≈ 0.21 tok/s** |
| 单次 `reconcile` 看到的持仓最大陈旧度 | 0 s | **≤ 4 s**（且被 C-3 / C-4 双重约束） |
| 日志行数（配合轴 D） | 4,200 行/小时 | **< 200 行/小时** |

**代价的诚实陈述**：TTL 内第 2..N 个成员看到的是同一份读数。在「A 成员刚被 OCO 平仓、B 成员这一轮读到的快照是 3 秒前的」这个场景里，B 成员对**A 的持仓**的认知会晚 3 秒——而 B 根本不消费 A 的持仓（`_net_abs_pos` 按 `instId` 过滤）。真正受影响的只有「本成员自己的持仓在过去 4 秒内变了」，而那恰好被 C-3（我方写）与 C-4（构造性保证）覆盖，剩下的交易所侧变化最多晚一个 TTL——**而今天它最多晚一个 `poll_interval_s = 5` 秒，本轮之后是 4 秒，严格更好**。

**总闸**：`ai_trade_positions_snapshot_ttl_s: float = 4.0`（`0` ⇒ 完全旁路，逐字节回到今天）。上界由启动期校验器钉死：**必须 < `live_auto_poll_interval_s`**，否则一轮盯市可能整轮读不到新数。

---

## 八、轴 D：日志治理与调用统计

### D-1｜桌面端日志轮转

**新增 `desktop/src/lib/rotating-log.js`**（零依赖，纯 `fs`；与 `desktop/scripts/ensure-wincodesign.mjs` 同族的小工具风格）：

```js
/**
 * openRotating(dir, name, { maxBytes, keep }) -> { write(chunk), end() }
 *
 * 语义：
 *  - 打开时若既有文件已 >= maxBytes，**先轮转一次**（本机现状 254.7MB 会立刻变成 backend.1.log）
 *  - 每次 write 之后累计字节；越线即同步 rename 链：
 *      backend.{keep-1}.log 删除 → … → backend.1.log → backend.2.log → backend.log → backend.1.log
 *  - rename 失败（Windows 上文件被占用）⇒ **不抛、不丢数据**：本次继续写当前流，
 *    下一次 write 再试。一个用来保命的日志轮转器，自己绝不能成为进程崩溃的原因。
 *  - 暴露的形状与 fs.WriteStream 在调用点所需的子集一致（.write / .end），
 *    故 process-manager 的 `logStream` 传参**一行不改**（main.js:172/182/197/208）。
 */
```

**接线**：`desktop/src/main.js:43-49` 的 `openLogStreams` 改为返回 `openRotating(dir, name, {...})`；dev 分支（返回 `null`）逐字不动。

**参数（【评审 P0-5】修正了 v1 的取值来源——照 v1 写，这两个开关在打包产品里按不下去）**：

v1 写的是 `process.env.ARAGON_LOG_MAX_BYTES`。但 `openLogStreams()` 在 `bootstrap()` 的**第一行**
执行（`main.js:528-529`），而 `agentEnv` 要到后端子进程装配时才被读（`main.js:150-155`）；
一个从开始菜单 / 桌面图标启动的 Electron 进程，`process.env` 里**不会有**任何用户设的键。
于是「`ARAGON_LOG_MAX_BYTES=0` ⇒ 逐字节回到今天」这句承诺，在唯一的产品形态里是空的。

正确取值链（三级，逐级回落）：

```js
const desk = readDesktopLogEnv();   // 1) agentEnvLib.loadAgentEnv() —— 用户可编辑的 userData/agent.env
                                    // 2) process.env               —— 开发 / CI / 命令行启动
                                    // 3) 内建默认 16MB × 3
const maxBytes = firstNumber(desk.ARAGON_LOG_MAX_BYTES, 16 * 1024 * 1024);
const keep     = firstNumber(desk.ARAGON_LOG_KEEP, 3);
```

- `loadAgentEnv()` **必须整段包在 `try/catch` 里**，任何异常（文件不存在 / 编码坏 / 权限）
  一律静默回落默认值：一个用来保命的日志轮转器，绝不能成为**启动失败**的原因，
  而它现在跑在启动的第一行上。
- `ARAGON_LOG_MAX_BYTES=0` ⇒ **不轮转**，行为与 `fs.createWriteStream(..., {flags:'a'})` 逐字节一致。
- 这两个键**不进后端**（它们是主进程的事），因此**不受** `config.js` 前缀白名单约束；
  但 `.env.example` 与「关于」页的诊断信息里都要登记，否则用户无从知道它们存在。

### D-2｜把 HTTP 客户端日志压到 `WARNING`

`services/quant/app/main.py:58` 之后追加：

```python
for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).setLevel(
        getattr(logging, settings.ai_trade_log_http_client_level.upper(), logging.WARNING))
```

- **新配置 `ai_trade_log_http_client_level: str = "WARNING"`；置 `"INFO"` ⇒ 逐字节回到今天。**
  **【评审 P0-5】键名从 v1 的 `log_http_client_level` 改成带 `AI_TRADE_` 前缀，这不是风格问题。**
  `desktop/src/lib/config.js:80-82` 的后端 env 透传白名单是**前缀式**的：
  `AGENT_ | LIVE_ | AI_TRADE_ | DISCOVERY_ | OKX_ | SECURITY_`。
  `LOG_HTTP_CLIENT_LEVEL` **一个前缀都不匹配** ⇒ 后端 `Settings`（`extra="ignore"`）
  把它静默丢掉 ⇒ D-2 声明的「一键恢复 `INFO`」在打包桌面端里**不存在**。
  这一条已经被 `desktop/scripts/config-passthrough.test.mjs` 的探针表钉住了形状，本轮**只需
  往那张表里补两行**（`AI_TRADE_LOG_HTTP_CLIENT_LEVEL` + 一个 `OKX_` 键），产品代码零改动。
  本轮另外三把闸（`okx_http_error_detail_enabled` / `okx_link_treat_4xx_as_link_failure` /
  `okx_call_stats_enabled`）走 `OKX_` 前缀，天然在白名单内——**评审已逐个核对**。
- **信息损失评估（【评审 P1-8】修正：v1 写「零」，那是不成立的，而证伪它的正是本文自己）**：

  | 被压掉的读数 | 压掉后还能从哪拿到 | 结论 |
  |---|---|---|
  | OKX 端点 × 状态码分布 | D-3 的 `CallStats`（三个 OKX 客户端全接） | **无损失** |
  | OKX 真实失败 | `link_state.note_failure` + 服务层 `WARNING` | **无损失** |
  | 决策 / 整理的失败 | `decider` 的 `WARNING` + `SummaryHealth` | **无损失** |
  | **LLM 提供商的状态码分布**（`bigmodel · 529×779 / 429×108`、`kimi · 403×408`） | **今天只有 `httpx` 的 `INFO` 行知道**——`decider` 只记失败**次数**，不分状态码 | **会损失，故 D-3 必须扩到 5 个接线点（见下）** |

  §一 E-2 / E-8 里那两组 LLM 读数，本身就是从 `httpx` 行里数出来的。把它压掉而不补，
  等于本轮亲手制造第五条静默降级——而轴 E 的第 4 行（决策模型）正要读它。

### D-3｜进程内按端点的调用与错误计数

**新增 `services/quant/app/okx/call_stats.py`**（纯内存、零 IO、零锁竞争——只在事件循环线程里读写）：

```python
@dataclass
class EndpointStat:
    calls: int = 0
    errors_4xx: int = 0
    errors_429: int = 0
    errors_5xx: int = 0
    transport_errors: int = 0
    last_ts: float = 0.0

class CallStats:
    """滑动窗口（默认 15 分钟，按分钟分桶）+ 进程累计。

    键是 (method, normalized_path)：路径里的 instId / ordId 一律不进键
    （否则多标的会把这张表撑成一份高基数指标，而它的用途是「看哪个端点最热」）。
    """
    def note(self, method: str, path: str, *, status: int | None, transport_fail: bool = False) -> None: ...
    def wire(self, now: float | None = None) -> dict: ...
```

- **五个接线点（【评审 P1-8】：v1 只有三个，会让 LLM 侧同时失去日志与统计）**：
  1. `okx/rest_client._request`（公共 REST）
  2. `okx/private_client._get`（签名 GET）
  3. `okx/trade_client._get/_post`（签名 GET/POST）
  4. `agent/llm/anthropic_client` 的响应出口
  5. `agent/llm/openai_client` 的响应出口
- **归一化分两族，不可混用**：
  - OKX 侧：键是 `("GET", "/api/v5/account/positions")`——**去掉 query string** 即可
    （带路径参数的端点本仓没有，OKX v5 全部走 query）。
  - LLM 侧：键是 `("POST", "<host>")`（如 `open.bigmodel.cn` / `api.kimi.com`）——**按 host 不按
    path**。不同厂商的 path 形状不同（`/api/paas/v4/chat/completions` vs `/v1/messages`），
    按 path 归一会把同一个提供商拆成好几格，而这张表的用途是「哪个提供商在拒我」。
  - 两族共用同一个 `CallStats` 实例但**键的第二段语义不同**，故 `wire()` 里分两个数组下发
    （`okx[]` / `llm[]`），前端不必知道这条区分规则。
- 暴露：`GET /api/okx/connectivity` 的返回体新增 `callStats` 加法键（该端点**已明文禁止声明 `response_model`**，天然安全）。
- **总闸**：`okx_call_stats_enabled: bool = True`。置 `False` ⇒ `note` 立即 return、`wire` 返回 `{}`，端点上不出现该键。

### D-4｜预期效果

| 读数 | 今天 | 本轮之后 |
|---|---:|---:|
| `backend.log` 单文件 | 254.7 MB 且无上限 | ≤ 16 MB × 3 |
| 日增量 | ≈ 12 MB/日 | ≈ 0.5 MB/日 |
| `httpx` 行占比 | 85.7% | **0%**（`WARNING` 以下不再落盘） |
| 「哪个端点最热」这个问题的答案在哪 | 只能对 216 万行做聚合 | `GET /api/okx/connectivity` 的 `callStats` |

---

## 九、轴 E：一张「真值源」卡片

### E-1｜后端：`GET /api/system/truth-sources`

新增路由文件 `services/quant/app/api/truth_sources.py`（**只读、永不 5xx、至多一次只读 DB**——前两条与 `api/okx_link.py` 的纪律逐字同源，并在 docstring 里显式引用它）。

> **【评审 P1-4】v1 写的是「零 IO」，与它自己的数据来源矛盾。** 下表四行里，`intelReader` 那一行的
> 真源是 `repo.document_health()`（`db/ai_intel_evidence_repositories.py:209`），那**是一次数据库读**。
> 纪律因此改成准确的那一句：**「至多一次只读 DB，且那次 DB 读失败即该行降级为 `unknown`，
> 绝不让整个端点 5xx」**——一个用来观察故障的端点，自己不许在故障时跟着倒下。
> 另外三行（`feeRates` / `privateQuota` / `decisionModel`）**必须保持零 IO**（全部来自进程内状态），
> 因为 §11.2 的 WS 键只由它们派生。

```jsonc
{
  "generatedTs": 1788345600,
  "sources": [
    {
      "key": "feeRates",
      "label": "交易所费率",
      "state": "degraded",                       // ok | degraded | unknown | off
      "reading": "本地保守常数：maker 2.00 / taker 5.00 bps",
      "reason": "交易所拒绝了费率查询：51000 Parameter instId error",
      "since": 1788180000,
      "action": {"kind": "retry", "label": "重新查询费率", "endpoint": "/ai-trade/fee-rates/refresh"}
    },
    {
      "key": "intelReader", "label": "情报正文取证", "state": "degraded",
      "reading": "抓取 0 / 96 成功 · 缓存 29 篇 / 正文 0 字节",
      "reason": "本机 DNS 把公网域名解析成了合成地址（198.18.0.0/15）",
      "since": 1788326000,
      "action": {"kind": "settings", "label": "调整解析策略", "target": "intel-reader"}
    },
    {
      "key": "privateQuota", "label": "账户接口配额", "state": "ok",
      "reading": "12.7 次/分 · 持仓快照共享：开（TTL 4s）",
      "reason": "", "since": 0, "action": null
    },
    {
      "key": "decisionModel", "label": "决策模型", "state": "degraded",
      "reading": "近 24 小时：决策失败 5 次 · 整理失败 3 次",
      "reason": "最近一次：TimeoutError(30s)", "since": 1788341056, "action": null
    }
  ]
}
```

**装配点唯一**：每一行的 `state` / `reading` / `reason` 都由一个纯函数从既有读数算出，**绝不在前端重算**：

| 行 | 数据来源（已存在，本轮只读） |
|---|---|
| `feeRates` | `AiTradeManager` 当前分组的 `FeeRateSnapshot` + A-2 的 `_errors` |
| `intelReader` | `repo.document_health()` + B-2 的三个加法键 |
| `privateQuota` | D-3 的 `CallStats.wire()` + `settings.ai_trade_positions_snapshot_ttl_s` |
| `decisionModel` | `decider.summary_health_wire()` + 新增的 `decide` 失败计数（复用同一份 `health_wire`） |

### E-2｜前端：`apps/web/components/Settings/TruthSourcesCard.tsx`

设计要点（这是本轮「顶级设计 / 人机交互最佳实践」的落点，逐条可验收）：

1. **默认折叠为一行摘要**：`真值源 4 项 · 2 项降级`，点击展开四行明细。一张常驻的四行诊断表会很快变成背景噪声——**默认状态必须是「一切正常时几乎不占视觉预算」**。
2. **状态不靠颜色单独承载**：每行是「色点 + 文字标签（正常 / 降级 / 未知 / 已关闭）+ 读数」。色觉障碍与截图分享两个场景都要能读。
3. **`reason` 是一句人话，不是一个码**。`51000` 这样的原始码只出现在展开后的 `<details>` 里，第一屏给的是「交易所拒绝了费率查询」。
4. **每一行的 `action` 都必须真的点得下去**（第 1 轮 `8451163` 的 422 补救三条已经立了这个规矩）：`retry` 发一个真实存在的端点，`settings` 跳一个真实存在的锚点。**没有可行动作时 `action` 为 `null`，界面上就不画按钮**——一个点了没反应的按钮比没有按钮更糟。
5. **文字排版遵循既有的《工作台文字零折行契约》（`fd64388`）**：读数行整块省略号，`reason` 这类必须读全的散文显式允许换行。
6. **后端下发的文案里绝不出现 `**`**。仓库已为此复发 4 次（最近一次是第 1 轮的 `liveness_text._HALT_SCOPE_TEXT`），且既有的源码门禁只扫前端、结构性看不见后端字面量。本轮**在后端也加一条会红的断言**：`test_truth_sources_text_has_no_markdown_markers`。
7. **`unknown` 与 `degraded` 必须分开**。「没在跑所以不知道」和「在跑但坏了」是两件事，压成一句话就是第 1 轮修过的那种假话（`422c5d8` 的 `arm_state`）。

**总闸**：`ai_trade_truth_sources_enabled: bool = True`。置 `False` ⇒ 端点返回 404（路由不注册）、卡片不渲染。

---

## 十、文件 / 模块变更清单

### 10.1 新增（后端 7）

| 文件 | 行数预算 | 一句话意图 |
|---|---:|---|
| `services/quant/app/okx/error_http.py` | ≤ 95 | 非 2xx 响应 → 一行可诊断描述（纯，绝不抛）**+ `_redact` 的新宿主**（评审 P1-2：原宿主 `accounts.py` 反向 import 了 `private_client`，在那里取用即 import 期环） |
| `services/quant/app/okx/inst_family.py` | ≤ 45 | `instId` → `instFamily`（纯；段数 ≠ 3 即 `None`） |
| `services/quant/app/okx/positions_cache.py` | ≤ 170 | 账户级持仓快照：单飞 + TTL + 指纹分桶 + 写即作废 |
| `services/quant/app/okx/call_stats.py` | ≤ 130 | 按 `(method, path)` 的调用 / 错误滑窗计数 |
| `services/quant/app/ai_auto/intel/netpolicy.py` | ≤ 110 | 解析结果三分类 + 合成地址段（纯，叶子模块） |
| `services/quant/app/api/truth_sources.py` | ≤ 150 | `GET /system/truth-sources`（只读、永不 5xx） |
| `services/quant/app/ai_auto/truth_wire.py` | ≤ 160 | 四行真值源的**唯一**装配点（纯函数，端点与 WS 共用） |

### 10.2 新增（前端 3 + 桌面 1）

| 文件 | 一句话意图 |
|---|---|
| `apps/web/components/Settings/TruthSourcesCard.tsx` | 四行真值源卡片（默认折叠） |
| `apps/web/lib/truthSources.ts` | wire 类型 + 状态/文案的**唯一**前端派生点 |
| `apps/web/components/Intel/IntelReaderBanner.tsx` | `zeroOkStreak` 横幅 + 被拦域名抽屉 |
| `desktop/src/lib/rotating-log.js` | 带上限的日志轮转写入器（零依赖） |

### 10.3 修改（后端 12）

| 文件:锚点 | 改动 | 轴 |
|---|---|---|
| `okx/private_client.py:244-249` | 4xx/5xx 的 message 换成 `describe_http_error`；`_note_link_fail` 按状态分级 | A-1 / A-3 |
| `okx/private_client.py:204` | `_get` 出入口接 `call_stats.note` | D-3 |
| `okx/trade_client.py:74-79` | 同 A-1 / A-3（POST 侧） | A-1 / A-3 |
| `okx/trade_client.py:283-301` | `get_trade_fee` 改参数阶梯（`instFamily` → `instType` → `{}`） | A-2 |
| `okx/trading.py:520-530` | `fetch_live_positions` 增 `use_cache` / `force` 形参（默认**不缓存**，调用方显式选择） | C-1 |
| `okx/trading.py`（4 处写） | 写成功后 `invalidate(fp)` | C-3 |
| `okx/trading.py:572-586` | `fetch_trade_fee` 把阶梯的 `_errors` 透传给调用方 | A-2 |
| `okx/link_state.py` | 增 `note_rejected` + `rejected_4xx` / `last_rejected_ts` / `last_rejected_status` 三个加法字段（**都不参与 `overall`**；评审 P1-3：v1 的 `note_ok` 会清零 `consecutive_failures`） | A-3 |
| **`okx/accounts.py:67-74`**（**评审 P1-2 补**） | `_redact` 迁到 `error_http.py`，此处改为 re-export；`trading.py:23` 的 import 一字不改 | A-1 |
| **`okx/types.py`**（**评审 P0-4 补**） | 新增 `FeeAttemptError` / `FeeFetchOutcome` 两个 frozen dataclass（叶子模块，零依赖） | A-5 |
| **`okx/trading.py::_normalize_trade_fee`（:611-629）**（**评审 P1-6 补**） | `source` 从硬编码 `"exchange"` 改为 `f"exchange:{rung}"`；`rung=None` ⇒ 逐字保留 `"exchange"`。**这是 `source` 的唯一产出点**，只改 `fee_rates.py` 不会让新值出现 | A-2 |
| **`db/ai_intel_evidence_repositories.py::document_health`（:209-223）**（**评审 P1-6 补**） | 四个新读数的**唯一**装配点；`ai_intel_rounds` 的聚合合并进同一次调用，不新开第二个 repo 函数 | B-2 |
| **`api/schemas_intel_evidence.py:66-74`**（**评审 P0-2 补，阻断级**） | `IntelDocumentHealthOut` 补五个带默认值的字段。**不补 = 四个加法键被 FastAPI 静默剔除** | B-2 |
| `okx/rest_client.py:259` | 接 `call_stats.note` | D-3 |
| `ai_auto/intel/reader.py:144-172` | `guard_hop` 四值判定 + `_fetch_one` 对端复核 | B-1 |
| `ai_auto/fee_rates.py:30-62,143` | `source` 值域扩到 3 个 + `last_error` 字段 | A-2 / A-4 |
| `ai_auto/manager.py:477-489` | `_bounded_fee_rate` 记录阶梯失败原因；`fetch_live_positions(force=True)` | A-2 / C-2 |
| `ai_auto/session.py:1170` | `_read_exchange_position` 走 `_read_positions_fresh()` | C-2 |
| `live_auto/executor.py:175-176,648,671,676-700` | 拆 `_read_positions` / `_read_positions_fresh`；`_last_private_write_wall`；幻影判定加条件 | C-1 / C-2 / C-4 |
| `live_auto/manager.py:227` | `fetch_live_positions(force=True)` | C-2 |
| `api/ai_intel.py:278-287` | 文档健康 wire 增 4 个加法键 | B-2 |
| `api/okx_link.py:33-40` | `_snapshot()` 增 `callStats` 加法键 | D-3 |
| `main.py:58` | `httpx`/`httpcore` 级别；注册 `truth_sources` 路由 | D-2 / E-1 |
| `config.py` | 12 个新配置项 + 启动期校验（TTL < poll_interval） | 全轴 |

### 10.4 修改（前端 5 + 桌面 1 + 仓库根 1）

| 文件 | 改动 | 轴 |
|---|---|---|
| `apps/web/lib/aiTradeCost.ts:117-125` | `describeFeeRates` 值域 2→4（含老值 `exchange`）+ `lastError` 尾注 | A-4 |
| `apps/web/lib/intelTypes.ts:145-151` | `IntelDocumentHealth` 增 4 键（全部 optional，老回执不炸） | B-2 |
| `apps/web/lib/intelEvidence.ts` | 新增 `describeReaderHealth` 三态 | B-2 |
| `apps/web/components/Settings/IntelReaderSettings.tsx` | 渲染抓取健康行 + 「0 字节」结论句 + 解析策略选择器 | B-1 / B-2 |
| `apps/web/components/Settings/SettingsPanel.tsx` + `SettingsNav.tsx` | 挂载 `TruthSourcesCard` | E-2 |
| `apps/web/components/Agent/AiTradeLaunchModal*.tsx` | 「成本」分组新增费率来源行 | A-4 |
| `desktop/src/main.js:43-49` | `openLogStreams` 改用 `openRotating` | D-1 |
| **`desktop/scripts/config-passthrough.test.mjs`**（**评审 P0-5 补**） | 探针表补 `AI_TRADE_LOG_HTTP_CLIENT_LEVEL` 与一个 `OKX_` 键——**产品代码零改动**，补这两行是为了将来有人把前缀白名单改窄时立刻判红 | D-2 |
| **`.claude-index/config.md`**（**评审 P1-1 补**） | 阈值偏离登记（见 §10.5） | 全轴 |
| `.env.example`（仓库根） | 12 个新键 + 2 个桌面端键**双向登记**（键 + 一行注释） | 全轴 |

### 10.5 阈值偏离登记（评审 P1-1 新增，本轮必做）

CLAUDE.md §二原文：「所有阈值都允许根据项目实际情况调整，但**调整必须在 `.claude-index/config.md`
中显式记录**，并在评审中说明理由，不要靠默认值偷偷放宽。」v1 的 §十只字未提这张表，而本轮往
**五个已越 800 行红线的文件**上继续加行。仓库里每一轮都补录了这张表（最近一次见
`.claude-index/config.md:198` 起），漏掉它就是把纪律变成惯例。

| 文件 | 当前行数 | 本轮增量 | 下一次触碰应先还的债 |
|---|---:|---|---|
| `services/quant/app/live_auto/executor.py` | 1,059 | 轴 C：`_read_positions_fresh` 属性、`_last_private_write_wall`、`_on_reconcile_ok` 的一条新条件。**判定全在新模块 `okx/positions_cache.py`**，此处只有接线 | 既有指示不变：resting-order 路径整体外移到 `live_auto/resting_flow.py` |
| `services/quant/app/ai_auto/session.py` | 3,756 | 轴 C 一处（`_read_exchange_position` 换缝）+ 轴 E 一处（`_status_payload` 加一键）。**零纯核心** | 既有指示不变：按「决策编排 / 状态·事件 / 维护任务」拆文件 |
| `services/quant/app/ai_auto/manager.py` | 2,211 | 轴 A 一处（`_bounded_fee_rate` 带回 errors）+ 轴 C 一处（`force=True`）。**零纯核心** | 既有指示不变：按「准入 / 生命周期 / 会话装配」三块拆 |
| `services/quant/app/config.py` | 1,470 | 12 个新键 + 一条跨字段校验器。前缀硬约束（`AI_TRADE_` / `OKX_`）见 §十三 | 既有指示不变：按家族拆 `config/` 包并保持单一 `Settings` 聚合 |
| `services/quant/app/okx/trading.py` | 844 | `_normalize_trade_fee` 的 `rung` 形参、四处 `invalidate`、`fetch_trade_fee_ex`。**新纯核心一律外置**（`error_http.py` / `inst_family.py` / `positions_cache.py` / `types.py`），正是 `error_text.py` 当年的同一条取舍 | 把 `fetch_*` 家族按「读 / 写」拆成两个模块 |

**未越线但需预警**：`app/ai_auto/intel/reader.py` 326 → 约 400（B-1 的四值判定 + 对端复核）。
纯核心（`classify_addr` / `classify_resolution`）已按 F-5 外置到 `intel/netpolicy.py`，此处只留接线。

> **登记的偏离**：`live_auto/executor.py` 与 `live_auto/manager.py` 被 AI-trade 与 live-auto **两条车道共用**（黑板已登记：`LiveExecutor 与 live_auto 共用同一 settings`）。轴 C 的改动因此同时作用于 live-auto。这是**有意的**（同样的 5× 压降对 live-auto 一样成立），并由 `ai_trade_positions_snapshot_ttl_s = 0` 一键回退两条车道。评审须确认这一点。

---

## 十一、接口设计

### 11.1 REST（新增 2、扩展 2）

| 方法 · 路径 | 说明 | 失败语义 |
|---|---|---|
| `GET /api/system/truth-sources` | 四行真值源快照（只读、零 IO） | **永不 5xx**；取不到即该行 `state="unknown"` |
| `POST /api/ai-trade/fee-rates/refresh` | 人工重取一次官方费率（走 A-2 的完整阶梯） | 成功 200 + 新快照；阶梯全败 200 + `source="config"` + `lastError`；未在跑 409 `AI_TRADE_NOT_RUNNING` |
| `GET /api/okx/connectivity` | **扩展**：增 `callStats` 加法键 | 不变（永不 502） |
| `GET /api/ai-trade/intel/documents/health` | **扩展**：增 `fetchAttempt` / `fetchOk` / `blockedByCode` / `zeroOkStreak` | 不变 |

- 两个新端点都**不声明 `response_model`**（`api/okx_link.py:8-11` 的成文纪律；本仓已为 `edgeSource` 被静默剔除付过一次代价）。
- **【评审 P0-2】但这条纪律对上表第 4 行的既有端点不成立，而 v1 没有说。**
  `GET /intel/documents/health` **已经声明了** `response_model=IntelDocumentHealthOut`
  （`api/ai_intel.py` 的装饰器那一行）。给它加四个加法键**必须同步改 schema**，
  且**不许**用「去掉 `response_model`」的办法绕过——那个 schema 正被前端类型文件对着抄，
  去掉它是把一个静默剔除换成一个静默漂移。见 §六 B-2 的两条修法与门禁 G-B10。
- `POST /fee-rates/refresh` 是**私有 GET 的触发器**，不触碰任何写路径，因此**不要求** `live_trading_enabled`（与 `/okx/connectivity/probe` 同源判断）。加单飞锁，避免连点造成探活风暴。

### 11.2 WS（`ai-trade` 通道，加法键）

`status` 事件的 `data` 增一个**可选**顶层键：

```jsonc
"truthSources": { "scope": "process", "trackedCount": 3,
                  "degradedCount": 2, "keys": ["feeRates", "decisionModel"] }
```

**【评审 P1-4】WS 上只带三行，不是四行——`intelReader` 由 REST 独占。**

`_status_payload()` 是一个**同步**方法（`session.py:3397` `def _status_payload(self) -> dict`），
而 `intelReader` 那一行的真源 `repo.document_health()` 是 `async` DB 读——同步方法里没有 `await`
的位置。硬要塞进去只有三条路，每一条都更糟：把 `_status_payload` 改成 async（它有十几处同步
调用点）、在里面起一个 `create_task`（一个会在事件循环里制造竞态的读数）、或者缓存一份可能
任意陈旧的副本（第五条静默降级）。

故：

- WS 的 `degradedCount` 的**分母是 3**（`feeRates` / `privateQuota` / `decisionModel`），
  并显式下发 `trackedCount: 3`——**绝不让前端以为它是 4**。顶栏芯片的文案因此写
  「3 项真值源中 2 项降级」，点进去的卡片才是完整四行。
- `intelReader` 只在 `GET /api/system/truth-sources` 上出现。卡片是用户主动点开的，那一次多查
  一次库完全值得；而 status 事件每 5 秒一次 × N 个成员，绝不能带 DB 读。
- 只下发计数与键名，**不下发文案**——文案的唯一装配点在 `truth_wire.py`，由 REST 取。这样避免「同一句话两处装配、必然漂移」。
- 前端 `store.patchAiTradeMemberStatus` 是**整体浅合并**（`session.py:_note_tick_recovered` 的
  docstring 有成文警告），故本键必须随 `_status_payload()` **每次都带**，不能只在降级时带——
  否则一次恢复会把它留在旧值上。

### 11.3 CLI / 脚本

`services/quant/scripts/probe_trade_fee.py`（新增，**开发者工具，不进产物**）：

```
用法: python -m scripts.probe_trade_fee --account-id 1
作用: 读 okx_accounts 一行凭证，按 A-2 的三跳阶梯各发一次 GET，逐跳打印
      HTTP 状态 + OKX code/msg（经 _redact），不下任何单、不写任何库。
输出: 直接贴进本 spec §附录 C，作为 A-2 的验收证据。
```

---

## 十二、数据模型

### 12.1 DB：**零迁移**

本轮不新增表、不新增列、不改任何列类型。`ai_intel_rounds.reader_attempt` / `reader_ok` / `quotes_proposed` / `quotes_verified` 四列**早已存在并且在写**（E-5 的数字就是从它们读出来的），本轮只是给它们接上第一个读点。

### 12.2 进程内结构（新）

```python
# okx/positions_cache.py
PositionsSnapshot(rows: tuple[dict, ...], taken_wall: float)
PositionsCache._entries: dict[str, PositionsSnapshot]          # key = 凭证指纹
PositionsCache._inflight: dict[str, asyncio.Future]

# okx/call_stats.py
CallStats._buckets: dict[int, dict[tuple[str, str], EndpointStat]]   # key = 分钟槽
CallStats._total: dict[tuple[str, str], EndpointStat]

# ai_auto/fee_rates.py（扩展既有 dataclass）
FeeRateSnapshot.source: str        # "exchange:family" | "exchange:type" | "exchange"(老) | "config"
FeeRateSnapshot.last_error: tuple[str, str] | None      # (code, msg)；None = 没失败过
```

### 12.3 wire 形状（新增 / 扩展）

```jsonc
// GET /api/ai-trade/intel/documents/health   （扩展，5 个加法键；**schema 必须同改**，见 P0-2）
{ "cachedDocs": 29, "bodyBytes": 0, "oldestFetchedTs": 0, "lastPrunedTs": 0, "readerEnabled": true,
  "fetchAttempt": 96, "fetchOk": 0, "zeroOkStreak": 16,
  "blockedByStatus": {"blocked": 29},                      // 持久，来自 ai_intel_documents.status
  "rejectCodesSinceStart": {"private_host": 29},           // 进程内计数器，重启归零
  "sinceProcessStart": true }                              // 上一行会归零这件事，必须自己说出来

// GET /api/okx/connectivity   （扩展，1 个加法键）
{ "channels": [...], "clockOffsetMs": 0, "clockSyncedTs": null,
  "callStats": { "windowMinutes": 15,
                 "top": [{"method":"GET","path":"/api/v5/account/positions",
                          "calls":190,"perMin":12.7,"errors429":0,"errors4xx":0,"errors5xx":0}] } }

// aiStats.feeRates —— 成功态（扩展，1 个加法键 + source 值域）
{ "makerBps": 2.0, "takerBps": 5.0, "level": "Lv1", "ruleType": "normal",
  "source": "exchange:family", "rebate": false, "stale": false,
  "lastError": null }

// aiStats.feeRates —— **降级态**（评审 P0-4 新增；这是本机今天 100% 会走到的那一格。
// v1 只给了成功态，而 `FeeRateCache.wire()` 在无快照时整段返回 null ⇒ lastError 没有宿主）
{ "makerBps": 2.0, "takerBps": 5.0, "level": "", "ruleType": "",
  "source": "config", "rebate": false, "stale": false, "attempted": true,
  "lastError": {"rung": "type", "httpStatus": 400,
                "code": "51000", "msg": "Parameter instId error"} }

// aiStats.feeRates —— 还没轮到取数：**仍为 null**（不许提前告知一件当时并不成立的事，
// 这是 `FeeRateCache.attempted` 的既有纪律，本轮一字不改）
```

**老回执兼容**：四处扩展全部是**可选加法键**，前端类型一律 `?`，缺席即回到今天的渲染。`source` 的老值 `"exchange"` 必须继续被 `describeFeeRates` 识别——生产库里已经落了这个值的历史记录（虽然本机因 E-3 一条都没有，但其它安装可能有）。

---

## 十三、配置项与总闸

**12 个新键。**每一行的「回退档」都必须让对应链路**逐字节**回到 `8451163`。

| # | 键 | 类型 · 默认 | 回退档 | 轴 | 作用 |
|---|---|---|---|---|---|
| 1 | `okx_http_error_detail_enabled` | `bool = True` | `False` | A-1 | 4xx/5xx 的异常 message 是否带 OKX 的 `code`/`msg` |
| 2 | `ai_trade_fee_rate_family_fallback_enabled` | `bool = True` | `False` | A-2 | `trade-fee` 是否走 `instFamily` → `instType` 阶梯 |
| 3 | `okx_link_treat_4xx_as_link_failure` | `bool = False` | `True` | A-3 | 非 429 的 4xx 是否计入链路健康的连续失败 |
| 4 | `ai_trade_fee_rate_origin_visible` | `bool = True` | `False` | A-4 | 费率来源是否在弹窗 / 成本卡上渲染 |
| 5 | `ai_trade_intel_reader_resolver` | `str = "auto"`（`auto`\|`strict`） | `"strict"` | B-1 | 解析闸是二值还是四值 |
| 6 | `ai_trade_intel_reader_synthetic_cidrs` | `str = ""` | `""` | B-1 | 追加的合成地址段（逗号分隔 CIDR） |
| 7 | `ai_trade_intel_reader_health_visible` | `bool = True` | `False` | B-2 | 抓取健康行 / 横幅是否渲染 |
| 8 | `ai_trade_intel_reader_zero_streak_warn` | `int = 3`（1–50） | `50` | B-2 | 连续多少轮 `reader_ok=0` 升横幅 |
| 9 | `ai_trade_positions_snapshot_ttl_s` | `float = 4.0`（0–30） | `0.0` | C | 账户持仓快照 TTL；`0` = 完全旁路 |
| 10 | `okx_call_stats_enabled` | `bool = True` | `False` | D-3 | 是否统计按端点调用量 |
| 11 | `ai_trade_log_http_client_level` | `str = "WARNING"` | `"INFO"` | D-2 | `httpx`/`httpcore` 的日志级别。**【评审 P0-5】键名带 `AI_TRADE_` 前缀是硬约束**——`desktop/src/lib/config.js:80-82` 的透传白名单按前缀放行，v1 的 `LOG_HTTP_CLIENT_LEVEL` 在打包桌面端里会被静默丢弃，那把闸按不下去 |
| 12 | `ai_trade_truth_sources_enabled` | `bool = True` | `False` | E | 真值源端点与卡片是否装配 |

**桌面端另有 2 个环境变量**（不属于后端 `Settings`，故**不受**前缀白名单约束）：
`ARAGON_LOG_MAX_BYTES`（默认 `16777216`，`0` = 不轮转）、`ARAGON_LOG_KEEP`（默认 `3`）。
**【评审 P0-5】二者的取值链必须是「`userData/agent.env` → `process.env` → 内建默认」三级回落**
（见 §八 D-1）：只读 `process.env` 时，一个从图标启动的打包进程永远读不到用户设的值，
这把闸同样按不下去。

**前缀合规性（评审 P0-5 逐个核对）**：

| 键 | 前缀 | 打包后端可达？ |
|---|---|---|
| `okx_http_error_detail_enabled` / `okx_link_treat_4xx_as_link_failure` / `okx_call_stats_enabled` | `OKX_` | ✅ 白名单内 |
| 其余 8 个后端键 | `AI_TRADE_` | ✅ 白名单内 |
| `ai_trade_log_http_client_level`（**改名后**） | `AI_TRADE_` | ✅ 白名单内 |
| ~~`log_http_client_level`~~（v1 的写法） | 无 | ❌ **静默丢弃** |

**另有一把既有总闸同时节制本轮轴 A（评审 P2-2 登记）**：`ai_trade_fee_rate_source_enabled`
（`config.py:569`，`manager._bounded_fee_rate` 的第一行早退）。它不是新键，但「关掉 12 把闸
即逐字节回退」这句话的前提里有它——它关着时，A-2 的阶梯**一跳都不会发**。

**启动期校验器**（`config.py` 的既有 `model_validator` 家族里加一条）：

```
ai_trade_positions_snapshot_ttl_s > 0  ⇒  必须 < live_auto_poll_interval_s
否则抛启动期配置错误，并在 message 里同时给出两个键的当前值与修法。
```

理由：TTL ≥ 盯市周期 ⇒ 存在「整轮盯市都读不到新持仓」的可能，那是一个会静默改变交易行为的配置。这条与 `alert_pending_ttl_s ≥ 2 × live_auto_poll_interval_s`（`config.py:1403-1429`）是同一族的跨字段不变式，实现风格逐字对齐。

**`.env.example` 双向登记**：仓库根 `.env.example` 里每个键一行 + 一句注释。**双向**的含义是：既不许有键没登记，也不许登记了不存在的键。第 1 轮已因为「`.env.example` 无用例扫描，故漏登记是静默的」付过一次代价（黑板 `30ff91b` 条目），本轮补一条门禁 `G-Env`：AST 读出 `Settings` 的全部字段名，与 `.env.example` 解析出的键名求两侧差集，任一非空即红。

---

## 十四、测试与验收

### 14.1 回归基线（实施前必须先跑一遍并记录）

| 套件 | 基线 | 备注 |
|---|---|---|
| 后端 `pytest` | **5,074 passed / 8 failed** | 8 条红全部与本轮无关：7 条在未跟踪的在制文件 `tests/test_factor_library_expansion.py`，1 条 `test_experience_incremental::test_g15` 是 500ms 预算的计时用例（并发跑时会超线，单独重跑即绿） |
| 前端 `vitest` | **2,598 passed / 225 files** | **必须在 `apps/web` 目录内跑**：多条 `*.source.test.ts` 用 `process.cwd()` 拼路径，`npx --prefix apps/web vitest run --root apps/web` 会让 7 条源码扫描门禁全 ENOENT 假红（黑板已登记） |
| `tsc` / `next build` / `ruff F,E9` | 全绿 | |
| 桌面 `node --test` | 全绿 | **不要用 glob 传参**（需 Node 21+，CI 钉 20；黑板已登记） |

### 14.2 后端门禁（G-A\*、G-B\*、G-C\*、G-D\*、G-E\*）

**轴 A（16 条；评审 P0-3 / P0-4 / P1-2 / P1-6 各补一组）**

| 编号 | 断言 |
|---|---|
| G-A1 | `describe_http_error(400, b'{"code":"51000","msg":"Parameter instId error"}')` == `"OKX HTTP 400 [51000] Parameter instId error"` |
| G-A2 | `describe_http_error(400, b'<html>...')` 落到第 2 档并**去掉换行**；长度 ≤ 200 + 前缀 |
| G-A3 | `describe_http_error(500, None)` / 非 UTF-8 字节 / 深嵌套 JSON ⇒ 逐字 `"OKX HTTP 500"`，且**不抛** |
| G-A4 | `okx_http_error_detail_enabled=False` ⇒ GET 与 POST 两处的 message 逐字为 `f"OKX HTTP {status}"` |
| G-A5 | 429 分支不受影响：`tests/test_okx_signing.py:95` **未经修改仍绿** |
| G-A6 | `inst_family_of("BTC-USDT-SWAP") == "BTC-USDT"`；`inst_family_of("BTC-USDT") is None`（两段）；`inst_family_of("BTC-USD-260626-50000-C") is None`（五段） |
| G-A7 | 阶梯：第 ① 跳 400 ⇒ 发第 ② 跳；第 ② 跳 200 ⇒ 返回它，`source == "exchange:type"`，且**恰好发了 2 次网络请求** |
| G-A8 | 阶梯两跳全败 ⇒ 返回 `{}` 且 `_errors` 含两跳的 `(code, msg)`；`FeeRateSnapshot` 为 `None`，调用方回落配置常数（**与今天的行为逐字一致**） |
| G-A9 | `ai_trade_fee_rate_family_fallback_enabled=False` ⇒ **只发一次**且参数是今天那一组（`instType` + `instId`） |
| G-A10 | 4xx（非 429/401/403）⇒ 走 `note_rejected`：`consecutive_failures` **不增**、`last_ok_ts` **不变**、`rejected_4xx` 增 1；401 ⇒ `last_error_code == "OKX_AUTH_REJECTED"`；`okx_link_treat_4xx_as_link_failure=True` ⇒ 逐字回到今天 |
| **G-A7b**（评审 P0-3） | `should_advance` 的五格真值表逐格构造：`http_status=None` / `429` / `401` / `400` / `503` ⇒ `False/False/False/True/False` |
| **G-A7c**（评审 P0-3） | 第 ① 跳抛 `OKX_UNREACHABLE`（`http_status is None`）⇒ **恰好发了 1 次网络请求**且异常**原样上抛**（不是返回空快照）；第 ① 跳 429 耗尽 ⇒ 同上 |
| **G-A11**（评审 P0-4） | 两跳全败 ⇒ `FeeRateCache.wire()` 返回**降级形状**（`source=="config"`、`attempted is True`、`lastError.code=="51000"`、`makerBps`/`takerBps` 等于配置常数），**不是 `None`** |
| **G-A12**（评审 P0-4） | `attempted is False`（还没轮到取数）⇒ `wire()` 仍返回 `None`；`ai_trade_fee_rate_origin_visible=False` ⇒ 无快照时逐字节回到今天（返回 `None`） |
| **G-A13**（评审 P1-2） | `okx.accounts._redact is okx.error_http._redact`（同一个函数对象）；且 AST 断言 `private_client.py` / `trade_client.py` **不 import `accounts`**（循环导入门禁） |
| **G-A14**（评审 P1-6） | `_normalize_trade_fee(raw, inst_id, rung="family")["source"] == "exchange:family"`；`rung=None` ⇒ 逐字 `"exchange"`（老调用点等价） |

**轴 B（13 条；评审 P0-1 补两条、P0-2 与 P1-5 各补一条）**

| 编号 | 断言 |
|---|---|
| G-B1 | `classify_addr("198.18.0.194") == "synthetic"`；`("10.0.0.1") == "private"`；`("1.1.1.1") == "public"` |
| G-B2 | `classify_resolution(["198.18.0.1","10.0.0.1"]) == "private"`（**混合即拒**，顺序不可换） |
| G-B3 | `classify_resolution([]) == "unresolved"` |
| G-B4 | `auto` + 全 synthetic + 合法域名 ⇒ `guard_hop` 返回 `None`（放行） |
| G-B5 | `auto` + 全 synthetic + **IP 字面量主机** ⇒ 拒（`private_host`） |
| G-B6 | `strict` ⇒ synthetic 与 unresolved 都记 `private_host`，**与今天逐字节一致** |
| G-B7 | 对端复核：`server_addr = 10.1.2.3` ⇒ 正文被丢弃、`status == "blocked"`、拒因 `private_peer`；`server_addr` 取不到 ⇒ **不拒** |
| G-B8 | 重定向第 2 跳解析成 `private` ⇒ 整条链拒（每跳都过闸这件事不因三分类而失效） |
| G-B9 | `ai_trade_intel_reader_synthetic_cidrs="240.0.0.0/4,bogus"` ⇒ 合法段生效、`bogus` 被跳过并 `warning` 一次、闸**不退化为 unresolved** |
| **G-B7b**（评审 P0-1，阻断级） | `cfg.proxy="http://127.0.0.1:7890"` + 对端 `("127.0.0.1", 7890)` ⇒ **不拒**、`status=="ok"`、`peer_checked is False`。**这条用例在 v1 的设计下是红的**，它守的是「配了代理的机器上情报抓取不会 100% 失败」 |
| **G-B7c**（评审 P0-1） | `server_addr` 返回元组 `("10.1.2.3", 443)` ⇒ 正确判为 `private` 并拒（证明取了 `[0]`）；返回 `("fe80::1%eth0", 443)` ⇒ 去掉 `%scope` 后判定，**不抛** |
| **G-B10**（评审 P0-2，阻断级） | 用 `TestClient` 真的 `GET /api/ai-trade/intel/documents/health`，断言响应 JSON 里**五个新键都在**。**不许**改成断言处理函数的返回值——schema 漏改时那种写法照样是绿的 |
| **G-B11**（评审 P1-5） | `blockedByStatus` 的键取自 `ai_intel_documents.status`（`blocked`/`http_error`/…），**不含** `private_host`；`rejectCodesSinceStart` 来自进程内计数器且 wire 上带 `sinceProcessStart is True`；新建一个进程（新 `CallStats`/计数器实例）⇒ 该字典为空而 `blockedByStatus` 不变 |

**轴 C（13 条，本轮最重；评审 P1-9 / P1-10 各补一条）**

| 编号 | 断言 |
|---|---|
| G-C1 | 5 个并发 `get()` ⇒ **底层 fetch 恰好被调 1 次**，5 个调用者拿到同一份 `rows` |
| G-C2 | TTL 内第二次 `get()` 不发请求；TTL 外发 |
| G-C3 | `force=True` **必发**，且**不覆盖**缓存里的 TTL 计时（避免一次强制读把后续共享窗口整体前移） |
| G-C4 | 底层抛 `OKXError` ⇒ **不写缓存**，且 N 个等待者**各自**接住异常 ⇒ `_reconcile_fail_streak` 的推进速率与今天逐字一致 |
| G-C5 | 两组不同凭证 ⇒ 两个桶，**绝不串数据**（指纹来自 `client_pool.fingerprint`，用例直接断言两者同源） |
| G-C6 | `ai_trade_positions_snapshot_ttl_s=0` ⇒ 完全旁路，调用次数与今天逐字一致 |
| G-C7 | `_confirm_net_cleared` 的**每一次**重试都 `force=True`（用例：第 1 次读到有仓、第 2 次读到无仓 ⇒ 返回 True；若走缓存则会返回 False） |
| G-C8 | `_read_exchange_position`（暂停路径）`force=True` |
| G-C9 | `place_live_order` / `close_live_position` / `cancel_live_order` 成功后 ⇒ `invalidate` 被调；失败后 ⇒ **不调**（一次失败的写没有改变持仓） |
| G-C10 | C-4 防御纵深：人为把 `invalidate` 打桩成 no-op，喂一份 `taken_wall < _last_private_write_wall` 的空快照 ⇒ **不合成 `external_close`**，本轮只更新未实现盈亏 |
| G-C11 | 启动期校验：`ttl=6.0` + `poll_interval=5` ⇒ 启动抛配置错，message 同时含两个键名与两个当前值 |
| **G-C12**（评审 P1-10） | AST 断言 `okx/trading.py` 里 `fingerprint` **只**以 `client._fingerprint()` 的形式出现，不作为自由函数被调用（两份指纹漂移 = 跨账户串数据，本轴唯一有资金后果的实现错误） |
| **G-C13**（评审 P1-9） | 注入 `positions_fn` ⇒ **两条路径**（`_read_positions` 与 `_read_positions_fresh`）都用它，底层 `fetch_live_positions` 一次都不被调用；`api/trading.py` 的 `GET /positions` 走的是**未缓存**路径（AST 断言它不 import `positions_cache`） |

**轴 D（8 条；评审 P0-5 / P1-8 各补一条）**

| 编号 | 断言 |
|---|---|
| G-D1 | `openRotating` 写满 `maxBytes` ⇒ 产生 `x.1.log`，原文件重置；写满 3 次 ⇒ 只保留 `x.log` / `x.1.log` / `x.2.log` |
| G-D2 | 打开时既有文件已超限 ⇒ **先轮转再写** |
| G-D3 | `rename` 抛错（模拟 Windows 占用）⇒ **不抛、不丢数据**，本次继续写当前流 |
| G-D4 | `ARAGON_LOG_MAX_BYTES=0` ⇒ 行为与 `fs.createWriteStream(..., {flags:'a'})` 等价 |
| G-D5 | `log_http_client_level="WARNING"` ⇒ `logging.getLogger("httpx").level == WARNING`；`"INFO"` ⇒ `INFO` |
| G-D6 | `CallStats.note` 的键**不含** instId：`GET /api/v5/account/positions?instId=BTC-USDT-SWAP` 与 `?instId=ETH-USDT-SWAP` 落同一键；`okx_call_stats_enabled=False` ⇒ `wire() == {}` 且 `/okx/connectivity` 里**不出现** `callStats` |
| **G-D7**（评审 P0-5，阻断级） | ①`desktop/scripts/config-passthrough.test.mjs` 断言 `AI_TRADE_LOG_HTTP_CLIENT_LEVEL` 与一个 `OKX_` 键都能穿过 `backendEnv()`；②同文件断言一个**无前缀**的键（如 `LOG_HTTP_CLIENT_LEVEL`）**穿不过去**——后者是这条门禁的全部意义；③`openRotating` 的参数来自 `agent.env` 而非仅 `process.env`（喂一个假 `loadAgentEnv` 返回 `{ARAGON_LOG_MAX_BYTES:"0"}` ⇒ 不轮转） |
| **G-D8**（评审 P1-8） | LLM 侧接线：喂一次 `bigmodel` 的 `529` 与一次 `kimi` 的 `403` ⇒ `CallStats.wire()["llm"]` 里各出现一格，键是 **host** 不是 path；`ai_trade_log_http_client_level="WARNING"` 时这两格**仍然在**（证明统计不依赖日志级别） |

**轴 E（7 条；评审 P1-4 补一条）**

| 编号 | 断言 |
|---|---|
| G-E1 | 四行**恒在**（`unknown` 也要有行）；`state` 的四个取值各有一条构造用例 |
| G-E2 | 未在跑 ⇒ `feeRates` 行是 `unknown` 而**不是** `degraded`（「不知道」≠「坏了」） |
| G-E3 | `action` 为 `null` 时前端**不渲染按钮**；不为 `null` 时 `endpoint`/`target` 必须在路由表 / 锚点表里真的存在（用例直接比对 `app.routes`） |
| G-E4 | 后端下发的 `label`/`reading`/`reason` **不含 `**`**（本仓第 5 次同形复发的预防；前端另有一条对 `textContent` 的同名断言） |
| G-E5 | `ai_trade_truth_sources_enabled=False` ⇒ 路由不注册（`/system/truth-sources` 404），`_status_payload()` 里**不出现** `truthSources` 键 |
| G-E6 | `truthSources` 随**每一次** `_status_payload()` 下发（含恢复轮），用例断言恢复后 `degradedCount` 归 0 而不是留在旧值 |
| **G-E7**（评审 P1-4） | ①WS 的 `truthSources.trackedCount == 3` 且 `keys` 里**永远不出现** `intelReader`；②`_status_payload()` 是同步方法这件事被 AST 冻结（`assert not isinstance(node, ast.AsyncFunctionDef)`）——它一旦被改成 async，本轮的假设就失效了，必须当场判红；③`repo.document_health()` 抛异常 ⇒ REST 端点仍回 200，`intelReader` 行为 `state="unknown"` |

**跨轴（3 条）**

| 编号 | 断言 |
|---|---|
| G-X1 | 12 个开关全置回退档 ⇒ `_status_payload()` / 启动回执 / WS 事件里**没有任何新键**（第 1 轮 `test_a1_every_new_switch_off_adds_nothing_to_payload_status_or_events` 的同款写法，直接扩展那条用例而不是新写一条） |
| G-Env | `Settings` 字段名 ∆ `.env.example` 键名 = ∅（两侧差集都为空） |
| G-Doc | 新增的 6 个后端模块每个都有模块级 docstring，且 `ruff` 的 `D` 规则子集（若仓库已开）通过 |

### 14.3 集成 / 端到端

| 编号 | 场景 | 判据 |
|---|---|---|
| I-1 | 假 OKX（`MockTransport`）对 `trade-fee?instType=SWAP&instFamily=BTC-USDT` 回 200，对带 `instId` 的回 400 ⇒ 完整启动流程后 `aiStats.feeRates.source == "exchange:family"` | 端到端，含 `manager._gather_context` |
| I-2 | 同上但两跳都回 400 ⇒ 启动仍**成功**（费率失败绝不阻断启动），弹窗回执含 `lastError` | |
| I-3 | 5 成员分组跑 60 秒（假时钟）⇒ `positions` 底层调用次数 ≤ `60/5 + 2`（而不是 `5 × 60/5`） | 轴 C 的量化验收 |
| I-4 | 假解析器全返回 `198.18.x` + 假 HTTP 回 200 HTML ⇒ 一轮采集后 `reader_ok > 0`、`ai_intel_documents.status == "ok"`、`body_chars > 0` | 轴 B 的量化验收 |
| I-5 | 同上但 `resolver="strict"` ⇒ `reader_ok == 0`，**与今天逐字一致** | |

### 14.4 真机人工验收（打包产物，6 步）

> 这一节**不能**由用例代替：本轮四条根因里有三条只在这台机器的网络拓扑下才成立。
>
> **【评审 P2-5】本节在 §16.1 的八步全部合入后一次性执行**，不要按步拆到各自的提交里
> ——第 6 步（老包→新包续跑）本来就要求整轮装好。各步之间的失败判据见下方每一条的括号。

1. **`trade-fee` 阶梯**：跑 `python -m scripts.probe_trade_fee --account-id <id>`，把三跳的 HTTP 状态 + OKX `code`/`msg` 贴进 §附录 C。**若第 ① 跳仍非 200，本轮 A-2 的验收判据变为「`lastError` 里出现了 OKX 的真实 `code`/`msg`」，并按该 `code` 修正 `inst_family_of` 后重跑一次。**
   **【评审 P0-4】本步的硬判据不是脚本输出，而是界面上那一句人话**：启动一次分组，
   启动弹窗的「成本」段必须能读到
   `本地保守常数：maker 2.00 / taker 5.00 bps —— 交易所拒绝了费率查询（<真实 code> <真实 msg>）`
   或者它的成功态。**读不到这句话，轴 A 就没做完，无论用例多绿。**
2. **情报正文**：启动一个分组，等一次采集轮，确认 `ai_intel_rounds.reader_ok > 0` 且 `ai_intel_documents` 出现 `status='ok'` 且 `byte_len > 0` 的行。
   **【评审 P0-1】本步必须跑两遍**：一遍在当前的 fake-IP TUN 拓扑下（`proxy_url` 为空），
   一遍在 `PROXY_URL=http://127.0.0.1:<本机代理端口>` 下。**第二遍同样必须 `reader_ok > 0`**
   ——它验的是对端复核没有在另一类拓扑上复现 R-2。第二遍跑完记得把 `PROXY_URL` 改回去。
3. **配额**：跑 30 分钟，用 `GET /api/okx/connectivity` 读 `callStats`，确认 `positions` 的 `perMin` ≤ 16（今天 63.5）。
4. **日志**：确认 `backend.log` ≤ 16 MB 且存在 `backend.1.log`；`grep -c httpx backend.log` 应接近 0。
5. **真值源卡片**：四行都能读；把网线拔掉 2 分钟，确认 `privateQuota` / `decisionModel` 两行转成 `degraded` 且给出人话原因；恢复后自动回 `ok`。
6. **老包 → 新包续跑**：用老包起一个带真实持仓的分组，换新包重启，确认冷续跑成功、持仓被认领、轴 C 的 `force=True` 前置读拿到的是新鲜读数。

---

## 十五、风险与缓解

| # | 风险 | 等级 | 缓解 |
|---|---|---|---|
| R-a | **轴 C 的缓存让某个成员用陈旧持仓做出交易决策** | **高** | 三重：①任何私有写成功即作废（C-3）；②`taken_wall < _last_private_write_wall` 时禁止裁决幻影平仓（C-4，构造性，不依赖①被正确调用）；③TTL 上界被启动期校验器钉死在 `< poll_interval`。另加 G-C7/G-C8：两个有交易后果的读点强制穿透 |
| R-b | **轴 B 放松了 SSRF 防护** | **高** | §六 B-4 的攻击面逐条对照：`auto` 档在 TUN 下净强度不低于今天、非 TUN 下严格更高（多一道对端复核）。`strict` 档一键回到今天。真实放松只发生在「攻击者已控制解析器」的前提下，而那时任何 DNS 判据都已失效 |
| R-c | **A-2 的 `instFamily` 假设是错的** | 中 | 阶梯设计使得假设错误时仍能取到账户通用档；A-1 会把交易所的真实原因写进日志与界面；§14.4 第 1 步把「假设错了怎么办」写成了验收流程的一部分而不是一个意外 |
| R-d | **D-2 压掉 httpx 日志之后，下一次诊断失去线索** | 中 | D-3 的 `callStats` 是补偿；且被压掉的 185 万行里没有一行是唯一信息源（§八 D-2 的评估）。`log_http_client_level="INFO"` 一键恢复 |
| R-e | **`live_auto` 车道被轴 C 顺带改变** | 中 | 已在 §10.4 显式登记为「有意的共用改动」；`ttl=0` 一键同时回退两条车道；live-auto 的三个消费点与 ai-trade 一一对应，分级规则完全相同 |
| R-f | **`link_state` 新增 `rejected_4xx` 打翻既有链路面板用例** | 中 | 加法键 + 老快照缺键即 0；`api/okx_link.py` 本就禁止 `response_model`。实施前先跑一遍 `tests/test_okx_link*.py` 记录基线 |
| R-g | **`_read_positions` 拆成两个方法后，测试注入缝只覆盖一半** | 中 | 明确写进 §七 C-2：`positions_fn` 必须同时覆盖两个方法。G-C7 会在注入缝漏接时直接变红（它断言的正是「第二次重试读到了不同的值」） |
| R-h | **`describe_http_error` 在异常处理路径上抛** | 中 | 「绝不抛」写进签名 docstring 并由 G-A3 用非 UTF-8 / 深嵌套 / `None` 三组输入冻结 |
| R-i | **轮转在 Windows 上遇到文件占用导致日志中断** | 低 | G-D3：rename 失败即静默继续写当前流，下次再试。日志轮转器绝不能成为进程崩溃的原因 |
| R-j | **12 个开关里有键忘了登记进 `.env.example`** | 低 | G-Env 双向差集门禁（第 1 轮为此付过一次代价） |
| R-k | **真值源卡片变成又一块没人看的面板** | 低 | 默认折叠为一行摘要；只有 `degradedCount > 0` 时摘要才带颜色；每行都有可点的动作 |
| **R-l**（评审 P0-1） | **B-1c 的对端复核在另一类拓扑（显式代理）上复现 R-2 的病灶** | **高** | `cfg.proxy` 非空即整条跳过并如实标 `peer_checked=False`；G-B7b 在 v1 的设计下是红的，它是这条风险的构造性门禁 |
| **R-m**（评审 P0-2 / P1-6） | **加法键被 FastAPI / 手抄清单静默剔除**（本仓第 2 次同形） | **高** | schema 同改 + G-B10 用 `TestClient` 判「真的到达了客户端」；§10.3 补齐两个「唯一真源」文件 |
| **R-n**（评审 P0-5） | **声明好的回退开关在打包产品里按不下去**（前缀白名单 / 启动顺序） | **高** | 键名改前缀 + `agent.env` 三级回落 + G-D7 的**反向**断言（无前缀的键必须穿不过去） |
| **R-o**（评审 P0-3 / P0-4） | **「交易所到底说了什么」这条本轮的核心产出在真实失败路径上无处落地** | **高** | `http_status` 加法字段 + `should_advance` 纯谓词 + `FeeFetchOutcome` + `wire()` 降级形状，四处缺一不可；§14.4 第 1 步的真机判据直接读那句人话 |
| **R-p**（评审 P1-3） | **A-3 用一个新的假阴性换掉旧的假阴性**（`note_ok` 清零连续失败计数） | 中 | 改用中性的 `note_rejected`：不动 `consecutive_failures`、不动 `last_ok_ts` |

---

## 十六、实施顺序与回滚

### 16.1 顺序（**A-1 必须最先**）

```
1. A-1  错误描述 + OKXPrivateError.http_status + _redact 迁到 error_http
        （+ G-A1..A5、G-A13）              ← 它是 A-2 的诊断前提，且独立可合
        ⚠ 评审 P0-3/P1-2：http_status 与 _redact 的搬家**必须在这一步**，
          否则第 4 步的 should_advance 无从判、且会在那时才发现循环导入
2. D-2  httpx 降级（键名 AI_TRADE_LOG_HTTP_CLIENT_LEVEL）
      + D-3 调用统计（**五个接线点**，含两个 LLM 客户端）
      + config-passthrough 探针两行          ← 立刻让后续每一步的验证成本下降
3. D-1  桌面日志轮转（参数走 agent.env 三级回落）  ← 与 2 同批，先把 254MB 归档掉
4. A-2/A-3/A-4/A-5  费率阶梯 + 链路中性登记 + 可见性 + **失败的宿主**
        ⚠ 评审 P0-4：A-5 与 A-2 同批，不许拆到后面——A-2 单独合入等于把
          1,524 次失败的原因又丢一次
5. C-1..C-5  持仓快照共享                  ← 本轮唯一触碰交易路径的一步，单独一个提交
6. B-1..B-4  情报解析三分类 + 对端复核（**含 cfg.proxy 豁免**）
7. B-2  情报健康可见性（**schema 与 wire 同一个提交**）
        ⚠ 评审 P0-2：schemas_intel_evidence.py 与 ai_intel.py 分开提交，
          中间那个提交是绿的、也是错的
8. E-1/E-2  真值源端点 + 卡片              ← 它消费前面所有轴的读数，必须最后
```

第 5 步（轴 C）**必须单独成一个提交**，且提交信息里写清「唯一触碰真实交易路径的改动 + 三重防护 + 一键回退键名」。其余步骤可按轴合并提交。

### 16.2 回滚

| 想回滚什么 | 怎么做 |
|---|---|
| 单条轴 | 把该轴的开关置回退档（§十三），**无需改代码、无需重启桌面端**（配置项走 `app_settings` 的走热更新，走 `Settings` 的需重启后端——本表第 3/9/11/12 项需重启，其余热更新） |
| 整轮 | 12 个开关全置回退档 ⇒ G-X1 保证 wire 与 `8451163` 逐字节一致 |
| 代码级 | 8 个新文件全删 + 15 处修改按 §10.3/10.4 的锚点逐处 revert；无 DB 迁移需要撤销（§12.1） |

---

## 十七、附录

### 附录 A｜复算脚本 `evidence/audit.py`

放在 `docs/plans/ai-trade-silent-degradation-repair/evidence/audit.py`，只读、零副作用，三个子命令：

```
python evidence/audit.py log      # 全量扫 backend.log：级别 / logger / 端点×状态 / 每分钟速率
python evidence/audit.py db       # 只读 aragon.db：intel rounds / documents / quotes / alerts / app_settings
python evidence/audit.py dns      # 本机 getaddrinfo 抽样 + ipaddress 分类（复现 E-6）
```

三个子命令分别复算出本文 §一 的每一个数字。**日志路径与库路径都从 `%APPDATA%/aragon-desktop` 推导，不接受手写路径**（避免有人不小心指到别的机器上的文件后得出一份对不上的读数）。

### 附录 B｜留给评审的待决问题

| # | 问题 | 本文的选择 | 反方 |
|---|---|---|---|
| Q1 | 轴 C 的 TTL 默认值 | **4.0 s**（略小于盯市周期，保证每轮仍有一次真实读取） | 取 `poll_interval / 2 = 2.5s` 更保守，但压降只有 2×，收益减半 |
| Q2 | `synthetic` 档下要不要仍然拒绝**公网** IP 字面量 | **拒**（没有域名可判，宁可少抓一篇） | 放行，理由是公网 IP 字面量本来就过了语法闸 |
| Q3 | `POST /ai-trade/fee-rates/refresh` 要不要要求 `live_trading_enabled` | **不要求**（只读语义，与 `/okx/connectivity/probe` 一致） | 要求，理由是它读的是私有端点 |
| Q4 | 轴 D 的 `httpx` 是压到 `WARNING` 还是直接 `ERROR` | **`WARNING`**（保留 httpx 自己的告警） | `ERROR`，更干净但会丢掉 httpx 的重试告警 |
| Q5 | 真值源卡片放设置中心还是顶栏 | **设置中心**（顶栏已有链路芯片，再加一个会稀释它） | 顶栏，理由是降级时更容易被看见——但 WS 的 `degradedCount` 已经能让顶栏芯片变色，无需第二块常驻面板 |
| Q6 | A-3 把 4xx 记成 `note_ok` 是否过度 | ~~**是 `note_ok` + 独立 `rejected_4xx` 计数**~~ **【评审否决，见 P1-3】改为中性的 `note_rejected`** | 记成一个新的第三态，但那要改 `link_state` 的状态机，代价远大于收益 |

### 附录 B′｜评审对六个待决问题的裁定

| # | 裁定 | 理由 |
|---|---|---|
| Q1（TTL 默认 4.0s） | **同意本文的选择** | 4.0s 与 `poll_interval=5` 的组合保证每轮仍有一次真实读取，压降 5×；`2.5s` 把收益砍半却换不到任何新的安全性（C-3/C-4 已经覆盖了「我方写」与「忘了作废」两条路） |
| Q2（synthetic 档拒公网 IP 字面量） | **同意「拒」，但必须改写理由与用例** | 见 P1-12：在正常拓扑下这一格不可达，它守的是「解析器被污染 / 未来引入自定义 resolver」。理由不写清楚，下一轮一定会被当噪声删掉 |
| Q3（`/fee-rates/refresh` 是否要求 `live_trading_enabled`） | **同意「不要求」** | 它是只读私有 GET，与 `/okx/connectivity/probe` 同源。**但必须加单飞锁**（本文已写），且**必须在未跑时回 409 而不是静默成功**——否则用户点了没反应，又是一个「按了没用的按钮」 |
| Q4（`httpx` 压到 `WARNING` 还是 `ERROR`） | **同意 `WARNING`** | `ERROR` 会连 httpx 自己的重试告警一起吃掉，而那正是断连自愈那一轮留下的线索 |
| Q5（真值源卡片放设置中心还是顶栏） | **同意「设置中心」，并追加一条约束** | 顶栏芯片靠 WS 的 `degradedCount` 变色即可。**但那个计数的分母是 3 不是 4**（见 P1-4），顶栏文案必须写「3 项真值源中 2 项降级」，不许写「真值源 2 项降级」——后者会让用户以为四行都在被实时监视 |
| Q6（4xx 记成 `note_ok`） | **否决** | `note_ok` 会清零 `consecutive_failures`，而私有通道所有端点共用一个 key ⇒ 一个周期性的 400 能让真实链路故障永远达不到降级阈值。改用只加 `rejected_4xx` 的中性登记，`link_state` 的状态机**一行不改**（新增一个方法不等于改状态机） |

### 附录 C｜`probe_trade_fee` 输出（实施节点已填，2026-09-02）

**R-1 在真机上被证实了，而且证据是决定性的。** 命令（读用户生产库的凭证，只读、不下单）：

```
cd services/quant
DB_PATH=%APPDATA%/aragon-desktop/aragon.db python -m scripts.probe_trade_fee \
    --account-id 1 --inst-id BTC-USDT-SWAP
```

真实输出（`--inst-id ETH-USDT-SWAP` 的第二次运行**逐字同形**，两次都以退出码 0 结束）：

```
账户 #1 · instId=BTC-USDT-SWAP · instType=SWAP · instFamily='BTC-USDT'

[① instFamily] GET /api/v5/account/trade-fee  params={'instType': 'SWAP', 'instFamily': 'BTC-USDT'}
  → HTTP 200  level='Lv1' ruleType='normal'
     maker='' taker='' makerU='-0.0002' takerU='-0.0005'

[② instType] GET /api/v5/account/trade-fee  params={'instType': 'SWAP'}
  → HTTP 200  level='Lv1' ruleType='normal'
     maker='-0.0002' taker='-0.0005' makerU='-0.0002' takerU='-0.0005'

[③ instType+instId（本轮之前的写法）] GET /api/v5/account/trade-fee  params={'instType': 'SWAP', 'instId': 'BTC-USDT-SWAP'}
  → HTTP 400  code=OKX_HTTP_ERROR  msg=OKX HTTP 400 [50016] instId and instType don't match

结论： 至少有一跳成功，阶梯可以取到官方档位
```

**三件事同时被这份输出确定下来：**

1. **那 1,524 次 400 的真实原因是 `50016 instId and instType don't match`。** §二 R-1 的推断
   （「`SWAP` 用的是 `instFamily` 而不是 `instId`」）**成立**，且交易所自己一直在这么说——
   只是 A-1 之前我们把它整段丢掉了。这也是 A-1「必须最先做」那条排序的直接兑现：没有 A-1，
   这份附录里只会有三行 `OKX HTTP 400`。
2. **阶梯的第 ① 跳直接成功**，`source` 因此是 `exchange:family` 而不是回落档。这个账户的真实
   档位是 **Lv1，makerU −0.0002 / takerU −0.0005 ⇒ maker 2.0 bps / taker 5.0 bps**。
3. **真实档位与本地保守常数恰好相等**（`ai_trade_maker_fee_bps=2.0` / `ai_trade_taker_fee_bps=5.0`）。
   这一条必须被诚实地说出来，且**它不削弱本轮的价值，反而定义了本轮的价值**：在这次运行之前，
   「常数等于真实档位」是一个**没有任何人验证过的巧合**；三个星期里成本模型、`edge` 门禁与净额
   扣减都跑在一个碰巧对的数上，而系统无法回答「它对不对」。本轮之后这个问题有了答案，且账户
   一旦升到 Lv2/Lv3、或开启 OKB 抵扣、或换成 USDC 保证金合约，系统会**当场**知道。

> **一句话**：本轮修好的不是「费率数值错了」，而是「**没有任何一处能告诉你费率数值对不对**」。

### 附录 D｜本轮登记的既有事实（供下一轮直接引用，不必重新勘察）

| 编号 | 事实 |
|---|---|
| F-1 | `fetch_live_positions(creds)` 是**账户级**读数，签名里没有 `instId`（`trading.py:520`） |
| F-2 | 私有令牌桶是**按账户**的（`private:{fingerprint}`，5/s，桶 5），且**已经**作用于 `trade_client`（它继承 `OKXPrivateClient._acquire_slot`） |
| F-3 | `api/okx_link.py` 明文禁止声明 `response_model`，且该端点**永不 502** |
| F-4 | `error_text.py` 的存在理由是 `trading.py` 已越过 800 行红线；新纯模块应沿用同一取舍 |
| F-5 | `intel/` 包里只有 `search.py` 与 `taxonomy.py` 是叶子；新纯核心必须做成叶子，否则会与 `service.py` 循环导入 |
| F-6 | `_status_payload()` 被前端**整体浅合并**，缺键会把别的键清成 null（`session.py:1440-1449`） |
| F-7 | `ai_intel_rounds` 的 `reader_attempt` / `reader_ok` / `quotes_proposed` / `quotes_verified` 四列早已在写，且**已有逐轮读点**（`db/ai_intel_repositories.py:139-140` → `apps/web/components/Intel/IntelRoundsTab.tsx:172-173` 的「正文 5/6」）；缺的是**聚合、跨轮趋势与告警**——一个人要发现「16 轮全零」得自己翻 16 行做加法（评审 P1-7 更正了 v1 的「没有任何读点」） |
| F-11 | `api/ai_intel.py` 的 `/intel/documents/health` **声明了 `response_model=IntelDocumentHealthOut`**（`schemas_intel_evidence.py:66`，严格 5 字段）。这是 `intel` 家族里少见的例外——同族的 `/okx/connectivity` 与 `/okx/link` 都明文禁止声明。往它加键必须同改 schema |
| F-12 | `okx/accounts.py` **反向 import 了 `private_client`**（`accounts.py:16`），故 `private_client` / `trade_client` **不得** import `accounts`。`_redact` 因此本轮迁到叶子模块 `okx/error_http.py` |
| F-13 | `desktop/src/lib/config.js:80-82` 的后端 env 透传白名单是**前缀式**的：`AGENT_ \| LIVE_ \| AI_TRADE_ \| DISCOVERY_ \| OKX_ \| SECURITY_`。任何不带这六个前缀的后端配置键，在打包桌面端里**等于不存在**（`Settings` 是 `extra="ignore"`，静默丢弃） |
| F-14 | `desktop/src/main.js` 的 `openLogStreams()` 跑在 `bootstrap()` 的**第一行**（:528-529），**早于** `agentEnv` 的加载（:150-155）。任何要在日志器里读用户配置的做法，都必须自己再 `loadAgentEnv()` 一次并包在 `try/catch` 里 |
| F-15 | `session._status_payload()` 是**同步**方法（:3397）。任何需要 DB 读的读数**进不了 status 事件**，只能走 REST |
| F-8 | `describeFeeRates` 已能区分「交易所官方档位 / 本地保守常数」，但它唯一的宿主 `ReconcileCard` 在缺键时**整段隐藏** |
| F-9 | 本机是 fake-IP 透明代理拓扑：**每一个**域名（含 `www.okx.com`）都解析进 `198.18.0.0/15`，且进程环境里**没有任何代理键** |
| F-10 | `'AsyncSession' object is not callable` 已于 `422c5d8` 修复并在 2026-09-02 01:16 重启后验证不再复现 |

---

## 十八、这一轮之后，「一直亏损」这个问题还剩什么

诚实地说清楚边界，是这份设计的一部分。

本轮**不直接**改善盈亏。它做的是**让盈亏可以被正确计算、让改善盈亏的那些机制拿到真数据**：

- 手续费占已实现亏损的 35%–100%（上一轮实测），而费率三周来一直是本地常数 ⇒ 轴 A 之后，成本口径第一次是这个账户的真实档位；
- 情报层每天在烧 token 而采纳率被正文缺失卡死 ⇒ 轴 B 之后，「情报到底有没有用」这个问题第一次可以被真实地回答（回答可能是「没用」，那也是一个有价值的答案，且下一轮可以据此关掉它省钱）；
- 下单与背景轮询争抢同一份配额 ⇒ 轴 C 之后，下单不再排在 4 份重复快照后面。

而**入场没有 edge**（黑板 `EC-3`：168 笔可评估开仓，已决样本止盈命中率 27.6% vs 盈亏平衡 33%）与**EV 分子没有水源**（`ai_prior:%` 0 行）这两条，仍然是兄弟 spec `docs/plans/ai-trade-edge-closed-loop-governor/spec.md` 的范围，**本轮明确不接手**（§三 N-1）。

**但两者有一个真实的依赖关系，必须写在这里**：那份 spec 的轴 A（用已结算往返算 `p̂ / W̄ / L̄` 喂给 `prior_ev_bps`）有一条铁律是 `costBasis == "gross"`——它要把成本从已实现盈亏里剥出来。**而成本口径今天是错的**（费率是本地常数而非真实档位）。所以：

> **本轮的轴 A 是那份 spec 的前置条件。** 先把费率变成真的，再去用已实现战绩算先验，否则算出来的先验会系统性地偏向一个方向，而且偏多少没人知道。

建议下一轮的顺序是：本 spec 全量落地 → 观察 3–5 天真实费率下的成本读数 → 再启动 `ai-trade-edge-closed-loop-governor`。


---

## 评审结论

### 判定：**有条件通过（approved with conditions）**

这份设计的**证据质量是仓库里少见的高**。它没有在任何一处用推断冒充读数：216 万行日志的全量
聚合、相隔 40 分钟的双时刻对照（比例逐位不变，据此排除采样噪声）、只读库的 36 张表、本机 DNS
实测，外加一份能在同一台机器上逐位复算的 `evidence/audit.py`。四条根因**全部为真**，而且四条
都是本仓 114 条黑板记录里**一条都没有提过**的新事实。§二 R-2 那句总结——

> 「一道 fail-closed 的闸，如果它的判据在某类环境里恒假，这个功能在该类环境里就是零可用——
> 而『零可用』和『很安全』在日志里长得一模一样。」

——是这一轮最有价值的产出，它的适用范围远超本 spec。§十八 对边界的诚实陈述（本轮不直接改善
盈亏、只让盈亏可被正确计算）与它论证的「轴 A 是兄弟 spec 轴 A 的前置条件」，同样是评审完全
认可并建议原样保留的部分。

**之所以是「有条件」而不是「无条件」通过**，是因为评审在逐节比对源码锚点时发现：这份修法在
**五处**会重犯它自己刚刚总结的那个形状——新通路的判据落在一个不携带该信息的地方、或者落在
一个它以为存在而实际不存在的位置上。五条都不会在任何一条离线用例上变红：

| 形状 | 本轮的五次复发 |
|---|---|
| **判据与被判定的事实没有因果关系** | P0-1（代理机器上对端恒为私有）、P0-3（异常里没有 HTTP 状态） |
| **有写点没读点 / 有读点没宿主** | P0-2（`response_model` 静默剔除）、P0-4（失败态没有 wire 容器） |
| **声明了但按不下去的回退开关** | P0-5（前缀白名单 + 启动顺序） |

**P0 五条、P1 十二条已在 v2 正文逐条修毕**，每一条都附了它被核实的文件与行号，并配了会在
v1 设计下变红的门禁（G-B7b / G-B10 / G-A7c / G-A11 / G-D7 是其中五条**判据反向**的门禁——
它们断言的是「旧写法必须红」，而不只是「新写法是绿的」）。

### 放行条件（C-1 … C-9，实施节点逐条勾选）

| # | 条件 | 判据 |
|---|---|---|
| **C-1** | **`_redact` 与 `http_status` 必须在第 1 步落地**（§16.1） | G-A13 绿；`private_client.py` / `trade_client.py` 的 AST 里不出现 `from .accounts` |
| **C-2** | **A-5 与 A-2 同批合入**，不许拆 | 真机第 1 步能在启动弹窗上读到那句人话（含真实 `code`/`msg`） |
| **C-3** | **B-2 的 schema 与 wire 同一个提交** | G-B10 用 `TestClient` 断言五个新键真的到达客户端；中间不许出现「绿的且错的」提交 |
| **C-4** | **对端复核的代理豁免必须先于 B-1 的放行档合入** | G-B7b 绿；真机第 2 步的**第二遍**（带 `PROXY_URL`）`reader_ok > 0` |
| **C-5** | **12 个键的前缀逐个核对 + `config-passthrough.test.mjs` 的反向断言** | G-D7 的第 ② 项：一个无前缀的键必须**穿不过去** |
| **C-6** | **`.claude-index/config.md` 的阈值偏离登记与代码同批** | §10.5 的五行全部落到那张表里，含「下一次触碰应先还什么」 |
| **C-7** | **轴 C 单独成一个提交**，提交信息写清「唯一触碰真实交易路径 + 三重防护 + 一键回退键名」 | G-C1..C13 全绿；G-C12 的 AST 断言尤其不许跳过 |
| **C-8** | **G-X1 扩展既有那条用例，而不是新写一条**，且分母是 **12** | 12 把闸全置回退档 ⇒ 三处 wire 无任何新键 |
| **C-9** | **附录 C 由实施节点用真实输出填满** | 这是本 spec 唯一允许在合入前为空的地方；其余任何「待填」视为未完成 |

### 评审不同意、但尊重设计节点判断的一处

**Q1 的 TTL 默认值取 4.0s。** 评审倾向于第一版落地时取 **2.5s**（`poll_interval / 2`），
理由是轴 C 是本轮唯一触碰真实交易路径的改动，而 2.5s 的压降仍有 2×、够本轮的目标（O-4 要求
`≤ 16 次/分`，2.5s + N=5 得 24 次/分 —— 达不到）。**核算之后评审收回这个意见**：2.5s 确实
达不到 O-4 的验收线，而 O-4 的验收线本身是合理的。故**同意 4.0s**，并把这次核算写在这里，
免得下一轮有人重新提一遍同一个建议。

### 复核方式

本轮评审的每一条都可复核：

```bash
# 五条 P0 的源码锚点（评审就是这样读出来的）
sed -n '27,45p'   services/quant/app/ai_auto/intel/evidence.py      # P0-1 proxy=proxy_url
sed -n '277,292p' services/quant/app/api/ai_intel.py                # P0-2 response_model
sed -n '66,74p'   services/quant/app/api/schemas_intel_evidence.py  # P0-2 严格 5 字段
sed -n '36,42p'   services/quant/app/okx/private_client.py          # P0-3 异常只有 code/message
sed -n '85,145p'  services/quant/app/ai_auto/fee_rates.py           # P0-4 wire() 无快照即 None
sed -n '78,88p'   desktop/src/lib/config.js                         # P0-5 前缀白名单
sed -n '43,49p;150,156p;527,530p' desktop/src/main.js               # P0-5 启动顺序
```

### 一句话

**这四条根因值得本轮全部做掉；而这份修法在被上面五条 P0 修正之前，会在它自己刚刚写下的那条
教训上再摔一次。** v2 之后可以进实施。

> 评审完成时间：2026-09-02 · 评审对象 v1（1,292 行）→ 定稿 v2
> P0 5 条（全部修毕）· P1 12 条（全部修毕）· P2 7 条（登记不改）· 确认为「不是缺陷」4 条

---

## 实施过程发现的方案缺陷

> 实施节点（loop `tge-8712e7ab` 第 2 轮 · Subtask #2）在落地 v2 时发现的问题。
> 体例与本仓既有做法一致：**发现即登记，绝不静默偏离**；每一条都写清「设计怎么说的 / 真机或
> 源码是什么样的 / 因此改成了什么 / 有没有一条会红的门禁把它钉住」。
>
> **前三条（I-1 / I-2 / I-3）是阻断级，而且是同一个形状的三次复发**：照 v2 的字面实现，轴 B
> 在**正是本轮要修的那台机器上**仍然 `reader_ok = 0`——本轮要消灭的那个形状会原封不动地再来
> 一次，而且和上一次一样不会在任何离线用例上变红。它们不是「设计写错了」，而是「设计的判据
> 在真机上还差一格」，与评审给 P0-1 下的判词是同一句话。
>
> 三条各自差的那一格是：**IPv6 侧的合成段没登记**（I-1）、**代理豁免看不见 `trust_env` 代理**
> （I-2）、**连不上的哨兵地址被当成了内网**（I-3）。三条都由「一道 fail-closed 的闸，判据在
> 某类环境里恒假 ⇒ 该环境下零可用，而零可用与很安全在日志里长得一模一样」这同一句话解释。
> **I-3 是被设计节点自己写的 `evidence/audit.py` 抓出来的**——那份复算脚本的价值在这一刻兑现了
> 一次它自己没预料到的用途。

### I-1（阻断级）｜合成地址段只登记了 IPv4 一侧，而这台机器的 IPv6 一侧也是合成的

**设计怎么说的**：§六 B-1 的 `SYNTHETIC_V4 = (198.18.0.0/15,)`，外加一个留给用户的
`ai_trade_intel_reader_synthetic_cidrs`。

**真机是什么样的**（实施节点实测，2026-09-02）：

```
coindesk.com   -> ['198.18.0.194']
coinbase.com   -> ['198.18.0.195', '2001::b33c:c110']     ← 多了一个 IPv6
theblock.co    -> ['198.18.0.199', '2606:4700::6812:f74', '2606:4700::6812:e74']
```

`2001::b33c:c110` 落在 Teredo（`2001::/32`，IANA「IETF Protocol Assignments」特殊用途段），
而 `ipaddress.ip_address('2001::b33c:c110').is_private` 为 **True**。于是
`classify_resolution` 的第 2 条判据（「任一 `private` ⇒ `private`——混合了真实私有地址即拒」）
把 `coinbase.com` 判成 `private`，**`auto` 档照样拒**：

```
coinbase.com   auto='private_host'   strict='private_host'      ← 修之前
```

也就是说：**任何带这类 AAAA 记录的域名，在这台机器上仍然 0 成功**。而 `theblock.co` 那种
「合成 IPv4 + 真实公网 IPv6」的组合又是放行的——同一台机器上两个公开新闻站得到两种结论，
差别只取决于运营商给不给它 Teredo 记录。这正是 R-2 那句总结的第三次复发。

**改成了什么**：`intel/netpolicy.py` 增 `SYNTHETIC_V6 = (2001::/32,)`，与 `SYNTHETIC_V4`
并列进 `classify_addr` 的合成段扫描。修完之后本机六个域名全部 `auto=None / strict='private_host'`
（`auto` 放行、`strict` 逐字节回到今天）。

**为什么归 `synthetic` 而不是 `public`**：`2001::/32` 是 IPv6-over-IPv4 隧道的公共中转段，
既不是任何人的内网、也不该被当成一个可信的公网终点。归 `synthetic` 意味着它在 `auto` 档下
仍要走「域名闸 + 出网后对端复核」，在 `strict` 档下**仍然被拒**——两档的语义都不变。

**刻意不做的一件事**：**不**登记 `fc00::/7`（ULA；Clash-meta 的 IPv6 fake-ip 默认段
`fd00::/18` 在它里面）。ULA 是真实可达的内网地址族，把它整段判成合成就是真的放松了 SSRF
防护。用那种配置的用户走 `AI_TRADE_INTEL_READER_SYNTHETIC_CIDRS` 显式登记——一次写在配置里
的放宽，与一次藏在常量表里的放宽不是一回事。

**门禁**：`test_i1_synthetic_ipv6_must_not_drag_the_whole_resolution_into_private`
（同时断言 `fd00::1` / `fc00::1` 仍判 `private`——防止下一轮有人「顺手」把整个 ULA 加进去）。

### I-2（阻断级）｜代理豁免的判据是 `cfg.proxy`，而它看不见 `httpx` 的 `trust_env` 代理

**设计怎么说的**：§六 B-1c 硬性约束第 1 条——「`cfg.proxy` 非空 ⇒ 整条对端复核跳过」。
评审 P0-1 正是为这一条把 v1 判红的。

**真机是什么样的**：`cfg.proxy` 为空（`settings.proxy_url` 读不到任何键，与 §一 E-6 的记载
一致），但 `httpx.AsyncClient` 默认 `trust_env=True`——它会自己去读进程环境里的
`HTTPS_PROXY` / 系统代理。实测：

```
server_addr = ('127.0.0.1', 7897)      ← 真实 TCP 对端
classify_addr('127.0.0.1') = 'private'
⇒ 三篇候选全部 blocked(private_peer)，reader_ok = 0
```

**这就是 P0-1 指出的那个缺陷本身，只是从另一侧进来的**：评审把「配了 `proxy_url` 的机器」堵上
了，而「没配 `proxy_url`、但环境里有 `HTTPS_PROXY`」这一格照样把功能打成零可用。判据
（`cfg.proxy`）与被判定的事实（这一跳到底有没有经过本机代理）之间**仍然没有因果关系**。

**改成了什么**：`peer_verdict` 增第二条豁免——**对端是回环地址（`127.0.0.0/8` / `::1`）即
视为「经本机代理出去」，跳过复核并如实记 `peer_checked=False`**。

**为什么这不打开任何一道已经关上的门**：回环对端只可能有一个含义，就是这一跳经过了本机代理。
它不可能是目标站点本身——「一个公开域名解析到 `127.0.0.1`」那一类早在**语法闸**
（`guard_url` 拒 `localhost` 与私有 IP 字面量）与**解析闸**（`classify_resolution` 判
`private`）就被拒了，根本走不到对端复核这一步。

**验收（§14.4 第 2 步的实施节点预跑）**：修完之后在这台真机上第一次抓到了正文——

```
http_error http=403 …/theblock.co        ← 站点自己的 bot 防护（一个诚实的、归因正确的失败）
http_error http=429 …/coindesk.com       ← 同上
ok         http=200 bytes=1436057 …/finance.yahoo.com
ok         http=200 bytes= 411461 …/cointelegraph.com
reader_ok = 2        reject counts = {}
```

生产库三周来的读数是 `reader_attempt=102 / reader_ok=0`。**这是它第一次不是 0。**

**门禁**：`test_i2_loopback_peer_is_treated_as_a_proxy_hop_not_as_an_internal_target`
（同时断言 `10.1.2.3` / `192.168.1.1` 仍判 `private_peer`——回环豁免只覆盖「经本机代理出去」
那一格）。

### I-3（阻断级）｜连不上的哨兵地址（`::` / `0.0.0.0`）被当成了「真实私有地址」

**这一条是设计节点自己那份 `evidence/audit.py` 抓出来的。** 实施节点跑
`python evidence/audit.py dns` 交叉核对 §一 E-6 时，看到七行里有一行与众不同：

```
binance.com   -> 198.18.0.196, ::        classify_resolution = private     ← 其余六行都是 synthetic
汇总 {'synthetic': 6, 'private': 1}
```

某些解析器对 AAAA 查询回 `::`（未指定地址）。`ipaddress.ip_address('::').is_unspecified` 为
**True**，于是 `classify_addr` 判它 `private`，`classify_resolution` 的第 2 条判据（「混合了
真实私有地址即拒」）把整组判成 `private` ⇒ **这个域名被拒**。

**它比 I-1 / I-2 更难查，因为它是间歇性的**：同一台机器上，`binance.com` 在实施节点的另一次
实测里返回的是 `['198.18.0.196']`（不带 `::`），`auto` 档放行。一个「有时能抓、有时抓不到，
而且两次都不报错」的功能，比一个稳定失败的功能更贵。

**改成了什么**：`classify_resolution` 在判定**之前**先丢掉连不上的哨兵
（`is_unroutable_sentinel`：`::` / `0.0.0.0`）；丢完之后没有剩下任何地址 ⇒ `unresolved`
（**而不是 `private`**——那是「这台机器解析不出来」，不是「这个域名指向内网」）。

**为什么这是范畴纠正而不是放宽**：`::` 不是任何人的内网，它根本不是一个**可以连接的目的地**。
真要只剩它一个，连接本来就会失败，而那是一次诚实的、归因正确的网络失败。把它算成「真实私有
地址」，与把 `198.18.0.0/15` 算成「内网」是同一个范畴错误——后者正是本轮 §二 R-2 的根因本身。

**门禁**：`test_i3_unroutable_sentinels_are_dropped_not_counted_as_private`
（同时断言 `["10.0.0.1", "::"]` 仍判 `private`——哨兵豁免只覆盖「连不上的占位符」那一格）。

### I-4｜A-5 的「塞进 `feeRates` 同一格」会让 `build_fee_ledger` 当场 KeyError

**设计怎么说的**：§五 A-5 第 4 条——把失败原因「塞进 `config_map[inst_id]["feeRates"]` 的同一
格里（**已有的 key，不新增**）」。

**源码是什么样的**：`live_auto/fee_ledger.build_fee_ledger` 对这个值的读法是
`fee_rates["makerBps"]`（在 `if fee_rates:` 之后**直接下标**）。一个「只有 `lastError`、没有
bps」的降级 dict 是真值，于是它会当场 `KeyError`。而如果反过来给降级 dict 补上配置常数的 bps，
`fee_rates.snapshot_from_wire` 就会铸出一份 `source="config"` 的「快照」——**一份冒充官方档位
的本地常数**，那恰恰是本轮要消灭的形状。

**改成了什么**：`feeRates` 只承载**成功态**的归一 wire（语义一字不改），失败原因走**兄弟键**
`feeRatesError`，沿 `config_map → _MemberPlan.fee_rates_error → build_fee_ledger(fee_rates_error=)
→ ledger.fee_rate_error → CostRuntime.seed(last_error=)` 这条与 `fee_rate_wire` **完全同款**的
既有搬运缝走到会话侧。新增的搬运格是 1 个，没有新增第二条通路。

### I-5｜C-4 的判据需要「这份读数真实取得的时刻」，而 `_read_positions` 只回 `list[dict]`

**设计怎么说的**：§七 C-4——`snapshot.taken_wall >= self._last_private_write_wall`。

**源码是什么样的**：`_read_positions` 是一个返回 `list[dict]` 的注入缝（30+ 个替身按这个契约
写成），`taken_wall` 在它的返回值里没有位置。照字面实现只能退而求其次用「我收到它的时刻」
——**那等于让缓存把自己洗成新鲜的，C-4 就成了一句空话**（一份 4 秒前的缓存此刻被交到手上，
它承载的事实仍然是 4 秒前的）。

**改成了什么**：新增 `trading.fetch_live_positions_at(...) -> (rows, taken_wall)`，只被
`LiveExecutor` 的**两个默认读法**使用；`fetch_live_positions` 的 `list[dict]` 签名与全部既有
调用点、全部替身**一字不改**。执行器上新增 `_positions_taken_wall`，`reconcile` 每轮先把它置成
「此刻」（于是注入替身与 `live_auto` 的行为逐字节不变、恒 fail-open），两个默认读法再覆盖成
真值。门禁 `test_g_c10_stale_snapshot_never_adjudicates_a_phantom_close` 两面都断言。

### I-6｜LLM 侧的两个接线点在源码里是**同一个**函数

**设计怎么说的**：§八 D-3 的五个接线点，其中 4/5 是「`agent/llm/anthropic_client` 的响应出口」
与「`agent/llm/openai_client` 的响应出口」。

**源码是什么样的**：两个 client 的响应**都且只**经过 `agent/llm/retry.py::_run_attempt`
（`open_stream_with_retry` 是它们共用的 pre-stream 编排）。各自的 `_request_cm` 里只有
`client.stream(...)` 的上下文管理器，没有第二个能看到 `Response` 的位置。

**改成了什么**：接线落在 `_run_attempt`——它覆盖 spec 点名的那两个出口，**且让未来的第三个
provider 不可能被漏掉**。一份「漏了一个 provider」的统计表在「谁在拒我」这个问题上给出的答案
是错的，而错的答案比没有答案更贵。门禁 `G-D8` 断言两个 host 各成一格。

### I-7｜启动弹窗在提交前就关掉了，回执行只能落在它之后的地方

**设计怎么说的**：§五 A-4 落点 1——「启动弹窗回执带上 `feeRates` → 弹窗「成本」分组新增一行」。

**产品形态是什么样的**：`AiTradeLaunchModal` 是**提交前**的配置弹窗，成功即关闭；它没有「成本」
分组，也没有任何渲染启动回执的位置（回执由 `AgentPanel` / `AiTradePanel` 消费）。而 §14.4 第 1
步的硬判据要的是「**启动一次分组之后，能读到那句人话**」。

**改成了什么**：那句人话落在**三个用户真的会看到、且会一直看到**的地方，而不是一个 2 秒后消失
的弹窗里：

| 落点 | 位置 | 何时可见 |
|---|---|---|
| `FeeSourceStrip` | `AiTradePanel`（纪律条**之上**） | 启动成功后整场会话常驻 |
| `FeeSourceCard` | 历史详情（**独立成卡**，见 I-7） | 事后复盘 |
| 真值源卡片第 1 行 | 设置中心 · 真值源 | 任何时候，且带一个可点的「重新查询费率」 |

### I-8｜`describeFeeRates` 的唯一宿主会自我隐藏，把它搬出来是本轮必须做的一步

§一 E-13 已经指出这件事，但 v2 的 §五 A-4 落点 2 写的是「运行中成本卡旁边渲染同一行」，没有
说要**把它从 `ReconcileCard` 里拿出来**。而 `ReconcileCard` 的第二行就是
`if (!view) return null`——生产库 211 条 run 里绝大多数根本没有对账读数，那一行因此**从来没有
被任何人看见过**。故实施节点把它拆成独立的 `FeeSourceCard`，缺键才整段不渲染。

### I-9｜`AiTradeSession` 没有 `self.settings`

轴 E 的 `_truth_sources_key()` 初版写了 `self.settings`，全量用例当场红 19 条
（`'AiTradeSession' object has no attribute 'settings'`，且它被会话的 `except` 吞成一条
`WARNING` ——**轮数不再推进而不报错**，正是本轮在讲的那种失效）。会话上的 settings 真源是
`self.executor.settings`（`ai_auto` 与 `live_auto` 共用的那一份）。已改，19 条恢复。

### I-10｜`FeeRateCache.wire()` 增了一个 `settings` 形参

降级形状里的 `makerBps` / `takerBps` 必须取**回落后的配置常数**（A-5 的原文），而缓存自己不
认识 `Settings`。故 `wire(now_wall, settings=None)` 增一个可选形参；不传即两个 bps 为 0.0
（只服务于直接构造缓存的纯单测）。生产的两个调用点（`CostRuntime` 的 `costSnapshot` 与
`aiStats`）都恒传。

### I-11｜日志轮转器必须用同步写，`fs.createWriteStream` 与 rename 有窗口

§八 D-1 的伪码是 `openRotating` 包一个 WriteStream。实测（`node --test`）：`WriteStream.write()`
只把字节交给内部缓冲，而轮转要 `renameSync` 那个**正被打开着**的文件——Windows 上重命名一个
仍有句柄的文件会 EBUSY，缓冲里还没落盘的那几行则会写进已经改名的旧文件里。四条门禁里有三条
因此是红的。改为 `fs.openSync(file,'a')` + `fs.writeSync`：「越线 → 关句柄 → rename → 重开」
四步之间不存在任何窗口。代价可忽略——轴 D-2 之后日增量约 0.5 MB/日（今天是 12 MB/日）。

### I-12｜`callStats` 的 wire 形状：§12.3 与 §八 D-3 自相矛盾，取后者

§12.3 的示例写的是 `{"windowMinutes":15, "top":[…]}`，而 §八 D-3 在评审 P1-8 把接线点扩到 5 个
之后明写「两族共用同一个 `CallStats` 实例但**键的第二段语义不同**，故 `wire()` 里分两个数组
下发（`okx[]` / `llm[]`）」。实施取**后者**（它是被评审修正过的那一份），并额外下发
`spanMinutes`——`perMin` 的分母必须是**真实观察到的分钟数**，否则一个刚启动 2 分钟的进程会把
63.5 次/分显示成 8.5，而 O-4 的验收线（≤ 16 次/分）正是拿这个数去比的。

### I-13｜两条既有的 wire 形状冻结用例必须同批扩展

- `tests/test_okx_link_state.py::test_snapshot_wire_shape_is_complete` —— A-3 的三个加法键
  （`rejected4xx` / `lastRejectedTs` / `lastRejectedStatus`）。
- `tests/test_ai_intel_evidence_api.py::test_documents_health_is_not_hijacked_by_read_intel`
  —— B-2 的六个加法键。

两条都是**集合相等**断言，扩展它们是 P0-2 那条纪律的正面兑现（「加法键必须真的到达客户端」）。

### I-14｜`get_call_stats()` 每次取用都重读总闸，用例必须打桩在**端点侧**

`okx_call_stats_enabled` 若只在单例创建时读一次，运维关掉它要重启后端才生效——在打包桌面端里
那等于半把闸。故 `get_call_stats()` 每次都从 `get_settings()`（`lru_cache`，一次字典查找）刷新
`enabled`。副作用：用例不能靠「换一个 `enabled=False` 的实例」来测回退档（会被当场覆盖回来），
必须打桩在 `api/okx_link.get_call_stats` 上。已写进 `G-D6`② 的用例注释。

### I-15｜三处「设计说改 A、真源在 B」的接线，逐条登记

| 设计说 | 真源 | 处置 |
|---|---|---|
| §10.4「`AiTradeLaunchModal*.tsx` 成本分组加一行」 | 弹窗是提交前的，见 I-6 | 落到 `AiTradePanel` / 历史详情 / 真值源卡 |
| §六 B-2「解析策略选择器」 | 它是一个 `Settings` 键，面板改不动 | 同批扩 `INTEL_SETTING_BRIDGE`（**不扩就是假面板**，本仓第四次同形）+ `IntelSettingsIn/Out` 各一字段 |
| §九 E-1「`action.target: intel-reader`」 | 设置中心的分区 key 是 `intel-source` | 用真实存在的那个 key（`G-E3` 断言 `retry` 类 action 的 endpoint 在 `app.routes` 里） |

### I-16｜真值源卡片自己占一个设置分区，而不是塞进「关于 / 诊断」

§九 E-2 只说「放设置中心」。实施把它做成 `SettingsNav` 的一个**独立分区**（排在「关于 / 诊断」
**之前**），理由是 §14.4 第 5 步要求它可以被单独打开来做真机验收；塞进「关于」页会让它和版本号
挤在一起，而「系统现在有没有在对我说真话」比版本号更该先被看到。

### I-17｜轴 B-2 的两把旋钮差点被做成「有写点没读点」的假开关

`ai_trade_intel_reader_health_visible` 与 `ai_trade_intel_reader_zero_streak_warn` 在 v2 的
§十三 里各占一行，但**正文里没有任何一处说它们在前端怎么被读到**。实施到一半时它们的状态是：
后端声明了、`.env.example` 登记了、`G-Env` 是绿的，而抓取健康行**恒渲染**、横幅的阈值是前端
写死的 `3`——两把闸都关不掉，且不报错。

这正是本仓成文的「有写点没读点」，也是本轮 §一 E-13/E-14 在讲的同一件事。**发现即补**：两者作为
**只读**字段进 `GET /settings/intel` 的生效值回读（不进 `IntelSettingsIn`，本轮不让用户从面板改
它们），前端 `IntelReaderSettings` 据 `readerHealthVisible` 决定渲不渲染、把 `readerZeroStreakWarn`
传给横幅。理由与既有的 `alertPendingTtlS` 那一行**逐字同源**——它的注释原文就是「前端写死一个数，
就会在用户调宽时限之后给出一句与后端不一致的判定」。

### I-18｜全量回归抓到的三条真缺陷 + 一条加法键的正面兑现（**修的是产品代码，不是用例**）

第一轮全量 `pytest` 是 **5,115 passed / 34 failed**（基线 8 failed，其中 7 条在未跟踪的在制文件
`tests/test_factor_library_expansion.py`）。26 条新红逐条定位，**没有一条是靠改断言消掉的**：

| # | 症状 | 真缺陷 | 修法 |
|---|---|---|---|
| 1 | `test_trading_api` / `test_trading_close` / `test_ai_trade_backstop_repair_io` 共 9 条：`'_Fake' object has no attribute '_fingerprint'` | **`_drop_positions_cache` 会让一次已经成功的私有写抛异常。** 它跑在写成功之后、审计落库之前——交易所侧仓位已经变了，而调用方会收到一个异常。「单发出去了，但接口告诉你失败了」是本仓最不能接受的一类失败 | 整段 `try/except` + `debug`。作废失败的后果由 C-4 的构造性保证兜住，**那正是 C-3 / C-4 冗余的意义** |
| 2 | `test_llm_client_url` / `test_agent_llm_retry` 共 8 条：`'_FakeResp' object has no attribute 'request'` | **`_note_llm_call` 会让一次真实 LLM 往返失败。** 且这不只是替身问题：`httpx.Response.request` 在未绑定时**会抛 `RuntimeError`**（不是返回 `None`） | 整段 `try/except`，与 `CallStats.note` 自己那条纪律同源 |
| 3 | `test_ai_trade_fee_ledger[reconcile_ok]`：`entry_fee_usd == 15.0`，期望 0.0 | **C-4 的极性反了。** `_positions_taken_wall` 默认 `0.0`（不知道这份读数何时取得），而 `0.0 >= wall` 为假 ⇒ **抑制幻影平仓裁决**。任何绕过 `reconcile()` 直接调 `_on_reconcile_ok` 的路径（替身、未来的新调用点）都会被静默关掉那条**真实存在、必须被裁决**的通路（交易所侧 OCO 触发就走它） | 「不知道」必须 fail-open：`_positions_taken_wall <= 0` ⇒ 恒真。两条 fail-open 退路都写进 docstring |
| 4 | `test_okx_link_api` / `test_ai_intel_evidence_api` / `test_okx_link_state`：集合相等断言多出新键 | 不是缺陷，是**加法键的正面兑现**（P0-2 那条纪律要的正是「它们真的到达了客户端」） | 三条冻结用例同批扩展并写明理由 |

另有 3 条把盯市周期调到 1 秒的用例（`test_ai_trade_wake_interval` × 2 / `test_live_auto_session`）
撞上轴 C 的启动期跨字段校验。**校验保留**（`LIVE_AUTO_POLL_INTERVAL_S` 既不在 `.env.example` 里、
也不在用户的 `agent.env` 里，实际部署恒取默认值 5，不存在「升级即起不来」的存量配置），用例改为
同时置 `ai_trade_positions_snapshot_ttl_s=0`——那也**正是真实用户在这种配置下该做的那件事**。

前端全量 `vitest` 另抓到两条，同样是产品代码的问题：

- `components/Intel/intelNoDangerousHtml.source.test.ts` 判红——本轮新写的解析策略说明文案里出现了
  字面 `**`。**这是本仓第 5 次同形复发**（后端文案 `**` 在 JSX 里显星号），而这一次它被既有的源码
  扫描门禁**当场**挡下来了。已删。
- 同一条门禁的「漏扫等于没扫」清单要求把新组件 `IntelReaderBanner.tsx` 登记进去。已补。


### I-19｜终审在源码里直接修掉的八处（复核阶段发现）

> 终审节点（loop `tge-8712e7ab` 第 2 轮 · Subtask #3）逐条比对 §十 变更清单、§十三 回退档与
> §14.2 门禁表之后，在**源码里直接修毕**下列八条。没有一条留成 TODO，也没有一条是靠改断言
> 消掉的。

1. **回退档没有逐字节回退到 `8451163`：`source` 变成了 `"exchange:type"`。**
   `_fee_rungs` 在总闸 `AI_TRADE_FEE_RATE_FAMILY_FALLBACK_ENABLED=false`（以及 `SPOT`/`MARGIN`）
   这一格里，把「今天那一跳」也叫作 `"type"`，于是 `_normalize_trade_fee` 产出
   `source="exchange:type"`——既不等于本轮之前的 `"exchange"`（§十三 要求「逐字节」），界面还会
   把一份**按 `instId` 取到的**档位说成「交易所账户通用档位」（`truth_wire.fee_rates_row` 的
   分档文案）。而 §14.2 的 `G-A14` 原文早就写着 `rung=None ⇒ 逐字 "exchange"`。
   **改法**：今天那一跳的 rung 名改为空串（下游据此逐字保留老值），`get_trade_fee` 用 `None`
   表示「一跳都没成功」——两者是两件事，不许压成同一个假值。`G-A8` / `G-A9` 同批扩断言，
   后者直接断言 `wire["source"] == "exchange"`。

2. **写即作废会被一次慢请求原地撤销。** `PositionsCache.get` 的成功分支无条件
   `self._entries[fp] = snapshot`，于是「飞行发出 → 私有写成功并 `invalidate` → 飞行才落地」
   这个时序会把一份**写之前**的快照重新写回缓存，随后整个 TTL 窗口里的每一个读者都拿到写之前
   的持仓——语义 3（写即作废）在这一格里是一句自称。**改法**：每桶一个**作废代次**，
   `invalidate` 自增；飞行落地时代次已变即**不写回缓存**（仍照常交给已经在等它的调用者——
   它们要的就是那一次读）。新用例
   `test_c3_an_invalidation_during_a_flight_is_not_undone_when_it_lands`。

3. **单飞把「取消」变成了跨成员传染的通路。** 领飞者被取消时，`except BaseException` 把
   `CancelledError` 原样喂给每个等待者，而等待者是**别的成员**的盯市轮，谁都没有被取消。
   于是「停掉某一个成员」（`ai_trade_member_exit` 取消它的任务）会把同账户其余成员的循环一起
   打断——本轴最不该引入的耦合。**改法**：领飞者被取消 ⇒ 给 future 一个 `cancel()`（而不是
   `set_exception`），等待者据此**接手重取**；等待者自己被取消时仍原样上抛（两者用
   `inflight.cancelled()` 区分）。新用例 `test_a_cancelled_leader_never_cancels_the_waiters`。

4. **`fetch_trade_fee` 成了零调用点的僵尸函数，而它的 docstring 用一个不成立的理由为自己辩护。**
   它自称「既有的 `CostRuntime` 绑定与全部测试替身一字不改」——但 `CostRuntime` 本轮已改绑
   `fetch_trade_fee_ex`，全仓（含 tests）再无一处引用它。留着它的害处不是几行代码，而是
   「下一轮有人对着它写新代码，然后再一次丢掉失败原因」。**已删除**，并把三处指向它的注释
   改写为指向「老契约」这件事本身。

5. **`summarizeTruthSources` 会在四行全是 `unknown` 时说「全部正常」。**
   没在跑时 `build_sources` 的四行全是 `unknown`（`G-E2` 正是为这一格立的），而折叠态摘要只数
   `degraded`，于是这张**专门用来指出「系统在说假话」**的卡片，自己在第一屏说了一句当时并不
   成立的安心话。**改法**：三态分开——有降级说降级（带颜色）、没降级但有读不到的说
   「N 项暂无读数」（**不带颜色**，「不知道」不是告警）、四行全 `ok` 才说全部正常。

6. **`AiTradePanel.tsx` 里 `DisciplineStrip` 的 docstring 被挤成了孤儿。**
   新增的 `FeeSourceStrip` 连同它自己的 docstring 被插在「`DisciplineStrip` 的 docstring」与
   「`DisciplineStrip` 本体」之间，于是那段解释纪律条为什么在主面板顶部的注释，现在挂在一个
   讲费率来源的函数上。**改法**：整块前移，两个函数各自与自己的 docstring 相邻。

7. **两处「有写点没读点 / 名不副实」。** `CallStats._first_slot` 被 `note` / `reset` 写、被
   **零处**读（`_span_minutes` 用的是 `min(self._buckets)`）——僵尸状态，已删；
   `truth_wire.decision_model_row` 里那个叫 `worst` 的变量取的是 `rejects[0]`，而 `llm[]` 是
   **按调用量**排序的，第一格只是「最热的提供商」——两个提供商同时出问题时它会指错方向。
   已改成真的取被拒最多的那一格（`max(..., key=_reject_total)`）。

8. **`process-manager.js` 的 `logStream` 类型注释仍写着 `fs.WriteStream`。**
   轴 D-1 之后 prod 传进来的是 `rotating-log.openRotating()` 的产物（同步写入器，只有
   `.write` / `.end` / `.path`）。注释描述了一件不再成立的事，已按本类真正用到的那个子集重写。

另有两处纯风格修正：`session.py` 里 `truth_wire` 的两行 import 被插在 `liveness` 与
`liveness_text` 之间（拆散了一对），移到 `track_record` 之后；三个前端用例的 mock 把六个键
挤成了一行 155 列（CLAUDE.md 阈值 80），按既有排版拆开。

### I-20｜终审补齐的六条门禁（§14.2 里有、实现里没有）

逐条比对 §14.2 的门禁表后，下列六条在实现中没有对应用例。它们**不是可选的**——其中三条守的
正是本轮最容易复发的形状。已全部补齐并通过：

| 编号 | 补在哪 | 它守什么 |
|---|---|---|
| `G-B8` | `..._io.py::test_g_b8_every_redirect_hop_goes_through_the_gate` | 三分类不许把「每一跳都过闸」这条既有约束弄丢：第 1 跳合成段放行、第 2 跳解析成真实内网 ⇒ 整条链拒，且第 2 跳**在发出去之前**就被拦下 |
| `G-B11` | `..._io.py::test_g_b11_persistent_status_and_process_local_reject_codes_are_two_things` | 两张分布表寿命不同：`blockedByStatus` 持久且**不含**拒因，`rejectCodesSinceStart` 重启归零且必须自陈 `sinceProcessStart` |
| `G-C8` | `..._core.py::test_g_c8_the_pause_position_read_takes_the_forced_path` | 暂停时读持仓真值走强制穿透那条缝（AST：`_read_exchange_position` 里只出现 `_read_positions_fresh`） |
| `G-C9` | `..._core.py`（AST）+ `..._io.py`（功能） | 五个私有写**成功后**作废、失败的写**绝不**作废（AST 判「调用在 `try` 的 body 里、不在任何 `except/else/finally` 上」，并钉死那五个函数一个不多一个不少）；功能侧断言只打自己那个桶、且对没有 `_fingerprint` 的替身绝不上抛 |
| `G-D5` | `..._io.py::test_g_d5_the_log_level_switch_really_moves_both_loggers` | D-2 的唯一回退档真的落到 `httpx` **与** `httpcore` 两个 logger 上（只压一个等于没压） |
| `G-E6` | `..._core.py::test_g_e6_truth_sources_ride_every_status_payload` | `truthSources` 无条件随每一次 `_status_payload()` 下发（AST：那一处 `**self._truth_sources_key()` 不在任何 `if` 之下），且恢复后 `degradedCount` 真的归 0——前端是**整体浅合并**，只在降级时带会把一次恢复留在旧值上 |

另补两处「实现了但没有任何一条用例会因为它消失而变红」的落点：

- **解析策略的往返 + 坏值拦截**（`..._io.py::test_the_resolver_switch_round_trips_and_a_bad_value_never_reaches_settings`）。
  它是本轮唯一一把用户能从面板改的闸，而既有的 `INTEL_SETTING_BRIDGE` 往返用例是**硬编码三个键**
  的，扩了表也不会自动覆盖到它。不测的症状正是本仓第四次同形前科：请求 200、开关照动、
  `Settings` 上的值永远不变，而且不报错——这一次改的还是一道**安全**闸。
- **面板上的两个新落点**（`components/Chart/AiTradePanel.test.tsx` 新增 4 例）：费率来源条的
  降级态必须**带着真实 `code`/`msg`** 渲染（§14.4 第 1 步的硬判据「读不到这句人话，轴 A 就没
  做完，无论用例多绿」在前端这一侧的冻结）、缺键即整段不渲染、真值源芯片只在有降级时出现且
  文案里写明**分母是 3**。此前这两个 `data-testid` 在全仓**零引用**。
