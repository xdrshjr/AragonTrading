# 交易策略回测 —— 可配置止盈止损（backtest-stop-loss-take-profit）

- **版本**: v2（Subtask #1 · 方案评审与修复；在 v1 基础上补「评审记录 / 评审结论」并就地修复 P0/P1）
- **作者**: 方案设计节点（solution architect）；评审与修复：方案评审节点（senior reviewer）
- **状态**: 已评审（P1 已就地修复，见「评审记录」；评审结论见文末「评审结论」——有条件通过）
- **需求来源**: 右侧策略面板「运行回测」弹窗当前缺少止盈止损设置，默认不设置；用户希望能在该弹窗内额外设置止盈 / 止损百分比，使回测结合止盈止损、更准确有效。

---

## 评审记录（Subtask #1 · 方案评审）

> 评审人：资深评审节点（Anthropic engineering）。评审维度：**可行性 / 完备性 / 一致性 / 规模合理性**。已对照源码逐条核验：`agent/services.py::run_single_backtest / _prepare_strategy / _execute_and_persist`、`engine/simulator.py::SimBroker.set_risk / apply_risk / _positive_float`、`engine/combo.py`（确无 `set_risk` / `RiskParams` 路径）、`strategies/base.py::risk_params`、`schemas.py::BacktestReq`、`api/strategies.py::backtest`、`StrategyPanel.tsx::runBacktest`、`lib/store.ts::backtestConfig`、`ComboStrategyDetail.tsx`、`lib/tradeExplain.ts`。**核心机制成立**：引擎与 `base.risk_params()` 早已支持止盈止损，本方案「入口 + 覆盖注入 + 展示」路线可行，无 P0。

**已核验为真的关键事实（支撑本方案可行性）：**
- 引擎 `apply_risk` 以**精确字符串** `reason="stop_loss"` / `"take_profit"` 强平，且**止损优先**（`simulator.py:180` 注释「止损优先（保守）」）；成交带 `stop_loss_price/take_profit_price`（由入场价×比例推导）。故 §6.1 的断言有据、`tradeExplain` 的 reason 映射有据。
- `run_single_backtest` 的**三处调用方**（`api/strategies.py` route、`run_multi_sample_backtest`、`agent/tools/strategy_tools.py`）**全部关键字传参** → 新增带默认值的 `risk_override` 参数对既有调用**零影响**（多样本 / Agent 工具默认不传，零回归）。
- `_prepare_strategy` 的 `if params_override:` 对**空 dict 为假** → 空覆盖时 `cls(params)` 与现状字节级等价，零回归成立。
- `SimBroker.set_risk` 用 `_positive_float`（`<=0`/非有限 → None）归一化，是覆盖注入的天然防御底座。

**问题清单（逐条附严重度与处置）：**

| # | 维度 | 严重度 | 问题 | 处置 |
|---|------|--------|------|------|
| R-1 | 一致性 / 完备性 | **P1**（已修） | `StrategyPanel.runBacktest` 被**组合**(`StrategyPanel.tsx:549`)与**原子**(`:612`)两条 `backtest.mutate` **共用**，且 `backtestConfig` 是**全局单例持久化对象**（`store.ts:225`，进 partialize `:960`，`onSuccess` 处 `setBacktestConfig`）。v1 的 #7「在 `config` 分支**无条件**并入 `riskOverrideBody(config)`」会导致：用户在原子策略设过的**有效**止盈止损被持久化后，切到组合弹窗——该分区被 `riskOverrideEnabled=false` 隐藏、用户无从清除——**组合回测请求体仍会携带该覆盖**。虽后端 combo 分支静默忽略（今日无功能损害），但①**违背方案自身「combo 不透传」契约**（决策 §2.2-3 / 风险表首行 / §6.3 DoD）、②静默吞掉用户意图（违 CLAUDE.md「错误显式传播」）、③一旦后续给 combo 加支持即成潜伏 bug。**v1 的「隐藏 UI」这一缓解并不能阻止 wire 携带**（因共用 runBacktest + 共用持久化 cfg）。 | 已就地修复：把 `riskOverrideEnabled` **同源信号透传进 mutation vars** → `runBacktest` 仅在**非组合**时注入 `riskOverrideBody`；组合请求体**永不含** SL/TP。见改写后的 §三 #7 与 §2.2-3 / §2.4。 |
| R-2 | 完备性（UX 陷阱） | P2（已修，防御性） | R-1 的一体两面：`validateConfig(cfg)` 也被两弹窗**共用同一 cfg**。组合弹窗隐藏了 SL/TP 输入，但**理论上**若持久化里残留越界 SL/TP，组合的 `validateConfig` 会对**看不见的字段**报硬错误 → 组合按钮被禁用且无可见原因。**核验后判为不可达**：`setBacktestConfig` 仅在 `onSuccess`（`StrategyPanel.tsx:275`）触发，而越界值会被前端 disabled-Run + 后端 `Field(gt/lt)` 双重拦截、根本无法落盘。 | 仍就地加固：`validateConfig` 增 `riskOverrideEnabled` 形参，仅在开启时校验 SL/TP 边界，令两弹窗对风控字段的行为对称、杜绝未来 foot-gun。见 §三 #5。 |
| R-3 | 一致性（错误显式传播） | P2（文档化） | 直连 API 对**组合**策略 POST `stopLossPct` 仍被后端 combo 分支**静默忽略**（前端修复后 UI 已不产生此请求，但契约层仍「接受即丢弃」，软违 CLAUDE.md §五）。 | v1 记为**已知限制**并在 §八 标注；不强制后端 422（需在校验前先查 kind、收益低、增分支复杂度）。列为后续可选增强：`combo + 非空 risk_override → 400`。 |
| R-4 | 完备性（测试精度） | P2（已修） | §6.1-4「逐字段一致」若直接比较 `run_single_backtest` 两次返回 dict 会因 `runId` 自增而**假失败**。 | 已就地修：零回归断言改为比对 **metrics / equity / trades（排除 `runId` 与落库时间戳）**。 |
| R-5 | 一致性（命名 / 单位） | P2（已注） | `BacktestConfig.stopLossPct` 存**百分数**(`5`)，线上同名字段 `stopLossPct` 存**小数**(`0.05`)——**同名异单位**，靠 `riskOverrideBody` 单点转换兜底。 | 保留（转换集中且已单测），但要求两处均加显式单位注释；并新增硬约束：前端 `validateConfig` 的百分数边界（SL `<100` / TP `<1000`）须与后端 `Field(gt=0, lt=1/10)` 小数边界**互为镜像**，避免 UI 放行却被 422。见 §三 #5 / §四 4.1。 |
| R-6 | 可行性（叙述一致性） | P2（已修） | §2.3 步骤 5 写作 `override = {**(params or {}), **risk_override}` 未对 `risk_override` 做 `or {}` 兜底；虽 §三 #3 已含 `(risk_override or {})`，叙述与清单需一致以免开发误读。 | 已就地统一为 `{**(params or {}), **(risk_override or {})}`，并显式说明「双空 → 传空 dict → `_prepare_strategy` 假值短路 → 字节级等价」。 |

## 一、Overview / 概述

右侧策略面板（`StrategyPanel` → `StrategyDetail` / `ComboStrategyDetail`）点击「运行回测」会弹出一个两步向导弹窗 `BacktestConfigModal`。第一步「配置」当前只暴露**回测区间、K 线周期、初始资金、手续费、滑点、杠杆**六项，并不暴露任何止盈 / 止损开关。用户因此无法在「跑一次回测」这个动作里临时给策略叠加或调整止盈止损，只能退回到策略参数编辑器里去**持久化地**改动该策略保存的参数（`params_json`）。对「我想看看这个策略加上 5% 止盈、3% 止损后表现如何」这类探索式诉求，现状既不顺手、又有副作用（改动会写回策略、影响之后所有回测与实盘）。

需要澄清的一个关键事实：**回测引擎本身早已支持止盈止损**。撮合层 `engine/simulator.py::SimBroker` 通过 `RiskParams(stop_loss_pct, take_profit_pct, max_hold_bars)` 在每根 K 线内做触发价强平（止损优先、止盈次之、`max_hold` 到期次根开盘平），`BaseStrategy.risk_params()` 从 `self.params` 读取这三项，`run_backtest()` 已在跑撮合前调用 `broker.set_risk(strategy.risk_params())`。事实上多数原子策略（`long_shadow_reversal`、`ema_cross_scalper`、`vwap_rsi_momentum`、`momentum_breakout`、`bollinger_reversion`、`trend_pullback_*`、`range_scalping`）已在 `default_params` 里内置了 `stop_loss_pct` / `take_profit_pct`。真正缺失的不是引擎能力，而是**「运行回测」弹窗这个入口没有把止盈止损开放给用户，也没有提供「本次回测临时覆盖」的能力**；且部分策略只内置了止损、没有止盈。

因此本方案要交付的是：在 `BacktestConfigModal` 第一步新增一个「止盈止损（可选）」分区，两个默认留空的百分比输入；留空即「不覆盖」——完全沿用策略自带风控（等价于当前行为、字节级不变）；填入即作为**本次回测的临时覆盖**，经 `POST /strategies/{id}/backtest` 下发，在服务层合并进策略实例参数、由既有 `risk_params()` → `SimBroker` 生效，**不写回策略、不持久化到策略行**。产出是一次「所见即所测」的、带止盈止损的更真实回测，且对未使用该功能的既有流程零影响。

---

## 二、Technical design / 技术方案

### 2.1 现状与端到端链路（已验证）

单策略「运行回测」的调用链：

```
StrategyDetail / ComboStrategyDetail
  → BacktestConfigModal (onRun(cfg))
  → StrategyPanel: backtest.mutate({id, params?, config: cfg})
  → runBacktest(): api.backtest(id, { instId, bar, startTs, endTs, initialCapital, fees, slippage, leverage })
  → POST /api/py/strategies/{id}/backtest  (schemas.BacktestReq)
  → api/strategies.py::backtest → services.run_single_backtest(...)
      ├─ 组合(kind==combo) → _run_saved_combo → engine/combo.py（不经 SimBroker 的 RiskParams 路径）
      └─ 原子 / 因子 → _prepare_strategy(params_override) → _execute_and_persist → run_backtest()
                       run_backtest: risk = strategy.risk_params(); broker.set_risk(risk); 逐根 broker.apply_risk(i,row,risk)
```

关键已存在的扩展点：
- `services.run_single_backtest(..., params: dict | None = None)` 已接受一个「策略参数覆盖」，经 `_prepare_strategy(strategy_id, params_override)` 以 `{**默认, **保存, **override}` 合并后 `cls(params)` 实例化。**当前 `/backtest` 路由并未传 `params`**，该通道对本端点是空闲的。
- `BaseStrategy.risk_params()` 直接读 `self.params["stop_loss_pct" | "take_profit_pct" | "max_hold_bars"]`。这意味着**把风控作为一次性 params 覆盖注入，即可零改引擎地生效**——这正是引擎设计好的扩展点。
- `SimBroker.set_risk()` 内部用 `_positive_float` / `_positive_int` 归一化（`<=0` 或非有限值 → None，即关闭），是天然的防御底座。

### 2.2 核心设计决策

1. **覆盖语义（override，而非新增独立风控）**：弹窗两个字段作用于「本次回测」的策略参数覆盖。留空 → 不进入覆盖字典 → 策略自带风控原样生效（字节级等价现状）；填入 → 覆盖该字段。**只合并非 `None` 的键**，杜绝「显式 None 把策略自带止损覆盖成关闭」的误伤。禁用策略自带止盈止损不在 v1 范围（需求是「新增」而非「移除」）。

2. **单位契约（percent in UI, fraction on the wire）**：
   - 前端 `BacktestConfig.stopLossPct / takeProfitPct` 存**用户输入的百分数**（`5` 表示 5%），与「止损%」标签直觉一致。
   - 线上契约（`api.backtest` body / `BacktestReq` / 引擎 `RiskParams`）统一为**小数**（`0.05`）。转换只发生在一个纯函数边界 `backtestConfig.ts::riskOverrideBody(cfg)`（`/100`、丢弃空值），集中、可单测，避免百分比 / 小数在多层间漂移。
   - 该小数契约与既有 `Trade.stopLossPct`（成交序列化里 `t.stop_loss_pct`，本就是小数）一致，前后端同构。

3. **组合策略（combo）范围**：`run_single_backtest` 对 `kind==combo` 在 `_prepare_strategy` **之前**就分流到 `_run_saved_combo` → `engine/combo.py`，后者独立回测各成员、按轮动拼组合净值，**根本不经 `SimBroker` 的 `RiskParams` 路径**（`engine/combo.py` 无 `set_risk` / `RiskParams` 调用）。给「轮动组合」施加统一止盈止损语义不清、改动面大，**v1 明确不支持 combo**。为避免「显示了却不生效」的 UX 陷阱，弹窗在组合详情下**不渲染**止盈止损分区（`riskOverrideEnabled=false`）。**（v2·R-1 关键修正）** 仅隐藏 UI 不足以阻止 wire 携带覆盖：`StrategyPanel.runBacktest` 被组合与原子**共用**、`backtestConfig` 又是**全局单例持久化**，故原子会话设过的止盈止损会残留并被 seed 进组合弹窗。因此组合隔离必须**贯穿到 wire**——见 §2.4「组合隔离」。combo 支持列为后续增量。

4. **不落策略行、可留痕**：覆盖只在内存合并进策略实例，**绝不** `updateStrategy` 写回 `params_json`。为可复现，落库的 `strategy_runs.config_json` 追加 `riskOverride` 字段（加法、可选）；此外每笔平仓的 `TradeRecord` 本就带 `stop_loss_pct/take_profit_pct/stop_loss_price/take_profit_price/reason`，触发时成交表即可见「止损 / 止盈」出场，天然留痕。

5. **多样本 / 对比 / Walk-forward 范围**：本次仅接 `/strategies/{id}/backtest` 单策略路径（需求锚定的「运行回测」弹窗）。`multi-backtest`（复用 `run_single_backtest`，默认不传覆盖故零影响）、`compare`、`walk-forward` 不接入，列为后续。

6. **`max_hold_bars` 不入 v1**：需求聚焦「止盈止损百分比」。`max_hold_bars`（时间止损）虽同通道几乎零成本，但为收敛范围仅作扩展说明，不在本方案实现。

### 2.3 时序（填入止盈止损后的一次回测）

1. 用户在弹窗「止盈止损（可选）」输入止盈 `5`、止损 `3`（%），点「开始回测」。
2. `onRun(cfg)` → `backtest.mutate`（vars 带 `riskOverrideEnabled=true`）→ `runBacktest`：`body = { ...区间/周期/成本, ...riskOverrideBody(cfg) }`，其中 `riskOverrideBody` 产出 `{ stopLossPct: 0.03, takeProfitPct: 0.05 }`（小数、丢空）。
3. `POST /strategies/{id}/backtest`，`BacktestReq` 用 `Field(gt=0, lt=...)` 校验（越界 422）。
4. 路由把两字段组装为 `risk_override={"stop_loss_pct":0.03, "take_profit_pct":0.05}`（丢 None；两者皆空则 `risk_override=None`），传 `run_single_backtest(risk_override=...)`。
5. 非 combo：`override = {**(params or {}), **(risk_override or {})}` → `_prepare_strategy` 合并 → `cls(params)` → `strategy.risk_params()` 得到覆盖后的 `RiskParams`。**（v2·R-6）** 双空时 `override == {}`，`_prepare_strategy` 内 `if params_override:` 对空 dict 短路 → 与现状字节级等价（零回归的实现级保证）。
6. `run_backtest` → `broker.set_risk(risk)`；逐根 `broker.apply_risk` 在触发价强平，成交带 `reason="stop_loss"/"take_profit"`。
7. `_execute_and_persist` 落库时**仅在覆盖非空时**把 `config_json.riskOverride` 记录本次覆盖（空则不写，历史 run 形状不变）；响应沿用既有 `{runId, metrics, extendedMetrics, 覆盖字段}`。
8. 前端结果屏渲染指标 / 净值；成交表展开的触发解释把 `stop_loss/take_profit` 译为「触发止损 / 止盈平仓」。

### 2.4 组合隔离（wire + validation，v2·R-1 / R-2）

`riskOverrideEnabled` 是**单一门控信号**（`BacktestConfigModal` prop，原子 / 因子默认 `true`、组合显式 `false`），必须**同源贯穿三层**，而不仅是「隐藏 UI」：

1. **渲染层**：`false` → `ConfigStep` 不渲染「止盈止损（可选）」分区（用户无从设置 / 清除）。
2. **校验层**：`validateConfig(cfg, warmupBars, riskOverrideEnabled)` → `false` 时**跳过** SL/TP 边界校验（组合永不因看不见的字段被禁用「开始回测」，R-2 防御）。
3. **wire 层（关键）**：`riskOverrideEnabled` 随 `backtest.mutate` vars 透传到 `runBacktest`；**仅在 `true` 时**把 `...riskOverrideBody(config)` 并入 `api.backtest` body。组合调用点（`StrategyPanel.tsx:549`）传 `riskOverrideEnabled=false` → 组合请求体**永不含** `stopLossPct/takeProfitPct`，与「combo 不透传」契约一致（R-1）。

> 为何不能只靠「组合无 `params`」隐式判定：`runBacktest` 现以 `if (params)` 决定是否 `updateStrategy`，而因子策略也带 `params`——用 `params` 是否存在来兼判 combo 会把两个无关概念耦合、且脆弱。故采用**显式** `riskOverrideEnabled` 单一信号，与弹窗 prop 同源。

---

## 三、File / module change plan / 文件变更清单

| # | 文件 | 改动 | 一句话意图 |
|---|------|------|-----------|
| 1 | `services/quant/app/schemas.py` | 改 | `BacktestReq` 新增 `stopLossPct: float\|None = Field(default=None, gt=0, lt=1)`、`takeProfitPct: float\|None = Field(default=None, gt=0, lt=10)`（小数契约 + 边界校验）。 |
| 2 | `services/quant/app/api/strategies.py` | 改 | `backtest` 端点把两字段组装为 `risk_override`（丢 None）并透传 `run_single_backtest`。 |
| 3 | `services/quant/app/agent/services.py` | 改 | `run_single_backtest` 加 `risk_override: dict\|None=None`：非 combo 分支 `override={**(params or {}),**(risk_override or {})}` 合并进 `_prepare_strategy`；透传 `_execute_and_persist`。`_execute_and_persist` 加同名可选参并把 `riskOverride` 写入 `config`。combo 分支不透传（文档化）。 |
| 4 | `apps/web/lib/types.ts` | 改 | `BacktestConfig` 新增可选 `stopLossPct?: number\|null`、`takeProfitPct?: number\|null`（存**百分数**，注释单位）。 |
| 5 | `apps/web/lib/backtestConfig.ts` | 改 | `DEFAULT_BACKTEST_CONFIG` 补 `stopLossPct:null, takeProfitPct:null`；**（v2·R-2/R-5）** `validateConfig(cfg, warmupBars?, riskOverrideEnabled = true)` 仅在 `riskOverrideEnabled` 时校验百分比边界（SL `>0 && <100`、TP `>0 && <1000`——**与后端 `Field(gt=0, lt=1/10)` 小数边界互为镜像**），空值放行；新增纯函数 `riskOverrideBody(cfg)`（%→小数、丢弃 `null/NaN/<=0`，供 body 拼装）。 |
| 6 | `apps/web/lib/api.ts` | 改 | `backtest` body 类型补 `stopLossPct?: number; takeProfitPct?: number;`（小数）。 |
| 7 | `apps/web/components/Strategy/StrategyPanel.tsx` | 改 | **（v2·R-1 关键）** `backtest.mutate` vars 增 `riskOverrideEnabled?: boolean`；`runBacktest(id, params, config, …, riskOverrideEnabled)` **仅在 `riskOverrideEnabled === true`（非组合）时**把 `...riskOverrideBody(config)` 并入 `api.backtest` body；**组合调用点（`:549`）显式传 `riskOverrideEnabled=false`**，原子调用点（`:612`）传 `true`（或依默认）。组合请求体永不含 SL/TP。 |
| 8 | `apps/web/components/Strategy/BacktestConfigModal.tsx` | 改 | 组件新增 `riskOverrideEnabled?: boolean`（默认 `true`）；`ConfigStep` 据此渲染「止盈止损（可选）」分区（两个可空百分比输入 `OptionalPctField`，**空串→`null` 而非 `NaN`**，留空占位「留空＝用策略自带」）；`patch` 支持两字段；组件内 `validateConfig(cfg, warmupBars, riskOverrideEnabled)` 传入门控（R-2）。 |
| 9 | `apps/web/components/Strategy/ComboStrategyDetail.tsx` | 改 | 渲染 `BacktestConfigModal` 处传 `riskOverrideEnabled={false}`；且 `onRunBacktest`→`backtest.mutate` 处传 `riskOverrideEnabled=false`（组合 wire 隔离，R-1）。 |
| 10 | `apps/web/lib/tradeExplain.ts` | 改 | `inferKindFromReason` / 回落 `headline` 识别 `stop_loss`/`take_profit`，译为「触发止损 / 止盈平仓」（成交解释可读）。 |
| 11 | `services/quant/tests/test_api.py` | 改/增 | 端点带 `stopLossPct/takeProfitPct` 回测→成交含 `stop_loss`/`take_profit` 出场；越界值→422。 |
| 12 | `services/quant/tests/test_backtest_services.py`（或就近既有服务测试） | 改/增 | `run_single_backtest(risk_override=...)` 覆盖策略自带风控（成交序列改变）；**（v2·R-4）** 零回归断言比对 **metrics / equity / trades（排除自增 `runId` 与落库时间戳）**，验证 `risk_override=None` 时引擎风控输出与现状一致。 |
| 13 | `apps/web/lib/backtestConfig.test.ts` | 改/增 | 默认含 `null` 风控；`validateConfig` 越界报错；`riskOverrideBody` %→小数、丢空。 |
| 14 | `apps/web/components/Strategy/BacktestConfigModal.test.tsx` | 改/增 | `riskOverrideEnabled` 开→渲染分区且 `onRun` 携带两字段；关→不渲染。 |

> 说明：`engine/simulator.py`、`engine/backtester.py`、`strategies/base.py` **无需改动**——止盈止损能力已存在，本方案只补「入口 + 覆盖注入 + 展示」。

---

## 四、Interface design / 接口设计

### 4.1 REST：`POST /api/py/strategies/{strategy_id}/backtest`

请求体在既有基础上**加法**新增两可选字段（小数、留空即不覆盖）：

```jsonc
{
  "instId": "BTC-USDT-SWAP",
  "bar": "1H",
  "startTs": 1710000000,
  "endTs": 1712000000,
  "initialCapital": 10000,
  "fees": 5,
  "slippage": 2,
  "leverage": 1,
  "stopLossPct": 0.03,      // 可选，(0,1)，小数（0.03=3%）；留空=不覆盖策略自带止损
  "takeProfitPct": 0.05     // 可选，(0,10)，小数（0.05=5%）；留空=不覆盖策略自带止盈
}
```

响应形状不变：`{ runId, metrics, extendedMetrics, effectiveCandleCount, requestedBars, truncated }`。校验失败（`<=0` 或越界）返回 FastAPI 422。

> **边界镜像约束（v2·R-5）**：后端 `stopLossPct ∈ (0,1)`、`takeProfitPct ∈ (0,10)`（小数）必须与前端 `validateConfig` 的百分数边界（SL `∈ (0,100)`、TP `∈ (0,1000)`）**逐一互为镜像**（同 `gt`/`lt` 语义），否则会出现「UI 放行、后端 422」的错位。二者是同一契约的两种单位表达。
>
> **combo 已知限制（v2·R-3）**：若对 `kind==combo` 的策略直接调用本端点并携带 `stopLossPct/takeProfitPct`，后端 combo 分支（`_run_saved_combo`）**不经 RiskParams 路径、静默忽略**该覆盖（不报错、响应不回显）。前端经 §2.4 wire 隔离已不会产生此请求；v1 记为已知限制（见 §八），combo 支持时再改为显式生效或 400 拒绝。

### 4.2 前端 API 客户端 `api.backtest(id, body)`

`body` 类型新增 `stopLossPct?: number; takeProfitPct?: number;`（小数）。由 `runBacktest` 经 `riskOverrideBody(config)` 注入，字段缺省即不出现在 JSON 里。

### 4.3 组件 props

`BacktestConfigModal` 新增：`riskOverrideEnabled?: boolean`（默认 `true`）。`StrategyDetail`（原子 / 因子）走默认 `true`；`ComboStrategyDetail` 显式传 `false`。**（v2·R-1）** 该 prop 不止管「是否渲染分区」，而是 §2.4 所述**同源门控三层**（渲染 / 校验 / wire）的唯一信号；`ComboStrategyDetail` 除给弹窗传 `false` 外，还须在其 `onRunBacktest`→`backtest.mutate` 处一并把 `riskOverrideEnabled=false` 透传到 `runBacktest`，确保组合请求体不携带 SL/TP。

### 4.4 纯函数签名

```ts
// backtestConfig.ts
export function riskOverrideBody(cfg: BacktestConfig):
  { stopLossPct?: number; takeProfitPct?: number };  // %→小数，丢弃 null/NaN/<=0
```

---

## 五、Data model / 数据模型

- **`BacktestReq`（Pydantic）**：见 4.1；两字段小数、`Field` 约束。
- **`BacktestConfig`（TS，持久化于 Zustand `aragon-ui` 的 `backtestConfig`）**：新增两可选**百分数**字段。为加法可选字段，**无需升 store 版本**：旧持久化配置缺字段 → `undefined` → 视为未设置，向后兼容。
- **`RiskParams`（既有，无改动）**：`stop_loss_pct/take_profit_pct/max_hold_bars` 小数 / 整数。覆盖字典键名须与之一致（`stop_loss_pct`、`take_profit_pct`）。
- **`strategy_runs.config_json`（既有列，无迁移）**：`_execute_and_persist` 追加 `"riskOverride": {stop_loss_pct?, take_profit_pct?}`；仅在非空时写入，历史 run 不受影响。**零 DB schema 变更 / 零迁移。**
- **`TradeRecord` / `Trade`（既有，无改动）**：平仓已带 `stop_loss_pct/take_profit_pct/stop_loss_price/take_profit_price/reason`，触发即在成交表可见。

---

## 六、Testing & acceptance criteria / 测试与验收

### 6.1 后端
1. `test_api.py::` 构造能确定性触发止盈或止损的 K 线 + 策略，`POST /strategies/{id}/backtest` 带 `stopLossPct/takeProfitPct`，断言返回 `runId` 且该 run 的成交里出现 `reason in {"stop_loss","take_profit"}`、`take_profit_price`/`stop_loss_price` 由入场价按覆盖比例正确推导。
2. 校验：`stopLossPct=0` / `-0.1` / `>=1`、`takeProfitPct<=0` → 422。
3. `run_single_backtest(risk_override={"take_profit_pct":X})` 覆盖策略自带止盈（对同一策略、开 / 不开覆盖，成交序列不同）。
4. **零回归（v2·R-4）**：`risk_override=None` 时 `run_single_backtest` 的 **metrics / equity / trades 与改动前一致**（**排除自增 `runId` 与落库时间戳**，避免假失败）；沿用 `test_simulator.py` 已覆盖的引擎风控行为，不重复测引擎，仅测「注入通道未触发时无副作用」。

### 6.2 前端
5. `backtestConfig.test.ts`：`DEFAULT_BACKTEST_CONFIG` 含 `stopLossPct:null/takeProfitPct:null`；`validateConfig` 对越界（`<=0`、SL `>=100`、TP `>=1000`）产出硬错误、空值放行；**（v2·R-2）** `validateConfig(cfg, warmup, /*riskOverrideEnabled*/ false)` 对同样的越界值**不报错**（组合门控生效）；`riskOverrideBody` 把 `{5,3}` → `{takeProfitPct? / stopLossPct?}` 小数并丢空。
6. `BacktestConfigModal.test.tsx`：`riskOverrideEnabled` 默认渲染止盈止损分区；填入后点「开始回测」，`onRun` 收到的 `cfg` 携带两字段；`riskOverrideEnabled={false}` 时分区不出现。
7. **（v2·R-1）** wire 隔离测试：组合路径（`riskOverrideEnabled=false`）即便 `config` 残留 `stopLossPct/takeProfitPct`，`runBacktest` 拼出的 `api.backtest` body **不含** SL/TP；原子路径（`true`）则含。可在 `StrategyPanel` 的 `runBacktest`（导出为可测纯函数或经组件测试）上断言。
   > 注：组件测试须置 `NODE_ENV=test`（本仓约定，避免 React act() 报错）。

### 6.3 验收标准（Definition of Done for feature）
- 「运行回测」弹窗（原子 / 因子策略）出现「止盈止损（可选）」两输入，默认空。
- 空 → 回测行为与现状一致；填 → 回测确实按覆盖的止盈止损强平，指标 / 净值 / 成交随之变化。
- 组合策略弹窗不显示该分区。
- 覆盖不写回策略 `params_json`（回测后策略参数不变）。
- 后端 + 前端既有测试全绿，新增测试通过；项目可编译 / 启动。

---

## 七、Risks & mitigations / 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| **组合「显示却不生效」/ wire 泄漏** | 用户在组合上设了止盈止损但无效果；或原子会话残留的覆盖经共用 `runBacktest` + 共用持久化 cfg 泄漏进组合请求体（v2·R-1） | `riskOverrideEnabled` **同源门控三层**（渲染 / 校验 / wire，§2.4）：组合弹窗不渲染分区，`validateConfig` 跳过 SL/TP 校验，`runBacktest` 组合路径**不注入** `riskOverrideBody` → 请求体永不含 SL/TP；文档明确 combo 不在 v1。 |
| **百分比 / 小数单位漂移** | 5% 被当 500% 或 0.05% | 单位转换集中在 `riskOverrideBody` 单一纯函数 + 单测；UI 标签「止盈%」「止损%」+ 占位说明；后端 `Field(gt=0, lt=1/10)` 兜底越界。 |
| **误伤策略自带风控** | 留空却把策略止损覆盖成关闭 | 只合并**非 None** 键；默认字段为 `null`；零回归测试锁 `risk_override=None` 等价现状。 |
| **持久化配置向后兼容** | 老用户 `backtestConfig` 无新字段 | 加法可选字段，`undefined` 即未设置，无需升 store 版本。 |
| **同根内止损止盈同时命中** | 结果依赖判定顺序 | 引擎既有「止损优先（保守）」逻辑，本方案不改，语义稳定。 |
| **触发价成交的乐观性** | 假设恰好在止损 / 止盈价成交，跳空穿透未建模 | 属引擎既有语义、非本功能引入；在文档标注为已知限制，指标解读时提示保守看待。 |
| **成交表出场原因为英文** | `stop_loss`/`take_profit` 直显英文 | `tradeExplain.ts` 增中文回落标签，成交解释可读。 |

---

## 八、Out of scope / 明确不做（v1）

- 组合（regime_rotation）止盈止损。
- **combo 端点对 `stopLossPct/takeProfitPct` 的显式拒绝（v2·R-3）**：v1 后端 combo 分支对直连 API 携带的覆盖**静默忽略**（前端已 wire 隔离、不产生此请求），记为已知限制；combo 支持时再改为显式生效或 400 拒绝。
- `max_hold_bars`（时间止损）弹窗入口（同通道，后续几乎零成本可加）。
- `compare` / `multi-backtest` / `walk-forward` 接入止盈止损覆盖。
- 「禁用策略自带止盈止损」的显式关闭开关（当前留空＝沿用、非关闭）。
- 追踪止损（trailing stop）等高级风控。

---

## 评审结论（Subtask #1 · Review Verdict）

**结论：有条件通过（Approved with conditions）。**

方案的核心技术路线经源码逐条核验为**可行且规模合理**：回测引擎（`SimBroker` + `RiskParams`）与 `BaseStrategy.risk_params()` 早已支持止盈止损，本方案只补「弹窗入口 + 一次性覆盖注入 + 中文展示」，**零引擎改动、零 DB 迁移、不写回策略**，且空覆盖路径经 `_prepare_strategy` 的 `if params_override:` 假值短路保证**字节级零回归**。无 P0。

评审发现 **1 项 P1（R-1：组合 wire 泄漏）与 5 项 P2**，其中 P1 及可低成本收敛的 P2（R-2/R-4/R-5/R-6）**已在本文（v2）就地修复**：新增 §2.4「组合隔离」把 `riskOverrideEnabled` 提升为**贯穿渲染 / 校验 / wire 三层的单一门控信号**；据此改写了 §三 #5/#7/#8/#9、§四 4.1/4.3、§2.2-3、§2.3、§6.1/§6.2 与风险表。**当前无未解决的 P0 / P1。**

**放行条件（代码开发节点 Subtask #2 必须逐条落实，否则视为未完成）：**
1. **组合 wire 隔离（R-1，硬性）**：`riskOverrideEnabled` 必须同源贯穿三层；组合调用点（`StrategyPanel.tsx:549` / `ComboStrategyDetail`）经 `backtest.mutate` vars 传 `false`，`runBacktest` 仅在 `true` 时注入 `riskOverrideBody`。组合请求体**不得**出现 `stopLossPct/takeProfitPct`（须有 §6.2-7 测试守门）。
2. **前后端边界镜像（R-5）**：前端 `validateConfig` 百分数边界（SL `(0,100)`、TP `(0,1000)`）与后端 `Field(gt=0, lt=1/10)` 小数边界**逐一对应**，杜绝「UI 放行、后端 422」。
3. **校验门控（R-2）**：`validateConfig` 增 `riskOverrideEnabled` 形参，组合（`false`）跳过 SL/TP 校验。
4. **零回归断言排除易变字段（R-4）**：比对 metrics / equity / trades，排除 `runId` 与落库时间戳。
5. **combo 静默忽略记为已知限制（R-3）**：v1 不强制后端拒绝，但须在实现与文档中保留 §八 / §4.1 的说明，避免误解为「已生效」。
6. **单位注释（R-5）**：`BacktestConfig.stopLossPct/takeProfitPct`（百分数）与 wire / `RiskParams`（小数）两处均加显式单位注释，转换仅经 `riskOverrideBody` 单点发生。

以上条件全部为「实现须忠实于 v2 已修订设计」，不涉及方案返工。达成后即可进入编码。
