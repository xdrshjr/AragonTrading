# Factor Layer 最终可靠性收口（Reliability & Closeout）开发方案 — spec v2

> 需求（全局目标）：**确保因子相关功能完善、稳健、可靠、高成功率**。这是因子层四轮迭代的
> **最后一轮（iteration 4/4）**，定位为「收口」：在前三轮已交付的功能之上，补齐**边界校验**、
> **端到端闭环**、**数值鲁棒性护栏**、**无障碍与可靠性体验**、**清洁度合规**五处仍然敞口的缺陷，
> 把「已建好但仍可能在边角失败」的因子层，收敛成「任何入口、任何退化输入都显式可预期」的产品。
>
> 本文档由 **Subtask #0（方案设计）** 产出，仅写设计、不写实现、不提交。Subtask #1（评审）将在
> 顶部追加「§0 评审记录」、文末追加「评审结论」并升 v2；Subtask #2（开发）按 §3 文件清单逐项落码，
> 并把实施中发现的方案缺陷回填 §10。

- **版本**：v2（Subtask #1 评审完成：0 P0 / 4 P1 全部就地修复 / 5 P2 标注或列为放行条件）
- **特性目录名**：`factor-layer-reliability`
- **作者**：Subtask #0（solution architect）
- **基线 commit**：`ad4dd2c`（factor-layer-lifecycle 已落地）
- **前序迭代**：iter1 `bb74ac3`（构建因子层）· iter2 `c9843fd`（归一化契约硬化）· iter3 `ad4dd2c`（生命周期闭环）

---

## 0. 评审记录（Review Notes，by Subtask #1）

> 评审方式：把方案逐节对照「可行性 / 完整性 / 一致性 / 适度性」四维，并**对照实际代码库逐条核验**
> 方案声称的根因与复用声明（`validate_combo_params` / `is_combo` / `validate_members` /
> `assert_norm_contract` / `assert_weights_cover` / `normalize_thresholds` / `create_factor_set` /
> `compose_score` / `causal_zscore` / `ToolRegistry` / `repositories.py` 行数 / `FactorLab` 焦点管理 等）。
> 严重度定义：**P0** = 会产出错误结果或破坏既有能力的硬伤；**P1** = 与代码现实冲突 / 关键缺口，会让
> 开发返工；**P2** = 准确性 / 健壮性建议，落地时遵循即可。

> 本轮评审于基线 `ad4dd2c` 逐节展开，并对照真实源码**逐条核验**方案声称的根因与复用声明。共发现
> **0 个 P0、4 个 P1、5 个 P2**；4 个 P1 已全部就地修复（见各节 v2 改动标注），P2 部分就地修复、其余列为
> 放行条件。无遗留 P0/P1。

### 0.1 根因 / 复用声明核验表

| # | 方案声明 | 核验源 | 结论 |
|---|---|---|---|
| 1 | `validate_combo_params` 首行 `if not is_combo: return`；`is_combo` 判 `meta.kind=="combo"` | `strategies/combo_specs.py:26-34` | ✅ 属实 |
| 2 | `FactorScoreStrategy.meta.kind=="factor"` | `strategies/factor_score_strategy.py:47` | ✅ 属实 |
| 3 | `create_strategy`/`update_strategy` 仅调 `validate_combo_params`，factor 无边界校验 | `api/strategies.py:48,59` | ✅ 属实（R1 缺口真实存在）|
| 4 | 非法 factor_score 快照「回测期 `prepare`/`compose_score` 抛 ValueError、运行期失败」 | `factor_score_strategy.py:85-96` | ❌ **不属实**：`prepare` 以 `try/except (ValueError, KeyError)` 兜底 → `_score=NaN` **安静空仓**；成员超 12 引擎不校验、**静默照算**。真实症状是「静默不交易 / 静默忽略上限」而非崩溃（见 P1-1）|
| 5 | `validate_members` 实现 frozen-direction / 1..12 / 已知 key 三检并抛 400 | `factors/specs.py:32-55` | ✅ 属实 |
| 6 | `create_factor_set` 顺序 = validate_members→normalize_thresholds→assert_norm_contract→assert_weights_cover→FactorSetValues→create_factor_set→factor_set_out | `api/factors.py:160-177`（含函数体内局部 import specs）| ✅ 属实 |
| 7 | `factors/service.py` 当前无 `create_set` | `factors/service.py`（74–286 行函数清单）| ✅ 属实（R2 新增无冲突）|
| 8 | Agent 工具现 7 项、无写组合 / 建策略工具 | `agent/tools/registry.py:38-59` | ✅ 属实 |
| 9 | 新工具注册入口为 `agent/registry.py` 的 `_ALL_TOOLS` | —— | ❌ **路径错误**：`_ALL_TOOLS` 在 `agent/tools/registry.py`，仓库根本无 `agent/registry.py`（见 P1-2）|
| 10 | 「Agent 已有 `mine_factors(persist=True)` 写能力，新写工具与之同级」 | `agent/tools/factor_tools.py` `MineFactorsTool.execute` | ❌ **不属实**：该工具 `persist=False` 硬编码、input_schema 无 persist 入口——Agent 当前在因子域**无任何写库能力**，两个新工具是净新增写能力（见 P1-3）|
| 11 | engine 退化行为：`causal_zscore` std0→NaN；`compose_score` auto→ValueError、空 / 全 0 权重→全 NaN；`train_weights` <30→ValueError；`compute_factor` 未知 key→ValueError | `scoring.py:41-115`,`training.py:209`,`library.py:241` | ✅ 属实 |
| 12 | FactorLab 缺 `role=dialog`/`aria-modal`/焦点陷阱 | `FactorLab.tsx:131` | ✅ 现状属实，但**该处方不当**：FactorLab 是与 SettingsPanel 同构的**全屏整页视图**（`page.tsx:494-501` 二选一整页切换、`FactorButton` 强制与设置互斥），既定约定即 `role="region"`（`SettingsPanel.tsx:38`，H7 注释明确选择 region）。对非浮层整页视图套 `aria-modal`+焦点陷阱属语义误用且破坏一致性（见 P1-4）|
| 13 | `db/repositories.py` 940 行、因子块 676–940 连续 | `db/repositories.py`（`wc -l`=939）| ⚠️ 文件实为 **939** 行、因子块 **676–939**；符号清单与连续性属实（见 P2-1）|
| 14 | 验收基线：后端 497 passed/2 deselected、前端 vitest 411 | iter3 `ad4dd2c` 迭代记录 | ✅ 属实 |

### 0.2 按严重度排序的问题清单

**P0（0 项）** —— 无。设计建立在三轮已落地（已逐文件核验）之上，五项均零迁移、零对外契约破坏，未见会产出错误结果或破坏既有能力的硬伤。

**P1（4 项，全部已就地修复并升 v2）**

- **P1-1（一致性 / 准确性，R1 根因症状描述错误）**：方案称非法 factor_score 快照「回测期抛 ValueError / 运行期失败」。实测 `prepare`（`factor_score_strategy.py:85-96`）捕获 `(ValueError, KeyError)` → `_score=NaN` **安静空仓**；且成员数超 12 在引擎层**无任何校验、静默照算**。真实危害是**静默失败**（用户得到一个静默不交易 / 静默越界上限的策略，毫无报错线索），比崩溃更伤「高成功率」——这反而**强化** R1 的必要性。已改正 §1 / §2.2 / §8 的症状表述（崩溃 → 静默）；R1 的修复契约、文件清单、§6.1 测试期望（创建期 400）均无需改动。
- **P1-2（可行性，R2 文件路径错误）**：`_ALL_TOOLS` 位于 `app/agent/tools/registry.py`，仓库**不存在** `app/agent/registry.py`。已在 §2.3#4、§3.1 改为正确路径，避免开发误建文件 / 找不到注册点而返工。
- **P1-3（一致性，R2 风险论据失实）**：RR2 与 §2.3 适度性以「Agent 已有 `mine_factors(persist=True)` 写能力、新工具与之同级」作缓解；实测 `MineFactorsTool` 的 `persist=False` 为硬编码、schema 无 persist 入口——Agent 在因子域**本无写库能力**。两个新写工具实为**净新增**写能力，应据实强调其真实护栏（Agent 工具启用开关 + 复用 R1 校验器 + 零新业务逻辑），而非援引一个不存在的「同级能力」。已改正 §2.3 适度性与 RR2。
- **P1-4（一致性 / 适度性，R4 无障碍处方过度且与约定冲突）**：FactorLab 是与 SettingsPanel 同构的**整页视图**（非浮层模态）。对其套 `role="dialog"`+`aria-modal="true"`+焦点陷阱：① 与 SettingsPanel 既定 `role="region"` 约定冲突（CLAUDE.md 一致性）；② 属 WAI-ARIA 语义误用——`aria-modal` 宣告「对话框外内容惰性」，而整页视图无「外部」可惰性化；③ 焦点陷阱对已占满视口、无并存内容的视图毫无意义，正是本方案别处自戒的「过度设计」。已在 §2.5 / §3.2 / §6.1 / RR6 改为：**保留 `role="region"` 与 SettingsPanel 对齐**，确认既有 a11y 基线（挂载聚焦标题 / Esc 关闭 / 卸载焦点归还 / `aria-label`，FactorLab 已全部具备）非回归即可；R4 的真实价值收敛到**查询失败兜底**与**挖掘空态**两处真实可靠性缺口。

**P2（5 项，已标注于正文 / 列为放行条件）**

- **P2-1（计数校正）**：`repositories.py` 为 **939** 行（非 940）、因子块 **676–939**；R5 拆出后残余 ≈680 行（含 re-export）、新模块 ≈265 行。已在 §2.6 / §3.1 / §6.1 校正数字。
- **P2-2（R1 import 口径）**：`api/strategies.py` 当前未从 `factors` 引入任何符号；现有约定是策略边界校验器经 `agent/services.py` 再导出后以 `services.X` 调用（`validate_combo_params` 即如此）。开发须明确择一并保持一致：或直接 `from ..factors.specs import validate_factor_strategy_params`（最短路径、与 `api/factors.py` 局部 import specs 同风格），或经 `services` 再导出走 `services.X`。已在 §2.2 标注。
- **P2-3（R1 None 守卫）**：`update_strategy` 的 factor-kind 判定须像 `is_combo` 一样对 `get_strategy_class(...)` 返回 `None` 做守卫（孤儿模板行场景），避免 `.meta.kind` 触发 `AttributeError`。已在 §2.2 标注。
- **P2-4（R2 单源对称性）**：方案把「建组合」单源化进 `service.create_set`，但「建 factor_score 策略」仍由工具与路由各自 `validate + repo.create_strategy`。鉴于校验已共用 `validate_factor_strategy_params`、逻辑极薄，可不强制再抽；若实现时发现漂移风险再考虑抽 `service.create_factor_strategy`。列为可选放行条件。
- **P2-5（R2 分层）**：`service.create_set` 需引入 `factor_set_out`（schemas 输出器）以回 dict，轻微下沉 schema 关注点到 service 层；为让 REST 与 Agent 同返结构，可接受。已在 §2.3 标注。

> 结论详见文末「评审结论」。无遗留 P0/P1。

---

## 1. 概述（Overview）

因子层经过三轮迭代已经「能用」：iter1 搭起了 `行情 → 计算 → 评估/挖掘 → 训练 → 组合得分 → factor_score
策略` 的独立分层；iter2 把训练端与打分端的归一化契约统一到唯一因果滚动 z-score（`resolve_norm_contract`），
并补齐挖掘去重、工作量护栏与权重单源；iter3 闭合了「看全部 → 编辑 → 激活/取消 → 进组合 → 训练 → 策略 →
回测」的用户主链路。功能上**已覆盖需求清单**，但全局目标的后两个词——**「可靠、高成功率」**——是关于
**「在所有入口、所有退化输入、所有运行环境下都不静默失败」**的。本轮（最后一轮）正是要把这层「可靠性
保证」补齐，而不是再叠功能。

本轮经逐文件核验，锁定五处仍然敞口、且都与全局目标直接相关的缺陷：**(R1)** `factor_score` 策略在
`POST/PUT /strategies` 创建/更新时**绕过了因子成员校验**（`validate_combo_params` 对非 combo kind 直接
返回，而 `factor_score.meta.kind=="factor"`），导致非法快照（`direction=="auto"`、未知 `factorKey`、成员数
越界）能被静默存进 DB；到了回测期，`prepare()` 对 `compose_score` 的 `(ValueError, KeyError)` 兜底成
`_score=NaN`（**安静空仓**），成员超 12 则引擎层不校验、**静默照算**——表现为「策略静默不交易 / 静默忽略
成员上限」而非显式报错（评审 P1-1 改正）。这种**静默失败**比崩溃更伤「高成功率」：用户得到一个看似已部署、
实则毫无交易的策略却无任何报错线索，正是要在创建期一次性显式 400 拦下的。**(R2)** Agent 现有 6 个数据工具能挖掘、评估、训练，却**无法
保存组合、无法部署策略**，mine→train 后断链——需求明确要求因子「作为工具提供给 Agent…作为策略的交易
信号」，闭环缺失即「不完善」。**(R3)** engine 层对退化输入（全 NaN、常数序列、样本不足、空/全零权重、含
NaN/Inf 的 OHLCV）的处理**良好但未被测试钉死**，是未受保护的不变量。**(R4)** 因子实验室全屏模态缺
查询失败兜底、挖掘空结果无空态（FactorLab 本身已与 SettingsPanel 同构、`role="region"` a11y 基线达标，评审
P1-4）——部分缺口不符合需求「符合人机交互最佳实践」。**(R5)** `db/repositories.py` 已 **939 行 > 800 行**
清洁度上限（iter2/iter3 明确记为「拆
`factor_repositories.py`」遗留项）。

本轮设计**严守前三轮的全部铁律**：严格因果、绝不假数据（OKX 不可达即显式报错）、`provenance` 仅供 UI
绝不参与打分、已保存组合自包含快照不随 `enabled` 失效、`auto` 绝不进组合。所有改动**零表迁移、零对外
契约破坏、零既有用例回归**；新增能力一律**复用既有校验器与服务编排**（不另造校验逻辑），以「单一真源」
保证高成功率。R5 为纯结构重构（零行为变更），R1/R3 是稳健性补强，R2 是完善性补强，R4 是体验补强——
五项分别精确命中「稳健 / 可靠 / 完善 / 高成功率 / 体验」，构成因子层的最终收口。

---

## 2. 技术设计（Technical design）

### 2.1 现状基线（ground truth，已逐文件核验）

- 策略创建：`api/strategies.py:create_strategy` → `services.validate_combo_params(templateKey, params)` →
  `repo.create_strategy`。`validate_combo_params`（`strategies/combo_specs.py:32`）首行 `if not is_combo(template_key): return`；
  `is_combo`（同文件）判定 `meta.kind == "combo"`。`FactorScoreStrategy.kind == "factor"`
  （`strategies/factor_score_strategy.py`）→ **校验被整体跳过**。
- 因子组合创建：`api/factors.py:create_factor_set`（160–177 行）顺序为 `validate_members` →
  `normalize_thresholds` → `assert_norm_contract` → `assert_weights_cover` → `repo.FactorSetValues` →
  `repo.create_factor_set` → `factor_set_out`。校验器全在 `factors/specs.py`。
- Agent 工具：`agent/tools/factor_tools.py` 现 7 项（`list_factors`/`compute_factor`/`evaluate_factor`/
  `mine_factors`/`train_factor_weights`/`score_factor_combo` + `show_factor` ui-action），均以
  `ctx.session_factory()` 取 DB 会话；异常由 `ToolRegistry.execute` 收敛为 `ToolResult(ok=False)`。**无任何
  写组合 / 建策略工具**。
- engine 退化行为（已读源）：`causal_zscore` 对 `std==0` 走 NaN（不除零）；`compose_score` 对 `auto` 抛
  `ValueError`、无非零权重时回全 NaN；`evaluate_factor` 用配对有限样本、空样本回 `None`；`train_weights`
  样本 `< MIN_TRAIN_SAMPLES(30)` 抛 `ValueError`。**这些行为无专门的退化输入回归测试**。
- 前端：`FactorLab.tsx` 与 `SettingsPanel.tsx` 同构——根容器 `role="region"` + `aria-label` + heading
  `tabIndex={-1}` 挂载聚焦 + Esc 关闭 + 卸载焦点归还（`FactorLab.tsx:112-131`，a11y 基线已达标，**非缺口**，
  评审 P1-4）。真实敞口：`FactorTable`/`MyFactorsSourcePicker` 查询失败仅依赖默认（无 `isError` 兜底）；
  `FactorMiningPanel` 扫描 0 行无空态。
- 清洁度：`db/repositories.py` = **939 行**；因子相关函数为 **676–939 行连续块**。其余因子文件均 < 800 行。

### 2.2 R1 — factor_score 策略边界校验补齐（稳健 / 高成功率，P0→P1）

**根因**：`factor_score` 经任何路径（REST / 前端 `FactorSetBuilder.createStrategy` / 未来 Agent 工具）创建时，
其内联快照 `params.members` 不经任何后端校验即落库。回测期 `FactorScoreStrategy.prepare`（`factor_score_strategy.py:85-96`）
对非法成员（`auto` 方向、未知 key）由 `compose_score` 抛出的 `(ValueError, KeyError)` **兜底成 `_score=NaN`**，
表现为**安静空仓**（非崩溃）；成员数超 12 引擎层**无校验、静默照算**。即非法快照的真实症状是**静默不交易 /
静默越界**而非显式失败（评审 P1-1 改正）——这正是 R1 要在边界一次性 400 拦下的。

**修复契约**：在**策略创建/更新边界**对 `kind=="factor"` 的策略统一执行因子成员校验，复用既有
`factors/specs.validate_members`（已实现 frozen-direction / 1..12 / 已知 factorKey 三检，抛 400
`FACTOR_DIRECTION`/`FACTOR_MEMBERS`/`FACTOR_UNKNOWN`），**不新造校验逻辑**。

**改动**：
1. `factors/specs.py`：新增薄封装 `validate_factor_strategy_params(params: dict | None) -> None`——取
   `(params or {}).get("members")`，交给现有 `validate_members` 校验（成员缺失/非数组 → 既有
   `FACTOR_MEMBERS` 400）。该函数是「因子策略快照」在策略边界的唯一校验入口，供 REST 与 Agent 共用。
2. `api/strategies.py`：`create_strategy` 在 `validate_combo_params` 之后追加——若
   `get_strategy_class(body.templateKey).meta.kind == "factor"`（`create_strategy` 已在首行保证模板非
   `None`，此处安全），调 `validate_factor_strategy_params(body.params)`；`update_strategy` 在已取出 `existing`
   后，若 `body.params is not None` 且其模板为 factor kind，同样校验。kind 判定复用 `get_strategy_class`（本文件
   已 import）。
   > 评审 P2-2（import 口径）：`api/strategies.py` 当前未从 `factors` 引入任何符号；现有约定是策略边界校验器
   > 经 `agent/services.py` 再导出后以 `services.X` 调用。开发须择一并保持一致——或直接
   > `from ..factors.specs import validate_factor_strategy_params`（最短、与 `api/factors.py` 局部 import specs
   > 同风格），或经 `services` 再导出走 `services.validate_factor_strategy_params`。
   > 评审 P2-3（None 守卫）：`update_strategy` 取 `existing["templateKey"]` 后须像 `is_combo` 一样对
   > `get_strategy_class(...)` 返回 `None`（孤儿模板行）做守卫，避免 `.meta.kind` 触发 `AttributeError`；可直接复用
   > 一个 `is_factor(template_key)` 薄封装（镜像 `is_combo`）统一 None 安全判定。
3. **不**改 `validate_combo_params` 本身（combo 语义不变），新增独立分支，避免误伤 combo 路径。

> 铁律对齐：校验只在**边界**做一次（CLAUDE.md「外部输入在边界处一次性校验」）；`prepare()`/`compose_score`
> 内部仍信任内联快照、保留对 `auto` 的 `ValueError` 作为**纵深兜底**，不重复校验。

### 2.3 R2 — Agent 闭环工具（完善 / 高成功率，P1）

**根因**：需求要求因子「作为工具提供给 Agent…作为交易信号」，但 Agent 工具链止于 `train_factor_weights`/
`score_factor_combo`，**不能持久化训练好的组合，也不能把组合部署成可回测/对比的 `factor_score` 策略**——
Agent 无法独立走完「挖掘 → 训练 → 保存 → 部署」闭环。

**修复契约**：补两个 Agent **写工具**，但**绝不另造业务逻辑**——一律复用 R1 校验器 + 既有 service/repo 编排，
保证与 UI/REST 完全同轨（同一成功率与同一错误码）。为消除路由与工具间的逻辑漂移，先把「创建组合」编排
**单源化**到 service 层。

**改动**：
1. `factors/service.py`：新增 `async def create_set(session, *, name, members_raw, method, config, model,
   metrics, enabled) -> dict`——内部顺序与 `api/factors.create_factor_set` 完全一致（`validate_members` →
   `normalize_thresholds` → `assert_norm_contract` → `assert_weights_cover` → `repo.FactorSetValues` →
   `repo.create_factor_set` → `factor_set_out`）。
2. `api/factors.py:create_factor_set`：**重构为调用** `service.create_set(...)`（行为字节级不变，仅消除重复，
   单一真源）。
3. `agent/tools/factor_tools.py`：
   - `SaveFactorSetTool`（`name="save_factor_set"`, `category="factor"`）：入参 `name`/`members`/`method?`/
     `modelJson?`/`config?`/`metricsJson?`/`enabled?`；`execute` 内 `async with ctx.session_factory()` 调
     `factor_service.create_set(...)`；`summary` 回「已保存组合『{name}』(#id)，成员 N 个，方法 {method}」，
     `payload` 为 `factor_set_out` 结果。校验失败（`HTTPException`）由 `ToolRegistry.execute` 收敛为
     `ok=False` + 中文消息。
   - `CreateFactorStrategyTool`（`name="create_factor_strategy"`, `category="factor"`）：入参
     `name`/`members`（含权重与冻结方向）/`thresholds?`/`normStats?`/`provenance?`；`execute` 内先
     `validate_factor_strategy_params({"members": members})`（R1），再以
     `repo.create_strategy(session, "factor_score", name, {"members":..., "thresholds":..., "normStats":...,
     "provenance":...})` 落库（内联快照，与 `FactorSetBuilder.createStrategy` 同构）；`summary` 回「已创建
     factor_score 策略『{name}』(#id)，已可在回测/对比中使用」。
4. `agent/tools/registry.py`：把两个新工具 import 进来并加入 `_ALL_TOOLS`（自动获得「设置中心 · Agent 工具
   管理」启用开关，`category="factor"` 已存在分组）。**注意路径**：注册表是 `app/agent/tools/registry.py`，
   仓库不存在 `app/agent/registry.py`（评审 P1-2 改正）。
5. `agent/prompts.py`：在因子 primer / `FACTOR_REASONING_SCAFFOLD` 描述里补全闭环
   （`mine_factors → evaluate_factor → train_factor_weights → save_factor_set → create_factor_strategy →
   （既有）backtest/compare`），并强调「方向须冻结、normWindow 训练后锁定、provenance 仅记录不参与打分」。

> 适度性：两个写工具是「完善需求」的必要项，且**零新业务逻辑**（全部委托 R1 校验器 + `service.create_set` /
> `repo.create_strategy`），blast radius 收敛在工具薄包装层。**评审 P1-3 校正**：Agent 当前在因子域**本无写库
> 能力**（`MineFactorsTool` 的 `persist=False` 为硬编码、schema 无 persist 入口），故这两个工具是**净新增**写能力——
> 正因如此更须据实强调其真实护栏：① 受 Agent 工具启用开关约束（运维可逐工具关闭）；② 校验与 REST/UI 完全同轨
> （同一 `validate_factor_strategy_params` / `service.create_set` 单源、同一错误码）；③ 失败统一由 `ToolRegistry.execute`
> 收敛为 `ok=False`，绝不假数据。
> `service.create_set` 需引入 `factor_set_out`（schemas 输出器）以回 dict（评审 P2-5：为让 REST 与 Agent 同返结构，
> schema 关注点轻微下沉到 service 层，可接受）。

### 2.4 R3 — 数值鲁棒性回归测试 battery + 补齐发现的护栏（可靠，P1）

**根因**：engine 退化行为正确但未被测试固化，回归风险高（任何后续重构都可能悄悄破坏「std=0 → NaN」这类
不变量）。

**修复契约**：以**回归优先**（CLAUDE.md 七）新增一个专门的鲁棒性测试套件，把每个退化场景钉成「不崩、返回
NaN/None/note + 显式错误、绝不假数据」的不变量；**仅当测试暴露真实缺口时**才就地补**最小**护栏（不为「理论
上不会发生」的分支写防御噪音）。

**改动**：
1. `tests/test_factor_robustness.py`（新建）：用确定性合成 K 线构造退化输入，覆盖——
   - `compute_factor`：极端 params（如 `win` 超过序列长度）→ 全 NaN，不抛异常；未知 key → `ValueError`。
   - `causal_zscore`：常数序列（`std==0`）→ 全 NaN，不除零、不 Inf。
   - `compose_score`：成员含 `auto` → `ValueError`；权重全 0 / 空权重 → 全 NaN（不 Inf）；`squash` 边界。
   - `evaluate_factor`：`occurrences==0`（全 NaN 因子）→ 各 horizon stat 为 `None`、`coverage==0`，不抛。
   - `train_weights`：样本 `< MIN_TRAIN_SAMPLES` → `ValueError`（service 转 400 `FACTOR_TRAIN_FAILED`）；
     常数 label → 不崩，权重退化可解释（`ic_weight`/`linear` 回退 equal 或返回有限权重）。
   - `mine_factors`：候选含 bar 不兼容因子 → 跳过且 `skipped` 计数正确，整轮不报错。
   - OHLCV 含 `NaN`/`Inf`（防御性）：计算链路不传播出 `Inf`（必要时在 `candles_to_df` 之后、engine 入口
     处 `replace([inf,-inf], nan)`——**仅当测试证明确有传播时**才加）。
2. `factors/service.py` / `engine/factors/*.py`：仅在 1 中发现真实缺口时补最小护栏，每处补丁附「为什么」注释
   与可回退条件，且必须由 1 中对应用例钉死。
3. `tests/test_factors_service.py`：补 `score_set`/`score_inline` 在退化组合（全 NaN 成员）下回
   `note=="window_too_short"` + `minBarsRequired` 的断言（若已有则跳过）。

> 隔离纪律：新测试沿用 `aragon-quant-test-isolation`（共享临时 DB；需要时调 `clear_candle_cache()`
> 防缓存串扰），全部用合成 K 线、零网络。

### 2.5 R4 — 因子实验室无障碍与可靠性打磨（顶级设计 / 人机交互，P1）

**根因**（评审 P1-4 改正）：FactorLab 是与 SettingsPanel **同构的整页视图**（`page.tsx:494-501` 二选一整页切换、
`FactorButton` 强制与设置面板互斥，绝非浮层模态）。SettingsPanel 既定 a11y 约定即根容器 `role="region"`
（`SettingsPanel.tsx:38`，H7 注释明确选择 region）+ 挂载聚焦标题 + Esc 关闭 + 卸载焦点归还；FactorLab 已**逐项
具备**（`FactorLab.tsx:112-131`：`role="region"` + `aria-label="因子实验室"` + `headingRef.focus()` + Esc +
卸载归还 `factor-lab-button`）。因此**原方案给整页视图套 `role="dialog"`/`aria-modal`/焦点陷阱是过度设计且语义
误用**——`aria-modal` 宣告「对话框外内容惰性」，而整页视图无并存「外部」可惰性化；焦点陷阱对占满视口的视图毫无
意义，且与 SettingsPanel 约定冲突。FactorLab 真实敞口的是**查询失败兜底**与**挖掘空态**两处可靠性缺口。

**修复契约**：纯前端、零后端改动，**右量化**到真实缺口；无障碍维持与 SettingsPanel 对齐的 `role="region"` 基线。
1. `components/Factor/FactorLab.tsx`：**保留 `role="region"`，不改为 dialog/aria-modal、不加焦点陷阱**（与
   SettingsPanel 一致）；仅复核既有 a11y 基线（挂载聚焦标题 / Esc 关闭 / 卸载焦点归还 / `aria-label`）非回归。
2. `components/Factor/FactorTable.tsx` + `FactorSetBuilder.tsx`（`MyFactorsSourcePicker`）：因子列表/下拉
   `useQuery` 增 `isError` 兜底文案「加载因子失败，请重试」（P2-7 遗留项），并提供重试入口（`refetch`）。
3. `components/Factor/FactorMiningPanel.tsx`：`mine` 成功但 `items` 为空 → 渲染空态「该区间/类别下未发现有效
   因子，请调整区间或类别后重试」（区分于「未扫描」初始态）。
4. 复核既有空态/错误态（`FactorEvalPanel`/`FactorScoreChart`/`FactorCatalog`）不回归。

> 不做：不引入新的 toast 库 / 设计系统（保持现有内联反馈范式，避免过度设计 R7-iter1）；**不把整页视图改造成
> 对话框语义**（评审 P1-4）；响应式仅在确有溢出处增补，不做全量断点重排。

### 2.6 R5 — db/repositories.py 拆分（稳健 / 可维护，P2）

**根因**：`repositories.py` = **939 行**（评审 P2-1 校正，原文 940）> 800 行上限（记录在案的遗留项）。

**修复契约**：把因子/组合相关 repo 代码（**676–939 行连续块**：`FactorValues`/`FactorPatch`/`_primary_rankic`/
`list_factors`/`get_factor`/`create_factor`/`update_factor`/`set_factor_eval`/`delete_factor`/
`_factor_dedupe_key`/`_params_of`/`bulk_create_factors`/`FactorSetValues`/`FactorSetPatch`/`list_factor_sets`/
`get_factor_set`/`create_factor_set`/`update_factor_set`/`delete_factor_set`）整体迁到新模块
`db/factor_repositories.py`；`repositories.py` 顶部**显式 re-export** 全部上述符号，保证所有现有 `repo.X`
调用点（`api/factors.py`/`agent/tools/factor_tools.py`/`factors/service.py`/seed/测试）**零改动**。

**改动**：
1. `db/factor_repositories.py`（新建，~265 行）：搬运上述块；补齐其依赖 import（`Factor`/`FactorSet` 模型、
   `json`、`select`、`AsyncSession`、`FACTOR_LIBRARY.category` 缓存所需引用等——以「迁移后能独立 import 通过」
   为准）。
2. `db/repositories.py`：删除该块；在合适位置加 `from .factor_repositories import (FactorValues, FactorPatch,
   list_factors, get_factor, create_factor, update_factor, set_factor_eval, delete_factor,
   bulk_create_factors, FactorSetValues, FactorSetPatch, list_factor_sets, get_factor_set, create_factor_set,
   update_factor_set, delete_factor_set)`（私有 `_*` 不必导出，随模块内引用走）。拆分后 `repositories.py`
   ≈ 680 行（< 800，含 re-export），`factor_repositories.py` ≈ 265 行。
3. 验收：`grep` 确认无遗漏调用点；后端全量 `pytest` 零回归即证明 re-export 完整。

> 适度性：纯机械迁移 + re-export，**零行为变更**；是清洁度合规而非功能改动，列 P2、可在时间压力下与核心项
> 解耦（不阻断 R1–R4）。

### 2.7 端到端关键路径（闭环时序）

```
Agent 自主闭环（R1+R2）：
  mine_factors → evaluate_factor（定方向/IR）→ train_factor_weights（得权重+样本内外IC）
    → save_factor_set（service.create_set：validate_members+契约校验+落库）
    → create_factor_strategy（validate_factor_strategy_params + repo.create_strategy 内联快照）
    → （既有）backtest/compare/walk-forward 路径，引擎读内联快照、零额外 IO

人工/REST 路径（R1）：
  POST/PUT /strategies(templateKey=factor_score) → validate_combo_params(no-op)
    → 【新增】kind=="factor" → validate_factor_strategy_params → repo.create/update_strategy
```

---

## 3. 文件 / 模块变更计划（File / Module Change Plan）

### 3.1 后端（services/quant）

| 文件 | 新建/修改 | 一句话意图 |
|---|---|---|
| `app/factors/specs.py` | 修改 | 新增 `validate_factor_strategy_params(params)`，复用 `validate_members` 作为因子策略快照的边界校验单源（R1/R2）。 |
| `app/api/strategies.py` | 修改 | `create_strategy`/`update_strategy` 对 `kind=="factor"` 追加 `validate_factor_strategy_params`（R1）。 |
| `app/factors/service.py` | 修改 | 新增 `create_set(...)` 把「创建组合」编排单源化，供 REST 与 Agent 共用（R2）。 |
| `app/api/factors.py` | 修改 | `create_factor_set` 重构为调用 `service.create_set`（行为不变，去重，R2）。 |
| `app/agent/tools/factor_tools.py` | 修改 | 新增 `save_factor_set` + `create_factor_strategy` 两个写工具（薄包装 service/repo + R1 校验，R2）。 |
| `app/agent/tools/registry.py` | 修改 | 注册两个新工具进 `_ALL_TOOLS`（R2；评审 P1-2：非 `app/agent/registry.py`）。 |
| `app/agent/prompts.py` | 修改 | 因子 primer/scaffold 补全闭环描述与铁律提醒（R2）。 |
| `app/engine/factors/*.py` · `app/factors/service.py` | 修改（按需） | 仅当 R3 测试暴露真实缺口时补最小护栏，每处带「为什么」注释（R3）。 |
| `app/db/factor_repositories.py` | 新建 | 迁入因子/组合 repo 代码（676–939 行连续块），独立可 import（R5）。 |
| `app/db/repositories.py` | 修改 | 删除因子块 + 显式 re-export，保持所有 `repo.X` 调用点不变（R5）。 |

### 3.2 前端（apps/web）

| 文件 | 新建/修改 | 一句话意图 |
|---|---|---|
| `components/Factor/FactorLab.tsx` | 复核 | **保持 `role="region"` 与 SettingsPanel 对齐**（评审 P1-4：不改 dialog/aria-modal、不加焦点陷阱）；复核既有 a11y 基线（聚焦标题 / Esc / 焦点归还 / `aria-label`）非回归（R4）。 |
| `components/Factor/FactorTable.tsx` | 修改 | 列表查询 `isError` 兜底文案 + 重试（R4）。 |
| `components/Factor/FactorSetBuilder.tsx` | 修改 | `MyFactorsSourcePicker` 下拉查询失败兜底（R4）。 |
| `components/Factor/FactorMiningPanel.tsx` | 修改 | 扫描返回空结果时的空态指引（R4）。 |

> 前端**无新增类型/接口契约改动**：R1/R2 全在后端，前端既有 `FactorSetBuilder.createStrategy` 仍走
> `POST /strategies`，新增的后端校验只会把非法快照从「静默存→回测期安静空仓」（评审 P1-1）改为「创建时 400」，
> 前端既有 `createStrategy.isError` 通道（`FactorSetBuilder.tsx:233-235`）自动呈现该错误，**无需前端改动**。

### 3.3 测试

| 文件 | 新建/修改 | 一句话意图 |
|---|---|---|
| `tests/test_factor_robustness.py` | 新建 | 退化输入鲁棒性 battery（compute/zscore/compose/evaluate/train/mine，R3）。 |
| `tests/test_strategies_api.py`（或既有策略 API 测试） | 修改 | 钉死 `factor_score` 创建/更新非法快照（auto/未知 key/越界）→ 400（R1）。 |
| `tests/test_factor_tools.py` | 修改 | `save_factor_set`/`create_factor_strategy` 成功路径 + 校验失败 `ok=False`（R2）。 |
| `tests/test_factors_api.py` | 修改 | `create_factor_set` 重构后行为不变回归（R2）。 |
| `apps/web/lib/factor.test.ts` 或组件测试 | 修改（按需） | 查询失败兜底 / 挖掘空态以组件渲染验证；其余以 `tsc`+`vitest`+`build` 守护（R4，不含焦点陷阱，评审 P1-4）。 |

---

## 4. 接口设计（Interface design）

### 4.1 REST（增量；均沿用 `{error:{code,message}}` 错误体）

- `POST /api/strategies`（templateKey=`factor_score`）：**新增**校验——`params.members` 非法时返回 **400**
  `FACTOR_DIRECTION` / `FACTOR_MEMBERS` / `FACTOR_UNKNOWN`（成功路径与响应体不变）。
- `PUT /api/strategies/{id}`（kind=`factor` 且带 `params`）：同上新增校验。
- `POST /api/factor-sets`：响应/错误码**完全不变**（内部改调 `service.create_set`，字节级等价）。

### 4.2 Agent 工具签名（input_schema 摘要，category=`factor`）

```jsonc
// save_factor_set —— 持久化一个（通常已训练的）因子组合
{ "name": "string(必填)",
  "members": "[{factorKey, direction(long|short), params?, weight?}] (必填, 1..12)",
  "method": "equal|ic_weight|linear|random_forest|xgboost (默认 equal)",
  "modelJson": "object? (训练产物：weights/normStats/provenance/trainerVersion)",
  "config": "object? (含 thresholds)", "metricsJson": "object?", "enabled": "boolean? (默认 true)" }
// → ok=true: summary「已保存组合『{name}』(#id)…」, payload=factor_set_out
// → ok=false: 校验失败（成员/契约/权重）如实回中文消息

// create_factor_strategy —— 以内联快照部署 factor_score 策略
{ "name": "string(必填)",
  "members": "[{factorKey, direction(long|short), params?, weight}] (必填, 1..12)",
  "thresholds": "object? (enterLong/enterShort/exitBand/normWindow/squash, 默认 DEFAULT_THRESHOLDS)",
  "normStats": "object? ({normWindow, clip})", "provenance": "object? (仅 UI 展示, 不参与打分)" }
// → ok=true: summary「已创建 factor_score 策略『{name}』(#id)，可在回测/对比中使用」, payload={strategyId, name}
// → ok=false: members 非法 → 校验消息
```

> 两个工具均经 `ctx.session_factory()` 取会话；异常统一由 `ToolRegistry.execute` 收敛为 `ToolResult(ok=False)`
> （OKX 不可达 / 校验失败 / DB 错误皆如实回报，绝不假数据 C1）。

---

## 5. 数据模型（Data model）

**本轮零表迁移、零字段新增、零 persist 版本变更。**

- `factors` / `factor_sets` 表结构沿用 iter1（`factor_sets.members_json` 自包含快照、`model_json` 存
  weights/normStats/provenance、`config_json` 存 thresholds）。
- `strategies.params_json` 对 `factor_score` 的内联快照形状沿用 iter1/iter3
  （`{members:[{factorKey,params,weight,direction}], thresholds, normStats?, provenance?}`）；R1/R2 只是把
  「写入前是否校验」补齐，不改形状。
- R5 仅迁移 Python 模块边界，**不触及任何 SQL/ORM 定义**（`Factor`/`FactorSet` 模型仍在 `db/models.py`）。
- 前端 store persist 版本保持 **v17**（无新增持久化字段，R4 为纯交互/无障碍改动）。

---

## 6. 测试与验收标准（Testing & acceptance criteria）

### 6.1 测试（回归优先）

- **R1**：`factor_score` 创建/更新——`direction:"auto"` → 400 `FACTOR_DIRECTION`；未知 `factorKey` → 400
  `FACTOR_UNKNOWN`；空成员/>12 → 400 `FACTOR_MEMBERS`；**合法快照仍 201/200**（不误伤）；combo/atomic 策略
  创建路径**零行为变化**（`validate_combo_params` 分支未动）。
- **R2**：`save_factor_set` 合法成员 → 落库且 `score_factor_combo` 能对其打分；非法成员 → `ok=False`。
  `create_factor_strategy` → 新策略出现在 `GET /strategies` 且可 `POST /strategies/{id}/backtest`。
  `create_factor_set` 重构后 `test_factors_api.py` 全绿（字节级等价）。
- **R3**：`test_factor_robustness.py` 所有退化用例「不崩 + 预期 NaN/None/note/显式错误」；新增护栏（若有）各有
  对应用例钉死。
- **R4**：`tsc --noEmit` 干净；`vitest` 既有 + 新增（若有）全绿；`next build` 成功。FactorLab 保持
  `role="region"` 与 SettingsPanel 一致、既有 a11y 基线（聚焦标题 / Esc / 焦点归还 / `aria-label`）非回归
  （评审 P1-4，不引入 dialog/焦点陷阱）；查询失败兜底 / 挖掘空态以组件渲染验证。
- **R5**：后端全量 `pytest` 零回归即证 re-export 完整；`repositories.py` ≈ 680 行 < 800、
  `factor_repositories.py` ≈ 265 行 < 800（`.claude-index/config.md` 阈值，无需调阈值）。

### 6.2 验收标准（Definition of Done，下游对照）

1. `docs/plans/factor-layer-reliability/spec.md` 存在且 `git status` 可见，全部必备章节齐备、篇幅 ≥ 800 字。
2. R1–R5 按 §3 文件清单逐项落码；R5 为 P2 可在时间压力下解耦但**默认应完成**。
3. 五项门禁全绿：后端 `pytest`（基线 iter3 = 497 passed/2 deselected，**新增用例后只增不减、零回归**；2 个
   pre-existing flaky agent-WS 计时用例不计）、前端 `npx tsc --noEmit` 干净、`vitest`（基线 411）零回归、
   `next build` 成功。**不跑 `npm run lint`**（沿用前三轮门禁口径）。
4. 铁律不破：边界校验只在创建/更新做、引擎内部不重复校验；`provenance` 不参与打分；OKX 不可达即报错；
   已保存组合自包含快照不随 `enabled` 失效；`auto` 绝不进组合。
5. 实施偏差回填 §10。

---

## 7. 风险与缓解（Risks & mitigations）

| # | 风险 | 缓解 |
|---|---|---|
| RR1 | R1 新校验误伤合法 `factor_score` 快照，破坏既有 UI 创建路径。 | 复用既有 `validate_members`（前端已按同规则校验），仅对 `kind=="factor"` 生效；补「合法快照仍 201」正向用例；combo/atomic 分支零改动。 |
| RR2 | R2 给 Agent **净新增**写能力（评审 P1-3：因子域此前无写库工具，`mine_factors` 的 `persist=False` 硬编码），可能误写脏组合/脏策略。 | 两工具**零新业务逻辑**，全部委托 R1 校验器 + `service.create_set`/`repo.create_strategy`（与 REST/UI 同一单源、同一错误码）；受 Agent 工具启用开关约束（运维可逐工具关闭）；失败统一 `ToolRegistry.execute` 收敛为 `ok=False`。 |
| RR3 | R2 `create_set` 重构使 `POST /factor-sets` 行为漂移。 | 重构为「同函数体搬到 service + 路由转调」，字节级等价；`test_factors_api.py` 回归钉死。 |
| RR4 | R3 为「不会发生」的分支加防御噪音，违背 CLAUDE.md 五。 | 护栏**仅在测试暴露真实传播时**才加，每处带「为什么」+ 可回退注释，且必有对应用例。 |
| RR5 | R5 拆分遗漏调用点 / 循环 import，启动即崩。 | re-export 全量符号保持 `repo.X` 不变；新模块以「独立 import 通过」为准；后端全量 `pytest`（import 即触发）守护；私有 `_*` 不外露。 |
| RR6 | R4 误把整页视图改成对话框语义 / 焦点陷阱，反而破坏与 SettingsPanel 一致性并困住键盘用户。 | 评审 P1-4 已撤销该处方：FactorLab **保持 `role="region"` 与 SettingsPanel 对齐、不加焦点陷阱**；R4 仅做查询失败兜底 + 挖掘空态 + 既有 a11y 基线非回归核验。 |
| RR7 | 本轮改动牵连既有 491+ 用例造成回归。 | 全部最小侵入、零迁移、零契约破坏；门禁以「既有用例零回归」为硬条件（§6.2.3）。 |

---

## 8. 与全局目标的对齐

全局目标「**完善、稳健、可靠、高成功率**」逐词命中：**完善** ← R2 补全 Agent「挖掘→训练→保存→部署」
闭环（需求明确项）；**稳健** ← R1 把策略边界校验补到所有入口、R5 清掉清洁度违规；**可靠** ← R3 把 engine
退化行为固化为受保护的回归不变量；**高成功率** ← R1 把回测期的**静默失败**（安静空仓 / 静默越界，评审 P1-1）
提前为创建期显式 400、R2 让 Agent 全程复用同一校验单源；**体验** ← R4 收口因子实验室的查询失败兜底与挖掘空态，
并维持与 SettingsPanel 一致的 `role="region"` 无障碍基线（评审 P1-4，非误套对话框语义）。所有项**零迁移、零
回归、严守既有铁律**，是因子层四轮迭代的最终收口而非新功能叠加。

---

## 9. 实现顺序建议（供 Subtask #2）

1. **R1**（specs `validate_factor_strategy_params` + api/strategies 接线 + 用例）——最小且解锁 R2。
2. **R3**（鲁棒性 battery + 按需护栏）——纯后端/纯测试，早暴露 engine 边界问题。
3. **R2**（service `create_set` 单源化 + 路由转调 + 两个 Agent 工具 + registry + prompts + 用例）。
4. **R4**（前端无障碍/兜底/空态）——纯前端。
5. **R5**（repositories 拆分 + re-export）——最后做，避免与前序改动产生大 diff 串扰。
6. 全量门禁：后端 `pytest` + 前端 `tsc`/`vitest`/`build` 零回归（无 `npm run lint`）。

> 严格按本「文件/模块变更计划」逐项落码，不做超出方案的额外重构；实施中发现的方案缺陷回填 §10。

---

## 10. 实施过程发现的方案缺陷（由 Subtask #2 回填）

实施按 §9 顺序（R1→R3→R2→R4→R5）完成，方案与代码现实**高度吻合，未发现需要纠偏的硬缺陷**。
以下逐条记录放行条件的最终处置与实现期的小发现：

- **（P2-2 处置 · import 口径择一）** 采用**直接导入**：`api/strategies.py` 顶部
  `from ..factors.specs import is_factor, validate_factor_strategy_params`，与 `api/factors.py`
  局部 import specs 同风格，全程不经 `services` 再导出，**未两路混用**。已核验 `factors.specs` 新增
  `from ..strategies.base import get_strategy_class` 无循环依赖（`strategies.base` 仅依赖
  `engine.simulator`，不反向 import factors；全量 pytest import 即触发、522 passed 佐证）。
- **（P2-3 处置 · None 守卫）** 在 `factors/specs.py` 新增 `is_factor(template_key)`（镜像
  `combo_specs.is_combo`，对 `get_strategy_class(...) is None` 安全返回 `False`）。`create_strategy`
  与 `update_strategy` 均经 `is_factor` 判定 kind，杜绝孤儿模板行触发 `.meta.kind` 的 `AttributeError`。
- **（P2-4 处置 · 单源对称性）** 维持方案默认：「建组合」单源化进 `service.create_set`（REST 路由与
  `save_factor_set` 工具共用）；「建 factor_score 策略」仍由 `create_factor_strategy` 工具与
  `POST /strategies` 路由各自 `validate_factor_strategy_params + repo.create_strategy`。两路已共用同一
  R1 校验单源、逻辑各仅 2~3 行，**未观察到漂移风险**，故不强行抽 `service.create_factor_strategy`
  （遵循 P2-4「逻辑极薄可不强制再抽」）。
- **（P2-5 处置 · schema 下沉）** `factors/service.py` 引入 `from ..schemas import factor_set_out`
  使 `create_set` 回 dict，让 REST 与 Agent 同返结构。已核验 `schemas` 不反向 import `factors/service`，
  无循环依赖。按方案接受此轻微下沉。
- **（R3 护栏纪律 · 不加防御噪音）** 鲁棒性 battery（`tests/test_factor_robustness.py`，15 例）实测：
  `compute_factor` 极端窗口→全 NaN / 未知 key→ValueError、`causal_zscore` 常数→全 NaN 无 Inf、
  `compose_score` auto→ValueError / 全零·空权重→全 NaN、`evaluate_factor` 全 NaN·常数→各 stat None、
  `train_weights` <30→ValueError / 常数 label→等权回退、`mine_factors` bar 不兼容→跳过——**全部已是
  正确不变量**。关键判断：**未新增任何 engine 护栏**。原方案预留的「OHLCV 含 Inf → 在 engine 入口
  `replace([inf,-inf], nan)`」经测试证明**无需添加**：`compose_score` 经 `causal_zscore` 的 clip 天然
  Inf-safe、`score_points`/`_series_points`/`evaluate` 经 isfinite 过滤/配对天然不外泄 Inf，「不传播出
  Inf」不变量**已在边界成立**。真实 OKX K 线（`load_range_candles`）永不为 Inf，再加 replace 属对
  「理论上不会发生」分支的防御噪音，违反 CLAUDE.md 五——故据 R3 纪律按下不表，仅以 2 个用例钉死边界
  不变量。
- **（R4 右量化 · 既有覆盖确认）** 复核发现 `FactorSetBuilder.MyFactorsSourcePicker` 的查询失败兜底
  （`enabledFactors.isError → 「加载因子失败，请重试」`）**已在 iter3（L2/L3）落地**，本轮无需改动，仅核验非
  回归。真实敞口只剩 `FactorTable` 缺 `isError`——已补「加载因子失败，请重试」+ `refetch` 重试入口。
  `FactorMiningPanel` 空态文案升级为「该区间/类别下未发现有效因子，请调整区间或类别后重试」（表格仅在
  扫描后渲染，天然区分于未扫描初始态）。`FactorLab` 维持 `role="region"`、未改 dialog/aria-modal、未加焦点
  陷阱（评审 P1-4）。**未新增组件渲染测试**：R4 改动为小而自包含的条件渲染兜底，已由 `tsc --noEmit` +
  `vitest`（既有 411）+ `next build` 三道门禁守护（§3.3「按需」+ R4 右量化），且为 `FactorTable` 引入
  render 测试需导出 `FactorLab` 内部 `WorkbenchContext`（超出本轮 diff 范围）。
- **（R5 拆分 · 零循环 import）** `db/factor_repositories.py`（283 行）独立 import 通过；`repositories.py`
  （691 行 < 800）删除因子块后从 `.models` 移除已不再使用的 `Factor`/`FactorSet` 导入，并在文末显式
  re-export 16 个公开符号（私有 `_*` 不外露）。因子 seed（`db/session.py:_seed_builtin_factors`）直接
  `from .models import Factor`，不经 repo，故拆分零影响。后端全量 522 passed 证明 re-export 完整、所有
  `repo.X` 调用点零改动。

**门禁实测（基线 iter3 = 后端 497 passed/2 deselected、前端 vitest 411）**：后端 `pytest`
**522 passed / 2 deselected**（+25 新例：R1×6 + R3×15 + R2×4；2 deselected 为既有 flaky agent-WS 计时用例，
非本轮引入）；前端 `npx tsc --noEmit` 干净；`vitest` **50 文件 / 411 passed**（零回归）；`next build`
**Compiled successfully**。零回归、零迁移、零对外契约破坏。

---

## 评审结论（Review Verdict）

### 四维评审小结

- **可行性（Feasibility）**：R1–R5 全部可在现栈直接落地，已对照源码核验复用点真实存在
  （`validate_members` / `is_combo` / `compose_score` / `_ALL_TOOLS` / 因子 repo 连续块均属实）。唯一硬可行性
  瑕疵是 R2 的注册文件路径错误（`agent/registry.py` 不存在），已修正为 `agent/tools/registry.py`（P1-2）。
- **完整性（Completeness）**：边界覆盖良好——R1 兼顾 create + update 两路、R3 以回归优先钉死退化输入、
  R5 以 re-export 守住全部调用点。补充了 R1 的 import 口径（P2-2）与 None 守卫（P2-3）两处实现期易漏的边界。
- **一致性（Consistency）**：三处与代码现实/既有约定冲突已修复——R1 症状由「崩溃」改为「静默失败」（P1-1）、
  R2 风险论据撤掉失实的「mine_factors 已有写能力」（P1-3）、R4 撤销与 SettingsPanel `role="region"` 约定冲突的
  对话框处方（P1-4）。修复后全文严守 CLAUDE.md 边界校验/错误显式传播/避免过度设计诸条。
- **适度性（Right-sizing）**：整体右量化得当（零迁移、零契约破坏、复用单源）。R4 原本是唯一过度设计点
  （给整页视图套 dialog/焦点陷阱），已收敛到「查询失败兜底 + 挖掘空态 + a11y 基线非回归」两处真实缺口。
  R2 单源对称性（P2-4）与 service 层 schema 下沉（P2-5）属可接受的微小权衡，列为条件。

### 修复情况

- **P0**：0 项。
- **P1**：4 项（P1-1 R1 症状 / P1-2 注册路径 / P1-3 风险论据 / P1-4 R4 处方），**已全部就地修复并升 v2**，
  分别落在 §0.2、§1、§2.2、§2.3、§2.5、§2.6、§3.1、§3.2、§3.3、§6.1、§8、RR2、RR6。无遗留 P1。
- **P2**：5 项，P2-1（计数 940→939）已就地校正；P2-2/P2-3 已在 §2.2 标注为实现期必办；P2-4/P2-5 列为放行条件。

### 结论：**有条件通过（Approved with conditions）**

设计根因真实、复用充分、与全局目标「完善/稳健/可靠/高成功率」逐词对齐，且全部零迁移、零回归、严守既有铁律。
P0=0、P1 已全部就地修复。Subtask #2 落码时须满足以下放行条件：

1. **（P2-2）** `validate_factor_strategy_params` 的 import 口径择一并保持一致（直接 `from ..factors.specs import`
   或经 `services` 再导出），不得两路混用。
2. **（P2-3）** `update_strategy` 的 factor-kind 判定必须对 `get_strategy_class(...)` 返回 `None` 做守卫
   （建议抽 `is_factor(template_key)` 镜像 `is_combo`），杜绝 `.meta.kind` 的 `AttributeError`。
3. **（R3 纪律）** 鲁棒性护栏**仅在测试暴露真实传播时**才补，每处带「为什么 + 可回退」注释并由对应用例钉死；
   不得为「理论上不会发生」的分支加防御噪音（CLAUDE.md 五）。
4. **（R4 边界）** FactorLab **保持 `role="region"`**，不得改为 dialog/aria-modal、不得加焦点陷阱；R4 仅做查询失败
   兜底 + 挖掘空态 + 既有 a11y 基线非回归核验。
5. **（门禁）** 五项门禁零回归：后端 `pytest`（基线 497 passed/2 deselected，新增用例只增不减）、前端
   `tsc --noEmit` 干净、`vitest`（基线 411）零回归、`next build` 成功；不跑 `npm run lint`。
6. **（偏差回填）** 实施中发现的方案缺陷（含 P2-4 单源对称性、P2-5 分层取舍的最终处置）回填 §10。
