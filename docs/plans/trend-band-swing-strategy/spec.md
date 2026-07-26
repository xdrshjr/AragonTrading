# 大周期趋势带波段策略（12H/1D 定势 · 15m/5m 择时）设计方案

> Feature slug: `trend-band-swing-strategy`
> 策略 key: `trend_band_swing`；中文名「大周期趋势带波段」；`StrategyMeta.kind = "atomic"`（进「常规策略」页签）
> 版本：**v2**（Subtask #0 产出 v1 → Subtask #1 评审就地修复 2 个 P0 + 6 个 P1 后升 v2）
> 关联既有特性：`multi-timeframe-strategy-signals`（MTF 因果合并链路）、
> `trend-pullback-dual-strategies`（顺势回踩双策略，本方案的最近参照物）、
> `range-scalping-strategy`（区间剥头皮，带结构参照物）、`ai-trade-base-strategy-ma-band`（MA 带铁律层，
> 「贴带」量化口径的先例）
>
> **需求出处**（Task Board「Original Task」逐字要点）：
> 1. 用 **12 小时 与 一日** 两个维度的 K 线走势判断大趋势是涨是跌；
> 2. 判断价格是否贴近 12H / 1D K 线均值的**上边界 / 下边界 / 中间界**；
> 3. 做多还是做空，由「**趋势方向** × **贴哪条线**」共同决定；
> 4. 用户给的判定样例：**下跌势 + 已反弹 3 根 12H K 线 + 价格回到中轨附近 → 做空**；
> 5. 具体**买点卖点**由 **5 分钟与 15 分钟**的 K 线走势决定；
> 6. 小波段做 T 必须发生在大趋势方向内，**不得违背总趋势**；
> 7. 「可以进一步完善，使它成为顶级交易策略」。

---

## 评审记录（Review Notes · v1 → v2）

评审人：Anthropic 工程团队高级评审（Subtask #1）。评审维度：**可行性 / 完备性 / 一致性 / 规模适配**。
方法：不采信 v1 的纸面断言，对 §〇 的 20 条 As-built 证据逐条回读源码复核（结论：**A-1 ~ A-20 全部属实**，
仅 §2.8 的调用序伪代码与真实签名有出入，见 P2-1），再按节评审设计本身。

**结论摘要：2 个 P0、6 个 P1（**均已在本文档正文就地修复**）、9 个 P2（记录在案，交由实施节点权衡）。**

### P0（阻断级：按 v1 原文实现必然产出错误行为）

| # | 位置 | 问题 | 修复（已落入正文） |
|---|---|---|---|
| **P0-1** | §2.3 / §2.4 / §2.6 | **「贴带」与「目标带」判定用的是 12H 收盘 %B，粒度与「5m/15m 做 T」自相矛盾。** `tb_pct_b` 由 `causal_htf_merge` 从 12H 帧带入，**每 12 小时才跳变一次**，在两次 12H 收盘之间对所有基础根恒为常数。后果：(a) 进场 `zone` 一天最多换 2 次，5m 扳机沦为在固定 12H 判定内挑一根，§2.5 的择时层实际不产生额外分辨率；(b) 离场「目标带」同样 12 小时才可能命中一次，一次「中轨开空 → 下轨平空」的完整往返**至少要跨 2 个 12H 收盘（≥24h）**，而默认 `max_hold_bars=288`（5m×288=24h）会先到期强平 —— §2.6 宣称的「反复中轨开空→下轨平空→等下一次反抽」在默认参数下**几乎无法完成**，「做 T」这一核心卖点落空。 | 新增**实时 %B** 口径：带边界仍取最近**已收盘**的 12H 带（`tb_band_low/mid/up`），价格取**当根基础收盘** `c`，`live_pct_b = (c − tb_band_low) / (tb_band_up − tb_band_low)`。`zone` 与目标带判定一律改用 `live_pct_b`；趋势方向、游程、回撤幅度仍取 12H 收盘值（它们本就是多根 12H 的概念）。严格因果：带来自已收盘高周期根，价格是当根基础收盘，信号次根开盘成交（A-9）。见 §2.3、§2.4、§2.6。 |
| **P0-2** | §2.7 运行态表 | **`_extreme_px`「空仓时清零」对空头是错误哨兵。** 空头跟踪止盈基准取 `min(l)`，初值 `0.0` 使 `_extreme_px = min(0.0, l) ≡ 0.0`，离场检查 `c >= _extreme_px + trail_atr_mult × cf_atr` 对任何真实价格**恒真** → **每一笔空单都会在开仓后第一根被「跟踪止盈」平掉**。更危险的是 v1 的 T-12 只断言「跟踪止盈仍能触发」，该 bug 会让测试**顺利通过**，缺陷可一路漏到实盘。 | `_extreme_px` 类型改 `float \| None`，哨兵为 `None`；持仓首根用当根 `l`（空）/ `h`（多）初始化，此后才 `min`/`max` 累进；`None` 时跳过跟踪止盈检查（离场 fail-open，I-2）。T-12 拆成「未达阈值**不得**触发」+「达阈值触发」两条断言，负向断言在前。见 §2.6、§2.7、§6.1。 |

### P1（必须修：正确性、完备性或事实错误）

| # | 位置 | 问题 | 修复（已落入正文） |
|---|---|---|---|
| **P1-1** | §2.7、§七 R-7 | **事实错误：OKX 的 `12H` / `1D` 是港时（UTC+8）对齐，不是 UTC。** OKX v5 `bar` 参数中 `[6H/12H/1D/1W/1M]` 为「香港时间开盘价 K 线」，UTC 对齐需用 `12Hutc` / `1Dutc` 后缀；本仓 `BAR_SECONDS` / `BAR_TO_WS_CHANNEL` 用的都是无后缀形式（`okx/types.py:93-102`），故真实 1D 根满足 `ts % 86400 == 57600`（16:00 UTC 开盘）。因此：(a) R-7 的「兜底路径用 `ts // 43200` 分桶，与 OKX 对齐同源」是**假前提**，两条路径相位差 4 小时；(b) §2.7 的桶 id `ts // BAR_SECONDS[trend_bar]` 与真实 12H 根**错位 4 小时**，「同一根 12H 内最多开 N 次」名不副实（会横跨两根真实 12H）。注：`causal_htf_merge` 本身用帧自带 `ts + htf_seconds` 做收盘对齐，**真实帧路径的因果性不受影响**，故不是 P0。 | `band_features` 增产一个数值列 `bar_ts`（该高周期根自身的 `ts`），经 `causal_htf_merge` 变成 `tb_bar_ts`（该函数只 drop `ts` / `_close_ts` 两列，`bar_ts` 会原样保留 —— `engine/mtf.py:85`）。频控桶身份直接取 `tb_bar_ts`，与交易所对齐方式**完全解耦**，语义精确为「同一根真实趋势 K 线」。R-7 改写为陈述实情。见 §2.3、§2.7、§五、§七 R-7。 |
| **P1-2** | §2.4 位置层 | **`zone_of` 未定义 %B 落在 `[0,1]` 之外的语义。** 破轨行情（`pct_b < 0` 或 `> 1`）恰恰是趋势加速段，按 v1 的三个双边锚区定义会落入 `none` → **静默不开仓**；而这正是 `allow_edge_entry=True` 想覆盖的「顺势反抽上轨 / 顺势回踩下轨」场景的极端形态。这是一个会让门禁在最该表态时沉默的空洞。 | 外侧两锚改**单边判据**：`pct_b <= tol → lower`；`pct_b >= 1 − tol → upper`；`\|pct_b − 0.5\| <= tol → mid`；其余 `none`。`tol ≤ 0.24` 下三区仍两两互斥（T-4 断言不变且更强）。见 §2.4。 |
| **P1-3** | §2.10 参数全表 | **`warmup()` 全文未定值。** 它决定回测起信号根（`backtester.py:94,102`）与 paper 窗口尺寸 `_window_max`（`paper/session.py:66-74`），且**单位是基础根**。若实施者望文生义按高周期根数填（例如把 `trend_ema_slow=60` 当成 60 根 12H），会同时撑大实时窗口并推迟信号；若填得过小，则基础扳机列（`trig_ema` / `trig_rsi`）尚未激活就开始产信号。v1 只在 §三 的方法清单里出现 `warmup` 三个字，没有任何取值约定。 | §2.10 增「非面板派生量」小节明确：`warmup() = max(trigger_ema, trigger_rsi_period, retrace_lookback) + 5`（默认 ≈ 25 **基础根**），且**必须是 params 的纯函数**（`warmup()` 早于 `prepare()` 调用）；高周期暖机由 `htf_warmup_bars()` 独立负责，两者不得混算。见 §2.10。 |
| **P1-4** | §2.6 离场表 + §2.10 | **结构止损与 broker 兜底止损的层级被默认值倒置。** `stop_atr_mult=1.2 × 12H ATR` 在 BTC 上约合 2~3%，已贴近 `stop_loss_pct=0.03`；而策略发出的 `close` 信号要**次根开盘**才成交（A-9），broker 的 `stop_loss_pct` 却是**根内触发价**立即成交（`simulator.py:180-192`）。两者叠加后，绝大多数情况下 `stop_loss` 会先于「结构止损(12H ATR)」触发 —— §2.6 的优先级表形同虚设，成交 `reason` 归因也随之失真（用户看到的全是 `stop_loss`，看不出是哪条结构线被打）。 | `stop_loss_pct` 默认由 `0.03` 改 **`0.05`**，并在 §2.6 注明「兜底必须显著宽于结构止损，否则结构止损永不生效」；G-3 增加核对项：成交 `reason` 中应能看到「结构止损(12H ATR)」。见 §2.6、§2.10、§6.3。 |
| **P1-5** | §2.2 三条链路 | **一次声明三个高周期帧的取数成本 / 限频风险零评估。** `load_htf_frames` 对每个周期各走一次 `_page_candles_back`，而 `_PAGE_CHUNK=1000`、`get_candles(limit=1000)` 内部会翻最多 12 次 `history-candles`（`agent/services.py:39,66-68`、`okx/rest_client.py:162-167`）。以 5m 基础 / 30 天区间估算：15m 帧 `target≈2955` → 约 36 次请求，12H / 1D 各约 12 次，**较单周期策略额外增加约 60 次 REST 请求**；OKX `history-candles` 限频约 20 次/2s，叠加基础 5m 自身的约 108 次后，单次回测的取数墙钟明显变长。既有 MTF 策略（`trend_pullback_*` / `ema_cross_scalper`）都只声明**一个**高周期，本策略是仓内第一个声明三个的，属新增风险面。 | §2.2 增「取数成本与限频」小节，给出量级与缓解：`confirm_bar` 默认保持 `15m`（设成 `5m` 会被 `load_htf_frames` 的 `htf_seconds <= base_seconds` 分支直接跳过，反而无谓）；长区间优先走 `/multi-backtest` 的分标的串行；`htf_warmup_bars()` 不得为了保险而虚高（它同时放大三个周期的 `target` 与左界回溯）。G-3 增记录一次回测墙钟时长。见 §2.2、§6.3、§七 R-13。 |
| **P1-6** | §2.1 不变式表 | **`flip_exit` 是面板可关参数，却与铁律 I-3「持仓期间一旦 bias 翻转即平仓」直接冲突。** 一条能被用户开关否决的规则不是不变式；而 §2.10 又写「关掉等于放弃铁律」、G-6 还要求实测关掉后能跑通 —— 三处自相矛盾，实施者无从判断该不该把 `flip_exit` 写进 schema。 | I-3 拆成两条：**I-3a（硬不变式，无任何参数可绕过）**「开仓方向恒 `== bias`，任何小周期条件都不得产生与 `bias` 相反的开仓」；**I-3b（默认开启的策略项）**「`bias` 翻转/失效即平仓，由 `flip_exit` 控制」。§2.10 `flip_exit` 行注明「关掉后 I-3b 不成立、I-3a 仍成立」，G-6 相应改写。见 §2.1、§2.10、§6.3。 |

### P2（记录在案，不阻断放行；实施节点自行权衡）

| # | 位置 | 问题 |
|---|---|---|
| P2-1 | §2.8 | 调用序伪代码与真实签名不符：实际是 `fill_pending(open_price=, ts=, bar_index=)`、`apply_risk(i, row, risk)`（三参）、`mark(ts=, close=)`（`backtester.py:99-101`）。已在正文顺手更正为真实签名。 |
| P2-2 | §2.3 | `_run_length` 参考实现有两处脆弱点：`flag.fillna(False)` 后若 `flag` 是 float 序列，`~flag` 会抛 `TypeError`；`groupby(...).cumsum()` 直接作用于 bool dtype 在不同 pandas 版本行为不一致。建议先 `astype(bool)` 再 `astype("int8")` 后累加。已在正文给出更稳的写法。 |
| P2-3 | §2.9 | `max_hold_bars` 在 paper/live 下因 `i` 冻结（A-12）**恒不触发**（`bar_index - entry_bar ≡ 0`，`simulator.py:195`）。v1 的降级表未列这一条，而 §2.6 把它写进兜底行，读者会以为实时链路也有 24 小时上限。已补进 §2.9。 |
| P2-4 | §2.9 | `12H` 不在 `BAR_TO_WS_CHANNEL`（`okx/types.py:82-90`），故**基础周期不可设为 12H 跑实时模拟**（WS 订阅无对应频道）。§2.9 末行「基础周期设成 12H/1D」的描述对 paper/live 不成立。已补注。 |
| P2-5 | §2.10 | `trend_bar="1D"` + `macro_bar="12H"` 会让「宏观」周期快于「交易」周期，语义倒置但不报错也不崩。设计上不强制大小关系是可以的，但应在文档写明，避免被当成 bug 上报。已补注。 |
| P2-6 | §6.1 | `attach_timeframes` 可能收到**部分帧**：paper 侧 `frames` 只装非空窗口（`paper/session.py:307-308`），12H 取数失败而 15m 成功时就是部分帧。T-9 只覆盖空 dict。已增 T-16 覆盖部分帧。 |
| P2-7 | §6.1 T-14 | 前缀不变性用例必须对两次运行**各自从头重放**（策略含会话级运行态 `_cooldown_until_ts` / `_bucket_*`），否则两侧状态不可比、断言无意义。已在 T-14 注明协议。 |
| P2-8 | §2.10 | `trend_slope_min=0.10` 配 `trend_slope_lookback=3` 在 12H/1D 上门槛偏松，`trend_dir=0`（震荡）几乎不会出现，决策表「双震荡 ⇒ 不交易」一行近似空转。不构成缺陷（保守方向是少一层过滤而非放开方向铁律），但建议实施后用一次真实区间回测标定 `trend_dir` 的三值分布。 |
| P2-9 | §6.3 G-2 | 前端「参考周期」徽标只认 `params.htf_bar` + `htf_trend_filter`（`StrategyDetail.tsx:150`），本策略三个周期参数都不叫这个名，**不会显示徽标**。这不影响「零前端改动」结论（参数面板 `enum/bool/int/number` 四型确已支持，`StrategyDetail.tsx:187-223`），但 G-2 不应把徽标列为期望。已在 G-2 注明。 |
| P2-10 | §三 / §六 | 内部计数不一致：§三文件表写「14 个用例」，§六实际列出 15 个。已统一为修复后的 **18** 个（新增 T-12 拆分、T-16 部分帧、T-17 实时 %B）。 |

### 未发现问题的维度（明确背书）

- **可行性**：新增策略的代价确为「新文件 + `@register_strategy` + `__init__.py` 一行 import」，注册表是 meta 唯一真源，`merge_strategy` 从 meta 取 `kind` / `description` / `paramsSchema`（`db/repositories.py:41-55`），前端 `splitStrategyCatalog` 按 `kind` 分流（`strategyCatalog.ts:22`）—— **「零前端、零迁移、零端点」成立**。
- **一致性**：I-1（`prepare` 无条件建全列）与 A-17 的全局冒烟用例吻合；I-2 的「开仓 fail-closed / 离场 fail-open」极性划分与 `ai-trade-profit-protection` 的既有教训一致；「合并前只产数值列」严格遵守 `engine/mtf.py:66-70` 的硬规则。
- **规模适配**：不新增端点 / 表 / 前端，把「不得违背总趋势」落成硬不变式而非权重项，是**恰当**的克制；§八 的 7 条非目标（尤其「不做加减仓」「不做连续评分」）划界准确，与 `simulator.py` 单向单仓的平台事实相符。

---

## 〇、As-built 对齐（事实基线）

沿用本仓房规：**以 as-built 代码为准，不据历史 spec 的纸面签名**。下列断言均为本次动工前实测，
`file:line` 为实测行号。下游节点若发现与实际不符，**以代码为准并回写到 §「实施过程发现的方案缺陷」**。

| # | 断言 | 证据 |
|---|---|---|
| A-1 | 「常规策略 / 组合策略 / 因子策略」三页签的真源是 `StrategyMeta.kind ∈ {atomic, combo, factor}`；前端分类表 `STRATEGY_LIST_TABS` 在 `strategyCatalog.ts:4-8`，`regular = kind !== "combo" && kind !== "factor"` | `apps/web/lib/strategyCatalog.ts:4-8,22`、`strategies/base.py:34-37` |
| A-2 | 新增策略的完整代价 = **新文件 + `@register_strategy` + `__init__.py` 一行 import**；注册表 `STRATEGY_REGISTRY` 是 meta 的唯一真源，引擎 / 接口 / 前端全部零改 | `strategies/base.py:3-5,40-53`、`strategies/__init__.py:14-24` |
| A-3 | 后端启动时 `init_db()` 幂等 seed：注册表里每个模板确保有一行 `builtin=True` 的 `strategies` 记录，**已存在的库只补新增 key**，无迁移、无手工建行 | `db/session.py:68-80,190-208` |
| A-4 | 多周期（MTF）声明式 hook 共三个：`timeframes()` / `htf_warmup_bars()` / `attach_timeframes()`，默认实现分别是 `[]` / `60` / 恒等 —— 未声明高周期的策略**零开销** | `strategies/base.py:69-89` |
| A-5 | MTF 因果对齐的唯一真源是 `causal_htf_merge`：对齐键取「高周期**收盘**时间 = ts + htf_seconds」，`merge_asof(backward)`，天然杜绝引用未收盘的当根高周期 | `engine/mtf.py:9-13,50-86` |
| A-6 | **`compute` 只许产出数值列**：`merge_asof` 对未匹配基础行回填 NaN，而 `bool(float('nan')) is True` 会**静默反转**门控；布尔列必须在合并之后由数值列派生 | `engine/mtf.py:66-70`、`_trend_structure.py:182-186,208-215` |
| A-7 | 回测取数链路：`api/strategies.py:100` → `services.run_single_backtest` → `load_range_candles`(基础) + `load_htf_frames`(每个严格高于基础周期的 bar，左界额外回溯 `htf_warmup_bars × htf_sec`) | `agent/services.py:82-136,282` |
| A-8 | `run_backtest` 的装配序：`candles_to_df` → `strategy.prepare` → **仅当 `htf_frames` 非空**才 `strategy.attach_timeframes` → 逐根 `fill_pending / apply_risk / mark / on_candle+submit` | `engine/backtester.py:82-107` |
| A-9 | **信号在下一根开盘价成交**（`submit` 只排队，`fill_pending` 用次根 open 成交）；最后一根不再开新仓 | `engine/simulator.py:156-167,206-224`、`engine/backtester.py:102` |
| A-10 | **SimBroker 单向单仓、不支持加减仓**：`strength ∈ [0,1]` 只在开仓时决定名义敞口 = `strength × equity × leverage`；同向重复信号被 `submit` 直接跳过 | `engine/simulator.py:5,206-224,256-266` |
| A-11 | **`risk_params()` 在回测里只被读取一次**（循环外 `set_risk`），故**按笔动态止损无法走 broker**，必须由策略自己发 `close` 信号；paper/live 每根重读一次 | `engine/backtester.py:91-93`、`paper/session.py:314-317` |
| A-12 | **paper/live 的 `i` 会冻结**：`i = len(self._candles) - 1`，而 `self._candles` 是 `maxlen=self._window_max` 的 deque —— 窗口填满后 `i` 恒为 `window_max-1`。因此**任何按 bar index 计时的逻辑在实时链路都是错的**（既有 `max_hold_bars` 亦受影响，属既有平台缺陷，本方案只规避不修） | `paper/session.py:75,300,315-317`、`live_auto/session.py:70-71` |
| A-13 | 实时窗口上限 `PAPER_WINDOW_MAX = 1200`；HTF 窗口按 `min(window_max, max(htf_warmup, span_htf) + 5)` 预填，实时段**仅跨高周期边界时才刷新一次**（限频），取数失败降级为空窗、绝不造假数据 | `paper/session.py:22,66-82,167-216`、`live_auto/session.py:378-419` |
| A-14 | 每个 paper/live 会话**新建策略实例**（`cls(params)`），故策略实例内的运行态不会跨会话共享 | `paper/strategy_factory.py:37-54` |
| A-15 | `engine/indicators.py` 现成可用：`ema / sma / rsi / bollinger(→(upper, ma, lower)) / atr / vwap_session`；**无** Keltner / %B / run-length 助手 | `engine/indicators.py:14,19,24,38,48,60` |
| A-16 | `BAR_SECONDS` 含 `12H=43200`、`1D=86400`，二者都是可向 OKX 取真实 K 线的标准 bar | `okx/types.py:93-102` |
| A-17 | `test_backtest_runs_for_all_strategies` 会把**注册表里每个 atomic 策略**喂进「300 根 1H 合成 K 线 + **无 HTF 帧**」的 `run_backtest`——新策略必须在缺帧下**不抛异常**（可以零成交） | `tests/test_strategies.py:68-82` |
| A-18 | MTF 零回归护栏 `_UNCHANGED_KEYS` 是**显式白名单**（5 个 key），新增声明高周期的策略不会打破它 | `tests/test_strategies.py:152-175` |
| A-19 | 桌面端生产库在 `%APPDATA%/aragon-desktop/aragon.db`（仓库根的 `services/quant/aragon.db` 是过期空壳）；验收「策略是否出现在常规页签」须以生产库为准 | 记忆项 `aragon-live-db-location-and-ai-trade-ts-dual-clock` |
| A-20 | ruff 实际门禁 `line-length = 110`（CLAUDE.md 建议 80）；既有策略文件的 `params_schema` 单行字典普遍在 100~110 列 | `services/quant/pyproject.toml`、`strategies/trend_pullback_long.py:56-68` |

---

## 一、概述（Overview）

本特性给「常规策略」新增一个原子策略 `trend_band_swing`（大周期趋势带波段）。它把交易决策**显式拆成三层**，
每层只回答一个问题：**方向层**用 12H 与 1D 两个高周期回答「现在只能做多、只能做空、还是不许做」；
**位置层**用 12H 布林带（均值 ± n×σ，中轨即均线）回答「价格现在贴的是上轨、中轨还是下轨，这个位置值不值得
按方向层的方向进场」；**择时层**用 15m 结构确认 + 基础周期（推荐 5m）扳机回答「具体这一根进不进」。
用户需求里的判定样例 —— 下跌势中价格反弹 3 根 12H K 线回到中轨附近就做空 —— 在本设计中是方向层输出
`bias=short`、位置层判定 `zone=mid` 且「反向连涨根数 ≥ 阈值」、择时层在 5m 出现动能破位时开空的**自然合成**，
不是特例分支。

之所以要分这三层，是因为需求里那句「**不能违背总趋势**」是一条**铁律**，而不是一个可以被小周期信号推翻的
权重项。本方案把它落成一条硬不变式（**I-3a，无任何参数可绕过**）：**开仓方向恒等于方向层输出的 `bias`；
`bias` 为 `flat`（两个高周期方向冲突、或任一高周期数据不可用）时一律不开仓。** 与之配套但**性质不同**的
是 I-3b：「持仓期间 `bias` 翻转或失效即平仓」，由 `flip_exit` 控制、默认开启 —— 前者是安全属性，后者是
可调的持有偏好，二者必须分开（v2·P1-6，见 §2.1）。小周期只能决定
「在允许的方向上，什么时候进、什么时候出」，**永远无权决定方向**。所谓「做 T」在本策略里的准确含义是：
在同一段大趋势内，沿着同一个方向反复完成「贴带进场 → 到目标带离场 → 等下一次贴带」的短周期往返，
而不是多空来回换手——这也正是 SimBroker 单向单仓、不支持加减仓（A-10）这一平台事实所允许的唯一实现形态。

工程上，本特性**不新增任何 REST/WS 端点、不动数据库结构、不改一行前端代码**。它完全落在既有的可插拔策略框架
与多周期（MTF）链路里：新增两个后端文件（一个纯函数特征/决策模块、一个策略类），在 `strategies/__init__.py`
追加一行 import 与一个 `BUILTIN_ORDER` 条目，随后回测、多样本回测、Walk-forward、策略对比、实时模拟（paper）、
实盘自动交易（live-auto）、组合策略成员、AI 自动交易信号板——**全部自动获得**这条策略（A-2/A-3）。所有新增
计算均为向量化纯函数、严格因果（无未来函数），并遵守 MTF 的「合并前只产数值列」硬规则（A-6）。

---

## 二、技术设计

### 2.1 分层架构与四条铁律不变式

```
                ┌──────────────────────────────────────────────┐
   1D 真实K线 ──►│ 方向层 (Direction)                            │
  12H 真实K线 ──►│  trend_dir(1D) × trend_dir(12H) → bias        │──► long / short / flat
                │  冲突或缺数据 ⇒ flat（铁律 I-2 fail-closed）  │      + conviction
                └──────────────────────────────────────────────┘
                                     │ bias ≠ flat
                                     ▼
                ┌──────────────────────────────────────────────┐
  12H 布林带 ──►│ 位置层 (Location)                             │
                │  %B → zone ∈ {lower, mid, upper, none}        │──► 允许 / 不允许开仓
                │  决策表 §2.4：顺势回归才开，逆势与追单一律否  │
                │  + 反向连涨/连跌根数、回撤幅度（"反弹三根"）  │
                └──────────────────────────────────────────────┘
                                     │ 允许
                                     ▼
                ┌──────────────────────────────────────────────┐
  15m 真实K线 ──►│ 择时层 (Timing)                               │
   5m 基础K线 ──►│  15m 结构确认（EMA 关系 + 归一化斜率）        │──► Signal(side=bias)
                │  5m 扳机（收阴/阳 + 破前根高低点 + EMA + RSI） │
                └──────────────────────────────────────────────┘
                                     │
                                     ▼
                   离场层（§2.6）：目标带 / 结构止损 / 跟踪止盈 / 趋势失效
```

**四条不变式（实现与评审都必须逐条守住）**

| # | 不变式 | 为什么 |
|---|---|---|
| **I-1** | `prepare()` **必须无条件建出** `on_candle()` 会读的**全部**列（数据不足时填 `NaN`），`attach_timeframes()` 只做「有真实帧就覆盖」 | 否则 `test_backtest_runs_for_all_strategies`（A-17）在无帧场景会 `KeyError` 崩测；也保证 `on_candle` 的 NaN 守卫是**唯一**失败闸门 |
| **I-2** | **开仓 fail-closed**：任一决策输入为 `NaN` / `bias == flat` ⇒ 返回 `[]`。**离场 fail-open**：缺数据只跳过该项检查，**绝不因缺数据而阻塞平仓** | 开仓侧「不知道就不做」；离场侧「不知道也要能跑」。这两条极性相反，混淆任意一条都是事故（参照 `ai-trade-profit-protection` 的 fail-open 教训） |
| **I-3a** | **硬不变式（无任何参数可绕过）**：开仓方向恒 `== bias`；**任何小周期条件都不能产生与 `bias` 相反的开仓** | 需求原文「不能违背这个总趋势的原则」。这条不接受开关：能被面板否决的规则不是不变式（v2·P1-6） |
| **I-3b** | **默认开启的策略项**：`bias` 翻转 / 失效即平仓，由 `flip_exit` 控制（默认 `True`） | 与 I-3a 分离：「不许逆势开仓」是安全属性，「趋势失效就走人」是可调的持有偏好；关掉 `flip_exit` 只放弃后者，前者仍然成立（v2·P1-6） |
| **I-4** | 一切计时按 **`ts` 秒级**，**禁止**按 `i`（bar index）计时 | A-12：paper/live 下 `i` 会冻结在 `window_max-1`，按 index 的冷却/计数在实盘必然失效 |

### 2.2 三条数据装配链路（同一策略对象，三个调用方）

| 链路 | 基础 K 线来源 | 高周期帧来源 | 关键约束 |
|---|---|---|---|
| 回测 / 多样本 / WF / 对比 | `load_range_candles`（全区间分页） | `load_htf_frames`：对 `timeframes()` 中**严格高于基础周期**的每个 bar 取真实 K 线，左界额外回溯 `htf_warmup_bars() × htf_sec`（A-7） | 帧齐全 ⇒ 走真实 12H/1D；`run.config.htfBars` 会记录实际取到的周期，可事后核查 |
| 实时模拟（paper） | OKX WS 确认根 + 启动预热 | `_prefill_htf` 一次性预填 + `_maybe_refresh_htf` 跨高周期边界限频刷新（A-13） | 12H/1D 每天最多各刷新 2 次 / 1 次，对 OKX 限频无压力 |
| 实盘自动（live-auto）/ AI 信号板 | 同上（`live_auto/session.py` 镜像实现） | 同上 | 同上；策略实例每会话新建（A-14） |

**因果性**：三条链路的高周期值最终都经 `causal_htf_merge` 按「高周期收盘时间」向后对齐（A-5），
基础周期第 `i` 行只可能读到**已收盘**的 12H/1D 值；重采样兜底路径同样 `shift(1)` 桶。**无未来函数**。

> **实时 %B 不破坏因果性**（v2·P0-1）：`live_pct_b` 的**带边界**取自已收盘高周期根、**价格**取当根基础
> 收盘 `c`。`c` 是当根收盘后才可读的量，而信号在**次根开盘**才成交（A-9），因此与既有全部策略读
> `row["c"]` 的口径完全一致，仍是严格因果。

#### 2.2.1 取数成本与限频（v2·P1-5）

本策略是本仓**第一个一次声明三个高周期**的策略（既有 MTF 策略都只声明一个），取数成本须显式记账：

| 周期 | 5m 基础 / 30 天区间的 `target` | 估算 REST 请求数 | 说明 |
|---|---|---|---|
| 15m | `2880 + htf_warmup(75) ≈ 2955` | ≈ 36 | `_PAGE_CHUNK=1000`，每次 `get_candles(limit=1000)` 内部翻最多 12 次 `history-candles` |
| 12H | `60 + 75 = 135` | ≈ 12 | 首次外层调用即取满 1000 根后 break |
| 1D | `30 + 75 = 105` | ≈ 12 | 同上 |
| （基础 5m） | `8640` | ≈ 108 | 既有开销，非本特性新增 |

即**新增约 60 次请求（较单周期策略约 +55%）**。OKX `history-candles` 限频约 20 次/2s，故一次 30 天回测的
取数墙钟约在十几秒量级。缓解与约束：

1. `confirm_bar` 默认保持 `15m`，**不要下调到 `5m`** —— 与基础周期同级会被 `load_htf_frames` 的
   `htf_seconds <= base_seconds` 分支直接跳过（`agent/services.py:126-127`），既省不下也拿不到，反而让
   `cf_*` 静默走重采样兜底；
2. `htf_warmup_bars()` **不得为求保险而虚高**：它同时放大三个周期的 `target` 与左界回溯
   （`oldest_stop = start_ts − htf_warmup_bars × htf_sec`，对 1D 就是往前 75 天）；
3. 长区间评估优先走 `/multi-backtest` 的分标的串行，不要并发多标的同时打 OKX。

### 2.3 特征层：`_band_structure.py` 的列定义

所有特征由**同一个纯函数** `band_features(frame, cfg)` 在**任意 OHLC 帧**上计算（真实 HTF 帧或重采样桶帧），
输出**全部为数值列**（A-6 硬规则）。设 `c=收盘, o=开盘, h=最高, l=最低`：

| 列名（无前缀） | 公式 | 说明 |
|---|---|---|
| `bar_ts` | `frame["ts"].astype("int64")` | **该高周期根自身的开盘 ts**（v2·P1-1）。合并后成为 `tb_bar_ts`，是「同一根真实趋势 K 线」的**身份标识**，频控桶直接取它，与交易所的对齐时区完全解耦。`causal_htf_merge` 只 drop `ts` / `_close_ts` 两列（`engine/mtf.py:85`），`bar_ts` 会原样保留并加前缀 |
| `ema_fast` | `ema(c, cfg.ema_fast)` | 快 EMA |
| `ema_slow` | `ema(c, cfg.ema_slow)` | 慢 EMA |
| `atr` | `atr(frame, cfg.atr_period)` | Wilder ATR，作波动率标尺 |
| `slope_atr` | `(ema_slow - ema_slow.shift(cfg.slope_lookback)) / atr` | **ATR 归一化斜率**（无量纲），使阈值跨标的/跨周期可比 |
| `band_up` / `band_mid` / `band_low` | `bollinger(c, cfg.band_period, cfg.band_std)` | `band_mid` 即需求所说「均线中间界」，`band_up/low` 即「上下边界」 |
| `band_width` | `(band_up - band_low) / band_mid`（`band_mid<=0` ⇒ NaN） | 相对带宽，用于挤压（squeeze）过滤 |
| `pct_b` | `(c - band_low) / (band_up - band_low)`（分母 `<=0` ⇒ NaN） | **%B（高周期收盘口径）**：0=下轨、0.5=中轨、1=上轨。与 `market_digest.bollPctB` 同口径。**只用于 `rebound_pct_b` / `pullback_pct_b` 这类多根概念**，不再用于 `zone` 判定（v2·P0-1） |
| `trend_dir` | `+1.0 / -1.0 / 0.0`，任一输入 NaN ⇒ **NaN** | `up = ema_fast>ema_slow 且 slope_atr>=cfg.slope_min`；`down = ema_fast<ema_slow 且 slope_atr<=-cfg.slope_min`；否则 0（震荡） |
| `up_run` / `down_run` | `c > c.shift(1)` / `c < c.shift(1)` 的**连续游程长度** | 需求样例「上涨了三个 12 小时 K 线」的量化载体 |
| `rebound_pct_b` | `pct_b - pct_b.rolling(cfg.retrace_lookback).min()` | 自近端最低 %B 的**反弹幅度**（带宽单位） |
| `pullback_pct_b` | `pct_b.rolling(cfg.retrace_lookback).max() - pct_b` | 自近端最高 %B 的**回撤幅度** |

> **`trend_dir` 必须是 float 列而不是布尔列**：合并未匹配行会被 `merge_asof` 填 NaN，而 `bool(nan) is True`
> 会把「无数据」静默翻译成「趋势成立」（A-6）。本方案**全程不产布尔列**，判定一律在 `on_candle` 内用
> `pd.isna(...)` 显式守卫后再比较。

**实时 %B（`live_pct_b`）—— 决策时口径**（v2·P0-1，核心修正）：

`zone`（贴哪条带）与「目标带」离场**一律不用高周期收盘 `pct_b`**，改用带边界与价格分离的实时口径：

```python
def live_pct_b(close: float, band_low: float, band_up: float) -> float:
    """当根基础收盘价在【最近已收盘高周期带】中的相对位置；带退化(分母<=0)返回 NaN。

    带边界来自已收盘的 12H 根（每 12 小时才更新），价格是当根基础收盘（5m 即每 5 分钟更新）。
    这样「贴带」判定拿到的是基础周期的分辨率，而不是被冻结 12 小时的常量。
    """
    span = band_up - band_low
    if not span > 0:
        return float("nan")
    return (close - band_low) / span
```

> **为什么必须这样**：`tb_pct_b` 是 12H 根的收盘 %B，在两次 12H 收盘之间对所有基础根**恒为常数**。
> 若用它判 `zone`，进场机会一天最多换 2 次、15m/5m 择时层不产生任何额外分辨率；离场「目标带」同样
> 12 小时才可能命中一次，一次「中轨开空 → 下轨平空」的往返至少要跨 2 个 12H 收盘（≥24h），会被
> `max_hold_bars` 先行强平 —— 需求原文的「做 T」将无法成立。这是 v1 最严重的缺陷（P0-1）。
> 它是**决策时的纯函数**而非 `prepare` 列：如此在「重采样兜底」与「真实帧覆盖」两条路径下自动同源
> （读到什么 `tb_band_*` 就用什么），也不必为它承担 I-1 的列契约义务。

游程（run-length）向量化实现（纯函数、因果；v2·P2-2 加固 dtype）：

```python
def _run_length(flag: pd.Series) -> pd.Series:
    """连续 True 的游程长度（False 处归零）；NaN 视为 False。

    显式 `astype(bool)` 再转 int8：入参若为 float 序列，`~flag` 会抛 TypeError；
    bool dtype 直接进 groupby().cumsum() 在不同 pandas 版本行为不一致。
    """
    mask = flag.fillna(False).astype(bool)
    group = (~mask).cumsum()
    return mask.astype("int8").groupby(group).cumsum().astype(float)
```

**三个周期、三个前缀**（同一 `band_features` 跑三次）：

| 前缀 | 周期参数 | 默认 | 用途 |
|---|---|---|---|
| `tb_` | `trend_bar` | `12H` | **交易趋势周期**：方向层主投票 + 位置层（带、%B、游程）全部取自它 |
| `mb_` | `macro_bar` | `1D` | **宏观趋势周期**：方向层第二票（一致性/共识） |
| `cf_` | `confirm_bar` | `15m` | **择时确认周期** |

> **前缀而非 bar 名做列前缀**是有意的：`ema_cross_scalper` 用 `htf_{bar}_` 前缀（`ema_cross_scalper.py:87`），
> 那样在 `on_candle` 里要用 f-string 反复拼列名；本策略有三个周期且**允许两两相同**（如 `trend_bar` 与
> `macro_bar` 都填 `1D`），固定前缀让「同一帧喂两个前缀」变成一行循环，也让列名在测试里可直接硬编码。

### 2.4 方向层 + 位置层：唯一决策表

**方向层** `classify_bias(tb_trend_dir, mb_trend_dir, cfg) -> (bias, conviction)`（纯函数）：

| `tb_trend_dir`(12H) | `mb_trend_dir`(1D) | `bias` | `conviction` | 备注 |
|---|---|---|---|---|
| +1 | +1 | `long` | 1.0 | 双周期共振多头 |
| −1 | −1 | `short` | 1.0 | 双周期共振空头 |
| +1 | 0 | `long` | `partial_strength_scale`(0.6) | 仅当 `allow_partial_bias=True`，否则 `flat` |
| 0 | +1 | `long` | `partial_strength_scale`(0.6) | 同上 |
| −1 | 0 | `short` | `partial_strength_scale`(0.6) | 同上 |
| 0 | −1 | `short` | `partial_strength_scale`(0.6) | 同上 |
| +1 | −1 | **`flat`** | 0.0 | **冲突 ⇒ 不许交易**（铁律 I-3a） |
| −1 | +1 | **`flat`** | 0.0 | **冲突 ⇒ 不许交易**（铁律 I-3a） |
| 0 | 0 | `flat` | 0.0 | 双震荡 ⇒ 不交易 |
| 任一为 `NaN` | 任意 | **`flat`** | 0.0 | **fail-closed**（铁律 I-2） |

**位置层** `zone_of(pct_b, tol)`（入参是 **`live_pct_b`**，非 `tb_pct_b`；`tol = band_touch_tol`，默认 0.15，
**schema 上界锁 0.24**）：

- `pct_b ≤ tol` → `"lower"`（贴下轨**及以下**）
- `|pct_b − 0.5| ≤ tol` → `"mid"` （贴中轨/均线）
- `pct_b ≥ 1 − tol` → `"upper"`（贴上轨**及以上**）
- 其余 → `"none"`（带中间的空档，不构成「贴着」）
- `pct_b` 为 `NaN`（带退化 / 缺帧）→ `"none"`

> **外侧两锚为什么是单边判据**（v2·P1-2）：v1 写的是双边 `|pct_b − 0.0| ≤ tol` / `|pct_b − 1.0| ≤ tol`，
> 于是价格**破轨**（`pct_b < 0` 或 `> 1`）时反而落回 `"none"` → 静默不开仓。而破轨恰恰是趋势加速段，
> 也正是 `allow_edge_entry` 想覆盖的那类位置的极端形态 —— 门禁在最该表态的时候沉默，是空洞不是保守。
> 改单边后语义变成「贴上轨或已经在上轨之外」，与「贴着上下边界」的需求原文一致。
>
> **为什么用 %B 而不是绝对百分比**：「离中轨 0.5% 以内」在 12H 与 1D、在高波动与低波动期完全不是同一量纲。
> %B 天然按带宽归一，跨周期跨标的可比。**为什么上界锁 0.24**：`tol = 0.25` 时三个锚区首尾相接，
> 「贴带」退化为「只要在带内」，门禁形同虚设——这是 `ai-trade-base-strategy-ma-band` 已踩过的坑（其 P1-8）。
> 单边化之后互斥性依然只依赖 `tol < 0.25`：`tol ≤ 0.24` ⇒ `tol < 0.5 − tol` 且 `0.5 + tol < 1 − tol`。

**开仓决策表（`entry_zone_allowed`，本策略的核心）**：

| `bias` | `zone` | 动作 | 理由 |
|---|---|---|---|
| `long` | `lower` | **开多**（需 `allow_edge_entry=True`） | 顺势回踩下轨：风险由带定义，赔率最好 |
| `long` | `mid` | **开多** | 顺势回踩均线：需求原文「贴着中间均线」的多头镜像 |
| `long` | `upper` | **不开**（既不追多，更绝不做空） | 追顶赔率差；做空违背总趋势（I-3a） |
| `short` | `upper` | **开空**（需 `allow_edge_entry=True`） | 顺势反抽上轨 |
| `short` | `mid` | **开空** | **需求样例正解**：下跌势反弹回中轨 → 做空 |
| `short` | `lower` | **不开**（既不追空，更绝不做多） | 杀跌赔率差；做多违背总趋势（I-3a） |
| `long`/`short` | `none` | 不开 | 带中间空档不构成结构位 |
| `flat` | 任意 | 不开 | 无共识不交易 |

> 表中 `zone` 一律由 `zone_of(live_pct_b(c, tb_band_low, tb_band_up), tol)` 得出（v2·P0-1）：
> **带来自最近已收盘的 12H 根，价格是当根基础收盘**。这既让「贴带」有 5m 的分辨率，也保证
> 「哪条带」始终由 12H 结构定义 —— 小周期只决定「什么时候贴上去了」，从不重新定义带本身。

**反向动能门槛**（把「已反弹三根 12H」写成可计算条件）——`bias=short` 时要求：

```
tb_up_run >= counter_run_min          # 连续收高的 12H 根数（默认 2；用户样例的 3 天然满足）
        或
tb_rebound_pct_b >= retrace_pct_b_min # 自近端最低 %B 的反弹幅度（默认 0.20 带宽）
```

`bias=long` 时镜像使用 `tb_down_run` / `tb_pullback_pct_b`。用 **OR** 而非 AND，是因为两者刻画的是同一件事的
两个侧面（**次数**与**幅度**），要求同时满足会把本就稀疏的 12H 级信号再砍掉一半；任一成立即证明「这是一次
回归而不是趋势加速」，正是我们要买/卖的那种位置。

**挤压过滤**：`tb_band_width >= min_band_width`（默认 0.02）。带宽被压扁时 %B 会剧烈跳动，`zone` 判定失去意义。

#### 2.4.1 用户样例逐步走查（必须能在单测里复现）

| 步骤 | 数据 | 判定 |
|---|---|---|
| 1 | 1D `ema_fast<ema_slow`，`slope_atr=-0.4` | `mb_trend_dir = -1` |
| 2 | 12H `ema_fast<ema_slow`，`slope_atr=-0.5` | `tb_trend_dir = -1` |
| 3 | 方向层 | `bias = short`，`conviction = 1.0` |
| 4 | 最近 3 根 12H 连续收高 | `tb_up_run = 3 ≥ counter_run_min(2)` ✅ |
| 5 | 12H 带 `[low=98, mid=104, up=110]`（上一根 12H 收盘定），当根 5m 收盘 `c=103.6` | `live_pct_b = (103.6−98)/(110−98) = 0.467` → `zone = mid`（\|0.467−0.5\|=0.033 ≤ 0.15）✅；12H 收盘口径 `tb_pct_b` 由 0.08 回到 0.47 ⇒ `rebound_pct_b = 0.39 ≥ 0.20` ✅ |
| 6 | 12H 带宽 5% | `≥ min_band_width` ✅ |
| 7 | 15m `cf_trend_dir = -1`（EMA 已回落） | 择时确认通过（`confirm_mode="aligned"`）✅ |
| 8 | 5m 当根 `c<o`、`c<trig_ema`、`c ≤ 前根 low`、`RSI=34 > 20` | 扳机触发 ✅ |
| 9 | 输出 | `Signal(side="short", strength=0.6, reason="12H/1D空势·中轨反抽做空")` |
| 10 | 成交 | **下一根 5m 开盘价**成交（A-9） |

### 2.5 择时层：15m 确认 + 基础周期扳机

**15m 确认**（`confirm_mode`，默认 `"aligned"`）：

- `"aligned"`：要求 `cf_trend_dir == (+1 if bias=="long" else -1)` —— 15m 的 EMA 结构**已经**转向交易方向，
  即「反弹已被打断」才进场，牺牲一点点价格换确定性；
- `"not_opposed"`：只要求 `cf_trend_dir != 反方向` —— 更早进场、更高频，赔率更好但胜率更低。

`cf_trend_dir` 为 `NaN`（15m 帧缺失且基础周期 < 15m）⇒ **不开仓**（I-2）。
若基础周期 ≥ `confirm_bar`（例如直接在 15m/1H 上跑），确认层**退化为基础周期自身**：
`prepare` 的重采样兜底在 `base_seconds >= tf_seconds` 时直接在基础帧上算（等价于 `_trend_structure._regime_degenerate`
的退化分支），语义自洽、无需特判。

**基础周期扳机**（默认基础周期 = 5m，列在 `prepare` 里算）：

| 方向 | 全部满足才触发 |
|---|---|
| `short` | `c < o`（收阴）且 `c < trig_ema`（跌破基础 EMA）且 `c <= l[i-1]`（破前根低点，动能确认）且 `trig_rsi > trigger_rsi_floor`（不在超卖区追空） |
| `long` | `c > o` 且 `c > trig_ema` 且 `c >= h[i-1]` 且 `trig_rsi < trigger_rsi_ceiling` |

`i < 1` 或任一值 `NaN` ⇒ 不触发。

### 2.6 离场层与「做 T」

持仓期间按**固定优先级**逐项检查，命中即返回 `Signal(side="close", reason=...)`（离场 **fail-open**：某项输入
`NaN` 只跳过该项，不阻塞其余检查）：

| 序 | 检查 | 空头条件（多头镜像） | reason 文案 |
|---|---|---|---|
| 1 | **大趋势失效**（I-3b） | `bias != "short"`（含 `flat`、翻多）且 `flip_exit=True` | `大周期趋势失效离场` |
| 2 | **结构止损** | `h >= entry_price + stop_atr_mult × tb_atr` | `结构止损(12H ATR)` |
| 3 | **目标带** | `live_pct_b(c, tb_band_low, tb_band_up) <= target_pct_b(bias, entry_zone)` | `触及目标带离场` |
| 4 | **跟踪止盈** | `_extreme_px is not None` 且 `c >= _extreme_px + trail_atr_mult × cf_atr` | `跟踪止盈(15m ATR)` |
| 5 | 兜底 | broker 级 `stop_loss_pct` / `max_hold_bars`（`risk_params()`，A-11） | `stop_loss` / `max_hold` |

**`_extreme_px`：持仓期间的有利极值，`float | None`，哨兵是 `None` 而不是 `0.0`**（v2·P0-2）：

```
持仓首根：_extreme_px = l（空） / h（多）
其后每根：_extreme_px = min(_extreme_px, l)（空） / max(_extreme_px, h)（多）
空仓时  ：_extreme_px = None
```

> ⚠️ **绝不能把哨兵写成 `0.0`**：空头基准取 `min(l)`，初值 0.0 会使 `min(0.0, l) ≡ 0.0`，于是
> `c >= 0 + trail_atr_mult × cf_atr` 对任何真实价格恒真 —— **每一笔空单都会在开仓后第一根被
> 「跟踪止盈」平掉**。更阴险的是「断言跟踪止盈能触发」这类正向用例会**顺利通过**，缺陷会一路
> 漏到实盘。故 T-12 必须先断言「未达阈值不得触发」（见 §6.1）。
> `None` 时跳过本项检查而非阻塞其余检查 —— 离场 fail-open（I-2）。

> **止损层级必须显著分层**（v2·P1-4）：序 2 的结构止损由策略发 `close` 信号，**次根开盘才成交**（A-9）；
> 序 5 的 broker `stop_loss_pct` 却是**根内触发价**立即成交（`simulator.py:180-192`）。若两者阈值接近，
> broker 兜底会先打掉仓位，序 2 永不生效，成交 `reason` 里也再看不出是哪条结构线被触发。因此
> `stop_loss_pct` 默认放宽到 **0.05**，作为真正的「熔断」而非第二条止损线；调参时须始终保持
> `stop_loss_pct` 明显宽于 `stop_atr_mult × 12H ATR / entry_price` 的典型值（BTC 上约 2~3%）。

**目标带** `target_pct_b(bias, entry_zone)`：

| `bias` | 进场 `zone` | 目标 |
|---|---|---|
| `short` | `upper` | `0.5`（打到中轨即落袋） |
| `short` | `mid` | `target_pct_b_edge`（默认 0.05，打到下轨） |
| `long` | `lower` | `0.5` |
| `long` | `mid` | `1 − target_pct_b_edge`（默认 0.95） |

**这就是「做 T」**：一段 12H 下跌趋势里，价格反复「反抽中轨 → 跌回下轨」，策略就反复「中轨开空 → 下轨平空 →
等下一次反抽」，方向**始终是空**，从不逆势做多。SimBroker 不支持分批加减仓（A-10），所以「做 T」只能是
**完整往返**而非仓位调节——这是平台事实约束，不是设计偷懒。

> 「做 T」能成立的前提是**目标带判定必须有基础周期的分辨率**（v2·P0-1）。若按 v1 用 12H 收盘的
> `tb_pct_b`，「反抽中轨」与「跌回下轨」都只能在 12H 收盘那一刻被观察到，一次完整往返至少跨
> 2 个 12H 收盘（≥24h），而默认 `max_hold_bars=288`（5m × 288 = 24h）会先到期强平 —— 往返永远
> 走不完。改用 `live_pct_b` 后，带仍由 12H 定义、触碰由 5m 观测，往返可在数小时内完成。

### 2.7 频控、仓位与运行态

| 机制 | 参数 | 实现 |
|---|---|---|
| **同一趋势 K 线内开仓上限** | `max_entries_per_trend_bar`（默认 2） | 桶 id = **`tb_bar_ts`**（当前生效的那根真实 12H 根自身的 ts）；其值变化即计数清零（v2·P1-1）。`tb_bar_ts` 为 `NaN` 时不计数也不拦截（缺帧场景本就已被 I-2 挡在开仓之外） |
| **止损后冷却** | `cooldown_minutes`（默认 60） | 因**结构止损/兜底止损**离场时置 `_cooldown_until_ts = ts + 60×60`；目标带/跟踪止盈离场**不**冷却 |
| **仓位** | `strength`（0.6）× `conviction` | 双周期共振 ⇒ `strength`；单周期（partial）⇒ `strength × partial_strength_scale`(0.6) |

**运行态字段（全部只在 `on_candle` 内写，绝不在 `prepare` 内写——`prepare` 在 paper/live 每根都重复调用）**：

| 字段 | 生命周期 | 用途 |
|---|---|---|
| `_extreme_px: float \| None` | 按笔（空仓时置 **`None`**，**不是 `0.0`**——见 §2.6 的 P0-2 说明） | 跟踪止盈基准 |
| `_entry_zone: str` | 按笔（空仓时置 `""`） | 决定目标带 |
| `_entry_bias: str` | 按笔（空仓时置 `""`） | 断言持仓方向与 bias 一致 |
| `_cooldown_until_ts: int` | 跨笔（会话级） | 止损冷却 |
| `_bucket_ts: float \| None` / `_bucket_entries: int` | 跨笔（会话级） | 每趋势 K 线开仓上限（桶身份 = `tb_bar_ts`） |

> ⚠️ **按笔字段与会话级字段必须分开清零**：空仓时清零的只能是前三个；把冷却/桶计数一起清掉，冷却就永远不生效。
> 三个按笔字段的哨兵值也不能混用「零值」惯例：`_extreme_px` 的零值哨兵会直接制造假离场（P0-2）。

### 2.8 一根 K 线的完整调用序（回测）

```
api/strategies.py:100  POST /api/strategies/{id}/backtest
  └─ agent/services.py  run_single_backtest
       ├─ load_range_candles(inst, bar="5m", [start,end])                    # 基础周期
       ├─ load_htf_frames(inst, timeframes=["15m","12H","1D"],
       │                  htf_warmup_bars=strategy.htf_warmup_bars())        # 三个真实高周期帧
       └─ run_in_threadpool(run_backtest, ...)
            ├─ candles_to_df                                                  # RangeIndex 唯一递增
            ├─ strategy.prepare(df, "5m")        → 基础列 + tb_/mb_/cf_ 重采样兜底列（I-1）
            ├─ strategy.attach_timeframes(...)   → 有真实帧则覆盖同名 tb_/mb_/cf_ 列
            └─ for i in range(n):                                        # backtester.py:97-106
                 broker.fill_pending(open_price=row.o, ts=row.ts, bar_index=i)  # 上一根排队的单在本根开盘成交（A-9）
                 broker.apply_risk(i, row, risk)   # 兜底 stop_loss_pct / max_hold_bars（risk 为循环外算好的同一对象，A-11）
                 broker.mark(ts=row.ts, close=row.c)
                 if i >= warmup and i < n-1:
                     signals = strategy.on_candle(df, i, ctx)   # ← 本方案全部逻辑在此
                     broker.submit(signals)                     # 排队，次根开盘成交
```

> 签名以 `backtester.py:99-106` 实测为准（v2·P2-1 更正 v1 的简写伪代码）。paper/live 侧同名四阶段，
> 差别只在 `i = len(self._candles) - 1` 会随窗口填满而冻结（A-12、I-4）。

### 2.9 缺数据与降级语义（必须写进策略 `description`）

| 场景 | 行为 | 用户如何自查 |
|---|---|---|
| 12H 或 1D 帧取不到（离线 / OKX 失败 / 基础周期 ≥ 该周期且窗口不足） | `tb_/mb_` 列 NaN ⇒ `bias=flat` ⇒ **零开仓**（不报错、不假数据） | `run.config.htfBars` 是否包含 `12H`/`1D`；实时模拟看是否长时间零信号 |
| 15m 帧取不到且基础周期 < 15m | `cf_trend_dir` NaN ⇒ **零开仓** | 同上 |
| 持仓中高周期帧断供 | 大趋势失效检查被跳过，**结构止损 / 跟踪止盈 / broker 兜底仍然工作**（I-2 离场 fail-open）。注意目标带检查依赖 `tb_band_*`，断供期间一并跳过 | 成交 `reason` 字段 |
| **部分帧**（如 15m 取到、12H 失败） | paper 侧 `frames` 只装非空窗口（`paper/session.py:307-308`），`attach_timeframes` 会收到不完整字典：**有帧的前缀被覆盖、无帧的前缀保留 `prepare` 的重采样兜底值**；若兜底值也不足则为 NaN ⇒ 开仓 fail-closed | 由 T-16 锁定 |
| 基础周期设成 12H/1D | 三层坍缩为同一周期，`zone`/扳机仍可算，但择时意义削弱 | `description` 已写明推荐 5m/15m |
| **基础周期设成 12H 跑实时模拟 / 实盘** | **不支持**：`12H` 不在 `BAR_TO_WS_CHANNEL`（`okx/types.py:82-90`），WS 无对应频道（v2·P2-4）。回测不受影响 | 启动会话时报错 |
| **paper/live 下 `max_hold_bars`** | **恒不触发**：`i` 冻结使 `bar_index - entry_bar ≡ 0`（`simulator.py:195`，A-12 既有平台缺陷）。实时链路的持仓时长上限实际只有「趋势失效 / 结构止损 / 目标带 / 跟踪止盈 / broker 硬止损」五道（v2·P2-3） | 长期持仓不自动到期即为此因 |

### 2.10 参数全表

**默认参数**（`meta.default_params`，共 **32** 项；其中 **22** 项进面板 `params_schema`）：

| 参数 | 默认 | 面板 | 含义 |
|---|---|---|---|
| `trend_bar` | `"12H"` | ✅ enum `["4H","12H","1D"]` | 交易趋势周期（带/位置/游程都取自它） |
| `macro_bar` | `"1D"` | ✅ enum `["12H","1D"]` | 宏观趋势周期（第二票） |
| `confirm_bar` | `"15m"` | ✅ enum `["5m","15m","30m","1H"]` | 择时确认周期 |
| `trend_ema_fast` | `20` | ✅ int 3~120 | 高周期快 EMA |
| `trend_ema_slow` | `60` | ✅ int 5~240 | 高周期慢 EMA |
| `trend_slope_lookback` | `3` | — | 斜率回看根数 |
| `trend_slope_min` | `0.10` | ✅ number 0~2 step .05 | ATR 归一化斜率阈值（越大越苛刻） |
| `band_period` | `20` | ✅ int 5~120 | 布林均线周期（= 需求的「均值」） |
| `band_std_mult` | `2.0` | ✅ number 1~4 step .1 | 布林带标准差倍数（= 上下边界） |
| `band_touch_tol` | `0.15` | ✅ number 0.02~**0.24** step .01 | 「贴着」的 %B 容差 |
| `atr_period` | `14` | — | 各周期 ATR 周期 |
| `retrace_lookback` | `6` | — | 反弹/回撤幅度的回看根数 |
| `allow_partial_bias` | `True` | ✅ bool | 允许「一涨一震荡」构成偏置（降权） |
| `partial_strength_scale` | `0.6` | — | 上者的仓位折扣 |
| `counter_run_min` | `2` | ✅ int 0~8 | 反向连续根数门槛（用户样例的 3 满足） |
| `retrace_pct_b_min` | `0.20` | ✅ number 0~1 step .05 | 反弹/回撤的 %B 幅度门槛 |
| `allow_edge_entry` | `True` | ✅ bool | 允许在上/下轨顺势进场（关掉则只在中轨附近开仓） |
| `min_band_width` | `0.02` | — | 挤压过滤：相对带宽下限 |
| `max_entries_per_trend_bar` | `2` | ✅ int 1~10 | 同一根 12H 内最多开几次（做 T 节流） |
| `cooldown_minutes` | `60` | ✅ int 0~1440 | 止损后冷却（**分钟，不是根数**，见 I-4） |
| `confirm_mode` | `"aligned"` | ✅ enum `["aligned","not_opposed"]` | 15m 确认严格度 |
| `trigger_ema` | `20` | — | 基础周期扳机 EMA |
| `trigger_rsi_period` | `14` | — | 基础周期 RSI 周期 |
| `trigger_rsi_floor` | `20` | — | 做空时的 RSI 下限（超卖不追空） |
| `trigger_rsi_ceiling` | `80` | — | 做多时的 RSI 上限（超买不追多） |
| `stop_atr_mult` | `1.2` | ✅ number 0.2~5 step .1 | 结构止损 = 开仓价 ± 该倍数 × 12H ATR |
| `trail_atr_mult` | `1.5` | ✅ number 0.2~6 step .1 | 跟踪止盈 = 极值 ± 该倍数 × 15m ATR |
| `target_pct_b_edge` | `0.05` | — | 中轨进场时的目标 %B（对侧轨） |
| `flip_exit` | `True` | ✅ bool | 大趋势翻转即平仓（= 不变式 **I-3b**）。关掉后 I-3b 不再成立，但 **I-3a「开仓方向恒 == bias」仍然成立且无法被关闭**（v2·P1-6） |
| `stop_loss_pct` | **`0.05`** | ✅ number .005~.2 | broker 兜底**熔断**（非第二条止损线）。必须显著宽于结构止损，否则结构止损永不生效（v2·P1-4，见 §2.6） |
| `max_hold_bars` | `288` | ✅ int 1~2000 | 最大持仓根数（5m ⇒ 24 小时） |
| `strength` | `0.6` | ✅ number .1~1 step .05 | 基准仓位比例 |

**仓位换算单点**：`conviction = 1.0`（双周期共振）或 `partial_strength_scale`（单周期偏置）；
最终 `strength_eff = clamp(strength × conviction, 0, 1)`，只在 `Signal(strength=...)` 处使用一次，不散落。
（`Signal.strength` 是 `Field(ge=0.0, le=1.0)`，超界会抛 pydantic `ValidationError` —— clamp 是硬要求
而非防御性代码，`strategies/base.py:21`。）

**非面板派生量（不是参数，但必须在实现中定值）**（v2·P1-3）：

| 量 | 取值 | 为什么必须写死在文档里 |
|---|---|---|
| `warmup()` | `max(trigger_ema, trigger_rsi_period, retrace_lookback) + 5` ≈ **25**，**单位是基础根** | 它决定回测起信号根（`backtester.py:94,102`）与 paper 窗口尺寸 `_window_max`（`paper/session.py:66-74`）。若望文生义按高周期根数填（如把 `trend_ema_slow=60` 当 60 根 12H），会同时撑大实时窗口并推迟信号；填得过小则基础扳机列尚未激活就开始产信号。**必须是 params 的纯函数**——`warmup()` 早于 `prepare()` 调用 |
| `htf_warmup_bars()` | `max(trend_ema_slow, band_period, atr_period, retrace_lookback) + 15` ≈ **75**，**单位是高周期根** | 高周期暖机的唯一来源，与 `warmup()` **不得混算**。它同时放大三个周期的取数 `target` 与左界回溯（§2.2.1），不可为求保险而虚高；也不得低于 `trend_ema_slow`，否则 `tb_/mb_trend_dir` 在区间前段恒 NaN |
| `timeframes()` | `sorted(set) `去重后的 `[confirm_bar, trend_bar, macro_bar]` 中**严格高于基础周期**者（过滤由调用方做，见 A-4） | 允许三者两两相同（如 `trend_bar == macro_bar == "1D"`）；去重后帧数减少，`classify_bias` 的两票会同源 ⇒ 恒共振，属用户自选，不报错 |

> **`trend_bar` 与 `macro_bar` 不强制大小关系**（v2·P2-5）：`trend_bar="1D"` + `macro_bar="12H"` 会让
> 「宏观」周期快于「交易」周期，语义倒置但不崩、也不违反任何不变式。文档在此明示，避免被当成缺陷上报。

> 面板暴露 **22 项**，其余留在 `default_params` 可通过「另存为」编辑 `params_json` 调整——沿用
> `trend_pullback_long` 的「只暴露最常调的核心项进面板」惯例（`trend_pullback_long.py:55`）。

---

## 三、文件 / 模块变更计划

| 文件 | 动作 | 一句话意图 | 预估行数 |
|---|---|---|---|
| `services/quant/app/strategies/_band_structure.py` | **新建** | 纯函数带/趋势特征层：`BandConfig`、`band_features`（含 `bar_ts` 列）、`_run_length`、`live_pct_b`、`attach_resampled_features`（重采样兜底）、`attach_frame_features`（真实帧合并）、`classify_bias`、`zone_of`、`entry_zone_allowed`、`target_pct_b` | ~320 |
| `services/quant/app/strategies/trend_band_swing.py` | **新建** | 策略类 `TrendBandSwing`：`meta`（含 32 参数 + 22 项 schema）、`warmup`、`timeframes`、`htf_warmup_bars`、`prepare`、`attach_timeframes`、`on_candle`、`_entry_signal`、`_exit_signal`、`_reset_trade_state`、频控助手 | ~380 |
| `services/quant/app/strategies/__init__.py` | 修改 | 追加 `from . import trend_band_swing` 与 `BUILTIN_ORDER` 末位新增 `"trend_band_swing"`（**放在 `range_scalping_strategy` 之后、`combination` 之前**，保持「原子策略在前、组合/因子在后」的展示顺序） | +2 |
| `services/quant/tests/test_trend_band_swing.py` | **新建** | 本策略专项单测（§六的 **18** 个用例 + 评审补的 T-19，共 **19**） | ~500 |
| `services/quant/tests/test_strategies.py` | 修改 | `test_registry_has_builtins` 的 key 元组追加 `"trend_band_swing"`（一行）；`_UNCHANGED_KEYS` **不动**（A-18） | +1 |
| `docs/plans/trend-band-swing-strategy/spec.md` | **新建** | 本文档 | — |

**明确不改的东西**（任何改动都属越界，评审须驳回）：

- 前端：零改动。`strategyCatalog.ts` 按 `kind` 分流，`kind="atomic"` 自动进「常规策略」（A-1）；
  参数面板由 `paramsSchema` 数据驱动，`enum/bool/int/number` 四种类型均已支持（`apps/web/lib/types.ts:82-93`）。
- 数据库：零迁移。新 builtin 行由 `_seed_builtin_strategies` 在下次后端启动时自动补（A-3）。
- 引擎：`backtester.py` / `simulator.py` / `mtf.py` / `paper/*` / `live_auto/*` / `ai_auto/*` 一行不改。
- 既有策略文件与 `_trend_structure.py` / `_range_structure.py`：一行不改（**不要**为「复用」去改它们的签名；
  本方案的特征需求与它们只有表面相似，强行合并会把两个特性耦死）。

---

## 四、接口设计

**无新增端点、无签名变更。** 新策略经既有端点自动可见：

| 端点 | 变化 |
|---|---|
| `GET /api/strategies` | 返回数组新增一行（见下方形状） |
| `POST /api/strategies/{id}/backtest` | 直接可用；`run.config.htfBars` 将出现 `["15m","12H","1D"]` |
| `POST /api/strategies/{id}/multi-backtest` `/walk-forward` `/compare` | 直接可用 |
| `POST /api/paper/sessions`（实时模拟）、live-auto 启动、组合成员、AI 信号板 | 直接可用（A-2/A-14） |
| `POST /api/strategies`（另存为） | 直接可用：`templateKey="trend_band_swing"` + 自定义 `params` |

新增行的 JSON 形状（`merge_strategy` 输出，camelCase）：

```jsonc
{
  "id": 11,
  "templateKey": "trend_band_swing",
  "name": "大周期趋势带波段",
  "description": "12H+1D 双周期定势，只在贴近布林中轨/上下轨且已出现反向回归时顺势开仓；15m 结构确认 + 5m 扳机择时…",
  "kind": "atomic",
  "params": { "trend_bar": "12H", "macro_bar": "1D", "confirm_bar": "15m", "...": "..." },
  "paramsSchema": {
    "trend_bar": { "type": "enum", "default": "12H", "options": ["4H","12H","1D"], "label": "交易趋势周期" },
    "band_touch_tol": { "type": "number", "default": 0.15, "min": 0.02, "max": 0.24, "step": 0.01, "label": "贴带容差(%B)" }
  },
  "enabled": true,
  "builtin": true
}
```

**回测请求示例**（推荐用法）：

```jsonc
POST /api/strategies/11/backtest
{ "instId": "BTC-USDT-SWAP", "bar": "5m",
  "startTs": 1750000000, "endTs": 1752592000,
  "initialCapital": 10000, "fees": 5, "slippage": 2, "leverage": 1 }
```

---

## 五、数据模型

**数据库：零迁移、零新表、零新列。** 唯一持久化影响是 `strategies` 表在下次后端启动时多出一行
`template_key='trend_band_swing', builtin=1, params_json=<default_params>`（A-3）。老库（含
`%APPDATA%/aragon-desktop/aragon.db`，A-19）同样只是补行，既有行不动。

**内存形状**：

```python
@dataclass(frozen=True)
class BandConfig:
    ema_fast: int
    ema_slow: int
    slope_lookback: int
    slope_min: float
    band_period: int
    band_std: float
    atr_period: int
    retrace_lookback: int
```

**DataFrame 列契约**（`prepare` 之后必然存在，值可为 NaN —— I-1）：

| 组 | 列 |
|---|---|
| 基础（原始） | `ts, o, h, l, c, vol` |
| 基础（新增） | `trig_ema, trig_rsi, base_atr` |
| 12H | `tb_bar_ts, tb_ema_fast, tb_ema_slow, tb_atr, tb_slope_atr, tb_band_up, tb_band_mid, tb_band_low, tb_band_width, tb_pct_b, tb_trend_dir, tb_up_run, tb_down_run, tb_rebound_pct_b, tb_pullback_pct_b` |
| 1D | 同上，前缀 `mb_` |
| 15m | 同上，前缀 `cf_` |

（共 3×15 + 3 = **48** 个派生列；均为 float，无布尔列 —— A-6。`*_bar_ts` 虽是时间戳但同样以 float 存放，
缺帧处为 NaN，判定前须 `pd.isna` 守卫 —— v2·P1-1。）

`live_pct_b` **不是列**：它由 `on_candle` 在决策时用当根 `c` 与 `tb_band_low/up` 现算（§2.3），
这样重采样兜底与真实帧两条路径自动同源，也不必承担 I-1 的列契约义务。

**信号形状**：沿用既有 `Signal(side, strength, reason)`，`explain` 保持 `None`（与全部 atomic 策略一致）；
可读性依靠中文 `reason`，它会流向图表 marker、`trades.reason`，并被 `build_atomic_explain` 用作 headline
（`live_auto/explain.py:64-77`）。**本期不新增 explain 富文本**（见 §八 非目标）。

---

## 六、测试与验收标准

### 6.1 单元测试清单（`services/quant/tests/test_trend_band_swing.py`）

测试用**构造帧**而非合成随机数据，直接喂 `attach_timeframes` 的 `frames` 字典（`{"12H": df, "1D": df, "15m": df}`），
这样每个用例都能精确控制 `trend_dir` / `pct_b` / 游程。

| # | 用例名 | 断言 |
|---|---|---|
| T-1 | `test_registered_and_atomic` | key 在 `STRATEGY_REGISTRY` 与 `BUILTIN_ORDER`；`meta.kind == "atomic"` |
| T-2 | `test_timeframes_declares_three_bars` | `timeframes() == ["15m","12H","1D"]`（去重、顺序稳定）；改 `macro_bar="12H"` 后去重为 2 项 |
| T-3 | `test_bias_requires_dual_timeframe_consensus` | `classify_bias` 全表逐行：共振 / 单边 / 冲突 / NaN → 4 类结果 |
| T-4 | `test_zone_of_anchors_are_disjoint_and_cover_out_of_band` | `tol=0.15` 下三锚区互不重叠（`tol=0.24` 边界同样互斥）；**且 `pct_b=-0.2 → "lower"`、`pct_b=1.3 → "upper"`**（v2·P1-2 的单边锚，破轨不得落回 `none`）；`pct_b=NaN → "none"` |
| T-5 | **`test_user_scenario_downtrend_rebound_to_mid_opens_short`** | §2.4.1 十步走查数据 → `on_candle` 产出 `side=="short"` |
| T-6 | `test_never_enters_against_macro_trend` | 上升趋势 + 中轨 + **看跌的 5m 扳机** → **无任何 short 信号**（硬不变式 I-3a） |
| T-7 | `test_no_chase_at_extreme_band` | 下跌趋势 + `pct_b≈0.02`（贴下轨）→ 不开空也不开多 |
| T-8 | `test_conflicting_timeframes_stay_flat` | 12H 多 / 1D 空 → 恒空仓 |
| T-9 | `test_missing_htf_frames_is_fail_closed_not_crash` | `attach_timeframes(df, "5m", {})` 后 `on_candle` 不抛异常且零开仓；`prepare` 后所有契约列存在（I-1） |
| T-10 | `test_regime_flip_forces_exit` | 持空仓中 `tb/mb trend_dir` 转 +1 → 立刻 `close`，reason 含「趋势失效」 |
| T-11 | `test_exit_priority_stop_before_target` | 同根同时满足结构止损与目标带 → 命中**止损**（保守优先） |
| T-12 | `test_trailing_exit_does_not_fire_before_threshold` | **两段断言，负向在前**（v2·P0-2）：① 空头开仓后第一根、价格仅小幅回抽（远未达 `trail_atr_mult × cf_atr`）⇒ **不得**产生任何 `close`；② 价格自极值反向走足阈值 ⇒ 触发 `跟踪止盈`。若 `_extreme_px` 被写成 `0.0` 哨兵，①**必然失败** —— 这条用例的全部价值在于 ① |
| T-13 | `test_exit_works_when_htf_missing` | 持仓中把 `tb_*` 置 NaN → 结构止损/跟踪止盈仍能触发、目标带检查被跳过而非抛错（离场 fail-open，I-2） |
| T-14 | `test_cooldown_and_bucket_cap_use_timestamps` | 止损后 `cooldown_minutes` 内不再开仓；**同一 `tb_bar_ts` 内**开仓次数不超 `max_entries_per_trend_bar`，`tb_bar_ts` 变化即放行（v2·P1-1，用**非 UTC 整点对齐**的 ts 构造，如 `ts % 86400 == 57600`，锁死「不得按 `ts // 43200` 分桶」）；**把 `i` 固定为常数**（模拟 paper 的 deque 冻结，A-12）仍正确 |
| T-15 | `test_causal_prefix_invariance` | 对同一数据，用 `df[:k]` 与完整 `df` 分别跑到第 `k-1` 根，信号**逐字段一致**（无未来函数）。**协议**：两侧各用**全新策略实例从第 `warmup` 根重放到 `k-1`**（策略含会话级运行态 `_cooldown_until_ts` / `_bucket_*`，直接跳到 `k-1` 单点调用两侧状态不可比，断言无意义 —— v2·P2-7） |
| T-16 | `test_partial_frames_only_overrides_present_prefixes` | `attach_timeframes(df, "5m", {"15m": frame})`（12H/1D 缺失，模拟 `paper/session.py:307-308` 的非空窗口过滤）⇒ 不抛异常；`cf_*` 被真实帧覆盖、`tb_/mb_*` 保留 `prepare` 兜底值；`tb_trend_dir` 若为 NaN 则零开仓（v2·P2-6） |
| T-17 | `test_target_band_exit_uses_live_price_not_stale_htf_pct_b` | 持空仓，**冻结 12H 帧不动**（`tb_band_*` 与 `tb_pct_b` 全程常量，模拟 12 小时内无新 12H 收盘），只让基础 `c` 从中轨跌到下轨 ⇒ **必须**在基础周期这一根产出 `触及目标带离场`（v2·P0-1；若仍用 `tb_pct_b` 判定，本用例必然失败，做 T 也不成立） |
| T-18 | `test_backtest_smoke_without_frames` | `run_backtest(strategy, make_synthetic_candles(300), "1H", htf_frames=None)` 正常返回，`equity_points` 数 == K 线数（对齐 A-17 的全局用例；该全局用例只跳过 `kind=="combo"`，本策略为 atomic **必然**被它喂进无帧回测） |

### 6.2 回归护栏（必须同时绿）

```bash
cd services/quant
python -u -m pytest tests/test_trend_band_swing.py -q
python -u -m pytest tests/test_strategies.py tests/test_strategies_registry.py tests/test_mtf.py tests/test_trend_pullback.py -q
python -u -m pytest tests/test_api.py tests/test_strategies_api.py -q
```

**跑测须知（血泪教训，务必遵守）**：

1. **全量 `pytest` 不能并发**：所有用例共用同一个临时 SQLite（`tests/conftest.py:11-13`），并发会在中途挂死；
2. `pytest -q` 在 Windows 下块缓冲，长跑请用 `python -u -m pytest ... > log 2>&1` 再读文件；
3. **既有红灯基线**（**不是**本次回归）：`test_factor_library_expansion` 的 7 个孤儿用例 + `test_agent_*`
   的计时 flake（单独跑必绿）。下游节点报告结果时必须把这两类与新增失败区分开。

### 6.3 手工验收（放行条件，逐条打勾才算完成）

| # | 步骤 | 期望 |
|---|---|---|
| G-1 | 重启后端，`GET /api/strategies` | 出现 `templateKey="trend_band_swing"`、`builtin=true`、`kind="atomic"` 的行 |
| G-2 | 前端右侧「交易策略」→「**常规策略**」页签 | 出现「大周期趋势带波段」卡片；点开参数面板 22 项参数可编辑，其中 4 个 `enum` 渲染为下拉、2 个 `bool` 渲染为勾选框（`StrategyDetail.tsx:187-223`，零前端改动验证）。**不期望出现「参考周期」徽标** —— 它只认 `params.htf_bar` + `htf_trend_filter`（`StrategyDetail.tsx:150`），本策略无此参数名（v2·P2-9） |
| G-3 | 选 `BTC-USDT-SWAP` / `5m` / 近 30 天跑一次回测 | 成功返回；`run.config.htfBars == ["15m","12H","1D"]`；成交 `reason` 为中文且能看出「哪条带 + 什么方向」；**成交 reason 中应能看到「结构止损(12H ATR)」而不是清一色 `stop_loss`**（v2·P1-4 的层级验证）；**记录本次回测的墙钟时长**（v2·P1-5 的取数成本基线，供后续对比） |
| G-4 | 检查该次回测的全部成交 | **同一段大趋势内的开仓方向唯一**；不存在「12H 空势里的做多」 |
| G-5 | 「实时模拟」启动该策略（5m） | 会话正常运行，无异常日志；12H/1D 窗口在启动时预填一次（限频 A-13） |
| G-6 | 把 `flip_exit` 关掉再跑一次回测 | 能跑通且**仍然不存在逆 `bias` 开仓**（I-3a 不受该参数影响，v2·P1-6）；关掉后仅「趋势失效离场」这一条离场消失。默认值必须是 `True` |
| G-7 | `ruff check services/quant/app/strategies` | 无新增告警（line-length 110，A-20） |
| G-8 | 抽一笔空头成交，核对其**开仓后第一根**的行为（v2·P0-2 专项） | **不得**出现开仓次根即「跟踪止盈」平仓；若出现，说明 `_extreme_px` 用了 `0.0` 哨兵而非 `None` |

---

## 七、风险与缓解

| # | 风险 | 影响 | 缓解 |
|---|---|---|---|
| R-1 | **信号稀疏**：12H/1D 定势 + 贴带 + 回归门槛三重过滤后，单标的单月可能只有个位数交易 | 回测样本少、指标不稳 | 面板暴露 `band_touch_tol` / `counter_run_min` / `confirm_mode` 三个「放宽阀门」；文档建议用**多样本回测**（`/multi-backtest`）跨标的评估；`max_entries_per_trend_bar` 默认 2 允许一根 12H 内做两次 T |
| R-2 | **零交易被误判为 bug**：缺 HTF 帧时策略静默不开仓 | 用户以为坏了 | `description` 明写；`run.config.htfBars` 可自查；T-9/T-16/T-18 锁定该行为；§2.9 表格进文档 |
| R-3 | **paper/live 下 `i` 冻结**（A-12） | 按 index 的冷却/计数在实盘失效 | 铁律 I-4：一切计时按 `ts`；`cooldown_minutes` 直接用分钟；T-14 用「`i` 恒定」的构造数据锁死。**注**：该冻结也使 `max_hold_bars` 在实时链路恒不触发（v2·P2-3，§2.9） |
| R-4 | **`bool(NaN) is True`** 静默反转门控（A-6） | 无数据被当成趋势成立，最危险的时候恰好失效 | 只产数值列；`on_candle` 内 `pd.isna` 显式守卫；T-9 覆盖 |
| R-5 | **`band_touch_tol ≥ 0.25` 使门禁空转** | 「贴带」退化成「在带内」 | schema `max=0.24`；T-4 断言三锚区不相接 |
| R-6 | **手续费侵蚀**：做 T 频率高，5m 上往返成本约 2×(5bps+2bps) | 高频小赢被吃掉 | 目标带最短也是半个带宽（≈2σ 的一半）；`max_entries_per_trend_bar` + `cooldown_minutes` 双节流；回测默认 `fees=5, slippage=2` 已含成本（`config.py:354-356`） |
| R-7 | **12H/1D 桶对齐**（v2·P1-1 重写）：OKX 的 `12H` / `1D` 是**港时（UTC+8）开盘价 K 线**，UTC 对齐要用 `12Hutc` / `1Dutc` 后缀；本仓 `BAR_SECONDS` / `BAR_TO_WS_CHANNEL` 用的都是无后缀形式（`okx/types.py:82-102`），故真实 1D 根满足 `ts % 86400 == 57600` | ① 重采样兜底路径（`ts // htf_sec` 分桶）与真实帧**相位差 4 小时**；② 任何按 `ts // BAR_SECONDS[bar]` 判「同一根高周期」的逻辑都会横跨两根真实根 | ① **真实帧路径不受影响**：`causal_htf_merge` 用帧自带的 `ts + htf_seconds` 做收盘对齐（`engine/mtf.py:78`），与交易所选哪个时区无关，因果性精确；② 频控桶身份改用 `tb_bar_ts`（真实根自身 ts），彻底与对齐方式解耦（§2.7，T-14 用非 UTC 整点 ts 锁死）；③ 重采样兜底的相位差**接受为已知降级**——它只在取不到真实帧时生效，而那时策略本就靠 I-2 fail-closed 少开仓，不构成风险敞口 |
| R-8 | **全局用例把新策略喂进无帧回测**（A-17） | `KeyError` 崩测 | I-1：`prepare` 无条件建全列；T-18 提前复现该场景 |
| R-9 | **参数过多**（32 项）导致面板膨胀 | 用户被淹没 | 只暴露 22 项，其余留 `default_params`；面板顺序按「趋势 → 位置 → 择时 → 风控」分组书写 |
| R-10 | **`risk_params()` 回测期只读一次**（A-11） | 想按笔改止损会静默失效 | 结构止损/跟踪止盈一律由策略发 `close` 信号实现；`stop_loss_pct` 只作静态兜底 —— 已写进 §2.6 |
| R-11 | **组合策略把它当成员**时 `htf_warmup_bars` 取成员最大值（`paper/live_combo.py:49-50`） | 组合窗口被本策略抬高 | `htf_warmup_bars()` 保持在 **75** 左右（`trend_ema_slow(60) + 15`），不超过 `PAPER_WINDOW_MAX=1200` 的量级，组合仍可正常启动 |
| R-12 | 「顶级策略」的预期落差 | 用户期待稳赚 | `description` 与文档只陈述机制，不承诺收益；所有增强项（共识、归一化斜率、挤压过滤、赔率导向的目标带、节流、冷却）都是**风险控制**而非收益承诺 |
| R-13 | **三个高周期帧的取数放大**（v2·P1-5）：本策略是仓内第一个一次声明 3 个高周期的策略，30 天 / 5m 区间下额外约 60 次 REST 请求 | 回测墙钟变长；密集触发时可能撞上 OKX `history-candles` 限频（约 20 次/2s） | §2.2.1 给出量级与三条约束（`confirm_bar` 不下调到 5m、`htf_warmup_bars()` 不虚高、长区间走 `/multi-backtest` 串行）；G-3 记录墙钟基线 |
| R-14 | **高周期值在两次收盘之间是常量**（v2·P0-1 的残余面）：`trend_dir` / 游程 / 回撤幅度仍只在 12H、1D 收盘时跳变 | 方向层的翻转最慢有 12 小时延迟，「趋势失效离场」不是即时的 | 这是**有意保留**的：方向层的价值恰在于慢。即时性由结构止损（每根基础 K 线检查）、目标带（`live_pct_b`，每根检查）、跟踪止盈与 broker 熔断四道共同承担；`description` 须写明「方向判定按 12H/1D 收盘更新」 |

---

## 八、非目标（Non-goals）

1. **不做分批建仓 / 加减仓 / 金字塔**：SimBroker 单向单仓（A-10），要支持得改引擎，越界。
2. **不改前端**：包括不加策略专属卡片、不加图表带线绘制。
3. **不加 DB 迁移 / 新表 / 新端点**。
4. **不接 `Signal.explain` 富文本**：需要在 `live_auto/explain.py` 增构造器 + 前端渲染分支，属独立增量。
5. **不做参数自动寻优 / 因子化**：因子实验室与权重训练是另一条链路。
6. **不动 AI 自动交易的铁律层**（`ai_auto/base_rules.py`）：那是决策层门禁，与本策略无耦合。
7. **不实现「12H 与 1D 权重可调的连续评分」**：本期用离散决策表（可读、可测、可审计）；连续评分留待后续迭代。

---

## 九、给下游实施节点的须知

1. **文件顺序**：先写 `_band_structure.py`（纯函数、可独立单测），再写策略类。策略类里**不许**出现 pandas
   指标计算之外的裸循环。
2. **函数尺寸**：`on_candle` 保持 ≤ 20 行（只做分派），实际逻辑落在 `_entry_signal` / `_exit_signal`；
   每个私有方法 ≤ 50 行（CLAUDE.md 阈值）。`_entry_signal` 若超长，按「方向层 / 位置层 / 择时层」拆三个助手。
3. **行宽**：逻辑行按 CLAUDE.md 的 80 列写；`params_schema` 的单行字典沿用既有策略风格（可到 ~110，ruff 门禁
   A-20）—— 这是**有意偏离**，请在实现时保持与 `trend_pullback_long.py:56-68` 一致的观感。
4. **docstring**：模块级 docstring 必须写清「生命周期陷阱」（`prepare` 在 paper/live 逐根重复调用，运行态只在
   `on_candle` 写）与「缺帧降级语义」，参照 `trend_pullback_long.py:1-10` 的写法。
5. **注释只解释「为什么」**：例如「`tol` 上界锁 0.24 是因为 0.25 会让三锚区首尾相接」这类判断必须留注释，
   而「计算布林带」这种复述不要写。
6. **测试先行**：T-5（用户样例）与 T-6（绝不逆势）建议先写、先红，再写实现。**T-12 的负向断言①**
   （空头开仓次根不得被跟踪止盈平掉）与 **T-17**（冻结 12H 帧、只动基础价，必须能触发目标带离场）
   是 v2 两个 P0 的守门用例，**必须先写、先确认会红**——它们各自对应一个「实现写错时正向断言仍会通过」
   的陷阱（见 §2.6、§2.3），先绿再改会失去全部意义。
7. **两处最容易写回 v1 错误的地方**：① 图省事直接读 `row["tb_pct_b"]` 判 `zone`（应走
   `live_pct_b(c, tb_band_low, tb_band_up)`）；② 按本仓常见的「空仓清零」惯例把 `_extreme_px` 置 `0.0`
   （应置 `None`）。两者都不会报错，只会静默改变策略行为。
8. **提交范围**：本仓工作树同时存在多个在制特性（`.env.example` / `desktop/src/lib/config.js` /
   `services/quant/app/config.py` 等已有他人改动）。Subtask #3 提交时必须 `git add <具体路径>`，
   **只暂存本特性的 5 个文件 + 本 spec**，严禁 `git add -A`。

---

## 评审结论（Review Verdict）

**结论：有条件通过（Approved with Conditions）。**

方案的骨架是对的，而且对本仓平台事实的掌握程度高于同类设计文档的平均水准：§〇 的 20 条 As-built 断言
经逐条回读源码复核**全部属实**（仅 §2.8 的调用序伪代码是简写，已更正）；把「不能违背总趋势」落成硬不变式
而不是权重项、把「合并前只产数值列」当成硬规则、把开仓 fail-closed 与离场 fail-open 的极性显式分开——
这三处判断都踩在了本仓过去真实踩过的坑上。工程规模也克制得当：零端点、零迁移、零前端，两个新文件加两行
注册，`kind="atomic"` 自动进「常规策略」页签的链路（`merge_strategy` → `splitStrategyCatalog`）已复核成立。

但 v1 存在**两个会让核心卖点失效的阻断级缺陷**（`zone`/目标带用 12 小时才跳变一次的高周期收盘 %B，
使「5m/15m 做 T」在默认参数下无法完成一次往返；`_extreme_px` 用 `0.0` 哨兵会让**每一笔空单**在开仓次根
被假「跟踪止盈」平掉，且原测试用例会为其背书），以及六个 P1（OKX 12H/1D 实为港时对齐的事实错误、
`zone_of` 在破轨时的语义空洞、`warmup()` 全文未定值、止损层级被默认值倒置、三帧取数成本零评估、
`flip_exit` 与铁律 I-3 自相矛盾）。**这些均已在本文档正文就地修复**，本文档现为 **v2**。

### 放行条件（下游实施节点必须逐条落实，缺一不可）

| # | 条件 | 验收锚点 |
|---|---|---|
| C-1 | `zone` 与「目标带」离场**一律用 `live_pct_b`**（带取最近已收盘 12H，价格取当根基础收盘）；`tb_pct_b` 只服务 `rebound_pct_b` / `pullback_pct_b` | T-17 必须先红后绿；§2.3 / §2.4 / §2.6 |
| C-2 | `_extreme_px` 哨兵为 `None`，持仓首根用当根 `l`/`h` 初始化；`None` 时跳过跟踪止盈检查 | T-12 的**负向断言①**必须先红；G-8 |
| C-3 | 频控桶身份取 `tb_bar_ts`，**禁止** `ts // BAR_SECONDS[trend_bar]` | T-14 用 `ts % 86400 == 57600` 的非 UTC 对齐 ts 构造 |
| C-4 | `zone_of` 外侧两锚用单边判据，破轨（%B ∉ [0,1]）归入最近的 `lower`/`upper` | T-4 |
| C-5 | `warmup()` / `htf_warmup_bars()` 按 §2.10「非面板派生量」表定值，两者**不得混算**，单位分别是基础根与高周期根 | 代码评审 + G-5（实时窗口尺寸正常） |
| C-6 | `stop_loss_pct` 默认 `0.05`，与结构止损保持显著分层 | G-3 能看到「结构止损(12H ATR)」reason |
| C-7 | I-3a（开仓方向恒 == bias）**不得**受任何参数影响；`flip_exit` 只控制 I-3b | T-6 + G-6 |
| C-8 | §2.2.1 的三条取数约束写进实现（尤其 `confirm_bar` 默认保持 15m、`htf_warmup_bars()` 不虚高） | G-3 记录墙钟基线 |
| C-9 | 18 个专项用例全绿；§6.2 的三组回归命令全绿，且报告须把 `test_factor_library_expansion` 的 7 个孤儿用例与 `test_agent_*` 计时 flake 这两类**既有红灯基线**与新增失败明确区分 | §6.2 / §6.3 |
| C-10 | 提交只暂存本特性的 5 个文件 + 本 spec，严禁 `git add -A`（工作树有多个在制特性） | §九-7 |

### 明确不作为放行条件（记录在案的 P2，实施节点自行权衡）

P2-1（伪代码签名，已更正）、P2-2（`_run_length` dtype 加固，已给稳妥写法）、P2-3（paper/live 下
`max_hold_bars` 恒不触发，已补进降级表）、P2-4（12H 不可作实时基础周期）、P2-5（`trend_bar`/`macro_bar`
不强制大小关系）、P2-6（部分帧用例 T-16，建议但不阻断）、P2-7（T-15 重放协议）、P2-8（`slope_min`
偏松，建议实施后用真实回测标定 `trend_dir` 三值分布）、P2-9（G-2 不应期待「参考周期」徽标）、
P2-10（用例计数已统一为 18）。

**无 P0 / P1 遗留。**

---

## 实施过程发现的方案缺陷

> 本节由「代码开发」节点（Subtask #2）填写：实现过程中发现的与本方案不符的事实、被迫的偏离及其理由。
> 所有偏离都在代码里留了指回本节的注释。C-1 ~ C-10 十条放行条件**全部落实**，18 个专项用例全绿。

### I-1（行为级，已就地修正默认值）：跟踪止盈线压制其余三道离场，`trail_atr_mult` 默认 1.5 → **5.0**

**事实**：§2.6 的四道离场里，序 4 的跟踪止盈用 **15m ATR**，序 2 的结构止损用 **12H ATR**，两者量纲
差约 `√(43200/900) ≈ 6.9` 倍。于是默认参数下：

```
跟踪止盈距离 = 1.5 × ATR(15m) ≈ 0.22 × ATR(12H)
结构止损距离 = 1.2 × ATR(12H)
```

跟踪线比结构止损**紧约 5.5 倍**，而它又从持仓首根就以当根 `l`/`h` 为基准武装（P0-2 修好的正是这一点），
结果是：任何足以打到结构止损的逆向行程，必定先穿过跟踪线。**序 2、序 3 实际上永不生效**。

**实测**（离线构造「下跌 + 每轮 3 根 12H 反弹回中轨」的 30 天 5m 路径，重采样出真实 12H/1D/15m 帧后
跑 `run_backtest`；脚本见下方「验证方式」）：

| `trail_atr_mult` | 开仓数 | 离场构成 | 总收益 |
|---|---|---|---|
| **1.5（v2 设计值）** | 10 | **跟踪止盈 ×10（100%）** | +1.7% |
| 3.0 / 5.0 / 8.0 | 5 | 趋势失效 ×2、目标带 ×1、max_hold ×2 | +11.7% |

该场景实测 `median ATR(12H)=4.75`、`median ATR(15m)=0.27`（比值 17.6×，比真实市场的 √48 更极端，
因为构造根的影线很窄）；即便按真实市场的 6.9× 计算，结论不变。

**这与 P1-4 是同一类缺陷、只差一层**：评审已发现「broker 兜底止损 vs 结构止损」的层级被默认值倒置并
把 `stop_loss_pct` 放宽到 0.05，但**没有回头检查四道策略级离场彼此之间的层级**。G-3 要求「成交 reason
中应能看到『结构止损(12H ATR)』」，在 v2 的默认参数下**不可能满足**。

**修正**（沿用 P1-4 的同款疗法——只动默认值、不动机制）：`trail_atr_mult` 默认 `1.5 → 5.0`。
`5.0 × ATR(15m) ≈ 0.72 × ATR(12H)`，**仍严格紧于**结构止损（跟踪线本就该更紧），但不再是「入场次根
即被噪音打掉」的线。schema 区间 `0.2~6.0` 不变（新默认值仍在区间内、两侧都留有调节余量）。机制、
优先级、reason 文案、T-12 的两段断言**全部零改动**——测试侧改为在 `TEST_PARAMS` 里显式钉住
`stop_atr_mult=1.2 / trail_atr_mult=1.5`，使离场用例的阈值不随默认值漂移。

**若不认可此修正**：把 `trend_band_swing.py` 的 `"trail_atr_mult": 5.0` 改回 `1.5` 即可完全回退，
无其他耦合。

### I-2（文档自相矛盾，按 I-2 铁律的字面语义实现）：高周期断供时结构止损**无法**工作

§2.9 第 3 行写「持仓中高周期帧断供 ⇒ 结构止损 / 跟踪止盈 / broker 兜底仍然工作」，但 §2.6 序 2 把结构
止损定义为 `entry_price ± stop_atr_mult × tb_atr`——`tb_atr` 正是断供时缺失的那个量。两句话不可能同时
成立。实现按铁律 I-2 的字面语义「缺数据只跳过**该项**检查」：`tb_atr` 为 NaN ⇒ 跳过结构止损，跟踪止盈
（依赖 `cf_atr`）与 broker 兜底仍然工作。T-13 相应断言「跟踪止盈仍能触发、目标带与结构止损被跳过而非
抛错」。**注**：实时链路里这一格其实很少命中——`prepare()` 每根都会无条件重算重采样兜底列，只有兜底
也不足时 `tb_*` 才真是 NaN。

### I-3（能力边界）：止损冷却只能覆盖策略自身发出的结构止损

§2.7 写「因**结构止损/兜底止损**离场时置冷却」。但 broker 的 `stop_loss_pct` 是在 `SimBroker.apply_risk`
内部按触发价成交的，**不经过 `on_candle`**，策略侧无从观测（要推断就得新增第 6 个按笔运行态字段去区分
「我发的 close」与「broker 平的仓」，而 §2.7 的运行态表恰好只列了 5 个）。故实现只覆盖前半：策略自己
发出 `结构止损(12H ATR)` 时置 `_cooldown_until_ts = ts + cooldown_minutes × 60`。考虑到 P1-4 已把
`stop_loss_pct` 放宽成熔断（应显著晚于结构止损触发），这一格覆盖了绝大多数止损场景。

### I-4（与 T-2 冲突，以 T-2 为准）：`timeframes()` 用**插入顺序**去重而非 `sorted`

§2.10 的派生量表写 `sorted(set)`，但 T-2 断言 `timeframes() == ["15m","12H","1D"]`——`sorted` 出来的是
字典序 `["12H","15m","1D"]`，两者不可能同时满足。实现取 `dict.fromkeys([confirm, trend, macro])`
（插入顺序去重，语义就是「确认 → 趋势 → 宏观」），T-2 通过。取数与合并均不依赖该顺序。

### I-5（文档计数错误）：G-2 的「2 个 bool」应为 **3 个**

§2.10 参数表里进面板的 bool 共三项：`allow_partial_bias` / `allow_edge_entry` / `flip_exit`；enum 四项
（`trend_bar` / `macro_bar` / `confirm_bar` / `confirm_mode`）属实。实测 seed 后 `GET /api/strategies`
返回 `paramsSchema` 22 项 = 4 enum + 3 bool + **7 int + 8 number**（int：两个高周期 EMA、`band_period`、
`counter_run_min`、`max_entries_per_trend_bar`、`cooldown_minutes`、`max_hold_bars`）。G-2 验收时按
**4 enum / 3 bool** 核对。

### I-6（列契约有、正文无消费者）：`base_atr` 目前是纯契约列

§五 的列契约把 `base_atr` 列进「基础（新增）」组，但 §2.5 / §2.6 的任何判定都没有读它（结构止损读
`tb_atr`、跟踪止盈读 `cf_atr`）。已按契约无条件建列（T-9 断言其存在），当前**无读者**。刻意不擅自给它
安排用途：把它当结构止损的断供兜底（`1.2 × 5m ATR`）会得到一条比噪音还窄的止损线，比跳过更危险
（见 I-2）。

---

## 终审就地修复（Subtask #3 · 代码评审）

> 评审方法：按 §三 的变更计划与 C-1 ~ C-10 逐条回读实现，再独立复跑测试与 ruff。
> **C-1 ~ C-10 复核结论：十条全部属实**（`zone`/目标带走 `live_pct_b`、`_extreme_px` 哨兵为 `None`、
> 桶身份取 `tb_bar_ts`、`zone_of` 单边锚、两个 warmup 分算且单位正确、`stop_loss_pct=0.05`、
> I-3a 不受任何参数影响、`confirm_bar` 默认 15m 且 `htf_warmup_bars()=75`、用例与回归全绿、
> 提交只暂存本特性文件）。32 项 `default_params` 全部有消费者，22 项 schema 的 key / 默认值 /
> 区间 / enum 选项与 `default_params` 逐项一致。下列三处是评审发现并**已就地修好**的问题。

### RV-1（用户可见的错误归因）：成交 `reason` 写死 `12H/1D` / `15m`，而三个周期都是面板可改的枚举

`_entry_reason` 硬编码 `f"12H/1D{trend}·…"`，`_REASON_STOP` / `_REASON_TRAIL` 硬编码
`结构止损(12H ATR)` / `跟踪止盈(15m ATR)`。但 `trend_bar` / `macro_bar` / `confirm_bar` 三项都在
`params_schema` 里以 enum 暴露（§2.10），用户把交易趋势周期改成 `4H` 后，`trades.reason` 与图表
marker 仍会声称「12H/1D 空势」「结构止损(12H ATR)」——**归因说谎**，而 G-3/G-4 恰恰要靠 reason
核对「哪条带 + 什么方向」。

**修复**：三处文案改由 params 派生（`_entry_reason(bias, zone, trend_bar, macro_bar)`、
`_stop_reason(trend_bar)`、`_trail_reason(confirm_bar)`）。默认配置下输出与写死时**逐字相同**，
故 T-5 / T-11 / T-12 等既有断言零改动。顺带把 `_exit_signal` 的「先拼字符串再比字符串定冷却」
改成四条显式优先级分支 + 四个 `_is_*` 谓词（`_is_trend_invalid` / `_is_structural_stop` /
`_is_target_reached` / `_is_trailing_stop`）：文案一旦可变，用「`reason == _REASON_STOP`」判断
是否置冷却就是脆的，谓词分支既消除该耦合，也让 CLAUDE.md 的「布尔函数以 is 开头」成立。
新增 **T-19** `test_reasons_name_the_configured_bars_not_the_defaults`（`4H`/`12H`/`30m` 配置下
断言开仓、结构止损、跟踪止盈三条文案都落到实际周期）守门。策略 `description` 里的离场优先级
同步改成「结构止损(趋势周期ATR) → 目标带 → 跟踪止盈(确认周期ATR)」。

### RV-2（僵尸运行态）：`_entry_bias` 写而不读

§2.7 的运行态表给 `_entry_bias` 的用途是「断言持仓方向与 bias 一致」，但实现里它只在
`_reset_trade_state` 与 `_entry_signal` 被**写**两次，四道离场检查全部用 `ctx.position_side`
（broker 侧的权威值），没有任何读者。保留一个永不被读的按笔字段，只会让后来者以为方向状态
有两个真源。**修复**：删除该字段（按笔运行态从 3 个减为 2 个：`_extreme_px` / `_entry_zone`），
模块 docstring 同步改写。方向的唯一真源恒为 `ctx.position_side`。

> 与之对照的 `base_atr` 列**保留**：它虽同样无读者（I-6），但属 §五 的列契约、由 T-9 断言存在，
> 是对外承诺而非内部状态，两者性质不同。

### RV-3（注释交叉引用指错）：`trail_atr_mult` 的默认值注释指向 I-6

`trend_band_swing.py` 里 `"trail_atr_mult": 5.0` 的注释写「详见 spec §实施过程发现的方案缺陷 I-6」，
而这条修正是本节的 **I-1**（I-6 是 `base_atr` 无消费者）。改默认值这种「与设计稿有意偏离」全靠
注释指回文档才可追溯，指错等于没指。**修复**：改为 I-1。

### 未改动 / 记录在案

- **`regimeAffinity` 未收录本策略**：后端 `engine/regime.py:REGIME_STRATEGY_HINTS` 与其前端只读镜像
  `apps/web/lib/regimeAffinity.ts` 都没有 `trend_band_swing` 条目。两者只驱动组合成员选择时的
  「体制亲和」徽标，`regimeAffinityLabels` 未命中即返回 `[]`（安全降级、不抛错），且补条目必然同时
  改前端镜像 —— 与 §八「不改前端」冲突。故本期不加，留待做体制轮动增量时一并处理。
- **`params_schema` 单行字典最长 131 列**：超过 §九-3 说的「~110」，但与仓内同类文件同档
  （`trend_pullback_long/short.py` 最长 130、`ema_cross_scalper.py` 124），且 ruff 实际未启用 E501
  （`[tool.ruff] line-length=110` 只在启用 `E5` 规则时才生效，本仓 select 为默认集），维持既有观感。

### 验证方式与未覆盖项

- **已跑绿**：`tests/test_trend_band_swing.py` 18/18；§6.2 三组回归命令
  （`test_strategies` + `test_strategies_registry` + `test_mtf` + `test_trend_pullback` = 32 passed，
  `test_api` + `test_strategies_api` = 18 passed）；`ruff check services/quant/app/strategies` 新增文件
  零告警（既有 22 条全部落在 `trend_pullback_*` / `long_shadow_reversal` / `momentum_breakout` /
  `bollinger_reversion`，与本特性无关）。
- **终审复跑（Subtask #3，含上节三处修复后）**：`test_trend_band_swing` **19/19**（含新增 T-19）；
  §6.2 三组回归 32 + 18 全绿；额外跑 `test_engine_regime` + `test_combo_engine` 一并 50 passed；
  ruff 对本特性三个文件仍零告警。既有红灯基线（`test_factor_library_expansion` 7 个孤儿用例、
  `test_agent_*` 计时 flake）未触碰，与本特性无关。
- **G-1 已离线复刻**：临时库 `init_db()` + ASGI `GET /api/strategies` 确认 seed 出
  `templateKey="trend_band_swing"`、`kind="atomic"`、`builtin=true`、`paramsSchema` 22 项、`params` 32 项。
- **G-4 已离线复刻**：上文那条构造路径的带帧回测中，5 笔开仓**全部为 short**，无一笔逆 12H/1D 空势做多；
  成交 reason 形如 `12H/1D空势·中轨反抽做空`。
- **未覆盖（需真实环境的人工闸门）**：G-2（前端页签与面板渲染）、G-3（BTC-USDT-SWAP 真实 30 天回测 +
  `htfBars` + 墙钟基线）、G-5（实时模拟会话）、G-6（关掉 `flip_exit` 再跑一次）、G-8（真实成交的次根行为）
  ——这四条都要连 OKX 或起桌面端，不在本节点的执行环境内。
- **终审未能自行复刻带成交的回测（诚实记录）**：评审另建了一条 80 天「6 根 12H 下跌 + 3 根反抽」的
  5m 路径重跑全链路，`aligned` 与 `not_opposed` 均 **0 成交**。逐门禁计数定位到卡点在基础周期扳机：
  该路径是平滑斜坡，每根 5m 的漂移（≈0.004%）小于影线宽度，`c <= 前根 low` 几乎恒不成立；方向层
  （19274 根放行）、挤压过滤、位置层（mid 区 2721 根）、反向动能门槛都正常工作，且 mid 区的
  `cf_trend_dir` 全为 `+1/0`（反抽途中 15m 尚未转向，`aligned` 按设计就该拒绝）。**这是构造数据的
  性质而非代码缺陷**——成交层面的端到端仍以 Subtask #2 的 G-4 离线复刻（5 笔全 short）与 19 个单测
  （四道离场各自的正负向断言）为准，真实市场表现须走 G-3。同时它给出一条调参提示：影线宽、单根
  漂移小的低波动标的上，「破前根高低点」这一条扳机会显著压低开仓频率（R-1 的具体形态）。
