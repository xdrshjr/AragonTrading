# AI 自动交易 · 自适应利润保护（ai-trade-profit-protection-adaptive）设计方案 v2

> 迭代循环 `tge-4ad08bb1`（全局目标：**让 AI 自动交易在合适的时候保住利润**）第 2/3 轮设计。
> 第 1 轮已交付并合入 `2da0131`（32 文件）：`ai_auto/profit_guard.py` 确定性保护层（峰值追踪 →
> 移动止盈 / 保本 / 可选硬止盈 → 实时价判定 → 市价平仓）+ prompt 上下文 + UI + 可观测。
> 上游设计基线：`docs/plans/ai-trade-profit-protection/spec.md` v2（下称 **v1 方案**）。
> 本轮**不推翻**任何 v1 不变式，只在其上做四件事：让保护线自适应、让保护后不立刻回吐、
> 让回测能验证保护、让「到底保住了多少」可被测量。

---

## 〇、评审记录（Subtask #1，v1 → v2）

评审口径：可行性 / 完整性 / 一致性 / 规模合宜。每一条都在真实代码上核对过落点（文件:行号为评审当日
`master` 工作树实测值）。**全部 P0 与 P1 已在下文正文就地修复**；P2 中可零成本修的也一并改了，
其余作为实现节点的注意事项留档。

### P0（阻断级：不改会造成错误交易行为或使验收门禁不可能达成）

**P0-1 · 「入场价夹紧」制造了一个新的可达状态：本层会以「移动止盈 / 锁定利润」之名做一次净亏出场。**

- §2.1.1 不变式 3 原文断言「本层不制造亏损出场」。实际不成立：`breakevenArmReturnPct` 在
  `schemas.py:518`（`ge=0`）与 `config.py:221`（`ge=0`）两处都允许取 0（= 关闭保本线），启动弹窗也能填 0。
  保本线关闭时 `_effective_exit_px` 只剩移动止盈线；ATR 下界把 `trail` 推到入场价之下、被夹到 `entry`
  ⇒ `profit_guard._touched("long", px, entry)` 在 `px ≤ entry` 即为真 ⇒ 返回 `REASON_TRAILING`。
  此时毛盈亏 = 0、净盈亏 = −（开仓 + 平仓手续费）。
- 更糟的是文案链条全部硬编码「锁定利润」：`profit_guard._REASON_LABEL[REASON_TRAILING] = "移动止盈"`、
  `live_auto/explain.py:164` 的 summary「…由利润保护层直接市价平仓锁定利润」、本方案 §2.6 时序图里的
  status 文案「已市价平仓锁定利润」。即在一笔净亏出场上向用户与模型宣称「利润已锁」——与 v1 终审
  RV-1（不得宣称并不存在的兜底）同一类诚实性缺陷，且这次是**本轮新引入**的。
- 还有一个被算例掩盖的方向性回归：§2.1.4 断面 C 在 v1 下 `trail = entry + 0.00085×0.6 = 0.07296`
  （**能落袋**），加了 ATR 下界后变成 `entry = 0.07245`（**净亏**）。原稿把这一格轻描淡写成
  「保本线胜出」，靠「保本线默认开着」遮住了问题——一旦用户关掉保本线，遮不住了。
- **修复（§2.1.1 / §2.1.3 / §2.1.4 / §六 / T-5）**：① 夹紧目标改为
  `breakeven_px if breakeven_armed else entry_px`；② 当夹紧真的绑定在 `entry`（即无保本线兜底）时，
  移动止盈线**整条置 `None`**、`trailBasis="suppressed_at_entry"`，本轮不产生任何 trailing 裁决
  （与本层 fail-open 极性一致：宁可不动，也不做一次注定净亏的强平）；③ status / `lastExit` /
  `build_profit_guard_explain` 的措辞一律由 `pnlUsd` 的符号派生，不得无条件写「锁定利润」。

**P0-2 · F3 反转了 v1 已记录并已用测试钉死的 P2-4 决策，但方案未点名两条反向冻结用例与两处反向注释；
G-7 按原文字不可能满足。**

- `services/quant/tests/test_ai_trade_api.py:552`
  `test_backtest_start_never_assembles_profit_guard`：`assert "profitGuard" not in AiBacktestStartIn.model_fields`。
- `apps/web/components/Agent/AiTradeLaunchModal.test.tsx:579`
  「回测段整块隐藏利润保护 UI 且提交体不带该键（§1.3 非目标，**不得暗示回测已含保护**）」。
- `services/quant/app/api/ai_auto.py:107` docstring：「**[P2-4] 只接 `ai_trade_start`，绝不接
  `ai_backtest_start`**」；`services/quant/app/schemas.py:549`：「**只挂实盘启动**，回测显式非目标」。
- 原稿 §3.2/§3.3 只写「修改」，G-7 又要求「失败集合 ⊆ 迭代 1 终审基线」。这两条用例必然先红：
  实现节点要么误判为回归而回滚 F3，要么直接删注释、在代码里留下与实际行为**相反**的记录
  （CLAUDE.md §四明令禁止僵尸/错误注释）。
- **修复（新增 §7.2a + 改写 G-7 + §3.1/§3.2/§3.3 相应行）**：正文新增「有意反转的既有断言清单」，
  逐条点名文件:行号、反转理由与必须同步改写的注释；G-7 的基线改为「迭代 1 基线 ∪ §7.2a 清单」。

### P1（必须修：可行性 / 完整性缺口）

| 编号 | 问题 | 修复落点 |
|---|---|---|
| P1-1 | **`ai_auto/backtest_manager.py` 缺席变更表**。`AiBacktestSpec` 由 `backtest_manager._build_spec`（:113-137）构造、入口是 `AiBacktestStartRequest` 数据类；不改这两处，API 层解析出的 `profit_rule` 根本到不了 runner，F3 整条断路 | §3.1 新增一行 + §9 顺序 5 |
| P1-2 | **F3 保守成交序在 epoch 起始未定义**。§2.3.1 第 1 步要求「用 `adverse` 组装快照且 `state` **不推进**」，但开仓根上 `state is None`，而 `advance_state` 是唯一的建 state 入口——按原文写会 `AttributeError` | §2.3.1 新增第 0 步 |
| P1-3 | **§2.3.2「开仓当根即可触发」是错的**。`opened_wall = 开仓根 ts`、`now_wall = 当前根 ts` ⇒ 开仓根 `holdSeconds = 0 < min_hold_s(30)` ⇒ 开仓根**恒不触发**，最早次根（15m ⇒ 900s）。照原文写 T-1 与 §7.3-5 的用例必红 | §2.3.2 改写 |
| P1-4 | **冷却期的 wire / trace / store 链路缺口**。§4.2 只写「挂决策 payload / `status` 事件」，但真实渲染点需要：`ai_auto/trace.py:90 new_round_trace` 是**显式取键**（现只取 `profitGuard`）、`session._status_payload()`、`lib/aiTradeTypes.ts` 的 `AiTradeRoundTrace`、分组流 hook 与 store。且 `AiTradePanel.tsx:103` 的快照取自 `member.position?.profitGuard`，而**平仓后 `position` 为 `null`**——冷却 chip 按原稿根本拿不到数据 | §4.2 重写 + §3.1/§3.3 补三行 |
| P1-5 | **`live_auto/explain.py` 的「峰值→落袋」metric 在其调用点算不出来**。`build_profit_guard_explain` 在 `session.py:978` 于 `executor.apply` **之前**构造，此刻 `trade.pnl` 尚不存在；捕获率是成交后量。且该函数已有 7 个 keyword 形参，再加参数进一步违反 CLAUDE.md「形参 ≤ 5」 | §3.1 该行改写 |
| P1-6 | **F3 的冷却门禁在回测侧无落点**。原稿把它塞进「`_build_payload` 注入两键 + 冷却门禁」，但 `_build_payload` 是只读装配函数；门禁必须落在 `_run_decision`（`backtest.py:360-364`）的 `apply_ma_band_gate` 与 `apply_consensus_gate` 之间，`gate_reason` 需从二元合成改为三元，并计入 `_tally` 的 `degraded_rounds`；冷却状态由谁持有（runner 字段）也未交代 | 新增 §2.3.3a |
| P1-7 | **F4 的分母在加仓 / 部分成交下不是一个真实存在过的数**。`peakUnrealizedPnlUsd = qty_coin × (peak_px − entry)` 用**当前** qty 与**漂移后**的均价去乘历史峰值价；`_adopt_partial/_adopt_fill` 改写 `entry_price` 时 `peak_px` 刻意不 rebase（v1 §2.3 的既定取舍）。捕获率是第 3 轮调参的唯一依据，口径瑕疵不写出来就是误导 | §2.4.2 补一条口径 + 用例 |
| P1-8 | **`AiTradeLaunchModal.tsx` 已 1284 行**，本轮再加 4 行输入 + 回测段渲染 ⇒ ~1400 行。F0 的正当性正是 CLAUDE.md 800 行红线，同一条红线在前端被无声突破，且 §7.3-9 只统计后端行数 | §3.3 抽 `ProfitGuardFields.tsx` + §7.3-9 扩口径 |

### P2（留档，部分已就地修正）

| 编号 | 问题 | 处置 |
|---|---|---|
| P2-1 | §3.2 的 `test_ai_backtest.py` **不存在**；实际是 `test_ai_backtest_runner.py` / `test_ai_backtest_api.py` / `test_ai_backtest_manager.py` | 已改 |
| P2-2 | §5.3 写 `trades.peak_pnl REAL NULL`，与 §2.4.3 的「类型串沿用 `FLOAT` 惯例，**勿写 REAL**」自相矛盾 | 已改为 FLOAT |
| P2-3 | §2.4.3 说「新增三键」却列了五键 | 已改 |
| P2-4 | 行号漂移：`_run_loop` 实为 `backtest.py:308-334`（非 315-320）；modal reset effect 实为 `:243-283`（非 261-268）；`_summarize_pairs` 实为 `ai_auto_repositories.py:82`（非 74）；`_close_at` 实为 `simulator.py:287` ✓；`apply_risk` 实为 `:169` ✓ | 已改 |
| P2-5 | 回测 ATR 只写了「`i-1 < 0` → None」，未覆盖 NaN / 非有限。`indicators.atr` 用 `ewm(min_periods=period)`，前 `period-1` 行恒 NaN，而 `_compute_warmup` 在无 HTF、无体制上下文、策略暖机小的组合下可小于 14 | 已补 |
| P2-6 | `AiBacktestRunner._run_config()` 未回填 `profitGuard`，回测 run 的 `config_json` 无法复现本次参数——§4.1 却承诺了 `config_json` 镜像 | 已补 |
| P2-7 | `trade.setdefault("peakPnlUsd", self._guard.take_peak_pnl())`：`setdefault` **恒求值实参**，键已存在时仍会把峰值消费掉 | 已改为显式 `not in` 判定 |
| P2-8 | §3.3 未提 `AiTradeLaunchModal.tsx:439 canSubmit` 与 `:484` 错误横幅里的 `mode === "live"` 分支——只改校验不改这两处，回测模式下校验会跑但拦不住提交 | 已补入该行 |

---

## 一、概述

### 1.1 为什么第 1 轮之后还需要这一轮

第 1 轮把「盈利了不平仓」从**没有任何机制**变成了**有一条确定性防线**：峰值回吐超过固定比例
（默认 40%）即由系统市价平仓。上线后暴露出的是这条防线的**形状问题**，不是有无问题：

1. **一刀切的回吐比例在两端都不对。** 回吐阈值 40% 与「已经赚了多少」「这个标的抖不抖」全无关系。
   浮盈只有 +5% 保证金收益时，40% 回吐在 5× 杠杆下折合标的价格仅 0.4%——低于多数标的的分钟级噪音，
   保护层会在趋势还在时把仓位扫掉（这是与原始 bug **相反**的失败模式，同样吃掉利润）；而浮盈到
   +50% 保证金收益时，40% 回吐意味着放任 20 个点的利润回吐后才动手，仍然是「眼看着利润溜走」。
2. **保护性平仓之后没有任何约束，利润可以当场还回去。** 保护层平仓 → 同一根 K 线内 Agent 因为
   「信号仍看多」立刻重新开同向仓 → 价格继续回落 → 二次止损。锁住的利润经两次手续费与一次反向
   波动被抹平。v1 方案 §12 已把「保护平仓后的冷却期」显式列为下一轮候选（刻意不加、留作观测项）。
3. **回测里根本没有保护层，用户无法在花真钱之前调这些参数。** v1 §1.3 把 AI 回测接入列为非目标，
   理由是「按根推进算不出 bar 内峰值，用收盘价近似 = 假数据」。这个理由对**收盘价近似**成立，
   但对 **bar 内 OHLC 极值 + 保守成交序**不成立——`engine/simulator.py:169` 的 `apply_risk`
   早就用同一套办法处理止损止盈（`h`/`l` 触发、止损优先、触发价成交），本项目已有可复用的诚实范式。
   现状的真实代价是：回测与实盘**系统性不同构**，用户拿回测调出来的参数在实盘直接失真。
4. **没有任何一个数字回答「保住了多少」。** 现有观测只有 `profitGuardExits`（保护触发次数）与
   `profitGuardArmedRounds`（武装轮数），两者都不能回答本次迭代循环的验收问题：**峰值浮盈里，
   最终落袋了几成**。没有这个数，第 3 轮无从判断参数该收紧还是放宽，只能继续拍脑袋。

### 1.2 方案要旨

本轮交付四个特性，全部落在 `ai_auto/` 与前端，**不新增对交易所的任何写路径**：

- **F1 自适应移动止盈**：回吐比例改为「利润阶梯（peak 越高、回吐阈越紧）」，回吐**距离**再经
  **ATR 上下界**归一（低于 `atr_floor_mult × ATR` 视为噪音、抬到下界；高于 `atr_cap_mult × ATR`
  视为过松、压到上界）。仍然是纯函数、仍然只用实时价判定、仍然 fail-open。
- **F2 保护性平仓后的冷却期**：保护层强平后的一段时间内，同向开仓被**确定性降级为 hold**
  （与 MA 通道铁律、共识门禁同一范式、同一降级码通道），并如实写进 prompt 与 UI；`close` 永不被拦。
- **F3 AI 回测接入保护层（bar 内极值 + 保守成交序）**：回测与实盘**共用同一纯核心**，
  差异只在「谁提供行情断面」。产出必须自带「保守近似」免责，绝不宣称等同实盘。
- **F4 利润捕获率（peak → realized）可观测**：每个持仓周期的峰值浮盈随平仓成交落库一列，
  统计看板给出 `捕获率 = Σ已实现 / Σ峰值浮盈`，历史与实时同口径。

### 1.3 目标 / 非目标

**目标（本轮交付）**

- F0 把保护层运行时从 `AiTradeSession` 抽成 `ai_auto/profit_guard_runtime.py`（**纯搬运、零行为变化**，
  先移动跑绿再加功能）。这是 v1 §11 D-6 欠账（`session.py` 已 1397 行，远超 CLAUDE.md 800 行红线）
  的部分偿还，也是 F1/F2 不把 session 推得更远的前提。
- F1 阶梯回吐 + ATR 归一的移动止盈距离；快照如实透出「本轮回吐阈是多少、被哪条约束定住」。
- F2 保护性平仓冷却期（默认 180s、仅锁同向），实盘与回测同一纯核心。
- F3 AI 回测接入保护层与冷却期，含保守成交序与免责文案。
- F4 峰值浮盈落库（`trades.peak_pnl`，一列，幂等补列）+ 捕获率聚合 + 统计看板展示。

**非目标（本轮明确不做）**

- **不把保护线同步到交易所侧。** 现状：`okx/trade_client.py` 只有 `get_algo_pending_orders`
  （只读），没有 `amend-algos` / `cancel-algos` / `order-algo` 任何写端点；要让移动止盈线在
  桌面端进程被关掉之后仍然生效，等于新增一整条**带真实资金写权限的算法单生命周期**
  （下单 / 改价 / 撤单 / 对账 / 幂等）。这是独立一轮的工作量，且失败模式（孤儿算法单）比
  本轮任何缺陷都严重。本轮维持现状：进程不在 = 只有开仓时随单附的 OCO 兜底，**并在宪章与 UI 中
  如实说明**（v1 已有的 fail-open 披露继续成立）。建议列为第 3 轮候选。
- **不做分批止盈（scale-out）。** `LiveExecutor._do_close` 走 OKX `close-position`（整仓），
  部分平仓需要新的 reduce-only 下单路径 + 均价/峰值/OCO 三处状态的部分化改造，且 `executor.py`
  被 live-auto 共用（v1 §2.1 的隔离结论继续成立）。
- **不改 `ai_trade_default_sl_pct=0.05` / `ai_trade_default_tp_pct=0.10` 的数值**（理由同 v1 §1.3：
  它们是进程崩溃时唯一还生效的交易所侧兜底，静默收紧会让存量会话被正常波动扫损）。
- **不做 AI 回测的捕获率统计**（`SimBroker.TradeRecord` 没有峰值字段；回测侧观测以
  `aiStats.profitGuardExits` + 权益曲线为准）。
- 不新增 WS 事件类型（冷却搭 `status` 与决策 payload；捕获率搭既有 `trade` 与历史详情）。

---

## 二、技术设计

### 2.0 F0：先把保护层运行时抽出来（纯搬运，零行为变化）

**为什么必须先做**：`session.py` 现在 1397 行；F1/F2 若继续原地接线，会再加 ~120 行，把一个
时序敏感（`_monitor_tick` / `_post_round` / `_dispatch` 三处编排点）的文件推得更不可维护。

**抽取边界**（严格按「有没有 `await` / 碰不碰 executor 的下单路径」切）：

| 迁出到 `ai_auto/profit_guard_runtime.py`（`ProfitGuardRuntime`） | 留在 `AiTradeSession` |
|---|---|
| 字段 `_profit_state` / `_profit_hold_streak` / `profit_guard_exits` / `profit_guard_armed_rounds` / `_profit_exit_warned_seq` / `_pending_protect_note` | `_maybe_profit_exit`（async 编排、状态机复检） |
| `_position_facts`（改为收显式入参，见下） | `_apply_profit_exit`（`_exec_lock` + `executor.apply`） |
| `_refresh_profit_state` → `refresh(...)` | `_report_profit_exit`（`_push` / 事件判据 P1-1） |
| `_update_profit_hold_streak` → `update_hold_streak(...)` | `_exchange_oco_px`（读 `executor.trades`，留在会话侧） |
| `_inject_profit_guard` → `inject(payload, ...)` | `_dispatch` / `_position_payload`（调用点不变） |

`ProfitGuardRuntime` **不持有 `session` 引用**（否则等于没抽）：`refresh()` 的签名收一个
`PositionFacts`，由会话侧 `_position_facts()` 组装；`ProfitGuardRuntime` 只做「推进状态 → 组装快照 →
计数 → 注入 payload」。计数以 `@property` 暴露（`session.profit_guard_exits` 等既有读点零改）。

**硬门禁（放行条件）**：F0 单独一步提交前，`services/quant/tests/test_ai_trade_profit_guard.py`、
`test_ai_trade_session.py`、`test_ai_trade_prompts.py` **断言一字不改**、仅改「取值路径」
（`session._profit_state` → `session._guard.state`），全部绿；`git diff` 里
`profit_guard.py` 与 `prompts.py` **零改动**。F0 绿之前不许写 F1 的第一行。

### 2.1 F1 自适应移动止盈：口径、公式与不变式

#### 2.1.1 两层自适应

设 `entry` 为持仓均价、`peak` 为方向化最优价（v1 已有、单调推进）、`lev` 为杠杆、
`peak_return = |peak−entry|/entry × lev`（**保证金收益率口径**，v1 §2.2 硬约束继续成立）。

1. **利润阶梯（tier）决定回吐比例**

   ```
   giveback_eff = 取满足 peak_return ≥ threshold 的最后一档的 giveback；无匹配档 → rule.giveback_pct
   ```

   默认阶梯（`settings.ai_trade_profit_guard_giveback_tiers = "0.10:0.35,0.25:0.28,0.50:0.20"`）：

   | 峰值保证金收益 | 允许回吐 | 落袋比例 |
   |---|---|---|
   | < 10% | 40%（`giveback_pct` 基准值，v1 默认不变） | 60% |
   | ≥ 10% | 35% | 65% |
   | ≥ 25% | 28% | 72% |
   | ≥ 50% | 20% | 80% |

   约束（解析期校验，违反即 422）：阈值**严格递增**、回吐比例**单调不增**且 ∈ (0,1]、≤ 6 档、
   首档阈值 > 0。单调不增是语义硬约束——赚得越多却允许回吐得越多，等于把本轮要修的 bug 写进配置。

2. **ATR 归一决定回吐距离**（价格单位，防噪音扫损 / 防过度宽松）

   ```
   dist_giveback = |peak − entry| × giveback_eff
   dist = clamp(dist_giveback,
                lo = atr_floor_mult × ATR   (atr_floor_mult>0 且 ATR 可用时才生效),
                hi = atr_cap_mult   × ATR   (atr_cap_mult>0   且 ATR 可用时才生效))
   trail_px = peak − sign(side) × dist          # long 减、short 加
   ```

   默认 `atr_floor_mult=0.8`、`atr_cap_mult=3.0`、`ATR = ATR(14)`。
   `trailBasis` 如实记录哪条约束定住了这条线：
   `"giveback" | "atr_floor" | "atr_cap" | "clamped_breakeven" | "suppressed_at_entry"`
   （后两个由下面第 3 条的夹紧 / 抑制分支产出，优先级高于前三个——夹紧是最后一道处理）。

3. **不变式（不可配置、不可绕过）：本层绝不做一次注定净亏的强平**（v2 / P0-1 重写）

   ATR 下界会把回吐距离撑大，从而可能把 `trail_px` 推到入场价之下（高波动 + 刚武装）。原始 v1
   公式恒有 `trail` 严格优于 `entry`（`giveback ∈ (0,1]`），本轮必须显式补回这条保证：

   ```
   floor_line = breakeven_px if breakeven_armed else entry_px     # long；short 取反向
   trail_px   = max(trail_px, floor_line)                          # long；short 取 min
   ```

   - **保本线已武装**（默认路径：`breakeven_arm=3% < arm=5%`，故移动止盈一旦武装保本线必然也已武装）
     ⇒ 夹到保本线。此时移动止盈线与保本线重合，`_effective_exit_px` 与判定序均不变，
     实际触发者会被记为 `保本线`——归因如实（是保本线在兜底，不是移动止盈在锁利）。
   - **保本线被用户关闭**（`breakevenArmReturnPct=0`，`schemas.py:518` 与 `config.py:221` 都允许）
     ⇒ 夹到入场价。但**夹到入场价的那条线一律不发裁决**：`trail_px` 置 `None`、
     `trailBasis="suppressed_at_entry"`、`trailClampedAtEntry=true`，本轮**不产生任何 trailing 裁决**。

   **为什么是「置 None 不动作」而不是「夹到入场价照常触发」**：`_touched("long", px, entry)` 在
   `px ≤ entry` 即为真，毛盈亏恰为 0、净盈亏 = −（开 + 平手续费）。让一个名为「利润保护」的层去执行
   一次必然净亏的强平，既与本层存在的理由冲突，也与它的 **fail-open 极性**冲突（宁可不动，也绝不
   据一个不作数的线动一笔真实持仓）。在这一格上，正确的行为是**如实告诉用户与模型「本轮没有可执行的
   移动止盈线」**，而不是制造一次假装在锁利的亏损出场。

   **零回归说明**：v1 参数下（`atr_*=0`）`dist = |peak−entry|×giveback`、`giveback ∈ (0,1]`
   ⇒ `trail` 恒优于 `entry`，也恒优于保本线（武装需 `peak_return ≥ 5%`，而保本偏移只有
   `0.2%/lev`）⇒ 夹紧与抑制两条分支都不会绑定 ⇒ 逐字节等价（G-1 据此成立）。

#### 2.1.2 ATR 从哪来（真源与新鲜度）

- **实盘**：`AiTradeSession` 在 `feed_candle` 的**确认根**分支（`session.py:495-512`，
  紧随 `self._last_candle_wall = self._last_px_wall = time.time()`）刷新
  `self._atr_px = _safe_atr(self._window.base_df(), period)`，复用 `engine/indicators.py:48` 的
  `atr()`（`market_digest.py:235` 的 `_atr_reading` 已是同一函数的既有消费者，不另造第二份公式）。
  样本不足（`len < period+1`）、非有限、≤0 → `None` ⇒ **上下界整体不生效**（退化为纯阶梯回吐）。
- **ATR 允许滞后一根，判定价不允许**：ATR 是**波动尺度**，天然是确认根的统计量；而判定价必须是
  `_current_px()`（v1 §2.2 / P1-2，15m 未收盘时确认根收盘价最坏滞后一整根）。两者口径不同、
  不得混用——`PositionFacts.mark_px` 的单源约束一字不改，本轮只新增 `PositionFacts.atr_px`。
- **回测**：`AiBacktestRunner._setup` 里一次性向量化 `self._atr_series = atr(main_df, period)`，
  第 `i` 根用 **`iloc[i-1]`**（第 `i` 根的 ATR 含本根 `h/l`，当根使用即未来函数）。
  `i-1 < 0` → `None`；**且必须与实盘用同一条降级判据**（P2-5）：`indicators.atr` 是
  `ewm(alpha=1/period, min_periods=period)`，前 `period-1` 行恒为 `NaN`，而 `_compute_warmup`
  在「无 HTF + 关闭体制上下文 + 策略暖机很小」的组合下可以小于 14 ⇒ 取值后必须过
  `math.isfinite(v) and v > 0`，否则 `None`（上下界整体不生效，退化为纯阶梯回吐）。

#### 2.1.3 有效退出线与快照新键

`_effective_exit_px` 语义不变（移动止盈线与保本线取更靠近现价的一条；硬止盈是上方目标位、
刻意不参与，v1 P2-5）。快照（§5.2）新增只读键：
`givebackPctEffective` / `trailBasis` / `trailDistancePx` / `trailClampedAtEntry` / `atrPx`。
`status="unavailable"` 时与三条线一样**一律 None**（绝不显示一个算得出来却不作数的数字）。

`trailBasis="suppressed_at_entry"` 时（P0-1）：`trailStopPx` 与 `trailDistancePx` 一律 `None`、
`trailClampedAtEntry=true`、`givebackPctEffective` 与 `atrPx` 仍如实下发（用户要能看出「是波动率把
线推到了入场价之下，所以这条线本轮作废」）。此时 `_effective_exit_px` 只剩保本线；保本线也关着
⇒ `effectiveExitPx=None` ⇒ 沿用 v1 既有文案「有效退出线：尚未武装，本轮保护层不会平仓——退出完全
由你负责」，无需新分支。

#### 2.1.4 一个可反算的完整算例（所有数字必须能被反算验证，v1 P1-3 的教训）

`DOGE-USDT-SWAP`，`side=long`，`entry=0.07245`，`lev=5`，`qty_coin=1000`，`ATR=0.00080`，
默认参数（`arm=5%`、`giveback=40%`、`breakeven_arm=3%`、`breakeven_offset=0.2%`、阶梯如上）：

| 断面 | peak | peak_return | 档位 → giveback_eff | dist_giveback | ATR 下/上界 | dist（basis） | trail_px | 有效退出线 |
|---|---|---|---|---|---|---|---|---|
| A 大幅盈利 | 0.07500 | 3.5197%×5 = **17.60%** | ≥10% → **0.35** | 0.00255×0.35 = **0.00089250** | 0.00064 / 0.00240 | 0.00089250（`giveback`） | 0.07500−0.00089250 = **0.0741075** | max(0.0741075, 0.0724790) = **0.0741075** |
| B 刚武装 | 0.07330 | 1.1732%×5 = **5.87%** | 无匹配 → **0.40** | 0.00085×0.40 = 0.00034 | **0.00064** / 0.00240 | 0.00064（`atr_floor`） | 0.07330−0.00064 = **0.07266** | max(0.07266, 0.0724790) = **0.07266** |
| C 高波动夹紧（保本线开） | 0.07330（ATR=0.00200） | 5.87% | 0.40 | 0.00034 | **0.00160** / 0.00600 | 0.00160（`atr_floor`） | 0.07330−0.00160 = 0.07170 < 保本线 ⇒ **夹到 0.0724790**（`clamped_breakeven`，`trailClampedAtEntry=true`） | max(0.0724790, 0.0724790) = **0.0724790**（两线重合，触发记为**保本线**） |
| D 同 C 但保本线关（`breakevenArmReturnPct=0`） | 0.07330（ATR=0.00200） | 5.87% | 0.40 | 0.00034 | **0.00160** / 0.00600 | —（线作废） | 0.07170 < entry 且无保本线 ⇒ **`trailStopPx=None`**（`suppressed_at_entry`） | **None** ⇒ 本轮保护层不会平仓，如实告知 |

反算式（评审与用例必须照此写成**构造式**而非字面价）：
`peak_return = (peak−entry)/entry×lev`；`trail = peak − clamp(|peak−entry|×giveback_eff, lo, hi)`；
`breakeven_px = entry × (1 + breakeven_offset/lev) = 0.07245 × (1+0.002/5) = 0.0724790`；
断面 A 触发时本地派生浮盈 `= qty_coin × (trail−entry) = 1000 × 0.0016575 = 1.6575 USDT`，
峰值浮盈 `= 1000 × 0.00255 = 2.55 USDT`，**税前捕获率 = 1.6575/2.55 = 65% = 1 − giveback_eff** ✓
（与 F4 的捕获率口径自洽：不含手续费时，捕获率恒等于 `1 − giveback_eff`，这正是可验证性的来源）。

对比 v1：断面 A 在 v1 下 `dist = 0.00255×0.40 = 0.00102`、`trail = 0.07398`、捕获率 60%。
本轮把大幅盈利段的落袋从 60% 抬到 65%，同时把小幅盈利段的退出线从 0.07278 放宽到 0.07266
（让出 ATR 级别的噪音空间）。**两个方向的改动都必须在回测里可验证**——这正是 F3 存在的理由。

**断面 C/D 是本轮最容易出错的一格，必须反着读一遍**（P0-1）：v1 在断面 C 的 `trail` 是
`0.07245 + 0.00085×0.6 = 0.07296`（**净盈出场**）；F1 加了 ATR 下界后原始 `trail` 掉到 `0.07170`
（**净亏出场**）。也就是说 ATR 下界在这一段是**比 v1 更差**的，夹紧不是锦上添花而是**必需的止血**：
保本线开着时夹到保本线（C，仍是净盈附近），保本线关着时整条线作废（D，不动作）。任何把 D 写成
「夹到 0.07245 后照常触发」的实现都会在真实资金上做一次 `pnl = −手续费` 的强平，并配上「锁定利润」
的文案——这正是本轮评审判为 P0 的那个状态。

### 2.2 F2 保护性平仓后的冷却期

#### 2.2.1 语义

- **触发**：仅当保护层**确实产生了 `close` 成交**（沿用 v1 P1-1 的唯一成交判据，`_report_profit_exit`
  的 `closed=True` 分支）时开启冷却：`cooldown_until_wall = now + rule.cooldown_s`，
  `cooldown_side = 被平掉的方向`。Agent 自己主动的 `close`、会话级止盈止损、外部平仓**都不开启冷却**
  ——冷却是对「系统替你做的决定」的补偿，不是对模型正常交易的惩罚。
- **作用**：冷却期内，`open_long`/`open_short` 按 `cooldown_scope` 降级为 hold：
  `"same_side"`（默认）只拦与 `cooldown_side` 同向的开仓；`"both"` 两个方向都拦。
- **豁免（不可动摇）**：`close` / `hold` / `cancel_order` **永不被拦**——与共识门禁、MA 通道铁律
  同一条铁律（`signals.py:137` 的注释即先例：保证「共识不足也永远能平仓保护」）。
  会话级止盈止损（`_maybe_session_exit`）与保护层自身平仓同样不受影响。
- **过期**：纯时间驱动，无需清理任务；`remaining ≤ 0` 即视为未激活，`profitGuardCooldown` 键消失。

#### 2.2.2 门禁落点与优先级

```
decide → _update_hold_streak / _update_profit_hold_streak
      → 【基础策略】MA 通道铁律门禁      (session.py:549)
      → ★ 利润保护冷却门禁（本轮新增）   ← 插在这里
      → 共识门禁                        (session.py:551)
      → 二次反思 → _execute_decision
```

理由：铁律优先级最高（并存时报铁律的原因，v1 已定）；冷却与共识都是「补偿控制」，但冷却是
**本系统刚刚亲手做过的动作**的直接后果，解释力强于共识票数，故排在共识之前，
沿用 `degrade or reason` 的**先执行者胜出**语义。降级码：`profit_cooldown(120s,long)`。

#### 2.2.3 让模型知道，而不是让它撞墙

冷却激活时，决策 payload 注入 `profitGuardCooldown`（§4.2），`prompts.py` 渲染一行：

```
【利润保护 · 冷却期】刚由移动止盈平掉一笔 long（0.0741075 平出，锁定 +1.658 USDT）。
接下来 120s 内**做多开仓会被系统拦截并降级为观望**（做空不受影响）。这是为防止刚锁住的利润
被一次立刻反手回吐；若你判断趋势确实延续，请等冷却结束后再以新的证据开仓。
```

不写这段的后果是可预期的：模型每轮提议开仓、每轮被降级、每轮把「被拦截」当成新证据去自我怀疑，
既烧 LLM 又污染上下文（这正是 `hold-inertia` 那一轮踩过的坑）。

### 2.3 F3 AI 回测接入保护层

#### 2.3.1 保守成交序（本特性最容易做错的地方）

在第 `i` 根上，令 `adverse = l`（short 为 `h`）、`favorable = h`（short 为 `l`），
`state.peak` 为**截至第 `i−1` 根**的峰值：

0. **epoch 初始化（v2 / P1-2，原稿缺此步会直接 `AttributeError`）**：若 `state is None`
   或 epoch 身份（`side`, `seq`）与本根持仓不符，**先**用 `mark_px = entry_px`（**不是** `adverse`）
   调一次 `advance_state` 把 state 建起来（`peak_px = entry_px`），**并跳过本根的第 1 步**。
   这与 `min_hold_s` 的效果一致（见 §2.3.2：开仓根 `holdSeconds = 0`，本就不可能触发），
   因此不是额外的保守假设，只是把「开仓根不判回撤」这条既有语义写清楚。
1. **先判回撤**：用 `mark_px = adverse` 组装快照（`state` **不推进**），
   `evaluate_profit_guard(snapshot, rule, allow=(TRAILING, BREAKEVEN))`。命中 ⇒ 平仓。
2. **再推峰值**：未命中才用 `mark_px = favorable` 调 `advance_state` 推进 `peak`，
   再 `evaluate_profit_guard(snapshot, rule, allow=(HARD_TAKE_PROFIT,))`。命中 ⇒ 平仓。
3. 都未命中 ⇒ 本根无动作，`peak` 已推进到 `favorable`，供下一根使用。

**这个顺序意味着：同一根内刚创出的新高不会立刻抬高移动止盈线。** 现实中若价格先冲高再回落触及
被抬高后的线，会以更好的价格出场；本模型不给这个便宜，因此**系统性低估**保护层收益——
这正是「保守」的定义，也是允许它进回测的前提（与收盘价近似的**高估/失真**有本质区别）。

**成交价（跳空处理）**：触发价即成交价，但绝不给出「这根从未成交过」的价格：

```
long  保护线（下方线）：fill = min(line, open)     # 跳空低开穿越 ⇒ 只能按开盘价成交（更差，保守）
short 保护线（上方线）：fill = max(line, open)
long  硬止盈（上方线）：fill = max(line, open)     # 跳空高开 ⇒ 按开盘价成交（更好，但那是事实）
short 硬止盈（下方线）：fill = min(line, open)
```

滑点与手续费由 `SimBroker._close_at` 既有逻辑照常施加（`simulator.py:287`）。

#### 2.3.2 时钟与 epoch：把 bar 时间戳当墙钟

回测没有墙钟。统一规定：`state.opened_wall = 开仓根的 ts`、`facts.now_wall = 当前根的 ts`。
于是 `holdSeconds` 是真实的 bar 时间秒数，`rule.min_hold_s` **一字不改地复用**，
无需另造 `min_hold_bars` 概念。

**语义后果（v2 / P1-3 更正，原稿写反了）**：开仓根上 `now_wall == opened_wall`
⇒ `holdSeconds = 0 < min_hold_s(默认 30)` ⇒ **开仓当根恒不触发**；最早可触发的是次根
（15m ⇒ `holdSeconds = 900`，1H ⇒ `3600`，任一 ≥ 30s 的周期都是「次根即可」）。
这是正确且保守的行为——开仓根的成交价就是本根开盘价，允许同根强平等于在一根内完成一次
零信息量的往返、只付两笔手续费；但用例与 §7.3-5 必须按「次根触发」写，照原稿的「开仓当根即可触发」
写会必红。若某天真要在开仓根生效，唯一正确的做法是把 `min_hold_s` 显式设为 0，而不是改判据。

冷却期同样用 bar ts 计时，`cooldown_s` 语义在两侧一致；`cooldown_s=180` 在 15m 周期上
**不足一根** ⇒ 回测里冷却几乎恒在次根即已过期。这不是 bug，而是「回测按根推进」的固有粗粒度，
必须写进 §2.3.5 的差异清单（已补第 ⑤ 条），不得让用户以为回测验证过了冷却期的效果。

epoch 身份：实盘是 `(side, seq)`（v1 P0-1，`seq` 由 `_dispatch` 观察 close 成交自增）；
回测 SimBroker 单持仓、无部分成交、无挂单，`pos.entry_bar` 就是天然的持仓周期 id，
故回测侧令 `seq = main.pos.entry_bar`。同一个 `ProfitGuardState` 结构、同一条 `advance_state`
判据，无第二套状态机。

#### 2.3.3 挂点与执行

`_run_loop`（`backtest.py:308-334`）每根的四阶段插一步：

```
fill_pending → apply_risk（交易所侧 OCO/max_hold，既有） → ★ _apply_profit_guard_bar(i, row) → mark → …
```

排在 `apply_risk` 之后：交易所侧 OCO 是物理上先于本进程存在的兜底，同根同时命中时按更保守的先手记因
（与实盘不同——实盘保护层跑在 5s 盯市上、通常先于远端 OCO 触发；这条差异必须写进 §2.3.5 差异清单）。
排在 `mark` 之前：本根内的平仓必须反映进本根权益点（与 `apply_risk` 的既有语义一致）。

平仓走新增的 `SimBroker.close_at_price(px, ts, reason)`——`_close_at` 的**公有薄封装**
（`simulator.py:287` 私有方法零改动，`run_backtest` 调用序零变化）。绝不用「把
`pos.stop_loss_price` 改写成移动止盈线、借 `apply_risk` 的手平仓」这种取巧：那会让成交理由
被记成 `stop_loss`，归因当场撒谎（保护层的存在感恰恰来自「用户能看出利润是被谁锁住的」）。

#### 2.3.3a 回测侧冷却门禁的落点（v2 / P1-6 补齐）

原稿把冷却门禁写进「`_build_payload` 注入两键 + 冷却门禁」，但 `_build_payload` 是只读装配函数、
不改写 decision。真实落点必须与实盘同构：

- **状态持有者**：`AiBacktestRunner` 的实例字段 `self._cooldown: CooldownState | None`
  （不是 payload、不是 SimBroker）。写点唯一：`_apply_profit_guard_bar` 观察到本根**确实产生了
  保护性平仓**（`close_at_price` 已调用）之后，`self._cooldown = CooldownState(until_wall=bar_ts + cooldown_s, …)`。
- **门禁落点**：`_run_decision`（`backtest.py:360-364`），插在 `apply_ma_band_gate` 与
  `apply_consensus_gate` **之间**，与实盘 §2.2.2 同序：

  ```python
  decision, base_reason      = apply_ma_band_gate(...)
  decision, cooldown_reason  = apply_profit_cooldown(decision, self._cooldown, now_wall=ts)  # ★ 新增
  decision, consensus_reason = apply_consensus_gate(...)
  gate_reason = base_reason or cooldown_reason or consensus_reason   # 三元，先执行者胜出
  ```

  `apply_profit_cooldown` 是与实盘**共用的同一个纯函数**（`ai_auto/profit_cooldown.py`），
  严禁在回测侧复制一份判定——这正是 v1 P1-4 给【基础策略】铁律定下的规矩。
- **计数**：`gate_reason` 非空即计入 `_tally` 的 `degraded_rounds`（既有语义自动覆盖，无需改 `_tally`）。
- **零回归**：`self._cooldown` 恒为 `None`（`profit_rule=None` 或 `cooldown_s=0`）⇒
  `apply_profit_cooldown` 原样直通 ⇒ `gate_reason` 与合入前逐字相同（G-3）。

#### 2.3.4 决策上下文与宪章

- `AiBacktestRunner.decider` 构造时传 `profit_guard_text=format_profit_guard_charter(profit_rule)`
  （`prompts.py:62` 的 `profit_guard_text` 形参**已存在**，v1 为实盘加的；`""` ⇒ 渲染逐字节不变）。
- `_build_payload` 注入 `profitGuard` 快照与（激活时）`profitGuardCooldown`，
  `prompts.py:410` 的既有渲染管线自动生效，**prompt 模板零改动**。
- 这是一次**有意的回测输出变更**：v1 时代回测 payload 无 `profitGuard` 键、prompt 逐字回退。
  本轮默认装配后，同参数同区间的回测结果会与 v1 不同。**这正是目的**（不对称比不一致更糟），
  但必须给出一键回退：`AI_TRADE_PROFIT_GUARD_BACKTEST_ENABLED=false` ⇒ 回测侧恒不装配。

#### 2.3.5 必须写进结果 notes 与 UI 的差异清单（诚实约束）

回测 `_build_notes` 追加一条、`components/Chart/aiBacktest/panels.tsx` 显示同源文案：

> 利润保护在回测中为 **bar 内极值保守近似**：① 同一根内的新高**不会**立即抬高移动止盈线
> （先判回撤、后推峰值）；② 跳空穿越按开盘价成交；③ 回测按根推进，实盘每 5 秒盯市一次，
> 触发时机必然更早、更细；④ 回测无部分成交、无挂单撤改、无交易所降级态；
> ⑤ 开仓当根不判回撤（`min_hold` 在 bar 时钟下等价于「至少持有一根」），且冷却秒数若短于一根
> K 线（默认 180s < 15m）在回测里几乎恒于次根即已过期——**回测无法验证冷却期的实际效果**。
> 故回测的保护触发次数与锁定利润**相对实盘偏保守**，不得据此推断实盘一定更好或更差。

### 2.4 F4 利润捕获率

#### 2.4.1 数据从哪来（一个持仓周期一个数）

`ProfitGuardRuntime.refresh()` 每次都会算出 `peakUnrealizedPnlUsd`；运行时把最近一次
**可用**（`status != "unavailable"`）快照的该值缓存为 `last_peak_pnl_usd`。
`AiTradeSession._dispatch`（`session.py:226-233`）在观察到 `side == "close"` 的 trade 时
（`_position_seq` 自增的同一处），**在 `_persist_trade` 之前**回填：

```python
if "peakPnlUsd" not in trade:                                  # 绝不用 setdefault（P2-7）
    trade["peakPnlUsd"] = self._guard.take_peak_pnl()           # 读取即消费，避免下一笔继承
```

**为什么不能写 `setdefault`**（P2-7）：`dict.setdefault(k, v)` **恒求值** `v`，键已存在时也会把
峰值消费掉，从而让下一笔平仓拿到 `None`。这是一个只在「同一笔 trade 被二次分发」时才现形的静默
数据丢失，用例很难覆盖，必须在写法上排除。

`take_peak_pnl()` 读取即清空（与 v1 P1-7 的 `lastExit` 同范式）。保护层未装配 / 快照一路
unavailable / 会话中途接管的仓位 ⇒ `None`，**绝不填 0**（0 会被聚合当成「峰值就是 0」，
把捕获率算成 0 或 ∞）。

**缓存新鲜度**：`last_peak_pnl_usd` 是「最近一次可用快照」的值。保护性平仓路径上它就是触发同一 tick
的值（`_maybe_profit_exit` 先 `refresh` 再判定再平仓）；**模型主动平仓**路径上它最坏落后一个 5s
盯市周期（`_monitor_tick` 的 `_maybe_profit_exit` 也会 refresh）。这个 ≤5s 的滞后是有意接受的
——把 `refresh` 塞进下单前的关键路径只会让 `_exec_lock` 持有时间变长，收益却只有小数点后几位。

#### 2.4.2 口径（写进 UI 提示，不写就是误导）

- **分子** `realized = trade.pnl`：executor 的净已实现盈亏，**含**手续费。
- **分母** `peak = peakPnlUsd`：保护层本地派生的峰值浮盈，**不含**手续费与资金费
  （v1 已就此与交易所 `upl` 划清界限）。
- `captureRatio = realized / peak`，**仅当 `peak > 0`** 时计入；`peak ≤ 0`（全程未浮盈）
  的往返计入 `noPeakCount` 单列，不进分母。
- **分母在加仓 / 部分成交下不是一个真实存在过的数**（v2 / P1-7，原稿漏列的口径瑕疵）：
  `peakUnrealizedPnlUsd = qty_coin × (peak_px − entry_px)` 用的是**当前** `qty_coin` 与**漂移后**的
  `entry_px`（`executor._adopt_partial` / `_adopt_fill` 会在部分成交与加仓时改写均价），而 `peak_px`
  是一个不随均价 rebase 的**市场量**（v1 §2.3 的既定取舍，改它会引入必错的 rebase 分支）。
  三者相乘得到的是「按最终手数与最终均价重估的峰值浮盈」，**不是账户在峰值时刻真实拥有过的浮盈**。
  故：① UI hint 必须加一句「持仓期间加过仓的往返，峰值为按最终均价重估值」；
  ② `lib/aiTradeCapture.test.ts` 与后端聚合用例各需一条「epoch 内均价漂移」样本，钉住这条口径是
  **已知且被接受的**，而不是某次实现走样；③ 第 3 轮据捕获率调参时，必须优先看**未加仓**样本。
- 由此：口径本身让小额交易的捕获率系统性偏低（手续费占比大），UI 必须标注
  「分母不含手续费，分子含；小额往返会偏低」。**同时**它给出一条可验证的锚：
  无手续费、无加仓的理想情形下，保护层触发的往返捕获率恒 `= 1 − giveback_eff`（§2.1.4）。

#### 2.4.3 落库与聚合

- 一列 `trades.peak_pnl`（`FLOAT NULL`），经既有幂等补列器 `db/session.py:83 _ensure_trade_risk_columns`
  的 `risk_columns` 字典新增一项 `"peak_pnl": "FLOAT"`（该字典已为 7 列做过同样的事，
  `create_all` 不补列的老库全靠它；类型串沿用该字典的 `FLOAT` 惯例，勿写 `REAL`）。
- `append_live_trade`（`repositories.py:294-303`）显式字段映射新增 `peak_pnl=trade.get("peakPnlUsd")`。
- `schemas.trade_out`（`schemas.py:871`）新增 `"peakPnlUsd": getattr(t, "peak_pnl", None)`
  （`getattr` 兜底：补列前的进程/老快照读到 None 而不是 AttributeError）。
- 历史聚合 `db/ai_auto_repositories.py:82 _summarize_pairs` 新增**五**键（P2-3：原稿写「三键」列了五个）：
  `peakPnlUsd`（Σ 有效峰值）、`capturedPnlUsd`（对应往返的 Σ realized）、`captureRatio`、
  `capturePairs` / `noPeakCount`（样本诚实性）。读侧一律
  `getattr(exit_trade, "peak_pnl", None)`（补列前的老库读到 None 而非 AttributeError）。
  配对逻辑 `_pair_round_trips`（按 `id` 因果序）
  **一字不改**——那是 `5173d88` 用双时钟事故换来的结论。
- 实时（未结束会话）由前端纯函数 `lib/aiTradeCapture.ts` 用同一口径算，与后端聚合
  **公式逐字对应**（两份实现是既有惯例：`_summarize_pairs` 的 docstring 就写着「口径同前端
  `summarizeRoundTrips`」；本轮沿用并在两侧都加同一组用例）。

### 2.5 fail-open 极性、并发与锁（全部沿用 v1，此处只声明不变）

- 保护层仍是 **fail-open**：ATR 不可用只降级为纯阶梯回吐；快照 `unavailable` 仍是零动作；
  冷却期只**阻止开仓**（更保守方向），不制造任何交易。
- 触发路径仍是 `executor.apply([close], order_plan=None)` + `_exec_lock` + 取锁后复检
  `projected != flat`；本轮不新增任何下单路径。
- 冷却状态是两个标量（`until_wall` / `side`），只在 `_report_profit_exit` 的 `closed=True` 分支写、
  在决策轮同步读，无跨协程可见性问题（同一事件循环、无线程）。

### 2.6 时序图（一次自适应触发 + 冷却）

```
5s monitor tick                                     决策轮（K 线确认 / 定时唤醒）
──────────────                                     ─────────────────────────
_monitor_tick
  reconcile / equity / position
  ProfitGuardRuntime.refresh(facts)
    advance_state(peak ← max(peak, current_px))
    giveback_eff ← tier(peak_return)                  _build_decision_payload
    dist ← clamp(|peak−entry|×giveback_eff,             payload["profitGuard"] = 同一快照
                 0.8×ATR, 3.0×ATR)                     payload["profitGuardCooldown"] = 冷却视图（激活时）
    trail ← max(peak − dist, entry)                    prompt 渲染两段（纯函数唯一真源）
  evaluate_profit_guard(snapshot)                     decide()
    current_px ≤ trail ⇒ ProtectVerdict(trailing)      ├ MA 通道铁律门禁
  _apply_profit_exit                                   ├ ★ 冷却门禁 → open_long 降级 hold
    async with _exec_lock: 复检 → executor.apply(close) │   degrade="profit_cooldown(120s,long)"
  _dispatch(events)                                    ├ 共识门禁 → 反思
    close 成交 ⇒ _position_seq += 1                     └ _execute_decision（本轮无单）
    trade["peakPnlUsd"] ← runtime.take_peak_pnl()
    _persist_trade → trades.peak_pnl
  _report_profit_exit（closed=True）
    profit_guard_exits += 1
    cooldown_until = now + 180s；cooldown_side = "long"
    status 事件：「利润保护触发（移动止盈）：已市价平仓锁定利润；180s 内暂停做多开仓」
```

---

## 三、文件 / 模块变更计划

### 3.1 后端源（`services/quant/app`）

| 文件 | 动作 | 一句话意图 |
|---|---|---|
| `ai_auto/profit_guard_runtime.py` | 新增 | **F0**：`ProfitGuardRuntime` 承接从 `AiTradeSession` 迁出的保护层状态 / 快照 / 计数 / payload 注入 / 峰值回执（不持 session 引用，纯搬运 + 本轮新增的 ATR 与冷却状态载体） |
| `ai_auto/profit_cooldown.py` | 新增 | **F2** 纯核心：`CooldownState` / `build_cooldown_view` / `apply_profit_cooldown`（降级门禁）/ `format_profit_cooldown`（prompt 文案唯一真源） |
| `ai_auto/profit_guard_bar.py` | 新增 | **F3** 纯核心：`evaluate_bar(state, rule, bar_facts)` 保守成交序（先判回撤、后推峰值）+ 跳空成交价规则；只调用 `profit_guard` 的既有纯函数，零逻辑复制 |
| `ai_auto/profit_guard.py` | 修改 | **F1**：`ProfitGuardRule` 新增 5 字段（dataclass 默认 = v1 行为）；`parse_giveback_tiers` / `resolve_giveback_pct`；`_derive` 改为「阶梯 → ATR clamp → 入场价夹紧」；快照 5 个新键；`evaluate_profit_guard` 新增 keyword-only `allow=` 过滤；`_format_lines` / 宪章补自适应与冷却披露；`rule_wire` 镜像新字段 |
| `ai_auto/session.py` | 修改 | F0 委托 runtime（净减行）；`feed_candle` 确认根刷新 `_atr_px`；`_position_facts` 带上 `atr_px`；决策管线插 `_apply_profit_cooldown_gate`（铁律后、共识前）；`_report_profit_exit` 成功分支开启冷却；`_dispatch` 回填 `peakPnlUsd` |
| `ai_auto/backtest.py` | 修改 | **F3**：`AiBacktestSpec.profit_rule`（默认 None）；`_setup` 预算 ATR 序列；`_run_loop`（:308-334）插 `_apply_profit_guard_bar`；决策器传 `profit_guard_text`；`_build_payload` 注入 `profitGuard`/`profitGuardCooldown` 两键；`_run_decision`（:360-364）插冷却门禁（§2.3.3a）；`_aggregate_stats` 出 `profitGuardExits`；`_run_config` 回填 `profitGuard`（P2-6：否则回测 run 的 `config_json` 无法复现参数，与 §4.1 的承诺矛盾）；`_build_notes` 追加保守近似免责 |
| `ai_auto/backtest_manager.py` | 修改 | **P1-1（原稿缺席）**：`AiBacktestStartRequest` 新增 `profit_rule` 字段；`_build_spec`（:113-137）透传给 `AiBacktestSpec`。不改这两处，API 层解析出的规则到不了 runner，F3 整条断路 |
| `ai_auto/trace.py` | 修改 | **P1-4（原稿缺席）**：`new_round_trace`（:90）是**显式取键**函数（现只取 `payload["profitGuard"]`），须新增 `"profitGuardCooldown": payload.get("profitGuardCooldown")` 初值，否则 `AiTradeTraceRound` 拿不到冷却数据 |
| `ai_auto/prompts.py` | 修改 | 新增 `_format_profit_cooldown`（延迟导入范式同 `_format_profit_guard`），在 `build_round_user_message` 的保护段之后渲染；**模板文本不动** |
| `engine/simulator.py` | 修改 | 新增公有薄封装 `close_at_price(px, ts, reason)`（转调既有 `_close_at`，`:287`），供回测保护层按触发价平仓；既有路径零改动 |
| `api/ai_auto.py` | 修改 | `ai_backtest_start`（:270）接 `_resolve_profit_guard(body.profitGuard, settings)`（受 `ai_trade_profit_guard_backtest_enabled` 总闸约束）并塞进 `AiBacktestStartRequest`；实盘接线不变。**必须同步改写 `_resolve_profit_guard` 的 docstring（:107）**——它现在写着「[P2-4] 只接 `ai_trade_start`，绝不接 `ai_backtest_start`」，留着就是一条与代码相反的记录（P0-2） |
| `schemas.py` | 修改 | `AiTradeProfitGuardIn`（:499）新增 5 入参（含 `givebackTiers` 结构化列表 + 单调性校验）；`AiBacktestStartIn`（:601）新增 `profitGuard`；`trade_out`（:870）出 `peakPnlUsd`。**必须同步改写 `AiTradeGroupStartIn.profitGuard` 的注释（:549）**——现写着「只挂实盘启动，回测显式非目标」（P0-2） |
| `config.py` | 修改 | 7 个新配置（阶梯串 / ATR 周期与上下界 / 冷却秒与范围 / 回测总闸），env 前缀沿用 `AI_TRADE_PROFIT_GUARD_*` |
| `db/models.py` | 修改 | `Trade.peak_pnl: Float NULL`（本轮唯一 schema 变更） |
| `db/session.py` | 修改 | `_ensure_trade_risk_columns`（:82）的 `risk_columns` 新增 `"peak_pnl": "FLOAT"`（幂等补列，老库零手工迁移；该字典已为 7 列做过同样的事） |
| `db/repositories.py` | 修改 | `append_live_trade`（:294-303）显式映射新增 `peak_pnl=trade.get("peakPnlUsd")` |
| `db/ai_auto_repositories.py` | 修改 | `_summarize_pairs`（:82）新增捕获率五键；`_pair_round_trips` **零改动** |
| `live_auto/explain.py` | 修改 | `build_profit_guard_explain`（:140）**只加一条** metric「本轮回吐阈 X%（约束来源：giveback / atr_floor / atr_cap / clamped_breakeven）」——纯规则量，构造时算得出来；并把 summary 的措辞由 `pnl` 符号派生（P0-1：不得无条件写「锁定利润」）。**「峰值→落袋」metric 移出本文件**（P1-5）：该函数在 `session.py:978` 于 `executor.apply` **之前**构造，此刻 `trade.pnl` 尚不存在，捕获率是成交后量，算不出来；且该函数已有 7 个 keyword 形参，再加就更偏离 CLAUDE.md「形参 ≤ 5」。捕获率一律由前端 `summarizeProfitCapture` / 后端 `_summarize_pairs` 在成交后派生 |

### 3.2 后端测试（`services/quant/tests`）

| 文件 | 动作 | 覆盖 |
|---|---|---|
| `test_ai_trade_profit_guard.py` | 修改 | 既有断言**一字不改**（G-1 零回归；已核对：该文件全部为构造式 / 子集断言，无整字典比对，故新增 5 个快照键不会撞既有用例）；新增阶梯选档 / 单调性校验 422 / ATR 上下界三态 / **T-5 夹紧与抑制两分支**（P0-1）/ `allow=` 过滤 / 快照新键 |
| `test_ai_trade_profit_cooldown.py` | 新增 | 开仓被降级、`close`/`hold`/`cancel_order` 豁免、`same_side` vs `both`、到期自动失效、`cooldown_s=0` 恒直通、降级码文本 |
| `test_ai_trade_profit_guard_bar.py` | 新增 | **T-1 复现用例**（§7.1）；保守序（同根新高不抬线）；跳空穿越按开盘价成交；硬止盈走 favorable 通道；epoch 初始化根跳过回撤判定（P1-2）；`min_hold_s` 用 bar ts ⇒ **开仓根不触发、次根才触发**（P1-3） |
| `test_ai_trade_session.py` | 修改 | F0 取值路径迁移（断言不改）；冷却门禁接线位置（铁律后、共识前）；保护平仓开启冷却；`_dispatch` 回填 `peakPnlUsd` 且读取即消费；未装配零键 |
| `test_ai_backtest_runner.py` | 修改 | **P2-1：原稿写的 `test_ai_backtest.py` 不存在**。`profit_rule=None` 逐字节零回归；装配后触发一次保护平仓并落 `aiStats.profitGuardExits`；notes 含免责句；ATR 用 `i-1` 且 NaN 降级（无未来函数）断言；冷却门禁三元合成 |
| `test_ai_backtest_manager.py` | 修改 | `AiBacktestStartRequest.profit_rule` 透传到 `AiBacktestSpec`（P1-1 的守门用例） |
| `test_ai_trade_api.py` | 修改 | 回测 start 接受并回传 `profitGuard`；阶梯非单调 → 422；`backtest_enabled=false` ⇒ 不装配。**并按 §7.2a 反转 `test_backtest_start_never_assembles_profit_guard`（:552）** |
| `test_ai_auto_history.py` | 修改 | 捕获率聚合（含 `peak ≤ 0` 不入分母、`peak` 缺失样本计数、**epoch 内均价漂移样本**，P1-7）；老库缺列时 `trade_out` 仍含 `peakPnlUsd: None` |

### 3.3 前端（`apps/web`）

| 文件 | 动作 | 一句话意图 |
|---|---|---|
| `lib/types.ts` | 修改 | `ProfitGuardSnapshot` 5 个新键 + 新增 `ProfitGuardCooldown`；`Trade.peakPnlUsd?: number \| null` |
| `lib/aiTradeTypes.ts` | 修改 | `AiTradeProfitGuardRule` 新增 5 字段（`givebackTiers` 为 `{peakReturnPct,givebackPct}[]`）；回测启动体加 `profitGuard`；`AiTradeRoundTrace` 加 `profitGuardCooldown`（P1-4，与 `trace.py` 的新键对齐）；`AiTradeGroupMemberState` 加 `profitGuardCooldown` |
| `lib/aiTrade.ts` | 修改 | `AI_TRADE_PROFIT_GUARD_DEFAULTS` 扩展；`validateProfitGuard` 加阶梯单调性 / ATR 倍数 / 冷却秒校验；新增 `describeProfitGuardTrail`（basis 文案唯一真源）与 `describeProfitGuardCooldown`；`describeDegradeReason` 补 `profit_cooldown` |
| `lib/aiTrade.test.ts` | 修改 | 上述纯函数用例（含 basis 三态与夹紧态文案） |
| `lib/aiTradeCapture.ts` | 新增 | 纯函数 `summarizeProfitCapture(trades)`：配对复用 `tradeTrails`/`autoTradePnl` 既有往返，口径与后端 `_summarize_pairs` 逐字对应 |
| `lib/aiTradeCapture.test.ts` | 新增 | 分母门槛（`peak ≤ 0` 不计）、缺失样本、手续费导致 `<1` 的正常态、无样本降级 |
| `lib/aiTradeHistoryTypes.ts` | 修改 | `AiTradeHistorySummary` 新增捕获率五键 |
| `lib/aiBacktestTypes.ts` | 修改 | 回测 `aiStats.profitGuardExits` + notes 已有结构复用 |
| `components/Agent/ProfitGuardFields.tsx` | 新增 | **P1-8**：把利润保护整段（开关 + 8 个受控输入 + 阶梯预设下拉 + 校验提示）抽成受控子组件（值与 setter 全部由父传入，**reset 仍留在父的 `[open]` effect 内**，不得在子组件里另写 effect——那正是 `ai-trade-modal-revert-recurrence` 的复发根因）。`AiTradeLaunchModal.tsx` 已 **1284 行**，远超 CLAUDE.md 800 行红线；F0 的正当性就是这条红线，同一条红线不能在前端被无声突破 |
| `components/Agent/AiTradeLaunchModal.tsx` | 修改 | 保护分区**回测模式也渲染**（删 `:931` 的 `mode === "live"` 门控，改为随模式切文案）；接入 `ProfitGuardFields`；新增「回吐阶梯（预设下拉）/ ATR 下界 / ATR 上界 / 冷却秒数」四行；回测提交体（`:458-462` 的 backtest 分支）带 `profitGuard`；`profitGuardError`（`:370`）两模式都校验，**并同步放开 `canSubmit`（`:439`）与错误横幅（`:484`）里的 `mode === "live"` 分支**（P2-8：只改校验不改这两处，回测模式下校验会跑但拦不住提交）；新增受控输入**必须塞进既有 `[open]` reset effect**（`:243-283`），不得另写 effect |
| `components/Agent/AiTradeLaunchModal.test.tsx` | 修改 | 回测模式露出保护段并随体提交；阶梯预设切换；reset 回归（open 恒真下输入不回弹，`:495` 那条注释已明令「每加一个新字段就要在此加一条断言」）。**并按 §7.2a 反转 `:579` 的「回测段整块隐藏利润保护 UI」用例** |
| `hooks/useAiTradeGroupStream.ts` + `lib/store.ts` | 修改 | **P1-4（原稿缺席）**：`profitGuardCooldown` 的落库点。`AiTradePanel.tsx:103` 的保护快照取自 `member.position?.profitGuard`，而**平仓后 `position` 恒为 `null`**——冷却 chip 若沿用同一取数路径必然永远拿不到数据。故冷却视图必须由 `status` 事件写入 `member.profitGuardCooldown`（与 `position` 平级的独立字段），并在 `remainingSeconds ≤ 0` 或新持仓建立时清空 |
| `components/Chart/AiTradePanel.tsx` | 修改 | 保护行补「回吐阈 X%（约束来源）」与 `suppressed_at_entry` 的如实文案；冷却激活时一枚倒计时 chip；持仓段门控仍是 `positionSide !== "flat"` 且快照存在（冷却 chip 例外：门控为 `member.profitGuardCooldown != null`，**不得挂在 `position` 之下**，见上一行） |
| `components/Agent/AiTradeTraceRound.tsx` | 修改 | 冷却降级轮显示原因与剩余时间（复用 `describeProfitGuardCooldown`） |
| `components/Agent/aiTradeStats/AiTradeStatsLive.tsx` | 修改 | 左侧 KPI 新增「利润捕获率」（含样本数与口径 hint） |
| `components/Agent/aiTradeStats/AiTradeStatsDetail.tsx` | 修改 | 历史详情同一 KPI，取后端 `summary.captureRatio`（不在前端重算历史） |
| `components/Chart/aiBacktest/panels.tsx` | 修改 | 回测结果显示 `profitGuardExits` + §2.3.5 免责全文（与后端 notes 同源文案） |

---

## 四、接口设计

### 4.1 `POST /api/ai-trade/start` 与 `POST /api/ai-trade/backtest/start`（加法字段）

```jsonc
{
  "profitGuard": {
    "enabled": true,
    "armReturnPct": 0.05,
    "givebackPct": 0.40,                 // 基准档（无 tier 命中时使用）
    "givebackTiers": [                   // ★ 新增；缺省 → settings 串解析；[] → 关闭阶梯
      { "peakReturnPct": 0.10, "givebackPct": 0.35 },
      { "peakReturnPct": 0.25, "givebackPct": 0.28 },
      { "peakReturnPct": 0.50, "givebackPct": 0.20 }
    ],
    "atrFloorMult": 0.8,                 // ★ 新增，0 = 关闭下界
    "atrCapMult": 3.0,                   // ★ 新增，0 = 关闭上界
    "cooldownSeconds": 180,              // ★ 新增，0 = 关闭冷却
    "cooldownScope": "same_side",        // ★ 新增，"same_side" | "both"
    "breakevenArmReturnPct": 0.03,
    "breakevenOffsetReturnPct": 0.002,
    "minHoldSeconds": 30,
    "hardTakeProfitReturnPct": null,
    "maxPriceAgeSeconds": 90
  }
}
```

- 语义三层不变（v1 §2.8）：运维总闸关 ⇒ 恒不装配；字段缺省 ⇒ settings 回填并**默认开启**；
  `enabled:false` ⇒ 该会话不装配。**回测**新增第四层：`ai_trade_profit_guard_backtest_enabled=false`
  ⇒ 回测侧恒不装配（一键回到 v1 的回测行为）。
- 422 条件（`schemas.py` 边界一次性校验，进入内部后信任）：阶梯阈值非严格递增 / 回吐比例非单调不增 /
  比例 ∉ (0,1] / 档数 > 6 / `atrFloorMult > atrCapMult`（两者都 > 0 时）/ 冷却秒 ∉ [0, 86400]。
- 回执 `group_status().profitGuard` 与 run `config_json` 由 `rule_wire` 镜像全部新字段。

### 4.2 WebSocket `/api/ws/ai-trade/{sessionId}`（加法负载，无新事件类型）

`position` 事件搭车的 `profitGuard` 快照新增：

```jsonc
{
  "givebackPctEffective": 0.35,
  "trailBasis": "giveback",            // "giveback" | "atr_floor" | "atr_cap"
  "trailDistancePx": 0.0008925,
  "trailClampedAtEntry": false,
  "atrPx": 0.0008
}
```

冷却视图**不挂 `position`**——冷却期内按定义已无持仓，`_position_payload` 恒返回 `None`，
挂上去等于永远送不出去（P1-4：原稿只写「挂决策 payload / `status` 事件」，没落到具体载体上，
而 `AiTradePanel.tsx:103` 恰恰是从 `member.position?.profitGuard` 取数的）。**三条载体逐一钉死**：

| 载体 | 落点 | 消费者 |
|---|---|---|
| 决策 payload（进 LLM） | `session._inject_profit_guard` 同处注入 `payload["profitGuardCooldown"]` | `prompts._format_profit_cooldown` |
| `agent_trace` 的 `round_start` | `trace.py:90 new_round_trace` **显式**新增 `"profitGuardCooldown": payload.get("profitGuardCooldown")` | `AiTradeTraceRound.tsx` |
| `status` 事件 | `session._status_payload()` 新增同名键（冷却未激活 ⇒ `None`） | `useAiTradeGroupStream` → `member.profitGuardCooldown` → `AiTradePanel` chip |

```jsonc
{
  "profitGuardCooldown": {
    "activeUntilTs": 1784817180,
    "remainingSeconds": 120,
    "scope": "same_side",
    "blockedSide": "long",
    "reasonCode": "profit_guard_trailing",
    "label": "移动止盈"
  }
}
```

`remainingSeconds` 是**下发瞬间**的快照值；前端不得据它做倒计时判真伪，只用于显示，
实际是否仍在冷却一律以后端每轮重算为准（避免前后端两个时钟各自过期）。
冷却结束后必须**主动再推一次 `status`**（`profitGuardCooldown: null`），否则 chip 会一直挂着。

`trade` 事件（close）新增 `peakPnlUsd`（`number | null`）。历史详情 `summary` 新增
`peakPnlUsd` / `capturedPnlUsd` / `captureRatio` / `capturePairs` / `noPeakCount`。

### 4.3 配置（`config.py`，env 前缀沿用）

| 键 | 默认 | 说明 |
|---|---|---|
| `ai_trade_profit_guard_giveback_tiers` | `"0.10:0.35,0.25:0.28,0.50:0.20"` | `阈值:回吐` 逗号分隔；空串 = 关闭阶梯（退回固定 `giveback_pct`） |
| `ai_trade_profit_guard_atr_period` | `14`（ge=2, le=200） | ATR 周期，与 `market_digest._ATR_PERIOD` 同值但**各自独立**（后者是展示读数，不共享配置以免一改两处语义漂移） |
| `ai_trade_profit_guard_atr_floor_mult` | `0.8`（ge=0, le=10） | 回吐距离下界倍数；0 = 关 |
| `ai_trade_profit_guard_atr_cap_mult` | `3.0`（ge=0, le=50） | 回吐距离上界倍数；0 = 关 |
| `ai_trade_profit_guard_cooldown_s` | `180.0`（ge=0, le=86400） | 保护性平仓后的冷却秒数；0 = 关 |
| `ai_trade_profit_guard_cooldown_scope` | `"same_side"` | `same_side` \| `both` |
| `ai_trade_profit_guard_backtest_enabled` | `True` | 回测侧总闸；false ⇒ 回测逐字回到 v1（无保护、payload 无键） |

---

## 五、数据模型

### 5.1 内存形状（`ai_auto/`）

```python
@dataclass(frozen=True)
class ProfitGuardRule:              # profit_guard.py（新增 5 字段，dataclass 默认 = v1 行为）
    ...                             # v1 既有 8 字段一字不改
    giveback_tiers: tuple[tuple[float, float], ...] = ()   # ((阈值, 回吐), …) 升序
    atr_floor_mult: float = 0.0
    atr_cap_mult: float = 0.0
    cooldown_s: float = 0.0
    cooldown_scope: str = "same_side"

@dataclass(frozen=True)
class PositionFacts:                # 新增一个可选字段，判定价单源约束不变
    ...
    atr_px: float | None = None     # 确认根 ATR(period)；None ⇒ 上下界整体不生效

@dataclass
class CooldownState:                # profit_cooldown.py
    until_wall: float               # 墙钟（回测为 bar ts）
    side: str                       # 被保护层平掉的方向
    reason_code: str
    label: str

class ProfitGuardRuntime:           # profit_guard_runtime.py
    rule: ProfitGuardRule | None
    state: ProfitGuardState | None
    cooldown: CooldownState | None
    hold_streak: int
    exits: int
    armed_rounds: int
    last_peak_pnl_usd: float | None      # F4：读取即消费
    warned_seq: int | None
    pending_protect_note: dict | None
```

`ProfitGuardState` **不新增字段**：`peak_px` 仍是唯一的持久量，`giveback_eff` / `dist` / `trail`
全部现算（v1 §2.3 的理由继续成立——存派生量就要处理均价漂移后的 rebase 分支）。

### 5.2 wire 形状

见 §4.2。约束：`status="unavailable"` 时新增的 5 个键与三条线一样**一律 None**；
`status="flat"` 空壳仍只承载 `lastExit`（v1 D-2），本轮不给空壳加新键。

### 5.3 持久化

- **唯一 schema 变更**：`trades.peak_pnl FLOAT NULL`（P2-2：原稿此处写 `REAL`，与 §2.4.3 的
  「类型串沿用该字典的 `FLOAT` 惯例，**勿写 REAL**」自相矛盾；以 `FLOAT` 为准）。
  老库经 `_ensure_trade_risk_columns` 幂等补列；
  读侧一律 `getattr(t, "peak_pnl", None)`，补列失败也只是全 `None`（降级，不炸）。
- 规则回执照旧进 `strategy_runs.config_json`；聚合口径**不落 metrics**——捕获率的单一真源是
  `trades` 表（`metrics_json` 里再存一份必然与逐笔重算漂移）。

---

## 六、渲染与文案（诚实约束）

1. **保护线文案必须交代「被谁定住」**：`移动止盈线 0.074108（已武装｜回吐阈 35%｜距峰值 0.000893）`；
   被 ATR 下界抬宽时追加 `（已按波动率放宽至 0.8×ATR）`，被上界压紧时 `（已按波动率收紧至 3×ATR）`，
   被保本线夹住时 `（已夹至保本线：本层不做劣于保本的出场）`，被抑制时整行改写为
   `移动止盈线：本轮作废（波动率下界把它推到了入场价之下，且未启用保本线）——本轮保护层不会平仓`。
   五种分支是**同一个纯函数**的分支，前后端各自渲染但公式同源（`trailBasis` 由后端下发，前端不重算）。
1b. **平仓文案不得无条件宣称「锁定利润」**（P0-1）：`status` 事件、`lastExit` 回执与
   `build_profit_guard_explain` 的 summary 一律由 `pnlUsd` 的**符号**派生
   （`> 0` ⇒「锁定利润 +X」／`≤ 0` ⇒「按保护线离场，本笔 X」）。硬编码「锁定利润」在
   保本线触发、硬止盈滑点不利、或未来任何新的夹紧分支上都会说谎——这是 v1 终审 RV-1
   （不得宣称并不存在的兜底）的同一条教训，只是换了个位置。
2. **冷却文案必须给出剩余时间与豁免范围**，且必须说明「做空/平仓不受影响」——只说「被拦截」会让
   模型以为整个交易能力被剥夺。
3. **捕获率必须标注口径与样本数**：`利润捕获率 68.2%（12 笔有峰值样本 / 共 15 笔；分母不含手续费）`；
   样本 < 3 时显示为 `样本不足`，不给百分比（沿用统计看板既有的最小样本门禁范式）。
4. **回测侧必须显示 §2.3.5 免责全文**，且**不得**在 UI 上把回测的保护触发说成「实盘会这样」。
5. v1 已有的两条诚实约束继续成立且不得回退：`status="unavailable"` 时必须按
   `_format_unavailable_fallback` 如实交代交易所侧是否真的挂了 OCO（RV-1）；`lastExit` 只随
   `status="flat"` 空壳下发，其渲染门控不得把该状态排除掉（RV-2）。

---

## 七、测试与验收标准

### 7.1 必须先写、先红、再绿的复现测试（CLAUDE.md 硬规矩）

- **T-1（F3 保守序，本轮的核心正确性）**：构造一根 `open=100, high=112, low=99, close=100` 的 bar，
  持仓 long、`entry=100`、`peak=110`（上一根遗留）、`giveback_eff=0.4` ⇒ `trail=106`。
  断言：① 本根**先**用 `low=99` 判定 ⇒ 触发 trailing，成交价 `min(106, open=100) = 100`；
  ② `peak` **不**因本根 `high=112` 先被抬到 112 再算出 `trail=107.2`（若实现写反，成交价会变成 107.2，
  用例必红）。这是整个 F3 唯一会产出「看起来更好、其实是未来函数」的路径。
- **T-2（F1 夹紧到保本线）**：§2.1.4 断面 C 的构造式断言 —— ATR 下界把线推到保本线之下时，
  `trail_px == breakeven_px`、`trailBasis == "clamped_breakeven"`、`trailClampedAtEntry is True`，
  且 `evaluate_profit_guard` 在 `current_px` 落在 (breakeven, peak) 之间时仍**不触发**。
- **T-5（F1 抑制分支，本轮 P0 的守门用例）**：§2.1.4 断面 D 的构造式断言 ——
  `breakeven_arm_return_pct=0`（保本线关）且 ATR 下界把线推到入场价之下时：
  ① `trailStopPx is None` 且 `trailBasis == "suppressed_at_entry"`；
  ② `effectiveExitPx is None`；
  ③ **把 `current_px` 一路压到 `entry_px` 甚至 `entry_px × 0.99`，`evaluate_profit_guard`
  仍恒返回 `None`**——若实现写成「夹到 entry 后照常触发」，这一条必红，而线上的表现会是
  一次 `pnl = −手续费` 的强平配上「锁定利润」的文案。
  ④ 同一构造下把 `breakeven_arm_return_pct` 恢复为 0.03 ⇒ 退回 T-2 的 `clamped_breakeven` 分支。
- **T-3（F2 豁免矩阵）**：冷却激活期内，`close` / `hold` / `cancel_order` 返回**同一个** decision 对象
  （`is` 相等，沿用 `apply_ma_band_gate` 的既有约定），只有同向 `open_*` 被降级。
- **T-4（F4 读取即消费）**：连续两次 close 成交，第二次 `peakPnlUsd` 不得继承第一次的值
  （无快照 ⇒ `None`，绝不填 0）。

### 7.2a 有意反转的既有断言与注释清单（v2 / P0-2 新增，**不在此列的一律视为回归**）

F3 反转了 v1 记录在案的 P2-4 决策（「利润保护只挂实盘，回测显式非目标」）。反转本身是本轮的
既定目标（§1.1 第 3 点：回测与实盘系统性不同构是真实代价），但**必须逐条点名**，否则实现节点
会把必然变红的用例误判为回归而回滚 F3，或者直接删掉注释、在代码里留下与行为相反的记录
（CLAUDE.md §四）。

| # | 位置 | 现状 | 本轮如何处理 |
|---|---|---|---|
| I-1 | `services/quant/tests/test_ai_trade_api.py:552` `test_backtest_start_never_assembles_profit_guard` | `assert "profitGuard" not in AiBacktestStartIn.model_fields` | **改写为正向用例**：`AiBacktestStartIn` 含该字段；`backtest_enabled=false` ⇒ runner 的 `profit_rule is None`（一键回到 v1 回测行为的守门），用例名改为 `test_backtest_start_assembles_profit_guard_unless_switch_off` |
| I-2 | `apps/web/components/Agent/AiTradeLaunchModal.test.tsx:579` 「回测段整块隐藏利润保护 UI 且提交体不带该键」 | 断言 checkbox 不在文档中、`opts.profitGuard` 为 `undefined` | **改写为正向用例**：回测模式下保护段可见、随体提交，且文案与实盘段不同（回测段必须显示 §2.3.5 的「保守近似」提示，不得让用户以为回测 = 实盘） |
| I-3 | `services/quant/app/api/ai_auto.py:107` docstring | 「**[P2-4] 只接 `ai_trade_start`，绝不接 `ai_backtest_start`**……用收盘价近似会低估触发频次……属假数据」 | **改写**：说明本轮改用 bar 内 OHLC 极值 + 保守成交序（§2.3.1），故解除该限制；并指向 `ai_trade_profit_guard_backtest_enabled` 总闸与 §2.3.5 免责 |
| I-4 | `services/quant/app/schemas.py:549` `AiTradeGroupStartIn.profitGuard` 注释 | 「**只挂实盘启动**，回测显式非目标（§1.3）」 | **改写**：两处启动体都接该字段，语义与解析点一致 |
| I-5 | `docs/plans/ai-trade-profit-protection/spec.md`（v1 方案）§1.3 非目标 | 「AI 回测接入」列为非目标 | **不改 v1 文档**（历史记录应保持可信），改由本文档 §1.1-3 与 §2.3 承接；实现节点在本文 §十一 记一句「v1 §1.3 该条已由本轮 F3 取代」 |

**G-7 的基线据此扩展**：允许失败/变更的集合 = 迭代 1 终审基线（`test_factor_library_expansion.py`
那 7 个他人在制孤儿用例）**∪ 本表 I-1/I-2 两条（且必须以改写而非删除的方式转绿）**。
清单之外任何一条变红都视为回归。

### 7.2 零回归门禁（硬性，任一不过即视为未完成）

| 编号 | 门禁 |
|---|---|
| G-0 | **F0 纯搬运**：抽取 runtime 的那一步 `git diff` 内 `profit_guard.py` / `prompts.py` 零改动，既有保护层用例断言零改动且全绿 |
| G-1 | `ProfitGuardRule()` 直构造（tiers=() / atr_*=0 / cooldown_s=0）⇒ **判定结果**与 v1 逐字节等价、快照既有键的**取值**与 v1 逐字节等价（新增 5 键为纯加法；已核对 `test_ai_trade_profit_guard.py` 无整字典比对，故「既有断言不改而绿」可达）。夹紧与抑制两条分支在 v1 参数下均不绑定（§2.1.1 第 3 条的零回归说明） |
| G-2 | `profit_rule=None` ⇒ payload 无 `profitGuard` / `profitGuardCooldown` 键 ⇒ 既有 prompt 冻结断言逐字节不变 |
| G-3 | `AiBacktestSpec.profit_rule=None`（既有直构造回测用例）⇒ `_run_loop` 的 SimBroker 调用序与结果逐字节等价 |
| G-4 | **live-auto 零触碰**：`live_auto/executor.py` 零改动（`explain.py` 仅加法 metric）；live-auto 相关用例全绿 |
| G-5 | 判定价单源仍是 `_current_px()`；ATR 只进入**距离**计算，不得出现在任何触发判定的比较式左右 |
| G-6 | 老库（无 `peak_pnl` 列）启动后幂等补列成功；补列前后 `trade_out` 恒含 `peakPnlUsd` 键 |
| G-7 | 后端 pytest 与前端 vitest 的失败集合 ⊆ 迭代 1 终审基线（`test_factor_library_expansion.py` 那 7 个他人在制孤儿用例）**∪ §7.2a 的 I-1/I-2（且二者必须以改写转绿，不得以删除「消失」）**；`ruff` 对本特性全部文件通过；`tsc --noEmit` 仅剩既有 `WeightBatchArena.test` 报错 |
| G-8 | **不留反向记录**：合入前 `grep -n "绝不接 ai_backtest_start\|只挂实盘启动\|回测显式非目标" services/quant/app` 零命中（§7.2a I-3/I-4）；`grep -rn "锁定利润" services/quant/app apps/web` 的每一处命中都必须处在由 `pnl` 符号派生的分支内（§六.1b） |

### 7.3 功能验收标准

1. 峰值保证金收益跨过 10% / 25% / 50% 三档时，快照 `givebackPctEffective` 立即变为 0.35 / 0.28 / 0.20，
   且 prompt 与 UI 显示同一个数（不存在「UI 说 35%、判定用 40%」）。
2. 低波动标的（`ATR` 小）上，回吐距离不被 ATR 下界抬宽（`trailBasis="giveback"`）；
   高波动标的上被抬宽且 `trailBasis="atr_floor"`；极端宽的阶梯被 `atr_cap` 压紧。
3. 保护性平仓后 180s 内，模型的同向开仓被降级为 hold，降级码 `profit_cooldown(<n>s,<side>)`
   出现在决策事件、轨迹与 UI；反向开仓与任何 `close` 不受影响；冷却到期后自动恢复。
4. 冷却期内每一轮的 prompt 都含冷却段（含剩余秒数与豁免说明），冷却结束后该段消失。
5. 同一份参数在**回测**里能跑出保护触发：结果 `aiStats.profitGuardExits > 0`，
   notes 与 UI 同时出现 §2.3.5 免责文案；`AI_TRADE_PROFIT_GUARD_BACKTEST_ENABLED=false` 时
   回测行为与合入前逐字节一致。
6. 每笔保护性平仓与模型主动平仓的 `trade` 都带 `peakPnlUsd`（保护层未装配 / 快照不可用时为 `null`），
   且落库可在重启后读回。
7. 统计看板（实时页与历史详情）显示利润捕获率、样本数与口径提示；样本 < 3 显示「样本不足」。
8. 理想校验（无手续费构造用例）：保护层触发的往返捕获率恒等于 `1 − giveback_eff`。
9. **行数红线双向成立**（v2 / P1-8 扩口径）：`session.py` 净减少（F0 抽取后 ≤ 1300 行）；
   三个新后端模块各自 ≤ 300 行；`AiTradeLaunchModal.tsx` 抽出 `ProfitGuardFields.tsx` 后
   **不得高于本轮开工前的 1284 行**（新增 4 行输入的增量必须被抽出的净减量抵消）。
10. 全流程零新依赖、零新 WS 事件类型、零对交易所的新写路径。
11. **保护层永不做净亏出场**（P0-1）：保本线关闭 + 高波动的构造下，`trailStopPx is None`、
    `trailBasis == "suppressed_at_entry"`，价格压到入场价之下仍不触发；且全仓库不存在
    无条件宣称「锁定利润」的文案分支（G-8）。
12. **反向记录清零**（P0-2）：§7.2a 的 I-1..I-4 逐条落实，I-1/I-2 以改写转绿而非删除。
13. 回测参数可复现：回测 run 的 `config_json` 含本次 `profitGuard` 全部字段（P2-6）。

---

## 八、风险与缓解

| 编号 | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R-1 | **ATR 下界放宽了保护，可能让回吐更多**（与本轮目标方向相反） | 高 | 下界只在「按比例算出的距离小于噪音尺度」时生效，且恒受入场价夹紧保护；默认倍数取偏小的 0.8；`trailBasis` 全链路透出以便事后归因；**F3 让用户能在花钱前用真实历史验证这条取舍** |
| R-2 | 阶梯配置写反（赚得越多允许回吐越多） | 高 | 解析期强制「阈值严格递增 + 回吐单调不增」，违反即 422；env 串与请求体两条入口共用同一个解析器与校验 |
| R-3 | 冷却期错过真实的趋势延续机会 | 中 | 默认只锁同向、180s（≈ 15m 周期的 1/5 根）；prompt 如实告知以便模型改用反向或等待；`cooldown_s=0` 一键关闭；冷却**永不**阻止平仓与风控 |
| R-4 | 回测接入后曲线变好看 ⇒ 用户高估实盘 | 高 | 保守成交序（同根新高不抬线）+ 跳空按开盘价 + §2.3.5 差异清单强制展示 + 不得宣称等同实盘；回测总闸可一键回退 |
| R-5 | 回测/实盘仍不完全对称（无 5s 盯市、无部分成交、无挂单/降级态） | 中 | 差异清单如实列出（§2.3.5）；对称性只承诺「同一纯核心、同一参数、同一口径」，不承诺同一结果 |
| R-6 | F0 抽取引入回归（保护层是真实资金路径） | 高 | 先移动、跑绿、再加功能的硬门禁 G-0；既有用例只改取值路径、断言一字不改；`_maybe/_apply/_report_profit_exit` 三个 async 编排点**不迁移** |
| R-7 | 新增 DB 列在老库缺失 | 中 | 复用已服役 5 列的幂等补列器；读侧 `getattr` 兜底；缺列时功能降级为全 `None` 而非报错 |
| R-8 | 捕获率口径被误读（分母不含手续费） | 中 | UI 与 API 文档双处标注；`peak ≤ 0` 不入分母并单列计数；给出「理想值 = 1 − giveback_eff」这一可自检锚点 |
| R-9 | `session.py` 仍超 CLAUDE.md 800 行红线 | 低 | 本轮净减至 ≤1300 并新增三个 ≤300 行模块；剩余欠账（`_monitor_tick`/`_post_round`/`_dispatch` 编排层拆分）继续记账，建议独立立项 |
| R-10 | prompt 变长（保护段 + 冷却段） | 低 | 冷却段仅在激活时出现；保护段新增内容并入既有行（不新增段落）；自察段仍受 `note_after_rounds` 门控 |
| R-11 | 阶梯 + ATR 让「为什么这条线在这里」变难解释 | 中 | `trailBasis` + `givebackPctEffective` + `trailDistancePx` 三键同时下发，UI/prompt/explain 三处复用同一渲染纯函数（v1 [R-5] 的教训：三处各写一份必然漂移） |
| R-12 | **ATR 下界在小幅盈利段把保护做得比 v1 更差**（§2.1.4 断面 C/D：v1 能在 0.07296 落袋，F1 原始线掉到 0.07170） | 高 | 夹紧 / 抑制两条分支是**必需的止血**而非可选优化（P0-1）；`atr_floor_mult` 默认取偏小的 0.8 且可置 0 一键关闭；F3 让用户能在花钱前用真实历史验证这条取舍；T-5 钉死抑制分支 |
| R-13 | 用户关掉保本线（`breakevenArmReturnPct=0`）后，高波动下保护层可能长期「无可执行的移动止盈线」 | 中 | 这是**如实降级**而非静默失效：快照 `effectiveExitPx=None`、prompt 明写「本轮保护层不会平仓——退出完全由你负责」、UI 同源文案（§六.1）；启动弹窗的保本线输入需加 hint「置 0 会在高波动下让移动止盈线失去兜底」 |
| R-14 | 冷却期在回测里几乎不可验证（180s < 一根 15m） | 中 | 写进 §2.3.5 差异清单第 ⑤ 条并在回测 UI 显示；不得把回测的「冷却无影响」当成实盘结论 |

---

## 九、实施顺序（供 Subtask #2）

1. **F0**：抽 `ProfitGuardRuntime`（纯搬运）→ 跑 G-0/G-1 → 绿了才继续。
2. **F1**：`profit_guard.py` 纯核心（阶梯 / ATR / **夹紧 + 抑制两分支** / `allow=` / 新键 / 文案）
   + 单测 **T-2 与 T-5 先红后绿**（T-5 是本轮 P0 的守门用例，先于任何接线写）；
   再接 `session.feed_candle` 的 ATR 刷新与 `_position_facts`；同步改 `live_auto/explain.py`
   与 status/`lastExit` 的措辞派生（§六.1b / G-8）。
3. **F2**：`profit_cooldown.py` + 单测 T-3 → 接 session 决策管线与 `_report_profit_exit`
   → prompt 渲染 → **wire 三载体**（`_status_payload` / `trace.py` / stream+store，§4.2）。
4. **F4**：DB 一列 + 回填（注意 P2-7 的 `not in` 写法）+ 聚合 + 前端捕获率（T-4 + 均价漂移样本）。
5. **F3**：`profit_guard_bar.py` + 单测 **T-1 先红后绿** → 接 `backtest.py`
   （含 §2.3.3a 冷却门禁与 `_run_config` 回填）→ **`backtest_manager.py` 透传**（P1-1，漏了就整条断路）
   → API/schema → **按 §7.2a 反转 I-1/I-3/I-4** → 回测 UI 与免责。
6. 前端：先抽 `ProfitGuardFields.tsx`（P1-8）再加四行输入与回测段渲染（含 `canSubmit` / 错误横幅）
   → **按 §7.2a 反转 I-2** → 统计看板收尾 → 全量门禁 G-0..G-8。

**提交注意**：工作树仍有他人在制的未提交改动（`.env.example` / `desktop/src/lib/config.js` /
`services/quant/app/config.py` 的 `ai_trade_enabled` 翻转 hunk / `engine/factors/*` 与其孤儿测试 /
`db/live_auto_repositories.py`）。`config.py` 与本特性**重叠**，必须按 hunk 行级暂存
（`git diff` → **以 bytes 读写**切 patch → 修正 `@@` 新侧起始行 → `git apply --cached --recount`），
不得整文件 `git add`。

---

## 十、评审记录（Subtask #1 已回填）

见文首 **§〇 评审记录**（2 个 P0、8 个 P1、8 个 P2；全部 P0/P1 已在正文就地修复，
文档版本已由 v1 升至 **v2**）。放行条件见文末 §评审结论。

## 十一、实施过程发现的方案缺陷（Subtask #2 回填）

### 11-0 方案要求的三个必答问题

1. **I-1..I-4 逐条落实情况**（§7.2a）：
   - **I-1** `test_ai_trade_api.py` 的 `test_backtest_start_never_assembles_profit_guard`
     已**改写**（非删除）为 `test_backtest_start_assembles_profit_guard_unless_switch_off`：
     正向断言 `AiBacktestStartIn` 含 `profitGuard`，并顺带钉死一键回退
     （`ai_trade_profit_guard_backtest_enabled=false` ⇒ 解析结果恒 `None`）。
   - **I-2** `AiTradeLaunchModal.test.tsx` 的「回测段整块隐藏利润保护 UI」已**改写**为
     「回测段渲染 + 随体提交 + 文案与实盘段不同」，并新增反向断言
     `expect(screen.queryByText(/判定只用/)).not.toBeInTheDocument()`——回测段**不得照抄**实盘的
     「判定只用实时价」文案。
   - **I-3** `api/ai_auto.py:_resolve_profit_guard` 的 docstring 已改写（说明改用 bar 内 OHLC 极值 +
     保守成交序故限制解除，并指向回测总闸与 §2.3.5 免责），另新增薄封装
     `_resolve_backtest_profit_guard`（在实盘语义之上叠一层运维总闸）。
   - **I-4** `schemas.py:AiTradeGroupStartIn.profitGuard` 注释已改写。
   - **额外一处方案未点名的反向记录（本节新增，见 11-1 的 D-3）**：`ai_auto/manager.py:131` 的
     `AiTradeGroupStartRequest.profit_rule` 注释同样写着「只挂实盘启动——回测显式非目标」，
     G-8 的 grep 会命中它，已一并改写。
2. **`AiTradeLaunchModal.tsx` 抽组件后的实际行数**：**1272 行**（开工前 1284 行，净 −12）。
   新增四个受控输入 + 回测段渲染的增量被抽出的 `ProfitGuardFields.tsx`（199 行）净减量抵消，
   §7.3-9 的「不得高于开工前」达成。`session.py` 由 1397 行降至 **1393 行**（净 −4，见 D-9：
   「≤1300」这半条未达成且原因是方案自身的硬约束）；三个新后端模块分别为
   `profit_guard_runtime.py` 222 行、`profit_cooldown.py` 116 行、`profit_guard_bar.py` 152 行，
   均 ≤300 行。
3. **`min_hold` 在 bar 时钟下的实测触发根**：**开仓根恒不触发、次根才可能触发**（§2.3.2 / P1-3 的
   写法正确）。用例 `test_entry_bar_builds_state_and_never_fires` 与
   `test_min_hold_seconds_uses_bar_timestamps` 双向钉死：`min_hold_s=1800` 时连次根（15m ⇒ 900s）
   也不触发，第三根才触发——判据确实没有被 bar 时钟绕过。

### 11-1 与方案的偏差（逐条给出原因）

| 编号 | 偏差 | 原因 |
|---|---|---|
| D-1 | `CooldownState` 比 §5.1 的四字段多一个 `scope`（默认 `"same_side"`） | §2.3.3a 规定回测侧的调用签名是 `apply_profit_cooldown(decision, self._cooldown, now_wall=ts)`——门禁**拿不到 rule**，而作用域判定必须在门禁里做。把 `scope` 放进状态是唯一不改签名的落法（也与 §4.2 的 wire 视图含 `scope` 自洽）。 |
| D-2 | `_derive` 在「basis 未被 ATR 改写」时**沿用 v1 的逐字表达式** `entry + sign(span×(1−giveback))`，而非方案 §2.1.1 写的 `peak − sign(dist)` | 两式代数等价但**浮点不等价**。G-1 要求「既有键的取值与 v1 逐字节等价」，快照的 `trailStopPx` 只 round 到 8 位小数，ulp 差异有可能穿过舍入。故在零回归路径上保留 v1 原式，仅在 ATR 绑定时用 `peak − dist`。 |
| D-3 | G-8 的 grep 还会命中 `ai_auto/manager.py:131`（方案 §7.2a 只点名了 `api/ai_auto.py:107` 与 `schemas.py:549`） | 同一句反向记录在三处，漏改一处 G-8 即不过。已一并改写。 |
| D-4 | G-8 的第二条 grep（`锁定利润`）还会命中三处方案未预见的位置：`lib/aiTrade.ts` 的 `profit_guard_trailing` 降级码标签、回测免责文案（后端 notes 与前端 panels 各一份） | 前者是**按码**渲染的固定标签，码本身不含盈亏符号（保本线触发 / 滑点不利时该笔可能净亏），已改为「已按保护线离场」；后两处是聚合口径描述，改为「落袋结果」以免与 §六.1b 的单笔措辞混淆。 |
| D-5 | `AiTradeLaunchModal.test.tsx:524` 的「默认勾选利润保护，提交体带完整参数」用例做的是**整体 `toEqual`**（非子集断言），四个新字段是纯加法也会让它变红 | 方案 §7.2a 的清单只列了 I-1/I-2，漏了这一条。已**扩展**该断言（语义一字未变：仍是「提交体逐键等于默认参数」），不是删除。**这属于清单外的必要更新，特此留档供终审核对。** |
| D-6 | `AiTradeGroupMemberState.profitGuardCooldown` 声明为**可选**（`?:`）而非方案 §3.3 的必填 | 五个既有测试文件直接构造成员对象（`AiTradeAgentTrace.test.tsx` / `AiTradeStatsPanel.test.tsx` / `AiTradeToggle.test.tsx` / `AiTradePanel.test.tsx` / `store.test.ts`），必填会让它们全部 tsc 报错——而「老快照无此键」本来就是真实状态。与 `baseRules` / `positionStepPct` 等既有可选键同范式。 |
| D-7 | 冷却在「新持仓建立时清空」这一条（§3.3 的 store 行）**未实现**，只保留「后端主动补推 status」与「`remainingSeconds ≤ 0` 不渲染」两条 | `position` 事件不带冷却字段，要在建仓时清空就得让前端自己判定冷却是否仍然有效——那正是 §4.2 明令禁止的「前后端两个时钟各自过期」。且语义上冷却期内成功反向建仓（`same_side` 作用域）时冷却**仍应生效**，清空反而是错的。单一真源仍是后端每轮重算。 |
| D-8 | 回测的 `_build_payload` 在**空仓**时不注入 `profitGuard`（实盘会用 `build_idle_snapshot` 空壳承载 `lastExit`） | 回测侧没有 `lastExit` 链路（`_pending_protect_note` 是会话态），空壳快照没有任何可承载的内容，注入它只会给 prompt 添一段「无持仓」的噪音。 |
| **D-9** | **§7.3-9 的「`session.py` ≤ 1300 行」未达成**：实测 1397 → **1393**（净减 4 行，「净减少」达成，「≤1300」未达成） | 方案自身的两条硬约束把这一格钉死了：R-6 / G-0 明令 `_maybe_profit_exit`／`_apply_profit_exit`／`_report_profit_exit` 三个 async 编排点**不迁移**（它们是真实资金路径），而 F0 表里可迁的纯同步部分只有约 60 行；本轮 F1/F2/F4 又必须在会话侧新增 ATR 刷新、冷却门禁与到期补推、`peakPnlUsd` 回填、`status` 新键共约 55 行。除非违反 R-6，1300 行不可达。已把可迁的都迁了（含 `_position_facts` / `_exchange_oco_px` → `profit_guard_runtime.build_position_facts` / `exchange_oco_px`，后者方案原列在「留在会话」列，但它是纯读 `executor.trades` 的无状态函数，迁出后会话侧再无重复读点）。**剩余欠账（`_monitor_tick`／`_post_round`／`_dispatch` 编排层拆分）继续记账，建议按 R-9 独立立项。** |

### 11-2 实测数据（放行条件与门禁）

- **G-0/G-1**：`test_ai_trade_profit_guard.py` 的既有断言**一字未改**且全绿（46 passed）；
  新增 `test_default_rule_is_byte_identical_to_v1_trail` 显式钉死 `ProfitGuardRule()` 直构造下
  `trailBasis == "giveback"`、`trailClampedAtEntry is False`、线值等于 v1 公式。
  `test_ai_trade_session.py` 只改**取值路径**（`session._profit_state` → `session._guard.state`、
  `_pending_protect_note` → `_guard.pending_protect_note`、`_profit_hold_streak` →
  `_guard.hold_streak`），断言一字未改。
- **T-5 先红后绿**：`test_trail_suppressed_at_entry_never_forces_a_losing_exit` 在写 `_clamp_trail_to_floor`
  之前先落地（当时 `trailBasis` 键还不存在 ⇒ KeyError 红），实现后转绿；四条断言全覆盖
  （`trailStopPx is None` / `basis == suppressed_at_entry` / `effectiveExitPx is None` /
  现价压到 `entry × 0.99` 仍恒返回 `None`），并含 ④ 恢复保本线退回 `clamped_breakeven` 分支。
- **T-1 先红后绿**：`test_conservative_order_uses_previous_peak_not_this_bars_high` 断言成交价为
  `min(106, open=100) = 100`；若实现写反（先推峰值）成交价会变成 107.2，用例必红。
- **端到端实测（放行条件 4）**：`test_assembled_profit_rule_fires_and_notes_the_conservative_caveat`
  真跑完一次 300 根回测，`aiStats.profitGuardExits > 0`、notes 含免责全文、`config_json` 含
  `profitGuard` 全字段、保护平仓的 `reason` 是保护码本身（未借 `stop_loss` 的手）；
  `test_backtest_cooldown_gate_composes_three_reasons` 实测到冷却降级轮
  （`degradeReason` 以 `profit_cooldown(` 开头且 `action == "hold"`）。
- **G-8**：`grep -rn "绝不接 ai_backtest_start\|只挂实盘启动\|回测显式非目标" services/quant/app`
  **零命中**；`grep -rn "锁定利润" services/quant/app apps/web` 的剩余命中全部处在
  `describe_exit_outcome` 的 `pnl > 0` 分支、`explain.py` 同款三元的正分支，或解释这条规则本身的注释里。
- **前端**：`tsc --noEmit` 仅剩既有 `WeightBatchArena.test.tsx(68,97)` 报错（迭代 1 基线）；
  vitest **1201 passed / 117 files** 全绿。
- **后端 G-7（与基线逐条比对，非「看起来差不多」）**：在 `git worktree` 拉出的 **HEAD 纯净副本**上跑
  同一条命令做对照——
  - 基线：`1 failed, 1420 passed, 1 skipped`，唯一失败 `test_agent_ws.py::test_ws_emits_ready_history_and_full_run`；
  - 本轮：`1 failed, 1461 passed, 1 skipped`，失败集合**逐条相同**（同一个用例、同一条断言）。
  即失败集合 ⊆ 迭代 1 基线，新增 41 个用例全绿。该用例与本特性零重叠（走 `api/agent.py` +
  agent manager，本轮未触碰），且在纯净基线上**单独运行同样挂死**（200s 超时被杀），属既有 flaky/hang。
  另 `test_factor_library_expansion.py` 的 7 个失败是他人在制的孤儿用例（两次运行都以 `--ignore` 排除）。
- **ruff**：`app/ai_auto` / `app/db` / `app/api/ai_auto.py` / `app/schemas.py` / `app/config.py` /
  `app/live_auto/explain.py` / `app/engine/simulator.py` 全部通过（`db/models.py:414` 的 `E741 l`
  是**本轮之前既有**、与本特性无关，未改动该行）。

## 十二、终审发现并就地修复的缺陷（Subtask #3 回填）

逐条对照 §3.1/§3.2/§3.3 变更表、§7.2 门禁 G-0..G-8、§7.3 验收 1..13 与文末 10 条放行条件复核了 49 文件
diff（含在真实代码落点上核对 P0-1/P0-2 与八条 P1）。**放行条件 1..10 全部命中**，§7.2a 的 I-1..I-4 均以
改写而非删除转绿，G-8 两条 grep 亲跑零命中 / 全部落在 `pnl` 符号派生分支内。终审另发现并**就地修复**了
下面五条（方案与实现均未覆盖）：

| 编号 | 缺陷 | 为什么是缺陷 | 修法 |
|---|---|---|---|
| **RV-1** | `aiStats.profitGuardExits` **恒下发**（未装配也是 0），而 `ProfitGuardBacktestCard` 按 `exits != null` 门控 ⇒ **一次根本没装配保护层的回测也会挂出「利润保护（回测 · 保守近似）· 触发平仓 0 次」整块免责卡片** | 用户取消勾选保护、或运维把 `AI_TRADE_PROFIT_GUARD_BACKTEST_ENABLED` 置 false 后，这张卡片在向用户宣称一条**本次回测里并不存在**的防线——与 v1 终审 RV-1（不得宣称并不存在的兜底）同一类诚实性缺陷，只是从实盘换到了回测。`panels.tsx` 里「未装配（exits 缺失）时整块不渲染」的注释与后端契约**相反**（CLAUDE.md §四禁止的反向记录） | `_aggregate_stats` 新增 `profitGuardEnabled: self.profit_rule is not None`；卡片门控改为 `enabled`；`aiBacktestTypes` 补键并改写两处注释。用例：后端两条（未装配 `is False` / 装配 `is True`）+ 前端新增 `AiBacktestModal.test.tsx` 一条（装配 ⇒ 卡片在且带触发次数；未装配 ⇒ 卡片与「保守近似」全不渲染） |
| **RV-2** | `trailBasis == "clamped_breakeven"` 时 `trailDistancePx` 仍是**夹紧前**的距离（`span×giveback` 或 `atr_floor×ATR`），与 `trailStopPx` 对不上 | prompt 的移动止盈行会写成「移动止盈线 0.072479（已武装｜回吐阈 40%｜**距峰值 0.001600**）（已夹至保本线）」，而实际距离是 0.000821——模型与用户据 `peak − 距峰值` 反算会得到一条**不存在**的线。R-11 要的可解释性正是 `trailBasis`/`givebackPctEffective`/`trailDistancePx` 三键**互相自洽**；不自洽的三元组比不下发更糟 | `_derive_trail` 在夹紧绑定时按**实际那条线**回算 `abs(peak_px − trail_px)`（抑制态仍为 `None`，线作废即距离作废）。T-2 用例补一条构造式断言钉死 |
| **RV-3** | `profit_guard.py` 由 553 行涨到 **870 行**，突破 CLAUDE.md 800 行红线 | F0 抽 `ProfitGuardRuntime`、前端抽 `ProfitGuardFields.tsx`（P1-8）引的都是这条红线，同一轮里让判定核心自己破线，等于把 §7.3-9 的口径变成「只查列进清单的那几个文件」 | 按「判定 / 文案」职责切出 `ai_auto/profit_guard_text.py`（**纯搬运、零行为变化**，与 F0 同款）：宪章章、每轮 prompt 段、保护线一行、`describe_exit_outcome`、`_UNAVAILABLE_LABEL` 迁出；判定纯核心留在 `profit_guard.py`。结果 **645 + 249 行**，两侧均在红线内；4 处应用导入 + 3 处用例导入同步改写，`test_ai_trade_profit_guard.py` / `test_ai_trade_prompts.py` 的**断言一字未改**且全绿（等价于 G-0 的搬运门禁） |
| **RV-4** | 冷却期的 **UI 半边零测试**：`AiTradePanel` 的 chip、`AiTradeTraceRound` 的降级说明、store 的 member 级落点全无用例 | 放行条件 5 与 P1-4 点名的正是这条链路「跑不出错、功能静默缺失」的失败模式——chip 若被误挂回 `member.position` 之下（平仓后恒 null）测试也不会红。冷却是本轮唯一**没有后端断言能覆盖到渲染门控**的特性 | 新增 3 条：`AiTradePanel.test.tsx` 两条（`position` 为 null 时 chip 仍在、含剩余秒与豁免范围；`remainingSeconds<=0` 不渲染）、`AiTradeAgentTrace.test.tsx` 一条（**无保护快照**的冷却轮仍渲染原因与剩余时间） |
| **RV-5** | 三处风格问题：`backtest.py` 在模块顶层已导入 `.profit_guard` 的前提下又在 `_profit_guard_wire` 里局部 `from .profit_guard import PositionFacts`；`..engine.indicators` / `.profit_guard_bar` 两处 import 插在乱序位置；`AiTradeLaunchModal.tsx` 的 `AI_TRADE_GIVEBACK_TIER_PRESETS` 与子组件 import 也插错位置 | 局部导入在此处没有任何延迟加载收益（同模块顶部已导入），只是噪音，且注释「只有装配了保护层的回测才需要」与事实不符；乱序 import 与仓库既有的字母序不一致 | 局部导入删除、`PositionFacts` 并入顶部 import 组；三处 import 归位 |

### 12-1 终审实测（合入前）

- **后端全量**：`pytest -q --ignore=tests/test_factor_library_expansion.py` ⇒ **1462 passed, 1 skipped,
  0 failed**（孤儿文件属他人在制的 factor-library-expansion，两轮都以 `--ignore` 排除；迭代 1 基线里的
  `test_agent_ws.py::test_ws_emits_ready_history_and_full_run` 本次未复现挂死，亦通过）。失败集合为空
  ⇒ **⊆ 迭代 1 基线 ∪ §7.2a**，G-7 成立。
- **前端全量**：`vitest run` ⇒ **1205 passed / 117 files**（较 Subtask #2 的 1201 增 4 条，即 RV-1 与
  RV-4 的新用例）。`tsc --noEmit` 仅剩既有 `WeightBatchArena.test.tsx(68,97)`（迭代 1 基线）。
- **ruff**：`app/ai_auto`（含新 `profit_guard_text.py`）/ `app/db` / `app/api/ai_auto.py` /
  `app/schemas.py` / `app/config.py` / `app/live_auto/explain.py` / `app/engine/simulator.py` 及本轮改动
  的用例全部通过；唯一报错是 `db/models.py:414` 的 `E741 l`（本轮之前既有，未触碰该行）。
- **G-8 亲跑**：`绝不接 ai_backtest_start|只挂实盘启动|回测显式非目标` 在 `services/quant/app` **零命中**；
  `锁定利润` 的剩余命中全部处在 `describe_exit_outcome` 的 `pnl > 0` 分支、`explain.py` 同款三元的正分支，
  或解释这条规则本身的注释 / 用例 / 文档里。
- **行数**：`session.py` 1393（D-9 的欠账维持原状，理由不变）、`AiTradeLaunchModal.tsx` **1272**（< 开工前
  1284，§7.3-9 达成）、`profit_guard.py` 645、`profit_guard_text.py` 249、`profit_guard_runtime.py` 222、
  `profit_cooldown.py` 117、`profit_guard_bar.py` 153、`ProfitGuardFields.tsx` 199。

### 12-2 未在本轮处理、明确留给第 3 轮的欠账

- `session.py` 仍 1393 行（> 800）：D-9 与 R-9 已记账，`_monitor_tick`/`_post_round`/`_dispatch` 三个
  async 编排点按 R-6 不可迁；建议按 R-9 独立立项，**不在本特性提交里顺手动真实资金路径**。
- 冷却期在回测里几乎不可验证（R-14 / §2.3.5 第 ⑤ 条）：已如实写进 notes 与回测 UI，不再另修。

---

## 评审结论

**有条件通过（有条件通过 / approved with conditions）。**

方案的技术判断整体是对的，而且难得地把上一轮的欠账（F0 抽运行时、F3 回测不同构、F4 无法回答
「保住了多少」）都摆到了台面上，没有回避。三条我认为特别值得肯定的地方：① 用 bar 内 OHLC 极值 +
**保守成交序**（先判回撤、后推峰值）解决回测接入，而不是用收盘价近似，并且明确承认这会系统性低估
——这是把「诚实」写进算法而不是写进免责声明；② 阶梯的「回吐比例单调不增」被提升为解析期硬约束，
因为写反就等于把本轮要修的 bug 写进配置；③ F0 先移动跑绿再加功能的硬门禁 G-0，对一条真实资金路径
是正确的谨慎。

但它有两个不能带进实现的问题，已在 v2 正文修掉：**新引入的「入场价夹紧」创造了一个会做净亏强平、
却仍宣称「锁定利润」的可达状态**（P0-1），以及 **F3 静默反转了 v1 记录在案并用测试钉死的 P2-4 决策**
（P0-2），后者还让 G-7 按原文字不可能满足。八条 P1 集中在「链路没接到底」：`backtest_manager.py`、
`trace.py`、store/stream 三个必经文件缺席变更表，冷却期在回测侧无门禁落点，捕获率 metric 被放在了
算不出它的调用点上。

**放行条件（Subtask #2 必须逐条落实，终审逐条验）**

1. **T-5 先红后绿**：保本线关闭 + ATR 下界绑定的构造下，`trailStopPx is None`、
   `trailBasis == "suppressed_at_entry"`，把现价压到入场价之下 `evaluate_profit_guard` 仍恒返回
   `None`。这条用例必须在 F1 的任何接线代码之前写出来并先红。
2. **文案由 `pnl` 符号派生**：`status` / `lastExit` / `build_profit_guard_explain` 三处，
   加 G-8 的 grep 自检；不留任何无条件的「锁定利润」。
3. **§7.2a 的 I-1..I-4 逐条落实**：I-1/I-2 以**改写**转绿（不得删除用例让它「消失」），
   I-3/I-4 的两处注释必须与新行为一致。
4. **`ai_auto/backtest_manager.py` 与 `ai_auto/trace.py` 必须动**：前者不透传 `profit_rule`
   则 F3 整条断路，后者不加键则冷却轨迹恒空——两者都属于「跑不出错但功能静默缺失」的类型，
   实现完成后须实测一次端到端（回测跑出 `profitGuardExits > 0`、轨迹里看得到冷却降级轮）。
5. **冷却 wire 三载体齐活**（§4.2 表）：决策 payload / `round_start` 轨迹 / `status` 事件，
   且冷却结束必须主动再推一次 `status`；`AiTradePanel` 的 chip 门控挂 `member.profitGuardCooldown`，
   **不得**挂在 `member.position` 之下（平仓后恒 null）。
6. **回测侧冷却门禁落在 `_run_decision` 的两个门禁之间**，`gate_reason` 改三元合成，
   且复用与实盘同一个 `apply_profit_cooldown` 纯函数（严禁在回测侧复制判定）。
7. **F3 的 epoch 初始化与 `min_hold` 语义按 v2 写**：开仓根建 state 且跳过回撤判定；
   用例按「次根才可能触发」写，不得按原稿的「开仓当根即可触发」。
8. **捕获率口径的两条诚实性**：`build_profit_guard_explain` 只加规则量 metric（不加算不出来的
   「峰值→落袋」）；`peak` 在加仓/部分成交下是「按最终均价重估值」这一点必须进 UI hint 并有用例。
9. **`ProfitGuardFields.tsx` 必须真的抽出来**，且 reset 仍留在父组件的 `[open]` effect 内；
   合入时 `AiTradeLaunchModal.tsx` 行数不得高于开工前的 1284 行
   （`ai-trade-modal-revert-recurrence` 是复发性缺陷，子组件里另写 effect 就是再破一次）。
10. **G-0..G-8 全绿**，其中 G-1 按 v2 的措辞理解（快照新增 5 键是纯加法，等价性指既有键取值与
    判定结果）；`config.py` 与他人在制改动重叠，仍须按 hunk 行级暂存（§9 提交注意），
    且 patch 文件必须以 **bytes** 读写（Windows 下 `write_text` 会翻 CRLF 弄坏 patch）。
