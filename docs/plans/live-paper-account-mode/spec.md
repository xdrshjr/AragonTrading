# 实时模拟（模拟盘）账户模式与实时动态联动 — 开发方案 spec.md

> 版本：**v2**（Subtask #1 方案评审与修复；在 v1 设计基础上插入「评审记录」、就地修复全部 P0/P1、补「评审结论」）
> 作者：Solution Architect（Anthropic 工程团队角色）
> 日期：2026-06-16
> 功能 slug：`live-paper-account-mode`
> 适用迭代：多轮迭代任务「标准四阶段开发」
> 关联代码索引：`.claude-index/index.md`
> 关联既有方案：`docs/plans/aragon-trading-platform/spec.md`、`docs/plans/chart-workspace-layout/spec.md`、`docs/plans/aragon-iter2-enhancements/spec.md`

---

## 0. 评审记录（Review Notes）

> 评审人：Senior Reviewer（Anthropic 工程团队角色）｜评审日期：2026-06-16｜评审基准：v1 全文 + 真实代码核验。
> 维度：**可行性 / 完整性 / 一致性 / 规模适配**。已逐文件核对 v1 引用的实现（`paper/session.py`、`engine/simulator.py`、`api/paper.py`、`paper/manager.py`、`schemas.py`、`config.py`、`db/repositories.py`、`apps/web/{lib/store.ts,lib/chartLive.ts,lib/api.ts,lib/ws.ts,lib/types.ts,lib/format.ts,app/page.tsx,components/ui/index.tsx,components/Strategy/{StrategyPanel,StrategyDetail}.tsx,components/Chart/ChartWorkspace.tsx}`）。
>
> **总体判断**：方案根因分析准确（数据通路确实存在、`shouldApplyPublicCandle` 抑制成形根、`if (c.confirm)` 丢弃 paper 成形根均与代码一致），技术路线（支柱 A 顶部开关 + 支柱 B priming/逐笔/快照三段）可落地、规模适中。下列 P1 为「按 v1 字面实现会产生错误行为或未达用户核心诉求」的缺陷，**已在正文就地修复并标注**；P2 为澄清/优化项，部分已就地修订，其余列入「评审结论」放行条件。

### 已核验为正确的关键前提（无需改动）
- `SimBroker.equity_at(close)`、`mark(ts, close)`、`initial_capital` 均已存在（`simulator.py:105/165/80`）→ B2 的逐笔盯市无需新增 broker 方法。
- `settings.initial_capital == 10000.0`（`config.py:44`）→ A1 的 `DEFAULT_SIM_BALANCE=10000` 对齐属实。
- `repo.create_run(config: dict)` 落 `config_json`（`repositories.py:114`）→ 追加 `priming/primingBars` 键无需迁移。
- `ws_paper` 在 `accept` 后已自行下发 `status`（`paper.py:74`）→ B3 仅需在其后追加一条 `snapshot`，可行。
- `feed_candle` 未确认分支已 `push` candle（`session.py:147`）→ B2「最右根跳动」的后端推流已存在，前端只差不再丢弃成形根。
- `WsEvent = { type: string; data: any }` 为松类型（`ws.ts:8`）→ 新增 `snapshot/position` 事件**无需改 `ws.ts`**，v1 文件清单未列 `ws.ts` 属正确（已补说明，见 §5 脚注）。
- `Mode/Trade/EquityPoint/AccountMode` 相关改动均为**追加可选字段**，向后兼容。

### 问题清单与处置

| # | 维度 | 严重度 | 位置 | 问题 | 处置 |
|---|------|--------|------|------|------|
| P1-1 | 可行性/正确性 | **P1** | §4.3 B1 | v1 称「预热窗口**写入 `_candles` 后**再对该窗口逐根 `_process_confirmed` 回放」。但 `_on_confirmed` 用 `i = len(_candles)-1` + 同 ts 去重/追加，**假设窗口逐根增量增长**；若先整窗预填再回放，非末根的 `candle.ts` 与 `_candles[-1].ts` 不等会**重复追加并恒在 i=len-1 误处理**，priming 结果错误。 | **已修**：priming 分支**不再整窗预填**，改为从空窗逐根 `append+处理`（等价微回测）；循环结束后 `_candles` 自然持有整窗、`_last_confirmed_ts`=末根。详见 §4.3 B1（重写）。 |
| P1-2 | 完整性/规模适配 | **P1** | §4.3 B1 / §6.1 | 预热只取 `max(warmup+5,60)` 根 → 仅末约 5 根能产信号，低频策略（默认 1H）**常无 seeded 成交/持仓**，「成交记录/marker/持仓」仍空，未达用户「点击后即看到动态」的核心诉求。 | **已修**：新增 `primingBars`（默认 **300**，夹取 `[warmup+5, _WINDOW_MAX=600]`）作为预热回放回看根数，使近端真实历史有现实概率体现当前持仓。见 §4.3 B1、§6.1、§7.3。 |
| P1-3 | 正确性 | **P1** | §4.3 B2（前端） | 逐笔 transient `equity` 与同根确认 `equity` **共享成形根 ts**；v1 前端 `setLiveEquity(prev => [...prev, e])` 为纯追加 → `liveEquity` 出现**重复 ts**，违反 lightweight-charts「time 严格递增」不变量，净值图报错/错乱。 | **已修**：`liveEquity` 改为**按 ts upsert**（同 ts 覆盖、保持升序），新增本地 `mergeEquityByTs`（与 candle 的 `mergeUnique` 同构，置于 `page.tsx`）；确认点覆盖同 ts 的 transient 点。见 §4.3 B2、§8.2。 |
| P1-4 | 完整性/一致性（诚实硬规则） | **P1** | §7.3 | §7.3 原句「transient equity 与 priming **之外**的 seeded 成交仍照常落库」自相矛盾（seeded 即 priming 产出）；且 `_persist_final` 对**整段 broker 序列**算 metrics，会把预热微回测与实时段混算，存在「把回填业绩当实时业绩」之嫌，触碰「绝不造假/诚实标注」硬规则。 | **已修**：明确落库与指标口径——seeded 成交及其逐根 equity **照常落库**（真实因果成交，且使右面板 REST `/trades`·`/equity` 与左下 WS 视图一致，落实 R9）；`run.config` 记 `primingBars`、`status` 报 `warmedBars/seededTrades` 以**显式标注预热边界**；唯独 intrabar transient equity 不落库、不计指标。重写 §7.3。 |
| P2-1 | 一致性 | P2 | §6.2 | WS 路径表述不一：§6.2 标题写 `/api/ws/paper/{sid}`，§4.x/§B3 写 `/ws/paper/{sid}`。 | **已修**：§6.2 注明——后端路由 `/ws/paper/{sid}`（挂载于 `/api` 前缀下），前端完整 URL `/api/ws/paper/{sid}`（`ws.ts:95`），二者指同一端点。 |
| P2-2 | 一致性/准确性 | P2 | §4.2 A5 / §5#9 | StrategyDetail **当前并无本金输入控件**（本金纯由后端默认提供），故「把内部恒定的本金来源改为只读提示」措辞不准；且 `simBalance` 注入更适合在持有 store 的 `StrategyPanel` 完成，而非 `StrategyDetail`。 | **已修**：A5 改述为「在 StrategyPanel 注入 `initialCapital/priming`；StrategyDetail 仅**可选**新增一行只读本金提示」，并把 #9 标注为「低优先/可选」。 |
| P2-3 | 规模适配 | P2 | §4.3 B2 / §6.2 | `PositionPanel` 已由 `overlayTrades + markPrice`（`deriveOpenPosition`）派生持仓，且 `markPrice`(lastPrice) 在 paper 模式下经 market WS 的 trade/ticker 仍实时更新 → 专门的 `position` 事件**与既有派生重复**。 | **已修（澄清）**：保留 `position` 为**可选**便捷读数，明确「当前持仓」**权威视图仍是 `deriveOpenPosition`」，避免双数据源不一致。见 §4.3 B2、§6.2。 |
| P2-4 | 准确性 | P2 | §4.2 A4 | A4 代码片段把主题按钮与开关包进新 `div.flex.items-center.gap-2`，但现 header 中主题按钮是 `header` 的直接 flex 子节点（无该 wrapper）。 | **已修**：A4 注明片段为示意，落地时新增该 wrapper 包裹「开关 + 主题按钮」。 |
| P2-5 | 完整性 | P2 | §6.2 / §7.3 | `TradeRecord` 无 `seeded` 字段，而事件/快照需区分 seeded 与实时成交（R2 依赖）；v1 未说明 seeded 标记如何产生。 | **已修**：明确机制——priming 结束记 `_seeded_trade_count = len(broker.trades)`；`_trade_record_out`/`snapshot()` 对索引 `< _seeded_trade_count` 的成交标 `seeded:true`；`_flush_new_trades` 增 `seeded` 形参用于实时段。 |
| P2-6 | 正确性（既有，超范围） | P2 | `session.py`（既有） | `i = len(_candles)-1` 配合 `deque(maxlen=600)`：窗口填满后 `entry_bar` 与 `_cur_bar` 同被钉在 599 → `bars_in_position` 恒为 0，超长 live 会话（>~540 根）`max_hold_bars` 失效。**本需求未引入**，但 priming 持仓会进入该区间。 | **不在本期修复**（既有缺陷）；列入「评审结论」跟进项，建议后续以「绝对 bar 序号 + 入场绝对序号」替代窗口下标。 |
| P2-7 | 规模适配（可选优化） | P2 | §4.3 B2 / §9 R3 | intrabar transient equity 是本方案唯一的洪泛/重复 ts 风险源；而持仓浮动盈亏已可由 `PositionPanel` 经 markPrice 实时显示。 | **保留设计**（满足「净值连续」目标）并已修正其正确性（P1-3）；同时在 §9 R3 注明「如需进一步降风险，可裁掉 transient equity，仅保留逐笔浮盈读数」作为可选简化。 |

> **结论摘要**：全部 P0 = 0；P1（P1-1~P1-4）均已在正文就地修复；P2 多数已就地修订，剩余（P2-6 既有缺陷跟进、验证门槛）列为放行条件。详见文末「评审结论」。

---

## 1. 概述（Overview）

本需求面向「启动实时模拟」这一核心功能链路，解决两个并列诉求。其一是**交互可达性与账户语义**：当前要进入实时模拟，用户必须先在 K 线工具栏右侧把「模式」从「回测」切到「实时」，再到右侧策略面板点击「启动实时模拟」，入口隐蔽、且**无法设置/查看模拟本金**（后端恒用默认 `initial_capital=10000`）。需求要求在**界面最顶端、最右侧「调整颜色（主题切换）按钮的左边**新增一个「实盘 / 模拟盘」切换开关；切到「模拟盘」时，开关右侧显示当前模拟账户的**总金额**，且用户可**随时调整**该金额；随后实时模拟即以该金额为初始权益、对当前所选策略做**跟盘模拟撮合**。

其二是**实时动态可见性**：用户反馈「点击启动实时模拟后看不到任何动态」。经核验，后端 `paper/session.py` 与 WS `/ws/paper/{sid}` 已经在每根**已确认** K 线上驱动 `SimBroker` 并推送 `equity / trade / candle / signal` 事件，前端 `app/page.tsx` 也已订阅、并把 `liveTrades / liveEquity` 经 `overlayTrades / overlayEquity` 接到了 K 线 marker、左下「成交记录」表与「当前持仓」卡。**数据通路本身存在**，但在 `source="live"` 且默认 `bar="1H"` 时，第一根「已确认」K 线最长要等约 1 小时才到达，期间撮合不动；且前端在 paper 模式下**丢弃了 paper 流的未确认（成形）K 线**（`if (c.confirm)` 才 `setLive`），同时 `shouldApplyPublicCandle` 在有 paper 会话时**抑制了公共行情的成形根**——于是最右 K 线、净值、持仓、成交全部「冻住」，观感等同「没反应」。

因此本方案分为两根支柱：**支柱 A（账户模式与可调本金）** 与 **支柱 B（实时动态联动修复）**。两者都严格遵守项目「**绝不造假数据**」硬规则：所有展示数字均来自真实 OKX 行情与真实撮合产出；「实盘」因 Phase 1 明确「**永不下真实单**」，在 UI 上**诚实置灰**并标注 Phase 2，而非伪装下单。本方案不改数据库 schema、不回滚既有功能，改动面集中在前端 `apps/web` 与后端 `services/quant/app/paper`、`schemas.py`、`api/paper.py`，把回归风险压到最小。

---

## 2. 设计目标与非目标

**目标**
1. 顶部右侧（主题切换按钮左侧）新增「实盘 / 模拟盘」分段开关；模拟盘态在其右侧显示**可随时编辑**的模拟资金总额。
2. 模拟资金作为新建实时模拟会话的初始权益（`initialCapital`）传入后端。
3. 「启动实时模拟」后**立即可见动态**：左下「成交记录」、K 线买/卖/平仓 marker、「当前持仓」、净值曲线在 2 秒内反映当前策略在近端真实历史上的持仓/成交，并随实时行情逐笔跳动。
4. 新的已确认 K 线触发信号时，成交、marker、持仓、净值实时增量更新。
5. 按钮与样式美观优雅、符合人机交互最佳实践（复用设计 token、键盘可达、`aria` 完整、暗/亮主题自适应）。

**非目标**
- 不接入任何真实下单 / 真实资金通道（「实盘」为 Phase 2 占位，本期禁用）。
- 不改 K 线主图/副图指标体系、不改回测/对比链路、不改数据库表结构。
- 不引入新的第三方依赖。

---

## 3. 背景与根因分析（基于真实代码核验）

| 现象 | 根因（文件:行为） | 处置支柱 |
|------|------------------|----------|
| 无「实盘/模拟」开关，本金不可调 | `app/page.tsx` header 仅有主题切换；`store.ts` 无账户态；`paper.py` 的 `initialCapital` 仅来自请求体（前端从不传），回落 `settings.initial_capital=10000` | A |
| 启动后长时间「冻住」 | `paper/session.py#feed_candle` 仅在 `confirm=1` 时撮合；默认 1H 下首个确认根最长约 1 小时才到 | B1 |
| 最右 K 线不跳动 | `page.tsx` paper 分支 `if (c.confirm) setLive(...)` 丢弃 paper 成形根；`chartLive.ts#shouldApplyPublicCandle` 在有 paper 会话时返回 false，公共成形根也被抑制 | B2 |
| 净值/浮动盈亏不连续 | 盯市 `broker.mark` 仅在确认根调用；未确认 tick 不更新权益 | B2 |
| 订阅晚于会话、刷新/重连后丢历史事件 | WS fan-out 为「订阅后才推」，无快照重放；预热产出的成交/净值在订阅前推送会丢失 | B3 |

---

## 4. 技术设计

### 4.1 总体架构与数据流（改动后）

```
Header(AccountModeSwitch) ──setAccountMode/setSimBalance──▶ useAppStore(accountMode, simBalance)
        │ 选「模拟盘」→ setMode("paper")
        ▼
StrategyPanel.startPaper({ ..., initialCapital: simBalance, priming:true })
        │  POST /api/py/paper/start
        ▼
PaperManager.start ──▶ PaperSession(initial_capital=simBalance, priming=true)
        │  ① 预热真实历史窗口
        │  ② priming：把预热窗口顺序喂入 SimBroker 四阶段 → 建立"当前持仓/成交/净值"(seeded)
        │  ③ 订阅 OKX candle{bar}（含未确认 tick）
        ▼
WS /ws/paper/{sid}: 订阅即发 status → snapshot(当前 trades/equity/position) → 之后 equity/trade/candle/signal/position(增量)
        ▼
page.tsx: snapshot→重置并填充 liveTrades/liveEquity；未确认 candle→setForming；确认 candle→setLive+清 forming
        ▼
overlayTrades→CandleChart markers + 左下「成交记录」TradesTable + 「当前持仓」PositionPanel；overlayEquity→EquityChart
```

### 4.2 支柱 A — 顶部账户模式开关 + 可调模拟资金

**A1. 全局状态（`lib/store.ts`）**
- 新增字段：`accountMode: "sim" | "live"`（默认 `"sim"`）、`simBalance: number`（默认常量 `DEFAULT_SIM_BALANCE = 10000`，与后端 `settings.initial_capital` 对齐）。
- 新增 setter：
  - `setAccountMode(m)`：仅写 `accountMode`（保持单字段原子，模式联动由组件编排）。
  - `setSimBalance(n)`：写 `simBalance`，并夹取到 `[MIN_SIM_BALANCE=100, MAX_SIM_BALANCE=1e8]`；非有限数忽略。
- `partialize` 增加持久化 `accountMode`、`simBalance`；`version` 升 `4 → 5`；`migrate`：`version < 5` 时补 `{ accountMode:"sim", simBalance:DEFAULT_SIM_BALANCE }`。运行期句柄（`paperSessionId`/`runId`）仍**不持久化**（R6 沿用）。

**A2. 顶部开关组件（新增 `components/Header/AccountModeSwitch.tsx`）**
- 分段控件，两段：「模拟盘」「实盘」，复用既有分段样式（`bg-[var(--surface-2)] p-0.5` 圆角胶囊，选中态 `bg-[var(--surface)] shadow-soft`，与 `ChartToolbar` 的 MODES 一致）。
- 「实盘」段：`disabled`、`opacity-45 cursor-not-allowed`，`title="实盘下单为 Phase 2 能力；当前版本仅提供模拟盘，绝不接入真实下单"`，右上角附 `Badge tone="neutral"` 文案「敬请期待」。点击不产生副作用（诚实，不伪装）。
- 「模拟盘」段：点击 → `setAccountMode("sim")` 且 `setMode("paper")`（使右侧策略面板进入实时模拟态、左下数据区绑定 paper 实时成交）。
- 当 `accountMode==="sim"` 时，开关右侧渲染 `<SimBalanceField/>`。
- `role`/`aria-pressed` 完整；键盘 Tab 可达、Enter/Space 触发。

**A3. 可编辑金额（新增 `components/Header/SimBalanceField.tsx`）**
- 显示态：`模拟资金 ¥10,000`（`fmtMoney`，千分位）。点击或聚焦切到输入态。
- 编辑态：`<input inputMode="decimal">`，失焦或 Enter 提交：经 `parseMoneyInput` 解析 → `setSimBalance`；Esc 取消恢复原值；越界/非法 → 回退原值并轻微抖动提示。
- 当存在活跃会话（`paperSessionId != null`）：**仍允许编辑**（满足「随时调整」），但显示浅色 hint「将于下次启动生效」；不热改已初始化的 broker（其本金在会话创建时已锁定）。
- 美观要求：等宽数字（`tabular`）、与主题 token 一致、悬停高亮边框 `hover:border-[var(--accent)]`、聚焦 `focus:border-[var(--accent)]`。

**A4. 接入位置（`app/page.tsx` header）**

现 header 中主题按钮是 `<header>` 的直接 flex 子节点（`justify-between` 的右侧项，无 wrapper）。落地时**新增一个 wrapper** 把「开关 + 主题按钮」包在一起，使开关位于主题按钮左侧（下列片段为**示意**，P2-4）：
```
<div className="flex items-center gap-2">
  <AccountModeSwitch />
  <Button variant="ghost" size="sm" onClick={toggleTheme} aria-label="切换主题">…</Button>
</div>
```
即把 `AccountModeSwitch` 置于主题切换按钮**左侧**，满足「调整颜色按钮的左边」。

**A5. 本金传入会话（`components/Strategy/StrategyPanel.tsx`）**〔v2 修订 P2-2〕
- **由 `StrategyPanel` 注入**（它持有 store）：从 store 读取 `simBalance`，在 `applyThenStartPaper(...)` → `api.paperStart` 时带入 `initialCapital: simBalance, priming: true`（`StrategyDetail.onStartPaper` 的 `opts` 仍只传 source/speed/replayBars，本金不下沉到 detail 组件）。
- `StrategyDetail.tsx`（**可选、低优先**）：当前 detail **并无本金输入控件**（本金纯由后端默认提供），故此项不是「改造已有输入」，而是**可选**新增一行只读提示「本金 ¥{simBalance}（在右上角「模拟资金」处调整）」以消歧；不新增第二个本金输入。其余 source/speed 控件保留。该提示若需 `simBalance`，由 `StrategyPanel` 以 prop 传入。

### 4.3 支柱 B — 实时动态联动（真实数据，不造假）

**B1. 启动即建仓：预热回放（warm-start priming，`paper/session.py`）**〔v2 重写：修复 P1-1 索引、P1-2 回看窗口〕
- 新增构造参数 `priming: bool = True`、`priming_bars: int = 300`（均经 `manager.start`、`api/paper.py`、`schemas.PaperStartReq` 透传；`primingBars` 夹取到 `[warmup+5, _WINDOW_MAX=600]`）。
- **抽出核心** `_process_confirmed(candle, *, seeded: bool, emit_candle: bool)`：把现有 `_on_confirmed` 主体迁入（去重/追加 `_candles`、`prepare`、四阶段撮合、flush 成交、记/推 equity、按 `emit_candle` 决定是否 push candle、按 `warmup` 门生成信号）。`_on_confirmed` 退化为 `await self._process_confirmed(candle, seeded=False, emit_candle=True)`。
- **`start()` 的 live 分支按 `priming` 二选一构建窗口（关键修复 P1-1）**：
  - `priming=True`：**不再整窗预填 `_candles`**（避免 `i=len-1` 与同 ts 去重在整窗预填下重复追加、恒在末根误处理）。改为：拉取 `limit=primingBars` 的真实历史后，**从空窗逐根** `await self._process_confirmed(Candle(...,confirm=True), seeded=True, emit_candle=False)`——每次仅追加一根、`prepare` 在增量增长的窗口上、`i=len-1` 恰为当根，等价于对近端真实历史做一次因果微回测，在「当前时刻」建立**真实**的持仓/成交/净值。循环结束后 `_candles` 自然持有整窗、`_last_confirmed_ts`=末根 ts、`_bar_index/_trade_cursor` 连续。
  - `priming=False`：沿用 v1 既有行为（整窗预填 `_candles` 并置 `_last_confirmed_ts`，不回放）。
- priming 不 `sleep`、不逐根 push candle（REST 历史已在前端绘出，且订阅尚未建立）。**seeded 标记机制（修复 P2-5）**：priming 循环结束记 `self._seeded_trade_count = len(self.broker.trades)`；`_flush_new_trades(seeded=...)` 与 `_trade_record_out(t, seeded=...)`/`snapshot()` 据此把索引 `< _seeded_trade_count` 的成交标 `seeded:true`。
- **无缝衔接**：游标连续 + 既有同 ts 去重（`_on_confirmed` 顶部），使首个 live 确认根若与预热末根同 ts 则被去重、否则正常处理，不重复处理末根（R6）。
- **注意**：因 SimBroker 信号在「次根开盘」成交，预热末根若刚触发开仓信号，其成交将落在**首个 live 根**——这是因果正确行为；故测试对「当前应持仓」用「若」描述（见 §8.1）。

**B2. 逐笔动态：未确认 tick 盯市 + 前端成形根联动**
- 后端 `feed_candle` 未确认分支：除现有「推未确认 candle」外，加 `eq = self.broker.equity_at(candle.c)`（`SimBroker.equity_at` 为纯计算、不落点），推一条**瞬时** `equity` 事件（标 `transient:true`，前端只用于绘动、**不持久化**）与一条 `position` 事件（`{side,size,avgEntry,markPrice,unrealizedPnl}`）。节流：距上次瞬时推送 ≥ 1s 或价格变动 ≥ 1bp 才推，避免洪泛。
- 已确认分支沿用：`mark` 落点 + 持久化 + 推 `equity`（非 transient）+ `candle(confirm:true)` + 必要时 `signal`。
- 前端 `page.tsx` paper 处理：
  - `candle` 且 `!confirm` → `setForming(c)`（修复：先前被 `if (c.confirm)` 丢弃）；`confirm` → `setLive(mergeUnique(...,[c]))` 且 `setForming(null)`。（成形根经既有 `mergeCandles(...,forming)` 并入最右端，paper 模式 `chartView==="live-follow"`、`chartRange===null`，故 `candles` 含 forming → 图表最右根跳动。）
  - `equity`：transient 与非 transient 都进 `liveEquity`，但**必须按 ts upsert 而非追加（修复 P1-3）**——transient 与同根确认 equity 共享成形根 ts，纯追加会产生重复 ts、违反 lightweight-charts「time 严格递增」。新增本地工具 `mergeEquityByTs(prev, point)`（与 candle `mergeUnique` 同构：`Map<ts,EquityPoint>` 覆盖、按 ts 升序），`setLiveEquity(prev => mergeEquityByTs(prev, e.data))`；确认点（非 transient）覆盖同 ts 的 transient 点，结算后的权威值留存。
  - `position`（**可选便捷读数**，P2-3）：可存入一个轻量 state 仅供数字展示；**「当前持仓」的权威视图仍是** `overlayTrades + markPrice` 的纯函数推导（`deriveOpenPosition`，`markPrice=lastPrice` 在 paper 模式下经 market WS 的 trade/ticker 实时更新，故浮动盈亏本就逐笔跳动）。若保留 `position` 事件，须与派生结果一致，避免双数据源分叉。

**B3. 订阅即重放快照（`paper/session.py` + `api/paper.py`）**
- 新增 `PaperSession.snapshot() -> dict`：返回 `{ trades:[...全部], equity:[...末 N 条], position:{...}, status:{state,warmedBars,seededTrades,initialCapital} }`。
- `ws_paper` 在 `accept` 后、进入推送循环前，于 `status` 之后**先发一条 `snapshot` 事件**。这样「订阅晚于 priming」或「断线重连」都能恢复完整 seeded 状态（解决 fan-out「订阅后才推」会丢历史事件的问题）。
- 前端收到 `snapshot`：以其 `trades`/`equity` **整体替换** `liveTrades`/`liveEquity`（而非追加），随后增量事件继续追加。

**B4. 诚实空态与「历史预热」提示（`components/Chart/ChartWorkspace.tsx`）**
- paper 运行中但暂无新信号：左下面板顶部显示一行非阻断提示「已启动实时模拟 · 正在等待新的交易信号；下方为基于近端真实历史的预热持仓 / 成交」。
- `TradesTable` 中 `seeded:true` 的成交以浅色「历史预热」分隔/角标区分于实时新成交，避免把回填误读为「刚发生的实时成交」。

### 4.4 关键时序（Sequence）

```
用户：右上角点「模拟盘」→ setAccountMode("sim") + setMode("paper")
用户：在「模拟资金」改为 50,000 → setSimBalance(50000)
用户：右侧选策略 → 点「启动实时模拟」
FE → POST /paper/start { strategyId, instId, bar, initialCapital:50000, source:"live", priming:true }
BE：PaperManager.start → 建 run(config含 initialCapital/source/priming) → PaperSession
BE：start(): 预热历史 → priming 回放建仓(seeded trades/equity/position)
FE → WS /ws/paper/{sid}：收 status → snapshot(seeded 全量) → 立即渲染 成交/marker/持仓/净值
BE：OKX 未确认 tick → 推 candle(confirm:false)+equity(transient)+position → FE 最右根跳动、净值/浮盈连续变化
BE：新确认根 + 触发信号 → 推 trade/candle(confirm:true)/equity/signal → FE 增量更新四处
用户：点「停止模拟」→ POST /paper/{sid}/stop → finalize 结算 + metrics 落库 → status:stopped
```

---

## 5. 文件 / 模块变更计划

| # | 文件 | 新建/修改 | 一句话意图 |
|---|------|-----------|------------|
| 1 | `apps/web/lib/store.ts` | 修改 | 新增 `accountMode`/`simBalance` 及 setter、持久化、`v4→v5` migrate |
| 2 | `apps/web/lib/types.ts` | 修改 | 新增 `AccountMode`；`Trade.seeded?`；`PaperPositionEvent`/`PaperSnapshot`/`PaperStatus` 形状 |
| 3 | `apps/web/lib/format.ts` | 修改 | 新增 `fmtMoney(n)`、`parseMoneyInput(raw)`（货币显示/解析） |
| 4 | `apps/web/lib/format.test.ts` | 修改 | 补 `fmtMoney`/`parseMoneyInput` 正常 + 边界用例 |
| 5 | `apps/web/components/Header/AccountModeSwitch.tsx` | 新建 | 「模拟盘/实盘」分段开关；实盘诚实置灰 Phase 2 |
| 6 | `apps/web/components/Header/SimBalanceField.tsx` | 新建 | 模拟资金内联可编辑字段（解析/夹取/运行中提示） |
| 7 | `apps/web/app/page.tsx` | 修改 | header 左插开关（含 wrapper）；paper 处理：snapshot 整体替换、未确认成形根 setForming、equity **按 ts upsert**（新增本地 `mergeEquityByTs`）、可选 position |
| 8 | `apps/web/components/Strategy/StrategyPanel.tsx` | 修改 | startPaper 带入 `initialCapital:simBalance, priming:true` |
| 9 | `apps/web/components/Strategy/StrategyDetail.tsx` | 修改 | 本金改为只读提示「在右上角调整」；保留 source/speed |
| 10 | `apps/web/components/Chart/ChartWorkspace.tsx` | 修改 | 运行中诚实空态横幅；seeded 成交「历史预热」标识 |
| 11 | `apps/web/lib/api.ts` | 修改 | `paperStart` 入参补 `priming?: boolean` |
| 12 | `apps/web/lib/store.test.ts`（如无则新建） | 新建/修改 | 断言 v5 migrate 默认值、`setSimBalance` 夹取 |
| 13 | `services/quant/app/schemas.py` | 修改 | `PaperStartReq.priming: bool = True`；`primingBars: int = Field(default=300, ge=10, le=600)` |
| 14 | `services/quant/app/api/paper.py` | 修改 | `paper_start` 透传 `priming/primingBars`；`ws_paper` 在 `status` 后**追加 `snapshot` 事件**（取自 `session.snapshot()`） |
| 15 | `services/quant/app/paper/manager.py` | 修改 | `start(...)` 增 `priming/priming_bars` 形参并下传；`create_run` 的 `config` 追加 `priming/primingBars` |
| 16 | `services/quant/app/paper/session.py` | 修改 | 抽出 `_process_confirmed(*, seeded, emit_candle)`；priming **增量回放**（修复 P1-1）+ `primingBars`；intrabar transient `equity`(节流) + 可选 `position`；`_seeded_trade_count` 标记；`snapshot()` |
| 17 | `services/quant/tests/test_paper_priming.py` | 新建 | priming 增量建仓 / intrabar 盯市 / snapshot / 去重衔接 / 本金传递 单测（真实历史/桩注入驱动） |

> 规模红线（CLAUDE.md §二）：每个新建组件单文件 < 800 行、函数 < 50 行；`session.py` 抽出 `_process_confirmed` 后若逼近 800 行，将 priming/snapshot 工具下沉到 `paper/_seed.py` 辅助模块。
> **`lib/ws.ts` 无需改动**（核验 P0 前提）：`WsEvent = { type: string; data: any }` 为松类型，新增 `snapshot/position` 事件在 `page.tsx` 内按 `e.type` 分支 + `as` 断言到 §7.1 新类型即可，无需扩展 `ws.ts`；故本表未列 `ws.ts` 是正确的。
> **`lib/api.ts`（#11）**：`paperStart` 入参补 `priming?: boolean; primingBars?: number`。

---

## 6. 接口设计

### 6.1 REST
- `POST /api/py/paper/start`
  - 请求体（在既有基础上加 `priming`）：
    ```jsonc
    {
      "strategyId": 12,
      "instId": "BTC-USDT-SWAP",
      "bar": "1H",
      "initialCapital": 50000,     // ← 前端传入 simBalance（缺省回落 settings.initial_capital）
      "source": "live",            // live | replay（既有）
      "speed": 1.0,                // 既有（replay 用）
      "replayBars": 300,           // 既有（replay 用）
      "priming": true,             // 新增：live 启动时是否预热回放建仓，默认 true
      "primingBars": 300           // 新增（修复 P1-2）：预热回放回看根数，默认 300，夹取 [warmup+5, 600]
    }
    ```
  - 响应：`{ "sessionId": "...", "runId": 34 }`（不变）。
- `POST /api/py/paper/{sid}/stop`、`GET /api/py/paper/sessions`：签名不变。

### 6.2 WebSocket（事件协议）

> **路径（修复 P2-1）**：后端路由 `@router.websocket("/ws/paper/{session_id}")`（router 挂载于 `/api` 前缀下）；前端 `paperWS` 连接完整 URL `${NEXT_PUBLIC_PY_WS_URL}/api/ws/paper/{sid}`（`ws.ts:95`）。二者指同一端点，下文 `/ws/paper/{sid}` 与 `/api/ws/paper/{sid}` 等价。
> **下发顺序**：`ws_paper` 在 `accept` 后已自行下发 `status`（`paper.py:74`）；本方案在其**紧随其后**追加一条 `snapshot`，再进入既有 `queue` 推送循环（保证 snapshot 先于任何增量到达，落实 R7）。
> **seeded 标记来源（P2-5）**：`TradeRecord` 无 `seeded` 字段；由会话侧 `_seeded_trade_count`（priming 结束时 `=len(broker.trades)`）判定——索引 `< _seeded_trade_count` 的成交在 `trade` 事件与 `snapshot.trades` 中标 `seeded:true`。

| event.type | data 形状 | 说明 |
|------------|-----------|------|
| `status` | `{state, warmedBars, seededTrades, initialCapital}` | 订阅即发；`warmedBars`/`seededTrades` 反映 priming 结果 |
| `snapshot` | `{trades:[Trade & {seeded?}], equity:[EquityPoint], position:PaperPositionEvent\|null}` | **新增**，`status` 后立即发；前端**整体替换** `liveTrades/liveEquity`（修复重连/晚订阅丢历史）；`equity` 取末 N 条 |
| `trade` | `Trade & { seeded?: boolean }` | `seeded:true` 为预热回填（索引 `< _seeded_trade_count`） |
| `candle` | `{ts,o,h,l,c,vol,confirm}` | 未确认根用于成形根跳动（后端 `feed_candle` 已推） |
| `equity` | `EquityPoint & { transient?: boolean }` | `transient:true` 为逐笔瞬时盯市（不落库、不计指标；前端按 ts upsert 绘动，见 P1-3） |
| `position` | `{side,size,avgEntry,markPrice,unrealizedPnl}` | **新增、可选**（P2-3）；权威持仓仍由前端 `deriveOpenPosition` 推导 |
| `signal` | `{ts,side,reason}` | 既有 |

### 6.3 前端 Store API
- `setAccountMode(m: "sim" | "live"): void`
- `setSimBalance(n: number): void`（夹取 `[100, 1e8]`）

---

## 7. 数据模型

### 7.1 前端内存形状（`lib/types.ts`）
```ts
export type AccountMode = "sim" | "live";
export interface PaperPositionEvent {
  side: "long" | "short" | "flat";
  size: number; avgEntry: number; markPrice: number;
  unrealizedPnl: number;
}
export interface PaperSnapshot {
  trades: Trade[]; equity: EquityPoint[]; position: PaperPositionEvent | null;
}
// Trade 增加可选：seeded?: boolean（历史预热回填标记）
// EquityPoint 增加可选：transient?: boolean（瞬时盯市，不入库/不计指标）
```

### 7.2 持久化（localStorage `aragon-ui` v5）
新增持久化键：`accountMode`、`simBalance`。其余 partialize 字段不变。运行期句柄不持久化。

### 7.3 后端持久化与指标口径（无 schema 变更）〔v2 重写：修复 P1-4 自相矛盾与诚实口径〕
- `StrategyRun.config_json`（既有 JSON 文本字段，`repositories.py:114`）记录 `{initialCapital, source, speed, replayBars, priming, primingBars, seededTrades}`；均为**追加键**，**无需数据库迁移**。`seededTrades` 在 priming 结束后写回（便于离线区分预热段）。
- **落库口径（明确而非含糊）**：
  1. **seeded（预热回放）成交及其逐根 equity 点**——**照常落库**。理由：它们是对近端真实历史的**真实因果撮合**结果（非合成），且落库后右面板 REST `/runs/{id}/trades`·`/equity` 与左下 WS 视图**同源一致**（落实 R9，否则右面板会缺失开仓成交、与左下分叉）。
  2. **intrabar 瞬时 `equity`（transient）**——**不落库**。避免与确认根同 ts 的净值点重复/膨胀（符合 `finalize`「同 ts 不重复」约束），前端仅用其绘动（按 ts upsert）。
- **指标与诚实标注**：`_persist_final` 对整段 `broker.equity_points/trades` 计 metrics，**包含预热段**——该 run 被定义为「**热启动的实时模拟会话**」，而非「纯实时」。诚实性由以下保证（满足「绝不造假/诚实标注」硬规则）：① WS `status.warmedBars/seededTrades` + `run.config.seededTrades` **显式标注预热边界**；② 前端 `TradesTable` 对 `seeded:true` 成交以「历史预热」角标/分隔区分（§4.3 B4）；③ 不在任何文案中把预热回填表述为「刚发生的实时成交/实时业绩」。**不**把预热回测包装成实时业绩，亦**不**隐藏其存在。

---

## 8. 测试与验收标准

### 8.1 后端单测（`services/quant/tests/test_paper_priming.py`，真实历史/桩注入）
- **priming 建仓**：构造一段会触发的近端历史，`PaperSession(priming=True).start()` 后，`broker.trades` 含 ≥1 笔 `seeded` 成交，且若策略当前应持仓则 `broker.pos.side != "flat"`；`equity_points` ≥1。
- **增量回放正确性（覆盖 P1-1）**：priming 后 `len(_candles) == primingBars`（或所取历史根数）、`_last_confirmed_ts == 末根 ts`、`_bar_index == primingBars-1`；断言**无重复 ts**、`equity_points` 根数与处理根数一致（验证未发生「整窗预填后重复追加」的退化）。
- **窗口尺寸（覆盖 P1-2）**：`primingBars=300` 时实际拉取/回放约 300 根；`primingBars` 被夹取到 `[warmup+5, 600]`（下越界取 `warmup+5`，上越界取 600）。
- **无信号也建净值**：即便无成交，priming 后 `snapshot().equity` ≥1 个点。
- **intrabar 盯市**：注入一根 `confirm=False` candle，断言触发了一次（且节流后仅一次）`equity(transient=True)` 推送，且 `equity_points`（落库序列）长度不变。
- **snapshot 完整**：`snapshot()` 返回 `trades/equity/position` 与 broker 状态一致。
- **去重衔接**：priming 末根 ts 与首个 live 确认根同 ts 时不重复处理（既有去重断言扩展）。
- **本金传递**：`initialCapital=50000` 时 `broker.initial_capital==50000` 且 run.config 记录之。
- **幂等结算**：`stop()` 后再 `stop()` 不重复结算（沿用既有 C3 断言）。

### 8.2 前端单测（vitest）
- `format.test.ts`：`fmtMoney(10000)=="¥10,000"`、`fmtMoney(1234567.5)` 千分位；`parseMoneyInput("¥1,234.5")==1234.5`、`parseMoneyInput("abc")==null`。
- `store.test.ts`：`v4→v5` migrate 补 `accountMode:"sim"`/`simBalance:10000`；`setSimBalance(-5)` 与 `setSimBalance(1e12)` 被夹取到 `[100,1e8]`。
- **净值 upsert（覆盖 P1-3）**：`mergeEquityByTs` 对同 ts 的 transient→confirmed 序列做覆盖后，结果**ts 唯一且严格升序**、同 ts 保留最后写入值（确认值覆盖瞬时值）。若 `mergeEquityByTs` 抽为 `page.tsx` 本地函数不便单测，则提取到 `lib/`（如 `liveSeries.ts`）并配 `*.test.ts`，遵循「纯函数 + 同名测试」约定。

### 8.3 端到端验收清单（人工 / smoke）
1. 顶部右侧、主题切换**左侧**出现「模拟盘 / 实盘」开关；「实盘」置灰且 hover 显示 Phase 2 文案。
2. 选「模拟盘」→ 右侧出现「模拟资金 ¥10,000」，可点击编辑、Enter/失焦提交、千分位显示、越界回退。
3. 改本金为 50,000 后点「启动实时模拟」→ 后端 `run.config.initialCapital==50000`。
4. 启动后 **< 2s**：若近端历史触发，左下「成交记录」出现成交、K 线出现买/卖/平仓 marker、「当前持仓」显示方向/均价/浮动盈亏、净值视图出现曲线。
5. 暂无新信号时，最右 K 线随真实 tick 跳动、净值与浮动盈亏随最新价连续变化。
6. 新确认根触发信号时，四处（成交/marker/持仓/净值）实时增量更新。
7. 点「停止模拟」→ 结算、metrics 落库、状态置 stopped。
8. 刷新页面或断线重连后，经 `snapshot` 恢复当前 seeded 成交/净值/持仓，不空屏。
9. `pnpm test` 全绿、`tsc` 无错、后端 `pytest`（无网络标记）全绿。

---

## 9. 风险与缓解

| # | 风险 | 影响 | 缓解 |
|---|------|------|------|
| R1 | 「实盘」被误期望为真实下单 | 触碰「永不下真实单」硬规则 | 实盘段诚实置灰 + Phase 2 文案，点击无副作用；方案与 UI 文案均不暗示真实成交 |
| R2 | priming 历史回填被误读为「刚发生的实时成交」 | 误导用户 | 事件标 `seeded`；`TradesTable` 以「历史预热」角标/分隔区分；`status.warmedBars` 文案说明 |
| R3 | 逐笔 transient equity 推送洪泛 / 净值点膨胀 | 性能与重复 ts | transient 不落库、不计指标；节流（≥1s 或 ≥1bp）；落库仅在确认根。**可选简化（P2-7）**：若要进一步降风险，可裁掉 transient equity，仅靠 `PositionPanel` 经 markPrice 显示逐笔浮盈、净值图按确认根步进 |
| R4 | 运行中编辑本金与已锁定 broker 冲突 | 语义混乱 | 编辑随时可改但「下次启动生效」，不热改已初始化 broker，并以 hint 明示 |
| R5 | 高周期(1H) live 首个确认根迟到 | 仍可能「等待」 | priming 提供即时建仓态 + intrabar 跳动 + 诚实「等待新信号」空态；replay 作为可选演示路径保留 |
| R6 | priming 末根与首个 live 确认根重复处理 | 重复成交/净值 | 复用既有同 ts 去重；游标连续；单测覆盖 |
| R7 | snapshot 与增量事件竞态（snapshot 前已到增量） | 状态错乱 | snapshot 紧跟 status 先发，前端对 snapshot 做整体替换、其后才追加增量 |
| R8 | persist v5 迁移遗漏默认值 | 旧用户读到 undefined | `migrate` 显式补默认；组件再做空值回落（双保险） |
| R9 | 两处成交视图（右面板轮询 REST vs 左下 WS）不一致 | 观感矛盾 | 二者同源同 runId；左下为实时权威视图，右面板「成交明细」继续作 REST 细览 |
| R10 | `session.py` 改动逼近 800 行红线 | 违反规模约定 | priming/snapshot 工具下沉 `paper/_seed.py` |
| R11 | transient 与确认根净值共享 ts，前端纯追加致重复 ts | 净值图报错/错乱（违反 time 严格递增） | 前端 `liveEquity` 按 ts upsert（`mergeEquityByTs`），确认点覆盖同 ts 瞬时点（修复 P1-3，§8.2 覆盖） |
| R12 | priming 回看过短致低频策略仍无 seeded 持仓/成交 | 未达「点击即见动态」核心诉求 | `primingBars` 默认 300、夹取 `[warmup+5,600]`；并叠加 B2 逐笔跳动作即时反馈兜底（修复 P1-2，§8.1 覆盖） |
| R13 | priming「整窗预填后回放」致 `i=len-1` 重复追加/误处理 | priming 结果错误 | 改为从空窗增量逐根回放，`_candles` 自然成形（修复 P1-1，§4.3 B1、§8.1 覆盖） |

---

## 10. 实施过程发现的方案缺陷（由 Subtask #2 填写）

> 开发节点若发现本方案与真实代码/运行时不符之处，在此记录并就地修正，保持 spec 与实现一致。
> 本期实现严格落地 §5 全部 17 项与放行条件 A/D；下列为对 spec 的**两处微澄清**（非缺陷，均在 spec 正文已留口子），记录以保持文档与实现一致。

- **D1（净值 upsert 工具落点，呼应 §8.2 / 放行条件 A2）**：`mergeEquityByTs` 未内联在 `page.tsx`，而是按 §8.2 给出的可选项**提取到 `apps/web/lib/liveSeries.ts`** 并配同名 `liveSeries.test.ts`，遵循「纯函数 + 同名测试」约定（§8.2 覆盖 P1-3 的单测要求）。`page.tsx` 从 `@/lib/liveSeries` 引入使用。文件清单 §5#7 的「新增本地 `mergeEquityByTs`」即指此函数，位置改在 `lib/` 更可测。
- **D2（seeded 成交标识落点，呼应 §4.3 B4 / R2）**：§5#10 把「seeded 成交『历史预热』标识」记在 `ChartWorkspace.tsx` 行，但渲染单条成交行的组件是其子组件 `components/Strategy/TradesTable.tsx`。故**横幅落在 `ChartWorkspace.tsx`、`seeded` 角标落在 `TradesTable.tsx`**（左下与右侧「成交明细」共用同一 `TradesTable`；REST 来源的成交无 `seeded` 字段，自然不显角标，互不影响）。
- **其余**：后端 `session.py` 抽出 `_process_confirmed(*, seeded, emit_candle)` 后全文 368 行，远低于 800 行红线，故 §5 脚注预案的 `paper/_seed.py` 下沉**未触发**；`lib/ws.ts` 如 §5 脚注所述**无需改动**。

---

## 11. 评审结论（Review Verdict）

**结论：有条件通过（Approved with Conditions）。**

方案根因分析与真实代码一致，技术路线（支柱 A 顶部账户开关 + 可调本金；支柱 B priming 热启动 / 逐笔盯市 / 订阅快照三段）可落地、规模适配本期目标，且严守「绝不造假、诚实标注」硬规则。本轮发现 **0 个 P0、4 个 P1（P1-1~P1-4）均已在正文就地修复并标注**，关键 P2 已就地修订。同意进入 Subtask #2 代码开发，但须满足以下放行条件：

**A. 必须遵守（开发期硬性条件）**
1. **priming 增量回放**（P1-1/R13）：严格按 §4.3 B1 v2 的「从空窗逐根 `_process_confirmed`」实现，**禁止**「整窗预填后再回放」；落地后须有 §8.1「增量回放正确性」测试（窗口根数、`_last_confirmed_ts`、无重复 ts）转绿。
2. **净值按 ts upsert**（P1-3/R11）：`liveEquity` 一律经 `mergeEquityByTs` 合并，**禁止**纯 `[...prev, e]` 追加；transient 与确认根同 ts 必须收敛为单点。
3. **落库与指标诚实口径**（P1-4）：seeded 成交/逐根 equity 照常落库，transient equity 不落库；`status.warmedBars/seededTrades` 与 `run.config.seededTrades` 必须如实写出，`TradesTable` 必须对 seeded 成交做「历史预热」可视区分；任何文案不得把预热回填表述为实时业绩。
4. **primingBars 兜底**（P1-2/R12）：`primingBars` 默认 300、夹取 `[warmup+5,600]`；即使预热无成交，B2 的成形根跳动 + markPrice 浮盈也须保证「点击后秒级可见动态」。
5. **诚实置灰实盘**（R1）：「实盘」段禁用、Phase 2 文案、点击无副作用；绝不接入真实下单。
6. 仅改动 §5 文件清单内文件；不改数据库 schema、不引入新第三方依赖；`lib/ws.ts` 无需改动。

**B. 建议遵循（P2，不阻断合并）**
- StrategyDetail 只读本金提示（#9）为可选，本金注入在 StrategyPanel 完成（P2-2）。
- `position` 事件保持可选，「当前持仓」权威视图为 `deriveOpenPosition`，避免双数据源分叉（P2-3）。
- header wrapper、WS 路径表述按 §4.2 A4 / §6.2 落地（P2-1/P2-4）。

**C. 跟进项（本期不修，记录在案）**
- **既有缺陷 P2-6**：`i=len(_candles)-1` 配合 `deque(maxlen=600)` 在超长 live 会话（>~540 根）使 `bars_in_position` 失真、`max_hold_bars` 失效。本需求未引入但 priming 持仓会进入该区间；建议后续 issue 以「绝对 bar 序号」替代窗口下标，**不在本期 spec 范围内**。

**D. 验收门槛（Definition of Done 对齐 §8.3）**
- `pnpm test`（vitest）、`tsc` 无错；后端 `pytest`（无网络标记）全绿，含新增 `test_paper_priming.py`。
- 端到端清单 §8.3 第 1–9 条人工/smoke 通过，重点验证：点击「启动实时模拟」后 < 2s 见动态、成形根跳动、刷新/重连经 snapshot 恢复不空屏。

> 满足 A、D 即视为达成本方案验收；B 为质量建议；C 为独立跟进。— Senior Reviewer, 2026-06-16
