# AI 自动交易 · 基础策略（铁律层）与 MA 通道开单约束设计方案

> Feature slug: `ai-trade-base-strategy-ma-band`
> 版本：**v2**（Subtask #1 评审后修订；4 个 P0 + 9 个 P1 已就地修复，见 §评审记录 / §评审结论）
> 关联既有特性：`ai-auto-trading`（v2 已上线）、`ai-trade-pre-trade-reflection`（`407d274`）、
> `ai-trade-limit-orders`（`3bbc503`）、`ai-trade-position-sizing-steps`（`cc3114b`）、
> `ai-trade-reasoning-chain`、`ai-trade-hold-inertia-observability`（`fe90c73`）
> 需求出处：Task Board「Original Task」——「现在自动交易的时候，现在有【常规策略、组合策略、因子策略】，
> 现在需要添加一个新的策略类型：【基础策略】……无论用户使用何种其他交易策略，都默认开启（当然启动自动交易
> 的时候可以手动关闭），这种策略的优先级最高，Agent 必须强制遵守。其中预制一个策略：1【交易的开单，无论
> 买多还是买空，仅能够在对应自动交易的时间级别上的 上下 MA 均线上下边界，或是 MA 均线中线附近开单】」

---

## 评审记录（Review Notes · Subtask #1）

> **评审方式与一处必须说清的事实**：本节点收到的输入是**空的**——上游 Subtask #0（方案设计）报告
> `completed`，但 `docs/plans/ai-trade-base-strategy-ma-band/` 只被创建为**空目录**，`spec.md` 从未生成
> （`find docs/plans -newermt 2026-07-19 -type f` 零命中）。评审节点不能对不存在的文档「评审」，也不能
> 让 Subtask #2 拿着空目录开工，故本节点**代偿产出全文**：先按需求 + as-built 代码重建设计（§〇~§八），
> 再以评审者身份对重建稿逐节质证，把质证结论落成下表并就地修复。下表每一条都经**回源码核对**（file:line
> 皆为本次实测），不是纸面推断。
>
> **重建过程中最关键的判断**：需求把「基础策略」与「常规/组合/因子策略」并列成「第四种策略类型」，
> 但这三者在本仓库是**策略库里的行**（`StrategyMeta.kind ∈ {atomic, combo, factor}`，
> `strategies/base.py:35`、`strategies/combination.py:35`、`strategies/factor_score_strategy.py:93`），
> 它们进入 AI 自动交易的唯一通路是 `strategyIds → specs → 影子 broker → signalBoard`
> （`schemas.py:488` → `api/ai_auto.py:95` → `signals.py:125` → `session.py:799`）。而信号板在本系统里
> 被**宪法级地定义为「参考而非指令」**——系统 prompt 首要原则原文：「交给你的多个策略信号……**只是决策的
> 参考与触发线索，不是必须执行的确定性指令**……你有权采纳、存疑或**推翻**任何单一信号」
> （`prompts.py:79-82`）。**把「必须强制遵守」的规则做成信号板的第 N 个成员，等于把铁律降级成建议**，
> 与需求正面冲突。故本方案把「基础策略」实现为**独立于信号板的硬约束层**（确定性门禁 + 宪章级 prompt），
> 而不是策略库里的第四个 `kind`。这是 P0-2 的由来，也是全篇的架构主轴。

| # | 严重度 | 位置 | 问题（一句话） |
|---|---|---|---|
| **P0-1** | **P0** | 上游节点产物 | **输入缺失**：Subtask #0 报 `completed` 但只留下空目录，`spec.md` 不存在 → 评审无对象、Subtask #2 无输入。**修复**：本节点代偿重建全文并标注 v2；`## 实施过程发现的方案缺陷` 段预留给 Subtask #2/#3 |
| **P0-2** | **P0** | 架构选型（§2.1） | **「第四类策略」的字面实现必然失效**：若新增 `kind="base"` 的策略行并混进 `strategyIds`，它只会成为 `signalBoard` 的一行（`session.py:832` `_view_full` 仅喂 `id/name/stance/latestSignal/strength/reason/风控`），而系统 prompt 明写信号可被推翻（`prompts.py:79-82`）——**架构上不可能「强制遵守」**。**修复**：改为硬约束层，走「宪章 prompt（软引导）+ 确定性门禁（硬保证）」双层，绝不进信号板（§2.1/§2.3） |
| **P0-3** | **P0** | 判定价（§2.4） | **限价单可整体绕过铁律**：`ai_trade_limit_orders_enabled` 默认 `True`（`config.py:163`），Agent 可给出最新价 ±2% 内的任意 `limitPrice`（`live_auto/resting.py:135` `validate_limit_intent`），**真实成交价 = limitPrice ≠ lastPx**。门禁若只判 `lastPx`，「在中线附近」的市价判定会放行一张挂在带外的限价单。**修复**：判定价单点收敛为 `entry_reference_px = limit_px if ord_type=="limit" else lastPx`（§2.4），并列为冻结不变式 + 专项单测 |
| **P0-4** | **P0** | 数据不可用路径（§2.6） | **两种错法都是事故**：带算不出时若 fail-open，铁律恰在数据异常（最危险）时静默失效；若 fail-closed 却无可观测性，则复演 `ai-trade-reflection-hold-inertia` 的「永不开单、用户不知为何」故障（该特性存在的全部理由）。**修复**：fail-closed + 专用 degrade 码 `base_rule_band_unavailable` + 每轮 `base_rule` 轨迹段 + 前端徽标 + 连续 N 轮不可用升级为 `error` 事件（§2.6/§2.9） |
| **P1-1** | P1 | 门禁落点（§2.5） | **落在 `_map_decision` 会被重试循环反复烧钱**：`decide` 的重试只认 `_map_decision` 返回的 degrade（`decider.py:220-229`），而同一轮价格与带都不变 ⇒ 重试必然同样被否，纯耗 LLM 配额与 45~60s 超时预算。**修复**：门禁做成 `base_rules.py` 纯核心，由 `session`/`backtest` 在 `decide` **之后**调用；并显式冻结「两个新 degrade 码**不得**进入 `_RETRYABLE_DEGRADE_REASONS`（`decider.py:73`）」（§2.5/§6.1） |
| **P1-2** | P1 | 管线顺序（§2.5） | **顺序错位有两个具体代价**：排在反思之后 ⇒ 一次注定被否的开仓仍会烧掉一次反思 LLM 调用（`decider.py:369` `reflect`，超时预算 45s），且让反思去「确认」一个非法决策；排在 `_update_hold_streak`（`session.py:487`）之前 ⇒ 污染惯性观测（N2 要求以**第一阶段模型真实意愿**计数）。**修复**：固定顺序 `decide → _update_hold_streak → 基础策略门禁 → 共识门禁 → 反思 → 执行`（§2.5 管线图） |
| **P1-3** | P1 | prompt 冻结门禁（§2.7） | **既有冻结断言会当场破**：`test_ai_trade_prompts.py`（374 行）对系统 prompt / 决策 prompt 有逐字冻结断言，`test_ai_trade_position_step.py:132` 更有恒等断言 `build_decision_tool_schema(0.0) is DECISION_TOOL_SCHEMA`。若无条件注入铁律文案即全线飘红。**修复**：沿用 `allow_limit`/`position_step_pct` 的**默认空垫片**范式——新形参 `base_rules_text: str = ""` 默认渲染逐字节不变；本特性**不新增工具字段、不改 action 枚举**，决策工具 schema 零改动（§2.7） |
| **P1-4** | P1 | 回测同源（§2.8） | **回测不同源 = 用户被自己的回测误导**：`backtest.py:338` `_run_decision` 是实盘门禁的镜像（共识 `:349`、反思 `:357-360`）。基础策略若只落实盘，AI 回测会系统性地比实盘更容易开仓，用户据此调出的参数在实盘直接失真。**修复**：`_run_decision`/`_build_payload`（`backtest.py:401`）同调纯核心，band 取回测帧的因果切片（§2.8） |
| **P1-5** | P1 | 默认开启的兼容语义（§2.10） | **「默认开启」与「零回归」互斥，必须分层解决**：字段缺省若在后端 dataclass 层默认为开，既有直接构造 `AiTradeGroupStartRequest` 的单测（`test_ai_trade_group.py` 546 行、`test_ai_trade_session.py` 742 行）行为集体改变；若默认为关，则老客户端/桌面旧包拿到的是「默认关闭」，与需求相悖。**修复**：**默认在 API 层解析**（`api/ai_auto.py` `_resolve_base_rules`：字段缺省 → 按 settings 装配开启态），dataclass 默认 `None`=不装配 ⇒ 既有直接构造零回归（§2.10） |
| **P1-6** | P1 | 前端偏好持久化（§三 前端） | **新增偏好键必须按 [R-7] 声明为可选并在读点兜底**：`aiTradeConfig` 进 `partialize`（`store.ts:1005`），老用户 localStorage 中无新键；`AiTradeConfig` 既有可选字段范式见 `aiTradeTypes.ts:455-459`。**修复**：`baseRules?:` 可选 + 所有读点 `?? 默认值`；**明确不要求 persist version 从 29 起跳**（`store.ts:972`）——既有 `positionStepPct`/`wakeIntervalMinutes` 的迁移（`:1138-1160`）自注「字段本不强制迁移」，属美化性回填，本特性不做，避免为零收益改动动 migration 链 |
| **P1-7** | P1 | 反手语义（§2.3） | **反向开仓被拦后的状态未定义**：`LiveExecutor._project_signals`（`executor.py:156-159`）把反向 `open_*` 展开为 `[close(reverse), open]` 两腿；门禁作用于**决策级**，否决即两腿都不发、原持仓保留。若不写死，实现者可能「放行 close 腿」造成半反手态。**修复**：显式定义「否决 = 整个反手不发生」，并冻结豁免矩阵——`close`/`hold`/`cancel_order` **永不**被基础策略拦截（镜像共识门禁 `signals.py:154` 的同款豁免），prompt 同步告知「离场用 close，不受基础策略约束」（§2.3） |
| **P1-8** | P1 | 「附近」的量化（§2.2） | **用绝对百分比定义「附近」跨周期不可用**：1m 与 1D 上的「0.5% 以内」完全不是同一量纲，且同一周期在高低波动期含义漂移。**修复**：用**带宽归一化的 %B 距离**（`tolerance` 默认 0.15，量纲自适应），与既有 `market_digest._boll_reading` 的 `bollPctB`（`market_digest.py:215`）**同口径同真源**；并注明 `tolerance=0.25` 是退化点（三锚区首尾相接 ⇒ 「只要在带内即放行」），故入参上界锁 0.25（§2.2） |
| **P1-9** | P1 | 降级原因中文化（§2.9） | **裸英文码会直接怼到用户脸上**：`aiTrade.ts:365` 现为 ``已降级观望（${degradeReason}）`` 原样拼接。基础策略是**高频**触发项（震荡市里大多数轮都可能被否），裸 `base_rule_ma_band(pctB=0.42)` 不可读。**修复**：新增 `describeDegradeReason(reason)` 前缀映射，未命中**原样回退** ⇒ 既有 `low_consensus(1/2)` 等断言不破（§三 前端 / §6.3） |
| P2-1 | P2 | §2.3 | `directional`（做多不追顶 / 做空不杀底）**默认关**：开启会与「突破/趋势跟随」类策略正面冲突，属可选增强而非需求原文 |
| P2-2 | P2 | §2.5 | 「限价被否 → 允许 Agent 一次重议价」本期不做（Non-goal）：实现它必须把门禁下沉进 `decider`，直接违反 P1-1 |
| P2-3 | P2 | §2.9 | 不新增 `baseRuleVetoes` 统计键：`_compute_ai_stats`（`session.py:1062`）的 `degradedRounds` 已按 `degradeReason` 非空计数，自动覆盖 |
| P2-4 | P2 | 既有缺陷（不修） | `PaperStrategySpec.strategy_kind` 把 `factor` 折叠成 `atomic`（`paper/strategy_factory.py:51`），且 `strategy_kind` 在整个 `app/ai_auto/` 零引用——与本特性无关的既有缺陷，记录不修 |
| P2-5 | P2 | §三 前端 | `AiTradeLaunchModal.tsx` 已有 ~15 个输入（925 行），新参数必须收进「高级参数」`<details>` 折叠区，避免表单膨胀 |
| P2-6 | P2 | §5.3 | 桌面端 env 透传**无需改动**：`desktop/src/lib/config.js:72` 已按 `key.startsWith('AI_TRADE_')` 白名单放行 |

**修复说明**：P0-1..P0-4 与 P1-1..P1-9 已**全部就地修复**于 v2 正文（分别落在 §2.1、§2.2、§2.3、§2.4、
§2.5、§2.6、§2.7、§2.8、§2.9、§2.10、§三、§6.1、§6.3）。P2 项按「记录不强制」处理，其中 P2-1 / P2-2
需 Subtask #2 知悉即可，无待决拍板项。

---

## 〇、As-built 对齐（Reconciliation · 本方案的事实基线）

沿用 `ai-auto-trading-enhancements` v3 房规：**以 as-built 代码为准，不据历史 spec 纸面签名**。下列断言
皆为本次动工前实测：

| # | 断言 | 证据 |
|---|---|---|
| A-1 | AI 自动交易的包在 `app/ai_auto/`（**不是** `app/engine/ai_auto/`），共 18 个模块 5147 行 | `services/quant/app/ai_auto/` |
| A-2 | 三类策略的真源是 `StrategyMeta.kind ∈ {atomic, combo, factor}`；前端分类表在 `strategyCatalog.ts:4-8`（`常规策略/组合策略/因子策略`） | `strategies/base.py:35`、`strategies/combination.py:35`、`strategies/factor_score_strategy.py:93`、`apps/web/lib/strategyCatalog.ts:4-8` |
| A-3 | **AI 启动弹窗并不按三类分组**：`AiTradeLaunchModal.tsx:435-463` 是一个扁平复选清单（`api.strategies` → `s.id/s.name`），`kind` 根本没被读 | `apps/web/components/Agent/AiTradeLaunchModal.tsx:165,435-463` |
| A-4 | 策略只以 `strategyIds` 进入会话，`kind` 既不校验也不持久化、更不喂给 LLM | `schemas.py:488` → `api/ai_auto.py:95` → `manager.py:510` → `session.py:832` `_view_full` → `prompts.py:217` `_format_board` |
| A-5 | 决策管线现有顺序：`decide`(含重试) → `_update_hold_streak` → 共识门禁 → 反思 → `_execute_decision` | `session.py:483-493` |
| A-6 | 共识门禁是**纯函数 + 实盘/回测共用**的既有范式，且只拦 `open_*`、永不拦 `close/hold` | `signals.py:137-161`（`:154` 豁免行）、`session.py:669-678`、`backtest.py:349` |
| A-7 | 决策工具是 `submit_trade_decision`（tool-use，非围栏 JSON）；动作枚举 `open_long/open_short/close/hold`（+ 限价总闸开时 `cancel_order`） | `prompts.py:14,17`、`decider.py:54,58` |
| A-8 | `_map_decision` 是唯一的决策合法化点；`decide` 的重试**只认它返回的 degrade** | `decider.py:329-366`、`:220-229`、`:73` |
| A-9 | `engine/indicators.py` 已有 `sma/ema/bollinger/atr`；**`bollinger` 的第二个返回值就是中轨 SMA**；无任何 MA 通道/Keltner 专用助手 | `engine/indicators.py:19,14,38,48` |
| A-10 | `market_digest` 已按 `_BOLL_PERIOD=20,_BOLL_STD=2.0` 算 `bollPctB` 并喂给 LLM（本方案与之同口径） | `market_digest.py:33,215-233` |
| A-11 | 决策 payload 的装配单点是 `_build_decision_payload`；band 可直接挂在此处，与 `lastPx` 同源 | `session.py:782-807` |
| A-12 | 会话/分组配置走 `strategy_runs.config_json` **零列迁移**（`positionStepPct`/`wakeIntervalMinutes` 先例） | `manager.py:585-597`（`:594` 注「零列迁移」）、`db/models.py:58` |
| A-13 | 轨迹相位由 `trace.trace_event` 发、前端 `reduceRoundPhase` 归约，**未知相位是 no-op**（老前端安全） | `trace.py:113`、`apps/web/lib/store.ts:152-202`（`:199` `default: return t`） |
| A-14 | 降级原因在前端**原样**拼进中文串 | `apps/web/lib/aiTrade.ts:360-373`（`:365`） |
| A-15 | `ai_trade_limit_orders_enabled` 默认 `True`，限价价带默认 2% | `config.py:163,166` |

---

## 一、Overview（概述）

### 1.1 需求解读

需求要新增的不是「第四种可选策略」，而是**一层凌驾于所有策略之上的开单纪律**：无论用户选了常规/组合/因子
中的哪些策略，开单（`open_long`/`open_short`）只允许发生在**执行周期的 MA 通道上轨、下轨或中轨附近**。
它的三个定语决定了实现形态：

- **「默认开启，启动时可手动关闭」** ⇒ 配置项 + 弹窗开关，缺省即开（§2.10）。
- **「优先级最高」** ⇒ 它必须在 LLM 之外、在真实下单之前生效，且不可被 LLM 的任何自述理由覆盖（§2.3）。
- **「Agent 必须强制遵守」** ⇒ 双层：宪章 prompt 让 Agent **知道并主动遵守**（软），确定性门禁让它
  **无法违反**（硬）。只有软层是空话，只有硬层则 Agent 会反复撞墙、浪费轮次——两层都要（§2.7）。

### 1.2 目标（Goals）

1. 新增**基础策略层**（Base Strategy Layer）：一组与信号板正交的硬约束，默认全部开启、可在启动弹窗关闭。
2. 预置第一条规则 **`ma_band_entry`（MA 通道开单约束）**：开仓的**参考成交价**必须落在执行周期 MA 通道的
   上轨 / 中轨 / 下轨**任一锚区**内，否则本轮开仓降级为 `hold`。
3. 实盘与 AI 回测**同源生效**，参数、判据、降级码逐字一致。
4. 全链路可观测：Agent 每轮看得见通道与自己所处位置；用户在执行轨迹里看得见「放行 / 否决 / 数据不足」。
5. 对既有行为**零回归**：总闸关闭或规则未装配时，prompt 渲染逐字节不变、决策工具 schema 恒等、
   管线行为与今日一致。

### 1.3 非目标（Non-goals）

- **不**新增策略库中的 `kind="base"` 行，**不**改 `StrategyMeta`/`PaperStrategySpec`（P0-2）。
- **不**新增决策工具字段或动作枚举（保住 `build_decision_tool_schema(0.0) is DECISION_TOOL_SCHEMA` 恒等）。
- **不**为被否决的限价单提供「重议价重试」（P2-2：会把门禁拖进 `decider` 重试循环，违反 P1-1）。
- **不**约束平仓 / 撤单 / 风控自动退出（止盈止损、`max_hold_bars`、staleness flatten）——铁律只管**开单**。
- **不**做多规则编排 UI（本期只有一条预置规则；`base_rules.py` 的注册表结构为后续留位，但不提前造框架）。
- **不**触碰 `live_auto/`（策略驱动实盘）：本特性只作用于 AI 自动交易链路。

---

## 二、Technical design（技术设计）

### 2.1 分层与新增件（P0-2 修复）

```
                 ┌──────────────────────── 顾问层（advisory，可被推翻）────────────────────────┐
 strategyIds ──► │ 常规/组合/因子策略 → 影子 SimBroker → signalBoard → consensus → prompt      │
                 └───────────────────────────────────────────────────────────────────────────┘
                                                    │  LLM 综合研判
                                                    ▼
                                        TradeDecision（action/size/ordType/limitPx）
                                                    │
                 ┌──────────────────── 铁律层（mandatory，不可推翻）· 本特性 ────────────────────┐
 baseRules   ──► │ base_rules.apply_ma_band_gate(decision, band, rule) → (decision', reason)   │
                 │  ↑ 同一 band 对象也被渲染进 prompt（Agent 决策前就看得见）                    │
                 └───────────────────────────────────────────────────────────────────────────┘
                                                    ▼
                                    共识门禁 → 二次反思 → LiveExecutor 真实下单
```

**新增件只有一个后端模块**：`services/quant/app/ai_auto/base_rules.py`（纯函数、无 IO、无 LLM、无 broker，
镜像 `signals.apply_consensus_gate` / `market_digest.build_market_digest` 的既有姿态）。它同时是
「算通道」「判合规」「渲染文案」三件事的**唯一真源**——三处若各写一份，UI 显示「合规」而门禁判「否决」
这类信任崩塌是必然的（`position_ladder` 的 [R-5] 教训）。

```python
MA_BAND_RULE_ID = "ma_band_entry"

@dataclass(frozen=True)
class MaBandRule:
    enabled: bool = True
    period: int = 20          # 中轨 SMA 周期
    std_mult: float = 2.0     # 上下轨 = 中轨 ± std_mult × σ
    tolerance: float = 0.15   # 「附近」= %B 距锚点的容差（∈(0, 0.25]）
    directional: bool = False # True: 做多只许 lower/mid，做空只许 upper/mid（P2-1，默认关）

def resolve_ma_band_rule(raw: dict | None, settings: Settings) -> MaBandRule | None
def build_ma_band(df: pd.DataFrame, rule: MaBandRule) -> dict | None
def classify_zone(pct_b: float | None, tolerance: float) -> str | None      # lower|mid|upper|None
def apply_ma_band_gate(decision: TradeDecision, band: dict | None,
                       rule: MaBandRule | None) -> tuple[TradeDecision, str | None]
def format_band_for_prompt(band: dict | None) -> str          # 决策 prompt 的只读上下文段
def format_charter(rule: MaBandRule | None) -> str            # 系统 prompt 的铁律段（默认 "" 垫片）
```

### 2.2 通道定义与「附近」的量化（P1-8 修复）

**通道** = 执行周期收盘价上的布林通道：`upper, mid, lower = bollinger(df["c"], rule.period, rule.std_mult)`
（`engine/indicators.py:38`，第二返回值即中轨 SMA）。选它而不是「高价 MA / 低价 MA 包络」的理由有三：
① 需求的「上下 MA 均线边界 + MA 中线」正是这个形状（一条中枢均线 + 两条自适应边界）；
② 仓库已有实现且已被 `market_digest` 用于喂 LLM（`market_digest.py:33,215`），复用即口径统一、零新依赖；
③ 带宽随波动自适应，天然满足「同一条规则在 1m 与 1D 上都成立」。

**「附近」的判据**：把价格归一到 %B，`pct_b = (px − lower) / (upper − lower)`。三个锚点固定为
`lower=0.0 / mid=0.5 / upper=1.0`，合规当且仅当

```
min(|pct_b − 0.0|, |pct_b − 0.5|, |pct_b − 1.0|) ≤ rule.tolerance
```

- 该判据**无量纲**，同一 `tolerance` 在任何周期/波动率下语义一致（P1-8）。
- 它天然覆盖「追高杀跌」：`pct_b > 1 + tol` 或 `< −tol`（跑到带外）距离最近锚点必然超容差 ⇒ 直接否决。
- **退化点（必须写进文档与 UI 提示）**：`tolerance = 0.25` 时三个锚区首尾相接，规则退化为「只要价格在
  通道内即可开单」；故入参上界锁死 `0 < tolerance ≤ 0.25`，UI 只给 `0.05/0.10/0.15/0.20/0.25` 五档，
  文案分别为「极严 / 严格 / 标准（默认）/ 宽松 / 仅限带内」。
- `upper − lower ≤ 0`、非有限数、`len(df) < period + 1` ⇒ `build_ma_band` 返回 `None`（走 §2.6）。

`build_ma_band` 产物（wire 形状，camelCase，直接进 payload / 轨迹 / prompt）：

```jsonc
{
  "ruleId": "ma_band_entry", "period": 20, "stdMult": 2.0, "tolerance": 0.15,
  "upper": 68120.5, "mid": 67240.1, "lower": 66359.7,
  "bandWidthPct": 2.62,        // (upper-lower)/mid×100，供人判断通道是否过窄
  "pctB": 0.42, "zone": "mid", // zone=classify_zone(pctB, tolerance)；不在任何锚区 → null
  "directional": false
}
```

> **因果性**：`df` 一律取会话滚动窗口 `self._window.base_df()`（`market_window.py:74`，末行=当前确认根），
> 与 `marketContext`/`marketDigest` 同一份数据，绝不引入未来根。

### 2.3 门禁规则与豁免矩阵（P1-7 修复）

`apply_ma_band_gate` 的判定表（`rule is None` 或 `rule.enabled=False` ⇒ 恒直通 `(decision, None)`）：

| decision.action | 是否受铁律约束 | 说明 |
|---|---|---|
| `open_long` / `open_short` | **是** | 唯一被拦的两种动作 |
| `close` | **否（永不拦）** | 铁律不得妨碍离场保护；镜像共识门禁的同款豁免（`signals.py:154`） |
| `hold` | 否 | 无副作用 |
| `cancel_order` | 否 | 撤单是风险收敛动作（`ai-trade-limit-orders-fix` F-7 同理） |

被否决时返回 `(_hold(reason), reason)`，`reason` 取值（**参数化后缀沿用 `low_consensus(k/n)` 的既有风格**）：

| degrade 码 | 触发条件 |
|---|---|
| `base_rule_ma_band(pctB=0.42)` | 参考价不在任一锚区内 |
| `base_rule_ma_band_direction(zone=upper)` | `directional=True` 且方向不符（做多在上轨 / 做空在下轨） |
| `base_rule_band_unavailable` | `band is None`（数据不足 / 带宽非正 / 计算异常，§2.6） |

**反手（reverse）语义（P1-7）**：`LiveExecutor` 会把反向 `open_*` 展开成 `[close(reverse), open]` 两腿
（`executor.py:156-159`）。门禁作用在**决策级**，被否即**两腿都不发生**、原持仓原样保留——绝不允许
「放行 close 腿、拦下 open 腿」的半反手态。若 Agent 真想离场，它有 `close`（永不被拦）；这一点**必须写进
prompt**（§2.7），否则模型会在被否几轮后陷入「想反手→被拦→继续持有」的困惑。

**不可推翻性**：门禁在 LLM 之外执行，`reason` 由服务端生成，Agent 无任何入参可影响判定；反思阶段
（`decider.reflect`）**只会更保守**（`reflection.py:16-55`，只允许收缩 `sizeFraction`），且被否决的轮次
根本不会进入反思（§2.5）。三重保证「优先级最高」。

### 2.4 判定价：`entry_reference_px`（P0-3 修复 · 必读）

```python
def entry_reference_px(decision: TradeDecision, last_px: float | None) -> float | None:
    """铁律的判定价：限价委托判**委托价**，市价判最新价。单点收敛，禁止在别处各判一次。"""
    if decision.ord_type == "limit" and decision.limit_px:
        return float(decision.limit_px)
    return last_px
```

理由（这是本方案最容易被漏掉、且漏掉即**规则整体失效**的一点）：限价总闸默认开启
（`config.py:163`），Agent 可以在最新价 ±2%（`config.py:166`）内任选委托价并通过既有校验
（`resting.py:135`）。若门禁只看 `lastPx`，一个「lastPx 恰在中轨、委托价挂到带外」的限价单会被完整放行，
而**真实成交发生在委托价**——铁律在最需要它的场景（挂单等待）下形同虚设。

`last_px` 的来源与限价门禁保持一致：取自 payload 的 `lastPx`（`session.py:786`），与喂给 prompt 的价同源。

### 2.5 管线落点与顺序（P1-1 / P1-2 修复）

```
feed_candle / interval wake
   └─ _run_decision_round
        1. payload = _build_decision_payload(...)          # ← 新增 payload["baseRules"]（§2.2）
        2. consensus = compute_consensus(board)
        3. _begin_round_trace(...)
        4. decision, degrade = decider.decide(payload)     # 含既有重试循环（不含本特性）
        5. _update_hold_streak(decision.action, degrade)   # N2：必须仍以第一阶段真实意愿计数
        6. decision, degrade = _apply_base_rule_gate(...)  # ★ 新增（铁律最先，reason 优先级最高）
        7. decision, degrade = _apply_consensus_gate(...)
        8. decision, degrade, note = _reflect_before_execute(...)   # 被否轮此时已是 hold → 直通、零 LLM
        9. executed = _execute_decision(...)
```

- **为何不在 `_map_decision` 内**（P1-1）：`decide` 的重试只认 `_map_decision` 的返回值
  （`decider.py:220-229`）。价格与通道在一轮内是常量，重试必然同样被否 —— 每次重试白烧一次 LLM 调用
  与最多 60s 超时预算。故门禁只能在 `decide` **之后**，且两个新码**严禁**进入
  `_RETRYABLE_DEGRADE_REASONS`（`decider.py:73`）；§6.1 有专项冻结断言。
- **为何在共识门禁之前**：铁律优先级最高，两者同时触发时应报告铁律的原因（`degrade or reason` 的合并
  语义使**先执行者胜出**，`session.py:678`）。
- **为何在反思之前**（P1-2）：反思是一次独立 LLM 调用（超时预算 45s，`config.py:136`）。被铁律否决的轮次
  已变成 `hold`，而 `_reflect_before_execute` 对 `hold` 本就直通（`session.py:556-558`）⇒ **自动零成本**，
  且杜绝「让反思去确认一个非法决策」的语义错乱。
- **为何在 `_update_hold_streak` 之后**：N2 明确要求惯性计数反映**模型自己的意愿**，不能把门禁造成的
  hold 计进去（否则 hold-streak 提示会误导模型「你已连续观望 N 轮」）。

### 2.6 数据不可用 → fail-closed + 强可观测（P0-4 修复）

`band is None` 时（`len(df) < period+1`、带宽 ≤0、NaN/Inf、计算异常）：

1. **开仓一律否决**（`base_rule_band_unavailable`）——「无法验证合规」不等于「合规」。姿态与反思
   fail-closed（`decider.py:121`）一致：校验不可用时绝不下单。
2. 平仓/撤单/观望**不受影响**（§2.3 豁免矩阵）——数据异常时仍能离场，这是 fail-closed 可被接受的前提。
3. 每轮照发 `base_rule` 轨迹段（`status="unavailable"`），前端渲染为醒目徽标 **「基础策略 · 数据不足 ·
   观望」**（红），并在 `reason` 位给出人话解释（「通道需要至少 21 根 K 线，当前 12 根」）。
4. **连续不可用升级**：同一会话连续 `_BAND_UNAVAILABLE_ALERT_ROUNDS = 3` 轮不可用 ⇒ 追加一条
   `{"type":"error","data":{"code":"AI_TRADE_BASE_RULE_UNAVAILABLE"}}` WS 事件（沿用
   `session.py:523` 的既有信封），使「为什么一直不开单」在 UI 上**自解释**。这是本条 P0 的核心诉求——
   `ai-trade-reflection-hold-inertia` 的教训是：静默的保守 = 用户眼中的故障。
5. **实践上极少触发**：live 会话默认 `priming=True, primingBars=300`（`schemas.py:501-502`），首轮即有
   足量根；`marketDigest` 本就要求 ≥35 根（`market_digest.py:27`）。真实触发面是 `priming=false` 的
   自定义启动与极端稀疏数据。

### 2.7 Prompt 双层注入（P1-3 修复：零回归垫片范式）

**(a) 系统 prompt · 铁律章**：`build_ai_trade_system_prompt(..., base_rules_text: str = "")`，新形参
**末位、默认空串 ⇒ 渲染逐字节不变**（既有冻结断言与直连调用零回归）。非空时插在「首要原则」之后、
「决策纪律」之前——位置本身就是优先级声明：

> **最高优先级铁律（不可违反 · 由系统强制执行）**：本会话启用了基础策略「MA 通道开单约束」。
> **开仓（open_long / open_short）只允许在执行周期 MA 通道的上轨、中轨或下轨附近发生**（容差以带宽
> 归一，见每轮输入的 `基础策略` 段）。不在锚区时提交的开仓会被系统直接降级为观望——这不是建议，是硬约束。
> 请在研判中主动检查当前位置：不满足就选择 `hold` 等待价格回到锚区，或改用 `limit` 把委托价挂在锚区内。
> **平仓 `close`、撤单 `cancel_order` 不受本铁律限制**——需要离场时永远可以直接 `close`。

**(b) 决策 prompt · 每轮只读上下文**：`build_decision_prompt(payload)` 在 `payload["baseRules"]` **存在时**
追加一段（键缺失 ⇒ 整段省略 ⇒ 既有冻结断言与回测 payload 逐字不变）：

```
【基础策略 · MA 通道开单约束（铁律）】
  通道(MA20±2.0σ)：上轨 68120.50 / 中轨 67240.10 / 下轨 66359.70（带宽 2.62%）
  当前价位置：%B=0.42 → 中轨锚区内（容差 0.15）✔ 允许开仓
  合规开单价区间：66359.70~66624.10（下轨）/ 67108.00~67372.20（中轨）/ 67856.10~68120.50（上轨）
```

给出**可直接使用的价区间**而不只是「是/否」，是让 Agent 能主动挂出合规限价单的关键——否则它只能靠
猜价然后被否，白白浪费轮次。区间由同一个 `band` 反解，仍是单一真源。

**(c) 决策工具 schema 零改动**：不新增字段、不改动作枚举，`build_decision_tool_schema(0.0) is
DECISION_TOOL_SCHEMA` 恒等断言（`test_ai_trade_position_step.py:132`）原样通过。

### 2.8 回测同源（P1-4 修复）

`backtest.py` 两处改动，与实盘调用**同一个纯核心**：

- `_build_payload`（`backtest.py:401`）：追加 `"baseRules"`，`df` 取回测帧到当前 `i` 的因果切片
  （`df.iloc[:i+1]`，与该文件既有取数姿态一致）。
- `_run_decision`（`backtest.py:338`）：在共识门禁（`:349`）**之前**插入同款门禁调用。

规则来源：`AiBacktestStartIn`（`schemas.py:554`）同样接受 `baseRules` 字段，缺省时由 API 层按 settings
装配为开启态（与实盘同一 `_resolve_base_rules` 函数，§2.10）——**实盘与回测的默认必须一致**，否则用户
用回测调出的参数在实盘直接失真。

### 2.9 可观测性（P1-9 修复）

| 通道 | 内容 |
|---|---|
| WS `agent_trace` 新相位 `base_rule` | `{status: "pass"\|"veto"\|"unavailable", zone, pctB, upper, mid, lower, reason}`；**仅在规则装配时发**（未装配 ⇒ 一条不发，老 UI 与既有断言零影响；未知相位在前端本就是 no-op，`store.ts:199`） |
| 轮次轨迹 `AiTradeRoundTrace` | 新增 `baseRuleStatus / baseRuleZone / baseRulePctB / baseRuleReason` 四个可选字段（`emptyRoundTrace` 初始化为 `null`） |
| `decision` 事件 | 复用既有 `degradeReason`（无新键）——`_emit_decision` 已透传（`session.py:507`） |
| 统计 | 复用 `_compute_ai_stats.degradedRounds`（`session.py:1062`），**不加新键**（P2-3） |
| 前端中文化 | 新增 `describeBaseRule(trace)` → `{label, tone}`：`基础策略 · 中轨锚区 · 放行`(muted) / `基础策略否决 · 观望`(short) / `基础策略 · 数据不足 · 观望`(short)；新增 `describeDegradeReason(reason)` 前缀映射（未命中原样回退，P1-9） |
| 状态徽标 | `describeTraceState`（`aiTrade.ts:332`）的「降级观望」分支保持不变（`degradeReason` 非空即触发），**不改优先级链**——把铁律信息放在卡片内的独立一行而非抢占轮头 chip，避免与既有 6 级优先级打架 |

### 2.10 默认开启 / 手动关闭 / 总闸（P1-5 修复）

三层语义，缺一不可：

1. **运维总闸** `ai_trade_base_rules_enabled`（`config.py`，默认 `True`）：`False` ⇒ 规则一律不装配，
   prompt 不注入、门禁不调用、轨迹不发 ⇒ **逐字回退到本特性合入前**。
2. **请求语义**（默认开启）：`baseRules` 字段**缺省 ⇒ 开启**。默认值在 **API 层**解析：
   ```python
   # api/ai_auto.py
   def _resolve_base_rules(raw: AiTradeBaseRulesIn | None, s: Settings) -> dict | None:
       """字段缺省 ⇒ 按 settings 装配默认开启态；总闸关 ⇒ None（不装配）。"""
   ```
   **不在 dataclass 层给开启默认值**——`AiTradeGroupStartRequest` 的字段默认仍是 `None`，
   既有直接构造它的单测（`test_ai_trade_group.py` / `test_ai_trade_session.py`）行为逐字不变（P1-5）。
   老客户端（桌面旧包）不发该字段 ⇒ 走「缺省=开启」，符合需求。
3. **用户手动关闭**：启动弹窗「基础策略（铁律）」区块的复选框取消勾选 ⇒ 发
   `{"maBandEntry": {"enabled": false}}` ⇒ 该会话不装配。

---

## 三、File / module change plan（文件 / 模块变更计划）

### 后端（`services/quant`）

| 文件 | 改动 |
|---|---|
| **`app/ai_auto/base_rules.py`**（新建，≤200 行） | `MaBandRule` / `resolve_ma_band_rule` / `build_ma_band` / `classify_zone` / `entry_reference_px` / `apply_ma_band_gate` / `format_band_for_prompt` / `format_charter`；纯函数、无 IO |
| `app/ai_auto/session.py` | ① 构造函数收 `base_rule: MaBandRule \| None = None`（默认 None ⇒ 零回归）；② `_build_decision_payload` 追加 `"baseRules"`；③ 新增 `_apply_base_rule_gate` 薄封装（镜像 `_apply_consensus_gate`，`:669`）并在 `_run_decision_round` 第 6 步调用；④ 新增 `_emit_base_rule_trace`；⑤ 连续不可用计数 + `AI_TRADE_BASE_RULE_UNAVAILABLE` 事件 |
| `app/ai_auto/decider.py` | **仅注释**：在 `_RETRYABLE_DEGRADE_REASONS`（`:73`）上方注明「`base_rule_*` 码为确定性终态，严禁加入本集合」。**无逻辑改动** |
| `app/ai_auto/prompts.py` | `build_ai_trade_system_prompt` 末位加 `base_rules_text: str = ""`；`build_decision_prompt` 在 `payload["baseRules"]` 存在时追加只读段（新 `_format_base_rules`） |
| `app/ai_auto/trace.py` | `new_round_trace` 增 4 个 `baseRule*` 初值键；新增 `base_rule_event(round_no, ts, band, status, reason)` builder |
| `app/ai_auto/manager.py` | `_build_member` 把 `req.base_rule` 传给 `AiTradeSession` 与 `build_ai_trade_system_prompt(base_rules_text=format_charter(...))`；`AiTradeGroupStartRequest` 增 `base_rule: MaBandRule \| None = None`；`_create_group_run` 的 `config` 追加 `"baseRules"`（零列迁移，A-12） |
| `app/ai_auto/backtest.py` | `_build_payload` 追加 `baseRules`；`_run_decision` 在共识门禁前插入门禁（§2.8） |
| `app/ai_auto/backtest_manager.py` | `AiBacktestStartRequest` 增 `base_rule` 并透传 |
| `app/api/ai_auto.py` | `_resolve_base_rules(raw, settings)`（默认开启单点）；start / backtest-start 两条路由各调一次 |
| `app/schemas.py` | 新增 `AiTradeMaBandRuleIn` / `AiTradeBaseRulesIn`；`AiTradeGroupStartIn`（`:478`）与 `AiBacktestStartIn`（`:554`）各加 `baseRules: AiTradeBaseRulesIn \| None = None`；`AiTradeGroupRow` 加回执镜像 `baseRules: dict \| None = None` |
| `app/config.py` | 5 个新配置项（§5.3） |
| `.env.example` | 5 行注释 + 默认值（沿用既有 `AI_TRADE_*` 段落风格） |

> **不改**：`engine/indicators.py`（`bollinger` 直接复用）、`signals.py`、`reflection.py`、
> `live_auto/*`、`strategies/*`、`paper/strategy_factory.py`、任何 DB 模型。

### 前端（`apps/web`）

| 文件 | 改动 |
|---|---|
| `lib/aiTradeTypes.ts` | `AiTradeMaBandRule` / `AiTradeBaseRules` 类型；`AiTradeGroupStartReq`（`:366`）加 `baseRules?`；`AiTradeTracePhase`（`:177`）加 `"base_rule"`；`AiTradeRoundTrace`（`:226`）加 4 个可选字段；`AiTradeConfig`（`:452`）加 `baseRules?`（**可选 + 读点兜底**，P1-6） |
| `lib/aiTrade.ts` | 常量 `AI_TRADE_BASE_RULE_DEFAULTS`、`AI_TRADE_BAND_TOLERANCE_OPTIONS`（5 档）；`describeBaseRule(trace)`；`describeDegradeReason(reason)`（前缀映射 + 原样回退）；`describeExecuted`（`:360`）改为经 `describeDegradeReason` 渲染 |
| `lib/store.ts` | `emptyRoundTrace`（`:119`）初始化 4 个新键；`reduceRoundPhase`（`:152`）新增 `case "base_rule"`。**不动 persist version 29**（P1-6） |
| `components/Agent/AiTradeLaunchModal.tsx` | 新区块「基础策略（铁律 · 默认开启）」置于「交易策略」清单**之上**；勾选框 + 规则说明 + `<details>` 高级参数（周期 / 带宽倍数 / 容差 5 档 / 方向性）；state 新增并在 `[open]` seed effect（`:183-209`）中回填（**该 effect 的依赖数组必须保持 `[open]`**——见 `docs/diagnoses/ai-trade-launch-modal-inputs-revert*`）；`AiTradeLaunchOpts`（`:57`）加字段 |
| `components/Agent/AiTradeToggle.tsx` | `startM` body 映射（`:85-99`）加 `baseRules`；`setAiTradeConfig`（`:101-113`）持久化；backtest 分支（`:118-148`）同步 |
| `components/Agent/AiTradeTraceRound.tsx` | 新增 `BaseRuleBlock`，插在 `DecisionToolCard` 与 `ReflectionBlock` 之间（`:358-359`），样式复用 `ReflectionBlock`（`:181-232`）的 accent 面板 + tone chip |

> **不改**：`strategyCatalog.ts`（不新增第四个 tab——基础策略不是策略库条目，A-3/P0-2）、
> `AiTradeAgentTrace.tsx`、`AgentPanel.tsx`、`useAiTradeGroupStream.ts`（`agent_trace` 已是通用路由）。
> `desktop/` **零改动**（P2-6）。

### 测试

| 文件 | 覆盖 |
|---|---|
| `services/quant/tests/test_ai_trade_base_rules.py`（新建） | §6.1 全部用例 |
| `services/quant/tests/test_ai_trade_session.py` | 追加：门禁顺序、被否轮不进反思、轨迹相位、连续不可用告警 |
| `services/quant/tests/test_ai_trade_decider.py` | 追加：冻结断言「两个新码不在 `_RETRYABLE_DEGRADE_REASONS`」 |
| `services/quant/tests/test_ai_trade_prompts.py` | 追加：`base_rules_text=""` 逐字节不变；payload 无 `baseRules` 键时决策 prompt 不变 |
| `services/quant/tests/test_ai_trade_api.py` | 追加：字段缺省 ⇒ 开启；显式 `enabled:false` ⇒ 关闭；总闸关 ⇒ 恒不装配；越界入参 422 |
| `services/quant/tests/test_ai_backtest_runner.py` | 追加：回测与实盘同判 |
| `apps/web/lib/aiTrade.test.ts` | `describeBaseRule` / `describeDegradeReason`（含未命中原样回退） |
| `apps/web/lib/aiTradeTrace.test.ts` | `base_rule` 相位归约 |
| `apps/web/components/Agent/AiTradeLaunchModal.test.tsx` | 默认勾选、取消勾选后提交体、高级参数回填 |
| `apps/web/components/Agent/AiTradeToggle.test.tsx` | start body 含 `baseRules` |

### 文档

- 本文件（`docs/plans/ai-trade-base-strategy-ma-band/spec.md`）。

---

## 四、Interface design（接口设计）

### 4.1 启动请求新增字段（`POST /api/py/ai-trade/start`）

```jsonc
{
  "members": [...], "bar": "1H", "strategyIds": [1, 2],
  "baseRules": {                       // 省略 ⇒ 默认开启（§2.10）
    "maBandEntry": {
      "enabled": true,
      "period": 20,                    // ge=2, le=200
      "stdMult": 2.0,                  // gt=0, le=5
      "tolerance": 0.15,               // gt=0, le=0.25（0.25=仅限带内，§2.2）
      "directional": false
    }
  }
}
```

`AiBacktestStartIn`（`schemas.py:554`）同字段同语义。越界入参在入口即 422（与既有 `leverage` 同惯例）。

### 4.2 决策 payload 新增只读键

`payload["baseRules"] = {"maBand": <§2.2 wire 形状> | null}`；规则未装配 ⇒ **整键缺席**（不是 `null`），
使 prompt 渲染与回测 payload 逐字节回退（冻结门禁友好）。

### 4.3 WS `agent_trace` 新相位

```jsonc
{"type": "agent_trace", "data": {
  "phase": "base_rule", "round": 7, "ts": 1752900000,
  "status": "veto", "zone": null, "pctB": 0.42,
  "upper": 68120.5, "mid": 67240.1, "lower": 66359.7,
  "reason": "base_rule_ma_band(pctB=0.42)"}}
```

### 4.4 无新增 REST 端点、无新增 LLM 工具、无新增动作枚举

---

## 五、Data model（数据模型）

### 5.1 零数据库迁移

`baseRules` 随 `strategy_runs.config_json` 落库（`manager.py:585-597`，`positionStepPct` 先例，A-12）。
**不新增列、不动 `db/session.py` 的 ALTER patcher**（`_ensure_live_auto_group_columns`，`session.py:146`）。

### 5.2 无内存新结构

`MaBandRule` 是 `frozen=True` 的会话级不可变配置，随 `AiTradeSession` 生命周期；`band` 每轮现算现用，
不缓存（一根 K 线一次 `bollinger`，成本可忽略，且与 `marketDigest` 同数量级）。

### 5.3 配置项（`app/config.py`，字段名即 env 名）

| 配置 | 默认 | 说明 |
|---|---|---|
| `ai_trade_base_rules_enabled` | `True` | 基础策略总闸；`False` 逐字回退（§2.10） |
| `ai_trade_base_rule_ma_period` | `20` | 中轨 SMA 周期（`ge=2`） |
| `ai_trade_base_rule_ma_std_mult` | `2.0` | 上下轨标准差倍数（`gt=0`） |
| `ai_trade_base_rule_band_tolerance` | `0.15` | %B 锚区容差（`gt=0, le=0.25`） |
| `ai_trade_base_rule_directional` | `False` | 方向性模式默认值（P2-1） |

桌面透传无需改动：`desktop/src/lib/config.js:72` 已白名单 `AI_TRADE_` 前缀（P2-6）。

---

## 六、Testing & acceptance criteria（测试与验收）

### 6.1 后端单测（不依赖网络 / LLM）

**纯核心（`test_ai_trade_base_rules.py`）**

1. `builds_band_from_execution_frame` — 构造 60 根合成 K 线，断言 `upper/mid/lower` 与直接调
   `indicators.bollinger` 逐值相等（同真源）。
2. `classifies_three_anchor_zones` — %B ∈ {0.0, 0.5, 1.0} → `lower/mid/upper`；{0.30, 0.72} → `None`。
3. `tolerance_edges_are_inclusive` — `|%B−anchor| == tolerance` 判为合规（边界含）。
4. `tolerance_quarter_degenerates_to_inside_band` — `tolerance=0.25` 时带内任意 %B 合规、带外仍否决。
5. `rejects_entry_outside_band` — %B=1.4 / −0.3 → `base_rule_ma_band(...)`。
6. **`limit_order_is_judged_by_limit_price`（P0-3 冻结）** — `lastPx` 在中轨、`ord_type="limit"` 且
   `limit_px` 在带外 ⇒ **否决**；反之 `lastPx` 带外但 `limit_px` 在下轨锚区 ⇒ **放行**。
7. `never_blocks_close_hold_cancel` — 三个动作在最恶劣 %B 下全部原样直通（豁免矩阵冻结）。
8. `unavailable_band_fails_closed` — `band=None` ⇒ 开仓否决且码为 `base_rule_band_unavailable`；
   `close` 仍直通。
9. `disabled_rule_is_identity` — `rule=None` / `enabled=False` ⇒ 返回**同一对象**（`is` 断言）且 reason 为 None。
10. `directional_mode_blocks_chasing` — `directional=True`：上轨锚区的 `open_long` 否决、`open_short` 放行；
    默认 `False` 时两者皆放行（默认行为冻结）。
11. `insufficient_bars_returns_none` — `len(df) == period` ⇒ `build_ma_band` 返回 `None`（不抛）。
12. `zero_width_band_returns_none` — 常数序列（σ=0）⇒ `None`（不产生除零/Inf）。

**管线（`test_ai_trade_session.py` 追加）**

13. `base_rule_gate_runs_before_consensus_and_reflection` — 桩 decider 返回带外 `open_long` + 桩反思
    计数器；断言最终 `hold`、`degradeReason` 为 `base_rule_ma_band(...)`、**反思调用次数 == 0**（P1-2）。
14. `hold_streak_counts_model_intent_not_gate` — 被门禁否决的轮次不增加 `hold_streak`（N2 语义）。
15. `emits_base_rule_trace_each_round` — 装配时每轮一条 `phase="base_rule"`；未装配时**零条**。
16. `three_consecutive_unavailable_rounds_emit_error_event` — 第 3 轮出现
    `AI_TRADE_BASE_RULE_UNAVAILABLE`（P0-4）。

**冻结门禁（回归防线）**

17. `test_ai_trade_decider.py::base_rule_reasons_are_not_retryable` —
    `{"base_rule_ma_band", "base_rule_ma_band_direction", "base_rule_band_unavailable"} &
    _RETRYABLE_DEGRADE_REASONS == set()`，且 `_is_retryable_degrade("base_rule_ma_band(pctB=0.4)")` 为 False（P1-1）。
18. `test_ai_trade_prompts.py::system_prompt_byte_identical_without_base_rules` —
    `build_ai_trade_system_prompt(...)`（不传新形参）与冻结基线逐字节相等（P1-3）。
19. `test_ai_trade_prompts.py::decision_prompt_unchanged_without_base_rules_key` — payload 无
    `baseRules` 键 ⇒ 渲染逐字节不变。
20. `test_ai_trade_position_step.py:132` 既有恒等断言保持通过（不得改动决策工具 schema）。

**API（`test_ai_trade_api.py` 追加）**

21. `base_rules_default_on_when_field_absent`（P1-5）／22. `explicit_disable_turns_rule_off`／
23. `master_switch_off_never_assembles`／24. `out_of_range_tolerance_returns_422`（`tolerance=0.4`）。

**回测（`test_ai_backtest_runner.py` 追加）**

25. `backtest_applies_same_gate_as_live`（P1-4）——同一 band 与决策下，回测与实盘产出同一 `degradeReason`。

### 6.2 前端测试（`NODE_ENV=test`，见记忆 `aragon-web-vitest-node-env`）

26. `aiTrade.test.ts::describeBaseRule` 三态标签与 tone；
27. `aiTrade.test.ts::describeDegradeReason` 命中两个新码 → 中文；**未命中 → 原样回退**（既有
    `low_consensus(1/2)` 断言不破，P1-9）；
28. `aiTradeTrace.test.ts::base_rule` 相位归约写入 4 个字段，且**未知相位仍 no-op**；
29. `AiTradeLaunchModal.test.tsx`：默认勾选、取消后 `onConfirm` 载荷 `enabled:false`、高级参数回填、
    重开弹窗不丢用户输入（seed effect 依赖数组仍为 `[open]`）；
30. `AiTradeToggle.test.tsx`：`aiTradeApi.start` 载荷含 `baseRules`。

### 6.3 验收标准（Definition of Done）

- [ ] 启动弹窗默认勾选「基础策略 · MA 通道开单约束」，可取消；取消后该会话不装配、轨迹无 `base_rule` 段。
- [ ] 开启时：带外开仓 100% 被降级为 `hold`，轨迹显示「基础策略否决 · 观望」+ 人话理由。
- [ ] 限价委托按**委托价**判定（P0-3），有专项单测冻结。
- [ ] `close` / `cancel_order` 在任何 %B 下都不被拦。
- [ ] 总闸 `AI_TRADE_BASE_RULES_ENABLED=false` ⇒ prompt、schema、轨迹、行为**逐字回退**到合入前。
- [ ] 回测与实盘同判（P1-4）。
- [ ] 后端 `pytest services/quant/tests -q` 全绿（含既有 5661 行 AI-trade 测试）；
      前端 `NODE_ENV=test pnpm -C apps/web test` 全绿。
- [ ] 无数据库迁移、无新增依赖。

---

## 七、Risks & mitigations（风险与缓解）

| # | 风险 | 缓解 |
|---|---|---|
| R-1 | **开单频率显著下降**（震荡市大量轮次被否）——用户可能感知为「不工作了」 | 每轮 `base_rule` 轨迹段显式显示放行/否决 + %B 位置；prompt 给出**合规价区间**引导 Agent 挂限价单主动进锚区；容差可调（5 档） |
| R-2 | **fail-closed 造成长期不开单**（数据异常时） | 连续 3 轮不可用升级为 `error` 事件（§2.6）；实践面极窄（priming 默认 300 根） |
| R-3 | **与趋势突破类策略冲突**：突破行情下价格常年贴上轨，`directional=True` 会禁掉顺势做多 | `directional` **默认关**（P2-1）；文案标注「保守模式，趋势策略慎用」 |
| R-4 | 参数被调到 `tolerance=0.25` 后规则形同虚设 | UI 该档文案直书「仅限带内（最宽松）」；入参上界锁死 0.25，不接受更大值 |
| R-5 | 新 degrade 码若被误加进重试白名单 ⇒ 每轮白烧 3 次 LLM 调用 | §6.1 用例 17 冻结断言 + `decider.py:73` 上方显式注释 |
| R-6 | prompt 变更打破既有逐字冻结断言 | 默认空垫片范式（P1-3）+ 用例 18/19 双向冻结 |
| R-7 | 弹窗新字段引发「输入约 1 秒回退」复发缺陷（见记忆 `aragon-ai-trade-modal-revert-recurrence`） | seed effect 依赖数组**必须**保持 `[open]`；新增字段只在该 effect 内回填；用例 29 守门 |
| R-8 | 通道过窄（横盘）时三锚区几乎重叠，规则近似「随便开」 | `bandWidthPct` 随 band 下发，prompt 与 UI 均显示；后续可加最小带宽门槛（本期不做，避免过度设计） |

---

## 八、实施注意（供 Subtask #2）

1. **先写 `base_rules.py` 与它的 12 个纯函数单测，再接管线**（TDD；纯核心零依赖，可秒级迭代）。
2. `apply_ma_band_gate` 在**未装配**时必须返回**同一个 decision 对象**（`is` 相等），别顺手 `replace` ——
   §6.1 用例 9 会抓。
3. 降级码带参数（`base_rule_ma_band(pctB=0.42)`），任何按**集合成员**判定的既有逻辑
   （`_is_retryable_degrade`、`_REFLECTION_FAIL_CLOSED_REASONS`）对它天然不命中，这是**有意设计**，
   勿为「统一风格」把它改成无参裸码。
4. `session.py` 已 1109 行（CLAUDE.md 阈值 800）——新增逻辑一律下沉 `base_rules.py` / `trace.py`，
   `session.py` 只留 ≤3 个薄封装（镜像 `_apply_consensus_gate` 的 10 行体量）。
5. 前端 `AiTradeConfig.baseRules` 声明为**可选**，所有读点 `?? AI_TRADE_BASE_RULE_DEFAULTS`
   （[R-7] 既有约定）；**不动 persist version 29**。
6. 提交前自查 CLAUDE.md 八条评审清单；工作树内有其他特性的在制改动，**必须行级精确暂存**
   （见记忆 `aragon-ai-trade-limit-orders` 的 Subtask#3 教训），严禁 `git add -A`。

---

## 评审结论（Review Verdict · Subtask #1）

### 裁决：**有条件通过**

本方案（v2）在可行性、完整性、一致性、体量四个维度均达到可开工标准，但由于**上游设计节点未产出任何
文档**（P0-1），本 v2 是评审节点代偿重建的产物——它没有经历「设计者提出 → 评审者证伪」的完整对抗，
因此放行条件比常规更严：下述 7 条为 Subtask #2 的**硬性验收前提**，缺一即视为未完成。

### 四个维度评估

- **可行性（Feasibility）**：高。零新依赖（`bollinger` 已在 `engine/indicators.py:38` 且已被
  `market_digest` 使用）、零数据库迁移、零新 REST 端点、零 LLM 工具契约变更。唯一新建文件是一个纯函数
  模块，全部改动都落在既有扩展点上（共识门禁范式、`allow_limit` 垫片范式、`config_json` 零迁移范式）。
- **完整性（Completeness）**：达标。边界与错误路径已逐条定义——限价判定价（P0-3）、数据不可用
  fail-closed 与告警升级（P0-4）、反手两腿语义（P1-7）、豁免矩阵、退化容差、回测同源、默认开启的
  兼容语义、老客户端与老 UI 的向后兼容。
- **一致性（Consistency）**：达标。遵循 CLAUDE.md 的文件/函数阈值（新增逻辑下沉、`session.py` 只加薄
  封装）、错误显式传播（不吞异常、不假数据）、修 bug 先写测试；命名与既有 `ai_trade_*` / `describe*` /
  `apply_*_gate` 体系一致；不与 A-1..A-15 的任何 as-built 事实冲突。
- **体量（Right-sizing）**：适中。后端 1 个新文件 + 10 处改动、前端 6 处改动、10 个测试文件。刻意**未**做的
  三件事——不建策略库第四个 `kind`、不做多规则编排 UI、不做限价重议价——每一件都在 §1.3 / P2 中给出了
  拒绝理由，不是遗漏。

### 放行条件（Subtask #2 必须逐条落实，缺一不可）

1. **P0-3 冻结**：§6.1 用例 6（限价按委托价判定）必须存在且通过——这是整条规则不被绕过的唯一保证。
2. **P1-1 冻结**：§6.1 用例 17 必须存在（三个新码不在 `_RETRYABLE_DEGRADE_REASONS`）。
3. **P1-2 顺序**：§6.1 用例 13 必须断言「被否轮的反思调用次数 == 0」，不得只断言最终动作。
4. **P1-3 零回归**：§6.1 用例 18/19/20 三条冻结断言全绿；决策工具 schema **一个字符都不许改**。
5. **P0-4 可观测**：`base_rule` 轨迹段 + 连续不可用告警（用例 15/16）必须落地——静默的保守会被用户
   当成故障，这是本仓库有前科的失败模式（`ai-trade-reflection-hold-inertia`）。
6. **P1-4 同源**：回测与实盘共用同一个 `apply_ma_band_gate`，用例 25 守门；严禁在 `backtest.py` 里
   复制一份判定逻辑。
7. **P1-5 默认语义**：默认开启只在 API 层解析（用例 21~23），后端 dataclass 默认保持 `None`。

### 一句话总结

把「基础策略」实现为**信号板之外的铁律层**（宪章 prompt + 确定性门禁 + 按委托价判定 + fail-closed 且可
观测），而不是策略库里的第四个 `kind`——这是让「优先级最高、必须强制遵守」这句需求在本代码库里**真正
成立**的唯一形态；其余全部是围绕它的零回归工程。

---

## 实施过程发现的方案缺陷（Issues Found During Implementation · Subtask #2 / #3）

> 本段留给下游节点填写：实现中发现的 spec 偏差、as-built 勘误、被迫的取舍与其理由。

### Subtask #2（代码开发）——已落地的偏差与其理由

7 条放行条件**全部落实**（用例编号见括号）：P0-3 限价按委托价（用例 6）、P1-1 三码不可重试（17）、
P1-2 被否轮反思调用数 == 0（13）、P1-3 prompt/schema 零回归三连（18/19/20）、P0-4 轨迹段 + 连续不可用
告警（15/16）、P1-4 回测共用同一纯核心（25）、P1-5 默认只在 API 层解析（21~23）。

| # | 偏差 | 理由 |
|---|---|---|
| I-1 | `apply_ma_band_gate(decision, band, rule, *, last_px=None)` 比 §2.1 的三形参签名**多一个 keyword-only 形参** | §2.4 要求判定价为 `limit_px if ord_type=="limit" else lastPx`，而 `band` 只承载通道三线与**最新价**的 %B。不把 `last_px` 传进门禁，P0-3 根本无法实现。新增 `band_pct_b(band, px)` 纯函数把任意价归一到 %B，判定价与展示价共用它 |
| I-2 | `_resolve_base_rules` 返回 `MaBandRule \| None`（§2.10 写的是 `dict \| None`） | 下游三处（`AiTradeGroupStartRequest.base_rule` / `AiBacktestStartRequest.base_rule` / `AiTradeSession.base_rule`）声明的都是 `MaBandRule \| None`；返回 dict 只会逼每个消费点各自再解析一次，与「单点解析」的初衷相反 |
| I-3 | `base_rules.py` 比 §2.1 的函数清单多 4 个纯函数：`band_pct_b` / `gate_status` / `compliant_price_ranges` / `rule_wire` | 全部是「唯一真源」的直接推论：`gate_status` 让轨迹三态由门禁结果派生而非调用方各判一次；`compliant_price_ranges` 由同一个 band 反解，用例 `compliant_ranges_match_gate_verdict` 逐点断言「宣称的合规区间必被门禁放行」；`rule_wire` 让回执 / `config_json` / 前端偏好共用一份 wire 形状 |
| I-4 | 回测的**系统 prompt 也注入** `format_charter`（§2.8 只列了 `_build_payload` 与 `_run_decision` 两处） | §1.2 目标 3 要求「实盘与回测同源生效」。只同步硬门禁而不同步软引导，会让回测里的 Agent 反复撞墙、决策分布与实盘系统性不同，用户据此调参仍会失真。`base_rule` 默认 `None` ⇒ `format_charter(None)==""` ⇒ 既有回测 prompt 逐字节不变（用例 `backtest_without_base_rule_is_unchanged` 守门） |
| I-5 | §2.7 (b) 示例里的「合规开单价区间」数值**与 §2.2 判据不自洽**（示例中轨区间半宽约 0.075×带宽，而判据是 `tolerance=0.15`）。实现按 **§2.2 判据**渲染：锚点 ± `tolerance`×带宽，且**不裁剪到带内** | 判据是规范、示例是插图。`|%B−0| ≤ tol` 本就允许 %B ∈ [−tol, 0)（贴着下轨稍下方仍属「下轨附近」，§2.2 明写只有 %B > 1+tol / < −tol 才必然超容差）。若按示例裁剪，prompt 宣称的区间会**窄于**门禁实际放行的区间，Agent 会错过合法挂单点 |
| I-6 | `AiTradeMaBandRuleIn` 的四个数值字段声明为 `X \| None = None` 而非硬编码默认值 | §5.3 规定默认值的唯一真源在 `config.py`。若在 schema 里写死 20/2.0/0.15，运维改 env 后「弹窗不填=20」与「settings=30」会分叉。边界仍在入口校验（`tolerance=0.4` → 422，用例 24） |
| I-7 | 新增两处 §三未列的落点：`AiTradeGroup` dataclass 加 `base_rule` 字段、`group_status()` 回显 `baseRules` | §三「schemas.py」一栏要求 `AiTradeGroupRow` 加 `baseRules` 回执镜像，但没写它由谁填。`group_status()` 是该 wire 的唯一装配点，分组必须持有规则才能回显 |
| I-8 | `_apply_base_rule_gate` 是 **async**（§三 写「镜像 `_apply_consensus_gate` 的 10 行体量」，后者是同步） | 门禁结果与轨迹段是同一件事的两面（§2.9 要求每轮发一条），拆成「同步门禁 + 调用方另调一次 emit」会给未来漏发轨迹留口子。体量仍是 10 行 + 一个 18 行的 emit 助手，`session.py` 未新增任何判定逻辑 |
| I-9 | 前端多改一个 §三未列的文件：`lib/aiBacktestTypes.ts`（`AiBacktestStartReq` 加 `baseRules?`） | §2.8 要求回测同字段同语义，但 §三 前端表只列了 `aiTradeTypes.ts`。不加此字段，`AiTradeToggle` 的回测分支透传 `baseRules` 会 tsc 编译失败 |
| I-10 | fail-closed 多覆盖一种情形：`last_px` 缺失（取价失败）也判 `base_rule_band_unavailable` | 与 §2.6 同理——「无法验证合规」不等于「合规」。用例 `missing_last_px_fails_closed` 守门 |
| I-11 | `AiTradeLaunchModal.test.tsx` 增加了必填 prop `defaultBaseRule`（既有测试文件的改动） | `defaultBaseRule` 是非可选 prop（弹窗必须知道回填值）。既有测试的 `renderModal` 夹具不传即 tsc 报错，属契约扩展的必要连带改动，非行为改动 |

### Subtask #3（终审与提交）——评审发现并就地修复的问题

逐条比对 §三 变更计划与 diff：**覆盖度**全中（后端 12 文件 + 前端 11 文件 + spec，7 条放行条件各有专项
用例）；**回归面**逐点复核（prompt 空垫片、`payload["baseRules"]` 整键缺席、dataclass 默认 `None`、
`agent_trace` 未知相位 no-op、`AiTradeConfig.baseRules` 可选 + 读点兜底、seed effect 依赖仍为 `[open]`）。

| # | 严重度 | 位置 | 问题与修复 |
|---|---|---|---|
| RV-1 | P1 | `AiTradeTraceRound.tsx` `BaseRuleBlock` | **本段自称「人话理由」却渲染裸降级码**：`{trace.baseRuleReason}` 原样输出 `base_rule_ma_band(pctB=0.42)`，与 P1-9 中文化的初衷正相反——而基础策略恰是**高频**触发项，震荡市里这行会每轮怼到用户脸上。**已修复**：经同一个 `describeDegradeReason` 渲染（与 `describeExecuted` 同真源，未命中仍原样回退） |

其余无 P0/P1。已知取舍（记录不改）：放行轮若模型选的是 `close`/`hold` 且价格恰在带外，徽标仍显示
「基础策略 · 放行」而正文提示「不在任何锚区附近」——语义正确（本轮无开仓被拦），且 §2.9 只定义了三态
标签，不为此扩标签体系。

### 验证记录

- 后端：`pytest services/quant/tests -q` → **1362 passed, 7 failed**。7 个失败全部在
  `tests/test_factor_library_expansion.py`（**未跟踪**的新文件 + `engine/factors/library.py` 的未提交改动，
  属 `factor-library-crypto-expansion` 特性的在制品），与本特性无关、且在本特性动工前即为红。
  本特性相关的 `-k "ai_trade or ai_backtest"` 子集：**384 passed**（其中新增 46 个用例）。
- 前端：`NODE_ENV=test pnpm -C apps/web test` → **114 files / 1111 tests 全绿**；
  `tsc --noEmit` 仅剩 `components/Factor/WeightBatchArena.test.tsx` 一条**既有**错误（`TrainMethod` 字面量，
  与本特性无关，见记忆 `aragon-factor-combo-insights-review`）。
- 零数据库迁移、零新依赖、决策工具 schema 一个字符未改。
- **工作树含多个特性的在制改动**：`desktop/src/lib/config.js`、`services/quant/app/api/live_auto.py`、
  `services/quant/app/engine/factors/*`、`tests/test_factor_*`、`tests/test_config.py`、
  `app/db/live_auto_repositories.py`、`desktop/scripts/*` 均**非本特性所改**。Subtask #3 提交时必须行级精确
  暂存，严禁 `git add -A`。本特性的完整文件清单（22 个）：
  `services/quant/app/ai_auto/{base_rules.py(新),session.py,decider.py,prompts.py,trace.py,manager.py,backtest.py,backtest_manager.py}`、
  `services/quant/app/api/ai_auto.py`、`services/quant/app/{schemas.py,config.py}`、`.env.example`、
  `services/quant/tests/{test_ai_trade_base_rules.py(新),test_ai_trade_session.py,test_ai_trade_decider.py,test_ai_trade_prompts.py,test_ai_trade_api.py,test_ai_backtest_runner.py}`、
  `apps/web/lib/{aiTradeTypes.ts,aiTrade.ts,store.ts,aiBacktestTypes.ts,aiTrade.test.ts,aiTradeTrace.test.ts}`、
  `apps/web/components/Agent/{AiTradeLaunchModal.tsx,AiTradeToggle.tsx,AiTradeTraceRound.tsx,AiTradeLaunchModal.test.tsx,AiTradeToggle.test.tsx}`、
  `docs/plans/ai-trade-base-strategy-ma-band/spec.md`。
- **Subtask #3 复验**（RV-1 修复后）：AI-trade 子集 `-k "ai_trade or ai_backtest"` → **386 passed**；
  全量 `pytest services/quant/tests -q` → **1362 passed, 7 failed**（同上，全在未跟踪的
  `test_factor_library_expansion.py`）；前端受影响 5 个测试文件 **150 passed**；
  `tsc --noEmit` 仅剩 `WeightBatchArena.test.tsx` 既有错误。
- **行级精确暂存的隔离验证**：`.env.example` 与 `app/config.py` 是**混合文件**（另含他人在制的
  `ai_trade_enabled` 总闸翻转），故按 hunk 切分后 `git apply --cached`；再把 index 打到一个 `HEAD`
  detached worktree 上单独跑，确认**仅暂存内容**即绿（AI-trade 子集 384 passed，
  + `test_auto_trade_admission/test_ai_backtest_api/test_live_auto_api` 17 passed）——排除「靠工作树里
  他人改动才通过」的假绿。`desktop/src/lib/config.js`、`app/api/live_auto.py`、`engine/factors/*`、
  `tests/test_config.py`、`tests/test_factor_*`、`app/db/live_auto_repositories.py` 一律未入本次提交。
