# 诊断报告：`momentum_breakout` 在回测与实时 paper/live 下跑成两套策略

- **Bug slug**: `momentum-breakout-prepare-resets-trailing-stop`
- **诊断节点**: Subtask #0（问题定位与分析）
- **结论一句话**: `MomentumBreakout.prepare()` 在
  `services/quant/app/strategies/momentum_breakout.py:49-50` 把跟踪止损的运行态
  `self._peak / self._trough` 置零。回测里 `prepare()` 只调用一次、运行态由
  `on_candle()` 跨整段持仓累积；而 paper/live 里**每根已确认 K 线都重新调用一次
  `prepare()`**，导致运行态被逐根重置，跟踪止损从「持仓期间最高/最低价」退化成「当根
  盘中极值」——同一策略、同一参数、同一行情，回测盈利、实盘亏损。

---

## 1. 问题描述（Problem description）

策略在**回测**与**实时 paper/live** 两条路径下，必须跑出完全一致的行为；否则「回测看起来
赚钱、实盘跑起来根本不是同一个策略」。

`momentum_breakout`（暴涨动量突破）依赖两个**持仓运行态**变量做跟踪止损：

- `self._peak`：做多后记录持仓期间的最高价；当 `c <= _peak*(1-trail)` 时触发跟踪止损平多。
- `self._trough`：做空后记录持仓期间的最低价；当 `c >= _trough*(1+trail)` 时触发跟踪止损平空。

问题在于：`prepare()` 本应是「只算指标列」的纯函数，但该策略在 `prepare()` 里把
`_peak/_trough` 重置了。回测路径下 `prepare()` 只调用一次，碰巧无害；实时路径下
`prepare()` **逐根调用**，于是每根 K 线都把跟踪止损基准抹掉，跟踪止损名存实亡。

后果：回测中跟踪止损在高位锁定利润离场；实盘中跟踪止损永不按预期触发，持仓一路扛到
**硬止损**（入场价 -3%）才离场。两条路径产出不同的成交、不同的离场价、相反的盈亏。

---

## 2. 复现步骤（Reproduction steps）

已提供一个**自包含、可直接运行**的最小复现脚本：
`docs/diagnoses/momentum-breakout-prepare-resets-trailing-stop/repro_divergence.py`

它在**同一组 K 线、同一参数、同一撮合引擎 `SimBroker`** 下对比三种配置：

| 配置 | 撮合管线 | `prepare()` 频次 | 含义 |
|---|---|---|---|
| **A** | 真实 `backtester.run_backtest()` | 一次 | 回测真相（对照基准）|
| **B** | 忠实复刻的 paper 撮合循环 | **每根** | 现状 = bug |
| **C** | 同一 paper 循环 | 一次 | 隔离变量（与 B 唯一差异就是 `prepare()` 频次）|

复刻循环逐字对齐 `paper/session.py:303-319` 的四阶段顺序
（`fill_pending → apply_risk → mark → on_candle/submit`），仅去掉 DB/WS，并把
`prepare()` 频次做成开关。

运行（仓库根目录）：

```bash
python docs/diagnoses/momentum-breakout-prepare-resets-trailing-stop/repro_divergence.py
```

> Windows 控制台若出现中文乱码，是 GBK code page 显示问题，不影响数值；可加
> `PYTHONUTF8=1 PYTHONIOENCODING=utf-8` 前缀获得清晰输出。

### 实测输出（真实日志，非推测）

```
A) 真实回测 run_backtest()  —— prepare 仅一次（对照真相）
    ts=1560  long   price= 104.000  pnl=    +0.00  reason=暴涨突破(累计4.0%)
    ts=1740  close  price= 117.600  pnl= +1176.92  reason=跟踪止损(多)
  最终权益: 11176.92  (初始 10000)
==============================================================================
B) paper 撮合循环 + prepare 每根  —— 现状 = BUG
    ts=1560  long   price= 104.000  pnl=    +0.00  reason=暴涨突破(累计4.0%)
    ts=2340  close  price= 100.880  pnl=  -270.00  reason=stop_loss
  最终权益: 9730.00  (初始 10000)
==============================================================================
C) paper 撮合循环 + prepare 一次  —— 隔离变量（仅 prepare 频次与 B 不同）
    ts=1560  long   price= 104.000  pnl=    +0.00  reason=暴涨突破(累计4.0%)
    ts=1740  close  price= 117.600  pnl= +1176.92  reason=跟踪止损(多)
  最终权益: 11176.92  (初始 10000)
==============================================================================
结论: A == C ? True   (复刻管线无偏差，prepare 一次时与回测逐笔一致)
      B == C ? False   (False = 唯一变量 prepare 频次导致策略跑成两套)
      回测盈亏 +1176.92  vs  paper 盈亏 -270.00
```

**解读**：
- **A ≡ C**（逐笔一致）证明复刻的 paper 管线没有任何偏差——回测与「prepare 只调一次的
  paper」产生完全相同的成交。
- **B ≠ C** 把唯一变量锁定为 `prepare()` 调用频次：把它从「一次」改成「每根」，跟踪止损
  就在 `ts=1740` 高位离场（+1176.92）变成在 `ts=2340` 被硬止损扫出（-270.00）。
- 同一份 K 线、同一份参数：**回测 +11.77%，实盘 -2.70%**，离场原因从
  `跟踪止损(多)` 变成 `stop_loss`，离场价从 117.6 变成 100.88。

### 真实环境下的复现路径（无需脚本）

1. 启动 quant 服务，对 `momentum_breakout` 跑一次回测，记下其跟踪止损成交。
2. 用同一标的/周期/参数启动一个 paper 或 replay 会话
   （`POST /paper` → `PaperSession`，见 `paper/session.py`）。
3. 喂入与回测相同的一段历史（replay 即直接拉真实历史逐根驱动）。
4. 对比两边成交：持仓中只要出现「冲高后缓慢回落」的行情，回测会按峰值触发跟踪止损，
   paper 不会——成交序列发散。

---

## 3. 根因分析（Root cause analysis，含证据）

### 3.1 两条路径对 `prepare()` 的调用次数不同

**回测路径** —— `prepare()` 在循环外，只调用一次：

```python
# services/quant/app/engine/backtester.py:60
df = strategy.prepare(df, bar)
...
# services/quant/app/engine/backtester.py:69-78
for i in range(n):
    ...
    if i >= warmup and i < n - 1:
        ctx = broker.context()
        signals = strategy.on_candle(df, i, ctx)   # 运行态由 on_candle 跨根累积
```

**实时 paper/live 路径** —— `prepare()` 在「每根已确认 K 线」处理中被重复调用：

```python
# services/quant/app/paper/session.py:290  （位于 _process_confirmed，每根确认 K 线触发一次）
df = self.strategy.prepare(candles_to_df(list(self._candles)), self.bar)
...
# services/quant/app/paper/session.py:315-319
if i >= self.strategy.warmup():
    ctx = self.broker.context()
    signals = self.strategy.on_candle(df, i, ctx)
```

`_process_confirmed` 既服务实时段（`_on_confirmed`，`session.py:266-268`），也服务
**预热回放**（`_prime_history`，`session.py:120-141`）——两者都逐根调用 `prepare()`。
也就是说：连 paper 会话内部的 seeded 微回测，都和离线回测器跑得不一样。

### 3.2 `prepare()` 违反「纯指标」契约，写入了运行态

基类对 `prepare()` 的契约（注意「算一次」）：

```python
# services/quant/app/strategies/base.py:60-62
# ---- 可选 hook：在回测前批量计算指标列（向量化，算一次）----
def prepare(self, df: pd.DataFrame, bar: str) -> pd.DataFrame:
    return df
```

而 `MomentumBreakout.prepare()` 在算完指标列后，额外重置了运行态：

```python
# services/quant/app/strategies/momentum_breakout.py:42-51
def prepare(self, df: pd.DataFrame, bar: str) -> pd.DataFrame:
    df = df.copy()
    w = int(self.params["breakout_window"])
    df["prior_high"] = df["h"].shift(1).rolling(w, min_periods=w).max()
    df["prior_low"]  = df["l"].shift(1).rolling(w, min_periods=w).min()
    df["vol_mean"]   = df["vol"].shift(1).rolling(w, min_periods=w).mean()
    # 跟踪止损的运行极值状态（每笔持仓独立，入场时设定、空仓时清零，见 §18 D5）
    self._peak = 0.0            # ← BUG：运行态写进了「每根都会重跑」的 prepare()
    self._trough = float("inf") # ← BUG
    return df
```

### 3.3 重置如何让跟踪止损退化

`on_candle()` 在持仓分支里用 `max/min` 累积运行极值：

```python
# services/quant/app/strategies/momentum_breakout.py:66-76
if ctx.position_side == "long":
    self._peak = max(self._peak, h)             # 期望：持仓期间最高价
    if c <= self._peak * (1 - trail):
        return [Signal(side="close", reason="跟踪止损(多)")]
    return []
if ctx.position_side == "short":
    self._trough = min(self._trough, lo)        # 期望：持仓期间最低价
    if c >= self._trough * (1 + trail):
        return [Signal(side="close", reason="跟踪止损(空)")]
    return []
```

- **回测**（`prepare` 一次）：`_peak` 从入场起跨根 `max` 累积 → 真正的「持仓期间最高价」。
- **实时**（`prepare` 每根）：每根开头 `_peak` 被 `prepare()` 抹成 `0.0`，紧接着
  `self._peak = max(0.0, h)` 恒等于 **当根最高价 `h`**。于是触发条件
  `c <= _peak*(1-trail)` 退化为 `c <= h_当根*(1-trail)`——这是**当根盘中回撤**判定，
  不再是跟踪止损。做空对称：`_trough` 退化为当根最低价。

复现脚本的 B 配置正是这一退化的实证：见顶后行情缓慢阴跌、每根「当根高 - 收盘」< 2%，
退化后的跟踪止损永不触发，持仓被一路扛到硬止损。

### 3.4 项目内已有的「正确范式」反证

同仓库的 `trend_pullback_long` 把同类运行态处理对了，恰好反证本 bug 是契约违例而非设计本意：

```python
# services/quant/app/strategies/trend_pullback_long.py:71-78
def __init__(self, params: dict | None = None):
    super().__init__(params)
    self._reset_state()

def _reset_state(self) -> None:
    """按笔清零持仓运行态（绝不在 `prepare()` 调用，见模块 docstring）。"""
    self._peak = 0.0
    self._trail_armed = False
```

```python
# services/quant/app/strategies/trend_pullback_long.py:131-158
def prepare(self, df, bar):
    df = df.copy()
    ...
    return df  # 不触碰 self._* 运行态   ← 第 158 行明确注释

# services/quant/app/strategies/trend_pullback_long.py:166
self._reset_state()  # 空仓第一时间按笔清零（承接实盘正确性）
```

模块 docstring 更把规则写死（`trend_pullback_long.py:7-9`）：

> ⚠️ 生命周期：`prepare()` 在 paper/live 下逐根重复调用，故持仓运行态
> (`_peak/_trail_armed`) 只在 `__init__` 建立、空仓分支按笔清零，**绝不在 `prepare()`
> 写入**。

`momentum_breakout` 恰恰违反了这条已在本仓库白纸黑字写明的规则。

---

## 4. 影响面（Impact surface）

### 4.1 直接受影响

- **策略 `momentum_breakout`（暴涨动量突破）**：唯一存在**真实正确性 bug** 的策略。
  其跟踪止损在所有实时路径下失效：
  - `source="live"` 实时撮合（`session.py:266-268 → 290`）。
  - `source="replay"` 加速回放（`session.py:207-221 → feed_candle → 290`）。
  - 预热回放 warm-start（`session.py:120-141 → _process_confirmed → 290`），连 paper
    会话内显示的 seeded 成交/净值都已偏离回测。
- **后果**：跟踪止损是该策略**唯一的主动获利了结**手段；失效后离场退化为只剩
  `SimBroker` 的硬止损（`stop_loss_pct`，`simulator.py:141-146`）与 `max_hold`
  （`simulator.py:160-163`）。表现为「回测漂亮、实盘扛单」，且方向多空对称受损。
- **用户面**：任何在 paper/live/replay 中选用 `momentum_breakout` 的用户，看到的盈亏与
  回测系统性不一致；回测优化出的参数在实盘失真。

### 4.2 排查过的其他策略（结论：不受本 bug 影响）

| 策略文件 | 是否在 `prepare()` 写 `self._*` | 是否受影响 | 说明 |
|---|---|---|---|
| `momentum_breakout.py` | 是（:49-50，运行态）| **是** | 本 bug 本体 |
| `trend_pullback_long.py` | 否（:158 注明不碰）| 否 | 正确范式（`__init__`+空仓清零）|
| `trend_pullback_short.py` | 否 | 否 | 同上（镜像版）|
| `vwap_rsi_momentum.py` | 是（:45 `self._intraday = bar in INTRADAY_BARS`）| 否（次要隐患）| `_intraday` 是 `bar` 的纯函数、会话内恒定，逐根重算幂等 → 不致 bug；但仍是同类反模式，宜顺手挪入 `__init__`/`warmup` |
| `bollinger_reversion.py` | 否（无实例运行态）| 否 | 全部从 df 列读取 |
| `ema_cross_scalper.py` | 否（仅方法调用）| 否 | 无跨根实例态 |
| `range_scalping_strategy.py` | 否（仅方法调用）| 否 | 无跨根实例态 |
| `long_shadow_reversal.py` | 否（无实例运行态，**评审补录**）| 否 | 全文无 `self._*` 赋值；`on_candle` 仅读 df 列/params，持仓全交 broker 风控（止盈/止损/max_hold）管理 |

> 次要发现：`vwap_rsi_momentum.py:45` 在 `prepare()` 里写 `self._intraday`，虽因「纯
> `bar` 派生、幂等」而**当前无害**，但属同一反模式，建议一并清理以防未来踩坑（非本次必须）。
>
> 评审补录：原表漏列了第 8 个策略 `long_shadow_reversal.py`。评审已对整个
> `services/quant/app/strategies/` 目录做穷尽扫描（grep `self\._\w+\s*=`），确认**全目录**
> 仅 `momentum_breakout`、`trend_pullback_long/short`、`vwap_rsi_momentum` 四个文件出现
> 实例态赋值，其中只有 `momentum_breakout.prepare()` 把运行态写进了「逐根重跑」的
> `prepare()`。`long_shadow_reversal.py` 无任何实例运行态，**不受本 bug 影响**——补录后
> 影响面表已覆盖全部 8 个策略，结论不变。

---

## 5. 候选修复方案（Candidate fixes，对比）

> 说明：本节仅为方案对比，依任务约束**本节点不落修复代码**；代码片段仅为示意。

| 方案 | 做法 | 侵入性 | 风险 | 工作量 | 是否根治回测/实盘一致性 |
|---|---|---|---|---|---|
| **A（推荐）策略层对齐范式** | 在 `momentum_breakout` 新增 `__init__`→`_reset_state()` 初始化 `_peak/_trough`，**从 `prepare()` 删除 :49-50 的两行**；空仓分支既有的 :79-80 继续按笔清零 | 单文件，约 +6/-2 行 | 极低 | 极低 | 是 |
| **B 引擎层让 `prepare` 只算一次** | 改 `paper/session.py` 缓存已 prepare 的 df、仅对新增根增量算指标，`prepare()` 全程一次 | 引擎层，牵动全部策略 | 高（滚动窗口 `maxlen=600` 截断、增量指标正确性、全策略回归）| 高 | 是，但代价大且违背 base 契约 |
| **C 契约固化 + 回归测试** | 不改运行逻辑，新增基类约束/文档，并补「连续两次 `prepare` 不改变运行态」「paper 逐根 prepare ≡ 回测单次 prepare」的回归测试 | 测试 + 少量文档 | 低 | 低 | 否（防回归，不单独修 bug）|

### 方案 A 示意（仅示意，不在本节点落地）

```python
def __init__(self, params: dict | None = None):
    super().__init__(params)
    self._reset_state()

def _reset_state(self) -> None:
    """按笔清零跟踪止损运行态（绝不在 prepare() 调用，对齐 trend_pullback_long）。"""
    self._peak = 0.0
    self._trough = float("inf")

def prepare(self, df, bar):
    df = df.copy()
    w = int(self.params["breakout_window"])
    df["prior_high"] = df["h"].shift(1).rolling(w, min_periods=w).max()
    df["prior_low"]  = df["l"].shift(1).rolling(w, min_periods=w).min()
    df["vol_mean"]   = df["vol"].shift(1).rolling(w, min_periods=w).mean()
    return df            # ← 删除原 :49-50 两行运行态重置
```

**为何方案 A 对回测零行为变化（重要）**：回测启动时账户为空仓，第一根进入
`on_candle` 即走空仓分支 `momentum_breakout.py:78-80`，在任何入场前就已把
`_peak/_trough` 清零；入场时再由 :88/:92 设定。因此「由 `prepare` 初始化」还是「由
`__init__` 初始化」对回测结果**逐字节等价**（已被复现脚本 A≡C 间接佐证：把重置时机
从每根改一次都不影响回测）。方案 A 只动了实时路径的错误重置，回测完全不变。

---

## 6. 推荐方案（Recommended fix）与理由

**推荐：方案 A（策略层对齐既有正确范式）+ 方案 C 的回归测试作为配套。**

理由：

1. **破坏面最小**：单文件、约 6 行；不碰引擎、不碰其余 6 个策略、不改公共契约。
2. **与项目既有正确范式逐字一致**：直接照搬 `trend_pullback_long.py:71-78 / 158 / 166`
   的 `__init__`+`_reset_state`+空仓清零写法，连模块 docstring 里「绝不在 `prepare()`
   写入」的规则都已写好，A 只是让 `momentum_breakout` 遵守它。
3. **回测路径零回归**：如上所证，回测结果逐字节不变；只修正实时路径的逐根重置，
   正好消除回测/实盘不一致。
4. **不引入新依赖、不改外部接口**：信号、`reason`、参数 schema 全部不变。
5. **引擎层方案（B）被否的原因**：它要求 `prepare` 全局只调一次，但 `base.py:60-62`
   的契约本就是「`prepare` 是可重复调用的纯指标函数」，paper 逐根调用是**合法**的；
   真正违约的是策略而非引擎。用引擎兜底既违背契约，又把滚动窗口截断、增量指标、全策略
   回归等高风险面全部拉进来，得不偿失。
6. **配套回归测试（C）**：遵循本仓库 `CLAUDE.md` §七「修 bug 必须先写能稳定复现的
   测试」。建议落两条断言：
   - **不变量**：对 `momentum_breakout` 连续两次 `prepare(df)` 后，`_peak/_trough` 不被
     改写（或干脆断言 `prepare` 不写这两个键）。
   - **等价性**：用本报告的复现数据，断言「paper 逐根 prepare 的成交序列」≡「回测单次
     prepare 的成交序列」（即把复现脚本里的 B==C 从 `False` 锁成 `True` 作为回归护栏）。

> 后续 Subtask #2（代码修复）落地方案 A 后，应让
> `repro_divergence.py` 的输出变为 **`A == C == B`、三者盈亏一致**，即视为修复成功。

---

## 附：证据索引（便于复核）

- 回测只调一次 `prepare`：`services/quant/app/engine/backtester.py:60`、循环 `:69-78`
- 实时逐根调用 `prepare`：`services/quant/app/paper/session.py:290`、`on_candle` `:315-319`
- 预热回放也逐根：`services/quant/app/paper/session.py:120-141`、`:266-268`
- bug 本体（运行态写进 `prepare`）：`services/quant/app/strategies/momentum_breakout.py:49-50`
- 退化的跟踪止损读取：`services/quant/app/strategies/momentum_breakout.py:66-76`
- 空仓按笔清零（正确，保留）：`services/quant/app/strategies/momentum_breakout.py:78-80`
- `prepare` 纯指标契约：`services/quant/app/strategies/base.py:60-62`
- 正确范式参照：`services/quant/app/strategies/trend_pullback_long.py:7-9 / 71-78 / 158 / 166`
- 硬止损/`max_hold`（失效后唯一兜底离场）：`services/quant/app/engine/simulator.py:141-163`
- 可运行复现：`docs/diagnoses/momentum-breakout-prepare-resets-trailing-stop/repro_divergence.py`

---

## 评审结论（Review Verdict）

> 评审节点：Subtask #1（分析报告评审与修复）。评审人独立复核了全部引用、独立重跑了复现
> 脚本、并对整个 `strategies/` 目录做了穷尽扫描。下述结论可直接作为 Subtask #2 的落地依据。

### A. 根因证据审计 —— 结论：证据链成立（airtight）

1. **被引代码确实在失败路径上执行（逐行复核通过）**
   - 回测：`backtester.py:60` 在 `run_backtest` 内、`for i in range(n)` 循环（`:69-78`）
     **之外**调用 `prepare()`——只一次。✓
   - 实时：`session.py:290` 位于 `_process_confirmed` 内，每根已确认 K 线触发一次
     `prepare()`；`on_candle/submit` 在 `:315-319`。✓ 且 `_process_confirmed` 同时服务
     live（`:266-268`）、replay（`:207-221`）、priming 预热（`:120-141`），三条实时路径
     **都逐根** `prepare()`。✓
   - bug 本体：`momentum_breakout.py:49-50` 在 `prepare()` 末尾写运行态
     `self._peak=0.0 / self._trough=float("inf")`；`on_candle` 持仓分支（`:67-76`）读取并
     `max/min` 累积。✓ 与基类契约 `base.py:60-62`（“向量化，算一次”的纯指标 hook）冲突。✓

2. **无幸存的替代解释（独立验证）**
   - 评审独立重跑 `repro_divergence.py`，得到与报告**逐字一致**的输出：A `+1176.92` /
     B `-270.00` / C `+1176.92`，`A==C → True`、`B==C → False`。复现脚本把 B、C 的**唯一
     差异**锁定为 `prepare_per_bar` 开关（`repro_divergence.py:100-102`），其余四阶段、参数、
     撮合引擎完全相同——因此“`prepare()` 频次”是导致发散的**唯一自变量**，排除了指标计算、
     撮合差异、参数差异等其它假说。
   - **止损数值自洽**：B 的离场价 `100.880` = 入场价 `104 × (1-0.03)`，恰为
     `simulator.py:141-146` 的硬止损触发价，`reason=stop_loss` 也与该分支一致；离场发生在
     入场后第 13 根（< `max_hold_bars=48`），故确为硬止损而非 `max_hold` 兜底。数值与机制
     互相印证。

3. **证据确实解释了症状**
   - 退化机制成立：`prepare()` 每根把 `_peak` 抹为 `0.0` 后，`self._peak=max(0.0,h)` 恒等于
     **当根最高价**，触发条件 `c<=_peak*(1-trail)` 退化为“当根盘中回撤≥trail”。复现行情见顶后
     缓慢阴跌、每根振幅 < 2%（`repro_divergence.py:65-79`），退化后的跟踪止损永不触发→
     一路扛到硬止损，与“回测高位止盈、实盘扛单”的症状完全吻合。
   - `momentum_breakout` 无 `take_profit`（`default_params` 仅 `stop_loss_pct/max_hold_bars`），
     跟踪止损确为该策略**唯一主动获利了结**手段，失效后果严重性的判断成立。

### B. 评审补全的证据（原报告偏薄之处）

- **B1（影响面完整性）**：原 §4.2 表只列 7 个策略，漏了第 8 个 `long_shadow_reversal.py`。
  评审已对全目录 `grep self\._\w+\s*=` 穷尽扫描：全目录实例态赋值仅出现在
  `momentum_breakout` / `trend_pullback_long` / `trend_pullback_short` / `vwap_rsi_momentum`
  四个文件；`long_shadow_reversal` 无任何实例运行态，不受影响。已把该行补入 §4.2 表，
  影响面现已覆盖全部 8 个策略，**“唯一真 bug = momentum_breakout”的结论不变**。
- **B2（复现脚本保真度的诚实标注）**：`repro_divergence.py` 的 `run_paper_loop` 为对齐
  `backtester` 而加了 `i < n-1` 的“最后一根不开仓”护栏（`:107`），而真实
  `session.py:315` 是 `if i >= warmup()`、**无** `i<n-1`（流式无总长 n）。该差异已被脚本
  末尾补 3 根（`:82-83`）中和，且发散点出现在序列中段，不触及边界——故**不影响定性结论**，
  但属于“复刻管线 vs 真实管线”的已知细微差异，记录在案以免被误读为逐字节等价。

### C. 候选修复审计 —— 结论：方案 A 正确、最小、回测零回归

- **边界/错误路径**：方案 A 在 `__init__→_reset_state()` 初始化 `_peak/_trough`，保证
  **任何** `on_candle` 调用前两变量已定义，杜绝预热段 `i<warmup()`（此时 `on_candle` 不被
  调用）或单测直接调用 `on_candle` 时的 `AttributeError`。运行期由 `on_candle` 自洽维护：
  空仓分支（`:78-80`）按笔清零、入场（`:88/:92`）设定、持仓（`:67-76`）累积——状态机闭环，
  无需 `prepare()` 参与。
- **回测零行为变化（已证）**：回测/预热均从空仓起步，第一根 `on_candle` 即走空仓分支先清零；
  “由 `prepare` 初始化”与“由 `__init__` 初始化”对回测结果逐字节等价，已被复现脚本
  `A≡C` 间接佐证。
- **最小变更**：删 `prepare()` 内 2 行 + 增 `__init__/_reset_state`（≈ +6/-2 行），直接照搬
  仓库既有正确范式 `trend_pullback_long.py:71-78/158/166`，不改信号、`reason`、参数 schema、
  公共契约或引擎。
- **并发面无新增**：本修复**不改变**策略实例的生命周期（每个会话仍持有各自实例），既有的
  实例态并发前提保持原样，故不引入新的并发回归。
- **方案 B 被否（复核同意）**：让引擎缓存/增量 `prepare` 会**违反** `base.py:60-62` 的“可重复
  调用的纯指标函数”契约（逐根调用本是合法的），并把滚动窗口 `maxlen` 截断、增量指标正确性、
  全 8 策略回归等高风险面全部拉进来。真正违约的是策略而非引擎，不应让引擎兜底。
- **方案 C 作为配套保留**：符合 `CLAUDE.md` §七“修 bug 必先写稳定复现测试”。

### D. 最终推荐修复（单一方案）

**采纳方案 A（策略层对齐既有正确范式），并以方案 C 的回归测试作为强制配套。** 落地动作精确到行：

1. 在 `momentum_breakout.py` 新增 `__init__`，调用 `_reset_state()` 初始化
   `self._peak=0.0`、`self._trough=float("inf")`（镜像 `trend_pullback_long.py:71-78`）。
2. **删除** `momentum_breakout.py:48-50` 在 `prepare()` 内的注释与两行运行态重置；`prepare()`
   仅保留 `prior_high/prior_low/vol_mean` 三列纯指标计算后 `return df`。
3. 保留 `on_candle` 空仓分支 `:78-80` 的按笔清零与入场 `:88/:92` 的设定——它们是运行期状态机
   的正确部分，不动。
4. 配套回归测试（`services/quant` 测试目录，沿用其既有 fixture 习惯）：
   - **不变量测试**：对 `momentum_breakout` 连续两次 `prepare(df)` 不改写 `_peak/_trough`
     （或断言 `prepare` 不触碰该两属性）——把契约钉死。
   - **等价性测试**：复用本报告复现数据，断言“paper 逐根 `prepare` 的成交序列”≡“回测单次
     `prepare` 的成交序列”，即把 `repro_divergence.py` 的 `B==C` 从 `False` 锁成 `True` 作为
     回归护栏。

**理由（单一方案的决定性依据）**：方案 A 是**唯一**同时满足“根治回测/实盘一致性 + 破坏面最小 +
回测逐字节零回归 + 不违背基类契约 + 与仓库既有范式一致”的方案；B 违约且高风险，C 单独无法修
bug 只能防回归。故定为：**方案 A 落地修复 + 方案 C 回归测试护栏**。

**验收标准（交付 Subtask #2 时校验）**：修复后重跑
`python docs/diagnoses/momentum-breakout-prepare-resets-trailing-stop/repro_divergence.py`，
输出应变为 **`A == C == B`、三者成交与盈亏完全一致**（B 的 `reason` 由 `stop_loss` 回到
`跟踪止损(多)`、离场价由 `100.880` 回到 `117.600`），且新增的不变量 / 等价性测试全部通过；
其余 7 个策略与引擎不得有任何改动。

> 评审范围声明：本节点仅审阅与补全 `analysis.md`，未改动任何源码，亦未提交 git——落代码为
> Subtask #2、Review+提交为 Subtask #3 的职责。
