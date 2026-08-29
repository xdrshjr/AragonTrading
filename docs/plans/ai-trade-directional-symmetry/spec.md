# AI 自动交易 · 方向对称性修复（ai-trade-directional-symmetry）

> **版本**：v2（评审节点订正）
> **状态**：已评审 —— **有条件通过**（条件见文末《评审结论》）
> **作者**：方案设计节点（Subtask #0）
> **评审**：设计评审节点（Subtask #1），2026-08-29；评审记录见下一节
> **需求来源**：「自动交易现在貌似只买多、不卖空了。需要分析历史交易情况排查原因并修复，确保 AI 自动交易能够双向交易；研判交易决策和行动的时候要考虑方向性，不能无脑开多。」
> **唯一实施依据**：本文自包含，实现者不需要读任何历史 spec。

---

## 评审记录（Review Notes · v2）

评审口径：可行性（本仓库现有技术栈能不能造）／完整性（边界、错误路径、上线路径有没有缺）／
一致性（与 `CLAUDE.md` 和代码里既有惯例冲不冲突）／右尺寸（过度设计还是欠定义）。
每一条都在仓库里核对过落点；带 `file:line` 的都是评审时实际读过的那一行。
**下列 P0 / P1 已在本 v2 正文中逐条改完**，P2 记录在案、不阻塞实现。

### 本次评审新增的取证（用于订正 §2.4，方法与结论见 §2.4/§2.5）

评审节点以只读方式（`mode=ro&immutable=1`）复查了同一个生产库，发现**方案作者认为不存在的那份证据其实是存在的**：
`strategy_runs.metrics_json → aiStats.openFunnel.blocked` 是逐会话持久化的层轴拦截计数。
回归窗口（按 `end_ts` 归并的两批，全部 `preset=balanced` / `ai_trade_min_consensus=1`）实测：

| 批次 | run 数 | 开仓意愿 | consensus 拦截 | confidence | baseRuleMaBand | 其它 | 委托 | 成交 |
|---|---|---|---|---|---|---|---|---|
| 2026-08-26 | 5 | 647 | **347** | 111 | 101 | turnover 5 / reflection 5 | 78 | 13 |
| 2026-08-29 | 5 | 893 | **485** | 212 | 123 | turnover 13 / reflection 5 / drawdownBrake 1 | 54 | 17 |
| 合计 | 10 | **1540** | **832（54.0%）** | 323（21.0%） | 224 | 29 | 132 | 30 |

最近 5 个 run 里有 4 个的 `topBlocker` 就是 `consensus`（单 run 64 / 103 / 154 / 164）。
`config_json.baseRules.maBandEntry.directional` 全部为 `false`（印证 §十一 非目标 3 属实）。
**这条证据把 R-1 从「推断」抬成「实测」，也让「轴 D 先行」的交付顺序更有依据**；代价是 §2.4 原文的三句判断必须撤回。

### P0（阻断实现，必须改完才能动代码）

| # | 位置 | 问题 | 本 v2 的处置 |
|---|---|---|---|
| **P0-1** | §四 D-1 / §八 8.1 | `filled_by_side` **不可能**按 `_intent_side` 分流。`OpenFunnelCounters.note_filled()` 是**无参**的事件级记点（`open_funnel.py:196`，由 `_dispatch` 的成交事件驱动），而它排在 `note_executed`（`open_funnel.py:186`，已把 `_intent_pending` 置 `False`）之后、甚至可能跨轮到达，此刻 `_intent_side` 已清。照 D-1 实现，`longFilled`/`shortFilled` 恒为 0，轴 D 卡片的「成交」实心段永远画不出来。同一段还漏掉了 `submitted_by_side` 与 `sizing_zeroed_by_side` 两个字段——D-1 的闭合不变式和 D-2 的 `bySide.submitted` 都在引用它们，而 §八 从未声明。 | D-1 / D-2 / §8.1 重写：`note_filled` 改收成交腿**自己的** `side`；`submitted_by_side` 落在 `note_executed`、`sizing_zeroed_by_side` 落在 `note_sizing`；三个字段补进数据模型与 wire |
| **P0-2** | §四 B-1 / B-2 | 三处连环错。**(a)** 产出判据 `decision is not stage1_decision` 会误报：`_apply_sizing`（`session.py:1642` 上一行）本就会 `replace` 出新对象，任何开仓轮该判据皆真；真正兜住它的是 `degrade` 非空，而 `degrade` 在 `commit_round` 之前已被 `degrade = degrade or executed.get("closeBlocked")`（`session.py:1633`）污染 ⇒ `exit_order_slot_busy` 经 `classify_block` 落进 `"other"`，模型会读到一条**凭空捏造**的「我被『其它』层拦下了」。**(b)** `low_confidence` 轮 `_map_decision` 已把 action 洗成 hold（`decider.py:749`），`stage1_decision.action == "hold"`，注记里的 `proposedAction` 会撒谎——而 `proposedAction` 带方向正是本轴宣称的全部价值。**(c)** B-1 给注记新增了 `layerLabel`，今天的注记只有四键，于是 B-1 自己那句「horizon 否决轮逐字节等于今天」与 §9.1 的 `test_horizon_note_is_byte_identical_to_legacy` 直接互斥，实现必红。 | B-2 改为在反思之后、`_apply_sizing` 之前留 `gate_degrade`，判据换成 `gate_degrade is not None and gate_degrade != llm_degrade`；`layerLabel` 只在总闸开时附加，冻结用例改名并限定在总闸关 |
| **P0-3** | §四 B-3 / §五 前端表 | B-3 认错了落点，会造出第二条平行通路。「提议 X → 被 Y 拦下」这条线**今天已经端到端存在**：`session._emit_tool_call_trace`（`session.py:1958`）把 `proposedAction / proposedReason / vetoedBy` 作为 `tool_call` 相位的**兄弟键**下发 → `store.ts` 的 `emptyRound`（222-226）与 `reduceRoundPhase`（`case "tool_call"`）**逐键手抄** → `aiTradeTypes.ts:1045/1157` 已有类型 → 渲染在 `AiTradeExitPriorityRows.tsx::ProposedVetoRow`（`AiTradeTraceRound.tsx:37` 只是 import 它）。它的作用域被硬钉在 `"horizon"`（该函数 docstring 明写）。更要命的是：只把后端 `vetoedBy` 放开到别的层，`aiTradeExit.ts:188` 那句 `vetoedBy === "horizon" ? "交易周期层" : (vetoedBy ?? "门禁")` 会当场把 `consensus` / `baseRuleMaBand` 这些**英文 key 怼到用户脸上**。 | B-3 重写为「泛化既有三键 + 后端下发 `layerLabel`」，并逐条列出五个必改读点 |
| **P0-4** | §四 A-2 / §五 前端表 | 五个新降级码没有中文落点。`apps/web/lib/aiTrade.ts::DEGRADE_REASON_LABELS` 是降级码中文化的唯一表，今天**没有** `low_consensus` 条目（`aiTrade.ts:615` 成文契约：未命中一律原样回退）。于是 `low_consensus_evidence(0/1)` 会以原始英文出现在决策卡与成交行上——而本方案的主张恰恰是「让方向被拦这件事对用户可读」。文件清单里根本没有这个文件。另外该表用 `find(startsWith)`，**顺序敏感**：裸 `low_consensus` 必须排在四个细分码之后（与 `open_funnel._DEGRADE_PREFIXES` 的成文顺序纪律同构）。 | §五补文件与条目、A-2 补顺序约束、§9.4 补专用用例 |

### P1（会让方案在实现期跑偏，已改）

| # | 位置 | 问题 | 本 v2 的处置 |
|---|---|---|---|
| **P1-1** | §2.1 / §2.4 | §2.4 的三句判断与生产库不符（详见本节开头的实测表）：层轴拦截计数**早就落库**，缺的只有方向轴；「产品今天根本回答不了」是过强的结论，正确的说法是「答得出被哪一层拦，答不出拦的是哪个方向」。§2.1 的按日表是抽样而非全量，全量里 2026-07-22 单日就是 16 多 / 0 空——「单日 0 空」不是新现象，能支撑「结构性」的是**跨 6 日 6 标的 41:0 的连续性**。 | §2.1 补全量口径与 07-22 反例；§2.4 整节重写为「层轴已落库、方向轴缺失」并写进实测表；新增 §2.5 收口 |
| **P1-2** | §五 discipline.py 行 | 「`aggressive` 开且配额更大」是死配置。`discipline.py` 的 `aggressive` 是 `ai_trade_min_consensus: 0`，`off` 走 `config.py:195` 的默认 `0`——共识门禁在这两档**根本不触发**，逆势通路自然永不触发。本轴的真实作用域只有 `steady`(2) 与 `balanced`(1)，而生产回归正是 `balanced`。 | §五该行与 §7.3 改写，明确作用域 |
| **P1-3** | §四 A-2 | `resolve_consensus_gate` 没有签名，`quantize_size_fraction` 要的 `step` 无来源（`ConsensusRule` 不带它）。真源是 `decider.position_step`（`session.py:1803/2265` 已有先例）。另外 `CLAUDE.md` 限形参 ≤5，而 A-2 的不变式 3 把 `confidence` / `contrarian_evidence` 说成独立入参——它们本就在 `decision` 上。还有一处顺序陷阱：`min_consensus` 今天由 `_apply_consensus_gate`（`session.py:2273`）**现读** `self.executor.settings.ai_trade_min_consensus`，`ConsensusRule` 必须从 `_resolve_discipline` **之后**的那份派生副本取，抢跑读到的是旧对象（`api/ai_auto.py:340` 的 [P1-2]/G-13 成文坑）。 | A-2 补显式签名与三条来源约束 |
| **P1-4** | §四 C-2 | 插入点在仓库里不成立。`SystemPromptTexts.charter()` 在 **`prompts_en.py:40`**（不在 `prompts.py`），而且它只是五个既有文本段的拼接、不是扩展点；中文脊柱 `_render_spine_zh`（`prompts.py:141-146`）**另有一份内联的同款拼接**、根本不调 `charter()`。只改一处 ⇒ 条款只出现在一条语言路径上。而生产路径是英文脊柱（`texts.reasoning_text` 非空即走 `render_spine_en`，深度思维链合入后它恒非空）。 | C-2 改为「给 `SystemPromptTexts` 加第六个文本字段 `direction_text`，在 `charter()` 与 `_render_spine_zh` 两处都追加」，并点名两个装配点 |
| **P1-5** | §五 store.ts 行 / RK-8 | 机制与落点都写反了。成员级 `status` 是**整体 spread**（`store.ts:992` `{...m.session, ...patch}`、`:1032` `{...local.session, ...row.session}`，`aiTradeTypes.ts:1290-1296` 有成文注释），故 `openFunnel.bySide` / `directionBalance` **不需要动 store.ts**，只需补 `AiTradeOpenFunnel` 的类型声明。真正逐键手抄的是 **trace 相位**（`emptyRound` + `reduceRoundPhase`）与**分组级** `groupStateFromStatus`（`useAiTradeGroupStream.ts:31`）。P0-3 的 `layerLabel` 落在前者。 | §五该行与 RK-8 重写 |
| **P1-6** | §四 D-4 | `unknown` 桶不是边角料。实测 `blocked.confidence` 在回归窗口占 **323/1540 ≈ 21%**——这些轮 `_map_decision` 已把 action 洗成 hold、方向不可知，按 D-1 全进 `unknown`。此时 D-4 那句「模型本会话从未提出过做空意愿 —— 这不是门禁问题」是一句**数据不支持的断言**，正是本仓库明令禁止的「把没有的事实画出来」。 | `direction_verdict` 的分母与 `MIN_SAMPLES` 只数 `long+short`；`unknown > 0` 时结论文案强制追加披露句；`bySide.unknown` 必须在卡片上有可见落点 |
| **P1-7** | §四 B-1 / §五 | `vetoedBy` 会有两份真源。B-1 说取 `open_funnel.classify_block`，但 `_emit_tool_call_trace` 里还硬编码着第二处 `"horizon"`——这正是 B-1 自己警告过的漂移形状，且漂移的表现（模型/界面读到一个不存在的层名）不报任何错。 | §五与 B-1 明确「两处共用同一判定函数」，并把 `_emit_tool_call_trace` 列进变更清单 |

### P2（记录在案，不阻塞实现）

- **P2-1** D-1 的伪代码 `action.removeprefix("open_")` 在 `low_confidence` 轮返回 `"hold"` 而不是散文里写的 `"unknown"`。散文对、代码错；本仓库有逐字照抄伪代码的前科，已在 v2 里把该行改对。
- **P2-2** `layerLabel` 若经任何带 `response_model` 的回执模型下发，须同步登记（本仓库有 `response_model` 静默吞新 wire 键的成文教训）；走 WS 原始 dict 的 trace 相位不受此限。实现时须确认一次。
- **P2-3** 文件尺寸：`session.py` 3063 行、`backtest.py` 1508、`decider.py` 1126、`prompts.py` 979、`api/ai_auto.py` 与 `AiTradeTraceRound.tsx` 均已越 `CLAUDE.md` 的 800 行线（`.claude-index/config.md` 有一张 19 文件的欠账表）。本方案新增纯核心是克制的（新建 `gate_veto.py`；`signals.py` 335→约 500、`open_funnel.py` 259→约 400，均在线内），但**不得再往 `session.py` 塞纯核心**，越线文件的增量须在 `.claude-index/config.md` 欠账表登记。
- **P2-4** A-4 的冷续跑配额归零（RK-10）：维持不落库的取舍是合理的，但「本会话已用 N/M」必须进 §7.2 的 status wire 让用户看得见——否则「重启刷配额」是一个既存在又不可见的行为。已在 §7.2 补。
- **P2-5** 可选（**本轮不做**）：`low_confidence` 轮的方向本可由 `AiDecider` 暴露一个只读 `last_intent_action` 保真，从而把 21% 的 `unknown` 收回真实方向轴。它是轴 D 的加分项，不是本轮的承重项；列在此处供下一轮取用。
- **P2-6** 轴 F 改的是 `_funding_carry_bps` 的语义，而该函数 docstring 第一行明写「**只取不利侧**」。改代码不改 docstring，就是让文档与实现分叉——轴 F 若实施，docstring 必须同步。
- **P2-7** 右尺寸：轴 F 与「方向对称」只有弱耦合，却牵动 edge 门禁 / 成本记账 / 已实现 edge 校准三条链路。建议在轴 A~E 的验收窗口结束前**不实现**，或直接拆成独立 spec。已写进《评审结论》的条件 4。

---

## 一、概述（Overview）

【AI 自动交易】是本仓库的核心产品：一个由 LLM 担任决策者、由十一层确定性门禁担任安全带的永续合约自动交易 Agent。它的宪章 prompt 第一条写着「**信号是证据，不是命令**——你有权推翻任何单一信号」，而它的动作集从第一天起就是对称的四元组 `open_long / open_short / close / hold`。也就是说，**做空在本系统里从设计上就是与做多完全对等的一等动作**，不是补充功能。

但生产库的取证结果是：自 2026-08-24 起的连续六个交易日、跨 BTC / ETH / SOL / DOGE / PEPE / SATS 六个标的、跨 15m 与 30m 两个执行周期，AI 自动交易一共开出 **41 笔多单、0 笔空单**；最后一次做空发生在 2026-08-18。而在 7 月的样本里两个方向是均衡的（例如 07-20 是 4 多 / 17 空，07-14 是 6 多 / 13 空）。同一份代码、同一套预设，方向分布从「均衡」塌缩成「单边」，这是一次真实的能力回归，不是行情的自然结果。

本方案要做的不是「强行让它去做空」——那是用配额掩盖问题，会直接损害盈利能力。本方案要做的是三件事：**（一）拆掉三处在物理上只会拦逆势那一侧的结构性单向阀**；**（二）把「模型这一轮到底想不想做空、想了又被谁拦下」从一个不可观测的黑箱变成一等的、可证伪的产品事实**；**（三）在十二阶段审议协议里把「方向对称论证」升格为硬约束**，让模型在每一轮都必须把做多与做空两个候选摆上台面、给出各自的净期望值，然后说明为什么选了这一个。修完之后，「上涨行情里做多占多数」应当仍然成立——但它必须是**论证出来的结论**，而不是**系统结构强加的唯一出口**。

方案覆盖后端纯核心（门禁、注记、协议、观测）、API 契约、前端启动弹窗与统计看板，并且**实盘 session 与 AI 回测 runner 共用同一份纯核心**（沿用本仓库 `base_rules` / `cost_model` / `edge_gate` 的成文姿态）：用户在回测里调出来的方向对称性参数，在实盘必须逐字生效，否则参数就是假的。

---

## 二、故障取证（历史交易分析）

取证对象是桌面端生产库 `%APPDATA%/aragon-desktop/aragon.db`（118 MB，取证时最后写入 2026-08-29 05:05），只读打开（`mode=ro&immutable=1`），不做任何写入。

### 2.1 方向分布按日切片

`SELECT date(ts,'unixepoch'), side, count(*) FROM trades t JOIN strategy_runs r ON r.id=t.run_id WHERE r.mode='ai-trade' GROUP BY 1,2`：

| 日期 | 开多 | 开空 | 平仓 |
|---|---|---|---|
| 2026-07-13 | 6 | 9 | 15 |
| 2026-07-14 | 6 | 13 | 19 |
| 2026-07-20 | 4 | 17 | 20 |
| 2026-07-21 | 11 | 10 | 21 |
| 2026-08-17 | 0 | 3 | 2 |
| 2026-08-18 | 0 | 1 | 0 |
| **2026-08-24** | **8** | **0** | 7 |
| **2026-08-25** | **5** | **0** | 5 |
| **2026-08-26** | **10** | **0** | 10 |
| **2026-08-27** | **1** | **0** | 1 |
| **2026-08-28** | **15** | **0** | 13 |
| **2026-08-29** | **2** | **0** | 4 |

全历史合计 `long=147, short=94, close=239`（评审节点复核，逐字相符）；**回归窗口 = 2026-08-19 ~ 2026-08-24 之间**，此后 41 : 0。

⚠️ **上表是抽样，不是全量**（评审订正）。全量按日表里还有 07-06 / 07-08 / 07-09 / 07-12 / 07-15 / 07-17 / 07-18 / 07-19 / 07-22 / 07-26 与 08-12 / 08-13 / 08-14 十余天，其中 **2026-07-22 单日就是 16 多 / 0 空**。这条反例必须写在这里：**「某一天 0 空」在本系统里不是新现象**，因此它不能当作论据。真正撑得住「结构性」这个词的，是**跨 6 个交易日、6 个标的、2 个执行周期、41 次开仓意图连续落在同一侧**这件事的联合概率——而不是任何单日的方向比。

### 2.2 经验图谱已方向退化

- `ai_experience_events`：28 条，**`side` 全部为 `'long'`，`short` 为 0 条**。
- `ai_experience_nodes` 中 `axis='side'` 的节点**只有一个**：`side:long`（`support=28`，`muted=0`）。`side:short` 节点在库里**不存在**。

这意味着经验检索（按上下文覆盖度打分）在结构上**只可能召回做多教训**，且「做空侧无教训」在模型看来与「做空侧教训是负面的」不可区分。

### 2.3 同窗口行情：强多头（必须诚实计入）

`candles_cache` 1H：BTC 64345 → 79975（**+24%**）、ETH 1905.49 → 2435.19（**+28%**）、DOGE 0.07038 → 0.09276（**+32%**）。

**结论必须写清楚**：41 : 0 中有相当一部分由行情体制解释，这不是纯粹的 bug。但「六天、六标的、两个周期、41 次开仓意图全部落在同一侧」在概率上仍然指向结构性单向阀——一个健康的双向系统在这种行情里应当出现少量的、经过论证的、降档的逆势试探，而不是**恒零**。本方案因此把目标定义为「拆掉结构性单向阀 + 让方向偏斜可观测」，而不是「把方向比拉回 50:50」。

### 2.4 观测面：**层轴已落库，缺的是方向轴**（v2 评审订正，含实测）

> v1 原文写的是「观测面缺失 / 产品今天根本回答不了」。评审节点复查生产库后**撤回这个结论**：
> 层轴的拦截计数早就在落库，只是从来没有人去查它。下面这张表是本次评审新取的证据，它把 R-1
> 从推断抬成了实测。

**已经存在的观测面**：`strategy_runs.metrics_json → aiStats.openFunnel`（`stats_ext.build_funnel_stats` 落盘）
逐会话持久化 `intents / blocked{层→次数} / topBlocker / submitted / filled / conversion`。
回归窗口的实测（只读打开，按 `end_ts` 归并的两批；两批的 `config_json.discipline.preset` **全部是
`balanced`**、`ai_trade_min_consensus = 1`、`baseRules.maBandEntry.directional = false`）：

| 批次 | run | 开仓意愿 | **consensus** | confidence | baseRuleMaBand | 其它 | 委托 | 成交 |
|---|---|---|---|---|---|---|---|---|
| 2026-08-26 | 5 | 647 | **347** | 111 | 101 | turnover 5 / reflection 5 | 78 | 13 |
| 2026-08-29 | 5 | 893 | **485** | 212 | 123 | turnover 13 / reflection 5 / drawdownBrake 1 | 54 | 17 |
| **合计** | 10 | **1540** | **832（54.0%）** | 323（21.0%） | 224（14.5%） | 29 | 132 | 30 |

最近 5 个 run 里 4 个的 `topBlocker` 就是 `consensus`（单 run 分别 64 / 103 / 154 / 164）。
**回归窗口里一半以上的开仓意愿死在共识门禁**——这是 R-1 的直接量化证据，不再是推断。

**真正缺失的是方向轴**（这一条 v1 说对了，且它现在是唯一还缺的那一块）：

- `open_funnel.OpenFunnelCounters` 的 `intents / blocked / submitted / filled` 全部是**方向合并的标量**，
  于是「这 832 次被拦的意愿里有几次是做空」在库里**无解**。
- `ai_trade_group_states.members_json` 只有持仓与挂单身份，**没有任何漏斗计数**（实时侧同样只有方向合并值）。
- `backend.log`（214 MB）里 `open_short` / `consensus` / `edge_gate` / `funnel` 出现次数为 0——门禁决策确实不落日志，
  但**日志从来不是这件事的证据源**，落库的 `aiStats` 才是；v1 把这条当成主要论据是取错了源。

### 2.5 取证结论：能证到哪一步，不能证到哪一步

**已证**（可复算，无需新代码）：
1. 回归窗口 41 : 0，最后一次做空在 2026-08-18，全历史 147 : 94（§2.1）。
2. 回归窗口 54% 的开仓意愿被共识门禁拦下，它是压倒性的第一拦截层（§2.4）。
3. 该窗口装的是 `balanced`（`min_consensus = 1`），信号板 2 个成员，MA 通道跑在非方向模式（§2.4）。
4. 同窗口行情是强多头，BTC/ETH/DOGE 分别 +24% / +28% / +32%（§2.3）。
5. 经验图谱 28 条事件 `side` 全为 `long`，`side:short` 节点在库里不存在（§2.2）。

**未证，且今天的库里也证不出**：那 832 次被共识门禁拦下的意愿里，**有几次的方向是 `open_short`**。
这正是轴 D 存在的全部理由，也是「轴 D 必须先行」的原因——因为这个问题的两个答案对应**完全相反**的补救动作：

- 若「提过做空、被门禁拦下」占多数 ⇒ 结构性单向阀成立，轴 A 是对症的；
- 若「从未提过做空」占多数 ⇒ 门禁再放宽也没用，对症的是轴 C（研判协议）与轴 E（先验去偏）。

**因此本方案不把 R-1 的修复当作已经验证的结论来交付**：轴 A 排在交付顺序第 4 位、且它的效果由轴 D 的读数来判定，
而不是反过来（§6.3）。这一点在 v1 里是隐含的，v2 把它写成明文。

---

## 三、根因分析

### R-1（P0）共识门禁是一只**单向阀** — `signals.py:252-259`

```python
if min_consensus <= 0 or decision.action not in ("open_long", "open_short"):
    return decision, None
votes = consensus.long_votes if decision.action == "open_long" else consensus.short_votes
if votes < min_consensus:
    ... → hold, "low_consensus(k/n)"
```

生产会话装的是 `balanced` 预设（`discipline.py:68`），`ai_trade_min_consensus = 1`；`steady` 是 2。信号板成员是策略 15（因子组合得分）与策略 28（大周期趋势带波段）。

**实测支撑（v2）**：回归窗口 1540 次开仓意愿里 832 次（54.0%）死在本层，最近 5 个 run 里 4 个的 `topBlocker` 就是它（§2.4）。这不再是一条推断出来的嫌疑，而是被落库数据点名的第一拦截层——但**它拦掉的是哪个方向仍然未知**（§2.5），故本层的修复必须排在轴 D 之后交付。

这条规则**看起来对称**（两个方向用同一个阈值），**在物理上却只会拦逆势那一侧**：单边行情里信号板必然一边倒，于是「同向票数 ≥ 1」对顺势方向恒成立、对逆势方向恒不成立。它把宪章里那句「你有权推翻任何单一信号」在**开仓这个唯一有意义的场景**里变成了空头支票。这是本次回归的主根因。

### R-2（P0）门禁否决对模型**完全静默** — `horizon_runtime.py:68-97` + `decider.py:931`

`build_gate_veto_note` 的第一行就把 `vetoedBy` 硬编码成 `"horizon"`：**只有持仓周期闸**会把「我提议了什么、被谁否了」写回模型的对话上下文。而 `commit_round` 收的是**门禁之后的最终 decision**（`session.py:1642` 注释「R-P1-3」明确如此）。于是共识 / edge / MA 通道 / 保护性平仓冷却 / 换手预算 / 交易所兜底 / 回撤刹车这**七道闸**的否决全部静默——模型下一轮读到的自己的历史是「上轮我决定观望」。

**评审补充的第二个静默面**：`prompts._format_consensus`（`prompts.py:411-417`）只渲染「N 多 / N 空 / N 空仓，净立场=…，一致度=…」，**从不渲染 `min_consensus` 这个阈值**——全仓搜索 `minConsensus` 只在回测 wire（`backtest.py:1357`）与请求 schema 里出现，实盘 payload 的 `consensus` 段里根本没有这个键。也就是说模型不但事后不知道自己被拦了（R-2 正文），事前也**不知道存在这条门槛、更不知道差几票**。A-5 因此不是锦上添花，而是本轴能否生效的前提。

这条缺陷本身是方向中性的，但它与 R-1 相乘就产生**方向性的记忆偏斜**：做多成功了会进 journal 与滚动摘要，做空被拦的十次从模型的历史里**彻底消失**。跑上十几轮，模型自己的记忆就变成了「我一直在做多，而且没什么想做空的时候」。仓库已经为这条同型缺陷修过两次（反思侧 `build_reflection_note` 的 F2、持仓周期侧的 F-2），这是第三次，落在方向轴上。

### R-3（P1）方向先验与经验图谱的**冷启动自我强化**

- `signals._weighted_net_stance`（`signals.py:130-142`）：无证据的一侧按中性 `0.5` **参与比较**。只要做多侧 `p>0.5` 而做空侧无样本，`netStance` 恒为 `long`。
- 经验图谱只有 `side:long` 节点（见 §2.2），注入的教训结构上只可能来自做多。

形成闭环：**不做空 → 无做空样本 → 先验与经验永远偏多 → 更不做空**。这是一个吸收态，不会自己走出来。

### R-4（P1）十二阶段审议协议**不强制双向对称论证** — `reasoning_protocol.py:150-163`

- S10 的原文是 "Put at least three candidate actions on the table (**one of them must be `hold`**)"——**没有任何一句要求两个方向都要有候选**。三个候选可以是 `open_long 25% / open_long 50% / hold`。
- S11 的 "steel-man **the opposite side**: the strongest possible argument for **the reverse action**"，在空仓状态下「reverse action」可以被合理地读成 `hold`，而不是「反方向开仓」。

于是「不能无脑开多」这条用户诉求在协议正文里**没有任何落点**。

### R-5（P2）观测面没有方向轴（见 §2.4）

`OpenFunnelCounters` 方向合并 ⇒ 前端 `OpenFunnelCard` 只能说「拦得最多的是 XX 层」，说不出「拦掉的是哪个方向」。**「有写点没读点」的镜像缺陷：这里是「连写点都没有」。**

### R-6（P2）资金费只取不利侧，**系统性低估做空 EV** — `cost_model.py:148-159`

`return max(0.0, signed)`：正资金费体制（本次取证窗口正是）做空实际是**收**资金费，被强行记为 0。这不会拦住做空（成本被高估只会让 edge 门禁更保守，而它对两侧都是 fail-open 的），但它让 `edge_bps ≥ k_min × cost_bps` 这道门禁对做空的通过标准**无谓地更高**。属可选修正，必须带总闸、默认关。

---

## 四、技术方案

总闸：`AI_TRADE_DIRECTIONAL_SYMMETRY_ENABLED`（`ai_trade_directional_symmetry_enabled: bool = True`）。**关闭时全仓逐字节回退到本特性合入前**——prompt 渲染、payload 键、wire 形状、degrade 码、前端渲染门控一律回到旧行为。这是本仓库处理「向决策链插入新语义」的成文纪律（`base_rules` §2.10 / `experience` E-15 同款）。

### 轴 A — 共识门禁改为「方向对称的证据通道」

**目标**：保留共识门禁的保护意图（不让模型在毫无策略支持时乱开仓），同时给它开一条**受严格约束、必须降档、有会话配额、对两个方向逐字同构**的逆势通路。

#### A-1 纯核心新增（`services/quant/app/ai_auto/signals.py`）

```python
@dataclass(frozen=True)
class ConsensusRule:
    """共识门禁的会话级不可变配置（随 AiTradeSession 生命周期）。"""
    min_consensus: int = 0
    contrarian_enabled: bool = False          # 逆势通路总闸；False ⇒ 行为逐字等于旧版
    contrarian_min_confidence: float = 0.65   # 模型自报置信度下界
    contrarian_max_opposing_votes: int = 1    # 反向票数上限（板子一边倒得太厉害就不放）
    contrarian_min_evidence_chars: int = 60   # contrarianEvidence 最小长度（中文字符数）
    contrarian_max_per_session: int = 3       # 会话级配额
    contrarian_size_mult: float = 0.5         # 强制降档系数（量化前）

@dataclass(frozen=True)
class ConsensusGateOutcome:
    decision: TradeDecision
    reason: str | None          # None = 放行
    lane: str                   # "consensus" | "contrarian" | "veto" | "exempt"
    contrarian_used: bool       # True ⇒ 调用方把会话配额 +1
    wire: dict                  # 进 trace / payload 的 camelCase 快照
```

#### A-2 判定顺序（`resolve_consensus_gate`，纯函数，实盘与回测共用）

**签名（v2 补全，P1-3）**——`CLAUDE.md` 限形参 ≤5，故 `confidence` 与 `contrarian_evidence`
**不做独立入参**（它们本来就在 `decision` 上，多传一份就是多一个可能漂移的真源）：

```python
def resolve_consensus_gate(decision: TradeDecision, consensus: Consensus,
                           rule: ConsensusRule, *, position_step: float,
                           used_contrarian: int) -> ConsensusGateOutcome:
```

三条**来源**约束（每一条都对应仓库里一个已经踩过的坑）：

1. `position_step` 的真源是 **`decider.position_step`**（`session.py:1803` / `:2265` 已有两处同款取法），
   回测侧取 runner 自己的那一份。`ConsensusRule` **刻意不带** step——步长是决策器的属性，不是纪律阈值，
   放进规则对象就会出现「规则里的 step 与决策器的 step 不是同一个」这种不报错的分叉。
2. `rule.min_consensus` 必须从 **`_resolve_discipline` 之后**的那份派生 settings 副本取
   （`self.executor.settings.ai_trade_min_consensus`，即 `_apply_consensus_gate` 今天现读的那一个）。
   在 `AiTradeSession.__init__` 里抢先快照会读到**旧的 settings 对象**——`api/ai_auto.py:340` 的
   [P1-2] / G-13 已经把这个顺序钉死过一次，纪律预设的阈值在那之前全是死字母。
3. `used_contrarian` 由调用方（session / runner）持有并传入，纯核心**不持状态**——这是它能被实盘与回测
   共用的前提。

```
1. rule.min_consensus <= 0 或 action ∉ {open_long, open_short}
       → lane="exempt"，返回**同一个** decision 对象（is 相等，绝不顺手 replace）
2. votes = long_votes if open_long else short_votes
   votes >= min_consensus                     → lane="consensus"，直通，同一个对象
3. rule.contrarian_enabled 为 False           → lane="veto"，degrade="low_consensus(k/n)"（**逐字不变**）
4. 逆势通路四条件依次判定（**顺序即降级码**，先判到哪条就报哪条）：
   a. opposing_votes > contrarian_max_opposing_votes → "low_consensus_opposed(k/n)"
   b. used_contrarian >= contrarian_max_per_session  → "low_consensus_quota(k/n)"
   c. confidence 为 None 或 < min_confidence         → "low_consensus_conf(k/n)"
   d. contrarian_evidence 长度 < min_evidence_chars  → "low_consensus_evidence(k/n)"
5. 四条全过 → lane="contrarian"，**强制降档**：
      size = quantize_size_fraction(size_fraction * contrarian_size_mult, step)
      size <= 0（被档位地板吃掉）→ 退回 lane="veto"，degrade="low_consensus_size(k/n)"
      否则 replace(decision, size_fraction=size, reason=原 reason)，reason 前缀不改
```

**五条不可动摇的不变式（改本轴前先读）：**

1. **对两个方向逐字同构**。判定分支里除了 `votes` 的取数那一行，不允许出现任何 `action == "open_short"` 的特判。否则我们只是把偏斜换了个方向。
2. **只可能更宽、绝不更窄**。逆势通路只在「原本必然被拦死」的轮次生效；它**不可能**把一个原本放行的决策变成 hold，也**不可能**放大仓位。
3. **fail-closed**。任一入参缺失 / 非法（`confidence` 非有限、`contrarian_evidence` 非字符串、`used_contrarian` 为 None）⇒ 不走逆势通路，退回原否决。「无法验证」不等于「合规」。
4. **全部新降级码以 `low_consensus` 开头**。`open_funnel._DEGRADE_PREFIXES` 里 `("low_consensus", "consensus")` 与 `discipline_runtime._DEGRADE_PREFIXES:29` 的同名条目因此**零改动**即可把五个细分码归到 `consensus` 那一格，漏斗继续闭合。这是刻意的：细分码是给人看的，归类真源只有一处。
   ⚠️ **但第三个消费者必须显式补**（v2 评审 P0-4）：`apps/web/lib/aiTrade.ts::DEGRADE_REASON_LABELS` 是降级码中文化的唯一表，今天**没有** `low_consensus` 条目（`aiTrade.ts:615` 的成文契约是「未命中一律原样回退」），不补就会把 `low_consensus_evidence(0/1)` 这样的原始英文码怼到用户脸上——而本方案的主张恰恰是「让方向被拦这件事对用户可读」。该表用 `find(startsWith)`，**顺序敏感**：四个细分码必须排在裸 `low_consensus` **之前**（与 `open_funnel._DEGRADE_PREFIXES` 里 `horizon_turnover_*` 排在裸 `horizon_` 之前的成文纪律逐字同构）。五个细分码 + 裸码共六条，一并补齐。
5. **`contrarian` 不是降级**。它进 trace 与漏斗的 `contrarianPassed` 独立计数，**不进 `blocked`**（镜像 `sizing_zeroed` 不是一道闸的成文纪律）。

`apply_consensus_gate(decision, consensus, min_consensus)` 的三参旧签名**保留不动**，实现改为 `resolve_consensus_gate(..., rule=ConsensusRule(min_consensus=min_consensus))` 的薄封装并返回 `(out.decision, out.reason)`——既有单测与任何未迁移调用点逐字节回退。

#### A-3 决策工具新增 `contrarianEvidence`

**不复用 `evidence_digest`**：`decider.py:206-209` 明文冻结了它是「**纯呈现物**——缺席 / 非字符串 / 超长 / 写成英文四种情形一律不产生 degrade、不改任何交易语义」。把它变成承重字段是一次静默的契约破坏。

改为**镜像 `exitEvidence` 的既有先例**新增一个专用字段：

- `TradeDecision.contrarian_evidence: str | None = None`（**末位带默认值** ⇒ 既有全部位置构造与元组解包零改动）。
- 工具 schema（`prompts.py` / `prompts_en.py`）新增 `contrarianEvidence`：中文，「当你的开仓方向与信号板净立场相反时，用不少于 N 字说明你推翻了哪几条信号、依据是什么、什么现象会让你立刻认错」。
- `decider._map_decision` 只在 `action in ("open_long","open_short")` 分支解析（与 `holdExitPx` 只在 hold 分支解析的 [P2-3] 纪律逐字同构，否则残留值会泄漏到别的动作）。**确切落点**是 `decider.py:754-756` 那条**提前 return**（`TradeDecision(action, size, confidence, reason, ord_type, limit_px, evidence_digest=digest)`）——开仓分支走的是它，不是文件末尾那条六位置参的 return；改错行的表现是字段恒为 None 且不报任何错。
- 值域外 / 类型错 **一律忽略、绝不据它降级**（沿用 `orderType` 的 F-2 先例）——它的缺席只会让逆势通路不可用，而那本就是旧行为。

#### A-4 会话侧装配（`session.py`）

- `AiTradeSession.__init__` 持 `self._consensus_rule: ConsensusRule` 与 `self._contrarian_used: int = 0`。
  ⚠️ `rule.min_consensus` 必须取自 `_resolve_discipline` **之后**的那份派生 settings 副本（A-2 来源约束 2）；本轴**只装配逆势通路的五个阈值**，`min_consensus` 本身仍由纪律预设单独拥有，绝不在两处各存一份。
- `_apply_consensus_gate` 改调 `resolve_consensus_gate(...)`，`out.contrarian_used` 为真时 `self._contrarian_used += 1`。
- `out.wire` 经 `_round_trace["consensusGate"]` 进轨迹（镜像 `apply_edge_gate_trace`）。
- 续跑（`resume.py`）：`_contrarian_used` **不落库、冷续跑归零**。理由：配额的语义是「一个会话内的逆势试探次数」，而冷续跑本就是新会话；落库反而要多维护一份可能漂移的状态。此决定需在评审节点复核。

#### A-5 模型必须**知道这条通路存在**（否则就是「有写点没读点」）

`payload["consensus"]` 增加 `contrarian` 子对象：`{available, minConfidence, minEvidenceChars, maxOpposingVotes, remaining, sizeMult}`。`prompts._format_consensus` 追加一行：

> 逆势通路：本轮做空与信号板净立场相反，共识门槛不满足。若你确信，请把 confidence 提到 ≥0.65 并在 `contrarianEvidence` 里用不少于 60 字写清你推翻了哪几条信号——系统会放行，但仓位强制减半。本会话还剩 2 次。

总闸关闭 / 通路不可用 ⇒ **整键缺席** ⇒ prompt 逐字节回退。

### 轴 B — 门禁否决一律回写模型上下文

#### B-1 新模块 `services/quant/app/ai_auto/gate_veto.py`（纯核心）

```python
def build_gate_veto_note(proposed, degrade, *, priority=None,
                         consensus_wire=None) -> dict | None:
    """把本轮**任一确定性门禁**的否决压成记忆注记；未被否决 ⇒ None。"""
```

- `vetoedBy` **不新写一份层名表**，直接取 `open_funnel.classify_block(degrade)`（单一真源；两处各写一份必然漂移，而漂移的表现是模型读到一个不存在的层名，且不会报任何错）。
- `layerLabel` 取 `open_funnel.LAYER_LABELS[key]`。
- 注记形状：`{"vetoedBy", "reason", "proposedAction", "proposedReason"}`，**总闸开时**追加 `"layerLabel"`。
  **`proposedAction` 是本轴的全部价值所在**——它带着方向（`open_short`），这正是模型下一轮需要知道的那个事实。
  ⚠️ **`layerLabel` 必须跟随总闸**（v2 评审 P0-2c）：今天的注记只有四键，若无条件新增第五键，B-1 自己那句「horizon 否决轮逐字节等于今天」与 §9.1 的冻结用例就直接互斥、实现必红。总闸关 ⇒ 四键 ⇒ 逐字节等于今天；总闸开 ⇒ 五键 ⇒ 这是**有意的**行为变更，由改名后的用例守（见 §9.1）。
- ⚠️ **`vetoedBy` 只允许有一份真源**（v2 评审 P1-7）：`session._emit_tool_call_trace`（`session.py:1958`）里今天硬编码着第二处 `"horizon" if vetoed else None`。本轴必须把**那一处也换成同一个判定函数**，否则就是 B-1 自己警告过的漂移形状——而漂移的表现（模型与界面读到一个不存在的层名）不报任何错。
- `vetoedBy == "horizon"` 时**继续附加**今天已有的五个字段（`exitConviction` / `exitLevel` / `downgradeReason` / `intentStreak` / `intentLapses`），使 horizon 否决轮的注记**逐字节等于今天**。
- `vetoedBy == "reflection"` ⇒ **返回 `None`**。反思否决另有 `build_reflection_note` 承载，两条注记同时出现会让模型读到重复事实。这是一条硬约束。
- `vetoedBy == "consensus"` 时附加 `{"longVotes", "shortVotes", "minConsensus", "contrarianAvailable", "contrarianRemaining"}`——让模型知道「还差几票 / 还能不能走逆势通路」。
- `horizon_runtime.build_gate_veto_note` 保留原三参签名，实现改为委托本模块（既有单测零改动）。

#### B-2 session 接线（`session.py:_run_decision_round`）

```python
decision, degrade = await self.decider.decide(...)
llm_degrade = degrade               # 既有（session.py:1580）：门禁改写之前的**模型侧**码
stage1_decision = decision          # ★ 新增：任何门禁之前的引用（B-2）
...
gate_entry = decision               # 既有，保持不动（horizon 层的 proposed 来源）
...
decision, degrade, reflection_note = await self._reflect_before_execute(...)
gate_degrade = degrade              # ★ 新增留存点：执行**之前**的 degrade（B-2）
decision = await self._apply_sizing(...)          # 既有：会 replace 出新对象
executed = await self._execute_decision(...)      # 既有
degrade = degrade or executed.get("closeBlocked") # 既有（session.py:1633）：执行侧码
...
gate_veto=build_gate_veto_note(
    stage1_decision, gate_degrade, llm_degrade=llm_degrade,
    priority=self._exit_priority,
    consensus_wire=self._round_trace.get("consensusGate"))
```

**产出判据（v2 评审 P0-2 改写）**：`gate_degrade is not None and gate_degrade != llm_degrade`。
v1 用的 `decision is not stage1_decision` 有两个致命处：

- **它恒为真**。`_apply_sizing`（`session.py:1642` 上一行）本就会 `replace` 出一个新对象，
  任何开仓轮该判据都成立，于是它根本没有在筛任何东西——真正兜住的是 `degrade` 非空。
- **`degrade` 在那个位置已经被污染**。`degrade = degrade or executed.get("closeBlocked")`（`session.py:1633`）
  排在 `commit_round` **之前**，于是 `exit_order_slot_busy` 会经 `classify_block` 落进 `"other"`
  （该码不在 `_DEGRADE_PREFIXES` 任何前缀里），模型会读到一条**凭空捏造**的「我被『其它』层拦下了」。
  取 `gate_degrade`（反思之后、`_apply_sizing` 之前）从根上避开它，且**不依赖任何对象身份**。

`llm_degrade` 参数用来排除**第一阶段模型侧**的码（`low_confidence` / `no_tool_call` / `missing_size` /
`reasoning_truncated` / …）：这类轮次 `_map_decision` 已经把 action 洗成 hold（`decider.py:749`），
`stage1_decision.action == "hold"`，注记里的 `proposedAction` 会**撒谎**——而它带方向正是本轴宣称的全部价值。
`gate_degrade == llm_degrade` ⇒ 本轮没有任何**确定性门禁**改写过决策 ⇒ 返回 `None`。

**零回归论证（v2 收紧）**：
- horizon 否决轮：`gate_degrade` 恒等于 `self._horizon.last_reason`（被本层拦下后 decision 已是 hold，
  下游各闸对 hold 一律早返回、不会再改写 degrade），且 `gate_entry is stage1_decision`
  （基础策略层放行时返回同一个对象；被它拦下时 degrade 是 `base_rule_*`，此后 horizon 不会再拦一次），
  故 `proposedAction` / `proposedReason` / `reason` 三键与今天逐字相同；`layerLabel` 由总闸门控（见 B-1）。
- 未被任何确定性门禁拦下的轮次 ⇒ `gate_veto is None` ⇒ `commit_round` 的 `tool_result` 内容逐字节不变
  （R-05 冻结契约）。

#### B-3 前端呈现：**泛化既有通路，绝不新造第二条**（v2 评审 P0-3 重写）

> v1 写的是「在 `AiTradeTraceRound.tsx` 新增一条独立降级行」。评审发现**这条线今天已经端到端存在**，
> 只是作用域被硬钉在 `horizon`。照 v1 实现会造出第二条平行通路；而只放开后端一侧，会立刻把英文层名
> 显示给用户。故本节改为「泛化 + 补一个后端下发的中文标签」。

**今天已经存在的完整链路**（评审逐跳核对过）：

```
session._emit_tool_call_trace (session.py:1958)
    → trace_event("tool_call", …, proposedAction=…, proposedReason=…, vetoedBy=…)   # 三个兄弟键
    → store.ts  emptyRound(:222-226) 初始化 + reduceRoundPhase 的 case "tool_call"   # ★ 逐键手抄
    → aiTradeTypes.ts:1045 / :1157                                                  # 类型
    → aiTradeExit.ts:184-189  describeVetoedProposal()                              # 文案
    → AiTradeExitPriorityRows.tsx::ProposedVetoRow                                  # 渲染
       （AiTradeTraceRound.tsx:37 只是 import 它，不是渲染点）
```

作用域今天被钉死在 `"horizon"`：`_emit_tool_call_trace` 的判据是
`proposed.action != decision.action and self._horizon.last_reason is not None`，`vetoedBy` 直接写字面量
`"horizon"`（该函数 docstring 明写「作用域钉死在**本层**」）。

**本轴要做的四件事**（缺任一件都会退化成一个说谎的界面）：

1. **后端判据泛化**：`_emit_tool_call_trace` 改用与 `gate_veto.py` **同一个**判定函数
   （`classify_block(gate_degrade)`），`vetoedBy` 取它的返回值。判据同样换成 B-2 的 `gate_degrade`
   口径，不要沿用 `proposed.action != decision.action`（`_apply_sizing` 会让它失真）。
2. **后端新增 `layerLabel` 兄弟键**，值取 `open_funnel.LAYER_LABELS[key]`。
   **中文标签必须由后端下发**——这是本仓库的成文纪律（`discipline._THRESHOLD_LABELS` 的 docstring：
   「数值与中文标签全部由后端下发，前端不复制任何一个」）。总闸关 ⇒ 该键缺席 ⇒ 前端逐字回退。
3. **`store.ts` 必须显式补这一个键**：`emptyRound` 的初始化块与 `reduceRoundPhase` 的 `case "tool_call"`
   **两处都要**。这里是真正的逐键手抄点（该文件已有四条同款警示注释），漏抄的表现是「后端发了、前端收不到、
   跑不出错」。
4. **`aiTradeExit.ts::describeVetoedProposal` 改吃 `layerLabel`**：
   `const by = layerLabel ?? (vetoedBy === "horizon" ? "交易周期层" : "门禁");`
   —— 保留 `"horizon"` 那条既有映射**只是为了老快照**（合入前落的 trace 里没有 `layerLabel`）；
   **绝不允许**回退到 `vetoedBy ?? "门禁"` 那条旧分支，否则 `consensus` / `baseRuleMaBand` 会以英文 key
   出现在界面上。同步补 `aiTradeTypes.ts` 的 `layerLabel?: string | null`。

最终效果：`提议 做空 25%，被「同向共识门槛」降级`。今天这句话只有 horizon 层说得出，其余七层是空白。

### 轴 C — 审议协议的方向对称化

#### C-1 `reasoning_protocol.py` 三处 mandate 就地改写

- **S7 CONFLICT** 末尾追加：
  `"State the net directional lean, its strength on a 0-1 scale, **and the strongest case for the opposite direction stated as its own advocate would state it**. If you cannot state the opposite case, you have not finished this stage."`
- **S10 HYPOTHESIS** 把括号改写为：
  `"(the set must contain at least one long candidate, at least one short candidate, and hold)"`，其余（入场逻辑 / 证伪价 / 目标 / 胜率 / 净 EV / 排序）不变。
- **S11 FALSIFY** 把 `the reverse action` 明确化：
  `"the reverse action - when you are flat, that means opening in the opposite direction, **not** holding"`。

#### C-2 新增方向对称宪章条款（中英双写，跟随总闸）

**插入点（v2 评审 P1-4 订正）**。v1 写的「`prompts.py` 新增 `DIRECTION_SYMMETRY_CHARTER`（进
`SystemPromptTexts.charter()`）」在仓库里不成立，有两处事实错：

- `SystemPromptTexts.charter()` 在 **`prompts_en.py:40`**（不在 `prompts.py`），而且它只是把五个既有文本段
  拼起来，**不是扩展点**；
- 中文脊柱 `_render_spine_zh`（`prompts.py:141-146`）**另有一份内联的同款拼接**，根本不调 `charter()`。
  只改一处，条款就只出现在一条语言路径上——而生产路径是**英文脊柱**（`texts.reasoning_text` 非空即走
  `render_spine_en`，深度思维链合入后它恒非空），中文脊柱是 `off` 档的回退真源。

**正确改法**（与既有五段宪章逐字同构，故零新机制）：

1. `SystemPromptTexts` 新增**第六个文本字段** `direction_text: str = ""`（默认空 ⇒ 两条脊柱都逐字节回退）。
2. `SystemPromptTexts.charter()` 与 `_render_spine_zh` 里那份内联拼接**两处都追加**它，并同步更新
   `charter()` docstring 里「五段 / 顺序：在哪开 → 何时锁 → 下多大注 → 付多少 → 持多久」那句话
   ——本条追加为**第六段「往哪边开」**，排在末位。
3. 文本由两个装配点按总闸填：`manager.py:685` 与 `backtest.py:339`（两处都构造 `SystemPromptTexts`；
   只填一处 ⇒ 实盘与回测的 prompt 分叉，正是 `base_rules` P1-4 那条成文教训）。
4. `prompts_en.py` 的 `render_spine_en` 在 DECISION DISCIPLINE 第 3 条后插入对应英文段。

中文条款原文：

> **方向对称条款**：这是一个可以双向交易的永续合约账户。做空与做多在本系统里是**完全对等**的一等动作，二者共用同一套仓位、成本、止损与门禁标准。上涨行情里做多不是默认答案，它只是「当下证据更强的那一个答案」；同理，下跌行情里做空也不是默认答案。你必须在 S10 同时给出做多与做空两个候选并各自算出净期望值，然后在 S12 说明你为什么选了这一个、放弃了另一个。**只列出一个方向的候选，等同于没有完成 S10。**

#### C-3 反思侧（`REVIEW_STAGES`）追加一句

复核官「只能收紧、不能改方向」的既有语义不动，追加：`"Apply exactly the same standard of evidence to a short proposal as you would to a long one; a proposal is never weaker merely because it is a short."`

#### C-4 冻结契约

总闸关闭 ⇒ 三处 mandate 与两段宪章**整体不渲染** ⇒ prompt 逐字节回退。开启时 `tests/test_ai_trade_prompts.py` / `test_ai_trade_reasoning_protocol.py` 里的字节冻结断言需要**同步更新**（这是有意的行为变更，不是回归）。

### 轴 D — 方向轴观测（漏斗 / 落库 / 前端）

#### D-1 `open_funnel.py` 增方向切分

```python
SIDES = ("long", "short", "unknown")      # 三格固定；unknown 单列，绝不并进任何一侧

@dataclass
class OpenFunnelCounters:
    ...  # 既有六个标量字段全部不动
    intents_by_side: dict = field(default_factory=dict)        # {"long": n, "short": n, "unknown": n}
    blocked_by_side: dict = field(default_factory=dict)        # {side: {layer: n}}
    submitted_by_side: dict = field(default_factory=dict)      # ★ v2 补：曾被 D-2/闭合式引用却从未声明
    sizing_zeroed_by_side: dict = field(default_factory=dict)  # ★ v2 补：同上
    filled_by_side: dict = field(default_factory=dict)         # 事件级：按成交腿自己的 side
    contrarian_passed: int = 0                                 # 走逆势通路放行的轮数（不进 blocked）
    _intent_side: str | None = None                            # 轮级：本轮意愿的方向
```

**四个记点各自的方向来源（v2 评审 P0-1 重写）**。v1 写的是「`note_block` / `note_filled` 按
`_intent_side` 分流」——这句话对前者成立、对后者**物理上不可能**：

| 记点 | 现有签名 | 方向从哪来 | 为什么 |
|---|---|---|---|
| `note_intent` | `(action, degrade)` | 置位 `_intent_side` | 唯一能拿到原始 `open_*` 的地方 |
| `note_block` | `(degrade)` | 读 `_intent_side` | 与 `_intent_pending` 同一轮、同一次消费 |
| `note_executed` | `(action, skipped)` | 读 `_intent_side` | **`submitted_by_side` 的唯一记点**（v1 从未提到本函数） |
| `note_sizing` | `(action, size_fraction)` | 读 `_intent_side` | `sizing_zeroed_by_side` 的唯一记点 |
| `note_filled` | `()` → **改为 `(side)`** | **成交腿自己的 `side`** | 见下 |

`note_filled()` 今天是**无参**的事件级记点（`open_funnel.py:196`），由 `_dispatch` 里
`side ∈ ("long","short")` 的成交事件驱动。它排在 `note_executed`（`open_funnel.py:186`，
**已把 `_intent_pending` 置 `False`**）之后，一次部分成交还会产生多条腿、甚至跨轮到达——此刻
`_intent_side` 早已被清。若照 v1 去读它，`longFilled` / `shortFilled` **恒为 0**，轴 D 卡片的
「成交」实心段永远画不出来，而且不会报任何错。故 `note_filled` 改为显式收成交腿自己的 `side`
（调用点本来就有这个值，零新取数）。

- `note_intent(action, degrade)` 追加方向置位。⚠️ **不能写 `action.removeprefix("open_")`**
  （v2 评审 P2-1：v1 伪代码如此，但散文写的是 `"unknown"`，两者矛盾）——`low_confidence` 那条路径此刻
  `action` 已被 `_map_decision` 洗成 `"hold"`，`removeprefix` 会原样返回 `"hold"`，凭空造出第四个桶。
  正确写法：
  ```python
  self._intent_side = (action[len("open_"):] if action in _OPEN_ACTIONS
                       else "unknown") if wants_open else None
  ```
- `end_round` 一并清零 `_intent_side`（R-12 同款兜底：决策轮抛异常时 `note_block` 不会被调到）。
- **闭合不变式按方向各自成立**：
  `sum(blocked_by_side[s].values()) + submitted_by_side[s] + sizing_zeroed_by_side[s] <= intents_by_side[s]`，
  且三侧求和后必须逐字等于既有的四个标量（`intents` / `blocked` / `submitted` / `sizing_zeroed`）
  ——**方向切分是对同一批事件的重新分组，不是第二次计数**。
- ⚠️ **`unknown` 不是边角料**（v2 评审 P1-6）：§2.4 的实测里 `blocked.confidence` 占 323/1540 ≈ **21%**，
  这些轮全部落进 `unknown`。任何据 `bySide` 下的结论都必须把它显式披露（见 D-2 / D-4）。

#### D-2 `funnel_wire` 增两个键

```jsonc
{
  "bySide": {
    "long":    {"intents": 41, "blocked": {...}, "submitted": 41, "sizingZeroed": 0, "filled": 41},
    "short":   {"intents": 0,  "blocked": {},    "submitted": 0,  "sizingZeroed": 0, "filled": 0},
    "unknown": {"intents": 12, "blocked": {"confidence": 12},     "submitted": 0, "sizingZeroed": 0, "filled": 0}
  },
  "directionBalance": {
    "longIntents": 41, "shortIntents": 0,
    "longFilled": 41,  "shortFilled": 0,
    "shortIntentShare": 0.0,          // 分母为 0 ⇒ null，绝不写 0
    "shortFillShare": 0.0,
    "unknownIntents": 12,             // ★ v2 补：方向不可知的开仓意愿数（见下）
    "contrarianPassed": 0,
    "verdict": "long_skewed"          // balanced | long_skewed | short_skewed | insufficient
  }
}
```

`verdict` 判据（纯函数 `direction_verdict(counters)`，单一真源，前端只做布局）。
**分母口径（v2 评审 P1-6 钉死）：`directed = intents_by_side["long"] + intents_by_side["short"]`，
`unknown` 一律不进分母、也不进任何 share。**

- `directed < MIN_SAMPLES(=8)` ⇒ `"insufficient"`（**绝不把 0/0 画成 0%**，沿用本仓库成文纪律）；
- 某一侧 `intents == 0` 且另一侧 `>= MIN_SAMPLES` ⇒ `"long_skewed"` / `"short_skewed"`；
- 弱侧占比 `< 0.15` ⇒ 同上；否则 `"balanced"`。

`unknownIntents` **必须下发且必须在卡片上有落点**：`low_confidence` 轮在 `_map_decision` 里就被洗成
hold（`decider.py:749`），方向在系统里从来没有出现过。实测它占 21%（§2.4）。不披露这个数，
`verdict == "short_skewed"` 这个结论就是**一句数据不支持的断言**——「模型没提过做空」与
「模型提过做空但在方向可见之前就被置信度门槛拦掉了」在这份数据里是不可区分的。

#### D-3 落库与历史聚合

`stats_ext.build_funnel_stats` 无需改动（`bySide` / `directionBalance` 随 `openFunnel` 键整体落进 `aiStats`，**零 DB 迁移**）。`db/ai_auto_repositories.py` 的历史聚合增两个加法读点（`longIntents` / `shortIntents`），使「历史上这台机器有没有做空过」可跨会话回答。

#### D-4 前端新卡 `DirectionBalanceCard.tsx`

置于 `OpenFunnelCard` 正下方（因果相邻：漏斗说「被谁拦」，本卡说「拦的是哪个方向」）。设计要点（顶级人机交互标准）：

- **一条以 0 为中心的双向水平分歧条**：向右为做多、向左为做空，长度按 `intents` 归一；成交部分用实心、被拦部分用条纹填充（同一条上就能读出「想开多少 / 成了多少」）。做多用 `--up`、做空用 `--down` 的既有语义色，**不新引入色板**。
- 中心 0 轴始终可见——这是「对称」这个概念的视觉锚点；单边塌缩时用户一眼就能看见整条都在一侧。
- 条下两行紧凑读数：`做多 想开 41 · 成交 41` / `做空 想开 0 · 成交 0`。
- **一句结论文案**，按 `verdict` 分支且**必须区分两种完全不同的病因**：
  - 有做空意愿但被拦：`本会话模型提出过 6 次做空意愿，其中 5 次被「同向共识门槛」拦下 —— 这是门禁问题，可在启动弹窗放宽「逆势通路」。`
  - 从未提出做空意愿：`模型本会话从未提出过做空意愿 —— 这不是门禁问题。放宽任何纪律都不会有效果，请检查信号板方向与研判协议。`
  - 样本不足：`样本不足（方向可辨的开仓意愿 3 次 < 8），不下结论。`
  - ⚠️ **`unknownIntents > 0` 时，上面任何一句都必须追加披露句**（v2 评审 P1-6，实测该桶占 21%）：
    `另有 N 轮在方向可见之前就被「置信度门槛」拦下，方向不可知——本结论只覆盖其余 M 轮。`
    没有这一句，「从未提出过做空意愿」就是一句系统证不出来的话，而它恰好会把运维引向完全错误的补救方向。
  - `bySide.unknown` 在分歧条上**单独画一段中性色**（既不在左也不在右，压在 0 轴上），
    并在读数行里占一行：`方向不可知 想开 12 · 成交 0`。它不参与左右两侧的长度归一。
- 无 `directionBalance` 键（老会话 / 总闸关）⇒ **整卡不渲染**（渲染门控：绝不在没有数据的会话上挂一排 0）。

#### D-5 启动弹窗新增 `AiTradeDirectionFields.tsx`

镜像 `AiTradeHorizonFields` / `ProfitGuardFields` 的既有形态，放在「交易纪律预设」之后：一个「逆势通路」开关 + 三个数值（置信度下界 / 会话配额 / 降档系数），默认值由服务端回填、留空即用服务端默认（**唯一默认真源在 `config.py`，绝不在前端硬编码**）。开关旁一行释义：「允许 Agent 在有充分证据时逆信号板方向开仓，仓位强制减半。关闭后，Agent 只能顺着信号板的方向开仓。」

### 轴 E — 先验与经验的冷启动去偏

- **E-1** `signals._weighted_net_stance`：一侧无证据时**不再**按 0.5 参与比较，返回 `"flat"`；`weighted_consensus_wire` 增 `netStanceBasis ∈ {"both","single_side","none"}`。`prompts._format_prior_side` 的 `"无证据"` 改为 `"无证据（样本不足，**不是**做空胜率低的证据）"`。
- **E-2** 经验检索：`side` 轴在**方向退化**（某一侧 `support == 0`）时**整轴退出评分**（不参与上下文覆盖度加权），并在注入抬头如实写一句「经验库当前只有做多样本，不可据此推断做空更差」。落点在 `ai_auto/experience/` 的检索与渲染两处，**只加不减**：退化判据不成立时逐字回退。
- **E-3** 经验事件的方向配额不做任何改动——我们修的是「无样本被当成负面证据」，不是「凭空造样本」。

### 轴 F — 资金费真值化（可选，默认关）

`AI_TRADE_COST_FUNDING_CREDIT_ENABLED`（默认 `false`，逐字回退）。开启后 `_funding_carry_bps` 返回 `signed` 并夹到 `>= -funding_credit_cap_bps`（默认 `5.0`，防止一个异常费率把成本算成负的），`total_bps` 相应下降。**默认关闭是刻意的**：它改的是成本口径，会同时影响 edge 门禁、成本记账与已实现 edge 校准三条链路，需要独立的验证窗口。

---

## 五、文件 / 模块变更清单

### 后端（`services/quant/`）

| 文件 | 动作 | 一句话意图 |
|---|---|---|
| `app/ai_auto/signals.py` | 改 | 新增 `ConsensusRule` / `ConsensusGateOutcome` / `resolve_consensus_gate` 与五个细分降级码；`apply_consensus_gate` 降为薄封装保持三参旧签名；`_weighted_net_stance` 单侧无证据改判 `flat` |
| `app/ai_auto/gate_veto.py` | **新建** | 通用门禁否决注记；`vetoedBy` 取 `open_funnel.classify_block`（单一真源）；`reflection` 层显式返回 `None` |
| `app/ai_auto/horizon_runtime.py` | 改 | `build_gate_veto_note` 保留三参签名，实现委托 `gate_veto.py` |
| `app/ai_auto/open_funnel.py` | 改 | 三个 `*_by_side` 计数 + `contrarian_passed` + `_intent_side` 轮级标志；`funnel_wire` 增 `bySide` / `directionBalance`；新增纯函数 `direction_verdict` |
| `app/ai_auto/decider.py` | 改 | `TradeDecision.contrarian_evidence`（末位带默认值）；`_map_decision` 仅在 `open_*` 分支解析 `contrarianEvidence`；非法值一律忽略 |
| `app/ai_auto/reasoning_protocol.py` | 改 | S7 / S10 / S11 三处 mandate 就地对称化；`REVIEW_STAGES` 追加同标准一句 |
| `app/ai_auto/prompts.py` | 改 | `DIRECTION_SYMMETRY_CHARTER`；`_format_consensus` 增逆势通路一行；工具 schema 增 `contrarianEvidence`；`_format_prior_side` 的「无证据」措辞 |
| `app/ai_auto/prompts_en.py` | 改 | **v2 评审 P1-4**：`SystemPromptTexts` 加第六个文本字段 `direction_text`（默认空 ⇒ 逐字节回退）并在 `charter()`（`:40`）里追加；`render_spine_en` 插英文段；同步更新 `charter()` docstring 里「五段 / 顺序」那句话 |
| `app/ai_auto/session.py` | 改 | 持 `_consensus_rule` / `_contrarian_used`；`_apply_consensus_gate` 改调 `resolve_consensus_gate`；`stage1_decision` **与 `gate_degrade`** 两个留存点（B-2）；`commit_round` 的 `gate_veto` 换用通用注记；`payload["consensus"]["contrarian"]` 注入；**`_emit_tool_call_trace`（`:1958`）的 `vetoed` 判据与 `vetoedBy` 改用与 `gate_veto.py` 同一个判定函数并新增 `layerLabel` 兄弟键**（v2 评审 P0-3 / P1-7：那里今天硬编码着第二份层名，两处不同源必漂移且不报错） |
| `app/ai_auto/backtest.py` | 改 | runner 持同一份 `ConsensusRule` 与逆势计数，改调 `resolve_consensus_gate`（实盘 / 回测共用纯核心，参数不得失真） |
| `app/ai_auto/backtest_manager.py` | 改 | 把 `ConsensusRule` 随 spec 透传 |
| `app/ai_auto/manager.py` | 改 | 会话装配时把解析后的 `ConsensusRule` 交给 session；`AiTradeGroupStartRequest`（`:165`）加 `direction_rule` 字段；**`SystemPromptTexts` 构造点（`:685`）填 `direction_text`**——`backtest.py:339` 是同款的第二个构造点，只填一处会让实盘与回测的 prompt 分叉（`base_rules` P1-4 成文教训） |
| `app/ai_auto/discipline.py` | 改 | **v2 评审 P1-2 订正作用域**：`aggressive` 的 `ai_trade_min_consensus` 就是 `0`、`off` 走 `config.py:195` 的默认 `0` ⇒ 共识门禁在这两档**根本不触发**，逆势通路在那里是死配置。本轴的真实作用域只有 `steady`(2) 与 `balanced`(1) —— 而生产回归正是 `balanced`。故只给这两档配默认值（`steady` 关 / `balanced` 开），另两档**一个键都不加**；`_THRESHOLD_LABELS` 行表同步 |
| `app/ai_auto/experience/`（检索 + 渲染两处） | 改 | 方向退化时 `side` 轴退出评分并如实声明 |
| `app/ai_auto/cost_model.py` | 改 | 轴 F：`_funding_carry_bps` 在总闸开启时返回夹紧后的 signed 值 |
| `app/config.py` | 改 | 九个新配置项（见 §七.3） |
| `app/schemas.py` | 改 | `AiTradeDirectionIn` + 启动体 `direction` 字段 + 回执镜像 + 越界 422 |
| `app/api/ai_auto.py` | 改 | `_resolve_direction`（「字段缺省 ⇒ 按 settings 装配开启态」的单点解析，与 `_resolve_base_rules` 同惯例），调用点排在 `_resolve_discipline` **之后** |
| `app/db/ai_auto_repositories.py` | 改 | 历史聚合增 `longIntents` / `shortIntents` 两个加法读点 |
| `.env.example`（仓库根） | 改 | 九个新变量逐条登记（**漏登记是静默的**：`AI_TRADE_` 前缀已在桌面端 prod 透传白名单内，但未登记的变量在文档上不存在） |

### 前端（`apps/web/`）

| 文件 | 动作 | 一句话意图 |
|---|---|---|
| `lib/aiTradeTypes.ts` | 改 | `AiTradeOpenFunnel` 增 `bySide` / `directionBalance`；新增 `AiTradeDirectionRule` |
| `lib/aiTradeFunnel.ts` | 改 | `directionRows` / `formatDirectionShare` / `directionVerdictText` 三个纯函数（结论文案的唯一真源） |
| `components/Agent/aiTradeStats/DirectionBalanceCard.tsx` | **新建** | 双向分歧条 + 两行读数 + 一句分病因结论 |
| `components/Agent/aiTradeStats/DirectionBalanceCard.test.tsx` | **新建** | 三种 verdict 的渲染 + 零样本不造百分比 |
| `components/Agent/aiTradeStats/AiTradeStatsLive.tsx` | 改 | 在 `OpenFunnelCard` 下方挂载新卡 |
| `components/Agent/aiTradeStats/AiTradeStatsDetail.tsx` | 改 | 历史页挂载新卡，数据取 `summary.openFunnel.directionBalance`（`aiTradeTypes.ts:1295` 已有该键）。⚠️ **注意本页今天并没有 `OpenFunnelCard`**（它只在 Live 页，`AiTradeStatsLive.tsx:227`），故 v1 那句「同款挂载」没有锚点——本页请挂在 `ExitSourceCard`（`:159`）附近 |
| `components/Agent/AiTradeDirectionFields.tsx` | **新建** | 启动弹窗「逆势通路」开关 + 三个数值 |
| `components/Agent/AiTradeDirectionFields.test.tsx` | **新建** | 默认回填 / 越界夹紧 / 关闭态 |
| `components/Agent/AiTradeLaunchModal.tsx` | 改 | 挂载上面这块 fields；启动体带 `direction` |
| `lib/aiTrade.ts` | **改（v2 评审 P0-4 新增）** | `DEGRADE_REASON_LABELS` 补六条：四个 `low_consensus_*` 细分码 + `low_consensus_size` + 裸 `low_consensus`。**顺序敏感**（`find(startsWith)`）：细分码必须排在裸码之前。今天该表**没有**任何 `low_consensus` 条目，不补就会把英文码怼给用户 |
| `lib/aiTradeExit.ts` | **改（v2 评审 P0-3 新增）** | `describeVetoedProposal` 改吃后端下发的 `layerLabel`；保留 `vetoedBy === "horizon"` 那条映射**仅供老快照**，绝不回退到 `vetoedBy ?? "门禁"`（会显示英文层名） |
| `lib/store.ts`（**trace 相位** `emptyRound` + `reduceRoundPhase` 的 `case "tool_call"`） | 改 | ⚠️ **v2 评审 P1-5 订正落点**：真正逐键手抄的是这里（该文件已有四条同款警示注释），必须显式补 `layerLabel`。**成员级 `status` 不需要改 store.ts**——它是整体 spread（`store.ts:992` / `:1032`），`openFunnel.bySide` / `directionBalance` 只要补类型声明就能到达 |
| `components/Agent/AiTradeExitPriorityRows.tsx` | 改 | `ProposedVetoRow` 是「提议 X → 被 Y 拦下」的**既有**渲染点（`AiTradeTraceRound.tsx:37` 只是 import 它）；泛化后文案覆盖八层 |
| ~~`components/Agent/AiTradeTraceRound.tsx`~~ | **不改** | v1 把它列为落点是认错了组件（v2 评审 P0-3）。它只是 import `ProposedVetoRow`；在这里新加一条降级行会造出第二条平行通路 |
| `lib/useAiTradeGroupStream.ts` | 不改（**但须复核**） | `groupStateFromStatus:31` 是**分组级**逐键手抄点。本轴的新键都挂在**成员级** `openFunnel` 之下（`session: m` 整对象赋值，第 38 行），故不需要改；一旦有人把 `directionBalance` 提到分组级，这里就必须同步补 |

### 文档

| 文件 | 动作 |
|---|---|
| `docs/plans/ai-trade-directional-symmetry/spec.md` | 本文 |

---

## 六、技术设计：关键代码路径与时序

### 6.1 一个「模型想做空」的决策轮（修复后）

```
_run_decision_round
 ├─ _build_decision_payload            payload.consensus.contrarian = {available, remaining, ...}
 ├─ decider.decide()                   → TradeDecision(open_short, size=0.5, conf=0.72,
 │                                        contrarian_evidence="…60+ 字…")
 ├─ stage1_decision = decision         ★ B-2 留存点（任何门禁之前）
 ├─ _discipline.note_intent()          → intents_by_side["short"] += 1        ★ D-1
 ├─ _apply_base_rule_gate              MA 通道（方向对称，directional 默认关）
 ├─ _apply_horizon_gate                空仓开仓永不被拦
 ├─ _apply_profit_cooldown_gate        same_side 冷却
 ├─ turnover_gate / backstop_gate
 ├─ _sizing.gate                       回撤刹车
 ├─ _apply_consensus_gate              ★ 轴 A
 │    votes(short)=0 < 1  →  逆势通路四条件：
 │      opposing(long)=1 ≤ 1 ✓ / quota 0<3 ✓ / conf .72≥.65 ✓ / evidence 64≥60 ✓
 │    → lane="contrarian"，size 0.5×0.5=0.25（量化后 0.25），_contrarian_used=1
 ├─ _apply_edge_gate                   edge ≥ 1.5×cost（方向无关）
 ├─ _reflect_before_execute            复核官只能收紧，且对两个方向同标准（C-3）
 ├─ _apply_sizing                      波动率目标 / 相关性上限（做空会**降低**净敞口）
 ├─ _execute_decision                  → 真实做空委托
 └─ commit_round(gate_veto=None)       未被否决 ⇒ tool_result 逐字节不变
```

### 6.2 同一轮若被拦下（修复后 vs 今天）

| | 今天 | 修复后 |
|---|---|---|
| 系统侧计数 | `blocked["consensus"] += 1`（方向不可见） | `blocked_by_side["short"]["consensus"] += 1` |
| 模型侧记忆 | journal 写 `{"action":"hold"}`，**做空意愿彻底消失** | journal 带 `gateVeto:{vetoedBy:"consensus", proposedAction:"open_short", longVotes:1, shortVotes:0, contrarianRemaining:3}` |
| 模型下一轮读到 | 「上轮我决定观望」 | 「上轮我提议做空、被同向共识门槛拦下，还剩 3 次逆势名额」 |
| 用户看到 | 「拦得最多的是同向共识门槛」 | 「模型提出过 6 次做空，5 次被同向共识门槛拦下」 |

### 6.3 交付顺序（每一阶段独立可上线、可回滚）

1. **轴 D（观测）先行**。没有方向轴的观测，后面所有改动的效果都不可证伪。轴 D 不改任何交易语义，风险最低。
2. **轴 B（注记）**。同样不改交易语义，只补模型的记忆。
3. **轴 C（协议）**。改的是「怎么想」，安全语义一个字节不动。
4. **轴 A（门禁）**。唯一真正改变放行集合的一轴，必须在 1~3 都上线、观测面已经能看见方向分布之后再上。
   **上线判据（v2 评审补，呼应 §2.5）**：轴 D 的读数必须先回答「那 54% 被共识门禁拦下的意愿里有几次是做空」。若实测显示做空意愿本就接近 0，则轴 A 是对错误病因的修复，应当**暂缓**、优先观察轴 C / 轴 E 的效果。把这条写成明文，是为了避免「方案里写了就一定要实现」这种路径依赖。
5. **轴 E（先验去偏）**、**轴 F（资金费，默认关）**。

---

## 七、接口设计

### 7.1 REST — `POST /ai-trade/start`（新增可选字段，老客户端零影响）

```jsonc
{
  "direction": {                       // 整字段缺省 ⇒ 服务端按 settings 装配开启态
    "contrarianEnabled": true,         // false ⇒ 该会话不装配逆势通路（等价旧行为）
    "contrarianMinConfidence": 0.65,   // null ⇒ 服务端回填；(0,1]，越界 422
    "contrarianMaxOpposingVotes": 1,   // null ⇒ 回填；ge=0 le=8
    "contrarianMinEvidenceChars": 60,  // null ⇒ 回填；ge=0 le=1000
    "contrarianMaxPerSession": 3,      // null ⇒ 回填；ge=0 le=50
    "contrarianSizeMult": 0.5          // null ⇒ 回填；(0,1]
  }
}
```

回执镜像 `AiTradeStartOut.direction`（`null` = 未装配）。语义与 `baseRules` / `profitGuard` / `tradingHorizon` / `experience` 的成文惯例逐字一致：**字段缺省 ⇒ 开启；`{"contrarianEnabled": false}` ⇒ 不装配**。AI 回测启动体 `POST /ai-trade/backtest` 接**同一个字段、走同一个解析点**——否则用户在回测里调出来的参数在实盘失真（`base_rules` P1-4 的成文教训）。

### 7.2 WebSocket — `status` / `aiStats`

```jsonc
"openFunnel": {
  "intents": 47, "blocked": {...}, "submitted": 41, "filled": 41,
  "bySide": { "long": {...}, "short": {...}, "unknown": {...} },
  "directionBalance": { "longIntents": 41, "shortIntents": 6,
                        "shortIntentShare": 0.1277, "contrarianPassed": 1,
                        "verdict": "long_skewed" }
}
```

新增 trace 事件相位 `consensus_gate`（与既有 `edge_gate` 同款）：`{lane, votes, minConsensus, opposingVotes, contrarianRemaining, reason}`。

`tool_call` 相位**新增一个兄弟键** `layerLabel`（与既有 `proposedAction` / `proposedReason` / `vetoedBy` 并列，B-3）：中文层名由后端下发，前端不复制（`discipline._THRESHOLD_LABELS` 的成文纪律）。总闸关 ⇒ 整键缺席。

成员级 `status` 的 `discipline` 段**新增** `contrarian: {remaining, used, maxPerSession}`（v2 评审 P2-4）：逆势配额不落库、冷续跑归零是有意取舍（A-4 / RK-10），但「本会话已用 N/M」必须让用户看得见——否则「重启刷配额」是一个既存在又不可见的行为。

**缺席语义**：总闸关 / 老后端 ⇒ 整键缺席（**不是 `null`**）⇒ 前端渲染门控整段隐藏。

### 7.3 配置项（`config.py` + `.env.example`）

| env | 默认 | 说明 |
|---|---|---|
| `AI_TRADE_DIRECTIONAL_SYMMETRY_ENABLED` | `true` | 本特性总闸；`false` ⇒ 全仓逐字节回退 |
| `AI_TRADE_CONSENSUS_CONTRARIAN_ENABLED` | `true` | 逆势通路默认装配。**作用域仅 `steady`(2) 与 `balanced`(1)**——`aggressive` 与 `off` 的 `min_consensus` 都是 `0`，共识门禁本就不触发（v2 评审 P1-2） |
| `AI_TRADE_CONSENSUS_CONTRARIAN_MIN_CONFIDENCE` | `0.65` | ∈(0,1] |
| `AI_TRADE_CONSENSUS_CONTRARIAN_MAX_OPPOSING_VOTES` | `1` | ≥0 |
| `AI_TRADE_CONSENSUS_CONTRARIAN_MIN_EVIDENCE_CHARS` | `60` | ≥0 |
| `AI_TRADE_CONSENSUS_CONTRARIAN_MAX_PER_SESSION` | `3` | ≥0 |
| `AI_TRADE_CONSENSUS_CONTRARIAN_SIZE_MULT` | `0.5` | ∈(0,1] |
| `AI_TRADE_DIRECTION_BALANCE_MIN_SAMPLES` | `8` | `verdict` 判「样本不足」的下界 |
| `AI_TRADE_COST_FUNDING_CREDIT_ENABLED` | `false` | 轴 F，默认关 |

---

## 八、数据模型

### 8.1 内存形状

```python
ConsensusRule(min_consensus, contrarian_enabled, contrarian_min_confidence,
              contrarian_max_opposing_votes, contrarian_min_evidence_chars,
              contrarian_max_per_session, contrarian_size_mult)          # frozen
ConsensusGateOutcome(decision, reason, lane, contrarian_used, wire)      # frozen
OpenFunnelCounters(..., intents_by_side, blocked_by_side, submitted_by_side,
                   sizing_zeroed_by_side, filled_by_side,
                   contrarian_passed, _intent_side)                      # mutable，会话级
#   ★ v2 评审 P0-1：submitted_by_side / sizing_zeroed_by_side 在 v1 里被闭合式与 wire 引用过，
#     却从未在数据模型里声明；filled_by_side 的方向来自成交腿自己的 side，不是 _intent_side。
TradeDecision(..., contrarian_evidence: str | None = None)               # 末位带默认值
AiTradeSession._contrarian_used: int                                     # 会话级，不落库
```

### 8.2 持久化：**零 DB 迁移**

- `strategy_runs.metrics_json → aiStats.openFunnel` 随 `funnel_wire` 整体带上 `bySide` / `directionBalance`——`build_funnel_stats` 无需改动。
- `strategy_runs.config_json` 随启动体原文带上 `direction`（冷续跑 `resume.py` 重跑 `build_start_request` 自动同配置，无额外代码）。
- `trades.explain_json.meta` **不新增字段**：方向已经在 `trades.side` 里，再落一份必漂移。
- `ai_experience_events` 表结构不动（轴 E 改的是检索与渲染，不是落库）。

### 8.3 老数据兼容

`bySide` / `directionBalance` 缺席的历史会话（本特性合入前的全部 run）在前端**整卡不渲染**；历史聚合的两个加法读点对缺键按 0 处理但**不计入分母**（绝不把「没有这个字段」画成「做空 0 次」）。

---

## 九、测试与验收标准

### 9.1 纯核心单测（`services/quant/tests/`）

**`test_ai_trade_consensus.py`（扩充）**
- `test_contrarian_lane_is_byte_identical_for_long_and_short`：把同一组入参的 `action` 从 `open_long` 换成 `open_short`、票数镜像交换，两次 `resolve_consensus_gate` 的 `lane` / `reason` / `size_fraction` 必须完全一致。**这是轴 A 的对称性铁律的唯一守卫。**
- `test_contrarian_disabled_is_byte_identical_to_legacy`：`contrarian_enabled=False` 时 `(decision, reason)` 与旧 `apply_consensus_gate` 逐字相等，且直通轮返回**同一个对象**（`is` 相等）。
- 四条否决码各一例：`opposed` / `quota` / `conf` / `evidence`；顺序敏感（先判 opposed）。
- `test_contrarian_always_shrinks`：放行时 `size_fraction` 必然 ≤ 原值 × `size_mult`（量化后），且被档位地板吃到 0 时退回 `low_consensus_size`。
- `test_contrarian_never_rescues_a_passing_decision`：`votes >= min_consensus` 时逆势通路完全不参与。
- `test_all_new_codes_classify_to_consensus`：五个细分码经 `open_funnel.classify_block` 全部归到 `"consensus"`。

**`test_ai_trade_gate_veto.py`（新建）**
- 七层各一例：`vetoedBy` 与 `classify_block` 一致，且 `proposedAction` 保真带方向。
- `test_reflection_layer_returns_none`：`reflection_*` 码必须返回 `None`（避免与 `build_reflection_note` 重复）。
- `test_horizon_note_is_byte_identical_to_legacy_when_switch_off`（**v2 评审 P0-2c 改名并加限定**）：**总闸关**时 horizon 码的注记与今天 `horizon_runtime.build_gate_veto_note` 的产物逐字节相等。总闸开时注记多一个 `layerLabel`，这是有意的行为变更——原用例名不加限定必红。
- `test_no_note_when_no_deterministic_gate_fired`（**v2 评审 P0-2a 改判据**）：`gate_degrade is None` ⇒ `None`；`gate_degrade == llm_degrade` ⇒ `None`。
- `test_close_blocked_never_becomes_a_gate_veto`（**v2 新增，P0-2a 的直接守卫**）：`gate_degrade=None` 而执行侧回执带 `closeBlocked="exit_order_slot_busy"` ⇒ 注记恒 `None`。没有这条用例，那个码会经 `classify_block` 落进 `"other"`，模型读到一条凭空捏造的门禁事实。
- `test_llm_stage_degrades_never_produce_a_note`（**v2 新增，P0-2b**）：`low_confidence` / `no_tool_call` / `missing_size` 三例 ⇒ `None`（此刻 `stage1_decision.action` 已是 hold，写出去的 `proposedAction` 会撒谎）。
- `test_veto_layer_is_single_sourced`（**v2 新增，P1-7**）：`gate_veto.build_gate_veto_note` 与 `session._emit_tool_call_trace` 对同一个 `gate_degrade` 给出的 `vetoedBy` 必须逐字相等。

**`test_ai_trade_open_funnel.py`（扩充）**
- `test_by_side_closes_per_direction`：方向切分后每一侧的闭合不变式独立成立，**且三侧求和逐字等于既有四个标量**（切分是重新分组，不是二次计数）。
- `test_low_confidence_falls_into_unknown`：`low_confidence` 轮记进 `unknown`，**不猜方向**；并断言它**不是** `"hold"`（v2 评审 P2-1：v1 伪代码的 `removeprefix` 会造出第四个桶）。
- `test_filled_by_side_uses_the_fill_leg_side`（**v2 新增，P0-1 的直接守卫**）：先 `note_executed` （它会清 `_intent_pending`）再 `note_filled("short")`，断言 `filled_by_side["short"] == 1`。若实现去读 `_intent_side`，这条必红——而它正是 v1 写法的形状。
- `test_submitted_and_sizing_zeroed_split_by_side`（**v2 新增，P0-1**）：两个 v1 漏声明的字段各一例。
- `test_verdict_denominator_excludes_unknown`（**v2 新增，P1-6**）：`long=3, short=0, unknown=40` ⇒ `"insufficient"`（不是 `"long_skewed"`），且 `unknownIntents == 40` 必须出现在 wire 里。
- `test_direction_verdict_never_fabricates_a_percentage`：`intents=0` ⇒ `verdict="insufficient"` 且两个 share 为 `None`。
- `test_intent_side_cleared_on_exception`：决策轮抛异常后 `_intent_side` 不跨轮泄漏。

**`test_ai_trade_signals.py`（扩充）**
- `test_single_side_prior_no_longer_leans`：`p_long=0.55, p_short=None` ⇒ `net_stance == "flat"`，`netStanceBasis == "single_side"`。

**`test_ai_trade_reasoning_protocol.py`（扩充）**
- S10 mandate 必须同时含 long 与 short 候选的要求；S11 必须含 "not holding"；总闸关时三处 mandate 逐字节回退。

**`test_ai_trade_prompts.py`（更新冻结断言）**
- 总闸关 ⇒ 系统 prompt 与 payload 渲染**逐字节等于合入前**；总闸开 ⇒ 含方向对称条款且 `contrarian` 段在通路不可用时**整段缺席**。

**`test_ai_trade_cost_model.py`（扩充）**
- 轴 F 总闸关 ⇒ `funding_carry_bps` 逐字回退；开 ⇒ 负费率被夹到 `-cap`。

### 9.2 会话级集成测试

**`test_ai_trade_funnel_session.py` / `test_ai_trade_discipline_session.py`（扩充）**
- 构造一个「信号板全 long、模型输出 open_short + 高置信 + 长证据」的假 decider，断言：委托真的发出、方向为 short、仓位是原提议的一半、`contrarian_passed == 1`、`funnel.bySide.short.submitted == 1`。
- 同一场景把 `contrarianEnabled` 关掉，断言：降级 hold、`blocked_by_side["short"]["consensus"] == 1`、且 `commit_round` 收到的 `gate_veto.proposedAction == "open_short"`。
- **回归复现用例（必须先写、先红）**：`test_short_is_not_structurally_impossible_in_a_long_only_board` —— 信号板 0 票做空时，旧代码恒 `hold`，新代码在证据充分时放行。这是本次 bug 的稳定复现器（项目规范第七条：修 bug 必须先写复现用例）。

### 9.3 回测一致性

`test_ai_backtest_runner.py`：同一份 `ConsensusRule` 在 runner 与 session 上产生**相同的 lane 与降级码**（实盘 / 回测共用纯核心的既有断言范式）。

### 9.4 前端

- `DirectionBalanceCard.test.tsx`：三种 verdict 各一例；零样本不造百分比；无键不渲染；两种病因的文案**不可互换**。
- `AiTradeDirectionFields.test.tsx`：默认回填、越界夹紧、关闭态下数值输入禁用。
- `store.ts` **trace 相位**用例（v2 评审 P1-5 订正落点）：`tool_call` 事件带 `layerLabel` 时，归约后的 `AiTradeRoundTrace` 必须拿得到它（`emptyRound` 与 `reduceRoundPhase` 漏抄任一处即静默丢数据）。成员级 `status` 走整体 spread，**不需要**这类用例。
- `aiTrade.test.ts` 降级码用例（v2 评审 P0-4）：五个 `low_consensus_*` 细分码经 `describeDegradeReason` 必须得到中文，且**参数后缀原样保留**；同时断言细分码不会被裸 `low_consensus` 条目抢先命中（顺序守卫）。
- `aiTradeExit.test.ts`（v2 评审 P0-3）：`describeVetoedProposal` 收到 `layerLabel` 时用它；只有 `vetoedBy="consensus"` 而无 `layerLabel`（老快照）时**不得**显示英文 key。
- `DirectionBalanceCard.test.tsx` 追加：`unknownIntents > 0` 时披露句必须出现（P1-6）。

### 9.5 验收标准（Definition of Done）

1. `pytest services/quant/tests` 全绿（**全量跑，不用 `-k` 子集判回归**；须 `python -u`，不并发）；`pnpm --filter web test` 全绿；`tsc --noEmit` 无新增错误。
2. `AI_TRADE_DIRECTIONAL_SYMMETRY_ENABLED=false` 时，系统 prompt、payload、wire、degrade 码、前端渲染**逐字节等于合入前**（由 §9.1 的冻结断言守）。
3. 在一个信号板全多头的仿真会话里，模型给出高置信 + 充分证据的 `open_short` **能够真实成交**，且仓位为提议的一半。
4. 统计看板能明确回答「模型有没有提出过做空 / 被谁拦下」——两种病因文案在 UI 上可分辨。
5. 决策卡上，被任一门禁否决的轮次都能看到「提议了什么（含方向）→ 被哪一层拦下」。
6. 九个新配置项全部在 `.env.example` 中登记（`AI_TRADE_` 前缀已在桌面端 prod 透传白名单内，见 `desktop/src/lib/config.js:80`；但**未登记的变量在文档上不存在**，而漏登记是静默的）。
7. **（v2 评审新增）** 五个新降级码在界面上是**中文**，不是原始英文码——`low_consensus_evidence(0/1)` 这样的字符串不得出现在任何用户可见处。
8. **（v2 评审新增）** 八层门禁的任一层否决，`tool_call` 相位都带得出 `layerLabel`，且界面显示中文层名；老快照（无 `layerLabel`）不得退化成英文 key。
9. **（v2 评审新增）** `unknownIntents > 0` 的会话，方向卡片上必须能看到「有 N 轮方向不可知」这句话；`verdict` 不得把 `unknown` 计进任何分母或占比。
10. **（v2 评审新增）** 轴 A 上线前，轴 D 的读数已经回答了 §2.5 那个未证的问题（那 54% 被共识门禁拦下的意愿里有几次是做空）。答案是「几乎没有做空意愿」时，轴 A **暂缓**。

---

## 十、风险与缓解

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| RK-1 | 逆势通路降低了共识门禁的保护强度，可能让 Agent 在趋势里反复逆势试探而亏损 | 高（真实资金） | 四重约束叠加（置信度 + 反向票上限 + 证据字数 + 会话配额）；**强制降档 ×0.5**；配额默认仅 3 次；`steady` 预设默认关闭；启动弹窗一等露出可随时关 |
| RK-2 | 「修法本身成为新的偏斜来源」——只给做空开后门 | 高（信任崩塌） | §9.1 的 `test_contrarian_lane_is_byte_identical_for_long_and_short` 是唯一守卫，实现中除 `votes` 取数外**不允许出现任何方向特判**；评审节点须逐行核对 |
| RK-3 | `gate_veto` 泛化后与 `build_reflection_note` 产生重复注记，污染模型上下文 | 中 | `reflection` 层显式返回 `None` + 专用用例；两条注记的共存路径在设计上被消除 |
| RK-4 | prompt 字节变化打破既有冻结断言，被误判为回归 | 中 | 冻结断言分两组：总闸关组（必须逐字节回退，红了就是真回归）/ 总闸开组（本轮同步更新，红了是预期） |
| RK-5 | `contrarianEvidence` 成为承重字段后，模型不写就永远走不了逆势通路 | 中 | 通路不可用时 payload 的 `contrarian` 段会**明确告诉模型缺什么**（A-5）；且不写只是退回旧行为，不产生任何新的降级 |
| RK-6 | 方向切分让漏斗不闭合（`_intent_side` 跨轮泄漏 / `low_confidence` 拿不到方向） | 中 | `unknown` 单列一格、**绝不猜方向**；`end_round` 兜底清零 + 异常路径用例（R-12 同款） |
| RK-7 | 行情本就单边，修完之后方向比仍然接近 41:0，被误读为「没修好」 | 中 | §2.3 已把行情因素写进取证结论；验收标准 3/4 判的是**能力**（能不能做空、看不看得见），不是**比例** |
| RK-8 | 逐键手抄吞掉新 wire 键（仓库已有教训） | 中 | **v2 评审 P1-5 订正落点**：成员级 `status` 是整体 spread、不受影响；真正的手抄点是 `store.ts` 的 **trace 相位**（`emptyRound` + `reduceRoundPhase`）与分组级 `groupStateFromStatus`。只需在前者显式补 `layerLabel` + 专用用例 |
| RK-11 | 方向不可知的 `unknown` 桶（实测 21%）被读者当成 0，把「没提过做空」当成结论 | **高（会把运维引向完全错误的补救）** | `direction_verdict` 的分母排除 `unknown`；`unknownIntents` 强制下发；卡片上有独立落点与披露句；三条专用用例（§9.1 / §9.4） |
| RK-12 | 五个新降级码在界面上显示为原始英文（`DEGRADE_REASON_LABELS` 未登记） | 中（直接违反本方案「让方向被拦这件事对用户可读」的主张） | §五补 `lib/aiTrade.ts`、六条条目 + 顺序约束 + 顺序守卫用例 |
| RK-13 | `vetoedBy` 在后端有两份真源（`gate_veto.py` 与 `_emit_tool_call_trace`）而漂移不报错 | 中 | 两处共用同一判定函数 + `test_veto_layer_is_single_sourced` 对拍 |
| RK-9 | 轴 F 改成本口径，牵动 edge 门禁 / 成本记账 / 已实现 edge 校准三条链路 | 中 | 默认关闭，独立验证窗口，可单独回滚 |
| RK-10 | 冷续跑后逆势配额归零，可能被利用为「重启刷配额」 | 低 | 已知取舍并明示（A-4）；会话重启本身有成本与人工介入，评审节点复核是否需要落库 |

---

## 十一、非目标（明确不做）

1. **不引入任何方向配额 / 方向轮换 / 强制做空**。为了平衡而降低单笔质量，是用一个更坏的问题替换当前问题。
2. **不改动任何策略实现**。策略 15 与策略 28 都已支持双向（`factor_score_strategy.py:171`、`trend_band_swing.py:253`），信号板的方向分布是市场的函数，不是缺陷。
3. **不改动 MA 通道铁律的 `directional` 语义**（默认关，两个方向同区）——它今天是对称的。
4. **不为做空放宽任何风控**：仓位、止损、成本、edge 门禁、利润保护、持仓周期对两个方向仍然逐字同标准。
5. **不做 DB 迁移**。所有新读数走既有 JSON 列。
6. **不在本轮补做空侧的历史经验样本**。经验只能靠真实成交沉淀，凭空造样本会污染整个经验图谱。


---

## 实施过程发现的方案缺陷

> 本节由**实施节点**追加（spec v2 → 实施），逐条记录「方案写的与仓库/物理事实不符」的地方，
> 以及实际采用的订正做法。凡在此登记的偏离，正文中对应处已按订正后的做法实现。

### I-1（阻断级）§四 A-2 第 5 步：只判 `size <= 0` 会让「强制降档」在粗档位上静默失效

**方案原文**：
```
5. 四条全过 → lane="contrarian"，**强制降档**：
      size = quantize_size_fraction(size_fraction * contrarian_size_mult, step)
      size <= 0（被档位地板吃掉）→ 退回 lane="veto"，degrade="low_consensus_size(k/n)"
```

**物理事实**：`quantize_size_fraction` 是**吸附到最近档位**，且带 `max(1, …)` 的下界保护
（`signals.py`：任何 `>0` 的输入至少落到最小一格）。于是在 `step=0.5` 这类粗档位上，
`0.5 × 0.5 = 0.25` 会被吸附**回 0.5**——`size <= 0` 不成立，函数按原样放行。

**后果**：逆势通路会**按原仓位全额放行**，RK-1 承诺的四重约束里最硬的那一条（「强制降档 ×0.5」）
当场失效，而且**不报任何错**。这正是本仓库反复出事的形状：一个安全约束在某个参数组合下静默蒸发。
生产默认 `positionStepPct` 由用户在弹窗里选，`0.5` 是预设选项之一（`AI_TRADE_POSITION_STEP_OPTIONS`），
所以这不是理论组合。

**实际做法**：判据改为三条并列，任一成立即退回 `low_consensus_size`：
① `size <= 0`（被地板吃到 0）；② `size > proposed`（量化向上吸附把仓位放大）；
③ `shrink_requested and size >= proposed`（请求了降档却没有真的降下来）。
第三条是新增的那一条；`contrarian_size_mult == 1.0`（用户显式要求不降档）时它不触发，
故不会把一个合法配置判死。用例 `test_contrarian_always_shrinks` 的后半段守它。

### I-2（记录级）轴 F 本轮不实现，故 `AI_TRADE_COST_FUNDING_CREDIT_ENABLED` 未落地

《评审结论》放行条件 4 明确写着「轴 F（资金费真值化）本轮不实现」。因此：

- `cost_model.py` **未改动**，`_funding_carry_bps` 的「只取不利侧」语义与其 docstring 保持一致
  （P2-6 要求的「改代码必须同步改 docstring」在本轮不适用，因为两者都没动）；
- §7.3 表里的九个配置项**只落地了八个**：`AI_TRADE_COST_FUNDING_CREDIT_ENABLED` 未加进
  `config.py`、也未登记进 `.env.example`。理由是本仓库的成文纪律「无展示落点的常量即死码」——
  一个声明了却没有任何读点的运维开关，比不声明更坏：它会让运维以为自己能一键打开某个行为。
  §9.5 验收标准 6 的口径相应收敛为「**八**个新配置项全部登记」。

### I-3（记录级）E-2 的方向退化声明句必须计进 L0 的框架开销

轴 E-2 要求在经验注入的抬头写一句「经验库当前只有做多样本，不可据此推断做空更差」。它被实现为
**L0 页眉的一部分**（而不是另起一行拼在返回值上），理由是 `disclosure._render` 的预算是
「先扣框架、再装条目」——拼在返回值上会绕过预算，把 L0 撑出 `l0_max_chars`。

代价是 `budget_floor("l0")` 必须相应抬高（96 + 124 → 96 + 54 + 124），否则
`l0_max_chars` 落在旧下限附近的配置会**通过**启动期校验，却在经验库真的方向退化的那一天让整层
一个字都渲染不出来（与 `v1:E-7` 逐字同形的不报错空转）。故：

- `disclosure.budget_floor` 新增 keyword-only 的 `side_note`，默认 `False` ⇒ 三层下限逐字回退；
- **三个调用点全部传同一个判据**（`config._check_experience_budgets` /
  `api/app_settings` 的读写两处 / `experience/service._cast_setting`），这是 ★ E-7 立下的规矩：
  少传一处，那一处就会用一个偏小的下限放行一个「整层渲染不出一个字」的配置。

### I-4（记录级）`lib/aiTradeFunnel.ts` 承载方向轴纯函数，**不新建** lib 文件

§五前端表把 `directionRows` / `formatDirectionShare` / `directionVerdictText` 三个纯函数指给了
`lib/aiTradeFunnel.ts`。实施期一度想拆成独立的 `lib/aiTradeDirection.ts`，已按方案回退——
新建文件不在变更清单内，而 `aiTradeFunnel.ts` 加完仍只有 258 行，远在 800 行线内。
`topSideBlocker` 是实现「被谁拦下」那句结论时新增的第四个导出（同文件、同分工）。

### I-5（记录级）三条既有前端断言随 `low_consensus` 中文化一并更新（**预期变更**，非回归）

A-2 不变式 4 把六个 `low_consensus*` 码补进了 `DEGRADE_REASON_LABELS`，于是三条以
「未命中一律原样回退」为名、却拿 `low_consensus(1/2)` 当样本的既有断言必然变红：

- `lib/aiTradeEdgeGate.test.ts` / `lib/aiTradeSizing.test.ts`：样本换成一个仍然未登记的码，
  这两条守的契约（「未命中即原样回退」）本身一个字没改；
- `lib/aiTrade.test.ts::describeExecuted`：改判「中文化 + 票数后缀原样保留」。

按 RK-4 的两组冻结分法，这三条属于**总闸开组**：红了是预期变更，不是回归。

### I-6（记录级）`session._apply_consensus_gate` 的既有单测垫片需要补四个属性

`test_ai_trade_consensus.py::_gate` 用 `SimpleNamespace` 当假 session。薄封装改调
`resolve_consensus_gate` 之后，它还需要 `decider.position_step` / `_consensus_rule` /
`_contrarian_used` / `_round_trace` / `_discipline.funnel`。垫片已补齐，**被测行为未变**：
`contrarian_enabled=False` 时该文件既有的六条断言逐字通过。

### I-7（记录级）全量回归的基线：7 条**先于本轮存在**的红

`pytest services/quant/tests` 全量：**4273 passed / 7 failed / 1 skipped**（953s）。

7 条红全部来自 `services/quant/tests/test_factor_library_expansion.py`，而该文件在 git 里
**是未跟踪的**（`git ls-files --error-unmatch` 报 "did not match any file(s) known to git"），
它断言的 8 个因子（`mom_tsmom_vol` / `trend_efficiency_ratio` / …）在 `services/quant/app/`
下**一个都不存在**——那是 `docs/plans/factor-library-crypto-expansion/`（同样未跟踪）那一轮
遗留在工作树里的在制用例，与本轮改动零交集。

三道质量门禁的实测口径（§9.5 验收 1）：
- `pytest services/quant/tests` 全量（`python -u`、不并发）：除上述 7 条外全绿；
- `pnpm --filter web test`（vitest）：**2208 passed / 190 files**，全绿；
- `tsc --noEmit`：无错误；`ruff check`（`F` + `E9`）：All checks passed。

### I-8（记录级）§9.5 验收 2 的逐字节回退已实测，不只是断言

除 §9.1 的冻结用例外，另跑了一份一次性核对脚本，对**十二个面**逐一对拍
`AI_TRADE_DIRECTIONAL_SYMMETRY_ENABLED=false` 与合入前：审议协议 / 复核协议的 sha256、
中英两条脊柱的 sha256、方向宪章空串、`payload.consensus.contrarian` 整键缺席、
共识行无多余换行、先验「无证据」措辞、`build_decision_tool_schema(0.0) is DECISION_TOOL_SCHEMA`
恒等、共识门禁降级码与旧 `apply_consensus_gate` 逐字相同、`openFunnel` 两键整键缺席。
第十二项反向核对「总闸开时这五个面**确实**都变了」——否则「总闸」只是一句空话。全部通过。

### I-9（记录级）§五变更清单之外**必须一并改**的六个文件

每一条都是「清单里那一行如果不带上它就落不了地」，不是顺手扩大范围：

| 文件 | 为什么它必须改 |
|---|---|
| `app/ai_auto/prior_runtime.py` | 轴 E-1 的装配点。`_weighted_net_stance` 的 `debias` 与 wire 上的 `directionDebias` 都要有人从 settings 传进来——渲染层只读 wire、绝不读 settings（`commit_round` 会把 prompt 再渲染一次，两次渲染必须同源）。 |
| `app/api/app_settings.py`（读 + 写两处） | I-3 的连坐：`budget_floor` 抬高后，`PUT /settings/experience` 与它下发的 `l0MinChars` 必须用**同一个**判据（★ E-7 的成文纪律）。少改一处，用户就能从界面上配出一个「整层渲染不出一个字」的值。 |
| `app/ai_auto/experience/service.py` | 同上，第三个 `budget_floor` 调用点（DB 值的夹紧）。 |
| `apps/web/lib/aiBacktestTypes.ts` | §7.1 明写「AI 回测启动体接**同一个**字段、走**同一个**解析点」；回测请求类型不带 `direction`，回测那半边的同源承诺就是假的。 |
| `apps/web/components/Agent/AiTradeToggle.tsx` | 弹窗的 `direction` 要真的到达 `aiTradeApi.start` / `aiBacktestApi.start`；它是这两个请求体的唯一装配点。 |
| `apps/web/lib/aiTradeHistoryTypes.ts` | 历史详情页的方向卡数据源是落库的 `aiStats.openFunnel`，而该类型此前没有声明 `openFunnel`。 |

另有 `.claude-index/config.md`：按 CLAUDE.md §二与《评审结论》条件 5，`session.py`（3063 → 3172，
+109）与 `prompts.py`（979 → 1078，+99）两个越线文件的增量已在欠账表登记，并注明本轮**没有**
往 `session.py` 塞任何纯核心（新判定全在 `gate_veto.py` / `signals.py` / `open_funnel.py`）。

### I-10（记录级）轴 A 的配置走**派生 settings 副本**，不新增 `direction_rule` 请求字段

§五后端表里 `manager.py` 那一行写的是「`AiTradeGroupStartRequest`（`:165`）加 `direction_rule` 字段」。
实施采用的是**另一条同样成文的姿态**：`api/ai_auto._resolve_direction` 产出**又一层派生 settings
副本**（镜像 `_resolve_discipline` 与 `resolve_experience_settings`），六个阈值本来就住在 settings 上，
`signals.consensus_rule_from_settings` 是它们**唯一**的读点。因此：

- `AiTradeGroupStartRequest` / `AiBacktestStartRequest` **零改动**，`backtest_manager.py` 也**零改动**
  （runner 从 `spec.settings` 自取，与实盘同一个转换函数）；
- 多造一个随请求穿行的规则对象，等于多一份会漂移的真源——而 `ConsensusRule` 的三条来源约束
  （A-2）恰恰是为了消灭这种漂移。

代价是 §五那两行与代码不再逐字对应，故在此登记。

---

## 终审复核记录（Final Review · Subtask #3）

按《评审结论》的六条放行条件逐条对拍了 50 个跟踪文件的全量 diff 与 6 个新建文件。
条件 1（不得回到 v1 写法）、2（对称性铁律逐行核对：`resolve_consensus_gate` 函数体内除
`votes` / `opposing` 的取数那一行外无任何方向字面判断）、4（轴 F 未实现）、5（未往 `session.py`
塞纯核心 + 欠账已登记）、6（三道门禁全量跑）均已兑现。下列三条是终审自己逮到的缺陷，**已在本轮
直接改完并补上守卫用例**。

### R-1（P0，已修）`tool_call` 相位漏传 `llm_degrade`，会渲染一条**凭空捏造**的门禁否决

`session._trace_veto_layer` 在总闸开时只调 `veto_layer(gate_degrade)`，**没有**把注记侧那条
「第一阶段模型侧的码不产生否决」的纪律（B-2 / 评审 P0-2b）一并带上。后果：

- `low_confidence` 轮 `gate_degrade == "low_confidence"`，经 `classify_block` 落进 `confidence` 层
  ⇒ `vetoedBy="confidence"` + `layerLabel="置信度门槛"` + `proposedAction="hold"`
  ⇒ 决策卡渲染出「模型提议的是『观望』，被『置信度门槛』降级」——一句自相矛盾的假话；
- `no_tool_call` / `llm_error` / `decision_timeout` 等码不在任何前缀里，落进 `other`
  ⇒ 「被『其它』降级」，而本轮根本没有任何门禁参与；
- 同一轮注记侧返回 `None`、相位侧返回一个层名，**RK-13 要守的「两处同源」当场破**——而
  `test_veto_layer_is_single_sourced` 只对拍了八个确定性门禁码，没覆盖这一类，故用例是绿的。

实测该路径占开仓意愿的 **21%**（§2.4 的 `blocked.confidence`），不是边角料。

**修法**：把 `llm_degrade` 收进 `gate_veto.veto_layer(degrade, *, llm_degrade=None)`，
`build_gate_veto_note` 改为委托它——注记与相位从此共用**同一个函数、同一组入参**；
`_emit_tool_call_trace` 增 `llm_degrade` 形参，调用点一并传。总闸关的分支一个字节没动。
守卫：`test_trace_and_note_agree_on_llm_stage_degrades`（纯核心六个码正反对拍）与
`test_llm_stage_round_never_shows_a_fabricated_gate_veto`（会话级两处对拍）。

### R-2（P1，已修）`consensusGate` 轨迹按**逆势通路开关**门控，`steady` 档的注记丢掉票数

`_apply_consensus_gate` 原判据是 `self._consensus_rule.contrarian_enabled`，而注记里
`longVotes` / `shortVotes` / `minConsensus` 那一段的唯一数据源就是它。于是 `steady`
（逆势通路默认关、`min_consensus=2`）被拦的轮次，模型读到的注记里**没有「还差几票」**
——恰恰是 R-2 评审补充点名的那条前提（模型事前不知门槛、事后不知差几票），在最需要它的那一档失效。

**修法**：判据换成「总闸开 **且** 本层真的判过（`out.lane != "exempt"`）」。
`exempt` 意味着 `min_consensus<=0` 或非开仓动作，本层没话说，写进轨迹只是噪音。

### R-3（P2，已修）英文方向子句的折行宽度与脊柱其余各条不一致

`prompts_en._direction_discipline_en` 渲染出的第二行长约 140 列，而决策纪律其余各条都在 ~75 列。
脊柱是一整块等宽正文，混进一条长行会让模型读到的段落结构与其它条目不同。已按 ~75 列重排，
文本内容一字未改（`"Direction is symmetric"` 这句断言不受影响）。

### R-4（P1，已修）分组级 `direction` 回执落在**逐键手抄**的映射里，却没人抄

§五前端表给 `lib/useAiTradeGroupStream.ts` 判的是「不改（**但须复核**）」，理由是「本轴的新键都挂在
**成员级** `openFunnel` 之下」。这句话在实现里不成立：`manager._direction_wire` 把 `direction` 挂在了
**分组级**回执上（§7.1 要的就是这个），而 `groupStateFromStatus`（`:31`）正是本仓库「静默吞新键」缺陷
的那个函数——它逐键手抄，漏抄的表现是「WS status 那条通路好好的，只有**刷新页面后重发现**这条路径
拿不到生效值，而且不报任何错」。该文件里已经有四条同款警示注释。

**修法**：`groupStateFromStatus` 补 `direction: row.direction ?? null`，`AiTradeGroupState` 补类型；
守卫用例与 `discipline` 那条并排（`aiTradeDiscipline.test.ts`）。

### R-5（P1，已修）[P2-4] 的逆势配额有写点、没有读点

`session.discipline_wire()` 已按 [P2-4] 下发 `contrarian: {used, remaining, maxPerSession}`，
`manager._direction_wire` 也下发了生效阈值，但**前端一个字都没渲染**——全仓搜索 `contrarian`，
读点只有启动弹窗那块入参。于是 spec 自己写的那句「『本会话已用 N/M』必须让用户看得见——否则
『重启刷配额』是一个既存在又不可见的行为」在产品里没有兑现。这正是本仓库反复登记的那个形状：
**wire 里有事实、用户那里没有**。

**修法**：文案真源落在 `lib/aiTradeDiscipline.describeContrarianQuota`（纯函数，前端不重算任何阈值），
`DisciplineCard` 只做布局，渲染在 13 行纪律之下：
「逆势通路 · 已放开 / 本会话已用 1/3 · 置信度 ≥0.65 · 仓位 ×0.5。配额不落库，重启会话后重新计数。」
配额读**成员级** digest（现算的会话状态），阈值读**分组级**回执（服务端解析后的生效值）——
这也顺带给 R-4 那个键补上了真正的读点。未装配 ⇒ 整行不渲染，绝不挂一行「—」。

### 复核了但**判定为不改**的三条

1. **弹窗恒下发 `direction` ⇒ `steady` 档的 `contrarian_enabled=False` 只对不发该字段的客户端生效。**
   这是 §7.1「字段缺省 ⇒ 开启 / 显式 `false` ⇒ 关闭」语义的直接结果，且用户在弹窗里看得见那个
   勾选框、随时可取消——是**显式选择压过预设默认**，不是静默失效。预设默认仍然守着老桌面包与
   直接调 API 的客户端。若下一轮要让预设默认在 UI 上也生效，正确做法是让
   `/ai-trade/discipline-presets` 下发每档的默认开关态、由弹窗据此回填（**中文与数值一律后端下发**
   的既有纪律），而不是在前端硬编码一份预设表。
3. **I-3 抬高 L0 下限（220 → 274）带来的升级面已核实，判定为可接受。** 只有把
   `AI_TRADE_EXPERIENCE_L0_MAX_CHARS` **显式**设在 [220, 274) 的部署会在升级后启动期报错
   （`_check_experience_budgets` 的既有语义就是「拒绝启动、不静默降级」）。该变量默认 480、
   `.env.example` 未登记、本机 `services/quant/.env` 未设置；走设置面板落库的那条路径由
   `_cast_setting` 夹紧并打 warning，不会拒启。另加一条用例
   `test_side_note_is_declared_and_fits_the_reserved_frame_overhead` 把「预留量 ≥ 真实声明句长度」
   钉死——低估它才是真正危险的那一侧（整层静默渲染不出一个字）。

2. **总闸关时 `netStanceBasis` / `directionDebias` / `sideDegeneracy` 三个键仍在 wire 上（值为
   `both`/`false`/`null`）。** 三者都是「渲染层只读 wire、绝不读 settings」这条纪律的产物
   （`commit_round` 会把 prompt 再渲染一次，两次渲染必须同源），且值为假时**渲染结果逐字节回退**。
   把它们改成整键缺席会让渲染层不得不回头读 settings，是用一个更坏的问题换一个更整齐的形状。

---

## 评审结论（Review Verdict）

### **有条件通过**

这份方案的问题定义、根因分层与「不靠配额、靠拆单向阀 + 让偏斜可观测」的总路线是**对的**，
交付顺序（轴 D 观测先行 → 轴 B 注记 → 轴 C 协议 → 轴 A 门禁）也是负责任的排法——把唯一真正改变
放行集合的那一轴排在观测面之后，正是这个仓库反复吃过亏之后该有的姿态。总闸 + 逐字节回退、
实盘与回测共用纯核心、`unknown` 不猜方向、拒绝复用 `evidence_digest` 这个纯呈现字段，这些都写得准确。

v1 的缺陷集中在一件事上：**方案对「落点在哪」的判断多处与代码实际不符**——四条 P0 里有三条
（B-2 的判据、B-3 的组件、A-2 新码的消费者）都是「修法落在它以为的位置之外」，第四条（D-1 的
`note_filled`）是「记点选错了，指标恒为 0 且不报错」。同一形状还出现在 P1-4（宪章插入点）与
P1-5（store.ts 手抄点）。这些已在 v2 中逐条改完，**无 P0 / P1 遗留**。

另外，评审复查生产库时发现 v1 §2.4「观测面缺失」的结论是错的：层轴拦截计数早就落库，
实测**回归窗口 54% 的开仓意愿死在共识门禁**。这条证据把 R-1 从推断抬成了实测，也让 §2.5
把「已证 / 未证」分了个明白——**未证的那一条（被拦的是哪个方向）恰恰是轴 D 的存在理由**，
方案因此不该把轴 A 的效果当成已经验证的结论去交付。

### 放行条件（实现期必须逐条兑现）

1. **P0 / P1 全部按 v2 正文实现，不得回到 v1 写法。** 尤其是四处：`gate_degrade` 判据（B-2）、
   泛化既有 `vetoedBy` 通路而非新建（B-3）、`note_filled(side)`（D-1）、
   `DEGRADE_REASON_LABELS` 六条 + 顺序（A-2 不变式 4）。
2. **RK-2 的对称性铁律由评审逐行核对。** 实现 `resolve_consensus_gate` 时，除 `votes` 取数那一行外，
   函数体内**不允许出现任何 `action == "open_short"` 的字面判断**。
   `test_contrarian_lane_is_byte_identical_for_long_and_short` 是这条铁律的唯一守卫，它必须先写。
3. **轴 A 的上线要过 §2.5 那道判据。** 轴 D 落地后先观察一个完整窗口：若「模型提过做空、被门禁拦下」
   确实占多数，轴 A 放行；若实测显示做空意愿本就接近 0，轴 A **暂缓**，把预算换到轴 C / 轴 E。
   方案里写了不等于必须实现——这条是本次评审最重要的一个条件。
4. **轴 F（资金费真值化）本轮不实现。** 它与「方向对称」只有弱耦合，却牵动 edge 门禁 / 成本记账 /
   已实现 edge 校准三条链路（RK-9 自己也这么写）。请在轴 A~E 的验收窗口结束后另开 spec；
   若坚持本轮做，`_funding_carry_bps` 的 docstring 必须同步改（P2-6）。
5. **不得再往 `session.py` 塞纯核心**（它已 3063 行）。新逻辑进 `gate_veto.py` / `signals.py` /
   `open_funnel.py`；越过 800 行线的文件的增量须在 `.claude-index/config.md` 的欠账表登记（P2-3）。
6. **验收按 §9.5 的十条跑满**，`pytest services/quant/tests` 走全量（不用 `-k` 子集判回归，须 `python -u`、不并发）。
   总闸关组的冻结断言若红，那是真回归；总闸开组的冻结断言若红，那是本轮的预期变更——两组不得混判。

### 不构成放行条件的观察（供实现者参考）

- §2.4 的实测顺带暴露了另一件事：`confidence` 门槛（`balanced` 下 0.5）在回归窗口拦掉了 21% 的开仓意愿，
  数量级仅次于共识门禁。它是方向中性的，**不在本方案范围内**，但它会持续制造 `unknown` 桶；
  若下一轮想把方向轴做实，P2-5 那条（`AiDecider.last_intent_action`）是成本最低的入口。
- `_format_consensus` 从不渲染 `min_consensus`（见 R-2 的评审补充）。A-5 把它补上之后，
  值得单独看一眼「模型知道门槛之后的行为变化」——那是本方案里最便宜、也可能最有效的一处改动。
