# AI 交易经验的复利化（ai-trade-experience-compounding）· 方案设计 **v2**

> 本轮 = 迭代 2/2 的节点 #0（方案设计）。迭代 1 已把「经验上下文图」从零建成并合入
> **`8e05dd9`**（74 文件 / +14011 −19），设计依据是
> `docs/plans/ai-trade-experience-context-graph/spec.md` v4。
>
> **本文档不重做那一轮。** 本文档只做一件事：把那套已经跑起来的机制，从「能存能读」推到
> **「越用越准、越用越省、越用越可信」**——这三件事恰好对应需求里被交付得最浅的三条
> （「经验是累积的」「高效快捷精准」「数字资产」）。
>
> **编号命名空间是本文档局部的**：不变式 `J-*`、缺陷 `F-*`、接线 `W-*`、门禁 `G-*`、
> 验收 `A-*`、风险 `R-*`、配置 `C-*`。凡引用上一轮的条目一律带 `v1:` 前缀（如 `v1:I-1`）。
>
> **本文档的每一条缺陷都附可执行取证**（`services/quant/.venv` 内实跑的探针输出，逐字贴在
> §2.2）。不采信上一轮任何文档结论；凡与源码不符**以源码为准**。

---

## 评审记录（节点 #1 · v1 → v2）

> **评审方式**：不采信本文档 v1 的任何取证结论，也不采信迭代 1 的任何文档。对 `8e05dd9` 的经验层
> 逐文件实读（`taxonomy` 191 / `aggregate` 390 / `disclosure` 222 / `retrieval` 309 / `graph` 408 /
> `promote` 152 / `runtime` 427 / `service` 740 / `resume` 479 / `db/ai_experience_repositories` 488
> / 前端 7 个文件），并在 `services/quant/.venv` 内跑一支八段可执行探针复核每一个数字
> （脚本全文见**附录 B**，节点 #2 / #3 可原样重跑）。凡与源码不符**以源码为准**。
>
> **v1 自己报告的九条缺陷 F-1…F-9 逐条复现属实**，本节不重复它们，只记 v1 **自己引入的**缺陷、
> **算错的数字**、以及**没看见的接缝**。定级口径：**P0** = 让本轮某条交付静默失效、或损坏既有
> 真实资金路径；**P1** = 让产品说假话 / 让一条承诺静默不兑现 / 让一条门禁必然误红；
> **P2** = 卫生与一致性。
>
> 结果：**5 条 P0、10 条 P1、7 条 P2**。P0 / P1 十五条已在正文逐条落修（修法编号 `E-1…E-16`，
> 与 `v1:E-*` 分属不同文档命名空间）；P2 七条中五条落修、两条留档。

| # | 级别 | 一句话 | 落修 |
|---|---|---|---|
| **R-1** | **P0** | 增量物化会把**整库**经验判为「沉降」并静音 ⇒ 第一个 tick 之后 L0/L1 当场变空 | E-1 · §4.5.3 |
| **R-2** | **P0** | `origin="adopted"` 的事件**写得进、读不出**：W-A 的产物 100% 不参与物化，却会在 UI 上报「已补采 N 笔」 | E-2 · §4.4 / §5.2 / §8.2 |
| **R-3** | **P0** | W-B 不解规模墙：`materialize_bounded` 的 overflow 闸在增量规划**之前**短路 | E-3 · §4.5.2b |
| **R-4** | **P0** | J-2「增量 ≡ 全量」在 `sign_streak` / `tier` 上**不可达**，且偏差方向是**更容易升成铁律**（破 J-1） | E-4 · §三 J-2/J-2b / §4.5.4b |
| **R-5** | **P0** | W-A 的 hook 落错位置：离线平仓在接管**之前**跑，合成断面此刻还不存在 ⇒ 「采集」永不发生 | E-5 · §4.4 |
| **R-6** | P1 | `FRAME_OVERHEAD["l2"]=83` / `budget_floor("l2")=203` 两个数都算错（实测 88 / 208），错的方向正是 `v1:E-7` 那条 P1 的复发 | E-6 · §4.2 D-3 |
| **R-7** | P1 | J-3「`describe` 对签名单射」与 D-2② 的实现自相矛盾，G-12 必然误红 | E-7 · §三 J-3 / §9.1 |
| **R-8** | P1 | `label_of` 按取值查表、不认轴：`horizon="none"` 渲染成「无事件」——加了轴名之后仍是一句不实的话 | E-8 · §4.2 D-2② |
| **R-9** | P1 | `_combos_for` 对**缺席**轴会 `KeyError`，而它会被 `materialize_bounded` 的裸 `except` 吞成一行 warning | E-9 · §4.2 D-2① |
| **R-10** | P1 | 增量的 NPMI 缺一个全局分母；epoch 状态里没有事件总数 | E-10 · §4.5.2 |
| **R-11** | P1 | 水位线可被「先取 id、后提交」的并发写**跳过**，且跳过之后无声；`EventQuery` 根本没有 `id > watermark`，而该数据层文件不在 §8.2 的改动清单里 | E-11 · §4.5.2 / §8.2 |
| **R-12** | P1 | `first_ts` 的前滚使增量与全量在批次乱序时不等值 | E-12 · §4.5.4 |
| **R-13** | P1 | `utility.refresh` 拿不到「同一把物化锁」——锁在 `rebuild()` 内部持有并在返回时释放 | E-13 · §4.6.3 |
| **R-14** | P1 | `match` 归一后 `_suppress` 的相对争议阈值前提被抽掉：满匹配恒等于 1.0 ⇒ 大批经验对被判「势均力敌」双双踢出 L0/L1 | E-14 · §4.3 E-2 |
| **R-15** | P1 | E-1 悄悄换掉 `applies_in` 边 `support` 的语义（含排除者 → 只数合格者），该列随图谱 wire 下发 | E-15 · §4.3 E-1 |
| R-16 | P2 | 桶最多只有 **2** 条非 symbol/bar 轴（实测形状 `{0:2, 1:38, 2:6}`），「四轴桶 = 三条其他轴」描述了一个不存在的桶；字符预算实算是 **110 < 120**（余量 10，不是 32） | E-16 · §4.2 |
| R-17 | P2 | §6.2 说「36 → 48 项」，正文只列 11 项，§8.2 又写「+12 项」——三处不一致（实为 **36 → 47**） | 已落修 §6.2 |
| R-18 | P2 | `render_label` 自身仍会 `text[:119] + "…"` 半句截断，而 label 是**进 prompt 的正文**；J-6 只约束了 `disclosure.format_*` | 已落修 §三 J-6 |
| R-19 | P2 | §7.2 要在启动弹窗加一行「已自动静音 N 条」，那是一次**新的读**，却未在 §5.3 登记超时档、也未计进净增预算（`AiTradeExperienceFields.tsx` 不在 §7.4 的表里） | 已落修 §5.3 / §7.4 |
| R-20 | P2 | §9.1 只说「后端红条数与改动前基线一致」，没有把基线数字写死，节点 #2 / #3 没有可核对的锚 | 留档：基线见 §9.1 注 |
| R-21 | P2 | 附录 A 的三支探针「位于会话临时目录」，节点 #1 / #3 无法原样重跑 | 留档：本轮探针全文见附录 B |
| R-22 | P2 | 三处交叉引用指错编号：`FRAME_OVERHEAD` 注释指 A-10（实为检索决定性）、§4.3 与 §十 R-4 两处指 A-13（实为 `fee_known` 排除） | 已落修（改指 A-6 / A-38） |

### R-1（P0）· 增量物化会把整库经验静音

`mark_stale_lessons(session, seen_keys)` 遍历**全部** `stat` 经验行，凡 key 不在 `seen_keys` 内的
一律置 `muted=True, mute_reason="stale"`：

```python
# app/db/ai_experience_repositories.py:315-336（实读）
rows = (await session.execute(select(AiExperienceNode).where(
    AiExperienceNode.kind == _LESSON_KIND, AiExperienceNode.source == "stat"))).scalars().all()
for row in rows:
    if row.key in seen_keys or row.muted or row.mute_reason == "user":
        continue
    row.muted = True
```

而 v1 的 §4.5.3 第 7 步逐字写着「`unmute_reappeared` / `mark_stale_lessons` # 既有函数，一字不改」，
增量步的 `seen_keys` 只有**本批 touched 的那几个桶**。`retrieval._collect` 过滤
`not graph.nodes[key].muted`（`retrieval.py:211`）⇒ **第一个增量 tick 之后，除本批碰过的十几条以外
的全部经验都进不了检索**。60 秒之内整层静默变空，`tsc` / `ruff` / 全量用例全绿，日志一行都没有。
这与 `v1:P0-1`（唯一写路径恒抛被吞成 warning）是**同一种失败形态**。

### R-2（P0）· `origin="adopted"` 写得进、读不出

```python
# app/db/ai_experience_repositories.py:104,112-113（实读）
origin: str | None = "live"       # 恒 "live"：口径闸而非死码（评审 P2-3）
if query.origin:
    stmt = stmt.where(AiExperienceEvent.origin == query.origin)
```

`rebuild()` 的取数正是 `load_experience_events(session, repo.ExperienceEventQuery())`
（`service.py:288`）⇒ `origin == "live"`。`load_evidence` 另有一处硬编码
`where(AiExperienceEvent.origin == "live")`（`:150`）。于是 W-A 采到的往返：

- **一条也进不了桶**（幸存者偏差原封不动，本轮 §零 表格里那一整格的收益归零）；
- **一条也进不了详情抽屉的证据表**；
- 而 `count_events`（`:91-93`）与 `overview`（`:461`）**都不过滤 origin** ⇒ L0 页眉那句
  「来自 X 组有成交记录的自动交易的 **Y 笔**已结算往返」会把这些**从未贡献过任何一条经验**的往返
  算进去。这是 `v1:I-9`（prompt 里的每个数字必须为真）在一个新地方被破——而且 `harvest.adopted`
  还会同时在 UI 上显示「已补采 N 笔」。**有写点、没读点，且读数在别处虚增。**

### R-3（P0）· 规模墙没有被推开

```python
# app/ai_auto/experience/service.py:350-359（实读）
events = await repo.count_experience_events(session)
cap = int(derived.ai_trade_experience_materialize_max_events)
if events > cap:
    await _write_health(session, {..., "overflow": True})
    return {"ran": False, "reason": "overflow", "events": events}
return await _run_materialize(session, derived)
```

这道闸**在 `_run_materialize` 之前**，而 v1 把 `incremental.plan` 挂在 `_run_materialize` 里面。
库越过 20 万条之后，增量**根本没有机会跑**，`materialize_health.overflow` 照旧置真、UI 照旧打
「规模墙」。本轮的头号产品承诺——§零 那句「本轮把它做掉」——在实现层面**一行都没有兑现**。
v1 §8.2 还给 `service.py` 上了「+45 硬上限、只加薄转发」，等于把这处必须改的判断也挡在门外。

### R-4（P0）· J-2 不可达，且偏差方向是「更容易升成铁律」

`roll_sign` 按**每次物化 +1** 计数，`resolve_tier` 的 iron 第三条门槛是 `sign_streak >= 2`：

```
探针 P6（实跑输出）
  full      -> prev_sign=1 sign_streak=1
  7-batch   -> prev_sign=1 sign_streak=7
  tier(full)      = normal
  tier(7-batch)   = iron
```

两个后果，第二个远比第一个严重：

1. G-10（v1 自称「本轮最重要的一条，不绿则 W-B 一行都不许接」）在 `tier` / `prevSign` /
   `signStreak` 上**必然红**，而且不是实现 bug——是 J-2 这条不变式本身写错了。节点 #2 面对一条
   「必然红且改不动」的门禁，只有两条路：把门禁悄悄放宽，或者停在这里。两条都坏。
2. 更严重：把「连续两次**物化**同号」这道门槛换成「连续两个**批次**同号」，等于让一条经验
   **更容易**升成 `iron` 进 L0 铁律。**这正是 J-1 明令禁止的方向**（「绝不允许新增任何自动升档
   路径」）。v1 全文没有察觉自己的压舱石被自己的另一条工作流顶穿。

### R-5（P0）· W-A 的 hook 落错位置

```python
# app/ai_auto/resume.py:291,294（实读，行号逐字）
291:            await settle_closed_offline(member, contract, creds=assessment.creds, ...)
294:        adopt_position(executor, assessed.get("actual"))
```

**离线平仓跑在接管之前**，而 v1 §4.4 的 hook 表把 `adopt_open` 挂在 `adopt_position` 之后、
把 `note_offline_close` 挂在 `append_live_trade` 之后，§4.1 的时序图也把 `[续跑接管]` 排在
`[离线平仓]` 之前——**与源码相反**。照 v1 落地，`note_offline_close` 永远取不到合成断面，永远走
`harvest_gap += 1` 分支：F-6 只兑现了「计上」，没兑现「采到」，而 §零 的表格、§4.4 的时序、
A-14 都写着这条路径会被采到。

第二处：被接管的那类成员 `assessed["actual"]` 为 `None`（仓已在交易所侧平掉），所以即便顺序对了，
`adopt_position` 那一侧也拿不到入场价与数量——**合成断面的数据源本来就不是它**，而是
`contract["position"]`（`_offline_exit_trade` 已经在读它的 `openCTime` / `size`）。

第三处：`settle_closed_offline` 在回读不到行时**提前 `return`**（`:333-335`），v1 把 hook 挂在
`append_live_trade` 之后 ⇒ **最该被计的那个缺口反而不计**。

### R-6…R-15（P1 详述）

- **R-6**：探针 P2 实测 `len(head)=21`、`len(tail)=**39**`、`detail` 最坏 **26**
  （`support` 7 位 + `t` 带符号 6 位 1 位小数）、两个换行 2 ⇒ 框架开销 **88**，v1 写 83。
  且 `budget_floor()` 的既有实现是 `FRAME_OVERHEAD[layer] + MIN_ITEM_BUDGET`，而
  `MIN_ITEM_BUDGET = 124` 里含「序号 + 换行」4 字，**L2 根本没有序号**。两条都必须写准，否则
  `reflection_max_chars ∈ [203, 207]` 会**通过**启动期校验却让 L2 永远渲染不出一个字——与
  `v1:E-7` 逐字同形的复发。
- **R-7**：探针 P4 实测 `describe_v2({BTC,15m,long})` 与 `describe_v2({ETH,1H,long})` 都等于
  `方向·做多`。D-2② 的实现明确排除 `symbol` / `bar`，所以它**对签名不可能单射**；真正单射的是
  整条 label（`_prefix` 会补 `{symbol} {bar} · `，`aggregate.py:287-293`）。G-12 照 v1 的措辞写
  必然红。
- **R-8**：`label_of(axis, value)` 收了 `axis` 却**不用**（`taxonomy.py:172-174`），`VALUE_LABEL_ZH`
  是按取值查的单表。实测 `label_of(AXIS_HORIZON,"none") == "无事件"`。加了轴名之后变成
  「持仓周期·无事件」——从「两条重复」变成「一条读不懂」，仍然是 `v1:I-9` 意义上的不实。
- **R-9**：实测 `_combos_for({symbol,bar,side,horizon})` ⇒ `KeyError: 'regime'`。W-A 的
  `degraded_context` 正是「其余轴一个都不写」。v1 的伪码用 `...` 省掉了四处 `signature[axis]`，
  只在散文里说「缺轴的组合直接跳过」。节点 #2 若照伪码只改 `known` 的构造，第一条接管事件就会让
  **每一次物化**抛 `KeyError` → `service.py:361-363` 的裸 `except Exception` 吞成一行 warning
  → 图永久停更且全绿。
- **R-10**：`_co_occurs_edges` 的 NPMI 分母是 `total = len(events)`（`graph.py:343,358`），而
  §6.1 的 `ai_experience_context_counts` 只有 `(a_key,b_key,count)`、§4.5.2 的 epoch JSON 也没有
  事件总数 ⇒ 增量算不出与全量相同的 NPMI ⇒ G-10 的边四元组断言必红。
- **R-11**：`EventQuery` 的过滤字段只有 `inst_id/bar/since_ts/until_ts/origin/limit/offset`
  （`:96-107`），**没有 `id > watermark`**；而 §8.2 的改动清单里**没有**
  `app/db/ai_experience_repositories.py` 这个必须改的文件（`v1:G-8` 存在的理由就是上一轮漏掉一个
  再导出让整条特性静默空转）。更实质的是并发：多成员会话各自 `persist_pending` 提交，id 在
  INSERT 时分配、在 COMMIT 时可见，两条连接交错时完全可能「id 100–105 比 106–110 后提交」。
  水位线推到 110 之后，100–105 **永久不被折进累加器**，且没有任何判据会发现。
- **R-12**：`_lesson_node` 的 `first_ts=(old.first_ts if old and old.first_ts else stats.firstTs)`
  （`graph.py:271`）。G-10 的协议是「清空后按 7 个**随机**批次逐批增量」，于是增量的 `first_ts`
  是第一批的最小值、全量是全批的最小值。
- **R-13**：锁在 `rebuild()` **内部**由 `async with _materialize_lock(...)` 持有并在返回时释放
  （`service.py:284-305`）。§4.6.3 要求 `utility.refresh` 跑在「同一把锁内」，§8.2 又要求
  `rebuild()` 一行不删——两条同时成立时它只能跑在**锁外**，而它要写 `muted` / `mute_reason`，
  与 `mark_stale_lessons` / `unmute_reappeared` 写同一批列，并发下会丢更新。
- **R-14**：`_DISPUTE_REL` 的注释逐字写着「用**相对**判据的理由：`match = Σ A(c)·w` 无量纲上界」
  （`retrieval.py:24-26`）。E-2 之后 `match ∈ [0,1]` 且**满匹配恒等于 1.0**，于是大量互相
  `contradicts` 的经验对同时取 1.0 ⇒ `rel = 0 ≤ 0.25` ⇒ **两条都被踢出 L0/L1** 打包进「争议」。
  方向仍单侧安全（说得更少），但这是一次会显著改变「模型看到哪几条」的行为变化，v1 只分析了
  排序、没有分析压制，那段注释也会就地变成一句假话。
- **R-15**：既有 `applies_in` 边的 `support` 是「桶内该语境的事件数（含被 I-7 排除的）」，E-1 的
  新实现写 `support=node.support`（= `stats.n`，只数合格者）。这一列随 `GET /api/experience/graph`
  下发给图谱画布，语义换了而没人说。

---

## 零、本轮结论速览（TL;DR）

上一轮交付的东西是**对的**：事件表是唯一真源、图由它派生、检索是有界种子激活、披露分三层带硬
字符预算、设置中心有完整的可视化管理、启动弹窗默认开启。这些不需要重做。

但把它当成一个**每天真金白银在用**的资产系统来审视，它今天有三个层次的问题：

| 层 | 症状 | 后果 | 本轮对应 |
|---|---|---|---|
| **说真话** | 同一份 prompt 里能出现**两行逐字相同**的经验；一条 **+88bps 的盈利经验**被挂在「与本次提议最相冲突的一条」标题下；反例段在预算收紧时**先丢掉免责句**、并且**自己超出自己的预算**；争议段说「两条互相矛盾」却只给一条 | 喂给要下真单的模型的文本里有假话。`v1:I-9`（prompt 里的每个数字必须为真）在这四处破了 | **W-D**（F-1…F-4） |
| **算得准** | `applies_in` 的边权**恒为 1.0**，而设计文档与源码 docstring 都声称它是「该语境在这条经验里的份额」。于是 `match = Σ A(c)·w` 退化成「命中轴数」——**签名越宽分越高，且一条经验有一半条件不满足也照样满分** | 检索排序系统性偏向过度specific 却半匹配的经验。需求原文「高效快捷精准」的「精准」在打分函数里没有落点 | **W-E**（F-5） |
| **攒得住 / 说得清** | 物化是**全量重建**，`materialize_max_events` 越界即**停止自动物化**；接管持仓与离线平仓的往返**采不到**，其中离线平仓那一条**连缺口都不计**（UI 上那句「另有 N 笔未采集」在这条路径上恒为 0）；没有任何机制回答「这条经验到底有没有用」 | 「经验是累积的」在规模上限处直接停摆；样本池带**幸存者偏差**且偏差量**不可见**；资产无法证明自己在增值，`v1:R-1`（自我强化的幻觉回路）没有关闭手段 | **W-B / W-A / W-C**（F-6…F-9） |

本轮交付 **6 条工作流**（依赖顺序即实施顺序）：

- **W-D 说真话** —— 四条 prompt 文案 / 预算缺陷的修法（最小、最急、零架构风险）。
- **W-E 算得准** —— `match` 改为**覆盖率归一**，并删掉那段算出常量 1.0 的 O(N×11) 扫描。
- **W-A 采全** —— 接管 / 离线平仓两条路径合成**降级开仓断面**，往返进事件表（`origin="adopted"`）；
  两条路径的采集缺口都真的被计数。
- **W-B 算得动** —— 真正的增量物化：桶级充分统计量 + epoch 冻结的成本分位 + 事件水位线，
  全量重建保留为**正确性预言机与兜底**，并有一条逐值等价门禁把两者钉死。
- **W-C 自证** —— 注入台账 → 每条经验的「注入之后表现如何」→ **失效即自动静音**。
- **W-F 管理面** —— 效用列 / 效用面板 / 采集完整性卡 / 批量清理沉降经验。

**一条压倒一切的安全性质（J-1）**：本轮新增的每一个闭环，其作用方向都是**让经验层说得更少**
（自动静音、覆盖率归一后的降权、降级签名只落进更粗的桶）。**没有任何一条新增逻辑会让经验层
说得更多、更肯定、或获得任何门禁 / 风控 / 仓位 / 下单判据的影响力。** 这是 `v1:I-1` 单侧安全
论证在本轮的延续，也是这一整轮敢默认开启的理由。

---

## 一、概览（Overview）

【AI 自动交易】现在会把每一笔已结算往返沉淀成一条事件，把事件物化成一张语境-经验图，并在每一轮
决策前用种子激活扩散取出最相关的几条喂给模型。这套机制在 `8e05dd9` 已经在跑。问题在于：一个
**资产**系统和一个**日志**系统的区别，不在于它有没有把数据存下来，而在于三件事——存下来的东西
**全不全**、取出来的东西**准不准**、以及这个资产能不能**证明自己在增值**。今天这三条都还没有
被正面回答：样本池漏掉了一整类（而且恰好是偏亏损的那一类）往返；打分函数里那个本该表达「这条
经验的条件被满足了多少」的因子实际上恒等于 1；而「这条经验被喂进去之后，那一单赚了还是亏了」
这个问题，系统从来没有记录过答案。

本轮要做的，就是把这三个洞补上，并且顺手修掉四处**产品在 prompt 里说假话**的地方。四处里最尖锐
的一处是：当模型提议平仓时，系统会挑出「净额绝对值最大的一条」经验，放在
**「与本次提议最相冲突的一条」**这个标题下面交给风控复核官——而实测表明，被挑中的完全可能是一条
**+88bps 的盈利经验**，也就是说，那条经验其实在**支持**这次平仓。把一条支持的证据说成「最相
冲突」，不是排序不佳，是**陈述不实**；它发生在一个只能 confirm / abort / 缩仓的复核官面前，
后果是「因为一条支持的证据而否掉一次正确的平仓」。同一类问题还有三处（详见 §2.2），共同点是：
**它们都不会报错，`tsc` / `ruff` / 全量用例全绿，只有把渲染结果逐字打出来才看得见。**

规模这一侧同样需要正面回答。今天每一次物化都是**从事件表全量重建**：读全表 → 铺 46 个桶 →
逐桶算统计 → 重算全部边。`materialize_min_interval_s` 的去抖把它压到最快 60 秒一次，
`materialize_max_events`（20 万）越界之后**直接停止自动物化**并在 UI 上打一行「规模墙」。也就是
说，需求里「经验是累积的，随着交易的进行能够进行不断的更新」这句话，在库变大之后是会**停下来**
的——而且是以一种「已经说清楚了」的姿态停下来。上一轮把这条明确记为欠账（`v1:§十一.8`），本轮
把它做掉：把统计量做成**可合并的充分统计量**，把成本三分位**冻结成 epoch**，用一条**事件水位线**
只吃增量，同时把全量重建原样保留成兜底和**正确性预言机**——两条路径的产物必须逐值相等，这条由
一条机械门禁（G-10）钉死，不绿则增量一行都不许接。

---

## 二、现状取证与本轮发现（全部实读 + 可执行探针）

### 2.1 上一轮已交付、本轮**零改动**的部分

| 模块 | 行数 | 本轮处置 |
|---|---|---|
| `experience/taxonomy.py` | 191 | 只改 `describe()` 一处（F-2），闭合词表与折叠规则**一字不动** |
| `experience/events.py` | 198 | 零改动（`OpenContext` 增一个字段除外，W-C） |
| `experience/promote.py` | 152 | 零改动（`mute_reason` 新增一个取值由数据层承载） |
| `experience/distill.py` | 204 | 零改动 |
| `experience/importing.py` | 169 | 零改动 |
| `experience/scheduler.py` | 197 | 零改动（四个触发点与关停链一字不动） |
| `experience/rules.py` | 65 | 零改动（「缺省即开启」的唯一解析点） |
| `api/app_settings.py` 的经验分支 | — | 只加新配置项，桥表范式不动 |

> 判断依据：本轮的全部改动都**落在 aggregate / graph / retrieval / disclosure / runtime / service
> 六个模块的既有接缝上**，以及三张新表。上一轮打磨过的采集口径（双时钟、`fee_known` 口径闸、
> `_take_slot` 的「恰好一个 seq 匹配」）**没有一条被推翻**。

### 2.2 本轮实读发现的九条缺陷（逐条含取证）

> 定级口径沿用上一轮：**P0** = 损坏真实资金路径或让特性静默失效；**P1** = 让产品说假话或让一条
> 承诺静默不兑现；**P2** = 卫生 / 规模 / 可维护性。

#### F-1（P1）· `close` 动作的 L2 反例可能是一条**支持该动作**的经验

`retrieval.most_conflicting()` 在 `action == "close"` 时走 `want_side is None` 分支，**直接返回**
`max(pool, key=|meanNetBps|)`——不看符号。而 `runtime._counter_wire` 把它送进
`disclosure.format_counter`，标题写死为「【交易经验 · 与本次提议最相冲突的一条】」。

**实测取证**（探针 3，`services/quant/.venv` 内直跑）：

```
[A] action=close -> picked meanNetBps = 88.0 | 该经验支持还是反对平仓？ 支持(正收益)
    【交易经验 · 与本次提议最相冲突的一条】
    BTC-USDT-SWAP 15m · 方向·做多：30 笔净额均值 +88bps（t=3.2），胜率 70%。（样本 30 笔，t=3.2）
    这是统计结论而非否决指令：若当前证据足以推翻它，请在复核理由中指名推翻的依据。
```

**为什么是 P1 而不是 P2**：这段文字进的是 `build_reflection_prompt`，读它的是一个裁决空间只有
`confirm` / `abort` / 缩仓的复核官。把一条支持性证据标注成「最相冲突」，唯一可能的行为后果是
**否掉一次本该确认的平仓**。方向仍然单侧（`v1:I-1` 不破），但产品说了一句不实的话。

#### F-2（P1）· `unknown` 轴的桶让**同一份 prompt 里出现两行逐字相同的经验**

`taxonomy.describe()` 在渲染语境短语时**丢弃取值为 `unknown` 的轴**，而 `signature_key()`
**保留**它们。于是 `{symbol,bar,regime=unknown,side}` 与 `{symbol,bar,volatility=unknown,side}`
是两个不同的桶（不同 key、不同节点），却渲染出**逐字相同**的一句话；并且那句话写的是
「BTC-USDT-SWAP 15m · 做多」，**声称的范围远大于它实际的支撑**（它只覆盖 regime 未知的那一部分）。

**实测取证**（探针 1 / 探针 2，端到端跑完 `materialize` → `Graph` → `retrieve`）：

```
[unknown axes] lessons: 44  with unknown axis: 12
distinct labels among unknown-axis lessons: 6 | labels shared by >1 lesson: 6
  LABEL: 全标的 15m · 做多：12 笔平均持仓仅 10 分钟，成本吃掉毛额 67%，净额均值 -25bps。
     sig: {"bar": "15m", "regime": "unknown", "side": "long"}
     sig: {"bar": "15m", "volatility": "unknown", "side": "long"}

L0: 0  L1: 3
  L1 score=3.999810673 match=4.0 sig={"symbol":"BTC-USDT-SWAP","bar":"15m","volatility":"unknown","side":"long"}
       label: BTC-USDT-SWAP 15m · 做多：24 笔平均持仓仅 10 分钟，成本吃掉毛额 62%，净额均值 -26bps。
  L1 score=3.999810673 match=4.0 sig={"symbol":"BTC-USDT-SWAP","bar":"15m","regime":"unknown","side":"long"}
       label: BTC-USDT-SWAP 15m · 做多：24 笔平均持仓仅 10 分钟，成本吃掉毛额 62%，净额均值 -26bps。
DUPLICATE RENDERED LINES IN ONE PROMPT: 1
```

**MMR 挡不住它**：两条签名的 Jaccard 是 **0.5**（交 2 / 并 4），`_mmr` 的冗余惩罚项按
`λ·0.5 = 0.25` 计，只要没有一条异质候选的归一分高出 0.25 以上，第二条照样入选。**去冗算法对
「文本相同但签名不同」这种形态天生看不见**——它比的是签名，不是渲染结果。

**可达性**：`regime` / `volatility` / `event` 取自 `market_context`，`wake` 取自
`self._wake.last_round_kind`，`confidence` 取自 `explain.meta.confidence`。任何一个缺席即折成
`unknown`（这是 `v1` 刻意的「绝不丢事件」纪律，本身是对的）。交易所侧 OCO 成交、兜底补挂成交、
续跑后的首笔成交都会命中。**W-A 一旦落地，`unknown` 会从偶发变成系统性**——所以 F-2 是 W-A 的
前置条件，不是可选项。

**另一半根因**：`describe()` 只拼**取值**、不拼**轴名**，所以它对签名**不是单射**。
`{...,event:"none"}` 与 `{...,horizon:"none"}` 同样会渲染成同一句话。只修 `unknown` 不够。

#### F-3（P1）· 反例段在预算收紧时**先丢掉免责句**，并且**违反自己的预算**

`disclosure.format_counter` 的超预算分支是
`f"{head}\n{body[:max(0, max_chars - len(head) - 1)]}"`——它丢掉 `detail`（样本数与 t）**和**
`tail`（「这是统计结论而非否决指令……」），而 `tail` 恰恰是 `v1:I-1` 那条单侧安全论证在**文本层
的唯一落点**。

**实测取证**（探针 3）：

```
[B] reflection_max_chars=240 len=134 含免责句=True
[B] reflection_max_chars=120 len= 79 含免责句=False   ← 只用了 79 字，却先把免责句丢了
[B] reflection_max_chars= 60 len= 60 含免责句=False   ← '……30 笔中亏 20 笔，' 半句截断
[B] reflection_max_chars= 20 len= 22 含免责句=False   ← len 22 > 预算 20：裁剪函数自己超预算
[B] reflection_max_chars= 10 len= 22 含免责句=False   ← 且正文为空 = 有标题没内容的空壳
```

三件事同时错：① 预算 120 时明明还剩 41 字却先扔掉免责句；② 半句截断把一个统计结论切成了残句；
③ 预算 ≤21 时返回长度 22 的字符串——**一个负责裁剪的函数不守自己的预算**，并产出模块 docstring
第 2 条明令禁止的「有标题没内容的空壳」。

**可达性**：`ai_trade_experience_reflection_max_chars` 是 `Field(ge=0, le=2000)` 的合法配置项
（env 可调），且**不在**设置中心的 12 项桥表里 ⇒ 不受 `budget_floor` 保护。**这正是 `v1:E-7`
（L0/L1 预算下限的唯一判据）漏掉的第三层。**

#### F-4（P1）· 争议段说「两条互相矛盾」却可能只给一条

`format_disputed` 的超预算分支是 `text[:max_chars]`，会把标题保下来、把第二条切掉。

**实测取证**（探针 3）：

```
[C] disputed budget= 90 -> '【交易经验 · 存在争议的结论（两条互相矛盾，均未进入纲要）】\n· BTC…+88bps（t=3.2），胜率 70%。'
```

标题承诺「两条」，正文一条。同样也可能在句子中间切断。

#### F-5（P1）· `applies_in` 边权**恒为 1.0**，`match` 因此退化成「命中轴数」

`graph._applies_in_edges` 的 docstring 写：「`w = 桶内该语境的事件数 / 桶的事件数`；一阶桶恒为 1；
高阶桶给出该语境在这条经验里的份额——于是决策轮拿不到 `side` 时，不匹配的轴自然减权。」

**这句话是假的。** `enumerate_buckets` 是按「事件签名的子集」建桶的，所以**桶内每一条事件在桶的
每一条签名轴上取值都等于桶的取值**，分子恒等于分母。

**实测取证**（探针 1）：

```
APPLIES_IN distinct weights: [1.0]
applies_in edge count: 111
lesson count: 50  sig width histogram: {1: 11, 2: 21, 3: 14, 4: 4}
```

以及探针 2 里三条 4 轴经验的 `match` 全部等于 **4.0**。两个后果：

1. **性能**：为算出一个常量，`_applies_in_edges` 对每个桶的每条事件重算 11 条语境
   （`Σ_buckets |events| × 11`，即约 `46 × N × 11`）。这是全量物化里最贵的一段之一，而它的产出
   是一个可以直接写死的 1.0。
2. **精准**（更重要）：`match = Σ_{c∈sig} A(c)·1.0` 没有对**未被满足的条件**做任何惩罚。一条
   「高波动 + 突破 + 做多 + 15m」的四轴经验，在只有两条轴被点亮时拿到 `match=2`，与一条完全
   匹配的两轴经验**同分**。而这两者的可信度完全不同：前者有一半前提不成立。需求里的「精准」
   在这个函数里没有落点。

#### F-6（P0）· 离线平仓的往返**既不成事件、也不计缺口**

`resume.settle_closed_offline` 在续跑前从交易所 `positions-history` 回读一笔平仓并
**直接 `repo.append_live_trade`**，完全不经 `session._dispatch`。而 `note_close` 与
`harvest_gap` 的三个自增点**全部**在 `_dispatch` 驱动的链上（`runtime.py` 三处，见 grep）。

**取证**：`grep -rn "harvest_gap" services/quant/app` 在 `resume.py` 里**零命中**。

于是 `ExperienceCard.tsx` 上那句逐字写着的文案——

```tsx
另有 {experience.harvestGap} 笔往返因续跑接管 / 暂停期离线平仓未能采集——
```

——在「暂停期离线平仓」这一半上**恒为 0**。产品指名道姓地承诺会计的一个数，在其中一条路径上
从来没有被计过。这是 `v1:I-8`（观测量必须有渲染落点）的镜像形态：**有渲染落点、有文案承诺，
没有写点。**

> 接管路径（`adopt_position`）**确实**会计：那笔仓最终经 `_dispatch` 平掉，`_take_slot` 找不到
> 开仓槽 ⇒ 返回 None ⇒ `harvest_gap += 1`。两条路径里只有一条是诚实的。

#### F-7（P1）· 接管持仓的往返永远进不了统计（`v1:§十一.7` 的欠账）

`_adopt_group` 把交易所真值灌回 executor，但既不建经验开仓断面、也不建 `AttributionLedger` 的
开仓断面（`cost_runtime.note_open` 只挂在开仓成交上）。故 `last_settled_trip()` 恒 None ⇒
`build_event` 恒 None ⇒ 该往返只贡献一个 `harvestGap`。

**为什么这是偏差而不只是缺失**：会被跨进程接管的仓位，是**那些没能在会话内平掉的仓位**——超时、
被套、等反弹。它们在净额分布上系统性偏左。把它们整类剔除，等于给整张经验表做了一次
**幸存者偏差**筛选，而筛掉的方向恰好是「教训」那一侧。

#### F-8（P2→规模 P0）· 物化是全量重建，规模墙之后直接停摆（`v1:§十一.8` 的欠账）

`service.rebuild()` 的第一行是 `load_experience_events(session, ExperienceEventQuery())`——**无过滤
全表**；随后 `run_in_threadpool(materialize, ...)` 铺 46 类桶、逐桶统计、重算全部边。
`materialize_bounded` 的规模闸是：事件数 > `materialize_max_events`（20 万）⇒
**不物化**，只在 `materialize_health.overflow` 置真。

也就是说，需求原文「经验是累积的，随着交易的进行能够进行不断的更新」在库越过阈值之后是
**停止**的。今天的处置（如实告知 + 手动重建）是诚实的，但它诚实地告诉用户的是一个**功能停摆**。

#### F-9（P2）· 没有任何机制回答「这条经验有没有用」（`v1:§十一.6` 的欠账）

`node.weight` 表达的是「这个语境桶历史上赚不赚」，**不是**「把这句话讲给模型之后有没有变好」。
两者是完全不同的量。今天没有任何一处记录「本轮注入了哪几条经验」，因此：

- 用户无法在管理界面回答「这条经验值不值得留着」——而这正是「经验管理」这件事的核心问题；
- `v1:R-1`（叙事经验 → 被读到 → 被照做 → 又被写成经验的自我强化回路）**没有任何检测手段**，
  上一轮只能靠「叙事不得自动升 iron」这条静态规则挡住一半。

### 2.3 承接策略

1. **W-D / W-E 先做**（都在纯函数层，零 IO、零数据模型改动），它们同时是 W-A 的前置：
   不修 F-2，W-A 引入的降级签名会立刻造出一批「声称范围大于支撑」的经验。
2. **W-A 其次**：**三处** hook 各 1–2 行，落在 `resume.py`（★ E-5：其中两处在
   `settle_closed_offline` **内部**，因为它跑在 `adopt_position` 之前）；核心逻辑住在新模块
   `adopted.py`。**前置多一条**：`STAT_ORIGINS` 的读侧口径（★ E-2 / §十二 P2.5）必须先落地，
   否则 W-A 写进去的事件一条也读不出来。
3. **W-B 再次**：三张新表里的两张 + 一个新的编排模块；**全量路径原样保留**，增量只在**五条**
   判据全部满足时才走。
4. **W-C 最后**：它依赖 W-A（否则「注入后表现」的样本同样带幸存者偏差）与 W-B（否则每次
   效用重算又是一次全量扫描）。
5. **W-F 贯穿**：每一条后端新增的观测量都必须在同一个 PR 里拿到渲染落点（`v1:I-8`）。

---

## 三、不变式（承接 `v1:I-1…I-10`，本轮新增 J-1…J-7，含评审新增的 J-2b / J-3b / J-7）

> `v1` 的十条不变式**全部继续有效**，其中三条在本轮被直接引用：`v1:I-1`（经验只影响 prompt，
> 绝不进任何确定性门禁 / 风控 / 仓位 / 下单判据）、`v1:I-7`（`cost_basis=="net" && fee_known`
> 才进 bps 统计）、`v1:I-9`（prompt 里的每个数字与每句断言必须为真）。

- **J-1 · 单侧性延拓（本轮的压舱石）**
  本轮新增的每一条自动化，其作用方向**只能是让经验层说得更少或说得更弱**：自动静音、覆盖率
  归一后的降权、降级签名落进更粗的桶、注入台账只用于**扣分**。
  **绝不允许**新增任何自动**加权 / 自动置顶 / 自动升档 / 自动扩容披露**的路径。
  机械判据：门禁 G-11 扫描 `utility.py` 与 `incremental.py`，禁止出现 `pinned = True`、
  `tier = TIER_IRON`、以及对 `l0_max` / `l1_max` / `*_max_chars` 的任何写操作。

- **J-2 · 增量必须与全量逐值一致，且**可机械判定（★ E-4 修订：把**路径依赖量**切出去）
  增量物化的产物与「同一批事件跑一次全量重建」的产物，在下列量上**逐值相等**：
  节点 key 集合；每个节点的 `label / signature / scope / support / weight / first_ts /
  last_seen_ts`；`stats` 的每一个键；边的四元组 `(src,dst,rel,weight)`。
  **两类明确的例外，各自单独钉死**：
  ① `medianNetBps`（1 bps 分箱近似）允许差 ≤ 1 bps，且 UI 上必须标注为近似值；
  ② `prevSign` / `signStreak` / 以及**由 `signStreak` 派生的 `tier`** —— 它们**按定义就是
     路径依赖的**（`roll_sign` 每被调用一次 streak 就 +1，`promote.py:85-86`），把同一批事件切成
     7 个批次跑，streak 必然是 7 而不是 1（探针 P6 实测：`tier` 因此从 `normal` 变成 `iron`）。
     对这三个量，等价判据换成**同批次数下的等价**：`incremental_batch` 调到大于事件总数、
     使增量恰好只跑一步 ⇒ 与全量**逐值相等**（G-10b）。
  违反即回退全量——**绝不允许「增量算错了但没人知道」这个状态存在**。

- **J-2b · 符号连续性只能变难，不能变容易**（★ E-4 · J-1 在 W-B 上的具体化）
  `sign_streak` 的产品语义是「这条结论**连续多少次重算**都是同一个方向」，它是 `iron`（L0 铁律）
  的第三道门槛。增量把「一次重算 = 一次全量」换成「一次重算 = 一个批次」，若不加约束就是一条
  **自动升档**路径——J-1 明令禁止的方向。本轮的处置是**两条同时成立**：
  ① 增量步**只对本批真正被新事件碰过的桶**调用 `roll_sign`（未被碰过的桶 streak 冻结，
     不再像全量那样「安静一轮也 +1」）；
  ② epoch 状态记 `signRollEpoch`，同一个桶在**同一次 `materialize_bounded` 调用**内最多前滚一次。
  合并效果：一条经验要升 `iron`，必须在**两次不同的物化 tick 里各拿到至少一条新的同号支撑事件**
  ——比全量时代**更难**，方向与 J-1 一致。这条差异必须在 §7.1 概览条的 tooltip 里写给用户看
  （G-11 只能证明「没有自动升档的赋值」，证不了「门槛没有变松」，故这一条由 A-40 承担）。

- **J-3 · 语境标签对签名单射**（★ E-7 修订：单射的主体是 **label 前缀**，不是 `describe`）
  `describe` 刻意**不含标的与周期**（它们由 `_prefix` 单独渲染，`aggregate.py:287-293`），所以
  「`describe` 对签名单射」这句话**在结构上就不可能成立**——`{BTC,15m,long}` 与 `{ETH,1H,long}`
  必然渲染出同一个 `方向·做多`（探针 P4 实测）。正确的不变式分两层：
  ① `describe(sig)` 对签名的**非 symbol/bar 部分**单射（拼轴名 + 取值即可满足）；
  ② `_prefix(sig)` 对**整个签名**单射（它已经带上了 symbol 与 bar）。
  两个不同的桶签名绝不允许渲染出逐字相同的整条 label。机械判据：G-12（两层分别断言）。

- **J-3b · 语境短语必须按 `(轴, 取值)` 取词，不能只按取值**（★ E-8）
  `label_of(axis, value)` 今天收了 `axis` 却不用（`taxonomy.py:172-174`），`VALUE_LABEL_ZH` 是一张
  按取值查的单表。于是 `horizon="none"` 渲染成「无事件」（实测），加上轴名之后是
  「持仓周期·无事件」——仍旧是一句不实的话。凡一个取值在**多条轴**上都合法（`none` / `unknown` /
  `low` / `mid` / `high`），必须先查 `(axis, value)` 表再回落到单表。机械判据：G-12c。

- **J-4 · 桶身份里的每一条轴都必须是可行动的**（★ E-9 修订：**缺席**与 `unknown` 同等处置）
  `unknown` 不是一个可行动的语境。任何一条轴取值为 `unknown`**或在签名里根本缺席**的组合
  **一律不进入桶枚举**（`symbol` / `bar` 除外——它们为空是数据缺陷，另有 `harvestGap` 覆盖）。
  「缺席」这一半是 W-A 引入的新形态（降级签名只写四条轴），而 `_combos_for` 今天对缺席轴是
  `signature[axis]` 硬取值 ⇒ **`KeyError`**（探针 P5 实测 `KeyError: 'regime'`），并且会被
  `materialize_bounded` 的裸 `except Exception` 吞成一行 warning ⇒ 图永久停更且全绿。
  这不是为了少建几个桶，是为了**让标签声称的范围恒等于它的支撑范围**。

- **J-7 · 「哪些事件参与统计」只能有一个判据**（★ E-2 · 本轮新增）
  `origin` 是这张表唯一能回答「这条经验的证据是本进程亲眼看到的、还是从交易所回读拼出来的」的列。
  它是一个**口径闸**，因此「参与统计的 origin 集合」必须是**一个常量**（`STAT_ORIGINS`），
  由桶枚举 / 证据表 / 概览计数 / 规模闸**四个读点共用**。
  推论一：新增一种 `origin` 时，**写侧与读侧必须同一个 PR**——只加写侧 = 数据写进去一条也读不出来
  （`v1` 在 `harvestGap` 上已经踩过一次「有渲染落点、没有写点」，这是它的镜像形态）。
  推论二：**L0 页眉那两个整数的口径必须等于这个集合**，否则页眉会把从未参与过任何一条经验的
  往返算进去，`v1:I-9` 当场破。机械判据：G-16。

- **J-5 · 归因是共现，不是因果**
  「这条经验被注入之后那一单赚了/亏了」是**共现**，模型是否真的采纳了它我们并不知道。
  所有 wire 键、UI 文案、prompt 文案一律使用「注入后表现」「一致率」这类措辞，
  **绝不使用**「贡献」「带来」「因此」。机械判据：G-13（中文文案源码扫描）。

- **J-6 · 裁剪函数必须守住自己的预算，且宁可整段不渲染也不出半句**
  任何 `format_*` 函数的返回值长度必须 ≤ 传入的 `max_chars`；装不下完整的一条 ⇒ **返回空串**，
  绝不返回「标题 + 空正文」，也绝不在句子中间截断。免责句（`v1:I-1` 的文本落点）与正文
  **同生共死**：留不下免责句就整段不渲染。机械判据：G-14（属性测试，1000 组随机预算）。
  **★ R-18 · 这条纪律同样覆盖 `aggregate.render_label`**：它今天在超 120 字时做
  `text[:119] + "…"`（`aggregate.py:284`），而 label 是**逐字进 prompt 的正文**，半句截断的
  统计结论与 F-3 的残段是同一类产品缺陷。本轮的处置是「让它不可能触发」而不是「截得好看些」：
  A-9 用**实算的最坏值**（110）把余量钉死，并要求 `render_label` 在真的越界时改为**丢掉最后一个
  完整子句**而不是砍在字中间（G-14 的第五个被测函数）。

---

## 四、技术设计

### 4.1 端到端时序（★ 标出本轮新增 / 变更）

```
[启动]  resolve_experience(raw, settings)            ← 不变（缺省即开启）
        load_session_graph(...)                      ← 不变（图 + totals 同一次取数）

[开仓成交] session._dispatch(side != close)
        └ experience.note_open(trade, seq, facts)
             └ freeze_open_context(...)              ← 不变（九轴冻结在开仓那一刻）
             └ ★ W-C：把「本轮实际注入的 l0/l1 key 与其断言符号」一并冻进开仓槽

[续跑接管] resume._adopt_group(...)
        └ ★ W-A：session.experience.adopt_open(       ← 新增，1 行 hook
                    entry_px, qty, ct_val, entry_fee_usd, open_wall_ts, side, seq)
             └ adopted.synth_open_snapshot(...)      ← 合成 OpenSnapshot（不进 AttributionLedger）
             └ adopted.degraded_context(...)         ← 只保留确定为真的轴，其余不写

[离线平仓] resume.settle_closed_offline(...)
        └ repo.append_live_trade(...)                ← 不变
        └ ★ W-A：session.experience.note_offline_close(trade)   ← 新增，1 行 hook
             └ 有合成断面 ⇒ trip_ledger.settle(...) ⇒ build_event ⇒ 入队 + 落盘
             └ 无合成断面 ⇒ harvest_gap += 1（F-6 的正解：终于计上了）

[平仓成交] session._dispatch(side == close)
        └ experience.note_close(trade, meta, trip)
        └ experience.persist_pending()               ← ★ W-C：事件与注入台账**同一次 commit**
        └ if take_dirty(): on_experience_dirty()     ← 不变（同步回调，不 await）

[后台物化] scheduler._materialize → service.materialize_bounded
        └ ★ W-B：incremental.plan(session, settings)
             ├ 五条判据任一不满足 ⇒ full rebuild（既有 rebuild()，一字不动）
             └ 全部满足   ⇒ incremental step（只吃 id > watermark 的事件）
        └ ★ W-C：utility.refresh(...)（同一把物化锁内，紧接其后）
        └ _note_materialized(...)                    ← 不变（健康读数唯一写回点）

[决策轮] runtime.inject_round → retrieve(graph, seed, cfg)
        └ ★ W-E：match = Σ_{c∈sig} A(c)·w / |sig|    ← 覆盖率归一
        └ 档位判定 / 三层披露                          ← 不变
[反思轮] runtime.inject_reflection
        └ ★ W-D：most_conflicting 的 close 分支修正；format_counter / format_disputed 修法
```

### 4.2 W-D · 说真话（四条修法）

#### 修法 D-1（对 F-1）· `close` 动作的反例必须真的是反例

`most_conflicting(graph, result, action)` 的 `close` 分支改为：

```python
# close：复核官需要的是「这一类平仓历史上亏在哪」，不是「净额绝对值最大的那一条」。
# 取 meanNetBps < 0 且 |t| 最大的一条；一条负的都没有 ⇒ 返回 None（整段不渲染）。
# **绝不**把一条正收益经验放在「与本次提议最相冲突」的标题下（F-1）。
if want_side is None:
    negatives = [i for i in pool if float(i.node.stats.get("meanNetBps") or 0.0) < 0]
    if not negatives:
        return None
    return max(negatives, key=lambda i: (abs(float(i.node.stats.get("tStat") or 0.0)),
                                         i.node.key))
```

**为什么不是「换个标题」**：「与本次提议最相冲突的一条」这个标题本身是对的——L2 的产品定义就是
**反例**（`v1:§4.8`）。错的是选择判据。若这一轮确实没有反例，正确的产品行为是**什么都不说**，
而不是找一条最响的话来填满这个位置。

#### 修法 D-2（对 F-2 / F-3 / J-3 / J-4）· 让标签与它的支撑范围一致

两处，缺一不可：

**① `aggregate._combos_for` 跳过取值为 `unknown` **或缺席** 的轴**（`symbol` / `bar` 除外）：

> ★ **E-9（评审 R-9）· 伪码必须写全，`...` 省掉的正是会炸的那四行。** `_combos_for` 今天在四处
> 做 `signature[axis]` 硬取值；W-A 的降级签名「其余轴一个都不写」⇒ 缺席 ⇒ **`KeyError`**
> （探针 P5 实测 `KeyError: 'regime'`）。这个异常会被 `service.py:361-363` 的裸
> `except Exception` 吞成一行 warning ⇒ **每一次物化都失败、图永久停更、日志与用例全绿**。
> 所以下面这段必须逐行落地，不许再留 `...`。

```python
def _combos_for(signature: dict[str, str]) -> list[dict[str, str]]:
    """一条事件所属的全部桶签名（受限枚举 + ★ J-4：unknown / 缺席轴一律不进桶身份）。

    `unknown` 不是一个可行动的语境。留着它会造出一批**标签声称的范围大于实际支撑**的经验
    （F-2 实测：同一份 prompt 里出现两行逐字相同的铁律），而它们对交易没有任何指导价值。
    **缺席**与 `unknown` 同等处置（★ E-9）：W-A 的接管事件只写四条确定为真的轴，其余轴根本
    不在字典里；此前的 `signature[axis]` 会当场 `KeyError` 并被上游裸 `except` 吞掉。
    事件本身**绝不丢**——它照常落进由已知轴构成的更粗的桶（`{symbol,bar}` 恒在）。
    """
    known = {axis: value for axis, value in signature.items()
             if value and (value != tax.UNKNOWN
                           or axis in (tax.AXIS_SYMBOL, tax.AXIS_BAR))}
    if tax.AXIS_SYMBOL not in known or tax.AXIS_BAR not in known:
        return []                          # 标的 / 周期缺失是数据缺陷，由 harvestGap 覆盖
    others = [axis for axis in tax.AXES
              if axis in known and axis not in (tax.AXIS_SYMBOL, tax.AXIS_BAR)]
    combos: list[dict[str, str]] = []
    for axis in others + [tax.AXIS_BAR]:                       # 一阶
        combos.append({tax.AXIS_SYMBOL: known[tax.AXIS_SYMBOL], axis: known[axis]})
        combos.append({axis: known[axis]})                     # 跨标的同形桶
    for axis in others:                                        # 二阶
        pair = {tax.AXIS_BAR: known[tax.AXIS_BAR], axis: known[axis]}
        combos.append({tax.AXIS_SYMBOL: known[tax.AXIS_SYMBOL], **pair})
        combos.append(dict(pair))
    for axes in THIRD_ORDER:                                   # 三阶：任一轴缺席 ⇒ 整条跳过
        if any(axis not in known for axis in axes):
            continue
        combos.append({axis: known[axis] for axis in axes})
        combos.append({axis: known[axis] for axis in axes if axis != tax.AXIS_SYMBOL})
    return combos
```

**② `taxonomy.describe` 拼轴名 + 按 `(轴, 取值)` 取词**（★ E-7 / E-8 修订）：

> ★ **E-7（评审 R-7）· 单射的主体不是 `describe`。** `describe` 刻意不含标的与周期（它们由
> `_prefix` 单独渲染），所以 `{BTC,15m,long}` 与 `{ETH,1H,long}` **必然**都渲染成 `方向·做多`
> （探针 P4 实测）。写成「`describe` 对签名单射」会让 G-12 必然误红，而那不是实现的错。
> 正确的两层判据见 J-3：`describe` 对签名的**非 symbol/bar 部分**单射，`_prefix` 对**整个签名**
> 单射。
>
> ★ **E-8（评审 R-8）· 只加轴名还不够，词表本身选错了词。** `label_of(axis, value)` 收了 `axis`
> 却不用，`VALUE_LABEL_ZH` 是按取值查的单表 ⇒ `label_of(AXIS_HORIZON, "none")` 实测返回
> **「无事件」**。加轴名之后变成「持仓周期·无事件」，从「两条重复」变成「一条读不懂」，
> 仍旧是 `v1:I-9` 意义上的不实。凡一个取值在多条轴上都合法（`none` / `unknown` / `low` /
> `mid` / `high`），必须先查 `(axis, value)` 再回落。

```python
# taxonomy.py 新增（闭合词表的一部分，G-7 中文词表门禁一并覆盖）
AXIS_VALUE_LABEL_ZH: dict[tuple[str, str], str] = {
    (AXIS_HORIZON, "none"): "不限",
    (AXIS_HORIZON, UNKNOWN): "周期未知",
    (AXIS_COST_REGIME, "low"): "低", (AXIS_COST_REGIME, "mid"): "中",
    (AXIS_COST_REGIME, "high"): "高",
    (AXIS_CONFIDENCE, "low"): "低", (AXIS_CONFIDENCE, "mid"): "中",
    (AXIS_CONFIDENCE, "high"): "高",
}


def label_of(axis: str, value: str) -> str:
    """语境的中文展示名；**先按 `(轴, 取值)` 取词**，再回落到按取值的单表（★ J-3b / E-8）。

    只按取值取词会让 `horizon="none"` 渲染成「无事件」——一个关于持仓周期的桶被描述成一个关于
    行情事件的事实。它不会报错，只会把一句不实的话逐字写进喂给模型的 prompt。
    """
    hit = AXIS_VALUE_LABEL_ZH.get((str(axis), str(value)))
    return hit if hit is not None else VALUE_LABEL_ZH.get(str(value), str(value))


def describe(signature: dict) -> str:
    """签名 → 人读短语；对签名的**非 symbol/bar 部分**单射（★ J-3 ①）。

    此前只拼取值，于是 `event:none` 与 `horizon:none` 渲染成同一句话，而 `unknown` 轴被
    静默丢掉、让标签声称一个比支撑更大的范围。两者都会让同一份 prompt 里出现重复或不实的行。
    **刻意仍不含标的与周期**：它们由 `_prefix` 渲染，在这里重复一次会白吃 22 字预算；
    整条签名的单射性由 `_prefix` 承担（★ J-3 ②）。
    """
    parts = [f"{AXIS_LABEL_ZH.get(axis, axis)}·{label_of(axis, signature[axis])}"
             for axis in AXES
             if axis in signature and axis not in (AXIS_SYMBOL, AXIS_BAR)]
    return "+".join(parts) if parts else "全局"
```

**字符预算复核（★ E-16 · 已在 `.venv` 内实算，v1 的三个数全错）**：`_LABEL_MAX = 120`。

| 量 | v1 写的 | **实测** | 依据 |
|---|---|---|---|
| 一个桶最多有几条非 symbol/bar 轴 | 3（「四轴桶」） | **2** | `_combos_for` 的桶形分布实测 `{0: 2, 1: 38, 2: 6}`——**三条其他轴的桶不存在**（`THIRD_ORDER` 的四组里最宽的也只有 `regime+side`） |
| 语境短语最坏 | ≈20 字 | **13 字**（`唤醒·新K线收线+方向·做多`） | 逐组合枚举 |
| 整条 label 最坏 | ≈88 | **110** | 25 字 instId + 最长模板 + 7 位样本数 + 带符号 t，逐组合 × 逐模板穷举 |

结论仍然成立（**110 ≤ 120**），但**余量只有 10 字而不是 32 字**，而且这 10 字全靠 instId 不超过
25 个字符。因此：**A-9 必须断言实算最坏值 ≤ 120 并把 110 这个数字写进用例**（不是「小于 120 即可」），
同时 J-6 的「不出半句」纪律扩展到 `render_label`（见 §三 J-6 与 G-14 第五个被测函数）。
**不许只在文档里算。**

**兼容性与一次性影响**：改法会让一批既有的 `unknown` 桶节点在下一次物化中不再出现 ⇒ 被
`mark_stale_lessons` 判为沉降。这是**正确的终局**，但会在管理界面上一次性冒出一批沉降条目——
故 W-F 必须同批交付「批量清理沉降经验」（§4.7），否则用户看到的是一次无法处置的脏数据涌入。

#### 修法 D-3（对 F-3 / F-4 / J-6）· 裁剪函数守住自己的预算

新增 `FRAME_OVERHEAD["l2"]` 与 `budget_floor("l2")`，并把 `format_counter` / `format_disputed`
改成**装得下就整段渲染、装不下就整段省略**（与 `_render` 的三条硬规则完全一致）：

```python
# ★ E-6（评审 R-6）· L2 框架句**实算**（用例 **A-6** 逐字钉死；v1 指的 A-10 是检索决定性，
# 指错了编号；v1 的 83 / 203 两个数也都低估了）：
#   head 21 + tail 39 + detail 最坏 26（样本 7 位数 + t 带符号 6 位 1 位小数）+ 两个换行 2 = 88
#   ⇒ FRAME_OVERHEAD["l2"] = 88
# L2 **没有** "1. " 序号前缀，所以它的单条预算是 120 而不是 MIN_ITEM_BUDGET(=124)：
#   budget_floor("l2") = 88 + 120 = 208
# 低估这个数的后果与 v1:E-7 逐字同形：reflection_max_chars ∈ [203, 207] 会**通过**启动期校验，
# 却让 L2 永远渲染不出一个字——一个不报错的空转。
FRAME_OVERHEAD = {"l0": 96, "l1": 40, "l2": 88}
MIN_ITEM_BUDGET_L2 = 120                      # L2 无序号前缀，与 MIN_ITEM_BUDGET(124) 差 4


def budget_floor(layer: str) -> int:
    """某一层字符预算的**唯一**下限判据（三处共用：启动期校验 / PUT 设置 / L2 新校验）。"""
    item = MIN_ITEM_BUDGET_L2 if layer == "l2" else MIN_ITEM_BUDGET
    return int(FRAME_OVERHEAD[layer]) + item


def format_counter(lesson: dict | None, max_chars: int) -> str:
    """L2 反例段；装不下**完整**的一条 ⇒ 空串（★ J-6）。

    此前的超预算分支同时犯三个错（F-3 实测）：先丢免责句、句中截断、返回值比预算还长。
    免责句是 `v1:I-1`「这不是否决指令」在文本层的**唯一**落点——留不下它就不该说这段话。
    """
    if not lesson or max_chars <= 0:
        return ""
    body = str(lesson.get("text") or "").strip()
    if not body:
        return ""
    text = f"{_L2_HEAD}\n{body}{_detail(lesson)}\n{_L2_TAIL}"
    return text if len(text) <= max_chars else ""
```

`format_disputed` 同款：**一对争议要么两条都渲染、要么整段省略**；不足以放下第二条时不许保留
那个写着「两条互相矛盾」的标题。

**配套（否则修法会静默把 L2 关掉）**：`reflection_max_chars` 的默认值必须 ≥ `budget_floor("l2")`。
实算默认值 **240 > 208** ✅，无需改默认。但**启动期校验必须覆盖它**：
`_check_experience_budgets` 增加一条——`reflection_max_chars == 0`（整段关闭，合法）
或 `>= budget_floor("l2")`，介于两者之间即**拒绝启动**（沿用 `v1:E-7` 的姿态：宁可拒绝启动，
不静默降级）。

> ★ **E-6 配套 ②**：`_cast_setting` 里那句 `budget_floor("l0" if "l0" in field else "l1")`
> （`service.py:224-228`）是一个**按字段名猜层**的分支。本轮不把 `reflection_max_chars` 加进
> 设置桥表（§6.3 只加三项 utility 开关），所以它暂时够用；但节点 #2 **必须**在该处补一行注释
> 写明「本函数只认 l0 / l1，新增任何 `*_max_chars` 桥项前必须先改这里」——否则下一轮把 L2 放进
> 面板时，它会静默拿 L1 的下限去夹 L2 的值。这是一处**被看见的**已知边界，不是欠账。

### 4.3 W-E · 算得准（覆盖率归一）

#### 修法 E-1（对 F-5 上半）· 删掉那段算出常量的扫描

`_applies_in_edges` 的 `weight` 恒为 1.0（实测），故：

```python
def applies_in_edges(lessons: list[tuple[GraphNode, dict]]) -> list[GraphEdge]:
    """`applies_in(lesson → context)`：**权重恒为 1.0**，由签名直接产出，不看事件。

    此前实现对每个桶的每条事件重算 11 条语境去求一个分数，而分子恒等于分母——桶是按
    「事件签名的子集」建的，桶内每条事件在桶的每条签名轴上取值都等于桶的取值（F-5 实测：
    `APPLIES_IN distinct weights: [1.0]`）。删掉这段 `Σ|events|×11` 的扫描既是提速，
    也是 W-B 增量化的前提：**它是唯一一处需要回看原始事件才能算出的边**。
    """
    return [GraphEdge(src_key=node.key, dst_key=tax.canonical_key(axis, signature[axis]),
                      rel=REL_APPLIES_IN, weight=1.0, support=node.support)
            for node, signature in lessons for axis in sorted(signature)]
```

> ⚠️ **这条修法是 W-B 的承重墙**：删掉它之后，全部四类边都可以由「节点表 + 语境计数器」推出，
> 增量物化因此**完全不需要回看历史事件**。

> ★ **E-15（评审 R-15）· `support` 的语义被这条修法悄悄换掉了，必须写明并保口径。**
> 既有实现里 `applies_in` 边的 `support = counts.get(key, 0)` = **桶内该语境的事件数（含被
> `v1:I-7` 排除的那些）**；上面这段新代码写的是 `support=node.support`，而 `node.support = stats.n`
> = **只数合格者**。这一列随 `GET /api/experience/graph` 下发给图谱画布（`_edge_wire`），
> 换了语义而不说，就是在 UI 上悄悄改掉一个用户已经在看的数字。
> **本轮的处置**：边的 `support` 改取**桶的事件总数**（`len(bucket.event_ids)`，与既有语义逐值
> 相同），它同样可以从累加器里直接读出（§6.1 的 `n + excluded`），**不需要回看事件**——承重墙
> 不受影响。G-10 的边四元组断言因此把 `support` 一并纳入（v1 只比 `(src,dst,rel,weight)`，
> 那会让这处漂移逃过门禁）。

#### 修法 E-2（对 F-5 下半）· `match` 改为覆盖率

```python
def _score(node, activation, graph, seed, cfg) -> ScoredLesson:
    """match 的语义订正为**这条经验自身的条件被当前语境满足的比例**，值域 [0,1]。

    此前 `match = Σ A(c)·w`（w 恒 1）= 命中的轴数，于是：
      · 签名越宽分越高（实测三条四轴经验 match 全部 = 4.0）；
      · **一半条件不成立的经验与完全匹配的经验同分**——四轴命中两轴 = 两轴命中两轴。
    覆盖率归一之后，前者 0.5、后者 1.0。这是需求里「精准」二字在打分函数里的落点。
    """
    edges = graph.applies_in.get(node.key, ())
    denominator = sum(float(w) for _c, w in edges) or 1.0
    match = sum(activation.get(c, 0.0) * float(w) for c, w in edges) / denominator
```

**为什么用「边权之和」而不是 `len(signature)` 作分母**：`_attach_seeded_lessons` 给
`manual` / `imported` / `narrative` 经验补的边权也是 1.0，两者一致；且若将来边权不再恒为 1，
这个式子**自动**仍然是覆盖率。**绝不写 `len(node.signature)`**——签名与边可能因物化撕裂而不同步
（`v1:E-14` 明确保留了撕裂可见这一形态），用边算才是用「图上真的有什么」算。

**行为变化的可见性与可控性**（这是一次会改变「模型看到哪三条」的改动，必须说清楚）：

- 排序变化**只影响 prompt**，`v1:I-1` 不破，最坏后果仍是「少说一条 / 说了另一条」；
- `ScoredLesson` 的 `match` 已经随 wire 下发，**设置中心的「检索预演」逐字展示打分明细**——
  用户可以在改动前后自己对比（**A-38** 要求节点 #2 在验收里贴出同一颗种子的前后对照；
  v1 指的 A-13 是 `fee_known` 排除，指错了编号）；
- **不加开关**：两套打分并存会立刻产生「预演看到的和真跑的不是同一条链」的漂移，那比这次改动
  本身危险得多（本仓库在 `v1:I-1` 的分页 / 筛选双路径上已有成文前科）。

> ★ **E-14（评审 R-14）· 归一化还有第二个落点，v1 只分析了排序、没分析压制。**
> `_suppress` 的争议判据是 `rel = |match₁ − match₂| / max(match₁, match₂, ε) ≤ 0.25 ⇒ 两条都踢出
> L0/L1，打包成「争议」送 L2`。而 `_DISPUTE_REL` 的注释逐字写着它用**相对**判据的理由是
> 「`match = Σ A(c)·w` **无量纲上界**，会随激活语境数增长」（`retrieval.py:24-26`）——
> **E-2 之后这个前提就没了**：`match ∈ [0,1]`，且**满匹配恒等于 1.0**。于是一大批互相
> `contradicts` 的经验对会同时取 1.0 ⇒ `rel = 0` ⇒ **双双被踢出 L0/L1**。
>
> 三件必须做的事：
> 1. **那段注释就地改掉**——留着它就是在源码里留一句假话（`v1:I-2` 的成文教训）。
> 2. **阈值语义随之变成绝对的**：`match` 现在是覆盖率，`0.25` 的含义从「相对差 25%」变成
>    「覆盖率差 0.25」，这是一个**更严格**且更可解释的判据，予以保留，但必须在注释里写明它
>    现在是绝对量纲。
> 3. **A-38 把这次行为变化钉出来**：同一颗种子、同一张图，贴出改动前后
>    `len(l0) / len(l1) / len(disputed) / len(suppressed)` 四个数的对照。方向必须是
>    「L0/L1 条数不增、disputed 条数不减」——**说得更少**，与 J-1 一致；若实测出现相反方向，
>    说明归一化写错了，回到设计。

### 4.4 W-A · 采全（接管与离线平仓）

#### 核心思路：合成开仓断面，让**既有的**结算链原样跑完

**绝不新写一份 bps 换算。** `trip_ledger.settle(open_snap, close_trade, ctx)` 已经把口径
（`net_mode` / `ct_val` / 双腿费 / `fee_known` 传染）全部处理妥当。W-A 要做的只是给它一个
**合成的 `OpenSnapshot`**：

```python
# experience/adopted.py（新模块，纯函数、零 IO、绝不抛）
def synth_open_snapshot(*, seq: int, side: str, entry_px: float, qty: float,
                        entry_fee_usd: float | None, open_wall_ts: int) -> OpenSnapshot:
    """接管持仓 → 一个合成开仓断面（**只给经验层用，绝不注册进 AttributionLedger**）。

    不注册进归因台账的理由：那条链跑在真实资金上，喂给它一个合成断面会改变 `aiStats` /
    已实现 edge 校准 / 验收卡上用户已经在看的数字，收益不抵风险（沿用 `v1:§十一.7` 的判断）。
    经验层需要的只是一个口径正确的 `SettledTrip`，本函数给它，仅此而已。

    `estimate=None`（接管仓没有本进程的门禁估计）⇒ `edge_error_bps` 恒 None ⇒
    「估计偏差型」模板对这类事件天然不触发。这是**对的**：我们确实不知道它当初估了多少。

    ⚠️ **`ts` 必须与 `close_trade["ts"]` 同一时钟**。离线平仓那条 trade 的 `ts` 取自
    `positions-history` 的 `uTime`（墙钟秒），故本函数的 `ts` 必须取交易所 `cTime`（同一时钟）。
    一侧 bar 钟一侧墙钟会让 `hold_seconds` 变成一个天文数字，且**不会报错**——本仓库在
    `holdSeconds` 上有成文前科（提交 `a8ed650` 的双时钟审计）。
    """
```

**入场费是可知的**：`_adopt_group` 已经调 `rebuild_continuity(member.run_id)` 从 DB 全量 `Trade`
行重建出 `entryFeeUsd`。可得 ⇒ `open_fee_known=True`；不可得 ⇒ `False`，该事件因 `v1:I-7`
被排除在 bps 统计外但**仍然入表**并计入 `excluded`——**被看见的缺失，而不是不存在的缺失**。

#### 降级语境签名：只写确定为真的轴

```python
def degraded_context(*, inst_id: str, bar: str, side: str,
                     horizon_profile_id: str | None) -> dict[str, str]:
    """接管仓的开仓语境：**只写四条确定为真的轴**，其余轴一个都不写。

    `regime` / `volatility` / `event` / `wake` / `confidence` 在接管那一刻是**真的不知道**——
    那笔仓是在上一个进程、上一段行情里开的。`v1:§十一.7` 写得对：「猜出来的语境比没有语境更坏」。
    但它当时的结论是「所以整笔都不采」，那是把幸存者偏差留了下来。

    正解是**降级而不是丢弃**：不写的轴在 `_combos_for`（★ J-4）里天然不参与桶枚举，于是这条
    事件只会落进由已知轴构成的**更粗的桶**（`{symbol,bar,side}` / `{symbol,bar,exit}` …）。
    标签声称的范围因此**恒等于**它的支撑范围——这正是 F-2 修法给出的结构保证。
    """
```

> **F-2 与 W-A 的依赖关系必须写死在实施顺序里**：先 J-4 再 W-A。反过来做，第一批接管事件就会
> 造出一堆「体制未知」的假宽经验。

#### 三处 hook（★ E-5 修订：v1 的两处**顺序与数据源都错了**）

> ★ **E-5（评审 R-5）· 离线平仓跑在接管之前。** 实读 `resume._adopt_group`：
>
> ```python
> 291:            await settle_closed_offline(member, contract, creds=assessment.creds, ...)
> 294:        adopt_position(executor, assessed.get("actual"))
> ```
>
> v1 把 `adopt_open` 挂在 `adopt_position` **之后**、把 `note_offline_close` 挂在
> `append_live_trade` 之后，§4.1 的时序图也把 `[续跑接管]` 排在 `[离线平仓]` 之前——**与源码相反**。
> 照 v1 落地，`note_offline_close` 永远取不到合成断面、永远走 `harvest_gap += 1` 分支：
> F-6 只兑现了「计上」，没兑现「采到」，而 §零 的表格与 A-14 都写着这条路径会被采到。
>
> 还有两处同源的错：① 被判 `closed_offline` 的成员，`assessed["actual"]` 是 `None`（仓已在
> 交易所侧平掉），所以就算顺序对了，`adopt_position` 那一侧**本来也拿不到入场价与数量**——
> 合成断面的数据源是 `contract["position"]`（`_offline_exit_trade` 已经在读它的 `openCTime` /
> `size`），不是 `assessed["actual"]`；② `settle_closed_offline` 在回读不到行时**提前 `return`**
> （`resume.py:333-335`），v1 的 hook 挂在 `append_live_trade` 之后 ⇒ **最该被计的那个缺口反而
> 不计**。

| # | 位置 | 新增 | 说明 |
|---|---|---|---|
| **H-1** | `resume.settle_closed_offline` 开头，`fetch_position_history` **之前** | `member.session.experience.adopt_open(**synth_from_contract(contract))` | 数据源是 `contract["position"]`（`openCTime` / `size` / `posId` / 方向），与 `_offline_exit_trade` 同源。**必须在回读之前**，因为两条 return 分支都要用它 |
| **H-2** | `resume.settle_closed_offline` 的**两条**出口 | `await member.session.experience.note_offline_close(trade)`（`trade is None` 时传 `None`） | 有回执 ⇒ 结算 + 入表 + 落盘；无回执（`_mark_offline_gap` 那条早退分支）⇒ `harvest_gap += 1`（**F-6 的正解，两条路都覆盖**） |
| **H-3** | `resume._adopt_group`，`adopt_position(...)` 之后 | `member.session.experience.adopt_open(...)` | **仍持仓**的接管（F-7 那一类）：入场价 / 数量取 `assessed["actual"]`，入场费取 `continuity["entryFeeUsd"]`，存进 `_open_slots` 的同一张表（key 仍是 `(side, seq)`），等它在会话内被平掉时由既有的 `note_close` 收口 |

**H-1 与 H-3 共用同一个 `adopt_open`**，只是入参来源不同；`adopt_open` 自身沿用 `note_open` 的
两条既有纪律：`if not self.writing: return`（经验模式关掉时一个字都不写），以及
「同一 `(side, seq)` 已有槽 ⇒ 不重新冻结」。

`note_offline_close` 走的是 `settle` → `build_event` → `_pending` → `persist_pending` 这条**既有**
链，`origin` 由 `EventIdentity(origin="adopted")` 承载（列已预留，零迁移）。

#### ★ E-2（评审 R-2）· `origin="adopted"` 必须同时打通**读**侧，否则这一整条工作流的产物为零

```python
# app/db/ai_experience_repositories.py:104,112-113（实读）
origin: str | None = "live"       # 恒 "live"：口径闸而非死码（评审 P2-3）
if query.origin:
    stmt = stmt.where(AiExperienceEvent.origin == query.origin)
```

`rebuild()` 的取数正是 `load_experience_events(session, repo.ExperienceEventQuery())`
（`service.py:288`）⇒ 恒 `origin == "live"`；`load_evidence` 另有一处硬编码
`where(AiExperienceEvent.origin == "live")`（`:150`）。**只写不改读，W-A 采到的往返一条也进不了桶、
一条也进不了证据抽屉**，而 `harvest.adopted` 会在 UI 上显示「已补采 N 笔」——比不做更坏。

更隐蔽的一半：`count_events`（`:91-93`）与 `overview`（`:461`）**都不过滤 origin**。于是这些
从未贡献过任何一条经验的往返，会被算进 L0 页眉那句「来自 X 组有成交记录的自动交易的 **Y 笔**
已结算往返」——`v1:I-9` 在一个新地方被破。

**修法（一处常量、四个读点，绝不各判一次）**：

```python
# app/db/ai_experience_repositories.py
STAT_ORIGINS: tuple[str, ...] = ("live", "adopted")
"""参与统计的事件来源集合——桶枚举 / 证据表 / 概览计数 / 规模闸**共用这一个常量**。

`imported` 之类将来可能出现的来源默认**不进**统计：口径闸是本表的产品属性，不是过滤器的默认值。
"""

@dataclass(frozen=True)
class EventQuery:
    ...
    origins: tuple[str, ...] = STAT_ORIGINS      # 取代原来的单值 `origin`
    after_id: int | None = None                  # ★ E-11：增量水位线（v1 的算法要它，v1 没加）
```

四个读点逐一改到同一个常量上：`load_experience_events`（`WHERE origin IN :origins`）、
`load_evidence`（同）、`count_events`（同）、`overview` 的 `events` / `sessions` / `excluded`
三个计数（同）。**G-16 是这条修法的机械判据**：源码扫描断言 `AiExperienceEvent.origin ==` 这个
字面量在数据层出现次数为 **0**（全部改走 `in_(STAT_ORIGINS)`）。

> **为什么不干脆把接管事件也写成 `origin="live"`**：`origin` 是这张表**唯一**能回答
> 「这条经验的证据是本进程亲眼看到的，还是从交易所回读拼出来的」的列。抹掉它，
> §7.3 那句「接管补采 2 笔」就无从算起，A-11 也失去断言对象。**口径要打通，不是要抹平。**

#### `harvestGap` 的口径订正（`v1` 的文案终于兑现）

`stats()` 的 `harvestGap` 拆成两个键，因为它们的产品含义不同：

```json
"harvest": {"gap": 3, "adopted": 2, "reasons": {"noOpenContext": 1, "noTrip": 1, "slotEvicted": 0, "persistFailed": 1, "offlineNoReadback": 0, "offlineInconsistent": 0}}
```

> ★ **E-5 配套**：后两个 `reasons` 键对应 `settle_closed_offline` 的**早退分支**
> （`_mark_offline_gap` 的 `has_row=False` / `True` 两种）。v1 的 hook 挂在 `append_live_trade`
> 之后，这两条路一条都走不到——而它们恰好是「暂停期离线平仓未能采集」这句 UI 文案里唯一真实
> 存在的两种原因。`reasons` 的键集必须与 `runtime` 里实际自增的点**一一对应**，多一个空键就是
> 一个永远显示 0 的假分类（`v1:I-8` 的反面形态）。

`adopted` = 本轮**采到了**的降级事件数（正面数字）；`gap` = 仍然采不到的（负面数字）；
`reasons` 让「为什么采不到」在 UI 上可点开（`v1:I-8`：算了就要有渲染落点）。

### 4.5 W-B · 算得动（真正的增量物化）

#### 4.5.1 三条使增量成为可能的前提

1. **E-1 之后，四类边全部不需要回看事件**：`applies_in` 由签名产；`contradicts` 由节点表两两比；
   `co_occurs` 由**语境计数器**算 NPMI；`context` 节点的 `support` 即语境计数器本身。
2. **统计量绝大多数是可合并的充分统计量**：`n / wins / Σx / Σx² / Σhold / Σcost / Σ|gross| /
   Σedgeerr / min ts / max ts / excluded` 全部满足 `merge(a,b) = a + b`。
3. **唯二不可合并的两个量各有有界解**：
   - `medianNetBps` → **稀疏定宽直方图**（1 bps 一箱，夹到 ±5000，只存非零箱）。确定性、
     可合并、误差 ≤ 1 bps，且它今天**只被详情抽屉的一格读**（`ExperienceDetailDrawer.tsx:80`），
     不进任何 prompt ⇒ 近似是安全的，但**必须在 UI 上标注**（J-2 后半）。
   - `symbols`（去重标的数，`resolve_scope` 的 `global_min_symbols` 闸要用）→ **有界集合**，
     上限 64 个标的；溢出即记 `symbolsOverflow=true` 并按 `>= 64` 处理（任何 `min_symbols`
     阈值都会通过）。真实使用中标的数远小于 64。

#### 4.5.2 `costRegime` 分位是唯一的全局耦合，用 **epoch 冻结**解开

`cost_regime_cuts` 是按**当前事件全集**算的三分位，而它决定每条事件的 `cost_regime` 标签，
进而决定桶身份。**分位一变，历史桶全错。** 这就是增量最难的一处，解法是把它变成一个**版本**：

```
epoch 状态（app_settings["experience.materialize_epoch"]，单键 JSON）
{
  "epoch": 7,                     // 每次全量重建 +1
  "watermarkEventId": 128743,     // 已折进累加器的最大事件 id
  "taxonomyVersion": 3,           // taxonomy.TAXONOMY_VERSION，轴集/折叠规则/模板集变更即 +1
  "cuts": { ... },                // 本 epoch 冻结的分位（与 COST_CUTS_KEY 同值，这里留快照供比对）
  "costHist": {                   // 成本 bps 的稀疏定宽直方图，按 (instId|bar) 分组 + __global__
    "__global__": {"12": 40, "13": 91},
    "BTC-USDT-SWAP|15m": {"12": 30, "14": 61}
  },
  "nodeCount": 3820,
  "eventCount": 128743,           // ★ E-10 / E-11：已折进累加器的事件条数（NPMI 的分母 + B-5 的对账基准）
  "signRollTick": 412,            // ★ E-4：物化 tick 序号，同一 tick 内同一个桶最多前滚一次
  "builtTs": 1787640000,
  "lastMode": "incremental"       // full | incremental
}
```

> ★ **E-10（评审 R-10）· `eventCount` 不是可选项，NPMI 没它算不出来。**
> `_co_occurs_edges` 的 npmi 分母逐字是 `total = len(events)`（`graph.py:343,358`）。
> §6.1 的 `ai_experience_context_counts` 只有 `(a_key, b_key, count)`，v1 的 epoch JSON 也没有
> 事件总数 ⇒ 增量算出的 NPMI 与全量**不可能相等** ⇒ G-10 的边四元组断言必红。

**五条判据，任一不满足即本次走全量重建**（并把 `epoch` 加一、重写全部累加器）：

| # | 判据 | 不做的后果 |
|---|---|---|
| B-1 | `taxonomyVersion` 与代码常量一致 | 轴集合 / 折叠规则改了却按老桶继续累加 ⇒ 图与代码语义永久错位，且完全静默 |
| B-2 | 把新事件折进 `costHist` 后重算的三分位，与 `cuts` 的每条边界相差 ≤ `cuts_drift_bps`（默认 1.0），且没有新出现的 `(instId,bar)` 分组跨过 `cost_regime_min_samples` | 分位漂了却继续用旧标签 ⇒ 「今天的 low 和半年前的 low 不是一回事」，缓慢失真且不可察觉（`v1:§4.4` 第 3 条已经点名过这个形态） |
| B-3 | `nodeCount` ≤ `graph_node_cap` | 增量只会加节点、不会施加全局 top-N 截断 ⇒ 图无界增长 |
| B-4 | epoch 状态存在、版本可识别、累加器表非空 | 冷启动 / 手工清库后按增量跑 = 从一个空底座上继续加，图**永久残缺** |
| **B-5** ★ | `COUNT(events WHERE id <= watermark AND origin IN STAT_ORIGINS) == eventCount` | **★ E-11（评审 R-11）**：id 在 `INSERT` 时分配、在 `COMMIT` 时可见。多成员会话各自 `persist_pending`，两条连接完全可能出现「id 100–105 比 106–110 后提交」。水位线一旦推到 110，100–105 就**永久不被折进累加器**，而且没有任何东西会发现——一条**静默丢样本**的路径。这条对账只花一次 `COUNT(*)`，不等即回全量并 `epoch+1`（自愈） |

> **判据本身必须可见**：`materialize_health` 增 `mode`（full/incremental）、`fullReason`
> （**五**条判据里是哪一条把它打回全量：`taxonomy` / `cuts_drift` / `node_cap` / `no_epoch` /
> `count_mismatch`）、`pendingEvents`（水位线之后还剩多少条没吃）。三个键在 §七 各有渲染落点。

#### 4.5.2b ★ E-3（评审 R-3）· 规模闸必须下沉，否则 W-B 一分钱产品价值都不产生

```python
# app/ai_auto/experience/service.py:350-359（实读）—— 这道闸在 `_run_materialize` **之前**
events = await repo.count_experience_events(session)
if events > int(derived.ai_trade_experience_materialize_max_events):
    await _write_health(session, {..., "overflow": True})
    return {"ran": False, "reason": "overflow", "events": events}
return await _run_materialize(session, derived)      # ← incremental.plan 住在这里面
```

v1 把 `incremental.plan` 挂在 `_run_materialize` 里面，而这道闸在它**之前**短路。于是库越过
20 万条之后，增量**根本没有机会跑**，`overflow` 照旧置真、UI 照旧打「规模墙」——本轮 §零 那句
「本轮把它做掉」在实现层面一行都没兑现。

**修法**：把闸 ③ 从「事件总数」判据改成「**本次真的要跑全量**」判据：

```python
plan = await incremental.plan(session, derived)      # 五条判据，纯读，不碰 CPU 段
if plan["mode"] == "full" and events > cap:
    ...overflow: True, "fullReason": plan["reason"]...   # 只有全量才受规模墙约束
    return {"ran": False, "reason": "overflow", "events": events}
return await _run_materialize(session, derived, plan=plan)
```

**增量路径不受 `materialize_max_events` 约束**，理由是它的成本与库大小近似无关：它只读
`id > watermark` 且被 `incremental_batch` 封顶，触碰的桶数由本批事件决定。这一句必须写进
`materialize_max_events` 的配置注释里——否则下一个读到这个名字的人会以为它还是总闸。

> **`service.py` 的 +45 行硬预算因此要重新分配**：这处分流约 12 行，其余转发压到 33 行。
> 超出的一律下沉到 `incremental.py`（§8.1），**不放宽 800 行阈值**。

#### 4.5.3 增量一步的算法

```
incremental_step(session, settings) -> dict
 1. 读 epoch 状态；**五条**判据任一不过 ⇒ 返回 {"mode": "full", "reason": ...}，由调用方走 rebuild()
 2. rows = load_experience_events(EventQuery(after_id=watermark,
                                             origins=STAT_ORIGINS,
                                             limit=incremental_batch(默认 20000)))
    ★ E-11：`after_id` 是本轮**新增**的过滤字段（既有 EventQuery 只有
      inst_id/bar/since_ts/until_ts/origin/limit/offset），落在 §8.2 新增的那一行数据层改动上
    · 空 ⇒ {"mode":"incremental","ran":False,"reason":"up_to_date"}
    · 满批 ⇒ 本次只吃这一批，脏标志**保留**（下一次继续），pendingEvents 如实回报
 3. for event in rows:                      # 单趟，O(len(rows) × 46)
      · 折进 costHist（B-2 的输入）
      · 折进语境计数器 cooc（single[c] += 1；pairs[(a,b)] += 1）
      · eventCount += 1                      # ★ E-10：NPMI 的分母 + B-5 的对账基准
      · for bucket_key in combos(event):     # J-4 之后不含 unknown / 缺席轴
            acc[bucket_key].fold(event)      # 充分统计量 + 直方图 + 标的集合
            touched.add(bucket_key)
 4. 落 acc（按 (epoch, key) upsert）与 cooc（按 (epoch, a, b) upsert）
 5. for key in touched:                      # 只重算被碰过的桶
      stats = acc[key].to_stats()            # 与 compute_stats 逐值等价（G-10 钉死）
      node  = _lesson_node(bucket(key), stats, rule, prior, roll=(tick > prior_roll_tick[key]))
        ★ E-4：符号前滚只对**本批真被碰过**的桶做，且同一个 tick 内至多一次（J-2b）
        ★ E-12：first_ts 改成 min(old.first_ts or ∞, stats.firstTs)，与批次顺序无关（R-12）
    upsert_lesson_nodes(那些节点)
 6. 边：全量重算但**不看事件**——
      applies_in  ← 节点签名（E-1；support 取 acc[key].n + acc[key].excluded，见 E-15）
      co_occurs   ← cooc 计数器算 NPMI，分母用 epoch.eventCount（★ E-10）
      contradicts ← 节点表两两比（≤ node_cap² 但有 t 阈值预筛，实测 4000 节点内 <200ms）
    replace_edges(...)                       # 既有函数，一字不改
 7. live_keys = {key for key in 累加器表 where epoch==当前 and n>0}      # ★ E-1（评审 R-1）
    unmute_reappeared(live_keys) / mark_stale_lessons(live_keys)
    ★★ **绝不能传 touched**——见下方 E-1 的说明，那会在一个 tick 内把整库经验静音
 8. 写回 epoch 状态（watermark = 本批最大 id，eventCount，nodeCount，signRollTick+1,
    lastMode="incremental"）
```

#### ★ E-1（评审 R-1）· 第 7 步是本轮**最容易写成灾难**的一行

`mark_stale_lessons(session, seen_keys)` 的实现是**全表扫 + 反向标记**：

```python
# app/db/ai_experience_repositories.py:315-336（实读）
rows = (... select(AiExperienceNode).where(kind == _LESSON_KIND, source == "stat"))
for row in rows:
    if row.key in seen_keys or row.muted or row.mute_reason == "user":
        continue
    row.muted = True                 # ← 凡不在 seen_keys 里的，一律静音
```

v1 §4.5.3 第 7 步逐字写着「既有函数，一字不改」。全量重建时 `seen_keys` 是**本次算出的全部经验**，
所以这条语义是对的；**增量步的 `touched` 只有本批碰过的十几个桶**，于是第一个 tick 之后，
除那十几条以外的**整库经验**都会被置 `muted=True, mute_reason="stale"`。而
`retrieval._collect` 过滤 `not graph.nodes[key].muted`（`retrieval.py:211`）⇒ **60 秒之内
L0/L1 当场变空**，`tsc` / `ruff` / 全量用例全绿，日志一行都没有。这与 `v1:P0-1` 是同一种失败形态。

**修法**：增量步传给这两个函数的必须是 `live_keys` = **本 epoch 累加器表里 `n > 0` 的全部桶 key**
（累加器是 append-only 的，所以它天然就是「本 epoch 至今存在过的全部经验」）。
两个推论必须写明：

1. **增量步不产生新的 `stale`**——一个桶在增量语义下**不会消失**（事件只增不减）。
   `stale` 只可能在**全量重建**时产生（`node_cap` 截断、J-4 让一批 `unknown` 桶消失、
   `taxonomy` 变更）。这是正确的终局，也让「为什么这条经验突然沉降了」这个问题始终有答案。
2. **G-10 必须把 `muted` / `mute_reason` 纳入逐值比对**（v1 的字段清单里没有它们）。
   这条门禁是唯一能当场抓住本缺陷的东西：不比 `muted`，上面那场灾难可以完整地通过 G-10。

**为什么第 5 步只重算 touched 桶是安全的**：`BucketStats` 的每一个字段都只依赖该桶自身的事件；
`_lesson_node` 额外读 `prior`（前滚 `first_ts` / `prev_sign` / `sign_streak`），那也是节点自身的
历史。**唯一的跨桶依赖是 `cost_regime_cuts`，而它已经被 epoch 冻结。**

**锁与线程**：完全沿用既有姿态——`_materialize_lock(timeout)` 有界等待、CPU 段
`run_in_threadpool`、`_MATERIALIZE_BUSY` 进程级单槽、`MaterializeBusyError` 回
`{"ran": False, "reason": "lock_timeout"}`。**本轮不新增任何一把锁、不新增任何一个后台任务**
（`v1` 在这三处各踩过一个 P0，不重蹈）。

#### 4.5.4 全量重建保留为预言机与兜底

`rebuild()` **一行不删**。它在三种情况下跑：**五条**判据打回、用户手动点「从事件重建」、
`epoch` 首建。并且它在结尾**额外**做一件事：把本次算出的全部桶累加器与语境计数器写进两张新表，
作为下一次增量的底座。

#### 4.5.4b ★ E-4（评审 R-4）· 符号连续性：J-2 唯一不可达的那三个字段，以及它背后的安全问题

```
探针 P6（services/quant/.venv 内实跑）
  full      -> prev_sign=1 sign_streak=1     tier = normal
  7-batch   -> prev_sign=1 sign_streak=7     tier = iron
```

`roll_sign` 按**每被调用一次 +1** 计数（`promote.py:85-86`），`resolve_tier` 的 `iron` 第三条门槛
是 `sign_streak >= 2`（`:111-112`）。把同一批事件切成 7 个批次跑，`streak` 必然是 7 而不是 1。
两个后果，第二个远比第一个严重：

1. **G-10 在 `tier` / `prevSign` / `signStreak` 上必然红**，而且不是实现 bug——是 J-2 这条不变式
   自己写错了。节点 #2 面对一条「必然红且改不动」的门禁只有两条路：悄悄放宽它，或者停在这里。
   两条都坏。**处置**：J-2 把这三个字段切出去，改由 **G-10b**（`incremental_batch` 调到大于事件
   总数、使增量恰好只跑一步）断言同批次数下的逐值相等。
2. **把「连续两次物化同号」换成「连续两个批次同号」＝一条自动升档路径**——`iron` 直通 L0 铁律，
   而 J-1 明令「绝不允许新增任何自动升档 / 自动加权路径」。v1 全文没有察觉自己的压舱石被自己的
   另一条工作流顶穿。**处置**（J-2b，两条同时落地）：
   - 增量步**只对本批真被新事件碰过的桶**调 `roll_sign`；未被碰过的桶 `streak` 冻结
     （全量时代是「安静一轮也 +1」，现在不是了）；
   - epoch 状态记 `signRollTick`，同一个桶在**同一次 `materialize_bounded` 调用**内至多前滚一次。

   合并效果：一条经验要升 `iron`，必须在**两次不同的物化 tick 里各拿到至少一条新的同号支撑
   事件**——比全量时代**更难**，方向与 J-1 一致。**这是一次会让某些既有 `iron` 停在 `normal` 的
   行为变化**，必须在 §7.1 概览条的 tooltip 里写给用户看（A-40）。

#### 4.5.4c ★ E-12（评审 R-12）· `first_ts` 的前滚必须与批次顺序无关

`_lesson_node` 今天写的是 `first_ts=(old.first_ts if old and old.first_ts else stats.firstTs)`
（`graph.py:271`）。G-10 的协议是「清空后按 7 个**随机**批次逐批增量」，于是增量的 `first_ts`
是第一批的最小值、全量是全批的最小值——**必然不等**。
改成 `first_ts = min(old.first_ts or stats.firstTs, stats.firstTs)`：既保住「首次出现时刻不被
重算抹掉」这条原意，又让它变成一个**与顺序无关的单调量**。这是一次纯改进，全量路径同样受益。

### 4.6 W-C · 自证（注入台账与效用闭环）

#### 4.6.1 写：注入台账与事件**同一次 commit**

- **冻结时机 = 开仓那一刻**。`note_open` 把「本轮 wire 里 l0/l1 的 key + 该经验当时断言的净额
  符号 `claim_sign`」一起冻进开仓槽（`OpenContext` 新增一个字段）。
  用开仓那一刻而不是平仓那一刻，理由与 `v1` 冻结九轴语境完全一致：**决策是在开仓时做的**。
- **落库时机 = 平仓那一刻**，与事件行**同一个 session、同一次 commit**。
  **绝不在开仓时写**：开了仓不一定平（进程崩溃 / 被接管），开仓时写会在表里留下一批
  `net_bps` 永远为 NULL 的悬空行，而那些行会静默拉偏效用统计。
- 自然键与事件表**逐字一致**（`group_id, inst_id, seq, exit_ts`），故 `INSERT OR IGNORE` 的
  幂等语义两张表同源。

#### 4.6.2 算：三个量，一条判据

`experience/utility.py`（纯函数、零 IO）对每个 `lesson_key` 聚合它的注入记录：

| 量 | 定义 | 用途 |
|---|---|---|
| `injected` | 注入次数（只计**开了仓**的轮次） | 「这条经验被用过几次」 |
| `meanNetAfter` / `tAfter` | 注入后那些往返的净额均值与 t（只计 `v1:I-7` 合格的） | 「用它的那些单后来怎么样」 |
| `agreement` | `sign(净额) == claim_sign` 的比例 | 「事实站在它那边的比例」 |

**失效判据（唯一一条，且只导向静音）**：

```
injected >= utility_min_samples(默认 12)
  且 claim_sign != 0
  且 sign(meanNetAfter) == -claim_sign        # 事实与它的断言系统性相反
  且 |tAfter| >= utility_t_threshold(默认 2.0)
⇒ muted = True, mute_reason = "ineffective"
```

**四条纪律**：

1. **只静音，绝不反向加权**（J-1）。一条「说亏其实赚」的经验被静音，**不会**变成一条「说赚」的
   新经验——那等于让系统自己发明一条从未被统计支撑的结论。
2. `mute_reason="ineffective"` 与 `"user"` **严格分离**：`unmute_reappeared` 的自动解除只碰
   非 `user` 的静音（`v1:E-9` 的既有纪律），本轮沿用；样本增长后不再满足判据 ⇒ 自动解除。
3. **置顶（`pinned`）的经验不参与自动静音**——用户的显式意志优先于统计（与
   `v1:I-4` 的「置顶是唯一的人工升 iron 路径」对称）。但**效用读数照常展示**，让用户自己看见
   「你置顶的这条，注入 30 次之后的均值是 -18bps」。
4. **文案一律「注入后表现」**（J-5）。`agreement` 的中文是「与事实一致率」，不是「准确率」，
   更不是「贡献」。

#### 4.6.3 何时算（★ E-13 修订：v1 说的那把锁，在那个位置**拿不到**）

> ★ **E-13（评审 R-13）**：锁是在 `rebuild()` **内部**由 `async with _materialize_lock(...)` 持有、
> 并在 `rebuild()` 返回时释放的（`service.py:284-305`）。v1 一边说「在 `materialize_bounded` 的
> 同一把锁内」，一边在 §8.2 要求 `rebuild()` 一行不删——两条同时成立时，`utility.refresh` 只能跑在
> **锁外**。而它要写 `muted` / `mute_reason`，与 `mark_stale_lessons` / `unmute_reappeared` 写的
> 是同一批列：并发下会丢更新（把 `ineffective` 覆盖成 `stale`，或反过来），而且是那种「数字不对
> 但不报错」的丢法。

**修法（不动 `rebuild()` 一行，把锁的作用域上提）**：

```python
# service.py —— 新增一个薄编排，rebuild() 与 utility.refresh 共处同一把锁
async def _run_materialize(session, settings, *, plan) -> dict:
    async with _materialize_lock(timeout):           # ← 锁在这一层持有
        result = await _materialize_unlocked(session, settings, plan=plan)
        if settings.ai_trade_experience_utility_enabled:
            result["utility"] = await utility.refresh(session, settings)
    await _note_materialized(session, settings, result)
    return result
```

`rebuild()` 保持现状（它自己那把 `_materialize_lock` 变成**可重入的空操作**：传
`lock_timeout=None` 且检测到本任务已持锁即直接执行）。**这是本轮唯一一处触碰锁语义的改动**，
因此它必须满足两条硬条件，缺一即退回「refresh 跑在锁外并接受最终一致」：

- **绝不新增第二把锁、绝不新增后台任务**（`v1` 在这两处各踩过一个 P0）；
- **G-5（停止路径时延）必须重跑并贴数**：`utility.refresh` 进了物化锁，就意味着一次终局物化的
  时长里多了它；`force=True` 的终局物化跑在 `_stop_session` / `pause_group` 的**后台任务**里
  （不在 `stop()` 的 await 链上），所以理论上不影响 `/stop` 的 20s 死线——**但这句话必须由
  G-5 的实测毫秒数证明，不能由这段推理证明**。

`utility.refresh` 自身的成本上界：一次 `SELECT` 扫注入台账（与事件表同量级、每笔往返 ≤ 6 行）
+ 纯内存聚合，无 CPU 密集段，**不进 `run_in_threadpool`**。

---

### 4.7 W-F · 管理面（每个后端观测量的渲染落点）

| 后端产出 | 渲染落点 | 不给落点的后果 |
|---|---|---|
| `utility.*`（三个量 + 失效标记） | 列表新增「效用」列；详情抽屉新增「注入后表现」区 + 「为什么被自动静音」+ 一键解除 | 后端算了没人看见 = 死码（`v1:I-8`） |
| `harvest.{gap,adopted,reasons}` | 运行中 `ExperienceCard` + 设置中心概览条（可点开看 reasons 明细） | F-6 修了但用户仍以为没修 |
| `materializeHealth.{mode,fullReason,pendingEvents}` | 概览条第四句注记 | 用户无法判断「为什么这次又走了全量」 |
| 沉降经验批量清理 | 工具栏新增「清理沉降经验」（二次确认 + 明示条数 + 跳过置顶与用户静音） | D-2 落地当天会涌入一批无法处置的沉降条目 |
| `medianNetBps` 的近似性 | 详情抽屉该格加「≈」与 tooltip「基于 1bps 分箱的近似值」 | J-2 允许的唯一误差变成一处无声的不准 |
| **★ J-2b 的门槛变化**（`iron` 现在需要跨两次 tick 各拿到新的同号证据） | 概览条「铁律 N 条」那格的 tooltip 增一句：「铁律需要连续两次重算都拿到新的同号证据；本次重建有 M 条因证据未更新而暂留在『可用』档」 | 用户会看到 `iron` 条数下降却没有任何解释——一次**看起来像 bug 的正确行为**，不说就是产品失信（A-40） |
| **★ `harvest.reasons` 的六个键** | 概览条 `[明细]` 弹层逐键列出，**零值的键也列**（列出来才知道「这条路今天没走过」与「这条路不存在」的区别） | v1 的四个键里有两个（离线平仓的两种）在 v1 的 hook 位置上永远走不到，是假分类 |

---

## 五、接口设计（wire）

### 5.1 新增 REST（3 个）

| 方法 | 路径 | 入参 | 回执 | 备注 |
|---|---|---|---|---|
| `GET` | `/api/experience/utility` | `instId?` `bar?` `minInjected?` `limit?`(≤500) | `{items:[{key,id,label,injected,meanNetAfter,tAfter,agreement,claimSign,muted,muteReason}],asOfTs,minSamples,tThreshold}` | 列表页与效用面板同源；筛选判据复用 `service._match`，**绝不另写一份** |
| `POST` | `/api/experience/prune` | `{scope:"stale", dryRun:bool}` | `{matched:int, deleted:int, skippedPinned:int, skippedUserMuted:int}` | `dryRun=true` 必须先跑一次并把条数显示给用户；只删 `source="stat" && muted && mute_reason!="user" && !pinned` |
| `GET` | `/api/experience/health` | — | `{mode,fullReason,pendingEvents,epoch,watermarkEventId,lastMs,slow,torn,overflow,lastTs}` | 从 `overview` 里**拆出来**：概览要轮询，而 overview 会扫节点表 |

### 5.2 变更的既有 wire（全部为**新增键**，无删除、无改名）

```jsonc
// GET /api/experience/overview —— 新增 3 键
{ "harvest": {"gapTotal": 12, "adoptedTotal": 5, "reasons": {...}},
  "utility": {"scored": 812, "ineffective": 14},
  "materializeHealth": { /* 既有 6 键 */ "mode": "incremental", "fullReason": null, "pendingEvents": 0 } }

// GET /api/experience/lessons 的每个 item —— 新增 1 键（缺席 = 尚未有注入样本）
{ "utility": {"injected": 21, "meanNetAfter": -8.4, "tAfter": 2.3, "agreement": 0.71} }

// GET /api/experience/nodes/{id} —— 新增 1 键
{ "utility": { /* 同上 + injections: [{groupId, exitTs, netBps, layer}] 最近 20 条 */ } }

// POST /api/experience/rebuild —— 入参 scope 放开（v1 已预留该字段）
{ "scope": "stat" | "full" }     // "full" = 强制全量并 epoch+1；"stat" = 由五条判据自选

// AI-trade status / aiStats 的 experience 块 —— 新增 2 键
{ "harvest": {"gap": 1, "adopted": 2, "reasons": {...}},   // 取代原来的标量 harvestGap（保留旧键一轮）
  "utilityMuted": 3 }                                       // 本会话开始时已被判为失效的条数

// POST /ai-trade/start 的 experience 入参 —— **零变更**（默认开启语义一字不动）
```

> **旧键保留一轮**：`experience.harvestGap`（标量）与新的 `experience.harvest.gap` **同时下发**
> 一个版本。理由：`lib/store.ts` 的 `emptyRoundTrace` 是**逐键手抄**装配（`v1:I-7` 的成文教训），
> 桌面端旧包与新后端并存时，删旧键会让运行中卡片直接空掉。

> ★ **E-2 配套 · `overview` 的 `events` / `sessions` 两个数的口径必须一起改。**
> 它们是 L0 页眉那句「来自 X 组有成交记录的自动交易的 Y 笔已结算往返」的唯一取数来源
> （`service._totals_wire`），而 `repo.overview` 今天**不过滤 origin**（`:461`）。W-A 落地后若不改，
> 这两个数会把「写进了表但从未参与过任何一条经验」的往返算进去 ⇒ `v1:I-9` 破。
> 改到 `origin IN STAT_ORIGINS` 之后两者恒等，**并且这是一处需要 A-42 逐值断言的地方**——
> 「页眉的数字 == 真的进了桶的事件数」是这条不变式唯一可机械判定的形态。

### 5.3 前端 `httpTimeout` 登记（G-6④ 的延伸，缺一即静默落错档）

- `/experience/prune` → **写路径**（`WRITE_PREFIXES`）。它是删除操作，超时必须说
  「结果未知，请刷新核对」而不是「失败」。
- `/experience/utility`、`/experience/health` → 读路径，走默认档，**不进** `LONG_EXACT_PREFIXES`。
- **不得**把 `/prune` 加进 `WRITE_ACTION_SUFFIXES`（那会波及既有的其它 `/prune` 类路径）。
  实读 `lib/httpTimeout.ts:127` 的现值是 `["/stop","/pause","/resume","/end","/repair"]`，
  确实没有 `/prune`；这条约束是**防止节点 #2 顺手加**，不是描述现状。

> ★ **R-19（P2，已落修）· §7.2 那行「已自动静音 N 条」是一次新的读，v1 没给它落点。**
> 启动弹窗要显示这个数，就得在弹窗打开时发一次请求——它既不在本节的登记表里，也不在 §7.4 的
> 净增预算表里（`AiTradeExperienceFields.tsx` 根本没出现）。**处置**：这个数**不发新请求**，
> 改从**已经在轮询的** `GET /api/experience/health` 的回执里取（§5.1 已把它从 `overview` 拆出来
> 正是为了轮询），弹窗打开时若尚无数据就**整行不渲染**（沿用「宁可不说，绝不说一个占位的 0」）。
> `health` 因此增一个键 `utilityMuted`。这样净增预算只落在 `AiTradeExperienceFields.tsx` 上
> （见 §7.4 新增行），零新增 IO 面。

---

## 六、数据模型

### 6.1 三张新表（`create_all` 自动建，**零迁移代码**）

> 沿用 `v1` 的成文判断：`create_all` **会**建缺失的表、**不会**给既有表补列。因此本轮
> **一列都不往既有三张表上加**——需要的新维度全部走新表。

#### `ai_experience_injections`（注入台账，append-only）

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | int PK | |
| `group_id` / `inst_id` / `seq` / `exit_ts` | str/str/int/int | **与 `ai_experience_events` 逐字同一自然键** |
| `lesson_key` | str(96) | 被注入的经验 key |
| `layer` | str(4) | `l0` \| `l1`（L2 反例不计——它进的是反思轮，不是决策依据） |
| `claim_sign` | int | 该经验注入当时断言的净额符号（-1/0/+1），**冻结值**，绝不回填 |
| `injected_wall_ts` | int | 开仓墙钟 |
| `net_bps` / `fee_known` / `cost_basis` | float?/bool/str | 平仓时与事件行同一次 commit 写入（反规范化：效用聚合是热路径，join 不划算，且两者同刻写入无漂移窗口） |
| `created_ts` | int | |

约束：`UNIQUE(group_id, inst_id, seq, exit_ts, lesson_key)`；`Index(lesson_key)`；`Index(exit_ts)`。

#### `ai_experience_buckets`（桶级充分统计量，按 epoch 分版）

| 列 | 说明 |
|---|---|
| `epoch` `key` | `UNIQUE(epoch, key)`；`Index(epoch)` |
| `signature_json` `scope` | 桶身份 |
| `n` `wins` `excluded` | 计数（`n` 只数 `v1:I-7` 合格者，与 `compute_stats` 逐字同义） |
| `sum_net` `sum_net_sq` | 均值 / 标准差 / t 的充分统计量 |
| `sum_hold` `sum_cost` `cost_n` `sum_abs_gross` `gross_n` `sum_edge_err` `edge_n` | 其余均值各自的分子分母（**分母各自独立**：`meanCostBps` 的分母是「有 cost 的条数」，不是 `n`） |
| `hist_json` | 稀疏定宽直方图 `{binIndex: count}`，1 bps 一箱，夹到 ±5000 |
| `symbols_json` `symbols_overflow` | 有界去重集合（≤64） |
| `first_ts_kept` `last_ts_kept` `first_ts_all` `last_ts_all` | **四个**：`compute_stats` 在 `kept` 非空/空两种情况下取自不同集合，不存四个就无法逐值等价 |
| `cost_fallback` | 任一事件用了全库分位 |
| `updated_ts` | |

#### `ai_experience_context_counts`（语境计数器，co_occurs 与 context.support 的唯一来源）

| 列 | 说明 |
|---|---|
| `epoch` `a_key` `b_key` | `b_key=""` 表示单点计数；`UNIQUE(epoch,a_key,b_key)` |
| `count` | |
| `updated_ts` | |

> 规模估算：闭合词表下语境节点约 10 轴 × 平均 4 取值 + 标的数 ≈ 50–80 个，两两配对 ≈ 3200 行
> 上限。**这是本设计敢把 co_occurs 做成计数器而不是重扫事件的全部理由。**

### 6.2 新增配置（`config.py`，现有 **36** 项 → **47 项**）

> ★ **R-17（P2，已落修）**：v1 的标题写「→ 48 项」、正文只列 11 个字段、§8.2 又写「+12 项新配置」
> ——三处互不相等。实测 `Settings.model_fields` 里 `ai_trade_experience_*` 恰为 **36** 项（探针 P1），
> 本节新增 **11** 项（6 + 4 + 1），合计 **47**。第 12 条不是字段，是 `reflection_max_chars` 的
> **下界语义变更**（见下方注释），它不占字段名额。§8.2 的「+12 项」同步改为「+11 项」。

```python
# ---- W-B 增量物化（6 项）----
ai_trade_experience_incremental_enabled: bool = True          # 总闸；False ⇒ 恒走全量（一键回滚）
ai_trade_experience_incremental_batch: int = Field(default=20000, ge=100, le=200000)
ai_trade_experience_cuts_drift_bps: float = Field(default=1.0, ge=0, le=100)
ai_trade_experience_hist_bin_bps: float = Field(default=1.0, gt=0, le=50)
ai_trade_experience_hist_clamp_bps: float = Field(default=5000.0, gt=0)
ai_trade_experience_symbols_cap: int = Field(default=64, ge=1, le=1000)

# ---- W-C 效用闭环（4 项）----
ai_trade_experience_utility_enabled: bool = True              # 总闸；False ⇒ 不写台账、不算、不静音
ai_trade_experience_utility_min_samples: int = Field(default=12, ge=1, le=10000)
ai_trade_experience_utility_t_threshold: float = Field(default=2.0, gt=0, le=100)
ai_trade_experience_utility_detail_rows: int = Field(default=20, ge=1, le=200)

# ---- W-A 采全（1 项）----
ai_trade_experience_adopt_harvest_enabled: bool = True        # False ⇒ 退回 v1 行为（只计 gap）

# ---- W-D 说真话（1 项，仅为把校验挂上去）----
# reflection_max_chars 的下界不再是 0：0（整段关闭）或 >= budget_floor("l2")，中间值拒绝启动。
```

**三项总闸各自独立**，且都在 `.env.example` 里成文。理由是回滚粒度：W-B 出问题只需关增量，
不该把 W-C 一并关掉。

### 6.3 设置桥表（12 项 → **15 项**）

新增可写：`utilityEnabled` / `utilityMinSamples` / `utilityTThreshold`。
**必须走 `service._SETTING_BRIDGE` 同一张表**（`v1:I-10`），并被 G-9 逐项覆盖——
漏一项就是一个「改得动、永远不生效」的假控件。

---

## 七、前端设计

### 7.1 设置中心 →「交易经验库」（既有页面，新增三处）

```
┌ 概览条 ────────────────────────────────────────────────────────────┐
│ 1204 条经验 · 38 组会话 · 2043 笔往返                                │
│ ⓘ 上次重建 3 分钟前（增量，12ms）· 待处理 0 条                        │  ← 新增 mode/pending
│ ⚠ 另有 12 笔往返未能采集（续跑接管 8 / 落盘失败 4）              [明细] │  ← F-6 之后这个数才是真的
│ ⓘ 14 条经验因「注入后表现与其结论系统性相反」被自动静音          [查看] │  ← 新增
└───────────────────────────────────────────────────────────────────┘
[标的▾][周期▾][档位▾][来源▾][效用▾]        [清理沉降经验] [新增] [导入] [导出] [从事件重建▾]
 └ 新增筛选：全部 / 有注入样本 / 已判失效      └ 新增          └ 新增下拉：智能(默认) / 强制全量
┌ Tabs：列表 | 图谱 | 检索预演 ─────────────────────────────────────┐
```

**列表新增「效用」列**（一列三态，绝不用颜色单独承载语义——沿用仓库既有的
`contrastTokens` 可读墨水层与「颜色 + 形状 + 文字」三重编码纪律）：

- 无样本：`—`（灰，tooltip：「尚未在任何一次开仓时被注入」）
- 有样本：`注入 21 · 之后 −8.4bps · 一致 71%`
- 已判失效：`⚠ 已自动静音` + 行左侧一条竖线（形状，不只是颜色）

**详情抽屉新增「注入后表现」区**：三个大数 + 最近 20 条注入记录的小表（时间 / 分组 / 层 /
该笔净额），以及**当它被自动静音时**的一段说明：

> 这条经验断言「该语境下做多亏钱」，但在它被注入之后开出的 21 笔中，净额均值为 **+18.4bps**
> （t=2.6，与其结论一致率 29%）。系统据此把它自动静音。
> **这是共现关系，不是因果**——我们无法确认模型是否真的采纳了它。 〔恢复启用〕

（J-5 的文案纪律在这段话里逐字兑现。）

**「清理沉降经验」**：点开先跑 `dryRun` → 弹二次确认，逐条列出「将删除 N 条 / 跳过置顶 M 条 /
跳过你手动静音的 K 条」→ 确认后执行。**绝不静默批删**。

### 7.2 启动弹窗「经验模式」

**零变更。** 需求里「启动自动交易时可选是否启用经验模式（默认开启）」在 `v1` 已经交付，
`resolve_experience` 的三层优先级（运维总闸 > 用户显式 > 服务端默认）一字不动。

唯一新增的是那张卡片上的一行**只读**说明（当库里存在已判失效的经验时）：

> 经验库中有 14 条已被自动静音，本次不会喂给 Agent。

### 7.3 运行中的可见性（`ExperienceCard`）

`harvestGap`（标量）替换为两行：

```
本次已沉淀 12 笔往返 · 接管补采 2 笔
另有 1 笔未能采集（无开仓语境）——这张表因此偏乐观 [?]
```

### 7.4 前端净增预算（**硬判据**）

| 文件 | 现状 | 预算 | 处置 |
|---|---|---|---|
| `ExperienceSection.tsx` | 519 | **+90** | 超出即把新面板整体下沉到子组件 |
| `ExperienceList.tsx` | 130 | +40 | 效用列 |
| `ExperienceDetailDrawer.tsx` | 223 | +70 | 效用区 |
| `ExperienceToolbar.tsx` | 135 | +45 | 清理按钮 + 效用筛选 |
| `experienceTypes.ts` / `experienceApi.ts` | 267 / 141 | +60 / +40 | 新 wire 类型与三个调用 |
| `aiTradeStats/ExperienceCard.tsx` | 62 | +30 | 采集两行 |
| `Agent/AiTradeExperienceFields.tsx` ★ | 实测 — | +18 | R-19：启动弹窗那行「已自动静音 N 条」的只读说明（数据取自已在轮询的 `health.utilityMuted`，**不新增请求**） |
| **新建** `experience/ExperienceUtilityPanel.tsx` | — | ≤180 | 效用面板（失效清单 + 一键恢复） |
| **新建** `experience/ExperiencePrunePrompt.tsx` | — | ≤120 | dryRun 二次确认 |

**超预算的唯一处置是再下沉一层，不是放宽阈值**（`v1:§8.2` 的成文纪律）。

---

## 八、文件 / 模块变更计划

### 8.1 后端 · 新建

| 文件 | 一句话意图 | 上限 |
|---|---|---|
| `app/ai_auto/experience/accumulate.py` | 桶级充分统计量：`fold_event` / `merge` / `to_stats` / 稀疏直方图中位数 / 有界标的集（纯函数、零 IO） | ≤320 |
| `app/ai_auto/experience/incremental.py` | 增量编排：epoch 状态、**五条**判据、水位线推进、cuts 漂移判定、`live_keys` 汇总（★ E-1） | ≤300 |
| `app/ai_auto/experience/utility.py` | 注入台账 → 三个量 → 唯一一条失效判据（纯函数、零 IO） | ≤200 |
| `app/ai_auto/experience/adopted.py` | 接管 / 离线平仓的合成开仓断面与降级签名（纯函数、零 IO、绝不抛） | ≤160 |
| `app/db/ai_experience_accum_repositories.py` | 三张新表的数据层（累加器 / 语境计数 / 注入台账） | ≤420 |

### 8.2 后端 · 修改

| 文件 | 现状行数 | 改动 | 净增预算 |
|---|---|---|---|
| `experience/aggregate.py` | 390 | `_combos_for` 跳 `unknown`（J-4）；`compute_stats` 拆出「从累加器还原」的等价入口 | +45 |
| `experience/taxonomy.py` | 191 | `describe` 拼轴名（J-3）；新增 `TAXONOMY_VERSION` 常量 | +20 |
| `experience/graph.py` | 408 | `applies_in_edges` 去掉事件扫描（E-1）；新增 `materialize_from_accumulators` 入口 | +70 |
| `experience/retrieval.py` | 309 | `match` 覆盖率归一（E-2）；`most_conflicting` 的 close 分支（D-1） | +25 |
| `experience/disclosure.py` | 222 | `FRAME_OVERHEAD["l2"]` / `budget_floor("l2")`；`format_counter` / `format_disputed` 修法（D-3） | +40 |
| `experience/runtime.py` | 427 | 注入 key 冻结、`adopt_open`、`note_offline_close`、`harvest` wire 拆分 | +85 |
| `experience/service.py` | **740** ⚠ | 只加**薄转发**（`materialize_bounded` 分流 + `utility.refresh` 一行 + 三个新读点） | **+45（硬上限）** |
| `ai_auto/resume.py` | 479 | **三处** hook（各 1–2 行；两处在 `settle_closed_offline` 内部，★ E-5）+ docstring | +18 |
| `ai_auto/session.py` | **2479** ⚠ | **零逻辑**，只把 `experience` 门面暴露给 resume（已有 `experience` property，可能 0 行） | +0～3 |
| `api/experience.py` | 258 | 3 个新端点 + `rebuild` 的 `scope="full"` 分支 | +75 |
| `app/schemas.py` | — | 5 个新 wire 模型 | +70 |
| `app/config.py` | — | **11** 项新配置 + `reflection_max_chars` 下界校验 + 注释 | +55 |
| `app/db/models.py` | — | 3 张新表 | +95 |
| `app/db/ai_experience_repositories.py` ★ | **488** | **★ E-2 / E-11（v1 的清单里漏了这个文件）**：`STAT_ORIGINS` 常量、`EventQuery` 增 `origins` / `after_id`、四个读点（`load_experience_events` / `load_evidence` / `count_events` / `overview`）统一走它 | +45 |
| `app/db/repositories.py` | — | 新数据层的**再导出**（G-8 会当场抓漏） | +12 |

> ⚠ `service.py` 已 740 行、阈值 800。**任何超出 +45 的逻辑必须下沉到 §8.1 的新模块**，
> 这是 `v1:I-8` 逼出来的成文纪律（`importing.py` 就是这么拆出来的）。
> ⚠ `session.py` 早已超阈，本轮**只允许接线，不允许任何判断逻辑**。

### 8.3 前端

见 §7.4 的预算表（6 个修改 + 2 个新建），外加 `lib/httpTimeout.ts` +6（两条路径登记 + 注释）。

### 8.4 测试（新建）

`test_experience_incremental.py`（含 G-10 等价门禁）、`test_experience_utility.py`、
`test_experience_adopted.py`、`test_experience_disclosure_budget.py`（G-14 属性测试）、
`test_experience_label_injective.py`（G-12）、`test_experience_single_sided.py`（G-11 源码扫描）；
前端 `ExperienceUtilityPanel.test.tsx`、`ExperiencePrunePrompt.test.tsx`、
`experienceUtility.test.ts`。

---

## 九、测试与验收标准

### 9.1 门禁（每条须贴**实测输出**，不采信上游报告）

> ★ **R-20（P2，已落修）· 基线必须写死，否则「与改动前一致」不可核对。**
> 本轮开工基线（迭代 1 节点 #3 终审的实测值，合入 `8e05dd9` 之后）：
> 后端 `pytest` **3870 passed / 7 failed**，7 条红全部来自**未跟踪**的
> `test_factor_library_expansion.py`（属另一未完成特性 `factor-library-crypto-expansion`，
> 用 `git ls-files` 可证不在本次范围内）；前端 vitest **180 文件 / 2094 用例全绿**；
> `tsc --noEmit` 0；`ruff check services/quant` 0。节点 #2 / #3 必须与这四个数逐个对齐。

**承接 `v1` 的 G-1…G-9，全部继续有效**（后端全量 `pytest -q` 且 **`-u`**、**不得并发**；
`NODE_ENV=test pnpm --filter web vitest run`；`tsc --noEmit`；`ruff check services/quant`；
零回归 G-2；停止路径时延 G-5；四条源码扫描 G-6；中文词表 G-7；数据层符号齐备 G-8；
可写配置读路径齐备 G-9——本轮桥表增到 15 项，G-9 必须**逐项**覆盖）。

本轮新增六条：

- **G-10 · 增量 ≡ 全量（本轮最重要的一条，不绿则 W-B 一行都不许接）**（★ E-4 / E-1 / E-15 修订）
  造 3 组数据集（500 / 5000 / 50000 条事件，含 `unknown` 轴、**缺席轴的 `origin="adopted"` 事件**、
  跨标的、`fee_known=False`、同 seq 反手腿）。对每一组：① 跑一次 `rebuild()` 得 `S_full`；
  ② 清空节点 / 边 / 累加器，按 7 个随机批次逐批增量物化得 `S_inc`；③ 断言 J-2 的**每一条**：
  - 节点 key 集合相等；
  - 逐节点 `label / signature / scope / support / weight / first_ts / last_seen_ts` 逐值相等；
  - **`muted` / `mute_reason` 逐值相等**（★ E-1：不比这两个，「增量把整库静音」这场灾难可以
    完整地通过 G-10——这是本条门禁最重要的一个断言）；
  - `stats` 除 `medianNetBps` / `prevSign` / `signStreak` 外逐值相等，median 差 ≤1bps；
  - 边**五**元组 `(src, dst, rel, weight, support)` 集合逐值相等（★ E-15：v1 只比四元组，
    会放过 `applies_in.support` 的语义漂移）。
  - **`prevSign` / `signStreak` / `tier` 不在本条比对范围内**（★ E-4：它们按定义路径依赖，
    见 J-2 ②），由 G-10b 单独覆盖。

- **G-10b · 同批次数下的完全等价（★ E-4 · 补上 G-10 切出去的那三个字段）**
  把 `incremental_batch` 调到大于事件总数，使增量恰好只跑**一步**；此时增量与全量的
  `prevSign` / `signStreak` / `tier` **必须逐值相等**。这条是「累加器算错了 vs 批次切法不同」的
  唯一分离判据：G-10 红而 G-10b 绿 ⇒ 是切法；两条都红 ⇒ 是算错了。
- **G-11 · 单侧性源码扫描（J-1 的机械判据）**
  按 AST（去 docstring 后 `ast.unparse`）扫 `utility.py` / `incremental.py` / `adopted.py`：
  不得出现 `pinned = True`、`tier = 'iron'`、以及对 `l0_max` / `l1_max` / `*_max_chars` /
  `manual_seed_weight` / `pinned_evidence_floor` 的任何赋值。
  **按 AST 而不是按原文**：这三个模块的 docstring 恰恰要逐字解释「为什么这里不能有它」，
  按原文扫会把解释本身判成违规（`v1:I-2` 的成文教训）。
- **G-12 · 标签单射（J-3 / J-3b）**（★ E-7 / E-8 修订：v1 的写法必然误红）
  - **G-12a**：对随机生成的 2000 组签名，断言 `describe` 在**非 symbol/bar 部分**上单射
    （v1 写的「对签名单射」在结构上不可能成立——`describe` 刻意不含 symbol/bar，
    `{BTC,15m,long}` 与 `{ETH,1H,long}` 必然同为 `方向·做多`，探针 P4 实测）。
  - **G-12b**：对同样 2000 组签名断言 `_prefix` 对**整个签名**单射；并对一次真实物化的全部经验
    节点断言 `len(set(labels)) == len(labels)`。**这条用例会当场复现 F-2**。
  - **G-12c**（★ J-3b）：遍历 `AXES × AXIS_VALUES` 的全部合法 `(轴, 取值)`，断言凡在多条轴上
    都合法的取值（`none` / `unknown` / `low` / `mid` / `high`）在不同轴上渲染出**不同**的词；
    并逐字断言 `label_of(AXIS_HORIZON, "none") != "无事件"`。**这条会当场复现 R-8**。
- **G-13 · 因果措辞扫描（J-5）**
  扫 `utility.py` 与全部 `experience*.tsx` / `experienceTypes.ts` 的中文字符串，
  禁止出现「贡献」「带来了」「因此赚」「导致盈利」等因果措辞（词表写在用例里，正则须 `\r?\n`）。
- **G-14 · 裁剪预算属性测试（J-6）**
  对 `format_l0` / `format_l1` / `format_counter` / `format_disputed`，用 1000 组
  （随机预算 0…4000 × 随机条目）断言：`len(out) <= max_chars` **恒成立**；
  `out` 为非空时必含框架句的**首尾两端**；不含被半句截断的条目（每条渲染行必须与某个输入条目
  的完整文本前缀相等且以句号收尾）。**这条会当场复现 F-3 与 F-4。**
- **G-15 · 增量性能（规模是本轮的产品承诺之一）**
  50000 条事件的库上，新增 50 条事件后的一次增量 **< 500ms**（贴实测毫秒数）；
  同一库的一次全量重建耗时同时贴出作对照。达不到即回到设计而不是放宽数字。

- **G-16 · `origin` 口径闸的单点化（★ E-2 · J-7 的机械判据）**
  源码扫描 `app/db/ai_experience_repositories.py`：字面量 `AiExperienceEvent.origin ==`
  出现次数必须为 **0**（全部改走 `in_(STAT_ORIGINS)`）；并断言四个读点
  （`load_experience_events` / `load_evidence` / `count_events` / `overview`）的默认口径
  **逐个**等于 `STAT_ORIGINS`。**这条会当场复现 R-2**：不改读侧，W-A 写进去的每一条都读不出来。

- **G-17 · 规模墙只约束全量（★ E-3）**
  造一个事件数 > `materialize_max_events` 的库，且 epoch 状态健康、五条判据全过 ⇒
  `materialize_bounded` 必须返回 `{"ran": True, "mode": "incremental"}`，
  `materialize_health.overflow` 为 **false**；把 `incremental_enabled` 关掉 ⇒ 同一个库上
  返回 `{"ran": False, "reason": "overflow"}`。**这条是「规模墙被推开了」这句产品承诺的
  唯一机械证据**。

### 9.2 逐条验收（A-*）

**W-D**
- **A-1** `most_conflicting(action="close")` 在候选池全为正收益时返回 `None`，`payload` 无
  `experienceCounter` 键（**不是** `None` 值）。
- **A-2** 同上，池中有负收益经验时返回 `|t|` 最大的那条负的；正收益经验永不入选。
- **A-3** `format_counter(budget=120)` 返回**空串**（装不下完整一条），不再返回丢了免责句的残段。
- **A-4** `format_counter(budget=10)` 返回空串，长度 0 ≤ 10（F-3 的「函数超自己预算」不再可达）。
- **A-5** `format_disputed` 放不下两条 ⇒ 空串；标题不再单独出现。
- **A-6** `reflection_max_chars` 设为 1…**207** ⇒ **启动失败**并给出下界；设为 0 或 **≥208**
  ⇒ 启动成功（★ E-6：v1 的 202 / 203 是把 `tail` 少算 1 字、`detail` 少算 4 字、又拿含序号的
  `MIN_ITEM_BUDGET=124` 当 L2 的单条预算算出来的；实测 `FRAME_OVERHEAD["l2"]=88`，
  L2 无序号 ⇒ 单条 120 ⇒ 下限 **208**）。用例必须**同时**断言 `budget_floor("l2") == 208`
  与默认值 240 通过。

**W-E**
- **A-7** 四轴经验只命中两轴 ⇒ `match == 0.5`；两轴经验命中两轴 ⇒ `match == 1.0`
  （F-5 下半的机械判据）。
- **A-8** `applies_in` 边全部 `weight == 1.0` 且 `graph.materialize` 不再读 `events` 求边权
  （源码扫描：`applies_in_edges` 的形参里没有 `events`）。
- **A-9** 穷举「全部桶形 × 全部合法取值 × 六个模板 × 25 字 instId」的 `label` 实算最坏长度
  **== 110** 且 ≤ `_LABEL_MAX`(120)（★ E-16：v1 估的 88 是错的，余量只有 10 字不是 32 字；
  用例必须把 **110** 这个数字写死，不能只断言「< 120」——只有钉死具体值，下一次改模板或加轴时
  它才会红）。附带断言：一个桶签名里非 symbol/bar 的轴**最多 2 条**（桶形分布实测
  `{0: 2, 1: 38, 2: 6}`，v1 说的「三条其他轴的四轴桶」不存在）。
- **A-10** 检索决定性未回归：同图同种子跑 100 次结果序列完全一致（`v1:A-6` 的延续）。

**W-A**
- **A-11** 接管一笔持仓 → 平仓 ⇒ 事件表新增一行 `origin="adopted"`，其 `context` **只含**
  symbol/bar/side/horizon 四轴，且 `hold_seconds` 在合理范围（双时钟未混用）。
- **A-12** 该事件的 `net_bps` 与用 `trip_ledger.settle` 独立算出的值**逐位相等**
  （证明没有另抄一份 bps 换算）。
- **A-13** 入场费不可得 ⇒ `fee_known=False` ⇒ 该事件计入桶的 `excluded` 而**不进** bps 统计。
- **A-14** 离线平仓有回读行 ⇒ 事件入表且 `origin=="adopted"`；**无**回读行（`_mark_offline_gap`
  的早退分支）⇒ `harvest_gap` **加一**且 `reasons.offlineNoReadback` 加一
  （**这条直接复现并关闭 F-6**）。★ E-5：用例必须**按真实调用顺序**构造
  ——`settle_closed_offline` 在 `adopt_position` **之前**跑，合成断面的数据源是
  `contract["position"]`；若把 hook 挂在 `adopt_position` 之后，这条用例会红在「事件入表」那一半上。
- **A-15** `AttributionLedger` 的产物**逐字节未变**：同一批成交下 `aiStats` 的归因块与合入前
  完全相同（证明合成断面没有溢出到真实资金的归因链）。
- **A-16** 降级事件不会造出任何含 `unknown` 轴的经验（J-4 与 W-A 的联合判据）。

**W-B**
- **A-17** 首次物化 ⇒ `mode="full"`、`epoch=1`、累加器表被写满。
- **A-18** 之后追加 100 条事件 ⇒ `mode="incremental"`、`watermarkEventId` 前进、
  只有被碰过的桶的 `updated_ts` 变化。
- **A-19** `TAXONOMY_VERSION` 变更 ⇒ 下一次物化 `mode="full"`、`fullReason="taxonomy"`。
- **A-20** 构造成本分布突变使三分位漂移 >1bps ⇒ `fullReason="cuts_drift"` 并走全量。
- **A-21** 节点数越过 `graph_node_cap` ⇒ `fullReason="node_cap"` 并走全量。
- **A-22** 清空 epoch 状态（模拟手工清库）⇒ `fullReason="no_epoch"` 并走全量。
- **A-23** 批次上限：一次投入 `incremental_batch + 1` 条 ⇒ 本次只吃一批、`pendingEvents=1`、
  脏标志**仍为真**、下一次自动吃完。
- **A-24** `incremental_enabled=False` ⇒ 恒走全量，行为与合入前**逐值相同**（一键回滚可用）。
- **A-25** `medianNetBps` 的增量值与全量值差 ≤ 1bps（J-2 允许的唯一误差，且被钉死）。

**W-C**
- **A-26** 一轮注入 3 条 L0 后开仓、平仓 ⇒ 台账新增 3 行，自然键与事件行**逐字一致**，
  且二者在**同一次 commit**（用例断言中途抛异常时两张表都没有新行）。
- **A-27** 开了仓但进程崩溃（未平仓）⇒ 台账**零行**（不留悬空行）。
- **A-28** 注入 12 次、注入后均值与断言符号相反且 |t|≥2 ⇒ 自动 `muted`、
  `mute_reason="ineffective"`，且该经验**不再**出现在下一次检索的 L0/L1。
- **A-29** 样本增长后不再满足判据 ⇒ 自动解除静音；`mute_reason="user"` 的**绝不**被自动解除。
- **A-30** `pinned=True` 的经验**永不**被自动静音，但效用读数照常产出并展示。
- **A-31** `utility_enabled=False` ⇒ 不写台账、不算、不静音，且 `overview` 无 `utility` 键。
- **A-32** J-1 的产品级判据：跑一遍 W-C 的全流程后，`l0_max` / `l1_max` / 任何 `*_max_chars` /
  任何节点的 `pinned` / `tier` **全部未被自动改动**。

**W-F / 端到端**
- **A-33** 后端产出的 5 组新观测量各自在 UI 上**都有**渲染落点（逐项截图或 DOM 断言）。
- **A-34** 「清理沉降经验」`dryRun` 的条数与真实执行的 `deleted` 一致；置顶与用户静音被跳过。
- **A-35** 详情抽屉的 `medianNetBps` 带「≈」与 tooltip。
- **A-36** 检索预演对同一颗种子给出的三条与真跑决策轮**逐字相同**（预演与真链同源，`v1` 的既有
  承诺在覆盖率归一之后仍成立）。
- **A-37** `v1:G-2` 零回归仍绿：总闸关 ⇒ `build_decision_prompt` 输出**逐字节**等于本特性合入前。

**本轮评审新增（A-38…A-46，逐条对应一条 P0 / P1 修法）**

- **A-38**（E-14）同一颗种子、同一张图，贴出 `match` 归一前后 `len(l0)` / `len(l1)` /
  `len(disputed)` / `len(suppressed)` 四个数的对照；方向必须是「L0/L1 不增、disputed 不减」。
  出现相反方向 ⇒ 归一化写错了，回到设计。
- **A-39**（E-1）一个含 200 条经验的库，跑一次**只碰 3 个桶**的增量步 ⇒ 其余 197 条的
  `muted` **全部保持 false**；再跑一次 `retrieve` ⇒ L0/L1 条数与增量前**逐值相同**。
  **把 `live_keys` 换成 `touched` 这条用例必须变红**（负向断言，写在同一个测试文件里）。
- **A-40**（E-4 / J-2b）一条 `n=30`、`|t|=3.0`、连续同号的经验：在**同一个** `materialize_bounded`
  调用里被 5 个批次各碰一次 ⇒ `sign_streak` 只 **+1**；要升到 `iron` 必须跨**两次不同的** tick
  且每次都有新的同号支撑事件。并断言合入前后同一份数据下 `iron` 条数**不增**。
- **A-41**（E-3）见 G-17：大库 + 健康 epoch ⇒ 增量照跑、`overflow` 为 false。
- **A-42**（E-2）W-A 采到 k 笔之后，`GET /api/experience/overview` 的 `events` **等于**
  真正参与桶枚举的事件数；L0 页眉渲染出的那个整数与它**逐值相同**（`v1:I-9` 的机械形态）。
- **A-43**（E-9）一条只写四条轴的降级事件走完 `materialize` 全程**不抛任何异常**，且
  `enumerate_buckets` 为它产出的桶**全部**只由已知轴构成；把 `_combos_for` 换回 `signature[axis]`
  ⇒ 用例必须红在 `KeyError` 上（负向断言）。
- **A-44**（E-11）构造「id 小的行后提交」的交错（先开两个 session 各插一批、后提交先取 id 的那批），
  ⇒ 下一次增量的 B-5 对账**必须**判 `count_mismatch` 并回全量，且回全量之后
  `COUNT(id<=watermark) == eventCount` 重新成立（自愈）。
- **A-45**（E-13）`utility.refresh` 与 `mark_stale_lessons` 并发时，一条被判 `ineffective` 的经验
  **不会**被改写成 `stale`（反之亦然）；并重跑 G-5 贴出 `/stop` 的实测毫秒数。
- **A-46**（E-8）`label_of(AXIS_HORIZON, "none")` 不再等于「无事件」；一条 `horizon` 桶的整条
  `label` 里不含「事件」二字。

### 9.3 定义完成（DoD）

1. **G-1…G-17（含 G-10b）**全绿，每条贴实测输出；后端红条数与 §9.1 写死的基线
   （3870 passed / 7 failed）逐值一致。
2. **A-1…A-46** 逐条有对应用例且真跑（A-38…A-46 是节点 #1 评审新增的九条）。
3. §8 的净增预算逐文件核对；超出的一律下沉，**不放宽阈值**。
4. `service.py` ≤ 800 行、`session.py` 无新增判断逻辑、每个新模块 ≤ 其上限。
5. 本文档的「实施过程发现的方案缺陷」一节被节点 #2 / #3 逐条回写（以源码为准）。
6. **不 `git commit`**（本节点与节点 #2 的既有约束；由节点 #3 统一合入）。

---

## 十、风险与缓解

| # | 风险 | 缓解 |
|---|---|---|
| **R-1** | 增量算错且无人察觉——本轮**最危险**的失败形态 | G-10 逐值等价门禁 + 四条打回全量判据 + `incremental_enabled` 一键回滚 + `materializeHealth.mode` 在 UI 上恒可见 |
| **R-2** | epoch 状态与累加器表不同步（一个写了另一个没写） | 两者在**同一把物化锁、同一次事务序列**内写；状态里带 `nodeCount`，与实表计数不符即判 `no_epoch` 走全量 |
| **R-3** | J-4 落地当天涌入一批沉降经验，用户以为数据坏了 | 「清理沉降经验」同批交付；概览条一次性提示「本次重建收敛了 N 条语境未知的经验」 |
| **R-4** | `match` 归一改变了排序，用户觉得「Agent 变笨了」 | 检索预演逐字展示打分明细（改动前后可自比）；**A-38** 要求贴前后对照（含 L0/L1/disputed/suppressed 四个条数，★ E-14）；**不加双路径开关** |
| **R-5** | 效用统计被当成因果解读，用户据此手工删掉有用的经验 | J-5 文案纪律 + G-13 扫描 + UI 上那句「这是共现关系，不是因果」；自动化只静音不删除 |
| **R-6** | 自动静音误杀（小样本噪声） | 三重门槛（≥12 次注入 + 符号系统性相反 + \|t\|≥2）；置顶豁免；样本增长后自动解除；一键恢复 |
| **R-7** | 合成开仓断面溢出到 `AttributionLedger`，污染真实资金的归因数字 | 结构性隔离：`adopted.py` 只把 `SettledTrip` 交给 `experience`，**不调** `cost.note_open`；A-15 逐字节断言 |
| **R-8** | 接管事件的双时钟混用让 `hold_seconds` 变成天文数字 | `synth_open_snapshot` 的 docstring 写死「与 `close_trade['ts']` 同一时钟」；A-11 断言范围 |
| **R-9** | 注入台账无界增长 | 与事件表同量级（每笔往返 ≤ `l0_max+l1_max` = 6 行）；`prune` 端点顺带清理孤儿行；A-27 保证无悬空行 |
| **R-10** | 直方图分箱让 median 在 UI 上与用户手算的不一致 | 1 bps 分辨率 + UI 明确标「≈」+ A-25 钉死误差上界；median 不进任何 prompt |
| **R-11** | `service.py` 逼近 800 行，实施期一不小心破阈 | 净增硬上限 +45；超出即下沉到 §8.1 的四个新模块（`importing.py` 是成文先例） |
| **R-12** | 三个总闸互相耦合，回滚时连坐 | 三个总闸独立（`incremental` / `utility` / `adopt_harvest`），各自可单独关；关任一都退回 `v1` 的对应行为 |
| **R-13** | 全量 pytest 因新表共享临时 DB 而互相污染 | 沿用仓库既有的 `services/quant` 测试隔离范式；新表的用例必须自己建自己清；**不得并发跑全量** |
| **R-14** | 新端点未登记 `httpTimeout` ⇒ `/prune` 超时说「失败」而其实已删 | §5.3 明列；G-6④ 的扫描扩展到 `/experience/prune` |

---

## 十一、明确不做（Out of scope）

1. **Agent 可调用的 `expand_experience` 工具**。理由同 `v1:§十一.1`：`decider._stream_decision`
   是「必须且只能调用一次 `submit_trade_decision`」的强制单工具循环，被大量用例冻结。
   本轮用**覆盖率归一 + 三层披露 + 失效静音**从另一侧提高精准度，收益更确定、风险更小。
2. **回测侧读写经验**。同 `v1:§十一.2`。`origin` 列与 `write` 开关继续留着。
3. **向量检索 / embedding 语义相似**。同 `v1:§十一.3`。本轮的 J-3（标签单射）让「同文异签」
   这个最刺眼的重复形态**在结构上消失**，剩下的语义近义合并仍留给后续。
4. **同义经验的自动合并（`generalize` 边）**。这一轮**刻意不做**：合并会**减少**经验条数、
   **改变**它们的支撑集，是一个会让用户「看不懂库里发生了什么」的操作，值得单开一份 spec 并配
   完整的合并预览 / 撤销 UI。本轮先把「效用」这把尺子造出来——**没有尺子就谈不上合并**。
5. **多经验库（book）与团队共享**。同 `v1:§十一.4`。
6. **经验对门禁的任何影响**。`v1:I-1` + 本轮 J-1，是这个特性敢默认开启的根本理由。
7. **注入归因的因果推断（倾向得分 / 反事实）**。J-5 已经把「共现」这条线划死。做因果需要随机化
   注入（即**故意对一部分交易隐瞒经验**），那是在真实资金上做 A/B——不做。
8. **累加器的自动分片 / 归档**。W-B 之后规模墙已被推得很远（增量与库大小近似无关）；真需要时
   `epoch` 字段天然就是分片键，届时单开 spec。
9. **`medianNetBps` 的精确增量**。1 bps 分箱已足够，且它只服务详情抽屉一格。若将来它要进
   prompt，必须先回到精确路径——**近似值绝不允许进 prompt**（`v1:I-9`）。

---

## 十二、实施顺序（节点 #2 的推进次序）

> 每一步都可独立回退：任一步停下，系统的外部行为与合入前的差异都**只是少了后面几步的能力**，
> 不会出现半成品状态。

| 步 | 内容 | 前置 | 预计 | 出口判据 |
|---|---|---|---|---|
| **P0** | W-D 四条修法（含 **E-6** 的 88 / 208）+ G-14 属性测试 | — | 半天 | A-1…A-6、G-14 绿；**这一步单独就值得合入** |
| **P1** | J-3 / J-3b / J-4（`describe` 拼轴名 + **E-8** 的 `(轴,取值)` 词表 + **E-9** 的缺席轴）+ G-12a/b/c | P0 | 半天 | A-9（**110** 逐值）、A-46、G-12 三条全绿；**用例必须先复现 F-2 / R-8 / R-9 再修** |
| **P2** | W-E（E-1 删扫描 + **E-15** 的 support 口径 + E-2 覆盖率归一 + **E-14** 的压制副作用） | P1 | 半天 | A-7、A-8、A-10、**A-38** 绿；贴出同一种子的前后打分**与四个条数**对照 |
| **P2.5** ★ | **E-2 的读侧**：`STAT_ORIGINS` + `EventQuery.origins` + 四个读点 + G-16 | P1 | 半天 | G-16 绿。**必须排在 P3 之前**——先写后读 = W-A 的产物一条也读不出来 |
| **P3** | W-A（`adopted.py` + **E-5 的三处 hook**，注意 `settle_closed_offline` 跑在 `adopt_position` 之前） | P1, **P2.5** | 1 天 | A-11…A-16、**A-42、A-43** 绿；**A-15 必须逐字节** |
| **P4** | W-B 上半：`accumulate.py` + 两张表 + 全量重建顺带写累加器（含 **E-12** 的 `first_ts=min(...)`） | P2 | 1.5 天 | 累加器还原出的 `BucketStats` 与 `compute_stats` 逐值相等（G-10 的一半） |
| **P5** | W-B 下半：`incremental.py` + **五条**判据（含 **E-11** 的 B-5 与 `after_id`）+ **E-10** 的 `eventCount` + **E-1** 的 `live_keys` + **E-3** 的规模闸下沉 + **E-4** 的符号前滚 | P4, P2.5 | **2 天**（v1 估 1.5 天，未含本轮五条修法） | G-10 / G-10b / G-15 / G-17 全绿、A-17…A-25 与 **A-39、A-40、A-41、A-44** 绿 |
| **P6** | W-C（台账表 + `utility.py` + 自动静音 + **E-13** 的锁作用域上提） | P3, P5 | 1.5 天 | A-26…A-32、**A-45** 绿、G-11 绿、**G-5 重跑贴数** |
| **P7** | W-F 前端全部 + `httpTimeout` 登记（含 R-19 的 `health.utilityMuted`） | P6 | 1.5 天 | A-33…A-36 绿、G-3/G-4 绿、净增预算逐文件核对 |
| **P8** | 全量门禁 + 回写「实施过程发现的方案缺陷」 | 全部 | 半天 | §9.3 的 6 条 DoD |

**工期吃紧时的取舍**：优先砍 **P6（W-C 效用闭环）**——它是本轮唯一引入新产品概念的子系统，
砍掉之后 P0…P5 仍然独立成立（说真话 + 算得准 + 采得全 + 算得动）。
**P0 与 P1 不可砍**：它们修的是产品今天正在说的假话。
★ **P2.5 不可砍、也不可与 P3 合并**：它是一条**纯读侧**的口径修法，独立成立且零风险；
把它砍掉或塞进 P3 一起做，最可能的结果是 P3 写完、测试只测了写侧、而读侧的四个点漏掉一两个
——那正是 `v1:P0-1`（漏一个再导出 ⇒ 整条特性静默空转）的复发形态。
★ **P5 若做不完，正确的停法是「停在 P4」**：累加器写了、增量不接，系统行为与合入前逐值相同
（`incremental_enabled=False` 的效果），**绝不允许**停在「增量接了但 G-10 还没绿」的状态——
那是 R-1 那场灾难唯一能溜进生产的路。

**节点 #3 终审必须逐个回答的**五**个问题**（沿用 `v1:§十二 P7` 的姿态；前三条是 v1 的，
后两条是本轮评审的 P0 逼出来的）：

1. **增量与全量真的等价吗？** —— 不看文档、不看报告，自己跑一次 G-10 **与 G-10b** 并贴输出。
2. **每一个新增的观测量都有渲染落点吗？** —— 逐个数：后端 7 组新键，UI 上指出各自在哪一行。
3. **本轮有没有任何一条自动化让经验层说得更多？** —— 逐个模块回答 J-1，跑一次 G-11，
   **并且单独回答 `iron` 条数有没有因为 W-B 而变多**（A-40）。
4. **W-A 采到的那些往返，真的进桶了吗？** —— 不看 `harvest.adopted` 这个数字，
   直接查一次 `ai_experience_events` 里 `origin='adopted'` 的行数，再查一次它们贡献到了哪几个桶；
   两者对不上就是 R-2 没修干净。
5. **跑一次只碰三个桶的增量，然后立刻 `retrieve` 一次** —— L0/L1 条数必须与增量前相同。
   这是 R-1 那场灾难的十秒钟复现法，比读一百行代码都快。

---

## 附录 A · v1 的取证探针（**节点 #1 无法原样重跑**，见 R-21）

> ★ **R-21（P2，留档）**：下表三支探针只有骨架描述、脚本本体「位于会话临时目录」，节点 #1 因此
> **无法复核它们的输出**——本轮改为对同一批结论独立重写探针（附录 B，全文落盘）。三条结论经
> 独立复核**全部属实**。后续轮次请沿用「探针全文进文档」这条做法：一个跑不了的探针，与一句
> 没有取证的断言等价。

三个探针位于会话临时目录，节点 #1 若要复核，可按下述骨架在
`services/quant` 目录下用 `.venv/Scripts/python.exe` 重跑（**须带 `PYTHONIOENCODING=utf-8`**，
否则 Windows 控制台的 GBK 会在中文输出上抛 `UnicodeEncodeError`）：

| 探针 | 验证 | 关键输出 |
|---|---|---|
| 1 | `applies_in` 权重集合 / `unknown` 桶的标签冲突 | `APPLIES_IN distinct weights: [1.0]`；6 组标签各被 ≥2 条经验共享 |
| 2 | 端到端 `materialize → Graph → retrieve` | `DUPLICATE RENDERED LINES IN ONE PROMPT: 1`；三条 4 轴经验 `match` 全为 4.0 |
| 3 | L2 的四处文案 / 预算行为 | 正收益经验被挂在「最相冲突」标题下；`budget=20 ⇒ len=22`；`budget=90` 的争议段只剩一条 |

---

## 附录 B · 本轮评审探针（节点 #2 / #3 可原样重跑）

保存为 `services/quant/probe_review.py`，在 `services/quant` 目录下执行：

```
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -u probe_review.py
```

**必须带 `PYTHONIOENCODING=utf-8`**（Windows 控制台默认 GBK，中文输出会抛 `UnicodeEncodeError`）；
**必须带 `-u`**（否则长输出会被缓冲吞掉，这是本仓库的成文教训）。

八段探针与它们各自钉住的结论：

| 段 | 验证 | 关键输出 | 对应 |
|---|---|---|---|
| P1 | `Settings` 里 `ai_trade_experience_*` 的真实项数 | `36` | R-17 |
| P2 | L2 三句框架句的**实测字符数** | `head 21 / tail 39 / detail 26 ⇒ 88`；`floor 208` | R-6 |
| P3 | 既有 `format_counter` 的超预算行为 | `budget=21 -> len=22 over_budget=True` | F-3 复核 |
| P4 | 拼轴名后的 `describe` 单射性、`(轴,取值)` 词表缺口、桶形与最坏 label 长度 | `方向·做多` 撞名；`horizon:none -> 无事件`；`{0:2,1:38,2:6}`；`worst=110` | R-7 / R-8 / R-16 |
| P5 | 缺席轴下的 `_combos_for` | `KeyError: 'regime'` | R-9 |
| P6 | 符号前滚的路径依赖 | `full streak=1 tier=normal` vs `7-batch streak=7 tier=iron` | R-4 |
| P7 | `first_ts` 的顺序依赖（源码判读） | — | R-12 |
| P8 | NPMI 的全局分母 | `total = len(events)` | R-10 |

```python
"""Node #1 review probes for docs/plans/ai-trade-experience-compounding/spec.md.

Run from services/quant with .venv/Scripts/python.exe and PYTHONIOENCODING=utf-8.
Verifies, against real source, the numbers the spec asserts and the numbers the
spec's own fixes would need.
"""
import itertools
import sys

sys.path.insert(0, ".")

from app.ai_auto.experience import aggregate as agg          # noqa: E402
from app.ai_auto.experience import disclosure as dis          # noqa: E402
from app.ai_auto.experience import promote as pro             # noqa: E402
from app.ai_auto.experience import taxonomy as tax            # noqa: E402

print("=" * 72)
print("[P1] settings count")
from app.config import Settings                               # noqa: E402
names = [n for n in Settings.model_fields if n.startswith("ai_trade_experience_")]
print("  ai_trade_experience_* field count =", len(names))
print("  reflection_max_chars field =", Settings.model_fields[
    "ai_trade_experience_reflection_max_chars"])

print("=" * 72)
print("[P2] L2 frame sentence real lengths (spec claims FRAME_OVERHEAD['l2']=83)")
head = "【交易经验 · 与本次提议最相冲突的一条】"
tail = ("这是统计结论而非否决指令：若当前"
        "证据足以推翻它，请在复核理由中"
        "指名推翻的依据。")
# detail worst case: support 7 digits, tStat signed with one decimal
detail = "（样本 {n} 笔，t={t}）".format(n=9999999, t="-123456.7")
print("  len(head)   =", len(head))
print("  len(tail)   =", len(tail))
print("  len(detail worst) =", len(detail))
frame = len(head) + len(tail) + len(detail) + 2
print("  real L2 frame overhead = head+tail+detail+2 newlines =", frame, " (spec says 83)")
print("  MIN_ITEM_BUDGET =", dis.MIN_ITEM_BUDGET)
print("  budget_floor()==FRAME+MIN_ITEM_BUDGET so l2 floor would be",
      frame + dis.MIN_ITEM_BUDGET, " (spec says 203)")
print("  default reflection_max_chars = 240 -> passes?",
      240 >= frame + dis.MIN_ITEM_BUDGET)
print("  L2 has no '1. ' index prefix, so item budget really needs only 120 ->",
      frame + 120)

print("=" * 72)
print("[P3] existing format_counter over-budget behaviour (F-3)")
lesson = {"text": "BTC-USDT-SWAP 15m · 方向·做多：30 笔中亏 20 笔。",
          "support": 30, "tStat": 3.2}
for budget in (240, 120, 60, 22, 21, 20, 10, 1):
    out = dis.format_counter(lesson, budget)
    print("  budget=%4d -> len=%3d  over_budget=%s  has_tail=%s"
          % (budget, len(out), len(out) > budget, tail[:6] in out))

print("=" * 72)
print("[P4] proposed describe() with axis names: injectivity + worst-case label length")


def describe_v2(signature):
    parts = ["%s·%s" % (tax.AXIS_LABEL_ZH.get(a, a), tax.label_of(a, signature[a]))
             for a in tax.AXES
             if a in signature and a not in (tax.AXIS_SYMBOL, tax.AXIS_BAR)]
    return "+".join(parts) if parts else "全局"


sig_a = {tax.AXIS_SYMBOL: "BTC-USDT-SWAP", tax.AXIS_BAR: "15m", tax.AXIS_SIDE: "long"}
sig_b = {tax.AXIS_SYMBOL: "ETH-USDT-SWAP", tax.AXIS_BAR: "1H", tax.AXIS_SIDE: "long"}
print("  describe_v2(BTC/15m/long) =", describe_v2(sig_a))
print("  describe_v2(ETH/1H /long) =", describe_v2(sig_b))
print("  -> identical for two DIFFERENT signatures?",
      describe_v2(sig_a) == describe_v2(sig_b), " => G-12 as worded FAILS")

print("  horizon='none' renders as:", tax.label_of(tax.AXIS_HORIZON, "none"),
      " (VALUE_LABEL_ZH is value-keyed, not (axis,value)-keyed)")
print("  event  ='none' renders as:", tax.label_of(tax.AXIS_EVENT, "none"))

# real bucket shapes: how many non-symbol/bar axes can a bucket signature have?
full = {a: (tax.AXIS_VALUES.get(a) or ("BTC-USDT-SWAP",))[0] for a in tax.AXES}
full[tax.AXIS_SYMBOL] = "BTC-USDT-SWAP"
full[tax.AXIS_BAR] = "15m"
widths = {}
for combo in agg._combos_for(full):
    others = [a for a in combo if a not in (tax.AXIS_SYMBOL, tax.AXIS_BAR)]
    widths[len(others)] = widths.get(len(others), 0) + 1
print("  bucket shapes by #non-symbol/bar axes:", widths,
      " (spec's 'four-axis bucket with three such axes' does not exist)")

# worst-case rendered label under describe_v2
long_symbol = "1000000BABYDOGE-USDT-SWAP"
worst = 0
worst_text = ""
for combo_axes in {tuple(sorted(c)) for c in map(lambda d: tuple(d.keys()),
                                                 agg._combos_for(full))}:
    axis_choices = []
    for a in combo_axes:
        if a == tax.AXIS_SYMBOL:
            axis_choices.append([long_symbol])
        elif a == tax.AXIS_BAR:
            axis_choices.append(["15m"])
        else:
            axis_choices.append(list(tax.AXIS_VALUES.get(a) or ("x",)))
    for values in itertools.product(*axis_choices):
        sig = dict(zip(combo_axes, values))
        bucket = agg.Bucket(signature=sig,
                            scope="symbol" if tax.AXIS_SYMBOL in sig else "global",
                            event_ids=tuple(range(30)))
        for template in (agg.TEMPLATE_LOSS, agg.TEMPLATE_WIN, agg.TEMPLATE_EXIT,
                         agg.TEMPLATE_EDGE_ERROR, agg.TEMPLATE_TURNOVER,
                         agg.TEMPLATE_CONFIDENCE):
            st = agg.BucketStats(n=9999999, wins=1234567, winRate=0.9999,
                                 meanNetBps=-99999.0, medianNetBps=-1.0,
                                 stdNetBps=1.0, tStat=-123456.7, meanHoldS=123456789.0,
                                 meanCostBps=99999.0, costShare=9.9999,
                                 meanEdgeErrorBps=-99999.0, template=template)
            prefix = "%s %s · %s：" % (sig.get(tax.AXIS_SYMBOL) or "全标的",
                                                sig.get(tax.AXIS_BAR) or "",
                                                describe_v2(sig))
            text = prefix + agg._TEMPLATES[template](bucket, st)
            if len(text) > worst:
                worst = len(text)
                worst_text = text
print("  worst rendered label length under describe_v2 =", worst, "(_LABEL_MAX =", agg._LABEL_MAX, ")")
print("  worst text:", worst_text)
print("  -> exceeds 120 and would be mid-sentence truncated by render_label?", worst > agg._LABEL_MAX)

print("=" * 72)
print("[P5] _combos_for with a DEGRADED signature (W-A writes only 4 axes)")
degraded = {tax.AXIS_SYMBOL: "BTC-USDT-SWAP", tax.AXIS_BAR: "15m",
            tax.AXIS_SIDE: "long", tax.AXIS_HORIZON: "none"}
try:
    agg._combos_for(degraded)
    print("  no exception")
except KeyError as exc:
    print("  KeyError:", exc, " <= W-A events crash enumerate_buckets unless J-4 handles MISSING keys")

print("=" * 72)
print("[P6] sign_streak / tier path dependence (breaks J-2 'incremental == full')")
mean_by_batch = [10.0] * 7
# full: one materialisation over all events
roll_full = pro.roll_sign(0, 0, 10.0)
# incremental: seven materialisations, one per batch
sign, streak = 0, 0
for m in mean_by_batch:
    r = pro.roll_sign(sign, streak, m)
    sign, streak = r.sign, r.streak
print("  full      -> prev_sign=%d sign_streak=%d" % (roll_full.sign, roll_full.streak))
print("  7-batch   -> prev_sign=%d sign_streak=%d" % (sign, streak))
rule = pro.PromoteRule()
stats = {"n": 30, "tStat": 3.0}
print("  tier(full)      =", pro.resolve_tier(stats, rule, sign_streak=roll_full.streak))
print("  tier(7-batch)   =", pro.resolve_tier(stats, rule, sign_streak=streak))
print("  -> J-2 'tier / prevSign / signStreak value-equal' is unreachable by construction")

print("=" * 72)
print("[P7] first_ts preservation is order dependent")
print("  _lesson_node keeps old.first_ts when truthy; incremental batch order != ts order")
print("  => first_ts(incremental) = min(batch1) but first_ts(full) = min(all)")

print("=" * 72)
print("[P8] NPMI needs a global event total that the spec's epoch state does not carry")
import inspect                                                # noqa: E402
from app.ai_auto.experience import graph as gr                # noqa: E402
src = inspect.getsource(gr._co_occurs_edges)
print("  _co_occurs_edges uses:", [ln.strip() for ln in src.splitlines()
                                   if "total" in ln][:3])
print("=" * 72)
print("PROBES DONE")
```

---

## 评审结论

**有条件通过。**

v1 的诊断部分是**扎实的**：九条缺陷 F-1…F-9 逐条复现属实，取证方式（实跑探针、逐字贴输出）
与定级都站得住；「说真话 / 算得准 / 采得全 / 算得动 / 自证 / 管理面」这六条工作流的**切分方式**
是对的，J-1 那条「本轮新增的每一条自动化都只能让经验层说得更少」的单侧安全论证尤其可贵——
它是这一轮敢默认开启的根本理由。

问题出在**从诊断到修法**的这一段：v1 对**它自己要改的那些函数的调用环境**读得不够。五条 P0
里有四条是同一种形态——**修法本身是对的，但它落地的那个位置不是它以为的那个位置**：

- `mark_stale_lessons` 的语义依赖「`seen_keys` 是全集」，增量传的是增量（R-1）；
- `origin` 是一个**双向**口径闸，v1 只改了写的那一半（R-2）；
- 规模闸在 `_run_materialize` **之前**，而增量住在它**里面**（R-3）；
- `settle_closed_offline` 跑在 `adopt_position` **之前**（R-5）。

第五条 P0（R-4）性质不同，也更值得记下来：**W-B 顶穿了 J-1**。把「一次物化」切成「七个批次」
之后，`iron` 的第三道门槛从「连续两次重算同号」变成「连续两个批次同号」——一条**自动升档**路径，
而 J-1 明令禁止。一份文档的压舱石被同一份文档的另一条工作流顶穿而全文没有察觉，说明
**不变式没有被逐条拿去检验每一条新增逻辑**。J-2b 与 A-40 就是补这个动作。

以上 15 条 P0 / P1 已在正文逐条落修（E-1…E-16），P2 六条中四条落修、两条留档。文档由 **v1** 修订
为 **v2**。**放行，但节点 #2 必须逐条兑现下列七条条件**：

1. **P2.5（`STAT_ORIGINS` 读侧）必须先于 P3 单独完成并单独绿 G-16。** 先写后读是 `v1:P0-1` 的
   复发形态，而这一次它会伪装成「已补采 N 笔」的正面数字。
2. **G-10 必须把 `muted` / `mute_reason` 纳入逐值比对。** 不比这两个，R-1 那场「一个 tick 静音
   整库」的灾难可以完整地通过本轮最重要的门禁。
3. **G-10b 必须与 G-10 同时绿。** 两条的分工是「累加器算错了」与「批次切法不同」的唯一分离判据；
   只留 G-10 会逼着实现方去放宽门禁。
4. **A-40 必须给出 `iron` 条数的前后对照，且方向只能是不增。** 这是 J-1 在 W-B 上唯一可机械
   判定的形态；G-11 的源码扫描证不了它。
5. **A-9 必须把 110 这个数字写死**（不是「< 120」），并同时断言「非 symbol/bar 轴最多 2 条」。
   余量只剩 10 字，靠一句「小于 120」守不住下一次加轴。
6. **E-13 的锁作用域上提是本轮唯一触碰锁语义的改动**，必须满足「不新增第二把锁、不新增后台
   任务」，并**重跑 G-5 贴出 `/stop` 的实测毫秒数**。做不到就退回「refresh 跑在锁外并接受最终
   一致」，并把这个取舍写进正文——`v1` 在锁上踩过两个 P0，这里不接受推理代替实测。
7. **停法必须遵守 §十二 的两条**：P5 做不完就停在 P4（行为与合入前逐值相同），
   **绝不允许**停在「增量接了但 G-10 未绿」的状态。

> 另有两条留档的 P2，不阻塞放行，但请节点 #2 在实施中顺手确认：
> ① `_cast_setting` 的 `budget_floor("l0" if "l0" in field else "l1")` 是一处**按字段名猜层**的
> 分支，本轮不受影响，但必须补上那行边界注释（§4.2 已写明）；
> ② 附录 A 的三支 v1 探针「位于会话临时目录」，无法复核——本轮的八段探针已按附录 B 全文落盘，
> 后续轮次请沿用「探针进文档」这条做法。

> 设计人：Anthropic 工程团队 · 解决方案架构（迭代 2 / 节点 #0）
> 设计方式：逐节实读 `8e05dd9` 的经验层全部源码 + `services/quant/.venv` 内三个可执行探针；
> 不采信迭代 1 任何文档的取证结论，凡与源码不符**以源码为准**
> 文档版本：**v2**（v1 = 节点 #0 首版；v2 = 节点 #1 设计评审后的修订版）·
> 上一轮：`docs/plans/ai-trade-experience-context-graph/spec.md` v4
> 评审人：Anthropic 工程团队 · 资深评审（迭代 2 / 节点 #1）
> 评审方式：逐节实读 `8e05dd9` 的经验层与数据层全部源码 + `services/quant/.venv` 内八段可执行探针
> （附录 B），**不采信 v1 的任何取证结论**；凡与源码不符**以源码为准**
> 下一站：节点 #2 开发实现

---

## 实施过程发现的方案缺陷

> 节点 #2（开发实现）逐条回写。口径与正文一致：**P0** = 让本轮某条交付静默失效或损坏真实资金
> 路径；**P1** = 让产品说假话 / 让一条承诺静默不兑现 / 让一条门禁必然误红；**P2** = 卫生与一致性。
> 凡与源码不符**以源码为准**——下表每一条都附实测取证或落修位置。

| # | 级别 | 一句话 | 处置 |
|---|---|---|---|
| **I-1** | **P0** | `live_keys` 的命名空间被 spec 写错了：`mark_stale_lessons` / `unmute_reappeared` 收的是**经验节点 key**（`lesson:<hex>`），而 §4.5.3 第 7 步说的「本 epoch 累加器表里 n>0 的全部桶」是**桶签名 key** —— 两者不是一个命名空间，照抄会让**没有一条能匹配上**，后果与 E-1 那场「一个 tick 静音整库」逐字相同 | 改传 `graph_plan.seen_lesson_keys`（与全量路径逐字同一个集合）；`incremental.step` 里逐条写明两个坑；A-39 是它的十秒复现法 |
| **I-2** | **P1** | `_fold_batch` 只把**本批**事件折进 `costHist`，历史直方图丢了 ⇒ 判据 B-2 每次都拿「只有最近 2 万条」的分布去和冻结分位比，**分位看起来永远没漂** ⇒ 那道判据成了摆设 | 底座聚合里传 `costHist`，逐条合并；A-20 断言真的会打回全量 |
| **I-3** | P1 | **A-9 的 110 是在旧 `describe` 上算的**：本轮 J-3 给每条轴补了轴名前缀，最坏语境短语从 13 字变成 14 字 ⇒ 实算最坏 label 长度是 **111**，不是 110。桶形分布实测也是 `{0:2, 1:36, 2:6}`（44 个桶形）而不是 `{0:2, 1:38, 2:6}`（46 个）——spec 的探针没有对「三阶的 `{symbol,bar,exit}`」与「二阶的同签名桶」去重 | A-9 把 **111** 与「非 symbol/bar 轴最多 2 条」逐值钉死（余量 9 字）；探针可原样重跑 |
| **I-4** | P1 | `EventQuery.after_id` 的语义是 `id > after_id`，而 B-5 对账要的是 `COUNT(id <= watermark)`。两处同名反向会造出一条**只在并发下现形**的错误 | `count_events` 的形参改名 `up_to_id` 并在 docstring 里写明「刻意不叫 after_id」 |
| **I-5** | P1 | `utility.refresh` 走 `patch_node` 自动静音会把 `mute_reason` 写死成 `"user"` —— 那是「人类按下了静音」的标记，也是 `unmute_reappeared` 判断「不许自动改写」的唯一依据。走它等于把每一次自动静音都**伪装成用户意志**，此后再也解除不掉，而且不报任何错 | 数据层新增 `set_lesson_mute(muted, reason)`（自动化专用，且 `mute_reason == "user"` 的行一律不动）；用例 `test_user_mute_is_never_touched_by_the_automation` 正反两向 |
| **I-6** | P1 | 经验层的三处 hook 直接写 `member.session.experience.…`，而**续跑是一次开仓授权**：一个统计增强读数抛出的 `AttributionError` 会把一次合法的续跑挡下来（既有 4 条续跑用例当场红） | `resume._experience(member)` 取门面，未装配 ⇒ `None` ⇒ 空转，并把「fail-closed 只管取数、不管旁路观测」这条极性写进 docstring |
| **I-7** | P1 | `up_to_date`（水位线之后没有新事件）时 `step()` 会对空的 `planned["rows"]` 求 `max(...)` ⇒ `ValueError`。手动重建走的正是这条路（它要的是「按新阈值重新推导一遍图」，不是「吃新事件」） | `max(..., default=0)`，并在注释里写明空批合法 |
| **I-8** | P1 | **规模闸下沉之后，「终局物化」的语义变了**：水位线之后没有新事件时 `force=True` 不再跑一次全库重建，而是回 `up_to_date` 并清脏标志。既有用例 `test_debounce_only_skips_when_not_forced` 断言的是旧行为 | 用例跟到新契约上，并补断言「确认没有待处理事件之后必须把脏标志清掉，否则每 60 秒白问一次」 |
| **I-9** | P1 | G-13 若按**原文**扫，会把 `utility.py` docstring 里那句「绝不使用「贡献」「带来」这类因果措辞」判成违规；同理会把前端用例里的**负向断言**（`.not.toMatch(/贡献\|…/)`）判成违规。一条会把自己的理由与护栏判成违规的门禁，最终只会被人放宽 | G-13 改为先去 docstring / 注释（`.py` 走 AST，`.tsx` 去 `//` 与 `/* */` 与 `{/* */}`），并排除 `*.test.tsx` |
| **I-10** | P1 | G-11 若按「凡出现 `pinned` 这个名字就红」判，会把 `prune_stale` 里那个数「跳过了几条置顶」的**局部计数器**判成违规——而它恰恰是在**保护**用户意志 | G-11 只判**属性赋值**与**写入 payload 的字面量**两种真正会改状态的形态；那个局部量顺手改名 `skipped_pinned` |
| **I-11** | P2 | G-10 的协议「跑一次 `rebuild()` 得 `S_full`；清空后按 7 个随机批次逐批增量」在两处不可直接落地：① 逐批物化会正当地留下一批「模板还没定下来」的旧 key（模板进 `lesson_key` 的哈希），**一串全量重建同样会留下它们**；② 等价的前提是**同一份成本分位**，而 `rebuild()` 每次都按当前全集现算 | ① 判据改成「活着的那些逐值相等 + 其余每一条都必须是 `stale`」（比忽略它们更强）；② `materialize()` 增一个**可选** `cuts` 形参，让预言机能用与增量同一份分位跑 —— `costRegime` 决定桶身份，分位不同就不是在比同一批桶 |
| **I-12** | P2 | 净增预算表有 9 项超出（见下方「预算核对」）。**CLAUDE.md 的硬阈值全部成立**：无文件 > 800 行、无方法 > 50 行、无形参 > 5 个（三处越界已就地收敛：`_fold_batch` 拆成 `_fold_one`、`materialize_from_accumulators` 收进 `AccumulatorPlan`、`seed_from_full` 收进 `outcome`） | 逐项列出实测值与超出原因；`service.py` 797 / 800 是本轮唯一逼近硬阈的文件，为此把「物化健康读数」整块下沉进 `incremental.py`（`importing.py` 的成文先例） |
| **I-13** | P2 | G-15 的 500ms 在本机首测是 **521ms**，而其中绝大部分是 SQLite 的**提交延迟**而不是计算 | 去掉两处可避免的写成本（累加器与语境计数合成一次提交；语境计数只写本批真的变过的行）⇒ 实测 **287–320ms**，同一库全量 31817–35634ms（**110 倍**）。未放宽数字 |

### 预算核对（§8.2 / §7.4 的净增预算 vs 实测）

> 超出的一律是**文档密度**而不是逻辑：CLAUDE.md 要求「所有公开 API 必须写 docstring」且
> 「复杂业务规则与踩坑记录必须写清原因与可回退条件」，而本轮几乎每一处修法都对应一条
> 「不写下来下一个人一定会踩回去」的取证。**硬阈值一条没破**。

| 文件 | 预算 | 实测 | 说明 |
|---|---|---|---|
| `experience/accumulate.py` | ≤320 | **319** | ✅ |
| `experience/incremental.py` | ≤300 | **536** | 承接了 spec 未预见的两块：`run_locked`（E-13 的锁作用域上提）与「物化健康读数」整块（为把 `service.py` 压回 800 以内） |
| `experience/utility.py` | ≤200 | **287** | 多出 `list_view` / `prune_stale` / `overview_wire` / `by_keys` / `detail` 五个读侧编排——它们本该落在 `service.py`，但那里只剩 3 行余量 |
| `experience/adopted.py` | ≤160 | **165** | ✅（+5，`AdoptedOpen` 聚合形参） |
| `db/ai_experience_accum_repositories.py` | ≤420 | **252** | ✅ |
| `experience/aggregate.py` | +45 | **+43** | ✅ |
| `experience/taxonomy.py` | +20 | **+46** | `AXIS_VALUE_LABEL_ZH` 与 `TAXONOMY_VERSION` 各自需要一段「为什么」 |
| `experience/graph.py` | +70 | **+107** | 多出 `ContextCounts` / `AccumulatorPlan` 两个聚合与 `_assemble` 的共用装配 |
| `experience/retrieval.py` | +25 | **+28** | ✅（近似） |
| `experience/disclosure.py` | +40 | **+28** | ✅ |
| `experience/runtime.py` | +85 | **+205** | W-A 的三个方法 + W-C 的两个 + `HARVEST_REASONS` 闭合词表。文件 632 行，距 800 尚远 |
| `experience/service.py` | **+45（硬上限）** | **+57，文件 797** | 硬阈是文件 ≤800：为此把健康读数整块下沉。**797 / 800** |
| `ai_auto/resume.py` | +18 | **+61** | 多出 `_experience()`（I-6）与 `_adopted_open()`（合成断面的装配下沉，session.py 不许有判断逻辑） |
| `ai_auto/session.py` | +0～3 | **+0** | ✅ 一行未改 |
| `api/experience.py` | +75 | **+61** | ✅ |
| `db/ai_experience_repositories.py` | +45 | **+88** | 多出 `set_lesson_mute`（I-5）与 `append_experience_events` 的 `commit` 形参（A-26 的同一次提交） |
| `ExperienceSection.tsx` | +90 | **+86** | ✅ |
| `ExperienceList.tsx` | +40 | **+21** | ✅ |
| `ExperienceDetailDrawer.tsx` | +70 | **+89** | 「注入后表现」区内联在此（§7.4 只批了两个新组件，故不另开文件） |
| `ExperienceToolbar.tsx` | +45 | **+36** | ✅ |
| `experienceTypes.ts` / `experienceApi.ts` | +60 / +40 | **+135 / +24** | 类型侧多出 7 个新 wire 结构，每个都带「它为什么不是那个意思」的注释 |
| `aiTradeStats/ExperienceCard.tsx` | +30 | **+21** | ✅ |
| `Agent/AiTradeExperienceFields.tsx` | +18 | **+20** | ✅（近似） |
| **新建** `ExperienceUtilityPanel.tsx` | ≤180 | **115** | ✅ |
| **新建** `ExperiencePrunePrompt.tsx` | ≤120 | **51** | ✅ |
| `lib/experience.ts` | 未在表内 | **+62** | `HARVEST_REASON_ZH` / `FULL_REASON_ZH` / `utilityCellText` 三个渲染判据的自然归属 |

### 门禁实测（节点 #2 本机，非采信上游报告）

| 门禁 | 基线（§9.1 写死） | 本轮实测 |
|---|---|---|
| `ruff check services/quant`（`app` + `tests`） | 0 | **0** |
| `tsc --noEmit` | 0 | **0** |
| 前端 `NODE_ENV=test vitest run` | 180 文件 / 2094 用例全绿 | **183 文件 / 2117 用例全绿**（新增 3 文件 / 23 用例） |
| 后端全量 `pytest -q -p no:randomly -u` | 3870 passed / **7 failed** | **3914 passed / 7 failed / 4 skipped**（新增 44 条通过；7 条红**逐条同基线**，全部来自未跟踪的 `test_factor_library_expansion.py`——`git ls-files --error-unmatch` 回 `did not match any file(s) known to git`，属另一未完成特性 `factor-library-crypto-expansion`） |
| **G-15**（50000 条事件的库 + 50 条新事件） | < 500ms | **287–320ms**；同一库一次全量 **31817–35634ms**（**约 110 倍**） |
| CLAUDE.md 硬阈值 | 文件 ≤800 / 方法 ≤50 / 形参 ≤5 | 全部成立（`service.py` **797** 是唯一逼近的） |

### 三条**判定为不改**的复核结论

1. **`describe` 不再丢弃 `unknown` 轴**（J-3 的实现）看似与 J-4「`unknown` 不进桶身份」重复。
   两者不是一回事：J-4 管**桶枚举**（统计经验因此不可能带 `unknown`），而 `describe` 还要服务
   **手工 / 导入**经验——`sanitize_lesson` 的闭合词表里 `unknown` 是合法取值。丢掉它会让一条
   用户写的「体制未知时做多」渲染成「做多」，那是一句范围被放大的话。
2. **离线平仓的 `exitSource="offline"` 折成 `unknown`**（它不在 `EXIT_SOURCES` 里）。不改
   `exit_attribution` 的闭合词表：那是一个被全仓共用的单点打标现场，为经验层一个降级路径去动它
   会造出第二个分类口径。渲染成「退出·来源未知」是**诚实的**——我们确实不知道交易所是按什么
   平的。
3. **`materialize_health.events` 仍是「本次处理的条数」**（增量下就是批次大小），不改成库存
   总量。概览条那一行的语义是「上次重建吃了多少」，与 `overview.events`（库存）**刻意不同**；
   把两者合成一个数会让「待处理 N 条」这句话失去参照。

---

## 终审（节点 #3）· 逐条复核、直接修复与门禁实测

> 口径与上一节一致（**P0** = 让本轮某条交付静默失效或损坏真实资金路径；**P1** = 让产品说假话 /
> 让一条承诺静默不兑现；**P2** = 卫生与一致性）。**不采信节点 #2 的任何自报结论**：下表每一条
> 都在工作树里实读源码或实跑用例复核过，四道门禁全部本机重跑。发现的问题**全部就地修掉**，
> 不留 TODO；判定为不修的那一条在 T-7 里写清理由。

| # | 级别 | 一句话 | 处置 |
|---|---|---|---|
| **T-1** | **P0** | `adopted.synth_from_contract` 读的入场价列名是 `avgPx` / `entryPrice`，而 `members_json` 里那一行由 `session._state_position_snapshot()` 与 `session._exchange_position()` 写出，**两处写的都是 `avgEntry`**（`avgPx` 只是交易所归一行的原始列名，落库那一步就换掉了）。读错列名**不抛异常**，只让该函数恒回 `None` ⇒ 建不出合成断面 ⇒ `note_offline_close` 里 `_adopted_snaps` 为空 ⇒ `_settle_adopted` 直接回 `None` ⇒ 那条往返照旧只记一个 `noTrip` 缺口。**W-A「离线平仓补采」这一半在生产上一条事件都产不出来**，而 `tsc` / `ruff` / 全量用例全绿——既有 A-11 / A-12 / A-14 三条用例的 `_CONTRACT` 用的是一份**生产上不存在**的形状（`avgPx` / `posSide`），它们守的是一个虚构的契约 | `avgEntry` 优先，`avgPx` / `entryPrice` 保留为回落（调用方直接把交易所归一行喂进来时同样可用），并把「列名是 `avgEntry` 不是 `avgPx`」写进 docstring；补两条按**真实** `members_json` 形状写的用例（列名一条 + 端到端一条），去掉 fix 即红 |
| **T-2** | **P1** | `materialize_bounded` 为了让规模闸看得见 `mode`，把 `incremental.plan()` 跑在物化锁**外**；而 `POST /api/experience/rebuild`（「强制全量」）**不受** `_MATERIALIZE_BUSY` 那个进程级单槽约束，完全可能在这中间抢到锁、`epoch+1` 并 `purge_experience_epochs` 掉旧 epoch 的累加器。此后 `step()` 会**在一个刚被清空的底座上折进本批** ⇒ 整张图被一批只含最后一批事件的节点替换、其余全部被 `mark_stale_lessons` 判为沉降，而且一行日志都没有。判据 B-5 会在下一个 tick 自愈，但那之间图是错的——**这正是 R-1「增量算错且无人察觉」的形态**，只不过触发它的不是算错，是一份过期的计划 | `incremental.run_locked` 进临界区后先跑 `_revalidate`：按 `(epoch, watermarkEventId, eventCount)` 三元组重新对一次表，不一致就**在锁内**重新 `plan()`。新增用例 `test_a_stale_plan_from_outside_the_lock_never_rebases_the_epoch`，去掉 `_revalidate` 即红（实测 `assert 1 == 2`：epoch 被拨回了已被 purge 的旧版本） |
| **T-3** | P1 | `aggregate.render_label` 的 docstring 写「实算最坏长度是 **110**，余量 10 字」，而 A-9 逐值钉死的是 **111**——I-3 自己已经订正过这个数，落码时漏改了注释。一句与用例互相矛盾的注释比没有注释更坏：下一个人改模板时会照着 110 去算余量 | 改成 111 / 余量 9 字，并写明这 1 个字是 J-3 的轴名前缀带来的 |
| **T-4** | P1 | `utility._is_ineffective` 的 docstring 写判据是 `injected >= min_samples`，而代码判的是 `scored`（口径合格、真的进了 bps 聚合的条数），用例也按 `scored` 断言。两者不是一回事：口径不合格的注入（毛额 / 费率未知）对 `meanNetAfter` 一点贡献都没有，拿它们凑够样本数等于用空气把一条经验静音掉 | docstring 与 `score()` 的 `Args` 一并订正为 `scored`，并把「为什么门槛必须数 `scored`」写进去 |
| **T-5** | P2 | 四处文案与实现脱节：① `GET /api/experience/overview` 的 docstring 仍写「物化健康（**六个键**）」，本轮已增到九个；② `lib/experience.ts` 的 `FULL_REASON_ZH` 注释写「**五个**原因」而表里是六项（五条判据 + `disabled`），`experienceUtility.test.ts` 断言的正是六项；③④ 桥表由 12 项增到 15 项，但 `service._SETTING_BRIDGE` 上方那句「设置中心可写的 **12** 项阈值」与 `api/ai_auto.py:344` 那句「那 **12** 项可写阈值」都没跟着改（只有 `setting_bridge()` 的 docstring 改了）——而这张表的全部意义就是「wire 键 / `app_settings` 键 / `Settings` 字段三者必须写在同一行」，它自己的计数先漂了 | 四处一并订正；`FULL_REASON_ZH` 的注释顺带写明「键集少一个，那一次回全量在 UI 上就变成一句没有解释的英文原文」 |
| **T-6** | P2 | 卫生四处：`runtime.py` 的 `from dataclasses import replace` 落在 `typing` 之后自成一段、`_injection_rows` 夹在两段模块常量中间；`models.py` 新类前只有一个空行；`.env.example` 末尾多一个空行。`ruff` 只选 `F` / `E9`（刻意不选 `E3` / `I`，见 `pyproject.toml` 的理由），这几条它一条都不会红——只能靠人看 | 四处按仓库既有风格收敛（导入并入 stdlib 段、helper 移到常量段之后、补空行、去尾行） |
| **T-7** | **P2 · 判定不修，明示留档** | I-12 声称「CLAUDE.md 硬阈值全部成立：**无文件 > 800 行**」——**不成立**。`app/db/models.py` 因本轮新增三张表由 **752 → 867 行**，跨过了 800。另有三个本轮改到的文件在**改动前就已经**超阈：`config.py` 941→998、`db/repositories.py` 1054→1071、`schemas.py` 2630→2659 | 终审判定**不拆** `models.py`：它是 `DeclarativeBase` 的**单一注册表**，拆出去要么引入 `models ⇄ ai_experience_models` 的循环导入（谁先被 import 谁就炸，且这个坑只在导入顺序变化时才现形），要么再造一个 `db/base.py` 改掉整个 db 包的形状——两者的回归面都明显大于收益，而本仓库对同类注册 / 聚合文件（`schemas.py` 2659、`session.py` 2479、`manager.py` 1854）一贯如此。按 CLAUDE.md「阈值调整必须在评审中说明理由，**不要靠默认值偷偷放宽**」，这条**在此明示留档**，而不是继续声称阈值全绿。**其余三条硬阈值（方法 ≤50 行、形参 ≤5 个、嵌套 ≤4 层）实测全部成立**，`service.py` 797 / 800 仍是唯一逼近的那个 |
| **T-8** | P1 | **A-33 有两处渲染落点没有任何 DOM 断言**：`ExperienceCard.tsx` 新增的「接管补采 N 笔」与缺口的逐原因明细、`ExperienceList.tsx` 新增的「注入后表现」列——两个既有用例文件（`ExperienceCard.test.tsx` / `ExperienceList.test.tsx`）本轮**一个字都没改**。A-33 的原文要的是「逐项截图或 DOM 断言」，只有纯函数用例（`utilityCellText` / `honestyNotes`）是不够的：`ExperienceCard` 里那行 `experience.harvest?.gap ?? experience.harvestGap` 的**旧键回落**恰恰是「桌面端旧包 + 新后端并存」时唯一的防线，它坏掉时纯函数用例全绿 | 两个文件各补一组用例（新键在场 / 缺席回落 / 零值不渲染 / 三态不同形 / 形状编码 / 措辞不含因果词），实跑 9 + 8 全绿 |

### 终审复核为「实现属实」的关键几条（抽查，非全表）

- **放行条件 1（P2.5 先于 P3）**：`STAT_ORIGINS` 是 `ai_experience_repositories.py` 里的**单一常量**，
  四个读点（`load_experience_events` / `load_evidence` / `count_events` / `overview`）逐个走它；
  源码里 `AiExperienceEvent.origin ==` 的字面量出现 **0** 次（G-16 实跑绿）。
- **放行条件 2 / 3（G-10 纳入 `muted` / `mute_reason`；G-10b 同时绿）**：`test_experience_incremental.py`
  的 G-10 逐节点比对九个字段（含 `muted` / `mute_reason`）与边的**五**元组，G-10b 单独覆盖
  `prevSign` / `signStreak` / `tier`。两条本机实跑均绿。
- **放行条件 4（A-40 的 `iron` 前后对照且方向不增）**：用例把「增量两个 tick」与「**同一份数据、
  同样两个 tick 的全量路径**」相比，断言 `iron_after <= iron_full`——比较对象选得对（不是「加事件
  之前」），J-2b 的方向性因此真的被守住了。
- **放行条件 6（E-13 的锁作用域上提）**：`_materialize_lock` 改成**按 task 可重入**，`rebuild()` 里那行
  `async with` 一行未删（对同一 task 变成空操作）；全局只有 `repo.experience_materialize_lock()`
  **一把**锁，`grep` 确认没有第二把、也没有新的后台任务。T-2 的 `_revalidate` 跑在这把锁**内部**，
  不新增任何同步原语。
- **判定不改的三条**（`describe` 保留 `unknown` 轴 / `exitSource="offline"` 折成 `unknown` /
  `materialize_health.events` 是「本次处理条数」）终审复核**同意**，理由与 I-13 上方那一节一致。

### 终审门禁实测（节点 #3 本机，含 T-1 / T-2 修复之后）

| 门禁 | 基线（§9.1 写死） | 终审实测 |
|---|---|---|
| `ruff check app tests` | 0 | **0（All checks passed）** |
| `tsc --noEmit`（`apps/web`） | 0 | **0** |
| 前端 `NODE_ENV=test vitest run` | 180 文件 / 2094 用例全绿 | **183 文件 / 2121 用例全绿**（终审为 T-8 补的 4 条已计入） |
| 后端全量 `pytest -q -p no:randomly`（`python -u`，不并发） | 3870 passed / **7 failed** | 修复**前** 3914 passed / 7 failed / 4 skipped（13 分 05 秒）；修复**后** **3917 passed / 7 failed / 4 skipped**（16 分 02 秒，+3 = 终审新增的后端用例）。7 条红两次逐条相同，全部来自未跟踪的 `test_factor_library_expansion.py`（`git ls-files --error-unmatch` 回 `did not match any file(s) known to git`），属另一未完成特性 `factor-library-crypto-expansion`，不在本次提交范围内 |
| 经验层子集（修复后重跑） | — | `test_experience_*.py` 14 个文件 **117 passed**（含终审新增的 3 条后端用例）；前端 `ExperienceCard.test.tsx` **9 passed** / `ExperienceList.test.tsx` **8 passed**（含终审新增的 4 条） |
| CLAUDE.md 硬阈值 | 文件 ≤800 / 方法 ≤50 / 形参 ≤5 | 方法 / 形参 / 嵌套三条成立；文件一条见 **T-7**（`models.py` 867，明示留档） |

> ⚠️ **跑测口径**：`pytest -k "resume or pause"` 这类**子集**跑法会让
> `test_discovery_manager.py::test_master_switch_off_vetoes_auto_resume` 偶发红——本仓库全部用例
> 共用同一个临时 DB，子集选择会改变前置数据。该用例单独跑绿、在全量里也绿，**不是回归**。
> 下一轮请沿用「全量 `-p no:randomly` + 按文件跑子集」这两种口径，不要用 `-k` 判定回归。

> 终审人：Anthropic 工程团队 · 资深评审（迭代 2 / 节点 #3）
> 终审方式：逐文件实读全部 30 个改动文件 + 11 个新建文件的 diff，四道门禁本机重跑，
> 两条修复各配一条**去掉修法即红**的用例；凡与源码不符**以源码为准**
> 终审结论：**通过并合入**
