# 多标的自动交易确认弹窗：修改总预算后各标的预算预览不实时更新

> Bug slug: `auto-trade-group-budget-not-recalculated`
> 诊断节点：Subtask #0（问题定位与分析）。本节点**不写修复代码**，仅产出含证据的根因分析与候选方案。

## 一、问题描述（Problem description）

在「自选」侧边栏的**自动交易面板**（左侧图表区 → 自选页签上半区 `AutoTradePanel`）里，
用户把若干标的拖入自动交易区并按下**分组开关**后，会弹出**「启动多标的自动交易」确认弹窗**
（`AutoTradeGroupConfirmModal`）。弹窗顶部展示一个**默认总预算**，下方有一个**「总预算（USDT）」**
输入框；中部「成员清单」按各标的的百分比展示**每标的预算预览**。

用户反馈：在下方输入框修改总预算后，**上方各标的的「每标的预算预览」没有按新的总预算实时重算**，
而是**仍停留在按默认总预算分配出的数额**，看起来像是"输入没生效 / 预算 UI 坏了"。期望行为是：
用户改动总预算后，上方每个标的的预算都应随之自动重新计算并刷新。

## 二、复现步骤（Reproduction steps）

### 2.1 真实 UI 复现（需实盘账户环境）

前置：`accountMode = live`、已配置默认实盘账户、后端 `LIVE_AUTO_TRADING_ENABLED=1`、默认账户有可用 USDT。

1. 打开应用 → 左侧图表区 → 标的侧边栏切到「自选」页签。
2. 从下方「其他自选」把 **5 个** USDT 本位永续标的（如 `BTC-USDT-SWAP` … `PEPE-USDT-SWAP`）拖入上方「自动交易」区（或点其「＋」加入）。点「平均分配」使各占 20%。
3. 打开「自动交易」面板 header 的**分组开关** → 弹出「启动多标的自动交易（5 个标的）」确认弹窗。
4. 观察弹窗：
   - 顶部「默认总预算（随成员数缩放）」= `可用余额 × 5%`（5 个成员时默认已顶到 5% 硬上限）。
   - 成员清单里每标的预算预览 = `默认总预算 × 20%`。
5. 在下方「总预算（USDT）」框输入一个**大于"可用余额 × 5%"**的值（例如可用 1000 时输入 `500`）。
6. **观察缺陷**：顶部数额与各标的预算预览**没有变化**（标签从"默认总预算"变成"生效总预算（预览）"，但**数字一模一样**）。继续把输入改成 `2000`、`100`…数字依旧纹丝不动。

> 触发条件本质：**只要输入值 ≥ 可用余额 × 5%，预览就被静默夹紧回 5% 上限**。由于该上限很小、且
> 成员数 ≥ 5 时默认总预算本就等于该上限，用户为"加大预算"输入的几乎任何值都落在上限之上，于是
> 预览看上去"永远不动、还是默认那套分配"。

### 2.2 确定性数值复现（无需账户，纯函数级）

复现脚本：`docs/diagnoses/auto-trade-group-budget-not-recalculated/repro.mjs`，原样复刻弹窗用到的纯函数
（`deriveGroupDefaultBudget` / `clampGroupBudgetToCap` / `memberBudget`）与 `previewTotal` memo 逻辑。

```bash
node docs/diagnoses/auto-trade-group-budget-not-recalculated/repro.mjs
```

实际输出（节选，可复制核对）：

```text
=== A. 5 members, avail=1000 (default == cap == 50) ===
5% hard cap = 50 USDT ; default total budget = 50 USDT (default == cap? true)
  user types <empty> -> previewTotal =     50 USDT  [默认总预算（随成员数缩放）]  per-symbol: BTC=10.00  ETH=10.00 ...
  user types    100 -> previewTotal =     50 USDT  [生效总预算（预览）]        per-symbol: BTC=10.00  ETH=10.00 ...
  user types    500 -> previewTotal =     50 USDT  [生效总预算（预览）]        per-symbol: BTC=10.00  ETH=10.00 ...
  user types   2000 -> previewTotal =     50 USDT  [生效总预算（预览）]        per-symbol: BTC=10.00  ETH=10.00 ...

=== B. 3 members, avail=1000 (cap=50, default=30) ===
5% hard cap = 50 USDT ; default total budget = 30 USDT
  user types <empty> -> previewTotal =     30 USDT   per-symbol: BTC=15.00  ETH=9.00   SOL=6.00
  user types    200 -> previewTotal =     50 USDT   per-symbol: BTC=25.00  ETH=15.00  SOL=10.00
  user types    500 -> previewTotal =     50 USDT   per-symbol: BTC=25.00  ETH=15.00  SOL=10.00
  user types   1000 -> previewTotal =     50 USDT   per-symbol: BTC=25.00  ETH=15.00  SOL=10.00
```

- 场景 A（≥5 成员）：输入 100/300/500/2000 时 `previewTotal` 恒为 50、每标的恒为 10.00——**与"还停留在默认分配"完全一致**。
- 场景 B（3 成员）：任何"加大预算"的输入都被钉死在 50（仅从默认 30 跳到上限 50 一次，之后再大也不动）。

## 三、根因分析（Root cause analysis）

### 3.1 预览其实是"响应式"的，但被 5% 硬上限静默夹紧

弹窗的总预算预览由 `previewTotal` 这个 `useMemo` 派生，**依赖项里包含 `capital.value`**，所以用户输入
确实会触发重算——问题出在重算的最后一步把结果**夹紧到了可用余额的 5%**：

`apps/web/components/Chart/AutoTradeGroupConfirmModal.tsx:99`
```tsx
const previewTotal = useMemo(() => {
  if (availUsdt == null) return null;
  const raw = capital.value ?? deriveGroupDefaultBudget(availUsdt, members.length);
  return clampGroupBudgetToCap(raw, availUsdt);   // ← 这里把用户输入夹回 5% 上限
}, [availUsdt, capital.value, members.length]);
```

每标的预算预览直接取这个被夹紧的 `previewTotal`：

`apps/web/components/Chart/AutoTradeGroupConfirmModal.tsx:189`
```tsx
{previewTotal != null
  ? `${fmtPrice(memberBudget(previewTotal, m.allocationPct))} USDT`
  : "—"}
```

夹紧函数把上限钉死在 `可用余额 × 5%`：

`apps/web/lib/autoTrade.ts:90`
```ts
export function clampGroupBudgetToCap(budget: number, availUsdt: number): number {
  const cap = Math.max(0, availUsdt) * LIVE_AUTO_MAX_CAPITAL_FRAC;  // 0.05
  return cap > 0 ? Math.min(budget, cap) : budget;
}
```

默认总预算又是 `min(可用 × 1% × 成员数, 可用 × 5%)`——**成员数 ≥ 5 时默认值恰好等于该 5% 上限**：

`apps/web/lib/autoTrade.ts:84`
```ts
export function deriveGroupDefaultBudget(availUsdt: number, memberCount: number): number {
  const avail = Math.max(0, availUsdt);
  return Math.min(avail * LIVE_AUTO_DEFAULT_CAPITAL_FRAC * memberCount, avail * LIVE_AUTO_MAX_CAPITAL_FRAC);
}
```

单测亦坐实该上限语义（`apps/web/lib/autoTrade.test.ts:56`、`:61`）：
```ts
expect(deriveGroupDefaultBudget(1000, 8)).toBe(50); // 8 成员默认 == 5% 上限
expect(clampGroupBudgetToCap(500, 1000)).toBe(50);  // 输入 500 被夹回 50
```

### 3.2 真正的缺陷：夹紧"无声无息"，用户无从知晓

把上面几段拼起来：当 `capital.value ≥ 可用 × 5%`（这是最常见的情形——上限本就很小，且 ≥5 成员时默认
已顶到上限）：

```
previewTotal = clampGroupBudgetToCap(capital.value, avail) = avail × 5% = （≥5 成员时）默认总预算
每标的预算   = memberBudget(previewTotal, pct) = 默认那套分配
```

预览数字因此**钉死在 5% 上限上**，与用户输入脱钩。而弹窗**没有任何"已被夹紧"的提示**：唯一的信号是
顶部那行标签从「默认总预算（随成员数缩放）」变成「生效总预算（预览）」，但**数字可以完全相同**
（`AutoTradeGroupConfirmModal.tsx:159` 处的 `usingDefault = capital.value == null` 只区分"空/非空"，
不区分"是否被夹紧"）。于是用户的主观体验就是："我改了总预算，可上面每个标的的预算压根没变，还是默认那套"——
**正是本 Bug 的报告原文**。

> 注意：这不是"预览没重算"（它重算了），而是"5% 上限静默夹紧 + 缺少夹紧反馈"，二者叠加表现为"预览冻结"。
> 该上限也**正是后端权威口径**，前端预览必须与之一致（见 3.3），所以方向不是"绕过上限"，而是"把夹紧讲清楚"。

### 3.3 该 5% 上限是后端权威护栏，前端预览必须与之吻合

后端启动分组时同样把非空输入夹紧到 `可用 × live_auto_max_capital_frac(=0.05)`：

`services/quant/app/live_auto/manager.py:434`
```python
@staticmethod
def _resolve_budget_group(capital_usd, avail, member_count, settings):
    """P1-1：留空→ min(avail×default_frac×N, avail×max_frac)；非空→ 夹到 ≤ avail×max_frac。"""
    hard_cap = avail * settings.live_auto_max_capital_frac
    if capital_usd and capital_usd > 0:
        budget = capital_usd
    else:
        budget = avail * settings.live_auto_default_capital_frac * member_count
    return min(budget, hard_cap) if hard_cap > 0 else budget
```

设计文档亦明确该语义（`docs/plans/watchlist-auto-trade-group/spec.md` §2.3）：
"非空输入则按可用余额硬上限（5%）夹紧"，且**特别叮嘱**："留空时确认弹窗须显式回显「将使用默认总预算
= …」，**避免用户误以为未生效**"。可见"让用户误以为未生效"正是规格预先警惕、却在**非空且超上限**这条路径上
没有兜住的情形——这就是本 Bug 的本质。

**结论（根因一句话）**：`previewTotal`（`AutoTradeGroupConfirmModal.tsx:99-103`）把用户输入的总预算
通过 `clampGroupBudgetToCap` 静默夹紧到"可用余额 × 5%"，而弹窗未对"输入被夹紧"给出任何提示；由于该上限
很小、且成员数 ≥ 5 时默认总预算本就等于上限，用户为加大预算输入的几乎所有值都被夹回同一数额，导致上方
各标的预算预览看似"不随总预算更新、永远停留在默认分配"。

## 四、影响面（Impact surface）

- **受影响功能**：仅限「多标的自动交易」启动确认弹窗 `AutoTradeGroupConfirmModal` 的预算预览（实盘账户模式）。
  策略面板的单标的 `AutoTradeConfirmModal` 走的是 `lib/liveAuto.ts` 的另一套函数，不在本路径内（但存在同源
  隐患：其 `clampBudgetToCap` 上限同为 5%，同样无夹紧提示——可顺带核查，非本 Bug 必修项）。
- **受影响用户**：任何使用多标的自动交易、且**期望的总预算超过"可用余额 × 5%"**的用户；尤其是**成员数 ≥ 5**
  者（默认已顶到上限，输入任何更大值都毫无变化）。
- **后果**：纯**前端预览/认知问题**——不影响真实下单安全（后端独立夹紧，实际预算永远 ≤ 5%）。但会让用户
  误判"预算 UI 坏了 / 输入没生效"，可能反复尝试、错估每标的实际可用预算，或干脆放弃启动；对一个真实资金
  入口而言，这种"静默吞掉用户意图"的体验是不可接受的。
- **不影响**：百分比分配（`AutoTradeAllocationInput`，与预算解耦）、止盈止损分摊、运行期态。

## 五、候选修复方案（Candidate fixes）

| # | 方案 | 做法概述 | 侵入性 | 风险 | 工作量 | 是否满足用户诉求 |
|---|------|---------|--------|------|--------|------------------|
| 1（推荐） | **显式提示夹紧** | 当 `capital.value != null && capital.value > 可用×5%` 时，在总预算输入框附近渲染一条就地提示（如"输入 {x} 超过可用余额 5% 上限（{cap} USDT），已按上限 {cap} 计算"）；并把"生效总预算（预览）"标签在夹紧时补注"（已按 5% 上限夹紧）"。预览仍用与后端一致的夹紧值。 | 极小（仅 `AutoTradeGroupConfirmModal.tsx` 一个组件、纯展示） | 极低（不改护栏、不改后端、不改预算口径） | 低 | 在上限内：预览随输入实时重算✔；超上限：明确告知原因，消除"没生效"的误解✔ |
| 2 | **预览展示原始未夹紧值，提交时再夹** | 每标的预览按用户输入的原始总预算实时计算，仅在确认提交时夹紧。 | 小 | **高**：后端必然把实际预算夹回 5%（`manager.py:438-443`），预览会显示系统根本不会执行的数额，对真实资金流误导极大，违背"预览≈实际执行" | 低 | 看似满足，实则展示假数据 ✗ |
| 3 | **上调 / 参数化分组预算上限** | 提高 `LIVE_AUTO_MAX_CAPITAL_FRAC` 或为分组单设更高上限，使用户输入真正生效。 | 大（前端 + 后端 `config.py` + `manager.py` + 单测同步） | **高**：直接削弱真实资金护栏，属产品/风控决策，超出"修 Bug"范畴 | 中-高 | 满足，但以放松安全上限为代价 ✗（如确为产品意图应单独立项） |

## 六、推荐方案（Recommended fix）

**采用方案 1：让"5% 上限夹紧"可见。**

理由：
1. **直击用户真实诉求**。用户的核心抱怨是"改了总预算，上方各标的预算却像没反应一样"。方案 1 在
   上限以内让预览随输入实时重算（本就如此），在超上限时**明确告知"已按 5% 上限夹紧到 X USDT"**，
   从根上消除"没生效/UI 坏了"的误解——这正是规格 §2.3 已点名要避免、却在"非空超限"路径漏掉的情形。
2. **不削弱真实资金护栏**。5% 硬上限是前后端一致的权威安全口径（`manager.py:438-443`、`config.live_auto_max_capital_frac`）。
   方案 1 保留夹紧、保留预览与后端实际执行一致，仅补"为什么被夹"的反馈。
3. **改动最小、风险最低**。仅触及 `AutoTradeGroupConfirmModal.tsx` 一个展示组件，无后端/配置/契约变更，
   零安全面影响，易于单测（给 `previewTotal`/夹紧判定补纯函数级断言即可）。
4. 方案 2 会对真实资金流展示系统不会执行的数额，方案 3 等于放松风控、属产品决策——均不适合作为本 Bug 的修复。

**给下游（Subtask #2 实现）的落地要点（建议，非强制）**：
- 在 `AutoTradeGroupConfirmModal.tsx` 内计算 `cap = 可用余额 × LIVE_AUTO_MAX_CAPITAL_FRAC` 与
  `isClamped = capital.value != null && capital.value > cap`。
- `isClamped` 为真时：在"总预算（USDT）"输入框下渲染就地提示，并将顶部"生效总预算（预览）"标签补注
  "（已按 5% 上限夹紧）"；仍以 `previewTotal`（夹紧后）驱动每标的预算预览，保证与后端一致。
- 复用既有纯函数（`clampGroupBudgetToCap`/`memberBudget`），不新增依赖；若抽取 `isClamped`/`cap` 计算为
  纯函数，顺手在 `autoTrade.test.ts` 补一条断言（超上限→提示、上限内→无提示）。
- （可选、非本 Bug）核查策略面板单标的 `AutoTradeConfirmModal` 是否存在同源"静默夹紧"，如有可一并对齐。

---

### 附：证据索引（file:line）

- `apps/web/components/Chart/AutoTradeGroupConfirmModal.tsx:99-103` —— `previewTotal` memo（依赖 `capital.value`，末步 `clampGroupBudgetToCap` 夹紧）。
- `apps/web/components/Chart/AutoTradeGroupConfirmModal.tsx:159` —— `usingDefault = capital.value == null`（只区分空/非空，不区分是否被夹紧）。
- `apps/web/components/Chart/AutoTradeGroupConfirmModal.tsx:189-191` —— 每标的预算预览取自 `previewTotal`。
- `apps/web/lib/autoTrade.ts:90-93` —— `clampGroupBudgetToCap` 夹到 `可用 × 5%`。
- `apps/web/lib/autoTrade.ts:84-87` —— `deriveGroupDefaultBudget = min(可用×1%×N, 可用×5%)`（N≥5 时 == 上限）。
- `apps/web/lib/autoTrade.test.ts:56-64` —— 坐实上限：`deriveGroupDefaultBudget(1000,8)=50`、`clampGroupBudgetToCap(500,1000)=50`。
- `services/quant/app/live_auto/manager.py:434-443` —— 后端 `_resolve_budget_group` 同样夹到 5%（权威口径）。
- `docs/plans/watchlist-auto-trade-group/spec.md` §2.3（行 139-141） —— 规格的预算/上限语义与"避免用户误以为未生效"的明确要求。
- `docs/diagnoses/auto-trade-group-budget-not-recalculated/repro.mjs` —— 确定性数值复现脚本（输出见 §2.2）。
- `apps/web/lib/liveAuto.ts:45-53` —— `parseOptionalUsd`：合法正数原样返回 `value: n`（**不夹紧、不再变换**），故 `capital.value` 即用户键入的原始数值；空串/非正/非数 → `value: null` 且给 `error`。**排除"输入解析器自身吞值/夹值"这一替代根因**（评审补强，见 §七-1）。
- `apps/web/components/Chart/AutoTradeGroupConfirmModal.tsx:90` —— `capital = parseOptionalUsd(capitalRaw)` 每次渲染重算，`capital.value` 是基本类型（number/null），故 memo 依赖项每次有效击键都真正变化、`useMemo` 必重算 → **预览确实是响应式的，"冻结"纯由夹紧造成**（评审补强，见 §七-1）。

---

## 七、评审结论（Review Verdict）

> 评审节点：Subtask #1（分析报告评审与修复）。本节点逐条核验上文证据链与候选方案，补强薄弱处，并敲定**单一**推荐修复。**不写修复代码**（留给 Subtask #2）。

### 7-1 根因证据复核：成立，且已补强为闭环

逐条核对 §三 引用的全部 `file:line`，**均与当前真实源码一致**（非陈旧引用），且 `repro.mjs` 在本机实跑输出与 §2.2 文档引用**逐字符吻合**（已复跑核对）。在此基础上，评审额外排除了两条"替代解释"，使证据链闭环：

1. **不是"输入没传进 memo"**：`capital.value` 来自 `parseOptionalUsd`（`liveAuto.ts:45-53`），合法正数原样返回、不夹不改；`capital` 每次渲染重算、`capital.value` 为基本类型，故 `previewTotal` 的 `useMemo` 依赖项每次有效击键都真正变化、**必然重算**。→ 证明"预览是响应式的"，"冻结"只可能来自下游夹紧，而非 stale memo / 解析器吞值。
2. **不是"无效输入回落默认"那条路径**：当用户键入非数/≤0/空时 `capital.value=null`、`usingDefault=true`、并显示 `inputError`，标签明确回到「默认总预算」——这是**另一条已被正确处理**的分支，与本 Bug（合法且 > 上限的输入被静默夹回）互不混淆。

**裁定**：§三 根因（`previewTotal` 经 `clampGroupBudgetToCap` 静默夹紧到 `可用×5%` + 弹窗无夹紧反馈；N≥5 时默认本就等于上限 → 几乎一切"加大预算"的输入都被夹回同值 → 视觉上"冻结在默认分配"）**证据充分、可复现、与用户报告原文吻合**，无需推翻或改写。

### 7-2 候选方案复核：方案 1 是最小且唯一正确解

- **方案 2（预览展示未夹紧原值）**：会向真实资金入口展示后端根本不会执行的数额（后端 `_resolve_budget_group` 必夹回 5%，`manager.py:438-443`），违背"预览≈实际执行"，**否决**。
- **方案 3（上调/参数化 5% 上限）**：直接削弱前后端一致的真实资金护栏，属产品/风控决策而非修 Bug，**否决**（如确为产品意图须单独立项）。
- **方案 1（让夹紧可见）**：纯展示层、单组件、不动护栏与后端口径、预览与服务端实际执行保持一致——既在上限内让预览随输入实时重算（本就如此），又在超上限时讲清"已按 5% 上限夹紧到 X"，**直击用户诉求且回应规格 §2.3 早已点名要避免、却在"非空超限"路径漏掉的"误以为未生效"**。

### 7-3 方案 1 边界审查：实现前必须收口的两处（评审补充，关键）

§六给出的落地判定 `isClamped = capital.value != null && capital.value > cap` 在主干场景正确，但**漏了两个边界**，若直接照搬会让"提示"与"实际预览值"自相矛盾。下游 Subtask #2 **必须**按下式实现，以保证提示语义与 `clampGroupBudgetToCap` 的真实行为严格一致：

1. **`availUsdt == null`（余额读取中/失败）**：此时 `previewTotal` 为 `null`、UI 显示「—」，**不得**计算 `cap` 或显示夹紧提示。判定须前置 `availUsdt != null` 守卫。
2. **`cap <= 0`（如 `availUsdt == 0`）**：`clampGroupBudgetToCap` 在 `cap <= 0` 时**原样返回 `budget`、并不夹紧**（`autoTrade.ts:92` 的 `cap > 0 ? Math.min(...) : budget`）。但 `capital.value > cap` 在 `cap=0` 时对任意正输入恒为真——若仅用该式，会出现"提示说已夹到 0、预览却显示未夹紧原值"的自相矛盾。故 `isClamped` **必须**附带 `cap > 0` 条件。

> 收口后的判定（供实现参考，**非强制写法**）：
> ```ts
> const cap = availUsdt != null ? availUsdt * LIVE_AUTO_MAX_CAPITAL_FRAC : 0;
> const isClamped = availUsdt != null && cap > 0 && capital.value != null && capital.value > cap;
> ```
> 另：边界比较用**严格 `>`**（而非 `>=`）是正确的——`capital.value === cap` 时夹紧值等于输入、属"原样通过"，无需提示。

单测建议（与 §六一致，补足边界）：在 `autoTrade.test.ts` 对抽出的纯判定补 4 条断言——上限内（无提示）、恰等上限（无提示）、超上限（有提示且提示金额 == cap）、`avail=0`（无提示，避免"夹到 0"假提示）。

### 7-4 最终推荐修复（单一结论）

**采用方案 1：在 `AutoTradeGroupConfirmModal.tsx` 内让"5% 硬上限夹紧"对用户可见**，并按 7-3 收口 `isClamped` 的两处边界。具体范围：

- 仅改 `apps/web/components/Chart/AutoTradeGroupConfirmModal.tsx` 一个展示组件：当 `isClamped`（按 7-3 收口式）为真时，在「总预算（USDT）」输入框下渲染就地提示（含原输入值与上限 `cap`，例如"输入 {x} 超过可用余额 5% 上限（{cap} USDT），已按上限 {cap} 计算"），并给顶部「生效总预算（预览）」标签补注"（已按 5% 上限夹紧）"。
- **预览金额仍由夹紧后的 `previewTotal` 驱动**，与后端实际执行保持一致——不改 `clampGroupBudgetToCap` / `deriveGroupDefaultBudget` / 后端 / 配置 / 契约，零安全面影响。
- 复用既有纯函数（`clampGroupBudgetToCap` / `memberBudget`），不新增依赖；如抽 `isClamped`/`cap` 计算为纯函数，按 7-3 在 `autoTrade.test.ts` 补 4 条边界断言。

**理由（一句话）**：5% 硬上限是前后端一致、不可放松的真实资金护栏，用户报告的"预算不更新"本质是"静默夹紧 + 缺反馈"叠加的**认知缺陷**；方案 1 以最小、零风险的纯展示改动让夹紧可见，既满足"改总预算→各标的预算随之变化（上限内实时、超限内讲清原因）"的诉求，又不触碰护栏与后端口径，是唯一兼顾正确性与安全性的最小修复。

- （可选、非本 Bug 必修）策略面板单标的 `AutoTradeConfirmModal` 用的 `liveAuto.ts:39 clampBudgetToCap` 为**同源同上限（5%）的静默夹紧**，存在同类"无反馈"隐患；如顺手可对齐同一提示模式，但**不应纳入本 Bug 的最小修复范围**，以免扩大改动面。
