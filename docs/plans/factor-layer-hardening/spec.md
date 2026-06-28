# Factor Layer 健壮性与完备性强化方案 — spec v2（factor-layer-hardening）

> 背景：上一轮（iteration 1）已按 `docs/plans/factor-layer/spec.md` v2 把**独立因子层**整体落地并提交
> （commit `bb74ac3` — engine/factors 五模块 + `factor_score` 策略 + factors service/specs + `api/factors.py`
> + 6 个 Agent 数据工具 + `show_factor` ui-action + 前端「因子实验室」+ 12 个内置因子）。后端 pytest 461 通过、
> 前端 vitest 395 通过、`next build` 成功，既有管线零回归。
>
> 本轮（iteration 2）全局目标是「**确保因子相关功能完善、稳健、可靠、高成功率**」。因此本方案**不重复**已完成的
> 从 0 搭建，而是**对照已落地代码逐文件核验**，针对发现的 9 类「会静默产出错误结果 / 配置漂移 / 覆盖缺口 / 体验
> 不可靠」的硬伤与缺口，给出**最小侵入、可逐项落码**的强化设计。每一项都标注根因（指向真实文件 / 行为）、修复
> 契约、改动点与验收用例，下游工程师可据此直接打补丁，无需再做设计决策。
>
> 本文档由 **Subtask #0（方案设计）** 产出，仅写设计、不写实现、不提交。Subtask #1（评审）将在 §0 追加
> 「评审记录」、文末追加「评审结论」并升 v2；Subtask #2（开发）按 §4 文件清单逐项落码，并把实施中发现的方案
> 缺陷回填 §10。

- **版本**：v2（Subtask #1 评审完成；逐项对照真实代码核验，已修复全部 P0/P1）
- **特性目录名**：`factor-layer-hardening`
- **作者**：Subtask #0（solution architect）；评审与 v2 修订：Subtask #1（senior reviewer）
- **状态**：有条件通过（详见文末「评审结论」）
- **基线**：commit `bb74ac3`（factor-layer v2 已落地）；本方案为其上的强化增量
- **依赖前提**：不改动既有原子策略 / combo / SimBroker / 回测·对比·WF 管线的对外行为；强化项均向后兼容
  （**含已保存的 factor_score 策略与因子组合**——见评审 R-P0-1 / R-P1-4 的向后兼容硬约束）

---

## 0. 评审记录（Review Notes，Subtask #1）

评审方式：把本方案逐项（H1..H9）对照「可行性 / 完整性 / 一致性 / 适度性」四维，并**逐文件核验**根因描述是否属实。

### 0.1 根因核验结论（先验证再评审，CLAUDE.md §七精神）

| 项 | 根因核验 | 结论 |
|---|---|---|
| H1 normWindow 漂移 | `service.score_set`(L188) / `score_inline`(L204) / `ScoreFactorComboTool`(L305-307) 均取 `thresholds.normWindow`，且 `resolve_set_to_spec`(L178) 只回 `{members,thresholds}` **丢弃 normStats**；前端 `train.mutate` 传 `config:{normWindow: thresholds.normWindow}`(L63)，`ThresholdsEditor` 训后可改 `normWindow` 且 `trained` 不重置 | **属实，且为线上活跃 bug**（normWindow 用户可见可改） |
| H1 clip 漂移 | 四处打分调用点确实从不传 `clip`；`service.train`(L153) 只读 `config.normWindow` 不读 clip | **属实但为潜在/防御性**：`clip` 在前后端**任何路径均未暴露**（`DEFAULT_THRESHOLDS`/`params_schema`/`ThresholdsEditor`/train config 都无 clip），实际恒为 4.0，故当前**不会**发生 clip 错配；穿线 clip 是单源化加固而非修活跃 bug（见 R-P2-1） |
| H2 快照丢 normStats | `resolve_set_to_spec` 确实不带 normStats；`createStrategy`(L88) 用可被事后改的 `thresholds` | 属实 |
| H3 双真源 | `createStrategy`(L86-90) 同传 `members`(weight=0) + 顶层 `weights`；`trained` 不随 `members` 变化失效（无 useEffect 重置） | 属实 |
| H4 enabled 无消费 | **部分不属实**：`FactorTable` 的激活 Switch **已**调 `PUT /factors/{id}`+`invalidate`(L21-25,59,129)；`list_factors(enabledOnly)` Agent 工具**已**生效(L89-91)；`bulk_create_factors` **已** `enabled=True`(L773)。但组合成员来源是**库目录**（`MemberPicker`/`FactorCatalog` 均迭代 `lib.data`）与**评估/挖掘**结果（`pendingMembers` 来自 `FactorEvalPanel`/`FactorMiningPanel` 的「加入组合」）——**已存「我的因子」从不进组合**，且 `FactorCatalog` 只有「加入我的因子」、**无**「加入组合」 | 见 R-P1-2 |
| H5 落库无去重 | `bulk_create_factors`(L768-780) 仅 `add_all` 无查重；`_mined_factor_values`(L110-114) 对 `ir=None` 仍落库 | 属实 |
| H6 短窗静默 | `factor_score.warmup`=`normWindow+60`(L69)；短窗→全 NaN→安静空仓无提示 | 属实（minBarsRequired 公式需修，见 R-P2-2） |
| H7 无溯源 | `train`(L161-163) 不回 provenance；`factor_sets.model_json`/`metrics_json` 无 instId/bar/区间/时间 | 属实（但 `factor_set_out` 已整体透传 `model`，provenance 落进 model_json 即自动可读，见 R-P2-3） |

### 0.2 评审发现（按严重度）

| 编号 | 维度 | 严重度 | 概述 | 处置 |
|---|---|---|---|---|
| R-P0-1 | 一致性/向后兼容 | **P0** | H3 改动 #2「删除 `prepare` 对 `self.params.get('weights')` 的依赖」会**静默打断已保存的 factor_score 策略**：旧快照是「`members.weight=0` + 顶层 `weights`」，删依赖后旧策略全员 0 权重→得分恒 NaN→不交易，且无任何报错——正是本方案要根除的「静默错算」 | 已就地修复：`prepare` **保留**传 `self.params.get("weights")`，`compose_score` 既有 `(weights or {}).get(name, member.get("weight"))` 同时兼容新（烘焙成员、无顶层 weights）与旧（顶层 weights、零成员权重）两种快照（见修订后 H3 + §6） |
| R-P1-2 | 一致性/可行性 | **P1** | H4 改动 #2 指向的 `MemberPicker`/`FactorCatalog「加入组合」`**取材自库目录、不取自已存因子**，`FactorCatalog` 更无「加入组合」入口——「过滤禁用的已存因子」无落点，`enabled` 对组合成员**无门可守**；按原文落码会改错组件或写出死代码 | 已重定义 H4 作用域（见修订后 H4）：`enabled` 的真实下游=`list_factors(enabledOnly)`（已生效）+「我的因子」可见性/默认态；删去对 `MemberPicker`/`FactorCatalog` 的过滤要求；若要让 `enabled` 真正约束组合成员，须新增「已存因子→组合」来源（属增量功能，列为放行条件 C-1，不在本轮强化范围） |
| R-P1-3 | 可行性 | **P1** | §7.2 DoD 的 `npm run lint`（`next lint`）**不可headless执行**：仓库无 `.eslintrc*`、`next.config.mjs` 亦未设 `eslint.ignoreDuringBuilds`，`next lint` 首次运行会**交互式**询问 ESLint 初始化方案→在 CI/无人值守下挂起；iteration1 实测门禁用的是 `tsc --noEmit`+`vitest`+`next build`，并非 lint | 已就地修正 DoD（见修订后 §7.2）：以 `npm run test`（vitest）+ `npm run build`（含 Next 内置 tsc 类型检查）为权威门禁，并保留显式 `npx tsc --noEmit` 作类型门；移除 `npm run lint` |
| R-P1-4 | 完整性/一致性 | **P1** | H3「权重单一真源」自相矛盾：改动 #1 的 `assert_weights_cover` 要查 `model.weights`，改动 #2/#3 又说「去顶层 weights」——若把组合的 `model.weights` 也删了，则陈旧校验与 `members_from_set_row` 的读时烘焙都断了 | 已澄清（见修订后 H3 + §6）：**单源是分层的**——「因子组合 `factor_sets.model_json.weights`」保留为存储侧真源（陈旧校验 + 读时经 `apply_weights` 烘焙进成员）；「`factor_score` 策略 `params_json` 快照」内**烘焙进成员 weight、不再写顶层 weights**（新建路径）。两者不构成双真源 |
| R-P2-1 | 适度性/准确性 | P2 | H1 把 clip 错配描述为活跃 bug（「非默认 clip 必然错配」），但 clip 在 UI/API 均未暴露、恒 4.0 | 已就地把 clip 部分注明为防御性单源化（修订后 H1）；改动保留（低成本、利于未来暴露 clip 时不踩坑） |
| R-P2-2 | 完整性/准确性 | P2 | H6 `minBarsRequired = normWindow + max(horizons)` 与打分实际暖机不符：score 端点不接收 horizons，`compose_score` 暖机≈`normWindow`，策略 `warmup`=`normWindow+60` | 已就地修正公式（修订后 H6）：score 端点 `minBarsRequired = normWindow`；并注明策略自身另有 +60 缓冲 |
| R-P2-3 | 适度性 | P2 | §4.1 列「`factor_set_out` 透传 provenance」为改动，但其已整体 `model: _load_json(model_json)` 透传全部 model 字段，provenance 落进 model_json 即自动可读 | 已注明该项为「无需改动，落 model_json 即可」（修订后 H7/§4.1） |
| R-P2-4 | 完整性 | P2 | H1 把 `score_inline` 列为接 `resolve_norm_contract` 的调用点，但未保存的内联组合**无 normStats 来源**（`score-preview` 请求体无该字段） | 已注明 `score_inline` 走 thresholds 回落分支即可（训后前端已锁 `normWindow`==`trained.normStats.normWindow`，clip 恒默认，故预览与落地一致）；如需精确可由 `score-preview` 请求体可选带 `normStats`，列为放行条件 C-2 |
| R-P2-5 | 适度性 | P2 | H4 改动 #1 要求补「乐观更新+失败回滚」，但现状已是 `PUT`+`onSettled` 失效，行为正确 | 降级为可选打磨；不阻断验收 |

P0/P1（R-P0-1、R-P1-2、R-P1-3、R-P1-4）已在下文正文就地修复并升 v2；P2 部分就地修复、余者列入「评审结论」放行条件。

---

## 1. 概述（Overview）

因子层已经能跑通「行情数据 → 因子计算 → 评估/挖掘 → 权重训练 → 组合得分 → `factor_score` 策略决策」的完整闭环，
且在纯计算层（`engine/factors/*`）严格遵守了「因果、空样本安全口径、绝不假数据、缺库显式降级」等项目铁律。但
**逐文件核验后发现：闭环里有三处会"静默产出错误结果"的契约漏洞，三处"配置漂移 / 需求未真正落地"的完备性缺口，
以及测试与前端可靠性上的若干薄弱点**。这些问题在「计算/评估/挖掘」这种一次性只读路径上不易暴露，却会在「训练→
保存→建策略→回测/实时」这条多跳、跨进程、跨会话的链路上累积放大——恰恰是全局目标「稳健、可靠、高成功率」最在意
的那条链路。

最关键的一条是**归一化契约未端到端闭合**（见 §3 H1/H2）：训练端 `build_factor_matrix` 把权重学在「因果滚动
z-score（窗口 `normWindow`、夹断 `clip`）」变换后的特征上，并把这两个参数作为"契约真相"写进 `normStats`；但所有
打分路径（`factor_score_strategy.prepare`、`service.score_set`、`service.score_inline`、`score_factor_combo` 工具）
在打分时却是从 `thresholds.normWindow` 取窗口、且**从不传 `clip`（恒用默认 4.0）**，完全无视 `normStats` 里记录的
训练真相。更糟的是前端 `FactorSetBuilder` 的阈值编辑器渲染在"训练结果"之后，用户**可以在训练完成后再改
`normWindow` 而不重训**，于是"权重学在窗口 200 的特征上、却被作用到窗口 500 归一化的特征上"这种 v2 评审 C-P0-1
本想根除的错配，会经由 UI 与跨路径取值不一致**悄悄复活**——无报错、回测照跑、结果却是错的。

第二类问题是**需求里"激活/取消"未真正生效**与**权重存在两个真相**：`Factor.enabled` 目前只是个列表过滤标志，没有
任何下游消费它，"取消激活"一个因子在功能上等于什么都没做；而组合权重同时存在于「成员 `weight` 字段」与「策略
顶层 `weights` 字典」两处，`createStrategy` 同时透传二者，一旦成员在训练后被增删，二者按 `member_names` 错位对齐就会
让新成员**被静默赋 0 权重**。本方案把这两处收敛为单一真源并补齐语义。第三类是**挖掘落库会累积重复行、会把无效
（IR=None）因子也存进"我的因子"**、`factor_score` 在短窗口（WF/Paper/小区间）下会**静默空仓不交易却无任何提示**，
以及**训练缺溯源元数据**导致无法判断权重是否陈旧。最后补齐 mining 等测试缺口与前端 NaN/空/错误态的可靠性。所有
强化项均为加固，不引入新强依赖、不改既有对外契约。

---

## 2. 背景与现状核验（Ground Truth）

下表是 Subtask #2 落码前必须知道的"代码现状"——根因均已在真实文件中核验（行为描述，非臆测）：

| 现状 | 位置（已核验） | 影响 |
|---|---|---|
| 打分窗口取自 `thresholds.normWindow`，**忽略** `model.normStats.normWindow` | `factor_score_strategy.prepare`、`service.score_set/score_inline`、`score_factor_combo` 工具 | normWindow 漂移 → 权重错配（H1/H2） |
| `compose_score(..., clip=DEFAULT_CLIP)` 的 `clip` 在所有调用点**从不被传入**，恒为 4.0 | 同上四处调用点 | clip 契约形同虚设，非默认 clip 必然错配（H1） |
| `train` 服务读 `config.get("normWindow", 200)`，但**不读 `config.clip`** | `factors/service.py:train` | 训练端 clip 也恒 4.0，且与打分端无握手（H1） |
| 训练后改 `normWindow` 不强制重训；`trained` 不随成员变化失效 | `FactorSetBuilder.tsx`（ThresholdsEditor 在训练结果之后渲染） | 用户可制造 train↔score 窗口错配（H2/H3） |
| `Factor.enabled` 无任何下游消费（只用于 `list_factors(enabled=?)` 过滤） | `api/factors.py`、`repositories.list_factors` | 需求"激活/取消"未真正落地（H4） |
| 组合权重存在两处真相：成员 `weight` 与策略顶层 `weights` | `FactorSetBuilder.createStrategy`、`factor_score_strategy.prepare` | 成员变更后静默 0 权重（H3） |
| `bulk_create_factors(_mined_factor_values(items))` 无去重；含 `ir=None` 也落库 | `factors/service.py:mine/_mined_factor_values` | "我的因子"累积重复 / 无效行（H5） |
| `factor_score` warmup=`normWindow+60`；短窗口→全 NaN→**静默空仓无提示** | `factor_score_strategy.warmup/prepare/on_candle` | WF/Paper/小区间不交易且无警示（H6） |
| `factor_sets` 不记录训练区间/时间（`metrics_json` 只有 IC） | `repositories.FactorSetValues`、`factor_set_out` | 无法判断权重陈旧（H7） |
| 无 `test_factor_mining.py`；归一化契约/权重单源/clip/enabled 无端到端用例 | `services/quant/tests/` | 覆盖缺口 → 回归风险（H8） |

---

## 3. 技术设计（Technical Design）

设计原则：**单一真源**（契约参数只在一处定义、各处取同值）、**显式失败**（漂移/陈旧/短窗在边界报错或如实标注，
绝不静默）、**最小侵入**（优先复用既有 helper 与 JSON 列，避免表迁移与对外契约破坏）。

### H1（P0）归一化契约端到端闭合：`normWindow` + `clip` 单一真源

**根因**：`normStats={normWindow,clip}` 是"权重训练所依赖的归一化变换"的唯一真相，但打分侧从 `thresholds` 取
`normWindow`、且对 `clip` 一律用默认值，二者一旦与 `normStats` 不一致，就把已训练权重作用到了不同量纲的特征上
（v2 C-P0-1 的复活路径）。

**修复契约**：打分阶段的归一化变换参数 `(normWindow, clip)` **必须等于权重训练时写入 `normStats` 的值**；当组合无
训练权重（`equal` 基线或未训练）时才回落到 `thresholds.normWindow` + `DEFAULT_CLIP`。

> **评审修订（R-P2-1）**：`normWindow` 漂移是**活跃 bug**（用户可在训练后经 `ThresholdsEditor` 改窗口而不重训），
> 必须修；`clip` 漂移目前是**潜在/防御性**——`clip` 在前后端任何路径均未暴露（恒 4.0），实际不会错配。穿线 `clip`
> 的价值在于「单一真源」一旦未来暴露 clip 不会重蹈覆辙，成本极低，故保留为加固项，但本项的**线上收益主要来自
> `normWindow` 一致性**，文案不再把 clip 描述为现存错算。

**改动**：
1. `engine/factors/scoring.py`：新增纯函数 `resolve_norm_contract(*, norm_stats: dict | None, thresholds: dict) ->
   tuple[int, float]`——`norm_stats` 含 `normWindow` 时返回 `(normStats.normWindow, normStats.clip|DEFAULT_CLIP)`，
   否则返回 `(thresholds.normWindow, DEFAULT_CLIP)`。该函数是打分窗口/clip 的**唯一解析口径**。
2. 所有 `compose_score(...)` 调用点显式传 `clip`：`factor_score_strategy.prepare`、`service.score_set`、
   `service.score_inline`、`agent/tools/factor_tools.ScoreFactorComboTool.execute` 四处，统一改为先调
   `resolve_norm_contract` 拿 `(norm_window, clip)` 再传给 `compose_score`。
3. `factors/service.py:train`：读取并应用 `config.get("clip", DEFAULT_CLIP)`，把 `clip` 一并写进
   `build_factor_matrix(..., clip=clip)` 与返回的 `normStats`（`build_factor_matrix` 已支持 `clip` 形参，仅需在
   service 传入并落进 `normStats`）。
4. `factor_score` 策略的内联快照新增 `normStats` 字段（见 H2 的快照扩展），`prepare` 经 `resolve_norm_contract`
   从快照取契约。

### H2（P0）训练↔打分 `normWindow` 一致性强制 + 快照携带 `normStats`

**根因**：`resolve_set_to_spec` 只返回 `{members, thresholds}`，**丢掉了 `model.normStats`**；`createStrategy` 把
可被用户事后改动的 `thresholds.normWindow` 当成打分窗口，于是 train↔score 窗口可不一致且无人拦截。

**修复契约**：保存组合 / 从组合建策略时，若该组合已有训练权重，则 `thresholds.normWindow` 必须等于
`model.normStats.normWindow`，否则在边界**显式 400 `FACTOR_NORM_WINDOW_MISMATCH`**（C1 边界一次性校验）；快照
必须把 `normStats` 一并内联，使引擎层零 IO 即可拿到契约真相。

**改动**：
1. `factors/service.py:resolve_set_to_spec`：返回 `{members, thresholds, normStats}`（`normStats` 取自
   `model_json.normStats`，缺失为 `{}`）。
2. `factors/specs.py`：新增 `assert_norm_contract(thresholds, model) -> None`——当 `model.normStats.normWindow`
   存在且 `!= thresholds.normWindow` 时抛 400 `FACTOR_NORM_WINDOW_MISMATCH`；在 `create_factor_set` /
   `update_factor_set` 路由校验链中调用。
3. 创建 `factor_score` 策略入参从 `{members, weights, thresholds}` 改为 `{members(已烘焙权重), thresholds, normStats}`
   （见 H3 去顶层 weights）；`prepare` 经 `resolve_norm_contract(norm_stats=params.get("normStats"),
   thresholds=resolve_thresholds(params))` 取窗口与 clip。
4. 前端 `FactorSetBuilder`：(a) 训练成功后把 `thresholds.normWindow` **锁定**为 `trained.normStats.normWindow`
   并在 ThresholdsEditor 对该字段置只读 + 提示「归一化窗口由训练锁定，改窗口需重新训练」；(b) `createStrategy`
   入参带上 `normStats: trained?.normStats`。

### H3（P1）组合权重单一真源 + 成员变更使训练结果失效

**根因**：权重同时存在于成员 `weight` 与策略顶层 `weights`；`createStrategy` 同传二者，且训练后允许增删成员而
`trained` 不失效，按 `member_names` 错位对齐 → 新增成员被静默赋 0 权重、训练无效却无提示。

**修复契约（评审 R-P1-4 澄清：单源是分层的，不是"到处删 weights"）**：
- **因子组合（`factor_sets.model_json.weights`）**：保留为**存储侧权重真源**。读时 `members_from_set_row` 已经
  经 `apply_weights` 把它烘焙进成员（现状即如此），`assert_weights_cover` 用它做陈旧校验。**不删。**
- **`factor_score` 策略（`params_json` 内联快照）**：新建路径把训练权重**烘焙进各成员 `weight`、不再写顶层
  `weights`**。这是"新建快照"的单源；不是删除引擎读取顶层 weights 的能力。
- 任何时刻 `trained` 必须与"当前成员集合的 `member_names`"一致，否则视为陈旧、禁止保存/建策略（前端预校验 + 后端 400 兜底）。

**改动**：
1. `factors/specs.py`：新增 `assert_weights_cover(members, model) -> None`——若 `model.weights` 存在且其键集
   `!= member_names(members)`，抛 400 `FACTOR_WEIGHTS_STALE`（提示"成员已变化，请重新训练"）。在保存/更新组合校验链调用
   （`model` 缺省或无 `weights` 时跳过，兼容 equal/未训练与 partial update）。
2. `factor_score_strategy.prepare`：**保留**传 `self.params.get("weights")` —— `compose_score` 既有
   `(weights or {}).get(name, member.get("weight"))` 已**同时兼容**新快照（烘焙成员、`weights=None`→取 `member.weight`）
   与旧快照（顶层 `weights`、`member.weight=0`→取顶层）。
   > **评审修订（R-P0-1，向后兼容硬约束）**：原 v1 写"删除对 `self.params.get('weights')` 的依赖"会**静默打断
   > 所有已保存的 factor_score 策略**（旧快照 `member.weight=0` + 顶层 `weights`；删依赖后全员 0 权重→得分恒 NaN→
   > 不交易、无报错）。**绝不删此读取路径**；新建路径不再"写"顶层 weights 即可达到"不再制造双真源"的目的，读侧对
   > 历史快照保持兼容。`default_params` 本就无顶层 `weights`（已核验），无需改动。
3. 前端 `FactorSetBuilder`：(a) `members` 变化时 `setTrained(null)`（`useEffect` 依赖 `members`），强制重训；
   (b) `createStrategy` 入参 `members` 改为 `applyWeights(members, trained.weights)` 后的烘焙结果、**不再传顶层
   `weights`**；(c) 未训练（或陈旧）时禁用"保存组合 / 从组合新建策略"并提示原因。
4. `apps/web/lib/factor.ts`：新增纯函数 `applyWeights(members, weights)` 与 `weightsCoverMembers(members, weights)`
   （与后端 `member_names` 同口径），配套单测。

### H4（P1）"激活/取消"语义：按真实架构精确定义并钉死（评审 R-P1-2 重定义）

**根因核验**：需求要求"激活/取消"，`Factor.enabled` 已有部分下游（见下），但 v1 设计把过滤点指向了**取材自库目录、
不取材自已存因子**的组件，落点错误。

> **评审修订（R-P1-2）**：核验后，组合成员的**全部来源**是：`MemberPicker`（迭代库目录 `lib.data`）、
> `FactorEvalPanel`/`FactorMiningPanel` 的「加入组合」（经 `wb.addPendingMember` 注入评估/挖掘出的候选）。
> **已存「我的因子」（`FactorTable`）从不进入组合**；`FactorCatalog` 只有「加入我的因子」，**没有**「加入组合」。
> 因此 v1「从 `MemberPicker`/`FactorCatalog加入组合` 过滤禁用的已存因子」**无落点**——按原文落码只会改错组件或写死代码。

**修复契约（重定义 `enabled` 真实作用域）**：`enabled` 是「已存因子行」的**策展/可见性**标志，其**真实且唯一连贯的下游**为：
(a) `list_factors` Agent 工具 `enabledOnly` 只回激活项（**已生效**，复核 + 补测试即可）；(b)「我的因子」表的默认视图/筛选；
(c) 挖掘"保存为我的因子"默认落激活态（`bulk_create_factors` **已** `enabled=True`，复核即可）。**因子库
（`FACTOR_LIBRARY`）与组合成员来源（库目录 / 评估 / 挖掘候选）不受 `enabled` 影响**——避免误伤可发现性。

**改动（收敛为"复核 + 文档化 + 补测"，不改错组件）**：
1. 前端 `FactorTable.tsx`：**复核**"激活/取消" `Switch` 已调 `PUT /api/factors/{id} {enabled}` + `invalidateQueries`
   （**已具备**：`update` mutation + `onSettled` 失效，见核验表）。乐观更新+回滚降级为可选打磨（R-P2-5），不阻断验收。
2. `agent/tools/factor_tools.ListFactorsTool`：复核 `enabledOnly` 语义并在 summary 注明"仅激活/全部"（**已具备**，加测试钉死）。
3. 文档化：在 §5 接口表精确注明 `enabled` 作用域为"已存因子行的策展标志 + Agent 池过滤"，**不**约束库目录与组合成员来源，
   避免下游误解为"禁用即从所有路径消失"。
4. **（放行条件 C-1，非本轮强化范围）**：若产品上希望 `enabled` 能真正约束"哪些因子可进组合"，需新增"已存因子→组合"
   的成员来源（如在 `MemberPicker` 旁加"从我的因子选择"并按 `enabled` 过滤）。这属**增量功能**而非加固，超出"最小侵入、
   不重写因子层"的本轮定位，列为后续条件。

### H5（P1）挖掘落库去重 + 无效因子不落库 + 计数透明

**根因**：`mine(persist=true)` 直接 `bulk_create_factors`，重复挖掘累积重复行；`ir=None` 的占位项也会被当作
top-N 落库为 `source="mined"`。

**修复契约**：落库前**去重**（同 `factor_key` + 规范化 `params` + `direction` 视为同一因子，已存在即跳过）且**过滤
无效项**（`ir is None` 或 `coverage < MIN_COVERAGE` 不落库）；响应回 `evaluated/persisted/skipped` 计数，**绝不静默
丢弃**（与 v2"绝不静默截断"一脉相承）。

**改动**：
1. `engine/factors/mining.py`：新增 `MINE_PERSIST_MIN_ABS_IR = 0.0`（默认仅过滤 None，可调）与 `MINE_MIN_COVERAGE`。
2. `factors/service.py:_mined_factor_values`：跳过 `ir is None` 与 `bar_incompatible`（后者已跳）；对 params 做规范化
   JSON key 排序生成 `dedupe_key`。
3. `db/repositories.py`：新增 `existing_mined_keys(session) -> set[str]` 或在 `bulk_create_factors` 内
   query-before-insert，跳过已存在的 `(factor_key, params_json_canonical, direction)`；返回实际写入条数。
4. `factors/service.py:mine` 响应新增 `{evaluated, persisted, skipped}`；`schemas` 与前端 `FactorMiningPanel` 同步展示。

### H6（P1）短窗口护栏：`factor_score` 不交易时如实标注

**根因**：`factor_score` warmup=`normWindow+60`（默认 260 根）。在 WF 窗口、Paper、短区间下，有效根数 < warmup → 全
NaN → 安静空仓。安静空仓在 compare 混排里是对的（不抛错），但**完全无提示**会让用户误以为"策略没信号"而非"窗口太短"。

**修复契约**：打分/预览响应在 `coverage==0`（无任何有效得分点）时返回 `note:"window_too_short"`（含所需最小根数）；
前端在因子得分图与 `factor_score` 策略详情显式提示"区间过短，至少需要 N 根"。引擎运行期行为不变（仍安静空仓），只是
**可观测**。

**改动**：
1. `factors/service.py:score_set/score_inline`：当 `not points` 时在返回体加 `note:"window_too_short"`,
   `minBarsRequired = normWindow`（取契约窗口）。
   > **评审修订（R-P2-2）**：v1 写 `normWindow + max(horizons)` 不符实际——**打分端点不接收 horizons**（horizons 仅用于
   > 评估/训练打标签），`compose_score` 的暖机就是滚动窗口 `normWindow`（`min_periods=window`），故 score 端点的最小根数
   > 是 `normWindow`。`factor_score` **策略**自身另有固定缓冲 `warmup=normWindow+60`，若要在策略详情提示，用策略口径的
   > `normWindow+60`；两个口径不要混用。
2. `engine/factors/scoring.py:score_points` 调用方据 `len(points)` 判定；保留纯函数无副作用。
3. 前端 `FactorScoreChart.tsx` 与 `StrategyDetail.tsx`（`kind==="factor"` 分支）渲染该 `note` 为黄色提示条。
4. 可选（落地条件）：`agent/services` 在 WF/compare 规划阶段，对 `kind=="factor"` 且单窗根数 < warmup 的情形附
   `notes`（沿用既有 combo `notes` 机制），不阻断、仅提示。

### H7（P2）训练溯源元数据 + 陈旧提示

**根因**：`factor_sets` 不记录训练所用 `instId/bar/区间/时间`，无法判断权重是否陈旧。

**修复契约**：训练产物附带溯源 `provenance = {instId, bar, startTs, endTs, horizon}`，连同 `trainedAt`（由调用层
注入秒级 UTC，引擎纯函数不取时钟）写入 `model_json`（**复用既有 JSON 列，零表迁移**）；前端展示"训练于 X 标的/周期
/区间"，并在 `icOut << icIn` 或样本不足时延续既有 `overfitWarning` 提示。

**改动**：
1. `factors/service.py:train` 返回体增 `provenance`（由 `FactorWindow` 与 `horizon` 组装；`trainedAt` 由路由/前端注入）。
2. 前端 `FactorSetBuilder.save`：把 `provenance` 并进 `modelJson`；详情/列表展示。
   > **评审修订（R-P2-3）**：`factor_set_out` **已**整体 `model: _load_json(model_json, {})` 透传全部 model 字段，
   > provenance 只要落进 `model_json` 即自动可读，**无需改 `factor_set_out`**；§4.1 该行同步标注为"无需改动"。
3. `apps/web/lib/factor.ts:overfitWarning` 扩展为同时覆盖"样本外显著低于样本内"与"样本量过小"，配套测试。

### H8（P1）测试缺口补齐

**根因**：mining 无专项测试；H1/H2/H3 的契约修复必须有"能稳定复现旧 bug"的回归测试护栏（CLAUDE.md §七：修
bug 先写复现测试）。

**改动**（新增/扩充测试，见 §7）：`test_factor_mining.py`（新建）、`test_factor_scoring.py`/`test_factor_training.py`
扩充契约用例、`test_factors_api.py` 扩充 400 错误码用例、前端 `lib/factor.test.ts` 扩充权重/契约纯函数用例。

### H9（P2）前端可靠性（NaN / 空 / 错误态）

**根因**：评估面板、得分图、训练结果在 IC/IR/t 为 `None`、得分序列为空、训练/打分失败时需统一、克制、不崩的呈现。

**改动**：`FactorEvalPanel.tsx` 对 `null` 统计渲染"—/样本不足"（复核并补齐）；`FactorScoreChart.tsx` 空序列渲染空态而
非空图；`FactorSetBuilder` 训练/保存/建策略失败已具错误提示（复核文案与可重试性）；所有 `tabular` 数字列对 `null`
兜底。这些为 UI 体验加固，不改数据契约。

---

## 4. 文件 / 模块变更计划（File / Module Change Plan）

### 4.1 后端（services/quant）

| 文件 | 新建/修改 | 一句话意图 |
|---|---|---|
| `app/engine/factors/scoring.py` | 修改 | 新增 `resolve_norm_contract`；`compose_score` 调用点统一传 `clip`（H1）。 |
| `app/engine/factors/__init__.py` | 修改 | 导出 `resolve_norm_contract`（聚合真源）。 |
| `app/engine/factors/training.py` | 修改 | `train_weights`/`build_factor_matrix` 链路确保 `clip` 进 `normStats`（H1）。 |
| `app/engine/factors/mining.py` | 修改 | 新增落库过滤阈值常量 `MINE_PERSIST_MIN_ABS_IR`/`MINE_MIN_COVERAGE`（H5）。 |
| `app/strategies/factor_score_strategy.py` | 修改 | `prepare` 经 `resolve_norm_contract` 取窗口/clip；**保留**传 `self.params.get("weights")` 以兼容旧快照（R-P0-1），不删读取路径（H1/H2/H3）。 |
| `app/factors/service.py` | 修改 | `train` 应用并回传 `clip`+`provenance`；`resolve_set_to_spec` 带 `normStats`；`score_*` 传 clip + `window_too_short` note；`_mined_factor_values` 过滤无效项（H1/H2/H5/H6/H7）。 |
| `app/factors/specs.py` | 修改 | 新增 `assert_norm_contract`、`assert_weights_cover`；在保存/更新组合校验链调用（H2/H3）。 |
| `app/db/repositories.py` | 修改 | `bulk_create_factors` query-before-insert 去重并回写入条数（H5）。 |
| `app/api/factors.py` | 修改 | 创建/更新组合路由接入 `assert_norm_contract`/`assert_weights_cover`；mine 响应回计数；新错误码（H2/H3/H5）。 |
| `app/schemas.py` | 修改 | mine 响应增 `evaluated/persisted/skipped`；train 响应增 `provenance`（H5/H7）。`factor_set_out` **无需改**——已整体透传 `model`，provenance 落 `model_json` 即可读（R-P2-3）。 |
| `app/agent/tools/factor_tools.py` | 修改 | `ScoreFactorComboTool` 经 `resolve_norm_contract` 取契约（含 clip）；`ListFactorsTool` enabled 语义复核（H1/H4）。 |

### 4.2 前端（apps/web）

| 文件 | 新建/修改 | 一句话意图 |
|---|---|---|
| `lib/factor.ts` | 修改 | 新增 `applyWeights`/`weightsCoverMembers`；扩展 `overfitWarning`（H3/H7）。 |
| `lib/factorTypes.ts` | 修改 | `TrainResult` 增 `provenance`；`FactorSet` 透传 `provenance`；mine 响应类型增计数（H5/H7）。 |
| `lib/factorApi.ts` | 修改 | 类型对齐新响应字段（H5/H7）。 |
| `components/Factor/FactorSetBuilder.tsx` | 修改 | 成员变更使 `trained` 失效；锁定 normWindow；烘焙权重去顶层 `weights`；带 `normStats`/`provenance`；未训练禁用保存（H2/H3/H7）。 |
| `components/Factor/FactorTable.tsx` | 复核 | "激活/取消" `Switch` **已**调 `PUT`+失效（核验通过）；乐观更新+回滚为可选打磨（R-P2-5），不阻断验收（H4）。 |
| ~~`components/Factor/FactorCatalog.tsx`~~ | **撤销** | v1 的"成员来源过滤禁用因子"无落点（`FactorCatalog`/`MemberPicker` 取材自库目录，非已存因子）——评审 R-P1-2 删除此项。 |
| `components/Factor/FactorMiningPanel.tsx` | 修改 | 展示 `evaluated/persisted/skipped` 计数（H5）。 |
| `components/Factor/FactorScoreChart.tsx` | 修改 | 空序列空态 + `window_too_short` 提示（H6/H9）。 |
| `components/Factor/FactorEvalPanel.tsx` | 修改 | `null` 统计统一"—/样本不足"兜底（H9）。 |
| `components/Strategy/StrategyDetail.tsx` | 修改 | `kind==="factor"` 分支展示 `window_too_short`/陈旧提示（H6/H7）。 |

### 4.3 测试

| 文件 | 新建/修改 | 意图 |
|---|---|---|
| `services/quant/tests/test_factor_mining.py` | 新建 | 413 工作量护栏 / bar 不兼容跳过 / `abs(IR)` 降序 / 落库去重 / None-IR 不落库 / 计数（H5/H8）。 |
| `services/quant/tests/test_factor_scoring.py` | 修改 | `resolve_norm_contract` 优先 `normStats`；clip 端到端一致；契约错配可复现（H1/H8）。 |
| `services/quant/tests/test_factor_training.py` | 修改 | `clip` 进 `normStats`；`provenance` 字段（H1/H7）。 |
| `services/quant/tests/test_factors_api.py` | 修改 | `FACTOR_NORM_WINDOW_MISMATCH`/`FACTOR_WEIGHTS_STALE` 400；mine 计数；enabled 过滤（H2/H3/H4/H5）。 |
| `services/quant/tests/test_factor_score_strategy.py` | 修改 | 快照带 `normStats` 时打分用训练窗口；新快照（成员烘焙权重、无顶层 weights）正确；**旧快照（顶层 weights+零成员权重、无 normStats）仍非全 NaN**（R-P0-1）（H1/H3）。 |
| `apps/web/lib/factor.test.ts` | 修改 | `applyWeights`/`weightsCoverMembers`/`overfitWarning` 正常+异常路径（H3/H7）。 |

---

## 5. 接口设计（Interface Design）

沿用既有路由前缀 `/api`、camelCase、秒级 UTC、错误体 `{error:{code,message}}`。本轮仅**新增错误码与响应字段**，
不改既有路径与请求体，向后兼容。

- **新增错误码**（均 HTTP 400）：
  - `FACTOR_NORM_WINDOW_MISMATCH` — 已训练组合的 `thresholds.normWindow != model.normStats.normWindow`（H2）。
  - `FACTOR_WEIGHTS_STALE` — `model.weights` 键集与当前成员 `member_names` 不一致（H3）。
- **`POST /api/factors/mine` 响应**新增计数（H5）：
  `{items:[RankedFactor], truncated, evaluated:int, persisted:int, skipped:int}`（`truncated` 语义不变，仅指 topN 截断）。
- **`POST /api/factor-sets/train` 响应**新增（H1/H7）：
  `{method, weights, normStats:{normWindow,clip}, metrics, provenance:{instId,bar,startTs,endTs,horizon}, available, note}`。
- **`POST /api/factor-sets/{id}/score` 与 `/score-preview` 响应**：空结果时新增
  `note:"window_too_short"` + `minBarsRequired:int`（H6）。
- **`enabled` 语义说明**（文档化，H4，按 R-P1-2 修正）：作用域仅限"已存因子行"的**策展/可见性**——真实下游为
  `list_factors(enabledOnly)` Agent 池过滤 + "我的因子"表默认视图；**不**约束 `FACTOR_LIBRARY` 目录、也**不**约束组合
  成员来源（库目录 / 评估 / 挖掘候选）。"禁用因子不能进组合"需另加"已存因子→组合"来源，属增量（放行条件 C-1）。
- **Agent 工具**：签名不变；`score_factor_combo` 内部改用 `resolve_norm_contract` 取窗口/clip（行为更正，对外契约不变）。

---

## 6. 数据模型（Data Model）

**零表结构迁移**（继续走 `Base.metadata.create_all` 幂等建表，本轮不新增列）。所有新增字段复用既有 `Text` JSON 列：

- `factor_sets.model_json`（既有 `Text`）扩展为：
  `{"weights":{name:float}, "normStats":{"normWindow":int,"clip":float}, "trainerVersion":str|None,
    "provenance":{"instId":str,"bar":str,"startTs":int,"endTs":int,"horizon":int}, "trainedAt":int}`。
  解析时缺字段一律安全回落（`members_from_set_row`/`thresholds_from_set_row` 已具 `json.JSONDecodeError` 兜底，按同范式扩展）。
- `factor_score` 策略 `params_json`（自包含内联快照）**新建路径**写为
  `{members(已烘焙 weight), thresholds, normStats:{normWindow,clip}}`；新建快照不再写顶层 `weights`（H3）。
  > **向后兼容硬约束（R-P0-1）**：**已保存的旧快照**形如 `{members(weight=0), weights:{...}, thresholds}` 且无
  > `normStats`。`prepare` 必须继续把 `self.params.get("weights")` 传给 `compose_score`：旧快照→走顶层 weights；
  > 新快照（无顶层 weights）→走成员烘焙 weight。`normStats` 缺失则 `resolve_norm_contract` 回落 `thresholds.normWindow`
  > +默认 clip。**绝不能因"去顶层 weights"而让旧策略全员 0 权重静默不交易。**
- `factors` 行（去重键，内存层）：`dedupe_key = factor_key + "|" + canonical_json(params) + "|" + direction`，仅用于
  落库前查重，不持久化为独立列（H5）。

内存 dataclass：`TrainResult` 增 `provenance: dict`（默认空）；mining 响应 dict 增 `evaluated/persisted/skipped`。
向后兼容：旧 `factor_sets`（`model_json` 无 `normStats`）走 `resolve_norm_contract` 的回落分支（用 `thresholds.normWindow`
+ 默认 clip），行为与现状一致，不破坏既有保存的组合。

---

## 7. 测试与验收标准（Testing & Acceptance Criteria）

### 7.1 关键回归用例（先写能复现旧 bug 的测试，再改业务，CLAUDE.md §七）

1. **归一化契约（H1/H2）**：构造一组成员，以 `normWindow=W1` 训练得权重；分别用 `normStats.normWindow=W1` 与
   `thresholds.normWindow=W2(≠W1)` 走打分，断言打分**采用 W1**（`resolve_norm_contract` 优先 `normStats`）；断言
   保存/建策略时 `W2≠W1` 触发 `FACTOR_NORM_WINDOW_MISMATCH` 400。clip 同理：非默认 clip 训练后打分采用同一 clip。
2. **权重单一真源（H3）**：训练后向成员追加一项，断言保存触发 `FACTOR_WEIGHTS_STALE` 400；烘焙权重后建策略，
   `prepare` 在无顶层 `weights` 时打分正确（与显式 weights 等价）。
2b. **旧快照向后兼容（R-P0-1，必须先复现再护栏）**：构造旧格式 `params_json`（`members[].weight=0` + 顶层
   `weights={name:w}`、无 `normStats`），断言 `FactorScoreStrategy.prepare` 仍按顶层 weights 算出**非全 NaN** 的得分、
   能正常多空（钉死"删顶层 weights 读取路径"这一回归绝不复发）。
3. **激活/取消（H4）**：禁用某已存因子后，断言其不出现在"可选成员"来源、`list_factors(enabledOnly=true)` 不含它；
   库目录与挖掘候选仍含其 key。
4. **挖掘落库（H5）**：连续两次 `mine(persist=true)` 同区间，断言第二次 `persisted=0`（全部去重）；含 `ir=None` 候选
   不落库；响应 `evaluated/persisted/skipped` 自洽（`evaluated == persisted + skipped + 已存`）；超量仍 413。
5. **短窗护栏（H6）**：用 < `normWindow+horizon` 根的区间打分，断言响应 `note=="window_too_short"` 且
   `minBarsRequired` 正确；引擎仍 0 成交不抛错。
6. **溯源（H7）**：训练响应含 `provenance`；保存后 `factor_set_out` 可读回；`overfitWarning` 在 `icOut<<icIn`/样本不足触发。
7. **前端纯函数（H3/H7/H9）**：`applyWeights`/`weightsCoverMembers`/`overfitWarning` 正常 + 异常路径；组件测试置
   `NODE_ENV=test`（既有约定）。

### 7.2 验收标准（Definition of Done）

1. 后端 `pytest`（含新增 `test_factor_mining.py` 与全部契约回归用例）全绿；前端 `npm run test`（vitest，组件测试置
   `NODE_ENV=test`）+ `npm run build`（Next 内置 tsc 类型检查）全绿，必要时再跑 `npx tsc --noEmit` 作显式类型门；
   既有 461/395 用例**零回归**。
   > **评审修订（R-P1-3）**：**不使用 `npm run lint`** —— 仓库无 `.eslintrc*`、`next.config.mjs` 未设
   > `eslint.ignoreDuringBuilds`，`next lint` 首次运行会进入**交互式** ESLint 初始化、在 CI/无人值守下挂起；
   > iteration1 实测门禁亦为 `tsc --noEmit`+`vitest`+`next build`，本轮沿用。
2. 真实 OKX 数据下：训练→保存→建 `factor_score` 策略→既有回测/对比/WF 全链路，**打分归一化窗口/clip 与训练严格一致**；
   人为制造窗口/权重漂移会被 400 拦截而非静默错算。
3. 禁用某因子后其在 `list_factors(enabledOnly)` Agent 池与"我的因子"默认视图中消失（**组合成员来源不受 `enabled`
   约束**，见 H4/R-P1-2）；挖掘重复落库被去重、无效因子不落库且计数透明。
4. 短区间下 `factor_score`/得分图给出"区间过短"明确提示，而非静默空白。
5. 已保存的旧组合（无 `normStats`）继续可用（回落分支）；**已保存的旧 `factor_score` 策略（顶层 weights+零成员权重）
   继续按原权重交易**（R-P0-1），均无破坏性变更。

---

## 8. 风险与缓解（Risks & Mitigations）

| 风险 | 影响 | 缓解 |
|---|---|---|
| 锁定 `normWindow` 改变用户既有交互预期 | 用户想单独调窗口被拦 | 提示文案明确"改窗口=重训"，并提供一键重训；`equal` 等无训练权重组合不锁定。 |
| `resolve_norm_contract` 回落分支与旧组合语义差异 | 旧组合打分轻微变化 | 旧组合 `normStats` 缺失即回落到 `thresholds.normWindow`+默认 clip，与现状**逐字一致**；加测试钉死。 |
| 落库去重的 `params` 规范化不稳定（键序/浮点） | 误判重复或漏判 | 用排序键 + 数值规范化（`json.dumps(sort_keys=True)`）；仅对 `source=="mined"` 生效，custom 不受影响。 |
| 新增 400 错误码打断既有前端流程 | 保存/建策略报错 | 前端在保存前本地预校验（`weightsCoverMembers`/窗口一致）并禁用按钮，400 仅作后端兜底；错误体可读可重试。 |
| `provenance.trainedAt` 取时钟 | 纯函数取时钟破坏可测性 | 时钟只在路由/前端注入（引擎纯函数不取 `time`），与既有 `signal_edge` 因果纪律一致。 |
| 短窗 `note` 增加响应字段 | 旧前端忽略新字段 | 新字段可选、缺省安全；旧前端不读即与现状一致。 |
| **删顶层 weights 读取路径打断旧策略**（R-P0-1） | 旧 factor_score 策略全员 0 权重→静默不交易 | `prepare` 永久保留传 `self.params.get("weights")`；新建快照只是"不再写"顶层 weights；§7.1 #2b 旧快照回归测试钉死。 |
| 新增 400 校验对 partial update / equal 组合误伤 | 合法保存被拦 | `assert_norm_contract`/`assert_weights_cover` 仅在 `model.normStats.normWindow`/`model.weights` 存在时才校验，缺省/未训练直接放行。 |
| 范围蔓延（误把它做成"重写因子层"） | 偏离"强化"定位 | 严守"最小侵入、零表迁移、零对外契约破坏"；任何新强依赖、新表、新路由一律出局。 |

---

## 9. 实现顺序（建议）

1. **后端契约层先行**：`scoring.resolve_norm_contract` + 四处打分调用点传 clip（H1）→ `resolve_set_to_spec` 带
   `normStats` + `assert_norm_contract`/`assert_weights_cover`（H2/H3）→ 配套后端测试先复现旧 bug 再修。
2. **挖掘与落库**：`mining` 阈值 + `repositories` 去重 + service 计数（H5）+ `test_factor_mining.py`。
3. **可观测性**：短窗 `note`（H6）+ 溯源 `provenance`（H7）。
4. **前端跟进**：`FactorSetBuilder` 锁窗/失效/烘焙（H2/H3）→ `FactorTable` 激活态复核（H4）→ 计数/提示/空态（H5/H6/H9）。
5. **门禁**：后端 `pytest`、前端 `vitest`（`npm run test`）+ `next build`（+ `npx tsc --noEmit`）全绿（**不跑 `npm run lint`**，R-P1-3），确认既有用例零回归。

每一步均可独立交付且不破坏既有行为；后端契约层（H1/H2/H3）是直接服务全局目标"稳健可靠"的核心，优先级最高。

---

## 10. 实施过程发现的方案缺陷（由 Subtask #2 回填）

落码逐项核对后，发现并处置如下与代码现实的偏差（均为「变更计划列了某文件但无需改动」或「契约口径需澄清」，
非根因错误；已就地修正/澄清，保持本 spec 为唯一真源）：

1. **`app/schemas.py` 无需改动（§4.1 列为「修改」）**：`/api/factor-sets/train` 与 `/api/factors/mine`
   两个路由**无 `response_model`**，直接返回 service 组装的 `dict`，故 `provenance` / `evaluated/persisted/skipped`
   作为普通 dict 字段流出即可，**无需新增 Pydantic 模型**；`factor_set_out` 亦如 R-P2-3 所述已整体透传 `model`，
   `provenance`/`trainedAt` 落进 `model_json` 自动可读。新字段的类型契约由前端 `lib/factorTypes.ts` 承载。
   **结论：schemas.py 本轮零改动**（避免无意义编辑，符合「保持 diff 聚焦」）。

2. **`apps/web/lib/factorApi.ts` 无需改动（§4.2 列为「修改」）**：该客户端的 `train`/`mine`/`sets.score`/
   `scorePreview` 已分别返回 `TrainResult` / `FactorMineResult` / `FactorScoreResult`（均定义在 `factorTypes.ts`）。
   一旦在 `factorTypes.ts` 扩展这三个类型（`provenance` / `evaluated|persisted|skipped` / `note|minBarsRequired`），
   新字段即沿类型自动贯通，**factorApi.ts 本身零改动**。`train` 的 `config` 已是 `Record<string,unknown>`，可承载
   `clip`（本轮未暴露 clip，故前端不传，恒默认，与 H1/R-P2-1 一致）。

3. **挖掘计数不变式口径澄清（§7.1 #4「evaluated == persisted + skipped + 已存」）**：实现取
   **`evaluated == persisted + skipped`**（`skipped` 已**含**「去重的已存项」与「无效项」两类，即把「已存」并入
   `skipped`）。且该不变式仅在 `persist=true` 时成立；`persist=false`（预览 / Agent `mine_factors` 路径）时
   `persisted=skipped=0`、`evaluated=len(items)`，前端据此条件展示，不显示误导性的「跳过」计数。去重对照库内
   **全部已存行**（任何 source），并在批内自去重；`bulk_create_factors` 返回**实际新写入**的行用于计数。

4. **`FactorTable.tsx` 仅复核、零代码改动（§4.2 列为「复核」+ H4/R-P1-2）**：激活 `Switch` 已
   `PUT /api/factors/{id}` + `onSettled` 失效（核验通过）；乐观更新+回滚为 R-P2-5 可选打磨，本轮不做。`enabled`
   真实下游（`list_factors(enabledOnly)` Agent 池 + 「我的因子」默认视图）由后端新增用例
   `test_disabled_factor_excluded_from_enabled_filter` 钉死。**组合成员来源不受 `enabled` 约束**（C-1 不在本轮）。

5. **`window_too_short` note 的前端落点（H6 改动 #3）**：`FactorScoreChart` 已新增可复用的 `emptyNote` prop
   （空序列时以 accent 提示条呈现，因仓库无 `--warn`/黄色 token，沿用项目既有「注意」色 `--accent`/`--accent-soft`，
   见 §9 风格一致性，避免引入新 token）。当前 UI **无独立的「复合得分预览图」渲染**（`FactorSetBuilder` 未挂
   score-preview 图——变更计划亦未要求新增），故本轮该 note 的**主用户可见面是 `StrategyDetail` 因子横幅的静态
   「至少需约 N 根」提示**（策略口径 `normWindow+60`，R-P2-2）；`FactorScoreChart.emptyNote` 为 score 端点响应
   预留的可复用通道（后续若挂出预览图即可直接消费 `note`/`minBarsRequired`，零额外改动）。

6. **`app/engine/factors/training.py` 无需改动（§4.1 列为「修改」）**：`build_factor_matrix(..., clip=...)` **本就**把
   `clip` 写进 `normStats`（`{"normWindow":…, "clip": float(clip)}`），`train_weights` 亦已整体透传 `norm_stats`。
   H1 的真实缺口在 **`service.train` 从不向 `build_factor_matrix` 传 `clip`**（恒默认）——已在 service 修复
   （读 `config.get("clip", DEFAULT_CLIP)` 并传入）。故「clip 进 normStats」的引擎链路无需改 training.py，本轮
   **training.py 零改动**（spec H1 改动 #3 自身亦注明「`build_factor_matrix` 已支持 clip 形参，仅需在 service 传入」）。

> 小结：变更计划列出的 `schemas.py` / `factorApi.ts` / `training.py` 三个文件，经核验**本轮均无需改动**（响应是
> 纯 dict 透传 / 类型沿 `factorTypes.ts` 自动贯通 / 引擎链路已就绪）；真正的改动点收敛在 service / specs /
> repositories / scoring / 策略 / agent 工具 / 前端 builder+types+组件 与测试。未做任何「为凑清单」的无意义编辑，
> 保持 diff 聚焦（CLAUDE.md §八 评审清单第 8 条）。

---

## 评审结论（Review Conclusion，Subtask #1）

**结论：有条件通过（Approved with Conditions）。**

本方案整体**目标正确、根因扎实、最小侵入**：逐文件核验后，H1/H2（normWindow 端到端闭合）、H3（权重单源 + 成员变更失效）、
H5（挖掘去重/无效过滤/计数）、H6（短窗可观测）四项均切中真实硬伤，直接服务全局目标"稳健、可靠、高成功率"；零表迁移、
向后兼容、不破坏既有对外契约的定位也克制得当。评审中发现 **1 个 P0 + 3 个 P1 + 5 个 P2**，P0/P1 已在 v2 正文就地修复
（见 §0.2 与各 H 项「评审修订」块），无遗留 P0/P1。

**放行条件（Subtask #2 落码时必须遵守 / 确认）：**

1. **C-P0（硬约束·R-P0-1）**：`factor_score_strategy.prepare` **必须保留**传 `self.params.get("weights")`，并新增
   §7.1 #2b「旧快照（顶层 weights+零成员权重、无 normStats）仍非全 NaN」回归测试，**先复现后护栏**。这是本轮最高优先级，
   绝不可因"权重单源"而删读取路径。
2. **C-1（R-P1-2）**：`enabled` 不约束组合成员来源。若产品确需"禁用因子不可进组合"，须另起增量（新增"已存因子→组合"
   成员来源并按 `enabled` 过滤），**不在本轮强化范围**；本轮只做 §H4 的"复核+文档化+补测"。
3. **C-2（R-P2-4）**：`score_inline`（`score-preview`）默认走 `thresholds` 回落即可；若要让未保存组合的预览精确反映训练
   `clip`，再由 `score-preview` 请求体可选携带 `normStats`（可选打磨，不阻断验收）。
4. **门禁（R-P1-3）**：前端验收用 `npm run test` + `npm run build`（+ `npx tsc --noEmit`），**不使用 `npm run lint`**。
5. **剩余 P2（R-P2-1/2/3/5）**：clip 穿线按"防御性单源化"落地、`minBarsRequired=normWindow`、`factor_set_out` 不改、
   乐观更新可选——均已在正文标注，按 v2 文案执行即可。

满足以上条件即视为达成 Definition of Done。下游 Subtask #2 可据本 v2 直接逐文件落码，无需再做设计决策；落码中若发现
新的方案与代码现实冲突，回填 §10 并保持本 spec 为唯一真源。

---

- **评审人**：Subtask #1（senior reviewer）
- **评审基线**：commit `bb74ac3`（逐文件核验：`scoring.py`/`training.py`/`factors/service.py`/`specs.py`/
  `factor_score_strategy.py`/`api/factors.py`/`agent/tools/factor_tools.py`/`db/repositories.py`/`schemas.py` +
  前端 `FactorSetBuilder.tsx`/`FactorTable.tsx`/`FactorCatalog.tsx`/`FactorLab.tsx`/`StrategyDetail.tsx`/
  `lib/factor.ts`/`next.config.mjs`/`package.json`）
- **文档版本**：v2（已含 §0 评审记录 + 本评审结论；P0/P1 全部就地修复）
