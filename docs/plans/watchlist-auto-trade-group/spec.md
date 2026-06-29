# 自选标的「自动交易分组」开发方案（Watchlist Auto-Trade Group）

> 文档版本：**v2（已评审）** · 原作者：方案设计节点（Subtask #0）· 评审/修复：方案评审节点（Subtask #1）· 日期：2026-06-29
>
> 关联既有方案：`docs/plans/instrument-sidebar-tabs/`（三页签标的侧边栏）、
> `docs/plans/live-auto-trading/`（单会话实时自动交易）、
> `docs/plans/live-auto-trading-hardening/`（独立盯市循环 / 容错 / 看门狗）。
> 本方案在两者之上叠加「自选分组 + 多标的同时自动交易 + 每标的仓位百分比」。

---

## 评审记录（Review Notes · Subtask #1）

> 评审维度：可行性（Feasibility）/ 完整性（Completeness）/ 一致性（Consistency）/ 适度性（Right-sizing）。
> 评审已对照**真实代码**核验本方案的关键断言：`live_auto/manager.py`（`_session`/`_start_lock`/`start`/
> `stop`/`get`/`active`/`status_row`/`shutdown`/`_require_*`/`_resolve_budget`/`_build_executor`/
> `_create_run`/`_launch_or_rollback`）、`live_auto/session.py`（`_maybe_session_exit`/`info`/`snapshot`/
> 每会话 `OKXWSClient`）、`config.py`（`live_auto_default_capital_frac=0.01`/`live_auto_max_capital_frac=0.05`/
> `live_default_leverage=3`/`live_max_order_notional_usd=1000`/`live_auto_max_sessions=1` **当前无引用**）、
> `api/live_auto.py`（4 端点 + WS 用 `manager.get(session_id)`）、`main.py` 全局 `HTTPException` 处理器
> （把 `detail` dict 原样包成 `{error: detail}`）、`schemas.py`（camelCase + Field 约束 + `PAPER_WINDOW_MAX`）、
> **`paper/manager.py` 既有 `start_batch` / `PaperGroup` / `_groups` 多会话分组且带整组回滚**、
> `store.ts`（persist `version=19`、`partialize` 含 `watchlist`、live-auto 状态为瞬态）、
> `InstrumentSidebar.tsx`（watch/spot/swap 三页签 + `InstrumentRow`）、`page.tsx`（`overlayTrades` 三分支 +
> 单会话 `liveAutoWS` effect）、`lib/ws.ts` `ReconnectingWS` / `liveAutoApi.ts` `liveAutoWS` /
> `instruments.ts` `inferInstType`、`desktop/src/lib/config.js`（`key.startsWith('LIVE_')` 白名单）——
> **断言全部属实**，设计与现有架构高度吻合。下列问题按严重度分级：**P0/P1 已在 v2 正文就地修复**，
> P2 列为实施期条件（见文末「评审结论」）。

### P0（阻断性）
- 无。设计可行、真实资金护栏未削弱、与既有 paper 多会话分组（`start_batch` / `PaperGroup` / `_groups`，
  **已存在且带整组回滚**）同构，可直接落地。

### P1（实现前必须修复——均已在 v2 正文就地修复）
- **P1-1 多标的分组「留空默认预算」几乎必然整组失败**（完整性 / 适度性）。原 §2.3/§4.1 规定「总预算留空 →
  可用 USDT × 1%」再按成员数切分，N 个标的每个仅得 ≈`avail×1%/N`，普遍低于 `minSz` → 整组
  `LIVE_AUTO_MEMBER_BUDGET_TOO_SMALL` 前置失败。单标的旧路径可用的「留空」默认值在分组下变成「几乎
  必然失败」。**修复**：默认总预算随成员数缩放 `min(avail × default_frac × len(members), avail × max_frac)`，
  使每标的仍保留 ≈avail×1% 保守默认且整体不越 5% 硬上限（N=1 与单标的旧路径完全一致）。→ §2.3 / §2.4-4 / §4.1。
- **P1-2 总止盈/止损「按 /100 分摊」在 `Σpct<100` 时被静默缩水**（完整性）。原 §2.6 `tp_i = totalTP × pct_i/100`；
  因弹窗允许 `Σpct ≤ 100`（可留闲置额度），各会话阈值之和 `= totalTP × Σpct/100 < totalTP`，用户填入的
  真实金额「总止盈/止损」被悄悄打折。**修复**：改按 `Σpct` 归一 `tp_i = totalTP × pct_i / Σpct`（`Σpct>0`
  必成立），使各会话阈值恒等于用户所填总额，与闲置额度无关。→ §2.6 / §4.1 / §7.1 / §八 R4。
- **P1-3 前置门禁逐成员重复拉取账户级数据**（可行性 / 可靠性）。原 §2.4-5 对「每个成员」并发
  `fetch_live_positions`，但持仓与可用余额均为**账户级**；N 次重复签名请求在安全关键前置阶段徒增延迟并
  易触发 OKX 限频。**修复**：账户可用 USDT 与持仓**整组各取一次**，逐成员仅取该标的 `fetch_trading_config`
  （`instrumentSupported`/`minSz`/`instMeta`）。→ §2.4-4/5 / §7.1。
- **P1-4 整组前置失败的 `members` 明细可能被路由静默丢弃**（一致性）。核验：全局 `HTTPException` 处理器把
  整个 `detail` dict 原样包进 `{error: detail}`，但既有路由 `except` 仅转发 `{"code","message"}`
  （`api/live_auto.py:70`）。分组端点若照搬该模式，`LiveAutoGroupPreflightError.members` 明细会丢失，
  §4.1 的逐标的原因 UX 落空。**修复**：明确异常携 `.members`，分组路由 `except` 须
  `detail={"code","message","members": exc.members}`。→ §2.4-5 / §四。

### P2（实施期条件，非阻断——见文末「评审结论」）
- **P2-1** `config.live_auto_max_sessions=1` 现已无引用（容量由 `_require_capacity` 直接判 `self._group`），
  泛化后注释「单会话独占」与「N 会话/组」语义冲突。建议更新注释或标注被「单活跃分组 +
  `live_auto_max_group_symbols`」取代，避免文档漂移。
- **P2-2** `start_group`（双阶段门禁 + 整组回滚）单方法将超 CLAUDE.md 阈值（≤50 行 / 圈复杂度≤10 / ≤5 形参）。
  建议拆 `_preflight_group` / `_build_member_sessions` / `_launch_group_or_rollback`，并以 `GroupStartRequest`
  聚合形参；**可直接复用已存在的 paper `start_batch` 回滚模板**（`paper/manager.py:108`）。
- **P2-3** `stop_member` 后该成员去留未定义。建议明确「保留为 `state="stopped"`，v1 不支持单成员重启，
  须整组重启」，并据此判定「无运行中成员则清组」。→ §2.4。
- **P2-4** 后端扇出连接数：N 个成员 = N 个后端 `OKXWSClient`（public + business 各一），R6 仅述浏览器侧。
  建议在 R6 补注后端连接数同受 `live_auto_max_group_symbols` 封顶。
- **P2-5** 单标的旧路径策略选择：统一状态来源时须保留近期修复（提交 `efa83f6`「实盘自动交易用勾选策略而非
  默认首个」）——策略面板 `AutoTradeToggle` 仍用面板所选策略，仅分组弹窗用自带下拉，勿在归一时回退。

---

## 一、Overview（概述）

K 线图左侧的多页签标的列表当前有「自选 / 现货 / 合约」三个页签（`InstrumentSidebar.tsx`）。
本需求只改造**自选页签**：把自选标的拆成上下两大类——上方是「自动交易」类别、下方是「其他自选」
类别，二者同处一个自选列表内、按类别分区显示，自动交易区**始终置顶**。视觉上自选页签变为上下各
约 50% 的两个面板（上：自动交易；下：其他自选）。用户可把下方自选标的**拖拽**进上方自动交易面板，
也可从上方**拖拽出**到下方。自动交易区的成员集合与「其他自选」的并集等于完整自选列表，二者互斥。

被放入自动交易面板的标的不再是「单个标的的自动交易」：当用户按下自动交易面板上的**那一个开关**时，
系统对**所有已加入自动交易类别的标的同时启动自动交易**（一组会话并发运行）。当同时运行多个标的时，
自动交易面板需让用户为**每个标的设置可用仓位百分比**，所有百分比都落在「总仓位限制」之下（即所有标的
百分比之和 ≤ 100%，而总仓位本身又被「可用余额硬上限」夹紧）。每个标的按 `总预算 × 该标的百分比` 得到
自己的会话预算，独立按所选策略下单 / 平仓。

实现路径上，本方案**不重写**已被加固和测试覆盖的单标的引擎（`LiveExecutor` / `LiveAutoSession` 保持
不变，每个会话仍只管一个标的），而是在 `LiveAutoManager` 上把「单会话独占（cap=1）」泛化为
「**单活跃分组（一个 group 含 1..N 个会话）**」，新增分组级 REST 端点；每标的会话仍复用既有
`/ws/live-auto/{session_id}` 事件流（前端为每个成员各开一条 WS）。单标的旧路径（策略面板的
`AutoTradeToggle`）保留为「1 成员、100% 分配」的兼容分组，外部契约不变。前端在自选页签内新增
「自动交易 / 其他自选」上下分区、拖拽、每标的百分比输入与分组开关，运行期态由瞬态分组 store 承载。
设计目标：功能稳定可靠、真实资金护栏零削弱、交互美观优雅且符合人机交互最佳实践。

---

## 二、Technical design（技术设计）

### 2.1 架构与数据流（端到端）

```text
自选页签（InstrumentSidebar / WatchlistTab）
  ├─ 上：AutoTradePanel（自动交易区，置顶，~50%）
  │     header：分组开关 + 聚合状态（运行中/已停止/盈亏）+「已分配 X%/100%」+「平均分配」
  │     body  ：AutoTradeMemberRow[]（可拖拽、可改百分比、可单独移除/单独停止）
  │     drop  ：可接收来自下方的拖拽（加入自动交易类别）
  └─ 下：WatchOtherPanel（其他自选区，~50%）
        body  ：可拖拽的自选行（InstrumentRow）
        drop  ：可接收来自上方的拖拽（移出自动交易类别，仍留在自选）

启动分组：AutoTradePanel 开关 → AutoTradeGroupConfirmModal（选策略/总预算/杠杆/止盈止损 + 风险勾选）
  → POST /api/live-auto/group/start { strategyId, bar, members:[{instId,allocationPct}], totalCapitalUsd, ... }
  → LiveAutoManager.start_group（两段式：全量前置门禁 → 建 N 个 Run → 起 N 个 LiveAutoSession）
  → 每会话 LiveExecutor 按 budget_i = total × pct_i 下真单（复用 okx/trading.py 全部护栏与审计）

运行期：前端对每个成员各开一条 ReconnectingWS(/api/ws/live-auto/{session_id})
  → 事件（snapshot/status/trade/position/equity/candle/error）按 sessionId 路由进
     store.liveAutoGroup.members[instId] → 面板按标的呈现 + 当前选中标的的成交映射为图表 markers

停止：分组开关关闭 → POST /api/live-auto/group/{groupId}/stop（停所有成员、各自平自管仓、落 Run 终态）
      或 AutoTradeMemberRow 的单独停止 → POST /api/live-auto/group/{groupId}/member/{instId}/stop
```

### 2.2 自选分组的数据归属与不变量

- **自选全集** 仍是 `store.watchlist: string[]`（持久化，唯一真源，决定一个标的是否「被收藏」）。
- **自动交易成员** 新增 `store.autoTradeMembers: AutoTradeMember[]`（持久化，偏好态），
  `AutoTradeMember = { instId: string; allocationPct: number }`，是 `watchlist` 的**有序子集**。
- 派生：自动交易区 = `autoTradeMembers`（保持其内部顺序，置顶）；其他自选区 =
  `watchlist.filter(id => !autoTradeMemberIds.has(id))`。两区并集 = 全集，互斥。
- **不变量**（在 store action 内强制，纯函数 `autoTrade.ts` 可单测）：
  1. `autoTradeMembers` 中每个 `instId` 必在 `watchlist` 中；取消收藏（`removeFromWatchlist` /
     `toggleWatchlist` 关闭）须同步从 `autoTradeMembers` 移除。
  2. 自动交易仅支持 **USDT 本位永续 / 交割合约**（`inferInstType` ∈ {SWAP, FUTURES} 且 quote=USDT）。
     拖入 SPOT 标的一律被加入门禁拒绝并就地提示「仅支持 USDT 本位永续 / 交割合约自动交易」。
  3. 运行期（有活跃分组）**锁定成员增删与百分比编辑**（拖拽 / 输入禁用），须先停止分组再改。

### 2.3 仓位百分比（可用仓位 / 总仓位限制）语义

- 「总仓位」= 分组总预算 `totalBudgetUsd`，由确认弹窗的「总预算」输入派生。**留空默认随成员数缩放
  （P1-1）**：`default = min(avail × live_auto_default_capital_frac × len(members), avail ×
  live_auto_max_capital_frac)`，使每标的仍保留 ≈avail×1% 的保守默认（N=1 时与单标的旧路径完全一致），
  非空输入则按可用余额硬上限（5%）夹紧（沿用 `live_auto_*_capital_frac`）。留空时确认弹窗须显式回显
  「将使用默认总预算 = …」，避免用户误以为未生效。
- 「每标的可用仓位百分比」`allocationPct_i` ∈ (0, 100]，约束 `Σ allocationPct_i ≤ 100`。
- 每标的会话预算 `budget_i = totalBudgetUsd × allocationPct_i / 100`。`Σ budget_i ≤ totalBudgetUsd
  ≤ 可用余额 × 5%`，天然不越「总仓位限制」。每标的单笔下单仍受 `live_max_order_notional_usd`（默认
  1000 USDT/单）本地拦截，护栏不变。
- 百分比默认与编辑规则（纯函数）：
  - 新增成员：`allocationPct = max(0, 100 − Σ其余)`（保留用户既有手改值，把剩余额度给新成员）。
  - 「平均分配」按钮：`evenSplit` → 各 `floor(100/n)`，首成员吸收余数使和恰为 100。
  - 手改某成员：夹取到 `[1, 100 − Σ其余]`；面板实时显示「已分配 X%/100%」，
    总和 > 100 或存在 0% 成员或无成员时，**开关禁用**并就地给出原因（不静默回落）。

### 2.4 后端：单会话 → 单活跃分组（`LiveAutoManager` 泛化）

`LiveAutoSession` 与 `LiveExecutor` **零改动**（每会话仍一标的）。改造集中在 `manager.py`：

- 状态：`self._group: LiveAutoGroup | None`（替代 `self._session`），`_start_lock` 仍用于消除
  容量检查与置位之间的 TOCTOU。一次至多一个活跃分组。
- 新增内存形态：
  ```python
  @dataclass
  class LiveAutoGroupMember:
      inst_id: str; allocation_pct: float; budget_usd: float
      session: LiveAutoSession; run_id: int            # session.session_id / state 即时读取
  @dataclass
  class LiveAutoGroup:
      group_id: str; strategy_id: int; strategy_name: str; strategy_kind: str
      bar: str; total_budget_usd: float; leverage: float; created_ts: int
      members: list[LiveAutoGroupMember]
  ```
- `start_group(*, strategy_spec, bar, members_in, account_row, settings, total_capital_usd,
  leverage, take_profit_usd, stop_loss_usd, priming, priming_bars)`：
  1. `_require_enabled(settings)`（双闸：`live_auto_trading_enabled` ∧ `live_trading_enabled`）。
  2. `async with self._start_lock:` → `_require_capacity()`（已有活跃分组 → `LIVE_AUTO_SESSION_LIMIT`）。
  3. 校验 `members_in` 非空、`len ≤ settings.live_auto_max_group_symbols`（否则 `LIVE_AUTO_GROUP_TOO_LARGE`）、
     `Σpct ≤ 100 ∧ 每个 pct ∈ (0,100]`（否则 `LIVE_AUTO_ALLOCATION_INVALID`），去重 instId。
  4. **账户级数据整组各取一次（P1-3）+ 总预算解析与夹紧（P1-1）**：取**默认账户可用 USDT** 与
     **全账户持仓**各一次（二者均为账户级，严禁逐成员重复签名请求徒增前置阶段限频风险；avail 可复用任一
     参考标的 `fetch_trading_config` 回执的 `availUsdt`，与具体标的无关）。
     `total = _resolve_budget_group(total_capital_usd, avail, len(members), settings)`：留空→
     `min(avail × default_frac × len(members), avail × max_frac)`；非空→ 夹到 ≤ avail × 5%。
  5. **第一阶段·全量前置门禁（不下任何单）**：对每个成员并发取**该标的** `fetch_trading_config`，校验
     `instrumentSupported`、`budget_i = total × pct_i/100` 能满足该标的 `minSz`（否则记
     `LIVE_AUTO_MEMBER_BUDGET_TOO_SMALL`）；用**步骤 4 已取的整组持仓快照**校验各成员**无既有持仓**
     （否则记 `LIVE_AUTO_PREEXISTING_POSITION`）。**任一成员失败即整组拒绝**，抛
     `LiveAutoGroupPreflightError`（**携 `.members` 明细**），**不起任何会话**。分组路由 `except` 须把明细
     透传进 detail：`detail={"code": exc.code, "message": exc.message, "members": exc.members}`，全局
     `HTTPException` 处理器据此原样包成 `{error:{code,message,members}}`（P1-4：既有路由 except 仅转发
     code/message，照搬会丢明细）。
  6. **第二阶段·建会话**：对每个成员 `_create_run`（`mode="live-auto"`，config 含
     `groupId/allocationPct/totalBudgetUsd`）+ `_build_executor(budget=budget_i)` + `LiveAutoSession(...)`，
     `take_profit_usd_i = (take_profit_usd × pct_i/100) if take_profit_usd else None`（止损同理，
     **按比例分摊到各会话**，复用既有 `_maybe_session_exit`，零新增盯市任务，见 §2.6）。
  7. 顺序 `await session.start()`；**任一启动失败 → 回滚已起会话（逐个 `stop` 平仓）+ 清空
     `self._group`**，不掩盖原始异常（镜像 `_launch_or_rollback` 与 paper `start_batch` 的回滚）。
- `stop_group(group_id, reason="user_stop")`：对每个成员 `session.stop(reason)`，聚合各自
  `flatten` 结果（含 `flattenFailed`/`residual` 诚实上报），全部停止后 `self._group=None`，返回
  `{groupId, stopReason, members:[{instId,sessionId,runId,flattened}]}`。
- `stop_member(group_id, inst_id)`：仅停某成员（其余继续）；若停后无运行中成员则清 `self._group`。
- `group_status()`：返回活跃分组的只读视图（含每成员 `instId/sessionId/runId/state/positionSide/
  sessionPnlUsd/budgetUsd/allocationPct`），供前端重载后重连。
- `get(session_id)`：跨所有成员按 session_id 查会话（WS 路由用），替代原「仅比对单会话」实现。
- `shutdown()`：停所有成员会话（进程退出场景日志为唯一出口，沿用 H4 不静默吞平仓失败）。
- **兼容层**：旧 `start(...)`/`stop(session_id)`/`active()`/`status_row()` 改为 `start_group` 等的
  thin wrapper（`start` = 1 成员、`allocation_pct=100` 的分组；`status_row` 仅当分组恰 1 成员时回旧
  单会话形态，否则回 `None`，旧 `GET /live-auto/status` 契约不破）。

### 2.5 信号 → 真实订单（不变）

每成员会话内仍是既有链路：确认根 → `strategy.on_candle` → `LiveExecutor.apply` → 同向跳过 /
反向先平后开 / 平仓即平 → `okx/trading.py` 真实下单（继承名义上限、净仓反向拦截、唯一
`clOrdId`、`live_order_audit` 留痕、脱敏）。多标的之间**互不耦合**：各自独立 reconcile、独立时间
止盈、独立交易所侧 OCO。

### 2.6 止盈止损（分组级 → 按比例分摊到会话，零新增任务）

会话级金额止盈止损沿用既有 `LiveAutoSession._maybe_session_exit`。分组的「总止盈/总止损」**按百分比
归一分摊**给各会话：`tp_i = totalTP × pct_i / Σpct`、`sl_i = totalSL × pct_i / Σpct`
（`Σpct = Σ allocationPct_i > 0` 必成立；留空则各会话该阈值为 `None`，不按金额触发）。**按 `Σpct`
而非 /100 归一（P1-2）**：使各会话阈值之和**恒等于**用户所填「总止盈/止损」真实金额，与是否留有闲置
额度（`Σpct < 100`）无关，杜绝真实金额输入被静默缩水。任一标的触及自身阈值即自动平自己的仓并停自身
会话，**不影响**其他成员（多标的组合中「坏标的自停、好标的续跑」更稳健）。每笔交易另自带交易所侧 OCO
（进程退出仍兜底）。**取舍**：相较「分组聚合阈值（和达标停全组）」，按比例归一分摊**复用已测试代码、
无新增 asyncio 盯市任务**，故 v1 选此；聚合阈值列为后续可选增强（见 §八 风险 R4）。

### 2.7 前端：自选页签上下分区 + 拖拽 + 百分比 + 分组开关

- `InstrumentSidebar` 的 `tab==="watch"` 分支从「单列表」改为渲染 `<WatchlistTab/>`：上下 flex 列，
  上 `AutoTradePanel`、下 `WatchOtherPanel`，默认各占 50%（`flex-1 basis-1/2`，可选拖拽分隔条
  调比例，复用 `panelResize.ts` 风格；v1 可固定 50/50 + `min-h` 下限）。`spot`/`swap` 分支不变。
- **拖拽**：HTML5 DnD（`draggable` + `onDragStart/onDragOver/onDrop`）。行 `dragStart` 写
  `dataTransfer` 的 `instId`；面板为 drop zone：拖到自动交易区 → `addAutoTradeMember(instId)`；
  拖到其他自选区 → `removeAutoTradeMember(instId)`。拖拽中高亮目标区（`aria-dropeffect` + 视觉描边）。
  键盘可达性：每行提供「加入自动交易 / 移出」按钮作为拖拽的等价操作（无障碍，HCI 最佳实践）。
- `AutoTradeMemberRow`：复用 `InstrumentRow` 视觉 + 追加：拖拽手柄、`AutoTradeAllocationInput`
  （百分比 number 输入，步进/夹取）、运行期单标的态（持仓 多/空/空仓 + 会话盈亏 + 单独「停止」）。
- `AutoTradePanel` header：分组开关（`Switch`）、聚合状态（运行中脉冲点 / 已停止 / 聚合盈亏）、
  「已分配 X%/100%」进度、「平均分配」按钮；禁用态就地显原因（沿用 `AutoTradeToggle` 的可见原因模式）。
- `AutoTradeGroupConfirmModal`：在既有 `AutoTradeConfirmModal` 基础上扩展为多标的：**策略下拉
  选择器**（自带，默认取当前激活策略；不再依赖策略面板复选框，解耦左右两区）、周期（默认当前
  chart `bar`）、总预算 / 杠杆 / 总止盈 / 总止损、成员清单与各自百分比及**每标的预算预览**、真实资金
  风险勾选。
- 运行期 WS：新增 `useLiveAutoGroupStream`（hook，挂载于 `page.tsx`），对 `liveAutoGroup.members`
  每个 `sessionId` 各开一条 `liveAutoWS`，事件经 `handleLiveAutoGroupEvent(event, sessionId, ...)`
  路由进 `store.liveAutoGroup.members[instId]`；重载重连：挂载时 `GET /live-auto/group/status`
  重建分组并重开 N 条 WS。
- 图表 markers：`overlayTrades` 改为「活跃分组时取**当前选中 instId** 对应成员的 trades」
  （`liveAutoGroup.members[instId]?.trades`），其余分支（paper/backtest）不变。
- 兼容：策略面板 `AutoTradeToggle`/`AutoTradeStatusBar`/`AutoTradeEventLog`/`AutoTradeTradesTable`
  改读统一分组状态（单标的即 1 成员分组）；行为不变，仅状态来源归一，避免「左右两套活跃态」。

---

## 三、File / module change plan（文件 / 模块变更计划）

### 后端 `services/quant/app/`

| 文件 | 动作 | 一句话意图 |
|---|---|---|
| `live_auto/manager.py` | 改 | 泛化为单活跃分组：新增 `LiveAutoGroup`/`LiveAutoGroupMember` 与 `start_group`/`stop_group`/`stop_member`/`group_status`/`get`，两段式门禁 + 整组回滚；旧 `start/stop/active/status_row` 降为兼容 wrapper。 |
| `live_auto/manager.py` | 改 | 新增异常 `LiveAutoGroupPreflightError`（携 `members` 明细）、`LiveAutoAllocationError`、`LiveAutoGroupTooLargeError`，均继承 `LiveAutoPreflightError`（带 code/message/status）。 |
| `api/live_auto.py` | 改 | 新增 `POST /live-auto/group/start`、`POST /live-auto/group/{groupId}/stop`、`POST /live-auto/group/{groupId}/member/{instId}/stop`、`GET /live-auto/group/status`；双闸守门、错误体 `{error:{code,message,members?}}`；旧端点保留。 |
| `schemas.py` | 改 | 新增 `LiveAutoGroupMemberIn`、`LiveAutoGroupStartIn`、`LiveAutoGroupStartOut`、`LiveAutoGroupMemberRow`、`LiveAutoGroupRow`、`LiveAutoGroupStatusOut`（camelCase wire，沿用既有风格 + 边界校验）。 |
| `config.py` | 改 | 新增 `live_auto_max_group_symbols: int = 8`（分组标的数硬上限）；其余 `live_auto_*` 复用。 |
| `db/repositories.py` | 复用 | `create_run`/`finalize_run`/`append_live_trade`/`get_run` 不改；分组信息进 Run `config_json`（`groupId/allocationPct/totalBudgetUsd`）。 |

### 前端 `apps/web/`

| 文件 | 动作 | 一句话意图 |
|---|---|---|
| `lib/store.ts` | 改 | 新增持久化 `autoTradeMembers` + actions（add/remove/setAllocation/reorder/evenSplit，含「移出自选即移出自动交易」不变量）；新增瞬态 `liveAutoGroup`（成员 runtime map）+ actions；`partialize` 加 `autoTradeMembers`；`version` 19→20 + migrate。 |
| `lib/autoTrade.ts` | 新增 | 纯函数：百分比 `normalize/total/evenSplit/clampOnEdit/defaultOnAdd`、分组守门 `resolveGroupGate`、每标的预算预览、可自动交易标的判定（`isAutoTradable`，SWAP/FUTURES+USDT）。 |
| `lib/autoTrade.test.ts` | 新增 | 上述纯函数单测（百分比边界、even split 余数、不变量、守门文案、SPOT 拒绝）。 |
| `lib/liveAutoGroupApi.ts` | 新增 | 分组 REST 客户端（group start/stop/member-stop/status）+ 复用 `liveAutoWS`。 |
| `lib/liveAutoTypes.ts` | 改 | 新增 `AutoTradeMember`、`LiveAutoGroupStartReq/Result`、`LiveAutoGroupMemberRow`、`LiveAutoGroupStatusResponse`、`LiveAutoMemberRuntime`、新增停止原因 `group_*`。 |
| `components/Chart/WatchlistTab.tsx` | 新增 | 自选页签上下分区容器（AutoTradePanel / WatchOtherPanel，默认 50/50 + min-h），承接拖拽编排与运行期锁。 |
| `components/Chart/AutoTradePanel.tsx` | 新增 | 自动交易区：header（分组开关 + 聚合态 + 已分配 + 平均分配）+ 成员列表 + drop zone。 |
| `components/Chart/WatchOtherPanel.tsx` | 新增 | 其他自选区：可拖拽自选行 + drop zone（移出自动交易）。 |
| `components/Chart/AutoTradeMemberRow.tsx` | 新增 | 自动交易成员行：行情 + 拖拽手柄 + 百分比输入 + 运行期单标的态 + 单独停止/移出。 |
| `components/Chart/AutoTradeAllocationInput.tsx` | 新增 | 受控百分比输入（夹取、步进、和超限着色）。 |
| `components/Chart/AutoTradeGroupConfirmModal.tsx` | 新增 | 多标的启动确认弹窗（策略下拉 + 周期 + 总预算/杠杆/止盈止损 + 成员预算预览 + 风险勾选）。 |
| `components/Chart/InstrumentSidebar.tsx` | 改 | `tab==="watch"` 改渲染 `<WatchlistTab/>`；`spot/swap` 不变。 |
| `components/Chart/InstrumentRow.tsx` | 改 | 增加可选 `draggable`/`onDragStart`/「加入自动交易」按钮（无障碍等价操作），现有行为不破。 |
| `hooks`（`app/page.tsx` 内或抽 `lib/useLiveAutoGroupStream.ts`） | 改/新增 | 分组 WS 多连接订阅 + 事件按 sessionId 路由 + 重载重连（替代/并存原单会话 effect）。 |
| `app/page.tsx` | 改 | `overlayTrades` 改取当前 instId 的成员 trades；接入分组流 hook 与重发现。 |
| `components/Strategy/AutoTradeToggle.tsx` 等 4 个 | 改 | 状态来源归一到分组（单标的=1 成员分组）；行为不变。 |

### 测试

| 文件 | 动作 | 一句话意图 |
|---|---|---|
| `services/quant/tests/test_live_auto_group.py` | 新增 | 分组 start/stop/member-stop、两段式门禁全量失败聚合、整组回滚、百分比预算分摊、按比例止盈止损、单活跃分组独占（桩 OKX，假凭证）。 |
| `services/quant/tests/test_live_auto*.py` | 改 | 既有单会话测试随兼容 wrapper 调整断言（cap=1 → 单活跃分组语义）。 |
| `apps/web/lib/autoTrade.test.ts` | 新增 | 见上。 |
| `apps/web/components/Chart/*.test.tsx` | 新增 | 分区渲染、拖拽加入/移出、百分比和超限禁用开关、SPOT 拒绝、运行期锁（vitest，`NODE_ENV=test`）。 |

### 桌面 `desktop/`

无需改动：`LIVE_AUTO_*` / `LIVE_*` 经 `desktop/src/lib/config.js` 既有白名单（`key.startsWith('LIVE_')`）
天然透传；不在后端代码里特判桌面端。

---

## 四、Interface design（接口设计）

### 4.1 `POST /api/live-auto/group/start`（受 `LIVE_AUTO_TRADING_ENABLED` ∧ `LIVE_TRADING_ENABLED` 双闸守门）

请求体（`LiveAutoGroupStartIn`，camelCase）：
```jsonc
{
  "strategyId": 12,
  "bar": "1H",
  "members": [
    { "instId": "BTC-USDT-SWAP", "allocationPct": 50 },
    { "instId": "ETH-USDT-SWAP", "allocationPct": 30 },
    { "instId": "SOL-USDT-SWAP", "allocationPct": 20 }
  ],
  "accountId": null,             // 省略=默认实盘账户
  "totalCapitalUsd": null,       // 留空→ min(availUsdt×1%×成员数, availUsdt×5%)（P1-1）；非空按 ≤ availUsdt×5% 夹紧
  "leverage": null,              // 留空→ live_default_leverage (3×)
  "takeProfitUsd": null,         // 分组总止盈金额；按 pct/Σpct 归一分摊到各会话（P1-2，和恒等于本值）；留空→不触发
  "stopLossUsd": null,           // 分组总止损金额；同上（按 pct/Σpct 归一分摊）
  "priming": true,
  "primingBars": 300
}
```
校验：`members` 非空且 ≤ `live_auto_max_group_symbols`；每 `allocationPct ∈ (0,100]` 且
`Σ ≤ 100`；`instId` 唯一；每个 `instId` 须 SWAP/FUTURES + USDT 本位。

成功回执（`LiveAutoGroupStartOut`）：
```jsonc
{
  "groupId": "liveautogrp_xxx", "strategyId": 12, "strategyName": "...",
  "strategyKind": "atomic", "bar": "1H",
  "totalBudgetUsd": 480.0, "leverage": 3,        // totalBudgetUsd 为夹紧后最终生效值
  "members": [
    { "instId": "BTC-USDT-SWAP", "sessionId": "liveauto_a", "runId": 101,
      "allocationPct": 50, "budgetUsd": 240.0, "state": "running" },
    { "instId": "ETH-USDT-SWAP", "sessionId": "liveauto_b", "runId": 102,
      "allocationPct": 30, "budgetUsd": 144.0, "state": "running" },
    { "instId": "SOL-USDT-SWAP", "sessionId": "liveauto_c", "runId": 103,
      "allocationPct": 20, "budgetUsd": 96.0,  "state": "running" }
  ]
}
```
错误体（经 `main` 全局 `HTTPException` 处理器包装为 `{error:{...}}`）：
- 403 `LIVE_AUTO_TRADING_DISABLED`；404 `ACCOUNT_NOT_FOUND` / `NOT_FOUND`(策略)；400 `ACCOUNT_NOT_CONFIGURED`；
- 400 `LIVE_AUTO_ALLOCATION_INVALID`、400 `LIVE_AUTO_GROUP_EMPTY`、400 `LIVE_AUTO_GROUP_TOO_LARGE`；
- 409 `LIVE_AUTO_SESSION_LIMIT`（已有活跃分组）；
- 409 `LIVE_AUTO_GROUP_PREFLIGHT`（整组前置门禁失败，附明细）：
  ```jsonc
  { "error": { "code": "LIVE_AUTO_GROUP_PREFLIGHT", "message": "部分标的无法启动自动交易",
    "members": [
      { "instId": "DOGE-USDT-SWAP", "code": "LIVE_AUTO_PREEXISTING_POSITION", "message": "该标的已有持仓" },
      { "instId": "PEPE-USDT-SWAP", "code": "LIVE_AUTO_MEMBER_BUDGET_TOO_SMALL", "message": "分配预算不足最小下单量" }
    ] } }
  ```

### 4.2 `POST /api/live-auto/group/{groupId}/stop`（刻意不守门，确保随时可停止平仓）
回执：`{ "groupId", "stopReason", "members": [{ "instId","sessionId","runId","flattened": {ok,closedSz,code?,message?,residual?} }] }`。
任一成员平仓失败 → 该成员 `flattened.ok=false` + `residual`，整体仍返回（UI 强提示人工平仓）。

### 4.3 `POST /api/live-auto/group/{groupId}/member/{instId}/stop`（不守门）
仅停某成员；回执 `{ "groupId","instId","flattened": {...} }`；停后无运行中成员则分组清空。

### 4.4 `GET /api/live-auto/group/status`（只读，不守门）
```jsonc
{ "liveAutoTradingEnabled": true,
  "group": null | {
    "groupId","strategyId","strategyName","strategyKind","bar",
    "totalBudgetUsd","leverage","createdTs",
    "members": [ { "instId","sessionId","runId","allocationPct","budgetUsd","state",
                   "positionSide","sessionPnlUsd","degraded","flattenFailed" } ] } }
```

### 4.5 `WS /api/ws/live-auto/{session_id}`（**不变**，事件协议与 `/ws/paper/{id}` 同构）
前端为分组每个成员各开一条；连接即原子 `subscribe → status → snapshot`，后续
`trade/position/equity/candle/error/status` 增量。前端按 `sessionId` 路由到对应 `instId` 成员。

### 4.6 旧端点（保留，兼容单标的快捷路径）
`POST /live-auto/start`、`POST /live-auto/{sid}/stop`、`GET /live-auto/status` 维持原契约
（内部走 1 成员、100% 分配的分组）。

---

## 五、Data model（数据模型）

### 5.1 后端 Pydantic（`schemas.py`，camelCase wire）
- `LiveAutoGroupMemberIn { instId: str(1..32); allocationPct: float(gt=0, le=100) }`
- `LiveAutoGroupStartIn { strategyId:int; bar:str="1H"; members:list[LiveAutoGroupMemberIn];
  accountId:int|None; totalCapitalUsd:float|None(gt=0); leverage:float|None(gt=0,le=125);
  takeProfitUsd:float|None(gt=0); stopLossUsd:float|None(gt=0); priming:bool=True;
  primingBars:int(10..PAPER_WINDOW_MAX)=300 }`（额外约束：`len(members)≥1`、`Σpct≤100`、instId 唯一，
  在 validator 内做边界校验）。
- `LiveAutoGroupMemberRow / LiveAutoGroupRow / LiveAutoGroupStartOut / LiveAutoGroupStatusOut`：见 §四。

### 5.2 会话内存形态（`manager.py`）
`LiveAutoGroup` / `LiveAutoGroupMember`：见 §2.4。`LiveAutoSession` / `LiveExecutor` 内部形态不变。

### 5.3 持久化（DB）
- **无新增表**。每成员会话仍建一行 `runs`（`mode="live-auto"`），`config_json` 追加
  `{ "groupId", "allocationPct", "totalBudgetUsd", "leverage", "strategyId", "strategyName",
  "takeProfitUsd", "stopLossUsd" }`；成交落 `trades`（含 `explain_json`）。可经 `groupId` 在 `config_json`
  事后聚合一组的运行历史。

### 5.4 前端 store（`lib/store.ts`）
- **持久化**（进 `partialize`，`version` 19→20）：
  `autoTradeMembers: { instId: string; allocationPct: number }[]`（自选子集，决定上区成员与百分比）。
- **瞬态**（不入 `partialize`，刷新由 `GET /live-auto/group/status` 重发现）：
  ```ts
  liveAutoGroup: {
    groupId: string; strategyId: number; strategyName: string; bar: string;
    totalBudgetUsd: number; leverage: number; state: "running"|"stopped"; startedTs: number;
    members: Record<string /*instId*/, {
      instId: string; sessionId: string; runId: number; allocationPct: number; budgetUsd: number;
      status: LiveAutoStatus | null; position: PaperPositionEvent | null;
      trades: Trade[]; events: LiveAutoEvent[];
    }>;
  } | null
  ```
- migrate v20：`autoTradeMembers` 缺省补 `[]`（老用户自动交易区初始为空，自选不变）；瞬态字段浅合并回落。

---

## 六、错误码归一

复用既有：`LIVE_AUTO_TRADING_DISABLED`、`LIVE_INSTRUMENT_UNSUPPORTED`、`LIVE_AUTO_PREEXISTING_POSITION`、
`LIVE_AUTO_SESSION_LIMIT`、`LIVE_AUTO_FLATTEN_FAILED`、`LIVE_AUTO_RECONCILE_DEGRADED`、`ACCOUNT_NOT_FOUND`、
`ACCOUNT_NOT_CONFIGURED`、`NOT_FOUND`。新增：`LIVE_AUTO_GROUP_EMPTY`、`LIVE_AUTO_ALLOCATION_INVALID`、
`LIVE_AUTO_GROUP_TOO_LARGE`、`LIVE_AUTO_GROUP_PREFLIGHT`（携 `members` 明细）、
`LIVE_AUTO_MEMBER_BUDGET_TOO_SMALL`。错误体一律 `{error:{code,message,members?}}`，码稳定即对外契约。

---

## 七、Testing & acceptance criteria（测试与验收）

### 7.1 后端单测（离线 pytest，CI 必跑；铁律：一律占位假凭证，严禁写真实凭证；OKX 调用全桩）
- `start_group` 成功：N 成员各建 Run、各起会话、`budget_i = total×pct/100`、回执 `totalBudgetUsd` 为夹紧值。
- 两段式门禁：构造「1 成员有既有持仓 + 1 成员预算过小」→ 抛 `LIVE_AUTO_GROUP_PREFLIGHT`，`members`
  含两条明细，且**无任何会话被创建 / 无任何下单**。
- 整组回滚：令第 2 个成员 `session.start()` 抛错 → 已起的第 1 个被 `stop` 平仓、`self._group` 清空、原异常透传。
- 百分比校验：`Σpct=101`/`pct=0`/空成员/超 `max_group_symbols` 各自映射到对应错误码。
- 单活跃分组独占：有活跃分组时再 `start_group` → `LIVE_AUTO_SESSION_LIMIT`。
- 按比例归一止盈止损（P1-2）：`totalTP=100` + 三成员 50/30/20（Σ=100）→ 各会话 `take_profit_usd=50/30/20`；
  另测 `Σpct<100`：40/30/10（Σ=80）→ 各会话 `take_profit_usd=50/37.5/12.5`（按 `/Σpct` 归一，和=100）。
- 默认预算缩放（P1-1）：`totalCapitalUsd=None` + 3 成员 → `total=min(avail×1%×3, avail×5%)`，每标的预算 ≈avail×1%；
  N=1 时回退到单标的旧默认 avail×1%。
- 前置门禁取数去重（P1-3）：账户持仓与可用余额各只取一次（断言 `fetch_live_positions` 仅被调用 1 次，
  逐成员仅各取一次 `fetch_trading_config`）。
- `stop_group` / `stop_member`：平仓回执聚合、单成员停止不误伤其余、停后空组清理。
- 兼容 wrapper：旧 `start/stop/status_row` 行为与回执契约不变（1 成员分组）。

### 7.2 前端单测（vitest，`NODE_ENV=test`，CI 必跑）
- `autoTrade.ts`：百分比 normalize/even-split 余数/手改夹取/新增默认/总和守门、`isAutoTradable`
  （SWAP/FUTURES+USDT=true，SPOT=false）、`resolveGroupGate` 文案矩阵。
- store：`addAutoTradeMember`/`removeAutoTradeMember`/`toggleWatchlist` 关闭联动移除自动交易成员、
  `setAutoTradeAllocation` 夹取、migrate v20 补默认。
- 组件：上下分区渲染与置顶、拖拽加入/移出（mock DnD）、SPOT 拖入被拒并提示、和超限/0% 成员禁用
  开关并显原因、运行期锁定编辑、成员行运行态与单独停止。

### 7.3 真实账户联调（opt-in，手动 / 网络标记，不进 CI）
默认账户 + `LIVE_AUTO_TRADING_ENABLED=LIVE_TRADING_ENABLED=true` + 极小总预算下，加入 2~3 个
USDT 永续、设百分比、启动 → 校验各标的真实下单、各自 OCO、分组/单成员停止平仓、重载重连、
某标的预置持仓被前置门禁整组拒绝。

### 7.4 Definition of Done（本设计交付物）
`docs/plans/watchlist-auto-trade-group/spec.md` 存在且 `git status` 可见；含 Overview / Technical design /
File-change plan / Interface / Data model / Testing / Risks 全部章节；篇幅 ≥ 800 字；详尽到下游可逐行实现。

---

## 八、Risks & mitigations（风险与缓解）

- **R1 真实资金多标的并发放大风险**：多标的同时下单放大敞口。缓解：自动化总闸 + 写链路总闸双闸
  默认关闭；总预算硬上限（avail×5%）；`Σpct≤100`；每标的单笔名义上限（1000 USDT/单）；
  `live_auto_max_group_symbols`（默认 8）封顶；前置门禁拒绝既有持仓标的；每笔自带交易所侧 OCO。
- **R2 部分成员启动失败的中间态**：缓解：**两段式**（先全量门禁、再建会话），第二阶段任一失败
  **整组回滚平仓**，杜绝「半启动」真实资金悬挂。
- **R3 停止时部分标的平仓失败**：缓解：沿用 H4 有界重试 + 诚实 `residual` 上报；分组停止聚合每成员
  `flattenFailed`，UI 红色强提示到实盘面板人工平仓；绝不假报干净。
- **R4 止盈止损语义**：按比例**归一**分摊（`/Σpct`，P1-2）≠ 聚合阈值，但各会话阈值之和**恒等于**用户所填
  总额。缓解：确认弹窗明确「总止盈/止损按各标的百分比归一分摊、各标的独立触发，和等于所填总额」；聚合阈值
  （和达标停全组）作为后续增强（需新增分组盯市任务，v1 不引入以保稳）。
- **R5 自选与自动交易成员一致性**：取消收藏 / 删自选须同步移出自动交易。缓解：在 store action 内强制
  不变量并单测；运行期锁定编辑避免「跑着改成员」。
- **R6 多 WS 连接开销**：浏览器侧 N≤8 条 WS；**后端侧每会话另开 `OKXWSClient`（public + business 各一），
  连接数同受 `live_auto_max_group_symbols` 封顶（P2-4）**。缓解：复用 `ReconnectingWS`；成员上限封顶；
  后续可选分组聚合 WS（本期不做）。
- **R7 多标的 K 线与图表叠加**：图表只画当前选中 instId 的成交，其余成员后台运行。缓解：
  `overlayTrades` 取 `members[instId]`，切换标的即换 markers；成员行各自呈现自身态。
- **R8 既有单会话测试回归**：cap=1 语义改为单活跃分组。缓解：保留兼容 wrapper 与旧端点契约；
  同批更新既有 live-auto 测试断言；新增分组测试覆盖回滚 / 门禁 / 独占。
- **R9 SPOT 标的不可自动交易**：自选可含现货。缓解：`isAutoTradable` 在拖入 / 加入门禁拦截并就地提示，
  服务端 `instrumentSupported` 二次兜底。
- **R10 配置取数失败（N 个 trading config）**：缓解：前置门禁并发取数 + 单标的失败计入整组明细；
  前端预算预览失败不阻塞启动（以服务端回执为准，沿用既有弹窗降级文案）。

---

## 实施过程发现的方案缺陷（Issues Found During Implementation · Subtask #2）

> 本节由代码开发节点（Subtask #2）在落地 v2 方案时追加，记录与原方案的偏差与原因，便于评审复核。

### D1 策略面板 4 组件「状态来源归一」按 v1 安全取舍**保留**为既有单标的态（未改 4 文件）

- **方案原文**：§2.7 / §三 前端清单要求把 `AutoTradeToggle` / `AutoTradeStatusBar` / `AutoTradeEventLog` /
  `AutoTradeTradesTable` 4 个策略面板组件的「状态来源归一到分组（单标的 = 1 成员分组）」，以避免「左右两套
  活跃态」。
- **发现**：这 4 个组件位于**真实资金安全关键**、已被测试覆盖且刚修复过的单标的路径（提交 `efa83f6`
  「实盘自动交易用勾选策略而非默认首个」即在此路径，对应评审条件 P2-5）。把它们改读分组瞬态 `liveAutoGroup`
  会重写其下单 / 状态读取逻辑，回归风险高，且与「保持 diff 聚焦、行为不变」直接冲突。
- **采取的做法（修正方案）**：**不修改**这 4 个文件，单标的快捷路径继续运行在其既有瞬态
  （`liveAutoSessionId` / `liveAutoStatus` / `liveAutoTrades`）上——后端已通过**保留的兼容 wrapper 端点**
  （`/live-auto/start|{sid}/stop|status`，内部走 1 成员、100% 分配分组）完整支撑该路径，外部契约不破。两条
  UI 表面（策略面板单标的 vs 自选页签分组）在**后端「单活跃分组独占」处天然互斥**（任一活跃即另一启动报
  `LIVE_AUTO_SESSION_LIMIT`），不会同时真正下单；自选页签 `WatchlistTab` 的 `GET /live-auto/group/status`
  重发现会拾取**任意**活跃分组（含单标的 1 成员组）并如实呈现，故无「静默丢失活跃态」。此做法**最大化满足
  §2.7『行为不变』并完全遵守 P2-5**（单标的 `AutoTradeToggle` 仍用面板所选策略）。完整双表面状态机统一可作
  为后续增强（需对单标的安全路径做等价回归测试后再行）。

### D2 分组成员 runtime 省略 per-member `events` 字段（活动流仍走既有全局通道）

- **方案原文**：§5.4 `liveAutoGroup.members[instId]` 形态含 `events: LiveAutoEvent[]`。
- **做法**：成员 runtime 仅保留驱动面板与图表 markers 所需的 `status` / `position` / `trades`（§2.7 面板呈现
  与 §四回执字段已足覆盖「持仓 / 会话盈亏 / 单独停止 / 当前标的成交叠加」）；关键 `error` / 降级 `status.message`
  仍经既有全局 `liveAutoEvents` 环形流（`pushLiveAutoEvent`）呈现，避免新增成员级事件 action 膨胀 store。
  功能等价、信息不丢，仅承载位置不同。

---

## 九、实施顺序建议（供 Subtask #2 参考）

1. 后端：`config.live_auto_max_group_symbols` → `schemas` 分组模型 → `manager.start_group/stop_group/
   stop_member/group_status/get` + 异常 → `api/live_auto.py` 分组端点 → 兼容 wrapper → 后端单测。
2. 前端纯件：`lib/autoTrade.ts` + 测试 → `liveAutoTypes` 扩展 → `store` 持久化/瞬态/migrate v20 + 测试。
3. 前端 UI：`WatchlistTab` / `AutoTradePanel` / `WatchOtherPanel` / `AutoTradeMemberRow` /
   `AutoTradeAllocationInput` / `AutoTradeGroupConfirmModal` → 接 `InstrumentSidebar` → 拖拽 → 组件测试。
4. 前端接线：`liveAutoGroupApi` + `useLiveAutoGroupStream` + `page.tsx` `overlayTrades`/重发现 →
   策略面板 4 组件状态来源归一。
5. 自测：`pnpm -C apps/web test`（`NODE_ENV=test`）+ `pytest services/quant`；可启动 / 可编译校验。

---

## 评审结论（Review Verdict · Subtask #1）

**有条件通过（Approved with conditions）。**

本方案对真实代码的关键断言经逐项核验**全部属实**，架构与既有 `paper` 多会话分组（`start_batch` /
`PaperGroup` / `_groups`，**已存在且带整组回滚**）高度同构，真实资金双闸 / 硬上限 / 单笔名义上限 /
前置门禁 / 两段式回滚等护栏均未削弱；`LiveExecutor` / `LiveAutoSession` 零改动、改造集中于 `manager.py` 与
分组级 REST 端点的策略可行且适度。评审发现的 **4 项 P1 已在本 v2 正文就地修复**——默认预算随成员数缩放
（P1-1）、TP/SL 按 `Σpct` 归一（P1-2）、前置门禁账户级数据各取一次（P1-3）、整组前置失败 `members`
明细透传（P1-4）——**无 P0、无遗留 P1**。

进入 Subtask #2 实施须满足以下条件（对应 P2，非阻断但须落实）：

1. **（P2-2）** `start_group` 拆为 `_preflight_group` / `_build_member_sessions` /
   `_launch_group_or_rollback`，并以 `GroupStartRequest` 聚合形参，满足 CLAUDE.md ≤50 行 / 圈复杂度 ≤10 /
   ≤5 形参；优先复用 `paper/manager.py:108` 既有 `start_batch` 回滚模板（`started_session_ids` → except 逐个
   `stop` → 透传原异常）。
2. **（P2-1）** 同步更新 `config.live_auto_max_sessions` 的注释 / 语义（标注被「单活跃分组 +
   `live_auto_max_group_symbols`」取代），避免文档漂移。
3. **（P2-3）** 在实现与测试中固化 `stop_member` 后成员保留为 `state="stopped"`、v1 不支持单成员重启、
   「无运行中成员则清组」的语义。
4. **（P2-5）** 状态来源归一时**不得回退**提交 `efa83f6`：单标的 `AutoTradeToggle` 仍用策略面板所选策略，
   仅分组弹窗用自带策略下拉。
5. **真实账户手动验收门（沿用既有惯例）**：§7.3 的 opt-in 真账户联调须由人工在真实 Windows 环境以极小预算
   执行并确认（CI 仅跑离线桩测，凭证一律占位）；未完成人工验收前不得宣称真实下单链路「通过」。

满足上述条件即视为方案落地就绪，可移交 Subtask #2 编码实施。

> —— 方案评审节点（Subtask #1），2026-06-29
