# AI 自动交易 · 后训练轨迹采集（思维链 · 工具调用 · 执行结果 → 本地数据集）

> **状态**：**v2**（方案评审与修复 · 2026-09-10）——v1 的 0 条 P0 / 15 条 P1 已全部就地修入正文（逐条见下方
> 「评审记录」，正文里以 **【v2 · RVn】** 标注修改落点）；P2 中能顺手修的已修，其余登记为开放项。
> **Feature slug**：`ai-trade-trajectory-capture`
> **要解决的用户诉求**（原文）：「在 AI 自动交易功能启动面板中添加一个开关，**默认关闭**。用户开启后，
> 对自动交易的数据进行收集，保存在**默认目录**下，且**目录可以修改**。收集的是 AI 自动交易时的
> **思考、执行、分析过程以及工具调用（含工具调用参数）**等全部相关数据，最终用于模型后训练，因此
> 必须是**完整的**思维链与工具调用数据，可收集成数据集，后续用于强化学习后训练。稳健、完善、可靠。」
>
> **范围纪律（本轮全部硬约束，评审请逐条核对）**
>
> 1. **只加法**：不改任何门禁判定式、不改任何交易语义、不改 `LLMClient` 协议签名、不新增下单路径。
> 2. **默认关闭且可逐字节回退**：开关不勾 ⇒ 不建目录、不起线程、不包装任何 LLM 客户端，决策轮与
>    合入前**逐字节相同**（由 §6.1 的 S-6 用例冻结）。
> 3. **零 DB 迁移**：不建表、不加列（数据只进文件，不进 SQLite）。
> 4. **零新增关停步骤**：`main._shutdown_steps()` 一行不改（理由见 §0.3）。
> 5. **不依赖任何未提交的在制代码**：尤其不 import `app.ai_auto.journal.*`、不读 `decider` 上在制新增的
>    `last_request_shape` / `last_context_turn` / `decision_input_wire`（理由见 §0.2）。
> 6. **热路径零 await、绝不抛**：所有采集写点都是同步调用，函数体整体 `try/except Exception`；
>    `asyncio.CancelledError` 语义逐字不变。
> 7. **数据只写本机**：不上传、不同步、不出网；写入前逐行剔除凭证原文。
> 8. **回测车道不采集**（v1 非目标，§1.3 N1）。

---

## 评审记录

> 评审节点：Subtask #1「方案评审与修复」· 2026-09-10 · 结论见文末「评审结论」。
> 评审方法：§九 交给评审的 8 条逐条实读源码核对（行号一律为**当前工作树**）；另按可行性 / 完整性 /
> 一致性 / 规模四个维度逐节过一遍。**v1 的源码引用本身全部属实**（decider / session / manager / executor /
> resume / llm_budget 的行号逐条命中），问题集中在「写点的时机与失败路径」「真实退出路径」「数据标签的真源」
> 「前端判据分叉」四处。严重度：**P0** 做出来即不可用或危及交易；**P1** 会静默丢数 / 写错数 / 违背既有成文纪律 /
> 按原文施工必红；**P2** 改进项。**全部 P1 已就地修入正文**，落点以【v2 · RVn】标注。

### P0（0 条）

无。包装器的透明性约束（§2.2.2）与「默认关闭即不包装」足以保证本特性不改变任何交易行为；全仓
`services/quant/app` 对 LLM 客户端**没有任何** `isinstance` / `hasattr` 分支（grep 零命中），`__getattr__`
透传不会改变决策器的分支。

### P1（15 条，全部已修）

- **RV1 · 可行性 · 表单文件行数预算必红。** `useAiTradeLaunchForm.ts` 实测 **780 行**（G-1 按
  `split("\n").length` 计为 781，`launchWizard.source.test.ts:65-75`），v1 写的「当前 744 行、净增 ≤ 30」
  是陈旧读数，照做即 811 行、G-1 判红。**修**：取数、有效值谓词、摘要位与提交体全部下沉到新 hook
  `useTrajectoryCapture.ts`，表单净增 **≤ 12 行**（§2.13.1 / §2.13.2）。
- **RV2 · 完整性 · 采集失败恰好看不见。** 状态条以回执 `enabled`（生效值）为渲染条件；而打开失败时
  `enabled=false`、`state="failed"` ⇒ **唯一需要被看见的那个状态永不渲染**（本仓「算了但看不见」第 N 次）。
  **修**：回执新增 `requested`（用户请求且未被运维关），状态条按 `requested` 渲染（§2.9 / §2.14.2 / F-3）。
- **RV3 · 一致性 · 前端「到底采不采」有三个判据。** 卡片禁用看 `configQ`、摘要看 `active`、提交体直接发
  `trajectory` 原值 ⇒ 持久化偏好里的 `enabled:true` 在「配置读取失败 / 运维关」时照样发出：新后端瞬时失败
  ⇒ **实际在采、摘要却不显示**；运维关 ⇒ 静默 `ops_off`。**修**：单一谓词 `effectiveEnabled`，摘要 / `ready` /
  提交体 / 第 6 步去向行共用；`rule.enabled && !effectiveEnabled` 时第 6 步明示「本次不会采集」及原因；
  提交体只在 `effectiveEnabled` 时携带该键（关闭时启动体逐字节不变，呼应范围纪律 2）（§2.13.1 / §2.14.1 / F-9）。
- **RV4 · 完整性 · 已成交的轮次会丢掉整条断面。** W3 排在 `_post_round` 之后，而它之前还有
  `commit_round`、四次推送、`flush_summary`（一次摘要 LLM 调用，最长 `ai_trade_summary_timeout_s`）与
  `_post_round`；`session.pause()` 第 ③ 步即 `_cancel_task("_decision_task")`（`session.py:1554`）。这段窗口内被
  暂停 / 停止 / 抛错的轮次**已经真实下单成交**，却只剩 `round_aborted` / `round_failed`：观测、模型动作、门禁
  全部丢失，成交腿被迫落进 `tripsWithoutDecisionRound`。**修**：W3 拆为 **W3a `stage_round`**（紧跟
  `_round_reflection_summary()`，同步、字段全部就绪）+ **W3b `end_round`**；W4 / W5 见到已暂存断面即写
  `round{completion:"partial"}`（§2.3 / §2.4 / S-10）。
- **RV5 · 完整性 · 跳过轮一轮两条互相矛盾的记录。** W6 在 `_maybe_skip_round` 内写完 `round_skipped` 即
  `return`，`finally` 里的 W5 仍会写 `round_aborted`；v1 没有定义回合作用域的状态机与关闭责任。**修**：
  `RoundScope.state ∈ {open, staged, closed}`，§2.3.2 逐写点列出迁移，`release_round` 只处理 `open` / `staged`（S-11）。
- **RV6 · 一致性 · 蒸馏采集是写点无读点，还会伪造缺口。** 蒸馏跑在 `stop_group` **之后**的后台任务里
  （`experience/scheduler.finalize` → `run_bounded(session.decider.llm, …)`，`scheduler.py:128-153`），此刻 episode
  已关、recorder 多半已关 ⇒ v1 所说「记为 `out_of_round`」几乎永远落不了盘，而这些晚到调用又被计进
  `dropped`，在状态条与编译报告里凭空造出「缺口」。**修**：v1 明确不采蒸馏；episode 关闭后的任何调用与写点
  一律直通，只计 `lateCalls`，**不分配 seq、不计 dropped**（§2.2.3 / §2.3.2 / S-12）。
- **RV7 · 稳健 · 收尾设计落在一条桌面端几乎不走的路上。** Windows 桌面端真实退出是 `POST /api/ai-trade/quit-pause`
  （`desktop/src/main.js:583-584`，**2 秒**硬超时，随后 `taskkill` 失败、8 秒后 `/F` 强杀）→ `manager.shutdown()`；
  lifespan 钩子与 `atexit`「大概率跑不到」是本仓成文事实（`main.js:578-581`、`manager.py:2180-2183`）。v1 的
  排空 / 活动段 gzip（最大 64 MiB，数秒）/ `state:"closed"` 依赖 5 秒 `aclose` + `atexit`。**修**：`note_paused`
  令写线程立即 flush + fsync；`app_quit` 关闭走 `aclose(reason, compress_active=False)`、死线
  `min(close_timeout_s, 1.0)`，活动段保留明文（读侧本就兼容）；`atexit` 降为纯兜底（§2.8 约束 8 / R-13）。
- **RV8 · 一致性 · 停止按钮上 await 后台收尾。** v1 在 `stop_group` / `end_group` 上 `await recorder.aclose()`
  （最长 5 秒且其间可能在压缩），违背成文纪律「真实资金的停止按钮绝不 await 后台收尾」
  （`experience/scheduler.py:10-12` F-1：`/ai-trade/{id}/stop` 是 `FAST_MS` 写路径，超时即「结果未知」）。
  **修**：用户动作路径一律同步 `close_nowait`，只有 `shutdown()` 做有界 await（§2.10.5）。
- **RV9 · 稳健 · 写线程三条泄漏路径 + 双写者。** ①`_build_group` 在 W11 打开 recorder（线程已起）之后，
  `_build_member` 对第 k 个成员抛错 ⇒ 无人关闭；②冷续跑 `resume_cold_group` 正常返回后
  `_verify_armed(cold=True)` 失败 → `_rollback_resume(cold=True)` → `stop("resume_failed", flatten=False)` →
  `_dispose_failed_resume`（`manager.py:1154/1278/1300`、`session.py:1243-1244`）⇒ 同样无人关闭，**再点一次继续**
  会在同一分组目录起第二个写者，两条线程抢写 `manifest.json`；③v1 散落的六个关闭点只有两个覆盖失败路径。
  **修**：「谁打开谁负责」——在 `start_group` 与 `resume_group`（冷分支）两个**所有者帧**里
  `try/except BaseException: close_nowait(...); raise`；recorder 按分组目录**单写者登记**，同目录新开先关旧的
  （§2.8 约束 8 / §2.10.5 / A-10 / A-11 / R-12）。
- **RV10 · 一致性 · 新启动时「打开失败」静默放行。** v1 让打开失败只落 `failed` 回执、启动照常成功，与 §2.10.3
  自己的立论「勾选了却静默不采 = 替用户丢掉明确要的数据，故拒绝启动」矛盾；且打开失败的原因比探测多
  （建分组目录、写 DATASET_CARD、起线程）。**修**：新启动（`resume_of is None`）打开失败 ⇒ 在 W11（**尚未建
  任何 run 行**，`_build_member` 之前）抛 `TrajectoryOpenError`，端点转成与目录校验同形的 422 + remedies；冷续跑
  维持 `failed` 回执、绝不阻塞（§2.10.4 / A-9）。
- **RV11 · 数据正确性 · 记录的采样参数不是真正发出去的那组。** Anthropic 开扩展思考时 `_body` 强制
  `temperature=1.0` 并删除 `top_p`（`anthropic_client.py:70-73`），OpenAI 端另带 `tool_choice` / `stream_options`
  （`openai_client.py:54-70`）。逐个读私有字段会把 `_temperature` 记成行为策略的温度——强化学习的重要性
  采样与离策略校正恰恰依赖它，记错是静默污染。**修**：以 provider 自己的 `_body("", [], tools)` 为唯一真源
  （剔除 `system` / `messages` / `tools`），取不到才回落逐字段读取并标 `paramsSource`（§2.2.4 / T-10）。
- **RV12 · 数据正确性 · 按 `ledgerSeq` 去重在跨 epoch 时会吞掉真实成交。** 冷续跑 `restore_trades` 令
  `trade_count = len(rows)`（DB 行数，`executor.py:1754`），而本仓存在「成交落库失败、仅留内存」的路径
  （`_trade_persist_failed` 一族）⇒ 重启后同一个 `ledgerSeq` 可能指向**另一笔**成交，去重会吞掉它、奖励错配。
  仓库已有唯一真源 `accounting_event_key(trade, runId)`（`accounting_replay.py:84-93`；实时成交恒带
  `explain.meta.accountingEventId`，`session.py:827`）。**修**：去重键改用它，排序键 `(epoch 序, ledgerSeq)`，
  同 seq 不同事件键计 `ledgerSeqConflicts`（§2.11.5 / C-7）。
- **RV13 · 数据正确性 · 奖励重放的会计口径缺席即走 legacy 分支。** v1 给 `TradeAccountingAccumulator` 的上下文
  「推不出时留空」⇒ `costBasis` 缺席即 `_legacy=True`（`accounting_replay.py:130/255/284`），费池口径与实盘不同；
  实盘累加器的真实上下文是 `{"costBasis": net|gross, "ctVal": …}`（`attribution.py:175-176`，run metrics 同源写
  `costBasis`，`manager.py:2459`）。**修**：W1 把该会话累加器的 `context` 原样快照进 `episode_start.accounting`，
  编译器逐 run 使用（§2.6 / §2.11.5 / C-8）。
- **RV14 · 数据正确性 · 模拟盘数据被标成 `live`。** `env = "paper" if creds is None else "live"` 的前一支永不成立
  （AI-trade 启动恒经 `_creds(req.account_row)`，`manager.py:471`）；本仓的模拟盘开关是进程级
  `okx_account_demo`（`config.py:49`，私有请求附 `x-simulated-trading:1`）⇒ 模拟撮合的成交与真实成交混进同一
  奖励分布——正是 N1 排除回测的同一条理由。**修**：`env = "demo" if settings.okx_account_demo else "live"`
  （§2.10.4 / A-2）。
- **RV15 · 可测性 + 稳健 · 一条不可实现的用例与一个无守卫的线程。** ①S-6 要求与「合入前（无本特性）」逐项比较，
  单测进程里不存在一份合入前代码；②写线程主循环若因非 `OSError` 的缺陷异常退出，v1 无顶层守卫，状态停在
  `recording`、队列涨满后静默丢弃——本仓头号反模式「看起来在采、其实没有」。**修**：S-6 改为「采集关 vs 采集开」
  两次运行逐项相等 + 关闭时 `is` 同一对象；写线程主循环顶层 `except BaseException` ⇒ `failed` +
  `lastError.code="WRITER_CRASHED"` + 停止受理（§2.8 约束 11 / S-6 / R-11）。

### P2（19 条；「已修」= 已顺手写进正文，「开放」= 登记不阻塞）

| # | 维度 | 问题 | 处置 |
|---|---|---|---|
| RV-P2-1 | 稳健 | `__getattr__` 在 `copy` / `pickle` 于 `__init__` 之前查属性时无限递归 | 已修：守卫 `_inner` / `_channel` / dunder（§2.2.2） |
| RV-P2-2 | 可行性 | Windows 路径阈值算术偏松：`.jsonl.gz.tmp` + instId≤32 时相对路径约 144 字符，120+144>259 | 已修：阈值改 100（§2.12.2 第 3 条） |
| RV-P2-3 | 稳健 | `manifest.json` 的 `os.replace` 在 Windows 被杀软 / 索引器短暂占用即 `PermissionError` | 已修：有界重试 3×50 ms，单独计 `manifestWriteErrors`，不计入连续写失败（§2.8 约束 10） |
| RV-P2-4 | 性能 | `gzip` 默认 `compresslevel=9`，64 MiB 段在写线程上耗时数秒 | 已修：`compresslevel=6`（§2.8 约束 5） |
| RV-P2-5 | 稳健 | 只写了 fsync 节流、未写 flush，强杀时 Python 缓冲区内的行整批丢失 | 已修：每批 `flush()`（§2.8 约束 6） |
| RV-P2-6 | 隐私 | 先算 sha 再剔除凭证，顺序反了 | 已修：先剔除再算 sha（§2.8 约束 9） |
| RV-P2-7 | 数据 | 摘要调用见 `message_done` 即 `break` ⇒ `closed_early` 实为完整响应 | 已修：记 `sawMessageDone`，编译器据它判 `responseComplete`（§2.2.5） |
| RV-P2-8 | 数据 | `thinkingCaptured` 不区分「链路采不到」与「本次未开启思考」（`agent_thinking_budget_tokens` 默认 0） | 已修：加 `thinkingEnabled`（§2.7.2） |
| RV-P2-9 | 完整性 | N5：OpenAI 兼容端的 `reasoning_content` 不采；本仓 OpenAI / Gemini 预设走 openai 标准（`providers.py:68-72`），对这批用户「思维链」= 研判全文 | 开放：卡片披露生效 provider 是否下发思考文本（需 config 接口带出 `api_standard`）；解析层改造另立特性 |
| RV-P2-10 | 存量缺陷 | R7 实读确认：`_on_tool_call` 后续分片 `id=None`（`openai_client.py:160-162`）被 `if ev.id in accs` 丢弃（`decider.py:908-910`） | 开放：建议**立即**以 bug-diagnosis 单独立项，不并入本特性 |
| RV-P2-11 | 人机 | 第 6 步 remedies 区无条件渲染「运维总闸的三个落点」（`StepReview.tsx:219-225`），对目录 422 是误导 | 开放：仅当 remedies 含 `ops_override` 时渲染（实施前先核既有 W 用例） |
| RV-P2-12 | 一致性 | `StepCognition.tsx` 文件头「本步永不产生阻断错误」会变成假注释 | 已修：列入 §3.4 |
| RV-P2-13 | 数据 | `_context` 里的历史 assistant 轮由 `commit_round` 按**门禁后的最终决策合成**（`decider.py:1293-1297`），不含当时的研判与思考 | 已修：`report.conventions` 与 DATASET_CARD 写明（§2.11.3） |
| RV-P2-14 | 产品 | 「记住上次选择」与需求「默认关闭」的张力 | 决策确认保持：首次 / 无偏好恒关；摘要「采集开」+ 第 6 步去向行 + RV3 提示行保证每次启动可见 |
| RV-P2-15 | 安全 | `POST /trajectory/validate` 可探测任意本机路径（在最近祖先里建删探针文件） | 开放：与既有端点同一「仅本机可信客户端」威胁模型，不另加防护 |
| RV-P2-16 | 规模 | 编译器 CLI 经包 `__init__` 间接 import recorder / llm_tap / db | 已修：`__init__` 不导出编译器，`compile` / `reader` / `rewards` 只依赖 `wire` 与 `accounting_replay`（§3.1） |
| RV-P2-17 | 运维 | 无保留期 / 自动清理 | 刻意：数据集是用户资产；靠 1 GiB 下限自动暂停 + 状态条字节数兜底 |
| RV-P2-18 | 规模 | 编译器（§2.11，约 610 行 + C 组用例）与采集链路完全解耦 | 登记：排在实施第 7 步，工期不足可整段顺延，不影响采集正确性 |
| RV-P2-19 | 数据 | `_cancel_task` 只 cancel 不 await（`session.py:1335-1338`）⇒ `round_aborted` 可能晚于 `episode_end` 入队 | 登记：编译器不得假设写入顺序（R8 已覆盖） |

### §九 核对清单的逐条答复

1. **§0.2 源码引用**：逐条实读，全部属实（`decider.py:884-886 / 901-902 / 915 / 1187-1205 / 1507-1521`、
   `experience/scheduler.py:153`）。
2. **五条硬约束是否足够**：足够。全仓无对 LLM 客户端的 `isinstance` / `hasattr`，`__getattr__` 只需补 RV-P2-1 的递归守卫。
3. **W3 / W7 锚点**：W3 的锚点语句在 HEAD 与工作树上都唯一，但**时机**有 RV4 缺陷，已拆成 W3a / W3b。W7 之后才落账的
   成交由下一次同步补上（水位保证恰采一次），承载记录不同不影响归因。
4. **§2.5 是否穷尽 `_decide_frozen`**：穷尽（`decider.py:625-647`）。墙钟闸只阻止**追加**重试 / 追问；`_maybe_reask` 在
   死线耗尽时返回 `(False, False, True)` ⇒ `reasked=False` ⇒「最后一次」；深度档关 ⇒ `last_reasoning=None` 且不可能追问。
5. **线程方案与 PyInstaller / Windows**：纯标准库，与打包相容；Windows 侧问题见 RV7（退出路径）与 RV-P2-3（文件锁）。
6. **目录校验放端点**：与冷续跑一致（`resume.py:98-125` 用 `start_wire` 重跑 `build_start_request`，任何 HTTPException
   翻成 409）；但「打开失败」一侧不一致，见 RV10。
7. **`trajectoryDirError` 进 `READY_INPUTS` 不进 `BANNER_INPUTS`**：可行（与 `directionError` / `trendError` 同形；
   `STEP_ERROR_OWNERS` 是 `Record<Key, …>`，漏登记 tsc 即红）；兜底行是通用句，卡片内另有就地 `role="alert"` 原因，足够。
8. **R7 是否立即立项**：建议立即单独立项（RV-P2-10）。

---

## 零、动手前必须知道的三件事

### 0.1 在制兄弟特性 `ai-trade-audit-journal` 与本特性的边界

工作树里有一个**尚未提交**的兄弟特性 `ai-trade-audit-journal`（`docs/plans/ai-trade-audit-journal/spec.md`
v2；`services/quant/app/ai_auto/journal/` 共 10 个文件，`git ls-files` 计数为 **0**，即全部未跟踪）。
它也有一个 `sft-jsonl` 导出。两者**目标不同，不得合并实现**：

| 维度 | 交易日志（兄弟特性） | 后训练轨迹采集（本特性） |
|---|---|---|
| 目的 | 管理台可查、可审计 | 训练数据集，**完整性优先** |
| 开关 | 进程级 env 总闸，默认开 | **启动面板逐会话开关，默认关** |
| 落点 | SQLite 三张新表 | **用户可改目录**下的 JSONL 段文件 |
| 文本 | 按采集档截断（standard 4000 字、full 20000 字） | **不截断任何一个字符** |
| 取数点 | 会话写点 + 决策器只读出口（重建上下文链） | **LLM 客户端边界**的原始请求与原始流 |
| 多次调用 | 一轮一行（重试 / 追问 / 反思的中间调用不单独留存） | **每一次** LLM 调用一条记录 |

本特性对它的**唯一**依赖是「不打架」：两者在 `session.py` / `manager.py` / `config.py` /
`.env.example` / `main.py` 上都是加法，且锚点语句不同（见 §3.6 的混合文件暂存纪律）。

### 0.2 为什么「完整」只能在 LLM 客户端边界上取

逐条核过源码（行号为**当前工作树**，含兄弟特性的在制插入；锚点以语句为准）：

| 事实 | 现场 | 后果 |
|---|---|---|
| 模型真正看到的输入是 `messages=self._context + [user_msg]` | `ai_auto/decider.py:884-886` | 单条 user 提示词不是完整输入；`_context` 逐轮累积、被 `_trim_context` 左裁、被摘要种子整体替换 |
| 研判文本在服务端**截断**：`self._last_analysis = "".join(analysis_parts).strip()[:_ANALYSIS_MAX_CHARS]`，`_ANALYSIS_MAX_CHARS = 20000` | `decider.py:915`、`:91` | 从 `last_analysis` 取数 ≠ 完整 |
| 扩展思考 `thinking_delta` **根本不在服务端累积**，只喂给轨迹合并器 `pump.feed("thinking", …)` | `decider.py:901-902` | 会话侧**没有任何对象**持有完整思考文本 |
| 反思只累积 `text_delta`，同样截断到 `_ANALYSIS_MAX_CHARS` | `decider.py:1187-1193`、`:1205` | 同上 |
| 摘要结果截断 `[:_SUMMARY_MAX_CHARS]`，且消费方见到 `message_done` 即 `break` | `decider.py:1517-1521` | 摘要调用是「提前关流」的，采集点必须能处理生成器被提前关闭 |
| 一轮可能有多次调用：首次 + 至多 `ai_trade_decision_max_retries` 次重试（带 `_retry_hint_suffix`）+ 至多 1 次追问（带 `extra_instruction`）+ 反思（含重试）+ 摘要 | `decider.py:629-647`、`:714-753`、`:1089-1098`；`session.py` 的 `flush_summary` 调用 | 只留「最后一次」会丢掉重试与被拒收的追问，那正是偏好/强化学习最需要的负样本 |
| 会话末蒸馏经 `decider.llm` 出口复用**决策客户端** | `decider.py:448`、`experience/scheduler.py:153` | 按「哪个客户端」分类会把蒸馏误标成决策；必须按**工具名**分类 |

结论：**唯一**同时看得见「完整请求」与「完整流式输出」的位置，是每个 `LLMClient.stream(...)` 调用本身。
故采集点一是一个包在客户端外面的**透明装饰器** `RecordingLlmClient`（§2.2），而不是往决策器里再加读点。
这同时带来第二个好处：本特性**一行都不改 `decider.py`**（它是混合文件，且是交易热路径的核心）。

### 0.3 为什么不加关停步骤

`test_main_shutdown.py::test_shutdown_steps_are_zero_arg_factories_in_the_legacy_order`
（工作树 `:145-165`）断言关停步骤表的**完整有序列表**，而这张表在 HEAD 与工作树上**不一样**
（HEAD 14 步；工作树因兄弟特性插入 `ai_trade_journal` 为 15 步，`git grep -n ai_trade_journal HEAD --
services/quant/tests/test_main_shutdown.py` 计数为 0）。本特性再加一步，就会让这个冻结注册表在两个
版本上都要改、且改法互相冲突。

本仓已有成文先例：`manager.shutdown()` 的注释原文「**关停步骤表一步都不加**：收敛落在既有的
`ai_trade` 那一步内部」（`manager.py:2190`）。本特性的写线程同样在 `ai_trade` 步内部（`manager.shutdown()`
的末尾）有界关闭，并以 `atexit` 兜底（§2.8 约束 8）。

---

## 一、概述

### 1.1 现状

AI 自动交易今天是一套完整的 Agent 决策系统：每一轮从唤醒判定、行情摘要、信号盘、多周期上下文、
情报与经验注入，到 LLM 研判（含重试与深度追问）、十余道确定性门禁、下单前反思、执行与成交归因，
全链路在运行期都有 WS 轨迹可看。但**这些「思考 → 行动 → 结果」的完整轨迹没有一个字节以训练可用的
形态落在磁盘上**：思考文本从不在服务端累积，研判与反思被截断到 2 万字，重试与追问的中间调用只存在于
一次函数调用的局部变量里，而决定奖励的已实现盈亏又要到若干轮之后的平仓腿上才出现。

后训练（SFT / 偏好优化 / 强化学习）需要的恰好是这三样东西的**同时完整**：①模型当时真正看到的输入
（system + 多轮 messages + tools 定义 + 采样参数）；②模型的完整输出（思考、研判文字、工具调用名与
入参原文、收尾码、token 用量），包括失败、重试、被拒收的那些；③环境反馈（门禁是否改写了动作、执行
回执、成交腿、往返结算的净额盈亏 / bps / R 倍数 / 退出归因）。缺任何一样，数据集要么不能训练，要么
训练的是一段模型从未见过的输入。

### 1.2 本轮做什么（五条轴）

- **轴 A · 采集点（LLM 边界）**：新增 `RecordingLlmClient`，在 `manager._build_member` 造完三个 LLM
  客户端之后、交给决策器之前透明包装。它原样转发每一个流事件（对象身份不变），同时把**完整请求**与
  **完整流式响应**冻结成一条 `llm_call` 记录。只在用户勾选开关时包装。
- **轴 B · 回合断面（会话写点）**：会话在十二个同步写点（§2.3；v2 把 W3 拆为 W3a / W3b）把「观测（payload）/ 模型原始动作 /
  门禁降级码 / 反思裁决 / 最终动作 / 执行回执 / 本轮成交腿 / 本轮全部 LLM 调用的 id 与被采纳的那一次」
  写成一条 `round` 记录，并补齐跳过轮、失败轮、被取消轮、盯市期成交、暂停 / 继续 / 停止。
- **轴 C · 落盘（文件）**：每次启动（分组）一个目录；每个标的一串 JSONL 段文件；大文本按
  sha256 **内容寻址去重**（每段自洽）；段满轮转并 gzip 封口；`manifest.json` 原子覆写；**专用写线程**
  承担全部文件 IO（事件循环上零文件 IO）；字节预算、磁盘低水位、连续写失败三道自保护。
- **轴 D · 启动面板与运行态**：六步向导第 4 步「Agent 认知」新增「后训练数据采集」卡片——默认关闭、
  目录默认值由服务端下发、可手输或（桌面端）用系统对话框选择、失焦即校验（可写 / 可用空间 / 路径合法）；
  第 6 步复述去向；运行中在 AI 自动交易开关行下方显示一条采集状态（条数 / 体积 / 异常）。
- **轴 E · 数据集编译（离线）**：纯函数 + CLI，把原始段文件编译成 `steps.jsonl`（一轮一条，强化学习
  用：输入、完整输出、动作、执行、按往返结算回填的奖励、质量标记）、`calls.jsonl`（一次调用一条，
  SFT / 偏好用）与 `report.json`（完整性报告：缺口、撕裂行、缺失 blob、未收尾的回合）。奖励配对**复用**
  仓库既有的唯一真源 `TradeAccountingAccumulator`（`ai_auto/accounting_replay.py:116`），不另写配对规则。

### 1.3 明确的非目标

| # | 非目标 | 理由 |
|---|---|---|
| N1 | 回测车道（`ai_auto/backtest.py`）不采集 | 回测的成交是模拟撮合、决策深度独立取 `AI_TRADE_REASONING_DEPTH_BACKTEST`，混入同一数据集会污染奖励口径；记录格式已预留 `env` 字段，v2 可按 `env="backtest"` 加入而不改格式。启动面板在回测模式下卡片明示「回测不采集」 |
| N2 | 不采集情报 Agent / 核查 Agent 的调用 | 它们不是交易策略本身；它们的产物**进入交易 Agent 输入的那部分**已随 `llm_call.request.messages` 原文完整留存 |
| N3 | 不做训练、数据配比、评测 | 本轮只负责产出格式正确、完整、可追溯的数据 |
| N4 | 不上传、不云同步 | 数据离开本机必须是用户自己的动作 |
| N5 | 不捕获 Anthropic thinking 的 `signature` / `redacted_thinking`，不捕获 OpenAI 兼容端的 `reasoning_content` | `anthropic_client._on_block_delta` 只转出 `text_delta` / `thinking_delta` / `input_json_delta`（`agent/llm/anthropic_client.py:180-191`），`openai_client._dispatch_chunk` 不解析推理字段（`openai_client.py:133-150`）。改 provider 解析层会波及 Agent 对话助手全链路，另立特性。每条 `llm_call` 如实写出 `capabilities.thinkingCaptured` |
| N6 | 不改 `LLMClient` 协议签名 | 32 个测试文件实现了 `async def stream(self, *, system, messages, tools)` 的假客户端（`llm_budget.py:9-11` 成文） |
| N7 | 不做 DB 迁移、不改兄弟特性 | 见范围纪律 3、5 |

---

## 二、技术设计

### 2.1 分层与数据流

```
启动面板（第 4 步卡片）──POST /ai-trade/start {trajectoryCapture:{enabled,directory}}──┐
                                                                                      ▼
api/ai_auto.py：build_start_request → _resolve_trajectory（纯函数，不抛、不做 IO）
                ai_trade_start 端点 → _require_trajectory_directory（IO 探测，失败 422 + remedies）
                                                                                      ▼
manager._build_group ──open_group_recorder()──► TrajectoryRecorder（每分组一个，含一条写线程）
        │                                              ▲ 同步 offer（已序列化、零 await、绝不抛）
        ├─ _build_member：clients = wrap_llm_clients(clients, recorder.channel(instId))
        │        decider ── RecordingLlmClient.stream ── inner.stream（原样转发每个事件）
        │                        └─► channel.record_call(...)  ──────────────┐
        └─ member.session.attach_trajectory(recorder.channel(instId))       │
                 session 的 12 个同步写点 ──► channel.begin_round / end_round / … ┤
                                                                            ▼
                         queue.SimpleQueue + 字节预算（热路径）──► 写线程 SegmentWriter
                                                                            │ blob 去重 · 轮转 · gzip
                                                                            │ fsync 节流 · 磁盘水位
                                                                            ▼ 逐行剔除凭证
  <root>/<YYYYMMDD>-<groupId>/manifest.json
                             /members/<instId>/seg-e<epoch 序号>-<UTC>-<epoch8>-<nnnnn>.jsonl[.gz]
                                                                            │
                     离线：python -m app.ai_auto.trajectory.compile ─────────┘──► steps.jsonl / calls.jsonl / report.json
```

**贯穿全设计的原则**：采集层**只读取、绝不参与判定**。`ai_auto/trajectory/` 下任何符号都不得出现在
`session.py` / `manager.py` / `decider.py` 的**判定表达式**里（由 §6.1 的 G-T1 AST 门禁冻结）。

### 2.2 采集点一：`RecordingLlmClient`（`ai_auto/trajectory/llm_tap.py`）

#### 2.2.1 包装位置（唯一）

`manager._build_member`（工作树 `manager.py:853-858`）造完 `clients = build_ai_trade_llm_clients(...)`
之后、`decider = AiTradeDecider(clients.decision, …)` 之前，插入：

```python
        channel = ctx.trajectory.channel(plan.inst_id) if ctx.trajectory is not None else None
        if channel is not None:
            clients = wrap_llm_clients(clients, channel)
```

三条理由：

1. **不动测试注入缝**：20+ 个测试文件以 `monkeypatch.setattr("app.ai_auto.manager.make_llm_client_from_config", …)`
   注入假客户端（`manager.py:855-857` 的注释原文）。包装发生在工厂**之后**，注入缝逐字不变。
2. **不开即零改动**：`ctx.trajectory is None`（开关未勾 / 运维关 / 打开失败）⇒ 三个客户端对象与合入前
   是同一批对象。
3. **一处覆盖三条链**：`start_group` 与冷续跑都经 `_build_group → _build_member`（`resume.py:380` 调
   `manager._build_group`），热续跑复用内存里已包装的客户端。

`wrap_llm_clients` 用 `dataclasses.replace(clients, decision=…, reflection=…, summary=…)`
（`AiTradeLlmClients` 是 `@dataclass(frozen=True)`，`llm_budget.py:30-36`）。注意 off 档三者可能是
**三个不同对象**（`llm_budget.py:91-92` 三次调工厂），故逐个包装，不做 `is` 去重。

#### 2.2.2 `stream` 的形状（硬约束，AST 门禁 G-T2 冻结）

```python
class RecordingLlmClient:
    """透明装饰器：原样转发 inner 的每个流事件，同时把完整请求与完整响应冻结成一条 llm_call 记录。"""

    def __init__(self, inner, channel: "TrajectoryChannel") -> None:
        self._inner = inner
        self._channel = channel
        self.provider = getattr(inner, "provider", "anthropic")   # decider._note_stream_outcome 读它

    def __getattr__(self, name):          # 私有字段（_model 等）照常可读：decider/测试替身零感知
        # 【v2 · RV-P2-1】`copy` / `pickle` 会在 `__init__` 之前查属性：不守卫即无限递归。
        if name in ("_inner", "_channel") or name.startswith("__"):
            raise AttributeError(name)
        return getattr(self._inner, name)

    async def stream(self, *, system, messages, tools):
        capture = self._channel.open_call(system=system, messages=messages, tools=tools,
                                          params=llm_params_of(self._inner, tools))  # 同步、绝不抛、可返回 None
        status = "closed_early"
        try:
            async for ev in self._inner.stream(system=system, messages=messages, tools=tools):
                if capture is not None:
                    capture.observe(ev)                                        # 同步、绝不抛
                yield ev
            status = "completed"
        except asyncio.CancelledError:
            status = "cancelled"
            raise
        except GeneratorExit:
            status = "closed_early"
            raise
        except BaseException as exc:
            status = "exception"
            if capture is not None:
                capture.note_exception(exc)                                    # 同步、绝不抛
            raise
        finally:
            if capture is not None:
                capture.finish(status)                                         # 同步、绝不抛、幂等
```

五条硬约束，各自有一条可证伪的理由：

1. **函数体内恰好一个 `async for`、零个 `await`、恰好一个 `yield`**。多一个 await 就是多一个
   `CancelledError` 落点，而决策调用跑在 `asyncio.wait_for` 的取消域里（`decider.py:797-799`）。
2. **`yield ev` 转发的是同一个对象**（不拷贝、不改字段）。决策器按 `ev.kind` / `ev.id` 分派，任何改写
   都会改变交易行为。
3. **每个 `except` 分支都以 `raise` 结尾**。包装器只观察结局，从不改变结局。
4. **`finally` 里不 await**。摘要消费方见到 `message_done` 即 `break`（`decider.py:1517-1518`），此时
   生成器由 asyncio 的 asyncgen 终结钩子异步 `aclose()`，`GeneratorExit` 抛在 `yield` 处——`finally`
   里任何 await 都可能在循环关闭阶段留下「Task was destroyed but it is pending」。内层生成器的关闭
   时机与合入前相同（同样由 GC 终结），故行为透明。
5. **`error` 事件即终局**：`capture.observe` 见到 `kind == "error"` 时**立刻** `finish("error")` 并入队，
   不等生成器结束。理由：`ai_auto` 内所有消费方（`_stream_decision` :911-913、`_stream_reflection`
   :1201-1203、`_stream_summary` :1515-1516、情报 `_consume`、蒸馏）见到 error 都立刻抛出，生成器随后
   只能等 GC 终结；先入队使这条记录的落盘时机确定。之后到达 `finish(...)` 的调用幂等空转。

#### 2.2.3 调用点分类：按**工具名**，不按客户端

```python
def classify_site(tools) -> str:
    names = {getattr(t, "name", "") for t in (tools or [])}
    if not names:                         return "summary"            # _stream_summary: tools=[]（decider.py:1512）
    if DECISION_TOOL_NAME in names:       return "decision"           # "submit_trade_decision"（prompts.py:31）
    if REFLECTION_TOOL_NAME in names:     return "reflection"         # "submit_trade_reflection"（prompts.py:1074）
    if DISTILL_TOOL_NAME in names:        return "experience_distill" # "submit_trade_lessons"（experience/distill.py:32）
    return "other"
```

三个常量**从真源 import**（`prompts.DECISION_TOOL_NAME` / `prompts.REFLECTION_TOOL_NAME` /
`experience.distill.DISTILL_TOOL_NAME`），在函数体内**延迟导入**并以模块级缓存保存，避免
`trajectory → experience → …` 的导入环。只有 `decision` / `reflection` / `summary` 三类会挂到当前回合
（§2.3）；`other` 记为 `scope.phase = "out_of_round"`。

**【v2 · RV6】`experience_distill` 在本轮不采集。** 蒸馏跑在 `stop_group` **之后**的后台任务里
（`experience/scheduler.finalize` → `run_bounded(session.decider.llm, …)`，`scheduler.py:128-153`），那一刻 episode
已关、recorder 多半已关，「记为 out_of_round」的承诺落不了盘。故 `open_call` 在两种情形**返回 `None`**（包装器随即
纯直通）：① `site == "experience_distill"`；② 该通道的 episode 已 `close_episode`，或 recorder 已停止受理。两种情形
都只做 `counters.lateCalls += 1`，**不分配 seq、不计 `dropped`、不写 `gap`**——晚到的调用不是缺口，把它记成缺口会在
状态条与编译报告里凭空造出一段「数据丢失」（用例 S-12）。

#### 2.2.4 请求的冻结（在 `open_call` 里同步完成）

- `system`：原串。
- `messages`：逐条经 `message_wire(msg)` 归一化为 Anthropic 风格 dict，再逐条
  `canonical_json(...)` 成字符串（**逐条**序列化，使写线程能按条去重）：
  - `TextBlock` → `{"type":"text","text":…}`；
  - `ToolUseBlock` → `{"type":"tool_use","id":…,"name":…,"input":…}`；
  - `ToolResultBlock` → `{"type":"tool_result","tool_use_id":…,"content":…,"is_error":…}`；
  - 未知块 → `dataclasses.asdict(b)` 若为 dataclass，否则 `{"type":"unknown","repr":repr(b)}`；
  - 消息外层 `{"role": msg.role, "content": [...]}`，**`role` 保留归一化值 `tool`**（不翻译成 provider
    的 user 轮，翻译规则由编译器在导出时声明，§2.11）。
- `tools`：`[{"name","description","input_schema"}]` 的 `canonical_json`。
- `params`：`llm_params_of(inner, tools)`。**【v2 · RV11】以 provider 自己的请求体装配为唯一真源**：
  1. `inner` 有 `_body` ⇒ `body = inner._body("", [], tools)`，记
     `requestParams = {k: v for k, v in body.items() if k not in ("system", "messages", "tools")}`、
     `paramsSource = "provider_body"`。理由：真正发出的采样参数**不等于**私有字段——Anthropic 开扩展思考时
     `_body` 强制 `temperature=1.0` 并删除 `top_p`（`anthropic_client.py:70-73`），OpenAI 端另带 `tool_choice` /
     `stream_options`（`openai_client.py:54-70`）；强化学习的重要性采样与离策略校正依赖行为策略的**真实**温度，
     逐字段读会静默记错。空 `system` / `messages` 使这次装配为常数开销；两个 provider 的 `_body` 实读均为纯函数。
  2. 无 `_body`（测试替身）或它抛异常 ⇒ 回落逐项 `getattr`，缺席即 `None`：`model`（`_model` → `model`）、
     `max_tokens`（`_max_tokens`）、`temperature`（`_temperature`）、`top_p`（`_top_p`），`paramsSource = "attributes"`。
  3. 另记两个派生项：`provider`（类属性，`anthropic_client.py:28` / `openai_client.py:28`）与
     `thinkingBudgetTokens`（`_thinking_budget`，`anthropic_client.py:49`；缺席即 `0`）。
  **绝不读取 `_api_key` 与 `_base_url` 进记录**（`_body` 不含二者，它们只在 `_headers()` 与 URL 里；`_api_key` 只登记
  进写线程的剔除表，§2.8 约束 9）。用例 T-10 以「开思考的 `AnthropicClient`」断言记录的 `temperature == 1.0` 且无 `top_p`。
- `canonical_json(obj) = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)`。

冻结在入队时完成的理由与兄弟特性同一条：`_context` 里的对象会被后续轮次引用，存引用 = 落盘时写下的
可能是之后的状态。冻结成本 O(上下文字节)，每次调用一次，量级毫秒；`serializeMs` 计入回执（§2.9），
让代价可见而不是靠猜。

#### 2.2.5 响应的累积（`capture.observe`）

| 事件 | 动作 |
|---|---|
| 第一个任意事件 | 记 `firstEventMs = (monotonic() - t0) * 1000` |
| `thinking_delta` | `thinking_parts.append(ev.text or "")` |
| `text_delta` | `text_parts.append(ev.text or "")` |
| `tool_use_start` | 新建 `{id, name, parts: []}`，按出现顺序入列表；`last_tool_id = ev.id` |
| `tool_use_args_delta` | `ev.id` 命中已开工具 ⇒ 追加到它；`ev.id is None` ⇒ 追加到 `last_tool_id` 并 `argsDeltaWithoutId += 1`（OpenAI 流式里后续分片只带 index，见 §七 R7） |
| `tool_use_end` | 记 `ended=True` |
| `message_done` | `stopReason = ev.stop_reason or stopReason`；`usage = ev.usage or usage`（与 `decider.py:890-891` 同一「非空者胜」口径）；**【v2 · RV-P2-7】`sawMessageDone = True`** |
| `error` | 记 `error={code,message}`，**立即** `finish("error")` |
| 任意 | `eventCounts[kind] += 1` |

`finish(status)` 装配：`thinking = "".join(...)`、`text = "".join(...)`（**不 strip、不截断**）；每个工具
`argumentsJson = "".join(parts)`，`input = json.loads(argumentsJson or "{}")` 成功且为 dict 时填，否则
`input = None` 且 `parseError = str(exc)[:500]`；`normalizedUsage = normalize_usage(provider, usage)`
（`agent/llm/retry.normalize_usage`，与 `decider.py:821` 同一函数）；`durationMs`；最终 `status`：
error 事件优先，否则取 `finish` 的实参。**【v2 · RV-P2-7】** 另记
`responseComplete = status == "completed" or (status == "closed_early" and sawMessageDone)`——摘要调用见
`message_done` 即被消费方 `break`（`decider.py:1517-1518`），它的 `closed_early` 是一次完整响应而不是半截。

### 2.3 采集点二：会话侧的十二个同步写点（v2 · W3 拆为 W3a / W3b）

会话持有一个通道对象 `self._trajectory`。**构造签名一个字都不改**：`__init__` 末尾新增一行
`self._trajectory = NULL_TRAJECTORY`（空对象，全部方法空转），真正的通道由 `manager._build_group`
在既有的「会话侧接线」循环里挂上（与 `member.session.group_id = …` / `on_stopped = …` 同一个循环，
`manager.py:805-815`）：

```python
            member.session.attach_trajectory(group.trajectory.channel(member.inst_id)
                                             if group.trajectory is not None else None)
```

`attach_trajectory(channel)`：`channel is None` ⇒ 保持空对象；否则 `self._trajectory = channel` 并
`channel.bind(SessionBinding(session_id, run_id, inst_id, bar), executor=self.executor)`。另加只读属性
`trajectory`（返回 `self._trajectory`），供 manager 的暂停 / 继续写点使用。

**空对象模式的理由**：十二个写点都不需要 `if self._trajectory is not None`，会话侧 diff 最小，且不存在
「漏判一个 None 就在真实资金热路径上抛 AttributeError」的形态。

| # | 位置（以锚点语句为准） | 调用 | 为什么必须在这里 |
|---|---|---|---|
| W1 | `start()`：`await self._prime()` 之后、`self._link = make_session_link(...)` 之前 | `self._trajectory.open_episode(self._trajectory_episode_facts())` | 暖机不下单；冷续跑的 `_adopt_group` 在 `session.start()` **之前**已回灌成交台账（`resume.py:340-342`），此刻的 `executor.trade_count` 才是「已存在成交」的正确水位 |
| W2 | `_run_decision_round`：`round_no = self.rounds_completed + 1` 之后、`try:` 之前 | `self._trajectory.begin_round(round_no, ts, wake)` | 本轮每一次 LLM 调用（重试 / 追问 / 反思 / 摘要）都要登记到本轮；放进 `try` 里则 `_maybe_skip_round` 之前的调用登记不到 |
| W3a【v2 · RV4】 | 同方法 `try` 内，`reflection_verdict, reflection_reason = self._round_reflection_summary()`（`session.py:2451`）**紧后一行** | `self._trajectory.stage_round(RoundCapture(...))`（字段见 §2.4） | 该行同步，其后才是 `_emit_decision` / `persist_pending` / `flush_summary` / `_post_round` 这串 await。此刻**成交已经发生**：断面必须先暂存，否则这段窗口里的暂停 / 停止（`session.pause` 第 ③ 步 cancel 决策任务，`session.py:1554`）或异常会让一笔真实成交失去它的观测与动作 |
| W3b | 同方法 `try` 内，`await self._post_round(ref_px, ts)` **紧后一行** | `self._trajectory.end_round(summary_installed=summary is not None)` | 必须晚于 `flush_summary`（摘要调用属于本轮）与 `_post_round`（利润保护会在其中**再成交一笔**，那笔不在 `executed` 里）；把暂存断面补上成交腿与摘要位后以 `completion:"full"` 写出 |
| W4 | 同方法 `except Exception as exc:` 分支，既有 `logger.warning(...)` 之后 | `self._trajectory.fail_round(round_no, ts, exc)` | 作用域 `open`（失败早于暂存，可能 `payload` 都未绑定）⇒ 只写 `round_failed` 与已发生的调用 id；作用域 `staged`（成交之后才抛）⇒ **【v2 · RV4】** 写 `round{completion:"partial", abortReason:"exception"}`，成交腿照常按水位取 |
| W5 | 同方法 `finally:` 首行 | `self._trajectory.release_round(round_no)` | 只处理仍为 `open` / `staged` 的作用域（`CancelledError`：暂停 / 停止打断在飞轮）：`open` ⇒ `round_aborted`；`staged` ⇒ `round{completion:"partial", abortReason:"cancelled"}`；`closed` ⇒ 空转（W3b / W4 / W6 已写过，§2.3.2） |
| W6 | `_maybe_skip_round`：`self.rounds_skipped += 1` 与既有 `round_skipped` WS 推送之后、`return True` 之前 | `self._trajectory.skip_round(round_no, ts, {"reason":"horizon_locked","minHoldRemainingSeconds":…,"wakeKind":…})` | 与 WS 事件同一组键、同一个数，不给第二个真源；**【v2 · RV5】写完即把作用域置 `closed`**——`_maybe_skip_round` 随后 `return`，`finally` 里的 W5 因此空转，否则每个跳过轮会多出一条自相矛盾的 `round_aborted` |
| W7 | `_monitor_tick`：`await self._maybe_session_exit(mark, now_ts)` 紧后一行 | `self._trajectory.sync_fills("monitor")` | 盯市期的成交（利润保护、会话止盈止损、离场意图兑现、外部平仓回执）在这之前全部产生 |
| W8 | `stop()`：`self._intel_cite.stop()` 紧后一行 | `self._trajectory.sync_fills("stop")` 然后 `self._trajectory.close_episode(self._trajectory_episode_end())` | 平仓回执、结算 drain、成交落库都已发生；在 `_finalize_run` 之前，保证 `episode_end` 先于任何可能抛出的收尾 IO 入队 |
| W9 | `manager.pause_group`：`receipts = [await member.session.pause(...) …]` 之后 | `for member in group.members: member.session.trajectory.note_paused(reason, flatten)` | `session.pause` 可能撤单 / 平仓产生成交；集中在 manager 写，liveness 自动暂停与 `shutdown()` 的 hold 分支都经 `pause_group` |
| W10 | `manager.resume_group` 热续跑分支：`intel_scheduler.resume(group_id)` 之后、`return group` 之前 | `for member in group.members: member.session.trajectory.note_resumed("hot")` | 冷续跑是新 epoch，由 W1 写 `episode_start{resumeOf}`，此处不写 |
| W11 | `manager._build_group`：`ctx = _GroupBuildCtx(...)` 之后、`members = [...]` 之前 | `ctx.trajectory = await self._open_trajectory(req, ctx, creds)` | 通道要在 `_build_member` 包装客户端之前存在（§2.2.1）；**【v2 · RV10】** 新启动打开失败即在此抛 `TrajectoryOpenError`——此刻尚未建任何 run 行，抛出零残留（§2.10.4） |

**十二个写点（v2 把 W3 拆成 W3a / W3b）的统一约束**（与 §2.2.2 同级，G-T3 AST 门禁冻结）：

- 会话侧写点全部是**同步**调用：`session.py` 全文不得出现 `await self._trajectory`；
- 通道的每个公开方法函数体顶层**恰好一个** `try/except Exception`，`except` 只做
  `recorder.note_serialize_error(exc)`；
- 通道方法不做文件 IO、不取锁等待（字节预算锁是非阻塞的，§2.8 约束 1）。

#### 2.3.1 `_trajectory_episode_facts()` 与 `_trajectory_episode_end()`

两个会话内私有薄方法（各 ≤ 20 行），只取既有属性、不做计算：

```python
EpisodeFacts(session_id=self.session_id, run_id=self.run_id, inst_id=self.inst_id, bar=self.bar,
             strategy_ids=list(self.strategy_ids), strategy_names=list(self.strategy_names),
             context_refresh_rounds=self.context_refresh_rounds, wake_interval_s=self.wake_interval_s,
             take_profit_usd=self.take_profit_usd, stop_loss_usd=self.stop_loss_usd,
             started_ts=int(self.started_ts or 0), accounting=accounting_context_of(self))
EpisodeEnd(stop_reason=self.stop_reason, trade_count=self.executor.trade_count,
           realized_pnl_usd=self.executor.realized_pnl_usd, flatten_result=self.flatten_result)
```

**【v2 · RV13】`accounting_context_of(session)`**（`records.py`，纯读、绝不抛）经 getattr 链取会话成本层那一份
`TradeAccountingAccumulator` 的 `context` **拷贝**（持有者见 `attribution.py:175-176`，形如
`{"costBasis": "net"|"gross", "ctVal": …}`；**取原对象、不另算**）；取不到 ⇒ `None`，编译器计
`report.accountingContextMissing`。它是编译期重放奖励的会计口径唯一来源（§2.11.5）。

#### 2.3.2 回合作用域状态机与 episode 关闭后的直通（【v2 · RV4 / RV5 / RV6】）

每个通道同一时刻至多一个回合作用域，`RoundScope.state` 只有三个值，**每个写点的迁移唯一**：

| 写点 | 前置状态 | 动作 | 后置状态 |
|---|---|---|---|
| W2 `begin_round` | 无 / `closed` | 建作用域（上一个若仍 `open` / `staged`，先按 W5 规则写出——防御「上一轮的 finally 没跑到」） | `open` |
| `open_call`（decision / reflection / summary） | `open` / `staged` | 登记 `{callId, site, callIndex}` | 不变 |
| W6 `skip_round` | `open` | 写 `round_skipped` | `closed` |
| W3a `stage_round` | `open` | 暂存断面（零序列化、零入队） | `staged` |
| W3b `end_round` | `staged` | 写 `round{completion:"full"}` | `closed` |
| W4 `fail_round` | `open` / `staged` | `open` ⇒ `round_failed`；`staged` ⇒ `round{completion:"partial", abortReason:"exception"}` | `closed` |
| W5 `release_round` | 任意 | `open` ⇒ `round_aborted`；`staged` ⇒ `round{completion:"partial", abortReason:"cancelled"}`；`closed` ⇒ 空转 | `closed` |

**episode 关闭之后**（W8 `close_episode` 已执行，或 recorder 已 `close_nowait` / `failed`）：通道全部写点与 `open_call`
一律直通空转，只由 `open_call` 计 `lateCalls`，**不分配 seq、不计 `dropped`、不写 `gap`**。真实来源有三：
`_maybe_session_exit` 在盯市 tick 内触发 `stop()` 之后 W7 仍会执行；`stop()` 只 cancel 不 await 决策任务
（`session.py:1335-1338`），其 `finally`（W5）晚于 `episode_end` 到达；会话末蒸馏（§2.2.3）。

### 2.4 回合断面：`RoundCapture` 与 `round` 记录

```python
@dataclass(frozen=True, slots=True)
class RoundCapture:                         # 只承载引用，不做任何计算（CLAUDE.md §二：禁止位置堆参）
    round_no: int
    ts: int
    wake: dict
    payload: dict
    stage1_decision: object                 # TradeDecision（任何门禁之前，session.py 的 stage1_decision）
    decision: object                        # TradeDecision（门禁 + 反思 + 仓位求解之后，真正执行的）
    executed: dict
    llm_degrade: str | None
    gate_degrade: str | None
    degrade: str | None
    reflection_verdict: str | None
    reflection_reason: str | None
    reasoning: object | None                # decider.last_reasoning（ReasoningMeta | None）
    elapsed_ms: float | None                # self._reasoning_elapsed_ms
```

【v2 · RV4】`summary_installed` 移出冻结断面：暂存（W3a）时摘要还没跑，它改由 W3b 的实参给出。

W3a / W3b 的调用逐字为（【v2 · RV4】）：

```python
            reflection_verdict, reflection_reason = self._round_reflection_summary()
            self._trajectory.stage_round(RoundCapture(              # W3a：同步，紧跟上一行
                round_no=round_no, ts=ts, wake=wake, payload=payload,
                stage1_decision=stage1_decision, decision=decision, executed=executed,
                llm_degrade=llm_degrade, gate_degrade=gate_degrade, degrade=degrade,
                reflection_verdict=reflection_verdict, reflection_reason=reflection_reason,
                reasoning=self.decider.last_reasoning, elapsed_ms=self._reasoning_elapsed_ms))
            …                                                         # 既有的推送 / 摘要，一行不改
            await self._post_round(ref_px, ts)
            self._trajectory.end_round(summary_installed=summary is not None)   # W3b
```

W3a 取值时刻的正确性（逐项实读）：`degrade` 在 `session.py:2403` 已并入 `closeBlocked` 且此后不再改写；`decision`
在 `:2400` 之后不再改写；`last_reasoning` 已被反思 `replace` 过（`decider.py:1114`），是本轮最终那一份。

以上每个名字都是 `_run_decision_round` 里**已经存在**的局部变量（`stage1_decision` `:2312`、
`llm_degrade` `:2297`、`gate_degrade` `:2385`、`executed` `:2400`、`reflection_verdict` `:2451`、
`summary` `:2470`），本特性不新增任何局部变量。

`stage_round(cap)` 只把 `cap` 存进作用域并置 `staged`。`end_round(summary_installed)`（W3b）、`fail_round`（W4）、
`release_round`（W5）在 `staged` 作用域上**共用下面同一段装配**，只差
`closure = {completion, abortReason, error, summaryInstalled}`——绝不写两份装配（通道，纯同步）：

1. `fills = self._take_new_fills()`（§2.6）；
2. `record = build_round_record(scope, cap, fills, closure)`（`records.py` 纯函数）：
   - `observation`：`payload` 原样；
   - `decision.model = decision_wire(stage1_decision)`、`decision.final = decision_wire(decision)`，
     `decision_wire = dataclasses.asdict`（`TradeDecision` 是 `@dataclass`，`decider.py:238`）——字段
     增删自动跟随，不存在逐键手抄的漂移面；
   - `degrade = {"llm": llm_degrade, "gate": gate_degrade, "final": degrade,
     "vetoLayer": open_funnel.classify_block(gate_degrade)}`（`classify_block` 在 HEAD 已存在，
     `open_funnel.py:133`；取 `gate_degrade` 而非合成后的 `degrade`，理由与 `session.py` 里
     directional-symmetry B-2 那段注释同一条：`exit_order_slot_busy` 不是门禁）；
   - `reflection = {"verdict", "reason"}`；`reasoning = cap.reasoning.wire() if cap.reasoning else None`
     （`ReasoningMeta.wire`，`decider.py:228`）；
   - `executed`、`fills`、`elapsedMs`、`summaryInstalled`、`trigger = wake.get("trigger")`、`wake`；
   - `calls = [{"callId","site","callIndex"}...]`（本轮登记的全部调用，按发生序）；
   - `adoptedDecisionCallId`（§2.5）。
3. `offer(canonical_json(envelope + record))`；关闭回合作用域。

### 2.5 「被采纳的那一次决策调用」的判定

决策器的控制流是确定的（`decider.py:625-647`）：先 `_attempt_decision` 一次，失败且可重试时再重试至多
`max_retries` 次，最后至多一次 `_maybe_reask`（`:742-753`）。故本轮 `site == "decision"` 的调用序列恒为
`[首次, 重试…, 追问?]`，被采纳者由 `decider.last_reasoning` 唯一确定：

| `reasoning` | 被采纳的调用 |
|---|---|
| `None`（深度档未生效，追问不可能发生，`_wants_reask` 首行 `if not self._depth_on`，`:706`） | 最后一次决策调用 |
| `reasked == False` | 最后一次决策调用 |
| `reasked == True` 且 `reask_adopted == True` | 最后一次（即追问那次） |
| `reasked == True` 且 `reask_adopted == False` | 倒数第二次（追问被拒收，`:749-753` 回滚到上一次的研判与决策） |
| 本轮没有任何决策调用 | `null` |

纯函数 `adopted_decision_call_id(calls, reasoning) -> str | None` 实现上表，由用例 T-9 逐行冻结。
被采纳调用若自身 `status != "completed"`（例如超时后降级 hold），`round.degrade.llm` 非空，编译器据此
在 `quality.degraded` 上如实标注——**不替系统合成的 hold 冒充模型输出**。

### 2.6 成交腿的水位（`sync_fills` / `_take_new_fills`）

**判据必须是 `executor.trade_count`，不能是 `len(executor.trades)`**：

- `executor.trades` 是会被**按块裁剪**的列表：`len(self.trades) > TRADE_LEDGER_MAX + _LEDGER_TRIM_SLACK`
  时 `del self.trades[:len - TRADE_LEDGER_MAX]`（`live_auto/executor.py:1729-1730`；`TRADE_LEDGER_MAX = 20000`，
  `wire_caps.py:38`；`_LEDGER_TRIM_SLACK = 500`，`executor.py:88`）。按下标取水位，裁剪那一刻会重复或漏记。
- `executor.trade_count` 单调递增、不受裁剪影响（`executor.py:200`、`:1726`）；续跑时由
  `restore_trades` 就地派生为 `len(rows)`（`:1754`）。

算法（通道私有，纯同步）：

```python
def _take_new_fills(self) -> list[dict]:
    count = int(self._executor.trade_count)
    new = count - self._ledger_seen
    if new <= 0:
        return []
    ledger = self._executor.trades
    available = min(new, len(ledger))
    first_seq = count - available + 1                    # 1-based：本 run 的第几笔成交
    fills = [{**deepcopy(t), "ledgerSeq": first_seq + k} for k, t in enumerate(ledger[-available:])]
    if available < new:                                 # 两次同步之间成交数超过台账保留量（理论上不会发生）
        self._note_ledger_gap(self._ledger_seen + 1, count - available)
    self._ledger_seen = count
    return fills
```

- **`open_episode` 把 `_ledger_seen` 置为当时的 `trade_count`**（W1），故暖机前已存在的历史成交不会被当成
  「本 epoch 新成交」。
- **冷续跑的离线补记**：若 `recorder.previous_manifest.members[instId].ledgerSeqCaptured`（上一个进程
  写下的已采水位）存在且 `< trade_count`，`open_episode` 先写一条 `fills{context:"offline_catch_up"}`，
  内容为 `ledgerSeq ∈ (captured, trade_count]` 的那几笔（取不到的部分记 `gap`）。这覆盖了
  `settle_closed_offline`（`resume.py:398-403`）在离线期补记的平仓腿——没有它，离线平掉的那一笔往返在
  数据集里永远结算不了。
- 承载成交腿的记录（`round` / `fills{context}`）**只表示「在何时被采到」，不表示「由谁产生」**：盯市 tick
  与决策轮是两个任务，在 await 点上交错。归因一律按成交自身字段（`reason` / `explain.meta.exitSource` /
  `ledgerSeq`），编译器不看承载记录的类型（§2.11）。
- **【v2 · RV12】`ledgerSeq` 只在同一 epoch 内单调唯一，跨 epoch 不是身份。** 冷续跑 `restore_trades` 令
  `trade_count = len(rows)`（DB 行数，`executor.py:1754`），而「成交落库失败、仅留内存」的路径存在
  （`_trade_persist_failed` 一族）⇒ 重启后同一个 seq 可能指向**另一笔**成交。成交身份一律用仓库唯一真源
  `accounting_event_key(trade, runId)`（`accounting_replay.py:84-93`；实时成交恒带 `explain.meta.accountingEventId`，
  `session.py:827`）；`ledgerSeq` 只作同 epoch 内的排序与缺口检测。离线补记可能与上一 epoch 已采的成交重叠——
  **采集侧不做跨 epoch 去重**，由编译器按事件键去重（§2.11.5）。

### 2.7 落盘格式

#### 2.7.1 目录布局

```
<root>/                                   # 用户所选；缺省 = <数据库文件所在目录>/datasets/ai-trade
  DATASET_CARD.md                         # 首次写入（已存在则不覆盖）：格式版本、记录种类、编译命令、隐私说明
  <YYYYMMDD>-<groupId>/                   # 一次启动一个目录；日期取分组 created_ts 的 UTC 日期；冷续跑复用
    manifest.json                         # 原子覆写（.tmp + os.replace）
    members/<instId>/
      seg-e<epoch 序号 4 位>-<YYYYMMDDTHHMMSSZ>-<epoch8>-<nnnnn>.jsonl       # 活动段（明文，逐行追加）
      seg-e<epoch 序号 4 位>-<YYYYMMDDTHHMMSSZ>-<epoch8>-<nnnnn>.jsonl.gz    # 已封口段（I-29）
```

- `groupId` 形如 `aitradegrp_<32hex>`（`manager.py:778`），`instId` 形如 `BTC-USDT-SWAP`；两者进路径前一律
  过 `safe_component(s) = re.sub(r"[^A-Za-z0-9._-]", "_", s)[:96]`。
- `created_ts` 在冷续跑时取 `resume_of.createdTs`（`manager.py:768-769`），故同一分组的全部 epoch 落在
  同一个目录。
- 段名里的 UTC 时刻取写线程**打开该段**的墙钟，`epoch8` 取本次 recorder 打开时生成的 `uuid4().hex[:8]`，
  `nnnnn` 为本 epoch 内的段序号（从 `00001` 起）——文件名按字典序即时间序。
- **每次打开 recorder 都从一个新段开始，绝不向既有段文件追加**：上一个进程若被硬杀，最后一段可能有一行
  被撕裂；不追加即不会把新数据接在一行半截的 JSON 后面。

#### 2.7.2 记录信封与种类

每行一个 JSON 对象，公共信封：

```jsonc
{"v": 1, "kind": "…", "epoch": "<uuid4 hex>", "seq": 17, "wallTs": 1788000912.123,
 "groupId": "aitradegrp_…", "instId": "BTC-USDT-SWAP", "runId": 1873}
```

`seq` 在热路径入队时由通道分配，**每个 (标的, epoch) 严格 +1**；被丢弃的记录照样消耗序号，故缺口可被
机械地检出（§2.8 约束 2）。

| kind | 写入方 | 关键字段 |
|---|---|---|
| `segment_open` | 写线程 | `segment`, `prevSegment`, `openedTs` |
| `episode_start` | W1 | `sessionId`, `bar`, `env`（`live`/`demo`，【v2 · RV14】）, `accounting`（`{costBasis, ctVal}` 或 `null`，【v2 · RV13】）, `strategyIds`, `strategyNames`, `contextRefreshRounds`, `wakeIntervalS`, `takeProfitUsd`, `stopLossUsd`, `startedTs`, `ledgerSeqAtOpen`, `resumeOf`（冷续跑时 `{groupId, createdTs, resumedCount}`，否则 `null`）, `catchUp`（`none`/`written`/`unavailable`） |
| `blob` | 写线程 | `sha256`, `type`（`system`/`message`/`tools`）, `body`（原 JSON 值） |
| `llm_call` | `RecordingLlmClient` | `callId`, `site`, `scope{phase, roundNo, callIndex}`, `request{system:{"$blob"}, messages:[{"$blob"}…], tools:{"$blob"}, messageCount}`, `params{requestParams, paramsSource, provider, thinkingBudgetTokens}`（【v2 · RV11】）, `response{thinking, text, toolUses[{id,name,argumentsJson,input,parseError}], stopReason, usage, normalizedUsage, sawMessageDone}`, `timing{startedWallTs, firstEventMs, durationMs, serializeMs}`, `status`, `responseComplete`, `error`, `eventCounts`, `argsDeltaWithoutId`, `capabilities{thinkingCaptured, thinkingEnabled}`（【v2 · RV-P2-7 / 8】） |
| `round` | W3b / W4 / W5（【v2 · RV4】） | 见 §2.4；另有 `completion`（`full` / `partial`）、`abortReason`（`exception` / `cancelled` / `null`）、`error` |
| `round_skipped` | W6 | `roundNo`, `ts`, `reason`, `minHoldRemainingSeconds`, `wakeKind` |
| `round_failed` | W4 | `roundNo`, `ts`, `errorType`, `message`（`str(exc)[:2000]`）, `calls` |
| `round_aborted` | W5 | `roundNo`, `calls`, `reason:"not_completed"` |
| `fills` | W7/W8/W9/离线补记 | `context`（`monitor`/`stop`/`pause`/`offline_catch_up`）, `fills[]`（成交 dict 原样 + `ledgerSeq`） |
| `lifecycle` | W9/W10 | `event`（`pause`/`resume`）, `reason`, `flatten`, `path` |
| `episode_end` | W8 | `stopReason`, `tradeCount`, `realizedPnlUsd`, `flattenResult` |
| `gap` | 写线程 | `fromSeq`, `toSeq`, `count`, `reason`（`queue_full`/`disk_low`/`write_failed`/`after_failure`/`ledger_trimmed`） |
| `segment_close` | 写线程 | `segment`, `records`, `bytes`, `reason`（`rotate`/`close`）, `nextSegment` |

`thinkingCaptured = (provider == "anthropic")`：只有 Anthropic 适配层会转出 `thinking_delta`（§1.3 N5）。
编译器把它原样带进每条样本，让下游知道「思考为空」是「模型没想」还是「这条链路采不到」。
【v2 · RV-P2-8】另记 `thinkingEnabled = params.thinkingBudgetTokens > 0`：`agent_thinking_budget_tokens` 默认 0，即默认
不开扩展思考——此时模型的思维链**全部在研判正文 `text` 里**（深度思维链协议的分阶段研判），本特性对它逐字节完整留存。

#### 2.7.3 内容寻址 blob 与「每段自洽」

`llm_call` 每次都会携带整段上下文；照原样写，单轮 2–3 次调用 × 数十条消息会让数据按轮数平方增长。
写线程因此把 `system` / 每条 `message` / `tools` 各自按 `sha256(utf-8 字节)` 去重：

1. 写一条 `llm_call` 前，对它引用的每个 blob：若当前段的已写集合里没有该 sha，先写一行 `blob`；
2. `llm_call` 行里 `request` 只放 `{"$blob": "<sha>"}` 引用；
3. **已写集合在每次轮转时清空** ⇒ 任意一个段文件单独解压都能完整解析（用例 R-1 冻结）；
4. blob 行与引用它的 `llm_call` 行**在同一次处理里连续写出**，轮转只发生在两条队列项之间。

量级（按兄弟特性 §2.3 对 payload 的实测量级 4–8 KB 推算，用于定默认值而非承诺）：去重后每轮新增的是
「本轮新 user 提示词 + 模型输出 + round 断面」，约 15–40 KB 明文；gzip 后通常压到 1/5 以下。
15m K 线 + 2 分钟唤醒约 670 轮 / 天 / 标的 ⇒ 明文约 10–27 MB / 天 / 标的。回执实时显示
`bytesWritten` 与可用空间，不让用户靠猜。

#### 2.7.4 `manifest.json`

```jsonc
{
  "schemaVersion": 1, "kind": "ai_trade_trajectory_group",
  "groupId": "aitradegrp_…", "createdTs": 1788000000, "bar": "15m", "env": "live",
  "launch": { /* start_wire 原文（启动请求体，本就不含凭证，api/ai_auto.py:629-631 的注释原文），再过一次凭证剔除 */ },
  "members": {
    "BTC-USDT-SWAP": {"runId": 1873, "ledgerSeqCaptured": 42, "records": 1234,
                      "segments": ["seg-e0001-20260910T061200Z-1a2b3c4d-00001.jsonl.gz", "…"]}
  },
  "epochs": [{"epoch": "…", "openedTs": 1788000001, "closedTs": null, "openReason": "start|cold_resume",
              "closeReason": null}],
  "counters": {"records": 0, "llmCalls": 0, "rounds": 0, "bytesWritten": 0, "dropped": 0,
               "writeErrors": 0, "redactions": 0, "lateCalls": 0, "manifestWriteErrors": 0},  // v2 +2
  "state": "recording", "updatedTs": 1788000912
}
```

写时机：打开时、每次轮转、`note_paused` 触发的 flush、关闭时，以及记录中每 60 秒最多一次。一律写
`manifest.json.tmp` 后 `os.replace`（同卷原子替换）。读失败（撕裂 / 不存在）⇒ 视为无上一份（续跑的离线补记
写 `catchUp:"unavailable"`），**绝不因为读不到 manifest 而拒绝打开**。

### 2.8 写入器：`TrajectoryRecorder` + `SegmentWriter` 写线程

一个分组一个 recorder、一条写线程（线程名 `ai-trade-trajectory-<groupId 前 12 位>`，`daemon=True`）。
选线程而不是 asyncio 任务的理由：负载是**文件 IO**（写、fsync、gzip、磁盘统计），放在事件循环上会与
真实资金的决策轮、盯市 tick 抢同一个线程；放进 `asyncio.to_thread` 又会在每一批上新建取消落点。一条专用
线程让事件循环上**零文件 IO**，生命周期只有「放哨兵 → 限时 join」一种形态。

十一条硬约束（v2 新增第 11 条，并改写第 5、6、8、9、10 条）：

1. **热路径入队非阻塞**：`offer(item)` 先查 `self._accepting`，再在 `threading.Lock` 内检查
   `pending_bytes + item.size <= queue_max_bytes`，通过才 `SimpleQueue.put_nowait`。锁内只有两次整数运算，
   不存在等待写线程完成 IO 的路径。
2. **超预算丢新条、绝不丢旧条、绝不阻塞**：丢弃计入 `dropped.queue_full`；序号已消耗，写线程在下一条到达时
   写 `gap`。丢旧条会静默改写历史且读侧无法察觉。
3. **入队即冻结**：队列里只有字符串（`llm_call` 项是「头部 JSON + system/messages/tools 的逐条 JSON」，
   其余种类是整行 JSON）。写线程不持有任何会话对象的引用。
4. **轮转**：写入前若 `segment_bytes + item.size > segment_max_bytes` 且当前段已有记录 ⇒ 写 `segment_close`、
   关文件、开新段写 `segment_open`、清空 blob 集合；单条超过段上限则独占一个新段，**绝不拆行**。
5. **封口压缩**：`compress_segments=True` 时，被关闭的段在写线程内 `gzip` 为 `.jsonl.gz`（先写
   `.gz.tmp`，校验可完整解压后 `os.replace`，再删明文）；任何一步失败 ⇒ 保留明文、`compressFailures += 1`。
   【v2 · RV-P2-4】`compresslevel=6`（默认 9 在 64 MiB 段上要数秒）。【v2 · RV7】`app_quit` 关闭**不压缩活动段**
   （约束 8），遗留明文段保持明文（读侧兼容，§2.11.2），不做事后补压。
6. **flush 每批、fsync 节流**（【v2 · RV-P2-5】）：每排空一批队列即 `file.flush()`——强杀时丢的只是 OS 页缓存里
   尚未落盘的部分，而不是 Python 缓冲区里的整批行；距上次 `os.fsync` 超过 `fsync_interval_s`（`0` ⇒ 每批）、段被
   关闭或处理到 flush 标记（约束 8）时执行 fsync。
7. **磁盘低水位**：每 64 条或每 30 秒检查一次 `shutil.disk_usage(group_dir).free`；低于
   `min_free_bytes` ⇒ 状态 `paused_low_disk`，其后到达的项丢弃并计入 `dropped.disk_low`；
   高于 `min_free_bytes × 1.1` ⇒ 回到 `recording`（缺口由 `gap` 记录）。`min_free_bytes = 0` ⇒ 不检查。
8. **关闭有界、幂等，按调用方分档**（【v2 · RV7 / RV8 / RV9】）：
   - `close_nowait(reason)`：置 `_accepting=False` 并放哨兵，**同步、立即返回**。用户动作路径（停止 / 结束 / 清组 /
     启动与续跑失败的所有者帧，§2.10.5）**只用它**——真实资金的停止按钮绝不 await 后台收尾
     （`experience/scheduler.py:10-12` F-1）。写线程收到哨兵后在后台排空队列、写最终 `gap`（通道已分配但未到达的
     尾部序号）、关段、压缩、写 `state:"closed"` 的 manifest。
   - `request_flush()`：放一个 flush 标记（同步）；写线程处理到它时 `flush` + `fsync` 活动段并刷新 manifest。
     `note_paused` 必调——桌面端 `quit-pause` 走的正是 `pause_group`。
   - `aclose(reason, *, compress_active=True, timeout_s=None)` = `close_nowait` +
     `await asyncio.to_thread(self._thread.join, timeout_s or close_timeout_s)`。**只有 `shutdown()` 调它**，且固定以
     `compress_active=False`、`timeout_s=min(close_timeout_s, 1.0)` 调用。理由：Windows 桌面端的真实退出是
     `POST /api/ai-trade/quit-pause`（`desktop/src/main.js:583-584`，2 秒硬超时，随后强杀）→ `manager.shutdown()`，
     lifespan 钩子与 `atexit` 大概率跑不到（`main.js:578-581`、`manager.py:2180-2183`）；不压活动段，关闭就只剩
     「排空 + 关文件 + 写 manifest」这几毫秒。超时 ⇒ 记 `closeTimedOut`，线程继续在后台收尾（daemon）。
   - **单写者登记**：模块级 `dict[group_id, TrajectoryRecorder]`。`open_group_recorder` 发现同一 `group_id` 的
     recorder 仍活着 ⇒ 先 `close_nowait("superseded")` 并在 `to_thread` 里 `join(close_timeout_s)`，仍未结束 ⇒ 本次
     打开失败（`OPEN_BUSY`），**绝不**起第二个写者抢写 `manifest.json`；上一份 manifest 在旧线程结束之后才读。
     `close_group_recorder_nowait(group_id, reason)` 经它按 id 关闭（所有者帧拿不到对象时用，§2.10.5）。
   - `atexit` 钩子对仍活着的 recorder 调 `close_nowait` 并各 `join(1.0)`、总计不超过 3 秒——**纯兜底**，正确性不依赖它。
9. **写入前逐行剔除凭证原文**：recorder 持有一个剔除表（打开时登记 OKX `api_key` / `secret_key` /
   `passphrase`，包装客户端时登记 LLM `_api_key`）。写线程对每一行（含 blob）检查每个秘密串及其
   `json.dumps(s)[1:-1]` 转义形态，命中即替换为 `[REDACTED]` 并计 `redactions`。**只登记长度 ≥ 8 的串**：
   更短的串（例如 6 位纯数字口令）替换会误伤行情数字，而事实核查表明 payload 装配链路本就不含凭证
   （兄弟特性 §2.2.5 已核实）——剔除表是纵深防御，由用例 R-7 以构造含密载荷证明其生效。
   【v2 · RV-P2-6】剔除发生在计算 blob sha **之前**：文件里的每个 sha 都对应文件里真实存在的那段正文。
10. **连续写失败即停**：单条写入的 `OSError` ⇒ `writeErrors += 1`、`lastError` 更新、该条丢弃；连续
    `max_write_errors` 次 ⇒ 状态 `failed`、关闭文件、`_accepting=False`，其后入队的一律计
    `dropped.after_failure` 并立即返回。**不重试**：一个会无限重试的写入，会在磁盘拔出的那一刻把写线程
    变成忙等。成功写一条即清零连续计数。**`manifest.json` 的 `os.replace` 是唯一例外**（【v2 · RV-P2-3】）：Windows 上
    杀软 / 索引器的短暂占用会让它抛 `PermissionError`，故在写线程内有界重试 3 次、间隔 50 ms；仍失败只计
    `manifestWriteErrors` 并保留旧 manifest，不计入连续写失败。
11. **写线程主循环顶层守卫**（【v2 · RV15】）：`run()` 最外层 `except BaseException as exc` ⇒ 状态 `failed`、
    `lastError = {"code": "WRITER_CRASHED", "message": f"{type(exc).__name__}: {exc}"[:500], "ts"}`、
    `_accepting=False`，再尽力关闭文件句柄。没有它，一个非 `OSError` 的缺陷异常会让线程静默退出、状态停在
    `recording`、队列涨满后静默丢弃——本仓头号反模式「看起来在采、其实没有」（用例 R-11）。

### 2.9 状态机与分组回执 `trajectoryCapture`

```
            open 成功                     磁盘 < 下限                 连续写失败 ≥ N
 (off) ───────────────► recording ─────────────────► paused_low_disk ──────┐
   ▲   open 失败 → failed    │  ▲  磁盘 ≥ 下限×1.1          │               ▼
   │                         │  └─────────────────────────┘            failed
   │                         │ 单次写失败（未达 N）⇄ degraded（下一次成功写回到 recording）
   └─ 未勾选 / 运维关         └──────────── close（stop / end / app_quit / group_cleared）──► closed
```

`group_status()` 新增一个**形状恒定、永不缺席**的加法键（该端点 `-> dict` 且无 `response_model`，
`manager.py:2025` 的注释已核实新键不会被静默剔除）：

```python
                "trajectoryCapture": trajectory_receipt(group),
```

```jsonc
{
  "enabled": true,                         // 本会话此刻是否真的在采集（生效值）
  "requested": true,                       // 【v2 · RV2】用户请求了采集且未被运维关（source=="request" && rule.enabled）
  "source": "request",                     // request | not_requested | ops_off | resolve_failed
  "state": "recording",                    // off | recording | degraded | paused_low_disk | failed | closed
  "root": "C:/Users/me/AppData/Roaming/aragon-desktop/datasets/ai-trade",   // 未启用 ⇒ null
  "directory": "C:/…/ai-trade/20260910-aitradegrp_1c8f…",                   // 未启用 ⇒ null
  "isDefaultRoot": true,                   // 未启用 ⇒ null
  "counters": {"records": 1204, "llmCalls": 388, "rounds": 130, "bytesWritten": 12345678,
               "segments": 1, "dropped": 0, "writeErrors": 0, "serializeErrors": 0, "redactions": 0,
               "lateCalls": 0, "manifestWriteErrors": 0},                    // v2 +2（§2.3.2 / §2.8 约束 10）
  "pendingBytes": 0,
  "freeBytes": 128400000000,               // 最近一次检查值；从未检查 ⇒ null（绝不补 0）
  "lastError": null,                       // {"code","message","ts"}
  "schemaVersion": 1
}
```

`trajectory_receipt(group)` 放在 `trajectory/recorder.py`，manager 里只多一行调用：
`getattr(group, "trajectory", None)` 兜底与 `_intel_receipt` 同款（既有测试替身用 `SimpleNamespace`
直接构造分组）。**`lastError.message` 由后端下发中文，不得含 `**`**（本仓第 4 次复发的教训，
`docs/plans/ai-trade-loss-and-liveness-hardening/`）。

【v2 · RV2】`enabled` 与 `requested` 刻意是两个键：打开失败 / 写线程崩溃时 `enabled=false`、`requested=true`、
`state="failed"`——**这恰是最需要被看见的状态**。`requested` 取自分组上的 `trajectory_rule` 与 `trajectory_source`
（打开失败时 `group.trajectory is None`，但规则仍在），故恒可算；前端状态条按 `requested` 渲染（§2.14.2），绝不按
`enabled`。上面的状态图另加一条迁移：任意状态 ──写线程崩溃（§2.8 约束 11）──► `failed`。

### 2.10 启动链路：请求 → 解析 → 端点校验 → 装配

#### 2.10.1 请求体

`app/schemas.py`（`AiTradeGroupStartIn`，`:978-1043`）在 `intel` 字段之后新增：

```python
    # 后训练轨迹采集（ai-trade-trajectory-capture §2.10）：**整键缺省 ⇒ 不采集**（与 `intel` 同向，
    # 与上面「缺省即开启」的七个字段方向相反）。解析点 `_resolve_trajectory`；目录可用性由端点
    # `_require_trajectory_directory` 校验——**绝不在 `build_start_request` 里校验**（冷续跑重跑该链，
    # 任何 HTTPException 都会把续跑翻成 409，见 `_require_profit_exit_with_horizon` 的成文理由）。
    # ⚠️ 仅实盘 tab 下发；回测启动体不接该字段（§1.3 N1）。
    trajectoryCapture: AiTradeTrajectoryIn | None = None
```

同文件在 `AiTradeGroupStartIn` 之前新增两个模型：

```python
class AiTradeTrajectoryIn(BaseModel):
    """会话级后训练轨迹采集约定。`directory=None` ⇒ 用服务端默认根目录（默认值随部署变，前端不复制）。"""
    enabled: bool = False
    directory: str | None = Field(default=None, max_length=1024)


class AiTradeTrajectoryValidateIn(BaseModel):
    """`POST /ai-trade/trajectory/validate` 的请求体。"""
    directory: str | None = Field(default=None, max_length=1024)
```

`start_wire = body.model_dump(mode="json")`（`api/ai_auto.py:631`）自动带上本键 ⇒ 冷续跑同配置，零额外代码。

#### 2.10.2 解析（纯函数，不抛、不做 IO）

`ai_auto/trajectory/rules.py`：

```python
@dataclass(frozen=True, slots=True)
class TrajectoryRule:
    enabled: bool = False
    directory: str | None = None           # 已 strip；空串归一为 None


SOURCE_REQUEST = "request"; SOURCE_NOT_REQUESTED = "not_requested"
SOURCE_OPS_OFF = "ops_off"; SOURCE_RESOLVE_FAILED = "resolve_failed"


def resolve_trajectory_rule(raw: dict | None, settings) -> tuple[TrajectoryRule, str]:
    """顺序即优先级：未请求 → 运维关 → 用户关 → 开。任何异常 ⇒ (关, resolve_failed)，方向是**关闭**。"""
```

| 输入 | 结果 |
|---|---|
| `raw is None` | `(TrajectoryRule(), "not_requested")` |
| `settings.ai_trade_trajectory_enabled is False` | `(TrajectoryRule(), "ops_off")`（即使 `raw.enabled`） |
| `raw["enabled"]` 为假 | `(TrajectoryRule(), "request")` |
| 否则 | `(TrajectoryRule(True, (raw.get("directory") or "").strip() or None), "request")` |
| 任一步抛异常 | `(TrajectoryRule(), "resolve_failed")` + `logger.warning` |

`api/ai_auto.py` 在 `_resolve_intel`（`:247-295`）之后新增薄包装 `_resolve_trajectory(body, settings)`，
并在 `build_start_request` 中紧跟 `intel_rule, intel_source = _resolve_intel(body, settings)`（`:549`）
新增一行 `trajectory_rule, trajectory_source = _resolve_trajectory(body, settings)`；
`AiTradeGroupStartRequest(...)` 装配处（`:589-631`）在 `intel_rule=…, intel_source=…,` 之后加
`trajectory_rule=trajectory_rule, trajectory_source=trajectory_source,`。

`manager.AiTradeGroupStartRequest`（`manager.py:177-258`）末尾新增两个**带默认值**的字段：

```python
    # 后训练轨迹采集（trajectory-capture §2.10）。带默认值 ⇒ 直接构造本 dataclass 的既有测试逐字节零回归，
    # 默认值（关闭）恰好就是本特性合入前的行为。
    trajectory_rule: TrajectoryRule = field(default_factory=TrajectoryRule)
    trajectory_source: str = "not_requested"
```

`AiTradeGroup`（`:297-340`）末尾新增 `trajectory: TrajectoryRecorder | None = None`、
`trajectory_rule: TrajectoryRule = field(default_factory=TrajectoryRule)`、`trajectory_source: str = "not_requested"`、
`trajectory_open_error: dict | None = None`；`_GroupBuildCtx`（`:382-394`）末尾新增
`trajectory: TrajectoryRecorder | None = None`。`_build_group` 的 `AiTradeGroup(...)` 构造（`:781-804`）
在 `intel_rule=…, intel_source=…,` 之后补四个同名实参。

#### 2.10.3 端点校验（唯一会让启动失败的地方）

`ai_trade_start`（`api/ai_auto.py:450-472`）在 `_require_profit_exit_with_horizon(req, settings)` 之后插入
`await _require_trajectory_directory(req, settings)`：

```python
async def _require_trajectory_directory(req: AiTradeGroupStartRequest, settings: Settings) -> None:
    """用户勾选了采集而目录不可用 ⇒ 422 + 可执行补救（落点只能在端点内，理由同互锁闸）。

    勾选了却静默不采，等于替用户丢掉了他明确要的数据；故这里拒绝启动并给出两条一键出路，
    而不是「先跑起来再说」。未勾选 ⇒ 一次 IO 都不做。

    Raises:
        HTTPException: 422 `AI_TRADE_TRAJECTORY_DIR_INVALID`。
    """
    if not req.trajectory_rule.enabled:
        return
    verdict = await probe_directory(req.trajectory_rule.directory, settings)
    if verdict.ok:
        return
    remedies = ([] if verdict.is_default
                else [{"id": "trajectory_use_default_dir", "label": "改用默认目录"}])
    remedies.append({"id": "trajectory_disable", "label": "本次不采集数据"})
    raise HTTPException(422, detail={
        "code": "AI_TRADE_TRAJECTORY_DIR_INVALID",
        "message": f"后训练数据目录不可用：{verdict.message}",
        "directory": verdict.directory, "reasonCode": verdict.code, "remedies": remedies})
```

`aiTradeApi.request` 已从错误体取 `remedies`（`apps/web/lib/aiTradeApi.ts:47-55`），第 6 步已有
remedies 渲染区（`StepReview.tsx:158-227`），本特性只加两个 id 的处理（§2.14）。

#### 2.10.4 装配（`manager._open_trajectory`，不抛）

```python
    async def _open_trajectory(self, req: AiTradeGroupStartRequest, ctx: _GroupBuildCtx,
                               creds: AccountCredentials | None) -> TrajectoryRecorder | None:
        """按规则打开分组 recorder（v2 · RV10：新启动失败抛 TrajectoryOpenError；冷续跑失败 ⇒ None + lastError）。"""
```

委托 `trajectory.recorder.open_group_recorder(GroupRecorderRequest(rule, settings, group_id, created_ts,
resume_of, start_wire, secrets, env))`：规则关 ⇒ 立即返回 `None`；否则 `asyncio.wait_for(asyncio.to_thread(
_prepare_group_dir, …), probe_timeout_s)` 完成「解析根目录 → 建目录 → 写 DATASET_CARD（若无）→ 读上一份
manifest → 读可用空间」，成功后构造 recorder 并 `start()` 写线程。超时 / `OSError` ⇒ 返回 `None`，
`_build_group` 把 `{"code": "OPEN_FAILED"|"OPEN_TIMEOUT", "message", "ts"}` 存入
`trajectory_open_error`，回执 `state:"failed"`。

**【v2 · RV10】打开失败的去向按路径二分**：
1. **新启动**（`req.resume_of is None`）⇒ `_build_group` 在 W11 抛 `TrajectoryOpenError(code, message, directory)`。此刻
   `_build_member` 尚未调用、未建任何 run 行，抛出零残留。`ai_trade_start` 在调用 `start_group` 处加
   `except TrajectoryOpenError as exc: raise trajectory_dir_http_error(...) from exc`，与 `_require_trajectory_directory`
   **共用同一个 422 构造函数**（`AI_TRADE_TRAJECTORY_DIR_INVALID` + 同一组 remedies，`reasonCode` 取
   `OPEN_FAILED` / `OPEN_TIMEOUT` / `OPEN_BUSY`）。理由与 §2.10.3 相同：勾选了却静默不采等于替用户丢数据；且打开
   失败的原因比探测多（建分组目录、写 DATASET_CARD、起线程、单写者占用）。
2. **冷续跑** ⇒ 维持返回 `None` + `failed` 回执，续跑照常成功（下一条）。

- **【v2 · RV14】`env = "demo" if settings.okx_account_demo else "live"`**。AI-trade 启动恒经 `_creds(req.account_row)`
  （`manager.py:471`），v1 的「`creds is None` ⇒ paper」一支永不成立；本仓模拟盘是进程级 `okx_account_demo`
  （`config.py:49`，私有请求附 `x-simulated-trading:1`）。模拟撮合的成交混进真实奖励分布，与 N1 排除回测是同一条理由。
- `secrets`：`creds` 的三个字段（`AccountCredentials(api_key, secret_key, passphrase)`，`manager.py:159-161`）。
- 冷续跑时端点校验**不会运行**（续跑不经 `/ai-trade/start`），故「上次能写、这次盘被拔了」的情形在这里
  以 `failed` 回执落地，续跑本身照常成功——**采集层绝不阻挡一个可能正持有真实仓位的会话续跑**（用例 A-5）。

#### 2.10.5 关闭点（全部幂等）

**【v2 · RV8 / RV9】关闭点收敛到「所有者帧」**：谁打开、谁的调用帧负责在失败时关闭；用户动作路径一律同步
`close_nowait`，只有 `shutdown()` 有界 await。

| 位置 | 调用 | 覆盖的路径 |
|---|---|---|
| `_build_group`：W11 之后到 `return group` 包进 `try`，`except BaseException` 里重抛之前 | `close_recorder_nowait(ctx.trajectory, "build_failed")` | 任一 `_build_member` 抛错（新启动与冷续跑共用；v1 漏网） |
| `start_group`：`_build_group` 返回之后到 `_launch_group_or_rollback` 返回之前，`except BaseException` 里重抛之前 | `close_group_recorder_nowait(group.group_id, "start_failed")` | `_probe_tradability` / `_persist_group_state` 失败、`_launch_group_or_rollback` 回滚 |
| `resume_group` 冷分支：`resume_cold_group(...)` 到 `self._group = resumed` 之前，`except BaseException` 里重抛之前 | `close_group_recorder_nowait(group_id, "resume_failed")` | `resume_cold_group` 内的接管 / 起会话回滚、`_adopt_entry_bars` / `_mark_runs_running` 失败、**`_verify_armed(cold=True)` → `_rollback_resume`**（`manager.py:1154/1278`，v1 漏网） |
| `_clear_group_if_current`（同步）：真正清组的那一支 | `close_recorder_nowait(group.trajectory, "group_cleared")` | `stop_group` / `stop_member` / `end_group` / 会话自终止 / `_preflight_resume` 自愈清组 |
| `shutdown()`：已暂停 / flatten / hold 三个分支各自 `return` 之前 | `await recorder.aclose("app_quit", compress_active=False, timeout_s=min(close_timeout_s, 1.0))` | 桌面端 `quit-pause`（`api/ai_trade_lifecycle.py:159-171` 直调 `manager.shutdown()`）与 lifespan 关停 |

`close_recorder_nowait(recorder, reason)` / `close_group_recorder_nowait(group_id, reason)`（`trajectory/recorder.py`）：
`None` / 未登记 ⇒ 空转；幂等、同步、绝不抛。**`resume.py` 因此一行不改**——v1 在 `resume_cold_group` 的回滚点被
`resume_group` 冷分支的所有者帧覆盖（按 id 关闭，不需要拿到分组对象）。`stop_group` / `end_group` 在
`_clear_group_if_current` 之后**不再** await 任何收尾；用例 A-8 相应改为轮询等待写线程结束（上限 `close_timeout_s + 1s`）。

### 2.11 数据集编译（离线，`ai_auto/trajectory/compile.py` + `rewards.py` + `reader.py`）

#### 2.11.1 命令行

```
cd services/quant
.venv/Scripts/python -m app.ai_auto.trajectory.compile --src <分组目录或根目录> --out <输出目录> [--strict]
```

- `--src` 是分组目录（含 `manifest.json`）⇒ 编译它；是根目录 ⇒ 编译其下每个分组目录到 `<out>/<分组目录名>/`。
- 输出三件：`steps.jsonl`、`calls.jsonl`、`report.json`；输出文件先写 `.tmp` 再 `os.replace`。
- 退出码：`0` 成功；`2` 输入不是可识别的分组目录；`--strict` 且 `report` 有任一完整性问题 ⇒ `3`（输出照写）。
- **只读输入目录**：绝不改写、绝不删除任何段文件（用例 C-6 以只读文件属性冻结）。

#### 2.11.2 读取（`reader.py`，容错）

1. 枚举 `members/<instId>/seg-*.jsonl` 与 `seg-*.jsonl.gz`，按文件名排序（§2.7.1 保证字典序即时间序）；
   同名明文与 `.gz` 并存（压缩中途断电）⇒ 以能完整读完的那一个为准，另一个计 `duplicateSegments`。
2. 逐行 `json.loads`；最后一行无换行或解析失败 ⇒ `tornLines += 1` 并跳过；中间行解析失败 ⇒
   `invalidLines += 1` 并跳过（**不中止**）。
3. 每段维护 `blobs: dict[sha, body]`；`$blob` 引用在**本段**解析，不跨段（§2.7.3 第 3 条）；解析不到 ⇒
   `missingBlobs += 1`，该调用 `quality.requestComplete = false`。
4. 以 `(epoch, seq)` 检查连续性；不连续且无对应 `gap` 记录 ⇒ `unexplainedGaps` 追加区间。

#### 2.11.3 `calls.jsonl`（一次 LLM 调用一行；SFT / 偏好 / 过程监督）

```jsonc
{"kind": "call", "schemaVersion": 1, "callId": "…", "groupId": "…", "instId": "…", "runId": 1873,
 "env": "live", "site": "decision", "roundNo": 412, "callIndex": 2, "adopted": true,
 "prompt": {"system": "…", "messages": [{"role": "user", "content": [...]}, …], "tools": [...]},
 "completion": {"thinking": "…", "text": "…", "toolUses": [...], "stopReason": "tool_use"},
 "params": {...}, "usage": {...}, "status": "completed", "error": null, "timing": {...},
 "quality": {"requestComplete": true, "responseComplete": true, "thinkingCaptured": true,
             "thinkingEnabled": false, "argsDeltaWithoutId": 0}}
```

`messages` 里的 `role:"tool"` **原样保留**，并在 `report.json.conventions` 写明「`tool` 角色消息仅承载
`tool_result` 块；转 Anthropic Messages 时并入 user 轮，转 OpenAI Chat 时展开为 `role:"tool"`」
（与 `anthropic_client._to_anthropic_message` / `openai_client._to_openai_messages` 的翻译规则同源，
`anthropic_client.py:105-111`、`openai_client.py:97-113`）——编译器不替下游选定训练框架。

【v2 · RV-P2-13】`report.json.conventions` 与 DATASET_CARD 另写明：**历史 assistant 轮是系统合成的**——`commit_round`
按门禁后的最终决策装配 `ToolUseBlock`，并把执行回执与门禁 / 反思注记并进 `tool_result`（`decider.py:1270-1297`），
它们不含模型当时的研判正文与思考。模型的真实输出只在各次 `llm_call.response` 里；多轮 SFT 若要以模型原话替换历史
assistant 轮，须按 `callId` 自行回填，编译器不代劳。

#### 2.11.4 `steps.jsonl`（一个 `round` 记录一行；强化学习）

```jsonc
{"kind": "step", "schemaVersion": 1, "id": "aitradegrp_…:BTC-USDT-SWAP:412",
 "groupId": "…", "instId": "…", "runId": 1873, "env": "live", "roundNo": 412,
 "ts": 1788000900, "wallTs": 1788000912.4, "trigger": "interval",
 "prompt": { /* 被采纳决策调用的完整请求，同 calls 的 prompt */ },
 "completion": { /* 被采纳决策调用的完整输出 */ },
 "params": {...},
 "attempts": [{"callId": "…", "site": "decision", "status": "cancelled"}, …],
 "reflection": {"verdict": "confirm", "reason": "…", "callIds": ["…"]},
 "observation": { /* round.observation */ },
 "action": {"model": {...}, "final": {...}, "degrade": {"llm": null, "gate": null, "final": null, "vetoLayer": null}},
 "execution": {"executed": {...}, "fills": [...]},
 "reward": {"settled": true, "legs": [{"role": "open_leg", "tripIndex": 3, "realizedPnlUsd": 12.34,
            "realizedNetBps": 41.2, "rMultiple": 0.83, "holdSeconds": 3612, "exitSource": "profit_guard"}]},
 "quality": {"adoptedCall": "exact", "degraded": false, "gateModified": false, "reasked": false,
             "gap": false, "requestComplete": true, "thinkingCaptured": true, "accountingIssues": [],
             "roundCompletion": "full"}}
```

- `quality.adoptedCall`：`exact`（按 §2.5 判出且记录存在）/ `missing`（判出的 callId 在段里找不到）/
  `none`（本轮无决策调用）。
- `quality.gateModified = action.model.action != action.final.action`。
- 【v2 · RV4】`quality.roundCompletion` 取 `round.completion`；`partial` 轮（成交之后被暂停 / 取消 / 抛错）照常输出，
  `abortReason` 原样带进 `action`。
- `quality.gap`：本轮 `seq` 区间内有缺口（记录或调用可能不全）。
- **编译器不做任何过滤**：被门禁否决、降级、未结算、观望的轮次一律输出并如实标注，筛选权交给训练侧
  （兄弟特性的「默认排除」口径是为「直接拿去训练 SFT」服务的，本数据集同时服务偏好对与强化学习，
  负样本与被否决的动作恰恰是有用的数据）。

#### 2.11.5 奖励回填（`rewards.py`，复用唯一真源）

1. 汇总该标的全部 epoch 的成交腿（`round.execution.fills` ∪ `fills.fills`）。**【v2 · RV12】去重键 =
   `accounting_event_key(trade, runId)`**（`accounting_replay.py:84-93`，仓库唯一真源；实时成交恒带
   `explain.meta.accountingEventId`）。该函数对无身份的成交会退化到 `id(trade)`（进程内对象地址，离线无意义），此时
   改用 `(runId, epoch, ledgerSeq)`。排序键 `(epoch 在 manifest.epochs 中的次序, ledgerSeq)`；同一 epoch 内同一
   `ledgerSeq` 对应两个不同事件键 ⇒ 两条都保留并计 `report.ledgerSeqConflicts`；
2. 按上一步的次序给每条补**重放序号** `id = 1..N`（只写进交给累加器的拷贝，原成交字典不改）与 `runId`，交给
   `TradeAccountingAccumulator(context).restore(rows)`（`accounting_replay.py:159-171`：按 `id` 排序、`record_trade`
   逐腿消费）。**【v2 · RV13】`context` = 该 run 最早一条 `episode_start.accounting`（并入 `runId`）**——即实盘那份
   累加器的原始口径（`attribution.py:175-176`）；缺席 ⇒ `report.accountingContextMissing += 1`，该 run 的全部 step 在
   `quality.accountingIssues` 里带 `context_missing`，**绝不猜 `costBasis`**（缺席即走 legacy 费池分支，
   `accounting_replay.py:130`）；
3. 每个 `completed_trips[k]` 给出 `entryTrade` / `closeTrade` / `recordedPnl`（`db/ai_auto_repositories.py:108-112`
   读的就是这几个键）：
   - `closeTrade` 所在回合 ⇒ `role:"close_leg"`；
   - 重放序号 `id ∈ [entryTrade.id, closeTrade.id)` 且 `side ∈ {long, short}` 的每条腿所在回合 ⇒ `role:"open_leg"`
     （【v2 · RV12】用重放序号而不是 `ledgerSeq`：后者跨 epoch 不可比）；
   - 数值取 `recordedPnl` 与 `closeTrade.explain.meta` 里 `trip_ledger._WIRE_FIELDS` 已写好的键
     （`realizedNetBps` / `rMultiple` / `holdSeconds` / `roundTripFeeUsd` 等，`trip_ledger.py:300-313`）与
     `exitSource`；**缺键即缺席，绝不补 0**；
4. 腿所在的承载记录不是 `round`（盯市 / 停止 / 离线补记）⇒ 奖励只进 `report.json.tripsWithoutDecisionRound`，
   不伪造回合归属；
5. `accumulator.issues` 非空 ⇒ 相关 step 的 `quality.accountingIssues` 带上原因码、`reward.settled` 按实际
   是否形成 trip 取值。

### 2.12 目录：默认值、归一化与探测（`ai_auto/trajectory/directory.py`）

#### 2.12.1 默认根目录（单一真源）

```python
def default_root(settings) -> Path:
    raw = (settings.ai_trade_trajectory_default_dir or "").strip()
    if raw:
        return normalize_directory(raw)
    return db_file_path(settings).parent / "datasets" / "ai-trade"
```

`db_file_path(settings)` 是 `app/db/session.py` 新增的**公开**函数，把 `_db_url()`（`db/session.py:23-29`）
里「相对路径以 services/quant 为根」的三行抽出来，`_db_url()` 改为调用它（行为逐字不变，用例 D-3 断言
两者指向同一文件）。**不得在 trajectory 包里再写一遍这段路径解析**——两份必然漂移。

结果：桌面端打包形态下 `DB_PATH=<userData>/aragon.db`（`desktop/src/lib/config.js:61-63`、`paths.js:76-78`）
⇒ 默认根 `%APPDATA%/aragon-desktop/datasets/ai-trade`；开发形态 `db_path="aragon.db"`（`config.py:923`）
⇒ `services/quant/datasets/ai-trade`（`.gitignore` 新增 `services/quant/datasets/`）。

**刻意不默认到「文档」文件夹**：Windows「受控文件夹访问」会静默拦截未签名进程写入 Documents，且常被
OneDrive 重定向；`userData` 恒可写。可发现性由卡片上的完整路径、「打开目录」按钮与运行态回执解决。

#### 2.12.2 归一化与判定

`normalize_directory(raw) -> Path`：去首尾空白与成对引号 → `os.path.expandvars` → `os.path.expanduser` →
`Path(...)`；**不** `resolve()`（不跟随目录联接，也不在输入阶段访问磁盘）。

`check_directory(raw, settings) -> DirVerdict`（同步、含 IO，只在线程里调用），判定按序短路：

| # | 条件 | `code` | `message`（后端下发，不含 `**`） |
|---|---|---|---|
| 0 | `settings.ai_trade_trajectory_enabled` 为假 | `TRAJECTORY_DISABLED_BY_OPS` | 后训练数据采集已被运维关闭 |
| 1 | 含 NUL、以 `\\?\` 或 `\\.\` 开头 | `TRAJECTORY_DIR_UNSUPPORTED` | 不支持设备命名空间路径，请选择普通文件夹 |
| 2 | 非绝对路径 | `TRAJECTORY_DIR_NOT_ABSOLUTE` | 请填写完整的绝对路径（例如 D:\data\ai-trade） |
| 3 | `sys.platform == "win32"` 且 `len(str(path)) > 100`（【v2 · RV-P2-2】） | `TRAJECTORY_DIR_PATH_TOO_LONG` | 路径过长：段文件完整路径会超过 Windows 260 字符限制，请选择更短的目录 |
| 4 | 位于后端程序目录之内（`Path(app.__file__).parent`，或 PyInstaller 的 `sys._MEIPASS`） | `TRAJECTORY_DIR_FORBIDDEN` | 不能写入程序安装目录，请另选文件夹 |
| 5 | 路径存在且不是目录 | `TRAJECTORY_DIR_IS_FILE` | 该路径是一个文件，请选择文件夹 |
| 6 | 在「最近的已存在祖先目录」里创建并删除 `.aragon-probe-<uuid4hex>` 失败 | `TRAJECTORY_DIR_NOT_WRITABLE` | 目录不可写：{OSError.strerror}（{errno}） |
| 7 | `min_free_bytes > 0` 且 `shutil.disk_usage(祖先).free < min_free_bytes` | `TRAJECTORY_DIR_LOW_SPACE` | 可用空间 {x} 低于下限 {y}，采集会立即自动暂停，请换一个磁盘 |
| — | 全部通过 | `None` | `None` |

第 3 条的阈值由段文件相对路径的上界反推（【v2 · RV-P2-2】按压缩中间文件 `.jsonl.gz.tmp` 计）：`/<YYYYMMDD>-aitradegrp_<32hex>/members/<instId≤32>/seg-<16>-<8>-<5>.jsonl.gz`
逐段计 1+52+9+32+1+48 = **143** 字符（以 `.jsonl.gz.tmp` 结尾）；Windows `MAX_PATH` 含结尾 NUL 为 260 ⇒ 根目录 ≤ 116，
取 **100** 留余量（v1 的「120 + 约 130」漏算了 `.tmp` 后缀与 instId 上界）。**探测绝不创建目标目录本身**（用户逐字输入时每次失焦都建一个半截目录是垃圾）；真正建目录
只发生在启动时的 `_prepare_group_dir`。

`async def probe_directory(raw, settings) -> DirVerdict`：`asyncio.wait_for(asyncio.to_thread(check_directory,
raw, settings), settings.ai_trade_trajectory_probe_timeout_s)`；超时 ⇒ `TRAJECTORY_DIR_PROBE_TIMEOUT`
（「检查目录超时（网络盘或设备无响应），请换一个本地文件夹」）。

`DirVerdict.wire()`：`{"ok", "code", "message", "directory"（归一化后的字符串，Windows 下原样反斜杠）,
"isDefault", "exists", "freeBytes"（读不到 ⇒ null）, "minFreeBytes"}`。

### 2.13 前端：第 4 步「后训练数据采集」卡片

#### 2.13.1 状态归属（沿用引导式启动的两条成文教训）

- **规则值住在受控状态层**：`useAiTradeLaunchState.ts` 新增
  `const [trajectory, setTrajectory] = useState<AiTradeTrajectoryRule>(defaultTrajectoryCapture ?? AI_TRADE_TRAJECTORY_RULE_DEFAULT)`，
  `seed()` 里加一行同款复位；本文件**一个 `useEffect` 都不加**（G-2(d) 冻结）。
- **取数、判据与「到底采不采」上提到表单 hook**：新增 `launch/useTrajectoryCapture.ts`（v1 名
  `useTrajectoryDirCheck`），由 `useAiTradeLaunchForm` 调用。理由是 guided-launch 评审 S-2 成文的那条：子组件在六步里有
  五步是卸载的，探针留在卡片里 ⇒ 用户在第 4 步改完目录直达第 6 步，判据丢失。本 hook **不含 `useEffect`**。
- **【v2 · RV3】单一有效值谓词**：`effectiveEnabled = open && mode === "live" && rule.enabled && configQ.data?.enabled === true`。
  卡片复选框的显示值、第 4 步摘要位、`ready`、提交体、第 6 步去向行**只读它**；`rule.enabled && !effectiveEnabled` 时由
  `notice` 说清「为什么这次不会采」。v1 让这几处各看各的，持久化偏好里的 `enabled:true` 会在配置读取失败时照样被发出去。
  **【v2 · RV1】** 摘要位与提交体也在这里算好，表单只接线：

```ts
export function useTrajectoryCapture(input: {
  open: boolean; mode: AiTradeLaunchMode; rule: AiTradeTrajectoryRule;
}) {
  const configQ = useQuery({
    queryKey: ["aiTrade", "trajectory", "config"],
    queryFn: trajectoryApi.config,
    enabled: input.open,
    staleTime: 60_000,
    retry: false,
  });
  const effectiveEnabled = input.open && input.mode === "live" && input.rule.enabled
    && configQ.data?.enabled === true;
  const verdictQ = useQuery({
    queryKey: ["aiTrade", "trajectory", "validate", input.rule.directory ?? ""],
    queryFn: () => trajectoryApi.validate(input.rule.directory),
    enabled: effectiveEnabled,
    staleTime: 15_000,
    retry: false,
  });
  const error = trajectoryDirError({ active: effectiveEnabled, verdict: verdictQ.data ?? null });
  const notice = trajectoryNotice({ mode: input.mode, rule: input.rule, config: configQ });
  // 关闭时整键缺席 ⇒ 启动体与合入前逐字节相同（范围纪律 2），冷续跑的 start_wire 同样不带它。
  const requestRule = effectiveEnabled ? input.rule : undefined;
  return { configQ, verdictQ, effectiveEnabled, error, notice, requestRule };
}
```

`trajectoryNotice`（`lib/trajectory.ts` 纯函数，文案后端无关、前端自有）：`mode !== "live" || !rule.enabled` ⇒ `null`；
`config.isPending` ⇒「正在读取采集配置…」；`config.isError` ⇒「无法读取采集配置（后端尚未就绪），本次不会采集」；
`config.data.enabled === false` ⇒「后训练数据采集已被运维关闭，本次不会采集」；否则 `null`。

`trajectoryDirError`（`lib/trajectory.ts` 纯函数）：`!active || verdict == null || verdict.ok` ⇒ `null`；
否则 `verdict.message ?? "目录不可用"`。**请求在途 / 请求失败 ⇒ 不产生阻断**（「不确定一律不翻译成否决」，
`aiTradeLaunchRisk.ts:97-100` 的成文纪律）；权威校验在服务端端点（§2.10.3）。

#### 2.13.2 步骤模型的最小改动（`lib/aiTradeLaunchSteps.ts`）

- `AI_TRADE_LAUNCH_ERROR_KEYS` 末尾加 `"trajectoryDirError"`；
- `READY_INPUTS` 加 `"trajectoryDirError"`，**`BANNER_INPUTS` 不加**——与 `directionError` / `trendError`
  同一形态（就地渲染在字段旁、只进 `ready`），`firstError` 的表达式因此**逐字节不改**（`useAiTradeLaunchForm.ts:561`
  的注释：它守着 79 条既有用例的次序）；第 6 步的兜底行「第 4 步（Agent 认知）有未解决的输入错误 · 去处理」
  自动覆盖它（`StepReview.tsx:138-156`）；
- `STEP_ERROR_OWNERS.trajectoryDirError = { step: "cognition", modes: ["live"] }`；
- 文件头与 `STEP_ERROR_OWNERS` 上方那句「`cognition` 一步永不产生阻断错误」改为「`cognition` 只产生一种阻断
  错误：用户勾选了数据采集而目录被服务端判定不可用」；
- `AiTradeLaunchSummaryInput` 加**可选**字段 `trajectoryEnabled?: boolean`（可选 ⇒ 既有测试里的 `SUMMARY`
  常量不必改就能过 `tsc`）；`stepSummary` 的 `cognition` 分支在末尾追加
  `${input.trajectoryEnabled ? `${DOT}采集开` : ""}`——**未勾选时摘要逐字节不变**。

`useAiTradeLaunchForm.ts`（**【v2 · RV1】实测 780 行**，G-1 以 `split("\n").length` 计为 781，红线 800；本轮净增预算
**≤ 12 行**。v1 的「当前 744 行、≤ 30 行」是陈旧读数，照做即 811 行判红）：

1. `import { useTrajectoryCapture } from "./useTrajectoryCapture";`；解构 `state` 时取 `trajectory, setTrajectory`；
2. `const trajectoryCapture = useTrajectoryCapture({ open, mode, rule: trajectory });`（放在 `disciplineQ` 之后）；
3. `ready` 的实盘分支（`:551-552`）末尾加 `&& !trajectoryCapture.error`；
4. `errors` 对象（`:616-637`）加 `trajectoryDirError: trajectoryCapture.error,`；
5. `summaryInput`（`:642-667`）加 `trajectoryEnabled: trajectoryCapture.effectiveEnabled,`；
6. `handleConfirm` 实盘分支的 `onConfirm({...})`（`:703-712`）加 `trajectoryCapture: trajectoryCapture.requestRule,` 与
   `trajectoryPreference: trajectory,`（后者是用户的**原始**选择，只供 `AiTradeToggle` 记偏好，§3.4）；回测分支都不加；
7. 返回值「Agent 认知」组（`:754`）新起一行 `trajectory, setTrajectory, trajectoryCapture,`。

#### 2.13.3 卡片 `components/Agent/AiTradeTrajectoryFields.tsx`（≤ 260 行，受控纯视图）

Props：`value: AiTradeTrajectoryRule`、`onChange`、`capture: ReturnType<typeof useTrajectoryCapture>`（【v2 · RV3】）、
`disabled?: boolean`、`backtest?: boolean`。

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│ [□] 采集本次会话的思维链与工具调用，用于模型后训练（默认关闭）                           │
│     只写入本机文件夹，不上传。交易行为不受是否采集影响。                                │
│ ── 勾选后展开 ───────────────────────────────────────────────────────────────────────── │
│ 采集内容  完整请求上下文 · 思考与研判全文 · 工具调用及参数 · 门禁 / 执行 / 成交 / 盈亏   │
│ 保存目录  [C:\Users\me\AppData\Roaming\aragon-desktop\datasets\ai-trade      ]          │
│           [选择文件夹…]  [恢复默认]  [打开]                                             │
│           ✓ 可写 · 可用 128.4 GB · 本次启动会新建子目录「日期-分组号」                   │
│      或   ⚠ 目录不可写：拒绝访问（13）                         [改用默认目录]           │
│ 不写入 API 密钥（落盘前逐行剔除）。可用空间低于 1.0 GB 时自动暂停采集。                   │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

行为规格（逐条可测）：

1. **默认不勾**；`backtest` ⇒ 整卡替换为一行 muted 说明「回测不采集后训练数据（只采集实盘会话）」，不渲染控件。
2. `capture.configQ` 失败（老后端 404 / 网络）⇒ 复选框禁用，说明取 `capture.notice`。**【v2 · RV3】复选框的显示值取
   `capture.effectiveEnabled`（不是 `value.enabled`）**，`value` 原样保留——配置恢复后用户上次的选择自动生效，而禁用
   期间界面上绝不出现一个「勾着却不采」的框。
   **理由**：老后端的 `AiTradeGroupStartIn` 会静默忽略未知键，勾上了却不采是本仓明令禁止的「显示了但不作数」。
3. `configQ.data.enabled === false` ⇒ 复选框禁用，说明取 `TRAJECTORY_DISABLED_BY_OPS` 的文案并附
   `<code>AI_TRADE_TRAJECTORY_ENABLED</code>`（JSX 里用 `<code>`，绝不写反引号或 `**`）。
4. 目录输入框：本地草稿 `draft` 用 `useState(value.directory ?? "")`，外加
   `useEffect(() => setDraft(value.directory ?? ""), [value.directory])`（**只跟随已提交值**，与父树重渲染
   无关，不会重演输入回退缺陷）；`placeholder` 为 `configQ.data.defaultRoot`；**失焦或 Enter** 才提交
   `onChange({ ...value, directory: draft.trim() || null })`；Esc 还原为已提交值。逐键输入不发请求。
5. 「选择文件夹…」：仅 `getBridge()?.pickDirectory` 存在时渲染；调用
   `pickDirectory({ defaultPath: value.directory ?? configQ.data.defaultRoot })`，返回非空即提交。
6. 「恢复默认」：`value.directory != null` 时可点，提交 `directory: null`。
7. 「打开」：仅桌面端且 `verdict.exists === true` 时渲染，调 `getBridge()?.openPath(verdict.directory)`；
   返回非空串 ⇒ 就地一行 muted「无法打开：{原因}」。
8. 校验行：`verdictQ.isFetching` ⇒「正在检查目录…」；`ok` ⇒「✓ 可写 · 可用 {formatBytes(freeBytes)}」
   （`freeBytes == null` 时省略可用空间，**绝不显示 0 B**）；`!ok` ⇒「⚠ {message}」（`role="alert"`，
   文字色 `--short-ink`），`!isDefault` 时带「改用默认目录」按钮（提交 `directory: null`）；请求失败 ⇒
   muted「暂时无法检查该目录，启动时会再检查一次」（不阻断）。
9. 可达性：复选框 `aria-label="采集后训练数据"`；输入框 `aria-label="后训练数据保存目录"`；校验行
   `aria-live="polite"`；每个按钮的 `aria-label` 包含其可见文字（WCAG 2.5.3 Label in Name，
   notice-state-sync 终审修过同形缺陷）；形状 + 文字双通道（✓ / ⚠），不靠颜色单独承载语义。
10. 配色只用既有文字色 token：`--text` / `--muted` / `--accent-ink` / `--short-ink` / `--warn`；
    `--accent` / `--surface-*` 只作边框与底色；布局沿用 `AiTradeIntelFields` 的外框
    （`rounded-md border border-[var(--border)] px-3 py-2`，`AiTradeIntelFields.tsx:112-116`）。
11. 卡片 `data-testid="ai-trade-trajectory-fields"`；上述各行各有 `data-testid`（见 §6.2）。

`StepCognition.tsx` 在 `<AiTradeIntelFields …/>` 之后渲染本卡片，传 `form.trajectory` / `form.setTrajectory` /
`form.trajectoryCapture` / `submitting` / `mode === "backtest"`。本卡片的说明文字**都是说明而不是警示**，
故在 `lib/aiTradeLaunchRisk.ts` 文件头的「已知排除项」清单里补一行
「`AiTradeTrajectoryFields` 的目录与空间说明——说明而非警示；目录不可用是**阻断错误**，走步骤模型而非风险清单」
（该文件的实施纪律要求新增 `var(--warn)` 文本必须二选一登记）。

### 2.14 第 6 步复述与运行态状态条

#### 2.14.1 第 6 步（`StepReview.tsx`）

1. 逐步摘要的「Agent 认知」行已随 `stepSummary` 带上「采集开」。
2. 摘要列表下方、风险清单上方，仅当 `mode === "live" && summaryInput.trajectoryEnabled` 时渲染一行
   （`data-testid="ai-launch-trajectory-destination"`，muted）：
   「后训练数据将写入：{verdict.directory ?? defaultRoot}（本机，不上传）」。
   **【v2 · RV3】** `trajectoryCapture.notice` 非空（用户勾了、本次却不会采）时，同一位置改渲染一行 `--warn` 文字
   （`data-testid="ai-launch-trajectory-notice"`，前缀 △，形状 + 文字双通道）：「△ {notice}」。它是说明不是阻断
   （「不确定一律不翻译成否决」），按 `aiTradeLaunchRisk.ts` 的实施纪律登记进文件头「已知排除项」。
3. remedies 按钮新增两个 id 的处理（与既有 `enable_profit_guard` / `disable_horizon` 同款并带就地回执）：
   - `trajectory_use_default_dir` ⇒ `setTrajectory({ ...trajectory, directory: null })`，回执
     「已改用默认目录（第 4 步 · Agent 认知）」，跳转目标 `cognition`；
   - `trajectory_disable` ⇒ `setTrajectorySkipOnce(true)`（**终审 · U-1**：一次性跳过，不改 `trajectory`
     ——拨 `enabled` 会被当成用户的选择记住，正在积累的数据集从下一次启动起静默断档），回执
     「已关闭本次数据采集（第 4 步 · Agent 认知）」，跳转目标 `cognition`；在第 4 步卡片上重新表态即撤销。

#### 2.14.2 运行态（`components/Agent/AiTradeTrajectoryStatus.tsx`，≤ 150 行）

`AiTradeToggle.tsx` 在开关行 `</div>`（`:580`）之后、错误行（`:587`）之前插入：

```tsx
      {/* 【v2 · RV2】按 requested 渲染：打开失败 / 写线程崩溃时 enabled=false，而那正是最需要被看见的状态。 */}
      {(running || paused) && aiTradeGroup?.trajectoryCapture?.requested && (
        <AiTradeTrajectoryStatus receipt={aiTradeGroup.trajectoryCapture} paused={paused} />
      )}
```

组件是一行 `flex min-w-0 items-center gap-1.5 text-[10px]`，文案 `min-w-0 truncate`（全文进 `title`），
数字 `tabular shrink-0`——**单行不折行**，不改变开关行的 24px 行高契约。文案由纯函数
`describeTrajectoryReceipt(receipt, paused)`（`lib/trajectory.ts`）给出：

| state | 点 / 色 | 文案 |
|---|---|---|
| `recording` 且未暂停 | ● `--accent-ink` | 数据采集中 · {records} 条 · {formatBytes(bytesWritten)} |
| `recording` 且分组已暂停 | ● `--muted` | 数据采集待命（交易已暂停）· {records} 条 |
| `degraded` | △ `--warn` | 数据采集有写入失败（{writeErrors} 次），仍在继续 · 交易不受影响 |
| `paused_low_disk` | △ `--warn` | 数据采集已暂停：可用空间低于下限 · 交易不受影响 |
| `failed` | ⚠ `--short-ink` | 数据采集已停止：{lastError.message} · 交易不受影响 |
| `closed` | — `--muted` | 数据采集已结束 · {records} 条 |

`dropped > 0` 时在文案后追加「· 缺口 {dropped} 条」。桌面端追加一个「打开目录」按钮
（`aria-label="打开后训练数据目录"`，调 `openPath(receipt.directory)`）。

#### 2.14.3 回执进入前端状态的两条通路（本仓「逐键手抄」缺陷的第七处登记）

- `lib/useAiTradeGroupStream.ts` 的 `groupStateFromStatus`（`:33-103`）在 `intel: row.intel ?? null,` 之后加
  `trajectoryCapture: row.trajectoryCapture ?? null,`（附第七条同款注释）；
- `lib/aiTrade.ts` 的 `buildLifecyclePatch`（`:1575-1605`）在 `funding: row.funding ?? null,` 之后加
  `trajectoryCapture: row.trajectoryCapture ?? null,`——这是 15 秒对账的**窄 patch**，不加它则计数只在
  刷新页面时更新；
- `lib/aiTradeTypes.ts`：`AiTradeGroupRow` 与 `AiTradeGroupState` 各加
  `trajectoryCapture?: AiTradeTrajectoryReceipt | null;`，`AiTradeGroupLifecyclePatch.group` 的 `Pick`
  联合（`:2207-2217`）加 `| "trajectoryCapture"`。

两处漏任何一处都不会让 `tsc` 或既有用例变红，故由 §6.2 的 F-6 用例分别冻结。

### 2.15 桌面端 IPC

`desktop/src/preload.js` 在 `openLogsDir` 之后新增：

```js
  // 后训练数据目录（ai-trade-trajectory-capture §2.15）：系统「选择文件夹」对话框与「在资源管理器中打开」。
  pickDirectory: (opts) => ipcRenderer.invoke('aragon:pick-directory', opts),
  openPath: (target) => ipcRenderer.invoke('aragon:open-path', target),
```

新增纯函数模块 `desktop/src/lib/dir-access.js`：

```js
/** 渲染层传来的 defaultPath 只接受绝对路径字符串；其余一律 undefined（对话框回落系统默认）。 */
function pickDefaultPath(opts) { … }
/** open-path 的入参校验：非字符串 / 非绝对 / 不存在 / 非目录 ⇒ 返回原因码；合法 ⇒ null。 */
function openPathRejection(target, fsImpl = fs) { … }   // 'invalid_path' | 'not_found' | 'not_directory' | null
module.exports = { pickDefaultPath, openPathRejection };
```

`desktop/src/main.js` 在 `ipcMain.handle('aragon:open-logs', …)`（`:617`）之后新增两个 handler：

```js
  ipcMain.handle('aragon:pick-directory', async (_e, opts) => {
    const options = { title: '选择后训练数据保存目录', defaultPath: dirAccess.pickDefaultPath(opts),
                      properties: ['openDirectory', 'createDirectory', 'dontAddToRecent'] };
    const res = win ? await dialog.showOpenDialog(win, options) : await dialog.showOpenDialog(options);
    return res.canceled || !res.filePaths || !res.filePaths[0] ? null : res.filePaths[0];
  });
  ipcMain.handle('aragon:open-path', async (_e, target) => {
    const rejected = dirAccess.openPathRejection(target);
    if (rejected) return rejected;
    return (await shell.openPath(target)) || '';      // 空串 = 成功（Electron 约定）
  });
```

`dialog` / `shell` 已在 `main.js:10` 解构导入。**只打开目录、从不打开文件**：渲染层是本应用自己的页面，
但 IPC 边界仍然只接受「存在的绝对目录」，不给「借 openPath 执行任意文件」留口子。

`apps/web/lib/desktop.ts` 的 `AragonBridge`（`:91-98`）加：

```ts
  pickDirectory?(opts?: { defaultPath?: string }): Promise<string | null>;
  /** 成功返回空串；失败返回原因（`invalid_path` / `not_found` / `not_directory` / 系统错误文本）。 */
  openPath?(target: string): Promise<string>;
```

---

## 三、文件 / 模块变更计划

> 行数一律按 `CLAUDE.md` §二「单文件 ≤ 800 行、单方法 ≤ 50 行、形参 ≤ 5」给出预算。`session.py`（4454 行）、
> `manager.py`（2241 行）、`schemas.py`（2481 行）、`api/ai_auto.py`（1029 行）、`aiTradeTypes.ts`（2470 行）
> 已是既有欠账，本轮对它们**只加薄接线**，任何新逻辑一律落在新文件里。

### 3.1 后端 · 新增（13 个文件）

| 文件 | 行数预算 | 一句话意图 |
|---|---:|---|
| `services/quant/app/ai_auto/trajectory/__init__.py` | 40 | 包出口（【v2 · RV-P2-16】**不**导出 `compile` / `reader` / `rewards`——它们只依赖 `wire` 与 `accounting_replay`，CLI 不连带 import recorder / db）：`TrajectoryRule` / `TrajectoryRecorder` / `NULL_TRAJECTORY` / `RoundCapture` / `EpisodeFacts` / `EpisodeEnd` / `resolve_trajectory_rule` / `open_group_recorder` / `wrap_llm_clients` / `trajectory_receipt` / `probe_directory` / `trajectory_config_wire` |
| `…/trajectory/rules.py` | 70 | `TrajectoryRule` + 四个 source 常量 + `resolve_trajectory_rule`（纯函数） |
| `…/trajectory/wire.py` | 120 | `canonical_json` / `sha256_text` / `message_wire` / `tools_wire` / `decision_wire` / `envelope`（纯函数） |
| `…/trajectory/directory.py` | 200 | `default_root` / `normalize_directory` / `check_directory` / `probe_directory` / `group_dir_name` / `safe_component` / `DirVerdict` / `trajectory_config_wire` |
| `…/trajectory/llm_tap.py` | 240 | `RecordingLlmClient` / `wrap_llm_clients` / `classify_site` / `llm_params_of`（以 provider `_body` 为真源，【v2 · RV11】）/ `CallCapture`（观察与装配） |
| `…/trajectory/channel.py` | 330 | `TrajectoryChannel`（每标的一个；十二个写点的实现、回合作用域三态机【v2 · RV4 / RV5】、episode 关闭后直通【RV6】、成交水位、序号分配）/ `NullTrajectoryChannel` / `NULL_TRAJECTORY` / `RoundCapture` / `EpisodeFacts` / `EpisodeEnd` / `SessionBinding` |
| `…/trajectory/records.py` | 240 | 各种记录的纯构造函数 + `adopted_decision_call_id` + `accounting_context_of`（【v2 · RV13】） |
| `…/trajectory/recorder.py` | 340 | `TrajectoryRecorder`（字节预算、状态机、计数、剔除表、通道工厂、`close_nowait` / `request_flush` / `aclose`、按 `group_id` 的单写者登记与 `atexit` 兜底【v2 · RV7 / RV9】）/ `RecorderConfig` / `GroupRecorderRequest` / `open_group_recorder` / `TrajectoryOpenError`【RV10】/ `close_recorder_nowait` / `close_group_recorder_nowait` / `trajectory_receipt` |
| `…/trajectory/writer.py` | 360 | `SegmentWriter` 写线程主体：blob 去重、轮转、gzip（level 6）、flush / fsync、flush 标记、磁盘水位、缺口、凭证剔除（先剔除后哈希）、manifest 刷新（Windows 有界重试）、主循环顶层守卫【v2 · RV15 / RV-P2-3~6】 |
| `…/trajectory/manifest.py` | 120 | `read_manifest` / `write_manifest_atomic` / `ensure_dataset_card` / `DATASET_CARD_TEXT` |
| `…/trajectory/reader.py` | 160 | 容错读取段文件（明文 / gz / 撕裂行 / 本段 blob 解析 / 连续性检查）/ `ReadStats` |
| `…/trajectory/rewards.py` | 170 | 以 `TradeAccountingAccumulator` 回填往返奖励：事件键去重 + 重放序号 + `episode_start.accounting` 口径（§2.11.5，【v2 · RV12 / RV13】） |
| `…/trajectory/compile.py` | 300 | `compile_group` 纯编排 + `steps` / `calls` / `report` 装配 + `main(argv)`（`python -m` 入口） |
| `services/quant/app/api/ai_trade_trajectory.py` | 70 | `GET /ai-trade/trajectory/config`、`POST /ai-trade/trajectory/validate` |

### 3.2 后端 · 修改（9 个文件；v2 移除 `resume.py`，其回滚点由 `resume_group` 冷分支的所有者帧覆盖）

| 文件 | 净增预算 | 一句话意图 |
|---|---:|---|
| `services/quant/app/config.py` | +70 | 10 项 `ai_trade_trajectory_*` 设置（放在「韧性流式（iteration 3）」块之后、「标的市场情报层」块之前，`config.py:1001-1007` 之间）+ 校验器 `_check_trajectory_wiring`（放在情报 drain 校验器之后，`:1590-1610` 附近） |
| `services/quant/app/db/session.py` | +12 | 新增公开 `db_file_path(settings) -> Path`，`_db_url()` 改为调用它（行为逐字不变） |
| `services/quant/app/schemas.py` | +22 | `AiTradeTrajectoryIn` / `AiTradeTrajectoryValidateIn` + `AiTradeGroupStartIn.trajectoryCapture` |
| `services/quant/app/api/ai_auto.py` | +55 | `_resolve_trajectory`、`build_start_request` 两行接线、`_require_trajectory_directory` 与端点一行调用、共用的 422 构造 `trajectory_dir_http_error`、端点对 `TrajectoryOpenError` 的转译（【v2 · RV10】） |
| `services/quant/app/main.py` | +3 | import 并 `app.include_router(ai_trade_trajectory.router, prefix="/api")`（与既有 17 个路由同一落点；**不改** `_shutdown_steps`） |
| `services/quant/app/ai_auto/manager.py` | +70 | 三个 dataclass 加字段、`_open_trajectory`、W9 / W10 / W11、`_build_member` 包装、`_build_group` 接线与构建期 `try`、`start_group` / `resume_group` 冷分支两个所有者帧、`_clear_group_if_current` 一行、`shutdown()` 三分支有界关闭、`group_status` 一行（【v2 · RV8 / RV9 / RV10】） |
| `services/quant/app/ai_auto/session.py` | +48 | `__init__` 一行、`attach_trajectory` / `trajectory` 属性、W1–W8（W3 拆为 W3a / W3b，【v2 · RV4】）、两个薄方法 |
| `.env.example`（仓库根） | +40 | 10 项双向登记（放在「深度思维链」块之后、「标的市场情报层」块之前，`.env.example:695-718` 之间） |
| `.gitignore` | +2 | `services/quant/datasets/` |

### 3.3 前端 · 新增（6 个文件）

| 文件 | 行数预算 | 一句话意图 |
|---|---:|---|
| `apps/web/lib/trajectoryTypes.ts` | 90 | `AiTradeTrajectoryRule` / `AI_TRADE_TRAJECTORY_RULE_DEFAULT` / `AiTradeTrajectoryConfig` / `AiTradeTrajectoryDirVerdict` / `AiTradeTrajectoryReceipt`（不把新类型塞进 2470 行的 `aiTradeTypes.ts`，沿用 `experienceTypes.ts` 先例） |
| `apps/web/lib/trajectoryApi.ts` | 60 | `config()` / `validate(directory)`（镜像 `aiTradeApi.ts` 的自持 `request<T>` + `timeoutFor`） |
| `apps/web/lib/trajectory.ts` | 130 | 纯函数：`trajectoryDirError` / `trajectoryNotice`（【v2 · RV3】）/ `describeTrajectoryReceipt` / `formatBytes` / 中文文案表 |
| `apps/web/components/Agent/AiTradeTrajectoryFields.tsx` | 260 | 第 4 步卡片（§2.13.3） |
| `apps/web/components/Agent/AiTradeTrajectoryStatus.tsx` | 150 | 运行态状态条（§2.14.2） |
| `apps/web/components/Agent/launch/useTrajectoryCapture.ts` | 70 | 配置与目录校验两个查询 + 单一有效值谓词 / `notice` / 提交体（零 `useEffect`，【v2 · RV1 / RV3】） |

### 3.4 前端 · 修改（12 个文件）

| 文件 | 净增预算 | 一句话意图 |
|---|---:|---|
| `apps/web/lib/aiTradeTypes.ts` | +12 | `AiTradeGroupStartReq.trajectoryCapture?`、`AiTradeGroupRow` / `AiTradeGroupState.trajectoryCapture?`、`AiTradeGroupLifecyclePatch` 的 `Pick` 加一项、`AiTradeConfig.trajectoryCapture?` |
| `apps/web/lib/aiTradeLaunchSteps.ts` | +15 | 错误键、`READY_INPUTS`、归属表、摘要可选字段与 cognition 摘要后缀、注释改写 |
| `apps/web/lib/aiTradeLaunchRisk.ts` | +4 | 文件头「已知排除项」补两行：卡片的目录 / 空间说明、第 6 步 `ai-launch-trajectory-notice` 行（仅注释，【v2 · RV3】） |
| `apps/web/lib/aiTrade.ts` | +2 | `buildLifecyclePatch` 抄 `trajectoryCapture` |
| `apps/web/lib/useAiTradeGroupStream.ts` | +5 | `groupStateFromStatus` 抄 `trajectoryCapture` |
| `apps/web/lib/desktop.ts` | +5 | `AragonBridge.pickDirectory?` / `openPath?` |
| `apps/web/components/Agent/AiTradeLaunchModal.tsx` | +10 | `AiTradeLaunchOpts.trajectoryCapture?` / `trajectoryPreference?`（【v2 · RV3】）、props `defaultTrajectoryCapture?` |
| `apps/web/components/Agent/launch/useAiTradeLaunchState.ts` | +5 | `trajectory` 状态 + `seed()` 一行 |
| `apps/web/components/Agent/launch/useAiTradeLaunchForm.ts` | **≤ +12**（【v2 · RV1】实测 780 行） | §2.13.2 的七处 |
| `apps/web/components/Agent/launch/StepCognition.tsx` | +10 | 渲染卡片；文件头「本步永不产生阻断错误」改写为「只产生一种阻断错误：勾选了数据采集而目录不可用」（【v2 · RV-P2-12】） |
| `apps/web/components/Agent/launch/StepReview.tsx` | +40 | 去向行 / `notice` 行（【v2 · RV3】）+ 两个 remedy |
| `apps/web/components/Agent/AiTradeToggle.tsx` | +16 | 启动体透传 `opts.trajectoryCapture`（关闭时为 `undefined` ⇒ 整键缺席）、偏好记忆取 `opts.trajectoryPreference`（[R-4] 同款理由；不能取 `trajectoryCapture`，否则配置读取失败的那一次会把用户的目录选择抹成空）、`defaultTrajectoryCapture` 传参、状态条按 `requested` 渲染（【v2 · RV2 / RV3】） |

### 3.5 桌面端 / 文档（5 个文件）

| 文件 | 意图 |
|---|---|
| `desktop/src/preload.js` | +3：`pickDirectory` / `openPath` |
| `desktop/src/main.js` | +14：两个 IPC handler |
| `desktop/src/lib/dir-access.js`（新增） | 两个纯函数（入参校验） |
| `desktop/scripts/dir-access.test.mjs`（新增） | 放在 `scripts/` 是因为桌面端的测试命令是 `node --test "scripts/*.test.mjs"`（`desktop/package.json:23`），放在 `src/lib/` 不会被执行；以 `createRequire` 引入 `../src/lib/dir-access.js` |
| `README.md` | 「新增功能用法」补一段：开关位置、默认目录、编译命令、隐私说明 |

### 3.6 混合文件与暂存纪律（给「代码 Review、修复与提交」节点）

以下文件**同时带着兄弟特性 `ai-trade-audit-journal` 的在制改动**（`git status` 显示为 `M`）：
`services/quant/app/ai_auto/session.py`、`services/quant/app/ai_auto/manager.py`、`services/quant/app/config.py`、
`services/quant/app/main.py`、`.env.example`。提交时必须**行级精确暂存**，只暂存本特性的增量：

- 配方（本仓已用过两次，见 `ai-trade-notice-state-sync` 终审）：脚本对工作树文件做**精确字符串删除**
  （每块断言全文恰好命中 1 次）得到「只含本轮改动」的版本 → 写回原路径 → `git add` → 立刻用备份还原工作树，
  还原后 `sha256` 与暂存前逐字节相同。
- 为此，本特性在这些文件里的每一处插入都以**独立的锚点语句**定位（§2.3 表格），不与兄弟特性的插入共用行。
  例外只有一处相邻：W3b（v2）紧跟 `await self._post_round(ref_px, ts)`，兄弟特性的 `_journal_round(...)` 也紧跟它——
  **本特性的那一行必须放在 `_post_round` 的紧后一行（在兄弟特性的注释块之前）**，使 HEAD 版本上同一锚点成立。【v2】W3a 的锚点（`_round_reflection_summary()` 那一行，`session.py:2451`）两侧没有兄弟特性的插入（实读）。
- 用「干净暂存树」验证本提交不依赖兄弟特性：`TREE=$(git write-tree)` → `git archive "$TREE" | tar -x -C <tmp>` →
  拷 `services/quant/.env` → 在 `<tmp>` 里跑本特性的全部后端用例与 `tsc` / `vitest`（junction 只对 tsc / vitest 有效）。

---

## 四、接口设计

### 4.1 REST（全部挂在 `/api` 下，前端经 `/api/py/*` 同源代理）

#### `GET /ai-trade/trajectory/config`

不守 AI 总闸（只读配置，卡片据它说明「为什么不能开」）。**无 `response_model`**（本仓成文教训：
`response_model` 静默吞新键）。

```jsonc
200 {
  "enabled": true,                       // AI_TRADE_TRAJECTORY_ENABLED
  "defaultRoot": "C:\\Users\\me\\AppData\\Roaming\\aragon-desktop\\datasets\\ai-trade",
  "minFreeBytes": 1073741824,
  "segmentMaxBytes": 67108864,
  "compressSegments": true,
  "schemaVersion": 1,
  "backtestSupported": false,
  "thinkingCapturedProviders": ["anthropic"]
}
```

#### `POST /ai-trade/trajectory/validate`

```jsonc
Body: {"directory": "D:\\data\\ai-trade" | null}        // null ⇒ 校验默认根目录
200 {"ok": false, "code": "TRAJECTORY_DIR_NOT_WRITABLE", "message": "目录不可写：拒绝访问（13）",
     "directory": "D:\\data\\ai-trade", "isDefault": false, "exists": true,
     "freeBytes": 128400000000, "minFreeBytes": 1073741824}
422 请求体非法（pydantic，`directory` 超过 1024 字符）
```

校验结果是**数据**不是错误：`ok=false` 也回 200。副作用仅限在最近的已存在祖先目录里创建并立即删除一个
`.aragon-probe-*` 文件；**不创建目标目录**。前端超时档走默认 `FAST_MS`（服务端自带 `probe_timeout_s` 死线），
`httpTimeout.ts` 不改。

#### `POST /ai-trade/start`（既有端点，加法字段）

```jsonc
Body（节选）: {…既有字段…, "trajectoryCapture": {"enabled": true, "directory": null}}
200: group_status()，其中新增 "trajectoryCapture": { …§2.9 回执… }
422: {"error": {"code": "AI_TRADE_TRAJECTORY_DIR_INVALID",
                "message": "后训练数据目录不可用：目录不可写：拒绝访问（13）",
                "directory": "D:\\data\\ai-trade", "reasonCode": "TRAJECTORY_DIR_NOT_WRITABLE",
                "remedies": [{"id": "trajectory_use_default_dir", "label": "改用默认目录"},
                             {"id": "trajectory_disable", "label": "本次不采集数据"}]}}
```

#### `GET /ai-trade/status`（既有端点）

`group` 内新增 `trajectoryCapture`（§2.9），形状恒定、永不缺席。

### 4.2 WebSocket

**不新增任何 WS 事件**。采集状态是分组级事实，AI-trade 没有分组级 WS 通道（成员级 WS 按会话建），
既有 15 秒对账（`useAiTradeGroupStream.ts:111` 的 `RECONCILE_POLL_MS`）经窄 patch 下发即可；加一条分组 WS
只是第二份真值。

### 4.3 CLI

```
python -m app.ai_auto.trajectory.compile --src <dir> --out <dir> [--strict]
```

见 §2.11.1。`--help` 输出中文用法与三种输出文件的说明。

### 4.4 桌面端 IPC

| 通道 | 方向 | 入参 | 返回 |
|---|---|---|---|
| `aragon:pick-directory` | invoke | `{defaultPath?: string}` | `string \| null`（取消 ⇒ `null`） |
| `aragon:open-path` | invoke | `string`（绝对目录） | `""` 成功；`invalid_path` / `not_found` / `not_directory` / 系统错误文本 |

### 4.5 Python 内部接口（跨模块契约）

```python
# 会话 ← manager
AiTradeSession.attach_trajectory(channel: TrajectoryChannel | None) -> None
AiTradeSession.trajectory -> TrajectoryChannel            # 恒非 None（空对象兜底）

# 通道（会话与 llm_tap 调用；全部同步、绝不抛）
TrajectoryChannel.bind(binding: SessionBinding, *, executor) -> None
TrajectoryChannel.open_episode(facts: EpisodeFacts) -> None
TrajectoryChannel.begin_round(round_no: int, ts: int, wake: dict) -> None
TrajectoryChannel.stage_round(cap: RoundCapture) -> None                        # v2 · W3a（RV4）
TrajectoryChannel.end_round(*, summary_installed: bool) -> None                 # v2 · W3b
TrajectoryChannel.fail_round(round_no: int, ts: int, exc: BaseException) -> None
TrajectoryChannel.release_round(round_no: int) -> None
TrajectoryChannel.skip_round(round_no: int, ts: int, detail: dict) -> None
TrajectoryChannel.sync_fills(context: str) -> None
TrajectoryChannel.note_paused(reason: str, flatten: bool) -> None
TrajectoryChannel.note_resumed(path: str) -> None
TrajectoryChannel.close_episode(end: EpisodeEnd) -> None
TrajectoryChannel.open_call(*, system, messages, tools, params: dict) -> CallCapture | None

# recorder（manager 调用）
async open_group_recorder(req: GroupRecorderRequest) -> RecorderOpenResult   # 不抛
TrajectoryRecorder.channel(inst_id: str) -> TrajectoryChannel                 # 幂等
TrajectoryRecorder.close_nowait(reason: str) -> None                          # 同步、幂等
async TrajectoryRecorder.aclose(reason: str, *, compress_active: bool = True, timeout_s: float | None = None) -> None                          # 有界、幂等
TrajectoryRecorder.receipt(source: str) -> dict
trajectory_receipt(group) -> dict
TrajectoryRecorder.request_flush() -> None                                    # v2 · 同步；note_paused 调用（RV7）
close_recorder_nowait(recorder: TrajectoryRecorder | None, reason: str) -> None  # v2 · 所有者帧（RV9）
close_group_recorder_nowait(group_id: str, reason: str) -> None               # v2 · 经单写者登记按 id 关闭
class TrajectoryOpenError(Exception): code: str; message: str; directory: str | None   # v2 · RV10
accounting_context_of(session) -> dict | None                                 # v2 · RV13，纯读、绝不抛
```

---

## 五、数据模型

### 5.1 数据库

**无**。不建表、不加列、不改任何既有表。

### 5.2 磁盘

见 §2.7（目录布局、记录信封与 14 种 kind、blob、manifest）。`schemaVersion = 1`；任何字段的改名 / 删除 /
语义变更都必须把 `v` 加一并在本文件登记——离线脚本会按字段名分支，改名等同破坏性变更。**新增字段不加版本**
（读侧一律「缺键即缺席」）。

### 5.3 内存形状（Python）

```python
@dataclass(frozen=True, slots=True)
class SessionBinding:  session_id: str; run_id: int; inst_id: str; bar: str

@dataclass(frozen=True, slots=True)
class EpisodeFacts:    session_id: str; run_id: int; inst_id: str; bar: str
                       strategy_ids: list[int]; strategy_names: list[str]
                       context_refresh_rounds: int; wake_interval_s: float
                       take_profit_usd: float | None; stop_loss_usd: float | None; started_ts: int
                       accounting: dict | None      # v2 · RV13：会话累加器 context 的拷贝

@dataclass(frozen=True, slots=True)
class EpisodeEnd:      stop_reason: str | None; trade_count: int
                       realized_pnl_usd: float | None; flatten_result: dict | None

@dataclass(slots=True)
class RoundScope:      round_no: int; ts: int; wake: dict
                       calls: list[dict]            # [{"callId","site","callIndex"}]
                       state: str = "open"          # v2 · open | staged | closed（§2.3.2）
                       staged: RoundCapture | None = None

@dataclass(frozen=True, slots=True)
class RecorderConfig:  queue_max_bytes: int; segment_max_bytes: int; compress_segments: bool
                       min_free_bytes: int; fsync_interval_s: float; close_timeout_s: float
                       max_write_errors: int

@dataclass(frozen=True, slots=True)
class QueuedLine:      member: str; seq: int; epoch: str; kind: str; line: str; size: int

@dataclass(frozen=True, slots=True)
class QueuedCall:      member: str; seq: int; epoch: str; head: str      # 不含 request 的整行 JSON
                       system: str; messages: tuple[str, ...]; tools: str; size: int
```

`RoundCapture` 见 §2.4；`TrajectoryRule` 见 §2.10.2；`DirVerdict` 见 §2.12.2。

### 5.4 前端类型（`lib/trajectoryTypes.ts`）

```ts
export interface AiTradeTrajectoryRule { enabled: boolean; directory: string | null }
export const AI_TRADE_TRAJECTORY_RULE_DEFAULT: AiTradeTrajectoryRule = { enabled: false, directory: null };

export interface AiTradeTrajectoryConfig {
  enabled: boolean; defaultRoot: string; minFreeBytes: number; segmentMaxBytes: number;
  compressSegments: boolean; schemaVersion: number; backtestSupported: boolean;
  thinkingCapturedProviders: string[];
}

export interface AiTradeTrajectoryDirVerdict {
  ok: boolean; code: string | null; message: string | null; directory: string;
  isDefault: boolean; exists: boolean; freeBytes: number | null; minFreeBytes: number;
}

export type AiTradeTrajectoryState =
  | "off" | "recording" | "degraded" | "paused_low_disk" | "failed" | "closed";

export interface AiTradeTrajectoryReceipt {
  enabled: boolean;
  requested: boolean; // v2 · RV2：状态条据它渲染
  source: "request" | "not_requested" | "ops_off" | "resolve_failed";
  state: AiTradeTrajectoryState;
  root: string | null; directory: string | null; isDefaultRoot: boolean | null;
  counters: { records: number; llmCalls: number; rounds: number; bytesWritten: number; segments: number;
              dropped: number; writeErrors: number; serializeErrors: number; redactions: number;
              lateCalls: number; manifestWriteErrors: number };
  pendingBytes: number; freeBytes: number | null;
  lastError: { code: string; message: string; ts: number } | null;
  schemaVersion: number;
}
```

`AiTradeConfig.trajectoryCapture?: AiTradeTrajectoryRule`（持久化**用户上一次的显式选择**，与 `intel`
同款，`aiTradeTypes.ts:2255-2258`；**不**改 persist version）。系统默认仍是关闭：首次使用 / 老 localStorage
无此键 ⇒ `AI_TRADE_TRAJECTORY_RULE_DEFAULT`。理由：一个每次重开都自动变回「关」的采集开关，会让正在
积累数据集的用户在某一次忘了勾而**静默丢掉一整段数据**；而第 4 步摘要「采集开」与第 6 步去向行让这个
选择在每次启动时都被看见。

### 5.5 设置项（10 项，前缀 `AI_TRADE_` 是硬约束：桌面壳按前缀透传，`desktop/src/lib/config.js:78-87`）

| # | 键 | 默认 | 约束 | 语义 / 回退 |
|---|---|---|---|---|
| 1 | `AI_TRADE_TRAJECTORY_ENABLED` | `true` | bool | 运维可用性总闸。`false` ⇒ 卡片禁用并说明；请求里的 `enabled` 解析为 `ops_off`；不建目录、不起线程、不包装客户端 |
| 2 | `AI_TRADE_TRAJECTORY_DEFAULT_DIR` | `""` | 字符串 | 空 ⇒ `<数据库文件所在目录>/datasets/ai-trade` |
| 3 | `AI_TRADE_TRAJECTORY_QUEUE_MAX_BYTES` | `134217728` | `ge=1048576, le=4294967296` | 待写字节预算（128 MiB）；超出丢新条 + `gap` |
| 4 | `AI_TRADE_TRAJECTORY_SEGMENT_MAX_BYTES` | `67108864` | `ge=1048576, le=1073741824` | 单段明文上限（64 MiB） |
| 5 | `AI_TRADE_TRAJECTORY_COMPRESS_SEGMENTS` | `true` | bool | 封口段 gzip |
| 6 | `AI_TRADE_TRAJECTORY_MIN_FREE_BYTES` | `1073741824` | `ge=0` | 可用空间下限（1 GiB）；`0` ⇒ 不检查 |
| 7 | `AI_TRADE_TRAJECTORY_FSYNC_INTERVAL_S` | `5.0` | `ge=0, le=300` | `0` ⇒ 每批 fsync |
| 8 | `AI_TRADE_TRAJECTORY_CLOSE_TIMEOUT_S` | `5.0` | `gt=0, le=60` | 关闭排空死线；**必须 < `SHUTDOWN_STEP_TIMEOUT_S`** |
| 9 | `AI_TRADE_TRAJECTORY_PROBE_TIMEOUT_S` | `3.0` | `gt=0, le=30` | 目录探测 / 打开死线 |
| 10 | `AI_TRADE_TRAJECTORY_MAX_WRITE_ERRORS` | `5` | `ge=1, le=1000` | 连续写失败达此数 ⇒ `failed` |

启动期校验器 `_check_trajectory_wiring`（`@model_validator(mode="after")`，写错即启动失败）：

1. `ai_trade_trajectory_close_timeout_s < shutdown_step_timeout_s`——倒挂时 `ai_trade` 步整体先被外层
   cancel，写线程的收尾（关段、压缩、manifest）永远轮不到；与经验层、情报层、交易日志三处的同名校验逐字同款。
2. `ai_trade_trajectory_probe_timeout_s <= ai_trade_trajectory_close_timeout_s`——打开探测比关闭死线还长，
   意味着一次卡住的打开会拖过关停预算。

---

## 六、测试与验收

> 跑测纪律（本仓既有教训）：后端全量 pytest **不能并发**、须 `python -u`、单次约 15–32 分钟，必须先注册
> `wait_async`；vitest 必须在 `apps/web` 内以 `NODE_ENV=test` 跑；源码扫描用例的正则写 `\r?\n`
> （`core.autocrlf=true`）。本机 HEAD 既有红：`test_ai_trade_backstop_repair_session::test_the_session_keeps_only_a_thin_seam`
> （`session.py` 里 `self._repair` 已是 7 次），未跟踪的 `test_factor_library_expansion.py` 7 条，
> `test_experience_incremental::test_g15_…` 计时条（本机 630ms > 500ms）。**本特性不得在 `session.py` 里新增
> `self._repair` 字样，也不得出现 `limits_from_settings` 子串**（情报层 G-7 门禁逐字扫描 `session.py`）。

### 6.1 后端用例（新增 7 个文件）

**`tests/test_ai_trade_trajectory_llm_tap.py`**

| # | 断言 |
|---|---|
| T-1 | 透明性：同一个假客户端（正常 / 中途 `error` 事件 / 内层抛异常 / 首个 `message_done` 后仍有 usage 帧），包装前后消费方看到的事件序列**对象身份与顺序完全一致**；`AiTradeDecider.decide` 在包装前后返回相同的 `TradeDecision` 与 `last_analysis` |
| T-2 | AST（G-T2）：`RecordingLlmClient.stream` 内 `ast.AsyncFor` 恰 1、`ast.Await` 为 0、`ast.Yield` 恰 1；每个 `ExceptHandler` 的最后一条语句是 `ast.Raise` |
| T-3 | 完整性：构造 30000 字研判 + 25000 字思考 + 两段工具入参分片，记录里的 `text` / `thinking` / `argumentsJson` 与拼接原文**逐字节相等**（证明不受 `_ANALYSIS_MAX_CHARS=20000` 影响） |
| T-4 | 状态分类：自然结束 ⇒ `completed`；`asyncio.wait_for` 超时 ⇒ `cancelled`；消费方 `break` 后 GC ⇒ `closed_early`；`error` 事件 ⇒ `error` 且在生成器结束**之前**已入队；内层 `RuntimeError` ⇒ `exception` |
| T-5 | 调用点分类：以 `prompts.DECISION_TOOL_NAME` / `REFLECTION_TOOL_NAME` / `experience.distill.DISTILL_TOOL_NAME` / 空工具 / 未知工具构造，分别得到五个 site；常量来自 import 而非字面量（源码扫描 `llm_tap.py` 不含 `"submit_trade_` 字面量） |
| T-6 | 绝不抛：monkeypatch `wire.canonical_json` 抛 `TypeError` ⇒ 事件照常全部转发、`serializeErrors == 1`、无记录入队 |
| T-7 | OpenAI 形态：`tool_use_args_delta` 的 `id=None` 分片被归到最近开启的工具，`argsDeltaWithoutId` 计数正确 |
| T-8 | 参数：`AnthropicClient` 实例的 `model/maxTokens/temperature/topP/thinkingBudgetTokens` 被读出；记录全文不含 `api_key` 原文 |
| T-9 | `adopted_decision_call_id` 逐行覆盖 §2.5 的五行表 |
| T-10【v2 · RV11】 | 采样参数真源：`thinking_budget_tokens>0` 的 `AnthropicClient` ⇒ `requestParams.temperature == 1.0` 且无 `top_p`；`OpenAIClient` ⇒ 含 `tool_choice:"auto"` 与 `stream_options`；无 `_body` 的替身 ⇒ `paramsSource == "attributes"`；记录全文不含 `api_key` 原文 |
| T-11【v2 · RV-P2-1 / RV6】 | `copy.copy(wrapper)` 与 `pickle.dumps` 不递归；`site == "experience_distill"` 的调用零记录、只计 `lateCalls` |

**`tests/test_ai_trade_trajectory_recorder.py`**（写线程；每个用例 `finally` 里 `aclose`，防线程泄漏）

| # | 断言 |
|---|---|
| R-1 | 轮转 + 去重：小段上限下写 50 条引用同一 system 的 `llm_call` ⇒ 多段；**任取一段单独读取**，全部 `$blob` 在本段内可解析 |
| R-2 | 关闭后所有非活动段为 `.jsonl.gz` 且可完整解压；压缩失败（monkeypatch `gzip.open` 抛）⇒ 保留明文、`compressFailures == 1` |
| R-3 | 字节预算：阻塞写线程（monkeypatch 写入等待一个 `threading.Event`）后连续入队至超预算 ⇒ `dropped.queue_full > 0`；放行后文件里出现 `gap{fromSeq,toSeq}` 且区间与被丢弃序号一致 |
| R-4 | 连续写失败：monkeypatch 写入抛 `OSError` N 次 ⇒ 状态 `failed`、`lastError.code == "WRITE_FAILED"`、之后 `offer` 立即返回并计 `dropped.after_failure` |
| R-5 | 磁盘水位：monkeypatch `shutil.disk_usage` 先低后高 ⇒ `paused_low_disk` → `recording`，中间丢弃的序号以 `gap{reason:"disk_low"}` 记录 |
| R-6 | 关闭有界且幂等：写线程卡住时 `aclose` 在 `close_timeout_s + 0.5s` 内返回；连续调用两次不抛 |
| R-7 | 凭证剔除：登记 `"sk-abcdefghijklmnop"` 与 `"okx-secret-123456"` 后写入含它们（含 JSON 转义形态）的记录 ⇒ 文件全文不含原串、`redactions` 计数正确；长度 < 8 的串不登记 |
| R-8 | manifest：原子写（写 `.tmp` 中途抛异常时旧 `manifest.json` 完整可读）；关闭后 `state == "closed"`、`members[*].ledgerSeqCaptured` 正确 |
| R-9 | 事件循环上零文件 IO：包装 `builtins.open` / `os.fsync` 记录调用线程，热路径 `offer` 期间事件循环线程的调用数为 0 |
| R-10 | 再次打开同一分组目录（模拟冷续跑）从新段开始，绝不向既有段追加；上一段末行撕裂不影响新段 |
| R-11【v2 · RV15】 | monkeypatch 写线程内部抛 `RuntimeError` ⇒ 状态 `failed`、`lastError.code == "WRITER_CRASHED"`、其后 `offer` 立即返回并计 `dropped.after_failure` |
| R-12【v2 · RV9】 | 同一 `group_id` 在旧 recorder 仍活着时再次打开 ⇒ 旧的先被关闭，任一时刻只有一个写线程存活；旧线程 join 超时（monkeypatch 卡住）⇒ 新开返回 `OPEN_BUSY` |
| R-13【v2 · RV7】 | 活动段写到 20 MiB 后 `aclose("app_quit", compress_active=False, timeout_s=1.0)` 在 1.5 s 内返回，活动段保持 `.jsonl` 明文、manifest `state=="closed"`；`request_flush()` 被处理后文件长度等于已入队的全部字节 |
| R-14【v2 · RV-P2-3】 | monkeypatch `os.replace` 前两次抛 `PermissionError` ⇒ manifest 最终写成、`manifestWriteErrors == 0`；三次都抛 ⇒ 旧 manifest 完整可读、`manifestWriteErrors == 1`、状态不变 |

**`tests/test_ai_trade_trajectory_session.py`**（真会话 + 假客户端，沿用 `test_ai_trade_session.py:60-72 / :387-401` 的假流写法）

| # | 断言 |
|---|---|
| S-1 | 端到端一轮：首次决策超时 → 重试成功开仓 → 反思 confirm → 摘要到期 ⇒ 记录序列含 `episode_start`、4 条 `llm_call`（decision×2、reflection、summary）、1 条 `round`；`round.calls` 恰 4 条且顺序正确；`adoptedDecisionCallId` 是第二次决策调用 |
| S-2 | `_post_round` 内利润保护平仓的那一笔出现在 `round.execution.fills` 里，`ledgerSeq` 连续 |
| S-3 | 决策轮抛异常 ⇒ `round_failed` 且带已发生的调用 id；无 `round` |
| S-4 | 在飞轮于**决策调用期间**被 `pause` 取消 ⇒ `round_aborted`；`CancelledError` 照常上抛 |
| S-5 | 整轮跳过 ⇒ `round_skipped`，键与数和同轮 WS `round_skipped` 事件一致 |
| S-6【v2 · RV15】 | **采集与否不改变交易**：同一脚本化假行情 + 假客户端，采集关（`attach_trajectory(None)`、客户端不包装）与采集开（真通道 + `RecordingLlmClient`）两次运行的 WS 事件序列、成交列表、`decider._context` 逐项相等；且规则关闭时 `manager._build_member` 交给决策器的客户端对象与工厂返回的是同一对象（`is`）。v1 的「与合入前比较」在单测进程里没有可比对象 |
| S-7 | AST（G-T3）：`session.py` 全文无 `await self._trajectory`；`channel.py` 每个公开方法顶层恰一个 `Try` 且 handler 覆盖 `Exception` |
| S-8 | 台账裁剪：把 `executor.trades` 人为裁到比新增成交少 ⇒ 记录 `gap{reason:"ledger_trimmed"}`，`ledgerSeq` 仍按 `trade_count` 编号 |
| S-9 | 冷续跑离线补记：上一份 manifest 的 `ledgerSeqCaptured=3`、回灌后 `trade_count=5` ⇒ `fills{context:"offline_catch_up"}` 恰含 `ledgerSeq` 4、5 |
| G-T1 | AST：`session.py` / `manager.py` / `decider.py` 的 `if` / `while` / 比较表达式中不出现任何从 `ai_auto.trajectory` 导入的名字 |
| S-10【v2 · RV4】 | 开仓成交之后、`flush_summary` 的摘要调用挂起期间 `pause` ⇒ 恰一条 `round{completion:"partial", abortReason:"cancelled"}`，含 `observation` / `decision.model` / `decision.final` / 开仓那笔 `fills`，且**无** `round_aborted`；同形用例令 `_post_round` 抛错 ⇒ `abortReason:"exception"` |
| S-11【v2 · RV5】 | 整轮跳过 ⇒ 恰一条 `round_skipped`，**无** `round_aborted` |
| S-12【v2 · RV6】 | `stop()` 之后经 `decider.llm` 发起一次调用，以及 `_maybe_session_exit` 触发 stop 之后的 W7 ⇒ 零新记录、`lateCalls == 1`、`dropped == 0`，编译报告无缺口 |

**`tests/test_ai_trade_trajectory_start_api.py`**

| # | 断言 |
|---|---|
| A-1 | 请求不带 `trajectoryCapture` ⇒ 回执 `enabled=false, source="not_requested", state="off"`，默认根目录下**没有**新建任何目录 |
| A-2 | 带 `{enabled:true, directory:<tmp>}` ⇒ 200，回执 `recording`、`directory` 形如 `<tmp>/<YYYYMMDD>-aitradegrp_…`，`start_wire` 含该键 |
| A-3 | 目录不可写（monkeypatch 探测）⇒ 422 `AI_TRADE_TRAJECTORY_DIR_INVALID`，两条 remedies；目录为默认根时只有 `trajectory_disable` 一条；**分组未被创建**（`group_status() is None`） |
| A-4 | `AI_TRADE_TRAJECTORY_ENABLED=false` 且请求 `enabled:true` ⇒ 200、`source="ops_off"`、无目录 |
| A-5 | 冷续跑时目录已不可写 ⇒ 续跑**成功**、回执 `state="failed"` + `lastError`；不出现 409 |
| A-6 | `POST /ai-trade/trajectory/validate` 覆盖 §2.12.2 表的每一个 `code`（含 `PROBE_TIMEOUT`）；校验**不创建**目标目录 |
| A-7 | `GET /ai-trade/trajectory/config` 的 `defaultRoot == str(db_file_path(settings).parent / "datasets" / "ai-trade")`（绝对 `db_path` 与相对 `db_path` 各一例） |
| A-8【v2 · RV8】 | `stop_group` / `end_group` **不等待**收尾（写线程被卡住时二者仍在 200 ms 内返回），随后轮询至 `close_timeout_s + 1s` 内线程结束、manifest `state=="closed"`；`shutdown()`（已暂停 / hold / flatten 三分支）返回时线程已结束或已记 `closeTimedOut` |
| A-9【v2 · RV10】 | 新启动时 monkeypatch 打开失败（`OPEN_FAILED` / `OPEN_TIMEOUT` / `OPEN_BUSY` 各一）⇒ 422 `AI_TRADE_TRAJECTORY_DIR_INVALID`、`reasonCode` 对应、remedies 与 A-3 同形；**`strategy_runs` 行数不变**、`group_status() is None` |
| A-10【v2 · RV9】 | 两成员分组、第 2 个 `_build_member` 抛错 ⇒ 启动失败，recorder 线程在 `close_timeout_s + 1s` 内结束 |
| A-11【v2 · RV9】 | 冷续跑 `_verify_armed(cold=True)` 失败 ⇒ 原异常照抛、recorder 线程结束；再次续跑时任一时刻只有一个写线程 |
| A-12【v2 · RV14】 | `OKX_ACCOUNT_DEMO=true` 启动 ⇒ manifest 与 `episode_start` 的 `env == "demo"`；默认 ⇒ `live` |

**`tests/test_ai_trade_trajectory_compile.py`**

| # | 断言 |
|---|---|
| C-1 | 以 S-1 的产出为输入：`steps.jsonl` 1 行、`calls.jsonl` 4 行；`steps[0].prompt` 等于被采纳调用的完整请求（`messages` 条数 == 该调用的 `messageCount`） |
| C-2 | 开仓回合与平仓回合（跨 epoch、跨段、明文与 gz 混合）各得到一条 `reward.legs`，数值取自平仓腿 `explain.meta`，缺键即缺席 |
| C-3 | 删除一个 blob 行 ⇒ 对应调用 `quality.requestComplete=false`、`report.missingBlobs==1`；制造未解释的序号缺口 ⇒ `report.unexplainedGaps` 有区间、`--strict` 退出码 3 |
| C-4 | 未平仓 ⇒ `reward.settled=false` 且无任何盈亏键 |
| C-5 | 末行撕裂 ⇒ `tornLines==1`，其余照常编译 |
| C-6 | 输入目录置只读 ⇒ 编译成功且输入目录文件 mtime 全部不变 |
| C-7【v2 · RV12】 | 两个 epoch 里 `ledgerSeq=41` 对应不同事件键 ⇒ 两笔都参与配对、`report.ledgerSeqConflicts == 1`；同一 `accountingEventId` 在离线补记与上一 epoch 各出现一次 ⇒ 只计一次 |
| C-8【v2 · RV13】 | `episode_start.accounting={"costBasis":"net","ctVal":0.01}` 时，重放结果与以同一 context 直接调用 `TradeAccountingAccumulator.restore` 逐 trip 相等；删去 `accounting` ⇒ `accountingContextMissing == 1` 且相关 step 带 `context_missing` |
| C-9【v2 · RV4 / RV-P2-7】 | `round{completion:"partial"}` 照常产出 step 且 `quality.roundCompletion == "partial"`；`closed_early` 且 `sawMessageDone` 的摘要调用 `responseComplete == true` |

**`tests/test_ai_trade_trajectory_settings.py`**：`.env.example` 与 `Settings` 的 `ai_trade_trajectory_*` 家族**双向逐键对齐**
（逐字仿 `test_ai_intel_wiring.py:488-512`）；两条启动期校验各一正一反；`_db_url()` 与 `db_file_path()` 指向同一文件（D-3）。

**`tests/test_ai_trade_trajectory_directory.py`**：`normalize_directory` 的引号 / 环境变量 / `~` 展开；`default_root` 的两个分支；
`safe_component` 对非法字符与超长串；Windows 长路径阈值（monkeypatch `sys.platform`）。

### 6.2 前端用例

| # | 文件 | 断言 |
|---|---|---|
| F-1 | `lib/trajectory.test.ts`（新增） | `trajectoryDirError`：未激活 / 无回执 / `ok` ⇒ `null`，`!ok` ⇒ message；`describeTrajectoryReceipt` 六个 state × 暂停与否；`formatBytes` 边界（`null` 不渲染成 `0 B`）；【v2 · RV3】`trajectoryNotice` 的五个分支 |
| F-2 | `components/Agent/AiTradeTrajectoryFields.test.tsx`（新增） | 默认不勾；回测只渲染说明行；配置读取失败 / 运维关 ⇒ 复选框禁用且有原因；失焦才提交目录、逐键输入不触发 `onChange`；Esc 还原；无 `window.aragon` 时不渲染「选择文件夹…」与「打开」；注入桥后点击调用 `pickDirectory` 并提交返回值；`!ok` 回执显示 `role="alert"` 与「改用默认目录」；渲染文本不含 `**` |
| F-3 | `components/Agent/AiTradeTrajectoryStatus.test.tsx`（新增） | 六个 state 的文案与 `data-tone`；`dropped>0` 追加缺口；桌面端「打开目录」调用 `openPath(receipt.directory)`；【v2 · RV2】`requested=true, enabled=false, state="failed"` 时渲染并显示 `lastError.message` |
| F-4 | `lib/aiTradeLaunchSteps.test.ts`（修改） | U-2 的「`cognition` 永不产生阻断错误」改为「`cognition` 恰好只拥有 `trajectoryDirError`，且它在 `READY_INPUTS`、不在 `BANNER_INPUTS`」；U-7 新增：`trajectoryEnabled` 缺省 / `false` 时 cognition 摘要与改前逐字相等，`true` 时以「· 采集开」结尾 |
| F-5 | `components/Agent/launch/AiTradeLaunchWizard.test.tsx`（修改，新增 W-T1…W-T4） | W-T1 勾选采集 + 校验回执 `ok=false` ⇒ 第 6 步启动按钮禁用且兜底行指向第 4 步；W-T2 改用默认目录后按钮恢复；W-T3 422 remedies 两个按钮各自拨动状态并出现就地回执；W-T4 走完整生命周期 `rerender`（`open` 恒真 + 父组件以新引用重渲染）后已勾选的采集与已提交的目录**不被重置**（修过三次的输入回退缺陷的第四道护栏） |
| F-6 | `lib/aiTrade.test.ts`（修改） | `groupStateFromStatus` 与 `buildLifecyclePatch` 各自把 `trajectoryCapture` 原样抄过来（两条独立用例，漏任何一处即红） |
| F-7 | `components/Agent/AiTradeToggle.test.tsx`（修改） | 有效开启时启动体含 `trajectoryCapture`；**未开启时启动体不含该键**（既有 `toEqual` 断言因此一字不改，【v2 · RV3】）；成功后 `setAiTradeConfig` 记住的是 `opts.trajectoryPreference`；运行中且回执 `requested` 时渲染状态条（含 `failed`，【v2 · RV2】） |
| F-8 | `components/Agent/launch/launchWizard.source.test.ts`（修改） | G-0 清单加 `useTrajectoryCapture.ts`；G-1 自动覆盖其与改后的 `useAiTradeLaunchForm.ts` ≤ 800 行（【v2 · RV1】）；新增 G-7：`useTrajectoryCapture.ts` 中 `useEffect(` 出现 0 次 |
| F-9【v2 · RV3】 | `components/Agent/launch/AiTradeLaunchWizard.test.tsx`（修改） | 偏好 `enabled:true` + 配置接口 404 ⇒ 复选框显示未勾且禁用、第 4 步摘要无「采集开」、第 6 步出现 `ai-launch-trajectory-notice`、提交体**不含** `trajectoryCapture` 而 `trajectoryPreference` 保留原选择；配置 `enabled:false` 同形；配置恢复后重开弹窗自动恢复为勾选 |

### 6.3 桌面端用例

`desktop/scripts/dir-access.test.mjs`：`pickDefaultPath` 对非对象 / 相对路径 / 非字符串一律 `undefined`；
`openPathRejection` 用注入的假 `fs` 覆盖 `invalid_path` / `not_found` / `not_directory` / `null` 四态。

### 6.4 验收标准（Definition of Done）

| # | 验收项 | 判定方式 |
|---|---|---|
| AC-1 | 启动面板第 4 步出现「后训练数据采集」卡片，首次打开**默认不勾** | F-2 + 手工 |
| AC-2 | 默认目录由服务端下发、界面显示完整绝对路径；可手输、可（桌面端）选择、可恢复默认；失焦即给出可写性与可用空间 | F-2、A-6、A-7 + 手工 |
| AC-3 | 目录不可用时无法启动，且有一键出路；冷续跑时目录不可用**不影响续跑** | F-5、A-3、A-5 |
| AC-4 | 勾选后运行一轮，磁盘上出现 `manifest.json` 与段文件；记录包含完整请求（system + 多轮 messages + tools + 参数）、完整思考与研判（不截断）、全部工具调用与入参、重试与追问、反思、摘要、门禁降级码、最终动作、执行回执、成交腿 | S-1、S-2、T-3 |
| AC-5 | 勾选与否交易行为、WS 事件逐项一致；不勾选时启动体逐字节不变、不创建任何目录、不启动任何线程（【v2 · RV3 / RV15】） | S-6、A-1、F-7 |
| AC-6 | 写满、断盘、磁盘不足、写线程卡住都不影响交易，且在运行态状态条上可见 | R-3、R-4、R-5、R-6、F-3 |
| AC-7 | 编译命令产出 `steps.jsonl` / `calls.jsonl` / `report.json`；奖励按往返结算回填、缺键不补 0；完整性问题如实报告 | C-1…C-6 |
| AC-8 | 数据文件中不出现 OKX 与 LLM 凭证原文 | R-7、T-8 |
| AC-9 | 质量门禁：`ruff check`（改动的后端文件）、`tsc --noEmit`、本特性全部新增 / 修改用例绿；后端全量与前端全量与合入前相比**无新增红**（既有红见 §六开头） | 实施节点贴出命令输出 |
| AC-10 | `git diff --stat` 只含 §三 列出的文件；兄弟特性的在制改动一行未进本特性的提交 | 提交节点用「干净暂存树」复跑 AC-9 |
| AC-11【v2】 | 暂停 / 停止 / 退出 / 启动与续跑失败的每条路径上：已成交轮次的断面不丢；写线程不泄漏、不双写；桌面端退出只损失 flush 之后的页缓存；打开失败对用户可见 | S-10、A-9…A-11、R-12、R-13、F-3 |

---

## 七、风险与缓解

| # | 风险 | 严重度 | 缓解 |
|---|---|:---:|---|
| R1 | 包装器改变了 LLM 流的语义（事件被改写、取消落点增加、异常被吞），在真实资金热路径上造成行为变化 | 高 | §2.2.2 五条硬约束 + T-1 透明性等价用例 + T-2 AST 门禁；默认关闭时根本不包装（S-6 以 `is` 断言） |
| R2 | 热路径序列化开销（整段上下文 `json.dumps`）拖慢决策轮 | 中 | 每次调用一次、量级毫秒，远小于 LLM 往返秒级；`timing.serializeMs` 进记录、回执可见；若实测超过 50ms，优化方向是按消息对象缓存序列化结果（v2，不在本轮） |
| R3 | 磁盘被写满，影响数据库与日志 | 高 | 1 GiB 可用空间下限自动暂停（含启动前校验）；gzip 封口；blob 去重；状态条与回执显示字节数与可用空间 |
| R4 | 凭证或隐私写入文件 | 高 | 事实层面 payload 不含凭证（兄弟特性已核实）；写线程逐行剔除 OKX 与 LLM 凭证原文（R-7）；不记录 `_api_key` / `_base_url`；数据只写本机；DATASET_CARD 写明数据含交易策略与账户规模信息，外发前应自行审阅 |
| R5 | 文件 IO 阻塞事件循环 | 高 | 全部 IO 在专用写线程；目录探测与打开走 `to_thread` + 死线；R-9 以线程标识断言事件循环线程零文件 IO |
| R6 | 写线程或文件句柄泄漏（清组路径漏关、测试不关） | 中 | 【v2 · RV9】所有者帧 + `_clear_group_if_current` + 按 `group_id` 单写者登记，`atexit` 仅兜底；用例 `finally` 里关闭；A-8 / A-10 / A-11 / R-12 断言线程结束 |
| R7 | **（实施前发现，不在本轮修）** `openai_client._on_tool_call` 对后续参数分片发出 `id=None` 的 `tool_use_args_delta`（`openai_client.py:160-162` 取 `call.get("id")`，OpenAI 流式协议后续分片只带 `index`），而 `decider._stream_decision` 以 `if ev.id in accs` 过滤（`decider.py:908-910`）⇒ 对严格遵循 OpenAI 流式协议的 provider，决策入参可能只剩首个分片、解析失败降级 hold | 中（存量缺陷） | 本特性如实记录 `argsDeltaWithoutId` 并在编译报告中汇总，使该现象在数据里可见；修复属于 provider 解析层，另立诊断（`bug-diagnosis`），评审结论：**建议立即以 bug-diagnosis 单独立项**，不并入本特性（RV-P2-10） |
| R8 | 摘要调用被消费方 `break`，记录的落盘时机依赖 asyncgen 终结钩子 | 低 | 调用 id 在开流时即登记进回合，编译器按 id 关联、不依赖写入顺序；T-4 覆盖 `closed_early` |
| R9 | 冷续跑时目录不可用 / manifest 损坏 | 中 | 打开失败只进回执不抛（A-5）；manifest 读失败视为无上一份、离线补记标 `unavailable` |
| R10 | 同一轮成交腿被盯市 tick 与决策轮交错采集，承载记录类型与「谁产生」不一致 | 低 | 以 `trade_count` 水位保证每笔恰采一次；编译器只按成交自身字段与 `ledgerSeq` 归因（§2.6、§2.11.5） |
| R11 | 混合文件暂存错误，把兄弟特性的在制改动带进本提交（或反之） | 高 | §3.6 的锚点隔离 + 行级精确暂存配方 + 干净暂存树复跑（AC-10） |
| R12 | `useAiTradeLaunchForm.ts` 越过 800 行红线（【v2 · RV1】实测 780） | 中 | 净增预算 ≤ 12 行，取数 / 谓词 / 提交体全部在 `useTrajectoryCapture.ts`；G-1 门禁自动判红 |
| R13 | 前端「输入约 1 秒回退」缺陷第四次复发 | 中 | 规则值住在受控状态层且只经 `[open]` effect 复位；卡片草稿只跟随**已提交值**；W-T4 用例 |
| R14 | 老后端 + 新前端：勾上了却静默不采 | 中 | 配置接口读取失败即禁用复选框（§2.13.3 第 2 条）；运行态只认服务端回执；【v2 · RV3】单一有效值谓词 + 第 6 步 `notice` 行 + 未开启不发该键 |
| R15 | 格式演进破坏既有数据集 | 低 | 每行 `v`；改名 / 删除必须加版本；读侧缺键即缺席；编译器按 `v` 分支 |
| R16 | Windows 路径长度、OneDrive / 杀软文件锁导致写失败 | 中 | 启动前长路径校验（§2.12.2 第 3 条）；默认目录避开 Documents；写失败进 `degraded` / `failed` 并可见，从不重试忙等 |
| R17【v2】 | 桌面端退出只走 `quit-pause`（2 秒）随后强杀，lifespan 与 `atexit` 跑不到 | 中 | 每批 flush + `note_paused` 触发 fsync；`shutdown()` 以不压缩、≤ 1 s 死线关闭；读侧兼容明文段与撕裂末行（RV7、R-13） |
| R18【v2】 | 采集层的失败路径泄漏写线程或造出双写者 | 中 | 所有者帧 + 按 `group_id` 单写者登记（RV9、A-10、A-11、R-12） |

---

## 八、实施顺序（每步结束都应能单独跑绿本步用例）

1. **设置与路径真源**：`config.py` 10 项 + 校验器、`.env.example` 登记、`db/session.py::db_file_path`；
   用例 `test_ai_trade_trajectory_settings.py`。
2. **纯函数层**：`rules.py` / `wire.py` / `directory.py` / `records.py`；用例 `…_directory.py` 与 T-9。
3. **写入器**：`manifest.py` / `writer.py` / `recorder.py`；用例 R-1…R-14。
4. **采集点**：`channel.py` / `llm_tap.py`；用例 T-1…T-11。
5. **会话与 manager 接线**：W1–W11（W3 拆为 W3a / W3b）、所有者帧与 `shutdown()` 关闭、`group_status` 回执（含
   `requested`）；用例 S-1…S-12、G-T1。
   动手前先 `rg -l "session.py" services/quant/tests` 列出所有扫描 `session.py` 源码的门禁并跑一遍基线。
6. **API**：`schemas.py`、`api/ai_auto.py`、`api/ai_trade_trajectory.py`、`main.py`；用例 A-1…A-12。
7. **编译器**：`reader.py` / `rewards.py` / `compile.py`；用例 C-1…C-9（与采集链路解耦，工期不足可整段顺延，RV-P2-18）。
8. **桌面端 IPC**：`dir-access.js` + 测试、`preload.js`、`main.js`。
9. **前端**：类型与 API → 纯函数 → 卡片 → 步骤模型与表单 → 第 6 步 → 运行态状态条 → 两条抄写通路；
   用例 F-1…F-9。
10. **文档**：README 一段、`.gitignore`。
11. **回归**：后端相关用例文件 → 后端全量（`wait_async`）→ `tsc --noEmit` → vitest 全量；对照 §六开头的既有红清单。
12. **手工验收**（有真实 LLM 与纸面 / 实盘账户时）：勾选采集跑满 3 轮 → 暂停 → 继续 → 停止 → 编译 →
    打开 `steps.jsonl` 核对一轮的 `prompt.messages` 条数、`completion.thinking` 非截断、`reward` 与界面盈亏一致。

---

## 九、交给评审节点重点核对的清单

> v2：8 条已逐条答复，见文首「评审记录 · §九 核对清单的逐条答复」。

1. §0.2 的每一条源码引用（尤其 `thinking_delta` 不在服务端累积、摘要 `break`、蒸馏复用决策客户端）。
2. §2.2.2 的五条硬约束是否足以保证透明性；`__getattr__` 透传私有字段是否会让某个 `hasattr` 判断改变分支。
3. §2.3 的 W3 锚点在 HEAD（无兄弟特性）与工作树上是否都唯一；W7 放在 `_maybe_session_exit` 之后是否会漏掉该 tick 之后才落账的成交（下一次同步会补上，但承载记录不同）。
4. §2.5 的判定表是否穷尽 `_decide_frozen` 的全部控制流（含墙钟闸 `deadline_hit` 阻止重试 / 追问的路径）。
5. §2.8 的线程方案与本仓 PyInstaller 打包形态、Windows 文件锁的相容性。
6. §2.10.3 把目录校验放在端点而非解析链的理由，是否与冷续跑的全部入口一致。
7. §2.13.2 把 `trajectoryDirError` 放进 `READY_INPUTS` 而不进 `BANNER_INPUTS`，对第 6 步兜底行的文案是否足够明确。
8. R7 是否应在本轮之外立即立项。

---

## 评审结论

**有条件通过**（v2）。

- **P0 0 条；P1 15 条全部就地修入正文**（RV1–RV15，落点以【v2 · RVn】标注）；P2 19 条中 11 条已顺手修入，其余 8 条
  为开放项或决策记录，均不阻塞本轮。
- 方案主干成立且与现栈契合：LLM 客户端边界的透明装饰器是唯一能拿到「完整请求 + 完整流式输出」的位置（§0.2 引用逐条
  属实）；默认关闭即不包装、不建目录、不起线程，交易热路径零 await、零判定参与；零 DB 迁移、零新增关停步骤、不依赖
  兄弟特性的在制代码。v1 的问题集中在失败路径与真实退出路径上，v2 已给出逐路径的写点、关闭点与用例。

**通过的条件**（交给「代码开发」与「代码 Review、修复与提交」节点逐条兑现）：

1. **按 v2 正文施工，不回退到 v1 写法**：尤其 RV1（表单净增 ≤ 12 行）、RV4（W3a / W3b 与作用域三态机）、RV7 / RV8
   （关闭按调用方分档）、RV9（所有者帧 + 单写者登记）、RV11–RV13（参数、成交身份、会计口径的唯一真源）。任何偏离
   写进本文件「实施过程发现的方案缺陷」段，写明原因与替代做法。
2. **动手前先跑门禁基线**：`rg -l "session.py" services/quant/tests` 列出的全部源码扫描用例，以及
   `launchWizard.source.test.ts` 的 G-0 / G-1 / G-2；改完后 `useAiTradeLaunchForm.ts` 以 G-1 口径 ≤ 800 行。
3. **混合文件行级精确暂存 + 干净暂存树复跑**（§3.6、AC-10）：`session.py` / `manager.py` / `config.py` / `main.py` /
   `.env.example` 上兄弟特性的在制改动一行不得进本提交。
4. **R7（RV-P2-10）另立 bug-diagnosis**，不并入本提交；RV-P2-9 / 11 / 15 保持开放、不阻塞。
5. **手工验收**（§八 第 12 步）至少覆盖一次「勾选采集 → 跑 3 轮 → 暂停 → 继续 → 桌面端退出 → 重开冷续跑 → 停止 →
   编译」，核对 `round.completion`、`lateCalls`、`env`、`requestParams.temperature` 与 `reward` 同界面盈亏一致。

---

## 实施过程发现的方案缺陷

> 代码开发节点 · 2026-09-11。每条都已按「修正后的做法」施工，编号 I-n；行号为当前工作树。

### 后端

- **I-1 · 写线程生成的记录没有序号语义（§2.7.2 信封示例未区分）。** 信封示例把 `seq` 写成恒为整数，但
  `segment_open` / `segment_close` / `blob` 与写线程补写的 `gap` 由写线程生成：若占用通道序号，热路径与写线程
  就要抢同一个计数器（须加锁），且这些记录会在编译报告里制造假缺口。**做法**：这四类记录 `seq=null`，连续性
  检查只看 `seq` 非空的记录（`wire.envelope` docstring 与 DATASET_CARD 写明）。
- **I-2 · `gap` 有两种，spec 只写了一种。** §2.6 的「台账被裁剪」缺口（`ledgerSeq` 区间）与 §2.8 约束 2 的「记录被
  丢弃」缺口（`seq` 区间）字段同名、语义不同。**做法**：加 `scope` 键——`record`（写线程写，`seq=null`，
  `fromSeq/toSeq` 是记录序号）与 `ledger`（通道写，自身占用一个记录序号，`fromSeq/toSeq` 是 `ledgerSeq`）。
- **I-3 · 「每批 flush」改为无缓冲写。** 带缓冲的文件对象上 `OSError` 会延迟到之后某次 flush 才冒出来，约束 10
  「单条写入失败 ⇒ 该条丢弃并计数」无法归属到条。**做法**：段文件以 `buffering=0` 打开，每条记录（连同它的 blob
  行）一次 `write()` 交给 OS——强杀时只丢页缓存，严格强于约束 6；写失败后丢弃当前段句柄、下一条另开新段，绝不把
  新数据接在半截行后面（`writer.py` `_append` / `_taint`）。
- **I-4 · RV-P2-16 按 §3.1 的静态导出做不到。** `python -m app.ai_auto.trajectory.compile` 会先执行包的
  `__init__.py`，静态导出会连带加载 recorder / channel / llm_tap（及 provider 层）。**做法**：`trajectory/__init__.py`
  用 PEP 562 `__getattr__` 惰性导出（与 `app/ai_auto/__init__.py` 同一范式），调用方 `from .trajectory import X` 写法不变。
- **I-5 · 会话侧接线会打红 55 条既有用例。** `test_ai_trade_api.py` / `test_ai_trade_group.py` /
  `test_ai_trade_resume_cold.py` / `test_ai_trade_pause_api.py` 以替身会话替换 `AiTradeSession`，替身没有
  `attach_trajectory`（实测 `AttributeError`）。**做法**：`_build_group` 接线循环用
  `getattr(member.session, "attach_trajectory", None)`，与同一循环里的 `install_integrity` 同款；W9 / W10 同样
  `getattr(session, "trajectory", NULL_TRAJECTORY)`。
- **I-6 · `close_episode` 先写出在飞作用域。** RV-P2-19：`stop()` 只 cancel 不 await 决策任务，其 W5 可能晚于
  `episode_end` 到达——那时通道已直通，被 cancel 的那一轮（可能已成交、作用域为 `staged`）会整条丢失。**做法**：
  `close_episode` 在写 `episode_end` 之前按 W5 规则写出仍为 `open` / `staged` 的作用域。
- **I-7 · 打开时没有目录复核。** 冷续跑不经端点，§2.10.4 的 `_prepare_group_dir` 只「建目录」。**做法**：线程内先跑
  同一个 `check_directory`（空间下限、长路径、程序目录全部同源），失败即 `failed` 回执，`reasonCode` 取判定码
  （如 `TRAJECTORY_DIR_NOT_WRITABLE`）而非笼统的 `OPEN_FAILED`；新启动同样适用（端点探测与打开之间目录被拔的竞态）。
- **I-8 · 若干接口加法。** `GroupRecorderRequest` 加 `bar`（manifest 要写）；`_GroupBuildCtx` 加
  `trajectory_open_error`（把冷续跑的失败原因从 W11 带到 `AiTradeGroup` 的唯一构造点）；`close_nowait(reason, *,
  compress_active=True)` 多一个关键字参数（`aclose` 据它不压活动段）；`request_flush()` 的标记挂在
  `recorder.last_flush`（用例据它确定性等待）；`_trajectory_episode_end()` 对执行器字段用 `getattr`（写点绝不在
  停止路径上抛）；`round_aborted` 记录多带 `ts`。
- **I-9 · 回执路径分隔符。** §2.9 示例写正斜杠；实现一律 `str(Path)`（Windows 为反斜杠），与 §2.12.2 `DirVerdict.wire()`
  的「原样反斜杠」一致。
- **I-10 · `shutdown()` 的 hold 分支。** `pause_group` 可能抛 `AI_TRADE_SETTLEMENT_UNRESOLVED`；照「return 之前关闭」
  写，抛出时写线程不会被关闭。**做法**：`try/finally` 包住，写线程恒被有界关闭。
- **I-24 · 轮转时没有刷新 manifest（用例 `test_manifest_is_rewritten_on_every_rotation` 实测判红）。** §2.7.4 的写时机含
  「每次轮转」，初版 `_rotate` 漏了：强杀落在两次 flush 之间时 manifest 缺刚封口的段，甚至指着已被压缩删除的明文名，
  冷续跑的合并会把这份陈旧清单带进后续全部 manifest。**做法**：`_rotate` 末尾刷新 manifest（此刻封口段已改名 `.gz`）。
- **I-25 · 「有界」关闭在线程池占满时不成立。** §2.8 约束 8 写的是 `await asyncio.to_thread(join, timeout)`：默认线程池
  被占满时那次 `join` 本身先排队，死线失效——而 `shutdown()` 背后是桌面端 2 秒后的强杀。**做法**：`aclose` 与单写者
  登记的「等旧线程结束」一律在事件循环上轮询 `is_alive()`（20 ms 一跳，`recorder.wait_thread_end`），不占线程池。
- **I-26 · 写失败留下 0 字节段。** 拔盘期间每次重试都会 `open("xb")` 成功、首行写失败，留下一串登记在 manifest 里的空段。
  **做法**：`_taint` 丢弃句柄时，一个完整行都没写成的段直接删除并移出段清单。
- **I-27 · `safe_component` 放行 `.` / `..`。** 二者在 §2.7.1 的正则里是合法字符，却会让 `members/<instId>` 指回上级
  目录。**做法**：替换为 `_`（OKX 的 instId 不会出现这种值，属纵深防御）。
- **I-28 · 压缩失败只进属性、不进回执。** `compress_failures` 不在 §2.9 冻结的 11 个计数里，故只在 recorder 属性与日志中
  可见（保留明文段、读侧兼容，不影响数据完整性）；登记为开放项，不改回执形状。
- **I-29 · 段文件名跨 epoch 不是时间序（编译器节点实测复现）。** §2.7.1 的 `seg-<UTC 秒>-<epoch8>-<nnnnn>` 把随机的
  `epoch8` 排在秒级 UTC 之后：冷续跑在同一秒内打开时，后一个 epoch 的段名可能排在前一个之前，与「文件名字典序即
  时间序」矛盾。**做法**：`seg-e<epoch 序号 4 位>-<UTC>-<epoch8>-<nnnnn>.jsonl`（序号 = 上一份 manifest 的 epoch 数 + 1）；
  段文件相对路径上界随之 143 → 149，Windows 根目录上限推得 ≤ 110，100 的阈值不变。
- **I-30 · 离线补记按计数判定会永久漏掉离线平仓腿（编译器节点复现）。** §2.6 的 `captured < trade_count` 判据：上一进程
  采到一笔落库失败、仅留内存的成交（水位虚高），离线期 `settle_closed_offline` 又补记一笔平仓，回灌后
  `trade_count = len(DB 行)` 恰等于旧水位 ⇒ `catchUp:"none"`，那笔往返永远结算不了且不留任何缺口——RV12 的同一形状出现在
  采集侧。**做法**：manifest 成员条目加 `lastAccountingEventId`；补记起点先在回灌台账里按它定位，找不到则回看
  `CATCH_UP_LOOKBACK = 64` 笔（多补无害，编译器按事件键去重）。用例 `test_s9_catch_up_finds_its_start_by_event_identity_not_by_count`。
- **I-31 · 写线程 `failed` 之后的丢失在段文件里不可见。** 进入 `failed` 后被丢的记录只进 manifest 计数、不写 gap（关闭时
  也不补尾部缺口），序号检查恒报完整。**做法**：编译报告 `integrity.droppedWithoutGap = manifest.counters.dropped −
  Σ record gap`，非零即完整性问题（`--strict` 退出码 3）。
- **I-32 · `wire.envelope` 形参 6 个超过 CLAUDE.md 的 5 个。** **做法**：拆出 `origin(epoch, groupId, instId, runId)`，
  `envelope(kind, seq, owner)`；记录形状逐字节不变。

### 离线编译器

- **I-33 · step id 取 `groupId:instId:epoch:roundNo`。** 冷续跑是新会话，轮号从 1 重新计数，§2.11.4 的
  `groupId:instId:roundNo` 会撞。
- **I-34 · `ledgerSeqConflicts` 按 run 跨 epoch 计。** §2.11.5 写「同一 epoch 内」，但同一 epoch 内水位保证 `ledgerSeq`
  唯一，字面判据永不触发；C-7 本就写「两个 epoch 里」。
- **I-35 · 读取容错的两处细化。** 末行没有换行但能完整解析为 JSON 对象 ⇒ 保留、不计撕裂（JSON 对象的真前缀不可能
  是合法 JSON 对象）；`missingBlobs` 按段内去重后的 sha 计，不按引用次数计。
- **I-36 · 输入识别。** 分组目录以 `manifest.json` **或** `members/` 识别；manifest 读不到照样编译并在报告里标
  `manifest.readable=false`；`--out` 位于 `--src` 之内 ⇒ 退出码 2（否则第二次编译会把上次的输出当输入）；manifest 未登记
  的 epoch 按其最早 `wallTs` 插入次序。
- **I-37 · 超出 §2.11.3 / §2.11.4 的加法字段。** 奖励腿带 `tripId / grossPnlUsd / feeUsd / tradingNetUsd / coverage`（RV13
  的会计口径只作用在这几个值上，不输出它们口径就不可见）；steps 带 `epoch / seq / reasoning / action.error /
  execution.summaryInstalled / elapsedMs / quality.responseComplete`；calls 带 `phase / epoch / seq / normalizedUsage`；
  `adopted` 三态（所属回合记录缺失 ⇒ `null`）。
- **I-38 · 质量标注的口径。** `quality.degraded` = `degrade.llm` 非空；`requestComplete` 在 `adoptedCall="none"` 时为
  `null`、`missing` 时为 `false`；`quality.gap` = 「上一条回合类记录 seq+1 → 本轮及其调用的最大 seq」窗口与任一缺口相交。
- **I-39 · `--strict` 的判定集显式列在 `report.integrity`。** 含 `manifestUnreadable / tornLines / invalidLines /
  missingSegments / missingBlobs / unsupportedRecords / unexplainedGaps / droppedRecords / droppedWithoutGap /
  ledgerGapLegs / epochsNotInManifest / ledgerSeqConflicts / accountingContextMissing / accountingIssues /
  serializeErrors`；`duplicateSegments` 不计（数据完整）。
- **I-40 · spec 缺口：部分平仓的回合没有奖励角色。** §2.11.5 只定义最终平仓的 `close_leg` 与开仓 `open_leg`，只做了部分
  平仓的回合 `reward.settled=false`；登记为开放项。
- **I-41 · `TRIP_WIRE_KEYS` 是 `trip_ledger._WIRE_FIELDS` 的副本。** RV-P2-16 禁止编译器 import 在线模块，由漂移门禁用例
  `test_reward_wire_keys_mirror_trip_ledger_wire_fields` 钉住两者一致。
- **I-42 · 规模。** `reader.py` 314 / `rewards.py` 249 / `compile.py` 506 行（预算 160 / 170 / 300），均 ≤ 800；按成员逐个
  编译并按 sha 去重 blob，峰值内存约为单个成员去重后的明文体积。

### 前端

- **I-11 · fetch 调用点门禁必红（§3.4 漏列文件）。** `components/ui/contrastTokens.source.test.ts` 冻结 `lib/**` 的
  fetch 调用点与文件数；新增 `trajectoryApi.ts` 使其 23→24 / 21→22。**该行已被兄弟特性 journal 改过（混合行）**：
  提交节点在干净暂存树上应为 **22 个调用点 / 21 个文件**（HEAD 21/20 + 本特性 1/1），工作树为 24 / 22。
- **I-12 · WCAG 2.5.3。** §2.14.2「打开后训练数据目录」不含可见文字「打开目录」；§2.13.3 第 9 条复选框
  `aria-label="采集后训练数据"` 不含可见长句。**做法**：按钮 aria-label 改为「打开目录（后训练数据）」；复选框去掉
  aria-label，由外层 label 提供可访问名（可见文字「采集后训练数据（默认关闭）」，说明挪到第二行）。
- **I-13 · 向导内 Esc 还原不可达。** `Modal` 在 window 捕获期监听 Esc 并 `stopImmediatePropagation` + 关窗（既定 N-7
  语义）。卡片照样实现还原（F-2 覆盖）；弹窗里按 Esc 即关窗、未提交草稿作废、重开由 seed 复位，不泄漏。
- **I-14 · `trajectoryNotice` 的判定序。** React Query v5 后台重取失败时同时给出 `isError` 与旧 `data`；先判 `isError`
  会出现「实际在采（`effectiveEnabled` 读 data）」却提示「本次不会采」。**做法**：先看 data（`trajectoryConfigBlocker`）。
- **I-15 · 第 6 步去向行错报目录。** `verdict.directory ?? defaultRoot` 在自定义目录校验在途时会把去向说成默认目录。
  **做法**：`verdict?.directory ?? rule.directory ?? defaultRoot`（hook 字段 `destination`）。
- **I-16 · 复选框禁用却无原因。** 默认未勾时 `notice` 为 null ⇒ 404 / 运维关时复选框禁用却没有原因。**做法**：卡片直接
  用 `trajectoryConfigBlocker(config)`；配置在途时也禁用。
- **I-17 · 空 message 被放行。** 后端 `message` 为空串时 `?? "目录不可用"` 不生效，`ready` 判真。**做法**：
  `plainText(message) || "目录不可用"`，后端文案在渲染入口统一剥掉 `**` 与反引号。
- **I-18 · RV-P2-11 已顺手修。** 第 6 步 remedies 区只在含 `ops_override` 时渲染运维总闸三个落点（W-16d 与既有 A-1
  断言照绿）。
- **I-19 · 规模。** 卡片 297 行（预算 260）、`lib/trajectory.ts` 240 行（预算 130）、`aiTradeTypes.ts` +16（预算 +12），
  均 ≤ 800；`useAiTradeLaunchForm.ts` 净增 10 行，G-1 口径 791 ≤ 800。

### 用例

- **I-20 · S-6 的「逐项相等」须剔除墙钟字段。** 摘要种子带 `summaryAsOfTs=<墙钟>`，两次运行必差毫秒；比较前归一。
- **I-21 · A-8 的 200 ms 阈值。** 替身会话的停止与归档本身有数据库写，本机偶发超过 200 ms；取 1 秒（仍远小于被卡住的
  10 秒与 5 秒关闭死线），足以证伪「在等写线程」。
- **I-22 · Windows 上 pytest 的 `tmp_path` 会超过 100 字符根目录上限。** 走目录校验的用例一律
  `tempfile.mkdtemp(prefix="trj")`。
- **I-23 · 本机既有：pytest 进程在全部用例通过之后退出时崩溃（EXIT=139）。** faulthandler 栈为
  `asyncio/proactor_events.py:116 __del__ → :82 __repr__`（垃圾回收期访问冲突）。`HEAD` 干净 worktree 上跑
  `tests/test_ai_trade_pause_api.py` 同样复现（15 passed 之后 EXIT=139），与本特性无关。**判定跑测结果须看
  「N passed / M failed」汇总行，不能看进程退出码。**

---

## 终审记录（代码 Review、修复与提交 · 2026-09-11）

> 方法：四路并行评审（采集链路 / 写线程与端点 / 离线编译器 / 前端与桌面端），每条结论逐行实读源码与调用方核实，
> 只保留真缺陷，全部在源码里修掉并各补回归用例。工作树上并行在制的兄弟特性 `ai-trade-audit-journal` 与本特性逐文件分离：
> 24 个纯本特性修改文件整文件暂存，`session.py` / `manager.py` / `config.py` / `main.py` / `.env.example` /
> `contrastTokens.source.test.ts` 六个混合文件按 §3.6 行级精确暂存（后者在干净暂存树上为 22 个调用点 / 21 个文件）。

### 修掉的缺陷

| # | 严重度 | 缺陷 | 失败场景 | 修法 | 回归用例 |
|---|---|---|---|---|---|
| R-1 | P1 | 盯市 tick 抢走在飞回合自己的成交腿 | 盯市循环是另一个任务，`_execute_decision` 之后到 W3b 之间的每个 await（落库、推送、摘要调用、`_post_round`）都可能插进 W7；成交进了 `fills{monitor}`、`round.fills` 为空，而 `rewards._place` 只把 `round` 承载的腿记进该轮奖励 ⇒ 真正开了仓的那一轮在 steps.jsonl 里空成交、未结算 | 在飞回合拥有它窗口里的全部成交：作用域未关闭时 W7 / W8 / W9 一律让路，由回合收尾取走；`close_episode` 释放作用域之后补取剩余腿 | `test_s2_a_monitor_sync_inside_the_round_never_steals_its_own_fill` |
| R-2 | P1 | 回合内部触发的会话止盈 / 止损被标成 `cancelled` 且丢成交腿 | `_post_round → _maybe_session_exit → stop()` 跑在决策任务内部：W8 先取走全部成交，`close_episode` 再把暂存断面写成 `partial/cancelled`、`fills: []`；训练侧按「被打断」过滤会删掉 episode 最后、奖励最大的一步 | `close_episode` 释放作用域用 `abortReason="episode_closed"`；配合 R-1 成交留在本轮；DATASET_CARD 登记 abortReason 三值 | `test_s10_a_session_stop_inside_the_round_is_episode_closed_not_cancelled` |
| U-1 | P1 | 422 出路「本次不采集数据」实际关掉了以后每一次 | remedy 拨 `trajectory.enabled=false`，启动成功后经 `trajectoryPreference` 被记住 ⇒ 下次打开默认不勾，正在积累的数据集静默断档（§5.4 记住选择恰是为了防这个） | 新增 `trajectorySkipOnce`（受控状态层、只经 `seed()` 复位）：本次启动整键不发、偏好原样；第 4 步卡片上重新表态或点「改用默认目录」即撤销 | `W-T3b` |
| R-3 | P2 | flatten 退出仍压缩活动段，RV7 的 ≤ 1 秒收尾失效 | flatten 分支会话停止经清组先发一次默认压缩的 `close_nowait`，随后 `aclose(compress_active=False)` 空转 ⇒ 几十 MiB 活动段赶在桌面端 2 秒强杀之前开始 gzip，manifest 写不到 `closed` | `recorder.skip_active_compression()`（只降不升），`shutdown()` 动手之前调用，写线程收尾时读 | `test_skip_active_compression_outranks_an_earlier_default_close`、`test_a8_flatten_shutdown_never_compresses_after_the_clear_group_close` |
| R-4 | P2 | `shutdown()` 只有 hold 分支在 `finally` 里关写线程 | 已暂停分支 `_touch_state_row` / flatten 分支 `_stop_session` 抛错 ⇒ 写线程不关、manifest 停在 recording | 三个分支统一包进一个 `try/finally` | `test_a8_a_failing_shutdown_branch_still_closes_the_writer` |
| R-5 | P2 | 目录探测误伤系统盘根目录下的新文件夹 | `C:\` 默认只授予已认证用户「创建文件夹」不授予「创建文件」，探针在祖先里建文件失败 ⇒ `C:\ai-trade-data` 被判不可写、启动被挡 | 探针与真正的打开同形：先建子目录、再在其中建文件，两者都删 | `test_the_write_probe_creates_a_folder_first_like_the_real_open` |
| R-6 | P2 | Windows 非法字符 / 保留名通过校验、启动时才 422 | `Path.exists()` 对 `a<b`、`CON` 只回 False，探测走到可写祖先判「可用」，`mkdir` 才 WinError 123 | win32 下静态拒绝（`TRAJECTORY_DIR_UNSUPPORTED`） | `test_windows_forbidden_names_are_rejected_before_probing`（6 例） |
| R-7 | P2 | 写线程崩溃时仍在队列里的记录不计丢弃 | 既不写也不计 `dropped`、不归还预算 ⇒ 回执 `pendingBytes` 永不归零、编译报告 `droppedWithoutGap` 看不见、等 flush 标记的一方挂住 | `_discard_pending`：本批余下与队列全部计 `after_failure`、归还预算、置位 flush 标记 | `test_a_crash_counts_every_queued_record_as_dropped_and_returns_the_budget` |
| R-8 | P2 | 相对的 `AI_TRADE_TRAJECTORY_DEFAULT_DIR` 让默认根目录不可用且无出路 | 被判「请填写完整的绝对路径」，而默认根目录的 422 只剩「本次不采集」 | 相对路径以数据库文件所在目录为根（`config.py` / `.env.example` 同步说明） | `test_a_relative_default_dir_is_anchored_at_the_database_directory` |
| R-9 | P2 | DATASET_CARD 仍写 I-29 之前的段文件名 | 按说明卡解析文件名的训练侧脚本在每个文件上失败 | 改为 `seg-e<epoch 序号>-…`，补 abortReason / 成交归属 / `closed_early` 三条约定；本文 §2.1、§2.7.1、§2.7.4 同步 | — |
| R-10 | P2 | 删除 0 字节段后 `counters.segments` 虚高 | 回执段数比 manifest 段清单多 | `note_segment_discarded`（与 `note_segment_opened` 对称） | — |
| C-1 | P2 | `droppedWithoutGap` 跨 epoch 互相抵消 | 上一 epoch 强杀前未刷新 manifest（登记 0、gap 行 5）+ 本 epoch 写线程失败丢 3 条 ⇒ `max(0, 3−5)=0`，`--strict` 放行 | manifest 每个 epoch 记 `dropped`，编译器逐 epoch 比；旧 manifest 退回合计口径 | `test_dropped_without_gap_is_counted_per_epoch_so_epochs_never_offset_each_other`、`test_dropped_without_gap_falls_back_to_the_total_without_epoch_counts` |
| C-2 | P2 | 输出写失败以 traceback + 退出码 1 结束 | Windows 上 steps.jsonl 被编辑器占用：文件新旧混杂，退出码不在文档内 | 捕获 `OSError` ⇒ 中文提示 + 退出码 4（docstring 与 `--help` 同步） | `test_cli_output_write_failure_exits_4_with_a_message` |
| C-3 | P2 | report.json 带本机绝对路径 | `source` 字段含本机用户名，随数据集外发 | 删除，只留 `groupDir` | 同 C-1 首个用例 |
| U-2 | P2 | 第 6 步在目录错误时仍说「将写入 X」 | 与同屏兜底行「第 4 步有未解决的输入错误」互相矛盾 | 有 `capture.error` 时不渲染去向行 | W-T1 追加断言 |
| U-3 | P2 | 一次配置读取失败锁住整个弹窗会话 | 查询 `retry:false` 且全局不在聚焦时重取；唯一出路是关弹窗重开，而重开会复位向导里填过的一切 | 禁用原因旁给「重试」（`refetch`） | `配置读取失败 ⇒ 原因旁给「重试」…` |
| U-4 | P2 | 「打开」失败直接显示 `not_found` 等英文码 | 用户移动 / 删除目录或拔盘后看到「无法打开：not_found」 | `describeOpenPathFailure`：三个码译成中文、系统文本透传（卡片与状态条共用） | `trajectory.test.ts` 新增 + 卡片 / 状态条用例改断言 |
| U-5 | P2 | 卡片文案承诺「思考与研判全文」 | 只有 Anthropic 且开启扩展思考时才下发思考文本（默认关） | 改为「研判全文（模型下发思考过程时一并记录）」 | — |
| U-6 | P2 | Electron 主进程同步 `statSync` 可卡死整个窗口 | 保存目录在网络盘、NAS 离线时点「打开」，主进程阻塞到 SMB 超时 | `openPathRejection` 改异步 `fs.promises.stat`；假 fs 只提供 `promises.stat`，实现退回同步即判红 | `dir-access.test.mjs` 4 例改异步 |
| U-7 | P2 | `DirectoryActions` 65 行，超 CLAUDE.md 50 行 | — | 拆出 `OpenRootButton` | — |
| U-8 | P2 | `aiTrade.ts` 注释说「窄 patch 不抄它」，下一行正是在抄 | 误导后人删掉那一行 | 改为「若不抄它」 | — |

### 登记不修（开放项）

- **O-1 · 未盖事件键的成交腿窗口。** 盯市 tick 持 `_exec_lock` 挂在 `executor.reconcile`（腿已追加）与 `_dispatch`
  （盖 `accountingEventId` 与平仓 meta）之间时，若决策轮的收尾写点恰好此刻取腿，该腿缺事件键与 trip 读数；编译器在同一
  epoch 内以 `(runId, ledger, epoch, ledgerSeq)` 回退去重、不重复计入，但该腿奖励读数不全。触发需要两段 await 精确交错；
  根治要会话向通道暴露「执行锁是否被持有」的谓词，超出本轮只加法的范围。
- **O-2 · 轮与轮之间的平仓腿不挂任何 step。** `rewards._place` 只把 `round` 承载的腿计入 step 奖励（模块 docstring 的刻意取舍），
  无在飞回合时由盯市触发的平仓腿进 `report.tripsWithoutDecisionRound`；开仓那一轮的 `open_leg` 不受影响。
- **O-3 · I-30「按事件键找不到 ⇒ 回看 64 笔」分支没有编译级用例**（采集级 S-9 覆盖；人工推演编译侧按事件键正确去重）。
- **O-4 · 小瑕疵：** `records.SITE_DECISION` 与 `llm_tap.SITE_DECISION` 重复定义；`TrajectoryChannel.note_error` 的 except
  分支调用的是刚失败的同一函数（为满足 G-T3 门禁的形状）。

### 验证

- 修复后本特性后端用例 **159 passed**（原 142 + 本轮新增 17）；前端受影响目录 vitest **17 文件 481 例全绿**；`tsc --noEmit`
  退出 0；桌面端 `node --test scripts/dir-access.test.mjs` 6/6；`ruff check` 干净。
- 修复前的干净暂存树（不含兄弟特性）后端全量：**6234 passed / 1 failed**（唯一红 = HEAD 既有的
  `test_ai_trade_backstop_repair_session::test_the_session_keeps_only_a_thin_seam`）；前端全量 vitest 246 文件 2940 例全绿、tsc 退出 0。
- **修复后、即本提交的干净暂存树**：后端全量 **6251 passed / 1 failed**（同一条既有红，+17 = 本轮新增用例，13 分 52 秒）；
  前端全量 vitest **246 文件 2944 例全绿**、`tsc --noEmit` 退出 0；桌面端 `scripts/*.test.mjs` 9 个文件逐个 `node --test` 全绿。
  pytest 进程退出码 139 为本机既有（I-23），判定以汇总行为准。
