# Factor Layer 生命周期闭环与规模化方案 — spec v2（factor-layer-lifecycle）

> 背景：因子层已经历两轮交付——iteration 1 按 `docs/plans/factor-layer/spec.md` v2 把**独立因子层**整体落地
> （commit `bb74ac3`：engine/factors 五模块 + `factor_score` 策略 + factors service/specs + `api/factors.py`
> + 6 个 Agent 数据工具 + `show_factor` ui-action + 前端「因子实验室」+ 12 个内置因子）；iteration 2 按
> `docs/plans/factor-layer-hardening/spec.md` v2 完成**契约级强化**（commit `c9843fd`：`resolve_norm_contract`
> 单源归一化契约、`assert_norm_contract`/`assert_weights_cover` 边界校验、权重单源烘焙、挖掘落库去重+计数、
> 短窗 `window_too_short` 可观测、训练 `provenance`、前端锁窗/失效/空态）。后端 pytest 483 通过、前端 vitest 401
> 通过、`next build` 成功、`tsc --noEmit` 干净，既有管线零回归。
>
> 本轮（iteration 3）全局目标仍是「**确保因子相关功能完善、稳健、可靠、高成功率**」。前两轮已把「计算→评估→
> 挖掘→训练→组合→打分→策略」这条**数值正确性主干**夯实到契约级别；本轮**不重复**已完成工作，而是聚焦
> 前两轮尚未闭合的**用户主链路生命周期**：`看到所有因子 → 编辑 → 激活/取消 → 选入组合 → 训练 → 建策略 → 回测/对比`。
> 逐文件核验后发现这条链路上仍有**一处会静默展示错误信息**的缺口（编辑因子后评估缓存陈旧）、**一处需求未真正闭合**
> 的完备性缺口（"激活/取消"对组合成员零约束）、**一处规模化短板**（"看到所有挖掘出的因子"在反复挖掘后不可用）、
> 以及**一处会静默给出乐观结果**的稳健性缺口（回测区间与训练区间重叠无提示）。本方案针对这些缺口给出**最小侵入、
> 零表迁移、可逐项落码**的设计，每一项标注根因（指向真实文件 / 行号）、修复契约、改动点与验收用例。
>
> 本文档由 **Subtask #0（方案设计）** 产出，仅写设计、不写实现、不提交。Subtask #1（评审）将在 §0 追加「评审记录」、
> 文末追加「评审结论」并升 v2；Subtask #2（开发）按 §4 文件清单逐项落码，并把实施中发现的方案缺陷回填 §10。

- **版本**：v2（Subtask #1 评审已完成：§0 评审记录 + 文末评审结论；P0/P1 已就地修复）
- **特性目录名**：`factor-layer-lifecycle`
- **作者**：Subtask #0（solution architect）；**评审**：Subtask #1（senior reviewer）
- **状态**：已评审（有条件通过）
- **基线**：commit `c9843fd`（factor-layer-hardening v2 已落地）；本方案为其上的生命周期闭环增量
- **依赖前提**：不改动既有原子策略 / combo / SimBroker / 回测·对比·WF 管线的**对外行为**与**数值契约**；
  所有改动向后兼容（含已保存的 `factor_score` 策略、因子组合、已存因子行与旧 localStorage）。

---

## 0. 评审记录（Review Notes，由 Subtask #1 填写）

> 评审方式：把本方案逐项（L1..L6）对照「可行性 / 完整性 / 一致性 / 适度性」四维，并**逐文件核验**根因描述
> 是否属实（重点核验：`update_factor` 是否清 `eval_json`、`MemberPicker` 成员来源、`list_factors` 过滤维度、
> `factor_score` 快照是否带 `provenance`、`_load_df` 取数 choke point、`is_bar_compatible` 现有用法）。
>
> 严重度定义：**P0** = 会产出错误结果或破坏既有能力的硬伤，必须修复；**P1** = 与代码现实冲突 / 关键缺口，
> 会让需求未闭合或返工，必须修复；**P2** = 健壮性 / 体验 / 可观测性建议，落地时遵循即可。

### 0.1 根因核验表（逐文件核对，基线 commit `c9843fd`）

| 方案声明 | 核验结论 | 证据（文件:行） |
|---|---|---|
| `update_factor` 改 params/direction **不清** `eval_json` | ✅ 属实 | `db/repositories.py:726-744`（仅 patch 五字段）；`set_factor_eval:747-753` 独立写缓存 |
| 「我的因子」RankIC 读缓存 `factor.eval`；行内方向 Select 触发 PUT direction | ✅ 属实 | `FactorTable.tsx:94`（`factor.eval?primaryRankIc(...)`）、`:58`（onDirection→update.mutate direction） |
| `MemberPicker` 成员**只**来自静态库 | ✅ 属实 | `FactorSetBuilder.tsx:248,257`（仅迭代/查 `library`）；pendingMembers 合并去重 `:56-62` |
| `GET /factors` 仅 `enabled`/`category` | ✅ 属实 | `api/factors.py:63-67`、`repositories.list_factors:699-707` |
| `FactorTable` 一次性渲染、无搜索/排序/分页 | ✅ 属实 | `FactorTable.tsx:32,54`（`data??[]`.map），仅计数 header `:37` |
| `factor_score` 内联快照带 `normStats`、**不带** `provenance` | ✅ 属实 | `FactorSetBuilder.tsx:119-123`；`service.resolve_set_to_spec:196-199` 回 `{members,thresholds,normStats}` |
| `compute`/`evaluate`/`score_*` **不预检** bar 兼容 | ✅ 属实 | `service.py:80-102,214-241`（无 `is_bar_compatible` 预检；仅 `window_too_short`） |
| `_load_df` 唯一取数 choke point + 6 调用点 | ✅ 属实 | `service.py:62-67`；compute:83 / evaluate:94 / mine:135 / train:171 / score_set:219 / score_inline:236 |
| store persist v17；`factorLabSection` 持久、`showFactorLab`/`factorOverlay` 瞬态 | ✅ 属实 | `lib/store.ts:388,420`（partialize 仅 `factorLabSection`） |
| 引擎 `is_bar_compatible`/`directionHint`/`intradayOnly` 已就绪 | ✅ 属实 | `engine/factors/library.py:215,200-207`；`factorTypes.ts:14,16`（`directionHint`/`intradayOnly`） |
| `TrainResult.provenance` / `FactorSet.model.provenance` 已就绪 | ✅ 属实 | `factorTypes.ts:153,171`；`service.train:179-183` 回 `provenance` |
| Agent `list_factors` 工具调用 `repo.list_factors(enabled=...)`（关键字） | ✅ 属实，新增可选参数向后兼容 | `agent/tools/factor_tools.py:91` |

> 结论：**全部根因核验属实**，方案对代码现状的描述精确、复用声明可靠。下列发现是在「已属实」基础上对**完整性 / 一致性 / 适度性**的补强。

### 0.2 评审发现（按严重度）

**P0**：无。本方案严守零表迁移、零对外契约破坏，未触碰已契约化的数值正确性主干。

**P1（已就地修复）**

- **P1-1（L2，完整性·正确性）成员去重键漏 `params`，会静默丢弃同 key 不同参的挖掘变体。** 原设计 `onAdd` 按 `(factorKey, direction)` 去重
  （与 pendingMembers 合并 `:59` 同口径）。但「我的因子」恰是大量**同 `factorKey`、不同 `params`** 的挖掘变体——`bulk_create_factors`
  按 `(factorKey, 规范化 params, direction)` 落为独立行（`_factor_dedupe_key:768-770`），且引擎/前端本就支持同 key 多成员
  （`memberNames` 追加 `#k`、`compose_score` 按各成员**自身 params** 计算）。按 `(key,direction)` 去重会把第二个变体**静默丢弃**，直接
  架空本项需求「把挖掘出的因子选入组合」。**修复**：去重键升级为 `(factorKey, 规范化 params, direction)`，抽共享纯函数 `memberIdentity(member)`
  （镜像后端 `_factor_dedupe_key`）落 `lib/factor.ts`，同时用于「我的因子」`onAdd` 与 pendingMembers 合并（一并修掉 `:59` 的同源潜在丢弃）；
  下拉项对同 key 因子用 `名称 · key · 关键参数` 消歧（已改 §3 L2 / §4.2 / §4.3 / §5 / §7）。
- **P1-2（L3，一致性）`_primary_rankic` 定义未钉死、且有键大小写错配风险，会让 `sort=rankic` 与列内显示值不一致。** 列内 RankIC 来自前端
  `primaryRankIc`＝**最小 horizon `h` 的 `rankIc`**（`factor.ts:194-198`），而缓存 `eval_json` 的 horizons 键为 **camelCase**
  （`evaluation.py:39,180`：`rankIc`、`h`）。若后端 `_primary_rankic` 写成 `rank_ic` / 取首项 / 取 max，则服务端排序与用户所见列值**不同序**
  （静默错位）。**修复**：在 §3 L3 / §4.1 / §4.3 / §5 / §7 钉死契约——解析 `eval_json["horizons"]`、按 `h` 升序取第一项的 `rankIc`（camelCase），
  `None` 沉底；排序 `sorted(rows, key=lambda r:(k(r) is None, -(k(r) or 0.0)))` 降序、`None` 沉底，与前端逐字对齐，并加单测断言两者同序。
- **P1-3（L6，正确性·仅在落地 L6 时生效）缓存按引用复用 DataFrame，下游若就地改列将静默污染缓存。** `_load_df` 命中后若返回**同一**
  DataFrame 对象，而任一消费方（`forward_return_label`/`build_factor_matrix`/`compute_factor`/`compose_score`/`score_points`）就地加列/改值，
  则后续命中拿到被污染的 df→**静默错算**，比重新取数更危险。**修复**：命中**必须返回 `cached.copy()`**（防御性拷贝）；作为 L6 的**硬前置条件**——
  不满足则 L6 整项推迟（L1..L5 不依赖 L6）。已改 §3 L6 / §6。

**P2（建议；部分已就地修复，余列为放行条件）**

- **P2-1（L1）无差别失效**：仅凭 `patch.params is not None`/`patch.direction is not None` 即清缓存，会在「PUT 了同值」时无谓失效。属保守安全
  （绝不展示陈旧），可接受；若易实现，建议与 `row` 现值比对后再清。已在 §3 L1 标注，不强制。
- **P2-2（L4）`resolve_set_to_spec` 的 `provenance` 当前无服务端消费方**：真正驱动横幅的是前端 `createStrategy` 内联快照（in-session 由
  `trained.provenance` 注入），唯一建策略入口不经 `resolve_set_to_spec`。已在 §3 L4 澄清后端改动为「单源/前瞻性」而非横幅接线，避免开发误解。
- **P2-3（L2）`freezeMemberDirection` 非 auto 分支应复用既有 `freezeDirection`（`factor.ts:189-191`）保持口径一致**。已在 §3 L2 标注。
- **P2-4（L3）措辞**：客户端分页减的是 DOM、不是网络载荷；服务端 `q`/`source`/`sort` 才减载荷。已在 §3 L3 订正措辞。
- **P2-5（一致性·CLAUDE.md §二）`repositories.py` 已 893 行 > 800 行上限**，L1/L3 再加码。属既有债、本轮增量小（清 eval_json 一行 +
  `list_factors` 参数 + `_primary_rankic`）。建议保持增量最小，并把「因子相关 repo 函数拆出 `factor_repositories.py`」列为后续条目（不阻断本轮）；
  阈值放宽须按 CLAUDE.md 记录于 `.claude-index/config.md`。列为放行条件。
- **P2-6（L3）`source` 非法值与 `ilike` 方言**：未知 `source` 走 `where(source==...)` 自然回空集（可接受），建议仅接受白名单 `{builtin,mined,custom}`、
  否则忽略该过滤；`ilike` 在 SQLite 经 `lower()` 编译可用、对 CJK 名称大小写无关；`q` 做长度上界（已在 L3 提及）。列为放行条件。
- **P2-7（L2/L3）「我的因子」下拉/列表的查询失败兜底**：除「无激活因子」空态外，补 `useQuery` 错误态文案（如「加载因子失败，请重试」）。列为放行条件。

---

## 1. 概述（Overview）

经过两轮交付，因子层的**数值正确性主干**已非常扎实：归一化契约端到端单源（`resolve_norm_contract`）、训练↔打分
窗口/clip 强一致（`assert_norm_contract`/400 `FACTOR_NORM_WINDOW_MISMATCH`）、权重单一真源并烘焙进成员
（`assert_weights_cover`/400 `FACTOR_WEIGHTS_STALE`）、挖掘落库去重并透明回 `evaluated/persisted/skipped`、短窗
`window_too_short` 可观测、训练 `provenance` 与样本内外 IC 过拟合提示。这些保证了「一旦配置正确，结果就正确」。
但全局目标里的「**完善**」与「**高成功率**」，关注的是用户从「看到一个因子」到「让它真正影响交易决策」这条**生命
周期链路**是否处处可用、可信、可规模化——而恰恰这条链路上还残留几处缺口：它们不会破坏已正确配置的组合，却会在
日常的「编辑 / 挑选 / 反复挖掘 / 回测验证」中**静默误导用户**或**让需求功能形同虚设**。

最关键的一条是**编辑因子后评估缓存陈旧**（§3 L1）：`repositories.update_factor` 在改 `params`/`direction` 时**从不
清空 `eval_json`**，而「我的因子」表（`FactorTable`）的 RankIC 列直接读这份缓存（`factor.eval` → `primaryRankIc`）。
于是用户把某因子的方向从「看多」翻成「看空」后，表里仍显示**翻转前的 RankIC**（符号本应取反）——一个会**静默展示
错误信息**的 bug，直接违背「可靠」。第二条是**需求里的"激活/取消"对组合成员零约束**（§3 L2）：`MemberPicker` 只从
**静态因子库**取材，已存「我的因子」（含挖掘出的、用户编辑过的）**根本无法被选进组合**，`Factor.enabled` 因此对
「哪些因子能进组合→进策略→影响交易」毫无作用——iteration 2 已把这条明确列为推迟项（C-1），本轮予以闭合，让"取消
激活"在交易链路上**真正生效**。第三条是**规模化短板**（§3 L3）：需求要求"看到所有挖掘出的因子"，但 `GET /api/factors`
仅支持 `enabled`/`category` 过滤、`FactorTable` 一次性渲染全部行、无搜索/排序/来源筛选/分页；反复挖掘后（每次落
topN）列表迅速膨胀到不可用。

此外还有两处稳健性/可观测性缺口：**回测区间与训练区间重叠无提示**（§3 L4）——`factor_score` 的权重在创建期固定窗口
训练并烘焙，回测/对比/WF 复用同一权重，若评估区间落在训练区间内即是**样本内**、结果偏乐观，而当前 UI 无任何提示
（策略快照甚至不携带 `provenance`，无从对比）；以及**非日内因子误用静默空白**（§3 L5）——`px_vwap_dist` 等
`intraday_only` 因子在日线/跨日 bar 上返回全 NaN，挖掘会跳过但单独计算/评估/打分却只给一片空白、不解释原因，库目录
也不标注"仅日内"。最后，为直接服务「**高成功率**」，本方案在**唯一取数 choke point** `_load_df` 上加一层**有界、短
TTL、仅缓存真实数据**的复用缓存（§3 L6，可选/最低优先级），让 `mine→evaluate→train→score` 这种多跳工作流在 OKX
抖动时不必反复取数、降低累积失败概率。所有改动均为**加固与闭合**，零表迁移、零对外契约破坏、不引入新强依赖。

---

## 2. 背景与现状核验（Ground Truth）

下表是 Subtask #2 落码前必须知道的「代码现状」——根因均已在真实文件中核验（行为描述，非臆测）：

| 现状 | 位置（已核验） | 影响 |
|---|---|---|
| `update_factor` 改 `params`/`direction` **不清** `eval_json` | `db/repositories.py:726-744`（`set_factor_eval` 仅写缓存、与 update 分离） | 编辑后表内 RankIC 陈旧、方向翻转后符号错（L1） |
| 「我的因子」表 RankIC 列读缓存 `factor.eval` | `FactorTable.tsx:94`（`primaryRankIc(factor.eval.horizons)`）；行内方向 `Select` 触发 PUT direction（`:107-114,:58`） | 缓存陈旧即静默展示错误（L1） |
| 组合成员**只能**来自静态库 + 评估/挖掘候选 | `FactorSetBuilder.tsx:226-291` `MemberPicker` 迭代 `library`（`factorApi.library`）；已存「我的因子」无入口 | 已存/已挖掘因子进不了组合；`enabled` 对成员零约束（L2，iter2 C-1） |
| `GET /api/factors` 仅 `enabled`/`category` | `api/factors.py:63-67`、`repositories.list_factors:699-707` | 无搜索/来源/排序；规模化缺口（L3） |
| `FactorTable` 一次性渲染全部行、无搜索/排序/分页 | `FactorTable.tsx:19,:32,:54-66`（仅一个计数 header） | "看到所有挖掘出的因子"反复挖掘后不可用（L3） |
| `factor_score` 内联快照带 `normStats`、**不带** `provenance` | `FactorSetBuilder.tsx:119-123`（`createStrategy` params）；`service.resolve_set_to_spec` 回 `{members,thresholds,normStats}` | StrategyDetail 无从展示训练区间/判定重叠（L4） |
| WF/对比复用同一烘焙权重、无逐窗重训 | `factor_score_strategy.prepare`（读内联快照）；既有 backtest/WF 路径零改动 | 评估区间⊆训练区间→样本内、静默乐观（L4） |
| `intraday_only` 因子在非日内 bar 全 NaN；挖掘跳过但 compute/evaluate/score 不解释 | `library.is_bar_compatible`（已存在，挖掘已用）；`service.compute/evaluate/score_*` 未预检 note | 单独使用时静默空白；库目录无"仅日内"徽标（L5） |
| 所有取数走唯一 `_load_df(rest, window)` | `factors/service.py:62-67`；6 个调用点（compute:83 / evaluate:94 / mine:135 / train:171 / score_set:219 / score_inline:236） | 多跳工作流重复取数、抖动时失败叠加（L6） |
| store persist v17；`factorLabSection` 持久、`showFactorLab`/`factorOverlay` 瞬态 | `lib/store.ts:388,:420,:475-483` | L3 的搜索/筛选态走瞬态即可，**无需** persist 迁移 |

---

## 3. 技术设计（Technical Design）

设计原则（延续前两轮）：**单一真源**、**显式失败 / 绝不静默**（陈旧、重叠、不兼容一律如实标注）、**最小侵入**
（复用既有 helper 与 JSON 列、保持对外契约与响应形状不破坏、零表迁移）、**严格因果**（不引入任何前视泄漏）。

### L1（P1）编辑因子即失效评估缓存（避免静默展示错误 IC）

**根因**：`update_factor`（`repositories.py:726-744`）逐字段 patch `name/params/direction/enabled/notes`，**唯独不动
`eval_json`**；而 `eval_json` 是「以**旧** params/direction 算出的」评估摘要。`FactorTable.Row`（`FactorTable.tsx:94`）
把它当作当前 RankIC 展示。于是「改 params」会让 IC 数值过期，「翻方向 long↔short」更会让 RankIC **符号本该取反却不变**——
属「会静默产出错误信息」的硬伤，是本轮最高优先级（直接服务「可靠」）。

**修复契约**：当 PUT 改变**影响评估结果的字段**（`params` 或 `direction`）时，必须**清空 `eval_json`（置 `NULL`）**；
仅改 `name`/`enabled`/`notes` 时保留缓存（这些不影响 IC）。清空后该因子的 RankIC 列回落到「—（待重新评估）」，用户重
评估后经既有 `set_factor_eval` 回填。**绝不**在 update 内顺手重算评估（那需取数、属另一条 IO 路径，且 update 是同步轻
操作）——只做"失效"，让"重算"显式发生。

**改动**：
1. `db/repositories.py:update_factor`：在写完 `params`/`direction` 后，若二者任一被 patch（`patch.params is not None`
   或 `patch.direction is not None`），追加 `row.eval_json = None`。一行注释说明「参数/方向变更使旧评估缓存失效，避免
   静默展示陈旧 IC」。（评审 P2-1：此为**保守失效**——「PUT 了同值」也会清缓存。属安全偏向，可接受；若易实现，可先与 `row` 现值
   比对、值确有变化再清，以免无谓失效。不强制。）
2. 前端 `FactorTable.tsx`：`factor.eval == null` 时 RankIC 列已渲染 `formatIc(null)→"—"`；为可读性，在该情形给 `<td>`
   加 `title="参数/方向已变更，请重新评估"`（纯提示，不改数据）。
3. （无需改 `factor_out`/schema：`eval_json=None` 经既有序列化即 `eval: undefined/null`，前端已兜底。）

### L2（P1·头牌）激活/取消闭环：已存因子 → 组合成员来源（按 `enabled` 过滤）

**根因**：`MemberPicker`（`FactorSetBuilder.tsx:226-291`）的成员下拉**只迭代静态因子库** `library`；组合成员的另一来源
是「因子评估 / 挖掘」面板的「加入组合」（经 `wb.pendingMembers`）。**已存「我的因子」（`FactorTable` 里那些挖掘出的、
被用户编辑过方向/参数的因子）从无任何入口进入组合**。结果：`Factor.enabled` 对「哪些因子能进组合 → 进 `factor_score`
策略 → 影响交易」**零约束**——需求明确要求的"激活/取消"在交易链路上形同虚设（iteration 2 R-P1-2 已核验并把闭合列为
推迟项 C-1）。本轮兑现 C-1。

**修复契约**：在 `MemberPicker` 新增**第二个成员来源「从我的因子选择」**，其候选**仅为 `enabled==true` 的已存因子**
（禁用因子不出现，从而"取消激活"**真正阻止**因子进入新组合）；选中即以该因子的 `factorKey` + **该因子自己的 `params`**
+ **冻结方向** + `weight:0` 加入成员。**方向冻结规则（遵守 C-P1-1：`compose_score` 不接受 `auto`）**：若因子 `direction`
∈ `{long,short}` 用之；若为 `auto`（尚未评估冻结）则回落到该因子的库 `directionHint`（一个合理的冻结默认），用户仍可在
成员列表里逐条用既有方向 `Select` 翻转。库目录来源**保持不变**（可发现性不受 `enabled` 影响，符合 iteration 2 R-P1-2：
库目录是"能力清单"，不是"启用清单"）。

**改动**：
1. 后端：**零改动**——复用既有 `GET /api/factors?enabled=true`（`api/factors.py:63-67` 已支持 `enabled` 过滤）。
2. 前端 `lib/factor.ts`，新增两枚纯函数（各配单测）：
   - `freezeMemberDirection(factor, library) -> "long" | "short"`——`factor.direction ∈ {long,short}` 时**复用既有
     `freezeDirection`（`factor.ts:189-191`）保持口径一致**；为 `auto` 时回落 `library.find(key)?.directionHint ?? "long"`（评审 P2-3）。
   - `memberIdentity(member) -> string`＝`` `${factorKey}|${JSON.stringify(规范化 params)}|${direction}` ``——**镜像后端
     `_factor_dedupe_key`（`repositories.py:768-770`）**：params 键排序后序列化，作为成员去重的**唯一身份键**（评审 P1-1）。
3. 前端 `FactorSetBuilder.tsx` / `MemberPicker`：
   (a) 加来源切换（两枚 Tab/Segmented：`库目录` / `我的因子`，默认 `库目录` 保持现状）。
   (b) 「我的因子」分支用 `useQuery(["factor","list",{enabled:true}], () => factorApi.list({enabled:true}))` 列出激活因子；
       下拉项对**同 `factorKey` 的因子用 `因子名 · key · 关键参数`（或附 `#id`）消歧**（挖掘变体大量同 key，评审 P1-1），并兜底查询失败/空态
       （评审 P2-7）；选中「添加」时 `onAdd({ factorKey, params: f.params, weight:0, direction: freezeMemberDirection(f, library) })`。
   (c) **去重（评审 P1-1，修正）**：`onAdd` 与 pendingMembers 合并（`:56-62`）**统一改用 `memberIdentity` 去重**，键**含规范化 params**——
       因「我的因子」恰是同 `factorKey`、不同 `params` 的挖掘变体，旧 `(factorKey, direction)` 口径会**静默丢弃**第二个变体、架空本需求；
       而引擎/前端本就支持同 key 多成员（`memberNames` 追加 `#k`、`compose_score` 按各成员自身 params 计算），故含 params 去重既正确又安全
       （顺带修掉 `:59` 的同源潜在丢弃）。同一身份（key+params+方向）仍只允许一条。
   (d) 「我的因子」为空（无激活因子）时给空态「暂无激活因子，去『我的因子』启用或在『因子挖掘』保存结果」。
4. 文档化：在 §5 接口表把 `enabled` 的下游补一条——「`enabled==false` 的已存因子**不可**被选入新因子组合」（C-1 兑现）。

> **范围边界（适度性）**：本项只新增「已存因子 → 组合」这一**来源**并按 `enabled` 过滤；**不**追溯改写已保存组合
> （已保存组合是自包含快照，不随因子 `enabled` 变化失效——这是 iteration 1 R5「自包含快照」的硬约束，绝不破坏）。
> 即 `enabled` 约束的是「**未来**能否把某因子选进**新**组合」，而非「让已存组合里的成员消失」。

### L3（P1）我的因子规模化：服务端过滤/排序 + 前端搜索/筛选/排序/分页

**根因**：`GET /api/factors`（`api/factors.py:63`、`repositories.list_factors:699-707`）仅 `enabled`/`category`；
`FactorTable`（`:19,:54-66`）`factorApi.list()` 无参拉全量并一次性渲染，无搜索/来源筛选/排序/分页。需求要求"看到所有
挖掘出的因子"，但每次 `mine(persist=true)` 落 topN，列表会快速膨胀到几十上百行，现状不可用。

**修复契约**：列表支持按 **名称/key 搜索**、按 **来源（builtin/mined/custom）/类别/激活态** 过滤、按 **RankIC / 名称 /
创建时间** 排序；**保持 `GET /api/factors` 返回数组（`[Factor]`）的对外契约不破坏**（避免波及 `factor_out` 消费方、Agent
`list_factors` 工具、既有测试）——新增 `q`/`source`/`sort` 均为**可选** query，服务端过滤+排序后**仍返回数组**；前端做
**客户端分页**（默认每页 50，"加载更多"/页码）——避免一次性渲染数百行 DOM（**网络载荷的收窄由服务端 `q`/`source`/`sort` 负责；客户端分页只减 DOM、不减载荷**，评审 P2-4）。

**改动**：
1. 后端 `db/repositories.list_factors`：签名增 `q: str | None = None`、`source: str | None = None`、
   `sort: str | None = None`。`q` → `Factor.name`/`Factor.factor_key` 子串（`ilike(f"%{q}%")`，对 `q` 做长度上界/转义）；
   `source` → 先按白名单 `{builtin,mined,custom}` 校验，命中则 `where(Factor.source == source)`、未知值**忽略该过滤**（评审 P2-6）；
   `ilike` 在 SQLite 经 `lower()` 编译可用、对 CJK 名称大小写无关。`sort`：
   - `"name"` → `order_by(Factor.name.asc())`；
   - `"created"` → `order_by(Factor.created_at.desc())`；
   - `"rankic"` → SQL 拿不到 JSON 内的 primary RankIC，故**在 Python 层**对结果按 `_primary_rankic(row)` **降序、`None` 沉底**
     （`sorted(rows, key=lambda r: (_primary_rankic(r) is None, -(_primary_rankic(r) or 0.0)))`）——数据量有限（受分页与挖掘 topN 约束），可接受。
     **`_primary_rankic` 契约（评审 P1-2，必须与前端 `primaryRankIc` 逐字对齐）**：解析 `row.eval_json`（JSON）→ 取 `horizons` 列表 →
     **按 `h` 升序取第一项**的 **`rankIc`（camelCase！缓存 JSON 即 camelCase，见 `evaluation.py:39,180`）**；无 eval / 空 horizons → `None`。
     **绝不可**写成 `rank_ic`、取 max 或取列表首项，否则服务端排序与列内显示值（前端 `primaryRankIc`＝最小 `h` 的 `rankIc`，`factor.ts:194-198`）
     **静默错位**。新增模块内私有 `_primary_rankic(row)` 助手（注意 `repositories.py` 已超 800 行，增量须最小，评审 P2-5）。
   - 缺省/未知 `sort` → 保持既有「builtin 在前、id 升序」。
2. 后端 `api/factors.list_factors`：透传 `q=Query(None)`、`source=Query(None)`、`sort=Query(None)`，仍 `return [factor_out(r) ...]`。
3. 前端 `lib/factorApi.ts`：`list(filter?)` 的 `filter` 类型扩展为 `{ enabled?, category?, q?, source?, sort? }`，拼 query string。
4. 前端 `FactorTable.tsx`：
   (a) 顶部工具条：搜索 `Input`（防抖，写瞬态 state）、来源 `Select`、类别 `Select`、激活 `Select`（全部/仅激活/仅停用）、
       排序 `Select`（RankIC/名称/创建时间）。这些 state 走组件本地 `useState`（**瞬态，不入 store**——避免 persist v18 迁移，
       符合最小侵入；刷新回默认可接受）。query key 带上过滤参数。
   (b) 客户端分页：`page` state + `slice`，底部「加载更多 / 共 N 条」。
   (c) 空/加载态打磨：加载用既有 `Skeleton`；过滤无结果显式空态「无匹配因子，调整筛选条件」。
5. 测试：`test_factors_api.py` 增 `q`/`source`/`sort=rankic|name|created` 用例，其中 **`sort=rankic` 须断言服务端顺序 == 前端
   `primaryRankIc` 降序（最小 `h` 的 `rankIc`，含 `None` 沉底）**，钉死 P1-2 的对齐契约；前端 `factor.test.ts` 对 `factorApi.list`
   query 拼装做轻量测试（可选）。

### L4（P2）训练区间泄漏诚实提示（消除静默乐观）

**根因**：`factor_score` 权重在**创建期固定窗口**训练并烘焙进成员；回测/对比/WF **复用同一烘焙权重、无逐窗重训**
（这是 iteration 1 C-P1-2「内联快照、回测路径零改动」的刻意决策，本身正确）。但若用户用**与训练区间重叠**的区间去回测，
结果即是**样本内**、偏乐观；而当前 UI **无任何提示**，且 `factor_score` 策略快照只带 `normStats`、**不带 `provenance`**
（`FactorSetBuilder.tsx:119-123`；`service.resolve_set_to_spec` 回 `{members,thresholds,normStats}`），StrategyDetail
无从对比。这是「会静默给出乐观结果」的稳健性缺口。

**修复契约**：(a) 把训练 `provenance`（iteration 2 已产出：`{instId,bar,startTs,endTs,horizon}`）**一并写入 `factor_score`
内联快照**（创建路径 + `resolve_set_to_spec` 路径）；(b) StrategyDetail 因子横幅**始终如实展示训练区间**「训练于
instId · bar · [start, end] · 前瞻 h 根」；(c) 当**可判定**当前评估区间与训练区间重叠时，显式提示「评估区间与训练区间重叠，
属样本内、结果可能偏乐观，建议用训练区间之后的样本外区间验证」。**绝不静默**。

**改动**：
1. 后端 `factors/service.resolve_set_to_spec`：返回体增 `provenance`（取自 `model_json.provenance`，缺为 `{}`；与既有
   `normStats` 同范式扩展）。**说明（评审 P2-2）**：当前**唯一**建 `factor_score` 策略入口是前端 `FactorSetBuilder.createStrategy`
   （in-session 由 `trained.provenance` 注入快照，见下条 #2），**不**经 `resolve_set_to_spec`；故本条属「set 行→快照」解析器的
   **单源/前瞻性**补全（未来若新增「从已保存组合直接建策略」的服务端路径即复用），**不是**横幅接线本身——横幅由 #2/#3 的前端改动驱动。
2. 前端 `FactorSetBuilder.createStrategy`（`:113-124`）：`params` 增 `provenance: trained?.provenance`（与 `normStats`
   并列；未训练/等权时为 `undefined`，安全缺省）。
3. 前端 `StrategyDetail.FactorStrategyBanner`（`StrategyDetail.tsx:17-55`）：读 `strategy.params.provenance`，新增一行训练
   区间展示（缺省则不展示，向后兼容旧快照）。
4. **重叠判定（必做 vs 可选打磨）**：
   - **必做（零依赖）**：横幅始终展示训练区间 + 一句静态自查提示「若回测/对比区间落在上述训练区间内即为样本内，请注意
     乐观偏差」。这不需要"当前评估区间"，对所有快照可用。
   - **可选打磨（落地条件 C-L4）**：若 store/父层易得「当前选择的回测区间」（instId/bar/startTs/endTs），用纯函数
     `rangesOverlap(aStart,aEnd,bStart,bEnd)` 精确高亮重叠并升级为黄/accent 警示条。`rangesOverlap` 落 `lib/factor.ts` 并配
     单测；若取「当前区间」需穿过多层 props 则**推迟为后续打磨**，不阻断本项验收（必做部分已消除"完全无提示"）。
5. 测试：`test_factors_api.py`/service 单测断言 `resolve_set_to_spec` 带 `provenance`；`factor.test.ts` 测 `rangesOverlap`
   正常+边界（相邻不重叠、包含、部分重叠）。

### L5（P2）非日内因子误用护栏 + 库 bar 兼容性可见

**根因**：`px_vwap_dist` 等 `intraday_only` 因子在日线/跨日 bar 上 `compute` 返回全 NaN（coverage=0）。挖掘已用
`is_bar_compatible` 跳过（标 `bar_incompatible`），但**单独** compute/evaluate/score 一个不兼容因子时只给一片空白、
**不解释原因**；库目录（`FactorCatalog`）与我的因子表（`FactorTable`）也**未标注"仅日内"**，用户无从预知。

**修复契约**：(a) `compute`/`evaluate`/`score_*` 在因子（或组合任一成员）因 bar 不兼容导致 coverage==0 时，返回
`note:"bar_incompatible"`（与 `window_too_short` 区分）；前端如实标注「该因子仅适用于日内周期」。(b) 库目录与我的因子表对
`intradayOnly` 因子显示徽标「仅日内」（数据已在 `FactorLibraryItem.intradayOnly` / `factor_meta`，无需后端新增）。

**改动**：
1. 后端 `factors/service.compute`：用既有 `is_bar_compatible(factor_key, window.bar)` 预检，不兼容则响应加
   `note:"bar_incompatible"`（compute 已回 `coverage`，加 `note` 字段为加性）。
2. 后端 `factors/service.evaluate`：同样预检，结果 `note` 置 `"bar_incompatible"`（`FactorEvalResult.note` 字段已存在）。
3. 后端 `factors/service.score_set/score_inline`：组合任一成员对当前 bar 不兼容时，在既有 payload 加 `note:"bar_incompatible"`
   （与 `window_too_short` 互斥择一，bar 不兼容优先）。
4. 前端 `FactorCatalog.tsx`/`FactorTable.tsx`：`intradayOnly` 因子旁加 `Badge`「仅日内」（沿用既有 Badge 组件）。
5. 前端 `FactorEvalPanel.tsx`/`FactorScoreChart.tsx`：`note==="bar_incompatible"` 时专门空态文案「该因子仅适用于日内周期，
   请切换到 1m/5m/15m/1H 等日内周期」。
6. 测试：`test_factors_api.py` 断言「intraday-only 因子于日线 evaluate → `note=="bar_incompatible"`」；前端徽标/空态可选。

### L6（P2·可选·最低优先级）多步因子工作流取数复用（有界短 TTL 真实缓存）

**根因**：`_load_df(rest, window)`（`service.py:62-67`）是因子层**唯一取数 choke point**；`mine→evaluate→train→score` 同窗
各自独立 `load_range_candles`。多跳工作流在 OKX 抖动时**失败概率叠加**，且重复取数慢——直接拖累「高成功率」。

**修复契约**：在 `_load_df` 内加一层**有界、短 TTL、仅缓存真实已取 candles** 的进程内缓存，键
`(instId, bar, startTs, endTs, maxBars)`；命中且未过期即复用，TTL 到期或容量超限即淘汰。**硬约束**：只缓存**成功取到的
真实数据**（绝不缓存空/异常）；未命中仍走 `load_range_candles`，OKX 不可达仍按既有语义显式 400/502（**绝不**以缓存假装
实时、绝不改"不可达即报错"铁律）；不同 instId/bar/区间严格隔离不串味。

**改动**：
1. `factors/service.py`：模块级 `_CandleCache`（`OrderedDict` + `time.monotonic()` 时间戳；`TTL_SECONDS≈90`、`CAP≈16`）。
   `_load_df`：先按 key 查未过期项 → **命中返回 `cached.copy()`**（防御性拷贝，评审 P1-3）；未命中取数成功后写入并按 LRU+TTL
   淘汰。`time.monotonic()` 仅用于 TTL、不进入任何计算，不影响数值可测性（与 iteration 2「provenance.trainedAt 由路由注入、
   引擎纯函数不取时钟」纪律一致——本项时钟只在 service 编排层、且只管缓存寿命）。
   > **硬前置条件（评审 P1-3，必须满足，否则 L6 整项推迟）**：下游消费方（`compute_factor`/`build_factor_matrix`/`forward_return_label`/
   > `compose_score`/`score_points`）可能就地改 df；若命中返回**同一对象**会静默污染缓存→后续命中错算（比重新取数更危险）。故命中**必须返回
   > `.copy()`**；若不愿承担拷贝成本且无法逐一证明所有 6 个消费方只读 df，则 L6 不得落地。L1..L5 不依赖 L6。
2. `factors/service.py`：导出 `clear_candle_cache()` 供测试隔离（呼应 `aragon-quant-test-isolation`：测试共享进程，须能清缓存
   防串扰）；在相关测试 setup/teardown 调用。
3. 测试：`test_factors_service.py`（或并入既有）monkeypatch `load_range_candles` 计数——同窗连续两次 `compute` 只取一次数；
   不同窗各取一次；TTL 过期后重取；`clear_candle_cache()` 后重取。

> **适度性声明**：L6 引入进程内状态，是本方案唯一"基础设施"项。因其收敛在**单一 choke point**、缓存的是**不可变的历史
> K 线**、且**严守"未命中即真实取数 / 不可达即报错"**，blast radius 极小、与"绝不假数据"铁律相容。但若评审认为引入进程
> 状态超出本轮"最小侵入"定位，**可整项推迟**到后续——L1..L5 不依赖 L6。

---

## 4. 文件 / 模块变更计划（File / Module Change Plan）

### 4.1 后端（services/quant）

| 文件 | 新建/修改 | 一句话意图 |
|---|---|---|
| `app/db/repositories.py` | 修改 | `update_factor` 在 params/direction 变更时清 `eval_json`（L1）；`list_factors` 增 `q`/`source`/`sort` + `_primary_rankic`（读 camelCase `eval_json.horizons`、最小 `h` 的 `rankIc`、`None` 沉底，对齐前端 `primaryRankIc`，评审 P1-2）助手（L3）。**注：本文件已 893 行 > 800 行上限，增量须最小（评审 P2-5）。** |
| `app/api/factors.py` | 修改 | `GET /factors` 透传 `q`/`source`/`sort` query，仍返回数组（L3）。 |
| `app/factors/service.py` | 修改 | `resolve_set_to_spec` 返回带 `provenance`（L4，单源/前瞻性，评审 P2-2）；`compute`/`evaluate`/`score_*` bar 不兼容时回 `note:"bar_incompatible"`（L5）；`_load_df` 加有界短 TTL 真实缓存（**命中返回 `.copy()`**，评审 P1-3）+ `clear_candle_cache`（L6，可选，可整项推迟）。 |
| `app/factors/specs.py` | 复核 | 确认 `resolve_set_to_spec` 的 provenance 取数与 `members_from_set_row`/`norm_stats_from_set_row` 同兜底范式（L4，预计零代码改动或仅一行）。 |

> 说明：`enabled` 闭环（L2）后端**零改动**（复用既有 `?enabled=` 过滤）；`schemas.py` **无需改动**（`compute`/`train`/`mine`
> 等路由本就返回纯 dict 透传，新增 `note`/`provenance` 字段沿 dict 自动流出，类型契约由前端 `factorTypes.ts` 承载——延续
> iteration 2 §10 的核验结论）。

### 4.2 前端（apps/web）

| 文件 | 新建/修改 | 一句话意图 |
|---|---|---|
| `lib/factor.ts` | 修改 | 新增纯函数 `freezeMemberDirection`（复用既有 `freezeDirection`，评审 P2-3）、`memberIdentity`（含规范化 params 的成员去重唯一键，镜像后端 `_factor_dedupe_key`，评审 P1-1）（L2）、`rangesOverlap`（L4 可选打磨）。 |
| `lib/factorApi.ts` | 修改 | `list(filter?)` 的 `filter` 增 `q`/`source`/`sort`（L3）。 |
| `lib/factorTypes.ts` | 修改 | `FactorComputeResult`/`FactorScoreResult`/`FactorEvalResult` 的 `note` 容纳 `"bar_incompatible"`（L5）；`factor_score` 快照 params 类型带可选 `provenance`（L4）。 |
| `components/Factor/FactorSetBuilder.tsx` | 修改 | `MemberPicker` 新增「从我的因子选择」来源（按 `enabled` 过滤 + 冻结方向 + **按 `memberIdentity` 含 params 去重**，评审 P1-1）（L2）；`createStrategy` 快照带 `provenance`（L4）。 |
| `components/Factor/FactorTable.tsx` | 修改 | 顶部搜索/来源/类别/激活/排序工具条 + 客户端分页 + 空态（L3）；RankIC 列陈旧 `title` 提示（L1）；`intradayOnly` 徽标（L5）。 |
| `components/Factor/FactorCatalog.tsx` | 修改 | `intradayOnly` 因子加「仅日内」徽标（L5）。 |
| `components/Factor/FactorEvalPanel.tsx` | 修改 | `note==="bar_incompatible"` 空态文案（L5）。 |
| `components/Factor/FactorScoreChart.tsx` | 修改 | `note==="bar_incompatible"` 空态文案（复用既有 `emptyNote` 通道）（L5）。 |
| `components/Strategy/StrategyDetail.tsx` | 修改 | `FactorStrategyBanner` 展示训练区间 + 样本内/外自查提示（L4）。 |

> 说明：`lib/store.ts` **无需改动**（L3 的搜索/筛选/排序态走组件本地瞬态 `useState`，不入 persist，**不**升 v18，零迁移）。

### 4.3 测试

| 文件 | 新建/修改 | 意图 |
|---|---|---|
| `services/quant/tests/test_factors_api.py` | 修改 | 编辑 params/direction 后 `eval` 失效、改 name 后保留（L1）；`q`/`source`/`sort` 过滤与排序（L3），**`sort=rankic` 须断言顺序 == 前端 `primaryRankIc` 降序（最小 `h` 的 `rankIc`、`None` 沉底），钉死 P1-2 对齐契约**；intraday-only 因子 evaluate → `bar_incompatible`（L5）；`resolve_set_to_spec` 带 `provenance`（L4）。 |
| `services/quant/tests/test_factor_score_strategy.py` | 修改 | 旧快照（无 `provenance`）仍正常（向后兼容）；带 `provenance` 快照可读回（L4）。 |
| `services/quant/tests/test_factors_service.py` | 新建（或并入既有） | `_load_df` 取数缓存命中/隔离/TTL/清缓存（L6，若落地）；**命中返回的 df 被就地修改后，下次命中不受污染（验证 `.copy()`，P1-3）**。 |
| `apps/web/lib/factor.test.ts` | 修改 | `freezeMemberDirection`（含 auto→directionHint 回落、复用 `freezeDirection`）（L2）；**`memberIdentity`：同 key 不同 params → 不同键、同 key 同 params 同向 → 同键（P1-1）**；`rangesOverlap` 正常+边界（L4）。 |

---

## 5. 接口设计（Interface Design）

沿用既有路由前缀 `/api`、camelCase、秒级 UTC、错误体 `{error:{code,message}}`。本轮**仅新增可选 query 与可选响应字段**，
不改既有路径、请求体与响应**形状**（数组仍是数组），向后兼容。

- **`GET /api/factors` 新增可选 query（L3）**：
  - `q?: string` — 名称/key 子串搜索（服务端 `ilike`，带长度上界；SQLite 经 `lower()` 编译、CJK 大小写无关）。
  - `source?: "builtin" | "mined" | "custom"` — 来源过滤（**白名单校验，未知值忽略该过滤**，评审 P2-6）。
  - `sort?: "rankic" | "name" | "created"` — 排序（`rankic` 在服务端 Python 层按 `_primary_rankic` ＝**缓存 `eval_json.horizons`
    最小 `h` 的 `rankIc`（camelCase）**降序、`None` 沉底，**与前端 `primaryRankIc` 逐字对齐**，评审 P1-2；缺省仍 builtin-first + id 升序）。
  - 既有 `enabled?`/`category?` 不变；**响应仍为 `[Factor]` 数组**（契约不破坏）。
- **`PUT /api/factors/{id}` 行为变更（L1，对外契约不变）**：当 body 含变更后的 `params` 或 `direction` 时，服务端**清空该因子
  `eval_json`**；响应 `Factor` 的 `eval` 字段随之为 `null`，前端展示「—（待重新评估）」。
- **`POST /api/factors/compute` / `/evaluate` / `/factor-sets/{id}/score` / `/score-preview` 响应新增可选字段（L5）**：
  因 bar 不兼容导致空结果时增 `note:"bar_incompatible"`（与 `window_too_short` 互斥；旧前端忽略新字段即与现状一致）。
- **`factor_score` 策略内联快照新增可选字段（L4）**：`params_json` 增 `provenance:{instId,bar,startTs,endTs,horizon}`
  （由前端 `createStrategy` 创建路径注入；`resolve_set_to_spec` 同步带出 `provenance` 为单源/前瞻性补全，当前无服务端建策略消费方，
  评审 P2-2）；旧快照无该字段时 StrategyDetail 不展示训练区间（向后兼容）。
- **`enabled` 语义补充（L2，文档化）**：除既有「`list_factors(enabledOnly)` Agent 池过滤 + 『我的因子』默认视图」外，
  本轮新增下游——**`enabled==false` 的已存因子不可被选入新的因子组合**（「从我的因子选择」来源按 `enabled` 过滤）。
  **不**追溯影响已保存组合（自包含快照，iteration 1 R5 硬约束）。
- **Agent 工具**：签名全部不变；`list_factors` 工具的 `enabledOnly` 语义与新增的「已存因子→组合」过滤同口径（仅展示激活项），
  无需改工具代码。

---

## 6. 数据模型（Data Model）

**零表结构迁移**（继续 `Base.metadata.create_all` 幂等建表，本轮不新增列、不改列类型）。所有新增字段复用既有 JSON 列 /
既有可空列：

- `factors.eval_json`（既有 `Text|None`）：L1 把它在 params/direction 变更时**置 `NULL`**——即「评估缓存失效」用既有可空
  语义表达，无新列。`factor_out` 对 `eval_json is None` 已回 `eval: null`（前端兜底「—」）。
- `factor_sets.model_json`（既有 `Text`）：`provenance` 早在 iteration 2 已落于此；L4 仅把它经 `resolve_set_to_spec` **透传**
  进 `factor_score` 策略 `params_json`，无新列。
- `factor_score` 策略 `params_json`（自包含内联快照）：新建路径写为
  `{members(已烘焙 weight), thresholds, normStats:{normWindow,clip}, provenance?:{instId,bar,startTs,endTs,horizon}}`。
  > **向后兼容硬约束**：已保存的旧快照**无 `provenance`**（甚至更旧者无 `normStats`、带顶层 `weights`）。`prepare` 行为
  > **完全不变**（`provenance` 不参与打分，仅供 UI 展示）；StrategyDetail 读不到 `provenance` 即不展示训练区间。**绝不**因
  > 新增字段而改变任何打分/交易行为。
- 内存层（L6，若落地）：`_CandleCache` 是 service 模块级 `OrderedDict[tuple, (monotonic_ts, DataFrame)]`，**不持久化**、
  进程级、有界（CAP）短寿（TTL），仅缓存真实历史 K 线；**命中返回 `cached.copy()`**（防御性拷贝，下游就地改 df 不污染缓存，评审 P1-3）。

前端类型（`lib/factorTypes.ts`）：`note` 联合增 `"bar_incompatible"`；`factor_score` 策略 params 类型增可选 `provenance`；
`factorApi.list` 的 `filter` 增 `q`/`source`/`sort`。均为加性、可选，旧调用安全缺省。

---

## 7. 测试与验收标准（Testing & Acceptance Criteria）

### 7.1 关键回归用例（先写能复现/钉死的测试，再改业务，CLAUDE.md §七）

1. **编辑失效缓存（L1）**：建因子→评估（写入 `eval_json`）→ PUT 改 `direction`（long↔short）→ GET 该因子，断言
   `eval == null`（不再展示陈旧 RankIC）；另断言「仅改 `name`」后 `eval` **保留**；改 `params` 后 `eval == null`。
2. **激活/取消闭环（L2）**：`freezeMemberDirection` 纯函数：`direction=long/short` 原样、`direction=auto` 回落库
   `directionHint`、库缺失回落 `"long"`；断言"从我的因子选择"来源**只列 `enabled==true`**（后端 `?enabled=true` 已有用例，
   复核钉死）；**`memberIdentity` 去重（P1-1）**：同 `factorKey` 不同 `params` 两条**都能加入**、同 `factorKey` 同 `params` 同向只留一条。
3. **规模化（L3）**：`GET /factors?q=` 子串命中；`?source=mined` 仅挖掘项；`?sort=rankic` 按 `_primary_rankic`（最小 `h` 的 `rankIc`）
   降序、无 eval 者沉底，**且顺序与前端 `primaryRankIc` 降序逐字一致（P1-2）**；`?sort=name|created` 正确；缺省排序仍 builtin-first。
   前端分页 slice 与"加载更多"。
4. **训练区间诚实（L4）**：`resolve_set_to_spec` 返回含 `provenance`；带 `provenance` 的 `factor_score` 快照可读回、
   `prepare` 行为与无 `provenance` 时**逐字一致**（打分不受影响）；`rangesOverlap` 正常+边界（相邻不重叠/包含/部分重叠）。
5. **非日内护栏（L5）**：intraday-only 因子（`px_vwap_dist`）于日线 `evaluate`/`compute` → `note=="bar_incompatible"`；
   日内 bar 正常无该 note；前端徽标/空态（可选组件测）。
6. **取数复用（L6，若落地）**：monkeypatch `load_range_candles` 计数——同窗两次 `compute` 仅取一次；异窗各取一次；TTL 过期
   重取；`clear_candle_cache()` 后重取；缓存未命中且 OKX 不可达仍按既有语义报错（不被缓存掩盖）。

### 7.2 验收标准（Definition of Done）

1. 后端 `pytest`（含 L1~L6 新增/扩充用例）全绿；前端 `npm run test`（vitest，组件测试置 `NODE_ENV=test`，见
   `aragon-web-vitest-node-env` 约定）+ `npm run build`（Next 内置 tsc 类型检查）全绿，必要时再跑 `npx tsc --noEmit` 作显式
   类型门；既有 **483/401** 用例**零回归**。**不使用 `npm run lint`**（仓库无 ESLint 配置，`next lint` 会交互式初始化挂起；
   延续 iteration 1 D-5 / iteration 2 R-P1-3 的门禁结论）。
2. 真实 OKX 数据下：编辑因子方向/参数后「我的因子」RankIC 列**不再展示陈旧值**；停用某因子后其**不可被选入新组合**、启用后可选；
   "我的因子"可搜索/筛选/排序/分页地浏览全部（含大量挖掘结果）。
3. `factor_score` 策略详情**如实展示训练区间**并给出样本内/外自查提示（旧快照无 `provenance` 时安全不展示）；非日内因子在
   日线周期下给出「仅日内」明确提示而非静默空白，库目录/我的因子带「仅日内」徽标。
4. （L6 若落地）多步因子工作流同窗取数被复用、OKX 抖动下成功率提升，且**未命中即真实取数、不可达即报错**的铁律不变。
5. **向后兼容**：已保存的旧 `factor_score` 策略（无 `provenance`/旧快照）、已保存因子组合（自包含快照不随 `enabled` 失效）、
   旧 localStorage（persist v17 不升级）继续可用，**零破坏性变更、零表迁移**。

---

## 8. 风险与缓解（Risks & Mitigations）

| 风险 | 影响 | 缓解 |
|---|---|---|
| L1 清缓存让用户「编辑后看不到 IC」 | 体验落差（需重评估） | RankIC 列 `title` 明示「参数/方向已变更，请重新评估」；这是**正确**行为（陈旧 IC 更危险）。 |
| L2 把 `enabled` 升级为成员约束改变既有预期 | 用户以为停用因子仍可进组合 | 仅约束**新**组合的成员来源；已存组合自包含快照不变（R5）；空态/提示清晰；库目录不受影响（可发现性保留）。 |
| L2 方向冻结回落（auto→directionHint）可能与用户意图不符 | 默认方向不理想 | 成员列表保留逐条方向 `Select` 可翻转；directionHint 是库内合理默认；绝不把 `auto` 带进组合（守 C-P1-1）。 |
| L3 `sort=rankic` 在 Python 层排序 | 数据量极大时排序开销 | 受挖掘 topN 与前端分页约束，量级有限；`q`/`source` 先在 SQL 收窄再排序；如未来超量可再下推 SQL/加索引（列出为后续条件）。 |
| L3 `_primary_rankic` 与前端 `primaryRankIc` 不同序（键大小写/定义错配） | `sort=rankic` 与列内显示值静默错位 | 钉死定义：读 camelCase `eval_json.horizons`、最小 `h` 的 `rankIc`、`None` 沉底；单测断言两端同序（评审 P1-2）。 |
| L2 成员去重漏 `params` 静默丢弃挖掘变体 | 同 key 不同参因子进不了组合，架空需求 | 去重键升级为 `(factorKey, 规范化 params, direction)`（`memberIdentity` 镜像后端 `_factor_dedupe_key`）；单测覆盖同/异参（评审 P1-1）。 |
| L6 缓存按引用复用 df 被下游就地改 | 后续命中静默错算 | 命中返回 `cached.copy()`；做不到则 L6 整项推迟（评审 P1-3）。 |
| L3 改 `list_factors` 签名 | 误伤既有调用方 | 新参数**全部可选、默认 None**，行为与现状逐字一致；**响应仍为数组**，零 ripple；加测试钉死缺省路径不变。 |
| L4 provenance 进快照被误当作打分输入 | 改变交易行为 | `provenance` **仅供 UI 展示**，`prepare`/`compose_score` 绝不读它；测试断言带/不带 provenance 打分逐字一致。 |
| L4「当前评估区间」难获取 | 精确重叠高亮做不出 | 必做部分（展示训练区间+静态自查提示）零依赖即可消除"完全无提示"；精确重叠列为可选打磨 C-L4，不阻断验收。 |
| L5 note 字段增加响应字段 | 旧前端忽略 | 字段可选、缺省安全；旧前端不读即与现状一致。 |
| L6 进程内缓存引入状态 / 跨测试串扰 | 测试不稳定、内存增长 | 单一 choke point + 有界(CAP)+短 TTL；只缓存真实数据；`clear_candle_cache()` 供测试隔离（呼应 `aragon-quant-test-isolation`）；未命中仍真实取数、不可达仍报错。 |
| 范围蔓延（误把它做成"重写"） | 偏离"闭合/规模化"定位 | 严守最小侵入、零表迁移、零对外契约破坏；L6 明确可整项推迟；任何新表 / 新强依赖 / 新路由一律出局。 |

---

## 9. 实现顺序（建议）

1. **L1（正确性优先）**：`update_factor` 清 `eval_json` + 回归测试（先复现"翻方向后 RankIC 陈旧"再修）。
2. **L2（头牌·完整性）**：`freezeMemberDirection` 纯函数 + 测试 → `MemberPicker` 「从我的因子」来源（enabled 过滤+去重）。
3. **L3（规模化）**：`repositories.list_factors` 增 `q`/`source`/`sort` + `api` 透传 → `factorApi.list` → `FactorTable` 工具条+分页+空态 + 测试。
4. **L4（稳健·诚实）**：`resolve_set_to_spec` 带 `provenance` → `createStrategy` 快照带 `provenance` → `FactorStrategyBanner` 展示 + `rangesOverlap`（可选）+ 测试。
5. **L5（边界）**：service 三处 `is_bar_compatible` 预检 + note → 前端徽标/空态 + 测试。
6. **L6（可选·高成功率）**：`_load_df` 缓存 + `clear_candle_cache` + 测试（评审若判超范围则推迟）。
7. **门禁**：后端 `pytest`、前端 `npm run test` + `npm run build`（+ `npx tsc --noEmit`）全绿（**不跑 `npm run lint`**），
   确认既有 483/401 用例零回归。

每一步均可独立交付且不破坏既有行为；L1（避免静默错算）与 L2（兑现需求"激活/取消"闭环）是直接服务全局目标"完善·可靠"
的核心，优先级最高；L6 为可整项推迟的加固项。

---

## 10. 实施过程发现的方案缺陷（由 Subtask #2 回填）

落码逐项核对后未发现需偏离方案的设计缺陷；以下为与代码现实的澄清/微调（均不改变方案契约，记录以保持本 spec 为唯一真源）：

1. **（L5）`FactorCatalog.tsx` 的「仅日内」徽标已存在，本轮零改动。** §4.2 把 `FactorCatalog.tsx` 列为「修改」，
   但核验 `FactorCatalog.tsx:60` 已渲染 `{item.intradayOnly && <Badge tone="neutral">仅日内</Badge>}`（前轮已落）。
   该变更计划项已满足，无需再改；本轮仅在 `FactorTable.tsx` 新增「仅日内」徽标（Factor 行不带 `intradayOnly`，
   故按 `factorKey` 查库目录映射出兼容性，新增一枚 `factor.library` 查询）。
2. **（L5）`FactorScoreChart.tsx` 零代码改动。** §4.2 列为「修改」，但其 `emptyNote` 通道已就绪（`FactorScoreChart.tsx:21`），
   `bar_incompatible` 空态文案由调用方 `FactorEvalPanel`（compute 上图预览路径）经既有 `emptyNote` prop 注入即可，
   组件本身无需改动（契约项「note 空态文案」已由调用方满足）。
3. **（L4）`factor_score` 快照类型与 `api.createStrategy` 的 `Record<string,unknown>` 约束。** 新增导出
   `FactorScoreSnapshotParams`（带可选 `provenance`，§4.2 要求）。但 TS 不允许**具名 interface**直接赋给
   `Record<string,unknown>`（缺索引签名），而**对象字面量**可以；故 `FactorSetBuilder.createStrategy` 的快照保持
   **内联对象字面量**（结构对齐 `FactorScoreSnapshotParams`，由 tsc 结构校验），`StrategyDetail` 横幅则经
   `Partial<FactorScoreSnapshotParams>` **消费**该类型读取 `provenance`（即类型有真实消费方，非死代码）。
4. **（L4）「当前评估区间」未穿层，按方案落「必做部分」。** 横幅始终如实展示训练区间 + 静态样本内/外自查提示
   （零依赖，对所有带 `provenance` 的快照可用）；`rangesOverlap` 纯函数 + 单测已落 `lib/factor.ts`，留待后续打磨
   精确高亮（评审 §3 L4 可选打磨 C-L4，不阻断本项验收）。
5. **（L6 已落地）取数缓存防御性拷贝硬前置已满足。** `_load_df` 命中返回 `cached.copy()`（评审 P1-3 硬条件），
   并导出 `clear_candle_cache()` 供测试隔离；新增 `test_factors_service.py` 钉死命中复用/窗口隔离/TTL/清缓存/
   **命中 df 就地改列后下次命中不被污染**/不可达仍报错。既有因子测试全部用确定性合成 K 线（同窗同数据），缓存
   命中即等价真实取数，零跨测串扰，未改动 `conftest.py`。

---

## 评审结论（Review Conclusion）

**结论：有条件通过（approved-with-conditions）。**

本方案根因核验**全部属实**、与代码现状高度吻合（§0.1 逐文件核对，基线 `c9843fd`），复用既有 helper / JSON 列、零表迁移、零对外契约
破坏，范围聚焦「用户主链路生命周期闭环 + 规模化」，定位与全局目标「完善·稳健·可靠·高成功率」一致。**无 P0**；已就地修复 **3 个 P1**：

- **P1-1**（L2 成员去重含 `params`，避免静默丢弃挖掘变体）—— 已改 §0.2 / §3 L2 / §4.2 / §4.3 / §5 / §7 / §8。
- **P1-2**（L3 `_primary_rankic` 钉死定义 + camelCase 键，服务端排序对齐前端列）—— 已改 §0.2 / §3 L3 / §4.1 / §4.3 / §5 / §7 / §8。
- **P1-3**（L6 命中返回 `.copy()`，否则整项推迟）—— 已改 §0.2 / §3 L6 / §4.1 / §6 / §8。

**放行条件（Subtask #2 落码须遵循）**：

1. **L6 防御性拷贝硬前置**：若实现 L6，`_load_df` 命中**必须**返回 `cached.copy()`（或逐一证明全部 6 个消费方只读 df）；做不到则
   L6 整项推迟，不阻断 L1..L5（P1-3）。
2. **服务端排序对齐前端**：`_primary_rankic` 严格读 camelCase `eval_json["horizons"]`、最小 `h` 的 `rankIc`、`None` 沉底；加单测
   断言 `sort=rankic` 顺序 == 前端 `primaryRankIc` 降序（P1-2）。
3. **成员去重含 params**：`memberIdentity` 镜像后端 `_factor_dedupe_key`，同时用于「我的因子」`onAdd` 与 pendingMembers 合并；
   下拉项对同 key 因子消歧（P1-1）。
4. **门禁**：后端 `pytest`、前端 `npm run test`（vitest，组件测试 `NODE_ENV=test`）+ `npm run build`（必要时 `npx tsc --noEmit`）
   全绿，既有 **483/401** 用例零回归，**不跑 `npm run lint`**。
5. **剩余 P2 落地遵循即可，不阻断验收**：P2-1（L1 可与现值比对再清，可选）、P2-5（`repositories.py` 已 893 行 > 800 行上限——增量
   最小化，并把「拆 `factor_repositories.py`」列为后续，阈值调整须记录于 `.claude-index/config.md`）、P2-6（`source` 白名单 + `q` 长度
   上界）、P2-7（「我的因子」查询失败/空态兜底）；P2-2 / P2-3 / P2-4 已就地订正。
6. **铁律不破**：`provenance` 仅供 UI、绝不参与打分（`prepare`/`compose_score` 不读）；未命中即真实取数、不可达即报错；已保存组合
   自包含快照不随 `enabled` 失效（iteration 1 R5）；绝不把 `auto` 带进组合（C-P1-1）。

满足以上条件即视为通过，Subtask #2 可按 §9 顺序落码，并把实施偏差回填 §10。

— 评审人：Subtask #1（senior reviewer）｜基线 commit `c9843fd`｜文档版本 **v2**

---

- **基线**：commit `c9843fd`（factor-layer-hardening v2 已落地）
- **核验范围**：`db/repositories.py`（`update_factor`/`list_factors`）、`api/factors.py`、`factors/service.py`
  （`_load_df`/`resolve_set_to_spec`/`compute`/`evaluate`/`score_*`）、`factors/specs.py`、`engine/factors/library.py`
  （`is_bar_compatible`）、前端 `FactorSetBuilder.tsx`/`FactorTable.tsx`/`FactorCatalog.tsx`/`StrategyDetail.tsx`/
  `lib/factor.ts`/`lib/factorApi.ts`/`lib/factorTypes.ts`/`lib/store.ts`。
- **文档版本**：v2（Subtask #1 评审完成：§0 评审记录 + 文末评审结论，3 个 P1 已就地修复，结论「有条件通过」）
