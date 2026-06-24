# 用户自定义组合策略管理（保存 / 增删改查 / 多页签成员选择 / 详情弹窗）

> 设计文档 **v2**（方案评审与修复）· feature slug: `strategy-combo-management`
> 作者：方案设计节点（标准四阶段开发 · Subtask #0）；评审与修复：方案评审节点（Subtask #1）
> 关联前序方案：`docs/plans/regime-adaptive-strategy-combination/spec.md`（体制自适应轮动组合，已落地）
> 评审摘要：以「方案声明 vs. 真实源码」逐节核对（三路并行核验：后端组合路由/CRUD、前端组件、前端 lib+store），确认架构与既有事实**整体准确、可落地**。修复 2×P1（详情弹窗回测响应的 `combo` 字段在 `api.backtest` 返回类型中被丢弃；`POLICY_HINTS`/`PolicyEditor` 为 `ComboView` 内联私有、详情弹窗无法「复用」），就地校正 5×P2（详情弹窗成员名数据源措辞、保存/编辑名称校验与回填、纯助手清单缺正向构造器、列表空/载入态与脏草稿覆盖、后端校验定级）。详见下方「评审记录」与文末「评审结论」。

---

## 评审记录（Review Notes · v2）

> 评审方法：逐节以「方案声明 vs. 真实源码」对照，覆盖**可行性 / 完整性 / 一致性 / 右尺寸**四维。每条标注严重度（P0 阻断 / P1 需修 / P2 建议）。P1 已在本 v2 正文就地修复；低成本 P2 就地校正，其余 P2 列入「评审结论」条件。
>
> **正向确认（已逐一查证为真，feasibility 通过）**：①后端 CRUD（`GET/POST/PUT/DELETE /strategies`、`GET /strategies/{id}`）齐备（`api/strategies.py:28-68`），响应经 `merge_strategy` 输出 `kind/params/paramsSchema/enabled/builtin/unavailable`（`repositories.py:31-65`）；②`POST /strategies/{id}/backtest` 对组合行已分流——`run_single_backtest` 检 `_is_combo(row.template_key)` → `_run_saved_combo`，响应在 `{runId, metrics}` 上**追加 `combo`**（`services.py:120-125, 382-397`）；③`POST /strategies/combo/preview` 已存（成员 `min_length=2,max_length=8` Pydantic 兜底）；④`_build_members` 显式跳过 `kind=="combo"` 成员与失效模板、可用 `<2` → 400 `COMBO_MEMBERS`（`services.py:274-298`）；⑤seed 为每模板种 `builtin=true` 行、`DELETE` 内置返回 **409 `BUILTIN_READONLY`**（`api/strategies.py:60-68`、`db/session.py:59-77`）；⑥`STRATEGY_REGISTRY` 为模板 meta 唯一真源；⑦轮动参数边界（rebalanceBars 4..240 / lookbackBars 24..1000 / minDwellBars 1..240 / switchMarginPct 0..1 / turnoverBps 0..50 / selectionMetric∈{return,sharpe,calmar}）前后端一致；⑧前端 `api.ts` 七个方法齐全、`lib/types.ts` 的 `Strategy`（含 `kind/enabled/builtin/unavailable`）与 `ComboMemberSpec/ComboPolicy/ComboPreviewResult` 齐全、`store` 现版 `version:11`、`comboDraft` 已 `partialize`、`comboEditingId` 确不存在（瞬态新增成立）、`Modal`（焦点陷阱/Esc/遮罩/滚动锁/焦点恢复）+ `Tabs` `underline` 变体 + `OkxAccountDetailModal` + `MetricsCards/ComboTimeline/regimeAffinityLabels` + 设计 token 均存在；新组件 `SavedCombosList/ComboDetailModal` 与独立 `MemberPicker` 确不存在（标注「新增」属实）。

| # | 严重度 | 维度 | 位置 | 问题 | 处置 |
|---|---|---|---|---|---|
| R1 | **P1** | 可行性 × 完整性 | §2.6 即时绩效区 / §四 接口 / §三 文件计划 | 详情弹窗承诺「运行回测 → 渲染 MetricsCards + **年化** + **ComboTimeline**」，而年化（`combo.annualizedReturn`）与时间线（`combo.timeline`）只存在于响应的 `combo` 字段。但前端 `api.backtest` 的返回类型被硬编码为 `{ runId, metrics }`（`lib/api.ts:120`），**丢弃了 `combo`**——后端确实在 HTTP 响应里返回 `combo`（裸 dict 不被剥离），但前端类型层吃不到，`res.combo` 在 TS 下报错。按原文「无需新增 api 方法」无法落地详情弹窗的绩效区。 | **已修**：§四 新增 `BacktestResult` 类型并把 `api.backtest` 返回类型放宽为 `{ runId; metrics; combo? }`；§三 文件计划新增 `lib/api.ts`、`lib/types.ts` 两行修改；§2.6 注明绩效区消费 `res.combo`（缺省即原子回测、`combo` 为 `undefined`，仅渲染指标卡）。 |
| R2 | **P1** | 一致性 × 可行性 | §2.6 轮动参数区 / §三 文件计划 | 详情弹窗「轮动参数区：**复用 `PolicyEditor` 的 `POLICY_HINTS` 文案**」。但经核验，`PolicyEditor` 与 `POLICY_HINTS` 当前是 `ComboView.tsx` 内的**内联私有局部**（未 `export`），且文件变更计划未列出任何抽取/导出动作。`ComboDetailModal` 既无法 import，也不应从兄弟组件文件反向 import 常量（制造孤岛，违 CLAUDE.md §8.8），照原文实现只会逼出复制粘贴（DRY 违规、文案漂移）。 | **已修**：§三 文件计划新增 `apps/web/lib/comboPolicyHints.ts`（抽出 `POLICY_HINTS` 与轮动字段标签为共享常量），`ComboView`/`PolicyEditor` 与 `ComboDetailModal` 同源消费；§2.6 措辞改为「复用共享 `comboPolicyHints` 文案 + 各 `Strategy` 自带的 `paramsSchema.label`」。 |
| R3 | P2 | 完整性（数据源准确性） | §2.6 成员策略区 | 「成员名（**回查注册表 meta** 得 name/description）」措辞不准——前端**没有** `STRATEGY_REGISTRY`，模板 meta 只能经 `GET /strategies` 列表（`merge_strategy` 已据注册表填充）回查，或取成员快照自带的 `name`。 | **已修文**：改述为「按 `member.templateKey` 在 `strategies` 列表中回查同模板行取 `name/description`，回退成员快照 `name`；均失配则标注失效成员」。 |
| R4 | P2 | 完整性 | §2.7 增/改 / §四 纯函数清单 | (a) 新建/编辑均未提**名称校验**（非空/去空白/可选重名提示），空名保存会落出无标题组合；(b) 编辑态载入只述 members+policy 回填，**漏了把组合现有 `name` 回填**到名称输入框；(c) 纯助手清单只给了反向 `resolveMemberIds`，缺**正向** `selectedMembers(memberIds, atomicCandidates) → ComboMemberSpec[]`（保存时把草稿 id 快照为成员），而保存/预览都依赖它。 | **已修文**：§2.7 增「名称非空校验 + 编辑态回填 name」；§四 清单补 `selectedMembers` 签名。 |
| R5 | P2 | 完整性 × 交互 | §2.3 / §2.5 / §2.6 | (a) `SavedCombosList` 未定义**空态/载入态**（无组合时的引导文案、`useQuery` pending 时的占位）；(b) 「编辑」「导入成员」会就地改写 `comboDraft`，若用户正处于另一组合的未保存编辑中，会**静默覆盖脏草稿**丢失改动。 | 列入「评审结论」条件：空/载入态给最简实现；脏草稿被覆盖前给一次轻量确认（或在编辑态对「编辑他项」禁用并提示先保存/放弃）。 |
| R6 | P2 | 右尺寸 | §三 后端项 / §五 校验区间 | 后端 `validate_combo_params`（成员 2..8 边界）被标 P1，但 UI 已以 `MAX_MEMBERS=8` 夹取 + `validateMemberCount` 禁用保存，运行期 `_build_members` 又兜底 `<2` → 400；唯一未守是「绕过 UI 直接 `POST /strategies` 存 >8 成员」。属**纵深防御**而非功能必需。 | **已修文**：把后端三项从 P1 降为 **P2（可选硬化）**，明确「仅前端即闭环；后端项为防绕过 UI 的额外护栏」。不阻断 DoD。 |
| R7 | P2 | 完整性（错误路径） | §2.6 即时绩效区 | 弹窗内回测取 store 的 `instId/bar/backtestStart/End/simBalance`；若用户尚未选回测区间，`startTs/endTs` 缺失 → 后端 `EMPTY_RANGE` 400。原文已要求「失败显式错误态」，但更优是**前置禁用**回测按钮直至区间就绪。 | 列入「评审结论」条件：区间未就绪时禁用「运行回测」并给 tooltip 说明，保留失败错误态兜底。 |

## 一、概述（Overview）

本项目「交易策略」面板已具备**体制自适应轮动组合**（`regime_rotation` 模板，`kind="combo"`）：用户在「组合」模式（`ComboView`）勾选成员原子策略、配置轮动参数、运行即时回测（`POST /strategies/combo/preview`），并可在回测结果区点「另存为组合」把它存成一行 `Strategy`（`templateKey="regime_rotation"`、`params={members:[...], ...轮动标量}`、`builtin=false`、`kind="combo"`）。然而当前体验存在三处明显缺口：(1) **保存入口被埋在回测之后**，没有「我的组合策略」列表，存完即「丢失」——无法再次打开、无法编辑、无法在 UI 内删除；(2) **成员选择只有单一原子列表**，不能按「普通策略 / 组合策略」分页签选择，用户无法复用既有组合作为搭建起点；(3) **没有详情弹窗**，无法直观查看一个已保存组合的成员构成与轮动配置全貌。

本方案在**不改动数据库表结构**、**几乎不动后端**的前提下，把已保存的用户组合提升为一等公民：在「组合」视图顶部新增「我的组合策略」管理区（列表 + 增删改查），把成员选择器改造为「普通策略 / 组合策略」**多页签**形式（选「组合策略」页签即把该组合的原子成员**展开导入**到当前草稿，组合不嵌套组合），并新增一个**美观优雅、符合人机交互最佳实践的组合详情弹窗**（复用既有 `Modal` 原语，懒触发回测可查看即时绩效）。

之所以「几乎不动后端」，是因为现有后端已经把组合当作普通 `Strategy` 行对待：策略 CRUD（`GET/POST/PUT/DELETE /strategies`）天然适用；`merge_strategy` 已输出 `kind` 与合并后的 `params`（含 `members` 与轮动标量），详情弹窗与列表可直接消费；尤为关键的是 **`POST /strategies/{id}/backtest` 已对组合行做分流**（`run_single_backtest` 检测 `_is_combo(template_key)` → `_run_saved_combo`，返回在 `{runId, metrics}` 之上**追加** `combo` 字段）。因此本方案主体是前端工作，后端仅建议补一处**保存时的成员数边界校验**（防止绕过预览存出非法组合），属防御性硬化。

---

## 二、技术设计（Technical Design）

### 2.1 架构与既有事实（复用，不重造）

- **存储模型不变**：组合即 `strategies` 表中一行，`template_key="regime_rotation"`，`params_json` 内含 `members:[{templateKey,params,name?}]` + 7 个轮动标量（`rebalanceBars/lookbackBars/selectionMetric/minDwellBars/switchMarginPct/regimeGating/turnoverBps`）。注册表 `STRATEGY_REGISTRY` 是 meta 唯一真源（C6）。
- **内置模板行**：seed 阶段为每个注册模板建一行 `builtin=true`，故存在一行 **builtin 的 `regime_rotation`**（`ComboView` 当前用它取 `paramsSchema`）。它**不是**用户组合，必须从「我的组合策略」列表中排除（仅作 schema 来源 + 不可编辑/删除，删它后端返回 409 `BUILTIN_READONLY`）。
- **运行链路全通**：保存组合的回测 / 对比已由 `run_single_backtest` / `run_compare` 经 `_combo_spec_from_row` → `_build_members` → `_run_combo` 正确分流；前端「回测」动作只需调用既有 `api.backtest(id, …)`。

### 2.2 组合判定与「用户组合」过滤（前端单一口径）

前端已从 `Strategy.kind` 拿到 `"atomic"|"combo"`。定义两个纯判定（落 `lib/combo.ts`，便于单测）：

- `isCombo(s)` → `s.kind === "combo"`。
- `isUserCombo(s)` → `isCombo(s) && !s.builtin && !s.unavailable`。

「我的组合策略」列表 = `strategies.filter(isUserCombo)`；成员选择器「组合策略」页签同样用 `isUserCombo`，且排除**当前正在编辑的组合自身**（避免自我导入）。

### 2.3 关键改动：`ComboView` 重构为三区

```
┌ ComboView（mode==="combo"）────────────────────────────┐
│ ① 我的组合策略 SavedCombosList                          │
│    - 行：名称 + 「组合」徽标 + 成员数/选优指标摘要        │
│    - 行内动作：查看(详情弹窗) · 编辑 · 删除 · 回测          │
│    - 顶部「+ 新建组合」按钮（清空草稿 + 退出编辑态）        │
│ ② 组合编辑器（create / edit 两态）                       │
│    - MemberPicker（页签：普通策略 / 组合策略）            │
│    - PolicyEditor（轮动参数，沿用）                       │
│    - 主按钮：create 态=「保存组合」；edit 态=「保存修改」  │
│    - 次按钮：「运行组合回测」（不落库，预览）              │
│ ③ ComboResult（预览结果，沿用：年化 + 指标 + 时间线 + 分体制）│
└────────────────────────────────────────────────────────┘
        ▲ 点「查看」                  ▲ 任意行
   ComboDetailModal（详情弹窗，复用 Modal 原语）
```

### 2.4 草稿模型与编辑态（store）

沿用 `comboDraft = { memberIds:number[]; policy:ComboPolicy }`（已持久化）。新增**瞬态**编辑指针 `comboEditingId:number|null`（**不**进 `partialize`，刷新即复位，避免指向已删行）。

- **新建态**（`comboEditingId===null`）：主按钮「保存组合」→ `POST /strategies`。
- **编辑态**（`comboEditingId===id`）：进入时把该组合的 `members` 经 `resolveMemberIds` 映射回 `memberIds`、`extractComboPolicy` 取出轮动参数，写入 `comboDraft`；主按钮「保存修改」→ `PUT /strategies/{id}`。

> **成员快照 ↔ 草稿 id 的映射（关键设计点）**：保存组合存的是成员**快照** `{templateKey, params, name}`，而编辑器以**当前原子策略行 id** 选择成员（其参数取自该行）。`resolveMemberIds(savedMembers, atomicCandidates)` 的确定性规则：对每个快照，优先匹配 `templateKey + name` 同时相等的原子行，其次回退到首个 `templateKey` 相等的可用行，找不到则跳过并计入 `notes`（与后端 `_build_members` 跳过失效成员的语义一致）。这与现状「成员参数取自原子行、组合搭建不做逐成员调参」完全一致，不引入逐成员参数编辑，避免有损往返争议。

### 2.5 多页签成员选择器（MemberPicker）

抽出独立组件 `MemberPicker.tsx`，内部用既有 `Tabs`（`underline` 变体）：

- **「普通策略」页签**：列 `atomicCandidates`（`kind!=="combo" && !unavailable && enabled`），勾选切换 `memberIds`（沿用现行为 + 体制亲和徽标 `regimeAffinityLabels`）。
- **「组合策略」页签**：列 `userCombos`（排除编辑中的自身）。每行：组合名 + 成员数摘要 + 「查看」（开详情弹窗）+ 「导入成员」按钮。点「导入成员」→ `importComboMembers`：把该组合成员经 `resolveMemberIds` 解析为 id，与当前 `memberIds` 取并集、去重、夹取到 `MAX_MEMBERS`，并就地提示「已展开导入 N 个原子成员（组合不可嵌套）」。

> **为何「导入展开」而非「嵌套」**：轮动引擎成员是原子 `{templateKey, params}`（`_build_members` 显式跳过 `kind=="combo"` 的成员）。让「组合策略」页签的选择落地为**把其原子成员展开进当前草稿**，既满足「选择普通或组合策略作为成员来源」的需求，又零改引擎、杜绝递归。

### 2.6 组合详情弹窗（ComboDetailModal）— 顶级设计

复用 `ui/Modal`（已含 `role=dialog`、`aria-modal`、焦点陷阱、Esc/遮罩关闭、滚动锁定、焦点恢复），输入一个 `Strategy`（组合行，`params` 已含成员与轮动参数，**无需额外拉取**即可渲染静态信息）：

- **页眉**：组合名（`aria-labelledby` 指向其 id）+「组合」徽标 +（内置则）「内置」徽标 + 关闭按钮。
- **概览区**：成员数、选优指标（`return/sharpe/calmar` 中文化）、是否体制门控、再平衡/回看/最短持有/迟滞/换手成本摘要（紧凑 KV 网格）。
- **成员策略区**：逐成员卡片——成员名（**R3**：按 `member.templateKey` 在 `strategies` 列表中回查同模板行取 `name/description`，回退成员快照自带 `name`；前端无 `STRATEGY_REGISTRY`，故不可直接「回查注册表」）+ `templateKey` + 体制亲和徽标（`regimeAffinityLabels`）+ 关键参数摘要；失配/失效成员（模板已删）显式标注。
- **轮动参数区**：**R2**——复用**共享** `lib/comboPolicyHints.ts` 的 `POLICY_HINTS` 文案（自 `ComboView` 抽出，避免从兄弟组件反向 import），结合各 `Strategy` 自带的 `paramsSchema.label`，只读网格展示每项标签 + 值 + 悬浮释义。
- **即时绩效区（懒触发）**：弹窗内「运行回测」按钮 → `api.backtest(combo.id, {instId,bar,limit,startTs,endTs,initialCapital})` → 渲染 `MetricsCards`（来自 `res.metrics`）+ **年化/`ComboTimeline`（来自 `res.combo.annualizedReturn` / `res.combo.timeline`）**。**R1**：`api.backtest` 返回类型须放宽到含可选 `combo`（见 §四 `BacktestResult`），否则 `res.combo` 在 TS 报错；组合行回测后端必带 `combo`（`_run_saved_combo`），原子行则 `combo===undefined` 仅渲染指标卡。**R7**：当 store 的回测区间（`startTs/endTs`）未就绪时**禁用**「运行回测」按钮并 tooltip 提示，避免 `EMPTY_RANGE` 400；触发后失败仍显式错误态（不假数据，C1）。
- **页脚动作**：编辑（载入编辑器并关闭弹窗）· 删除（二次确认）· 关闭。

视觉遵循既有设计 token（`--bg/--surface/--surface-2/--border/--accent/--accent-soft/--text/--muted/--long/--short`），明暗主题零适配；层级清晰（区块标题用 `uppercase tracking-wide text-muted`，与 `OkxAccountDetailModal` 同语汇）；动效用既有 `transition-* duration-150 ease-smooth`，克制不喧宾夺主。

### 2.7 增删改查时序（Sequence）

- **查（列表）**：`useQuery(["strategies"])` → `filter(isUserCombo)` 渲染列表（与编辑器共用同一份缓存，零额外请求）。**R5a**：`isLoading` 时渲染占位骨架；`isUserCombo` 结果为空时渲染空态引导（「还没有保存的组合，点『+ 新建组合』开始」）。
- **查（详情）**：点「查看」→ 打开 `ComboDetailModal(strategy)`（静态信息直接渲染；绩效按需回测）。
- **增**：编辑器新建态「保存组合」→ 校验 `validateMemberCount`（2..8）**与名称非空（去空白后 length>0；可附同名提示，不强制拒绝）** → `api.createStrategy({templateKey:"regime_rotation", name: name.trim(), params:{members:selectedMembers(memberIds, atomicCandidates), ...clampComboPolicy(policy)}})` → `invalidateQueries(["strategies"])` → 列表即现新行 → 清空草稿 / 退出编辑态。名称为空时禁用「保存组合」。
- **改**：行内「编辑」→ 载入草稿（`resolveMemberIds` 回填 `memberIds`、`extractComboPolicy` 回填 policy、**并把该组合现有 `name` 回填名称输入框**）+ 置 `comboEditingId` → 改成员 / 参数 → 「保存修改」→ `api.updateStrategy(id, {name: name.trim(), params})` → `invalidate` → 退出编辑态。**R5b**：若进入编辑/导入会覆盖一份未保存的脏草稿，先给一次轻量确认（或在编辑态下对「编辑他项」禁用并提示先保存/放弃）。
- **删**：行内「删除」→ 二次确认 → `api.deleteStrategy(id)` → `invalidate`；若删的正是编辑中的组合则清编辑态。后端内置行返回 409，前端把内置组合的删除入口禁用并提示。

> **写后缓存一致性**：经核验，现仓库仅在 `createStrategy` 成功后 `invalidateQueries(["strategies"])`（`ComboView.tsx:107`、`StrategyPanel.tsx:226`）；本特性新增的 **`updateStrategy` / `deleteStrategy` mutation 必须各自补 `onSuccess: invalidateQueries(["strategies"])`**，否则编辑/删除后列表不刷新。

---

## 三、文件 / 模块变更计划（File / Module Change Plan）

| 文件 | 新增/修改 | 一句话意图 |
|---|---|---|
| `apps/web/components/Strategy/ComboView.tsx` | 修改 | 重构为三区（我的组合列表 + create/edit 编辑器 + 结果区）；接入 `comboEditingId`、保存/更新/删除 mutation 与详情弹窗开关。 |
| `apps/web/components/Strategy/SavedCombosList.tsx` | 新增 | 「我的组合策略」列表组件：行渲染 + 行内动作（查看/编辑/删除/回测）+「新建组合」。 |
| `apps/web/components/Strategy/MemberPicker.tsx` | 新增 | 从 ComboView 抽出并改造为「普通策略 / 组合策略」多页签成员选择器（组合页签=导入展开 + 查看）。 |
| `apps/web/components/Strategy/ComboDetailModal.tsx` | 新增 | 组合详情弹窗（概览 + 成员 + 轮动参数 + 懒触发绩效），复用 `Modal`。 |
| `apps/web/components/Strategy/DeleteComboConfirm.tsx` | 新增（可内联） | 删除二次确认小弹窗 / 行内确认（复用 `Modal` 或行内 confirm 区）。 |
| `apps/web/lib/combo.ts` | 修改 | 新增纯助手：`isCombo` / `isUserCombo` / `selectedMembers`（**R4c** 正向构造器）/ `resolveMemberIds` / `extractComboPolicy` / `importComboMembers` / `summarizeComboMembers` / `comboPolicySummary`。 |
| `apps/web/lib/combo.test.ts` | 修改 | 为上述纯助手补正常 + 异常路径单测（失效成员跳过、容量夹取、自我排除、参数夹取、`selectedMembers` 往返）。 |
| `apps/web/lib/comboPolicyHints.ts` | 新增（**R2**） | 从 `ComboView` 抽出 `POLICY_HINTS` 与轮动字段标签为共享常量，供 `PolicyEditor`（仍内联于 `ComboView`）与新 `ComboDetailModal` 同源消费，杜绝复制粘贴。 |
| `apps/web/lib/types.ts` | 修改（**R1**） | 新增 `BacktestResult`（`{runId, metrics, combo?}`），供 `api.backtest` 与详情弹窗绩效区使用。 |
| `apps/web/lib/api.ts` | 修改（**R1**） | 把 `backtest` 返回类型由 `{runId, metrics}` 放宽为 `BacktestResult`（端点/请求体不变）。**无新增端点。** |
| `apps/web/lib/store.ts` | 修改 | 新增瞬态 `comboEditingId:number\|null` + `setComboEditingId`（**不**入 `partialize`，无需 bump `version:11`）；`clearPaperRuntime` 风格不变。 |
| `apps/web/components/Strategy/ComboView.test.tsx` | 修改 | 扩展：保存/编辑/删除流、页签切换与导入、详情弹窗开启与绩效懒触发。 |
| `apps/web/components/Strategy/ComboDetailModal.test.tsx` | 新增 | 详情弹窗渲染、关闭契约、绩效按需回测的快照/交互测试。 |
| `services/quant/app/api/strategies.py` | 修改（**P2**，可选硬化） | 在 `create_strategy`/`update_strategy` 中，对 `kind=="combo"` 的模板做成员数边界校验（2..8），非法返回 400 `COMBO_MEMBERS`。 |
| `services/quant/app/agent/services.py` | 修改（**P2**，可选） | 抽 `validate_combo_params(template_key, params)` 复用 `_is_combo` 与成员计数，供路由调用（避免在路由内重复逻辑）。 |
| `services/quant/tests/test_combo_api.py` | 修改（**P2**） | 补：保存/更新合法组合成功、成员 <2 或 >8 返回 400、删除用户组合成功 / 删内置 409、列表含用户组合。 |
| `docs/plans/strategy-combo-management/spec.md` | 修改 | 本设计文档（v2 评审）。 |

> 说明（**R6**）：后端 CRUD 与组合回测分流**已存在**，且 UI 已以 `MAX_MEMBERS=8` 夹取 + `validateMemberCount` 禁用保存、运行期 `_build_members` 兜底 `<2`→400，故后端三项为 **P2（可选硬化）**——唯一未守口径是「绕过 UI 直接 `POST /strategies` 存 >8 成员」的纵深防御，不是功能必需。若评审/开发节点选择「前端先行」，仅落前端项功能即闭环；后端项可随后补。

---

## 四、接口设计（Interface Design）

**全部复用既有 REST，无新增端点。** 经 Next rewrite `/api/py/*` 代理到 FastAPI。

| Method | Path | 用途 | 入参（关键） | 出参（关键） |
|---|---|---|---|---|
| GET | `/strategies` | 列全部策略（含组合） | — | `Strategy[]`（含 `kind/params/builtin`） |
| GET | `/strategies/{id}` | 取单个组合（详情备用） | — | `Strategy`（`params` 含 `members`+轮动标量） |
| POST | `/strategies` | **保存新组合** | `{templateKey:"regime_rotation", name, params:{members, ...policy}}` | `Strategy`（含新 `id`） |
| PUT | `/strategies/{id}` | **编辑组合** | `{name?, params?:{members, ...policy}}` | `Strategy` |
| DELETE | `/strategies/{id}` | **删除组合** | — | `204`；内置 `409 BUILTIN_READONLY` |
| POST | `/strategies/{id}/backtest` | **回测已存组合**（已分流） | `{instId,bar,limit,startTs,endTs,initialCapital?}` | `{runId, metrics, combo}` |
| POST | `/strategies/combo/preview` | 预览未保存组合（沿用） | `{members,policy?,instId,bar,...}` | `{runId, metrics, combo}` |

前端 `lib/api.ts` 已含 `strategies/strategy/createStrategy/updateStrategy/deleteStrategy/backtest/comboPreview`，**无需新增 api 端点/方法**。

> **R1（返回类型放宽，非新增方法）**：现 `api.backtest` 返回类型被硬编码为 `{ runId; metrics }`（`lib/api.ts:120`），丢弃了组合回测响应里的 `combo`。详情弹窗绩效区需消费它，故在 `lib/types.ts` 定义 `BacktestResult` 并把 `api.backtest` 返回类型改为该型（端点 URL/请求体不变，向后兼容原子行）：
>
> ```ts
> // lib/types.ts —— 原子行回测 combo 为 undefined；组合行后端必带 combo（_run_saved_combo）
> export interface BacktestResult {
>   runId: number;
>   metrics: Metrics;
>   combo?: ComboPreviewResult["combo"];   // { annualizedReturn; timeline; breakdown; notes? }
> }
> // lib/api.ts —— request<{ runId; metrics }> 改为 request<BacktestResult>
> ```

**前端纯函数签名（新增于 `lib/combo.ts`）**

```ts
export function isCombo(s: Strategy): boolean;
export function isUserCombo(s: Strategy): boolean;
// 正向：把草稿成员 id 快照为成员规格（保存/预览均依赖；R4c）
export function selectedMembers(
  memberIds: number[],
  atomicCandidates: Strategy[],
): ComboMemberSpec[];
// 把保存组合的成员快照映射回当前原子策略行 id（templateKey+name 优先，回退 templateKey；失效计入 dropped）
export function resolveMemberIds(
  savedMembers: ComboMemberSpec[],
  atomicCandidates: Strategy[],
): { memberIds: number[]; dropped: string[] };
// 从组合 Strategy.params 取出轮动参数（叠加默认，夹取合法区间）
export function extractComboPolicy(params: Record<string, unknown>): ComboPolicy;
// 导入组合成员到当前草稿（并集 + 去重 + 夹取 MAX_MEMBERS），返回 { memberIds, importedCount, dropped }
export function importComboMembers(
  combo: Strategy, atomicCandidates: Strategy[], currentMemberIds: number[],
): { memberIds: number[]; importedCount: number; dropped: string[] };
// 成员摘要文案（如「4 个成员 · 夏普选优 · 体制门控」）
export function summarizeComboMembers(combo: Strategy): string;
export function comboPolicySummary(policy: ComboPolicy): string;
```

---

## 五、数据模型（Data Model）

**无表结构变更。** 组合复用 `Strategy` 行：

```text
strategies(
  id, template_key="regime_rotation", name,
  params_json = JSON({
    members: [{ templateKey, params, name? }, ...],   // 2..8 个原子成员快照
    rebalanceBars, lookbackBars, selectionMetric,      // 轮动标量（缺省回落模板默认）
    minDwellBars, switchMarginPct, regimeGating, turnoverBps
  }),
  enabled=true, builtin=false, created_at, updated_at
)
```

**前端内存形态（沿用 `lib/types.ts`）**：`Strategy`（含 `kind`）、`ComboMemberSpec`、`ComboPolicy`、`ComboDraft`（`{memberIds, policy}`）、`ComboPreviewResult`。新增 store 字段 `comboEditingId:number|null`（瞬态）。

**校验区间（前后端一致，单一真源在后端模板 `params_schema` / `ComboPolicyIn`）**：成员数 `2..8`；`rebalanceBars 4..240`、`lookbackBars 24..1000`、`minDwellBars 1..240`、`switchMarginPct 0..1`、`turnoverBps 0..50`、`selectionMetric ∈ {return,sharpe,calmar}`。前端以 `clampComboPolicy`/`validateMemberCount` 夹取与校验，后端 `ComboPolicyIn` + 建议新增的 `validate_combo_params` 在边界兜底。

---

## 六、测试与验收标准（Testing & Acceptance Criteria）

**前端单测（vitest，纯助手优先；组件测试需 `NODE_ENV=test`）**

- `combo.test.ts`：`isUserCombo` 正确排除内置/失效；`selectedMembers`/`resolveMemberIds` 往返一致（命中 templateKey+name、回退 templateKey、失效成员计入 `dropped`）；`importComboMembers` 并集去重 + 容量夹取 `MAX_MEMBERS`；`extractComboPolicy` 叠加默认 + 越界夹取；`summarizeComboMembers`/`comboPolicySummary` 文案稳定。
- `ComboView.test.tsx`：保存新组合（POST 调用入参正确、列表刷新）、编辑载入（草稿回填 + PUT 入参）、删除（DELETE + 编辑态清理）、页签切换、「导入成员」并集与提示、保存按钮在成员 <2 时禁用。
- `ComboDetailModal.test.tsx`：渲染概览/成员/轮动参数；Esc 与遮罩关闭；「运行回测」懒触发后从 `res.combo` 渲染年化/时间线 + 指标卡；区间未就绪时按钮禁用；失败显式错误态。

**后端测试（pytest，离线；P2 可选硬化项随后端改动落地，前端先行时本段可缓）**

- `test_combo_api.py`：`POST /strategies`（regime_rotation，合法成员）成功；成员 `<2`/`>8` 返回 `400 COMBO_MEMBERS`；`PUT` 改成员/参数成功且回读一致；`DELETE` 用户组合成功、删内置 `409 BUILTIN_READONLY`；`GET /strategies` 含用户组合且 `kind=="combo"`；`POST /strategies/{id}/backtest` 对已存组合返回含 `combo` 字段（回归既有分流不破）。

**验收清单（Definition of Done 对齐）**

1. 「组合」视图顶部出现「我的组合策略」列表，能新建、查看、编辑、删除（增删改查闭环）。
2. 成员选择器为「普通策略 / 组合策略」双页签；选「组合策略」可把其成员展开导入当前草稿（含上限与提示）。
3. 「查看」打开详情弹窗，可见成员构成 + 轮动参数 +（按需）即时绩效；弹窗符合 a11y 契约（焦点陷阱 / Esc / 遮罩 / 焦点恢复）且明暗主题正常。
4. 保存后刷新页面，组合仍在（持久化）；编辑保存即时反映；删除后从列表消失。
5. 不改 DB 表结构；不破坏既有回测/对比/实时链路；`pnpm test`（前端 vitest + 后端 pytest 离线）通过；前端 `tsc` 无误。

---

## 七、风险与缓解（Risks & Mitigations）

| 风险 | 等级 | 缓解 |
|---|---|---|
| **成员快照 ↔ 草稿 id 映射有损**：编辑保存会按当前原子行参数重新快照，可能与原快照不同 | 中 | 维持现状语义（组合搭建本就不逐成员调参）；`resolveMemberIds` 规则文档化（templateKey+name 优先）；失效成员就地提示「N 个成员不可用已跳过」。 |
| **内置 `regime_rotation` 行误入列表 / 被误删** | 中 | `isUserCombo` 显式排除 `builtin`；列表对内置不渲染删除入口；后端 `DELETE` 仍以 409 兜底。 |
| **组合自我导入 / 嵌套递归** | 中 | 「组合策略」页签排除编辑中的自身；导入一律展开为原子成员，引擎层 `_build_members` 再次跳过 `kind=="combo"`。 |
| **绕过预览保存出非法组合（成员 <2）** | 中 | 前端 `validateMemberCount` 禁用保存；后端建议补 `validate_combo_params`（P1）在边界返回 400。 |
| **`ComboView.tsx` 体量膨胀超 800/50 行阈值** | 低 | 抽出 `SavedCombosList`/`MemberPicker`/`ComboDetailModal`/`DeleteComboConfirm` 子组件与 `combo.ts` 纯助手，单文件守阈值。 |
| **TanStack Query 缓存不一致**（增删改后列表未刷新） | 低 | 所有写操作 `onSuccess` 统一 `invalidateQueries(["strategies"])`；编辑器与列表共享同一 query key。 |
| **回测请求依赖左图标的/周期/区间**（组合不接实时模拟） | 低 | 详情弹窗与列表「回测」均取 store 的 `instId/bar/backtestStart/End/simBalance`；文案明确「组合仅支持回测/对比」。 |
| **store 版本迁移**：新增 `comboEditingId` | 低 | 该字段为瞬态、不入 `partialize`，无需 bump 持久化 version；仅初始化器给默认 `null`。 |

---

## 八、实施顺序建议（供开发节点参考）

1. `lib/combo.ts` 纯助手 + 单测（先红后绿，TDD）。
2. `store.ts` 加瞬态 `comboEditingId`。
3. 抽 `MemberPicker.tsx`（双页签）→ 接回 `ComboView`。
4. `SavedCombosList.tsx` + `ComboView` 三区重构（增删改 mutation）。
5. `ComboDetailModal.tsx`（静态信息 → 懒触发绩效）。
6. 组件测试补齐。
7. （P1）后端 `validate_combo_params` + 路由校验 + `test_combo_api.py`。

> 全程遵循项目 Clean Code 阈值（文件 ≤800 行 / 函数 ≤50 行 / 形参 ≤5 / 嵌套 ≤4），错误显式传播、不假数据（C1），密钥与持久化口径不变。

---

## 评审结论（Review Verdict）

**结论：有条件通过（Approved with Conditions）。**

本方案经三路并行核验（后端组合路由/CRUD、前端组件、前端 lib+store）逐节与真实源码对照，**架构判断准确、可行、与既有约定一致、规模适中**：组合即 `Strategy` 行、CRUD 与 `POST /strategies/{id}/backtest` 的组合分流、`POST /strategies/combo/preview`、`_build_members` 跳过嵌套与失效成员、内置行 `DELETE`→409 `BUILTIN_READONLY`、`Modal/Tabs(underline)/MetricsCards/ComboTimeline/regimeAffinityLabels` 与设计 token、`store.version:11` + `comboDraft` 持久化等关键事实**均查证为真**；「主体前端、零表结构变更、几乎不动后端」的定位成立。2×P1（R1 `api.backtest` 丢弃 `combo` 字段、R2 `POLICY_HINTS`/`PolicyEditor` 内联私有无法复用）已在本 v2 正文就地修复；5×P2 中 R3/R4/R6 已就地校正，R5/R7 列为下列放行条件。**无遗留 P0/P1。**

放行附以下条件，开发节点（Subtask #2）须遵守：

1. **R1 必须落地**：`lib/types.ts` 增 `BacktestResult`、`lib/api.ts` 的 `backtest` 返回类型放宽为该型，详情弹窗绩效区从 `res.combo` 取年化/时间线；严禁因类型缺失而让弹窗「只剩指标卡、悄悄丢掉年化/时间线」。
2. **R2 必须落地**：`POLICY_HINTS` 与轮动字段标签抽到共享 `lib/comboPolicyHints.ts`，`ComboView`/`PolicyEditor` 与 `ComboDetailModal` 同源消费；不得从兄弟组件文件反向 import，不得复制粘贴常量。
3. **R5（实现期落实）**：`SavedCombosList` 给空态引导与 `isLoading` 占位；「编辑」/「导入成员」覆盖未保存脏草稿前给一次轻量确认或在编辑态禁用「编辑他项」。
4. **R7（实现期落实）**：详情弹窗「运行回测」在 store 回测区间未就绪时禁用并 tooltip 提示，避免 `EMPTY_RANGE` 400；失败仍保留显式错误态（不假数据，C1）。
5. **写后缓存一致性**：本特性新增的 `updateStrategy` / `deleteStrategy` mutation 各自 `onSuccess` 必须 `invalidateQueries(["strategies"])`（现仓库仅在 create 后失效），否则编辑/删除后列表不刷新。
6. **阈值自查兜底**：`ComboView.tsx` 现 406 行，三区重构后须按 §三 抽出 `SavedCombosList`/`MemberPicker`/`ComboDetailModal`（必要时再抽 `ComboResult`/`PolicyEditor`），单文件不得超 800 行、单函数不得超 50 行。
7. **后端 P2 为可选**：R6 已将后端成员数边界校验降为 P2 纵深防御；若本期不落，须确保前端 `MAX_MEMBERS=8` 夹取 + `validateMemberCount` 与运行期 `_build_members`（`<2`→400）仍是有效护栏，不得移除。

满足以上条件即视为方案验收通过，可进入编码阶段。

---

（评审完 · v2）
