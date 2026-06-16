# 顺势回踩双策略（做多 / 做空）开发方案 spec

- **Feature**: `trend-pullback-dual-strategies`
- **文档版本**: v2（评审已完成；全部 P0/P1 已就地修复，见 §0「评审记录」与文末「评审结论」）
- **作者节点**: Subtask #0 方案设计；评审 / 修复节点 Subtask #1 方案评审与修复
- **目标读者**: 下游"代码开发"节点（须可据此逐行落地）
- **关联基线**: `.claude-index/index.md`、`docs/plans/aragon-trading-platform/spec.md`

---

## 0. 评审记录（Review Notes，v2 由 Subtask #1 补充）

> 评审维度：**可行性 / 完整性 / 一致性 / 规模适配**。下表为本次评审发现的全部问题；
> 所有 **P0 / P1** 已在 v2 文档正文就地修复（见「修复定位」列），**P2** 作为非阻断建议在「评审结论」的条件项中列出。
> 评审依据：已逐一核对源码 `strategies/base.py`、`engine/simulator.py`、`engine/backtester.py:55-58`、
> `engine/indicators.py`、`paper/session.py:86,170`、`db/session.py:_seed_builtin_strategies`、
> `db/repositories.py`、`okx/types.py`、`tests/test_strategies.py`、`tests/conftest.py`，spec 对引擎契约的描述**与源码一致**。

| # | 维度 | 严重度 | 问题 | 修复定位 |
|---|---|---|---|---|
| R1 | 可行性 / 正确性 | **P1** | **实盘生命周期缺陷**：spec 原文要求在 `prepare()` 内初始化每笔持仓运行态（`_peak/_trail_armed`）。但 paper/live 会话**每收到一根已确认 K 线都会重新调用 `prepare()`**（`paper/session.py:170`，回测仅 `backtester.py:55` 调用一次）。结果是实盘路径下峰值跟踪与"分级收紧"状态被逐根清零 → 跟踪止损与分级离场（本方案的核心卖点）在 paper/live 下静默失效（现有 `momentum_breakout` 亦有此隐患，本方案放大了后果）。 | §2-4(b)、§3.3 `__init__/_reset_state`、§3.4、§6、§7-3 |
| R2 | 完整性 | **P1** | **`warmup()` 未给出具体实现**：§3.3/§3.4 未定义两个策略的 `warmup()` 返回值。而 live 会话在 `prepare()` 之前即调用 `warmup()`（`session.py:86`），它必须是 `params` 的纯函数。缺此公式实现者无据可依。 | §3.3 `warmup()`、§3.4 |
| R3 | 可行性 / 有效性 | **P1** | **区域半宽量纲错配**：支撑/阻力"区域"半宽用**基础周期 ATR**（如 5m ATR）定义，却套在 **HTF 级摆动点**上。基础 ATR 相对 HTF 摆动间距过小 → 区域过窄 → 价格极少触区 → 入场稀少，重新引入用户最担心的"卖飞 / 错过"。 | §3.2(htf_atr)、§3.1 映射表、§3.3/§3.4 区域公式、§4 |
| R4 | 一致性 / 可行性 | **P1** | **HTF 摆动点算法二义**：§3.2 原文"在 HTF 桶上算 **或** 在基础周期上用映射后的 HTF 桶 low/high 算"留了两种实现，阻碍确定性落地。 | §3.2(已钉死为"HTF 桶上算 + 因果映射") |
| R5 | 完整性 | **P1** | **`_trail_armed` 只锁不解**：原设计 `fast_break` 一旦触发即永久收紧跟踪止损，永不解除。若是假破后结构修复，收紧态仍在 → 随后一次正常回踩即被收紧止损扫出，**自相矛盾地让"假破"间接触发离场**（违背本策略"抗假破"初衷）。 | §3.3(新增 disarm 条件)、§3.4 |
| R6 | 规模适配 | P2 | `params_schema` 暴露 **17 个参数**（既有策略多为 7 个），面板偏长、过拟合面更大。建议精简或分组（保留核心 8~10 个进 schema，其余降级为可选 params）。 | 见「评审结论」条件 C1 |
| R7 | 一致性 | P2 | §5 称需"修正过期断言 `test_registry_has_five_builtins`（断言 `>=5`）"——实测该断言在新增后仍为真（`7>=5`），**并非失败/过期**；真正价值是把 2 个新 key 纳入显式校验并把误导性函数名改正。措辞已纠正。 | §5、§8 |
| R8 | 完整性 | P2 | `max_hold_bars` 默认 240 与"1h 未破则持有底仓跟随大趋势"哲学张力：细周期下 240 根会过早强平趋势单，粗周期下又过长。建议文案提示其与周期的关系，或按周期缩放默认值。 | §3.5 提示、见条件 C2 |
| R9 | 完整性 | P2 | 细基础周期（如 5m）下 HTF EMA(50) 需要约 `50×(htf/base)` 根基础数据才非 NaN；窗口过短则长期无信号（由 NaN 守卫兜住，不报错）。建议在 `description`/§3.5 提示推荐最小历史根数。 | §3.5 提示 |
| R10 | 一致性 | P2 | §7-5 主动豁免布尔量 `is_/has_/should_` 命名，与 CLAUDE.md《命名》小节冲突；不过既有代码（`momentum_breakout` 的 `surge/prior_high`）本就未严守该约定，故沿用语义清晰名可接受，记录在案。 | §7-5（保留并注明） |

**未发现的问题（已核验为非问题）**：①未来函数——设计用 `shift(1)` 桶 + `shift(right)` 确认显式规避，并要求专门因果单测，方向正确；②DB / 前端零改动——`_seed_builtin_strategies` 按 `BUILTIN_ORDER+extras` 幂等补插、`repositories` 注入 `paramsSchema`，spec 描述属实；③单仓 / 全平 / 次根成交契约描述与 `simulator.py` 完全一致。

---

## 1. Overview（概述）

本需求要在现有「多策略量化模拟交易平台 Aragon Trading」中**新增两个内置策略**：一个用于**上涨周期做多**（`trend_pullback_long` 顺势回踩做多），一个用于**下降周期做空**（`trend_pullback_short` 顺势反弹做空）。二者是同一交易哲学的镜像实现，核心思想来自用户提供的策略描述：**大周期（1h）定方向与回踩买区，小周期（1min/2min）只用于结构确认与减仓，而不用于判断大趋势反转；只在大趋势强的时候顺势操作，买入/卖出讲究"区域"而非"单点"，并且入场后必须带有"大周期失效止损"。** 这把一个偏主观的看盘思路，固化成可回测、可复盘、可在 paper 模式实盘验证的规则化策略。

之所以做成两个独立策略而不是一个带方向开关的策略，是因为本平台的注册表（`STRATEGY_REGISTRY`）以"一个 key 一个模板"为唯一真源（C6），前端策略列表、DB seed、回测/对比/ paper 均以 key 维度展开。做多与做空在"区域定义、止损方向、结构破坏方向"上完全镜像，拆成两个 key 既符合既有约定，又能让用户在 UI 上分别启用、分别调参、在对比视图里并排评估。两者复用一个共享的、**严格因果（无未来函数，R4）**的结构计算工具模块，避免镜像逻辑重复。

平台既有的硬约束在本方案中全部保留：**数据必须真实**（OKX 公共行情，不可造假）；**时间统一秒级 UTC**；**信号在下一根开盘价成交**（避免未来函数）；新增策略遵循"新文件 + `@register_strategy` + 在 `__init__.py` 增一行 import"的插件式约定，**不修改引擎、不修改 DB schema、不修改前端**。需要诚实声明的一点：用户希望"确保稳健盈利"，但任何策略都**无法保证盈利**；本方案能做的是把规则化、止损分层、趋势门控、顺势交易这些"提高稳健性"的工程手段落地，真实盈亏仍以真实行情回测 / paper 验证为准（见 §10 风险）。

## 2. 背景与现状约束（实现者必读）

下列事实来自对现有源码的精确核对，是本方案所有设计决策的依据：

1. **策略契约**（`services/quant/app/strategies/base.py`）：
   - `BaseStrategy` 子类须提供类属性 `meta: StrategyMeta(key, name, description, default_params, params_schema)` 并实现 `on_candle(df, i, ctx) -> list[Signal]`。
   - 可选 hook：`prepare(df, bar) -> df`（回测前**批量、向量化**算指标列，只算一次）；`warmup() -> int`（产生信号前所需最小历史根数）；`risk_params() -> RiskParams`（默认从 params 取 `stop_loss_pct/take_profit_pct/max_hold_bars`）。
   - `Signal(side: "long"|"short"|"close", strength: float∈[0,1], reason: str)`。
2. **撮合引擎契约**（`services/quant/app/engine/simulator.py` `SimBroker`）：
   - **单向单仓、全开全平**：同方向重复 `open` 信号被 `submit()` 忽略（`if projected == side: continue`），**不支持加仓 / 金字塔 / 部分平仓**。`close` 一律全平。
   - `strength` = 开仓目标名义敞口占当前权益比例：`notional = strength·equity·leverage`。
   - 信号在**下一根开盘价**成交（`fill_pending`）；风控止损/止盈在**当根**用触发价强平（止损优先）；`max_hold_bars` 到期次根开盘平。
   - `BrokerContext(position_side, bars_in_position, entry_price, equity)` 为只读上下文。
3. **回测编排**（`engine/backtester.py:55-58`）：调用顺序为 `prepare()` → `risk_params()` → `warmup()` → 逐根 `on_candle()`。
4. **⚠️ 生命周期陷阱（两条，实现者必读）**（`paper/session.py`）：
   - **(a) `warmup()` 早于 `prepare()`**（`session.py:86`）：live 会话先调用 `warmup()` 决定拉多少根历史，再在每根上 `prepare()`。**因此 `warmup()` 绝不能依赖 `prepare()` 设置的实例状态**，必须是 `params` 的纯函数。
   - **(b) `prepare()` 在 paper/live 下逐根重复调用**（`session.py:170`：每根已确认 K 线都 `self.strategy.prepare(...)` 重算指标列；而回测仅 `backtester.py:55` 调用一次）。**推论（关键）**：任何"每笔持仓运行态"（`_peak/_trough/_trail_armed`）**绝不能在 `prepare()` 里初始化**，否则会被逐根重置，使跟踪止损峰值与"分级收紧"在实盘路径下静默失效。运行态必须在 **`__init__` 建立一次**、仅在 `on_candle` 的**空仓分支按笔清零**（§3.3 详述）。`prepare()` 只负责**纯指标列**的向量化计算，对状态无副作用。
5. **多周期事实**：回测 / paper 一次只喂**单一 `bar` 周期**的 `df`（列 `ts,o,h,l,c,vol`）。要在策略内实现"1h 定方向 + 小周期执行"，必须**在 `prepare()` 内从基础周期 df 因果地重采样出 1h 结构**，不能依赖引擎提供多周期数据。
6. **自动接入**：新增 key 后，`db/session.py:_seed_builtin_strategies()` 会在下次 `init_db()` 幂等地为其插入一行 builtin 策略；`db/repositories.py` 把 `meta.params_schema` 作为 `paramsSchema` 暴露给前端；前端 `StrategyList` 通用渲染参数控件。**故前端零改动、DB schema 零改动**。
7. `okx/types.py` 提供 `BAR_SECONDS`（`1m=60 … 1H=3600 … 1D=86400`），`engine/indicators.py` 提供 `ema/sma/rsi/bollinger/atr/vwap_session`（纯函数、不就地改入参）。

## 3. Technical design（技术设计）

### 3.1 总体思路：把"主观看盘"翻译成"单仓引擎可执行的规则"

用户描述里有几处与现有引擎能力不完全匹配，方案做如下**显式映射**（这是本设计最关键的部分，实现者须严格遵循）：

| 用户原始诉求 | 引擎现实 | 本方案映射 |
|---|---|---|
| 1h 支撑/阻力"区域"分批挂限价单 | 引擎无限价单 / 无部分成交，信号次根开盘市价成交 | 用"价格是否进入区域 `[zone_low, zone_high]`"作为入场门控；区域由 **HTF 级波动率** `htf_swing ± zone_atr_mult·htf_atr` 定义（**注意：半宽用 HTF-ATR 而非基础周期 ATR，避免区域过窄、入场稀少**，见 §3.2 R3 修复）；入场只发一次满仓信号（次根开盘成交）。区域成员判定替代三笔限价挂单。 |
| 1min 破线先减仓、2min/5min 再减、留底仓 | 引擎不能部分平仓 | **分级收紧而非分批减仓**：1min 级结构破→不平仓，仅**收紧跟踪止损**（armed=tight）；2min/5min 级结构也破 **或** 跟踪止损被打 **或** 1h 失效→**全平**。底仓哲学映射为"1h 未破则继续持有"。 |
| 1h 支撑失效止损 | 引擎有按入场价百分比的硬止损 | **双层止损**：①结构止损（`on_candle` 里 `c` 跌破 `htf_swing_low − stop_atr_mult·ATR` 立即发 close）；②引擎级 `stop_loss_pct` 兜底（入场价相对，防极端跳空）。 |
| 不要 1min 一破就全卖 / 假破洗盘 | — | 入场需"止跌确认"，离场需"小级别破 + 更慢级别确认 或 跟踪止损"，避免单一信号触发。 |

### 3.2 共享结构工具模块（因果、无未来函数）

新建 `services/quant/app/strategies/_trend_structure.py`，全部为**纯函数**、向量化、可单测。关键是**严格因果**（R4 无未来函数是平台硬规则）：

- **HTF（高周期）重采样 + 桶级 OHLC `attach_htf_regime(df, base_seconds, htf_seconds, ema_fast, ema_slow, atr_period)`**：
  1. `bucket = df["ts"] // htf_seconds`（秒级整除分桶）。
  2. 若 `base_seconds >= htf_seconds`（用户在 1H/4H/1D 上跑），HTF 退化为基础周期本身：直接在基础周期 `o/h/l/c` 上算 EMA / ATR / 摆动点，不分桶（桶级 high=h、low=l、close=c）。
  3. 否则按 bucket 聚合出每个 HTF 桶的 OHLC：`htf_o`=桶内首根 o，`htf_h`=桶内 `h` 最大，`htf_l`=桶内 `l` 最小，`htf_c`=桶内**最后一根** c。在 HTF 桶序列上算 `ema(htf_c, ema_fast/ema_slow)`、`slope = htf_ema_slow.diff()`、以及 **HTF 级 ATR `htf_atr = atr(htf_ohlc, atr_period)`**（用桶级 h/l/c，复用 `indicators.atr`）。
  4. **因果映射（唯一实现，已钉死）**：把上述全部 HTF 桶级序列（`htf_ema_*/htf_slope/htf_atr/htf_h/htf_l` 及下一条的 HTF 摆动点）**整体 `shift(1)` 桶**后，按 `bucket` map 回基础行（即基础行只能看到**已收盘的上一个 HTF 桶**的值，杜绝当前未收盘 HTF 桶里的未来基础根）。返回列 `htf_ema_fast, htf_ema_slow, htf_slope, htf_atr, htf_high, htf_low, htf_swing_low, htf_swing_high`。
  5. 派生 `htf_uptrend = (htf_ema_fast > htf_ema_slow) & (htf_slope > 0)`；`htf_downtrend = (htf_ema_fast < htf_ema_slow) & (htf_slope < 0)`。
- **确认型摆动点 `pivot_low_confirmed(low, left, right)` / `pivot_high_confirmed(high, left, right)`**（纯函数，作用于任意序列）：
  1. `is_piv = low[k] == min(low[k-left : k+right+1])`（中心窗口，分形 pivot）。
  2. pivot 的值**只有在 `k+right` 根才被确认可用**，故 `piv_val = where(is_piv, low, NaN)` 后 `piv_val.shift(right).ffill()` → 得到"截至当前可用的最近一个已确认摆动低点"。这保证 `on_candle(i)` 永不使用 `> i` 的数据。
  3. **算法已钉死（R4 修复，去掉原"或"二义）**：`htf_swing_low/high` 一律**在 HTF 桶级 `htf_l/htf_h` 序列上**用较大 `slow_pivot_left/right`（HTF 桶维度）计算 `pivot_low_confirmed/pivot_high_confirmed`，再经上条"shift(1) 桶 + 因果映射"落到基础行；**不**在基础周期上对 HTF 量直接取窗口。`fast_swing_low/high`（"1min 结构"）在**基础周期** `l/h` 上用小 `fast_pivot_left/right`；`slow_swing_low/high`（"2m/5m 结构"）在**基础周期**上用中等 `slow_pivot_left/right`。
- **量能 `attach_volume_flags(vol, window, contract_ratio, spike_mult)`**：`vol_mean = vol.shift(1).rolling(window).mean()`；`vol_contracting = vol <= contract_ratio·vol_mean`（缩量回踩）；`vol_spike = vol >= spike_mult·vol_mean`（放量砸穿/突破）。
- **两类 ATR（R3 修复，明确量纲分工）**：
  - **`atr`（基础周期）** = `engine.indicators.atr(df, atr_period)`：用作**结构止损距离**与跟踪止损步长的波动率标尺（贴合基础周期噪声尺度）。
  - **`htf_atr`（HTF 桶级，见上）**：用作**支撑/阻力区域半宽**的标尺（`zone = htf_swing ± zone_atr_mult·htf_atr`）。**区域属于 HTF 结构，半宽必须用 HTF-ATR**，否则（用基础 ATR）区域过窄、价格极少触区、入场稀少。退化场景（`base_seconds>=htf_seconds`）下 `htf_atr` 即基础周期 ATR，自然一致。

> 设计校验点：所有派生列在历史不足时为 `NaN`；`on_candle` 对所有依赖列做 `pd.isna` 守卫并返回 `[]`（与现有 5 策略一致）。因此 `warmup()` 只需覆盖**基础周期**的 pivot/ATR/vol 窗口，HTF 不足由 NaN 守卫自然兜住——这也满足 §2 第 4 条"`warmup` 不依赖 `prepare`"的约束。

### 3.3 策略一：`trend_pullback_long`（顺势回踩做多，上涨周期）

**`__init__(self, params=None)`（R1 修复，持仓运行态在此建立一次，绝不放进 `prepare()`）**：
```python
def __init__(self, params=None):
    super().__init__(params)
    self._reset_state()

def _reset_state(self) -> None:
    self._peak = 0.0           # 入场后运行最高价（跟踪止损基准）
    self._trail_armed = False  # 是否已收紧跟踪止损（假破修复后可解除）
```
> 为何不在 `prepare()` 初始化：§2-4(b) —— paper/live 逐根调用 `prepare()`，若在其中重置会逐根清零运行态，使跟踪/分级在实盘失效。`prepare()` 只算纯指标列。

**`warmup(self) -> int`（R2 修复，`params` 纯函数，不读任何 `prepare` 状态）**：
```python
def warmup(self) -> int:
    p = self.params
    return max(
        int(p["atr_period"]),
        int(p["vol_window"]) + 1,
        int(p["slow_pivot_left"]) + int(p["slow_pivot_right"]) + 1,
        int(p["fast_pivot_left"]) + int(p["fast_pivot_right"]) + 1,
    ) + 2
```
> 仅覆盖**基础周期**列的窗口；HTF 列在历史不足时为 NaN，由 `on_candle` 的 NaN 守卫自然兜住（§3.2 校验点），无需计入 warmup。

**`prepare(df, bar)`**：`base_seconds = BAR_SECONDS.get(bar, 60)`；`df = df.copy()`；依次调用 §3.2 的 `attach_htf_regime(...含 atr_period...) / pivot_*_confirmed / attach_volume_flags` 与基础 `atr`，写入所有结构列后 `return df`。**不触碰 `self._*` 运行态。**

**`on_candle(df, i, ctx)`**（若超 50 行则抽 `_exit_signal()/_entry_signal()` 私有方法，CLAUDE.md 尺寸阈值）：
- **持多仓时**（`ctx.position_side == "long"`）：
  1. `self._peak = max(self._peak, h)`。
  2. `fast_break = c < fast_swing_low`；`slow_break = c < slow_swing_low`；`htf_break = (not htf_uptrend) or (c < htf_swing_low - stop_atr_mult·atr)`。
  3. **收紧 / 解除（R5 修复，不再"只锁不解"）**：
     - 若 `fast_break`：`self._trail_armed = True`（**仅收紧跟踪止损，不平仓** → 映射"1min 破=减仓不清仓"）。
     - 否则若 `c > fast_swing_high`（结构重新站回，假破修复）：`self._trail_armed = False`（解除收紧，避免一次假破后被正常回踩扫出）。
  4. `trail = trail_atr_mult_tight if self._trail_armed else trail_atr_mult`；`trail_hit = c <= self._peak - trail·atr`。
  5. 若 `slow_break or htf_break or trail_hit`：返回 `Signal(side="close", reason=...)`（"2m/5m 也破 / 1h 失效 / 跟踪止损"）。否则 `[]`（1h 未破 → 持有底仓）。
- **空仓时**（**第一时间** `self._reset_state()` 按笔清零，承接 §2-4(b) 的实盘正确性）：
  1. **趋势门控**：`htf_uptrend` 必须为真，否则 `[]`（"1h 破位就不用回踩买入思路"）。
  2. **支撑区（半宽用 HTF-ATR，R3 修复）**：`zone_low = htf_swing_low - zone_atr_mult·htf_atr`，`zone_high = htf_swing_low + zone_atr_mult·htf_atr`。
  3. **回踩进区**：`l <= zone_high and c >= zone_low and c <= zone_high`（下影探入支撑区且收盘仍在区内附近）。
  4. **非放量砸穿**：`not vol_spike`（"大阴线放量砸穿不是低吸，是趋势转弱"）；可选 `vol_contracting` 加分（缩量回踩更可靠）。
  5. **止跌确认**：`c > o`（收阳）。
  6. 全部满足 → 先 `self._peak = h`（预置峰值），再返回 `Signal(side="long", strength, reason="回踩1h支撑区止跌")`。
- 所有用到的结构列（`htf_*/fast_*/slow_*/atr/htf_atr/vol_*`）任一为 `NaN` → `[]`。

`risk_params()`：用基类默认（从 params 取 `stop_loss_pct`、`max_hold_bars`；`take_profit_pct` 默认不设，让趋势延续，由结构/跟踪止损离场）。

### 3.4 策略二：`trend_pullback_short`（顺势反弹做空，下降周期）

与 §3.3 **完全镜像**，**全部 v2 修复同样适用**（运行态在 `__init__` 建立、`warmup` 为 params 纯函数、区域半宽用 `htf_atr`、收紧态可解除）：

- **`__init__/_reset_state`**：`self._trough = float("inf")`（入场后运行最低价）、`self._trail_armed = False`；同样**绝不在 `prepare()` 初始化**。
- **`warmup`**：与 §3.3 公式逐字相同（同构 params）。
- **门控**：`htf_downtrend`。
- **阻力区（HTF-ATR 半宽）**：`zone_low = htf_swing_high - zone_atr_mult·htf_atr`，`zone_high = htf_swing_high + zone_atr_mult·htf_atr`。
- **入场**：`h >= zone_low and c <= zone_high and c >= zone_low`（上影探入阻力区且收盘仍在区内附近）+ `not vol_spike` + 止涨确认 `c < o`（收阴）→ 先置 `self._trough = lo` 再返回 `Signal(side="short", strength, reason="反弹1h阻力区止涨")`。
- **持空仓离场**：`fast_break_up = c > fast_swing_high`（收紧 `self._trail_armed = True`）；若 `c < fast_swing_low`（结构重新走弱，假破修复）则 `self._trail_armed = False`（解除收紧，R5 镜像）；`self._trough = min(self._trough, lo)`；`trail = trail_atr_mult_tight if armed else trail_atr_mult`；全平条件 = `slow_break_up = c > slow_swing_high` **或** `htf_break = (not htf_downtrend) or (c > htf_swing_high + stop_atr_mult·atr)` **或** 跟踪止损 `c >= self._trough + trail·atr`。
- 空仓分支第一时间 `self._reset_state()`；NaN 守卫同 §3.3。

### 3.5 退化与边界

- `base_seconds >= htf_seconds`（在 1H/4H/1D 上回测，含既有测试 `test_backtest_runs_for_all_strategies` 用 1H）：HTF 退化为基础周期 EMA / ATR / 摆动点，`htf_atr` 即基础 ATR，策略仍能正常跑、`len(equity_points)==len(candles)`、回撤≤0（引擎保证），不报错。
- 推荐基础周期 5m / 15m（在 `description` 文案中提示）；不强制禁用任何周期（区别于 VWAP 策略在 1D 静默）。
- **最小历史根数提示（R9）**：细基础周期下 HTF `ema_slow` 需约 `ema_slow×(htf_seconds/base_seconds)` 根基础数据才非 NaN（如 5m+1h HTF、`ema_slow=50` 需约 600 根）；窗口不足时仅"无信号"而非报错。`description` 应提示用户保证足够历史（回测窗口 / `replay_bars` ≥ 该量级）。
- **`max_hold_bars` 与"持有底仓"的张力提示（R8）**：默认 240 根在细周期上偏紧（如 1min≈4h 即强平）、粗周期上偏松（1H=10 天）。本策略以"1h 未破即持有"为主，`max_hold_bars` 仅作为防御性兜底；`description` 与参数 `label` 应说明其按周期换算的实际时长，用户可据自身周期调大/调小（区别于既有"短持"策略）。
- 数据真实性、秒级 UTC、次根成交等平台不变量全部沿用，无新增边界。

## 4. Interface design（接口设计）

**不新增任何 REST / WS / CLI 接口。** 既有接口零改动即自动支持新策略：

- `GET /strategies` → 返回新增的 2 行 builtin（含 `paramsSchema`，由 `repositories.list_strategies` 注入 `meta.params_schema`）。
- `POST /strategies/{id}/backtest`、`POST /strategies/compare` → 通过 `get_strategy_class(key)` 实例化即可回测 / 对比。
- `POST /paper/start` + `WS /ws/paper/{sid}` → paper 实盘复用同一 `BaseStrategy`，零改动。

唯一"接口契约"是两个策略的 `params_schema`（暴露给前端的参数面板）。两者参数同构，列举如下（实现时按此字段、范围、label 落地）：

| 参数 | type | default(多/空) | min | max | step | label |
|---|---|---|---|---|---|---|
| `htf_seconds` | int | 3600 | 300 | 14400 | 300 | 高周期秒数 |
| `htf_ema_fast` | int | 20 | 3 | 100 | 1 | 高周期快EMA |
| `htf_ema_slow` | int | 50 | 5 | 200 | 1 | 高周期慢EMA |
| `atr_period` | int | 14 | 2 | 100 | 1 | ATR周期 |
| `zone_atr_mult` | number | 1.0 | 0.1 | 5.0 | 0.1 | 区域半宽(HTF-ATR) |
| `fast_pivot_left` | int | 3 | 1 | 20 | 1 | 快结构左窗 |
| `fast_pivot_right` | int | 2 | 1 | 20 | 1 | 快结构右窗 |
| `slow_pivot_left` | int | 6 | 1 | 40 | 1 | 慢结构左窗 |
| `slow_pivot_right` | int | 3 | 1 | 40 | 1 | 慢结构右窗 |
| `vol_window` | int | 20 | 2 | 100 | 1 | 量能窗口 |
| `vol_spike_mult` | number | 2.0 | 1.0 | 6.0 | 0.1 | 放量倍数 |
| `stop_atr_mult` | number | 1.5 | 0.2 | 6.0 | 0.1 | 结构止损(ATR) |
| `trail_atr_mult` | number | 2.5 | 0.3 | 8.0 | 0.1 | 跟踪止损(ATR) |
| `trail_atr_mult_tight` | number | 1.2 | 0.2 | 6.0 | 0.1 | 收紧跟踪(ATR) |
| `stop_loss_pct` | number | 0.03 | 0.005 | 0.2 | 0.005 | 兜底硬止损% |
| `max_hold_bars` | int | 240 | 1 | 1000 | 1 | 最大持仓根数 |
| `strength` | number | 0.8 | 0.1 | 1.0 | 0.05 | 仓位比例 |

> `vol_contract_ratio`（缩量阈值，默认 1.0）作为可选加分项，可放入 params 但不必进 schema（避免面板过长）；若放入则 `type:number, min:0.3, max:2.0, step:0.05`。
>
> **R6（P2，面板规模）**：上表 17 项相对既有策略（多为 7 项）偏多。**实现建议**：仅把最常调的 ~10 项进 `params_schema`（`htf_seconds, htf_ema_fast, htf_ema_slow, atr_period, zone_atr_mult, stop_atr_mult, trail_atr_mult, trail_atr_mult_tight, stop_loss_pct, strength`），其余 pivot/vol 细参降级为仅 `default_params`（仍可用、仅不进面板），以降低过拟合面与 UI 拥挤。最终取舍由"代码开发"节点定，但应有意识收敛，见评审结论条件 C1。

## 5. File / module change plan（文件 / 模块变更计划）

| 文件 | 动作 | 一句话意图 |
|---|---|---|
| `services/quant/app/strategies/_trend_structure.py` | 新建 | 共享因果结构工具：HTF 重采样、确认型摆动点、量能标志、区域半宽（纯函数、向量化、无未来函数）。 |
| `services/quant/app/strategies/trend_pullback_long.py` | 新建 | `TrendPullbackLong`：1h 上涨门控 + 回踩支撑区止跌做多 + 分级收紧/结构/跟踪/1h失效离场。 |
| `services/quant/app/strategies/trend_pullback_short.py` | 新建 | `TrendPullbackShort`：1h 下跌门控 + 反弹阻力区止涨做空（§3.3 的镜像）。 |
| `services/quant/app/strategies/__init__.py` | 修改 | 增 2 行 `from . import trend_pullback_long / trend_pullback_short`；在 `BUILTIN_ORDER` 末尾追加 2 个 key（保持稳定展示顺序）。 |
| `services/quant/tests/test_trend_pullback.py` | 新建 | 单测：工具因果性 / 多策略在构造上涨回踩场景出多 / 空策略镜像出空 / 分级离场 / 全策略回测可跑通。 |
| `services/quant/tests/test_strategies.py` | 修改 | 把 `test_registry_has_five_builtins` **改名**为 `test_registry_has_builtins`（原名将随 7 个内置而误导），在 key 循环中纳入 2 个新 key 并断言 `len >= 7`。注（R7）：原 `>= 5` 断言新增后**仍为真、并非失败**，本次属"覆盖增强 + 命名纠正"，非"修复过期断言"。 |

**显式不改动**：`engine/*`（SimBroker/backtester/metrics/indicators 全部沿用）、`db/*`（schema 不变，seed 自动幂等接入）、`api/*`、`apps/web/*`（前端通用渲染，零改动）、`okx/*`、`desktop/*`。**不新增第三方依赖**（仅用既有 pandas / numpy）。

## 6. Data model（数据模型）

- **DB schema：无变更。** `Strategy` 表沿用；`_seed_builtin_strategies()` 在下次 `init_db()` 幂等地为 2 个新 key 各插一行（`template_key/name/params_json=default_params/builtin=True`）。已存在的 dev DB 只会"补插"缺失 builtin，不覆盖用户已有行。
- **注册表内存形状**：`StrategyMeta` 同既有；`params/params_schema` 形状见 §4。
- **`prepare()` 产出的 df 新增列**（仅内存、纯指标、无状态副作用）：`atr, htf_atr, htf_ema_fast, htf_ema_slow, htf_slope, htf_uptrend, htf_downtrend, htf_high, htf_low, htf_swing_low, htf_swing_high, fast_swing_low, fast_swing_high, slow_swing_low, slow_swing_high, vol_mean, vol_contracting, vol_spike`。
- **每笔持仓策略状态**（实例变量，**在 `__init__` 建立、空仓分支按笔 `_reset_state()` 清零，绝不在 `prepare()` 初始化**，R1）：多策略 `self._peak: float, self._trail_armed: bool`；空策略 `self._trough: float, self._trail_armed: bool`。理由见 §2-4(b)：paper/live 逐根重调 `prepare()`，状态若放其中将被逐根重置。
- **`Signal` / `TradeRecord` / `EquityRecord` 形状**：全部沿用，无新增字段。

## 7. 算法因果性与正确性要点（实现必须遵守）

1. **绝不引用未来根**：HTF 用 `shift(1)` 桶、pivot 用 `shift(right)` 确认；`on_candle(i)` 只读 `df[... ].iat[i]` 及更早。新增单测专门断言"改变 `i` 之后的数据不影响 `on_candle(i)` 的输出"。
2. **NaN 守卫**：任一依赖列在 `i` 处为 NaN → 返回 `[]`。
3. **状态管理（R1，与 `momentum_breakout` 的不同点）**：运行态 `_peak/_trough/_trail_armed` 在 `__init__` 建立，**仅**在 `on_candle` 空仓分支第一时间 `_reset_state()` 清零；**`prepare()` 不得写入这些状态**。这样在回测（`prepare` 一次）与 paper/live（`prepare` 逐根）两条路径下行为一致——既避免上一笔残留污染下一笔，又避免实盘逐根 `prepare` 把峰值/收紧态清零。（本方案在此点上**优于**现有 `momentum_breakout` 在 `prepare` 里置位的写法。）
4. **尺寸阈值**（CLAUDE.md）：单文件 < 800 行、单方法 < 50 行、嵌套 ≤ 4 层、行宽 ≤ 80。`on_candle` 若超 50 行，抽 `_entry_signal()/_exit_signal()` 私有方法。
5. **命名**：布尔量以 `is_/has_/should_` 起头不强制，但延用 `*_break/*_uptrend` 等语义清晰名；禁用 `data/tmp/obj`。

## 8. Testing & acceptance criteria（测试与验收）

**单元测试（`test_trend_pullback.py`，pytest，无网络）：**
- `test_pivot_confirmed_is_causal`：构造序列，断言确认型摆动点在 `k` 处的值不早于 `k+right` 出现（无未来函数）。
- `test_htf_regime_causal`：断言基础行 `i` 的 `htf_*` 只反映已收盘 HTF 桶。
- `test_long_emits_long_on_pullback`：构造"HTF 上涨 + 回踩进支撑区 + 缩量 + 收阳止跌"场景，断言出 `long`；构造下跌场景断言**不**出 `long`（门控生效）。
- `test_short_mirror`：镜像断言出 `short`。
- `test_staged_exit`：构造持多仓后仅 `fast_break`（不平、收紧），再 `slow_break`（平），断言收紧→全平的分级行为。
- `test_trail_disarm_on_recovery`（R5）：持多仓 `fast_break` 收紧后，价格 `c>fast_swing_high` 修复 → 断言 `_trail_armed` 解除、不被随后正常回踩误平。
- `test_state_survives_repeated_prepare`（R1，关键回归）：模拟 paper 路径——开多仓后**逐根重复调用 `prepare()` 再 `on_candle()`**，断言 `_peak` 持续累积、`_trail_armed` 不被 `prepare` 重置（即跟踪/收紧在实盘逐根 `prepare` 下仍有效）。
- `test_warmup_is_pure`（R2）：`warmup()` 在未调用 `prepare()` 时即可返回正整数且只依赖 `params`。
- 复用既有 `test_backtest_runs_for_all_strategies`（现遍历全注册表）：2 个新策略在 1H 合成数据上**可跑通**、`len(equity_points)==len(candles)`、回撤≤0。
- `test_registry_has_builtins`（改名自 `_has_five_builtins`，R7）：断言 2 个新 key ∈ 注册表且 `len>=7`。

**集成 / 验收：**
- `pytest services/quant`（或 `pnpm test` / `scripts/dev.py test`）全绿。
- 启动后 `GET /strategies` 返回含 2 个新 builtin（`builtin=true`、带完整 `paramsSchema`），`len>=7`。
- 前端策略列表能选中新策略、渲染参数面板、触发回测出权益曲线与买卖 marker，对比视图可并排（**前端零改动即生效**）。
- 前端 `tsc/vitest/build` 不受影响（未触前端）。

**Definition of Done（本设计节点）：** `docs/plans/trend-pullback-dual-strategies/spec.md` 存在、出现在 `git status`、含全部规定章节、篇幅 ≥ 800 字。

## 9. 验收口径与"稳健盈利"的诚实边界

平台为**模拟 / paper**，**不下真实单**；"稳健盈利"不可承诺。本方案以工程手段提升稳健性：趋势门控（只顺势）、区域入场（非单点）、双层止损（结构 + 兜底）、分级离场（抗假破）、波动率自适应（ATR 标尺）。是否真盈利须由**真实 OKX 行情**的回测 + walk-forward + paper 验证，并在对比视图中与既有 5 策略横评。验收只认"规则按 spec 落地 + 测试绿 + 真实数据可跑"，不以单次回测收益为准（避免曲线拟合误导）。

## 10. Risks & mitigations（风险与缓解）

| 风险 | 等级 | 缓解 |
|---|---|---|
| **未来函数**（HTF 重采样 / pivot 用到未收盘数据），违反 R4 | P0 | `shift(1)` 桶 + `shift(right)` 确认 + 专门因果单测；code review 重点核对。 |
| **引擎不支持部分平仓/加仓**，"分批"无法字面实现 | P1 | 显式映射为"区域入场 + 分级收紧跟踪止损"（§3.1 表）；在 `description` 与本 spec 注明为已知局限，未来若需真·分批须扩展 SimBroker（不在本次范围）。 |
| **粗基础周期（1H+）下 HTF 退化** | P1 | `base_seconds>=htf_seconds` 时回退基础周期 EMA，保证 `test_backtest_runs_for_all_strategies`（1H）不报错。 |
| **`warmup()` 早于 `prepare()` 调用**（paper） | P1 | `warmup()` 设计为 params 纯函数（不读 `prepare` 状态）；HTF 不足由 NaN 守卫兜住。**v2 已给出具体公式（§3.3 R2）。** |
| **`prepare()` 在 paper/live 逐根重调 → 持仓运行态被重置**（R1） | P1（v2 已修） | 运行态移入 `__init__`/`_reset_state`，仅在 `on_candle` 空仓分支按笔清零，`prepare` 不写状态（§2-4(b)、§3.3、§7-3）。专门单测覆盖"逐根 prepare 不破坏跟踪/收紧"。 |
| **区域半宽用基础 ATR 导致区域过窄、入场稀少**（R3） | P1（v2 已修） | 区域半宽改用 HTF 桶级 `htf_atr`；基础 ATR 仅用于止损距离（§3.2 两类 ATR、§3.1 映射表）。 |
| **HTF 摆动点算法二义阻碍确定性实现**（R4） | P1（v2 已修） | §3.2 钉死为"HTF 桶级 `htf_l/htf_h` 上计算 + shift(1) 桶因果映射"。 |
| **`_trail_armed` 只锁不解，假破间接触发离场**（R5） | P1（v2 已修） | 新增解除条件：结构重新站回（多 `c>fast_swing_high` / 空 `c<fast_swing_low`）即解除收紧（§3.3 step3、§3.4）。 |
| **参数过拟合 / 假信号** | P2 | 默认参数保守；全参数经 `params_schema` 暴露可调；鼓励 walk-forward / 对比横评。 |
| **既有测试 `test_registry_has_five_builtins` 名称/断言过期** | P2 | 同步更新为含新 key、`>=7`。 |
| **过早平仓导致"卖飞" / 过晚平仓回吐** | P2 | 分级离场 + 1h 门控保留底仓；参数可调 `trail_atr_mult(_tight)`。 |
| **dev DB 已有旧行不含新策略** | P3 | seed 幂等只补插，重启即生效；无需迁移。 |

## 11. 实施过程发现的方案缺陷（预留，由"代码开发"节点回填）

> 下游实现若发现本方案与引擎实际行为不符、或需偏离 §5 计划，请在此记录：偏差点、原因、实际采用做法、对验收的影响。设计节点暂留空。

**由 Subtask #2「代码开发」回填（均为方案授权范围内的取舍，非引擎契约冲突）：**

1. **C1/R6 面板收敛取舍——`params_schema` 落地为 11 项而非所列 10 项**：在评审建议的 10 个核心项之外，**额外保留 `max_hold_bars` 进 schema**。原因：既有 5 个内置策略**全部**暴露 `max_hold_bars`，去掉它会与既有 UI 习惯不一致；且 C2/R8 要求其周期换算文案可见，进 schema 才有 `label` 承载。其余 pivot/vol 细参（`fast/slow_pivot_left/right`、`vol_window`、`vol_spike_mult`、`vol_contract_ratio`）按 C1 **降级为仅 `default_params`**（可调不进面板）。17→11，已显著收敛过拟合面与面板长度。对验收无负面影响。
2. **C2/R8/R9 文案落地**：`max_hold_bars` 的 `label` 标注「(按周期换算)」；推荐基础周期 5m/15m 与「细周期需足够历史(如 5m+1h 约需 600 根才激活高周期 EMA，否则仅无信号而非报错)」已写入两策略 `description`。
3. **`vol_contracting` 按 §3.3「可选加分」处理**：已在 `prepare()` 计算并写入 df（满足数据模型 §6 的列清单），但入场**未**将其作为硬性 gate（保持"可选/加分"语义，不收窄入场）。如需更稳健可后续将其升为 gate，不属本次范围。
4. **环境观察（非方案缺陷，记录以便复现）**：本地 Python 环境最初缺 `pydantic-settings`（`requirements.txt` 已声明 `==2.7.1`），导致 `import app.config` 的测试模块（`test_api/test_compare/test_paper_replay/test_runs_list/test_market_headers/test_ws_market`）无法收集；与本次新增策略无关（新增 3 个策略文件均不 import `app.config`）。安装该**已声明**依赖后 `pytest services/quant` 全绿（**52 passed, 1 deselected**，deselected 为 `network` 标记的真实 OKX 端到端用例）。

---

## 评审结论（Review Verdict）

**结论：有条件通过（Approved with Conditions）。**

方案整体**可行、与现有架构一致、规模适配得当**：插件式注册 / SimBroker / DB seed / 前端通用渲染的复用判断准确（已逐一核对源码），把"主观看盘"映射为"单仓引擎可执行规则"的思路（区域门控替代分批挂单、分级收紧替代分批减仓、双层止损）是对引擎能力的正确妥协。本次评审发现的 **5 个 P0/P1**（实为全部 P1，无遗留 P0）已在 v2 正文**就地修复**：

1. **R1（实盘生命周期）**——运行态移出 `prepare()`、置于 `__init__`/`_reset_state`，新增逐根 `prepare` 回归测试；
2. **R2（warmup 公式）**——给出 params 纯函数实现；
3. **R3（区域量纲）**——区域半宽改用 HTF-ATR，基础 ATR 仅用于止损；
4. **R4（算法二义）**——HTF 摆动点计算唯一化为"桶级 + 因果映射"；
5. **R5（收紧只锁不解）**——新增结构修复后的解除条件。

**放行条件（交由 Subtask #2「代码开发」承接，均为 P2，不阻断进入开发）：**

- **C1（R6）**：`params_schema` 收敛到 ~10 个核心项，其余降级为 `default_params`；避免面板过长与过拟合面扩大。
- **C2（R8）**：`max_hold_bars`、推荐周期、最小历史根数（R9）须在 `description`/参数 `label` 文案中明确，避免误用。
- **C3（验收诚实边界）**：保留 §1/§9 的"无法承诺稳健盈利"声明；验收以"规则按 v2 落地 + `pytest services/quant` 全绿 + 真实 OKX 数据可回测/对比"为准，**不以单次回测收益判定通过**（防曲线拟合）。
- **C4（因果红线）**：实现必须配齐 §8 的因果性单测（`test_*_is_causal`、`test_htf_regime_causal`），R4 平台硬规则不可回退；code review 阶段重点复核 `shift(1)` 桶与 `shift(right)` 确认。

满足以上条件即视为完成；若开发中发现与本方案冲突，按 §11 回填并知会评审。

---
*（本 spec 为 **v2**：v1 设计 + 本节点评审记录/就地修复/评审结论；仍不含实现代码，不做 git commit。）*
