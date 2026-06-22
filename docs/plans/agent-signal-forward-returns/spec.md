# Agent 信号前瞻收益工具 `test_signal_forward_returns` —— 设计方案 (spec)

> 文档版本：**v2**（Subtask #1 评审与修复完成：顶部含「评审记录」、文末含「评审结论」；P0/P1 已就地修复，无遗留）
> Feature slug：`agent-signal-forward-returns`
> 作者：Solution Architect（Anthropic Eng）
> 关联全局目标：为 Agent Trading Copilot 增加「**不完整跑策略、只测某信号出现后未来 N 根 K 线收益**」的工具能力与配套**渐进式提示词**，让 Agent 先验证「信号本身有没有统计优势」，再决定要不要做完整策略。
> 前置依赖（同构参照）：`docs/plans/agent-market-regime-labeling/spec.md`（v2，已落地的 `label_market_regime` 工具体系）；本方案与之**同一套薄增量范式**（1 个纯计算引擎模块 + 1 个工具壳 + 提示词增量 + 一行注册）。

---

## 0. 评审记录（Review Notes · Subtask #1）

> 评审人：Senior Reviewer（Anthropic Eng）。评审维度：**可行性 / 完整性 / 一致性 / 规模适配**。
> 评审 ID 用 `RV-n`（避免与正文「C 项目硬约束」「R 风险表」「P0/P1/P2 严重度」混淆）。
> 评审基线：已**逐条对照真实源码**核验本方案的全部架构断言（见下「已核验」清单），结论是**方案与既有 `label_market_regime` 体系高度同构、技术断言全部属实、可直接落地**；问题集中在数值边界安全与文档自洽性。**P1 已在本 v2 正文就地修复，无 P0、无遗留 P1**；P2 多数已修，余项作为下游放行条件在「评审结论」复述。

### 0.1 已核验属实的关键断言（构成可行性基线）

- `agent/loop.py:382-395`：`tool_result` 仅回喂 `summary`，`ui_action` 仅当 `is_ui_action and ok and payload` 才发 → 本工具非 ui-action，`payload` 确无线上消费方，统计与脚手架必须全进 `summary`。✅
- `agent/tools/base.py`：`require_str/check_enum/check_limit/check_optional_range/first_error` 签名均 **args 在先**、`BAR_ENUM` 七值齐备；`check_pos_int_list`（必填非空、不限上界）确已存在 → 新增 `check_int_list` 与之区分成立。✅
- `agent/tools/registry.py:28`：`_ALL_TOOLS` 为实例列表，`execute` 统一收敛 `OKXError/HTTPException/Exception → ok=False`。✅
- `engine/indicators.py`：`ema/rsi/bollinger(返回 upper,ma,lower)/atr` 纯函数因果，可直接复用。✅
- `engine/backtester.py:26 candles_to_df` / `:71 fill_pending(open_price=row["o"])` 且 `i<n-1` 才开仓 → 默认入场 `open[i+1]` 与撮合一致。✅
- `agent/services.py:30 load_range_candles(rest,*,inst_id,bar,limit,start_ts,end_ts)`、`okx/types.py·Candle(ts,o,h,l,c,vol,confirm)`、`regime_tools._drop_forming`、`engine/regime.py:18 from ..strategies import STRATEGY_REGISTRY` + `candidate_strategies()`（去重保序 + 按注册表过滤 + 补 `.meta.name`）—— 全部属实。✅
- 候选策略映射涉及的 `momentum_breakout / bollinger_reversion / vwap_rsi_momentum / ema_cross_scalper` 四 key 均在 `STRATEGY_REGISTRY` 真实存在。✅
- 命名一致性：内部参数 dataclass 用 snake_case（`SignalParams` 同 `RegimeParams`），payload 绑定 dataclass 用 camelCase（`HorizonStat` 同 `RegimeEvidence/RegimeResult`）——与既有 `regime` 约定一致，**非偏离**。✅

### 0.2 问题清单

| ID | 严重度 | 维度 | 问题 | 处置 |
|---|---|---|---|---|
| RV-1 | **P1** | 完整性/正确性 | §2.4 仅守护 `m<2`（std/t），未守护 `count==0`：某 horizon ≥ 窗口长度或贴近右端被全部截断时 `R_h` 为空 → `mean([])/median([])` 抛错或产 NaN；`avgWin/avgLoss` 在「无盈/无亏」子集上同样会对空集求均值。 | **已修**：§2.4 增「空样本安全口径」，`count==0`/空子集一律取 0.0、`payoff=None`、`tStat=0.0`，summary 标「样本 0」；§6.1 增 `test_zero_count_horizon_safe`。 |
| RV-2 | **P1** | 完整性/正确性 | 基线 `baseline_h` 在「该 horizon 无合格根」时同样对空集求均值（与 RV-1 同源）。 | **已修**：§2.4 规定无合格根时 `baselineRet=edgeVsBaseline=0.0`。 |
| RV-3 | **P1** | 一致性/清晰度 | §5.1「`SignalParams` 可透传覆盖」与 §4.1 工具 schema（无任何阈值字段）自相矛盾——模型实际无法经工具覆盖阈值；§2.5 脚手架却让模型「调阈值」。 | **已修**：v1 工具面**固定用默认阈值**，`forward_returns(params=)` 降为引擎/单测内部缝、不进 schema；§2.4/§2.5/§4.1/§5.1 统一措辞，阈值调参列为后续增强。 |
| RV-4 | P2 | 完整性 | 显式空 `horizons: []` 行为未定义；重复/乱序 horizons 未归一，summary 行会重复或乱序。 | **已修**：`check_int_list` 要求给值时长度 1..8；execute 内对 horizons **去重 + 升序**归一后再算。 |
| RV-5 | P2 | 清晰度 | §2.1/§8 简写 `get_rest_client().get_candles(limit)` 省略必填的 `instId,bar` 位置参数，易误导实现。 | **已修**：更正为 `get_candles(instId,bar,limit)`。 |
| RV-6 | P2 | 完整性/清晰度 | `entry_at="next_open"` 入 `open[i+1]`、出 `close[i+h]` 为「开盘→收盘」口径，与回测实际「开盘→开盘」出场不同口径，未披露。 | **已修**：§2.4 明示该不对称，提醒 edge 非可直接等同回测 PnL，落策略仍须 `run_backtest` 复核。 |
| RV-7 | P2 | 规模适配 | R3 的 `dedupe_bars` cooldown「留参数位本期不启用」属轻度 YAGNI（未实现的空旋钮）。 | **接受现状**：保留为 `forward_returns` 内部默认关闭 kwarg、不进 schema、本期不实现 cooldown 逻辑（非 no-op 陷阱）；§2.4 注明仅内部。 |

---

## 1. Overview（概述）

本方案为已落地的 **Agent Trading Copilot**（右侧「AI 助手」对话面板，后端 agentic loop + 工具调用）新增一个 **alpha 研究工具** `test_signal_forward_returns`，并配套一套**渐进式提示词（progressive prompts）**。它解决的问题是：在投入成本写/调一整套策略之前，先用最便宜的方式回答「**这个信号本身，到底有没有统计优势？**」——即「某个信号（如放量突破、RSI 超卖、价格触及布林下轨）出现之后，未来 1/3/6/12 根 K 线的收益分布、胜率与显著性如何，是否跑赢同期无条件基线」。这是量化 alpha 研究里最常用、最该先做的一步「事件研究（event study）」。

工具做的是**确定性、可复现、可审计**的统计计算，**不**调用回测引擎、**不**撮合、**不**计风控/持仓：从真实 OKX K 线出发，复用项目既有纯函数指标（`engine/indicators.py` 的 EMA / RSI / Bollinger / ATR），在**严格因果（信号检测只用历史数据）**前提下，先把一段窗口里**每根**是否触发某个具名信号标出来（信号库 8 个内置信号，含方向 long/short），再对每个触发点度量「**入场后持有 h 根的前瞻收益**」，最后按多个 horizon 聚合出**样本数 / 胜率 / 均值 / 中位数 / 标准差 / t 统计量 / 相对基线的超额收益（edge）**等口径。模型拿到这份**带样本量与显著性的统计画像**后，并不直接复述，而是被工具结果附带的**分阶段推理脚手架**牵引，显式写出三段式思考：① 读统计 → ② 判信号是否真有边际优势（样本是否够、跨 horizon 是否一致、是否跑赢基线、是否经得起成本）→ ③ 决定下一步（用 `run_backtest` 落成完整策略 / 调参细化信号 / 直接放弃）。

这一「工具算证据 + 提示词管推理」的分工，与既有 `label_market_regime` 完全同构，也与项目「绝不假数据（C1）/ 严格因果 / 不臆造指标」的硬约束一致：**统计数字必须由确定性引擎给出**，**「信号值不值得做成策略」的判断则交给模型在受控脚手架下完成**。本工具与 `label_market_regime` 形成天然的研究链路——先用体制工具判断「现在是什么市场」，再用本工具验证「在这种市场里，某信号是否真有 edge」，最后才用 `run_backtest` 把验证过的信号落成可回测的完整策略。整套能力是既有工具体系的**薄增量**：新增 1 个纯计算引擎模块 + 1 个工具类 + 提示词增量 + 注册一行 + 1 个校验原语，**不改任何对外 WS/REST 契约、不动数据库、不引入三方依赖**（`numpy/pandas` 已是回测既有依赖），与 `agent-trading-copilot`/`agent-market-regime-labeling` 既定架构、`CLAUDE.md` 代码阈值完全一致。

---

## 2. Technical design（技术设计）

### 2.1 在既有架构中的位置

```text
AgentSession.run (agent/loop.py)
  └─ _stream_assistant_turn → LLM 决定调用 test_signal_forward_returns
       └─ _execute_tool_calls → ToolRegistry.execute("test_signal_forward_returns", args, ctx)
            └─ TestSignalForwardReturnsTool.execute(args, ctx)   [新] agent/tools/signal_tools.py
                 ├─ 取真实 K 线：get_rest_client().get_candles(instId,bar,limit) / services.load_range_candles
                 │     └─ _drop_forming：剔除尾部未确认（forming）K 线（P-A / 因果 / C1）
                 ├─ candles_to_df()                              复用 engine/backtester.py
                 ├─ forward_returns(df, signal, horizons, ...)   [新] engine/signal_edge.py（纯计算）
                 │     ├─ detect_signal(df, signal, params)      复用 engine/indicators.py（ema/rsi/bollinger/atr）
                 │     └─ 逐 horizon：因果前瞻收益 + 方向调整 + 基线 + 统计量
                 └─ 组装 ToolResult(summary=统计画像+渐进式三步脚手架, payload=结构化统计)
       └─ loop 把 result.summary 作为 tool_result 回喂模型（本工具为非 ui-action：payload 既不回喂模型、也不下发前端，仅供单测/未来接线，见 §4.3）
       └─ 下一轮：模型按脚手架显式三段式推理 → 文本输出「信号是否有 edge + 下一步建议」
```

**关键事实（已对照真实源码核验，构成可行性基线）：**

- `agent/loop.py:390-394` —— `tool_result` 事件仅发 `{id,name,ok,summary}`，`ToolResultBlock(content=result.summary, ...)` 回喂模型的也是 `summary`；`payload` **仅当 `is_ui_action=True`** 时才作为 `ui_action` 事件经 WS 发前端。本工具非 ui-action（`is_ui_action=False`），故 **`payload` 无任何线上消费方**——**统计数字与三步脚手架必须全部写进 `summary`**（同 `label_market_regime` 的 P1-1 落点）。
- `agent/tools/base.py` —— `Tool` 子类设类属性 `name/description/input_schema/is_ui_action` + 实现 `validate/execute`；已备 `require_str/check_enum/check_limit/check_optional_range/first_error` 等原语，签名**均为 args 在先**：`require_str(args, key)`、`check_enum(args, key, allowed, *, required=False)`、`check_limit(args, low, high)`、`check_optional_range(args)`；`BAR_ENUM = ("1m","5m","15m","30m","1H","4H","1D")`。本方案另需一个「可选有界正整数数组」原语校验 `horizons`，拟在 `base.py` 增 `check_int_list`（见 §4.1）。
- `agent/tools/registry.py:28` —— `_ALL_TOOLS` 列表注册全部工具实例；`schemas()` 出 LLM 工具表；`execute()` 统一异常收敛——`OKXError → ok=False`（“OKX 暂不可达”）、`HTTPException → ok=False`、其他 `Exception → ok=False`（脱敏根因）。故工具实现保持精简，数据不足等以 `ValueError`/`HTTPException` 上抛即可。
- `engine/indicators.py` —— `ema/sma/rsi/bollinger/atr` 均为**纯函数、因果（trailing 窗口）、不就地改入参**，可直接复用算信号触发条件。
- `engine/backtester.py:26 candles_to_df(candles)` —— 把 `Candle` 列表转成含 `ts,o,h,l,c,vol` 列、按 `ts` 升序的 DataFrame，可直接喂检测与收益计算。
- `engine/backtester.py:69-79 run_backtest` —— 信号在第 `i` 根产生后，由 `broker.fill_pending(open_price=row["o"])` 在**下一根开盘**成交（`i < n-1` 才开仓）。本方案前瞻收益的**默认入场口径取「下一根开盘」`open[i+1]`**，与回测撮合一致，杜绝「用刚收盘那根即时成交」的前视偏差。
- `agent/services.py:30 load_range_candles(rest, *, inst_id, bar, limit, start_ts, end_ts)` —— 拉区间真实 K 线并过滤未确认 / 越界，空区间抛 `HTTPException(400, EMPTY_RANGE)`（registry 收敛为 `ok=False`）。`_filter_range_candles` 中 `getattr(c,"confirm",True)` 即 forming 过滤语义。
- `okx/types.py · Candle` —— 字段 `ts(秒级 UTC),o,h,l,c,vol,confirm`；`okx/rest_client.py · get_candles(inst_id,bar,limit,*,before,after)` 返回**升序**列表，单次上限 300。
- `strategies/__init__.py` 的**包导入副作用** + `strategies/base.py · STRATEGY_REGISTRY`（meta 含 `.meta.name`）—— 8 个内置策略 key 的唯一真源。本工具的「信号→候选策略」映射须经**包导入** `from ..strategies import STRATEGY_REGISTRY`（与 `engine/regime.py:18`、`db/repositories.py` 一致）才能拿到非空注册表（仅 import `strategies.base` 得空 dict）。
- `agent/prompts.py · SYSTEM_PROMPT / REGIME_REASONING_SCAFFOLD / build_system_prompt(ui)` —— 系统提示集中此处，现有纪律 1–6（第 6 条即体制工具）；本方案接第 7 条 + 信号目录 + 新增 `SIGNAL_EDGE_REASONING_SCAFFOLD` 常量。

### 2.2 信号库（signal taxonomy）—— 8 个内置具名信号

信号库是 `engine/signal_edge.py · SIGNAL_LIBRARY` 的**唯一真源**（key → 中文定义 + 方向 + 默认参数 + 纯检测函数），被「系统提示目录」「工具 summary」「候选策略映射」共同引用，杜绝多处定义漂移（C6）。全部检测条件仅用 `engine/indicators.py` 现有指标（EMA / RSI / Bollinger），可由 EMA/RSI/布林直接推导，无需新增指标，无 MACD/KDJ 依赖。

| signal key | 中文 | 方向 | 触发条件（因果，仅用 ≤ i 数据） | 覆盖任务示例 |
|---|---|---|---|---|
| `volume_breakout` | 放量突破前高 | long | `close[i] > max(high[i-W..i-1])` 且 `vol[i] ≥ vol_mult·mean(vol[i-W..i-1])` | ✅ 放量突破后收益 |
| `volume_breakdown` | 放量跌破前低 | short | `close[i] < min(low[i-W..i-1])` 且放量同上 | （对称） |
| `rsi_oversold` | RSI 超卖（下穿入） | long | `rsi[i] < rsi_low` 且 `rsi[i-1] ≥ rsi_low`（**边沿触发**，避免连续根聚簇） | ✅ RSI 超卖后收益 |
| `rsi_overbought` | RSI 超买（上穿入） | short | `rsi[i] > rsi_high` 且 `rsi[i-1] ≤ rsi_high` | （对称） |
| `bollinger_lower_touch` | 触及布林下轨 | long | `low[i] ≤ lower[i]` 且 `low[i-1] > lower[i-1]`（新触及） | ✅ 触及下轨后反弹概率 |
| `bollinger_upper_touch` | 触及布林上轨 | short | `high[i] ≥ upper[i]` 且 `high[i-1] < upper[i-1]` | （对称） |
| `ema_golden_cross` | EMA 金叉 | long | `ema_fast[i] ≥ ema_slow[i]` 且 `ema_fast[i-1] < ema_slow[i-1]` | （趋势类补充） |
| `ema_death_cross` | EMA 死叉 | short | `ema_fast[i] ≤ ema_slow[i]` 且 `ema_fast[i-1] > ema_slow[i-1]` | （对称） |

**方向（direction）的意义**：信号方向决定「胜」的定义。原始前瞻收益 `rawRet = exit/entry - 1` 是价格涨跌；**方向调整收益** `sigRet = rawRet`（long）或 `-rawRet`（short）才是「信号是否说对了」的口径。「触及下轨后反弹概率」即 long 信号的 `winRate`（`sigRet>0` 比例）。所有统计量、基线、edge 均建立在 `sigRet` 上。

**边沿触发（edge-triggered）的意义**：`rsi<30`、`low≤lower` 这类「状态型」条件会在连续多根上持续为真 → 触发点高度聚簇、前瞻窗口大量重叠 → 样本非独立、t 统计量被高估。故对状态型信号一律取**状态转入的那一根**（cross-in / 新触及）作为唯一事件根，使每个事件尽量独立（残余重叠在 §7-R3 给出缓解与诚实披露）。

### 2.3 信号检测（`detect_signal`，纯计算、严格因果、edge-triggered）

```python
def detect_signal(df: pd.DataFrame, signal: str, params: SignalParams | None = None) -> pd.Series:
    """返回与 df 等长的 bool Series：第 i 根是否触发该具名信号（只用 ≤ i 数据，因果）。"""
```

- 入口校验 `signal in SIGNAL_LIBRARY`，否则 `ValueError`（工具层转 `ok=False`）。
- 指标一次性向量化算好（`ema/rsi/bollinger`），布尔条件用 `shift(1)` 取「前一根」实现 cross-in（`shift` 天然因果，不引未来）。
- 检测**绝不**使用任何 `i` 之后的数据；这是 `test_causality_no_lookahead` 的守护对象：`detect_signal(df[:k]).iloc[k-1] == detect_signal(df).iloc[k-1]`。
- 前 `warmup` 根（指标未稳定，含 NaN）一律判 `False`，不补零、不外推。

### 2.4 前瞻收益与统计（`forward_returns`，因果截断 + 方向调整 + 基线对照）

```python
def forward_returns(df, signal, *, horizons=(1,3,6,12), params=None,
                    entry_at="next_open", cost_bps=0.0, min_signals=20) -> SignalEdgeResult: ...
```

设 `n = len(df)`，触发序列 `S = detect_signal(df, signal, params)`，信号方向 `d ∈ {long, short}`。

**入场 / 出场口径（因果，无前视）**：
- `entry_at="next_open"`（默认，与回测撮合一致）：入场价 `e = open[i+1]`，出场价 `x = close[i+h]`；**持有恰好 h 根**（i+1 … i+h）。
- `entry_at="signal_close"`（可选，close-to-close 研究口径）：`e = close[i]`，`x = close[i+h]`。
- **口径不对称提示（评审 RV-6）**：`next_open` 为「**开盘→收盘**」口径（入 `open[i+1]`、出 `close[i+h]`），与完整回测实际「开盘→开盘」出场口径**不同**；故本工具 edge 仅用于**判信号有无统计优势**，**不可直接等同**回测逐笔 PnL，落策略仍须 `run_backtest` 复核（脚手架第③步已强调）。
- **右端因果截断（关键，防截断偏差）**：第 i 根的触发，**仅当 `i + h ≤ n-1`** 才计入 horizon h 的样本（`i+h ≤ n-1` 因 `h≥1` 已蕴含 `next_open` 所需的 `i+1 ≤ n-1`，入场根必存在）。窗口右端「未来还没走完」的触发点**逐 horizon 各自剔除**，绝不补零或截断到现有数据假装走完（C1）。

**单点收益**：`rawRet = x/e - 1`；`sigRet = rawRet if d==long else -rawRet`；可选成本：`netRet = sigRet - cost_bps/1e4`（一次性扣单边/双边成本，默认 `cost_bps=0` → 原始信号收益）。**主统计量建立在无成本 `sigRet` 上**（隔离信号纯预测力），`cost_bps>0` 时**额外**给一行「扣 X bps 后净均值」。

**逐 horizon 聚合**（样本 `R_h = [sigRet over 合格 i]`，`m = len(R_h)`；**入聚合前先把 `horizons` 去重并升序归一**，使输出稳定、summary 可读，评审 RV-4）：
- **空样本安全口径（关键，评审 RV-1 / RV-2）**：当 `m == 0`（某 horizon ≥ 窗口长度或贴近右端被全部截断）时，`winRate / meanRet / medianRet / stdRet / tStat / avgWin / avgLoss / baselineRet / edgeVsBaseline / netMeanRet` **一律取 `0.0`、`payoff` 取 `None`**，**绝不**对空集调用 `mean/median`（会抛错或产 NaN）；summary 该行标「样本 0（窗口不足以覆盖该 horizon）」。下列各式仅在 `m ≥ 1` 时计算。
- `count = m`、`winRate = mean(1[r>0])`、`meanRet = mean(R_h)`、`medianRet = median(R_h)`。
- `stdRet = std(R_h, ddof=1)`（`m<2` 取 0）；`tStat = meanRet / (stdRet/√m)`（`m<2` 或 `stdRet==0` 取 0.0）—— 衡量「均值收益是否显著异于 0」。
- `avgWin = mean(r for r>0)`、`avgLoss = mean(r for r<0)`（**对应子集为空时各取 0.0**，不对空集求均值）；`payoff = avgWin/|avgLoss|`（`avgLoss==0` 即无亏损样本时为 `None`）。
- `p25/p50/p75` 分位（仅入 `payload`，不进 summary，控上下文）。
- **基线对照（edge）**：对 horizon h，在**所有合格根**（`i+h ≤ n-1`，不论是否触发）上算无条件方向调整均值 `baseline_h`（long 取均值、short 取相反数）；**合格根为空时 `baseline_h=0.0`**（同 RV-2 守护）；`edgeVsBaseline = meanRet - baseline_h`。这把「信号是否真有 alpha」与「市场本身的漂移」分离——`meanRet>0` 但 `edgeVsBaseline≈0` 说明只是吃 beta，而非信号 edge。

**样本量裁决**：总触发数 `occurrences = int(S.sum())`。
- `occurrences == 0`：**`ok=True`** 且 summary 如实「该信号在此窗口从未触发」（这是合法事实，非错误，不假数据）。
- `0 < occurrences < min_signals`：summary 标「⚠ 样本偏少（m），统计结论谨慎」。
- `len(df) < params.min_bars`：抛 `ValueError`（数据不足，工具层 `ok=False`，C1）。

**候选策略映射**：`candidate_strategies_for_signal(signal)` 按 `SIGNAL_STRATEGY_HINTS` 给出「若该信号有 edge，可落成哪个内置策略」，运行时按 `STRATEGY_REGISTRY` 过滤掉不存在的 key 并补 `name`（经包导入，杜绝指向已删策略，R7）。

### 2.5 渐进式提示词（progressive prompts）—— 两层

与 `label_market_regime` 同构：**两层、由泛到精、即时升格**，二者文本同源引用 §2.2 信号库与方向语义。

**层一 · 系统提示 primer（常驻、低 specificity）** —— 写入 `agent/prompts.py · SYSTEM_PROMPT`：
- 「可用工具」追加：`test_signal_forward_returns：不跑完整策略，只统计某具名信号（放量突破/RSI 超卖/触及布林下轨等）出现后未来 1/3/6/12 根的前瞻收益分布、胜率、t 值与相对基线的超额收益，用于先验证信号本身有没有统计优势。`
- 新增第 7 条纪律：「遇到「某信号到底有没有用 / 值不值得做成策略 / XX 出现后会涨吗」类问题：**先调用 `test_signal_forward_returns` 取统计证据**，再按工具返回的三步脚手架显式写出思维链（① 读样本量/胜率/均值/t/edge ② 判该信号是否真有统计优势——样本够否、跨 horizon 是否一致、是否跑赢基线、是否经得起成本 ③ 决定下一步：用 `run_backtest` 落成策略 / 调参细化 / 放弃），最后才给结论；**样本不足或不显著时必须如实说「证据不足」，绝不为了给答案而夸大**。」
- 附 8 信号一句话定义 + 方向（取自 `SIGNAL_LIBRARY`，让模型未调用工具时也理解词义、能正确指定 `signal` 参数）。

**层二 · 工具结果脚手架（即时、高 specificity）** —— `TestSignalForwardReturnsTool.execute` 把引擎算出的统计画像，拼上 `agent/prompts.py · SIGNAL_EDGE_REASONING_SCAFFOLD` 常量作为 `summary` 回喂模型。脚手架骨架（定长常量，控上下文 R4）：

```text
【统计证据已就绪 · 请按三步显式推理后再下结论】
第①步 读数据：逐 horizon 看 样本量 / 胜率 / 均值收益 / t 值 / 相对基线超额(edge)；样本是否够（≥~20）？
第②步 判优势：该信号是否真有统计边际？——胜率与均值是否一致为正、t 值是否够大、edge 是否显著正
  （仅 meanRet>0 但 edge≈0 只是吃市场漂移，非信号 alpha）；跨 horizon 趋势是否稳定（短赢长亏=均值回复，反之=动量）？
第③步 定下一步：若有稳健 edge → 建议用 run_backtest 在对应候选策略上落成完整策略并计成本验证；
  若样本不足/不显著/仅吃 beta → 如实说证据不足，建议换窗口/换信号/换标的或周期/放弃，绝不硬给买卖建议。
```

> 「渐进式」体现为：层一在系统层常驻地建立「先验证信号统计优势、再决定要不要做策略」的范式；层二在模型**恰好拿到统计的那一刻**注入高细节分步清单，把抽象纪律升格为可照做的步骤。因脚手架要求模型**把推理写进可见正文**，故无论 provider 是否支持原生 thinking，思维链都被强制产出（与体制工具同一保证）。

### 2.6 关键时序（用户：「最近 BTC 永续，放量突破后真的会涨吗？值得做策略吗？」）

```text
AgentPanel.send ─WS→ AgentSession.run
  → LLM(round1) 依 primer 决定调用 test_signal_forward_returns{instId:BTC-USDT-SWAP,bar:1H,signal:volume_breakout}
  → tool_call 事件（前端卡片：🔬 信号前瞻收益）
  → execute：取真实近端 300 根 → 剔 forming → candles_to_df → detect_signal → forward_returns
       → 组装 summary(逐 horizon 统计 + edge + 样本告警 + 三步脚手架)
  → tool_result 回喂模型
  → LLM(round2) 按脚手架显式写出 ①读数 ②判 edge（如「1/3 根胜率 58%、t=2.1、edge=+0.4%，6/12 根 edge 衰减」）
       ③下一步（如「短线有动量 edge，建议用 momentum_breakout run_backtest 计入手续费验证」）→ message_end → done
```

---

## 3. File / module change plan（文件 / 模块变更计划）

> 约定：新建标 **[新]**、修改标 **[改]**。后端 PEP8 + Google docstring，单文件 ≤800 行、单函数 ≤50 行、形参 ≤5、圈复杂度 ≤10、嵌套 ≤4（CLAUDE.md 阈值）。前端 camelCase。

### 3.1 后端（services/quant）—— 必做

| 文件 | 类型 | 一句话意图 |
|---|---|---|
| `app/engine/signal_edge.py` | [新] | 信号前瞻收益引擎：`SIGNAL_LIBRARY`/`SIGNAL_STRATEGY_HINTS`/`SignalParams`/`SignalEdgeParams`/`HorizonStat`/`SignalEdgeResult` + `detect_signal()`/`forward_returns()`/`available_signals()`/`candidate_strategies_for_signal()` + 序列化 helper。严格因果、**不触网络/DB/磁盘 IO**；除 `engine.indicators` 外，经**包导入** `from ..strategies import STRATEGY_REGISTRY` 取注册表元数据（与 `engine/regime.py:18` 同法、无环） |
| `app/agent/tools/signal_tools.py` | [新] | `TestSignalForwardReturnsTool`（name=`test_signal_forward_returns`）：schema/validate/execute；取真实 K 线（剔 forming）→`candles_to_df`→`engine/signal_edge`→组装含三步脚手架的 summary + 结构化 payload |
| `app/agent/tools/registry.py` | [改] | import `TestSignalForwardReturnsTool` 并加入 `_ALL_TOOLS`（一行注册） |
| `app/agent/tools/base.py` | [改] | 新增校验原语 `check_int_list(args, key, *, max_len, max_val)`：可选「正整数数组、长度/元素均有界」（供 `horizons` 校验复用；与既有 `check_pos_int_list` 区分——后者必填非空、不限上界） |
| `app/agent/prompts.py` | [改] | `SYSTEM_PROMPT` 追加工具说明 + 第 7 条纪律 + 8 信号定义；新增 `SIGNAL_EDGE_REASONING_SCAFFOLD` 常量（供工具拼 summary） |

> 拆分理由：统计计算放 `engine/`（与 `indicators.py`/`regime.py` 同层，纯计算、可复用、利于单测）；工具壳放 `agent/tools/`（薄包装 + 取数 + 脚手架拼装）。`SIGNAL_LIBRARY`/`SIGNAL_STRATEGY_HINTS` 作单一真源置于 `engine/signal_edge.py`，`prompts.py` 与工具均 import，杜绝漂移。

### 3.2 前端（apps/web）—— 可选增强（不阻塞验收）

| 文件 | 类型 | 一句话意图 |
|---|---|---|
| `components/Agent/ToolCallCard.tsx` | [改·可选] | `TOOL_LABELS` 增 `test_signal_forward_returns: { label: "信号前瞻收益", icon: "🔬" }`（仅美化工具卡标题） |

> 既有 `ToolCallCard.labelFor` 对未知工具回退 `{label:name,icon:"🔧"}`（见 `ToolCallCard.tsx:23-25`），故**前端零改动亦可正常工作**；本项纯属体验镀金。无新增组件、无 store/类型/reducer 改动。

### 3.3 测试

| 文件 | 类型 | 一句话意图 |
|---|---|---|
| `services/quant/tests/test_engine_signal_edge.py` | [新] | 纯引擎单测：合成序列覆盖 8 信号各自检测正确 + 因果无前视 + 右端 horizon 截断 + 方向调整 + 基线 edge + t 值 + `min_bars` 守卫 + 零触发诚实 + `candidate_strategies_for_signal` 只回注册表内 key。**首行 `import app.strategies` 以填充 `STRATEGY_REGISTRY`（否则候选策略用例形同虚设）** |
| `services/quant/tests/test_agent_signal_tool.py` | [新] | 工具级单测：假 OKX（复用 `test_agent_regime_tool.py · _FakeRest` 范式，monkeypatch `app.agent.tools.signal_tools.get_rest_client`）→ summary 含逐 horizon 统计 + 三步脚手架（「第①步/第②步/第③步」）+ 候选策略；**末根 `confirm=False` 被剔除（payload.lastTs 为已确认末根）**；空/不可达→`ok=False`；`validate` 正反例（缺/越界 signal、horizons 越界、ts 逆序、limit 越界） |

### 3.4 不涉及

- **数据库**：无新表、无迁移（统计结论随 `tool_result.summary` 已被既有 `AgentMessage` 持久化）。
- **三方依赖**：`numpy/pandas` 已是回测既有依赖；零新增。
- **WS/REST 契约**：工具经既有 `ToolRegistry` 暴露，不新增端点、不改信封。
- **桌面打包**：纯后端 Python 增量，随既有 PyInstaller 打包，无需改 `desktop/`。

---

## 4. Interface design（接口设计）

### 4.1 工具 JSON Schema（`test_signal_forward_returns`）

```json
{
  "name": "test_signal_forward_returns",
  "description": "不跑完整策略，只统计某具名信号（如放量突破/RSI 超卖/触及布林下轨）出现后未来 N 根 K 线的前瞻收益：返回每个 horizon 的样本量、胜率、均值/中位收益、t 值与相对无条件基线的超额收益(edge)，用于先验证信号本身有没有统计优势，再决定要不要做成完整策略。",
  "input_schema": {
    "type": "object",
    "properties": {
      "instId": {"type": "string", "description": "如 BTC-USDT-SWAP / ETH-USDT"},
      "bar": {"type": "string", "enum": ["1m","5m","15m","30m","1H","4H","1D"]},
      "signal": {"type": "string", "enum": ["volume_breakout","volume_breakdown","rsi_oversold","rsi_overbought","bollinger_lower_touch","bollinger_upper_touch","ema_golden_cross","ema_death_cross"], "description": "要检验的具名信号"},
      "horizons": {"type": "array", "items": {"type": "integer"}, "description": "前瞻根数列表，默认 [1,3,6,12]，至多 8 个、每个 1..100"},
      "entryAt": {"type": "string", "enum": ["next_open","signal_close"], "description": "入场口径：next_open=下一根开盘(默认，与回测撮合一致)；signal_close=信号根收盘"},
      "costBps": {"type": "number", "description": "可选成本(基点)，>0 时额外给扣成本后的净均值；默认 0=原始信号收益"},
      "startTs": {"type": "integer", "description": "秒级 UTC 起（可选；给区间则按区间取数，缺省取最新 limit 根）"},
      "endTs": {"type": "integer", "description": "秒级 UTC 止（可选）"},
      "limit": {"type": "integer", "description": "取数根数（默认 300，硬上限 300=OKX 单页）"}
    },
    "required": ["instId","bar","signal"]
  }
}
```

`validate`（复用 `agent/tools/base.py` 原语，**均为 args 在先的真实签名**）：

```python
def validate(self, args: dict) -> str | None:
    return first_error(
        require_str(args, "instId"),
        check_enum(args, "bar", BAR_ENUM, required=True),
        check_enum(args, "signal", SIGNAL_ENUM, required=True),   # SIGNAL_ENUM = tuple(SIGNAL_LIBRARY)
        check_enum(args, "entryAt", ("next_open", "signal_close")),
        check_int_list(args, "horizons", max_len=8, max_val=100), # 新增原语：可选；给值则长度 1..8、每元素 1..100
        check_optional_range(args),                               # startTs/endTs 可选且有序
        check_limit(args, 1, 300),                                # 可选 limit，硬上限 300
    )
```

> `costBps` 为可选 number、非法值由 `execute` 内 `float(... or 0)` 兜底（与 `RunBacktestTool` 对 `limit` 的 `int(args.get(...))` 兜底同风格）；不进 `validate` 以保持原语集精简。
> **horizons 归一（评审 RV-4）**：缺省取默认 `[1,3,6,12]`；给值经 `check_int_list`（长度 1..8、元素 1..100）后，`execute` 内**去重 + 升序**再传入 `forward_returns`，杜绝重复/乱序行。
> **阈值不进 schema（评审 RV-3）**：v1 工具面**只暴露上表字段**，检测阈值（`breakout_win/vol_mult/rsi_low/...`）一律取 `SignalParams` 默认值；`forward_returns(params=)` 仅为**引擎/单测内部缝**，不经工具 schema 透传。阈值逐信号调参属后续增强，本期不做（保持 schema 精简、控上下文 R8）。

### 4.2 `ToolResult.summary`（回喂模型 —— 决定推理质量的关键面）

纯中文文本，结构 = **头部（信号/标的/周期/窗口/触发数）** + **逐 horizon 一行统计** + **样本充分性裁决** + **§2.5 三步脚手架**。snapshot 例（数字示意）：

```text
BTC-USDT-SWAP 1H · 信号 volume_breakout（放量突破前高，方向 long）前瞻收益（近 300 根，触发 24 次，入场=next_open）：
  h=1：样本 24，胜率 58.3%，均值 +0.21%，中位 +0.15%，t=1.9，相对基线 edge=+0.18%
  h=3：样本 24，胜率 62.5%，均值 +0.46%，中位 +0.30%，t=2.3，edge=+0.35%
  h=6：样本 23，胜率 56.5%，均值 +0.52%，中位 +0.20%，t=1.6，edge=+0.30%
  h=12：样本 22，胜率 50.0%，均值 +0.10%，中位 -0.05%，t=0.4，edge=-0.05%
样本充分性：24 次触发，达基本阈值（≥20），短中线 edge 为正、长线衰减。
【统计证据已就绪 · 请按三步显式推理后再下结论】第①步 读数据：…第②步 判优势：…第③步 定下一步：…
```

> `costBps>0` 时，每行末追加「（扣 {costBps}bps 后均值 {netMean}）」。触发 0 次时头部改为「触发 0 次：该信号在此窗口从未出现，无从评估」并省略逐 horizon 行（仍附脚手架引导模型换窗口/换信号）。

### 4.3 `ToolResult.payload`（结构化数据：仅供单测断言 / 未来接线，当前不回喂模型、也不下发前端；camelCase）

```jsonc
{
  "instId": "BTC-USDT-SWAP", "bar": "1H", "signal": "volume_breakout",
  "direction": "long", "entryAt": "next_open", "costBps": 0,
  "windowStartTs": 1717000000, "windowEndTs": 1718000000, "bars": 300,
  "lastTs": 1718000000, "occurrences": 24,
  "horizons": [
    {"h": 1, "count": 24, "winRate": 0.583, "meanRet": 0.0021, "medianRet": 0.0015,
     "stdRet": 0.0054, "tStat": 1.9, "avgWin": 0.0048, "avgLoss": -0.0017, "payoff": 2.8,
     "p25": -0.0009, "p50": 0.0015, "p75": 0.0041, "baselineRet": 0.0003, "edgeVsBaseline": 0.0018,
     "netMeanRet": 0.0021},
    { "h": 3, "...": "..." }
  ],
  "candidateStrategies": [{"key": "momentum_breakout", "name": "动量突破"}]
}
```

> **数据流真相**：本工具非 ui-action，故 `payload` **当前既不回喂模型、也不经 WS 下发前端**（`agent/loop.py:390-394`：`tool_result` 仅带 `summary`）。`ToolCallCard` 工具卡只渲染 `summary` 文本，**不依赖 payload**。payload 本期**唯一消费方是单元测试**（断言逐 horizon 统计与候选策略结构）；未来若要把前瞻收益柱状图叠到前端，需另改 loop/WS 把该 payload 下发，属本期范围外。

---

## 5. Data model（数据模型）

### 5.1 内存形状（`engine/signal_edge.py`，无持久化）

```python
@dataclass
class SignalSpec:                          # 信号库单条目（key 隐含在 dict 键）
    cn: str                                # 一句话中文定义（系统提示 / summary 共用真源，C6）
    direction: str                         # "long" | "short"
    detect: Callable[[pd.DataFrame, "SignalParams"], pd.Series]  # 纯检测函数，返回 bool Series

SIGNAL_LIBRARY: dict[str, SignalSpec]      # 8 条，§2.2 表为准
SIGNAL_STRATEGY_HINTS: dict[str, list[str]]  # 信号 key → 候选策略 key（运行时按注册表过滤）
from ..strategies import STRATEGY_REGISTRY  # 包导入触发 @register_strategy，确保注册表非空

@dataclass
class SignalParams:                        # 检测阈值（全部相对量纲，默认即生产值；v1 工具面固定用默认，params= 仅引擎/单测内部缝，不进 schema，RV-3）
    breakout_win: int = 20; vol_mult: float = 1.5
    rsi_period: int = 14; rsi_low: float = 30.0; rsi_high: float = 70.0
    boll_period: int = 20; boll_std: float = 2.0
    ema_fast_p: int = 12; ema_slow_p: int = 26
    warmup: int = 30                       # 指标稳定前不触发
    min_bars: int = 60                     # 不足即拒（不假数据，C1）

@dataclass
class HorizonStat:                         # 单个 horizon 的统计画像（字段即 payload 真源）
    h: int; count: int; winRate: float; meanRet: float; medianRet: float
    stdRet: float; tStat: float; avgWin: float; avgLoss: float; payoff: float | None
    p25: float; p50: float; p75: float
    baselineRet: float; edgeVsBaseline: float; netMeanRet: float

@dataclass
class SignalEdgeResult:
    signal: str; direction: str; entryAt: str; costBps: float
    bars: int; lastTs: int; windowStartTs: int; windowEndTs: int
    occurrences: int
    horizons: list[HorizonStat]
    candidateStrategies: list[dict]        # [{"key","name"}]
```

`SIGNAL_STRATEGY_HINTS` 默认映射（key 取自内置 8 策略，`candidate_strategies_for_signal()` 运行时过滤掉不在 `STRATEGY_REGISTRY` 的 key 并补 `name`）：

| signal | 候选策略 key |
|---|---|
| `volume_breakout` / `volume_breakdown` | `momentum_breakout` |
| `rsi_oversold` | `bollinger_reversion`, `vwap_rsi_momentum` |
| `rsi_overbought` | `vwap_rsi_momentum` |
| `bollinger_lower_touch` / `bollinger_upper_touch` | `bollinger_reversion` |
| `ema_golden_cross` / `ema_death_cross` | `ema_cross_scalper` |

### 5.2 函数签名（`engine/signal_edge.py`）

```python
def detect_signal(df: pd.DataFrame, signal: str, params: SignalParams | None = None) -> pd.Series: ...
def forward_returns(df: pd.DataFrame, signal: str, *, horizons: tuple[int, ...] = (1, 3, 6, 12),
                    params: SignalParams | None = None, entry_at: str = "next_open",
                    cost_bps: float = 0.0, min_signals: int = 20) -> SignalEdgeResult: ...
def available_signals() -> list[dict]: ...                 # [{"key","cn","direction"}]（供 summary/primer）
def candidate_strategies_for_signal(signal: str) -> list[dict]: ...  # 去重保序 + 按注册表过滤 + 补 name
def horizon_stat_to_dict(stat: HorizonStat) -> dict: ...   # asdict，§5.1 字段唯一真源
```

`df` 为 `candles_to_df` 产物（列 `ts,o,h,l,c,vol`，升序）。`forward_returns` 入口先校验 `len(df) ≥ params.min_bars` 与 `signal in SIGNAL_LIBRARY`，不足/未知抛 `ValueError`（工具层转 `ok=False`）。

> **CLAUDE.md 阈值护栏**：`forward_returns` 拆 `_eligible_indices`/`_returns_for_horizon`/`_summarize_horizon`/`_baseline_for_horizon` 辅助函数（各 ≤50 行、嵌套 ≤4）；`detect_signal` 用「signal→检测函数」查找表（`SIGNAL_LIBRARY[signal].detect`）替代 if 阶梯，圈复杂度 ≤10；引擎与工具壳分文件，均 ≪800 行。

### 5.3 持久化

无新增。统计结论以 `tool_result` 文本进入既有 `AgentMessage`（role=`tool`）持久化，resume / history 重放自动带回，符合既定机制。

---

## 6. Testing & acceptance criteria（测试与验收）

### 6.1 单元测试（离线、不依赖真实网络，沿用 pytest）

**`test_engine_signal_edge.py`（纯引擎，合成 K 线 DataFrame；首行 `import app.strategies` 填充注册表）：**
- `test_each_signal_detects`：为 8 信号各造一段命中序列，断言 `detect_signal` 在预期根为 `True`、非预期根为 `False`。
- `test_signal_edge_triggered_not_clustered`：连续多根满足 `rsi<30` 时只在转入根触发一次（验证 edge-trigger 防聚簇）。
- `test_causality_no_lookahead`：`detect_signal(df[:k]).iloc[k-1] == detect_signal(df).iloc[k-1]`（任一根触发不因未来数据改变）。
- `test_forward_return_math`：手算一段确定序列的 `h=1/3` 收益，断言 `meanRet/winRet` 与手算一致；`entry_at` 两口径分别核对（`next_open` 用 `open[i+1]`、`signal_close` 用 `close[i]`）。
- `test_right_edge_truncation`：窗口末尾 h 根内的触发**不计入** horizon h 样本（`count` 随 h 增大而非增）；绝不补零。
- `test_zero_count_horizon_safe`（评审 RV-1/RV-2 守护）：构造「信号有触发但某大 horizon 被右端全部截断」的序列 → 该 horizon `count==0` 时 `winRate/meanRet/medianRet/avgWin/avgLoss/baselineRet/edgeVsBaseline==0.0`、`payoff is None`、`tStat==0.0`，**不抛异常、不产 NaN**；summary 该行含「样本 0」。
- `test_direction_adjustment`：对下跌序列，`short` 信号 `winRate` 高、`long` 信号 `winRate` 低（方向调整正确）。
- `test_baseline_and_edge`：构造「信号点收益≈全样本均值」的中性序列 → `edgeVsBaseline≈0`，证明 edge 隔离了市场漂移。
- `test_tstat_and_low_sample`：`m<2` 时 `tStat==0`、`stdRet==0`；多样本时 `tStat==meanRet/(std/√m)` 数值吻合。
- `test_zero_occurrence_is_ok`：信号从未触发 → `occurrences==0`、`horizons` 各 `count==0`，**不抛异常**（诚实）。
- `test_min_bars_guard`：`len(df)<min_bars` → 抛 `ValueError`。
- `test_candidate_strategies_filtered`：返回 key 全部 ∈ `STRATEGY_REGISTRY`、含 `name`、保序去重；**断言 `STRATEGY_REGISTRY` 非空**（守护包导入）。

**`test_agent_signal_tool.py`（工具壳，假 OKX，monkeypatch `app.agent.tools.signal_tools.get_rest_client`）：**
- `test_summary_has_stats_and_scaffold`：假 OKX 喂含放量突破的序列 → `ok=True`，`summary` 含 `h=1`/`胜率`/`t=`/`edge`、含候选策略、且含「第①步/第②步/第③步」脚手架字样。
- `test_custom_horizons`：`horizons=[2,5]` → `payload["horizons"]` 的 `h` 恰为 `[2,5]`。
- `test_drops_forming_candle`：假 OKX 末根 `confirm=False` → 该 forming bar 不参与（`payload.lastTs` 为倒数第二根 ts）。
- `test_empty_or_unreachable_not_faked`：空 K 线（数据不足）/ `_FakeRest(raise_okx=True)` → `ok=False`，summary 如实告知（C1）。
- `test_validate_reject_and_accept`：缺 `instId`/越界 `bar`/未知 `signal`/`horizons` 含非正或超 8 个/`ts` 逆序/`limit` 越界 → `validate` 非空；合法入参 → `None`。

### 6.2 验收标准（Definition of Done）

1. `engine/signal_edge.py`、`agent/tools/signal_tools.py` 创建，`test_signal_forward_returns` 经 `ToolRegistry().schemas()` 出现在工具表，`registry._ALL_TOOLS` 含其实例。
2. 在 AI 助手输入「最近 BTC 永续放量突破后会涨吗、值得做策略吗」→ Agent 调 `test_signal_forward_returns`，并在回复中**显式呈现三段式推理**（先读样本量/胜率/均值/t/edge → 判是否有统计优势 → 决定要不要 `run_backtest` 落策略），数字均来自工具统计而非臆造。
3. 信号检测**严格因果**（`test_causality_no_lookahead` 通过）；前瞻收益**右端因果截断**（`test_right_edge_truncation` 通过）；OKX 不可达 / 数据不足时如实 `ok=False`，信号零触发时如实 `ok=True` 说明（C1）。
4. 系统提示含第 7 条纪律与 8 信号定义；工具 summary 含逐 horizon 统计 + 样本裁决 + 三步脚手架，且**不依赖 provider 原生 thinking** 即能产出可见思维链。
5. `pnpm test`（后端 pytest 非 network + 前端既有 vitest/tsc）全绿；新增测试全部通过；未破坏既有 `test_agent_*`。
6. 未引入三方依赖、未改 DB / WS / REST 契约、未提交构建产物；候选策略映射只指向 `STRATEGY_REGISTRY` 真实存在的 key。

---

## 7. Risks & mitigations（风险与缓解）

| # | 风险 | 缓解 |
|---|---|---|
| R1 | 阈值因标的 / 周期失准（不同币种波动量级不同） | 全部判据用**相对量纲**（放量比、RSI 分位阈、布林标准差倍数、EMA 交叉），不含绝对价位魔法数；阈值集中在 `SignalParams`（默认即生产值，可透传覆盖） |
| R2 | 前视偏差（信号检测用了未来数据） | 检测仅用 `≤ i` 数据 + `shift(1)` 实现 cross-in；`test_causality_no_lookahead` 守护 |
| **R3** | **前瞻窗口重叠 → 样本非独立 → t 值高估（统计陷阱）** | 状态型信号一律**edge-trigger**（只取转入根），减少聚簇；t 值仅作参考且 summary 标注样本量；**在 summary 与脚手架第②步明确提醒「样本重叠时 t 值偏乐观」**，诚实披露不夸大（C1 精神延伸）。注：cooldown 去重（`dedupe_bars`）本期**不实现**，仅作 `forward_returns` 内部默认关闭 kwarg 占位、不进 schema、不构成空旋钮陷阱；启用属后续增强（评审 RV-7） |
| R4 | 右端截断偏差（末尾触发未来没走完仍计入） | **逐 horizon 各自剔除** `i+h>n-1` 的触发，绝不补零/截断假装走完；`test_right_edge_truncation` 守护 |
| R5 | 把「吃市场 beta」误判为「信号 alpha」 | 引入**基线对照** `edgeVsBaseline`（信号均值 − 同期无条件方向调整均值），脚手架第②步强制区分 edge 与漂移；`test_baseline_and_edge` 守护 |
| R6 | 忽略交易成本导致虚高 edge | 提供可选 `costBps`，summary 给「扣成本后净均值」；脚手架第③步要求「落成策略时计入成本验证」 |
| R7 | 候选策略映射指向已删策略 | `candidate_strategies_for_signal()` 运行时按 `STRATEGY_REGISTRY`（经包导入）过滤并补 `name`；`test_candidate_strategies_filtered` 守护 |
| R8 | summary 过长撑爆上下文 token（任务明确要求控上下文） | 逐 horizon 仅一行关键数字并四舍五入；分位/avgWin 等细节只入 `payload` 不进 summary；horizons ≤ 8；脚手架为定长常量；触发 0 次时省略逐 horizon 行 |
| R9 | 模型不照脚手架推理、直接复述统计 | 脚手架显式编号三步并要求「样本不足/不显著须说证据不足」；系统提示第 7 条强制「先验证再决定、不夸大」 |
| R10 | 单文件 / 单函数超 CLAUDE.md 阈值 | `forward_returns` 拆 `_eligible_indices`/`_returns_for_horizon`/`_summarize_horizon`/`_baseline_for_horizon`；`detect_signal` 用 `SIGNAL_LIBRARY[signal].detect` 查找表替代 if 阶梯；引擎与工具壳分文件，均 ≪800 行 |
| R11 | OKX 单次取数上限 300 限制样本量 | `limit` 硬上限 300；窄窗口触发偏少时由 `min_signals` 阈值标注「样本偏少」；更长历史（分页）属未来增强，本期不做并在文档注明 |

---

## 8. 附：实现注意事项（给下游工程师）

- **summary 是唯一对外通道**：本工具非 ui-action，`payload` 既不回喂模型、也不下发前端（仅供单测）；统计画像与三步脚手架**务必全部进 `summary`**，勿把关键数字或推理指令只放 `payload`。
- **取数必剔除 forming bar**：复用 `regime_tools.py · _drop_forming` 同款 `confirm` 过滤——snapshot/无区间走 `get_rest_client().get_candles(instId, bar, limit)`（位置参数齐全，勿漏 instId/bar）后过滤；给区间走 `services.load_range_candles`（自带 confirm + 越界 + 空区间 400）。
- **入场口径默认 next_open**：与 `engine/backtester.py:71` 撮合一致（`open[i+1]`）；`signal_close` 仅供 close-to-close 研究，二者均严格因果。
- **右端逐 horizon 截断**：horizon h 的样本只含 `i+h ≤ n-1` 的触发；不同 horizon 样本量可不同，这是正确行为，勿对齐到最小集。
- **注册表用包导入**：`engine/signal_edge.py` 须 `from ..strategies import STRATEGY_REGISTRY`（同 `engine/regime.py:18`）；`test_engine_signal_edge.py` 首行 `import app.strategies`。
- **validate 签名 args 在先**：`require_str(args, key)` / `check_enum(args, key, allowed, *, required=)` / `check_limit(args, low, high)`；新增 `check_int_list` 同风格（args 在先、可选、有界）。
- **复用而非重写**：指标一律用 `engine/indicators.py`；DataFrame 一律 `candles_to_df`；区间取数优先 `services.load_range_candles`。
- **时间秒级 UTC**：入出参 `ts` 全秒级（OKX 适配层已做毫秒转换，勿在工具层重复）。
- **camelCase 边界**：`payload` 字段 camelCase 与前端对齐；`summary` 用中文自然语言。
- **不假数据（C1）**：OKX 不可达 / 数据不足 → `ok=False` + 明确原因；信号零触发 → `ok=True` 如实说「未出现」，绝不补零、插值或编造收益。
- **统计诚实**：样本不足、t 值低、edge 近零时，summary 与脚手架都要让模型「说证据不足」而非硬给买卖建议——这是本工具相对「直接给信号」的核心价值。
- **跨平台命令**：建目录用 `python -c "from pathlib import Path; Path('...').mkdir(parents=True, exist_ok=True)"`，勿用 `mkdir -p` / `&&`（见任务环境约定）。
- **提交期精确暂存**：下游 Subtask #3 用 `git add <具体路径>`，严禁 `git add -A`，复核 diff 不含任何密钥 / 构建产物。

（全文 > 800 字，覆盖 Overview / 技术设计 / 文件清单 / 接口 / 数据模型 / 测试验收 / 风险，满足 Definition of Done。）

---

## 9. 评审结论（Review Verdict · Subtask #1）

**结论：有条件通过（Approved with conditions）。**

本方案技术断言已**逐条对照真实源码核验全部属实**（详见 §0.1），架构与既有 `label_market_regime` 高度同构，规模为「薄增量」——不触 DB / WS / REST 契约、零新增三方依赖、与 `CLAUDE.md` 阈值一致，**可直接进入编码（Subtask #2）**。

评审共发现 7 项问题（**P1×3、P2×4，无 P0**）：3 项 P1（空样本数值安全 RV-1、基线空集 RV-2、参数可覆盖性自相矛盾 RV-3）与 P2 中的 RV-4/RV-5/RV-6 均已在本 **v2 正文就地修复**；**无遗留 P0/P1**。

**放行条件（下游编码 Subtask #2 须遵守）：**

1. **空样本安全（RV-1/RV-2）**：逐 horizon 统计必须按 §2.4「空样本安全口径」实现——`count==0` 与「无盈/无亏子集」一律不对空集 `mean/median`，按规定取 `0.0` / `None`；并补 `test_zero_count_horizon_safe` 守护（§6.1）。
2. **阈值不暴露（RV-3）**：v1 工具面固定用 `SignalParams` 默认阈值，**不**在 schema 暴露阈值字段；`forward_returns(params=)` 仅作引擎/单测内部缝。
3. **horizons 归一（RV-4）**：经 `check_int_list`（给值长度 1..8、元素 1..100）校验后，`execute` 内**去重 + 升序**再计算。
4. **dedupe_bars 占位（RV-7）**：保持为 `forward_returns` 内部默认关闭 kwarg、不进 schema、本期不实现 cooldown 逻辑；不要暴露空旋钮。
5. **既定硬约束不变**：严格因果、右端逐 horizon 截断、forming 剔除、不假数据（C1）、候选策略仅指向 `STRATEGY_REGISTRY` 真实 key、`summary` 为唯一对外通道、注册表经包导入。

满足上述条件即视为完全通过。评审签字：Senior Reviewer（Anthropic Eng），文档升至 **v2**。

---

## 10. 实施过程发现的方案缺陷（Issues Found During Implementation · Subtask #2）

> 实施工程师（Anthropic Eng）在按 v2 落代码时发现以下三处**文档内部不自洽 / YAGNI**。均不影响放行条件实质（严格因果、右端截断、空样本安全、阈值不进 schema、候选策略过滤、summary 唯一通道、注册表包导入全部如实实现并有单测守护），已按「更干净且不违 CLAUDE.md 阈值」的取舍就地处理，记录如下：

| ID | 维度 | 文档不一致 | 处置（已落地） |
|---|---|---|---|
| IMPL-1 | 一致性 | §3.1 文件清单把 `SignalEdgeParams` 列入 `engine/signal_edge.py` 应建 dataclass，但 §5.1 数据模型与 §5.2 函数签名**从未定义或使用**它（研究旋钮 `horizons/entry_at/cost_bps` 在 §5.2 以关键字参数直接传入 `forward_returns`）。 | **不创建 `SignalEdgeParams`**（创建即死代码，违 CLAUDE.md「无僵尸代码」）。以 §5.2 的关键字参数为准。 |
| IMPL-2 | 一致性 / YAGNI | §9 放行条件 4 要求保留 `dedupe_bars` 为 `forward_returns` 内部默认关闭 kwarg；但 §5.2 的规范签名**不含**该参数，且本期明确不实现 cooldown 逻辑——加一个永不读取的 kwarg 即「空旋钮 / 未使用形参」，恰是 RV-7 与 CLAUDE.md 都要避免的。 | **不加 `dedupe_bars` 形参**。RV-7 的核心诉求「不进 schema、不暴露空旋钮、本期不实现」已通过「根本不引入该参数」满足；cooldown 留作后续增强时再加。 |
| IMPL-3 | 右尺寸 / 职责归位 | §5.2 把 `min_signals=20` 作 `forward_returns` 形参，但该阈值仅用于 §2.4「样本充分性裁决」的 **summary 文案**（「summary 标⚠样本偏少」），引擎数学不消费它；若留在引擎签名则为未使用形参，且使 `forward_returns` 形参达 7 个（超 CLAUDE.md ≤5）。 | 阈值**下沉为 `engine/signal_edge.py` 模块常量 `MIN_SIGNALS = 20`**，由工具壳 `signal_tools.py` 在拼 summary 时读取（职责归位到 summary 层）。`forward_returns` 形参收敛为 2 位置 + 4 关键字（`horizons/params/entry_at/cost_bps`），契合 CLAUDE.md「以命名参数替代位置堆参」的推荐范式。 |

> 上述处置均为**收敛性**调整（去死代码、去空旋钮、阈值归位），未改变任何对外行为、未减少任一放行条件的实现与守护测试。实现产物：`engine/signal_edge.py`（引擎）/ `agent/tools/signal_tools.py`（工具壳）/ `agent/tools/base.py`（新增 `check_int_list`）/ `agent/tools/registry.py`（注册一行）/ `agent/prompts.py`（primer 第 7 条 + 8 信号目录 + `SIGNAL_EDGE_REASONING_SCAFFOLD`）/ `apps/web/components/Agent/ToolCallCard.tsx`（工具卡标签）+ 两个单测文件，共 22 条新测试全绿，既有 273 条无回归。
