# 因子组合「组合成员」快速多选重设计 — spec v2

> 需求（用户原话）：因子实验室的「权重训练与组合」右侧面板，整体使用逻辑不合理。上方的**组合成员**
> 需要在右边的下拉框里**一个一个选择再点添加**，太不方便、太慢。需要调整这块的界面布局与使用逻辑，
> 让用户**更方便快捷**地完成组合成员的拼装；**美观优雅、顶级设计、符合人机交互最佳实践**。
>
> 本文档由 **Subtask #0（方案设计）** 产出：**仅写设计，不写实现、不提交**。
> Subtask #1（评审）已在顶部追加「§0 评审记录」、文末追加「评审结论」并升版至 **v2**（P0/P1 已就地修复）；
> Subtask #2（开发）按 §4 文件清单逐项落码，并把实施中发现的方案缺陷回填 §10。

- **版本**：v2（已评审；P0/P1 已就地修复，详见「§0 评审记录」与文末「评审结论」）
- **特性目录名（feature slug）**：`factor-combo-member-quick-pick`
- **作者**：Subtask #0（solution architect）
- **基线 commit**：`0c2904b`（feat: add multi-sample backtest）
- **影响范围**：纯前端（`apps/web`）；**零后端改动、零接口改动、零数据库改动**。
- **关联前序方案**：`docs/plans/factor-layer/spec.md`、`docs/plans/factor-layer-hardening/spec.md`、
  `docs/plans/factor-layer-lifecycle/spec.md`、`docs/plans/factor-layer-reliability/spec.md`。

---

## 0. 评审记录（Review Notes · Subtask #1）

> 评审人：Subtask #1（Anthropic 资深评审）。评审基线 commit `0c2904b`，已对照
> `apps/web/components/Factor/FactorSetBuilder.tsx`（实测 507 行，方案行号引用属实）、
> `apps/web/lib/factor.ts`、`apps/web/lib/factorApi.ts`、`apps/web/lib/factorTypes.ts`
> 与 `CLAUDE.md` 阈值**逐节核验**。维度：可行性 / 完整性 / 一致性 / 体量适配。
> **结论：无 P0；P1 已就地修复（见各节「评审 R-x」标注），P2 已采纳或转为文末「评审结论」中的条件。**

| 编号 | 维度 | 严重度 | 问题 | 处置 |
|---|---|---|---|---|
| R1 | 完整性/正确性 | **P1** | 候选「已选态」用含 `direction` 的 `memberIdentity` 判定，而篮子内方向**可翻转**。用户翻转某成员方向后，其候选卡的 `aria-pressed`/勾选会失配（显示为未选），再点该卡会以默认方向追加一个**同 key 同 params 仅方向不同的近重复成员**——破坏 AC-1「选中态实时回显」并产生隐性重复（训练时会被当成两个成员）。 | **已就地修复**：调色板的「已选回显 / 单击切换 / 批量去重」一律改用**候选身份 `candidateIdentity`＝factorKey+规范化 params（忽略 direction）**；direction 仅作加入时默认值，翻转不改候选已选态。见 §3.3 / §3.5 / §5.1 / §5.2 / §8 / §9-R8。 |
| R2 | 可行性/正确性 | **P1** | 「我的因子」服务端过滤复用 `factorApi.list({enabled,q,category,sort})` 已核实可行（factorApi.ts:57–72），但方案未规定 TanStack Query 的 `queryKey` 组成；若沿用现有固定键 `["factor","list",{enabled:true}]`，q/category 变化时会命中旧缓存、不重新拉取，导致候选列表陈旧。 | **已就地修复**：§3.3 明确 `queryKey` 必须包含全部入参 `{enabled,q,category,sort}`，q 经 300ms 去抖后再进 key。见 §9-R9。 |
| R3 | 完整性 | P2 | 「我的因子」候选卡 `RankIC` 只写「来自 `factor.eval`」，未给取值口径。 | 已就地修复：§3.2 明确复用既有 `FactorTable` 口径 `factor.eval ? primaryRankIc(factor.eval.horizons) : null`，并沿用 `factor.eval == null` 时「请重新评估」title。 |
| R4 | 一致性 | P2 | §3.2 称「左导航占 15%」与现状不符（NavRail 已收窄为固定 `w-12`≈48px）。 | 已就地修复：改为「左侧固定窄导航（约 48px）」的客观表述，不再断言百分比。 |
| R5 | 完整性 | P2（条件） | 父层 `notice` 为单槽、无自动消失：上限提示与「已保存」提示会相互覆盖。 | 可接受（沿用现状单 notice 范式）；列为「评审结论」实现期注意项，不阻断。 |
| R6 | 一致性 | P2（条件） | 删除 `MemberPicker` 等后，`FactorSetBuilder.tsx` 中 `directionLabel` 会成为未使用 import（实测当前已未使用）。 | 列为实现期清理项（`tsc`/lint 会提示），见「评审结论」。 |

**体量适配**：纯前端、零后端/接口/DB、复用既有纯函数与设计 token、选择 UI 抽独立组件守 800 行阈值——评定为「右尺寸」，无过度设计。
**一致性**：P1 修复后与 `memberIdentity`/后端 `_factor_dedupe_key` 去重契约、H1/H2 归一化锁定、H3 训练新鲜度门禁等下游契约均保持一致。

---

## 1. 概述（Overview）

因子实验室（`FactorLab`）的第五个工作区「权重训练与组合」由 `FactorSetBuilder.tsx` 实现，其顶部
「组合成员」区域当前的拼装方式是**两步串行的单条录入**：用户先在一个 `<Select>` 下拉框里逐个滚动
寻找并选中某个因子，再点旁边的「添加」按钮把它追加进成员列表；要凑齐一个 4–8 成员的组合，就得把
「展开下拉 → 滚动找项 → 选中 → 点添加」这套动作**重复四到八遍**。下拉框一次只显示一项焦点、看不到
全貌、无法搜索、无法批量、也无法一眼看出「哪些已经选过」，是典型的低带宽录入控件用错了场景。当
「我的因子」里堆积了大量挖掘变体（同 `factorKey` 不同参数、命名高度相似）时，逐条下拉尤其缓慢且
容易选错。这正是用户反馈「太不方便、太慢」的根因。

本方案把「组合成员」从**下拉单选 + 添加按钮**改造为一个**候选调色板（Member Palette）+ 已选篮子
（Selected Basket）的双栏多选界面**：左栏把所有可选因子平铺成可搜索、可按类别过滤、可点击切换的
**卡片网格**——点一下即选入、再点一下即移除，选中态以强调色描边 + 勾选角标实时回显，并提供「全选
当前结果 / 清空已选」批量操作；右栏是实时联动的已选成员清单，保留每个成员的看多/看空方向切换与单条
移除。整套交互把「凑齐 N 个成员」从 4N 次点击压到「搜索一次 + 点 N 张卡」甚至「点一次全选」，并且
全程所见即所得。

设计严格复用既有视觉与交互 token（`card` / `Badge` / `Button` / `Input` / 强调色变量 / 圆角节奏）
与既有纯函数约定（`apps/web/lib/factor.ts` + 同名 `factor.test.ts`），不引入任何新依赖、不触碰后端
与归一化契约，保证改动面**克制、可回归、与现有架构一致**。训练 / 阈值 / 保存组合 / 从组合新建策略等
下游逻辑（含训练新鲜度门禁 H3、归一化窗口锁定 H1/H2、成员数 ≤12 校验）**完全保持原样**，本次只重塑
「成员是如何被选进来的」这一段体验。

---

## 2. 现状与问题诊断（Current Behavior）

> 以下行号基于基线 `0c2904b`，已逐文件核对属实。

### 2.1 现状结构

- `apps/web/components/Factor/FactorSetBuilder.tsx`
  - 父组件持有 `const [members, setMembers] = useState<FactorMember[]>([])`（line 49）。
  - 渲染 `<MemberPicker>`（line 147–160），向其传入 `onAdd`（按 `memberIdentity` 去重追加，line 150–155）、
    `onRemove`（line 156）、`onDirection`（line 157–159）。
  - `MemberPicker`（line 352–426）头部有标题「组合成员（N）」+ 一个 **`库目录 / 我的因子` 段控**，右侧条件渲染
    `LibrarySourcePicker`（line 254–287）或 `MyFactorsSourcePicker`（line 291–350）。
  - 两个 SourcePicker 都是**「一个 `<Select>` + 一个『添加』按钮」**的单条录入器：选中下拉项后点「添加」才追加一条。
  - 下方是已选成员 `<ul>`（line 405–423），每行：中文名 + `factorKey` + 方向 `<Select>`（看多/看空）+「移除」按钮。
- 数据来源：
  - 库目录候选 = `factorApi.library()`（12 个内置因子，`FactorLibraryItem[]`）。
  - 我的因子候选 = `factorApi.list({ enabled: true })`（仅激活因子，`Factor[]`，可能包含大量挖掘变体）。
  - 跨工作区交接 = `wb.pendingMembers`（「因子评估 / 因子挖掘」点「加入组合」时塞入，`FactorSetBuilder` useEffect 合并，line 58–73）。

### 2.2 问题清单（Pain Points）

| # | 问题 | 后果 |
|---|---|---|
| P-1 | 单条下拉 + 添加，**两步 × N 次**才能凑齐组合 | 操作冗长、慢，正是用户主诉 |
| P-2 | `<Select>` 一次只露一项，**无全貌、无法搜索** | 「我的因子」量大时找项困难 |
| P-3 | 候选区与已选区割裂，**看不出哪些已选** | 易重复尝试、易选错变体 |
| P-4 | 无批量操作（全选 / 清空） | 想快速搭一个「全类别等权」组合也得逐条点 |
| P-5 | 库目录候选无类别/方向提示，**信息密度低** | 选择缺乏决策依据，需要回「因子目录」对照 |

---

## 3. 技术设计（Technical Design）

### 3.1 设计目标与交互模型

把「单条下拉录入」替换为**点击切换式多选（click-to-toggle multi-select）**，核心三条 HCI 原则：

1. **所见即所得**：候选与已选同屏；候选卡片的选中态实时回显（强调色描边 + 勾选角标 + `aria-pressed`）。
2. **一次点击完成一次增删**：点候选卡 = 选入；再点同一张 = 移除；点已选篮子里的「移除」= 移除。**消灭「添加」按钮这一步**。
3. **批量与过滤**：搜索框（去抖）+ 类别过滤 chips（复用挖掘面板既有样式）+「全选当前结果 / 清空已选」按钮，
   把「搭一个多成员组合」压缩到秒级。

「单一真相」仍是父组件的 `members: FactorMember[]`；候选卡是否「已选」由 `members` 经 `candidateIdentity`
派生（不另存一份选中态，杜绝两份状态漂移；候选身份不含 direction，见 §3.3 / §5.2 / 评审 R1）。

### 3.2 布局（Layout / Wireframe）

在内容区（左侧为固定窄导航 NavRail，约 48px=`w-12`，其余横向空间充裕；评审 R4）内，把「组合成员」卡升级为**双栏**（窄屏自动堆叠为单列）：

```
┌─ 组合成员（已选 5 / 上限 12）───────────────────────────── [清空已选] ┐
│ ┌── 候选调色板（≈ 60%）──────────────┐ ┌── 已选成员（≈ 40%）─────────┐ │
│ │ [库目录│我的因子]  🔍搜索…          │ │ ① 动量 ROC   mom_roc  [看多▾] ✕ │ │
│ │ 类别: (动量)(趋势)(反转)(波动)…清除 │ │ ② RSI 背离   rsi_div  [看空▾] ✕ │ │
│ │ ┌─────────┐┌─────────┐┌─────────┐ │ │ ③ 布林宽度   bb_width [看多▾] ✕ │ │
│ │ │✓动量ROC ││ EMA斜率 ││✓RSI背离 │ │ │ ④ …                          │ │
│ │ │mom_roc  ││ema_slope││rsi_div  │ │ │ ⑤ …                          │ │
│ │ │动量·看多││趋势·看多││反转·看空│ │ │                              │ │
│ │ └─────────┘└─────────┘└─────────┘ │ │ （空态：从左侧点选，或在评估/  │ │
│ │ … 可滚动网格 …                     │ │  挖掘中「加入组合」）          │ │
│ │ [全选当前结果(8)]  共 23 个候选     │ │                              │ │
│ └───────────────────────────────────┘ └──────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
```

- **候选卡（CandidateChip）**：圆角小卡，内含中文名（截断）、`factorKey`（等宽小字）、类别 Badge、
  方向 Badge（看多绿 / 看空锈）、`仅日内` Badge（如适用）、「我的因子」来源额外显示主 horizon `RankIC`
  （取值复用既有 `FactorTable` 口径 `factor.eval ? primaryRankIc(factor.eval.horizons) : null`，无则「—」，
  且 `factor.eval == null` 时沿用「参数/方向已变更，请重新评估」title；评审 R3）。
  选中态：`border-[var(--accent)]` + `bg-[var(--accent-soft)]` + 右上角 ✓；未选态：常规 `card` 描边 + hover 提亮。
- **已选成员行**：序号 + 中文名 + `factorKey` + 方向 `<Select>`（沿用现有看多/看空切换）+「移除」按钮（沿用现有 `Button variant="danger" size="sm"`）。

### 3.3 状态与数据流

- 父组件 `FactorSetBuilder` **维持** `members` 为单一真相；新增三个回调下传：
  - `onToggle(member)`：`members` 中按**候选身份 `candidateIdentity`（factorKey+规范化 params，忽略 direction，见 §5.2）**命中则移除该因子、否则以候选默认方向追加（追加受 ≤12 上限保护，见 §3.4；评审 R1）。
  - `onClear()`：清空 `members`（`setMembers([])`）。
  - `onAddMany(candidates)`：批量并入（去重 + 截断到上限），用于「全选当前结果」。
  - 既有 `onRemove(index)` / `onDirection(index, direction)` **保留**。
- 新组件 `MemberPalette` 内部本地态（`useState`，不入 store，刷新回默认，可接受）：
  - `source: "library" | "mine"`（段控）。
  - `searchInput` + 去抖 300ms 后的 `q`（复用 `FactorTable` 既有去抖范式）。
  - `category: string`（""=全部；点 chip 切换）。
- 候选来源：
  - `source==="library"`：`factorApi.library()`，**客户端**按 `q`/`category` 过滤（仅 12 条，开销可忽略）。
  - `source==="mine"`：`factorApi.list({ enabled: true, q, category, sort: "rankic" })`，**服务端**过滤（复用既有 query 能力，已核实 factorApi.ts:57–72 支持 q/category/sort）。
    TanStack Query 的 **queryKey 必须包含全部入参** `["factor", "list", { enabled: true, q, category, sort: "rankic" }]`，
    否则 q/category 变化时会命中旧缓存导致候选陈旧（评审 R2）；`q` 经 300ms 去抖后才进 key，避免逐键触发请求。
- 候选 → 成员的映射（沿用现有口径，**不改语义**）：
  - 库目录卡 → `{ factorKey: item.key, params: {}, weight: 0, direction: item.directionHint }`。
  - 我的因子卡 → `{ factorKey: f.factorKey, params: f.params, weight: 0, direction: freezeMemberDirection(f, library) }`。
- 「已选」判定（评审 R1）：`isCandidateSelected(members, candidateMember)` = `members.some(m => candidateIdentity(m) === candidateIdentity(candidateMember))`，
  其中 `candidateIdentity = factorKey | 规范化(params)`（**不含 direction**）。这样篮子内翻转方向不会让候选卡失配，也不会因方向不同而把同一因子重复加入。
  **注意职责区分**：跨工作区 `pendingMembers` 合并仍用含 direction 的 `memberIdentity`（沿用现状 line 58–73，允许评估/挖掘按需带入同因子的两个方向），与调色板候选去重互不冲突。
- 成员集合变化即作废训练结果（现有 `useEffect(() => setTrained(null), [members])`，line 76–78）**原样保留**，
  确保「成员变了必须重训」的 H3 契约不被新交互绕过。
- `wb.pendingMembers` 合并逻辑（line 58–73）**原样保留**：评估/挖掘的「加入组合」仍可用，新界面会即时回显这些成员为已选。

### 3.4 成员上限护栏（≤12）

`validateMembers` 规定组合最多 12 成员（`factor.ts:178–188`）。新交互能一次批量并入，必须在并入处兜住上限：

- `onToggle`：仅当该候选当前未选（按 `candidateIdentity` 判定）且 `members.length >= 12` 时，**拒绝追加**并触发一次 `notice`「最多 12 个因子成员」（不静默吞）。
- `onAddMany`：用纯函数 `addMembersCapped(members, candidates, 12)` 去重 + 截断；若发生截断，回传被截断条数供父组件 `notice` 提示「已达上限，部分候选未加入」。
- 候选卡在 `members` 已满且该卡未选时呈 `disabled`（降透明度 + `cursor-not-allowed` + `title` 说明），避免无效点击。

### 3.5 时序（Sequence）

```
用户进入「权重训练与组合」
  → MemberPalette 拉取 library（已缓存）/ mine（首拉）
  → 用户切 source / 输类别 / 搜索 → 候选网格即时过滤
  → 点候选卡 A：isCandidateSelected(A)? 按 candidateIdentity 移除A : (members<12? 追加A : notice上限)
        → setMembers → 候选卡 A 选中态翻转 + 已选篮子刷新 + trained 置空
  → 篮子内翻转 A 方向：A 的 candidateIdentity 不含 direction → 候选卡 A 仍显示为已选（评审 R1）
  → 点「全选当前结果」：onAddMany(可见候选) → addMembersCapped（按 candidateIdentity 去重 + 截断）→ setMembers(+notice if 截断)
  → 用户在已选篮子调方向 / 移除 → 沿用既有 onDirection / onRemove
  → 下游训练 / 阈值 / 保存 / 新建策略：完全不变
```

### 3.6 组件拆分与清洁度

当前 `FactorSetBuilder.tsx` 约 507 行；新 Palette 逻辑更重，若内联会逼近/突破 800 行单文件上限（CLAUDE §二）。
故**把成员选择 UI 抽到独立文件** `apps/web/components/Factor/MemberPalette.tsx`（含 `CandidateChip`、
`SelectedBasket` 两个子组件），`FactorSetBuilder.tsx` 仅保留状态与回调装配并 `import` 之。纯增删/判定逻辑下沉到
`apps/web/lib/factor.ts`（纯函数 + 单测），组件只消费。每个函数 ≤50 行、形参 ≤5、嵌套 ≤4，符合阈值。

---

## 4. 文件 / 模块变更计划（File / Module Change Plan）

| 文件 | 动作 | 一句话意图 |
|---|---|---|
| `apps/web/components/Factor/MemberPalette.tsx` | **新建** | 双栏「候选调色板 + 已选篮子」多选组件；导出默认 `MemberPalette`；内含 `CandidateChip`/`SelectedBasket`。 |
| `apps/web/components/Factor/FactorSetBuilder.tsx` | **修改** | 删除内联 `MemberPicker`/`LibrarySourcePicker`/`MyFactorsSourcePicker`/`myFactorOptionLabel`；改用 `<MemberPalette>` 并新增 `onToggle`/`onClear`/`onAddMany` 装配 + 上限 `notice`。 |
| `apps/web/lib/factor.ts` | **修改** | 新增纯函数：`candidateIdentity`、`isCandidateSelected`、`toggleMember`、`addMembersCapped`、`filterLibraryItems`、`libraryItemToMember`、`factorRowToMember`。 |
| `apps/web/lib/factor.test.ts` | **修改** | 为上述纯函数补正常路径 + 异常/边界路径（上限截断、去重、空集、同 key 不同参变体）单测。 |
| `apps/web/components/Factor/MemberPalette.test.tsx` | **新建** | RTL 组件测试：点卡切换选中态、搜索/类别过滤、全选截断到 12、清空、方向切换、空/错/满态。 |
| `docs/plans/factor-combo-member-quick-pick/spec.md` | **本文件** | 设计方案（Subtask #0 产出）。 |

> 说明：不新增第三方依赖；不改 `factorApi.ts`（沿用既有 `library()` / `list({enabled,q,category,sort})`）；
> 不改 `FactorLab.tsx` 的 Workbench 上下文与 `pendingMembers` 协议；不改任何后端文件与数据库 schema。

---

## 5. 接口设计（Interface Design）

> 无 REST / WebSocket / CLI 变更。以下为**前端组件 props 与纯函数签名**契约。

### 5.1 组件 props（`MemberPalette.tsx`）

```ts
interface MemberPaletteProps {
  /** 因子库目录（FactorLibraryItem[]，父层已 useQuery 拿到，避免子层重复请求）。 */
  library: FactorLibraryItem[];
  /** 当前已选成员（单一真相，来自 FactorSetBuilder.members）。 */
  members: FactorMember[];
  /** 切换某候选的入选/移除（命中 candidateIdentity 即移除该因子，否则以默认方向追加；追加受上限保护；评审 R1）。 */
  onToggle: (member: FactorMember) => void;
  /** 批量并入候选（去重 + 截断到 maxMembers），用于「全选当前结果」。 */
  onAddMany: (members: FactorMember[]) => void;
  /** 清空全部已选。 */
  onClear: () => void;
  /** 按下标移除单个已选成员（沿用既有语义）。 */
  onRemove: (index: number) => void;
  /** 切换某已选成员方向（沿用既有语义）。 */
  onDirection: (index: number, direction: FrozenDirection) => void;
  /** 成员数上限（默认 12，与 validateMembers 同口径）。 */
  maxMembers?: number;
}
```

子组件（同文件内，不对外导出）：

```ts
interface CandidateChipProps {
  label: string;                 // 中文名
  factorKey: string;
  category: string;              // 经 categoryLabel 显示
  direction: FrozenDirection;    // 方向 Badge
  rankIc?: number | null;        // 我的因子来源展示主 horizon RankIC
  intradayOnly?: boolean;        // 仅日内 Badge
  selected: boolean;             // 决定描边/勾选/aria-pressed
  disabled: boolean;             // 已满且未选时禁用
  onClick: () => void;
}

interface SelectedBasketProps {
  members: FactorMember[];
  cn: (factorKey: string) => string;            // factorKey → 中文名
  onRemove: (index: number) => void;
  onDirection: (index: number, d: FrozenDirection) => void;
  onClear: () => void;
}
```

### 5.2 纯函数签名（`apps/web/lib/factor.ts`）

```ts
/** 候选身份键＝`factorKey | 规范化(params)`（**不含 direction**，评审 R1）。用于调色板「已选回显 / 单击切换 / 批量去重」：
 *  同一因子在调色板里只对应一个已选态，direction 仅是加入时默认值，加入篮子后可翻转而不影响其候选已选态。
 *  与含 direction 的后端对齐键 `memberIdentity`（§6，用于 pendingMembers 合并）职责不同、并存不冲突。 */
export function candidateIdentity(
  member: Pick<FactorMember, "factorKey"> & { params?: Record<string, unknown> },
): string;

/** 候选是否已在 members 中（按 candidateIdentity 判定，忽略 direction）。 */
export function isCandidateSelected(members: FactorMember[], candidate: FactorMember): boolean;

/** 切换：按 candidateIdentity 命中则移除所有匹配该因子的成员，否则在 < max 时以候选默认方向追加；
 *  已满且未选则原样返回（由调用方提示）。
 *  返回 { next, added, capped }：added=true 表示发生追加；capped=true 表示因已满而拒绝追加（供调用方提示上限）。 */
export function toggleMember(
  members: FactorMember[],
  candidate: FactorMember,
  max: number,
): { next: FactorMember[]; added: boolean; capped: boolean };

/** 批量并入：按 candidateIdentity 对「候选 vs 既有」与「候选之间」双重去重并截断到 max；
 *  返回 { next, skipped }，skipped=被去重或越上限丢弃的条数（绝不静默吞，供调用方提示）。 */
export function addMembersCapped(
  members: FactorMember[],
  candidates: FactorMember[],
  max: number,
): { next: FactorMember[]; skipped: number };

/** 客户端过滤库目录：按中文名/key 命中 q（大小写不敏感）+ 类别匹配（""=全部）。 */
export function filterLibraryItems(
  items: FactorLibraryItem[],
  q: string,
  category: string,
): FactorLibraryItem[];

/** 库目录条目 → 组合成员（默认参数 + directionHint）。 */
export function libraryItemToMember(item: FactorLibraryItem): FactorMember;

/** 已存因子行 → 组合成员（自带 params + 冻结方向，方向冻结复用 freezeMemberDirection）。 */
export function factorRowToMember(
  factor: Pick<Factor, "factorKey" | "params" | "direction">,
  library: FactorLibraryItem[],
): FactorMember;
```

---

## 6. 数据模型（Data Model）

**无持久化数据模型变更**（不动 DB、不动 Pydantic schema、不动 `factorTypes.ts` 既有类型）。

涉及的内存形状均为既有类型：

- `FactorMember = { factorKey: string; params: Record<string, unknown>; weight: number; direction: "long"|"short" }`（`factorTypes.ts:120–125`）。
  - 新选入成员一律 `weight: 0`（权重由后续「训练权重」回填，沿用现状）。
  - `direction` 始终冻结为 `long`/`short`（绝不带 `auto` 进组合，沿用 `freezeMemberDirection`）。
- `FactorLibraryItem`（`factorTypes.ts:10–17`）、`Factor`（`factorTypes.ts:30–44`）作为候选来源，**只读消费**。
- 去重唯一键沿用 `memberIdentity = factorKey | 规范化(params) | direction`（`factor.ts:225–229`），
  保证同 `factorKey` 不同参数的挖掘变体可共存、同身份只留一条——与后端 `_factor_dedupe_key` 同口径。

保存组合 / 新建策略时写入后端的形状（`factorApi.sets.create` / `api.createStrategy` 的 `params`）**完全不变**。

---

## 7. 交互与可访问性细则（HCI & A11y）

- **键盘可达**：候选卡为原生 `<button type="button">` + `aria-pressed={selected}`，Tab 可达、Enter/Space 切换；
  搜索框为既有 `Input`；类别 chips 为 `<button>` + `aria-pressed`；已选方向沿用既有 `<Select>`。
- **回显与语义**：选中态用强调色描边 + ✓ 角标 + `aria-pressed`（不依赖颜色单一通道）；方向用绿/锈 Badge + 文案「看多/看空」双通道。
- **状态文案**（不静默空白，延续因子层「显式可预期」约定）：
  - 加载中：候选区 `Skeleton` 占位。
  - 加载失败（我的因子）：「加载因子失败，请重试」+ 重试按钮（复用既有范式）。
  - 空候选：库目录恒非空；我的因子空 → 「暂无激活因子，去『我的因子』启用或在『因子挖掘』保存结果」（沿用现有文案）。
  - 过滤无结果：「无匹配候选，调整搜索/类别」。
  - 已选为空：篮子显示「从左侧点选，或在『因子评估』/『因子挖掘』中『加入组合』」（沿用现有空态文案）。
  - 达上限：`notice`「最多 12 个因子成员」；满后未选卡 `disabled`。
- **计数与可预见性**：标题「组合成员（已选 N / 上限 12）」；候选区底部「共 M 个候选」+「全选当前结果（K）」。
- **视觉一致**：仅用既有 token（`--accent`/`--accent-soft`/`--surface-2`/`--border`/`--long`/`--short`/`--muted`、`card`、圆角 8/10/12），明暗主题零适配；过渡用既有 `transition-all duration-150 ease-smooth`。
- **响应式**：宽屏双栏（候选 ≈ 60% / 已选 ≈ 40%）；窄屏（< `lg`）堆叠为上下两段，候选网格列数自适应（1–3 列）。
- **性能**：库目录 12 条客户端过滤；我的因子服务端过滤 + Query 缓存；候选区 `max-h` + 纵向滚动，避免长列表撑爆视口（无需虚拟化）。

---

## 8. 测试与验收标准（Testing & Acceptance Criteria）

### 8.1 单元测试（`apps/web/lib/factor.test.ts`，纯函数，必须覆盖正常 + 异常路径）

1. `candidateIdentity` / `isCandidateSelected`：同 key 同 params **不同 direction → 仍判定已选**（候选去向忽略 direction，评审 R1）；
   同 key 不同 params 的变体 → false；空 members → false。
2. `toggleMember`（按 candidateIdentity 判定）：
   - 未选 + 未满 → 追加，`added=true,capped=false`。
   - 已选 → 移除该因子（含「篮子内已被翻转方向」的成员也能被移除），`added=false`。
   - 未选 + 已满（12）→ 原样返回，`added=false,capped=true`。
3. `addMembersCapped`（按 candidateIdentity 去重）：
   - 全新候选并入、去重既有、跨候选自去重。
   - 候选与某既有成员仅 direction 不同 → **仍视为重复、不重复加入**（评审 R1）。
   - 总数越 12 → 截断且 `skipped` 计数正确。
   - 空候选 → 原数组、`skipped=0`。
4. `filterLibraryItems`：按中文名/key 大小写不敏感命中；类别过滤；`q=""&category=""` → 原集。
5. `libraryItemToMember` / `factorRowToMember`：方向冻结正确（`auto` 回落 directionHint/库提示）、`params` 透传、`weight=0`。

### 8.2 组件测试（`apps/web/components/Factor/MemberPalette.test.tsx`，RTL；`NODE_ENV=test` 运行，见仓库既有约定）

1. 点候选卡 → `onToggle` 被以正确成员调用；再点 → 再次调用（移除）。
2. 已选卡渲染为 `aria-pressed="true"` + 勾选；未选为 `false`；**篮子内翻转某成员方向后，其候选卡仍为 `aria-pressed="true"`（评审 R1）**。
3. 搜索/类别过滤后候选数变化；过滤无结果显示空态文案。
4. 「全选当前结果」→ `onAddMany` 收到当前可见候选；members 满（12）时多余卡 `disabled`。
5. 「清空已选」→ `onClear`；已选行「移除」→ `onRemove(index)`；方向切换 → `onDirection(index, dir)`。
6. 我的因子加载失败 → 错误态 + 重试；空激活因子 → 引导文案。

### 8.3 验收标准（Acceptance）

- **AC-1**：「权重训练与组合」中**无需展开下拉、无需逐条点添加**即可选成员——单击候选卡即增/删，选中态实时回显，
  且篮子内**翻转方向不会破坏候选已选态、不会产生同因子近重复**（评审 R1）。
- **AC-2**：可搜索、可按类别过滤候选；可「全选当前结果」一键并入（自动去重 + 截断到 12）。
- **AC-3**：已选成员上限严格 ≤12；越限有显式提示且不静默丢弃语义（提示被截断条数）。
- **AC-4**：训练 / 阈值 / 保存组合 / 从组合新建策略全链路与改造前等价；成员变化仍强制重训（trained 作废）。
- **AC-5**：评估/挖掘「加入组合」仍可用，且新界面即时把这些成员显示为已选。
- **AC-6**：`pnpm --filter web tsc` 无误、`pnpm --filter web test` 通过（含新增用例）。
- **AC-7**：`FactorSetBuilder.tsx` 与 `MemberPalette.tsx` 单文件 ≤800 行、单函数 ≤50 行、形参 ≤5、嵌套 ≤4。
- **AC-8**：视觉与明暗主题一致、键盘可达、读屏可识别（`aria-pressed` + 双通道方向/选中提示）。

### 8.4 手工核对清单（开发自验）

- [ ] 搭一个 5 成员组合的总点击数显著低于改造前（目标：搜索/过滤后 ≤ 成员数 + 1）。
- [ ] 同 `factorKey` 不同参数的两个挖掘变体可同时入选，互不顶替。
- [ ] 选入某因子后在篮子内翻转其方向，左侧对应候选卡仍高亮为已选；再点该卡能正确移除（评审 R1）。
- [ ] 切换 `库目录 / 我的因子` 时已选态不丢失、回显正确。
- [ ] 12 上限处禁用态与提示文案正确；移除一个后可再次选入。

---

## 9. 风险与缓解（Risks & Mitigations）

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R-1 | 抽出 `MemberPalette` 时漏接 `pendingMembers` 合并或 `trained` 作废副作用 | 评估/挖掘「加入组合」失效、或成员变了权重不重训（破 H3） | 这两个 `useEffect` 留在 `FactorSetBuilder` 父层、**不随 Palette 迁移**；§3.3/§3.6 明确；组件测试 AC-5 + 既有 H3 行为回归。 |
| R-2 | 批量「全选」绕过 ≤12 上限 | 触发 `validateMembers` 报错或后端 422 | 上限护栏集中在 `toggleMember`/`addMembersCapped` 纯函数 + 满态禁用卡；单测覆盖截断。 |
| R-3 | 去重口径与后端不一致导致变体被静默吞 | 用户「明明点了却没进去」 | 统一复用既有 `memberIdentity`（含规范化 params），与后端 `_factor_dedupe_key` 同口径；单测覆盖同 key 不同参。 |
| R-4 | 我的因子服务端 `q`/`category` 过滤与库目录客户端过滤行为不一致 | 体验割裂 | 两源 UI 一致（同搜索框 + 同类别 chips）；库目录仅 12 条客户端过滤、行为等价；文案统一。 |
| R-5 | 候选卡信息过密影响美观 | 视觉杂乱 | 卡片信息分级（主：中文名；次：key/Badge；再次：RankIC 仅我的因子展示）；复用既有 Badge/卡片 token；窄屏降列。 |
| R-6 | 组件测试在默认 `NODE_ENV=production` 下触发 React act() 报错 | CI/本地测试失败 | 遵循仓库既有约定以 `NODE_ENV=test` 运行 web vitest（见既有记录）。 |
| R-7 | 单文件膨胀超 800 行 | 违反 CLAUDE §二阈值 | 选择 UI 抽到独立 `MemberPalette.tsx`；纯逻辑下沉 `factor.ts`；AC-7 自查。 |
| R-8 | 候选「已选态」用含 direction 的键判定，翻转方向后失配并产生近重复（评审 R1） | 破 AC-1、隐性重复成员 | 调色板统一用 `candidateIdentity`（忽略 direction）判定/切换/去重；`memberIdentity` 仅留给 pendingMembers 合并；单测覆盖「翻转后仍已选」与「仅方向不同视为重复」。 |
| R-9 | TanStack `queryKey` 未含 q/category 导致候选缓存陈旧（评审 R2） | 过滤后候选不刷新、体验错乱 | `queryKey` 含全部入参 `{enabled,q,category,sort}`；`q` 去抖后进 key。 |

---

## 10. 实施过程发现的方案缺陷（由 Subtask #2 回填）

> 开发阶段如发现本方案与代码现实冲突或不可行之处，在此分条记录（问题 / 实际情况 / 处置），供回溯。

（待开发阶段回填）

---

## 11. 清洁度合规自查（Clean Code Checklist）

- 命名自解释（`candidateIdentity`/`isCandidateSelected`/`addMembersCapped`/`CandidateChip`/`SelectedBasket`），无无意义缩写。
- 文件/方法/参数/嵌套/圈复杂度在阈值内：选择 UI 抽独立组件、纯逻辑入 `factor.ts`、props 用对象聚合。
- 错误显式传播：加载失败/空态/上限均有明确文案，不静默吞。
- 注释只解释「为什么」（如「为何保留 trained 作废 effect 于父层」），无僵尸代码。
- 测试覆盖正常 + 异常路径（上限截断、去重、空集、加载失败）。
- 不引入新依赖；不泄露任何密钥/敏感信息；与既有 token / 命名风格一致，不制造孤岛。

---

## 评审结论（Review Verdict · Subtask #1）

**结论：有条件通过（Approved with Conditions）。**

本方案可行性、完整性、一致性、体量适配整体良好：纯前端改造、零后端/接口/DB 变更已逐项核实可行
（`factorApi.list` 已原生支持 `q`/`category`/`sort`，`Factor.eval`/`primaryRankIc` 口径有现成复用），
设计严守 `CLAUDE.md` 阈值与既有 token / 纯函数约定，保留 H1/H2/H3 等全部下游契约。
评审发现的 **2 个 P1（R1 候选已选态去 direction、R2 queryKey 组成）与 2 个 P2（R3 RankIC 口径、R4 导航宽度表述）已在 v2 文档正文就地修复**，无 P0 遗留。

放行须在 **Subtask #2（开发）** 落地时满足以下条件（均为实现期注意项，不影响方案放行）：

1. **C1**：调色板的「已选回显 / 单击切换 / 全选去重」必须实现为按 `candidateIdentity`（factorKey+规范化 params，忽略 direction），
   并以单测覆盖「翻转方向后仍已选」「仅方向不同视为重复」两条（对应 R1 / R-8 / §8.1 / §8.2）。
2. **C2**：「我的因子」候选查询的 `queryKey` 必须包含 `{enabled,q,category,sort}` 全部入参，`q` 去抖后入 key（对应 R2 / R-9）。
3. **C3（R5）**：上限提示与「已保存」提示共用单 `notice` 槽，开发时确认覆盖行为可接受（沿用现状）；若易引起误解，可加短时自动消失，但非阻断项。
4. **C4（R6）**：移除 `MemberPicker`/`LibrarySourcePicker`/`MyFactorsSourcePicker`/`myFactorOptionLabel` 后，
   清理 `FactorSetBuilder.tsx` 中因此变为未使用的 import（如 `directionLabel`），确保 `pnpm --filter web tsc` 零告警（对应 AC-6 / AC-7）。

满足 C1–C4 即视为达成本方案「Definition of Done」，可进入开发。
