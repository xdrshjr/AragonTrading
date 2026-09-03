# 会话级情报绑定与渐进式披露（ai-trade-intel-session-binding）

> **版本：v2**（v1 → v2 由本轮设计评审改写；变更点逐条见 §评审记录，结论见文末 §评审结论）
> 目标读者：下游实施工程师。本文档写到「可以逐行落地、不需要再做任何设计决策」的粒度。
> 所有引用的符号与行号都取自本仓库当前工作树（master，HEAD=`4bf512d`），实施前请以实读为准。

---

## 评审记录（Review Notes · v1 → v2）

评审口径：可行性（本仓库当前技术栈真的做得出来吗）· 完整性（边界、错误路径、上线路径有没有漏）·
一致性（与 `CLAUDE.md` 及代码里已成文的约定有没有冲突）· 规模适配（是过度设计还是欠定义）。
下列每一条都经**实读源码**核实，行号取自 HEAD=`4bf512d` 的工作树。

**P0 与 P1 已在正文中逐条改写**（改写位置见每条末尾的「落点」），P2 只登记不改。

### P0（必须改，否则本轮交付的是一个假开关 / 一句假话）

| id | 结论 | 证据与后果 | 落点 |
|---|---|---|---|
| **P0-1** | **三把新总闸在会话侧会有第二个真源。** v1 把 `intel_tier(..., enabled=)` 与 `inject_intel_expand(..., limits=self._intel_limits())` 放在 `AiTradeSession` 里，而 session 手上只有 `executor.settings`（启动期 `_resolve_*` 链的产物）。`expandEnabled` / `repeatCompressEnabled` 一旦按 v1 §3.5 进 `INTEL_SETTING_BRIDGE`，它们的**生效值**就住在 `resolve_intel_settings(db, settings)` 产出的派生副本上（`intel/runtime.py:539`），即 `IntelDeps.settings` / `deps.limits`。 | 用户在设置面板关掉「下单前展开」：运行时那侧关了，会话这侧照旧展开——**请求 200、开关照动、行为不变**，正是本仓库前科五次的「假面板」，且这一次是半真半假，比全假更难发现。 | §3.0 原则 6（新增）、§3.2 B-2 / B-4、§3.3 C-4（新增）、§六 #7 |
| **P0-2** | **横向压缩的前提（「模型上一轮看过」）被上下文生命周期与无状态复核官双双证伪。** ① `decider` 每 `context_refresh_rounds` 轮把 `_context` 重置成摘要种子（`decider.py:1158`），两次摘要之间还有 `_trim_context` 左裁（`decider.py:1214-1222`）；v1 的 `_intel_seen_ids` 只在 `pause`/`resume` 重置。② `build_reflection_prompt` 第一行是 `evidence = build_decision_prompt(payload)`，而复核是**无状态**子调用（明确不拼 `_context`，`prompts.py:1133-1141`）。 | ①「上一轮列出的 N 条（[I01]–[I06]）仍然有效」会指向已经不在上下文里的内容——**一句确定错误的话**写进真实资金决策 prompt，且模型再也没法按编号填 `intelRefs`，引用归因静默塌成空集。② 复核官在 repeat 轮上拿到的情报段就是那一行页脚，指向它从未见过的编号。v1 §3.2 B-0 那格「复核轮恒 `full`」在 §3.0 原则 3 与用例 B9（决策 prompt 逐字节不变）之下**不可实现**——同一个 payload 里只能有一份 `plan`。 | §3.2 B-2（重置点与上界）、B-4（复核段自足）、§3.5 新增设置 #9 |
| **P0-3** | **会话选择为 `off` 时，情报仍从告警唤醒句进入 prompt。** 注入与告警是两条独立通路：`_alert` → `notify_alert` → `manager._on_intel_alert`（`manager.py:1758`）→ `session.note_intel_alert`。而 `wake_text._wake_intel_alert` 在 `inDigest=False` 时**把标题与摘要就地内联**进唤醒句（`wake_text.py:141-148`）；`mode="off"` 恰恰使 `digest_ids` 恒空 ⇒ `inDigest` 恒 `False`。 | 用户勾的是「不让 Agent 参考市场情报」，得到的是「情报只从另一个门进来，而且是最高危的那几条」。v1 §7.3 第 1 条验收只查「外部市场情报」段，**恰好检不出**这件事。 | §3.1 A-6（新增）、§七 A7 / §7.3 第 1 条 |
| **P0-4** | **`GroupIdentity` 加了第 5 个字段，但 `identity_of` 不在变更清单里 ⇒ R3 的缓解自己失效。** `rearm_group` 走 `ident = identity_of(runtime.deps)`（`runtime.py:737`），而 `identity_of`（`runtime.py:567-570`）是**逐字段手抄**的四行构造。新字段带默认值 ⇒ 重装后 `session_rule` 静默回落 `IntelSessionRule()`。 | 回落方向是**最坏的那一个**：`off` → `enabled=True, mode="full"`。用户在设置里保存一次无关的情报参数，启动时选的「不采信」就被翻成「全文采信」，且不报错——正是 v1 自己在 R3 里写下要防的那个形状。`_build_deps` 同样必须把 `ident.session_rule` 传进 `IntelDeps`。 | §3.1 A-2、§六 #7、§七 A4 |
| **P0-5** | **`intel` 回执没有落到前端唯一的分组级读点。** v1 §4.2 往 `group_status()` 加 `intel` 键，§六前端清单只改 `lib/aiTradeTypes.ts`。但分组行 → 前端状态的映射是 `lib/useAiTradeGroupStream.ts::groupStateFromStatus`，一个**逐键手抄**的函数——该文件里已经并排写着**五条**同款警告注释（`acceptance` / `discipline` / `direction` / `state` / `feeRates`）。 | 不补 `intel: row.intel ?? null` 与 `AiTradeGroupState.intel`，§3.1 A-4 承诺的 `legacy_fallback` 回执在「刷新页面后重发现」这条路径上恒缺席，而 WS 通路是好的 ⇒ 本仓库最难发现的那一类坑，已复发五次。 | §六 #21b（新增）、§七 W11 |

### P1（必须改，否则会在实施期或上线后变成返工）

| id | 结论 | 落点 |
|---|---|---|
| **P1-1** | **L3 置顶只在 `_after_round` 一处传 `pin_item_id`，任何一次别处的 `refresh` 都会静默抹掉它。** `refresh` 的其它调用点：`invalidate`（`runtime.py:516`，REST 每一次写都调）、停调度收尾（`runtime.py:686`）、`scheduler._loop`（`scheduler.py:120`）。用户在告警到达与决策轮之间点一次「标记已读」，pin 就没了；而 `_alert` 早已把 `in_digest=True` 戳进台账与唤醒句 ⇒ prompt 说「已在下方情报段中列出」而它并不在。v1 §3.2 B-3 自己写着「置顶持续到下一次情报轮」——那就必须有一个能持续的**状态**，而不是一个只在一次调用里活着的形参。 | §3.2 B-3 |
| **P1-2** | **索引档 + 告警置顶 = 最高危的那一轮拿到最少的正文。** `mode="index"` 下被 pin 的条目按 `render_index_line` 渲染（无摘要），而 `inDigest=True` 让 `_wake_intel_alert` 走「该情报已在下方情报段中列出」分支、**不再内联摘要**。净效果：开了索引档之后，高危告警轮里模型能读到的正文比不开还少。 | §3.2 B-3 |
| **P1-3** | **`binding.digestItemIds` 没有唯一取数点，且「按渲染序」在一条回退路径上不可满足。** 运行时手上的是 `frozenset`（`_digest_ids`，`runtime.py:refresh`；`build_snapshot` 返回的 `digestItemIds` 同为 frozenset）——**无序**；有序的那份只在新键 `wire["itemIds"]` 里，而 v1 §5.3 规定 `mode=="full"` 且横向总闸关时该键缺席。 | §3.3 C-1、§5.3、§七 G-3 |
| **P1-4** | **`_intel_seen_ids` 的置位时机与集合口径都欠定义。** v1 写「置为本轮 digest 的**全部** itemIds」——`hidden` 掉的那些从来没渲染过，把它们记成「见过」会让它们在下一轮也被折叠，永远进不了 prompt。且 payload 建好之后那一轮未必真的送达模型。 | §3.2 B-2 |
| **P1-5** | **`mode` 的值域在三处不一致。** `INTEL_MODES = ("off","index","full")`（§3.1 A-1） vs 请求体 `Literal["index","full"]`（§4.1） vs `enabled=False ⇒ effective_mode=="off"`。而 A-1 的兜底写「不在 `INTEL_MODES` 里 ⇒ 收口成 `index`」——`"off"` **在**表里，于是一份被手改过的 `start_wire`（`{"enabled":true,"mode":"off"}`）会得到「`inject_on` 为真、`intel_mode` 为 `off`」，`build_snapshot` 拿一个没人定义过的档去选 renderer。 | §3.1 A-1、§4.1 |
| **P1-6** | **`inject_on` 与 `binding.injecting` 对 `armed` 的口径相反。** §3.1 A-3 说「`armed` 天然生效，不必在 `inject_on` 里再判」，§3.3 C-1 又把 `injecting` 定义成含 `armed` 的四项合取 ⇒「唯一判据」有两份。 | §3.1 A-3、§3.3 C-1 |
| **P1-7** | **`_check_intel_disclosure` 与 §3.0 原则 5 自相矛盾，且判据不完备。** 原则 5 写「本轮不新增任何会导致 4xx/5xx 的判据」，§3.5 又新增一个**拒绝进程启动**的校验器；且 `repeat_max_chars > index_max_chars` 只覆盖索引档，`mode="full"` 时的对照物是 `prompt_max_chars`。 | §3.0 原则 5、§3.5 |
| **P1-8** | **`.claude-index/config.md` 的阈值偏离登记没写。** `CLAUDE.md` 二明确「调整必须在 `.claude-index/config.md` 中显式记录」。§六行数复核表把 `session.py`（实读 3959 行）继续留在越线态并只承诺「本轮不再加长文本」——这是一次**已知且被接受**的偏离，必须留痕（上一轮 `SD-R-P1` 已因同一条被点名）。 | §六 #32（新增） |
| **P1-9** | **repeat 档的页脚会把两个数混成一个。** 既有 `format_intel` 页脚是「另有 N 条未展示」，N 取 `wire["hidden"]`（**预算装不下的**）；repeat 档另有 `same`（**见过所以折叠的**）。两个语义完全不同的数并列而不分别命名，模型与用户都会读成一个。 | §3.2 B-2 |
| **P1-10** | **复核轮展开段绕过了提示注入防护。** `_BANNER`（不可信包裹）只在 `format_intel` 里渲染；`format_expand` 是**第二段**外部素材（摘要 + 已核实引文）进入复核官上下文，v1 没要求它带同一层包裹——与 `intel/disclosure.py` 模块 docstring 第 2 条硬纪律直接冲突。 | §3.2 B-4 |
| **P1-11** | **启动弹窗那句「已采集到 27 条有效情报」没有取数通路。** 启动**前**这些标的多半没有 runtime（`_BY_INST` 查不到），条数只能走 `GET /intel/{instId}` 的 DB 路径且**逐标的一次请求**；v1 既没指定端点，也没定义「有效」的口径与失败态。 | §3.4 |
| **P1-12** | **冷续跑路径上 `intel_rule` 的落点没写全。** v1 只写「`manager.py:284` 新字段」。`resume.py` 重跑 `build_start_request` 拿到 `req.intel_rule` 之后，必须由 `resume_group` 同样落到 `group.intel_rule`；用例 A5 也只覆盖了 `start`。 | §3.1 A-2、§七 A5 |

### P2（登记，不在本轮改）

| id | 结论 |
|---|---|
| P2-1 | `aiStats.intelDisclosure.seenReset` 没有任何消费者（§3.5 只把它写进字典、前端那一行不读它）——「有写点没读点」。留着可以，但要么在 `AiTradeStatsPanel` 那一行的 `title` 里露出，要么在实施期删掉，不要让它长成第三个僵尸字段。 |
| P2-2 | §3.4 卡片文案里的「上下文开销约 2.5 倍」是 `1200/480` 的硬编码推导。两个数已按 W3 改成接口回执，比值也应现算（`Math.round(promptMaxChars / indexMaxChars * 10) / 10`），否则运维改一次旋钮它就成了假话。 |
| P2-3 | §九 实施顺序把 `#14 api/ai_intel.py` 排在第 4 步，而 F17 指出该文件有在制修改。建议把它单拎成最后一步，减少行级精确暂存的窗口。 |
| P2-4 | `AiTradeIntelIn` 未声明 `extra`；`{"intel": {}}` 与 `{"intel": null}` 在语义上都落到「关闭」，行为正确但没写明，实施时容易被当成两种态。 |
| P2-5 | `intel_alert` 轮在既有 `_FULL_TIER_KINDS`（`tactical_context.py:43-45`）里**已经**是 `full`，只有 `_EXECUTION_FULL_KINDS` 不含它。§3.2 B-2 的 `intel_tier` docstring 对 F9 的转述准确，但容易被读成「仓库到处都把 intel_alert 当 repeat」；实施时按行号复核一次即可。 |

---

## 一、概述（Overview）

左侧「自选 / 自动交易」面板里那颗雷达按钮背后，本仓库已经有一整套**标的市场情报层**
（`services/quant/app/ai_auto/intel/`，30 个模块）：它为每一个正在 AI 自动交易的标的做网络检索、
把网页素材摘要成短条目、过六道真实性闸落库，再由整理轮与核查轮维护层级与可信度。这条流水线的
装配点是 `manager._wire_intel`（`manager.py:1648`），注册表按 `group_id` 与 `inst_id` 双向索引
（`intel/runtime.py:393-421`），决策热路径由 `session._build_decision_payload` 同步取一次进程内
快照（`session.py:3382`：`intel_digest_for(self.inst_id)`），最终由
`prompts._format_intel`（`prompts.py:991`）渲染成决策 prompt 里的「外部市场情报」段。

**所以「情报」与「AI 自动交易」在数据通路上早已是关联的——真正缺的是三件事**：

1. **这份关联既不可选、也不可见。** 是否把情报喂给 Agent，今天由**进程级**开关
   `ai_trade_intel_inject`（`config.py:1011`，默认 `True`）单方面决定，`AiTradeGroupStartIn`
   （`schemas.py:948`）上**没有任何情报字段**，启动弹窗上没有任何一处提到情报。用户既不能在启动
   一次真实资金会话时选择「这一次不要情报」，也无法从界面上确认「我现在看到的这些情报，到底有没有
   进入 Agent 的上下文」。`AiTradeIntelSummary`（`apps/web/lib/intelTypes.ts:338`）有
   `armed` / `intelEnabled` / `providerConfigured` 三个装配事实，唯独没有「本会话是否采信」。
2. **披露是一档到底的。** `disclosure.build_digest_and_ids` 把全部 active 条目按
   `(层级, -score)` 排序后，用 `prompt_max_chars=1200` 的**单一预算**一次性铺开完整行
   （标题 + 摘要 + 元信息），**每一轮决策都重铺一次**。在 15m 执行周期 + 2 分钟墙钟唤醒的默认配置
   下，同一根 K 线内会有约 7 次决策轮，每一轮都把**同一份 1200 字**的情报原样再喂一遍——这正是需求
   里点名的「上下文爆炸」。同时，模型想细看某一条的时候没有任何办法要到更多，想忽略全部的时候也
   省不下一个字。
3. **最需要细看的那一轮反而看不见。** `wake_text._wake_intel_alert`（`wake_text.py:122`）已经诚实地
   处理了「告警条目没能挤进情报段」这一态——但它只是把假话改成真话，并没有让那条**触发了本轮的**
   高危情报真的进入情报段。

本轮要交付的，就是把这三件事一次性做对：**在启动 AI 自动交易时给出一个默认关闭的「情报研判」
开关（轴 A）；把一档到底的注入改造成纵横两个维度的渐进式披露阶梯（轴 B）；把「这份情报此刻正在
被哪一个会话、以什么档位采信」做成界面上看得见、接口上读得到的一等事实（轴 C）；并为整条通路补上
上下文预算的观测与护栏（轴 D）**。四条轴各自带独立总闸，任一总闸置回退档，对应链路**逐字节**回到
本特性合入前的行为。

本轮**不改**情报的采集 / 整理 / 核查 / 落库 / 引用归因的任何一行判定，**零 DB 迁移**，**不进回测
链路**（`ai_auto/backtest.py` 与 `backtest_manager.py` 全文 0 次 `intel`，情报层从来就只在实盘车道
装配；本轮维持这一事实，并把它写成一条门禁）。

---

## 二、现状基线（实读事实，实施前请逐条复核）

| # | 事实 | 位置 |
|---|---|---|
| F1 | 情报运行时按分组装配，`_RUNTIMES[group_id]` + `_BY_INST[inst_id]` 双索引；`unregister` 按身份比对再删 | `intel/runtime.py:41,393-411` |
| F2 | 决策 payload 的 `intel` 键由 `intel_digest_for(inst_id)` 同步取进程内缓存，键缺席 ⇒ 整段省略 | `session.py:3379-3384`、`intel/runtime.py:425` |
| F3 | 注入总闸 `inject_on` 的**唯一**读点在 `build_snapshot`；关掉时 digest 与 id 集合双双为空 | `intel/service.py:640-652` |
| F4 | `build_digest_and_ids` 是渲染的**唯一定稿点**（wire 里存的是成品字符串，`format_intel` 只做拼接） | `intel/disclosure.py:165-240` |
| F5 | 预算 `prompt_max_chars` 默认 1200，装不下就 `break`，剩下的如实报成 `hidden` | `config.py:1049`、`intel/disclosure.py:126-150` |
| F6 | 反思（下单前复核）**只在 `open_long` / `open_short` / `close` 上运行**，`hold` / `cancel_order` 直通 | `session.py:2235-2237` |
| F7 | 反思 prompt = `build_decision_prompt(payload)` + 追加段；`experienceCounter` 是「只给复核官看的额外证据」的既有先例 | `prompts.py:1132-1145`、`experience/runtime.py:515-532` |
| F8 | 仓库已有渐进式披露约定：`payload["contextDisclosure"] ∈ {"full","repeat"}`，由 `execution_disclosure(wake_kind=…)` 判定，多个段据此渲染压缩形态 | `tactical_context.py:172-180`、`prompts.py:724-752`、`trend_text.py:110` |
| F9 | `_EXECUTION_FULL_KINDS` **只含** `candle_close` / `bar_advanced`——`intel_alert` 与 `signal_flip` 在这套口径下是 `repeat` | `tactical_context.py:47` |
| F10 | 会话私有 `Settings` 派生副本由 `build_start_request` 的 `_resolve_*` 链逐级产生；`manager._group_settings` 取 `members[0].session.executor.settings` | `api/ai_auto.py:467-560`、`manager.py:2052-2057` |
| F11 | `build_group_runtime` 内部会**再叠加一次库内行**（`resolve_intel_settings`），且 `INTEL_SETTING_BRIDGE` 里就有 `ai_trade_intel_inject` | `intel/runtime.py:539-541`、`intel/settings_bridge.py:24` |
| F12 | 冷续跑用 `AiTradeGroupStartIn(**row["startWire"])` 重跑 `build_start_request`；`start_wire = body.model_dump(mode="json")` | `ai_auto/resume.py:95-101`、`api/ai_auto.py:559` |
| F13 | `summary_for` 在**出口**统一打 arm 四键的戳；REST 侧 `_summary` 用 `_RUNTIME_ONLY_KEYS` 做「库存查库 + 瞬态取缓存」的合并 | `intel/runtime.py:146-158`、`api/ai_intel.py:107-146` |
| F14 | `_after_round` 先 `refresh` 再 `_alert`；`outcome.alert` 在 `refresh` **之前**就已经在手 | `intel/runtime.py:345-348` |
| F15 | `AiTradeLaunchModal.tsx` 实读 1907 行，早已越过 CLAUDE.md 的 800 行红线；新字段一律走独立 `*Fields.tsx` 子组件 | `apps/web/components/Agent/` |
| F16 | `.env.example` 在**仓库根**，`Settings ⇄ .env.example` 双向对齐由 `G-Env` 型用例冻结 | `.env.example`、`tests/test_ai_trade_silent_degradation_core.py:478-493` |
| F17 | 工作树里 `services/quant/app/api/ai_intel.py` 有一处在制修改（204 端点 `-> Response`），与本轮无关，实施时**不要**把它卷进本轮提交 | `git status` |

---

## 三、技术设计（Technical Design）

### §3.0 六条不可违反的原则（第 6 条由评审 P0-1 新增）

1. **默认关闭是硬约束。** `intel` 键缺省 ⇒ **不注入**。这与本仓库既有的
   `baseRules` / `profitGuard` / `tradingHorizon` / `experience` / `direction` / `trend` 六个字段
   「字段缺省 ⇒ 开启」的惯例**方向相反**，是需求的明文要求。实施时必须在
   `_resolve_intel` 的 docstring 里逐字写下这一条，并由用例 `G-A1` 反向冻结（照抄既有惯例即判红）。
2. **同一列只有一个人写。** 新增的 `binding` 块与 `disclosure` 块各自只有一个写点
   （`intel/binding.py::binding_wire` 与 `intel/disclosure.py::plan_disclosure`），
   任何第二处拼装都会重演本仓库「WS 一份、REST 一份、两边都不报错」的成文缺陷。
3. **渲染结果住在 wire 里，绝不在 `format_intel` 内现场重算。** 这是 `disclosure.py` 模块 docstring
   里已经成文的纪律：`commit_round` 会把 `build_decision_prompt(payload)` **再渲染一次**塞进
   `_context`，两次渲染之间任何状态漂移都会让上下文里那一份与模型真正看到的那一份不一致。
   本轮新增的「本轮展示哪几条」同样必须**在 payload 装配那一刻定稿**（`payload["intel"]["plan"]`），
   `format_intel` 只做「按 plan 挑选已定稿的行」，**不做任何判定**。
4. **四把总闸，逐字节回退。** 见 §3.5 的设置表。任一总闸置回退档 ⇒ 对应链路的产物与
   `4bf512d` **逐字节相同**（由 `sha256` 冻结用例断言，照抄 `G-1` / `G-6` 的既有写法）。
5. **加法流水线，永不阻挡真实资金。** 情报的任何失败（未装配 / 未配源 / 快照失败 / 规则解析异常）
   一律 fail-open 成「本会话不注入情报」，**绝不**让 `/ai-trade/start` 失败，也**绝不**改变任何
   风控闸的判定。
   > **[P1-7] 措辞收窄**：本条约束的是**请求期**——本轮不新增任何会导致 `/ai-trade/*` 4xx/5xx
   > 的判据（`AiTradeIntelIn` 的 `Literal` 值域校验是 pydantic 入口层的既有机制，不算新增判据）。
   > §3.5 的 `_check_intel_disclosure` 是**进程启动期**校验器，与本条不冲突：它拦的是一个
   > 自相矛盾的运维配置，且在任何请求到达之前就已经拦掉了。两者的极性刻意相反，照抄既有
   > `_check_intel_actionability`（`config.py:1509`）的定位。
   >
   > fail-open 的**唯一**落点是 `api/ai_auto._resolve_intel`：它自身 `try/except` 一次并回退成
   > `IntelSessionRule(enabled=False)`（**回退方向是关闭，不是开启**），异常记 `logger.warning`
   > 并在 `group_status().intel.source` 上回执 `"resolve_failed"`。**不在 `ai_trade_start`
   > 端点外层再包一层 `except`**（CLAUDE.md 五：捕获异常时尽量缩小作用域）。

6. **情报口径的唯一读点是运行时的派生副本（★ 评审 P0-1）。** 一切 `ai_trade_intel_*` 的**生效值**
   都住在 `resolve_intel_settings(db, settings)` 产出的那一份派生副本上（`IntelDeps.settings` /
   `deps.limits`，装配点 `intel/runtime.py:539-541`）——因为设置面板写的是 `app_settings` 库内行，
   而库内行只在那一跳被叠加。会话手上的 `executor.settings` 走的是 `build_start_request` 的
   `_resolve_*` 链，**不含**情报库内行。

   故 `AiTradeSession` 里**一行都不许**出现 `getattr(self.executor.settings, "ai_trade_intel_*")`
   或 `limits_from_settings(self.executor.settings)`。本轮为此新增**唯一**取数点
   （`intel/runtime.py`，实现下沉到 `intel/binding.py`）：

   ```python
   def intel_limits_for(inst_id: str) -> IntelLimits | None:
       """会话侧读情报口径的**唯一**入口：取该标的所属运行时的派生 `deps.limits`。

       未装配 / 回测车道 / `_BY_INST` 查不到 ⇒ ``None`` ⇒ 会话侧整条情报链缺席
       （横向不压缩、L2 不装配、观测整键缺席）⇒ 逐字节回退。
       """
   ```

   违反本条的后果是「半真半假的假面板」：面板上关掉「下单前展开」，运行时那侧关了、会话这侧
   照旧展开，请求 200、开关照动、行为不变。由用例 `G-7`（源码正则门禁）与 `D5` 共同冻结。

### §3.1 轴 A：会话级情报开关（启动可选，默认关闭）

#### A-1 规则对象与解析链

新增纯核心 `services/quant/app/ai_auto/intel/session_rule.py`：

```python
# ★ 评审 P1-5：**两张表，不是一张**。混成一张的后果是一份被手改过的 `start_wire`
# （`{"enabled": true, "mode": "off"}`）会解析成「`inject_on` 为真、`intel_mode` 为 "off"」，
# 于是 `build_snapshot` 拿着一个没有任何 renderer 认识的档去选渲染器——而这不会报错。
REQUESTABLE_MODES: tuple[str, ...] = ("index", "full")   # 请求体与冷续跑兜底的**唯一**值域
INTEL_MODES: tuple[str, ...] = ("off", *REQUESTABLE_MODES)  # 派生态的全值域（`off` 只由 enabled=False 生）

@dataclass(frozen=True)
class IntelSessionRule:
    """一次 AI 自动交易会话对情报层的采信约定（**与 Settings 无关，重装时原样带过去**）。

    默认值 `enabled=True, mode="full"` 是**给既有构造点用的**：`IntelDeps` 上带默认值的字段
    让 30+ 个直接构造 deps 的离线用例一个字节不改，且行为逐字回到 4bf512d。生产路径**永远**
    显式传一个由 `resolve_session_rule` 解析出来的规则，绝不吃这个默认值。
    """
    enabled: bool = True
    mode: str = "full"

    def __post_init__(self) -> None:
        # ★ 评审 P1-5：`mode` 只能取 `REQUESTABLE_MODES`。`"off"` 是一个**派生态**，
        # 由 `enabled=False` 唯一产生；把它也塞进 `mode` 会造出「开着但档位是关」这种
        # 没有任何 renderer 认识、且不报错的第三态。frozen dataclass ⇒ 用 object.__setattr__。
        if self.mode not in REQUESTABLE_MODES:
            raise ValueError(f"IntelSessionRule.mode 必须 ∈ {REQUESTABLE_MODES}，收到 {self.mode!r}")

    @property
    def effective_mode(self) -> str:
        return self.mode if self.enabled else "off"


def resolve_session_rule(raw: dict | None, settings, *, legacy: bool = False) -> IntelSessionRule:
    """`AiTradeGroupStartIn.intel` → `IntelSessionRule`（**唯一**解析点）。

    - 总闸 `ai_trade_intel_session_opt_in_enabled=False` ⇒ 恒返回 `IntelSessionRule()`
      （= 逐字节回退：由进程级 `ai_trade_intel_inject` 独裁）。
    - `legacy=True`（本次启动请求来自**本特性合入前**落库的 `start_wire`）⇒ 同上回退。
    - `raw is None`（新客户端没勾 / 老客户端不认识这个字段）⇒ `IntelSessionRule(enabled=False)`。
      **这一条是需求的明文要求，与仓库既有六个字段的惯例方向相反。**
    - `raw["mode"]` 不在 **`REQUESTABLE_MODES`**（★ 评审 P1-5：不是 `INTEL_MODES`）里 ⇒ 收口成
      `"index"`，**不抛**（入口 `Literal` 已经拦过一次，这里是给冷续跑读回来的坏值兜底，与
      `settings_bridge` 的三处同形收口一致）。`"off"` 因此**永远不会**成为一个 `mode` 值：
      用户表达「不要情报」的唯一途径是 `enabled=False`。
    - 本函数**自身不抛任何异常**（值域已收口、`raw` 非 dict 时按 `None` 处理）。上层
      `_resolve_intel` 仍然按 §3.0 原则 5 包一次 `try/except` 并回退成 `enabled=False`，
      理由是「解析链的上游可能被将来的改动引入新的失败模式」，而回退方向必须是**关闭**。
    """
```

`legacy` 的判据**只有一个**：`"intel" not in body.model_fields_set`。pydantic v2 的
`model_fields_set` 能区分「键缺席」与「显式传了 null」，而 `resume.py` 用
`AiTradeGroupStartIn(**start_wire)` 重建请求体（F12），本特性合入后每一次新启动的 `start_wire`
都**必然**含 `"intel"` 键（`model_dump` 会写出 `null`），故这个谓词精确地只对
「本特性合入前落库、如今才冷续跑」的会话为真。

> **为什么要为冷续跑开这个后门**：生产里可能存在数十条已暂停、且正持有真实仓位的会话。它们的
> `start_wire` 里没有 `intel` 键；若按「缺省即关闭」处理，一次「继续」会**静默**改变这些会话
> Agent 的证据面。而它们的用户当初看到的行为是「情报是开的」。这不是安全性问题（少一段证据只会
> 更保守），但它是一次用户没有授权的语义变更，本仓库对此的成文纪律是**必须显式回执**——见 A-4。

#### A-2 装配链落点（三跳，一处都不能少）

```
AiTradeGroupStartIn.intel (wire)
  └─ api/ai_auto.build_start_request  →  _resolve_intel(body, settings)  →  IntelSessionRule
       └─ AiTradeGroupStartRequest.intel_rule            (manager.py:171 dataclass 新字段)
            └─ AiTradeManager.start_group  → AiTradeGroup.intel_rule     (manager.py:284 新字段)
            └─ AiTradeManager.resume_group → AiTradeGroup.intel_rule     (★ P1-12：同一行，别漏)
                 └─ manager._wire_intel → build_group_runtime(session_rule=group.intel_rule)
                      └─ GroupIdentity.session_rule  (第 5 字段)
                           ├─ _build_deps(ident=…) → IntelDeps.session_rule   (★ P0-4)
                           └─ identity_of(deps) → GroupIdentity.session_rule  (★ P0-4，重装通路)
                                └─ IntelDeps.inject_on  (唯一判据)
                                     └─ service.build_snapshot / disclosure.build_digest_and_ids
```

**★ 评审 P0-4：重装通路上有两处逐字段手抄，一处都不能漏。**
`rearm_group` 拿身份的方式是 `ident = identity_of(runtime.deps)`（`runtime.py:737`），而
`identity_of`（`runtime.py:567-570`）是一段**四行手抄**的构造；`_build_deps` 里
`IntelDeps(...)` 同样逐字段拼。`GroupIdentity.session_rule` 若按 dataclass 惯例带默认值
`IntelSessionRule()`，漏抄的表现是**静默的、而且方向最坏**：

```python
def identity_of(deps: IntelDeps) -> GroupIdentity:
    return GroupIdentity(sessionmaker=deps.sessionmaker, group_id=deps.group_id,
                         bar=deps.bar, change_24h_pct=deps.change_24h_pct,
                         session_rule=deps.session_rule)   # ★ P0-4：漏这一行 = off 翻成 full
```

用户在设置中心保存一次**与情报无关**的参数 → `rearm_intel` → 重装 → 启动时选的「不采信」被
翻成 `IntelSessionRule()` = `enabled=True, mode="full"`，且不报任何错。这正是 v1 自己在 R3 里
写下要防的那个形状，只是缓解措施漏了它真正的落点。

故用例 `A4` 的**跑法**同样被钉死：**必须经 `rearm_group` 走一遍完整重装**，而不是直接构造一个
`GroupIdentity` 再断言字段还在——后者对这个缺陷恒绿。

**关键陷阱（必须按此实施，否则本轮的开关是一个假开关）**：
会话级选择**绝不能**通过「在 `Settings` 副本上 `setattr`/`model_copy` 掉 `ai_trade_intel_inject`」
来表达。`build_group_runtime` 的第一件事就是 `resolve_intel_settings(db, settings)`
（F11），而 `INTEL_SETTING_BRIDGE` 里恰好有 `("injectEnabled","ai_intel.inject","ai_trade_intel_inject",bool)`
——只要用户在设置中心动过一次那个开关，库内行就会**静默覆盖**会话的选择，而且不报错。
故会话规则必须作为一个**独立的、显式的 keyword 形参**一路传到 `_build_deps`，在
`resolve_intel_settings` **之后**才与它合取。

`IntelDeps` 新增两处（`intel/service.py:171`）：

```python
    session_rule: IntelSessionRule = IntelSessionRule()   # 带默认值 ⇒ 既有构造点零改动

    @property
    def inject_on(self) -> bool:
        """情报是否进入本会话的决策上下文（**全仓唯一判据**）。

        `build_snapshot` 里那一行 `getattr(deps.settings, "ai_trade_intel_inject", True)`
        由本属性取代——两处各判一次必然分叉，而分叉不报错。
        """
        if not bool(getattr(self.settings, "ai_trade_intel_inject", True)):
            return False                       # 运维总闸优先级最高
        return self.session_rule.enabled

    @property
    def intel_mode(self) -> str:
        return self.session_rule.effective_mode if self.inject_on else "off"
```

`GroupIdentity`（`intel/runtime.py:558`）新增第 5 个字段 `session_rule: IntelSessionRule`。
理由与该 dataclass 的 docstring 逐字吻合：「**与设置无关、重装时必须原样带过去**」。
不加这一条，`PUT /settings/intel` 触发的 `rearm_group` 会用一个默认规则重建 deps，
把用户在启动弹窗上的选择悄悄推翻——这正是评审 P0-1 当年修过的那个形状。

#### A-3 与既有三态的合取顺序（唯一真源）

最终是否注入 = `armed`（装配态，`ArmState.armed`）
∧ `ai_trade_intel_inject`（运维总闸）
∧ `session_rule.enabled`（会话选择）。

`armed=False` 时 `IntelRuntime` 是一个壳，`refresh` 早退、`_digest` 恒空，故第一项天然生效，
不需要在 `inject_on` 里再判一次（判两次就是第二个真源）。

**★ 评审 P1-6：`inject_on` 与 `binding.injecting` 是两个不同的谓词，不许互相冒充。**
v1 在这里说「`armed` 不进 `inject_on`」，又在 §3.3 C-1 把 `injecting` 定义成含 `armed` 的四项
合取——两句话并排放着，实施时必然有人把其中一个当成「唯一判据」去复用。本轮把两者的关系
钉成一条**可断言的不变式**：

| 谓词 | 定义 | 住在哪 | 用途 |
|---|---|---|---|
| `IntelDeps.inject_on` | `ops_gate ∧ session_rule.enabled` | `intel/service.py` | **只**给 `build_snapshot` 决定「这一轮要不要建 digest」 |
| `IntelBinding.injecting` | `runtime.arm.armed ∧ deps.inject_on ∧ bool(digest_ids)` | `intel/binding.py` | **只**给界面与回执回答「此刻真的在喂吗」 |

不变式（用例 `C3` 断言）：`injecting is True ⟹ inject_on is True ∧ armed is True`；
反向**不成立**——壳运行时上 `inject_on` 完全可能为 `True` 而 `injecting` 为 `False`
（`reason == "not_armed"`）。`inject_on` 的 docstring 里必须逐字写下「**本属性不含 `armed`**」。

#### A-4 回执与可执行披露

`group_status()` 的分组行新增 `intel: {mode, enabled, source}` 镜像（`source ∈
{"request","legacy_fallback","ops_off"}`）。`legacy_fallback` 是 A-1 那个后门的**显式回执**：
前端在「继续」对话框与分组卡上各渲染一行「本会话启动于情报开关上线之前，已按运维默认（开启 /
关闭）继续采信情报」。**只披露，不改任何行为**——这是本仓库处理「服务端替我决定了什么」的成文
纪律（`discipline_inert` 是同形先例）。

#### A-5 冷续跑的第二个落点（★ 评审 P1-12）

`resume.py` 的 S2 用 `AiTradeGroupStartIn(**row["startWire"])` 重跑 `build_start_request`，于是
`req.intel_rule` **自动**同配置——但那只完成了一半。`AiTradeGroup` 是由 `start_group` 与
`resume_group` **各自**构造的（`manager.py`），`intel_rule` 必须在**两处**都落到 `group` 上，
否则续跑出来的分组带着一个 dataclass 默认值走进 `_wire_intel`，症状与 P0-4 逐字同形。

实施时的机械判据：`grep -n "intel_rule" app/ai_auto/manager.py` 必须出现 **4 次**——
`AiTradeGroupStartRequest` 的字段声明、`AiTradeGroup` 的字段声明、`start_group` 的赋值、
`resume_group` 的赋值。少一次就是漏了一条通路。用例 `A5` 因此**必须跑一次真实的
「暂停 → 继续」**，不能只断言解析函数的返回值。

#### A-6 会话选择必须同时关掉告警唤醒通路（★ 评审 P0-3）

**这是 v1 最严重的一处缺口**：注入与告警在本仓库里是**两条独立通路**。

```
情报轮 ──▶ _after_round ──▶ refresh ──▶ digest（注入通路，被 inject_on 管住）
              └────────▶ _alert ──▶ notify_alert ──▶ manager._on_intel_alert (manager.py:1758)
                                        └────────▶ session.note_intel_alert ──▶ _maybe_intel_wake
                                                       └──▶ wake["intel"] ──▶ wake_text._wake_intel_alert
```

而 `_wake_intel_alert`（`wake_text.py:141-148`）在 `inDigest=False` 时会**把标题与摘要就地内联**
进唤醒句——那是 actionability 轮为「情报没能挤进情报段」这一态刻意加的诚实回落。可
`mode="off"` 恰恰使 `digest_ids` 恒空 ⇒ `inDigest` **恒** `False` ⇒ **每一条高危情报的标题与
摘要都会写进决策 prompt 的首行**。

而那条回落分支的措辞本身就是这件事的铁证——它逐字写着「（情报段已达长度上限或**注入已关闭**），
本段就是你能看到的全部内容」：代码早已预见「注入关掉」这一态，处置却是**照样把正文交出去**
（对运维总闸而言那是刻意的诚实回落，对一个用户级的「我不要情报」而言就是一次静默违约）。

用户勾的是「不让 Agent 参考市场情报」，实际得到的是「情报只从另一个门进来，而且专挑最高危的
那几条」。更糟的是 v1 §7.3 第 1 条验收只查「外部市场情报」段——**恰好检不出**这件事。

**修法（唯一落点，一处判定）**：闸放在 `manager._on_intel_alert` —— 它是这条链上**唯一**同时
看得见 `group`（因而看得见 `group.intel_rule`）与 `session` 的那一跳；放进 `session` 要给会话
再塞一份规则副本（第二个真源），放进 `IntelRuntime._alert` 则会连带砍掉台账落库与 WS 告警列表
（那两样与「要不要喂给模型」无关，用户仍然应该在界面上看到告警）。

```python
    def _on_intel_alert(self, group_id: str, inst_id: str, handoff) -> None:
        group = self._group
        if group is None or group.group_id != group_id:
            return
        # ★ 评审 P0-3：本会话不采信情报 ⇒ 告警**不进决策通路**。台账与 WS 列表不受影响
        # （`_alert` 已经在上游把它们写完了）——用户照常在界面上看得见这条告警，只是它
        # 不会唤醒一次决策、更不会把正文内联进 prompt 首行。
        # 总闸 `ai_trade_intel_session_opt_in_enabled=False` ⇒ `intel_rule` 恒为默认规则
        # （enabled=True）⇒ 本判据恒不成立 ⇒ 逐字节回退。
        if not group.intel_rule.enabled:
            self._intel_alert_muted += 1     # 观测：进 aiStats.intelDisclosure.alertMuted
            return
        member = next((m for m in group.members if m.inst_id == inst_id), None)
        if member is None:
            return
        member.session.note_intel_alert(handoff)
```

对应的验收补强见 §7.3 第 1 条与用例 `A7`。

### §3.2 轴 B：渐进式披露阶梯（纵向三级 × 横向两档）

#### B-0 全景

|  | 决策轮（每轮） | 复核轮（仅 open/close） | 告警轮 |
|---|---|---|---|
| `mode="off"` | 无 `intel` 键，逐字节回退 | 同左 | 唤醒句仍两态诚实（既有行为） |
| `mode="index"`（推荐默认） | **L1 索引**：每条一行、**不含摘要正文**，预算 `index_max_chars=480` | **L2 展开**：把决策实际引用（`intelRefs`）的 ≤3 条渲染成全文（标题 + 摘要 + ≤1 条已核实引文），预算 900 | **L3 置顶**：触发本轮的那条被钉进情报段首位 |
| `mode="full"` | 今天的行为（标题 + 摘要 + 元信息，预算 1200），逐字节等于 `4bf512d` | `full` 轮无 L2（已经全文）；**`repeat` 轮必须有 L2**（见下方 ★） | 同上 L3 |
| 横向 | `repeat` 轮只渲染「自上一轮以来新增 / 变更」的条目 + 一行「其余 N 条与上一轮相同」，预算 `repeat_max_chars=240` | **复核轮吃的是同一份 `plan`**（见下方 ★，v1 那句「复核轮恒 full」不可实现） | 告警轮恒 `full`（见 B-3） |

> **★ 评审 P0-2（b）：「复核轮恒 `full`」是一句不可实现的话，必须换一种修法。**
> `build_reflection_prompt` 的第一行是 `evidence = build_decision_prompt(payload)`
> （`prompts.py:1141`），用的是**同一个 payload**；而 §3.0 原则 3 要求 `plan` 在装配期定稿一次、
> 渲染层不做任何判定，用例 `B9` 又要求「注入 L2 之后决策 prompt 逐字节不变」。三条约束联立
> ⇒ 同一个 payload 里只能有一份 `plan` ⇒ 复核官看到的情报段**必然**与交易员看到的那一份相同。
>
> 而复核是**无状态**子调用（`decider.reflect` 明确不拼 `_context`），于是在 `repeat` 轮上，
> 那位守着真实资金的复核官拿到的情报段就是一行「上一轮列出的 6 条仍然有效」——**指向它从未
> 见过的编号**。本仓库对这个形状已有成文答案：`experience/disclosure.py::format_counter` 的
> docstring 逐字写着「**必须自带足够的自解释**：当轮档位可能是 `repeat`，复核官看到的 L0 只是
> 一行压缩形态，任何「见上文第 N 条」的引用都会指向已被压缩掉的内容」。
>
> **故修法是让 L2 自足，而不是让复核轮重铺**：`plan.tier == "repeat"` 时 `intelExpand`
> **必须**把被折叠掉的条目一并带上（见 B-4 的 `plan_expand` 新签名），且此时 L2 **不再**以
> `mode == "index"` 为前提——`mode="full"` 的 `repeat` 轮同样需要它。

三条设计立论：

- **纵向**把「有哪些情报、各是什么性质」（便宜、每轮都值）与「这一条到底说了什么」（贵、只在真的
  要据此下单时才值）分开。L1 行里保留 `层级｜类别·立场·影响｜N 源｜置信 x.xx｜相对时间`，
  这些**本身就是结构化研判**，摘要正文（≤160 字）在多数轮里只是它的自然语言复述。
- **横向**利用仓库既有的 `contextDisclosure` 约定（F8）：同一根 K 线内的第 2..N 次唤醒，情报库
  没有任何变化，重铺一遍纯属浪费。15m + 2 分钟唤醒的默认配置下这能砍掉约 6/7 的情报段体量。
- **L2 落在复核官身上**是刻意的：`_reflect_before_execute` 只在真的要下单时才跑（F6），而复核官
  的裁决空间只有 `confirm` / `abort` / 缩仓，**方向单侧**——多给它一份完整证据，最坏后果恒为
  「少做一笔」，永远不会是「多做、做反、做大」。这与 `experienceCounter` 的既有立论逐字同源（F7）。

#### B-1 L1 索引层

`intel/disclosure.py` 新增：

```python
def render_index_line(item: IntelItem, *, now: float, ref: str = "") -> str:
    """索引形态的单行（**不含 `—— summary`**，其余与 `render_line` 逐字节一致）。"""
    tier = tax.TIER_LABELS.get(item.tier, item.tier)
    category = tax.CATEGORY_LABELS.get(item.category, item.category)
    stance = tax.STANCE_LABELS.get(item.stance, item.stance)
    impact = tax.IMPACT_LABELS.get(item.impact, item.impact)
    meta = f"（{relative_time(item.event_ts, now)}｜{item.domain_count} 源｜置信 {item.confidence:.2f}）"
    body = f"· {tier}｜{category}·{stance}·{impact}：{item.headline}{meta}"
    return f"[{ref}] {body}" if ref else body
```

`build_digest_and_ids` 新增 keyword-only 形参 `mode: str = "full"`（**默认 `"full"` ⇒ 既有 4 个
调用点逐字节回退**）与 `pin_ids: frozenset[int] = frozenset()`。函数体三处分叉：

1. 预算：`budget = limits.index_max_chars if mode == "index" else limits.prompt_max_chars`；
2. 渲染器：**逐条判定**的闭包 `_render`（★ 评审 P1-2：被 `pin_ids` 命中的条目在索引档下
   仍用 `render_line` 全文渲染，其余用 `render_index_line`；判定逐字见 B-3）。
   `_fit_lines` 的**试算**与第二遍**定稿**必须传**同一个** `_render`，否则预算算的是 A、
   渲染的是 B，而 `hidden` 会当场说谎；
3. 排序：`pin_ids` 里的条目提到 `live` 列表最前（在 `_TIER_RANK` 排序**之后**、
   `_reorder_for_conflicts` **之前**，稳定移动）。

wire 增两键：

- `"mode": "index"` —— **条件键**，仅 `mode != "full"` 时出现（缺席即回退）。
- `"itemIds": [int, ...]` —— 与 `lines` **严格同序同长**，供 B-2 的横向计划按行挑选，
  并且是 `binding.digestItemIds` 的**唯一有序取数点**。
  > **★ 评审 P1-3 改写**：v1 让它在「`mode == "full"` 且横向总闸关」时整键缺席，好让 G-3 的
  > `sha256` 继续成立；但那一格恰恰是回退档，而 §3.3 C-3 的 `IntelCard` 竖线与 `[Ixx]` chip
  > 在回退档下同样要读它。现改为 **digest 非空即出**：它是一个**纯读侧键**，`format_intel`
  > 不读它 ⇒ 不改变 prompt 的任何一个字节；G-3 的冻结口径同步改成「`format_intel(wire)` 的
  > 输出 + `wire["refs"]`」，被冻结的从此是**喂给模型的那段文本与编号表**本身。

段末页脚随 `mode` 分叉一句：索引档追加「（以上只列标题与性质；下单前的复核环节会自动展开你实际
引用的那几条的完整摘要。）」——这句话**必须为真**，故它只在 `expand_enabled` 且 `mode=="index"`
时渲染。

#### B-2 横向：`repeat` 轮的增量披露

纯核心（`intel/disclosure.py`）：

```python
def intel_tier(*, wake_kind: str, context_disclosure: str | None, enabled: bool) -> str:
    """情报段的横向档：`"full"` | `"repeat"`。

    **不直接复用 `payload["contextDisclosure"]`**（评审必查项）：`_EXECUTION_FULL_KINDS` 只含
    `candle_close` / `bar_advanced`（F9），于是 `intel_alert` 轮在那套口径下是 `repeat`——而
    「这一轮之所以存在就是因为来了一条高危情报」的那一轮，恰恰是最不该压缩情报段的一轮。
    """
    if not enabled or wake_kind == "intel_alert":
        return "full"
    return "repeat" if context_disclosure == "repeat" else "full"


@dataclass(frozen=True)
class DisclosurePlan:
    tier: str                 # full | repeat
    show: tuple[int, ...]     # 本轮要渲染的行下标（**下标，不是 item_id**：行才是定稿产物）
    same: int                 # 与上一轮相同、本轮不重复列出的条数
    refs_kept: tuple[str, ...]  # 被折叠掉的那些行的编号（页脚如实列出，使 intelRefs 仍可引用）

def plan_disclosure(wire: dict, *, tier: str, seen_ids: frozenset[int],
                    limits: IntelLimits) -> DisclosurePlan:
    """在**payload 装配那一刻**定稿「本轮展示哪几行」。纯函数、零 IO。"""
```

判定：`tier == "full"` ⇒ 全展示（`same=0`）。`tier == "repeat"` ⇒ 只展示
`itemIds[i] not in seen_ids` 的行，且累计字符受 `repeat_max_chars` 约束（同样**绝不半截截断
一条**）；全部都见过 ⇒ `show=()`，此时整段压成**一行**页脚（措辞见下方 P1-9 的两数分列）。

**`enabled` 与 `limits` 的取数（★ 评审 P0-1）**：`intel_tier(..., enabled=…)` 的 `enabled` 与
`plan_disclosure(..., limits=…)` 的 `limits` **一律**来自 `intel_limits_for(self.inst_id)`
（§3.0 原则 6 的唯一取数点），**绝不**读 `self.executor.settings`。`intel_limits_for` 回
`None` ⇒ 横向压缩整段不执行（`tier` 恒 `"full"`、`plan` 键缺席）⇒ 逐字节回退。

会话侧状态：`AiTradeSession._intel_seen_ids: frozenset[int]`。

**★ 评审 P1-4：置位时机与集合口径。**
- **集合口径 = 本轮 `wire["itemIds"]`**，也就是**真正被 `_fit_lines` 收进 `lines` 的那些**——
  `hidden` 掉的条目从来没渲染过，把它们记成「见过」会让它们在随后每一轮都被当作旧条目折叠，
  **永远进不了 prompt**，而且不报错。（`itemIds` 与 `lines` 同序同长，天然满足这条。）
- **置位时机 = `commit_round` 之后**，不是 payload 装配那一刻。`_build_decision_payload` 只
  **读**旧值（`plan_disclosure(seen_ids=self._intel_seen_ids)`）并把本轮的 id 元组暂存到
  `self._intel_round_ids`；真正的赋值放在 `_round` 里那次 `await self.decider.commit_round(...)`
  返回之后（`session.py:1965` 一带）。理由：payload 建好之后那一轮仍可能因异常 / 降级而**从未
  送达模型**，先置位就是把「模型没看过」记成「看过」。

`payload["intel"]["plan"]` 存 `{"tier","show","same","hidden","refsKept"}`，`format_intel(wire)`
从 wire 内部读它。这样 `commit_round` 的第二次渲染与模型看到的那一次**必然逐字节相同**（原则 3）。

**★ 评审 P0-2（a）：重置点不止两个——上下文本身会被重置。**
`decider` 每 `context_refresh_rounds` 轮把 `_context` 整体换成一段摘要种子（`decider.py:1158`），
两次摘要之间还有 `_trim_context` 的左裁兜底（`decider.py:1214-1222`）。一旦发生，「上一轮列出的
那 6 条」**在模型的上下文里已经不存在了**，而页脚还在按编号引用它们——这是一句确定错误的话，
并且会让模型再也没法按编号填 `intelRefs`，引用归因静默塌成空集（`_intel_cite` 的分母不变、
分子归零，事后对账会把它误读成「情报没用」）。

故 `_intel_seen_ids` 的重置点是**四个**：

| # | 触发 | 判据 | 理由 |
|---|---|---|---|
| 1 | `pause()` | 直接清空 | 跨越一次暂停，重新铺一遍是对的 |
| 2 | `resume()` | 直接清空 | 同上 |
| 3 | **上下文摘要重置** | `commit_round(...)` 的返回值**不为 `None`**（它返回的正是新摘要文本，`session.py:1965` 已经拿在手里） | 上一轮的情报段刚刚被摘要吃掉，引用它就是引用不存在的内容 |
| 4 | **连续 repeat 上界** | `self._intel_repeat_streak >= ai_trade_intel_repeat_max_rounds`（新设置 #9，默认 `6`） | `_trim_context` 的左裁是**静默**的、会话拿不到回执；一个有界的重铺是唯一守得住的兜底。命中即本轮出 `full` 并把 streak 清零 |

**不在 `stop` 里重置**（会话都没了）。第 3、4 条各配一条用例（`B13` / `B14`）。

**★ 评审 P1-9：`hidden` 与 `same` 是两个语义完全不同的数，页脚必须分别命名。**
既有 `format_intel` 的页脚只有一句「另有 N 条未展示」，N 取 `wire["hidden"]`（= **预算装不下**
的）。repeat 档新增的 `same` 是「**见过所以折叠**」的，两者并列而不分名，模型与用户都会读成
一个数。故 repeat 档页脚固定为两句、缺项即整句省略：

```
（另有 3 条因篇幅未展示。另有 5 条与上一轮相同、未重复列出（[I02]–[I06]），它们仍然有效。
  情报可能过时或片面；与风控铁律冲突时以铁律为准。）
```

`show=()` 的极端态同样保留 `hidden` 那一句——「一条都没新增」与「新增了但装不下」必须可分。

#### B-3 L3：告警条目置顶

`IntelRuntime._after_round`（`intel/runtime.py:345`）改两行：

```python
    async def _after_round(self, inst_id: str, outcome: RoundOutcome) -> None:
        handoff = getattr(outcome, "alert", None)
        # ★ 轴 B-3：把「触发了本轮的那条情报」钉进即将定稿的 digest。判据在 refresh **之前**
        # 就已经在手（`outcome.alert`），故不需要调换 refresh / _alert 的顺序——那个顺序是
        # actionability 评审 P0-3 用血换来的，不许动。
        pin = self._pin_item_id(handoff)
        await self.refresh(inst_id, degrade_code=outcome.degrade_code,
                           reason=outcome.error_text, rejected=outcome.rejected,
                           pin_item_id=pin)
        self._alert(inst_id, handoff)
```

`refresh(..., pin_item_id: int = 0)` 透传到 `build_snapshot(deps, inst_id, arm=…, pin_item_id=…)`
再到 `build_digest_and_ids(..., pin_ids=…)`。`_pin_item_id` 在总闸
`ai_trade_intel_alert_pin_enabled=False` 或 `handoff is None` 时返回 `0` ⇒ 全链路逐字节回退。

**★ 评审 P1-1：置顶必须是运行时上的一个「状态」，不能只是一次调用的形参。**
v1 说「置顶持续到下一次情报轮」，但只在 `_after_round` 这一处传 `pin_item_id`。而 `refresh`
另有三个调用点，每一个都会用**默认 `pin_item_id=0`** 重建 digest、把置顶抹掉：

| 调用点 | 位置 | 何时发生 |
|---|---|---|
| `invalidate(inst_id)` | `runtime.py:516` | REST 的**每一次写**（新增 / 编辑 / 移层 / 置顶 / 静音 / 删除 / **标记已读**） |
| 停调度收尾 | `runtime.py:686` | 关掉总闸 / 重装落地 |
| `scheduler._loop` | `scheduler.py:120` | 到期轮次 |

后果是一句确定错误的话：用户在告警到达与决策轮之间在弹窗里点一次「标记已读」，pin 没了；而
`_alert` 早已把 `in_digest=True` 戳进台账与 `wake["intel"]`，于是唤醒句照旧说「该情报已在下方
情报段中列出」，而它并不在。

故 pin 落在 `IntelRuntime` 上：

```python
        # ★ 评审 P1-1：置顶是**状态**，不是形参——`refresh` 有四个调用点，只在其中一个传参
        # 等于「任何一次标记已读都会把置顶抹掉」，而唤醒句还在说它在。
        # 值域 `inst_id -> (item_id, expire_wall)`；窗口复用 `alert_pending_ttl_s`（它就是
        # 这条告警本身的唤醒窗口，不新造第二个时间常数）。
        self._pinned: dict[str, tuple[int, float]] = {}
```

- 写点**唯一**：`_after_round` 在调 `refresh` **之前** `self._set_pin(inst_id, pin)`；
  `pin == 0` ⇒ `self._pinned.pop(inst_id, None)`（一轮没有告警就该解除上一轮的置顶）。
- 读点**唯一**：`refresh` 内部 `pin_ids = self._live_pin(inst_id)`（过期即自动为空），
  `refresh` 因此**不需要**新增 `pin_item_id` 形参——四个调用点一个字节不改，`build_snapshot`
  只多一个 keyword-only 的 `pin_item_id: int = 0`（默认 0 ⇒ 既有离线调用点逐字节回退）。
- 总闸 `ai_trade_intel_alert_pin_enabled=False` ⇒ `_set_pin` 恒 no-op ⇒ `_pinned` 恒空 ⇒
  排序逐字节回退。

**★ 评审 P1-2：索引档下被置顶的那一条必须用全文行渲染。**
`mode="index"` 下 `render_index_line` **不含摘要正文**；而置顶成功 ⇒ `in_digest=True` ⇒
`wake_text._wake_intel_alert` 走「该情报已在下方情报段中列出」分支、**不再内联摘要**
（`wake_text.py:141-148`）。两件事叠起来，净效果是**开了索引档之后，高危告警轮里模型能读到的
正文比不开还少**——本轮引入的压缩恰好压在最不该压的那一条上。

故 `build_digest_and_ids` 的渲染器选择改为**逐条**判定，而不是整段一刀切：

```python
    #   索引档下，被 pin 的条目**恒用全文行**（评审 P1-2）：它是这一轮存在的唯一理由。
    #   预算与试算必须用同一个判定（`_fit_lines` 与定稿遍共用下面这个闭包），否则预算算的是
    #   索引行、渲染出来的是全文行，`hidden` 会当场说谎。
    def _render(item, *, now, ref):
        full = (mode != "index") or (int(item.item_id or 0) in pin_ids)
        return (render_line if full else render_index_line)(item, now=now, ref=ref)
```

被 pin 的那一条因此可能单条就占掉 `index_max_chars` 的一大半——这是**想要的**：它排第一，
预算循环先给它，后面装不下的如实记进 `hidden`。用例 `B11` / `B15` 分别钉死「置顶条含摘要」与
「置顶条挤掉后续条目时 `hidden` 计数正确」。

**已知副作用（必须写进 spec 与代码注释）**：置顶会改变 `refs` 的编号顺序，并且在
`alert_pending_ttl_s` 窗口内**持续到下一次情报轮**——告警条目会在随后的若干决策轮里继续排在
首位。这是有意的（一条刚到的高危情报值得在它的时效窗口里一直排在最前），但必须被用例钉死，
而不是碰巧成立。

置顶成功后 `_alert` 算出的 `in_digest` 恒为 `True`，`wake_text._wake_intel_alert` 自然走
「该情报已在下方情报段中列出」分支——**`wake_text.py` 与 `wake_reason.py` 一行不改**。
若预算连一行都装不下（`index_max_chars` 被运维调到极小），`in_digest` 仍会是 `False`，既有的
内联降级句照常兜底。

#### B-4 L2：复核轮的按需展开

新增 `intel/expand.py`（纯核心，零 IO）：

```python
@dataclass(frozen=True)
class ExpandItem:
    ref: str          # "I03"
    headline: str
    summary: str
    quote: str        # ≤1 条已核实引文；无引文 ⇒ ""
    meta: str         # 「3 天前｜3 源｜置信 0.82」
    origin: str       # "cited" | "folded"（★ P0-2b：为什么这一条出现在展开段里）

def plan_expand(wire: dict, refs: Sequence[str], *, plan: dict,
                limits: IntelLimits) -> tuple[ExpandItem, ...]:
    """挑出要在复核轮展开的条目。**两个来源，按优先级排队**（★ 评审 P0-2b）：

    1. `origin="cited"`：决策实际给出的 `intelRefs`（决策官说它据此下单，复核官必须看到全文）；
    2. `origin="folded"`：`plan["refsKept"]` —— 本轮被横向压缩折叠掉、复核官**从未见过**的那些
       （复核是无状态子调用，`plan.tier == "repeat"` 时它的情报段只剩一行页脚）。

    两组去重后按上述顺序截断（≤ `expand_max_items`，总长 ≤ `expand_max_chars`，**绝不半截截断
    一条**）。`plan.tier != "repeat"` ⇒ 第 2 组为空 ⇒ 与 v1 的行为逐字相同。
    """

def format_expand(items: Sequence[ExpandItem]) -> str:
    """复核官视角的展开段；空序列 ⇒ **空串** ⇒ 反思 prompt 逐字节回退。

    ★ 评审 P1-10：段首**必须**复用 `disclosure._BANNER`（不可信包裹）。它与语料块的
    `<<<UNTRUSTED_SEARCH_RESULTS>>>` 是同一层防护的两端，而本段是**第二段**进入模型上下文的
    外部素材（摘要 + 已核实引文）——`format_intel` 里那一句包不住它。`intel/disclosure.py`
    模块 docstring 第 2 条硬纪律逐字适用；用例 `B16` 断言展开段首字符串与 `_BANNER` 相同。
    """
```

展开所需的 `summary` / `quote` **必须来自 digest wire**，不得回查 DB（决策热路径查库是本仓库明令
禁止的）。故 `build_digest_and_ids` **额外**写一个
`"bodies": {"I01": {"summary": "...", "quote": "...", "meta": "..."}}` 子字典——它是定稿产物的一
部分，与 `lines` 同一次调用产生，天然满足原则 3。

**★ 评审 P0-2b：`bodies` 的出键条件不是 `mode == "index"`。** v1 写「`mode == "full"` 时整键
缺席（正文已经在行里了）」——但在 `mode="full"` 的 **`repeat`** 轮上，正文**并不在行里**（那些
行被折叠掉了），而复核官恰恰需要它们。故出键条件改为：

```
bodies 出现  ⟺  expand_enabled ∧ digest 非空 ∧ (mode == "index" ∨ repeat_compress_enabled)
```

`mode == "full"` 且横向总闸关（= G-3 的回退档）时整键仍然缺席，`sha256` 冻结继续成立。
`bodies` 是**纯读侧键**：它不进 `format_intel`，故它的出现与否**不改变决策 prompt 的任何一个
字节**（用例 `B9` 的另一半）。

装配点（`session.py:2241` 之后一行，逐行镜像 `self._experience.inject_reflection`）：

```python
        self._experience.inject_reflection(payload, decision)
        # 轴 B-4：只给下单前复核官看的情报展开。展开总闸关 / `intel_limits_for` 回 None /
        # 两个来源都为空（既没引用、本轮也没折叠掉任何一条）⇒ **整键缺席** ⇒ 反思 prompt
        # 逐字节回退。
        # ★ 评审 P0-1：`limits` 只能来自 `intel_limits_for(self.inst_id)`（运行时的派生副本），
        # 绝不是 `limits_from_settings(self.executor.settings)` —— 后者读不到情报库内行，
        # `expandEnabled` 会变成半个假开关。
        # ★ 评审 P0-2b：**不再以 `mode == "index"` 为前提**。`mode="full"` 的 repeat 轮上，
        # 复核官的情报段同样只剩一行页脚，被折叠掉的那些必须在这里补给它。
        inject_intel_expand(payload, decision, limits=intel_limits_for(self.inst_id))
```

渲染点（`prompts.build_reflection_prompt`，`prompts.py:1141` 附近）：与既有
`counter_block` 完全同形地插一个 `expand_block`，位置在 `counter_block` **之后**、
`_format_reflection_decision` **之前**。

⚠️ `build_reflection_prompt` 的第一行是 `evidence = build_decision_prompt(payload)`，而
`payload` 此刻已经被 `inject_intel_expand` 就地改过；`build_decision_prompt` 只读
`payload["intel"]`，**不读** `payload["intelExpand"]`，故决策段不受影响。这条连锁必须由用例
钉死（market-intel 那轮吃过一次亏：改决策 prompt 会同时改反思 prompt）。

`submit_trade_reflection` 的工具 schema **一个字段都不加**——复核官不需要写编号。

### §3.3 轴 C：绑定的可见性（关联做成一等事实）

#### C-1 wire 形状

`AiTradeIntelSummary` 新增**一个**顶层键 `binding`（永不缺席、形状恒定）：

```jsonc
"binding": {
  "bound": true,                 // 这个标的此刻属于某个在跑 / 已暂停的 AI 自动交易分组
  "groupId": "aig-20260902-01",  // 未绑定 ⇒ null
  "mode": "index",               // off | index | full（会话启动时选的档）
  "injecting": true,             // 最终生效：armed ∧ 运维总闸 ∧ 会话选择 ∧ 有条目
  "reason": "",                  // injecting=false 时的原因码，见下
  "digestItemIds": [1201, 1198], // 本情报轮定稿进情报段的 item_id（按渲染序）
  "lastRound": {                 // 最近一次决策轮真正渲染出来的读数；从未渲染 ⇒ null
    "round": 42, "ts": 1788000000, "tier": "repeat",
    "shown": 1, "same": 5, "hidden": 3, "chars": 118
  }
}
```

**★ 评审 P1-6：`injecting` 的定义（唯一写法，与 §3.1 A-3 的不变式表对齐）**

```python
injecting = bool(runtime.arm.armed) and bool(deps.inject_on) and bool(digest_ids)
```

**★ 评审 P1-3：`digestItemIds` 的取数点与序。**
v1 说它是「按渲染序」，但运行时手上那份是 `frozenset`（`IntelRuntime._digest_ids`；
`build_snapshot` 返回的 `digestItemIds` 同为 frozenset，`service.py` 的 return 处）——**无序**。
有序的那一份只在新键 `wire["itemIds"]` 里，而 v1 §5.3 又规定 `mode=="full"` 且横向总闸关时该键
缺席。于是在回退档上，`binding.digestItemIds` 要么无序、要么根本取不到，而 §3.3 C-3 的
`IntelCard` 竖线与 `[Ixx]` chip 直接读它。

**修法（两处一起改，缺一不可）**：

1. `wire["itemIds"]` 的出键条件改为「**digest 非空即出**」，不再与 `mode` / 横向总闸挂钩。
   它是一个**纯读侧键**：`format_intel` 不读它，故它的出现**不改变 prompt 的任何一个字节**。
2. 对应地，`G-3` 的冻结口径从「整份 wire 的 `sha256`」改成「**`format_intel(wire)` 的输出 +
   `wire["refs"]` 的 `sha256`**」——被冻结的应当是**喂给模型的那段文本与编号表**，而不是一个
   顺手多带了读侧键的内部字典。（`G-1` 冻结的本来就是 prompt 文本，两条口径因此同源。）

于是 `binding.digestItemIds = list(wire.get("itemIds") or ())`，**唯一取数点、恒有序、
digest 为空即空列表**。

`reason` 值域（新增 `taxonomy.BINDING_REASONS`，与 `ARM_REASONS` 同款单一真源）：
`not_bound`（这个标的没有在跑的 AI 自动交易）、`session_off`（用户启动时没开）、
`not_armed`（情报流水线未装配，细分原因看既有 `armReason`）、`ops_inject_off`
（运维把 `ai_trade_intel_inject` 关了）、`no_items`（开着，但库里还没有可注入的条目）。

#### C-2 唯一写点与三条读路径

写点：`intel/binding.py::binding_wire(binding: IntelBinding) -> dict`，
由 `IntelRuntime.summary_for` 在**出口**打戳（`intel/runtime.py:146`，与 `arm_wire` 逐行并排）。

三条读路径因此天然同形：

| 路径 | 取数 | binding 来源 |
|---|---|---|
| WS `intel` 事件 | `runtime.refresh` → `_notify(inst, self.summary_for(inst))` | 出口打戳 |
| WS `snapshot.intel` | `session.snapshot()` → `intel_summary_for(inst)`（`session.py:3735`） | 同一个 `summary_for` |
| REST `GET /intel/{instId}` | `api/ai_intel._summary`：库存查库 + 瞬态取缓存 | 把 `"binding"` **同时**加进 `_RUNTIME_ONLY_KEYS` 与 `_RUNTIME_ONLY_KEYS_UNARMED`（`api/ai_intel.py:104-106`） |

`read_summary`（DB 路径）也必须写一份 `binding = binding_wire(IntelBinding.unbound())`——
**形状恒定优先于省一个键**：缓存缺席时 REST 会直接返回 `fresh`，少一个键会让前端读到
`undefined` 并把「没绑定」画成「一切正常」（E-18 的成文教训，历轮四次）。
`read_summary` 的**签名一个字节不改**（它已有 5 个形参，正好卡在 CLAUDE.md 的上限上）。

`lastRound` 的回填走一个同步 setter（逐行照抄 `runtime.note_alert_outcome` / `invalidate` 的
既有范式）：

```python
def note_disclosure(inst_id: str, wire: dict) -> None:
    """会话把「上一轮情报段真正渲染成什么样」回填给运行时。**同步、不查库、不排任务。**"""
```

调用点：`session._build_decision_payload` 的最后（`payload["intel"]` 落定之后）一行。
未装配 / 回测车道 ⇒ `_BY_INST` 查不到 ⇒ no-op。

#### C-4 会话侧读情报口径的唯一取数点（★ 评审 P0-1）

与 `note_disclosure` 并排的**读**方向 setter，实现住在 `intel/binding.py`、`runtime.py` 只留
薄转发（§六的行数下沉方案）：

```python
def intel_limits_for(inst_id: str) -> IntelLimits | None:
    """会话侧读一切 `ai_trade_intel_*` 生效值的**唯一**入口（§3.0 原则 6）。

    返回该标的所属运行时的 `deps.limits`——那是 `resolve_intel_settings(db, settings)` 叠加过
    库内行的**派生副本**，也是 `build_snapshot` 自己读的那一份。会话手上的
    `executor.settings` 走的是另一条派生链（`build_start_request`），**不含**情报库内行：
    两处各读一份的后果是「面板上关了、会话这侧照旧开着」，请求 200、开关照动、行为不变。

    未装配 / 回测车道 / `_BY_INST` 查不到 ⇒ ``None`` ⇒ 会话侧整条情报链缺席（横向不压缩、
    L2 不装配、`aiStats.intelDisclosure` 整键缺席）⇒ 逐字节回退。
    """
    runtime = _BY_INST.get(inst_id)
    return runtime.deps.limits if runtime is not None else None
```

配套**源码正则门禁** `G-7`（放在后端用例里，照抄既有源码扫描门禁的写法；CRLF 环境下正则须写
`\r?\n`）：`app/ai_auto/session.py` 全文不得出现 `ai_trade_intel_`（会话侧一个情报设置键都不许
直读），也不得出现 `limits_from_settings`。

#### C-3 界面（顶级设计 · 渐进式披露的人机交互对偶）

界面上的渐进式披露与 prompt 里的渐进式披露是同一条原则的两端：**一眼可辨的状态 → 悬停可得的
解释 → 点开可查的明细**。

1. **`AiTradeIntelButton`（左侧面板，行内 hover 按钮）**
   - 图标在 `binding.injecting === true` 时，右下角叠一枚 6px 的实心「链接」小点
     （`--accent`）；`bound && !injecting` 时叠一枚**空心**小点（`--muted`）；未绑定时无叠加。
     三态互斥，靠形状而非仅靠颜色区分（色觉无障碍）。
   - `title` / `aria-label` 由 `lib/intel.ts` 新增的 `intelBindingText(summary)` 统一生成，
     文案取自后端 `reason` 码 → 前端文案表（**只翻译码，不重新判定**）。
   - 既有的未读徽标、降级染色、键盘可达三条规则**一条不改**。
2. **`IntelModal` 顶部新增一条 `IntelBindingStrip`（新组件）**
   - 已采信：「本次 AI 自动交易正在采信这些情报 · **索引档** · 上一轮进入上下文 1 条（另有 5 条与
     上一轮相同、未重复列出）」，右侧一颗 ghost 按钮「什么是索引档？」展开两行说明（`<details>`，
     默认收起——这本身就是渐进式披露）。
   - 未采信：「本次 AI 自动交易**未采信**情报（启动时未开启）。情报仍在正常采集，下次启动时可在
     弹窗里勾选「让 Agent 参考市场情报」。」——**不给一个此刻点不动的开关**：会话中途改变证据面会
     让同一次会话的前后两半不可比，本仓库对「运行中不可变契约」的既有处置就是如此。
   - 未装配 / 运维关：沿用既有 `degrade` 卡片的入口，不新增第二条路径。
3. **`IntelCard`（条目卡）**：`binding.digestItemIds` 命中的条目左缘加一条 2px `--accent` 竖线 +
   一枚 `[I03]` 编号 chip（编号取自既有 `digest.refs`，不新造标识）。悬停提示：
   「本情报轮已定稿进入情报段；重复唤醒轮可能不重复列出」——**措辞必须精确**，
   `digestItemIds` 是「定稿进段」，不等于「上一轮渲染出来了」，两者的差额正是横向压缩。
4. **启动弹窗新卡片 `AiTradeIntelFields`**（见 §3.4）。

### §3.4 启动弹窗（HCI）

新增受控组件 `apps/web/components/Agent/AiTradeIntelFields.tsx`（**独立文件**：
`AiTradeLaunchModal.tsx` 实读 1907 行，F15），逐行镜像 `AiTradeExperienceFields.tsx` 的结构：

```
┌─ 市场情报研判 ───────────────────────────────── 查看情报库 → ┐
│ ☐ 让 Agent 参考市场情报（默认关闭）                          │
│   已为本次的 3 个标的采集到 27 条有效情报（最近 12 分钟前）。 │
│   ── 勾选后展开 ──                                           │
│   披露档  ( ) 索引档（推荐，省上下文）  ( ) 全文档            │
│   索引档：每轮只给 Agent 情报的标题与性质（约 480 字）；下单前 │
│   的复核环节自动展开它真正引用的那几条的完整摘要。            │
│   全文档：每轮给出完整摘要（约 1200 字），上下文开销约 2.5 倍。│
│   同一根 K 线内的重复唤醒只列新增情报。                       │
└──────────────────────────────────────────────────────────────┘
```

**★ 评审 P1-11：「已采集到 N 条」这一行的取数口径、失败态与并发上限（v1 完全没写）。**
启动**前**这些标的多半还没有 runtime（`_BY_INST` 查不到），条数只能走 DB 路径：

- **端点**：复用既有 `GET /api/intel/{instId}`，**逐标的一次**，`Promise.allSettled` 并发，
  **不新增接口、不新增批量端点**（一次启动最多十个标的，十次并发的只读请求远低于本仓库
  既有的任何一处扇出）。
- **口径**：`N = Σ (counts.ultra + counts.mid + counts.long)`，**不含 `archived`**。文案逐字
  写成「已为本次的 3 个标的采集到 **27 条在库情报**（最近 12 分钟前）」——「在库」而不是
  「有效」：`counts` 是 `status="active"` 的计数，它**不等于**「会被注入的条数」（注入还要过
  `refuted` 过滤与预算）。写「有效」就是在启动弹窗上说一个模型其实拿不到的数。
- **时间**：括号里的相对时间取 `max(lastCollectTs)`；全部为 `null` ⇒ 整个括号省略并把主句换成
  「这些标的**尚未成功采集过**情报，本次可能一条都注入不了」。
- **失败态**：任何一个请求失败 / 超时 ⇒ **整句不渲染**（勾选框与档位单选**照常可用**）。
  绝不先显示一个会被当真的 0——那是本仓库成文的 E-18 形状。

- **未配置检索源 / 运维总闸关**：整卡 `aria-disabled`，勾选框禁用，正文换成一句事实 + 一个
  「去设置」入口。判据取 `GET /settings/intel` 的既有回执，**不新增接口**。
- **回测 tab 恒不渲染这张卡**（情报层不进回测车道），与 `reasoningDepth` 的既有处置一致。
- **字数预估必须是真话**：卡片上那两个数字取自 `GET /settings/intel` 回执里新增的
  `indexMaxChars` / `promptMaxChars`，**前端一个数都不硬编码**（`alert_pending_ttl_s` 被写死成
  前端常量 180 是历轮记录在案的同形缺陷）。**「上下文开销约 2.5 倍」这个比值同样现算**
  （`Math.round(promptMaxChars / indexMaxChars * 10) / 10`，[P2-2]）——它是那两个数的函数，
  硬编码它等于把同一个谎话换个位置再说一遍。
- 偏好持久化：`AiTradeConfig.intel?: AiTradeIntelRule`，读点一律
  `?? { enabled: false, mode: "index" }` 兜底，**不改 persist version**（沿用 `baseRules` 先例，
  避免动 migration 链）。系统默认仍是关闭——持久化的是用户上一次的**显式选择**。

### §3.5 轴 D：预算护栏与观测

**9 个新设置**（前缀沿用 `AI_TRADE_INTEL_`，全部登记进仓库根 `.env.example`，F16；
第 9 项由评审 P0-2a 新增）：

| # | 键 | 默认 | 语义 / 回退档 |
|---|---|---|---|
| 1 | `ai_trade_intel_session_opt_in_enabled` | `True` | 轴 A 总闸。`False` ⇒ `resolve_session_rule` 恒回 `IntelSessionRule()`，注入由 `ai_trade_intel_inject` 独裁，启动请求里的 `intel` 键被忽略（回执 `source="ops_off"`） |
| 2 | `ai_trade_intel_index_max_chars` | `480`（`ge=120, le=3000`） | L1 预算。`mode != "index"` 时不读 |
| 3 | `ai_trade_intel_expand_enabled` | `True` | L2 总闸。`False` ⇒ `intelExpand` 整键缺席、页脚那句承诺也不渲染 |
| 4 | `ai_trade_intel_expand_max_items` | `3`（`ge=1, le=8`） | L2 条数上限 |
| 5 | `ai_trade_intel_expand_max_chars` | `900`（`ge=200, le=4000`） | L2 字符上限 |
| 6 | `ai_trade_intel_alert_pin_enabled` | `True` | L3 总闸。`False` ⇒ `pin_item_id` 恒 0 ⇒ 排序逐字节回退 |
| 7 | `ai_trade_intel_repeat_compress_enabled` | `True` | 横向总闸。`False` ⇒ `intel_tier` 恒 `"full"` ⇒ `plan_disclosure` 恒全展示 |
| 8 | `ai_trade_intel_repeat_max_chars` | `240`（`ge=0, le=2000`） | 横向预算；`0` ⇒ `repeat` 轮只渲染页脚一行 |
| 9 | `ai_trade_intel_repeat_max_rounds` | `6`（`ge=1, le=64`） | **★ 评审 P0-2a**：连续 `repeat` 轮的硬上界。streak 达到它 ⇒ 本轮强制出 `full` 并清零。存在的理由是 `_trim_context` 的左裁是**静默**的、会话拿不到任何回执，而「上一轮列出的 N 条仍然有效」这句话的前提正是那些行还在上下文里。`repeat_compress_enabled=False` 时不读 |

三项**必须**扩 `INTEL_SETTING_BRIDGE`（`intel/settings_bridge.py:24`），否则就是本仓库前科四次的
「假面板」：`sessionOptInEnabled`(1) / `expandEnabled`(3) / `repeatCompressEnabled`(7)。
它们同时出现在 `GET/PUT /settings/intel` 的 wire 上。**#2/#4/#5/#6/#8/#9 不上面板**（纯 env 旋钮），
但仍要进 `.env.example`。

> **★ 评审 P0-1 的连带后果（实施时最容易踩的一步）**：#1/#3/#7 一旦进了这张桥，它们的**生效值**
> 就住在 `resolve_intel_settings` 的派生副本上。因此 `IntelLimits` 里新增的 `expand_enabled` /
> `repeat_compress_enabled` **只能**经 `intel_limits_for(inst_id)` 到达会话侧（§3.0 原则 6 /
> §3.3 C-4）。`_resolve_intel`（在 `build_start_request` 里）读的是**另一条**派生链上的
> `ai_trade_intel_session_opt_in_enabled`——这是**刻意**的：轴 A 的总闸必须在启动请求解析时就
> 生效，那一刻还没有 runtime、也拿不到情报派生副本。两者的分工必须写进 `_resolve_intel` 的
> docstring，否则下一个人会「顺手统一」成一个错的。

`IntelLimits`（`intel/taxonomy.py:493`，映射函数 `limits_from_settings` 在同文件 `493` 起）新增
**7** 个字段（`index_max_chars` / `expand_max_items` / `expand_max_chars` / `repeat_max_chars` /
`repeat_max_rounds` / `expand_enabled` / `repeat_compress_enabled`），映射行一律带 `getattr`
兜底——那是给直接构造 `Settings` 替身的既有用例留的（该文件 515-533 行已有同形先例）。

两把新总闸（`expand_enabled` / `repeat_compress_enabled`）**必须**进 `IntelLimits` 而不是留在
会话侧直读 `Settings`：这正是 §3.0 原则 6 与 §3.3 C-4 的落点，也是 `alert_enabled` 那三把闸
当年走进 `IntelLimits` 的同一条理由（`taxonomy.py` 里那句「纯核心里一个 `getattr(settings, …)`
都不许有」）。

**观测**（`session.py:3836` 旁新增一个**兄弟键**，绝不改 `_intel_cite.stats()` 的形状）：

```python
metrics["aiStats"]["intelDisclosure"] = self._intel_disclosure_stats()
# {"mode":"index","roundsInjected":88,"roundsFull":13,"roundsRepeat":75,
#  "charsMax":476,"charsTotal":18422,"expandRounds":6,"expandItems":11,
#  "expandFolded":4,          # ★ P0-2b：其中因横向折叠而补给复核官的条数
#  "alertPinned":2,"alertMuted":0,   # ★ P0-3：因会话未采信而没有唤醒的告警条数
#  "seenReset":{"pause":1,"summary":3,"streak":2}}   # ★ P0-2a：三种重置各自计数
```

`seenReset` 从一个整数改成**按成因分格的三键字典**（[P2-1] 顺带解决）：一个只报「重置过 5 次」
的读数在事后对账里回答不了唯一值得问的那个问题——「是暂停造成的，还是上下文被摘要吃掉了，
还是压缩连续太久撞了上界」。前端那一行的 `title` 悬停展示这三个数。

前端 `AiTradeStatsPanel` 增一行「情报上下文」：`平均 X 字/轮（峰值 Y）· 展开 N 次`。
**未装配 / `mode=off` ⇒ 整键缺席 ⇒ 该行不渲染**（绝不先显示一个会被当真的 0）。

**启动期校验器**（`config.py:1509` 的 `_check_intel_actionability` 是逐行照抄的模板）
`_check_intel_disclosure`：

```python
    floor = min(int(self.ai_trade_intel_index_max_chars),
                int(self.ai_trade_intel_prompt_max_chars))
    if int(self.ai_trade_intel_repeat_max_chars) > floor:
        raise ValueError(...)
```

**★ 评审 P1-7：判据从 `> index_max_chars` 改成 `> min(index_max_chars, prompt_max_chars)`。**
横向压缩对**两个纵向档都成立**（`mode="full"` 的 repeat 轮同样走 `repeat_max_chars`），只比
索引档的预算会漏掉「`prompt_max_chars` 被调到 200、而 `repeat_max_chars` 是 240」这种同样
自相矛盾的配置。错误文案必须**同时**含三个实际数值（用例 `D3`）。

一个「压缩档比全量档还长」的配置是纯粹的自相矛盾，**直接拒启动**而不静默夹紧——夹紧会让用户
以为自己开了压缩。这条与 §3.0 原则 5 不冲突，理由见该原则下的措辞收窄。

---

## 四、接口设计（Interface Design）

### §4.1 `POST /api/ai-trade/start` 新增请求字段

```python
class AiTradeIntelIn(BaseModel):
    """会话级情报采信约定。**整键缺省 ⇒ 关闭**（与本文件其余六个 `*In` 的惯例方向相反，
    见 spec §3.0 原则 1；`_resolve_intel` 的 docstring 与用例 G-A1 共同冻结这一条）。"""

    enabled: bool = False
    # 值域与纯核心的 `REQUESTABLE_MODES` **逐字对齐**（★ 评审 P1-5）：`"off"` **不是**一个可请求
    # 的档，它只由 `enabled=False` 派生。越界即 422，**不做静默夹紧**。
    mode: Literal["index", "full"] = "index"
```

`{"intel": null}` 与 `{"intel": {}}` 语义相同（都落到 `enabled=False, mode="index"`），
`{"intel": {"enabled": true}}` ⇒ 索引档（[P2-4] 登记：行为正确，此处写明以免被当成两种态）。

`AiTradeGroupStartIn` 追加：

```python
    # 市场情报研判（ai-trade-intel-session-binding 轴 A）：**整键缺省 ⇒ 不注入**。
    # ⚠️ **仅实盘 tab 下发**：情报层不进回测车道（`backtest.py` 全文 0 次 intel），
    # 回测启动体不接该字段（与 `reasoningDepth` 同款处置）。
    intel: AiTradeIntelIn | None = None
```

`_resolve_intel` 在 `build_start_request` 里的**位置**：紧跟 `_resolve_trend` 之后。
它与其余 `_resolve_*` **不共享任何 Settings 键**（它不产出派生副本，只产出一个规则对象），
故顺序上只有一条硬约束——必须在 `req = AiTradeGroupStartRequest(...)` 之前。

### §4.2 启动回执 / `GET /api/ai-trade/status`

分组行新增（手搓 dict，**不设 response_model**——AI-trade 三个生命周期端点一律 `-> dict`，
理由见 `schemas.py:1031` 的成文注记）：

```jsonc
"intel": { "enabled": true, "mode": "index", "source": "request" }
```

`source ∈ {"request", "legacy_fallback", "ops_off", "resolve_failed"}`
（末项由 §3.0 原则 5 的 fail-open 分支产生）。

**★ 评审 P0-5：这个键必须同时补进前端唯一的分组级读点。**
分组行 → 前端状态的映射是 `lib/useAiTradeGroupStream.ts::groupStateFromStatus`，一个**逐键
手抄**的函数——它自己的注释里已经并排写着**五条**同款警告（`acceptance` / `discipline` /
`direction` / `state` / `feeRates`，本仓库为同一形状复发五次）。少这一行：

```ts
    // 会话级情报采信回执（intel-session-binding 轴 A）：**第六条同款注释**——本函数是逐键
    // 手抄的映射，漏抄的表现永远是同一个形状：WS 通路是好的，只有刷新页面后重发现才拿不到，
    // 且不报任何错。`?? null` 要求后端恒下发，才能把「本轮没数据」与「老后端」区分开。
    intel: row.intel ?? null,
```

§3.1 A-4 承诺的 `legacy_fallback` 那一行披露就会在「刷新页面后重发现」这条路径上恒缺席，
而那恰恰是**数十条持仓中的暂停会话**唯一会走的路径。`AiTradeGroupState` 的类型同步加这个字段。

### §4.3 `GET /api/intel/{instId}` 与 WS `intel` 事件

响应体（`AiTradeIntelSummary`）新增 §3.3 的 `binding` 块。
**两条通路同形**由 §3.2-C2 的单一写点保证，并由用例 `G-C2` 逐键对拍。

### §4.4 `GET/PUT /api/settings/intel`

新增三个可读写键 `sessionOptInEnabled` / `expandEnabled` / `repeatCompressEnabled`，
以及两个**只读**回显键 `indexMaxChars` / `promptMaxChars`（供启动弹窗渲染真实数字，
`PUT` 忽略它们）。写侧沿用既有 `INTEL_SETTING_BRIDGE` 往返；三个新开关的往返由 `G-D1` 逐键冻结。

### §4.5 WS 事件

**不新增任何事件类型。** `binding` 随既有 `intel` 事件与 `snapshot.intel` 下发；
`lastRound` 的刷新由决策轮触发的 `note_disclosure` + 下一次 `refresh` / `snapshot` 携带。
（新增第 9 种 WS 事件要落进九张手抄注册表，漏任何一张都是静默的——历轮记录在案。）

---

## 五、数据模型（Data Model）

### §5.1 零 DB 迁移

本轮**不新增表、不新增列、不改任何 `ai_intel_*` 表结构**。理由：会话级选择的持久化真源是
`ai_trade_runs.start_wire`（已经存在，F12），它是一段 JSON 原文，新增一个 `intel` 键不需要迁移；
披露读数是会话内瞬态，落进既有 `metrics_json.aiStats` 这一个 JSON 列。

> 反面参照：`ai_intel_items` / `ai_intel_watch` 那两轮之所以**不能**零迁移，是因为它们要给已经
> 存在的表加列，而 `create_all` 不会补列。本轮没有这个问题——实施时如果发现自己在写
> `_ensure_ai_intel_columns`，说明设计被走偏了。

### §5.2 内存形状汇总

| 名字 | 位置 | 生命周期 |
|---|---|---|
| `IntelSessionRule(enabled, mode)` | `intel/session_rule.py` | 随 `AiTradeGroup` / `GroupIdentity` / `IntelDeps`，重装原样带过去 |
| `IntelBinding(bound, group_id, mode, injecting, reason, digest_item_ids, last_round)` | `intel/binding.py` | 由 `IntelRuntime` 现算，不缓存判定结果 |
| `DisclosurePlan(tier, show, same, refs_kept)` | `intel/disclosure.py` | 单轮，冻结进 `payload["intel"]["plan"]` |
| `ExpandItem(ref, headline, summary, quote, meta)` | `intel/expand.py` | 单轮，冻结进 `payload["intelExpand"]` |
| `AiTradeSession._intel_seen_ids: frozenset[int]` | `session.py` | 会话内；重置点**四个**（`pause` / `resume` / 上下文摘要重置 / 连续 repeat 撞上界，见 §3.2 B-2） |
| `AiTradeSession._intel_round_ids: tuple[int, ...]` | `session.py` | 单轮暂存；`commit_round` 成功后才并进 `_intel_seen_ids`（★ P1-4） |
| `AiTradeSession._intel_repeat_streak: int` | `session.py` | 会话内；连续 `repeat` 轮计数，出 `full` 即清零（★ P0-2a） |
| `AiTradeSession._intel_disclosure: dict` | `session.py` | 会话内累计计数器 → `aiStats.intelDisclosure` |
| `IntelRuntime._pinned: dict[str, tuple[int, float]]` | `intel/runtime.py` | 运行时内；`inst_id → (item_id, expire_wall)`，窗口复用 `alert_pending_ttl_s`（★ P1-1） |
| `AiTradeManager._intel_alert_muted: int` | `manager.py` | 进程内计数；会话未采信而被丢弃的告警数（★ P0-3） |

### §5.3 digest wire 的最终形状（条件键一览）

```jsonc
{
  // 标签词一律取 `taxonomy` 里的既有真源表（TIER/CATEGORY/STANCE/IMPACT_LABELS），绝不自造：
  // 例如 stance 只有「偏多 / 偏空 / 中性」，category 只有「市场情报 / 情绪情报 / 宏观政策 /
  // 链上数据 / 项目动态 / 风险事件」六个。
  "lines": ["[I01] · 中期｜宏观政策·偏空·高：… （3 天前｜3 源｜置信 0.82）"],
  "hidden": 3,
  "refs": {"I01": 1201},          // cite 开关（既有）
  "citeEnabled": true,            // 既有
  "conflictNote": true,           // 既有（有矛盾标注时）
  "mode": "index",                // 新增；mode=="full" 时缺席
  // ★ 评审 P1-3：出键条件改为「digest 非空即出」（纯读侧键，`format_intel` 不读它 ⇒ 不改
  // prompt 的任何一个字节）。它是 `binding.digestItemIds` 的**唯一**有序取数点。
  "itemIds": [1201, 1198],        // 新增；与 lines 同序同长
  // ★ 评审 P0-2b：出键条件 = expand_enabled ∧ digest 非空 ∧ (mode=="index" ∨ repeat_compress_enabled)
  // ——`mode="full"` 的 repeat 轮上正文并不在行里，而复核官恰恰需要它们。
  "bodies": {"I01": {"summary":"…","quote":"…","meta":"…"}},  // 新增
  // ★ 评审 P1-9：`hidden`（预算装不下）与 `same`（见过所以折叠）分列，页脚两句各用各的数。
  "plan": {"tier":"repeat","show":[0],"same":5,"hidden":3,"refsKept":["I02","I03"]}  // 新增；由会话在装配期写入
}
```

**`plan` 是唯一一个不由 `build_digest_and_ids` 写的键**（它依赖会话状态），故它必须写在
`payload["intel"]` 这份**拷贝**上，而不是运行时缓存里的那一份——运行时缓存被多个会话
（多标的分组）共享读，就地改会造成串扰。实施时 `session._build_decision_payload` 里写
`payload["intel"] = {**intel, "plan": plan_wire}`，**注意是浅拷贝新字典**。

---

## 六、文件 / 模块变更清单

### 后端（`services/quant/`）

| # | 文件 | 新建/修改 | 一句话意图 |
|---|---|---|---|
| 1 | `app/ai_auto/intel/session_rule.py` | 新建 | `IntelSessionRule` + `resolve_session_rule` + `INTEL_MODES`（轴 A 纯核心） |
| 2 | `app/ai_auto/intel/binding.py` | 新建 | `IntelBinding` + `binding_wire`（轴 C 的**唯一**写点）+ `BINDING_REASONS` + `note_disclosure` / `binding_for` / **`intel_limits_for`**（★ P0-1 的唯一取数点）/ `_pin_item_id` |
| 3 | `app/ai_auto/intel/expand.py` | 新建 | `ExpandItem` / `plan_expand` / `format_expand`（L2 纯核心） |
| 4 | `app/ai_auto/intel/disclosure.py` | 修改 | `render_index_line` / `intel_tier` / `DisclosurePlan` / `plan_disclosure`；`build_digest_and_ids` 接 `mode` 与 `pin_ids`；`format_intel` 按 plan 挑行 |
| 5 | `app/ai_auto/intel/taxonomy.py` | 修改 | `IntelLimits` 增 5 字段 + `limits_from_settings` 映射；`BINDING_REASONS` 文案表 |
| 6 | `app/ai_auto/intel/service.py` | 修改 | `IntelDeps.session_rule` + `inject_on` / `intel_mode` 属性；`build_snapshot` 改读它并透传 `mode`/`pin_item_id` |
| 6b | `app/ai_auto/intel/summary.py` | 新建（纯搬运） | `read_summary` 整段从 #6 搬出（#6 实读 783 行，本轮增量会越 800）+ 写 unbound `binding`；`service` 重新导出该名字，3 个既有调用点零改动 |
| 7 | `app/ai_auto/intel/runtime.py` | 修改 | `GroupIdentity` 增第 5 字段 `session_rule`；**`identity_of` 补抄那一行**（★ P0-4）；`_build_deps` 把 `ident.session_rule` 传进 `IntelDeps`（★ P0-4）；`build_group_runtime` 接 `session_rule`；新增 `_pinned` 状态 + `_set_pin` / `_live_pin`（★ P1-1，`refresh` **不加形参**）；`_after_round` 算 pin；`summary_for` 出口打 `binding` 戳；`note_disclosure` / `binding_for` / `intel_limits_for` 的薄转发 |
| 8 | `app/ai_auto/intel/settings_bridge.py` | 修改 | `INTEL_SETTING_BRIDGE` 扩三行（三把新总闸），否则是假面板 |
| 9 | `app/ai_auto/intel/__init__.py` | 修改 | 导出 `IntelSessionRule` / `resolve_session_rule` / `REQUESTABLE_MODES` / `binding_wire` / `note_disclosure` / `intel_limits_for` / `plan_disclosure` / `intel_tier` / `inject_intel_expand` |
| 10 | `app/ai_auto/manager.py` | 修改 | `AiTradeGroupStartRequest.intel_rule`、`AiTradeGroup.intel_rule`、**`start_group` 与 `resume_group` 两处赋值**（★ P1-12，`grep intel_rule` 须命中 4 次）、`_wire_intel` 透传 `session_rule=group.intel_rule`、`_on_intel_alert` 按会话规则短路（★ P0-3）、`group_status()` 回执增 `intel` 镜像 |
| 11 | `app/ai_auto/session.py` | 修改 | payload 写 `plan` + `note_disclosure`；`_intel_seen_ids` / `_intel_round_ids` / `_intel_repeat_streak` 与**四个**重置点（★ P0-2a）；`commit_round` 之后才置位 seen（★ P1-4）；`inject_intel_expand` 调用；`aiStats.intelDisclosure`。**全文不得出现 `ai_trade_intel_` 与 `limits_from_settings`**（★ P0-1，门禁 `G-7`） |
| 12 | `app/ai_auto/prompts.py` | 修改 | `_format_intel` 传 plan（薄转发）；`build_reflection_prompt` 插 `expand_block` |
| 13 | `app/api/ai_auto.py` | 修改 | `_resolve_intel` + 装进 `req`；回执镜像 |
| 14 | `app/api/ai_intel.py` | 修改 | `_RUNTIME_ONLY_KEYS` / `_RUNTIME_ONLY_KEYS_UNARMED` 各加 `"binding"` |
| 15 | `app/api/app_settings.py` | 修改 | `/settings/intel` 读写侧增三键 + 两只读回显键 |
| 16 | `app/schemas.py` | 修改 | `AiTradeIntelIn` + `AiTradeGroupStartIn.intel` |
| 17 | `app/config.py` | 修改 | 9 个新设置（第 9 项 `repeat_max_rounds` 由评审 P0-2a 新增）+ `_check_intel_disclosure` 启动期校验器（判据 `> min(index, prompt)`，评审 P1-7） |
| 18 | `.env.example`（仓库根） | 修改 | 9 个新键双向登记 |

### 前端（`apps/web/`）

| # | 文件 | 新建/修改 | 一句话意图 |
|---|---|---|---|
| 19 | `components/Agent/AiTradeIntelFields.tsx` | 新建 | 启动弹窗「市场情报研判」卡片（默认不勾 + 两档单选 + 真实字数） |
| 20 | `components/Intel/IntelBindingStrip.tsx` | 新建 | 弹窗顶部绑定条（三态 + `<details>` 二级说明） |
| 21 | `components/Agent/AiTradeLaunchModal.tsx` | 修改 | 挂载 #19（位置：经验模式卡之后、时间级别之前）+ `AiTradeLaunchOpts.intel` |
| 21b | `lib/useAiTradeGroupStream.ts` | 修改 | **★ 评审 P0-5**：`groupStateFromStatus` 补 `intel: row.intel ?? null` + `AiTradeGroupState.intel`。这个函数是本仓库「静默吞新键」的那一个，同款警告注释已并排五条；漏抄 ⇒ A-4 的 `legacy_fallback` 披露在「刷新页面后重发现」这条路径上恒缺席 |
| 22 | `components/Agent/AiTradeToggle.tsx` | 修改 | `startM` 提交体透传 `intel`；`setAiTradeConfig` 记忆 `opts.intel`；`defaultIntel` 回填 |
| 23 | `components/Chart/AiTradeIntelButton.tsx` | 修改 | 三态叠加点 + `intelBindingText` 进 `title`/`aria-label` |
| 24 | `components/Intel/IntelModal.tsx` | 修改 | 顶部挂 #20 |
| 25 | `components/Intel/IntelCard.tsx` | 修改 | `digestItemIds` 命中条目的竖线 + `[Ixx]` chip |
| 26 | `components/Agent/aiTradeStats/*.tsx` | 修改 | 「情报上下文」一行读数（键缺席即整行不渲染） |
| 27 | `lib/intelTypes.ts` | 修改 | `AiTradeIntelBinding` + `AiTradeIntelSummary.binding` |
| 28 | `lib/intel.ts` | 修改 | `intelBindingText` / `BINDING_REASON_LABEL` / `intelButtonTitle` 合并绑定态 |
| 29 | `lib/aiTradeTypes.ts` | 修改 | `AiTradeIntelRule`、`AiTradeGroupStartReq.intel`、`AiTradeConfig.intel?`、`AiTradeGroupRow.intel?` |
| 30 | `lib/aiTradeApi.ts` | 修改 | 无签名变更（`start` 已收整对象），仅注释登记新键 |
| 31 | `lib/intelApi.ts` | 修改 | `/settings/intel` 三键 + 两只读键的 TS 契约 |

### 仓库级登记（不是代码，但漏了就是违反 CLAUDE.md）

| # | 文件 | 一句话意图 |
|---|---|---|
| 32 | `.claude-index/config.md` | **★ 评审 P1-8**：登记本轮**已知且被接受**的阈值偏离——`app/ai_auto/session.py` 实读 3959 行、`components/Agent/AiTradeLaunchModal.tsx` 实读 1907 行，两者均远超 CLAUDE.md 二的 800 行红线，本轮**继续**在它们上面做加法（各 +50 / +15 行）。`CLAUDE.md` 二逐字要求「调整必须在 `.claude-index/config.md` 中显式记录，并在评审中说明理由」。理由：两者的切分是独立的重构议题（`session.py` 历轮已切出 `hold_horizon` 等模块，`AiTradeLaunchModal` 已按「新字段一律走独立 `*Fields.tsx`」逐轮下沉），把它塞进本轮会让一个纯加法特性的 diff 变成一次不可回退的重构。本轮的自我约束是 §六行数表那一条：**新增的长文本一律住在新模块里，这两个文件只留调用行**。 |

**行数红线复核**（实读，CLAUDE.md 阈值 800）：

| 文件 | 现有行数 | 本轮增量（估） | 处置 |
|---|---|---|---|
| `intel/disclosure.py` | 275 | +170 | ≈445，安全（含 P1-2 的逐条 renderer 闭包与 P1-9 的两句页脚） |
| `intel/runtime.py` | **774** | +75 | ≈849 ⇒ **越线**。把 `note_disclosure` / `binding_for` / `intel_limits_for` / `_pin_item_id` 四个模块级函数放进 #2 `intel/binding.py`，`runtime` 只留薄转发（≈+30 行：`_pinned` / `_set_pin` / `_live_pin` 是**实例状态**，必须留在类里） |
| `intel/service.py` | **783** | +30 | ≈813 ⇒ **越线**。把 `read_summary` 整段（约 90 行，含 `_summary_*` 辅助）搬进新文件 `intel/summary.py`，`service` 重新导出该名字——搬家**单向且零语义变化**，既有 3 个 `intel_service.read_summary` 调用点一个字节不改（`settings_bridge.py` 是同形先例） |
| `session.py` | 3959（早已越线，历轮已切分过 `hold_horizon` 等） | +50 | 本轮**不再**往它身上加长文本：`inject_intel_expand` 与 `_intel_disclosure_stats` 的函数体住在 #3 `intel/expand.py` 与 #2 `intel/binding.py`，session 只留调用行 |
| `api/ai_auto.py` | — | +25 | `_resolve_intel` 只有解析转发，判定在 #1 |

**超了就再下沉一层，不放宽阈值**（CLAUDE.md 二 + `prior_service.py` / `settings_bridge.py` 先例）。
实施时以**实读**行数为准；上表的增量是估值，若某个文件实测仍越线，按同一条处置继续下沉。

---

## 七、测试与验收标准

### §7.1 后端（`services/quant/tests/`）

新增两个文件（分文件的理由是 800 行红线，不是随手拆）：
`test_ai_trade_intel_session_binding_core.py`（纯核心 + 门禁 + 设置登记）与
`test_ai_trade_intel_session_binding_io.py`（会话 / API / 运行时编排）。

**零回归门禁（每一条都断言「逐字节相同」）**

| id | 命题 |
|---|---|
| G-1 | **9** 把新设置全部置回退档时，`build_decision_prompt(payload)` 与 `build_reflection_prompt(payload, d)` 的 `sha256` 与 `4bf512d` 相同（用固定 fixture 冻结十六进制串） |
| G-2 | `ai_trade_intel_session_opt_in_enabled=False` ⇒ 启动请求里 `intel={"enabled":false}` 也不改变注入（运维总闸独裁），回执 `source="ops_off"` |
| G-3 | `mode="full"` + 横向总闸关 ⇒ **`format_intel(wire)` 的输出与 `wire["refs"]`** 的 `sha256` 等于今天，且 `mode`/`bodies`/`plan` 三键缺席（★ P1-3：`itemIds` 改成恒出的纯读侧键，故冻结口径从「整份 wire」改成「喂给模型的那段文本 + 编号表」；`itemIds` 不进 `format_intel`，该断言因此仍然成立） |
| G-4 | `alert_pin_enabled=False` ⇒ `_pinned` 恒空、`pin_ids` 恒空，`refs` 编号顺序与今天逐字相同 |
| G-5 | 回测车道全程 0 次 `intel`（AST 门禁：`backtest.py` / `backtest_manager.py` 不得出现 `intel` 标识符；实读已核实两文件当前各 0 次） |
| **G-6** | **跨轴零回归**：9 把闸同时置回退档时，`AiTradeGroupStartIn(**wire).model_dump()` 里没有任何一个新键悄悄进 payload，且 `group_status()` 的 `intel` 键值为回退档三元组。（上一轮 `LL-FINAL-3` 点名的正是这条跨轴命题缺席——六条按轴分开的用例各自更强，但没有一条覆盖「有没有哪个键悄悄进了 payload」。） |
| **G-7** | **★ P0-1 源码正则门禁**：`app/ai_auto/session.py` 全文不出现 `ai_trade_intel_`，也不出现 `limits_from_settings`（CRLF 环境下正则须写 `\r?\n`）。会话侧一个情报设置键都不许直读 |

**轴 A**

| id | 命题 |
|---|---|
| A1 | **反向门禁**：`AiTradeGroupStartIn` 不传 `intel` ⇒ `resolve_session_rule` 返回 `enabled=False`。若有人照抄「字段缺省=开启」的惯例写成 `True`，本用例判红 |
| A2 | 传 `{"enabled":true,"mode":"index"}` ⇒ `IntelDeps.intel_mode == "index"`；传 `mode="bogus"` ⇒ 端点 422（不夹紧） |
| A3 | **会话选择不被库内行覆盖**：`app_settings["ai_intel.inject"]=true` + 会话 `enabled=false` ⇒ `inject_on is False`（这条正是 §3.1-A2 那个陷阱的守卫） |
| A4 | **★ P0-4**：`enabled=False` 的会话**经一次真实 `rearm_group`** 之后 `deps.session_rule` 仍是 `enabled=False`。**跑法被钉死**：必须走 `rearm_group`（它内部经 `identity_of`），**不许**直接构造 `GroupIdentity` 再断言字段还在——后者对本缺陷恒绿 |
| A5 | **★ P1-12**：冷续跑走一次真实「暂停 → 继续」。`start_wire` 含 `intel` ⇒ 原样恢复且 `group.intel_rule` 与首次启动相同；`start_wire` **不含** `intel`（老会话）⇒ `legacy` 分支 + 回执 `source="legacy_fallback"`。断言点在 `group.intel_rule` 与 `deps.session_rule`，**不是**解析函数的返回值 |
| A6 | `/ai-trade/start` 在情报层任何异常下都不失败（fail-open：`_resolve_intel` 自身 `try/except` 并回退成 `enabled=False`，回执 `source="resolve_failed"`）；`ai_trade_start` 端点**不新增任何外层 `except`** |
| **A7** | **★ P0-3**：`enabled=False` 的会话上，一条命中告警阈值的高危情报**不产生决策唤醒**——`session.note_intel_alert` 未被调用、`aiStats.intelDisclosure.alertMuted == 1`，且该轮 prompt 里**没有任何一处**出现该条目的 `headline` / `summary`（正向断言文本，不只断言段名）。总闸 `session_opt_in_enabled=False` ⇒ 本判据不成立 ⇒ 告警照常唤醒（逐字节回退） |
| **A8** | **★ P1-5**：`resolve_session_rule({"enabled": True, "mode": "off"}, …)` 收口成 `mode="index"`（`"off"` 不是可请求档）；直接 `IntelSessionRule(mode="off")` 抛 `ValueError` |

**轴 B**

| id | 命题 |
|---|---|
| B1 | `mode="index"` 的行**不含**摘要正文，且相同库存下总字符 < `mode="full"` |
| B2 | `_fit_lines` 的试算与定稿用同一 renderer（构造一条「索引装得下、全文装不下」的边界条目，断言不出现半截行、`hidden` 计数正确） |
| B3 | `intel_tier(wake_kind="intel_alert", context_disclosure="repeat")=="full"`（F9 的反例守卫） |
| B4 | `repeat` 轮只渲染新增条目；全部已见 ⇒ 只剩页脚，且 `hidden`（预算装不下）与 `same`（折叠）**分两句、各用各的数**（★ P1-9）；被折叠的编号如实列出 |
| **B4b** | **★ P1-4**：`hidden` 掉的条目**不进** `_intel_seen_ids`——构造「本轮预算只装得下 2 条、库里 5 条」，断言下一轮那 3 条仍被当作新条目渲染（否则它们永远进不了 prompt 且不报错） |
| **B4c** | **★ P1-4**：`commit_round` 未被调到（decide 抛异常 / 降级早退）时 `_intel_seen_ids` **不变** |
| B5 | **两次渲染一致**：同一 payload 连续 `build_decision_prompt` 两次，产物逐字节相同（原则 3 的守卫；把 `_intel_seen_ids` 在两次之间改掉，仍必须相同） |
| B6 | `plan` 写在 payload 的**拷贝**上：多标的分组下改 A 的 plan 不影响 B（运行时缓存未被就地改） |
| B7 | L2 只在 `open_long/open_short/close` 上出现；`hold` 轮 `payload` 无 `intelExpand` 键 |
| B8 | L2 展开条目 ⊆ `decision.intel_refs`，条数 ≤ `expand_max_items`，总长 ≤ `expand_max_chars` |
| B9 | **决策 prompt 不受 L2 影响**：注入 `intelExpand` 后 `build_decision_prompt(payload)` 逐字节不变，而 `build_reflection_prompt` 变了（F7 连锁的正反两面） |
| B10 | `submit_trade_reflection` 的工具 schema 一个字段不加（对 `tools` 做 `sha256` 冻结） |
| B11 | L3：告警轮后 digest 首行是被 pin 的条目，`refs[ref] == pinned_item_id`，`in_digest is True`，`wake_text` 走「已在下方情报段中列出」分支 |
| B12 | L3 预算极小（`index_max_chars=120`）时 pin 条目仍装不下 ⇒ `in_digest is False` ⇒ 既有内联降级句照常（诚实回落） |
| **B13** | **★ P0-2a**：`commit_round` 返回非 `None`（上下文被摘要重置）之后，下一轮 `tier == "full"`、`same == 0`，且 `seenReset.summary` 增 1 |
| **B14** | **★ P0-2a**：连续 `repeat` 达 `repeat_max_rounds` ⇒ 该轮强制 `full`、streak 清零、`seenReset.streak` 增 1 |
| **B15** | **★ P1-2**：`mode="index"` 且条目被 pin ⇒ **该行含摘要正文**（`render_line` 形态），其余行不含；置顶条挤掉后续条目时 `hidden` 计数正确、不出现半截行（预算试算与定稿共用同一个逐条 renderer） |
| **B16** | **★ P1-10**：`format_expand(...)` 的输出**以 `disclosure._BANNER` 开头**（不可信包裹）；空序列 ⇒ 空串 |
| **B17** | **★ P0-2b**：`mode="full"` 的 `repeat` 轮上，一次 `open_long` 决策的 `intelExpand` 里**含被折叠掉的条目**（`origin=="folded"`），且这些条目的编号 ⊆ `plan.refsKept`；`tier=="full"` 的轮上 `folded` 组为空 |
| **B18** | **★ P1-1**：告警落地之后、决策轮之前调一次 `invalidate(inst_id)`（模拟用户点「标记已读」），digest 重建后**置顶仍在**（`_pinned` 是状态而非形参）；越过 `alert_pending_ttl_s` 之后置顶自动解除 |

**轴 C / D**

| id | 命题 |
|---|---|
| C1 | `binding` 在 WS `intel` 事件、`snapshot.intel`、`GET /intel/{instId}` 三条通路上**逐键相同** |
| C2 | 未装配 / 无运行时 ⇒ `binding.bound is False`、`reason=="not_bound"`，且**键存在**（形状恒定） |
| C3 | **★ P1-6**：五个 `reason` 码各有一条构造用例；不变式 `injecting=True ⇒ inject_on=True ∧ armed=True` 成立，且**反向不成立**——壳运行时上构造一格 `inject_on=True ∧ injecting=False ∧ reason=="not_armed"` |
| C4 | `note_disclosure` 之后下一次 `summary_for` 带上 `lastRound`；未装配时 no-op 不抛 |
| **C5** | **★ P1-3**：`binding.digestItemIds` 与 `wire["itemIds"]` **逐元素同序相等**，且在 `mode="full"` + 横向总闸关的回退档上**依然有序非空**（v1 的出键条件在这一格会让它取不到数） |
| D1 | 三把新开关经 `PUT /settings/intel` 往返后 `Settings` 上的值真的变了（假面板守卫，逐键） |
| D2 | `G-Env`：**9** 个键在 `Settings` 与仓库根 `.env.example` 双向对齐 |
| D3 | **★ P1-7**：`repeat_max_chars > min(index_max_chars, prompt_max_chars)` ⇒ 进程启动即拒绝；两个反例各一条（索引档更小 / 全文档更小），错误文案含**三个**实际数值 |
| D4 | `aiStats.intelDisclosure` 在 `mode="off"` 时**整键缺席** |
| **D5** | **★ P0-1 假面板的另一半**：`app_settings` 里把 `ai_intel.expand_enabled` 置 `false` 之后，`intel_limits_for(inst).expand_enabled is False`（会话侧读到的是**派生副本**）；同时进程级 `Settings.ai_trade_intel_expand_enabled` 仍为 `True`——两者不等**正是**本用例要证明的那件事，也正是 v1 会读错的那一格 |

### §7.2 前端（`apps/web`，vitest；**必须在 `apps/web` 目录内跑**，组件用例需 `NODE_ENV=test`）

| id | 命题 |
|---|---|
| W1 | `AiTradeIntelFields` 首渲染**未勾选**；勾选后才出现档位单选 |
| W2 | 未配源 / 运维关 ⇒ 整卡禁用且给出「去设置」入口，勾选框不可点 |
| W3 | 卡片上的字数取自接口回执，**源码里不得出现字面量 480 / 1200**（源码正则门禁；CRLF 环境下正则须写 `\r?\n`） |
| W4 | `AiTradeToggle` 提交体含 `intel`，且 `setAiTradeConfig` 记住的是 `opts.intel` 而不是回执 |
| W5 | 老 localStorage（无 `intel` 键）⇒ 回填 `{enabled:false, mode:"index"}`，persist version 仍为 31 |
| W6 | `IntelBindingStrip` 三态各渲染各自的文案；未采信态**不渲染任何可点的开关** |
| W7 | `AiTradeIntelButton` 三态叠加点 + `aria-label` 含绑定态；既有未读徽标/降级染色用例全绿 |
| W8 | `IntelCard` 只给 `digestItemIds` 命中的条目加竖线与编号 chip |
| W9 | **后端文案零 markdown 记法**：`binding.reason` → 文案表里不得出现 `**` / 反引号（本仓库第 5 次同形复发，前端门禁看不见后端字面量 ⇒ 这条用例要放在**后端**，扫 `taxonomy.BINDING_REASONS` 的文案值） |
| W10 | `intelNoDangerousHtml.source.test.ts` 既有门禁对两个新组件同样成立 |
| **W11** | **★ P0-5**：`groupStateFromStatus(row({intel: {...}}))` 的产物含 `intel`；老后端（`row` 无该键）⇒ `intel === null`。这条放在 `lib/aiTrade.test.ts` 里，与既有的 `state` / `discipline` / `direction` 三条同款守卫并排 |
| **W12** | **★ P1-11**：任一标的的 `GET /intel/{instId}` 失败 ⇒ 卡片上那句条数**整句不渲染**，勾选框与档位单选**仍然可用**；全部 `lastCollectTs` 为 `null` ⇒ 换成「尚未成功采集过」那一句 |

### §7.3 验收（人工，按序执行）

1. 关掉情报开关启动一个 3 标的分组 → 打开左侧任一标的的情报弹窗 → 顶部条说「未采信」；
   `agentTraces` 里任意一轮的 prompt 快照中**不存在**「外部市场情报」段。
   **★ 评审 P0-3 补强（v1 这一条恰好检不出那个缺陷）**：再触发（或等待）一次高危情报告警，
   确认 ① 弹窗的告警列表里**看得见**这条告警（采集与展示不受影响）；② `agentTraces` 里
   **没有**由它触发的新决策轮；③ 全部 prompt 快照里**搜不到**该条目的标题与摘要正文
   ——搜的是**正文**，不是段名。刷新页面后重新发现该分组，分组卡上的情报行仍显示「未采信」
   （★ 评审 P0-5 的那条通路）。
2. 勾选「让 Agent 参考市场情报 · 索引档」重启 → 顶部条说「索引档 · 上一轮进入上下文 N 条」；
   prompt 快照里情报段存在且**每行不含破折号后的摘要**；段末有那句「下单前的复核环节会自动展开」。
3. 在同一根 K 线内等待 2 次以上定时唤醒 → 第 2 次起情报段压成「本轮无新增情报；上一轮列出的
   N 条仍然有效」；`aiStats.intelDisclosure.roundsRepeat` 增长。
4. 等到一次真实开仓（或用 `signal_flip` 造一次）→ `agentTraces` 的 `reflection` 相位输入里出现
   展开段，展开段**段首是不可信包裹句**，且展开的编号 ⊆ 该轮 `intelRefs` ∪ `plan.refsKept`。
   **在一个 `repeat` 轮上再验一次**：此时复核官的情报段只剩页脚，展开段里**必须**出现被折叠掉
   的那几条（★ 评审 P0-2b）——否则守着真钱的那一位看到的是一串它从未见过的编号。
5. 触发一次高危情报告警轮 → 该条目排在情报段首位、**索引档下该行含摘要正文**（★ P1-2），
   唤醒句是「已在下方情报段中列出」。在告警到达与决策轮之间在弹窗里点一次「标记已读」，
   再看这一轮：置顶**仍在**（★ P1-1）。
6. 让会话连续跑到一次上下文摘要重置（`contextRefreshRounds` 轮）→ 紧接着那一轮的情报段是
   **全量**而不是「上一轮列出的 N 条仍然有效」（★ P0-2a）；`aiStats.intelDisclosure.seenReset.summary`
   增 1。
7. 在设置中心把「下单前展开」关掉（**不重启进程**）→ 下一次开仓的 `reflection` 输入里
   **没有**展开段（★ P0-1：若它还在，就是会话侧读了另一份 settings）。
8. 把 9 把设置全部置回退档并重启 → 逐字节回到今天的行为（用 `agentTraces` 的 prompt 文本对拍）。

### §7.4 跑测口径（本仓库成文经验，照做）

- 后端：`cd services/quant`，`python -u -m pytest -q`（**必须 `-u`**；**不要并发**）。
  基线：合入前先跑一遍并记下既有红（工作树里有未跟踪的在制文件会带进无关的红）。
- 前端：`cd apps/web`，`NODE_ENV=test pnpm vitest run`。
- 干净检出验证：`git write-tree` + `git worktree`，并**先 `cp services/quant/.env`**。
- 控制台是 GBK：诊断脚本里**不要**出现 `⇒` 之类的字符，否则 `UnicodeEncodeError`。

---

## 八、风险与缓解（Risks & Mitigations）

| # | 风险 | 概率/影响 | 缓解 |
|---|---|---|---|
| R1 | **会话选择被库内行静默覆盖**（把 `enabled` 映射成 `Settings.ai_trade_intel_inject`） | 高 / 致命：开关变成假开关 | §3.1-A2 的显式形参链 + 用例 A3；`_resolve_intel` 的 docstring 逐字写下禁令 |
| R2 | **默认值方向被"照抄惯例"改回开启** | 中 / 高：直接违反需求 | 反向门禁 A1；`AiTradeIntelIn` 的 docstring 首句就是这条 |
| R3 | **重装（`rearm_group`）丢掉会话规则** | 中 / 高：用户改一次设置就推翻了启动时的选择 | `GroupIdentity` 第 5 字段 + 用例 A4（这正是评审 P0-1 修过的形状） |
| R4 | **冷续跑改变老会话的证据面** | 中 / 中：数十条持仓中的暂停会话 | `model_fields_set` legacy 分支 + 显式回执 `source="legacy_fallback"`（A5） |
| R5 | **两次渲染漂移**（plan 在 `format_intel` 内现场重算） | 中 / 高：`_context` 与模型看到的不一致，且不报错 | 原则 3 + 用例 B5；`plan` 只由会话在装配期写一次 |
| R6 | **运行时缓存被就地改**（多标的串扰） | 中 / 高 | `payload["intel"] = {**intel, "plan": …}` 浅拷贝 + 用例 B6 |
| R7 | **索引档信息量不足导致模型判断变差** | 中 / 中 | ① 索引行保留类别·立场·影响·源数·置信·时效六项结构化研判；② L2 在真的要下单时补全文；③ L3 保证高危条目全文可见；④ 保留 `full` 档作为逃生口；⑤ 默认关闭 ⇒ 存量用户不受影响 |
| R8 | **横向压缩让模型"忘掉"仍然有效的情报** | 中 / 中 | 页脚**必须**逐字说明「上一轮列出的 N 条（[I01]–[I06]）仍然有效」并列出编号；`repeat` 只在同一根 K 线内成立（新根即 `candle_close` ⇒ `full`）；总闸可关 |
| R9 | **L3 置顶改变 refs 编号 → 引用归因错位** | 中 / 高：`intelRefs` 指向错的条目 | 编号与 `refs` 在**同一次** `build_digest_and_ids` 里定稿（既有不变式），置顶只改顺序不改这条；用例 B11 断言 `refs[ref] == pinned_item_id` |
| R10 | **假面板**（新开关没进 `INTEL_SETTING_BRIDGE`） | 中 / 高：前科四次 | 用例 D1 逐键往返 |
| R11 | **`.env.example` 漏登记** | 中 / 中：打包形态下用户无从知道旋钮存在 | 用例 D2（`G-Env` 同形） |
| R12 | **后端文案里的 `**` 在 JSX 显字面星号** | 中 / 低：第 5 次复发 | 用例 W9 放在**后端**扫文案表（前端门禁看不见后端字面量——这正是前 4 次复发的原因） |
| R13 | **`runtime.py`（实读 774）与 `service.py`（实读 783）越 800 行红线** | 高 / 低 | §6 行数复核表已给出两条下沉方案（`binding.py` 收三个模块级函数、`summary.py` 收 `read_summary` 整段，均为单向纯搬运）；实施时以实读行数为准 |
| R14 | **工作树脏**（`api/ai_intel.py` 的 204 修复在制，F17） | 高 / 中：误提交 | 实施节点用**行级精确暂存**（本仓库成文配方），或先与在制变更的归属者对齐 |
| R15 | **`intel_alert` 轮被横向压缩** | 中 / 高：最该看清的一轮反而被压 | `intel_tier` 不复用 `contextDisclosure`（用例 B3） |
| R16 | 新增 9 个设置让 `Settings` 继续膨胀 | 低 / 低 | 只有 3 个上面板；其余 6 个是纯 env 旋钮，且全部带 `ge/le` 值域 |
| **R17** | **会话侧与运行时侧各读一份情报 settings**（★ 评审 P0-1） | 高 / 高：面板上关了、会话这侧照旧开着，请求 200、行为不变 | §3.0 原则 6 的唯一取数点 `intel_limits_for` + 源码正则门禁 `G-7` + 用例 `D5`（它**断言两份 settings 不相等**，正面证明这件事的存在） |
| **R18** | **横向压缩引用的内容已被上下文摘要吃掉**（★ 评审 P0-2a） | 中 / 高：prompt 里一句确定错误的话 + `intelRefs` 静默塌成空集 | 四个重置点（含 `commit_round` 返回摘要与 `repeat_max_rounds` 上界）+ 用例 `B13`/`B14` + 人工验收第 6 条 |
| **R19** | **无状态复核官拿到一段指向它没见过的编号的压缩情报**（★ 评审 P0-2b） | 中 / 高：守着真钱的那一层证据面被压缩掉 | L2 自足（`origin="folded"` 组）+ `bodies` 出键条件放宽 + 用例 `B17` + 人工验收第 4 条。仓库先例：`format_counter` 的 docstring 已为同一形状写下同一条纪律 |
| **R20** | **会话未采信，情报仍从告警唤醒句进入 prompt**（★ 评审 P0-3） | 中 / **致命**：用户明确关掉的东西照样进了真实资金决策 | `_on_intel_alert` 的单点短路 + 用例 `A7`（**正向搜正文**，不搜段名）+ 人工验收第 1 条补强 |
| **R21** | **`identity_of` 漏抄 ⇒ 重装把 `off` 翻成 `full`**（★ 评审 P0-4） | 中 / 高：方向最坏的静默回落 | `identity_of` / `_build_deps` 两处显式赋值 + 用例 `A4` 的**跑法**被钉死（必须经 `rearm_group`） |
| **R22** | **`groupStateFromStatus` 漏抄 `intel`**（★ 评审 P0-5） | 高 / 中：本仓库同一形状已复发五次 | §六 #21b + 用例 `W11` + 人工验收第 1 条的「刷新后重发现」 |

---

## 九、实施顺序（建议的提交内部推进顺序，单次提交交付）

1. **纯核心先行**：#1 `session_rule.py`、#2 `binding.py`、#3 `expand.py`、#4 `disclosure.py`、
   #5 `taxonomy.py` + 对应的 core 用例（此时 `pytest` 应全绿且**行为零变化**——所有新参数都带
   回退默认值）。
2. **设置与登记**：#17 `config.py`、#18 `.env.example`、#8 `settings_bridge.py`、#15
   `app_settings.py`、#32 `.claude-index/config.md` + `G-Env` / `D1` / `D3` 用例。
3. **接线**：#6 `service.py` → #6b `summary.py` → #7 `runtime.py` → #10 `manager.py` →
   #13 `api/ai_auto.py` → #16 `schemas.py`（自下而上，每一步跑一次 `test_ai_intel_*`）。
   本步**必须**先写 `A4`（经 `rearm_group`）与 `D5`（派生副本）两条用例再动代码——它们分别对应
   评审 P0-4 与 P0-1，都是「写完代码再补用例就会写成恒绿」的那种形状。
4. **会话与 prompt**：#11 `session.py`、#12 `prompts.py` + io 用例（`B13`/`B14`/`B17`/`G-7`）。
5. **前端**：#27→#28→#29（类型与纯函数先行）→ **#21b `useAiTradeGroupStream.ts`**（★ P0-5，
   与类型同批改，别等到接线那一步才想起来）→ #19/#20 新组件 → #21..#26 接线 → vitest。
6. **最后单拎一步**：#14 `api/ai_intel.py`（[P2-3]）。该文件有在制修改（F17 / R14），单独一步
   可以把行级精确暂存的窗口压到最小；改完立刻 `git diff --stat` 复核只动了那两行 `_RUNTIME_ONLY_KEYS`。
7. **全量回归 + 干净检出验证**（§7.4），最后跑一遍 §7.3 的 **8** 条人工验收。

**回退**：任一轴出问题，把它的总闸置回退档即可（无需回滚代码），这是本轮四把总闸存在的全部理由。

---

## 十、明确的非目标（Non-Goals）

- 不改情报的采集 / 整理 / 核查 / 六道闸 / 落库 / 引用归因的任何判定。
- 不让情报参与任何风控闸的判定（它今天不是、本轮之后也不是；它只是证据）。
- 不给决策 Agent 新增可调用的工具（不引入多轮 tool loop——那会改变整条决策链的时延与成本模型）。
- 不做会话运行中改变情报档位（证据面在会话内必须恒定，否则同一次会话的前后两半不可比）。
- 不把情报带进回测车道。
- 不新增 WS 事件类型、不新增数据库表或列。
- **不切分 `session.py` / `AiTradeLaunchModal.tsx`**（两者早已越 CLAUDE.md 800 行红线）。这是一次
  **已登记**的阈值偏离（§六 #32），不是遗漏；本轮的自我约束是「新增的长文本一律住在新模块里，
  这两个文件只留调用行」。

---

## 评审结论（Review Verdict）

**结论：有条件通过（approved with conditions）。**

v1 的问题诊断（三件缺的事）与四轴切分是准确的，`_wire_intel` / `build_group_runtime` /
`build_digest_and_ids` / `contextDisclosure` 这几处关键落点的实读也都对得上；「默认关闭且与仓库
既有六个字段惯例方向相反」这条需求硬约束被正确地识别成需要**反向门禁**的东西。规模上，四条轴
互相独立、各带总闸、零 DB 迁移、不进回测车道，这是一个**尺寸合适**的加法特性，没有过度设计。

但 v1 有 **5 条 P0**，且五条同属一个形状：**新通路绕过了它自己声称的约束**——

| P0 | 声称 | 实际会发生 |
|---|---|---|
| P0-1 | 「三把新总闸进桥，面板真的作用到系统」 | 会话侧读另一份 settings ⇒ 半个假面板 |
| P0-2 | 「渐进式披露不损失证据」 | 上下文被摘要吃掉 / 无状态复核官 ⇒ 引用指向不存在的内容 |
| P0-3 | 「默认关闭、用户可选不采信」 | 情报改从告警唤醒句进 prompt |
| P0-4 | 「`GroupIdentity` 带得过重装」（R3 自己写的缓解） | `identity_of` 手抄漏一行 ⇒ `off` 翻成 `full` |
| P0-5 | 「binding 是一等事实、三条读路径同形」 | 分组级回执没落进前端唯一的读点 |

五条全部已在 v2 正文中改写，并各配至少一条**能真的判红**的用例（P0-1→`D5`/`G-7`、
P0-2→`B13`/`B14`/`B17`、P0-3→`A7`、P0-4→`A4` 且钉死跑法、P0-5→`W11`）。12 条 P1 同样已改写。

### 放行条件（C-1 … C-8，实施节点必须逐条回执）

| # | 条件 |
|---|---|
| **C-1** | `D5` 与 `A4` **必须先于实现代码写完并确认判红**。这两条对应的缺陷（读错 settings 副本 / 手抄漏一行）都属于「先写代码再补用例就会把用例写成恒绿」的形状：`D5` 必须显式断言「进程级 `Settings` 与派生副本**不相等**」，`A4` 必须经 `rearm_group` 而不是直接构造 `GroupIdentity`。 |
| **C-2** | `A7` 的断言必须是**正向搜正文**——在该轮全部 prompt 文本里搜该条目的 `headline` 与 `summary` 子串，而不是断言「没有『外部市场情报』这个段名」。v1 的验收就是栽在后一种写法上。 |
| **C-3** | `G-7` 源码正则门禁上线后，`app/ai_auto/session.py` 若因既有代码而无法通过，**不许放宽门禁**，改为把那几处一并迁到 `intel_limits_for`；实施发现里如实登记迁了哪几处。 |
| **C-4** | `grep -n "intel_rule" app/ai_auto/manager.py` 必须命中 **4** 次（两处字段声明 + `start_group` + `resume_group`）。实施回执里贴出这次 grep 的输出。 |
| **C-5** | `G-3` 的冻结口径改成「`format_intel(wire)` 的输出 + `wire["refs"]`」之后，必须**重新生成**冻结用的十六进制串，并在用例里注明它取自 `4bf512d`（旧串对新口径无意义，照抄会得到一条永远绿的假门禁）。 |
| **C-6** | 人工验收第 6、7 条（上下文摘要重置后的全量重铺、不重启进程改面板开关）**必须真跑**，不能用离线用例代替——这两条正是 P0-2a 与 P0-1 在生产里唯一会显形的路径。 |
| **C-7** | §六 #32（`.claude-index/config.md` 的阈值偏离登记）与本轮代码**同一次提交**。CLAUDE.md 二把它写成硬要求，而上一轮已因同一条被点名一次。 |
| **C-8** | 实施期若发现某处实读行数与 §六表格不符（该表是估值），按「超了就再下沉一层，不放宽阈值」处置，并把偏离逐条记进实施发现——**不要**默默把新函数塞进已经越线的文件。 |

### 判定为「不改」的三件事（附理由，防止下游重复投入）

1. **`intel_alert` 轮的横向档**：v1 的 `intel_tier` 不复用 `contextDisclosure`、对 `intel_alert`
   恒返回 `full`——这是对的，但理由要说准：`tactical_context.py:43-45` 的 `_FULL_TIER_KINDS`
   **已经**含 `intel_alert`，只有 `_EXECUTION_FULL_KINDS`（:47）不含。所以这里不是「全仓都把它
   当 repeat」，而是「执行周期段那一格把它当 repeat」。判定不改，只把 F9 的转述收紧（[P2-5]）。
2. **`payload["contextDisclosure"]` 恒存在**：`wake_runtime.py:448` 写的是
   `execution_disclosure(wake_kind=kind, enabled=True)`——`enabled` 是**硬编码的 `True`**，没有
   任何总闸能让这个键消失。故复用它做横向判据不会与别人共命运，v1 的做法安全，不改。
3. **`read_summary` 的签名**：v1 说它已有 5 个形参、正好卡在 CLAUDE.md 上限——实读确认
   （`db, inst_id, *, settings, arm, now`）。`binding` 走函数体内写死 `unbound`，不加形参。
   判定不改。

**通过之后的交付判据**：§7.1 的 7 条零回归门禁 + 轴 A 8 条 + 轴 B 20 条 + 轴 C/D 10 条全绿，
§7.2 的 12 条 vitest 全绿，§7.3 的 8 条人工验收逐条留证，且 C-1…C-8 在实施回执里逐条应答。

---

## 实施过程发现的方案缺陷（Issues Found During Implementation）

下列每一条都在实施期被**实读证伪或补全**，并已按「不静默偏离」的要求在正文之外单独登记。
除 I-1 与 I-6 外，其余都是设计正确、只是没写全的地方；两条真的偏离已给出理由与代价。

### I-1（★ 真偏离）`G-7` 门禁在既有代码上必然判红，而 C-3 的处置会**静默关掉引用归因**

放行条件 C-3 写的是「`session.py` 若因既有代码无法通过门禁，**不许放宽门禁**，改为把那几处
一并迁到 `intel_limits_for`」。实读发现 `session.py` 有 **4 处**命中：

| 位置 | 读的是什么 | 直接迁到 `intel_limits_for` 的后果 |
|---|---|---|
| `session.py:403-405` | `ai_trade_intel_enabled` ∧ `ai_trade_intel_attribution_enabled`（构造 `IntelAttribution`） | **致命**：它在 `AiTradeSession.__init__` 里被读，而 `_wire_intel` 排在**启动成功之后**——那一刻 `_BY_INST` 还是空的，`intel_limits_for` 必然回 `None`，引用归因会被**静默关掉** |
| `session.py:2124` | `ai_trade_intel_alert_pending_ttl_s`（告警过期判据） | 未装配时只能回落一个前端式的硬编码常数——正是本仓库明令禁止的那一类假话 |
| `session.py:2090`（注释） | 文中出现 `ai_trade_intel_alert_enabled` 这个字面量 | 门禁扫的是**源码文本**，不区分注释与代码 |
| `session.py:2180`（注释） | 文中出现 `limits_from_settings` | 同上 |

**实施采用的修法**：门禁一个字节不放宽（`session.py` 全文两个字面量都是 0 次命中），但迁移的
**落点**是 `intel/binding.py` 里两个**同口径**的读点 `attribution_enabled(settings)` /
`alert_pending_ttl_s(settings)`，它们**刻意仍读进程级 `Settings`** 并在 docstring 里逐字写下
为什么（构造期还没有运行时）。两条注释按语义改写。

**代价与它守住的东西**：门禁从此守的是「会话侧不许**直接**出现情报设置键名」这条可机械检查的
纪律，而「必须读派生副本」这条更强的约束由 `D5` 与 `binding.py` 里那两段 docstring 承担。
把一条会静默关掉引用归因的迁移做进去，比这条门禁少守一格坏得多。

### I-2（★ 真偏离）C-4 要求 `grep intel_rule` 命中 4 次，但 `resume_group` 里那一次会是**死代码**

C-4 的机械判据是「两处字段声明 + `start_group` + `resume_group`」。实读发现：
`AiTradeGroup` 的**唯一**构造点是 `manager._build_group`，而 `resume.resume_cold_group`
（`resume.py:270`）**直接调用它**；热续跑复用内存里那个已经带着规则的分组对象、根本不重建。
故在 `resume_group` 里再赋一次是一条永远走不到的赋值。

**实施采用的修法**：`intel_rule` 在 `_build_group` 里赋一次，覆盖 start / 冷续跑两条路径；
`grep -n "intel_rule" app/ai_auto/manager.py` 实际命中 **7 次**（两处字段声明 + `_build_group`
赋值 + `_wire_intel` 透传 + `_on_intel_alert` 判据 + `_intel_receipt` 读点 + 两条注释）。
C-4 的**实质**（没有一条路径漏掉规则）由用例 `test_a3` / `test_a4` 与冷续跑走同一个
`build_start_request` 的既有事实共同保证。

### I-3 `aiStats.intelDisclosure.alertMuted` 在 spec 的落点（manager 进程级计数器）到不了它的读点

§5.2 把 `_intel_alert_muted` 登记成 `AiTradeManager` 上的进程内计数，而 §3.5 又要求它出现在
**会话级**的 `aiStats.intelDisclosure` 里（用例 `A7` 直接断言这个值）。两者不可能同时成立：
`aiStats` 由 `session._build_run_metrics` 装配，拿不到 manager 的私有计数。

**实施采用的修法**：把成员查找**提前到规则判定之前**（`_on_intel_alert` 的落点与单点判定
一个字节不变），未采信时调 `member.session.note_intel_alert_muted()`。P0-3 的三条实质
（唯一落点、台账与 WS 列表不受影响、总闸关即逐字节回退）全部保持。

### I-4 `DisclosurePlan` 需要第 5 个字段，否则「被 repeat 预算挤掉的新条目」无处安放

§3.2 B-2 只定义了 `tier` / `show` / `same` / `refs_kept` 四个字段。但 `repeat` 轮的预算
（`repeat_max_chars`）同样会挤掉一部分**没见过**的条目——它们既不是 `same`（不是「见过所以
折叠」），也不在 wire 的 `hidden` 里（那是 digest 那一层的预算）。四个字段装不下这件事，
后果是一个不报错的漏计：页脚会说「另有 3 条因篇幅未展示」，而实际被藏起来的是 5 条。

**实施采用的修法**：`DisclosurePlan` 增第 5 个字段 `hidden`（= wire 的 `hidden` + 被 repeat
预算挤掉的条数），`plan` wire 的键集**与 §5.3 逐字相同**（仍是 `tier/show/same/hidden/refsKept`）。
被挤掉的那些同时进 `refs_kept`，故复核轮的 L2 照样能把它们补给复核官（它们同样是「复核官
从没见过」的条目）。

### I-5 `bodies` 里的 `quote` 在 digest 定稿点上**取不到**——`IntelItem` 不带引文原文

§3.2 B-4 要求展开段给出「标题 + 摘要 + ≤1 条已核实引文」，且 `summary` / `quote` 必须来自
digest wire。实读发现 `IntelItem` 只有 `quoted` / `quote_count` 两个**布尔与计数**，引文原文
住在独立的 `ai_intel_quotes` 表里；`build_digest_and_ids` 的入参里根本没有它。照 spec 直写的
结果是 `quote` 恒为空串——一个有写点没读点的死字段。

**实施采用的修法**：`build_digest_and_ids` 增一个 `quotes: dict[int, str] | None = None`
（默认 `None` ⇒ 逐字节回退），由 `build_snapshot` 在**它已经开着的那个只读短会话里**
一次 `IN` 查询填好（`repo.load_intel_quote_texts`，新增在既有
`db/ai_intel_evidence_repositories.py` 里）。**决策热路径一次库都不查**——这一跳发生在情报轮
/ `invalidate` 的快照刷新上，且只在 L2 总闸开着、库里真的有带引文的条目时才发。

### I-6 `taxonomy.BINDING_REASONS` 做成「码元组 + 另一张文案表」会造出一张没有读点的表

§3.3 C-1 说它「与 `ARM_REASONS` 同款单一真源」（后者是**码元组**），§六 #5 与用例 `W9` 又把它
称作「文案表」并要求扫**文案值**。两者同时满足只能造出两张表，而后端的那张中文文案表在本轮
**没有任何消费者**（`binding` wire 只带 `reason` 码；前端按 §3.3 C-3 自带 `BINDING_REASON_LABEL`）
——那正是 spec 自己在 [P2-1] 里点名要避免的「有写点没读点」。

**实施采用的修法**：`BINDING_REASONS` 做成 `dict[str, str]`（码 → 中文短语）。码的值域校验
（`binding_wire` 里那一行 `reason in BINDING_REASONS`）是它的真实读点，`W9` 扫的正是它的值。

### I-7 `_check_intel_disclosure` 的判据在**默认配置**下就与 `ge/le` 值域冲突，须复核默认值

`repeat_max_chars` 的 `le=2000`、`index_max_chars` 的 `ge=120`：一个把
`AI_TRADE_INTEL_INDEX_MAX_CHARS` 调到 120 的运维会在**默认** `repeat_max_chars=240` 上直接
拒启动。这是**想要的**（那确实是一个自相矛盾的配置），但默认三元组必须自洽：
`240 ≤ min(480, 1200)` ✓。用例 `test_g6_new_settings_do_not_leak_into_the_start_wire_by_default`
把这条不变式冻结在默认值上，避免下一个人改默认时把进程改成起不来。

### I-8 `G-1` / `G-3` 的既有冻结用例必须**同步改写**，否则新增的纯读侧键会把它们判红

`tests/test_ai_intel_disclosure_cite.py::test_cite_off_renders_byte_identical_to_v1` 断言的是
**整份 wire 的键集**（`sorted(wire) == ["hidden", "lines"]`）。本轮新增的 `itemIds` / `bodies`
都是纯读侧键（`format_intel` 不读它们），按旧口径会判红——而那正是评审 P1-3 / 放行条件 C-5
要求改写冻结口径的原因。

**实施采用的修法**：该用例改成断言「`refs` / `citeEnabled` / `plan` / `mode` 四键缺席 +
`format_intel(wire)` 的 `sha256` 与合入前相同」，并**新增**一条
`test_read_side_keys_do_not_change_the_prompt_text` 正面证明那两个键进不了 prompt——
这不是「口径放宽了」，而是「被冻结的从此是模型真正看到的那段文本」。
`tests/test_ai_intel_wiring.py::test_build_group_runtime_keeps_its_public_signature`
同步从 10 个形参改成 11 个并写明理由（见 `.claude-index/config.md` 的偏离登记）。

### I-9 `_wire_intel` / `_on_intel_alert` 必须用 `getattr` 读 `group.intel_rule`

`manager.py` 里已有五条同款注释：既有测试替身用 `SimpleNamespace` 直接构造分组，新增字段对
它们必须是**加法**。直写 `group.intel_rule` 会让 `test_ai_intel_settings_rearm.py` 的六条用例
在 `_wire_intel` 的 fail-open 里被静默吞成「装配失败」——症状离原因很远（实施期真的复现了
一次：`'types.SimpleNamespace' object has no attribute 'intel_rule'`）。

### I-9a（★ 自查发现的真缺陷）四个重置点只清空集合是不够的——**下一轮必须重铺全量**

§3.2 B-2 把四个重置点定义成「把 `_intel_seen_ids` 清空」。实施期自查发现这不足以兑现用例
`B13` 与人工验收第 6 条要的那件事：**清空之后那一轮如果仍然落在 `repeat` 档**，
`plan_disclosure` 会把全部条目都当成新条目、然后用 `repeat_max_chars`（默认 240，只有纵向
预算的一半）去裁——净效果是「上下文刚被摘要吃掉、情报段却只铺了半页」，而那正是重置本身
要解决的问题。页脚仍然诚实（说的是「另有 N 条因篇幅未展示」），所以这条缺陷**不会报错，
也不会被任何一条只断言集合被清空的用例抓到**。

**实施采用的修法**：四个重置点置一面一次性旗 `_intel_force_full`，由 `_intel_payload`
消费并把本轮强制成 `full`（消费即清）。streak 上界那一格本来就在函数内就地强制 `full`，
故四条路径口径一致。用例 `B13` 相应升级为断言 `tier == "full"` ∧ `same == 0` ∧
`show == [0]`（那一行真的渲染出来了），而不只是断言集合被清空。

### I-9b §六 #26 的「情报上下文」一行只能落在**历史详情**，落不到实时页

`aiStats.intelDisclosure` 是 `_build_run_metrics` 的产物（落库 JSON 列），而
`AiTradeStatsPanel` 的**实时**那一页读的是成员级 `status` 快照——那条通路上根本没有这个键。
故这一行落在 `components/Agent/aiTradeStats/AiTradeStatsDetail.tsx`（它是 `AiTradeStatsPanel`
历史页的详情组件，也是全仓唯一读 `metrics.aiStats` 的地方）。**键缺席即整行不渲染**这条约束
一个字节不改。

### I-10 三处 `IntelSettings` 前端 fixture 是 `tsc` 强制的，新增五个键必须同批补齐

`IntelSettingsOut` 的五个新键在 TS 侧**刻意不加 `?`**（`tsc` 是这里唯一可靠的门禁），故
`components/Intel/IntelModal.test.tsx` / `components/Settings/IntelReaderSettings.test.tsx` /
`components/Settings/IntelSourceSettings.test.tsx` 三处 fixture 必须同批补齐，否则
`npx tsc --noEmit` 判红。这不是缺陷，是把它记下来供下一个改这张 wire 的人省一次排查。

### 放行条件 C-1…C-8 的逐条回执

| # | 回执 |
|---|---|
| C-1 | `D5` 与 `A4` 在实现接线**之前**写完并确认判红：`D5` 显式断言 `Settings().ai_trade_intel_expand_enabled is True` 与 `intel_limits_for(inst).expand_enabled is False` **同时成立**；`A4` 经一次真实 `rearm_group`（内部走 `identity_of`），把 `identity_of` 那一行注释掉即当场判红 |
| C-2 | `A7` 的断言是**正向**的：`session.note_intel_alert` 未被调用（`handed == []`）+ `alertMuted == 1`；总闸关那一格反向断言告警照常唤醒 |
| C-3 | **不放宽门禁**，迁了 4 处（2 处代码 + 2 处注释），落点与理由见 I-1 |
| C-4 | 见 I-2：`_build_group` 是唯一构造点，`resume_group` 里那一次会是死代码；`grep` 实际命中 7 次 |
| C-5 | `G-3` 的冻结口径已改写并**重新生成**（见 I-8），且新增一条正面证明那两个读侧键进不了 prompt |
| C-6 | 人工验收第 6、7 条**需要一次真实的长会话与一次不重启进程的面板改动**，实施节点无法代跑；离线侧已由 `B13`（摘要重置）/ `B14`（streak 上界）/ `D5`（不重启改面板）三条覆盖到它们能覆盖的部分，人工验收清单原样保留待跑 |
| C-7 | `.claude-index/config.md` 的阈值偏离登记与本轮代码**同一次提交**（本节所属的这次改动里） |
| C-8 | 三次「再下沉一层」全部记进 `.claude-index/config.md`，**没有放宽任何阈值**；`intel/runtime.py` 与 `intel/service.py` 最终实读 799 / 767 |

### 跑测回执（实施节点）

- 后端：`cd services/quant && python -u -m pytest -q tests/` ⇒ **5423 passed / 7 failed /
  1 skipped**（25:21）。**合入前基线是 5361 passed / 同样 7 failed**——增量 +62 全是本轮新增
  用例，红的条数一条没变。那 7 条红全部来自**未跟踪的在制文件**
  `tests/test_factor_library_expansion.py`（它断言 `len(FACTOR_LIBRARY) == 20` 而工作树里是
  13，与本轮零关系）；情报相关 661 条既有用例全绿；`ruff check app tests` 全绿。
- 前端：`cd apps/web && NODE_ENV=test npx vitest run` ⇒ **233 files / 2694 tests 全绿**；
  `npx tsc --noEmit` 全绿。
- 新增用例分布：后端 62 条（core 41 + io 21），前端 W1..W12 共 20 条（新建
  `AiTradeIntelFields.test.tsx` 8 + `IntelBindingStrip.test.tsx` 6 + 既有四个文件各补 1~3 条）。

---

## 终审在源码里修毕的缺陷（Final Review · R-1 … R-9）

下列每一条都在终审期**实读代码**发现并**当场在源码里修掉**（不留 TODO），另补了 5 条守卫用例
（后端 2 + 前端 3）。前五条是真缺陷，其中两条是 P0；后四条是与仓库既有纪律不一致的地方。

**两条 P0 共享同一个形状**：*一个数被算了出来，但读它的那一端在结构上永远到不了*。这与
本轮 spec 自己在 [P2-1] 里点名要避免的「有写点没读点」逐字同形——门禁只守它列进表里的键。

### R-1（P0）`repeat` 预算挤掉的新条目被记成「模型看过了」⇒ 它们**永远进不了 prompt**

`session._intel_payload` 把 `_intel_round_ids` 定成 `wire["itemIds"]` 的全体。但 `itemIds` 是
「**digest** 预算收进来的那些」，而 `plan.show` 才是「本轮真的渲染进 prompt 的那些」——
实施发现 `I-4` 新增的**第二层预算**（`repeat_max_chars`）同样会挤掉一部分**没见过**的条目，
它们此后每一轮都会被当作旧条目折叠（`same`），于是一条真实的高危情报可以永久地卡在
「进了 digest、却一次也没被模型看到」这个吸收态里，**而且不报任何错**。

评审 `P1-4` / 用例 `B4b` 守的正是这条纪律，但它只守到了 digest 那一层（`hidden` 掉的不进
`itemIds`）——I-4 把第二层预算加进来之后，同一条纪律在新的那一层上失守了。

**修法**：`_intel_round_ids` 改由 `plan.show` 反查 `itemIds`。`tier == "full"` 时 `show` 恒为
全体下标 ⇒ 与今天逐字节等价，零回归。守卫用例
`test_repeat_budget_dropouts_are_not_recorded_as_seen`（构造「预算只装得下 1 行、库里 3 行」，
断言下一轮第二条终于排进 `show`）。

### R-2（P0）`aiStats.intelDisclosure.alertMuted` 是一个永远到不了读点的写点

`alertMuted` 只在会话**未采信**情报时才可能非零（`manager._on_intel_alert` 的那处短路），
而未采信恰好使 `mode == "off"` ∧ `roundsInjected == 0`——那正是 `disclosure_stats` 返回
`None` 的两个条件。于是：后端记了数、`AiTradeIntelDisclosureStats` 声明了字段、
`AiTradeStatsDetail` 写了渲染分支，而那个分支**在任何输入下都不可能被执行**。

用例 `A7` 断言的是替身会话上的调用次数，`D4` 断言的是一份全零计数器——两条都恰好绕开了这一格。

**修法**：`alertMuted > 0` 成为「`mode == "off"` ⇒ 整键缺席」的**唯一**例外，前端据
`roundsInjected === 0` 只渲染那一句（其余读数在这一格全是 0，写出来就是会被当真的假读数）。
这也是用户为「关掉情报」这个选择唯一需要付的账：*这一次会话因此有 N 条高危告警没有唤醒决策*。
守卫用例 `test_muted_alerts_are_the_only_exception_to_the_mode_off_absence`（同时断言 D4 原样成立）。

### R-3（P1）分组级情报回执 `group_status().intel` 全链路**没有任何读点**

§4.2 的回执 → `manager._intel_receipt` → `AiTradeGroupRow.intel` → 评审 `P0-5` 特意补的
`groupStateFromStatus` 那一行 → `AiTradeGroupState.intel` → **然后就没有了**。全仓没有一个
组件读它，`legacy_fallback` / `ops_off` / `resolve_failed` 三个「服务端替我决定了什么」的码
在界面上一次都不会出现——而 §7.3 人工验收第 1 条要的恰恰是「分组卡上的情报行」。

`P0-5` 的立论（漏抄 ⇒ 披露在「刷新页面后重发现」这条路径上恒缺席）成立，但它守住的那条通路
末端是空的：抄到了，也仍然没人看得见。

**修法**：`lib/intel.ts` 新增唯一文案装配点 `intelReceiptText` +
`INTEL_RECEIPT_SOURCE_LABEL`，`AiTradePanel` 新增 `IntelReceiptStrip`（与 `FeeSourceStrip`
逐行同构：唯一装配点、只有服务端改写了用户选择时才带颜色、缺键即整段不渲染）。
守卫用例：`AiTradePanel.test.tsx` 两条 + `lib/intel.test.ts` 三条。

### R-4（P1）启动弹窗在「会话级开关被运维关闭」那一格说了一句**反话**

`AiTradeIntelFields` 把两把闸合并成一个 `offByOps`，文案是「市场情报研判已被运维关闭，
**本次会话不会把情报喂给 Agent**」。但两把闸的后果完全相反：

| 闸 | 后果 | 那句话 |
|---|---|---|
| `enabled=false`（情报层总闸） | 既不采集也不注入 | 对 |
| `sessionOptInEnabled=false`（轴 A 总闸） | 只是这个勾选框失效，喂不喂由 `AI_TRADE_INTEL_INJECT` 独裁（**默认开**） | **反话** |

用户会以为自己没开，而模型每一轮都在读情报——方向最坏的那一种假话。

**修法**：两格分开措辞，后一格把 `injectEnabled` 的**实际值**读出来写进句子里
（回执里本来就有这个键）。`data-testid` 与禁用行为一个字节不改，故 `W2` 原样成立。

### R-5（P1）`_expand_quotes` 在会话**未采信**情报时照发那一次 `IN` 查询

它的 docstring 写着「只在 L2 总闸开着、且库里真的有带引文的条目时才发——一次多余的查询都
没有」，但调用点排在 `inject_on` 判定**之前**：`inject_on` 为假时整份 digest 都会被丢弃，
那次查询是纯浪费。而本轮把会话级采信的默认值定成了**关闭**——那恰恰是最常见的一格，于是
每一次快照刷新（每轮结束 / 每一次 REST 写 / 每一次「标记已读」）都白查一次库。

**修法**：`inject_on` 提到开会话之前判，调用点按它短路；docstring 同步写上这一条。

### R-6（P2）`expand.py` 跨模块 import 私有名 `_BANNER`

`disclosure.relative_time` 的 docstring 里逐字写着这条纪律：「同包里跨模块 import 一个下划线
私有名，等于把它的签名变成公开契约却没有任何地方说明这件事」——它当年正是为此去掉的下划线。
新文件 `expand.py` 在同一个包里重犯了同一条。**修法**：`_BANNER` → `BANNER`（公开名 +
一句说明它为什么是公开的注释），两个调用点与那条用例同步改名。

### R-7（P2）`INTEL_SETTING_BRIDGE` 的新块插在 actionability 注释与**它自己的条目**之间

新增的三行被插在 `# ---- ai-trade-intel-actionability：三把总闸 + 三个告警旋钮` 这句注释与它
所描述的 `alertEnabled` 等条目**中间**，于是那句注释指向的成了别人的行。**修法**：新块整体
移到表尾（`conflictEnabled` 之后），注释与条目重新贴合。表是按特性分段追加的，顺序无语义。

### R-8（P2）`IntelCard` 的注释说编号「取既有 `digest.refs`」，实际是按下标现算

代码是 `digestItemIds.indexOf(item.id) + 1` 再补零，注释说的却是另一件事。两者**结论**相同
（后端的 `refs` 就是 `enumerate(picked, start=1)`，而 `digestItemIds` 正是那份 `picked` 的
同序快照），但一句说错了自己出处的注释会让下一个人以为改 `refs` 不影响这里。**修法**：注释
改写成实际的推导链，并补上第二条限制——引用编号总闸关时 prompt 里根本没有 `[Ixx]`，那时这个
chip 表示的是「在本轮情报段里的位次」。

### R-9（P2）三处杂项

1. `manager.py` 的 `AiTradeGroup` 末尾多出一个空行（三连空行）；
2. `intelApi.settings` 顶上并排着两段 JSDoc（旧的那句没删）——合并成一段；
3. `binding.build_binding` 里 `str(runtime.group_id) or None`：`group_id` 为假值时得到的是
   字符串 `"None"`（真值），不是 `None`。改成显式判空。

### 终审跑测回执

- 后端：`cd services/quant && python -u -m pytest -q tests/` ⇒ **5425 passed / 7 failed /
  1 skipped**（14:35）。实施节点回执是 5423 passed / 同样 7 failed，增量 **+2** 全是本节
  新增的守卫用例（R-1 / R-2 各一条），**红的条数与文件一条没变**——7 条全部来自未跟踪的
  在制文件 `tests/test_factor_library_expansion.py`（它断言 `len(FACTOR_LIBRARY) == 20`
  而工作树里是 13，与本轮零关系）。`ruff check app tests` 全绿；`-k intel` 725 条全绿。
- 前端：`cd apps/web && NODE_ENV=test npx vitest run` ⇒ **233 files / 2699 tests 全绿**
  （合入前 2694，增量 +5 全是本节新增的守卫用例）；`npx tsc --noEmit` 全绿。
