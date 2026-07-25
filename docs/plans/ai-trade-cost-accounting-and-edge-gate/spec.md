# AI 自动交易 · 成本记账与最小 edge 门禁（阶段一 · 止血）

> **版本：v2**（2026-07-25 设计评审后修订；v1 为设计节点初稿）
> 需求来源：`docs/improve/01-ai-autotrading` 诊断集（D-1 ~ D-18）
> 本 spec 覆盖范围：[08 · 实施路线图](../../improve/01-ai-autotrading/08-实施路线图与里程碑.md) §3.1 **阶段一 · 止血**的全部六项任务
> 迭代：loop `tge-b46952e9` 第 1/3 轮 · 设计节点产物（本节点只出设计，不写实现代码）

---

## 〇、评审记录（Review Notes · v1 → v2）

评审时间 2026-07-25，评审口径：可行性 / 完备性 / 一致性 / 规模适配。**v1 的全部代码引用（行号、函数名、常量值）已逐条回读当前工作树复核，结论：引用全部准确**——`session.py` 1404 行、`executor.py` 863 行、`backtest.py` 966 行、`_close_pnl` 在 `executor.py:732-738`、`"fee": 0.0` 在 `:779`、`session_pnl_usd` 在 `:147-149`、实盘门禁位 `session.py:575`/`:578`、回测门禁位 `backtest.py:520`/`:522`、`entry_reference_px` 在 `base_rules.py:169-177`、`fetch_live_fills` 在 `trading.py:454`、`_inst_meta_cache` 写法在 `rest_client.py:226-250`、`default_taker_fee_bps=5.0`/`default_slippage_bps=2.0` 在 `config.py:280-281`、SimBroker 双边费 + 双边滑点合计 14 bps（`engine/simulator.py:108-109, 150-153, 266, 306`）、`build_user_message` 只读显式键（`prompts.py:444-495`）。以下问题全部来自**代码引用之外的推理层**。

| 编号 | 级别 | 位置 | 问题 | v2 处置 |
|---|---|---|---|---|
| RV-1 | **P0** | §3.8 | `_compute_ai_stats` 是**模块级纯函数** `def _compute_ai_stats(records, rounds)`（`session.py:1357`），拿不到 `self`，因此「在其**内部**合并、不改签名」在物理上不可实现 | 改为**调用点合并**（`session.py:1285` 所在的 `_finalize_run` 富化块），与既有 `wakeSkips`/`maxHoldStreak`/`profitGuardExits` 四个加法键**同一范式**；`_compute_ai_stats` 一字不动。见 §3.8 |
| RV-2 | **P0** | §3.2 / §3.3 / §4.2 | `LiveExecutor` 由 **AI 与非 AI 两条产品线共用**（`ai_auto/manager.py:590` 与 `live_auto/manager.py:501` 构造同一个类、共享同一份 `settings`）。v1 明写「两个 manager 无需改动、`FeeLedger` 由 `__init__` 内部按 settings 自建」⇒ 默认开启的 `ai_trade_fee_accounting_enabled` 会让**非 AI** 的 live_auto 成交也开始带非零 `fee`（前端 `TradeTooltip.tsx:73` 直接展示），且 L1 打开净额口径后 `live_auto/session.py:345` 的**会话级金额止盈止损判据一并变严**——以 `ai_trade_` 冠名的开关跨产品线改了另一条链路的真实平仓行为。Z-1 只断言 payload 逐字节相同，抓不到这类改动 | 给 `LiveExecutor.__init__` 增第七个**默认 `None`** 的 keyword-only 缝 `fee_ledger`，**只有** `ai_auto/manager.py` 传（+2 行）；`None` ⇒ `trade["fee"]` 恒 `0.0`、`_close_pnl` 恒毛口径 ⇒ live_auto 逐字节回退。见 §3.2.0 / §3.3 / §4.2 / Z-7 |
| RV-3 | **P1** | §3.3 | `entry_fee_usd` 的复位点是**三处**不是两处：`_settle_close`(`:523`)、`_settle_benign_close`(`:542`)、`_on_reconcile_ok`(`:600`) 都把 `entry_qty_sz` 归零。漏掉第三处 ⇒ 交易所侧 / 外部干预平仓后开仓费残留，被**下一笔**平仓重复扣一次 | §3.3 补齐三处并列为放行条件；§7.2 加对应用例 |
| RV-4 | **P1** | §3.2.4 / §6.3 | OKX 的 `fee` 是**「收费为负、返佣为正」的有符号量**，`_normalize_fill`(`trading.py:475`) 原样透传。若不做符号归一，`measuredFeeBps` 会是负数、`feeDeviationBps ≈ −2×feeBps`，R-4 那条「长期 ≈ taker−maker 即说明挂价激进」的探针直接失效并会诱发错误的运维处置 | §3.2.4 显式规定归一规则与 rebate 分流；§7.2 加负值样本用例 |
| RV-5 | **P1** | §3.3 | 部分成交按**哪个量**计费未写。`_adopt_partial → _adopt_fill(order, avg_px, acc, ts, delta_sz=delta)` 里 `total_sz` 是**累计**量、`delta_sz` 才是本轮增量；按 `total_sz` 累加会把开仓费重复计入（两次部分成交即高估约 1.5 倍）。v1 的 §7.2 用例 2 想抓的正是这个，但设计正文没给判据 | §3.3 写死按 `delta_sz` 计，且与 `trade["qty"]` 同口径 |
| RV-6 | **P1** | §5.4 / §7.5 | `AiTradeStatusOut` 只有 `{group, aiTradeEnabled}`（`schemas.py:630-632`），**没有任何 `stats` 字段**；`_compute_ai_stats` 的唯一调用点在 `_finalize_run`。故 v1「新统计键随既有 `GET /ai-trade/status` 自然下发」与事实不符，而 L0/L1/L2 的放行判据全部是**会话运行期内**的「滚动 50 笔 / 滚动 200 轮」——按 v1 的落点，运维要观察这些数必须先把会话停掉 | §5.4 改为如实描述；新增**会话内**可观测通道（`cost_snapshot` 随既有 `edge_gate` / `status` 事件下发），§7.5 据此重写观察方式 |
| RV-7 | **P1** | §3.6 | 「会话止盈距离」指代不明：session 上另有一个**会话级金额**阈值 `self.take_profit_usd`（`session.py:114`，`_maybe_session_exit` 用，单位 USD），与本节想用的**单仓 OCO 止盈百分比**（`executor.risk.take_profit_pct`）量纲完全不同，极易错接；且 `take_profit_pct` 允许为空 | §3.6 逐字点名取数路径，并补 `None` 时的退化行为 |
| RV-8 | **P1** | §3.10 | `executor.py` 的 `+38` 行硬预算与 §4.2 列举的改动量不自洽（逐项估算约 42 行），而 §3.10 写明「超出即判不合格」。一个自身不可达的验收阈值只会逼实现节点做假账 | 把审计取数与节流整体下沉 `fee_ledger.py`，`executor.py` 只留委托；预算改为可核对的 **≤ +32**，并增一条 `git diff --numstat` 机械判据（Z-8） |
| RV-9 | P2 | §4.2 | `trace.PHASES`（`trace.py:25`）是前后端镜像常量（`aiTradeTypes.ts:184` 有对应注释），v1 只说新增 `edge_gate_event`，没说把 `"edge_gate"` 加进 `PHASES` | §4.2 补 |
| RV-10 | P2 | §3.2.3 | 市价腿的 `trade["price"]` 本就是 `ref_px`（`_do_open:281` / `_settle_close:526`），故 `expectedPx == price`，滑点**只能**由 fills 审计产出；模型费率的名义额同样用 `ref_px` 而非真实成交价 | §3.2.3 写明，避免实现节点试图从 trade 字段自行推滑点 |
| RV-11 | P2 | §8 R-9 | 「须在集成测试中以 5 成员分组跑一遍确认无 429」——§7.3 全部脱网打桩，桩测不可能证伪 429 | 改为断言**调用次数上界** |
| RV-12 | P2 | §3.7 | 门禁只拦 `open_*`（与 `base_rules._GATED_ACTIONS`，`base_rules.py:44` 同构），但**反手轮**（持空时 `open_long`）被降级为 hold 会把仓位留在原方向，严格说不等于「更保守」。铁律层已有同样性质，属既定先例，但 spec 不该默认它是无代价的 | §3.7 显式声明该继承与其代价 |
| RV-13 | P2 | §6.3 | `feeCoverage` 定义为「平仓样本中 fee != 0 的占比」，而 G1 表述是「每笔平仓成交都带非零费率」；开仓腿也记费却不进该分母，键名易被误读为全量 | 更名 `closeFeeCoverage` 并写清分母 |
| RV-14 | P2 | §3.6 | 「共用同一条降级判据 `sanitize_atr`（`profit_guard.py:283`）」——实盘侧实际调用的是其包装 `safe_atr`（`profit_guard.py:296`），回测侧才直接调 `sanitize_atr` | 表述精确化 |
| RV-15 | P2 | §4.2 | `okx/trading.py` 记 `+1`，但新增 `feeCcy` 还需同步 `_normalize_fill` 的 docstring 契约段（该 docstring 逐条列举了回带字段） | 改 `+2` |

**未采纳的疑虑（记录以免下轮重开）**：① 「费率入账应改用交易所实测值」——§3.2.1 已给出充分理由（可用性耦合 + D-14 回测同构），**维持确定性入账**；② 「edge 门禁应 fail-closed 与铁律一致」——§3.7 的暖机论证成立且本仓库有「反思致永不开单」实案，**维持 fail-open**；③ 「`crossing_legs` 泛化偏离 01 号文档」——退化条件成立（市价时 `2×`），属严格泛化，**维持**。

---

## 一、概述（Overview）

AI 自动交易实盘持续亏损的第一根因不是某个 bug，而是**盈亏口径本身是错的**。经回读源码确认：`LiveExecutor._close_pnl` 是纯价差乘数量（`services/quant/app/live_auto/executor.py:732-738`），每条成交记录的费率被硬编码为 `"fee": 0.0`（`executor.py:779`），而只读属性 `session_pnl_usd = 已实现 + 未实现`（`executor.py:147-149`）正由它累加。这个被高估的数字同时喂给四个消费方：会话级金额止盈止损（`ai_auto/session.py:898-907`）、利润保护棘轮、统计看板、以及决策 prompt 里的「会话盈亏」。结论是**每一笔往返都被系统报得比真实结果好约一个往返成本**，模型在系统性乐观的反馈里学习与决策。与之对称的另一侧是：`default_taker_fee_bps = 5.0` / `default_slippage_bps = 2.0`（`config.py:280-281`）的全部消费方是 `SimBroker`、AI 回测与纸面/影子路径，`LiveExecutor` 全链路从不引用它们——也就是说成本既不进实盘盈亏，也不进任何决策条件。

本次改造把系统从**叙事驱动**改造成**期望值驱动**的第一步：先把成本记进账，再把「期望毛收益覆盖不了成本」的开仓轮拦掉。归因恒等式是 `E = p·W − (1−p)·L − C`；仓库里已有的三层确定性保护（基础策略铁律 `ai_auto/base_rules.py`、下单前反思 `ai_auto/reflection.py`、利润保护棘轮 `ai_auto/profit_guard.py`）解决的都是等式左侧「少犯错」，**没有任何一层回答「这笔交易的期望收益是否覆盖成本」**。本 spec 新增的就是这一层，并刻意与前三层同构：纯函数核心 + session 薄封装、显式入参、只会更保守、`close`/`cancel_order` 永不被拦、实盘与回测共用同一核心、默认关闭时逐字回退。

交付分三块，顺序不可颠倒：**① A0-1「只记录不切换」**——每笔成交按确定性费率模型记 `trade["fee"]`，并旁路用交易所真实成交明细（`okx/trading.py:454 fetch_live_fills`，已存在且已归一出 `fee`/`fillPx`/`clOrdId`）度量实测费率与实测滑点，二者偏差进归因表；**② A0-2「切换判据」**——`_close_pnl` 扣双边费用，`session_pnl_usd` 随之变净额，由独立开关 `ai_trade_net_pnl_enabled` 控制且**默认 `False` ⇒ 逐字回退毛口径**；**③ 最小 edge 门禁**——新增 `cost_model.py` / `edge_gate.py` 两个纯函数模块与一个资金费率只读取数通道，把 `毛 edge(bps) ≥ k_min × C(bps)` 插进既有确定性链的「共识门禁之后、反思之前」，`k_min` 默认 `0.0`（空转），运维按本 spec §7.5 的启用 runbook 逐级放开。

**为什么阶段一不做别的**：08 号路线图的第一条纪律是「阶段一不加任何新的保护层，只做两件事——把成本记进账，把不值得做的交易拦掉」。信号先验（02）、因子接入（03）、仓位重构（05）、五道闸门（06）全部依赖 A0 落地后的干净标签，在此之前推进它们等于在被污染的标签上做工程。

---

## 二、目标与不做什么

### 2.1 目标（可验收）

| 编号 | 目标 | 对应根因 | 判据 |
|---|---|---|---|
| G1 | **AI 会话**每笔平仓成交都带非零、可解释的费率（非 AI 的 live_auto 不受影响，§3.2.0） | D-16 | `closeFeeCoverage == 1.0`（滚动 50 笔） |
| G2 | maker 腿与 taker 腿在成本口径上可区分 | D-17 | 每条成交的 `explain.meta.ordType` 与 `feeBps` 非空 |
| G3 | 实测滑点被观测并可与模型对账 | D-18 | `explain.meta.expectedPx` 存在；归因表出 `slipBps` |
| G4 | 存在一个可被实盘数据校准的往返成本函数 `C` | D-1 | `estimate_round_trip_cost` 纯函数 + 单测覆盖六个分支 |
| G5 | 永续资金费率有取数通道且缺席时显式降级 | D-2 | `GET /api/v5/public/funding-rate` + 会话级缓存；缺失 ⇒ `confidence="degraded"` |
| G6 | 期望毛收益不足以覆盖 `k_min × C` 的开仓轮降级为 hold | D-1 | `apply_edge_gate` 落在 `session.py:575` 与 `:578` 之间；回测同位插入 |
| G7 | 净期望 / 成本占比 / 门禁拦截率可被度量，且**会话运行期内**可读 | D-15 | `aiStats` 新增 `cost` / `edgeGate` / `decisionConversionRate` 三组加法键（§6.3）；会话内经 `costSnapshot` 下发（§5.4） |
| G8 | 全部新机制可独立回滚，关闭后逐字回退当前行为 | 08 §六 | 开关矩阵 §3.9，每个开关一条零回归单测；作用域缝 §3.2.0 由 Z-7 钉死 |

### 2.2 不做什么（本轮 Non-goals，明确留给后续迭代）

1. **不新建限价能力**——`ai_trade_limit_orders_enabled` 默认 `True`、状态机在 `live_auto/resting.py`、TTL 在 `config.py:169`，全部已上线。本轮只补「限价腿走 maker 费率」的记账区分，**不动挂价策略、不动 TTL 自适应**（那是 01 号文档 §3.4，留给阶段二）。
2. **不改 prompt 一个字节**。成本快照进 payload 的新键 `cost`，而 `prompts.py:444-490` 只读显式键（`round/lastPx/position/profitGuard/...`），不存在「payload 全量渲染」路径，故 prompt 渲染逐字节不变、模型行为零回归。EV 目标函数写进 prompt 是 04 号文档的 `edge_decision_fields_enabled`，属阶段二。
3. **不做信号先验（02）/ 因子接入（03）/ 仓位重构（05）/ 元标签（阶段三）**。阶段一的 edge 分子是**显式标注的粗估**，M1 只看结构性指标（入账完整性、决策转化率、归因覆盖率），**不看净期望**（08 §六 自我反驳二）。
4. **不引入新的第三方 Python 包**。资金费率走新增的 OKX 只读端点，`services/quant/requirements.txt` 不变。
5. **不新建数据库表、不做任何迁移**。归因与统计全部走既有 `metrics_json` / 内存环形缓冲。
6. **不改动交易所侧 OCO 行为**，不改 `_risk_trigger_px`、不改利润保护棘轮的任何判据。
7. **不重构 `session.py` / `executor.py`**。二者已超 `CLAUDE.md` 800 行红线，本轮只允许新增**薄封装调用**（见 §3.10 行数预算），拆分本身是本计划之前就存在的技术债。

---

## 三、技术设计

### 3.1 总体架构与数据流

```
                 ┌────────────────────────── 决策轮（无网络 IO）──────────────────────────┐
 确认K线/墙钟唤醒 → _build_decision_payload → decider.decide → 铁律 → 冷却 → 共识
                                                                            ↓
                                                          ★ apply_edge_gate（本次新增）
                                                                            ↓
                                                                   反思 → _execute_decision
                 └───────────────────────────────────────────────────────────────────────┘
                                        ↑ 读                              ↓ 成交事件
                        ┌───────────────┴────────────┐          ┌─────────┴──────────┐
                        │ cost_model.estimate_...    │          │ FeeLedger（费率记账）│
                        │ edge_gate.estimate_gross_..│          │  · 确定性费率入账     │
                        └───────────────┬────────────┘          │  · 待审计挂账        │
                                        │ 读                     └─────────┬──────────┘
                        ┌───────────────┴────────────┐                    │
                        │ FundingCache（会话级快照）  │                    │ _dispatch（唯一收口）
                        │  refresh 落在 _monitor_tick │                    ↓
                        └───────────────┬────────────┘          ┌────────────────────┐
                                        │ 5s 盯市 tick            │ AttributionLedger  │
                     okx/rest_client.get_funding_rate            │  滚动 200 笔往返   │
                                                                 └─────────┬──────────┘
                     okx/trading.fetch_live_fills ──→ FeeLedger.resolve ────┘
                     （旁路审计，≥60s 一次，绝不进决策轮）        stats_ext.build_cost_stats
```

三条硬约束贯穿全篇：

- **决策轮内零网络 IO**。资金费率、成交明细审计一律跑在 5s 盯市 tick（`session._monitor_tick`）上；决策轮只读会话级内存快照。
- **盈亏口径必须确定性**。见 §3.2 的关键设计决策 —— `trade["fee"]` 由**本地确定性费率模型**产出，交易所实测值只进归因与告警，**绝不回写盈亏**。
- **门禁只会更保守**。`apply_edge_gate` 的唯一可能输出变化是 `open_* → hold`；`close` / `cancel_order` / `hold` 永不被触碰，与 `apply_ma_band_gate`（`base_rules.py:211`）、`apply_consensus_gate`（`signals.py:154`）逐字同构。

### 3.2 A0-1 · 费率入账（记录层）

#### 3.2.0 作用域硬约束：本特性只作用于 AI 自动交易，不得外溢到 live_auto（v2 · RV-2）

**`LiveExecutor` 是 AI 与非 AI 两条产品线共用的类**：`ai_auto/manager.py:590` 与 `live_auto/manager.py:501` 各构造一份，且**共享同一份 `settings`**。若按 v1 的写法把 `FeeLedger` 放进 `LiveExecutor.__init__` 内部按 `settings` 自建，`ai_trade_*` 前缀的开关就会跨产品线改写 `live_auto`：

- `ai_trade_fee_accounting_enabled` 默认 `True` ⇒ **非 AI** 的 live_auto 成交也开始带非零 `fee`，前端 `TradeTooltip.tsx:73` 直接展示；
- L1 打开 `ai_trade_net_pnl_enabled` 后，`live_auto/session.py:345` 的**会话级金额止盈止损**判据一并变严 ⇒ 一条与本特性无关的产品线的**真实平仓时点**被改变。

故本特性的作用域由**构造缝**而非全局配置决定：

```python
def __init__(self, *, …既有形参逐字不变…,
             place_order_fn=None, close_position_fn=None, positions_fn=None,
             cancel_order_fn=None, get_order_fn=None,
             fee_ledger: "FeeLedger | None" = None):    # 第七缝，默认 None
    …
    self.fee_ledger = fee_ledger
    self.entry_fee_usd = 0.0
```

- `fee_ledger is None`（**`live_auto` 的全部路径、以及全部既有单测**）⇒ `trade["fee"]` 恒 `0.0`、四个加法键整键缺席、`_close_pnl` 恒走毛口径 ⇒ **逐字节回退**，`ai_trade_*` 开关取任何值都不影响 live_auto。
- 只有 `ai_auto/manager.py:590` 传 `fee_ledger=FeeLedger(ct_val=…, params=resolve_fee_params(settings))`（+2 行）。**这是 §4.2 相对 v1 的唯一新增改动点**。
- 由此 `ai_trade_fee_accounting_enabled` / `ai_trade_net_pnl_enabled` 两个开关只在 `FeeLedger` 内部被读取，语义收敛为「AI 会话的记账开关」，与前缀名一致。

零回归判据见 §7.1 的 **Z-7**（live_auto 成交的键集合与 `pnl` 位相同）。

#### 3.2.1 关键设计决策：确定性入账 + 实测旁路校准

01 号文档原文写的是「成交回执里的真实 `fee` 与 `fillPx` 落进 `trade["fee"]`」。**本 spec 有意偏离这一句，并给出理由**（下游实现须按本节执行，不按 01 号文档字面执行）：

- OKX 的下单回执（`place_live_order` 返回 `{ok, ordId, clOrdId, code, message}`，`trading.py:176-177`）**不含费率**；费率只能经 `GET /trade/order`（`_normalize_order`，`trading.py:364-375`，当前也未取 `fee`）或 `GET /trade/fills`（`_normalize_fill`，`trading.py:468-485`，**已经取了 `fee`**）二次查询获得，且成交后瞬间未必已结算。
- 若把 `trade["fee"]` 绑在一次可能失败/延迟的 REST 查询上，等于把「盈亏是否正确」变成**网络可用性的函数**；更糟的是回测世界根本没有这次查询，会立刻复现 D-14「回测与实盘决策链不同构」。
- 故：**费率入账用本地确定性模型**（`notional × feeBps`，`feeBps` 按腿的 `ordType` 取 maker/taker），**由构造保证「费率入账完整性 = 100%」**，不依赖任何外部系统的可用性；**实测 `fee`/`fillPx` 的价值是校准与证伪**，进 `explain.meta` 与归因表，度量「模型费率 vs 实测费率」「期望成交价 vs 实测成交价」两个偏差，超阈告警并要求人工调整 `ai_trade_taker_fee_bps` / `ai_trade_maker_fee_bps`。

#### 3.2.2 费率归属规则（D-17）

| 腿 | `ordType` 判据 | 费率 |
|---|---|---|
| 开仓（市价） | `_plan_limit_px(order_plan) is None`（`executor.py:250`） | `ai_trade_taker_fee_bps`（默认 5.0） |
| 开仓（限价挂单成交） | `_adopt_fill` 路径，`order.intent == "open"` | `ai_trade_maker_fee_bps`（默认 2.0） |
| 平仓（市价 / OCO / 外部干预合成） | `_settle_close` / `_settle_benign_close` / `_synthesize_external_close` | `ai_trade_taker_fee_bps` |
| 平仓（限价挂单成交） | `_adopt_close_fill` 路径 | `ai_trade_maker_fee_bps` |

**限价挂单未必成交在 maker 侧**（越过盘口的限价单会被 taker 撮合）。本轮按 maker 计并把差额交给实测校准发现：若归因表上 `feeDeviationBps` 长期为正且约等于 `taker − maker`，即说明限价挂价过于激进，处置见 §8 风险 R-4。

#### 3.2.3 成交记录与 explain.meta 的新字段（全部加法，缺失即省略键）

`_make_trade`（`executor.py:772`）新增两个 keyword-only 形参 `ord_type: str = "market"` / `expected_px: float | None = None`，默认值保证 `live_auto` 的全部既有调用点 payload 逐字节不变。`trade` 新增/改写：

| 字段 | 取值 | 备注 |
|---|---|---|
| `fee` | `FeeLedger.leg_fee(sz=…, px=…, ord_type=…)`（USD，**正数=支出**） | 开关关时逐字回退 `0.0` |
| `feeBps` | 该腿实际适用的 bps | 加法键 |
| `ordType` | `"market"` \| `"limit"` | 加法键 |
| `expectedPx` | 市价腿 = 下单时 `ref_px`；限价腿 = `order.px` | 加法键，供 D-18 计算滑点 |

**市价腿的 `expectedPx` 与 `price` 必然是同一个数（v2 · RV-10）**：`_do_open`（`executor.py:281`）与 `_settle_close`（`:526`）传给 `_make_trade` 的 `price` 本就是 `ref_px`——本仓库从不回查市价单的真实成交价。两个直接后果，实现节点必须认账：① **滑点只能由 §3.2.4 的 fills 审计产出**，严禁试图从 `trade` 的 `price`/`expectedPx` 之差推导（那恒为 0）；② 模型费率的名义额同样以 `ref_px` 而非真实成交价计，存在一个与滑点同量级（默认假设 1~2 bps）的系统性小偏差，它被 `feeDeviationBps` 覆盖观测，不单独建模。

`_inject_explain_meta`（`session.py:248-261`，全仓库**唯一**的 explain 注入收口）新增四行 `setdefault`：`feeUsd` / `feeBps` / `ordType` / `expectedPx`，取值直接从 `trade` 搬运。**只加字段、不加收口**。

#### 3.2.4 实测审计通路（D-18）

- `LiveExecutor.__init__` 新增第六个可注入回调 `fills_fn=None`，默认 `self._default_read_fills`（`fetch_live_fills(self.creds, self.inst_id, limit=100)`），与既有五缝（`place_order_fn` 等，`executor.py:115-120`）完全同构，脱网单测靠它。
- `reconcile()` 末尾新增**一行委托**：`await audit_fills(self.fee_ledger, fills_fn=self._fills_fn, now_wall=time.time(), settings=self.settings)`（`fee_ledger is None` 时该自由函数直接返回 `0`）。**v2 · RV-8：节流（`ai_trade_fill_audit_interval_s`）、总闸（`ai_trade_fill_audit_enabled`）、`has_pending()` 短路、`try/except` 全吞（best-effort，绝不影响盯市主流程）、以及交给 `FeeLedger.resolve` 的整段流程，全部在 `fee_ledger.py` 的 `audit_fills` 里**——v1 把这 ~10 行放在 `executor.py` 的 `_audit_fills` 上，与 §3.10 的行数预算直接冲突，且那是业务逻辑而非薄接线。
- `_normalize_fill`（`trading.py:468`）新增一个键 `feeCcy`（当前被丢弃）。**非 USDT 计价的 `feeCcy` 一律跳过该样本并计入 `feeAuditSkipped`**，绝不按面值混记（币本位合约的 fee 以币计价，直接相加是量纲错误）。
- **OKX `fee` 的符号必须归一（v2 · RV-4，阻断级）**。OKX 的 `fee` 字段是**有符号量：负数 = 平台向你收取，正数 = 返佣**，而 `_normalize_fill`（`trading.py:475`）原样透传。本 spec 全篇的费率口径是「**正数 = 支出**」，故 `FeeLedger.resolve` 必须在入表前归一：
  - `fill["fee"] < 0` ⇒ `measured_fee_usd = -fill["fee"]`（正常路径，计入 `feeAuditSamples`）；
  - `fill["fee"] > 0` ⇒ 是**返佣**，`measured_fee_usd = -fill["fee"]`（保持「支出为正」⇒ 返佣为负），并另计 `feeRebateSamples`，**不混入** `measuredFeeBps` 的均值（返佣样本与收费样本混一起求均值会把 R-4 的探针读数拉向 0）；
  - `fill["fee"] == 0` ⇒ 未结算，**挂账保留、不消化**，等下一轮审计。
  - 漏掉这一步的后果不是精度问题而是**方向问题**：`measuredFeeBps` 会是负的、`feeDeviationBps ≈ −2×feeBps`，于是 R-4 那条「长期 ≈ `taker − maker` 即说明挂价激进」的探针恒不触发，且 §7.5 的「按实测重标 `ai_trade_taker_fee_bps`」会把费率往错误方向调。
- 滑点定义（有符号，**正数 = 不利**）：买方向（`open_long` / `close short`）`slipBps = (measuredPx − expectedPx)/expectedPx × 1e4`；卖方向取相反数。

### 3.3 A0-2 · 净额 PnL 口径切换

**开关**：`ai_trade_net_pnl_enabled: bool = False`。关闭时 `_close_pnl` 逐字回退今日实现（纯价差乘数量）。

**实现**：`executor.py` 新增实例字段 `self.entry_fee_usd: float = 0.0`（开仓腿费率累加器）。它有两条必须逐字执行的不变式：

**(a) 累加按「本轮采纳增量」而非累计成交量（v2 · RV-5）。** `_adopt_partial`（`executor.py:449-462`）调用的是 `_adopt_fill(order, avg_px, acc, ts, delta_sz=delta)`——其中 `total_sz=acc` 是**累计**成交量、`delta_sz` 才是本轮新增。故费率一律按

```python
adopted = delta_sz if delta_sz is not None else total_sz
self.entry_fee_usd += self.fee_ledger.leg_fee(sz=adopted, px=avg_px, ord_type="limit")
```

与同一处 `_make_trade(..., sz=delta_sz if delta_sz is not None else total_sz, ...)` **同口径**（费率与 `trade["qty"]` 描述的必须是同一批张数）。按 `total_sz` 累加会把开仓费重复计入——两次部分成交即高估约 1.5 倍，三次约 2 倍。

**(b) 复位点是三处，不是两处（v2 · RV-3，阻断级）。** 全仓库把 `entry_qty_sz` 归零的地方有三个，`entry_fee_usd` 必须与之**逐处并列**清零：

| # | 位置 | 场景 |
|---|---|---|
| 1 | `_settle_close`（`executor.py:523`） | 主动平仓 / 限价平仓采纳（`_adopt_close_fill` 复用它） |
| 2 | `_settle_benign_close`（`executor.py:542`） | 平仓收 `LIVE_NO_POSITION`（交易所侧 OCO 已先平） |
| 3 | `_on_reconcile_ok`（`executor.py:600`） | 盯市侦测到交易所侧 / 外部干预已平仓 |

漏掉第 3 处的后果是**静默的**：外部干预平仓后开仓费残留在累加器里，被**下一笔**平仓再扣一次，净额盈亏出现一个恒为负、随外部平仓次数累积的偏差——而这恰恰是 A0-2 想要修正的那类误差。放行条件见 §7.2 `test_ai_trade_fee_ledger.py` 用例 8。

`_close_pnl` 改为两行：

```python
def _close_pnl(self, exit_px: float) -> float:
    gross = self._gross_close_pnl(exit_px)          # 原逻辑原样搬入（零行为改动）
    if self.fee_ledger is None:                     # v2 · RV-2：live_auto 恒走这一支
        return gross
    return self.fee_ledger.net_close_pnl(gross, entry_fee_usd=self.entry_fee_usd,
                                         exit_px=exit_px, sz=self.entry_qty_sz)
```

`FeeLedger.net_close_pnl` 在开关关时**恒返回 `gross` 本身**（同一个 float，等价性由单测钉死），开关开时返回 `gross − entry_fee_usd − 平仓腿费率`。

**`_close_pnl` 在一次平仓里会被调用 2~3 次**（`_close_explain:817` → `_settle_close:516` → `_close_return_pct:749`；良性 / 外部平仓路径则是 `_synthesize_external_close:835` + 调用方各一次）。它是**无副作用的纯读**——`entry_fee_usd` 只在上表三处复位、绝不在 `_close_pnl` 内被消费——故重复调用恒得同一个数，不存在重复扣费。实现节点**严禁**把「扣完即清零」的写法塞进 `_close_pnl`。

**四条必须同时认账的连带影响**（下游实现须逐条验证，任何一条被漏掉即为不合格）：

1. `session_pnl_usd`（`executor.py:147-149`）随之变净额 ⇒ **会话级金额止盈止损**（`session.py:898-907`）判据变严（同样行情下更晚触发止盈、更早触发止损）。这是**期望内的正确行为**，须在切换前的用户告知文案里写明。
2. `_close_return_pct`（`executor.py:740-749`）分子调用 `_close_pnl`，故 `pnlPct` 同步变净额。分母（开仓占用保证金）不变。
3. `_close_explain`（`executor.py:815-828`）与 `_synthesize_external_close` 里的 `pnl` 同步变净额，UI 解释文案自动一致，**无需改文案**。
4. **利润保护棘轮判据不受影响**：它读的是本地派生浮盈（`_unrealized_at`）而非 `realized_pnl_usd`，本轮不动 `_unrealized_at`。既有的 `ai_trade_profit_guard_breakeven_offset_return_pct = 0.002`（`config.py:223`）本就是为「本地浮盈不含费用」而设的补偿项，切换后**仍然需要**、**不得删除**。
5. **`live_auto`（非 AI）的同名判据必须完全不受影响**（v2 · RV-2）：`live_auto/session.py:345` 也有一份读 `session_pnl_usd` 的会话级金额止盈止损。由 §3.2.0 的 `fee_ledger is None` 缝保证——live_auto 的 `_close_pnl` 恒返回 `gross`，与本特性合入前位相同。这一条由 **Z-7** 钉死，不靠约定。

### 3.4 往返成本模型（新模块 `ai_auto/cost_model.py`）

公式（对 01 号文档 §3.2 的公式做了一处**显式泛化**，见下）：

```
C = feeOpenBps + feeCloseBps + crossingLegs × (halfSpreadBps + impactBps) + fundingCarryBps
```

- `feeOpenBps` = maker（`ordType=="limit"`）或 taker；`feeCloseBps` **恒取 taker**——开仓时平仓腿的委托类型未知，保守取贵的一侧。
- `crossingLegs` = 市价开仓 `2`、限价开仓 `1`。**泛化理由**：01 号文档写的是固定 `2×`，但 maker 腿按定义是被动方、不吃半价差；固定 `2×` 会抹掉 maker-first 的一半收益来源，与 D-17 要解决的问题自相矛盾。`ordType=="market"` 时本式退化为文档原式，故是严格泛化而非改口径。
- `halfSpreadBps`：本轮**无盘口数据**（D-3：AI 会话构造的是 candle-only 包装器，`session.py:275` → `ws_client.py:204`，books5 通道虽已实现但未订阅），故恒走 `ai_trade_half_spread_fallback_bps` 默认 `1.0`。形参保留 `half_spread_bps: float | None`，阶段二接盘口时只改调用方。
- `impactBps` = `ai_trade_impact_bps` 默认 `0.5`（单笔名义额远小于盘口深度时的保守常数）。名义额自适应放大留给阶段二。
- `fundingCarryBps`：见 §3.5。**方向感知且只取不利侧**——`signed = fundingRate × 1e4 × periods × (+1 if side=="long" else −1)`，`fundingCarryBps = max(0.0, signed)`。收资金费率的一侧**不给 edge 折扣**（绝不因预期收费而放松门禁），付费的一侧全额计入。`periods = ceil(expectedHoldBars × barSeconds / fundingIntervalS)`。
- `confidence`：`funding_rate is None` ⇒ `"degraded"`，否则 `"ok"`。**取数失败绝不静默按 0 计**。

默认参数下的标定值（用于对账，实现完成后须与单测断言一致）：市价往返 `5 + 5 + 2×(1.0 + 0.5) = 13.0 bps`（`fundingCarryBps=0` 时）；限价开仓 + 市价平仓 `2 + 5 + 1×(1.0 + 0.5) = 8.5 bps`。

### 3.5 资金费率取数与缓存（D-2）

仓库当前**没有**该端点：`okx/rest_client.py:226-236` 的唯一公共只读调用是 `/api/v5/public/instruments`。

- **新增** `OKXRestClient.get_funding_rate(inst_id) -> dict | None`，走 `GET /api/v5/public/funding-rate`（公共、无签名），归一为 `{"fundingRate": float, "fundingTs": int, "nextFundingTs": int, "intervalS": float}`；`intervalS = max(60.0, nextFundingTs − fundingTs)`，缺失回落 `28800.0`（8h）。进程内按 `instId` 短 TTL 缓存，**完全镜像 `_inst_meta_cache` 的既有写法**（`rest_client.py:232-234, 250`）。
- **新增** `ai_auto/funding.py`：`FundingCache(inst_id, *, ttl_s, stale_factor=5.0)`，`async refresh(fetch_fn, now_wall) -> bool`（未到 TTL 直接返回 `False`；异常一律吞掉并 `logger.debug`，**绝不上抛**）、`snapshot(now_wall) -> FundingSnapshot | None`（超过 `ttl_s × stale_factor` 即判定陈旧返回 `None`）、`wire(now_wall) -> dict | None`。
- **接线**：`session._monitor_tick` 末尾加一行 `await self._funding.refresh(self._fetch_funding, time.time())`。5s 盯市 × TTL 60s ⇒ 每分钟至多一次公共请求，配额压力可忽略。决策轮只调同步的 `snapshot()`。
- **总闸** `ai_trade_funding_enabled: bool = True`。**关闭时传 `funding_rate=0.0`（不是 `None`）** ⇒ `fundingCarryBps = 0.0` 且 `confidence` 保持 `"ok"`：「关掉资金费率建模」是一次**有意的选择**，不是数据缺失，二者不得混为一谈。

### 3.6 粗估毛 edge（新模块 `ai_auto/edge_gate.py`）

阶段一没有 02 号文档的概率化信号，故 edge 的分子是**显式标注的粗估**，并刻意选择一个**不依赖未校准量**的定义：

```
毛 edge(bps) = min( atr_target_mult × ATR(bps) ,  单仓 OCO 止盈距离(bps) )
```

- **为什么不用 `decision.confidence`**：D-7 已确认置信度未校准、门槛 `ai_trade_min_confidence` 默认 `0.0` 形同虚设。把未校准的置信度当胜率先验用，会把 D-7 的错误传播进门禁，且无法证伪。阶段一坚持只用**可观测的行情量**。
- **为什么用 ATR**：本层实际拦的是「在一个真实行程只有 20 bps 的死水行情里，去做一笔往返成本 13 bps 的交易」——这正是系统性小额亏损的结构性来源，也是唯一不需要胜率先验就能判定的一类负期望。ATR 在实盘与回测两侧都已存在（`session._atr_px`，`session.py:520-521`；`backtest._atr_at(i)`，`backtest.py:398-407`），共用同一条降级判据：实盘走包装 `safe_atr`（`profit_guard.py:296`）、回测直接调 `sanitize_atr`（`profit_guard.py:283`），二者是同一个判据的两个入口（v2 · RV-14）。**本模块不得再引第三个 ATR 取数口径。**
- **上界项是「单仓 OCO 止盈距离」，取数路径逐字点名（v2 · RV-7）**：
  - 实盘 = `session.executor.risk.take_profit_pct`（`RiskParams`，由 `AiTradeManager._resolve_trade_risk` 夹紧后注入）；
  - 回测 = `runner._oco_risk.take_profit_pct`（`backtest.py:245-247`，源自 `spec.take_profit_pct`）。
  - **严禁错接 `session.take_profit_usd`**（`session.py:114`，`_maybe_session_exit:902` 用）——那是**会话级金额**阈值，单位是 USD 而非百分比，量纲完全不同，错接会产生一个数值上"看起来合理"却毫无意义的上界。
  - `take_profit_pct is None`（默认允许为空，`spec.take_profit_pct or None`）⇒ **上界整体不生效**，`target_bps` 退化为纯 ATR 预算、`source == "atr"`；`EdgeEstimate.take_profit_bps` 置 `None`。它只**收窄**、绝不放大 edge（模型再有想法也拿不到超过 OCO 止盈的利润）。
- **判定价单点收敛**：复用既有 `base_rules.entry_reference_px(decision, last_px)`（`base_rules.py:169-177`）——限价委托判**委托价**、市价判最新价。**严禁在本模块另写一份判定价逻辑**，那正是 base_rules 特性交付时定下的规矩。
- **`hold_exit_px`（承诺退出线）本轮不参与**：`decider.TradeDecision` 的契约是「仅 `action=hold` 携带的合法值有意义」（`decider.py:121-123`），门禁面对的恒是 `open_*`，取到的必是 `None`。写进来是死代码。留给阶段二 02 号先验落地后重估。
- **ATR 不可用 ⇒ 返回 `None` ⇒ 门禁 fail-open 放行 + 计 `unavailable`**（见 §3.7 的极性论证）。

### 3.7 最小 edge 门禁与其插入位置

**判据**：`edge.target_bps ≥ k_min × cost.total_bps × (degraded_mult if cost.confidence=="degraded" else 1.0)`

**降级码**：`edge_below_cost(edge=8.1bps,cost=13.0bps,k=2.0)`（与既有 `low_consensus(k/n)`、`base_rule_ma_band(pctB=0.42)` 同为带参数后缀的前缀码风格）。

**极性 = fail-open（与铁律层相反，与利润保护层相同）**。这是本 spec 最重要的一次取舍，必须写明理由：

- `base_rules` 之所以敢 fail-closed，是因为它的输入（MA 通道）确定性极强、几乎恒可用（只需 `period+1` 根 K 线）。
- 本层的输入是 ATR，而 `ewm(min_periods=period)` 的前 `period−1` 行恒为 NaN，暖机在「无 HTF + 关闭体制上下文 + 策略暖机很小」的组合下可以小于 14（`backtest.py:399-403` 已记录过这个坑）。一个 fail-closed 的粗估门禁会在这些断面上**把开仓全部否掉**——这正是本仓库已经发生过的「反思致永不开单」事故（`docs/diagnoses/`，`prompts.py:527-530` 留有记录）。
- 故：估算不可用 ⇒ **放行** + `edgeGateStatus="unavailable"` + 计数；连续 `_EDGE_UNAVAILABLE_ALERT_ROUNDS = 20` 轮不可用即推一条 `AI_TRADE_EDGE_GATE_UNAVAILABLE` error 事件（镜像 `_BAND_UNAVAILABLE_ALERT_ROUNDS` 的既有写法，`session.py:788-793`）。**静默的降级与静默的保守一样，都会被用户当成故障**。

**豁免矩阵继承自铁律层，其代价须显式认账（v2 · RV-12）**：门禁只作用于 `("open_long", "open_short")`，与 `base_rules._GATED_ACTIONS`（`base_rules.py:44`）逐字同构。由此产生一个**已被既有铁律层接受**的性质：持空仓时模型给出 `open_long`（反手意图），被降级为 `hold` 的结果是**留在原来的空仓里**，而不是回到空仓位——严格说这不是「更保守」，只是「不新增反向敞口」。本 spec 明确**继承**该行为而非另设例外，理由是：① 与铁律层不一致会产生第二套豁免语义；② 反手轮的正确处置需要「先平后开可拆分」的能力，那是 05 号文档的仓位重构（阶段二），不在阶段一范围。代价记入 §8 R-2 的监控口径：`edgeGate.veto` 若集中出现在持仓轮，说明反手被大量拦截，属于 `k_min` 回调信号。

**插入位置（硬约束，两侧必须同位）**：

| 侧 | 位置 | 前一步 | 后一步 |
|---|---|---|---|
| 实盘 | `session.py:575` 与 `:578` 之间 | `_apply_consensus_gate` | `_reflect_before_execute` |
| 回测 | `backtest.py:520` 与 `:522` 之间 | `apply_consensus_gate` | `gate_reason` 三元合成 → 反思 |

理由与 `session.py:568-570` 已确立的先例逐字同构：① 必须落在 `decide` **之后**——重试循环只认 `_map_decision` 的 degrade，落进去会每轮白烧 LLM；② 必须落在反思**之前**——被拦轮此时已是 `hold`，而反思对 `hold` 本就直通（`session.py:644-646`）⇒ EV 不足的轮次自动省掉一次反思 LLM 调用，**门禁本身就是省钱项**。

回测侧的 `gate_reason` 合成随之变为四元、先手胜出：`base_reason or cooldown_reason or consensus_reason or edge_reason`（`backtest.py:522`）。

**回测世界的成本必须用回测世界的常数**（闸 0 链路同构性的正确含义不是「两侧数字相同」而是「两侧都对自己的世界说真话」）：`SimBroker` 实际收的是 `default_taker_fee_bps` + `default_slippage_bps`（`simulator.py:103-109`、`backtest.py:247-249`），且 `allow_limit=False`（`backtest.py:232`）、不收资金费率。故回测侧 `CostParams(maker=default_taker_fee_bps, taker=default_taker_fee_bps, half_spread_fallback=default_slippage_bps, impact=0.0)`、`funding_rate=0.0` ⇒ `C = 5 + 5 + 2×2 = 14.0 bps`、`confidence="ok"`。这与 01 号文档「回测世界 C ≈ 14 bps」的表述一致。

### 3.8 归因与统计扩展（D-15）

- **`ai_auto/attribution.py`**：`AttributionLedger(capacity=200)`，滚动记录已完成往返。写入点是 `session._dispatch`（`session.py:228-246`，**全部 executor 事件的唯一收口**，`side == "close"` 覆盖全部五条平仓路径），新增 **1 行**调用；门禁计数写入点是 `_apply_edge_gate`。
- **`ai_auto/stats_ext.py`**：`build_cost_stats(...) -> dict` 纯函数 + `merge_cost_stats(base, ext)`（键冲突即 `raise ValueError`，防止未来静默覆盖既有统计键）。
- **合并落点：`_finalize_run` 的调用点，不是 `_compute_ai_stats` 内部（v2 · RV-1，阻断级修正）。** v1 写的「在 `_compute_ai_stats` 内部合并、不改它的签名」**在物理上不可实现**——`_compute_ai_stats` 是**模块级纯函数** `def _compute_ai_stats(records, rounds) -> dict`（`session.py:1357`），没有 `self`，拿不到 `AttributionLedger` 与 `FeeLedger.audit_stats()`。正确落点是它的**唯一调用点** `session.py:1285`，且本仓库已经为「会话级直取的加法键」确立了这一范式（`wakeSkips:1287` / `maxHoldStreak:1290` / `profitGuardExits:1293` / `profitGuardArmedRounds:1294` 四个键全部在调用点赋值，并附注释说明「非 `_compute_ai_stats` 样本口径」）：

```python
# _finalize_run 既有的 try/except 富化块内，紧随 profitGuardArmedRounds 之后：
metrics["aiStats"] = merge_cost_stats(
    metrics["aiStats"],
    build_cost_stats(self._attribution.stats(), self.executor.fee_ledger))
```

  - `_compute_ai_stats` **一字不动**（签名、返回值、既有九键全部不变）。
  - 富化块外层已有 `except Exception → logger.warning + pop 三键` 的 best-effort 兜底（`session.py:1295-1298`），故 `merge_cost_stats` 抛 `ValueError` 最坏只丢失富化字段，**绝不阻断真实资金平仓后的落库收尾**——这正是既有设计刻意留出的保护，本特性顺势复用，不新增兜底。
  - `metrics_json` 消费方（历史看板 `ai_auto_repositories`、`/ai-trade/history/{group_id}`）零改动、零迁移。
- **会话内可观测通道（v2 · RV-6）**：上述落点只在**会话结束**时产出，而 §7.5 的放行判据是会话运行期内的滚动窗口。故另加一条**只读快照**下发路径：`build_cost_stats` 的同一份 `dict` 经 `cost_snapshot` 键搭载在既有 `edge_gate` trace 事件上（`k_min == 0` 时不发 ⇒ 改由 §5.3 的降级路径每 `_COST_SNAPSHOT_EVERY_ROUNDS = 10` 轮搭一次既有 `status` 事件）。它是**纯加法键**、前端未识别即忽略，不新增端点、不新增 WS 事件类型。
- 新增九个加法键见 §6.3。

### 3.9 开关矩阵与上线次序

| 开关 | 默认 | 关闭后行为 | 归属 |
|---|---|---|---|
| `ai_trade_fee_accounting_enabled` | `True` | `trade["fee"]` 逐字回退 `0.0`，四个 explain.meta 加法键整键缺席 | A0-1 |
| `ai_trade_fill_audit_enabled` | `True` | 不发任何 `fetch_live_fills` 请求，归因表 `feeDeviationBps`/`slipBps` 恒 `None` | A0-1 |
| `ai_trade_net_pnl_enabled` | **`False`** | `_close_pnl` 逐字回退毛口径 | A0-2 |
| `ai_trade_funding_enabled` | `True` | `fundingCarryBps` 恒 `0.0` 且 `confidence` 保持 `"ok"`（有意选择≠数据缺失） | 成本模型 |
| `ai_trade_edge_gate_k_min` | **`0.0`** | 门禁空转（同一 decision 对象原样返回，`is` 相等） | edge 门禁 |

**为什么 A0-2 与 edge 门禁默认关**：08 号路线图第二条纪律是「每个上线项都必须有一个默认关闭的独立开关」，且 01 号文档 §五把「费率入账完整性 = 100%（滚动 50 笔）」定为**阻断条件**——未达标前不得开启 A0 第二步。本轮交付的默认行为改变**只有一条**：系统开始如实记录费率（一个纯粹的诚实性修复）。其余按 §7.5 的 runbook 逐级放开。

### 3.10 落地位置与行数预算（硬约束）

`ai_auto/session.py` 现 **1404** 行、`live_auto/executor.py` 现 **863** 行、`ai_auto/backtest.py` 现 **966** 行，均已超 `CLAUDE.md` 的 800 行红线。08 号文档 §四的原文是「两个超红线文件只允许新增**薄封装调用**；任何让它们继续增长的实现判为不合格」。本 spec 的执行口径：

| 文件 | 增量上限 | 验收判据 |
|---|---|---|
| `live_auto/executor.py` | ≤ **+32** 行 | 新增的每一行必须是委托调用或字段搬运；**零业务分支** |
| `ai_auto/session.py` | ≤ **+48** 行 | 同上；`_apply_edge_gate` / `_emit_edge_gate_trace` 各 ≤ 16 行 |
| `ai_auto/backtest.py` | ≤ **+26** 行 | 同上；成本构造下沉 `cost_model.backtest_params(settings)` |
| `ai_auto/manager.py` | ≤ **+3** 行 | 仅 §3.2.0 的 `fee_ledger=` 构造缝（v2 · RV-2 新增改动点） |

**`executor.py` 的预算由 +38 收紧到 +32，同时把工作量搬走（v2 · RV-8）**：v1 的 +38 与 §4.2 列举的改动量不自洽——逐项估算（`__init__` 缝 ~6 + `_default_read_fills` ~3 + `_audit_fills` ≤10 + `_make_trade` 形参与四加法键 ~7 + `_close_pnl` 拆分 ~5 + 六处调用点补参 ~6 + 三处 `entry_fee_usd` 累加 / 复位 ~5 + `reconcile` 1）已达约 43 行，而本节写明「超出即判不合格」。一个自身不可达的阈值只会逼实现节点做假账。修正方式不是抬高上限，而是**把不该在这里的东西搬走**：

- `_default_read_fills` 的取数、`ai_trade_fill_audit_interval_s` 的节流、`try/except` 全吞、以及归一后交给 `FeeLedger.resolve` 的整段流程，全部下沉为 `fee_ledger.py` 的自由函数 `async def audit_fills(ledger, *, fills_fn, now_wall, settings) -> int`；
- `executor.py` 只保留：`fills_fn` 缝（2 行）、`reconcile` 末尾一行委托（1 行）——即 v1 的 `_audit_fills` 与 `_default_read_fills` 合计 ~13 行降为 3 行。

**机械判据（Z-8）**：`git diff --numstat` 对上述四个文件的 `added − deleted` 必须逐一 ≤ 上表数值。评审时跑命令，不靠目测。超出即判不合格。全部业务逻辑一律进 §4 的六个新模块。

---

## 四、文件 / 模块变更计划

### 4.1 新增（后端）

| 文件 | 预估行数 | 一句话意图 |
|---|---|---|
| `services/quant/app/ai_auto/cost_model.py` | ~185 | 往返成本 `C` 的纯函数真源：`CostParams` / `CostQuery` / `CostBreakdown` / `estimate_round_trip_cost` / `cost_wire` / `backtest_params` |
| `services/quant/app/ai_auto/edge_gate.py` | ~110 | 粗估毛 edge + 最小 edge 门禁纯函数：`EdgeEstimate` / `estimate_gross_edge_bps` / `apply_edge_gate` / `gate_status` |
| `services/quant/app/ai_auto/funding.py` | ~95 | 资金费率会话级缓存：`FundingSnapshot` / `FundingCache`（异常全吞、陈旧即 `None`） |
| `services/quant/app/live_auto/fee_ledger.py` | ~175 | 费率记账与实测审计：`FeeParams` / `FillAudit` / `FeeLedger`（腿费率、挂账、`resolve`、`net_close_pnl`、`audit_stats`） |
| `services/quant/app/ai_auto/attribution.py` | ~135 | 滚动往返归因：`RoundTrip` / `AttributionLedger`（净期望、成本占比、tStat、门禁计数、决策转化率） |
| `services/quant/app/ai_auto/stats_ext.py` | ~85 | `build_cost_stats` / `merge_cost_stats`（加法键合并，冲突即抛） |

### 4.2 修改（后端）

| 文件 | 变更 | 增量 |
|---|---|---|
| `services/quant/app/config.py` | 新增一个 `# ---- 成本记账与最小 edge 门禁 ----` 配置段，14 个 `ai_trade_*` 字段（§5.1） | +30 |
| `services/quant/app/okx/rest_client.py` | 新增 `get_funding_rate` + `_funding_cache`（镜像 `_inst_meta_cache`） | +30 |
| `services/quant/app/okx/trading.py` | `_normalize_fill` 新增一个键 `feeCcy`（当前被丢弃）+ 同步该函数 docstring 的回带字段契约段（v2 · RV-15） | +2 |
| `services/quant/app/live_auto/executor.py` | 薄接线：`fee_ledger`（第七缝，默认 `None`）/ `entry_fee_usd` / `fills_fn` 第六缝；`_make_trade` 两个默认形参 + 四个加法键；`_close_pnl` 拆 `_gross_close_pnl` + `None` 短路 + 委托；六处调用点补传 `ord_type`/`expected_px`；三处 `entry_fee_usd` 复位（§3.3(b)）；`reconcile` 末尾一行委托 `fee_ledger.audit_fills` | +32 |
| `services/quant/app/ai_auto/manager.py` | **v2 新增**（RV-2）：`_build_executor` 传 `fee_ledger=FeeLedger(...)`——本特性作用域的唯一开关 | +3 |
| `services/quant/app/ai_auto/session.py` | 薄接线：`_funding` / `_attribution` / `_edge_unavailable_streak` 三个字段；`_monitor_tick` 一行 refresh；`_apply_edge_gate` + `_emit_edge_gate_trace`；`_run_decision_round` 插一行；`_inject_explain_meta` 四行 setdefault；`_dispatch` 一行归因；`_finalize_run` **调用点**一行 `merge_cost_stats`（§3.8，非 `_compute_ai_stats` 内部） | +48 |
| `services/quant/app/ai_auto/backtest.py` | 同位插入 edge 门禁；`gate_reason` 四元合成；`_backtest_cost` 缓存一次；`_build_notes` 追加一条粗估免责 | +26 |
| `services/quant/app/ai_auto/trace.py` | 新增 `edge_gate_event`；**`PHASES` 元组追加 `"edge_gate"`**（v2 · RV-9，前后端镜像常量，`trace.py:25`）；`new_round_trace` 初始化四个新键 | +27 |
| `.env.example` | 新增 14 个 `AI_TRADE_*` 环境变量注释块 | +18 |

> `services/quant/app/live_auto/manager.py` **无需改动**——它构造 `LiveExecutor` 时不传 `fee_ledger`，默认 `None` ⇒ 非 AI 链路逐字节回退（§3.2.0）。
> **v1 的「两个 manager 都无需改动、`FeeLedger` 由 `__init__` 内部自建」已作废**（RV-2）：那会让 `ai_trade_*` 开关跨产品线改写 live_auto 的成交与盈亏。
> `desktop/src/lib/config.js` **无需改动**——新字段一律 `ai_trade_*` 前缀 ⇒ 环境变量 `AI_TRADE_*` ⇒ 已在 prod 透传白名单内。

### 4.3 修改（前端 · 四文件镜像，与 `base_rule` 完全同构）

| 文件 | 变更 |
|---|---|
| `apps/web/lib/aiTradeTypes.ts` | `AiTradeTracePhase` 加 `"edge_gate"`；`AiTradeEdgeGateStatus = "pass"\|"veto"\|"unavailable"`；`AiTradeRoundTrace` 加 `edgeGateStatus` / `edgeBps` / `edgeCostBps` / `edgeGateReason` 四个可选键 |
| `apps/web/lib/store.ts` | `case "edge_gate":` 落四个字段（镜像 `case "base_rule"`，`store.ts:195-203`） |
| `apps/web/lib/aiTrade.ts` | `describeEdgeGate(trace)`（三态中文化 + tone：放行 `muted`、否决/不可用 `short`，`edgeGateStatus` 为空 ⇒ `null` ⇒ 整段不渲染）；`DEGRADE_REASON_LABELS` 加 `{ prefix: "edge_below_cost", label: "期望收益不足以覆盖交易成本，本轮观望" }` |
| `apps/web/components/Agent/AiTradeTraceRound.tsx` | 在铁律徽标同排渲染 edge 门禁徽标（`describeEdgeGate` 返回 `null` 时整段不渲染 ⇒ 老会话与老快照零影响） |

> **老前端 / 老快照安全**：`applyAiTradeTracePhase` 的 `default: return t`（`store.ts:213-214`）已有专门单测「未知 phase 走 default 容错」（`aiTradeTrace.test.ts:170-176`），故后端先行上线不会打崩任何旧前端。

### 4.4 新增（测试）

| 文件 | 覆盖 |
|---|---|
| `services/quant/tests/test_ai_trade_cost_model.py` | 成本模型六分支 + 两个标定值 + 资金费率方向性 + degraded |
| `services/quant/tests/test_ai_trade_edge_gate.py` | 门禁豁免矩阵、`k_min=0` 对象同一性、fail-open、降级码格式、ATR 缺失 |
| `services/quant/tests/test_ai_trade_fee_ledger.py` | maker/taker 归属、部分成交累加、`net_close_pnl` 开关等价性、`feeCcy` 跳过、滑点符号 |
| `services/quant/tests/test_ai_trade_funding_cache.py` | TTL 去重、异常吞并、陈旧转 `None`、总闸关时 `0.0` vs 缺失 `None` |
| `services/quant/tests/test_ai_trade_attribution.py` | 净期望 / 成本占比 / tStat / 覆盖率 / 门禁计数 |
| `services/quant/tests/test_ai_trade_cost_gate_session.py` | 端到端：门禁插入位序、被拦轮零反思 LLM、trace 事件、A0-2 会话止盈判据变严 |
| `apps/web/lib/aiTradeEdgeGate.test.ts` | `describeEdgeGate` 三态 + 降级码中文化 + 老快照 `null` |

### 4.5 修改（诊断文档标记，见 §9）

`docs/improve/01-ai-autotrading/00-总纲与现状诊断.md` + `01`/`08` 三篇加实施状态标记。**本设计节点不写**（受「不得修改 `docs/plans/<feature>/` 之外的文件」约束），由下游实现节点按 §9 的逐字模板执行。

---

## 五、接口设计

### 5.1 配置项（`services/quant/app/config.py`，全部 `ai_trade_` 前缀）

```python
# ---- 成本记账与最小 edge 门禁（ai-trade-cost-accounting-and-edge-gate §3.9）----
# A0-1 记录层：每笔成交按确定性费率模型入账（notional × feeBps）。False ⇒ trade["fee"] 逐字回退 0.0。
ai_trade_fee_accounting_enabled: bool = True
# A0-2 口径切换：_close_pnl 扣双边费用 ⇒ session_pnl_usd 变净额。**破坏性变更，默认关**。
ai_trade_net_pnl_enabled: bool = False
# 费率常量刻意**不复用** default_taker_fee_bps（config.py:280）：后者同时服务 SimBroker / 纸面 / 影子
# broker，改它等于静默重写每一次历史回测的成本假设。两个世界的费率必须能各自演进。
ai_trade_maker_fee_bps: float = Field(default=2.0, ge=0, le=100)
ai_trade_taker_fee_bps: float = Field(default=5.0, ge=0, le=100)
ai_trade_half_spread_fallback_bps: float = Field(default=1.0, ge=0, le=100)  # 无 books5 时的保守假设
ai_trade_impact_bps: float = Field(default=0.5, ge=0, le=100)
# 成本模型的预期持仓根数（资金费率结算周期数由它推）。
ai_trade_cost_expected_hold_bars: int = Field(default=8, ge=1, le=1000)
# 最小 edge 门禁：判据 毛edge(bps) ≥ k_min × C(bps)。**0 = 门禁空转（默认，逐字回退）**。
# 启用 runbook 见 spec §7.5：先满 50 笔费率入账 → 开 A0-2 → 观察 100 笔 → 再置 2.0。
ai_trade_edge_gate_k_min: float = Field(default=0.0, ge=0, le=10)
ai_trade_edge_gate_degraded_mult: float = Field(default=1.2, ge=1.0, le=3.0)  # 成本 degraded 时的收紧倍数
ai_trade_edge_atr_target_mult: float = Field(default=1.0, gt=0, le=10)        # 粗估毛 edge 的 ATR 倍数
# 资金费率：总闸关 ⇒ fundingCarryBps=0.0 且 confidence 保持 "ok"（有意选择 ≠ 数据缺失）。
ai_trade_funding_enabled: bool = True
ai_trade_funding_poll_ttl_s: float = Field(default=60.0, gt=0, le=3600)
# 实测成交明细旁路审计（校准 feeBps / 观测滑点）；绝不进决策轮，绝不回写盈亏。
ai_trade_fill_audit_enabled: bool = True
ai_trade_fill_audit_interval_s: float = Field(default=60.0, gt=0, le=3600)
```

### 5.2 纯函数签名（下游按此逐字实现）

```python
# ---- ai_auto/cost_model.py ----
DEFAULT_FUNDING_INTERVAL_S: float = 28800.0

@dataclass(frozen=True)
class CostParams:
    maker_fee_bps: float
    taker_fee_bps: float
    half_spread_fallback_bps: float
    impact_bps: float
    expected_hold_bars: int

def resolve_cost_params(settings) -> CostParams: ...
def backtest_params(settings) -> CostParams: ...        # SimBroker 世界的常数（§3.7）

@dataclass(frozen=True)
class CostQuery:
    ord_type: str                       # "market" | "limit"（开仓腿意图）
    side: str                           # "long" | "short"（资金费率收/付方向）
    bar_seconds: int
    half_spread_bps: float | None = None
    funding_rate: float | None = None   # None ⇒ 缺席降级；0.0 ⇒ 有意不建模
    funding_interval_s: float = DEFAULT_FUNDING_INTERVAL_S

@dataclass(frozen=True)
class CostBreakdown:
    fee_open_bps: float
    fee_close_bps: float
    half_spread_bps: float
    impact_bps: float
    crossing_legs: int
    funding_carry_bps: float | None
    total_bps: float
    confidence: str                     # "ok" | "degraded"

def estimate_round_trip_cost(query: CostQuery, params: CostParams) -> CostBreakdown: ...
def cost_wire(cost: CostBreakdown | None) -> dict | None: ...   # camelCase，None → None
def query_for_decision(decision, *, bar_seconds: int, funding, funding_enabled: bool,
                       half_spread_bps: float | None = None) -> CostQuery: ...

# ---- ai_auto/edge_gate.py ----
@dataclass(frozen=True)
class EdgeEstimate:
    target_bps: float
    source: str                         # "atr" | "take_profit"
    atr_bps: float | None
    take_profit_bps: float | None

def estimate_gross_edge_bps(*, action: str, ref_px: float | None, atr_px: float | None,
                            take_profit_pct: float | None,
                            atr_target_mult: float) -> EdgeEstimate | None: ...

def apply_edge_gate(decision, edge: EdgeEstimate | None, cost: CostBreakdown | None, *,
                    k_min: float, degraded_mult: float) -> tuple["TradeDecision", str | None]: ...

def gate_status(edge, cost, gate_reason: str | None) -> str:   # pass | veto | unavailable
    ...
def edge_wire(edge: EdgeEstimate | None) -> dict | None: ...

# ---- live_auto/fee_ledger.py ----
class FeeLedger:
    def __init__(self, *, ct_val: float, params: FeeParams, audit_capacity: int = 200) -> None: ...
    def leg_fee(self, *, sz: float, px: float, ord_type: str) -> float: ...   # sz = 本轮采纳增量（§3.3a）
    def leg_fee_bps(self, ord_type: str) -> float: ...
    def register(self, *, cl_ord_id: str, direction: str, ord_type: str,
                 expected_px: float, sz: float, model_fee_usd: float) -> None: ...
    def resolve(self, fills: list[dict]) -> int: ...            # 返回本次消化的挂账条数
                                                                # 内部按 §3.2.4 归一 OKX fee 符号
    def net_close_pnl(self, gross: float, *, entry_fee_usd: float,
                      exit_px: float, sz: float, ord_type: str = "market") -> float: ...
    def has_pending(self) -> bool: ...
    def audit_stats(self) -> dict: ...

def resolve_fee_params(settings) -> FeeParams: ...              # ai_auto/manager.py 构造缝用

async def audit_fills(ledger: FeeLedger, *, fills_fn, now_wall: float, settings) -> int:
    """旁路审计的**全部**流程（v2 · RV-8：从 executor.py 下沉至此）。

    节流（`ai_trade_fill_audit_interval_s`）、总闸（`ai_trade_fill_audit_enabled`）、
    `ledger.has_pending()` 短路、`try/except Exception` 全吞（best-effort，绝不影响盯市主流程）
    一律在本函数内；`executor.reconcile` 末尾只留一行委托。返回本次消化的挂账条数。
    """

# ---- live_auto/executor.py 的构造缝（§3.2.0，v2 · RV-2）----
# LiveExecutor.__init__(..., fills_fn=None, fee_ledger: FeeLedger | None = None)
#   fee_ledger is None  ⇒ trade["fee"] 恒 0.0 / 四加法键整键缺席 / _close_pnl 恒毛口径
#                          ⇒ live_auto 与全部既有单测逐字节回退
#   仅 ai_auto/manager.py 传入实例。

# ---- ai_auto/attribution.py ----
class AttributionLedger:
    def __init__(self, capacity: int = 200) -> None: ...
    def record_close(self, trade: dict, *, net_mode: bool, cost_bps: float | None,
                     edge_bps: float | None) -> None: ...
    def note_gate(self, status: str) -> None: ...
    def note_round(self, executed: bool) -> None: ...
    def stats(self) -> dict: ...
```

### 5.3 WS 事件：新增 `edge_gate` trace 相位

```jsonc
{ "type": "agent_trace",
  "data": { "phase": "edge_gate", "round": 42, "ts": 1769470800,
            "status": "veto",                         // pass | veto | unavailable
            "edgeBps": 8.1, "costBps": 13.0, "kMin": 2.0,
            "requiredBps": 26.0, "costConfidence": "ok",
            "edgeSource": "atr",
            "reason": "edge_below_cost(edge=8.1bps,cost=13.0bps,k=2.0)",
            "costSnapshot": { /* §6.3 的 stats.cost 同款只读快照，见下 */ } } }
```

**每轮都发一条**（`k_min > 0` 时），与 `base_rule_event` 的理由逐字相同：静默的保守会被用户当成故障。`k_min == 0` 时**一条都不发** ⇒ 老前端与老快照零影响。

`costSnapshot` 是 v2 新增的**纯加法只读键**（RV-6）：前端 reducer 未识别即忽略（`store.ts:213-214` 的 `default: return t` 已有专门单测），后端也不因它改变任何判定。它的存在理由见 §5.4。

### 5.4 REST（无新端点）与统计键的实际下发路径（v2 · RV-6 修正）

本特性**不新增任何 REST 端点**。但 v1「新统计键随既有 `GET /ai-trade/status` 的 `stats` 自然下发」的表述**与代码不符**，须更正：

- `AiTradeStatusOut` 的字段只有 `{group: AiTradeGroupRow | None, aiTradeEnabled: bool}`（`schemas.py:630-632`），**不存在 `stats` 字段**；
- `_compute_ai_stats` 的**唯一**调用点在 `_finalize_run`（`session.py:1285`），即新统计键只在**会话停止时**写进 `metrics_json`；
- 故新键的 REST 下发路径**只有一条**：`GET /ai-trade/history/{group_id}` 的 `metricsJson.aiStats.cost` / `.edgeGate` / `.decisionConversionRate`。

而 §7.5 的三级 runbook 的放行判据（「滚动 50 笔」「滚动 200 轮」）全部是**会话运行期内**的观察。若不补通道，运维要看这些数就必须先把会话停掉——L0/L1 就地失效。故 v2 补一条**会话内**通道，且刻意不新增端点、不新增 WS 事件类型：

| 通道 | 何时 | 载体 |
|---|---|---|
| `edge_gate` trace 事件的 `costSnapshot` 键 | `k_min > 0` 的每一轮 | §5.3 |
| `status` 事件的 `costSnapshot` 键 | `k_min == 0`（L0/L1 阶段，门禁不发事件）时每 `_COST_SNAPSHOT_EVERY_ROUNDS = 10` 轮一次 | 既有 `_status_payload` 的加法键 |
| `metricsJson.aiStats` | 会话停止后 | `GET /ai-trade/history/{group_id}` |

前两条是**同一个** `build_cost_stats` 产物的搬运，绝不另算一份（否则立刻产生第二个统计口径真源）。前端本轮**不消费** `costSnapshot`（阶段一的读者是运维，用 WS 原始事件即可）；§4.3 的前端四文件镜像范围不因此扩大。

---

## 六、数据模型

### 6.1 无数据库变更

零新表、零新列、零迁移。归因与审计全部走内存环形缓冲（`collections.deque`，容量 200），随会话终结经既有 `metrics_json`（JSON 列）落库。

### 6.2 成交记录（`trade` dict）的形状变化

```jsonc
{ "ts": 1769470800, "side": "close", "price": 61234.5, "qty": 3.0,
  "fee": 0.184,                  // 变化：曾恒为 0.0；现为 USD 正数（支出）
  "feeBps": 5.0,                 // 新增
  "ordType": "market",           // 新增
  "expectedPx": 61240.0,         // 新增（市价腿=下单 ref_px；限价腿=order.px）
  "pnl": 12.31,                  // ai_trade_net_pnl_enabled=true 时为净额
  "explain": { "meta": { "source": "ai", "instId": "…", "bar": "15m", "barTs": …,
                         "clOrdId": "…", "ordId": "…",
                         "feeUsd": 0.184, "feeBps": 5.0,      // 新增
                         "ordType": "market", "expectedPx": 61240.0 } } }
```

### 6.3 统计新增键（`_compute_ai_stats` 返回值的加法合并）

```jsonc
{ // …既有九键 + wakeSkips/maxHoldStreak/profitGuard* 四键原样保留…
  "cost": {
    // v2 · RV-13：更名自 feeCoverage。分母 = **平仓**成交样本数（开仓腿也记费，但 G1 的
    // 阻断判据说的是「每笔平仓成交都带非零费率」，两者分母不同，键名必须自证是哪一个）。
    "closeFeeCoverage": 1.0,     // 平仓样本中 fee != 0 的占比（G1 阻断判据）
    "roundTrips": 37,
    "grossPnlUsd": -12.4, "feeUsd": 6.8, "netPnlUsd": -19.2,
    "netExpectancyUsd": -0.519,  // netPnl / roundTrips
    "costRatio": 0.31,           // Σfee / Σ|gross|
    "tStat": -1.42,              // n<2 → null
    "modelCostBps": 13.0,        // 最近一次成本模型读数
    // 实测校准（无样本 → null）。measuredFeeBps 只统计**收费**样本（OKX fee<0），
    // 返佣样本单列 feeRebateSamples 且不入均值（§3.2.4，v2 · RV-4）。
    "measuredFeeBps": 5.4, "feeDeviationBps": 0.4,
    "slipBps": 1.9, "feeAuditSamples": 24, "feeAuditSkipped": 0, "feeRebateSamples": 0
  },
  "edgeGate": { "pass": 118, "veto": 46, "unavailable": 3, "vetoRate": 0.275 },
  "decisionConversionRate": 0.22 // 产生成交的轮次 / 决策轮次
}
```

同一份 `cost` / `edgeGate` 对象也是 §5.4 会话内通道搭载的 `costSnapshot`（同一个 `build_cost_stats` 产物，绝不另算）。

### 6.4 内存结构

```python
@dataclass(frozen=True)
class FundingSnapshot:
    rate: float; funding_ts: int; next_funding_ts: int
    interval_s: float; fetched_wall: float

@dataclass(frozen=True)
class FillAudit:
    cl_ord_id: str; direction: str; ord_type: str
    expected_px: float; sz: float; model_fee_usd: float
    measured_fee_usd: float | None; measured_px: float | None; slip_bps: float | None

@dataclass(frozen=True)
class RoundTrip:
    ts: int; side: str; ord_type: str
    gross_pnl_usd: float; fee_usd: float; net_pnl_usd: float; notional_usd: float
    cost_bps: float | None; edge_bps: float | None
```

---

## 七、测试与验收标准

### 7.1 零回归门禁（最高优先级，任何一条红即整体不合格）

| 编号 | 断言 | 手段 |
|---|---|---|
| Z-1 | 全部开关取默认值时，`live_auto`（非 AI）路径的 `place_order` / `close_position` payload **逐字节相同** | 既有 `test_live_executor.py` / `test_live_auto_session.py` 全绿，不改一个断言 |
| Z-2 | `ai_trade_net_pnl_enabled=False` 时 `_close_pnl` 返回值与改造前**位相同** | 参数化对拍：随机 200 组 `(side, entry, exit, sz, ct_val)`，断言 `==` |
| Z-3 | `ai_trade_edge_gate_k_min=0` 时 `apply_edge_gate` 返回**同一个** decision 对象（`is` 相等） | 单测，镜像 base_rules「§六 用例 9」的既有写法 |
| Z-4 | prompt 渲染逐字节不变 | 冻结门禁：对 `build_user_message(payload)` 的 sha256 断言（payload 带/不带 `cost` 键两种输入产出同一 hash） |
| Z-5 | `ai_trade_fee_accounting_enabled=False` 时 `trade` 的键集合与改造前**完全相同**（四个加法键整键缺席，非 `None`） | 单测断言 `set(trade.keys())` |
| Z-6 | 老前端遇 `edge_gate` 相位不崩、不误改其它字段 | 既有 `aiTradeTrace.test.ts:170` 的 default 容错用例 + 新增一条针对 `edge_gate` 的 |
| Z-7 | **（v2 · RV-2）`live_auto`（非 AI）链路在 `ai_trade_*` 全部开关取任意值时逐字节回退**：`fee_ledger=None` 的 executor 产出的 `trade` 键集合、`trade["fee"] == 0.0`、`_close_pnl` 返回值**位相同**，`live_auto/session.py:345` 的会话止盈止损触发时点不变 | 新增 `test_live_executor.py` 参数化用例：同一段成交序列在 `net_pnl_enabled=True/False` × `fee_accounting=True/False` 四种配置下，**非 AI** executor 的输出恒等 |
| Z-8 | **（v2 · RV-8）四个超红线 / 接线文件的净增行数不超 §3.10 上限** | `git diff --numstat` 对 `executor.py`/`session.py`/`backtest.py`/`ai_auto/manager.py` 逐一核对 `added − deleted`；机械判据，不靠目测 |

### 7.2 单元测试（新增，按模块）

**`test_ai_trade_cost_model.py`**
1. `estimate_round_trip_cost` 市价默认参数 ⇒ `total_bps == 13.0`、`crossing_legs == 2`、`confidence == "ok"`。
2. 限价开仓 ⇒ `total_bps == 8.5`、`fee_open_bps == 2.0`、`fee_close_bps == 5.0`、`crossing_legs == 1`。
3. `funding_rate=None` ⇒ `funding_carry_bps is None` 且 `confidence == "degraded"` 且 `total_bps` 不含资金费率项。
4. `funding_rate=0.0001, side="long", expected_hold_bars=8, bar_seconds=900` ⇒ `periods == 1`、`funding_carry_bps == 1.0`。
5. **方向性**：同一 `funding_rate=0.0001` 下 `side="short"` ⇒ `funding_carry_bps == 0.0`（收费一侧不给折扣）。
6. `backtest_params(settings)` ⇒ 市价 `total_bps == 14.0`（对齐 SimBroker 实收）。

**`test_ai_trade_edge_gate.py`**
1. 豁免矩阵：`close` / `hold` / `cancel_order` 四种动作恒返回同一对象、`reason is None`。
2. `k_min=0` ⇒ 对象同一性（Z-3）。
3. `atr_px=None` ⇒ `estimate_gross_edge_bps` 返回 `None` ⇒ 门禁放行 + `gate_status == "unavailable"`（**fail-open 钉子**）。
4. `atr_px` 使 `edge=8.1`、`cost=13.0`、`k_min=2.0` ⇒ 降级为 hold，`reason == "edge_below_cost(edge=8.1bps,cost=13.0bps,k=2.0)"`（逐字节）。
5. `cost.confidence=="degraded"` ⇒ 阈值乘 `1.2`；构造一个「ok 放行、degraded 拦下」的临界样本。
6. 限价决策 ⇒ `ref_px` 取 `decision.limit_px`（经 `entry_reference_px`），与市价同一 ATR 下得到不同 `target_bps`。
7. 单仓 OCO 止盈距离小于 ATR 预算 ⇒ `source == "take_profit"`。
8. **（v2 · RV-7）`take_profit_pct is None` ⇒ 上界不生效**：`target_bps` 等于纯 ATR 预算、`source == "atr"`、`take_profit_bps is None`；且与传入一个极大 `take_profit_pct` 的结果**位相同**（证明 `None` 是"不设上界"而非"上界为 0"）。

**`test_ai_trade_fee_ledger.py`**
1. 市价开仓 ⇒ `feeBps == 5.0`；限价采纳成交 ⇒ `feeBps == 2.0`。
2. 部分成交两次 `_adopt_fill` ⇒ `entry_fee_usd` 为两段之和（**不是最后一段、也不是两次累计量之和**）。构造 `acc` 从 3 → 5（`delta` 分别为 3 和 2）⇒ 断言 `entry_fee_usd == leg_fee(3) + leg_fee(2)`，而**不是** `leg_fee(3) + leg_fee(5)`（v2 · RV-5 的可证伪判据）。
3. `net_close_pnl` 开关关 ⇒ 返回 `gross` 本身（`is` 或 `==` 位相同，Z-2）。
4. 开关开 ⇒ `gross − entry_fee − exit_fee`，且 `exit_fee` 按 `exit_px × sz × ct_val` 算（不是按 `entry_price`）。
5. `resolve` 遇 `feeCcy == "BTC"` ⇒ 该样本被跳过、`feeAuditSkipped == 1`、`measuredFeeBps` 不被污染。
6. 滑点符号：`open_long` 实测价高于期望价 ⇒ `slip_bps > 0`（不利）；`open_short` 同样价差 ⇒ `slip_bps < 0`（有利）。
7. `resolve` 对同一 `clOrdId` 的多条 fills 做量加权均价 + 费率求和。
8. **（v2 · RV-3）三处复位点全覆盖**：分别走 `_settle_close` / `_settle_benign_close` / `_on_reconcile_ok` 三条路径平掉一笔持仓后，`entry_fee_usd == 0.0`；随后再开一笔并平掉，断言第二笔的净额**不含**第一笔的开仓费（可证伪写法：若漏掉 `_on_reconcile_ok`，第三条路径的第二笔净额会比预期少一个 `entry_fee`）。
9. **（v2 · RV-4）OKX fee 符号归一**：`fill["fee"] == -0.184` ⇒ `measured_fee_usd == +0.184`、`measuredFeeBps > 0`、`feeAuditSamples == 1`；`fill["fee"] == +0.05`（返佣）⇒ `feeRebateSamples == 1` 且**不进** `measuredFeeBps` 均值；`fill["fee"] == 0.0` ⇒ 挂账**保留不消化**、`has_pending()` 仍为 `True`。
10. **（v2 · RV-2）`fee_ledger=None` 的 executor**：`trade` 的键集合与改造前完全相同、`trade["fee"] == 0.0`、`_close_pnl` 与 `_gross_close_pnl` 返回值位相同（Z-7 的单测面）。

**`test_ai_trade_funding_cache.py`**
1. TTL 内二次 `refresh` 不再调用 `fetch_fn`。
2. `fetch_fn` 抛异常 ⇒ `refresh` 返回 `False`、**不上抛**、上一份快照仍可读。
3. 超过 `ttl_s × stale_factor` ⇒ `snapshot()` 返回 `None`（陈旧数据绝不冒充新鲜）。
4. `ai_trade_funding_enabled=False` ⇒ `query_for_decision` 产出 `funding_rate == 0.0`（**不是 `None`**）⇒ `confidence == "ok"`。

**`test_ai_trade_attribution.py`**
1. 三笔往返（+10 / −5 / +2 毛，fee 各 1）⇒ `netPnlUsd == 4.0`、`netExpectancyUsd ≈ 1.333`、`costRatio == 3/17`。
2. `net_mode=True` 时 `trade["pnl"]` 已是净额 ⇒ `grossPnlUsd = pnl + fee`（不重复扣费）。
3. `closeFeeCoverage`：一笔 `fee == 0` 的**平仓**样本混入 ⇒ `< 1.0`（G1 判据可证伪）；一笔 `fee == 0` 的**开仓**样本混入 ⇒ 该指标**不变**（分母只数平仓，v2 · RV-13）。
4. `n < 2` ⇒ `tStat is None`（不造假显著性）。
5. 环形容量 200：第 201 笔挤掉第 1 笔。
6. **（v2 · RV-1）`merge_cost_stats(base, ext)`**：`base` 取 `_compute_ai_stats` 的真实返回值（含 `wakeSkips` 等调用点加法键）⇒ 合并后既有键**逐一位相同**；构造一个键冲突（如 `ext` 里塞 `"holdRate"`）⇒ `raise ValueError`，绝不静默覆盖。

### 7.3 集成测试（`test_ai_trade_cost_gate_session.py`，脱网、注入桩回调）

1. **插入位序**：构造一个「共识放行、edge 不足」的轮次 ⇒ 最终 `action == "hold"`、`degradeReason == "edge_below_cost(...)"`、且 `decider.reflect` **调用次数为 0**（门禁省钱的可证伪断言）。
2. **优先级合成**：铁律与 edge 同时否决 ⇒ `degradeReason` 报**铁律**的码（先手胜出，`degrade or reason`）。
3. **trace 事件**：`k_min > 0` 时每轮恰一条 `edge_gate` 事件；`k_min == 0` 时零条。
4. **连续不可用告警**：连续 20 轮 `atr_px=None` ⇒ 恰一条 `AI_TRADE_EDGE_GATE_UNAVAILABLE` error 事件，且这 20 轮开仓**全部放行**。
5. **A0-2 连带影响**：同一段行情下，`net_pnl_enabled=True` 比 `False` **更早**触发会话止损、**更晚**触发会话止盈。
6. **归因写入**：三次平仓后 `stats()["cost"]["roundTrips"] == 3`、`closeFeeCoverage == 1.0`。
7. **零 IO 保证**：整个决策轮期间桩 `funding fetch_fn` 与 `fills_fn` 的调用次数恒为 0（只在 `_monitor_tick` / `reconcile` 被调）。
8. **（v2 · RV-1）统计合并落点**：跑完一个会话并 `stop()` ⇒ `metrics["aiStats"]` 同时含既有键（`decisionRounds` / `holdRate` / `wakeSkips` / `profitGuardExits`）与新键（`cost` / `edgeGate` / `decisionConversionRate`）；且 `_compute_ai_stats` 本身的返回值**不含**新键（证明合并发生在调用点、该函数未被改签名）。
9. **（v2 · RV-1）富化失败不阻断收尾**：桩 `build_cost_stats` 抛异常 ⇒ `finalize_run` 仍被调用、`status == "stopped"`、`metrics` 退回不含富化字段的基础形态（复用既有 `session.py:1295-1298` 兜底，不新增）。
10. **（v2 · RV-6）会话内可观测通道**：`k_min > 0` 时每条 `edge_gate` 事件带 `costSnapshot`；`k_min == 0` 时每 10 轮恰一条 `status` 事件带 `costSnapshot`，且两者与 `stop()` 后 `metrics["aiStats"]["cost"]` 出自同一 `build_cost_stats`（同输入下断言 `==`）。
11. **（v2 · RV-11）多标的分组的请求次数上界**：5 成员分组跑 60 秒模拟时钟 ⇒ 桩 `funding fetch_fn` 总调用次数 ≤ 5、桩 `fills_fn` ≤ 5。这是**次数上界**的断言，不是 429 的证伪（脱网桩测无法证伪限流，见 §8 R-9）。

### 7.4 回测同构测试（`test_ai_backtest_runner.py` 增量）

1. `k_min=0` ⇒ 回测结果与改造前**逐字节相同**（既有回测用例全绿，不改断言）。
2. `k_min=2.0` ⇒ 门禁在 `apply_consensus_gate` 之后、反思之前生效；被拦轮 `decider.reflect` 零调用。
3. `_build_notes` 追加免责条目：「阶段一的 edge 为 ATR 粗估，非概率化先验；门禁拦截率仅作结构性参考」。

### 7.5 上线验收与启用 runbook（运维执行，非自动化测试）

阶段一的三个阶梯必须**按序**执行，每级不达标即停在该级：

| 级 | 动作 | 放行判据 | 观察窗口 |
|---|---|---|---|
| L0 | 合入（默认配置）：只开 `fee_accounting` + `fill_audit` | `closeFeeCoverage == 1.0`；`feeDeviationBps` 绝对值 < 5 bps；`slipBps` 绝对值 < 3 bps | 滚动 50 笔 |
| L1 | 置 `AI_TRADE_NET_PNL_ENABLED=true` | 毛/净两条曲线的差额 ≈ `roundTrips × C`（对账通过）；用户已收到「历史数据与新口径不可比」告知 | 滚动 50 笔 |
| L2 | 置 `AI_TRADE_EDGE_GATE_K_MIN=2.0` | 门禁拦截率落在 20%~70%；决策转化率落在 10%~35%；归因覆盖率 ≥ 95% | 滚动 200 轮 |

**观察方式（v2 · RV-6，须与 §5.4 一致）**：上表三级的判据全部读同一份 `costSnapshot`。运维有两条读法，**不要指望 `GET /ai-trade/status`**（它只返回 `{group, aiTradeEnabled}`，没有统计字段）：

- **会话运行中**：订阅 `ws /ws/ai-trade/{sid}`，读 `edge_gate` 事件（L2，每轮）或 `status` 事件（L0/L1，每 10 轮）的 `costSnapshot`。
- **会话停止后**：`GET /ai-trade/history/{group_id}` 的 `metricsJson.aiStats.cost` / `.edgeGate` / `.decisionConversionRate`。

L0/L1 的「滚动 50 笔」按**跨会话累计**理解：单会话未必够 50 笔平仓，运维需把多个已停止会话的 `roundTrips` 与 `feeUsd` 手工累加（阶段一不做跨会话聚合看板，那是 07 号文档的范围）。

**不达标处置**（与 01 号文档 §五、08 号文档 §五逐条对齐）：拦截率 > 70% ⇒ `k_min` 降到 1.5 重测；拦截率 < 20% ⇒ 门禁未生效，查 `edgeGate.unavailable` 是否居高；转化率 < 10% ⇒ 判定过度收紧，`k_min` 直接回 0；`feeDeviationBps` 长期 > 5 bps ⇒ 按实测重标 `ai_trade_taker_fee_bps` / `ai_trade_maker_fee_bps`，**不要**去调 `impact_bps`（那是在用参数掩盖模型错误）。**重标前先确认 `feeRebateSamples == 0`**（v2 · RV-4）：若账户享有返佣，`measuredFeeBps` 的样本构成本身就与模型假设不同，此时重标会把返佣当成"费率更低"永久写进配置。

**M1 出口条件**（进入阶段二的前提，08 §五）：① `closeFeeCoverage == 1.0`；② 决策转化率 10%~35%；③ 归因覆盖率 ≥ 95%；④ 样本量 ≥ 100 笔。四条全达标才允许开始阶段二。

---

## 八、风险与缓解

**R-1 · A0-2 会让盈亏数字变差，用户极可能误判为「系统被改坏了」。**
这不是副作用而是本质——之前的数字是错的。缓解：① 开关默认关；② 两步走（L0 只记录、L1 才切换）；③ L0 期间看板同时具备毛/净两个数（`grossPnlUsd` / `netPnlUsd` 都在 `stats.cost` 里），提供滚动 50 笔的对照；④ 切换时必须显式告知「历史数据与新口径不可比」。

**R-2 · 门禁可能反而降低收益。** 若真实 edge 分布的中位数本就接近 `k_min × C`，门禁会把大量微正期望的交易一并拦掉，而它们的累积贡献可能为正。缓解：`edgeGate.vetoRate` 与 `decisionConversionRate` 双指标监控；转化率跌破 10% 即判定过度收紧并回滚 `k_min=0`。**本仓库有过「反思致永不开单」的同类事故**，这条不是理论风险。

**R-3 · 阶段一的 edge 粗估精度很差，可能给出一个悲观的错误结论。** 缓解（08 §六 自我反驳二的原文口径）：粗估在 `_build_notes` 与 trace 里**显式标注**；M1 的判据**只看结构性指标**（入账完整性、转化率、归因覆盖率），**不看净期望**——净期望的判据留到 M2。

**R-4 · 限价腿按 maker 计费可能高估 maker 收益。** 越过盘口的限价单会被 taker 撮合。缓解：`feeDeviationBps` 就是这个偏差的探针；若它长期 ≈ `taker − maker`（3 bps），说明挂价过于激进，处置是把 `ai_trade_maker_fee_bps` 临时上调至 taker 值（等价于「暂不承认 maker 收益」），并把挂价策略改造留给阶段二 01 §3.4。

**R-5 · 成本模型的 `impact_bps` / `half_spread_fallback_bps` 是拍出来的常数。** 若实测偏差长期超阈，说明**模型本身而非参数**有问题。缓解（01 §六 自我反驳三）：退回到「只用实测费率 + 实测滑点」的两项简化式，而不是继续加项。本 spec 的 `CostBreakdown` 逐项拆开正是为了让这次退化只需改 `estimate_round_trip_cost` 的一个分支。

**R-6 · 三个超红线文件继续增长。** 缓解：§3.10 给出逐文件的硬行数上限与「零业务分支」判据，评审时逐行核对；全部业务逻辑进六个新模块。**这不是消除技术债，只是承诺不再恶化**——`session.py` / `executor.py` / `backtest.py` 的拆分仍是本计划范围外的独立欠账，须在 §9 的看板里如实记为 `欠账`。

**R-7 · 工作树历来同时存在多个在制特性，混合提交风险真实。** 缓解：本特性触及的 `config.py` / `session.py` / `executor.py` / `backtest.py` 极可能同时含他人在制改动，提交须按**精确路径** `git add`，混合文件按 hunk 行级暂存（`git apply --cached --recount`，patch 文件须以 **bytes 读写**——`write_text` 在 Windows 上翻 CRLF 会弄坏 patch）。**严禁 `git add -A`**。

**R-8 · 旁路审计增加 OKX 私有接口调用。** `fetch_live_fills` 每 ≥60s 至多一次、且仅在有待审计挂账时发起。缓解：`ai_trade_fill_audit_interval_s` 可调；`ai_trade_fill_audit_enabled=false` 可整段关闭且不影响费率入账（入账是本地确定性的）。

**R-9 · 多标的 AI 分组下每个成员各有一份 FundingCache / 审计节流。** 5 个成员 ⇒ 每分钟 5 次公共 funding 请求 + 至多 5 次 fills 请求，仍远低于既有 5s 盯市的 reconcile 频率，无新增压力量级。缓解（v2 · RV-11 修正）：§7.3 用例 11 断言的是**调用次数上界**（5 成员 / 60 秒内 funding ≤ 5、fills ≤ 5）——脱网桩测**不可能证伪 429**，v1「跑一遍确认无 429」的说法是错的。真实限流风险只能由 L0 阶段的实盘观察证伪：`AI_TRADE_FILL_AUDIT_INTERVAL_S` 上调即可整体降频，`ai_trade_fill_audit_enabled=false` 可整段关闭且不影响费率入账。

**R-10 ·（v2 新增）作用域外溢：`ai_trade_*` 开关改写非 AI 的 live_auto 链路。** 根因是 `LiveExecutor` 被两条产品线共用（`ai_auto/manager.py:590` / `live_auto/manager.py:501`）而 v1 把 `FeeLedger` 建在 `__init__` 里。这类外溢的危险在于**它默认发生且完全静默**：L1 打开净额口径后，一条与本特性无关的产品线的会话止盈止损触发时点会被改变，而所有 v1 的零回归门禁（只断言下单 payload）都抓不到。缓解：§3.2.0 的 `fee_ledger: FeeLedger | None = None` 构造缝把作用域从「全局配置」降为「构造点」，`None` 即逐字节回退；Z-7 以四配置矩阵钉死。**这条风险的教训应推广**：本仓库凡是 AI 与非 AI 共用的类（`LiveExecutor` 是最大的一个），新增行为一律走构造缝而非 `settings` 读取。

**总回滚**：把 §3.9 的五个开关全部置为关闭值（`fee_accounting=false`、`fill_audit=false`、`net_pnl=false`、`funding=false`、`k_min=0`），系统逐字节回退到本特性之前的行为——无残留状态、无数据库变更、无迁移。若需连 AI 侧的记账一并摘除，删掉 `ai_auto/manager.py` 的 `fee_ledger=` 一行即可（§3.2.0），无需改任何其它文件。

---

## 九、`docs/improve/01-ai-autotrading` 实施状态标记规范

需求原文要求「已经完成的开发，需要在 `docs/improve/01-ai-autotrading` 中进行标记，方便后续随时继续开发」。**本设计节点受约束不得修改该目录**，故在此给出下游实现节点须逐字执行的标记规范。

### 9.1 三条硬规则

1. **单一真源**：实施状态只在 `00-总纲与现状诊断.md` 新增的 §七 看板里维护；01~08 各分册只放一行**指针 banner**，绝不各记一份状态（否则必然漂移）。
2. **只增不改**：`D-1 ~ D-18` 的编号与结论文字**冻结**，任何标记只允许**追加**，严禁改写既有句子、严禁删除既有表格行。
3. **状态词汇表固定五值**：`未开始` / `设计完成` / `已实现(未提交)` / `已合并` / `已验证`。`已验证`专指 §7.5 runbook 的对应级别放行判据在实盘上达标，**不是**「单测绿了」。

### 9.2 在 `00-总纲与现状诊断.md` 文末追加（逐字模板）

```markdown
## 七、实施状态看板（由实现节点维护，单一真源）

> 词汇表：未开始 / 设计完成 / 已实现(未提交) / 已合并 / 已验证（= §五验收指标在实盘达标）。
> 编号 D-1 ~ D-18 冻结；本表只允许追加与更新「状态 / 落点 / 提交 / 备注」四列。

| 编号 | 状态 | 落点 | 提交 | 备注 |
|---|---|---|---|---|
| D-16 | 设计完成 | `live_auto/fee_ledger.py` + `executor._close_pnl` | — | A0-1 记录默认开；A0-2 切换默认关 |
| D-17 | 设计完成 | `fee_ledger.leg_fee_bps` + `trade.ordType` | — | maker/taker 按腿归属 |
| D-18 | 设计完成 | `trade.expectedPx` + `FeeLedger.resolve` | — | 实测滑点进归因，不回写盈亏 |
| D-1  | 设计完成 | `ai_auto/cost_model.py` + `ai_auto/edge_gate.py` | — | `k_min` 默认 0（空转） |
| D-2  | 设计完成 | `okx/rest_client.get_funding_rate` + `ai_auto/funding.py` | — | 缺席即 degraded，绝不按 0 计 |
| D-15 | 设计完成 | `ai_auto/attribution.py` + `ai_auto/stats_ext.py` | — | 净期望 / 成本占比 / tStat |
| D-3 ~ D-14（除上列） | 未开始 | — | — | 阶段二 / 阶段三，见 08 号路线图 |

**阶段里程碑**

| 里程碑 | 状态 | 出口条件达标情况 |
|---|---|---|
| M1 · 阶段一 | 进行中 | 费率入账完整性 —；决策转化率 —；归因覆盖率 —；样本量 — |
| M2 · 阶段二 | 未开始 | — |
| M3 · 阶段三 | 未开始 | — |

**已知欠账（不在本阶段范围，但须持续可见）**

- `ai_auto/session.py`（1404 行）/ `live_auto/executor.py`（863 行）/ `ai_auto/backtest.py`（966 行）均超 `CLAUDE.md` 800 行红线；本阶段只承诺「不再恶化」，拆分待独立立项。
- 微观结构数据（D-3）：`books5`/`trades` WS 通道已实现但 AI 会话未订阅，成本模型的 `halfSpreadBps` 因此恒走常数回落值。
```

### 9.3 在 `01-交易成本与执行质量.md` / `08-实施路线图与里程碑.md` 的**结论先行段之后**各插一行 banner

```markdown
> **实施状态**：D-16 / D-17 / D-18 / D-1 / D-2 / D-15 见 [00 · 七、实施状态看板](./00-总纲与现状诊断.md#七实施状态看板由实现节点维护)；设计文档 [`docs/plans/ai-trade-cost-accounting-and-edge-gate/spec.md`](../../plans/ai-trade-cost-accounting-and-edge-gate/spec.md)。
```

### 9.4 在 `08-实施路线图与里程碑.md` §3.1 的阶段一表格**追加一列** `状态`

追加列（不改任何既有单元格文字），六行初值一律填 `设计完成`；实现节点合并提交后逐行改为 `已合并`，§7.5 runbook 达标后改为 `已验证`。

### 9.5 更新时机（强制）

- 设计节点产出 spec 后：相关根因行置 `设计完成`（本轮由实现节点顺手补，因设计节点受目录约束）。
- 代码实现完成但未提交：置 `已实现(未提交)`。
- 合并后：置 `已合并`，并在「提交」列填 commit 短 hash。
- §7.5 的对应级别放行判据在实盘达标后：置 `已验证`，并更新 M1 里程碑行的四个出口指标实测值。

---

## 十、给下游实现节点的落地顺序

严格按此顺序，每步单测绿了再进下一步（每一步都是可独立回滚的完整增量）：

1. `config.py` 的 14 个字段 + `.env.example`（无行为改动，先把配置面钉死）。
2. `ai_auto/cost_model.py` + `test_ai_trade_cost_model.py`（纯函数，零接线）。
3. `ai_auto/edge_gate.py` + `test_ai_trade_edge_gate.py`（纯函数，零接线）。
4. `live_auto/fee_ledger.py`（含 `audit_fills` 自由函数）+ `test_ai_trade_fee_ledger.py`；随后接 `executor.py`（第七缝 `fee_ledger` + A0-1 记录 + A0-2 开关 + 三处复位）与 `ai_auto/manager.py` 的构造缝，跑 **Z-1/Z-2/Z-5/Z-7** 零回归门禁。**`fee_ledger` 缝与 `manager.py` 的传参必须同一步落地**——先落 executor 后落 manager 会有一个中间态：AI 会话拿不到 ledger，A0-1 静默不生效。
5. `okx/rest_client.get_funding_rate` + `okx/trading._normalize_fill` 的 `feeCcy`（含 docstring 契约段）+ `ai_auto/funding.py` + 缓存单测。
6. `ai_auto/attribution.py` / `stats_ext.py` + 单测（含 `merge_cost_stats` 冲突即抛）。
7. `session.py` 接线（`_apply_edge_gate` / trace / 归因 / funding refresh / **`_finalize_run` 调用点**的 stats 合并 + `costSnapshot` 两条会话内通道）+ `trace.py` 的 `edge_gate_event` 与 `PHASES` 追加，跑集成测试 §7.3。
8. `backtest.py` 同位插入 + §7.4 同构测试。
9. 前端四文件镜像 + `aiTradeEdgeGate.test.ts`。
10. 按 §9 标记 `docs/improve/01-ai-autotrading`。
11. **全量跑**后端 + 前端测试（终审必跑全量：本仓库有过「终审只跑增量，漏掉第 14 个镜像点」的前科），再按 R-7 的精确路径暂存提交。

**已知的、与本特性无关的工作树噪声**（提交前须剔除，勿一并 `git add`）：`.env.example` / `desktop/src/lib/config.js` / `services/quant/app/config.py` 上可能存在的他人在制 hunk、`docs/diagnoses/*` 的大量未跟踪目录、以及 factor-expansion 的既有失败用例（非本次回归）。

---

## 十一、评审结论（Review Verdict）

**结论：有条件通过（v2）。**

**评价**：本设计的问题定位是准确且有价值的——它没有去猜"哪个 bug 导致亏损"，而是先证明**盈亏口径本身是错的**，并把"先记账、再拦不值得的交易"作为唯一的阶段一目标。三条硬约束（决策轮零网络 IO / 盈亏必须确定性 / 门禁只会更保守）、与前三层保护同构的模块形态、以及默认关闭 + 三级 runbook 的上线纪律，都与本仓库既有的工程惯例一致。v1 的全部代码引用经逐条回读**准确无误**，这在本仓库的历史上并不常见，值得记一笔。

v1 的缺陷集中在**推理层而非事实层**：两个 P0（`_compute_ai_stats` 是模块级函数导致的落点不可实现、`LiveExecutor` 跨产品线共用导致的作用域外溢）都源于"引用了正确的行号，但没有追问那个符号的宿主是谁"。这也正是 §7.1 的零回归门禁需要 Z-7 的原因：只断言下单 payload 逐字节相同，抓不到"另一条产品线的平仓时点被改变"这一类改动。

**放行条件**（下游实现节点必须逐条落实，任一条未做即判不合格）：

1. **作用域缝**（RV-2）：`LiveExecutor.__init__` 的 `fee_ledger: FeeLedger | None = None` 第七缝落地，且**只有** `ai_auto/manager.py` 传入实例；`live_auto/manager.py` 零改动。Z-7 的四配置矩阵用例必须绿。
2. **统计合并落点**（RV-1）：合并写在 `_finalize_run` 的调用点（`session.py:1285` 之后），`_compute_ai_stats` 的签名与返回值**一字不动**；§7.3 用例 8 必须能证伪"改了函数内部"。
3. **`entry_fee_usd` 三处复位**（RV-3）：`_settle_close` / `_settle_benign_close` / `_on_reconcile_ok` 全覆盖，§7.2 用例 8 以第二笔净额可证伪。
4. **OKX fee 符号归一**（RV-4）：收费为负 ⇒ 取正；返佣单列 `feeRebateSamples` 且不入均值；`fee == 0` 挂账保留不消化。§7.2 用例 9。
5. **部分成交按增量计费**（RV-5）：`sz=delta_sz if delta_sz is not None else total_sz`，与 `trade["qty"]` 同口径。§7.2 用例 2 的断言必须写成"不是 `leg_fee(3)+leg_fee(5)`"的可证伪形式。
6. **会话内可观测通道**（RV-6）：`costSnapshot` 两条通道落地，且与 `metrics_json` 出自同一 `build_cost_stats`。同时更正任何仍宣称 `GET /ai-trade/status` 带统计字段的文案——**它没有**。
7. **上界取数路径**（RV-7）：接 `executor.risk.take_profit_pct` / `_oco_risk.take_profit_pct`，**不得**误接 `session.take_profit_usd`；`None` 时上界整体不生效。
8. **行数预算机械核对**（RV-8）：审计流程下沉 `fee_ledger.audit_fills`；提交前跑 `git diff --numstat` 逐一核对 §3.10 的四条上限（Z-8）。
9. **P2 项一并落实**：`PHASES` 追加 `"edge_gate"`（RV-9）、`trading.py` docstring 契约段同步（RV-15）、`closeFeeCoverage` 更名（RV-13）、R-9 的断言改为次数上界（RV-11）。

**遗留的、本轮有意不解决的欠账**（须在 §9 的看板里如实记为「欠账」，不得静默）：

- `session.py`（1404 行）/ `executor.py`（863 行）/ `backtest.py`（966 行）超 `CLAUDE.md` 800 行红线——本轮只承诺"不再恶化"（§3.10 + Z-8），拆分待独立立项。
- 阶段一的 edge 是 ATR 粗估而非概率化先验，M1 的判据据此**只看结构性指标、不看净期望**（§8 R-3）。
- 跨会话的成本聚合看板不在本轮范围，L0/L1 的"滚动 50 笔"需运维手工累加（§7.5）。
- 微观结构数据（D-3）未接入，`halfSpreadBps` 恒走常数回落值。

**复审要求**：实现节点完成后，终审**必须跑全量**后端 + 前端测试（§10 第 11 步），并逐条核对上述 9 条放行条件。本仓库有过"终审只跑增量、漏掉第 14 个镜像点"的前科，本特性的镜像点（后端 8 文件 + 前端 4 文件 + 测试 7 文件）比那次更多。

---

## 十二、实施过程发现的方案缺陷（Issues Found During Implementation）

> 由实现节点在落地 v2 时追加（2026-07-25）。每条都写明「v2 怎么说的 / 实际是什么 / 采取的修正」，
> 已按修正后的做法实现。**均未改变 §二 的目标与 §7.1 的零回归门禁**。

### I-1（**阻断级**）`gate_status` 少一个 `action` 形参，否则观望轮会误触发不可用告警

- **v2 的写法**：`def gate_status(edge, cost, gate_reason: str | None) -> str`（§5.2）。
- **实际**：§5.3 要求「`k_min > 0` 时**每轮都发一条** `edge_gate` 事件」，而 §3.6 的
  `estimate_gross_edge_bps` 只对 `open_*` 估算（其余动作返回 `None`）。二者叠加的结果是：一个持续
  `hold` 的会话每轮都会被判成 `unavailable`，连续 20 轮即推一条
  `AI_TRADE_EDGE_GATE_UNAVAILABLE`——而那条告警的文案说的是「**期间开仓一律放行**」，此时根本没有
  开仓意图。这不是精度问题，是**告警语义错误**，会把运维引向排查一个不存在的 ATR 故障。
- **修正**：`gate_status(action, edge, cost, gate_reason)`。非受控动作恒 `pass`（本轮门禁无事可做，
  不是"不可用"）。§7.2 新增用例 `test_gate_status_reports_pass_for_non_gated_actions` 钉死。

### I-2 门禁空转时不应发资金费率请求

- **v2 的写法**：§3.5「接线：`session._monitor_tick` 末尾加一行 `await self._funding.refresh(...)`」，
  只由 `ai_trade_funding_enabled`（默认 `True`）控制。
- **实际**：`k_min` 默认 `0`（门禁空转），此时 `CostBreakdown` 没有任何消费方——资金费率取回来只是
  写进内存快照然后被丢弃。默认配置下这会给**每个** AI 会话每分钟一次无消费方的公共请求，多标的分组
  按成员数线性放大（§8 R-9 关心的正是这个量级）。
- **修正**：`FundingCache` 增 `enabled` 形参，由 `CostRuntime` 以
  `funding_enabled and k_min > 0` 构造。L2 阶段（`k_min=2.0`）行为与 v2 完全一致；L0/L1 阶段**一个
  请求都不发**。§7.2 新增 `test_disabled_cache_never_sends_a_request` 钉死。

### I-3 新增 `ai_auto/cost_runtime.py`（第 7 个新模块），否则 §3.10 的 `session.py ≤ +48` 不可达

- **v2 的写法**：§4.1 列 6 个新模块；§3.10 给 `session.py` 的硬上限 `+48` 行且「超出即判不合格」。
- **实际**：按 v2 的接线清单逐项落地后 `git diff --numstat` 实测 **+66**。超出的来源不是冗余，而是
  「4 个模块各自的 import + 3 个状态字段 + 每个字段的取数样板」这一层**编排状态**本身。
- **修正**：新增 `ai_auto/cost_runtime.py`（`CostRuntime`），把资金费率缓存 / 归因账本 / 门禁编排 /
  统计装配的状态与编排全部下沉。这**不是新造框架**——它逐字复用本仓库已有的同款范式
  `profit_guard_runtime.ProfitGuardRuntime`（其存在理由与本条完全相同：§3.10 的行数纪律）。落地后
  `session.py` 实测 **+48**，四个文件的 Z-8 机械判据全部达标：
  `executor.py +32 / session.py +48 / backtest.py +19 / manager.py +3`。
- **连带的两处签名微调**（同为「把业务状态挪出会话」）：
  - `AttributionLedger(net_mode=...)` 构造期读一次 A0-2 开关，`record_close(trade)` 不再逐笔传
    `net_mode`（它是会话级静态量）；`net_mode` 保留为可选覆盖形参供单测对拍两种口径。
  - `record_close` 的 `cost_bps` / `edge_bps` 改由账本自己记住（`note_gate` 写入），使会话不必维护
    这两个字段。

### I-4 旁路审计会落在决策轮的**执行**阶段（不违反「决策轮零 IO」，但 v2 的表述需要精确化）

- **v2 的写法**：§3.1「决策轮内零网络 IO……成交明细审计一律跑在 5s 盯市 tick 上」。
- **实际**：`audit_fills` 挂在 `executor.reconcile()` 上，而 `session._execute_decision` 在下单后
  **也会调 `reconcile()`**（既有行为，它本就是网络操作：`_poll_resting` + `_read_positions`）。故审计
  确实可能发生在一个决策轮之内——只不过是在**执行**阶段，不在决策**输入装配**阶段。
- **结论**：这不是缺陷，是 v2 表述的粗糙。真正需要保证的不变式是「**决策输入的装配与门禁判定**零
  网络 IO」（资金费率恒读内存快照），这一条已由 §7.3 用例 `io_calls["funding"] == 0` 钉死；审计的
  额外请求由 `ai_trade_fill_audit_interval_s`（默认 60s）封顶，3 轮至多 1 次。
- **另**：`audit_fills` 的委托放在 `reconcile()` 的**开头**（`_poll_resting` 之后）而非 v2 说的
  「末尾」，理由有二：① 使持仓读取失败的分支也照常审计（审计与持仓读数互不依赖）；② 末尾插入需把
  两条 `return` 改写为 `+=`（+3 行），而开头插入只要 +2 行——`executor.py` 的预算只有 32 行且已用满。

### I-5（**阻断级**）回测的 ATR 序列只在装配了利润保护时才构建，门禁在回测里会恒 `unavailable`

- **v2 的写法**：§3.6「ATR 在实盘与回测两侧都已存在（`backtest._atr_at(i)`）」，§3.7 要求回测同位插入。
- **实际**：`_atr_at(i)` 读的 `self._atr_series` 在 `_setup` 里是 **`if self.profit_rule is not None`**
  才计算的。一次没有装配利润保护的 AI 回测里，`_atr_series` 恒为 `None` ⇒ 粗估 edge 恒不可用 ⇒ 门禁
  fail-open 恒放行。「回测同位插入」会成为一句**跑得通但什么都不做**的空话，而且是静默的——回测结果
  与门禁关闭时逐字节相同，看不出任何异常。
- **修正**：把该条件放宽为 `profit_rule is not None or k_min > 0`（净增 0 行）。§7.4 新增用例
  `test_backtest_edge_gate_vetoes_and_skips_reflection` 以「被拦轮零反思 + 零成交」可证伪。

### I-8 `edgeGate` 计数的分母必须是**受约束的轮次**，不是全部决策轮

- **v2 的写法**：§6.3 给出 `"edgeGate": {"pass": 118, "veto": 46, "unavailable": 3, "vetoRate": 0.275}`，
  §7.5 的 L2 放行判据是「门禁拦截率落在 20%~70%」，但没写清 `pass` 到底数的是什么。
- **实际**：§5.3 要求「每轮都发一条」事件，而门禁只作用于 `open_*`。若把观望 / 平仓 / 撤单轮也记成
  `pass`（它们的 `status` 确实是 `pass`——门禁无事可做），分母就变成了**全部决策轮**。AI 会话的观望
  轮占比很高（`aiStats.holdRate` 本就是既有观测项），`vetoRate` 会被稀释到 20% 以下，而 §7.5 对这个
  读数的处置是「**门禁未生效**，查 `edgeGate.unavailable` 是否居高」——一条把运维引向错误排查方向的
  指标。同理，一个持续观望的会话会连续 20 轮记 `unavailable` 并误触发告警（这也是 I-1 的另一半）。
- **修正**：`EdgeGateOutcome` 增 `gated: bool`（= 动作在豁免矩阵之外），`CostRuntime.note_gate` 只对
  `gated` 轮推进计数与不可用连击。**trace 事件仍每轮都发**（透明性不打折），只是不进计数分母。
  §7.2 / §7.3 各新增一条用例，后者断言「3 个观望轮 ⇒ 3 条事件 + 三个计数全 0 + 连击 0」。

### I-7 平仓腿「记的费率」与「扣的费率」必须同口径（v2 的 `net_close_pnl` 默认值会让两者分叉）

- **v2 的写法**：§3.3 的代码块里 `_close_pnl` 调 `net_close_pnl(gross, entry_fee_usd=…, exit_px=…,
  sz=…)`，不传 `ord_type` ⇒ 走签名默认值 `"market"`（= taker）。
- **实际**：§3.2.2 的费率归属表规定**限价平仓采纳走 maker**，故同一笔限价平仓上
  `trade["fee"]` 记的是 maker、而 `trade["pnl"]` 扣的是 taker。归因表按 `gross = pnl + fee`
  反推毛额（§3.8 的口径钉子），于是毛额被系统性低估 `taker − maker`（默认 3 bps）——**静默、只在
  限价成交上出现、随成交笔数累积**，正是 A0-2 想要消灭的那一类误差。
- **修正**：`_close_pnl(exit_px, *, ord_type="market")` 与 `_close_return_pct` 同款透传，由
  `_settle_close` 把它已有的 `ord_type` 传下去（全部为**改写既有行**，净增 0 行）。其余五个调用点
  （良性平仓 / 外部干预 / flatten / 解释合成）都是真市价，默认值即正确。
  §7.2 新增 `test_limit_close_deducts_the_maker_fee_it_actually_recorded` 钉死。
- **注意**：这与 §3.4 成本**模型**里「`feeCloseBps` 恒取 taker」并不冲突——那是**开仓时**对未来平仓腿
  的保守预估（届时委托类型未知），这里是**平仓已发生后**的如实记账。两者本就该用不同的数。

### I-6 `_settle_close` 需要两个新形参才能把限价平仓腿的 maker 费率与期望价传下去

- **v2 的写法**：§4.2 只说「六处调用点补传 `ord_type`/`expected_px`」。
- **实际**：`_settle_close` 被三条路径共用（市价平仓 / 限价平仓采纳 / flatten），而 `_make_trade` 在
  它内部被调用，故这两个值必须先进 `_settle_close` 的签名才能到达 `_make_trade`。
- **修正**：`_settle_close(..., ord_type="market", expected_px=None)`，默认值保证 `live_auto` 与全部
  既有调用点 payload 逐字节不变。已计入 `executor.py` 的 +32 预算。
