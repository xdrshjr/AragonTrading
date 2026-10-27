# 顶栏「模拟盘 / 实盘」分段开关「仍无法有效切换」— 问题定位与分析

- **Bug slug**: `sim-live-account-mode-toggle-cannot-switch`
- **诊断节点**: Subtask #0（问题定位与分析，本节点**不写修复代码**）
- **日期**: 2026-06-29
- **本轮来源**: 多轮迭代第 3 轮（在第 2 轮 `8be958a`「restore header right-cluster clicks…」之上继续修改）
- **结论先行（单一根因）**:
  > 第 3 轮报障**只针对「模拟盘 / 实盘」这一个控件**（不再提主题 / 设置 / 窗口键，也不再提自动交易开关）。这是**决定性信号**：拖拽区 / caption 命中缺陷会让一**整片连续几何区域**失效，**不可能**单独吞掉「模拟盘/实盘」却放过紧邻的「主题」按钮。故本轮**不是**第 2 轮那条「还原态拖拽命中」几何根因的复发，而是一条**一直存在、被几何症状掩盖、直到第 2 轮 A1 让顶栏右侧恢复可点后才暴露**的**前端切换逻辑根因**：
  >
  > `AccountModeSwitch.selectLive`（`apps/web/components/Header/AccountModeSwitch.tsx:43-51`）**只有在 `hasReadyDefault` 为真时才 `setAccountMode("live")`**；否则**静默改为打开「设置中心」**而**不切换**。叠加挂载期水合守门（`:33-36`）在 `accountMode==="live" && !hasReadyDefault` 时**强制回落 `sim`**。而 `hasReadyDefault`（`:28-30`）把**三种本不同的状态**——① `/accounts` 查询**尚未返回**、② 查询**失败**、③ **确无就绪默认账户**——**一律折叠为「禁止进实盘」**。于是**只要账户查询未成功落到「带 secret+passphrase 的默认账户」**，点「实盘」就**永远切不过去**（要么弹设置、要么被守门弹回），分段开关**始终停在「模拟盘」**。
  >
  > git 佐证：`AccountModeSwitch.tsx` 最近改动是 `4f72c39` / `660895a`，**第 1/2 轮的 `f0d44a7` / `a0b7b87` / `8be958a` 从未触碰它**——印证「切换逻辑自始未动、是被掩盖的旧根因」。

---

## 一、问题描述（Problem description）

第 3 轮用户报障（原文）：

> 没修改好，继续修改：
> 1 模拟盘/实盘 按钮，仍然无法进行有效切换

拆解关键词：

- **范围收敛**：第 2 轮报「整个 header 右侧无法点击」+「自动交易开关没反应」两个症状；本轮**只剩「模拟盘/实盘」这一个**，且措辞从「**无法点击**」变为「**无法进行有效切换**」。
- **「有效切换」≠「能点」**：用户表达的是「我点了，但它没有从模拟盘切到实盘 / 切不动」，而非「按钮像死的一样按不下去」。这与「点击根本不下发」（几何/拖拽）是**不同层面**的故障。

> **诊断学含义（本报告的支点）**：第 2 轮 A1（删中部 `flex-1` 拖拽面，`page.tsx:546`）若**已让顶栏右侧恢复可点**，则与「模拟盘/实盘」并排的**主题 / Factor / 设置齿轮 / 窗口键**也应一并恢复——用户本轮**不再抱怨它们**，恰说明它们已可用。**唯独**「模拟盘/实盘」仍「切不动」。一个**功能性控件单独失效、而其几何邻居正常**，**在物理上排除了拖拽/caption 几何根因**（几何缺陷按坐标吞一整片，不会按控件语义挑一个），只能是**该控件自身的切换逻辑**问题。

---

## 二、复现步骤（Reproduction steps）

### 2.0 端口与代理（供下文 curl 取证）

- 桌面后端固定 `18760`、前端 `18761`（`desktop/src/lib/config.js:8-9`）。
- 前端 `/api/py/:path*` 经 Next rewrite 转发到后端 `/api/:path*`（`apps/web/next.config.mjs:18-19`）。
- 账户列表：`accountApi.accounts.list` → `GET /api/py/accounts`（`apps/web/lib/accountApi.ts:12,52`）。

### 2.1 主复现（切换逻辑根因，端到端）

1. 启动桌面端任一形态：
   - 生产态：运行打包产物 `Aragon Trading.exe`；
   - 开发态：`desktop/` 下 `npm run dev`（窗口默认 1440×900 还原态，`desktop/src/main.js:214-215`）。
2. **置触发条件**（三者任一即可，均使 `hasReadyDefault=false`）：
   - (a) **未配置默认实盘账户**：设置中心未新增账户，或新增了但**未设为默认**（`defaultId` 为 `null`）；
   - (b) 默认账户存在但**缺 secret / passphrase**（`hasSecret` 或 `hasPassphrase` 为 `false`）；
   - (c) **后端 `/accounts` 查询未成功**：打包后端尚未就绪 / 不可达 / 报错（此时查询恒非 success）。
3. 单击顶栏右侧「**实盘**」分段。→ **观察到**：分段**不**高亮切到「实盘」；而是**弹出「设置中心 · 实盘账户」页**（条件 a/b 命中 `selectLive` 的 else 分支），或**毫无切换地停在「模拟盘」**。反复点击「实盘」只会反复（重）开设置，分段**永远显示「模拟盘」为选中**。
4. 即便用别的途径让 `accountMode` 短暂变 `live`（如持久化恢复，默认初值本就是 `live`，`store.ts:218`），挂载期水合守门（`AccountModeSwitch.tsx:33-36`）会在 `!hasReadyDefault` 时**立刻 `setAccountMode("sim")`**，分段被**弹回「模拟盘」**。

### 2.2 无 GUI 取证（确认是否命中「`hasReadyDefault=false`」）

```bash
# 经前端代理（推荐，与渲染层取数完全同源同口径）
curl -s http://127.0.0.1:18761/api/py/accounts
# 或直连后端
curl -s http://127.0.0.1:18760/api/accounts
```

判读（命中本 bug 的典型回包）：
- `{"accounts":[...],"defaultId":null}` → **无默认**（条件 a）；
- 默认账户对象 `"hasSecret":false` 或 `"hasPassphrase":false` → **凭证不全**（条件 b）；
- **请求超时 / 连接拒绝 / 非 2xx** → 查询恒失败（条件 c），`accountsQ.isSuccess` 永不为真 → `hasReadyDefault` 恒 `false`。

三种回包**都会**让「实盘」切不过去——这正是根因「三态被折叠」的直接证据（见 §3.2）。

### 2.3 竞态变体（即便已配好默认账户也会偶发「切不动」）

`selectLive` 在**点击当下同步读取** `hasReadyDefault`；而 `/accounts` 是异步查询。**在启动后查询落地前的窗口期**（打包后端冷启动较慢时尤甚），`hasReadyDefault` 仍为 `false` → 此刻点「实盘」**照样弹设置**，给已配好账户的用户造成「时灵时不灵」的「切不动」。守门 `:33-36` 用 `if (!accountsQ.isSuccess) return` 规避了加载期误回落，但 **`selectLive` 没有同样的加载期保护**（见 §3.2(3)）。

### 2.4 排除几何根因的判别步（务必执行，决定走哪条修复）

在**同一还原态窗口**里，依次单击顶栏右侧的「**主题**（暗色/亮色）」与「**设置齿轮**」：
- **若二者均有反应** → 顶栏右侧可点（第 2 轮 A1 已生效）→ 「模拟盘/实盘」切不动是**本报告的切换逻辑根因**，按 §五修。
- **若二者也无反应** → A1 未根治、**几何根因仍在**，属另一条 bug（见 `docs/diagnoses/desktop-header-right-and-auto-trade-toggle-unresponsive/`），本报告的逻辑修复**不适用**，应转向该诊断的 A3（Windows Controls Overlay）路线。

> 本报告基于「用户本轮只抱怨这一个控件、不再抱怨其几何邻居」这一**强排他信号**，判定第一类（切换逻辑）为现实根因；§2.4 把该前提固化为**可一键证伪的验收前置**。

---

## 三、根因分析（Root cause analysis）

### 3.1 切换入口：「实盘」侧**没有无条件的切换路径**

`apps/web/components/Header/AccountModeSwitch.tsx`：

```ts
// :38-41  模拟盘：永远能切
const selectSim = () => {
  setAccountMode("sim");
  setMode("paper");
};

// :43-51  实盘：仅 hasReadyDefault 才切；否则改为「打开设置」——不切换
const selectLive = () => {
  if (hasReadyDefault) {
    setAccountMode("live");
    return;
  }
  // 未配置默认账户：引导去设置中心实盘账户页，而非静默进入实盘。
  setSettingsSection("okx-accounts");
  setShowSettings(true);
};
```

- 「模拟盘」侧 `selectSim` **无条件**切换 → 故用户在 `sim` 上点「模拟盘」**看不到任何变化**（本就选中），点「实盘」又切不过去 → 主观「这个分段开关是死的」。
- 「实盘」侧 `selectLive` 的 else 分支**没有任何可见的切换反馈**：它只是 `setShowSettings(true)`。从「分段开关」角度看，**控件状态没变**（仍 `sim`），用户得到的是「点了实盘 → 跳去设置 → 开关没动」，即「**无法有效切换**」。

### 3.2 `hasReadyDefault` **把三种状态折叠成一个「禁止」**（核心机理）

```ts
// :27       账户列表查询
const accountsQ = useQuery({ queryKey: ["okx","accounts"], queryFn: accountApi.accounts.list });
// :28-30    默认账户 + 就绪判定
const defaultAccount =
  accountsQ.data?.accounts.find((a) => a.id === accountsQ.data?.defaultId) ?? null;
const hasReadyDefault = !!defaultAccount && defaultAccount.hasSecret && defaultAccount.hasPassphrase;
```

`hasReadyDefault` 为 `false` 的**三条互不相同的来路**被一视同仁：

| # | 真实状态 | `accountsQ` | `hasReadyDefault` | 合理的产品反应 | 现状反应 |
|---|---|---|---|---|---|
| ① | 查询**尚未返回**（启动窗口期 / 冷启动） | `isPending` | `false` | 等待 / 允许乐观切换 | **弹设置**（错误） |
| ② | 查询**失败**（后端不可达 / 4xx-5xx） | `isError` | `false` | 报错并允许重试 | **静默弹设置**（错误、且无错误提示） |
| ③ | **确无**就绪默认账户 | `isSuccess` | `false` | 引导去设置（合理） | 弹设置（合理） |

只有 ③ 该弹设置；①②被**误当作 ③**。在打包桌面端，后端冷启动 + 代理链路使 ①②**并不罕见**，导致**即便用户已配好默认账户**也会遭遇「点实盘没切过去」（§2.3 竞态）。

### 3.3 挂载期水合守门**进一步把任何 `live` 态弹回 `sim`**

```ts
// :33-36  账户数据就绪后，若停在实盘态却无可用默认账户，回落模拟盘
useEffect(() => {
  if (!accountsQ.isSuccess) return;
  if (accountMode === "live" && !hasReadyDefault) setAccountMode("sim");
}, [accountsQ.isSuccess, accountMode, hasReadyDefault, setAccountMode]);
```

- store **默认初值就是 `accountMode: "live"`**（`apps/web/lib/store.ts:218`，注释「本轮需求：实盘模式默认开启」），且 `migrate` 亦补 `accountMode:"live"`（`:542`），并入 `partialize` 持久化（`:455`）。
- 故每次挂载都有一帧 `live`；一旦 `accountsQ` 成功且 `!hasReadyDefault`，该守门**立即把它打回 `sim`**。这是「实盘怎么都停不住、总是弹回模拟盘」的第二道闸：`selectLive` 不让进（§3.1/3.2），守门把残留的 `live` 也清掉。
- 注意该守门**有** `isSuccess` 前置保护（加载期不误伤），但 `selectLive` **没有**——两者保护不对称，正是 §2.3 竞态的来源。

### 3.4 为何「本轮只此一个控件失效」== 排除几何根因（再次强调，含 git 佐证）

- **物理排他**：`-webkit-app-region` 的可拖区是**按内容坐标算出的矩形并集差集**，命中失效作用于**连续几何带**，不可能「按钮 A（实盘）吞、紧邻按钮 B（主题）放」。本轮主题/设置/窗口键不再被抱怨 → 几何层已正常 → 残留故障必在**控件语义层**。
- **改动史佐证**：`git log -- apps/web/components/Header/AccountModeSwitch.tsx` 仅 `4f72c39`、`660895a`；第 1 轮 `f0d44a7`/`a0b7b87`、第 2 轮 `8be958a` **均未触碰**该文件（已实测 `git show --stat` 三个提交，无 `AccountModeSwitch`）。即**切换逻辑自实盘特性落地以来从未改动**——它**不是本轮新引入**，而是**一直被「整片右侧不可点」掩盖**；第 2 轮 A1 揭开几何遮罩后，这条旧逻辑根因**首次裸露**给用户。

### 3.5 排除项（已证伪 / 已排除）

- **非覆盖层遮挡**：`SimBalanceField` 仅在 `accountMode==="sim"` 时渲染、且排在分段**之后**（右侧，`AccountModeSwitch.tsx:95`），不压在两个分段按钮上；`Modal` 关闭即 `return null`（`apps/web/components/ui/index.tsx`，第 2 轮已核实无常驻遮罩）。
- **非 `setAccountMode` 自身缺陷**：`store.ts:284-285` `setAccountMode` 仅 `set(...)` 落状态，`live` 时联动 `chartView`，无任何拦截；`sim` 路径同理。**逻辑闸全在组件层**（`selectLive` + 守门），store 无辜。
- **非自动交易开关问题**：本轮用户**未**提自动交易开关，且第 2 轮 B（`config.js` 注入 `LIVE_AUTO_TRADING_ENABLED` + `AutoTradeToggle` 就地原因）已落地（`8be958a`）。自动交易是**下游**——其守门第 1 条仍是 `accountMode==="live"`（`apps/web/lib/liveAuto.ts`），故本 bug **会连带**让自动交易不可达，但它**不是**本轮根因。

---

## 四、影响面（Impact surface）

| 维度 | 说明 |
|---|---|
| 平台 | **全平台（含纯 Web）**——纯前端组件逻辑，与 OS / 无边框 / 还原态**无关**（区别于第 2 轮的「Windows 还原态」几何根因，那条是平台相关的） |
| 触发条件 | `/accounts` 查询**未成功落到「带 secret+passphrase 的默认账户」**：①加载中 ②失败 ③确无默认账户——三者任一即复现 |
| 受影响功能 | 整条**实盘链路入口**：无法进入 `accountMode==="live"` → `ChartWorkspace` 不渲染 `LiveBottomPanel`（实盘下单/成交/持仓面板）；**连带**使第 1 轮的实时自动交易（守门要求 `live`）100% 不可达 |
| 严重度 | **高**——实盘 / 实盘自动交易是第 1 轮交付的核心新特性，本 bug 使其在「未配默认账户 / 后端慢/抖动」时整体不可达，且**无任何可见原因**（静默弹设置 / 静默弹回） |
| 体感放大 | 切换**无可见失败反馈**：点「实盘」→ 要么跳设置、要么没反应，用户无从知道「因为没有就绪默认账户」——故主观判定为「按钮坏了 / 切不动」 |
| 误诊风险 | 极易被**再次误判为几何/拖拽根因**（前两轮已两次走该路线）。§2.4 判别步用于**一键证伪**该误诊 |

---

## 五、候选修复方案对比（Candidate fixes）

> 本节仅比较，**不写修复代码**（留给下游修复节点）。三方案均落在 `apps/web/components/Header/AccountModeSwitch.tsx`（F2 另涉 Live 面板既有禁用逻辑，已存在，无需新增）。

| 方案 | 做法 | 侵入性 | 风险 | 工作量 | 是否根治 |
|---|---|---|---|---|---|
| **F1（推荐）三态解耦 + 可见反馈** | (i) `selectLive` 的「弹设置」**仅在 `accountsQ.isSuccess && !hasReadyDefault`（确无账户=状态③）** 时触发；(ii) 状态①（pending）点击给「正在校验账户…」轻提示或暂置 pending，状态②（error）**就地/Toast 报错并提供重试**，**不**再静默跳设置；(iii) 弹设置/拦截时**给一行可见中文原因**（对齐第 2 轮 B2「就地原因」哲学）。守门 `:33-36` 维持，但与 `selectLive` 取**同一** `isSuccess` 前置，消除 §2.3 竞态。 | 低（单文件、~15 行） | 低（仍不无凭证进实盘；只是把「静默」改「可见」、把误折叠的①②分流） | 低 | 是——直击「三态折叠 + 无反馈」，覆盖竞态 |
| **F2 进实盘无条件、下游守门下单** | `selectLive` **总是** `setAccountMode("live")`；放宽/移除水合守门强制回落；把「无就绪默认账户」的约束**下沉到 `LiveTradePanel`**（按 [[aragon-live-trading-mode]] 记载，`liveTradingEnabled=false` 本就**不回落 sim**、而是面板内禁用下单 + 横幅提示，读类仍可用）。与该既定 spec 哲学（I-1/I-2/I-3）一致。 | 中（改切换契约 + 守门语义） | 中（需确保「无账户进实盘」绝不放行真实下单；依赖 Live 面板禁用闸的完备性；改既定「入口守门」契约面较大） | 中 | 是，但语义改动面更大、需回归实盘下单禁用闸 |
| **F3 最小：仅补可见反馈** | 不改切换条件，仅在 `selectLive` 弹设置/拦截时加 Toast/就地文案，并让账户查询错误冒泡可见。 | 极低 | 无 | 极低 | 否——**不修**竞态①②（已配账户者启动窗口期仍切不动），只缓解「为何没反应」体感 |

---

## 六、推荐方案（Recommended fix）

> **F1：在 `apps/web/components/Header/AccountModeSwitch.tsx` 内把 `hasReadyDefault` 的三态解耦——「确无账户(success && 无就绪默认)」才引导去设置，「加载中」给等待提示、「失败」就地报错可重试，且每种拦截都给一行可见中文原因；`selectLive` 与水合守门统一加 `accountsQ.isSuccess` 前置以消竞态。**

**理由（凝练）**：
1. **机理对位、最小且单文件**：根因是「`selectLive` 无条件切换缺位 + `hasReadyDefault` 三态折叠 + 守门不对称保护 + 切换无可见反馈」，全部集中在该组件 `:27-51`。F1 逐条对位，改动 ~15 行、纯前端、Web/桌面同修，且**不放松安全姿态**（仍不允许「无就绪默认账户」静默进入可下单实盘）。
2. **覆盖竞态、解释「时灵时不灵」**：把 `selectLive` 也加上 `isSuccess` 前置（与守门 `:35` 对齐），消除「启动窗口期点实盘被误弹设置」——这正是已配账户用户「偶发切不动」的来路。
3. **延续第 2 轮 B2 哲学**：第 2 轮已确立「守门拦截要**就地可见**、不可静默」（`AutoTradeToggle` 就地原因）。本 bug 的「静默弹设置 / 静默弹回」与该哲学冲突；F1 将其补齐，对未来同类「点不动」报障也是护栏。
4. **F2 留作后续演进**：若产品希望「实盘可先进、下单再守门」（与 `liveTradingEnabled` 既有 spec 一致），F2 更彻底，但改切换契约面更大、需回归 Live 面板下单禁用闸，**本轮不必**；F3 不修竞态，**否决为单独方案**（可并入 F1）。

**验收对齐（交付下游修复节点的硬性口径）**：
1. **判别前置（§2.4）**：同一还原态窗口里「主题 / 设置齿轮」可点——确认本轮是逻辑根因而非几何复发；否则转 `desktop-header-right-and-auto-trade-toggle-unresponsive` 的 A3 路线。
2. **状态③（确无默认账户）**：点「实盘」→ 引导去设置中心实盘账户页，**且顶栏就地显示一行可见中文原因**（如「请先在设置中配置默认实盘账户」），而非静默。
3. **状态①（加载中）/②（失败）**：点「实盘」**不再**误跳设置；①给等待态、②就地可见错误并可重试。
4. **状态「已就绪默认账户」**：点「实盘」**稳定切到 `live`**（分段高亮、`ChartWorkspace` 渲染 `LiveBottomPanel`），点「模拟盘」稳定切回；**启动窗口期点击不再被误弹设置**（竞态修复）。
5. **范围守卫**：仅改 `apps/web/components/Header/AccountModeSwitch.tsx`；**不得带入** `desktop/dist/`、`tmps/`、`.next/` 等构建/临时产物；纯 Web `pnpm dev` 与桌面打包态均不回归。

---

## 附录：关键证据索引

- 切换入口与守门（根因主体）：`apps/web/components/Header/AccountModeSwitch.tsx:27`（accountsQ）、`:28-30`（defaultAccount / **hasReadyDefault 三态折叠**）、`:33-36`（**水合守门强制回落 sim**）、`:38-41`（selectSim 无条件切）、**`:43-51`（selectLive 仅就绪才切、否则静默弹设置）**、`:60-93`（两个分段按钮 `aria-checked=accountMode===…`）、`:95`（SimBalanceField 仅 sim、排在分段之后，不遮挡）。
- store 默认/持久化/写入：`apps/web/lib/store.ts:218`（**默认 `accountMode:"live"`**）、`:284-285`（`setAccountMode` 无拦截）、`:455`（partialize 含 accountMode）、`:542`（migrate 补 live）。
- 改动史佐证（逻辑根因自始未动、被几何症状掩盖）：`git log -- apps/web/components/Header/AccountModeSwitch.tsx` = `4f72c39` / `660895a`；`git show --stat f0d44a7 / a0b7b87 / 8be958a` 均**无** `AccountModeSwitch`。
- 几何根因排他对照（第 2 轮 A1 已落地，右侧应已可点）：`apps/web/app/page.tsx:546`（A1 后中部 `flex-1` 已去 `drag`）、`:547-555`（右簇 `no-drag` 含 `AccountModeSwitch`/主题/Factor/设置/窗口键）；前序诊断 `docs/diagnoses/desktop-header-right-and-auto-trade-toggle-unresponsive/analysis.md`。
- 取数链路（复现取证）：`apps/web/lib/accountApi.ts:12,52`（`GET /api/py/accounts`）、`apps/web/next.config.mjs:18-19`（`/api/py/* → 后端 /api/*`）、`desktop/src/lib/config.js:8-9`（后端 18760 / 前端 18761）。
- 下游耦合（连带影响）：`apps/web/components/Strategy/AutoTradeToggle.tsx:40-41,56-61`（自动交易守门同读 `hasReadyDefault` + 第 1 条要求 `accountMode==="live"`）、`apps/web/lib/liveAuto.ts:22-28`（`resolveAutoTradeGate` 守门四条，第 1 条即 `accountMode !== "live"`）。

### 附录·评审补充证据（Reviewer-added citations，本次评审实测补入）

> 以下为评审节点实测补入，用于把原文证据链「补到滴水不漏」，均已逐条核对。

- **「store 无辜 / 切换闸全在组件层」已用全仓 grep 坐实**：`git grep "setAccountMode"` 全仓**仅** `AccountModeSwitch.tsx` 三处运行期调用（`:35` 守门、`:39` selectSim、`:45` selectLive）；其余命中均为类型声明（`store.ts:148`）、纯落状态实现（`store.ts:284-285`）、或**测试**（`store.test.ts`、`AutoTradeToggle.test.tsx:66`）。**无任何其他运行期组件改写 `accountMode`**。
- **`store.ts:491` / `:542` 是一次性迁移、非运行期回落**：`:491`（`version < 5`）设 `accountMode:"sim"`、`:542`（`version < 16`）设 `accountMode:"live"`，二者同处 `migrate` 链、**仅水合时按版本顺序各跑一次**（旧用户最终落 `live`），**不是**持续运行的回落器；唯一的**持续**运行期闸是组件内 `useEffect` 水合守门（`:33-36`）。故原文「store 无辜」成立。
- **加载/竞态窗口被 `retry:1` 放大（强化 §2.3 / 状态①）**：全局 QueryClient `apps/web/app/providers.tsx:13` 设 `retry: 1`、`:12` `staleTime: 30_000`。后端冷启动 / 抖动时 `accountsQ` 会**先重试一次再落 `isError`**，`isPending` 窗口被**拉长约一倍** → 「启动后点实盘被误弹设置」的命中概率显著高于「无重试」假设。这是 §2.3 竞态在真实环境**并不罕见**的量化佐证。
- **「刚配好账户仍切不动」子情形已被失效化缓解、但残留同款竞态**：设置页 `apps/web/components/Settings/OkxAccountsSection.tsx:13,22-23` 的失效键 `["okx","accounts"]` 与开关组件 `AccountModeSwitch.tsx:27` 查询键**完全一致** → 在设置中新增 / 编辑 / 设默认账户后会触发开关查询 `invalidateQueries` 重取，**不会**被 `staleTime:30s` 卡住陈旧。但「失效→重取落地」之间仍有一段 `isPending`，此窗口内点实盘仍命中状态①——与冷启动竞态同源，**同一处 `isSuccess` 对齐即可一并覆盖**。
- **该组件当前无任何单测**：`apps/web/components/Header/` 下**无** `*.test.*`，全仓亦无测试 import `AccountModeSwitch`（仅 `store.test.ts` 覆盖 `setAccountMode` 落状态）。即本 bug 的三态 / 竞态路径**完全无回归护栏**——下游修复节点据 CLAUDE.md §七「修 bug 必须先写能稳定复现的测试」**必须补测**。

---

## 评审结论（Review Verdict）

> 评审节点（Anthropic 资深评审）对 §一～§六逐条复核的最终裁定。**仅审，不写修复代码**；本节为交付下游修复节点的硬性口径。

### 一、证据链裁定：**成立、且经实测加固为「滴水不漏」**

原文每一处行号 / git / 链路引用均已逐条实测核对，**全部命中、无一漂移**：

1. `selectLive`（`:43-51`）「**仅 `hasReadyDefault` 才切，否则改为打开设置、不切换**」——✅ 原文准确。
2. 水合守门（`:33-36`）「`isSuccess && accountMode==="live" && !hasReadyDefault` → 强制回落 `sim`」——✅ 准确。
3. `hasReadyDefault`（`:28-30`）「**把『pending / error / 确无默认』三态折叠为单一禁止**」——✅ 准确：该表达式只看 `accountsQ.data`，**不区分** `isPending` / `isError`。
4. **守门有 `isSuccess` 前置、`selectLive` 没有 → 保护不对称 → 竞态**——✅ 准确，且经 `providers.tsx:13` `retry:1` **加固**（竞态窗口被放大约一倍）。
5. 默认 `accountMode:"live"`（`store.ts:218`）+ partialize（`:455`）+ migrate（`:542`）——✅ 准确。
6. `setAccountMode` 无拦截（`store.ts:284-285`）——✅ 准确，且经**全仓 grep** 坐实「**无第二处运行期改写 `accountMode`**」，原文「store 无辜」结论**airtight**。
7. git 改动史：`AccountModeSwitch.tsx` 仅 `4f72c39` / `660895a` 触碰，本迭代三提交（`f0d44a7` / `a0b7b87` / `8be958a`）**均未触碰**——✅ 实测 `git show --stat` 三提交确无该文件。**「逻辑根因自始未动、被几何症状掩盖」论断成立。**
8. 几何根因物理排他（`page.tsx:546` 中部已去 `drag`、`:547-555` 右簇 `no-drag` 含本控件与其几何邻居）——✅ 准确。「单一功能控件失效而几何邻居正常 ⇒ 非几何根因」的推理在物理上**自洽**。

**评审认定根因唯一且正确**：`AccountModeSwitch` 内「`selectLive` 缺无条件切换路径 + `hasReadyDefault` 三态折叠 + 守门/`selectLive` 保护不对称 + 拦截无可见反馈」四重叠加，使「实盘」在**账户查询未成功落到带 secret+passphrase 的默认账户**时**永远切不过去**。

### 二、唯一保留的证据缺口，及其为何**不削弱**推荐

- **缺口**：原文 §2.2 的 `curl /api/py/accounts` 是**取证程序**而非**已捕获的真实回包**；因此「**该用户实际命中①/②/③中的哪一态**」**未被锁定**（后端未在本评审环境运行，无法补取）。
- **为何不削弱推荐、反而**指向 F1**：正因触发态未锁定，修复**必须对三态同时鲁棒**。这恰好：(i) **证否 F3**（只补文案、不修竞态①）——若用户命中的是①/②，F3 修不好；(ii) **证否任何「只修竞态」的窄改**——若用户命中③，窄改只会继续静默弹设置、用户仍觉「切不动」。**只有同时分流三态的 F1 能不依赖『先确定是哪一态』就闭合所有复现路径。** 缺口因此被 F1 的设计**吸收**，而非遗留为风险。

### 三、最终推荐修复：**F1（三态解耦 + 可见反馈 + `selectLive`/守门同步 `isSuccess` 前置）**，附一处**强制收敛**

裁定与原文 §六一致：**采用 F1**。理由不复述（机理对位、单文件 ~15 行、Web/桌面同修、不放松安全姿态、覆盖竞态、延续第 2 轮 B2「就地可见」哲学）。评审在此**收紧两处实现口径**，以堵住 F1 描述里的歧义与潜在回归：

1. **【强制】状态①（pending）严禁「乐观进 live」**：原文 F1 写「轻提示或**暂置 pending**」，其中"乐观切到 live"是**回归陷阱**——若 pending 期把 `accountMode` 置 `live`，一旦查询落地为 `!hasReadyDefault`，水合守门（`:33-36`）会**立即弹回 `sim`**，**重现用户报障的「闪一下又弹回 / 切不动」**。故 pending 期 `selectLive` **只**给「正在校验账户…」瞬态提示并**不改 `accountMode`**；待查询 `isSuccess` 落地后由其结果驱动切换（或要求用户在落地后再点一次）。**`selectLive` 与守门必须共用同一 `accountsQ.isSuccess` 前置**，从根上消除不对称竞态。
2. **【建议】状态③文案区分两种「不就绪」**：`!hasReadyDefault && isSuccess` 含「**确无默认账户**」与「**默认账户存在但缺 secret/passphrase**」两子情形，二者引导语不同（前者「请先配置并设为默认实盘账户」，后者「默认账户凭证不全，请补全 API secret/passphrase」），可显著降低「我明明配了账户为何还切不动」的二次困惑。

### 四、回归与边界核查（评审独立完成）

- **并发 / 竞态**：①/④的根因即竞态；F1 的 `isSuccess` 对齐 + 「pending 不乐观切」两条**同时**覆盖冷启动与「刚配好账户重取窗口」两类竞态（二者同源，见补充证据）。✅
- **错误路径**：状态②（`isError`）由 F1 改为**就地可见报错并可重试**（react-query `retry:1` 用尽后落 `isError`），不再静默跳设置。✅
- **边界输入**：`defaultId=null` / 凭证缺失 → `isSuccess && !hasReadyDefault` = 状态③，F1 引导设置并给可见原因；裁定正确。✅
- **新增回归风险**：F1 不放松「无就绪默认账户不得静默进入可下单实盘」的安全姿态（守门 `:33-36` 维持）；唯一风险点即上文【强制】第 1 条已堵死。✅
- **下游不回归**：F1 不改 `setAccountMode` 契约、不动 `liveAuto.ts` 守门、不动自动交易链路；`AutoTradeToggle` 仍按 `accountMode==="live"` 自然解禁。✅
- **范围**：仅改 `apps/web/components/Header/AccountModeSwitch.tsx` 一个源文件，**外加新增**其单测 `AccountModeSwitch.test.tsx`（当前**零测试**，须按 CLAUDE.md §七补「能稳定复现三态 + 竞态」的回归用例）。✅

### 五、交付下游修复节点的验收口径（在原文 §六基础上固化/新增）

1. **判别前置（§2.4，强制 pre-flight）**：同一还原态窗口「主题 / 设置齿轮」可点 → 确认逻辑根因而非几何复发；否则转 `desktop-header-right-and-auto-trade-toggle-unresponsive` 的 A3 路线，**本修复不适用**。
2. **状态③**：点「实盘」→ 引导设置中心实盘账户页 **且就地可见一行中文原因**（区分「确无默认」与「凭证不全」两文案）。
3. **状态①/②**：点「实盘」**不再误跳设置**；①给「正在校验账户…」瞬态且**不**改 `accountMode`，②就地可见错误并可重试。
4. **已就绪默认账户**：点「实盘」**稳定切到 `live`**（分段高亮 + `ChartWorkspace` 渲染 `LiveBottomPanel`），点「模拟盘」稳定切回；**启动 / 重取窗口期点击不再被误弹设置**（竞态闭合）。
5. **回归护栏（新增硬性项）**：必须新增 `AccountModeSwitch.test.tsx`，覆盖①pending / ②error / ③no-ready-default / ④ready 四态点击行为 + 竞态（pending→success 落地后的切换结果），以「reject 误弹设置」「pending 不进 live」为断言核心。
6. **范围守卫**：仅改 `AccountModeSwitch.tsx`（+ 其新测）；**不得带入** `desktop/dist/`、`tmps/`、`.next/` 等构建/临时产物；纯 Web `pnpm dev` 与桌面打包态均不回归。

> **一句话裁定**：根因证据链经实测加固后**滴水不漏**；唯一缺口（实际触发态未捕获）由「F1 对三态同时鲁棒」吸收而非遗留。**最终推荐 F1**，但**强制**其 pending 态**不得乐观进 live**（否则重现报障），并**强制补齐当前缺失的组件回归单测**。

