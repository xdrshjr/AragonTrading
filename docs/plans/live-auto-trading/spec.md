# 实时自动交易（Live Auto-Trading）开发方案

- **Feature key**: `live-auto-trading`
- **版本**: **v2**（iteration 1 · Subtask #1 方案评审与修复节点）。演进链：v1 方案设计 → **本轮 v2（Subtask #1 评审）**：对照**当前工作树实代码**逐项核验 v1 的全部 `file:line` 引用与复用断言，发现并就地修复 **5 个 P1 + 1 个 P2 据实修正**，结论 **有条件通过**。完整评审记录见下「评审记录」、结论见文末「评审结论」。
- **作者**: 方案设计节点（solution architect）
- **关联红线**: 本方案是项目 **Phase 2「真实下单」** 之上的**自动化**扩展——把已落地的「人工逐单实盘下单」（`docs/plans/live-trading-mode/spec.md` v4，已实现并通过测试）升级为「**策略驱动、实时、自动**买卖」。因为它会**无人值守地用真实资金下单**，故 *安全护栏（**自动化总闸默认关闭** `LIVE_AUTO_TRADING_ENABLED=false`、预算上限、名义上限、会话级止盈止损、无既有持仓门禁、单会话独占）* 与 *诚实数据（持仓/成交真源在 OKX）* 是一等需求，而非可选项。（⚠️ 评审 R1：底层写链路总闸 `LIVE_TRADING_ENABLED` 现已**默认 `True`**（`config.py:47`，「本轮需求把实盘下单默认翻为开启」），故**真正的默认关闭闸是新增的 `LIVE_AUTO_TRADING_ENABLED`**，而非「双重」默认关闭，详见评审记录与 §2.1。）
- **复用基线**：本功能**最大化复用**两条已存在且经测试的链路——
  - 实时策略步进：`paper/` 包（`PaperSession` 逐根驱动 + 暖机 priming + WS 快照/事件扇出），见 `services/quant/app/paper/session.py`、`manager.py`。
  - 实盘写链路：`okx/trading.py` 服务层（`place_live_order` / `close_live_position` / `set_account_leverage`，含 `LIVE_TRADING_ENABLED` 守门、名义上限、净仓反向单拦截、唯一 clOrdId、`live_order_audit` 留痕、`_redact` 脱敏），见 `services/quant/app/okx/trading.py`、`order_builder.py`、`trade_client.py`、`api/trading.py`。

  **核心设计一句话**：把 `PaperSession` 逐根驱动循环里的 `broker.submit(signals)`（`SimBroker`，`session.py:332` 附近）替换为一个 **`LiveExecutor`**，由它把同一份策略信号翻译为对 `okx/trading.py` 服务层的**真实下单/平仓调用**；持仓/成交/权益真源切换为**真实 OKX 账户**，事件协议沿用 paper 的 WS 快照流，前端因此可零成本复用图表 markers、成交/持仓表。

---

## 评审记录（Review Notes · Subtask #1）

> 评审人：资深评审（senior reviewer）。维度：**可行性 / 完备性 / 一致性 / 规模适配**。
> 评审基线：对照**当前工作树实代码**逐项回查 v1 全部 `file:line` 引用与「最大化复用」断言（后端 `paper/` 实时流、`engine/simulator.py` SimBroker 语义、`okx/trading.py` 写链路、`okx/order_builder.py`、`db/repositories.py`、`config.py`；前端 `ui` 原语、`StrategyPanel.tsx`、`lib/store.ts`、`app/page.tsx`、`lib/ws.ts`、`lib/tradingApi.ts`、`components/Live/*`、`ChartWorkspace.tsx`）。
> 所有 **P0 / P1 已在本 v2 正文就地修复**（每条标注落点小节）；P2 作为实施提示保留，其中据实硬错误（R6 记法）一并就地修正。

### 核验证据（v1 复用断言 ↔ 工作树实代码，命中项）

> 以下断言经核验**成立**，构成本方案可行性的直接背书：

| v1 断言 | 工作树核验 | 结论 |
|---|---|---|
| `PaperSession` 逐根驱动 + `broker.submit(signals)` + `_prime_history` 暖机 + WS 队列扇出 | `paper/session.py:31/131/282/316-319/332`，`_subscribers:set[asyncio.Queue]:86`、`subscribe:98`、`_push:106` | ✅ |
| `PaperManager` 注册表 + start/stop/get/list/shutdown + 容量上限 | `paper/manager.py:48/56/154/163/201/207`，`_ensure_capacity:215`（`settings.max_paper_sessions`） | ✅ |
| `build_paper_strategy_spec(row)->PaperStrategySpec` | `paper/strategy_factory.py:30`（返回 strategy_id/name/kind/strategy/config_extra） | ✅ |
| SimBroker 单向单仓语义：`submit(list)`、`_open` notional=`strength*equity*leverage`、反手=先平后开、`apply_risk`(SL/TP/max_hold_bars) | `engine/simulator.py:206/245/250/266`，反手两单 `_pending`；`apply_risk:169`（`_open` 实为 245、`apply_risk` 实为 169，v1 行号近似但语义一致） | ✅ |
| `Signal{side:Literal["long","short","close"], strength:0~1, reason}` + `risk_params()`（含 max_hold_bars） | `strategies/base.py:17/20/21/22/92/98` | ✅ |
| 净仓反向单本地拦截 `LIVE_WOULD_REDUCE_POSITION` | `okx/trading.py:142-144` + `_would_reduce_net_position:101` | ✅ |
| `close_live_position` 存在（含 `auto_cxl=True`、市价/限价、`LIVE_NO_POSITION` 守门）、`close_all_live_positions`、`set_account_leverage`、`fetch_live_positions/fills` | `okx/trading.py:208/256/305/319/332`（前端 `tradingApi.closePosition/closeAll` 已对应） | ✅ |
| `validate_order` 名义上限拦截、`build_attach_algo_orders` OCO、`is_instrument_supported`（settle=USDT 且 ct_type=linear，覆盖 SWAP/FUTURES）、`snap_size`/`compute_notional` | `okx/order_builder.py:153/115/52/75/94` | ✅ |
| `config.py` live 字段、`live_max_order_notional_usd=1000.0`、`live_default_leverage=3.0` | `config.py:50/52`（**但 `live_trading_enabled=True`，见 R1**） | ✅/⚠️ |
| `LiveOrderAudit`(status accepted/rejected/error, clOrdId, ordId, error_code, 秒级 UTC) + `create_live_order_audit` + `get_default_okx_account` | `db/models.py:179`、`db/repositories.py:647/536` | ✅ |
| clOrdId 前缀 `aragon`(开)/`aragonc`(平) | `okx/trading.py:73/172` | ✅ |
| 前端 `Switch`/`Modal`、`ReconnectingWS`+`paperWS`、`tradingApi.request`(`{error:{code,message}}`)、`Live*Table`(5s/8s, accountMode==="live" 渲染)、`overlayTrades` 在 live 模式为 `undefined`（加 liveAutoTrades 纯增量） | `ui/index.tsx:149/249`、`lib/ws.ts`、`lib/tradingApi.ts:20`、`ChartWorkspace.tsx`、`app/page.tsx:401-406` | ✅ |
| `schemas.py` camelCase wire；`PAPER_WINDOW_MAX=1200`（`paper/session.py:22`） | `schemas.py:10`（import）/ paper.session | ✅ |

### 本轮问题清单（severity P0 / P1 / P2）

| # | 维度 | 严重度 | 问题 | 处置 |
|---|---|---|---|---|
| R1 | 一致性 / 安全 | **P1** | v1 多处（关联红线、§2.1 双重独立守门、§2.4①、§八 风险①、§六）以「**双重默认关闭** `LIVE_TRADING_ENABLED`+`LIVE_AUTO_TRADING_ENABLED`」作为「无人值守误下单」这一最高优先级风险的**头号缓解**。但当前工作树 `config.py:47` 明确 `live_trading_enabled: bool = True`（注释「本轮需求把实盘下单默认翻为开启」）。即**底层写链路总闸现已默认开启**，所谓「双重默认关闭」**不成立**——真正默认关闭的仅有新增的 `LIVE_AUTO_TRADING_ENABLED`。把不成立的护栏列为头号缓解，会误导下游与运维高估默认安全姿态，对真实资金属安全相关一致性缺陷，定 P1。 | **已修复**：§2.1「双重独立守门」、§2.4①、§八 风险①、§六 错误码表均据实更正——明确 `LIVE_TRADING_ENABLED` 现默认 `True`，**自动化的默认关闭闸为 `LIVE_AUTO_TRADING_ENABLED`（默认 `false`，仍须显式开启才能自动下单）**；并新增运维建议：无人值守部署应显式置 `LIVE_TRADING_ENABLED=false` 之外的环境亦须确保 `LIVE_AUTO_TRADING_ENABLED` 不被误开。`place_live_order` 内 `LIVE_TRADING_ENABLED` 守门仍是写链路硬闸（关闭即 403），此事实保留。 |
| R2 | 可行性 / 完备性 | **P1** | §三 `db/repositories.py` 行与 §5.3 / §2.6 称「**复用 `create_run`/`finalize_run`** 为自动会话建/结 Run」。核验：`create_run` 存在（`repositories.py:130`，`PaperManager:76` 已用），但 **`finalize_run` 并不存在**——Run 终态更新当前内嵌于 `persist_backtest_result:142`/`persist_walk_forward_result:180`，**无通用终态/状态更新函数**。把不存在的函数标为「复用」会让下游误以为零成本，且停止时无法落 Run 终态指标，属可行性/完备性缺陷，定 P1。 | **已修复**：§三 `db/repositories.py` 行改为「复用 `create_run`（PaperManager 已用）+ **新增最小 `finalize_run(session, run_id, *, status, metrics)`** 仓储函数（仿 `append_trade/append_equity` 提交模式，仅 UPDATE 终态/指标）」；§2.6 / §5.3 同步标注 finalize 为**新增**而非复用。 |
| R3 | 完备性 | **P1** | 核心调用路径缺失「DB 会话」这一**必备入参**。实代码 `place_live_order(session: AsyncSession, creds, account_id, order, *, settings)` 与 `close_live_position(session, creds, account_id, close)` **均需 `AsyncSession`**（用于审计落库），但 v1 §2.2 / §3 `LiveExecutor.apply(signals)` 全程**未提会话从何而来**。长运行会话不能复用单个长生命周期 session。下游按 v1 签名实现会立即卡壳。 | **已修复**：§2.2 新增「会话获取约定」——`LiveExecutor` 每次下单/平仓经 `get_sessionmaker()` 开**短生命周期 session**（`async with sm() as s: await place_live_order(s, creds, account_id, order, settings=settings)`），镜像 `PaperManager:74`/`paper/session.py:400`；`creds` 于会话启动时一次解析复用；§3 executor/session 行与 §5.2 内存形态同步补 `session_factory`/`creds`/`account_id`/`settings`。 |
| R4 | 安全 / 完备性 | **P1** | §八 风险①宣称「预算**硬上限 5%**」、§三 `config.py` 列 `live_auto_max_capital_frac=0.05`，但 §2.4 前置门禁与 §4.1 start **均未**校验/夹紧 `capitalUsd ≤ availUsdt × live_auto_max_capital_frac`——宣传的硬上限**无落点**，用户在弹窗填超大预算时不被服务端拦。虽单笔名义上限 1000 仍兜底每单，但「预算硬上限」作为对外承诺的护栏未实现，属安全相关完备性缺口，定 P1。 | **已修复**：§2.4 前置门禁新增第 7 步「**预算硬上限校验**」——读 availUsdt（复用 `okx/trading._avail_usdt` + balance），`capitalUsd`（含留空派生的默认 1%）一律夹紧到 `≤ availUsdt × live_auto_max_capital_frac`，超限回 `400 LIVE_AUTO_BUDGET_EXCEEDS`（或夹紧并在 start 回执 `budgetUsd` 告知）；§4.1 / §六 / §八 同步。 |
| R5 | 可行性 | **P1** | §2.2「反手必须走『先平后开』」断言「对 net_mode 与 long_short_mode 同时正确」。但 net_mode 下：`close_live_position` 市价平仓返回后，紧接的 `place_live_order` **内部会自行 `get_positions()`**（`trading.py:141`）并对反向单本地拦截（`LIVE_WOULD_REDUCE_POSITION`，`:142`）。若平仓尚未在交易所侧结算完毕，open 的内部前查可能**仍读到未清算的旧净仓**→ 把开新仓**误判为反向减仓而拒单**，导致反手失败、bot 停在 flat（安全但功能不达「可靠买卖」）。v1 未处理此竞态。 | **已修复**：§2.2 关键不变量「反手」补**净仓清算确认**——net_mode 下 close 返回后，executor 先 `get_positions()` 复核该 instId `pos==0`（短轮询至多 N 次/超时）**确认已平**再 open；若超时仍未清算则**本轮跳过 open**、记 status 事件，交由下一根/ reconcile 重试，杜绝 open 被反向单护栏误拒。long_short_mode 因 open 走独立 posSide、不触发 net 反向护栏，不受影响。 |
| R6 | 一致性（据实记法） | P2 | §2.2 表 / §2.3 用 `place_live_order(direction=…, ordType=…, sz, lever, attachAlgo=策略止盈止损)` 与 `close_live_position(posSide, mgnMode, ordType="market", autoCxl=True)` 的「具名参数」记法，与实签名不符：实参为 `OrderInput`(含 `tp_trigger_px`/`sl_trigger_px`，OCO 由 `build_order_payload` 内部生成，**无 `attachAlgo` 形参**) 与 `ClosePositionInput`(含 pos_side/mgn_mode/ord_type/px/pos/sz，**`auto_cxl` 已硬编码 `True`**、无该形参)。 | **已就地修正**：§2.2 表脚注 + §2.3 注明「TP/SL 经 `OrderInput.tp_trigger_px/sl_trigger_px` 传入、OCO 内部组装；平仓 `auto_cxl` 已恒为 True」，避免下游照搬不存在的形参。 |
| R7 | 规模适配 / 安全 | P2 | §三 `okx/trading.py ✎`「增加可传入预解析账户/客户端的内部入口」**多半不必要**：实签名**已**接受预解析 `creds` + `session`（无每单 DB 读账户），仅每单新建一次 `OKXTradeClient`（与既有人工流一致、`finally: aclose()`，可接受）。为省一次建客户端而**改动经审计的实盘写链路**风险高于收益，且 CLAUDE.md「错误码/护栏稳定、更名等同破坏性变更」。 | **建议（已注于 §三/§九）**：v1 实现优先**原样调用** `place_live_order`/`close_live_position`/`set_account_leverage`，**不改** `okx/trading.py` 既有函数；仅按需**新增只读** `fetch_session_pnl`（或直接复用 `fetch_live_positions` 的 `upl`）。把 `okx/trading.py` 由「✎」降级为「（可选）仅新增读取辅助，不动既有写函数」。 |
| R8 | 完备性 | P2 | 平仓竞态：交易所侧 OCO 在两次 reconcile 之间已平仓后，executor 再发 close 会得 `LIVE_NO_POSITION`（`trading.py:225`）。v1「下单失败不崩会话」笼统覆盖，但未点明此码属**良性**（实为已平），不应作刺眼 error 上报。 | **已注**：§2.2 失败处理 + §六 注明 `LIVE_NO_POSITION` 于平仓路径按「已平/flat」良性处理（归 projected=flat、status 事件），不计入异常告警。 |
| R9 | 一致性 | P2 | `StrategyPanel.tsx`（`:49-83` 解构）**未取 `accountMode`**；v1 §3 让 `<AutoTradeToggle>` 守门含 `accountMode==="live"`。 | **已澄清**：§3 注明 `AutoTradeToggle` 作为独立组件**直接订阅全局 store** 的 `accountMode`/`hasReadyDefault`/`liveAutoTradingEnabled`，`StrategyPanel` 仅以其已有的 `mode==="paper"` 控制**是否渲染**该开关；并提示 Subtask #2 核验 `accountMode==="live"` 时 StrategyPanel（右栏）确实在场。 |
| R10 | 完备性 / UX | P2 | 需求明列弹窗示意「可用账户仓位」。v1 弹窗摘要列「账户/标的/策略/杠杆」与可选预算/止盈止损，但未明确**展示 availUsdt 与据此派生的默认预算（1%）**，致「留空＝保守默认」对用户不透明。 | **已注**：§3 `AutoTradeConfirmModal` 增「展示 availUsdt + 默认预算(availUsdt×1%) 预览」，使留空默认对用户可见可核。 |
| R11 | 完备性 | P2 | §2.2 张数换算「夹到 `>= minSz`」：当 `strength·budget·leverage` 算得 sz < minSz 时强制抬到 minSz，可能**超出预算意图**（极小预算尤甚）。单笔 1000 名义上限仍兜底。 | **已注**：§2.2 注明「计算 sz < minSz 时按策略**跳过该信号或抬至 minSz 二选一**（v1 取跳过更稳健，避免越预算），并以 signal/status 事件告知」。 |

---

## 一、Overview（概述）

当前 Aragon Trading 工作台有三种「模式」：回测（`mode==="backtest"`）、对比（`"compare"`）、**实时**（`"paper"`，即「实时模拟」）。实时模式下，用户在右侧策略面板选中一个策略，点「启动实时模拟」即用 `PaperSession` 跑真实 OKX 行情 + `SimBroker` 模拟撮合（不下真单）。与之并行，软件还有一个**账户模式**开关（`accountMode`：`"sim"` 模拟盘 / `"live"` 实盘）；切到 `"live"` 时左下方区域渲染 `<LiveBottomPanel>`（人工逐单下单 + 真实持仓/成交轮询）。这两个维度（mode 与 accountMode）相互独立。

本方案要新增的能力是：**当用户处于「实时模式」（`mode==="paper"`）时，右侧「交易策略」面板的标题栏最右侧出现一个「自动交易」开关**。该开关仅在软件整体切换到**实盘账户模式**（`accountMode==="live"`、有就绪默认账户、`LIVE_AUTO_TRADING_ENABLED=true`）时可用。打开开关 → 先弹出一个**美观优雅的确认弹窗**（可填「可用账户资金预算」与「预期止盈/止损金额阈值」，**全部可选、默认留空即用保守内置默认**，什么都不填直接确认即开始）→ 后端起一个 **`LiveAutoSession`**：按当前选中策略实时步进，把策略信号自动翻译为**真实 OKX 下单/平仓**，并随单挂上**交易所侧止盈止损**（OCO `attachAlgoOrds`）。买卖记录实时画到 K 线图上（markers），左下方真实持仓/成交表（`LivePositionsTable`/`LiveFillsTable`）随真实账户刷新。关闭开关 → 平掉该会话自管持仓并停止会话。

工程层面：后端新增一个 **`live_auto/` 包**（镜像 `paper/`：`LiveAutoSession` 逐根驱动 + `LiveExecutor` 信号→真实订单 + `LiveAutoManager` 单会话独占注册表）、一组受 `LIVE_AUTO_TRADING_ENABLED` 守门的 **`/api/live-auto/*` REST 接口**与一个 **`/ws/live-auto/{session_id}` WebSocket**（事件协议与 `/ws/paper/{id}` 同构）；前端新增「自动交易」开关组件、启动确认弹窗、`liveAutoApi`/`liveAutoTypes` 两个 lib，并把自动交易的成交流接入既有图表 `overlayTrades` 渲染。所有真实下单**一律经过既有 `okx/trading.py` 服务层**，从而自动继承全部安全护栏与审计——本方案**不新增任何绕过护栏的下单路径**。

---

## 二、Technical design（技术设计）

### 2.1 架构与数据流

```text
浏览器/Electron（mode==="paper" 实时模式 + accountMode==="live" 实盘）
  ├─ 右侧 StrategyPanel 标题栏右上：<AutoTradeToggle>（Switch）
  │     ├─ 守门：accountMode==="live" && hasReadyDefault && liveAutoEnabled && 已选策略 && instrumentSupported
  │     ├─ 打开 → <AutoTradeConfirmModal>（可填 预算 / 止盈止损金额阈值；全部可选）
  │     └─ 确认 → liveAutoApi.start({strategyId, instId, bar, capitalUsd?, takeProfitUsd?, stopLossUsd?, leverage?})
  ├─ 经 Next 同源代理 /api/py/* → FastAPI :18760
  │     └─ api/live_auto.py（受 LIVE_AUTO_TRADING_ENABLED 守门）
  │           └─ paper.strategy_factory.build_paper_strategy_spec(row)  ← 复用：DB行→可运行策略
  │           └─ live_auto/manager.LiveAutoManager.start(...)（单会话独占 cap=1）
  │                 └─ live_auto/session.LiveAutoSession（镜像 PaperSession 逐根驱动）
  │                       ├─ OKX 公共 WS 实时 K 线（OKXWSClient）+ 暖机 priming（仅热指标，不下单）
  │                       ├─ 每根确认 K 线：strategy.on_candle(df,i,ctx) → list[Signal]
  │                       └─ LiveExecutor.apply(signals)        ← 替代 SimBroker.submit
  │                             ├─ 开/平/反手 → okx/trading.place_live_order / close_live_position
  │                             │     （继承 LIVE_TRADING_ENABLED / 名义上限 / 净仓反向拦截 / clOrdId / 审计 / 脱敏）
  │                             ├─ 随单 attachAlgoOrds（交易所侧 OCO 止盈止损，来自策略 risk_params）
  │                             ├─ 持仓真源：周期性 reconcile（OKX get_positions）→ 校正 projected
  │                             └─ 会话级止盈止损：累计 PnL ≥ +TP 或 ≤ −SL → 自动平仓 + 停会话
  └─ 前端 /ws/live-auto/{id}（事件 snapshot/trade/position/equity/signal/status/error，与 paper 同构）
        ├─ trade 事件 → store.liveAutoTrades → 图表 overlayTrades（buildTradeMarkerEvents → setMarkers）
        ├─ 左下方 <LiveBottomPanel>：真实持仓/成交（LivePositionsTable 5s / LiveFillsTable 8s 轮询，真源 OKX）
        └─ status/equity → 顶部「自动交易运行中」状态条 + 会话权益/盈亏摘要
```

要点：
- **双重独立守门（R1 据实修正）**：① 既有 `LIVE_TRADING_ENABLED`——写链路总闸，但其强制点在 **API 路由层**（`api/trading.py:_require_live_enabled:55`），**并非** `place_live_order`/`close_live_position` 函数体内；且**当前默认已为 `True`**（`config.py:47`，「本轮需求把实盘下单默认翻为开启」）。② 新增 `LIVE_AUTO_TRADING_ENABLED`（**自动化专用总闸**，默认 **`false`**）——这是自动交易**真正的默认关闭闸**。
  - 因 ① 的强制点在路由层、且自动路径**直接调用服务层** `place_live_order`（绕开 `api/trading.py` 的 `_require_live_enabled`），故自动路径**必须**在 `api/live_auto.py` 守门 + `LiveAutoManager` 前置门禁（§2.4①）处**显式同时校验** `LIVE_AUTO_TRADING_ENABLED && LIVE_TRADING_ENABLED`，两者皆 `true` 方可启动；任一为 `false` → `403 LIVE_AUTO_TRADING_DISABLED`。不可依赖「服务层内已强制」（实则未强制）。
  - **安全姿态结论**：由于 `LIVE_TRADING_ENABLED` 现默认开启，**自动下单的默认安全完全依赖 `LIVE_AUTO_TRADING_ENABLED=false`**。自动化风险高于人工，单独这道默认关闭闸即为头号护栏；运维若需无人值守环境的额外纵深防御，应显式确保 `LIVE_AUTO_TRADING_ENABLED` 不被误开，并可按需将 `LIVE_TRADING_ENABLED` 置 `false` 一键回退「实盘禁用」姿态。
- **单会话独占**：`LiveAutoManager` 容量上限 `live_auto_max_sessions=1`（真实资金，绝不并发多策略自动下单）。
- **账户唯一**：自动交易固定使用**设置中心的默认实盘账户**（需求「当前默认的真实账户」），即 `get_default_okx_account()`。
- **持仓/成交真源在 OKX**：会话内不缓存/伪造持仓；`LiveExecutor` 周期性 `get_positions()` reconcile，前端持仓/成交表直接轮询真实账户。图表 markers 来自会话自身记录的「已执行订单→Trade」事件流（含 reason / 止盈止损价），与真源持仓互为印证。

### 2.2 信号 → 真实订单映射（`LiveExecutor`，镜像 `SimBroker.submit` 语义）

`SimBroker` 的单向单仓语义（见 `engine/simulator.py:206 submit`）是本映射的权威参照，必须逐条对齐：

| 策略信号（`Signal.side`，见 `strategies/base.py:17`） | 当前自管持仓 `projected` | `LiveExecutor` 动作 |
|---|---|---|
| `"long"` | `flat` | **开多**：`place_live_order(direction="long", ordType="market", sz, lever, attachAlgo=策略止盈止损)` |
| `"short"` | `flat` | **开空**：`place_live_order(direction="short", ...)` |
| `"long"` | `long`（同向） | **跳过**（不加仓，v1 单位仓，守安全） |
| `"short"` | `short`（同向） | **跳过** |
| `"long"` | `short`（反向） | **反手**：先 `close_live_position(自管空仓)` → 再 `place_live_order(direction="long")` |
| `"short"` | `long`（反向） | **反手**：先 `close_live_position` → 再开多 |
| `"close"` | `long`/`short` | **平仓**：`close_live_position(posSide, mgnMode, ordType="market", autoCxl=True)` |
| `"close"` | `flat` | 跳过 |

> **调用记法说明（R6 据实修正）**：上表/下文为可读性用「具名参数」示意，**实际服务层签名为**：
> - 开/反手开 → `place_live_order(session, creds, account_id, order: OrderInput, *, settings)`，止盈止损经 **`OrderInput.tp_trigger_px`/`sl_trigger_px`** 传入（OCO `attachAlgoOrds` 由 `build_order_payload` 内部组装，**无 `attachAlgo` 形参**）。
> - 平仓 → `close_live_position(session, creds, account_id, close: ClosePositionInput)`，`ClosePositionInput` 含 `inst_id/pos_side/mgn_mode/ord_type/px/pos/sz`；**`auto_cxl` 已硬编码 `True`**（撤随单 algo），无该形参。
> - 设杠杆 → `set_account_leverage(creds, inst_id, lever, mgn_mode, pos_side=None)`（无需 session）。

> **会话与 DB session 获取约定（R3，必读）**：`place_live_order`/`close_live_position` **均需 `AsyncSession`**（用于 `live_order_audit` 落库）。长运行会话**不得**复用单个长生命周期 session；`LiveExecutor` 每次下单/平仓经 **`get_sessionmaker()` 开短生命周期 session**：`sm = get_sessionmaker(); async with sm() as s: await place_live_order(s, creds, account_id, order, settings=settings)`，镜像 `paper/manager.py:74`/`paper/session.py:400`。`creds: AccountCredentials` 于**会话启动时一次解析**（默认实盘账户）后整会话复用，避免每单 DB 读账户。

关键不变量：
- **反手必须走「先平后开」两步**，**不**用 `place_live_order` 的反向单——既有 net_mode 反向单本地拦截（`LIVE_WOULD_REDUCE_POSITION`，`trading.py:142`）会拒掉反向 `place_live_order`；平仓走独立的 `close_live_position` 路径（合法、且 `auto_cxl=True` 会撤掉随单 algo），随后再开新仓。
  - ⚠️ **net_mode 反手竞态与清算确认（R5，安全关键）**：net_mode 下 `close_live_position` 市价平仓返回后，紧接的 `place_live_order` **内部会自行 `get_positions()`**（`trading.py:141`）并对反向单本地拦截。若平仓尚未在交易所侧结算完毕，open 的内部前查可能仍读到**未清算的旧净仓**→ 把开新仓误判为反向减仓而拒（`LIVE_WOULD_REDUCE_POSITION`），反手失败、bot 停在 flat。**故 net_mode 反手在 close 返回后，必须先 `get_positions()` 复核该 instId `pos==0`（短轮询至多 N 次/带超时）确认已清算，再 open**；超时仍未平则**本轮跳过 open**、记 status 事件，交下一根/ reconcile 重试。long_short_mode 反手因 open 走独立 `posSide`、不触发 net 反向护栏，不受此竞态影响。
- **`strength` → 下单张数**：沿用 `SimBroker._open` 的 `notional = strength · equity · leverage`（`simulator.py:250`），但 `equity` 换成**会话预算 `budgetUsd`**：
  `targetNotionalUsd = strength · budgetUsd · leverage`；`sz(张) = targetNotionalUsd / (ctVal · refPx)`；按 `lotSz` 向下取整（`order_builder.snap_size`），并经 `validate_order` 受 `live_max_order_notional_usd` 名义上限本地拦截。`refPx` 取最新成交价 `lastPx`（市价单），名义上限用保守价（买多 `lastPx·(1+buffer)`）以免越限漏拦——复用 `order_builder.validate_order` 既有逻辑（`order_builder.py:153`）。
  - **sz < minSz 处置（R11）**：当算得 `sz < minSz`，**默认跳过该信号**（更稳健，避免抬至 minSz 反而越预算意图），以 `signal`/`status` 事件告知「预算/杠杆过小，单笔不足 minSz，已跳过」；如产品上需保证最小可成交，亦可改为抬至 minSz，但须同时确保不超 `live_max_order_notional_usd`。v1 取「跳过」。
- **每根至多一次状态跃迁**：一根确认 K 线最多触发「一次平 + 一次开」（反手），避免抖动连环下单。
- **下单失败不崩会话**：任一下单/平仓抛错（余额不足 / 拒单 / 连通失败）→ 记 `error` 事件推 WS + 写 `live_order_audit`（既有路径已实现失败留痕），**保持 `projected` 与真实持仓一致**（下一次 reconcile 校正），会话继续。
  - **平仓 `LIVE_NO_POSITION` 良性处置（R8）**：交易所侧 OCO 已在两次 reconcile 之间触发平仓后，executor 再发 close 会得 `LIVE_NO_POSITION`（`trading.py:225`）——此码于平仓路径属**良性**（标的实已平），**不**作刺眼 `error` 告警：归 `projected=flat`、推一条 `status` 事件即可，会话继续。

### 2.3 止盈止损与平仓（两层）

1. **每仓·交易所侧 OCO（来自策略风控参数）**：开仓时把策略的 `risk_params()`（`stop_loss_pct`/`take_profit_pct`，`base.py:92`）按 `SimBroker._open_risk_snapshot`（`simulator.py:266`）同一公式换算为触发价，挂进 `attachAlgoOrds`：
   - 多：`slTriggerPx = fill·(1−stop_loss_pct)`，`tpTriggerPx = fill·(1+take_profit_pct)`；
   - 空：`slTriggerPx = fill·(1+stop_loss_pct)`，`tpTriggerPx = fill·(1−take_profit_pct)`。
   交易所侧 OCO 即使 bot 停止/断线仍在 OKX 生效（服务器端兜底），复用 `order_builder.build_attach_algo_orders`（`order_builder.py:115`）。`refPx` 为市价单的 `lastPx`，UI 须标注「按最新价估算，含滑点」。
2. **每仓·时间止盈（`max_hold_bars`）**：`LiveExecutor` 跟踪「进仓后已过确认根数」，达到 `max_hold_bars`（`base.py:98`）即主动 `close_live_position`，对齐 `SimBroker.apply_risk`（`simulator.py:195`）。
3. **会话级·金额阈值（来自确认弹窗，可选）**：每根 reconcile 后计算**会话累计盈亏**（已实现平仓 PnL + 当前持仓未实现 PnL，源自 OKX `upl`）；若设了 `takeProfitUsd` 且累计 ≥ `+takeProfitUsd`，或设了 `stopLossUsd` 且累计 ≤ `−stopLossUsd` → **自动平仓 + 停会话**（`stop_reason="session_take_profit"|"session_stop_loss"`），推 status 事件告知前端。未设则不触发。

### 2.4 启动前置门禁（Pre-flight，安全关键）

`LiveAutoManager.start` 在创建会话前**顺序校验**，任一不过即拒绝并回结构化错误（绝不假启动）：
1. `LIVE_AUTO_TRADING_ENABLED` 且 `LIVE_TRADING_ENABLED` 均为 `true`，否则 `403 LIVE_AUTO_TRADING_DISABLED`。
2. 有**就绪默认账户**（含 secret/passphrase），否则 `400 ACCOUNT_NOT_CONFIGURED`。
3. 标的为 **USDT 本位 SWAP/FUTURES**（`is_instrument_supported`，`order_builder.py:52`），否则 `400 LIVE_INSTRUMENT_UNSUPPORTED`。
4. **该标的当前无任何既有持仓**（`get_positions()` 该 instId `pos==0`），否则 `409 LIVE_AUTO_PREEXISTING_POSITION`——守需求/红线「**不能动现有持仓**」：bot 只管它自己开的仓，绝不触碰用户已有持仓。
5. **无其它活跃自动会话**（`live_auto_max_sessions=1`），否则 `409 LIVE_AUTO_SESSION_LIMIT`。
6. 选中策略存在且可实例化（复用 `build_paper_strategy_spec`，`paper/strategy_factory.py:30`）。
7. **预算硬上限校验（R4，安全关键）**：读 `availUsdt`（复用 `okx/trading._avail_usdt(balance)` + 余额拉取），解析最终预算 `budgetUsd`——`capitalUsd` 留空则取 `availUsdt × live_auto_default_capital_frac`(1%)，已填则取填值；随后**一律夹紧到 `≤ availUsdt × live_auto_max_capital_frac`(默认 5%)**。若用户填值超该硬上限：默认**夹紧到上限**并在 start 回执 `budgetUsd` 如实告知（或按实现选择回 `400 LIVE_AUTO_BUDGET_EXCEEDS` 拒绝）。此步把 §八「预算硬上限 5%」承诺落到实处，杜绝弹窗填超大预算绕过护栏（单笔 `live_max_order_notional_usd`=1000 仍各单兜底）。

### 2.5 暖机 priming 与持仓 reconcile

- **priming（仅热指标，不下单）**：复用 `PaperSession._prime_history`（`session.py:131`）思路——拉历史 K 线喂 `strategy.prepare()`/`on_candle()` 暖机指标与 `df`，但 `LiveExecutor` 在 priming 阶段**忽略一切信号、不下任何真单**，`projected` 恒为 `flat`（前置门禁 4 已保证起始无持仓）。真实下单从**第一根实时确认 K 线**开始。
- **reconcile（真源校正）**：每根确认 K 线后（或每 `live_auto_poll_interval_s` 秒）`get_positions()` 取该 instId 真实持仓，用以：① 校正 `projected`（防 bot 内部状态与真实账户漂移，例如交易所侧 OCO 已触发平仓、或外部干预/强平）；② 计算未实现 PnL 供会话级止盈止损与权益事件。若发现「`projected` 非 flat 但真实已平」→ 记 status 事件、`projected` 归 flat。

### 2.6 停止与清理

- **用户关开关 / 调用 stop**：`LiveAutoSession.stop` → ① 撤销该 instId 未成交挂单并 `close_live_position` **平掉自管持仓**（市价，`auto_cxl=True`）；② 取消 WS/任务；③ 落 Run 终态指标（经**新增** `finalize_run`，见 §三/§5.3——`create_run` 可复用，`finalize_run` 当前**不存在**须新建）；④ 推 `status{state:"stopped", stopReason}`。默认**平仓退出**（不留无人管理的真实持仓），交易所侧 OCO 亦随 `auto_cxl` 撤销。
- **会话级止盈止损触发**：同上自动执行，`stopReason` 标注。
- **进程关闭（FastAPI lifespan）**：`LiveAutoManager.shutdown()` 随 `paper` 一并在 `main.py` lifespan 注册，停止所有活跃自动会话（含平仓）。
- **前端重载孤儿**：renderer 刷新后 `liveAutoSessionId` 不持久化（瞬态），但后端会话仍在跑；前端挂载时 `GET /live-auto/status` 重新发现并重连 WS（镜像 paper 的 reconcile 思路）。

---

## 三、File / module change plan（文件 / 模块变更计划）

> 约定：✚=新建，✎=修改。所有改动遵循 CLAUDE.md 阈值（单文件 ≤800 行、单函数 ≤50 行、≤5 形参、圈复杂度 ≤10、嵌套 ≤4、命名自解释）。真实下单**一律经 `okx/trading.py` 服务层**，不新增绕过护栏的下单路径。

### 后端 `services/quant/app/`

| 文件 | 操作 | 一句话意图 |
|---|---|---|
| `live_auto/__init__.py` | ✚ | 包导出（`LiveAutoManager`/`get_live_auto_manager`/`LiveAutoSessionLimitError`/`LiveAutoPreflightError`）。 |
| `live_auto/session.py` | ✚ | `LiveAutoSession`：镜像 `PaperSession` 的逐根驱动（OKX 公共 WS + priming + 确认根流水线 + WS 快照/事件扇出），但用 `LiveExecutor` 替代 `SimBroker`；维护会话累计 PnL、reconcile、停止平仓。 |
| `live_auto/executor.py` | ✚ | `LiveExecutor`：纯编排层，持有 `creds`/`account_id`/`budget`/`leverage`/`settings` 及 `session_factory=get_sessionmaker`（R3），把 `list[Signal]` 按 §2.2 表翻译为对 `okx/trading.place_live_order`/`close_live_position`/`set_account_leverage` 的调用（每单 `async with session_factory() as s` 开短生命周期 session 落审计）；张数换算（`strength·budget·lever`→sz）、止盈止损经 `OrderInput.tp_trigger_px/sl_trigger_px`（R6）、`max_hold_bars` 计时平仓、net_mode 反手清算确认（R5）、失败留痕不崩、`LIVE_NO_POSITION` 良性（R8）、projected 状态机。 |
| `live_auto/manager.py` | ✚ | `LiveAutoManager`：单会话独占注册表（cap=1）、§2.4 前置门禁、lifecycle（start/stop/get/status/shutdown）、`session_id=f"liveauto_{uuid.hex}"`。 |
| `api/live_auto.py` | ✚ | 路由：`POST /live-auto/start`、`POST /live-auto/{id}/stop`、`GET /live-auto/status`、`WS /ws/live-auto/{id}`；写类受 `LIVE_AUTO_TRADING_ENABLED` 守门（`_require_auto_enabled`，仿 `trading._require_live_enabled`）。 |
| `okx/trading.py` | （可选，**仅新增读取，不动既有写函数**，R7） | **R7 据实降级**：既有 `place_live_order(session, creds, account_id, order, *, settings)` / `close_live_position(session, creds, account_id, close)` **已接受预解析 `creds`+`session`**（无每单 DB 读账户），仅每单新建一次 `OKXTradeClient`（与人工流一致、`finally: aclose()`，可接受）。为省一次建客户端而改动**经审计的实盘写链路**风险高于收益（CLAUDE.md：护栏/错误码稳定，更名即破坏性变更）。故 v1 实现**优先原样调用**既有写函数、**不改其签名与护栏**；仅按需**新增只读** `fetch_session_pnl`（或直接复用 `fetch_live_positions` 返回的 `upl`）。若不需该辅助，本文件可不改。 |
| `config.py` | ✎ | 新增 `live_auto_trading_enabled: bool=False`（**自动化专用总闸，自动下单真正的默认关闭闸，R1**）、`live_auto_max_sessions: int=1`、`live_auto_default_capital_frac: float=0.01`（留空预算时取可用 USDT 的 1%）、`live_auto_max_capital_frac: float=0.05`（预算对可用余额硬上限，**§2.4⑦ 前置门禁强制夹紧，R4**）、`live_auto_poll_interval_s: int=5`、`live_auto_default_leverage`（复用 `live_default_leverage=3.0`）。复用既有 `live_max_order_notional_usd=1000.0` 作单笔名义上限。**注意**：既有 `live_trading_enabled` 现默认 `True`（`config.py:47`，R1），本方案**不改其默认**，仅以新增 `live_auto_trading_enabled=False` 作自动化默认关闭闸。 |
| `schemas.py` | ✎ | 新增 `LiveAutoStartIn`/`LiveAutoStartOut`/`LiveAutoStatusOut`/`LiveAutoSessionRow`（见 §五）。 |
| `main.py` | ✎ | `from .api import ... live_auto`；`app.include_router(live_auto.router, prefix="/api")`；lifespan 关停增 `await get_live_auto_manager().shutdown()`（紧随 paper manager 关停）。 |
| `db/repositories.py` | ✎ | **复用** `create_run`（`repositories.py:130`，`PaperManager:76` 已用）为自动会话建 Run（`config` 标 `mode:"live-auto"` + 预算/止盈止损/strategyId）；**新增** `finalize_run(session, run_id, *, status, metrics)`（**R2：`finalize_run` 当前不存在**——Run 终态更新现仅内嵌于 `persist_backtest_result:142`/`persist_walk_forward_result:180`；故须仿 `append_trade/append_equity` 提交模式新建一个仅 UPDATE 终态/指标的小函数）停止时落终态。每笔真单经既有 `create_live_order_audit:647` 自动留痕，**无需新表**。 |

### 前端 `apps/web/`

| 文件 | 操作 | 一句话意图 |
|---|---|---|
| `lib/liveAutoTypes.ts` | ✚ | camelCase 类型：`LiveAutoStartReq`/`LiveAutoStartResult`/`LiveAutoStatus`/`LiveAutoSessionRow`/`AutoStopReason`。 |
| `lib/liveAutoApi.ts` | ✚ | `/api/py/live-auto/*` REST 客户端（复用 `tradingApi` 的 `request` 风格与错误体解析）+ `liveAutoWS(id, onEvent)`（复用 `ws.ts` reconnecting 客户端，事件协议同 paper）。 |
| `components/Strategy/AutoTradeToggle.tsx` | ✚ | 标题栏右上「自动交易」`Switch`（复用 `ui/index.tsx:149` 的 `Switch`）+ 守门禁用态与悬浮提示（缺实盘/缺账户/未启用/未选策略/标的不支持各有文案）；开→弹确认弹窗，关→确认后停会话。**R9**：本组件作为独立组件**直接订阅全局 `useAppStore`** 的 `accountMode`/`hasReadyDefault`/`liveAutoTradingEnabled`（`StrategyPanel` 当前未解构 `accountMode`，无需经其透传）。 |
| `components/Strategy/AutoTradeConfirmModal.tsx` | ✚ | 启动确认弹窗（复用 `ui` 的 `Modal`，对齐 `OrderConfirmModal` 风格但更隆重）：账户/标的/策略/杠杆摘要 + **可用余额 `availUsdt` 与据此派生的默认预算（availUsdt×1%）预览（R10，使「留空＝保守默认」对用户可见可核，呼应需求「可用账户仓位」）** + 可选「预算」「止盈金额」「止损金额」输入（占位提示「留空＝保守默认」）+ 真实资金风险勾选 + 主 CTA「确认并开始自动交易」。 |
| `components/Strategy/AutoTradeStatusBar.tsx` | ✚ | 运行中状态条（策略名 / 标的 / 已运行时长 / 自管持仓方向 / 会话盈亏 / 「停止」按钮）；停止态隐藏。 |
| `components/Strategy/StrategyPanel.tsx` | ✎ | 标题栏（`:424-436`）右上插入 `<AutoTradeToggle>`（仅 `mode==="paper"` 显示，置于「+ 新建组合」逻辑旁，二者互斥/并列按现有 `justify-between` 布局）。**R9**：`StrategyPanel` 仅以其已有的 `mode` 控制开关**是否渲染**，`accountMode` 守门由 `AutoTradeToggle` 自读全局 store；Subtask #2 须核验 `accountMode==="live"` 时右栏 `StrategyPanel` 确在场（与 `mode==="paper"` 可共存）。 |
| `components/Live/LiveTradePanel.tsx` | ✎ | 自动会话活跃时**禁用人工下单控件**并横幅提示「自动交易进行中，人工下单已暂停」（防人工与 bot 争抢同标的持仓）。读 store `liveAutoSessionId`。 |
| `components/Chart/ChartWorkspace.tsx` | ✎ | 透传/优先使用自动交易成交流：`overlayTrades` 在自动会话活跃时取 `liveAutoTrades`（live 模式下原本无策略 overlay，故为纯增量，不影响人工态）。 |
| `app/page.tsx` | ✎ | 新增 `useEffect` 订阅 `/ws/live-auto/{id}`（仿 paper 的 `handlePaperEvent`，写 `liveAutoTrades`/`liveAutoStatus`/`liveAutoPosition`）；挂载时 `GET /live-auto/status` 重连孤儿会话；把 `liveAutoTrades` 并入 `overlayTrades`。 |
| `lib/store.ts` | ✎ | 新增**瞬态**（不入 `partialize`）：`liveAutoSessionId: string|null`、`liveAutoStatus`、`liveAutoTrades`、`liveAutoPosition`、`liveAutoAcknowledged: boolean`（首启风险勾选会话记忆，刷新复位）及对应 setter；`persist` version bump + `migrate` 兜底。运行期句柄**不持久化**（与既有 `paperSessionId` 一致约定）。 |

### 测试

| 文件 | 操作 | 一句话意图 |
|---|---|---|
| `services/quant/tests/test_live_executor.py` | ✚ | 纯/桩单测：§2.2 信号→动作映射全表（开/平/同向跳过/反手先平后开）、张数换算与名义上限拦截、attachAlgo 止盈止损多空对称、`max_hold_bars` 计时平仓、下单失败不崩 + 状态一致。 |
| `services/quant/tests/test_live_auto_api.py` | ✚ | `ASGITransport` + 桩执行器：`LIVE_AUTO_TRADING_ENABLED=false`→403；前置门禁 4/5（既有持仓 409 / 第二会话 409）；start→status→stop 生命周期；停止平仓被调用。 |
| `services/quant/tests/test_live_auto_session.py` | ✚ | 用 replay/桩行情驱动一根确认 K 线：priming 不下单、首实时根触发开仓、reconcile 校正 projected、会话级止盈止损触发自动停。 |
| `apps/web/lib/liveAuto.test.ts` | ✚ | 前端纯件：守门状态判定、预算/止盈止损输入校验、留空→保守默认派生。 |
| `apps/web/components/Strategy/AutoTradeToggle.test.tsx` | ✚ | 守门禁用态、开→弹窗、确认→`liveAutoApi.start` 入参正确（`NODE_ENV=test`）。 |

### 桌面 `desktop/`

| 文件 | 操作 | 一句话意图 |
|---|---|---|
| `src/lib/config.js` | ✎（核验，既有白名单已含 `LIVE_*`） | 确认 `backendEnv` 透传 `LIVE_AUTO_TRADING_ENABLED` 等——既有白名单为 `key.startsWith('LIVE_')`（见 live-trading-mode spec C1-6），`LIVE_AUTO_*` 天然命中，无需改动；本行仅作复核备注。 |

---

## 四、Interface design（接口设计）

所有 `/live-auto/*` **写类**接口在 `LIVE_AUTO_TRADING_ENABLED=false`（或底层 `LIVE_TRADING_ENABLED=false`）时返回 `403 {error:{code:"LIVE_AUTO_TRADING_DISABLED", message:"实时自动交易未启用"}}`；`GET /live-auto/status` 为只读，不受该闸限制。错误体统一 `{error:{code,message}}`，message 先经 `_redact` 脱敏。

### 4.1 `POST /api/live-auto/start`
```jsonc
// LiveAutoStartIn（全部金额字段可选；留空→保守默认）
{
  "strategyId": 12,
  "instId": "BTC-USDT-SWAP",
  "bar": "1H",
  "accountId": null,            // 省略=默认实盘账户
  "capitalUsd": null,           // 预算（USDT），留空→ availUsdt × live_auto_default_capital_frac(1%)
  "leverage": null,             // 留空→ live_default_leverage(3)
  "takeProfitUsd": null,        // 会话级止盈金额阈值；留空→不触发
  "stopLossUsd": null,          // 会话级止损金额阈值；留空→不触发
  "priming": true,
  "primingBars": 300
}
// → LiveAutoStartOut
{ "sessionId": "liveauto_7f3a…", "runId": 412, "strategyKind": "atomic",
  "instId": "BTC-USDT-SWAP", "bar": "1H", "budgetUsd": 12.34, "leverage": 3 }
```
启动前执行 §2.4 全部前置门禁（含⑦**预算硬上限夹紧/校验**，R4）；任一不过回对应 4xx（`LIVE_AUTO_PREEXISTING_POSITION`=409、`LIVE_AUTO_SESSION_LIMIT`=409、`LIVE_INSTRUMENT_UNSUPPORTED`/`ACCOUNT_NOT_CONFIGURED`/`LIVE_AUTO_BUDGET_EXCEEDS`=400、`LIVE_AUTO_TRADING_DISABLED`=403）。回执 `budgetUsd` 为夹紧后的**最终生效预算**（可能 < 用户填值）。

### 4.2 `POST /api/live-auto/{session_id}/stop`
```jsonc
// → 平掉自管持仓 + 停会话；返回 Run 终态
{ "sessionId": "liveauto_7f3a…", "stopReason": "user_stop",
  "run": { "id": 412, "metrics": { /* 复用 Run metrics */ } },
  "flattened": { "ok": true, "closedSz": 1, "clOrdId": "aragonc…" } }
```

### 4.3 `GET /api/live-auto/status`
```jsonc
// 当前活跃自动会话（无则 session:null）；前端重载后据此重连 WS
{ "session": {
    "sessionId": "liveauto_7f3a…", "runId": 412, "state": "running",
    "strategyId": 12, "strategyName": "EMA 交叉剥头皮", "instId": "BTC-USDT-SWAP", "bar": "1H",
    "budgetUsd": 12.34, "leverage": 3, "takeProfitUsd": null, "stopLossUsd": null,
    "positionSide": "long", "sessionPnlUsd": 0.42, "startedTs": 1718000000 },
  "liveAutoTradingEnabled": true }
```

### 4.4 `WS /api/ws/live-auto/{session_id}`（事件协议与 `/ws/paper/{id}` 同构）
连接即原子「subscribe→snapshot」（防丢事件，对齐 paper R7）。事件 `type`：
- `snapshot`：`{ trades:Trade[], equity:EquityPoint[], position:PositionEvent|null, status:LiveAutoStatus }`
- `trade`：单笔已执行真单 → `Trade{ts, side:"long"|"short"|"close", price, qty, fee, pnl, reason, stopLossPrice, takeProfitPrice, ...}`（驱动图表 markers）
- `position`：`{side:"long"|"short"|"flat", size, avgEntry, markPrice, unrealizedPnl}`（真源 OKX）
- `equity`：`{ts, equity, drawdown}`（会话权益 = 预算 + 累计 PnL）
- `signal`：`{ts, side, reason}`（策略原始信号，便于诊断未成交/被拦截）
- `status`：`{state:"running"|"stopped", warmedBars, sessionPnlUsd, stopReason?}`
- `error`：`{ts, code, message}`（单笔下单失败的脱敏提示，不崩会话）

### 4.5 后端 → OKX（全部经既有服务层，无新增上游端点）
下单 `place_live_order`→`/api/v5/trade/order`（含 `attachAlgoOrds`）；平仓 `close_live_position`→`/api/v5/trade/close-position`（`autoCxl=true`）；设杠杆 `set_account_leverage`→`/api/v5/account/set-leverage`；持仓 `get_positions`→`/api/v5/account/positions`。签名/紧凑 JSON/`content=`/唯一 clOrdId/审计/脱敏一律复用既有实现，**本方案零新增签名代码**。

---

## 五、Data model（数据模型）

### 5.1 后端 Pydantic（`schemas.py`，camelCase wire）
```python
class LiveAutoStartIn(BaseModel):
    strategyId: int
    instId: str = Field(min_length=1, max_length=32)
    bar: str = "1H"
    accountId: int | None = None
    capitalUsd: float | None = Field(default=None, gt=0)      # 留空→默认
    leverage: float | None = Field(default=None, gt=0, le=125)
    takeProfitUsd: float | None = Field(default=None, gt=0)
    stopLossUsd: float | None = Field(default=None, gt=0)
    priming: bool = True
    primingBars: int = Field(default=300, ge=10, le=PAPER_WINDOW_MAX)

class LiveAutoStartOut(BaseModel):
    sessionId: str; runId: int; strategyKind: str
    instId: str; bar: str; budgetUsd: float; leverage: float

class LiveAutoSessionRow(BaseModel):
    sessionId: str; runId: int; state: str
    strategyId: int; strategyName: str; instId: str; bar: str
    budgetUsd: float; leverage: float
    takeProfitUsd: float | None; stopLossUsd: float | None
    positionSide: str; sessionPnlUsd: float; startedTs: int

class LiveAutoStatusOut(BaseModel):
    session: LiveAutoSessionRow | None
    liveAutoTradingEnabled: bool
```

### 5.2 会话内存形态（`live_auto/session.py` / `executor.py`）
```text
LiveExecutor:
  creds: AccountCredentials            # 启动时一次解析、整会话复用（R3，避免每单 DB 读账户）
  account_id: int; settings: Settings  # place_live_order(*, settings) 必需
  session_factory: get_sessionmaker    # 每单 async with sm() as s 开短生命周期 session（R3）
  projected: "flat"|"long"|"short"     # 自管持仓状态机（镜像 SimBroker._Position.side）
  entry_price: float; entry_bar: int   # 计时止盈/反手用
  budget_usd: float; leverage: float
  realized_pnl_usd: float              # 累计已实现（平仓回执累加）
  trades: list[Trade]                  # 已执行真单→Trade（驱动图表 markers）
LiveAutoSession:
  session_id, run_id, strategy, inst_id, bar, account_id
  take_profit_usd|None, stop_loss_usd|None, state, started_ts
  _subscribers: set[asyncio.Queue]     # WS 扇出（同 PaperSession）
```

### 5.3 持久化
- **每笔真单**：经既有 `create_live_order_audit` 写 `live_order_audit`（`db/models.py:179`，含 status `accepted|rejected|error`、clOrdId、ordId、error_code、秒级 UTC），**无需新表**。开仓 clOrdId 前缀 `aragon`、平仓 `aragonc`（既有约定，便于对账区分 bot 开/平）。
- **会话级**：复用 `runs` 表——**复用** `create_run`（已有）建 Run（`config` 标 `{mode:"live-auto", strategyId, budgetUsd, leverage, takeProfitUsd, stopLossUsd}`）；停止时经**新增** `finalize_run`（**R2：当前不存在，须新建** UPDATE 终态/指标的小函数）落终态指标，进「运行历史」可复现。
- **前端**：运行期句柄 `liveAutoSessionId` 等**瞬态不持久化**（与既有 `paperSessionId` 一致，README 迭代3 F3 约定「不持久化 runId/paperSessionId」）。

### 5.4 store 增量（`lib/store.ts`，瞬态）
```ts
liveAutoSessionId: string | null;                 // 运行期句柄
liveAutoStatus: LiveAutoStatus | null;
liveAutoTrades: Trade[];                           // → overlayTrades（图表 markers）
liveAutoPosition: PositionEvent | null;
liveAutoAcknowledged: boolean;                     // 首启风险勾选，会话记忆，刷新复位
```

---

## 六、错误码归一（新增本地码，复用既有 `_map_trade_code` / 错误体）

| 码 | HTTP | 语义 / 中文提示 |
|---|---|---|
| `LIVE_AUTO_TRADING_DISABLED` | 403 | 实时自动交易未启用（设 `LIVE_AUTO_TRADING_ENABLED=true`，且 `LIVE_TRADING_ENABLED=true`） |
| `LIVE_AUTO_PREEXISTING_POSITION` | 409 | 该标的已有持仓，自动交易拒绝启动（守「不动现有持仓」；请先平仓或换无持仓标的） |
| `LIVE_AUTO_SESSION_LIMIT` | 409 | 已有活跃自动交易会话（单会话独占，请先停止） |
| `LIVE_AUTO_BUDGET_EXCEEDS` | 400 | 预算超出可用余额硬上限（`availUsdt × live_auto_max_capital_frac`，默认 5%）；**R4** 前置门禁⑦，亦可选择夹紧而非拒绝 |
| `LIVE_INSTRUMENT_UNSUPPORTED` | 400 | 仅支持 USDT 本位 SWAP/FUTURES（复用既有码） |
| `ACCOUNT_NOT_CONFIGURED` | 400 | 默认实盘账户缺凭证（复用既有码） |
| （下单期）`INSUFFICIENT_BALANCE`/`LEVERAGE_EXCEEDS`/`ORDER_PARAM_INVALID`/`ACCT_MODE_MISMATCH`/`OKX_IP_DENIED`/`LIVE_ORDER_REJECTED_LOCAL`/`LIVE_WOULD_REDUCE_POSITION` | — | 全部复用 `okx/trading._map_trade_code`，以 `error` 事件推 WS，不崩会话 |
| `LIVE_NO_POSITION`（平仓路径） | — | **R8 良性**：交易所侧 OCO 已先平后再发 close 才会出现，视作「已平/flat」处理（归 projected=flat + `status` 事件），**不**计入异常告警 |

---

## 七、Testing & acceptance criteria（测试与验收）

### 7.1 后端单测（离线，pytest，CI 必跑；铁律：一律占位假凭证，严禁写入真实开发凭证）
- `test_live_executor.py`：§2.2 映射全表、张数/名义上限、TP/SL 经 `OrderInput.tp_trigger_px/sl_trigger_px` 多空对称(R6)、`max_hold_bars` 平仓、失败不崩 + projected 一致（至少一条下单异常路径）；**R5**：net_mode 反手在 close 返回后、`get_positions()` 仍报旧仓时**不立即 open**（短轮询/超时跳过），仅确认 `pos==0` 后 open；**R8**：close 收 `LIVE_NO_POSITION` 归 projected=flat 且不作 error。
- `test_live_auto_api.py`：`LIVE_AUTO_TRADING_ENABLED=false`（或 `LIVE_TRADING_ENABLED=false`）→403（先于一切，R1）；既有持仓→409 `LIVE_AUTO_PREEXISTING_POSITION`；二次启动→409 `LIVE_AUTO_SESSION_LIMIT`；**R4**：`capitalUsd` 超 `availUsdt×5%`→夹紧（回执 `budgetUsd` 被夹）或 400 `LIVE_AUTO_BUDGET_EXCEEDS`；start/status/stop 生命周期；stop 调用平仓 + `finalize_run`(R2)。
- `test_live_auto_session.py`：replay/桩行情——priming 不下任何真单；首实时确认根触发开仓（断言调用 `place_live_order` 一次且入参正确）；reconcile 校正 projected；会话级 `stopLossUsd` 触发自动平仓 + 停。

### 7.2 前端单测（vitest，`NODE_ENV=test`，CI 必跑）
- `liveAuto.test.ts`：守门状态判定（缺实盘/缺账户/未启用/未选策略/标的不支持各分支）；预算/止盈止损输入校验；留空→保守默认派生。
- `AutoTradeToggle.test.tsx`：禁用态文案；开→弹窗；确认→`liveAutoApi.start` 入参正确；活跃时 `LiveTradePanel` 人工下单禁用。

### 7.3 真实账户联调（opt-in，手动/网络标记，不进 CI）
按 README「真实数据硬约束 + 代理」前置，使用任务下发的开发测试账号。**联调铁律（守任务硬约束）**：
1. 资金/标的限制：**最多用开发账号资金的 1%**；买卖按 **0.01 手** 在 **BTC**（`BTC-USDT-SWAP`）上测试——即把确认弹窗「预算」设为 ≤1% 可用余额、`live_max_order_notional_usd` 维持 1000、`leverage` 取小（≤3）使单笔张数落在 `minSz` 附近（≈0.01 手量级）。
2. 启动前 `GET /trading/positions` 确认 BTC 标的**当前无持仓**（前置门禁 4 会再校验一次）。
3. 打开「自动交易」开关→确认弹窗留空（或填极小预算）→ 确认；观察：首笔开仓真单成交（`live_order_audit` 落 `accepted`）、图表出现 entry marker、`LivePositionsTable` 显示真实持仓、随单 OCO 止盈止损在 OKX 生效。
4. 触发一次平仓信号或点「停止」→ 自管持仓被市价平、marker 出现 close、持仓归零。
5. 关 `LIVE_AUTO_TRADING_ENABLED` 复跑 start→断言 403。
6. 全程**绝不对已有持仓标的自动交易**；联调结束确认无残留持仓/挂单。

### 7.4 Definition of Done（本设计交付物）
- 本 `spec.md` 已建并见于 `git status`；含 Overview / Technical design / File change plan / Interface design / Data model / Testing & acceptance / Risks 全部小节，篇幅 ≥800 字。
- 文件 / 模块变更计划逐项可被下游工程师按行实现，无需再决策。

---

## 八、Risks & mitigations（风险与缓解）

| 风险 | 影响 | 缓解 |
|---|---|---|
| **无人值守用真实资金误下单（最高优先级）** | 资损 | ① **自动化总闸默认关闭（R1 据实修正）**：`LIVE_AUTO_TRADING_ENABLED` 默认 `false`、为自动下单**真正的默认关闭闸**；`LIVE_TRADING_ENABLED` 现默认 `True`（`config.py:47`），故**不再是「双重」默认关闭**——启动须 `api/live_auto.py` 显式同时校验两闸皆 `true`（§2.1/§2.4①）；运维无人值守可显式置 `LIVE_TRADING_ENABLED=false` 作纵深回退；② **预算上限**：`capitalUsd` 默认仅 availUsdt×1%，**硬上限 5% 由 §2.4⑦ 前置门禁强制夹紧/拒绝（R4，原 v1 仅宣称未落地）**；③ **单笔名义上限** `live_max_order_notional_usd`(1000) 本地拦截；④ **会话级止盈止损**金额阈值自动停；⑤ **单会话独占** cap=1；⑥ 启动确认弹窗逐项复核 + 真实资金风险勾选；⑦ 联调用 0.01 手 BTC、≤1% 资金。 |
| **触碰用户既有持仓**（红线「不能动现有持仓」） | 资损/越权 | 前置门禁 4：启动即校验该标的**无任何既有持仓**（`LIVE_AUTO_PREEXISTING_POSITION`）；bot 只管自开仓；反手走独立 `close_live_position`，net_mode 反向 `place_live_order` 仍被既有 `LIVE_WOULD_REDUCE_POSITION` 拦截。 |
| **per-order 确认被自动化绕过**（偏离人工逐单确认约定 `live-trading-mode` C1-3） | 安全设计争议 | **刻意且有界的偏离**：自动化按定义不能逐单弹窗，故以**单次启动确认弹窗 + 真实资金风险勾选**为知情同意闸，叠加预算（默认 1%、硬上限 5% 强制夹紧 R4）/单笔名义上限 1000/会话级止盈止损/无既有持仓门禁/单会话独占/自动化默认关闭闸 `LIVE_AUTO_TRADING_ENABLED`（R1）等**补偿控制**共同限定风险敞口。**Subtask #1 评审判定**：在 R1（默认关闭闸据实落到 `LIVE_AUTO_TRADING_ENABLED`）与 R4（预算硬上限真正落地）修复后，上述补偿控制对「逐单确认缺失」**构成充分对冲**——风险敞口被默认关闭闸、预算/名义双上限、会话止盈止损与无既有持仓门禁有界封顶，故该偏离**评审通过（列为放行条件，见文末评审结论）**。 |
| **bot 内部状态与真实账户漂移**（OCO 已平/外部干预/强平） | 误判方向、重复下单 | 每根 reconcile `get_positions()` 校正 projected；「projected 非 flat 但真实已平」→ 归 flat 并推 status；下单失败不改 projected、待下次 reconcile。 |
| **下单/网络失败致会话假死或连环重试** | 不可控 | 单笔失败 `error` 事件 + 审计，不崩会话、不自动重试该信号（等下一根）；写链路 `_post` 仅传输层失败重试一次且携唯一 clOrdId，OKX 去重，绝不双重成交（继承既有不变量）。 |
| **市价单滑点 / 名义按滞后 lastPx 估算** | 量级偏差 | 名义上限取保守价（买多 `lastPx·(1+buffer)`）；UI 标注「按最新价估算，含滑点」；预算小 + 杠杆低使单笔量极小。 |
| **人工与 bot 争抢同标的持仓** | 状态冲突 | 自动会话活跃时**禁用人工下单面板**并横幅提示；bot 独占该标的自管仓。 |
| **前端重载致 WS 孤儿** | UI 与真实脱节 | `liveAutoSessionId` 瞬态不持久化；挂载 `GET /live-auto/status` 重发现并重连 WS；后端会话独立于 socket 存活（镜像 paper）。 |
| **会话权益 vs 真实余额口径混淆** | 误读盈亏 | 会话「权益」= 预算 + 累计（已实现+未实现）PnL，明确为**会话视角**；真实账户余额/持仓另由 `LiveBottomPanel` 真源轮询展示，二者并列不混用。 |
| **进程退出残留自动会话/持仓** | 无人管理真实仓 | `main.py` lifespan 关停 `LiveAutoManager.shutdown()`（含平仓）；停止默认平仓退出 + `autoCxl` 撤 OCO。 |
| **密钥泄露** | 安全 | 全程经既有服务层；接口绝不回 secret/passphrase；message 单点 `_redact`；审计表不存密钥。 |

---

## 九、实施顺序建议（供 Subtask #2 参考）

1. 后端纯/编排件先行：`live_auto/executor.py`（§2.2 映射 + TP/SL 经 `OrderInput`(R6) + 计时平仓 + net_mode 反手清算确认(R5) + 失败不崩(含 `LIVE_NO_POSITION` 良性 R8)）+ `test_live_executor.py`。
2. `config.py` live_auto_*（含 `live_auto_max_capital_frac` R4）+ `schemas.py` + `db/repositories.py` **新增 `finalize_run`**(R2)；**不改** `okx/trading.py` 既有写函数（R7，如需仅新增只读 `fetch_session_pnl`）。
3. `live_auto/session.py`（镜像 PaperSession 流水线）+ `manager.py`（前置门禁 + cap=1）+ `api/live_auto.py` + `main.py` 接线 + `test_live_auto_api.py` / `test_live_auto_session.py`。
4. 前端纯件：`lib/liveAutoTypes.ts` / `liveAutoApi.ts` + `liveAuto.test.ts`。
5. UI：`AutoTradeToggle` / `AutoTradeConfirmModal` / `AutoTradeStatusBar` + `StrategyPanel` 接线 + `page.tsx` WS 订阅 + `store` 瞬态 + `LiveTradePanel`/`ChartWorkspace` 接线 + 组件测试。
6. 真实账户 opt-in 联调（§7.3，0.01 手 BTC、≤1% 资金），通过后收尾。

---

> 版本：**v2**（iteration 1 · Subtask #1 方案评审与修复） · 基线：对照工作树实代码逐项核验 v1 全部 `file:line` 引用与复用断言（paper 实时流、实盘写链路、order_builder、repositories、config、前端 ui/store/page/ws/tradingApi/Live*/Chart）。本轮就地修复 5 个 P1（R1–R5）+ 据实修正 P2（R6–R11），结论 **有条件通过**。v1 历史正文整体保留以备审计。

---

## 评审结论（Review Verdict · Subtask #1）

**有条件通过（Approved with conditions）。**

本方案核心设计——把 `PaperSession` 逐根驱动里的 `SimBroker.submit` 替换为 `LiveExecutor`、将策略 `Signal` 翻译为对既有 `okx/trading.py` 服务层的真实下单/平仓，从而继承既有安全护栏与审计；WS 事件协议沿用 paper 同构以零成本复用图表 markers 与成交/持仓表——**经对照工作树实代码逐项核验，可行性成立**（见「评审记录 · 核验证据」：SimBroker 单向单仓语义、反手先平后开、`strength·equity·leverage` 张数公式、`close_live_position`(`auto_cxl=True`)、`validate_order` 名义上限、`is_instrument_supported`、`Switch`/`Modal`/`ReconnectingWS`/`tradingApi`/`Live*Table`/`overlayTrades` 加性等断言全部命中）。规模适配得当（最大化复用既有两条经测链路，订单真源在 OKX，无新表）。

**一致性 / 完备性 / 安全**维度发现并**已在 v2 正文就地修复 5 个 P1**：
- **R1**：纠正「双重默认关闭」误述——`LIVE_TRADING_ENABLED` 现默认 `True`，自动下单**真正的默认关闭闸是 `LIVE_AUTO_TRADING_ENABLED`**；并据实指出 `LIVE_TRADING_ENABLED` 守门在 API 层、自动路径须显式同时校验两闸。
- **R2**：`finalize_run` **当前不存在**，由「复用」更正为「新增最小仓储函数」。
- **R3**：补齐核心调用路径缺失的 `AsyncSession` 获取约定（每单经 `get_sessionmaker()` 开短生命周期 session，镜像 paper）。
- **R4**：把宣称却未落地的「预算硬上限 5%」落到 §2.4⑦ 前置门禁（强制夹紧/拒绝）。
- **R5**：补 net_mode 反手 close→open 竞态的「清算确认」，杜绝 open 被既有反向单护栏误拒。
- P2（R6–R11：调用记法据实、避免改动经审计写链路、`LIVE_NO_POSITION` 良性、toggle 自读全局 store、minSz 处置、弹窗展示 availUsdt）已就地修正或标注。

**放行条件（Subtask #2 实施时须满足，缺一即视为未达 DoD）**：
1. **R1**：`api/live_auto.py` 写类与 `LiveAutoManager` 前置门禁**显式同时校验** `LIVE_AUTO_TRADING_ENABLED && LIVE_TRADING_ENABLED` 皆 `true`；不得依赖「服务层内已强制」。新增 `live_auto_trading_enabled` 默认 `False`。
2. **R3**：`LiveExecutor` 每次下单/平仓经 `get_sessionmaker()` 短生命周期 session 落审计；`creds` 启动时一次解析复用；绝不复用单个长生命周期 session。
3. **R4**：`capitalUsd`（含留空派生默认）一律服务端夹紧到 `≤ availUsdt × live_auto_max_capital_frac`(5%)，回执 `budgetUsd` 为最终生效值。
4. **R5**：net_mode 反手在 `close_live_position` 返回后须 `get_positions()` 确认该 instId `pos==0` 再 open（带超时/重试，超时本轮跳过 open）。
5. **R2**：新增 `finalize_run` 并在 stop / 会话级止盈止损触发时落 Run 终态。
6. **R7（强烈建议）**：**不改动** `okx/trading.py` 既有写函数（保护经审计写链路与稳定错误码/护栏）；如需会话 PnL 仅新增只读辅助或复用 `fetch_live_positions` 的 `upl`。
7. **任务硬约束（联调铁律）**：真实账户联调一律 0.01 手 BTC、≤1% 可用资金、占位假凭证入测、绝不对已有持仓标的自动交易（§7.3）。

**per-order 确认偏离判定**：本功能按定义无法逐单弹窗，偏离 `live-trading-mode` C1-3「每单确认」约定。评审认为，在 R1（默认关闭闸据实）+ R4（预算硬上限落地）修复后，**单次启动确认弹窗 + 真实资金风险勾选 + 自动化默认关闭闸 + 预算/名义双上限 + 会话级止盈止损 + 无既有持仓门禁 + 单会话独占**构成对「逐单确认缺失」的**充分补偿控制**，风险敞口有界可控，故该偏离**通过**（列为上方放行条件共同约束）。

满足上述 7 项放行条件后，本方案可作为 Subtask #2 的实现依据。无 P0；P1 已全部就地修复，**无 P0 / P1 遗留**。

> 版本：**v2**（iteration 1 · Subtask #1 复评：对照工作树核验 v1 全部断言，就地修复 R1–R5 (P1) + R6–R11 (P2)，结论有条件通过） · 评审人：资深评审（senior reviewer） · 维度：可行性 / 完备性 / 一致性 / 规模适配 · 复核基线：工作树实代码逐一回查（附 `file:line`）。

---

## 实施过程发现的方案缺陷（Issues Found During Implementation · Subtask #2）

> 实现节点据实记录与方案的差异与小幅修正（均为更贴合实代码的等价落地，不改变安全契约与放行条件）。

1. **`ChartWorkspace.tsx` 未改（等价上移到 `app/page.tsx`）**：`overlayTrades` 由 `app/page.tsx` 统一计算后下传 `ChartWorkspace`→`CandleChart`（既有 `trades={overlayTrades}` 链路）。故「自动会话活跃时取 `liveAutoTrades`」最自然的落点是 `page.tsx` 的 `overlayTrades` 计算处（`liveAutoSessionId ? liveAutoTrades : …`），`ChartWorkspace` 本就纯透传，无需改动。结果与 §3 一致（图表照常画出买卖 markers）。

2. **守门字段来源据实修正（呼应 R9）**：store 中**并无** `hasReadyDefault` / `liveAutoTradingEnabled` 字段（`AccountModeSwitch` 也是经 `accountApi.accounts.list` 的 react-query 派生 `hasReadyDefault`）。故 `AutoTradeToggle` 仅从 store 订阅 `accountMode`/`strategyId`/`instId`/`bar`/`liveAutoSessionId`，并以 react-query 取 `accounts`（判 `hasReadyDefault`）与 `/live-auto/status`（判 `liveAutoTradingEnabled`）。守门纯逻辑收敛到新增纯件 `lib/liveAuto.ts`（`resolveAutoTradeGate` + 预算派生/夹紧 + 输入校验），由 `lib/liveAuto.test.ts` 单测。

3. **自动交易策略选择 = `store.strategyId`（当前聚焦策略）**：实时模式右栏选中某策略即写 `strategyId`；`AutoTradeToggle` 以其为自动交易标的策略，未选则守门禁用（文案「请先选择一个交易策略」）。符合需求「根据当前用户选中的交易策略」。

4. **`okx/trading.py` 未改（严格遵循 R7）**：会话未实现盈亏直接复用 `fetch_live_positions` 返回的 `upl`，**未**新增 `fetch_session_pnl`，经审计的实盘写链路与稳定错误码零改动。

5. **`desktop/src/lib/config.js` 未改（核验通过）**：env 白名单为 `key.startsWith('LIVE_')`，`LIVE_AUTO_TRADING_ENABLED` 等天然命中透传，无需改动（与 §三 备注一致）。

6. **`AutoTradeStatusBar` 渲染位置**：置于 `StrategyPanel` 标题栏下方，仅 `mode==="paper"` 渲染、无活跃会话时组件自隐（§3 未钉死位置，此处与「自动交易运行中状态条」语义一致）。

> 实现节点验证：后端 `pytest`（`test_live_executor`/`test_live_auto_api`/`test_live_auto_session` 全绿 + 相关回归 54 项绿）；前端 `vitest` 全量 428 项绿（含 `liveAuto.test.ts`/`AutoTradeToggle.test.tsx`）；`tsc --noEmit` 与 `next build` 均通过。未 `git commit`（留待 Subtask #3）。
