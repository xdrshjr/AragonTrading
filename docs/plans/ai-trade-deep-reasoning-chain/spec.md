# AI 自动交易 · 深度思维链与对抗式反思（ai-trade-deep-reasoning-chain）

| 项 | 值 |
|---|---|
| 版本 | **v2**（评审节点修订：P0×5 / P1×8 已在正文修复；见《评审记录》与《评审结论》） |
| 状态 | 有条件通过（条件见文末《评审结论》） |
| 影响面 | `services/quant/app/ai_auto/*`、`services/quant/app/agent/llm/*`、`apps/web` AI-trade 轨迹面板 |
| DB 迁移 | **无**（新增读数一律走 `metrics_json` 既有 JSON blob 与进程内形状） |
| 运维回退 | 单键 `AI_TRADE_REASONING_DEPTH=off` ⇒ 三份 prompt **逐字节相同**、**且超时/重试/追问/截断码/wire 加法键一并回到合入前形态**（v2 修订：超时不再改全局默认，改为随档位派生，见 §3.3.5 与 §5.3） |

---

## 评审记录（Review Notes · v1 → v2）

评审人：Anthropic 工程团队资深评审。评审方式：**逐节读 + 逐条取证**——文中每一条根因、每一处
「本仓库先例」、每一个行号引用，都在工作树上实跑 `grep` / 读源码核对过；下表的「取证」列给出的是
**评审自己复核到的位置**，不是照抄 v1 的引用。

评审维度与总体判断：

| 维度 | 判断 | 一句话依据 |
|---|---|---|
| 可行性 | **通过** | 六条轴全部落在现有形状内。三处关键前提**已核实为真**：`StreamEvent` 早有 `stop_reason` / `usage` 两字段（`agent/llm/base.py:79-80`），故截断诚实化无需改 Protocol；`LlmProviderConfig` 是 `@dataclass(frozen=True)`（`agent/providers.py:26`），故 `dataclasses.replace` 造三份预算成立；`desktop/src/lib/config.js:80` 的透传白名单确含 `AI_TRADE_` 与 `AGENT_` 两个前缀，故新 env 无需改桌面壳 |
| 完整性 | **不通过 → 已修** | 8 处边界/错误路径缺失，其中 5 条足以让本特性「上线即半瘫且不报错」：截断码只认 Anthropic 口径、token 用量在 OpenAI 标准恒为 `None`、追问在轨迹流上与首次研判相互覆盖、单轮墙钟无上界、反思协议的注入点在设计图上指错了文件 |
| 一致性 | **不通过 → 已修** | 与 `CLAUDE.md` §二 + `.claude-index/config.md` 的《显式记录的阈值偏离》表直接冲突：该表已为 `build_ai_trade_system_prompt` 登记「13 个形参，**下次触碰应一次性收进 `SystemPromptTexts` 聚合结构**」，而 v1 直接加第 14 个形参且未登记；`prompts.py`(919 行) 与 `decider.py`(746 行) 在本特性后必然越 800 行线且**不在**该表里 |
| 右尺寸 | **基本合适** | 35 文件 / 6 轴对「把两段推理做深 5 倍并让它可观测、可回退」是相称的：轴 A/B 是任务本体，轴 C 是它的物理前提（2048 token 封顶下轴 A 无从落地），轴 D/E 是 5× 成本与 5× 篇幅各自的必要对价。唯一的过度设计候选是 `exhaustive` 档，评审结论保留但加了约束（见 P2-4） |

### P0（阻断合入，必须在实现前修）

| 编号 | 问题 | 取证 | 修法与落点 |
|---|---|---|---|
| **P0-1** | **截断码只认 Anthropic 口径，OpenAI 标准下根因 C-4 原样存活。** v1 §3.3.3 的分派表写死 `stop_reason == "max_tokens"`。但 `openai_client` 把 `choice["finish_reason"]` 原样塞进同一个字段，其取值域是 `length` / `stop` / `tool_calls` / `content_filter`——**永远不会等于 `max_tokens`**。于是在 OpenAI 标准的 provider 上，「预算耗尽在工具调用之前」仍旧表现为 `no_tool_call` → 重试 → 再次耗尽 → 降级 `hold`，界面上依旧看不出真实原因，即 C-4 被宣布修复而实际只修了一半 | `agent/llm/openai_client.py:147-149`（`stop_reason=choice["finish_reason"]`）对 `agent/llm/anthropic_client.py:152`（`stop_reason=delta.get("stop_reason")`） | 已在 §3.3.3 引入 `TRUNCATION_STOP_REASONS = frozenset({"max_tokens", "length"})` 作为**单一真源**，并把 `content_filter` 单列为 `content_filtered` 码；§7.3 新增 D-1b（openai 口径）与 D-1c（`content_filter`）两条用例，两条都以「换一个 provider 口径就必须判红」为价值 |
| **P0-2** | **token 用量在 OpenAI 标准下恒为 `None`，G-8「成本可见」与 G-3 的权威判据一并落空。** OpenAI 的 usage 走 `stream_options.include_usage`，它在**独立的、`choices` 为空的收尾 chunk** 里到达，因而 `_dispatch_chunk` 会发出**第二条** `message_done`；而 `_stream_decision` 在**第一条** `message_done` 上就 `break`，那一条的 `usage` 正是 `None`。后果有三：UI 的 token 累计恒空、`aiStats.reasoning.inputTokens/outputTokens` 恒空、`scripts/ai_trade_cot_probe.py` 的「output token 中位数之比 ≥ 5.0」在该 provider 上**取不到数**。而 R-3（token 成本 5 倍）的缓解措施第 4 条恰恰就是「逐轮与会话级 token 落痕并在 UI 可见」——一条针对本特性头号真金风险的缓解，在半个 provider 家族上是静默失效的 | `agent/llm/openai_client.py:134-139`（`if not choices: if chunk.get("usage"): …message_done`）+ `:61`（`include_usage`）对 `ai_auto/decider.py:360-362`（`elif ev.kind == "message_done": await pump.flush(); break`） | 已在 §3.3.3 把 `_stream_decision` 的收尾语义从「首条 `message_done` 即 `break`」改为「首条 `message_done` 置 `done` 标志并记录读数，随后**继续消费到流自然结束、但只接受后续 `message_done` 做 usage 回填**，其余事件一律丢弃」；§7.4 新增 B-7（OpenAI 双 `message_done` 回填）与 B-8（`message_done` 之后到达的 `text_delta` 不得进入 `last_analysis`，冻结语义） |
| **P0-3** | **浅研判追问会在轨迹流上与首次研判相互覆盖，UI 出现 18k 字符的重复思维链或「文不对题」的研判。** 前端对 `analysis` 是**纯追加**归约，全流程**唯一**的重置点是 `retry` 相位。而 §3.4.2 的追问发生在 `decide()` 的重试循环**之后**、不经过 `_emit_retry`：若追问沿用同一个 `emit`，S1–S12 会被追加成两遍（阶段导轨读出 24/12）；若追问改用 `emit=None`，界面上留着的是**被丢弃的浅研判**，而真正被采纳的深研判用户永远看不到——两种写法都让「界面上的研判」与「实际生效的决策」不是同一份。更糟的是追问**可能被拒收**（更浅 / 失败 → 保留首次决策），此时若已覆盖，界面停在一份系统明确判定为不采纳的文本上 | `apps/web/lib/store.ts:236-237`（`analysis: t.analysis + delta`）与 `:262-272`（`retry` 是唯一清零点）；`ai_auto/decider.py:265-287`（重试循环内才有 `_emit_retry`） | 已在 §3.4.2 把追问的流式产出改到**独立相位** `reask`（前端独立字段 `reaskAnalysis`，绝不覆盖 `analysis`），并在 `result` 帧的 `reasoning` 里新增 `reaskAdopted` 明示两份中哪一份生效；§3.5.2 展开态因此多一段「深化追问（已采纳／未采纳）」。**两份都留在界面上**是刻意的：被拒收的那一份正是「模型第二次想得更浅」的证据，抹掉它等于抹掉追问单向增益这条设计的可证伪性。§7.3 D-4/D-5 扩断言，§7.6 新增 F-9 |
| **P0-4** | **单轮墙钟无上界，且 `off` 档并不是真正的一键回退。** v1 §5.3 把 `ai_trade_decision_timeout_s` 60→210、`ai_trade_reflection_timeout_s` 45→150 **改成全局默认**，而 `ai_trade_decision_max_retries` 默认是 **3**。于是最坏单轮 = 210×4（首次+3 次重试）+210（追问）+150（反思）≈ **20 分钟**，而 `_deciding` in-flight 守卫在这 20 分钟里会把每一根 K 线确认与每一次信号翻转**全部丢弃或压进待办**，默认唤醒周期却只有 **2 分钟**。§3.4.3 的压力提示要求「样本 ≥ 3」才发声，也就是说最坏情况下用户要在近一小时后才第一次被告知交易节奏已被推理吃掉。与此同时，超时默认值的改动**完全不受深度档门控**：`AI_TRADE_REASONING_DEPTH=off` 之后，一次挂死的 LLM 调用仍要 210 秒而不是 60 秒才回落 `hold`——恰恰在「出事了要回退」这条路径上，安全回落的时延被悄悄放大了 3.5 倍，而 §九 的回滚清单里根本没有「超时」二字 | `config.py:171,186`（今日 60.0 / 45.0）、`config.py:175`（`max_retries` 默认 3）、`config.py:234`（默认唤醒周期 2 分钟）；`ai_auto/session.py:1340,1533,1685`（三处 `if self._deciding` 丢弃/记待办） | 已在 §3.1.1 给 `DepthProfile` 增 `decision_timeout_s` / `reflection_timeout_s` / `max_attempts_cap` 三个派生量（`off` 档取 **60.0 / 45.0 / 不封顶**，即逐字回退），并在 §3.3.5 新增**轮级墙钟闸** `ai_trade_reasoning_round_deadline_s`：一轮内「全部尝试 + 追问」共用一份预算，耗尽即停止再发起新尝试（**只停止追加尝试，绝不改变任何安全回落语义**）。§5.3 相应**删除**了两个全局默认值改动。§3.4.3 的压力提示改为样本 ≥ 1 即可发声，且首轮就把真实耗时报出来 |
| **P0-5** | **反思协议的注入点在架构图上指错了文件，照图实现会让轴 B 整条死掉而全部离线用例仍绿。** §3.0 画的是 `manager.py / backtest.py ──► prompts.build_reflection_system_prompt(..., reasoning_text=…)`。事实是：全仓**唯一**的调用点在 `decider._stream_reflection` 里、**每轮现造**，manager 与 backtest 从头到尾没有引用过这个函数。照图实现的结果是 R1–R7 永远进不了发给模型的 `system`，而 §7.2 的 P-5 是直接调 `build_reflection_system_prompt()` 断言的、照样全绿——这正是本仓库反复栽跟头的「写点有、读点无」形状 | `grep -rn build_reflection_system_prompt services/quant/app` 的全部命中：`ai_auto/decider.py:48`（import）、`:504`（唯一调用，位于 `_stream_reflection` 内、每轮现造 `system`）、`ai_auto/prompts.py:778`（定义）。manager.py / backtest.py **零命中** | 已在 §3.0 改正数据流、在 §3.2 明写「复核协议经 `AiTradeDecider(depth_profile=…)` 携带、由 `_stream_reflection` 在每轮现造 `system` 时注入，manager/backtest 只负责把 `DepthProfile` 交给决策器」；§6.1 第 5 项补上该职责。并在 §7.5 新增 **S-7 装配用例**：以假客户端捕获**真实发出**的 `system` 文本，断言决策侧含 `## S12`、反思侧含 `## R7`——判据取自「模型实际收到的那一份」，而不是任何 `build_*` 的直接返回值 |

### P1（必须在本轮修复，已全部落到正文）

| 编号 | 问题 | 取证 | 修法与落点 |
|---|---|---|---|
| **P1-1** | **`reasoningDepth` 从 wire 到决策器的落点未指定，且变更清单漏了 `schemas.py`。** §4.4 给 `/ai-trade/start` 加了可选字段，但 §6.1 的 16 项后端清单里没有 `app/schemas.py`（`AiTradeGroupStartIn` 的定义处），也没有 manager 侧的 `AiTradeGroupStartRequest`。而 §3.1.1 的 `resolve_depth_profile(settings, …)` **只读 settings**——若实现照它写，启动请求里的 `reasoningDepth` 会被接收、校验、然后丢弃 | `app/schemas.py:879-909`（`AiTradeGroupStartIn`）；`ai_auto/manager.py:276`（`start_wire` 字段）；`api/ai_auto.py:392`（`start_wire=body.model_dump(mode="json")`）；`ai_auto/resume.py:80`（冷续跑「用 `start_wire` **重跑一遍**解析链」） | 已在 §4.4 与 §6.1 补齐三处落点，并写明续跑语义：因为 `start_wire` 存的是 `body.model_dump()` 全文、冷续跑重跑同一条 `build_start_request`，**只要深度档落在请求对象上而非只读 settings，暂停→续跑就自动同档**；§7.5 新增 S-8 冻结这一点 |
| **P1-2** | **journal 摘录换源同时改掉了左栏决策卡的正文，而 v1 只按 journal 论证、S-2 也只断言 journal。** `session.py:1627` 那一行算出的 `excerpt` 有**两个**消费者：`commit_round(analysis=excerpt)`（进 journal）与 `_emit_decision(analysis=excerpt)`（进 `decision` 事件 → 左栏决策卡首段 + WS 快照重放）。把它换成 `evidenceDigest` 优先，等于同时重定义了一处用户天天在看的界面文案 | `ai_auto/session.py:1627` → `:1631`（`commit_round(..., analysis=excerpt)`）与 `:1640-1643`（`_emit_decision(..., analysis=excerpt)`）；`ai_auto/session.py:2616-2617` 的 docstring 明写该字段「供左栏决策卡首段」 | 已在 §3.3.4 显式列出两个消费者并说明这是**有意**的（决策卡首段从「S1 样板抬头」变成模型自己写的中文证据摘要，是净改善），§7.5 的 S-2 扩为同时断言 journal 与 `decision` 事件两处取到同一份 |
| **P1-3** | **扩展思考一旦开启，`thinking` 的呈现无归宿。** 轴 C 会让 `thinking_delta` 第一次真的有内容，而今天渲染它的 `<details>「深度思考」` 就长在 `AnalysisBlock` 里——正是 §6.2 第 22 项要**整体取代**的那个组件。v1 全文没有一句交代 `trace.thinking` 去哪。更实际的问题是：那段 `<p className="whitespace-pre-wrap">` 是**一次性整段**渲染的，而 `_TRACE_MAX_CHARS` 提到 20000 之后它最长会是两万字符 | `apps/web/components/Agent/AiTradeTraceRound.tsx:113-140`（`AnalysisBlock` 内含 `trace.thinking` 的 `<details>`，`:119-123`）；`:830`（`<AnalysisBlock trace={trace} />`） | 已在 §3.5.2 增设第 ④ 态：`AiTradeReasoningBlock` **接管** `thinking`，默认折叠、展开时按段落切分并复用与研判段同一套「只渲染尾部若干行」的流式策略；§6.2 第 21/22 项写明这一职责转移 |
| **P1-4** | **三条生产装配链一条用例也没有，而 G-3 的 5× 判据是一个需要真实 API key 的 opt-in 脚本。** §6.3 的 10 个测试文件里，没有任何一条覆盖 `manager.py` / `backtest.py` / `resume.py` 是否真的把协议与三份预算接上了。于是「协议没被装配」这一类缺陷（P0-5 就是它的一个实例）在 CI 里**完全不可见**：所有 prompt 用例都在直调 `build_*`，所有决策器用例都在手工构造决策器 | §6.3 的清单本身；`ai_auto/manager.py:657-700` 与 `ai_auto/backtest.py:322-345` 两处 `AiTradeDecider(...)` 装配点在 §6.3 中无对应用例 | 已在 §7.5 新增 S-7（决策+反思两侧 `system` 的装配断言，实盘车道）、S-9（回测车道同款，且断言取的是**回测档**）、S-8（续跑车道同档），三条都以假客户端捕获真实请求为判据。§7.7 同时补一条**离线**结构性判据作为 G-3 的 CI 替身：`off` 与 `deep` 两档渲染出的系统 prompt 字符数之比 ≥ 5——它证不了模型会不会真的多写，但能挡住「协议根本没进 prompt」这一整类静默失效 |
| **P1-5** | **压力提示在「关闭定时唤醒」的会话上恒为真，且 wire 字段名与算法不符。** 判据是 `ewma_ms > 0.6 × wake_interval_s × 1000`；而 `wakeIntervalMinutes = 0` 是一个**合法且有语义**的入参（= 关闭墙钟定时唤醒，只靠 K 线与信号翻转唤醒）。此时右侧恒为 0，判据退化成 `ewma_ms > 0` ⇒ 恒真，界面会**永远**挂着一条「研判耗时逼近唤醒周期」并给出一个由除以 0.6 现算、毫无意义的建议周期。另外字段叫 `p50Ms`（中位）而值是 α=0.3 的 EWMA，§3.5.3 的界面文案还写成「中位 3.1 分钟」——三处口径不一 | `app/schemas.py:899-901`（`wakeIntervalMinutes`：「0→关闭定时唤醒」）；`config.py:234-236`（默认 2 分钟 / 下限 30 秒 / 上限 240 分钟） | 已在 §3.4.3 补两条硬条件：`wake_interval_s <= 0` ⇒ **整键缺席**（关掉定时唤醒的用户本就不按周期交易，对他提周期建议是噪音）；`suggestIntervalMin` 按 `ai_trade_wake_interval_ceil_min` 夹紧。字段统一改名 `ewmaMs` 并把界面文案改为「近期均值」；§7.5 的 S-4 增补这两条分支 |
| **P1-6** | **§5.1 的内存容量估算只算了四个累积槽位里的一个，也没算多标的分组。** 写的是「20 轮 × 20000 字符 ≈ 400KB/会话」。实际上 `_TRACE_MAX_CHARS` 是**单 phase 单轮**上限，而一轮最多有 `analysis` / `thinking` / `reflection` / `reask`（末者为 v2 新增）**四个**槽位各 20000 字符；`_traces` 又是**每个成员一份**，多标的分组按成员数倍增。真实量级是 20×4×20000 ≈ **1.6MB/成员**，一个 5 标的分组即 8MB 常驻，并且 WS 快照重放会把它整份推给每个新订阅者 | `ai_auto/trace.py:50-54`（三个常量的注释明写「单 phase 累计文本硬上限」）；`ai_auto/session.py:378`（`deque(maxlen=_TRACES_MAX)`，每会话一份）；`:410`（订阅队列 `maxsize=1000`） | 已在 §5.1 按四槽位 × 成员数重算并给出显式的**每轮合计**上限常量 `_TRACE_ROUND_TOTAL_MAX = 36000`（四槽位共享，而非各自 20000），使最坏量级回到 ~720KB/成员；§3.3.4 的常量表同步 |
| **P1-7** | **`evidenceDigest` 被要求写中文，却没有任何校验，而它同时是三处中文界面的取值源；G-5 因此没有可证伪判据。** 它是 journal 摘录的首选源（P1-2）、收起态标题、决策卡首段。模型在一份**通篇要求用英文思考**的 prompt 下回一段英文摘要是完全可能的，届时三处中文界面同时变英文，而 G-5「面向操作员的产出仍是中文」的判据栏只写了「UI 阶段名、摘要、状态文案全中文」——那说的是前端静态文案，不是模型产出 | §1.1 G-5 判据栏；§3.1.7 的 schema（只有 `maxLength: 400`，无语言约束）；§7.3 D-11 只断言「不产生 degrade」 | 已在 §3.1.7 增一条**纯呈现层**的轻量守卫 `is_probably_chinese(text)`（CJK 字符占比 < 0.3 ⇒ 视作未提供，回落到 §3.3.4 的第 ②/③ 级来源），并明写它**绝不产生 degrade、绝不影响任何交易语义**；G-5 判据栏补上这条；§7.3 新增 D-12 |
| **P1-8** | **与 `CLAUDE.md` §二 + `.claude-index/config.md` 的阈值偏离登记表直接冲突。** 该表已经为 `build_ai_trade_system_prompt` 登记了「13 个形参」并写明**下一次触碰应一次性收进 `SystemPromptTexts` 聚合结构**——v1 §4.1 恰恰是在「下一次触碰」，做的却是加第 **14** 个位置形参。另外 `prompts.py` 今日 **919 行**、`decider.py` **746 行**：前者要加整条英文脊柱 + 三个英文子句孪生 + 英文工具描述，后者要加 `_StreamOutcome` / `ReasoningMeta` / 追问 / 三客户端，两者在本特性后必然越过 800 行线，而**两者都不在**偏离表里。`CLAUDE.md` 明写「调整必须在 `.claude-index/config.md` 中显式记录，不得静默放宽」 | `.claude-index/config.md:160-162`（`MAX_FILE_LINES=800` / `MAX_FUNCTION_PARAMS=5`）、`:171-177`（偏离表首行即 `build_ai_trade_system_prompt` 的 13 形参登记与其修法指示）；`wc -l`：`prompts.py` 919、`decider.py` 746 | 已在 §3.1.6 把英文脊柱整体外移到**新模块** `ai_auto/prompts_en.py`（与既有 `reasoning_protocol.py` / `prompt_glossary.py` 同族），使 `prompts.py` 只增一个分叉判断；§4.1 把新增文本参数收进 `SystemPromptTexts` 聚合 dataclass、**兑现**偏离表里那条指示（形参 13→6）；§6.1 增第 **4b** 项 `prompts_en.py`、第 **16b** 项 `.claude-index/config.md`（登记 `decider.py` 的越线与本轮已还的债；行号用 `4b`/`13b`/`14b`/`16b` 后缀是为了不打乱既有 1–35 的编号，那些编号已被 §九 的实施顺序引用），§九 的实施顺序同步 |

### P2（建议，不阻断；本轮已按建议落字，实现节点可再权衡）

| 编号 | 问题 | 处置 |
|---|---|---|
| **P2-1** | **三处阶段标题正则口径不一。** `STAGE_HEADING_RE` 允许 0–3 个前导空格且 `#{2,3}\s*`（`##S1` 也算）；前端解析器是它的镜像；但**第三个**消费者 `apps/web/lib/markdown.ts:8` 的 `HEADING_RE = /^(#{1,6})\s+(.*)$/` 要求行首无空格且**至少一个空格**。三者不一致时，模型写的 `##S1` 会被前两者计为阶段、被第三者渲染成普通段落文字 | 已在 §3.1.1 把协议正文的要求收紧为「行首顶格 `## S<k>`、`#` 与 `S` 之间恰好一个空格」，并把 `STAGE_HEADING_RE` 收敛到同一口径；§7.2 的 P-6 增补 `##S1`（无空格）与 `   ## S1`（缩进）两种输入的期望 |
| **P2-2** | **弹窗「研判深度」选择器与回测 tab 的归属未定。** 启动弹窗的请求体里 `base` 对象是实盘/回测两 tab **共用**的（`wakeIntervalMinutes` 就因此被显式限定为「仅 live 分支带上」）。若深度选择器落进 `base`，用户在回测 tab 选 `deep` 会直接推翻 §3.6「回测独立默认 `standard`」的成本护栏 | 已在 §3.5.3 明写选择器**仅实盘 tab 露出**、随 live 分支下发；回测 tab 按 §3.6 只做**只读展示**当前回测档 |
| **P2-3** | **`_stream_reflection` 把 `thinking_delta` 与 `text_delta` 喂进同一个 `reflection` phase**（`decider.py:511-512`）。§3.2 若照抄该处累积 `reflection_parts`，思考文本会混进 `_last_reflection_analysis`，令 `reflection_stages` 的 R1–R7 计数与实际不符 | 已在 §3.2 明写 `reflection_parts` **只收 `text_delta`**；phase 扇出维持今日形状不变（那是呈现层的合并，与留存无关） |
| **P2-4** | **`exhaustive` 档（16384 token / 200 词每阶段）无验证路径。** §十 待定项 3 自陈只提供开关 | 评审判定：**保留四档**（删除的收益确实极小，而 `exhaustive` 是给「日线级别、一天几轮」这类真实场景留的门）。但已在 §5.3 要求 `_check_reasoning_budget` 对 `exhaustive` 额外校验一条：该档下 `decision_max_tokens` 可能超过部分模型的 `max_tokens` 上限，故校验失败的报错文案必须点名「这是模型侧上限，不是本地配置错误」；§7.4 的 B-6 增补该档 |
| **P2-5** | **术语表漂移门禁（G-2′）判红后的正确动作没写。** 这条用例的价值就是会因为**别的特性**新加一个中文标签而判红——但一个不知情的实现者最省事的做法是删断言 | 已在 §3.1.5 与 §7.2 的 G-2′ 里写明：判红的**唯一**正确动作是把新标签登记进 `GLOSSARY`；并写明本条用例的 owner 是本特性 |

### 复核后判定为「不改」的三条（记录理由，避免下轮重提）

1. **`response_model` 静默吞 wire 键（§4.3 的 ⚠️）**——已复核 `api/ai_auto.py:507-513`：`/ai-trade/status` 返回裸 `dict` 且**无** `response_model`，其 docstring 甚至专门写明「该端点 `-> dict` 且无 `response_model`，故新键不会被 FastAPI 静默吞掉」。v1 的警告是正确的防御性提醒，但此处不构成待办，故降为一句备注。
2. **不改 `LLMClient.stream` 签名、预算走构造参数（§3.3.1 / R-9）**——已复核：`services/quant/tests` 下 **32 个文件**实现了 `stream`，其中 **42 处**是固定签名 `async def stream(self, *, system, messages, tools)`、只有 5 处是 `**_kw`。给 Protocol 加一个生产上恒会传的关键字参数会一次性打翻 42 处。v1 的取舍**正确且论据充分**，予以确认。
3. **五段宪章与证据渲染层保持中文（§1.2 / §十 待定项 1）**——予以确认，不追加轴 G。理由采纳 v1 的三条，并补一条评审自己的观察：`_context` 里累积的是「决策 prompt + 结构化工具入参 + tool_result」而**不含**模型的研判正文（`decider.py:606-610`），所以证据层的语言不会随思维链变长而被反复重读放大；语言混排的成本是一次性的、且被 S9 的英文复述这一可审计动作兜住。**但**：G-5 必须有可证伪判据，见 P1-7。

---

## 一、概览（Overview）

【AI 自动交易】当前每一轮决策（定时唤醒 / K 线确认 / 信号翻转三种唤醒源）都会向决策 LLM 发一次
带唯一放行工具 `submit_trade_decision` 的流式请求，模型先输出一段自然语言研判、再调用工具提交决策；
若该轮产生交易意愿（`open_long` / `open_short` / `close`），还会再发一次独立的下单前反思（ReAct 校验）
子调用 `submit_trade_reflection`。这两段推理是整个自动交易系统里**唯一由模型自主完成的部分**——其余
（铁律层、利润保护、仓位求解、成本 edge 门禁、持仓周期、离场优先级）都是确定性纯核心。也就是说，
系统的上限由这两段推理的质量决定，而它们目前很浅：决策侧的「分步研判」只是系统 prompt 纪律 #1 里
一句 839 字符的中文长句所列的 8 个圈序号（① 价格走势 …… ⑧ 结论），没有任何一步规定要输出多少、
要回答哪些具体问题、要如何证伪自己；反思侧更薄，只有 5 个复核角度 + 3 条分级标准，共 731 字符。
更致命的是传输层：`agent_max_tokens = 2048` 是**整个后端共用的一个上限**，决策请求的「研判文字 +
工具调用 JSON」必须一起挤进这 2048 个输出 token——模型即使想深想，物理上也没有地方写。

本特性把这两段推理从「一句话提示 + 2048 token 预算」升级为**受协议约束、可度量、可观测、可回退的
深度思维链**：新增一份纯核心的《审议协议》（Deliberation Protocol，决策侧 12 个阶段 S1–S12）与
《对抗式复核协议》（Adversarial Review Protocol，反思侧 7 个阶段 R1–R7），每个阶段都有**明确的
必答问题、最小篇幅要求与产出形态**；把推理语言切成英文（含一条显式的「用英文思考、用中文向操作
员汇报」语言契约），因为本仓库的推理指令层此前是中文长句，而英文短句式的协议在同等 token 下能表达
更多的结构约束；把 LLM 预算从「全后端共用一个 2048」拆成**按调用点分配的三份预算**（决策 8192 /
反思 5120 / 摘要 1536），并顺带给 Anthropic 标准通道加上可选的扩展思考（extended thinking）开关
——`thinking_delta` 事件在归一化层早就解析好了，只是从来没有人往请求体里写过那个字段。

深度不是免费的，所以本特性的另一半是**诚实**：新增截断检测（`stop_reason == "max_tokens"` 不再被
静默吞成 `no_tool_call`）、一次且仅一次的「浅研判追问」（追问只提升质量、**永不**因为研判太浅而把
决策降级成观望）、逐轮 token 用量与研判长度的落痕、以及一条「研判耗时逼近唤醒周期」的运维提示。
呈现层同步重做：一段 5 倍长的英文思维链如果原样糊在中文界面的右侧窄栏里，就是一次人机交互的倒退。
新的《分阶段研判视图》按稳定的阶段标记把英文全文切段、用中文阶段名索引、流式时只展示**当前阶段 +
进度导轨**、结算后折叠并以模型自己给出的中文摘要（新的 `evidenceDigest` 工具字段）作为收起态标题。

### 1.1 目标（Goals）

| 编号 | 目标 | 可证伪判据 |
|---|---|---|
| G-1 | 决策思维链**明确以思维链方式**逐阶段展开，覆盖 12 个规定阶段 | 系统 prompt 含 `S1…S12` 全部阶段与各自 mandate；`count_stages(analysis) ≥ 10` 的轮次占比 ≥ 80%（探针脚本实测） |
| G-2 | 反思同样是**深度**推理，而非一句结论 | 反思 prompt 含 `R1…R7`；反思文本中位长度 ≥ 决策研判的 40% |
| G-3 | 强化后的思维链长度 **≥ 现状 5 倍** | `scripts/ai_trade_cot_probe.py` 对同一 provider 分别以 `depth=off`（= 合入前形态）与 `depth=deep` 跑 K 轮，断言输出 token 中位数之比 ≥ 5.0 |
| G-4 | 相关提示词用英文，且模型**用英文思考** | 决策/反思系统 prompt、协议、工具描述、任务指令、追问提示全英文；语言契约要求 100% 推理用英文 |
| G-5 | 面向操作员的产出仍是中文 | `reason` / `evidenceDigest` / `exitEvidence` 三个字段强制简体中文；UI 阶段名、摘要、状态文案全中文 |
| G-6 | 深度不得换来「不确定时下单」 | 全部安全回落语义**逐字不变**：超时/错误/无工具/非法入参 → `hold`；反思 fail-closed；浅研判**永不**降级 |
| G-7 | 一键运维回退 | `AI_TRADE_REASONING_DEPTH=off` ⇒ 系统/决策/反思三份 prompt sha256 与合入前相同 |
| G-8 | 成本可见 | 逐轮 `inputTokens/outputTokens` 落痕，会话级累计进 `metrics_json.aiStats.reasoning`，UI 可见 |

### 1.2 非目标（Non-goals）

- **不翻译证据渲染层**：`market_text` / `wake_text` / `hold_horizon_text` / `cost_text` / `sizing_text` /
  `profit_guard_text` / `acceptance_text` / `discipline_view` 与 `prompts._format_*` 共 ~150KB 中文渲染
  逻辑保持原样。理由三条：(a) 它们是**数据行**（数字与枚举本身语言中立），推理语言由指令层决定而非
  数据标签决定；(b) 它们分属另外 10 个特性、带 sha256 冻结门禁与上百条字面量断言，与「思维链深度」
  同一提交里改会让 diff 不可评审；(c) 本特性用两条手段消除语言混排的风险——一份**术语对照表**
  （轴 A-4）与阶段 S9 的「用英文复述每一条已装配铁律及其阈值」的强制要求，后者反而把语言边界变成了
  一次可审计的理解力校验。
- **不改任何确定性安全层**：`apply_reflection`（只能收紧）、`quantize_size_fraction`、`validate_limit_intent`、
  铁律层、利润保护、edge 门禁、持仓周期、离场优先级——本特性一个字节都不碰。
- **不改唤醒调度**：不动 `wake_runtime` / `wake_guard` / `wake_backlog` 的判据，只新增一条**只读的**
  「研判耗时逼近唤醒周期」提示（轴 D-4）。
- **不做多模型编排**（多个 Agent 互辩）：本轮只强化单模型的单次推理深度与一次独立复核。

---

## 二、现状取证与根因分析

### 2.1 实测基线（本文档编写时在工作树上实跑）

```
build_ai_trade_system_prompt("BTC-USDT-SWAP","30m",1000.0,3.0)  → 1145 字符
  └ 其中「决策纪律」整段                                          →  839 字符
build_reflection_system_prompt()                                 →  731 字符
DECISION_TOOL_DESCRIPTION                                        →  194 字符
REFLECTION_TOOL_DESCRIPTION                                      →  301 字符
```

### 2.2 六条根因

| 编号 | 根因 | 取证位置 |
|---|---|---|
| C-1 | **输出预算物理封顶 2048**，且与对话式 Agent Copilot 共用一个字段；研判文字与工具 JSON 必须挤在同一预算里，模型只要多写就会挤掉工具调用 | `config.py:892 agent_max_tokens=2048`；`providers.py` 的 `LlmProviderConfig.max_tokens` 恒取自它；`anthropic_client._body` / `openai_client._body` 原样写入 |
| C-2 | **思维链只有一句提示、没有协议**：8 个圈序号写在纪律 #1 的一句话里，无每步必答项、无篇幅要求、无证伪环节、无置信度校准 | `prompts.py:129-136` |
| C-3 | **反思比决策还浅**：5 个复核角度 + 3 条分级标准，且反思推理文本**服务端完全不留存**（只作为 trace delta 扇出），事后无法度量、无法沉淀 | `prompts.py:778-811`；`decider.py:495-533`（无 `_last_reflection_analysis`） |
| C-4 | **截断被静默吞掉**：`message_done` 只用于 `break`，`stop_reason` 被丢弃；一旦输出预算耗尽在工具调用之前，表现为 `no_tool_call` → 重试 → 同样耗尽 → 最终降级 `hold`，而日志与界面上**看不出**真实原因是预算不足 | `decider.py:363-372`；`_RETRYABLE_DEGRADE_REASONS` 含 `no_tool_call` |
| C-5 | **扩展思考通道建好了却从未启用**：归一化层解析 `thinking_delta`、trace 有 `thinking` phase、前端有「深度思考」折叠块，但请求体里从来没有 `thinking` 字段 | `anthropic_client._body`（无 thinking）↔ `anthropic_client._on_block_delta`（解析 thinking_delta）↔ `AiTradeTraceRound.tsx:119` |
| C-6 | **深度提上去会立刻打翻三处硬上限**：`_ANALYSIS_MAX_CHARS=4000`、`_TRACE_MAX_CHARS=4000`（单 phase 单轮）、journal 摘录取研判**前 400 字符**——第三条尤其恶劣：12 阶段协议下的前 400 字符恰好是 S1 的样板抬头，长期记忆会被样板文喂满 | `decider.py:69`、`trace.py:47`、`session.py:133,1627` |

### 2.3 一句话总结

> 思维链浅**不是**模型不愿意想，而是：没有协议告诉它想什么（C-2/C-3）、没有预算让它写下来（C-1）、
> 写超了会被静默吞掉（C-4）、写出来了也留不住（C-3/C-6）。四条同时修，深度才会真的落地。

---

## 三、技术设计

### 3.0 总体架构

```
                     settings.ai_trade_reasoning_depth  ──┐
                                                          │ resolve_depth_profile()
                                                          ▼
                                          ai_auto/reasoning_protocol.py（新·纯核心）
                                          ├ DepthProfile(off|standard|deep|exhaustive)
                                          ├ DECISION_STAGES  S1..S12（英文 mandate）
                                          ├ REVIEW_STAGES    R1..R7（英文 mandate）
                                          ├ build_deliberation_protocol(profile) -> str
                                          ├ build_review_protocol(profile)       -> str
                                          └ count_stages(text, prefix) -> int
                                                          │
             ai_auto/prompt_glossary.py（新）  GLOSSARY_BLOCK（zh→en 术语表）
                                                          │
                                                          ▼
             ai_auto/prompts_en.py（新·v2）  英文脊柱 + 三个英文纪律子句孪生
                                                          │
                                                          ▼
   manager.py / backtest.py ──► prompts.build_ai_trade_system_prompt(..., texts=SystemPromptTexts(…))
                             ──► llm_budget.build_ai_trade_llm_clients(cfg, settings, profile=…)
                                     ├ decision  client（max_tokens=8192, thinking?）
                                     ├ reflection client（max_tokens=5120）
                                     └ summary   client（max_tokens=1536）
                             ──► AiTradeDecider(..., depth_profile=profile)
                                                          │
                                                          ▼
   decider.py ── _stream_decision ──► 捕获 stop_reason / usage / 全文（≤20000）
              ── _shallow_reask   ──► 至多 1 次深度追问（永不降级，独立 `reask` 相位）
              ── _stream_reflection ► build_reflection_system_prompt(horizon, reasoning_text=
                                        build_review_protocol(profile))   ★ **每轮现造**
                                      同款捕获，新增 _last_reflection_analysis
                                                          │
                                                          ▼
   session.py ── result 事件新增 reasoning{…} ──► WS agent_trace ──► store.ts
                                                          │
                                                          ▼
   apps/web/lib/aiTradeReasoning.ts（新）  parseReasoningStages(text) -> Stage[]
   apps/web/components/Agent/AiTradeReasoningBlock.tsx（新）  分阶段研判视图
```

> **【v2 评审修订 · P0-5】两条 prompt 的装配点不对称，图上必须画准。**
> 决策系统 prompt 由 manager / backtest 在**会话装配时构造一次**，交给 `AiTradeDecider` 长期持有；
> 而**反思系统 prompt 全仓只有一个调用点**——`decider._stream_reflection`（`decider.py:504`），
> 它在**每一轮**用当轮 payload 的持仓周期补充段现造。manager 与 backtest **从未**引用过
> `build_reflection_system_prompt`。因此复核协议（R1–R7）**只能**经 `AiTradeDecider(depth_profile=…)`
> 携带进去，任何「在 manager 里把 `reasoning_text` 传给 `build_reflection_system_prompt`」的写法都会
> 落空：轴 B 整条死掉，而 §7.2 的 P-5（直调 `build_reflection_system_prompt()` 断言）照样全绿。
> 这正是本仓库反复出现的「写点有、读点无」形状，故 §7.5 用 **S-7 / S-9** 两条**装配用例**冻结它——
> 判据取自假客户端捕获到的、**模型真正收到的那一份** `system` 文本。

---

### 3.1 轴 A —— 决策侧《审议协议》（Deliberation Protocol）

#### 3.1.1 新纯核心 `services/quant/app/ai_auto/reasoning_protocol.py`

无副作用、无外部依赖（只依赖 `dataclasses` / `re`），可离线单测。

```python
"""AI 自动交易深度思维链协议（ai-trade-deep-reasoning-chain 轴 A / 轴 B）。

纯核心：不读 settings、不碰 IO。`resolve_depth_profile` 是 settings → profile 的**唯一**转换点，
prompts / decider / session / backtest 一律只认 `DepthProfile`，杜绝「四处各读一次 settings」的漂移。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# 阶段标记的**唯一真源**：模型被要求逐字输出 `## S1` … `## S12` / `## R1` … `## R7` 作为行首。
# [v2 评审修订 · P2-1] 口径收紧为「行首顶格 `##` + 恰好一个空格 + `S|R` + 数字」。原先的
# `^\s{0,3}#{2,3}\s*` 会把 `##S1`（无空格）与 `   ## S1`（缩进）也计为阶段——但这条标记有**三个**
# 消费者，第三个是 `apps/web/lib/markdown.ts:8` 的 `HEADING_RE = /^(#{1,6})\s+(.*)$/`，它要求行首
# 顶格且至少一个空格。三者口径不一致时，`##S1` 会被前两者计为阶段、被渲染层当成普通正文——
# 表现是「导轨说有 12 段，展开后某一段的标题混在上一段正文里」。三处必须是同一条口径。
STAGE_HEADING_RE = re.compile(r"^## (S|R)(\d{1,2})\b", re.MULTILINE)

DEPTHS = ("off", "standard", "deep", "exhaustive")


@dataclass(frozen=True)
class DepthProfile:
    """一档深度的全部派生量（单一真源：预算、篇幅、追问、超时倍率都从这里取）。"""

    name: str                 # off | standard | deep | exhaustive
    enabled: bool             # False ⇒ 全链路逐字节回退（off 档）
    words_per_stage: int      # 每阶段最小篇幅（英文词），写进协议正文
    decision_max_tokens: int  # 决策调用的输出预算
    reflection_max_tokens: int
    min_analysis_chars: int   # 浅研判追问阈（英文字符）；0 ⇒ 不追问
    # ---- [v2 评审修订 · P0-4] 超时与尝试次数随档位派生，**不再**改 config 的全局默认 ----
    decision_timeout_s: float     # 单次决策尝试超时；off 档 = 今日 60.0（逐字回退）
    reflection_timeout_s: float   # 单次反思尝试超时；off 档 = 今日 45.0（逐字回退）
    max_attempts_cap: int         # 单轮「首次 + 重试」的尝试次数上限；0 ⇒ 不封顶（off 档）
    round_deadline_s: float       # 单轮墙钟总闸（全部尝试 + 追问共享）；0 ⇒ 不设闸（off 档）


_PROFILES = {
    "off":        DepthProfile("off", False, 0, 0, 0, 0, 60.0, 45.0, 0, 0.0),
    "standard":   DepthProfile("standard", True, 60, 4096, 3072, 1500, 150.0, 100.0, 3, 420.0),
    "deep":       DepthProfile("deep", True, 120, 8192, 5120, 3000, 210.0, 150.0, 2, 600.0),
    "exhaustive": DepthProfile("exhaustive", True, 200, 16384, 8192, 6000, 420.0, 240.0, 2, 1200.0),
}
```

> **【v2 评审修订 · P0-4】为什么超时必须挂在档位上、而不是改 `config.py` 的全局默认。**
> v1 §5.3 的写法是把 `ai_trade_decision_timeout_s` 60→210、`ai_trade_reflection_timeout_s` 45→150
> 改成**全局默认**。那样做有两个后果，都不可接受：
> 1. **`off` 档不再是回退。** 关掉深度档之后，一次挂死的 LLM 调用仍要 210 秒而非 60 秒才回落
>    `hold`——恰恰在「出事了要止血」这条路径上，安全回落的时延被悄悄放大 3.5 倍，而 §九 的回滚
>    清单里根本没有「超时」二字。回退开关必须回退**全部**行为，否则它就不是回退开关。
> 2. **`max_attempts_cap` 是必需的第二道闸。** `ai_trade_decision_max_retries` 默认 **3**
>    （`config.py:175`），210×4 + 追问 210 + 反思 150 ≈ **20 分钟**，而默认唤醒周期是 **2 分钟**
>    （`config.py:234`）；这 20 分钟里 `_deciding` 守卫会把每一根 K 线确认与每一次信号翻转全部
>    丢弃或压进待办（`session.py:1340,1533,1685`）。深度档下把有效尝试次数收到 2，配合 §3.3.5
>    的轮级墙钟闸，最坏单轮回到 10 分钟以内。
>
> `resolve_depth_profile` 之外**不得**有第二处读取超时的地方；`decider` 一律取
> `self._depth.decision_timeout_s`，`self._depth is None`（既有直接构造 / 32 个假客户端用例）
> ⇒ 逐字回退到 `settings.ai_trade_decision_timeout_s`（今日行为，零回归）。

`resolve_depth_profile(settings, *, backtest: bool = False) -> DepthProfile`：

1. `name = settings.ai_trade_reasoning_depth_backtest if backtest else settings.ai_trade_reasoning_depth`；
   未知值一律回落 `"deep"`（`Literal` 已在 pydantic 层挡住，这里是二次保险，**不抛异常**——一个
   拼错的运维开关不该让整个会话起不来）。
2. 覆盖：`ai_trade_reasoning_max_tokens > 0` ⇒ 覆盖 `decision_max_tokens`；
   `ai_trade_reflection_max_tokens > 0` ⇒ 覆盖 `reflection_max_tokens`；
   `ai_trade_reasoning_min_chars >= 0` 且显式非默认 ⇒ 覆盖 `min_analysis_chars`。
   覆盖用 `dataclasses.replace`，返回新的 frozen 实例。

#### 3.1.2 十二个阶段（`DECISION_STAGES`）

每个阶段是一个 `ReasoningStage(id, code, title, mandate)`；`mandate` 是英文祈使句列表。
实现者**逐字**采用下表的英文（这是本特性的产品文案，不是示意）：

| id | code | mandate（英文，写进 prompt） |
|---|---|---|
| S1 | `REGIME` | Read the higher-timeframe context strictly in the order 1D → 12H → 4H → 1H. For each frame state: trend direction, structure (higher-highs / lower-lows / range), the nearest support and resistance with its distance in %, and whether RSI is at an extreme. End with one sentence naming the dominant regime and one sentence naming the single strongest piece of evidence **against** that regime. Mark any missing or stale frame explicitly; never infer a frame that was not given. |
| S2 | `EXECUTION` | Analyse the execution bar: direction, momentum, moving-average structure, position inside the recent range, and distance to the range edges. State whether the execution bar agrees or disagrees with S1, and quantify the disagreement. |
| S3 | `TACTICAL` | If tactical frames are supplied, judge **timing only**: has a valid breakout or pullback formed on this frame, is volume confirming, or is this noise? Tactical frames may confirm or delay an entry; they may **never** on their own reverse the S1 conclusion. If no tactical frame is supplied, say so in one line and move on. |
| S4 | `FLOW` | Volume and participation: current volume vs its mean, volume ratio, expansion/contraction trend, and any anomaly. Map each observation to a directional claim ("rising volume into a breakout supports continuation") rather than restating the number. |
| S5 | `FACTORS` | Take every supplied factor reading (RSI / MACD / Bollinger / ATR / momentum …) one at a time. For each: the value, what it claims directionally, and how much weight it deserves **in the current regime** (a mean-reversion factor in a strong trend deserves little). Do not list a factor you were not given. |
| S6 | `SIGNALS` | Adjudicate the signal board **one strategy per paragraph**. For each strategy state: its current stance and intent, whether its precondition actually holds in the current regime, and your verdict — adopt / doubt / overturn — with the specific evidence that drives the verdict. A signal is a lead, never an order. Explicitly name at least one signal you are **not** following and why. |
| S7 | `CONFLICT` | Resolve the conflicts you have just surfaced. Assign each block of evidence (S1–S6) an explicit weight and justify the ranking. State the net directional lean and its strength on a 0–1 scale. If the evidence is genuinely balanced, say so — a forced lean is worse than an honest tie. |
| S8 | `PRIOR` | Bring in history: the consensus quality prior, the measured track record, and every injected experience lesson. For each lesson state whether the current situation matches its context; a lesson whose context does not match must be discarded **out loud**. Give the base rate you are implicitly assuming for the trade you are considering. |
| S9 | `BOOK` | Restate **in English**, one line each, every armed hard-constraint section that appears in your system prompt or in this round's evidence — base-strategy MA band, profit protection, position sizing and drawdown brake, trading cost, holding horizon, exit priority — together with its numeric thresholds as given. Then state, for the action you are leaning towards, whether it satisfies each of them. Also state the current real position, session P&L versus budget, remaining take-profit / stop-loss headroom, and the round-trip cost in bps that this action must overcome. |
| S10 | `HYPOTHESIS` | Put at least three candidate actions on the table (one of them must be `hold`). For each: entry logic, the price or condition that would prove it wrong, a realistic target, the approximate win probability, and the resulting expected value net of the cost from S9. Rank them. |
| S11 | `FALSIFY` | Pre-mortem. Assume your top candidate has already lost money. Write the most likely story of how that happened. Then steel-man the opposite side: the strongest possible argument for the reverse action, stated as its own advocate would state it. List the three specific observations that would force you to abandon your candidate. If S11 changes your mind, say so and go back to S10 — that is the point of this stage. |
| S12 | `VERDICT` | Conclude. Name the chosen action, the size fraction with its justification, and a calibrated confidence (state what you would need to see to move that confidence up or down by 0.2). Name the single observation you will look for next round. Then, and only then, call the decision tool. |

#### 3.1.3 协议正文渲染 `build_deliberation_protocol(profile) -> str`

```
# DELIBERATION PROTOCOL — MANDATORY

Think in an explicit chain of thought. Before the tool call you MUST write out your
reasoning as {N} numbered stages, in order, each opened by a line that begins exactly with
`## S<k>` followed by the stage code. Stages are not optional and may not be merged.
Write at least {words_per_stage} words per stage. Do not summarise a stage away with
"as above" — every stage must stand on its own, because a later reader will read stages
in isolation.

Reason from evidence to conclusion, never from conclusion to evidence. Whenever you make
a claim, name the input line it comes from. If an input is missing, stale, or contradicts
another input, say so at the stage where you first notice it — silent smoothing over a
gap is the single most expensive mistake you can make here.

## S1 REGIME
<mandate>
...
## S12 VERDICT
<mandate>

# OUTPUT BUDGET DISCIPLINE
Your entire response for this turn is capped at {decision_max_tokens} output tokens, and the
final tool call must fit inside the last ~{reserve} of them. Pace yourself: if you are past
stage S10 and approaching the cap, compress the remaining stages to their conclusions and
call the tool anyway. **An unfinished analysis with a tool call is recoverable; a complete
analysis with no tool call is a lost round and the system will fall back to `hold`.**
```

`{N}` / `{words_per_stage}` / `{decision_max_tokens}` / `{reserve}` 均由 `DepthProfile` 与
`settings.ai_trade_reasoning_reserve_tokens` 现算——**绝不写死算例**（沿用 `format_horizon_charter`
按本会话真实杠杆现算解除阈的先例）。

#### 3.1.4 语言契约 `LANGUAGE_CONTRACT`（常量，逐字采用）

```
# LANGUAGE CONTRACT
Conduct 100% of your reasoning in English. Every stage of the deliberation protocol, every
intermediate note, and every internal argument must be written in English.

Some input blocks below are rendered with Simplified-Chinese labels (the numbers, symbols
and enum values inside them are language-neutral). A glossary is provided. Reading Chinese
labels is expected; writing Chinese reasoning is not.

Three fields are the exception, because a human operator reads them on a Chinese-language
console. Write these in Simplified Chinese, and only these:
  - `reason`          — the operator-facing justification of the decision.
  - `evidenceDigest`  — the operator-facing digest of what you adopted and what you overturned.
  - `exitEvidence`    — the named new evidence for a high-conviction exit.
Everything else you write is English.
```

#### 3.1.5 术语对照表 `services/quant/app/ai_auto/prompt_glossary.py`（新）

一个 `GLOSSARY: tuple[tuple[str, str], ...]`（中文标签 → 英文术语），渲染成一个紧凑块：

```
# GLOSSARY (labels you will see in the evidence blocks)
当前持仓=current position | 浮盈=unrealised P&L | 会话累计盈亏=session P&L | 预算=budget
止盈=take-profit | 止损=stop-loss | 均线通道=moving-average band | 上轨/中轨/下轨=upper/mid/lower band
利润保护=profit protection | 保本线=breakeven line | 移动止盈=trailing take-profit
换手迟滞=turnover hysteresis | 最短持仓=minimum hold | 离场连续确认=exit confirmation streak
体制=market regime | 共识=consensus | 先验=prior | 战术周期=tactical frame | 唤醒原因=wake reason
...
```

**这个模块存在的唯一理由是可审计性**，因此它必须带一条会真的判红的漂移门禁（见 §7.2 用例 G-2）：
测试从 `prompts.py` + 8 个 `*_text` 模块的源码里正则抽出所有出现在 f-string / 字面量里的
「【…】」段标题与冒号前中文标签，断言它们**全部**出现在 `GLOSSARY` 左列里。新增一个中文标签而
忘了登记 ⇒ 用例判红（本仓库前科：`.env.example` 无扫描用例，漏登记是静默的）。

> **【v2 评审修订 · P2-5】判红之后的唯一正确动作。**
> 这条用例会因为**别的特性**新加一个中文证据标签而判红，这正是它存在的理由。但对一个不知情的
> 实现者来说，最省事的做法是删掉断言。故此处与 §7.2 的 G-2′ 同时写明：**判红的唯一正确动作是把
> 新标签登记进 `GLOSSARY` 左列**；放宽正则、加白名单、删用例三种做法一律视为缺陷。本条用例的
> owner 是本特性（ai-trade-deep-reasoning-chain），用例 docstring 里必须写上这句话。

#### 3.1.6 `prompts.py` 的英文脊柱分叉

> **【v2 评审修订 · P1-8】英文脊柱外移到新模块，并**兑现**偏离表里那条已登记的还债指示。**
> `.claude-index/config.md` 的《显式记录的阈值偏离》表**首行**就是本函数：
> 「`build_ai_trade_system_prompt` | `MAX_FUNCTION_PARAMS=5` | 13 | …**下一次触碰应一次性收进
> `SystemPromptTexts` 聚合结构。**」——本特性恰恰就是「下一次触碰」。v1 的写法（末位加第 14 个
> 形参）是在一条已登记、已写明修法的债上再压一层，`CLAUDE.md` §二明写「调整必须在
> `.claude-index/config.md` 中显式记录，不得静默放宽」。故 v2 做两件事：
>
> 1. **英文脊柱整体外移到新模块 `services/quant/app/ai_auto/prompts_en.py`**（与本特性已经在建的
>    `reasoning_protocol.py` / `prompt_glossary.py` 同族）。理由不只是洁癖：`prompts.py` 今日已经
>    **919 行**，而英文脊柱 + 三个英文纪律子句孪生 + 英文工具描述 + 英文收尾指令合计约 300 行，
>    直接写进去会到 ~1220 行，而 `prompts.py` **不在**偏离表里——那就是一次静默放宽。
> 2. **新增文本参数收进聚合结构**，把形参从 13 降到 6，正面还掉表里那条债：

```python
@dataclass(frozen=True)
class SystemPromptTexts:
    """决策系统 prompt 的全部可选文本段（默认全空 ⇒ 渲染逐字节回退到本特性合入前）。"""

    base_rules: str = ""
    profit_guard: str = ""
    sizing: str = ""
    tactical_bars: str = ""
    cost: str = ""
    horizon: str = ""
    reasoning: str = ""      # 本特性新增：审议协议 + 输出预算纪律（空 ⇒ 走中文脊柱）


def build_ai_trade_system_prompt(inst_id: str, bar: str, budget_usd: float, leverage: float,
                                 *, position_step_pct: float = 0.0, allow_limit: bool = False,
                                 limit_band_pct: float = 0.02,
                                 texts: SystemPromptTexts | None = None) -> str: ...
```

**迁移必须是机械且可证伪的**：六个既有文本参数保留为 `deprecated` 的关键字兼容垫片（收到即折进
`texts`，两种写法产出 sha256 相同），因为本仓库有上百条冻结断言以关键字直连调用本函数（G-1 依赖
它们）。垫片与聚合两条路径的等价性由 §7.1 的 G-1b 冻结。**同一提交里必须更新
`.claude-index/config.md`**：把该行从「13 形参 / 待还」改为「6 形参 / 本轮已还」，并**新增登记**
`decider.py` 的越线（746 → 约 900 行，见 §6.1 第 5 项）。

- `texts.reasoning == ""` → 走今日的中文渲染路径，**一个字符都不改**（sha256 冻结门禁 G-7）。
- `texts.reasoning != ""` → 走 `prompts_en.render_spine_en()`：

```
You are a disciplined, risk-first execution trader running one crypto perpetual-swap
account autonomously. You are not a chat assistant and not a signal repeater. Your single
responsibility is to produce one tightly constrained trading decision for {inst_id}
(execution bar {bar}) each round. Session budget ≈ {budget:.2f} USDT at ≈ {leverage:g}x.

# FIRST PRINCIPLE — SIGNALS ARE EVIDENCE, NOT ORDERS
The strategy signals, factor readings, regime label and consensus score handed to you are
leads and triggers, not instructions. They may be right; they may equally be wrong, late,
or mutually contradictory. Your job is not to relay them but to adjudicate them the way a
senior discretionary trader would — step by step, from the evidence up. You may adopt,
doubt, or overturn any single signal, and you must say which you are doing and why.

{LANGUAGE_CONTRACT}

{GLOSSARY_BLOCK}

# HARD CONSTRAINT SECTIONS
The sections that follow are rendered in Chinese and are **hard constraints imposed by the
system**, not suggestions. Stage S9 requires you to restate each of them in English with
its thresholds before you decide; that restatement is how you prove you have read them.

{charter}   ← 五段宪章（基础策略 → 利润保护 → 仓位 → 成本 → 持仓周期），顺序与今日**完全一致**

{reasoning_text}   ← 审议协议 + 输出预算纪律

# DECISION DISCIPLINE
1. Follow the deliberation protocol above for every round without exception. …
2. Use only the real data supplied … choose `hold` when evidence is insufficient or
   contradictory. Higher-timeframe conflict … overbought/oversold is never on its own an
   entry instruction … never guess a stale or missing frame. Holding is an active decision
   that needs present-tense evidence; do not become path-dependent on a history of holding.
3. Risk first: … {size discipline (EN)} … avoid churn and over-trading.
4. {action discipline (EN)} … The guarded executor places the real order and inherits every
   capital guardrail; you only supply the decision and the reasoning.
5. **CLOSING HARD CONSTRAINT: after completing the full deliberation protocol you MUST call
   `submit_trade_decision` exactly once. Emitting analysis without the tool call is never
   acceptable and costs the round.**
```

三个子句助手各加一个英文孪生：`_tactical_discipline_en` / `_action_discipline_en` /
`_size_discipline_en`，**三者与 `render_spine_en` 一并住在新模块 `prompts_en.py` 里**（§3.1.6 开头
的评审修订）。**中文版一字不改**（它们仍是 `off` 档的真源）。

`build_decision_prompt` 的收尾任务指令按 `payload["reasoningDepth"]`（新加法键，缺席 ⇒ 逐字节回退）
分叉；英文版：

```
Now run the full deliberation protocol (S1 → S12) on the evidence above, then call
submit_trade_decision exactly once. Analysis without a tool call is a lost round.
Remember: `reason` and `evidenceDigest` are written in Simplified Chinese for the operator;
everything else you write is English.
```

#### 3.1.7 工具 schema 的加法字段 `evidenceDigest`

`build_decision_tool_schema(..., allow_reasoning_digest: bool = False)`（keyword-only，默认 False
⇒ `build_decision_tool_schema(0.0) is DECISION_TOOL_SCHEMA` 的既有恒等断言原样通过）。为真时在
调用方自己的 `props` 浅拷贝上补：

```python
props["evidenceDigest"] = {
    "type": "string", "maxLength": 400,
    "description": "（可选，用简体中文写）给人类操作员看的证据摘要：你采纳了哪些信号、"
                   "推翻了哪些、决定性证据是什么、什么会让你改变主意。不要复述行情数字。"}
```

> **【v2 评审修订 · P1-7】「要求中文」必须有一条**纯呈现层**的守卫，否则 G-5 没有判据。**
> 这个字段在一份**通篇要求用英文思考**的 prompt 里被单独要求写中文，模型回英文是完全现实的
> 失败模式。而它同时是**三处中文界面**的取值源：journal 摘录的首选来源（§3.3.4）、结算后收起态
> 的标题（§3.5.2 ②）、以及左栏决策卡的首段（§3.3.4 的第二个消费者，见 P1-2）。一旦模型回英文，
> 三处同时变英文，而 G-5 的判据栏只写了「UI 阶段名、摘要、状态文案全中文」——那说的是前端静态
> 文案，管不住模型产出。故新增一条纯核心守卫（放 `reasoning_protocol.py`，可离线单测）：
>
> ```python
> def is_probably_chinese(text: str, *, floor: float = 0.3) -> bool:
>     """CJK 字符占比是否达标；用于**呈现层**取值回落，绝不参与任何交易语义。"""
> ```
>
> 不达标 ⇒ **视作未提供**，回落到 §3.3.4 的第 ②/③ 级来源。三条铁律必须写进实现注释：
> ① 它**绝不**产生 degrade、② **绝不**改变 `TradeDecision` 的任何字段、③ **绝不**触发追问——
> 一个摘要写成英文是排版问题，不是安全问题，让它影响下单是把 R-4 的教训又走一遍。

工具散文描述在英文档下整体换成英文版 `DECISION_TOOL_DESCRIPTION_EN`（同款 gated：
`build_decision_tool_description(..., lang="zh")`，`"zh"` ⇒ 恒等返回今日常量）。

---

### 3.2 轴 B —— 反思侧《对抗式复核协议》（Adversarial Review Protocol）

反思的**安全语义一个字节都不改**：只能收紧、`abort` 需指名实质性缺陷、机会成本对称、
fail-closed、`close` 的否决须有极强理由、`apply_reflection` 只减不增。改的只有「怎么想」。

| id | code | mandate（英文） |
|---|---|---|
| R1 | `RESTATE` | Restate the proposed decision and the trader's central claim in one sentence each, in your own words. If you cannot restate the claim, the reasoning was too vague — note that. |
| R2 | `AUDIT` | Audit every piece of evidence the trader cited **against the input block above**. For each citation: does the input actually say that? Is the number right? Is the frame the trader used the frame that was supplied? A miscited number is a named, concrete defect. |
| R3 | `OMISSION` | Hunt for what the trader did not mention. Scan the evidence for the strongest item pointing the other way and state it plainly. Silence about a strong counter-signal is itself a finding. |
| R4 | `CONFLICT` | Check the proposal against the higher-timeframe regime and against every armed hard-constraint section, restating each threshold in English. Name any conflict and whether the trader offered a hedge rationale for it. |
| R5 | `EXPOSURE` | Given the current real position, the session P&L against budget, the round-trip cost in bps, the holding-horizon state and the correlated exposure already on the book, is this incremental exposure acceptable? Is the size heavier than the conviction justifies? Quantify. |
| R6 | `PREMORTEM` | Assume this order is filled and loses money. Write the most likely path to that loss in three or four sentences. Then answer one question explicitly: is that path acceptable given the stop and the budget? An acceptable loss path is a reason to confirm, not to abort. |
| R7 | `VERDICT` | Grade it. `confirm` if the proposal is broadly consistent with the evidence and the exposure is tolerable. `confirm` with a smaller `adjustedSizeFraction` if your only concern is size — shrinking is your first-choice conservative tool. `abort` **only** when you can name one substantive defect from R2–R5, quoted concretely. A generic unease is not a defect. Missing a well-evidenced trade costs exactly as much as taking a bad one. You may only tighten. Then call the reflection tool exactly once. |

`build_reflection_system_prompt(horizon_text="", reasoning_text="")` 同款分叉：`reasoning_text == ""`
⇒ 今日中文原文逐字节；非空 ⇒ 英文复核官脊柱 + 语言契约 + R1–R7 协议 + 今日分级标准的英文版 +
`horizon_text`（仍为中文，同 §3.1.6 的 HARD CONSTRAINT 框注）。

> **【v2 评审修订 · P0-5】`reasoning_text` 从哪里来——这是本轴唯一会整条落空的地方。**
> 全仓**唯一**调用 `build_reflection_system_prompt` 的位置是 `decider._stream_reflection`
> （`decider.py:504`），它在**每一轮**用当轮 payload 的 `tradingHorizon` 现造 `system`；
> manager.py 与 backtest.py **从未**引用过它（`grep -rn build_reflection_system_prompt
> services/quant/app` 的命中只有 `decider.py:48/504` 与 `prompts.py:778`）。因此：
>
> - 复核协议**只能**经 `AiTradeDecider(..., depth_profile=profile)` 携带进决策器，由
>   `_stream_reflection` 在现造 `system` 时注入 `reasoning_text=build_review_protocol(self._depth)`；
> - `self._depth is None`（既有直接构造 / 全部离线用例）⇒ 传 `""` ⇒ **逐字节回退**，
>   `test_ai_trade_exit_freeze.py:66` 那条 sha256 冻结断言原样成立；
> - **禁止**在 manager / backtest 里调用 `build_reflection_system_prompt`——那不是「另一种写法」，
>   而是一条不会报错也不会判红的死路：§7.2 的 P-5 直调该函数断言，照样全绿。
>
> §7.5 的 **S-7 / S-9** 两条装配用例就是为这条而写：判据取自假客户端 `stream(system=…)` 实际
> 收到的字符串，断言决策侧含 `## S12`、反思侧含 `## R7`。

`build_reflection_prompt` 的收尾指令同款按 `payload["reasoningDepth"]` 分叉为英文版，并保留
今日的两条关键结构：被复核决策的一行压缩（`_format_reflection_decision`）与 L2 反例段
（`experienceCounter`）——两者位置与内容不变。

**反思推理必须留存**（补 C-3）：`decider._stream_reflection` 新增与决策侧同款的
`reflection_parts` 累积 → `self._last_reflection_analysis`（入口清零、流正常结束才落、
≤`_ANALYSIS_MAX_CHARS`），并暴露只读属性 `last_reflection_analysis`。

> **【v2 评审修订 · P2-3】`reflection_parts` 只收 `text_delta`。**
> 注意 `_stream_reflection` 今日把 `text_delta` 与 `thinking_delta` **合喂**进同一个
> `pump.feed("reflection", …)`（`decider.py:511-512`，与决策侧刻意分成两个 phase 不同）。
> 若照抄那一行的位置累积 `reflection_parts`，扩展思考开启后模型的思考文本会混进
> `_last_reflection_analysis`，令 `reflection_stages` 的 R1–R7 计数虚高、`reflection_chars`
> 失真。故：**累积只挂在 `ev.kind == "text_delta"` 上**；phase 扇出维持今日形状不变
> （那是呈现层的合并，与服务端留存是两件事）。

---

### 3.3 轴 C —— 预算、传输层与截断诚实化

#### 3.3.1 三份预算，一处装配：`services/quant/app/ai_auto/llm_budget.py`（新）

```python
@dataclass(frozen=True)
class AiTradeLlmClients:
    """AI 自动交易三个调用点各自的 LLM 客户端（预算互不串味）。"""
    decision: LLMClient
    reflection: LLMClient
    summary: LLMClient


def build_ai_trade_llm_clients(cfg: "LlmProviderConfig", settings: Settings, *,
                               profile: DepthProfile) -> AiTradeLlmClients:
    """按深度档给三个调用点各造一个 client（`profile.enabled=False` ⇒ 三者同参、等价今日单 client）。"""
```

实现要点（**刻意不改 `LLMClient.stream` 的签名**）：

- 用 `dataclasses.replace(cfg, max_tokens=…, thinking_budget_tokens=…)` 造三份 config，再各调
  `make_llm_client_from_config`。理由：`stream()` 是 Protocol，本仓库有 **32 个测试文件**实现了
  `async def stream(self, *, system, messages, tools)` 的假客户端；给 `stream` 加一个生产上恒会
  传的关键字参数会一次性打翻它们全部。改构造参数则零波及。
- `profile.enabled == False` ⇒ 三者都用原 `cfg`（`decision is reflection is summary` 不做恒等，
  但三次 `make_llm_client_from_config(cfg)` 的行为与今日单 client 逐字一致）。
- `thinking_budget_tokens` 只在 `decision` 上施加，且仅当 `settings.agent_thinking_budget_tokens > 0`
  且 `cfg.api_standard == "anthropic"`；同时要求 `decision_max_tokens > thinking_budget + reserve`，
  不满足则**降为 0 并 `logger.warning`**（绝不发一个必然 400 的请求体）。

`decider.AiTradeDecider.__init__` 末位新增两个默认 `None` 的形参：
`reflection_llm: LLMClient | None = None`、`summary_llm: LLMClient | None = None`；
`self._reflection_llm = reflection_llm or llm`、`self._summary_llm = summary_llm or llm`。
`_stream_reflection` 改用 `self._reflection_llm`，`_stream_summary` 改用 `self._summary_llm`。
默认 `None` ⇒ 现有全部直接构造（回测早期路径、单测、`decider.llm` 蒸馏出口）逐字零回归。

#### 3.3.2 扩展思考（可选、默认关、零回归）

- `providers.LlmProviderConfig` 末位新增 `thinking_budget_tokens: int = 0`；
  `provider_config_from_settings` / `config_from_row` 都从 `settings.agent_thinking_budget_tokens` 填。
- `factory.make_llm_client_from_config` 把它透传给 `AnthropicClient(...)`；`OpenAIClient` **不接**
  （openai 标准无此概念，显式不透传比静默忽略诚实）。
- `AnthropicClient._body`：

```python
if self._thinking_budget > 0:
    body["thinking"] = {"type": "enabled", "budget_tokens": self._thinking_budget}
    body["temperature"] = 1.0     # Anthropic 扩展思考要求 temperature=1
    body.pop("top_p", None)       # 同上：扩展思考下不接受 top_p
```

`self._thinking_budget == 0`（默认）⇒ 请求体与今日**逐字节相同**（用例断言 `body == 合入前 body`）。

#### 3.3.3 截断检测：`no_tool_call` 不再吞掉「预算耗尽」

`_stream_decision` 记录 `stop_reason`（`message_done` 事件）与 `usage`，返回值从 `dict | None`
改为一个小 dataclass `_StreamOutcome(raw: dict | None, stop_reason: str | None, usage: dict | None)`。

**【v2 评审修订 · P0-1】截断口径必须是 provider 无关的，且要有单一真源。**
v1 写死 `stop_reason == "max_tokens"`，那是 **Anthropic 口径**。`StreamEvent.stop_reason` 这个字段
在 OpenAI 标准下装的是 `choice["finish_reason"]`（`openai_client.py:147-149`），取值域是
`length` / `stop` / `tool_calls` / `content_filter`——**永远不会等于 `max_tokens`**。照 v1 实现，
OpenAI 标准的 provider 上根因 C-4 原样存活：预算耗尽仍表现为 `no_tool_call` → 重试 → 再次耗尽 →
降级 `hold`，日志与界面上依旧看不出真实原因。故新增归一常量，放在 `reasoning_protocol.py`（纯核心）：

```python
# 「输出预算耗尽」的 provider 归一口径（**唯一真源**；新增 provider 必须在这里登记）。
# anthropic: stop_reason == "max_tokens"        （anthropic_client.py:152）
# openai:    finish_reason == "length"           （openai_client.py:147-149）
TRUNCATION_STOP_REASONS = frozenset({"max_tokens", "length"})
# 内容安全拦截：与「写太长」是两回事，重试同一份 prompt 只会再被拦一次，故单列且**不可重试**。
CONTENT_FILTER_STOP_REASONS = frozenset({"content_filter", "content_filtered"})
```

`_attempt_decision` 据 `_StreamOutcome` 分派：

| 情形 | 今日 | 本特性后 |
|---|---|---|
| 有工具调用 | 正常映射 | 不变（`stop_reason` / `usage` 只进遥测） |
| 无工具调用 且 `stop_reason ∈ TRUNCATION_STOP_REASONS` | `no_tool_call` | **`reasoning_truncated`**（新码，入 `_RETRYABLE_DEGRADE_REASONS`） |
| 无工具调用 且 `stop_reason ∈ CONTENT_FILTER_STOP_REASONS` | `no_tool_call` | **`content_filtered`**（新码，**不入**可重试集合——同一份 prompt 重试只会再被拦一次，白烧一次预算） |
| 无工具调用 且其它 | `no_tool_call` | 不变 |

**【v2 评审修订 · P0-2】`message_done` 不能再「见到即 break」，否则 OpenAI 标准下 token 用量恒空。**
OpenAI 的 usage 走 `stream_options.include_usage`（`openai_client.py:61`），它在一个**独立的、
`choices` 为空**的收尾 chunk 里到达，`_dispatch_chunk` 因此会发出**第二条** `message_done`
（`openai_client.py:134-139`）。而今日 `_stream_decision` 在**第一条** `message_done` 上就 `break`
（`decider.py:360-362`）——第一条正是携带 `finish_reason`、`usage` 为 `None` 的那一条。后果有三，
且全部是静默的：UI 的 token 累计恒空、`aiStats.reasoning.inputTokens/outputTokens` 恒空、
`scripts/ai_trade_cot_probe.py` 的「output token 中位数之比 ≥ 5.0」在该 provider 上**取不到数**。
这直接击穿 G-8，也击穿 R-3（token 成本 5 倍）的第 4 条缓解——一条针对本特性头号真金风险的措施，
在半个 provider 家族上不工作。故收尾语义改为：

```python
done = False                      # 首条 message_done 之后进入「只等 usage 回填」状态
async for ev in self._llm.stream(...):
    if ev.kind == "message_done":
        stop_reason = ev.stop_reason or stop_reason      # 非空者胜（第二条通常只带 usage）
        usage = ev.usage or usage                        # 同上：只回填，绝不用 None 覆盖已有值
        if not done:
            await pump.flush()
            done = True
        continue
    if done:
        continue                  # 收尾之后到达的任何事件一律丢弃（语义与今日 break 后等价）
    ...                           # text_delta / tool_use_* / error 分支逐字不变
```

三条必须写进实现注释：① `done` 之后**只接受** `message_done` 做回填，`text_delta` 一律丢弃，
使 `last_analysis` 与今日 `break` 的结果逐字相同（§7.4 B-8 冻结）；② 外层 `asyncio.wait_for`
仍然兜住「provider 不关流」的极端情形，故这里不需要第二个超时；③ `error` 事件在 `done` 之后
到达也一并丢弃——工具入参已经拿到，此时抛异常会把一轮已经成功的决策变成 `llm_error`。

`_retry_hint_suffix("reasoning_truncated")` 给一条**不同的**英文纠偏提示：

```
[SYSTEM] Your previous response ran out of output budget before the tool call. Compress
stages S1–S9 to their conclusions, keep S10–S12 intact, and call submit_trade_decision
within the budget. A shorter analysis with a decision beats a long analysis with none.
```

其余降级码的提示文案改为英文但语义逐字不变。

#### 3.3.4 三处硬上限与摘要护栏

| 常量 / 位置 | 今日 | 本特性 | 理由 |
|---|---|---|---|
| `decider._ANALYSIS_MAX_CHARS` | 4000 | **20000** | 5× |
| `trace._TRACE_MAX_CHARS` | 4000 | **20000** | 同上（单 phase 单轮） |
| `trace._TRACE_ROUND_TOTAL_MAX` | 无 | **36000**（新增，v2 评审修订 P1-6） | 单轮**四槽位合计**上限。`_TRACE_MAX_CHARS` 是单 phase 的，而一轮最多有 `analysis` / `thinking` / `reflection` / `reask` 四个累积槽位，各 20000 ⇒ 80000/轮 ⇒ ×`_TRACES_MAX`(20) ⇒ **1.6MB/成员**，一个 5 标的分组即 8MB 常驻并随 WS 快照整份重放。合计闸把最坏量级压回 ~720KB/成员 |
| `trace._TRACE_FLUSH_CHARS` | 48 | **160** | 5× 文本量下 48 字符一帧会把 1000 槽位的订阅队列压满而**静默丢帧**（`_push` 的 `QueueFull: pass`）。160 使单轮 delta 帧数与今日同量级 |
| `session._ANALYSIS_EXCERPT_CHARS` | 400 | 400（不变） | 但**取值来源改掉**，见下 |
| `decider._SUMMARY_MAX_CHARS` | 无 | **3000**（新增） | 摘要客户端预算 1536 token，再加一道字符兜底，防摘要种子撑爆 `_context` |

**journal 摘录来源换源（补 C-6，本轴最容易被漏掉的一条）**：
`session.py:1627` 今日是 `self.decider.last_analysis[:400]`。12 阶段协议下这 400 字符恒为 S1 样板抬头，
长期记忆会被样板文喂满。改为：

```python
excerpt = (decision.evidence_digest                       # ① 模型给的中文摘要（首选）
           or _tail_stage_excerpt(self.decider.last_analysis)   # ② S12 段落的前 400 字符
           or self.decider.last_analysis[:_ANALYSIS_EXCERPT_CHARS]  # ③ 今日行为（零回归回退）
           or None)
```

`_tail_stage_excerpt` 放在 `reasoning_protocol.py`（纯核心、可离线单测）：按 `STAGE_HEADING_RE`
定位最后一个 `## S12`，取其后 400 字符；找不到则返回 `""` 让第三分支兜底。
第 ① 级来源 `decision.evidence_digest` 还要先过 §3.1.7 的 `is_probably_chinese` 守卫（P1-7）：
模型把摘要写成英文时视作未提供，落到第 ② 级，而不是把英文摘要摆到中文界面上。

> **【v2 评审修订 · P1-2】这一行有**两个**消费者，换源等于同时改掉左栏决策卡。**
> v1 全程按「journal 摘录」论证，S-2 也只断言 journal。事实是 `session.py:1627` 算出的 `excerpt`
> 紧接着被喂给**两处**：
> - `commit_round(payload, decision, executed, analysis=excerpt, …)`（`session.py:1631`）→ journal
>   → 滚动摘要素材；
> - `_emit_decision(..., analysis=excerpt, …)`（`session.py:1640-1643`）→ `decision` 事件的
>   `analysis` 字段 → **左栏决策卡首段 + WS 快照重放**（`session.py:2616-2617` 的 docstring 明写
>   「供左栏决策卡首段」）。
>
> 换源因此同时重定义了一处用户天天在看的界面文案。**评审判定这是有意且正确的**：决策卡首段从
> 「`## S1 REGIME` 样板抬头」变成模型自己写的中文证据摘要，是本特性最直接的一处 HCI 净改善——
> 但它必须被**写明**并被**断言**，否则下一个人会把它当成回归。§7.5 的 S-2 因此扩为：同一轮里
> journal 的 `analysis` 与 `decision` 事件的 `analysis` 取到**同一份**摘录，且三级回退在两处
> 表现一致。

#### 3.3.5 轮级墙钟闸（v2 评审修订 · P0-4）

深度档把单次 LLM 调用的超时从 60s 抬到 210s，而 `ai_trade_decision_max_retries` 默认是 **3**
（`config.py:175`）。两者相乘再加上追问与反思，最坏单轮 = 210×4 + 210 + 150 ≈ **20 分钟**；这 20
分钟里 `_deciding` in-flight 守卫会把每一根 K 线确认与每一次信号翻转全部丢弃或压进待办
（`session.py:1340` / `:1533` / `:1685`），而默认唤醒周期只有 **2 分钟**（`config.py:234`）。
也就是说：**推理深度会在用户毫不知情的情况下改变这个账户的交易频率**，而这不是通过任何一条
交易判据发生的，纯粹是墙钟挤占。故新增两道闸，都只**收敛尝试次数**、**绝不改变任何安全回落语义**：

1. **尝试次数闸**：单轮有效尝试数 = `min(1 + settings.ai_trade_decision_max_retries,
   profile.max_attempts_cap or 无穷)`。`deep` 档取 2（首次 + 1 次重试），`off` 档不封顶（逐字回退）。
2. **轮级墙钟闸** `profile.round_deadline_s`（`deep` = 600s，可由
   `settings.ai_trade_reasoning_round_deadline_s > 0` 覆盖；`off` = 0 ⇒ 不设闸）：一轮开始时记
   `deadline = monotonic() + round_deadline_s`，**每次发起新尝试之前**检查；已耗尽则不再发起新尝试，
   直接沿用当前的 `(decision, degrade)`。追问（§3.4.2）与反思共享同一份预算。

三条实现铁律：

- **闸只决定「要不要再发起一次」，绝不中断在飞的那一次**——中途 cancel 一个正在流式的决策请求，
  会让 `_last_analysis` 停在半截、`stop_reason` 为空，那正是我们刚刚花一整条轴消除的那种「看不出
  原因」的形态。
- **闸耗尽不是新的降级码**。它沿用当前已有的 `degrade`（多半是 `decision_timeout`），只是不再
  重试。新增一个 `round_deadline` 码等于给「模型话说不完」发明第二种降级路径，而 R-4 的教训
  正是「不要为质量问题新造安全回落」。
- 闸的读数进 `ReasoningMeta.deadline_hit: bool` 与 `result` 帧，**在界面上明示**「本轮因超时预算
  用尽提前收敛」，否则它就成了新的静默行为。

`monotonic()` 而非 `time.time()`：本仓库已经为「双时钟」栽过（见
`docs/plans/ai-trade-wake-aware-context`），墙钟闸必须用单调钟。

---

### 3.4 轴 D —— 深度可观测性与「浅研判追问」

#### 3.4.1 逐轮研判元数据 `ReasoningMeta`

`decider` 每轮产出（`decide()` 的返回值不变，元数据经新只读属性 `last_reasoning` 取用，
与 `last_analysis` 同款范式，保证 `decide()` 的签名契约零改动）：

```python
@dataclass(frozen=True)
class ReasoningMeta:
    depth: str            # off | standard | deep | exhaustive
    chars: int            # 研判全文字符数（截断后）
    stages: int           # count_stages(analysis, "S")：命中的阶段标记数
    truncated: bool       # stop_reason ∈ TRUNCATION_STOP_REASONS（provider 归一，§3.3.3）
    reasked: bool         # 本轮是否触发过浅研判追问
    reask_adopted: bool   # [v2·P0-3] 追问结果是否被采纳（False = 追问跑了但保留首次决策）
    deadline_hit: bool    # [v2·P0-4] 本轮是否因轮级墙钟闸提前收敛（§3.3.5）
    input_tokens: int | None
    output_tokens: int | None
    reflection_chars: int | None
    reflection_stages: int | None
```

`usage` 归一复用既有 `agent.llm.retry.normalize_usage(getattr(self._llm, "provider", "anthropic"), raw)`
——**不新写第二份口径**（`agent/loop.py:343` 已是同一写法）。

#### 3.4.2 浅研判追问：只提质量，永不降级

在 `decide()` 的重试循环**之后**（即已经拿到一个终态决策），插入至多
`ai_trade_reasoning_reask_max`（默认 1）次的深度追问：

```python
# 条件：profile.enabled 且 min_analysis_chars > 0 且 degrade is None（真实决策）
#       且 len(last_analysis) < min_analysis_chars
#       且 §3.3.5 的轮级墙钟闸尚未耗尽
# 动作：以英文追问提示重跑一次 _attempt_decision，**流式产出走独立的 `reask` 相位**（见下）
# 采纳规则（安全关键）：
#   - 追问成功且更深（chars 更大）→ 采纳追问结果，reask_adopted=True
#   - 追问失败 / 更浅 / 产生 degrade → **原样保留第一次的决策**，reask_adopted=False
#   - 任何情况下都**不会**因为「研判太浅」而把决策改成 hold
```

> **【v2 评审修订 · P0-3】追问的流式产出必须走独立相位，绝不能与首次研判互相覆盖。**
> 前端对 `analysis` 是**纯追加**归约（`store.ts:236-237`），而全流程**唯一**的重置点是 `retry`
> 相位（`store.ts:262-272`）。追问发生在 `decide()` 的重试循环**之后**、不经过 `_emit_retry`，
> 于是 v1 的写法有两种落地，**两种都坏**：
> - 沿用同一个 `emit` ⇒ S1–S12 被追加成两遍，阶段导轨读出 24/12，用户看到一份 18k 字符的重复
>   思维链；
> - 改用 `emit=None` ⇒ 界面上留着的是**被丢弃的那份浅研判**，真正生效的深研判用户永远看不到。
>
> 而追问**本来就可能被拒收**（更浅 / 失败 ⇒ 保留首次决策）。若已覆盖，界面就停在一份系统明确
> 判定为不采纳的文本上——「界面上的研判」与「实际生效的决策」不是同一份，这是本特性最不该犯的错。
>
> 修法：追问的 `text_delta` 走**新相位** `reask`（前端独立字段 `reaskAnalysis`，`analysis`
> 一个字符都不动），并在 `result` 帧的 `reasoning` 里下发 `reasked` 与 `reaskAdopted` 两个布尔。
> §3.5.2 的展开态因此多一段「深化追问（已采纳／未采纳）」。
>
> **两份都留在界面上是刻意的**：被拒收的那一份正是「模型第二次反而想得更浅」的现场证据，
> 抹掉它就抹掉了「追问是单向增益」这条设计的可证伪性——而这条设计正是为了不再走一遍
> `docs/plans/ai-trade-reflection-hold-inertia` 的老路。
>
> 追问提示的传递**不复用** `_attempt_decision` 的 `hint` 形参：`hint` 的值域是 degrade 码
> （`_retry_hint_suffix` 的查表键），塞一段自由文本进去会让那张表多出一条永远不匹配的分支。
> 改为新增 keyword-only 的 `extra_instruction: str | None = None`，默认 `None` ⇒ 与今日逐字一致。

追问提示（英文，一次性，不落 `_context/_journal`）：

```
[SYSTEM] Your previous analysis covered {stages}/12 stages in {chars} characters, which is
below the depth this desk requires. The decision itself was accepted; this is a depth
request, not a rejection. Re-run the full protocol S1 → S12 with at least
{words_per_stage} words per stage, paying particular attention to the stages you skipped,
and submit your decision again. If, after genuinely deeper analysis, the decision is
unchanged, submit the same decision.
```

> 设计上的关键取舍：**浅研判是质量问题，不是安全问题。** 把它接到 `_RETRYABLE_DEGRADE_REASONS`
> 上会造出「模型话少 ⇒ 全部降级观望」的新吸收态——本仓库为「反思致永不开单」已经付过一次学费
> （见 `docs/plans/ai-trade-reflection-hold-inertia`）。追问必须是单向增益。

#### 3.4.3 研判耗时压力提示（只读，不改调度）

`session` 维护决策墙钟耗时的 EWMA（α=0.3，仅统计非降级轮）。当
`wake_interval_s > 0` 且 `ewma_ms > pressure_ratio × wake_interval_s × 1000` 且样本 ≥ **1**，
`status` wire 追加：

```json
"reasoningPressure": {"ewmaMs": 186000, "intervalS": 120,
                      "suggestIntervalMin": 6, "level": "warn"}
```

`suggestIntervalMin = min(ceil(ewma_ms / 1000 / 60 / pressure_ratio),
ai_trade_wake_interval_ceil_min)`。前端在轨迹面板概览条渲染一条中性提示：
「研判耗时逼近唤醒周期（近期均值 3.1 分钟 / 周期 2 分钟），建议把唤醒周期调到 ≥ 6 分钟」。
**只提示、不自动改**——唤醒周期是用户的交易节奏决定，静默替用户改参数是本仓库明令禁止的形态。

> **【v2 评审修订 · P1-5】三处修订，全部来自「判据取自不携带该信息的入参」这一老形状。**
> 1. **`wake_interval_s <= 0` 必须整键缺席。** `wakeIntervalMinutes = 0` 是一个**合法且有语义**的
>    入参——它表示「关闭墙钟定时唤醒，只靠 K 线确认与信号翻转唤醒」（`schemas.py:899-901`）。
>    此时 v1 的判据右侧恒为 0，退化成 `ewma_ms > 0` ⇒ **恒真**：一个刻意关掉定时唤醒的用户会
>    永远挂着一条「研判耗时逼近唤醒周期」，还附赠一个由除以 0.6 现算出来的、毫无意义的建议周期。
>    对不按周期交易的人提周期建议是纯噪音。
> 2. **`suggestIntervalMin` 必须按 `ai_trade_wake_interval_ceil_min`（240 分钟）夹紧**，
>    否则一轮 40 分钟的 `exhaustive` 会建议一个后端根本不接受的周期。
> 3. **字段改名 `p50Ms` → `ewmaMs`，界面文案「中位」→「近期均值」。** 算的是 α=0.3 的指数移动
>    平均，不是中位数；v1 的字段名、§3.5.3 的文案与算法三处口径不一。本仓库已经为「判据量纲须与
>    施加粒度一致」付过学费（见 `docs/plans/resilience-and-hci-hardening`），指标名与算法名不符是
>    同一类债的起点。
> 4. **样本门槛 3 → 1。** 最坏单轮墙钟接近 10 分钟（§3.3.5 封顶后），要 3 个样本意味着用户可能
>    在半小时后才第一次被告知交易节奏已被推理吃掉。压力提示是**只读提示**、没有任何副作用，
>    首轮就该说话；抖动由 EWMA 自身平滑，不需要再靠样本数压一层。

#### 3.4.4 会话级落痕（零迁移）

`metrics_json.aiStats.reasoning`：

```json
{"depth": "deep", "rounds": 128, "medianChars": 9412, "medianStages": 12,
 "truncatedRounds": 3, "reaskedRounds": 7, "reaskAdoptedRounds": 4,
 "deadlineHitRounds": 1, "contentFilteredRounds": 0,
 "inputTokens": 4210553, "outputTokens": 812004, "tokensProviderKnown": true,
 "reflectionRounds": 34, "reflectionMedianChars": 4180}
```

`aiStats` 是既有 JSON blob（`ai_auto_repositories._load_config(run.metrics_json)`），加键零迁移。

`tokensProviderKnown`（v2 评审修订 · P0-2 的诚实化配套）：本会话是否**真的**从 provider 拿到过
usage。`normalize_usage` 在两字段全缺时返回 `None`（`agent/llm/retry.py:88-89`），若不区分
「没花 token」与「provider 没告诉我们花了多少」，一份 `outputTokens: 0` 的历史会被当成
「这个会话几乎没推理」而误导后续调参。为 `false` 时 UI 显示「该服务未回报用量」而不是 `0`。

---

### 3.5 轴 E —— 分阶段研判视图（人机交互）

一段 ~9000 字符的英文思维链原样糊进右侧窄栏 = 交互倒退。呈现层按「渐进式披露 + 稳定锚点 + 母语索引」
三条原则重做。

#### 3.5.1 新纯函数 `apps/web/lib/aiTradeReasoning.ts`

```ts
export interface ReasoningStage {
  id: string;        // "S1" … "S12" / "R1" … "R7"
  index: number;     // 1..12
  code: string;      // REGIME / EXECUTION …（模型给的英文码，缺失则空串）
  titleZh: string;   // 中文阶段名（来自静态映射，**不依赖模型措辞**）
  body: string;      // 该阶段正文（markdown 原文）
  complete: boolean; // 是否已收到下一个阶段标记（流式进度用）
}

export function parseReasoningStages(text: string, kind: "decision" | "review"): {
  preamble: string; stages: ReasoningStage[];
};
export function stageProgress(stages: ReasoningStage[], kind): { done: number; total: number };
```

中文阶段名静态映射（**唯一真源在前端**，因为它是纯呈现物；后端只认 `S<k>` 数字）：

| id | 中文阶段名 | id | 中文阶段名 |
|---|---|---|---|
| S1 | 多周期背景 | S7 | 冲突与权重 |
| S2 | 执行周期结构 | S8 | 历史先验与经验 |
| S3 | 战术择时 | S9 | 持仓与铁律核对 |
| S4 | 量能与参与度 | S10 | 候选方案与期望值 |
| S5 | 因子读数 | S11 | 证伪与事前验尸 |
| S6 | 信号逐条裁决 | S12 | 校准结论 |
| R1 | 复述提议 | R5 | 敞口与成本 |
| R2 | 证据核对 | R6 | 事前验尸 |
| R3 | 遗漏的反证 | R7 | 分级裁决 |
| R4 | 高周期与铁律冲突 | | |

解析必须**容错**：模型漏了某个阶段、多写了 `S13`、在 S1 之前写了引言、正在流式写到一半——四种
情形都不得抛错。漏掉的阶段在导轨上呈灰色空槽（这本身就是有价值的观测：用户能一眼看出模型跳过了
S11 证伪）。

#### 3.5.2 新组件 `AiTradeReasoningBlock.tsx`（取代今日的 `AnalysisBlock`）

三态设计：

**① 流式中（`state === "deciding"`）** —— 不再滚动一整面墙的文字，而是：

```
┌────────────────────────────────────────────┐
│ 🧠 深度研判中 · 阶段 7/12                   │
│ ●●●●●●●○○○○○   ← 12 格进度导轨（已完成实心） │
│ ⟨当前阶段⟩ 冲突与权重                        │
│ …only the last ~4 lines of the live stage… ▌│
└────────────────────────────────────────────┘
```

- 导轨格子 `title` 属性给中文阶段名 + 该阶段字数（hover 可读）。
- 只渲染当前阶段的尾部若干行（`whitespace-pre-wrap` + 光标），DOM 节点数与轮次长度**解耦**——
  这是这段 UI 能在 20 轮 × 9000 字符下不卡的关键。
- 无阶段标记时（模型没按协议输出）优雅回退到今日的整段流式渲染，并在导轨位显示「未分阶段」。

**② 结算后 · 收起态（默认）**

```
┌────────────────────────────────────────────┐
│ 🧠 深度研判 · 12/12 阶段 · 9.4k 字 · 42s   ⌄│
│ 采纳 4H 趋势与量能配合，推翻布林回归信号；    │  ← evidenceDigest（中文）
│ 若 1H 跌破 29,850 则本判断失效。             │
└────────────────────────────────────────────┘
```

- 标题行右侧的 `⌄` 展开；键盘可达（`<button aria-expanded>`），符合本仓库既有折叠件的写法。
- `evidenceDigest` 缺席时回退到 S12 段落首句；再缺席则回退到 `reason`。
- 缺阶段时标题显示 `9/12 阶段`，并把缺失阶段名以 `title` 列出（诚实呈现，不美化）。

**③ 结算后 · 展开态**

- 每个阶段一个 `<details>`：中文阶段名 + 英文 code 小字 + 字数；S11（证伪）与 S12（结论）**默认展开**，
  其余默认折叠——这是「最有决策价值的两段优先可见」的信息层级判断。
- 正文走既有 `renderMarkdown`（安全铁律：绝不 `dangerouslySetInnerHTML`；模型输出即不可信输入）。
- 底部一行元信息：`输入 12.1k tok · 输出 3.4k tok · 耗时 42s`，截断轮追加红色 chip「输出预算耗尽（已截断）」。
- 一个「复制全文」按钮（`navigator.clipboard`，失败静默降级为选中提示）。
- **深化追问段**（v2 评审修订 · P0-3）：`reaskAnalysis` 非空时，在 12 段之后追加一段独立折叠块
  「深化追问（已采纳）」或「深化追问（未采纳，保留首次研判）」，取自 `reasoning.reaskAdopted`。
  两份研判**并列呈现、绝不互相覆盖**；未采纳的那一份用 `--muted` 色系但**不隐藏**——它是
  「模型第二次反而想得更浅」的现场证据。
- **轮级墙钟收敛提示**（v2 评审修订 · P0-4）：`reasoning.deadlineHit` 为真时，在元信息行追加一枚
  中性 chip「本轮超时预算用尽，已提前收敛」。

**④ 扩展思考段（`thinking`）的归属**（v2 评审修订 · P1-3）

轴 C 的扩展思考一旦开启，`thinking_delta` 第一次真的会有内容——而今天渲染它的
`<details>「深度思考」` 恰好长在 `AnalysisBlock` 里（`AiTradeTraceRound.tsx:113-140`，其中
`:119-123` 是那段 `<p className="whitespace-pre-wrap">{trace.thinking}</p>`），也就是 §6.2 第 22 项
要**整体取代**的那个组件。v1 全文没有一句交代它去哪，而 `_TRACE_MAX_CHARS` 抬到 20000 之后，
那一段最长会是**两万字符、一次性整段渲染**。故明确：

- `AiTradeReasoningBlock` **接管** `thinking` 的呈现，作为研判段之上的一个默认折叠块，
  标题保持今日的「深度思考」（用户已经认得它，不改文案）。
- 流式期间复用与研判段**同一套**「只渲染尾部若干行」的策略（DOM 节点数与文本长度解耦），
  结算后按空行切段渲染；**不走** `renderMarkdown`——扩展思考不是 markdown，按 `pre-wrap` 纯文本
  渲染既真实又便宜。
- `thinking` 为空（默认，`AGENT_THINKING_BUDGET_TOKENS=0`）⇒ **整块不渲染**，与今日逐字一致。

**反思段同款**：`ReflectionBlock` 内的复核文本改用同一个组件（`kind="review"`，7 格导轨，
R6 事前验尸 / R7 裁决默认展开），与决策段视觉同源但用既有的 accent 边框区分。

#### 3.5.3 概览条与启动弹窗

- `TraceOverview` 增加一枚深度 chip（`深度研判 · deep`）与会话 token 累计；`reasoningPressure`
  非空时在其下渲染 §3.4.3 的提示条（`--warn` 色系，可一键跳到设置）。
- `AiTradeLaunchModal` 增加「研判深度」四选一（关闭 / 标准 / 深度（默认） / 极致），每档下方一行
  中文说明写清**代价**：「深度：每轮约 6–9k 字研判，用时约 2–4 分钟，token 约为标准档 2 倍」。
  选择「极致」且唤醒周期 < 6 分钟时，在周期输入下方给一条**非阻断**的黄色提示（不禁用提交——
  用户的交易节奏由用户决定）。
- **该选择器仅在实盘 tab 露出、且只随 live 分支下发**（v2 评审修订 · P2-2）。理由：启动弹窗的
  请求体里 `base` 对象是实盘/回测两 tab **共用**的，`wakeIntervalMinutes` 当初就因此被显式限定为
  「仅 live 分支带上」（`AiTradeLaunchModal.tsx:619-622` 的 `[RN-1]`）。深度选择器若落进 `base`，
  用户在回测 tab 选「深度」会直接推翻 §3.6 那道成本护栏——而那正是它存在的理由。回测 tab 按
  §3.6 只做**只读展示**当前回测档，改档走设置页。

---

### 3.6 轴 F —— 回测与成本护栏

- 回测独立深度档 `ai_trade_reasoning_depth_backtest`，默认 **`standard`**（不是 `deep`）：一次 500 轮
  的 AI 回测在 `deep` 档下 token 成本约为今日的 4–5 倍且墙钟成倍拉长，把它做成与实盘同档是把
  「用户点一次回测」变成一次意外的高额账单。回测面板需在启动处显式写明当前回测深度档。
- `backtest.py` 的决策器构造改用 `build_ai_trade_llm_clients(spec.provider, settings, profile=回测档)`，
  并把**同一个** `DepthProfile` 交给 `AiTradeDecider(..., depth_profile=…)`——回测车道的反思协议
  与实盘走的是同一条注入路径（§3.2 的评审修订），不存在第二种写法。其余（`allow_limit=False` 等
  既有取舍）**一字不改**。
- **回测车道必须有自己的装配用例**（v2 评审修订 · P1-4）：本仓库刚刚在
  `docs/plans/ai-trade-backstop-repair` 上栽过「双通道共用纯核心时只有自动侧被实现」，而这里的
  两条通道（`manager.py:657` 与 `backtest.py:322`）是两处**各自手抄**的装配点。§7.5 的 S-9 因此
  与实盘的 S-7 同形，且额外断言取到的是**回测档**（`standard`）而非实盘档。

---

## 四、接口设计

### 4.1 内部 Python 签名变更（全部末位带默认值，既有调用零改动）

```python
# prompts.py —— [v2·P1-8] 六个既有文本参数收进聚合结构，形参 13 → 6，兑现偏离表里那条还债指示
build_ai_trade_system_prompt(inst_id, bar, budget_usd, leverage, *,
                             position_step_pct=0.0, allow_limit=False, limit_band_pct=0.02,
                             texts: SystemPromptTexts | None = None)
#   六个旧文本关键字（base_rules_text / profit_guard_text / sizing_text / tactical_bars_text /
#   cost_text / horizon_text）保留为兼容垫片：收到即折进 `texts`，两种写法产出 sha256 相同
#   （G-1b 冻结）。本仓库有上百条冻结断言以关键字直连调用本函数，垫片不可省。
build_reflection_system_prompt(horizon_text: str = "", reasoning_text: str = "")
#   ⚠️ 唯一调用点是 `decider._stream_reflection`（每轮现造），**不是** manager/backtest（§3.2）。
build_decision_tool_description(allow_limit=False, *, limit_band_pct=0.02,
                                allow_hold_exit=False, allow_exit_priority=False,
                                lang: str = "zh")
build_decision_tool_schema(position_step_pct, *, allow_limit=False, limit_band_pct=0.02,
                           allow_hold_exit=False, allow_exit_priority=False,
                           allow_reasoning_digest: bool = False)

# prompts_en.py（新·v2）
render_spine_en(inst_id, bar, budget_usd, leverage, *, texts: SystemPromptTexts) -> str
# 三个英文纪律子句孪生：_tactical_discipline_en / _action_discipline_en / _size_discipline_en

# decider.py
AiTradeDecider(llm, system_prompt, ..., reflection_llm=None, summary_llm=None,
               allow_reasoning_digest: bool = False, depth_profile: DepthProfile | None = None)
#   depth_profile 是本特性在决策器上的**唯一**开关：超时（§3.1.1）、轮级墙钟闸（§3.3.5）、
#   追问阈、以及反思协议文本（§3.2）全部由它派生。None ⇒ 全部逐字回退到今日 settings 读法。
AiTradeDecider._attempt_decision(..., *, hint=None, extra_instruction: str | None = None)
#   [v2·P0-3] 追问提示走 `extra_instruction`，**不复用** `hint`（`hint` 的值域是 degrade 码，
#   是 `_retry_hint_suffix` 的查表键，塞自由文本进去会让那张表多一条永不匹配的分支）。
AiTradeDecider.last_reasoning -> ReasoningMeta | None      # 新只读属性
AiTradeDecider.last_reflection_analysis -> str             # 新只读属性

# providers.py
LlmProviderConfig(..., thinking_budget_tokens: int = 0)

# anthropic_client.py
AnthropicClient(..., thinking_budget_tokens: int = 0)

# schemas.py —— [v2·P1-1] v1 的变更清单漏了这个文件
AiTradeGroupStartIn(..., reasoningDepth: Literal["off","standard","deep","exhaustive"] | None = None)

# manager.py —— [v2·P1-1] 深度档必须落在**请求对象**上，而不是在装配时再读一次 settings
AiTradeGroupStartRequest(..., reasoning_depth: str | None = None)
```

`TradeDecision` 末位新增 `evidence_digest: str | None = None`（沿用 `exit_conviction` /
`hold_exit_px` 的先例：末位带默认 ⇒ 既有全部位置构造与解包零改动；非法值一律忽略、**绝不据它降级**）。

### 4.2 WS `agent_trace` 载荷（加法键，`off` 档 ⇒ 整键缺席）

`phase: "result"` 事件新增：

```json
{"type": "agent_trace", "data": {
  "phase": "result", "round": 42, "ts": 1756300000,
  "executed": {...}, "degradeReason": null,
  "reasoning": {"depth": "deep", "chars": 9412, "stages": 12,
                "truncated": false, "reasked": false, "reaskAdopted": false,
                "deadlineHit": false,
                "inputTokens": 12083, "outputTokens": 3401, "elapsedMs": 42160,
                "reflectionChars": 4180, "reflectionStages": 7}
}}
```

**刻意不新增 trace phase**：新增一个相位在本仓库的代价是 8 个前端镜像点，而研判元数据是
**轮末才定型的一份只读读数**，`result` 相位本就是手抄区，加一个整份 `reasoning` 对象即可
（沿用 `horizon` 相位「整份落 `horizonWire`」的先例，避免下次加字段时逐键手抄漏掉）。
流式期间的阶段导轨由前端从已累积的 `analysis` 文本现算，不占 WS 带宽。

**新增一个流式相位 `reask`**（v2 评审修订 · P0-3）：`{"phase": "reask", "delta": "…"}`，前端归约进
**独立字段** `reaskAnalysis`。这与「不新增 `result` 子相位」并不矛盾——`reasoning` 是轮末定型的
只读读数，适合整份挂在 `result` 上；而追问是一段**流式文本**，它必须有自己的累积槽位，否则就会
落到 `analysis` 上把首次研判覆盖掉（`store.ts:236-237` 是纯追加，唯一清零点是 `retry` 相位）。
`store.ts` 的 `retry` 分支须**一并清零** `reaskAnalysis`，与它今日清零 `analysis`/`thinking` 同理。

### 4.3 HTTP `GET /api/py/ai-trade/status`（加法键）

```json
{"members": [{"instId": "BTC-USDT-SWAP", "...": "...",
  "reasoningDepth": "deep",
  "reasoningTokens": {"inputTokens": 4210553, "outputTokens": 812004},
  "reasoningPressure": {"ewmaMs": 186000, "intervalS": 120,
                        "suggestIntervalMin": 6, "level": "warn"}}]}
```

三个键在 `off` 档下**整键缺席**（沿用本仓库「未装配即缺席，绝不出现半截空信息」的既有范式）。
~~⚠️ `response_model` 若对该端点生效，必须同步补字段~~——**评审复核后判定此处无待办**：
`/ai-trade/status` 返回裸 `dict` 且**无** `response_model`，其 docstring 甚至专门写着「该端点
`-> dict` 且无 `response_model`，故新键不会被 FastAPI 静默吞掉」（`api/ai_auto.py:507-513`）。
保留这句提醒作为背景（前科见 `docs/plans/ai-trade-backstop-repair`），但它不构成本特性的实施要点，
§8 的 R-12 同步降级。

### 4.4 启动请求 `POST /api/py/ai-trade/start`（可选加法字段）

```jsonc
{ "reasoningDepth": "deep" }   // 可选；缺省 ⇒ settings.ai_trade_reasoning_depth
```

越界值 422（`Literal` 校验），不做静默夹紧——一个拼错的深度档静默回落会让用户以为自己开了深度档。

> **【v2 评审修订 · P1-1】这个字段必须一路走到决策器，否则它会被接收、校验、然后丢弃。**
> v1 §6.1 的 16 项后端清单里既没有 `app/schemas.py`（`AiTradeGroupStartIn` 的定义处，
> `schemas.py:879-909`），也没有 manager 侧的 `AiTradeGroupStartRequest`（`manager.py:276`）；
> 而 §3.1.1 的 `resolve_depth_profile(settings, …)` **只读 settings**。照 v1 实现的结果是：
> 请求里的 `reasoningDepth` 通过 422 校验、进了 `start_wire`，然后在装配时被 settings 默认值覆盖。
>
> 落点链（三处，缺一即静默失效）：
> `AiTradeGroupStartIn.reasoningDepth` → `build_start_request` 解析进
> `AiTradeGroupStartRequest.reasoning_depth` → `manager` 装配时
> `resolve_depth_profile(settings, override=req.reasoning_depth)`。
>
> **续跑（暂停 → 恢复）自动同档，但前提正是上面这条链。** `api/ai_auto.py:392` 存的
> `start_wire=body.model_dump(mode="json")` 是启动请求**全文**，而冷续跑走的是
> `resume.py:80`「用 `start_wire` **重跑一遍** `/ai-trade/start` 的解析链（绝不另写一份）」。
> 所以只要深度档落在**请求对象**上，续跑就自动同档、无需任何额外代码；反之若只读 settings，
> 一次「深度档启动 → 暂停 → 续跑」会静默降回默认档，而这类漂移**从不报错**——那条解析链的
> docstring 里写得很清楚：「两处各写一遍必然漂移，而这里漂移的是止损百分比与账户凭证——
> 不会报错，只会让用户以为止损 1% 实际 5%」。§7.5 的 S-8 冻结这一点。

### 4.5 CLI：`scripts/ai_trade_cot_probe.py`（新，opt-in、走真实 provider）

```bash
services/quant/.venv/Scripts/python scripts/ai_trade_cot_probe.py \
    --inst BTC-USDT-SWAP --bar 30m --rounds 6 --out docs/plans/ai-trade-deep-reasoning-chain/probe.json
```

对**同一份真实 payload 集合**分别以 `depth=off`（= 合入前形态）与 `depth=deep` 各跑 `--rounds` 轮，
输出每轮的 `chars / stages / outputTokens / elapsedMs`，并打印判据行：

```
baseline(off)  median output tokens = 487
deep           median output tokens = 2914
ratio = 5.98x   → PASS (>= 5.0)
```

退出码 0=PASS / 1=FAIL。这是 G-3 的**唯一**权威判据（离线单测无法度量真实模型的话痨程度，
把 5× 写成离线断言只会得到一条自欺的绿灯）。

---

## 五、数据模型

### 5.1 进程内形状（无 DB 迁移）

- `DepthProfile`（frozen dataclass，见 §3.1.1）
- `ReasoningStage`（前端 interface，见 §3.5.1）
- `ReasoningMeta`（frozen dataclass，见 §3.4.1）
- `_StreamOutcome`（decider 私有，见 §3.3.3）
- `AiTradeLlmClients`（frozen dataclass，见 §3.3.1）
- `session._round_trace["reasoning"]`：与 §4.2 同形的 dict；轮次环形容量 `_TRACES_MAX=20` 不变。

> **【v2 评审修订 · P1-6】v1 的「20 轮 × 20000 字符 ≈ 400KB/会话」只算了三个 phase 里的一个。**
> `_TRACE_MAX_CHARS` 是**单 phase 单轮**上限（`trace.py:52` 的注释原文就是「整轮单 phase 累计文本
> 硬上限」），而一轮最多有 **四个**累积槽位：`analysis` / `thinking` / `reflection` / `reask`
> （最后一个是 v2 新增）。各 20000 ⇒ 80000/轮 ⇒ ×`_TRACES_MAX`(20) ⇒ **1.6MB**；而 `_traces` 是
> **每个成员一份**（`session.py:378`，每会话一个 deque），一个 5 标的分组即 8MB 常驻，并且 WS
> 快照重放会把它整份推给**每一个**新订阅者，而订阅队列只有 1000 个槽位、满了就静默丢
> （`session.py:410` / `:417-421`）。
>
> 故引入**单轮四槽合计**上限 `_TRACE_ROUND_TOTAL_MAX = 36000`（§3.3.4 常量表）：四个槽位共享
> 一份配额，先到先得、超出丢弃尾部。最坏量级回到 20×36000 ≈ **720KB/成员**、5 标的 ≈ 3.6MB。
> 真实分布下 `thinking` 与 `reask` 多数轮为空，实际远低于此。

### 5.2 落库（零迁移，仅 `metrics_json.aiStats.reasoning`）

见 §3.4.4。`ai_auto_repositories` 的读侧只需在既有 `_load_config(run.metrics_json)` 之后多读一个
键；**不新增列、不改 `append_live_trade` 的逐列手抄区**。

### 5.3 新增配置项（`services/quant/app/config.py`）

```python
    # ---- AI 自动交易 · 深度思维链（ai-trade-deep-reasoning-chain）----
    # 档位是**唯一**总闸：off ⇒ 三份 prompt 与本特性合入前逐字节相同（sha256 门禁 G-7）。
    # env 名 AI_TRADE_REASONING_* 已在 desktop config.js 的 prod 透传白名单内（AI_TRADE_ 前缀），
    # AGENT_THINKING_* 同理（AGENT_ 前缀）——两者都无需改 desktop 白名单，此处显式记录以免下次误改。
    ai_trade_reasoning_depth: Literal["off", "standard", "deep", "exhaustive"] = "deep"
    # 回测独立档，默认 standard：deep 档会让一次 500 轮回测的 token 成本涨到 4~5 倍（§3.6）。
    ai_trade_reasoning_depth_backtest: Literal["off", "standard", "deep", "exhaustive"] = "standard"
    # 0 ⇒ 用档位表的值；>0 ⇒ 覆盖。决策与反思各一份，绝不共用（共用正是根因 C-1 的形状）。
    ai_trade_reasoning_max_tokens: int = Field(default=0, ge=0, le=32768)
    ai_trade_reflection_max_tokens: int = Field(default=0, ge=0, le=32768)
    ai_trade_summary_max_tokens: int = Field(default=1536, ge=256, le=8192)
    # 留给收尾工具调用的输出 token；协议正文按它现算「预算纪律」那一段，绝不写死算例。
    ai_trade_reasoning_reserve_tokens: int = Field(default=768, ge=256, le=4096)
    # 浅研判追问阈（英文字符）与次数；-1 ⇒ 用档位表的值，0 ⇒ 关闭追问。
    ai_trade_reasoning_min_chars: int = Field(default=-1, ge=-1, le=40000)
    ai_trade_reasoning_reask_max: int = Field(default=1, ge=0, le=1)   # [终审 V-4] le 由 2 收紧为 1
    # 研判耗时压力提示阈（占唤醒周期的比例）；0 ⇒ 关闭提示。
    # 判据只在 wake_interval_s > 0 时成立——0 = 用户刻意关掉了定时唤醒（§3.4.3，P1-5）。
    ai_trade_reasoning_pressure_ratio: float = Field(default=0.6, ge=0, le=1)
    # 单轮墙钟总闸（秒）：一轮内「全部尝试 + 追问」共享；0 ⇒ 用档位表的 round_deadline_s（§3.3.5）。
    ai_trade_reasoning_round_deadline_s: float = Field(default=0.0, ge=0, le=3600)

    # ---- [v2 评审修订 · P0-4] **不再**调整这两个既有项的全局默认 ----
    # v1 曾提出 ai_trade_decision_timeout_s 60→210、ai_trade_reflection_timeout_s 45→150。
    # 评审否决：改全局默认会让 `AI_TRADE_REASONING_DEPTH=off` 之后一次挂死的 LLM 调用仍要 210 秒
    # 而非 60 秒才回落 hold——恰恰在「出事了要止血」的回退路径上把安全回落时延放大 3.5 倍，
    # 而 §九 的回滚清单里根本没有「超时」二字。回退开关必须回退**全部**行为。
    # 两个超时改为随 DepthProfile 派生（§3.1.1），off 档 = 今日的 60.0 / 45.0，逐字回退；
    # 下面两行保持**今日的值不变**，作为 depth_profile is None 时的回退真源。
    ai_trade_decision_timeout_s: float = 60.0     # 不变（今日值）
    ai_trade_reflection_timeout_s: float = 45.0   # 不变（今日值）

    # ---- Agent LLM 扩展思考（可选，默认 0 = 不发该字段 ⇒ 请求体逐字节不变）----
    # 仅 api_standard == "anthropic" 生效；>0 时强制 temperature=1 且移除 top_p（Anthropic 硬要求）。
    agent_thinking_budget_tokens: int = Field(default=0, ge=0, le=32768)
```

**新增一条 `model_validator`（启动即 fail-fast）**：

```python
@model_validator(mode="after")
def _check_reasoning_budget(self) -> "Settings":
    """预算必须容得下「思考 + 收尾工具调用」，否则每一轮都必然截断而**不报任何错**。"""
```

判据：对四档中当前生效的那一档，`decision_max_tokens > reserve_tokens + agent_thinking_budget_tokens`，
否则抛 `ValueError` 并在消息里写清三个数与如何调。理由：预算不足的症状是「每轮都降级观望」，
用户从界面上完全看不出是配置问题——这类不报错的空转只能在启动时挡下来（与既有
`_check_health_window` 同款理由）。

**校验器的两条补充**（v2 评审修订）：

1. **`exhaustive` 档的报错文案必须点名「这是模型侧上限」**（P2-4）：该档 `decision_max_tokens`
   取 16384，而各家模型的 `max_tokens` 上限并不一致，超限时 provider 会回 400。本地校验器管不到
   模型上限，故它至少要在 `exhaustive` 档的报错里写清「若 provider 回 400 且提示 max_tokens 超限，
   请调低 `AI_TRADE_REASONING_MAX_TOKENS` 或换档，这不是本地配置错误」——否则用户会在本地设置里
   反复找一个不存在的问题。
2. **校验器**只校验「当前生效的那一档」，不遍历四档**：一个用户永远不会用到的档位配得不合理，
   不该让整个后端起不来。这与 §3.1.1 里「拼错的运维开关不该让整个会话起不来」是同一条取舍。

### 5.4 `.env.example` 新增段（**必须补，否则漏登记是静默的**）

```dotenv
# ---- AI 自动交易 · 深度思维链（AI_TRADE_REASONING_*，spec ai-trade-deep-reasoning-chain §5.3）----
# off | standard | deep | exhaustive；off = 逐字节回退到本特性合入前的浅研判形态。
AI_TRADE_REASONING_DEPTH=deep
AI_TRADE_REASONING_DEPTH_BACKTEST=standard
AI_TRADE_REASONING_MAX_TOKENS=0
AI_TRADE_REFLECTION_MAX_TOKENS=0
AI_TRADE_SUMMARY_MAX_TOKENS=1536
AI_TRADE_REASONING_RESERVE_TOKENS=768
AI_TRADE_REASONING_MIN_CHARS=-1
AI_TRADE_REASONING_REASK_MAX=1
AI_TRADE_REASONING_PRESSURE_RATIO=0.6
# 单轮墙钟总闸（秒，全部尝试 + 追问共享）；0 = 用档位表的值（deep=600）。
AI_TRADE_REASONING_ROUND_DEADLINE_S=0
# [v2 评审修订·P0-4] 两个超时**不再**在此改默认：它们随深度档派生（off 档 = 60/45 逐字回退）。
# 下面两行是显式覆盖入口，留空即用档位值；写死会同时压住 off 档的回退语义，慎用。
# AI_TRADE_DECISION_TIMEOUT_S=
# AI_TRADE_REFLECTION_TIMEOUT_S=
# Anthropic 扩展思考（0=不启用，请求体逐字节不变；>0 时强制 temperature=1）
AGENT_THINKING_BUDGET_TOKENS=0
```

---

## 六、文件 / 模块变更清单

### 6.1 后端 `services/quant`

| # | 文件 | 新/改 | 一句话意图 |
|---|---|---|---|
| 1 | `app/ai_auto/reasoning_protocol.py` | 新 | 深度档纯核心：`DepthProfile` / S1–S12 / R1–R7 / 协议渲染 / 阶段计数 / 尾段摘录 |
| 2 | `app/ai_auto/prompt_glossary.py` | 新 | 中→英术语对照块 + 供漂移门禁扫描的标签清单 |
| 3 | `app/ai_auto/llm_budget.py` | 新 | 按调用点分配三份 LLM 预算（`replace(cfg,…)` + factory），零 Protocol 变更 |
| 4 | `app/ai_auto/prompts.py` | 改 | 英文脊柱分叉、英文纪律子句孪生、`evidenceDigest` schema、英文工具描述、英文收尾指令、反思英文协议 |
| 4b | `app/ai_auto/prompts_en.py` | **新**（v2·P1-8） | 英文脊柱 `render_spine_en` + 三个英文纪律子句孪生 + 英文工具描述/收尾指令。外移自 `prompts.py`：后者今日已 **919 行**，且**不在**`.claude-index/config.md` 的偏离表里，直接写进去即静默越线 |
| 5 | `app/ai_auto/decider.py` | 改 | 三客户端、`_StreamOutcome`（provider 归一的 stop_reason/usage，含 `message_done` 不再直接 break）、`reasoning_truncated` / `content_filtered`、浅研判追问（**独立 `reask` 相位** + `extra_instruction`）、轮级墙钟闸、`ReasoningMeta`、**反思协议注入**（`build_reflection_system_prompt(reasoning_text=…)` 的唯一调用点在此）、反思推理留存（只收 `text_delta`）、上限 ×5、摘要字符兜底。**注**：今日 746 行，本轮后约 900 行 ⇒ 须在 §6.1 第 16b 项登记越线 |
| 6 | `app/ai_auto/trace.py` | 改 | `_TRACE_MAX_CHARS` 4000→20000、`_TRACE_FLUSH_CHARS` 48→160、`REASONING_TRACE_KEYS` 单一真源 |
| 7 | `app/ai_auto/session.py` | 改 | journal 摘录换源、`result` 事件挂 `reasoning`、会话 token 累计、耗时 EWMA 与压力提示、`aiStats.reasoning` |
| 8 | `app/ai_auto/manager.py` | 改 | 解析深度档、装配三客户端、把协议文本传进两份系统 prompt、`allow_reasoning_digest` |
| 9 | `app/ai_auto/backtest.py` | 改 | 同上但取回测档；其余既有取舍一字不改 |
| 10 | `app/agent/providers.py` | 改 | `LlmProviderConfig.thinking_budget_tokens`（末位默认 0）+ 两个构造点填值 |
| 11 | `app/agent/llm/factory.py` | 改 | 把 thinking 预算透传给 `AnthropicClient`；`OpenAIClient` 显式不接 |
| 12 | `app/agent/llm/anthropic_client.py` | 改 | `thinking_budget>0` 时写 `thinking` 块并强制 `temperature=1` / 去 `top_p`；0 ⇒ 请求体逐字节不变 |
| 13 | `app/config.py` | 改 | §5.3 全部新增项 + 两个默认值调整 + `_check_reasoning_budget` 校验器 |
| 14 | `app/api/ai_auto.py` | 改 | `start` 请求可选 `reasoningDepth`；`status` 三个加法键（含 `response_model` 同步） |
| 15 | `scripts/ai_trade_cot_probe.py` | 新 | 真实 provider 的 5× 探针（G-3 的权威判据，opt-in） |
| 13b | `app/schemas.py` | **改**（v2·P1-1） | `AiTradeGroupStartIn.reasoningDepth`（可选 `Literal`）；v1 清单漏了本文件，而 §4.4 的字段就定义在这里（`schemas.py:879`） |
| 14b | `app/ai_auto/manager.py` 的 `AiTradeGroupStartRequest` | **改**（v2·P1-1） | `reasoning_depth` 字段：深度档必须落在**请求对象**上，否则装配时被 settings 默认值覆盖，且暂停→续跑会静默降档（§4.4） |
| 16 | `.env.example`（仓库根） | 改 | §5.4 新增段 |
| 16b | `.claude-index/config.md` | **改**（v2·P1-8） | 把偏离表首行 `build_ai_trade_system_prompt` 由「13 形参 / 待还」改为「6 形参 / 本轮已还（收进 `SystemPromptTexts`）」；**新增登记** `decider.py` 越 800 行线。`CLAUDE.md` §二明写「调整必须在此显式记录，不得静默放宽」，这一项与代码同提交 |

### 6.2 前端 `apps/web`

| # | 文件 | 新/改 | 一句话意图 |
|---|---|---|---|
| 17 | `lib/aiTradeReasoning.ts` | 新 | 阶段解析纯函数 + 中文阶段名映射 + 进度/摘要派生 |
| 18 | `lib/aiTradeTypes.ts` | 改 | `AiTradeReasoningWire`、`AiTradeRoundTrace.reasoning`、`reasoningPressure`、会话级 token |
| 19 | `lib/store.ts` | 改 | `result` 相位归约整份 `reasoning`；**新增 `reask` 相位**累积进独立字段 `reaskAnalysis`（绝不并进 `analysis`，v2·P0-3）；`retry` 相位一并清零 `reasoning` 与 `reaskAnalysis` |
| 20 | `lib/aiTrade.ts` | 改 | `DEGRADE_REASON_LABELS` 增 `reasoning_truncated`；深度 chip / 压力提示的 describe 函数 |
| 21 | `components/Agent/AiTradeReasoningBlock.tsx` | 新 | 分阶段研判视图（四态：流式导轨 / 收起摘要 / 展开逐阶段 / **扩展思考段**）。**同时接管 `trace.thinking` 的呈现**（v2·P1-3）——今天渲染它的 `<details>` 就长在被取代的 `AnalysisBlock` 里（`AiTradeTraceRound.tsx:119-123`），不接管即丢失；并新增「深化追问（已采纳／未采纳）」段（v2·P0-3） |
| 22 | `components/Agent/AiTradeTraceRound.tsx` | 改 | 用新组件取代 `AnalysisBlock`；`ReflectionBlock` 内文改用 `kind="review"` |
| 23 | `components/Agent/AiTradeAgentTrace.tsx` | 改 | 概览条加深度 chip / token 累计 / 压力提示条 |
| 24 | `components/Agent/AiTradeLaunchModal.tsx` | 改 | 「研判深度」四选一 + 代价说明 + 非阻断的周期建议 |
| 25 | `components/Agent/aiTradeStats/AiTradeStatsDetail.tsx` | 改 | 历史会话详情增「研判概况」行（阶段中位数 / 截断轮 / token） |

### 6.3 测试

| # | 文件 | 新/改 |
|---|---|---|
| 26 | `tests/test_ai_trade_reasoning_protocol.py` | 新 |
| 27 | `tests/test_ai_trade_prompt_glossary.py` | 新 |
| 28 | `tests/test_ai_trade_llm_budget.py` | 新 |
| 29 | `tests/test_agent_llm_thinking.py` | 新 |
| 30 | `tests/test_ai_trade_prompts.py` | 改 |
| 31 | `tests/test_ai_trade_decider.py` | 改 |
| 32 | `tests/test_ai_trade_reflection.py` | 改 |
| 33 | `tests/test_ai_trade_agent_trace.py` | 改 |
| 33b | `tests/test_ai_trade_reasoning_assembly.py` | **新**（v2·P1-4） | 三条生产装配链（manager / backtest / resume）的 `system` 捕获断言，见 §7.5 S-7/S-8/S-9 |
| 34 | `apps/web/lib/aiTradeReasoning.test.ts` | 新 |
| 35 | `apps/web/components/Agent/AiTradeReasoningBlock.test.tsx` | 新 |

> **【v2 评审修订 · P1-4】为什么必须单开一个「装配」测试文件。**
> v1 的 10 个测试文件里没有任何一条覆盖 `manager.py:657` / `backtest.py:322` / `resume.py` 这三个
> 装配点：prompt 用例全在直调 `build_*`，决策器用例全在手工构造决策器。于是「协议根本没被装配
> 进去」这一整类缺陷（P0-5 就是它的一个实例）在 CI 里**完全不可见**。本仓库刚在
> `docs/plans/ai-trade-backstop-repair` 上栽过同形的一次（双通道共用纯核心，只有自动侧被实现）。
> 判据必须取自**假客户端 `stream(system=…)` 实际收到的那一份字符串**，而不是任何 `build_*` 的
> 直接返回值——这是「写点有、读点无」唯一能被机器发现的角度。

---

## 七、测试与验收标准

> 后端跑法：`cd services/quant && .venv\Scripts\python -u -m pytest tests -q`（**必须 `-u`**，
> 否则长跑无输出被判挂起；**不可并发跑全量**，多份用例共用同一个临时 DB）。
> 前端跑法：`cd apps/web && NODE_ENV=test pnpm vitest run`（组件用例缺 `NODE_ENV=test` 会
> 报 React `act()` 错）。源码正则扫描类用例一律写 `\r?\n`（`core.autocrlf=true`）。

### 7.1 零回归门禁（`off` 档 = 合入前形态）

| 用例 | 断言 |
|---|---|
| G-1 `test_off_depth_keeps_three_prompts_byte_identical` | `sha256(build_ai_trade_system_prompt(...))` 在「不传 `texts`」与「传全空 `SystemPromptTexts()`」两种写法下相等；`build_reflection_system_prompt()` 同；`build_decision_prompt(payload)` 在 payload 无 `reasoningDepth` 键时与今日相同 |
| G-1b `test_legacy_text_kwargs_equal_aggregated_texts`（v2·P1-8） | 六个旧文本关键字（`base_rules_text` / `profit_guard_text` / `sizing_text` / `tactical_bars_text` / `cost_text` / `horizon_text`）逐一直传，与折进 `SystemPromptTexts` 传入，产出 sha256 **相等**。这条用例是聚合迁移的安全网：仓库里有上百条冻结断言以关键字直连调用本函数 |
| G-7b `test_off_depth_restores_today_timeouts`（v2·P0-4） | `resolve_depth_profile(off).decision_timeout_s == 60.0` 且 `.reflection_timeout_s == 45.0` 且 `.max_attempts_cap == 0` 且 `.round_deadline_s == 0.0`——即 `off` 档下超时、尝试次数与墙钟闸**全部**回到合入前形态，而不只是 prompt |
| G-2 `test_off_depth_tool_schema_identity` | `build_decision_tool_schema(0.0) is DECISION_TOOL_SCHEMA` 仍成立；`allow_reasoning_digest=True` 时返回**新 dict** 且模块常量未被就地改 |
| G-3 `test_off_depth_tool_description_identity` | `build_decision_tool_description()` 恒等返回 `DECISION_TOOL_DESCRIPTION` |
| G-4 `test_thinking_budget_zero_keeps_request_body_identical` | `AnthropicClient(thinking_budget_tokens=0)._body(...)` 与冻结的期望 dict 逐键相等，且 `"thinking" not in body`、`top_p` 仍在 |
| G-5 `test_decider_without_extra_clients_uses_the_single_client` | 只传一个 `llm` 构造决策器时，反思与摘要都打在同一个假客户端上（既有 32 个假客户端零改动的可证伪判据） |
| G-6 `test_backtest_default_depth_is_standard` | `resolve_depth_profile(settings, backtest=True).name == "standard"` |

### 7.2 协议与术语表（纯核心，离线）

| 用例 | 断言 |
|---|---|
| P-1 `test_protocol_declares_all_twelve_stages_in_order` | 渲染文本中 `## S1`…`## S12` 全部出现且**下标单调递增**；`## S13` 不出现 |
| P-2 `test_protocol_states_word_floor_and_budget_from_profile` | `deep` 档正文含 `120 words`、含 `8192 output tokens`、含 `768`；换档后三个数**跟着变**（防写死算例） |
| P-3 `test_protocol_puts_the_tool_call_hard_constraint_last` | 协议 + 纪律拼出的系统 prompt 以收尾硬约束句结束（近因位，镜像今日 RC-4） |
| P-4 `test_language_contract_names_the_three_chinese_fields` | 契约段含且仅含 `reason` / `evidenceDigest` / `exitEvidence` 三个中文例外字段 |
| P-5 `test_review_protocol_declares_seven_stages_and_keeps_safety_anchors` | `## R1`…`## R7` 齐全；英文脊柱仍含「only tighten」「name one substantive defect」「missing a well-evidenced trade costs exactly as much」三条冻结锚点（对应今日 `test_reflection_system_prompt_keeps_frozen_safety_anchors`） |
| P-6 `test_count_stages_tolerates_partial_and_extra_markers` | 流式半截文本 / 漏阶段 / 乱序 / `####` 四级标题四种输入都不抛错且计数合理 |
| P-7 `test_tail_stage_excerpt_prefers_s12` | 有 S12 ⇒ 取 S12 段前 400 字符；无 S12 ⇒ 返回 `""` |
| G-2′ `test_every_chinese_evidence_label_is_in_the_glossary` | 从 `prompts.py` 与 8 个 `*_text` 模块源码正则抽出「【…】」段标题与冒号前中文标签，断言全部命中 `GLOSSARY` 左列（**这条用例的价值就是会因为别人加标签而判红**） |

### 7.3 决策器行为（离线，假客户端）

| 用例 | 断言 |
|---|---|
| D-1 `test_max_tokens_stop_reason_maps_to_reasoning_truncated` | 假客户端只吐文本、`message_done(stop_reason="max_tokens")` ⇒ degrade 为 `reasoning_truncated`（不是 `no_tool_call`） |
| D-1b `test_openai_length_finish_reason_also_maps_to_truncated`（v2·P0-1） | 同款输入但 `stop_reason="length"`（OpenAI 标准把 `finish_reason` 塞进同一字段，`openai_client.py:147-149`）⇒ **同样**得到 `reasoning_truncated`。**这条用例的价值就是会因为有人把判据写回 `== "max_tokens"` 而判红** |
| D-1c `test_content_filter_is_not_retryable`（v2·P0-1） | `stop_reason="content_filter"` ⇒ degrade 为 `content_filtered` 且**不在** `_RETRYABLE_DEGRADE_REASONS` 里（重试同一份 prompt 只会再被拦一次，白烧一次预算） |
| D-2 `test_truncated_retry_hint_asks_for_compression` | 第二次尝试的 user 文本含压缩指令，且**不含**通用 hint；`_context` / `_journal` 未被污染 |
| D-3 `test_shallow_analysis_triggers_exactly_one_reask` | 首次研判 200 字符（阈 3000）⇒ 恰好多打一次 LLM；`last_reasoning.reasked is True` |
| D-4 `test_shallow_reask_never_downgrades_the_decision` | 追问失败（超时/无工具）⇒ **保留第一次的 `open_long`**，degrade 恒 `None`（本轴最重要的一条：追问是单向增益）；且 `last_reasoning.reask_adopted is False` |
| D-5 `test_shallow_reask_keeps_the_deeper_of_the_two` | 追问更深 ⇒ 采纳追问结果、`reask_adopted is True`；追问更浅 ⇒ 保留首次、`reask_adopted is False` |
| D-5b `test_reask_emits_into_its_own_phase`（v2·P0-3） | 捕获 emit 序列：追问的文本增量全部落在 `phase="reask"`，`phase="analysis"` 在追问期间**一帧都不再新增**——即首次研判在轨迹流上不被覆盖也不被追加污染 |
| D-5c `test_reask_uses_extra_instruction_not_hint`（v2·P0-3） | 追问那次尝试的 user 文本含追问提示、**不含**任何 `_retry_hint_suffix` 文案；`_context` / `_journal` 未被污染 |
| D-5d `test_round_deadline_stops_new_attempts_only`（v2·P0-4） | 墙钟耗尽后不再发起新尝试，但**不中断在飞的那一次**；沿用当前 `degrade`（不新造降级码）；`last_reasoning.deadline_hit is True` |
| D-6 `test_reask_disabled_when_min_chars_is_zero` | `reask_max=0` 或 `min_chars=0` ⇒ LLM 调用次数与今日相同 |
| D-7 `test_usage_is_normalised_and_carried_into_meta` | anthropic 风格 `{input_tokens, output_tokens}` ⇒ `ReasoningMeta.input_tokens/output_tokens` 正确；缺失 ⇒ `None` 而非 0 |
| D-8 `test_reflection_analysis_is_retained_and_bounded` | 反思推理落 `last_reflection_analysis`，超 20000 被截断；反思超时/取消 ⇒ 维持入口清零的空串 |
| D-9 `test_analysis_cap_is_twenty_thousand` | 25000 字符输入 ⇒ `len(last_analysis) == 20000` |
| D-10 `test_cancelled_error_still_propagates` | 决策与反思两条路径上 `asyncio.CancelledError` 仍原样上抛（安全契约冻结） |
| D-11 `test_evidence_digest_is_optional_and_never_downgrades` | 缺席 / 非字符串 / 超长 三种输入都不产生 degrade；超长按 400 截断 |
| D-12 `test_english_digest_falls_back_and_never_downgrades`（v2·P1-7） | `evidenceDigest` 写成纯英文 ⇒ `is_probably_chinese` 判否 ⇒ 摘录回落到 S12 尾段；**degrade 恒 `None`、`TradeDecision` 其余字段逐字不变、不触发追问**（一个摘要写成英文是排版问题，不是安全问题） |

### 7.4 预算装配与传输层

| 用例 | 断言 |
|---|---|
| B-1 `test_three_clients_get_three_budgets` | `deep` 档 ⇒ 决策 8192 / 反思 5120 / 摘要 1536 |
| B-2 `test_explicit_setting_overrides_profile_budget` | `ai_trade_reasoning_max_tokens=12000` ⇒ 覆盖档位值；`0` ⇒ 用档位值 |
| B-3 `test_thinking_budget_forces_temperature_one_and_drops_top_p` | `thinking_budget=4096` ⇒ body 含 `thinking`、`temperature==1.0`、无 `top_p` |
| B-4 `test_thinking_budget_is_dropped_for_openai_standard` | `api_standard="openai"` ⇒ `OpenAIClient` 构造不含该参数，body 无 `thinking` |
| B-5 `test_thinking_budget_larger_than_max_tokens_is_refused` | 预算不合法 ⇒ 降为 0 + 一条 warning 日志，**不发**必然 400 的请求 |
| B-6 `test_settings_validator_rejects_impossible_budget` | `reserve=4096` + `max_tokens` 覆盖为 2048 ⇒ `Settings()` 构造即抛，消息含三个数；`exhaustive` 档的报错文案含「模型侧上限」提示（v2·P2-4）；**只校验当前生效档**，另外三档配得再离谱也不阻止启动 |
| B-7 `test_openai_second_message_done_backfills_usage`（v2·P0-2） | 假客户端按 OpenAI 真实顺序发两条 `message_done`：第一条带 `finish_reason` 且 `usage=None`，第二条 `choices` 为空只带 usage ⇒ `ReasoningMeta.input_tokens/output_tokens` **取到真值**（今日的「首条即 break」写法在此判红） |
| B-8 `test_events_after_message_done_are_ignored`（v2·P0-2） | 首条 `message_done` 之后到达的 `text_delta` / `tool_use_start` / `error` 一律不生效：`last_analysis` 与「收尾即 break」的结果**逐字相同**，且晚到的 `error` 不把一轮已成功的决策变成 `llm_error` |
| B-9 `test_usage_absent_marks_provider_unknown`（v2·P0-2） | provider 全程不回 usage ⇒ `inputTokens/outputTokens` 为 `None` 且 `aiStats.reasoning.tokensProviderKnown is False`（与「真的没花 token」区分开） |

### 7.5 会话与呈现（后端）

| 用例 | 断言 |
|---|---|
| S-1 `test_result_event_carries_reasoning_block` | `agent_trace` 的 `result` 帧含完整 `reasoning` 对象；`off` 档 ⇒ 整键缺席 |
| S-2 `test_journal_and_decision_event_share_one_excerpt`（v2·P1-2 扩） | 有 `evidenceDigest` ⇒ journal 的 `analysis` 是它；无 ⇒ S12 尾段；再无 ⇒ 今日的前 400 字符。**并断言同一轮里 `decision` 事件的 `analysis` 取到的是同一份**——`session.py:1627` 的 `excerpt` 有两个消费者（`commit_round` 与 `_emit_decision`），换源等于同时改掉左栏决策卡首段 |
| S-3 `test_journal_excerpt_is_never_the_s1_boilerplate` | 12 阶段全文入参下，journal 摘录**不以** `## S1` 开头（C-6 的可证伪判据） |
| S-4 `test_pressure_note_appears_only_above_ratio` | EWMA 未过阈 ⇒ 整键缺席；过阈 ⇒ `suggestIntervalMin` 算式正确且按 `wake_interval_ceil_min` 夹紧；wire 字段名是 `ewmaMs`（不是 `p50Ms`） |
| S-4b `test_pressure_note_absent_when_interval_wake_disabled`（v2·P1-5） | `wake_interval_s == 0`（用户刻意关闭定时唤醒）⇒ **整键恒缺席**，无论耗时多长。v1 的判据在此退化为 `ewma > 0` 恒真，会给一个不按周期交易的用户永久挂一条无意义的建议 |
| S-5 `test_ai_stats_reasoning_is_written_on_finalize` | `metrics_json.aiStats.reasoning` 六个统计量正确，且**不新增任何列** |
| S-6 `test_trace_flush_threshold_keeps_frame_count_flat` | **单轮四个槽位（analysis / thinking / reflection / reask）合计**的 delta 帧数 ≤ 今日单轮帧数 × 2（v2 修订：v1 只算了 analysis 一个槽位，而扩展思考开启后 thinking 是同量级的第二个帧源） |
| S-6b `test_round_total_trace_cap_is_enforced`（v2·P1-6） | 四个槽位合计超过 `_TRACE_ROUND_TOTAL_MAX=36000` 时按先到先得丢弃尾部；20 轮 × 单成员的常驻内存上界可断言 |
| S-7 `test_assembled_system_prompts_carry_both_protocols`（v2·P0-5 / P1-4） | **实盘车道装配用例**：经 `manager` 起一个装配好的会话，用假客户端捕获 `stream(system=…)` 实际收到的字符串，断言决策侧含 `## S12`、**反思侧含 `## R7`**。判据取自模型真正收到的那一份，而不是任何 `build_*` 的返回值——这是「协议根本没被装配进去」唯一能被机器发现的角度 |
| S-8 `test_start_request_depth_survives_pause_resume`（v2·P1-1） | `reasoningDepth="exhaustive"` 启动 → 暂停 → 冷续跑（经 `start_wire` 重跑 `build_start_request`）⇒ 续跑后的 `DepthProfile.name` 仍是 `exhaustive`（若深度档只读 settings，这里会静默降回 `deep`，且**不报任何错**） |
| S-9 `test_backtest_lane_assembles_backtest_depth`（v2·P1-4） | **回测车道装配用例**：同 S-7 但走 `backtest.py`，且断言取到的是**回测档**（默认 `standard`）而非实盘档——`manager.py:657` 与 `backtest.py:322` 是两处各自手抄的装配点 |

### 7.6 前端

| 用例 | 断言 |
|---|---|
| F-1 `parses twelve stages with chinese titles` | 12 段全 ⇒ 12 个 `ReasoningStage`，`titleZh` 取自静态映射而非模型措辞 |
| F-2 `tolerates missing and streaming-partial stages` | 漏 S3 ⇒ 导轨 11 实 1 灰、不抛错；半截 S7 ⇒ `complete === false` |
| F-3 `falls back to flat rendering without stage markers` | 无标记 ⇒ 回退整段渲染 + 「未分阶段」标识 |
| F-4 `collapsed header prefers evidenceDigest` | 收起态标题依次取 `evidenceDigest` → S12 首句 → `reason` |
| F-5 `expanded view opens S11 and S12 by default` | 展开态下 S11/S12 的 `<details open>`，其余关闭 |
| F-6 `truncated round shows the budget chip` | `reasoning.truncated` ⇒ 出现「输出预算耗尽（已截断）」chip |
| F-7 `streaming view renders only the active stage tail` | 9000 字符流式输入下渲染的文本节点数 ≤ 阈值（DOM 与长度解耦的可证伪判据） |
| F-8 `renders model text through the safe markdown path` | 含 `<img onerror=…>` / `javascript:` 链接的模型文本不产生原始 HTML（安全铁律回归） |
| F-9 `reask segment never overwrites the first analysis`（v2·P0-3） | 依次喂 `analysis` 增量与 `reask` 增量 ⇒ 两段**并列存在**、阶段导轨仍读 12/12（不是 24/12）；`reaskAdopted=false` 时该段标题为「深化追问（未采纳，保留首次研判）」且**不隐藏** |
| F-10 `thinking block survives the component swap`（v2·P1-3） | `trace.thinking` 非空 ⇒ 新组件里仍有「深度思考」折叠块，流式期只渲染尾部若干行；为空 ⇒ 整块不渲染（与今日逐字一致） |
| F-11 `stage split agrees with the markdown heading rule`（v2·P2-1） | `##S1`（无空格）与 `   ## S1`（缩进）**都不**被当作阶段标记——三处正则（后端计数 / 前端切段 / `markdown.ts:8` 的 `HEADING_RE`）必须同一口径 |

### 7.7 验收（人工 + 探针）

1. `python scripts/ai_trade_cot_probe.py --rounds 6` 的 `ratio ≥ 5.0`（G-3 **对真实模型**的权威
   判据），报告归档到 `docs/plans/ai-trade-deep-reasoning-chain/probe.json`。
   1b. **【v2 评审修订 · P1-4】G-3 必须另有一条能在 CI 里判红的离线替身。** 探针要真实 provider
   与 API key，属 opt-in，CI 跑不了；而「协议根本没进 prompt」这一整类静默失效（P0-5 即其一例）
   恰恰会让全部离线用例照绿。故新增离线结构性判据
   `test_deep_profile_prompt_is_at_least_five_times_the_off_profile`：同一组入参下
   `len(系统 prompt @deep) / len(系统 prompt @off) >= 5`。它证不了模型会不会真的多写，
   但能挡住「协议没被装配」与「协议被删剩一句」两类回归——那是探针在 CI 里唯一能留下的影子。
2. 桌面端起一个真实 paper 会话，肉眼确认：流式期间导轨推进、结算后收起态是中文摘要、
   展开后 12 段中文阶段名齐全、反思段 7 段齐全、概览条显示 token 累计。
3. `AI_TRADE_REASONING_DEPTH=off` 重启，确认界面与合入前**没有任何差异**（含没有多出来的空 chip）；
   并确认**行为**也回退：单次决策超时回到 60 秒、重试次数不被封顶、无轮级墙钟闸、无追问
   （v2·P0-4——回退开关必须回退全部行为，不只是 prompt）。
4. 全量后端 pytest 绿（基线：合入前 3627 passed + 7 skipped，本特性新增用例后应为 3627+N）。

---

## 八、风险与缓解

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R-1 | **预算耗尽在工具调用之前** ⇒ 每轮都降级观望，且看不出原因 | 致命（系统停摆且静默） | ① `stop_reason` 检测 → `reasoning_truncated` 显式码 + 界面 chip（§3.3.3）；② 协议正文里的「输出预算纪律」明确要求留出收尾预算并给出「宁可分析不完整也要调工具」的优先级；③ 截断专用的压缩重试提示；④ `Settings` 启动校验挡住不可能的预算组合 |
| R-2 | **研判耗时 > 唤醒周期** ⇒ 轮次持续排队，实际交易频次被推理拖慢 | 高（改变交易行为而用户不知情） | 只读的压力提示（§3.4.3）+ 启动弹窗的深度代价说明 + 非阻断的周期建议。**刻意不自动改周期**——静默替用户改交易节奏是更坏的形态 |
| R-3 | **token 成本 5 倍** | 高（真金） | 四档可选、回测独立默认 `standard`、启动弹窗明写代价、逐轮与会话级 token 落痕并在 UI 可见、`off` 一键回退 |
| R-4 | 深度追问变成新的「永不开单」吸收态 | 高（本仓库前科） | 追问**永不降级**（D-4 是硬门禁用例）、至多 1 次、失败即保留原决策 |
| R-5 | 英文指令层 + 中文证据层的**语言混排**让模型误读 | 中 | ① 显式语言契约；② 术语对照表 + 会判红的漂移门禁；③ 阶段 S9 强制「用英文复述每条铁律及其阈值」——把风险变成一次可审计的理解力校验；④ 探针脚本人工复核 6 轮全文 |
| R-6 | 面向中文操作员的界面被英文淹没 | 中（HCI 倒退） | 中文阶段名静态映射（不依赖模型措辞）、中文 `evidenceDigest` 作为收起态标题、`reason` 强制中文、流式只显当前阶段 |
| R-7 | `thinking` 字段被非 Anthropic 官方端点（KIMI Coding）拒绝 400 | 中 | 默认 `0` = **不发该字段**（请求体逐字节不变，用例 G-4 冻结）；开启是显式运维动作；合法性预检不通过即降为 0 并告警 |
| R-8 | WS 帧暴涨导致 `_push` 静默丢帧（`QueueFull: pass`） | 中（界面缺段而不报错） | `_TRACE_FLUSH_CHARS` 48→160 + 用例 S-6 的帧数上界断言 |
| R-9 | 32 个假 LLM 客户端被签名变更打翻 | 中（改动面失控） | **不改 `LLMClient.stream` 签名**，预算走构造参数（§3.3.1）；用例 G-5 冻结这一取舍 |
| R-10 | 大量中文断言的既有 prompt 用例判红 | 中 | 既有断言全部保留并改挂到 `off` 档（它们本就是 `off` 档的行为真源）；英文路径新写一套断言。**不允许**删掉旧断言了事 |
| R-11 | journal 被样板抬头喂满 ⇒ 长期记忆退化 | 中（隐蔽、慢性） | 摘录换源三级回退（§3.3.4）+ 用例 S-3 |
| R-12 | ~~`response_model` 静默吞掉新 wire 键~~ | 低（**复核后降级**） | 评审已核实 `/ai-trade/status` 返回裸 `dict` 且无 `response_model`（`api/ai_auto.py:507-513`），前科不适用于本端点。仍保留用例 S-1 断言**经端点出去的**载荷而非内部 dict |
| R-13 | **provider 口径差异让新逻辑只在一半 provider 上生效**（v2 评审新增） | 高（静默半瘫） | 本轮已发现两例：截断码只认 Anthropic 的 `max_tokens`（P0-1）、usage 在 OpenAI 标准下因「首条 `message_done` 即 break」恒空（P0-2）。缓解：`TRUNCATION_STOP_REASONS` 作为归一单一真源（§3.3.3）；用例 D-1b / B-7 **各自以另一个 provider 的口径**判红；`tokensProviderKnown` 把「没花 token」与「没被告知」区分开 |
| R-14 | **推理墙钟悄悄改变账户的交易频率**（v2 评审新增） | 高（改变交易行为且不经任何交易判据） | 最坏单轮曾达 ~20 分钟而默认唤醒周期只有 2 分钟，其间 `_deciding` 守卫丢弃全部 K 线与信号翻转唤醒。缓解：档位派生的超时 + `max_attempts_cap` + 轮级墙钟闸（§3.3.5），`deadlineHit` 在界面上明示，压力提示首轮即发声 |
| R-15 | **回退开关只回退了 prompt**（v2 评审新增） | 高（在止血路径上失效） | v1 把两个超时改成全局默认，使 `off` 档下安全回落时延从 60s 变成 210s。缓解：超时随 `DepthProfile` 派生、`off` 档 = 今日值；用例 G-7b 冻结；§7.7 第 3 条的人工验收同时验**行为**而不只是界面 |

---

## 九、实施顺序与回滚

建议按「纯核心 → 传输层 → 决策器 → 会话 → 装配 → 前端」推进，每一步都可独立跑测：

1. `reasoning_protocol.py`（含 `TRUNCATION_STOP_REASONS` / `is_probably_chinese` / `DepthProfile`
   的超时与墙钟字段）+ `prompt_glossary.py` + 用例 P-1…P-7、G-2′（无任何调用方，纯加法）。
2. `providers.py` / `factory.py` / `anthropic_client.py` + 用例 G-4、B-3…B-5（默认 0 ⇒ 全仓零行为变化）。
3. `config.py`（新设置 + 校验器）+ `.env.example` + 用例 B-6。
4. `llm_budget.py` + 用例 B-1、B-2、G-5、G-6。
5. `prompts_en.py`（新）+ `prompts.py` 的 `SystemPromptTexts` 聚合迁移与英文分叉 + 用例
   G-1、G-1b、G-2、G-3、P-3、P-5，并把既有中文断言改挂 `off` 档。**本步同提交更新
   `.claude-index/config.md`**（偏离表首行 13→6「已还」）——`CLAUDE.md` §二要求阈值变动显式登记。
6. `decider.py`（三客户端 / 截断 / 追问 / 元数据 / 上限）+ 用例 D-1…D-11、D-9。
7. `trace.py` + `session.py` + 用例 S-1…S-6。
8. `schemas.py` / `manager.py` / `backtest.py` / `api/ai_auto.py` 装配（含 `reasoningDepth` 的
   三段落点链，§4.4）+ **用例 S-7 / S-8 / S-9**。这三条装配用例是本特性唯一能发现「协议根本没被
   装上」的角度，**不可推迟到最后**——它们要先于前端存在，否则第 9 步会在一份实际是空协议的
   数据上调 UI。同步在 `.claude-index/config.md` 登记 `decider.py` 越 800 行线。
9. 前端 17–25 + 用例 F-1…F-8。
10. `scripts/ai_trade_cot_probe.py` 并跑一次真实探针，归档 `probe.json`。

**回滚**：任何阶段出问题，`AI_TRADE_REASONING_DEPTH=off` 单键即可让三份 prompt、预算、追问、
截断码、wire 加法键**以及超时、重试次数上限、轮级墙钟闸**全部回到合入前形态（前端对缺席键的
处理一律是「整段不渲染」）。**最后那三项是 v2 评审补进这句话的**（P0-4）：v1 把两个超时改成
`config.py` 的全局默认，于是 `off` 之后一次挂死的 LLM 调用仍要 210 秒而非 60 秒才回落 `hold`
——一个只回退界面不回退行为的开关，在真正需要它的那一刻是最危险的。用例 G-7b 与 §7.7 第 3 条
的人工验收共同冻结这一点。`AGENT_THINKING_BUDGET_TOKENS=0` 是第二个独立的回退开关。

---

## 十、三个已知待定项 —— 评审裁定（v2）

v1 把三个问题留给了评审节点。三条均已裁定，正文已按裁定改完，实现节点**不需要**再就这三条做
判断；若实测数据与裁定冲突，按第 2 条给出的口径处理。

**1. 语言边界：维持原方案，不追加轴 G。**

裁定：**采纳 v1 的取舍**——五段宪章与 ~150KB 证据渲染层保持中文，推理语言由指令层决定。
v1 给的三条理由（数据行语言中立 / 分属另外 10 个特性且带 sha256 冻结门禁 / 用术语表 + S9 英文
复述把风险变成可审计动作）全部成立。评审另补一条支持证据：`_context` 里逐轮累积的是
「决策 prompt + 结构化工具入参 + tool_result」三条消息，**不含**模型的研判正文
（`decider.py:606-610`），所以中文证据层的体量不会随思维链变长而被反复重读放大——语言混排的
成本是一次性的。

**但附一个条件**：G-5「面向操作员的产出仍是中文」原先没有任何可证伪判据（P1-7）。既然把语言
边界画在指令层，那么「模型的三个中文出口真的是中文」就必须被验，否则这条边界只是个愿望。
已在 §3.1.7 增补 `is_probably_chinese` 呈现层守卫 + 用例 D-12。

**2. 超时默认值：否决 v1 的写法，改为随档位派生。**

裁定：**不接受**把 `ai_trade_decision_timeout_s` 60→210 / `ai_trade_reflection_timeout_s` 45→150
改成 `config.py` 的全局默认（P0-4）。两条理由：
- 它让 `off` 档不再是回退——止血开关按下去之后，安全回落的时延仍是 3.5 倍；
- 它与默认 `max_retries=3` 相乘，把最坏单轮墙钟推到 ~20 分钟，而默认唤醒周期是 2 分钟，
  这 20 分钟里所有 K 线确认与信号翻转唤醒都被 `_deciding` 守卫丢弃——**推理深度会在用户毫不
  知情的情况下改变这个账户的交易频率，且不经过任何一条交易判据**。

改为：超时、尝试次数上限、轮级墙钟闸三者一并挂在 `DepthProfile` 上（§3.1.1 / §3.3.5），
`off` 档取今日的 60/45/不封顶。

**关于「按实测收紧」**：v1 的建议本身是对的，予以保留并收窄口径——探针跑完后，按
**p95 而非 p50** 收紧 `DepthProfile.decision_timeout_s`，并同步调 `round_deadline_s` 使
`round_deadline_s ≥ 2 × decision_timeout_s`（至少容得下一次重试），两个数一起改、不单动其一。

**3. `exhaustive` 档：保留四档，但补两条约束。**

裁定：**保留**。删除的收益确实极小，而这一档有真实场景——日线/12 小时执行周期、一天几轮的
慢速账户，那里 40 分钟一轮完全不构成问题，而 200 词/阶段的深度是有意义的。四档并不是过度设计，
过度设计的是「四档但只有一档被验证过」。故补两条约束（已落 §5.3 / §7.4）：
- `_check_reasoning_budget` 对该档的报错文案必须点名「若 provider 回 400 提示 max_tokens 超限，
  那是模型侧上限、不是本地配置错误」——16384 超出部分模型的上限，而本地校验器看不见那个上限；
- 用例 B-6 覆盖该档；启动弹窗对该档的代价说明必须写出**分钟级**的单轮耗时预期，而不只是 token。


---

## 评审结论（Review Verdict）

### 有条件通过（Approved with conditions）

本方案的**问题定位是准确的、少见地准确**。§2.3 那句总结——「思维链浅不是模型不愿意想，而是：
没有协议告诉它想什么、没有预算让它写下来、写超了会被静默吞掉、写出来了也留不住」——把一个容易
被做成「换个更长的 prompt」的任务，拆成了四个各自可验证的物理约束，并且四条**全部经评审复核为
真**：`agent_max_tokens=2048` 确实是全后端共用一个字段（`config.py:892`）、`stop_reason` 确实被
解析出来又丢掉（`decider.py:360-362`）、`thinking` 确实建好了通道却从未写进请求体、journal 确实
取的是研判前 400 字符（`session.py:1627`）。轴 A/B 的协议文本（S1–S12 / R1–R7）质量很高：S6 要求
「显式点名一条你**不**跟随的信号」、S11 要求先自我验尸再为反方做最强辩护、R7 把「错过一笔证据
充分的交易」与「做错一笔」摆成等价——这些不是把 prompt 写长，而是把**推理的义务**写死，是本方案
最有价值的部分。安全立场同样正确：全部回落语义逐字不变、追问单向增益、`apply_reflection` 一个
字节不碰。

问题集中在**「新逻辑在它自己声称的位置之外」**这一类——五条 P0 里有四条是同一个形状：截断码只在
一半 provider 上生效、token 用量只在一半 provider 上取得到、追问的产出落在一个会覆盖首次研判的
槽位上、反思协议的注入点在架构图上指向一个从不调用它的文件。这四条的共同点是**照着 v1 实现会
得到一个「看起来全绿、实际半瘫」的系统**：离线用例照绿，界面照常渲染，只是它承诺的东西有一半
没有发生。第五条（墙钟）不同，它是**方向正确但没有算完账**：把单次超时抬到 210 秒，却没有乘上
默认 `max_retries=3`，也没有对上默认 2 分钟的唤醒周期。

上述 5 条 P0 与 8 条 P1 **已在 v2 正文逐条修复**，修法与落点写在《评审记录》表的第三列，正文对应
位置均以「【v2 评审修订 · 编号】」标注。5 条 P2 亦已按建议落字。**当前文档中没有未解决的
P0 / P1。**

### 放行条件（实现节点必须逐条满足，缺一即视为未完成）

| # | 条件 | 验收方式 |
|---|---|---|
| C-1 | **三条装配用例先于前端存在**：S-7（实盘）/ S-8（续跑）/ S-9（回测）必须在第 8 步完成并判绿，才允许开始第 9 步前端。判据一律取自假客户端 `stream(system=…)` **实际收到的字符串** | `pytest tests/test_ai_trade_reasoning_assembly.py` 全绿 |
| C-2 | **provider 归一口径有单一真源且被双向验证**：`TRUNCATION_STOP_REASONS` 只存在一份；D-1b（openai `length`）与 B-7（openai 双 `message_done` 回填 usage）必须**在把判据写回 Anthropic 口径时判红** | 故意把判据改回 `== "max_tokens"`，确认 D-1b 判红 |
| C-3 | **`off` 档回退的是行为、不只是 prompt**：G-7b 判绿，且 §7.7 第 3 条的人工验收同时确认超时回到 60 秒、无追问、无墙钟闸 | G-7b + 人工验收记录 |
| C-4 | **追问不覆盖首次研判**：D-5b + F-9 判绿；桌面端肉眼确认一轮真实追问后，界面上**两段研判并列存在**且明确标注哪一段被采纳 | 用例 + 人工验收截图 |
| C-5 | **`.claude-index/config.md` 与代码同提交**：偏离表首行由「13 形参 / 待还」改为「6 形参 / 本轮已还」；新增登记 `decider.py` 越 800 行线。`CLAUDE.md` §二要求阈值变动显式登记，不得静默放宽 | `git show --stat` 含该文件 |
| C-6 | **G-3 的 5× 有离线替身**：`test_deep_profile_prompt_is_at_least_five_times_the_off_profile` 判绿；真实探针 `ratio ≥ 5.0` 并归档 `probe.json`。两者**都要**——探针证明模型真的多想了，离线替身在 CI 里挡住「协议被删剩一句」 | 用例 + `probe.json` |
| C-7 | **全量后端 pytest 绿**，基线 3627 passed + 7 skipped，本特性新增用例后为 3627+N。跑法 `cd services/quant && .venv\Scripts\python -u -m pytest tests -q`（**必须 `-u`**，**不可并发跑全量**——多份用例共用同一个临时 DB） | 完整输出留档 |

### 一条给实现节点的提醒

本轮五条 P0 里有四条，是「一处新逻辑落在了它自己以为的位置之外」。这不是巧合，而是这个仓库当前
阶段的主导缺陷形状——`MEMORY.md` 里同形的记录已经有七八条（「双通道共用纯核心时只有自动侧被
实现」「有写点没读点」「修法落在它以为的位置之外」）。实现时请把**判据取自哪里**当成和实现本身
同等重要的决定：断言 `build_reflection_system_prompt()` 的返回值是零成本的自欺，断言假客户端
**收到**的那一份 `system` 才是真的在验。这条提醒适用于本特性之后的每一轮。

---

**评审人**：Anthropic 工程团队资深评审
**评审对象**：`docs/plans/ai-trade-deep-reasoning-chain/spec.md` v1（1065 行）
**结论**：**有条件通过**（P0×5 / P1×8 已在 v2 正文修复；放行条件 C-1 ~ C-7 见上表）
**文档版本**：v2


---

## 实施过程发现的方案缺陷（Issues Found During Implementation）

按任务约束：凡是发现设计有错或不完整的地方，都在此逐条登记并说明**实际采用的修正做法**，
绝不静默偏离。以下 11 条按「会不会让人照 spec 实现出一个坏东西」排序。

| 编号 | 严重度 | 方案里的说法 | 实际情况 | 采用的修法 |
|---|---|---|---|---|
| **I-1** | 中 | §6.1 第 7 项把「耗时 EWMA / 压力提示 / token 累计 / `aiStats.reasoning`」全部划给 `session.py` | `session.py` 合入前已 2965 行，且 `.claude-index/config.md` 明确登记「下一次触碰应优先按决策编排、状态/事件、维护任务拆文件」。照清单写会让它再涨 ~130 行，而这三件事是**纯计算、零会话依赖** | 新增 `app/ai_auto/reasoning_runtime.py`（143 行，与 `cost_runtime` / `sizing_runtime` / `horizon_runtime` 同族），`session` 只持一个实例并在三处调用。用例见 `tests/test_ai_trade_reasoning_session.py` |
| **I-2** | 中 | §3.6 只说回测取独立档 `standard`，未提追问 | 追问会把决策 LLM 调用数**直接翻倍**——而 §3.6 那道成本护栏的全部理由就是「用户点一次 500 轮回测不该收到一张意外账单」。且它**打翻了 9 条既有回测用例**（假客户端按调用序返回决策，调用数翻倍即序列错位） | `resolve_depth_profile(..., backtest=True)` 额外把 `min_analysis_chars` 归零。理由写在实现注释里：追问的价值是让操作员读到更深的研判，回测没有那个读者、只有那张账单。用例 `test_backtest_lane_never_reasks` |
| **I-3** | **高** | §3.3.1 只说「用 `replace(cfg,…)` 造三份 config 再各调 `make_llm_client_from_config`」 | 照字面实现会把 `make_llm_client_from_config` 的导入从 `manager.py` / `backtest.py` 移走，而**20+ 个测试文件**以 `monkeypatch.setattr("app.ai_auto.manager.make_llm_client_from_config", …)` 注入假客户端——一次全量跑测判红 **150 条**，且症状是「装配点起不来」这种与本特性完全无关的形态。另外那些用例的 provider 是裸 `object()`，`dataclasses.replace` 会当场 `TypeError` | `build_ai_trade_llm_clients(..., make_client=None)` 新增工厂注入参数，两个装配点**传自己模块里的那一份**（保住注入缝）；并加 `is_dataclass(cfg)` 守卫：非 dataclass ⇒ 三者同用原 cfg（此时本就不可能拆预算）。这正是 R-9 那条取舍的同一个道理，只是 spec 只写了 `stream` 签名那一半 |
| **I-4** | 中 | §5.4 的 `.env.example` 注释把 `AI_TRADE_DECISION_TIMEOUT_S` / `AI_TRADE_REFLECTION_TIMEOUT_S` 描述为「显式覆盖入口，留空即用档位值」 | 两者是 `Settings` 的普通字段，**没有「留空」这个状态**（缺省即 60.0/45.0）。要区分「用户显式设过」需要读 `model_fields_set`，而纪律预设那条链会 `model_copy(update=…)` 派生 settings、把字段标记为已设——判据会在装了预设的会话上翻转 | 语义收敛为：**深度档生效 ⇒ 取档位值；未装配 / `off` 档 ⇒ 逐字回退到 settings**（正是 §3.1.1 那条注释的写法）。`.env.example` 的措辞已同步改成事实。G-7b 与用例 `test_off_depth_restores_today_timeouts` 冻结这一点 |
| **I-5** | 低 | §4.1 给出的目标签名是 8 个具名形参，正文却说「形参 13→6」 | 两者自相矛盾；照签名写实际是 13→8，而偏离表要写「6」就会变成在一张以诚实为目的的表里写一个假数字 | 真的收敛到 **6**：`(inst_id, bar, budget_usd, leverage, *, texts, options)`。三个开关收进第二个聚合结构 `SystemPromptOptions`；九个旧关键字走 `**legacy` 垫片且**未知键抛 TypeError**（静默吞掉拼错的关键字 = 宪章章没进 prompt 且不报错）。等价性由 G-1b 的 sha256 断言冻结 |
| **I-6** | 中 | §3.5.2 ② 要求收起态标题「`evidenceDigest` → S12 首句 → `reason`」 | `evidenceDigest` **在 wire 上根本不存在**：§4.2 只把它写进后端的摘录逻辑，前端拿不到它，那条三级回退的第 ① 级恒不可达（典型的「有写点没读点」） | 把它加进 `tool_call` 相位的 `input`（它本就是**模型提交的工具入参**，归属正确），且**仅非空时才写这个键** ⇒ `off` 档 wire 逐字节回退。用例 `AiTradeReasoningBlock.test.tsx` 的收起态标题一条 |
| **I-7** | 低 | §4.2 与 §3.4.4 各写了一遍 `reasoning` 的键名 | 两处各手抄一遍正是本仓库「逐键手抄漂移」的形状 | `ReasoningMeta.wire()` 是**唯一**一份 camelCase 装配，`result` 帧与 `aiStats` 都读它；键集合由 `test_json_wire_of_the_reasoning_meta_is_camel_case` 冻结 |
| **I-8** | 中 | §3.3.4 把 `_TRACE_ROUND_TOTAL_MAX` 写成 `trace.py` 的常量，暗示由 `_TraceCoalescer` 施加 | 合并器是**每条流一个实例**（决策流有 analysis+thinking、追问流有 reask、反思流有 reflection），它看不见「一轮的四个槽位合计」。在那里施加等于没施加 | 闸落在 `session._make_trace_emit` 那个闭包——它是四个槽位**唯一**汇合的地方；纯函数 `trace.append_round_text` 承载判定并**返回被接受的那一段**，session 用它同时决定「进不进内存」与「发不发 WS」（只压服务端等于没压：前端的 trace 对象是同一形状的第二份内存）。用例 S-6b |
| **I-9** | 低 | §3.3.4 的三级摘录回退写在 `session.py` 里 | 那样一来用例只能再抄一份同样的逻辑去断言，而「断言一份自己抄的逻辑」是零成本的自欺（正是评审给实现节点的那条提醒） | 抽成纯核心 `reasoning_protocol.resolve_analysis_excerpt`，`session` 与用例调**同一个函数** |
| **I-10** | 低 | §3.5.3 要求压力提示条用 `--warn` 色系 | 直觉写法 `var(--warn-ink)` 在 `globals.css` 里**不存在**该 token，被 `contrastTokens.source.test.ts` 判红 | 改用真实存在的 `--warn-soft`（底）+ `--warn`（字），并按该文件的既有纪律加一个前缀符号：警告面绝不靠颜色单通道传达 |
| **I-11** | 低 | §3.1.2 定义 `ReasoningStage(id, code, title, mandate)` 四字段，但 §3.1.3 的渲染示意只用到 `id` 与 `code` | `title` 会成为无消费者的死数据（CLAUDE.md §四禁止） | 渲染成 `## S1 REGIME - Higher-timeframe regime`：`title` 有了真实落点，而阶段标记正则只认 `^## S1\b`，三处口径不受影响 |
| **I-12** | **高** | §3.3.3 只说把 `no_tool_call` 细分成 `reasoning_truncated` / `content_filtered` 两个码 | 那个码有**第三个消费者**：`open_funnel._LLM_FAILURE_PREFIXES`（开仓漏斗的「模型调用失败」计数）。只改码不登记，等于同一个物理事件换了个名字就**掉出**计数——漏斗少报，而界面上看不出任何异常。这是本轮我自己差点犯的、与评审那五条 P0 完全同形的错：新码落在了它自己以为的位置之外 | 两个新码一并加进 `_LLM_FAILURE_PREFIXES`；`classify_block` 不动（`no_tool_call` 本就不在前缀表里 ⇒ 两者同归 `"other"`，与合入前一致）。冻结用例 `test_ai_trade_open_funnel.py::test_split_no_tool_call_codes_stay_inside_the_llm_failure_bucket` |

> **I-12 是这批里唯一一条「实现期自查才发现」的。** 找它的方法值得复用：把新码 / 新键当成一次**接口变更**，`grep` 出旧码的**全部**消费者，逐个问「换了名字之后这里还成立吗」。本轮 `no_tool_call` 有三个消费者（重试白名单、漏斗的 LLM 失败集、漏斗的层归类），spec 只写了第一个。

### 一条给下一轮的提醒

本轮最贵的一条是 **I-3**：它不是「设计写错了」，而是「设计只写了取舍的一半」。§3.3.1 正确地
拒绝了改 `LLMClient.stream` 的签名（理由是 32 个假客户端），却没有意识到**同一批用例还依赖
第二条注入缝**——模块级的 `make_llm_client_from_config` 名字。一次全量跑测的 150 条红里，
没有一条是本特性的功能缺陷，全部来自那条被搬走的缝。

判据取自哪里，和实现本身同等重要；**测试注入缝也是接口**，搬动它与改签名是同一类破坏性变更。


---

## 终审发现与修复（Final Review Findings）

终审节点逐项对照 §六 变更清单与 C-1 ~ C-7 复核了全部 diff，共发现并**直接修复**下列 5 条。
每一条都配一条可证伪的回归用例（已实验：去掉修法即判红）。

| 编号 | 严重度 | 缺陷 | 为什么前面的用例没接住 | 修法与回归用例 |
|---|---|---|---|---|
| **V-1** | **P0（界面崩溃）** | `AiTradeReasoningBlock` 把早退 `return null` 写在了 `useMemo` **之前**。降级轮（LLM 超时 / 未产出工具调用）的 `analysis` 恒为空串，轮次从 `deciding` 走到 `done` 时本次渲染比上一次少调一个 Hook，React 抛 `Rendered fewer hooks than expected` —— **整个轨迹面板崩掉** | 既有用例只单独**挂载**一个空轮次（那条路径 Hook 数一致，恒绿）；该缺陷只在**重渲染**时成立 | Hook 全部上提到早退之前；`AiTradeReasoningBlock.test.tsx::survives a degraded round settling from deciding to done with an empty analysis` |
| **V-2** | P1 | 回测车道的 `_build_payload` **从未写** `reasoningDepth`。回测默认 `standard` 档即生效，于是系统 prompt 已是英文脊柱、收尾任务指令却停在中文原句，反思侧的英文收尾指令也永远不会追加 | S-9 只断言了 `system`；而这一半落在**用户消息**上。与 P0-5 同形：单一真源 `decider.depth_name` 只有实盘一个读点 | `backtest._build_payload` 补上加法键（与 `session._build_decision_payload` 逐字对称）；`test_ai_trade_reasoning_assembly.py::test_backtest_lane_user_prompt_carries_the_english_closing_instruction`（判据取自假客户端实际收到的用户文本） |
| **V-3** | P2 | `resolve_depth_profile` 把回测的 `min_analysis_chars=0` 写在 `_apply_overrides` **之前**，一个显式的 `AI_TRADE_REASONING_MIN_CHARS=1500` 会把追问又装回回测车道，使 I-2 那道成本护栏静默失效（决策 LLM 调用数翻倍） | `test_backtest_lane_never_reasks` 只跑默认 settings，覆盖键未设 | 归零改排在覆盖之后；`test_backtest_guard_survives_an_explicit_min_chars_override`（同时断言实盘车道**不受**影响） |
| **V-4** | P2 | `ai_trade_reasoning_reask_max` 的上界是 2，而 `_maybe_reask` 全链只跑**一次**（§3.0 架构图与 §3.4.2 也都写死「至多 1 次」）。填 2 的运维开关会通过校验、然后静默地只生效一半 | 全部用例只用 0 / 1 两个值 | 上界收紧为 `le=1`（**这是对 §5.3 那一行的显式偏离**）；`test_ai_trade_llm_budget.py::test_reask_max_upper_bound_matches_what_the_decider_actually_does` |
| **V-5** | P2（质量门禁） | `tests/test_ai_trade_prompts.py` 把新增导入成块附在**文件末尾**（一排 `# noqa: E402`），其中 `build_reflection_system_prompt` 与文件顶部重复导入 ⇒ `ruff --select F` 判红 F811，且该写法在本仓库无先例 | 交付说明声称 ruff(F+E9) 全绿，实测为 1 error | 导入全部上提并合入顶部已有块，去掉全部 `# noqa: E402`；`ruff check app tests --select F,E9` 重新全绿 |

> **V-1 与 V-2 是同一个形状的两面，也正是评审给实现节点那条提醒的第八个实例：判据取自哪里。**
> V-2 的判据写在 `system` 上，就看不见落在 `messages` 上的那一半；V-1 的判据写在「挂载」上，
> 就看不见只在「重渲染」时才成立的那一半。**下一轮写前端组件用例时，至少要有一条走完整
> 生命周期（`deciding` → `done`，含降级轮）的 `rerender`，而不是只 `render` 终态。**

### 复核后判定为「不改」的三条（记录理由，避免下轮重提）

1. **`.claude-index/config.md` 未随代码入库（C-5）**。偏离表已按 C-5 逐行更新
   （`build_ai_trade_system_prompt` 改为「6 形参 / 本轮已还」，新登记 `decider.py`(1099) /
   `prompts.py`(979) / `config.py`(1126)），但 `.claude-index/` 在本仓库**从未被跟踪**
   （`git log -- .claude-index/` 为空，且不在 `.gitignore` 里）—— 历轮特性的阈值登记全部落在
   这份未跟踪文件上。在终审节点单独把它加进版本库会新开一个与本特性无关的跟踪面
   （而 `index.md` 3602 行仍会留在外面），故**保持仓库现有惯例**：登记已在工作树完成，
   不入本次提交。若要改成跟踪，应单独开一轮把 `.claude-index/` 整体入库并写进索引器的生成约定。
2. **`build_reflection_prompt` 的英文收尾指令是「追加」而非「替换」**（决策侧是替换）。两者不对称
   是刻意的：反思侧那段中文收尾指令里写着安全语义（只能收紧 / abort 须指名实质性缺陷），删它
   就是在一个「安全语义一个字节不改」的轴上改安全语义。中英共存的代价只是多几十个 token。
3. **`session._apply_retry_marker` 不清 `trace["reasoning"]`**。它只在 `result` 相位定型，而一个已到
   `result` 的轮次不会再重试，故后端无需防御性清零（CLAUDE.md §五：不为「理论上不会发生」的分支
   写防御代码）。前端仍清，是因为它还要接住乱序 / 重放帧；`store.ts` 那句「逐字对齐」的注释已改写为实情。
