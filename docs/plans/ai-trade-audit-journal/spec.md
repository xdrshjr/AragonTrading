# AI 自动交易 · 交易日志与审计导出（决策留痕 · 管理台可查 · 可导出用于后训练）

> **状态**：**v2**（评审后修订 · 2026-09-03）——评审记录见 §零，评审结论见文末 §十二。
> **Feature slug**：`ai-trade-audit-journal`
> **要解决的用户诉求**：「完善交易日志系统，用户可以在对应的管理员界面中进行查看【AI 自动交易】
> 功能产生的交易记录以及相关日志，使之**可查看、可审计**，并且**可被后续用于导出，导出后可用于
> 进行 LLM 模型的后训练**。」
> **范围纪律**：**只加法**（不改任何既有安全门禁的判定式、不改任何交易语义）、**零 DB 迁移**
> （只新增表，**绝不给既有表加列**）、**每个新机制都有一个关得掉的取值**、**不进回测车道**、
> **不新增下单路径**、**不复制既有真源**（成交与订单回执已有真源，日志台只引用、绝不再抄一份）。

---

## 零、评审记录（v1 → v2）

评审口径：**可行性**（本仓当前技术栈真的做得出来吗）/ **完备性**（边界、错误路径、上线回退有没有
缺）/ **一致性**（是否与 `CLAUDE.md` 或代码库里已成文的约定冲突）/ **合尺寸**（过度设计还是欠定义）。

v1 的引用是**逐条核过源码**的：`session.py` / `decider.py` / `prompts.py` / `main.py` /
`db/models.py` / `db/session.py` / `open_funnel.py` / `trip_ledger.py` /
`db/ai_auto_repositories.py` / `live_auto/executor.py` / `apps/web/lib/store.ts` /
`NavRail.tsx` / `page.tsx` / `httpTimeout.ts` / `lib/export.ts` / `next.config.mjs` 共 20 余处，
其中**绝大多数属实**（`_pair_round_trips` 的 id-vs-ts 判据、`trades.reason` 的 128 与 221 笔全截断、
`create_all` 不补列、`truth_sources` 的条件注册、`AI_TRADE_` 前缀白名单、`trip_meta_wire` 的
「算不出即省略」、`live_order_audit` 的 `ix_live_audit_account`、`.env.example` 双向登记的既有先例）。
下面只列**不属实或不完备**的部分。

### P0（阻断合入；必须在动手前改掉设计本身）

| # | 严重度 | 结论 | 证据 | v2 处置 |
|---|:---:|---|---|---|
| **RP0-1** | P0 | **后训练样本里的 `messages` 不是模型真正看到的输入**。v1 §2.5.2 把样本写成 `[system, user, assistant]` **单轮**三段，并用 `prompt_sha256` 担保「输入是真的」。但真实请求是 `messages=self._context + [user_msg]`（`decider.py:697-699`），而 `_context` 是**逐轮累积**的三元组（`decider.py:1080-1084`：user 决策提示词 / assistant `tool_use` / tool `tool_result`），中途还会被 `_trim_context()` 左裁、被 `summarize()` 整体换成「摘要种子」。更糟的是 `prompt_sha256` 这道担保**结构上看不见这件事**：它只覆盖最后一条 user 文本，存档 sha 与重建 sha 都来自同一个纯函数 `build_decision_prompt(payload)`，于是 `shaMatch=true` 与「样本是假的」可以同时成立——一道**给出虚假安全感**的护栏比没有护栏更坏，且它恰好违反 §2.2.4 自己立的最高纪律。第二半：重试轮与追问轮会把 `_retry_hint_suffix(hint)` / `extra_instruction` 追加进真正送出的那条 user 文本（`decider.py:604-608`），而写点只拿得到 `payload`，永远算不出那个后缀。 | `decider.py:697-699` / `1080-1084` / `604-608` / `1122-1145` | 见 §2.2.4bis（新增）、§2.5.2（重写样本形状与 `contextMode`）、§5.1（新增 `context_ref_json` / `context_turn_json`）、§七 第 15 项、§8.1 新用例、§8.3 A3 重写 |
| **RP0-2** | P0 | **导出流撞在 Next 代理的 300 秒硬墙上，而 v1 的唯一闸是行数不是时间**。`apps/web/next.config.mjs` 的 `experimental.proxyTimeout: 300000` 对**每一个** `/api/py/*` 请求生效，`<a download>` 走的正是同一条 rewrite。`EXPORT_MAX_ROWS=200000` × §2.3 自己给的 6–10 KB/行 ≈ **1.2–2 GB**，早在行数触顶之前就会超过 300 秒被传输层重置——本仓已经为同一形状付过三次代价（`docs/diagnoses/desktop-backtest-proxy-timeout-socket-hang-up/`、`factor-lab-train-long-range-socket-hang-up/`、`desktop-paper-batch-start-socket-reset/`）。v1 的 `eof` 契约能让用户**发现**文件不完整，但设计里没有任何一处让它**不发生**。 | `apps/web/next.config.mjs:26-28`；spec §2.3 自身的量级估算 | 见 §2.5.1（新增墙钟预算 + 分片契约 + `truncatedReason`）、§2.6.4（分片下载）、§4.7、§七 第 16 项、§8.1 新增跨仓源码门禁、§九 R8 重写 |
| **RP0-3** | P0 | **门禁链既没有取数真源，正文又自造了第二份层名表，且成员是错的**。§2.6.2 第 2 段逐字列出八个中文层名（`基础策略铁律 / edge 门禁 / 仓位求解与刹车 / 持仓周期契约 / 同向共识 / 执行周期趋势基调 / 换手纪律 / 利润保护`），而本仓的层名唯一真源是 `open_funnel.LAYERS`（13 项）+ `LAYER_LABELS`，且 `gate_veto.py` 的开篇纪律 1 明写「**绝不**在本模块另写一张层名表……漂移的表现（模型与界面读到一个不存在的层名）**不报任何错**」。v1 那张表与真源逐项对不上：字面不同（`基础策略铁律` vs `基础策略·MA 通道`、`edge 门禁` vs `最小 edge 门禁`）、漏了四层（`orphanPosition` / `profitCooldown` / `confidence` / `reflection`），而**「利润保护」根本不是开仓漏斗的一层**——`_maybe_profit_exit` 的 docstring 第一句就是「本层只会产生 `close`，**永远不会**阻止或改写模型的任何决策」。此外 §2.2 / §五 从头到尾没说 `gates_json` 从哪里取数，而「未装配 / 放行 / 否决」三态需要的 `armed` 与 `readings` 今天分散在八个 runtime 上，压根不存在一份聚合结构。 | `open_funnel.py:48-63`；`gate_veto.py:17-22`；`session.py:3274-3280` | 见 §2.2.7（新增 `gates_json` 的唯一装配点与取数表）、§2.6.2 第 2 段重写、§六 第 6 条、§8.1 / §8.2 新增两条层名门禁 |

### P1（合入前必须改；v2 已在正文改掉）

| # | 严重度 | 结论 | 证据 | v2 处置 |
|---|:---:|---|---|---|
| **RP1-1** | P1 | **唯一写点的方法名不存在**。`session.py` 里没有 `_run_round`；决策轮主体是 `_run_decision_round`（`session.py:1847`），`try` 起于 1856、`await self._post_round(ref_px, ts)` 在 2052、`except asyncio.CancelledError` 在 2053、`except Exception` 在 2054。v1 给的 `1876-2052` 与 `2053-2059` 两个区间都偏。 | `session.py:1847` / `2052-2059` | §2.2.1 逐字改正 |
| **RP1-2** | P1 | **写点位置的第 3 条理由是错的，而它掩盖了一个真的缺口**。v1 称「必须在 `_post_round` 之后：本轮的成交落库与归因都在那之前完成，`executed` 里的 `clOrdId` 此刻才是最终值」——`executed` 早在 `session.py:1981` 就绑定完毕，`_post_round` 再怎么跑也不会改它，故这条理由是空的。真实情况相反且更要紧：`_post_round` 内的 `_maybe_profit_exit` **会在本轮再产生一笔 `close` 成交**（`session.py:3209-3214` / `3274-3280`），而那笔成交**不在** `executed` 里 ⇒ 决策行的执行断面会漏掉同轮的利润保护平仓。 | `session.py:1981` / `3209-3214` / `3274-3280` | §2.2.1 理由 3 重写；新增 `post_round_fills` 补采与 §2.4.2 的读时兜底 |
| **RP1-3** | P1 | **关停步没说插在哪，而那张表是被两处用例冻结的**。`test_main_shutdown.py:160-168` 断言 14 个步骤名的**完整有序列表**；`test_ai_intel_wiring.py:392` 断言 `names.index("ai_trade_intel") == 6`。插在第 7 位之前会同时打翻两条，插在别处也会打翻前者，而 §3.2 的修改清单里**没有这两个测试文件**，§8.3 的 A4 却承诺「全量 pytest 与合入前同绿」。§九 R6 称位置「由 §2.2.2 与 §七校验 1 钉死」，但那两处一个字都没提位置。 | `test_main_shutdown.py:160-168`；`test_ai_intel_wiring.py:387-393`；`main.py:113-152` | §2.2.2 约束 6 补位置；§3.2 补两个测试文件；§九 R6 重写 |
| **RP1-4** | P1 | **`_journal_round` 9 个形参，直接违反 `CLAUDE.md` 的「函数形参不超过 5 个」**，而该文件的规范原文是「多了请改用配置对象、命名参数或聚合结构体，禁止靠位置堆参数」。 | `CLAUDE.md` §二；spec §2.2.1 的签名 | §2.2.1 改为单个冻结入参对象 `RoundJournalInput` |
| **RP1-5** | P1 | **序列化跑在热路径上、可以抛，而唯一的护栏只查 `await`**。§2.2.2 约束 1 要求「入队即已序列化」，即 `json.dumps` 发生在决策轮的 `try` 里；用例 A-1 只 AST 断言 `ast.Await` 计数为 0，抓不到 `TypeError: Object of type X is not JSON serializable`。一次序列化失败会把一轮**已经成功成交**的决策变成 `AI_TRADE_ROUND_FAILED`——审计需求正好害了它声称绝不碰的东西。§九 R1 提了「全程 try/except」，但规范章节 §2.2.1 没有把它写成约束，也没有任何用例守它。 | spec §2.2.1 / §2.2.2 / §8.1 A-1 | §2.2.1 补硬约束（整体 `try/except Exception` + `default=str`），A-1 加第二条 AST 判据 |
| **RP1-6** | P1 | **`linkConfidence="exact"` 的锚点被高估，且纸面会话下默认筛选可能整段清零**。v1 §2.4.2 称「`executor.py:1388` 证实开仓与平仓两条路径都会带上它」，源码原文却是「**clOrdId / ordId 为 best-effort 可选**……**缺失即省略键**」（`live_auto/executor.py:1385-1389`）。更要紧的是 AI 自动交易支持 `creds is None` 的**纸面 / 无凭证**会话（`manager.py:2290-2295` 明写这条分支），那种会话既没有交易所单号也没有 `live_order_audit` 行 ⇒ `minLinkConfidence="exact"` 的默认值会让它导出 **0 条**样本。 | `live_auto/executor.py:1385-1389` / `1506-1511`；`manager.py:2290-2295` | §2.4.2 步骤 1 改写；§2.5.2 `minLinkConfidence` 默认改为按会话形态自适应并在预检里说明 |
| **RP1-7** | P1 | **总闸关 + 已持久化的 `activeView="journal"` ⇒ 开屏空白工作台**。`store.ts` 的 `partialize` **持久化 `activeView`**；`AI_TRADE_JOURNAL_UI_ENABLED=false` 时 NavRail 不渲染该项、`page.tsx` 也不渲染该视图，于是老用户重启后看到的是一屏什么都没有、且没有任何解释的空白。 | `apps/web/lib/store.ts`（`partialize` 含 `activeView`；`version: 31`）；spec §2.6.1 | §2.6.1 补水合守卫；§8.2 补用例 |
| **RP1-8** | P1 | **被整轮跳过的决策轮在日志里是一个无解释的空洞**。`_maybe_skip_round`（`session.py:1815-1846`）在 `payload` 之前就 `return`，它自增 `rounds_skipped`、推一条 `round_skipped` WS 事件，但 §2.2.6 的事件码表里**没有它**。结果是：运行期的实时轨迹上看得见「这一轮被跳过了」，事后的审计台上却只有一段没有任何行的时间——而「为什么 14:30 没有决策」正是审计要回答的问题。 | `session.py:1815-1846` | §2.2.6 补 `ROUND_SKIPPED` 事件码；§2.6.2 / §8.1 同步 |
| **RP1-9** | P1 | **E-2「同一用例同时跑前后端两份实现并断言逐字节相同」写不出来**：后端用例跑在 pytest、前端用例跑在 vitest，一个进程里没有另一份实现；且 `escapeField` 在 `apps/web/lib/export.ts` 里是**模块私有**（未 export），前端侧也拿不到它单测。照 v1 写下去的结果是这道门禁**根本不存在**，而两处实现会安静地漂移。 | `apps/web/lib/export.ts:12-22`（`escapeField` 未导出） | §2.5.1 改为**共享黄金样本**（committed fixture）双向断言；§8.1 / §8.2 对应改写 |

### P2（记录在案；v2 已顺手改正文字面，不改设计）

| # | 结论 | 证据 |
|---|---|---|
| RP2-1 | 端点计数错：§1.2 与 §3.1 写「8 个只读 + 1 个剪枝」，§四实际列出 **9 个只读 + 1 个剪枝 = 10 个**。 | spec §四 4.1–4.8 |
| RP2-2 | 文件计数错：§3.1 表头「12 个」实列 **13** 行；§3.3 表头「15 个」实列 15 行 + 正文另述 2 个 = **17**。 | spec §3.1 / §3.3 |
| RP2-3 | `ai_auto/executor.py` **不存在**；`clOrdId` 的写点在 `services/quant/app/live_auto/executor.py:1388`（AI 会话经 `manager._build_executor` 复用 `LiveExecutor`）。 | `ls services/quant/app/ai_auto/` |
| RP2-4 | `_last_decision` 的赋值在 `session.py:3663`（3664 是 `self._decisions.append(data)`）。 | `session.py:3663-3664` |
| RP2-5 | §十一 D3 称「按 `account_id + created_ts` 范围后再过滤」，但 `live_order_audit` 的索引是 `ix_live_audit_account("account_id", "id")`，`created_ts` 不在索引里；正确说法是按 `account_id + id` 范围。 | `db/models.py:209-210` |
| RP2-6 | §2.6.3 称「本轮新增的 4 张表」，§3.3 实际只有 3 张（决策 / 成交与订单 / 系统事件）。 | spec §3.3 |
| RP2-7 | §3.4 把 `/ai-trade/journal/export` 也登进 `httpTimeout` 长跑档，但导出走 `<a download>` 的浏览器导航，根本不经过 `timeoutFor` ⇒ 那一行是死码；真正需要它的只有 `/export/preview`。 | `apps/web/lib/httpTimeout.ts:147-156`；spec §2.6.4「刻意不用 fetch + Blob」 |
| RP2-8 | `session.py` 今天已 **4162 行**，远超 `CLAUDE.md` 的 800 行上限（既有欠账，非本轮引入）。本轮只允许往它加两个薄方法，任何新逻辑一律落在 `journal/` 包内。 | `wc -l services/quant/app/ai_auto/session.py` |
| RP2-9 | `db/models.py` 里 `AiExperienceEvent` 的 docstring 原文说的是「**四个** `_ensure_*_columns` 补丁」，而 `db/session.py` 今天是 **5 个**。v1 说 5 是对的，但「逐字沿用」那句会把读者引到一句已经过期的原文。 | `db/session.py:96-100`；`db/models.py:646-648` |

**未采纳的质疑（记录下来，免得下一轮再问一遍）**：① `decider` 是否拿得到系统提示词——拿得到，
`self.system_prompt` 在 `decider.py:306` 就是公开属性（由 `manager.py:813` 构造后传入），§2.2.4 的
内容寻址方案可行；② 新表是否真的零迁移——是，`db/session.py:93-100` 的 `create_all` + 5 个
`_ensure_*_columns` 的分工与 v1 的描述一致；③ `/health` 加法键是否会被静默剔除——不会，该端点
**未声明** `response_model`（`api/market.py:31-53` 且注释已就此成文）；④ 按 `trades.id` 而非 `ts`
配对——判据属实，`db/ai_auto_repositories.py:80-85` 的 docstring 逐字支持 v1 的引用。

---

## 一、概述

### 1.1 现状：交易发生了，但「为什么这么交易」在进程退出的那一刻就没了

本仓库的 AI 自动交易已经是一套相当完整的决策系统：`ai_auto/` 下 90 个模块，一轮决策要穿过
唤醒判定 → 行情摘要 → 信号盘 → 多周期上下文 → 情报注入 → 先验 → LLM 研判 → 下单前反思 →
基础策略铁律 → edge 门禁 → 仓位求解 → 持仓周期契约 → 执行 → 归因，十余道关卡。这条链路今天
**在运行期是完全可见的**：`session.py` 每轮往 WS 推 `agent_trace`（18 个相位）与 `decision`
事件，前端 `AiTradeAgentTrace` 逐轮回放。

但**这份可见性没有一个字节落在磁盘上**：

| 事实 | 现场 |
|---|---|
| 轮次认知轨迹只在内存里存 **20 条**，随会话对象回收 | `ai_auto/trace.py:_TRACES_MAX = 20`；`session.py:531` `self._traces: deque = deque(maxlen=_TRACES_MAX)`，注释原文「瞬态环形，仅内存 / 快照回放」 |
| 决策事件只在内存里存 **50 条** | `session.py:176` `_DECISIONS_MAX = 50`；`session.py:528` `self._decisions = deque(maxlen=50)` |
| 落库的只有一份 **≤50 条的紧凑摘要**，且只在会话终结时写进 `metrics_json` | `session.py:4001` `metrics["decisionsDigest"] = _digest_decisions(...)`；该摘要每条只有 7 个键（`ts/round/action/executed/confidence/reason[:N]/reflectionVerdict`），**没有上下文、没有模型输出、没有门禁链** |
| 研判思维链、反思复核推理、提示词全文、工具入参原文、token 用量、逐轮耗时 —— **一个都不落库** | `decider.py:365/373/376` 的 `_journal` / `_last_analysis` / `_last_reflection_analysis` 全是进程内 list/str；`_journal` 甚至在每次滚动摘要成功后被**清空**（`decider.py:1145`） |

于是今天这套系统答不上来三个问题，而这三个正是用户点名的三件事：

1. **可查看**：「2026-09-01 14:32 那一单，模型当时看到了什么、说了什么？」——重启后无解。
2. **可审计**：「这 31 笔违反最短持仓被平掉的单，是谁在什么判据下放行的？」——只剩一行 ≤128
   字符的 `trades.reason`（`models.py:87` 的注释里明写着它此前是 `String(64)` 且 **221 笔 LLM
   平仓的 reason 长度全部恰好 64 = 100% 被静默截断**）。
3. **可导出用于后训练**：后训练需要 `(输入上下文, 模型输出, 结果标签)` 三元组。今天三样**全缺**：
   输入在 `payload` 里（不落库）、输出在 `_StreamOutcome.raw` 里（不落库）、标签虽然存在
   （`trip_ledger.trip_meta_wire` 已经把 `realizedBps` / `rMultiple` / `exitSource` 写进平仓
   成交的 `explain_json.meta`）却**无从与产生它的那一轮决策关联**。

### 1.2 本轮做什么

新建一条**与交易链路完全解耦、只读取不干预**的日志通路，并给它一个一等的管理台入口。四条轴：

- **轴 A · 采集（写侧）**：新增 `ai_auto/journal/` 包与一个进程级 `JournalWriter`。会话在每轮
  决策收尾处**同步入队**一条已完成序列化的记录，写库由**单一后台任务**批量完成。热路径上
  **没有 await、没有事务、没有异常出口**——入队失败只记一个计数器，绝不让审计需求碰坏真实资金
  的交易。三档采集粒度（`minimal` / `standard` / `full`）决定落多少字节。
- **轴 B · 存储**：三张**新表**（`ai_trade_decision_logs` / `ai_trade_event_logs` /
  `ai_trade_prompt_profiles`）。全部是新表 ⇒ `Base.metadata.create_all` 自动建出 ⇒ **零迁移
  代码、老库首次启动即获得、不可能 `no such column`**（这条纪律在 `db/models.py:647` 的
  `AiExperienceEvent` docstring 里已经成文，本轮逐字沿用）。附带保留策略（天数 + 行数双闸）。
- **轴 C · 读侧与关联**：新增 `api/ai_trade_journal.py`（**9 个只读端点 + 1 个剪枝端点**）。
  成交与订单**不复制**：日志台按 `clOrdId` 把「决策轮」↔「成交」↔「订单回执」↔「往返结果」
  串起来，并**如实标注关联置信度**（`exact` / `heuristic` / `none`）。
- **轴 D · 导出**：三种流式格式——`audit-csv`（人看 / Excel）、`audit-jsonl`（全保真归档）、
  **`sft-jsonl`（后训练样本）**。后训练样本是 Messages 形态的 `{system, user, assistant(tool_use)}`
  三段 + `tools` 工具定义 + `label`（已实现盈亏 / bps / R 倍数 / 退出归因）+ `quality`
  （是否降级 / 是否被门禁否决 / 是否已结算），并**默认排除被确定性门禁否决的轮次**——把模型
  说了但被拒绝的动作当正样本，训练出来的就是被拒绝的行为。
- **轴 E · 管理台界面**：左侧主导航新增第四项「交易日志」，进入一个**列表 + 详情双栏**的日志
  控制台（决策轮次 / 成交与订单 / 系统事件 三页 + 导出中心 + 采集健康度）。

### 1.3 明确的非目标（本轮不做）

| # | 非目标 | 理由 |
|---|---|---|
| N1 | 不给 `trades` / `strategy_runs` / `live_order_audit` 加任何列 | 「零 DB 迁移」是本轮硬纪律；`create_all` 不给既有表补列，加列就要写 `_ensure_*_columns` 补丁（`db/session.py:95-99` 已有 5 个先例），而本轮完全不需要 |
| N2 | 不做多租户 / 登录鉴权 | 本产品是单机桌面工具，"管理员界面"= 应用内的管理台视图；引入鉴权是一次产品语义变更 |
| N3 | 不给回测车道（`ai_auto/backtest.py` / `backtest_manager.py`）接线 | 回测是合成数据，混进后训练集会污染样本；由 AST 门禁冻结（§8.2 G-4） |
| N4 | 不做日志的实时 WS 推送 | 运行期的实时轨迹已由既有 `agent_trace` 承担；日志台是**事后**面，REST 分页足够，加一条 WS 只是第二份真值 |
| N5 | 不做自动上传 / 远程归档 | 导出是**用户主动**的动作。数据离开本机必须是一次显式点击 |
| N6 | 不做训练本身（微调脚本 / 数据配比 / 评测） | 本轮只负责产出**格式正确、标签可信、来源可追**的样本文件 |

---

## 二、技术设计

### 2.1 分层与数据流

```
                    ┌──────────────────────────── 交易热路径（本轮零改判定）────────────────────────────┐
唤醒 → 建 payload → decider.decide() ─► TradeDecision ─► 13 层确定性门禁 ─► executor 下单 ─► _emit_decision
                    └───────────────────────────────────────┬──────────────────────────────────────────┘
                                                            │ 同步 put_nowait（已序列化，不 await、不抛）
                                                            ▼
                                        ┌───────────────────────────────────┐
                                        │  JournalWriter（进程级单例）       │
                                        │  asyncio.Queue(maxsize=2000)      │
                                        │  单一 drain task：批量 32 / 1.0s  │
                                        └───────────────┬───────────────────┘
                                                        │ session.add_all + commit（WAL）
                                                        ▼
        ┌────────────────────────┬───────────────────────────────┬──────────────────────────────┐
        │ ai_trade_decision_logs │ ai_trade_event_logs           │ ai_trade_prompt_profiles     │
        │ 一轮一行（新事实）      │ 生命周期 / 风控 / 故障（新事实）│ 系统提示词 + 工具 schema     │
        └───────────┬────────────┴───────────────┬───────────────┴──────────────┬───────────────┘
                    │                            │                              │
                    │  ← 只读引用，绝不复制 →    │                              │
        ┌───────────▼──────────┐   ┌─────────────▼────────────┐                 │
        │ trades（既有真源）    │   │ live_order_audit（既有）  │                 │
        │ + explain_json.meta   │   │ 订单回执 / 拒单码         │                 │
        │   (realizedBps/R/…)   │   └──────────────────────────┘                 │
        └───────────┬──────────┘                                                │
                    │                                                            │
                    └────────────► journal/linking.py（纯函数关联）◄─────────────┘
                                            │
                    ┌───────────────────────┼──────────────────────────┐
                    ▼                       ▼                          ▼
          GET /journal/decisions   GET /journal/fills        GET /journal/export
          （列表 / 详情 / 事件）    （成交 ⇄ 订单 ⇄ 往返）    audit-csv / audit-jsonl / sft-jsonl
                    │                                                  │
                    ▼                                                  ▼
          前端「交易日志」管理台（NavRail 第 4 项）           浏览器下载 → 离线分析 / 后训练
```

**一条贯穿全设计的原则**：日志层**只读取，绝不参与任何判定**。`journal/` 下的任何模块都不得被
`session.py` / `decider.py` / `manager.py` 的**判定表达式**引用；它们只被 `session.py` 的三个
写点（§2.2.1）与 `main.py` 的编排点调用。由 §8.2 的 AST 门禁 G-3 冻结。

### 2.2 轴 A：采集层（写侧）

#### 2.2.1 唯一写点与时序

**决策行的唯一写点**在 `ai_auto/session.py` 的决策轮主体 `_run_decision_round`
（**注意方法名**：本仓没有 `_run_round`；`session.py:1847` 起，`try` 起于 1856，
`await self._post_round(ref_px, ts)` 在 2052，`except asyncio.CancelledError` 在 2053、
`except Exception` 在 2054）的 `try` **末尾**，即 `_post_round` 之后、`except` 之前，插入一行：

```python
            self._journal_round(RoundJournalInput(
                round_no=round_no, ts=ts, wake=wake, payload=payload,
                decision=decision, stage1_decision=stage1_decision, executed=executed,
                degrade=degrade, gate_degrade=gate_degrade, llm_degrade=llm_degrade,
                reflection_verdict=reflection_verdict, reflection_reason=reflection_reason,
                post_round_fills=self.executor.trades_since(fill_watermark)))
```

**入参必须是一个冻结的聚合对象，不得堆位置形参**（RP1-4）：`CLAUDE.md` §二把「函数形参不超过
**5** 个」写成硬阈值，原文是「多了请改用配置对象、命名参数或聚合结构体，**禁止靠位置堆参数**」。
`RoundJournalInput` 是 `journal/records.py` 里的 `@dataclass(frozen=True, slots=True)`，
**只承载引用、不做任何计算**；裁剪与序列化在 `build_decision_row()` 里做（单一真源，§2.2.3）。
`fill_watermark = len(self.executor.trades)` 在 `_execute_decision` **之前**取一次（同步、无 await）。

**为什么必须是这个位置**（三条，都可被用例证伪）：

1. **必须在 `commit_round` 之后**：`commit_round` 收的是**门禁后的最终 decision**
   （`session.py:2000` 注释原文「R-P1-3：commit_round 收门禁后的最终 decision」）。排在它之前，
   记下来的最终动作会是模型的原意而不是真正发生的事。
2. **必须在 `_emit_decision` 之后**：`_last_decision` 在那里赋值（`session.py:3664`），日志行的
   执行断面直接复用它，杜绝「日志里的执行结果与推给界面的不是同一份」。
3. **必须在 `_post_round` 之后**——但**不是** v1 说的那个理由（RP1-2）。`executed` 早在
   `session.py:1981` 就已绑定，`_post_round` 再怎么跑都不会改它，故「`clOrdId` 此刻才是最终值」
   是一句空话。真实理由是相反方向的、也更要紧的一条：**`_post_round` 自己会再产生成交**。
   它是 `_emit_equity` / `_emit_position` / `_maybe_profit_exit` / `_maybe_session_exit` 四步
   （`session.py:3209-3214`），其中 `_maybe_profit_exit` 会在本轮**市价平掉**一笔持仓
   （`session.py:3274-3280`），而那笔成交**不在** `executed` 里。排在它之后，决策行才能把
   「这一轮模型说了什么 + 这一轮真正发生了哪几笔成交」记在同一行上。
   > 因此写点必须额外带上 `post_round_fills`：以 `_execute_decision` **之前**取的
   > `fill_watermark`（= `len(executor.trades)`）为界，把本轮 `executor.trades` 上新增的**全部**
   > 腿一并落进 `fill_ref_json.legs`（每腿只留 `side/px/sz/fee/pnl/clOrdId/ordId/source`）。
   > 没有这一条，「利润保护在决策轮内平掉的那一笔」在日志台上会表现为「模型说开多、然后什么都
   > 没发生、然后不知从哪冒出来一笔平仓」——而利润保护正是本仓实测唯一为正的确定性离场通道。
   > 由用例 A-4 冻结（构造一轮：开仓成交 + `_post_round` 内保护平仓 ⇒ `legs` 恰好两条）。

**方法签名必须是同步的** `def _journal_round(...) -> None`，**不得是 `async def`**。理由是硬的：
决策轮跑在会话的取消域里，任何新的 await 点都是一个新的 `CancelledError` 落点，而本仓库已经为
「关停链上多一个 await 就泄一条野连接」付过代价（`_rearm_feed` 换代竞态，见
`docs/plans/ai-trade-feed-self-healing/`）。审计需求**不配**在真实资金的取消语义上冒一点风险。

**并且它的函数体必须整体裹在一个 `try / except Exception` 里**（RP1-5）。这条与「零 await」
同等硬，理由同样具体：§2.2.2 约束 1 要求**入队即已序列化**，也就是说 `json.dumps` 跑在决策轮的
`try` 内；一次 `TypeError: Object of type X is not JSON serializable` 会把一轮**已经成功成交**的
决策整个翻成 `AI_TRADE_ROUND_FAILED`——审计需求恰好害了它声称绝不碰的东西。三条具体要求：

- `_journal_round` / `_journal_event` 的**整个函数体**在一个 `try: ... except Exception: ...` 内，
  `except` 分支只做 `serialize_errors += 1` + 一次 `logger.warning`，**绝不重抛**；
- 所有 `json.dumps` 一律带 `default=str`（未知对象降级成它的 `repr`，而不是抛）；
- **`asyncio.CancelledError` 不在 `Exception` 之下**，故它仍照常向上传播，取消语义逐字不变。

由用例 A-1 的**两条** AST 判据冻结：① `_journal_round` / `_journal_event` 的函数体内
`ast.Await` 计数为 0；② 两个方法体的顶层语句**恰好是一个 `ast.Try`**，且其 handler 覆盖
`Exception`。只查 ① 是 v1 的缺口——「不会 await」与「不会抛」是两件不同的事。

**异常行的写点**在同一个 `try` 的 `except Exception` 分支（`session.py:2054-2059`；
它上面的 `except asyncio.CancelledError: raise` 在 2053，**绝不在那一支写任何东西**——
一次用户暂停不是一条审计事实），追加一行：

```python
            self._journal_event("round", "AI_TRADE_ROUND_FAILED", "error",
                                "本轮决策异常，已跳过", detail={"round": round_no, "ts": ts})
```

**刻意不在异常分支写决策行**：失败可能发生在 `payload` 尚未绑定之前，一个字段半空的决策行比
一条明确的错误事件更坏。

#### 2.2.2 `JournalWriter` 的队列语义（背压、丢弃、排空）

```python
class JournalWriter:
    def start(self) -> None: ...                     # 幂等；创建单一 drain task
    async def stop(self, timeout_s: float) -> None:  # 有界 drain → cancel → **await**
    def note_decision(self, row: dict) -> None: ...  # 同步、绝不抛
    def note_event(self, row: dict) -> None: ...     # 同步、绝不抛
    def stats(self) -> dict: ...                     # 观测面（§4.6）
    def reset(self) -> None: ...                     # 测试夹具用（仿 okx-link-resilience 四单例）
```

七条硬约束：

1. **入队即已序列化**。`note_*` 收到的是**已经 `json.dumps` 过的字符串字段**的 dict，不是活的
   对象引用。理由：`self._round_trace` 是**原地累积**的（`session.py:2296` `trace = self._round_trace`
   之后逐相位改写），把它的引用排进队列，落库时写下的是**下一轮的内容**。序列化在入队时完成
   既冻结了内容，又把上限施加在了热路径上（可控、可测）。由用例 A-2 冻结（入队后修改源 dict，
   落库内容不变）。
2. **队满丢新条，绝不丢旧条、绝不阻塞**。`put_nowait` 撞 `QueueFull` ⇒ `dropped_total += 1` 并
   按 kind 分计。丢旧条会**静默改写历史**且读侧无法察觉；丢新条则由 `stats.dropped` 与管理台的
   琥珀色横幅明示（§2.6.5）。
3. **两级卸载**：队列长度 > `queue_max × 0.8` 时，`note_event(level="info")` **立即被丢弃**并
   计入 `shed_info_events`，为决策行保留槽位。决策行是本特性的主产物，info 事件是配菜。
4. **drain 循环单例**：`await queue.get()` 拿到第一条后，用 `get_nowait()` 贪心收集至多
   `batch_size - 1` 条，一次 `session.add_all()` + `commit()`。**绝不在决策协程里开事务**——
   SQLite 单写者，并发事务会把写锁竞争带进热路径。
5. **落库失败不重试、不冒泡**：`except Exception` ⇒ `write_errors += 1` + `logger.warning`
   + **丢弃该批**。一个会无限重试的审计写入，会在磁盘满的时候把整个后端拖死。
6. **`stop()` 必须先有界 drain、再 cancel、最后 `await` 那个 task**。只 cancel 不 await 是本仓
   库已经踩过的坑（`GeneratorExit` 会在别的模块里报红，见
   `docs/plans/stability-hardening-audit/`）。超时未排空的条数计入 `dropped_on_shutdown`，并在
   **下次启动**时由 `runtime.py` 写一条 `journal.shutdown_drop` 事件行如实记录——「没写完」
   是要被看见的事实。

   **关停步的位置是硬性的，且它撞在两处冻结注册表上（RP1-3）**。`_shutdown_steps()`
   （`main.py:113-152`）今天是 14 步，新步 `ai_trade_journal` **必须恰好插在第 8 位**，即紧跟
   `ai_trade_intel` 之后、`ai_backtest` 之前：

   ```
   … ai_trade → ai_trade_experience → ai_trade_intel → ★ai_trade_journal → ai_backtest → … → db_engine
   ```

   三条判据各自独立：① 必须在 `ai_trade` **之后**——`manager.shutdown()` 里还会写
   `GROUP_STOPPED` / `GROUP_ENDED` 事件行（§2.2.6）；② 必须在 `db_engine` **之前**——drain task
   要用引擎提交，否则它会活着穿过 `dispose_engine()`；③ 必须在 `ai_trade_intel` **之后**——
   `test_ai_intel_wiring.py:392` 逐字断言 `names.index("ai_trade_intel") == 6`，插在它前面会
   当场把那条打红。

   **同时必须改一个既有测试文件**：`test_main_shutdown.py:160-168` 断言的是 14 个步骤名的
   **完整有序列表**，其 docstring 原文写着「这张清单是一份**冻结注册表**：加一步就必须在这里改
   一行，否则新步骤有没有真的接上完全无从判断」。§3.2 已把它列进修改清单——漏了它，
   §8.3 的 A4「全量 pytest 与合入前同绿」就是一句假话。
7. **总闸关闭时 `start()` 直接返回、`note_*` 直接 return**。不建 task、不建队列、零内存占用。
   `AI_TRADE_JOURNAL_ENABLED=false` ⇒ 决策轮的行为与合入前**逐字节相同**。

#### 2.2.3 三档采集粒度

`AI_TRADE_JOURNAL_CAPTURE_TIER` ∈ `{minimal, standard, full}`，默认 `standard`。
裁剪在 `journal/records.py::build_decision_row()` **一处**完成（单一真源）：

| 字段 | minimal | standard（默认） | full |
|---|:---:|:---:|:---:|
| 结论（动作 / 仓位 / 置信 / 委托 / 限价 / 最终动作 / 降级码 / 反思裁决） | ✓ | ✓ | ✓ |
| 执行断面（是否成交 / 价 / 量 / 费 / 盈亏 / `clOrdId` / `ordId` / 拒单码） | ✓ | ✓ | ✓ |
| 门禁链 `gates_json`（**13 层**门禁各自的装配态 / 判定 / 读数，层集取自 `open_funnel.LAYERS`，§2.2.7） | ✓ | ✓ | ✓ |
| 遥测（provider / model / inputTokens / outputTokens / 耗时 / 重试次数 / 深度档） | ✓ | ✓ | ✓ |
| `payload_json`（喂给模型的结构化上下文 = 提示词的唯一真源） | ✗ | ✓ | ✓ |
| `output_json`（模型工具入参原文） | ✗ | ✓ | ✓ |
| `analysis_text`（研判思维链） | ✗ | 截断至 `TEXT_MAX_CHARS`（默认 4000） | 截断至 `max(TEXT_MAX_CHARS, 20000)` |
| `reflection_text`（反思复核推理） | ✗ | 同上 | 同上 |
| `thinking_text`（深度思考） | ✗ | ✗ | 同上 |
| `prompt_text`（渲染后的用户消息原文） | ✗ | ✗ | ✓ |

**`minimal` 档不足以做后训练**（缺输入与输出），管理台的导出对话框必须在采集档为 `minimal` 时
**禁用 `sft-jsonl` 选项并说明原因**——一个点了会导出空文件的按钮比没有按钮更坏。

`payload_json` 超过 `PAYLOAD_MAX_CHARS`（默认 24000）时**截断并置 `payload_truncated=1`**。
被截断的行在 SFT 导出中**一律排除**（截断的上下文重建不出原提示词），并计入
`skipped.payloadTruncated`。

#### 2.2.4 提示词档案（`ai_trade_prompt_profiles`）与提示词的可重建性

系统提示词（`prompts.build_ai_trade_system_prompt`）与决策工具 schema
（`prompts.build_decision_tool_schema`）在一次会话内**恒定**，且体量大（系统提示词含方向宪章、
趋势宪章、纪律段等，量级 KB）。逐轮存一份是纯浪费。

做法：**内容寻址、写一次**。
`sha256(system_prompt + "\x00" + tools_json)` 作主键，`INSERT OR IGNORE` 语义（先 `SELECT`，
不存在才 `add`）。决策行只存 `profile_sha256`。

用户消息（决策提示词）是**每轮不同**的。它**基本上**是 `payload` 的纯函数
（`decider.py:603` `prompt_text = build_decision_prompt(payload)`），但**不完全是**——
`decider.py:604-608` 会在重试轮追加 `_retry_hint_suffix(hint)`、在追问轮追加
`extra_instruction`。因此 `prompt_sha256` **必须取自真正送出去的那一条文本**，而不是写点
按 `payload` 重算一遍的那一条（重算永远看不见后缀，于是「存档 sha == 重建 sha」与
「样本是假的」可以同时成立）。取数口径见 §2.2.4bis 的 `last_request_shape.finalUserSha256`。

`standard` 档存 `payload_json` + `prompt_sha256`，导出时**重新渲染并比对**：

- 渲染结果 sha256 与存档一致 ⇒ `promptSource="rendered"`，该**单轮**合格；
- **不一致**（提示词渲染代码改过 / 该轮带过重试后缀）⇒ **该样本被排除**，计入
  `skipped.promptDrift`，并在导出 manifest 里如实报数；
- `full` 档存了 `prompt_text`（**存的是含后缀的原文**）⇒ 直接用，`promptSource="stored"`，
  不受代码漂移影响。

这是本设计里最重要的一条诚实性约束：**宁可少导出一条样本，也绝不导出一条"我们以为模型看到了
这个"的假样本**。要做长期后训练数据积累的用户应当把采集档置为 `full`——导出对话框会在
`standard` 档时明示这一权衡。

#### 2.2.4bis 上下文链：模型看到的是一段**多轮对话**，不是一条 user 消息（★ 评审 RP0-1）

这是 v1 里最严重的一条缺陷，且它的形状正是本仓库反复付过代价的那一种——**判据取自不携带该
信息的入参**。事实很硬：

```python
# decider.py:697-699（_stream_decision 唯一的出站调用）
async for ev in self._llm.stream(system=self.system_prompt,
                                  messages=self._context + [user_msg],   # ← 不是 [user_msg]
                                  tools=[self._tool]):
```

`self._context` 是**逐轮累积**的：`commit_round` 每轮往里追加**三条**消息
（`decider.py:1080-1084`）——`user`（该轮决策提示词）/ `assistant`（`tool_use`，入参是
**门禁之后**的最终 decision）/ `tool`（`tool_result`，内容是 `executed` 再合并反思与门禁注记）。
中途还会被 `_trim_context()` 左裁、被 `summarize()` 整体换成一段「摘要种子」
（`decider.py:1122-1145`）。

于是 v1 §2.5.2 那种 `[system, user, assistant]` 的**单轮**重建，训练的是一段模型从未见过的输入。
而 v1 唯一的护栏 `prompt_sha256` 只覆盖最后一条 user 文本，**结构上不可能**发现整段历史被丢了。

**修法：把「这一轮的上下文长什么样」在唯一知道真相的那一行现算，并让导出按它重建整段对话。**

① `decider` 新增**一个**只读属性 `last_request_shape`（纯 dict，在 `_attempt_decision` 里、
`self._llm.stream(...)` **之前**同步赋值；`decide()` 入口清零，与 `_last_analysis` 的 RC-1 范式
逐字同款）：

```python
{"contextTurns": len(self._context),        # 出站时上下文里已有的消息条数（3 的倍数或含种子）
 "contextFirstRound": 391,                  # 该段上下文的首轮轮次（摘要重置后重新起算）
 "summarySha256": "…" | "",                 # 摘要种子的 sha；无种子为空串
 "finalUserSha256": "…",                    # ★ 真正送出的那条 user 文本（含 retry hint / 追问）
 "attempt": 1, "hintApplied": None, "extraInstruction": False}
```

② 决策行新增两列（§5.1）：

- `context_ref_json` —— 上面那个 dict 原样落库。它是「这一轮的输入到底由哪些轮拼成」的唯一凭据。
- `context_turn_json` —— **本轮进上下文的那一段**的真实内容：
  `{"assistant": <decision_input 原文>, "toolResult": <tool_result_text 原文>}`。
  **必须单独存，不能复用 `output_json` 或 `fill_ref_json`**：`output_json` 是**模型原始**工具入参，
  而进上下文的 `decision_input` 是**门禁之后**的最终 decision（`session.py:2000` 的
  `R-P1-3` 注释与 `commit_round` docstring 都明写如此），两者在任何被门禁改写的轮上都不同；
  `fill_ref_json` 是按采集档**裁剪过**的，而 `tool_result` 是未裁剪的原串。两者体量都很小
  （合计约 0.6–1.5 KB/轮）。

③ 摘要种子按**内容寻址**存进 `ai_trade_prompt_profiles`（该表加一个 `kind` 列，取值
`profile | summary`；`kind=summary` 时 `system_prompt` 列放种子文本、`tools_json` 恒 `"[]"`）。
零新表、零迁移。

④ `sft-jsonl` 的 `messages` 由 `export_sft.py` **按链重建**：

```
[system]
  + ([user(摘要种子)] if summarySha256 else [])
  + 对 run_id 相同、round_no ∈ [contextFirstRound, roundNo) 的每一行，按 round_no 升序展开：
        user      = 该行的 prompt（stored 原文，或按 payload 重建且 shaMatch）
        assistant = tool_use(该行 context_turn_json.assistant)
        tool      = tool_result(该行 context_turn_json.toolResult)
  + [user(本轮 prompt)]
  + [assistant(tool_use = 本轮 output_json)]      ← 训练目标只有这一段
```

⑤ **重建不出来就不导出**（沿用本节的最高纪律）。任一前驱行缺席（已被剪枝 / 采集档为 `minimal` /
`payload_truncated` / 该行 `promptDrift`）、或 `summarySha256` 非空而种子行找不到 ⇒ 该样本排除，
计入 **`skipped.contextIncomplete`**（第 9 个 skipped 桶）。`contextTurns` 与实际重建出的条数
对不上也一律排除——**数得出来的不一致必须当作不一致**。

⑥ 导出参数新增 `contextMode ∈ {full, lastTurnOnly}`，**默认 `full`**。`lastTurnOnly` 是给
「就是要单轮样本」的用户留的显式出口，选它的样本一律带 `meta.contextMode="lastTurnOnly"` 且
`quality.contextTruncated=true`；**默认路径永远不说假话**，这正是 v1 缺的那一半。

⑦ 总闸 `AI_TRADE_JOURNAL_CONTEXT_CHAIN_ENABLED`（§七 第 15 项，默认 `true`）。置 `false` ⇒
两列不落、`decider` 的属性不被读、`contextMode` 只接受 `lastTurnOnly` 且每条样本强制带
`quality.contextTruncated=true`——**关掉它换来的是更少的字节，不是更好看的标签**。

由用例 `test_ai_trade_journal_context_chain.py` 冻结：构造 3 轮（其中第 2 轮被门禁改写、
第 3 轮触发摘要重置），断言第 3 轮样本的 `messages` 逐条等于按 `_context` 语义拼出的那一段、
且第 2 轮的 assistant 段用的是**门禁后**的 decision 而不是 `output_json`；再删掉第 1 行，
断言第 3 轮样本被排除且 `skipped.contextIncomplete == 1`。

#### 2.2.5 脱敏（`journal/redact.py`）

导出的文件会离开这台机器，因此脱敏是**写入时**就做，而不是导出时才做（导出时才做 ⇒ 库里躺着
明文 ⇒ 一次误操作的 `sqlite3 .dump` 就泄了）。

纯函数 `redact(obj)` 递归遍历 dict / list：

- **键名黑名单**（大小写与下划线/驼峰无关归一后匹配）：`apikey` / `secretkey` / `secret` /
  `passphrase` / `password` / `token` / `authorization` / `cookie` / `privatekey` / `credential`
  ⇒ 值一律替换为 `"[REDACTED]"`。
- **值形态黑名单**：命中 `Bearer\s+\S+` / `sk-[A-Za-z0-9]{16,}` 的字符串整串替换。
- **深度与广度上限**：深度 > 12 或节点数 > 20000 ⇒ 截断并置 `_redactTruncated: true`（防御
  病态嵌套把写入协程卡住）。
- **绝不改变形状**：键一律保留（值被替换），否则读侧会把「脱敏了」误读成「本来就没有」。

事实核查：现有 `payload` 构造链路（`_build_decision_payload`）里**本就不含任何凭证**——
账户密钥只存在于 `okx_accounts` 表与 `OKXPrivateClient` 内部。`redact` 因此是**纵深防御**，
不是补一个已知漏洞。用例 R-1/R-2 用构造出的含密载荷证明它确实生效。

#### 2.2.6 事件行的写点清单（全部 best-effort，全部同步入队）

| 位置 | category | code | level |
|---|---|---|---|
| `manager.py` 分组启动成功 | `lifecycle` | `GROUP_STARTED` | info |
| `manager.py` 分组停止 / 结束 | `lifecycle` | `GROUP_STOPPED` / `GROUP_ENDED` | info |
| `manager.py` 暂停 / 继续 | `lifecycle` | `GROUP_PAUSED` / `GROUP_RESUMED` | info |
| `session.py` `_emit_degraded` | `decision` | `DECISION_DEGRADED` | warn |
| `session.py` `_maybe_skip_round` 整轮跳过 | `round` | `ROUND_SKIPPED` | info |
| `session.py` 决策轮异常分支 | `round` | `AI_TRADE_ROUND_FAILED` | error |
| `session.py` 活性看门狗升橙 / 整组停手 | `liveness` | `LIVENESS_STALLED` / `LIVENESS_HALTED` | warn / error |
| `session.py` 行情降级 / 自愈 | `feed` | `FEED_DEGRADED` / `FEED_RECOVERED` | warn / info |
| `session.py` 孤儿持仓侦测 | `reconcile` | `ORPHAN_POSITION` | error |
| `session.py` 兜底自愈 | `backstop` | `BACKSTOP_REPAIRED` / `BACKSTOP_FAILED` | info / error |
| `runtime.py` 启动补记上次关停丢弃 | `journal` | `SHUTDOWN_DROP` | warn |

**`ROUND_SKIPPED` 是评审 RP1-8 补进来的，不是可选项**：`_maybe_skip_round`
（`session.py:1815-1846`）在 `payload` 之前就 `return`，那一轮**没有决策行**——它自增
`rounds_skipped`、往 WS 推一条 `round_skipped`，运行期的实时轨迹上看得见，事后的审计台上却只有
一段没有任何行的空白。而「为什么 14:30 没有决策」正是审计最常被问到的那个问题。`detail` 带
`{"roundNo", "reason": "horizon_locked", "minHoldRemainingSeconds", "wakeKind"}`
（与那条 WS 事件**同一组键、同一个数**，不给第二个真源），列表页把它渲染成一条灰色的占位行，
**明说「本轮被整轮跳过」而不是什么都不显示**。它是 info 级，故 `EVENT_MIN_LEVEL=warn` 时整体不采
——那是用户显式选择的静默，不是设计上的空洞。

**刻意不写的两类**：① 逐笔下单 / 撤单回执——`live_order_audit` 已是它的真源（§N1 纪律）；
② 每轮的门禁判定——它已经在决策行的 `gates_json` 里，再发一条事件就是同一事实的第二份记录。

#### 2.2.7 `gates_json` 的取数与**唯一**层名真源（★ 评审 RP0-3）

v1 从头到尾没说 `gates_json` 从哪儿来，而 §2.6.2 又承诺了「逐条三态 + 读数」。补齐如下。

**层名与层集只有一份真源：`open_funnel.LAYERS`（13 项）与 `open_funnel.LAYER_LABELS`**
（`open_funnel.py:48-63`）。这不是风格偏好——`gate_veto.py` 的开篇纪律 1 逐字写着：
「`vetoedBy` 一律取 `open_funnel.classify_block(degrade)`，**绝不**在本模块另写一张层名表……
两处各写一份必然漂移，而漂移的表现（模型与界面读到一个不存在的层名）**不报任何错**。」

真源的 13 层是：

```
baseRuleMaBand  orphanPosition  horizon      profitCooldown  turnover
exchangeBackstop  drawdownBrake  trendBias   consensus       confidence
edgeGate        reflection      other
```

> v1 §2.6.2 自造的那八个中文名与它逐项对不上（`基础策略铁律` vs `基础策略·MA 通道`、
> `edge 门禁` vs `最小 edge 门禁`），漏了 `orphanPosition` / `profitCooldown` / `confidence` /
> `reflection` 四层，且**多出一个根本不是开仓漏斗层的「利润保护」**——`_maybe_profit_exit` 的
> docstring 第一句就是「本层只会产生 `close`，**永远不会**阻止或改写模型的任何决策」
> （`session.py:3277-3278`）。利润保护属于**离场**通道，它在详情页的落点是第 6 段「执行与结果」，
> 不是门禁链。

**装配点唯一**：`session._journal_gates(...)`，纯同步、纯读，产物形状固定：

```jsonc
{"schema": 1,
 "vetoedBy": "consensus" | null,        // ← classify_block(degrade) 的原值，绝不另算
 "layers": [ {"key": "consensus",       // ← 恒等于 open_funnel.LAYERS 的元素与顺序
              "armed": true,            // 该层本会话是否装配（取各 runtime 已有的只读断面）
              "verdict": "pass" | "veto" | "skip",
              "readings": { }           // best-effort；取不到即整键缺席，绝不补 0
             }, … ]}
```

三条判据各自有真源、互不重算：

| 字段 | 取自 | 说明 |
|---|---|---|
| `key` / 顺序 | `open_funnel.LAYERS` | 逐字沿用，不重排、不增删 |
| 中文名 | `open_funnel.LAYER_LABELS` | **随 wire 下发**给前端；`lib/journal.ts` 里**一个层名字面量都不许有**（门禁 G-6） |
| `vetoedBy` | `open_funnel.classify_block(degrade)` | 与 `gate_veto.build_gate_veto_note` 共用同一个函数 |
| `armed` | 各层 runtime 已有的只读属性（`horizon_rule is not None` / `_trend.verdict` / `_discipline` / `edge_gate` 装配位 …） | **取不到 ⇒ `armed: null`**，前端渲染成「未知」而不是「未装配」——把「问不出来」说成「没装」是本仓库反复禁止的那种谎 |
| `verdict` | `veto` = `key == vetoedBy`；`skip` = `armed` 为假；其余 `pass` | 单一判定式，不在前端重算 |
| `readings` | 各层自己的 wire 断面（已存在，直接引用） | 缺即省略 |

**「未显示 = 通过」是被禁止的**（`trace.py` 的 `base_rule` 相位注释原文：「**含放行轮**——
静默的保守会被用户当成故障」），故 `layers` 恒为 13 条、一条不少，`skip` 与 `pass` 各自有自己的
外观。`AI_TRADE_JOURNAL_CAPTURE_TIER=minimal` 时 `gates_json` **仍然落**（它是结论类字段，
§2.2.3 的表里三档全 ✓）。

`AI_TRADE_JOURNAL_EVENT_MIN_LEVEL`（默认 `info`）为采集下限；置 `warn` ⇒ 上表 info 行整体不写。

### 2.3 轴 B：存储与保留策略

见 §五（DDL）。保留策略在 `journal/retention.py`：

- **触发点两处**：① 应用启动后 30s（错峰，仿 `_backfill_discovery_assets` 的姿态，整段
  try/except、失败只 warning、绝不阻断启动）；② `POST /journal/prune`（管理台手动，支持
  `dryRun`）。**刻意没有常驻定时器**——多一个常驻协程就多一个关停步骤，而日志增长是缓慢的。
- **双闸取交集后按最老优先删**：`created_ts < now - RETENTION_DAYS×86400` 或
  总行数 > `MAX_ROWS`（超出部分按 `id` 升序删）。
- **绝对禁止删除的行**：`group_id` 属于当前 `status ∈ {running, paused}` 的分组。判据取
  `ai_trade_group_states` 与内存 manager 的并集——正在跑的会话的日志被删掉，是审计系统能犯的
  最严重的错。由用例 T-3 冻结。
- **`RETENTION_DAYS=0` 或 `MAX_ROWS=0` ⇒ 该闸不生效**（两个都为 0 = 永不剪枝）。

**存储量级（必须写在这里，因为它决定默认值）**：`standard` 档单条决策行的中位数体量约
**7–12 KB**（`payload_json` 4–8 KB 是主体；§2.2.4bis 的 `context_ref_json` +
`context_turn_json` 再加 **0.6–1.5 KB**）。15m K 线 + 2min 定时唤醒 ⇒ 约 **7 轮 / 根 × 96 根
≈ 670 轮 / 天 / 成员**。5 标的分组 ⇒ **≈ 30 MB / 天**。默认 `RETENTION_DAYS=90` 与
`MAX_ROWS=100000` 的交集把最坏情形钉在 **≈ 1.2 GB**，且管理台健康卡把**当前实占字节**
（`SUM(LENGTH(...))` 一次聚合查询）直接显示出来，不让用户靠猜。
`AI_TRADE_JOURNAL_CONTEXT_CHAIN_ENABLED=false` ⇒ 逐字回到 6–10 KB / 25 MB / 1 GB 这组数
（代价见 §2.2.4bis 第 ⑦ 条：SFT 样本一律降级为单轮并如实标注）。

### 2.4 轴 C：读侧与关联

#### 2.4.1 服务层 `journal/service.py`

只做「查询参数 → 仓储调用 → wire 装配」的编排，**不做任何业务判定**（判定在 `linking.py` 的
纯函数里）。列表端点一律 keyset 分页（`beforeId` 游标），不用 `OFFSET`——十万行上的
`OFFSET 90000` 在 SQLite 上是全扫。

#### 2.4.2 关联：决策轮 ⇄ 成交 ⇄ 订单 ⇄ 往返结果（`journal/linking.py`，纯函数）

```python
def link_decisions_to_fills(decisions: list[dict], trades: list[dict]) -> list[dict]:
```

四步，逐步降级并**如实标注置信度**：

1. **精确锚点**：决策行的 `fill_ref_json.clOrdId`（写点从 `executed` 取）↔ 成交行
   `explain_json.meta.clOrdId`（`db/repositories.py:322` 与 `session.py:712-713` 两个写点）。
   命中 ⇒ `linkConfidence="exact"`。
   > ⚠️ **评审 RP1-6 更正**：v1 称「`executor.py:1388` 证实开仓与平仓两条路径都会带上它」
   > ——① 路径是 `services/quant/app/live_auto/executor.py`（`ai_auto/` 下**没有** `executor.py`，
   > AI 会话经 `manager._build_executor` 复用 `LiveExecutor`）；② 更要紧的是那一行**上面**的
   > 注释原文是「`clOrdId` / `ordId` 为 **best-effort 可选**（开仓取 `_place_order` 回执、平仓取
   > `result.clOrdId`）：**缺失即省略键**，绝不阻塞 trade / explain」
   > （`live_auto/executor.py:1385-1389`，取数经 `_order_ids` 的 `if not isinstance(result, dict)`
   > 兜底，`1506-1511`）。故 `exact` 是**常见**锚点，不是**保证**锚点。
   > **纸面 / 无凭证会话（`creds is None`）在结构上根本没有交易所单号**，也不会有任何
   > `live_order_audit` 行（`manager.py:2290-2295` 明写这条分支的存在与理由）。因此
   > §2.5.2 的 `minLinkConfidence` 默认值**不能是一个恒定的 `exact`**，否则纸面会话导出恒 0 条。
2. **启发式**：`clOrdId` 缺席时，在同一 `run_id` 内按 `(side, |ts − tradeTs| ≤ 90s,
   |qty − tradeQty| ≤ 1e-9)` 唯一匹配。**必须唯一**——命中两条及以上一律判 `none`，
   绝不"挑一个最近的"。命中 ⇒ `linkConfidence="heuristic"`。
3. **未成交轮**：`executed.traded == False` ⇒ `linkConfidence="none"` 且带 `noFillReason`
   （取 `executed.reason`，如 `LIVE_NO_POSITION` / `open_funnel_*` / `gate_veto`）。
   > **但「`executed` 说没成交」不等于「这一轮没有成交」**（评审 RP1-2）：`_post_round` 内的
   > `_maybe_profit_exit` 会在同一轮再平一笔。故本步的判据是
   > `executed.traded is False **且** fill_ref_json.legs 为空`；只要 `legs` 非空，该轮按
   > `legs[-1].clOrdId` 走第 1 步，并在 wire 上带 `fillSource="post_round"`，详情页第 6 段
   > 明说「本笔由利润保护在决策轮末触发，不是模型的动作」。这条一并把 `exitSource` 的归因
   > 与决策行对齐——否则那笔平仓在日志台上会像是凭空出现的。
4. **往返结果**：对已关联到**开仓腿**的决策行，其平仓腿 = 同一 `run_id` 内**按 `id` 升序**的
   下一条 `side == "close"` 成交。
   > ⚠️ **排序键必须是 `trades.id`，绝不能是 `trades.ts`**。这不是风格问题：AI 自动交易的
   > `trades.ts` 由**两套时钟**写入（K 线轮 = bar 开盘秒 / 定时唤醒轮与盯市轮 = 墙钟），按 `ts`
   > 排序会破坏真实的开→平交替。这条已经在 `db/ai_auto_repositories.py:_pair_round_trips` 的
   > docstring 里被完整记录（"实测生产库按 id 配对 161 笔平仓零孤儿"），本函数**逐字沿用同一
   > 条判据**。由用例 L-3 冻结（构造 ts 逆序数据，断言按 ts 排会错、按 id 排是对的）。

   标签从平仓腿取：`pnl`（净额，口径随 run 的 `costBasis`）、`fee`、以及
   `explain_json.meta` 里 `trip_ledger.trip_meta_wire` 已经写好的
   `realizedBps` / `rMultiple` / `roundTripFeeUsd` / `exitSource` / `holdSeconds`。
   **缺键即缺席，绝不补 0**（`trip_meta_wire` 的约束 6 原文：「键一律『算不出即省略』」）。

5. **订单回执**：`live_order_audit` 按 `cl_ord_id` 直查（该表有 `ix_live_audit_account` 索引；
   本轮**不加索引**，按 `cl_ord_id` 的查询走 `account_id + created_ts` 范围后再过滤，量级可控）。

### 2.5 轴 D：导出

#### 2.5.1 三种格式与共同的完整性契约

三种格式**全部流式**（`StreamingResponse`），分页 `EXPORT_PAGE_SIZE`（默认 500）。
一次性拼进内存再序列化会在桌面端把整个进程顶起来——这条已由 `experience/service.py:757`
的 `export_stream` docstring 记录在案，本轮沿用并**加强两处**：

##### 加强一：墙钟预算与分片（★ 评审 RP0-2 —— 这是本节最硬的一条）

**流式解决的是内存，解决不了墙钟**，而这条链路上有一堵 300 秒的墙：
`apps/web/next.config.mjs` 的 `experimental.proxyTimeout: 300000` 对**每一个** `/api/py/*`
请求生效，而 §2.6.4 的 `<a download>` 走的正是同一条 rewrite。本仓已经为同一形状付过三次代价
（`docs/diagnoses/desktop-backtest-proxy-timeout-socket-hang-up/`、
`factor-lab-train-long-range-socket-hang-up/`、`desktop-paper-batch-start-socket-reset/`），
症状一律是传输层的 `socket hang up / ECONNRESET`，而**后端日志全绿**。

v1 唯一的闸 `EXPORT_MAX_ROWS=200000` 是**行数**闸：按 §2.3 的 7–12 KB/行算是 **1.4–2.4 GB**，
早在行数触顶之前就撞墙了。所以本轮加一道**墙钟**闸：

- `AI_TRADE_JOURNAL_EXPORT_TIME_BUDGET_S`（§七 第 16 项，默认 **240**）。生成器每写完一页
  检查一次 `monotonic()`；**超预算即干净收尾**（NDJSON 补 `eof` 行、CSV 补尾注行），
  绝不让代理去撕一条写到一半的流。240 与 300 之间留 60 秒余量，覆盖首字节前的
  `COUNT` / 索引扫描与桌面端的磁盘抖动。
- 三个终止条件取**先到者**：过滤耗尽 / `EXPORT_MAX_ROWS` / 墙钟预算。`eof` 行因此从
  `{"kind":"eof","emitted":N,"complete":bool}` 扩为
  `{"kind":"eof","emitted":N,"complete":bool,"truncatedReason":"none|max_rows|time_budget",
  "nextBeforeId":91733|null}`。
- **`nextBeforeId` 就是分片契约**：它是下一片的 keyset 游标。§2.6.4 的导出中心据此把一次
  「导出全部历史」变成**顺序下载 N 个文件**（`…-part01.ndjson` / `-part02` …），每片一次
  独立请求、各自远在 300 秒之内；预检回执带 `estimatedSeconds` 与 `slices`，界面在点之前
  就说清「将分 3 个文件导出，约 6 分钟」。**绝不**做断点续传（§十一 D1 仍成立）——分片是
  「把一次长请求切成若干次短请求」，不是「把一次失败接着跑」。
- **CSV 没有 `eof` 行可写**，故截断时追加**一行**：首列 `#TRUNCATED`、次列
  `time_budget|max_rows`、第三列 `nextBeforeId`。Excel 会把它显示成一条普通行——那正是目的，
  一个静默截断的 CSV 是本节要消灭的东西。导出中心在下载前就说明这条约定。
- 由用例 `test_ai_trade_journal_export_budget.py` 冻结：把预算设成 0.0 ⇒ 首页之后即收尾且
  `truncatedReason="time_budget"` + `nextBeforeId` 非空；按该游标再取一次能拿到剩余部分且
  两片无重、无漏。
- 由**跨仓源码门禁** `test_ai_trade_journal_proxy_budget.py` 冻结上下界：读
  `apps/web/next.config.mjs` 里的 `proxyTimeout` 字面量，断言
  `EXPORT_TIME_BUDGET_S + 30 <= proxyTimeout / 1000`。这道门禁的价值不在今天——它在于**下一个
  人把 `proxyTimeout` 调小的时候会当场判红**，而不是让用户在半年后收到一个 0 字节的文件。

##### 加强二：完整性信封

> 既有先例只在**首行**放 `counts` 信封。首行信封能证明"我打算给你 N 条"，但**证明不了流是否
> 完整地走到了尾**——被网络截断的 NDJSON 与完整的在语法上没有任何区别。因此本轮的 NDJSON
> **首尾各一行**：首行 `{"kind":"manifest", ...}`、末行
> `{"kind":"eof","emitted":N,"complete":true|false}`。**没有 eof 行的文件一律视为不完整**，
> 导入方（以及我们自己的 `journal_export_check` 用例）据此判定。

| 格式 | media type | 内容 | 用途 |
|---|---|---|---|
| `audit-csv` | `text/csv; charset=utf-8` | UTF-8 BOM + 中文表头 + 一轮一行的扁平字段（22 列，见 §4.7） | 人看 / Excel / 合规留档 |
| `audit-jsonl` | `application/x-ndjson` | manifest → `promptProfile`* → `decision`* → `event`* → `fill`* → `order`* → eof | 全保真归档 / 离线复算 |
| `sft-jsonl` | `application/x-ndjson` | manifest → `sample`* → eof | **LLM 后训练** |

CSV 的转义规则与前端 `lib/export.ts` 的 `escapeField` 逐字同构（RFC4180 风格：含 `,` `"` `\r`
`\n` 时双引号包裹、内部 `"` 转 `""`；行分隔 `\r\n`），BOM 为 `﻿`。

> ⚠️ **评审 RP1-9 更正**：v1 说「由用例 E-2 用同一组样本**同时跑前后端两份实现**并断言逐字节
> 相同」——这个用例写不出来。后端用例跑在 pytest、前端用例跑在 vitest，一个进程里没有另一份
> 实现；而且 `escapeField` 在 `apps/web/lib/export.ts:12` 是**模块私有的**（未 `export`），
> 前端侧连直接调都调不到。照 v1 写下去的结果是这道门禁**根本不存在**，两份实现会安静地漂移。
>
> **改用共享黄金样本**：仓库里提交**一个**夹具目录
> `docs/plans/ai-trade-audit-journal/fixtures/`，内含 `csv_escape_cases.json`（20 组刁钻输入：
> 含逗号 / 含双引号 / 含 `\r\n` / 含前导空格 / 含 BOM 字符本身 / `null` / `undefined` /
> 中文全角逗号 / 超长串 / 空串）与 `csv_escape_golden.csv`（期望输出，含 BOM、`\r\n` 行分隔）。
> 后端用例读这两个文件断言自己的实现逐字节命中；前端用例用同样两个文件、经 `toCsv`
> （`escapeField` 的唯一公开出口）断言同一件事。**两侧断言的是同一个文件**，任何一侧漂移都会
> 在自己的 CI 上判红，而夹具本身的改动会出现在 diff 里、必须两侧同时改。
> 前端正则须写 `\r?\n`——本仓 `core.autocrlf=true`，写死 `\n` 在干净检出上恒红（既有教训）。

#### 2.5.2 后训练样本（`sft-jsonl`）的形状

```jsonc
{
  "kind": "sample",
  "id": "grp-1c8f:BTC-USDT-SWAP:412",       // groupId:instId:roundNo，全库唯一、可回查
  // ★ 评审 RP0-1：`messages` 是**按 §2.2.4bis 的上下文链重建的整段多轮对话**，
  // 不是单轮三段。真实请求是 `messages = self._context + [user_msg]`（decider.py:697-699）。
  "messages": [
    { "role": "system",    "content": "<ai_trade_prompt_profiles.system_prompt 原文>" },
    // ↓ 摘要种子（summarySha256 非空时才有；内容取 kind=summary 的档案行）
    { "role": "user",      "content": "<滚动摘要种子原文>" },
    // ↓ 历史轮，按 round_no 升序，每轮恰好三条（commit_round 的 decider.py:1080-1084 语义）
    { "role": "user",      "content": "<第 409 轮的决策提示词>" },
    { "role": "assistant", "content": [
        { "type": "tool_use", "id": "toolu_journal_409", "name": "submit_trade_decision",
          "input": { /* 第 409 轮 context_turn_json.assistant = **门禁后**的最终 decision */ } } ] },
    { "role": "tool",      "content": [
        { "type": "tool_result", "tool_use_id": "toolu_journal_409",
          "content": "<第 409 轮 context_turn_json.toolResult 原串>" } ] },
    // … 410 / 411 同形 …
    // ↓ 本轮的输入
    { "role": "user",      "content": "<第 412 轮渲染或存档的决策提示词原文>" },
    // ↓ 训练目标：**只有这一段**，且用的是 output_json（模型原始入参），不是门禁后的值
    { "role": "assistant", "content": [
        { "type": "tool_use", "id": "toolu_journal_412",
          "name": "submit_trade_decision",
          "input": { /* output_json 原文：模型自己交出的工具入参 */ } } ] }
  ],
  "tools": [ { "name": "submit_trade_decision", "description": "...", "input_schema": { } } ],
  "meta": {
    "groupId": "grp-1c8f", "runId": 1873, "instId": "BTC-USDT-SWAP", "bar": "15m",
    "roundNo": 412, "ts": 1788000900, "wallTs": 1788000912,
    "trigger": "interval", "captureTier": "standard", "promptSource": "rendered",
    "model": { "provider": "anthropic", "name": "claude-opus-5" },
    "finalAction": "open_long", "modelAction": "open_long",
    // ★ RP0-1：这一轮的输入到底由哪些轮拼成，如实写出来，供离线侧自行核验
    "contextMode": "full", "contextTurns": 10, "contextFirstRound": 409,
    "contextSummarySeed": true, "accountMode": "live"
  },
  "label": {
    "executed": true, "linkConfidence": "exact", "settled": true, "outcome": "win",
    "realizedPnlUsd": 12.34, "realizedBps": 41.2, "rMultiple": 0.83,
    "roundTripFeeUsd": 1.02, "exitSource": "profit_guard", "holdSeconds": 3612
  },
  "quality": {
    "degraded": false, "degradeReason": null, "gateVetoed": false,
    "reflectionVerdict": "confirm", "payloadTruncated": false,
    // ★ RP0-1：`true` 只出现在用户**显式**选了 contextMode=lastTurnOnly、
    // 或总闸 CONTEXT_CHAIN_ENABLED 关闭时。默认路径上它恒为 false——
    // 默认路径绝不产出一条「看起来像整段对话、其实只有最后一轮」的样本。
    "contextTruncated": false
  }
}
```

**八条筛选纪律**（默认值即"能直接拿去训练"的口径，全部可由查询参数放开；末两条是评审
RP0-1 / RP1-6 加进来的）：

| 参数 | 默认 | 含义与理由 |
|---|---|---|
| `excludeGateVetoed` | `true` | `finalAction != modelAction` 的轮次（模型说了但被确定性门禁改写 / 否决，层集见 §2.2.7）**默认排除**。把被否决的输出当正样本，训出来的就是会被否决的行为。放开时该样本的 `quality.gateVetoed=true`，供做偏好对（DPO）时当负例。 |
| `excludeDegraded` | `true` | `degradeReason` 非空（超时 / LLM 错 / 未产出工具调用）的轮次里，assistant 那一段是**系统合成的 hold**，不是模型说的话。 |
| `onlySettled` | `true` | 只导出已产生完整往返结果的轮次——没有标签的样本对后训练没有价值。`false` 时 `label.settled=false` 且盈亏字段整键缺席（**绝不填 0**）。 |
| `includeHold` | `false` | 观望轮占比极高（生产实测某 run 484 轮 100% 观望），全量导出会把样本严重压偏。默认排除，需要平衡负样本时显式开启。 |
| `minLinkConfidence` | **`auto`** | ★ 评审 RP1-6：**不能是恒定的 `exact`**。纸面 / 无凭证会话（`creds is None`）在结构上没有交易所单号（§2.4.2 步骤 1 的更正），恒定 `exact` 会让它导出 0 条而界面上说不出为什么。`auto` 的判定在服务端一处完成：该 scope 内**存在**任一 `cl_ord_id` 非空的决策行 ⇒ 取 `exact`，否则取 `heuristic`；实际生效值随预检与 manifest 一起下发（`resolvedMinLinkConfidence`），前端**照抄不重算**。用户仍可显式指定 `exact` / `heuristic` / `none` 覆盖它。 |
| `contextMode` | `full` | ★ 评审 RP0-1：`full` = 按 §2.2.4bis 重建整段多轮对话，重建不出来即排除；`lastTurnOnly` = 只要最后一轮，样本强制带 `quality.contextTruncated=true`。**默认永远是 `full`**。 |
| `sinceTs` / `untilTs` / `instId` / `bar` / `groupId` | 无 | 常规过滤。 |

manifest 里必须给出**被排除的原因分布**（`skipped: {gateVetoed, degraded, unsettled, hold,
lowConfidence, promptDrift, payloadTruncated, minimalTier, contextIncomplete}`）——一个
"导出了 37 条"却不说"另外 2810 条为什么没导出"的导出器，会让人误以为系统只做了 37 次决策。
第 9 个桶 `contextIncomplete` 是评审 RP0-1 加的：**前驱轮缺席、上下文链重建不出来**的样本
（前驱被剪枝 / 是 `minimal` 档 / `payload_truncated` / 该行 `promptDrift` / 摘要种子档案行找不到 /
`contextTurns` 与实际重建条数对不上）。它必须与别的桶分开计——「模型说错了话」和
「我们没留住模型看过的话」是两件性质完全不同的事，混进一个数字里等于把后者藏起来。

`EXPORT_MAX_ROWS`（默认 200000）与 `EXPORT_TIME_BUDGET_S`（默认 240，见 §2.5.1 加强一）
是**两道并列**的硬上限，先到者生效：流写满即干净收尾，`eof` 行 `complete=false` 并带
`truncatedReason` 与 `nextBeforeId`。管理台在点导出**之前**先调
`POST /journal/export/preview` 拿到精确条数、预估体积**与预估耗时**；预估耗时超过一片的预算时
**不禁用按钮**，而是切成 N 片顺序下载并在按钮上写明片数（§2.6.4）——只有当**单片**都装不下
（理论上不会发生，因为片是按游标切的）才禁用。

### 2.6 轴 E：管理台界面

#### 2.6.1 入口与信息架构

左侧 `NavRail` 新增**第 4 项**「交易日志」（当前 3 项：交易 / 因子实验室 / 策略与因子挖掘）。
`WorkbenchView` 加 `"journal"` 取值；挂载沿用 `page.tsx` 既有的**首激活后常驻**范式
（`hasOpenedJournal` + `hidden={showSettings || activeView !== "journal"}`，
见 `app/page.tsx:713-722`），避免每次切视图重拉数据。

导航项**受 `AI_TRADE_JOURNAL_UI_ENABLED` 门控**：`/api/health` 增一个加法键
`journalEnabled: bool`（`api/market.py:31`，该端点**未声明** `response_model`，故加法键不会被
静默剔除——这条已在它自己的注释里成文），前端读它决定是否渲染该项。总闸关 ⇒ 路由不注册
（404）+ 导航项不渲染——沿用 `truth_sources` 的先例（`main.py:500-503` 注释原文：「总闸关 ⇒
路由不注册 ⇒ 404，而不是注册一个恒回空的端点」）。

**必须配一道水合守卫（★ 评审 RP1-7）**：`apps/web/lib/store.ts` 的 `partialize` **持久化
`activeView`**。于是「用户上次停在交易日志 → 管理员把 `AI_TRADE_JOURNAL_UI_ENABLED` 关掉 →
重启」这条完全现实的路径会让老用户开屏看到**一片什么都没有、也不说为什么的空白工作台**
（NavRail 不渲染该项 ⇒ 没有一个 tab 是选中的；`page.tsx` 不渲染该视图 ⇒ 主区为空）。修法是
一行，落点唯一——读到 `journalEnabled === false` 的那个既有 effect 里：

```ts
// 门控关闭时，把持久化下来的 activeView 拉回默认视图。落点必须是这里（拿到 health 回执的那一处），
// 不是 store 的 migrate——migrate 跑在拿到 health 之前，那时还不知道门控关没关。
if (!journalEnabled && useAppStore.getState().activeView === "journal") setActiveView("trade");
```

由用例 `NavRail.test.tsx` / `page.test.tsx` 各一条冻结（持久化态为 `"journal"` +
`journalEnabled=false` ⇒ 落在 `"trade"` 且主区非空）。这条与仓库既有的
「实盘模式 enable / hydration 守卫」是同一条纪律的第二次落地，不是新发明。

```
┌ 交易日志 ────────────────────────────────────────────────────────────────────────┐
│ [会话选择 ▾ grp-1c8f · BTC/ETH/SOL · 09-01 14:02 → 运行中]   [采集: standard ●]  │
│ [时间范围][标的▾][周期▾][动作▾][仅成交 ☐][仅降级 ☐][门禁否决 ☐][唤醒▾][🔍关键字] │
│                                                            [健康度] [导出 ⤓]     │
├───────────────── 决策轮次 │ 成交与订单 │ 系统事件 ──────────────────────────────┤
│ ┌ 列表（keyset 分页 / ↑↓ 选择 / 行内徽标）──┐║┌ 详情（选中行）────────────────┐│
│ │ 时间   轮次 标的  唤醒  动作  仓位 置信 …  │║│ ▸ 结论                        ││
│ │ 14:32  412  BTC   定时  开多  0.5  0.72 ✓ │║│ ▸ 门禁链（8 道）              ││
│ │ 14:30  411  BTC   定时  观望   —   0.31 ⊘ │║│ ▸ 上下文（分区 + 原始 JSON）  ││
│ │ 14:28  410  ETH   K线   平仓  1.0  0.68 ✓ │║│ ▸ 提示词（原文 / 重建）       ││
│ │ …                                          │║│ ▸ 模型输出（工具入参 + 研判） ││
│ │                             [加载更多]     │║│ ▸ 执行与结果（成交/订单/往返）││
│ └────────────────────────────────────────────┘║│ ▸ 遥测（token / 耗时 / 重试） ││
│                              ↕ PanelResizeHandle└──────────────────────────────┘│
└──────────────────────────────────────────────────────────────────────────────────┘
```

**双栏而不是弹窗**：日志浏览的核心动作是"在列表里连续上下走、右边跟着变"。`Modal` 会锁
`body` 滚动、抢焦点、并在每次切换时销毁重建（`components/ui/index.tsx` 的 Modal 实现），
是列表浏览的反模式。分栏用仓库既有的 `PanelResizeHandle`，不是新发明。

#### 2.6.2 详情面板的七段

1. **结论** — 动作 / 仓位比例 / 置信度 / 委托方式 / 限价 / **最终动作**（与模型动作不同时并排
   显示并标注"被 XX 门禁改写"）/ 降级码中文化 / 反思裁决。
2. **门禁链** — 逐层一行，**层集与层名一律取自 `gates_json.layers`**（服务端按
   `open_funnel.LAYERS` / `LAYER_LABELS` 下发，见 §2.2.7）。今天是 **13 层**：
   `基础策略·MA 通道` / `交易所侧存在未接管持仓` / `持仓周期纪律` / `保护性平仓冷却` /
   `换手预算` / `交易所侧兜底` / `回撤刹车 · 连亏熔断` / `趋势基调 · 逆势门槛` /
   `同向共识门槛` / `置信度门槛` / `最小 edge 门禁` / `下单前二次反思` / `其它`。
   > ⚠️ 上面这串**只是给读者看的当前快照，不是契约**。契约是「层名一个字都不许在前端硬编码」
   > （门禁 G-6）——`gate_veto.py` 的纪律 1 原文：「绝不在本模块另写一张层名表……漂移的表现
   > **不报任何错**」。v1 在这里自造的那八个名字与真源逐项对不上，且把**利润保护**也算成了
   > 一道门禁——它不是：`_maybe_profit_exit` 的 docstring 第一句就是「本层只会产生 `close`，
   > **永远不会**阻止或改写模型的任何决策」（`session.py:3277-3278`）。利润保护是**离场**通道，
   > 它的落点在下面第 6 段。

   每行四态（比 v1 多一态）：**未装配**（灰，写明"本会话未开启"）/ **放行**（显示关键读数）/
   **否决**（显示判据与差多少）/ **未知**（`armed === null`，写明"该层未上报装配状态"）。
   第四态是硬要求：把「问不出来」渲染成「没装」，就是用一个看起来确定的说法盖住一个不确定的
   事实。**绝不用"没显示 = 通过"**——静默的保守是本仓库的前科（`trace.py` 的 `base_rule` 相位
   注释原文：「**含放行轮**——静默的保守会被用户当成故障」），故 13 行**一行不少地渲染**。
3. **上下文** — `payload_json` 按语义分区渲染（行情 / 信号盘 / 多周期 / 情报 / 先验 / 持仓 /
   唤醒 / 纪律），每区可折叠；底部「查看原始 JSON」展开等宽代码块（`overflow-x-auto`）。
   `minimal` 档时整段替换为一句说明 + 「如何开启」指引。
4. **提示词** — `full` 档直接显示原文；`standard` 档显示「按上下文即时重建」按钮，点击调
   `GET /journal/decisions/{id}/prompt`，服务端重建后返回原文 + `shaMatch: true|false`。
   `shaMatch=false` 时**必须**用醒目条说明「提示词渲染代码在此之后已变更，下方为按当前代码重建
   的版本，与当时喂给模型的**不完全相同**」——这句话直接决定用户会不会拿一批假样本去训练。
5. **模型输出** — `output_json` 语法高亮（无三方依赖，简单 `<pre>` + 等宽即可）+ 研判文本
   （`analysis_text`）+ 反思文本，各自可折叠、超长带「展开全部」。
6. **执行与结果** — 成交（价 / 量 / 费 / 净额盈亏 / 持仓后）+ 订单回执（`clOrdId` / `ordId` /
   状态 / 拒单码）+ 往返结果（已实现 bps / R 倍数 / 退出归因 / 持仓时长）+ **关联置信度徽标**
   （`exact` 绿 ✓ / `heuristic` 琥珀 ≈ / `none` 灰 —，形状 + 文案，不靠颜色单独承载语义）。
   未成交轮显示「本轮未下单」+ 原因中文化。
7. **遥测** — provider / model / 输入输出 token / 决策耗时 / 重试次数 / 研判深度档 /
   采集档 / `payloadTruncated`。

#### 2.6.3 表格契约（硬性）

工作台的 9 张表今天全是 `w-full` 无 `nowrap`，10 列的表在 1440 默认窗口下已折行（本项目
`docs/plans/workstation-layout-*` 已记录）。本轮新增的 **3 张表**（决策轮次 / 成交与订单 /
系统事件；v1 写「4 张」是计数错误，见 RP2-6）**一律采用仓库里已经正确的那种写法**
（`Factor` / `Settings` 下 8 张表的既有范式），不是发明：

```tsx
<div className="min-w-0 overflow-x-auto">
  <table className="w-full min-w-[880px] text-xs">
    <thead>…<th className="whitespace-nowrap px-2 py-1.5 text-left …">
    <tbody>…<td className="whitespace-nowrap px-2 py-1.5 tabular …">
```

由源码扫描用例 `journalNoWrap.source.test.ts` 冻结：`components/Journal/` 下每一个 `<table`
的**前一个非空祖先 div** 必须含 `overflow-x-auto`，且 `<table` 的 className 必须含 `min-w-[`。

#### 2.6.4 导出中心

`JournalExportDialog`（`Modal`，`max-w-[720px]`）：

- **格式**（单选，带一句用途说明）：审计 CSV / 审计 JSONL / **后训练 JSONL**。
- **范围**：沿用当前过滤 / 指定会话 / 全部历史（三选一，实时显示"这会导出多少"）。
- **后训练专属**：**八个**筛选纪律（§2.5.2）以开关 + 单选呈现，**默认值即表格里的默认值**，
  每一项都带一句为什么。采集档为 `minimal` 时整块禁用并给出「当前采集档不含模型输入/输出」
  的说明与设置项名。
- **预检**：任一参数变化 → 防抖 400ms → `POST /journal/export/preview` → 显示
  「将导出 N 条（约 X MB）；另有 M 条被排除：门禁否决 a / 降级 b / 未结算 c / 观望 d /
  关联不足 e / 提示词漂移 f」。**超过 `EXPORT_MAX_ROWS` ⇒ 禁用导出按钮 + 给出收窄建议**。
- **下载**：构造 GET URL，用隐藏 `<a download>` 点击触发（服务端已设 `Content-Disposition`）。
  **刻意不用 `fetch` + `Blob`**：几百 MB 的 NDJSON 走 Blob 会把渲染进程的内存顶起来。
- **分片下载（★ 评审 RP0-2）**：预检回执带 `estimatedSeconds` 与 `slices`。`slices > 1` 时
  按钮文案变成「导出（分 3 个文件，约 6 分钟）」，点击后**顺序**触发 N 次 `<a download>`，
  第 k+1 片的 URL 带上第 k 片 `eof.nextBeforeId`（NDJSON）或尾注行的 `nextBeforeId`（CSV）。
  必须顺序、不得并发——并发会让 SQLite 上的几个长扫描互相抢读锁，而这条链上还挂着一个正在
  下单的会话。每片下载后界面上留一行「第 k / N 片 · N 行 · 完整」，**任何一片显示为不完整时
  整个批次判失败并保留已下载的片**（片是自洽的：每片都有自己的 manifest 与 eof）。
- 采集档为 `minimal` 时 SFT 整块禁用（原因见 §2.2.3）；`minLinkConfidence` 的实际生效值
  取自预检回执的 `resolvedMinLinkConfidence`，并在旁边一句话说明它是怎么定出来的
  （「本次范围内未发现交易所单号，已自动放宽到启发式关联」——纸面会话的常态，见 RP1-6）。
- 前端**一个口径数字都不硬编码**：上限、保留天数、采集档全部取自 `/journal/stats` 回执。
  （教训来源：`_compute_ai_stats` 里那句注释——硬编码的"近 50 轮"一旦与常量分叉就开始说谎。）

#### 2.6.5 采集健康度卡

`JournalHealthCard`：入队总数 / 已落库 / **被丢弃**（`dropped`）/ 卸载的 info 事件 /
写库错误 / 当前队列深度 / 最老待写毫秒 / 三张表行数 / 实占字节 / 保留策略读数 / 采集档。

**`dropped > 0` ⇒ 琥珀色横幅**：「有 N 条日志因写入积压被丢弃，本时段的审计记录存在缺口」。
审计系统必须说出自己哪里不完整。

---

## 三、文件 / 模块变更计划

### 3.1 后端 · 新增（13 个文件）

| 文件 | 一句话意图 |
|---|---|
| `services/quant/app/ai_auto/journal/__init__.py` | 包出口：`get_journal_writer` / `build_decision_row` / `build_event_row` 三个符号，别的一律私有 |
| `services/quant/app/ai_auto/journal/records.py` | **纯函数**：把会话侧的活对象裁剪 + 序列化成可入队的 dict；三档采集粒度在此一处生效 |
| `services/quant/app/ai_auto/journal/redact.py` | **纯函数**：递归脱敏（键名黑名单 + 值形态黑名单 + 深广度上限），形状不变 |
| `services/quant/app/ai_auto/journal/writer.py` | `JournalWriter`：有界队列 + 单一 drain task + 两级卸载 + 有界 stop；`note_*` 同步且绝不抛 |
| `services/quant/app/ai_auto/journal/runtime.py` | 进程级单例 `get_journal_writer()` / `reset_journal_writer()`；启动补记上次关停丢弃 |
| `services/quant/app/ai_auto/journal/linking.py` | **纯函数**：决策 ⇄ 成交 ⇄ 订单 ⇄ 往返 四步关联，输出 `linkConfidence` |
| `services/quant/app/ai_auto/journal/service.py` | 读侧编排：列表 / 详情 / 事件 / 成交 / 统计的 wire 装配（不做业务判定） |
| `services/quant/app/ai_auto/journal/export_audit.py` | `audit-csv` 与 `audit-jsonl` 的流式生成器（manifest + eof 契约） |
| `services/quant/app/ai_auto/journal/export_sft.py` | `sft-jsonl`：Messages 样本装配 + **上下文链重建（§2.2.4bis）** + 八条筛选纪律 + 提示词重建与 sha 判据 + 9 个 skipped 桶 |
| `services/quant/app/ai_auto/journal/retention.py` | 保留策略：双闸 + 最老优先 + **绝不删运行中分组的行** |
| `services/quant/app/db/ai_journal_repositories.py` | 三张表的读写与流式分页（仿 `ai_trade_state_repositories.py` 的小文件先例） |
| `services/quant/app/api/ai_trade_journal.py` | **10 个端点（9 只读 + 1 剪枝）**；总闸关时整个路由不注册 |
| `services/quant/app/api/schemas_journal.py` | 本路由的 pydantic 出入参（仿 `api/schemas_intel_*.py` 的先例，不动 2000 行的 `schemas.py`） |

### 3.2 后端 · 修改（11 个文件）

| 文件 | 一句话意图 |
|---|---|
| `services/quant/app/db/models.py` | **+3 张新表**（`AiTradeDecisionLog` / `AiTradeEventLog` / `AiTradePromptProfile`）；**不改任何既有表** |
| `services/quant/app/db/repositories.py` | 尾部 `from .ai_journal_repositories import (...)  # noqa: E402,F401` 再导出（沿用既有 12 处同款） |
| `services/quant/app/config.py` | **+16 项设置** + 4 条启动期校验（§七） |
| `services/quant/app/main.py` | 条件注册路由；lifespan 启动 `journal_writer.start()`；`_shutdown_steps()` 在**第 8 位**（`ai_trade_intel` 之后、`ai_backtest` 之前）插入 `ai_trade_journal` 步（位置理由见 §2.2.2 约束 6）；启动后 30s 错峰剪枝任务 |
| `services/quant/app/api/market.py` | `/health` 加法键 `journalEnabled`（旧前端忽略未知字段，向前兼容） |
| `services/quant/app/ai_auto/session.py` | **+2 个同步方法** `_journal_round` / `_journal_event`；决策轮末尾与异常分支各一处调用；**不动任何判定式** |
| `services/quant/app/ai_auto/decider.py` | **+3 个只读出口**：`last_usage`（今日私有 `_last_usage`，`decider.py:380`）、`prompt_profile()`（系统提示词 / 工具 schema / sha256；`self.system_prompt` 在 `decider.py:306` 已是公开属性，`self._tool` 需一个只读出口）、**`last_request_shape`**（§2.2.4bis 的上下文断面，在 `_attempt_decision` 的 `self._llm.stream(...)` **之前**同步赋值，`decide()` 入口清零） |
| `services/quant/app/ai_auto/manager.py` | 分组生命周期 6 个事件写点（全部 best-effort，同步入队） |
| `.env.example`（仓库根） | **16 项**双向登记（本仓库把它当契约：漏登记会被源码扫描用例判红；`test_ai_intel_wiring.py` 等 6 个既有用例已是先例） |
| `services/quant/tests/test_main_shutdown.py` | ★ 评审 RP1-3：`test_shutdown_steps_are_zero_arg_factories_in_the_legacy_order`（`:160-168`）断言的是 14 个步骤名的**完整有序列表**，加一步就必须在这里补一行——其 docstring 原文明写「这张清单是一份**冻结注册表**」。漏改它，§8.3 的 A4「全量 pytest 同绿」即为假 |
| `services/quant/tests/test_ai_intel_wiring.py` | ★ 评审 RP1-3：`:392` 断言 `names.index("ai_trade_intel") == 6`。本轮把新步插在**第 8 位**故该断言**保持原样即绿**；此处列出只为让实施者显式核对一次，**不得**为图省事去改它 |

### 3.3 前端 · 新增（17 个文件：表内 15 + 表后 2）

| 文件 | 一句话意图 |
|---|---|
| `apps/web/components/Journal/JournalView.tsx` | 视图外壳：工具条 + Tabs + 双栏 + 分栏拖拽；`active` 为 false 时不发请求 |
| `apps/web/components/Journal/JournalToolbar.tsx` | 会话选择 + 8 个过滤控件 + 健康度徽标 + 导出按钮 |
| `apps/web/components/Journal/JournalSessionPicker.tsx` | 会话下拉（运行中置顶 + 历史分页） |
| `apps/web/components/Journal/JournalDecisionTable.tsx` | 决策轮次表（keyset 分页 / ↑↓ 选择 / 行徽标 / 空·载入·错三态） |
| `apps/web/components/Journal/JournalDecisionDetail.tsx` | 详情七段的壳与折叠状态 |
| `apps/web/components/Journal/JournalGateChain.tsx` | 门禁链八行三态（未装配 / 放行 / 否决） |
| `apps/web/components/Journal/JournalContextSections.tsx` | `payload` 分区渲染 + 原始 JSON 折叠 |
| `apps/web/components/Journal/JournalPromptBlock.tsx` | 提示词原文 / 重建 + `shaMatch=false` 的醒目告知 |
| `apps/web/components/Journal/JournalOutcomeBlock.tsx` | 成交 / 订单 / 往返结果 + 关联置信度徽标 |
| `apps/web/components/Journal/JournalFillTable.tsx` | 成交与订单表 |
| `apps/web/components/Journal/JournalEventTable.tsx` | 系统事件表（级别筛选 + 分类徽标） |
| `apps/web/components/Journal/JournalExportDialog.tsx` | 导出中心（格式 / 范围 / 后训练筛选 / 预检 / 下载） |
| `apps/web/components/Journal/JournalHealthCard.tsx` | 采集健康度 + `dropped>0` 琥珀横幅 |
| `apps/web/lib/journalTypes.ts` | wire 类型（与后端 schemas 一一对应） |
| `apps/web/lib/journalApi.ts` | REST 客户端（镜像 `aiTradeHistoryApi.ts` 的自持 `get<T>` 范式 + `httpTimeout`） |

另：`apps/web/lib/journal.ts`（纯派生与中文文案：降级码 / 退出归因 / 唤醒原因 / 关联置信度的
本地化，**绝不在组件里散落 switch**）与 `apps/web/lib/journalExport.ts`（导出 URL 组装 +
体积格式化）。

### 3.4 前端 · 修改（4 个文件）

| 文件 | 一句话意图 |
|---|---|
| `apps/web/lib/store.ts` | `WorkbenchView` 增 `"journal"`；新增 `journalFilters` 持久化切片；`version: 31 → 32` + migrate（老用户补默认，不清空任何既有键） |
| `apps/web/components/Nav/NavRail.tsx` | 第 4 项 + 内联 SVG 图标（20×20 / 1.6 stroke / currentColor，与既有三枚同规格）；受 `journalEnabled` 门控 |
| `apps/web/app/page.tsx` | `hasOpenedJournal` 首激活常驻挂载（逐行镜像 `hasOpenedDiscovery`） |
| `apps/web/lib/httpTimeout.ts` | **只**把 `/ai-trade/journal/export/preview` 进长跑前缀表（走 `LONG_MS`）。★ 评审 RP2-7：`/export` 本身走 `<a download>` 的浏览器导航，**根本不经过 `timeoutFor`**（`httpTimeout.ts:147-156` 只服务于 `fetch` 侧），把它登进去是一行死码；`/export` 的时间保护在服务端（§2.5.1 的墙钟预算） |

### 3.5 测试 · 新增（12 后端 + 7 前端）

见 §八。

---

## 四、接口设计

全部挂在 `/api` 前缀下（前端经 `/api/py/*` 同源代理）。总闸 `AI_TRADE_JOURNAL_UI_ENABLED=false`
⇒ **整个路由不注册**（全部 404）。共 **10 个**：4.1–4.6 与 4.7 的两个是只读（9 个），4.8 是剪枝。

> ⚠️ 每一个经 `/api/py/*` 的请求都受 `apps/web/next.config.mjs` 的
> `experimental.proxyTimeout: 300000` 约束（300 秒）。列表 / 详情 / 统计都是毫秒级，唯一贴近
> 这堵墙的是 `/export`，其保护见 §2.5.1 加强一。

### 4.1 `GET /ai-trade/journal/sessions`

会话选择器数据源。**与既有 `/ai-trade/history` 的区别必须写清楚**：后者刻意**排除当前运行组**
（`ai_auto_repositories` 的既有语义），而日志台必须能看正在跑的那一个。

```
Query: limit=30&cursorTs=<int|null>&includeRunning=true
200:  { "items": [ { "groupId": "grp-1c8f", "status": "running|paused|stopped|done|error",
                    "instIds": ["BTC-USDT-SWAP", ...], "bar": "15m",
                    "startedTs": 1788000000, "endedTs": null,
                    "decisionRows": 412, "eventRows": 37, "fillRows": 18 } ],
        "nextCursorTs": 1787900000 }
```

### 4.2 `GET /ai-trade/journal/decisions`

```
Query: groupId? instId? bar? sinceTs? untilTs? action? trigger?
       executedOnly=false degradedOnly=false gateVetoedOnly=false
       q?（在 reason / analysis 摘要上做 LIKE，长度 ≤64，超出 422）
       beforeId?（keyset 游标）  limit=50（1..200）
200:  { "items": [ DecisionBrief ], "nextBeforeId": 91733, "total": null }
```

`total` 恒为 `null`（**不做 COUNT**：十万行上的 `COUNT(*)` + 过滤会让翻页变慢，而列表页
不需要精确总数；需要精确条数的地方只有导出预检，那里单独算）。

`DecisionBrief`（16 键）：`id / groupId / runId / instId / bar / roundNo / ts / wallTs /
trigger / action / finalAction / sizeFraction / confidence / degradeReason /
reflectionVerdict / executed / linkConfidence`。

### 4.3 `GET /ai-trade/journal/decisions/{id}`

```
200: { "brief": DecisionBrief,
       "gates": [ { "key":"edge_gate", "armed":true, "verdict":"pass|veto|skip",
                    "note":"…", "readings": { } } ],
       "payload": { } | null, "payloadTruncated": false,
       "output": { } | null,
       "analysis": "…" | null, "reflection": "…" | null, "thinking": "…" | null,
       "prompt": { "source":"stored", "text":"…" } | { "source":"none" },
       "telemetry": { "provider":"anthropic", "model":"claude-opus-5",
                      "inputTokens":8123, "outputTokens":412,
                      "latencyMs":6120, "retries":0, "reasoningDepth":"deep",
                      "captureTier":"standard" },
       "outcome": { "linkConfidence":"exact", "fill": {…}|null, "order": {…}|null,
                    "trip": {…}|null, "noFillReason": null } }
404: { "error": { "code":"JOURNAL_DECISION_NOT_FOUND", "message":"…" } }
```

### 4.4 `GET /ai-trade/journal/decisions/{id}/prompt`

`standard` 档的提示词按需重建。

```
200: { "text":"…", "shaMatch": true, "storedSha":"ab12…", "renderedSha":"ab12…" }
409: { "error": { "code":"JOURNAL_PAYLOAD_ABSENT",
                  "message":"该轮以 minimal 档采集，未留存上下文，无法重建提示词。" } }
```

`shaMatch=false` **不是错误**（返回 200），因为重建出来的文本仍有参考价值——但前端必须显著
告知（§2.6.2 第 4 段）。

### 4.5 `GET /ai-trade/journal/events` / `GET /ai-trade/journal/fills`

```
events Query: groupId? instId? level? category? sinceTs? untilTs? beforeId? limit=50
fills  Query: groupId? instId? sinceTs? untilTs? beforeId? limit=50
```

`fills` 的每条带 `decisionRef: { id, roundNo, linkConfidence } | null`——**成交找得回决策**
是审计的另一半（决策找得到成交只是一半）。

### 4.6 `GET /ai-trade/journal/stats`

```
200: { "enabled": true, "captureTier":"standard",
       "queue": { "depth":3, "max":2000, "oldestPendingMs":120 },
       "counters": { "enqueued":91733, "written":91730, "dropped":0,
                     "shedInfoEvents":0, "writeErrors":0, "droppedOnShutdown":0 },
       "tables": { "decisions":91730, "events":1204, "promptProfiles":6,
                   "approxBytes":742318112 },
       "retention": { "days":90, "maxRows":100000, "lastPrunedTs":1787990000,
                      "lastPrunedRows":0 },
       "limits": { "textMaxChars":4000, "payloadMaxChars":24000,
                   "exportMaxRows":200000, "exportPageSize":500 } }
```

前端的每一个口径数字都来自这里，**一个都不硬编码**。

### 4.7 `POST /ai-trade/journal/export/preview` 与 `GET /ai-trade/journal/export`

```
POST /export/preview
Body: { "format":"sft-jsonl", "scope": { …与 4.2 同构的过滤… },
        "sft": { "excludeGateVetoed":true, "excludeDegraded":true, "onlySettled":true,
                 "includeHold":false, "minLinkConfidence":"exact" } }
200:  { "rows": 372, "approxBytes": 18344112, "overLimit": false,
        // ★ RP0-2：分片契约。estimatedSeconds 由「行数 × 单行装配耗时的滚动中位数」估出，
        // slices = ceil(estimatedSeconds / EXPORT_TIME_BUDGET_S)，两者都随回执下发、前端不重算。
        "estimatedSeconds": 512, "slices": 3, "timeBudgetS": 240,
        // ★ RP1-6：`minLinkConfidence=auto` 的实际解出值，前端照抄不重算
        "resolvedMinLinkConfidence": "heuristic",
        "resolvedReason": "no_client_order_id_in_scope",
        "skipped": { "gateVetoed":118, "degraded":41, "unsettled":93, "hold":2810,
                     "lowConfidence":6, "promptDrift":0, "payloadTruncated":0,
                     "minimalTier":0, "contextIncomplete":12 } }

GET /export?format=audit-csv|audit-jsonl|sft-jsonl&<同上过滤扁平化>&beforeId=<上一片的 nextBeforeId>
200:  StreamingResponse
      Content-Disposition: attachment; filename="aragon-ai-trade-journal-<格式>-<YYYYMMDD-HHMM>[-partNN].csv|ndjson"
      Content-Type: text/csv; charset=utf-8 | application/x-ndjson

      NDJSON 末行（★ RP0-2）：
      {"kind":"eof","emitted":500,"complete":false,
       "truncatedReason":"time_budget","nextBeforeId":91733}
      CSV 截断时的尾注行：  #TRUNCATED,time_budget,91733
```

`audit-csv` 的 22 列（中文表头，UTF-8 BOM）：
时间(UTC) / 时间(本地) / 分组 / 运行ID / 标的 / 周期 / 轮次 / 唤醒原因 / 模型动作 / 最终动作 /
仓位比例 / 置信度 / 委托方式 / 限价 / 降级原因 / 反思裁决 / 是否成交 / 成交价 / 成交量 /
手续费 / 已实现盈亏 / 决策理由。

### 4.8 `POST /ai-trade/journal/prune`

```
Body: { "dryRun": true }
200:  { "wouldDelete": { "decisions": 12043, "events": 88 }, "deleted": null,
        "protectedGroups": ["grp-1c8f"] }
```

`dryRun=false` 时 `deleted` 填实际删除数、`wouldDelete` 为 `null`。**必须落写路径超时档**
（`httpTimeout.ts` 的 `WRITE_PREFIXES`）——它是删除操作，超时要说「结果未知，请刷新核对」
而不是「失败」（这条纪律取自 `api/experience.py` 的 `prune` 端点 docstring）。

---

## 五、数据模型

### 5.1 `ai_trade_decision_logs`（一轮一行）

```python
class AiTradeDecisionLog(Base):
    """AI 自动交易的一轮决策留痕（append-only；本表是本特性的唯一真源）。

    为什么是新表而不是给 `strategy_runs.metrics_json` 塞更多东西：`metrics_json` 只在会话
    **终结时**写一次（`session.py:4001`），进程崩了就什么都没有；而且它是一个 TEXT 列，
    没法按轮次分页、按标的过滤、按时间剪枝。

    为什么新表 = 零迁移：`create_all` **会**建缺失的表、**不会**给既有表补列
    （见 `db/session.py:95-99` 的五个 `_ensure_*_columns` 补丁）。
    """
    __tablename__ = "ai_trade_decision_logs"
    __table_args__ = (
        Index("ix_ai_journal_dec_group_id", "group_id", "id"),
        # ★ RP0-1：上下文链重建按 (run_id, round_no) 范围取前驱行，正好走这条既有索引，
        # 不需要为它再加一条。
        Index("ix_ai_journal_dec_run_round", "run_id", "round_no"),
        Index("ix_ai_journal_dec_ts", "ts"),
        Index("ix_ai_journal_dec_clordid", "cl_ord_id"),
    )

    id:              Mapped[int]  = mapped_column(Integer, primary_key=True, autoincrement=True)
    schema_version:  Mapped[int]  = mapped_column(Integer, default=1)      # wire/格式演进锚点
    group_id:        Mapped[str]  = mapped_column(String(48), index=True)
    run_id:          Mapped[int]  = mapped_column(Integer, index=True)     # 无外键（本库不开 FK）
    inst_id:         Mapped[str]  = mapped_column(String(32))
    bar:             Mapped[str]  = mapped_column(String(8), default="")
    round_no:        Mapped[int]  = mapped_column(Integer, default=0)
    ts:              Mapped[int]  = mapped_column(Integer, default=0)      # 判定时刻（秒 UTC，可能是 bar 开盘秒）
    wall_ts:         Mapped[int]  = mapped_column(Integer, default=0)      # 墙钟秒（双时钟必须两个都留）
    trigger:         Mapped[str]  = mapped_column(String(16), default="")  # candle|interval|monitor|…
    # ---- 结论 ----
    action:          Mapped[str]  = mapped_column(String(20), default="")  # 模型说的
    final_action:    Mapped[str]  = mapped_column(String(20), default="")  # 门禁后真正执行的
    size_fraction:   Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence:      Mapped[float | None] = mapped_column(Float, nullable=True)
    ord_type:        Mapped[str | None]   = mapped_column(String(8), nullable=True)
    limit_px:        Mapped[float | None] = mapped_column(Float, nullable=True)
    degrade_reason:  Mapped[str | None]   = mapped_column(String(48), nullable=True)
    reflection_verdict: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reason:          Mapped[str]  = mapped_column(String(512), default="")  # 不再 128 截断
    # ---- 执行 ----
    executed:        Mapped[bool] = mapped_column(Boolean, default=False)
    cl_ord_id:       Mapped[str]  = mapped_column(String(32), default="")
    ord_id:          Mapped[str]  = mapped_column(String(64), default="")
    fill_ref_json:   Mapped[str | None] = mapped_column(Text, nullable=True)  # 价/量/费/盈亏/持仓后
    # ---- 上下文与模型输出（受采集档控制，可为 NULL）----
    profile_sha256:  Mapped[str]  = mapped_column(String(64), default="")
    # ★ 评审 RP0-1：取自 `decider.last_request_shape.finalUserSha256`——**真正送出去的**那条 user
    # 文本的 sha（含重试后缀 / 追问后缀），**不是**写点按 payload 重算的那一条。重算永远看不见
    # `decider.py:604-608` 追加的后缀，于是「存档 sha == 重建 sha」与「样本是假的」会同时成立。
    prompt_sha256:   Mapped[str]  = mapped_column(String(64), default="")
    payload_json:    Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    prompt_text:     Mapped[str | None] = mapped_column(Text, nullable=True)   # 仅 full 档（含后缀原文）
    output_json:     Mapped[str | None] = mapped_column(Text, nullable=True)   # 模型**原始**工具入参
    # ---- ★ 评审 RP0-1：上下文链（§2.2.4bis）。总闸 CONTEXT_CHAIN_ENABLED 关 ⇒ 两列恒 NULL ----
    # `context_ref_json`：{"contextTurns","contextFirstRound","summarySha256","finalUserSha256",
    #                      "attempt","hintApplied","extraInstruction"}
    context_ref_json:  Mapped[str | None] = mapped_column(Text, nullable=True)
    # `context_turn_json`：{"assistant": <门禁**后**的 decision_input>, "toolResult": <原串>}
    # 必须与 `output_json` 分开存：进上下文的是门禁后的最终 decision（`session.py:2000` 的
    # R-P1-3 与 `commit_round` docstring 都明写如此），而训练目标要的是模型原始入参。
    # 也不能复用 `fill_ref_json`：那一列按采集档裁剪过，而 tool_result 是未裁剪的原串。
    context_turn_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    analysis_text:   Mapped[str | None] = mapped_column(Text, nullable=True)
    reflection_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    thinking_text:   Mapped[str | None] = mapped_column(Text, nullable=True)   # 仅 full 档
    gates_json:      Mapped[str | None] = mapped_column(Text, nullable=True)
    telemetry_json:  Mapped[str | None] = mapped_column(Text, nullable=True)
    capture_tier:    Mapped[str]  = mapped_column(String(10), default="standard")
    created_ts:      Mapped[int]  = mapped_column(Integer, default=0)
```

**没有 `outcome_json` 列，这是有意的。** 往返结果的真源是 `trades`（平仓腿的 `pnl` 与
`explain_json.meta` 里 `trip_ledger` 已经写好的 `realizedBps` / `rMultiple` / `exitSource`）。
再存一份副本，就会产生本仓库反复付过代价的那种缺陷形状——**两份真值，其中一份悄悄过期**。
结果一律由 `linking.py` 在读时关联（§2.4.2），并如实给出 `linkConfidence`。

### 5.2 `ai_trade_event_logs`

```python
class AiTradeEventLog(Base):
    __tablename__ = "ai_trade_event_logs"
    __table_args__ = (Index("ix_ai_journal_evt_group_id", "group_id", "id"),
                      Index("ix_ai_journal_evt_ts", "ts"))
    id:         Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id:   Mapped[str]  = mapped_column(String(48), default="", index=True)
    run_id:     Mapped[int | None] = mapped_column(Integer, nullable=True)
    inst_id:    Mapped[str]  = mapped_column(String(32), default="")
    ts:         Mapped[int]  = mapped_column(Integer, default=0)
    wall_ts:    Mapped[int]  = mapped_column(Integer, default=0)
    level:      Mapped[str]  = mapped_column(String(8), default="info")   # info|warn|error
    category:   Mapped[str]  = mapped_column(String(16), default="")      # lifecycle|decision|round|…
    code:       Mapped[str]  = mapped_column(String(48), default="")      # 稳定机器码（改名 = 破坏性变更）
    message:    Mapped[str]  = mapped_column(String(512), default="")
    detail_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_ts: Mapped[int]  = mapped_column(Integer, default=0)
```

`code` 是**稳定契约**：它会进导出文件、会被离线脚本按值分支。改名等同破坏性变更，必须在
本文件的「事件码表」（§2.2.6）里同步登记。

### 5.3 `ai_trade_prompt_profiles`（内容寻址、写一次）

```python
class AiTradePromptProfile(Base):
    """内容寻址的大文本档案（写一次、按 sha 复用）。

    `kind` 是评审 RP0-1 加的：滚动摘要种子（`decider.summarize()` 的产物）也必须留档，
    否则上下文链的第一条消息重建不出来。**刻意不为它再建一张表**——两者的形状完全一致
    （一段大文本 + 一个内容 sha），而多一张表就多一处剪枝、多一处统计、多一处关停考量。
    """
    __tablename__ = "ai_trade_prompt_profiles"
    sha256:        Mapped[str] = mapped_column(String(64), primary_key=True)
    kind:          Mapped[str] = mapped_column(String(12), default="profile")  # profile | summary
    system_prompt: Mapped[str] = mapped_column(Text, default="")   # kind=summary 时放摘要种子原文
    tools_json:    Mapped[str] = mapped_column(Text, default="[]") # kind=summary 时恒 "[]"

    provider:      Mapped[str] = mapped_column(String(24), default="")
    model:         Mapped[str] = mapped_column(String(64), default="")
    first_group_id: Mapped[str] = mapped_column(String(48), default="")
    created_ts:    Mapped[int] = mapped_column(Integer, default=0)
```

### 5.4 入队记录的内存形状（`records.py` 的产物）

```python
{"kind": "decision",
 "cols": { ...与 5.1 列名一一对应的、已经全部序列化好的标量与字符串... },
 "profile": {"sha256": "...", "systemPrompt": "...", "toolsJson": "[...]",
             "provider": "...", "model": "..."} | None}   # 首次见到该 sha 时才带
```

`profile` 只在**本进程尚未写过该 sha** 时带上（writer 内持一个有界 `set`，上限 64，
超出即清空重来——写重复了也只是一次 `INSERT OR IGNORE`）。

---

## 六、前端设计补充（交互与可访问性）

1. **键盘可达**：列表行 `<tr tabIndex={0} aria-selected>`，`↑/↓` 移动选中并滚动到可视区，
   `Enter` 把焦点送进详情栏，`Esc` 送回列表。分页「加载更多」是真按钮（不是无限滚动——
   审计场景需要可预期的位置）。
2. **三态齐全**：每张表与详情栏都必须有 载入（`Skeleton`）/ 空 / 错误（含重试按钮与错误码）
   三态。空态要说清**为什么空**：总闸关 / 采集档为 minimal / 该会话尚无决策 / 过滤太窄。
3. **语义不靠颜色单独承载**：关联置信度、门禁三态、事件级别一律「形状/字形 + 文案 + 颜色」
   三者齐全（仓库既有纪律）。
4. **数字一律 `tabular`**；时间双列（UTC + 本地），因为审计口径必须无歧义。
5. **零横向溢出**：§2.6.3 的表格契约 + 详情栏所有等宽块 `overflow-x-auto`；
   1280×720 与 1440×900 两档由用例 U-6 冻结。
6. **中文文案集中在 `lib/journal.ts`**：降级码 / 退出归因 / 唤醒原因 / 事件 code
   的中文化表各一张，组件里不写 switch。
   > ⚠️ **门禁层名是唯一的例外，且方向相反**（评审 RP0-3）：`lib/journal.ts` 里**不许有**任何
   > 门禁层名的中文化表——层名一律随 `gates_json.layers[].label` 从服务端下发
   > （真源 `open_funnel.LAYER_LABELS`）。在前端补一张表就是本仓明令禁止的第二份真源，
   > 而它漂移时**不报任何错**。由源码扫描用例 `journalNoLayerLabels.source.test.ts` 冻结
   > （`components/Journal/` 与 `lib/journal*.ts` 全文不得出现 `LAYER_LABELS` 里的任一中文串）。
   > ⚠️ 后端下发的文案里**不得含 `**` 粗体标记**——JSX 里会显示成字面星号。本仓库已为此复发
   > 过 4 次（`docs/plans/ai-trade-loss-liveness-hardening/`），本轮所有新增后端文案由用例
   > B-9 用正则扫描冻结。

---

## 七、设置项（16 项）与 `.env.example` 双向登记

前缀 `AI_TRADE_` 是**硬约束**：`desktop/src/lib/config.js` 的后端 env 透传白名单是**前缀式**
的，裸名在打包桌面端里会被静默丢弃——那把开关在唯一的产品形态里按不下去
（理由原文见 `config.py:1270-1275`）。

| # | 键 | 默认 | 回退语义 |
|---|---|---|---|
| 1 | `AI_TRADE_JOURNAL_ENABLED` | `true` | `false` ⇒ writer 不装配、`note_*` 直接 return、三张表零行；决策轮**逐字节**回退合入前 |
| 2 | `AI_TRADE_JOURNAL_CAPTURE_TIER` | `standard` | `minimal` ⇒ 不落上下文与模型输出（SFT 导出被禁用并说明） |
| 3 | `AI_TRADE_JOURNAL_QUEUE_MAX` | `2000` | 队列上界；越小越早开始丢新条 |
| 4 | `AI_TRADE_JOURNAL_BATCH_SIZE` | `32` | 单次提交条数 |
| 5 | `AI_TRADE_JOURNAL_FLUSH_INTERVAL_S` | `1.0` | 空闲冲刷间隔 |
| 6 | `AI_TRADE_JOURNAL_TEXT_MAX_CHARS` | `4000` | 单段文本上限；`full` 档实际取 `max(本值, 20000)` |
| 7 | `AI_TRADE_JOURNAL_PAYLOAD_MAX_CHARS` | `24000` | 上下文上限；超出截断并置 `payload_truncated` |
| 8 | `AI_TRADE_JOURNAL_EVENT_MIN_LEVEL` | `info` | `warn` ⇒ info 级事件整体不采 |
| 9 | `AI_TRADE_JOURNAL_RETENTION_DAYS` | `90` | `0` ⇒ 该闸不生效 |
| 10 | `AI_TRADE_JOURNAL_MAX_ROWS` | `100000` | `0` ⇒ 该闸不生效 |
| 11 | `AI_TRADE_JOURNAL_EXPORT_PAGE_SIZE` | `500` | 流式导出的分页大小 |
| 12 | `AI_TRADE_JOURNAL_EXPORT_MAX_ROWS` | `200000` | 硬上限；触顶 ⇒ `eof.complete=false` |
| 13 | `AI_TRADE_JOURNAL_DRAIN_TIMEOUT_S` | `5.0` | 关停排空死线 |
| 14 | `AI_TRADE_JOURNAL_UI_ENABLED` | `true` | `false` ⇒ 路由不注册（404）+ 导航项不渲染 + **持久化的 `activeView="journal"` 被水合守卫拉回 `trade`**（§2.6.1 / RP1-7） |
| 15 | `AI_TRADE_JOURNAL_CONTEXT_CHAIN_ENABLED` | `true` | ★ 评审 RP0-1。`false` ⇒ `context_ref_json` / `context_turn_json` 两列恒 NULL、`decider.last_request_shape` 不被读、摘要种子不留档；SFT 导出**只接受 `contextMode=lastTurnOnly`** 且每条样本强制 `quality.contextTruncated=true`。关掉它换来的是每轮少 0.6–1.5 KB，**不是**更好看的标签 |
| 16 | `AI_TRADE_JOURNAL_EXPORT_TIME_BUDGET_S` | `240` | ★ 评审 RP0-2。单片导出的墙钟死线；触顶即干净收尾并给 `nextBeforeId`。默认 240 是从 `apps/web/next.config.mjs` 的 `experimental.proxyTimeout: 300000` 倒推的（留 60s 余量）。置 `0` ⇒ 该闸不生效（**只在无代理的直连排障场景下这么用**，桌面端产品形态下等于把 RP0-2 那堵墙放回来） |

**四条启动期校验**（加进 `config.py` 既有的 `model_post_init` 校验段，写错即启动失败而不是
运行期才发现）：

1. `AI_TRADE_JOURNAL_DRAIN_TIMEOUT_S < shutdown_step_timeout_s`（默认 20.0）。倒挂时外层
   先 cancel、内层清理永不执行，写队列会活着穿过 `dispose_engine`——这条坑本仓库已经在
   `ai_trade_experience_drain_timeout_s` 上踩过一次并成文（`main.py:132-135`）。
2. `AI_TRADE_JOURNAL_CAPTURE_TIER ∈ {minimal, standard, full}`。
3. `AI_TRADE_JOURNAL_BATCH_SIZE ≤ AI_TRADE_JOURNAL_QUEUE_MAX`。
4. ★ 评审 RP0-2：`AI_TRADE_JOURNAL_EXPORT_TIME_BUDGET_S == 0 或 ≥ 10.0`。**刻意不在这里与
   `proxyTimeout` 比**——那是前端仓的一个 JS 字面量，后端的 `Settings` 读不到它，写一个读不到
   的判据就是一条恒真的假门禁。两者的上下界关系由**源码扫描用例**
   `test_ai_trade_journal_proxy_budget.py` 守（读 `apps/web/next.config.mjs` 断言
   `budget + 30 <= proxyTimeout / 1000`），这样下一个人调小 `proxyTimeout` 时会当场判红。

16 项全部在仓库根 `.env.example` 逐条登记（键 + 一句为什么 + 回退语义），由源码扫描用例
S-1 双向校验（`config.py` 有而 `.env.example` 没有 ⇒ 红；反之亦然）。该范式在本仓已有 6 个
既有用例文件作先例（`test_ai_intel_wiring.py` / `test_ai_trade_fill_truth_wiring.py` 等）。

---

## 八、测试与验收标准

### 8.1 后端用例（16 个新文件 + 1 个既有文件改一行，`services/quant/tests/`）

| 文件 | 关键断言 |
|---|---|
| `test_ai_trade_journal_writer.py` | 入队不抛（构造会抛的记录）；队满丢**新**条且 `dropped` 递增；水位 >80% 时 info 事件先被卸；批量提交；DB 错不冒泡且 `write_errors` 递增；`stop()` 有界 drain 后 **await** 了 task（断言 task.done()） |
| `test_ai_trade_journal_records.py` | 三档裁剪逐字段；文本与 payload 上限；`payload_truncated` 置位；**入队后修改源 dict 不改变落库内容**（冻结性） |
| `test_ai_trade_journal_redaction.py` | 嵌套 dict / list 里的 `apiKey` / `secret` / `passphrase` / `Bearer xxx` / `sk-…` 全部替换；**键仍在**；深广度上限触发时置 `_redactTruncated` |
| `test_ai_trade_journal_session_hook.py` | 跑一轮 ⇒ 恰好 1 条决策行；总闸关 ⇒ 0 行且 writer 未被构造；轮内抛异常 ⇒ 1 条 error 事件行 + 0 条决策行；**A-1 的两条 AST 判据**：① `_journal_round` / `_journal_event` 体内 `ast.Await` 计数为 0；② ★ RP1-5：两个方法体的顶层语句**恰好是一个 `ast.Try`** 且 handler 覆盖 `Exception`（只查 ① 抓不到 `json.dumps` 抛在热路径上）；**A-3 ★ RP1-5**：喂一个不可序列化对象 ⇒ 决策轮仍成功、`serialize_errors == 1`、**不产生 `AI_TRADE_ROUND_FAILED`**；**A-4 ★ RP1-2**：一轮里开仓成交 + `_post_round` 内保护平仓 ⇒ `fill_ref_json.legs` 恰好两条且第二条 `source="post_round"`；**A-5 ★ RP1-8**：`_maybe_skip_round` 命中 ⇒ 0 条决策行 + 恰好 1 条 `ROUND_SKIPPED` 事件行，且 `EVENT_MIN_LEVEL=warn` 时 0 条 |
| `test_ai_trade_journal_linking.py` | `clOrdId` 精确关联；缺 `clOrdId` 的启发式；**两条候选 ⇒ 判 `none` 而不是挑一条**；**构造 ts 逆序数据，证明按 ts 配对会错、按 id 配对是对的** |
| `test_ai_trade_journal_api.py` | 9 个端点的分页 / 过滤 / 404 / 422（`q` 超长）；`total` 恒 `null`；总闸关 ⇒ 全部 404 |
| `test_ai_trade_journal_prompt_rebuild.py` | sha 一致 ⇒ `shaMatch=true`；提示词函数被 monkeypatch 后 ⇒ `shaMatch=false` 且仍返回 200；`minimal` 档 ⇒ 409 |
| `test_ai_trade_journal_export_audit.py` | CSV 有 BOM、22 列、行分隔 `\r\n`；★ RP1-9：读共享黄金夹具 `fixtures/csv_escape_cases.json` + `csv_escape_golden.csv` 断言**逐字节命中**（前端用例读同两个文件，见 §8.2）；NDJSON 首行 manifest、末行 eof |
| `test_ai_trade_journal_export_budget.py` | ★ 新增（RP0-2）：`EXPORT_TIME_BUDGET_S=0.0` ⇒ 首页后即收尾、`eof.complete=false`、`truncatedReason="time_budget"`、`nextBeforeId` 非空；按该游标再取一次 ⇒ 两片**无重、无漏、并集 = 全集**；`EXPORT_MAX_ROWS` 触顶 ⇒ `truncatedReason="max_rows"`；CSV 截断 ⇒ 末行是 `#TRUNCATED,<reason>,<nextBeforeId>` |
| `test_ai_trade_journal_proxy_budget.py` | ★ 新增（RP0-2）：**跨仓源码门禁**——读 `apps/web/next.config.mjs` 里的 `proxyTimeout` 字面量，断言 `EXPORT_TIME_BUDGET_S + 30 <= proxyTimeout / 1000`。文件缺失 / 解析不出数字 ⇒ **判红而不是跳过**（一道会自己变成空转的门禁等于没有） |
| `test_ai_trade_journal_context_chain.py` | ★ 新增（RP0-1）：构造 3 轮（第 2 轮被门禁改写、第 3 轮触发摘要重置）⇒ 第 3 轮样本的 `messages` 逐条等于按 `_context` 语义拼出的那一段；第 2 轮的 assistant 段用的是**门禁后**的 decision（`context_turn_json.assistant`）而**不是** `output_json`；删掉第 1 行 ⇒ 该样本被排除且 `skipped.contextIncomplete == 1`；`contextTurns` 与实际重建条数不符 ⇒ 同样排除；总闸关 ⇒ 两列恒 NULL 且 `contextMode` 只接受 `lastTurnOnly` |
| `test_ai_trade_journal_gates.py` | ★ 新增（RP0-3）：`gates_json.layers` 的 key 与顺序**恒等于** `open_funnel.LAYERS`（13 条，一条不少）；`vetoedBy` 恒等于 `open_funnel.classify_block(degrade)`；`armed` 取不到时是 `null` 而**不是** `false`；`minimal` 档下 `gates_json` **仍然落**；**AST 门禁**：`journal/` 与 `api/ai_trade_journal.py` 全文不得出现 `LAYER_LABELS` 里的任一中文串（层名只许转发） |
| `test_ai_trade_journal_export_sft.py` | messages 以 system 开头、以 `tool_use` 的 assistant 结尾，中间的历史轮**三条一组**；**八条**筛选纪律各自生效；`skipped` 的 **9 个桶**加总 = 总候选 − 导出条数；提示词漂移的样本被排除并计数；`minimal` 档 ⇒ 0 条 + `skipped.minimalTier`；★ RP1-6：scope 内无任何 `cl_ord_id` ⇒ `minLinkConfidence=auto` 解出 `heuristic` 且回执带 `resolvedReason`，**导出条数 > 0**（这条正是纸面会话恒 0 条的回归护栏） |
| `test_ai_trade_journal_retention.py` | 天数闸 / 行数闸各自生效；**运行中与暂停分组的行绝不被删**；`dryRun` 不删任何东西 |
| `test_ai_trade_journal_settings.py` | **16 项**在 `config.py` ⇄ `.env.example` 双向登记；**四条**启动期校验各自能把非法配置挡在启动期 |
| `test_main_shutdown.py`（**改既有文件**） | ★ RP1-3：`test_shutdown_steps_are_zero_arg_factories_in_the_legacy_order` 的冻结列表补 `"ai_trade_journal"` 到第 8 位；同文件新增一条断言 `names.index("ai_trade_journal") == 7` 且 `< names.index("db_engine")`、`> names.index("ai_trade")` |
| `test_ai_trade_journal_isolation.py` | **AST 门禁**：`ai_auto/backtest.py` 与 `backtest_manager.py` 全文 0 次 `journal`（G-4）；`journal/` 下任何模块都不出现在 `session.py`/`decider.py`/`manager.py` 的**判定表达式**里（G-3）；`journal/records.py` 与 `redact.py` 无 `raise` 语句（G-5）；★ RP0-3：层名只许来自 `open_funnel`（G-6，见 `test_ai_trade_journal_gates.py`） |

### 8.2 前端用例（9 个，`apps/web/`，`NODE_ENV=test` 下 vitest；须在 `apps/web` 目录内跑）

| 文件 | 关键断言 |
|---|---|
| `lib/journal.test.ts` | 中文化表全覆盖（未知码回退到原码而不是空串）；体积/时间格式化 |
| `lib/journalApi.test.ts` | 过滤 → querystring 组装（`null`/`undefined` 不进串）；长跑前缀命中 `LONG_MS` |
| `components/Journal/JournalDecisionTable.test.tsx` | 载入/空/错三态；`↑↓` 改选中且 `aria-selected` 跟随；空态文案随原因变化 |
| `components/Journal/JournalExportDialog.test.tsx` | `minimal` 档 ⇒ SFT 选项 disabled + 原因；`skipped` **9 项**逐项渲染；★ RP0-2：`slices=3` ⇒ 按钮写明片数与预估耗时、点击后**顺序**触发 3 次下载且第 2 片带上第 1 片的 `nextBeforeId`（**不并发**）；★ RP1-6：回执 `resolvedMinLinkConfidence="heuristic"` ⇒ 界面照抄并给出 `resolvedReason` 的中文说明，**前端不重算**；★ RP0-1：`contextMode` 单选默认停在 `full`，切到 `lastTurnOnly` 时出现「样本将只含最后一轮」的醒目提示 |
| `components/Journal/JournalHealthCard.test.tsx` | `dropped>0` ⇒ 琥珀横幅出现且文案含条数；口径数字全部来自 props（无硬编码） |
| `components/Journal/journalNoWrap.source.test.ts` | 源码扫描：**3 张表**每个 `<table` 的容器含 `overflow-x-auto` 且表自身含 `min-w-[`（**正则须写 `\r?\n`**——本仓 `core.autocrlf=true`，写死 `\n` 在干净检出上恒红） |
| `lib/exportCsvGolden.test.ts` | ★ 新增（RP1-9）：经 `toCsv`（`escapeField` 的唯一公开出口，它本身**未导出**）跑 `fixtures/csv_escape_cases.json`，断言输出与 `fixtures/csv_escape_golden.csv` **逐字节相同**。与后端 `test_ai_trade_journal_export_audit.py` 读**同两个文件** ⇒ 任一侧漂移都在自己的 CI 上判红，且夹具的任何改动都会出现在 diff 里 |
| `components/Nav/NavRail.test.tsx`（增补） | 第 4 项存在、`aria-current` 正确；`journalEnabled=false` ⇒ 不渲染该项。**注意该文件既有用例名是「renders the three primary nav items」（`:15`），加第 4 项必然要动它——这是预期内的修改，不是回归** |
| `components/Journal/journalNoLayerLabels.source.test.ts` | ★ 新增（RP0-3）：源码扫描 `components/Journal/**` 与 `lib/journal*.ts`，断言其中**不出现** `open_funnel.LAYER_LABELS` 里的任一中文层名字面量（层名只许来自 wire）。名单从后端夹具 `fixtures/layer_labels.json` 读，由 `test_ai_trade_journal_gates.py` 保证该夹具与 `LAYER_LABELS` 同步 |
| `app/page.test.tsx`（增补） | ★ 新增（RP1-7）：持久化 `activeView="journal"` + `journalEnabled=false` ⇒ 落在 `"trade"` 且主区非空（**不是空白页**）；`journalEnabled=true` ⇒ 停在 `"journal"` |

### 8.3 验收标准（人工可核验，逐条给出操作步骤）

| # | 标准 | 怎么验 |
|---|---|---|
| **A1** | 决策留痕在进程重启后仍在 | 跑一次 AI 自动交易 ≥3 轮 → 停后端 → 重启 → 「交易日志」里逐轮可见动作、上下文、模型输出、门禁链 |
| **A2** | 决策 ⇄ 成交双向可达 | 任一条已成交的决策行能点到它的成交与订单回执；「成交与订单」页的任一行能点回产生它的决策轮；未成交轮明说原因 |
| **A3** | 后训练样本的输入是真的（★ 评审 RP0-1 重写） | v1 的验法**判不出这条**：它只比 `messages[1]` 与 trace 面板上的上下文，而两者都是「本轮 payload」——真正被丢掉的是**前面那十条历史消息**，两边都不携带这个信息。改为：采集档置 `full` 跑 ≥8 轮（跨过一次滚动摘要重置）→ 在 `decider._stream_decision` 的出站处临时打一行 `logger.info(json.dumps([m.role for m in messages]))`（只为验收，验完删）→ 导出 `sft-jsonl` → 抽 3 条，断言其 `messages` 的 **role 序列与条数**与那一行日志逐项一致，且最后一条 assistant 的 `input` 等于 `output_json`。`standard` 档下由 `shaMatch` + `skipped.contextIncomplete` 两条判据共同保证 |
| **A4** | 总闸关 = 逐字节回退 | `AI_TRADE_JOURNAL_ENABLED=false` 重启 → 跑 3 轮 → 三张表零行、REST 全 404、导航项不渲染。**全量 pytest 与合入前同绿，但有两个例外必须先改**（★ RP1-3）：`test_main_shutdown.py:160-168` 的冻结步骤列表要补一行、`NavRail.test.tsx:15` 的「three primary nav items」要改成四项。这两处是**预期内的契约更新**，不是回归——把它们写在这里，正是为了不让实施者在最后一刻拿「反正是既有红」把它们放过去 |
| **A5** | 热路径零影响 | 24 小时 5 标的会话后 `stats.counters.dropped == 0` 且 `queue.depth` 峰值 < `queue.max × 20%`；同期决策轮耗时分布（`telemetry.latencyMs`）与关闭日志时的对照组无系统性差异 |
| **A6** | 界面零横向溢出 | 1280×720 与 1440×900 两档窗口下，日志台页面级无横向滚动条（表格自身的 `overflow-x` 除外） |
| **A7** | 不完整必须被说出来 | 人为把 `QUEUE_MAX` 调到 1 制造丢弃 → 健康卡出琥珀横幅并给出条数；人为截断导出文件 → 缺 `eof` 行 ⇒ 校验脚本判不完整；★ RP0-2：把 `EXPORT_TIME_BUDGET_S` 调到 `1` → 导出一个大范围 → 得到的文件**有** `eof` 行且 `complete=false` / `truncatedReason="time_budget"`（而不是一个被代理撕断的、语法上看不出问题的半截文件），界面上如实显示「第 1 / N 片」 |
| **A9** | 大导出在桌面端真的能跑完（★ RP0-2） | 攒够 ≥5 万条决策行 → 在**打包后的桌面端**（不是 `next dev`）导出「全部历史」的 `audit-jsonl` → 预检说几片就得到几个文件，每个文件都有 `eof` 行且逐片 `complete=true`（末片除外可为 `false`），**全程没有 `socket hang up` / `ECONNRESET`**。这条必须在桌面端产物上验——`proxyTimeout` 是烤进 `required-server-files.json` 的 |
| **A10** | 门禁链说的是真话（★ RP0-3） | 任取一条被门禁改写的决策行 → 详情页第 2 段列出 **13 行**（不是 8 行），其中恰好一行标「否决」且其层名与后端 `gates_json.vetoedBy` 一致；把某一层关掉重启再跑 → 该行变「未装配」而不是消失；`grep` 前端源码，**搜不到任何一个层名的中文字面量** |
| **A8** | 保留策略不伤活会话 | 起一个会话 → 把 `RETENTION_DAYS` 调到 0 天等价的极端值 → `POST /prune` → 该会话的行一条不少，`protectedGroups` 里有它 |

---

## 九、风险与缓解

| # | 风险 | 后果 | 缓解 |
|---|---|---|---|
| **R1** | 采集拖慢或打断决策热路径 | 真实资金的交易被审计需求害了 | 同步入队 + 已序列化 + `put_nowait` + **整体 try/except**（★ RP1-5：`json.dumps` 跑在热路径上，它会抛）+ `default=str` + **A-1 的两条 AST 判据：零 await **且** 函数体恰好是一个覆盖 `Exception` 的 `ast.Try`**；`CancelledError` 不在 `Exception` 之下故取消语义不变；写库在独立 task 里；A5 用实测数据把它钉死 |
| **R2** | 磁盘无节制增长（桌面端 SQLite） | 用户机器被塞满 | §2.3 的量级估算 + 天数/行数双闸 + 健康卡直接显示实占字节 + `minimal` 档兜底；默认值把最坏情形钉在 ≈1 GB |
| **R3** | 导出文件泄露敏感信息 | 数据离开本机后不可撤回 | 写入时脱敏（不是导出时）+ 键名与值形态双黑名单 + manifest 写明脱敏策略；事实核查确认 `payload` 本就不含凭证，脱敏是纵深防御 |
| **R4** | 提示词渲染代码演进 ⇒ 重建不出当时的原文 | 拿假样本去训练，且无人察觉 | `prompt_sha256` 判据（★ RP0-1：取自**真正送出**的那条文本，含重试后缀，而不是按 payload 重算的那一条）+ 不一致即**排除该样本**并在 manifest 报数 + 详情页醒目告知 + `full` 档可完全规避 |
| **R14** | ★ 新增（RP0-1）：把单轮重建当成模型看到的输入 | **本轮最贵的一条**：训练集里每一条样本的 `messages` 都缺了前十条历史消息，而 `shaMatch=true` 让所有人以为它是对的 | §2.2.4bis 的上下文链：`decider.last_request_shape` 在出站那一行现算 ⇒ `context_ref_json` / `context_turn_json` 落库 ⇒ 导出按链重建；重建不出即排除并计入 `skipped.contextIncomplete`；`contextMode=lastTurnOnly` 是**显式**出口且强制 `quality.contextTruncated=true`；总闸 15 可整条回退 |
| **R15** | ★ 新增（RP1-6）：纸面 / 无凭证会话导出恒 0 条 | 用户跑了三个月纸面、点导出拿到一个空文件，且界面说不出为什么 | `minLinkConfidence` 默认改 `auto`（服务端一处解出、随回执下发 `resolvedMinLinkConfidence` + `resolvedReason`，前端照抄不重算）；`test_ai_trade_journal_export_sft.py` 里一条专门的回归护栏：scope 内无任何 `cl_ord_id` ⇒ 导出条数 > 0 |
| **R5** | 日志与既有真源漂移（两份真值） | 审计系统自己在说谎 | **不存 `outcome_json`**、不复制 `trades` / `live_order_audit`；结果一律读时关联并标注 `linkConfidence` |
| **R6** | 关停时丢日志 | 最后一段（往往是出事那一段）恰好没了 | 有界 drain → cancel → **await**；未排空计入 `droppedOnShutdown` 并在下次启动写一条事件行；**关停步位置钉在第 8 位**（`ai_trade_intel` 之后、`ai_backtest` 之前），由 §2.2.2 约束 6 写明理由、由 `test_main_shutdown.py` 的冻结列表与新增的位置断言两处守住（★ RP1-3：v1 称「由 §2.2.2 与 §七校验 1 钉死」，而那两处当时一个字都没提位置——一条**声称自己已被守住、实际没有**的缓解比没有缓解更危险） |
| **R7** | SQLite 写锁竞争 | 交易落库变慢甚至超时 | 单写者 task + 批量提交 + 既有 WAL（`db/session.py:_configure_sqlite`）；**绝不在决策协程里开事务** |
| **R8** | 大导出把渲染进程顶爆 **或被代理在 300 秒处撕断** | 桌面端崩溃 / 拿到一个语法完好但内容不全的文件 | ★ RP0-2 后：服务端流式分页 + **`EXPORT_MAX_ROWS` 与 `EXPORT_TIME_BUDGET_S`（240s）两道并列硬顶** + `eof.truncatedReason` / `nextBeforeId` 分片契约 + 预检给 `estimatedSeconds` / `slices` + 顺序分片下载 + `<a download>` 而不是 Blob + **跨仓源码门禁**盯住 `next.config.mjs` 的 `proxyTimeout`。v1 只有行数闸，而按 §2.3 的量级它在触顶前早就撞墙了（本仓已为同一形状付过三次代价） |
| **R9** | 关联启发式误配 | 训练样本的标签是错的 | 启发式**必须唯一命中**，否则判 `none`；SFT 默认 `minLinkConfidence=auto`（有交易所单号的 scope 恒解出 `exact`，与 v1 行为一致；没有的 scope 才放宽到 `heuristic` 并如实说明，见 R15）；`linkConfidence` 全链路可见 |
| **R10** | 被误接进回测车道 | 合成数据混进后训练集 | AST 门禁 G-4 冻结「`backtest.py`/`backtest_manager.py` 全文 0 次 `journal`」 |
| **R11** | 新导航项挤压 48px 竖栏 | 小窗口下布局塌 | 竖栏是纵向排布（`w-12` + `h-11` 每项），第 4 项只增 44+4 px 高度；1280×720 下 4 项仍有大量余量；由 U-6 覆盖 |
| **R12** | `store.ts` persist 版本升级伤到老用户 | 刷新后偏好丢失 | `version: 31 → 32` 的 migrate **只补新键默认值、不删不改任何既有键**；由既有 `store.test.ts` 的迁移用例扩一条覆盖 |
| **R13** | 事件 `code` 被随手改名 | 离线脚本静默失配 | §5.2 明写它是稳定契约；§2.2.6 的码表是唯一登记处；新增码必须同时进码表与中文化表（用例 B-8 双向校验） |
| **R16** | ★ 新增（RP0-3）：门禁层名在前端被抄第二份 | 界面上出现一个后端不认识的层名，或漏掉真源里新增的层，**且不报任何错** | 层名一律随 `gates_json.layers[].label` 下发（真源 `open_funnel.LAYER_LABELS`）；后端 AST 门禁 + 前端源码扫描门禁两侧同时守；`gate_veto.py` 的纪律 1 是这条的成文先例 |

---

## 十、实施顺序（每一步都可独立跑测、独立回退）

0. **先建夹具**（★ RP1-9 / RP0-3）：`docs/plans/ai-trade-audit-journal/fixtures/` 下的
   `csv_escape_cases.json` / `csv_escape_golden.csv` / `layer_labels.json`。它们是前后端两侧
   用例的共同依赖，先有它们，后面两条跨仓门禁才写得出来。
1. **数据与配置**：`models.py` 三张表（含 `context_ref_json` / `context_turn_json` /
   `prompt_profiles.kind`）→ `config.py` **16 项 + 4 条校验** → `.env.example` 登记 →
   跑 `test_ai_trade_journal_settings.py` 与 `test_ai_trade_journal_proxy_budget.py`。
2. **采集内核**：`journal/records.py` + `redact.py` + `writer.py` + `runtime.py` →
   跑 writer / records / redaction 三个用例文件。**此时尚未接线，全量回归必须与合入前同绿。**
3. **接线**：`decider.py` **三个**只读出口（含 `last_request_shape`）→ `session.py` 两个同步
   方法（`RoundJournalInput` 入参 + 整体 try/except）、`_journal_gates` 与**三处**调用
   （轮末 / 异常分支 / `_maybe_skip_round`）→ `manager.py` 六个事件写点 → `main.py` 启动与
   **第 8 位**关停步 → **同步改 `test_main_shutdown.py` 的冻结列表** → 跑 session_hook /
   isolation / gates / context_chain 四个用例。
4. **仓储与读侧**：`ai_journal_repositories.py` → `linking.py` → `service.py` →
   `schemas_journal.py` → `api/ai_trade_journal.py` → 跑 api / linking 用例。
5. **导出**：`export_audit.py`（含墙钟预算与分片）→ `export_sft.py`（含上下文链重建与
   `minLinkConfidence=auto`）→ `retention.py` → 跑 export_audit / export_budget / export_sft /
   retention 四个用例。
6. **前端数据层**：`journalTypes.ts` / `journalApi.ts` / `journal.ts` / `journalExport.ts` →
   跑两个 lib 用例。
7. **前端界面**：13 个组件由内向外（表 → 详情段 → 详情壳 → 视图壳 → 工具条 → 导出弹窗 →
   健康卡）→ 跑 5 个组件用例。
8. **接线与打磨**：`store.ts`（v32）/ `NavRail.tsx` / `page.tsx` / `httpTimeout.ts` →
   跑 NavRail 与 noWrap 用例 → 全量 `pytest` + `vitest` → 按 §8.3 逐条走 A1–A8。

---

## 十一、已知欠账（本轮不做，但必须写下来）

| # | 欠账 | 为什么现在不做 |
|---|---|---|
| D1 | 导出没有断点续传 | 桌面端本地流，失败重来的代价是可接受的；加断点要引入服务端导出作业表 |
| D2 | 没有偏好对（DPO）导出格式 | 需要「同一上下文下的两个不同输出」，而生产里只有一个。可由 `excludeGateVetoed=false` 的样本在离线侧自行构造，本轮先把 `gateVetoed` 标签留准 |
| D3 | `live_order_audit` 没有按 `cl_ord_id` 的索引 | 加索引 = 动既有表（违反 §N1）；当前量级下**按 `account_id + id` 范围后过滤**足够（★ RP2-5：该表的索引是 `ix_live_audit_account("account_id", "id")`，`created_ts` **不在索引里**；v1 写「`account_id + created_ts` 范围」会让实施者写出一条走不到索引的查询） |
| D4 | 事件日志没有全文检索 | SQLite FTS5 需要额外虚表与触发器，与「零迁移」冲突；`q` 参数的 `LIKE` 在十万行级可用 |
| D5 | 日志台没有实时跟随（tail -f） | §N4；运行期的实时面已由 `agent_trace` 承担 |
| D6 | 采集档变更不追溯 | 改档只影响此后的行；每行自带 `capture_tier`，读侧据此说明「这一段为什么没有上下文」 |

---

## 十二、评审结论

**有条件通过（Approved with conditions）。**

这份设计的骨架是对的，而且对得相当扎实：它没有去动任何一条交易判定式，把日志层做成了一条
「只读取、不参与」的旁路；它拒绝复制 `trades` / `live_order_audit`，用读时关联 + 如实标注置信度
换掉了「两份真值、其中一份悄悄过期」这个本仓库反复付过代价的形状；它把「零 DB 迁移」「每个新
机制都有一个关得掉的取值」「前端一个口径数字都不硬编码」这三条既有纪律逐条落到了实处；连
`trades.id` 而非 `ts` 配对、`AI_TRADE_` 前缀白名单、`/health` 未声明 `response_model` 这类只有
读过源码才写得出来的细节都是准的。**它的问题不在骨架，而在三处「护栏看起来在，其实拦不住它
声称要拦的东西」**——而这恰好是审计系统最不能犯的错，因为审计系统一旦说谎，没有第二个系统会
来纠正它。

三条 P0 与九条 P1 已在 v2 正文逐条改掉（改动点见 §零的「v2 处置」列）。**放行的条件是下面
五条，全部要在实施期被验证，任何一条不满足即视为本轮未完成**：

1. **上下文链必须真的按链重建（RP0-1）**。`test_ai_trade_journal_context_chain.py` 必须包含
   那条「删掉第 1 行 ⇒ 第 3 轮样本被排除且 `skipped.contextIncomplete == 1`」的断言。
   如果实施期发现按链重建的体量或复杂度不可接受，**允许的退路只有一条**：把
   `AI_TRADE_JOURNAL_CONTEXT_CHAIN_ENABLED` 默认置 `false`、SFT 一律产出
   `quality.contextTruncated=true` 的单轮样本、并在导出对话框里说清「这批样本只含最后一轮，
   与模型当时看到的不同」。**绝不允许的是**：保留 v1 那种「单轮样本 + `shaMatch=true`」的组合
   ——那不是少做一件事，那是把一句假话写进了导出文件。

2. **导出必须在打包后的桌面端上跑通一次大导出（RP0-2，验收 A9）**。`next dev` 上验不算数：
   `proxyTimeout` 是烤进 `required-server-files.json` 的。`test_ai_trade_journal_proxy_budget.py`
   在 `next.config.mjs` 解析不出数字时必须**判红而不是跳过**。

3. **门禁链的层名一个字都不许落在前端（RP0-3）**。后端 AST 门禁与前端源码扫描门禁两条都要真的
   写出来并判过一次红（先故意抄一个层名进去，确认它红，再删掉）。一道从未红过的门禁，与没有
   门禁在工程上是同一件事。

4. **关停步插在第 8 位，且 `test_main_shutdown.py` 的冻结列表在同一个提交里改（RP1-3）**。
   不允许「先合入、红着、下一轮再修」——那张列表的 docstring 已经预先说明了它为什么必须同步改。

5. **`_journal_round` / `_journal_event` 的两条 AST 判据都要有（RP1-5）**。只查 `await` 是 v1 的
   缺口：「不会 await」与「不会抛」是两件不同的事，而序列化恰好是会抛的那一件。

另有九条 P2 已在 §零记录并顺手改掉了正文字面（端点与文件计数、`executor.py` 的真实路径、
`live_order_audit` 的索引口径、`httpTimeout` 里那行死码等），不构成放行障碍。

最后一句提醒，给实施者：本设计里唯一一处**没有**任何自动化门禁能守住的东西，是
§2.2.4bis 的 `last_request_shape` **必须赋值在 `self._llm.stream(...)` 之前**那一行。
放到别处（比如写点里按 `payload` 重算）不会报错、不会判红、用例照绿，而导出的每一条样本都会
从此静静地说假话。**在那一行上多写两句注释，说清它为什么必须在那里。**


---

## 实施过程发现的方案缺陷

本节按实施顺序记录**设计与代码库实情不符**、或**设计内部自相矛盾**的地方，以及实际采取的修正。
每一条都遵循同一条纪律：**不静默偏离**——先在这里写清楚，再按修正后的方案落地。

### A. 上下文链（§2.2.4bis）：三处必须改，否则那条链在结构上就重建不出来

| # | 设计怎么说 | 代码库实情 | 处置 |
|---|---|---|---|
| **I-1** | §2.2.4bis ④ 的重建公式写「`+ ([user(摘要种子)] if summarySha256 else [])`」——**一条**消息 | `decider._install_summary_seed` 装的是**两条**：`[user(种子文本), assistant("已了解此前执行情况，将据此继续决策。")]` | 重建按**两条**。按一条重建会让 `contextTurns`（= 出站时 `len(self._context)`）与实际重建条数**恒差 1**，于是摘要重置之后的**每一条**样本都会掉进 `skipped.contextIncomplete`——一条「护栏把所有好样本也拦掉了」的失败，且它在用例里看起来像是护栏在正常工作。由 `test_ai_trade_journal_context_chain.py::test_third_round_messages_rebuild_the_whole_conversation` 的 role 序列断言冻结 |
| **I-2** | §2.2.4bis ① 把 `last_request_shape` 定为 **7 个结构性键** | §2.2.3 同时要求 `full` 档存 `prompt_text`（**含重试后缀的原文**），§2.2.4bis ③ 要求摘要种子留档；这两段大文本**只有出站那一刻拿得到**，写点按 `payload` 重算不出来（那正是 RP0-1 的整条论证） | `last_request_shape` 扩为 **9 键**：7 个结构性字段逐字保留，另加 `finalUserText` / `summaryText` 两段**引用**（不复制，两者本就活在 `_context` 与局部变量里）。它们只在 `full` 档与摘要留档时被真正读走 |
| **I-3** | §3.2 说 `decider.py` **+3 个只读出口** | `context_turn_json` 的内容（门禁**后**的 `decision_input` + **未裁剪**的 `tool_result` 原串）只有 `commit_round` 知道，而它发生在 `last_request_shape` 赋值**之后**（两者生命周期不同，塞不进同一个 dict）。在 session 侧按 `decision` 重建会漏掉 `extra`（反思 / 门禁注记）合并进 `tool_result` 的那一半 | 增第 **4** 个只读出口 `last_context_turn`，唯一写点就在 `commit_round` 刚 append 完那两条之后。这不是加法洁癖：在别处重建它就是第二份真源，而它漂移时导出的样本会教模型去说门禁替它说的话 |

### B. 单一真源与形参纪律

| # | 结论 | 处置 |
|---|---|---|
| **I-4** | `output_json`（模型**原始**工具入参）与上下文里的 assistant 段（门禁**后**的最终 decision）必须**同形**才比得出差异，而 `commit_round` 里那段 `decision_input = {...}` 是它今天唯一的装配点 | 抽成模块级纯函数 `decider.decision_input_wire(decision)`，`commit_round` 与 `journal/records` 共用。两处各写一遍，会在下一次给 `TradeDecision` 加字段时静默分叉——而后训练样本的训练目标正是这两者之一 |
| **I-5** | §2.2.1 给出的 `_journal_event` 调用是「4 个位置形参 + keyword `detail`」，超过 `CLAUDE.md` §二的「形参 ≤ 5」硬阈值 | 改收冻结聚合对象 `EventJournalInput`——与评审 RP1-4 对 `_journal_round` 的处置**同一条纪律**，两个写点没有理由用两种姿态 |
| **I-6** | §2.2.1 的 `RoundJournalInput` 只给了一个 `post_round_fills` 列表，但用例 A-4 要求 `legs` 的第二条标 `source="post_round"`——**一个列表标不出那条分界线** | 改为两字段：`round_fills`（本轮 `executor.trades` 上新增的**全部**腿）+ `post_round_offset`（`_post_round` 之前那一刻的水位差）。入参仍然只承载引用，裁剪与标注全在 `build_decision_row` 一处 |
| **I-7** | §2.2.7 要求 `_journal_gates` 的装配点唯一在 `session`，而 RP2-8 要求「`session.py` 只允许加两个薄方法，任何新逻辑一律落在 `journal/` 包内」——两条互相拉扯 | `session._journal_gates` 只做**取数**（把各层 runtime 已有的只读断面读成两张 dict），形状装配交给纯函数 `journal.records.build_gates`。装配点仍然唯一，而 13 层三态的判定逻辑不落在那个已经 4162 行的文件里 |

### C. 门禁判据本身写错了会怎样

| # | 结论 | 处置 |
|---|---|---|
| **I-8** | §8.1 的 **G-4** 写「`backtest.py` / `backtest_manager.py` 全文 0 次 `journal`」。**这条按字面实现必红且永远红**：`decider._journal`（滚动摘要的素材表）早于本特性存在，`backtest.py` 的注释里逐字引用它十余处 | 判据改取 **AST**：不得 `import` 日志包、不得引用它导出的任何符号。既不可能被无关注释影响，也真的拦得住「回测悄悄接上了日志层」这件事本身。**一道会因无关文字变红的门禁，下一个人只会把它放宽或删掉**——那才是真正的损失 |
| **I-9** | §七 第 16 项把 `EXPORT_TIME_BUDGET_S=0` 定义为「该闸**不生效**」（无代理直连排障用），而 §8.1 的用例描述写「预算设成 `0.0` ⇒ 首页之后即收尾」。**两处互相矛盾** | 按 §七 实现（那是与 `.env.example` 双向登记、且被启动期校验器守着的那一份）。用例改用**极小正数**触发墙钟截断，另加一条专门守「`0` = 关闸」。矛盾记录在 `test_ai_trade_journal_export_budget.py` 的模块 docstring 里 |
| **I-10** | 前端源码扫描门禁 `journalNoLayerLabels` 扫的是**源码原文**，不区分注释与代码 | 三条降级码 / 退出归因的中文化文案与门禁层名撞串（`交易所侧存在未接管持仓` / `保护性平仓冷却` / `交易所侧兜底`）。**改的是措辞，不是门禁**：那三条本来就是「降级原因」而不是「层名」，换个说法反而更准；放宽门禁则等于把它要拦的那件事放回来 |

### D. 关停与导出的两处工程折中

| # | 结论 | 处置 |
|---|---|---|
| **I-11** | §2.2.2 约束 6 说未排空的条数「在**下次启动**时由 `runtime.py` 写一条 `journal.shutdown_drop` 事件行」 | 改为在 `stop()` 内**当场写**。关停步的位置已被同一条约束钉死在 `db_engine` **之前**，故此刻引擎仍在、这一次写必然写得出去；推迟到下次启动反而要引入一个新的跨进程状态文件，而进程被硬杀（桌面端 8s 后 `/F`）时那个文件同样写不出来。事件码 / 分类 / 级别逐字沿用 §2.2.6 的码表 |
| **I-12** | §2.5.1 的 `audit-jsonl` 行序写「manifest → promptProfile* → decision* → event* → fill* → order* → eof」 | 两处调整，各有硬理由：① **档案行改为首次被引用时就地发出**（仍在它的引用者**之前**到达，进程内按 sha 去重）——把它们全部排到最前面需要先做一次全量预扫描，而那次预扫描会把墙钟预算吃掉一半；每一行都自带 `kind`，顺序不承载语义。② **成交 / 订单 / 往返不单独出行**，而是进每条 `decision` 的 `outcome`——它们已经在那里了，再发一遍就是同一事实的第二份记录，而「两份真值、其中一份悄悄过期」正是 §R5 从头到尾拒绝的形状 |

### E. §3.2 / §3.4 的修改清单漏了两个**冻结注册表**

评审 RP1-3 已经点名了 `test_main_shutdown.py`（关停步的冻结列表）。实施期发现**另外两个同形的**：

| # | 文件 | 为什么漏了它就是假绿 |
|---|---|---|
| **I-13** | `apps/web/components/ui/contrastTokens.source.test.ts` | 它的门禁 ④ 断言 `lib/**` 下的 **fetch 调用点数与文件数**（施工前 21 / 20），并在注释里逐轮记录了这个数字为什么变。新增 `journalApi.ts` 带来 2 个调用点 / 1 个文件，必须在**同一个提交里**改成 23 / 21——那张计数表存在的理由正是「一个新 `*Api.ts` 忘了接超时时按钮会静默冻结 5 分钟」。**另有一条随之而来的写法约束**：它按**行**扫「出站调用与 `withTimeout` 是否同现」，故 `journalApi.post` 里那两者必须写在同一行（跨行会被判成漏接） |
| **I-14** | `apps/web/lib/types.ts` 的 `Health` 接口 | §3.4 的前端修改清单里没有它，但 `/api/health` 的加法键 `journalEnabled` 不在这个接口上声明，`page.tsx` 就读不到它（tsc 直接红）。加的是一个**可选**键（老后端没有这个键，按「开着」处理） |

另有两处与设计无冲突、只是设计没写到的实情：

- **I-15**：`app/page.test.tsx` 在仓库里**原本不存在**（§8.2 写的是「增补」）。本轮新建，并按本仓惯例把重型子组件全部替身，只测外壳的那一条 effect。
- **I-16**：`scrollIntoView` 在 jsdom 里不存在。「选中行滚动到可视区」是渐进增强而不是契约，故用 `?.` 兜底——绝不让一个可有可无的体验优化把整张表打崩。

### F. 自查阶段发现的两条**本轮引入**的缺陷（已修，并各补了回归用例）

这两条不是设计的问题，是**实现的问题**。写在这里的理由与上面各条相同：不静默修掉。

| # | 缺陷 | 后果 | 修法 |
|---|---|---|---|
| **I-17** | `ai_trade_decision_logs.executed` 最初只取 `executed.traded` | 那正是评审 RP1-2 点名的形状**在另一处复发**：利润保护在 `_post_round` 里平掉的那一笔不在 `executed` 里，于是「模型什么都没做、轮末被保护平了一笔」的轮次会**整条不出现**在「成交与订单」页上（该页按 `executedOnly` 过滤）。而利润保护正是本仓实测唯一为正的确定性离场通道 | 该列的语义确定为「本轮到底有没有发生成交」= `traded or bool(legs)`。由 `test_ai_trade_journal_records.py::test_executed_column_admits_the_post_round_fill` 正反两向冻结 |
| **I-19** | `audit-jsonl` 的 `eof` 在**事件段被墙钟截断**时仍写 `complete: true` | 事件段与决策段共享同一份墙钟预算，但截断信号只从决策段那条路径回到 `eof`。结果是一个「语法完好、少了一半事件行、且自称完整」的文件——正是完整性信封本身要消灭的东西 | 事件段改用可变回执把 `emitted` 与 `reason` 交回调用方；`eof` 的 `complete` 取两段的合取，并新增 `eventsEmitted` 让两段的条数各自可数。两条用例分别冻结「事件段被截断 ⇒ complete=false」与「两段都跑完 ⇒ 两个计数各自正确」 |
| **I-22** | 保留策略用例**整表删** `strategy_runs`（本轮引入的**测试**缺陷，不是产品缺陷） | 本仓的用例共用一个临时库，而 `equity_points` / `trades` 的外键在 SQLite 上不被强制、Core 的 `delete()` 也不走 ORM 级联。整表删把**别的用例**的净值点变成孤儿行；而 `INTEGER PRIMARY KEY` 在删掉最大 id 之后**会复用**它——于是后面某个用例新建的 run 会读到一段属于已删 run 的净值序列。症状离现场极远（`test_api.py::test_backtest_uses_requested_time_range` 的 `equity[-1].ts` 对不上），而单独跑那个用例**又是绿的**——正是最难归因的那一类。由「排除本轮全部日志用例后跑同一批文件」的对照跑锁定：**对照组 3294 条零失败，含日志用例时恰好多出那一条**。 | 四处收敛：保留策略只删自己那两组 run；`export_sft` / `context_chain` 的 `Trade` 清理按 `run_id ∈ (本组的 run)` 收敛；API 用例那处 scoped delete 直接去掉（删任何一行 run 都会把 id 释放出去给别人复用）。四个文件各自留了一段注释写清这条纪律 |
| **I-21** | 门禁链最初按**合成后**的 `degrade` 归类 | `degrade` 在写点之前已被 `degrade or executed.get("closeBlocked")` 污染，而 `exit_order_slot_busy` 不在任何降级前缀里、经 `classify_block` 会落进 `"other"` ⇒ 门禁链上会出现一条**凭空捏造**的「本轮被『其它』层拦下了」。这条形状 directional-symmetry B-2 已经成文过一次（当时是模型读到那句假话，这次是用户在审计台上读到） | 改取 `gate_degrade`（**执行之前**的那一个）。`degrade_reason` 那一列仍存合成值——用户看到的「本轮降级原因」就该是它，两者各说各的、互不冒充。挂单槽位被占用的落点在详情页第 6 段的 `closeBlocked` 上。由一条**源码判据**冻结（只断言运行期结果的用例对它不敏感：正常轮里两个值恰好都是 `None`） |
| **I-20** | `journal` 的 `limits_from_settings` 与情报层门禁 `G-7` 的冻结**子串**撞名 | `test_ai_trade_intel_session_binding_core.py::test_g7_...` 逐字断言 `session.py` 全文不含 `limits_from_settings`——那道门禁守的是「会话侧一个情报设置键都不许直读」。加前缀（`journal_limits_from_settings`）**也不行**，它仍含那个子串 | 改用完全不同的构词 `capture_limits_of`。放宽一道守着真问题的既有门禁，代价远大于换一个名字 |
| **I-18** | 分片下载**没有游标** | §2.6.4 说「第 k+1 片的 URL 带上第 k 片 `eof.nextBeforeId`」——**但浏览器导航式的下载读不回响应体**，那个值前端永远拿不到。照原样实现的结果是：`slices=3` 时顺序发三次**完全相同**的请求，用户得到三个一模一样的文件，而界面上写着「第 3 / 3 片 · 完整」。**比不分片更坏**：它看起来像成功了 | 游标改由**预检**给出（`preview.sliceCursors`，服务端在同一份过滤 + 同一个排序上精确算出）：SFT 侧在预检遍历时顺手记下边界（零额外代价）；审计两档由 `slice_boundary_ids` 用至多 N−1 次 id-only 查询算出（N 是片数，个位数）。前端按 `min(slices, cursors.length + 1)` **收敛片数**——绝不发一片没有游标、会从头重拉的请求，**且按钮文案用的就是这个收敛后的数**（写「分 3 个文件」却只下 2 个，是这个界面上最容易被信以为真的一句假话）。四条用例分别冻结：后端的边界切分（含「边界落在最后一行 ⇒ 不切」）、后端的两片无重无漏、前端的「三片各带自己的游标」、前端的「只给得出 1 个游标 ⇒ 只发 2 片且文案说 2 片」 |
