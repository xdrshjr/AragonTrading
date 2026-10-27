# 诊断报告：启动「实时自动交易」时未使用用户选中的策略，而是用了默认第一个

- **Bug slug**: `live-auto-trade-ignores-selected-strategy`
- **诊断节点**: Subtask #0（问题定位与分析）
- **诊断日期**: 2026-06-29
- **结论**: 已定位根因，证据充分（前端代码引用 + 规格书设计意图 + 提交时间线）。属**前端选择态解耦**缺陷，**后端无须改动**。

---

## 一、问题描述（Problem description）

在「实时」工作台（`mode === "paper"`）+「实盘」账户模式（`accountMode === "live"`）下，「交易策略」面板标题栏右上有「自动交易」开关。用户在策略列表里**选中（勾选）**某个策略后，点开「自动交易」开关弹出「启动实时自动交易」确认弹窗并确认启动时：

- 弹窗中「策略」一行显示的、以及实际下单所用的策略，**不是用户刚刚勾选的那个策略**；
- 而是固定回落到列表里的**默认第一个策略**。

由于自动交易会**用真实资金、无人值守地按该策略自动下单/平仓**，用错策略意味着真金白银按非预期逻辑成交，属高严重度问题。

---

## 二、复现步骤（Reproduction steps）

> 前提：已在「设置中心」配置好就绪默认实盘账户（含 Secret/Passphrase），且后端 `LIVE_AUTO_TRADING_ENABLED=true`、`LIVE_TRADING_ENABLED=true`（否则开关被守门禁用，无法触发）。纯 UI 复现可忽略真实账户，只观察弹窗「策略」一行显示。

1. 打开应用（桌面端或 Web），右侧面板切到「交易策略」页签。
2. 顶部账户模式置为「实盘」（`accountMode === "live"`，本项目默认即为 live），工作台模式置为「实时」（`mode === "paper"`）。
3. 此时策略列表每行左侧带一个**复选框**；首个策略默认处于「激活高亮 + 被自动勾选」状态（见根因 §3.4 的 seeding 逻辑）。
4. **只点击另一个策略（例如第 3 个）行内的复选框**把它勾上（保持不点击该行主体/不点其名字区域）；可顺手取消第一个的勾选。
5. 点击标题栏右上「自动交易」开关 → 弹出「启动实时自动交易」确认弹窗。
6. **观察**：弹窗中「策略」一行显示的是**第 1 个策略**的名字，而非你在第 4 步勾选的第 3 个。勾选「真实资金」复选框并点「确认并开始自动交易」后，后端实际用第 1 个策略启动会话。

**期望**：弹窗与启动应使用第 4 步用户勾选的第 3 个策略。

### 代码级最小复现（说明缺陷面，无需起服务）

把全局 store 的两个选择态人为置成「分叉」即可复现：

```ts
useAppStore.setState({
  accountMode: "live",
  strategyId: 7,            // 「激活/聚焦」策略（默认第一个）—— AutoTradeToggle 读它
  paperSelectionIds: [9],   // 用户实际勾选的策略 —— 复选框写它
  liveAutoSessionId: null,
});
// 渲染 <AutoTradeToggle/>，打开弹窗并确认
// 实测：liveAutoApi.start 被以 { strategyId: 7, ... } 调用，而非 9。
```

注：现有单测 `apps/web/components/Strategy/AutoTradeToggle.test.tsx:50-52,91-94` 直接 `setState({ strategyId: 7 })`、再断言 `start` 以 `strategyId: 7` 调用——它**从未经由复选框选择**，因此恰好「通过」，也正因此漏掉了本缺陷（见 §三-证据 E9）。

---

## 三、根因分析（Root cause analysis）

### 核心结论

「实时」模式下同时存在**两套互相独立、不同步的策略选择态**：

| 选择态 | 含义 | 由谁写入 | 谁来读 |
|---|---|---|---|
| `strategyId`（单值） | 「激活/聚焦」的单个策略（高亮、驱动下方 StrategyDetail） | **点击策略行主体** → `selectStrategyPassively` → `setStrategyId` | 自动交易（`AutoTradeToggle`）、回测、StrategyDetail |
| `paperSelectionIds`（数组） | 多选「实时模拟」要跑的策略集合 | **点击行内复选框** → `togglePaperSelectionId` | 多策略 paper 启动 |

「自动交易」启动链路读取的是 **`strategyId`**，而用户在「实时」面板里实际操作的「选中」是**复选框**（写 `paperSelectionIds`）。**勾选复选框不会更新 `strategyId`**，于是 `strategyId` 停留在它被 seeding/解析出来的初值——即**默认第一个策略**。这就是「用了默认的第一个」的直接成因。

### 证据链

**E1 — 自动交易启动用的是 `strategyId`（非用户勾选的复选框）。**
`apps/web/components/Strategy/AutoTradeToggle.tsx:24` 订阅 `strategyId`；启动 mutation 用它构造请求体：

```ts
// AutoTradeToggle.tsx:24
const strategyId = useAppStore((s) => s.strategyId);
// AutoTradeToggle.tsx:63-66
const startM = useMutation({
  mutationFn: (opts) =>
    liveAutoApi.start({ strategyId: strategyId as number, instId, bar, ... }),
```

弹窗「策略」一行显示的名字也由 `strategyId` 派生（`AutoTradeToggle.tsx:51-53` → `strategyName` → 传入 `AutoTradeConfirmModal`，见 `AutoTradeConfirmModal.tsx:103` 的 `<Row label="策略" value={strategyName} />`）。**全程未读取 `paperSelectionIds`。**

**E2 — 勾选复选框只写 `paperSelectionIds`，且阻断冒泡，不触达「激活」逻辑。**
`apps/web/components/Strategy/StrategyList.tsx:45-54`：复选框 `onChange` 调 `onToggleMultiSelect`，并 `onClick={(e) => e.stopPropagation()}`——刻意阻止冒泡到外层按钮的 `onClick`（行主体的 `onSelect`）：

```tsx
// StrategyList.tsx:30-32  外层按钮：点击行主体才会改「激活」
<button onClick={() => onSelect(s.id)} ...>
// StrategyList.tsx:46-54  复选框：只切多选，且 stopPropagation
<input type="checkbox" ... onChange={() => onToggleMultiSelect(s.id)}
       onClick={(event) => event.stopPropagation()} />
```

**E3 — 两个回调在 StrategyPanel 中分别绑定到两套 store 写函数。**
`apps/web/components/Strategy/StrategyPanel.tsx:490-498`：

```tsx
<StrategyList
  activeId={strategyId}
  onSelect={selectStrategyPassively}                 // → setStrategyId（写 strategyId）
  multiSelectIds={mode === "paper" ? paperSelectionIds : undefined}
  onToggleMultiSelect={mode === "paper" ? togglePaperSelectionId : undefined} // → 只写 paperSelectionIds
/>
```

`selectStrategyPassively` 仅改 `strategyId`（`StrategyPanel.tsx:107-114`）；`togglePaperSelectionId` 仅改 `paperSelectionIds`（`apps/web/lib/store.ts:297-302`）。两者**无任何联动**。

**E4 — `strategyId` 的初值就是「默认第一个」。**
`apps/web/lib/strategyCatalog.ts:29-40` 的 `resolveVisibleStrategyId`：当 `currentId` 为 `null` 或不在可见列表时，回落 `visible[0]?.id`（第一个）。`StrategyPanel.tsx:135-153` 的 effect 在策略加载后据此把 `strategyId` 落为第一个。store 初始 `strategyId: null`（`store.ts:206`）。即用户若从未点过任何策略**行主体**，`strategyId` 恒为列表第一个。

**E5 — seeding 进一步制造「看起来已选」的假象。**
`StrategyPanel.tsx:155-167`：进入 paper 模式且 `paperSelectionIds` 为空时，自动 `setPaperSelectionIds([strategyId])`——把**第一个**策略的复选框勾上。用户看到第一个被勾选，再去勾第二个/第三个（写 `paperSelectionIds`），但 `strategyId` 始终没动，于是自动交易仍用第一个。

**E6 — 守门门禁也只看 `strategyId`，所以开关「能开」但选错。**
`apps/web/lib/liveAuto.ts:22-28` 的 `resolveAutoTradeGate`：仅 `input.strategyId == null` 才禁用。因 `strategyId` 恒为第一个（非 null），守门放行，弹窗照常弹出——只是用错策略。

**E7 — 后端忠实使用前端传入的 `strategyId`，不是后端选错。**
`services/quant/app/api/live_auto.py:60-62`：`strategy_row = await repo.get_strategy_row(session, body.strategyId)`，按传入 id 取策略。故错误**完全发生在前端选择态**，后端无责、无须改动。

**E8 — 设计意图 vs 现实：规格书假设单选 `strategyId`，但多选复选框先它存在。**
规格 `docs/plans/live-auto-trading/spec.md` §一（第 61-63 行）写「实时模式下，用户在右侧策略面板**选中一个策略**……」，§2.1（第 76-78 行）守门列「**已选策略**」、确认后 `liveAutoApi.start({strategyId, ...})`——设计预设了「面板里有且仅有一个被选中的 `strategyId`」。但提交时间线显示：多策略 paper 复选框（`paperSelectionIds`）于 **ba057ce（2026-06-18）`feat: support multi-strategy paper simulation`** 引入，**早于**自动交易 **f0d44a7（2026-06-28）`feat: add live auto-trading mode`** 十天。即自动交易落地时，「实时」面板的主选择交互**早已是复选框**，但新功能仍沿用旧的单值 `strategyId`，从而与用户实际操作的复选框选择脱节。

**E9 — 既有单测从不经由复选框，故漏检本缺陷（回归护栏盲区）。**
`apps/web/components/Strategy/AutoTradeToggle.test.tsx:50-53` 在 `beforeEach` 直接 `setState({ strategyId: 7 })`，且 mock 的策略列表只有一个（`:23` 的 `api.strategies → [{ id: 7, name: "EMA 交叉剥头皮" }]`）；`:91-94` 断言 `start` 以 `strategyId: 7` 调用。**全程未设置、未触达 `paperSelectionIds`**，也不存在「多个可选策略 + 复选框分叉」的场景，故既有测试恰好「绿」，却完全没有覆盖「复选框选中 ≠ `strategyId`」这一真实交互。守门纯函数测试 `apps/web/lib/liveAuto.test.ts:11-31` 同理只覆盖 `strategyId` 维度（含 `strategyId: null → /选择/`），无「勾选 0 / 1 / >1 个」分支。这正是本缺陷得以上线的测试盲区。

### 一句话根因

> 自动交易启动读取「激活态」`strategyId`（仅「点击策略行主体」才更新，初值=列表第一个），而用户在实时面板里通过**复选框**表达选择（写 `paperSelectionIds`）；二者解耦、勾选复选框不更新 `strategyId`，于是自动交易恒用默认第一个策略。

---

## 四、影响面（Impact surface）

- **受影响功能**：实时自动交易（live-auto-trading）启动流程——确认弹窗「策略」显示 + 真实下单所用策略。
- **受影响用户**：所有在「实盘 + 实时」下用**复选框**选择策略后启动自动交易的用户（这是该面板最显眼、最自然的选择方式）。
- **后果严重度：高**。自动交易使用真实资金、无人值守下单/平仓；用错策略会按非预期信号在真实账户成交，造成直接经济损失，且与用户认知严重不符（弹窗显示的也是错的策略名，难以在确认前察觉——除非用户恰好注意到名字对不上）。
- **不受影响**：
  - 用户若**点击策略行主体**（而非仅勾复选框）使其高亮，`strategyId` 会正确更新，此路径下自动交易用对策略——但该交互不直观，多选面板里用户多倾向勾复选框。
  - 多策略「实时模拟」（paper）启动用的是 `paperSelectionIds`，不受影响。
  - 后端 `/live-auto/start` 逻辑正确（E7），无须改动。
  - 回测/对比等其它模式不涉及该解耦。

---

## 五、候选修复方案（Candidate fixes）

> 目标：让自动交易使用「用户在交易策略面板里实际选中的策略」，且改动面最小、最不破坏既有多策略 paper 行为。

| 方案 | 做法 | 侵入性 | 风险 | 工作量 | 备注 |
|---|---|---|---|---|---|
| **A（推荐）** 自动交易改读复选框选择 | `AutoTradeToggle` 计算「有效策略」：`paperSelectionIds` 恰为 1 个 → 用它；为 0 → 回落 `strategyId`/守门提示「请先选择一个交易策略」；>1 → 守门提示「自动交易仅支持单个策略，请只勾选一个」。`strategyName`、`start` 请求体、守门统一改用该有效 id | **低**：仅改 `AutoTradeToggle.tsx` + 守门入参（`liveAuto.ts`），不动列表/store/后端 | **低**：完全不碰多策略 paper 既有写路径 | 低 | 直接对齐用户「勾选=选中」的心智；与自动交易单会话独占（spec cap=1）天然契合 |
| **B** 复选框/行点击联动更新 `strategyId` | 让 `togglePaperSelectionId`（或 `StrategyList` 复选框）在勾选时一并把「激活」`strategyId` 指向该 id；自动交易仍读 `strategyId` | 中：改 store/`StrategyList`/`StrategyPanel` | **中**：会改变多策略 paper 体验——勾一个复选框就会跳动下方 StrategyDetail 焦点；取消勾选时「激活」回落语义含糊，可能回归既有 paper 流程 | 中 | 单一数据源更「干净」，但把多选与单聚焦耦合，破坏面比 A 大 |
| **C** 实盘场景下策略列表改单选 | 当 `accountMode === "live"` 时把策略列表渲染为单选（radio/仅激活、隐藏复选框），与 `strategyId` 唯一绑定；sim 仍多选 | 高：`StrategyPanel`/`StrategyList` 按 `accountMode` 分支渲染 | 中：把 `accountMode` 渗进列表渲染，需保证 sim paper 多选不受影响 | 高 | UX 最彻底（自动交易本就是单策略），但改动最大，超出「最小修复」 |

---

## 六、推荐方案（Recommended fix）

**推荐 A：让 `AutoTradeToggle` 以用户的复选框选择（`paperSelectionIds`）为准来解析自动交易的目标策略。**

理由：

1. **直接命中根因与用户心智**：用户报告的「选中的策略」就是复选框勾选（中文「选中」≈ 勾选）。A 让自动交易读复选框选择，弹窗显示与实际下单立即与用户操作一致。
2. **破坏面最小（满足「改动尽量小 / 最小化破坏面」）**：只触及自动交易自身路径（`AutoTradeToggle.tsx` + `resolveAutoTradeGate` 入参），**完全不动** `StrategyList`、`store` 的多选写逻辑与后端，故不会回归已上线、已测试的多策略 paper 流程；后端 E7 证实无须改。
3. **与单会话独占一致**：自动交易后端 `live_auto_max_sessions=1`（spec §2.1）。A 对「勾选 >1 个」给出明确守门文案「仅支持单个策略」，把 UI 选择语义与后端单会话约束对齐，而非默默取某一个。
4. **可即时加测试护栏**：补一条单测——`strategyId` 与 `paperSelectionIds` 分叉（如 `strategyId:7, paperSelectionIds:[9]`）时，启动应以 `9` 调 `liveAutoApi.start`，并覆盖「0 个/多个勾选」的守门分支，把本回归钉死（现有 `AutoTradeToggle.test.tsx` 的盲区见 E9）。

**实施要点（供 Subtask #2）**：
- 在 `AutoTradeToggle` 读 `paperSelectionIds`，派生 `effectiveStrategyId`（恰 1 个→取之；0 个→回落 `strategyId`；>1→视为未就绪并给守门文案）。
- `strategyName`、`startM` 请求体、`resolveAutoTradeGate` 一律改用 `effectiveStrategyId`（守门新增「>1 个」分支文案）。
- 不改 `StrategyList`/`store`/后端；补上述分叉单测。

> 备选：若产品更希望「点击行主体高亮即选中」的单值语义（规格原意），可在 A 落地后另行评估 C 作为体验优化，但不应纳入本次最小修复。

---

## 附：关键引用清单

- `apps/web/components/Strategy/AutoTradeToggle.tsx:24`（读 `strategyId`）、`:51-53`（派生 `strategyName`）、`:56-61`（守门）、`:63-66`（`start` 用 `strategyId`）、`:147`（传 `strategyName` 给弹窗）
- `apps/web/components/Strategy/AutoTradeConfirmModal.tsx:103`（弹窗「策略」一行显示 `strategyName`）
- `apps/web/components/Strategy/StrategyList.tsx:30-32`（行主体 `onSelect`）、`:45-54`（复选框 `onToggleMultiSelect` + `stopPropagation`）
- `apps/web/components/Strategy/StrategyPanel.tsx:107-114`（`selectStrategyPassively`→`setStrategyId`）、`:135-153`（解析 `strategyId` 为第一个）、`:155-167`（seeding `paperSelectionIds`）、`:490-498`（两套回调绑定）
- `apps/web/lib/store.ts:206`（`strategyId` 初值 null）、`:261`（`setStrategyId`）、`:297-302`（`togglePaperSelectionId` 仅写 `paperSelectionIds`）
- `apps/web/lib/strategyCatalog.ts:29-40`（`resolveVisibleStrategyId` 回落第一个）
- `apps/web/lib/liveAuto.ts:22-28`（`resolveAutoTradeGate` 仅看 `strategyId`）
- `services/quant/app/api/live_auto.py:60-62`（后端按 `body.strategyId` 取策略，证实后端无责）
- `docs/plans/live-auto-trading/spec.md` §一(61-63)、§2.1(76-78)（设计预设单值 `strategyId`）
- 提交时间线：`ba057ce`(2026-06-18 多策略 paper) 早于 `f0d44a7`(2026-06-28 自动交易)
- `apps/web/components/Strategy/AutoTradeToggle.test.tsx:50-53,91-94`（现有测试盲区：直接 set `strategyId`，从不经复选框，且 mock 仅单策略）、`apps/web/lib/liveAuto.test.ts:11-31`（守门测试仅覆盖 `strategyId` 维度，无勾选数分支）

---

## 评审结论（Review Verdict）

- **评审节点**: Subtask #1（分析报告评审与修复）
- **评审日期**: 2026-06-29
- **评审人**: 资深评审（Anthropic 工程团队）
- **总体结论**: **根因证据链成立、可判定为 airtight；推荐方案维持 A，并按下文收紧边界与测试口径。** 仅前端修复，后端无须改动。

### 一、根因证据复核（逐条独立核验）

我已回到源码 / 规格 / git 历史对 E1–E9 逐条独立核验，结论一致，未发现反例或被遗漏的替代成因：

| 证据 | 核验点 | 结论 |
|---|---|---|
| E1 | `AutoTradeToggle.tsx:24/52-53/63-66/147` 全程只读 `strategyId`，弹窗策略名经 `AutoTradeConfirmModal.tsx:103` 渲染；无任一处读 `paperSelectionIds` | ✅ 属实 |
| E2 | `StrategyList.tsx:30-32` 行主体 `onSelect`；`:45-54` 复选框 `onChange→onToggleMultiSelect` 且 `onClick stopPropagation`，刻意不冒泡到行主体 | ✅ 属实 |
| E3 | `StrategyPanel.tsx:490-498` 两回调分绑 `selectStrategyPassively`(:107-114→`setStrategyId`) 与 `togglePaperSelectionId`(`store.ts:297-302` 仅写数组)，二者无联动 | ✅ 属实 |
| E4 | `strategyCatalog.ts:36-39` 在 `currentId` 为 null / 不可见时回落 `visible[0]?.id`；`store.ts:206` 初值 null；`StrategyPanel.tsx:135-153` effect 落首个 | ✅ 属实 |
| E5 | `StrategyPanel.tsx:155-167` paper 模式 `paperSelectionIds` 为空时 seed `[strategyId]`（=首个），制造「首个已勾」假象 | ✅ 属实 |
| E6 | `liveAuto.ts:22-28` 守门仅判 `strategyId == null`，`strategyId` 恒非 null → 放行但选错 | ✅ 属实 |
| E7 | `live_auto.py:60-62` 后端忠实按 `body.strategyId` 取策略，错误全在前端选择态 | ✅ 属实，后端无责 |
| E8 | 规格 `spec.md` §一(61)/§2.1(76-78) 预设单值 `strategyId`；`paperSelectionIds` 引入于 `ba057ce`(2026-06-18)，早于自动交易 `f0d44a7`(2026-06-28) 10 天 | ✅ git 历史核对一致 |
| E9 | 既有单测从不经复选框、且 mock 仅单策略，守门测试无勾选数分支，故漏检本缺陷 | ✅ 属实 |

**执行路径确认**：`<AutoTradeToggle>` 仅在 `mode==="paper"` 渲染（`StrategyPanel.tsx:440`），其守门要求 `accountMode==="live"`；用户在该面板的主选择交互即复选框（写 `paperSelectionIds`）。启动链路读 `strategyId`，与复选框写入解耦——**症状（弹窗「策略」显示 + 实际下单恒为首个）由该解耦完全解释**。我另排查两类潜在反例并排除：(a) 全仓未见任何 effect 把 `paperSelectionIds` 同步回 `strategyId`（仅 E5 反向 seed）；(b) `startM` 的 `mutationFn` 也不读 `paperSelectionIds`（`AutoTradeToggle.tsx:63-73`）。证据链闭合。

### 二、候选方案复核

- **方案 A（自动交易改读复选框选择）**：破坏面最小，只触 `AutoTradeToggle.tsx` 与守门入参，**不动** `StrategyList`/`store` 多选写路径与后端，故不回归已上线的多策略 paper 流程（其启动走 `validPaperSelectionIds`，`StrategyPanel.tsx:294-319`，与本改动无交集）。与后端单会话独占 `cap=1`（`spec.md §2.1`）天然对齐。**采纳。**
- **方案 B（复选框联动 `strategyId`）**：把多选与单聚焦耦合，勾选即跳动 `StrategyDetail` 焦点、取消勾选时「激活」回落语义含糊，破坏面大于 A 且需改 `store`/`StrategyList`。**否决**（非最小修复）。
- **方案 C（实盘下列表改单选）**：UX 最彻底但需按 `accountMode` 分支渲染列表，工作量与回归面最大，超出本次最小修复边界。**否决**，可作 A 落地后的体验优化另行评估。

### 三、对方案 A 的边界收紧（交付 Subtask #2 的硬性约束）

原文 Fix A 对「0 个勾选」给出「回落 `strategyId` 或守门」二选一。评审定稿如下，消除歧义并杜绝缺陷在边界态复活：

1. **有效策略口径**：`effectiveStrategyId = (paperSelectionIds 恰为 1) ? paperSelectionIds[0] : null`。
   - **0 个勾选 → 不回落 `strategyId`，直接守门**「请先选择一个交易策略」。理由：用户点「清空全部」(`StrategyPanel.tsx:465`) 后 `paperSelectionIds=[]` 但 `strategyId` 仍停在首个；若回落即把刚修掉的 bug 在该态复活。这也与 paper 启动自身的「请选择至少一个策略」守门（`StrategyPanel.tsx:410-415`）口径一致。
   - **>1 个勾选 → 守门**「自动交易仅支持单个策略，请只勾选一个」，与后端 `cap=1` 对齐，而非默默取某一个。
2. **统一改用 `effectiveStrategyId`**：`strategyName` 派生、`startM` 请求体、`resolveAutoTradeGate` 入参三处一律改用之。`resolveAutoTradeGate` 须扩展入参（以可空 `effectiveStrategyId` + `selectedCount`，或等价信号取代现 `strategyId`）以表达「0 / >1」两类守门文案；其唯一非测试调用方为 `AutoTradeToggle.tsx:56`，改动面可控。
3. **数据有效性**：`AutoTradeToggle` 直接订阅原始 `paperSelectionIds` 即可——`StrategyPanel.tsx:245-249` 已有 effect 持续把它剪除为合法可选集（`validPaperSelectionIds`），且 `AutoTradeToggle` 必随 `StrategyPanel` 同时挂载，故无需在开关内重复过滤；派生 `strategyName` 时既有 `?? ""` 兜底（`AutoTradeToggle.tsx:52-53`）已对脏 id 安全。
4. **不改**：`StrategyList`、`store` 多选写逻辑、后端 `live_auto.py`。

### 四、必须同步更新的测试（防止再次「绿着漏」）

方案 A 改了 `resolveAutoTradeGate` 签名与启动取值口径，以下测试**必须**随改并新增分叉用例：

- `apps/web/components/Strategy/AutoTradeToggle.test.tsx`：现有 `beforeEach` 只 `setState({ strategyId: 7 })`、mock 单策略——应改为提供 ≥2 个可选策略并 `setState({ paperSelectionIds: [9] })`，断言 `start` 以 `9` 调用；新增「勾 0 个 → 守门、不弹窗」「勾 >1 个 → 守门」「`strategyId:7` 与 `paperSelectionIds:[9]` 分叉 → 用 9」三条（即「§二·代码级最小复现」的回归钉）。
- `apps/web/lib/liveAuto.test.ts`：为新签名补「selectedCount=0 → /选择/」「selectedCount>1 → /单个/」两条守门分支。

### 五、最终推荐（单一结论）

> **采纳方案 A：让 `AutoTradeToggle` 以「复选框选择」(`paperSelectionIds`) 解析自动交易的目标策略——恰选 1 个时用之，0 或 >1 时按 §三 文案守门拦截；`strategyName`、`startM` 请求体、`resolveAutoTradeGate` 一律改用该有效 id。不改 `StrategyList`/`store`/后端，并同步补齐 §四 的分叉与守门测试。**

理由：A 直接命中根因（复选框写 `paperSelectionIds`、启动读 `strategyId` 的解耦）与用户心智（中文「选中」≈勾选），破坏面最小且与后端单会话约束一致；§三 的边界收紧消除了「0 个勾选回落首个」这一会让缺陷复活的暗角；§四 的测试把本回归永久钉死。后端经 E7 证实无责，不在本次改动范围。

— 评审通过，可进入 Subtask #2 实施。
