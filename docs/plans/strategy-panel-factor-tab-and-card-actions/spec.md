# 交易策略面板：新增「因子策略」页签 + 策略卡片内编辑/删除按钮

- **Feature**: `strategy-panel-factor-tab-and-card-actions`
- **版本**: v2（方案评审与修复 / Subtask #1 产出；v1 由方案设计节点产出）
- **作者**: 方案设计节点（solution architect）· 评审修复：方案评审节点（senior reviewer）
- **状态**: 已评审 · 有条件通过（Subtask #1）
- **范围**: 纯前端（`apps/web`），无后端、无数据库变更

---

## 评审记录（Review Notes · v2）

> 评审人：资深评审节点（Anthropic engineering）。逐节按 **可行性 / 完整性 / 一致性 / 适配度** 四维核查，并对照真实代码（`strategyCatalog.ts`、`StrategyPanel.tsx`(896 行)、`StrategyList.tsx`、`useComboPanel.tsx`、`FactorSetList.tsx`、`factor.ts`、`store.ts`、`types.ts`、`combo.ts`、`ComboEditorModal.tsx`）逐条验证方案断言。下表 P0/P1 已在本 v2 文档正文**就地修复**；R-P2-1 因修复成本极低且改善一致性，亦一并就地修复；其余 P2 为非阻塞建议，列为实施期注意项（见文末「评审结论」）。

| 编号 | 维度 | 严重度 | 问题 | 处置 |
|---|---|---|---|---|
| R-P1-1 | 可行性 / 一致性 | **P1** | DoD #6「`StrategyPanel.tsx` 行数不增反降（净抽离）」不可达：本次新增（第三页签接线 + 每卡片编辑/删除分发 + 弹窗渲染）均为**净新增**，而 StrategyPanel 内**并无可对冲的既有删除逻辑**（组合删除早已外置于 `useComboPanel`），仅靠新增 `useStrategyDelete` 无法让本文件净降；且该文件现 896 行，已超 CLAUDE 800 行硬上限 96 行，无对冲新增将进一步固化违规。 | 文件计划新增 `StrategyListSection.tsx`，把「页签 + 空态(combo/factor) + StrategyList + 卡片编辑/删除薄分发」整块从 StrategyPanel 抽出承载，使其**净减**并向 800 收敛；DoD #6 改写为可度量口径（见 §2.2-B 行数约束、§三、§六、§七-2）。 |
| R-P2-1 | 一致性 (DRY) | P2→已修 | 原方案让**组合**卡片删除也走新 `useStrategyDelete`（§2.2-B/D），与既有 `useComboPanel.removeCombo` 形成**两套组合删除实现 / 两处确认弹窗 / 两条失效路径**，违反评审清单第 8 条；且组合删除的「编辑器/详情态收敛」只在 `useComboPanel` 内，分流后易漂移。 | 卡片删除按 kind 分流，与编辑分发对称：`isCombo → openDeleteCombo`（`useComboPanel` 单一真源），其余（atomic / factor）才 `requestDelete`（`useStrategyDelete`）。已在 §2.2-B/D、§三、§七-8 就地修正。 |
| R-P2-2 | 完整性 (UX) | P2 | 原子卡片「编辑」按钮语义等同于「选中该卡片」（原子无独立编辑弹窗，仅在下方详情区内联改参），可能让用户误以为会弹编辑框。 | 接受（需求要求「每张卡片」均有编辑入口）。实施期建议：原子「编辑」按钮加 `title="选中并在下方编辑参数"` 降歧义；不阻塞。 |
| R-P2-3 | 完整性 | P2 | 删除按钮 `disabled={s.unavailable \|\| s.builtin}` 会**连带禁掉失效(unavailable)策略行的删除**，使用户无法从卡片清理坏行（后端 DELETE 按 id 删除，本可删）。 | 建议删除可用性仅以 `!s.builtin` 为准（允许删 unavailable 清理）；编辑仍可对 unavailable 禁用。实施期注意项，不阻塞。 |
| R-P2-4 | 完整性 | P2 | paper 模式删除「正在运行模拟会话」的策略未设护栏（与既有 `useComboPanel` 同样未防，属既有口径）；但卡片常驻删除按钮使其更易触达。 | 建议：当 `strategyId ∈ paperSessionsByStrategyId` 时禁用该卡删除并 `title` 说明，或确认后端拒绝/级联停止。沿用既有口径，不阻塞。 |
| R-OK-1 | 一致性 | 通过 | 现卡片把 `<input type=checkbox>` 嵌在 `<button>` 内（交互元素非法嵌套，既有问题）。§2.2-C 改 `div` + 选择 `button` + 兄弟动作簇后**顺带修复**此 HTML 合法性问题。 | 维持方案；要求 StrategyList 测试断言「卡片内无嵌套交互元素」（§6.1）。 |
| R-OK-2 | 可行性 | 通过 | 复用端点与交接均已核实存在：`api.deleteStrategy`（`useComboPanel` 在用）、`factorApi.sets.remove` + 双失效（`FactorSetList` 在用）、因子编辑三连 `setFactorEditTarget(buildEditTargetFromStrategy)/setFactorLabSection("builder")/setActiveView("factor")`（`StrategyDetail.FactorStrategyBanner` 在用）、store action 与 partialize（`strategyListTab` 已持久化、store version 22、union 扩展向后兼容、无需 bump）。 | 方案断言全部属实，无需改动。 |
| R-OK-3 | 适配度 | 通过 | 「因子策略能否作为组合成员」不受本次拆分影响：`ComboEditorModal` 自有过滤 `s.kind !== "combo"`（已核实 line 60），不经 `splitStrategyCatalog`；`splitStrategyCatalog().regular` 唯一消费者确为 StrategyPanel。范围切割正确。 | 维持方案。 |

---

## 一、概述（Overview）

当前右侧「交易策略」面板（`apps/web/components/Strategy/StrategyPanel.tsx`）在回测 / 模拟（backtest / paper）模式下只暴露**两个**分类页签：`常规策略`（regular）与 `组合策略`（combo）。页签集合定义在 `apps/web/lib/strategyCatalog.ts` 的 `STRATEGY_LIST_TABS`，分类逻辑在 `splitStrategyCatalog`。问题在于：用户在「因子实验室」基于因子组合（`factor_set`）创建的**因子组合得分策略**（后端 `kind === "factor"`、模板 `factor_score`）目前被 `splitStrategyCatalog` 归入 `regular`（过滤口径为 `kind !== "combo"`），与普通原子策略混在「常规策略」页签里，缺少独立入口，辨识度低，也与因子实验室的「配方 ↔ 按配方做的菜」一一对应心智模型不一致。

本方案要做两件事。**第一**，把分类页签从两个扩展为三个：`常规策略`、`组合策略`、`因子策略`。`splitStrategyCatalog` 拆出独立的 `factors` 列表（口径 `kind === "factor"`），并从 `regular` 中排除 factor；当用户从因子实验室保存因子组合时（后端单事务自动部署 1:1 伴生 `factor_score` 策略，见记忆 `aragon-factor-set-ui-gap` 与 `docs/plans/factor-combo-strategy-sync/spec.md`），该伴生策略将出现在「因子策略」页签对应面板中。**第二**，为列表中**每一张**策略卡片在**最右侧**增加「编辑」与「删除」两个快捷按钮，让用户无需先选中、再到下方详情区操作，而是直接在卡片上就地操作；点击删除时必须弹出二次确认，用户确认后才真正删除，删除失败显式报错（不假成功）。

本方案是纯展示层与交互层改造：复用既有 REST 端点（`DELETE /strategies/{id}`、`DELETE /factor-sets/{id}`）与既有跨视图编辑交接机制（`setFactorEditTarget` + `setFactorLabSection("builder")` + `setActiveView("factor")`），不新增后端接口、不改数据库 schema、不改因子同步链路。核心约束是：**因子策略的删除必须经由其来源 `factor_set` 的级联删除完成**，以守住后端既有的「`factor_set` ↔ 伴生 `factor_score` 策略」1:1 同步不变量，避免直接删策略行造成孤儿、且被 `backfill_companions` 在下次启动时「复活」。

---

## 二、技术设计（Technical Design）

### 2.1 现状关键代码路径

- **页签与分类**：`apps/web/lib/strategyCatalog.ts`
  - `STRATEGY_LIST_TABS = [{key:"regular"...},{key:"combo"...}]`
  - `splitStrategyCatalog(strategies) => { regular: kind!=="combo", combos: isUserCombo }`
  - `visibleStrategiesForStrategyPanel({strategies,mode,strategyListTab})`：非 backtest/paper 一律 regular；否则按 tab 返回 combos 或 regular。
  - `resolveVisibleStrategyId(...)`：选中合法性回落到首个可见策略。
- **页签渲染与列表**：`StrategyPanel.tsx`
  - `catalog = splitStrategyCatalog(baseStrategies)`；`listStrategies = visibleStrategiesForStrategyPanel(...)`。
  - 第 487–496 行渲染 `<Tabs tabs={STRATEGY_LIST_TABS} active={strategyListTab} onChange={setStrategyListTab}/>`。
  - `allSelectableStrategies = [...catalog.regular, ...catalog.combos]`（paper 多选「全选当前页 / 清空」配额计算用）。
  - 选中合法性守门 `useEffect`（第 138–156 行）已在 `mode/strategyListTab/strategies` 变化时调用 `resolveVisibleStrategyId` 回落，**删除后列表 invalidate 刷新即可自动收敛选中态**，无需额外处理。
  - 组合三弹窗（编辑/详情/删除）已抽到 `useComboPanel`，删除走 `DeleteComboConfirm` + `api.deleteStrategy` + `invalidate(["strategies"])`。
- **卡片**：`StrategyList.tsx` 当前把每张卡片渲染为单个 `<button>`（含多选 checkbox、名称、徽标、描述）。
- **因子策略详情/编辑交接**：`StrategyDetail.tsx` 的 `FactorStrategyBanner` 已实现「在因子实验室编辑」按钮：`setFactorEditTarget(buildEditTargetFromStrategy(strategy)); setFactorLabSection("builder"); setActiveView("factor");`（`buildEditTargetFromStrategy` 来自 `apps/web/lib/factor.ts`，内部按 `params.factorSetId → set 分支`，旧策略回落 snapshot 分支）。
- **因子组合删除参照**：`FactorSetList.tsx` 的 `onDelete` 用 `window.confirm` + `factorApi.sets.remove(id)`，成功后**双失效** `["factor","sets"]` 与 `["strategies"]`（删组合连带删伴生策略）。

### 2.2 目标设计

#### (A) 三页签与分类拆分

1. `StrategyListTab` 联合类型增加 `"factor"`。
2. `STRATEGY_LIST_TABS` 追加 `{ key: "factor", label: "因子策略" }`，顺序为 `regular → combo → factor`。
3. 新增纯助手 `isFactorStrategy(s: Strategy): boolean => s.kind === "factor"`（放 `strategyCatalog.ts`）。
4. `splitStrategyCatalog` 返回 `{ regular, combos, factors }`：
   - `factors = strategies.filter(isFactorStrategy)`
   - `combos = strategies.filter(isUserCombo)`（不变）
   - `regular = strategies.filter(s => s.kind !== "combo" && !isFactorStrategy(s))`（**新增排除 factor**）
5. `visibleStrategiesForStrategyPanel`：非 backtest/paper 仍返回 `regular`；backtest/paper 下按 tab 三分支：`combo → combos`、`factor → factors`、其余 `regular`。
6. `resolveVisibleStrategyId` 逻辑不变（基于 `visibleStrategiesForStrategyPanel`，自动支持新 tab）。

> **影响面收敛**：经全仓检索，`splitStrategyCatalog().regular` 的唯一消费者是 `StrategyPanel`（`allSelectableStrategies`）。组合成员选择器 `ComboEditorModal` 使用自有过滤 `s.kind !== "combo"`，**不**经 `splitStrategyCatalog`，故本次拆分不改变「因子策略可否作为组合成员」的既有行为（保持现状，超出本需求范围，不动）。`compare.ts` 的 `atomic` 过滤同理独立，不动。

#### (B) StrategyPanel 接线

1. `allSelectableStrategies` 改为 `[...catalog.regular, ...catalog.combos, ...catalog.factors]`，使 paper 模式多选/全选/配额纳入因子策略（因子策略可参与模拟）。
2. 空态：现有 `showEmptyComboTab`（combo 页签且 `listStrategies.length===0`）旁，新增 `showEmptyFactorTab`（factor 页签且空）。因子空态文案与 CTA 不同：提示「暂无因子策略，去因子实验室创建因子组合（保存后自动出现在此）」，按钮 `去因子实验室` 调 `setActiveView("factor")`（+ `setFactorLabSection("builder")`）。建议抽一个小函数/分支统一渲染，避免重复 JSX。
3. 卡片编辑分发 `editStrategy(strategy)`（在 `StrategyPanel` 内，≤ 数行）：
   - `isCombo(strategy)` → `openEditCombo(strategy)`（复用 `useComboPanel`）。
   - `isFactorStrategy(strategy)` → 交接因子实验室（同 `FactorStrategyBanner` 三连：`setFactorEditTarget(buildEditTargetFromStrategy(strategy)); setFactorLabSection("builder"); setActiveView("factor");`）。
   - 否则（atomic）→ `selectStrategyPassively(strategy.id)`（等价于点击卡片；原子策略在下方详情区内联改参，无独立编辑弹窗）。
4. 卡片删除分发 `requestStrategyDelete(strategy)`（薄分发，与编辑分发对称，R-P2-1）：
   - `isCombo(strategy)` → `openDeleteCombo(strategy)`（复用 `useComboPanel`，**组合删除单一真源**，保留其「编辑器/详情态收敛」）。
   - 否则（atomic / factor）→ `useStrategyDelete` 的 `requestDelete(strategy)`（见 (D)）。
5. 把 `onEditStrategy={editStrategy}`、`onDeleteStrategy={requestStrategyDelete}` 透传给 `StrategyList`；同时渲染 `useComboPanel` 的 `comboModals` 与 `useStrategyDelete` 的 `deleteModal`。

> **注（R-P1-1）**：上述接线（编辑/删除分发 + 空态 + 列表 + 页签）整块迁入新组件 `StrategyListSection.tsx`（见 §三），不直接堆进已超阈值的 `StrategyPanel.tsx`。

> **行数约束（硬约束，v2 修订 R-P1-1）**：`StrategyPanel.tsx` 现 896 行，已超 CLAUDE 清洁码 800 行硬上限 96 行（既有技术债）。本次新增（第三页签接线 + 每卡片编辑/删除分发 + 两类弹窗渲染）均为净新增，**仅靠**新增 `useStrategyDelete.tsx` 无法对冲（StrategyPanel 内并无既有删除逻辑可移出）。因此本方案**强制**把「页签 + 空态(combo/factor) + StrategyList + 卡片编辑/删除薄分发」整块抽到新组件 `StrategyListSection.tsx`（见 §三），承载本次全部新增 JSX 与薄分发；删除态/双路 mutation/确认弹窗仍归 `useStrategyDelete.tsx`。验收以 §六 DoD #6 的可度量口径为准：改后 `StrategyPanel.tsx` 行数**必须严格小于 896（净减）**，且本次改动不得使任何单文件新越 800 行上限。

#### (C) StrategyList 卡片改造（避免嵌套 button）

当前外层是 `<button>`，若在其内部再放编辑/删除 `<button>` 会产生**非法嵌套 button**。改造为（参照 `FactorSetList.tsx` 的卡片结构）：

- 外层改为 `<div className="group ...">`（保留卡片边框/激活/hover 样式与 `s.unavailable` 透明度）。
- 内部「选择区」用一个 `<button className="flex-1 min-w-0 text-left ..." aria-pressed={active} disabled={s.unavailable} onClick={()=>onSelect(s.id)}>` 包住名称 + 徽标行 + 描述（即把原 button 的内容搬进来）。
- 多选 checkbox 仍作为选择 button 之前的**兄弟**节点（paper 模式）。
- 新增「动作簇」`<div className="flex items-center gap-1">` 作为选择 button 之后的**兄弟**节点，置于卡片**最右侧**，含：
  - 编辑按钮：`onClick={(e)=>{e.stopPropagation(); onEditStrategy?.(s);}}`，`disabled={s.unavailable}`，`aria-label={`编辑 ${s.name}`}`。
  - 删除按钮：`onClick={(e)=>{e.stopPropagation(); onDeleteStrategy?.(s);}}`，`disabled={s.unavailable || s.builtin}`，内置时 `title="内置策略不可删除"`，`aria-label={`删除 ${s.name}`}`。
- 因兄弟节点不在选择 button 内，点击动作不会触发选中（`stopPropagation` 为冗余保险，仍保留）。
- 动作按钮用紧凑图标/文字按钮（`✎`/`🗑` 或「编辑」「删除」），复用 `Button` 的 `ghost`+`size="sm"` 或等价紧凑样式，保证与现有视觉一致、点击热区可达（移动端可见，不做 hover-only）。
- 仅当父组件传入 `onEditStrategy`/`onDeleteStrategy` 时渲染动作簇（向后兼容其它调用点，如有）。

**删除可用性规则**：`canDelete = !s.builtin`。内置策略后端 `DELETE` 返回 409 `BUILTIN_READONLY`，故删除按钮**禁用**并 `title` 说明（不隐藏，保持三类卡片布局一致，并向用户解释原因）。组合页签仅含用户组合（`isUserCombo` 已滤掉内置组合）、因子页签为非内置伴生/副本，二者删除恒可用；仅常规页签的内置原子策略禁用删除。

#### (D) useStrategyDelete hook + 通用确认弹窗

新增 `apps/web/components/Strategy/useStrategyDelete.tsx`：

```ts
function useStrategyDelete(args: {
  strategyId: number | null;
  onAfterDelete: (deletedId: number) => void; // 通常 setStrategyId(null) 让守门回落
}): { requestDelete: (s: Strategy) => void; deleteModal: ReactNode }
```

内部：
- `deleteTarget: Strategy | null` 状态。`requestDelete = setDeleteTarget`。
- `removeStrategy = useMutation(api.deleteStrategy)`；`onSuccess` → `invalidate(["strategies"])`。
- `removeFactorSet = useMutation(factorApi.sets.remove)`；`onSuccess` → 双失效 `["factor","sets"]` + `["strategies"]`。
- 确认回调按目标分流（**组合不在此处理**，已分流至 `useComboPanel`，R-P2-1）：
  - `isFactorStrategy(t) && typeof t.params.factorSetId === "number"` → `removeFactorSet.mutate(t.params.factorSetId)`（级联删伴生策略，守 1:1）。
  - 否则（原子、或无 `factorSetId` 的孤儿因子副本——「另存为」已剥离 `factorSetId`）→ `removeStrategy.mutate(t.id)`。
- 任一成功：若 `deleteTarget.id === strategyId` 调 `onAfterDelete(id)`；`setDeleteTarget(null)`。失败保留弹窗并显示错误。
- `deleteModal` 渲染 `<DeleteStrategyConfirm>`（仅当 `deleteTarget` 非空）。

新增 `apps/web/components/Strategy/DeleteStrategyConfirm.tsx`（由现有 `DeleteComboConfirm.tsx` **泛化**而来，删除旧文件、更新 `useComboPanel` 引用）：

- props：`{ strategy: Strategy; pending: boolean; error: string|null; onCancel; onConfirm }`。
- 复用 `Modal` 原语（焦点陷阱 / Esc / 遮罩关闭）。
- 文案按 `strategy.kind` + 是否带 `factorSetId` 分支：
  - combo：「确认删除组合「{name}」？此操作不可撤销。」
  - factor 且有 `factorSetId`：「确认删除因子策略「{name}」？将同时移除因子实验室中对应的因子组合（数据保持一致）。此操作不可撤销。」
  - 其余（atomic / 孤儿 factor）：「确认删除策略「{name}」？此操作不可撤销。」
- 失败显式错误态；确认按钮 `variant="danger"`，`pending` 时禁用并显「删除中…」。

### 2.3 操作时序（删除因子策略，含级联）

1. 用户在「因子策略」页签某卡片点「删除」→ `StrategyList` 调 `onDeleteStrategy(s)` → `requestDelete(s)` 设 `deleteTarget`。
2. `DeleteStrategyConfirm` 弹出，文案提示将连带移除因子组合。
3. 用户点「删除」→ `onConfirm`：检测 `params.factorSetId` 存在 → `factorApi.sets.remove(factorSetId)`。
4. 后端单事务删 `factor_set` 并级联删伴生 `factor_score` 策略。
5. 成功 → 双失效 `["factor","sets"]`、`["strategies"]`；若删的是当前选中策略 → `setStrategyId(null)`；关闭弹窗。
6. `["strategies"]` 重新拉取 → `StrategyPanel` 选中守门 `useEffect` 调 `resolveVisibleStrategyId` 回落到因子页签首个剩余策略（或为空时显示空态）。

---

## 三、文件 / 模块变更计划（File / Module Change Plan）

| 文件 | 动作 | 一句话意图 |
|---|---|---|
| `apps/web/lib/types.ts` | 修改 | `StrategyListTab` 联合增加 `"factor"` 成员 |
| `apps/web/lib/strategyCatalog.ts` | 修改 | 加「因子策略」页签项；`splitStrategyCatalog` 拆出 `factors` 并从 `regular` 排除 factor；`visibleStrategiesForStrategyPanel` 处理 factor 页签；新增 `isFactorStrategy` 助手 |
| `apps/web/lib/strategyCatalog.test.ts` | 修改 | 加 factor fixture，覆盖三页签拆分 / 可见 / 选中回落 |
| `apps/web/components/Strategy/StrategyList.tsx` | 修改 | 卡片改 `div` + 选择 `button` + 右侧编辑/删除动作簇；内置禁用删除；新增 `onEditStrategy`/`onDeleteStrategy` props |
| `apps/web/components/Strategy/StrategyList.test.tsx` | 新建 | 动作簇渲染、内置删除禁用、编辑/删除回调、selection 不被动作误触 |
| `apps/web/components/Strategy/DeleteStrategyConfirm.tsx` | 新建（泛化自 DeleteComboConfirm） | 通用删除二次确认弹窗（combo/factor/atomic 文案分支），由 `useComboPanel`（combo）与 `useStrategyDelete`（atomic/factor）**共用** |
| `apps/web/components/Strategy/DeleteComboConfirm.tsx` | 删除 | 由 `DeleteStrategyConfirm` 取代（去重，守评审清单第 8 条）|
| `apps/web/components/Strategy/useComboPanel.tsx` | 修改 | 删除弹窗引用改为 `DeleteStrategyConfirm`；作为**组合删除单一真源**，卡片删除入口复用其既有 `openDeleteCombo`（R-P2-1）|
| `apps/web/components/Strategy/useStrategyDelete.tsx` | 新建 | 删除态 + 双路 mutation（factor_set 级联 vs 直接删策略）+ 确认弹窗渲染；**仅处理 atomic / 孤儿 factor**，combo 不经此 hook（R-P2-1）|
| `apps/web/components/Strategy/StrategyListSection.tsx` | 新建（R-P1-1） | 承载「页签 + 空态(combo/factor) + StrategyList + 卡片编辑/删除薄分发」，自 StrategyPanel 抽出以**净降其行数**并守 800 行硬上限；外部依赖以单一 props 配置对象传入（React 习惯，满足「形参 ≤5」约定）|
| `apps/web/components/Strategy/StrategyPanel.tsx` | 修改 | 接入 `catalog.factors`、paper 全选纳入 factor、装配 `useComboPanel`/`useStrategyDelete`、渲染 `StrategyListSection` 与两类弹窗；列表区 JSX 迁出后本文件**净减**（R-P1-1）|
| `apps/web/components/Strategy/StrategyPanel.test.tsx` | 修改 | 三页签渲染 / 切到因子页签可见因子策略 / 删除确认 / 因子级联删除 |
| `apps/web/lib/store.test.ts` | 修改 | `setStrategyListTab("factor")` 与持久化用例 |

> 备注：`apps/web/lib/store.ts` **无需**改动逻辑——`strategyListTab` 已声明为 `StrategyListTab`（联合扩展即兼容），持久化 `partialize` 已含该字段，旧持久化值 `"regular"/"combo"` 仍合法，新值 `"factor"` 仅在用户点击新页签时写入。**无需 bump store version**。（可选加固：如担心异常持久化值，可在新 migrate 版本里把非法 tab 收敛到 `"regular"`；本方案默认不加，避免无谓 version bump。）

---

## 四、接口设计（Interface Design）

**无新增后端接口**，全部复用既有端点；下列为前端契约（函数签名 / 复用 REST）。

### 4.1 复用 REST

- `DELETE /strategies/{id}` → `api.deleteStrategy(id)`：删原子 / 用户组合 / 孤儿因子副本。内置返回 409 `BUILTIN_READONLY`（前端已禁用内置删除，理论不触达）。
- `DELETE /factor-sets/{id}` → `factorApi.sets.remove(id)`：删因子组合，后端级联删伴生 `factor_score` 策略（守 1:1）。

### 4.2 前端组件 / hook 契约

```ts
// strategyCatalog.ts
export function isFactorStrategy(s: Strategy): boolean; // s.kind === "factor"
export function splitStrategyCatalog(strategies: Strategy[]): {
  regular: Strategy[]; combos: Strategy[]; factors: Strategy[];
};

// StrategyList.tsx 新增 props（均可选，缺省不渲染动作簇）
onEditStrategy?: (s: Strategy) => void;
onDeleteStrategy?: (s: Strategy) => void;

// useStrategyDelete.tsx
function useStrategyDelete(args: {
  strategyId: number | null;
  onAfterDelete: (deletedId: number) => void;
}): { requestDelete: (s: Strategy) => void; deleteModal: ReactNode };

// DeleteStrategyConfirm.tsx
interface DeleteStrategyConfirmProps {
  strategy: Strategy; pending: boolean; error: string | null;
  onCancel: () => void; onConfirm: () => void;
}
```

### 4.3 跨视图编辑交接（复用既有 store action，无新增）

`setFactorEditTarget(buildEditTargetFromStrategy(strategy))` → `setFactorLabSection("builder")` → `setActiveView("factor")`。

---

## 五、数据模型（Data Model）

**无数据库 / schema 变更**。仅前端内存形态：

- `StrategyListTab = "regular" | "combo" | "factor"`（`types.ts`）。
- `splitStrategyCatalog` 返回新增 `factors: Strategy[]`。
- 因子反向链接沿用既有约定：`Strategy.params.factorSetId?: number`（无 DB 列，删除分流读此字段）。`kind === "factor"` 由后端 `merge_strategy` 透传（`StrategyKind = "atomic" | "combo" | "factor"`，已存在）。
- 持久化：`localStorage` 键 `aragon-ui` 的 `state.strategyListTab` 允许取值集合扩为三者；无版本迁移。

---

## 六、测试与验收标准（Testing & Acceptance Criteria）

> 组件 vitest 须以 `NODE_ENV=test` 运行（见记忆 `aragon-web-vitest-node-env`；shell 默认 production 会触发 React act() 报错）；jest-dom 未全局 setup，断言用原生 DOM。

### 6.1 单元测试

- `strategyCatalog.test.ts`：
  - 新增 `FACTOR_ONE`（`kind:"factor"`，`params.factorSetId:1`）fixture。
  - `splitStrategyCatalog().factors` 仅含 factor；`regular` **不含** factor；`combos` 不变。
  - `visible("backtest","factor")` / `visible("paper","factor")` 返回 factor 集；`visible(*, "regular")` 不含 factor。
  - `resolveVisibleStrategyId({tab:"factor", currentId: 非 factor})` 回落首个 factor；无 factor 时返回 `null`。
- `StrategyList.test.tsx`（新建）：
  - 传入 `onEditStrategy/onDeleteStrategy` 渲染编辑/删除按钮；点编辑/删除分别触发对应回调且**不**触发 `onSelect`（selection 不被误触）。
  - `builtin` 卡片删除按钮 `disabled`，普通卡片可用。
  - 卡片根节点为非交互容器，内部无嵌套于 `<button>` 的交互元素（checkbox / 动作按钮均为选择 button 的兄弟节点，R-OK-1）。
- `store.test.ts`：`setStrategyListTab("factor")` 后 state 与 `localStorage` 持久化值为 `"factor"`。

### 6.2 组件 / 集成测试（`StrategyPanel.test.tsx`）

- backtest/paper 模式渲染三个页签文案：`常规策略 / 组合策略 / 因子策略`。
- 切到「因子策略」页签：列表只显示 `kind==="factor"` 策略；常规页签不再出现因子策略。
- 因子页签为空时显示空态文案与「去因子实验室」CTA。
- 卡片删除：点删除弹出 `DeleteStrategyConfirm`，点取消不调用任何删除 API；点确认对**普通策略**调 `api.deleteStrategy`、对**带 factorSetId 的因子策略**调 `factorApi.sets.remove`、对**组合策略**经 `useComboPanel` 调 `api.deleteStrategy`（R-P2-1 分流），且成功后失效查询。
- 删除失败时弹窗保留并显示错误文案（不假成功）。

### 6.3 验收标准（DoD）

1. 右侧交易策略面板在回测 / 模拟模式出现三页签，顺序 `常规策略 → 组合策略 → 因子策略`。
2. 因子实验室保存的因子组合得分策略只在「因子策略」页签出现，不再混入常规页签。
3. 每张策略卡片最右侧有「编辑」「删除」按钮：编辑按 kind 正确分发（组合→编辑弹窗、因子→因子实验室、原子→选中改参）；删除弹二次确认，确认后才删，内置策略删除禁用。
4. 删除因子策略经 `factor_set` 级联（带 `factorSetId` 时），删后因子实验室与交易面板两端一致；孤儿因子副本与普通/组合策略走直接删除。
5. 删除后选中态自动回落到当前页签首个剩余策略（或空态）。
6. `apps/web` 全量 lint / typecheck / vitest 通过；列表区已抽至 `StrategyListSection.tsx`，改后 `StrategyPanel.tsx` 行数**严格小于改前 896 行（净减）**；新增的 `StrategyListSection.tsx` / `useStrategyDelete.tsx` 各自 ≤800 行（理应远小于）。本次改动不得使任何单文件新越 800 行硬上限。

---

## 七、风险与缓解（Risks & Mitigations）

1. **嵌套 button 非法 HTML**：原卡片是 `<button>`，内放动作 `<button>` 非法。→ 卡片改 `<div>` + 选择 `<button>` + 动作簇兄弟节点（参照 `FactorSetList`），动作不嵌套于选择 button。
2. **`StrategyPanel.tsx` 已超 800 行**：继续加逻辑会恶化技术债。→ 删除态/双路 mutation/确认弹窗抽 `useStrategyDelete.tsx`；**列表区（页签 + 空态 + StrategyList + 卡片编辑/删除薄分发）整块抽 `StrategyListSection.tsx`**（R-P1-1），使本文件**净减**。改后 `StrategyPanel.tsx` 行数必须 < 896（见 DoD #6）。
3. **因子策略删除破坏 1:1 同步**：直接 `api.deleteStrategy` 会留孤儿 `factor_set`，且 `backfill_companions`（`main.py` lifespan）下次启动复活伴生策略。→ 带 `factorSetId` 时一律走 `factorApi.sets.remove` 级联删除；文案明确告知连带移除。
4. **内置策略删除 409**：→ `builtin` 删除按钮禁用 + `title` 解释，不发请求。
5. **paper 多选/全选漏掉因子策略**：`allSelectableStrategies` 原只含 regular+combos。→ 追加 `...catalog.factors`，配额/全选/清空覆盖因子策略。
6. **持久化 tab 兼容**：→ 联合扩展向后兼容，旧值合法、无需迁移；如需加固再补 migrate 收敛非法值。
7. **三页签布局换行/拥挤**：→ 复用 `Tabs variant="underline"`，必要时容器允许换行 / 缩短 label（保持「因子策略」四字）。
8. **组合删除回归**：`DeleteComboConfirm` 泛化为 `DeleteStrategyConfirm` 须同步改 `useComboPanel` 引用与既有组合删除测试；卡片删除对 combo 仍分流回 `useComboPanel`（R-P2-1），组合删除单一真源不变。→ 保留组合文案分支，跑既有 `StrategyPanel.test.tsx` 组合删除用例 + 新增「卡片删除组合」用例验证不回归。
9. **测试环境 act() 报错**：→ 组件测试 `NODE_ENV=test` 运行，断言用原生 DOM。
10. **编辑交接副作用**：因子编辑切到 `factor` 视图会离开交易面板。→ 与 `FactorStrategyBanner` 现有「在因子实验室编辑」行为完全一致，符合既有心智，不引入新副作用。

---

## 八、实施过程发现的方案缺陷（Subtask #2 回填，预留）

> 开发节点如发现本方案缺陷，请在此追加记录（问题 / 影响 / 处置）。

---

## 评审结论（Review Verdict）

**有条件通过（Approved with conditions）。**

四维核查结果：

- **可行性**充分——所有复用端点（`api.deleteStrategy`、`factorApi.sets.remove`）、store action（`setStrategyListTab`/`setFactorEditTarget`/`setFactorLabSection`/`setActiveView`）、跨视图编辑交接链路（`buildEditTargetFromStrategy`）与分类 helper（`isFactorStrategy`/`isUserCombo`）均已对照真实代码核实存在且语义吻合（R-OK-2）。
- **一致性**良好——守住 `factor_set ↔ 伴生 factor_score 策略` 1:1 不变量（带 `factorSetId` 走级联删除）、复用 `Modal`/`Button`/`Tabs` 原语、union 扩展向后兼容无需 bump store version，并**顺带修复**既有「checkbox 嵌套于 button」的非法 HTML（R-OK-1）。
- **完整性**覆盖空态（combo/factor 各自文案与 CTA）、失败态（弹窗保留 + 显式错误，不假成功）、选中回落（复用既有守门 `useEffect`）、孤儿因子副本（「另存为」剥离 `factorSetId` → 直接删）等边界。
- **适配度**得当——范围正确切割，未越界改动「因子策略作为组合成员」的既有资格（`ComboEditorModal` 自有过滤，R-OK-3）或 `compare.ts` 过滤。

裁决依据：P0 **无**；P1 **1 项**（R-P1-1 行数 DoD 不可达）——已在本 v2 正文就地修复（强制抽 `StrategyListSection.tsx` 并改写可度量 DoD #6 / §2.2-B / §三 / §七-2）。另将 R-P2-1（组合删除重复实现）一并升格为正文修复（卡片删除对 combo 分流回 `useComboPanel`，组合删除保持单一真源）。**至此无 P0 / P1 遗留。**

实施期须遵守的非阻塞条件（P2，由开发 / 代码 Review 节点落实即可，无须再次评审）：

1. **R-P2-2**：原子「编辑」按钮加 `title`（如「选中并在下方编辑参数」），降低「期待弹窗」的歧义。
2. **R-P2-3**：删除可用性以 `!s.builtin` 为准，允许删除 `unavailable` 坏行以便清理（编辑仍可对 unavailable 禁用）。
3. **R-P2-4**：删除入口对「正在运行模拟会话」的策略加禁用 / 二次提示（或确认后端拒绝 / 级联停止），避免误删运行中策略。
4. **R-OK-1**：`StrategyList` 测试须断言卡片内无嵌套交互元素。

满足以上条件后即视为方案落地就绪，可进入 Subtask #2 代码开发。
