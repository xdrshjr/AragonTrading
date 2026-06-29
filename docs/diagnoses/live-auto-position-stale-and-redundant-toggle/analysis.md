# 诊断报告：自选自动交易持仓恒显「空仓」 + 右侧策略面板「自动交易」开关冗余

> 轮次：第 2 轮（在 `360c1cb feat: add watchlist auto-trade group …` 之上继续修改）
> 节点：Subtask #0 问题定位与分析（**仅分析，不写修复代码、不提交**）
> 关联前序：round-1 自动交易可解释性（`c7420ca`）、左侧自动交易分组（`360c1cb`）

本轮包含两个独立诉求，逐一定位如下。

---

## 一、问题描述（Problem description）

### 问题 A：自选「自动交易」分组里每个标的的持仓恒显示为「空仓」
左侧自选页签的「自动交易」区，运行后每个成员标的下方应显示其真实持仓方向（多 / 空 / 空仓）。
实际表现：无论交易所侧是否已真实开仓，行内持仓状态**一直显示「空仓」**，与真实实时下单情况不一致。

### 问题 B：右侧「交易策略」面板上的「自动交易」开关与相关功能已冗余
左侧自选面板已具备自动交易能力（`360c1cb` 引入的 watchlist 自动交易分组）后，右侧「交易策略」
面板标题栏右上的「自动交易」开关，以及其下的状态条 / 事件日志 / 成交记录，已不再需要，应移除以
避免两套自动交易入口并存造成的困惑与潜在冲突。

---

## 二、复现步骤（Reproduction steps）

### 问题 A —— 确定性最小复现（无需真实 OKX，桩驱动）

用一段桩驱动脚本驱动 `LiveAutoSession` 走「订阅 → 暖机 → 首根实时确认根开多仓」，
打印订阅期推送的全部 WS 事件类型，证明开仓后**没有任何携带 `positionSide` 的 `status` 事件**被推送。

复现脚本（已实跑，路径仅为临时；核心逻辑镜像 `services/quant/tests/test_live_auto_session.py` 的桩）：

```python
# 关键片段：构造 1H/long 信号会话 → subscribe → feed 两根确认根 → drain 队列
session, calls = _make_session()                 # signals_by_bar={1:[Signal("long")]}, priming=False
q = session.subscribe()
connect_status = session._status_payload()       # 连接时下发的 status
await session.feed_candle(_candle(0))            # 暖机根，无信号
await session.feed_candle(_candle(1))            # 首实时确认根 → 真实开多
events = _drain(q)
```

运行（在 `services/quant` 下 `PYTHONPATH=. python repro_position_stale.py`）实测输出：

```text
[connect] status.positionSide = 'flat'  (this is what the WS sends on connect)

[after OPEN] executor.projected = 'long'
[after OPEN] orders placed         = 1
[after OPEN] event types pushed     = ['equity', 'candle', 'signal', 'trade', 'equity', 'candle', 'position']
[after OPEN] status events' positionSide = []          # ← 开仓后没有任何 status 事件
[after OPEN] position events' side       = ['long']     # ← 只有 position 事件带了 long
[after OPEN] _status_payload() now says  = 'long'       # ← 后端其实知道是 long，但从不推送
[FRONTEND] status.positionSide='flat' ?? position.side='long'
[FRONTEND] => row shows side = 'flat'  (label: 空仓)
RESULT: real position is LONG, but no status event carried it; row stays '空仓'. BUG REPRODUCED.
```

### 问题 A —— 真实 UI 路径（人工，需实盘账户与 `LIVE_TRADING_ENABLED`）
1. 配置 OKX 实盘默认账户（含 secret/passphrase），设 `LIVE_TRADING_ENABLED=true`，账户模式切到「实盘」。
2. 左侧自选页签把某标的拖入上方「自动交易」区，设分配百分比，点分组开关启动。
3. 等到首个实时确认根触发策略开仓，交易所侧确有持仓（可在下方实盘面板「持仓」处核对为多/空）。
4. 观察该成员行下方持仓状态：**始终显示「空仓」**，不随真实持仓变化。

### 问题 B —— UI 观察（人工）
1. 右侧「交易策略」面板切到「模拟 / 实时」（`mode === "paper"`）模式。
2. 标题栏右上出现「自动交易」开关，其下出现状态条 / 事件日志 / 成交记录区块。
3. 这一整套与左侧自选「自动交易」分组功能重复，即为应移除对象。

---

## 三、根因分析（Root cause analysis，含证据）

### 问题 A：前端持仓取值被「陈旧的 status.positionSide」屏蔽，而后端在开/平仓时不刷新该字段

**前端取值与渲染（直接现场）**
`apps/web/components/Chart/AutoTradeMemberRow.tsx:45`：

```ts
const side = runtime?.status?.positionSide ?? runtime?.position?.side ?? "flat";
```

`apps/web/components/Chart/AutoTradeMemberRow.tsx:27` 与 `:92`：

```ts
const POSITION_LABEL: Record<string, string> = { long: "多", short: "空", flat: "空仓" };
// …
{POSITION_LABEL[side] ?? "空仓"}
```

JS 的 `??` 仅在左值为 `null`/`undefined` 时回落。字符串 `"flat"` 非空值，因此一旦
`runtime.status.positionSide === "flat"`，就会**短路屏蔽**掉真实的 `runtime.position.side`，
始终渲染为「空仓」。

**store 写入是整体替换，不会自然「合并出」正确值**
`apps/web/lib/store.ts:453-456`（经 `patchLiveAutoMember`，见 `:247-256`）：

```ts
setLiveAutoMemberStatus: (instId, status) =>
  set((s) => patchLiveAutoMember(s.liveAutoGroup, instId, (m) => ({ ...m, status }))),
setLiveAutoMemberPosition: (instId, position) =>
  set((s) => patchLiveAutoMember(s.liveAutoGroup, instId, (m) => ({ ...m, position }))),
```

`status` 与 `position` 各自独立整体替换。`status.positionSide` 只会被新的 `status` 事件更新。

**WS 事件路由：status 与 position 是两条独立通道**
`apps/web/lib/useLiveAutoGroupStream.ts:118-138` `routeMemberEvent`：`snapshot` 用 `snap.status` 设
status、`snap.position` 设 position；`position` 事件只更新 position；`status` 事件只更新 status。
即 `status.positionSide` 的新鲜度完全取决于后端是否推送新的 `status` 事件。

**后端：连接即下发一次 status，但开/平仓时不再推送 status**
- 连接握手（`services/quant/app/api/live_auto.py:171-178`）原子下发 `status(snapshot)` + `snapshot`，
  其 `positionSide` 取自当时的 `_status_payload()`（连接时通常为 `flat`）。
- `_status_payload()`（`services/quant/app/live_auto/session.py:430-437`）确实带 `positionSide: self.executor.projected`，
  数据本身是对的。
- 但 `status` 事件**只**在以下时机推送：会话 `start()`（`session.py:160`，此时 `flat`）、`stop()`（`:193`）、
  行情停滞降级 / 恢复（`:280` / `:289`）。
- 正常的「开仓 / 平仓 / 反手」均发生在 `_on_confirmed`（`session.py:305-323`）→ `executor.apply`，
  其中开仓 `_do_open` 仅返回 `[{"type":"trade", ...}]`（`services/quant/app/live_auto/executor.py:193`），
  **不**返回 status；随后 `_emit_position`（`session.py:425-428`）推一条 `position` 事件。
  全程**没有**刷新 `positionSide` 的 status 事件 —— 与上节复现输出
  `status events' positionSide = []` 完全一致。

**结论（A）**：`status.positionSide` 在会话开跑后冻结在连接时的 `flat`，而前端 `?? ` 链优先用它，
导致真实 `position.side='long'/'short'` 被屏蔽，行恒显「空仓」。

> 附带缺陷（同源、需一并考虑）：平仓时 `position_payload` 在 `projected=='flat'` 返回 `None`
> （`executor.py:107-112`），`_emit_position` 因 `if pos is not None` 而**不推**任何 position 事件
> （`session.py:425-428`），所以 `runtime.position` 在平仓后也**不会被清回 flat**，停留在上一笔开仓方向。
> 这意味着「只把前端 `??` 顺序对调成 `position.side ?? status.positionSide`」并不够——平仓后又会错显为持仓。
> 真正可靠的单一真源应是 `status.positionSide`（它由 `executor.projected` 经 `reconcile_truth` 用真实 OKX
> 持仓校正，见 `executor.py:252-272`），因此修复应让该字段在持仓变化时被推送刷新。

> **【评审补强证据 R1 · 后端存在两类 `status` 事件】** 这是根因「`status.positionSide` 既不刷新、也不会被偶发事件意外修正」的关键，原报告未点明，补全如下：
> - **会话级全量** `session._status_payload()`（`session.py:430-437`）：**唯一**携带 `positionSide=self.executor.projected` 的 status。仅 4 处推送——`start()`/`stop()`/停滞降级/恢复（`session.py:160/193/280/289`）。
> - **执行器级轻量** `executor._status_event(message)`（`executor.py:467-469`）：仅 `{state, message, sessionPnlUsd}`，**不含 `positionSide`**。用于 reconcile 抖动/降级恢复/外部平仓/反手未清算/单笔过小等提示（`executor.py:172,175,237,274,282,288`）。
> 因此正常开仓持有路径上**一条 status 都不会推**（恒显空仓）；而当偶发推一条轻量 status 时，前端 `setLiveAutoMemberStatus` 整体替换会把 `status.positionSide` 变成 `undefined`，`??` 链反而回落到 `position.side`——这恰好解释了「持有时恒空仓、外部平仓时却能短暂显示对」的差异。**推论：A1 修复必须推送全量 `_status_payload()`（带 positionSide），绝不能用 `executor._status_event`。**

### 问题 B：右侧策略面板仍渲染整套「单标的」自动交易 UI，已被左侧分组取代

`apps/web/components/Strategy/StrategyPanel.tsx:440-446`：

```tsx
{/* 实时模式：标题栏右上「自动交易」开关… */}
{mode === "paper" && <AutoTradeToggle />}
…
{mode === "paper" && <AutoTradeStatusBar />}
{mode === "paper" && <AutoTradeEventLog />}
{mode === "paper" && <AutoTradeTradesTable />}
```

- `AutoTradeToggle`（`apps/web/components/Strategy/AutoTradeToggle.tsx:67-90`）驱动的是**遗留的单标的**自动交易引擎：
  `liveAutoApi.start({ strategyId, instId, bar, … })` → `POST /live-auto/start`，并写单标的 store
  `liveAutoSessionId`。
- 该路径已被 `360c1cb` 引入的「自选自动交易分组」取代：左侧 `AutoTradePanel` / `liveAutoGroupApi`
  （`POST /live-auto/group/start`）+ store `liveAutoGroup`。两者面向同一个后端「单活跃分组注册表」，
  **同时启用会相互冲突 / 困惑**（单标的旧端点内部也只是 1 成员兼容分组）。
- `AutoTradeStatusBar` / `AutoTradeEventLog` / `AutoTradeTradesTable` 均读取单标的 store
  （如 `AutoTradeTradesTable.tsx:31` 取 `s.liveAutoTrades`），分组路径并不写该 store，故这些区块在分组
  使用场景下也是空壳或重复入口。

**结论（B）**：右侧策略面板 `mode === "paper"` 分支下的这 4 个区块是遗留单标的自动交易 UI，功能已被
左侧分组覆盖，应从右侧面板移除。

---

## 四、影响面（Impact surface）

| 范围 | 受影响内容 |
|---|---|
| 问题 A 用户面 | 所有使用左侧自选「自动交易」分组的实盘用户：持仓列恒显「空仓」，无法据此判断各标的真实方向/盈亏 side，削弱审计与风控可见性。会话盈亏 `sessionPnlUsd` 不受影响（它走 status.message/快照另算），仅持仓方向标签错误。 |
| 问题 A 代码面 | 前端：`AutoTradeMemberRow.tsx`（渲染）；后端：`live_auto/session.py`（status 推送时机）。`position` 事件在平仓时不下发是同源附带缺陷。 |
| 问题 B 用户面 | 实时/模拟模式下右侧「交易策略」面板：移除遗留「自动交易」开关 + 状态条 + 事件日志 + 成交记录，统一由左侧分组入口承担。 |
| 问题 B 代码面 | `StrategyPanel.tsx:440-446` 的条件渲染与相应 import；可选地清理 `AutoTradeToggle/StatusBar/EventLog/TradesTable` 及单标的 `liveAuto*` 引擎（store `liveAutoSessionId`、`liveAutoApi`、`/live-auto/start` 等）。 |
| 回归红线（B 必须核对） | round-1「成交记录可展开看触发原因」不得被回归。左侧 `TradesTable`（`apps/web/components/Strategy/TradesTable.tsx:69-134`）已支持按 `trade.explain` 展开，并被 `ChartWorkspace.tsx:367` 以 `overlayTrades`（当前 instId 的分组成员成交）喂入。**但**实盘账户模式下底部区域会切换为 `LiveBottomPanel`（`ChartWorkspace.tsx:323-325`），可展开的 `TradesTable` 仅在非实盘底部页签可见。移除右侧区块前，须确认分组自动交易的「可展开成交记录」在**实盘模式**下仍有可达入口（图表 marker tooltip / 或为实盘模式补充成交记录入口），避免删掉右侧后实盘下失去 round-1 审计面。 |

---

## 五、候选修复方案对比（Candidate fixes）

### 问题 A

| 方案 | 做法 | 侵入性 | 风险 | 工作量 |
|---|---|---|---|---|
| **A1（推荐）后端在持仓方向变化时推送 status** | 在 `_on_confirmed` / `_monitor_tick` 派发 executor 事件后，检测 `executor.projected` 是否较上次变化，变化即 `await self._push({"type":"status","data": self._status_payload()})`。保持 `status.positionSide` 为权威单一真源，开/平/反手都即时刷新。 | 低（`session.py` 数行 + 一个上次 side 缓存） | 低：协议不变；store 已幂等整体替换 status；前端零改动 | 低 |
| A2 后端每根确认根/每次盯市 tick 无条件补推 status | 在 `_emit_position` 后无条件补一条 status。实现更简单，但 WS 略增 status 频率（每根 + 每 tick 一条）。 | 低 | 低：略增流量 | 很低 |
| A3 纯前端调整优先级 | 把取值改为 `position?.side ?? status?.positionSide`，并补「平仓时把 position 清回 flat」。 | 中：需前端 + 后端补「平仓推 flat position」两处 | 中：单改前端顺序会在平仓后错显持仓（见三-A 附注），必须同时改后端 position 下发，触面反而更大 | 中 |

### 问题 B

| 方案 | 做法 | 侵入性 | 风险 | 工作量 |
|---|---|---|---|---|
| **B1（推荐）仅移除右侧 UI 渲染 + 失效 import** | 删除 `StrategyPanel.tsx:441,444,445,446` 四处条件渲染与对应 import，保留组件/引擎文件不删，最小 diff。 | 低 | 低：不动后端与单标的引擎；先确认四-表「回归红线」 | 低 |
| B2 移除右侧 UI 并整体下线单标的引擎 | 在 B1 基础上删除 `AutoTradeToggle/StatusBar/EventLog/TradesTable` 组件、单标的 `liveAuto*` store/api、`/live-auto/start` 旧端点等。 | 高：跨前后端、波及测试 | 中：单标的旧端点/兼容分组仍被后端管理器引用，需逐处确认无残留依赖 | 高 |

---

## 六、推荐方案（Recommended fix）

- **问题 A：采用 A1（后端在 `executor.projected` 变化时推送一条 `status`）。**
  理由：`status.positionSide` 本就是设计上的持仓真源（由 `executor.projected` 经 `reconcile_truth` 用真实
  OKX 持仓校正），让它在开/平/反手时被刷新，既修复「开仓后恒显空仓」，又顺带修复「平仓后不归位」的同源
  附带缺陷；前端零改动、协议不变、store 已幂等，破坏面最小。A2 可作为更省事的退路但 WS 略冗余；A3 触面更大
  且单独使用不正确，不推荐。

- **问题 B：采用 B1（仅移除右侧四处渲染 + 失效 import）。**
  理由：完全满足「右侧不再需要自动交易开关与功能」的诉求，diff 最小、风险最低，不牵动后端与单标的引擎。
  **落地前置校验（交由 Subtask #2/#3 执行）**：确认左侧分组的「可展开成交记录（含触发原因）」在**实盘账户模式**
  下仍有可达入口（见四-表回归红线），否则需先补该入口再移除右侧区块，确保 round-1 可解释性审计不被回归。
  是否进一步整体下线单标的引擎（B2）属于后续清理，不在本轮最小修复范围。

---

## 七、证据索引（便于评审复核）

- 前端持仓取值/标签：`apps/web/components/Chart/AutoTradeMemberRow.tsx:27,45,92`
- store 整体替换 + 局部 patch：`apps/web/lib/store.ts:247-256,453-456`
- WS 事件路由（status/position 双通道）：`apps/web/lib/useLiveAutoGroupStream.ts:118-138`
- 后端 status 推送时机：`services/quant/app/live_auto/session.py:160,193,280,289`
- `_status_payload`（带 positionSide）：`services/quant/app/live_auto/session.py:430-437`
- 开仓只返回 trade、`_emit_position` 仅非空才推：`services/quant/app/live_auto/executor.py:107-112,193`；`session.py:425-428`
- WS 连接握手：`services/quant/app/api/live_auto.py:171-178`
- 右侧遗留自动交易 UI：`apps/web/components/Strategy/StrategyPanel.tsx:440-446`
- 单标的开关引擎：`apps/web/components/Strategy/AutoTradeToggle.tsx:67-90`
- 右侧成交记录读单标的 store：`apps/web/components/Strategy/AutoTradeTradesTable.tsx:31`
- 左侧可展开成交记录与实盘底部切换：`apps/web/components/Strategy/TradesTable.tsx:69-134`；`apps/web/components/Chart/ChartWorkspace.tsx:323-325,367`
- 复现脚本输出：见本报告「二、复现步骤」问题 A 实测块
- 右侧表读单标的 store vs 分组流写分组 store（数据源不同）：`apps/web/components/Strategy/AutoTradeTradesTable.tsx:31`；`apps/web/app/page.tsx:475-484`；`apps/web/lib/useLiveAutoGroupStream.ts:118-138`
- 实盘底部不渲染可展开 `TradesTable`：`apps/web/components/Live/LiveBottomPanel.tsx:48-52`（`LiveFillsTable`/`LivePositionsTable`）；图表 marker 无结构化展开：`apps/web/components/Chart/CandleChart.tsx:480`
- 两类 status 事件：`services/quant/app/live_auto/session.py:430-437`（全量带 positionSide）vs `services/quant/app/live_auto/executor.py:467-469`（轻量无 positionSide）

---

## 八、评审结论（Review Verdict）

> 评审人：Subtask #1（资深评审）。已**逐文件复核**全部证据引用：`AutoTradeMemberRow.tsx:45,92`、`session.py:160/193/280/289/305-323/425-437`、`executor.py:107-112/193/251-275/467-469`、`useLiveAutoGroupStream.ts:118-138`、`live_auto.py:171-178`、`StrategyPanel.tsx:440-446`、`AutoTradeToggle.tsx:67-90`、`AutoTradeTradesTable.tsx:31`、`ChartWorkspace.tsx:323-325/367`、`page.tsx:475-484`、`LiveBottomPanel.tsx:48-52`、`CandleChart.tsx:480`。结论如下。

### 1. 根因证据：成立（airtight，无反例）
- **Bug A 成立。** 在「连接 → 暖机 → 开仓持有」正常路径上：连接时下发 `_status_payload()`（`positionSide='flat'`）；其后开仓只经 `_do_open` 返回 `trade`（`executor.py:193`）+ `_emit_position` 推 `position`（`session.py:425-428`），**全程无任何携带 positionSide 的 status**。前端 `status.positionSide('flat') ?? position.side` 被陈旧 `flat` 短路，恒显「空仓」。复现脚本输出 `status events' positionSide = []` 与代码路径完全一致；新增「两类 status 事件」补强证据（评审补强 R1）排除了「偶发轻量 status 会意外修正」的反例假设。
- **同源附带缺陷成立。** 平仓后 `position_payload` 返回 `None`（`executor.py:108`）→ `_emit_position` 不推 position（`session.py:427`），`runtime.position` 不归位。故「仅前端对调 `??` 顺序」不充分（平仓后会错显持仓），与三-A 附注一致。
- **Bug B 成立。** `StrategyPanel.tsx:441,444,445,446` 四处 `mode === "paper"` 渲染遗留单标的自动交易 UI；`AutoTradeToggle` 驱动 `liveAutoApi.start({strategyId,instId,bar,…})`（`AutoTradeToggle.tsx:69-77`）的单标的旧引擎，已被 `360c1cb` 左侧分组取代。

### 2. 推荐修复（每个 Bug 各一个最小方案）

- **Bug A → A1（后端在 `executor.projected` 变化时推送一条全量 `status`）。唯一推荐。**
  - 理由：`status.positionSide` 是设计上的持仓单一真源（经 `reconcile` 用真实 OKX 持仓校正）；让其在开/平/反手/外部平仓时刷新，一并修复「恒显空仓」与「平仓不归位」，前端零改动、协议不变、store 幂等整体替换，破坏面最小。A2（无条件每 tick 补推）功能等价但 WS 冗余；A3（改前端顺序）单用不正确且触面更大，均不取。
  - **落地红线（交 Subtask #2 严格遵守）**：
    1. 推送的必须是 `session._status_payload()`（带 `positionSide`），**禁止**用 `executor._status_event`（无 positionSide，见评审补强 R1）。
    2. 「上次 side」缓存的读写须在持锁路径内完成——`_on_confirmed`（经 `feed_candle` 持 `self._lock`）与 `_monitor_tick`（经 `_monitor_loop` 持 `self._lock`）天然互斥串行（`session.py:232/302`），勿在锁外更新缓存。
    3. 推送须在 `_dispatch(events)` **之后**，确保最终态覆盖同轮可能出现的轻量 status。
    4. 须覆盖 `_on_confirmed`（signals/reverse/time-exit 改 projected）与 `_monitor_tick`（reconcile 侦测外部平仓改 projected，`executor.py:267-273`）两条路径；`stop()` 已于 `session.py:193` 推全量 status，无需重复。建议抽 `_push_status_if_side_changed()` helper 供两处复用，避免逻辑重复。
  - **验证（先红后绿）**：在 `services/quant/tests/test_live_auto_session.py`（已有 `test_first_live_candle_triggers_open`、`test_reconcile_corrects_projected_to_flat` 脚手架）补用例——开仓后 drain 订阅队列，断言存在 `type=="status"` 且 `data["positionSide"]=="long"` 的事件；平仓 / reconcile 外部平仓后断言出现 `positionSide=="flat"`。修复前该断言必失败（复现），修复后通过。

- **Bug B → B1（仅移除 `StrategyPanel.tsx:441,444,445,446` 四处条件渲染 + 失效 import）。唯一推荐。**
  - 理由：精确满足「右侧不再需要自动交易开关与功能」，diff 最小、不牵动后端与单标的引擎。是否整体下线单标的引擎（B2）属后续清理，不在本轮最小修复范围。

### 3. 对 Bug B「回归红线」的评审修正（重要）
原报告把「移除右侧 → 可能丢失 round-1 可展开成交记录」列为**前置阻断**。复核后**修正其因果范围**：
- 右侧 `AutoTradeTradesTable` 读的是**单标的遗留 store** `s.liveAutoTrades`（`AutoTradeTradesTable.tsx:31`），而分组流写的是 `liveAutoGroup.members[instId].trades`（`page.tsx:475-476`、`useLiveAutoGroupStream.ts:121-135`）。**两者数据源不同，分组运行时右侧表恒为空（占位空态）**。故**移除右侧 4 处渲染不会删除分组流的任何审计面**——B1 自身对分组可解释性**零回归**，无需「先补入口再移除」的前置条件。
- 真正的缺口**预先存在**且与 B1 正交：实盘账户模式下底部恒为 `LiveBottomPanel`（`ChartWorkspace.tsx:323-325`）→ `LiveFillsTable`/`LivePositionsTable`，**不渲染**带 `TradeExplainDetail` 展开的 `TradesTable`；分组成交在实盘模式仅以 `CandleChart` 轻量 marker（`CandleChart.tsx:480`，无结构化展开）呈现。该缺口自 `360c1cb` 引入分组功能即存在，**非本轮移除右侧所致**。
- **裁定**：B1 可独立、安全落地，**不被该红线阻断**。但 round-1 诉求针对**实盘**自动交易，建议 Subtask #2 在 B1 同轮**增强**：为实盘模式补一个可达的分组「可展开成交记录」入口（如把分组成交的 `TradesTable` 并入 `LiveBottomPanel` 的「成交记录」页签，或为 marker 增加点击展开 `TradeExplainDetail`），以完整保住 round-1 可解释性审计。**此为增强项，非 B1 的前置阻断。**

### 4. 一致性 / 副作用复核
- A1 的全量 `_status_payload()` 不含 `message` 字段，整体替换会清掉瞬时停滞 banner 文案；但其保留 `degraded` 标志，且 reconcile 失败期 projected 不变（不触发 A1 推送），二者冲突概率极低，可接受。
- A1 不改协议、不改前端、不新增依赖；B1 仅删渲染与 import。均符合「最小破坏面」与项目清洁码阈值，无新孤岛。

### 最终裁决
- **Bug A：采用 A1**（后端 projected 变化即推全量 `_status_payload()`，前端零改）。
- **Bug B：采用 B1**（仅移除右侧四处渲染 + 失效 import；独立落地，不被回归红线阻断；实盘分组可展开成交入口作为同轮增强项一并落地）。
- 证据链已闭合，可进入 Subtask #2 落码。
