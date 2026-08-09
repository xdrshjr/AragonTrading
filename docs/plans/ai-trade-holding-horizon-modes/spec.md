# AI 自动交易 · 交易周期模式（持仓周期铁律层）

- **feature slug**：`ai-trade-holding-horizon-modes`
- **版本**：**v2**（v1 = 方案设计节点产出；v2 = 设计评审后修订，全部 P0 / P1 已在正文闭合）
- **日期**：2026-08-09（v1）／2026-08-09（v2 评审修订）
- **影响面**：`services/quant/app/ai_auto/*`（实盘 + 回测决策链）、`services/quant/app/api/ai_auto.py`、
  `services/quant/app/config.py`、`services/quant/app/schemas.py`、`apps/web`（启动弹窗 / 面板 / 轨迹 / 统计）
- **DB 迁移**：无（全部落既有 `strategy_runs.config_json` 与 `metrics_json`，沿用 §2.13 的零迁移惯例）

---

## 评审记录（v1 → v2）

评审方式：逐节对照仓库真实代码核实（下表每条结论均已按行号核对过源文件，不是纸面推演）。
分维度结论如下——

- **可行性**：整体成立。`estimate_cost_only`（`cost_runtime.py:180`）、`BAR_SECONDS`（`okx/types.py:93`）、
  `_bar_ts`（`backtest.py:476`）、`apply_ma_band_gate`（`base_rules.py:193`）、`_maybe_time_exit`
  （`live_auto/executor.py:244`）、`_EDGE_UNAVAILABLE_ALERT_ROUNDS=20`（`session.py:98`）、
  `AiExecutionBar` 含 `5m/1H/4H`（`schemas.py:341`）、`build_reflection_system_prompt()` 当前无形参
  （`prompts.py:665`）——方案依赖的每一个既有构件都存在且签名与文中描述一致。§2.5 的决策链顺序
  与 `session.py:647-676` 逐行吻合（`_apply_profit_cooldown_gate` 确为同步、`_apply_base_rule_gate` 确为
  async），插入点可直接落地。
- **完整性**：三处**边界/错误路径缺口**足以致命——G2 的连续确认没有墙钟上界（P0-3）、豁免矩阵自相矛盾
  （P0-2）、`custom` 空转口径破裂（P1-1）。另有 5 条「字段/配置无消费方」的欠规格。
- **一致性**：与 `CLAUDE.md`（800 行 / 50 行 / 形参 ≤5 / 先写复现测试）与仓库既有铁律层范式一致；
  但**踩中了两条仓库成文陷阱**：逐键手抄 reducer 吞新键（P1-4）、跨链路共享配置被静默改写（P0-1）。
- **适度性**：范围合适，不算过度设计。三道闸 + 四条豁免是本问题的最小充分集；
  唯一略超范围的是 `enforceMaxHold`（P1-5，且无实现落点）。

### P0（阻断，必须在实现前闭合）

| 编号 | 位置 | 问题 | 结论 |
|---|---|---|---|
| **P0-1** | §2.11 联动规则 2 | 切换 profile 时把 `profitGuard.minHoldSeconds` 抬到 `min(profile.minHoldSeconds, 3600)`。核实：`ai_trade_profit_guard_min_hold_s` 默认 **30s**（`config.py:279`），而 `should_protect` 在 `holdSeconds < min_hold_s` 时**直接 return None**（`profit_guard.py:748-749`）——即整层保护静默停摆。于是 `intraday_swing` 下会出现「开仓后 1 小时内：G1 禁止 Agent 平仓 **且** 利润保护层不工作」的双关窗口，此时唯一兜底只剩交易所 OCO；而同一屏的契约摘要还写着「利润保护层不受限制」。这是**用真实资金**兑现的假承诺。 | 已修：**删除该联动**，profitGuard 的 `minHoldSeconds` 不再随模式变化；并在契约摘要里点名保护层的最短持有是独立的、与本层无关。见 §2.11 与 §9 D-7 |
| **P0-2** | §2.3.3 表 vs §6.1 H-13 | 豁免语义自相矛盾：§2.3.3 正文写「以下任一成立，**三道闸整体解除**」，而 H-13 断言 `epoch_estimated` 时「G1 跳过、G2/G3 仍生效」。豁免矩阵被本文声明为「冻结」，且 P0 级用例（`test_ai_trade_horizon_exemptions.py`）挂在它上面——两种读法会实现出两套安全阀。 | 已修：§2.3.3 改为**逐闸解除矩阵**（哪条豁免解除哪几道闸写死成表），H-13 同步改写 |
| **P0-3** | §2.3.2 G2 | 连续确认只数**轮次**、不设墙钟上界。`trend_hold` 预设为「唤醒 0（仅 K 线/信号驱动）+ 4H + 确认 3 轮」，则一次离场意图最坏要等 2 根 4H 收盘 = **8 小时**才被放行；若期间没有新确认根（WS 静默、`_stale_degraded` 使 `_maybe_interval_wake` 直接早退，`session.py:466-468`），推迟**没有上界**。而 `loss_release` 只在浮亏达阈时才解除，正常波动区间内不解除。这层是给真实持仓加的锁，锁必须有钥匙。 | 已修：G2 增加**确认窗口上界** `confirm_window_s`（派生量 = `min(0.25 × target_hold_min_s, 4 × bar_seconds)`）——首次离场意图起算，超时即视为确认完成；并入 §5.4 wire 与轨迹文案 |

### P1（必须闭合）

| 编号 | 位置 | 问题 | 结论 |
|---|---|---|---|
| **P1-1** | §2.2 派生规则 2 / §6.1 H-2 | `custom` 全默认时 `target_hold_max_s = 0`，按 `clamp(ceil(0/bar), 1, 1000)` 派生出 `expected_hold_bars = 1`，而**不是**回退全局常数 8。后果：H-2 宣称的「装配但空转 ⇒ 与未装配逐字段相等」不成立，且资金费期数与先验前瞻窗口被静默改成 1（`signal_prior.py:182` 会因 `horizon_mismatch` 丢弃整份先验）。这是本特性给用户的**安全退出口**，不能带副作用。 | 已修：§2.2 增加第三条派生规则——`target_hold_max_s <= 0 ⇒ expected_hold_bars 覆盖为 None`（逐字回退全局常数） |
| **P1-2** | §2.3.2 G3 | `cost_bps` 取 `CostRuntime.estimate_cost_only(decision, …)`，而该入口内部走 `query_for_decision`，其方向是 **`side = "long" if action == "open_long" else "short"`**（`cost_model.py:208`）——对 `close` 决策恒判为 `short`。又因资金费**只取不利侧** `max(0.0, signed)`（`cost_model.py:129-130`），**平多仓时资金费恒记 0、平空仓时恒记满额**。本轮恰恰把 `expected_hold_bars` 从 8 拉到 12–72，资金费在 `cost_bps` 里的占比被同步放大 ⇒ G3 阈值按方向系统性偏斜。 | 已修：G3 的成本查询改为**按当前持仓方向**构造（`query_for_position`，复用 `query_for_preview`，不新开估算路径）；§3.2 增列 `cost_model.py` 该项，`estimate_cost_only` 既有调用点行为逐字不变 |
| **P1-3** | §2.6 / §3.2 | 只改了 `resolve_cost_params`，漏了 `backtest_params(settings)`——它同样硬取 `settings.ai_trade_cost_expected_hold_bars`（`cost_model.py:105`）。于是回测侧 `cost_bps` 仍按 8 根算、实盘按 12–72 根算，G-3「实盘与回测在同一断面下 reason 逐字相等」**不可能通过**。 | 已修：§2.6 / §2.10 / §3.2 同步补 `backtest_params(settings, *, expected_hold_bars=None)` |
| **P1-4** | §3.4 store.ts 行 | 只写了 partialize。核实：`applyAiTradeTracePhase` 是**逐键手抄** reducer（`store.ts:241-261`，`edge_gate` 分支里还留着「不抄这三键界面永远显示旧文案」的血泪注释），新 `phase="horizon"` 没有 `case` 分支就会被静默丢弃（`aiTradeEdgeGate.test.ts:148` 证明未知相位是**静默忽略**、不报错）；`patchAiTradeMemberStatus`（`store.ts:886-895`）把 status 整体浅合并进 `m.session`，故 `status.horizon` 必须同时进 `AiTradeSessionRow` 类型才拿得到。 | 已修：§3.4 明列 store.ts 的三处改动（`case "horizon"` reducer + 轨迹初值键 + `AiTradeSessionRow.horizon`），并加放行条件 G-11 |
| **P1-5** | §4.2 `enforceMaxHold` / §3.2 | 该字段声称「true ⇒ 把 executor 的 `max_hold_bars` 设为 `expectedHoldBars`」，但 `max_hold_bars` 的唯一装配点 `manager._resolve_trade_risk`（`manager.py:657-668`）当前只取 config 默认，而 §3.2 的 manager 行未列此改动 ⇒ 字段无实现落点（死字段）。 | 已修：§3.2 manager 行补 `_resolve_trade_risk`；§4.2 补写该字段与 `max_hold_bars` 豁免的关系 |
| **P1-6** | §5.1 | 三个配置无消费方：`ai_trade_horizon_max_min_hold_s` / `ai_trade_horizon_max_confirm_rounds`（§4.2 明写「schema 层 422，服务端只做缺省回填，**不复夹**」，而 Pydantic 常量读不到 settings ⇒ 永远没人读）；`ai_trade_horizon_note_after_rounds` 全文只出现在配置表里，没有任何一节定义这条注记写什么、注到哪。「没有展示落点的常量即死码」是本仓库成文教训。 | 已修：删除前两个配置（值域唯一真源留在 schema）；为第三个补 §2.9(e) 机制定义与 wire 键 |
| **P1-7** | §2.1 / §2.4 | 「文案与判定共用同一份快照」在时序上不成立：payload 在 `decide()` **之前**装配（`session.py:639`），门禁在 `decide()` **之后**执行（§2.5），中间隔着一次完整 LLM 调用（数秒到数十秒）。而 §2.4 的 `gate(decision, degrade)` 不收 `facts`，没有定义它用哪一份快照 ⇒ G-4 无法验收，且用旧快照判 `loss_release` 会让「刚跌进解除区」的仓位晚一轮才拿回自由。 | 已修：§2.4 钉死双快照契约——prompt 段用 round-start 快照并带 `asOfWall`；**判定用门禁时刻重新 `refresh` 的快照**（时间只会向前 ⇒ G1 只会更松、豁免只会更早生效，方向安全）；trace 与 decision 事件下发的是**判定用**的那一份。G-4 相应改写为「trace/判定同源」 |
| **P1-8** | §4.1 | `AiTradeHorizonProfileOut` 的示例形状把 `bar` / `wakeIntervalMinutes` / `targetHold*` / `minHoldSeconds` 都写成非空，但 §2.2 表里 `custom` 这些格子是「不联动 / 用户填」。按示例形状实现，前端渲染 `custom` 卡片时会拿到一组无意义数字并据此联动 bar。 | 已修：§4.1 把这些字段声明为可空，并写明 `custom` 条目全为 `null`；前端契约同步 |
| **P1-9** | §4.2 | 「`targetHoldMinSeconds`/`targetHoldMaxSeconds` 仅 custom 需要；其余模式给了也**只影响文案与 withinTargetRate**」与 §2.2「`expected_hold_bars` 由 `target_hold_max_s` 派生」直接冲突——非 custom 模式覆盖该值会静默改写成本资金费期数与先验前瞻窗口。 | 已修：§4.2 改为如实描述（覆盖 `targetHoldMaxSeconds` 会连带改变 `expectedHoldBars`），并在回执里同时镜像 `expectedHoldBars` 供核对 |
| **P1-10** | §2.11 契约摘要 | 第 4 行「浮亏达 −12.5%（保证金）时以上限制全部解除」是拿 `5% × 5× × 0.5` 写死的算例，而 `intraday_swing` 的建议杠杆上限是 3×，且真实值取决于用户在同一张表单里填的 `stopLossPct` 与 `leverage`。这一行是**用户判断这层锁安不安全的唯一依据**，静态文案会说谎。 | 已修：该行改为按当前表单值现算（`stopLossPct ?? 服务端默认` × `leverage` × `lossReleaseFrac`），并在无 OCO 止损时改述为兜底常数口径 |

### P2（记录，不阻断本轮）

| 编号 | 位置 | 问题 |
|---|---|---|
| P2-1 | 全文命名 | 仓库已有 `signalPriors.horizonBars`（先验**前瞻**根数）；本轮新增的 `horizon` 是**持仓周期**。§2.6 让二者同源后语义确实一致，但 wire 里两个词必须始终带限定（`tradingHorizon` / `expectedHoldBars`），不得裸用 `horizon` 指代前瞻窗口 |
| P2-2 | §2.10 对照表 | 缺 `side` 一行。实盘取 `executor.projected`（**投影立场**，wake-scheduler 的 P0-1 铁律），回测应取 `pos.side`——已在 §2.10 补入 |
| P2-3 | §2.12 | `withinTargetRate` / `turnoverPerDay` / `avgHoldSeconds` 在**零样本**（未平过仓）时应下发 `null` 而非 0，否则 UI 的「目标区间内占比 0%」会被读成「一次都没达标」。已在 §2.12 加注 |
| P2-4 | §2.12 / §2.11 | `estimatedEpochs`、`releaseReasons` 明细在三个展示落点里没有指定归宿；G-9 的逐键核对表须覆盖它们（不展示就删键） |
| P2-5 | §3.2 | `session.py` 现为 **1664 行**，早已越过 `CLAUDE.md` 的 800 行红线。本轮承诺「净增 ≤55 行」是止血不是还债；建议在实施顺序末尾记一条独立技术债条目（拆分 `session.py` 的门禁编排段），不在本轮做 |
| P2-6 | §2.5 | min_hold 窗口内的每一轮唤醒仍会**完整烧一次决策 LLM**（`intraday_swing`：30min 唤醒 / 2h 窗口 ⇒ 至少 4 轮），门禁只省下反思那一次。这是「门禁必须在 decide 之后」的必然代价，属已知成本；L0 观察窗口应把每会话 LLM 轮数一并记下来 |
| P2-7 | §2.10 | 回测分辨率免责已写，但应**点名** `hourly_swing`（5m 执行 + 180s 窗口 ⇒ 回测里 G1 恒不生效），否则用户仍会拿该模式的回测结论上实盘 |
| P2-8 | §八 实施顺序 | 新增的 `AI_TRADE_HORIZON_*` env 依赖 desktop `config.js` 的 `AI_TRADE_` 前缀透传白名单（`config.py:182-183` 注释）。前缀已覆盖，但实施时须实测一次，别假设 |

---

## 一、概述

### 1.1 要解决的问题

线上样本给出的结论非常清楚：**AI 自动交易的毛利润是正的，被换手成本吃成了净亏损**。

最近 4 个有成交的 AI 运行样本一律是「执行周期 15m + Agent 每 3 分钟唤醒 + 约 5× 杠杆 + 利润保护开启」，
其中 16 次完整往返里：15 次由 Agent 主动平仓、仅 1 次由利润保护层平仓；14 笔时间戳可比的交易里 11 笔持仓
不足 1 小时。Agent 主动平仓那部分毛利润 +0.5845 USDT、双边手续费 3.0586 USDT、净结果 −2.4741 USDT；
而唯一那笔由保护层平掉的交易扣费后 +1.8655 USDT。

代码侧的根因同样可定位，且**四条互相叠加**：

1. **没有任何一层约束 Agent 主动平仓的最短持仓时间。** 现有 `minHoldSeconds`
   （`services/quant/app/config.py:279` → `profit_guard.py:748`）只抑制**利润保护层**在开仓瞬间被噪音触发，
   完全不约束 Agent 自己发出的 `close`。强制时间平仓 `ai_trade_default_max_hold_bars` 默认 0（关闭，
   `config.py:106`），也不是短持仓的成因。
2. **决策频率与持仓周期完全脱钩。** 默认每 2 分钟唤醒一次（`config.py:184`），且持仓轮**不会**被
   普通跳过规则过滤（`session.py:452` `_maybe_interval_wake` 的守卫只在空仓 + 无挂单时才跳过）。
   一个 15m 执行周期的会话，一小时内会被问 30 次「现在要不要平」。
3. **Prompt 存在明确的抢跑退出偏置。** 利润保护宪章写着「被保护层强制平仓……应视为一次决策失误。
   主动的 `close` 永远优先于等待保护层触发」（`profit_guard_text.py:53-54`），
   反思层又写着「`close` 是风险收敛动作，否决 close 等于延长敞口，须有极强理由」（`prompts.py:687`）。
   两条合起来 = 系统在制度上鼓励模型早退，而历史平仓理由里反复出现「与其等待系统平仓，不如主动退出」。
4. **成本与先验的预测窗口是一个与真实持仓无关的常数。** `ai_trade_cost_expected_hold_bars=8`
   （`config.py:327`）同时决定了往返成本里的资金费期数（`cost_model.py:127`）与信号先验的前瞻根数
   （`signal_prior.py:108`），它既不随用户想要的持仓周期变化，也不与实际持仓时长对账。

2026-08-06 的成本真值化提交（`cebee54`）已经让手续费与净盈亏显示正确、也让 Agent 看得见成本，
但**没有**、也不打算解决持仓周期问题——那次的范围是「把数字算对」，本轮的范围是「把周期管住」。

### 1.2 要建的东西

在启动 AI 自动交易时，让用户**显式选择一种「交易周期模式」**（小时波段 / 日内波段 / 短波段 / 趋势持仓 /
自定义）。这个选择不是一个提示性标签，而是一份**结构化契约**：它一次性决定执行周期、唤醒节奏、
成本与先验的前瞻窗口、以及三条**由系统确定性执行**的换手约束（最短持仓窗口、离场连续确认、换手迟滞），
并同时改写系统 prompt 里那两处抢跑偏置，让利润保护层真正承担尾部退出职责。

实现方式**完全复用仓库已有的「铁律层」范式**——不是给策略库加第四种策略，而是在信号板之外再加一层
纯核心 + 确定性门禁 + 宪章 prompt，与 `base_rules.py`（MA 通道开单铁律）、`profit_guard.py`（利润保护）
逐字同构：纯函数核心（无 IO / 无 LLM / 无 broker）+ 会话侧薄封装运行时 + 实盘与回测共用同一份判定。

### 1.3 为什么这样切

「让用户选周期」有三种做法，本方案选第三种：

- **只改默认值**（把默认周期改 1H、唤醒改 0）：这就是调查里写的「不改代码的缓解办法」，
  它治不了「Agent 仍可在任意决策轮主动 close」，样本里 11/14 笔短持仓就是在这条路径上产生的。
- **加一个策略**：系统 prompt 宪法级地写着「信号是参考不是指令，你有权推翻任何单一信号」
  （`prompts.py:119-122`）。把持仓纪律做成信号板成员 = 把铁律降级成建议，这条教训在
  `ai-trade-base-strategy-ma-band` 已经付过学费。
- **加一层确定性门禁 + 一份用户可选的契约**（本方案）：软引导（宪章 prompt）让模型主动遵守、
  硬保证（门禁）让模型无法违反，并且**门禁只约束 Agent 自己发起的换手**，止损、交易所 OCO、
  利润保护层、会话止损、强制时间平仓一律不受约束——这是本层唯一不可动摇的安全语义。

---

## 二、技术设计

### 2.1 架构位置与数据流

```
启动弹窗（模式选择器）
      │  tradingHorizon: {profile, 可选覆盖}
      ▼
POST /api/ai-trade/start ──► api/ai_auto.py::_resolve_horizon   ← 唯一解析点（默认开启语义）
                                     │  HorizonRule（frozen dataclass）
                                     ▼
                        manager.AiTradeGroupStartRequest.horizon_rule
                          │                              │
                          │ 宪章 prompt（软引导）           │ 会话装配（硬保证）
                          ▼                              ▼
        build_ai_trade_system_prompt(horizon_text=…)   AiTradeSession._horizon = HorizonRuntime(rule)
                                                             │
   每 5s 盯市 tick ──► _horizon.refresh(facts)  ← epoch/时钟/浮盈断面（纯同步）
                                                             │
   每个决策轮 ──► payload["tradingHorizon"] = _horizon.wire() ─┤（prompt 只读段）
                 decide() → base_rule 门禁 → **horizon 门禁** → cooldown → brake → consensus → edge → reflection
                                                             │
   每笔平仓成交 ──► _dispatch → _horizon.note_close(trade)   ← 真实持仓时长样本（唯一收口）
                                                             │
   finalize_run ──► metrics_json.aiStats.horizon              ← 验收指标
```

三条纪律（与仓库既有分层完全一致，评审时按此对照）：

1. **判定纯核心在 `hold_horizon.py`，一处也不得复制**。实盘 `session.py` 与回测 `backtest.py` 调同一个
   `evaluate_horizon_gate`，差别只在「谁提供 `HorizonFacts`」（实盘给墙钟 + 实时价，回测给 bar ts + 收盘价）。
   这条约束的来源是 `base_rules` P1-4 与 `profit_guard` F3 的先例：两侧各写一份 = 用回测调出的参数在实盘失真。
2. **文案与判定共用同一份快照**。`hold_horizon_text.py` 只读 `wire()` 产物，绝不重算任何一个数
   （`profit_guard_text.py` 的 `[R-5]` 教训）。
3. **未装配 ⇒ 整键缺席 ⇒ 渲染与 payload 逐字节回退**（`baseRules` / `profitGuard` / `sizingPlan` / `costAwareness`
   已确立的范式）。这使「关掉总闸 = 回到本特性合入前」是一个可被冻结断言证明的事实，而不是一句承诺。

### 2.2 模式预设表（唯一真源：`hold_horizon.PROFILES`）

| id | 标题 | 目标持仓 | targetMin(s) | targetMax(s) | 建议执行周期 | 建议唤醒(min) | **最短主动平仓窗口**(s) | **离场确认轮数** | **换手迟滞 k** | 建议杠杆上限 |
|---|---|---|---|---|---|---|---|---|---|---|
| `hourly_swing` | 小时波段 | 6 分钟 – 2 小时 | 360 | 7 200 | `5m` | 3 | 180 | 2 | 1.2 | 5× |
| `intraday_swing` | 日内波段 | 4 – 12 小时 | 14 400 | 43 200 | `1H` | 30 | 7 200 | 2 | 1.5 | 3× |
| `short_swing` | 短波段 | 12 – 48 小时 | 43 200 | 172 800 | `4H` | 120 | 21 600 | 3 | 2.0 | 2× |
| `trend_hold` | 趋势持仓 | 1 – 3 天 | 86 400 | 259 200 | `4H` | 0（仅 K 线 / 信号驱动） | 43 200 | 3 | 2.0 | 2× |
| `custom` | 自定义 | 用户自定义 | 用户填 | 用户填 | 不联动 | 不联动 | 用户填（默认 0） | 用户填（默认 1） | 用户填（默认 0） | — |

三条派生规则（**必须写成代码里的函数，不得在表里手抄第二遍**）：

- `min_hold_s = 0.5 × target_hold_min_s`（表中数字即该式结果，评审可逐行核对）。
  取一半而非取满，是因为硬地板应当**明显低于**目标下界：它的职责是掐掉明显的噪音换手，
  不是替模型做判断；完整的目标区间通过 prompt 软引导传达。
- `expected_hold_bars = clamp(ceil(target_hold_max_s / BAR_SECONDS[bar]), 1, 1000)`，
  按**用户实际选择的 bar** 现算（不是按预设的建议周期）。取上界而非中值，是因为它唯一的消费方是
  成本模型的资金费期数与先验前瞻窗口——成本估算宁可高估不可低估（`cost_model.py` 既有极性）。
  **`target_hold_max_s <= 0` ⇒ 本项恒为 `None`**（[P1-1]）：`None` 一路透传到
  `resolve_cost_params` / `backtest_params` / `resolve_prior_params` 的可选形参，即**逐字回退全局常数**
  `ai_trade_cost_expected_hold_bars`。若不写这一条，`ceil(0/bar)` 经 `clamp(...,1,...)` 会派生出
  `expected_hold_bars = 1`——资金费期数被压成 1 期、先验快照按 `horizon_mismatch` 被整份丢弃
  （`signal_prior.py:182`），而这恰恰发生在用户选「自定义 + 什么都不填」这个**本应零副作用**的路径上。
- `confirm_window_s = min(0.25 × target_hold_min_s, 4 × BAR_SECONDS[bar])`（[P0-3]，G2 的墙钟上界）。
  `target_hold_min_s <= 0` ⇒ `0` ⇒ **窗口机制关闭**（此时 `exit_confirm_rounds` 也已是 1，G2 本就空转）。
- `custom` 的三个空值默认（0 / 1 / 0）使该模式在用户不填任何东西时**逐字等价于本层未装配**：
  `min_hold_s=0` ⇒ G1 恒放行；`exit_confirm_rounds=1` ⇒ G2 首轮即满足；`k=0` ⇒ G3 恒放行；
  且 `target_hold_max_s=0` ⇒ `expected_hold_bars=None` ⇒ 成本与先验窗口逐字回退（见上条）。
  这是「自定义」的安全退出口，也是单测里构造「装配但空转」用例的唯一方式。

预设值的落地形态是**后端 `GET /api/ai-trade/horizon-profiles`**（§4.1），前端**不复制任何一个数字**
（含中文标签）。理由见 §7 风险 R-3：本仓库已经有过前后端各存一份口径导致静默漂移的事故
（`edgeSource` 逐键手抄吞新键），一张有 11 列 × 5 行的表手抄一遍必然漂移。

### 2.3 持仓周期铁律层纯核心（`hold_horizon.py`）

#### 2.3.1 受约束的动作（豁免矩阵，冻结）

```python
def gated_kind(action: str, position_side: str, *, gate_reverse: bool) -> str | None:
    """本轮动作是否受本层约束：'close' | 'reverse' | None（None ⇒ 恒直通、同一对象返回）。"""
    if action == "close" and position_side in ("long", "short"):
        return "close"
    if not gate_reverse:
        return None
    if action == "open_long" and position_side == "short":
        return "reverse"
    if action == "open_short" and position_side == "long":
        return "reverse"
    return None
```

- **只有** Agent 主动发起的平仓与反手受约束。`hold` / `cancel_order` / 空仓开仓**永不**被拦。
- 反手被否 ⇒ 整个决策降级为 `hold`（close 腿与 open 腿**都不发生**），与 `base_rules._veto`
  的「绝不产生半反手态」语义逐字同构。
- **结构性豁免**（不经决策链，因此天然不受本层影响，必须写成单测断言而不是口头承诺）：
  利润保护层平仓 `_apply_profit_exit`、承诺退出线、会话级 USD 止盈止损 `_maybe_session_exit`、
  executor 的强制时间平仓 `_maybe_time_exit`、交易所侧 OCO、`stop()` 的 flatten、外部干预的合成平仓。

#### 2.3.2 三道闸

设 `facts` 为本轮断面（§2.4），`rule` 为已装配规则：

**G1 最短持仓窗口**
```
hold_s < rule.min_hold_s  ⇒  veto("horizon_min_hold(held=92m,min=120m)")
```
时钟为**墙钟秒**（实盘 `time.time()`，回测 bar ts），刻意**不用根数**：执行周期可变而目标持仓是一个
物理时长，用根数会在 15m/4H 之间产生 16 倍的量纲差——这正是仓库里 `trend_band_swing`
（15m ATR 跟踪线比 12H ATR 结构止损紧 5.5 倍，导致后者永不生效）与 `wake-aware-context`
（ATR 跨尺度量纲失配）两次踩过的同一个坑。

**G2 离场连续确认（带墙钟上界）**
```
exit_confirm_streak + 1 < rule.exit_confirm_rounds
    且 now_wall - first_exit_intent_wall < rule.confirm_window_s     ⇒ veto("horizon_exit_unconfirmed(1/2)")
```
`exit_confirm_streak` 由运行时维护：**第一阶段决策**（`decider.decide` 的直接返回值，门禁与反思之前）
为 `close`/反手时 +1，为其它动作时归零，持仓 epoch 变化时归零。用第一阶段而非门禁后的动作，
理由与 `_update_hold_streak`（`session.py:520`）逐字相同——惯性以模型的真实意愿为准。

**墙钟上界 `confirm_window_s` 是本闸的钥匙，不是优化项（[P0-3]）**：`first_exit_intent_wall` 记录本
epoch 内**第一次**离场意图的墙钟（streak 归零时一并清空）；从它起算超过 `confirm_window_s` 之后，
G2 视为已确认、直接放行（快照标 `confirmWindowElapsed=true`，轨迹文案写「确认窗口已到，按意愿放行」）。
没有这条上界时，`trend_hold`（唤醒 0 + 4H + 3 轮）的一次离场意图最坏要等两根 4H 收盘 = 8 小时；
而当行情源静默、`_stale_degraded` 置位时 `_maybe_interval_wake` 直接早退（`session.py:466-468`），
根本不会再产生新的决策轮 ⇒ 推迟**没有上界**。给真实持仓上的锁必须有钥匙，而不是只有「亏够了才开」
的 `loss_release`。派生式见 §2.2（`min(0.25 × target_hold_min_s, 4 × bar_seconds)`：
既不超过目标下界的四分之一，也不超过四根执行周期）。

**G3 换手迟滞（只约束获利了结）**
```
move_bps > 0 且 move_bps < rule.hysteresis_k × cost_bps
    ⇒ veto("horizon_turnover_hysteresis(move=8.2bps,req=19.5bps)")
```
- `move_bps = (mark_px − entry_px)/entry_px × 1e4 × (+1 多 / −1 空)`，**价格口径**。
- `cost_bps` 取 `CostRuntime.estimate_cost_for_position(position_side, bar_seconds)`——仍是同一对纯核心
  （`estimate_round_trip_cost` + `resolve_cost_params`），**不新开第二条估算路径**，只是把方向换成
  **当前持仓方向**而非本轮动作。
  > **[P1-2] 为什么不能直接复用 `estimate_cost_only(decision, …)`**：该入口内部走 `query_for_decision`，
  > 其方向是 `side = "long" if action == "open_long" else "short"`（`cost_model.py:208`）——对 `close`
  > 决策恒判为 `short`。而资金费**只取不利侧**（`max(0.0, signed)`，`cost_model.py:129-130`），于是
  > **平多仓时资金费恒记 0、平空仓时恒记满额**。本轮把 `expected_hold_bars` 从 8 提到 12–72，资金费
  > 在 `cost_bps` 里的占比同步放大，方向取错会让 G3 的阈值按多空系统性偏斜——多头永远拦不住、
  > 空头永远拦过头。实现上新增薄入口 `estimate_cost_for_position(side, bar_seconds)`：直接调
  > `query_for_preview(ord_type=…, side=position_side, …)` + 同一对纯核心，`estimate_cost_only` 的既有
  > 签名与既有调用点（`session.py:990` 仓位层）**一字不改**。
- **`move_bps <= 0` 一律放行**：止损、割肉、认错离场永远不受迟滞约束。本闸的唯一职责是
  「不要为了一个还没覆盖手续费的浮盈去付两笔手续费」，也就是样本里 +0.5845 毛利被 3.0586 手续费
  吃掉的那个机制本身。
- `cost_bps` 不可用（`None`）⇒ 本闸放行（fail-open）。

#### 2.3.3 豁免（release）——本层的安全阀

豁免命中即在快照里如实标注 `released=true` 与 `releaseReason`。**[P0-2] 每条豁免解除哪几道闸是逐条
写死的，不存在「大概是全解」这种读法**——下表即冻结口径，`test_ai_trade_horizon_exemptions.py` 与
H-11 ~ H-14 逐格对照：

| releaseReason | 判据 | 解除 G1 | 解除 G2 | 解除 G3 | 理由 |
|---|---|---|---|---|---|
| `loss_release` | `return_pct <= -loss_release_return_pct` | ✔ | ✔ | ✔ | 亏损到一定程度必须允许随时离场；`return_pct` 为**保证金收益率口径**（与利润保护层、OCO 同口径）。G3 本就只约束获利了结，此处一并列出是为了让「全解」这件事不依赖第二条推理 |
| `stale_px` | 判定价年龄 > `ai_trade_profit_guard_max_px_age_s`，或会话 `_stale_degraded`，或 `mark_px`/`entry_px` 非正有限 | ✔ | ✔ | ✔ | 「算不出」绝不能变成「不许平」；三道闸的输入全部来自这个断面，任一不可信则整层不可信 |
| `epoch_estimated` | 本 epoch 的 `opened_wall` 是估算值（未观察到开仓成交事件） | ✔ | ✘ | ✘ | **只解 G1**：受污染的只有「持仓时长」这一个量，`exit_confirm_streak` 与 `move_bps` 不依赖 `opened_wall`，全解等于借一个局部不确定性把整层关掉 |
| `max_hold_bars` | 启用了强制时间平仓且 `bars_in_position >= risk.max_hold_bars` | ✔ | ✔ | ✔ | 上限已到、executor 下一跳就会强平（`live_auto/executor.py:244`），此刻任何限制都只是让 Agent 比系统晚一步平，纯负收益 |

`releaseReason` 同时命中多条时按上表**自上而下**取第一条（`loss_release` 优先级最高），
`released` 字段的语义恒为「本轮**至少有一道闸**被解除」；被解除的具体闸位在快照里以
`releasedGates: ["G1","G2","G3"]` 如实列出——**只写 `released=true` 而不写解了哪几道，就是把 P0-2
这个歧义原样留给实现者**。

`loss_release_return_pct` 的求法（**派生量，不是第五个魔数**）：

```python
def loss_release_return_pct(rule, *, stop_loss_pct: float | None, leverage: float) -> float:
    """解除阈（保证金收益率，正数=幅度）。有单仓 OCO 止损 ⇒ 取其一定比例；否则回落配置常数。"""
    if stop_loss_pct and stop_loss_pct > 0 and leverage > 0:
        return float(stop_loss_pct) * float(leverage) * rule.loss_release_frac
    return rule.loss_release_return_pct_fallback
```
默认 `loss_release_frac = 0.5`：OCO 止损 5% 价格 × 5× 杠杆 = 25% 保证金亏损，
则浮亏达到 −12.5% 保证金收益率时本层完全放手，Agent 重新拥有随时止损的自由。

#### 2.3.4 极性：fail-open（与 MA 通道铁律相反，与利润保护层相同）

`base_rules` 是 fail-closed（算不出通道 ⇒ 不许开仓），因为「无法验证合规」不等于「合规」。
本层必须**相反**：它拦的是 `close`，而 `close` 是风险收敛动作。任何「算不出 ⇒ 不许平仓」的设计
都会在断网 / 暖机 / 脏数据的瞬间把一笔真实持仓锁死——那比它想解决的问题严重一个数量级。
所有不确定路径一律放行，并记 `released` 计数供告警。

### 2.4 会话运行时（`horizon_runtime.py`）

镜像 `ProfitGuardRuntime` / `CostRuntime` / `SizingRuntime` 的既有姿态：**纯同步、无 await、
不持有 session 引用、不碰 executor 写路径**；`session.py` 只留薄封装调用（该文件已 1664 行，
远超 CLAUDE.md 800 行红线，本特性**不得**再往里加逻辑）。

```python
@dataclass(frozen=True)
class HorizonFacts:
    """一次判定所需的断面（聚合结构体，守 CLAUDE.md「形参 ≤5」）。"""
    seq: int                      # 会话级持仓序号（session._position_seq）
    side: str                     # long | short | flat（executor.projected）
    entry_px: float
    mark_px: float                # 判定价 = session._current_px()（单源，绝不用确认根收盘价）
    leverage: float
    now_wall: float
    px_age_s: float | None
    stale_feed: bool
    bars_in_position: int | None
    max_hold_bars: int | None     # executor.risk.max_hold_bars（None/0 = 未启用）
    stop_loss_pct: float | None   # executor.risk.stop_loss_pct（loss_release 派生用）
    cost_bps: float | None        # CostRuntime.estimate_cost_only 产物（G3 用）


class HorizonRuntime:
    def __init__(self, rule: HorizonRule | None) -> None: ...
    # 状态推进（每个盯市 tick + 每个决策轮各一次；幂等）
    def refresh(self, facts: HorizonFacts) -> dict | None: ...
    # 决策链薄封装：未装配 / 非受控动作 ⇒ 返回**同一个** decision 对象（`is` 相等）
    # facts 为门禁时刻**重新采集**的断面（[P1-7]），判定与下发的快照恒取本次产物
    def gate(self, decision, degrade: str | None,
             facts: HorizonFacts) -> tuple[object, str | None, dict | None]: ...
    # 第一阶段意图记账（连续确认计数）；须在 gate 之前调用
    def note_intent(self, stage1_action: str, stage1_degrade: str | None) -> None: ...
    # payload 注入（未装配 ⇒ 整键缺席）
    def inject(self, payload: dict, snapshot: dict | None) -> None: ...
    # 成交事件收口（epoch 起点 / 真实持仓时长样本）
    def note_open(self, trade: dict, now_wall: float) -> None: ...
    def note_close(self, trade: dict, now_wall: float) -> None: ...
    # finalize 统计合并（镜像 _sizing.merge_into）
    def merge_into(self, stats: dict) -> dict: ...
```

**epoch 身份**：`(side, seq)`，与利润保护层完全同源（`profit_guard.advance_state` 的 `(side, seq)`）。
`seq` 复用 `session._position_seq`（`_dispatch` 观察到任一 `side=="close"` 成交即 +1，`session.py:278`）。
`opened_wall` 的**首选真源**是 `_dispatch` 里的开仓成交事件（`note_open`，精确到成交时刻，
部分成交不重置——`(side, seq)` 未变即同一 epoch）；`refresh()` 发现有持仓却无 epoch 时兜底建一个并置
`opened_wall_estimated=True`，此时 G1 走 `epoch_estimated` 豁免（§2.3.3）。

**持仓时长样本**：`note_close` 在同一个唯一收口（`_dispatch`，与 `_cost.record_close` /
`_sizing.note_close` 并列）记录 `hold_s = now_wall - opened_wall`，只在 `opened_wall_estimated=False`
时入样本（估算值不得污染验收指标）。开仓**部分成交**不重置 epoch：`_dispatch` 只在 `side == "close"`
时自增 `_position_seq`（`session.py:277-278`），而开仓增量成交发的是 long/short 事件；同时
`LiveExecutor._adopt_fill` 会在每次增量采纳时刷新 `entry_bar`（`executor.py:489`），故 `opened_wall`
**必须**由 `note_open` 在 epoch 首次建立时锁定，绝不可回读 `entry_bar` 反推。平仓侧不存在部分结算
（`_adopt_partial` 对 close 意图刻意不做增量采纳，`executor.py:461-472`），故一个 epoch 恰对应一次
`note_close`。

**[P1-7] 双快照契约（判定与文案的同源边界）**：payload 在 `decide()` **之前**装配（`session.py:639`），
门禁在 `decide()` **之后**执行，中间隔着一次完整 LLM 调用。因此本层每轮存在两份快照，职责必须分开写死：

| | 采集时刻 | 消费方 | 约束 |
|---|---|---|---|
| **round-start 快照** | `_build_decision_payload` 内 `refresh(facts)` | `payload["tradingHorizon"]` → prompt 只读段 | 必须带 `asOfWall`；文案渲染只读它、绝不重算 |
| **gate 快照** | `_apply_horizon_gate` 内**重新** `refresh(facts)` | 三道闸判定、`horizon` 轨迹段、`decision` 事件、`status` 事件 | 判定与下发恒取同一份 |

方向安全性可证：两次采集之间只有时间向前流逝，故 `hold_s` 只增不减（G1 只会更松）、
`confirm_window` 只会更接近到期（G2 只会更松）、`loss_release` 只会更早生效（豁免只会更可得）。
唯一可能收紧的是 `move_bps` 的浮动，而 G3 被拦意味着「浮盈还没盖过成本」——那本就是它的职责。
G-4 的验收口径据此改写为「**trace / decision 事件里的数与判定同源**」，prompt 段另守
「`asOfWall` 必须存在且早于本轮 trace 的 ts」。

### 2.5 决策链插入位置

`session._run_decision_round`（`session.py:629`）现有顺序：

```
decide() → _update_hold_streak → _update_profit_hold_streak → _record_profit_commitment
        → base_rule 门禁 → profit cooldown → sizing/brake → consensus → edge → reflection → sizing 求解 → execute
```

本层插入为 **base_rule 之后、profit cooldown 之前**，并在 `decide()` 之后立刻调用 `note_intent`：

```python
self._horizon.note_intent(decision.action, degrade)          # 与 _update_hold_streak 同一批，第一阶段口径
decision, degrade = await self._apply_base_rule_gate(...)    # 既有
decision, degrade = await self._apply_horizon_gate(round_no, ts, decision, degrade)  # ★ 新增
decision, degrade = self._apply_profit_cooldown_gate(...)    # 既有
```

四条硬约束（任一违反即方案失效）：

1. **必须在 `decide()` 之后**：`decider` 的重试循环只认 `_map_decision` 产出的 degrade，
   把门禁塞进循环内会让每一轮被否的 close 白烧一次 LLM（`base_rules` P1-1 同款教训）。
2. **必须在 reflection 之前**：被否轮此时已是 `hold`，而反思对 `hold` 本就直通 ⇒ 自动省掉一次反思 LLM。
3. **必须在 base_rule 之后**：优先级顺序 `base_rule > horizon > cooldown > brake > consensus > edge`，
   `degrade or reason` 的先执行者胜出语义据此产生正确的归因文案。
4. **`note_intent` 必须在任何门禁之前**：连续确认计数以模型真实意愿为准，若记门禁后的动作，
   一次 MA 通道否决就会把 close 意图的连续性洗掉，G2 永远数不到 2。
5. **[P1-7] `_apply_horizon_gate` 内部必须先 `_horizon_facts()` + `refresh()` 再 `gate()`**：
   它拿到的不能是 `_build_decision_payload` 那一刻的旧断面（中间隔着一次完整 LLM 调用）。
   薄封装的三行职责固定为「采断面 → 推状态 → 判定并发轨迹」，判定与下发的 wire 恒取本次产物（§2.4）。

### 2.6 成本与先验的前瞻窗口改由真实目标持仓驱动（调查约束 #6）

现状：`ai_trade_cost_expected_hold_bars=8` 是**全局常数**，同时被两处消费：

- `cost_model.resolve_cost_params`（`cost_model.py:90`）→ 资金费期数
  `periods = ceil(expected_hold_bars × bar_seconds / funding_interval_s)`（`cost_model.py:128`）；
- `signal_prior.resolve_prior_params`（`signal_prior.py:108`）→ 先验前瞻根数 `h`，
  并与快照的 `horizonBars` 做 C-3 同源校验（`signal_prior.py:181-183`，不等即整份先验被丢弃）。

改法（**只加可选覆盖，不改默认行为**）：

```python
# cost_model.py
def resolve_cost_params(settings, *, rates=None, expected_hold_bars: int | None = None) -> CostParams:
    ...
    expected_hold_bars=int(expected_hold_bars if expected_hold_bars is not None
                           else settings.ai_trade_cost_expected_hold_bars)

# cost_model.py —— [P1-3] 回测世界的成本常数同样硬取全局值（现 `cost_model.py:105`），必须同步开口，
# 否则回测按 8 根算资金费、实盘按 12–72 根算，G-3「两侧 reason 逐字相等」不可能通过。
def backtest_params(settings, *, expected_hold_bars: int | None = None) -> CostParams: ...

# signal_prior.py
def resolve_prior_params(settings, *, horizon_bars: int | None = None) -> PriorParams: ...
```

- `CostRuntime.__init__` 新增 keyword-only `expected_hold_bars: int | None = None`，
  由会话从 `HorizonRule.expected_hold_bars` 传入；`_cost_params()` 三个分支统一透传。
- `PriorRuntime.__init__` 同款新增，`session` 传同一个数（**两处必须同源**，否则又造出第三个口径）。
- 未装配 / 总闸关 ⇒ `None` ⇒ 逐字回退全局常数。

**先验快照的 C-3 冲突（必须正视，不得假装不存在）**：一旦会话的 `horizon_bars` 变成 12/18/24，
而库里的快照是按全局 8 建的，`signal_prior.py:182` 会判 `horizon_mismatch` 并**丢弃整份先验**。
这不是 bug 而是正确行为（拿 8 根前瞻的胜率去给 12 根持仓做 EV 分子是错的），但必须给运维一条出路：

- `POST /api/ai-trade/priors/rebuild` 入参新增可选 `horizonBars`（`ge=1, le=1000`），
  缺省仍取 `settings.ai_trade_cost_expected_hold_bars`；
- `GET /api/ai-trade/priors` 的 `contract.horizonMatch` 增加可选 query `horizonBars` 用于按目标模式核对；
- 启动时若装配了 horizon 且快照 `horizonBars` 不匹配，`PriorRuntime` 已有的
  `AI_TRADE_PRIOR_CONTRACT_MISMATCH` 告警照常推送（不新增机制），弹窗侧不做任何拦截。
- 影响面有限：`ai_trade_signal_prior_enabled` 默认 `False`（`config.py:362`），默认部署零影响。

### 2.7 资金费（调查约束 #7）

调查要求「1–2 天持仓必须计算实际资金费，OKX 结算周期会动态变为 1/2/4/8 小时，不能固定假设」。

现状核对结论：**机制已经正确，缺的只是一个真实的持仓时长**。

- `OKXRestClient.get_funding_rate`（`rest_client.py:259-284`）已经返回 `intervalS`，
  且它是**由 `fundingTs` 与 `nextFundingTs` 相减得出**的真实值，只有缺失时才回落 8h。
- `cost_model._funding_carry_bps` 按 `ceil(hold_s / interval)` 算期数、**方向感知且只取不利侧**、
  取数失败时 `funding_carry_bps=None` + `confidence="degraded"`（绝不静默按 0 计）。
- `FundingCache.enabled` 的构造条件是 `funding_enabled and (k_min>0 or cost_awareness_enabled)`
  （`cost_runtime.py:56-62`），而 `ai_trade_cost_awareness_enabled` 默认 True ⇒ 默认在刷。

本特性要做的只有两件事：

1. §2.6 的 `expected_hold_bars` 覆盖，让 `hold_s` 变成真实目标持仓（趋势持仓 3 天 ⇒ 9 期而非 1 期）；
2. **在成本宪章与每轮成本段里显式披露期数**：`cost_text.format_cost_charter` /
   `cost_view.build_cost_view` 增加 `fundingPeriods` 与 `fundingIntervalS` 两个只读键，
   prompt 文案写成「按目标持仓 12 小时估算，预计跨 2 个资金费结算点（当前周期 8h，实际以交易所为准）」。
   缺 `intervalS` 时如实写「结算周期未知，按 8h 保守估算」——**绝不**写成一个确定的数。

### 2.8 长持仓的交易所侧兜底（调查约束 #8）

约束要求「长持仓必须保留交易所侧持久化止盈止损」。现状：`manager._resolve_trade_risk`
（`manager.py:657-668`）把 `stop_loss_pct` / `take_profit_pct` 夹紧后随开仓单附上，
`ai_trade_default_sl_pct=0.05` / `tp_pct=0.10`，用户可在弹窗填 0 之外的值。

本特性**不改这条链路**，只加三条防线：

1. 弹窗在选择 `short_swing` / `trend_hold` 时，若单仓 OCO 止损被清空（走服务端默认）或被填为极大值，
   显示一条**阻断性**校验错误：「跨日持仓必须设置交易所侧止损（进程退出后它是唯一还生效的兜底）」。
   判据：`stopLossPct == null` 时视为使用服务端默认 5%，允许通过但显示提示；显式填入 0 时阻断。
2. `hold_horizon_text.format_charter` 在跨日模式下把「交易所侧止损 X% / 止盈 Y% 已随单挂出」
   写进宪章——模型必须知道自己的兜底在哪，才能规划「不必每轮盯盘」的退出。
3. 每轮 prompt 的持仓周期段里复用 `profit_guard` 快照已有的
   `exchangeStopLossPx` / `exchangeTakeProfitPx`（**不重算**），并在两者皆 `None` 时如实写
   「交易所侧未挂止盈止损——此刻没有任何持久化兜底」（沿用 `_format_unavailable_fallback` 的诚实约束，
   即 v1 终审 RV-1「不得宣称并不存在的兜底」）。

### 2.9 Prompt 偏置矫正（调查约束 #4）

三处文案改动。三处**全部**由「本会话是否装配了 horizon」门控，未装配时逐字节回退（冻结断言可证）。

**(a) 利润保护宪章（`profit_guard_text.format_profit_guard_charter`）**

现文案（`profit_guard_text.py:53-54`）：

> 它是**兜底底线，不是你的止盈策略**——被保护层强制平仓意味着你没有在更好的位置主动离场，
> 应视为一次决策失误。主动的 `close` 永远优先于等待保护层触发。

改为（`horizon_armed=True` 分支；`False` 分支一字不改）：

> 它是**兜底底线，也是你的退出执行器**——当你的持仓理由仍然成立时，让保护线去执行退出，
> 与你主动离场同样正确；**提前离场同样是一种错误**，它用两笔确定的手续费换一个尚未成熟的判断。
> 只有当你能指出「持仓理由已经失效」时才主动 `close`。

保留原文案的**第二层语义一字不动**（「保护层在行情或持仓读数异常时会主动不动作……不是一条你可以
依赖的保证」）。这一层是 v1 的 P1-5 放行条件，删掉会让模型据一条它以为无条件成立的保证去延长持有，
而本层极性是 fail-open。

> **有意偏离记录**：v1 的 P1-5 把「被保护层强平 = 一次决策失误」列为**不可删**，理由是防止
> 「反正有兜底」的道德风险。本轮据线上证据推翻该条的**前半句**：15/16 次退出由 Agent 主动发起、
> 净 −2.4741 USDT，唯一一次保护层退出净 +1.8655 USDT。道德风险的防线改由**确定性门禁**
> （G1/G2/G3）与保留的第二层语义共同承担，而不再依赖一句会诱发抢跑的措辞。详见 §9 拍板 D-1。

**(b) 保护性平仓回执（`profit_guard_text._format_last_exit`）**

现结尾：「这意味着你没有在更好的位置主动离场——本轮请据此调整你的退出计划。」
→ `horizon_armed` 时改为：「保护线已按你设定的持仓周期完成了这次退出。请据此校准你的退出计划，
但不必因此提前离场。」

**(c) 反思复核官（`prompts.build_reflection_system_prompt`）**

现文案（`prompts.py:687-688`）：「`close` 是风险收敛动作，否决 close 等于延长敞口，须有极强理由。」
→ 追加（不替换）一段，仅在 horizon 装配时注入：

> 本会话设定了持仓周期模式（目标持仓 4–12 小时）。因此「过早的 `close`」与「该平不平」是**对称**的
> 两种错误：前者用两笔确定的手续费换一个未成熟的判断。复核一笔主动平仓时，请同样检查它的依据是否
> 只是分钟级噪音、是否与该模式的时间尺度相称。系统另有确定性的最短持仓窗口与连续确认约束，
> 你无需替它执行，但你的裁决不应与它相悖。

实现方式：`build_reflection_system_prompt(horizon_text: str = "")`，默认 `""` ⇒ 逐字节回退；
`decider.reflect` 在调用处传 `hold_horizon_text.format_reflection_note(payload.get("tradingHorizon"))`
（`decider.py:451` 处，payload 就在手上，零额外管线）。

**(d) 战术周期纪律（调查约束 #2）**

`prompts._tactical_discipline` 已有「战术周期只用于择时与确认，不得单独反转中线判断」。
本层在宪章里再钉一句更强的：「**单根分钟级回撤不构成平仓理由**；执行周期与更高周期的持仓逻辑
未失效之前，战术周期的反向信号只应影响你的离场**价位**，不应影响你的离场**决定**。」

**(e) 连续被拦时的自察注记（[P1-6]，`ai_trade_horizon_note_after_rounds` 的唯一消费方）**

`HorizonRuntime` 维护 `blocked_streak`（本 epoch 内被本层连续拦下的轮数；放行 / 豁免 / epoch 变化即归零）。
当 `blocked_streak >= settings.ai_trade_horizon_note_after_rounds`（默认 3，`0 = 关闭`）时，
`build_snapshot` 追加一个只读键 `blockedStreakNote`，由 `hold_horizon_text.format_horizon_for_prompt`
渲染进每轮的持仓周期段：

> 你已连续 4 轮提出离场而被持仓周期约束拦下（最短持仓窗口还差 28 分钟）。这不是让你重复同一个判断——
> 请要么明确指出「持仓理由已经失效」的**新证据**，要么把这段时间用于规划离场**价位**（限价委托 / 目标位），
> 而不是在每一轮重复同一次点击。

设计约束三条：**(i)** 这是纯加法的只读键，`note_after_rounds=0` 或未装配 ⇒ 整键缺席 ⇒ prompt 逐字节回退；
**(ii)** 文案里的数**全部**取自同一份 gate 快照（`blockedStreak` / `minHoldRemainingSeconds`），不重算；
**(iii)** 它不改变任何判定——只是让模型知道自己在撞墙，避免把 G2 的连续确认计数变成一个模型察觉不到的黑箱。

### 2.10 回测侧同构

回测（`ai_auto/backtest.py`）与实盘共用同一份纯核心，差别仅在时钟与断面来源：

| | 实盘 | 回测 |
|---|---|---|
| `now_wall` | `time.time()` | `int(row["ts"])`（当前根 ts） |
| `opened_wall` | 开仓成交事件墙钟（`note_open` 锁定，部分成交不重置） | `self._bar_ts(pos.entry_bar, default=ts)`（既有 helper，`backtest.py:476`） |
| `mark_px` | `_current_px()`（未确认根实时价） | 当前根收盘价 |
| `side` | `executor.projected`（**投影立场**，[P2-2]） | `pos.side` |
| `seq` | `session._position_seq` | `pos.entry_bar`（`_profit_guard_wire` 既有口径） |
| `cost_bps` | `CostRuntime.estimate_cost_for_position(pos_side, bar_s)` | `estimate_round_trip_cost(query_for_preview(side=pos.side, …), backtest_params(settings, expected_hold_bars=rule.expected_hold_bars))`（[P1-2]/[P1-3]：方向按持仓、前瞻窗口按 rule，两侧同源） |

插入位置与实盘同序：`apply_ma_band_gate` 之后、`apply_profit_cooldown` 之前
（`backtest.py:530` 附近），四元 `gate_reason` 合成扩为五元
`base_reason or horizon_reason or cooldown_reason or brake_reason or consensus_reason or edge_reason`。

回测另有运维总闸 `ai_trade_horizon_backtest_enabled`（默认 True），关闭即回测侧不装配
（沿用 `ai_trade_profit_guard_backtest_enabled` 的先例）。

**必须写进回测结果 notes 的差异免责**（沿用 §2.3.5 保守近似免责的先例）：回测按根推进，
持仓时长的分辨率就是一根 K 线，故 `min_hold_s` 在回测里的实际生效粒度是
`ceil(min_hold_s / bar_seconds)` 根；1H 执行 + 2h 最短窗口 ⇒ 精确，5m 执行 + 3min 窗口 ⇒ 恒不生效。
UI 必须显示这句话，不能让用户以为两侧等价。**[P2-7] 免责文案必须点名具体模式**——
`hourly_swing`（5m 执行 + 180s 窗口）在回测里 G1 恒不生效，notes 与 UI 都要直写
「本模式的最短持仓窗口小于一根 K 线，回测中不生效；回测结论只覆盖 G2/G3」，
否则用户仍会拿这份回测去为实盘的 G1 背书。同理 `confirm_window_s` 小于一根 K 线时 G2 的墙钟上界在
回测里恒立即到期，也须同文列出。

### 2.11 前端交互设计

**设计目标**：这是一个「一次选择、全局生效」的模式开关，必须让用户在**不展开任何高级参数**的情况下
就能理解「我选的这个模式到底会让系统做什么」。

#### 位置与形态

新组件 `apps/web/components/Agent/AiTradeHorizonFields.tsx`，挂在启动弹窗**最顶部**——在「时间级别」
之上、在【基础策略】铁律之上。位置即优先级声明：它决定了下面几乎所有字段的合理取值。
（抽独立组件的理由与 `ProfitGuardFields.tsx` 完全相同：`AiTradeLaunchModal.tsx` 已 1272 行。）

形态：**4 + 1 张卡片的单选组**（`role="radiogroup"`，卡片本身是 `<button role="radio">`，
支持方向键切换，`aria-checked` 正确反映状态）。每张卡片三行：

```
┌─────────────────────────┐ ┌─────────────────────────┐
│ ● 小时波段               │ │ ○ 日内波段        推荐   │
│   目标持仓 6 分钟–2 小时  │ │   目标持仓 4–12 小时     │
│   5m · 唤醒 3min · ≤5×   │ │   1H · 唤醒 30min · ≤3× │
└─────────────────────────┘ └─────────────────────────┘
┌─────────────────────────┐ ┌─────────────────────────┐
│ ○ 短波段                 │ │ ○ 趋势持仓               │
│   目标持仓 12–48 小时     │ │   目标持仓 1–3 天        │
│   4H · 唤醒 120min · ≤2× │ │   4H · 仅K线驱动 · ≤2×   │
└─────────────────────────┘ └─────────────────────────┘
┌───────────────────────────────────────────────────┐
│ ○ 自定义       （展开后可逐项填写三道闸的参数）        │
└───────────────────────────────────────────────────┘
```

选中卡片下方是一段**契约摘要**（这是本设计最重要的一块信息，必须默认可见、不藏在 details 里）：

```
本模式下系统将强制执行：
  · 开仓后 2 小时内不接受 AI 主动平仓（交易所止损/止盈、利润保护层、会话止损不受本层限制）
  · 离场理由需连续 2 轮成立才执行，最多等待 60 分钟（到时即按你的意愿放行）
  · 获利了结需浮盈覆盖 1.5× 往返成本（当前费率约 13.0bps ⇒ 需 ≥19.5bps）
  · 浮亏达 −7.5%（保证金，按你当前的止损 5% × 杠杆 3× 估算）时以上限制全部解除
```

第三行的 `13.0bps` 取自后端 `GET /api/ai-trade/horizon-profiles` 同批返回的
`estimatedCostBps`（按当前配置常数算，非实时盘口）——**必须标注「约」并附 title 说明它是估算**。

第二行的等待上限即 `confirm_window_s`（§2.2 派生，[P0-3]），随所选执行周期变化，**必须现算**。

**[P1-10] 第四行必须按当前表单值现算，不得写死算例**：解除阈 =
`(stopLossPct ?? 服务端默认 5%) × leverage × lossReleaseFrac`，三个输入全部在同一张表单里、用户随时会改。
v1 的文案是拿 `5% × 5× × 0.5` 算出的 −12.5%，而 `intraday_swing` 的建议杠杆上限是 3×——
用户读到的会是一个和自己配置无关的数字。规则：
- 用户显式把 `stopLossPct` 填成 0（无交易所止损）⇒ 该行改述为
  「浮亏达 −10%（保证金，兜底口径：本会话未挂交易所止损）」，数值取 `lossReleaseReturnPctFallback`；
- `leverage` 或 `stopLossPct` 变化 ⇒ 该行**同步重算**（与 §2.11 一致性提示同一批 memo 依赖）；
- 这一行是用户判断「这把锁安不安全」的唯一依据，任何静态兜底文案都视为缺陷。

#### 联动规则（防「输入被冲回默认」复发）

本仓库有一个已知的复发性缺陷：`AiTradeLaunchModal` 的重置 effect 依赖不稳定 ⇒ 输入约 1 秒后被冲回
（`ai-trade-modal-revert-recurrence`）。因此本特性的所有新状态**必须且只能**写进既有那一个
`useEffect(..., [open])` 里，**严禁**新增第二个 reset effect。这是硬验收条件（§6.3 G-7）。

联动的精确语义（三条，缺一即产生「用户改了又被改回去」的错觉）：

1. **弹窗打开时**：从 `aiTradeConfig.tradingHorizon` 恢复上次选择（含 profile 与自定义覆盖）；
   `bar` / `wakeIntervalMinutes` 仍按既有逻辑恢复（`propBar` / 记忆值），**不被 profile 覆盖**。
2. **用户主动切换 profile 时**：`setBar(profile.bar)`、`setWakeIntervalRaw(String(profile.wakeIntervalMinutes))`
   同步联动一次（这正是「模式」的意义）。切到 `custom` 时不联动任何字段。
   > **[P0-1] 严禁联动 `profitGuard.minHoldSeconds`（v1 曾要求提到 `min(profile.minHoldSeconds, 3600)`，
   > 本轮删除）**：那个字段的语义是**利润保护层**的噪音抑制窗口，默认仅 30 秒
   > （`ai_trade_profit_guard_min_hold_s`，`config.py:279`），而 `should_protect` 在
   > `holdSeconds < min_hold_s` 时**直接 `return None`**（`profit_guard.py:748-749`）——整层保护在该窗口内
   > 完全停摆。把它抬到 3600 秒，等于让 `intraday_swing` 出现「开仓后 1 小时内：G1 禁止 Agent 平仓
   > **且** 利润保护层不工作」的双关窗口，此时唯一兜底只剩交易所 OCO；而同一屏的契约摘要还在向用户
   > 承诺「利润保护层不受限制」。两层各自的最短持有期解决的是**两个不同问题**（本层：换手成本；
   > 保护层：开仓瞬间的一跳噪音），没有任何理由让前者去改后者。契约摘要须显式点明二者独立。
3. **用户此后手改 bar / 唤醒**：不回写 profile、不弹窗警告，只在下方显示一条**非阻断**的一致性提示 +
   一个「按模式对齐」按钮：
   - `BAR_SECONDS[bar] × 2 > targetHoldMinSeconds` ⇒ 「执行周期偏粗：目标持仓下界不足 2 根 K 线」
   - `wakeMinutes × 60 > minHoldSeconds` ⇒ 「唤醒周期长于最短持仓窗口，该窗口几乎不会被触发」
   - `wakeMinutes > 0 && wakeMinutes × 60 < BAR_SECONDS[bar] / 10` ⇒ 「唤醒远快于执行周期，
     每根 K 线会被问 10 次以上，LLM 成本与换手压力都会显著上升」
   - `leverage > profile.leverageMaxHint` ⇒ 「杠杆高于本模式建议上限 N×，手续费对预算的侵蚀会成比例放大」
     （**只提示不阻断**：杠杆是用户的资金决策，静默夹紧比不夹更危险）

#### 运行期呈现（三个落点，缺一即产生死数据）

- **`AiTradePanel`（左侧标的面板）**：成员行新增一枚 chip
  「日内波段 · 已持仓 1h32m / 最短 2h」，持仓中显示倒计时，`released` 时显示
  「限制已解除（浮亏）」并用 `--short` 色，空仓时显示「日内波段 · 待入场」。
- **`AiTradeTraceRound`（右侧执行轨迹）**：新增 `horizon` 段（与既有 `base_rule` / `edge_gate` /
  `sizing` 段同款渲染），每轮一条，`status` 为 `pass` / `veto` / `released` / `idle`，
  被否轮显示中文理由（如「离场需连续确认 2 轮（当前 1/2）」）。
- **统计看板（`AiTradeStatsLive` / `AiTradeStatsDetail`）**：新增「持仓周期」三格 KPI——
  **平均持仓时长**、**日均换手次数**、**目标区间内占比**，以及一行
  「本层拦截：最短窗口 3 / 未确认 5 / 迟滞 2 / 反手 1」。

「每一个后端算出来的数都必须有展示落点」是本仓库的成文教训
（`wake-scheduler-closed-loop`：后端算了 + 类型补了 ≠ 用户看得见 ⇒ 死数据）。§6.3 的 G-9 逐项核对。

### 2.12 可观测性与验收指标

`metrics_json.aiStats.horizon`（`HorizonRuntime.merge_into`，经既有 `merge_cost_stats` 的键冲突守卫合并）：

```json
{
  "profileId": "intraday_swing",
  "minHoldSeconds": 7200, "exitConfirmRounds": 2, "turnoverHysteresisK": 1.5,
  "expectedHoldBars": 12,
  "minHoldVetoes": 3, "unconfirmedVetoes": 5, "hysteresisVetoes": 2, "reverseVetoes": 1,
  "releasedRounds": 4, "releaseReasons": {"loss_release": 3, "stale_px": 1},
  "confirmWindowReleases": 2,
  "roundTrips": 6,
  "avgHoldSeconds": 9120.0, "medianHoldSeconds": 8400.0,
  "minHoldObservedSeconds": 3200.0, "maxHoldObservedSeconds": 20100.0,
  "withinTargetRate": 0.6667,
  "turnoverPerDay": 1.8,
  "estimatedEpochs": 0
}
```

**[P2-3] 零样本口径**：`roundTrips == 0` 时，`avgHoldSeconds` / `medianHoldSeconds` /
`minHoldObservedSeconds` / `maxHoldObservedSeconds` / `withinTargetRate` / `turnoverPerDay`
一律下发 `null` 而**不是** `0`——「目标区间内占比 0%」与「还没有样本」在看板上是两个截然不同的结论，
用 0 表示后者会把一个正常运行的会话读成彻底失效。前端对 `null` 渲染为「—」。

**[P2-4] 展示归宿**：`estimatedEpochs` 与 `releaseReasons` 明细必须在 §2.11 的统计看板落地
（前者进「持仓周期」KPI 组的脚注「其中 N 个持仓周期起点为估算值，未计入均值」，后者进拦截计数行的
 title 悬浮），否则按 G-9 的规则应当直接删键——后端算出来而界面看不见的数，本仓库一律视为死数据。

调查要求的五项验收指标与本表的对应关系：

| 调查要求 | 数据来源 |
|---|---|
| 平均持仓时间 | `aiStats.horizon.avgHoldSeconds` / `medianHoldSeconds` |
| 每日换手次数 | `aiStats.horizon.turnoverPerDay` |
| 手续费占毛利润比例 | 既有 `aiStats.cost`（`cost-truth-and-net-pnl` 轴 B 的归因账本），**本特性不重算** |
| 资金费后净收益 | 既有 `metrics.totalReturn`（`ai_trade_net_pnl_enabled=true` 时为净额口径）+ `aiStats.cost` |
| 不同退出来源 | 既有 `trade.explain.kind` / `aiStats.profitGuardExits` + 本表四个 veto 计数 |

---

## 三、文件 / 模块变更计划

### 3.1 后端 · 新建

| 文件 | 预估行数 | 一句话意图 |
|---|---|---|
| `services/quant/app/ai_auto/hold_horizon.py` | ~300 | 纯核心：预设表 `PROFILES`、`HorizonRule`、`resolve_horizon_rule`（唯一解析点）、`rule_wire`、`HorizonFacts`、`build_snapshot`、`evaluate_horizon_gate`、`gated_kind`、`release_reason_for`、`gate_status` |
| `services/quant/app/ai_auto/hold_horizon_text.py` | ~170 | 纯渲染：`format_charter`（系统 prompt 宪章章）、`format_horizon_for_prompt`（每轮只读段）、`format_reflection_note`、中文标签映射；只读快照、不重算任何数 |
| `services/quant/app/ai_auto/horizon_runtime.py` | ~230 | 会话运行时：epoch/时钟/连续确认计数/veto 计数/持仓时长样本、`refresh`/`gate`/`note_intent`/`inject`/`note_open`/`note_close`/`merge_into` |

### 3.2 后端 · 修改

| 文件 | 一句话意图 |
|---|---|
| `services/quant/app/config.py` | 新增 9 个 `ai_trade_horizon_*` 配置（§5.1），全部带值域约束 |
| `services/quant/app/schemas.py` | 新增 `AiTradeHorizonIn` / `AiTradeHorizonProfileOut` / `AiTradeHorizonProfilesOut`；`AiTradeGroupStartIn`、`AiBacktestStartIn` 加 `tradingHorizon`；`AiTradeGroupRow` 加 `tradingHorizon` 回执镜像；`AiTradePriorRebuildIn` 加可选 `horizonBars` |
| `services/quant/app/api/ai_auto.py` | 新增 `_resolve_horizon`（默认开启语义的唯一解析点，镜像 `_resolve_base_rules`）+ `_resolve_backtest_horizon`；新增 `GET /ai-trade/horizon-profiles`；两处 start 装配；`priors/rebuild` 透传 `horizonBars` |
| `services/quant/app/ai_auto/manager.py` | `AiTradeGroupStartRequest.horizon_rule` / `AiTradeGroup.horizon_rule`（默认 None，零回归）；`_build_member` 把 `horizon_text` 传进系统 prompt、把 rule 传进 `AiTradeSession` 与 `CostRuntime`/`PriorRuntime` 的 `expected_hold_bars`；`_create_group_run` config 落 `tradingHorizon`；`group_status` 回显；**[P1-5] `_resolve_trade_risk`（`manager.py:657-668`，现只取 config 默认）加可选形参：`enforce_max_hold=True` 时 `max_hold_bars = rule.expected_hold_bars`，否则逐字不变**——不加这一处，`enforceMaxHold` 是个没有实现落点的死字段 |
| `services/quant/app/ai_auto/session.py` | 构造 `HorizonRuntime`；新增薄封装 `_apply_horizon_gate` / `_emit_horizon_trace` / `_horizon_facts`；`_run_decision_round` 插入 `note_intent` + 门禁；`_build_decision_payload` 注入；`_monitor_tick` 调 `refresh`；`_dispatch` 调 `note_open`/`note_close`；`_status_payload` + `_finalize_run` 合并统计。**净增控制在 ~55 行以内**（逻辑一律在 runtime） |
| `services/quant/app/ai_auto/cost_model.py` | `resolve_cost_params` 新增 keyword-only `expected_hold_bars: int \| None = None`；**[P1-3] `backtest_params` 同款开口**（它现在同样硬取全局常数，`cost_model.py:105`） |
| `services/quant/app/ai_auto/cost_runtime.py` | 构造参数 `expected_hold_bars`；`_cost_params()` 三分支透传；**[P1-2] 新增薄入口 `estimate_cost_for_position(side, bar_seconds)`**（按**持仓方向**建查询；`estimate_cost_only` 的签名与既有调用点 `session.py:990` 一字不改） |
| `services/quant/app/ai_auto/cost_text.py` / `cost_view.py` | 成本段增加 `fundingPeriods` / `fundingIntervalS` 只读键与文案（§2.7） |
| `services/quant/app/ai_auto/signal_prior.py` | `resolve_prior_params` 新增 keyword-only `horizon_bars` |
| `services/quant/app/ai_auto/prior_runtime.py` / `prior_service.py` | 透传 `horizon_bars` 覆盖 |
| `services/quant/app/ai_auto/prompts.py` | `build_ai_trade_system_prompt` 新增末位 `horizon_text: str = ""`（插在 `cost_text` 之后，五段宪章）；`build_decision_prompt` 渲染 `tradingHorizon` 段（键缺失即整段省略）；`_format_profit_guard` 透传 `horizonArmed`；`build_reflection_system_prompt(horizon_text="")` |
| `services/quant/app/ai_auto/profit_guard_text.py` | `format_profit_guard_charter(rule, *, horizon_armed=False)`、`_format_last_exit(..., horizon_armed)`、`format_profit_guard_for_prompt(snapshot)` 读 `snapshot["horizonArmed"]`；默认分支逐字节不变 |
| `services/quant/app/ai_auto/decider.py` | `reflect()` 处传 `horizon_text`（从 payload 取，零额外管线） |
| `services/quant/app/ai_auto/trace.py` | `horizon_event(round_no, ts, wire)` + `apply_horizon_trace(trace, wire, reason)`（镜像 `base_rule_event` / `apply_edge_gate_trace`） |
| `services/quant/app/ai_auto/backtest.py` | `AiBacktestSpec.horizon_rule`（默认 None）；`_apply_horizon_gate` 同位插入；bar-ts 时钟断面；计数进 `_aggregate_stats`；notes 追加分辨率免责 |
| `services/quant/app/ai_auto/backtest_manager.py` | `AiBacktestStartRequest.horizon_rule` 透传 |

### 3.3 前端 · 新建

| 文件 | 预估行数 | 一句话意图 |
|---|---|---|
| `apps/web/lib/aiTradeHorizon.ts` | ~180 | 类型 + `validateHorizon` + `describeHorizonStatus` / `describeHorizonGate` / `describeHorizonRelease` + 一致性提示 `horizonCoherenceHints`（**不含任何预设数值**，数值一律来自接口） |
| `apps/web/lib/aiTradeHorizonApi.ts` | ~25 | `GET /api/ai-trade/horizon-profiles` 客户端 |
| `apps/web/components/Agent/AiTradeHorizonFields.tsx` | ~260 | 模式卡片单选组 + 契约摘要 + 自定义高级参数 + 一致性提示与「按模式对齐」按钮 |
| `apps/web/lib/aiTradeHorizon.test.ts` / `AiTradeHorizonFields.test.tsx` | ~300 | 见 §6.2 |

### 3.4 前端 · 修改

| 文件 | 一句话意图 |
|---|---|
| `apps/web/lib/aiTradeTypes.ts` | `AiTradeHorizonProfileId` / `AiTradeHorizonRule` / `AiTradeHorizonSnapshot` / `AiTradeHorizonProfile`；`AiTradeGroupStartReq`、`AiTradeGroupRow`、`AiTradeGroupState`、`AiTradeConfig`、`AiTradeDecision`、`AiTradeRoundTrace` 加对应可选键；`AiTradeTracePhase` 加 `"horizon"` |
| `apps/web/components/Agent/AiTradeLaunchModal.tsx` | 挂载 `AiTradeHorizonFields`；新状态并入**既有唯一** `[open]` reset effect；profile 切换联动 bar/wake/minHold；`handleConfirm` 带 `tradingHorizon`（实盘与回测同源）；`firstError` 并入 `horizonError` |
| `apps/web/components/Agent/AiTradeToggle.tsx` | start 请求透传 `tradingHorizon`；`setAiTradeConfig` 从 `opts` 记忆偏好（**取 opts 不取 res**，`[R-4]` 先例） |
| `apps/web/lib/store.ts` | **三处，缺一即静默失效（[P1-4]）**：(1) `applyAiTradeTracePhase` 增 `case "horizon"` 分支 + 轨迹初值里的 `horizonStatus` / `horizonReason` / `horizonWire` 三键——该 reducer 是**逐键手抄**装配（`store.ts:154-171`、`241-261`，`edge_gate` 分支里还留着「不抄这三键界面永远显示旧文案」的注释），且未知相位是**静默忽略**不报错（`aiTradeEdgeGate.test.ts:148` 已固化该行为）；(2) `AiTradeSessionRow` 加 `horizon` 键——`patchAiTradeMemberStatus`（`store.ts:886-895`）把 status 整体浅合并进 `m.session`，类型里没有这个键则面板取不到；(3) `aiTradeConfig.tradingHorizon` 进 partialize（沿用 `baseRules` 先例，**不改 persist version**） |
| `apps/web/lib/useAiTradeGroupStream.ts` | 从 `row.tradingHorizon` 映射进分组态；成员 `horizon` 快照由 status/decision 事件整对象透传 |
| `apps/web/components/Chart/AiTradePanel.tsx` | 成员行持仓周期 chip（§2.11） |
| `apps/web/components/Agent/AiTradeTraceRound.tsx` | `horizon` 段渲染 |
| `apps/web/components/Agent/aiTradeStats/AiTradeStatsLive.tsx` / `AiTradeStatsDetail.tsx` | 三格 KPI + 拦截计数行 |
| `apps/web/lib/aiTrade.ts` | `DEGRADE_REASON_LABELS` 增三条前缀（`horizon_min_hold` / `horizon_exit_unconfirmed` / `horizon_turnover_hysteresis`） |

---

## 四、接口设计

### 4.1 `GET /api/ai-trade/horizon-profiles`（新增，只读，不守门）

不守 AI 总闸：读预设表不下任何单，弹窗在总闸关闭时也要能把「有哪些模式」画出来
（沿用 `priors/rebuild` 不守总闸的先例理由）。

```jsonc
{
  "enabled": true,                       // ai_trade_horizon_enabled
  "defaultProfile": "intraday_swing",
  "estimatedCostBps": 13.0,              // 按当前配置常数估的往返成本（非实时盘口，UI 须标「约」）
  "profiles": [
    {
      "id": "intraday_swing",
      "title": "日内波段",
      "targetLabel": "4 – 12 小时",
      "description": "在小时级别上捕捉一段趋势，日内完成一到两次往返。",
      "bar": "1H",
      "wakeIntervalMinutes": 30,
      "minHoldSeconds": 7200,
      "exitConfirmRounds": 2,
      "turnoverHysteresisK": 1.5,
      "targetHoldMinSeconds": 14400,
      "targetHoldMaxSeconds": 43200,
      "expectedHoldBars": 12,            // 按本条 bar 派生的预览值；实际按用户所选 bar 现算
      "confirmWindowSeconds": 3600,      // [P0-3] G2 墙钟上界的预览值；同样按用户所选 bar 现算
      "leverageMaxHint": 3.0
    },
    {
      // [P1-8] `custom` 条目：除 id/title/description 外**全部为 null**。v1 的示例形状把这些字段
      // 写成必填非空，而 §2.2 表里 custom 这些格子是「不联动 / 用户填」——按 v1 形状实现，前端渲染
      // custom 卡片时会拿到一组无意义数字并据此联动 bar 与唤醒周期。
      "id": "custom", "title": "自定义",
      "targetLabel": null, "description": "自行设定目标持仓与三道闸参数；全部留空即不施加任何约束。",
      "bar": null, "wakeIntervalMinutes": null,
      "minHoldSeconds": null, "exitConfirmRounds": null, "turnoverHysteresisK": null,
      "targetHoldMinSeconds": null, "targetHoldMaxSeconds": null,
      "expectedHoldBars": null, "confirmWindowSeconds": null, "leverageMaxHint": null
    }
    // … hourly_swing / short_swing / trend_hold
  ]
}
```

`AiTradeHorizonProfileOut` 的字段类型据此声明：`id` / `title` / `description` 非空，
其余**全部 `| None`**。前端 `AiTradeHorizonFields` 对 `bar === null` 的条目不执行任何联动，
契约摘要退化为「本模式下的约束由你自己设定」。

### 4.2 `POST /api/ai-trade/start`（加法字段）

```jsonc
{
  "members": [...], "bar": "1H", "strategyIds": [...],
  "tradingHorizon": {                    // 字段整体缺省 ⇒ 服务端按 settings 装配默认模式（§9 D-2）
    "enabled": true,                     // false ⇒ 该会话不装配（用户显式关闭的唯一途径）
    "profile": "intraday_swing",
    "minHoldSeconds": null,              // 以下 5 项：null ⇒ 取 profile 预设；显式值 ⇒ 覆盖
    "exitConfirmRounds": null,
    "turnoverHysteresisK": null,
    // [P1-9] 这两项**不是纯展示字段**：`targetHoldMaxSeconds` 是 `expectedHoldBars` 的唯一派生源
    // （§2.2），覆盖它会连带改写成本模型的资金费期数与信号先验的前瞻窗口；`targetHoldMinSeconds`
    // 同时是 `confirmWindowSeconds` 的派生源（§2.2 [P0-3]）。v1 写的「其余模式给了也只影响文案与
    // withinTargetRate」是错的，会让运维以为这是安全旋钮。
    "targetHoldMinSeconds": null,        // 影响：withinTargetRate 文案 + confirmWindowSeconds
    "targetHoldMaxSeconds": null,        // 影响：withinTargetRate 文案 + expectedHoldBars（成本/先验窗口）
    "enforceMaxHold": false              // true ⇒ 把 executor 的 max_hold_bars 设为 expectedHoldBars（默认关）
  }
}
```

`enforceMaxHold` 的落点是 `manager._resolve_trade_risk`（§3.2 [P1-5]）。开启后它与
§2.3.3 的 `max_hold_bars` 豁免构成闭环：executor 在 `bars_in_position >= max_hold_bars` 时会强制平仓
（`live_auto/executor.py:244`），本层在**同一判据**上整体解除，故绝不会出现「系统要强平、本层却拦着
Agent 先走一步」的对冲态。关闭时（默认）`max_hold_bars` 仍取 `ai_trade_default_max_hold_bars`（默认 0
= 禁用），该豁免恒不命中——行为逐字回退。

校验（schema 层 422，服务端只做缺省回填，不复夹——沿用 `_resolve_position_step` 的 `[R-2]` 惯例）：

| 字段 | 约束 |
|---|---|
| `profile` | `Literal["hourly_swing","intraday_swing","short_swing","trend_hold","custom"]` |
| `minHoldSeconds` | `ge=0, le=604800`（7 天硬顶） |
| `exitConfirmRounds` | `ge=1, le=10` |
| `turnoverHysteresisK` | `ge=0, le=10` |
| `targetHoldMinSeconds` / `targetHoldMaxSeconds` | `gt=0, le=2592000`（30 天）；`model_validator` 校验 `min <= max` |

回执（`AiTradeGroupRow.tradingHorizon`）为**服务端解析后的生效值**镜像（`rule_wire` 产物），
使「弹窗预览 == 落地」可被逐字段核对。回执**必须**同时带上两个派生量
`expectedHoldBars` 与 `confirmWindowSeconds`（[P1-9]/[P0-3]）——它们是覆盖 `targetHold*` 之后
唯一能看出「我到底改了什么」的字段，只回显入参等于把派生关系藏起来。

### 4.3 `POST /api/ai-trade/backtest/start`（加法字段）

同名 `tradingHorizon`，走 `_resolve_backtest_horizon`（在实盘解析之上再叠
`ai_trade_horizon_backtest_enabled` 一层运维总闸）。**实盘与回测必须同一解析点**，
否则用户拿回测调出来的参数在实盘失真（`base_rules` P1-4 / `profit_guard` I-3 的成文教训）。

### 4.4 WebSocket 事件（全部为加法键，旧前端忽略即可）

- `decision` 事件新增 `horizon`：本轮 `hold_horizon.build_snapshot` 的 wire（无持仓 / 未装配 ⇒ 缺席）。
- 新增 `agent_trace` 的 `phase="horizon"` 段（装配的会话每轮一条，**含放行轮**——静默的保守会被
  用户当成故障，这是 `base_rule_event` / `edge_gate_event` 已确立的理由）。
- `status` 事件的 `data` 新增 `horizon`（同一份快照，供面板 chip 在无决策事件时也能更新）。
- 连续 `released` 告警：连续 20 轮 `released` 追发一条
  `{"type":"error","code":"AI_TRADE_HORIZON_RELEASED","message":"持仓周期约束已连续 N 轮解除（原因：…），期间主动平仓一律放行"}`
  （阈值与理由镜像 `_EDGE_UNAVAILABLE_ALERT_ROUNDS=20`：本层 fail-open，故阈值比铁律层宽）。

### 4.5 先验接口（§2.6）

- `POST /api/ai-trade/priors/rebuild`：body 新增 `horizonBars: int | None = Field(None, ge=1, le=1000)`。
- `GET /api/ai-trade/priors`：新增可选 query `horizonBars`，`contract.horizonMatch` 按它比对
  （缺省仍比全局常数，行为逐字不变）。

---

## 五、数据模型

### 5.1 配置（`services/quant/app/config.py`，全部新增）

```python
# ---- 交易周期模式（ai-trade-holding-horizon-modes §2.2/§2.3）----
ai_trade_horizon_enabled: bool = True             # 总闸；False ⇒ 恒不装配、渲染与 payload 逐字回退
ai_trade_horizon_default_profile: Literal[        # 字段缺省时装配的模式（Literal 而非 str：
    "hourly_swing", "intraday_swing",             # 写错一个字母就该在进程启动时炸，而不是在
    "short_swing", "trend_hold", "custom",        # 第一次 start 时 KeyError）
] = "intraday_swing"
ai_trade_horizon_backtest_enabled: bool = True    # 回测侧运维总闸（一键回到不装配的回测行为）
ai_trade_horizon_gate_reverse: bool = True        # 是否把「反手」也纳入约束（退出口）
ai_trade_horizon_loss_release_frac: float = Field(default=0.5, ge=0, le=1)
ai_trade_horizon_loss_release_return_pct: float = Field(default=0.10, gt=0, le=5)  # 无 OCO 止损时的兜底解除阈
ai_trade_horizon_note_after_rounds: int = Field(default=3, ge=0)   # 连续被本层拦截 N 轮后注入自察注记（0=关；消费方见 §2.9(e)）
```

**[P1-6] v1 的 `ai_trade_horizon_max_min_hold_s` 与 `ai_trade_horizon_max_confirm_rounds` 已删除**：
§4.2 明确规定「schema 层 422，服务端只做缺省回填、**不复夹**」（沿用 `_resolve_position_step` 的
`[R-2]` 惯例），而 Pydantic 的字段约束是类定义期常量、读不到 `Settings` 实例 ⇒ 这两个字段永远不会有
消费方。值域的唯一真源留在 §4.2 的 schema 约束表（`le=604800` / `le=10`）。
「没有展示落点／没有消费方的常量即死码」是本仓库的成文教训，不能因为「留着以防万一」就带进去。

`ai_trade_horizon_note_after_rounds` 的消费方在 §2.9(e)（`blockedStreakNote`）——v1 只声明了这个配置
却没有任何一节定义它做什么，同属上述问题，本轮补齐机制而非删除（该功能对 G2 的可解释性是必要的）。

判定价年龄阈**刻意不新增字段**，复用 `ai_trade_profit_guard_max_px_age_s`（90s）——
「判定价多老算老」在一个会话里只能有一个答案。

### 5.2 内存态

```python
@dataclass(frozen=True)
class HorizonProfile:          # 预设表条目（模块级常量 PROFILES: dict[str, HorizonProfile]）
    id: str; title: str
    description: str
    target_label: str | None                       # [P1-8] custom 条目除 id/title/description 外全为 None
    bar: str | None; wake_interval_minutes: float | None
    target_hold_min_s: float | None; target_hold_max_s: float | None
    exit_confirm_rounds: int | None; hysteresis_k: float | None
    leverage_max_hint: float | None
    # min_hold_s / expected_hold_bars / confirm_window_s 都不是字段：恒由 §2.2 的三条式子派生
    # （杜绝手抄第二遍；G-10 的正则扫描同时守前端不许硬编码这些数）

@dataclass(frozen=True)
class HorizonRule:             # 会话级不可变配置（随 AiTradeSession 生命周期）
    enabled: bool = True
    profile_id: str = "intraday_swing"
    min_hold_s: float = 0.0
    exit_confirm_rounds: int = 1
    confirm_window_s: float = 0.0        # [P0-3] 0 = 窗口机制关闭（G2 只数轮次；仅当 G2 本就空转时成立）
    hysteresis_k: float = 0.0
    target_hold_min_s: float = 0.0
    target_hold_max_s: float = 0.0
    expected_hold_bars: int | None = None   # [P1-1] None ⇒ 成本/先验逐字回退全局常数（custom 全默认即此值）
    gate_reverse: bool = True
    enforce_max_hold: bool = False
    loss_release_frac: float = 0.5
    loss_release_return_pct_fallback: float = 0.10
```

`HorizonRuntime` 的可变状态：`epoch: tuple[str,int] | None`、`opened_wall: float`、
`opened_wall_estimated: bool`、`exit_confirm_streak: int`、`first_exit_intent_wall: float | None`、
`blocked_streak: int`、四个 veto 计数、`released_rounds: int`、`release_reasons: Counter`、
`hold_samples: list[float]`。

### 5.3 落库（零迁移）

- `strategy_runs.config_json.tradingHorizon` ← `rule_wire(rule)`（未装配存 `None`），
  与既有 `baseRules` / `profitGuard` / `sizing` 三键并列。
- `strategy_runs.metrics_json.aiStats.horizon` ← `HorizonRuntime.merge_into` 产物（§2.12）。
- 历史详情接口 `GET /ai-trade/history/{group_id}` 天然带出上述两者（既有实现直读 json 列，无需改动）。

### 5.4 wire 形状（`build_snapshot` 产物，即 payload / decision / status / trace 共用的唯一真源）

```jsonc
{
  "profileId": "intraday_swing", "title": "日内波段", "targetLabel": "4 – 12 小时",
  "status": "flat" | "holding" | "released",
  "holdSeconds": 5520.0, "barsInPosition": 2,
  "targetHoldMinSeconds": 14400, "targetHoldMaxSeconds": 43200,
  "minHoldSeconds": 7200, "minHoldRemainingSeconds": 1680.0,
  "exitConfirmRounds": 2, "exitConfirmStreak": 0,
  "confirmWindowSeconds": 3600, "confirmWindowRemainingSeconds": 2280.0,  // [P0-3]
  "confirmWindowElapsed": false,
  "turnoverHysteresisK": 1.5,
  "moveBps": 8.2, "costBps": 13.0, "requiredMoveBps": 19.5,
  "returnPct": 0.041, "lossReleaseReturnPct": 0.125,
  "released": false, "releaseReason": null, "releasedGates": [],          // [P0-2] 解了哪几道闸
  "blockedStreak": 0, "blockedStreakNote": null,                          // [P1-6]/§2.9(e)
  "expectedHoldBars": 12, "openedWallEstimated": false, "gateReverse": true,
  "asOfWall": 1754720000.0,                                              // [P1-7] 本快照的采集时刻
  "exchangeStopLossPx": 63120.5, "exchangeTakeProfitPx": 69800.0   // 只读转发，不重算（§2.8）
}
```

`asOfWall` 是双快照契约（§2.4 [P1-7]）的可验证凭据：prompt 段带的是 round-start 快照、
trace 与 decision 事件带的是 gate 快照，二者 `asOfWall` 必不相等且**恒有 prompt < trace**。
G-4 的用例即据此断言，而不是断言两份快照逐字段相等（那在时序上根本不可能）。

---

## 六、测试与验收标准

### 6.1 后端单测（新增文件）

**`services/quant/tests/test_ai_trade_hold_horizon.py`（纯核心，~30 用例）**

| 编号 | 用例 | 断言 |
|---|---|---|
| H-1 | 未装配（`rule is None`）任意动作 | 返回**同一个** decision 对象（`is` 相等），reason `None`，零副作用 |
| H-2 | `custom` 全默认（0/1/0） | 与 H-1 行为逐字段相等（「装配但空转」） |
| H-3 | `hold_s < min_hold_s` 的 `close` | 降级 `hold`，reason 前缀 `horizon_min_hold(` |
| H-4 | `hold_s >= min_hold_s` 且确认满足且迟滞满足 | 原样直通 |
| H-5 | 连续确认 1/2 | veto `horizon_exit_unconfirmed(1/2)`；第二轮同意图 ⇒ 放行 |
| H-6 | 确认序列被 `hold` 打断 | streak 归零，下次 close 重新从 1/2 起算 |
| H-7 | epoch 变化（side 或 seq 变） | streak 归零、`opened_wall` 重置 |
| H-8 | `move_bps = 8.2 < 1.5 × 13.0` | veto `horizon_turnover_hysteresis(` |
| H-9 | `move_bps = -30`（浮亏） | **放行**（迟滞只约束获利了结） |
| H-10 | `cost_bps is None` | 迟滞闸放行（fail-open） |
| H-11 | `return_pct <= -loss_release` | 三闸全解除，`released=true`，`releaseReason="loss_release"`，`releasedGates == ["G1","G2","G3"]` |
| H-12 | `px_age_s > 90` / `stale_feed=True` / `mark_px<=0` | 全部 `released` + `releaseReason="stale_px"` + `releasedGates` 三闸全列 |
| H-13 | `opened_wall_estimated=True` | **只解 G1**：`releasedGates == ["G1"]`，`releaseReason="epoch_estimated"`；同一断面下若 G2 未满足**仍被否**（[P0-2] 的判据，直接钉死 §2.3.3 表与本条的一致性） |
| H-14 | `bars_in_position >= max_hold_bars` | `released`，`releaseReason="max_hold_bars"`，`releasedGates` 三闸全列 |
| H-15 | `open_long` 空仓 / `hold` / `cancel_order` | 恒直通（豁免矩阵冻结） |
| H-16 | `open_short` 且 `projected=="long"`（反手） | 受约束；被否时整轮降级 `hold`（**不产生半反手**） |
| H-17 | `gate_reverse=False` | 反手恒直通 |
| H-18 | `resolve_horizon_rule`：总闸关 / `enabled=false` / 字段缺省 / 逐项覆盖 | 四层语义各一条 |
| H-19 | `expected_hold_bars` 随 bar 变化 | `1H⇒12`、`4H⇒3`、`5m⇒144`（`intraday_swing` 的 43200s） |
| H-20 | `min_hold_s == 0.5 × target_hold_min_s` | 对 4 个预设逐条断言（防手抄漂移） |
| H-21 | 预设表冻结 | `PROFILES` 序列化后与 fixture JSON 逐字节相等；`custom` 条目除 `id`/`title`/`description` 外**全部为 None**（[P1-8]） |
| **H-22** | **[P0-3]** `exit_confirm_rounds=3`，仅两轮意图但 `now - first_exit_intent_wall >= confirm_window_s` | **放行**，`confirmWindowElapsed=true`；reason 为 `None`；再断言 `first_exit_intent_wall` 在 streak 归零时被清空、下一次意图重新起算 |
| **H-23** | **[P0-3]** `confirm_window_s == min(0.25×target_hold_min_s, 4×bar_seconds)` | 对 4 个预设 × 3 个 bar 逐格断言；`target_hold_min_s<=0 ⇒ 0` |
| **H-24** | **[P1-1]** `custom` 全默认 | `rule.expected_hold_bars is None`（**不是** 1）；再断言 `resolve_cost_params(settings, expected_hold_bars=None)` 与 `resolve_cost_params(settings)` 逐字段相等 |
| **H-25** | **[P1-2]** 平**多**仓时的 `cost_bps` | 用同一 `funding_rate>0` 断面对比：按持仓方向建查询 ⇒ 资金费**非 0**；若误按 `query_for_decision`（side 恒 `short`）⇒ 记 0。断言前者，并对空仓方向做对称断言 |
| **H-26** | **[P1-6]** `blocked_streak >= note_after_rounds` | 快照含 `blockedStreakNote`；`note_after_rounds=0` 或未装配 ⇒ **整键缺席**；放行 / 豁免 / epoch 变化后归零且键消失 |

**`test_ai_trade_horizon_runtime.py`（运行时，~18 用例）**：epoch 推进、`note_open` 部分成交不重置、
`note_close` 样本入库、估算 epoch 不入样本、四个 veto 计数、`withinTargetRate` 口径、
`turnoverPerDay` 口径、`merge_into` 键冲突即抛、`inject` 未装配整键缺席。

**`test_ai_trade_horizon_session.py`（会话集成，~12 用例）**：门禁在链中的位置
（用 monkeypatch 记录调用顺序断言 `base_rule → horizon → cooldown`）、
`note_intent` 取第一阶段动作、被否轮**不触发反思 LLM**（反思 mock 的调用次数为 0）、
被否轮 payload 与 trace 事件成对出现、`_dispatch` 双收口、finalize 统计落库。

**`test_ai_trade_horizon_exemptions.py`（安全，~8 用例，P0 级）**：
利润保护平仓 / 会话止损 / executor 时间平仓 / flatten / 外部平仓合成 —— 五条路径**逐条断言**
在 `min_hold_s = 86400` 的会话里依然能平掉仓位。这组用例是本特性的安全底线，
任何一条失败都必须视为阻断级缺陷。

**`test_ai_trade_horizon_api.py`（~10 用例）**：`GET /horizon-profiles` 形状与默认值、
start 的四层解析语义、回执镜像与入参一致、越界 422、`targetMin > targetMax` 422、
总闸关闭时 start 成功但回执 `tradingHorizon = null`。

**`test_ai_trade_horizon_backtest.py`（~10 用例）**：同核心同判定（同一断面下回测与实盘 reason 逐字相等）、
bar-ts 时钟、回测总闸关闭即不装配、notes 含分辨率免责（且 `hourly_swing` 的免责**点名该模式**，[P2-7]）、
计数进 `aiStats`；**新增两条**：`backtest_params(settings, expected_hold_bars=k)` 的资金费期数与实盘同值
（[P1-3]），以及回测断面的 `side` 取 `pos.side`（[P2-2]）。

**prompt 冻结（改既有文件）**：`test_ai_trade_prompts*.py` 增加
「未装配 ⇒ 系统 prompt / 决策 prompt / 反思 prompt 三者与 HEAD 逐字节相等」的 sha256 门禁，
以及「装配 ⇒ 五段宪章顺序为 基础策略 → 利润保护 → 仓位 → 成本 → 持仓周期」的顺序断言。

### 6.2 前端单测

- `aiTradeHorizon.test.ts`：`validateHorizon` 边界与后端 schema 一致；三条一致性提示的触发条件；
  `describeHorizonGate` 对三个 reason 前缀的中文映射；**断言本文件不含任何预设数值常量**
  （用正则扫描源码，防止有人为了省一次请求把表抄回来）。
- `AiTradeHorizonFields.test.tsx`：卡片单选可达性（`role="radiogroup"` / 方向键 / `aria-checked`）；
  切换 profile 触发 bar/wake 联动；切到 custom 不联动；契约摘要四行文案随 profile 变化；
  接口加载中禁用。
- `AiTradeLaunchModal.test.tsx`（改既有）：**G-7 复发回归**——打开弹窗后修改
  「最短持仓窗口」输入并推进 2 秒假时钟，断言输入值未被冲回默认（`ai-trade-modal-revert-recurrence`
  的守门用例，本特性每加一个新输入就必须加一条）；提交体含 `tradingHorizon`；实盘与回测两 tab 同源。
- `AiTradePanel.test.tsx` / `AiTradeTraceRound` / 统计看板：三个展示落点各一条渲染断言
  （含 `released` 态与 `flat` 态——**新增边缘态必须验渲染门控没把它整段吞掉**，
  这是 `ai-trade-profit-protection` 终审 RV-2 的成文教训）。
- **[P1-4] `store.test.ts` / `aiTradeHorizon.test.ts` 新增两条 reducer 守门用例**（这是本轮最容易
  静默失效的一处，必须有专属用例而不是靠渲染用例间接覆盖）：
  (a) 喂一条 `phase="horizon"` 的 `AiTradeTraceEvent` 给 `applyAiTradeTracePhase`，断言产出的
  `AiTradeRoundTrace` 上取得到 `horizonStatus` / `horizonReason` / `horizonWire`——该 reducer 对未知
  相位是**静默忽略**（`aiTradeEdgeGate.test.ts:148` 已固化），漏写 case 不会有任何报错；
  (b) 喂一条带 `horizon` 的 status 事件给 `patchAiTradeMemberStatus`，断言 `member.session.horizon` 非空。
- **[P1-10] `AiTradeHorizonFields.test.tsx` 增一条**：改动 `leverage` 或 `stopLossPct` 后，契约摘要第 4 行
  的解除阈**随之变化**；`stopLossPct = 0` 时改述为兜底口径文案。
- **[P0-1] `AiTradeLaunchModal.test.tsx` 增一条冻结断言**：切换任意 profile 前后，
  `profitGuard.minHoldSeconds` 的表单值**逐字不变**。这条是 P0-1 的回归护栏，删掉它等于把那个双关窗口
  的复发概率交还给下一次迭代。

### 6.3 放行条件（Definition of Done，逐条可核对）

| 编号 | 条件 |
|---|---|
| G-1 | `AI_TRADE_HORIZON_ENABLED=false` 时，系统 prompt / 决策 prompt / 反思 prompt / decision payload 与 HEAD **逐字节相等**（sha256 门禁通过） |
| G-2 | §6.1 `test_ai_trade_horizon_exemptions.py` 五条豁免路径全绿 |
| G-3 | 实盘与回测在同一断面下产出**逐字相等**的 reason 字符串（同核心断言通过） |
| G-4 | **trace / decision 事件里的数与门禁判定同源同值**（用例：篡改 gate 快照某个数，断言轨迹文案随之变化）；prompt 段另守「带 `asOfWall` 且早于本轮 trace 的 ts」。**[P1-7] 不得断言 prompt 段与判定逐字段相等**——二者隔着一次 LLM 调用，那个断言在时序上不可能成立，写下去只会逼实现者去掉一次 refresh |
| G-5 | `session.py` 净增 ≤ 55 行；三个新文件各自 < 800 行；无函数 > 50 行 / 形参 > 5 个 |
| G-6 | 全量 `pytest` 相对 HEAD 干净 worktree 基线**无新增红灯**（基线本身含已知孤儿用例，须先实测对账） |
| G-7 | 弹窗新输入的「1 秒后被冲回默认」回归用例通过；全仓库 `AiTradeLaunchModal.tsx` 内 reset effect 仍**只有一个**且依赖仍为 `[open]` |
| G-8 | `pnpm -F web test` + `tsc --noEmit` 相对基线无新增失败 |
| G-9 | 后端产出的每一个新键都有前端展示落点（逐键核对表随实现提交，空落点即缺陷） |
| G-10 | `GET /ai-trade/horizon-profiles` 的每个数值字段在前端**均无同名硬编码常量**（正则扫描通过） |
| **G-11** | **[P1-4]** `applyAiTradeTracePhase` 的 `case "horizon"` 与 `AiTradeSessionRow.horizon` 均已落地，且各有专属 reducer 用例（§6.2）；核对方式：把后端 `horizon` 轨迹事件的 JSON 直接喂进 reducer，断言键可取——**不接受**「渲染用例绿了就算」 |
| **G-12** | **[P0-1]** 切换 profile 不改动 `profitGuard.minHoldSeconds`（表单冻结断言通过）；且契约摘要中出现的每一个数（解除阈、确认窗口、所需 bps）都能在同屏表单值或接口返回里找到来源，无静态算例残留 |
| **G-13** | **[P0-3]** 对四个预设逐条核算「最坏离场延迟」= `min_hold_s` 与 `confirm_window_s` 的较大者，并写进 runbook；`trend_hold` 的该值必须 ≤ `0.5 × target_hold_min_s`（即 12 小时），否则预设参数须回调 |

### 6.4 上线后的观察窗口（运维 runbook）

1. **L0（不改行为，先看数）**：`AI_TRADE_HORIZON_ENABLED=true` + 选 `custom` 全默认，
   跑 3 个会话，只看 `aiStats.horizon.avgHoldSeconds` / `turnoverPerDay` 与既有基线是否对得上。
2. **L1（按调查建议做净额回测）**：`4H + 唤醒 0 + 2× 杠杆 + trend_hold`，
   样本外区间 ≥ 90 天，验收五项指标（§2.12 表）。
3. **L2（小资金实盘）**：`intraday_swing`，预算取账户 1%（`ai_trade_default_capital_frac` 默认值），
   连续 7 天，要求 `avgHoldSeconds` 落在 `[targetMin, targetMax]` 内的占比 ≥ 50%
   且手续费/毛利润比例相对基线下降。
4. **未通过 L1 之前，不得放大实盘资金**（调查原文要求，写进 runbook 与弹窗提示）。

---

## 七、风险与缓解

| 编号 | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R-1 | **最短持仓窗口把一笔亏损仓位锁死** | 真实资金重大损失 | §2.3.3 四条豁免（`loss_release` 按 OCO 止损派生、`stale_px`、`epoch_estimated`、`max_hold_bars`）+ 五条结构性豁免路径 + `test_ai_trade_horizon_exemptions.py` 作为 P0 门禁 |
| R-2 | **fail-open 极性写反**（照抄 `base_rules` 的 fail-closed） | 断网瞬间无法平仓 | 纯核心文件头写死四条不变式；H-10/H-12/H-13 三条用例专守极性；评审须逐条核对 |
| R-3 | **前后端预设表漂移** | 弹窗预览与落地不一致，用户按错误信息决策 | 数值与中文标签**全部**由 `GET /horizon-profiles` 下发；G-10 用正则扫描禁止前端硬编码；回执镜像生效值供逐字段核对 |
| R-4 | **量纲失配**（迟滞用保证金口径 vs 成本用价格口径） | 5× 杠杆下阈值差 5 倍，门禁形同虚设或全拦 | §2.3.2 明确 `move_bps` 与 `cost_bps` **同为价格口径 bps**，`return_pct`/`lossRelease` **同为保证金收益率**；两组数在 wire 里字段名前缀不同（`Bps` vs `Pct`）；H-8/H-11 分别守 |
| R-5 | **连续确认计数取错阶段**（记门禁后动作） | G2 永远数不到 2，整闸空转 | §2.5 硬约束 4 + H-5/H-6 + 会话集成用例断言调用顺序 |
| R-6 | **先验快照 `horizonBars` 不匹配导致先验整体失效** | 先验层静默退化 | §2.6：默认部署 `ai_trade_signal_prior_enabled=False` 影响为零；rebuild 接口加 `horizonBars`；沿用既有 `AI_TRADE_PRIOR_CONTRACT_MISMATCH` 告警（不新增机制，不静默） |
| R-7 | **prompt 偏置改写引入新的道德风险**（模型以为有兜底而无限持有） | 尾部风险放大 | 只改「主动 close 永远更优」这一半，**保留** fail-open 免责整段；宪章新增一段明写「本层不保证你能被兜底，浮亏解除阈是 X%」；G-4 守文案与判定同源 |
| R-8 | **回测分辨率误导**（5m 执行 + 3min 窗口在回测里恒不生效） | 用户按回测结论上实盘 | §2.10 免责必须进 notes 与 UI；`test_ai_trade_horizon_backtest.py` 断言该文案存在 |
| R-9 | **默认开启改变存量用户行为** | 存量会话行为突变 | §9 D-2 拍板：默认开启但**弹窗每次都显式呈现契约摘要**（不是静默默认）；`enabled:false` 一键关；`AI_TRADE_HORIZON_ENABLED=false` 运维级回退 |
| R-10 | **`session.py` 再次膨胀** | 触碰 800 行红线、后续无处下手 | G-5 硬性行数预算；逻辑一律落 `HorizonRuntime`；会话只留 `_apply_horizon_gate` / `_emit_horizon_trace` / `_horizon_facts` 三个薄方法 |
| R-11 | **与回撤刹车「只平不开」叠加** | 刹车要求收敛敞口、本层却拦 close | 二者本就不冲突（刹车拦 open、本层拦 close），且 `loss_release` 通常先于刹车 halt 触发；文档记明，并在会话集成用例里造一条叠加断面断言不死锁 |
| R-12 | **多标的分组共享同一 rule** | 5 个标的同一套周期 | 有意为之：模式是**会话级契约**而非标的属性，与 `base_rule` / `profit_rule` 的分组共享姿态一致；`prior_snapshots` 那种按 instId 分键的做法不适用（这里没有标的相关的统计量） |
| **R-13** | **本层与利润保护层的最短持有期被当成同一个旋钮** | 出现「G1 拦 Agent + 保护层停摆」的双关窗口，唯一兜底只剩交易所 OCO | [P0-1]：删除弹窗联动；两个 `minHoldSeconds` 在 UI 上分列两处并各自注明适用层；G-12 用表单冻结断言守；契约摘要显式写「保护层不受本层限制」 |
| **R-14** | **离场延迟没有上界**（稀疏轮次 × 连续确认） | 真实持仓在行情源静默期间无法按意愿离场 | [P0-3]：G2 增墙钟上界 `confirm_window_s`；G-13 要求逐预设核算最坏延迟并写进 runbook；`confirmWindowReleases` 计数进 `aiStats` 供事后核对该阀门是否真的在用 |

---

## 八、实施顺序（建议给下游代码开发节点）

1. **纯核心先行**：`hold_horizon.py` + `test_ai_trade_hold_horizon.py`（H-1 ~ H-21 全绿再往下）。
   这一步不碰任何既有文件，可独立验证。
2. **配置 + schema + API**：`config.py` / `schemas.py` / `api/ai_auto.py`（含 `/horizon-profiles`），
   跑 `test_ai_trade_horizon_api.py`。
3. **运行时 + 会话接线**：`horizon_runtime.py` → `manager.py` → `session.py`（严守 G-5 行数预算），
   跑 runtime / session / exemptions 三组用例。
4. **成本与先验前瞻窗口**：`cost_model` / `cost_runtime` / `signal_prior` / `prior_runtime` / `prior_service`
   的可选覆盖 + `cost_text` / `cost_view` 的资金费期数披露。
5. **prompt 三处改写 + trace 段**：先加 sha256 冻结门禁（G-1），再改文案。
6. **回测同构**：`backtest.py` / `backtest_manager.py`。
7. **前端**：`aiTradeHorizon.ts` → `AiTradeHorizonFields.tsx` → 弹窗接线（G-7 回归用例先写）→
   三个展示落点 → 统计看板。
8. **收尾**：G-1 ~ G-13 逐条核对，产出「后端新键 → 前端落点」核对表；
   并实测一次 `AI_TRADE_HORIZON_*` 是否经 desktop `config.js` 的 prod 透传白名单落到打包产物
   （`config.py:182-183` 注释称 `AI_TRADE_` 前缀已覆盖，[P2-8] 要求实测而非假设——
   运维总闸如果在打包版里读不到，G-1 的一键回退就只是文档上的承诺）。
9. **技术债登记（不在本轮做，[P2-5]）**：`session.py` 现为 **1664 行**，已远超 `CLAUDE.md` 的 800 行
   红线；本轮的「净增 ≤55 行」是止血。收尾时在 `docs/plans/` 下登记一条独立债项：把
   `_run_decision_round` 的门禁编排段（现已串了 base_rule / horizon / cooldown / brake / consensus /
   edge 六道）下沉为一个独立的 `gate_chain.py`。

---

## 九、有意偏离与拍板记录

- **D-1（推翻既有 P1-5 的一半）**：`ai-trade-profit-protection` v1 把「被保护层强平 = 一次决策失误」
  与「主动 `close` 永远优先」列为不可删。本轮据线上证据推翻**前半句与整个第二句**，
  保留 fail-open 免责整段。证据：16 次往返中 15 次 Agent 主动退出净 −2.4741 USDT，
  唯一一次保护层退出净 +1.8655 USDT。道德风险改由确定性门禁承担。
- **D-2（默认开启）**：`tradingHorizon` 字段缺省 ⇒ 装配 `ai_trade_horizon_default_profile`
  （`intraday_swing`），与 `baseRules` / `profitGuard` 的「字段缺省 ⇒ 开启」惯例一致。
  理由：本特性若默认关闭，等于把用户明确提出的需求做成一段死码；且弹窗**每次**都把契约摘要
  显式呈现，不存在「用户不知情」的路径。回退口有两级（请求体 `enabled:false` / 运维总闸）。
- **D-3（不联动覆盖用户已选的 bar / 唤醒）**：预设携带建议值，但只在**用户主动切换 profile**
  时联动一次；打开弹窗时恢复记忆值。理由：静默覆盖用户上次的选择，与
  `ai-trade-modal-revert-recurrence` 是同一类「输入被系统改掉」的体验缺陷。
- **D-4（迟滞只约束获利了结）**：调查原文写的是「需要证明继续持有的预期损失大于平仓手续费」，
  这是一个双向表述。本方案**收窄**为只约束 `move_bps > 0` 的平仓。理由：对亏损仓位施加迟滞
  等于延迟止损，与 R-1 直接冲突；而样本里被成本吃掉的恰恰是获利了结那一侧。
- **D-5（`min_hold_s` 取目标下界的一半而非取满）**：硬地板只负责掐掉明显噪音换手，
  完整目标区间交给 prompt 软引导。取满会让「目标 4–12 小时」变成「4 小时内不许动」，
  超出「让用户选周期」的授权范围。
- **D-6（不改 `max_hold_bars` 默认值）**：`enforceMaxHold` 默认 `false`，
  即目标持仓上界只进 prompt、不强制平仓。理由：调查已确认强制时间平仓不是短持仓的成因，
  默认打开会引入一个新的、与本轮目标无关的强制退出源。
- **D-7（评审新增，推翻 v1 §2.11 联动规则 2）**：切换模式**不**改写 `profitGuard.minHoldSeconds`。
  v1 的意图是「模式统管所有周期相关旋钮」，方向对但对象选错了：那个字段控制的是**利润保护层**在
  开仓瞬间的噪音抑制（默认 30 秒，`config.py:279`），而 `should_protect` 在窗口内**整层 return None**
  （`profit_guard.py:748-749`）。把它抬到 3600 秒会造出「G1 拦 Agent + 保护层停摆」的双关窗口——
  这与本层「拦的是主动换手，绝不削弱任何兜底」的唯一不可动摇语义（§1.3）正面冲突。
  两层的最短持有期解决两个不同问题，各自独立。详见 [P0-1] / R-13。
- **D-8（评审新增，G2 从「数轮次」改为「数轮次 + 墙钟窗口」）**：v1 的连续确认只数轮次。
  在 `trend_hold`（唤醒 0 + 4H）这种**刻意稀疏**的轮次节奏下，轮次不再是时间的代理——
  行情源静默时 `_maybe_interval_wake` 直接早退（`session.py:466-468`），一次离场意图可以无限期悬置。
  本轮补 `confirm_window_s`（§2.2 派生式），语义从「必须连续 N 轮」收敛为
  「**在窗口内**必须连续 N 轮，窗口到期即按你的意愿放行」。这不削弱 G2 的本意（滤掉一次分钟级回撤），
  因为窗口下界仍是四根执行周期或目标下界的四分之一。详见 [P0-3] / R-14。
- **D-9（评审新增，删除两个无消费方的配置）**：`ai_trade_horizon_max_min_hold_s` 与
  `ai_trade_horizon_max_confirm_rounds` 删除，值域唯一真源留在 schema 约束（§4.2）。
  理由：§4.2 同时规定「服务端不复夹」，而 Pydantic 字段约束读不到 settings 实例——
  留着它们只会让运维以为改了 env 就能收紧上限。详见 [P1-6]。

## 十、非目标（本轮明确不做）

- 不做「按标的分别设置周期模式」（分组共享一份契约，理由见 R-12）。
- 不做运行中动态改模式（改模式 = 停会话重开；运行中改会让 epoch 与计数语义无解）。
- 不做基于持仓时长的自适应仓位 / 自适应止损（那是 `vol-sizing` 层的职责，不在本轮）。
- 不改 `live_auto`（非 AI）链路的任何行为——注意 `LiveExecutor` 被两条链路共用，
  本特性**不得**改动 `executor.py` 的任何既有分支（这是 `ai-trade-cost-accounting` 评审
  发现的仓库级陷阱：`ai_trade_*` 开关会静默改写非 AI 链路）。
- 不做资金费率的历史回补与真实结算入账（当前只做**预估**进成本模型；真实资金费入账属
  `cost-truth-and-net-pnl` 的后续轴）。

---

## 评审结论

**有条件通过（Approved with conditions）· 文档版本 v2**

方案的问题定位是扎实的：调查数据（16 次往返、15 次 Agent 主动平仓、毛利 +0.58 被 3.06 手续费吃成
−2.47）与代码根因（无最短持仓约束、决策频率与持仓周期脱钩、prompt 抢跑偏置、前瞻窗口是个常数）
一一对得上，而不是从现象直接跳到方案。技术路线选得也对——复用仓库已经付过学费的「铁律层」范式
（纯核心 + 会话运行时 + 宪章 prompt + 实盘回测同源），而不是把持仓纪律做成信号板的第四个成员。
方案依赖的每一个既有构件（`estimate_cost_only`、`BAR_SECONDS`、`_bar_ts`、`_maybe_time_exit`、
决策链插入点）都已按行核实存在且签名吻合，**可行性没有疑问**。

评审共提 **3 个 P0、10 个 P1、8 个 P2**。P0 与 P1 已在正文逐条闭合（每处修改都带 `[P0-x]` / `[P1-x]`
标记，可反查本文顶部的评审记录表）。其中三条值得下游节点特别记住，因为它们都不是笔误，
而是**在纸面上读着完全合理、只有对着代码才会暴露**的设计缺陷：

1. **P0-1**：模式切换去改利润保护层的 `minHoldSeconds`，会造出「Agent 不许平 + 保护层停摆」的双关
   窗口，而同屏 UI 还在承诺「利润保护层不受限制」。跨层共享同名旋钮 ≠ 同一语义。
2. **P1-2**：G3 的成本估算走 `query_for_decision`，其方向由**动作**推导（`close` 恒判为 short），
   叠加资金费只取不利侧的极性，导致平多仓恒记 0 资金费——本轮恰好把前瞻窗口从 8 根拉到 12–72 根，
   把这个原本无害的近似放大成了系统性偏斜。
3. **P1-4**：前端轨迹 reducer 是逐键手抄且对未知相位**静默忽略**，新相位不加 case 不会有任何报错，
   只会安静地什么都不显示。这与仓库里 `edgeSource` 那次是同一个坑。

放行条件（下游代码开发节点必须逐条兑现，任一未落实即视为本轮未完成）：

| # | 条件 | 对应 |
|---|---|---|
| 1 | 弹窗切换 profile **不得**触碰 `profitGuard.minHoldSeconds`，并有表单冻结用例守门 | P0-1 / G-12 |
| 2 | `releasedGates` 逐闸解除矩阵按 §2.3.3 实现，H-13 断言 `["G1"]` 而非全解 | P0-2 |
| 3 | G2 的 `confirm_window_s` 落地，四个预设的「最坏离场延迟」核算结果写进 runbook | P0-3 / G-13 |
| 4 | `custom` 全默认时 `expected_hold_bars is None`（成本与先验逐字回退），H-24 守 | P1-1 |
| 5 | G3 成本查询按**持仓方向**构造，`estimate_cost_only` 既有调用点一字不改，H-25 守 | P1-2 |
| 6 | `backtest_params` 同步开口，G-3 的两侧 reason 逐字相等实测通过 | P1-3 |
| 7 | store.ts 三处（reducer case / 轨迹初值键 / `AiTradeSessionRow.horizon`）落地并有专属 reducer 用例 | P1-4 / G-11 |
| 8 | `_resolve_trade_risk` 承接 `enforceMaxHold`；否则该字段**删除**，不留死字段 | P1-5 |
| 9 | 两个无消费方的配置已删除；`note_after_rounds` 按 §2.9(e) 实现 `blockedStreakNote` | P1-6 |
| 10 | 双快照契约按 §2.4 实现（gate 时刻重新 refresh，`asOfWall` 下发），G-4 按新口径验收 | P1-7 |
| 11 | `custom` 的接口条目字段全为 `null`，前端对 `bar === null` 不联动 | P1-8 |
| 12 | 回执镜像 `expectedHoldBars` 与 `confirmWindowSeconds` 两个派生量 | P1-9 |
| 13 | 契约摘要第 4 行按当前表单值现算，无静态算例残留 | P1-10 |
| 14 | 上线仍按 §6.4 的 L0 → L1 → L2 三级；**未通过 L1 的样本外净额回测前不得放大实盘资金** | 调查原文要求 |

八条 P2 不阻断本轮，但要求在实现阶段一并处理或显式记为欠账（P2-5 的 `session.py` 行数债、
P2-6 的 min_hold 窗口内 LLM 轮次成本，两条必须进 L0 观察窗口的记录项）。

**范围判断**：本轮切得合适，没有过度设计。唯一略微外溢的是 `enforceMaxHold`——它默认关闭、
且已与 `max_hold_bars` 豁免闭环，可以保留；但若实现阶段发现 `_resolve_trade_risk` 的改动会波及
`live_auto`（`LiveExecutor` 为两条链路共用，这是本仓库反复出事的地方），应**直接砍掉该字段**
而不是想办法绕过——它不是本轮要解决的问题的一部分。

---

## 实施过程发现的方案缺陷

下列条目均为**实现阶段对着代码才暴露**的问题。凡涉及行为的，一律按「不静默偏离」的约定在此登记，
并说明采用的修正方案与判据来源。

### I-1（阻断级）G2 的判据式与钉死的调用顺序自相矛盾

§2.3.2 的伪码写的是 `exit_confirm_streak + 1 < rule.exit_confirm_rounds`，它隐含「`streak` 不含本轮」；
而 §2.5 硬约束 4 钉死 `note_intent` 必须在**一切门禁之前**推进计数 ⇒ 门禁读到的 `streak` **已含本轮**。
按字面实现，`rounds=2` 时首轮离场意图会算成 `1+1<2 = False` 而**直接放行**，G2 整闸恰好弱掉一轮，
且 H-5（「连续确认 1/2 被否」）会红。

**修正**：以 H-5 为准，判据实现为 `streak < rounds`（`streak` 含本轮）。另加一条**结构性早退**
`rule.exit_confirm_rounds <= 1 ⇒ 本闸整体空转`——否则 H-2 承诺的「`custom` 全默认逐字等价于未装配」
要依赖「调用方一定先调了 `note_intent`」才成立，任何一处漏调都会把一笔真实持仓的 `close` 锁死。
纯核心不该有这种对调用顺序的隐式依赖。

### I-2 `HorizonFacts` 缺 §5.4 wire 所需的两个字段

§5.4 的 wire 含 `exchangeStopLossPx` / `exchangeTakeProfitPx`（§2.8 第 3 条要求**只读转发**
`profit_guard` 快照、不重算），但 §2.4 的 `HorizonFacts` 字段表里没有它们。已补两个可选字段，值由
`profit_guard_runtime.exchange_oco_px`（既有唯一真源）转发。

### I-3 两个会话级常量落进 `HorizonRule` 而非 `build_snapshot` 形参

`note_after_rounds`（§2.9(e) 自察注记阈）与 `max_px_age_s`（§5.1 明确复用
`ai_trade_profit_guard_max_px_age_s`）都是会话级不变量。给 `build_snapshot` 加形参会突破
CLAUDE.md 的「形参 ≤5」，故随 `HorizonRule` 一起在 `resolve_horizon_rule` 里解析一次。

### I-4 `HorizonRuntime.gate` 的三元返回值不足以渲染轨迹

§2.4 的签名只回 `(decision, degrade, snapshot)`，而 `degrade` 遵循「先执行者胜出」的合成语义：
基础策略先拦一轮时，`degrade` 是基础策略的码，本层的 `reason` 被吞掉。轨迹段若据合成 `degrade` 渲染，
会在一个本层明明放行的轮次上显示「交易周期否决」。已在 runtime 上增加 `last_reason`（本层自己的码，
放行轮为 `None`），轨迹与 `gate_status` 一律读它。

### I-5（阻断级）回测必须显式建 epoch，否则 G1 恒不生效且不报错

§2.10 只给了「`opened_wall` = `self._bar_ts(pos.entry_bar, default=ts)`」这一行对照。若照最自然的写法
让 `refresh()` 走兜底路径建 epoch，`opened_wall_estimated` 会**恒为真** ⇒ `epoch_estimated` 豁免每一轮
命中 ⇒ **G1 在回测里恒不生效，且不报任何错**——回测结论会系统性地比实盘宽松，正是 §2.10 要杜绝的
「用回测调出的参数在实盘失真」。已新增 `AiBacktestRunner._track_horizon_epoch`（逐根：开仓即以开仓根
ts 锁定 `opened_wall`、转 flat 即收一条持仓时长样本），并把 `_bar_ts_index` 的构造条件加上本层
（缺这条索引持仓时长恒为 0，症状与上面完全相同）。回归用例
`test_backtest_epoch_uses_bar_ts_not_estimated` 专守这条。

### I-6 `CostQuery` 需要一个「结算周期是否真的取自交易所」的标记

§2.7 要求「缺 `intervalS` 时如实写『结算周期未知，按 8h 保守估算』——**绝不**写成一个确定的数」。
但 `CostQuery.funding_interval_s` 是带默认值 `28800.0` 的 `float`、永远不为 `None`，照它下发会在一个
**根本不知道**结算周期的会话上报出「当前结算周期 8h」。已加 `funding_interval_known: bool = False`，
只有 `query_for_preview` 拿到真实资金费率快照时才置真；`CostBreakdown.funding_interval_s` 据它下发
`None`，文案分支据 `None` 走「未知」措辞。

### I-7 G-5 行数预算超出：`session.py` 净增 +63 行（预算 ≤55）

判定逻辑已全部下沉 `HorizonRuntime`、断面装配下沉 `build_horizon_facts`、注释逐条压缩之后，剩余增量
全是**接线**：7 行 import、3 行告警常量、9 行构造（含成本 / 先验的前瞻窗口同源透传）、5 行 `_dispatch`
双收口、6 行决策链（`note_intent` + 门禁）、24 行两个薄方法（`_horizon_facts` / `_apply_horizon_gate`）、
9 行三处下发落点（payload / status / decision / finalize）。再压只能删掉解释仓库级陷阱的注释，收益为负。
**记为欠账**，与 P2-5 的「把 `_run_decision_round` 的门禁编排段下沉 `gate_chain.py`」一并处理——该文件
现为 1727 行，已是 CLAUDE.md 红线的 2.2 倍。

同款欠账在前端：`AiTradeLaunchModal.tsx` 由 1272 行涨到 1396 行。§2.11 抽 `AiTradeHorizonFields.tsx`
已经把**渲染**那一半搬走（该组件 381 行），剩下的增量是状态声明 + `[open]` reset 分支 + 联动回调 +
提交体装配 + 两个种子/覆盖 helper——它们**必须**留在父组件（子组件里写第二个 reset effect 正是
`ai-trade-modal-revert-recurrence` 的复发根因）。下一次再给这个弹窗加区块前，应先把「预算 / 杠杆 /
OCO / 会话止盈损」那一段也抽成受控子组件。

### I-8 G-13 最坏离场延迟核算（runbook 条目）

「最坏离场延迟」= `max(min_hold_s, confirm_window_s)`（两者不叠加：确认窗口在最短窗口内也在走）。
按 §2.2 的三条派生式，四个预设在**各自建议周期**下的核算结果：

| 模式 | 建议周期 | `min_hold_s` | `confirm_window_s` | **最坏离场延迟** | 判据 `≤ 0.5 × target_min` |
|---|---|---|---|---|---|
| `hourly_swing` | 5m | 180s（3 分钟） | 90s | **3 分钟** | 3min ≤ 3min ✔ |
| `intraday_swing` | 1H | 7 200s（2 小时） | 3 600s | **2 小时** | 2h ≤ 2h ✔ |
| `short_swing` | 4H | 21 600s（6 小时） | 10 800s | **6 小时** | 6h ≤ 6h ✔ |
| `trend_hold` | 4H | 43 200s（12 小时） | 21 600s | **12 小时** | 12h ≤ 12h ✔ |

G-13 对 `trend_hold` 的要求（≤ 12 小时）**取等号成立**，无需回调预设参数。四行的等号都来自
`min_hold_s = 0.5 × target_hold_min_s` 这条派生式本身——也就是说该判据在当前派生规则下对**任何**
预设恒成立，它约束的其实是「不要绕过派生式去手填一个更大的 `minHoldSeconds`」。

### I-9 运行期没有 `aiStats.horizon`，live 三格 KPI 另有口径

§2.11 要求「持仓周期」三格 KPI 在 `AiTradeStatsLive` 与 `AiTradeStatsDetail` 两处都出现，但
`aiStats.horizon` 只在 `_finalize_run` 落库——运行期根本没有它。已在 live 侧用**成交时间戳配对**的
往返持仓秒数现算（`horizonLiveKpis`），并在卡片上如实写明口径差异（后端样本取 `note_close` 的墙钟差、
未平仓的持仓不计入、会话停止后以「历史」标签的落库值为准）。不写这句就是误导。

### I-10 P2 欠账登记

- **P2-5**（`session.py` 行数债）：见 I-7，本轮止血未还债。
- **P2-6**（min_hold 窗口内每轮仍烧一次决策 LLM）：这是「门禁必须在 `decide` 之后」的必然代价，本轮
  未做优化。§6.4 的 L0 观察窗口须把**每会话 LLM 轮数**一并记下来，供后续判断是否值得在窗口内降频。
- **P2-7 / P2-3 / P2-4** 已在实现中处理（回测免责点名模式、零样本下发 `null`、`estimatedEpochs` 与
  `releaseReasons` 各有展示落点）。

### I-11 红灯基线对账

以干净 HEAD（`cebee54`）worktree 实测：`tests/test_ai_trade_prior_session.py` 的 7 条**在 HEAD 上即为红**
（同一组用例、同一处 `TypeError`），`tests/test_factor_library_expansion.py` 是**未提交的孤儿测试文件**
（HEAD 上不存在），其 7 条同为非回归。本轮全量 `pytest` 结果 `14 failed, 2709 passed`，与该基线**逐条
一致，无新增红灯**（G-6）。前端 `vitest` 1516/1516 全绿；`tsc --noEmit` 仅剩 `WeightBatchArena.test.tsx`
一条既有错误（与本轮无关）。

---

## 终审（合入前评审）发现并已修复

逐节对照 spec v2 与 diff 之后，14 条放行条件全部核对为已落实（含 P0-1 的表单冻结用例、P0-2 的逐闸
解除矩阵、P0-3 的 `confirm_window_s`、P1-2 的按持仓方向估成本、P1-4 的 store 三处）。另发现并**直接
修复**下列四条，均补了专属回归用例：

### RV-1（实盘，静默失效级）`note_open` 不升级估算态 ⇒ G1 可能整段不生效

`refresh()` 在盯市 tick 与决策轮上都会跑，而 `executor.projected` 在成交事件扇出**之前**就已翻面
（两者在不同 asyncio 任务里推进）。于是 `refresh()` 会先兜底建出一个 `opened_wall_estimated=True`
的 epoch；原实现的 `note_open` 遇到「同一 `(side, seq)`」即早退，估算标记便挂满整个持仓期 ⇒
`epoch_estimated` 豁免每轮命中 ⇒ **G1 恒不生效且不报任何错**——正是 I-5 在回测侧逮到的同一形态，只是
换到了实盘。**修正**：只有已由成交事件锁定过（`estimated=False`）的 epoch 才早退，其余一律以成交
时刻改写。守门用例 `test_note_open_upgrades_an_estimated_epoch`。

### RV-2（回测，指标失真级）反手漏收持仓时长样本

`_track_horizon_epoch` 只在 `pos.side == "flat"` 时 `note_close`，而反手在**同一根**里直接换了持仓
身份、中途不经过 flat ⇒ 上一段持仓的时长样本与 `roundTrips` 静默消失。「平均持仓时间 / 每日换手
次数」恰是本特性的两项验收指标（§2.12），而反手又正是本层专门计数的一类换手。**修正**：epoch 身份
变化时先收一笔 `note_close` 再 `note_open`（实盘侧无此问题——`_dispatch` 的 close 与 open 是两条成交
事件，天然成对）。守门用例 `test_track_horizon_epoch_closes_the_previous_epoch_on_reverse`。

### RV-3（[P0-3] 的钥匙在一条可达路径上丢失）自定义模式可造出没有墙钟上界的 G2

`derive_confirm_window_s` 把「目标下界未设」读成 `0`，其隐含前提是「此时 `exit_confirm_rounds` 也是
1、G2 本就空转」（§2.2）。但自定义模式允许「只填确认轮数、不填目标区间」——该前提不成立，于是得到
一道**没有钥匙的锁**：唤醒 0 的会话里一次离场意图可以被无限期悬置，正是 R-14 要根除的形态。
**修正**：新增 `_resolve_confirm_window_s`——派生式为 0 但 G2 真的在生效时，回落到派生式的另一半
`4 × 一根执行周期`（`min(a, b)` 在 a 未定义时理应退化为 b），派生式本身与 H-23 逐字不变；
`custom` 全默认（轮数=1）仍逐字回退 0，安全退出口不受影响。
守门用例 `test_confirm_window_falls_back_to_four_bars_when_no_target_lower_bound`。

### RV-4（呈现）`AiTradeStatsLive` 的运行期口径提示把 Markdown 星号渲染成了字面量

JSX 文本里的 `**已完成往返**` 会原样显示两对星号。改为 `<strong>`（与本仓库其余强调文案同款）。

### 终审复核但**未改**的两处（记录判据，避免下次重复讨论）

- **§2.8 防线 1（跨日模式必须有交易所侧止损）不需要在弹窗另写一条阻断校验**：`parseOptionalPct`
  对「显式填 0」本就返回 error 并并入 `inputError`（`0` 解析不出 value），该输入根本到不了周期层；
  留空则走服务端默认 5%，契约摘要按它现算解除阈并写明来源。再加一条判据只会是永不触发的死码，
  与本仓库「没有落点的分支即死码」的成文纪律冲突。
- **G-5 行数预算超支（`session.py` +63 > 55）**按 I-7 记为欠账放行：增量已全是接线，判定逻辑确已
  下沉 `HorizonRuntime`；继续压只能删掉解释仓库级陷阱的注释，收益为负。
