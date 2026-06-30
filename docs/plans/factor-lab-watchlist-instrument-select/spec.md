# 因子实验室「标的」改为自选标的下拉框 — 开发方案 spec.md

> Feature: `factor-lab-watchlist-instrument-select`
> 版本：v2（评审修复 — Subtask #1）
> 状态：评审通过（已就地修复全部 P0/P1，见文末「评审结论」）
> 所属任务：01-标准开发3-系统模板 / Subtask #1 方案评审与修复

---

## 评审记录（Review Notes — Subtask #1，v2）

> 评审人：Anthropic 工程团队资深评审（设计评审）。评审维度：可行性 / 完整性 / 一致性 / 范围合理性。
> 评审基线：已对照 `apps/web/components/Factor/FactorLab.tsx`、`apps/web/components/ui` 的 `Select`、`apps/web/lib/store.ts`（`watchlist` 声明 L138 / 初值 L326 / partialize L650 / persist `version: 22` L629 / `toggleWatchlist` L549）、`apps/web/lib/instruments.ts`（`FALLBACK_INSTRUMENTS` L12）、四个下游消费方（`FactorSetBuilder.tsx:202` / `FactorEvalPanel.tsx:46` / `FactorMiningPanel.tsx:23` / `FactorSetDetail.tsx:32`）逐一核对——spec 引用的代码位置、行号与签名均属实。
> 核心不变量复核：评审确认**无任何子工作区调用 `wb.setInstId`**（`setInstId` 仅出现在 `FactorLab.tsx` 自身与测试 mock 中），故「原生 `<select>` 的 value 恒命中一个 `<option>`」这一不变量在「下拉 onChange（必为 watchlist 成员）+ 重协调副作用」两个唯一写入口下成立。
> 处置：P0/P1 已在 v2 正文就地修复；P2 部分修复、部分记录为「已知接受」。

| 编号 | 维度 | 严重度 | 问题 | 处置（v2） |
|---|---|---|---|---|
| R-1 | 完整性 | **P1** | §2.5 / 风险表第 1 行把「是否补『已切换到自选标的』提示」留作评审期决策，悬而未决会阻塞实现节点。 | **已决策（不加提示）**：下拉框已可见地呈现当前选中标的，切换对用户可见；且「标的须来自自选」本就是需求本意，每次进入都弹提示是噪音。见 §2.5、风险表第 1 行。 |
| R-2 | 完整性 | **P1** | 空自选态下工作台向五个工作区广播 `instId=""`，但未写明下游契约（是否会在挂载时自动触发请求）。 | **已补契约**：评审核实下游 计算/评估/挖掘 均为按钮点击触发的 `useMutation`，挂载不自动发请求；唯一自动 `useQuery` 是与 instId 无关的因子库目录。空 instId 仅在用户显式点击时到后端并触发既有明确报错，与当前自由文本可清空为 `""` 的暴露面一致，**不构成回归**。见 §2.4「空自选态契约」、§6.3。 |
| R-3 | 一致性 / 可访问性 | P2 | 新 `Select` 加了 `aria-label="标的（自选）"`，而其对齐范式「周期」`Select` 并无 aria-label、由外层 `<label>` 提供可访问名；额外 aria-label 既偏离范式又覆盖可见「标的」文案（可见标签 ≠ 可访问名）。 | **已修复**：移除冗余 `aria-label`，与「周期」一致由外层 `<label>` 命名。见 §2.4 步骤 4、§四。 |
| R-4 | 一致性 | P2 | 组件测试 `FactorLab.test.tsx` 在「文件清单」标注为「新增（推荐）」（可选），而 §6.2 又列出三条具体用例如同必做，定位不一致。 | **已对齐**：纯函数单测（§6.1）为必做 P0；组件测试明确为推荐增强（非阻断），并补一行测试中如何经真实 store 注入 `watchlist`（需 `NODE_ENV=test`）。见 §6.2、文件清单备注。 |
| R-5 | 完整性 | P2 | 常驻挂载下，惰性 `useState` 初值在补水前的默认态求值，重协调副作用依赖 `[watchlist]` 在补水后纠偏；spec 未点明此时序与「刻意不跟随 `storeInst`」的关系。 | **已补说明**：§2.4 增加一行，说明重协调有意只跟踪 `watchlist`（不跟 `storeInst`），保持既有「lab 本地态不回写主工作台」解耦（FactorLab L22 注释），且比现状 `useState(storeInst)` 更稳健。 |
| R-6 | 范围合理性 | P2 | 风险表第 4 行将「手敲未收藏标的」兜底入口（「其它…」）推迟到后续。 | **已知接受**：符合需求本意，v1 明确不纳入；用户可先在交易工作台一键收藏再选。无需改动。 |

**可行性结论**：方案纯前端、零后端、零数据模型新增；`Select` 透传 props、`watchlist` 既有且已被自动交易分组等消费、纯函数 + 同名单测均符合现有约定，**技术上完全可落地**。**无 P0**。

---

## 一、Overview（概述）

因子实验室（Factor Lab，`apps/web/components/Factor/FactorLab.tsx`）在标题「因子实验室」下方有一条**工作台条**（workbench bar），统一承载「标的 / 周期 / 起始 / 结束」四个选择控件，并通过 `WorkbenchContext` 把这四个值下发给五个工作区（因子目录、我的因子、因子评估、因子挖掘、权重训练与组合、因子组合策略），用于因子计算 / 评估 / 挖掘 / 训练 / 打分时拼装请求区间 `range = { instId, bar, startTs, endTs }`。目前「周期」是 `Select` 下拉、「起始 / 结束」是 `<input type="date">`，唯独「标的」是一个**自由文本输入框**（`<input>`，输入时强制大写）。

让用户手工敲入 instId（如 `BTC-USDT-SWAP`）有三个问题：其一，容易拼错或敲入不存在 / 非本平台支持的 instId，错误要等到点「计算 / 评估 / 训练」打到后端才暴露，反馈链路长；其二，与产品其余部分（交易工作台的标的侧边栏、自动交易分组）已经建立的「自选标的（watchlist）」心智不一致——用户在别处都是「从自选里挑」，到了因子实验室却要凭记忆手敲；其三，无法在因子实验室内快速切换自己关注的多个标的去分别建因子 / 训模型。

本方案把「标的」这个控件从自由文本输入框改为**下拉框**，其候选项来自用户在交易工作台维护的**自选标的列表**（Zustand store 的 `watchlist: string[]`，已持久化、已是 self-curated 单一真相）。用户在因子实验室顶部直接从下拉里选择不同的自选标的，即可分别对这些标的创建因子、搭建因子组合策略、训练权重并打分。该改动是**纯前端、零后端、零数据模型新增**：`watchlist` 已存在且已被自动交易分组等功能消费；本方案只是把它作为因子实验室标的选择的候选源，并补一个纯函数做「当前选中标的 ⇄ 自选列表」的协调，确保原生 `<select>` 的值恒与某个 `<option>` 对齐、不出现空值或静默漂移。

---

## 二、Technical Design（技术设计）

### 2.1 现状代码路径

- `FactorLab.tsx`
  - L79–87：从 store 读初值 `storeInst = useAppStore((s) => s.instId)`，并用本地 state `const [instId, setInstId] = useState(storeInst)`（lab 内本地态，**刻意不回写主工作台**，见 L22 注释）。
  - L91–115：`useMemo` 组装 `Workbench` 上下文对象，含 `instId` 与 `setInstId`。
  - L155–193：工作台条 JSX。其中 L156–163 是「标的」自由文本输入框：
    ```tsx
    <input
      value={instId}
      onChange={(e) => setInstId(e.target.value.toUpperCase())}
      className="w-44 rounded-[8px] border border-[var(--border)] bg-[var(--surface)] px-2 py-1 text-xs tabular"
    />
    ```
  - L164–171：紧邻的「周期」已是 `Select`（候选 `BARS`），是本次要对齐的视觉 / 交互范式。
- 下游消费：`FactorSetBuilder.tsx:202`、`FactorEvalPanel.tsx:46`、`FactorMiningPanel.tsx:23`、`FactorSetDetail.tsx:32` 均以 `wb.instId` 拼 `range`。**这些下游对 `instId` 只读取字符串值**，因此把输入框换成下拉框后下游零改动——流过去的仍是同一个 instId 字符串。

### 2.2 自选标的数据源

- `apps/web/lib/store.ts`：`watchlist: string[]`（L138 声明、L326 初值 `[...FALLBACK_INSTRUMENTS]`、L650 进 `partialize` 持久化）。`FALLBACK_INSTRUMENTS = ["BTC-USDT-SWAP","ETH-USDT-SWAP","SOL-USDT-SWAP"]`（`apps/web/lib/instruments.ts:12`）。
- 该列表已被 `AutoTradePanel` / `InstrumentSidebar` 等消费，是「自选标的」的单一真相，**本方案直接复用，不新增任何 store 字段、不动 persist version、不动 migrate**。

### 2.3 新增纯函数：标的 ⇄ 自选列表协调

原生 `<select>` 要求其 `value` 必须命中某个 `<option>`，否则浏览器会回落到首项并产生「显示值 ≠ state 值」的不一致。因子实验室本地 `instId` 初值来自 `storeInst`，而 `storeInst` 不保证在 `watchlist` 内（例如用户在交易工作台看了一个未收藏的标的再进因子实验室）。因此新增一个纯函数集中处理协调：

文件：`apps/web/lib/factor.ts`（既有「因子层纯展示 / 几何 helper」文件，约定纯函数 + 同名 `factor.test.ts` 单测）。

```ts
/**
 * 把「当前选中标的」协调到「自选标的下拉」可呈现的合法值：
 * - current 在 watchlist 内 → 原样返回（尊重用户已选）；
 * - 否则回落到 watchlist[0]（下拉首项），保证原生 select 的 value 恒命中一个 option；
 * - watchlist 为空 → 返回 ""（由 UI 渲染禁用态 + 空态提示）。
 */
export function resolveWatchlistInstId(current: string, watchlist: string[]): string {
  if (watchlist.includes(current)) return current;
  return watchlist[0] ?? "";
}
```

该函数同时服务两处：**挂载时初值解析** 与 **watchlist 变化时的重协调**（见 2.4）。

### 2.4 FactorLab 组件改造

1. 订阅自选列表：`const watchlist = useAppStore((s) => s.watchlist);`
2. 初值用纯函数解析（替换 L84 的 `useState(storeInst)`）：
   ```ts
   const [instId, setInstId] = useState(() => resolveWatchlistInstId(storeInst, watchlist));
   ```
3. 重协调副作用：当 `watchlist` 变化（例如在别处取消收藏了当前标的，持久化态变更）导致 `instId` 掉出列表时，把它拉回合法值，避免 select 显示与 state 漂移：
   ```ts
   useEffect(() => {
     setInstId((cur) => resolveWatchlistInstId(cur, watchlist));
   }, [watchlist]);
   ```
   （`setInstId` 接受函数式更新；当解析结果与 `cur` 相等时 Zustand/React 不会触发多余渲染。）
4. 控件替换（L156–163）：把自由文本 `<input>` 换为 `Select`（复用 `@/components/ui` 已导入的 `Select`，与紧邻「周期」**完全对齐**——含「不显式加 `aria-label`、由外层 `<label>` 的「标的」文本提供可访问名」，避免可见标签与可访问名分叉，评审 R-3）：
   ```tsx
   <label className="flex flex-col gap-1">
     <span className="text-[11px] text-[var(--muted)]">标的</span>
     <Select
       value={instId}
       onChange={(e) => setInstId(e.target.value)}
       disabled={watchlist.length === 0}
       className="w-44 py-1 text-xs"
     >
       {watchlist.length === 0 ? (
         <option value="">无自选标的</option>
       ) : (
         watchlist.map((id) => (
           <option key={id} value={id}>{id}</option>
         ))
       )}
     </Select>
   </label>
   ```
5. 空态提示：在工作台条沿用既有 `!rangeValid` 提示同一范式（L190–192），当 `watchlist.length === 0` 时追加一条就地提示：
   ```tsx
   {watchlist.length === 0 && (
     <span className="pb-1 text-[11px] text-[var(--muted)]">
       请先在交易工作台添加自选标的
     </span>
   )}
   ```
6. **空自选态契约（评审 R-2）**：watchlist 为空时 `resolveWatchlistInstId` 返回 `""`，工作台向五个工作区广播 `instId=""`。下游契约：**计算 / 评估 / 挖掘 / 训练 / 打分均为按钮点击触发的 `useMutation`，挂载时不会以空 `instId` 自动发请求**（唯一自动 `useQuery` 是与 `instId` 无关的因子库目录）。故空 `instId` 仅在用户在空态下显式点击动作时才会抵达后端，由后端既有的空 `instId` 校验返回明确错误——这与当前自由文本输入框可被清空为 `""` 的暴露面**完全一致，不是本方案引入的回归**。因此**子工作区无需改动**（保持范围聚焦）；下拉禁用 + 空态提示已是面向「空自选」这一边界的充分前端引导。
7. **重协调与补水时序（评审 R-5）**：FactorLab 为**常驻挂载**（L66–68 docstring）。惰性 `useState(() => resolveWatchlistInstId(storeInst, watchlist))` 仅在首次挂载（此时 `storeInst` / `watchlist` 多为补水前默认态）求值一次；随后 persist 补水使 `watchlist` 引用变化，重协调副作用（依赖 `[watchlist]`）据最新自选纠偏选中值。重协调**有意只跟踪 `watchlist` 而不跟踪 `storeInst`**，从而保留既有「lab 本地态刻意不回写主工作台」的解耦（FactorLab L22 注释），并比现状 `useState(storeInst)` 在补水后更稳健（现状不会在补水后纠偏）。

### 2.5 时序（用户操作流）

1. 用户进入因子实验室 → FactorLab 挂载 → `resolveWatchlistInstId(storeInst, watchlist)` 得到初始 instId（命中自选则用之，否则用自选首项）。**评审 R-1 决策：当初值因不在自选而回落首项时，不额外弹「已切换到自选标的」提示**——下拉框已可见地呈现当前选中标的，切换对用户透明可见，且「标的须来自自选」即需求本意，每次进入弹提示只是噪音。
2. 工作台条「标的」下拉渲染 `watchlist` 全量为 `<option>`，当前 instId 高亮选中。
3. 用户从下拉选另一个自选标的 → `setInstId` 更新本地 state → `Workbench.instId` 变化 → 五个工作区 `wb.instId` 随之变化 → 用户在某工作区点「计算 / 评估 / 挖掘 / 训练 / 打分」时，请求 `range.instId` 即为新选标的。
4. 用户在交易工作台增删自选 → 持久化 `watchlist` 变化 → 因子实验室若可见，重协调副作用保证下拉候选与选中值始终一致。

---

## 三、File / Module Change Plan（文件 / 模块变更计划）

| 文件 | 变更类型 | 一句话意图 |
|---|---|---|
| `apps/web/lib/factor.ts` | 修改 | 新增纯函数 `resolveWatchlistInstId(current, watchlist)`，集中处理「当前标的 ⇄ 自选列表」协调与空列表回落。 |
| `apps/web/lib/factor.test.ts` | 修改 | 为 `resolveWatchlistInstId` 补单测：命中原样返回 / 未命中回落首项 / 空列表回落 `""`。 |
| `apps/web/components/Factor/FactorLab.tsx` | 修改 | 订阅 `watchlist`；用纯函数解析初值并加重协调副作用；把「标的」自由文本 `<input>` 替换为来自自选标的的 `Select` 下拉，并补空态禁用 + 提示。 |
| `apps/web/components/Factor/FactorLab.test.tsx` | 新增（推荐 / 非阻断 stretch，评审 R-4） | 组件测试：下拉渲染 watchlist 候选、选择切换 workbench 标的、空 watchlist 禁用并显示提示。需 `NODE_ENV=test`，并在 render 前经真实 store（`useAppStore.setState({ watchlist: [...] })`）注入测试自选。**纯函数单测（§6.1）为必做 P0；本组件测试为推荐增强，不阻断合并。** |

> 说明：下游 `FactorSetBuilder.tsx` / `FactorEvalPanel.tsx` / `FactorMiningPanel.tsx` / `FactorSetDetail.tsx` **不改**——它们只读取 `wb.instId` 字符串值，控件形态改变对它们透明。`store.ts`、后端、数据库**不改**。

---

## 四、Interface Design（接口设计）

本方案不涉及 REST / WebSocket / CLI 变更（纯前端 UI），接口面是组件 props / context / 纯函数签名：

- **纯函数**：`resolveWatchlistInstId(current: string, watchlist: string[]): string`
  - 入：当前选中 instId、自选 instId 数组。
  - 出：协调后的合法 instId（命中→原值；未命中→`watchlist[0]`；空→`""`）。
  - 不抛异常（边界在此一次性消化，符合 CLAUDE.md「边界处一次性校验」）。
- **`WorkbenchContext` 契约不变**：`Workbench.instId: string` 与 `Workbench.setInstId: (v: string) => void` 形态、语义保持不变，仅写入来源从「文本输入」变为「下拉选择」。下游消费方零感知。
- **`Select`（`@/components/ui`）**：复用既有原生 `<select>` 封装（实现为 `({className, ...props}) => <select {...props}>`，透传任意原生属性），本方案透传 `value` / `onChange` / `disabled` / `className`，**不透传 `aria-label`**——可访问名由外层 `<label>` 的「标的」文本提供，与相邻「周期」一致（评审 R-3）。无需扩展该原子组件。

---

## 五、Data Model（数据模型）

无新增 / 变更持久化数据模型。涉及的内存形态均为既有：

- `AppState.watchlist: string[]` —— 自选 instId 有序列表，持久化于 localStorage key `aragon-ui`（store persist version 维持 **22**，不 bump）。
- `Workbench`（`FactorLab.tsx` 内存上下文）—— `{ instId, bar, startTs, endTs, setInstId, setBar, setRange, evalTarget, ... }`，字段形态不变。
- 因子请求区间 `range = { instId, bar, startTs, endTs }` —— 形态不变，`instId` 取值来源改变。

由于 `watchlist` 早已在 `partialize`（store.ts:650）与各 migrate 分支中就位，本方案**无需新增 persist 版本、无需写 migrate**。

---

## 六、Testing & Acceptance Criteria（测试与验收标准）

### 6.1 单元测试（P0，必做）

`apps/web/lib/factor.test.ts` 新增对 `resolveWatchlistInstId` 的覆盖：

1. `resolves_current_when_in_watchlist`：`current` 在列表内 → 原样返回。
2. `falls_back_to_first_when_current_absent`：`current` 不在列表 → 返回 `watchlist[0]`。
3. `returns_empty_for_empty_watchlist`：空列表 → 返回 `""`（含 `current` 任意值）。

### 6.2 组件测试（推荐增强 / 非阻断，评审 R-4）

> 本节为推荐增强，**不阻断合并**；核心协调逻辑已由 §6.1 的必做纯函数单测覆盖。组件测试运行需 `NODE_ENV=test`（否则 React act() 告警——见项目既有 web vitest 约定），并在 render 前经真实 store 注入测试自选：`useAppStore.setState({ watchlist: ["BTC-USDT-SWAP", "ETH-USDT-SWAP"] })`。

`apps/web/components/Factor/FactorLab.test.tsx`：

1. `renders_watchlist_options`：给定 `watchlist=["BTC-USDT-SWAP","ETH-USDT-SWAP"]`，标的下拉渲染对应 `<option>`，且默认选中 store 的 `instId`（若在列表内）。
2. `switching_option_updates_workbench`：选择另一项后，子工作区收到的 `wb.instId` 同步更新（可借探针子组件或断言下游请求 instId）。
3. `empty_watchlist_disables_select`：`watchlist=[]` 时下拉禁用、显示「无自选标的」选项与「请先在交易工作台添加自选标的」提示。

### 6.3 手动验收（Definition of Done 对齐）

- 进入因子实验室，「标的」呈现为下拉框（非文本框），候选恰为当前自选标的，默认选中合理（命中自选或自选首项）。
- 下拉切换标的后，在「因子评估 / 挖掘 / 权重训练与组合」任一工作区触发计算，请求落在新选标的上。
- 在交易工作台取消收藏当前因子实验室所选标的后回到因子实验室，下拉候选与选中值自动协调一致、不出现空白或错位。
- **空自选态（评审 R-2）**：清空全部自选后进入因子实验室，「标的」下拉禁用并显示「无自选标的」与「请先在交易工作台添加自选标的」提示；此时挂载不会自动触发任何计算请求（不会出现空 `instId` 的静默请求）。
- 既有 web 测试（`pnpm --filter web test`）全绿；TypeScript 编译无新增报错。

---

## 七、Risks & Mitigations（风险与缓解）

| 风险 | 影响 | 缓解 |
|---|---|---|
| 当前 `instId`（来自主工作台）不在自选列表 → 进入因子实验室时标的被静默切换 | 用户可能困惑「为什么我看的标的变了」 | 用 `resolveWatchlistInstId` 显式回落自选首项，行为可预测；常见路径下默认标的 `BTC-USDT-SWAP` 即在默认自选内，几乎不触发。**评审 R-1 已决策：不补「已切换」提示**——下拉可见呈现当前标的即足够，详见 §2.5 步骤 1。 |
| 自选列表为空（用户清空收藏） | 下拉无候选、计算会因空 instId 报错 | 下拉禁用 + 空态提示，引导用户去交易工作台添加自选；后端对空 instId 本就返回明确错误（不会伪造数据）。**评审 R-2 已写明空态契约**：下游动作均为点击触发、挂载不自动发空请求，子工作区无需改动，详见 §2.4 步骤 6。 |
| 自选列表很长 → 原生下拉滚动体验一般 | 极端情况下选取略繁琐 | 自选为用户 self-curated，通常规模小；v1 采用与「周期」一致的原生 `Select`（零新增依赖、最低风险）。若后续需搜索，可复用既有 `MemberPalette` 式可搜索面板，列为 v2 增强，不阻塞本次。 |
| 失去「手敲任意 instId（含未收藏标的）」能力 | 个别高级用户想临时评估未收藏标的 | 符合需求本意（标的应来自自选）；用户可先在交易工作台一键收藏再回来选。如确有强诉求，评审可考虑保留「其它…」入口，本 v1 暂不纳入以保持范围聚焦。 |
| 重协调副作用与本地 state 交互引入多余渲染 | 轻微性能噪音 | 函数式 `setInstId((cur) => resolve(...))` 在结果不变时返回同值，Zustand/React 不触发额外渲染；副作用仅依赖 `watchlist` 引用变化。 |

---

## 八、实施边界与约束复述

- 本节点（方案设计）仅产出本文档，**不写实现代码**、不改 `docs/plans/<feature>/` 以外的任何源文件、不执行 `git commit`。
- 实现阶段（Subtask #2）须严格按「第三节 文件 / 模块变更计划」逐项落地，遵循项目既有风格（纯函数 + 同名单测、复用 `@/components/ui` 原子、中文注释只解释「为什么」），不引入超出本方案的额外重构。
- 若实现中发现方案缺陷，记录到本文档「实施过程发现的方案缺陷」段（由 Subtask #2 追加）。

---

## 评审结论（Review Verdict）

**结论：有条件通过（Approved with conditions）。**

本方案设计扎实、范围聚焦、与既有约定一致。评审已逐节核对引用的代码位置、`Select` 原子签名、`watchlist` 数据源（persist v22 / partialize / toggleWatchlist）与四个下游消费方，确认其陈述属实；核心不变量「原生 `<select>` 的 value 恒命中一个 `<option>`」由 `resolveWatchlistInstId` 在「初始化」与「`watchlist` 变更」两处保证，且评审核实**无任何子工作区会以越界值调用 `wb.setInstId`**，不变量成立。全部 **P0/P1 已在 v2 正文就地修复**（R-1、R-2），P2 已部分修复（R-3、R-4、R-5）或记录为已知接受（R-6）。批准进入实现（Subtask #2），实现须满足以下条件：

1. **不破坏标的不变量**：`instId` 的唯一写入口仍限于「下拉 `onChange`（值必为 `watchlist` 成员）」与「`[watchlist]` 重协调副作用」；不得在任何工作区以越界值写 `wb.setInstId`。
2. **遵循 R-1 决策（静默切换）**：不新增「已切换到自选标的」提示。
3. **遵循 R-2 空自选态契约**：`watchlist` 为空时禁用下拉 + 文案提示；不改动任何子工作区，空 `instId` 由后端既有校验兜底；不得为此新增挂载即自动发请求的逻辑。
4. **遵循 R-3 可访问性范式**：标的 `Select` 不加冗余 `aria-label`，由外层 `<label>` 命名，与「周期」一致。
5. **测试**：§6.1 三条纯函数单测为必做 P0；§6.2 组件测试为推荐增强、不阻断合并。
6. **范围纪律**：不引入超出本方案的额外重构；下游四个消费方、`store.ts`、后端、数据库均不改；不 bump persist version、不写 migrate。

无遗留 P0 / P1。
