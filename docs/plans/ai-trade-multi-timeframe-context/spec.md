# AI 自动交易多周期决策背景 Implementation Plan

> **版本：v2（设计评审修订版）**
>
> **For downstream engineer:** 按本文逐项实施；先写失败测试，再完成最小实现。实现期间必须保持实盘与回测因果一致，并继续复用 `LiveExecutor`，不得新增第二条下单路径。

## 评审记录

- **P0（已修复）— `12H` 行情能力与执行周期边界混淆。** v1 仅说明把 `12H` 加入 `BAR_SECONDS`、不加入 WS/UI，但 AI 实盘与回测请求的 `bar` 当前是普通字符串，且实时 `OKXWSClient` 对未知周期会回退到 `candle1H`。直接扩表会让直接 API 调用存在“名义 12H、实际 1H 行情”的危险路径。v2 将“REST 可取行情周期”和“允许执行周期”分开：`12H` 只进入行情秒数表；AI 实盘/回测入口在 Pydantic 边界继续只接受 `BAR_TO_WS_CHANNEL` 对应的既有执行周期，并增加 12H 拒绝测试。
- **P1（已修复）— 高周期刷新按 Unix 整数桶会错过交易所实际边界。** v1 沿用 `base_ts // seconds` 的思路，无法正确表达 OKX 非 UTC 零点锚定的日线/12H 边界，且现有 `_safe_get_candles()` 无法区分“请求成功但无新增”和“请求失败”。v2 改为由 REST 返回的最新原始 K 线开盘时间推导 `nextRefreshTs`；失败不推进，下一基础根重试，并对非零点锚定边界做回归测试。
- **P1（已修复）— 回测/实盘的 `asOfTs` 使用了基础 K 线开盘时间。** AI 在确认根收盘后决策；若仍传 `row.ts`，恰在高周期收盘边界会少看到一根当时已经确认的高周期 K 线。v2 统一用 `decisionAsOfTs = baseOpenTs + BAR_SECONDS[executionBar]`，同时保留既有 `barTs` 为基础根开盘时间，避免事件契约破坏。
- **P1（已修复）— 完全缺数与部分降级语义不完整。** v1 在全部周期不可用时返回 `None`，导致无法区分“功能关闭”和“功能开启但行情缺失”，且没有定义 `regime`/`digest` 均为空时是否算可用。v2 规定：仅功能关闭或政策周期为空返回 `None`；功能开启但无证据仍返回结构化对象并列全量 `missingBars`；每个周期至少一个派生结果非空才进入 `frames`，陈旧周期同时进入 `staleBars`。
- **P1（已修复）— 既有体制/摘要开关与新总开关关系未定义。** v2 明确新总开关控制整个多周期层；已有 `ai_trade_regime_context` 和 `ai_trade_market_digest` 分别控制每个高周期的 `regime` 与 `digest` 子结果。二者都关闭时，多周期对象仍如实报告缺失，不伪造证据。
- **P1（已修复）— 回测审计事件未要求携带模型实际看到的多周期输入。** v2 要求历史回测的实时 `decision` 事件和最终 `decisionsDigest` 与实盘 `decision` 一样保存同一 `multiTimeframeContext`，使结果可复查；仍不保存原始 K 线。
- **P1（已修复）— v1 声称字段进入 journal，但变更清单没有 `decider.py`，与现有显式 journal 结构不一致。** v2 删除这项不必要扩展：完整背景进入当轮 prompt 和有界审计事件；滚动摘要继续依赖既有 `analysis` 摘录，不把四周期完整结构复制进 `_journal`，避免扩大上下文与文件范围。
- **P2（已处理）— 请求量、prompt 和轨迹体积增长。** v2 保持每标的最多四个背景周期、策略/背景请求去重、窗口与决策日志有界；prompt 只输出每周期一行摘要，结构化完整结果仅进入既有有界事件/轨迹。无需新增缓存服务、数据库表或并行调度器。

**Goal:** 让 5m / 15m / 30m 等短线 AI 决策在每一轮都获得独立于所选策略的 1H / 4H / 12H / 1D 中线背景，包括趋势、候选支撑阻力、超买超卖和经典指标，再以该背景指导开仓、平仓或观望。

**Architecture:** 在现有 `MarketWindow` 与 AI payload 之间增加一个纯计算的 `multi_timeframe_context` 层。实盘会话统一维护真实、已确认的高周期 K 线；历史回测一次性加载相同周期并按决策收盘时点切片。两个入口都调用同一个因果构造器，随后由 prompt、实盘/回测决策事件、轨迹事件和前端只读视图消费。行情 REST 支持的周期与允许启动 AI 交易的执行周期是两个显式集合，`12H` 只属于前者。

**Tech Stack:** Python 3.11、FastAPI、pandas、现有 OKX REST 适配器、pytest；Next.js 14、React 18、TypeScript、Vitest。无新增第三方依赖，无数据库迁移。

---

## 1. Overview（概述）

当前 AI 自动交易已经有策略信号板、基础周期市场体制 `marketContext` 和基础周期技术摘要 `marketDigest`，但这三者不能满足本需求。`session._build_decision_payload()` 把 `MarketWindow.base_df()` 同时交给 `build_market_context()` 与 `build_market_digest()`，因此当执行周期是 5m 时，这两份证据仍然只是 5m 证据。`MarketWindow` 虽然保存了高周期窗口，但高周期集合来自所选策略的 `strategy.timeframes()` 并集，只用于给各策略构造指标帧；普通单周期策略没有声明 12H / 1D 时，AI 决策者完全看不到这些背景。这正是本功能要消除的结构性缺口。

本方案新增“决策级多周期背景”，并明确它与“策略级 MTF 帧”是两个概念：前者对所有 AI 决策默认存在，不因策略选择而变化；后者继续只服务策略自身，不改变策略信号。短线执行周期使用固定、确定的背景映射，按从长到短的层级向 LLM 提供 1D、12H、4H、1H 证据。每个周期只使用在当前决策时间已经收盘的真实 K 线，计算既有市场体制和市场摘要，并额外汇总趋势一致性、RSI 超买/超卖周期和最近候选支撑/阻力。LLM 负责综合研判，现有置信度、共识、预算、OCO、互斥和执行器仍是最终硬护栏。

设计比较过三条路径。第一种是只改 system prompt，要求 LLM 自己“考虑大周期”；它没有相应真实数据，必然诱发臆测，拒绝。第二种是复用策略声明的高周期并集；它使决策输入随策略组合随机变化，正是现有缺陷，拒绝。第三种是本方案的独立决策背景层：多一次有界 REST 预取和少量纯计算，但数据确定、可测试、可回放且不污染策略帧，故采用。

## 2. Technical design（技术设计）

### 2.1 周期政策与数据所有权

新增纯函数 `decision_timeframes(execution_bar)`，周期映射必须是单一真源：

| 执行周期 | 决策背景周期（内部按秒升序保存，prompt 按降序展示） |
|---|---|
| `1m` / `5m` / `15m` / `30m` | `1H`, `4H`, `12H`, `1D` |
| `1H` | `4H`, `12H`, `1D` |
| `4H` | `12H`, `1D` |
| `1D` | 空；1D 已是当前支持的最长执行周期，继续使用基础周期 digest |

`12H` 是 OKX V5 candlesticks REST 的正式 bar 值。把它加入表示“REST 可取真实 K 线”的 `BAR_SECONDS`，但不得加入 `BAR_TO_WS_CHANNEL`、前端 `Bar` 联合类型或周期选择器。`AiTradeGroupStartIn.bar` 与 `AiBacktestStartIn.bar` 在 Pydantic 请求边界显式限制为 `BAR_TO_WS_CHANNEL` 的既有键（`1m/5m/15m/30m/1H/4H/1D`）；直接提交 `12H` 必须在创建 run、会话或回测作业之前失败。这样 `bar_to_seconds("12H")` 可供内部 REST/MTF 加载使用，却不会把 12H 变成新的 AI 执行周期，更不会触发 `OKXWSClient` 的默认 1H channel 回退。

`MarketWindow` 在构造时计算两个集合：`strategy_bars` 来自策略声明；`context_bars` 来自上述政策；底层 `_htf_windows` 使用二者去重后的并集，因此相同的 1H / 4H 只抓取一次。决策背景至少保留 80 根高周期 K 线，覆盖 `classify_regime` 的 60 根下限及 `market_digest` 的 35 根下限；窗口仍受 `PAPER_WINDOW_MAX` 硬上限约束。

实盘启动时 `prefill_htf()` 为并集逐周期拉取最近真实 K 线，只把 `confirm=True` 的记录写入窗口，但刷新调度必须观察这次成功响应里的**全部原始 K 线**（含最新未确认根）。每周期维护 `nextRefreshTs`：成功响应非空时取最新原始根的 `ts + BAR_SECONDS[bar]`；只有 `decisionAsOfTs >= nextRefreshTs` 才再次请求。该规则直接跟随交易所返回的真实开盘锚点，不得使用 `base_ts // seconds` 推断 12H/1D 边界。成功但没有新确认根也可推进 `nextRefreshTs`；请求异常则返回显式失败状态、不推进调度，下一根基础 K 线重试。响应为空时同样不推进，避免把异常空响应当成成功。不得伪造或用基础周期重采样数据，本功能也不增加高周期 WebSocket 连接。

所有周期预取继续串行复用现有 REST 客户端的超时/重试，不新增并发风暴。多标的组即使部分周期预取失败也可以启动，但首轮必须在 `missingBars` 中如实暴露；后续成功刷新后自然恢复。

### 2.2 纯计算多周期构造器

新增 `services/quant/app/ai_auto/multi_timeframe_context.py`，不得执行网络、数据库或 broker 操作。公开签名固定为：

```python
def decision_timeframes(execution_bar: str) -> tuple[str, ...]: ...

def build_multi_timeframe_context(
    *,
    execution_bar: str,
    frames: dict[str, pd.DataFrame],
    as_of_ts: int,
    last_px: float,
    settings: Settings,
) -> dict | None: ...
```

调用方传入的 `as_of_ts` 是决策可见时刻，而不是基础 K 线开盘时间：`decisionAsOfTs = baseOpenTs + BAR_SECONDS[execution_bar]`。构造器先对每个政策周期执行硬性因果过滤：只保留 `row.ts + BAR_SECONDS[bar] <= as_of_ts` 的 K 线。这一检查必须存在，即使实盘窗口已经过滤 `confirm`，因为历史回测拿到的是完整区间数组。过滤后按 `ts` 升序排列，并按 `ts` 去重保留最后一条；未来行、非有限 OHLCV 行不得进入指标计算。

对每个因果切片调用既有 `build_market_context(frame, bar, settings)` 和 `build_market_digest(frame, bar, settings)`，不复制 RSI、MACD、布林、ATR、ROC 或体制算法。新总开关 `ai_trade_multi_timeframe_context` 先控制整个层；已有 `ai_trade_regime_context` 与 `ai_trade_market_digest` 分别继续控制每周期 `regime` 与 `digest`。某周期至少一个派生结果非 `None` 才进入 `frames`；无已收盘数据、清洗后为空、两个派生结果均为空或该周期出现意外异常时，把该 bar 加入 `missingBars`，其余周期继续。

每个可用 frame 产出 `bar`、`sampleCount`、最后已收盘时间 `closeTs`、`ageSeconds`、`stale`、`regime` 和 `digest`。`ageSeconds = max(0, asOfTs - closeTs)`；`stale=True` 的唯一规则是 `ageSeconds >= 2 * BAR_SECONDS[bar]`。陈旧帧仍保留在 `frames`，同时加入 `staleBars`，但 prompt 明确标记陈旧，不得当作新鲜确认。`frames`、`missingBars`、`staleBars` 均按 `1D → 12H → 4H → 1H` 的政策顺序稳定输出。

只有功能总开关关闭或 `decision_timeframes(execution_bar)` 为空时正常返回 `None`。功能开启但所有周期不可用时仍返回带空 `frames` 和完整 `missingBars` 的对象，使 prompt、事件和 UI 能区分“关闭”与“缺数”，并回退到基础周期证据。未知执行周期、非正 `last_px`、非整数 `as_of_ts` 属内部契约破坏，纯函数明确抛 `ValueError`；实盘/回测调用点只捕获该异常，记录不含密钥的关键参数并把该轮字段降级为 `None`，不能让决策轮或作业失败。

汇总字段必须由简单、可解释的规则生成：

- `trendAlignment` 只依据每个 frame 的 `regime.primary`。`trend_up` 进入 `bullishBars`，`trend_down` 进入 `bearishBars`，`range` 进入 `rangeBars`。只有偏多且没有偏空时为 `bullish`，只有偏空且没有偏多时为 `bearish`，两边都有为 `mixed`，只有震荡为 `range`，无体制结果为 `insufficient`。不做隐藏权重或价格预测。
- `rsiOverboughtBars` / `rsiOversoldBars` 读取 `digest.factors.rsi14.label`，使“多个大周期同时超买/超卖”可被直接识别；布林和动量标签仍保留在各 frame digest 中。
- 每个 frame 的 `digest.priceAction.swingLow` 是候选支撑、`swingHigh` 是候选阻力。候选支撑限定 `price <= last_px` 后取最高值，候选阻力限定 `price >= last_px` 后取最低值；同价来源合并 `bars`。结果包含 `price`、相对最新价的非负 `distancePct` 和 `bars`。找不到时为 `null`。文案必须称“候选”，不能把区间高低点伪装成已经验证的支撑阻力。

### 2.3 实盘决策序列

确认的基础 K 线进入 `AiTradeSession.feed_candle()` 后，先计算 `decisionAsOfTs = candle.ts + BAR_SECONDS[self.bar]`，顺序保持：基础窗口追加 → 以 `decisionAsOfTs` 驱动高周期刷新 → 各策略帧与信号板 → 决策 payload。既有事件的 `ts` / `barTs` 继续使用基础根开盘时间，只有多周期上下文的 `asOfTs` 使用决策收盘时刻。`MarketWindow` 新增只读方法 `context_frames() -> dict[str, pd.DataFrame]`，只返回 `context_bars` 的副本/新 DataFrame，不把策略 `prepare()` 生成的指标列暴露给上下文构造器。

`_build_decision_payload()` 在现有 `marketContext` 和 `marketDigest` 之外添加 `multiTimeframeContext`。它以 `decisionAsOfTs` 为 `as_of_ts`、本轮收盘价为 `last_px`。该字段随后原样进入 `AiTradeDecider.decide()`、`decision` WS 事件、`agent_trace.round_start` 和重连 snapshot；不把四周期完整结构复制进 `decider._journal`，滚动摘要继续使用既有有界 `analysis` 摘录。`_emit_decision_skipped()` 因没有发生新 AI 决策，字段固定为 `None`。任何多周期数据缺失都只影响该字段，不得跳过决策轮；全部缺失时 prompt 明示缺失并回退到当前基础周期逻辑。

system prompt 的研判顺序改为：**当输入提供可用多周期证据时**，先从 `1D → 12H → 4H → 1H` 判断中线背景、趋势一致性、候选支撑阻力和超买超卖，再分析当前执行周期的价格/成交量/技术因子，最后批判策略信号并结合持仓风险给出动作；字段为 `None` 或所有周期缺失时直接执行原基础周期顺序。明确以下纪律：高周期强冲突时短线开仓应优先 `hold`，除非证据充分且使用保守档位；超买/超卖不是单独开仓指令；已有持仓同时出现短线失效与高周期反转时优先考虑 `close`；陈旧或缺失周期不得补猜。它仍是软决策背景，不能新增一个绕过 LLM 的方向硬门禁，以免破坏合法的震荡/均值回归策略。

### 2.4 历史回测一致性

`AiBacktestRunner._load_htf()` 把策略周期与 `decision_timeframes(self.bar)` 合并去重，并以 `max(80, strategy_htf_warmup)` 根的 HTF warmup 调用现有 `load_htf_frames()`。`_setup()` 除 `main_df` 与策略 dfs 外，再把原始高周期 Candle 列表一次性转换成只含 OHLCV 的 `context_frames`，随 setup 结果传给 `_run_loop()` / `_build_payload()`；不能在逐根循环中重新请求网络或重复转换整个数组。

每个决策点 `_build_payload()` 调用同一个 `build_multi_timeframe_context()`，传入完整 `context_frames` 和 `decisionAsOfTs = int(main_df.iloc[i].ts) + BAR_SECONDS[self.bar]`。构造器内部的 close-time 过滤保证一根 12H K 线只有在 `open_ts + 43200 <= decisionAsOfTs` 时可见；例如 15:55 开盘、16:00 收盘的 5m 决策可以使用恰在 16:00 已收盘的高周期根，但不能使用 16:00 才开盘的下一根。改变 `decisionAsOfTs` 之后的高周期 K 线不得改变历史决策点 payload。

回测 `_emit_decision()` 把 payload 中同一个 `multiTimeframeContext` 放入实时 `decision` 事件；该事件原本就进入有界 `_decisions_digest`，因此最终 `result.decisionsDigest` 自然携带同一字段。前端复用 `AiTradeDecision` 可选字段即可兼容，不要求本期在回测大屏增加第二套展示组件。这样实盘与回测共享指标、汇总、prompt 和审计形状，只在行情来源与执行器上不同。

### 2.5 前端可观测性

前端不新增设置项，也不允许用户手填背景周期。wire 类型增加可选 `multiTimeframeContext`，保持旧后端/旧快照兼容。store 在 `round_start` 创建轨迹时复制该字段。`AiTradeTraceRound.SignalContext` 在基础周期 `marketDigest` 之前增加“中线背景”块：第一行显示整体偏多/偏空/混合、候选支撑/阻力；其后按 1D、12H、4H、1H 展示趋势体制、RSI 标签、区间位置与样本数，并给陈旧周期加“数据陈旧”。缺失周期显示在一条低强调提示中；全部缺失时显示“中线背景暂不可用，已回退基础周期”，不渲染伪值。该区域纯展示，绝不参与决策或下单。

## 3. File / module change plan（文件 / 模块变更计划）

| 文件 | 动作 | 意图 |
|---|---|---|
| `services/quant/app/ai_auto/multi_timeframe_context.py` | 新建 | 周期政策、close-time 因果过滤、逐周期 digest/regime 复用、趋势/超买超卖/候选支撑阻力汇总 |
| `services/quant/app/okx/types.py` | 修改 | 在 REST/MTF 秒数真源 `BAR_SECONDS` 增加 `12H: 43200`；`BAR_TO_WS_CHANNEL` 保持不变，明确其键才是实时执行周期 |
| `services/quant/app/config.py` | 修改 | 增加默认开启的 `ai_trade_multi_timeframe_context: bool = True` 回退开关 |
| `services/quant/app/ai_auto/market_window.py` | 修改 | 合并策略周期与决策周期、至少预填 80 根、暴露 `context_frames()`、用 REST 原始根推导 `nextRefreshTs`、失败不推进 |
| `services/quant/app/ai_auto/session.py` | 修改 | 用基础根收盘时刻构造上下文，并传播到 decider、decision 事件与 trace；跳过轮固定为空 |
| `services/quant/app/ai_auto/prompts.py` | 修改 | 新增紧凑多周期格式化；把长周期到短周期的研判纪律写入 system/user prompt |
| `services/quant/app/ai_auto/trace.py` | 修改 | 在 `new_round_trace()` 输入快照中保存多周期上下文 |
| `services/quant/app/ai_auto/backtest.py` | 修改 | 加载策略+决策周期并集、一次性建立背景帧、按基础根收盘时刻调用同一构造器并写入 decision/digest |
| `services/quant/app/schemas.py` | 修改 | AI 实盘/回测请求边界拒绝 12H 执行；给 `AiTradeDecisionOut` 与 `AiTradeRoundTraceOut` 增加可选契约镜像字段 |
| `apps/web/lib/aiTradeTypes.ts` | 修改 | 声明多周期 frame、汇总、候选价位类型，并给 decision/trace 事件与状态加可选字段 |
| `apps/web/lib/aiTrade.ts` | 修改 | 增加整体方向、候选价位、逐周期证据的中文只读格式化函数 |
| `apps/web/lib/store.ts` | 修改 | `emptyRoundTrace()` 从 `round_start` 保留 `multiTimeframeContext` |
| `apps/web/components/Agent/AiTradeTraceRound.tsx` | 修改 | 在输入信号折叠区展示“中线背景”及缺失/陈旧状态 |
| `services/quant/tests/test_ai_trade_multi_timeframe_context.py` | 新建 | 纯函数政策、聚合、候选价位、降级、禁用和未来泄漏测试 |
| `services/quant/tests/test_ai_trade_market_window.py` | 新建 | 证明普通单周期策略也预取决策周期、并集去重、只返回背景帧、失败可重试 |
| `services/quant/tests/test_ai_trade_session.py` | 修改 | 断言实盘 payload、decision 与 trace 均携带字段，缺失时决策仍进行 |
| `services/quant/tests/test_ai_trade_prompts.py` | 修改 | 断言长→短顺序、冲突纪律、缺失/陈旧标识和字段缺省回退 |
| `services/quant/tests/test_ai_backtest_runner.py` | 修改 | 断言回测加载决策周期并集并把相同上下文交给 decider |
| `services/quant/tests/test_ai_trade_api.py` | 修改 | 断言 AI 实盘启动请求拒绝 12H，且拒绝发生在任何 run/会话副作用之前 |
| `services/quant/tests/test_ai_backtest_api.py` | 修改 | 断言 AI 回测启动请求拒绝 12H，且不创建作业 |
| `services/quant/tests/test_mtf.py` | 修改 | 增加内部 REST/MTF `12H ↔ 43200` 映射回归，并锁定 WS 执行周期集合不含 12H |
| `apps/web/lib/aiTrade.test.ts` | 修改 | 覆盖多周期方向/候选价位/陈旧与未知枚举格式化 |
| `apps/web/lib/aiTradeTrace.test.ts` | 修改 | 断言 reducer 在 round_start、后续 phase 与重放中保留多周期上下文 |
| `apps/web/components/Agent/AiTradeAgentTrace.test.tsx` | 修改 | 断言中线背景按长到短显示，缺失与陈旧状态可见 |

除表中路径外不得修改执行器、订单构造器、数据库模型、API 启动入参、策略实现或 live-auto。特别禁止把多周期汇总接入 `apply_consensus_gate()` 或直接产生 `Signal`。

## 4. Interface design（接口设计）

REST、WebSocket 路径和启动请求保持不变：`POST /api/ai-trade/start`、`POST /api/ai-trade/backtest/start`、`WS /api/ws/ai-trade/{session_id}`。这是加法式响应扩展，不需要客户端版本协商。

`decision` 事件及 `agent_trace` 的 `round_start` data 新增：

```ts
interface MultiTimeframeContext {
  executionBar: string;
  asOfTs: number;
  frames: MultiTimeframeFrame[];
  trendAlignment: {
    status: "bullish" | "bearish" | "mixed" | "range" | "insufficient";
    bullishBars: string[];
    bearishBars: string[];
    rangeBars: string[];
  };
  rsiOverboughtBars: string[];
  rsiOversoldBars: string[];
  nearestSupport: CandidateLevel | null;
  nearestResistance: CandidateLevel | null;
  missingBars: string[];
  staleBars: string[];
}

interface MultiTimeframeFrame {
  bar: string;
  sampleCount: number;
  closeTs: number;
  ageSeconds: number;
  stale: boolean;
  regime: MarketContext | null;
  digest: MarketDigest | null;
}

interface CandidateLevel {
  price: number;
  distancePct: number;
  bars: string[];
}
```

字段值必须可 JSON 序列化；NaN/Infinity 归一为 `null` 或使该候选被忽略。Python 侧保持 camelCase，因为该 dict 直接进入 wire 和 prompt。功能开启但缺数时返回 `frames=[]` 与非空 `missingBars`；`multiTimeframeContext` 缺省或 `null` 仅表示旧后端、功能关闭、无政策周期或被调用点捕获的内部契约错误，均为合法兼容形状。旧前端忽略未知字段，新前端对空值不渲染。

## 5. Data model（数据模型）

无数据库 schema 和迁移。所有状态是会话内存态或回测作业内存态：`MarketWindow._htf_windows` 继续是每周期有界 `deque[dict]`；`context_frames()` 临时转换为 pandas DataFrame；回测作业持有一次性 `dict[str, DataFrame]`。决策日志、轨迹环形缓冲和最终 run 的既有 JSON 能自然承载新增可选字段，不增加新的表或列。

数据不变量如下：所有 `ts` 仍是 UTC 秒；K 线 `ts` 表示开盘时刻；事件 `barTs` 保持基础根开盘时间，而多周期 `asOfTs` 是该基础根收盘时间；高周期可见性以 `ts + period_seconds <= asOfTs` 判定；frame 必须按 `ts` 升序且同 `ts` 唯一；只有 OKX 返回的 `confirm=True` 记录可进入上下文窗口（未确认根只参与推导下一次刷新时刻）；决策背景与策略 prepare 后的列互不共享；总窗口、决策日志和轨迹缓冲继续有界；任何不可用数据如实标为 missing/stale，不允许用基础周期本地重采样冒充真实 12H/1D。

## 6. Testing & acceptance criteria（测试与验收标准）

### 6.1 后端测试

1. `decision_timeframes("5m")` 精确返回 `("1H", "4H", "12H", "1D")`，`1H`/`4H`/`1D` 映射符合表格，未知 bar 明确抛 `ValueError`。
2. 内部 `bar_to_seconds("12H") == 43200`，但 AI 实盘与 AI 回测 HTTP 启动请求提交 `bar="12H"` 分别返回边界校验错误，且 run、会话、作业注册表均无新增；`BAR_TO_WS_CHANNEL` 仍不含 12H。
3. 构造明确上行、下行、震荡的多周期帧，断言 `trendAlignment`、RSI 列表、`sampleCount` 和最近候选价位严格符合规则。
4. 以 15:55 开盘的 5m 确认根构造决策，断言 `asOfTs=16:00`；`openTs + seconds == asOfTs` 的高周期根恰好可见，差一秒不可见。在 `asOfTs` 之后追加走势相反的 12H/1D K 线，当前上下文必须逐字相同。
5. 用非 Unix 零点锚定的 1D/12H 原始 K 线证明 `nextRefreshTs = latest_raw.ts + seconds`；成功但无新增确认根会推进到真实下一边界，空响应/异常不会推进，下一基础根会重试，恢复后只追加 `confirm=True` 根。
6. 单周期少于 35/60 根、含零成交量、含 NaN、计算器返回 `None` 或单周期处理抛错时，其余周期与决策轮继续；无可用证据的周期进入 `missingBars`，不得生成假值。全部周期缺失时对象仍存在且 `frames=[]`。
7. 分别关闭新总开关、既有体制开关、既有摘要开关及两个子开关，断言 `None` / 部分 frame / 全量 missing 的语义符合 §2.2，prompt 不要求模型补猜。
8. 选择只声明基础周期的策略时，`MarketWindow` 仍包含政策周期；策略与背景都需要 4H 时只请求一个 4H 窗口。
9. 实盘 session 的 decider payload、decision 事件和 round-start trace 三处字段相同；`decision_skipped` 为 `None`；缺失时决策仍进行且原有市场摘要、共识和执行路径不变。
10. 回测首个用户区间决策点已有至少 80 根 HTF warmup；相同历史输入重复运行得到相同背景 payload；实时 decision 与最终 `decisionsDigest` 携带同一字段；逐根循环不发生额外网络请求。
11. 运行现有 execution bar、WS channel、MTF 因果、AI 实盘与 AI 回测测试，确认所有旧周期和执行路径不回归。

### 6.2 前端测试

1. 类型、formatter 和 reducer 接受字段缺失，兼容旧 snapshot。
2. 轨迹卡按 `1D → 12H → 4H → 1H` 展示，不依赖后端 frames 数组偶然顺序。
3. 整体 mixed、RSI 超买/超卖、候选支撑阻力距离、样本数、missing/stale 都有稳定文本；未知标签原样回显，不臆造中文含义。
4. `multiTimeframeContext=null` 时不出现“中线背景”容器；对象存在但 `frames=[]` 时显示明确的全缺失回退提示；原信号板、体制、基础 digest、工具调用与结果仍正常渲染。
5. 回测 `decision` / `decisionsDigest` 可携带同一可选字段并通过现有 `AiTradeDecision` 类型与 reducer，不增加非必要的持久 store 状态。

### 6.3 验收命令与产品标准

后端运行 `services/quant/.venv/Scripts/python -m pytest services/quant/tests/test_ai_trade_multi_timeframe_context.py services/quant/tests/test_ai_trade_market_window.py services/quant/tests/test_ai_trade_session.py services/quant/tests/test_ai_trade_prompts.py services/quant/tests/test_ai_backtest_runner.py services/quant/tests/test_ai_trade_api.py services/quant/tests/test_ai_backtest_api.py services/quant/tests/test_mtf.py -q`，预期全部 PASS。前端运行 `pnpm --filter web test -- aiTrade.test.ts aiTradeTrace.test.ts AiTradeAgentTrace.test.tsx AiBacktestModal.test.tsx`，预期全部 PASS；随后运行项目既有完整 `pnpm test`。

人工验收以 5m BTC-USDT-SWAP 模拟/受控实盘会话为例：首次决策轨迹必须显示 1D/12H/4H/1H，12H/1D 的最后收盘时间不得晚于本轮 5m **收盘时刻**；更换所选策略不应删除这些背景周期；断网时显示陈旧/缺失而不是伪造；恢复后下一基础根可重新获得新背景。另用直接 API 请求确认 `bar=12H` 不能启动 AI 实盘或 AI 回测。AI 的自然语言研判必须先讨论可用中线背景再讨论 5m 触发；全部缺失时必须明确回退且不补猜。真正动作仍只由一次 `submit_trade_decision` 工具调用产生，并经原 `LiveExecutor` 守护执行。

## 7. Risks & mitigations（风险与缓解）

| 风险 | 缓解措施 |
|---|---|
| 高周期未来泄漏或边界少算，尤其回测把未收盘 12H/1D 送入 AI、或漏掉恰好已收盘的根 | `asOfTs=baseOpenTs+baseSeconds`；构造器以 `openTs + seconds <= asOfTs` 二次过滤；等号/差一秒/未来变更专测；实盘窗口仍只收 confirm 根 |
| 5 个标的启动时额外 REST 请求触发限流 | 每标的最多四个背景周期、与策略周期去重、每周期启动一次且仅跨边界刷新；保持现有 REST 重试/错误显式化 |
| 12H 扩展意外变成新的执行周期，甚至因 WS 默认值而实际订阅 1H | `BAR_SECONDS` 仅表示 REST/MTF 能力；AI 实盘/回测请求边界只接受 `BAR_TO_WS_CHANNEL` 既有键；不加前端 Bar/WS channel；API 负测锁定零副作用拒绝 |
| 12H/1D 的交易所开盘锚点与 Unix 零点不同，整数桶导致刷新延迟 | 用成功 REST 响应的最新原始根 `ts + seconds` 推导 `nextRefreshTs`；非零点锚定测试；失败/空响应不推进且下一基础根重试 |
| prompt 过长、LLM 延迟增加 | 每周期仅格式化一行关键结论；完整结构只用于 trace；继续受现有决策超时、重试和安全 hold 保护 |
| 大周期与短线策略冲突导致行为分布变化 | 这是需求目标，但只改变 LLM 输入；不增加硬方向门禁，保留禁用开关用于 A/B 回退，所有资金与执行护栏不动 |
| 区间高低点被误称为真正支撑阻力 | 数据字段和 UI 统一称“候选支撑/阻力”，展示来源周期与距离，不声称已验证 |
| 某周期异常拖垮整轮或全部缺数被误认为功能关闭 | 单 frame 隔离降级并记录 missing/stale；总开关开启时即使全部不可用也返回空 frames + 完整 missing，决策回退到现有基础周期证据 |
| 实盘与回测实现漂移 | 两者只负责准备 frames，所有过滤、计算和汇总共享 `build_multi_timeframe_context()`；等价 payload 测试锁定 |
| 轨迹内存增加 | 高周期数量固定最多四个，沿用后端 20 轮/前端 20 轮有界轨迹；不保存原始 K 线到事件，只保存紧凑摘要 |

## 8. 实施顺序（无待决策项）

1. 先写 AI 实盘/回测 API 的 12H 执行拒绝测试与 `test_mtf.py` 的内部 12H 映射测试，再实现请求边界、周期政策与纯构造器，确保没有“12H 名义周期 + 1H WS”路径。
2. 写 `MarketWindow` 并集、非零点锚定、成功/空响应/刷新失败重试测试，再扩展窗口与 `nextRefreshTs`；确保策略 frame 输出逐字不变。
3. 写 session 与 prompt 失败测试，统一 `decisionAsOfTs`，接入实盘 payload、trace 和 decision 事件。
4. 写 backtest 收盘边界、未来泄漏、加载与 decision/digest 审计测试，接入历史背景帧；禁止复制聚合逻辑。
5. 更新 schema 镜像和前端类型/reducer/formatter/轨迹卡，并补 Vitest。
6. 运行定向测试、完整测试与人工检查。任何失败只在本文列出的文件范围内修复，不趁机重构执行器或策略系统。

完成后应得到一个可解释但不越权的层级决策过程：中长周期回答“当前大环境允许什么、关键候选价位在哪里”，基础周期回答“眼下是否出现触发”，策略信号提供可批判的线索，LLM 给出唯一动作，原有确定性守卫决定动作能否安全执行。

## 评审结论

**通过。** v2 已关闭全部 P0/P1：12H 不会越界成为执行周期；实盘刷新遵循交易所真实 K 线锚点且失败可重试；实盘与回测统一以基础确认根收盘时刻做因果截断；部分/全部缺失、子开关与总开关语义明确；回测决策输入可审计。剩余 P2 成本由固定周期数、请求去重、紧凑 prompt 和既有有界缓冲充分约束，不构成实施前置条件。

## 实施过程发现的方案缺陷

- 前端验收命令中的 workspace filter 写成了 `web`，但实际 `package.json` 名称是
  `aragon-web`；原命令会输出 `No projects matched the filters` 并零退出，无法验证任何测试。
  此外当前桌面构建环境继承了 `NODE_ENV=production`，会使 React Testing Library 的
  `act()` 在 Vitest 中不可用。实施与验收改用 PowerShell 命令
  `$env:NODE_ENV='test'; pnpm --filter aragon-web test -- ...`，不改变产品代码或依赖。
