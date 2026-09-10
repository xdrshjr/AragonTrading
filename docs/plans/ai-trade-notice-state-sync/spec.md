# AI 自动交易 · 提示与状态同步（ai-trade-notice-state-sync）

> **版本：v2**（设计评审后修订；v1 为设计节点产物 Subtask #0）。本文档只做设计，不含实现代码。
> 下游实施节点请**逐条**照 §七 的变更清单落地，照 §八 的门禁编号补用例。
> v2 的改动全部集中在 §评审记录 列出的 **2 条 P0 + 7 条 P1**，已在正文对应小节就地修毕；
> 凡 v1 与 v2 正文冲突之处，**一律以 v2 正文为准**（v1 不再单独保留副本）。

---

## 评审记录

> 评审口径：可行性（本仓库现有栈能不能建）、完备性（边界 / 错误路径 / 上线路径有没有漏）、
> 一致性（与 `CLAUDE.md` 和代码里既有约定冲不冲突）、尺度（过度设计还是欠规格）。
> **每一条都在源码里取过证**，证据行号随条列出；P0 / P1 已在正文修毕，P2 只登记不改。

### P0（阻断级：不改则本轮修复在关键断面上不成立）

| 编号 | 结论 | 取证 | 已修于 |
|---|---|---|---|
| **RP0-1** | **暂停回执会被自己的规则判死，出现一段「该说话时不说话」的空窗，最坏永不出现。** `paused` 的 `validIn:["paused"]` 判的是**当前** `lifecycle`，而 `pause_group` 的回执**不是** `group_status()` 形状（只有 `groupId/state/pauseReason/members/notice`），`pauseM.onSuccess` 因此**不调** `setAiTradeGroup`，`statusQ` 的结果也从不落 store —— 本地 `resolveAiTradeLifecycle` 在对账落地**之前**仍返回 `"running"`。于是这条唯一会说出「平仓其实没成功」「此刻没有任何持久化兜底」的判定性文案，在 0.3~3 秒内不渲染；若这次对账落到 `lifecycleUnknown`、或页面此刻 `document.hidden`、或后端连不上，它**一次都不会出现**。更坏的是 §7.3 迁移后的既有用例断的是 `store.aiTradeNotice?.text`（store 里**确实有**），门禁恒绿 —— 本仓库成文的「用例绿着但分支从没被走到」。 | `manager.py:1087-1095`、`AiTradeToggle.tsx:289-302`、`:97-101`、`:109`、`aiTrade.ts:1351-1362`、`useAiTradeGroupStream.ts:170-176` | §4.3 新增「未落地宽限」子句；§4.4 新增路径 A′；§8.1 U-10；§8.2 C-9 |
| **RP0-2** | **§4.6 给 `error` 定的纪元，恰好关不掉 E-13 唯一可达的那条路径。** E-13 可达的走法是**外部发起**的继续（另一个窗口 / 看门狗）：`resumeM.onSuccess` 根本不跑，`noteError(null)` 也就不跑；`paused → running → paused` 之后 `error.lifecycle === lifecycle` 成立 ⇒ 过期错误**原样复活**，正是 G-4 承诺要杜绝的那一条。而 C-7 只走本地 resume 分支（那条路上 `setError(null)` **v1 之前就已存在**于 `:311`），故门禁同样恒绿。§4.6 正文本身也自相矛盾：先说「不会在下一次 `paused` 时复活」，紧接着说「刻意只比 lifecycle 不比 resumedCount」。 | `AiTradeToggle.tsx:311`、`:544-553`、`:353-358`、`useAiTradeGroupStream.ts:343-353` | §4.6 全段重写（`error` 携完整纪元三元组、共用同一谓词）；§8.2 C-7 补外部迁移分支 |

### P1（必须改：会在实施期直接变红，或让某条论证失效）

| 编号 | 结论 | 取证 | 已修于 |
|---|---|---|---|
| **RP1-1** | **§5.3 的类型归属与 §6.2 冲突，照字面实现必成模块环。** `AiTradeGroupMemberState` 在 `aiTradeTypes.ts`，§6.2 要给它加 `messageEpoch?: AiTradeNoticeEpoch`；而 §5.3 的代码块把 `AiTradeNoticeEpoch` 声明在 `lib/aiTradeNotice.ts`（它又依赖 `aiTrade.ts` → `aiTradeTypes.ts`）。这正是 §5.3 与 G-N4 自己要防的那个环。 | `aiTradeTypes.ts:2044`、`:2062`、`:1879`、`aiTrade.ts:1329` | §5.3 四个类型下沉叶模块 + 原地再导出；§8.3 新增 G-N7 |
| **RP1-2** | **§4.8 色板引用了 `globals.css` 里不存在的 `--long-soft`，且警告档违背成文的配色纪律。** 全仓只有 `--accent-soft` / `--warn-soft` 两个 soft 面；写成裸 `var(--long-soft)` 是**静默**无底色（门禁 ③ 只扫带回退的写法，看不见），写成 `var(--long-soft, …)` 则 `contrastTokens.source.test.ts` 第 ③ 组当场红。回退到 `--accent-soft` 又会让 `resumed` 与 `paused` 同色，G-5 的「分色」直接落空。另 `action_unknown` 用 `--surface-2` 打底，与 `globals.css:47`「警告面一律用 `--warn-soft` 打底 + 图标」的成文口径相反。 | `globals.css:17,39-49,63-81`、`components/ui/contrastTokens.source.test.ts:50-130` | §4.8-1 换成只用既有 token 且逐格标注对比度的新色板 |
| **RP1-3** | **§4.3 的 SETTLE 判据取的是「对账落地时刻」，而它要排除的是「在动作之前就已出发」的那一次。** 超时断面下后端本就慢：一次先于动作出发、2 秒后才落地的对账，会用一份**不含该动作结果**的快照当场结算掉「结果未知」—— 本仓库成文的「判据取自不携带该信息的入参」。 | `useAiTradeGroupStream.ts:170-186`、`aiTrade.ts:1335` | §4.3 改为打**请求出发**时刻；§5.4 时戳语义随改 |
| **RP1-4** | **§4.5 的安全性论证有取证错误，并漏了一个断面。** `AiTradeFeedRecoveryCard` 读的是 `session.lifecycle.feedRecovery`（不是 `session.feed`），`AiTradeLivenessBanner` 读 `session.liveness`；而 `lifecycle` 与 `liveness` **两个键都是总闸门控、关闭即整键缺席**。总闸关时那两个「常驻落点」整块不渲染，此时按纪元清掉 `message`，就是把一个**仍在持续**的降级抹掉 —— 与本轮立论正好反向。 | `session.py:1829-1831`、`:3359-3366`、`feed_runtime.py:114-130`、`aiTradeFeed.ts:71`、`config.py:471,696,1184` | §4.5 盖戳改为「成员 status 携带 `lifecycle` 键时才盖」；§九 R-2 补残留说明 |
| **RP1-5** | **§6.1 与 AC-3 的「刷新后由对账器重新产生」经取证为假。** `reconcileAiTradeLifecycle` 在 `local == null` 时**早退**（`if (!local) return;`），第七行不可达；刷新后 store 是空的，那条「以下持仓从此不再由任何自动化管理」的真钱待办**永久消失**，而 AC-3 把这句假话写成了验收步骤（必不可过）。 | `aiTrade.ts:1550-1562` | §4.2 规则表加 `persist`；§5.4 / §6.1 只让 `ended`/`backend_ended` 进 `partialize`；AC-3 改写；§8.1 U-11 |
| **RP1-6** | **R-10 的互斥性论证不成立。** 它只论了 `ended` 与 `resumed`，而单槽位真正会被谁顶掉是 `action_unknown`：用户在一条「已暂停，但平仓没有完成：…请立即手动处理」还挂着的时候点【继续】并超时 ⇒ 这条真钱待办被一句「本次操作结果未知」覆盖 ⇒ 3 秒后「结果未知」被结算掉 ⇒ **两句话一起没了**。 | `AiTradeToggle.tsx:276-287`、`pause.py:322-323` | §4.2 规则表加 `sticky`；§4.3 新增「非 sticky 不得驱逐仍判活的 sticky」；§8.1 U-12 |
| **RP1-7** | **§4.8 没有声明 `AiTradeNoticeBar` 的订阅面。** 谓词的入参 `reconciledAtMs` 与 `nowMs` 都不是 props；组件若不显式订阅 `aiTradeReconciledAtMs`，`action_unknown` 的退场在「除了时戳什么都没变」的那一次对账里**不会触发重渲染**，AC-4 的「≤6 秒自行消失」落空。`syncAiTradeNotice()` 只是 store 卫生，不能替代订阅。 | `store.ts:1085`、`AgentPanel.tsx:338,423-446` | §4.8-6 明写四个订阅源；§8.2 C-9 断言 |

### P2（登记不改，实施时留意）

| 编号 | 结论 |
|---|---|
| RP2-1 | §7.3 #14/#15 的改点不全：`AiTradeToggle.test.tsx` 命中 **5 处**（`:126` 初值、`:363`、`:454`、`:669`、`:710`），`aiTrade.test.ts` 命中 **3 处**。`tsc` 与 vitest 会立刻点出来，但清单不该是错的。 |
| RP2-2 | §4.4 路径 A 把 `setError(null)` 标成【新增】，实为既有（`AiTradeToggle.tsx:311`）。v2 已就地改成【改写】。 |
| RP2-3 | 路由 `ai_trade_resume` 现写 `return group_status() or {}`；`group_status()` 返回 `None` 时并入 `notice` 会造出一份**只有 notice** 的回执体。v2 在 §4.7 明写「空 body 不并 notice」。 |
| RP2-4 | `data-testid` 由 `ai-trade-lifecycle-notice` 改为 `ai-trade-notice-bar`：全仓仅 `AgentPanel.tsx:430` 一处定义、无任何用例引用（已核），改名安全，但 §七 之前没写这条。v2 已补。 |
| RP2-5 | `document.hidden` 时对账整跳（既有守卫），后台标签页里 AC-2 的「3 秒」不成立；回到前台由 `visibilitychange` 立刻补拉。v2 已写进 AC-2。 |
| RP2-6 | 新组件没有行数预算。v2 在 §7.1 给 `AiTradeNoticeBar.tsx` 钉了 ≤ 180 行、单函数 ≤ 50 行（CLAUDE.md 阈值）。 |

---

## 一、概述

用户报告的现象只有一句话：**「暂停交易，再恢复交易以后，这个面板上的提示没有自动消失」**，
并给出了那句提示的原文 ——「已暂停：不再开新仓，但盯市、保护性平仓与交易所侧兜底核实照常运行。
BTC-USDT-SWAP：无持仓。…」。这句话由后端 `pause.build_pause_notice`
（`services/quant/app/ai_auto/pause.py:303-347`）单点生成，随 `POST /ai-trade/{gid}/pause`
的回执下发，前端在 `AiTradeToggle.tsx:295` 把它整条塞进全局 store 的 `aiTradeLifecycleNotice`，
再由 `AgentPanel.tsx:423` 那个常驻通知条渲染。取证之后可以确定：它**不是**一个渲染时序问题，
而是这条提示从设计上就**不带任何「它在什么时候成立」的信息** —— 它是一个裸的
`string | null`，谁写谁负责清；而全仓五个写点里，恰恰**只有「继续」这条路径没有写点**
（`resumeM.onSuccess`，`AiTradeToggle.tsx:304-326`）。于是「继续」之后这句针对暂停态的话
原样留在界面上，与旁边显示「运行中 · 下一次决策 03:12」的徽标同屏互相矛盾 —— 用户正是
拿着这两个互相矛盾的控件在对真实资金做判断。

同一形状在本轮取证中至少还出现两处，用户需求里的第二句「需要【AI自动交易】功能状态发生变化的
时候，能够正确同步变化各种提示」正是在说它们：① 成员行的运行期状态行 `session.message`
（`AiTradeMemberRow.tsx:85-93`）—— 后端的 `_status_payload()`（`session.py:4390`）**根本不含
`message` 键**，故 `store.patchAiTradeMemberStatus`（`store.ts:1045`）的浅合并永远清不掉它，
而行情自愈层的 L4 告警按设计「只出声一次」（`feed_runtime.py:238-246`），也就不会有第二条把它
覆盖掉；② `AiTradeToggle` 的局部 `error`，它的读点是 `(paused || stopped) && error`
（`AiTradeToggle.tsx:544`），而 `resumeM.onSuccess` 不清它 —— 一条早已过期的错误会在
**下一次暂停时原地复活**，那比「不消失」更坏。

因此本轮不做「在 resume 成功回调里补一行 `setNotice(null)`」这种点状修补。那样修，下一个新增的
提示种类会以同样的方式再漏一次（本仓库已经为「新增枚举漏改一处落点」付过至少四次代价）。本轮
的做法是给**每一条判定性提示都盖上一枚「生命周期纪元」的戳**，并把「这条提示此刻还成不成立」
收敛成**一个纯函数、一张规则表**：提示在渲染点先过这道谓词，纪元不匹配就整条不渲染。这样一来，
「继续之后暂停提示消失」不再依赖任何写点记得去清 —— 它是一条**结构上成立**的性质。作为配套，
后端为「继续」补上它自己的那句判定性回执（与暂停回执同一处收口，§3.10 的既有纪律），让状态
迁移在界面上是**换了一句话**，而不是「一句话凭空不见了」。

---

## 二、现状取证（事实基线，评审请勿重新推翻）

| 编号 | 事实 | 证据 |
|---|---|---|
| E-1 | 用户看到的那句话由后端单点生成，随 pause 回执下发 | `ai_auto/pause.py:303-347`、`ai_auto/manager.py:1092-1094` |
| E-2 | 前端把它整条存进 store 的一个裸字符串字段 | `lib/store.ts:474`（声明）、`:798`（初值）、`:1033`（setter） |
| E-3 | 唯一读点是 AgentPanel 头部之下的常驻通知条 | `components/Agent/AgentPanel.tsx:338`（挂载）、`:423-446`（组件） |
| E-4 | **五个写点**：start 清空 / pause 写入 / 超时写入 / end 写入 / 对账器写入 | `AiTradeToggle.tsx:197`、`:295`、`:280`、`:334`；`lib/aiTrade.ts:1560-1562` |
| E-5 | **「继续」没有写点** —— 这就是用户报告的那条缺陷 | `AiTradeToggle.tsx:304-326`（`resumeM.onSuccess` 全文无 notice 相关调用） |
| E-6 | `clearAiTradeRuntime` **刻意**不清这条提示（结束后面板整块消失，跟着清等于没说过） | `store.ts:1147-1148` + 该处注释 |
| E-7 | 后端 `resume` 回执**没有** `notice` 键，它直接 `return group_status()` | `api/ai_trade_lifecycle.py:92-119` |
| E-8 | 后端 `session.resume()` 会推 `status` + `lifecycle` 两帧，并**已**复位活性看门狗与情报去重 | `ai_auto/session.py:1676-1731` |
| E-9 | 前端 WS `case "lifecycle"` 只 patch **成员**行，不碰分组结论（L-1 纪律），随后请一次快对账 | `lib/useAiTradeGroupStream.ts:343-353` |
| E-10 | `resumedCount` 在**两条**装配路径上都已逐键手抄（重发现 + 窄 patch），可直接用作纪元的一位 | `useAiTradeGroupStream.ts:85`、`aiTrade.ts:buildLifecyclePatch` |
| E-11 | `_status_payload()` 不含 `message` 键 ⇒ 成员状态行的浅合并**没有复位点** | `session.py:4390-4392` 全表 |
| E-12 | 行情自愈 L4 告警「只出声一次，不每 5 秒刷屏」 ⇒ 也不会有第二条来覆盖它 | `ai_auto/feed_runtime.py:238-246` |
| E-13 | 局部 `error` 的读点带 `(paused \|\| stopped)` 前置 ⇒ 过期错误会在下一次暂停时复活 | `AiTradeToggle.tsx:544-553` |
| E-14 | 与状态**派生**的提示都是好的（暂停横幅 / 徽标 / 统计页暂停提示 / 冷续跑卡），本轮一个都不动 | `AiTradePanel.tsx:163,95`、`AiTradeStatsLive.tsx:185-190` |

**取证结论**：界面上的提示实际分成两类。第一类**由状态派生**（`resolveAiTradeLifecycle` →
徽标 / 横幅 / 按钮三态），它们天然随状态同步，本轮零改动。第二类是**回执latch**（某一刻发生过、
状态里看不出来的事实），今天它们是裸字符串，没有任何东西记得它们何时过期。本轮要消灭的正是
「既不由状态派生、又不带纪元」的第三种存在 —— 那是这次缺陷的形状。

---

## 三、目标与非目标

### 3.1 目标

1. **G-1**：暂停 → 继续之后，暂停回执文案**必须**消失。本地发起的迁移 **0 延迟**（同一次渲染），
   外部发起的迁移（另一个窗口 / 活性看门狗自动停手）**≤ 3 秒**（既有快对账窗口）。
2. **G-2**：把「提示何时失效」从五个分散写点收敛成**一张规则表 + 一个纯谓词**，新增提示种类
   必须在类型层被强制登记（`satisfies Record<AiTradeNoticeKind, …>`，漏登记即编译失败）。
3. **G-3**：`resumeM` 成功后有它自己的判定性回执（后端单点生成），状态迁移在界面上表现为
   **换了一句话**并在 12 秒后自行淡出，而不是「一句话凭空不见了」。
4. **G-4**：成员行运行期状态行（`session.message`）与 `AiTradeToggle` 局部 `error` 同样纪元化，
   杜绝「跨过一次暂停/继续往返后仍在说旧话」与「过期错误在下一次暂停时复活」。
5. **G-5**：通知条本身达到顶级设计标准：按种类分色**且分形**（不只靠颜色）、长文两行折叠可展开、
   `aria-live` 极性按「是否需要用户行动」区分、进出场动效尊重 `prefers-reduced-motion`。
6. **G-6**：零 DB 迁移、零新增 `Settings` 键、不进回测车道（`backtest.py` / `backtest_manager.py`
   全文 0 次 notice 相关符号）。

### 3.2 非目标（明确不做）

1. **不改**任何由状态派生的提示（暂停横幅、四态徽标、统计页暂停提示、冷续跑卡）——它们没坏。
2. **不改**暂停 / 继续 / 结束的任何**安全语义**：判据、拒绝条件、回滚、`_verify_armed` 全部逐字节不动。
   本轮只动「说什么、什么时候还说」。
3. **不把**判定性文案的生成权从后端搬到前端（§3.10 既有纪律）。前端只负责**装配纪元**与**决定还渲不渲**。
4. **不新增**运维开关。本轮是缺陷修复，没有「保留缺陷」的档位；回滚 = revert（见 §十）。
5. **不做** toast。sticky 事实（孤儿持仓 / 平仓没完成）三秒消失等于没说 —— 这是既有结论，本轮加固而非推翻。
6. **不拆分**既有历史超限文件（`store.ts` 1553 行、`aiTrade.ts` 1626 行、`aiTradeTypes.ts` 2555 行、
   `manager.py` 2488 行均为遗留超限）。新逻辑一律落新文件；对超限文件的净增行数每个 ≤ 40 行。

---

## 四、技术设计

### 4.1 核心概念：提示纪元（notice epoch）

一条判定性提示成立与否，取决于**它被铸造时的运行时上下文是否还是当前上下文**。把这个上下文
收敛成四元组：

```
AiTradeNoticeEpoch = {
  groupId:        string | null   // 当前分组（换组 ⇒ 上一组的一切回执作废）
  lifecycle:      "idle" | "running" | "paused" | "stopped"   // resolveAiTradeLifecycle 的产物
  resumedCount:   number | null   // 后端权威的单调计数（每次 pause→resume 往返 +1）
  reconciledAtMs: number          // 最近一次**确定**结论的对账落地时刻（syncState==="ok"）
}
```

为什么必须是这四位、少一位都不行：

- `groupId`：结束旧组、开新组之后，旧组的一切回执都不再是事实。
- `lifecycle`：这是**主判据**，用户报告的缺陷正是它变了而提示没变。
- `resumedCount`：单靠 `lifecycle` 无法区分「本次 running」与「上一次 running」。
  一条「已继续」的回执如果只按 `validIn:["running"]` 判活，用户走一遭
  `running → paused → running` 之后它会**原地复活**（与 E-13 同形的复活缺陷）。
  `resumedCount` 是后端已经在下发、且前端两条装配路径都已手抄的单调计数（E-10），拿来当纪元的
  一位是零成本的。
- `reconciledAtMs`：唯一能让「本次操作结果未知」这条超时提示**如实退场**的判据 —— 它承诺的正是
  「由 3 秒节奏的对账给出最终结论」（`AiTradeToggle.tsx:276-286` 的既有注释），而今天全仓没有
  任何一处兑现这句承诺。

### 4.2 规则表（`AI_TRADE_NOTICE_RULES`）

每一种提示登记**九个**属性（v1 是五个，v2 加了 `sticky` / `persist` 与呈现三键），全部是**可证伪**的判据，绝不留「看情况」。表里六种是分组级提示，**第七种 `action_error` 在 §4.6 登记**（它是 `AiTradeToggle` 的局部态，不占分组级槽位，但共用同一张表与同一个谓词）：

| kind | 产生点 | `validIn` | `groupScoped` | `resumeScoped` | `settledByReconcile` | `ttlMs` | `sticky` | `persist` |
|---|---|---|---|---|---|---|---|---|
| `paused` | pause 回执的 `notice` | `["paused"]` | true | true | false | `null` | **true** | false |
| `resumed` | resume 回执的 `notice`（**本轮新增**） | `["running"]` | true | true | false | `12_000` | false | false |
| `ended` | end 回执的 `orphanPositions` | `["idle","stopped"]` | **false** | false | false | `null` | **true** | **true** |
| `backend_ended` | 对账器第七行（会话在后端自行终止） | `["idle","stopped"]` | **false** | false | false | `null` | **true** | **true** |
| `action_unknown` | 写端点超时（`code === "TIMEOUT"`） | `[]`（不限态） | false | false | **true** | `null` | false | false |
| `member_status` | 成员行运行期状态行（§4.5） | `[]`（不限态） | true | true | false | `null` | false | false |

`sticky` / `persist` 两列是 **v2 新增**，各自封住一条被 v1 论证漏掉的路径：

- **`sticky`（[评审 RP1-6]）**：`true` 表示「这条话在它自己判死之前，不许被一条非 sticky 的话顶掉」。
  单槽位设计真正会出事的不是 `ended` 撞 `resumed`（那两个确实互斥），而是
  `action_unknown` 撞**仍然成立**的 `paused`：用户在一条「已暂停，但平仓没有完成：…请立即到下方
  实盘面板手动处理这些持仓。」还挂着的时候点【继续】并超时 ⇒ 真钱待办被一句「本次操作结果未知」
  覆盖 ⇒ 3 秒后「结果未知」被对账结算掉 ⇒ **两句话一起消失**。铸造点因此加一道闸（§4.3 末段）。
  反向不会误伤：`resumed` 铸造时 `setAiTradeGroup` 已把态推到 `running`，那条 `paused` sticky
  **此刻已判死**，不在「仍判活」的保护范围内。
- **`persist`（[评审 RP1-5]）**：只有 `ended` / `backend_ended` 为 `true`。理由见 §6.1 —— v1
  「刷新后由对账器重新产生」这句话经取证**是假的**，而这两条恰恰是本轮唯一两条**真钱待办**。

另有三处**反直觉但必须如此**的取值，实施时不得「顺手改直觉」：

- `ended` / `backend_ended` 的 `groupScoped` **必须是 false**。结束的那一刻 `aiTradeGroup`
  被清成 `null`，当前纪元的 `groupId` 也就变成 `null`；若按 `groupScoped:true` 判，这条
  「以下持仓从此不再由任何自动化管理」的提示会在**它最该出现的那一瞬间**被自己判死。
  它的退场只有两条路：用户点「知道了」，或开一个新分组（`startM.onSuccess` 显式清空，既有行为）。
- `action_unknown` 的 `validIn` 是**空数组**（不按 lifecycle 限定）。它的语义就是「我不知道现在是
  什么态」，拿一个自己都不确定的态去判自己的死活是循环论证。它只由对账落地来结算。
- `paused` 的 `resumeScoped` 是 true 而不是靠 `validIn` 兜底。多余？不多余：
  `running → paused(第2次)` 时 `lifecycle` 又回到 `"paused"`，若无 `resumeScoped`，
  **上一次**暂停的回执会跟着新暂停一起复活；而新暂停自己的回执会把它覆盖掉 —— 只在
  「第二次暂停的回执请求失败」这个窄断面上暴露。留着它，这个断面不存在。

### 4.3 判活谓词（唯一实现，纯函数、零 IO、绝不抛）

```
isAiTradeNoticeLive(notice, now: AiTradeNoticeEpoch, nowMs): boolean
  rule = AI_TRADE_NOTICE_RULES[notice.kind]
  if (!rule) return false                                    // 未登记的 kind ⇒ 不渲染
  if (rule.groupScoped   && now.groupId      !== notice.epoch.groupId)      return false
  if (rule.resumeScoped  && now.resumedCount !== notice.epoch.resumedCount) return false
  if (rule.validIn.length && !rule.validIn.includes(now.lifecycle)) {
    // 【v2 · 未落地宽限，修 RP0-1】权威结论还没落地之前，不拿一份已知落后的本地投影
    // 去判一条刚铸出来的话的死活。settled 一旦为真，本子句失效，validIn 恢复为硬判据。
    const settled = now.reconciledAtMs > notice.ts
    if (settled) return false
  }
  if (rule.settledByReconcile && now.reconciledAtMs > notice.ts + AI_TRADE_NOTICE_SETTLE_MS)
                                                                             return false
  if (rule.ttlMs != null && nowMs - notice.ts > rule.ttlMs)                 return false
  return true
```

**`reconciledAtMs` 的定义（v2 改，修 RP1-3）**：它是**那一次对账请求出发的墙钟**，不是回执落地的
墙钟。`pull()` 在发起 `aiTradeApi.status()` 之前取一次 `Date.now()`，回执落地时把这个**出发时刻**
连同 `"ok"` 一起交给 `setAiTradeSyncState`。v1 取落地时刻是错的：它想排除的是「在动作**之前**就已
出发」的那一次对账，而超时断面下后端本来就慢 —— 一次 `t-100ms` 出发、`t+2000ms` 才落地的对账，
按落地时刻比会**当场**用一份不含该动作结果的快照结算掉「结果未知」。这是本仓库成文的
「判据取自不携带该信息的入参」。取出发时刻之后，这条判据在语义上就是「有没有一次**在你之后**才
去问过后端的对账回来了」，与它想表达的意思逐字对齐。

`AI_TRADE_NOTICE_SETTLE_MS = 1_500` **不是保险丝，是竞态修复**：`handleActionError` 铸造
`action_unknown` 的同时会 `requestAiTradeReconcile()`，而 `requestAiTradeReconcile` 会改
`aiTradeReconcileUntil` ⇒ 对账 effect 重跑 ⇒ **立刻**补拉一次。那一次虽然出发在动作之后，但它问的
是一个**可能还在处理这次写请求**的后端，拿回来的仍可能是动作之前的断面。1.5 秒的窗把这一次排除
掉，同时保证**下一次**（快窗口 `AI_TRADE_RECONCILE_FAST_MS = 3_000`，≈3 秒后出发）必然结算。
两个数的关系必须写死成 `SETTLE < FAST`，U-4 用两条边界值冻结。

**铸造闸（v2 新增，修 RP1-6）**：`noteAiTradeNotice(kind, text)` 在写进 store 之前先看一眼当前槽位 ——

```
if (incomingRule.sticky === false
    && current != null && AI_TRADE_NOTICE_RULES[current.kind]?.sticky
    && isAiTradeNoticeLive(current, currentEpoch, nowMs)) {
  return {}          // 丢弃来者，保住那条仍然成立的真钱待办
}
```

极性是刻意的：一句「我不知道现在是什么态」压不过一句「你有一笔仓没平掉、现在就去处理」。
「结果未知」那条信息并不因此丢失 —— 动作在飞时 `actionsDisabled` 与武装清单本来就在如实呈现进度，
而最终结论由徽标给出（这正是 §4.4 路径 C 结尾那句话的原意）。

### 4.4 关键代码路径与时序

**路径 A —— 本地发起的「继续」（用户报告的那条，0 延迟）**

```
用户点【继续】
 └─ AiTradeToggle.resumeM.mutate
     └─ POST /ai-trade/{gid}/resume            （后端：①体检 ②逐成员 resume ③_verify_armed
                                                 ④置 running + resumed_count += 1 ⑤落契约）
     └─ onSuccess(res):
          setAiTradeGroup(groupStateFromStatus(res))   // state=running, resumedCount=N+1
          setAiTradeResumable(null)
          noteError(null)                              // 【改写】`:311` 的 setError(null) 早已存在，
                                                       //  本轮只是换成带纪元的写点（[评审 RP2-2]）
          noteAiTradeNotice("resumed", res.notice ?? null)   // 【新增】后端有文案 ⇒ 换一句；
                                                             //  老后端缺席 ⇒ 传 null ⇒ 只清不换
          requestAiTradeReconcile(); qc.invalidateQueries(...)
 └─ 同一次 React 渲染：AiTradeNoticeBar 重新计算 epoch
       lifecycle 已是 "running" ⇒ 旧的 paused 提示 isAiTradeNoticeLive === false ⇒ 整条不渲染
```

注意**这条链路的关键性质**：即使 `onSuccess` 里那一行 `noteAiTradeNotice` 被误删，暂停提示照样
消失 —— 因为渲染点的谓词已经判死它。写点只负责「换上新的那句话」，不负责「记得清掉旧的」。
这正是 G-2 想要的结构性质，也是本设计与「补一行 setNotice(null)」的本质区别。

**路径 A′ —— 本地发起的「暂停」（v2 新增，修 RP0-1）**

```
用户点【⏸ 暂停】
 └─ POST /ai-trade/{gid}/pause  ⇒  回执是 pause_group 的聚合回执，
                                   不是 group_status() 形状（manager.py:1087-1095）
 └─ onSuccess(res):
      noteAiTradeNotice("paused", res.notice ?? null)   // 铸造：epoch.lifecycle 此刻仍是 "running"
      setPauseOpen(false); noteError(null)
      requestAiTradeReconcile(); qc.invalidateQueries(...)
 └─ 同一次渲染：当前 lifecycle 还是 "running"（pauseM 不调 setAiTradeGroup，statusQ 不落 store）
      ⇒ 若照 v1 的谓词，validIn:["paused"] 当场判死这条刚铸出来的话
      ⇒ 「未落地宽限」子句接住它：reconciledAtMs 尚未越过 notice.ts ⇒ 判活 ⇒ 立刻渲染
 └─ ≤3s（快窗口的第一次补拉通常 <1s）对账落地 ⇒ lifecycle 变 "paused" ⇒ validIn 正常命中
```

**为什么不能改成「pause 成功就地把本地态置 paused」**：那等于让前端自己推导分组结论（L-1 的反面），
而 `pause_group` 的回执里根本没有 `lifecycle` 证据可供 `groupStateFromStatus` 消费 —— 拿一个自造的
`state` 覆盖真值，是把本轮要消灭的那类缺陷换个方向再造一次。宽限子句把这件事收敛在**谓词**里：
它只在「权威结论还没回来」这段时间内生效，`settled` 一旦为真立刻退场。

**对账连不上时它会一直挂着，这是刻意的极性**：这句话说的是真钱（「平仓没有完成」「此刻没有任何
持久化兜底」），核实不了就继续说，而不是悄悄收起来；此时徽标同屏显示「与后端同步中…」
（`syncing`），两者一起构成一个诚实的断面。

**路径 B —— 外部发起的迁移（另一个窗口 / 活性看门狗自动停手，≤ 3 秒）**

```
后端 session.resume() / _liveness_pause()
 └─ WS 推 {type:"lifecycle"} → routeMemberEvent case "lifecycle"
      patch 成员行 + requestAiTradeReconcile()      // 既有行为，不改
 └─ ≤3s 后 GET /ai-trade/status 落地
      reconcileAiTradeLifecycle(res, setters)       // 分组级 state / resumedCount 更新
      setters.setAiTradeSyncState("ok", startedAtMs)  // 【改】打的是这次请求**出发**的时刻（§4.3）
 └─ syncAiTradeNotice()                             // 【新增】把已死的提示从 store 里真删掉
 └─ 渲染点谓词同样判死（两道保险，且**共用同一个谓词实现**）
```

`case "lifecycle"` 里**刻意不直接清提示**：分组级结论是后端 `derive_group_state` 的产物（L-1），
成员级事件不该在前端重新推导一次分组结论。这条纪律在 `useAiTradeGroupStream.ts:340-342` 已有明文，
本轮遵守它。代价是外部迁移有 ≤3 秒延迟，写进验收标准（AC-2）明示。

**路径 C —— 超时（`action_unknown`）的如实退场**

```
POST /resume 超时 ⇒ ApiRequestError(code="TIMEOUT")
 └─ handleActionError: 不写 error；noteAiTradeNotice("action_unknown", e.message)
                       requestAiTradeReconcile()
 └─ t+1.5s 之内落地的对账**不算**（4.3 的 SETTLE 窗口）
 └─ t+3s 的快对账落地 ⇒ reconciledAtMs > ts+1500 ⇒ 该提示判死 ⇒ 消失
    此时界面上的态由徽标 / 横幅如实呈现（对账已给出最终结论），无需第二句话
```

### 4.5 成员级状态行的纪元化（修 E-11 / E-12）

`session.message` 今天没有复位点。本轮**不**改后端的推送形状（那会牵动七八个推送点，且
`_status_payload` 里塞一个恒为 null 的键会让「本轮没消息」与「老后端」混为一谈）。改法是在
**store 的写点**上盖戳：

- 新增 store action `noteAiTradeMemberMessage(instId, text)`：在 `set((s) => …)` 内部读到当前纪元，
  把 `{ session: {...session, message: text}, messageEpoch: epoch }` 一起写进成员槽。
- `patchAiTradeMemberStatus` 内部同样处理：**当且仅当**传入 status 携带非空 `message` 时刷新
  `messageEpoch`（缺席即不动，保住「浅合并不清空」的既有语义）。
- **盖戳的前置条件（v2 新增，修 RP1-4）**：只有在该成员的会话断面**携带 `lifecycle` 键**时才盖戳
  （`noteAiTradeMemberMessage` 读 `member.session?.lifecycle != null`；`patchAiTradeMemberStatus`
  读 `status.lifecycle !== undefined`）。理由见下方的安全性论证：`lifecycle` 键缺席 ⇒
  `AiTradeFeedRecoveryCard` 那个常驻落点**整块不渲染** ⇒ 此时清掉 `message` 就是把一个仍在持续的
  降级抹掉。不盖戳 ⇒ `messageEpoch` 缺席 ⇒ 按 §6.2 的「缺席即渲染」逐字回退到今天的行为。
- `routeMemberEvent` 的 `case "error"` 改调 `noteAiTradeMemberMessage`（今天是
  `patchAiTradeMemberSession(instId, {message})`，`useAiTradeGroupStream.ts:381`）。
- 读点 `AiTradeMemberRow.tsx:85` 改为：`message` 且 `isAiTradeNoticeLive({kind:"member_status", …})`
  才渲染。`member_status` 作为第六种 kind 登记进规则表：
  `{validIn: [], groupScoped: true, resumeScoped: true, settledByReconcile: false, ttlMs: null}`。

**为什么清掉它是安全的**（这条论证必须写进代码注释，否则下一个人会把它当成 fail-open）：
凡是**仍在持续**的降级，都另有一处**由状态派生**的常驻落点在说 —— 行情断连有
`AiTradeFeedRecoveryCard`（读 `member.session.lifecycle.feedRecovery`，`lifecycle` 挂在
`_status_payload()` 里，每次 status 都刷新），活性阻塞有 `AiTradeLivenessBanner`
（读 `session.liveness`，且后端在 `resume()` 里已显式 `self._liveness.reset()`，E-8）。
`session.message` 只是这些条件的**一次性回声**。清掉一次过期的回声，不会让任何一个仍然成立的降级
失去它的呈现面。

**这条论证有一个前提，v1 没写出来（[评审 RP1-4]）**：`lifecycle` 与 `liveness` **两个键都是总闸
门控**（`session.py:1829-1831` / `:3359-3366`：`ai_trade_lifecycle_truth_enabled` /
`ai_trade_liveness_enabled` 关 ⇒ **整键缺席**，不是下发 null）。总闸关时那两个常驻落点整块不渲染，
上面那句「都另有一处在说」就不成立。故盖戳的前置条件必须是「`lifecycle` 键在场」——
键不在场就不盖戳，行为逐字回退到今天（一条过期的回声继续挂着）。
**残留（已知、已接受）**：`ai_trade_liveness_enabled=False` 但 `lifecycle` 总闸开着时，
一条活性类的过期回声仍会被清掉；两个总闸默认都是 `True`（`config.py:696`、`:1184`），
且这个组合下的极性是「宁可多说一句过期的降级，也不要把一个仍在持续的降级抹掉」的反面 ——
写进 §九 R-2 明示，不在本轮扩大改动面去追它。

### 4.6 局部 `error` 的纪元化（修 E-13）—— v2 重写

**v1 的写法关不掉 E-13（[评审 RP0-2]）。** v1 让 `error` 只记 `lifecycle` 并按
`error.lifecycle === lifecycle` 判活，理由是「`error` 是一次动作的失败说明，同一态内保持可见是对的」。
这个理由本身没错，错在它**恰好放过了 E-13 唯一可达的那条路径**：

```
paused 态下 openResume 的复核请求失败 ⇒ noteError("无法核对持仓，请稍后重试")  （行内可见，正确）
另一个窗口点了【继续】/ 看门狗自动停手 ⇒ WS lifecycle + 对账 ⇒ 本地 lifecycle 变 running
      ⇒ 行内读点被 (paused || stopped) 挡住而隐藏，但 error 没有任何写点去清它
      （resumeM.onSuccess 根本没跑，`:311` 的 setError(null) 与本轮新增的 noteError(null) 都不在这条路上）
用户再次点【⏸ 暂停】⇒ lifecycle 又是 "paused" ⇒ error.lifecycle === lifecycle 成立
      ⇒ 那条早已过期的错误原样复活
```

而 C-7 只走本地 resume（那条路上 `setError(null)` **本轮之前就已存在**于 `AiTradeToggle.tsx:311`），
所以按 v1 写的门禁会**恒绿**地放行这个缺陷 —— 本仓库成文的「用例绿着但分支从没被走到」。

**v2 的写法**：`error` 携带**与提示同一枚纪元戳**，并且**共用同一个判活谓词**，不再自造第二套判据。

```ts
const [error, setError] = useState<AiTradeNotice | null>(null);   // kind 固定为 "action_error"
const noteError = (text: string | null) =>
  setError(mintAiTradeNotice("action_error", text, epoch, Date.now()));
const liveError = isAiTradeNoticeLive(error, epoch, nowTs) ? error!.text : null;
```

`action_error` 作为**第七种** kind 登记进规则表：
`{validIn: [], groupScoped: true, resumeScoped: true, settledByReconcile: false, ttlMs: null,
  sticky: false, persist: false}`。

- `validIn: []`：**保留** v1 那条正确的直觉 —— 一次动作的失败说明在同一段生命周期里应当保持可见，
  不该因为态本身没变就自己消失。行内读点的 `(paused || stopped)` 前置**逐字不动**（那是排版契约，
  不是判活判据）。
- `resumeScoped: true`：这一位才是修 E-13 的那一位。它不看「态叫什么名字」，看的是「这是不是**同一段**
  running/paused」——上面那条外部迁移路径上 `resumedCount` 已经 +1，过期错误当场判死，
  而且**不依赖任何写点记得去清它**（与提示同一条结构性质，G-2）。
- `groupScoped: true`：换组之后上一组的失败说明不再是事实。

四个弹窗的 `error={...}` 与行内读点统一改吃 `liveError`；`resumeM.onSuccess` 仍显式 `noteError(null)`
（路径 A 已列，那是「换一句话」而不是「靠它来清」）。C-7 因此必须**两条分支都走**：本地 resume 与
外部迁移（§8.2）。

### 4.7 后端：为「继续」补上它自己的判定性回执（G-3）

在 `pause.py` 里新增 `build_resume_notice`，与 `build_pause_notice` **同处收口**（§3.10：
判定性文案单点在后端）：

```python
def build_resume_notice(members: list[dict], *, resumed_count: int) -> str:
    """续跑回执的 `notice`（与 `build_pause_notice` 同一处收口，§3.10）。

    **只说这一刻已经核实过的事**：`resume_group` 只有在 `_verify_armed` 通过之后才返回，
    故「重新武装完成」是有据的。持仓张数**一律不复述** —— 热续跑没有重新向交易所读过持仓，
    照抄本地投影等于在一句判定性文案里混进一个未经核实的数（诚实不变式 I-4：读不到 ≠ 没有，
    没核实过 ≠ 可以当成核实过）。纯文本，绝不用 `**粗体**`：后端文案直接进 JSX，
    markdown 会原样显示成星号（本仓库已复发四次）。
    """
```

产出形如：
`「已继续：3 个标的重新武装完成，暂停期「不开新仓」的限制已解除。按本会话的自管投影，
BTC-USDT-SWAP、ETH-USDT-SWAP 仍记有持仓并已重新纳入盯市（本次继续未重新向交易所核实持仓）。
（这是本会话第 2 次继续。）」`
—— 只在**至少有一个成员带仓**时追第二句，只在 `resumed_count > 1` 时追第三句。

**第二句的措辞是被取证钉死的（[评审 RP2-4]）**：不带回执调用时 `_state_member_rows` 走
`session.state_member_row()`，那里 `positionUnknown` **恒为 `False`**、`position` 取的是
`executor` 的**自管投影**（`session.py:1114-1138`）。换言之这份断面在热续跑路径上从未向交易所核实
过。不复述张数还不够 —— 点名「哪几个标的有持仓」同样是一句断言。故文案必须**自报数据来源**
（「按本会话的自管投影」）并**自报未核实**（「本次继续未重新向交易所核实持仓」），
B-2 用机械断言冻结这两个短语（§8.4）。冷续跑路径已由 `resume-preview` 做过交易所核实，
本句在两条路径上共用同一措辞是**保守方向**（把已核实的说成未核实，最坏后果是用户多看一眼）。

管线：`api/ai_trade_lifecycle.py::ai_trade_resume` 捕获 `resume_group` 的返回值（今天丢弃了；
它返回的正是 `AiTradeGroup`，见 `manager.py:1097`），调用新增的公开方法
`AiTradeManager.build_resume_notice_for(group) -> str`（5 行：内部转调
`pause_core.build_resume_notice(self._state_member_rows(group), resumed_count=group.resumed_count)`；
`_state_member_rows` 是 `@staticmethod`，`self.` 调用合法），把它作为**加法键** `notice` 并进
`group_status()` 的回执体。

**空 body 不并 notice（[评审 RP2-3]）**：该路由现写 `return get_ai_trade_manager().group_status() or {}`。
`group_status()` 返回 `None` 时**必须原样返回 `{}`**，绝不并入 `notice` —— 否则会造出一份
「只有 notice、没有任何分组字段」的回执，而前端 `groupStateFromStatus(res)` 是它的消费者。
实施形状：`body = get_ai_trade_manager().group_status(); return {**body, "notice": …} if body else {}`。

**为什么不复用 `/status` 的 body**：`/ai-trade/status` 的 `group` 是一个长期存在的只读视图，
往里塞一个只在一次动作里成立的 `notice` 会让它变成第二个真源。`resume` 路由自己组装
`{**(group_status() or {}), "notice": …}`，TS 侧用一个**扩展类型** `AiTradeResumeResult
extends AiTradeGroupRow { notice?: string | null }` 承接。`groupStateFromStatus(res)` 是
`AiTradeGroupRow` 的消费者，超集入参逐字节兼容，一行都不用改。

### 4.8 视觉与交互（G-5 · 顶级设计）

通知条从 `AgentPanel.tsx` 内联函数**抽出**为独立组件 `components/Agent/AiTradeNoticeBar.tsx`
（顺带让它可被单测直接渲染，不必拖起整个 AgentPanel）。设计要点：

1. **按种类分色且分形**（无障碍：绝不只靠颜色）。字形前缀 + 色板一一对应：

   **色板只许用 `globals.css` 里已定义的 token（v2 改，修 RP1-2）**：全仓只有 `--accent-soft` /
   `--warn-soft` 两个 soft 面，**没有** `--long-soft` / `--short-soft`。分色靠「左缘 3px 面色竖线 +
   中性底」而不是靠给每种态造一个新底色 —— 既零改 `globals.css`（也就零触碰
   `app/globals.source.test.ts` 的 WCAG 配对矩阵），又让四种态在视觉上真的分得开。

   | kind | 前缀 | 左缘（`border-l-[3px]`） | 底色 | 文字色 | 对比度（浅 / 暗） | `role` |
   |---|---|---|---|---|---|---|
   | `paused` | `‖` | `--accent` | `--accent-soft` | `--accent-ink` | 4.61 / 4.98 | `status` |
   | `resumed` | `▶` | `--long` | `--surface-2` | `--long-ink` | 5.49 / 5.70 | `status` |
   | `ended` | `!` | `--short` | `--surface-2` | `--short-ink` | 5.73 / 4.74 | **`alert`** |
   | `backend_ended` | `!` | `--short` | `--surface-2` | `--short-ink` | 5.73 / 4.74 | **`alert`** |
   | `action_unknown` | `?` | `--warn` | `--warn-soft` | `--warn` | 5.07 / 6.14 | `status` |
   | `action_error` | `×` | `--short` | `--surface-2` | `--short-ink` | 5.73 / 4.74 | （见 §4.6，沿用行内 `role="alert"`） |
   | `member_status` | （成员行内，沿用既有 `--warn` 单行样式，不进本条） | — | — | — | — | — |

   四条**必须逐字遵守**的既有纪律（前三条违反即 `contrastTokens.source.test.ts` / `globals.source.test.ts` 变红）：
   - 面色（`--accent` / `--long` / `--short`）**只能进 `border` / `background`，绝不能进任何 `color:` 或
     `text-[var(--面色)]`；文字一律用墨色（`globals.css:31-32`）。
   - **`--short-ink` 不得与 `bg-[var(--accent-soft)]` 出现在同一行**（暗色仅 4.41，是配对矩阵里那个
     显式排除项，由源码扫描看守）。上表用 `--surface-2` 打底，天然避开。
   - 警告档用 `--warn-soft` 打底（`globals.css:47`：「警告面一律用 `--warn-soft` 打底 + 图标，
     绝不靠颜色单通道传达」）。v1 写的 `--surface-2` 虽然对比度也达标，但与这条成文口径相反。
   - 对比度一列的数值取自 `globals.css` 已复算好的注释（`:39-41`、`:46`、`:68-69`、`:79`），
     **不新增任何需要重算 WCAG 的配对**。

   `role="alert"` **只给需要用户行动的两种**（孤儿持仓 / 会话自行终止）：把每一条提示都做成
   `alert` 会让屏幕阅读器在每次暂停时打断用户，那不是「更安全」，是噪音。其余用
   `role="status" aria-live="polite"`。

2. **长文两行折叠**。用户给出的那条提示有 5 个标的、约 90 个汉字，在 AgentPanel 这个窄栏里
   要占 6 行、把对话区顶下去。改为 `line-clamp-2` + 行尾内联「展开 / 收起」按钮
   （`aria-expanded`）。折叠是**纯呈现**，`title` 属性仍带全文，复制/朗读不受影响。
   可行性已核：本仓库 Tailwind 为 3.4.19，`line-clamp-*` 与 `motion-reduce:` 均为**核心工具类**，
   无需插件（`tailwind.config.ts` 的 `plugins: []` 保持不动）；`ease-smooth` 已在 `transitionTimingFunction`
   里定义；`content` glob 已覆盖 `./components/**`，新组件天然被扫到。
   `member_status` 那一行维持既有 `truncate` 单行，不改。

3. **进出场动效**：`transition-[opacity,transform] duration-150 ease-smooth`，进场
   `opacity-0 translate-y-[-2px]` → `opacity-100 translate-y-0`；
   `@media (prefers-reduced-motion: reduce)` 下动效整段关闭（Tailwind `motion-reduce:transition-none`）。
   **绝不用高度动画**：AgentPanel 头部之下的高度跳变会把整段对话推位，那是本仓库
   `min-h-[24px]` 锁行高那条教训的同一形状。

4. **`resumed` 的 12 秒 TTL 需要一个时钟**。组件内 `useState(Date.now())` + 1 秒
   `setInterval`，**且只在「当前提示的 kind 带 ttl」时才起表**（`useEffect` 依赖
   `notice?.kind`）。⚠️ 全部 hooks 必须声明在任何早退**之前** —— 本仓库有一次
   「条件调用 hook 直接把面板打崩」的成文事故（深度思维链那一轮的 P0）。

5. 关闭按钮文案随种类：sticky 的两种用「知道了」，其余用「关闭」。手动关闭永远可用
   （用户能主动收起任何一条），实现即 `dismissAiTradeNotice()`。

6. **订阅面必须显式声明（v2 新增，修 RP1-7）**。谓词的入参没有一个是 props，组件必须自己订阅齐
   **四个**来源，少一个就会出现「事实变了但这一条没有重算」：

   | 订阅 | 少了它会怎样 |
   |---|---|
   | `aiTradeNotice` | 连内容都拿不到（不可能漏） |
   | `aiTradeGroup` + `aiTradeResumable` | `lifecycle` / `groupId` / `resumedCount` 变了不重算 ⇒ AC-1 / AC-2 落空 |
   | **`aiTradeReconciledAtMs`** | 一次「除了时戳什么都没变」的对账不会触发重渲染 ⇒ `action_unknown` 不退场 ⇒ **AC-4 落空**；`paused` 的未落地宽限也永远退不出去 |
   | 组件内 1 秒 `nowMs` 表（仅当前 kind 带 `ttlMs` 时起） | `resumed` 的 12 秒 TTL 不生效 ⇒ C-3 红 |

   `syncAiTradeNotice()` 是 **store 卫生**（把已死的条目真删掉），**不能替代订阅** —— 它自己也只在
   对账落地时跑，而那正是需要重渲染的那一刻。

7. **行数预算（CLAUDE.md，[评审 RP2-6]）**：`AiTradeNoticeBar.tsx` ≤ 180 行、单个函数 ≤ 50 行、
   嵌套 ≤ 4 层。超了就把「折叠 / 展开」拆成同文件内的子组件，**不**新开第三个文件。

---

## 五、接口设计

### 5.1 `POST /api/ai-trade/{group_id}/resume` —— 唯一的 wire 变更（加法键）

请求体：**不变**（`{ acknowledge: boolean }`）。
响应：在既有 `group_status()` 形状上**新增一个加法键**：

```jsonc
{
  "groupId": "g-...", "bar": "15m", "state": "running", "resumedCount": 2,
  "members": [ ... ],
  // ...既有全部键逐字不变...
  "notice": "已继续：3 个标的重新武装完成，暂停期「不开新仓」的限制已解除。BTC-USDT-SWAP 的持仓重新交由本会话管理。（这是本会话第 2 次继续。）"
}
```

- 该端点 `-> dict` 且**无** `response_model`，故新键不会被 FastAPI 静默吞掉（与 `/ai-trade/status`
  同一条既有理由，`api/ai_auto.py:808` 的 docstring 已写明）。这一点必须在实施时复核 ——
  本仓库有过 `response_model` 静默吞新 wire 键的成文事故。
- 老后端 / 键缺席 ⇒ 前端 `res.notice ?? null` ⇒ `noteAiTradeNotice("resumed", null)` ⇒ **只清不换**。
  G-1 在没有后端变更时**依然成立**（这是刻意的：前端修复自足，后端是增益）。
- 错误路径（403 / 409 / 400）**逐字节不变**。

### 5.2 不变的接口

`POST /pause`（`notice` 语义不变）、`GET /resume-preview`、`POST /end`、`POST /quit-pause`、
`GET /ai-trade/status`、全部 WS 事件形状 —— 本轮一个字都不改。

### 5.3 前端内部接口

**类型归属（v2 改，修 RP1-1）**：v1 把 `AiTradeNoticeEpoch` 等四个类型声明在 `lib/aiTradeNotice.ts`，
同时又在 §6.2 要求给 `aiTradeTypes.ts` 里的 `AiTradeGroupMemberState` 加一个
`messageEpoch?: AiTradeNoticeEpoch` —— 照字面实现，`aiTradeTypes.ts` 就要反向导入
`aiTradeNotice.ts`（后者又依赖 `aiTrade.ts` → `aiTradeTypes.ts`），**正好造出 §5.3 与 G-N4 自己要防
的那个环**。v2 的归属：

| 放哪 | 内容 |
|---|---|
| `lib/aiTradeTypes.ts`（叶，**只有类型**） | `AiTradeLifecycle`（从 `aiTrade.ts:1329` 下沉）、`AiTradeNoticeKind`、`AiTradeNoticeEpoch`、`AiTradeNotice`、`AiTradeNoticeRule`、`AiTradeResumeResult`，以及 `AiTradeGroupMemberState.messageEpoch?` |
| `lib/aiTradeNotice.ts`（纯核心，**只有值**） | `AI_TRADE_NOTICE_RULES`、两个常量、`currentAiTradeEpoch` / `mintAiTradeNotice` / `isAiTradeNoticeLive`；顶部一行 `export type { … } from "./aiTradeTypes"` 原地再导出，调用方**只需 import 一个模块** |
| `lib/aiTrade.ts` | 原地再导出 `AiTradeLifecycle`（`export type { AiTradeLifecycle } from "./aiTradeTypes"`），既有全部导入点**一行都不用改** |

依赖方向因此是 `aiTradeTypes.ts`（叶） ← `aiTrade.ts` ← `aiTradeNotice.ts` ← `store.ts` / 组件，
无环，由 G-N4 + **新增的 G-N7** 冻结两端。

```ts
// ---- lib/aiTradeTypes.ts（类型侧）----
export type AiTradeLifecycle = "idle" | "running" | "paused" | "stopped";

export type AiTradeNoticeKind =
  | "paused" | "resumed" | "ended" | "backend_ended"
  | "action_unknown" | "member_status" | "action_error";

export interface AiTradeNoticeEpoch {
  groupId: string | null;
  lifecycle: AiTradeLifecycle;
  resumedCount: number | null;
  reconciledAtMs: number;
}

export interface AiTradeNotice {
  kind: AiTradeNoticeKind;
  text: string;
  epoch: AiTradeNoticeEpoch;
  ts: number;                       // Date.now()，由调用方注入（纯函数不自取时钟）
}

export interface AiTradeNoticeRule {
  validIn: readonly AiTradeLifecycle[];
  groupScoped: boolean;
  resumeScoped: boolean;
  settledByReconcile: boolean;
  ttlMs: number | null;
  sticky: boolean;                  // v2：非 sticky 不得驱逐仍判活的 sticky（§4.3）
  persist: boolean;                 // v2：只有 ended / backend_ended 为 true（§6.1）
  tone: "accent" | "long" | "short" | "warn";
  glyph: string;                    // 分形前缀（无障碍：不只靠颜色）
  urgent: boolean;                  // true ⇒ role="alert"
  dismissLabel: "知道了" | "关闭";
}

// ---- lib/aiTradeNotice.ts（值侧）----

export const AI_TRADE_NOTICE_SETTLE_MS = 1_500;      // 必须 < AI_TRADE_RECONCILE_FAST_MS
export const AI_TRADE_NOTICE_RULES = { /* 七行，见 §4.2 / §4.6 / §4.8 */ } satisfies
  Record<AiTradeNoticeKind, AiTradeNoticeRule>;

export function currentAiTradeEpoch(
  group: AiTradeGroupState | null,
  resumable: AiTradeResumableGroup | null,
  reconciledAtMs: number,
): AiTradeNoticeEpoch;

export function mintAiTradeNotice(
  kind: AiTradeNoticeKind, text: string | null,
  epoch: AiTradeNoticeEpoch, nowMs: number,
): AiTradeNotice | null;            // text 为空 / 全空白 ⇒ null（= 清空，不留空条）

export function isAiTradeNoticeLive(
  notice: AiTradeNotice | null, now: AiTradeNoticeEpoch, nowMs: number,
): boolean;
```

`satisfies` 而不是 `:` 标注：既保证**漏登记一种 kind 就编译失败**（G-2），又保留字面量的窄类型
以便呈现层直接取 `tone` / `glyph` 而不退化成宽联合。

**模块依赖方向（必须无环，源码门禁 G-N4 / G-N7 冻结）**：
`aiTradeTypes.ts`（叶） ← `aiTrade.ts` ← `aiTradeNotice.ts` ← `store.ts` / 组件。
`aiTrade.ts` **绝不**导入 `aiTradeNotice.ts`，`aiTradeTypes.ts` **绝不**导入前两者中的任何一个。
`aiTrade.ts` 只需要 `AiTradeNoticeKind` 这个类型（`AiTradeReconcileSetters.noteAiTradeNotice` 的形参），
它与 `AiTradeLifecycle` 一起住在叶模块，故这条依赖天然成立。

### 5.4 Store 契约变更

| 变更 | 说明 |
|---|---|
| `aiTradeLifecycleNotice: string \| null` → `aiTradeNotice: AiTradeNotice \| null` | **必须改名**：留着旧名会让漏改的写点悄悄编译通过，而本缺陷的根因正是「有写点没人负责清」 |
| 新增 `aiTradeReconciledAtMs: number`（初值 `0`，瞬态、不进 `partialize`） | 由 `setAiTradeSyncState("ok", startedAtMs)` 顺手打戳，**没有第二个写点**。打的是那次对账**请求出发**的墙钟（§4.3 / [评审 RP1-3]），故 setter 签名从 `(state)` 变为 `(state, startedAtMs?)`；`"unknown"` 分支**绝不打戳**（读不到 ≠ 已核实）。`startedAtMs` 缺席时退回 `Date.now()`，老调用点逐字兼容 |
| `setAiTradeLifecycleNotice(s)` → `noteAiTradeNotice(kind, text)` | 铸造在 store 内完成（读得到当前纪元），调用方只给「说什么」 |
| 新增 `dismissAiTradeNotice()` | 手动关闭；语义上等价 `set({aiTradeNotice:null})`，独立命名是为了让源码门禁能分辨「用户关的」与「系统清的」 |
| 新增 `syncAiTradeNotice()` | 对账落地后把已死提示真删掉（store 卫生；渲染安全由谓词保证） |
| 新增 `noteAiTradeMemberMessage(instId, text)` | 成员状态行的唯一写点（带纪元；盖戳的前置条件见 §4.5） |
| `partialize` 新增一行（v2，修 RP1-5） | `aiTradeNotice: s.aiTradeNotice && AI_TRADE_NOTICE_RULES[s.aiTradeNotice.kind]?.persist ? s.aiTradeNotice : null` —— **只有 `ended` / `backend_ended` 落盘**，其余种类恒写 `null`。无需升 persist 版本：新键在旧快照里缺席，zustand 的浅合并会回落到初值 `null`（与仓库既有范式一致） |
| `clearAiTradeRuntime` | **逐字不变**：仍然不清 `aiTradeNotice`（E-6 的理由今天依然成立） |

---

## 六、数据模型

### 6.1 数据库

**零迁移。** 本轮不新增、不修改任何表或列；`messageEpoch` / `aiTradeReconciledAtMs` 是
**前端内存瞬态**，不进 `partialize`（刷新即回落初值，由 `GET /ai-trade/status` 重发现）。
后端 `notice` 是一次性回执，不落库。

**为什么绝大多数提示不持久化**：一条提示的有效期上界是「当前进程内的这一段生命周期」。跨刷新恢复
一条暂停回执，等于在一个刚刚重新对账过的界面上放一句来历不明的旧话 —— 那是本轮要消灭的形状本身。

**唯一的例外：`ended` / `backend_ended` 必须落盘（v2 改，修 RP1-5）。** v1 说它们「在刷新后由对账器
重新产生（`aiTrade.ts:1560` 那一行）」—— **这句话经取证是假的**：`reconcileAiTradeLifecycle` 在
`if (!local) return;` 处早退（`aiTrade.ts:1553`），而刷新之后 store 里根本没有 `local`，第七行
**不可达**。于是「以下持仓从此不再由任何自动化管理」这条**真钱待办**在一次刷新之后永久消失，
而 AC-3 把这句假话写成了验收步骤（照 v1 手动走必不过）。

落盘的形状与安全性：
- `partialize` 里按规则表的 `persist` 位过滤（§5.4），**只有这两种**能进 localStorage；
  `paused` / `resumed` / `action_unknown` / `member_status` / `action_error` 恒写 `null`。
- 水合回来之后**照样过谓词**：这两条 `groupScoped:false`、`resumeScoped:false`、`ttlMs:null`、
  `validIn:["idle","stopped"]`。刷新后 `aiTradeGroup` 为空 ⇒ `lifecycle === "idle"` ⇒ 判活 ⇒ 渲染；
  一旦对账发现后端有一个**新的**在跑分组 ⇒ `lifecycle === "running"` ⇒ 判死（与 §4.2 已经接受的
  「开新组即清」同一条语义，不新增第二套判据）。
- 它带着的 `epoch.reconciledAtMs` 是上一个进程的时戳，而这两条 `settledByReconcile:false`，
  该字段对它们**不参与判活**；`ts` 也只被 `ttlMs`（为 `null`）用到。故跨进程的旧时戳不会产生任何
  影响 —— 这一点由 U-11 冻结。

### 6.2 内存形状

```ts
// store（新增/变更）
aiTradeNotice: AiTradeNotice | null            // 分组级唯一一条（后来者覆盖先来者）
aiTradeReconciledAtMs: number                  // 0 = 从未落地过一次确定结论

// AiTradeGroupMemberState（新增一个可选键）
messageEpoch?: AiTradeNoticeEpoch | null       // 与 session.message 配对；缺席 ⇒ 视为老数据 ⇒ 渲染
```

`messageEpoch` **声明为可选并在缺席时判活为 true**：既有用例与老快照直接构造成员对象、没有这个键，
把「没戳」当成「已死」会让一批本该显示的状态行凭空消失（这与本轮修复的方向相反）。缺席即渲染，
只有**盖过戳且戳已过期**的才隐藏。

这条极性在 v2 里又多承担了一项职责（§4.5 / [评审 RP1-4]）：`lifecycle` 总闸关时**故意不盖戳**，
于是「缺席即渲染」把行为逐字回退到今天 —— 常驻落点不在场时，宁可留着一条过期的回声，
也不把一个仍在持续的降级抹掉。

### 6.3 后端形状

`build_resume_notice(members, *, resumed_count)` 的 `members` 复用
`AiTradeManager._state_member_rows(group)` 的行形状（`{instId, position, positionUnknown, …}`），
与 `build_pause_notice` 的入参**同一形状、同一构造点** —— 绝不为续跑另造一份行。

---

## 七、文件 / 模块变更清单

### 7.1 前端新增（5 个）

| # | 文件 | 意图 |
|---|---|---|
| 1 | `apps/web/lib/aiTradeNotice.ts` | 提示纪元纯核心：规则表 + `currentAiTradeEpoch` + `mintAiTradeNotice` + `isAiTradeNoticeLive`（≤ 200 行，零 IO、零 React） |
| 2 | `apps/web/lib/aiTradeNotice.test.ts` | 纯函数用例（六种 kind × 判活矩阵、SETTLE 窗口、TTL、规则表完备性） |
| 3 | `apps/web/lib/aiTradeNotice.source.test.ts` | 源码门禁 G-N1 ~ G-N5（正则一律写 `\r?\n`，见 §8.3） |
| 4 | `apps/web/components/Agent/AiTradeNoticeBar.tsx` | 通知条组件（从 AgentPanel 抽出）：判活谓词 + 分色分形 + 两行折叠 + TTL 时钟 + §4.8-6 的四个订阅源。**≤ 180 行、单函数 ≤ 50 行**；`data-testid` 由 `ai-trade-lifecycle-notice` 改为 `ai-trade-notice-bar`（全仓仅 `AgentPanel.tsx:430` 一处定义、零用例引用，改名安全） |
| 5 | `apps/web/components/Agent/AiTradeNoticeBar.test.tsx` | 组件用例（含 AC-1 的直接复现：paused→running 后整条消失） |

### 7.2 前端改写（8 个）

| # | 文件 | 意图 |
|---|---|---|
| 6 | `apps/web/lib/aiTradeTypes.ts` | 下沉 `AiTradeLifecycle`；**新增 `AiTradeNoticeKind` / `AiTradeNoticeEpoch` / `AiTradeNotice` / `AiTradeNoticeRule` 四个类型**（§5.3 的类型归属，修 RP1-1）；新增 `AiTradeResumeResult`；`AiTradeGroupMemberState` 增 `messageEpoch?`（净增 ≤ 40 行） |
| 7 | `apps/web/lib/aiTrade.ts` | 原地再导出 `AiTradeLifecycle`（调用点零改）；`AiTradeReconcileSetters` 的 `setAiTradeLifecycleNotice` → `noteAiTradeNotice(kind, text)`；第七行判定改传 `"backend_ended"` |
| 8 | `apps/web/lib/store.ts` | 字段更名 + 4 个新 action（含 §4.3 的铸造闸）+ `setAiTradeSyncState(state, startedAtMs?)` 打**请求出发**时戳 + `patchAiTradeMemberStatus` 携 `message` 且 `lifecycle` 在场时刷戳 + `partialize` 按 `persist` 位过滤一行（净增 ≤ 40 行） |
| 9 | `apps/web/lib/useAiTradeGroupStream.ts` | `pull()` 里记 `startedAtMs` 并透传给 `setAiTradeSyncState`（含 `.catch` 分支**不打戳**）；对账 `.then` 里补 `syncAiTradeNotice()`；`case "error"` 改调 `noteAiTradeMemberMessage`；两处 setters 表跟改键名 |
| 10 | `apps/web/lib/aiTradeApi.ts` | `resumeGroup` 返回类型 `AiTradeGroupRow` → `AiTradeResumeResult` |
| 11 | `apps/web/components/Agent/AgentPanel.tsx` | 删除内联 `AiTradeLifecycleNotice`（−28 行），改 `import { AiTradeNoticeBar }`；挂载点 `:338` 换组件名 |
| 12 | `apps/web/components/Agent/AiTradeToggle.tsx` | 五个写点全改经 `noteAiTradeNotice(kind, …)`；**新增** `resumeM.onSuccess` 的 `resumed` 写点（`noteError(null)` 是把既有 `:311` 改写，不是新增）；局部 `error` 纪元化为 `action_error`（§4.6 重写版），**全部 19 处 `setError(…)` 调用点与 5 处读点一律改经 `noteError` / `liveError`**（另有 1 处 `setError(null)` 出现在 `:542` 的注释里，一并改文案） |
| 13 | `apps/web/components/Chart/AiTradeMemberRow.tsx` | `session.message` 读点改经 `isAiTradeNoticeLive`（`member_status` 规则） |

### 7.3 前端用例改写（4 个）

| # | 文件 | 意图 |
|---|---|---|
| 14 | `apps/web/lib/aiTrade.test.ts` | **3 处**命中：`makeSetters` 的 `setAiTradeLifecycleNotice` → `noteAiTradeNotice`（声明 + `calls` 键）；`:1443` 那条断言改断 `("backend_ended", 含「请到实盘面板核对持仓」)` |
| 15 | `apps/web/components/Agent/AiTradeToggle.test.tsx` | **5 处**命中（[评审 RP2-1]）：`:126`（`setState` 初值）、`:363`（暂停回执有读点）、`:454`（孤儿仓）、`:669` / `:710`（两条「结果未知」）全部改断 `store.aiTradeNotice?.text`；**新增** C-6 / C-7（两分支）两条 |
| 16 | `apps/web/components/Chart/AiTradePanel.test.tsx` | 若有直接构造 `member.session.message` 的断言，补 `messageEpoch` 缺席路径（§6.2 的缺席即渲染）不受影响的守卫 |
| 16b | `apps/web/lib/store.test.ts` | **新增 1 条**：`ended` 进 `partialize`、`paused` 不进（§6.1 的 `persist` 位，U-11 的持久化侧） |

### 7.4 后端（3 个源文件 + 2 个用例文件）

| # | 文件 | 意图 |
|---|---|---|
| 17 | `services/quant/app/ai_auto/pause.py` | 新增 `build_resume_notice(members, *, resumed_count)`（≈ 22 行，含 docstring；文件 374 → ≈ 396 行，仍在红线内） |
| 18 | `services/quant/app/ai_auto/manager.py` | 新增公开方法 `build_resume_notice_for(group) -> str`（5 行，转调 #17 + `_state_member_rows`） |
| 19 | `services/quant/app/api/ai_trade_lifecycle.py` | `ai_trade_resume` 捕获 `resume_group` 返回值，回执体并入加法键 `notice`（3 行；docstring 补一段「为什么不塞进 `/status`」） |
| 20 | `services/quant/tests/test_ai_trade_pause_drift.py` | 3 条纯函数用例（见 §8.4） |
| 21 | `services/quant/tests/test_ai_trade_pause_api.py` | 1 条路由用例：`/resume` 200 回执必带非空 `notice`，且 403/409 路径逐字不变 |

**不在清单里的文件一律不动**，特别是：`ai_auto/session.py`、`ai_auto/resume.py`、
`ai_auto/lifecycle.py`、`backtest.py`、`backtest_manager.py`、`config.py`、`.env.example`、
任何 DB 模型或迁移。

---

## 八、测试与验收标准

### 8.1 纯函数用例（vitest · `apps/web/lib/aiTradeNotice.test.ts`）

| 编号 | 断言 |
|---|---|
| U-1 | `paused` 提示在 `lifecycle="paused"` 且同组同 `resumedCount` 下判活；`lifecycle` 变 `"running"` 后判死 |
| U-2 | `paused` 提示在 `resumedCount` 由 0 → 1 后判死，**即使 lifecycle 又回到 `"paused"`**（§4.2 第三条反直觉） |
| U-3 | `ended` 提示在 `groupId` 由 `"g1"` → `null` 后**仍判活**（`groupScoped:false`）；在 `lifecycle="running"` 下判死 |
| U-4 | `action_unknown`：`reconciledAtMs = ts + 1_400` ⇒ 判活；`= ts + 1_600` ⇒ 判死（SETTLE 窗口的两侧各一条） |
| U-5 | `resumed`：`nowMs - ts = 11_900` 判活、`12_100` 判死；且 `lifecycle="paused"` 时立即判死 |
| U-6 | 未登记的 `kind`（强转一个假值）⇒ `isAiTradeNoticeLive` 返回 `false` 且**不抛** |
| U-7 | `mintAiTradeNotice(kind, "", …)` 与 `(kind, "   ", …)` 均返回 `null`（不留空条） |
| U-8 | `Object.keys(AI_TRADE_NOTICE_RULES)` 与写死的六元组集合逐元素相等（规则表完备性；`satisfies` 已在编译期守一半，这条守运行期） |
| U-9 | `currentAiTradeEpoch(null, resumableCard, ms)` ⇒ `lifecycle === "paused"`、`groupId === null`（冷占位路径） |
| **U-10** | **未落地宽限（修 RP0-1）**：`paused` 提示铸于 `lifecycle="running"`（pause 回执落地时本地态尚未翻转）且 `reconciledAtMs <= notice.ts` ⇒ **判活**；把 `reconciledAtMs` 推到 `notice.ts + 1` ⇒ **判死**。两条断言必须成对，只写前一条等于把宽限做成了永久豁免 |
| **U-11** | **`persist` 位（修 RP1-5）**：`AI_TRADE_NOTICE_RULES` 里 `persist === true` 的 kind 集合**恰好**是 `{"ended","backend_ended"}`；且一条 `ended` 提示在 `epoch.reconciledAtMs` 为上一个进程的旧时戳、`ts` 为一天前时**仍判活**（证明 `settledByReconcile:false` + `ttlMs:null` 让跨进程旧戳不参与判活） |
| **U-12** | **铸造闸（修 RP1-6）**：当前槽位是一条**仍判活**的 `paused`（sticky）⇒ `noteAiTradeNotice("action_unknown", …)` **不改变槽位**；把 lifecycle 推到 `running` 让那条 `paused` 判死后再铸 ⇒ 槽位被替换。反向：`noteAiTradeNotice("ended", …)`（sticky）**永远**能覆盖 |
| **U-13** | **`action_error` 的纪元（修 RP0-2）**：铸于 `{groupId:"g1", resumedCount:0}` 的 `action_error` 在 `resumedCount` 变 1 之后**判死**，**即使 `lifecycle` 仍是 `"paused"`**；`resumedCount` 不变时无论 `lifecycle` 怎么在 `paused`/`running` 之间来回都**判活**（`validIn:[]` 的语义） |

### 8.2 组件用例（vitest + testing-library）

> ⚠️ 组件用例必须在 `apps/web` 目录内、`NODE_ENV=test` 下跑（否则 React act() 报错），
> 这是本仓库成文的环境约束。

| 编号 | 断言 |
|---|---|
| C-1 | **AC-1 的直接复现**：先 `useAppStore.setState({ aiTradeNotice: mint("paused", …, epoch(paused)) })` 并渲染 `AiTradeNoticeBar` ⇒ 文案在场；再把 store 的 `aiTradeGroup.state` 置 `"running"`、`resumedCount` +1 ⇒ **同一次重渲染后 `ai-trade-notice-bar` 不在文档里** |
| C-2 | `ended` 提示在 `aiTradeGroup: null` 下仍渲染，且 `role === "alert"`、按钮文案是「知道了」 |
| C-3 | `resumed` 提示：`vi.useFakeTimers()` 推进 12.5 秒后自行消失（证明 TTL 真的有时钟在走，而不是等下一次外部事件） |
| C-4 | 长文（> 2 行）折叠：默认 `line-clamp-2` 且「展开」按钮 `aria-expanded="false"`；点击后 `true` 且全文可见 |
| C-5 | 分形前缀：五种 kind 各自的 `glyph` 出现在可访问名里（保证「不只靠颜色」这条真的落到了 DOM 上） |
| C-6 | `AiTradeToggle`：mock `resumeGroup` 成功且回执带 `notice` ⇒ store 的 `aiTradeNotice.kind === "resumed"`；回执**不带** `notice`（老后端）⇒ `aiTradeNotice === null` |
| C-7 | `AiTradeToggle`：**两条分支都必须走（修 RP0-2）**。①**本地** resume：`paused` 态制造一条 `error` ⇒ 可见；点【继续】成功转 `running` ⇒ 不可见；再次转回 `paused` ⇒ 仍不可见。②**外部**迁移（这条才是 E-13 的可达路径，v1 漏了）：`paused` 态制造 `error` ⇒ 可见；**不走任何本地 mutation**，直接 `useAppStore.setState` 把 `aiTradeGroup.state` 置 `running` 且 `resumedCount` +1 ⇒ 不可见；再把 `state` 置回 `paused`（`resumedCount` 保持 +1 后的值）⇒ **仍不可见**。只写 ① 的话，这条门禁在缺陷仍然存在时恒绿 |
| C-8 | `AiTradeMemberRow`：`session.message` 带过期 `messageEpoch` ⇒ 不渲染；`messageEpoch` **缺席** ⇒ 照常渲染（§6.2 的老数据兼容）。**再补一条（修 RP1-4）**：成员 status 不含 `lifecycle` 键（总闸关）时走一次 `noteAiTradeMemberMessage` ⇒ `messageEpoch` **仍缺席** ⇒ 跨一次 pause/resume 往返后该状态行**照常渲染** |
| **C-9** | **AC-1 / AC-4 的渲染侧（修 RP0-1 / RP1-7）**：①铸一条 `paused` 并把 store 的 `aiTradeGroup.state` 保持在 `"running"`、`aiTradeReconciledAtMs` 保持 `0` ⇒ 通知条**在场**（未落地宽限）；②只把 `aiTradeReconciledAtMs` 推到 `notice.ts + 1`、**其余 store 字段一个都不动** ⇒ 通知条**消失**。第②步同时是「组件确实订阅了 `aiTradeReconciledAtMs`」的唯一证明 —— 不订阅的话它不会重渲染，这条必红 |

### 8.3 源码门禁（`apps/web/lib/aiTradeNotice.source.test.ts`）

> ⚠️ 全部正则写 `\r?\n`：仓库 `core.autocrlf=true`，写死 `\n` 会在干净检出上恒红（成文教训）。

| 编号 | 冻结的性质 | 失败时它在防什么 |
|---|---|---|
| G-N1 | `apps/web` 非测试源码中，`aiTradeNotice:` 作为 `set({…})` 的键**只出现在 `lib/store.ts`** | 绕过 `noteAiTradeNotice` 直写裸字符串，纪元丢失 |
| G-N2 | 全仓非测试源码中不再存在标识符 `setAiTradeLifecycleNotice` / `aiTradeLifecycleNotice` | 改名漏改 —— 旧名还在就说明还有一个不带纪元的写点 |
| G-N3 | `AiTradeNoticeBar.tsx` 与 `AiTradeMemberRow.tsx` 各至少出现一次 `isAiTradeNoticeLive` | 「有写点没读点」/「有读点不过谓词」的第 N 次复发 |
| G-N4 | `lib/aiTrade.ts` 全文**不含** `from "./aiTradeNotice"` | 模块环（§5.3 的依赖方向） |
| G-N5 | `AI_TRADE_NOTICE_RULES` 字面量中 `validIn:` 出现次数 == `AiTradeNoticeKind` 联合成员数（v2 = **7**） | 新增 kind 漏登记规则（`satisfies` 在编译期守，这条在源码层再守一次） |
| G-N6 | `services/quant/app/ai_auto/backtest.py` 与 `backtest_manager.py` 全文 0 次 `resume_notice` | 回测车道零沾染（§3.1 G-6） |
| **G-N7** | `lib/aiTradeTypes.ts` 全文**不含** `from "./aiTradeNotice"` 与 `from "./aiTrade"` | 叶模块被反向依赖 ⇒ 模块环（§5.3 / [评审 RP1-1]）。G-N4 只守了 `aiTrade.ts` 那一端，另一端 v1 没人守 |
| **G-N8** | `components/Agent/AiTradeNoticeBar.tsx` 全文**不含** `text-[var(--long)]` / `text-[var(--short)]` / `text-[var(--accent)]`，且**不含** `--long-soft` / `--short-soft` 两个未定义 token | 面色当文字用 / 引用不存在的 token（§4.8 / [评审 RP1-2]）。既有 `contrastTokens.source.test.ts` 的第 ③ 组只扫**带回退**的 `var(--x, …)` 写法，裸 `var(--long-soft)` 它看不见 |

### 8.4 后端用例（pytest）

| 编号 | 断言 |
|---|---|
| B-1 | `build_resume_notice([], resumed_count=1)` 不抛、返回非空中文串，且**不含** `**`（后端文案进 JSX，markdown 会显字面星号 —— 本仓库第 5 次守它） |
| B-2 | 成员带仓 ⇒ 文案含该 `instId`，但**不含任何张数数字**；且**必须同时含**「自管投影」与「未重新向交易所核实」两个短语（诚实不变式：`state_member_row()` 的 `positionUnknown` 恒 `False`、`position` 取自本地投影，点名标的同样是一句未经核实的断言，[评审 RP2-4]） |
| B-3 | `resumed_count=1` ⇒ 不含「第 1 次继续」；`=3` ⇒ 含「第 3 次继续」 |
| B-4 | `POST /ai-trade/{gid}/resume` 200 回执含非空 `notice`，且 `state == "running"`、`resumedCount` 已 +1；`ai_trade_pause_enabled=False` ⇒ 仍 403，回执体逐字不变 |

### 8.5 验收标准（产品级，可由人手动复现）

- **AC-1（用户报告的那条）**：实盘模式下启动一组 AI 自动交易 → 点【⏸ 暂停】→ AgentPanel 头部下方
  出现暂停回执条 → 点【▶ 继续】并等到武装清单自动关闭 → **暂停回执条必须已经不在**，取而代之的是
  一条绿色的「已继续：…」，并在约 12 秒后自行淡出。全程 AgentPanel 高度不出现跳变。
- **AC-2（外部迁移）**：开两个窗口指向同一后端，**两个窗口都保持在前台可见**。窗口 A 暂停 →
  两个窗口都出现暂停条；在窗口 B 点【继续】→ **窗口 A 的暂停条在 3 秒内消失**（快对账窗口）。
  ⚠️ 窗口 A 若被切到后台标签页，`document.hidden` 那道既有守卫会跳过全部对账 tick，这 3 秒不成立；
  切回前台由 `visibilitychange` 立刻补拉一次，回到前台后**应在 1 秒内**消失。这是既有守卫的行为，
  本轮不改，写在这里是为了让手动验收不误判（[评审 RP2-5]）。
- **AC-3（sticky 跨刷新存活）**（v2 改写，修 RP1-5）：带仓结束（选「保留持仓」）→
  「以下持仓仍留在交易所上…」提示出现 → **刷新页面 → 该提示必须仍在**（它是本轮唯一两条落盘的
  提示之一）→ 点「知道了」才消失，消失后再刷新**不再出现** → 全程点任何其他按钮都不清它；
  在此期间开一组**新的** AI 自动交易 ⇒ 该提示按 `validIn` 退场（这是 §4.2 已接受的语义）。
- **AC-3b（非 sticky 不落盘）**：暂停 → 出现暂停回执条 → 刷新页面 ⇒ **暂停回执条不应出现**
  （由对账重新确认的暂停态自有横幅与徽标呈现），证明 `persist` 位真的只放行了那两种。
- **AC-4（超时如实退场）**：把 `/resume` 打成超时（可用 devtools 限速或临时改
  `httpTimeout` 白名单复现）→ 出现「本次操作结果未知…」→ 保持不动，**≤ 6 秒内它自行消失**，
  界面上的态由徽标给出最终结论。⚠️ 前置条件：此刻槽位上**不能**压着一条仍判活的 sticky
  （否则按 §4.3 的铸造闸，「结果未知」根本不会被铸上去 —— 那是**正确**行为，见 AC-4b）。
- **AC-4b（真钱待办不被「我不知道」顶掉）**（v2 新增，修 RP1-6）：造一次「平仓后暂停但没平完」
  （或直接用一条带「请立即到下方实盘面板手动处理」的暂停回执）→ 提示在场 → 点【继续】并让它超时 ⇒
  **那条暂停回执必须仍在原地**，界面上不出现「本次操作结果未知」把它顶掉；动作在飞的事实由按钮的
  「正在恢复…」与武装清单如实呈现。
- **AC-5（成员状态行）**：制造一次行情断连让成员行出现橙色状态行 → 暂停 → 继续 →
  **该状态行消失**；若断连仍在持续，`AiTradeFeedRecoveryCard` 那张卡**必须仍在**（§4.5 的安全性论证）。
- **AC-5b（总闸关时不清）**（v2 新增，修 RP1-4）：把 `AI_TRADE_LIFECYCLE_TRUTH_ENABLED` 置 false 重启后端
  （此时 `lifecycle` 整键缺席、恢复卡不渲染）→ 制造行情断连让成员行出现状态行 → 暂停 → 继续 ⇒
  **该状态行必须仍在**（没有常驻落点时不许清回声）。验完把总闸改回默认 true。
- **AC-6（无障碍）**：仅用键盘可聚焦到「展开」与「知道了」；屏幕阅读器在暂停/继续时读到
  polite 更新，在孤儿持仓时读到 assertive 中断；强制单色模式下仍能靠 `‖ / ▶ / ! / ?` 区分种类。

### 8.6 回归口径

- 前端：`cd apps/web && NODE_ENV=test npx vitest run`（**必须在 `apps/web` 内跑**）+
  `npx tsc --noEmit`。
- 后端：`cd services/quant && python -u -m pytest -q`（**必须 `-u`**；**不得并发跑全量**，
  测试共用同一个临时 DB —— 两条成文的仓库级约束）。
- 判回归**只认全量**：`-k` 子集跑法不可用于判定回归（成文教训）。
- 本机既有红（与本轮无关）在实施节点须先跑一次基线并记录条数，终审时逐条比对，**不得把既有红
  算作本轮引入，也不得把本轮引入的红算作既有**。

---

## 九、风险与缓解

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R-1 | 谓词判死了一条**仍然成立**的提示（fail-open 到「什么都不说」） | 用户少看到一句该看的话 | 规则表六行**逐行**给出「它凭什么在这一态下不成立」的理由（§4.2）；`ended`/`backend_ended` 两条真钱待办刻意 `groupScoped:false` + 无 TTL + 只能手动关；U-3 冻结这一条 |
| R-2 | `messageEpoch` 清掉了一条**仍在持续**的降级回声 | 用户以为行情恢复了 | §4.5 的安全性论证：每一种持续降级都另有**状态派生**的常驻落点（`AiTradeFeedRecoveryCard` 读 `session.lifecycle.feedRecovery` / `AiTradeLivenessBanner` 读 `session.liveness`）；**但那两个键都是总闸门控**（[评审 RP1-4]），故盖戳的前置条件是「`lifecycle` 键在场」，键缺席即不盖戳、缺席即渲染、逐字回退；AC-5 / AC-5b 各验一半；C-8 冻结两条。**已知残留**：`ai_trade_liveness_enabled=False` 且 `lifecycle` 总闸开着时，活性类回声仍会被清（两闸默认均为 true，不在本轮扩大改动面） |
| R-3 | 改名 `aiTradeLifecycleNotice` → `aiTradeNotice` 漏改某处 | 编译失败（好）或某条提示永不出现（坏） | 改名是**刻意**的（§5.4）；G-N2 源码门禁 + `tsc --noEmit` 双保险；`partialize` 不含该键，无迁移风险 |
| R-4 | 超时提示的 SETTLE 窗口选错，导致「结果未知」提前消失或永不消失 | 用户对着一个已经确定的态继续犹豫 / 对着一句永久的废话 | 1.5s < 3s 快窗口，数学上保证「先于动作出发的那次不算、下一次必算」；U-4 用两条边界值冻结 |
| R-5 | 后端 `notice` 说了一个未经核实的持仓数 | 一句判定性文案里混进假数字（真钱） | `build_resume_notice` **禁止**复述张数（§4.7 docstring 明文）；B-2 用「不含任何数字」的断言机械冻结 |
| R-6 | `resume` 回执被 `response_model` 静默吞掉新键 | 前端拿不到 `notice`，静默退化 | 该端点无 `response_model`（E-7 已核实）；B-4 断言回执里**真的有**这个键，而不是断言后端调用过 |
| R-7 | TTL 时钟在组件里常驻，浪费一个每秒定时器 | 轻微耗电 | `useEffect` 依赖 `notice?.kind`，**只在带 ttl 的 kind 存在时**起表；C-3 用 fake timers 验证行为，代码评审核对依赖数组 |
| R-8 | hooks 声明在早退之后 ⇒ React hook 顺序崩面板 | 整个 AI 面板白屏 | §4.8-4 明文；本仓库已有一次成文事故；组件用例 C-1 ~ C-5 每条都会渲染两种以上状态，顺序错必红 |
| R-9 | 新模块与 `aiTrade.ts` 成环 | 打包期/运行期诡异 undefined | §5.3 的依赖方向 + G-N4 源码门禁；`AiTradeLifecycle` 下沉到叶模块并原地再导出，调用点零改 |
| R-10 | 同时只保留一条分组级提示，新提示会覆盖旧的 sticky 提示 | 「孤儿持仓」/「平仓没完成」被顶掉 | v1 只论了 `ended` 与 `resumed` 互斥（那条确实成立：前者只在 `idle/stopped` 下产生、后者只在 `running` 下产生）。**真正会出事的是 `action_unknown`**（[评审 RP1-6]）：它 `validIn:[]`、任何态下都能铸，会盖掉一条仍然成立的 `paused`（可能正写着「请立即手动处理这些持仓」），随后自己被对账结算掉 ⇒ 两句话一起没了。v2 的封法是规则表的 `sticky` 位 + §4.3 的铸造闸：**非 sticky 不得驱逐仍判活的 sticky**；U-12 冻结正反两向，AC-4b 手动验收。若后续要并存，扩展为数组是**加法**改动，本轮不做 |
| R-11 | 「未落地宽限」把一条**本该判死**的提示留在屏幕上 | 用户多看一句过期的话 | 宽限只在 `now.reconciledAtMs <= notice.ts` 这一段窄窗内生效（正常路径 <1 秒，最坏 3 秒），且**只对 `validIn` 一条判据放行** —— `groupScoped` / `resumeScoped` / `ttlMs` / `settledByReconcile` 四道全部照常硬判。极性是刻意的：这段窗里唯一可能被放行的就是刚刚那次动作自己的回执，而它说的是真钱；U-10 用成对边界值冻结「宽限不是永久豁免」 |
| R-12 | `persist` 让一条上个进程的旧提示水合到新界面上 | 「来历不明的旧话」—— 本轮要消灭的形状本身 | 只有 `ended` / `backend_ended` 落盘，二者 `groupScoped:false` + `resumeScoped:false` + `ttlMs:null` + `settledByReconcile:false`，水合后**照样过谓词**；一旦对账发现后端有在跑的分组即按 `validIn` 判死。U-11 冻结集合与跨进程旧戳不参与判活两件事；AC-3 / AC-3b 正反各验一次 |
| R-13 | `setAiTradeSyncState` 加了第二个形参，某个调用点漏传 | 时戳退回 `Date.now()`（落地时刻）⇒ RP1-3 悄悄复发 | 形参可选、缺席即 `Date.now()`，**行为退化而不是崩**；全仓 `setAiTradeSyncState("ok")` 只有 3 个调用点且全在 `reconcileAiTradeLifecycle` 内（已核），一次改全；`"unknown"` 分支**不打戳**由 U-4 的语义边界间接守住 |

---

## 十、回滚方案

1. **整轮回滚** = `git revert` 本轮提交。本轮**不新增** `Settings` 键、不改 `.env.example`、
   不动 DB —— revert 之后行为逐字节回到今天（含那条不消失的暂停提示）。
2. **只回滚后端增益**：删掉 `api/ai_trade_lifecycle.py` 那三行，`notice` 键即缺席，前端
   `res.notice ?? null` 走「只清不换」分支，**G-1 依然成立**。这条是刻意设计的分层回滚点。
3. **只回滚视觉部分**（两行折叠 / 动效 / 分色分形）：`AiTradeNoticeBar.tsx` 内部改动，
   不触碰 `lib/aiTradeNotice.ts` 与 store，判活逻辑不受影响。

---

## 附录 A：五个既有写点 → 新写法逐条对照（实施依据）

| 位置 | 今天 | 改成 |
|---|---|---|
| `AiTradeToggle.tsx:197`（start 成功） | `setAiTradeLifecycleNotice(null)` | `dismissAiTradeNotice()`（语义：新会话起来了，上一轮的告知使命结束） |
| `AiTradeToggle.tsx:280`（写端点超时） | `setAiTradeLifecycleNotice(e.message)` | `noteAiTradeNotice("action_unknown", e.message)` |
| `AiTradeToggle.tsx:295`（pause 成功） | `setAiTradeLifecycleNotice(res.notice \|\| null)` | `noteAiTradeNotice("paused", res.notice ?? null)` |
| `AiTradeToggle.tsx:304-326`（resume 成功） | **无** | `noteAiTradeNotice("resumed", res.notice ?? null)` + `noteError(null)` |
| `AiTradeToggle.tsx:334`（end 成功） | `setAiTradeLifecycleNotice(describeOrphanPositions(...))` | `noteAiTradeNotice("ended", describeOrphanPositions(...))` |
| `aiTrade.ts:1560-1562`（对账器第七行） | `setters.setAiTradeLifecycleNotice("…请到实盘面板核对持仓。")` | `setters.noteAiTradeNotice("backend_ended", "…请到实盘面板核对持仓。")` |
| `useAiTradeGroupStream.ts:381`（WS error 帧） | `patchAiTradeMemberSession(instId, {message})` | `noteAiTradeMemberMessage(instId, err.message)` |
| `useAiTradeGroupStream.ts:170-183`（`pull`） | `aiTradeApi.status().then(res => reconcile(res, setters))` | 先 `const startedAtMs = Date.now()`，setters 的 `setAiTradeSyncState` 闭包捕获它并透传（§4.3 的出发时刻，修 RP1-3）；`.catch` 分支**照旧只置 `"unknown"`、不打戳** |
| `AiTradeToggle.tsx` 全部 19 处 `setError(…)` / 5 处读点（`:544` 行内 + `:558` / `:574` / `:602` / `:640` 四个弹窗） | 裸 `useState<string \| null>` | `noteError(text)` 铸造 `action_error` + `liveError` 过同一个谓词（§4.6 重写版） |

## 附录 B：实施顺序建议

1. `aiTradeTypes.ts` 下沉 `AiTradeLifecycle` + 新增类型 → `aiTrade.ts` 原地再导出（先跑一次
   `tsc --noEmit` 确认调用点零改，再往下走）。
2. `lib/aiTradeNotice.ts` + `aiTradeNotice.test.ts`（U-1 ~ U-9 全绿之后再碰任何组件）。
3. `store.ts` 的字段更名与 4 个 action → `tsc --noEmit` 会精确点出所有漏改写点，逐个改。
4. `AiTradeNoticeBar.tsx` 抽出 + `AgentPanel.tsx` 换挂载 + 组件用例 C-1 ~ C-5。
5. `AiTradeToggle.tsx` 五个写点 + 局部 `error` 纪元化 + C-6 / C-7。
6. `AiTradeMemberRow.tsx` + C-8。
7. 后端 `pause.py` / `manager.py` / 路由 + B-1 ~ B-4。
8. 源码门禁 `aiTradeNotice.source.test.ts`（G-N1 ~ G-N6）**放最后写**：它冻结的是最终形态，
   早写会在重构途中反复变红、诱使人放宽判据。
9. 全量回归（§8.6 的两条口径）+ 按 §8.5 手动走一遍 AC-1 ~ AC-6（含 v2 新增的 AC-3b / AC-4b / AC-5b）。

**v2 追加的三条实施纪律**（对应评审的两条 P0）：

- 第 2 步写 `isAiTradeNoticeLive` 时，**U-10 与 U-13 必须与谓词同一次提交**。这两条守的是
  「宽限不是永久豁免」与「`action_error` 靠 `resumedCount` 而不是靠 `lifecycle` 判死」，
  而它们各自的反面在 v1 里都是**恒绿**的（RP0-1 / RP0-2 的共同形状）。
- 第 5 步写 C-7 时，**先写外部迁移那条分支**（②），再写本地那条（①）。顺序反了极易在
  「①已经绿了」的错觉下把②省掉 —— 而②才是 E-13 的可达路径。
- 第 8 步的源码门禁里，**G-N7 / G-N8 必须与 G-N1 ~ G-N6 一起写**：它们守的是 v1 完全没人守的
  另一端（模块环的叶侧、未定义 token 的裸写法）。

---

---

## 实施过程发现的方案缺陷

> 本节由**实施节点**追加（v2 正文一字未改）。全部按「不静默偏离」的纪律登记：每条给出取证、
> 实际采用的修正做法、以及它是否影响 v2 的任何一条论证。八条里 F-1 / F-3 / F-4 三条是
> **照字面实现会直接失效**的，其余五条是规格内部的自相矛盾或估算偏差。

### F-1（阻断级）：B-2 的「不含任何数字」与 §4.7 的示例文案、B-3 自相矛盾，字面不可满足

§九 R-5 把 B-2 描述成「用『不含任何数字』的断言机械冻结」，但同一份文档的 §4.7 给出的产出
示例是「已继续：**3** 个标的重新武装完成，……（这是本会话第 **2** 次继续。）」，而 §8.4 的
**B-3** 明确要求断言「`resumed_count=3` ⇒ 含『第 3 次继续』」。三处里任意两处都不能同时成立。

**采用的修正**：B-2 冻结的是**真正的不变式**——「不复述**张数**」，而不是「不出现任何字符」。
实现为三条机械断言：① 该成员持仓 `size` 的数值字面量不出现在文案里（用例取 `size=7`，与文案里
其余任何数都不撞）；② 量词「张」整个不出现；③ RC-6 的两个短语「自管投影」与「未重新向交易所
核实」必须同时在场。这比「不含任何数字」更强也更准：它排除的是「把一个未经核实的持仓数写进
一句判定性文案」，而不是排除计数类的、由后端权威值直接派生的数。**R-5 的风险本身零改动。**

### F-2：§3.2-6 的「对超限文件净增 ≤ 40 行」在 `store.ts` / `aiTradeTypes.ts` 上不可兑现

§7.2 #6 / #8 自己列出的改点就超了这个预算。实测（已做过一轮注释压缩之后）：

| 文件 | 本轮净增 | 其中代码 | 其中注释/空行 | 预算 |
|---|---|---|---|---|
| `lib/aiTradeTypes.ts` | 80 | **38** | 42 | ≤ 40 |
| `lib/store.ts` | 90（108 增 18 删） | **70** | 38 | ≤ 40 |
| `lib/aiTrade.ts` | **+8**（21 增 13 删） | 5 | 3 | ≤ 40 |

> 计数口径：`store.ts` 在本节点开工前就已是 `git status` 里的在制文件（兄弟节点的 journal 特性
> 在它上面 +11 行）。上表**已剔除**那 11 行，只算本轮。

`aiTradeTypes.ts` 的 38 行代码是 §5.3 点名要求的**六个类型声明 + 一个可选键**，其中
`AiTradeLifecycle` 及其 10 行 docstring 是从 `aiTrade.ts` **搬过来的**（那边同步减了 13 行），
跨两个文件净增接近零。`store.ts` 的 70 行代码是 §7.2 #8 逐条列出的那些：4 个新 action、
`setAiTradeSyncState` 与 `patchAiTradeMemberStatus` 两处改写、2 个字段声明、`partialize` 一行、
以及 8 行 import。**没有一行是清单外的东西。**

**采用的做法**：先做一轮注释压缩（`aiTradeTypes.ts` 新增 93→80 行、`store.ts` 新增 114→108 行），保留每一条
「为什么」，删掉复述性的段落；剩下的超额如实登记在此，**不为了凑数字去删载荷性注释**
（CLAUDE.md 第四节要求公开 API 写 docstring、复杂规则写清原因，两者直接冲突时以后者为准）。
两个文件都是既有历史超限文件，本轮不拆分（§3.2-6 的另一半仍然遵守）。

### F-3（阻断级）：G-N7 / G-N8 照字面实现会被**它们自己的说明性注释**判红

G-N7 的判据是「`lib/aiTradeTypes.ts` 全文不含 `from "./aiTradeNotice"` 与 `from "./aiTrade"`」。
但按 §5.3 的要求，那个文件的顶部正要写一句「本文件**绝不**出现 `from "./aiTrade"` ……由 G-N7
冻结」——门禁第一次跑就被这句话绊倒了（实测已复现）。G-N8 同理：`AiTradeNoticeBar.tsx` 里
解释「为什么不能写 `--long-soft`」的那句注释本身含有该字符串。

**采用的修正**：两条的极性刻意相反。
- **G-N7 放宽判据、不放宽性质**：改为**行首锚定 + 单词边界**、只匹配真实 import / export
  语句的正则（`^\s*(?:import|export)\b … from "\.\/aiTradeNotice"`）。注释里提到模块名是合法的；一条会被文字绊倒的门禁不是门禁。
- **G-N8 保留严格的字符串判据**，改的是注释措辞。理由：裸 `var(--long-soft)` 是**静默**无底色，
  必须零容忍；而这一条同时加了一句**正向**断言（组件里用到的每一个 `var(--x)` 都必须在
  `globals.css` 里真的有定义），免得那份禁用名单本身不完整。

RC-4 要求的「G-N8 必须真的红过一次」已执行：临时把 `--long-ink` 改成 `--long`、把 `--surface-2`
改成 `--long-soft`，G-N8 当场红（其余 8 条绿），改回后 9/9 全绿。

### F-4（阻断级）：C-7① 若按 v1 写法会与 ② **同形**，RC-2 想演示的对比不成立

§8.2 C-7① 的字面写法是「`paused` 态制造 error ⇒ 点【继续】成功转 `running` ⇒ 不可见」。若照
最省事的路子实现（用 `useAppStore.setState` 把 `state` 推到 running），① 就**变成了另一条外部
迁移**——两条一起红、一起绿，而 RC-2 的整个论点是「① 在缺陷仍存在时**恒绿**，只有 ② 抓得住」。

**采用的修正**：① 改为**真的走完整条本地 mutation**（点【继续】→ 复核屏 →【继续】，让
`resumeM.onSuccess` 自己去推态）。变异验证：把 `AI_TRADE_NOTICE_RULES.action_error.resumeScoped`
由 `true` 改成 `false`（= 评审 RP0-2 描述的 v1 缺陷形状），重跑 33 条 ——
**① 仍然绿、② 红**。RC-2 的对比因此是可判定的，而不是一句约定。

### F-5：§4.8-1 的色板表没给 `member_status` / `action_error` 备齐呈现三键，而类型要求全登记

`AiTradeNoticeRule` 含 `tone` / `glyph` / `urgent` / `dismissLabel` 四个呈现键，而规则表写成
`satisfies Record<AiTradeNoticeKind, AiTradeNoticeRule>` ⇒ **七种 kind 全都要给值**。
§4.8-1 的表把 `member_status` 那一行整行写成「—」，`action_error` 的 `role` 一栏写「见 §4.6」。

**采用的取值**：`member_status` = `{tone:"warn", glyph:"", urgent:false, dismissLabel:"关闭"}`
（沿用成员行既有的 `--warn` 单行样式，本身不经通知条渲染）；
`action_error` = `{tone:"short", glyph:"×", urgent:true, dismissLabel:"关闭"}`
（行内读点的 `role="alert"` 是既有 JSX 字面量，逐字未动）。两者的呈现键只用于满足类型完备性，
**不产生任何新的视觉落点**，故不触碰任何 WCAG 配对。

### F-6：§4.8-5 说「sticky 的**两种**用『知道了』」，而 §4.2 规则表里 sticky 为 true 的是**三种**

规则表的 sticky 列是 `paused` / `ended` / `backend_ended` 三行。**采用**：按「sticky ⇒ 知道了」
统一实现（三种），其余四种用「关闭」。C-2 断的是 `ended` ⇒「知道了」，与此一致。

### F-7：C-5 要求 glyph 落在**可访问名**里，与「装饰性字符加 `aria-hidden`」的通行做法冲突

分形前缀（`‖ / ▶ / ! / ?`）按前端惯例应当 `aria-hidden`（它是视觉冗余）。但 §8.2 C-5 断的是
「各自的 glyph 出现在**可访问名**里」，藏起来这条就恒绿而性质不成立。

**采用**：**不加** `aria-hidden`。理由与 §4.8-1 那句「绝不只靠颜色」同源——藏起来等于这条无障碍
承诺只兑现在视觉通道上，屏幕阅读器用户拿到的仍是一句无差别的话。代价是朗读时会多念一个符号，
可接受（`role="status"` 只在种类切换时播报一次）。

### F-8：C-7 里那条行内 `error` 的**唯一可达路径**需要一个可控 promise，否则用例空转

`AiTradeToggle` 的既有注释已写明：四个弹窗的 `onClose` 一律清 error，唯一能让行内读点
`(paused || stopped) && liveError && !resumeOpen && !endOpen` 成立的是「续跑复核**在途**时先关掉
弹窗、随后复核才失败」这个竞态。用 `mockRejectedValue` + 点关闭，`onClose` 的 `noteError(null)`
会紧跟着把它清掉 —— 用例断的就成了一个恒不存在的元素。

**采用**：`resumePreview` 返回一个**手动控制的 promise**，先关弹窗、再 `reject`。
另一处取证偏差：续跑弹窗的关闭按钮文案是**「取消」**（武装态下是「收起」），不是「关闭」。

---

## 评审结论

**有条件通过（conditional-pass）。**

设计的核心判断是对的，而且是本轮最有价值的部分：用户报告的现象**不是**渲染时序问题，而是
「一条既不由状态派生、又不带纪元的裸字符串」这种存在形式本身有缺陷；把「它此刻还成不成立」
收敛成一张规则表 + 一个纯谓词，让「继续之后暂停提示消失」变成**结构上成立**的性质，而不是
「某个写点记得去清」，这个方向不必推翻。取证部分（§二的 E-1 ~ E-14）逐条复核，**全部属实**，
包括几条最难自证的：`_status_payload()` 确实不含 `message` 键、`pause_group` 的回执确实不是
`group_status()` 形状、对账器第七行确实是唯一的 `backend_ended` 产生点。

但 v1 有**两条 P0**：它对**暂停**这一侧的时序判断反了（回执落地时本地 `lifecycle` 还没翻转，
`validIn:["paused"]` 会当场判死那条唯一会说出「平仓其实没成功」的话），以及给 `error` 定的纪元
**恰好绕开了 E-13 唯一可达的那条路径**（外部发起的继续）。这两条共享同一个形状 ——
**修法落在它以为的位置之外，而为它写的门禁恰好只走那条已经好的分支**，于是缺陷仍在、用例全绿。
另有 7 条 P1，其中三条是「论证的前提没取过证」（`--long-soft` 这个 token 根本不存在、
`session.feed` 这个读路径根本不叫这个名字、「刷新后由对账器重新产生」这句话是假的），
一条是「判据取自不携带该信息的入参」（SETTLE 取的是落地时刻而不是出发时刻），
一条是类型归属自相矛盾、照字面实现必成模块环，
两条是边界没闭合（sticky 被一句「我不知道」顶掉、组件没声明订阅面）。
以上 **2 条 P0 + 7 条 P1 已在 v2 正文逐条修毕**，6 条 P2 已登记。

### 放行条件（下游实施节点必须逐条兑现，缺一条即视为未完成）

1. **RC-1** 未落地宽限必须与 **U-10** 同一次提交，且 U-10 的两条边界断言**成对**存在。
   只写「宽限下判活」而不写「`reconciledAtMs` 越过 `notice.ts` 后判死」，等于把宽限做成永久豁免，
   §4.3 的整条论证随之失效。
2. **RC-2** **C-7 必须真的走外部迁移分支**（`useAppStore.setState` 直接推 `state` + `resumedCount`，
   不经任何本地 mutation）。只走本地 resume 分支的 C-7 在缺陷仍然存在时恒绿 —— 这正是本轮
   两条 P0 的共同形状，不许再犯第三次。
3. **RC-3** **C-9 的第②步必须只改 `aiTradeReconciledAtMs` 一个字段**。它同时是「组件确实订阅了这个
   字段」的唯一证明；顺手多改一个 store 字段，这条断言就退化成「随便什么变化都会重渲染」。
4. **RC-4** 色板**只许用 `globals.css` 里已定义的 token**。实施前先 `grep -n -- "-soft" apps/web/app/globals.css`
   自证一次；`AiTradeNoticeBar.tsx` 落地后 **G-N8 必须真的红过一次**（临时写入
   `text-[var(--long)]` 验证门禁不是空转），再改回来。
5. **RC-5** `partialize` 那一行必须**按 `persist` 位过滤**，不许写成无条件持久化 `aiTradeNotice`。
   同一次提交里补上 `store.test.ts` 的正反两条（`ended` 进、`paused` 不进）。
6. **RC-6** 后端 `build_resume_notice` 的第二句必须**同时**含「自管投影」与「未重新向交易所核实」
   两个短语，由 **B-2** 机械冻结。热续跑路径上那份持仓断面从未向交易所核实过
   （`state_member_row()` 的 `positionUnknown` 恒 `False`），点名标的与复述张数是同一类断言。
7. **RC-7** 路由并入 `notice` 时必须**先判 body 非空**：`group_status()` 返回 `None` 时原样返回 `{}`，
   绝不产出一份「只有 notice」的回执体。
8. **RC-8** `setAiTradeSyncState` 的第二个形参必须**可选且缺席即 `Date.now()`**，
   且 `"unknown"` 分支**绝不打戳**。三个 `"ok"` 调用点全在 `reconcileAiTradeLifecycle` 内，一次改全。
9. **RC-9** 回归口径照 §8.6 逐字执行：前端必须 `cd apps/web` 且 `NODE_ENV=test`；后端必须
   `python -u -m pytest -q` 且**不得并发跑全量**；**判回归只认全量**，`-k` 子集跑法不作数。
   实施前先跑一次基线并记录既有红的条数，终审逐条比对。
10. **RC-10** 源码门禁（§8.3 的 G-N1 ~ G-N8）放在最后写，但**一条都不能少**。
    G-N7 / G-N8 守的是 v1 完全没人守的另一端（模块环的叶侧、未定义 token 的裸写法）。

### 尺度评价

范围是**合适的**，没有过度设计：一个 ≤200 行的纯核心 + 一个 ≤180 行的组件 + 后端 3 个源文件
共约 30 行，换掉五个分散写点与三类无纪元提示；零 DB 迁移、零新增 `Settings` 键、不进回测车道、
不拆既有超限文件、对每个超限文件的净增都钉了 ≤40 行。§十的三级回滚是可执行的
（尤其「只回滚后端增益、G-1 依然成立」那一层是真实存在的分层点，已复核）。

唯一一处**曾**逼近「顺手改多了」的是 §4.6 —— 它要改 `AiTradeToggle` 里全部 19 处 `setError`。
但那是 P0 修复的必要面：`error` 不带纪元正是 E-13 的根因，而只改读点不改写点会留下第二个
不带纪元的写点，与 G-2 的整条立论冲突。v2 保留这一改动，并用 `action_error` 复用同一张规则表、
同一个谓词，不新造第二套判据 —— 这是本轮把「顺手改多了」压回「必须改」的唯一方式。
