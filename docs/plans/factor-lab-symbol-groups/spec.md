# 因子实验室「标的组」多标的因子挖掘 / 权重训练 —— 开发方案（spec）

- **Feature slug**: `factor-lab-symbol-groups`
- **文档版本**: **v2**（方案评审与修复，Subtask #1 产出；v1 为方案设计，Subtask #0 产出）
- **作者**: 方案设计 Agent（v1）；方案评审 Agent（v2，Anthropic 工程团队 · 资深评审视角）
- **状态**: 已评审（Subtask #1）——结论见文末「评审结论」：**有条件通过**，所有 P0/P1 已就地修复。
- **语言约定**: 正文简体中文；标识符、路径、字段名沿用仓库既有英文 camelCase / snake_case。
- **时间约定**: 一切时间戳为**秒级 UTC**（沿用 `_FactorRange` / provenance 口径）。

---

## 评审记录（Review Notes · Subtask #1）

> **评审方法**：以 Anthropic 工程团队资深评审视角，对本 spec 逐节比对**当前真实代码**核验四维——可行性（现有栈能否实现）/ 完整性（边界、错误路径、上线）/ 一致性（是否冲突 `CLAUDE.md` 与既有约定）/ 规模适配（是否过度或欠设计）。
>
> **总体判定**：设计与仓库现状**高度吻合**，被引用的锚点经核验**全部真实存在**：等价性门禁函数 `train_weights` / `build_train_result` / `fit_at_fraction` / `prepare_curve_sample`（`engine/factors/training.py:371/299/341/313`）确存，方案仅**并行新增** `build_pooled_sample` / `train_weights_on_sample`；`build_factor_matrix` 明确「`normStats` 仅记录契约参数 `{normWindow, clip}`、**不**存固定 mean/std」（`training.py:116`），故多标的池化**无 normStats 歧义**，单标的输入与 `prepare_curve_sample` 可字节等价；`resolve_direction`（`evaluation.py:186`）、进程内 `_candle_cache`（`service.py:74`，TTL 90s / cap 16）、pydantic `BaseSettings` 且 `factor_train_max_bars` 已存、`ConfirmDialog`（`components/ui/ConfirmDialog.tsx`）/ `SettingsPanel` / `Modal`（`components/ui/index.tsx` 导出）均在。**未发现 P0（阻断性）问题**。
>
> 下表 P1 已在本文档 v2 **就地修复**；P2 为实现期建议（其中文档准确性类已一并订正）。

| # | 严重度 | 维度 | 问题 | 处置 |
|---|---|---|---|---|
| C-1 | **P1** | 完整性/可行性 | **多标的评估无扩散护栏**：`mine`（`_guard_mine_workload` 在取数前 413、且工作量将乘标的数）与 `train`（新增 `max_train_symbols`）均有护栏，唯 `evaluate` 对标的数**零护栏**；组容量上限 `_GROUP_SYMBOLS_MAX=100`，大组评估将无界扇出逐标的 OKX 取数，重演 429 / socket 压力，违反 R2 与「绝不静默」口径。 | 已修 §3.3 / §四 / §五：评估新增 `max_eval_symbols`（默认 30）标的数上限 → 400 `FACTOR_EVAL_TOO_MANY_SYMBOLS`；组内各标的**共享同一区间**，故逐标的区间由既有 `_FactorRange` 校验覆盖，无需额外 413。 |
| C-2 | **P1** | 完整性/正确性 | **评估聚合的方向冻结未精确**：`resolve_direction` 现于 `evaluate_factor` **内部**逐标的解析 `auto`（`evaluation.py:220`）；若逐标的各自定向，跨标的 RankIC 符号可能相反，`occurrences` 加权均值统计上不自洽——原文仅点到「先定向后聚合」，机制不落地。 | 已修 §3.3：明确 `auto` 仅在**焦点标的**解析一次（复用其 `result.direction`，焦点标的评估结果直接并入、**不重复取数**），其余标的以**冻结后的显式 `long`/`short`** 评估；入参本就是 `long`/`short` 时无需冻结、直接透传。 |
| C-3 | **P1** | 完整性/正确性 | **挖掘聚合跨标的方向归并缺陷**：§3.4 归并键含 `direction`；`mine_factors` 逐标的对同一 `factorKey+params` 候选的 `auto` 可能解析出**相反**方向 → 被拆成两键无法归并，甚至 topN 同因子一多一空、结论自相矛盾。 | 已修 §3.4：归并键改为 `(factorKey, canonical(params))`（**不含** direction）；候选方向按**焦点标的**解析统一（回落聚合 IR 符号），每候选仅聚合一次并回填统一 `direction`。 |
| C-4 | **P1** | 一致性 | **「镜像 `MemberPalette`」与代码冲突**：`MemberPalette` 候选来源是**因子库**（「库目录 / 我的因子」，`factorApi.list`），**非自选标的**；直接「镜像」会误导实现者去复用错误的数据源与组件。 | 已修 §6.3：改为复用其 **chip-toggle + 搜索交互范式**，候选来源明确为 `store.watchlist`；`SymbolGroupModal` 的成员挑选器为**新增轻组件**（参照自选 / 自动交易成员挑选口径），不复用 `MemberPalette` 本体。 |
| C-5 | P2 | 文档准确性 | persist 版本表述「22→24」与括注「现值 23」自相矛盾（实际 `store.ts:663` 为 `version: 23`）。 | 已修 §3.2：更正为 **23→24**（单步递增）。 |
| C-6 | P2 | 路径精确性 | `ui/Modal` 并非独立文件，实为 `components/ui/index.tsx` 导出（`import { Modal } from "@/components/ui"`）；`ConfirmDialog` 在 `components/ui/ConfirmDialog.tsx`。 | 已修 §6.3 / §七：标注真实导入路径。 |
| C-7 | P2 | 签名精确性 | `service.evaluate` / `mine` 以 `window: FactorWindow` 入参、**无 `body`**（`service.py:139/182`）；文中「`body.symbols or [instId]`」为示意伪码，易被误当真实签名。 | 已修 §3.3 / §3.4：明确 `symbols` 作为**新增 keyword 参数**从 API 层贯通至 service，服务内按标的构造 per-symbol `FactorWindow`（同 `bar`/区间、仅换 `inst_id`），复用 `_candle_cache`。 |
| C-8 | P2 | 交互/完整性 | 多标的 `_run` 仅发一次 `loading@5%`（`weight_train_manager.py`），≤20 标的顺序取数期间 UI 疑似「卡在 5%」。 | 已修 §3.5：`loading` 阶段按标的发**增量进度**（`5 → 25%` 区间线性推进），并在取数间隙检查取消。 |
| C-9 | P2 | 局限声明 | 焦点标的 = 组内**首个**，既是图表 / 建模默认，又（经 C-2）成为聚合**方向锚点**，组内排序会影响锚定与建模默认——需显式声明以免被当 bug。 | 已修 §3.1 / R6：明确「确定性取首成员、且为方向锚点」；组编辑器成员**有序**，用户可经排序选定锚点。 |
| C-10 | P2 | 缓存/规模 | `_candle_cache` cap=16 < 评估可能标的数（≤100）；大组下 LRU 逐出会削弱 R2「复用进程内缓存」的收益，评估 / 训练可能重取。 | 已修 R2：注明 cap=16 与「大组超 cap 时逐出重取」事实；`max_eval_symbols=30` 亦顺带把评估扇出压回 cap 量级附近。 |

---

## 一、概述（Overview）

当前「因子实验室」（`apps/web/components/Factor/FactorLab.tsx`）在标题「因子实验室」下方有一条**工作台横栏**，其中「标的」下拉框只允许从用户**自选标的**（`store.watchlist`）中选择**单一标的**。该单一标的经 `WorkbenchContext` 向下贯穿到「因子评估」「因子挖掘」「权重训练与组合」「权重模型」四个工作区：评估 / 挖掘直接以单标的取数计算 IC / RankIC / IR，权重训练亦只在单标的的一段 K 线上拟合因子权重。这导致训练出的因子权重 / 评估结论**只对单一标的过拟合**，无法回答「这组因子在一**篮子**标的上是否稳健有效」这一量化研究的核心问题。

本方案在**不破坏现有单标的路径**（含 `test_streaming_result_equals_sync_train` 等价性硬门禁）的前提下，为因子实验室引入**「标的组」（Symbol Group）** 一等概念。用户可在工作台横栏**最右侧**新增的「管理标的组」按钮弹出的**弹窗面板**中，对标的组进行**增删改查**；标的组的成员**来自用户的自选标的**（自选标的的任意组合）。横栏的目标选择器由「单标的下拉」升级为「单标的 / 标的组」统一选择器，用户在外部即可选中某个标的组，驱动评估 / 挖掘 / 权重训练。当选中的是标的组时，后端会**分别取出组内各标的的真实 OKX K 线数据**，逐标的计算后按**样本数加权聚合**（评估 / 挖掘）或**池化拼接**（训练），从而得到「跨标的稳健」的因子评估与权重。

设计遵循仓库既有架构与「绝不假数据、绝不静默降级、绝不静默丢弃」的工程约束：标的组持久化复用既有 `workspace.watchlist_prefs` KV 文档与 `useWorkspaceSync` 后端权威水合 + 去抖写穿链路（后端 SQLite 权威、桌面端强杀重启后仍在）；训练侧多标的能力**新增**并行的池化函数，绝不修改被等价性门禁守护的 `train_weights` / `build_train_result`；DB 侧**零迁移**（新增数据一律写入既有 JSON 列 `config_json` / `provenance_json`，契合本仓库 `create_all` 加法建表、无列迁移的约束）。视觉上追求顶级设计、符合人机交互最佳实践（optgroup 分组选择器、聚焦陷阱弹窗、click-to-toggle 成员挑选、逐标的贡献可见的透明聚合、空态与越界提示）。

---

## 二、术语与现状（Glossary & Current State）

| 术语 | 含义 |
|---|---|
| 自选标的（watchlist） | `store.watchlist: string[]`，后端权威（`/workspace/watchlist`）+ localStorage 缓存。 |
| 标的组（Symbol Group） | **本方案新增**：从自选标的挑选并命名的一组 instId，供因子实验室按组评估 / 挖掘 / 训练。 |
| 焦点标的（focus instId） | 目标为单标的时即该标的；为标的组时取组内**首个**标的。用于**单标的语义**的图表 / 打分 / 建模默认标的（`compute` / `score` / `score-preview` / `WeightModelEditor` 建模）。 |
| 工作台（Workbench） | `FactorLab.tsx` 内 `WorkbenchContext`，承载「标的 / 周期 / 区间」并向四个工作区下发（lab 本地态，不回写主工作台）。 |

**现状关键事实（已核验）**：

1. 横栏「标的」为单一 `<Select value={instId}>`（`FactorLab.tsx:169-183`），`instId` 由 `resolveWatchlistInstId(storeInst, watchlist)`（`lib/factor.ts:459`）夹取到自选列表合法值。
2. 评估 / 挖掘面板直接以 `range = { instId: wb.instId, bar, startTs, endTs }` 调 `factorApi.evaluate` / `factorApi.mine`（`FactorEvalPanel.tsx:46`、`FactorMiningPanel.tsx:23`）。
3. **训练链路已全部走权重模型工作区**：`FactorSetBuilder`「训练权重」写瞬态 `weightTrainTarget`（含单 `instId`）并跳到 `models` 区，`WeightTrainLive` 以 `weightModelApi.train(trainStartBody(target))` 发起流式训练（`WeightTrainLive.tsx:92`）。`factorApi.train` / `factorApi.scorePreview` 已**无任何调用点（dead）**，本方案不复活。
4. 训练引擎：`build_factor_matrix` 逐成员**因果滚动 z-score** → X[n,k]；`forward_return_label` → y；`train_weights` = `_aligned` 去 NaN + `_train_cut` 时序切分（默认前 70%）+ 拟合 + `build_train_result`。流式作业 `weight_train_manager._TrainJob._run` 取单 df → 单 matrix → `prepare_curve_sample` + 逐 fraction `fit_at_fraction` + 末步 `train_weights`（与同步 `POST /factor-sets/train` **字节级等价**，`test_streaming_result_equals_sync_train` 守护）。
5. 持久化：`workspace.watchlist_prefs`（`AppSetting` KV）经 `_sanitize_workspace_watchlist` 边界规整（读写各跑一次），`useWorkspaceSync` 首次 GET 后 `hydrateWorkspace` 覆盖、切片变更去抖 fire-and-forget PUT。DB 为 `Base.metadata.create_all`（`session.py:69`）——**加法建表、无列迁移**。

---

## 三、技术设计（Technical Design）

### 3.1 选择模型：`FactorTarget`（单标的 / 标的组）

在 `FactorLab.tsx` 的 `Workbench` 上以「目标」取代「单 instId」，同时保留 `instId` 作为**焦点标的**以零成本兼容所有单标的消费方：

```ts
// lib/factorTypes.ts（新增）
export interface SymbolGroup {
  id: string;          // 客户端生成的 uuid（crypto.randomUUID）
  name: string;
  symbols: string[];   // 来自自选标的（创建时），持久化后不因自选变动被强制裁剪（§3.2 决策）
  createdTs: number;
  updatedTs: number;
}
export type FactorTarget =
  | { kind: "single"; instId: string }
  | { kind: "group"; groupId: string; name: string; symbols: string[] };
```

`Workbench` 接口扩展（`FactorLab.tsx`）：

```ts
export interface Workbench {
  target: FactorTarget;          // 新增：当前目标
  symbols: string[];             // 新增：派生——single→[instId]；group→去空去重后的 symbols（保证非空）
  instId: string;                // 保留：焦点标的——single→instId；group→symbols[0]
  bar: string; startTs: number; endTs: number;
  setTarget: (t: FactorTarget) => void;   // 取代 setInstId
  setBar: (v: string) => void;
  setRange: (startTs: number, endTs: number) => void;
  // evalTarget / pendingMembers 不变
}
```

- **消费分工**：`symbols`（多）驱动**统计**类工作区（评估 / 挖掘 / 训练）；`instId`（焦点，单）驱动**图表 / 单标的**语义（`compute` 上图预览、`score` / `score-preview`、`WeightModelEditor` 建模默认标的）。
- **焦点标的的确定性与三重角色（C-9）**：焦点标的**确定性**取组内**首个**成员（`symbols[0]`）。它同时承担三重角色——(a) 图表 / 打分 / 建模的单标的默认；(b) 评估 / 挖掘聚合的**方向锚点**（`auto` 在焦点标的解析一次后冻结下发，见 §3.3 / §3.4）；(c) 一键回测的落地标的（§3.5）。因此组编辑器的成员**有序**，用户可通过排序把期望的代表标的置顶以选定锚点；此为**已声明的确定性行为**，非缺陷。
- **派生与守门**（新增纯函数 `resolveFactorTarget(ref, watchlist, groups)` in `lib/factor.ts`，类比 `resolveWatchlistInstId`）：把持久化的目标指针解析为合法 `FactorTarget`——group 指针若组已删 / 组内标的全部失效则回落 focus 单标的；single 指针经 `resolveWatchlistInstId` 夹取。保证选择器 `value` 恒命中一个 option、`symbols` 恒非空。
- **选择持久化**：新增 `store.factorLabTarget: { kind:"single"; instId:string } | { kind:"group"; groupId:string } | null`（persist，默认 `null`）。工作台挂载时 seed，`setTarget` 双写（本地 + store 指针）。跨会话记住上次选择。

### 3.2 标的组数据模型与持久化（复用 workspace 偏好文档）

标的组是**用户偏好**（服务端权威、随桌面端持久），且成员**来自自选标的**，与 `watchlist` / `autoTradeMembers` 同域，故**扩展**既有 `workspace.watchlist_prefs` 文档而非新建端点 / 表：

- 前端 `WorkspacePrefs`（`lib/workspaceTypes.ts`）新增 `symbolGroups: SymbolGroup[]`；`WorkspaceWatchlistIn/Out` 随之带该字段。
- 后端 `_sanitize_workspace_watchlist`（`db/repositories.py`）新增第 4 段规整 `symbolGroups`（唯一规整点，读写各跑一次，作为跨版本 / 手改信任边界）：
  - 每组：`id` 转 str 去空、按 id **去重**（首现保留）；`name` 转 str、trim、截断（≤ 64）；`symbols` 转 str、trim、单 id 截断（≤ `_INST_ID_MAX_LEN=64`）、组内**去重**、**每组上限** `_GROUP_SYMBOLS_MAX = 100`；`createdTs`/`updatedTs` 转 int 非负、缺省 0。
  - **整表上限** `_SYMBOL_GROUPS_MAX = 50` 组；超出按序截断。
  - **丢弃退化组**：`name` 为空**或** `symbols` 为空的组丢弃（空组无意义）。
  - **决策（不强制 `⊆ watchlist`）**：与 `autoTradeMembers` 的 `⊆ watchlist` 不同，标的组成员**不**按当前自选裁剪——避免用户临时取消某自选标的时**静默掏空**已保存组（符合「绝不静默丢弃」）。编辑器**候选来源**仍限自选标的；对某组内已不在自选的标的以「灰显 + 已移出自选」提示（§六 UI）。instId 仍为合法 OKX 标的，评估 / 训练可正常取数。
- `set_workspace_watchlist` 增 `symbol_groups` 入参并入库；`get_workspace_watchlist` 经 sanitizer 回读。
- `api/workspace.py` GET/PUT 透传 `symbolGroups`。
- `useWorkspaceSync`（`lib/useWorkspaceSync.ts`）：`WorkspaceSlice` 增 `symbolGroups`；`sliceOf` / `sliceChanged`（第 4 字段引用比对）/ `hydrateWorkspace` 同步扩展。
- `store.ts`：新增 `symbolGroups: SymbolGroup[]`（默认 `[]`，**persist**）+ 三个纯 CRUD setter：`addSymbolGroup(g)` / `updateSymbolGroup(id, patch)` / `removeSymbolGroup(id)`（不可变更新，`updatedTs` 由调用方注入 `Math.floor(Date.now()/1000)`）。`hydrateWorkspace` 参数与 `partialize` 增 `symbolGroups`；persist `version` **23→24**（现值 `store.ts:663` 为 `version: 23`，单步递增至 24）并加 additive migrate 分支回填 `symbolGroups: []`、`factorLabTarget: null`。

> 说明（C-5 修正）：本方案唯一改动 persist 版本，且为 **23→24 单步递增**（v1 误写「22→24」已订正）；`factorLabTarget`（瞬态语义但为记忆选择而 persist）与 `symbolGroups` 一并在 migrate 中回填默认，杜绝老缓存缺键。

### 3.3 评估多标的聚合（`/factors/evaluate`）

**引擎不改** `evaluate_factor` / `evaluate_factor_key`（零回归；实测签名 `evaluate_factor_key(df, key, params, direction, *, bar, horizons, quantiles, ...)`，`service.py:139`）。在 `engine/factors/evaluation.py` **新增纯函数** `aggregate_eval_results(results, symbols, direction) -> dict`，服务层编排：

1. **签名（C-7 精确化）**：`symbols` 作为**新增 keyword 参数**从 API 层贯通到服务——`service.evaluate(session, rest, window, *, factor_key, params, direction, horizons, quantiles, symbols=None, ...)`。服务内 `effective_symbols = symbols or [window.inst_id]`；多标的时**按标的构造 per-symbol `FactorWindow`**（`replace(window, inst_id=sym)`，同 `bar`/区间、仅换 `inst_id`）逐一 `_load_df`（复用进程内 `_candle_cache`，逐标的取真实 OKX 数据）。单标的（`len==1`）→ 走**现有路径**（行为字节级不变）。
2. **扩散护栏（C-1）**：多标的评估在取数前先夹取标的数——`去空去重后 len(effective_symbols) > settings.max_eval_symbols`（默认 30）→ 400 `FACTOR_EVAL_TOO_MANY_SYMBOLS`（绝不无界扇出 OKX 请求）。组内各标的**共享同一 `bar`/区间**，故逐标的区间合法性由既有 `_FactorRange` 请求校验一次覆盖，无需额外 413。
3. **方向冻结（C-2 精确化）**：`auto` 的方向必须**跨标的一致**，否则各标的 RankIC 符号相反、加权均值不自洽。落地机制——
   - `direction ∈ {long, short}`：**无需冻结**，直接以该显式方向逐标的 `evaluate_factor_key`。
   - `direction == "auto"`：**仅在焦点标的**（`effective_symbols[0]`）解析一次——对焦点标的 `evaluate_factor_key(..., "auto")`，从其结果 `result.direction` 读回**已冻结**的 `long`/`short`（`resolve_direction` 现于 `evaluate_factor` 内部按主 horizon RankIC 符号定向，`evaluation.py:220`）；**焦点标的的该结果直接并入聚合、不重复取数 / 不二次评估**；其余标的一律以**冻结后的显式方向** `evaluate_factor_key`。逐标的同向，聚合才自洽。
4. `aggregate_eval_results`（各标的均已同向，聚合才成立）：
   - `occurrences = Σ`；`bars = Σ`；`coverage =` 按 bars 加权均值。
   - 每 horizon 每指标（ic / rankIc / icMean / icStd / ir / tStat / monotonicity / topBottomSpread）= 各标的**非空值按 occurrences 加权均值**（全空→null，**绝不臆造 0**）。
   - 分位桶：逐分位 index `meanRet =` 按 count 加权均值，`count = Σ`。
   - `note`：任一成员 bar 不兼容→`bar_incompatible`（优先）；否则池化 `occurrences < MIN_SIGNALS`（实测 `MIN_SIGNALS=20`，`signal_edge.py`）→`samples_sparse`；否则 `""`。
   - **透明性**：附 `perSymbol: [{instId, occurrences, primaryRankIc, coverage}]`（逐标的贡献，供 UI 抽屉展示，绝不隐藏哪些标的入了聚合）。
   - 顶层附 `symbols: effective_symbols`、`direction:` 冻结后的方向、`aggregated: len>1`。
5. 兼容：`FactorEvalResult` TS 类型新增可选 `symbols?`、`perSymbol?`、`aggregated?`；`instId` 在聚合态置焦点标的。旧响应缺省安全。

> 备选（已否决）：跨标的**池化配对样本**再算 IC，统计上更强，但需改引擎、破坏单标的测试、且失去逐标的可解释性。选用「逐标的评估 + occurrences 加权均值」——即量化常用的「组内平均 IC」，可复用引擎、逐标的可审计。

### 3.4 挖掘多标的聚合（`/factors/mine`）

`engine/factors/mining.py` 新增纯函数 `aggregate_mine_results(per_symbol_ranked, symbols, top_n) -> list[dict]`；`service.mine`：

1. **签名（C-7 精确化）**：`symbols` 作为**新增 keyword 参数**贯通——`service.mine(session, rest, window, *, categories, horizons, top_n, persist, symbols=None)`；`effective_symbols = symbols or [window.inst_id]`；多标的按标的构造 per-symbol `FactorWindow` 逐一取数。单标的走现有路径。
2. **413 工作量护栏（取数前，天然扇出保护）**：`_guard_mine_workload`（实测在 `service.py:202`，非 engine）的 `mining_workload(len(candidates), effective)` 改为**乘以 `len(effective_symbols)`**（总工作量 = 候选 × 有效根数 × 标的数），超 `MINE_WORK_UNIT_LIMIT`（实测 `3_000_000`）仍 413（绝不静默少算）。该护栏**在 `_load_df` 之前**执行（`service.py:187` 先于 `188`），故大组会**先 413、再取数**，扇出天然受限——挖掘无需额外标的数上限。
3. **逐标的 `mine_factors` → 归并（C-3 方向统一）**：归并键改为 `(factorKey, canonical(params))`（**不含 `direction`**，避免同一候选在不同标的上 `auto` 解析出相反方向而被拆成两键、甚至 topN 同因子一多一空）。每候选的**方向统一**取自**焦点标的**的解析结果（焦点缺该候选则回落聚合 IR 符号）；组分 = 各标的 IR **非空按 coverage 加权均值**（全空丢弃该候选），`rankIc`/`monotonicity` 同法，`coverage` 加权均值，`occurrences = Σ`，并回填统一 `direction`。按 `abs(组 IR)` 降序取 topN。
4. 落库：挖掘因子**与标的无关**（仅 factorKey+params），沿用 `_mined_factor_values` + `bulk_create_factors` 去重落库；`evaluated/persisted/skipped` 计数照旧透明。
5. `FactorMineReq` 新增 `symbols`；`FactorMineResult` 可选新增 `symbols?`（回显聚合标的）。

### 3.5 训练 / 权重模型多标的池化（核心）

**硬约束**：绝不修改 `train_weights` / `build_train_result` / `fit_at_fraction` / `prepare_curve_sample`（`aragon-weight-model-workspace` 等价性门禁）。多标的能力以**并行新增函数**实现，单标的（len==1）**继续走原路径**、字节级不变。

`engine/factors/training.py` **新增**：

```python
def build_pooled_sample(parts: list[tuple[np.ndarray, np.ndarray]], config: dict) -> TrainSample:
    """把逐标的 (matrix, label) 池化为单 TrainSample：各自 _aligned 去 NaN → 各自 _train_cut 时序切分 →
    拼「全体训练块 || 全体验证块」，cut = Σ 各标的训练段长。空/不足标的跳过（调用方记录，绝不静默）。
    池化后对齐样本 < MIN_TRAIN_SAMPLES → InsufficientSamplesError。
    单标的输入等价于 prepare_curve_sample（train 块=aligned[:cut]、valid 块=aligned[cut:]，vstack 复原、cut 相同）。"""

def train_weights_on_sample(sample: TrainSample, names, *, method, config, norm_stats) -> TrainResult:
    """在给定 TrainSample 上拟合（backend.fit(sample.matrix[:cut], sample.label[:cut]) → _normalize_weights →
    build_train_result）。复用 build_train_result（不改），供池化末步与曲线共用。"""
```

- **无未来泄漏**：每个标的的验证段严格晚于其自身 `cut`；池化验证块 = 各标的留出段之并集；任意 fraction 的训练前缀只取自训练块，绝不含任何标的的留出段。
- **曲线语义**：`fit_at_fraction(sample.matrix, sample.label, cut=sample.cut, frac=…)` 的 `matrix[:int(frac*cut)]` 为池化训练块前缀、`matrix[cut:]` 恒为池化留出块——扩样本曲线随池化训练行增长（按标的块顺序拼接，文档标注）。

`weight_train_manager.py`：

- `JobSpec` 新增 `symbols: list[str]`（`window` 仍承载焦点标的与区间）。
- `_TrainJob._run`：`loading` 阶段**逐标的** `_load_df`（各标的一个 `FactorWindow`，仅换 inst_id）；取数间检查取消（扩展 H-B2）。**多标的进度粒度（C-8）**：`loading` 现仅发一次 `@5%`，≤20 标的顺序取数期间 UI 会疑似「卡在 5%」——改为在 `[5%, 25%]` 区间按已取标的数**线性推进**（`5 + 20*(i+1)/len(symbols)`，`_emit_phase("loading", …)`），逐标的可见进度。`building` 阶段逐标的 `build_factor_matrix` + `forward_return_label`。
  - `len(symbols)==1` → **原路径**：单 matrix → `prepare_curve_sample` + 逐 fraction `fit_at_fraction` + 末步 `train_weights`（等价门禁保持）。
  - `len(symbols)>1` → **池化路径**：`build_pooled_sample(parts)` → 逐 fraction `fit_at_fraction`（同曲线事件）→ 末步 `train_weights_on_sample`。
- `_finalize_success` 的 `provenance` 新增 `"symbols": spec.symbols`（写入 `provenance_json`，`weight_train_run_out` 已透传）。

`factors/weight_model_service.py`：

- `WeightTrainStartReq` / `WeightModelCreateReq` / `WeightModelUpdateReq` 新增可选 `symbols: list[str]`、`symbolGroupId: str | None`。
- `start_training` 守门序（R-04）保持并扩展：`effective_symbols = req.symbols or [req.instId]`；新增 **④.5 标的数护栏**——去重去空后 `len > settings.max_train_symbols`（默认 20）→ 400 `FACTOR_TRAIN_TOO_MANY_SYMBOLS`；**逐标的**沿用 `_guard_range`（每标的区间 ≤ `factor_train_max_bars`）。焦点 `inst_id = effective_symbols[0]`。
- `_resolve_model`：`WeightModelSpec.config` 注入 `{"symbols": effective_symbols, "symbolGroupId": req.symbolGroupId, ...原 config}`；`inst_id = focus`。**零迁移**——symbols 落 `config_json`。
- `JobSpec(symbols=effective_symbols, window=FactorWindow(inst_id=focus, …), …)`。
- `weight_model_out` 新增派生 `"symbols": config.get("symbols") or [row.inst_id]`、`"symbolGroupId": config.get("symbolGroupId")`（纯 serializer，无 DB 改）。
- 一键回测 `backtest_run`：**保持单标的**（在 `run.inst_id`=焦点上回测；跨标的组合权益超出本方案范围）。文档标注：组训练模型的回测在焦点标的上进行。

`factors/service.py` 同步 `train`（供 dead 同步端点 + 池化单测）：新增可选 `symbols`，多标的时 `build_pooled_sample` + `train_weights_on_sample`；单标的字节不变。

前端训练管线（lockstep）：`WeightTrainTarget` 增 `symbols: string[]`、`symbolGroupId?`；`buildTrainTarget`（`lib/weightModel.ts`）由 `wb.symbols` 填 `symbols`；`trainStartBody` 发 `symbols` + `symbolGroupId`；`WeightTrainStartBody` / `WeightModelCreateBody`（`lib/weightModelApi.ts`）增 `symbols?` / `symbolGroupId?`；`WeightModel` / `WeightTrainRun` / `TrainProvenance` TS 类型增可选 `symbols?`（缺省安全）。`WeightModelList` / `WeightModelDetail` / `WeightTrainLive` 头部由「`instId · bar`」升级为「`焦点标的 +N`（组名）· bar」样式展示组信息。

---

## 四、接口设计（Interface Design）

**REST（均经 `/api/py/*` 代理，错误体 `{error:{code,message}}`，camelCase）**：

| 端点 | 变更 | 入参新增 | 出参新增 |
|---|---|---|---|
| `POST /factors/evaluate` | 兼容扩展 + 标的数 400 护栏（C-1） | `symbols?: string[]` | `symbols?`、`perSymbol?`、`aggregated?`、`direction`（冻结后） |
| `POST /factors/mine` | 兼容扩展 + 413 计入标的数 | `symbols?: string[]` | `symbols?` |
| `POST /weight-models/train` | 兼容扩展 | `symbols?: string[]`、`symbolGroupId?: string` | 出参不变 `{jobId,runId,modelId}`；run.provenance 含 `symbols` |
| `POST /weight-models` `PUT /weight-models/{id}` | 兼容扩展 | `symbols?`、`symbolGroupId?` | `weight_model_out` 增派生 `symbols`、`symbolGroupId` |
| `GET /weight-models[...]` `.../runs[...]` | 出参增派生 | — | model.`symbols`；run.provenance.`symbols` |
| `GET/PUT /workspace/watchlist` | 兼容扩展 | `symbolGroups: SymbolGroup[]` | `symbolGroups` |
| `POST /factors/compute`、`.../score`、`.../score-preview` | **不变** | — | 单标的（焦点）语义保持 |

**新增错误码**：`FACTOR_TRAIN_TOO_MANY_SYMBOLS`（400，训练标的数超 `max_train_symbols`）、`FACTOR_EVAL_TOO_MANY_SYMBOLS`（400，评估标的数超 `max_eval_symbols`，C-1）。沿用 `FACTOR_TRAIN_TOO_LARGE`（413，逐标的区间过长）、`FACTOR_MINE_TOO_LARGE`（413，含标的数的工作量）。三者均在**取数前**判定，保证扇出受控、绝不静默降级。

**WebSocket `/api/ws/weight-train/{job_id}`**：事件联合类型不变；`result` / run 记录的 `provenance` 内多一个 `symbols` 字段（前端缺省安全）。

**请求/响应形状（关键）**：

```jsonc
// POST /factors/evaluate（组）
{ "instId":"BTC-USDT-SWAP", "symbols":["BTC-USDT-SWAP","ETH-USDT-SWAP"],
  "bar":"1H", "startTs":1710000000, "endTs":1712592000,
  "factorKey":"mom_roc", "direction":"auto", "horizons":[1,3,6,12] }
// 响应：既有 FactorEvalResult + { "symbols":[...], "aggregated":true,
//   "perSymbol":[{"instId":"BTC-USDT-SWAP","occurrences":700,"primaryRankIc":0.04,"coverage":0.98}, ...] }

// PUT /workspace/watchlist（新增段）
{ "watchlist":[...], "autoTradeMembers":[...], "autoTradeAllocationMode":"even",
  "symbolGroups":[{ "id":"g_a1b2", "name":"主流币", "symbols":["BTC-USDT-SWAP","ETH-USDT-SWAP"],
                    "createdTs":1712000000, "updatedTs":1712000000 }] }
```

**Store 契约（前端）**：`symbolGroups: SymbolGroup[]`（persist + backend-hydrate）；`factorLabTarget: FactorTargetRef | null`（persist）；`addSymbolGroup/updateSymbolGroup/removeSymbolGroup`；`setTarget`。

---

## 五、数据模型（Data Model）

**零 DB 迁移**（`create_all` 无列迁移）——所有新增数据写入既有结构：

| 存储位置 | 字段 | 说明 |
|---|---|---|
| `AppSetting["workspace.watchlist_prefs"]` (JSON) | `symbolGroups: [{id,name,symbols[],createdTs,updatedTs}]` | 标的组唯一权威；sanitizer 规整。 |
| `WeightModel.config_json` (既有 Text) | `symbols: string[]`、`symbolGroupId?: string` | 组训练模型的组成员与来源组；`inst_id` 列 = 焦点。 |
| `WeightTrainRun.provenance_json` (既有 Text) | `symbols: string[]` | 该次训练所用组成员，随 run 溯源。 |
| `FactorSet.model_json.provenance` (既有 Text) | `symbols?: string[]` | 回流保存组合时随权重 provenance 透传（仅展示，不参与打分）。 |

**前端类型新增**：`SymbolGroup`、`FactorTarget`、`FactorTargetRef`（`lib/factorTypes.ts`）；`WorkspacePrefs.symbolGroups`；`WeightModel.symbols` / `WeightTrainRun`（provenance）/ `TrainProvenance.symbols?` / `WeightTrainTarget.symbols` 等可选扩展。

**上限常量**（后端 `config.py` pydantic `Settings` / repositories 模块常量）：`max_train_symbols=20`、`max_eval_symbols=30`（C-1，评估扇出上限）、`_SYMBOL_GROUPS_MAX=50`、`_GROUP_SYMBOLS_MAX=100`。`config.py` 为 `class Settings(BaseSettings)`（pydantic-settings），新增字段以 `Field(default=…, ge=1)` 一行声明即可，与既有 `factor_train_max_bars` / `max_weight_train_jobs` 同风格。

---

## 六、UI / UX 设计（横栏按钮 + 弹窗面板 + 选择器）

1. **横栏最右侧「管理标的组」按钮**：在 `FactorLab.tsx` 工作台横栏（标的 / 周期 / 起始 / 结束 行）**最右端**加 `Button variant="ghost" size="sm"`（图标 `Layers` + 文案「管理标的组」，`aria-haspopup="dialog"`）。点击 `setGroupModalOpen(true)`。自选为空时按钮禁用并提示「请先在交易工作台添加自选标的」。
2. **目标选择器（单标的 / 标的组统一）**：把原「标的」`<Select>` 升级为带两个 `<optgroup>` 的原生 select——「标的组」组（每项 `组名（N标的）`，value=`group:<id>`）、「单标的」组（每个 watchlist instId，value=`single:<instId>`）。`onChange` 解析 value → `resolveFactorTarget` → `setTarget`。选中组时选择器旁显示一枚 `Badge`「N 标的」。
3. **标的组弹窗面板 `SymbolGroupModal.tsx`（新增，增删改查）**：复用 `Modal`（**实测为 `components/ui/index.tsx` 导出，非 `components/ui/Modal.tsx` 独立文件**；`import { Modal } from "@/components/ui"`，已内置聚焦陷阱 + Esc + portal + 滚动锁 + 还焦，镜像 `Settings/SettingsPanel.tsx`）。左栏：标的组列表（组名 + 标的数 + 「编辑 / 删除」，删除走 `components/ui/ConfirmDialog.tsx`）+「新建标的组」。右栏：组编辑器——名称输入 + **成员挑选器**。
   > **成员挑选器实现更正（C-4）**：**不复用 `MemberPalette`**——`MemberPalette` 的候选来源是**因子库**（「库目录 / 我的因子」，`factorApi.list`），与本处所需的「自选标的」无关，直接镜像会误导。此处**新增一个轻量的自选标的 chip 挑选器**：仅**复用其 click-to-toggle chip + 搜索的交互范式**（`aria-pressed` 切换 + ✓ 角标 + 关键词过滤），但候选来源为 `store.watchlist`（口径参照自动交易成员挑选，即 watchlist 内挑选并成组）。**成员有序**（决定焦点/锚点，见 C-9），支持拖拽或上移置顶。已在组但已移出自选的标的以灰显 chip + 「已移出自选」角标呈现，可移除不可再加（呼应 §3.2「不强制 ⊆ watchlist」决策）。

   底部保存 / 取消。所有变更即时落 `store.symbolGroups`（去抖写穿后端 + localStorage）。空态：无自选→引导去交易工作台添加；无标的组→「新建你的第一个标的组」。
4. **评估 / 挖掘结果的组态呈现**：聚合态在结果区顶部加「组内均值 · N 标的」`Badge` + 可展开「逐标的贡献」抽屉（`perSymbol` 表：instId / 配对样本 / 主 RankIC / 覆盖）；单标的态与现状一致。
5. **权重模型 / 训练头部**：`WeightTrainLive` / `WeightModelList` / `WeightModelDetail` 由「`instId · bar · N 成员`」升级为「`焦点标的 +K`（组名）· bar · N 成员」，`title` 悬浮显示全部组成员。

设计基调：延续 Factor Lab 现有 `--accent-soft`/`--surface-2`/圆角 token 与紧凑排版；键盘可达、`aria-current`/`aria-label` 完整；聚合「均值」措辞明确不误导（区别于池化 IC）。

---

## 七、文件 / 模块变更计划（Change Plan）

| 文件 | 新建/改 | 一句话意图 |
|---|---|---|
| `apps/web/lib/factorTypes.ts` | 改 | 新增 `SymbolGroup` / `FactorTarget` / `FactorTargetRef`；`FactorEvalResult` 增可选 `symbols`/`perSymbol`/`aggregated`；`TrainProvenance` 增可选 `symbols`。 |
| `apps/web/lib/factor.ts` | 改 | 新增 `resolveFactorTarget(ref,watchlist,groups)` 纯函数（类比 `resolveWatchlistInstId`）。 |
| `apps/web/lib/workspaceTypes.ts` | 改 | `WorkspacePrefs`/`WorkspaceWatchlistIn/Out` 增 `symbolGroups`。 |
| `apps/web/lib/useWorkspaceSync.ts` | 改 | `WorkspaceSlice`/`sliceOf`/`sliceChanged`/`hydrateWorkspace` 纳入 `symbolGroups`。 |
| `apps/web/lib/store.ts` | 改 | 新增 `symbolGroups` + CRUD setter、`factorLabTarget` + `setTarget`；partialize/hydrateWorkspace/persist version→24 + migrate。 |
| `apps/web/components/Factor/FactorLab.tsx` | 改 | 工作台条：目标选择器（optgroup）+ 最右「管理标的组」按钮；`Workbench` 增 `target`/`symbols`/`setTarget`；挂载 seed + 重协调。 |
| `apps/web/components/Factor/SymbolGroupModal.tsx` | 新建 | 标的组增删改查弹窗；`import { Modal } from "@/components/ui"` + `ConfirmDialog`；成员挑选器为**新增自选标的 chip 挑选器**（候选 = `store.watchlist`，仅复用 chip-toggle 交互范式，**不复用 `MemberPalette`**，C-4）。 |
| `apps/web/components/Factor/FactorEvalPanel.tsx` | 改 | `range` 带 `symbols: wb.symbols`；聚合态「组内均值」Badge + 逐标的抽屉。 |
| `apps/web/components/Factor/FactorMiningPanel.tsx` | 改 | `range` 带 `symbols: wb.symbols`；组态提示。 |
| `apps/web/lib/factorApi.ts` | 改 | `evaluate`/`mine` 入参增 `symbols?`。 |
| `apps/web/lib/weightModelApi.ts` | 改 | `train`/`create`/`update` body 增 `symbols?`/`symbolGroupId?`。 |
| `apps/web/lib/weightModel.ts` | 改 | `buildTrainTarget`/`trainStartBody` 贯通 `symbols`/`symbolGroupId`；`modelToTrainTarget` 复用 `model.symbols`。 |
| `apps/web/lib/weightModelTypes.ts` | 改 | `WeightTrainTarget`/`WeightModel`/`WeightTrainRun` 增可选 `symbols`/`symbolGroupId`。 |
| `apps/web/components/Factor/WeightTrainLive.tsx`、`WeightModelList.tsx`、`WeightModelDetail.tsx`、`WeightModelEditor.tsx` | 改 | 头部展示组信息；建模从 `wb.symbols` 取组。 |
| `services/quant/app/schemas.py` | 改 | `FactorEvaluateReq`/`FactorMineReq`/`WeightTrainStartReq`/`WeightModelCreate/UpdateReq` 增 `symbols?`/`symbolGroupId?`；`WorkspaceWatchlistIn/Out` 增 `symbolGroups`；`weight_model_out` 派生 `symbols`。 |
| `services/quant/app/api/factors.py` | 改 | `evaluate`/`mine` 透传 `symbols` 给 service。 |
| `services/quant/app/api/workspace.py` | 改 | GET/PUT 透传 `symbolGroups`。 |
| `services/quant/app/db/repositories.py` | 改 | `_sanitize_workspace_watchlist` 规整 `symbolGroups`；`set/get_workspace_watchlist` 带该字段。 |
| `services/quant/app/factors/service.py` | 改 | `evaluate`/`mine`/`train` 增 keyword `symbols`（多标的按 `replace(window,inst_id=…)` 逐标的取数 + 聚合 / 池化）；`evaluate` 加 `max_eval_symbols` 400 护栏（C-1）+ 方向冻结（C-2）；`_guard_mine_workload`（本文件）计入标的数（C-3 归并键去 direction）。 |
| `services/quant/app/factors/weight_model_service.py` | 改 | `start_training` 标的数护栏 + 焦点 inst + `config.symbols`；`_resolve_model` 注入组；`weight_model_out` 派生。 |
| `services/quant/app/factors/weight_train_manager.py` | 改 | `JobSpec.symbols`；`_run` 逐标的取数 + 单/多标的分支（池化）；provenance 增 `symbols`。 |
| `services/quant/app/engine/factors/evaluation.py` | 改 | 新增纯函数 `aggregate_eval_results`（不动 `evaluate_factor*`）。 |
| `services/quant/app/engine/factors/mining.py` | 改 | 新增纯函数 `aggregate_mine_results`。 |
| `services/quant/app/engine/factors/training.py` | 改 | **仅新增** `build_pooled_sample` / `train_weights_on_sample`（绝不改 `train_weights`/`build_train_result`/`fit_at_fraction`）。 |
| `services/quant/app/config.py` | 改 | `Settings(BaseSettings)` 新增 `max_train_symbols`（默认 20）、`max_eval_symbols`（默认 30，C-1），`Field(default=…, ge=1)`。 |
| 测试（见 §八） | 新建/改 | 前后端单测与集成测试。 |

---

## 八、测试与验收标准（Testing & Acceptance）

**后端 pytest（`services/quant/tests/`，遵守 `aragon-quant-test-isolation` 共享 DB 清缓存约定）**：

1. `test_workspace_api`：PUT/GET 往返含 `symbolGroups`；sanitizer 去重 / 截断 / 丢空组 / 组数与组容量上限；越界值夹取（类型错 422、越界不 422）。
2. `test_factors_api`：`evaluate` 传 2 标的→响应含 `aggregated/symbols/perSymbol`，聚合值为 occurrences 加权均值（构造两标的可验证的确定性数据）；单标的响应与旧版**逐字段不变**（回归护栏）。**方向冻结（C-2）**：构造一标的主 horizon RankIC 为负、另一为正的 `auto` 场景，断言两标的以焦点标的**同一冻结方向**评估、响应 `direction` 一致（避免符号相反的均值）。**评估扇出护栏（C-1）**：`len(symbols) > max_eval_symbols` → 400 `FACTOR_EVAL_TOO_MANY_SYMBOLS`。`mine` 多标的→413 工作量计入标的数（取数前判定）；**归并键去 direction（C-3）**：构造同一候选在两标的 `auto` 解析相反方向，断言仍归并为**一条**且回填统一方向；聚合排名按 abs(组 IR)。
3. `test_weight_models_api`：多标的 `train` → run.provenance.symbols == 组；model.config.symbols 落库 + `weight_model_out` 派生 `symbols`；`len(symbols) > max_train_symbols` → 400 `FACTOR_TRAIN_TOO_MANY_SYMBOLS`；逐标的区间过长 → 413。
4. `test_training_pooled`（新）：`build_pooled_sample([single])` 与 `prepare_curve_sample` **字节级相等**；两标的池化 cut == Σ 各 cut、验证块 == 各留出并集；池化不足 → `InsufficientSamplesError`。
5. **等价性硬门禁不破**：`test_streaming_result_equals_sync_train`（单标的）继续通过——多标的走并行新函数，`train_weights`/`build_train_result` 零改动。

**前端 vitest（`apps/web`，组件测试须 `NODE_ENV=test`，见 `aragon-web-vitest-node-env`）**：

6. `resolveFactorTarget`：组已删 / 组内标的失效回落焦点；single 夹取。
7. `store` / `useWorkspaceSync`：`symbolGroups` CRUD 不可变、`sliceChanged` 命中、`hydrateWorkspace` 覆盖、写穿仅在切片变更时触发；persist migrate 回填默认。
8. `SymbolGroupModal`：创建 / 改名 / 增删成员 / 删除组；自选为空空态；已移出自选成员灰显。
9. `FactorLab`：选组后 `wb.symbols` 为组成员、`wb.instId` 为焦点；选单标的回退单值。

**验收标准（Definition of Done for the feature）**：

- 横栏最右侧「管理标的组」按钮可弹出面板，完成标的组增 / 删 / 改 / 查；成员仅来自自选标的；关闭 / 重开桌面端后标的组仍在（后端权威）。
- 横栏可选中标的组；选组后运行「因子评估」「因子挖掘」「权重训练」时，后端**分别取出组内各标的真实数据**并聚合 / 池化，结果标注「组内均值 · N 标的」且可见逐标的贡献。
- 单标的路径行为**完全不变**；权重训练等价性门禁通过；`services/quant` pytest 与 `apps/web` vitest 全绿；`pnpm -C apps/web build` 与后端启动无误。

---

## 九、风险与缓解（Risks & Mitigations）

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R1 | 误改 `train_weights`/`build_train_result` 破坏 `test_streaming_result_equals_sync_train` | 权重回归、老策略打分漂移 | **仅新增** `build_pooled_sample`/`train_weights_on_sample`；单标的走原路径；门禁测试保留并在 CI 前本地跑。 |
| R2 | 多标的取数放大 OKX 请求与训练时长 → socket / 事件循环压力 | 429 / 训练超时 / 卡 running | 训练侧逐标的 `_guard_range` + `max_train_symbols`（20）上限；**评估侧 `max_eval_symbols`（30）上限、挖掘侧 413 取数前判定**（C-1）；复用进程内 K 线缓存（`_candle_cache`，实测 **cap=16 / TTL 90s**——**注**：当组标的数超 16 时 LRU 逐出会削弱缓存复用、评估/训练可能重取，属可接受的规模权衡）；取数在线程池外 await、CPU 在 `run_in_threadpool`；作业与 socket 解耦（既有）保证断连续跑。 |
| R3 | persist `version` 升级 migrate 遗漏回填 → 老缓存缺 `symbolGroups`/`factorLabTarget` 崩溃 | 前端白屏 | additive migrate 分支显式回填默认；`resolveFactorTarget` 对缺失指针回落焦点；类型侧全部 optional 缺省安全。 |
| R4 | 标的组不强制 `⊆ watchlist` 与 `autoTradeMembers` 口径不一致，易被误当 bug | 评审/维护困惑 | 在 sanitizer 与本 spec 显式记录决策理由（避免静默掏空）；UI 灰显已移出自选成员使状态可见。 |
| R5 | 聚合「均值 IC」被误读为池化统计 | 结论误导 | UI 明确标注「组内均值」并给逐标的 `perSymbol` 抽屉；spec 记录备选池化方案及否决理由。 |
| R6 | 一键回测对「组模型」语义模糊（在焦点标的回测）；焦点=组内首个既定图表/建模默认又是聚合方向锚点（C-9） | 用户预期偏差 | 回测明确在**焦点标的**进行并在 UI/文档标注；焦点**确定性取组内首成员且为方向锚点**（已在 §3.1 声明），组编辑器成员有序、可置顶选定锚点；跨标的组合权益列为后续里程碑。 |
| R7 | 扩样本曲线在多标的下按「标的块顺序」拼接、非严格时间序 | 学习曲线解读弱化 | 文档标注；验证块恒为各留出并集、无未来泄漏（核心正确性不受影响）。 |
| R8 | DB 误加新列触发 SQLite 无迁移导致老库启动缺列 | 桌面端崩溃 | **零迁移**——一律写既有 JSON 列（`config_json`/`provenance_json`/KV），评审清单核对无 `mapped_column` 新增。 |

---

## 十、关键不变量守护清单（供实现与评审逐条核对）

1. `train_weights` / `build_train_result` / `fit_at_fraction` / `prepare_curve_sample` **零改动**；多标的仅经新增函数。
2. 单标的（`len(symbols)==1`）在评估 / 挖掘 / 训练三处**行为字节级不变**（回归测试守护）。
3. 归一化契约仍单源 `resolve_norm_contract`；池化前每标的各自 `build_factor_matrix` 做**因果 z-score**（绝不跨标的拼原始 K 线再归一，杜绝跨标的泄漏）。
4. DB **零列迁移**；新增数据入既有 JSON / KV。
5. 取数一律真实 OKX（逐标的 `_load_df`）；空区间 400、超限 413/400 如实报错，绝不假数据、绝不静默降级。
6. 标的组持久化复用单一 `/workspace/watchlist` 端点 + `useWorkspaceSync` 单水合 / 单写穿；sanitizer 为唯一规整点。
7. 聚合 / 池化对空样本走安全口径（null 而非 0）；逐标的贡献可见（`perSymbol`），绝不静默丢弃标的。
8. **跨标的方向一致性（C-2/C-3）**：评估/挖掘聚合前，`auto` 方向仅在焦点标的解析一次并**冻结下发**；挖掘归并键**不含 direction**；绝不把符号相反的逐标的指标混入同一均值/同一候选。
9. **扇出上限（C-1）**：评估 `max_eval_symbols`、训练 `max_train_symbols`、挖掘 413 均**在取数前**判定；绝不无界扇出 OKX 请求。

---

## 评审结论（Review Verdict · Subtask #1）

**结论：有条件通过（Approved with Conditions）。**

**依据**：本方案与仓库当前真实代码经逐节核验**高度吻合**，架构选型（复用 `workspace.watchlist_prefs` KV + `useWorkspaceSync` 水合/写穿、零 DB 列迁移、等价性门禁下并行新增池化函数、`normStats` 仅契约参数因而池化无歧义）**可行、右尺寸、与既有约定一致**。**无 P0**。评审发现的 **4 项 P1（C-1 评估扇出护栏 / C-2 评估方向冻结 / C-3 挖掘方向归并 / C-4 成员挑选器数据源）已全部在本文档 v2 就地修复**；文档准确性类 P2（C-5 persist 版本、C-6 Modal 路径、C-7 service 签名）亦已订正。

**放行条件（由 Subtask #2 代码开发承接，须在实现与自测中逐条落实）**：

1. **等价性门禁（最高优先级）**：单标的路径必须字节级不变；实现后本地必跑 `test_streaming_result_equals_sync_train` 与 `test_training_pooled`（含 `build_pooled_sample([single]) == prepare_curve_sample` 断言）通过，`train_weights`/`build_train_result`/`fit_at_fraction`/`prepare_curve_sample` **零改动**（`git diff` 核对）。
2. **方向一致性（C-2/C-3）**：评估与挖掘的聚合必须在**同一冻结方向**下进行；补齐 §八 中「符号相反」的确定性用例，防止统计不自洽回归。
3. **扇出护栏（C-1）**：`max_eval_symbols` 400 护栏在取数前判定并有测试覆盖；确认挖掘 413 亦在 `_load_df` 之前。
4. **C-8 进度粒度**：多标的 `loading` 阶段按标的增量推进（可作为体验项，不阻断）；若实现从简，须在 UI 明示「正在取 N 个标的」以免误判卡死。
5. **不越界**：严格按 §七「文件 / 模块变更计划」落地，不引入超出方案的重构；DB 零列迁移（核对无新增 `mapped_column`）。

满足上述条件即视为达成本特性的 Definition of Done。

---

*（本文档 v2：Subtask #1 已逐节评审并就地修复全部 P0/P1；v1 为 Subtask #0 方案设计产物，字数已超 800 字下限门槛。）*
