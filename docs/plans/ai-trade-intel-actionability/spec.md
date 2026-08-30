# AI 自动交易 · 情报可行动化（ai-trade-intel-actionability）

| 项 | 值 |
|---|---|
| 版本 | **v2**（评审后定稿：4 P0 / 10 P1 全部在正文改毕；见《评审记录》与文末《评审结论》） |
| 特性目录 | `docs/plans/ai-trade-intel-actionability/` |
| 承接 | `52f466e` 市场情报层 → `7e2d5be` 情报闭环 → `4b9c352` 情报证据接地 |
| 四条轴 | **A 及时**（情报告警 → 第九种唤醒原因）· **B 战绩**（引用结算与核查判定 → 收缩先验）· **C 去噪**（立场矛盾检测 → 注入标注 + 整理裁决）· **D 可解释**（动态时间线 / 告警台账 / 六键设置桥） |
| 影响面 | 后端新增 `ai_auto/intel/` 三个**纯核心**模块（`alert.py` 告警判定、`efficacy.py` 战绩与信誉、`conflict.py` 立场矛盾）+ `db/ai_intel_alert_repositories.py` + `api/schemas_intel_actionability.py`；**一张新表 `ai_intel_alerts`，零 ALTER 补列**（与前两轮的 12 列 / 8 列刻意不同，理由见 §5.1）；`gate.py` / `disclosure.py` / `service.py` / `runtime.py` / `taxonomy.py` / `prompts.py` 各处薄改；`ai_auto` 侧改 `wake_reason.py` / `wake_text.py` / `wake_runtime.py` / `session.py` / `manager.py` / `tactical_context.py` / `experience/taxonomy.py`。**`ai_auto/prompts.py` 一个字节不改**（§十 非目标 6 的债务转记）。前端新增 4 组件 + 1 lib，改 14 个，**零新增页签**（§3.4.1 的 HCI 裁决） |
| 关键危险 | 「第九种唤醒原因」在本仓库要同时落进 **九组手抄注册表（共十张表）**（后端五组：`WAKE_KINDS` / `conditions` / `wake_text` 的两张表 / `_FULL_TIER_KINDS` / 经验图标签；前端四组），漏一处**不报错**，症状是界面上一个裸 slug、或经验图里那一类**根本选不到**（E-9/E-10）。§3.1.5 把九处逐个钉死，G-9 做并集断言 |
| 约束遵守 | 本文档只写设计，不写实现代码；只创建 `docs/plans/ai-trade-intel-actionability/spec.md`；不 `git commit` |

---

## 评审记录（v1 → v2）

> 评审时间 **2026-08-30**，工作树 `4b9c352` 之后。评审方法：**不采信文中任何一条转述**——
> §二 的 E-1…E-26 全部在源码里重新实读，§十二 的每一个行数重新 `wc -l`，每一处「某函数今天是
> 这样写的」重新打开那一行。**成立 24 条**（E-1/E-2/E-3/E-4/E-5/E-6/E-7/E-8/E-9/E-11/E-12/
> E-13/E-14/E-15/E-16/E-17/E-18/E-19/E-20/E-21/E-22/E-24/E-25/E-26 逐条复验通过，含
> `INTEL_SETTING_BRIDGE` 确为 17 行、`RoundOutcome` 确为 9 字段 frozen、`_ALSO_TRUE_LABEL`
> 确在 `wake_text.py:16`、`_WAKE_BODY` 确在 `:171`、四个 `score_item` 调用点的位置与传参
> 逐字相符、`_shutdown_steps` 的 `ai_trade_intel` 确为第 7 步、§十二 的 16 个行数**无一漂移**）；
> **更正 2 条**：E-10 的第二处镜像真名是 `WAKE_LABELS` 而不是 `WAKE_KIND` 且它被声明为
> `Record<WakeKind, …>`（tsc 强制、并非静默），E-23 的 `.env.example` 在**仓库根**而不是
> `services/quant/`。
>
> 共 **4 P0 / 10 P1 / 12 P2**。P0 与 P1 已全部在正文改毕（本节只留判据与取证，修法写在各自章节
> 里，**不在本节重复**）；P2 中 11 条一并改毕，1 条（P2-10 的既有欠账）如实登记。

### P0（不修则本轮承诺的能力在默认路径上不成立）

| 编号 | 判据与取证 | 后果 | 正文修法落点 |
|---|---|---|---|
| **P0-1** | **轴 B 的两个消费点拿不到 `Efficacy`。** `run_curate_round` 在 `service.py:378-411` 里调 `gate.review_actions` 与 `cleanup.plan_cleanup`，它手上只有 `deps`；而 §3.2.4 把 `Efficacy` 的求解放在 `build_snapshot`（`service.py:592`）并缓存进 `IntelRuntime._efficacy`——`IntelDeps`（`service.py:140-159`）**没有任何指向 runtime 的引用**，整理轮拿不到那份缓存 | 消费点 #2（`promote_weak_record`）与 #3（溢出淘汰打分）在默认路径上 `eff=None` ⇒ 判据整段不参与 ⇒ 轴 B 四个消费点里只有两个真的活着。而 §7.1 原本**没有任何一条闸**覆盖 #2/#3 ⇒ 静默空转。这是黑板 `CL-1` / `EG-3`「写点有、读点无」的第 N 次同形 | §3.2.4 新增「整理轮自解一次」段；§3.2.3 表格 #2/#3 的「关闭时回退」列改写；新增 **G-28** |
| **P0-2** | **`_review_promote` 的入参里没有 `reason`。** 实读 `gate.py:644`：签名是 `(item, to_tier, *, limits, now)`，`reason` 只存在于 `review_actions` 循环里的 `entry` dict。§3.2.3 #2 却把「`reason` 归一后长度 ≥ N」这条判据放进 `_review_promote` | 正是本仓库五次 P0 的同一形状——**新判据取自不携带该信息的入参**。而且再塞两个 keyword 形参会到 6 个，越 CLAUDE.md「形参 ≤5」 | §3.2.3 表格 #2 改判据落点为 `review_actions` 的 promote 分支；§6.1 #9 同步 |
| **P0-3** | **唤醒句断言「该情报已在下方情报段中列出」，而它在默认配置下大概率不在。** 两条独立路径：① `disclosure._TIER_RANK = {"long":0,"mid":1,"ultra":2}`（`disclosure.py:39`）⇒ 注入排序 **long 最前、ultra 最后**；`prompt_max_chars` 默认 **1200**（`config.py:1015`）。单行由 `render_line` 定稿（`disclosure.py:61-68`）：`[I01] · 超短｜风险事件·偏空·高：<标题> —— <摘要>（16 分钟前｜3 源｜置信 0.78）`，固定部分 ≈ 44 字符，而 `headline_max_chars = 40`（`taxonomy.py:242`）、`summary_max_chars` 默认 **160**（`config.py:1001`）⇒ **单行上界 ≈ 244 字符、典型 ≈ 190** ⇒ **一次注入只装得下 ~5–6 行**；而 `max_active` 默认 long=16 / mid=24（`config.py:1006-1008`）。**长期层只要攒到 6 条 active，ultra 条目就再也进不了 prompt**——而告警条目恒是刚采到的 ultra。② `ai_trade_intel_inject=False`（`INTEL_SETTING_BRIDGE` 的 `injectEnabled`）时 `build_snapshot` 直接把 digest 置空（`service.py:608-612`），情报段整段不存在 | 一次**真实资金**决策轮的 prompt 首行说「详见下方情报段」，而下方没有那一条。模型要么凭标题裸判、要么把这句话当成幻觉忽略——把一句假话逐字写进喂给模型的 prompt，本仓库已为此栽过（黑板 `v1:I-9`） | §3.1.3 的 `AlertHandoff` 加 `in_digest`；§4.3 唤醒句改**两态**（内联态把摘要就地带进唤醒句）；§5.3 形状表；G-7 扩两条 + 新增 **G-29** |
| **P0-4** | **`keep_both` 无条件进 `CURATE_ACTIONS`。** 实读 `prompts.py:98-125`：`action.enum` 今天是硬写的五值；§5.2 要求改为从 `taxonomy.CURATE_ACTIONS` 生成，而该元组把 `keep_both` 写成第六个元素、**不受 `conflict_enabled` 约束** | §3.0 第 2 条自我约束（三把总闸任一关闭 ⇒ 产物与 `4b9c352` 逐字节相同）被本文自己破坏：矛盾开关关掉时工具 schema 仍多一个枚举值、system prompt 仍多一条裁决纪律，模型可以发出一个必然被 `keep_both_bad_pair` 驳回的动作、白占一个 `curate_max_actions` 名额。原 G-3 只冻结 `build_digest`，看不见这件事 | §3.3.3 的 schema 段改条件化；§5.2 的 `CURATE_ACTIONS` 注释改写；G-3 扩为三件产物一起 `sha256`；新增 **G-30** |

### P1

| 编号 | 判据与取证 | 正文修法落点 |
|---|---|---|
| **P1-1** | **`.env.example` 在仓库根，不在 `services/quant/`。** `find . -name .env.example` 只命中 `./.env.example`；`services/quant/` 下只有 `.env`。冻结门禁自己写死了路径：`root = Path(__file__).resolve().parents[3]` 然后 `root / ".env.example"`（`tests/test_ai_intel_wiring.py:447`）。E-23 与 §6.1 #27 都写成了 `services/quant/.env.example`——**第 1 轮评审已就同一个路径纠正过一次**，本轮复发 | E-23 与 §6.1 #27 改为仓库根 |
| **P1-2** | **`limits_from_settings` 住在 `taxonomy.py:316` 而不是 `service.py`**（`IntelDeps.__post_init__` 在 `service.py:161-163` 调它）。§6.1 #12 把它记在 `service.py`；更实的问题是 `taxonomy.py` 的模块 docstring 明写「**不 import 同包的任何有状态模块**」，而 `alert.py` 必须 import `taxonomy.IMPACT_W` ⇒ 若 taxonomy 反向 import `alert.AlertLimits` 就是**循环 import** | 三组阈值改为**并进 `IntelLimits`**（§3.1.1 / §3.2.2 / §3.3.1 的 `*Limits` 定义与 §6.1 #8/#12/#26 全部改写） |
| **P1-3** | **`gate ↔ efficacy` 会成环。** §3.2.2 的 `weight_for(item, eff, *, limits)` 的 `item` 是 `gate.IntelItem`，而 `gate.score_item` 要在**运行期**调 `efficacy.weight_for` | §3.2.2 改成 `efficacy.py` **不 import `gate`**：`solve()` 收纯行 dict、`weight_for(category: str, …)` 只收类别串 |
| **P1-4** | **`merges` 无法投影成 `AlertCandidate`。** 实读 `gate.py:125-140`：`MergeOp` 只有 `item_id / dedup_key / sources / domain_count / expires_ts / revive / updated_ts / prior_quote*`——**没有** headline / category / stance / impact / confidence / event_ts / verdict / is_scheduled / tier。而「同一条风险情报被第二、第三个域名再次报道」恰恰只走 merges 分支，也恰恰是 `min_domains>=2` 最该放行的那一类 | §3.1.1 明确 merges 候选由 `run_collect_round` 手上的 `existing[dedup_key]`（`service.py:247`）与 `merge.domain_count` 拼出；新增 **G-31** |
| **P1-5** | **告警判定必须落在 `_persist_collect` 的写事务里**，而 §3.1.2 写成「`run_collect_round` 在写完条目之后」。`item_id` 只有 `_persist_collect` 里 `upsert_intel_items` 的返回值知道（`service.py:337-343`，注释明写「必须接住返回值」），冷却与日配额的两条 SELECT 也需要一个会话 | §3.1.2 写点 1 的落点改为 `_persist_collect` 的同一个 `async with` 与同一次 `commit` |
| **P1-6** | **`alert_pending_ttl_s` 的启动期校验器永远不可能红。** §十一 给它的约束是 `ge=30`，而校验器判的是「≥ 2 个盯市 tick = 10s」——`ge=30` 已经覆盖住了。同时 `live_auto_poll_interval_s` 是**无上界**的普通 `int = 5`（`config.py:125`），用户把它调到 60 时真正的坏配置反而拦不住。上一轮终审刚修过一条同形的「永远走不到的 >99 校验器」 | §十一 校验器 2 改为**跨字段**：`>= 2 * live_auto_poll_interval_s` |
| **P1-7** | **`metrics_json.aiStats.wakeScheduler` 在前端没有任何读点。** 全仓 `grep` 在 `apps/web` 只命中 `lib/aiTradeHistoryTypes.ts:124` 这一处**类型声明**；唯一展示点 `AiTradeStatsLive.describeWakeScheduler`（:335-346）读的是 `session?.signalFlipWakes`（实时行=镜像 a）。后端 `session.py:3138` 已把 `self._wake.stats()` **整体**写进 metrics ⇒ 新增两个计数天然落库，与 TS 类型无关。故 §3.4.4 (c) 的「缺它 ⇒ 会话结束后永久丢失」不成立，照原文实施只会新增第二处「写点有读点无」，且 G-17 会变成一条断言类型声明的空闸 | §3.4.4 表格 (c) 行改写；G-17 改为断言**后端 metrics_json**；欠账登记进 §十二 |
| **P1-8** | **轴 C 的重排与 `build_digest` 的 `break` 语义未被裁决。** 预算循环是 `if used + len(line) + 1 > budget: break`（`disclosure.py:117-119`）——**不是 `continue`**；且排序首键是 `_TIER_RANK`。把一个 long 层的 B 项移到某个 ultra 层 A 项旁边，既打乱层分组，也可能因为一条更长的行**提前 break** 而让整个 digest 变短。§3.3.2 只证明了「不超预算」，Q4 只讨论了排序质量，二者都没覆盖这条 | §3.3.2 改为「**只在同一 `_TIER_RANK` 分块内部重排**」；G-20 相应改写；R-7 登记代价 |
| **P1-9** | **三把总闸到不了纯核心。** `build_digest` 的签名是 `(items, *, limits, now, last_collect_ts, cite)`（`disclosure.py:80`），而「纯核心从不自己读 `Settings`」是 `taxonomy.py:317` 的成文纪律。§3.4.2 只把三把总闸放进了 `INTEL_SETTING_BRIDGE`，从没说它们怎么到达 `build_digest` / `score_item` / `find_conflicts` | 与 P1-2 合并：三把总闸走 `IntelLimits` 新字段；`build_digest` 多收 `conflicts=()`（默认空 ⇒ 逐字节回退，照抄 `limits=None` 范式） |
| **P1-10** | **「被覆盖的告警立刻结账」需要一次写库，而 `note_intel_alert` 被要求全同步。** `session.py:65` 今天 import 的是 `from .intel import IntelAttribution, intel_digest_for, intel_summary_for`，没有 runtime | §3.1.3 明确 `note_alert_outcome` 是 `intel/runtime.py` 的**模块级函数**（内部 `scheduler.schedule(...)`，照抄 `runtime.invalidate` 的既有形状），因此可在同步上下文里调；§6.1 #14/#16 同步 |

### P2（11 条已在正文改毕，1 条登记为欠账）

| 编号 | 内容 |
|---|---|
| **P2-1** | `apps/web/lib/aiTrade.ts` 的表真名是 **`WAKE_LABELS`**（:1148）不是 `WAKE_KIND`，且它被声明为 `Record<WakeKind, AiTradeTriggerView>` 并带一句「后端新增 kind 时 tsc 强制要求补齐，不会静默漏一个展示点」——**这一处不是静默的**。§3.1.5 #7 与 §6.2 #43 已改正 |
| **P2-2** | 注册表 #5/#8 已被**既有**门禁 `apps/web/lib/experienceLabels.source.test.ts` 双向冻结（它解析 `experience/taxonomy.py` 的 `VALUE_LABEL_ZH`，含那行笔误补丁，并反向断言「前端没有多出后端不认识的取值」）。真正静默的只剩 **#9 `AXIS_VALUES.wake`**。G-9 因此改为**扩这条既有用例**而不是新建第二条判据 |
| **P2-3** | `_ALSO_TRUE_LABEL` 漏登记的症状**不是「裸 slug」而是静默省略**：`[_ALSO_TRUE_LABEL[name] for name in … if name in _ALSO_TRUE_LABEL]`（`wake_text.py:33-34`）会把不认识的键直接过滤掉。证据消失比显示一个 slug 更难发现。§3.1.5 #3 已改正 |
| **P2-4** | 「轮次」页签今天的**实际标签是「整理记录」**（`IntelModal.tsx:211`），`TabKey` 里的值是 `"rounds"`。§3.4.1 / §6.2 #35 的措辞已按实际标签改写 |
| **P2-5** | §5.2 说 `CODE_LABEL` 在「改名后的 `IntelActivityTab.tsx`」里，而 §6.2 #36 又说 `IntelRoundsTab.tsx` **不删**且继续渲染轮次——两处矛盾。已定死：`CODE_LABEL` 留在 `IntelRoundsTab.tsx` 并 `export`，`IntelActivityTab` import 它（单一真源） |
| **P2-6** | §3.1.1 用 `taxonomy.IMPACT_W`（`{low:0.0, medium:0.5, high:1.0}`，`taxonomy.py:47`）是对的，但本仓库**有两张影响力权重表**：`gate.SCORE_IMPACT_W` 是 `{low:0.34, medium:0.67, high:1.0}`（`gate.py:44`）。已在正文写明取的是哪一张、以及为什么不能混用 |
| **P2-7** | §3.1.4 说把情报唤醒排在 `_maybe_signal_flip_wake` 之前「能省掉那一次白跑的预演」——**不成立**：`_maybe_signal_flip_wake` 先 `await self._preview.run(...)` 再判 `self._deciding`（`session.py:1793-1798`），预演照跑。排序本身仍然正确（真正的理由是 E-6 与「外生离散事件的时延」），但那句话若照原文写进源码注释就是一句不实的话。已改写 |
| **P2-8** | `resolve_require_quote` 应与 `taxonomy.resolve_quote_mode`（`taxonomy.py:295`）**并列**放在 `taxonomy.py`——「生效值读点」在本仓库已经有唯一归属地，再在 `alert.py` 开第二处只会让下一个人两边找。已改 |
| **P2-9** | §4.1 #1 说「沿用 `/citations` 的既有裁决」，但 `/intel/{instId}/citations` 今天**挂着** `response_model=IntelCitationListOut`（`api/ai_intel.py:529`）。已写明新端点 `/alerts` **不挂** `response_model`（沿用模块 docstring 第一条纪律），并说明这与 `/citations` 的差别是刻意的 |
| **P2-10** | §十二 的规模表漏登 `IntelSourceSettings.tsx`（实测 383 → ≈460）与 `IntelCard.tsx`（306 → ≈320），二者都在 §6.2 的改动清单里。两者都不越线，已补进登记表 |
| **P2-11** | 轮级抑制（`daily_cap` / `cooldown`）时 `AlertDecision.candidate` 为 `None` ⇒ 台账行 `item_id=0` / `headline=""` ⇒ 动态页签只能说「未告警 · 日配额已满」而说不出**被压下去的是哪一条**。已改为：轮级抑制时仍把最高 `severity` 的候选投影进 `candidate` 并落台账（`fired=False` 不变），代价为零 |
| **P2-12** | G-26 原文断言「`main._shutdown_steps()` 的步骤表**逐字节**未变」——而该函数的 docstring 明写「本表当前 14 步……这里**不再写死一个数字**」（`main.py:89-92`），注释本身是会动的。已改为断言「表内不新增步骤，且 `ai_trade_intel` 仍是第 7 项」 |


---

## 〇、本轮定位：为什么是「可行动化」

三轮之后，标的市场情报层已经是完备的：它能**采**（52f466e：独立 Agent、后端检索、六道服务端闸、三层库、自主整理）、能**引**（7e2d5be：决策 Agent 可指名引用、往返净额回写、无交易工具的核查 Agent 可证伪、用户可拉黑信源）、能**证**（4b9c352：后端第一方抓正文、逐字引文闸、证据抽屉、排期事件的事后兑现）。用户提出的每一条功能要求都已经在跑。

但这三轮共同留下了**同一类天花板**：**情报被采到、被证实、被存好之后，什么时候真正改变一次决策，是不可知、不可测、也不可改进的。**

- **不可知**：一条「交易所热钱包异常出金」的高影响风险情报，在 10:03 被采集轮收进库、通过全部六道闸、拿到两条逐字引文。它下一次进入模型视野的时刻，取决于墙钟唤醒周期（`ai_trade_wake_interval_min`）——`trend_hold` 档的用户把它设成 0，于是这条情报要等到**下一根 4H K 线收线**。情报层的全部价值密度都被这段延迟稀释掉了。
- **不可测**：`ai_intel_citations` 从 7e2d5be 起就在逐笔记录「哪条情报被引用为入场依据、这笔往返净赚多少」，核查轮从同一轮起就在逐条记录「哪条被证实、哪条被证伪」。这两条证据流今天**只被显示，不被使用**：影响力页签把它们画成表格，然后就没有下文了。用户看到 `coindesk.com` 与某个内容农场并排列在信源页签上，**手里没有任何数据**可以决定该拉黑哪一个——而拉黑功能是上一轮刚做好的。
- **不可改进**：库里可以同时躺着「ETF 资金连续第 5 日净流入」（bullish/high）与「ETF 单日净流出创年内新高」（bearish/high）两条都 active、都未被证伪、都会被注入的情报。今天的注入端把它们各自当成独立证据平铺给模型，**不告诉模型它们互相矛盾**；整理轮也从不被要求裁决这种矛盾。用户要的「对噪声或无效的短线情报进行整理或清理」，在「两条都不是噪声、但至少一条是错的」这个最需要整理的场景上是空白的。

本轮把这三件事补齐，并且**只补这三件事**：让高危情报**当场**唤醒一次决策（轴 A）；让已经攒了两轮的战绩与判定**回流**成排序与举证责任（轴 B）；让互相矛盾的情报在注入端**成对出现**、在整理轮**必须被裁决**（轴 C）；再把这三件事各自的过程**摊到一条可审计的时间线上**（轴 D）。

**本轮是收口轮**，因此有一条额外的自我约束：**不开新战线**。跨标的情报关联、多检索源扇出、回测车道情报重放、无头浏览器——四条前三轮反复画过的红线一条都不碰（§十）。

---

## 一、概述（Overview）

**这是什么。** 本轮给已有的标的市场情报层加三条闭合边，把它从「一个持续产出、被动等待被读取的知识库」变成「一个会在关键时刻主动出声、会根据自己历史准确率调整音量、并且会承认自己内部有矛盾的顾问」。

**轴 A（及时）** 新增一条**确定性**的告警判定：采集轮每收进一批新情报，纯核心 `intel/alert.py` 按一组固定判据（影响力 = high、类别 ∈ {风险, 宏观, 项目}、事件新鲜度、逐字引文在位、独立域名数、置信度下限）挑出至多一条「值得当场问一次 AI」的情报，写一行**告警台账**，并把它作为一个**待处理告警**交给该标的的交易会话。会话在自己的盯市 tick 上认领决策槽位、跑一轮标准决策——**走完全部既有门禁，一道不绕**——并把这一轮的唤醒原因如实标注成第九种 `intel_alert`。判定**不是**第二次 LLM 调用：真实资金路径上的唤醒判据必须是确定性的、可回放的、可在离线用例里逐条冻结的。全部抑制理由（冷却中 / 日配额满 / 无引文 / 会话不在运行 / 撞上在飞轮）都**写进同一行台账**，因此「今天为什么一次都没唤醒」永远有答案。

**轴 B（战绩）** 把两条已经在产出、但只被显示的证据流回流进系统：**引用结算流**（哪一类情报被采信为入场依据之后真的赚到钱）与**核查判定流**（哪一类情报、哪一个信源域名，被独立的核查 Agent 证实还是证伪）。纯核心 `intel/efficacy.py` 把它们收敛成两组带**层级收缩**的先验——按类别的战绩先验、按域名的信誉先验——样本不足时**恒等回退到中性**，并且权重被硬性封顶。四个消费点：注入排序（`score_item` 的第二个加分项）、整理轮的举证责任（一个历史战绩为负的类别，升长期要写更多理由）、溢出淘汰打分、以及信源页签上一张**用户终于可以据以决定拉黑谁**的表。**它永远不是门禁**：先验只改变顺序与举证责任，绝不改变任何一条情报能不能进库、能不能被注入。

**轴 C（去噪）** 新增纯核心 `intel/conflict.py`：在同一标的的 active 库存里，找出「同类别 + 立场相反 + 主题高度重叠 + 事件时刻落在同一个层级窗内」的成对条目。注入端把这一对**排到相邻位置**并各自加一句 `（与 [I05] 相互矛盾）`；整理轮的用户 prompt 新增一段「未裁决的矛盾对」，服务端逐轮统计 `conflictsOpen` / `conflictsResolved` 并把久未裁决的对**置顶**到下一轮整理的最前面。**刻意不自动删除任何一方**：确定性层没有能力判断两条相反的报道里哪一条是对的，它唯一诚实的动作是**让矛盾对模型和用户都可见**。

**轴 D（可解释）** 把前三条轴的过程摊开：情报弹窗既有的「整理记录」页签（`TabKey` 值 `"rounds"`）升级为**「动态」时间线**（轮次 + 告警 + 唤醒结果按时间合流），影响力页签追加「战绩与信誉」两块，信源页签的每一行补上「证实 / 证伪 / 准确率」三列并可据此排序，卡片上出现矛盾徽标与「仅看冲突」筛选。六把新开关经 `INTEL_SETTING_BRIDGE` 逐键往返（本仓库已两次把安全开关做成假面板，见 E-15），并且**告警的准入判定只在服务端读一次**——会话侧永不复读那把开关，从物理上杜绝第三次假开关。

**为什么这个组合是对的。** 三条轴共享同一个形状：**把系统已经知道、但没有用起来的事实，变成一次可观测的行为**。轴 A 用的是「刚采到的这条情报有多重」（采集轮已经算过置信度、域名数、引文数），轴 B 用的是「过去这类情报的结局」（结算与判定已经逐行落库），轴 C 用的是「库存内部的相互关系」（条目已经全在内存里）。三条轴**都不需要新的外部数据、不需要新的网络调用、不需要新的 LLM 调用车道**，因此本轮的边际成本几乎只有代码本身——这正是一个收口轮该有的成本结构。

---

## 二、代码取证（本文所有判断的锚点）

> 下列锚点均于 **2026-08-30** 在当前工作树（`4b9c352` 之后）逐行实读。评审节点应逐条复验；不成立的锚点即为本设计的缺陷。

| 编号 | 事实 | 位置 |
|---|---|---|
| **E-1** | 情报包已有 21 个模块 / 5940 行；`score_item` 住在 `gate.py` 而不是 `disclosure.py`，签名 `(item, *, now, limits=None)`，**`limits=None` ⇒ 返回值逐字节回退**（closed-loop 评审 P1-1 的成文修法） | `ai_auto/intel/gate.py:183-213` |
| **E-2** | `build_digest` 是注入行的**唯一定稿点**；预算循环「装不下就停，绝不半截截断一条」，`refs` 键序与 `lines` 严格一致且无空洞；`cite=False` 时 `refs`/`citeEnabled` **整键缺席** | `ai_auto/intel/disclosure.py:80-131` |
| **E-3** | `WAKE_KINDS` 是**八值**元组，判定顺序即优先级；`classify_wake` 里的 `conditions` 元组与之逐值对应，首个命中即 `kind`，其余进 `alsoTrue` | `ai_auto/wake_reason.py:22-23, 135-147` |
| **E-4** | `plan_signal_flip` **有副作用**（判 confirmed 即锁存并计数），故其 docstring 明写「调用方在起不了轮的时刻必须**根本不调用**，而不是调用后丢弃返回值」 | `ai_auto/wake_runtime.py:302-333` |
| **E-5** | 盯市 tick 的最后两行是 `await self._maybe_signal_flip_wake()` 然后 `await self._maybe_interval_wake()`，且注释写明「翻转优先于墙钟到点」 | `ai_auto/session.py:1361-1362` |
| **E-6** | `_maybe_signal_flip_wake` 的早退判据是 `if result is None or self._deciding: return`——**`_deciding` 为真时它不消费任何锁存**，因此在它之前插入一条会认领槽位的新唤醒源是安全的 | `ai_auto/session.py:1788-1811` |
| **E-7** | `should_skip_wake` 只对 `heartbeat` 与 `position_watch` 两种 kind 返回 True，其余 kind 恒不跳过 | `ai_auto/wake_guard.py` 尾部 |
| **E-8** | 战术段升档集 `_FULL_TIER_KINDS` 是六值 frozenset；执行周期全量披露集 `_EXECUTION_FULL_KINDS` 只有两值，且注释明写「刻意不增」 | `ai_auto/tactical_context.py:42-45` |
| **E-9** | 经验图的取值中文标签表 `VALUE_LABEL_ZH` 里 wake 轴是**八值逐字对齐**，且紧跟着一行 `VALUE_LABEL_ZH["position_watch"] = "持仓盯市"`——那是一次笔误留下的**物理防线**，说明这张表历史上就漂移过 | `ai_auto/experience/taxonomy.py:107-110, 123` |
| **E-10** | 前端有**四处**镜像，但它们的**失效方式各不相同**（评审 P2-1 / P2-2 更正）：① `aiTradeTypes.ts:134-145` 的 `WakeKind` 联合类型（tsc）；② `aiTrade.ts:1148-1160` 的 **`WAKE_LABELS`**（真名不是 `WAKE_KIND`），它声明为 `Record<WakeKind, AiTradeTriggerView>` 且相邻注释明写「后端新增 kind 时 tsc 强制要求补齐，不会静默漏一个展示点」⇒ **这一处不是静默的**；③ `experience.ts:58-65` 的短标签表——它已被**既有**门禁 `lib/experienceLabels.source.test.ts` 与后端 `experience/taxonomy.py` 双向冻结（含反向的「前端没有多出后端不认识的取值」）⇒ **也不是静默的**；④ `experience.ts:92-93` 的 `AXIS_VALUES.wake` 硬编码数组——**只有这一处真的静默**（其相邻注释已经为 `exit` 轴写过一次「漏一个值 → 预演选择器里根本选不到，而 tsc / vitest 都不会红」） | `apps/web/lib/*` |
| **E-11** | 情报运行时回到会话的既有通道是 `manager._on_intel_summary(group_id, inst_id, summary)`：**同步回调、不 await**，内部 `intel_scheduler.schedule(member.session.push_intel(summary))` | `ai_auto/manager.py:1512-1520` |
| **E-12** | `IntelRuntime._after_round` 先 `refresh`（重算并缓存 digest + summary）再返回；`refresh` 内部最后一步才 `self._notify(...)` | `ai_auto/intel/runtime.py:186-196` |
| **E-13** | 调度 tick **一个 tick 只推进一个标的一轮**（`return`），且核查轮排在整理轮之前 | `ai_auto/intel/scheduler.py:_tick` |
| **E-14** | `RoundOutcome` 是 `frozen=True` 的九字段 dataclass，`notify` 与 REST 202 都读它 | `ai_auto/intel/service.py:196-208` |
| **E-15** | `INTEL_SETTING_BRIDGE` 现有 **17 行**；其上方注释明写「不扩这张表 ⇒ 请求 200、面板上开关照动、`Settings` 上的值永远不变……把安全开关做成假开关是最不可接受的一种失败」；`resolve_intel_settings` 用 `model_copy(update=…)`，而 **`model_copy` 不跑 pydantic 校验**，故值域要在桥里再收一次 | `ai_auto/intel/service.py:700-760` |
| **E-16** | `_ensure_ai_intel_columns` 是第五个 patcher，已是**三表循环**（`ai_intel_items` / `ai_intel_watch` / `ai_intel_rounds`）；`create_all` 会建**缺失的整张表**但不给既有表补列 | `db/session.py:195-244` |
| **E-17** | `AiIntelItem` 上后加的 14 列（10 列 closed-loop + 4 列 evidence-grounding）全部带同一条警告：**绝不允许出现在 `_item_values` 里**，否则同一条情报被再次报道时战绩会被清零且不报错 | `db/models.py:928-960` |
| **E-18** | `AiIntelCitation` 是 append-only 事件流表 + 一次结算 UPDATE，带 `ix_ai_intel_cite_scope(inst_id, cited_ts)` 索引；`outcome ∈ win|loss|flat|void`，`role ∈ entry|exit|hold` | `db/models.py:963-1007` |
| **E-19** | `api/ai_intel.py` 模块 docstring 第 9 行写「读端点一律不挂 `response_model`」，**但 `/intel/{instId}/impact` 挂了 `IntelImpactOut`**——往该端点加新键必须同步扩模型，否则被静默吞掉 | `api/ai_intel.py:9, 517` |
| **E-20** | 四个 `/intel/feedback*` 与 `/intel/settings/health`、`/intel/documents/health` 全部被移到 `read_intel` **之前**：FastAPI 按注册顺序匹配，静态段追加在 `/{instId}` 之后会被静默劫持（症状是 500 不是 404） | `api/ai_intel.py:99-238`（黑板 `I-1`） |
| **E-21** | `IntelModal.tsx` 已 **553 行 / 7 个页签**（`TabKey = IntelTier \| "impact" \| "sources" \| "archived" \| "rounds"`） | `apps/web/components/Intel/IntelModal.tsx:51-53` |
| **E-22** | 情报按钮**必须是 `AiTradeMemberRow` 的兄弟节点**（行本体是 `<button>`，按钮不能嵌套按钮），该约束由源码扫描用例冻结 | `apps/web/components/Chart/AiTradeIntelButton.tsx` 模块注释 + `aiTradePanelNoNestedButton.source.test.ts` |
| **E-23** | `config.py` 现有 **64** 项 `ai_trade_intel_*`；`test_ai_intel_wiring.test_every_intel_setting_is_registered_in_env_example` 对 `.env.example` 与 `Settings` 的该家族做**动态双向集合比对**（不是固定条数），是上一轮终审新增的（该文件历史上**两次静默漏登记**）。⚠️ **`.env.example` 在仓库根**（`M:/takoAI/AragonTrading/.env.example`），**不在 `services/quant/`**——该目录下只有 `.env`；门禁自己写死了 `root = Path(__file__).resolve().parents[3]` 再 `root / ".env.example"`（`tests/test_ai_intel_wiring.py:447`）。第 1 轮评审曾就同一个路径纠正过一次，本文 v1 复发（评审 P1-1） | `services/quant/app/config.py:976-1080`；仓库根 `.env.example` |
| **E-24** | `taxonomy.REJECT_CODES` 与前端 `IntelRoundsTab.tsx` 的 `CODE_LABEL` 必须**并集对齐**，漏登记不报错、症状是用户看到一个裸码 | `ai_auto/intel/taxonomy.py:87-105` 的警告块 |
| **E-25** | `plan_cleanup` 与 `CleanupPlan` 已在上一轮实施期外移到 `intel/cleanup.py`（`gate.py` 曾到 843 行、`service.py` 曾到 912 行，均越 CLAUDE.md 的 800 上限，修法是纯搬运、阈值未放宽） | `ai_auto/intel/cleanup.py`（黑板 `EG-I2`） |
| **E-26** | `_maybe_interval_wake` 的第一道判据是 `self.wake_interval_s <= 0 → return`；`_maybe_signal_flip_wake` **没有**这道判据 —— 即「墙钟唤醒关掉」不等于「所有唤醒源关掉」，这是本轮轴 A 得以服务 `trend_hold` 用户的前提 | `ai_auto/session.py:1378, 1788-1793` |

---

## 三、技术设计

### 3.0 三条不可越过的方向

1. **确定性优先于聪明。** 轴 A 的告警判定、轴 B 的先验求解、轴 C 的矛盾检测**全部是纯函数**：无 IO、无网络、无 LLM、无时钟（`now` 一律由调用方传入）。理由不是洁癖——唤醒一次决策是会花真钱的动作，它的判据必须能在一个离线用例里被逐条冻结、被回放、被反证。**本轮不新增任何 LLM 调用车道。**
2. **加法，且可逐字节回退。** 三把总闸（`alert_enabled` / `efficacy_enabled` / `conflict_enabled`）任一关闭时，对应链路的产物必须与 `4b9c352` **逐字节相同**：`score_item` 的第二个加分项在关闭时不参与运算（沿用 `limits=None` 那条既有范式）、`build_digest` 的行体一个字节不变、`classify_wake` 的 wire 无 `intel` 键。三条冻结用例（G-1/G-2/G-3）用 `sha256` 比对而不是眼看。
   ⚠️ **「产物」不止 digest（评审 P0-4）**：轴 C 关闭时，`build_curate_tool_schema` 的 `action` 枚举、整理 system prompt 的裁决纪律段、整理 user prompt 的矛盾段**三者也必须一起缺席**。v1 把 `keep_both` 无条件写进 `taxonomy.CURATE_ACTIONS`，等于让这条自我约束在轴 C 上被本文自己破坏，而原 G-3 只看 `build_digest`、看不见它。G-3 因此扩为**四件产物一起 sha256**。
   ⚠️ **三把总闸怎么到达纯核心（评审 P1-9）**：纯核心从不自己读 `Settings`（`taxonomy.py:317` 的成文纪律），故三把总闸与本轮全部 14 项阈值**一律并进 `IntelLimits`**（`taxonomy.py:235` 的既有聚合结构体），由**唯一**的配置读点 `taxonomy.limits_from_settings` 组装。**不新建第二个 `*Limits` 家族**——`alert.py` 要 import `taxonomy.IMPACT_W`，若 taxonomy 反向 import `alert.AlertLimits` 就是一次循环 import（评审 P1-2）。
3. **绝不新建后台任务、绝不新建循环、绝不新建关停步骤。** 告警的写库回写走 `intel_scheduler.schedule(...)` 这个既有 tracked 任务集（它已经被 `_shutdown_steps` 第 7 步 drain）；矛盾检测与先验求解跑在 `build_snapshot` 这个**唯一查库点**里。`main._shutdown_steps()` 一个字节不改——上一轮的实施记录已经把它列为「冻结注册表」。

### 3.1 轴 A · 情报告警与第九种唤醒原因

#### 3.1.1 判定纯核心 `ai_auto/intel/alert.py`

> **评审 P1-2 / P1-9 定稿**：下面这些字段**不是**一个新的 `AlertLimits` 类，而是**追加进
> `taxonomy.IntelLimits`**（`taxonomy.py:235`，本轮从 30 个字段涨到 44 个），由既有的唯一配置
> 读点 `taxonomy.limits_from_settings`（`taxonomy.py:316`）组装，全部带默认值 ⇒ 既有构造零改动。
> 理由有两条，缺一不可：① `limits_from_settings` 实读住在 **`taxonomy.py`** 而不是 `service.py`
> （v1 §6.1 #12 记错了文件）；② `taxonomy.py` 的模块 docstring 明写「不 import 同包的任何有状态
> 模块」，而 `alert.py` 必须 import `taxonomy.IMPACT_W` —— 反向 import 一个 `alert.AlertLimits`
> 就是循环 import。三个纯核心模块因此**只 import `taxonomy`，不被 `taxonomy` import**。
> 下文仍写作 `limits.alert_*` 以保持可读性；`AlertLimits` 这个名字**不进代码**。

```python
# ---- taxonomy.IntelLimits 追加的告警字段（评审 P1-2：不新建第二个 Limits 家族）----
    alert_enabled: bool = False
    alert_min_impact: str = "high"           # "medium" | "high"
    alert_categories: frozenset[str] = frozenset({"risk", "macro", "project"})
    alert_require_quote: bool = True         # ★ 见 §3.1.1.1 的降级
    alert_min_domains: int = 2
    alert_min_confidence: float = 0.6
    alert_max_event_age_min: float = 180.0
    alert_cooldown_min: float = 20.0
    alert_daily_cap: int = 6
    alert_allow_scheduled: bool = False      # 未来事件（is_scheduled）默认不告警

@dataclass(frozen=True)
class AlertCandidate:
    """本轮 accepted / merges 条目的只读投影（**不含 URL、不含域名、不含正文**）。

    ★ 评审 P1-4：**两个分支的投影方式不同，`MergeOp` 一个内容字段都没有。**
    - `accepted`：直接投影 `gate.IntelItem`（`quoted` 已由 `_build_item` 按本轮已验证引文
      写好，见 `gate.py:499`——**不是** `False`）。
    - `merges`：`gate.MergeOp`（`gate.py:125-140`）只有 `item_id / dedup_key / sources /
      domain_count / expires_ts / revive / updated_ts / prior_quote*`，**没有** headline /
      category / stance / impact / confidence / event_ts / verdict / is_scheduled / tier。
      故投影必须由 `run_collect_round` 手上的 ``existing[merge.dedup_key]``
      （`service.py:247` 建的那张表）出内容字段，`domain_count` 取 ``merge.domain_count``
      （合并**之后**的新值），`captured_ts` 取 ``merge.updated_ts``，`quoted` 取
      ``merge.prior_quoted or bool(admitted.quotes.get(merge.dedup_key))``。
      **不能砍掉 merges 分支**：「同一条风险情报被第二、第三个域名再次报道」只走这条分支，
      而它恰恰是 `min_domains >= 2` 最该放行的那一类（G-31 冻结）。
    """
    item_id: int; headline: str; category: str; tier: str; stance: str
    impact: str; confidence: float; event_ts: int; captured_ts: int
    domain_count: int; quoted: bool; is_scheduled: bool; verdict: str

@dataclass(frozen=True)
class AlertDecision:
    """**恒有产物**：要么 fired，要么带一个抑制码。绝不返回 None。"""
    fired: bool
    # 评审 P2-11：**轮级抑制（daily_cap / cooldown）时也要带上最高 severity 的候选**——
    # 否则台账行是 `item_id=0` / `headline=""`，动态页签只能说「未告警 · 日配额已满」而
    # 说不出被压下去的是哪一条。只有「本轮一条候选都没有」（`no_candidate`）时才为 None。
    candidate: AlertCandidate | None
    severity: float                           # 0..1
    suppressed_code: str                      # "" 当且仅当 fired=True
    reasons: dict[str, float | int | bool]    # 判据明细，逐条落台账

def plan_alert(candidates: list[AlertCandidate], *, limits: IntelLimits, now: float,
               last_fired_ts: int, day_fired: int) -> AlertDecision: ...

def severity(candidate: AlertCandidate, *, now: float, limits: IntelLimits) -> float: ...
```

**判定顺序（逐条命中即抑制，抑制码写台账）**

| 序 | 判据 | 抑制码 | 理由 |
|---|---|---|---|
| 0 | `limits.alert_enabled` | `disabled` | 总闸 |
| 1 | 候选非空 | `no_candidate` | 本轮一条都没收进来（空轮也写台账 ⇒「今天没告警」有答案） |
| 2 | `day_fired < alert_daily_cap` | `daily_cap` | 成本与打扰上限，**优先于**逐条判据：配额满时不必逐条算（但仍按 P2-11 把最高分候选投影进 `AlertDecision.candidate`） |
| 3 | `now - last_fired_ts >= alert_cooldown_min*60` | `cooldown` | 同上 |
| 4 | `impact` 权重 ≥ `alert_min_impact` 权重 | `impact_below_bar` | 逐条 |
| 5 | `category ∈ limits.alert_categories` | `category_out` | 逐条 |
| 6 | `verdict != "refuted"` | `refuted` | 已证伪的情报绝不唤醒（与注入端同一条纪律） |
| 7 | `is_scheduled` 且不允许 | `scheduled_future` | 「今晚的 CPI」不该在中午打断一次决策；它自然会随墙钟轮进视野 |
| 8 | 事件年龄 ≤ `max_event_age_min` | `stale_event` | 用 `event_ts>0 ? event_ts : captured_ts` |
| 9 | `domain_count >= min_domains` | `few_domains` | 单一来源不足以打断决策 |
| 10 | `confidence >= min_confidence` | `low_confidence` | 服务端算出来的置信度，不是模型自报 |
| 11 | `alert_require_quote → quoted` | `no_quote` | 有逐字原文引证才够格打断 |

全部通过后按 `severity` 降序取**第一条**（一轮至多一条告警——两条同时唤醒只会撞在同一个 `_deciding` 槽位上，第二条必然被丢弃，那不如根本不发）。

```
severity = clamp01( 0.45*impact_w + 0.20*min(1, domain_count/3)
                  + 0.15*(1 if quoted else 0) + 0.20*confidence )
         * freshness,   freshness = clamp01(1 - age_min / max_event_age_min)
```

`impact_w` 复用 `taxonomy.IMPACT_W`（**不新建第二张权重表**，E-1 那条纪律的同款）。

> ⚠️ **本仓库有两张影响力权重表，取错一张不报错**（评审 P2-6）：
> `taxonomy.IMPACT_W = {"low": 0.0, "medium": 0.5, "high": 1.0}`（`taxonomy.py:47`）
> 与 `gate.SCORE_IMPACT_W = {"low": 0.34, "medium": 0.67, "high": 1.0}`（`gate.py:44`）。
> 判据 #4 与 `severity` **都取前者**：`IMPACT_W` 的 `low = 0.0` 让「低影响力恒不告警」成为
> 一条算术性质而不是一条要单独记住的规则；`SCORE_IMPACT_W` 服务的是注入排序，那里 `low`
> 必须仍有正分（一条低影响力情报仍该被看见，只是排在后面）。两张表**不合并**。

##### 3.1.1.1 `require_quote` 必须在**单一有效值读点**降级

`require_quote=True` 与 `quote_mode="off"`（或 `reader_enabled=False`）组合时，`quoted` 恒为 False ⇒ **一次告警都不会发生，且没有任何错误**。这与上一轮终审的 `EG-FINAL-2` 是同一个形状（strict + reader-off 从设置面板可达，因为它们是两把独立的 `app_settings` 键，而 `model_copy` 不跑校验器）。

**修法逐行照抄上一轮的成文修法**：新增 `taxonomy.resolve_require_quote(settings) -> bool`，在**取有效值的那一个点**（`limits_from_settings` 组装 `IntelLimits` 时）降级为 `False`，**存储的偏好一个字节不改**；设置卡上该复选框在 `quoteMode == "off" || !readerEnabled` 时置灰并写明原因。G-6 冻结之。

> ⚠️ **落点是 `taxonomy.py` 而不是 `alert.py`**（评审 P2-8）：`resolve_quote_mode` 这个「生效值读点」在本仓库已经有唯一归属地（`taxonomy.py:295`，上一轮终审刚为 `EG-FINAL-2` 立的），新函数必须与它**并列**。在 `alert.py` 里开第二处会让下一个人两边找，而「两处各读一次同一件事」正是本轮要修的那类缺陷本身。

#### 3.1.2 告警台账：一行两写，写点各自唯一

新表 `ai_intel_alerts`（§5.1）。**同一行有且只有两个写点**：

1. **创建**（`db/ai_intel_alert_repositories.write_intel_alert`）：落点是 **`service._persist_collect` 的同一个 `async with` 与同一次 `commit`**（`service.py:333-359`），**不是** `run_collect_round` 的函数体（评审 P1-5）。三条理由缺一不可：① `item_id` 只有 `upsert_intel_items` 的返回值知道，而那行注释明写「★ 必须**接住**返回值」（`service.py:337-339`），出了这个 `with` 就再也拿不到；② 冷却时刻与日配额的两条 `SELECT` 也要一个会话，`_persist_collect` 的这一个是本轮唯一开着的写会话，另开一个就是SQLite 单写者下的嵌套会话（`run_collect_round` 的注释已为同一件事踩过一次）；③ 台账行必须与条目行**同生共死**——条目回滚而告警行留下，等于凭空多出一条指向不存在条目的告警。**无论 fired 与否都写**——「今天为什么没告警」的答案就住在这些行里。
2. **回写**（`mark_intel_alert_outcome(alert_id, *, outcome, outcome_ts, run_id, round_no)`）：由**会话侧**经 `intel_runtime.note_alert_outcome(...)` 转发，落 `outcome` 一列。

> ⚠️ **这是本轮唯一一处「一个事实由两个进程位置写同一行」的地方**，正是本仓库反复出错的形状（黑板 `EG-3`：四个新列有写点没读点；`EG-P0-1`：读出通路是三跳手抄）。因此：
> - `outcome` 的值域写死在 `taxonomy.ALERT_OUTCOMES`，前后端共用；
> - `write_intel_alert` 落的 `outcome` 恒为 `""`（= 尚未有结果），**绝不预填**；
> - `fired=True` 且 `outcome==""` 的行在界面上显示为「等待会话响应」，超过 `alert_pending_ttl_s` 仍为空的行由动态页签渲染成 `expired_no_response`（**渲染期判定，不回写库**——一个只为了让界面好看而产生的写操作会与真正的回写竞态）；
> - G-11 端到端断言：造一条必然 fired 的候选 → 跑采集轮 → 会话吃下告警并起轮 → **从 REST 输出**（不是从 DB）读到该行 `outcome == "woke"` 且 `runId/roundNo` 指向那一轮。

#### 3.1.3 从后台任务到会话：三跳，每一跳都是既有形状的镜像

```
service._persist_collect(...)             # ① 算 AlertDecision + 写台账（同一事务，P1-5）
service.run_collect_round(...)            # ①' 把 AlertHandoff 挂上 RoundOutcome
        └─> RoundOutcome(alert=AlertHandoff | None)      # 冻结 dataclass 新增 1 个带默认值的字段
IntelRuntime._after_round(inst_id, outcome)
        ├─> await self.refresh(...)       # ② 既有：先重算 digest（新条目此时才拿到 [Ixx] 编号）
        └─> self._alert(inst_id, handoff) # ③ 同步回调，逐行镜像既有的 self._notify
              # ★ 就在这一跳把 `ref` 与 `in_digest` 一起从刚刷新的 digest 里反查出来（P0-3）
manager._on_intel_alert(group_id, inst_id, handoff)      # 同步；镜像 _on_intel_summary(E-11)
        └─> member.session.note_intel_alert(handoff)     # 同步 setter，绝不 await
```

三条硬约束：

- **顺序不可换**：`refresh` 必须在 `_alert` 之前。理由：`AlertHandoff.ref` 要从**刚刚重算好的** digest 的 `refs` 反查（`item_id → "I03"`），这样 prompt 里的唤醒句能说「本轮由情报 **[I03]** 触发」，而模型可以用同一个编号把它引用回来（复用 7e2d5be 轴 A 的既有编号表，不发明第二套标识）。`cite_enabled=False` 时 `refs` 整键缺席（E-2）⇒ `ref` 为 `""` ⇒ 唤醒句退化为不带编号的版本。
- **★ 同一跳还要算出 `in_digest`（评审 P0-3，本轮最容易写出一句假话的地方）。** 「这条情报有编号」与「这条情报真的出现在 prompt 里」**不是同一件事**，而 v1 的唤醒句直接断言了后者（「该情报已在下方情报段中列出」）。两条独立路径会让这句话变成假话：
  1. `disclosure._TIER_RANK = {"long": 0, "mid": 1, "ultra": 2}`（`disclosure.py:39`）⇒ 注入排序 **long 在最前、ultra 在最后**；`prompt_max_chars` 默认 **1200**（`config.py:1015`）。单行由 `render_line` 定稿（`disclosure.py:61-68`）：`[I01] · 超短｜风险事件·偏空·高：<标题> —— <摘要>（16 分钟前｜3 源｜置信 0.78）`，固定部分 ≈ 44 字符，而 `headline_max_chars = 40`（`taxonomy.py:242`）、`summary_max_chars` 默认 **160**（`config.py:1001`）⇒ **单行上界 ≈ 244 字符、典型 ≈ 190** ⇒ **一次注入只装得下 ~5–6 行**；而 `max_active` 默认 long=16 / mid=24（`config.py:1006-1008`），且预算循环是 `break` 不是 `continue`（装不下就停）。**长期层只要攒到 6 条 active，ultra 条目就再也进不了 prompt**——而告警条目恒是刚采到的 ultra。这不是一个边缘情形，而是这套默认值下的**稳态**。
  2. `ai_trade_intel_inject=False` 时 `build_snapshot` 直接把 digest 置空（`service.py:608-612`），情报段整段不存在，而告警链路**不读这把开关**，照样唤醒。
  故 `_alert` 这一跳必须同时产出 `in_digest = ref in (digest.get("refs") or {}).values()` 的等价判定（`cite` 关时改判 `item_id` 是否在本次 `lines` 对应的条目集合里；`inject` 关时**恒为 False**），并由 §4.3 的唤醒句分两态渲染。**绝不允许**用「有 ref」冒充「在 prompt 里」。
- **绝不 await**：`_on_intel_alert` 与 `note_intel_alert` 全同步。情报后台任务里出现一次对会话的 await，就等于把「停止按钮」的响应时间与情报轮绑在一起（`scheduler.py` 纪律 1 的成文理由）。
- **至多一条待处理告警**：`note_intel_alert` 用 `severity` 比大小覆盖，**不排队**。一个五分钟前的旧告警与一个刚到的新告警排在一起时，先跑旧的是错的。被覆盖掉的那一条立刻经 `note_alert_outcome(..., outcome="superseded")` 结账。
  ⚠️ **`note_alert_outcome` 是 `intel/runtime.py` 的模块级函数，不是协程**（评审 P1-10）：「结账」要写一次库，而 `note_intel_alert` 与 `_maybe_intel_wake` 的清理分支都在同步上下文里。修法逐行照抄同文件里的 `invalidate(inst_id)`（`runtime.py:250-262`）——同步函数体内 `scheduler.schedule(...)` 一个协程，交给既有 tracked 任务集。`session.py:65` 的 `from .intel import IntelAttribution, intel_digest_for, intel_summary_for` 相应补上它，`intel/__init__.py` 导出它（§6.1 #6/#14/#16）。

#### 3.1.4 会话侧 `_maybe_intel_wake`

插入位置：`_monitor_tick` 的倒数第三行，**紧邻 `_maybe_signal_flip_wake` 之前**（E-5/E-6）。

```
await self._maybe_intel_wake()          # 轴 A：外生离散事件优先于内生预演信号
await self._maybe_signal_flip_wake()
await self._maybe_interval_wake()
```

排在最前的理由要写进注释——但**必须写对**（评审 P2-7）：v1 说「排在它前面能省掉那一次白跑的预演」是**不成立**的，实读 `session.py:1789-1798`，`_maybe_signal_flip_wake` 是先 `await self._preview.run(...)` **再**判 `self._deciding`，预演照跑，一次也省不掉。真正的两条理由是：① **时延**——反过来排会让一条刚到的高危情报白等一个 tick（默认 5s，`live_auto_poll_interval_s`）甚至更久；② **外生离散事件优先于内生预演信号**——预演是对「未收线的信号板可能翻转」的估计，情报告警是一件**已经发生**的外部事实。**安全性由 E-6 保证**：`_maybe_signal_flip_wake` 的早退判据 `if result is None or self._deciding: return` 在 `_deciding` 为真时**不消费任何锁存**（`plan_signal_flip` 根本不被调用），故那次翻转确认不会被白白吃掉，下一 tick 仍可唤醒。

判定与清理规则（**每一条抑制都要回写台账**）：

| 判据 | 动作 | `outcome` |
|---|---|---|
| 无待处理告警 | 直接 return | — |
| `self.state != "running"` | **清空**待处理 | `dropped_not_running` |
| `self._stale_degraded` | **清空** | `dropped_feed_stale` |
| `self._last_board is None` | **清空** | `dropped_no_board` |
| `now - handoff.created_wall > alert_pending_ttl_s` | **清空** | `dropped_expired` |
| `self._deciding` | **保留**（下个 tick 再试），节流记一次 | `deferred`（只回写第一次） |
| 以上全不命中 | 认领槽位并起轮 | `woke` |

- **保留 vs 清空的分界线是成文的**：`_deciding` 是一个**几秒内必然消失**的状态，而在飞的那一轮**看不到**这条新情报（它的 payload 在告警之前就定稿了），所以等下一 tick 是对的；其余四条都是「等下去也不会变好」的状态，留着只会让一条陈旧情报在用户恢复会话时突然唤醒一轮。
- **不复用 `wake_skips` / `wakeDrops` 口径**：那两个计数的语义是「原本会发生的墙钟唤醒被抑制了多少次」。情报告警另立 `intelAlertWakes` / `intelAlertDrops`，并在 `WakeRuntime.stats()` / `status_counts()` 里与既有四个计数并列（§3.4.3）。
- **不依赖墙钟唤醒是否启用**（E-26）：`_maybe_intel_wake` **没有** `wake_interval_s <= 0` 这道判据。`trend_hold` 用户把墙钟唤醒设成 0 时，情报告警是他们唯一的日内触发源——这恰恰是本轮最主要的受益场景。
- 认领槽位后与既有两条唤醒源**逐行相同**：`_deciding = True` → 记 `_last_round_bar_index` / `_last_round_px` / `_last_decision_started_wall` → `asyncio.create_task(self._run_decision_round(board, bar_index, ref_px, int(now), wake))`。**check→set 之间无 await**。
- `_last_decision_started_wall = now` 会把下一次墙钟唤醒推迟一个完整周期——与 `signal_flip` 轮的既有行为一致，**刻意保持一致而不是特事特办**。

#### 3.1.5 九组注册表 / 十张表（**其中四处漏了不报错**——评审 P2-1 / P2-2 收窄）

> **评审更正**：v1 把九处一律说成「漏一处不报错」。逐处实读之后，真实的风险面是**四处**：后端 #2（`conditions`）/ #3（`wake_text` 两张表）/ #4（`_FULL_TIER_KINDS`）与前端 #9（`AXIS_VALUES.wake`）。#6 与 #7 由 tsc 强制（`WAKE_LABELS` 声明为 `Record<WakeKind, …>`），#5 与 #8 由**既有**门禁 `lib/experienceLabels.source.test.ts` 双向冻结。把不静默的四处也说成静默，会让 G-9 的设计错配（v1 就因此打算新建一条与既有门禁重叠的源码扫描用例）。下表逐处如实标注。

| # | 位置 | 要加什么 | 漏掉的症状 | 静默？ |
|---|---|---|---|---|
| 1 | `ai_auto/wake_reason.py` `WAKE_KINDS` | `"intel_alert"`，**插在 `candle_close` 之后、`bar_advanced` 之前** | 判定永远落不到新 kind | **否**（它是本表的源，漏了下面八处全错，G-9 后端一侧当场红） |
| 2 | `ai_auto/wake_reason.py` `classify_wake` 的 `conditions` | `("intel_alert", facts.intel is not None)`；`WakeFacts` 加 `intel: dict \| None = None`；wire 在 `facts.intel is not None` 时加 `"intel"` 键（缺席即整键不出现，同 `flip` 的既有范式） | 同上 | **是**（G-9 后端一侧新加的并集断言正是为它写的） |
| 3 | `ai_auto/wake_text.py` 的 **`_ALSO_TRUE_LABEL`（:16）与 `_WAKE_BODY`（:171）两张表** | 前者加 `"intel_alert": "情报告警"`（它是「同时成立项」的短标签），后者加 `"intel_alert": _wake_intel_alert` 渲染器 | **缺前者不是裸 slug 而是静默省略**（评审 P2-3）：渲染是 `[_ALSO_TRUE_LABEL[name] for name in … if name in _ALSO_TRUE_LABEL]`（`wake_text.py:33-34`），不认识的键被直接过滤掉——**证据消失比显示一个 slug 更难发现**。缺后者 ⇒ `_WAKE_BODY.get(kind)` 为 None ⇒ `format_wake_for_prompt` 直接 `return ""`（`wake_text.py:29-31`）⇒ **整段唤醒句连同 `alsoTrue` 与 backlog 子句一起消失** | **是**（两张表都是；G-9 后端一侧覆盖） |
| 4 | `ai_auto/tactical_context.py` `_FULL_TIER_KINDS` | 加 `"intel_alert"`；**`_EXECUTION_FULL_KINDS` 不加**（沿用它自己那句「刻意不增」——执行周期证据自上一轮起没变） | 最需要细看战术明细的那一轮反而拿到压缩档 | **是**（G-9 后端一侧单列一条子集断言） |
| 5 | `ai_auto/experience/taxonomy.py` `VALUE_LABEL_ZH` | `"intel_alert": "情报告警"` | 经验图节点标签是裸 slug | **否**（`lib/experienceLabels.source.test.ts` 与 #8 双向对拍：**只漏这一边** ⇒ 前端多出一个后端不认识的取值 ⇒ 该用例第三个 `it` 当场红） |
| 6 | `apps/web/lib/aiTradeTypes.ts` `WakeKind` 联合 | `\| "intel_alert"`；`WakeInfo` 加可选 `intel?: WakeIntelInfo \| null` | tsc 在别处红 | **否**（tsc） |
| 7 | `apps/web/lib/aiTrade.ts` 的 **`WAKE_LABELS`**（:1148，**真名不是 `WAKE_KIND`**） | `intel_alert: { label: "情报告警 · 高危市场情报", tone: "warn" }` | **这一处不是静默的**（评审 P2-1）：它声明为 `Record<WakeKind, AiTradeTriggerView>`，相邻注释明写「后端新增 kind 时 tsc 强制要求补齐」⇒ 漏掉即 tsc 红。列在此表是为了完整，**不计入 R-4 的静默风险面** | **否**（tsc） |
| 8 | `apps/web/lib/experience.ts` 标签表 | `intel_alert: "情报告警"` | **已被既有门禁冻结**（评审 P2-2）：`lib/experienceLabels.source.test.ts` 解析后端 `experience/taxonomy.py` 的 `VALUE_LABEL_ZH` 并双向断言（含反向的「前端没有多出后端不认识的取值」）⇒ 与 #5 一起漏 = 绿，只漏一边 = 红。**不计入 R-4 的静默风险面** | **否**（既有门禁；⚠️ 但「#5 与 #8 一起漏」是绿的——这正是 #9 那条新断言要补的缺口） |
| 9 | `apps/web/lib/experience.ts` `AXIS_VALUES.wake` | 数组加值 | **经验图预演选择器里根本选不到这一类**，而 tsc / vitest 都不会红（E-10 相邻注释已为 `exit` 轴写过同一句） | **是**（前端唯一真正静默的一处；G-9 前端一侧就是为它加的） |

**G-9 扩既有用例，不新建第二条判据**（评审 P2-2 定稿）：上表九处里，#6/#7 由 tsc 兜住、#5+#8 由**既有**的 `apps/web/lib/experienceLabels.source.test.ts` 双向兜住，**真正静默的只剩 #9 `AXIS_VALUES.wake`**（以及后端的 #2/#3/#4，它们由 pytest 兜）。故 G-9 的正确形态是：
- **前端**：在 `experienceLabels.source.test.ts` 里**追加一个 `it`**——从后端 `ai_auto/wake_reason.py` 正则抽出 `WAKE_KINDS`，断言 `AXIS_VALUES.wake` ⊇ 该集合 ∪ `{"unknown"}`。放在这条既有用例里而不是新建 `wakeKindRegistry.source.test.ts`，理由是**同一件事只该有一处判据**——两条源码扫描用例各解析一次同一个 Python 常量，第二天就会有一条被改坏而没人发现。
- **后端**：在 `test_ai_trade_wake_reason.py` 里断言 `set(WAKE_KINDS)` 与 `conditions` 的名字集合、`_WAKE_BODY` 的键集合、`_ALSO_TRUE_LABEL` 的键集合 ∪ `{"heartbeat"}`、`experience.taxonomy.VALUE_LABEL_ZH` 的覆盖**四者一致**（`_FULL_TIER_KINDS` 是**子集**关系，单列一条断言 `"intel_alert" in _FULL_TIER_KINDS and "intel_alert" not in _EXECUTION_FULL_KINDS`）。
⚠️ 本仓库 `core.autocrlf=true`，源码正则**必须写 `\r?\n`**，否则在干净检出上恒红。

#### 3.1.6 与既有唤醒的相互作用（逐条裁决）

- **优先级为什么排在 `bar_advanced` 之前？** `signal_flip` 当年排在它之后的理由是「真的收了新根永远优先」。情报告警不同：**这一轮之所以存在，就是因为这条告警**；把它标注成 `bar_advanced` 会让 prompt 的唤醒句说「你补看一根新 K 线」，而真正的原因从标题里消失。证据不丢——`bar_advanced` 若同时成立会进 `alsoTrue`（E-3）。
- **会不会被跳过守卫吃掉？** 不会：`should_skip_wake` 只跳 `heartbeat` 与 `position_watch`（E-7）。**但必须写一条用例冻结它**（G-8），因为「新 kind 意外落进可跳过集合」是一个静默失效。
- **会不会绕过任何交易门禁？** 不会：它调用的是同一个 `_run_decision_round`，共识门禁、铁律层、edge 门禁、仓位层、反思层、离场优先级全部照跑。**这一条是轴 A 全部安全性的支点，必须写进 `_maybe_intel_wake` 的 docstring**（逐行照抄 `plan_signal_flip` 那句「本方法返回的 wake 只影响 `payload["wake"]`」的写法）。
- **暂停 / 结束会话时？** `state != "running"` 即丢弃（含 `paused`）。`intel_scheduler.pause` 已经会停调度，但**在飞轮仍可能产出告警**，故会话侧这道判据不是冗余。

### 3.2 轴 B · 战绩与信誉（efficacy）

#### 3.2.1 两条已经在产出、但没有下文的证据流

| 流 | 来源 | 累积速度 | 说明 |
|---|---|---|---|
| **B1 战绩**（引用结算） | `ai_intel_citations`（E-18）：`role="entry"` 且 `settled=True` 且 `outcome != "void"` 的行，带 `net_pnl_usd` / `outcome` | 慢（要真的开仓并平仓） | 回答「采信这一类情报之后，钱是赚了还是赔了」 |
| **B2 信誉**（核查判定） | `ai_intel_items.verdict` ∈ `confirmed/refuted/unresolved` + `sources_json` 的域名 | 快（每个整理周期跑一次核查轮） | 回答「这一类情报 / 这个域名说的话，事后有多大比例被证实」 |

B2 是本轮**最快见效**的部分：上一轮之后每个核查轮都在逐条产出判定，但这些判定今天只作用于**被判的那一条**（refuted 立即停注入、降一层）。把它按类别与**域名**聚合起来，就得到了一张用户拉黑信源时**唯一需要**的表——而拉黑功能是上一轮就做好的、至今没有数据支撑的功能。

#### 3.2.2 纯核心 `ai_auto/intel/efficacy.py`

> **评审 P1-2 / P1-3 定稿（两条硬约束，缺一不可）**
>
> 1. **不新建 `EfficacyLimits`**：四个旋钮追加进 `taxonomy.IntelLimits`（理由同 §3.1.1 的方框），
>    由 `taxonomy.limits_from_settings` 组装。
> 2. **`efficacy.py` 绝不 import `gate`**。v1 的 `weight_for(item, …)` 里的 `item` 是
>    `gate.IntelItem`，而 `gate.score_item` 又要在**运行期**调 `efficacy.weight_for` ⇒
>    `gate ↔ efficacy` 循环 import。修法不是延迟 import（那只是把环藏起来），而是**让先验层
>    根本不需要认识条目**：`solve()` 收纯行 dict（仓库函数的产物），`weight_for` 只收一个类别串。
>    `gate.py` 侧因此连函数调用都省了，直接读 `eff.weights.get(item.category, 0.0)`。

```python
# ---- taxonomy.IntelLimits 追加的战绩字段 ----
    efficacy_enabled: bool = False
    efficacy_k_min: int = 8                  # 收缩强度（伪计数）
    efficacy_max_weight: float = 0.25        # 先验对 score_item 的绝对封顶
    efficacy_min_domain_samples: int = 3     # 域名信誉的最小样本，低于它一律显示「样本不足」

# ---- intel/efficacy.py（只 import taxonomy，**不 import gate**）----
@dataclass(frozen=True)
class Stat:
    n: int; wins: int; net_usd: float          # B1
    confirmed: int; refuted: int; unresolved: int  # B2

@dataclass(frozen=True)
class Efficacy:
    """一次求解的全部产物（只读；调用方缓存它，注入端与 REST 共读同一份）。"""
    overall: Stat
    by_category: dict[str, Stat]
    by_domain: dict[str, Stat]
    weights: dict[str, float]        # category -> [-max_weight, +max_weight]
    domain_weights: dict[str, float]

def solve(citation_rows: list[dict], item_rows: list[dict], *,
          limits: IntelLimits, now: float) -> Efficacy: ...
def weight_for(category: str, eff: Efficacy, *, limits: IntelLimits) -> float: ...
def accuracy(stat: Stat) -> float | None: ...   # None = 分母为 0，与 0.0 严格区分
```

`Efficacy` 是一个**纯函数的产物**：同一批 `(citation_rows, item_rows, limits, now)` 在任何地方
求解都得到同一个值。这一条性质是下面 §3.2.4「两处各求一次不构成两个真源」的全部依据，
**必须用 G-28 冻结**，否则它就退化成一句好听话。

**层级收缩（两级，不是一级）**：

```
raw(bucket)  = 0.6 * win_rate_centered + 0.4 * tanh(net_usd / scale)     # B1，缺样本时为 None
             ⊕ verdict_score = (confirmed - refuted) / (confirmed + refuted + unresolved)  # B2
shrunk(b)    = (n_b * raw_b + k_min * parent) / (n_b + k_min)
weight(b)    = clamp(shrunk(b), -max_weight, +max_weight)
parent 链    = category → overall → 0.0（中性）
```

三条必须写死的性质：

1. **样本为 0 ⇒ 权重恒为 0.0**，且 `accuracy()` 返回 `None` 而不是 `0.0`。「没数据」与「全错」是两件事，写成同一个值就是把一次没跑过的统计伪装成一次很差的成绩（沿用 `taxonomy.VERDICTS` 里 `""` 与 `unresolved` 分开的既有裁决）。
2. **B1 与 B2 分开求解、分开展示，只在 `weight` 上合成**。理由：它们的可证伪性完全不同（B1 混杂了仓位与时机，B2 只关于「这句话是不是真的」）。合成式 `raw = 0.5*b1 + 0.5*b2`，缺哪一半就只用另一半，两半都缺则 `None`。
3. **`scale` 不是常数**：`net_usd` 的量纲随账户大小变化。取 `scale = max(1.0, 该标的已结算引用的 |net_usd| 中位数)`——**中位数不是均值**（一笔离群的大额往返会让均值失去意义）。

#### 3.2.3 四个消费点（每一个都必须有读点，且都不是门禁）

| # | 消费点 | 修法 | 关闭时的回退 |
|---|---|---|---|
| 1 | **注入排序** `gate.score_item` | 新增第二个加分项 `eff_bonus = eff.weights.get(item.category, 0.0)`（**不调 `weight_for`，见 §3.2.2 的 P1-3 定稿**），签名扩为 `score_item(item, *, now, limits=None, eff=None)`，**`eff=None` ⇒ 返回值逐字节回退**（照抄 `limits=None` 那条既有范式，E-1） | `eff=None` |
| 2 | **整理轮的举证责任** `gate.review_actions` 的 promote 分支（**不是 `_review_promote`**，见下方 P0-2 方框） | 目标类别 `weight < -0.5*efficacy_max_weight` 时，升长期额外要求 `reason` 归一后长度 ≥ `WEAK_RECORD_MIN_REASON_CHARS`（新常量，默认 20，写在 `efficacy.py` 里），否则驳回码 `promote_weak_record`。**只加一条对理由长度的要求，不改 `_review_promote` 里任何既有数值门槛**（`promote_min_domains` / `promote_min_age_s` 一个字节不动） | 判据整段不参与 |
| 3 | **溢出淘汰打分** `cleanup.plan_cleanup` | 它已经调 `score_item`（`cleanup.py:115`），把 `eff` 一路透传即可；**不新增第二套打分**。⚠️ `plan_cleanup` 与 #2 一样住在**整理轮**里（`service.py:409`），`eff` 从哪儿来见 §3.2.4 的 P0-1 方框 | 同 #1 |
| 4 | **信源页签 / 影响力页签** | `by_domain` 与 `by_category` 经 REST 下发（§四） | 下发 `enabled: false` + 空表，前端显示「战绩统计已关闭」 |

> ⚠️ **评审 P0-2：判据落在 `review_actions` 而不是 `_review_promote`。**
> 实读 `gate.py:644`：`_review_promote(item, to_tier, *, limits, now)` —— **它的入参里根本
> 没有 `reason`**，`reason` 只存在于 `review_actions` 循环里的那个 `entry` dict（`gate.py:
> 690-705）。照 v1 写就是本仓库五次 P0 的同一形状：**新判据取自不携带该信息的入参**。而且再
> 塞 `reason` 与 `eff` 两个 keyword 形参会让它到 6 个，越 CLAUDE.md「形参 ≤5」。
>
> **定稿**：`review_actions` 的 promote 分支在拿到 `_review_promote` 的 `None`（= 既有门槛
> 全过）之后，**再**判一次战绩举证责任——它同时持有 `entry["reason"]`、`item` 与 `eff`，
> 三样都在手上。`_review_promote` 的签名与函数体**一个字节不改**，既有的 8 条升级判据因此
> 也一个字节不改（G-2 的逐字节回退才有意义）。归一函数与 `WEAK_RECORD_MIN_REASON_CHARS`
> 常量写在 `efficacy.py`（`review_actions` 已经 import 了 `taxonomy`，再 import 一个纯核心
> 不成环）。
>
> ⚠️ **`score_item` 今天有四个调用点，而它们对 `limits` 的传法本来就不一致**——这是上一轮的成文裁决（closed-loop 评审 P1-1）而不是缺陷：
>
> | 调用点 | 位置 | 今天传 `limits` 吗 | 本轮传 `eff` 吗 |
> |---|---|---|---|
> | `disclosure.build_digest` 注入排序 | `disclosure.py:112` | **传** | **传** |
> | `cleanup.plan_cleanup` 溢出淘汰 | `cleanup.py:115` | **传** | **传** |
> | `gate.admit` 的 over_cap 截断 | `gate.py:614` | 不传 | **不传** |
> | `service.render_listing` 整理轮清单 | `service.py:519` | 不传 | **不传** |
>
> **`eff` 必须逐行照抄 `limits` 的传法**：给新形参一个 `None` 默认值，让后两处零改动、返回值逐字节回退。若实施者「顺手」把 `eff` 也传给后两处，`gate.py:614` 与 `service.py:519` 的排序会在本轮**首次**与合入前不同——那是一处没人要求、也没人会发现的行为变更。G-13 因此断言的是：**前两处一致、后两处逐字节不变**。

#### 3.2.4 求解落在哪里：`build_snapshot` 这个唯一查库点

`service.build_snapshot(deps, inst_id)` 是「一次性算出注入用 digest 与界面用 summary」的唯一查库点（`service.py:593`）。轴 B 在这里**加一次聚合查询**（`ai_intel_citations` 按 `inst_id` 的结算行 + 已在内存里的 items），求解 `Efficacy` 并：

- 传给 `build_digest`（供 `score_item`）；
- 放进 `summary["efficacy"]` 的**摘要形态**（只有 `overall` 与 `enabled`、`kMin`、`sampleN` 四个数）——**完整的 `by_category` / `by_domain` 不进 summary**：summary 走 WS 推给每个前端连接，一张几十行的表逐轮广播是纯浪费。完整表由 REST 拉（§四）。

**查询代价**：一次带 `ix_ai_intel_cite_scope(inst_id, cited_ts)` 索引的聚合，行数被 `cite_retention_d`（默认 180 天）天然封顶。`build_snapshot` 本来就在每轮结束与每次 REST 写之后跑一次，不是热路径。

##### 3.2.4.1 整理轮**自己再求一次**（评审 P0-1，本轮唯一一处「同一个值算两遍」）

v1 只写了 `build_snapshot` 这一处求解，然后把 `Efficacy` 缓存进 `IntelRuntime._efficacy`。**四个消费点里有两个够不着那份缓存**：

- 消费点 #2（`review_actions` 的 `promote_weak_record`）与 #3（`plan_cleanup` 的溢出淘汰）都住在 `service.run_curate_round` 里（`service.py:378-411`）；
- 而 `run_curate_round` 手上只有 `deps`，`IntelDeps`（`service.py:140-159`）**没有任何指向 `IntelRuntime` 的引用**——它被刻意设计成「显式注入，使离线用例不必造一个真的会话」，给它塞一个 runtime 反手就打翻那条纪律与二十多个用例。

照 v1 实施的结果是：这两个消费点在**默认路径上恒 `eff=None`** ⇒ 判据整段不参与 ⇒ 轴 B 四个消费点里只有两个真的活着，而 v1 的 §7.1 **一条闸都没覆盖它们** ⇒ 一次完整的静默空转（黑板 `CL-1` / `EG-3` 的第 N 次同形）。

**定稿：`run_curate_round` 在它自己那个只读会话里再求一次。** 它已经在开会话取 `load_intel_scope` 与 `list_intel_blocked_domains`（`service.py:372-377`），加一次同样的 citations 聚合即可，随后把同一个 `Efficacy` 显式传给 `review_actions` 与 `plan_cleanup`。

**这不构成第二个真源**，理由必须写死：`efficacy.solve` 是**纯函数**（§3.2.2），同一批 `(citation_rows, item_rows, limits, now)` 在哪里求都得到同一个值；两处求解的输入来自同一张表、同一个标的、相隔至多一个 tick。**G-28 用一条用例把这句话钉住**：对同一批库存，`build_snapshot` 与 `run_curate_round` 求出的 `Efficacy` 逐字段相等。若日后有人想省掉这一次查询，正确的动作是把 `Efficacy` 塞进 `IntelDeps` 的一个**可选字段**并由 `IntelRuntime` 在调用前填好——**不是**让整理轮读一份可能已经过期的缓存。

### 3.3 轴 C · 立场矛盾与去噪

#### 3.3.1 纯核心 `ai_auto/intel/conflict.py`

```python
CONFLICT_WINDOW_H: dict[str, float] = {"ultra": 24.0, "mid": 240.0, "long": 1440.0}

@dataclass(frozen=True)
class ConflictPair:
    a_id: int; b_id: int; overlap: float; window_h: float; category: str

# `conflict_enabled` / `conflict_min_overlap` 同样追加进 taxonomy.IntelLimits（评审 P1-9）；
# 本函数不读 Settings，判据由调用方传入。
def find_conflicts(items: list[IntelItem], *, min_overlap: float, max_pairs: int,
                   now: float) -> list[ConflictPair]: ...
def topic_tokens(headline: str, summary: str) -> frozenset[str]: ...
def overlap(a: frozenset[str], b: frozenset[str]) -> float: ...   # Jaccard
```

四条判据（全部满足才成对）：

1. 两条都是 `status == "active"` 且 `verdict != "refuted"`（被证伪的一方**自动解除矛盾**——它已经不进注入了）；
2. `category` 相同（跨类别的相反立场不是矛盾：宏观偏空 + 项目偏多是常态）；
3. `stance` 一个 `bullish` 一个 `bearish`（`neutral` 不参与）；
4. `overlap(topic_tokens(a), topic_tokens(b)) >= min_overlap`（默认 0.35）**且** `|a.event_ts - b.event_ts| <= min(window_h(a.tier), window_h(b.tier)) * 3600`。

`topic_tokens` 复用 `quotes.normalize_for_match`（`quotes.py:83`）的 NFKC / 引号 / 空白归一（**不写第二个归一器**；`normalize_with_map` 是带回切索引的那一支，本轮不需要），再切分为 2-gram 字符组（中文没有空格分词，字符 2-gram 是这个仓库里唯一不引入分词依赖的可行选择），去掉长度 < 2 的组，去掉出现在**该标的全部 active 条目的 60% 以上**的高频组（标的名、"价格"、"市场" 这类没有区分度的词——一个自适应停用表，不需要维护词典）。

`max_pairs` 封顶（默认 6）：按 `overlap` 降序取前 N。理由是成本与注意力都不是免费的——20 对矛盾摊在 prompt 里等于没说。

#### 3.3.2 注入端：两遍预算，绝不越过 `prompt_max_chars`

`build_digest` 的既有预算循环有一条硬纪律：「装不下就停，绝不半截截断一条」（E-2）。加矛盾标注会改变行长，而标注文本 `（与 [I05] 相互矛盾）` 里的编号**要等这一行被收进 `lines` 才知道**——这是一个循环依赖。

**修法：两遍。**

0. **接口**：`build_digest` 多收一个 `conflicts: tuple[ConflictPair, ...] = ()` 形参（默认空 ⇒ 三步全部跳过 ⇒ 逐字节回退，照抄 `limits=None` 的既有范式）。**纯核心不自己读 `Settings`**，`conflict_enabled` 的效果体现为调用方传不传这个元组（评审 P1-9）。
1. **重排（★ 只在同一层块内部）**：`live` 排序后，把每个矛盾对的 B 项**移到 A 项的紧后面**（稳定移动，不改其余相对次序）。理由：两条相反的证据必须**同屏出现**，否则模型看到的是「一条偏多情报」和「另一处一条偏空情报」，而不是「一个未解的分歧」。

   ⚠️ **跨层的矛盾对不重排（评审 P1-8）。** 实读 `disclosure.py:111-119`，排序首键是 `_TIER_RANK`（`{"long": 0, "mid": 1, "ultra": 2}`），而预算循环是 `if used + len(line) + 1 > budget: break` —— **`break` 不是 `continue`**。把一条 long 层的 B 项拖进 ultra 块，会同时干两件坏事：① 打乱层分组（渲染行上还写着「长期」，读者会以为排序坏了）；② 一条更长的行被提前放进队列，可能让整个循环**提前 break**，于是本来能装下的若干条一条都进不来——v1 §3.3.2 只证明了「不超预算」，Q4 只讨论了排序质量，**两者都没覆盖这一条**。
   定稿：`a.tier != b.tier` 的矛盾对**不重排**，双方仍各自渲染标注（只要两端都被收进）；同层对照常重排。代价（同层内一条低分条目可能挤掉同层一条高分）登记进 R-7。
2. **第一遍（预算）**：按重排后的顺序跑既有预算循环，但每一条**若它属于某个矛盾对**，额外预留常量 `CONFLICT_MARKER_RESERVE = 24` 字符。产出「被收进来的 id 集合」。
3. **第二遍（定稿）**：对被收进来的条目按顺序编号、渲染；**当且仅当矛盾对的两端都在集合里**时才渲染标注（否则标注会指向一个不存在的编号）。`cite=False`（无编号）时标注退化为固定文案 `（与库中另一条同题反向情报冲突）`。

**性质**：第二遍产出的总长度 ≤ 第一遍的预算上界（预留是上界，实际标注 ≤ 24 字符），故**永不超预算**；`hidden` 仍等于 `len(live) - len(lines)`，口径不变。`conflicts=()` 时跳过重排与两遍，`build_digest` 逐字节回退（G-3 用 sha256 冻结）。

**第二遍内部还要再分两小步**（否则会渲染出指向自己的编号）：先按被收进的顺序把 `refs` 全部定稿，**再**渲染标注文本——`（与 [I05] 相互矛盾）` 里的 `I05` 是对方的编号，而对方可能排在自己后面。这一步不额外消耗预算（长度已在第一遍预留）。

`format_intel` 的段末在**存在已渲染标注**时追加一句固定说明：`（标注为「相互矛盾」的两条情报指向同一主题但方向相反，请在推理中明确说明你采信哪一条以及为什么。）`——放在 `_CITE_NOTE` 之后，`conflict_enabled=False` 时整句缺席。

#### 3.3.3 整理轮的裁决义务：可测量，不可强制

模型不能被"强制"做任何事。能做的是**把矛盾摆到它面前，并测量它有没有处理**。

- `prompts.build_curate_user_prompt` 的清单文本前追加一段「**未裁决的矛盾对**」：每对两行，带双方的 item id、标题、立场、置信度、`verdict`、以及**已经悬置了几轮**（`open_rounds`）。悬置轮数最多的对排在最前。
- `build_curate_system_prompt` 新增一条纪律（**英文正文，与该 prompt 既有语言一致**；`conflict_enabled=False` 时整段缺席，见上一条的 P0-4 定稿）：对每一个列出的矛盾对，必须给出一个动作（`demote` / `archive` / `merge` / 或显式的 `keep_both` 并说明为什么两条可以同时成立）。⚠️ **不要写 `supersede`**：`gate.CurationAction.action` 的值域是 `promote|demote|archive|merge|keep`（`gate.py:630`），`supersede` 不是一个动作而是 `merge` 的落库效果（`supersedes_id` 列）——把一个不存在的动作名写进 system prompt，模型会照着发，然后被 `bad_action` 驳回，而界面上没有任何线索指向这句 prompt。
- `prompts.build_curate_tool_schema`（`prompts.py:98-125`）的 `action` 枚举改为**从 `taxonomy.CURATE_ACTIONS` 生成**（今天它是一行手写的 JSON 数组，见 §5.2 的警告），并新增 `pairId`（对方 id，`action=keep_both` 时必填）。`reason` 字段既有，不新增。
  ⚠️ **★ 评审 P0-4：枚举必须按 `limits.conflict_enabled` 条件化。** v1 把 `keep_both` 无条件写进 `CURATE_ACTIONS`，于是矛盾开关关掉时工具 schema 仍多一个枚举值、system prompt 仍多一条裁决纪律、user prompt 仍……——**§3.0 第 2 条自我约束被本文自己破坏**，而原 G-3 只看 `build_digest`、看不见它。后果不只是「字节不同」：模型可以发出一个必然被 `keep_both_bad_pair` 驳回的动作，白占一个 `curate_max_actions` 名额（默认 24 里的一个），而界面上只会多出一行驳回码。
  定稿：`build_curate_tool_schema(limits)` 里写 `actions = tax.CURATE_ACTIONS if limits.conflict_enabled else tax.CURATE_ACTIONS_BASE`（`CURATE_ACTIONS_BASE` 是**不含 `keep_both`** 的五值元组，`CURATE_ACTIONS = CURATE_ACTIONS_BASE + ("keep_both",)` —— 单一真源仍然只有一个，第二个名字只是它的前缀）；`pairId` 属性、下面那条 system prompt 纪律、以及 user prompt 的「未裁决的矛盾对」段**三者同条件缺席**。G-3 因此扩为**四件产物一起 sha256**，G-30 单独冻结这条条件化。
- `gate.review_actions` 新增 `_review_keep_both`：驳回码 `keep_both_bad_pair`（两个 id 不构成本轮列出的矛盾对）、`keep_both_thin_note`（`reason` 归一后短于 `KEEP_BOTH_MIN_REASON_CHARS`）。**`keep_both` 通过时不改任何条目的 tier / status**，只把双方的 `last_curated_ts` 与 `curation_note` 写上——它是一次**有记录的不作为**，而不是一次静默的忽略。
- 轮存根 `actions_json` 新增两个计数：`conflictsOpen`（本轮开始时的对数）与 `conflictsResolved`（本轮之后不再成对的对数，**由服务端在动作落库后重算一次 `find_conflicts` 求差得到，不采信模型自报**）。

> ⚠️ **`open_rounds` 不落库。** 它由 `min(a.last_curated_ts, b.last_curated_ts)` 与该标的的整理轮流水推算——新增一列去记「这对悬置了几轮」会立刻带来「这一列谁写、谁清」的问题，而 `ai_intel_items` 上已经有 14 个后加列都带着「绝不进 `_item_values`」的警告（E-17）。推算的代价是一次已经在做的查询。

### 3.4 轴 D · 可解释

#### 3.4.1 既有「整理记录」页签升级为「动态」时间线（**零新增页签**）

`IntelModal` 已有 7 个页签 / 553 行（E-21）。再加两个会同时越过 HCI 的合理上限与 CLAUDE.md 的 800 行上限。裁决：

| 新内容 | 落点 | 理由 |
|---|---|---|
| 告警台账 | **既有的「整理记录」页签（`TabKey` 值 `"rounds"`，`IntelModal.tsx:211`；v1 一直称它「轮次」页签，评审 P2-4 按实际标签更正）改名「动态」**，`TabKey` 值同步改 `"activity"`，轮次行与告警行按时间倒序**合流**成一条时间线 | 二者本来就是同一类东西：「情报层在什么时刻做了什么」。合流之后用户第一次能看到「10:03 采集轮收进 3 条 → 10:03 告警：⚠️ 高危风险 → 10:03 唤醒了第 47 轮决策」这条完整链路 |
| 战绩 + 信誉（按类别） | **「影响力」页签**追加第二块 | 该页签本就在讲「这些情报带来了什么」 |
| 信誉（按域名） | **「信源」页签**每行补「证实 / 证伪 / 准确率」三列 + 按准确率排序 | 拉黑决策就在这一页上做 |
| 矛盾 | **三个层级页签**：`IntelCard` 上一枚 `IntelConflictBadge`，列表头一个「仅看冲突（N）」筛选 chip | 矛盾是条目的属性，不是一个独立的视图 |

组件拆分（守 800 行）：新增 `IntelActivityTab.tsx`（吸收既有 `IntelRoundsTab` 的渲染并合流告警）、`IntelEfficacyBlock.tsx`、`IntelConflictBadge.tsx`、`IntelAlertRow.tsx`；`IntelModal.tsx` 只做路由，**净增不超过 40 行**。

**告警行的文案必须区分三态**（这是本轮最容易做成「看不见的停摆」的地方）：

- `fired=false` → 灰色，`「未告警 · <抑制码中文>」`；连续同码的多行**折叠成一行 + 计数**（否则一个配置偏严的用户会看到几十行「未告警 · 影响力未达门槛」把真正的事件淹掉）；
- `fired=true, outcome=""` 且未超 TTL → `「已告警 · 等待会话响应」`；
- `fired=true, outcome=<code>` → 带色调的结果行，`woke` 用 accent 并**可点击跳到那一轮决策轨迹**（`runId` + `roundNo` 已在行上）。

抑制码与结果码的**中文文案由后端下发**（跟随 `degrade_wire` 的既有裁决：进 wire 的文案由后端管，只在轮存根直方图里出现的码由前端 `CODE_LABEL` 管）。**这一条必须写死**，否则就是第三次「后端加了码、前端没登记、用户看到裸 slug」。

#### 3.4.2 设置桥：六键，逐键往返

`INTEL_SETTING_BRIDGE`（E-15，现 17 行）新增 6 行 ⇒ 23 行：

| wire 键 | app_settings 键 | Settings 字段 | 类型 |
|---|---|---|---|
| `alertEnabled` | `ai_intel.alert_enabled` | `ai_trade_intel_alert_enabled` | bool |
| `alertMinImpact` | `ai_intel.alert_min_impact` | `ai_trade_intel_alert_min_impact` | str |
| `alertCooldownMin` | `ai_intel.alert_cooldown_min` | `ai_trade_intel_alert_cooldown_min` | float |
| `alertDailyCap` | `ai_intel.alert_daily_cap` | `ai_trade_intel_alert_daily_cap` | int |
| `efficacyEnabled` | `ai_intel.efficacy_enabled` | `ai_trade_intel_efficacy_enabled` | bool |
| `conflictEnabled` | `ai_intel.conflict_enabled` | `ai_trade_intel_conflict_enabled` | bool |

`alertMinImpact` 是 `Literal["medium","high"]`，而 `model_copy(update=…)` **不跑校验**（E-15）⇒ 必须在桥的循环里像 `quote_mode` 那样再收一次值域（`value not in ("medium","high") → continue`）。G-16 逐键往返断言（读 `resolve_intel_settings` 的产物，不是「请求没报错」）。

#### 3.4.3 假开关的第三道防线：会话侧**永不复读**告警开关

`ai_trade_intel_alert_enabled` 只在**一个地方**被读：`service.limits_from_settings` 组装 `AlertLimits` 时（用的是 `resolve_intel_settings` 的派生副本）。关闭时 `plan_alert` 返回 `suppressed_code="disabled"`、**不发 handoff**，会话因此根本收不到东西。

**会话侧不得再判一次 `settings.ai_trade_intel_alert_enabled`。** 理由是成文的：会话持的是 `executor.settings`（分组启动时定稿的那一份），而情报设置住在 `app_settings` 里、经 `model_copy` 派生——两份在用户改设置之后必然不同步，会话侧那一判会变成「面板上关了但还在唤醒」或「面板上开了却永不唤醒」。**这条不变式写进 `_maybe_intel_wake` 的 docstring，G-15 用「PUT 关掉 → 跑采集轮 → 断言 handoff 未发出且台账写了 `disabled`」冻结。**

#### 3.4.4 观测键

`WakeRuntime.stats()` / `status_counts()` 各加两个键：`intelAlertWakes` / `intelAlertDrops`。

⚠️ **它们要落进四个前端位置**（实读确认；`signalFlipWakes` 今天就同时住在这四处）：

| # | 位置 | 缺失的症状 |
|---|---|---|
| a | `apps/web/lib/aiTradeTypes.ts:1219-1224`（WS `status` 形状） | 实时看板读不到 |
| b | `apps/web/lib/aiTradeTypes.ts:1287-1290`（另一份统计形状） | 历史页读不到 |
| c | `apps/web/lib/aiTradeHistoryTypes.ts:123-129`（`metrics_json.aiStats.wakeScheduler`） | **（评审 P1-7 更正）不会丢失，也没有读点。** 后端 `session.py:3138` 是 `metrics["aiStats"]["wakeScheduler"] = self._wake.stats(...)` —— **整体赋值**，故 `stats()` 新增的两个键**天然落库**，与这份 TS 类型毫无关系；而全仓 `grep wakeScheduler` 在 `apps/web` 只命中这一处**类型声明**，今天既有的四个计数在这里也没有任何读点（唯一展示点 `AiTradeStatsLive.describeWakeScheduler` 读的是实时行 = 镜像 a）。本轮**仍然补这两键**（跟随既有形状，避免类型与 wire 分叉），但必须如实说清它是**纯声明位**——照 v1 那句「缺它就永久丢失」去写用例，只会得到一条断言类型声明的空闸 |
| d | `apps/web/components/Agent/aiTradeStats/AiTradeStatsLive.tsx:339-343`（唯一展示点，`if (x) parts.push(...)` 的既有范式） | 后端有数、界面上一个字都没有 |

这是本轮第二处「后端有数、前端没落点」的风险位（黑板 `EG-3` 的同形）。**G-17 因此只端到端断言 a 与 d 两处，外加后端一条**：`metrics_json["aiStats"]["wakeScheduler"]` 里确有这两个键且与 `status` 同值（在**后端**断言，不在 TS 类型上断言）。「历史页至今读不到 `wakeScheduler`」按原样登记为既有欠账（§十二），本轮**不顺手做**——那是一个独立的展示位设计，塞进本轮只会让「零新增红」失去说服力。

### 3.5 关停 / 生命周期 / 并发

- **不新增关停步骤。** 台账回写走 `intel_scheduler.schedule(...)`（既有 tracked 任务集），已被 `_shutdown_steps` 第 7 步 `drain_intel_tasks` 覆盖。`main._shutdown_steps()` **零改动**（上一轮的实施记录已把它列为冻结注册表）。
- **不新增锁。** `plan_alert` / `solve` / `find_conflicts` 全是纯函数，跑在既有的 `IntelRuntime._lock` 之内（采集轮）或 `build_snapshot` 之内。
- **会话侧的待处理告警不是共享状态**：`session._pending_intel_alert` 只被 `note_intel_alert`（同步回调，事件循环单线程）与 `_maybe_intel_wake`（同一循环的 tick）读写，无竞态。
- **续跑**：热续跑（`resume_group`，runtime 还在）无需处理——待处理告警在 `state != "running"` 时已被丢弃。冷续跑（进程重启）时 `ai_intel_alerts` 里可能留着 `fired=true, outcome=""` 的行，**不做启动期回填**（沿用「旧库自然补 NULL，不做不可靠历史回填」的成文纪律），由动态页签按 TTL 渲染成 `expired_no_response`。
- **`_alert` 回调在未装配（`enabled=False`）的 runtime 壳上是 no-op**：那种 runtime 不起任何后台任务，也不该产生任何告警。

---

## 四、接口设计

### 4.1 REST（新增 1 个端点，扩 **3** 个既有端点的返回 + 1 个写端点透传）

| # | 方法 / 路径 | 说明 |
|---|---|---|
| 1 | `GET /ai-trade/intel/{instId}/alerts?limit=&before=` | **新增**。告警台账，按 `id` 降序游标分页（游标是 `id` 不是时间戳——同一秒里的多行用时间戳翻页会漏行，沿用 `/citations` 的既有裁决）。返回 `{"alerts":[…],"hasMore":bool}`。⚠️ **本端点不挂 `response_model`**（评审 P2-9）：`/citations` 今天**是**挂着 `IntelCitationListOut` 的（`api/ai_intel.py:529`），故这里与它**刻意不同**——告警行 wire 上有 `reasons` 这个自由形状的判据明细字典，挂模型只会得到「加了新判据、界面上看不到、也不报错」。这与模块 docstring 第一条纪律（「读端点一律不挂 `response_model`」）一致 |
| 2 | `GET /ai-trade/intel/{instId}/impact` | **扩返回**：新增 `efficacy: {enabled, kMin, overall, byCategory:[…]}`。⚠️ **该端点挂着 `response_model=IntelImpactOut`（E-19），不扩模型 = 新键被静默吞掉**——本轮最容易踩的一处 |
| 3 | `GET /ai-trade/intel/{instId}/sources` | **扩返回**：每行新增 `confirmed` / `refuted` / `unresolved` / `accuracy`（`accuracy` 可为 `null` = 样本不足）。同样挂着 `response_model=IntelSourceListOut`，同一条警告 |
| 4 | `GET /ai-trade/intel/{instId}` | **扩返回**：新增 `conflicts: [{aId,bId,overlap,openRounds}]`。该端点**未挂** `response_model`（E-19），可直接加键 |
| 5 | `PUT /ai-trade/settings/intel` | 六把新键透传（§3.4.2） |

**路由注册顺序**：`/intel/{instId}/alerts` 比 `/intel/{instId}` 多一段，不会被劫持；但**任何新增的静态段端点必须排在 `read_intel` 之前**（E-20）。本轮不新增静态段端点，这条只作为实施期提醒。

**告警行 wire**（camelCase，`taxonomy` 单一真源）：

```jsonc
{
  "id": 812, "instId": "BTC-USDT-SWAP", "itemId": 4471, "roundId": 991,
  "createdTs": 1788110583, "severity": 0.82, "fired": true,
  "suppressedCode": "", "suppressedText": "",
  "outcome": "woke", "outcomeText": "已唤醒一轮决策",
  "outcomeTs": 1788110585, "runId": 37, "roundNo": 47,
  "headline": "某交易所热钱包异常出金 1.2 万枚 ETH",
  "category": "risk", "tier": "ultra", "stance": "bearish", "impact": "high",
  "domainCount": 3, "quoted": true, "confidence": 0.78, "ref": "I02",
  "reasons": {"impactW": 1.0, "freshness": 0.91, "ageMin": 16.4}
}
```

**绝不出现在 wire 上的东西**：URL、域名（`by_domain` 那张表除外——它本来就是关于域名的）、原文正文、检索源 API key。沿用 §3.9 的提示注入防护姿态。

### 4.2 WebSocket

**不新增事件类型。** 告警只经两条既有通道到达前端：

1. `AiTradeIntelSummary`（既有 `intel` 事件）新增两键：`alerts24h`（近 24 小时 fired 计数）与 `lastAlertTs`。它们进 `_static_summary`（未装配时也必须**形状一致**，否则前端读到 `undefined` 并把「没装上」画成「零告警」——上一轮 `citations` / `verify` / `quoteHitRate` 三次踩过同一个坑）。
2. 决策轮的 `status` / 轨迹事件里，`wake.kind === "intel_alert"` 与 `wake.intel` 已经把「这一轮为什么发生」带到了前端（§3.1.5 的 6/7 两处注册表）。

### 4.3 决策 prompt 的新增文本（两处，均可逐字节回退）

1. **唤醒句**（`wake_text._wake_intel_alert`）：**必须两态**（评审 P0-3）。
   - **在段内**（`in_digest=True`）：
     `本轮由一条高危市场情报触发（[I02]｜风险事件·偏空·高｜3 源｜16 分钟前）：某交易所热钱包异常出金 1.2 万枚 ETH。该情报已在下方情报段中列出；请在推理中明确说明你是否采信它、以及它如何改变（或不改变）你的结论。`
     无编号时去掉 `[I02]｜`。
   - **不在段内**（`in_digest=False`，包括 `inject_enabled=False` 与「被 `prompt_max_chars` 挤出去」两种情形）：**把摘要就地内联进唤醒句**，并如实说明它为什么不在下面——
     `本轮由一条高危市场情报触发（风险事件·偏空·高｜3 源｜16 分钟前）：某交易所热钱包异常出金 1.2 万枚 ETH —— <几句话摘要，按 summary_max_chars 封顶>。**该情报未出现在下方情报段中**（情报段已达长度上限或注入已关闭），本段就是你能看到的全部内容；请在推理中明确说明你是否采信它、以及它如何改变（或不改变）你的结论。`

   > **为什么必须两态。** `_TIER_RANK` 让注入排序 **long 在最前、ultra 在最后**（`disclosure.py:39`），而告警条目恒是刚采到的 ultra；`prompt_max_chars` 默认 1200，而单行上界 ≈ 244 / 典型 ≈ 190 字符（`headline_max_chars=40` + `summary_max_chars=160` + 固定部分 ≈44）⇒ 只装得下 **~5–6 行**，长期层默认上限却有 16 条。**长期层攒到 6 条 active 之后，告警条目就再也进不了 prompt 了**，而 v1 的唤醒句会照旧说「详见下方」。把一句确定错误的话逐字写进真实资金决策的 prompt，本仓库已经为此栽过（黑板 `v1:I-9`：「一个关于持仓周期的桶被描述成一个关于行情事件的事实……它不会报错，只会把一句不实的话逐字写进喂给模型的 prompt」）。
   > **刻意不做的替代方案**：给告警条目在 `build_digest` 里留一个置顶保留位。那会动到「唯一定稿点」的排序纪律（E-2），且让「注入看到的库存」与「REST 看到的库存」出现第二种排序——代价大于收益。本轮选择**如实说明**而不是**强行塞进去**，并把「高危告警可能不在情报段里」登记进 R-15。
2. **矛盾说明句**（§3.3.2 段末），`conflict_enabled=False` 时整句缺席。

**`ai_auto/prompts.py` 一个字节不改**：唤醒句住在 `wake_text.py`（`_format_wake` 只是三行委派，`prompts.py:632-641`），矛盾句住在 `intel/disclosure.py`（`_format_intel` 同样是三行委派，`prompts.py:824-836`）。**这是本轮能不触发 `prompt_sections.py` 那笔历史欠账的唯一原因**，§十 非目标 6 原样转记。

---

## 五、数据模型

### 5.1 新表 `ai_intel_alerts`（**本轮唯一的 schema 变更；零 ALTER 补列**）

```python
class AiIntelAlert(Base):
    """情报告警台账：一次告警判定的完整记录，含「没告警」与「告警了但没能唤醒」。

    为什么是新表而不是 ai_intel_items 上的几列：告警是**事件流**（同一条情报可能因冷却而被
    抑制多次），条目是**状态**。把事件流塞进状态行会让每次判定都变成一次读-改-写，而情报轮与
    决策轮跑在不同的 DB 会话里——读改写会丢更新，且丢更新不会报错（逐行照抄 AiIntelCitation
    的既有裁决）。

    ``create_all`` 会建这张缺失的表 ⇒ **零迁移**（本轮不给任何既有表补列）。
    """
    __tablename__ = "ai_intel_alerts"
    __table_args__ = (
        Index("ix_ai_intel_alert_scope", "inst_id", "id"),
        Index("ix_ai_intel_alert_item", "item_id"),
        Index("ix_ai_intel_alert_fired", "inst_id", "fired", "created_ts"),  # 冷却/日配额查询
    )
    id: int (pk, autoincrement)
    inst_id: str(32) idx = ""
    group_id: str(48) = ""
    item_id: int = 0                 # 0 = 无候选（空轮也写一行）
    round_id: int = 0                # 产生它的采集轮 ai_intel_rounds.id
    created_ts: int = 0              # 墙钟秒 UTC
    severity: float = 0.0
    fired: bool = False
    suppressed_code: str(24) = ""    # taxonomy.ALERT_SUPPRESS_CODES；fired=True 时恒为 ""
    outcome: str(24) = ""            # taxonomy.ALERT_OUTCOMES；"" = 尚无结果
    outcome_ts: int = 0
    run_id: int = 0                  # outcome="woke" 时指向那一轮决策
    round_no: int = 0
    headline: str(160) = ""          # 冗余快照：条目可能已被整理轮归档/合并，台账要能独立可读
    category: str(16) = ""; tier: str(8) = ""; stance: str(8) = ""; impact: str(8) = ""
    domain_count: int = 0; quoted: bool = False; confidence: float = 0.0
    ref_token: str(4) = ""           # "I02"；cite 关时为 ""
    reasons_json: Text = "{}"        # 判据明细（severity 的各分量、ageMin 等）
```

**为什么本轮刻意做到「零 ALTER」**（与 closed-loop 的 12 列、evidence-grounding 的 8 列对照）：

- **冷却时刻与日配额不落 watch 表**，改为从本表现算：`SELECT MAX(created_ts) WHERE inst_id=? AND fired=1` 与 `COUNT(*) WHERE inst_id=? AND fired=1 AND created_ts >= 当日 UTC 起点`。两条走 `ix_ai_intel_alert_fired` 的点查，代价可忽略，**换来的是没有第二份需要维护的账**——`ai_intel_watch` 上的 `day_key`/`day_rounds` 已经因为「谁写谁清」的语义在上一轮引出过缺陷（黑板 `CL-P0-3`：`bump_intel_watch` 是二值 catch-all）。
- **矛盾不落库**：它是当前库存的函数，持久化只会带来陈旧（§3.3.3 的 `open_rounds` 同理）。
- **战绩不落库**：`ai_intel_citations` 与 `ai_intel_items.verdict` 已经是真源，再落一份聚合就是第二个会漂移的账。

因此 `db/session.py:_ensure_ai_intel_columns` **一个字节不改**（E-16）——这也让 G-19「零新增红」的说服力不被迁移风险稀释。

### 5.2 `taxonomy.py` 新增值域（单一真源，前后端共用）

```python
ALERT_SUPPRESS_CODES = ("disabled", "no_candidate", "daily_cap", "cooldown",
                        "impact_below_bar", "category_out", "refuted",
                        "scheduled_future", "stale_event", "few_domains",
                        "low_confidence", "no_quote")
ALERT_OUTCOMES = ("woke", "deferred", "superseded", "dropped_not_running",
                  "dropped_feed_stale", "dropped_no_board", "dropped_expired")
ALERT_SUPPRESS_LABELS: dict[str, str] = {...}   # 中文，**后端下发**（进 wire）
ALERT_OUTCOME_LABELS: dict[str, str] = {...}    # 同上
MIN_IMPACTS = ("medium", "high")                # alert_min_impact 的值域
# ★ 今天**没有**这张表：动作枚举是手写在 `prompts.build_curate_tool_schema` 的 JSON schema
# 里（`prompts.py:111`），而 `gate.CurationAction.action` 只有一行注释镜像它（`gate.py:630`）。
# 本轮新建单一真源，并让 schema 从它生成；**元素顺序必须逐字保持**，否则工具 schema 的字节
# 会变（G-27 用 sha256 冻结「未加 keep_both 时 schema 与合入前逐字节相同」）。
#
# ⚠️ 评审 P0-4：**拆成两个名字，不是一个。** `keep_both` 是轴 C 的产物，而 §3.0 第 2 条要求
# `conflict_enabled=False` 时整理链路逐字节回到 4b9c352。一个无条件含 `keep_both` 的元组会让
# 那条自我约束在轴 C 上被本文自己破坏（工具 schema 的枚举多一项、模型可以发出一个必然被
# `keep_both_bad_pair` 驳回的动作、白占一个 `curate_max_actions` 名额）。
# 两个名字仍然只有一个真源：后者由前者拼出，**顺序天然保持**。
CURATE_ACTIONS_BASE = ("promote", "demote", "archive", "merge", "keep")
CURATE_ACTIONS = CURATE_ACTIONS_BASE + ("keep_both",)
REVIEW_REJECT_CODES = (..., "promote_weak_record", "keep_both_bad_pair", "keep_both_thin_note")
```

⚠️ 三个新的 `REVIEW_REJECT_CODES` **必须同步登记进 `CODE_LABEL`**，否则用户看到裸码（E-24；G-24 已有并集断言，本轮把新码纳入它的覆盖范围）。

> **`CODE_LABEL` 的归属定死在 `IntelRoundsTab.tsx`（评审 P2-5）。** v1 这里写「改名后的 `IntelActivityTab.tsx`」，而 §6.2 #36 又说 `IntelRoundsTab.tsx` **不删**且继续渲染轮次行——两句话互相矛盾，实施者会得到两份 `CODE_LABEL`。定稿：`CODE_LABEL` 留在 `IntelRoundsTab.tsx:10` 并 `export`，`IntelActivityTab.tsx` **import 它**；G-24 的前端一侧因此仍然只解析一个文件。

### 5.3 内存形状

| 结构 | 归属 | 生命周期 |
|---|---|---|
| `AlertCandidate` / `AlertDecision` | `intel/alert.py`，frozen dataclass | 单次采集轮 |
| 三组阈值（告警 10 + 战绩 4 + 矛盾 2） | **`taxonomy.IntelLimits` 的新字段**（评审 P1-2：**不新建 `AlertLimits` / `EfficacyLimits` / `ConflictLimits`**，否则 `taxonomy ↔ alert` 循环 import） | 每次 `limits_from_settings` |
| `AlertHandoff`（`alert_id` + `AlertCandidate` 投影 + `ref` + **`in_digest: bool`** + `created_wall`） | `intel/alert.py` | 从采集轮到会话起轮，至多 `alert_pending_ttl_s`。`in_digest` 由 `IntelRuntime._alert` 那一跳从**刚刷新的** digest 反查（评审 P0-3），决定 §4.3 唤醒句走哪一态 |
| `Efficacy` | `intel/efficacy.py`，frozen | **两处各求一次**：`build_snapshot`（供注入端与 REST，缓存在 `IntelRuntime._efficacy[inst_id]`）与 `run_curate_round`（供举证责任与溢出淘汰）。纯函数 ⇒ 两处同值，G-28 冻结（评审 P0-1） |
| `ConflictPair` | `intel/conflict.py`，frozen | 每次 `build_snapshot` 重算 |
| `session._pending_intel_alert: AlertHandoff \| None` | `ai_auto/session.py` | 至多一条，覆盖式 |

---

## 六、文件变更清单

> 顺序即实施顺序的粗粒度分组；细粒度顺序见 §九。「新」=新建，「改」=修改。**清单之外的任何文件被改动，都视为方案偏离，必须在实施报告里显式登记**（沿用前三轮的成文纪律）。

### 6.1 后端（新 5 / 改 22）

| # | 文件 | 新/改 | 一句话意图 |
|---|---|---|---|
| 1 | `services/quant/app/ai_auto/intel/alert.py` | 新 | 轴 A 纯核心：`AlertCandidate` / `AlertDecision` / `AlertHandoff` / `plan_alert` / `severity` （**阈值住 `taxonomy.IntelLimits`、`resolve_require_quote` 住 `taxonomy.py`**——评审 P1-2 / P2-8） |
| 2 | `services/quant/app/ai_auto/intel/efficacy.py` | 新 | 轴 B 纯核心：`Stat` / `Efficacy` / `solve` / `weight_for` / `accuracy` / `WEAK_RECORD_MIN_REASON_CHARS`（两级收缩，样本 0 ⇒ 权重 0、准确率 `None`）。⚠️ **只 import `taxonomy`，绝不 import `gate`**（评审 P1-3：否则 `gate ↔ efficacy` 成环） |
| 3 | `services/quant/app/ai_auto/intel/conflict.py` | 新 | 轴 C 纯核心：`find_conflicts` / `topic_tokens` / `overlap` / `CONFLICT_WINDOW_H` |
| 4 | `services/quant/app/db/ai_intel_alert_repositories.py` | 新 | `write_intel_alert` / `mark_intel_alert_outcome` / `list_intel_alerts` / `alert_cooldown_state` / `prune_intel_alerts` |
| 5 | `services/quant/app/api/schemas_intel_actionability.py` | 新 | `IntelAlertOut` / `IntelAlertListOut` / `IntelEfficacyOut` / `IntelConflictOut` |
| 6 | `services/quant/app/ai_auto/intel/__init__.py` | 改 | 导出 `note_alert_outcome`、三个新纯核心的公开面 |
| 7 | `services/quant/app/db/models.py` | 改 | 新增 `AiIntelAlert`（**唯一 schema 变更**；`db/session.py` 零改动） |
| 8 | `services/quant/app/ai_auto/intel/taxonomy.py` | 改 | §5.2 的六组新值域与两张中文标签表（含 `CURATE_ACTIONS_BASE` / `CURATE_ACTIONS` 两个名字）；**`IntelLimits` 追加 16 个带默认值的字段（告警 10 + 战绩 4 + 矛盾 2）**；`limits_from_settings` 追加对应映射；新增 `resolve_require_quote`（与 `resolve_quote_mode` 并列）。⚠️ 评审 P1-2：**`limits_from_settings` 实读住在本文件（`taxonomy.py:316`），v1 §6.1 #12 把它记成了 `service.py`** |
| 9 | `services/quant/app/ai_auto/intel/gate.py` | 改 | `score_item` 加 `eff=None` 形参（默认逐字节回退，内部只读 `eff.weights.get(item.category, 0.0)`）；**`review_actions` 的 promote 分支**新增 `promote_weak_record`（评审 P0-2：**`_review_promote` 的签名与函数体一个字节不改**——它的入参里根本没有 `reason`）；`review_actions` 新增 `eff=None` 形参；新增 `_review_keep_both` |
| 10 | `services/quant/app/ai_auto/intel/cleanup.py` | 改 | `plan_cleanup` 透传 `eff` 给 `score_item`（**不新增第二套打分**）；`prune_intel_alerts` 的调用点 |
| 11 | `services/quant/app/ai_auto/intel/disclosure.py` | 改 | `build_digest` 两遍预算 + 矛盾重排与标注；`format_intel` 段末矛盾说明句 |
| 12 | `services/quant/app/ai_auto/intel/service.py` | 改 | `_persist_collect` 算 `AlertDecision` + 写台账（**同一事务、同一次 commit**，评审 P1-5）；`run_collect_round` 拼 merges 候选投影（评审 P1-4）并挂 `RoundOutcome.alert`；`build_snapshot` 求解 `Efficacy` / `find_conflicts` 并下发摘要；**`run_curate_round` 自解一次 `Efficacy` 并传给 `review_actions` 与 `plan_cleanup`**（评审 P0-1）；`INTEL_SETTING_BRIDGE` +6 行。⚠️ **`limits_from_settings` 不在本文件**（见 #8）；`render_listing` **不透传 `eff`**（见 §3.2.3 的四调用点表） |
| 13 | `services/quant/app/ai_auto/intel/prompts.py` | 改 | 整理轮 user prompt 的「未裁决矛盾对」段；system prompt 的裁决纪律；`curate` 工具 schema 的 `keep_both` |
| 14 | `services/quant/app/ai_auto/intel/runtime.py` | 改 | `notify_alert` 回调、`_after_round` 的第二跳（**并在这一跳算出 `AlertHandoff.in_digest`**，评审 P0-3）、`note_alert_outcome` **模块级同步函数**（内部 `scheduler.schedule(...)`，逐行照抄 `invalidate` 的既有形状，`runtime.py:250-262`——评审 P1-10）、`_efficacy` 缓存、`_static_summary` 的两个新键 |
| 15 | `services/quant/app/ai_auto/manager.py` | 改 | `_on_intel_alert` 薄转发（镜像 `_on_intel_summary`）+ `_wire_intel` 多传一个 `notify_alert` |
| 16 | `services/quant/app/ai_auto/session.py` | 改 | `note_intel_alert`（同步 setter）+ `_maybe_intel_wake`（≤35 行，判定下沉到 `wake_runtime`）+ tick 里一行调用 + `:65` 的 import 行补 `note_alert_outcome`（评审 P1-10） |
| 17 | `services/quant/app/ai_auto/wake_reason.py` | 改 | `WAKE_KINDS` 第 2 位插 `intel_alert`；`WakeFacts.intel`；`conditions` 与 wire 的 `intel` 键 |
| 18 | `services/quant/app/ai_auto/wake_runtime.py` | 改 | `plan_intel_alert`（镜像 `plan_signal_flip`）+ `classify(intel=…)` + `intelAlertWakes/Drops` 两个计数 |
| 19 | `services/quant/app/ai_auto/wake_text.py` | 改 | `_ALSO_TRUE_LABEL`（:16）与 `_WAKE_BODY`（:171）各加一行；新增 `_wake_intel_alert` 渲染器 |
| 20 | `services/quant/app/ai_auto/tactical_context.py` | 改 | `_FULL_TIER_KINDS` 加 `intel_alert`（`_EXECUTION_FULL_KINDS` 不加） |
| 21 | `services/quant/app/ai_auto/experience/taxonomy.py` | 改 | `VALUE_LABEL_ZH` 加 `intel_alert` |
| 22 | `services/quant/app/db/repositories.py` | 改 | 转发新仓库函数（沿用既有 re-export 范式）；`read_intel_impact` / `list_intel_sources` 带上新读数 |
| 23 | `services/quant/app/api/ai_intel.py` | 改 | 新增 `GET /intel/{instId}/alerts`；`/impact`、`/sources`、`/{instId}` 三处扩返回 |
| 24 | `services/quant/app/api/schemas_intel_closed_loop.py` | 改 | `IntelImpactOut`（:102）扩 `efficacy` 键、`IntelSourceRowOut`（:74）扩四个信誉列（E-19，不扩即静默吞） |
| 25 | `services/quant/app/api/schemas.py` | 改 | `PUT /settings/intel` 的六把新键 |
| 26 | `services/quant/app/config.py` | 改 | 18 项新设置 + 两条启动期校验器（§十一） |
| 27 | **仓库根 `.env.example`** | 改 | 18 项登记。⚠️ **不是 `services/quant/.env.example`**（评审 P1-1：该目录下只有 `.env`；门禁自己写死 `parents[3] / ".env.example"`，见 `tests/test_ai_intel_wiring.py:447`）。该文件历史上两次静默漏登记，现有双向对齐门禁会当场红，E-23 |

### 6.2 前端（新 5 / 改 14）

| # | 文件 | 新/改 | 一句话意图 |
|---|---|---|---|
| 28 | `apps/web/components/Intel/IntelActivityTab.tsx` | 新 | 轮次 + 告警合流时间线（吸收 `IntelRoundsTab` 的渲染） |
| 29 | `apps/web/components/Intel/IntelAlertRow.tsx` | 新 | 告警行三态渲染 + 同码折叠 + 跳轨迹 |
| 30 | `apps/web/components/Intel/IntelEfficacyBlock.tsx` | 新 | 影响力页签的「战绩与信誉」块（含「样本 3/8，先验未生效」的诚实文案） |
| 31 | `apps/web/components/Intel/IntelConflictBadge.tsx` | 新 | 卡片矛盾徽标 + 悬浮说明对方标题 |
| 32 | `apps/web/lib/intelActionability.ts` | 新 | 三条轴的 wire 类型与纯函数（折叠、排序、准确率格式化） |
| 33 | `apps/web/lib/intelTypes.ts` | 改 | `IntelAlert` / `IntelEfficacy` / `IntelConflict` 类型；`AiTradeIntelSummary` 两个新键 |
| 34 | `apps/web/lib/intelApi.ts` | 改 | `fetchIntelAlerts` |
| 35 | `apps/web/components/Intel/IntelModal.tsx` | 改 | `TabKey` 的 `"rounds"` → `"activity"`、页签文案「整理记录」→「动态」（评审 P2-4：v1 一直称它「轮次」，实际标签见 `IntelModal.tsx:211`）；矛盾筛选 chip 的状态提升；**净增 ≤ 40 行** |
| 36 | `apps/web/components/Intel/IntelRoundsTab.tsx` | 改 | 保留纯轮次渲染，被 `IntelActivityTab` 复用（**不删**，避免一次大搬运把 diff 撑爆）；**`CODE_LABEL` 留在本文件并 `export`**（评审 P2-5：单一真源，`IntelActivityTab` import 它） |
| 37 | `apps/web/components/Intel/IntelCard.tsx` | 改 | 挂 `IntelConflictBadge` |
| 38 | `apps/web/components/Intel/IntelList.tsx` | 改 | 「仅看冲突（N）」筛选 |
| 39 | `apps/web/components/Intel/IntelImpactTab.tsx` | 改 | 追加 `IntelEfficacyBlock` |
| 40 | `apps/web/components/Intel/IntelSourcesTab.tsx` | 改 | 三列 + 按准确率排序 + 「样本不足」态 |
| 41 | `apps/web/components/Settings/IntelSourceSettings.tsx` | 改 | 告警卡片（四个旋钮 + `require_quote` 置灰与原因）+ 两把总闸 |
| 42 | `apps/web/lib/aiTradeTypes.ts` | 改 | `WakeKind` 加值（:134-145）、新增 `WakeIntelInfo`、**两份统计形状**（:1219-1224 与 :1287-1290）各加两个计数 |
| 43 | `apps/web/lib/aiTrade.ts` | 改 | **`WAKE_LABELS`**（:1148）加一行（评审 P2-1：真名不是 `WAKE_KIND`；它是 `Record<WakeKind, …>` ⇒ 漏掉即 tsc 红，不是静默） |
| 44 | `apps/web/lib/experience.ts` | 改 | 标签表 + `AXIS_VALUES.wake` 各加一值 |
| 45 | `apps/web/lib/aiTradeHistoryTypes.ts` | 改 | `metrics_json.aiStats.wakeScheduler` 的两个新计数。⚠️ 评审 P1-7：**这是纯声明位**——后端 `session.py:3138` 整体赋值 `self._wake.stats(...)`，新键天然落库；本文件今天在 `apps/web` 里**没有任何读点**。补它是为了类型与 wire 不分叉，**不是**防丢失措施 |
| 46 | `apps/web/components/Agent/aiTradeStats/AiTradeStatsLive.tsx` | 改 | 两个新计数的唯一展示点（沿用既有 `if (x) parts.push(...)` 范式，零值不显示） |

### 6.3 桌面壳

**零改动。** 本轮不新增任何外链、不新增任何窗口、不改任何 IPC。沿用 closed-loop 的做法，只在既有的配置透传用例里补一条断言：18 项新设置能从桌面壳的环境注入穿到后端（`desktop/scripts/*.test.mjs` 的既有用例扩一行数据）。

### 6.4 文档

| # | 文件 | 意图 |
|---|---|---|
| 47 | `docs/plans/ai-trade-intel-actionability/spec.md` | 本文件 |
| 48 | `README.md` 的 AI 自动交易段 | 追加一句「情报告警可触发额外决策轮」与开关位置 |
| 49 | `docs/plans/ai-trade-market-intel/spec.md` §十 非目标 | 追加一行指向本轮（三轮非目标的转记链保持连续） |

---

## 七、测试与验收

### 7.1 验收总闸（G-1 … G-31；G-28…G-31 为评审新增）

| 编号 | 断言 | 落点 |
|---|---|---|
| **G-1** | **轴 A 逐字节回退**：`alert_enabled=False` 时，跑 20 轮采集，`classify_wake` 的 wire 无 `intel` 键、`WAKE_KINDS` 判定与 `4b9c352` 的 `sha256` 相同、`_pending_intel_alert` 恒为 `None` | `test_ai_intel_alert_rollback.py` |
| **G-2** | **轴 B 逐字节回退**：`efficacy_enabled=False` ⇒ `score_item(item, now=…, limits=L)` 与不传 `eff` 的返回值**浮点恒等**；四个调用点各断言一次 | `test_ai_intel_efficacy.py` |
| **G-3** | **轴 C 逐字节回退（★ 评审 P0-4：四件产物，不是一件）**：`conflict_enabled=False` ⇒ ① `build_digest` 产物、② `build_curate_tool_schema(limits)` 的 `json.dumps(sort_keys=True)`、③ `build_curate_system_prompt`、④ `build_curate_user_prompt` **四者的 `sha256` 全部与合入前相同**（①含 `lines` / `hidden` / `refs`）。只冻结 ① 是看不见「`keep_both` 无条件进枚举」这条的 | `test_ai_intel_conflict.py` |
| **G-4** | **告警判定的 12 条抑制码各有一条用例**，每条构造一个只差那一个判据的候选，断言 `suppressed_code` 精确等于目标码（**不是「不为空」**） | `test_ai_intel_alert_plan.py` |
| **G-5** | **一轮至多一条**：两条同样够格的候选 ⇒ `fired=True` 的行恰好 1 行，且是 `severity` 更高的那条 | 同上 |
| **G-6** | **`require_quote` 的降级**：`quote_mode="off"` 时 `resolve_require_quote()` 返回 `False`，**且 `app_settings` 里存的偏好仍是 `True`**（存储不被改写）；`reader_enabled=False` 同理 | 同上 |
| **G-7** | **端到端唤醒**：造一条必然 fired 的候选 → 采集轮 → `_maybe_intel_wake` → 断言 `_run_decision_round` 被调用一次且 `wake["kind"] == "intel_alert"`、`wake["intel"]["itemId"]` 正确、`alsoTrue` 含 `bar_advanced`（若成立）；**并断言 `wake["trigger"] == "interval"`** ⇒ `candle_close` 条件恒不成立 ⇒ `intel_alert` 在自己认领的那一轮里恒为首位（Q2 裁决的算术依据） | `test_ai_trade_intel_wake.py` |
| **G-8** | **新 kind 不可被跳过**：`should_skip_wake({"kind":"intel_alert",…}, 任意 policy)` 恒为 `False` | `test_ai_trade_wake_guard.py`（扩既有） |
| **G-9** | **注册表并集（评审 P2-2 改落点）**：① 后端在 `test_ai_trade_wake_reason.py` 里断言 `WAKE_KINDS` 与 `conditions` / `_WAKE_BODY` / `_ALSO_TRUE_LABEL ∪ {heartbeat}` / `VALUE_LABEL_ZH` 四者一致，并单列 `"intel_alert" in _FULL_TIER_KINDS and not in _EXECUTION_FULL_KINDS`；② 前端在**既有**的 `lib/experienceLabels.source.test.ts` 里**追加一个 `it`**，从 `wake_reason.py` 抽 `WAKE_KINDS` 并断言 `AXIS_VALUES.wake` ⊇ 它 ∪ `{"unknown"}`（#9 是唯一真正静默的一处；#6/#7 由 tsc、#5+#8 由该文件既有的双向断言兜住）。**不新建 `wakeKindRegistry.source.test.ts`**——同一件事两处判据，第二天就会有一处被改坏而没人发现。⚠️ 正则写 `\r?\n`（`core.autocrlf=true`） | `test_ai_trade_wake_reason.py`（扩既有）+ `apps/web/lib/experienceLabels.source.test.ts`（扩既有） |
| **G-10** | **五条会话侧抑制路径各写一次台账**：`not_running` / `feed_stale` / `no_board` / `expired` / `deferred`，逐条断言 `outcome` 与「待处理是否被清空」 | `test_ai_trade_intel_wake.py` |
| **G-11** | **台账两写点闭合**：从 **REST 输出**（不是 DB）读到 `outcome=="woke"`、`runId`/`roundNo` 指向那一轮 | `test_ai_intel_alerts_api.py` |
| **G-12** | **冷却与日配额从台账现算**：连发两条够格候选，第二条 `cooldown`；把 `daily_cap` 设为 1 后第二条 `daily_cap`；**断言 `ai_intel_watch` 上一列都没被改** | `test_ai_intel_alert_plan.py` |
| **G-13** | **`eff` 只透传到该传的两处**：`build_digest` 与 `plan_cleanup` 对同一批条目排序一致且确实受先验影响；`gate.admit` 的 over_cap 与 `service.render_listing` 的排序与合入前**逐字节相同**（`sha256`） | `test_ai_intel_efficacy.py` |
| **G-14** | **样本不足 ⇒ 中性**：`n < k_min` 时 `weight_for` 返回严格 0.0；`accuracy()` 在分母 0 时返回 `None`（**不是 0.0**），REST 上是 `null` | 同上 |
| **G-15** | **告警开关不是假开关**：`PUT /settings/intel {alertEnabled:false}` → 跑采集轮 → 断言 handoff 未发出、台账 `suppressed_code=="disabled"`、且**会话侧代码里不存在对 `ai_trade_intel_alert_enabled` 的第二处读取**（源码扫描） | `test_ai_intel_settings_bridge.py` + `sessionNoIntelSwitchReread.source.test`（py 侧正则扫描） |
| **G-16** | **六键逐键往返**：每一把新键 `PUT` 之后 `resolve_intel_settings` 的产物上该字段确实变了；`alertMinImpact` 写入非法值时被丢弃而不是穿透 | `test_ai_intel_wiring.py`（扩既有） |
| **G-17** | **两个新计数端到端（评审 P1-7 改断言对象）**：在**后端**断言 `status_counts()` 与 `metrics_json["aiStats"]["wakeScheduler"]` 两处 `intelAlertWakes` 同值；在**前端**断言 `AiTradeStatsLive.describeWakeScheduler` 能把它渲染出来（零值不显示）。**不在 `aiTradeHistoryTypes.ts` 上断言**——那是一处今天就没有任何读点的纯声明位，断言它只会得到一条空闸 | `test_ai_trade_wake_context_session.py`（扩既有）+ `AiTradeStatsPanel.test.tsx` |
| **G-18** | **矛盾检测四条判据**：同类别/反立场/主题重叠/时间窗，每条各造一个只差它的反例；`refuted` 一方**自动解除** | `test_ai_intel_conflict.py` |
| **G-19** | **两遍预算永不超限**：构造 40 条含 8 对矛盾的库存 + 极小 `prompt_max_chars`，断言 `sum(len(line)+1) <= prompt_max_chars`、`hidden` 口径正确、**不存在指向未收进 `lines` 的编号的标注** | 同上 |
| **G-20** | **矛盾对相邻（评审 P1-8 收窄）**：**同层**矛盾对重排后两端在 `lines` 里下标相差 1（两端都被收进时）；**跨层**矛盾对**不重排**，只断言两端都被收进时各自带标注。另加一条反例断言：一条 long 层条目**不会**因为与某条 ultra 条目矛盾而被移进 ultra 块 | 同上 |
| **G-21** | **`keep_both` 的三条服务端判据**：非本轮矛盾对 ⇒ `keep_both_bad_pair`；短理由（< `KEEP_BOTH_MIN_REASON_CHARS`，新常量默认 20，写在 `conflict.py` 里）⇒ `keep_both_thin_note`；通过时 **tier 与 status 一个字节不变**，只写 `curation_note` / `last_curated_ts` | `test_ai_intel_curate_conflict.py` |
| **G-22** | **`conflictsResolved` 由服务端重算**：模型自报「已解决 3 对」而实际只解决 1 对 ⇒ 轮存根记 1 | 同上 |
| **G-23** | **`response_model` 未吞键**：`GET /impact` 的返回里 `efficacy` 在位、`GET /sources` 的返回里 `accuracy` 在位（**从 HTTP 响应体断言，不是从仓库函数返回值**） | `test_ai_intel_alerts_api.py` |
| **G-24** | **码表并集**（扩既有）：`ALERT_SUPPRESS_CODES ∪ ALERT_OUTCOMES ∪ 三个新 REVIEW_REJECT_CODES` ⊆ 前端 `CODE_LABEL ∪ 后端下发文案` | `IntelActivityTab.test.tsx` + `test_ai_intel_taxonomy.py` |
| **G-25** | **静态 summary 形状一致**：未装配分组的 `AiTradeIntelSummary` 上 `alerts24h`/`lastAlertTs` **在位**（0 / `null`），前端不读到 `undefined` | `test_ai_intel_service.py`（扩既有）+ `useAiTradeGroupStream.intel.test.ts` |
| **G-26** | **关停不泄漏**：起一轮告警回写任务后立刻 `drain_intel_tasks()`，断言 `intel_scheduler.tasks()` 收敛为空且不抛；`main._shutdown_steps()` **不新增步骤，且 `ai_trade_intel` 仍是第 7 项**（评审 P2-12：**不要断言「逐字节未变」**——该函数的 docstring 明写「本表当前 14 步……这里不再写死一个数字」，`main.py:89-92`，注释本身是会动的） | `test_ai_intel_alert_shutdown.py`（新）+ `test_main_shutdown.py`（扩既有） |
| **G-27** | **工具 schema 单一真源不改字节**：把 `action` 枚举改为从 `taxonomy.CURATE_ACTIONS_BASE` / `CURATE_ACTIONS` 生成后，**`conflict_enabled=False` 时** `build_curate_tool_schema(limits)` 的 `json.dumps(sort_keys=True)` 与合入前 `sha256` 相同；并断言 `CURATE_ACTIONS[:5] == CURATE_ACTIONS_BASE`（顺序即字节） | `test_ai_intel_curate_conflict.py` |
| **G-28** | **★ 评审 P0-1：战绩先验在整理轮里真的算出来了。** ① 对同一批库存，`build_snapshot` 与 `run_curate_round` 求出的 `Efficacy` **逐字段相等**（纯函数性质，不是巧合）；② 造一个历史战绩为负的类别 + 一条 20 字以下理由的升长期动作 ⇒ 驳回码精确等于 `promote_weak_record`；③ 同一动作把理由写足 ⇒ 通过。**②必须从 `run_curate_round` 这条真实路径上跑**，直接调 `review_actions(eff=…)` 的用例会掩盖「整理轮根本拿不到 `eff`」这个根因 | `test_ai_intel_efficacy.py` + `test_ai_intel_curate_conflict.py` |
| **G-29** | **★ 评审 P0-3：唤醒句绝不说假话。** ① 造一个长期层塞满、`prompt_max_chars` 只装得下长期层的库存 ⇒ 告警条目落进 `hidden` ⇒ 断言 `AlertHandoff.in_digest is False` 且唤醒句**不含**「已在下方情报段中列出」、**含**摘要正文；② `inject_enabled=False` ⇒ 同样走内联态；③ 正常库存 ⇒ `in_digest is True` 且唤醒句里的 `[Ixx]` 确实出现在 `payload["intel"]["refs"]` 里。**断言对象是渲染后的 prompt 字符串，不是 handoff 字段** | `test_ai_trade_intel_wake.py` |
| **G-30** | **★ 评审 P0-4：轴 C 总闸关掉时整理链路不多一个字节。** `conflict_enabled=False` ⇒ `build_curate_tool_schema` 的 `action.enum` **不含** `keep_both`、**不含** `pairId` 属性；system prompt 无裁决纪律段；user prompt 无矛盾段。打开时四者同时出现 | `test_ai_intel_curate_conflict.py` |
| **G-31** | **★ 评审 P1-4：merges 分支能触发告警。** 造一条已在库、`domain_count=1` 的高危条目，再跑一轮只产出 `MergeOp`（第二个域名再次报道）的采集轮 ⇒ 断言告警 `fired=True` 且台账行的 `headline` / `category` / `impact` 取自**旧条目**、`domainCount` 取自**合并后的新值**。反例：若实现只投影 `accepted`，本条必红 | `test_ai_intel_alert_plan.py` + `test_ai_intel_alerts_api.py` |

### 7.2 测试文件规划

**新增 pytest（9）**：`test_ai_intel_alert_plan.py`、`test_ai_intel_alert_severity.py`、`test_ai_intel_alert_rollback.py`、`test_ai_intel_alert_shutdown.py`、`test_ai_trade_intel_wake.py`、`test_ai_intel_alerts_api.py`、`test_ai_intel_efficacy.py`、`test_ai_intel_conflict.py`、`test_ai_intel_curate_conflict.py`。
**改既有 pytest（8，全部已在 `services/quant/tests/` 里实读确认存在）**：`test_ai_trade_wake_reason.py`、`test_ai_trade_wake_guard.py`、`test_ai_trade_wake_text.py`、`test_ai_trade_wake_context_session.py`、`test_ai_intel_taxonomy.py`、`test_ai_intel_wiring.py`（`.env.example` 双向对齐 + 设置桥往返）、`test_ai_intel_service.py`、`test_main_shutdown.py`。

> ⚠️ **不要发明「扩既有」的文件名**：`test_ai_intel_settings_bridge.py` / `test_ai_intel_runtime.py` / `test_ai_intel_shutdown.py` / `test_ai_trade_status_wire.py` **在本仓库都不存在**。设置桥往返落在 `test_ai_intel_wiring.py`（它已经是「装配 + 登记」这一类的归属地），runtime 快照落在 `test_ai_intel_service.py`。清单里每一个「改既有」的路径都必须先 `ls` 确认。
**新增 vitest（5）**：`IntelActivityTab.test.tsx`、`IntelAlertRow.test.tsx`、`IntelEfficacyBlock.test.tsx`、`IntelConflictBadge.test.tsx`、`intelActionability.test.ts`。
**新增源码扫描（0，评审 P2-2）**：G-9 的前端一侧改为**扩既有**的 `apps/web/lib/experienceLabels.source.test.ts`，**不新建** `wakeKindRegistry.source.test.ts`——同一个 Python 常量被两条源码扫描用例各解析一次，是本轮自己要修的那类漂移。
**改既有 vitest（6，全部已实读确认存在）**：`IntelModal.test.tsx`、`IntelSourcesTab.test.tsx`、`IntelImpactTab.test.tsx`、`components/Agent/AiTradeStatsPanel.test.tsx`、`lib/useAiTradeGroupStream.intel.test.ts`、`lib/experienceLabels.source.test.ts`（G-9 前端一侧）。

### 7.3 跑测纪律（本仓库成文教训，逐条照抄）

- 后端全量 `python -u -m pytest`（**`-u`**：不加时 Windows 控制台缓冲会让长跑看起来像挂死）；**不并发**跑 `services/quant` 的全量（多个用例共用同一个临时 DB）。
- 前端 component 用例必须 `NODE_ENV=test`（否则 React `act()` 报错）。
- **`-k` 子集跑法不可用于判回归**（成文教训）：终审必须跑全量。
- 入轮前先记基线红集合（当前已知：未跟踪在制文件 `test_factor_library_expansion.py` 的 7 条），**新增红必须为 0**。

---

## 八、风险与缓解

| # | 风险 | 等级 | 缓解 |
|---|---|---|---|
| **R-1** | **告警把交易变频繁**，唤醒次数上升带来更多开仓机会与更多手续费 | 高 | 判据门槛高（high + 三类别 + 引文 + ≥2 源 + 置信 ≥0.6 + ≤3h）；`daily_cap=6` + `cooldown=20min` 双封顶；**每一轮仍走完全部八道门禁**；台账让「今天多跑了几轮、其中几轮真的开了仓」可查 |
| **R-2** | **误报唤醒**：一条被后来证伪的情报触发了一轮决策 | 中 | 唤醒是「多问一次 AI」而不是「下单」；核查轮会把它标 `refuted` 并停止注入；台账保留 `itemId`，`GET /alerts` 上可以看到该告警对应的条目**后来被证伪了** |
| **R-3** | **`intel_alert` 落进 `_EXECUTION_FULL_KINDS`** 或被误加进跳过集合 | 中 | §3.1.5 表格 #4 明写「不加」；G-8 冻结不可跳过 |
| **R-4** | **注册表漏一处**（评审 P2-1 / P2-2 收窄：九处里真正静默的是后端 #2/#3/#4 与前端 #9 共**四处**，其余五处由 tsc 或既有门禁兜住） | 中 | G-9 的两侧断言（后端并集 + 前端扩既有 `experienceLabels.source.test.ts`）；§3.1.5 逐处标注了各自的失效方式；注册表本身在 E-9/E-10 有历史前科（一次笔误留下的物理防线） |
| **R-5** | **`response_model` 静默吞键**（E-19） | 高 | G-23 从 HTTP 响应体断言而不是仓库函数返回值；§6.1 #24 单列一行提醒 |
| **R-6** | **战绩先验把一条正确但历史不佳类别的情报压到看不见** | 中 | 权重被 `max_weight=0.25` 硬封顶且只影响**排序**；两级收缩使样本不足时恒为 0；四个消费点里**没有一个是门禁** |
| **R-7** | **矛盾检测误判**（两条讲不同事情的情报被判为矛盾）；**以及同层重排挤掉一条更好的独立情报** | 中 | 四条判据合取 + 自适应停用表 + `max_pairs=6`；**误判的后果上限是「prompt 里多一句提示」**，不删除、不降级、不停注入；用户可在卡片上看到对方标题自行判断。重排的代价（评审 P1-8 定稿后**只限同层内部**）：同层里一条低分条目可能被提到高分条目旁边，从而挤掉同层末尾一条更好的独立情报——上界是 `max_pairs=6` 条，且 Q4 已明确接受这个取舍。**跨层不重排**，故不会出现「一条长期条目挤掉一批超短条目」这种量级完全不同的损失 |
| **R-8** | **两遍预算实现错误导致 prompt 超长**，触发 LLM 的上下文上限 | 中 | 预留是**上界**（常量 24 字符 ≥ 任何标注实际长度）；G-19 用极小预算 + 40 条库存做压力断言 |
| **R-9** | **台账无界增长** | 低 | `alert_retention_d=30` 由整理轮的既有清理点顺带 prune；一个标的每天至多 `daily_cap` 条 fired + 每轮 1 条判定行，30 天上界约 1500 行 |
| **R-10** | **告警回写任务在关停时被 cancel，留下 `outcome=""` 的行** | 低 | 渲染期按 TTL 判为 `expired_no_response`，**不回写**；G-26 断言 drain 不抛 |
| **R-11** | **`build_snapshot` 变慢**（多一次聚合查询 + 两次纯函数求解） | 低 | 不在决策热路径上（热路径读的是进程内快照）；查询走既有索引且被 `cite_retention_d` 封顶；`conflict` 的复杂度是 O(n²) 但 n 被三层 active 上限（40+24+16）封顶为 80 ⇒ 至多 3160 次 Jaccard |
| **R-12** | **`session.py` 与 `IntelModal.tsx` 继续膨胀**（3264 行 / 553 行，前者早已越线） | 中 | 会话侧只加 ≤35 行且判定下沉到 `wake_runtime`；弹窗只做路由、净增 ≤40 行，新内容全在新组件里。**阈值不放宽**，既有欠账原样登记（§十二） |
| **R-13** | **回测车道** 被新 kind 影响 | 低 | 回测不装配情报层（`provider is None` ⇒ runtime 壳），`intel_digest_for` 恒 `None`，告警链路整条不存在；G-1 的回退断言覆盖 |
| **R-14** | **提示注入**：告警的 `headline`（以及 `in_digest=False` 时的 `summary`）进 prompt | 中 | `headline` 与 `summary` 早在采集轮就过了 `imperative_text` 闸与长度闸（`REJECT_CODES` 的闸⑥），**内联态不引入任何未过闸的新文本**——它只是把情报段里那一条搬到唤醒句里。台账 wire 不含 URL / 域名 / 正文 |
| **R-15** | **高危告警可能根本不在情报段里**（评审 P0-3 的**残余**风险）：`_TIER_RANK` 让 ultra 排最后，`prompt_max_chars` 默认 1200 只装得下 **~5–6 行**（单行典型 ≈190 字符），长期层默认上限 16 条 | 中 | 唤醒句**如实分两态**（§4.3），内联态把摘要就地带进 prompt 并写明「未出现在下方情报段中」，G-29 冻结。**刻意不给告警条目在 `build_digest` 里留置顶保留位**——那会动到唯一定稿点的排序纪律（E-2）并让 REST 与注入的排序分叉。用户可在设置里调高 `prompt_max_chars`（上限 6000）；动态页签的告警行显示「已告警 · 未进入情报段」以便发现这件事 |

---

## 九、实施顺序（12 步，**不得压缩**）

0. **（评审新增，不可省）** `taxonomy.py` 的 `IntelLimits` 追加 16 个带默认值的字段 + `limits_from_settings` 的映射 + `resolve_require_quote`。**必须排在第 1 步之前**：三个纯核心模块的签名全部收 `IntelLimits`，先写它们就要先造一个假的 Limits，而那个假 Limits 会活到评审阶段（评审 P1-2）。此时跑一次全量 pytest，**应当完全绿**——16 个新字段全带默认值，既有构造零改动，这一刻的绿是「阈值扩容本身零回归」的唯一证据。
1. **纯核心先行**：`intel/alert.py` + `intel/efficacy.py` + `intel/conflict.py` 三个模块与它们的 pytest（`test_ai_intel_alert_plan.py` / `test_ai_intel_alert_severity.py` / `test_ai_intel_efficacy.py` / `test_ai_intel_conflict.py`）。此时不接任何线，**四个用例文件必须先绿**。⚠️ `efficacy.py` **不 import `gate`**（P1-3），三者**都不被 `taxonomy` import**（P1-2）——写完先跑一次 `python -c "import app.ai_auto.intel.taxonomy"` 确认没有环。
2. `taxonomy.py` 的六组值域与两张标签表（含 `CURATE_ACTIONS_BASE` / `CURATE_ACTIONS` 两个名字）+ `test_ai_intel_taxonomy.py`。
3. `db/models.py` 的 `AiIntelAlert` + `db/ai_intel_alert_repositories.py` + 仓库层用例（含 `alert_cooldown_state` 的当日边界）。**确认 `db/session.py` 零改动**。
4. `config.py` 的 18 项 + 两条启动期校验器 + `.env.example` 登记 → 立刻跑 `.env.example` 双向对齐门禁（它会当场红，正是要它红）。
5. `service.py`：`_persist_collect` 的告警段（**同一事务**，P1-5）→ merges 候选投影（P1-4）→ `RoundOutcome.alert` → `build_snapshot` 的 efficacy/conflict 求解 → **`run_curate_round` 自解一次 `Efficacy`**（P0-1）→ `INTEL_SETTING_BRIDGE` +6 行。（`limits_from_settings` 已在第 0 步改完，本步不碰 `taxonomy.py`。）
6. `gate.py` / `cleanup.py` / `disclosure.py` 的四个 `score_item` 调用点与两遍预算 + G-2/G-3/G-13/G-19/G-20。
7. `runtime.py` + `manager.py` 的两跳转发 + `note_alert_outcome` + `_static_summary` 两键 + G-25。
8. **唤醒栈**：`wake_reason.py` → `wake_runtime.py` → `wake_text.py` → `tactical_context.py` → `experience/taxonomy.py`，**五处后端注册表一次改完**，跑 G-8/G-17。
9. `session.py` 的 `note_intel_alert` + `_maybe_intel_wake` + tick 一行 + G-7/G-10/G-15。
10. `prompts.py` 的整理轮矛盾段（**三处同条件缺席**，P0-4）+ `gate._review_keep_both` + `review_actions` 的 `promote_weak_record`（**落在 promote 分支，不动 `_review_promote`**，P0-2）+ G-21/G-22/G-28/G-30。
11. **API 层**：`api/ai_intel.py` 的新端点与三处扩返回 + `schemas_intel_closed_loop.py` 扩模型 + `schemas_intel_actionability.py` + G-11/G-16/G-23。
12. **前端**：类型 → lib → 四个新组件 → 六处改 → 四处注册表 → **扩既有** `lib/experienceLabels.source.test.ts`（G-9 前端一侧，**不新建**`wakeKindRegistry.source.test.ts`）→ `tsc` + vitest 全量 → 后端 pytest 全量 → desktop node:test。

> **第 8 步不可与第 9 步合并**：五处注册表改完但会话还没接线时，全量回归应当**完全绿**（新 kind 存在但永不被判定命中）。这一刻的绿是「注册表改动本身零回归」的唯一证据；合并之后就再也拿不到它了。

---

## 十、非目标（本轮明确不做）

1. **跨标的情报关联 / 跨标的战绩池化。** 沿用三轮的红线：条目按 `instId` 严格隔离，**引用统计仍各算各的账**。轴 B 的先验因此也是**每标的一份**——代价是新用户很久看不到先验生效，这一点必须在 UI 上如实写成「样本 3/8，先验未生效」，而不是靠池化把它藏起来。
2. **多检索源扇出。** 仍是单活。
3. **用 LLM 判断告警。** 本轮全部三条轴的判定都是确定性纯函数。理由见 §3.0 第 1 条。
4. **自动调整告警门槛。** 一个会自己改变唤醒频率的系统，其行为无法被任何一次回归覆盖（沿用 evidence-grounding 非目标 5 对 `strict` 自动升档的同一条裁决）。
5. **让告警直接下单 / 直接改仓位 / 绕过任何门禁。** 告警的全部权力就是「让 AI 提前被问一次」。
6. **把 `_format_*` 外移到 `prompt_sections.py`。** closed-loop 非目标 6 把它升级为「下一个触碰 `ai_auto/prompts.py` 的特性必须先还这笔债」，evidence-grounding 原样转记。**本轮同样不触碰 `ai_auto/prompts.py`（§4.3 已说明两处新文案的落点都在委派目标里，§6.1 逐文件列出），因此这笔债不被触发、继续挂账。** 本节第三次原样转记，交给下一个真正要改那个文件的特性。
7. **`db/models.py` / `config.py` 的整表拆分。** 二者早已越线（1165 / 1313），拆它们会触碰全仓库的 import，与本特性混在一起会让 G-19 的零新增红失去说服力。登记为既有欠账，本轮**不新增**（§十二）。
8. **回测车道的情报重放。** 红线，仍不做。
9. **矛盾的自动裁决。** 确定性层没有能力判断两条相反的报道里哪一条为真；它只负责让矛盾可见并测量有没有被处理。
10. **告警的推送通知 / 桌面通知。** 桌面壳零改动；告警只经既有 WS 与 REST 到达界面。

---

## 十一、配置项清单（18 项新增，全部 `ai_trade_intel_` 前缀；总数 64 → 82）

| 键 | 默认 | 约束 | 说明 |
|---|---|---|---|
| `ai_trade_intel_alert_enabled` | `True` | — | 轴 A 总闸。`False` ⇒ **不发任何 handoff**，唤醒链路逐字节回到 `4b9c352` |
| `ai_trade_intel_alert_min_impact` | `"high"` | `Literal["medium","high"]` | 影响力门槛 |
| `ai_trade_intel_alert_categories` | `"risk,macro,project"` | csv，元素 ∈ `CATEGORIES` | 启动期校验器解析并校验；空串 ⇒ 全类别 |
| `ai_trade_intel_alert_require_quote` | `True` | — | 须有逐字原文引证；`quote_mode="off"` 或 `reader_enabled=False` 时**在单一有效值读点降级为 False**（§3.1.1.1） |
| `ai_trade_intel_alert_min_domains` | `2` | `ge=1, le=5` | 独立域名下限 |
| `ai_trade_intel_alert_min_confidence` | `0.6` | `ge=0, le=1` | 服务端算出的置信度下限 |
| `ai_trade_intel_alert_max_event_age_min` | `180.0` | `ge=5, le=1440` | 事件新鲜度上限（分钟） |
| `ai_trade_intel_alert_cooldown_min` | `20.0` | `ge=1, le=720` | 同标的两次告警的最小间隔 |
| `ai_trade_intel_alert_daily_cap` | `6` | `ge=1, le=48` | 每标的每 UTC 日 fired 上限 |
| `ai_trade_intel_alert_allow_scheduled` | `False` | — | 未来事件是否可告警 |
| `ai_trade_intel_alert_pending_ttl_s` | `180.0` | `ge=30, le=900` | 待处理告警的最长存活；超时丢弃并记 `dropped_expired` |
| `ai_trade_intel_alert_retention_d` | `30.0` | `ge=1, le=365` | 台账保留期 |
| `ai_trade_intel_efficacy_enabled` | `True` | — | 轴 B 总闸 |
| `ai_trade_intel_efficacy_k_min` | `8` | `ge=1, le=100` | 收缩伪计数 |
| `ai_trade_intel_efficacy_max_weight` | `0.25` | `ge=0, le=1` | 先验对 `score_item` 的绝对封顶 |
| `ai_trade_intel_efficacy_min_domain_samples` | `3` | `ge=1, le=50` | 域名信誉的最小样本，低于它显示「样本不足」 |
| `ai_trade_intel_conflict_enabled` | `True` | — | 轴 C 总闸 |
| `ai_trade_intel_conflict_min_overlap` | `0.35` | `ge=0.1, le=0.9` | 主题重叠阈值（Jaccard） |

（`ai_trade_intel_conflict_max_pairs` 复用既有的 `curate_max_actions` 语义档，**不新增第 19 项**：上限固定为常量 `MAX_CONFLICT_PAIRS = 6`，写在 `conflict.py` 里并在 docstring 说明为什么它不是配置——一个没人会调的旋钮只会增加 `.env.example` 的对齐负担。）

**这 18 项如何到达纯核心**（评审 P1-9）：全部经 `taxonomy.limits_from_settings` 映射进 `IntelLimits` 的 16 个新字段（`alert_retention_d` 与 `alert_pending_ttl_s` 两项不进——前者只被清理点读、后者只被会话侧读，进 `IntelLimits` 就是两个没有读点的死字段）。三把总闸也走这条路，**不在任何纯核心里 `getattr(settings, …)`**。

**登记落点**：仓库根 `.env.example`（**不是** `services/quant/.env.example`，评审 P1-1）。该家族 64 → 82，双向对齐门禁 `test_every_intel_setting_is_registered_in_env_example` 会在第 4 步当场红——**那正是要它红**。桌面壳按 `AI_TRADE_INTEL_` **前缀**透传（`desktop/scripts/config-passthrough.test.mjs` 的白名单是按前缀而不是按键名逐个列举），故 18 项新键零改动即可穿到后端，§6.3 只需在该用例里补一条数据。

**两条启动期校验器**（`config.py` 的 `model_validator`，与既有四条并列）：

1. `alert_categories` 的每个元素必须 ∈ `CATEGORIES`，否则启动即报错并指出坏值；
2. `alert_pending_ttl_s` 必须 **≥ `2 * live_auto_poll_interval_s`**——小于它时告警在被 tick 看到之前就过期了，是一个**永远不会唤醒**的配置，且没有任何错误。

> ⚠️ **评审 P1-6：v1 的写法是一条永远走不到的校验器。** 它判的是「≥ 2 个盯市 tick（`> 2 × 5s` = 10s）」，而同一张表给 `alert_pending_ttl_s` 的约束是 `ge=30` —— **`ge=30` 已经把 10 覆盖住了，这条 `model_validator` 一辈子不可能红**。上一轮终审刚修过一条同形的「永远走不到的 `>99` 校验器」。
> 而它本该拦住的坏配置恰恰拦不住：`live_auto_poll_interval_s` 是一个**无上界**的普通 `int = 5`（`config.py:125`，没有 `Field(le=…)`），用户把盯市周期调到 60s 时，`alert_pending_ttl_s=30` 就是「告警在被看到之前必然过期」。
> 故定稿为**跨字段**校验（与既有那条 `quote_mode="strict"` × `reader_enabled=False` 的跨字段校验器同款），并在 `_maybe_intel_wake` 的 docstring 里把这条不变式写一遍。

---

## 十二、规模与阈值登记（CLAUDE.md 合规）

| 文件 | 当前 | 预计 | 判定 |
|---|---|---|---|
| `intel/alert.py` | — | ≈ 200 | 新建，达标（阈值已移出，见 §3.1.1） |
| `intel/efficacy.py` | — | ≈ 200 | 新建，达标 |
| `intel/conflict.py` | — | ≈ 150 | 新建，达标 |
| `db/ai_intel_alert_repositories.py` | — | ≈ 200 | 新建，达标 |
| `intel/gate.py` | 754 | ≈ 800 | **贴线**。实施时若越过 800，按 `EG-I2` 的成文修法把 `_review_*` 系列外移到 `intel/review.py`（纯搬运、零语义变化、阈值不放宽），**不得靠删注释压行数** |
| `intel/service.py` | 768 | ≈ 815 | **同上贴线**：越线时把 `INTEL_SETTING_BRIDGE` 与 `resolve_intel_settings` 外移到 `intel/settings_bridge.py` |
| `intel/disclosure.py` | 155 | ≈ 215 | 达标 |
| `intel/taxonomy.py` | 357 | ≈ **470** | 达标（评审 P1-2 把三组阈值并进 `IntelLimits` 并追加 `resolve_require_quote` 之后，本文件比 v1 估的 400 多约 70 行；仍在 800 内。**若实施时越过 800，按 `EG-I2` 的成文修法把 `IntelLimits` 与 `limits_from_settings` 外移到 `intel/limits.py`（纯搬运、零语义变化、阈值不放宽），不得靠删注释压行数**） |
| `ai_auto/wake_runtime.py` | 389 | ≈ 430 | 达标 |
| `ai_auto/wake_reason.py` | 168 | ≈ 190 | 达标 |
| `ai_auto/session.py` | 3264 | ≈ 3300 | **既有欠账**（早已越线四倍）。本轮只加 ≤35 行且判定全部下沉到 `wake_runtime`；不做整文件拆分（会触碰真实资金主路径，与本特性混在一起会让零新增红失去说服力） |
| `api/ai_intel.py` | 540 | ≈ 590 | 达标 |
| `db/models.py` | 1165 | ≈ 1200 | **既有欠账**，本轮不拆（§十 非目标 7） |
| `config.py` | 1313 | ≈ 1370 | **既有欠账**，同上 |
| `IntelModal.tsx` | 553 | ≈ 590 | 达标（新内容全在新组件里） |
| `IntelSourcesTab.tsx` | 237 | ≈ 290 | 达标 |
| `IntelSourceSettings.tsx` | 383 | ≈ 460 | 达标（评审 P2-10 补登：v1 漏登，但它在 §6.2 #41 的改动清单里） |
| `IntelCard.tsx` | 306 | ≈ 320 | 达标（评审 P2-10 补登，同上，§6.2 #37） |
| `IntelRoundsTab.tsx` | 202 | ≈ 210 | 达标（只多 export 一个 `CODE_LABEL` 与三个新码） |

**函数级**：`plan_alert` 的判定链有 12 个分支，圈复杂度会越过 10 ⇒ **必须**拆成 `_suppress_round_level(…)`（0-3 号判据）与 `_suppress_item_level(…)`（4-11 号判据）两个辅助函数，主函数只做编排（这正是 CLAUDE.md §六「循环里出现复杂条件分支，改写为查找表或策略对象往往更易读」指向的形状：12 条判据落成一张 `(码, 谓词)` 元组表，与 `wake_reason.classify_wake` 的 `conditions` 同款）。

**新登记的既有欠账（评审 P1-7）**：`metrics_json.aiStats.wakeScheduler` 在 `apps/web` 里**至今没有任何读点**——`lib/aiTradeHistoryTypes.ts:123-129` 是一份从 wake-scheduler-closed-loop 起就没人读的类型声明，历史页因此看不到任何调度计数。本轮**不顺手做**它（那是一个独立的展示位设计，塞进来会让「零新增红」失去说服力），原样转记给下一个真正要动历史页的特性。


### 实测行数（实施后逐个 `wc -l`）

> 三次**纯搬运**（I-7 / I-8）之后，本轮触碰的每一个文件都回到 800 以内，两处既有欠账（`db/models.py` / `config.py`）按 §十 非目标 7 原样保留、未新增。
>
> ⚠️ **本表已按终审后的实测逐个改正**（9 处漂移）：实施轮登记的数字取自修正 F-1…F-6 之前的那一版工作树。数字本身不构成缺陷（全部仍在阈值内），但一张写着旧数的规模表下一轮会被当成基线读，故按「不静默偏离」就地更正。

| 文件 | 实测 | 判定 |
|---|---|---|
| `intel/alert.py` | 321 | 达标 |
| `intel/efficacy.py` | 286 | 达标 |
| `intel/conflict.py` | 211 | 达标 |
| `intel/review.py（I-8 新建）` | 126 | 达标 |
| `intel/settings_bridge.py（I-7 新建）` | 105 | 达标 |
| `intel/service_curate.py（I-7 新建）` | 280 | 达标 |
| `db/ai_intel_alert_repositories.py` | 280 | 达标 |
| `api/schemas_intel_actionability.py` | 108 | 达标 |
| `intel/gate.py` | 778 | 达标 |
| `intel/service.py` | 766 | 达标 |
| `intel/disclosure.py` | 275 | 达标 |
| `intel/taxonomy.py` | 519 | 达标 |
| `intel/prompts.py` | 387 | 达标 |
| `intel/runtime.py` | 451 | 达标 |
| `ai_auto/wake_runtime.py` | 436 | 达标 |
| `ai_auto/wake_reason.py` | 188 | 达标 |
| `ai_auto/wake_text.py` | 339 | 达标 |
| `ai_auto/session.py` | 3390 | **既有欠账**（本轮不拆，§十 非目标 7） |
| `api/ai_intel.py` | 611 | 达标 |
| `db/models.py` | 1236 | **既有欠账**（本轮不拆，§十 非目标 7） |
| `config.py` | 1384 | **既有欠账**（本轮不拆，§十 非目标 7） |
| `IntelModal.tsx` | 592 | 达标 |
| `IntelActivityTab.tsx（新建）` | 88 | 达标 |
| `IntelAlertRow.tsx（新建）` | 127 | 达标 |
| `IntelEfficacyBlock.tsx（新建）` | 82 | 达标 |
| `IntelConflictBadge.tsx（新建）` | 41 | 达标 |
| `lib/intelActionability.ts（新建）` | 180 | 达标 |
| `IntelSourcesTab.tsx` | 259 | 达标 |
| `IntelSourceSettings.tsx` | 519 | 达标 |
| `IntelCard.tsx` | 317 | 达标 |
| `IntelRoundsTab.tsx` | 218 | 达标 |

**语言**：Python 侧 PEP 8 + Google docstring；TS 侧公开 API 用 `interface`。行宽 80。

---

## 十三、留给评审的待决问题（**已裁决**）

> 本节四个问题由评审逐条裁决，裁决结果**即为定稿**，实施期不得再摇摆。

| # | 问题 | 本文取向 | **评审裁决** |
|---|---|---|---|
| **Q1** | `alert_enabled` 默认 `True` 还是 `False`？ | `True` | **维持 `True`，但附三条兑现条件。** 本文把两难说清楚了：reader 只花钱、告警会改变交易行为。但「默认关」在本仓库有成文的反面先例——第 1 轮的 `inject`、第 3 轮的 `reader_enabled` 都裁成默认开，理由是「默认关意味着这个功能对绝大多数用户不存在」，而本轮是**收口轮**，一个默认关的收口等于没收口。三条兑现条件：① 设置卡（§6.2 #41）**必须与功能同批交付**，且四个旋钮与总闸在同一张卡上一眼可见（照抄第 3 轮对 `reader_enabled` 的同一条裁决）；② G-1 的逐字节回退用例走**配置路径**（`alert_enabled=False`），另加一条**默认路径**的用例断言 12 条抑制码在默认阈值下仍能逐条命中（否则「默认开」会掩盖「判据全都算错了」）；③ 第一次 `fired=True` 时必须补推一次 `status`，让用户在动态页签上**当场**看到「情报层刚刚多起了一轮决策」——一个会自己多花钱的默认值，必须自己说出来 |
| **Q2** | `intel_alert` 排在 `WAKE_KINDS` 第 2 位（`candle_close` 之后、`bar_advanced` 之前）是否正确？ | 是 | **正确，且比本文说的更强。** 本文担心「与 `signal_flip` 当年排在 `bar_advanced` 之后的裁决取向相反」——两者不冲突：`signal_flip` 是**内生**的预演估计（对未收线数据的猜测），让位给「真的收了新根」是对的；`intel_alert` 是**外生**的既成事实，且**这一轮之所以存在就是因为它**，把它标成 `bar_advanced` 会让唤醒句说「你补看一根新 K 线」而真正的原因从标题里消失。补充一条本文没写的算术依据：`_maybe_intel_wake` 认领槽位时传的 `trigger` 恒为 `"interval"`，而 `candle_close` 的判据是 `facts.trigger == "candle"`（`wake_reason.py:136`）⇒ **在告警自己认领的那一轮里 `candle_close` 恒不成立，`intel_alert` 恒为首位**。这一句要写进 §3.1.6 并由 G-7 冻结，否则读者会以为存在「被 `candle_close` 盖住」的情形 |
| **Q3** | 轴 B 的先验是否也应作用于**告警门槛**？ | 不 | **明确驳回。** 三条理由：① 与 §十 非目标 4 直接冲突（一个会自己改变唤醒频率的系统，其行为无法被任何一次回归覆盖）；② 会让 G-4 的 12 条抑制码用例失去确定性——「为什么这条没告警」的答案从「置信度 0.55 < 0.6」变成「置信度 0.55 < 一个随历史漂移的数」，而后者**没有展示位**就是一个静默门禁；③ 本轮全部三条轴的立身之本是「确定性、可离线回放」（§3.0 第 1 条），破了它，轴 A 就不再是「可在离线用例里逐条冻结」的东西。若日后确实要做，前置条件是先把「门槛现在是多少」做成一个用户看得见的数 |
| **Q4** | 矛盾对的重排是否应当牺牲排序质量？ | 是 | **接受，但按评审 P1-8 收窄为「只在同层块内部重排」。** 「让分歧同屏」的价值确实高于「让每一行都按分数排」——本文这个判断是对的。但 v1 的无约束重排会撞上两件它没考虑到的事：`_TIER_RANK` 是排序首键（`disclosure.py:39`），且预算循环是 `break` 不是 `continue`（`disclosure.py:117-119`）。跨层重排因此不只是「牺牲一点排序质量」，而是可能让整个 digest 提前截断。收窄之后代价有界且可解释：最坏是同层里一条低分挤掉同层末尾一条更好的独立情报，上界 `max_pairs=6` 条，已登记进 R-7。本文给的退路（「只在两端本来就都会被收进时才重排」）**不采纳**——那恰好让最需要被看见的「一强一弱」矛盾看不见，本文自己也这么说了 |

## 附：本轮与前三轮的关系速查

| 轮次 | commit | 解决的问题 | 留下的天花板 |
|---|---|---|---|
| 1 | `52f466e` | 情报能被**采**到、分层存住、Agent 自主整理、用户增删改查 | 采到的东西**没人知道它有没有被用过** |
| 2 | `7e2d5be` | 情报能被**引**用、往返净额能回写、能被独立 Agent **证伪**、信源能被拉黑 | 全链路唯一的「事实」只是搜索引擎的 snippet |
| 3 | `4b9c352` | 情报能被**证**（第一方抓正文 + 逐字引文 + 证据抽屉 + 排期兑现） | **采到、证实、存好之后，什么时候真正改变一次决策，不可知、不可测、不可改进** |
| **4（本轮）** | — | **及时**（告警唤醒）· **战绩**（先验回流）· **去噪**（矛盾裁决）· **可解释**（一条时间线） | 见 §十 的十条非目标 |


---

## 评审结论

**【有条件通过】**

本轮的立项判断是**成立**的：三轮之后情报层确实能采、能引、能证，而「采到、证实、存好之后，
什么时候真正改变一次决策」确实是它们共同的天花板；三条轴（及时 / 战绩 / 去噪）共享同一个
形状——**把系统已经知道、但没有用起来的事实，变成一次可观测的行为**——这是一个收口轮该有的
问题选择。三条「不可越过的方向」（确定性优先、加法且可逐字节回退、不新建后台任务/循环/关停步骤）
与「零 ALTER 补列」的数据模型取舍都是本仓库成文纪律的正确延续，`ai_auto/prompts.py` 一字不改
从而不触发 `prompt_sections.py` 那笔历史欠账的论证也站得住（§4.3 已逐文件核对委派目标）。

同时，v1 有 **4 个 P0** 与 **10 个 P1**，全部已在正文改毕。值得单独说的是：**4 个 P0 里有 3 个是
本轮自己立项要修的那个缺陷的同形**——

- **P0-1**（战绩先验的两个消费点住在整理轮，而整理轮拿不到 `Efficacy`）与 **P0-2**
  （`promote_weak_record` 的判据落在一个入参里没有 `reason` 的函数上）：都是「新判据取自不携带
  该信息的入参 / 写进一张没人读的表」。黑板上这个形状已经出现过 5 次，本文在 §3.1.2 的方框里
  亲自把它写成了警告，然后在 §3.2 犯了两次。
- **P0-3**（唤醒句断言「该情报已在下方情报段中列出」，而 `_TIER_RANK` 把 ultra 排在最后、
  `prompt_max_chars` 默认只装得下 ~5–6 行（单行典型 ≈190 字符）、长期层默认上限 16 条 ⇒ 默认配置下它**在稳态上就不在**）：
  这是「把一句确定错误的话逐字写进喂给模型的 prompt」，且发生在**真实资金**决策轮的首行。
- **P0-4**（`keep_both` 无条件进 `CURATE_ACTIONS`）：直接违反本文 §3.0 第 2 条自己立的规矩，
  而 v1 的 G-3 只冻结 `build_digest`、看不见它。

10 个 P1 里，**P1-2 / P1-3 / P1-9 是同一件事的三个面**（三个新纯核心模块与既有纯核心之间的
import 方向没有被想清楚），已合并处理为「三组阈值一律并进 `taxonomy.IntelLimits`、
`efficacy.py` 绝不 import `gate`、总闸经 `IntelLimits` 而不是 `getattr(settings, …)` 到达纯核心」。
**P1-1**（`.env.example` 的路径）是第 1 轮评审纠正过的同一个错误再次出现，值得在实施期专门看一眼。

### 放行前必须兑现的条件（C-1 … C-9）

| # | 条件 | 判定方式 |
|---|---|---|
| **C-1** | **轴 B 的四个消费点全部有活的读点。** `run_curate_round` 必须自己求解一次 `Efficacy` 并显式传给 `review_actions` 与 `plan_cleanup`；`promote_weak_record` 必须能在一条**真实整理轮**路径上被触发 | G-28 的三条断言全绿；且 G-28 的 ② **不得**通过直接调 `review_actions(eff=…)` 来满足 |
| **C-2** | **唤醒句在任何配置下都不说假话。** `AlertHandoff.in_digest` 必须由 `IntelRuntime._alert` 从刚刷新的 digest 反查得出（不是从「有没有 ref」推断），`inject_enabled=False` 时恒为 `False` | G-29 的三条断言全绿，且断言对象是**渲染后的 prompt 字符串** |
| **C-3** | **三把总闸任一关闭时四件产物逐字节回退。** 特别是 `conflict_enabled=False` 时工具 schema 的 `action.enum` 不含 `keep_both`、不含 `pairId` | G-3（四件 sha256）+ G-27 + G-30 |
| **C-4** | **`merges` 分支能触发告警。** 实现里必须能看到「候选投影从 `existing[merge.dedup_key]` 取内容字段」这段代码 | G-31 必须先红后绿（先写用例、再写投影） |
| **C-5** | **`.env.example` 改的是仓库根那一份**，18 项新键双向对齐门禁绿 | `test_every_intel_setting_is_registered_in_env_example` 绿；且 `services/quant/` 下**不得**新出现一个 `.env.example` |
| **C-6** | **三个新纯核心不与 `taxonomy` / `gate` 成环。** | `python -c "import app.ai_auto.intel.taxonomy, app.ai_auto.intel.gate, app.ai_auto.intel.efficacy"` 干净；`grep -n "^from .gate\|^from \.\.gate" intel/efficacy.py` 为空 |
| **C-7** | **G-9 扩既有用例，不新建第二条源码扫描。** 九处注册表里 #9 `AXIS_VALUES.wake` 必须被真的覆盖 | 故意在 `AXIS_VALUES.wake` 里漏掉 `intel_alert` 时 `experienceLabels.source.test.ts` 必红 |
| **C-8** | **`alert_pending_ttl_s` 的校验器要能红。** | 造一个 `live_auto_poll_interval_s=60` + `alert_pending_ttl_s=30` 的 `Settings`，断言启动期即报错并指出两个字段名 |
| **C-9** | **全量回归零新增红。** 后端 `python -u -m pytest`（不并发）、前端 `NODE_ENV=test` vitest 全量 + `tsc`、桌面 `node:test` 全量、`ruff F,E9` | 入轮基线红集合 = 未跟踪在制文件 `test_factor_library_expansion.py` 的 7 条；**新增红必须为 0**。`-k` 子集跑法不可用于判回归 |

### 评审建议（不阻塞放行，但值得在实施期顺手做）

1. **§3.1.4 的排序理由要按 P2-7 改写后再抄进源码注释。** v1 那句「能省掉那一次白跑的预演」是
   不成立的（`_maybe_signal_flip_wake` 先 `await self._preview.run(...)` 才判 `_deciding`）。
   本仓库的注释是被后来的人当证据读的，抄一句错的进去比不写更坏。
2. **`deferred` 与 `woke` 共用一列 `outcome`，`deferred` 的事实会被后来的 `woke` 覆盖掉。**
   本轮接受这个取舍（台账要的是「最终发生了什么」），但动态页签的告警行文案不要写成
   「本次未被推迟过」——它说不出这句话。
3. **§3.3.3 的 system prompt 纪律不要写 `supersede`**：`gate.CurationAction.action` 的值域里
   没有这个动作（`gate.py:630`），模型照着发会被 `bad_action` 驳回，而界面上没有任何线索指向
   那句 prompt。正文已改，实施期再核一遍英文措辞。
4. **第 8 步与第 9 步不可合并这条纪律，本轮新增第 0 步之后同样成立**：第 0 步（`IntelLimits`
   扩 16 个带默认值的字段）跑完全量应当**完全绿**，那一刻的绿是「阈值扩容本身零回归」的唯一
   证据，合并之后就再也拿不到它了。

### 评审未改动的部分（明确表示同意）

- **零 ALTER 补列**的数据模型取舍（§5.1）：冷却与日配额从台账现算、矛盾与战绩都不落库——
  三条理由（事件流 vs 状态、读改写丢更新不报错、第二份会漂的账）都成立，且 `db/session.py`
  一字不改让 C-9 的说服力不被迁移风险稀释。
- **`ai_intel_alerts` 空轮也写一行**（§3.1.2）：「今天为什么一次都没告警」永远有答案，
  这是本轮最好的一个设计决定。
- **§十 的十条非目标**逐条同意，特别是非目标 3（不用 LLM 判断告警）与非目标 5
  （告警的全部权力就是「让 AI 提前被问一次」）——后者是轴 A 全部安全性的支点。
- **§3.4.3 会话侧永不复读告警开关**：理由（会话持 `executor.settings` 的启动期定稿副本，
  而情报设置住 `app_settings` 并经 `model_copy` 派生）在源码上成立，G-15 的源码扫描是对的。

### 约束遵守

- 本次评审**只改 `docs/plans/ai-trade-intel-actionability/spec.md` 一个文件**；
  `git diff --stat` 对已跟踪文件为空（零源码改动）。
- **未 `git commit`**。

---

## 实施过程发现的方案缺陷

> 按本轮任务书的成文约束：**发现方案有错或不完整时不得静默偏离**，一律追加在这里，然后按修正
> 后的做法实施。下列 12 条全部已按「修法」落地并有用例覆盖；每一条都写清了**照原文实施会
> 发生什么**——那才是这一节的价值所在。

| # | 位置 | 缺陷 | 修法（已实施） |
|---|---|---|---|
| **I-1** | §3.1.1 `AlertCandidate` 的字段表 | **字段表里没有 `summary`，而 §4.3 的唤醒句在 `in_digest=False` 时要把摘要就地内联进 prompt。** 照原文实施，内联态**渲染不出摘要**——而那一态存在的全部理由就是「本段就是你能看到的全部内容」。G-29 ② 会当场红，但更坏的情形是实现者顺手把 `headline` 当摘要用，那既不会红也不会被发现 | `AlertCandidate` 追加 `summary: str = ""`（**带默认值**，不参与任何判据、不进台账、不进 wire 的告警行）。`AlertHandoff.wire()` 带上它，`wake_text._wake_intel_alert` 的内联态读它 |
| **I-2** | §3.3.1 判据 4 | **时间窗判据写的是 `\|a.event_ts - b.event_ts\|`，而无日期条目的 `event_ts` 恒为 0。** 照原文实施有两个方向都错的后果：一条无日期条目与任何有日期条目相距半个世纪 ⇒ **永不成对**；而两条无日期条目会被判成「时刻完全相同」⇒ **恒成对** | 参照点取 `event_ts if event_ts > 0 else captured_ts`，与 `gate.score_item` / `alert.age_minutes` **同一条既有口径**（`conflict._ref_ts`，用例 `test_undated_items_use_captured_ts…` 两个方向各冻结一条） |
| **I-3** | §3.2.3 / §3.3.3 的 `review_actions` 签名 | **`review_actions(actions, items, *, limits, now, eff, conflicts)` 是 6 个形参，越 CLAUDE.md「形参 ≤5」**——而 §3.2.3 的方框恰恰是为同一条理由否掉了「往 `_review_promote` 里塞 `reason` 与 `eff`」 | 两样可选证据并进一个 frozen 聚合结构体 `review.ReviewContext(eff, conflicts, max_weight)`，签名回到 5 个（`max_weight` 同理：`promote_weak_record` 的门槛按 spec 是 `-0.5 × efficacy_max_weight`，而 `weak_record_code` 也塞不下第 6 个形参——从「观察到的最大权重」去推那个门槛是**错的**，它会让一个所有类别权重都很小的库变得异常敏感）。`ReviewContext()`（两项皆空）⇒ 两条新判据整段不参与 ⇒ 逐字节回退，与 `score_item` 的 `limits=None` 同款范式 |
| **I-4** | §6.1 的文件清单 | **`keep_both` 通过之后要写 `curation_note` / `last_curated_ts`，而 `apply_intel_curation` 的动作分派是一条 `else: continue`** ⇒ 照清单实施，`keep_both` 会被**静默跳过**：G-21 断言的那次写入永远不发生，而「tier 与 status 一个字节不变」这半条却是真的（它什么都没做） | `db/ai_intel_repositories.apply_intel_curation` 新增一个 `elif kind == "keep_both": pass` 分支（**只落公共的三行赋值**，不碰 tier / status）。该文件不在 §6.1 清单里，故在此登记 |
| **I-5** | §3.1.5 #7 / §6.2 #43 | **`WAKE_LABELS` 的 `tone` 被指定为 `"warn"`，而 `AiTraceTone` 的值域只有 `long \| short \| muted \| accent`** ⇒ tsc 当场红（这一处不是静默的，但它会让第 12 步卡住） | 取 `accent`——它与「补看新 K 线 / 立场翻转 / 价格异动」同属「值得注意」的那一档 |
| **I-6** | §5.3 / §3.1.3 的 `in_digest` 反查 | **`cite=False` 时 `build_digest` 的 wire 上没有 `refs`，而「这一条到底在不在 prompt 里」仍然必须答得出来。** §3.1.3 说「`cite` 关时改判 `item_id` 是否在本次 `lines` 对应的条目集合里」——但那个集合**不存在于任何产物上**，而给 wire 加一个键会让 G-3 的 `sha256` 当场变（那是一次逐字节变更） | 拆出 `disclosure.build_digest_and_ids(...) -> (wire, picked_ids)`，`build_digest` 退化成一行薄壳（**wire 一个字节不变**）。`build_snapshot` 把 id 集合放进 `snapshot["digestItemIds"]`（**不进 digest wire**），`IntelRuntime.refresh` 记下它、`_alert` 据它判 `in_digest` |
| **I-7** | §十二 的规模表（`intel/service.py` 768 → ≈815） | **实测越到 919 行**：告警写段、两个求解入口、矛盾渲染器与两个新读数加起来远超 47 行的估计。§十二 给的处置（外移 `INTEL_SETTING_BRIDGE`）只减 70 行，仍在 800 以上 | 按 §十二「超了就再下沉一层」的成文修法做**两次纯搬运**：① `INTEL_SETTING_BRIDGE` / `resolve_intel_settings` / `resolve_api_key` → 新建 `intel/settings_bridge.py`（90 行），`service` 原样重新导出，调用点零改动；② 整理轮编排（`run_curate_round` / `_persist_curate` / `_after_actions` / `render_listing` / `render_conflicts` / `_conflict_counts`）→ 新建 `intel/service_curate.py`（**文件名沿用同目录 `service_verify.py` 的既有范式**），`service.run_curate_round` 留一个延迟导入的薄转发（逐行照抄 `IntelRuntime.run_verify` 对 `service_verify` 的做法）。最终 `service.py` 749 行 |
| **I-8** | §十二 的规模表（`intel/gate.py` 754 → ≈800「贴线」） | **实测越到 851 行** | 按 §十二 点名的那条修法把复核判据外移到新建的 `intel/review.py`（123 行）：`ReviewContext` / `review_promote` / `weak_record_code` / `keep_both_code`。**方向单向**——`review` 不 import `gate`，三条判据只返回码、由 `gate` 拿着码去构造 `CurationAction`。`_review_promote` 的**签名与函数体一个字节不改**，只去掉前导下划线（它现在跨模块被调用），并在 `gate` 里保留 `_review_promote = review_promote` 的别名——既有用例直接断言它的返回码，「搬家不改任何调用点」正是这条修法「纯搬运」的含义。最终 `gate.py` 773 行 |
| **I-9** | §6.1 #4 的仓库函数清单 | 清单里没有 `count_recent_alerts`，而 §4.2 要求 `AiTradeIntelSummary` 带 `alerts24h` / `lastAlertTs` 两键 | `db/ai_intel_alert_repositories.py` 增补 `count_recent_alerts`（**`lastAlertTs` 为 `None` = 从未告警过**，与 0 分开：后者会被渲染成 1970 年） |
| **I-10** | §4.1 #2/#3 的「扩返回」 | 端点层拿不到 `IntelDeps`（那是情报后台任务的依赖聚合，REST 请求手上只有一个会话与 `Settings`），而 §3.2.4 的 `solve_efficacy` 签名收的是 `deps` | 增补 `service.read_efficacy(db, inst_id, *, limits, now)`：**与 `solve_efficacy` 同一批入参、同一个纯函数**。这不构成第三个真源（`efficacy.solve` 是纯函数），G-28 的三处对拍把它一并冻结 |
| **I-11** | §3.1.1.1 与 G-6 的措辞差 | 正文写「`quote_mode="off"`（或 `reader_enabled=False`）」，而 v1 的实现只判前者。G-6 明写「`reader_enabled=False` 同理」 | `taxonomy.resolve_require_quote` 两条降级链都判：`quote_mode == "off"` 或 `reader_enabled` 为假 ⇒ 生效值降为 `False`，**存储的偏好一个字节不改** |
| **I-13** | §3.2.4「放进 `summary["efficacy"]`」 | **照字面把它拼在 `build_snapshot` 里，会让两条通路的 summary 形状分叉。** `AiTradeIntelSummary` 有**两个**出口：WS `intel` 事件（走 `build_snapshot`）与 `GET /intel/{instId}`（走 `api/ai_intel._summary` → `read_summary`，它只把 `inFlight` / `degrade` / `providerConfigured` 三个瞬态从运行时缓存里取）。拼在 `build_snapshot` 里 ⇒ **REST 那一份没有 `efficacy` 键**，而 TS 侧它是必填字段 —— 症状是「WS 通路是好的，只有刷新后重发现拿不到值」，正是本仓库 `groupStateFromStatus` 那类坑。它**不报错** | 拼在 `read_summary`（该函数的 docstring 自己写着「**唯一**装配点」）。代价是快照路径上多求解一次 `Efficacy`——`efficacy.solve` 是纯函数，两处同值（G-28 的性质本身就是为这件事写的）。用例：`test_the_summary_always_carries_the_actionability_keys` 里加一条 `set(read_summary(...)) == set(build_snapshot(...)["summary"])` |
| **I-12** | §7.2 的「新增 pytest（9）」清单 | 清单里的 `test_ai_intel_alert_plan.py` 同时被指派了「12 条抑制码」「一轮至多一条」「冷却与日配额从台账现算」「merges 分支」四组断言，而**第三组需要真 SQLite**（`alert_cooldown_state` 是一个仓库函数）——把 DB 用例塞进一个宣称「离线、零 IO」的纯核心文件里，会让那句话在第一天就变成假的 | 拆成两个文件：`test_ai_intel_alert_plan.py`（纯核心，零 IO）与 `test_ai_intel_alert_ledger.py`（真 SQLite：两个写点、冷却/日配额现算、保留期裁剪、游标分页）。文件总数仍是 9（`test_ai_intel_alert_rollback.py` 吸收了 G-1 与 G-3 的另外三件产物） |

### 与 §6 文件清单的差异（逐项登记）

- **多出 4 个后端文件**：`intel/settings_bridge.py`、`intel/service_curate.py`、`intel/review.py`
  （三者皆为 I-7 / I-8 的**纯搬运**产物，零语义变化）与 `db/ai_intel_alert_repositories.py`
  （清单 #4，本就在列）。
- **多改 1 个后端文件**：`db/ai_intel_repositories.py`（I-4 的 `keep_both` 落库分支）。
- **`api/schemas_intel_actionability.py` 的 `IntelAlertOut` / `IntelAlertListOut` 不参与序列化**：
  §4.1 #1 明写该端点不挂 `response_model`，故这两个模型只作为文档与类型意图存在。
- **测试文件**：新增 9 个 pytest（含 I-12 的拆分）+ 5 个 vitest；改既有 8 个 pytest、6 个
  vitest、1 个 node:test、2 条源码扫描门禁（`intelNoDangerousHtml.source.test.ts` 的文件清单、
  `experienceLabels.source.test.ts` 的 G-9 前端一侧）。

---

## 终审记录（第 4 轮，合入前）

> 逐条比对工作树 diff 与 §6 的《文件变更清单》、§7.1 的 G-1…G-31、§评审结论的 C-1…C-9。
> **发现的问题一律直接改在源码里，不留 TODO**；每一条都写清「照原样合入会发生什么」——那才是
> 这一节的价值。

### 一、覆盖度复核（结论：清单已兑现）

- **6.1 后端**：新 5 + I-7/I-8 纯搬运出的 3 个（`settings_bridge` / `service_curate` / `review`）
  全部在位；改 22 项逐项核对到位，另加 I-4 登记的 `db/ai_intel_repositories.py`。
- **6.2 前端**：新 5 / 改 14 全部在位；`CODE_LABEL` 与 `RoundRow` 按 P2-5 留在
  `IntelRoundsTab.tsx` 并 `export`，`IntelActivityTab` import 它（**没有第二份**）。
- **6.3 桌面壳**：零改动，只扩既有 `config-passthrough.test.mjs` 的白名单探针，符合 §6.3。
- **6.4 文档**：`README.md` 与 `ai-trade-market-intel/spec.md` §十 的转记链都在。
- **C-1…C-9**：逐条复核通过。C-5 的 `.env.example` 18 项在**仓库根**、双向对齐门禁绿；
  C-6 的三个新纯核心确实不 import `gate`（源码扫描用例在案）；C-9 见下方回归数据。

### 二、终审改掉的 6 条（F-1 … F-6）

| # | 位置 | 问题 | 照原样合入会发生什么 | 修法 |
|---|---|---|---|---|
| **F-1**（P1） | `db/models.py` · `db/ai_intel_alert_repositories.py` · `intel/runtime.py` · `components/Intel/IntelAlertRow.tsx` | **`ref_token` 有列、有读点、没有写点。** `AlertHandoff.ref` 与 `in_digest` 都由第三跳（`IntelRuntime._alert`）从**刚刷新的** digest 反查得出，而那发生在采集轮 commit **之后**——`_persist_alert` 的 payload 里因此根本没有 `ref` 这一键，`write_intel_alert` 的 `ref_token=...` 恒取到空串。更坏的是前端拿 `!alert.ref` 当「不在情报段里」的判据 | `GET /alerts` 上 `ref` 恒为 `""` ⇒ ① 台账行的 `[Ixx]` 前缀**永远不出现**；② **每一条 fired 告警都挂着一句「· 未进入情报段编号」**——一句关于数据的假话，而且恰是 §3.1.3 花了整节去禁止的那次混淆（「有编号」≠「在 prompt 里」）搬到了界面上；③ R-15 的可见落点因此失效：真正没进情报段的那一条与其它条长得一模一样 | 台账改为**三个写点、每个独占一组列**（纪律本来就不是「只许写一次」，而是「同一列只有一个人写」）：新增 `stamp_intel_alert_digest(alert_id, *, ref, in_digest)`，由 `IntelRuntime._alert` 经既有 `scheduler.schedule(...)`（**不新增关停步骤**，G-26 不受影响）补写。`AiIntelAlert` 补 `in_digest` 列（新表，`create_all` 建，仍是零 ALTER）、wire 补 `inDigest`；前端判据改读 `inDigest`，文案改「未进入情报段」。新增 4 条用例（数据层 2、REST 2）+ 1 条运行时用例，另加一条**反向**用例冻结「`cite` 关掉时空 `ref` 不得触发那句话」 |
| **F-2**（P1） | `api/ai_intel.py::_summary` | 端点把**进程级 `Settings`** 传进 `read_summary`，而后者本轮起要读 `efficacy_enabled` / `efficacy_k_min`（以及既有的 `dailyCap`）——这三样都住在 `app_settings` 里、由设置面板改写 | 用户在面板上关掉「用历史战绩给情报排序」后，WS 通路（`build_snapshot` 用的是 `deps.settings`，本来就是派生副本）如实回 `enabled:false`，而 `GET /intel/{instId}` 这一份仍然回 `true` 并照样求解一次战绩。**两条通路给出两个不同的答案，两边都不报错**——正是本文件自己在 `_effective_limits` 的 docstring 里点名的那句「面板上关了，可这一页还按开着算」 | `_summary` 先 `resolve_intel_settings` 再传派生副本（与同文件 `_effective_limits` 同一条通路）。顺带修好既有的 `dailyCap` 同形漂移 |
| **F-3**（P2） | `components/Intel/IntelModal.tsx` · `schemas.py` · `api/app_settings.py` | 动态页签的「等待响应 / 未在时限内响应」分界写死成前端常量 `DEFAULT_ALERT_TTL_S = 180`，而 `alert_pending_ttl_s` 是一个 30–900 的配置项，且它**根本没在 `/settings/intel` 的回执上** | 把时限调到 900 的用户，会在第 180 秒看到一句「会话未在时限内响应」——而后端那一侧还在正常等着这条告警。一句由前端常量编出来的、关于后端状态的判定 | `IntelSettingsOut` 补一个**只读**的 `alertPendingTtlS`（不进 `IntelSettingsIn`——本轮不让用户从面板改它），`_intel_wire` 下发生效值，弹窗改读它、常量降级为「设置还没取回来时的回落」。新增 1 条用例 |
| **F-4**（P2） | `components/Intel/IntelAlertRow.tsx` | 组件 docstring 写着「缺省 ⇒ 该行不可点（`woke` 行也**只显示**，不做假的可点样式）」，而代码是 `{jumpable && (…)}`——拿不到回调时**整段不渲染**；而 `IntelModal` 今天根本没有跳转决策轨迹的能力，从来不传这个回调 | `runId` / `roundNo` 是 G-11 的验收事实、就在 wire 上，却在界面上一个字都看不到：用户只看到「已唤醒一轮决策」，看不到是**哪一轮** | 不可点时渲染成静态文本「第 N 轮」（可点时才画成按钮），与 docstring 对齐。新增 1 条用例。**刻意不去接跨面板跳转**——那要动 `AiTradeIntelButton` 与轨迹面板的接线，不在 §6.2 清单里 |
| **F-5**（P3） | `intel/disclosure.py` | `conflict_pair_index` 用了一个函数内局部 import，而 `conflict` 只 import `quotes`、与 `disclosure` **不成环** | 本仓库用函数内 import 表示「这里有一个真实的循环要打破」（`runtime.invalidate`）。拿它当风格用，会让下一个人误以为这里有环而不敢动——上一轮终审刚为完全同形的 `evidence_repositories` 修过一次 | 提到模块级，并在 docstring 里写明「不成环」这一事实 |
| **F-6**（P3） | `README.md` · §十二 规模表 | README 把「共 82 项」挂在第二段单键说明上（第一段反而没数）；§十二 的实测行数表有 **9 处**与终审后的工作树不符 | 规模表下一轮会被当成基线读，一张写着旧数的表会让下一轮的「贴线判定」全部偏移 | README 的计数移到第一段；规模表 9 个数字按实测逐个改正（全部仍在阈值内，见该表新增的说明段） |

### 三、复核后判定为「不改」的 3 处（连同理由一并留档）

1. **`IntelRuntime._efficacy` 缓存没有实现**（§6.1 #14 提到过它）。三个求解点各自持有自己的
   只读会话（`build_snapshot` / `run_curate_round` / REST 的 `read_efficacy`），没有任何一处
   够得着那份缓存——建它就是一个零读点的字段，正是本轮立项要修的那个形状。`efficacy.solve`
   是纯函数、G-28 已冻结三处同值，缓存缺席不改变任何行为。**按原样登记，不补。**
2. **`test_ai_trade_wake_text.py` 没有被扩**（§7.2 曾把它列进「改既有 8 个」）。`intel_alert`
   的两态唤醒句由 `test_ai_trade_intel_wake.py` 的三条用例（G-29）覆盖，且**断言对象是渲染后
   的 prompt 字符串**——那正是 C-2 要求的判据。再在另一个文件里写一份只会得到两处会漂的断言。
3. **`alertRequireQuote` 是一行只读说明而不是一个「置灰的复选框」**（G-6 的措辞）。§3.4.2 的
   六键桥里本来就没有它，`IntelSettingsIn` 也不收它——面板上从来没有过那个复选框，「置灰」
   无从谈起。当前实现（生效值为假时用警示色写明「因为引文要求是不要求 / 原文抓取已关闭，
   打开任一项即自动恢复，你的偏好没有被改写」）比一个灰掉的勾选框说得更清楚。

### 四、回归（C-9）

| 车道 | 命令 | 结果 |
|---|---|---|
| 入轮基线 | `python -u -m pytest`（不并发） | **4862 passed / 7 failed / 1 skipped** —— 7 条全部来自未跟踪且从未提交的在制文件 `test_factor_library_expansion.py`（断言库里不存在的因子），入轮前即红，**本轮新增 0 条红** |
| 终审后后端全量 | 同上 | **4865 passed / 7 failed / 1 skipped** —— 红集合与基线**逐条相同**（仍是那 7 条），新增红 0；`4862 + 3` 恰好是终审补的 3 条新用例（台账两条 + 设置桥一条），对得上 |
| 前端 | `NODE_ENV=test vitest run` 全量 + `tsc --noEmit` | **219 文件 / 2523 用例全绿**；`tsc --noEmit` 退出码 0 |
| 桌面 | `npm test`（`node --test "scripts/*.test.mjs"`） | **43 passed / 0 failed** |
| 静态 | `ruff check --select F,E9 app tests` | 干净 |
| 静态 | `pycodestyle --max-line-length=100 --select=E1,E2,E3,E5,E7,W2,W3` 对本特性全部文件 | 新增文件全绿；既有文件上报出的 E501 / E305 / E261 **逐条比对过 `HEAD` 基线，全部是本轮未触碰的行** |

> ⚠️ **`-k` 子集跑法不可用于判回归**（本仓库成文教训）：上表两次后端数字都来自**全量**、
> 且**不并发**（`services/quant` 的用例共用同一个临时 DB）。

