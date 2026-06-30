# 因子组合策略 ↔ 交易策略 自动同步（Factor Combo ⇄ Strategy Sync）开发方案 · spec.md

> Feature: `factor-combo-strategy-sync`
> 版本：**v2**（方案评审与修复 / Subtask #1 产出；P0/P1 已就地修复，v1 设计见提交历史）
> 适用分支：master
> 语言栈：Next.js 14 App Router + React 18 + Zustand + TanStack Query（前端）；FastAPI + SQLAlchemy async + SQLite（后端）
> 关联既有特性：`factor-combination-strategies`（已实现「因子组合策略」工作区 + `factorSetId` 弱关联 + 交易面板「在因子实验室编辑」回链，commit `e1cd1ff`）。本方案在其之上把**手动**「从组合新建策略」改为**自动同步**。

---

## 评审记录（Review Notes · Subtask #1）

> 评审人：Anthropic 工程团队资深评审。评审维度：可行性 / 完备性 / 一致性（对照 `CLAUDE.md` 与现网代码）/ 右尺度。
> 评审已核验下列真源文件：`factors/service.py`（`create_set` / `resolve_set_to_spec` 实测在场且单源，REST 与 Agent `SaveFactorSetTool` 共用）、`api/factors.py`、`db/repositories.py`（`create_strategy`/`update_strategy`/`delete_strategy` 各自 `commit`；行数 **791/800**）、`db/factor_repositories.py`（`create_factor_set`/`update_factor_set`/`delete_factor_set` 各自 `commit`，且 `delete_factor_set` docstring 明言「删除不影响已创建的 factor_score 策略」）、`agent/tools/factor_tools.py`、`schemas.py`（`factor_set_out` 为纯同步函数，行数 **1021**，已超 800）、`main.py`（lifespan 仅 `init_db()`，未开 session）、`db/session.py`（**未** `PRAGMA foreign_keys=ON`）、前端 `FactorSetList.tsx` / `FactorSetBuilder.tsx` / `StrategyPanel.tsx` / `factor.ts` / `factorTypes.ts`。
>
> **总体结论：方案主线（后端单事务伴生同步 + 反查解析 + 零迁移）可行且右尺度，下列 P0/P1 已就地修复，详见正文标注「评审修复 R*」处。**

| 编号 | 维度 | 严重度 | 问题 | 处置 |
|---|---|---|---|---|
| R0a | 一致性 | P0（作者已识别·缓解充分·保留） | 组合与伴生策略双写不一致（部分成功）。 | 维持 §2.5 单 `AsyncSession` 单 `commit` 编排；本评审仅补强其**测试可复现性**（见 R8），不改主线。 |
| R0b | 一致性 | P0（作者已识别·缓解充分·保留） | `create_set` 行为变更波及 Agent `save_factor_set` 旧后端测试。 | 已核实 `SaveFactorSetTool` 确经 `create_set`（`factor_tools.py:349`），属期望行为；维持 §6「显式更新共用 `create_set` 的断言」。 |
| R1 | 完备性 | **P1** | §4 承诺 GET `/factor-sets`(`/{id}`) 返回 `strategyId`，但回填机制只覆盖 POST/PUT 的 service 返回值；`factor_set_out` 是纯同步函数、GET 路由直调它，**刷新页面后因子实验室列表的 `strategyId` 恒为 null**。 | 就地修复：新增**批量**反查 `map_companion_strategy_ids(session, set_ids)`，list/get 读路径经薄 service 包装统一回填（避免 N+1）；正文 §3/§4/§5 已改。 |
| R2 | 一致性 | **P1** | `ensure_companion_strategy` 在「历史多伴生」上自相矛盾：§4 签名说「取第一条为正典刷新」，§2.6 说「全刷」。若只刷一条而删除时全删，则更新后交易面板可能残留**陈旧的重复伴生**，违背用户「数据保持一致」。 | 就地修复：统一为「**收敛到正典（min id）**：刷新正典 + 删除多余重复伴生」，每次 update 都把多伴生坍缩为恰好一条。正文 §2.4/§2.6/§4/§5/§7 已改。 |
| R3 | 一致性 | **P1** | 删除组合连带删伴生策略，**反转**了现网已发布、刻意设计的不变量（`FactorSetList.tsx:69` 删除确认明言「得分策略不受影响（内联快照自包含）」；`factor_repositories.delete_factor_set` docstring 同义；`factor.ts` P1-1「组合被删时回落快照」）；且 §2.6/§7 用「与既有组合删除同口径（`useComboPanel` 已会删带 run 的策略）」作据系**误引**——既有因子组合删除其实**不动任何策略**，而 `useComboPanel` 是「策略组合」另一特性。 | 就地修复：显式声明此为用户明确要求的**有意反转**；把删带 run 策略的先例更正为 `delete_strategy` + 本机 `foreign_keys=OFF`（已核 `session.py` 未开 FK）；§3 变更清单补「更新 `delete_factor_set` 过时 docstring」与「审计/升级断言旧不变量的测试（`factor.test.ts`/`StrategyDetail.test.tsx`/`FactorSetList` 删除文案）」。 |
| R4 | 可行性 | **P1** | §3 计划给 `create_factor_strategy`（`CreateFactorStrategyTool`）加「该组合已有伴生则不重复创建」幂等守卫——但该工具入参只有 `members/thresholds/normStats/provenance/name`，**根本没有 `factorSetId`**（`factor_tools.py:383-391`），守卫无法实现。 | 就地修复：移除该不可实现守卫；澄清该工具产出**无 `factorSetId` 的孤儿策略**（与「另存为」副本同类），天然不参与 1:1、无重复风险；`save_factor_set` 现已自动部署，故该工具为冗余但保留（仅文案）。正文 §3 已改。 |
| R5 | 一致性 | **P1** | 违反 `CLAUDE.md`「单文件 ≤800 行」：`repositories.py` 已 **791** 行，新增 `add_strategy`/`apply_strategy`/`list_factor_score_rows_for_set`(+批量) 约 50–70 行必越 800；§6 DoD 第 6 条却声称达标，且 `schemas.py` 已 **1021** 行（既有超限）。 | 就地修复：把不提交的策略原语 + 反查/批量反查下沉**新模块 `db/strategy_repositories.py`**，经 `repositories.py` 顶部 re-export（沿用 `factor_repositories.py` 既有先例 `repositories.py:774`）；DoD 第 6 条改为只约束「新增/改动的代码与新建文件」，并标注 `schemas.py` 既有超限本期不重构（改动仅 1 字段）。正文 §2.5/§3/§6/§8 已改。 |
| R6 | 完备性 | P2 | §2.4 流程 D 让 lifespan 调 `backfill_companions(session)`，但 `main.py` lifespan 当前**不开 session**，且补建失败不应拖垮启动。 | 就地修复：明确用 `db.session.get_sessionmaker()` 开一次性 session；`try/except` 包裹、失败仅 `log.warning` 不阻断启动（下次 `update_set` 自愈）。正文 §2.4/§3 已改。 |
| R7 | 完备性 | P2 | §3 前端措辞「删除 `createStrategy` mutation / `strategyParams` 中仅服务该按钮的分支」不精确：`strategyParams` 仍被**保留的** `updateStrategy`（旧孤儿快照分支）使用，不可删。 | 就地修复：澄清仅删 `createStrategy` mutation 与其触发按钮，`strategyParams` 保留。正文 §3 已改。 |
| R8 | 可测性 | P2 | §6「注入非法快照使伴生创建失败→回滚」难以构造：`resolve_set_to_spec` 纯函数、`add_strategy` 不做校验，正常路径无从触发失败。 | 就地修复：改为 monkeypatch `add_strategy`（或 `apply_strategy`）抛异常以**真正触发**回滚断言。正文 §6 已改。 |
| R9 | 右尺度 | P2（确认接受） | update 以组合为真源覆盖伴生策略的 `name`/`params`，但**不动** `enabled`（保留用户在交易面板的启停）；create 恒 `enabled=True`。 | 接受并显式写入契约（§2.6 新增一条），banner 已提示「参数由因子实验室组合管理」。 |

---

## 一、概述（Overview）

因子实验室（Factor Lab）的「因子组合策略」工作区（`FactorSetList` / `FactorSetBuilder`）让用户「选因子 → 训练权重 → 设阈值 → 保存组合」，把一条 **因子组合（`factor_set`）** 落库。交易工作台右侧「交易策略」面板（`StrategyPanel`）则按 `factor_score`（`kind="factor"`）模板展示**可交易**的「因子组合得分策略」，它把组合的 `{members, thresholds, normStats, provenance}` 内联快照进 `params_json`，天然复用回测 / 对比 / Walk-forward / 实时链路。二者是「配方（组合）」与「按配方做出的菜（得分策略）」的关系。

但当前两者**不会自动对应**：用户保存组合后，必须再手动点「从组合新建策略」（builder）或每卡上的「新建策略」（list）才会在交易面板里出现一条对应策略；之后在因子实验室对组合做的「编辑 / 删除」也完全不会回传给交易面板里那条已经创建的策略——组合改了、删了，交易策略仍是旧快照、甚至成为悬空记录。这与用户预期「因子实验室里建/改/删因子组合策略，交易面板里对应的交易策略要同步建/改/删、数据保持一致」相悖。

本方案的目标是把这条「组合 → 得分策略」的关系从**手动一次性**改造为**自动、双向可追踪、强一致**的 1:1 同步：

1. **去掉手动新建步骤**：在因子实验室「保存组合」即自动在交易面板出现对应的因子组合得分策略，无需再点任何「新建策略」按钮。
2. **增删改实时对应**：在因子实验室对组合「更新 / 删除」时，交易面板里对应的得分策略同步「更新 / 删除」；组合改名、改成员、重训权重、改阈值，对应策略的快照与名称随之刷新。
3. **数据强一致**：组合与其对应得分策略的写入在**同一数据库事务**内完成（要么都成功、要么都不落），并对历史遗留数据做**幂等补建**，确保「因子实验室列表」与「交易面板里的因子组合得分策略」恒为同一份数据的两个视图。

设计把同步的**单一真源**放在后端 `factors/service.py` 的组合 CRUD 编排里（REST 路由与 Agent `save_factor_set` 工具共用同一入口，天然全覆盖），前端只做「移除手动按钮 + 跨视图缓存双失效 + 文案校正」。后端无需任何表结构变更：正向链路 `strategy → set` 复用既有 `params_json.factorSetId`，反向 `set → strategy` 用「按 `factorSetId` 反查 `factor_score` 行」解析，迁移成本为零。所有快照构造复用现成的 `service.resolve_set_to_spec(row)`（其 docstring 早已预留「从已保存组合直接建策略」的服务端路径），不引入新依赖、不改打分契约（归一化窗口/clip 仍单源 `resolve_norm_contract`）。

---

## 二、技术设计（Technical Design）

### 2.1 概念与现状盘点

| 概念 | 载体 | 现状 |
|---|---|---|
| 因子组合（配方） | `factor_sets` 表（`FactorSet`），`factor_set_out` → 前端 `FactorSet` | 因子实验室「因子组合策略」工作区 CRUD 完整（`GET/POST/PUT/DELETE /factor-sets`、`/{id}/score`）。 |
| 因子组合得分策略（菜） | `strategies` 表（`Strategy`，`template_key="factor_score"`），`merge_strategy` → 前端 `Strategy(kind="factor")` | 交易面板「常规策略」页签展示；`StrategyDetail` 渲染 `FactorStrategyBanner`。 |
| 正向链接 strategy→set | `strategies.params_json.factorSetId` | 已存在（`factor-combination-strategies` 引入；`prepare/on_candle` 不读它，仅 UI 回链）。 |
| 反向链接 set→strategy | 无显式列 | 本方案用「按 `factorSetId` 反查」解析，**不新增列**。 |

**关键现成件**（已实测）：
- `services/quant/app/factors/service.py: resolve_set_to_spec(row) -> {members, thresholds, normStats, provenance}`：把组合行解析为自包含快照，正是 `factor_score` 策略 `params` 所需。
- `services/quant/app/factors/service.py: create_set(...)`：组合创建的**单源编排**，被 `api/factors.create_factor_set` 与 Agent `SaveFactorSetTool` 共用。
- `services/quant/app/factors/specs.py: validate_members / validate_factor_strategy_params / assert_norm_contract / assert_weights_cover / is_factor`：边界校验齐备。
- `services/quant/app/db/repositories.py: create_strategy / update_strategy / delete_strategy / merge_strategy`：策略 CRUD；`Strategy.builtin=False` 的 `factor_score` 行可删可改。
- `FactorSetBuilder.tsx` 现有「保存组合 / 更新组合 / 从组合新建策略 / 更新该策略」多按钮；`FactorSetList.tsx` 每卡有「编辑 / 新建策略 / 删除」。

### 2.2 核心设计决策

**决策 1 — 1:1「伴生（companion）」模型，后端权威单事务同步。**
每条 `factor_set` 唯一对应一条 `factor_score` 策略（其 `params.factorSetId == set.id`）。组合的 create / update / delete 由后端在**同一 `AsyncSession`、单次 `commit()`** 内连带管理这条伴生策略：

- **create**：落组合行（`flush` 取 `id`）→ `resolve_set_to_spec` 取快照 → 落伴生 `factor_score` 策略（`params = 快照 + {factorSetId}`，`name = "{组合名} · 得分策略"`）→ 单次 `commit`。
- **update**：改组合行（`flush`）→ 重算快照 → **ensure-companion**：若伴生存在则刷新其 `params`（快照）与 `name`，若缺失（历史遗留组合）则补建 → 单次 `commit`。
- **delete**：删按 `factorSetId` 反查到的全部伴生 `factor_score` 行 → 删组合行 → 单次 `commit`。

> 选后端而非前端编排的理由：① 强一致——单事务保证「组合与策略要么都改、要么都不改」，杜绝前端两次请求间断电导致的数据漂移；② 全覆盖——REST UI 与 Agent 工具共用 `create_set` 单源，改一处即两条入口都同步；③ 幂等补建可在后端一次性处理历史数据。符合 CLAUDE.md「错误显式传播 / 边界一次性校验 / IO 收口在仓储与服务层」。

**决策 2 — 反向链接「按 `factorSetId` 反查」，零迁移。**
不新增 `factor_sets.strategy_id` 列。伴生策略由 `list_factor_score_rows_for_set(session, set_id)`（查 `template_key="factor_score"` 行 → 解析 `params_json.factorSetId == set_id`）解析。好处：① 无 schema 迁移；② 历史上用「从组合新建策略」手动建过的策略（已带 `factorSetId`）被自动识别为伴生，天然纳入同步；③ 链接只有一处真源（策略 `params`），不会双写漂移。`template_key` 已建索引，反查高效。

**决策 3 — 因子实验室为唯一编辑面，交易面板为只读/只跑投影。**
组合是真源，伴生策略是其投影。交易面板里对因子策略的临时改参（`StrategyDetail` 阈值编辑 + 回测前 `updateStrategy` 持久化）仅用于即时回测，**不回传**给组合，且会在下次组合更新时被快照覆盖；为避免静默分歧，banner 增「参数由因子实验室组合管理，请回因子实验室编辑」提示（阈值锁定为可选 P2）。「另存为」因子策略时**剥离 `factorSetId`**，使副本成为独立孤儿、不破坏 1:1。

**决策 4 — 历史遗留数据幂等补建。**
应用启动 lifespan 内调用一次 `backfill_companions(session)`：遍历全部 `factor_set`，对**无**伴生策略的组合补建一条。幂等（已有伴生即跳过 / 刷新），可重复运行、可记日志。确保「因子实验室列表」与「交易面板因子策略列表」对历史数据也一致。

### 2.3 架构与数据流

```text
因子实验室（activeView==="factor"）
  FactorLab ─ section==="sets" ─► FactorSetList ──┐
                                  │  「编辑」► setFactorEditTarget + section="builder"
                                  │  「删除」► DELETE /factor-sets/{id}
              section==="builder" ─► FactorSetBuilder
                                     「保存组合」► POST   /factor-sets
                                     「更新组合」► PUT    /factor-sets/{id}
                                                         │
                          ┌──────────────────────────────┘
                          ▼  （后端单事务，单源 factors/service.py）
   ┌───────────────────────────────────────────────────────────────────┐
   │ create_set:  add FactorSet → flush → resolve_set_to_spec           │
   │              → add Strategy(factor_score, params+factorSetId)      │
   │              → commit                                              │
   │ update_set:  patch FactorSet → flush → ensure_companion(refresh|  │
   │              create) → commit                                      │
   │ delete_set:  delete companions(by factorSetId) → delete FactorSet  │
   │              → commit                                              │
   └───────────────────────────────────────────────────────────────────┘
                          │ 影响 strategies 表
                          ▼
交易工作台（activeView==="trade"）
  StrategyPanel ── useQuery(["strategies"]) ─► StrategyList（常规策略页签含 kind==="factor"）
                                              └► StrategyDetail.FactorStrategyBanner

前端缓存一致性：因子实验室组合 CRUD 成功后 invalidateQueries(["factor","sets"]) **且** (["strategies"])
→ 交易面板下次激活即重新拉取，看到同步后的因子组合得分策略。
```

### 2.4 关键代码路径与操作时序

**流程 A — 保存组合（去手动新建）。**
1. 用户在 `FactorSetBuilder` 选成员、训练、设阈值、点「保存组合」→ `POST /factor-sets`（body 同现状：`name/members/method/modelJson/metricsJson/config`）。
2. 路由 `api/factors.create_factor_set` → `service.create_set(...)`：`validate_members` → `normalize_thresholds` → `assert_norm_contract` → `assert_weights_cover` →（**新增**）`add_factor_set`（flush 取 `id`）→ `resolve_set_to_spec(row)` 取快照 → `add_strategy("factor_score", "{name} · 得分策略", 快照 + {factorSetId:id})`（**新增**）→ 单次 `commit` → `factor_set_out(row)` 并附 `strategyId`。
3. 前端 `save.onSuccess`：`invalidateQueries(["factor","sets"])` **且** `invalidateQueries(["strategies"])`；提示「组合已保存并同步到交易策略」。
4. 用户切到交易工作台「常规策略」即见到该因子组合得分策略——**无需任何手动新建**。

**流程 B — 更新组合。**
1. 用户在列表点「编辑」载入 builder，改成员/重训/改阈值/改名后点「更新组合」→ `PUT /factor-sets/{id}`。
2. 路由 → `service.update_set(...)`：partial 校验（同现状 `assert_norm_contract`/`assert_weights_cover`）→（**新增**）`apply_factor_set_patch`（flush）→ `ensure_companion_strategy(row)`（**评审修复 R2：坍缩到正典**）：`list_factor_score_rows_for_set(id)` 按 `id` 升序取**正典 = 首条（min id）**，`apply_strategy` 刷新其 `params=resolve_set_to_spec(row)+{factorSetId}`、`name="{name} · 得分策略"`；**其余重复伴生行一并 `session.delete`**（把历史多伴生坍缩为恰好一条）；若一条都没有则 `add_strategy`（自愈补建）→ 单次 `commit`。`enabled` 不在刷新范围内（评审修复 R9：保留用户启停）。
3. 前端 `update.onSuccess`：双失效（同流程 A）。交易面板对应策略的成员/权重/阈值/名称即随之刷新。

**流程 C — 删除组合。**
1. 列表「删除」二次确认（**文案校正**：明确「将同时移除其在交易策略面板中的对应因子组合得分策略」）→ `DELETE /factor-sets/{id}`。
2. 路由 → `service.delete_set(id)`：`list_factor_score_rows_for_set(id)` → 逐条 `session.delete(strategy_row)` → `session.delete(set_row)` → 单次 `commit`（组合不存在 → 404 `NOT_FOUND`，同现状）。
3. 前端 `remove.onSuccess`：清 `selectedId` + 双失效。交易面板对应策略消失。

**流程 D — 启动幂等补建。**
1. `main.py` lifespan 启动（DB init 之后）用 `get_sessionmaker()` 开一次性 session（评审修复 R6）调用 `service.backfill_companions(session)`：`list_factor_sets` → 对每个 set，`list_factor_score_rows_for_set` 为空则 `ensure_companion_strategy` 补建，单次 `commit`（或逐条 commit）。幂等、可重复、记 `log.info("backfilled N companion strategies")`。**全程 `try/except` 包裹，异常仅 `logger.warning` 不阻断启动**（补建失败可由后续 `update_set` 自愈）。

### 2.5 事务原子性实现要点

现有 `repo.create_factor_set` / `update_factor_set` / `create_strategy` / `update_strategy` 各自 `await session.commit()`，无法直接拼成单事务。为实现「要么都成、要么都不落」，新增**不自动提交**的仓储原语，由 service 单点 `commit`：

- `db/factor_repositories.py`：`add_factor_set(session, values) -> FactorSet`（`add` + `flush`，无 commit）；`apply_factor_set_patch(session, row, patch) -> None`（就地改字段，无 commit）。
- `db/strategy_repositories.py`（**评审修复 R5：新建模块**，不再塞进 791 行的 `repositories.py`）：`add_strategy(session, template_key, name, params) -> Strategy`（`add` + `flush`，无 commit）；`apply_strategy(session, row, *, name=None, params=None) -> None`（就地改，无 commit）；`list_factor_score_rows_for_set(session, set_id) -> list[Strategy]`（按 `template_key` + 解析 `params_json.factorSetId`，`id` 升序）；`map_companion_strategy_ids(session, set_ids) -> dict[int,int]`（批量反查，R1）。四者经 `repositories.py` 顶部 `from .strategy_repositories import ...` re-export（沿用 `factor_repositories.py` 既有先例 `repositories.py:774`），故 `repo.X` 调用点不变、两侧文件均 <800 行。
- `factors/service.py`：`create_set` / `update_set` / `delete_set` 编排上述原语并在末尾**单次** `await session.commit()` + `refresh`；任一步抛 `HTTPException`（成员非法/契约漂移）时事务未提交，组合与策略均不落（原子回滚）。既有 `repo.create_factor_set` / `update_factor_set` 保留供其它调用点（不破坏），但组合 CRUD 主链路改走新编排。

> 注：`create_set` 行为变更（现在连带建伴生策略）会影响共用它的 Agent `SaveFactorSetTool`——这是**期望行为**（Agent 保存组合同样即时同步到交易面板）。既有 `service.create_set` 的后端测试需相应补断言（见 §6）。

### 2.6 边界与一致性细节

- **「另存为」剥离 `factorSetId`**：`StrategyPanel.saveAs` 对 `templateKey==="factor_score"` 的副本删去 `params.factorSetId`，副本为独立孤儿、不被组合同步影响，保持 1:1。
- **历史多伴生兼容（评审修复 R2）**：旧流程允许一个组合手动建多条策略。统一收敛规则——`delete_set` 删「按 `factorSetId` 反查到的**全部**」行；`update_set` 经 `ensure_companion_strategy` **刷新正典（min id）+ 删除其余重复伴生**，使每次更新后该组合恰好剩一条伴生（杜绝交易面板残留陈旧重复行）；`create` 时该组合必为新建、无既有伴生。
- **更新只覆盖 name/params、保留 enabled（评审修复 R9，确认接受）**：组合为真源，`ensure_companion_strategy` 仅以快照覆盖伴生策略的 `name` 与 `params`，**不改 `enabled`**（用户在交易面板对该策略的启停得以保留）；`create` 时新伴生恒 `enabled=True`。配合 banner「参数由因子实验室组合管理」明确分工。
- **有意反转「删除组合不影响策略」的旧不变量（评审修复 R3）**：现网刻意把 `factor_score` 策略设计为**自包含快照、独立于来源组合**——`FactorSetList.tsx:69` 删除确认明言「已基于它创建的得分策略不受影响（内联快照自包含）」、`factor_repositories.delete_factor_set` docstring 同义、`factor.ts` 的 P1-1「组合被删时回落快照」皆据此。本方案按**用户明确要求**把该关系反转为 1:1 强耦合生命周期（删组合即删伴生策略）。这是**经评审确认的有意破坏性语义变更**，非「文案校正」：实施时必须同步更新上述过时 docstring/确认文案，并审计/升级断言旧不变量的测试（见 §3、§6）。
- **伴生策略带 run 时删除**：本机 `db/session.py` **未** `PRAGMA foreign_keys=ON`（已核验，SQLite 默认 FK 关闭），删带 run 的 `factor_score` 行不因 `strategy_runs.strategy_id` 外键失败——与现网 `repo.delete_strategy`（交易面板删用户策略）**同口径**；历史 run 经 `strategy_id` 仍可在「历史」查阅。（**注：先例应引 `delete_strategy`，而非「既有组合删除」——既有 `delete_factor_set` 实际不动任何策略；亦非 `useComboPanel`「策略组合」另一特性。评审修复 R3。**）
- **快照自包含不破坏运行中的回测/实时**：得分策略 `params` 自包含，删组合不影响**已在跑**的该策略某次 run（run 已读取参数）；删除后该策略行消失，新回测自然不可发起，符合「删了就没了」。
- **归一化契约不变量**：`resolve_set_to_spec` 输出的 `normStats/thresholds` 与训练写入一致，伴生策略 `prepare` 仍走 `resolve_norm_contract` 单源；`update_set` 重算快照前的 partial 校验沿用 `assert_norm_contract`/`assert_weights_cover`，不引入新窗口漂移路径。

---

## 三、文件 / 模块变更计划（File / Module Change Plan）

| 文件 | 类型 | 一句话意图 |
|---|---|---|
| `services/quant/app/factors/service.py` | 修改 | `create_set` 改为单事务连带建伴生 `factor_score` 策略；新增 `update_set` / `delete_set` / `ensure_companion_strategy` / `backfill_companions` 编排（组合 CRUD 同步单源，UI 与 Agent 共用）。 |
| `services/quant/app/db/factor_repositories.py` | 修改 | 新增不提交的 `add_factor_set` / `apply_factor_set_patch`（供 service 单事务编排）；保留既有 `create_factor_set`/`update_factor_set`。**更正 `delete_factor_set` 过时 docstring**（现写「删除不影响已创建的 factor_score 策略」，与新伴生删除语义冲突，评审 R3）；该 repo 删除函数本身保留供非组合调用点，但组合 DELETE 主链路改走 `service.delete_set`（连带删伴生）。 |
| `services/quant/app/db/strategy_repositories.py` | **新建（评审 R5）** | 不提交的 `add_strategy` / `apply_strategy` + 反查 `list_factor_score_rows_for_set` + 批量 `map_companion_strategy_ids`（R1）。**因 `repositories.py` 已 791/800 行，新原语下沉独立模块**，沿用 `factor_repositories.py` 拆分先例。 |
| `services/quant/app/db/repositories.py` | 修改 | 仅在顶部 `from .strategy_repositories import (add_strategy, apply_strategy, list_factor_score_rows_for_set, map_companion_strategy_ids)` re-export（与既有 `factor_repositories` re-export 同处），保持 `repo.X` 调用点不变、文件 <800 行（评审 R5）。 |
| `services/quant/app/api/factors.py` | 修改 | `create_factor_set` / `update_factor_set` / `delete_factor_set` 路由改调 `service.create_set/update_set/delete_set`（路由变薄，校验与同步下沉 service）。**GET `list_factor_sets`/`get_factor_set` 经 `map_companion_strategy_ids` 批量回填 `strategyId`**（评审 R1，非 N+1）。 |
| `services/quant/app/schemas.py` | 修改（评审 R1/R5） | `factor_set_out` **本体保持纯同步函数**（不增 IO）；`strategyId` 不写入其内，而由读路径包装层经 `map_companion_strategy_ids` 附加（缺省 `None`）。`schemas.py` 既有 1021 行超限，本期仅最小改动、不重构。 |
| `services/quant/app/main.py` | 修改（**评审 R6 具体化**） | lifespan 在 `init_db()` 后用 `db.session.get_sessionmaker()` 开一次性 session（lifespan 无 `Depends(get_session)`，已核 `main.py:38-47`/`session.py:51`），`async with sm() as s: await service.backfill_companions(s)`；**整段 `try/except` 包裹，失败仅 `logger.warning` 不阻断启动**（补建为非关键收敛，下次 `update_set` 自愈）。 |
| `services/quant/app/agent/tools/factor_tools.py` | 修改（**评审 R4 缩窄**） | `SaveFactorSetTool` 因共用 `create_set` 自动同步——仅更新 summary 文案（如「并已自动部署为交易策略」），逻辑无须改。`CreateFactorStrategyTool`（`create_factor_strategy`）**入参无 `factorSetId`**（仅 `members/thresholds/normStats/provenance/name`，见 `factor_tools.py:383-391`），故无法实现「按组合去重」守卫；**移除该不可实现项**，仅在 description 注明：该工具产出**无 `factorSetId` 的孤儿 `factor_score` 策略**（与「另存为」副本同类，不参与 1:1、无重复风险）；因 `save_factor_set` 现已自动部署伴生，此工具属冗余但保留。 |
| `apps/web/components/Factor/FactorSetBuilder.tsx` | 修改 | 删除「从组合新建策略」按钮与 `createStrategy` mutation；**`strategyParams` helper 保留**（仍被保留的 `updateStrategy` 旧孤儿快照分支使用，不可删——评审修复 R7）；`save`/`update` 成功 `onSuccess` 追加 `invalidateQueries(["strategies"])`（现仅失效 `["factor","sets"]`，已核 `FactorSetBuilder.tsx:249/275`）；保留「另存为组合 / 更新该策略」（旧孤儿策略兼容）；保存区文案改为「保存后自动同步到交易策略」。 |
| `apps/web/components/Factor/FactorSetList.tsx` | 修改 | 删除每卡「新建策略」按钮与 `createStrategy` mutation / `setToStrategyParams` helper；删除成功后双失效 `["factor","sets"]`+`["strategies"]`；删除二次确认文案改为「将同时移除对应交易策略」。 |
| `apps/web/components/Strategy/StrategyPanel.tsx` | 修改 | `saveAs` 对 `templateKey==="factor_score"` 的副本剥离 `params.factorSetId`（副本为独立孤儿，守 1:1）。 |
| `apps/web/components/Strategy/StrategyDetail.tsx` | 修改 | `FactorStrategyBanner` 增「参数由因子实验室组合管理，请在因子实验室编辑」提示；可选：set-backed 因子策略阈值输入只读（P2）。 |
| `apps/web/lib/factorTypes.ts` | 修改 | `FactorSet` 增可选 `strategyId?: number`（映射后端 `factor_set_out.strategyId`）。 |
| `services/quant/tests/test_factor_set_strategy_sync.py` | 新建 | 后端集成测试：create/update/delete 组合连带建/改/删伴生策略；事务原子性（伴生建失败回滚组合）；`list_factor_score_rows_for_set` 反查；`backfill_companions` 幂等；Agent `save_factor_set` 亦同步。 |
| `apps/web/components/Factor/FactorSetList.test.tsx` | 修改 | 断言删除后双失效；列表卡片不再渲染「新建策略」；删除文案更新。 |
| `apps/web/components/Factor/FactorSetBuilder.test.tsx` | 修改/新建 | 断言保存 / 更新成功后双失效；不再渲染「从组合新建策略」。 |
| `apps/web/components/Strategy/StrategyPanel.test.tsx` | 修改/新建 | 断言对 `factor_score` 策略「另存为」时 `createStrategy` 入参 `params` 不含 `factorSetId`。 |
| `apps/web/lib/factor.test.ts`、`apps/web/components/Strategy/StrategyDetail.test.tsx` | 修改（**评审 R3**） | 审计并按新「删组合即删伴生」语义升级断言；保留孤儿/副本经 snapshot 回链分支，去除/改写「伴生随组合删除仍存活」的过时断言。 |
| `services/quant/tests/test_factor_set_strategy_sync.py` | 新建（并入上行） | 追加 R1（GET 重载回填 strategyId / `map_companion_strategy_ids`）与 R2（多伴生 PUT 坍缩为一条）断言。 |

> 不变更项：`factor_score` 策略引擎（`factor_score_strategy.py`）、打分契约（`engine/factors/scoring.py`）、表结构（`db/models.py`）、`resolve_set_to_spec` 本体——均零改动。

---

## 四、接口设计（Interface Design）

**不新增 REST 路由**，复用既有端点；仅**改变副作用与响应形态**（向后兼容）。

| 端点 | 方法 | 同步副作用（新增） | 响应变化 |
|---|---|---|---|
| `/factor-sets` | POST | 单事务连带创建伴生 `factor_score` 策略 | `factor_set_out` 增 `strategyId:number`（伴生 id）。 |
| `/factor-sets/{id}` | PUT | 单事务刷新 / 自愈补建伴生策略（params + name） | 同上增 `strategyId`。 |
| `/factor-sets/{id}` | DELETE | 单事务删除全部按 `factorSetId` 反查的伴生策略 | 204（不变）。 |
| `/factor-sets`、`/factor-sets/{id}` | GET | 无写副作用；列表经**批量**反查回填（评审 R1，非 N+1） | `factor_set_out` 增 `strategyId`（若有伴生，否则 `null`）。 |
| `/strategies`、`/strategies/{id}` | * | 不变 | 不变（伴生策略即普通 `factor_score` 行）。 |

**`strategyId` 的回填路径（评审修复 R1）**——`factor_set_out` 是 `schemas.py` 里的**纯同步函数**、且 GET `list_factor_sets`/`get_factor_set` 路由直调它（不经 service），故「仅在 create/update 编排末尾回填」会让**刷新页面后列表 `strategyId` 恒为 null**。修复：`strategyId` 在**所有读路径**统一回填，避免 N+1——
> - 新增批量反查 `map_companion_strategy_ids(session, set_ids) -> dict[int,int]`（一次查 `template_key="factor_score"` 行 → 解析 `params_json.factorSetId` → `{factorSetId: 正典(min id) strategyId}`）。
> - GET 列表路由：`rows = list_factor_sets(...)` → `id_map = map_companion_strategy_ids(session, [r.id for r in rows])` → `factor_set_out(r) | {"strategyId": id_map.get(r.id)}`（单次额外查询，非逐行 N+1）。
> - GET 单条 / create / update：同样以 `id_map`（或单 set 反查）回填。`factor_set_out` 本体保持纯函数，`strategyId` 一律在路由/service 包装层附加，缺省 `None`。
>
> `factor_set_out` 形态（`strategyId` 由上述包装层附加）：

```jsonc
{
  "id": 7, "name": "动量反转组合", "members": [/* … */], "method": "linear",
  "config": { "thresholds": { /* … */ } }, "model": { /* weights/normStats/provenance/trainedAt */ },
  "metrics": { /* icIn/icOut … */ }, "enabled": true,
  "createdTs": 0, "updatedTs": 0,
  "strategyId": 42            // 新增：对应交易策略 id（无伴生时为 null）
}
```

**伴生策略 `params`（写入约定）**——`resolve_set_to_spec(row)` 输出 + `factorSetId`：

```jsonc
{
  "members": [ { "factorKey": "...", "direction": "long", "params": {}, "weight": 0.31 } ],
  "thresholds": { "enterLong": 0.3, "enterShort": 0.3, "exitBand": 0.1, "normWindow": 200, "squash": true },
  "normStats": { "normWindow": 200, "clip": 3.0 },
  "provenance": { "instId": "...", "bar": "1H", "startTs": 0, "endTs": 0, "horizon": 12 },
  "factorSetId": 7
}
```

**前端类型增补（factorTypes.ts）：** `FactorSet` 接口增 `strategyId?: number;`。前端 `factorApi.sets.*` 客户端签名不变。

**Service 新增 / 改造签名（factors/service.py）：**

```python
async def create_set(session, *, name, members_raw, method, config, model, metrics, enabled) -> dict: ...
# 行为变更：内部单事务连带建伴生 factor_score 策略；返回 factor_set_out + strategyId。

async def update_set(session, set_id, *, patch_fields...) -> dict | None:  # None=404
    # 校验 → apply_factor_set_patch(flush) → ensure_companion_strategy → commit

async def delete_set(session, set_id) -> str:  # 'ok' | 'not_found'
    # 删全部伴生 factor_score 行 → 删组合行 → commit

async def ensure_companion_strategy(session, set_row) -> Strategy:
    # 评审修复 R2：list_factor_score_rows_for_set 按 id 升序 → 取正典(min id) apply_strategy 刷新 name/params，
    # 其余重复伴生 session.delete 坍缩为一条；一条都没有则 add_strategy 补建。不触 enabled（R9）。不在此处 commit（由编排单点 commit）。

async def backfill_companions(session) -> int:  # 返回补建条数（幂等）

# 评审修复 R1 —— 读路径批量回填 strategyId（仓储原语，置于 db/strategy_repositories.py，见 §3 / R5）：
async def map_companion_strategy_ids(session, set_ids: list[int]) -> dict[int, int]:
    # 一次查 template_key="factor_score" 行 → 解析 params_json.factorSetId →
    # {factorSetId: 正典(min id) strategyId}；GET 列表/单条与 create/update 据此回填，避免 N+1。
```

---

## 五、数据模型（Data Model）

- **数据库：零变更。** 不新增列、不建迁移。复用 `factor_sets`（`FactorSet`）与 `strategies`（`Strategy`）两表。
- **正向链接 strategy→set**：`strategies.params_json.factorSetId`（既有）。
- **反向链接 set→strategy**：不持久化，运行时按 `template_key="factor_score"` + 解析 `params_json.factorSetId == set_id` 反查（单 set 用 `list_factor_score_rows_for_set`；读路径批量用 `map_companion_strategy_ids`，评审 R1）。
- **1:1 不变量（评审修复 R2）**：每个 `factor_set` 恰一条「正典伴生」`factor_score` 策略。收敛规则：`update` 经 `ensure_companion_strategy` 刷新正典(min id)并删除多余重复伴生、`delete` 删全部反查行、`create` 仅当无既有伴生时建（新组合必满足）——任一写后该组合的伴生数 ≤1，历史多伴生在首次 update 即坍缩。
- **内存形态（前端）**：`FactorSet`（增 `strategyId?`）、`Strategy`（`kind="factor"`，不变）。
- **归一化契约**：伴生策略快照的 `normStats.normWindow` == `thresholds.normWindow`（组合保存即一致，`resolve_set_to_spec` 原样透传）；`compose_score` 与训练共享 `causal_zscore`（`resolve_norm_contract` 单源，不变）。

---

## 六、测试与验收标准（Testing & Acceptance）

**后端集成测试（pytest，真实临时 DB，遵循 `aragon-quant-test-isolation` 单库共享 + 清缓存约定）：** `services/quant/tests/test_factor_set_strategy_sync.py`
- `POST /factor-sets` 后，`GET /strategies` 出现一条 `kind="factor"` 且 `params.factorSetId == set.id` 的策略；`factor_set_out.strategyId` 指向它。
- `PUT /factor-sets/{id}` 改成员 + 重训后，伴生策略 `params.members/normStats` 同步刷新、`name` 随组合改名更新；对**无伴生**的组合（模拟历史）PUT 触发自愈补建。
- `DELETE /factor-sets/{id}` 后，对应 `factor_score` 策略从 `GET /strategies` 消失。
- **原子性（评审修复 R8 可复现化）**：`resolve_set_to_spec` 是纯函数、`add_strategy` 不做校验，正常路径无从「注入非法快照」触发失败；故测试以 **monkeypatch `add_strategy`（或 `apply_strategy`）抛异常**来真正触发，断言 `factor_sets` 与 `strategies` **两表均无新行**（单 `commit` 前异常 → 整体回滚）。另：成员非法（`FACTOR_MEMBERS`/`FACTOR_DIRECTION`）400 时校验在落库前、两表亦均无写入。
- `backfill_companions` 幂等：对已有伴生的组合二次运行不重复建；对历史无伴生组合恰好补建一条。
- Agent `save_factor_set` 工具保存组合后，`GET /strategies` 同步出现伴生策略（共用 `create_set` 的回归护栏）。
- 旧 `factor_score` 孤儿策略（无 `factorSetId`）不受影响：不被任何组合的 update/delete 误删/误改。
- **GET 回填 strategyId（评审 R1）**：`POST` 建组合后，重新 `GET /factor-sets` 与 `GET /factor-sets/{id}` 返回的 `strategyId` 均指向伴生（**模拟刷新场景，不依赖 POST 响应**）；`map_companion_strategy_ids` 对多 set 一次查询即得正确映射。
- **多伴生坍缩（评审 R2）**：对人为构造 2 条同 `factorSetId` 伴生的组合执行 `PUT`，断言更新后该组合**恰剩一条**伴生（正典 min id 被刷新、其余被删）。

**前端组件 / 单元测试（vitest + RTL，需 `NODE_ENV=test`，遵循 `aragon-web-vitest-node-env`）：**
- `FactorSetList.test.tsx`：列表卡片**不**渲染「新建策略」；点「删除」二次确认后调用 `sets.remove` 且 `invalidateQueries` 命中 `["factor","sets"]` 与 `["strategies"]`；删除当前选中项后清 `selectedId`。
- `FactorSetBuilder.test.tsx`：**不**渲染「从组合新建策略」；「保存组合」/「更新组合」成功后双失效 `["factor","sets"]`+`["strategies"]`。
- `StrategyPanel.test.tsx`：对 `kind="factor"` 策略「另存为」时，`api.createStrategy` 入参 `params` **不含** `factorSetId`。
- **旧不变量测试审计（评审 R3）**：审查并按新语义升级断言「组合删除后策略仍在 / 回落快照」的用例——`factor.test.ts`（P1-1 `buildEditTargetFromStrategy` set 分支带 snapshot 兜底）、`StrategyDetail.test.tsx`、`FactorSetList` 删除确认文案相关用例；保留「孤儿/另存为副本仍可经 snapshot 回链」分支（仍有效），仅去除/改写对「伴生随组合删除仍存活」的过时断言与文案断言。

**验收标准（Definition of Done，功能级）：**
1. 在因子实验室「保存组合」后，**无需任何手动新建**，交易工作台「常规策略」即出现对应「因子组合得分策略」（名称 `{组合名} · 得分策略`）。
2. 在因子实验室「更新组合」（改成员 / 重训 / 改阈值 / 改名）后，交易面板对应策略的快照与名称同步刷新；「删除组合」后对应策略同步消失。
3. 组合与对应策略的写入原子一致：非法成员 / 契约漂移导致失败时两者均不落；历史遗留组合经启动补建或首次编辑自愈，确保两侧列表一致。
4. 旧 `factor_score` 孤儿策略与「另存为」副本不被组合同步误删 / 误改（剥离 `factorSetId` 生效）。
5. `factor_set_out` 返回 `strategyId`；前端因子实验室与交易面板对同一组合呈现一致数据。
6. `pnpm --filter web test`、`pnpm --filter web typecheck`（或 `next lint`）、`pytest services/quant` 全绿；**新增/改动的代码与新建文件**遵守清洁阈值（≤50 行/函数、嵌套 ≤4、形参 ≤5、圈复杂度 ≤10，文件 ≤800 行）。**评审修复 R5 说明**：策略原语下沉新建 `db/strategy_repositories.py`，确保 `repositories.py`（现 791）不越 800；`schemas.py`（现 1021，**既有超限**）本期仅加 1 字段、不在本特性范围内重构，不据此判失败。

---

## 七、风险与缓解（Risks & Mitigations）

| 风险 | 等级 | 缓解 |
|---|---|---|
| 组合与策略**双写不一致**（部分成功）。 | **P0** | 后端单 `AsyncSession` 单 `commit` 编排（新增不提交仓储原语，service 末尾统一 commit）；任一步抛 `HTTPException` 即整体未提交、原子回滚；后端测试覆盖「伴生建失败 → 组合亦未落」。 |
| `create_set` 行为变更波及 Agent `save_factor_set`，旧后端测试可能因「多出一条策略」而断言失败。 | **P0** | 这是期望行为（Agent 路径同样同步）；显式更新 / 补充共用 `create_set` 的后端测试断言；本特性测试加 Agent 同步回归。 |
| 历史遗留组合无伴生 → 两侧列表不一致。 | P1 | 启动 lifespan 幂等 `backfill_companions` 补建；并在 `update_set` 自愈补建（双保险）。 |
| 历史「从组合新建策略」产生的**多伴生**导致更新只动一条而残留陈旧重复（评审 R2）。 | P1（已修复） | `ensure_companion_strategy` **刷新正典(min id)+删除多余重复伴生**坍缩为一条；`delete_set` 删全部反查行；`create` 仅当无既有伴生时建。 |
| 用户在交易面板改因子策略阈值并回测 → 与组合**静默分歧**，且下次更新组合时被覆盖。 | P1 | banner 增「参数由因子实验室组合管理」提示；下次组合更新以快照为准覆盖（明确组合为真源）；可选 P2 锁定 set-backed 因子策略阈值输入只读。 |
| 删除被「正在跑的得分策略」引用的组合 → 连带删策略行；且反转旧「策略不受删除影响」不变量（评审 R3）。 | P1（已修复·有意为之） | 已核 `session.py` 未开 FK，删带 run 的 `factor_score` 行与 `repo.delete_strategy` 同口径、不报错；已发起的 run 自包含、不受影响。**有意反转**旧不变量系用户要求：实施须更新 `delete_factor_set` 过时 docstring、`FactorSetList` 删除确认文案（明确「将同时移除对应交易策略」），并审计旧不变量测试。 |
| `list_factor_score_rows_for_set` 反查需解析 `params_json`，O(n)。 | P2 | `template_key` 已建索引，`factor_score` 行数量级小；仅在组合 CRUD / 补建时调用，非热路径；如未来量大可再加 `factor_sets.strategy_id` 列优化（本期不引入迁移）。 |
| 「另存为」副本仍带 `factorSetId` → 误成第二伴生被同步。 | P2 | `StrategyPanel.saveAs` 对 `factor_score` 剥离 `factorSetId`；前端测试护栏。 |
| 前端只失效 `["factor","sets"]` 漏失效 `["strategies"]` → 交易面板看旧数据。 | P2 | builder 与 list 的 create/update/delete `onSuccess` 统一双失效；组件测试断言两键均被 invalidate。 |
| 文件行数膨胀触 800 上限（`repositories.py` 已 791 行）。 | P2（已修复·评审 R5） | 同步编排抽 `ensure_companion_strategy` / `backfill_companions` 小函数；策略原语**下沉新建 `db/strategy_repositories.py`**（非塞进 791 行的 `repositories.py`），经顶部 re-export，控两侧 ≤800 行；`schemas.py` 既有超限本期不重构。 |

---

## 八、实现顺序建议（供 Subtask #2）

1. **后端仓储原语**：`factor_repositories.add_factor_set / apply_factor_set_patch`；**新建 `db/strategy_repositories.py`**（评审 R5）放 `add_strategy / apply_strategy / list_factor_score_rows_for_set / map_companion_strategy_ids`（不提交版）+ 经 `repositories.py` 顶部 re-export。
2. **后端 service 编排**：改造 `create_set`，新增 `update_set / delete_set / ensure_companion_strategy（坍缩到正典，R2）/ backfill_companions`（单事务、复用 `resolve_set_to_spec`）；读路径经 `map_companion_strategy_ids` 回填 `strategyId`（R1，`factor_set_out` 本体不变）。
3. **后端路由变薄**：`api/factors.py` create/update/delete 改调 service；`main.py` lifespan 接入 `backfill_companions`。
4. **后端测试**：`test_factor_set_strategy_sync.py`（含原子性、幂等、Agent 同步、孤儿不受影响）；跑 `pytest services/quant`。
5. **前端**：`FactorSetBuilder` / `FactorSetList` 去手动按钮 + 双失效 + 文案；`StrategyPanel.saveAs` 剥离 `factorSetId`；`StrategyDetail` banner 提示；`factorTypes.ts` 增 `strategyId?`。
6. **前端测试**：列表 / builder 双失效与按钮移除、saveAs 剥离；跑 `NODE_ENV=test pnpm --filter web test` 与 typecheck。
7. **Agent 工具**：`save_factor_set` summary 文案（已自动部署伴生）；`create_factor_strategy` **仅 description 注明产出孤儿策略**（不加不可实现的按组合去重守卫，评审 R4）。

> 本文档 v2 经 Subtask #1 评审与就地修复，仅描述实现、不含实现代码，交 Subtask #2 落码。

---

## 评审结论（Review Verdict · Subtask #1）

**结论：有条件通过（Approved with Conditions）。**

方案主线经核验**可行、与现网架构一致、尺度恰当**：`create_set` 确为 REST 与 Agent `SaveFactorSetTool` 共用的单源（`factor_tools.py:349`），`resolve_set_to_spec` 在场且输出契约匹配，反向「按 `factorSetId` 反查」对历史手建策略天然兼容（前端 `FactorSetBuilder.tsx:258`/`FactorSetList.tsx:28` 确写 `factorSetId`），后端单事务 + 零迁移的取向正确，`factor_score` 模板 `kind="factor"`、本机 SQLite 未开 FK 等关键前提均已实测成立。

评审发现的 **2 个作者已识别 P0（双写原子性、`create_set` 波及 Agent 测试）缓解充分予以保留**；新发现的 **5 个 P1 与 4 个 P2 已全部在正文就地修复并升版 v2**：

- **R1（P1）** GET 读路径经批量 `map_companion_strategy_ids` 回填 `strategyId`，杜绝刷新后恒 null 与 N+1。
- **R2（P1）** `ensure_companion_strategy` 统一为「刷新正典(min id)+删除多余重复伴生」，消除 §2.6/§4 自相矛盾与多伴生残留。
- **R3（P1）** 显式声明删除连带删伴生系**有意反转**旧不变量、更正误引的删除先例，并把过时 docstring/确认文案/旧不变量测试纳入变更与测试计划。
- **R4（P1）** 移除 `create_factor_strategy` 不可实现的「按组合去重」守卫，澄清其产出孤儿策略。
- **R5（P1）** 策略原语下沉新建 `db/strategy_repositories.py`，确保 `repositories.py` 不越 800 行，并修正 DoD 第 6 条措辞与 `schemas.py` 既有超限说明。
- **R6–R9（P2）** lifespan session 来源与启动健壮性、前端 `strategyParams` 保留、原子性测试可复现化、`enabled` 保留契约均已写入正文。

**放行条件（Subtask #2 必须满足，否则视为未完成）：**

1. 严格按 v2 §3 变更清单实现，尤其：策略原语置于**新模块 `db/strategy_repositories.py`** 并 re-export；不得把新原语塞回 `repositories.py` 致其越 800 行。
2. `ensure_companion_strategy` 按 **R2 坍缩语义**实现（正典刷新 + 删多余），并以 §6「多伴生 PUT 坍缩为一条」测试守护。
3. 删除连带删伴生为**有意破坏性变更**：必须同步更新 `delete_factor_set` docstring、`FactorSetList` 删除确认文案，并审计/升级 `factor.test.ts`、`StrategyDetail.test.tsx` 等旧不变量断言（§6）。
4. `strategyId` 必须在**所有读路径**（含 GET 列表/单条）回填，且以「模拟刷新」的后端测试验证（R1）。
5. 原子性测试以 **monkeypatch `add_strategy` 抛错**真正触发回滚断言（R8）；`backfill_companions` lifespan 失败不得阻断启动（R6）。
6. 落码后 `pnpm --filter web test`、typecheck、`pytest services/quant` 全绿；新增/改动代码守清洁阈值。

满足上述条件即等价于通过；条件均为可在 Subtask #2 内闭环的实现项，不需返工设计。

---

## 实施过程发现的方案缺陷（Issues Found During Implementation · Subtask #2）

> 落码后回填，记录与 v2 §3 变更计划的细微偏差及处置，避免静默偏离。

1. **`apps/web/lib/factor.test.ts` 审计后无需修改（R3 计划的细微不精确）。** §3 变更计划把它列为「修改（评审 R3）—— 去除/改写『伴生随组合删除仍存活』的过时断言」。实测审计发现：该文件的 `buildEditTargetFromStrategy` 用例只断言「策略 → 编辑交接目标」的纯映射与**仍保留的** snapshot 回链兜底分支（孤儿/副本经 snapshot 回链，仍有效），**并不含**任何「伴生随组合删除后仍存活」的断言——后者只存在于 `FactorSetList` 删除确认文案与 `factor_repositories.delete_factor_set` docstring（二者本次均已更新）。故 `factor.test.ts` 按 R3 审计结论**保持不变**（无过时断言可删），其 65 项用例落码后全绿。此为 §3 该行表述与真源的细微出入，非功能缺陷。

2. **新增前端测试落点。** §3 计划的 `StrategyDetail.test.tsx`「修改（R3）」按上条审计同样无过时断言可删；改为为本次新增的 banner 提示「参数由因子实验室组合管理」补一条存在性断言（与 `StrategyDetail.tsx` 改动配套），更贴合实际改动。`StrategyPanel.test.tsx` 的「另存为剥离 factorSetId」、`FactorSetList.test.tsx` 的「双失效 + 无新建策略按钮 + 删除文案」、`FactorSetBuilder.test.tsx` 的「无『从组合新建策略』+ 更新双失效」均按 §6 落地。
