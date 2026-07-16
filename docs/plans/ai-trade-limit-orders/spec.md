# AI 自动交易 · 限价委托挂单（AI Trade Limit Orders）设计方案

> Feature slug: `ai-trade-limit-orders`
> 版本：**v2**（Subtask #1 评审后修订；3 个 P0 + 6 个 P1 已就地修复，见 §评审记录 / §评审结论）
> 关联既有特性：`ai-auto-trading`（v2 已上线）、`ai-trade-position-sizing-steps`（已提交 `cc3114b`）、
> `ai-trade-pre-trade-reflection`（`407d274`）、`live-trading-mode`、`live-pending-orders-visualization`（已上线）
> 需求出处：Task Board「Original Task」——「现在的 AI 自动交易功能缺少限价委托的挂单功能和 Agent 能够使用的
> 工具，需要给对应 Agent 进行实际买卖平仓等，添加对应的限价委托的挂单功能和工具，确保其能够进行限价委托进行
> 交易，之后 Agent 可以限价委托，也可以市价委托」
> OKX 参考：<https://www.okx.com/docs-v5/zh/#overview>（`POST /api/v5/trade/order`、`POST /api/v5/trade/cancel-order`、
> `GET /api/v5/trade/order`、`GET /api/v5/trade/orders-pending`）

---

## 评审记录（Review Notes · Subtask #1）

> **评审方式**：不据纸面签名——对 v1 的 11 条 As-built 断言与全部 `file:line` 证据**逐条回源码核对**，
> 另经 ccxt / hummingbot 交叉验证 OKX 撤单错误码语义。
>
> **核对结论（先说 v1 对的部分）**：v1 的事实基线**基本可信**。A-1/A-2/A-3/A-5/A-6/A-7/A-8/A-9/A-10/A-11
> 逐条复核**属实**；§5.3「`AI_TRADE_` 前缀已在桌面透传白名单内」亦**属实**（`desktop/src/lib/config.js:72`
> 确有 `key.startsWith('AI_TRADE_')`）；`close_live_position` 确已支持 `ord_type="limit"` 走 `place_order`
> （`trading.py:231-237`），故「限价平仓复用既有链路」**可行**。v1 的三条核心架构主张——限价能力早已存在、
> 真实缺口在 AI 链路、乐观成交模型是首要危险——**成立且有价值**，本次评审予以保留。
> （唯一笔误：A-4 的 `signals.py:11` 实为 `:23`，不影响结论，已在 §〇 订正。）
>
> **但 v1 在它自己识别出的那类危险上，留了三个同构的洞**：它正确指出「不在挂单列表」对「已成交 / 已撤销」
> 是**二义**的，却在自己的撤单错误码映射里犯了同一个二义错误（P0-2）；它用 I-2 堵死了**开仓**路径的幻影
> 成交，却从未定义**平仓**路径的 `projected` 语义（P0-3）；它列了 8 处契约镜像点并特别标注了会**大声**
> KeyError 的那处，却漏掉了唯一一处会**静默**误执行真实平仓的（P0-1）。三者均已在 v2 就地修复。

| # | 严重度 | 位置 | 问题（一句话） |
|---|---|---|---|
| **P0-1** | **P0** | `ai_auto/signals.py:176-183`（镜像清单缺失） | `decision_to_signals` 末行是**裸兜底 `return [Signal(side="close")]`**——任何未知 action **静默**落成平仓信号。若 `cancel_order` 漏了 session 层前置分发，Agent 说「撤单」会被翻译成**真实市价平掉持仓**。v1 的 M-1..M-11 **未包含此点**，且 §6.1 的 `cancel_order_with_no_resting_is_benign`（flat 场景）**测不出**它（仅在持仓时发作） |
| **P0-2** | **P0** | §4.3 / §4.4 错误码映射 | 撤单-成交竞态。经核实 OKX 语义为 **51400=订单不存在 / 51401=已撤销 / 51402=已成交**；v1 的中文标注**两个都写反**（把 51400 记为「已撤销」、51401 记为「已完成」），且**完全遗漏 51402**，并把「已撤销」与「已完成」合并为单一 `ORDER_ALREADY_SETTLED` 按「良性幂等」丢弃 `resting`。后果：挂单在撤单瞬间成交 → 我方丢弃 resting 却**不采纳成交** → 自管 flat 而交易所有真实持仓；在 §2.5 撤旧挂新路径下更会**在已成交仓位之上再开一单**（双倍敞口）。这正是 v1 自己在 §2.3 批判 `orders-pending` 时点破的同一个二义陷阱 |
| **P0-3** | **P0** | §2.3 I-2 / `executor.py:281` | 限价**平仓**期间 `projected` 语义未定义（I-2 括注仅「开仓挂单场景」），且 `_poll_resting` 与 `_on_reconcile_ok` 的**执行顺序未规定**。平仓挂单成交后 OKX 持仓归零，`_on_reconcile_ok` 的 `projected != "flat" and abs_pos <= 0` 分支即命中 → 合成 `external_close` 幻影成交并按 `mark_px` 捏造盈亏——**R-1 在平仓路径原样复发** |
| **P1-1** | P1 | §2.3「部分成交」/ R-2 | **不存在的兜底**。v1 两处宣称「`abs_pos` 会自然覆盖漂移 / 作最终兜底」，但 `_on_reconcile_ok`（`executor.py:277-290`）**从不**用 `abs_pos` 覆写 `entry_qty_sz`，也**没有** `projected=="flat" and abs_pos>0` 的采纳分支——`abs_pos` 仅用于判定归 flat。该兜底纯属纸面，`_poll_resting` 实为成交侦测的**唯一**路径 |
| **P1-2** | P1 | §6.1 / §三 | **测试注入缝缺失**。§6.1 称单测「全部走既有注入缝 `place_order_fn`/`close_position_fn`/`positions_fn`」，但 `cancel_resting` / `_poll_resting` 依赖的 `cancel_live_order` / `fetch_live_order **无任何注入缝**（`executor.py:78-80` 只有三个），§三 亦未列。照 v1 写，`test_live_executor_limit.py` 全组**无法脱网运行** |
| **P1-3** | P1 | M-2 / §6.1 零回归门禁 | **既有测试必破 + 门禁空转**。`test_ai_trade_position_step.py:132` 有**恒等断言** `build_decision_tool_schema(0.0) is DECISION_TOOL_SCHEMA`；`allow_limit` 若默认 `True` 即破。更糟：§6.1 的 `test_schema_without_limit_flag_identical`「与今日 `DECISION_TOOL_SCHEMA` 深度相等」——若实现就地改坏了模块常量，该测试是**拿改坏的常量和它自己比**，恒真、**测不出任何东西** |
| **P1-4** | P1 | §2.9 启动前置校验 | **误拒启动**。`fetch_live_open_orders` 会把 OCO 策略委托一并归一返回（`trading.py:386-387`，`kind="tp"`/`"sl"`）。既有随单止盈止损、或残留 OCO 都会使其**非空** → 正常启动被 `AI_TRADE_PENDING_ORDER_EXISTS` 409 拒。必须按 `kind` 过滤 |
| **P1-5** | P1 | §2.1 图 vs §2.6 / R-5 | **价带门禁归属自相矛盾**。§2.1 把「价带校验」画在 `order_plan.py`，§2.6/R-5 却要求它降级 hold 且 reason 进 `_RETRYABLE_DEGRADE_REASONS`。而重试循环在 `decider.decide`（`decider.py:190-197`）内、**只认 `_map_decision` 返回的 degrade**——门禁若落 `order_plan.py`，§2.6 承诺的「可重试」**静默失效** |
| **P1-6** | P1 | §2.1 `apply(..., order_plan=)` | **限价 + 反手未定义**。Agent 持空仓时给 `open_long` 会经 `_project_signals` 产出**两腿** `[close(reverse), open]`（`executor.py:156-159`），而 `order_plan` 只有一个——绑哪条腿未规定。若两腿都限价：close 腿挂起 → `_confirm_net_cleared` 失败 → open 腿被跳过，且 `self.resting` 被 close 腿占用，会话陷入半反手态 |
| P2-1 | P2 | §2.1 / §三 | `tick_sz` 注入与 `snap_price_directional` 必要性存疑（`build_order_payload` 已用权威 `meta.tick_sz` 二次对齐）；它是**唯一**触碰共享 `live_auto/manager.py` 的改动，建议权衡血溅半径 |
| P2-2 | P2 | §5.3 | `ai_trade_limit_orders_enabled` 默认 `True` ⇒ 合入即对全部实盘会话生效 |
| P2-3 | P2 | §2.5 | 撤旧挂新缺**幂等**：同向同价的重复 `open` 决策会每轮撤挂、白丢排队优先级 |
| P2-4 | P2 | M-11 | 镜像点仍漏 `apps/web/lib/aiTrade.ts:424` `describeTrigger` 内硬编码的动作数组 |
| P2-5 | P2 | §4.4 | `51503` → `ORDER_NOT_FOUND` 归因存疑：撤单「订单不存在」的真实码是 `51400` |
| P2-6 | P2 | §4.1 | schema 示例把 `sizeFraction` 写成固定四档 `enum`，与 `step<=0` 时的连续 schema（`{"type":"number","minimum":0,"maximum":1}`）不符 |

**修复说明**：P0-1..P0-3 与 P1-1..P1-6 已**全部就地修复**于 v2 正文（分别落在 §2.2 M-12/M-13、§2.3、§2.5、
§2.6、§2.9、§三、§4.3、§4.4、§6.1、§七）。P2 项按「记录不强制」处理，其中 P2-1 / P2-2 需 Subtask #2 拍板，
已在 §八 列为待决。

---

## 〇、As-built 对齐（Reconciliation · 本方案的事实基线）

> 沿用 `ai-auto-trading-enhancements` v3 的房规（该 spec §2.0）：**本方案以 as-built 代码为准，不据历史 spec
> 纸面签名**。下表是本次动工前经并行代码勘察逐条核对的结论（非纸面推断），评审节点请以此为基线。

| # | 命题 | 核对结论 | 证据（file:line） |
|---|---|---|---|
| A-1 | 「项目不支持限价委托」 | **证伪**。手工/API 链路早已端到端支持限价 | `order_builder.py:44` `ord_type: limit\|market`；`:145-146` 限价写 `px`；`schemas.py:1146` `PlaceOrderIn.ordType: Literal["limit","market"]`；`api/trading.py:152` `POST /trading/order` |
| A-2 | 真实缺口所在 | **AI / 自动化链路**：`LiveExecutor` 三处硬编码 `ord_type="market"` | `live_auto/executor.py:182-184`（开）、`:208-210`（平）、`:322-324`（flatten） |
| A-3 | 决策契约无价格 | 属实。`submit_trade_decision` 仅 4 字段，无价格/订单类型 | `prompts.py:17-26`；`decider.py:88-95` `TradeDecision` |
| A-4 | `Signal` 无价格字段 | 属实——**管道最窄处** | `strategies/base.py:17-25`；`signals.py:164-183` `decision_to_signals` |
| A-5 | 撤单能力 | **完全不存在**。全仓库无 `/trade/cancel-order`、`/trade/amend-order`、`GET /trade/order` | `trade_client.py` 仅 `place_order:60` / `close_position:77` / `set_leverage:100` / `get_fills:118` / `get_pending_orders:123` / `get_algo_pending_orders:130` |
| A-6 | 挂单读回 | **已存在**（`live-pending-orders-visualization` 已上线） | `trading.py:366` `fetch_live_open_orders`；`:404` `_classify_pending_kind`（已分 `limit_open`/`limit_close`）；`api/trading.py:130` `GET /trading/open-orders` |
| A-7 | 审计表是否需迁移 | **不需要**。`live_order_audit.ord_type` 已 `String(8)`、`px` 已 nullable Float | `db/models.py:201-219` |
| A-8 | 执行器是否拿得到 `tickSz` | **拿不到**。仅注入 `ct_val/lot_sz/min_sz` | `executor.py:51`；装配处 `live_auto/manager.py:506-507`、`ai_auto/manager.py:532-533` |
| A-9 | `LiveExecutor` 复用范围 | `live_auto` 与 `ai_auto` **共用同一个类**，改动同时命中两条链路 | `ai_auto/manager.py:33` import、`:527-533` 构造 |
| A-10 | SimBroker 是否支持限价 | **不支持**。`_Order` 无价格字段，`_pending` 是「次根开盘无条件成交」的一根队列 | `engine/simulator.py:73-78`、`:156-167` |
| A-11 | ai_auto 是否有 paper 模式 | **无**。ai_auto 只有实盘；SimBroker 仅用于影子读立场与回测 | `ai_auto/manager.py:527`；`signals.py:23`（v2 订正：v1 误记为 `:11`，该行是文档字符串） |
| **A-12** | `decision_to_signals` 对未知 action 的处置 | **裸兜底落 `close` 信号**（非报错）——P0-1 的根因 | `signals.py:176-183`：末行 `return [Signal(side="close", …)]` 无 action 白名单 |
| **A-13** | 撤单错误码语义（经 ccxt/hummingbot 交叉验证） | `51400`=订单不存在、`51401`=已撤销、**`51402`=已成交**（v1 三者均误）| OKX V5 error code；P0-2 的根因 |
| **A-14** | `_on_reconcile_ok` 是否以 `abs_pos` 校正张数 | **否**。仅用于判归 flat，从不覆写 `entry_qty_sz`；无「flat 但有仓」分支 | `executor.py:277-290`；P1-1 的根因 |
| **A-15** | executor 的可注入缝 | **仅 3 个**：`place_order_fn` / `close_position_fn` / `positions_fn`（无 cancel / get） | `executor.py:78-80`；P1-2 的根因 |

**一句话结论**：本特性**不是**「从零实现限价单」，而是「把早已存在、已被手工链路验证过的限价能力，安全地接到
AI 决策链路上，并补齐它缺失的那一半——**挂单生命周期（撤单 / 成交侦测 / 超时）**」。工作量的重心在生命周期与
安全护栏，不在下单本身。

---

## 一、Overview（概述）

当前「AI 自动交易」中，Agent 每轮经 `submit_trade_decision` 只能表达**意图**（`open_long` / `open_short` /
`close` / `hold`）与**仓位占比**，真实下单由 `LiveExecutor` 落地，而 `LiveExecutor` 把订单类型写死为
`ord_type="market"`（`executor.py:182-184`）。这意味着 Agent 无论研判出多精细的入场点位，最终都只能以**市价**
成交——吃掉全部买卖价差与滑点，且在剧烈行情中成交价完全不可控。与此同时，项目在手工交易链路上早已具备完整
的限价能力（`order_builder.py:145-146` 的 `px` 组装、`tickSz` 对齐、`validate_order` 的限价校验），也早已能读回
未结挂单（`GET /trading/open-orders`）。**能力是现成的，只是 AI 用不到。**

本方案给 Agent 补齐两样东西：一是**表达能力**——决策工具 schema 增加 `orderType`（`market` | `limit`）与
`limitPrice`，并新增第五个动作 `cancel_order` 用于撤回自己挂出的未成交单；二是**执行与治理能力**——在
`LiveExecutor` 中引入一个**挂单生命周期状态机**（resting → filled / canceled / expired），配套新增
`POST /api/v5/trade/cancel-order` 与 `GET /api/v5/trade/order` 两个 OKX 客户端方法，并把「当前挂单」作为只读
上下文注入每轮决策 prompt，使 Agent 能在下一轮看见自己的挂单、判断是否撤单或改价（撤单重挂）。

必须先讲清一个**架构级张力**，它是本方案全部复杂度的来源：现有执行器是**乐观成交模型**——`_do_open` 发出
市价单后**立即**假定已成交，写死 `self.projected = side`、`self.entry_price = ref_px`（`executor.py:189-191`）。
限价单**可能不成交**，这个假设随之破裂。更危险的是反向：盯市校正 `_on_reconcile_ok` 每 5 秒
（`live_auto_poll_interval_s`，`config.py:80`）比对「自管立场 vs 交易所真实持仓」，一旦 `projected != flat` 而
`abs_pos <= 0`，就会**合成一条 `external_close` 假成交并捏造盈亏**后归 flat（`executor.py:281-289`）。若限价单
天真地沿用乐观模型，一笔挂着没成交的单会在 5 秒内被记为「已开仓又被外部平仓」的幻影交易，污染成交流水、
会话盈亏与统计看板。**这是整个改动中最锋利的一处危险**，本方案 §2.3 用「resting 不写 projected + reconcile
显式感知挂单」正面处理它。

设计上恪守本仓既有的三条纪律：（1）**不新开下单通道**——限价单仍走 `LiveExecutor → okx/trading.py`，完整继承
`LIVE_TRADING_ENABLED` 双闸、单笔名义上限、唯一 `clOrdId`、`live_order_audit` 留痕与脱敏（`ai-auto-trading`
spec 第 6 行「关联红线」）；（2）**默认等价可回退**——总闸 `AI_TRADE_LIMIT_ORDERS_ENABLED=false` 时 schema
不露出新字段、`_ACTIONS` 不含 `cancel_order`、`order_plan` 恒为 `None`，行为**逐字回退**到今天的 market-only；
（3）**新逻辑落新模块**——`ai_auto/session.py` 已 932 行、`AiTradeLaunchModal.tsx` 已 924 行，双双越过
CLAUDE.md 的 800 行红线（最新 spec 记为 RV-2），故本方案把生命周期与纯映射沉到两个**新建**模块，严格控制既有
大文件的净增行数。

---

## 二、Technical design（技术设计）

### 2.1 分层与新增件

```
LLM ──submit_trade_decision(action, sizeFraction, orderType, limitPrice, reason)
      │
      ▼
 decider._map_decision            ← ✎ 校验 orderType/limitPrice + **价带门禁（必须在此，P1-5）** + 新动作
      │  TradeDecision(+ord_type, +limit_px)   ← 门禁不过 = 降级 hold + 可重试 reason
      ▼
 ai_auto/order_plan.py            ← ✚ 新建：TradeDecision → OrderPlan（**纯映射，不含任何门禁**）
      │  OrderPlan(ord_type, px) | None
      ▼
 session._execute_decision        ← ✎ 透传 order_plan / 分发 cancel_order
      │
      ▼
 LiveExecutor.apply(signals, *, order_plan=None)   ← ✎ 默认 None ⇒ market ⇒ live_auto 零回归
      ├── _do_open   ← ✎ 限价：下单后进 resting，**不写 projected**
      ├── _do_close  ← ✎ 限价平仓（复用既有 ClosePositionInput(ord_type="limit")）
      ├── cancel_resting  ← ✚ 新方法
      └── _poll_resting   ← ✚ 新方法（reconcile 内调用，成交/撤销/超时收敛）
      │
      ▼
 live_auto/resting.py             ← ✚ 新建：RestingOrder 状态机（纯函数，易单测）
      │
      ▼
 okx/trading.py                   ← ✎ cancel_live_order / fetch_live_order / 错误码 / clOrdId 归一
      ▼
 okx/trade_client.py              ← ✎ cancel_order / get_order（复用 _post / _get 与双层码校验）
      ▼
 OKX V5: /trade/order · /trade/cancel-order · /trade/order(GET)
```

**为何 `OrderPlan` 单独一条通路、而不是给 `Signal` 加价格字段**：`strategies/base.Signal`（`base.py:17-25`）被全部
5 个内置策略、`SimBroker`、回测、纸面模拟共用。给它加 `px` 会把「交易所委托价」这个执行层概念漏进策略信号词汇
表，且 `SimBroker` 会**静默忽略**该字段——导致 AI 回测「每单必成」而实盘「可能不成」，产生系统性高估。故限价意图
走 `LiveExecutor.apply(..., order_plan=...)` 独立入参，`order_plan=None` 即 market，`live_auto` 调用点一字不改。

### 2.2 决策契约扩展（Agent 的「工具」）

`submit_trade_decision` 增 2 字段、`action` 增 1 枚举值：

| 字段 | 类型 | 必填 | 语义 |
|---|---|---|---|
| `action` | enum | 是 | `open_long` / `open_short` / `close` / `hold` / **`cancel_order`**（新） |
| `sizeFraction` | number/enum | open_* 必填 | 不变（仍受 `position_ladder` 档位吸附） |
| **`orderType`** | enum `market`\|`limit` | 否，缺省 `market` | 仅对 `open_*` / `close` 有意义 |
| **`limitPrice`** | number > 0 | `orderType=="limit"` 时必填 | 委托价（USDT）；由系统按 `tickSz` 对齐 |
| `confidence` | number | 否 | 不变 |
| `reason` | string | 是 | 不变 |

`cancel_order` 语义：撤回本会话当前挂着的未成交委托，不开不平。无挂单时为**良性 no-op**（回 status 事件，不报错）。

**为何新增动作而非 `hold` 加布尔旗标**：Agent 必须能主动撤回一笔「市场已走反、再挂无意义」的单。用 `hold` +
`cancelResting: true` 会让 hold 同时承担「什么都不做」与「撤单」两种语义，破坏 `_ACTION_KEYS` 统计口径与
`hold` 的兜底地位（全部降级路径都回落 `hold`——若 hold 可能带副作用，降级就不再安全）。故独立动作。

**契约镜像点（务必同步，否则运行期 KeyError / 静默误执行）**——经 v2 复核，本仓动作枚举实际被抄写在
**13 处无任何共享常量关联**的字面量里（v1 记为「8 处」并漏了最危险的 M-12）。按**失败模式**分级看，比按
位置看更有用：

- **静默误执行（最危险）**：M-12 —— 不报错，直接下真单。
- **大声 KeyError**：M-6 —— 当轮即炸，反而安全。
- **静默丢统计 / 丢字段**：M-3 / M-7 / M-8 / M-9。
- **编译期兜住**：M-11（TS 联合类型改动会强制 `ACTION_LABELS` 补齐，Python 侧无此保护）。


| # | 位置 | 处置 |
|---|---|---|
| M-1 | `prompts.py:17-26` `DECISION_TOOL_SCHEMA` | ✎ 加 `orderType`/`limitPrice`；enum 加 `cancel_order` |
| M-2 | `prompts.py:84-99` `build_decision_tool_schema` | ✎ 增 `allow_limit` 形参；`False` 时**返回不含新字段的 schema** |
| M-3 | `decider.py:53` `_ACTIONS` | ✎ 加 `"cancel_order"`（与 M-1 是重复真源，必须同改） |
| M-4 | `prompts.py:28-33` `DECISION_TOOL_DESCRIPTION` | ✎ 散文里补新动作与限价说明 |
| M-5 | `prompts.py:66-67` 纪律 #4 | ✎ 同上 |
| M-6 | `session.py:631` `{"open_long":"long",...}[action]` | ✎ **裸下标，新动作必 KeyError**——改 `.get()` + 显式分支 |
| M-7 | `session.py:864` `_ACTION_KEYS` / `session.py:68` `_ACTION_LABEL` | ✎ 统计与中文标签补 `cancel_order`=「撤单」 |
| M-8 | `backtest.py:185` `action_counts` | ✎ 同上（回测虽禁限价，动作字典仍须容纳） |
| M-9 | `decider.py:415-416` journal `decision_input` | ✎ 补 `orderType`/`limitPrice`，否则滚动摘要丢失挂单史 |
| M-10 | `schemas.py:385-402` `AiTradeDecisionOut` | ✎ 补新字段 |
| M-11 | `apps/web/lib/aiTradeTypes.ts:5` `AiTradeAction` | ✎ 联合类型补 `"cancel_order"`（改后 `aiTrade.ts:130` `ACTION_LABELS` 会**编译期报错**逼你补中文标签，是唯一有编译保护的镜像点） |
| **M-12** | **`ai_auto/signals.py:176-183` `decision_to_signals`** | ✎ **【P0-1，v1 遗漏】必改**。末行是裸兜底 `return [Signal(side="close", …)]`——`cancel_order` 落到这里会被**静默**翻译成真实市价平仓。改为**显式 action 白名单**：`open_long`/`open_short` → 开仓信号，`close` → 平仓信号，`hold` **及 `cancel_order`** → `return []`，其余未知 action 一律 `return []`（**绝不**兜底成 close）。见 §2.2a |
| **M-13** | `apps/web/lib/aiTrade.ts:424` `describeTrigger` | ✎ 【P2-4】内含第 2 处硬编码动作数组 `["open_long","open_short","close"]`（用于触发原因统计）；`cancel_order` 不应计入「有效交易动作」，确认后按需补 |

### 2.2a `cancel_order` 的双层防线（P0-1 修复 · 必读）

`cancel_order` 是本特性唯一的**新动作**，而本仓对未知 action 的既有处置是**不一致**的：`session.py:631`
会**报错**，`signals.py:183` 会**静默落 close 信号**。后者意味着——如果 session 层忘了在调用
`decision_to_signals` 之前把 `cancel_order` 分流走，Agent 说「撤掉我那笔没成交的挂单」会被系统翻译成
**「市价平掉真实持仓」**。不抛异常、不留错误、盈亏照记，事后只能靠人肉看流水才发现。这是本特性
**风险最高的单点**，故用两层独立防线，任一层单独成立即可阻断：

- **第一层（分发）**：`session._execute_decision` 在进入信号翻译**之前**先判 `action == "cancel_order"`
  → 直接 `await executor.cancel_resting("agent")` 并 return，**根本不调用** `decision_to_signals`。
- **第二层（白名单兜底，M-12）**：`decision_to_signals` 改为**显式白名单**，未知 action 一律 `return []`
  （空信号 = 什么都不做），**删除 close 裸兜底**。即便第一层被后续重构绕过，最坏结果也只是「无事发生」，
  而不是「误平真仓」。

> 纪律：**兜底的方向必须是「不动」，不能是「动」**。`hold` 之所以能当全部降级路径的落点，正因为它无副作用；
> 一个会下真单的兜底分支不配做兜底。这条同样是 §2.2「为何新增动作而非 `hold` 加布尔旗标」的延伸理由。

守门测试（§6.1）：`cancel_order_never_closes_open_position`——**持有真实多仓**时喂 `cancel_order`，断言
`_close_position` 桩**零调用**且 `projected` 仍为 `long`。注意 v1 原列的
`cancel_order_with_no_resting_is_benign` 是 **flat** 场景，**测不出**本缺陷，两者都要有。

### 2.3 挂单生命周期状态机（本方案的核心）

新建 `live_auto/resting.py`，承载**至多一笔**挂单的状态（会话级不变式，见下）：

```python
@dataclass(frozen=True)
class RestingOrder:
    cl_ord_id: str
    ord_id: str
    intent: str          # "open" | "close"
    side: str            # open: long|short；close: 被平方向
    px: float            # 已按 tickSz 对齐的委托价
    sz: float            # 已按 lotSz 对齐的张数
    strength: float      # open 用（回填 entry_position_pct）
    reason: str
    created_ts: int
    expire_ts: int       # created_ts + ttl_s
    adopted_sz: float = 0.0   # v2：已采纳的成交张数；部分成交按增量采纳，防重复计入（R-19）
```

> `frozen=True` 下推进 `adopted_sz` 用 `dataclasses.replace(self.resting, adopted_sz=...)` 生成新值对象，
> 不要改成可变 dataclass——不可变性正是它易测的来源。

`LiveExecutor` 新增字段 `self.resting: RestingOrder | None = None`。

**不变式 I-1：单会话至多一笔挂单。** 理由——会话本就是「单标的、单仓位」模型（`projected` 是标量），允许多笔
挂单会把状态炸成订单簿，且预算/敞口核算需按笔预留。新的 `open` 决策撞上已有挂单 → **撤旧挂新**（cancel-replace，
§2.5），而非并存。

**不变式 I-2（v2 拆分修复 P0-3）：resting 期间 `projected` 一律保持「挂单前的值」，绝不乐观置位。**
v1 只写了开仓场景并括注「（开仓挂单场景）」，把平仓路径留成了空白——而平仓路径恰恰会让 R-1 原样复发。
故 v2 把 I-2 显式拆成对称的两条：

| | 场景 | resting 期间 `projected` | 成交后由谁置位 | 若不这样会怎样 |
|---|---|---|---|---|
| **I-2a** | 开仓挂单（`intent="open"`） | 保持 **`flat`** | `_adopt_fill` 置为 `side` | 乐观置位 → `projected!="flat" and abs_pos<=0` 命中 → 幻影 `external_close`（R-1） |
| **I-2b** | 平仓挂单（`intent="close"`） | **保持原方向**（`long`/`short`），**不**提前归 flat | `_adopt_close_fill` 走 `_settle_close` 归 flat | 提前归 flat → 真仓变「不可见」，且 `flatten` 早退会漏平（比 R-1 更糟） |

I-2a 直接绕开幻影陷阱：`_on_reconcile_ok` 的假平仓分支条件是 `projected != "flat" and abs_pos <= 0`
（`executor.py:281`），`projected` 既仍为 `flat`，该分支**根本不会触发**。

**但 I-2b 会正面撞上它**：平仓挂单期间 `projected` 仍是 `long`/`short`（真仓确实还在），一旦挂单成交、
OKX 持仓归零，下一次 `_on_reconcile_ok` 就会命中该分支，把这笔**我方主动的限价平仓**记成
「外部干预平仓」并按 `mark_px` **捏造盈亏**（真实成交价 `avgPx` 被丢弃）。故 v2 追加两条硬约束：

**约束 O-1（执行顺序，必须）**：`_poll_resting()` 必须在 `reconcile()` 内、**先于** `_read_positions()` 与
`_on_reconcile_ok()` 执行。这样同一轮里成交总是先被 `_adopt_close_fill` 正确采纳（`projected` 已归 flat），
`_on_reconcile_ok` 随后看到 `projected=="flat"` 便不会再合成幻影。因 `reconcile()` 是全部 6 处调用点
（`ai_auto/session.py:293,604`、`live_auto/session.py:246,322` 等）的**唯一收口**，在此处收口即全覆盖。

**约束 O-2（分支抑制，兜底）**：`_on_reconcile_ok` 的 `external_close` 分支追加前置条件
`self.resting is None or self.resting.intent != "close"`。即便 O-1 因异常/时序被绕过（如 `_poll_resting`
本轮读取失败），也**只是延后一轮采纳**，绝不误记幻影。O-1 保正确、O-2 保安全，两者都要。

> ⚠️ `_poll_resting` 的异常必须**自行隔离**（`try/except OKXError` 就地吞并记 status），**不可**外泄进
> `reconcile()` 既有的 `except OKXError` —— 否则一次 `get_order` 失败会被误归因为「持仓读取失败」，
> 污染 `_reconcile_fail_streak` 并触发假降级（`executor.py:271-275/300-312`）。

**成交侦测（新增，必须补）**：I-2a 的代价是——开仓挂单**成交**后 `projected` 仍是 `flat`，而现有
`_on_reconcile_ok` 只处理「自管有仓、交易所无仓」，**没有**处理反向的「自管无仓、交易所有仓」，故成交会
**不可见**。因此 `reconcile` 内新增 `_poll_resting()`：

```
_poll_resting()   # 仅当 self.resting is not None；在 reconcile 内最先执行（O-1）；异常自隔离
  ├─ state = await fetch_live_order(inst_id, cl_ord_id)   # GET /trade/order
  ├─ "live"             → 未成交；now >= expire_ts ? _cancel_resting("expired") : no-op
  ├─ "partially_filled" → 按 accFillSz 采纳（intent 分派，见下），继续挂剩余
  ├─ "filled"           → intent=="open"  ? _adopt_fill(avgPx, accFillSz)
  │                     → intent=="close" ? _adopt_close_fill(avgPx, accFillSz)
  │                       两者均 resting=None + 推 trade 事件
  └─ "canceled"|"mmp_canceled" → resting=None + status 事件（外部/我方撤单皆收敛）

_adopt_fill(avgPx, sz)        # intent="open"：projected=side、entry_price=avgPx（真实成交价，非 ref_px）、
                              # entry_bar=当前根、entry_qty_sz=sz、entry_position_pct 回填 → 合成开仓 trade
_adopt_close_fill(avgPx, sz)  # intent="close"：**复用既有 `_settle_close(ref_px=avgPx, …)`**——它已经把
                              # realized_pnl / projected=flat / entry 复位 / trade 事件一次做全（executor.py:221-237）。
                              # 关键：ref_px 传 **avgPx**（真实成交价）而非 mark_px，盈亏才是真的。
                              # 绝不新写一份平仓结算逻辑——那会立刻产生第二个盈亏口径真源。
```

> `_adopt_close_fill` 复用 `_settle_close` 而非另写，是刻意的：`_settle_close` 已封装了「先算 pnl/pnlPct
> 再复位 entry」这个**顺序敏感**的既有约束（`executor.py:223-231`，v2 的 P1-2 修复过一次）。另起炉灶必然
> 重蹈覆辙。

**为何用 `GET /trade/order` 而非 `orders-pending`**：后者只能回答「还挂着吗」，「不在挂单列表」对
「已成交」与「已撤销」是**二义**的——按它归因会把撤单记成成交。`GET /trade/order` 直接给权威 `state` 与
`avgPx` / `accFillSz`，无歧义。（`orders-pending` 仍保留给 UI 的只读展示与启动前置校验。）

**顺带修正一处既有失真**：`entry_price` 今天取 `ref_px`（K 线收盘价，`executor.py:190`），本就不是真实成交价。
限价路径改用 OKX 回报的 `avgPx`，是**更准**的口径。为控制回归半径，本方案**不**改市价路径的 `entry_price`
取值（仍 `ref_px`），仅在限价路径用 `avgPx`——差异在 §七 R-6 记录并留给后续特性统一。

**部分成交**：`partially_filled` 时以 `accFillSz` 采纳持仓（`projected` 置位、`entry_qty_sz=accFillSz`），剩余
继续挂到 TTL；TTL 到则撤剩余并保留已成交部分。为避免同一笔单被重复采纳，`RestingOrder` 增
`adopted_sz: float`（已采纳张数），每轮只按**增量** `accFillSz - adopted_sz` 追加，`adopted_sz` 随之推进
（`RestingOrder` 是 `frozen=True`，用 `dataclasses.replace` 生成新值对象）。

> ⚠️ **v2 更正一处失实主张（P1-1）**：v1 在此处与 R-2 两次宣称「持仓张数的最终可信源仍是 OKX 持仓——
> `abs_pos` 会自然覆盖漂移 / 作最终兜底」。**该兜底并不存在**：`_on_reconcile_ok`（`executor.py:277-290`）
> 只把 `abs_pos` 用于「是否归 flat」的判定，**从不**把它写回 `entry_qty_sz`，也**没有**「自管 flat 但交易所
> 有仓」的采纳分支。故必须诚实记为：**`_poll_resting` 是成交侦测的唯一路径，没有第二道网。**
>
> **为何 v2 不去补那道网**：给 `_on_reconcile_ok` 加「flat 但 `abs_pos>0` 就采纳」的分支，会**同时**改变
> 共享该执行器的 `live_auto` 行为（A-9），把「用户手工开的仓」也误接管进自动会话——这是比本特性大得多的
> 语义变更，且与既有「绝不触碰非自管持仓」红线（`executor.py:315` flatten 文档字符串）直接冲突。
> 正确的取舍是**缩小承诺**而非扩大改动：`_poll_resting` 失败仅致「延后一轮采纳」（下轮重试即收敛），
> 真正的残余风险只有「会话在挂单成交后立刻异常退出」，由 §2.9 启动前置校验兜住。残余风险记入 R-2。

### 2.4 超时治理（TTL）

每笔挂单带 `expire_ts = created_ts + ai_trade_limit_order_ttl_s`（默认 300s）。到期由 `_poll_resting` 撤单并推
status 事件 + trace。**为何必须有 TTL**：Agent 唤醒是离散的（确认 K 线 / 墙钟 `wake_interval` 默认 2 分钟），
一笔永不成交的挂单会长期占用保证金与会话预算，且若会话在挂单期间因异常退出，就成了交易所侧的孤儿单。TTL 是
兜底，Agent 的 `cancel_order` 是主动手段，二者互补。

### 2.5 撤旧挂新（cancel-replace）

无 `POST /trade/amend-order`（A-5），且改价语义（改价是否丢失排队优先级、部分成交后能否改）复杂度高，故本期
**不做 amend**，统一用「撤单 + 重下」：`_do_open` / `_do_close` 入口若发现 `self.resting is not None`，先
`await self._cancel_resting("replace")` 且**确认撤单成功**后再下新单。撤单失败 → 不下新单、推 error 事件、保留
旧挂单（绝不制造双挂）。

**撤单三态，不是两态（P0-2 修复 · 本节最锋利处）**。v1 写「确认撤单成功后再下新单」，隐含假设撤单只有
成功 / 失败两种结局。真实的第三态是——**撤单请求发出的同时，那笔挂单成交了**（`51402`）。此时：

| 撤单结局 | 语义 | resting 处置 | 可否下新单 |
|---|---|---|---|
| 成功 / `51401`（已撤销） | 单子没了、**无持仓** | `resting=None` | ✅ 可 |
| **`51402`（已成交）** | **单子成了、有真实持仓** | **必须回查 `get_order` 并 `_adopt_fill`／`_adopt_close_fill`** | ❌ **绝不可** |
| `51400`（订单不存在）/ 其他 | 状态未知 | 保留 resting，下轮 `_poll_resting` 再判 | ❌ 不可 |

若把 `51402` 当成「良性幂等」直接 `resting=None` 然后照常下新单（= v1 的字面写法），结果是
**在一笔已经成交的真实仓位之上再开一笔**——敞口翻倍、预算失控，且自管 `projected` 还以为自己是 flat。
这是本方案中**唯一能凭一个错误码把风险放大一倍**的路径，故：

- `_cancel_resting(reason)` 的返回值必须是**三态枚举** `"canceled" | "filled" | "unknown"`，**不是 bool**。
- 调用方（cancel-replace / TTL / flatten / Agent 主动撤单）**一律**只在 `"canceled"` 时才继续原计划；
  收到 `"filled"` 必须先采纳成交、推 trade 事件，然后**放弃本轮的新单**（下一轮 Agent 会看到真实持仓再决策）。

**幂等：同向同价不重挂（P2-3）**。若新 `open` 决策与 `self.resting` 的 `(side, intent)` 相同且委托价按
`tickSz` 对齐后**逐 tick 相等**，则直接 no-op 保留旧单——否则 Agent 每轮重复同一判断都会撤挂一次，
白白丢掉排队优先级（maker 单的价值恰在排队）。

**限价 + 反手的绑定规则（P1-6 修复）**。`_project_signals` 对「持空仓时 `open_long`」会产出**两腿**
`[close(reverse), open]`（`executor.py:156-159`），而 `order_plan` 只有一个。v2 明确：

> **`order_plan` 只绑定终态腿（the terminal leg）**——即 `_project_signals` 产出的**最后一个**动作。
> 反手的 close 腿**一律强制 `market`**，open 腿才用 Agent 给的限价。

理由：反手的 close 腿是「为了开新仓而必须先清掉的障碍」，不是 Agent 的价格意图所在；且若 close 腿挂起，
`_confirm_net_cleared`（`executor.py:179-180`、`:253-262`）必然失败 → open 腿被跳过 → 会话陷入
「一笔挂着的平仓单 + 没开成的新仓」的半反手态，还占掉了 I-1 的唯一挂单额度。守门测试
`reverse_forces_market_on_close_leg`。

### 2.6 价带门禁（安全护栏）

LLM 完全可能给出离谱的 `limitPrice`（幻觉，如 BTC 报 60000 却挂 6000）。挂在深处的买单短期无害，但一旦行情
真的下穿就会以远坏于研判的位置成交；反向的高价买单则等同市价单且**绕过**了名义上限的保守缓冲
（`trading.py:40` `_MARKET_NOTIONAL_BUFFER=0.02`）。故做**本地价带校验**：

```
dev = abs(limit_px - last_px) / last_px
dev > ai_trade_limit_price_max_deviation_pct (默认 0.02)  →  降级 hold，reason="limit_price_out_of_band"
```

**门禁必须落在 `_map_decision` 内，不能落在 `order_plan.py`（P1-5 修复）**。v1 §2.1 的分层图把「价带校验」
画在 `order_plan.py`，与本节要求的「降级 hold + 可重试」**自相矛盾**：重试循环在 `decider.decide`
（`decider.py:190-197`）里，它 `while degrade is not None and _is_retryable_degrade(degrade)` ——
**只认 `_map_decision` 返回的 `degrade`**。若门禁落在下游的 `order_plan.py`（由 session 调用），
它产出的降级**根本进不了重试循环**，本节承诺的「重试一次极可能给出合理价」会**静默失效**，
症状是「Agent 报了个离谱价 → 直接 hold 一轮」而非重试。故：

- `_map_decision` 负责**全部**门禁（含价带），返回 `(hold, "limit_price_out_of_band")`。
- `order_plan.py` 只做 `TradeDecision → OrderPlan` 的**纯映射**，不做校验、不产生降级。

**`last_px` 的取得（同源保证）**：`_map_decision(raw)` 由 `_attempt_decision(payload, …)` 调用
（`decider.py:224`），而 `payload` **已经含 `lastPx`**（`_build_decision_payload`，`session.py:659-673`，
字段清单含 `lastPx`）。故只需把签名改为 `_map_decision(raw, *, last_px: float | None = None)` 并在
`decider.py:224` 传 `payload.get("lastPx")` 即可——**天然与喂给 prompt 的价同源**，无需任何新的取价通路，
也保证 Agent 看到的价与被校验的价逐字一致。`last_px` 缺失或 `<=0` 时：限价单降级 hold
（reason `limit_price_out_of_band`，可重试），**绝不**跳过门禁放行。

该 reason 归入**可重试**集合（`decider.py:61-62` `_RETRYABLE_DEGRADE_REASONS`）——它是 LLM 的瞬时失误，重试
一次极可能给出合理价，比直接降级 hold 更有价值。

> 注：`missing_limit_price`（`orderType="limit"` 却没给价）同理，也必须加入 `_RETRYABLE_DEGRADE_REASONS`。
> 两个新 reason 都要加，v1 只在 §6.1 的测试名里提了它们、正文未点明集合归属。

价带门禁是**本地**护栏，与 OKX 侧的 ±5% 限价带（错误码 `51006`）是两层，前者更紧、先触发、且给出中文可解释
原因，不消耗 API 配额。

### 2.7 TP/SL 触发价基准修正（限价路径必须处理）

`_risk_trigger_px(side, ref_px)`（`executor.py:388-397`）按**当前价** `ref_px` 推算随单 OCO 的止盈止损触发价，
经 `attachAlgoOrds` 附在开仓单上（`order_builder.py:115-127`）。OKX 支持限价单携带 `attachAlgoOrds`，OCO 在成交
后才激活，机制上可用。但限价单的**预期成交价是 `limit_px` 而非 `ref_px`**——若仍以 `ref_px` 为基准，止损距离会
系统性偏移（挂单越远偏得越多）。故限价路径下 `_risk_trigger_px` 的基准价改传 `order_plan.px`。这是限价特有的
**正确性**要求，非可选优化。

### 2.8 停止 / flatten 必须撤挂单

`flatten`（`executor.py:314-340`）今天只处理持仓。新增：**先撤挂单，再平持仓**。
注意 `close_position(auto_cxl=True)`（`trade_client.py:89`）确实会撤掉该合约全部挂单，但它**仅在有持仓时被调用**
——「无持仓 + 有挂单」（即挂单从未成交）恰恰是 `flatten` 的早退分支 `if self.projected == "flat": return`
（`executor.py:320-321`），会把挂单**遗留在交易所**。这是一个必现的孤儿单缺陷，必须显式修：早退前先撤挂单。

**且撤单必须先于早退判定，而不只是先于平仓（v2 补强）**。正确顺序是：

```
flatten():
  1. if self.resting is not None:  result = await self._cancel_resting("session_stop")
  2.    result == "filled"  →  先采纳成交（projected 随之变为非 flat！）           ← 关键
  3. if self.projected == "flat": return {"ok": True, "closedSz": 0}              ← 早退判定放在采纳之后
  4. ...既有市价平仓 + 有界重试逻辑一字不动...
```

第 2 步是 §2.5 三态撤单的**第二个红利**：会话停止的瞬间挂单恰好成交（`51402`）时，采纳成交会把 `projected`
置为非 flat，于是第 3 步的早退**自然不再命中**，第 4 步照常把这笔刚成交的仓位平掉。若照 v1 的两态写法，
这一幕会变成「撤单『成功』→ 早退 → 会话报告干净停止 → 交易所侧留着一笔无人看管的真实持仓」——
比孤儿挂单严重得多，且与 `flatten` 文档字符串承诺的「绝不假报干净停止」（`executor.py:317-318`）直接冲突。

守门测试：`flatten_cancels_resting_when_flat`（孤儿单）与 `flatten_adopts_and_closes_last_moment_fill`
（`51402` 竞态），两者都要。

### 2.9 启动前置校验

沿用既有「无预存持仓」门禁的哲学，`ai_auto/manager.py` 前置校验补一条：若目标 `instId` 已有**未结普通挂单**，
拒绝启动并回 `AI_TRADE_PENDING_ORDER_EXISTS`（409）。理由：会话以 in-memory `resting` 跟踪挂单，若交易所侧
已有来路不明的挂单，自管状态与真实状态从第一秒就不一致。该读取复用既有 `live_auto_start_concurrency`
信号量（默认 2）以规避已知的 429（`docs/diagnoses/live-auto-multi-symbol-rate-limit-429`）。

**必须按 `kind` 过滤，不能用「非空」判定（P1-4 修复）**：

```python
pending = await fetch_live_open_orders(creds, inst_id)
blocking = [o for o in pending if o["kind"] in ("limit_open", "limit_close")]   # ← 必须
if blocking: raise ... AI_TRADE_PENDING_ORDER_EXISTS
```

v1 写的「`fetch_live_open_orders` 非空即拒」会**误拒正常启动**：该函数并非只返回普通挂单，它同时并发查
`orders-algo-pending` 的 `oco` / `conditional` 并把每条 OCO **展开成 `tp` / `sl` 两条**归一记录一并返回
（`trading.py:375-388`、`_normalize_algo_order:433-459`，`kind` 取值为 `"tp"`/`"sl"`）。而随单 OCO 正是
**本项目自己**在每次开仓时附的（`order_builder.py:115-127` `attachAlgoOrds`）——只要该标的上有任何带
止盈止损的持仓、或有一条残留未清的 OCO，`fetch_live_open_orders` 就非空，用户会被一个自己也看不懂的
409 挡在门外。而 `tp`/`sl` 算法委托与本特性的「普通限价挂单」是**两类不同对象**，本就不该参与该判定。

（`kind` 的分类真源是 `_classify_pending_kind`，`trading.py:404-412`，已按 `reduceOnly` / 对冲反向单区分
`limit_open` / `limit_close`——A-6 已证其存在，此处直接复用，不新写分类逻辑。）

### 2.10 回测：本期显式禁用限价（Non-goal）

`SimBroker._pending` 是「次根开盘无条件成交」的一根队列，`_Order` 连价格字段都没有（A-10）。要在回测中真实
模拟限价，须让 `_Order` 带 `px`、`_pending` 变成跨根常驻的挂单簿并按每根 high/low 判定触碰，还要补撤单与
超时——这会改动被全部策略、纸面模拟、对比回测共用的核心撮合器，回归半径远超本特性。

**本期决策**：回测模式下 `build_decision_tool_schema(..., allow_limit=False)`，Agent **看不到** `orderType` /
`limitPrice`，只能市价——**零分歧、诚实**。若放任 Agent 在回测里挂限价而 SimBroker 每单必成，回测会系统性
高估限价策略，这是比「回测不支持限价」严重得多的错误。该限制在 §八 记为 Deferred。

---

## 三、File / module change plan（文件 / 模块变更计划）

### 后端（`services/quant`）

| 文件 | ✚/✎ | 一句话意图 |
|---|---|---|
| `app/live_auto/resting.py` | ✚ | 新建：`RestingOrder` 数据类 + TTL/状态映射/价带的**纯函数**（无 IO，易单测） |
| `app/ai_auto/order_plan.py` | ✚ | 新建：`OrderPlan` + `decision_to_order_plan`（纯映射，不碰 `Signal`） |
| `app/okx/trade_client.py` | ✎ | 增 `cancel_order`（`POST /trade/cancel-order`）与 `get_order`（`GET /trade/order`），复用 `_post`/`_get` 与双层 `code`/`sCode` 校验 |
| `app/okx/trading.py` | ✎ | 增 `cancel_live_order` / `fetch_live_order`（归一 + 脱敏 + 审计）；`_map_trade_code` 补 `51006`/`51121`/`51503`；`_normalize_pending_order` **补回被丢弃的 `clOrdId`** |
| `app/okx/order_builder.py` | ✎ | 增 `snap_price_directional(px, tick, side)`（买向下取、卖向上取），供挂单 maker 意图使用；既有 `snap_price` 不动 |
| `app/live_auto/executor.py` | ✎ | `apply(..., order_plan=None)`；`_do_open`/`_do_close` 支持限价；新增 `cancel_resting`(三态)/`_poll_resting`/`_adopt_fill`/`_adopt_close_fill`；`flatten` 撤单后再判早退；`_on_reconcile_ok` 加 O-2 抑制条件；**`__init__` 增 `cancel_order_fn`/`get_order_fn` 两个注入缝（P1-2）**；`__init__` 收 `tick_sz`（P2-1 待决） |
| **`app/ai_auto/signals.py`** | ✎ | **【P0-1，v1 遗漏】`decision_to_signals` 改显式 action 白名单，删除 `close` 裸兜底**（§2.2a / M-12）。**注意**：本文件同时含 `position_ladder` / `quantize_size_fraction` 等档位真源，改动须严格限定在 `decision_to_signals`（`:164-183`）内 |
| `app/ai_auto/decider.py` | ✎ | `TradeDecision` 增 `ord_type`/`limit_px`；`_map_decision(raw, *, last_px)` 校验新字段 + **价带门禁（P1-5：必须在此）**；`_RETRYABLE_DEGRADE_REASONS` 补 `limit_price_out_of_band`/`missing_limit_price`；`_ACTIONS` 加 `cancel_order`；`__init__` 增 `allow_limit`（默认 **False**，P1-3）并透传给 `build_decision_tool_schema`（`:144` 是其唯一调用点）；journal 补字段 |
| `app/ai_auto/prompts.py` | ✎ | schema/描述/纪律补限价与撤单；`build_decision_tool_schema` 增 **keyword-only `allow_limit: bool = False`**（P1-3：默认 False 才不破 `test_ai_trade_position_step.py:132` 的恒等断言）；`allow_limit=True` 时须**深拷贝** `properties` 与 `action.enum`（绝不就地改模块常量）；决策 prompt 注入「当前挂单」段 |
| `app/ai_auto/session.py` | ✎ | 透传 `order_plan`；**`cancel_order` 前置分发（§2.2a 第一层，须在 `decision_to_signals` 之前）**；payload 加 `restingOrder`；**修 `:631` 裸下标**（该 dict 仅 3 键、连 `hold` 都没有）；统计/标签补新动作（净增须最小化，RV-2） |
| `app/ai_auto/manager.py` | ✎ | 向 `LiveExecutor` 注入限价设置（及 `tick_sz`，P2-1 待决）；前置校验补「已有**普通**挂单即拒」（**须按 `kind` 过滤**，P1-4） |
| `app/live_auto/manager.py` | ✎ | 仅补 `tick_sz` 注入（保持构造签名一致），行为零变化。**若 P2-1 决定不注入 `tick_sz`，本文件从清单中整行删除**——那样 `live_auto` 侧改动为**零**，R-9 的血溅半径论证更强 |
| `app/ai_auto/backtest.py` | ✎ | 构造 `AiTradeDecider(..., allow_limit=False)`（注意 `build_decision_tool_schema` 的唯一调用点在 `decider.py:144`，回测**不直接**调它）；`action_counts` 容纳新动作 |
| `app/config.py` | ✎ | 增 3 个 `AI_TRADE_LIMIT_*` 设置（前缀已在 desktop 白名单内，见 §5.3） |
| `app/schemas.py` | ✎ | `AiTradeDecisionOut` 补 `orderType`/`limitPrice`；新增 `AiTradeRestingOrderOut` |

### 前端（`apps/web`）

| 文件 | ✚/✎ | 一句话意图 |
|---|---|---|
| `lib/aiTradeTypes.ts` | ✎ | `AiTradeAction` 补 `"cancel_order"`；决策/挂单 wire 类型补字段 |
| `lib/aiTrade.ts` | ✎ | `describeAiAction` 补「撤单」；新增 `describeOrderType` / 挂单文案（复用既有 `describeAiAction` 风格） |
| `components/Agent/AiTradeTraceRound.tsx` | ✎ | 轨迹卡展示「限价 @ 12345.6」/「市价」与撤单事件 |
| `components/Chart/AiTradePanel.tsx` | ✎ | 状态卡增「当前挂单」行（价 / 张数 / 剩余 TTL） |

### 测试

| 文件 | ✚/✎ | 意图 |
|---|---|---|
| `tests/test_resting_order.py` | ✚ | `resting.py` 纯函数：TTL、状态映射、价带 |
| `tests/test_ai_trade_order_plan.py` | ✚ | `decision_to_order_plan` + `_map_decision` 新字段与降级 |
| `tests/test_live_executor_limit.py` | ✚ | 限价开/平、resting 不写 projected、成交采纳、撤旧挂新、flatten 撤单、**平仓成交不记 external_close（P0-3）**、**撤单撞成交不双开（P0-2）**、反手强制 market（P1-6） |
| `tests/test_okx_cancel_order.py` | ✚ | `cancel_order`/`get_order` 双层码校验；**三态归一 `canceled`/`filled`/`unknown` 互不相等（P0-2）** |
| **`tests/test_ai_trade_cancel_action.py`** | ✚ | **【P0-1】`cancel_order` 绝不平仓 + `decision_to_signals` 未知动作绝不落 close** |
| `tests/test_live_executor.py` | ✎ | **零回归门禁**：`order_plan=None` 时 payload 与今日**逐字节相同** |
| `tests/test_ai_trade_position_step.py` | — | **不改一行**，但必须**继续全绿**：其 `:130-135` 的恒等断言是 `allow_limit` 默认值的硬约束（P1-3） |
| `apps/web/lib/aiTrade.test.ts` | ✎ | 新动作/订单类型文案 |

> 测试隔离既知点：`services/quant` 全量测试共用一个临时 DB，且既有一条时序 flaky 用例——新增用例请全部
> 走注入缝、不落库、不依赖挂钟。

### 文档

| 文件 | ✚/✎ | 意图 |
|---|---|---|
| `docs/plans/ai-trade-limit-orders/spec.md` | ✚ | 本文件 |
| `.env.example` | ✎ | 补 3 个新 env 及默认值注释 |

---

## 四、Interface design（接口设计）

### 4.1 LLM 工具契约（`submit_trade_decision`，`allow_limit=True` 时）

```jsonc
{
  "type": "object",
  "properties": {
    "action":      {"type": "string",
                    "enum": ["open_long", "open_short", "close", "hold", "cancel_order"]},
    "sizeFraction":{"type": "number", "enum": [0.25, 0.5, 0.75, 1.0]},   // 档位由 position_ladder 派生
    "orderType":   {"type": "string", "enum": ["market", "limit"],
                    "description": "market=立即成交、价格不可控；limit=挂单等待，可能不成交。缺省 market"},
    "limitPrice":  {"type": "number", "exclusiveMinimum": 0,
                    "description": "限价委托价（USDT），orderType=limit 时必填；须在最新价 ±2% 内"},
    "confidence":  {"type": "number", "minimum": 0, "maximum": 1},
    "reason":      {"type": "string", "maxLength": 500}
  },
  "required": ["action", "reason"]
}
```

`allow_limit=False`（回测 / 总闸关）时 `orderType` 与 `limitPrice` **不出现**，`action` enum 回退 4 值——与今日
逐字相同。

### 4.2 决策 prompt 新增只读上下文

`build_decision_prompt` 的 payload 增 `restingOrder`（`session._build_decision_payload` 装配，`session.py:659-673`）：

```jsonc
"restingOrder": {                 // 无挂单时该段整体省略
  "intent": "open",               // open | close
  "side": "long",
  "px": 61234.5,
  "sz": 0.03,
  "ageS": 47,
  "expiresInS": 253,
  "filledSz": 0.0
}
```

渲染为一行中文：`当前挂单：限价买多 0.03 张 @ 61234.5（已挂 47s，剩余 253s 到期自动撤销，未成交）`。
无此段 Agent 便无法就挂单做任何决策——**这是 `cancel_order` 可用的前提**。

### 4.3 OKX 客户端新增方法（`okx/trade_client.py`）

```python
async def cancel_order(self, inst_id: str, *, ord_id: str | None = None,
                       cl_ord_id: str | None = None) -> dict:
    """POST /api/v5/trade/cancel-order：ordId 与 clOrdId 二选一（至少一个）。

    与 place_order 同口径：顶层 code 与 data[0].sCode 皆须为 "0"，否则以更具体的 sCode 抛
    OKXPrivateError。本层**只忠实上抛原始 sCode，不做良性/非良性的价值判断**——三态归因
    由服务层 cancel_live_order 与 executor._cancel_resting 负责（见 §2.5 与下表）。
    """

async def get_order(self, inst_id: str, *, ord_id: str | None = None,
                    cl_ord_id: str | None = None) -> dict:
    """GET /api/v5/trade/order：返回单条订单权威状态（签名 GET，复用基类 _get）。

    关键回读字段：state ∈ {live, partially_filled, filled, canceled, mmp_canceled}、
    avgPx（成交均价）、accFillSz（累计成交张数）、px、sz、ordId、clOrdId、uTime。
    """
```

> ⚠️ 签名不变式（`trade_client.py:7-9`）：POST 的签名预串是紧凑 JSON 且须以 `content=` 原样发送，**严禁**
> `json=payload`，否则必得 `50113` 签名错。新增的 `cancel_order` 走同一 `_post`，天然继承该约束与
> 「同 `clOrdId` 重试幂等」的性质。

### 4.4 服务层（`okx/trading.py`）

```python
async def cancel_live_order(session, creds, account_id, inst_id, *, cl_ord_id) -> dict
    # 撤单 + live_order_audit 留痕 + _map_trade_code 归一 + _redact 脱敏。
    # 返回 {"outcome": "canceled" | "filled" | "unknown", "code": str | None}（三态，§2.5/P0-2）：
    #   sCode=="0" 或 51401 → "canceled"（审计 status="canceled"）
    #   51402                → "filled"（审计 status="accepted"——它是成交，不是拒单！调用方须采纳）
    #   51400 / 其他         → "unknown"（审计 status="error"）
    # **绝不**把 filled 与 canceled 合并回同一个返回值。
async def fetch_live_order(creds, inst_id, *, cl_ord_id) -> dict
    # 只读；归一为 {state, px, sz, avgPx, accFillSz, ordId, clOrdId, updatedTs(秒级 UTC)}
```

> 审计行的组装：撤单没有 `direction`/`sz` 之类的天然取值，一律取自 `self.resting` 的快照（`intent`/`side`/
> `px`/`sz` 都在 `RestingOrder` 上），复用既有 `_close_audit_values` 形态。`live_order_audit.status` 是自由
> `String` 无 CHECK（§5.1 已证），新增 `"canceled"` 取值无需迁移。

新增语义错误码（`_map_trade_code`，`trading.py:52-66`）——**v2 按真实 OKX 语义重写（P0-2）**：

| OKX 码 | 真实语义 | 归一码 | 撤单场景处置 |
|---|---|---|---|
| `51006` | 委托价超出交易所限价带（±5%） | `LIMIT_PRICE_OUT_OF_RANGE` | （下单场景）本地 ±2% 门禁应已先行拦截，此码是第二层 |
| `51121` | 委托张数须为下单单位整数倍 | `ORDER_SIZE_NOT_MULTIPLE` | （下单场景） |
| `51400` | **撤单失败：订单不存在** | `ORDER_NOT_FOUND` | → 三态 `"unknown"`：保留 resting，下轮 `_poll_resting` 再判 |
| `51401` | **撤单失败：订单已撤销** | `ORDER_ALREADY_CANCELED` | → 三态 `"canceled"`：良性幂等，`resting=None` |
| **`51402`** | **撤单失败：订单已成交** | **`ORDER_ALREADY_FILLED`** | → 三态 **`"filled"`：必须回查 `get_order` 并采纳成交，且绝不下新单** |

> ⚠️ **v1 在此表上错了三处**，且错法恰好互相掩盖，故特别标注：（a）把 `51400` 注为「订单已撤销」——实为
> **订单不存在**；（b）把 `51401` 注为「订单已完成」——实为**已撤销**；（c）**完全遗漏 `51402`（已成交）**，
> 并把 `51400`/`51401` 合并为单一 `ORDER_ALREADY_SETTLED` 按「良性幂等」处理。三处叠加的后果是：真正
> 危险的「撤单时已成交」既没有码、又会落进「良性幂等」的默认篮子被丢弃 → 丢失真实持仓（详见 §2.5）。
> 语义经 ccxt / hummingbot 两个独立实现交叉验证（A-13）。
>
> **归一码必须三分，绝不可合并**。把「已撤销」（无持仓）与「已成交」（有持仓）压成同一个
> `ORDER_ALREADY_SETTLED`，正是本 spec 自己在 §2.3 批判 `orders-pending` 时点破的那个二义陷阱：
> **「订单不在了」对「成了」和「撤了」是二义的，而这两者的持仓后果完全相反。**
> 既然 §2.3 为此专门选了 `GET /trade/order`，撤单路径就不该在归一码上把刚分开的东西又粘回去。
>
> 落地提示：`_map_trade_code` 的 `mapping` 是纯字典查表（`trading.py:54-60`），三个新码直接加行即可；
> 但 `ORDER_ALREADY_FILLED` **不属于**本地拦截类，**不要**加进 `_LOCAL_REJECT_CODES`——否则审计会把它
> 记成 `rejected`，而它其实是一笔**成功的成交**。

### 4.5 无新增 REST / WS 端点

Agent 挂单经既有 WS `/api/ws/ai-trade/{session_id}` 以既有 `status` / `trade` / `agent_trace` 事件推送；
「当前挂单」的人工只读视图沿用已上线的 `GET /trading/open-orders`（A-6）——**不新开端点**，避免与
`live-pending-orders-visualization` 造成第二个真源。

---

## 五、Data model（数据模型）

### 5.1 无数据库迁移（Zero-migration）

| 表 | 结论 |
|---|---|
| `live_order_audit` | **无需改列**。`ord_type String(8)` 已存 `limit\|market`、`px` 已 nullable Float（`models.py:201-219`）。撤单仅新增一种 `status` 取值 `"canceled"`，该列是自由 `String`、无 CHECK/enum |
| `strategy_runs` | 不动。`config_json` 内加 `limitOrdersEnabled` 等键即可（JSON 列，无迁移；与 `mode="ai-trade"` 免迁移同理，见 `ai-auto-trading` spec P2-5） |
| `trades` | 不动。限价成交仍是一条普通 trade 行 |

**故本特性不需要 `db/session.py` 的 ALTER 补丁**（对比 `_ensure_trade_risk_columns:83` / `_ensure_weight_run_columns:104`
等既有补丁）。这是刻意的设计取舍：**挂单状态不落库**。

### 5.2 为何挂单状态不落库（in-memory + OKX 真源）

`db/models.py:196` 已把立场写死：「订单唯一真源在 OKX，本表仅记操作痕迹」。挂单状态若落库，立刻产生第三个真源
（内存 / DB / OKX）与三方对账问题。本方案沿用既有 `reconcile` 哲学——**OKX 是真源，内存只是缓存，每 5 秒对账**：

- 进程存活期：`self.resting` 内存态，`_poll_resting` 每 `live_auto_poll_interval_s` 向 OKX 核对。
- 进程重启：会话本就不可恢复（既有 `_reconcile_orphan_ai_trade_runs`，`db/session.py:169`，把残留
  `running` 翻 `stopped`）。挂单孤儿由 §2.9 的启动前置校验兜住——下次启动同标的会被 409 显式拒绝并提示先
  在 OKX 侧处理，**绝不静默接管来路不明的挂单**。

### 5.3 配置项（`app/config.py`，字段名即 env 名）

| 字段 | 默认 | 语义 |
|---|---|---|
| `ai_trade_limit_orders_enabled` | `True`（**P2-2 待决，评审建议首版 `False`**） | 总闸。`False` ⇒ schema 不露新字段、`order_plan` 恒 `None` ⇒ **逐字回退 market-only** |
| `ai_trade_limit_price_max_deviation_pct` | `0.02` | 本地价带：\|limitPx-lastPx\|/lastPx 超此值即降级 hold（可重试） |
| `ai_trade_limit_order_ttl_s` | `300.0` | 挂单存活上限（秒），到期自动撤销 |

> 三项均为 `AI_TRADE_` 前缀，**已在 `desktop/src/lib/config.js` prod 透传白名单内**（v2 已回源码核实：
> `desktop/src/lib/config.js:72` 确有 `key.startsWith('AI_TRADE_')`），故桌面打包**无需改动**。但必须真的在
> `Settings` 上声明——`extra="ignore"`（`config.py:19`）会静默丢弃未声明的 env（既知易踩点 P1-6）。
> 声明风格照既有 `ai_trade_*` 字段（`config.py:98-171`）：snake_case + 行内默认值，字段名即 env 名。
>
> **P2-2（默认值待决）**：v1 取 `True`，即合入当天所有实盘 AI 会话立刻获得限价能力。评审倾向首版取
> `False`：本特性给了 LLM 一个**直接控制真实委托价**的新旋钮，而 §2.6 的价带门禁、§2.5 的三态撤单都是
> 首次上线、尚无生产数据。既有先例两可——`ai_trade_enabled` 默认 `True`、`ai_trade_default_position_step_pct`
> 默认 `0.25`（都是默认即生效），故这不是「违反约定」而是**风险偏好选择**，交 Subtask #2 拍板。
> 无论取哪个，§6.4 DoD #6 的「`false` 时逐字等价」测试都必须存在。

### 5.4 内存结构

`RestingOrder`（§2.3）+ `LiveExecutor.resting: RestingOrder | None`。`OrderPlan`：

```python
@dataclass(frozen=True)
class OrderPlan:
    ord_type: str        # "market" | "limit"
    px: float | None     # limit 时为已校验的委托价；market 恒 None
```

---

## 六、Testing & acceptance criteria（测试与验收）

### 6.1 后端单测（不依赖网络）

**注入缝必须先补齐（P1-2 修复）**：v1 称单测「全部走既有注入缝 `place_order_fn`/`close_position_fn`/
`positions_fn`（`executor.py:78-80`）」——但那里**只有这 3 个缝**，而 `cancel_resting` / `_poll_resting`
依赖的是 `cancel_live_order` / `fetch_live_order`，**无缝可走**。照 v1 写，下面「生命周期」整组测试
**无法脱网运行**。故 `LiveExecutor.__init__` 必须按既有范式**新增两个同款注入缝**：

```python
self._cancel_order = cancel_order_fn or self._default_cancel_order   # 镜像 :78-80 的既有写法
self._get_order    = get_order_fn    or self._default_get_order      # 默认走 okx/trading.py 服务层
```

（`__init__` 形参本已超 CLAUDE.md 的 ≤5 上限，再加 2 缝 + `tick_sz` 更甚——见 §八.7 的聚合建议。
本期若不做聚合重构，至少**不要**再堆位置参数，全部走 keyword-only。）

**零回归门禁（最高优先级）**
- `test_market_path_payload_byte_identical`：`order_plan=None` 时 `build_order_payload` 产物与改动前**逐字节相同**。
- `test_live_auto_unaffected`：`live_auto` 全量既有用例零改动通过（它从不传 `order_plan`）。
- `test_schema_without_limit_flag_identical`：`allow_limit=False` 的 schema 须与一份**硬编码的冻结字面量快照**
  深度相等。
  > ⚠️ **P1-3 修复**：v1 写的是「与今日 `DECISION_TOOL_SCHEMA` 深度相等」——那是个**空测**。若实现
  > 就地改坏了模块常量（`build_decision_tool_schema` 在 `step<=0` 时**返回常量本身而非拷贝**，
  > `prompts.py:92`，极易误改），这个断言就变成「拿改坏的常量和它自己比」，**恒真**。必须比对**写死在
  > 测试文件里的字面量**，才真的锁得住。
- **`test_existing_identity_assertion_survives`**：既有 `test_ai_trade_position_step.py:130-135` 必须**零改动
  通过**——它断言 `build_decision_tool_schema(0.0) is DECISION_TOOL_SCHEMA`（**恒等**，不是相等）且 `step>0`
  的调用不污染常量。这是 `allow_limit` **必须默认 `False`** 的硬约束来源：默认 `True` 会让该断言当场失败。

**契约与降级**
- `rejects_limit_without_price`：`orderType="limit"` 缺 `limitPrice` → hold，reason `missing_limit_price`（可重试）。
- `rejects_limit_price_out_of_band`：偏离 > 2% → hold，reason `limit_price_out_of_band`（可重试）。
- `accepts_cancel_order_action`：`cancel_order` 不再 KeyError（守 M-6）。
- `cancel_order_with_no_resting_is_benign`：无挂单撤单 → status 事件、不抛错、不改 `projected`。
- **`cancel_order_never_closes_open_position`**【P0-1 守门，v1 缺】：**持有真实多仓**（`projected=="long"`）
  时喂 `action="cancel_order"`，断言 `close_position_fn` 桩**零调用**且 `projected` 仍为 `"long"`。
  注意上一条 `cancel_order_with_no_resting_is_benign` 是 **flat** 场景，**测不出**该缺陷——两条都要。
- **`decision_to_signals_never_falls_back_to_close`**【P0-1 守门】：对
  `["cancel_order", "unknown_future_action", ""]` 逐个断言 `decision_to_signals(...) == []`
  （**不是** `[Signal(side="close")]`）。这条测试是 M-12 白名单的直接锁；它同时锁住「未来再加新动作时
  不会重蹈覆辙」。

**生命周期（核心）**
- `resting_open_does_not_set_projected`：限价开仓后 `projected == "flat"`（守 I-2）。
- `unfilled_resting_survives_reconcile`：**回归守门**——挂单未成交时连跑 3 次 `reconcile`，断言**不产生任何
  `external_close` 假成交**、`trades` 长度不变（正面锁死 §一 所述幻影成交陷阱）。
- `filled_resting_adopts_avg_px`：`state="filled"` → `projected` 置位、`entry_price == avgPx`（非 `ref_px`）。
- `partially_filled_adopts_acc_fill_sz`：部分成交按 `accFillSz` 采纳且剩余仍挂。
- `expired_resting_is_cancelled`：`now > expire_ts` → 触发 `cancel_order` 且 `resting is None`。
- `new_open_cancels_stale_resting`：撤旧挂新；**撤单失败时不下新单**（守绝不双挂）。
- `flatten_cancels_resting_when_flat`：无持仓 + 有挂单 → `flatten` 仍撤单（守 §2.8 孤儿单缺陷）。
- `limit_tp_sl_anchored_on_limit_px`：限价开仓的 `attachAlgoOrds` 触发价基于 `limit_px` 而非 `ref_px`（守 §2.7）。

**平仓挂单与竞态（v2 新增，守 P0-2 / P0-3）**
- **`resting_close_keeps_projected_until_filled`**：限价平仓挂出后 `projected` 仍为原方向（守 I-2b）。
- **`filled_close_is_not_recorded_as_external_close`**【P0-3 正面守门】：限价平仓挂单成交 + OKX 持仓归零，
  跑 `reconcile` → 断言产出的 trade `reason == "signal_close"`（**不是** `"external_close"`）且
  `price == avgPx`（**不是** `mark_px`）——即 O-1 顺序真的生效、盈亏是真的。
- **`reconcile_suppresses_external_close_while_close_resting`**：`resting.intent=="close"` 且
  `_poll_resting` 被桩为读取失败时，`reconcile` **不得**合成任何 `external_close`（守 O-2 兜底）。
- **`poll_resting_failure_does_not_degrade_reconcile`**：`get_order` 抛 `OKXError` → `_reconcile_fail_streak`
  **不增**、不触发 `LIVE_AUTO_RECONCILE_DEGRADED`（守 `_poll_resting` 的异常自隔离）。
- **`cancel_racing_fill_adopts_and_skips_new_order`**【P0-2 正面守门】：撤单桩回 `51402` →
  断言（a）成交被采纳（`projected` 置位、`entry_price==avgPx`）；（b）`place_order_fn` **零调用**
  （绝不在已成交仓位上再开一单）。这是本特性**唯一能把敞口放大一倍**的路径，必须有正面测试。
- **`cancel_on_51401_is_benign_canceled`**：`51401` → 三态 `"canceled"`、`resting=None`、不采纳任何成交。
- **`flatten_adopts_and_closes_last_moment_fill`**：flatten 时撤单回 `51402` → 采纳成交 → **不早退** →
  照常市价平掉（守 §2.8 的「绝不假报干净停止」）。
- **`reverse_forces_market_on_close_leg`**【P1-6 守门】：持空仓 + `open_long(limit)` → 断言 close 腿 payload
  `ordType=="market"`、open 腿 `ordType=="limit"`（守 §2.5 终态腿绑定规则）。
- **`same_price_open_does_not_replace_resting`**【P2-3】：同向同价重复决策 → `cancel_order_fn` 零调用。

**OKX 客户端**
- `cancel_order_raises_on_scode`：`sCode != "0"` → `OKXPrivateError` 带具体 sCode。
- **`cancel_live_order_maps_three_outcomes`**（替换 v1 的 `cancel_order_benign_on_51400`——后者基于错误的
  码语义，见 P0-2）：`sCode="0"`/`51401` → `"canceled"`；**`51402` → `"filled"`**；`51400` → `"unknown"`。
  三者**必须互不相等**。
- `get_order_maps_state`：各 `state`（`live`/`partially_filled`/`filled`/`canceled`/`mmp_canceled`）正确归一。

### 6.2 集成测（`-m network`，opt-in，默认排除）

- 演示盘（`x-simulated-trading: 1`，`private_client.py:90-101`）下：挂一笔远离市价的限价单 → 读回 `state=="live"`
  → 撤单 → 读回 `state=="canceled"`。全链路真实、不引入 mock。

### 6.3 前端测试

- `aiTrade.test.ts`：`describeAiAction("cancel_order") === "撤单"`；订单类型文案。
- `AiTradeTraceRound.test.tsx`：限价决策渲染出「限价 @ …」。
- 运行须 `NODE_ENV=test`（既知易踩点：shell 默认 production → React `act()` 报错）。

### 6.4 验收标准（Definition of Done）

1. Agent 可在一轮内输出 `action=open_long, orderType=limit, limitPrice=X` 并在 OKX 侧真实挂出限价单
   （演示盘可验证），**且 Agent 仍可照旧市价委托**（需求原文「之后 Agent 可以限价委托，也可以市价委托」）。
2. 挂单未成交时：连续 reconcile **不产生**任何幻影成交，会话盈亏不受污染。
3. 挂单成交后：`projected` / `entry_price(=avgPx)` / `entry_qty_sz` 正确，trade 事件与 WS 推送正常。
4. Agent 下一轮能在 prompt 中看到自己的挂单，并能用 `cancel_order` 撤回；**且 `cancel_order` 在持仓状态下
   绝不触发任何平仓调用**（P0-1）。
5. TTL 到期自动撤单；会话停止（flatten）不遗留任何孤儿挂单，**且不遗留任何「最后一刻成交」的未平持仓**（P0-2）。
5b. **限价平仓成交被记为正常 `signal_close`（价 `=avgPx`），而非 `external_close`（价 `=mark_px`）**——
   即 §2.3 的 O-1/O-2 真的生效（P0-3）。
5c. **撤单撞上成交（`51402`）时：成交被采纳、且当轮不再下任何新单**——敞口绝不翻倍（P0-2）。
6. `AI_TRADE_LIMIT_ORDERS_ENABLED=false` 时行为与本特性合入前**逐字等价**。
7. 后端 `pytest -q`（不含 network）与前端 `pnpm test` 全绿；`tsc` 无新增错误
   （注：`WeightBatchArena.test` 的 tsc 报错是**既有**问题，非本次回归）。
8. 变更文件均在 §三 清单内；`session.py` 净增行数受控，新逻辑落 `resting.py` / `order_plan.py`。

---

## 七、Risks & mitigations（风险与缓解）

| # | 风险 | 严重度 | 缓解 |
|---|---|---|---|
| R-1 | **幻影成交（开仓路径）**：未成交挂单被 `_on_reconcile_ok` 记成「开仓又被外部平仓」并捏造盈亏（`executor.py:281-289`） | **P0** | I-2a：resting 期间 `projected` 保持 `flat`，该分支条件根本不满足；并以 `unfilled_resting_survives_reconcile` 正面锁死 |
| **R-1b** | **幻影成交（平仓路径）**：限价平仓成交后 OKX 持仓归零，同一分支把我方主动平仓记成 `external_close` 并按 `mark_px` 捏造盈亏（v1 的 I-2 只括注了开仓场景，**留空了平仓**） | **P0** | v2 §2.3：I-2b（平仓 resting 期间保持原方向）+ **O-1 顺序**（`_poll_resting` 先于 `_on_reconcile_ok`，`reconcile()` 是全部 6 处调用点的唯一收口）+ **O-2 抑制**（`intent=="close"` 时禁用该分支）；`filled_close_is_not_recorded_as_external_close` 正面锁死 |
| R-2 | **成交不可见**：I-2a 的代价——成交后 `projected` 仍 flat，而 reconcile 无「自管无仓、交易所有仓」分支 | **P0** | `_poll_resting` 用 `GET /trade/order` 权威 `state` 侦测成交并 `_adopt_fill`。**v2 更正**：v1 宣称的「`abs_pos` 作最终兜底」**并不存在**（`_on_reconcile_ok` 从不覆写 `entry_qty_sz`，A-14）——`_poll_resting` 是**唯一**侦测路径。刻意不补第二道网，理由见 §2.3（会污染共享的 `live_auto` 语义）。**残余风险**：单轮读取失败仅延后一轮采纳；「成交后会话立刻异常退出」由 §2.9 启动前置校验兜住 |
| **R-2b** | **撤单-成交竞态**：撤单瞬间挂单成交（`51402`），若按「良性幂等」丢弃 resting → 丢失真实持仓；cancel-replace 下更会在已成交仓位上再开一单（**敞口翻倍**） | **P0** | v2 §2.5：`_cancel_resting` 返回**三态** `canceled\|filled\|unknown`（非 bool）；`filled` 必须采纳成交且**放弃本轮新单**；§4.4 归一码三分，`ORDER_ALREADY_FILLED` 独立成码。`cancel_racing_fill_adopts_and_skips_new_order` 正面锁死 |
| **R-2c** | **静默误平**：`decision_to_signals` 对未知 action 裸兜底成 `close` 信号 → `cancel_order` 若漏了前置分发，会**真实市价平掉持仓**且不报错 | **P0** | v2 §2.2a 双层防线（session 前置分发 + M-12 白名单兜底，兜底方向为「不动」）；`cancel_order_never_closes_open_position` 与 `decision_to_signals_never_falls_back_to_close` 双测锁死 |
| R-3 | **孤儿挂单**：flatten 早退分支（`executor.py:320-321`）在「无持仓+有挂单」时直接返回，挂单遗留交易所 | **P1** | §2.8 早退前先撤单；`flatten_cancels_resting_when_flat` 守门；§2.9 启动前置校验二次兜底 |
| R-4 | **回测高估**：SimBroker 每单必成，若放任限价进回测会系统性高估（A-10） | **P1** | §2.10 回测 `allow_limit=False`，Agent 看不到限价工具——零分歧而非假模拟 |
| R-5 | **LLM 幻觉价**：离谱 `limitPrice` 导致远坏成交或等同市价单绕过名义缓冲 | **P1** | §2.6 本地 ±2% 价带门禁（早于 OKX 的 51006、更紧、中文可解释）；校验价与 prompt `lastPx` 同源 |
| R-6 | **`entry_price` 口径分裂**：限价用 `avgPx`、市价仍用 `ref_px` | P2 | 有意为之以控回归半径；差异记录在案，留给后续特性统一。市价路径口径**一字不改** |
| R-7 | **双挂**：撤旧挂新时撤单失败却下了新单 | **P1** | §2.5 撤单成功才下新单；失败推 error 并保留旧单；`new_open_cancels_stale_resting` 守门 |
| R-8 | **契约镜像漏改**：动作枚举抄写在 **13 处**（v2 复核；v1 记为 8 处），无任何共享常量关联。漏改的后果**按位置分两类**：`session.py:631` 裸下标会**大声** KeyError（当轮即炸，反而安全）；`signals.py:183` 裸兜底会**静默误平真仓**（见 R-2c） | **P1** | §2.2 表 M-1..M-13 逐条列全并按**失败模式**分级；`accepts_cancel_order_action` 守 M-6；`decision_to_signals_never_falls_back_to_close` 守 M-12；`:631` 改 `.get()` + 显式分支 |
| R-9 | **共享 `LiveExecutor` 血溅半径**：改动同时命中 `live_auto` 与 `ai_auto`（A-9） | **P1** | `order_plan` 默认 `None` ⇒ market ⇒ `live_auto` 调用点一字不改；以「payload 逐字节相同」测试锁死 |
| R-10 | **`snap_price` 四舍五入**：round-to-nearest 可能把买单价格**上抬**过意图价（`order_builder.py:82`） | P2 | 新增 `snap_price_directional`（买 floor / 卖 ceil）供挂单用；既有 `snap_price` 不动（OCO 触发价维持原语义） |
| R-11 | **429 限流**：`_poll_resting` 增加私有 GET 频次 | P2 | 仅在 `resting is not None` 时才查、且搭 `reconcile` 既有 5s 节奏（不新增独立轮询循环）；启动读挂单复用 `live_auto_start_concurrency` 信号量 |
| R-12 | **部分成交漂移**：`accFillSz` 与本地记账不一致 | P2 | 持仓最终真源仍是 OKX `abs_pos`，既有 `_on_reconcile_ok` 自然覆盖 |
| R-13 | **`session.py` 越界**：已 932 行（v2 核实：确为 932），再加逻辑恶化 RV-2 | P2 | 生命周期/纯映射落 `resting.py` / `order_plan.py`；`session.py` 只做透传与分发 |
| **R-14** | **启动误拒**：`fetch_live_open_orders` 含 OCO 展开的 `tp`/`sl` 行，「非空即拒」会把带随单止盈止损的正常账户挡在门外 | **P1** | v2 §2.9：按 `kind in {"limit_open","limit_close"}` 过滤（复用既有 `_classify_pending_kind`） |
| **R-15** | **重试链路静默失效**：价带门禁若落在 `order_plan.py`，其降级进不了 `decider.decide` 的重试循环，§2.6 承诺的「重试一次」形同虚设 | **P1** | v2 §2.6：门禁**必须**在 `_map_decision` 内；`last_px` 由 `payload["lastPx"]` 传入（与 prompt 天然同源）；`order_plan.py` 降为纯映射 |
| **R-16** | **限价 + 反手**：单个 `order_plan` 对应 `_project_signals` 的两腿，绑定不明；两腿都限价会使 `_confirm_net_cleared` 失败、open 腿被跳过、会话陷半反手态 | **P1** | v2 §2.5：`order_plan` 只绑**终态腿**，反手 close 腿强制 `market`；`reverse_forces_market_on_close_leg` 守门 |
| **R-17** | **门禁空转**：零回归 schema 测试若与模块常量比对，常量被就地改坏时该测试恒真 | **P1** | v2 §6.1：改与**冻结字面量快照**比对；`allow_limit` 默认 `False` 以保既有恒等断言（`test_ai_trade_position_step.py:132`）零改动通过 |
| **R-18** | **测试无缝可走**：`cancel_live_order`/`fetch_live_order` 无注入缝，生命周期测试须真连网 | **P1** | v2 §6.1/§三：`__init__` 补 `cancel_order_fn`/`get_order_fn` 两缝，镜像既有 `:78-80` 范式 |
| **R-19** | **重复采纳**：`partially_filled` 每轮回读的 `accFillSz` 是**累计值**，逐轮全量采纳会把持仓越加越多 | P2 | v2 §2.3：`RestingOrder.adopted_sz` 记已采纳量，每轮只采纳增量 `accFillSz - adopted_sz` |

---

## 八、实施注意（供 Subtask #2）

0. **【v2 新增·最高优先】先落三条 P0 修复，再谈功能**。它们各自只有几行，但漏任一条都会在**真实资金**上
   造成静默损失，且都不会报错：
   - **P0-1**：`signals.py:183` 的 `close` 裸兜底 → 改白名单（§2.2a / M-12）。**这条最隐蔽**：漏了它，
     `cancel_order` 会平掉真仓，日志干干净净。
   - **P0-2**：`51402`（已成交）必须与 `51401`（已撤销）**分开**，三态返回（§2.5 / §4.4）。
   - **P0-3**：`_poll_resting` 必须在 `reconcile()` 内**最先**执行 + `intent=="close"` 抑制分支（§2.3 O-1/O-2）。
1. **动手顺序**（自底向上，每层可独立单测）：`trade_client`(cancel/get) → `trading`(服务层+**三态**错误码) →
   `resting.py` → `executor`（含 2 个新注入缝）→ `order_plan.py` → `decider`（含价带门禁）→ `prompts` →
   **`signals.py`（M-12 白名单）** → `session`/`manager` → 前端。
2. **先写回归门禁再动 `executor.py`**：`test_market_path_payload_byte_identical` 必须先绿，再改
   `_do_open`——它是 R-9 的唯一保险。
3. **`json=payload` 是雷**（`trade_client.py:7-9`）：`cancel_order` 必须走 `_post`，切勿自行 `client.post(json=...)`。
4. **`session.py:631` 是必炸点**：`{"open_long":"long",...}[action]` 裸下标，加 `cancel_order` 当轮即 KeyError。
5. **`_ACTIONS`(`decider.py:53`) 与 schema enum(`prompts.py:17-26`) 是两个真源**，必须同改，否则新动作会被
   `_map_decision` 判为 `invalid_action` 而静默降级 hold——症状是「Agent 说撤单但什么也没发生」。
6. **`_normalize_pending_order`(`trading.py:415`) 丢了 `clOrdId`**，补回它是挂单归属判定的前提。
7. **CLAUDE.md 阈值是硬约束**：单文件 ≤800 行、单函数 ≤50 行、形参 ≤5、嵌套 ≤4、圈复杂度 ≤10。
   `_poll_resting` 的分支易破 50 行/复杂度 10——状态分派请落 `resting.py` 的查找表/纯函数。
   `LiveExecutor.__init__` 形参已多，新增 `tick_sz` 与限价设置请考虑聚合为配置对象而非继续堆位置参数。
8. **Deferred / Out of Scope（本期明确不做，勿擅自扩张）**：回测限价模拟（SimBroker 改造）、
   `POST /trade/amend-order` 改价（统一用撤旧挂新）、`post_only`/`fok`/`ioc`/`optimal_limit_ioc` 高级
   `ordType`、多笔并存挂单（I-1 单笔不变式）、挂单状态落库（§5.2）、跨进程重启接管挂单（§2.9 拒绝启动）。
9. **提交纪律**（Subtask #3 教训，`ai-trade-position-ladder` 记录）：工作树存在多个未提交特性且共享文件，
   须 `git add <具体路径>` 精确暂存，**严禁 `git add -A`**；混合文件用 `git apply --cached` 行级剔除他特性 hunk。
10. **【v2 新增】两项待决，请 Subtask #2 显式拍板并把结论写回文末〈实施过程发现的方案缺陷〉段**：
    - **P2-1 `tick_sz` 注入是否保留**。它是**唯一**触碰共享 `live_auto/manager.py` 的改动，而其收益存疑：
      委托价的权威对齐已由 `build_order_payload` 用 `meta.tick_sz` 完成（`order_builder.py:146`），
      真实 `px` 也可由 `_poll_resting` 的 `get_order` 回读。若仅为本地记账，建议**删除 `tick_sz` 注入与
      `snap_price_directional`**，`live_auto` 侧改动归零，R-9 的论证更硬。若保留，须在 spec 说明
      「executor 定向 snap → `build_order_payload` 再 `snap_price` 四舍五入」的**二次对齐是幂等的**
      （对已对齐值 round 不变），否则读者会疑心两次 snap 打架。
    - **P2-2 `ai_trade_limit_orders_enabled` 默认值**（`True` vs `False`），理由见 §5.3。
11. **【v2 新增】`_map_decision` 的复杂度红线**。它已承载 action 校验 / `low_confidence` / 档位量化
    （`decider.py:284-305`），再加 `orderType`/`limitPrice`/价带三重校验必破 CLAUDE.md 的 50 行与圈复杂度 10。
    请把限价校验抽为 `resting.py`（或 `order_plan.py`）里的**纯函数** `validate_limit_intent(raw, last_px,
    max_dev) -> (ord_type, limit_px, degrade_reason | None)`，`_map_decision` 只做一次调用 + 分支。
    纯函数天然好测，也让价带门禁的单测不必构造 `AiTradeDecider`。

---

## 评审结论（Review Verdict · Subtask #1）

### 裁决：**有条件通过**

方案的**架构方向正确，应按 v2 实施**。v1 最有价值的贡献是那次认知纠偏——它证伪了「本项目不支持限价单」
这个想当然的前提，把工作量从「从零实现限价」重新定位到「把已验证的限价能力接上 AI 链路，并补齐它缺失的
那一半：挂单生命周期」。它还独立发现了乐观成交模型这个真正的暗礁（`executor.py:281-289`），并给出了
I-2 这个正确的解法。这些判断经本次逐行核源码**全部成立**，v2 予以保留。

### 对四个质证点的回答（v1 §评审结论 自设的靶子）

| 质证点 | 结论 |
|---|---|
| （a）I-2 是否真能规避 `executor.py:281` 的幻影成交 | **开仓路径：能**（`projected` 恒 flat，分支条件不满足，已复核）。**平仓路径：不能**——v1 的 I-2 括注只覆盖开仓，平仓路径 `projected` 必为非 flat，该分支照常命中。已按 P0-3 拆为 I-2a/I-2b 并补 O-1 顺序 / O-2 抑制 |
| （b）`_poll_resting` 是否覆盖全部 `state` 且无二义归因 | **`state` 覆盖完整**（5 个取值齐全），**但归因有二义**——不在 `_poll_resting` 里，而在它旁边的**撤单路径**：v1 把 `51401`(已撤销) 与 `51402`(已成交) 合并为 `ORDER_ALREADY_SETTLED`，正是它自己批判 `orders-pending` 时点破的同一个陷阱。已按 P0-2 三分 |
| （c）M-1..M-11 镜像点是否有遗漏 | **有，且漏的恰是唯一会静默误执行的那处**：`signals.py:183` 的 `close` 裸兜底（M-12）。v1 标注了会**大声** KeyError 的 M-6，却漏了会**安静平掉真仓**的 M-12。另漏 `aiTrade.ts:424`（M-13，P2）。实际镜像点为 13 处，非 8 处 |
| （d）R-9 的「默认等价」是否真做到 `live_auto` 零回归 | **基本做到，但有一处未言明**：`order_plan=None ⇒ market` 的设计确实使 `live_auto` 调用点一字不改（已核实两处构造与 6 处调用点）。**唯一破绽**是 `tick_sz` 注入会强制改 `live_auto/manager.py`——已列为 P2-1 待决，建议删除以使 `live_auto` 改动归零 |

### 四个维度评估

- **可行性：通过。** 关键前提均已回源码证实：`close_live_position` 确已支持 `ord_type="limit"` 走
  `place_order`（`trading.py:231-237`），故限价平仓无需新链路；`tick_sz` 在两处构造点的 `instMeta` 里都拿得到；
  `AI_TRADE_` 前缀确已在桌面白名单内（`config.js:72`）；审计表确实无需迁移（`ord_type String(8)` 存
  `limit`/`market` 有余、`px` 已 nullable）。**未发现任何不可实现项**。
- **完备性：修复后通过。** v1 的缺口集中在**竞态与边界**（撤单撞成交、平仓成交、反手两腿、启动误拒），
  而非主干；均已就地补齐并配正面守门测试。
- **一致性：修复后通过。** v1 有一处与 CLAUDE.md 冲突（`_map_decision` 再堆校验必破 50 行/复杂度 10 —— 已
  在 §八.11 给出纯函数抽取方案），一处与既有测试冲突（`allow_limit` 默认值 vs
  `test_ai_trade_position_step.py:132` 恒等断言 —— 已改默认 `False`）。其余（不新开下单通道、错误码归一、
  脱敏、审计、新逻辑落新模块）与既有约定一致。
- **规模适配：基本合适，建议再瘦一圈。** 生命周期状态机不是过度设计——它是乐观成交模型下的**必需品**。
  唯一可裁的是 `tick_sz` 注入 + `snap_price_directional`（P2-1），裁掉可使 `live_auto` 侧改动归零。

### 放行条件（Subtask #2 必须逐条落实，缺一不可）

1. **P0-1**：`decision_to_signals` 白名单化（M-12），删除 `close` 裸兜底；session 层 `cancel_order` 前置分发。
   守门：`cancel_order_never_closes_open_position`、`decision_to_signals_never_falls_back_to_close`。
2. **P0-2**：撤单三态（`canceled`/`filled`/`unknown`），`51402` 独立成 `ORDER_ALREADY_FILLED` 且必须采纳成交、
   **绝不下新单**。守门：`cancel_racing_fill_adopts_and_skips_new_order`。
3. **P0-3**：I-2 拆 I-2a/I-2b；`_poll_resting` 在 `reconcile()` 内**最先**执行（O-1）且 `intent=="close"` 时
   抑制 `external_close` 分支（O-2）；`_poll_resting` 异常自隔离。
   守门：`filled_close_is_not_recorded_as_external_close`、`poll_resting_failure_does_not_degrade_reconcile`。
4. **P1-1**：删除「`abs_pos` 兜底」的失实主张，`_adopt_close_fill` 复用 `_settle_close(ref_px=avgPx)`。
5. **P1-2**：`__init__` 补 `cancel_order_fn`/`get_order_fn` 两注入缝（全 keyword-only）。
6. **P1-3**：`allow_limit` 默认 `False` 且 `True` 时深拷贝 schema；零回归门禁改与**冻结字面量**比对；
   `test_ai_trade_position_step.py` 零改动全绿。
7. **P1-4**：§2.9 前置校验按 `kind in {"limit_open","limit_close"}` 过滤。
8. **P1-5**：价带门禁落 `_map_decision`，`last_px` 取自 `payload["lastPx"]`；两个新 degrade reason 入
   `_RETRYABLE_DEGRADE_REASONS`。
9. **P1-6**：`order_plan` 只绑终态腿，反手 close 腿强制 `market`。
10. **拍板并记录** P2-1（`tick_sz` 去留）与 P2-2（总闸默认值）到文末〈实施过程发现的方案缺陷〉段。

> P2-3..P2-6 为建议项，不阻塞放行；若不做，请在文末〈实施过程发现的方案缺陷〉段一句话说明理由即可。

### 一句话总结

> v1 是一份**判断力很好、但在自己划出的雷区里踩了三脚**的方案：它准确指出「订单不在了」对「成了」和
> 「撤了」是二义的，然后在撤单错误码上把这两者合并；它用 I-2 堵死了开仓的幻影成交，却没给平仓写对称的
> 一条；它列了 8 处镜像点并标出会大声报错的那处，却漏了会安静平掉真仓的那处。**这三处同构的疏漏都源于
> 同一个习惯：把「不确定」当成「良性」。** v2 已把这三处的兜底方向统一扳回「不动」，并为每处配了正面
> 守门测试。按 v2 落地即可放行。

## 实施过程发现的方案缺陷（Issues Found During Implementation · Subtask #2 / #3）

### 两项待决的拍板结论（§八.10）

- **P2-1 `tick_sz` 注入：决定「不做」。** 委托价的权威 `tickSz` 对齐已由 `build_order_payload`
  （`order_builder.py:146`）完成，真实成交价另由 `_poll_resting` 的 `get_order` 回读 `avgPx` 取得，
  故 executor 本地再持有一份 `tick_sz` 无收益。裁掉它同时裁掉了 `snap_price_directional`，
  换来的是 **`live_auto/manager.py` 与 `live_auto` 侧改动归零**——R-9「共享 `LiveExecutor` 血溅
  半径」的论证因此从「靠测试锁死」升级为「结构上不可能」。§三 中 `live_auto/manager.py` 一行按
  spec 自身的约定整行删除。代价：`is_same_intent`（P2-3 幂等判定）按**原始委托价**而非 tick 对齐后
  的价比较，故 Agent 若在同一 tick 内给出两个不同浮点价，会各挂一次（多一次撤挂，不影响正确性）。
- **P2-2 `ai_trade_limit_orders_enabled` 默认值：取 `True`。** 与既有 `ai_trade_reflection_enabled`
  / `ai_trade_default_position_step_pct`「默认即生效」的约定一致，且需求原文要求的正是「之后 Agent
  可以限价委托，也可以市价委托」——默认 `False` 会让合入当天的实盘会话拿不到本特性。风险由三层
  兜住：本地 ±2% 价带门禁、三态撤单、以及 `false` 时逐字等价的回退路径（§6.4 DoD #6 有测试守门）。
  运维如需保守上线，置 `AI_TRADE_LIMIT_ORDERS_ENABLED=false` 即可一键回到 market-only。

### P2-3..P2-6 的处置

P2-3（撤旧挂新幂等）**已做**（`resting.is_same_intent` + `_prepare_resting_slot` 的同向同价短路）。
P2-4（`aiTrade.ts:424` `describeTrigger`）经核查**无需改动**：该处硬编码数组用于统计「有效交易动作」，
而 `cancel_order` 本就不该计入，现状即正确。P2-5 / P2-6 已在 v2 正文的 §4.4 / §4.1 就地修正。

### 实施 / 终审过程中发现的 spec 缺陷

- **O-3（P0 级，v2 spec 的盲区，Subtask #3 终审发现并修复）**：v2 用 O-1（`_poll_resting` 先于
  `_on_reconcile_ok`）+ O-2（`intent=="close"` 时抑制幻影分支）堵住了**平仓**路径的 R-1 复发，但
  **开仓**路径留了同构的一个洞：`_adopt_fill` 把 `projected` 由 `flat` 置为有仓后，**同一轮**紧接着
  的 `_on_reconcile_ok` 会用一份**可能尚未跟上**的持仓读数去判「自管有仓、交易所无仓」——OKX 的
  `/trade/order` 与 `/account/positions` 之间存在最终一致性窗口，而 O-1 恰恰把这两次读取压缩到仅隔
  一次网络往返（市价路径下二者相隔一整个 5s 轮询周期，故此前不显）。命中即把一笔**刚采纳的真实
  持仓**合成为 `external_close` 幻影平仓、按 `mark_px` 捏造盈亏并归 flat，自管随即丢失对真仓的跟踪
  ——即 R-1 在开仓路径上的原样复发，且**由 O-1 这条修复本身引入**。
  修复：`_adopt_fill` 置 `_fill_grace`，`_on_reconcile_ok` 给刚成交的那一轮**一次**宽限（恒被消费，
  绝不跨轮累积成永久豁免）；持仓读数若下一轮仍为空，则如实校正。守门测试
  `adopted_open_fill_is_not_phantom_closed_by_lagging_positions` 与 `fill_grace_lasts_exactly_one_round`。
  > 教训与 v2 评审结论那句总结完全同构：**把「不确定」当成「良性」**。v2 已把这个习惯在三处扳回，
  > 却在自己新增的 O-1 顺序约束里第四次踩到——「持仓读数为空」对「真的没仓」与「还没同步」是**二义**的。
- **限价平仓的部分成交刻意不结算（残余偏差，记录在案）**：`_adopt_partial` 只对 `intent=="open"` 做
  增量采纳；平仓挂单的部分成交仅推进 `adopted_sz`，等全额成交或 TTL 撤单后再全额结算。理由：
  `_settle_close` 是「先算 pnl 再复位 entry」的**全额**结算，另写一份部分平仓结算逻辑会立刻产生第二个
  盈亏口径真源（§2.3 明令禁止）。残余偏差：平仓挂单**部分成交后被 TTL 撤单**时，`entry_qty_sz` 会
  短暂高于交易所侧真实张数，直到下一次决策全额平掉；因 `_on_reconcile_ok` 从不覆写 `entry_qty_sz`
  （A-14），该偏差不会被自动纠正。判定为可接受（不影响安全红线，只影响一次仓位显示），留待后续特性。
